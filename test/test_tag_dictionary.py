# -*- coding: utf-8 -*-
"""tag_dictionary.json（辞書の唯一の正）の読み込み・フォールバック・再読み込みのテスト"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server


def deep(v):
    if isinstance(v, (list, tuple)):
        return [deep(x) for x in v]
    return v


class TestTagDictionary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tagdict_")
        self.orig_path = server.TAG_DICT_PATH
        self.orig_mtime = server._tag_dict_mtime

    def tearDown(self):
        server.TAG_DICT_PATH = self.orig_path
        server._tag_dict_mtime = self.orig_mtime
        server.reload_tag_dictionary()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- 読み込み ---
    def test_01_real_json_loads(self):
        """実際のJSONが読み込める（全カテゴリが空でない）"""
        d = server._load_tag_dictionary(server.TAG_DICT_PATH)
        self.assertIsInstance(d, dict)
        for k in ("pose", "prop", "item", "expression", "place", "camera"):
            self.assertGreater(len(d[k]), 0, k)

    def test_02_globals_match_json(self):
        """モジュール変数がJSONの内容と順序どおり一致する"""
        with open(server.TAG_DICT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(deep(server.POSE_TAG_MAP), deep(d["pose"]))
        self.assertEqual(deep(server.PROP_TAG_MAP), deep(d["prop"]))
        self.assertEqual(deep(server.ITEM_TAG_MAP), deep(d["item"]))
        self.assertEqual(deep(server.EXPRESSION_TAG_MAP), deep(d["expression"]))
        self.assertEqual(deep(server.PLACE_TAG_MAP), deep(d["place"]))
        self.assertEqual(deep(server.CAMERA_TAG_MAP), deep(d["camera"]))
        self.assertEqual(dict(server.POSE_TAG_ALIASES), d["pose_aliases"])
        self.assertEqual(dict(server.PROP_TAG_ALIASES), d["prop_aliases"])
        self.assertEqual(deep(server.GENERAL_TAG_MAP), deep(d.get("general") or []))

    # --- フォールバック ---
    def test_03_missing_file_returns_none(self):
        """ファイルが無ければ None（起動は止めない）"""
        self.assertIsNone(server._load_tag_dictionary(os.path.join(self.tmp, "nope.json")))

    def test_04_broken_json_returns_none(self):
        """壊れたJSONは None"""
        p = os.path.join(self.tmp, "broken.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        self.assertIsNone(server._load_tag_dictionary(p))

    def test_05_missing_key_returns_none(self):
        """必須キーが無ければ None"""
        p = os.path.join(self.tmp, "missing.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"pose": [], "prop": [], "item": [], "expression": [], "camera": []}, f)
        self.assertIsNone(server._load_tag_dictionary(p))

    def test_06_empty_tags_returns_none(self):
        """tags が空のエントリは無効"""
        d = {
            "pose": [[["x"], []]], "prop": [], "item": [], "expression": [], "camera": [],
            "place": [], "pose_aliases": {}, "prop_aliases": {},
            "posture_conflict_tags": [], "place_never_strip": [], "place_compatible": [],
            "camera_angle_tags": [], "camera_framing_tags": [], "expression_groups": [],
        }
        p = os.path.join(self.tmp, "empty_tags.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        self.assertIsNone(server._load_tag_dictionary(p))

    def test_07_failed_reload_keeps_current(self):
        """壊れたJSONでリロードしても、今の辞書を壊さない"""
        before = server.POSE_TAG_MAP
        p = os.path.join(self.tmp, "bad2.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{oops")
        server.TAG_DICT_PATH = p
        self.assertFalse(server.reload_tag_dictionary())
        self.assertEqual(server.POSE_TAG_MAP, before)

    # --- 再読み込み ---
    def test_08_reload_applies_new_entry(self):
        """JSONに追記してリロードすると、新しいタグが実際に使われる"""
        with open(server.TAG_DICT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        d["pose"].append([["テスト用ポーズ"], ["test pose"]])
        p = os.path.join(self.tmp, "added.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        server.TAG_DICT_PATH = p
        self.assertTrue(server.reload_tag_dictionary())
        st = [{"name": "A", "pose": "テスト用ポーズ"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertIn("test pose", res)

    def test_10_general_loaded(self):
        """general（一般タグ）が読み込まれている"""
        d = server._load_tag_dictionary(server.TAG_DICT_PATH)
        self.assertGreater(len(d.get("general") or []), 0)
        self.assertEqual(len(server.GENERAL_TAG_MAP), len(d["general"]))

    def test_11_general_applied_from_outfit(self):
        """state.outfit の一般タグが画像タグとして出る"""
        st = [{"name": "A", "outfit": "黒髪で水着"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("black hair", res)
        self.assertIn("swimsuit", res)

    def test_11b_ascii_tags_in_outfit_and_fluids(self):
        """辞書に無い衣装・下着・体液は英語タグとしてそのまま拾う"""
        st = [{"name": "A", "outfit": "blue dress, white thighhighs",
               "underwear": "white panties", "body_fluids": "sweat"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        for tag in ("blue dress", "white thighhighs", "white panties", "sweat"):
            self.assertIn(tag, res)
        # 日本語で辞書にヒットする場合は二重に拾わない
        st2 = [{"name": "A", "outfit": "全裸"}]
        res2 = server.sanitize_prompt("1girl", state=st2, allow_inject=True)
        self.assertIn("completely nude", res2)
        self.assertNotIn("全裸", res2)

    def test_12_general_does_not_false_positive_on_place(self):
        """state.place='ベッドルーム' で general の「ベッド→bed」が誤爆しない"""
        st = [{"name": "A", "place": "ベッドルーム"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("bedroom", tags)
        self.assertNotIn("bed", tags)

    def test_13_m_legs_maps_to_danbooru_tag(self):
        """M字開脚・観音開きは Danbooru の m_legs タグに変換される"""
        for pose in ("M字開脚", "観音開き"):
            st = [{"name": "A", "pose": pose}]
            res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
            self.assertIn("m legs", res, pose)
        # 単なる開脚は m legs にしない
        st = [{"name": "A", "pose": "脚を開いた"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertNotIn("m legs", res)
        self.assertIn("spread legs", res)

    def test_14_text_tag_misses_collects_unknown_nouns(self):
        """本文の未登録名詞だけを text_tag_misses.log に記録する"""
        orig_data = server.DATA
        p = os.path.join(self.tmp, "text_tag_misses.log")
        server.DATA = self.tmp
        try:
            setting = {"character": {"name": "ラピ"}, "user_name": "あなた"}
            text = "ラピは宇宙遊泳している。M字開脚で座り、シャンプーボトルを持っている。"
            server.collect_text_tag_misses(text, "ai", setting)
            with open(p, encoding="utf-8") as f:
                body = f.read()
            self.assertIn("宇宙遊泳", body)
            self.assertNotIn("M字開脚", body)          # 既知語
            self.assertNotIn("シャンプーボトル", body)  # 既知語
            self.assertNotIn("ラピ", body)              # キャラ名は除外
            # 同じ内容を再投入しても増えない
            n = len(body.splitlines())
            server.collect_text_tag_misses(text, "ai", setting)
            with open(p, encoding="utf-8") as f:
                self.assertEqual(len(f.read().splitlines()), n)
        finally:
            server.DATA = orig_data

    def test_09_order_preserved(self):
        """先頭エントリがJSONの先頭と一致する（並び順が保たれる）"""
        with open(server.TAG_DICT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(deep(server.POSE_TAG_MAP[0]), deep(d["pose"][0]))
        self.assertEqual(deep(server.PLACE_TAG_MAP[0]), deep(d["place"][0]))


if __name__ == "__main__":
    unittest.main()
