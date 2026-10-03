"""Catalog generation from the live list; never reads personal state or calls real APIs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from test_bridge import SCRIPT, native_template, sample_manifest

spec = importlib.util.spec_from_file_location("bridge_live_sync", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def entry(slug: str, **fields) -> dict:
    levels = [{"effort": effort, "description": effort} for effort in ("low", "medium", "high")]
    return {**native_template(), "slug": slug, "default_reasoning_level": "low",
            "supported_reasoning_levels": levels, **fields}


class LiveSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.catalog = self.root / "catalog.json"
        self.config = self.root / "config.toml"
        self.write_config('model = "gpt-6-astra"\n')
        self.upstream = [entry("gpt-6-astra", priority=1), entry("gpt-6-sol", priority=2),
                         entry("gpt-image-2", visibility="hide", priority=9)]
        self.requests = []

    def write_config(self, extra: str = "") -> None:
        self.config.write_text(
            'model_provider = "openai"\nopenai_base_url = "http://127.0.0.1:8318/v1"\n'
            f'model_catalog_json = {json.dumps(str(self.catalog))}\n{extra}')

    def write_overrides(self, models: dict) -> None:
        (self.state / "overrides.json").write_text(json.dumps({"schema_version": 1, "models": models}))

    def fake_fetch(self, url, token, timeout=15):
        self.requests.append((url, token))
        if "client_version=" in url:
            return {"models": self.upstream}
        return {"data": [{"id": item["slug"]} for item in self.upstream] + [{"id": "kimi-k3"}]}

    def sync(self, *extra, expected=0, fetch=None):
        args = bridge.parser().parse_args(["sync", "--config", str(self.config), "--state-dir", str(self.state), *extra])
        output = io.StringIO()
        with patch.object(bridge, "fetch_json", side_effect=fetch or self.fake_fetch), patch.object(
            bridge.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "codex-cli 0.160.0\n", "")
        ), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as caught:
                bridge.cmd_sync(args)
        self.assertEqual(caught.exception.code, expected, output.getvalue())
        return json.loads(output.getvalue())

    def entries(self) -> dict:
        return {item["slug"]: item for item in json.loads(self.catalog.read_text())["models"]}

    def test_writes_live_list_to_configured_catalog_with_installed_client_version(self):
        planned = self.sync()
        self.assertEqual(planned["status"], "planned")
        self.assertFalse(self.catalog.exists())
        result = self.sync("--apply")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(list(self.entries()), ["gpt-6-astra", "gpt-6-sol", "gpt-image-2"])
        self.assertEqual(self.entries()["gpt-6-sol"], self.upstream[1])
        url, token = self.requests[-1]
        self.assertEqual(url, "http://127.0.0.1:8318/v1/models?client_version=0.160.0")
        self.assertIsNone(token, "the transparent proxy adds credentials")
        self.assertEqual(result["source"]["mode"], "desktop-transparent")

    def test_unchanged_run_neither_writes_nor_backs_up_and_change_keeps_one_previous_copy(self):
        self.sync("--apply")
        first = self.catalog.read_bytes()
        result = self.sync("--apply")
        self.assertEqual(result["status"], "unchanged")
        self.assertIsNone(result["backup"])
        self.assertFalse(self.catalog.with_name("catalog.json.previous").exists())
        self.upstream.append(entry("gpt-6.2-sol", priority=0))
        result = self.sync("--apply")
        self.assertEqual(result["changes"]["added"], ["gpt-6.2-sol"])
        self.assertEqual(Path(result["backup"]).read_bytes(), first)
        self.upstream[0] = entry("gpt-6-astra", priority=1, description="newer")
        result = self.sync("--apply")
        self.assertEqual(result["changes"]["updated"], {"gpt-6-astra": ["description"]})
        self.assertEqual(sorted(p.name for p in self.root.glob("catalog.json*")), ["catalog.json", "catalog.json.previous"])

    def test_overrides_apply_globs_in_order_then_exact_ids_and_null_removes_fields(self):
        self.write_overrides({
            "gpt-6.1-sol": {"visibility": "hide"},
            "gpt-6*": {"context_window": 500000, "supports_parallel_tool_calls": None},
            "gpt-6-sol": {"visibility": "hide", "default_reasoning_level": "medium"},
            "*": {"context_window": 400000},
        })
        result = self.sync("--apply")
        entries = self.entries()
        self.assertEqual(entries["gpt-6-astra"]["context_window"], 400000, "later glob wins")
        self.assertNotIn("supports_parallel_tool_calls", entries["gpt-6-astra"])
        self.assertEqual(entries["gpt-6-sol"]["visibility"], "hide")
        self.assertEqual(entries["gpt-6-sol"]["default_reasoning_level"], "medium")
        self.assertEqual(entries["gpt-image-2"]["context_window"], 400000)
        self.assertEqual(result["overrides_unmatched"], ["gpt-6.1-sol"])

    def test_invalid_overrides_block_without_writing(self):
        for models in [{"gpt-6-sol": {"visibility": "invisible"}},
                       {"gpt-6-sol": {"default_reasoning_level": "ultra"}},
                       {"gpt-6-sol": {"slug": "renamed"}},
                       {"gpt-6-sol": {"context_window": "500K"}}]:
            with self.subTest(models=models):
                self.write_overrides(models)
                result = self.sync("--apply", expected=2)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(self.catalog.exists())

    def test_failed_empty_or_malformed_live_list_never_writes(self):
        self.sync("--apply")
        before = self.catalog.read_bytes()
        http_error = urllib.error.HTTPError("http://127.0.0.1:8318/v1/models", 502, "bad gateway", {}, None)
        results = {}
        for name, fetch in [("http", self.raiser(http_error)), ("network", self.raiser(OSError("down"))),
                            ("empty", lambda *a, **k: {"models": []}),
                            ("wrong format", lambda *a, **k: {"data": []})]:
            with self.subTest(name=name):
                results[name] = self.sync("--apply", expected=2, fetch=fetch)
                self.assertEqual(results[name]["status"], "blocked")
                self.assertEqual(self.catalog.read_bytes(), before)
        self.assertEqual(results["http"]["error"], "live model list failed: HTTPError status=502")

    @staticmethod
    def raiser(exc):
        def fetch(*args, **kwargs):
            raise exc
        return fetch

    def test_live_list_dropping_a_configured_model_blocks(self):
        self.write_config('model = "gpt-6-astra"\n[agents]\ndefault_subagent_model = "gpt-6.1-sol"\n')
        result = self.sync("--apply", expected=2)
        self.assertEqual(result["missing_configured_models"], ["gpt-6.1-sol"])
        self.assertFalse(self.catalog.exists())

    def test_extra_manifests_fill_gaps_shadow_on_live_ids_and_need_a_route(self):
        models_d = self.state / "models.d"
        models_d.mkdir()
        (models_d / "kimi-k3.json").write_text(json.dumps({**sample_manifest(), "slug": "kimi-k3", "template_slug": "retired"}))
        (models_d / "gpt-6-sol.json").write_text(json.dumps({**sample_manifest(), "slug": "gpt-6-sol"}))
        result = self.sync("--apply")
        self.assertEqual(result["extra_models"], ["kimi-k3"])
        self.assertEqual(result["manifests_shadowed_by_live_list"], ["gpt-6-sol"])
        self.assertEqual(result["template_fallbacks"], {"kimi-k3": {"requested": "retired", "selected": "gpt-6-astra"}})
        self.assertEqual(self.entries()["gpt-6-sol"], self.upstream[1], "the live entry wins over a stale manifest")
        self.assertEqual(self.entries()["kimi-k3"]["context_window"], 1000000)
        (models_d / "glm-9.json").write_text(json.dumps({**sample_manifest(), "slug": "glm-9"}))
        result = self.sync("--apply", expected=2)
        self.assertEqual(result["missing_live_routes"], ["glm-9"])

    def test_legacy_state_files_are_reported_not_read(self):
        (self.state / "catalog-policy.json").write_text("not json")
        (self.state / "enabled-manifests.json").write_text("not json")
        result = self.sync("--apply")
        self.assertEqual(sorted(result["legacy_files_ignored"]), ["catalog-policy.json", "enabled-manifests.json"])

    def test_isolated_profile_uses_helper_token(self):
        self.config.write_text(
            'model_provider = "cli_proxy"\n'
            f'model_catalog_json = {json.dumps(str(self.catalog))}\n'
            '[model_providers.cli_proxy]\nbase_url = "http://127.0.0.1:8317/v1"\n'
            '[model_providers.cli_proxy.auth]\ncommand = "fixture-helper"\n')
        with patch.object(bridge, "token_from_provider", return_value=("fixture-token", None)):
            result = self.sync("--apply")
        self.assertEqual(result["source"]["mode"], "isolated-profile")
        self.assertEqual(self.requests[-1], ("http://127.0.0.1:8317/v1/models?client_version=0.160.0", "fixture-token"))
        self.assertNotIn("fixture-token", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
