"""Fast catalog metadata and one-shot Codex probe settings."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge_fast_test", ROOT / "scripts" / "bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FastTests(unittest.TestCase):
    def manifest(self):
        return json.loads((ROOT / "models" / "gpt-6-astra.json").read_text(encoding="utf-8"))

    def native_models(self):
        return {
            "gpt-5.6-sol": {"slug": "gpt-5.6-sol", "additional_speed_tiers": ["fast"],
                            "service_tiers": [{"id": "priority", "name": "Fast", "description": "Sol metadata"}]},
            "gpt-6-astra": {"slug": "gpt-6-astra", "additional_speed_tiers": ["fast"],
                            "service_tiers": [{"id": "priority", "name": "Fast", "description": "Astra metadata"}]},
        }

    def test_preserves_speed_tiers_from_exact_native_model(self):
        native = self.native_models()
        original = copy.deepcopy(native)
        entry = bridge.build_entry(self.manifest(), native)
        self.assertEqual(entry["additional_speed_tiers"], ["fast"])
        self.assertEqual(entry["service_tiers"], native["gpt-6-astra"]["service_tiers"])
        entry["service_tiers"][0]["name"] = "changed"
        self.assertEqual(native, original)

    def test_requested_gpt_family_members_keep_their_native_fast_support(self):
        native = self.native_models()
        for model in ("gpt-5.6-luna", "gpt-5.6-terra"):
            native[model] = {"slug": model, "additional_speed_tiers": ["fast"],
                             "service_tiers": [{"id": "priority", "name": "Fast", "description": model}]}
        for model in ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra"):
            with self.subTest(model=model):
                entry = bridge.build_entry({**self.manifest(), "slug": model}, native)
                self.assertEqual(entry["slug"], model)
                self.assertEqual(entry["additional_speed_tiers"], ["fast"])
                self.assertEqual(entry["service_tiers"], native[model]["service_tiers"])

    def test_template_fast_does_not_leak_to_missing_or_third_party_models(self):
        native = self.native_models()
        native.pop("gpt-6-astra")
        for slug in ["gpt-6-astra", "kimi-k3"]:
            manifest = {**self.manifest(), "slug": slug}
            entry = bridge.build_entry(manifest, native)
            self.assertEqual(entry["additional_speed_tiers"], [])
            self.assertEqual(entry["service_tiers"], [])

    def test_explicit_speed_override_is_respected(self):
        manifest = {**self.manifest(), "additional_speed_tiers": [], "service_tiers": []}
        entry = bridge.build_entry(manifest, self.native_models())
        self.assertEqual(entry["additional_speed_tiers"], [])
        self.assertEqual(entry["service_tiers"], [])

    def test_fast_probe_enables_feature_without_changing_config(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            catalog = root / "catalog.json"
            config = root / "config.toml"
            catalog.write_text(json.dumps({"models": list(self.native_models().values())}), encoding="utf-8")
            config.write_text('model_provider = "openai"\n[features]\nfast_mode = false\n', encoding="utf-8")
            before = config.read_bytes()
            args = bridge.parser().parse_args(["probe", "--desktop", "--fast", "--models", "gpt-6-astra",
                                              "--catalog", str(catalog), "--config", str(config)])

            def fake_codex(command, **kwargs):
                flags = [command[i + 1] for i, value in enumerate(command[:-1]) if value == "--config"]
                self.assertIn('service_tier="fast"', flags)
                self.assertIn("features.fast_mode=true", flags)
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-astra")
                Path(command[command.index("--output-last-message") + 1]).write_text("CODEX_BRIDGE_OK", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            output = io.StringIO()
            with patch.object(bridge, "DEFAULT_CODEX_HOME", root), patch.object(bridge.subprocess, "run", side_effect=fake_codex), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as result:
                    bridge.cmd_probe(args)
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(config.read_bytes(), before)
            self.assertIsNone(json.loads(output.getvalue())["results"]["gpt-6-astra"]["served_service_tier"])

    def test_fast_probe_rejects_unadvertised_models_without_calling_codex(self):
        with tempfile.TemporaryDirectory() as raw:
            catalog = Path(raw) / "catalog.json"
            catalog.write_text(json.dumps({"models": [{"slug": "kimi-k3", "additional_speed_tiers": []}]}), encoding="utf-8")
            args = bridge.parser().parse_args(["probe", "--fast", "--models", "kimi-k3", "--catalog", str(catalog)])
            with patch.object(bridge.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    bridge.cmd_probe(args)
            self.assertEqual(result.exception.code, 2)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
