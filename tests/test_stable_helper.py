"""Credential helper configuration must survive removal of uv environments."""
import contextlib
import io
from pathlib import Path
import plistlib
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from test_bridge import load_bridge


class StableHelperTests(unittest.TestCase):
    def test_profile_and_launch_agent_never_persist_uv_sys_executable(self):
        bridge = load_bridge("stable_helper")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            stable = root / "host/bin/python3"
            stable.parent.mkdir(parents=True)
            stable.touch()
            ephemeral = str(root / "cache/uv/environments-v2/fixture/bin/python")
            profile = root / "profile.toml"
            helper = root / "helper.py"
            args = bridge.parser().parse_args(["configure", "--profile-config", str(profile),
                "--helper", str(helper), "--proxy-config", str(root / "fake-proxy.yaml"), "--apply"])
            with patch.object(bridge.sys, "executable", ephemeral), patch.object(
                bridge.shutil, "which", return_value=str(stable)), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    bridge.cmd_configure(args)
                self.assertEqual(caught.exception.code, 0)
                plist = bridge.launch_agent_source(root / "node", root / "runtime.mjs", helper,
                    "http://127.0.0.1:1/v1", "http://127.0.0.1:2/v1")
            command = tomllib.loads(profile.read_text())["model_providers"]["cli_proxy"]["auth"]["command"]
            self.assertEqual(command, str(stable.absolute()))
            self.assertEqual(plistlib.loads(plist.encode())["EnvironmentVariables"]["CODEX_BRIDGE_HELPER_CMD"], command)
            self.assertNotIn(ephemeral, profile.read_text())
            self.assertNotIn(ephemeral, plist)

    def test_uv_and_virtual_environment_candidates_are_rejected_even_through_symlinks(self):
        bridge = load_bridge("stable_helper_rejection")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            stable = root / "host/bin/python3"
            stable.parent.mkdir(parents=True)
            stable.touch()
            custom = root / "arbitrary-env"
            (custom / "bin").mkdir(parents=True)
            (custom / "pyvenv.cfg").touch()
            (custom / "bin/python3").symlink_to(stable)
            candidates = [root / "cache/uv/environments-v2/fixture/bin/python3",
                          root / "cache/uv/archive-v0/fixture/bin/python3",
                          root / ".local/share/uv/python/cpython-3.11.15-macos-aarch64-none/bin/python3",
                          root / ".venv/bin/python3", custom / "bin/python3"]
            for candidate in candidates:
                with self.subTest(candidate=candidate), patch.object(
                    bridge.shutil, "which", return_value=str(candidate)):
                    command, arguments = bridge.helper_invocation(root / "helper.py")
                    self.assertIn(command, {"/usr/bin/python3", "python3"})
                    self.assertNotEqual(command, str(candidate))
                    self.assertEqual(arguments, [str(root / "helper.py")])

    def test_host_symlink_entry_is_preserved_and_absent_host_uses_fallback(self):
        bridge = load_bridge("stable_helper_fallback")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "host/python"
            target.parent.mkdir()
            target.touch()
            link = root / "python3"
            link.symlink_to(target)
            with patch.object(bridge.shutil, "which", return_value=str(link)):
                self.assertEqual(bridge.python_executable(), str(link.absolute()))
            with patch.object(bridge.shutil, "which", return_value=None), patch.object(Path, "exists", return_value=True):
                self.assertEqual(bridge.python_executable(), "/usr/bin/python3")
            with patch.object(bridge.shutil, "which", return_value=None), patch.object(Path, "exists", return_value=False):
                self.assertEqual(bridge.python_executable(), "python3")


if __name__ == "__main__":
    unittest.main()
