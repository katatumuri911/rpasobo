# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

import server


class DefaultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_data = server.DATA
        self._old_defaults = server.DEFAULTS
        server.DATA = os.path.join(self.tmp.name, "data")
        server.DEFAULTS = os.path.join(self.tmp.name, "defaults")
        os.makedirs(server.DATA, exist_ok=True)
        os.makedirs(server.DEFAULTS, exist_ok=True)

    def tearDown(self):
        server.DATA = self._old_data
        server.DEFAULTS = self._old_defaults
        self.tmp.cleanup()

    def _write(self, root, name, value):
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            if isinstance(value, str):
                fp.write(value)
            else:
                json.dump(value, fp, ensure_ascii=False)
        return path

    def test_defaults_path_is_under_defaults(self):
        p = server.defaults_path("sample.json")
        self.assertTrue(p.startswith(os.path.abspath(server.DEFAULTS)))

    def test_seed_path_returns_empty_when_missing(self):
        self.assertEqual(server.seed_path("missing.json"), "")

    def test_data_overrides_defaults(self):
        self._write(server.DEFAULTS, "sample.json", {"source": "defaults"})
        self._write(server.DATA, "sample.json", {"source": "data"})
        self.assertEqual(
            server.read_seed_json("sample.json", {}),
            {"source": "data"},
        )

    def test_defaults_used_when_data_missing(self):
        self._write(server.DEFAULTS, "sample.json", {"source": "defaults"})
        self.assertEqual(
            server.read_seed_json("sample.json", {}),
            {"source": "defaults"},
        )

    def test_missing_returns_fallback(self):
        self.assertEqual(server.read_seed_json("missing.json", {"x": 1}), {"x": 1})
        self.assertEqual(server.read_seed_text("missing.md", "fallback"), "fallback")

    def test_text_data_overrides_defaults(self):
        self._write(server.DEFAULTS, "style.md", "default")
        self._write(server.DATA, "style.md", "user")
        self.assertEqual(server.read_seed_text("style.md", ""), "user")

    def test_text_defaults_used_when_data_missing(self):
        self._write(server.DEFAULTS, "style.md", "default")
        self.assertEqual(server.read_seed_text("style.md", ""), "default")

    def test_parent_path_is_rejected(self):
        with self.assertRaises(ValueError):
            server.seed_path("../secret.json")

    def test_absolute_path_is_rejected(self):
        with self.assertRaises(ValueError):
            server.seed_path(os.path.abspath(os.sep + "outside.json"))

    def test_windows_separator_is_normalized(self):
        self._write(server.DEFAULTS, "nested/file.json", {"ok": True})
        self.assertEqual(
            server.read_seed_json("nested\\file.json", {}),
            {"ok": True},
        )

    def test_invalid_json_returns_fallback(self):
        self._write(server.DEFAULTS, "broken.json", "{")
        self.assertEqual(
            server.read_seed_json("broken.json", {"fallback": True}),
            {"fallback": True},
        )

    def test_repo_defaults_readme_exists(self):
        self.assertTrue(
            os.path.isfile(os.path.join(server.ROOT, "defaults", "README.md"))
        )


class WizardCatalogTest(unittest.TestCase):
    def test_wizard_tags_catalog_is_in_defaults(self):
        expected = os.path.join(server.ROOT, "defaults", "wizard_tags.json")
        self.assertEqual(
            os.path.abspath(server.WIZARD_TAGS_PATH),
            os.path.abspath(expected),
        )
        self.assertTrue(os.path.isfile(expected))
        raw = server.read_json(expected, {})
        self.assertIsInstance(raw.get("tags"), list)
        self.assertTrue(raw.get("tags"))


if __name__ == "__main__":
    unittest.main()
