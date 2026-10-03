#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Codex ↔ CLIProxyAPI model bridge.

The script deliberately keeps secrets out of stdout. It manages only the Codex
provider block and the model catalog, which it regenerates from the live
CLIProxyAPI list plus personal overrides.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
DEFAULT_STATE_DIR = Path("~/.config/codex-cli-model-bridge").expanduser()
DEFAULT_PROXY_URL = "http://127.0.0.1:8317/v1"
DEFAULT_TRANSPARENT_PROXY_URL = "http://127.0.0.1:8318/v1"
DEFAULT_PROFILE_NAME = "cli-proxy"
DEFAULT_PROFILE_CONFIG = DEFAULT_CODEX_HOME / f"{DEFAULT_PROFILE_NAME}.config.toml"
DEFAULT_STATE_DB = DEFAULT_CODEX_HOME / "state_5.sqlite"
DEFAULT_AUTH_FILE = DEFAULT_CODEX_HOME / "auth.json"
DEFAULT_TRANSPARENT_RUNTIME = DEFAULT_STATE_DIR / "transparent_proxy.mjs"
DEFAULT_LAUNCH_AGENT = Path(
    "~/Library/LaunchAgents/com.zhijian.codex-cli-model-bridge-transparent-proxy.plist"
).expanduser()
TRANSPARENT_LAUNCH_LABEL = "com.zhijian.codex-cli-model-bridge-transparent-proxy"
PROVIDER_ID = "cli_proxy"
SCHEMA_VERSION = 1


class SyncBlocked(Exception):
    """Carries a redacted receipt for a sync that must not write."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "blocked"))
        self.payload = {"status": "blocked", **payload}


def is_windows() -> bool:
    return os.name == "nt" or platform.system() == "Windows"


def python_executable() -> str:
    """Persist a host interpreter, not the uv environment running this script."""
    candidate = shutil.which("python3")
    if candidate:
        path = Path(candidate).absolute()
        resolved = path.resolve()
        virtual_roots = [os.environ.get("VIRTUAL_ENV"), os.environ.get("CONDA_PREFIX")]
        if sys.prefix != sys.base_prefix:
            virtual_roots.append(sys.prefix)
        temporary = False
        for location in (path, resolved):
            rendered = location.as_posix().lower()
            if "/uv/" in rendered and ("environments" in rendered or "archive" in rendered or "/uv/python/" in rendered):
                temporary = True
            if any((parent / "pyvenv.cfg").exists() for parent in location.parents):
                temporary = True
            if any(part.lower() in {".venv", "venv", "virtualenv"} for part in location.parts):
                temporary = True
            if any(location.is_relative_to(Path(root).absolute()) for root in virtual_roots if root):
                temporary = True
        if not temporary:
            # Keep the stable PATH alias (e.g. Homebrew bin/python3), not its
            # versioned Cellar target, while checking both for virtual envs.
            return str(path)
    return "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"


def ruby_executable() -> str:
    found = shutil.which("ruby")
    if found:
        return found
    return "ruby" if is_windows() else "/usr/bin/ruby"


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        expanded = path.expanduser()
        if expanded.exists():
            return expanded
    return None


def discover_executable(names: list[str], extra: list[Path] | None = None) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return first_existing(extra or [])


def default_proxy_binary() -> Path:
    extra: list[Path] = []
    if platform.system() == "Darwin":
        extra = [
            Path("/opt/homebrew/opt/cliproxyapi/bin/cliproxyapi"),
            Path("/usr/local/opt/cliproxyapi/bin/cliproxyapi"),
        ]
    found = discover_executable(["cliproxyapi", "cli-proxy-api", "CLIProxyAPI"], extra)
    return found or Path("cliproxyapi")


def default_node() -> Path:
    found = discover_executable(
        ["node"],
        [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")],
    )
    return found or Path("node")


def default_proxy_config() -> Path:
    env = os.environ.get("CLIPROXYAPI_CONFIG")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    candidates = [
        home / ".cli-proxy-api" / "config.yaml",
        home / ".cliproxyapi" / "config.yaml",
        Path("/opt/homebrew/etc/cliproxyapi.conf"),
        Path("/usr/local/etc/cliproxyapi.conf"),
    ]
    if is_windows():
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        roaming = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        candidates = [
            home / ".cli-proxy-api" / "config.yaml",
            local / "CLIProxyAPI" / "config.yaml",
            roaming / "CLIProxyAPI" / "config.yaml",
            local / "EasyCLIProxyAPI" / "cpa-core" / "config.yaml",
        ] + candidates
    return first_existing(candidates) or candidates[0]


def default_helper_path() -> Path:
    base = Path("~/.config/codex-cli-proxy").expanduser()
    python_helper = base / "read-client-key.py"
    ruby_helper = base / "read-client-key.rb"
    if python_helper.exists():
        return python_helper
    if ruby_helper.exists():
        return ruby_helper
    return python_helper


def helper_invocation(helper_path: Path) -> tuple[str, list[str]]:
    if helper_path.suffix.lower() == ".rb":
        return ruby_executable(), [str(helper_path)]
    python = python_executable()
    if Path(python).name.lower() in {"py", "py.exe"}:
        return python, ["-3", str(helper_path)]
    return python, [str(helper_path)]


DEFAULT_PROXY_CONFIG = default_proxy_config()
DEFAULT_PROXY_BINARY = default_proxy_binary()
DEFAULT_HELPER = default_helper_path()


def emit(value: object, exit_code: int = 0) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


def mode(path: Path) -> str | None:
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except FileNotFoundError:
        return None


def owner_mode_ok(path: Path, expected: int) -> bool:
    if is_windows():
        return True
    return mode(path) == oct(expected)


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def parse_proxy_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"CLIProxyAPI Version:\s*(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def proxy_version(path: Path) -> tuple[str | None, tuple[int, int, int] | None]:
    try:
        proc = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    parsed = parse_proxy_version(proc.stdout)
    rendered = ".".join(str(part) for part in parsed) if parsed else None
    return rendered, parsed


def backup(path: Path) -> Path:
    target = path.with_name(f"{path.name}.backup-{timestamp()}")
    shutil.copy2(path, target)
    os.chmod(target, 0o600)
    return target


def atomic_write(path: Path, data: str, file_mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, file_mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path: Path, default: object | None = None) -> object:
    if not path.exists():
        if default is not None:
            return copy.deepcopy(default)
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def is_loopback(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname
        return host in {"127.0.0.1", "localhost", "::1"}
    except ValueError:
        return False


def provider_from_config(config: dict, provider_id: str = PROVIDER_ID) -> dict:
    return dict(config.get("model_providers", {}).get(provider_id, {}))


def load_config(path: Path) -> tuple[dict, str | None]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return {}, "missing"
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def token_from_provider(provider: dict) -> tuple[str | None, str | None]:
    auth = provider.get("auth")
    if not isinstance(auth, dict):
        return None, "command-backed auth is not configured"
    command = auth.get("command")
    args = auth.get("args", [])
    if not isinstance(command, str) or not command:
        return None, "auth.command is missing"
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return None, "auth.args must be a string array"
    timeout = max(1, int(auth.get("timeout_ms", 5000)) / 1000)
    try:
        proc = subprocess.run(
            [command, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"credential helper failed: {type(exc).__name__}"
    token = proc.stdout.strip()
    if proc.returncode != 0 or not token:
        return None, "credential helper returned no usable token"
    return token, None


def required_live_routes(catalog_entries: list[dict], default_model: str | None) -> list[str]:
    required: set[str] = set()
    if default_model:
        required.add(default_model)
    for entry in catalog_entries:
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        if entry.get("visibility") == "hide":
            continue
        if entry.get("supported_in_api") is False:
            continue
        required.add(slug)
    return sorted(required)


def fetch_json(url: str, token: str | None, timeout: int = 15) -> object:
    """GET a JSON document; the transparent proxy adds credentials when token is None."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return json.load(response)


def live_model_ids(base_url: str, token: str | None, fixture: Path | None = None) -> set[str]:
    payload = read_json(fixture) if fixture else fetch_json(f"{base_url.rstrip('/')}/models", token)
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return {item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)}


def catalog_entries(payload: object, label: str = "catalog") -> list[dict]:
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError(f"{label} must contain a models array")
    slugs: set[str] = set()
    for entry in models:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            raise ValueError(f"every {label} entry needs a string slug")
        if entry["slug"] in slugs:
            raise ValueError(f"duplicate {label} slug: {entry['slug']}")
        slugs.add(entry["slug"])
    return models


def catalog_models(path: Path) -> list[dict]:
    return catalog_entries(read_json(path))


