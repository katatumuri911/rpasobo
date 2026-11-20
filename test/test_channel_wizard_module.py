import ast
import os
import unittest
import rpchat.channel_wizard as w


class TestChannelWizardModule(unittest.TestCase):
    def test_import_has_no_side_effects(self):
        # import 済みなので I/O なしを担保: 主要パスが getter 経由であること
        self.assertTrue(w.WIZARD_SLOTS and w.REQUIRED_SLOTS)

    def test_deps_satisfied(self):
        import server
        self.assertEqual(w.missing_deps(), [])

    def test_missing_deps_detection(self):
        import server
        orig_load = w._deps.get("load")
        try:
            w._deps.pop("load", None)
            self.assertIn("load", w.missing_deps())
        finally:
            if orig_load is not None:
                w._deps["load"] = orig_load

    def test_missing_getters_detection(self):
        import server
        orig_wiz_dir = w._getters.get("WIZARD_DIR")
        try:
            w._getters.pop("WIZARD_DIR", None)
            self.assertIn("WIZARD_DIR", w.missing_deps())
        finally:
            if orig_wiz_dir is not None:
                w._getters["WIZARD_DIR"] = orig_wiz_dir

    def test_bind_deps_collision_prevention(self):
        # 自モジュールで定義された関数や import 済みの標準モジュールは上書き不可
        with self.assertRaisesRegex(RuntimeError, "定義済み（衝突）"):
            w.bind_deps(wslot_set=lambda *a: None)
        with self.assertRaisesRegex(RuntimeError, "定義済み（衝突）"):
            w.bind_deps(uuid=None)

    def test_bind_getters_callable_check(self):
        with self.assertRaisesRegex(TypeError, "ゼロ引数関数"):
            w.bind_getters(INVALID_KEY="not_a_callable")

    def test_g_missing_raises_runtime_error(self):
        with self.assertRaisesRegex(RuntimeError, "未設定"):
            w._g("DEFINITELY_NONEXISTENT_GETTER_NAME_12345")

    def test_getter_dynamism(self):
        import server
        old_dir = server.WIZARD_DIR
        try:
            server.WIZARD_DIR = "custom_test_dir_dynamic"
            self.assertEqual(w._g("WIZARD_DIR"), "custom_test_dir_dynamic")
        finally:
            server.WIZARD_DIR = old_dir

    def test_pure_functions_work(self):  # 依存ゼロの関数を実際に呼ぶ
        d = {}
        self.assertTrue(w.wslot_set(d, "world", ["学園"], "user"))
        self.assertEqual(w.wslot_value(d, "world"), ["学園"])
        self.assertNotEqual(w.wizard_norm_value(w.WIZARD_SLOTS["rough_idea"], "あ" * 400), "")
        self.assertEqual(w.wizard_clean_tags(["headdress", "flying"]), ["headdress", "flying"])
        self.assertEqual(w.wizard_public({"id": "wiz_0123456789", "slots": {}})["title_hint"], "新しいチャンネル")

    def test_server_hasattr_compatibility(self):
        import server
        # All required deps and getters exist on server
        for name in w.REQUIRED_DEPS:
            self.assertTrue(hasattr(server, name), f"server missing dep: {name}")
        for name in w.REQUIRED_GETTERS:
            self.assertTrue(hasattr(server, name), f"server missing getter: {name}")

        # Extract all symbols imported from rpchat.channel_wizard in server.py
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        imported_names = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "rpchat.channel_wizard":
                imported_names.extend(alias.name for alias in node.names)

        self.assertGreater(len(imported_names), 100, f"Expected >100 re-exports, got {len(imported_names)}")
        for s in imported_names:
            self.assertTrue(hasattr(server, s), f"server missing re-exported symbol: {s}")

        # Key 3 constants on server
        for c in ("WIZARD_DIR", "WIZARD_IDE_DIR", "WIZARD_TAGS_PATH"):
            self.assertTrue(hasattr(server, c), f"server missing constant: {c}")


if __name__ == "__main__":
    unittest.main()

