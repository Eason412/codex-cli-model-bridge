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

from test_bridge import SCRIPT, native_template

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
            catalog.write_text(json.dumps({"models": [
                {"slug": "default-model", "visibility": "hide"},
                {"slug": "visible-model", "visibility": "list"},
                {"slug": "managed-model", "visibility": "hide"}]}))
            config = root / "config.toml"
            config.write_text('model = "default-model"\nmodel_provider = "openai"\n'
                              'openai_base_url = "http://127.0.0.1:1/v1"\n'
                              'model_catalog_json = ' + json.dumps(str(catalog)) + '\n')
            config.chmod(0o600)
            (root / "state.json").write_text('{"managed_model_ids": ["managed-model"]}')
            proxy_config = root / "fixture-proxy.yaml"
            proxy_config.write_text('codex:\n  optimize-multi-agent-v2: true\n')
            models_file = root / "models.json"
            args = bridge.parser().parse_args(["audit", "--config", str(config), "--state-dir", str(root),
                "--profile-config", str(root / "absent-profile"), "--proxy-config", str(proxy_config),
                "--models-file", str(models_file)])
            all_ids = {"default-model", "visible-model", "managed-model"}
            for live, missing in [(set(), all_ids), (all_ids - {"default-model"}, {"default-model"}),
                                  ({"default-model"}, {"visible-model", "managed-model"}), (all_ids, set())]:
                with self.subTest(live=live), contextlib.ExitStack() as mocks:
                    models_file.write_text(json.dumps({"data": [{"id": slug} for slug in live]}))
                    mocks.enter_context(patch.object(bridge, "thread_inventory", return_value=(
                        {"openai": 1}, {"integrity": "ok", "total": 1}, None)))
                    mocks.enter_context(patch.object(bridge, "chatgpt_auth_state", return_value=(
                        {"mode": "chatgpt", "chatgpt_tokens_present": True}, None)))
                    mocks.enter_context(patch.object(bridge, "proxy_version", return_value=("7.3.18", (7, 3, 18))))
                    mocks.enter_context(patch.object(bridge.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, "codex-cli 0.160.0", "")))
                    code, result = invoke(args)
                    self.assertEqual(result["catalog"]["missing_live_routes"], sorted(missing))
                    self.assertEqual(result["provider"]["live_model_count"], len(live))
                    self.assertEqual(result["status"], "attention" if missing else "ready")
                    self.assertEqual(code, 2 if missing else 0)
                    if not missing:
                        for version in [("7.0.0", (7, 0, 0)), (None, None)]:
                            with patch.object(bridge, "proxy_version", return_value=version):
                                code, receipt = invoke(args)
                            self.assertEqual(code, 0)
                            self.assertEqual(receipt["provider"]["version"], version[0])
                            self.assertNotIn("minimum_tool_safe_version", receipt["provider"])
                    with patch.object(bridge, "live_model_ids", side_effect=OSError("fixture failure")):
                        code, failure = invoke(args)
                        self.assertEqual(code, 2)
                        self.assertEqual(failure["status"], "attention")
                        self.assertEqual(failure["provider"]["live_error"], "OSError")
                        self.assertEqual(failure["catalog"]["missing_live_routes"], [])

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

    def test_cli_state_dir_isolates_manifests_enabled_policy_and_ownership(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "home"
            default_state = home / ".config/codex-cli-model-bridge"
            (default_state / "models.d").mkdir(parents=True)
            # These would break the command if the default directory leaked in.
            (default_state / "models.d/interference.json").write_text("{}")
            (default_state / "catalog-policy.json").write_text("not json")
            (default_state / "state.json").write_text('{"managed_model_ids": ["interference"]}')
            state = root / "selected-state"
            (state / "models.d").mkdir(parents=True)
            manifest = json.loads((SCRIPT.parents[1] / "models/gpt-6-astra.json").read_text())
            manifest["slug"] = "local-target"
            (state / "models.d/local-target.json").write_text(json.dumps(manifest))
            (state / "enabled-manifests.json").write_text('{"schema_version": 1, "enabled": ["local-target"]}')
            policy = state / "catalog-policy.json"
            policy.write_text('{"schema_version": 1, "hidden_native_model_ids": ["gpt-5.6-sol"]}')
            native = root / "native.json"
            native.write_text(json.dumps({"models": [native_template()]}))
            config = root / "config.toml"
            config.write_text('model_provider = "openai"\n')
            target = root / "catalog.json"
            env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(root / "codex"),
                   "CLIPROXYAPI_CONFIG": str(root / "absent-proxy"), "PYTHONDONTWRITEBYTECODE": "1"}
            base = ["sync", "--config", str(config), "--native-catalog", str(native),
                    "--catalog", str(target), "--state-dir", str(state), "--skip-live-check", "--apply"]
            before_default = {p: p.read_bytes() for p in default_state.rglob("*") if p.is_file()}
            def run(*extra):
                process = subprocess.run([os.sys.executable, str(SCRIPT), *base, *extra],
                                         env=env, text=True, capture_output=True, check=False)
                return process.returncode, json.loads(process.stdout)
            code, result = run()
            self.assertEqual(code, 0, result)
            self.assertEqual(result["enabled_manifests"], ["local-target"])
            self.assertEqual(result["catalog_policy"], str(policy))
            self.assertEqual(result["managed_after"], ["local-target"])
            entries = {entry["slug"]: entry for entry in json.loads(target.read_text())["models"]}
            self.assertEqual(set(entries), {"gpt-5.6-sol", "local-target"})
            self.assertEqual(entries["gpt-5.6-sol"]["visibility"], "hide")
            self.assertEqual(json.loads((state / "state.json").read_text())["managed_model_ids"], ["local-target"])
            explicit = root / "explicit-policy.json"
            explicit.write_text('{"schema_version": 1, "hidden_native_model_ids": []}')
            code, result = run("--catalog-policy", str(explicit))
            self.assertEqual(code, 0, result)
            self.assertEqual(result["catalog_policy"], str(explicit))
            self.assertEqual(json.loads(target.read_text())["models"][0]["visibility"], "list")
            before_target = target.read_bytes()
            code, result = run("--catalog-policy", str(default_state / "catalog-policy.json"))
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(target.read_bytes(), before_target)
            self.assertEqual({p: p.read_bytes() for p in before_default}, before_default)

    def test_missing_template_uses_highest_priority_and_reports_only_fallbacks(self):
        manifest = json.loads((SCRIPT.parents[1] / "models/gpt-6-astra.json").read_text())
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


if __name__ == "__main__":
    unittest.main()