def codex_client_version(codex: str) -> str | None:
    try:
        output = subprocess.run(
            [codex, "--version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"\d+\.\d+\.\d+", output)
    return match.group(0) if match else None


def redact_url(url: str) -> str:
    """Drop userinfo, query and fragment before a URL enters a receipt."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def configured_catalog_path(config_path: Path, value: object) -> Path | None:
    """Codex resolves a relative model_catalog_json next to the config file, not the working directory."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_path.expanduser().parent / path


def codex_rejects_catalog(codex: str, path: Path, slugs: list[str]) -> str | None:
    """Load the catalog with Codex's own parser (offline); return why it failed, or None."""
    try:
        proc = subprocess.run(
            [codex, "debug", "models", "-c", "model_catalog_json=" + json.dumps(str(path))],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"codex debug models failed: {type(exc).__name__}"
    if proc.returncode != 0:
        lines = proc.stderr.strip().splitlines()
        return (lines[-1] if lines else f"codex debug models exited {proc.returncode}")[:300]
    try:
        loaded = [entry["slug"] for entry in json.loads(proc.stdout)["models"]]
    except (ValueError, KeyError, TypeError):
        return "codex debug models returned unreadable output"
    if sorted(loaded) != sorted(slugs):
        return "Codex loaded a different model list than the catalog"
    return None


def sync_route(config: dict, transparent_url: str) -> tuple[str, str, str | None]:
    """Return (mode, base_url, token) for this bridge's own route; other gateways are never synced."""
    active = config.get("model_provider", "openai")
    root_base_url = config.get("openai_base_url")
    if active == "openai" and isinstance(root_base_url, str) and root_base_url:
        if root_base_url.rstrip("/") != transparent_url.rstrip("/"):
            raise SyncBlocked({
                "error": "openai_base_url is not this bridge's transparent proxy; another gateway owns this catalog "
                         "(pass --transparent-url if the proxy listens elsewhere)",
                "route_owned_elsewhere": True,
            })
        if not is_loopback(root_base_url):
            raise SyncBlocked({"error": "transparent OpenAI base URL is not loopback-only"})
        return "desktop-transparent", root_base_url, None
    if active != PROVIDER_ID:
        raise SyncBlocked({
            "error": f"active Provider {active!r} is neither the transparent proxy nor {PROVIDER_ID}",
            "route_owned_elsewhere": True,
        })
    provider = provider_from_config(config)
    base_url = provider.get("base_url", DEFAULT_PROXY_URL)
    if not isinstance(base_url, str) or not is_loopback(base_url):
        raise SyncBlocked({"error": "active Provider is not loopback-only"})
    token, error = token_from_provider(provider)
    if error or not token:
        raise SyncBlocked({"error": error or "credential helper failed"})
    return "isolated-profile", base_url, token


OVERRIDE_INT_FIELDS = {"context_window", "max_context_window", "auto_compact_token_limit"}


def load_overrides(path: Path) -> list[tuple[str, dict]]:
    """个人偏好只写与上游不同的字段；键为精确 ID 或 glob，null 表示删除该字段。"""
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("overrides root must be an object with schema_version 1")
    models = payload.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("overrides.models must be an object")
    for key, fields in models.items():
        if not key.strip() or not isinstance(fields, dict) or not fields:
            raise ValueError(f"override {key!r} must map a non-empty key to a non-empty object")
        if "slug" in fields:
            raise ValueError(f"override {key!r} may not change slug")
        for field, value in fields.items():
            if value is None:
                continue
            if field == "visibility" and value not in {"list", "hide"}:
                raise ValueError(f"override {key!r}: visibility must be list or hide")
            if field in OVERRIDE_INT_FIELDS and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"override {key!r}: {field} must be a positive integer")
            if field == "priority" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"override {key!r}: priority must be an integer")
            if field == "effective_context_window_percent" and (
                isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 100
            ):
                raise ValueError(f"override {key!r}: effective_context_window_percent must be 1-100")
    return list(models.items())


def apply_overrides(entries: list[dict], overrides: list[tuple[str, dict]]) -> tuple[list[str], list[str]]:
    """Apply glob keys in file order, then an exact ID; return (unmatched keys, errors)."""
    patterns = [(key, fields) for key, fields in overrides if any(char in key for char in "*?[")]
    exact = dict(overrides)
    matched: set[str] = set()
    errors: list[str] = []
    for entry in entries:
        slug = entry["slug"]
        # 精确 ID 优先识别，带方括号的真实 ID 也不会被当成通配符。
        rules = [(key, fields) for key, fields in patterns if key != slug and fnmatch.fnmatchcase(slug, key)]
        if slug in exact:
            rules.append((slug, exact[slug]))
        touched_reasoning = False
        for key, fields in rules:
            matched.add(key)
            touched_reasoning |= bool({"default_reasoning_level", "supported_reasoning_levels"} & set(fields))
            for field, value in fields.items():
                if value is None:
                    entry.pop(field, None)
                else:
                    entry[field] = copy.deepcopy(value)
        if touched_reasoning:
            levels = entry.get("supported_reasoning_levels")
            efforts = [level.get("effort") for level in levels if isinstance(level, dict)] if isinstance(levels, list) else []
            if entry.get("default_reasoning_level") not in efforts:
                errors.append(f"{slug}: default_reasoning_level is not one of {efforts}")
    return [key for key, _ in overrides if key not in matched], errors


def extra_manifests(state_dir: Path, live_ids: set[str]) -> tuple[list[dict], list[str], dict[str, list[str]]]:
    """models.d 只补充上游列表里没有的模型；同名清单让位给实时列表，不再校验。"""
    extras: list[dict] = []
    shadowed: list[str] = []
    errors: dict[str, list[str]] = {}
    owners: dict[str, Path] = {}
    for path in sorted((state_dir / "models.d").glob("*.json")):
        try:
            data = read_json(path)
        except (OSError, ValueError) as exc:
            errors[str(path)] = [f"{type(exc).__name__}: {exc}"]
            continue
        slug = data.get("slug") if isinstance(data, dict) else None
        if isinstance(slug, str) and slug in live_ids:
            shadowed.append(slug)
            continue
        problems = validate_manifest(data) if isinstance(data, dict) else ["manifest root must be an object"]
        if not problems and slug in owners:
            problems = [f"slug {slug!r} is also declared in {owners[slug]}"]
        if problems:
            errors[str(path)] = problems
        else:
            owners[slug] = path
            extras.append(data)
    return extras, sorted(set(shadowed)), errors


def validate_manifest(data: dict) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version": int,
        "slug": str,
        "display_name": str,
        "description": str,
        "context_window": int,
        "effective_context_window_percent": int,
        "default_reasoning_level": str,
        "reasoning_efforts": list,
        "input_modalities": list,
        "priority": int,
    }
    for key, expected in required.items():
        if not isinstance(data.get(key), expected):
            errors.append(f"{key} must be {expected.__name__}")
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if isinstance(data.get("slug"), str) and not re.fullmatch(r"[a-zA-Z0-9._:-]+", data["slug"]):
        errors.append("slug contains unsupported characters")
    efforts = data.get("reasoning_efforts", [])
    allowed_efforts = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
    if not efforts or any(not isinstance(item, str) or item not in allowed_efforts for item in efforts):
        errors.append("reasoning_efforts contains an invalid value")
    modalities = data.get("input_modalities", [])
    if not modalities or any(not isinstance(item, str) or item not in {"text", "image"} for item in modalities):
        errors.append("input_modalities must contain only text/image")
    if data.get("default_reasoning_level") not in efforts:
        errors.append("default_reasoning_level must be in reasoning_efforts")
    if "template_slug" in data and not isinstance(data["template_slug"], str):
        errors.append("template_slug must be str")
    if "supersedes" in data:
        errors.append("supersedes is no longer supported; retired IDs disappear with the live list")
    return errors


def reasoning_levels(efforts: list[str]) -> list[dict]:
    descriptions = {
        "minimal": "Minimal reasoning for the fastest response",
        "low": "Fast responses with lighter reasoning",
        "medium": "Balanced speed and reasoning depth",
        "high": "Greater reasoning depth for complex work",
        "xhigh": "Extra-high reasoning depth",
        "max": "Maximum reasoning depth",
        "ultra": "Maximum reasoning with automatic task delegation",
    }
    return [{"effort": effort, "description": descriptions[effort]} for effort in efforts]


def preferred_native_model(models: list[dict]) -> dict:
    """Prefer visible native entries, then ascending priority and stable slug."""
    if not models:
        raise ValueError("native model cache is empty")
    visible = [entry for entry in models if entry.get("visibility") == "list"]
    return min(visible or models, key=lambda entry: (
        entry.get("priority") if isinstance(entry.get("priority"), (int, float)) else float("inf"),
        entry["slug"],
    ))


def build_entry(manifest: dict, templates: dict[str, dict],
                template_fallbacks: dict[str, dict[str, str]] | None = None) -> dict:
    template_slug = manifest.get("template_slug")
    if template_slug not in templates:
        replacement = preferred_native_model(list(templates.values()))["slug"]
        if template_fallbacks is not None and template_slug:
            template_fallbacks[manifest["slug"]] = {"requested": template_slug, "selected": replacement}
        template_slug = replacement
    entry = copy.deepcopy(templates[template_slug])
    # 速度档位属于具体模型，不能从通用模板借用；清单未声明时为空。
    entry.update(
        {
            "slug": manifest["slug"],
            "display_name": manifest["display_name"],
            "description": manifest["description"],
            "default_reasoning_level": manifest["default_reasoning_level"],
            "supported_reasoning_levels": reasoning_levels(manifest["reasoning_efforts"]),
            "visibility": "list",
            "supported_in_api": True,
            "priority": manifest["priority"],
            "additional_speed_tiers": copy.deepcopy(manifest.get("additional_speed_tiers", [])),
            "service_tiers": copy.deepcopy(manifest.get("service_tiers", [])),
            "context_window": manifest["context_window"],
            "max_context_window": manifest["context_window"],
            "effective_context_window_percent": manifest["effective_context_window_percent"],
            "input_modalities": manifest["input_modalities"],
            "supports_search_tool": bool(manifest.get("supports_search_tool", False)),
            "supports_image_detail_original": bool(
                manifest.get("supports_image_detail_original", "image" in manifest["input_modalities"])
            ),
            "prefer_websockets": bool(manifest.get("prefer_websockets", False)),
        }
    )
    # Tool mode is a model-specific calling protocol, not a generic capability.
    # Third-party models copied from an OpenAI template must be able to opt out
    # of `code_mode_only`, whose freeform `exec` payload cannot be represented
    # by a normal JSON function schema through compatibility bridges.
    if "tool_mode" in manifest:
        tool_mode = manifest["tool_mode"]
        if tool_mode is None:
            entry.pop("tool_mode", None)
        else:
            entry["tool_mode"] = tool_mode
    return entry


