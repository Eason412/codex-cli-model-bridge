import argparse
import contextlib
import io
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bridge.py"


def load_bridge(name: str = "bridge"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_bridge(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def run_probe_fixture(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Use an isolated active root rather than pretending --config loads it."""
    module = load_bridge("probe_fixture")
    output = io.StringIO()
    with patch.object(module, "DEFAULT_CODEX_HOME", root), contextlib.redirect_stdout(output):
        parsed = module.parser().parse_args(list(args))
        try:
            module.cmd_probe(parsed)
        except SystemExit as exc:
            return subprocess.CompletedProcess(args, exc.code, output.getvalue(), "")
    raise AssertionError("probe must emit a result")


def native_template() -> dict:
    return {
        "slug": "gpt-5.6-sol",
        "display_name": "GPT-5.6-Sol",
        "description": "native",
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [{"effort": "high", "description": "high"}],
        "visibility": "list",
        "supported_in_api": True,
        "priority": 1,
        "additional_speed_tiers": ["fast"],
        "service_tiers": [{"id": "priority", "name": "Fast"}],
        "context_window": 272000,
        "max_context_window": 272000,
        "effective_context_window_percent": 95,
        "input_modalities": ["text", "image"],
        "supports_search_tool": True,
        "supports_image_detail_original": False,
        "supports_parallel_tool_calls": True,
        "tool_mode": "code_mode_only",
        "model_messages": {"instructions_template": "test"},
    }


def sample_manifest() -> dict:
    return {
        "schema_version": 1,
        "slug": "gpt-6-astra",
        "display_name": "GPT-6 Astra",
        "description": "fixture",
        "template_slug": "gpt-5.6-sol",
        "context_window": 1000000,
        "effective_context_window_percent": 95,
        "default_reasoning_level": "medium",
        "reasoning_efforts": ["low", "medium", "high"],
        "input_modalities": ["text", "image"],
        "supports_search_tool": False,
        "priority": 0,
    }


class BridgeTests(unittest.TestCase):
    def test_multi_agent_probe_requires_delivered_task_result(self) -> None:
        module = load_bridge()
        for scenario in ["valid", "empty", "wrong", "incomplete", "invalid_json", "input_echo", "error"]:
            with self.subTest(scenario=scenario):
                def fake_open(request, timeout):
                    task = json.loads(request.data)["input"][0]["content"][1]["encrypted_content"]
                    marker = task.removeprefix("Reply exactly: ")
                    body = {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": marker}]}]}
                    if scenario == "empty": body["output"] = []
                    if scenario == "wrong": body["output"][0]["content"][0]["text"] = "Please provide a task"
                    if scenario == "incomplete": body["status"] = "incomplete"
                    if scenario == "input_echo": body["output"][0]["role"] = "user"
                    if scenario == "error": body["error"] = {"message": "fixture failure"}
                    response = io.BytesIO(b"not-json" if scenario == "invalid_json" else json.dumps(body).encode())
                    response.status = 200
                    return response
                output = io.StringIO()
                args = module.parser().parse_args(["probe-multi-agent", "--models", "fixture-model"])
                with patch.object(module.urllib.request, "urlopen", side_effect=fake_open), contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as caught:
                        module.cmd_probe_multi_agent(args)
                self.assertEqual(caught.exception.code, 0 if scenario == "valid" else 2)
                result = json.loads(output.getvalue())
                self.assertFalse(result["native_spawn_tested"])
                self.assertNotIn("Please provide a task", output.getvalue())

    def test_multi_agent_configuration_requires_approval_and_never_restarts(self) -> None:
        module = load_bridge()
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.yaml"
            before = "codex:\n  optimize-multi-agent-v2: false\n"
            config.write_text(before, encoding="utf-8")
            for approved in [False, True]:
                command = ["configure-multi-agent", "--proxy-config", str(config), "--apply"]
                if approved: command += ["--expected-sha256", hashlib.sha256(before.encode()).hexdigest()]
                args = module.parser().parse_args(command)
                output = io.StringIO()
                with patch.object(module.subprocess, "run") as runner, patch.object(module.urllib.request, "urlopen") as http, contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as caught:
                        module.cmd_configure_multi_agent(args)
                    runner.assert_not_called()
                    http.assert_not_called()
                self.assertEqual(caught.exception.code, 0 if approved else 2)
                if not approved: self.assertEqual(config.read_text(), before)
                else:
                    result = json.loads(output.getvalue())
                    self.assertFalse(result["restarted"])
                    self.assertFalse(result["runtime_verified"])
                    self.assertTrue(Path(result["backup"]).exists())

    def test_extra_manifest_template_is_optional_and_supersedes_is_rejected(self) -> None:
        module = load_bridge()
        manifest = sample_manifest()
        manifest.pop("template_slug")
        self.assertEqual(module.validate_manifest(manifest), [])
        self.assertIn(
            "supersedes is no longer supported; retired IDs disappear with the live list",
            module.validate_manifest({**manifest, "supersedes": ["old-id"]}),
        )

    def test_required_live_routes_include_visible_and_default_models(self) -> None:
        module = load_bridge()

        required = module.required_live_routes(
            [
                {"slug": "gpt-5.6-sol", "visibility": "list", "supported_in_api": True},
                {"slug": "gpt-5.5", "visibility": "hide", "supported_in_api": True},
                {"slug": "gpt-5.6-sol-wm", "visibility": "list", "supported_in_api": False},
                {"slug": "grok-4.6", "visibility": "list", "supported_in_api": True},
            ],
            "gpt-5.6-sol",
        )

        self.assertEqual(required, ["gpt-5.6-sol", "grok-4.6"])

    def test_parse_proxy_version(self) -> None:

        module = load_bridge()

        self.assertEqual(
            module.parse_proxy_version("CLIProxyAPI Version: 7.2.130, Commit: Homebrew"),
            (7, 2, 130),
        )
        self.assertIsNone(module.parse_proxy_version("unknown"))

    def test_build_entry_can_disable_inherited_code_mode(self) -> None:
        manifest = {
            "template_slug": "gpt-5.6-sol",
            "slug": "grok-4.6",
            "display_name": "Grok 4.6",
            "description": "test",
            "default_reasoning_level": "high",
            "reasoning_efforts": ["high"],
            "priority": 40,
            "context_window": 500000,
            "effective_context_window_percent": 95,
            "input_modalities": ["text"],
            "tool_mode": None,
        }

        module = load_bridge()
        result = module.build_entry(manifest, {"gpt-5.6-sol": native_template()})

        self.assertNotIn("tool_mode", result)

    def test_shell_probe_requires_command_execution_event(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            catalog = root / "catalog.json"
            fake_codex = root / "codex"
            catalog.write_text(json.dumps({"models": [{"slug": "grok-4.6"}]}), encoding="utf-8")
            for scenario, command_exit in [("valid", 0), ("marker_only", None), ("failed_command", 1),
                                           ("wrong_marker", 0)]:
                with self.subTest(scenario=scenario):
                    fake_codex.write_text(
                        f"#!{sys.executable}\n"
                        "import json, pathlib, sys\n"
                        "assert '--json' in sys.argv\n"
                        "assert 'CODEX_BRIDGE_SHELL_OK' in sys.argv[-1]\n"
                        "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                        f"out.write_text({('not CODEX_BRIDGE_SHELL_OK' if scenario == 'wrong_marker' else 'CODEX_BRIDGE_SHELL_OK')!r}, encoding='utf-8')\n"
                        f"command_exit = {command_exit!r}\n"
                        "if command_exit is not None:\n"
                        " print(json.dumps({'type':'item.completed','item':{'type':'command_execution',"
                        "'command':'/bin/zsh -lc pwd','status':'completed','exit_code':command_exit}}))\n",
                        encoding="utf-8",
                    )
                    fake_codex.chmod(0o700)
                    proc = run_bridge(
                        "probe", "--desktop", "--shell", "--models", "grok-4.6",
                        "--catalog", str(catalog), "--codex", str(fake_codex),
                        env={"HOME": str(root), "CODEX_HOME": str(root)},
                    )
                    valid = scenario == "valid"
                    self.assertEqual(proc.returncode, 0 if valid else 2, proc.stderr or proc.stdout)
                    result = json.loads(proc.stdout)["results"]["grok-4.6"]
                    self.assertEqual(result["shell_executed"], command_exit == 0)
                    self.assertEqual(result["ok"], valid)

    def test_desktop_probe_uses_active_config_catalog_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            catalog = root / "router-catalog.json"
            fake_codex = root / "codex"
            model = "zai-coding/glm-5.3-flash"
            config.write_text(
                f"model_catalog_json = {json.dumps(str(catalog))}\n",
                encoding="utf-8",
            )
            catalog.write_text(json.dumps({"models": [{"slug": model}]}), encoding="utf-8")
            fake_codex.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                "out.write_text('CODEX_BRIDGE_OK', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o700)

            proc = run_probe_fixture(
                root,
                "probe",
                "--desktop",
                "--models",
                model,
                "--config",
                str(config),
                "--codex",
                str(fake_codex),
            )

            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["catalog"], str(catalog))
            self.assertTrue(payload["results"][model]["ok"])

    def test_desktop_probe_explicit_catalog_overrides_active_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            catalog = root / "explicit-catalog.json"
            fake_codex = root / "codex"
            model = "grok-4.6"
            config.write_text(
                'model_catalog_json = "/missing/router-catalog.json"\n',
                encoding="utf-8",
            )
            catalog.write_text(json.dumps({"models": [{"slug": model}]}), encoding="utf-8")
            fake_codex.write_text(
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                "out.write_text('CODEX_BRIDGE_OK', encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o700)

            proc = run_probe_fixture(
                root,
                "probe",
                "--desktop",
                "--models",
                model,
                "--config",
                str(config),
                "--catalog",
                str(catalog),
                "--codex",
                str(fake_codex),
            )

            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertEqual(json.loads(proc.stdout)["catalog"], str(catalog))

    def test_desktop_probe_blocks_when_config_has_no_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")

            proc = run_probe_fixture(
                Path(raw),
                "probe",
                "--desktop",
                "--models",
                "gpt-5.6-sol",
                "--config",
                str(config),
            )

            self.assertEqual(proc.returncode, 2)
            self.assertIn("no model_catalog_json", json.loads(proc.stdout)["error"])

    def test_profile_probe_keeps_cli_proxy_catalog_default(self) -> None:
        module = load_bridge()

        target = module.probe_catalog_path(
            argparse.Namespace(catalog=None, desktop=False)
        )

        self.assertEqual(
            target,
            module.DEFAULT_CODEX_HOME / "model-catalog-cli-proxy.json",
        )

    def test_tool_sequence_probe_requires_both_commands_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            catalog = root / "catalog.json"
            fake_codex = root / "codex"
            catalog.write_text(json.dumps({"models": [{"slug": "grok-4.6"}]}), encoding="utf-8")
            commands = [('/bin/zsh -lc pwd', 0), ("/bin/zsh -lc 'git --version'", 0)]
            scenarios = {
                "valid": (commands, 0),
                "marker_only": ([], 0),
                "only_pwd": (commands[:1], 0),
                "reversed": (list(reversed(commands)), 0),
                "failed_command": ([commands[0], (commands[1][0], 1)], 0),
                "process_failure": (commands, 1),
                "wrong_marker": (commands, 0),
            }
            for scenario, (events, process_exit) in scenarios.items():
                with self.subTest(scenario=scenario):
                    fake_codex.write_text(
                        f"#!{sys.executable}\n"
                        "import json, pathlib, sys\n"
                        "assert '--json' in sys.argv\n"
                        "prompt = sys.argv[-1]\n"
                        "assert 'first run pwd' in prompt and 'then run git --version' in prompt\n"
                        "assert 'CODEX_BRIDGE_TOOL_SEQUENCE_OK' in prompt\n"
                        "assert 'CODEX_BRIDGE_SHELL_OK' not in prompt\n"
                        "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                        f"out.write_text({('not CODEX_BRIDGE_TOOL_SEQUENCE_OK' if scenario == 'wrong_marker' else 'CODEX_BRIDGE_TOOL_SEQUENCE_OK')!r}, encoding='utf-8')\n"
                        f"for command, command_exit in {events!r}:\n"
                        " print(json.dumps({'type':'item.completed','item':{'type':'command_execution',"
                        "'command':command,'status':'completed','exit_code':command_exit}}))\n"
                        f"sys.exit({process_exit})\n",
                        encoding="utf-8",
                    )
                    fake_codex.chmod(0o700)
                    proc = run_bridge(
                        "probe", "--desktop", "--shell", "--tool-sequence",
                        "--models", "grok-4.6", "--catalog", str(catalog),
                        "--codex", str(fake_codex),
                        env={"HOME": str(root), "CODEX_HOME": str(root)},
                    )
                    self.assertEqual(proc.returncode, 0 if scenario == "valid" else 2, proc.stderr or proc.stdout)
                    result = json.loads(proc.stdout)["results"]["grok-4.6"]
                    self.assertTrue(result["shell"])
                    self.assertTrue(result["tool_sequence"])
                    self.assertEqual(result["tool_sequence_executed"], scenario in {"valid", "process_failure", "wrong_marker"})
                    self.assertEqual(result["ok"], scenario == "valid")

    def test_configure_writes_isolated_profile_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            home.mkdir()
            env = {"HOME": str(home), "CODEX_HOME": str(root)}
            config = root / "config.toml"
            profile = root / "cli-proxy.config.toml"
            catalog = root / "catalog.json"
            helper = root / "private" / "read-client-key.rb"
            proxy_config = root / "cliproxy.yml"
            config.write_text(
                'model = "keep-me"\npersonality = "pragmatic"\n\n'
                '[mcp_servers.keep]\ntype = "http"\nurl = "https://example.test"\n',
                encoding="utf-8",
            )
            original_config = config.read_bytes()
            proxy_config.write_text('api-keys: ["fixture"]\n', encoding="utf-8")
            args = (
                "configure",
                "--profile-config",
                str(profile),
                "--catalog",
                str(catalog),
                "--helper",
                str(helper),
                "--proxy-config",
                str(proxy_config),
                "--apply",
            )
            first = run_bridge(*args, env=env)
            self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
            self.assertEqual(config.read_bytes(), original_config)
            text = profile.read_text(encoding="utf-8")
            self.assertIn("[model_providers.cli_proxy]", text)
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
            self.assertEqual(helper.stat().st_mode & 0o777, 0o700)
            second = run_bridge(*args, env=env)
            self.assertEqual(second.returncode, 0, second.stderr or second.stdout)
            self.assertEqual(json.loads(second.stdout)["status"], "unchanged")
            self.assertEqual(config.read_bytes(), original_config)

    def test_restore_default_follows_dominant_history_without_mutating_threads(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            native = root / "models_cache.json"
            state_db = root / "state.sqlite"
            config.write_text(
                'model = "grok-4.6"\nmodel_provider = "cli_proxy"\nmodel_catalog_json = "/tmp/custom.json"\n',
                encoding="utf-8",
            )
            native.write_text(json.dumps({"models": [native_template()]}), encoding="utf-8")
            connection = sqlite3.connect(state_db)
            connection.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, archived INTEGER)")
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, 0)",
                [("openai-1", "openai"), ("openai-2", "openai"), ("proxy-1", "cli_proxy")],
            )
            connection.commit()
            threads_before = connection.execute(
                "SELECT id, model_provider, archived FROM threads ORDER BY id"
            ).fetchall()
            connection.close()
            sha = hashlib.sha256(config.read_bytes()).hexdigest()
            proc = run_bridge(
                "restore-default",
                "--config",
                str(config),
                "--state-db",
                str(state_db),
                "--native-catalog",
                str(native),
                "--expected-sha256",
                sha,
                "--apply",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["target_provider"], "openai")
            text = config.read_text(encoding="utf-8")
            self.assertIn('model_provider = "openai"', text)
            self.assertIn('model = "gpt-5.6-sol"', text)
            self.assertNotIn("model_catalog_json", text)
            self.assertNotIn("openai_base_url", text)
            connection = sqlite3.connect(state_db)
            self.assertEqual(
                connection.execute("SELECT id, model_provider, archived FROM threads ORDER BY id").fetchall(),
                threads_before,
            )
            connection.close()

    def test_configure_desktop_preview_preserves_openai_history_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.toml"
            catalog = root / "catalog.json"
            auth = root / "auth.json"
            helper = root / "read-client-key.rb"
            state_db = root / "state.sqlite"
            runtime = root / "runtime.mjs"
            launch_agent = root / "bridge.plist"
            node = root / "node"
            config.write_text('model = "gpt-5.6-sol"\nmodel_provider = "openai"\n', encoding="utf-8")
            catalog.write_text(
                json.dumps(
                    {
                        "models": [
                            native_template(),
                            {**native_template(), "slug": "grok-4.6"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            auth.write_text(
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {
                            "id_token": "fixture",
                            "access_token": "fixture",
                            "refresh_token": "fixture",
                        },
                    }
                ),
                encoding="utf-8",
            )
            helper.write_text("fixture", encoding="utf-8")
            node.write_text("fixture", encoding="utf-8")
            connection = sqlite3.connect(state_db)
            connection.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, archived INTEGER)")
            connection.executemany(
                "INSERT INTO threads VALUES (?, ?, 0)",
                [("openai-1", "openai"), ("openai-2", "openai"), ("proxy-1", "cli_proxy")],
            )
            connection.commit()
            threads_before = connection.execute(
                "SELECT id, model_provider, archived FROM threads ORDER BY id"
            ).fetchall()
            connection.close()
            proc = run_bridge(
                "configure-desktop",
                "--config",
                str(config),
                "--state-db",
                str(state_db),
                "--catalog",
                str(catalog),
                "--auth-file",
                str(auth),
                "--helper",
                str(helper),
                "--runtime-script",
                str(runtime),
                "--launch-agent",
                str(launch_agent),
                "--transparent-url",
                "http://127.0.0.1:1/v1",
                "--node",
                str(node),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "planned")
            self.assertIn('model_provider = "openai"', payload["diff"])
            self.assertIn('openai_base_url = "http://127.0.0.1:1/v1"', payload["diff"])
            self.assertEqual(payload["service_action"], "start")
            self.assertFalse(runtime.exists())
            self.assertFalse(launch_agent.exists())
            with sqlite3.connect(state_db) as connection:
                self.assertEqual(
                    connection.execute("SELECT id, model_provider, archived FROM threads ORDER BY id").fetchall(),
                    threads_before,
                )

    def test_configure_multi_agent_is_guarded_redacted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            proxy_config = root / "cliproxyapi.conf"
            proxy_config.write_text(
                'api-keys:\n  - "fixture-secret"\ncodex:\n  identity-confuse: false\n  optimize-multi-agent-v2: false\n',
                encoding="utf-8",
            )
            base = (
                "configure-multi-agent",
                "--proxy-config",
                str(proxy_config),
            )
            preview = run_bridge(*base)
            self.assertEqual(preview.returncode, 0, preview.stderr or preview.stdout)
            payload = json.loads(preview.stdout)
            self.assertEqual(payload["status"], "planned")
            self.assertNotIn("fixture-secret", payload["diff"])
            sha = payload["config_sha256"]
            wrong = run_bridge(*base, "--expected-sha256", "0" * 64, "--apply")
            self.assertEqual(wrong.returncode, 2)
            applied = run_bridge(*base, "--expected-sha256", sha, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr or applied.stdout)
            applied_payload = json.loads(applied.stdout)
            self.assertEqual(applied_payload["status"], "applied")
            self.assertTrue(applied_payload["backup"])
            text = proxy_config.read_text(encoding="utf-8")
            self.assertIn("  optimize-multi-agent-v2: true\n", text)
            self.assertIn("fixture-secret", text)
            again = run_bridge(*base, "--apply")
            self.assertEqual(again.returncode, 0, again.stderr or again.stdout)
            self.assertEqual(json.loads(again.stdout)["status"], "unchanged")

    def test_configure_writes_python_helper_that_reads_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            profile = root / "cli-proxy.config.toml"
            catalog = root / "catalog.json"
            helper = root / "private" / "read-client-key.py"
            proxy_config = root / "cliproxy.yml"
            proxy_config.write_text('api-keys:\n  - "fixture-key-value"\n', encoding="utf-8")
            proc = run_bridge(
                "configure",
                "--profile-config",
                str(profile),
                "--catalog",
                str(catalog),
                "--helper",
                str(helper),
                "--proxy-config",
                str(proxy_config),
                "--apply",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            text = profile.read_text(encoding="utf-8")
            command = tomllib.loads(text)["model_providers"]["cli_proxy"]["auth"]["command"]
            self.assertEqual(command, load_bridge().python_executable())
            self.assertIn(str(helper), text)
            parsed = subprocess.run(
                [command, str(helper)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(parsed.returncode, 0, parsed.stderr)
            self.assertEqual(parsed.stdout, "fixture-key-value")

    def test_helper_invocation_matches_helper_extension(self) -> None:
        module = load_bridge()

        command, args = module.helper_invocation(Path("read-client-key.py"))
        self.assertEqual(command, module.python_executable())
        self.assertEqual(args, ["read-client-key.py"])
        command, args = module.helper_invocation(Path("read-client-key.rb"))
        self.assertTrue(Path(command).name.startswith("ruby") or command.endswith("ruby"))
        self.assertEqual(args, ["read-client-key.rb"])


if __name__ == "__main__":
    unittest.main()
