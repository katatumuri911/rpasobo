import hashlib
import json
import os
import shutil
import tempfile
import unittest
import server


class TestModelFavoritesFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = server.DATA
        server.DATA = self.tmp

        # Record real data/model_favorites.json hash if it exists
        self.real_fav_path = os.path.join(self.orig_data, "model_favorites.json")
        self.real_fav_exists = os.path.exists(self.real_fav_path)
        if self.real_fav_exists:
            with open(self.real_fav_path, "rb") as f:
                self.real_fav_hash = hashlib.sha256(f.read()).hexdigest()
        else:
            self.real_fav_hash = None

    def tearDown(self):
        server.DATA = self.orig_data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_favorites_path_reflects_swapped_data(self):
        expected = os.path.join(self.tmp, "model_favorites.json")
        self.assertEqual(server.model_favorites_path(), expected)

    def test_save_model_favorites_file_format_and_order(self):
        favs = [
            {
                "id": "fav_b",
                "name": "テストB（日本語）",
                "provider": "google",
                "main": "gemini-2.5-flash",
                "choices": "",
                "thinking": "auto",
                "choices_thinking": "low",
                "order": 99,
            },
            {
                "id": "fav_a",
                "name": "テストA（絵文字✨）",
                "provider": "google",
                "main": "gemini-2.5-pro",
                "choices": "gemini-2.5-flash",
                "thinking": "high",
                "choices_thinking": "low",
                "order": 12,
            },
        ]
        server.save_model_favorites(favs)

        target_file = os.path.join(self.tmp, "model_favorites.json")
        self.assertTrue(os.path.exists(target_file))

        # Check raw bytes for ensure_ascii=False and utf-8
        with open(target_file, "rb") as f:
            raw_bytes = f.read()

        text = raw_bytes.decode("utf-8")
        self.assertIn("テストB（日本語）", text)
        self.assertIn("✨", text)
        self.assertNotIn(r"\u30c6", text)  # not escaped

        # Check indent=2
        lines = text.splitlines()
        self.assertTrue(any(line.startswith("  \"version\": 1") for line in lines))
        self.assertTrue(any(line.startswith("  \"favorites\": [") for line in lines))

        # Check content and order re-indexing
        data = json.loads(text)
        self.assertEqual(data.get("version"), 1)
        loaded_favs = data.get("favorites")
        self.assertEqual(len(loaded_favs), 2)
        self.assertEqual(loaded_favs[0]["order"], 0)
        self.assertEqual(loaded_favs[1]["order"], 1)
        self.assertEqual(loaded_favs[0]["id"], "fav_b")
        self.assertEqual(loaded_favs[1]["id"], "fav_a")

        # Check load_model_favorites works
        loaded = server.load_model_favorites()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["id"], "fav_b")
        self.assertEqual(loaded[1]["id"], "fav_a")

    def test_real_data_remains_unchanged(self):
        dummy_fav = [{
            "id": "fav_dummy",
            "name": "実データ破壊防止",
            "provider": "antigravity",
            "main": "",
            "choices": "",
            "thinking": "",
            "choices_thinking": "",
            "order": 0,
        }]
        server.save_model_favorites(dummy_fav)

        # Ensure real data directory was NOT touched
        if self.real_fav_exists:
            self.assertTrue(os.path.exists(self.real_fav_path))
            with open(self.real_fav_path, "rb") as f:
                cur_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(cur_hash, self.real_fav_hash)
        else:
            self.assertFalse(os.path.exists(self.real_fav_path))

    def test_bind_deps_type_error_and_unbound_error(self):
        import rpchat.model_favorites as mf
        # Non-callable raises TypeError
        with self.assertRaises(TypeError):
            mf.bind_deps(data="not_a_callable")

        # Unbound dep raises RuntimeError
        with self.assertRaises(RuntimeError):
            mf._dep("non_existent_dep_key")

    def test_load_model_favorites_handles_malformed_items(self):
        target_file = os.path.join(self.tmp, "model_favorites.json")
        malformed = {
            "version": 1,
            "favorites": [
                "not-a-dict",
                None,
                {"id": "fav_bad_order", "name": "不正order", "order": "invalid_int"},
                {"id": "fav_valid", "name": "正常", "order": 0},
            ]
        }
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(malformed, f)

        loaded = server.load_model_favorites()
        self.assertEqual(len(loaded), 2)
        # fav_valid has order 0, fav_bad_order has order 2 (from enumeration index)
        self.assertEqual(loaded[0]["id"], "fav_valid")
        self.assertEqual(loaded[1]["id"], "fav_bad_order")
        self.assertEqual(loaded[1]["order"], 2)

    def test_model_favorites_unbound_raises_runtime_error(self):
        import rpchat.model_favorites as mf
        saved = dict(mf._deps)
        try:
            mf._deps.clear()
            with self.assertRaises(RuntimeError):
                mf.model_favorites_path()
            with self.assertRaises(RuntimeError):
                mf.load_model_favorites()
            with self.assertRaises(RuntimeError):
                mf.save_model_favorites([])
        finally:
            mf._deps.update(saved)

    def test_read_json_write_json_monkeypatch_dynamic(self):
        calls = []
        orig_read = server.read_json
        orig_write = server.write_json
        try:
            server.read_json = lambda path, default=None: calls.append(("read", path)) or default
            server.load_model_favorites()
            self.assertTrue(any(c[0] == "read" and c[1].endswith("model_favorites.json") for c in calls))

            server.write_json = lambda path, obj: calls.append(("write", path))
            server.save_model_favorites([])
            self.assertTrue(any(c[0] == "write" and c[1].endswith("model_favorites.json") for c in calls))
        finally:
            server.read_json = orig_read
            server.write_json = orig_write