def replace_top_scalar(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    first_table = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(first_table):
        if pattern.match(lines[index]):
            lines[index] = f"{key} = {json.dumps(value)}\n"
            return "".join(lines)
    lines.insert(first_table, f"{key} = {json.dumps(value)}\n")
    return "".join(lines)


def remove_top_scalar(text: str, key: str) -> str:
    lines = text.splitlines(keepends=True)
    first_table = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    return "".join(line for index, line in enumerate(lines) if not (index < first_table and pattern.match(line)))


def thread_inventory(path: Path) -> tuple[dict[str, int], dict[str, object] | None, str | None]:
    if not path.exists():
        return {}, None, "state database is missing"
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            "SELECT id, model_provider, archived FROM threads ORDER BY id"
        ).fetchall()
        connection.close()
    except sqlite3.Error as exc:
        return {}, None, f"{type(exc).__name__}: {exc}"
    counts: dict[str, int] = {}
    digest = hashlib.sha256()
    for thread_id, provider, archived in rows:
        counts[str(provider)] = counts.get(str(provider), 0) + 1
        digest.update(f"{thread_id}\0{provider}\0{archived}\n".encode())
    return counts, {"total": len(rows), "sha256": digest.hexdigest(), "integrity": integrity}, None


def dominant_provider(counts: dict[str, int]) -> str | None:
    if not counts:
        return None
    return max(counts, key=lambda item: (counts[item], item))


def config_diff(path: Path, before: str, after: str, context: int = 3) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
            n=context,
        )
    )


def root_config_diff(path: Path, before: str, after: str) -> str:
    """Print only managed root values, never raw lines or credential sections."""
    fields = ("model", "model_provider", "model_catalog_json", "openai_base_url")

    def view(text: str) -> str:
        config = tomllib.loads(text)
        return "".join(f"{key} = {json.dumps(config[key])}\n" for key in fields if key in config)

    return config_diff(path, view(before), view(after))


def require_apply_sha(args: argparse.Namespace, current_sha: str, changed: bool) -> None:
    if args.expected_sha256 and args.expected_sha256 != current_sha:
        emit({"status": "blocked", "error": "config changed after approval",
              "expected_sha256": args.expected_sha256, "actual_sha256": current_sha}, 2)
    if args.apply and changed and not args.expected_sha256:
        emit({"status": "blocked", "error": "preview first and supply --expected-sha256 before applying changes"}, 2)


def replace_yaml_section_bool(text: str, section: str, key: str, value: bool) -> str:
    lines = text.splitlines(keepends=True)
    section_pattern = re.compile(rf"^{re.escape(section)}:\s*(?:#.*)?$")
    key_pattern = re.compile(rf"^(\s+){re.escape(key)}:\s*(?:true|false)\s*(?:#.*)?$", re.IGNORECASE)
    section_start = next((index for index, line in enumerate(lines) if section_pattern.match(line.rstrip("\n"))), None)
    rendered = "true" if value else "false"
    if section_start is None:
        suffix = "" if not text or text.endswith("\n") else "\n"
        return f"{text}{suffix}{section}:\n  {key}: {rendered}\n"
    section_end = next(
        (
            index
            for index in range(section_start + 1, len(lines))
            if lines[index].strip()
            and not lines[index].lstrip().startswith("#")
            and not lines[index][0].isspace()
        ),
        len(lines),
    )
    for index in range(section_start + 1, section_end):
        match = key_pattern.match(lines[index].rstrip("\n"))
        if match:
            lines[index] = f"{match.group(1)}{key}: {rendered}\n"
            return "".join(lines)
    lines.insert(section_start + 1, f"  {key}: {rendered}\n")
    return "".join(lines)


def yaml_section_bool(text: str, section: str, key: str) -> bool | None:
    lines = text.splitlines()
    section_pattern = re.compile(rf"^{re.escape(section)}:\s*(?:#.*)?$")
    key_pattern = re.compile(rf"^\s+{re.escape(key)}:\s*(true|false)\s*(?:#.*)?$", re.IGNORECASE)
    section_start = next((index for index, line in enumerate(lines) if section_pattern.match(line)), None)
    if section_start is None:
        return None
    for line in lines[section_start + 1 :]:
        if line.strip() and not line.lstrip().startswith("#") and not line[0].isspace():
            break
        match = key_pattern.match(line)
        if match:
            return match.group(1).lower() == "true"
    return None


def replace_provider_block(text: str, provider_id: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    root = f"model_providers.{provider_id}"
    start = None
    end = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^]]+)\]\s*$", line)
        if match and match.group(1) == root:
            start = index
            continue
        if start is not None and match:
            section = match.group(1)
            if section != root and not section.startswith(root + "."):
                end = index
                break
    if start is None:
        prefix = text.rstrip() + ("\n\n" if text.strip() else "")
        return prefix + block.rstrip() + "\n"
    if end is None:
        end = len(lines)
    replacement = [line + "\n" for line in block.rstrip().splitlines()]
    if end < len(lines):
        replacement.append("\n")
    return "".join(lines[:start] + replacement + lines[end:])


def ruby_helper_source(proxy_config: Path) -> str:
    return f'''#!/usr/bin/ruby
require "yaml"

config = YAML.safe_load(File.read({json.dumps(str(proxy_config))}), aliases: true)
keys = Array(config["api-keys"]).map(&:to_s).reject(&:empty?)
abort "CLIProxyAPI client key is missing" if keys.empty?
STDOUT.write(keys.first)
'''


def python_helper_source(proxy_config: Path) -> str:
    return f'''#!/usr/bin/env python3
from pathlib import Path
import sys

text = Path({json.dumps(str(proxy_config))}).read_text(encoding="utf-8")
keys = []
in_keys = False
quotes = chr(39) + chr(34)
for raw in text.splitlines():
    stripped = raw.strip()
    if stripped.startswith("api-keys:"):
        in_keys = True
        rest = stripped.split(":", 1)[1].strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if inner:
                keys.append(inner.strip(quotes))
            break
        if rest:
            keys.append(rest.strip(quotes))
            break
        continue
    if in_keys:
        if stripped.startswith("-"):
            item = stripped[1:].strip().strip(quotes)
            if item:
                keys.append(item)
            continue
        if stripped and not stripped.startswith("#"):
            break
if not keys:
    sys.exit("CLIProxyAPI client key is missing")
sys.stdout.write(keys[0])
'''


def helper_source(proxy_config: Path, helper_path: Path) -> str:
    if helper_path.suffix.lower() == ".rb":
        return ruby_helper_source(proxy_config)
    return python_helper_source(proxy_config)


def chatgpt_auth_state(path: Path) -> tuple[dict[str, object], str | None]:
    try:
        payload = read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        return {}, f"{type(exc).__name__}"
    if not isinstance(payload, dict):
        return {}, "auth root is not an object"
    tokens = payload.get("tokens")
    token_fields = ("id_token", "access_token", "refresh_token")
    has_tokens = isinstance(tokens, dict) and all(
        isinstance(tokens.get(field), str) and bool(tokens.get(field)) for field in token_fields
    )
    return {
        "mode": payload.get("auth_mode"),
        "chatgpt_tokens_present": has_tokens,
        "mode_on_disk": mode(path),
    }, None


def transparent_health_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/__codex_bridge_health", "", "", ""))


def wait_for_transparent_proxy(base_url: str, attempts: int = 20) -> bool:
    request = urllib.request.Request(transparent_health_url(base_url))
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.load(response)
            if response.status == 200 and payload.get("status") == "ok":
                return True
        except (OSError, ValueError, urllib.error.URLError):
            time.sleep(0.2)
    return False


