"""Catalog and probe contracts with isolated files and observable receipts."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_bridge import SCRIPT, native_template, sample_manifest

spec = importlib.util.spec_from_file_location("catalog_contract_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def invoke(args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            args.func(args)
        except SystemExit as caught:
            return caught.code, json.loads(output.getvalue())
    raise AssertionError("command did not emit a receipt")


class CatalogContractTests(unittest.TestCase):
    def test_audit_distinguishes_empty_missing_partial_success_and_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            catalog = root / "catalog.json"
            entries = [{"slug": "default-model", "visibility": "hide"},
                       {"slug": "visible-model", "visibility": "list"}]
            catalog.write_text(json.dumps({"models": entries}))
            upstream = root / "upstream.json"
            upstream.write_text(json.dumps({"models": entries}))
            config = root / "config.toml"
            config.write_text('model = "default-model"\nmodel_provider = "openai"\n'
                              'openai_base_url = "http://127.0.0.1:1/v1"\n'
                              'model_catalog_json = ' + json.dumps(str(catalog)) + '\n')
            config.chmod(0o600)
            proxy_config = root / "fixture-proxy.yaml"
            proxy_config.write_text('codex:\n  optimize-multi-agent-v2: true\n')
            models_file = root / "models.json"
            args = bridge.parser().parse_args(["audit", "--config", str(config), "--state-dir", str(root),
                "--profile-config", str(root / "absent-profile"), "--proxy-config", str(proxy_config),
                "--models-file", str(models_file), "--upstream-file", str(upstream)])
            all_ids = {"default-model", "visible-model"}
            with contextlib.ExitStack() as mocks:
                mocks.enter_context(patch.object(bridge, "thread_inventory", return_value=(
                    {"openai": 1}, {"integrity": "ok", "total": 1}, None)))
                mocks.enter_context(patch.object(bridge, "chatgpt_auth_state", return_value=(
                    {"mode": "chatgpt", "chatgpt_tokens_present": True}, None)))
                mocks.enter_context(patch.object(bridge, "proxy_version", return_value=("7.3.18", (7, 3, 18))))
                mocks.enter_context(patch.object(bridge.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, "codex-cli 0.160.0", "")))
                for live, missing in [(set(), all_ids), (all_ids - {"default-model"}, {"default-model"}),
                                      ({"default-model"}, {"visible-model"}), (all_ids, set())]:
                    with self.subTest(live=live):
                        models_file.write_text(json.dumps({"data": [{"id": slug} for slug in live]}))
                        code, result = invoke(args)
                        self.assertEqual(result["catalog"]["missing_live_routes"], sorted(missing))
                        self.assertEqual(result["provider"]["live_model_count"], len(live))
                        self.assertTrue(result["catalog"]["in_sync_with_live_list"])
                        self.assertEqual(result["status"], "attention" if missing else "ready")
                        self.assertEqual(code, 2 if missing else 0)
                # A catalog that lags the live list is the failure this audit exists to catch.
                upstream.write_text(json.dumps({"models": [*entries, {"slug": "new-model", "visibility": "list"}]}))
                code, stale = invoke(args)
                self.assertEqual(code, 2)
                self.assertFalse(stale["catalog"]["in_sync_with_live_list"])
                self.assertIn("model catalog differs from the live list; run sync", stale["findings"])
                with patch.object(bridge, "live_model_ids", side_effect=OSError("fixture failure")):
                    code, failure = invoke(args)
                self.assertEqual(code, 2)
                self.assertEqual(failure["status"], "attention")
                self.assertEqual(failure["provider"]["live_error"], "OSError")
                self.assertEqual(failure["catalog"]["missing_live_routes"], [])
                self.assertIsNone(failure["catalog"]["in_sync_with_live_list"])

    def test_probe_requires_exact_marker_allowing_outer_whitespace(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            catalog = root / "catalog.json"
            catalog.write_text('{"models": [{"slug": "fixture-model"}]}')
            with patch.object(bridge, "DEFAULT_CODEX_HOME", root):
                args = bridge.parser().parse_args(["probe", "--desktop", "--catalog", str(catalog),
                                                  "--models", "fixture-model"])
                for answer, expected in [("CODEX_BRIDGE_OK", True), (" \nCODEX_BRIDGE_OK\n ", True),
                                         ("not CODEX_BRIDGE_OK", False), ("CODEX_BRIDGE_OK extra", False),
                                         ("extra CODEX_BRIDGE_OK", False), ("", False)]:
                    with self.subTest(answer=answer):
                        def fake(command, **kwargs):
                            Path(command[command.index("--output-last-message") + 1]).write_text(answer)
                            return subprocess.CompletedProcess(command, 0, "", "")
                        with patch.object(bridge.subprocess, "run", side_effect=fake):
                            code, result = invoke(args)
                        self.assertEqual(result["results"]["fixture-model"]["ok"], expected)
                        self.assertEqual(code, 0 if expected else 2)

    def test_removed_noop_options_are_rejected(self):
        for option in ["--brew", "--skip-restart"]:
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    bridge.parser().parse_args(["configure-multi-agent", option])
                self.assertEqual(caught.exception.code, 2)

    def test_cli_state_dir_isolates_overrides_and_extra_manifests(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            default_state = home / ".config/codex-cli-model-bridge"
            (default_state / "models.d").mkdir(parents=True)
            # These would break the command if the default directory leaked in.
            (default_state / "models.d/interference.json").write_text("{}")
            (default_state / "overrides.json").write_text("not json")
            state = root / "selected-state"
            (state / "models.d").mkdir(parents=True)
            manifest = {**sample_manifest(), "slug": "local-target"}
            (state / "models.d/local-target.json").write_text(json.dumps(manifest))
            (state / "overrides.json").write_text(json.dumps(
                {"schema_version": 1, "models": {"gpt-5.6-sol": {"visibility": "hide"}}}))
            upstream = root / "upstream.json"
            upstream.write_text(json.dumps({"models": [native_template()]}))
            models_file = root / "models.json"
            models_file.write_text(json.dumps({"data": [{"id": "local-target"}]}))
            config = root / "config.toml"
            config.write_text('model_provider = "openai"\nopenai_base_url = "http://127.0.0.1:1/v1"\n')
            target = root / "catalog.json"
            env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(root / "codex"),
                   "CLIPROXYAPI_CONFIG": str(root / "absent-proxy"), "PYTHONDONTWRITEBYTECODE": "1"}
            before_default = {p: p.read_bytes() for p in default_state.rglob("*") if p.is_file()}
            process = subprocess.run(
                [os.sys.executable, str(SCRIPT), "sync", "--config", str(config), "--catalog", str(target),
                 "--state-dir", str(state), "--upstream-file", str(upstream), "--models-file", str(models_file),
                 "--codex", str(root / "absent-codex"), "--apply"],
                env=env, text=True, capture_output=True, check=False)
            result = json.loads(process.stdout)
            self.assertEqual(process.returncode, 0, result)
            self.assertEqual(result["extra_models"], ["local-target"])
            self.assertEqual(result["overrides"], str(state / "overrides.json"))
            entries = {entry["slug"]: entry for entry in json.loads(target.read_text())["models"]}
            self.assertEqual(set(entries), {"gpt-5.6-sol", "local-target"})
            self.assertEqual(entries["gpt-5.6-sol"]["visibility"], "hide")
            self.assertEqual({p: p.read_bytes() for p in before_default}, before_default)

    def test_missing_template_uses_highest_priority_and_reports_only_fallbacks(self):
        manifest = sample_manifest()
        templates = {"native-b": {**native_template(), "slug": "native-b", "priority": 2},
                     "native-z": {**native_template(), "slug": "native-z", "priority": 0,
                                  "model_messages": {"instructions_template": "Z"}},
                     "native-a": {**native_template(), "slug": "native-a", "priority": 0,
                                  "model_messages": {"instructions_template": "A"}}}
        fallbacks = {}
        original = json.dumps(manifest, sort_keys=True)
        entry = bridge.build_entry(manifest, templates, fallbacks)
        self.assertEqual(entry["model_messages"]["instructions_template"], "A")
        self.assertEqual(fallbacks, {"gpt-6-astra": {"requested": "gpt-5.6-sol", "selected": "native-a"}})
        self.assertEqual(entry["additional_speed_tiers"], [])
        self.assertEqual(json.dumps(manifest, sort_keys=True), original)
        templates[manifest["template_slug"]] = {**native_template(), "priority": 99}
        fallbacks = {}
        entry = bridge.build_entry(manifest, templates, fallbacks)
        self.assertEqual(entry["model_messages"]["instructions_template"], "test")
        self.assertEqual(fallbacks, {})
        with self.assertRaisesRegex(ValueError, "native model cache is empty"):
            bridge.build_entry(manifest, {})

    def test_native_fallback_prefers_visible_entries_before_priority(self):
        manifest = sample_manifest()
        entries = [{**native_template(), "slug": "hidden", "visibility": "hide", "priority": -100},
                   {**native_template(), "slug": "visible", "visibility": "list", "priority": 10}]
        fallbacks = {}
        bridge.build_entry(manifest, {entry["slug"]: entry for entry in entries}, fallbacks)
        self.assertEqual(fallbacks[manifest["slug"]]["selected"], "visible")
        entries[1]["visibility"] = "hide"
        fallbacks = {}
        bridge.build_entry(manifest, {entry["slug"]: entry for entry in entries}, fallbacks)
        self.assertEqual(fallbacks[manifest["slug"]]["selected"], "hidden")


if __name__ == "__main__":
    unittest.main()
