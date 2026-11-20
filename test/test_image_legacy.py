# -*- coding: utf-8 -*-
"""L-07: 旧形式（文字列）image の取込・マイグレーション・要約の回帰テスト"""
import unittest

import server
from test.support import IsolatedTestCase


class TestLegacyStringImage(IsolatedTestCase):
    def test_clean_import_normalizes_string_image(self):
        _st, msgs = server.clean_import({
            "messages": [{"role": "user", "text": "こんにちは", "image": " images/old.png "}]
        })
        self.assertEqual(msgs[0]["image"], {"file": "images/old.png", "status": "done"})

    def test_clean_import_marks_unfinished_dict_as_error(self):
        _st, msgs = server.clean_import({
            "messages": [{"role": "assistant", "text": "生成中",
                          "image": {"file": "a.png", "status": "queued"}}]
        })
        self.assertEqual(msgs[0]["image"]["status"], "error")
        self.assertIn("error", msgs[0]["image"])

    def test_session_needs_migration_detects_string_image(self):
        s = {"messages": [{"id": "u1", "role": "user", "text": "x", "image": "images/old.png"}]}
        self.assertTrue(server.session_needs_migration(s))

    def test_summary_of_tolerates_string_image(self):
        s = {
            "id": "legacy_summary",
            "setting": {},
            "messages": [{"id": "u1", "role": "user", "text": "古い画像", "image": "images/old.png"}],
        }
        sm = server.summary_of(s)
        self.assertFalse(sm["generating"])
        self.assertEqual(sm["count"], 1)

    def test_summary_of_reports_generating_for_dict(self):
        s = {
            "id": "dict_summary",
            "setting": {},
            "messages": [{"id": "u1", "role": "user", "text": "生成中",
                          "image": {"file": "a.png", "status": "queued"}}],
        }
        self.assertTrue(server.summary_of(s)["generating"])

    def test_load_migrates_string_image(self):
        s = server.create_session({})
        s["messages"].append({
            "id": server.new_id(), "role": "user", "text": "古い画像",
            "ts": server.now(), "status": "answered", "image": "images/old.png",
        })
        server.save(s)
        server.session_cache.clear()   # 再起動直後のコールドリードを再現
        loaded = server.load(s["id"], readonly=True)
        self.assertEqual(loaded["messages"][-1]["image"], {"file": "images/old.png", "status": "done"})
        self.assertFalse(server.session_needs_migration(loaded))


if __name__ == "__main__":
    unittest.main()