def launch_agent_source(node: Path, runtime: Path, helper: Path, transparent_url: str, upstream_url: str) -> str:
    transparent = urllib.parse.urlparse(transparent_url)
    upstream = urllib.parse.urlparse(upstream_url)
    command, helper_args = helper_invocation(helper)
    payload = {
        "Label": TRANSPARENT_LAUNCH_LABEL,
        "ProgramArguments": [str(node), str(runtime)],
        "EnvironmentVariables": {
            "CODEX_BRIDGE_HELPER": str(helper),
            "CODEX_BRIDGE_HELPER_CMD": command,
            "CODEX_BRIDGE_HELPER_ARGS": json.dumps(helper_args[:-1]),
            "CODEX_BRIDGE_LISTEN_PORT": str(transparent.port or 8318),
            "CODEX_BRIDGE_UPSTREAM_PORT": str(upstream.port or 8317),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 5,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def start_detached_proxy(
    node: Path,
    runtime: Path,
    helper: Path,
    transparent_url: str,
    upstream_url: str,
) -> str | None:
    transparent = urllib.parse.urlparse(transparent_url)
    upstream = urllib.parse.urlparse(upstream_url)
    command, helper_args = helper_invocation(helper)
    env = os.environ.copy()
    env["CODEX_BRIDGE_HELPER"] = str(helper)
    env["CODEX_BRIDGE_HELPER_CMD"] = command
    env["CODEX_BRIDGE_HELPER_ARGS"] = json.dumps(helper_args[:-1])
    env["CODEX_BRIDGE_LISTEN_PORT"] = str(transparent.port or 8318)
    env["CODEX_BRIDGE_UPSTREAM_PORT"] = str(upstream.port or 8317)
    kwargs: dict[str, object] = {
        "args": [str(node), str(runtime)],
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "close_fds": True,
    }
    if is_windows():
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(**kwargs)
    except OSError as exc:
        return f"failed to start transparent proxy: {type(exc).__name__}"
    return None


def start_transparent_proxy(
    node: Path,
    runtime: Path,
    helper: Path,
    transparent_url: str,
    upstream_url: str,
    launch_agent_path: Path,
) -> str | None:
    if platform.system() == "Darwin":
        desired = launch_agent_source(node, runtime, helper, transparent_url, upstream_url)
        current = launch_agent_path.read_text(encoding="utf-8") if launch_agent_path.exists() else None
        changed = current != desired
        if changed:
            atomic_write(launch_agent_path, desired, 0o600)
        return start_launch_agent(launch_agent_path, reload=changed)
    return start_detached_proxy(node, runtime, helper, transparent_url, upstream_url)


def start_launch_agent(path: Path, reload: bool = False) -> str | None:
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{TRANSPARENT_LAUNCH_LABEL}"
    loaded = subprocess.run(
        ["/bin/launchctl", "print", target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    if loaded and reload:
        stopped = subprocess.run(
            ["/bin/launchctl", "bootout", target], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
        if stopped.returncode != 0:
            return "launchctl failed to unload the existing transparent proxy"
        loaded = False
    command = ["/bin/launchctl", "kickstart", "-k", target] if loaded else [
        "/bin/launchctl",
        "bootstrap",
        domain,
        str(path),
    ]
    proc = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return None if proc.returncode == 0 else "launchctl failed to start the transparent proxy"


def cmd_configure_multi_agent(args: argparse.Namespace) -> None:
    proxy_config = Path(args.proxy_config).expanduser()
    if not is_loopback(args.transparent_url):
        emit({"status": "blocked", "error": "transparent URL must be loopback-only"}, 2)
    try:
        current = proxy_config.read_text(encoding="utf-8")
    except OSError as exc:
        emit({"status": "blocked", "error": f"CLIProxyAPI config is unavailable: {type(exc).__name__}"}, 2)
    current_sha = hashlib.sha256(current.encode()).hexdigest()
    if args.expected_sha256 and current_sha != args.expected_sha256:
        emit(
            {
                "status": "blocked",
                "error": "CLIProxyAPI config changed after approval",
                "expected_sha256": args.expected_sha256,
                "actual_sha256": current_sha,
            },
            2,
        )
    updated = replace_yaml_section_bool(current, "codex", "optimize-multi-agent-v2", True)
    changed = updated != current
    if args.apply and changed and not args.expected_sha256:
        emit({"status": "blocked", "error": "preview first and supply --expected-sha256 before applying changes"}, 2)
    result = {
        "status": "planned" if not args.apply else "unchanged",
        "finding_id": "models.codex_multi_agent_v2_compat_disabled",
        "proxy_config": str(proxy_config),
        "config_sha256": current_sha,
        "diff": config_diff(proxy_config, current, updated, context=0),
        "multi_agent_v2_compat": True,
        "backup": None,
        "restarted": False,
        "runtime_verified": False,
        "secrets_redacted": True,
    }
    if not args.apply:
        emit(result)
    if changed:
        result["backup"] = str(backup(proxy_config))
        atomic_write(proxy_config, updated, 0o600)
        result["status"] = "applied"
    verified = proxy_config.read_text(encoding="utf-8")
    if yaml_section_bool(verified, "codex", "optimize-multi-agent-v2") is not True:
        emit({"status": "blocked", "error": "post-write multi-agent compatibility verification failed"}, 2)
    # Homebrew 存在不代表它管理当前监听进程。只写配置，不猜测或切换服务所有者。
    result["restart_hint"] = (
        "Identify the active listener executable and its service manager; preserve local patches. "
        "After approval reload/restart that same service if needed, then run protocol and native spawn checks."
    )
    emit(result)


def cmd_probe_multi_agent(args: argparse.Namespace) -> None:
    if not is_loopback(args.transparent_url):
        emit({"status": "blocked", "error": "transparent URL must be loopback-only"}, 2)
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models:
        emit({"status": "blocked", "error": "--models is required"}, 2)
    results: dict[str, dict] = {}
    for model in models:
        # 随机标记只放进 agent_message 的任务正文，防止固定回复或空任务被误判为成功。
        expected = "CODEX_MULTI_AGENT_OK_" + uuid.uuid4().hex
        payload = {
            "model": model,
            "input": [
                {
                    "type": "agent_message",
                    "id": "amsg_00000000-0000-4000-8000-000000000001",
                    "author": "/root",
                    "recipient": "/root/compat_probe",
                    "content": [
                        {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                        {"type": "encrypted_content", "encrypted_content": f"Reply exactly: {expected}"},
                    ],
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": "00000000-0000-4000-8000-000000000002"
                    },
                }
            ],
            "stream": False,
        }
        request = urllib.request.Request(
            f"{args.transparent_url.rstrip('/')}/responses",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer codex-bridge-probe",
                "Content-Type": "application/json",
                "User-Agent": "Codex Desktop/0.147.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                body = json.load(response)
            # 只接收完成的 Responses assistant 文本；HTTP 200、回显 input 或 reasoning 不算完成任务。
            output = body.get("output", []) if isinstance(body, dict) else []
            texts = []
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                        continue
                    content = item.get("content", [])
                    if isinstance(content, list):
                        texts.extend(part["text"] for part in content if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))
            ok = (response.status == 200 and isinstance(body, dict) and body.get("status") == "completed"
                  and not body.get("error") and "".join(texts).strip() == expected)
            results[model] = {"ok": ok, "status": response.status, "error": None if ok else "response did not complete the agent task with the expected marker"}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            results[model] = {
                "ok": False,
                "status": exc.code,
                "error": "unsupported Codex agent_message input" if "ModelInput" in body else "upstream rejected the probe",
            }
        except (OSError, TimeoutError, ValueError) as exc:
            results[model] = {"ok": False, "status": None, "error": type(exc).__name__}
    passed = all(item["ok"] for item in results.values())
    emit({"status": "passed" if passed else "failed", "probe_scope": "synthetic_agent_message_delivery",
          "native_spawn_tested": False, "results": results, "secrets_redacted": True}, 0 if passed else 2)


def cmd_configure_desktop(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    state_db = Path(args.state_db).expanduser()
    catalog_path = Path(args.catalog).expanduser()
    auth_path = Path(args.auth_file).expanduser()
    helper_path = Path(args.helper).expanduser()
    runtime_path = Path(args.runtime_script).expanduser()
    launch_agent_path = Path(args.launch_agent).expanduser()
    node_path = Path(args.node).expanduser()

    if not is_loopback(args.proxy_url) or not is_loopback(args.transparent_url):
        emit({"status": "blocked", "error": "both proxy URLs must be loopback-only"}, 2)
    if urllib.parse.urlparse(args.proxy_url).netloc == urllib.parse.urlparse(args.transparent_url).netloc:
        emit({"status": "blocked", "error": "transparent and authenticated proxy endpoints must differ"}, 2)
    config, config_error = load_config(config_path)
    if config_error:
        emit({"status": "blocked", "error": f"Codex config is invalid: {config_error}"}, 2)
    counts, inventory_before, inventory_error = thread_inventory(state_db)
    if inventory_error or not inventory_before:
        emit({"status": "blocked", "error": inventory_error or "thread inventory is empty"}, 2)
    history_provider = dominant_provider(counts)
    if history_provider != "openai":
        emit(
            {
                "status": "blocked",
                "error": "desktop-transparent mode requires openai to own the majority of task history",
                "provider_counts": counts,
            },
            2,
        )
    auth, auth_error = chatgpt_auth_state(auth_path)
    if auth_error or auth.get("mode") != "chatgpt" or not auth.get("chatgpt_tokens_present"):
        emit(
            {
                "status": "blocked",
                "error": "a healthy ChatGPT login is required before desktop-transparent mode",
                "auth": auth,
            },
            2,
        )
    if not helper_path.exists() or not node_path.exists():
        emit({"status": "blocked", "error": "Node.js or the CLIProxyAPI credential helper is missing"}, 2)
    try:
        catalog_ids = {entry["slug"] for entry in catalog_models(catalog_path)}
    except Exception as exc:
        emit({"status": "blocked", "error": f"model catalog is invalid: {type(exc).__name__}"}, 2)
    effective_model = args.default_model or config.get("model")
    if effective_model and effective_model not in catalog_ids:
        emit({"status": "blocked", "error": "default model is absent from the proxy catalog"}, 2)

    current = config_path.read_text(encoding="utf-8")
    current_sha = hashlib.sha256(current.encode()).hexdigest()
    updated = replace_top_scalar(current, "model_provider", "openai")
    updated = replace_top_scalar(updated, "openai_base_url", args.transparent_url)
    updated = replace_top_scalar(updated, "model_catalog_json", str(catalog_path))
    if args.default_model:
        updated = replace_top_scalar(updated, "model", args.default_model)
    config_changed = updated != current
    runtime_source = (SKILL_DIR / "scripts" / "transparent_proxy.mjs").read_text(encoding="utf-8")
    runtime_before = runtime_path.read_text(encoding="utf-8") if runtime_path.exists() else None
    runtime_changed = runtime_before != runtime_source
    plist_source = launch_agent_source(node_path, runtime_path, helper_path, args.transparent_url, args.proxy_url) if platform.system() == "Darwin" else None
    plist_before = launch_agent_path.read_text(encoding="utf-8") if plist_source is not None and launch_agent_path.exists() else None
    plist_changed = plist_source is not None and plist_before != plist_source
    healthy = wait_for_transparent_proxy(args.transparent_url, attempts=1)
    service_action = ("restart" if healthy else "start") if runtime_changed or plist_changed or not healthy else "none"
    require_apply_sha(args, current_sha, config_changed or service_action != "none")
    diff = root_config_diff(config_path, current, updated)
    result = {
        "status": "planned" if not args.apply else "unchanged",
        "finding_id": "models.desktop_transparent_proxy_missing",
        "config": str(config_path),
        "config_sha256": current_sha,
        "diff": diff,
        "diff_scope": "managed_root_fields",
        "provider_counts": counts,
        "thread_inventory_before": inventory_before,
        "auth": auth,
        "catalog": str(catalog_path),
        "catalog_model_count": len(catalog_ids),
        "transparent_url": args.transparent_url,
        "authenticated_upstream": args.proxy_url,
        "runtime_script": str(runtime_path),
        "runtime_changes": {
            "changed": runtime_changed,
            "before_sha256": hashlib.sha256(runtime_before.encode()).hexdigest() if runtime_before is not None else None,
            "after_sha256": hashlib.sha256(runtime_source.encode()).hexdigest(),
        },
        "service_started": False,
        "service_action": service_action,
        "launch_agent_changes": {
            "applicable": plist_source is not None,
            "changed": plist_changed,
            "before_sha256": hashlib.sha256(plist_before.encode()).hexdigest() if plist_before is not None else None,
            "after_sha256": hashlib.sha256(plist_source.encode()).hexdigest() if plist_source is not None else None,
        },
        "launch_agent": str(launch_agent_path),
        "backup": None,
        "secrets_redacted": True,
    }
    if not args.apply:
        emit(result)

    if runtime_changed:
        atomic_write(runtime_path, runtime_source, 0o700)
        result["status"] = "applied"
    if service_action != "none":
        launch_error = start_transparent_proxy(
            node_path, runtime_path, helper_path, args.transparent_url, args.proxy_url, launch_agent_path,
        )
        if launch_error or not wait_for_transparent_proxy(args.transparent_url):
            emit({"status": "blocked", "error": launch_error or "transparent proxy health check failed"}, 2)
        result["service_started"] = True
        result["status"] = "applied"

    if updated != current:
        result["backup"] = str(backup(config_path))
        atomic_write(config_path, updated, 0o600)
        result["status"] = "applied"
    verified, verify_error = load_config(config_path)
    if (
        verify_error
        or verified.get("model_provider", "openai") != "openai"
        or verified.get("openai_base_url") != args.transparent_url
        or verified.get("model_catalog_json") != str(catalog_path)
    ):
        emit({"status": "blocked", "error": "post-write config verification failed"}, 2)
    _, inventory_after, after_error = thread_inventory(state_db)
    if after_error or inventory_after != inventory_before:
        emit({"status": "blocked", "error": "thread inventory changed during desktop configuration"}, 2)
    result["thread_inventory_after"] = inventory_after
    result["transparent_proxy_healthy"] = True
    emit(result)


def cmd_audit(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    profile_path = Path(args.profile_config).expanduser()
    state_db = Path(args.state_db).expanduser()
    config, config_error = load_config(config_path)
    profile, profile_error = load_config(profile_path)
    default_provider = config.get("model_provider", "openai")
    root_base_url = config.get("openai_base_url")
    transparent_mode = (
        default_provider == "openai" and isinstance(root_base_url, str) and bool(root_base_url)
    )
    provider = provider_from_config(profile)
    base_url = root_base_url if transparent_mode else provider.get("base_url")
    base_url = base_url if isinstance(base_url, str) else None
    catalog_path = (
        configured_catalog_path(config_path, config.get("model_catalog_json"))
        if transparent_mode
        else configured_catalog_path(profile_path, profile.get("model_catalog_json"))
    )
    provider_counts, inventory, inventory_error = thread_inventory(state_db)
    history_provider = dominant_provider(provider_counts)
    auth, auth_error = chatgpt_auth_state(Path(args.auth_file).expanduser())
    proxy_config_path = Path(args.proxy_config).expanduser()
    try:
        proxy_multi_agent_compat = yaml_section_bool(
            proxy_config_path.read_text(encoding="utf-8"), "codex", "optimize-multi-agent-v2"
        )
    except OSError:
        proxy_multi_agent_compat = None
    findings: list[str] = []
    if config_error:
        findings.append(f"config: {config_error}")
    if profile_error and not transparent_mode:
        findings.append(f"profile: {profile_error}")
    if inventory_error:
        findings.append(f"thread inventory: {inventory_error}")
    if config_path.exists() and not owner_mode_ok(config_path, 0o600):
        findings.append("config.toml is not mode 0600")
    if profile_path.exists() and not owner_mode_ok(profile_path, 0o600):
        findings.append(f"{profile_path.name} is not mode 0600")
    if history_provider and default_provider != history_provider:
        findings.append("default Provider hides the majority of indexed thread history")
    if transparent_mode:
        if auth_error or auth.get("mode") != "chatgpt" or not auth.get("chatgpt_tokens_present"):
            findings.append("desktop-transparent mode is missing a healthy ChatGPT login")
        if not base_url or not is_loopback(base_url):
            findings.append("transparent OpenAI base URL is missing or not loopback-only")
        if proxy_multi_agent_compat is not True:
            findings.append("CLIProxyAPI Codex multi-agent v2 compatibility is disabled")
        token, token_error = (None, None)
    else:
        if profile.get("model_provider") != PROVIDER_ID:
            findings.append(f"bridge profile model_provider is not {PROVIDER_ID}")
        if not base_url or not is_loopback(base_url):
            findings.append("custom Provider is missing or not loopback-only")
        if provider.get("wire_api", "responses") != "responses":
            findings.append("custom Provider wire_api is not responses")
        token, token_error = token_from_provider(provider) if provider else (None, "Provider missing")
    live_ids: set[str] = set()
    discovery_succeeded = False
    live_error = token_error
    if base_url and not token_error:
        try:
            live_ids = live_model_ids(base_url, token, Path(args.models_file) if args.models_file else None)
            discovery_succeeded = True
            live_error = None
        except Exception as exc:  # errors are redacted to type/status only
            live_error = f"{type(exc).__name__}"
            if isinstance(exc, urllib.error.HTTPError):
                live_error += f" status={exc.code}"
    catalog_ids: list[str] = []
    catalog_error = None
    if catalog_path:
        try:
            catalog_ids = [entry["slug"] for entry in catalog_models(catalog_path)]
        except Exception as exc:
            catalog_error = f"{type(exc).__name__}: {exc}"
            findings.append("model catalog is invalid")
    else:
        findings.append("model_catalog_json is not configured")
    current_entries = catalog_models(catalog_path) if catalog_path and not catalog_error else []
    required_routes = required_live_routes(current_entries, config.get("model") if isinstance(config.get("model"), str) else None)
    missing_routes = sorted(model for model in required_routes if discovery_succeeded and model not in live_ids)
    if missing_routes:
        findings.append("visible or default catalog models are missing from the live proxy")
    catalog_readable = bool(catalog_path and not catalog_error)
    catalog_codex_error = (
        codex_rejects_catalog(args.codex, catalog_path, [entry["slug"] for entry in current_entries])
        if catalog_readable
        else None
    )
    if catalog_codex_error:
        findings.append("Codex cannot load the model catalog")
    # 目录落后于上游时，Codex 会停在旧的模型参数和指令上；这里用 sync 的同一套规则比对。
    catalog_in_sync = None
    catalog_sync_error = None
    if catalog_readable and discovery_succeeded:
        try:
            plan = plan_catalog(
                config if transparent_mode else profile, config_path if transparent_mode else profile_path,
                catalog_path, Path(args.state_dir).expanduser(), args.codex, args.transparent_url,
                Path(args.upstream_file) if args.upstream_file else None,
                Path(args.models_file) if args.models_file else None,
            )
            catalog_in_sync = plan["final"] == plan["current"]
            if not catalog_in_sync:
                findings.append("model catalog differs from the live list; run sync")
        except SyncBlocked as blocked:
            # Router 等其他网关拥有的目录不归本工具同步，不算问题。
            if not blocked.payload.get("route_owned_elsewhere"):
                catalog_sync_error = blocked.payload.get("error") or "sync is blocked"
                findings.append("model catalog cannot be regenerated from the live list")
    codex_version = None
    try:
        codex_version = subprocess.run(
            [args.codex, "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        findings.append("Codex CLI is unavailable")
    proxy_version_text, _ = proxy_version(Path(args.proxy_binary).expanduser())
    emit(
        {
            "status": "ready" if not findings and not live_error else "attention",
            "codex": {
                "version": codex_version,
                "config": str(config_path),
                "config_mode": mode(config_path),
                "model": config.get("model"),
                "model_provider": default_provider,
                "bridge_mode": "desktop-transparent" if transparent_mode else "isolated-profile",
                "openai_base_url": redact_url(root_base_url) if transparent_mode else None,
                "service_tier": config.get("service_tier"),
            },
            "auth": auth,
            "history": {
                "state_db": str(state_db),
                "provider_counts": provider_counts,
                "dominant_provider": history_provider,
                "inventory": inventory,
                "error": inventory_error,
            },
            "profile": {
                "name": args.profile,
                "config": str(profile_path),
                "config_mode": mode(profile_path),
                "model": profile.get("model"),
                "model_provider": profile.get("model_provider"),
            },
            "provider": {
                "id": "openai" if transparent_mode else PROVIDER_ID,
                "base_url": redact_url(base_url) if base_url else None,
                "loopback_only": bool(base_url and is_loopback(base_url)),
                "wire_api": "responses" if transparent_mode else provider.get("wire_api", "responses") if provider else None,
                "command_auth": False if transparent_mode else isinstance(provider.get("auth"), dict) if provider else False,
                "live_model_count": len(live_ids),
                "live_error": live_error,
                "multi_agent_v2_compat": proxy_multi_agent_compat,
                "version": proxy_version_text,
            },
            "catalog": {
                "path": str(catalog_path) if catalog_path else None,
                "mode": mode(catalog_path) if catalog_path else None,
                "model_count": len(catalog_ids),
                "codex_load_error": catalog_codex_error,
                "in_sync_with_live_list": catalog_in_sync,
                "sync_error": catalog_sync_error,
                "missing_live_routes": missing_routes,
                "error": catalog_error,
            },
            "findings": findings,
            "secrets_redacted": True,
        },
        0 if not findings and not live_error else 2,
    )


def cmd_configure(args: argparse.Namespace) -> None:
    profile_path = Path(args.profile_config).expanduser()
    catalog_path = Path(args.catalog).expanduser()
    helper_path = Path(args.helper).expanduser()
    proxy_config = Path(args.proxy_config).expanduser()
    if not is_loopback(args.proxy_url):
        emit({"status": "blocked", "error": "proxy URL must be loopback-only"}, 2)
    current = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    updated = replace_top_scalar(current, "model_provider", PROVIDER_ID)
    updated = replace_top_scalar(updated, "model_catalog_json", str(catalog_path))
    if args.default_model:
        updated = replace_top_scalar(updated, "model", args.default_model)
    command, helper_args = helper_invocation(helper_path)
    block = f'''[model_providers.{PROVIDER_ID}]
name = "CLI Proxy API"
base_url = {json.dumps(args.proxy_url)}
wire_api = "responses"
request_max_retries = 4
stream_max_retries = 5
stream_idle_timeout_ms = 300000

[model_providers.{PROVIDER_ID}.auth]
command = {json.dumps(command)}
args = {json.dumps(helper_args)}
timeout_ms = 5000
refresh_interval_ms = 300000'''
    updated = replace_provider_block(updated, PROVIDER_ID, block)
    changed = updated != current or not helper_path.exists()
    result = {
        "status": "planned" if not args.apply else "unchanged",
        "changes": {"config": updated != current, "credential_helper": not helper_path.exists()},
        "backup": None,
        "profile": {"name": args.profile, "config": str(profile_path)},
        "default_config_unchanged": True,
        "provider": {"id": PROVIDER_ID, "base_url": args.proxy_url, "wire_api": "responses"},
        "secrets_redacted": True,
    }
    if args.apply and changed:
        if profile_path.exists() and updated != current:
            result["backup"] = str(backup(profile_path))
        atomic_write(profile_path, updated, 0o600)
        helper_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(helper_path, helper_source(proxy_config, helper_path), 0o700)
        result["status"] = "applied"
    emit(result)


def cmd_restore_default(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    state_db = Path(args.state_db).expanduser()
    native_path = Path(args.native_catalog).expanduser()
    config, config_error = load_config(config_path)
    if config_error:
        emit({"status": "blocked", "error": f"Codex config is invalid: {config_error}"}, 2)
    counts, inventory_before, inventory_error = thread_inventory(state_db)
    if inventory_error or not inventory_before:
        emit({"status": "blocked", "error": inventory_error or "thread inventory is empty"}, 2)
    target_provider = args.provider or dominant_provider(counts)
    if not target_provider:
        emit({"status": "blocked", "error": "cannot determine the history Provider"}, 2)
    largest = max(counts.values()) if counts else 0
    if counts.get(target_provider, 0) < largest and not args.allow_minority_provider:
        emit(
            {
                "status": "blocked",
                "error": "target Provider is not the dominant thread-history Provider",
                "provider_counts": counts,
            },
            2,
        )
    native_models = catalog_models(native_path)
    native_ids = {entry["slug"] for entry in native_models}
    target_model = args.model or config.get("model")
    if target_model not in native_ids:
        try:
            target_model = preferred_native_model(native_models)["slug"]
        except ValueError as exc:
            emit({"status": "blocked", "error": str(exc)}, 2)
    current = config_path.read_text(encoding="utf-8")
    current_sha = hashlib.sha256(current.encode()).hexdigest()
    updated = replace_top_scalar(current, "model_provider", target_provider)
    updated = replace_top_scalar(updated, "model", target_model)
    updated = remove_top_scalar(updated, "model_catalog_json")
    updated = remove_top_scalar(updated, "openai_base_url")
    require_apply_sha(args, current_sha, updated != current)
    diff = root_config_diff(config_path, current, updated)
    result = {
        "status": "planned" if not args.apply else "unchanged",
        "finding_id": "threads.default_provider_history_scope_mismatch",
        "config": str(config_path),
        "config_sha256": current_sha,
        "diff": diff,
        "diff_scope": "managed_root_fields",
        "provider_counts": counts,
        "target_provider": target_provider,
        "target_model": target_model,
        "thread_inventory_before": inventory_before,
        "backup": None,
        "secrets_redacted": True,
    }
    if args.apply and updated != current:
        result["backup"] = str(backup(config_path))
        atomic_write(config_path, updated, 0o600)
        verified, verify_error = load_config(config_path)
        if verify_error or verified.get("model_provider", "openai") != target_provider:
            emit({"status": "blocked", "error": "post-write config verification failed"}, 2)
        _, inventory_after, after_error = thread_inventory(state_db)
        if after_error or inventory_after != inventory_before:
            emit({"status": "blocked", "error": "thread inventory changed during config repair"}, 2)
        result["thread_inventory_after"] = inventory_after
        result["status"] = "applied"
    emit(result)


LEGACY_STATE_FILES = {
    "catalog-policy.json": 'hide models with overrides.json, e.g. {"<id>": {"visibility": "hide"}}',
    "enabled-manifests.json": "bundled manifests were removed; models now come from the live list",
    "state.json": "ownership state is no longer needed; the catalog is fully generated",
}


def configured_models(config: dict, config_path: Path) -> tuple[list[str], list[str]]:
    """Return (required, protected). Required models must be in the catalog; protected ones are never removed."""
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    required = {config.get("model"), config.get("review_model"), agents.get("default_subagent_model")}
    protected: set[object] = set()
    profiles = config.get("profiles") if isinstance(config.get("profiles"), dict) else {}
    for profile in profiles.values():
        if isinstance(profile, dict):
            protected.add(profile.get("model"))
    for name, role in agents.items():
        if not isinstance(role, dict) or not isinstance(role.get("config_file"), str) or not role["config_file"].strip():
            continue
        role_path = configured_catalog_path(config_path, role["config_file"])
        role_config, error = load_config(role_path)
        if error:
            raise SyncBlocked({"error": f"agent role {name!r} config_file is unreadable: {error}"})
        protected.add(role_config.get("model"))
    keep = lambda names: sorted(name for name in names if isinstance(name, str) and name)
    return keep(required), keep(protected - required)


def catalog_changes(current: list[dict], final: list[dict]) -> dict:
    """Changed field names only; values such as instructions are too long for a receipt."""
    before = {entry["slug"]: entry for entry in current}
    after = {entry["slug"]: entry for entry in final}
    updated = {
        slug: sorted(field for field in before[slug].keys() | after[slug].keys()
                     if before[slug].get(field) != after[slug].get(field) or (field in before[slug]) != (field in after[slug]))
        for slug in sorted(before.keys() & after.keys())
        if before[slug] != after[slug]
    }
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "updated": updated,
        "order_changed": [entry["slug"] for entry in current] != [entry["slug"] for entry in final],
    }


def catalog_order(entry: dict) -> tuple[float, str]:
    priority = entry.get("priority")
    return (priority if isinstance(priority, int) and not isinstance(priority, bool) else float("inf"), entry["slug"])


def plan_catalog(config: dict, config_path: Path, catalog_path: Path, state_dir: Path, codex: str,
                 transparent_url: str = DEFAULT_TRANSPARENT_PROXY_URL,
                 upstream_file: Path | None = None, models_file: Path | None = None) -> dict:
    """Build the catalog from the live Codex-format list, personal overrides and extra manifests."""
    mode_name, base_url, token = sync_route(config, transparent_url)
    client_version = codex_client_version(codex)
    if not client_version and not upstream_file:
        raise SyncBlocked({"error": "cannot read the installed Codex version; the live list depends on it"})
    try:
        if upstream_file:
            payload = read_json(upstream_file)
        else:
            query = urllib.parse.urlencode({"client_version": client_version})
            payload = fetch_json(f"{base_url.rstrip('/')}/models?{query}", token)
        upstream = catalog_entries(payload, "live model list")
    except Exception as exc:  # errors are redacted to type/status only
        error = f"live model list failed: {type(exc).__name__}"
        if isinstance(exc, urllib.error.HTTPError):
            error += f" status={exc.code}"
        raise SyncBlocked({"error": error})
    if not upstream:
        raise SyncBlocked({"error": "live model list is empty"})
    overrides_path = state_dir / "overrides.json"
    try:
        overrides = load_overrides(overrides_path)
    except (OSError, ValueError) as exc:
        raise SyncBlocked({"error": f"overrides are invalid: {exc}", "overrides": str(overrides_path)})
    upstream_ids = {entry["slug"] for entry in upstream}
    extras, shadowed, manifest_errors = extra_manifests(state_dir, upstream_ids)
    if manifest_errors:
        raise SyncBlocked({"manifest_errors": manifest_errors})
    if extras:
        try:
            live_ids = live_model_ids(base_url, token, models_file)
        except Exception as exc:
            raise SyncBlocked({"error": f"live route discovery failed: {type(exc).__name__}"})
        missing = sorted(item["slug"] for item in extras if item["slug"] not in live_ids)
        if missing:
            raise SyncBlocked({"missing_live_routes": missing})
    templates = {entry["slug"]: entry for entry in upstream}
    template_fallbacks: dict[str, dict[str, str]] = {}
    final = copy.deepcopy(upstream) + [build_entry(item, templates, template_fallbacks) for item in extras]
    unmatched, override_errors = apply_overrides(final, overrides)
    if override_errors:
        raise SyncBlocked({"error": "overrides produce invalid entries", "override_errors": override_errors})
    final.sort(key=catalog_order)
    existing_error = None
    try:
        current = catalog_models(catalog_path) if catalog_path.exists() else []
    except (OSError, ValueError) as exc:
        # 目录完全由本工具生成；损坏时直接重建，不让每日同步永久卡住。
        current, existing_error = [], f"{type(exc).__name__}: {exc}"
    final_ids = {entry["slug"] for entry in final}
    current_ids = {entry["slug"] for entry in current}
    required, protected = configured_models(config, config_path)
    missing_required = [name for name in required if name not in final_ids]
    removed_protected = [name for name in protected if name in current_ids and name not in final_ids]
    if missing_required or removed_protected:
        raise SyncBlocked({
            "error": "the live list no longer has a model the config uses",
            "missing_configured_models": sorted(missing_required + removed_protected),
        })
    legacy = {name: hint for name, hint in LEGACY_STATE_FILES.items() if (state_dir / name).exists()}
    return {
        "final": final,
        "current": current,
        "current_valid": existing_error is None,
        "receipt": {
            "catalog": str(catalog_path),
            "source": {"mode": mode_name, "base_url": redact_url(base_url), "client_version": client_version,
                       "live_model_count": len(upstream)},
            "changes": catalog_changes(current, final),
            "existing_catalog_invalid": existing_error,
            "overrides": str(overrides_path) if overrides_path.exists() else None,
            "overrides_unmatched": unmatched,
            "extra_models": [item["slug"] for item in extras],
            "manifests_shadowed_by_live_list": shadowed,
            "template_fallbacks": template_fallbacks,
            "legacy_files_ignored": legacy,
            "secrets_redacted": True,
        },
    }


def sync_catalog_path(args: argparse.Namespace, config: dict, config_path: Path) -> Path:
    if args.catalog:
        return Path(args.catalog).expanduser()
    return configured_catalog_path(config_path, config.get("model_catalog_json")) or (
        DEFAULT_CODEX_HOME / "model-catalog-cli-proxy.json"
    )


def write_checked_catalog(target: Path, models: list[dict], codex: str, keep_previous: bool) -> str | None:
    """Write only a catalog Codex itself can load; keep one copy of the previous good catalog."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".candidate", dir=target.parent)
    candidate = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"models": models}, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(candidate, 0o600)
        rejected = codex_rejects_catalog(codex, candidate, [entry["slug"] for entry in models])
        if rejected:
            raise SyncBlocked({"error": "Codex cannot load the generated catalog", "codex_error": rejected})
        previous = None
        if keep_previous and target.exists():
            previous = target.with_name(target.name + ".previous")
            shutil.copy2(target, previous)
            os.chmod(previous, 0o600)
        os.replace(candidate, target)
        return str(previous) if previous else None
    finally:
        if candidate.exists():
            candidate.unlink()


def cmd_sync(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    config, config_error = load_config(config_path)
    if config_error:
        emit({"status": "blocked", "error": f"Codex config is invalid: {config_error}"}, 2)
    target_path = sync_catalog_path(args, config, config_path)
    try:
        plan = plan_catalog(
            config, config_path, target_path, Path(args.state_dir).expanduser(), args.codex, args.transparent_url,
            Path(args.upstream_file) if args.upstream_file else None,
            Path(args.models_file) if args.models_file else None,
        )
        result = {"status": "planned" if not args.apply else "unchanged", **plan["receipt"], "backup": None}
        if args.apply and (plan["final"] != plan["current"] or not plan["current_valid"]):
            # A corrupt current file is not worth keeping as the rollback copy.
            result["backup"] = write_checked_catalog(target_path, plan["final"], args.codex, plan["current_valid"])
            result["status"] = "applied"
    except SyncBlocked as blocked:
        emit({**blocked.payload, "secrets_redacted": True}, 2)
    except Exception as exc:  # unattended runs still need a readable receipt
        emit({"status": "blocked", "error": f"unexpected {type(exc).__name__}", "secrets_redacted": True}, 2)
    emit(result)


def probe_prompt(shell: bool, tool_sequence: bool) -> str:
    if tool_sequence:
        return (
            "Use the shell tool twice and in this exact order: first run pwd, "
            "then run git --version. Both must succeed. After both succeed, "
            "reply with exactly CODEX_BRIDGE_TOOL_SEQUENCE_OK. "
            "Do not simulate either command and do not call any other tool."
        )
    if shell:
        return (
            "Use the shell tool to run exactly this command: pwd. "
            "After it succeeds, reply with exactly: CODEX_BRIDGE_SHELL_OK. "
            "Do not simulate the command and do not call any other tool."
        )
    return "Reply with exactly: CODEX_BRIDGE_OK"


def probe_catalog_path(args: argparse.Namespace) -> Path:
    if args.catalog:
        return Path(args.catalog).expanduser()
    if not args.desktop:
        return DEFAULT_CODEX_HOME / "model-catalog-cli-proxy.json"
    config_path = Path(args.config).expanduser()
    config, config_error = load_config(config_path)
    if config_error:
        emit(
            {
                "status": "blocked",
                "error": f"cannot read the desktop Codex config: {config_error}",
                "config": str(config_path),
            },
            2,
        )
    catalog = configured_catalog_path(config_path, config.get("model_catalog_json"))
    if catalog is None:
        emit(
            {
                "status": "blocked",
                "error": "desktop Codex config has no model_catalog_json; pass --catalog to override",
                "config": str(config_path),
            },
            2,
        )
    return catalog


def cmd_probe(args: argparse.Namespace) -> None:
    if Path(args.config).expanduser().resolve() != (DEFAULT_CODEX_HOME / "config.toml").resolve():
        emit({"status": "blocked", "error": "alternate --config files are not supported by Codex exec; use the active Codex config and --profile for a named profile"}, 2)
    target_path = probe_catalog_path(args)
    entries = {entry["slug"]: entry for entry in catalog_models(target_path)}
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models:
        emit({"status": "blocked", "error": "--models is required"}, 2)
    results: dict[str, dict] = {}
    for model in models:
        if model not in entries:
            results[model] = {"ok": False, "error": "model is absent from the catalog"}
            continue
        if args.fast and "fast" not in entries[model].get("additional_speed_tiers", []):
            results[model] = {"ok": False, "error": "model does not advertise Fast"}
            continue
        with tempfile.TemporaryDirectory(prefix="codex-model-probe-") as temp_dir:
            output = Path(temp_dir) / "answer.txt"
            command = [
                args.codex,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--model",
                model,
                "--output-last-message",
                str(output),
            ]
            if not args.desktop:
                command.extend(["--profile", args.profile])
            command.extend(["--config", "model_catalog_json=" + json.dumps(str(target_path.resolve()))])
            if args.fast:
                command.extend(["--config", 'service_tier="fast"', "--config", "features.fast_mode=true"])
            if args.shell or args.tool_sequence:
                command.append("--json")
            prompt = probe_prompt(args.shell, args.tool_sequence)
            command.append(prompt)
            try:
                proc = subprocess.run(
                    command,
                    stdout=subprocess.PIPE if args.shell or args.tool_sequence else subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=args.timeout,
                    check=False,
                    start_new_session=True,
                )
                answer = output.read_text(encoding="utf-8").strip() if output.exists() else ""
                shell_executed = False
                tool_sequence_executed = False
                completed_commands: list[str] = []
                if args.shell or args.tool_sequence:
                    for line in proc.stdout.splitlines():
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        item = event.get("item", {})
                        command_text = item.get("command", "")
                        if (
                            event.get("type") == "item.completed"
                            and item.get("type") == "command_execution"
                            and item.get("status") == "completed"
                            and item.get("exit_code") == 0
                            and re.search(r"(?:^|[ ;])pwd(?:$|[ ;])", command_text)
                        ):
                            shell_executed = True
                        if (
                            event.get("type") == "item.completed"
                            and item.get("type") == "command_execution"
                            and item.get("status") == "completed"
                            and item.get("exit_code") == 0
                        ):
                            completed_commands.append(command_text)
                    tool_sequence_executed = (
                        len(completed_commands) >= 2
                        and re.search(r"(?:^|[ ;])pwd(?:$|[ ;])", completed_commands[0]) is not None
                        and "git --version" in completed_commands[1]
                    )
                expected = (
                    "CODEX_BRIDGE_TOOL_SEQUENCE_OK"
                    if args.tool_sequence
                    else "CODEX_BRIDGE_SHELL_OK"
                    if args.shell
                    else "CODEX_BRIDGE_OK"
                )
                execution_ok = (
                    tool_sequence_executed
                    if args.tool_sequence
                    else shell_executed
                    if args.shell
                    else True
                )
                ok = proc.returncode == 0 and expected == answer and execution_ok
                results[model] = {
                    "ok": ok,
                    "fast": bool(args.fast),
                    # CLI 完成只证明请求执行；未提供上游 tier 证据时不推断实际计费档位。
                    "requested_service_tier": "fast" if args.fast else None,
                    "served_service_tier": None,
                    "shell": bool(args.shell),
                    "shell_executed": shell_executed if args.shell else None,
                    "tool_sequence": bool(args.tool_sequence),
                    "tool_sequence_executed": tool_sequence_executed if args.tool_sequence else None,
                    "exit_code": proc.returncode,
                    "error": None if ok else (
                        "Codex did not execute the required ordered shell-tool sequence"
                        if args.tool_sequence
                        else "Codex did not execute a successful pwd shell call"
                        if args.shell
                        else "Codex did not complete the expected Responses turn"
                    ),
                }
            except subprocess.TimeoutExpired:
                results[model] = {
                    "ok": False,
                    "fast": bool(args.fast),
                    "shell": bool(args.shell),
                    "error": "probe timed out",
                }
            except OSError as exc:
                results[model] = {
                    "ok": False,
                    "fast": bool(args.fast),
                    "shell": bool(args.shell),
                    "error": type(exc).__name__,
                }
    emit(
        {
            "status": "passed" if all(item["ok"] for item in results.values()) else "failed",
            "catalog": str(target_path),
            "results": results,
        },
        0 if all(item["ok"] for item in results.values()) else 2,
    )


def cmd_validate_manifest(args: argparse.Namespace) -> None:
    path = Path(args.path).expanduser()
    data = read_json(path)
    errors = validate_manifest(data) if isinstance(data, dict) else ["manifest root must be an object"]
    emit({"status": "valid" if not errors else "invalid", "path": str(path), "errors": errors}, 0 if not errors else 2)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Codex CLI model bridge")
    sub = root.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit")
    audit.add_argument("--config", default=str(DEFAULT_CODEX_HOME / "config.toml"))
    audit.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    audit.add_argument("--profile-config", default=str(DEFAULT_PROFILE_CONFIG))
    audit.add_argument("--state-db", default=str(DEFAULT_STATE_DB))
    audit.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    audit.add_argument("--auth-file", default=str(DEFAULT_AUTH_FILE))
    audit.add_argument("--proxy-config", default=str(DEFAULT_PROXY_CONFIG))
    audit.add_argument("--models-file")
    audit.add_argument("--transparent-url", default=DEFAULT_TRANSPARENT_PROXY_URL)
    audit.add_argument("--upstream-file", help="Tests only: Codex-format live list fixture")
    audit.add_argument("--codex", default="codex")
    audit.add_argument("--proxy-binary", default=str(DEFAULT_PROXY_BINARY))
    audit.set_defaults(func=cmd_audit)

    configure = sub.add_parser("configure")
    configure.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    configure.add_argument("--profile-config", default=str(DEFAULT_PROFILE_CONFIG))
    configure.add_argument("--catalog", default=str(DEFAULT_CODEX_HOME / "model-catalog-cli-proxy.json"))
    configure.add_argument("--helper", default=str(DEFAULT_HELPER))
    configure.add_argument("--proxy-config", default=str(DEFAULT_PROXY_CONFIG))
    configure.add_argument("--proxy-url", default=DEFAULT_PROXY_URL)
    configure.add_argument("--default-model")
    configure.add_argument("--apply", action="store_true")
    configure.set_defaults(func=cmd_configure)

    desktop = sub.add_parser("configure-desktop")
    desktop.add_argument("--config", default=str(DEFAULT_CODEX_HOME / "config.toml"))
    desktop.add_argument("--state-db", default=str(DEFAULT_STATE_DB))
    desktop.add_argument("--catalog", default=str(DEFAULT_CODEX_HOME / "model-catalog-cli-proxy.json"))
    desktop.add_argument("--auth-file", default=str(DEFAULT_AUTH_FILE))
    desktop.add_argument("--helper", default=str(DEFAULT_HELPER))
    desktop.add_argument("--proxy-url", default=DEFAULT_PROXY_URL)
    desktop.add_argument("--transparent-url", default=DEFAULT_TRANSPARENT_PROXY_URL)
    desktop.add_argument("--runtime-script", default=str(DEFAULT_TRANSPARENT_RUNTIME))
    desktop.add_argument("--launch-agent", default=str(DEFAULT_LAUNCH_AGENT))
    desktop.add_argument("--node", default=str(default_node()))
    desktop.add_argument("--default-model", help="Explicitly change the default model; otherwise preserve the current value")
    desktop.add_argument("--expected-sha256")
    desktop.add_argument("--apply", action="store_true")
    desktop.set_defaults(func=cmd_configure_desktop)

    multi_agent = sub.add_parser("configure-multi-agent")
    multi_agent.add_argument("--proxy-config", default=str(DEFAULT_PROXY_CONFIG))
    multi_agent.add_argument("--transparent-url", default=DEFAULT_TRANSPARENT_PROXY_URL)
    multi_agent.add_argument("--expected-sha256")
    multi_agent.add_argument("--apply", action="store_true")
    multi_agent.set_defaults(func=cmd_configure_multi_agent)

    restore = sub.add_parser("restore-default")
    restore.add_argument("--config", default=str(DEFAULT_CODEX_HOME / "config.toml"))
    restore.add_argument("--state-db", default=str(DEFAULT_STATE_DB))
    restore.add_argument("--native-catalog", default=str(DEFAULT_CODEX_HOME / "models_cache.json"))
    restore.add_argument("--provider")
    restore.add_argument("--model")
    restore.add_argument("--expected-sha256")
    restore.add_argument("--allow-minority-provider", action="store_true")
    restore.add_argument("--apply", action="store_true")
    restore.set_defaults(func=cmd_restore_default)

    sync = sub.add_parser("sync")
    sync.add_argument("--config", default=str(DEFAULT_CODEX_HOME / "config.toml"))
    sync.add_argument("--catalog", help="Defaults to the config's model_catalog_json")
    sync.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    sync.add_argument("--transparent-url", default=DEFAULT_TRANSPARENT_PROXY_URL)
    sync.add_argument("--codex", default="codex")
    sync.add_argument("--upstream-file", help="Tests only: Codex-format live list fixture")
    sync.add_argument("--models-file", help="Tests only: OpenAI-format /models fixture")
    sync.add_argument("--apply", action="store_true")
    sync.set_defaults(func=cmd_sync)

    probe = sub.add_parser("probe")
    probe.add_argument("--catalog")
    probe.add_argument("--config", default=str(DEFAULT_CODEX_HOME / "config.toml"), help="Active Codex root config only; alternate files are rejected")
    probe.add_argument("--profile", default=DEFAULT_PROFILE_NAME)
    probe.add_argument("--desktop", action="store_true")
    probe.add_argument("--models", required=True)
    probe.add_argument("--fast", action="store_true")
    probe.add_argument("--shell", action="store_true", help="Require a real read-only pwd tool execution")
    probe.add_argument(
        "--tool-sequence",
        action="store_true",
        help="Require ordered successful pwd and git --version shell executions",
    )
    probe.add_argument("--timeout", type=int, default=180)
    probe.add_argument("--codex", default="codex")
    probe.set_defaults(func=cmd_probe)

    probe_multi_agent = sub.add_parser("probe-multi-agent")
    probe_multi_agent.add_argument("--transparent-url", default=DEFAULT_TRANSPARENT_PROXY_URL)
    probe_multi_agent.add_argument("--models", required=True)
    probe_multi_agent.add_argument("--timeout", type=int, default=120)
    probe_multi_agent.set_defaults(func=cmd_probe_multi_agent)

    validate = sub.add_parser("validate-manifest")
    validate.add_argument("path")
    validate.set_defaults(func=cmd_validate_manifest)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
