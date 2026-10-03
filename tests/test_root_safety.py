"""Root configuration behavior, with fake data and mocked service operations."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_bridge import SCRIPT, native_template

spec = importlib.util.spec_from_file_location("root_safety_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class RootSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.toml"
        self.catalog = self.root / "catalog.json"
        self.native = self.root / "native.json"
        self.state = self.root / "state.sqlite"
        self.runtime = self.root / "runtime.mjs"
        self.config.write_text('model = "gpt-5.6-sol"\nmodel_provider = "openai"\n')
        self.catalog.write_text(json.dumps({"models": [native_template()]}))
        self.native.write_text(self.catalog.read_text())
        with sqlite3.connect(self.state) as connection:
            connection.execute("CREATE TABLE threads (id TEXT, model_provider TEXT, archived INTEGER)")
            connection.execute("INSERT INTO threads VALUES ('fixture-thread', 'openai', 0)")
        self.auth_patch = patch.object(bridge, "chatgpt_auth_state", return_value=(
            {"mode": "chatgpt", "chatgpt_tokens_present": True}, None))
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)

    def base(self, command):
        result = [command, "--config", str(self.config), "--state-db", str(self.state)]
        if command == "configure-desktop":
            result += ["--catalog", str(self.catalog), "--helper", str(self.config),
                       "--node", str(self.config), "--runtime-script", str(self.runtime),
                       "--launch-agent", str(self.root / "fixture.plist")]
        else:
            result += ["--native-catalog", str(self.native)]
        return result

    def invoke(self, command, expected=0):
        output = io.StringIO()
        args = bridge.parser().parse_args(command)
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            args.func(args)
        self.assertEqual(caught.exception.code, expected, output.getvalue())
        return json.loads(output.getvalue()), output.getvalue()

    def test_preview_omits_adjacent_credentials_sections_and_deleted_line_comments(self):
        before = (
            'model = "gpt-5.6-sol"\naccess_token = "FAKE_ADJACENT_TOKEN"\n'
            'model_provider = "openai"\n'
            'model_catalog_json = "/tmp/old-catalog" # FAKE_DELETED_LINE_SECRET\n'
            '[credentials]\napi_key = "FAKE_BLOCK_KEY"\n'
            'bearer = """FAKE_MULTILINE_BEARER\nFAKE_SECOND_LINE"""\n'
            '[model_providers.private.auth]\nsecret = "FAKE_AUTH_SECRET"\n')
        for command in ["configure-desktop", "restore-default"]:
            with self.subTest(command=command):
                self.config.write_text(before)
                result, output = self.invoke(self.base(command))
                self.assertTrue(result["secrets_redacted"])
                self.assertEqual(result["diff_scope"], "managed_root_fields")
                self.assertIn("model_catalog_json", result["diff"])
                for fake in ["FAKE_ADJACENT_TOKEN", "FAKE_DELETED_LINE_SECRET", "FAKE_BLOCK_KEY",
                             "FAKE_MULTILINE_BEARER", "FAKE_SECOND_LINE", "FAKE_AUTH_SECRET"]:
                    self.assertNotIn(fake, output)
                self.assertEqual(self.config.read_text(), before)
                self.assertFalse(self.runtime.exists())

    def test_apply_rejects_missing_or_stale_sha_before_any_write_or_service_call(self):
        for command in ["configure-desktop", "restore-default"]:
            for sha in [None, "0" * 64]:
                with self.subTest(command=command, sha=sha):
                    self.config.write_text('model = "gpt-5.6-sol"\nmodel_provider = "cli_proxy"\n')
                    before = self.config.read_bytes()
                    args = self.base(command) + ["--apply"]
                    if sha:
                        args += ["--expected-sha256", sha]
                    with patch.object(bridge, "atomic_write") as writer, patch.object(
                        bridge, "backup") as backup, patch.object(bridge, "start_transparent_proxy") as launch:
                        result, _ = self.invoke(args, expected=2)
                        writer.assert_not_called()
                        backup.assert_not_called()
                        launch.assert_not_called()
                    self.assertEqual(result["status"], "blocked")
                    self.assertEqual(self.config.read_bytes(), before)

    def test_approved_restore_preserves_credentials_and_thread_snapshot(self):
        self.config.write_text('model = "custom"\nmodel_provider = "cli_proxy"\napi_key = "FAKE_PRESERVED"\n')
        with sqlite3.connect(self.state) as connection:
            before = connection.execute("SELECT * FROM threads ORDER BY id").fetchall()
        sha = hashlib.sha256(self.config.read_bytes()).hexdigest()
        result, output = self.invoke(self.base("restore-default") + ["--apply", "--expected-sha256", sha])
        self.assertEqual(result["status"], "applied")
        self.assertIn('api_key = "FAKE_PRESERVED"', self.config.read_text())
        self.assertNotIn("FAKE_PRESERVED", output)
        self.assertTrue(Path(result["backup"]).exists())
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute("SELECT * FROM threads ORDER BY id").fetchall(), before)

    def configured_desktop(self):
        self.config.write_text('model = "gpt-5.6-sol"\nmodel_provider = "openai"\n'
                               'openai_base_url = "http://127.0.0.1:8318/v1"\n'
                               'model_catalog_json = ' + json.dumps(str(self.catalog)) + '\n')
        self.runtime.write_text((SCRIPT.parent / "transparent_proxy.mjs").read_text())

    def test_restore_selects_native_priority_without_changing_valid_selection(self):
        entries = [{**native_template(), "priority": 50},
                   {**native_template(), "slug": "native-top", "priority": 0},
                   {**native_template(), "slug": "native-other", "priority": 2}]
        self.native.write_text(json.dumps({"models": entries}))
        self.config.write_text('model = "custom"\nmodel_provider = "cli_proxy"\n')
        result, _ = self.invoke(self.base("restore-default"))
        self.assertEqual(result["target_model"], "native-top")
        self.assertIn('+model = "native-top"', result["diff"])
        result, _ = self.invoke(self.base("restore-default") + ["--model", "native-other"])
        self.assertEqual(result["target_model"], "native-other")
        self.native.write_text('{"models": []}')
        before = self.config.read_bytes()
        result, _ = self.invoke(self.base("restore-default"), expected=2)
        self.assertIn("empty", result["error"])
        self.assertEqual(self.config.read_bytes(), before)

    def test_healthy_unchanged_desktop_does_not_write_or_start(self):
        self.configured_desktop()
        with patch.object(bridge, "wait_for_transparent_proxy", return_value=True), patch.object(
            bridge, "atomic_write") as writer, patch.object(bridge, "backup") as backup, patch.object(
            bridge, "start_transparent_proxy") as launch:
            result, _ = self.invoke(self.base("configure-desktop") + ["--apply"])
            self.assertEqual(result["status"], "unchanged")
            self.assertFalse(result["runtime_changes"]["changed"])
            self.assertFalse(result["service_started"])
            writer.assert_not_called()
            backup.assert_not_called()
            launch.assert_not_called()

    def test_changed_runtime_requires_sha_then_deploys_once(self):
        self.configured_desktop()
        self.runtime.write_text("// old fixture runtime\n")
        before_config = self.config.read_bytes()
        preview, _ = self.invoke(self.base("configure-desktop"))
        self.assertTrue(preview["runtime_changes"]["changed"])
        self.assertNotEqual(preview["runtime_changes"]["before_sha256"], preview["runtime_changes"]["after_sha256"])
        with patch.object(bridge, "start_transparent_proxy") as launch:
            self.invoke(self.base("configure-desktop") + ["--apply"], expected=2)
            launch.assert_not_called()
        with patch.object(bridge, "wait_for_transparent_proxy", return_value=True), patch.object(
            bridge, "start_transparent_proxy", return_value=None) as launch:
            result, _ = self.invoke(self.base("configure-desktop") + [
                "--apply", "--expected-sha256", preview["config_sha256"]])
            self.assertEqual(result["status"], "applied")
            launch.assert_called_once()
        self.assertEqual(self.runtime.read_bytes(), (SCRIPT.parent / "transparent_proxy.mjs").read_bytes())
        self.assertEqual(self.config.read_bytes(), before_config)

    def test_approved_config_change_keeps_healthy_runtime_and_credentials(self):
        self.configured_desktop()
        self.config.write_text(self.config.read_text().replace(
            'model_provider = "openai"', 'model_provider = "cli_proxy"') + 'access_token = "FAKE_KEEP"\n')
        sha = hashlib.sha256(self.config.read_bytes()).hexdigest()
        with patch.object(bridge, "wait_for_transparent_proxy", return_value=True), patch.object(
            bridge, "start_transparent_proxy") as launch:
            result, output = self.invoke(self.base("configure-desktop") + [
                "--apply", "--expected-sha256", sha])
            self.assertEqual(result["status"], "applied")
            self.assertFalse(result["runtime_changes"]["changed"])
            launch.assert_not_called()
        self.assertIn('model_provider = "openai"', self.config.read_text())
        self.assertIn('access_token = "FAKE_KEEP"', self.config.read_text())
        self.assertNotIn("FAKE_KEEP", output)

    def test_unchanged_runtime_unhealthy_service_starts_without_rewriting_runtime(self):
        self.configured_desktop()
        with patch.object(bridge, "wait_for_transparent_proxy", side_effect=[False, True]), patch.object(
            bridge, "atomic_write") as writer, patch.object(bridge, "start_transparent_proxy", return_value=None) as launch:
            result, _ = self.invoke(self.base("configure-desktop") + ["--apply"])
            self.assertEqual(result["status"], "applied")
            writer.assert_not_called()
            launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
