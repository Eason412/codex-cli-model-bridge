"""Configuration identity regressions; no real credentials or upstream calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_bridge import SCRIPT

spec = importlib.util.spec_from_file_location("config_contract", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ConfigContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.toml"
        self.catalog = self.root / "catalog.json"
        self.config.write_text('model = "gpt-6-astra"\nmodel_provider = "openai"\nmodel_catalog_json = ' + json.dumps(str(self.catalog)) + '\n')
        self.catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-astra"}, {"slug": "gpt-5.6-sol"}]}))

    def invoke(self, command, expected=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            args = bridge.parser().parse_args(command)
            with self.assertRaises(SystemExit) as caught:
                args.func(args)
        self.assertEqual(caught.exception.code, expected, out.getvalue())
        return json.loads(out.getvalue())

    def test_desktop_preserves_default_unless_explicitly_requested(self):
        base = ['configure-desktop', '--config', str(self.config), '--catalog', str(self.catalog),
                '--helper', str(self.config), '--node', str(self.config)]
        with patch.object(bridge, 'thread_inventory', return_value=({'openai': 1}, {'total': 1, 'integrity': 'ok'}, None)), patch.object(
            bridge, 'chatgpt_auth_state', return_value=({'mode': 'chatgpt', 'chatgpt_tokens_present': True}, None)
        ):
            self.assertNotIn('-model =', self.invoke(base)['diff'])
            self.assertIn('+model = "gpt-5.6-sol"', self.invoke(base + ['--default-model', 'gpt-5.6-sol'])['diff'])
            self.catalog.write_text('{"models": [{"slug": "gpt-5.6-sol"}]}')
            self.assertIn('default model is absent', self.invoke(base, expected=2)['error'])

    def test_probe_rejects_alternate_config_before_subprocess(self):
        with patch.object(bridge, 'DEFAULT_CODEX_HOME', self.root / 'different-root'), patch.object(bridge.subprocess, 'run') as run:
            result = self.invoke(['probe', '--desktop', '--config', str(self.config), '--catalog', str(self.catalog), '--models', 'gpt-6-astra'], expected=2)
            run.assert_not_called()
            self.assertIn('alternate --config', result['error'])

    def test_probe_forwards_catalog_for_desktop_and_named_profile(self):
        for mode in [['--desktop'], ['--profile', 'fixture-profile']]:
            with self.subTest(mode=mode):
                before = self.config.read_bytes()
                def fake(command, **kwargs):
                    flags = [command[i+1] for i, value in enumerate(command[:-1]) if value == '--config']
                    self.assertIn('model_catalog_json=' + json.dumps(str(self.catalog.resolve())), flags)
                    if '--desktop' in mode:
                        self.assertNotIn('--profile', command)
                    else:
                        self.assertEqual(command[command.index('--profile')+1], 'fixture-profile')
                    Path(command[command.index('--output-last-message')+1]).write_text('CODEX_BRIDGE_OK')
                    return subprocess.CompletedProcess(command, 0, '', '')
                with patch.object(bridge, 'DEFAULT_CODEX_HOME', self.root), patch.object(bridge.subprocess, 'run', side_effect=fake):
                    result = self.invoke(['probe', '--catalog', str(self.catalog), '--models', 'gpt-6-astra', *mode])
                self.assertTrue(result['results']['gpt-6-astra']['ok'])
                self.assertEqual(self.config.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
