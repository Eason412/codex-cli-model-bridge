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
        self.codex_error = None
        self.current_error = None
        self.checked = []

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

    def fake_codex_check(self, codex, path, slugs):
        if Path(path) == self.catalog:
            return self.current_error
        self.checked.append([entry["slug"] for entry in json.loads(Path(path).read_text())["models"]])
        return self.codex_error

    def sync(self, *extra, expected=0, fetch=None, cwd=None):
        args = bridge.parser().parse_args(["sync", "--config", str(self.config), "--state-dir", str(self.state), *extra])
        output = io.StringIO()
        with patch.object(bridge, "fetch_json", side_effect=fetch or self.fake_fetch), patch.object(
            bridge, "codex_client_version", return_value="0.160.0"
        ), patch.object(bridge, "codex_rejects_catalog", side_effect=self.fake_codex_check), \
                contextlib.chdir(cwd or self.root), contextlib.redirect_stdout(output):
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

    def test_codex_rejection_keeps_the_current_catalog_and_its_backup(self):
        self.sync("--apply")
        self.upstream.append(entry("gpt-6.2-sol", priority=0))
        self.sync("--apply")
        current, previous = self.catalog.read_bytes(), self.catalog.with_name("catalog.json.previous").read_bytes()
        self.upstream.append(entry("broken", priority=5))
        self.codex_error = "missing field `display_name` at line 1 column 23"
        result = self.sync("--apply", expected=2)
        self.assertEqual(result["codex_error"], self.codex_error)
        self.assertEqual(self.catalog.read_bytes(), current)
        self.assertEqual(self.catalog.with_name("catalog.json.previous").read_bytes(), previous)
        self.assertEqual(sorted(p.name for p in self.root.glob("*.candidate")), [])
        self.assertIn("broken", self.checked[-1], "Codex saw the candidate, not the old catalog")

    def test_codex_check_reads_codex_debug_models(self):
        catalog = self.root / "check.json"
        catalog.write_text(json.dumps({"models": [entry("a"), entry("b")]}))
        cases = [
            (subprocess.CompletedProcess([], 1, "", "Error: failed to parse: missing field `display_name`\n"),
             "Error: failed to parse: missing field `display_name`"),
            (subprocess.CompletedProcess([], 0, json.dumps({"models": [{"slug": "a"}]}), ""),
             "Codex loaded a different model list than the catalog"),
            (subprocess.CompletedProcess([], 0, "not json", ""), "codex debug models returned unreadable output"),
            (subprocess.CompletedProcess([], 0, json.dumps({"models": [{"slug": "b"}, {"slug": "a"}]}), ""), None),
        ]
        for completed, expected in cases:
            with self.subTest(expected=expected), patch.object(bridge.subprocess, "run", return_value=completed) as run:
                self.assertEqual(bridge.codex_rejects_catalog("codex", catalog, ["a", "b"]), expected)
                command = run.call_args.args[0]
                self.assertEqual(command[:3], ["codex", "debug", "models"])
                self.assertEqual(command[-1], "model_catalog_json=" + json.dumps(str(catalog)))

    def test_only_this_bridges_route_is_synced(self):
        for config, error in [
            ('model_provider = "openai"\nopenai_base_url = "http://127.0.0.1:4202/v1"\n', "another gateway"),
            ('model_provider = "other"\n[model_providers.other]\nbase_url = "http://127.0.0.1:2/v1"\n'
             '[model_providers.cli_proxy]\nbase_url = "http://127.0.0.1:1/v1"\n', "neither the transparent proxy"),
        ]:
            with self.subTest(error=error):
                self.config.write_text(config)
                result = self.sync("--catalog", str(self.catalog), "--apply", expected=2)
                self.assertIn(error, result["error"])
                self.assertEqual(self.requests, [])
                self.assertFalse(self.catalog.exists())
        self.write_config()
        self.config.write_text(self.config.read_text().replace("8318", "9318"))
        self.assertEqual(self.sync("--transparent-url", "http://127.0.0.1:9318/v1", "--apply")["status"], "applied")

    def test_relative_catalog_is_resolved_next_to_the_config(self):
        self.config.write_text('model_provider = "openai"\nopenai_base_url = "http://127.0.0.1:8318/v1"\n'
                               'model_catalog_json = "relative.json"\n')
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        result = self.sync("--apply", cwd=elsewhere)
        self.assertEqual(result["catalog"], str(self.root / "relative.json"))
        self.assertTrue((self.root / "relative.json").exists())
        self.assertFalse((elsewhere / "relative.json").exists())

    def test_profile_and_agent_role_models_are_never_removed(self):
        (self.root / "worker.toml").write_text('model = "gpt-6-sol"\n')
        self.write_config('model = "gpt-6-astra"\n[profiles.quick]\nmodel = "gpt-image-2"\n'
                          '[agents.worker]\ndescription = "w"\nconfig_file = "worker.toml"\n')
        self.sync("--apply")
        for retired in ["gpt-6-sol", "gpt-image-2"]:
            with self.subTest(retired=retired):
                kept = [item for item in self.upstream]
                self.upstream[:] = [item for item in kept if item["slug"] != retired]
                result = self.sync("--apply", expected=2)
                self.assertEqual(result["missing_configured_models"], [retired])
                self.assertIn(retired, self.entries())
                self.upstream[:] = kept
        (self.root / "worker.toml").write_text("model = [")
        self.assertIn("config_file is unreadable", self.sync("--apply", expected=2)["error"])

    def test_corrupt_catalog_is_rebuilt_without_replacing_the_good_backup(self):
        previous = self.catalog.with_name("catalog.json.previous")
        previous.write_text('{"models": []}')
        self.catalog.write_text("{broken")
        result = self.sync("--apply")
        self.assertEqual(result["status"], "applied")
        self.assertIn("JSONDecodeError", result["existing_catalog_invalid"])
        self.assertIsNone(result["backup"])
        self.assertEqual(previous.read_text(), '{"models": []}')
        self.assertEqual(list(self.entries()), ["gpt-6-astra", "gpt-6-sol", "gpt-image-2"])

    def test_duplicate_extra_slugs_block_and_shadowed_manifests_are_not_validated(self):
        models_d = self.state / "models.d"
        models_d.mkdir()
        (models_d / "a.json").write_text(json.dumps({**sample_manifest(), "slug": "kimi-k3"}))
        (models_d / "old.json").write_text(json.dumps({"slug": "gpt-6-sol", "supersedes": ["gpt-5"]}))
        result = self.sync("--apply")
        self.assertEqual(result["manifests_shadowed_by_live_list"], ["gpt-6-sol"])
        (models_d / "b.json").write_text(json.dumps({**sample_manifest(), "slug": "kimi-k3"}))
        before = self.catalog.read_bytes()
        result = self.sync("--apply", expected=2)
        self.assertIn("also declared in", json.dumps(result["manifest_errors"]))
        self.assertEqual(self.catalog.read_bytes(), before)
        (models_d / "b.json").write_text(json.dumps({**sample_manifest(), "slug": "x", "input_modalities": [["text"]]}))
        self.assertIn("input_modalities", json.dumps(self.sync("--apply", expected=2)["manifest_errors"]))

    def test_catalog_codex_rejects_is_replaced_but_never_becomes_the_backup(self):
        previous = self.catalog.with_name("catalog.json.previous")
        previous.write_text('{"models": []}')
        self.catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-astra"}]}))
        self.current_error = "missing field `display_name`"
        result = self.sync("--apply")
        self.assertEqual(result["status"], "applied")
        self.assertIn("Codex cannot load it", result["existing_catalog_invalid"])
        self.assertIsNone(result["backup"])
        self.assertEqual(previous.read_text(), '{"models": []}')

    def test_unreadable_catalog_still_protects_role_models_using_the_backup(self):
        (self.root / "worker.toml").write_text('model = "gpt-image-2"\n')
        self.write_config('model = "gpt-6-astra"\n[agents.worker]\ndescription = "w"\nconfig_file = "worker.toml"\n')
        self.upstream = [item for item in self.upstream if item["slug"] != "gpt-image-2"]
        previous = self.catalog.with_name("catalog.json.previous")
        self.catalog.write_text("{broken")
        for backup, expected in [(None, 2), ({"models": [{"slug": "gpt-image-2"}]}, 2), ({"models": []}, 0)]:
            with self.subTest(backup=backup):
                if backup is None:
                    previous.unlink(missing_ok=True)
                else:
                    previous.write_text(json.dumps(backup))
                result = self.sync(expected=expected)
                if expected:
                    self.assertEqual(result["missing_configured_models"], ["gpt-image-2"])
        (self.root / "worker.toml").write_text('model = ["gpt-image-2"]\n')
        self.assertEqual(self.sync(expected=2)["error"], "a configured model name is not a string")

    def test_reasoning_check_only_applies_to_a_set_default_with_known_levels(self):
        self.upstream.append(entry("noreason", priority=8, supported_reasoning_levels=[], default_reasoning_level=None))
        self.write_overrides({"*": {"default_reasoning_level": "medium"}, "noreason": {"default_reasoning_level": None}})
        self.sync("--apply")
        self.assertNotIn("default_reasoning_level", self.entries()["noreason"])
        self.assertEqual(self.entries()["gpt-6-sol"]["default_reasoning_level"], "medium")

    def test_codex_errors_and_urls_never_echo_values(self):
        failed = subprocess.CompletedProcess([], 1, "", 'Error: invalid type: string "fixture-secret", expected a boolean at line 1 column 5\n')
        with patch.object(bridge.subprocess, "run", return_value=failed):
            message = bridge.codex_rejects_catalog("codex", self.catalog, [])
        self.assertNotIn("fixture-secret", message)
        self.assertIn("line 1 column 5", message)
        self.assertEqual(bridge.redact_url("http://127.0.0.1:abc/v1"), "<invalid URL>")
        self.assertTrue(bridge.same_local_endpoint("http://localhost:8318/v1/", "http://127.0.0.1:8318/v1"))
        self.assertFalse(bridge.same_local_endpoint("http://127.0.0.1:4202/v1", "http://127.0.0.1:8318/v1"))
        self.config.write_text(self.config.read_text().replace("127.0.0.1:8318", "localhost:8318"))
        self.assertEqual(self.sync("--apply")["status"], "applied")

    def test_priority_zero_bracket_ids_and_output_order(self):
        self.upstream += [entry("foo1", priority=7), entry("foom", priority=7)]
        self.upstream.append(entry("foo[1m]", priority=7))
        self.write_overrides({"gpt-image-2": {"priority": 0}, "foo[1m]": {"visibility": "hide"}})
        result = self.sync("--apply")
        self.assertEqual(result["overrides_unmatched"], [])
        self.assertEqual(self.entries()["foo[1m]"]["visibility"], "hide")
        self.assertEqual([self.entries()[slug]["visibility"] for slug in ("foo1", "foom")], ["list", "list"])
        self.assertEqual(list(self.entries()), ["gpt-image-2", "gpt-6-astra", "gpt-6-sol", "foo1", "foo[1m]", "foom"])

    def test_receipt_drops_credentials_embedded_in_the_proxy_url(self):
        url = "http://user:fixture-secret@127.0.0.1:8318/v1?token=fixture-secret"
        self.config.write_text(f'model_provider = "openai"\nopenai_base_url = "{url}"\n'
                               f'model_catalog_json = {json.dumps(str(self.catalog))}\nmodel = "gpt-6-astra"\n')
        result = self.sync("--transparent-url", url, "--apply")
        self.assertEqual(result["source"]["base_url"], "http://127.0.0.1:8318/v1")
        self.assertNotIn("fixture-secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
