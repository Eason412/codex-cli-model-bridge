"""Isolated regressions for catalog scope; never reads personal manifests or APIs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_bridge import SCRIPT, native_template

spec = importlib.util.spec_from_file_location("bridge_sync", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class SyncScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "catalog.json"
        self.native = self.root / "native.json"
        self.config = self.root / "config.toml"
        self.policy = self.root / "policy.json"
        self.state = self.root / "state.json"
        self.enabled = None
        self.config.write_text('model_provider = "openai"\n')
        self.policy.write_text(json.dumps({"schema_version": 1, "hidden_native_model_ids": ["gpt-5.4"]}))
        self.astra = {**native_template(), "slug": "gpt-6-astra", "context_window": 1000000,
                      "max_context_window": 1000000, "priority": 0}
        self.old = {**native_template(), "slug": "gpt-5.4", "visibility": "hide"}
        self.manual = {**native_template(), "slug": "manual", "priority": 99}
        self.write_catalog(self.target, [self.astra, self.old, self.manual])
        self.write_catalog(self.native, [native_template(), {**native_template(), "slug": "gpt-6-astra"},
                                         {**self.old, "visibility": "list"}])
        self.state.write_text(json.dumps({"managed_model_ids": ["gpt-6-astra"]}))
        self.manifests = {}
        self.skill = self.root / "skill"
        (self.skill / "models").mkdir(parents=True)
        for slug in ["gpt-6-astra", "deepseek-v4.1-flash", "claude-opus-4-6-thinking"]:
            manifest = json.loads((SCRIPT.parents[1] / "models/gpt-6-astra.json").read_text())
            manifest.update(slug=slug, display_name=slug)
            path = self.skill / "models" / (slug + ".json")
            path.write_text(json.dumps(manifest))
            self.manifests[slug] = path

    def write_catalog(self, path, entries):
        path.write_text(json.dumps({"models": entries}))

    def entries(self):
        return {m["slug"]: m for m in json.loads(self.target.read_text())["models"]}

    def sync(self, *extra, expected=0):
        enabled_path = self.root / "enabled-manifests.json"
        if self.enabled is None:
            if enabled_path.exists():
                enabled_path.unlink()
        else:
            enabled_path.write_text(self.enabled, encoding="utf-8")
        args = bridge.parser().parse_args([
            "sync", "--config", str(self.config), "--native-catalog", str(self.native),
            "--catalog", str(self.target), "--catalog-policy", str(self.policy),
            "--state-dir", str(self.root), "--enabled-manifests", str(enabled_path),
            "--skip-live-check", "--apply", *extra])
        output = io.StringIO()
        with patch.object(bridge, "SKILL_DIR", self.skill), patch.object(
            bridge, "DEFAULT_STATE_DIR", self.root
        ), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as caught:
                bridge.cmd_sync(args)
        self.assertEqual(caught.exception.code, expected, output.getvalue())
        return json.loads(output.getvalue())

    def test_deepseek_then_opus_preserve_all_unselected_entries_and_ownership(self):
        for slug in ["deepseek-v4.1-flash", "claude-opus-4-6-thinking"]:
            before = self.entries()
            result = self.sync("--models", slug)
            after = self.entries()
            self.assertEqual({k: after[k] for k in before}, before)
            self.assertEqual(result["changes"]["added"], [slug])
            self.assertEqual(result["changes"]["updated"], [])
            self.assertIn("gpt-6-astra", result["managed_after"])
            snapshot = self.target.read_bytes()
            self.assertEqual(self.sync("--models", slug)["status"], "unchanged")
            self.assertEqual(self.target.read_bytes(), snapshot)

    def test_subset_preserves_native_override_even_without_ownership_record(self):
        self.state.write_text('{"managed_model_ids": []}')
        self.sync("--models", "deepseek-v4.1-flash")
        self.assertEqual(self.entries()["gpt-6-astra"], self.astra)
        self.assertNotIn("gpt-5.6-sol", self.entries())

    def test_full_sync_refreshes_unmanaged_native_and_reports_policy_fields(self):
        self.write_catalog(self.target, [self.astra, {**self.old, "visibility": "list"},
                                        self.manual, {**native_template(), "context_window": 123}])
        result = self.sync()
        entries = self.entries()
        self.assertEqual(entries["gpt-6-astra"]["context_window"], 1000000)
        self.assertEqual(entries["gpt-5.6-sol"]["context_window"], 272000)
        self.assertEqual(entries["manual"], self.manual)
        self.assertEqual(entries["gpt-5.4"]["visibility"], "hide")
        self.assertEqual(result["field_changes"]["gpt-5.4"]["visibility"]["after"], "hide")
        self.assertIn("gpt-5.6-sol", result["changes"]["updated"])
        self.assertEqual(self.sync()["status"], "unchanged")

    def test_missing_managed_manifest_preserved_until_explicit_full_prune(self):
        self.manifests.pop("gpt-6-astra").unlink()
        self.sync()
        self.assertEqual(self.entries()["gpt-6-astra"], self.astra)
        entries = list(self.entries().values()) + [{**self.manual, "slug": "stale"}]
        self.write_catalog(self.target, entries)
        state = json.loads(self.state.read_text())
        state["managed_model_ids"].append("stale")
        self.state.write_text(json.dumps(state))
        result = self.sync("--prune-managed")
        self.assertEqual(self.entries()["gpt-6-astra"]["context_window"], 272000)
        self.assertNotIn("gpt-6-astra", result["managed_after"])
        self.assertEqual(result["changes"]["removed"], ["stale"])
        self.assertIn("gpt-6-astra", result["changes"]["updated"])
        self.assertEqual(self.entries()["manual"], self.manual)

    def test_subset_prune_rejected_without_writes(self):
        before = {p: p.read_bytes() for p in [self.target, self.state]}
        self.sync("--models", "deepseek-v4.1-flash", "--prune-managed", expected=2)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_explicit_supersedes_removes_alias_and_reports_it(self):
        path = self.manifests["deepseek-v4.1-flash"]
        manifest = json.loads(path.read_text())
        manifest["supersedes"] = ["manual"]
        path.write_text(json.dumps(manifest))
        result = self.sync("--models", "deepseek-v4.1-flash")
        self.assertEqual(result["changes"]["removed"], ["manual"])
        self.assertNotIn("manual", self.entries())
        self.assertEqual(self.entries()["gpt-6-astra"], self.astra)

    def test_enabled_manifests_limit_bundled_scope(self):
        """使用真实清单解析验证全量同步范围。"""
        self.enabled = json.dumps({"schema_version": 1, "enabled": ["gpt-6-astra"]})
        result = self.sync()
        self.assertEqual(result["enabled_manifests"], ["gpt-6-astra"])
        self.assertEqual(result["sync_scope"], "full")
        self.assertNotIn("deepseek-v4.1-flash", self.entries())
        self.assertNotIn("claude-opus-4-6-thinking", self.entries())
        # 该条目由清单重建：必须保留受管覆盖值，而不是退回原生缓存窗口
        self.assertEqual(self.entries()["gpt-6-astra"]["context_window"], 1000000)
        self.assertEqual(self.entries()["gpt-6-astra"]["priority"], 0)

    def test_enabled_manifests_absent_keeps_previous_behavior(self):
        """文件不存在时仍同步全部内置清单，不影响未使用该功能的机器。"""
        self.enabled = None
        result = self.sync()
        self.assertIsNone(result["enabled_manifests"])

    def test_enabled_manifests_rejects_unknown_without_writing(self):
        before = {p: p.read_bytes() for p in [self.target, self.state]}
        self.enabled = json.dumps({"schema_version": 1, "enabled": ["gpt-6-astra", "no-such-model"]})
        result = self.sync(expected=2)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("no-such-model", result["error"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_enabled_manifests_rejects_duplicates_and_malformed(self):
        for payload, marker in [
            (json.dumps({"schema_version": 1, "enabled": ["gpt-6-astra", "gpt-6-astra"]}), "duplicates"),
            ('{"schema_version": 1, "enabled": "gpt-6-astra"}', "array"),
            ('{"schema_version": 2, "enabled": []}', "schema_version"),
            ("not json at all", "invalid"),
        ]:
            with self.subTest(marker=marker):
                self.enabled = payload
                result = self.sync(expected=2)
                self.assertEqual(result["status"], "blocked")

    def test_models_flag_overrides_enabled_manifests(self):
        """临时接入必须能用 --models 越过启用清单，无需先改文件。"""
        self.enabled = json.dumps({"schema_version": 1, "enabled": ["gpt-6-astra"]})
        before = self.entries()
        result = self.sync("--models", "deepseek-v4.1-flash")
        self.assertEqual(result["changes"]["added"], ["deepseek-v4.1-flash"])
        self.assertEqual({k: self.entries()[k] for k in before}, before)

    def test_explicit_models_does_not_read_invalid_full_sync_preferences(self):
        self.enabled = "not json"
        result = self.sync("--models", "deepseek-v4.1-flash")
        self.assertEqual(result["changes"]["added"], ["deepseek-v4.1-flash"])
        self.assertIsNone(result["enabled_manifests"])

    def test_manifest_paths_scope_is_limited_by_enabled_ids(self):
        """纯函数级验证：启用清单只裁剪内置清单，个人清单始终参与。"""
        skill = self.root / "path-fixture"
        (skill / "models").mkdir(parents=True)
        state = self.root / "state-dir"
        (state / "models.d").mkdir(parents=True)
        for name in ["gpt-6-astra", "gpt-5.6-sol"]:
            (skill / "models" / f"{name}.json").write_text("{}", encoding="utf-8")
        (state / "models.d" / "glm-5.3-flash.json").write_text("{}", encoding="utf-8")
        with patch.object(bridge, "SKILL_DIR", skill), patch.object(bridge, "DEFAULT_STATE_DIR", state):
            bundled_only = {p.stem for p in bridge.manifest_paths()}
            self.assertEqual(bundled_only, {"gpt-6-astra", "gpt-5.6-sol", "glm-5.3-flash"})
            scoped = {p.stem for p in bridge.manifest_paths(None, ["gpt-6-astra"])}
            self.assertEqual(scoped, {"gpt-6-astra", "glm-5.3-flash"})
            selected = {p.stem for p in bridge.manifest_paths({"glm-5.3-flash"}, ["gpt-6-astra"])}
            self.assertEqual(selected, {"glm-5.3-flash"})
            selected_bundled = {p.stem for p in bridge.manifest_paths({"gpt-5.6-sol"}, ["gpt-6-astra"])}
            self.assertEqual(selected_bundled, {"gpt-5.6-sol"})
            with self.assertRaises(OSError) as caught:
                bridge.enabled_manifest_ids(
                    self.root / "enabled-manifests.json", {"gpt-6-astra"}
                )
            self.assertIn("No such file", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
