# -*- coding: utf-8 -*-
"""候補の婉曲表現 → 直接表現（decensor）のテスト"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server

# serve_forever の待ち時間。既定の 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒ブロックする
_POLL = 0.02

# リポジトリの data/decensor.json と同じ語彙（辞書は data/decensor.json だけが正になったため、
# テストはユーザーの辞書に依存しないよう自前の語彙を書く）
DICT = {
    "後ろの穴": "ケツマンコ",
    "お尻の穴": "ケツマンコ",
    "後孔": "アナル",
    "不浄の穴": "アナル",
    "肛門": "アナル",
    "前の穴": "おまんこ",
    "前穴": "おまんこ",
    "蜜壺": "肉壺",
    "蜜穴": "おまんこ",
    "秘所": "おまんこ",
    "秘部": "おまんこ",
    "胸の先": "乳首",
    "膣": "ナカ",
    "胸の膨らみ": "おっぱい",
    "双乳": "おっぱい",
    "乳房": "おっぱい",
    "臀部": "ケツ",
    "尻肉": "尻たぶ",
    "肉棒": "おちんぽ",
    "剛直": "おちんぽ",
    "怒張": "おちんぽ",
    "陰茎": "おちんぽ",
    "精液": "ザーメン",
    "白濁液": "ザーメン",
    "突起（クリトリス）": "クリトリス",
    "突起(クリトリス)": "クリトリス",
    "胸の先端の突起": "乳首",
    "突起洗濯バサミ": "乳首クリップ",
    "突起吸引器": "乳首吸引器",
    "突起ピンチ": "乳首ピンチ",
    "突起クリップ": "乳首クリップ",
    "突起ピアス": "乳首ピアス",
    "突起リング": "乳首リング",
    "突起ローター": "乳首ローター",
    "突起吸引": "乳首吸引",
    "乳首の先端の突起": "乳首",
    "クリトリスの突起": "クリトリス",
    "乳首の突起": "乳首",
    "突起した乳首": "勃起した乳首",
    "突起した先端": "勃起した先端",
    "突起した": "勃起した",
    "突起して": "勃起して",
    "突起する": "勃起する",
    "突起し": "勃起し",
    "突起物": "乳首",
    "突起部分": "乳首",
    "胸の突起": "乳首",
    "先端の突起": "乳首",
    "外側の突起": "クリトリス",
    "奥と突起": "奥とクリトリス",
    "割れ目の突起": "クリトリス",
    "前の突起": "クリトリス",
    "クリの突起": "クリトリス",
    "花芯の突起": "クリトリス",
    "芯の突起": "クリトリス",
    "突起": "乳首",
    "胸の頂点": "乳首",
    "胸の先っぽ": "乳首",
    "股の頂点": "クリトリス",
    "割れ目の頂点": "クリトリス",
    "花芯": "クリトリス",
    "肉芽": "クリトリス",
}


def write_dict_file(words=None):
    """テスト用の辞書を data/decensor.json（DATA はテストごとの tmp）に置く"""
    server.write_json(server.decensor_path(), {"enabled": True, "words": words or DICT})
    server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)


class TestDecensor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_decensor_")
        self.orig = {
            "DATA": server.DATA,
            "SESS_DIR": server.SESS_DIR,
            "CHOICES_DIR": server.CHOICES_DIR,
        }
        server.DATA = os.path.join(self.tmp, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        os.makedirs(server.CHOICES_DIR, exist_ok=True)
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        server._decensor_undo.clear()
        write_dict_file()

    def tearDown(self):
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        server._decensor_undo.clear()
        for k, v in self.orig.items():
            setattr(server, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_dict(self, words, bump=10):
        p = server.decensor_path()
        server.write_json(p, {"enabled": True, "words": words})
        t = time.time() + bump
        os.utime(p, (t, t))  # 同じ秒に2回書いても mtime が変わるように
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)

    def test_default_dictionary(self):
        out, info = server.finalize_choices([
            {"label": "後ろの穴を攻める", "text": "秘所と膣と肉棒と精液。尻肉を掴む。"},
        ])
        self.assertEqual(out[0]["label"], "ケツマンコを攻める")
        self.assertEqual(out[0]["text"], "おまんことナカとおちんぽとザーメン。尻たぶを掴む。")
        got = {(a["from"], a["to"]): a["count"] for a in info["applied"]}
        self.assertEqual(got[("後ろの穴", "ケツマンコ")], 1)
        self.assertEqual(got[("膣", "ナカ")], 1)

    def test_protrusion_variants(self):
        out, info = server.finalize_choices([{
            "label": "突起洗濯バサミ",
            "text": "胸の先端の突起と突起物、突起した乳首を摘む。",
        }])
        self.assertEqual(out[0]["label"], "乳首クリップ")
        self.assertEqual(out[0]["text"], "乳首と乳首、勃起した乳首を摘む。")
        out, _ = server.apply_choice_decensor([{
            "label": "突起ピンチ",
            "text": "外側の突起と奥と突起、割れ目の突起、突起（クリトリス）を責める。",
        }])
        self.assertEqual(out[0]["label"], "乳首ピンチ")
        self.assertEqual(out[0]["text"], "クリトリスと奥とクリトリス、クリトリス、クリトリスを責める。")

    def test_protrusion_default_is_nipple(self):
        # 手がかりが無い「突起」は乳首（データ上、選択肢の突起はほぼ乳首）
        out, _ = server.apply_choice_decensor([{"label": "突起責め", "text": "敏感な突起を弾く"}])
        self.assertEqual(out[0]["label"], "乳首責め")
        self.assertEqual(out[0]["text"], "敏感な乳首を弾く")

    def test_placeholder_then_decensor(self):
        out, info = server.finalize_choices([{"label": "a", "text": "__JP_CS__と__JP_HB__"}])
        self.assertEqual(out[0]["text"], "ナカとおまんこ")  # __JP_HB__ は「秘部」なので辞書で戻る
        self.assertEqual(info["replaced"], 2)

    def test_longest_first_and_single_pass(self):
        self.write_dict({"穴": "X", "後ろの穴": "Y", "Y": "Z"})
        out, _ = server.apply_choice_decensor([{"label": "後ろの穴と穴", "text": "Y"}])
        self.assertEqual(out[0]["label"], "YとX")   # 長い語が先。Y→Z は起きない（1パス）
        self.assertEqual(out[0]["text"], "Z")       # 元から Y だったものは Z になる

    def test_count_with_overlapping_keys(self):
        self.write_dict({"後ろの穴": "アナル", "穴": "ホール"})
        _, applied = server.apply_choice_decensor([{"label": "後ろの穴と穴", "text": "穴"}])
        got = {a["from"]: a["count"] for a in applied}
        self.assertEqual(got["後ろの穴"], 1)
        self.assertEqual(got["穴"], 2)   # 「後ろの穴」の中の「穴」は数えず、単独の「穴」2か所だけ

    def test_backslash_in_value(self):
        self.write_dict({"秘所": "a\\1b"})
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "秘所"}])
        self.assertEqual(out[0]["text"], "a\\1b")

    def test_channel_switch_off(self):
        out, info = server.finalize_choices([{"label": "x", "text": "秘所"}], {"decensor_choices": False})
        self.assertEqual(out[0]["text"], "秘所")
        self.assertEqual(info["applied"], [])
        self.assertIsNone(info["before"])

    def test_parse_text(self):
        words, warnings = server.parse_decensor_text(
            "後ろの穴=ケツマンコ\n膣＝ナカ\n__JP_AN__=アナル\n# コメント\n穴=ホール\n変な行"
        )
        self.assertEqual(words, {"後ろの穴": "ケツマンコ", "膣": "ナカ", "穴": "ホール"})
        self.assertTrue(any("1文字" in w for w in warnings))
        self.assertTrue(any("プレースホルダー" in w for w in warnings))
        self.assertTrue(any("形ではありません" in w for w in warnings))

    def test_file_change_is_picked_up(self):
        self.write_dict({"秘所": "おまんこ"})
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "秘所"}])
        self.assertEqual(out[0]["text"], "おまんこ")
        self.write_dict({"秘所": "ワレメ"})
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "秘所"}])
        self.assertEqual(out[0]["text"], "ワレメ")

    def test_explicit_hits_count_decensored_words(self):
        # 膣→ナカ に置換しても「露骨語0件」にならない（差し替え疑いの誤報を防ぐ）
        self.assertTrue(server.choices_explicit_hits([{"label": "x", "text": "ナカ"}]))
        self.assertTrue(server.choices_explicit_hits([{"label": "x", "text": "ケツマンコ"}]))

    def test_rescue_path_applies_decensor(self):
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "本文", "ts": server.now()})
        server.save(s)
        n = server._rescue_into_message(s["id"], mid, "erotic", "t", {"choices": [
            {"label": "後ろの穴", "text": "秘所を責める"},
        ]})
        self.assertEqual(n, 1)
        s2 = server.load(s["id"])
        m = server.find_msg(s2, mid)
        self.assertEqual(m["choices_erotic"][0]["label"], "ケツマンコ")
        self.assertEqual(m["choices_decensor"]["slot"], "erotic")
        rec = server._decensor_undo.get((s["id"], mid, "choices"))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["before"][0]["label"], "後ろの穴")

    def test_context_cue_before_and_after(self):
        self.write_dict({"突起|股": "クリトリス", "突起": "乳首"})
        # 手がかりが前
        out, _ = server.apply_choice_decensor([{"label": "股の突起を愛撫", "text": "突起を摘む"}])
        self.assertEqual(out[0]["label"], "股のクリトリスを愛撫")
        self.assertEqual(out[0]["text"], "乳首を摘む")
        # 手がかりが後ろ
        out, _ = server.apply_choice_decensor([{"label": "突起の先の股", "text": "突起"}])
        self.assertEqual(out[0]["label"], "クリトリスの先の股")
        self.assertEqual(out[0]["text"], "乳首")

    def test_context_cue_gap_exact_window(self):
        self.write_dict({"突起|股": "クリトリス", "突起": "乳首"})
        # 窓境界テスト: 手がかり前
        # gap = 16 文字: "股" + 16文字 + "突起" -> ヒット
        text_16_before = "股" + ("あ" * 16) + "突起"
        out, _ = server.apply_choice_decensor([{"label": "x", "text": text_16_before}])
        self.assertEqual(out[0]["text"], "股" + ("あ" * 16) + "クリトリス")
        # gap = 17 文字: "股" + 17文字 + "突起" -> 窓外、既定の乳首へフォールバック
        text_17_before = "股" + ("あ" * 17) + "突起"
        out, _ = server.apply_choice_decensor([{"label": "x", "text": text_17_before}])
        self.assertEqual(out[0]["text"], "股" + ("あ" * 17) + "乳首")
        # 窓境界テスト: 手がかり後ろ
        # gap = 16 文字: "突起" + 16文字 + "股" -> ヒット
        text_16_after = "突起" + ("い" * 16) + "股"
        out, _ = server.apply_choice_decensor([{"label": "x", "text": text_16_after}])
        self.assertEqual(out[0]["text"], "クリトリス" + ("い" * 16) + "股")
        # gap = 17 文字: "突起" + 17文字 + "股" -> 窓外、既定の乳首へフォールバック
        text_17_after = "突起" + ("い" * 17) + "股"
        out, _ = server.apply_choice_decensor([{"label": "x", "text": text_17_after}])
        self.assertEqual(out[0]["text"], "乳首" + ("い" * 17) + "股")

    def test_context_cue_closest_wins(self):
        self.write_dict({"突起|股": "クリトリス", "突起|胸": "乳首"})
        # "股" gap = 5, "胸" gap = 1 -> "胸"が近いので乳首
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "股の奥で胸の突起を触る"}])
        self.assertEqual(out[0]["text"], "股の奥で胸の乳首を触る")
        # "胸" gap = 7, "股" gap = 1 -> "股"が近いのでクリトリス
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "胸を揉みつつ股の突起を弾く"}])
        self.assertEqual(out[0]["text"], "胸を揉みつつ股のクリトリスを弾く")

    def test_context_cue_same_distance_dict_order(self):
        # 辞書順で 突起|股 が先
        self.write_dict({"突起|股": "クリトリス", "突起|胸": "乳首"})
        # "股" gap = 1, "胸" gap = 1 の等距離
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "股と突起と胸"}])
        self.assertEqual(out[0]["text"], "股とクリトリスと胸")

        # 辞書順を入れ替えて 突起|胸 が先
        self.write_dict({"突起|胸": "乳首", "突起|股": "クリトリス"})
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "股と突起と胸"}])
        self.assertEqual(out[0]["text"], "股と乳首と胸")

    def test_context_cue_overlap_ignored(self):
        # 手がかりと語本体が同一または包含関係にある場合、自分自身との重なりは無視される
        self.write_dict({"突起|突起": "クリトリス", "突起": "乳首"})
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "突起"}])
        self.assertEqual(out[0]["text"], "乳首")  # 重なり除外でフォールバック

        # 包含関係: 突起物 をターゲットとし、突起 を手がかりとした場合
        self.write_dict({"突起物|突起": "クリトリス", "突起物": "乳首"})
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "突起物"}])
        self.assertEqual(out[0]["text"], "乳首")  # 包含重なり除外でフォールバック

    def test_context_no_fallback_leaves_original(self):
        # 既定行（突起=...）が無い場合
        self.write_dict({"突起|股": "クリトリス"})
        out, applied = server.apply_choice_decensor([
            {"label": "股の突起", "text": "無関係な突起を摘む"}
        ])
        self.assertEqual(out[0]["label"], "股のクリトリス")
        self.assertEqual(out[0]["text"], "無関係な突起を摘む")  # 手がかり無し＆既定無しで無置換
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["from"], "突起")
        self.assertEqual(applied[0]["to"], "クリトリス")
        self.assertEqual(applied[0]["count"], 1)

    def test_missing_file_means_no_conversion(self):
        # 辞書は data/decensor.json だけが正。無ければ変換しない（既定辞書は持たない）
        p = server.decensor_path()
        if os.path.exists(p):
            os.remove(p)
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)

        m1 = server.decensor_map()
        self.assertEqual(server._DECENSOR_CACHE["key"], "missing")
        self.assertEqual(m1, {})
        out, applied = server.apply_choice_decensor([{"label": "x", "text": "後ろの穴と秘所"}])
        self.assertEqual(out[0]["text"], "後ろの穴と秘所")
        self.assertEqual(applied, [])

        m2 = server.decensor_map()
        self.assertIs(m1, m2)  # キャッシュから同一オブジェクトが返る

    def test_parse_and_validate_context_keys(self):
        text = """
        # 正常な文脈ルール
        突起|股,割れ目=クリトリス
        # 1文字の語本体
        芽|股=クリトリス
        # 手がかり空
        突起|=乳首
        # 語本体空
        |股=クリトリス
        # プレースホルダー
        突起|__JP_AN__=アナル
        __JP_CS__|股=ナカ
        # 連鎖
        胸=突起
        # 正常な同語フォールバック行
        突起=乳首
        """
        words, warnings = server.parse_decensor_text(text)
        self.assertIn("突起|股,割れ目", words)
        self.assertEqual(words["突起|股,割れ目"], "クリトリス")
        self.assertIn("芽|股", words)
        self.assertIn("胸", words)
        self.assertIn("突起", words)
        self.assertNotIn("突起|", words)
        self.assertNotIn("|股", words)

        # 警告の検証
        self.assertTrue(any("1文字の語は誤爆しやすいので注意: 芽" in w for w in warnings))
        self.assertFalse(any("1文字の語は誤爆しやすいので注意: 股" in w for w in warnings))
        self.assertTrue(any("手がかりが空です" in w for w in warnings))
        self.assertTrue(any("語本体が空です" in w for w in warnings))
        self.assertTrue(any("プレースホルダー" in w for w in warnings))
        # 連鎖警告: 胸→突起 が 突起 を含む
        self.assertTrue(any("「胸→突起」は「突起」を含むため" in w for w in warnings))
        # 突起|股 や 突起 自体は誤って連鎖と判定されない
        self.assertFalse(any("「突起|股" in w for w in warnings))
        self.assertFalse(any("「突起→乳首」" in w for w in warnings))

    def test_raw_json_validation_with_context(self):
        raw = {
            "enabled": True,
            "words": {
                "突起|股,割れ目": "クリトリス",
                "突起|": "乳首",
                "|股": "クリトリス",
                "|": "無効",
                "__JP_AN__|股": "アナル",
                "突起|__JP_AN__": "アナル",
                "突起|股": "突起",  # word == to
                "突起": "乳首",
                "正常な語": "値",
            }
        }
        res = server._decensor_words_from(raw)
        self.assertIsNotNone(res)
        self.assertIn("突起|股,割れ目", res)
        self.assertEqual(res["突起|股,割れ目"], "クリトリス")
        self.assertIn("突起", res)
        self.assertEqual(res["突起"], "乳首")
        self.assertIn("正常な語", res)
        self.assertEqual(res["正常な語"], "値")
        self.assertNotIn("突起|", res)
        self.assertNotIn("|股", res)
        self.assertNotIn("|", res)
        self.assertNotIn("__JP_AN__|股", res)
        self.assertNotIn("突起|__JP_AN__", res)
        self.assertNotIn("突起|股", res)  # word == to で除外

    def test_applied_records_actual_context_translation(self):
        self.write_dict({"突起|股": "クリトリス", "突起": "乳首"})
        out, applied = server.apply_choice_decensor([
            {"label": "股の突起を愛撫", "text": "突起を摘む"}
        ])
        got = {(a["from"], a["to"]): a["count"] for a in applied}
        self.assertEqual(got[("突起", "クリトリス")], 1)
        self.assertEqual(got[("突起", "乳首")], 1)

    def test_context_cue_fullwidth_delimiters(self):
        # 全角 ｜、全角 、、全角 ＝、全角 ＃ のテスト
        text = "＃ 全角コメント\n突起｜股、割れ目＝クリトリス\n突起＝乳首"
        words, warnings = server.parse_decensor_text(text)
        self.assertEqual(warnings, [])
        self.assertEqual(words, {"突起|股,割れ目": "クリトリス", "突起": "乳首"})
        self.write_dict(words)
        out, applied = server.apply_choice_decensor([{"label": "股の突起", "text": "突起"}])
        self.assertEqual(out[0]["label"], "股のクリトリス")
        self.assertEqual(out[0]["text"], "乳首")

    def test_context_cue_multiple_pipes(self):
        # 突起|股|割れ目 のように | を複数書いた場合の安全な正規化
        words, warnings = server.parse_decensor_text("突起|股|割れ目=クリトリス")
        self.assertEqual(warnings, [])
        self.assertEqual(words, {"突起|股,割れ目": "クリトリス"})

    def test_context_cue_two_targets_in_same_string(self):
        # 同一文字列内に複数のターゲット語があり、それぞれ異なる最近傍手がかりを持つ
        self.write_dict({"突起|股": "クリトリス", "突起|胸": "乳首"})
        out, applied = server.apply_choice_decensor([
            {"label": "x", "text": "股の突起と、遠く離れた別の場所にある胸の突起"}
        ])
        self.assertEqual(out[0]["text"], "股のクリトリスと、遠く離れた別の場所にある胸の乳首")
        got = {(a["from"], a["to"]): a["count"] for a in applied}
        self.assertEqual(got[("突起", "クリトリス")], 1)
        self.assertEqual(got[("突起", "乳首")], 1)

    def test_text_apply(self):
        text, applied = server._decensor_apply("突起を摘み、尻肉を揉む。")
        self.assertEqual(text, "乳首を摘み、尻たぶを揉む。")
        self.assertEqual({a["from"] for a in applied}, {"突起", "尻肉"})

    def test_text_context_rule(self):
        # 辞書ファイルへ正しく dict を書き込む（set リテラル禁止、キーに = は含めない）
        self.write_dict({"突起|股,割れ目": "クリトリス", "突起": "乳首"})
        text, _ = server._decensor_apply("股の突起を弾く")
        self.assertEqual(text, "股のクリトリスを弾く")   # 手がかりの「股」は残る
        text, _ = server._decensor_apply("突起を弾く")
        self.assertEqual(text, "乳首を弾く")             # 手がかりが無ければ既定

    def test_clone_session_text_decensor(self):
        s = {
            "id": "s_test",
            "setting": {},
            "messages": [{
                "id": "m1",
                "role": "assistant",
                "text": "乳首を摘む",
                "text_decensor": {
                    "applied": [{"from": "突起", "to": "乳首", "count": 1}],
                    "ts": "2026-11-10T00:00:00"
                }
            }]
        }
        cloned = server.clone_session(s)
        self.assertEqual(cloned["messages"][0]["text_decensor"]["applied"][0]["from"], "突起")
        # 深いコピーの確認: 複製側を変更しても元データに影響しないこと
        cloned["messages"][0]["text_decensor"]["applied"][0]["from"] = "改変"
        self.assertEqual(s["messages"][0]["text_decensor"]["applied"][0]["from"], "突起")

    def test_text_apply_edge_cases(self):
        # 空文字・非文字列・None
        self.assertEqual(server._decensor_apply(""), ("", []))
        self.assertEqual(server._decensor_apply(None), (None, []))
        self.assertEqual(server._decensor_apply(12345), (12345, []))
        # 置換対象のない平文
        t_plain, app_plain = server._decensor_apply("こんにちは世界")
        self.assertEqual(t_plain, "こんにちは世界")
        self.assertEqual(app_plain, [])
        # 同一語が複数回出現した場合のカウント
        t_multi, app_multi = server._decensor_apply("突起と突起")
        self.assertEqual(t_multi, "乳首と乳首")
        self.assertEqual(app_multi[0]["count"], 2)
        # compiled 引数の明示的受け渡し
        compiled = server._decensor_compiled()
        t_comp, app_comp = server._decensor_apply("突起", compiled=compiled)
        self.assertEqual(t_comp, "乳首")

    def test_undo_lru_and_reinsert_behavior(self):
        # 20件制限のLRU挙動
        server._decensor_undo.clear()
        for i in range(25):
            server.remember_text_undo("sid", f"mid_{i}", f"before_{i}", f"after_{i}")
        self.assertEqual(len(server._decensor_undo), server._DECENSOR_UNDO_MAX)
        # 古い mid_0 ~ mid_4 は追い出され、mid_5 以降が残る
        self.assertNotIn(("sid", "mid_0", "text"), server._decensor_undo)
        self.assertIn(("sid", "mid_24", "text"), server._decensor_undo)

        # 既存キーの再挿入で末尾に移動（LRU順位の更新）
        server.remember_text_undo("sid", "mid_5", "before_5_new", "after_5_new")
        # 16件追加して合計20件を超過させる
        for j in range(16):
            server.remember_text_undo("sid", f"mid_new_{j}", f"b_{j}", f"a_{j}")
        # mid_6 などは追い出されるが、末尾に更新された mid_5 は生き残る
        self.assertNotIn(("sid", "mid_6", "text"), server._decensor_undo)
        self.assertIn(("sid", "mid_5", "text"), server._decensor_undo)


class TestReplyDecensor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_reply_decensor_")
        self.orig = {
            "DATA": server.DATA,
            "SESS_DIR": server.SESS_DIR,
            "CHOICES_DIR": server.CHOICES_DIR,
            "OUTBOX": server.OUTBOX,
            "DONE": server.DONE,
            "SENT": server.SENT,
        }
        server.DATA = os.path.join(self.tmp, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        server.OUTBOX = os.path.join(self.tmp, "outbox")
        server.DONE = os.path.join(self.tmp, "done")
        server.SENT = os.path.join(self.tmp, "sent")
        for d in (server.DATA, server.SESS_DIR, server.CHOICES_DIR, server.OUTBOX, server.DONE, server.SENT):
            os.makedirs(d, exist_ok=True)
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        server._decensor_undo.clear()
        server.queued_images.clear()
        server.queued_memories.clear()
        write_dict_file()

    def tearDown(self):
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        server._decensor_undo.clear()
        server.queued_images.clear()
        server.queued_memories.clear()
        for k, v in self.orig.items():
            setattr(server, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pending_session(self, **setting):
        s = server.create_session({"title": "t", "image_mode": "off", **setting})
        s["messages"].append({
            "id": server.new_id(), "role": "user", "text": "続けて",
            "ts": server.now(), "status": "pending",
        })
        server.save(s)
        return s

    def _drop(self, name, obj):
        with open(os.path.join(server.OUTBOX, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_reply_body_decensored(self):
        s = self._pending_session(decensor_reply=True)
        uid = s["messages"][-1]["id"]
        self._drop("r_reply.json", {"session": s["id"], "reply_to": uid, "text": "突起を摘み、尻肉を揉む。"})
        self.assertTrue(server.process_outbox())
        m = server.load(s["id"])["messages"][-1]
        self.assertEqual(m["text"], "乳首を摘み、尻たぶを揉む。")
        self.assertEqual({a["from"] for a in m["text_decensor"]["applied"]}, {"突起", "尻肉"})
        self.assertIn((s["id"], m["id"], "text"), server._decensor_undo)

    def test_reply_body_off_by_default(self):
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        self._drop("r_reply.json", {"session": s["id"], "reply_to": uid, "text": "突起を摘む。"})
        self.assertTrue(server.process_outbox())
        m = server.load(s["id"])["messages"][-1]
        self.assertEqual(m["text"], "突起を摘む。")
        self.assertNotIn("text_decensor", m)

    def test_reply_body_can_be_disabled(self):
        s = self._pending_session(decensor_reply=False)
        uid = s["messages"][-1]["id"]
        self._drop("r_reply.json", {"session": s["id"], "reply_to": uid, "text": "突起を摘む。"})
        self.assertTrue(server.process_outbox())
        m = server.load(s["id"])["messages"][-1]
        self.assertEqual(m["text"], "突起を摘む。")
        self.assertNotIn("text_decensor", m)
        self.assertNotIn((s["id"], m["id"], "text"), server._decensor_undo)

    def test_reply_body_exception_resilience(self):
        # 復元処理で予期せぬ例外が発生しても、本文取り込みが完了し outbox ファイルが DONE に移動すること
        s = self._pending_session(decensor_reply=True)
        uid = s["messages"][-1]["id"]
        self._drop("r_reply.json", {"session": s["id"], "reply_to": uid, "text": "突起を摘む。"})
        with unittest.mock.patch("server._decensor_apply", side_effect=RuntimeError("simulated error")):
            self.assertTrue(server.process_outbox())
        m = server.load(s["id"])["messages"][-1]
        self.assertEqual(m["text"], "突起を摘む。")      # 元の文面のまま保存される
        self.assertNotIn("text_decensor", m)
        self.assertEqual(len(os.listdir(server.OUTBOX)), 0)  # outbox に残留しない


class TestDecensorApi(unittest.TestCase):
    """「戻す」は save() で rev を進める（画面が取り直す）ことまで確認する"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_decensor_api_")
        self.orig = {k: getattr(server, k) for k in (
            "DATA", "SESS_DIR", "CHOICES_DIR", "OUTBOX", "DONE", "AGENTS_LOG", "NEW_IDE_DIR", "IDE_KNOWN")}
        self.orig_known = server.known_ides
        server.DATA = os.path.join(self.tmp, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        server.OUTBOX = os.path.join(self.tmp, "outbox")
        server.DONE = os.path.join(server.OUTBOX, "done")
        for d in (server.SESS_DIR, server.CHOICES_DIR, server.DONE):
            os.makedirs(d, exist_ok=True)
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        server.NEW_IDE_DIR = os.path.join(self.tmp, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.IDE_KNOWN = os.path.join(server.DATA, "ide_known.json")
        server.known_ides = None
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        server._decensor_undo.clear()
        write_dict_file()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.th.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.th.join(timeout=2)
        for k, v in self.orig.items():
            setattr(server, k, v)
        server.known_ides = self.orig_known
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        server._decensor_undo.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def test_dictionary_api_roundtrip_and_cache_refresh(self):
        code, r = self.get("/api/decensor")
        self.assertEqual(code, 200)
        self.assertIn("後ろの穴=ケツマンコ", r["text"])
        code, r = self.post("/api/decensor", {"text": "秘所=おまんこ\n膣＝ナカ\n__JP_AN__=x"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 2)
        # 保存直後（同じ秒）でも新しい辞書が使われる
        out, _ = server.apply_choice_decensor([{"label": "x", "text": "秘所と膣"}])
        self.assertEqual(out[0]["text"], "おまんことナカ")
        code, r = self.get("/api/decensor")
        self.assertEqual(r["text"], "秘所=おまんこ\n膣=ナカ")

    def test_seed_text_for_reset(self):
        # 「初期セットに戻す」用のスナップショットは返すが、変換には使わない
        os.remove(server.decensor_path())
        server._DECENSOR_CACHE.update(key=None, words={}, compiled=None)
        code, r = self.get("/api/decensor")
        self.assertEqual(code, 200)
        self.assertEqual(r["text"], "")          # ファイルが無ければ辞書は空
        self.assertFalse(r["file_exists"])
        self.assertIn("後ろの穴=ケツマンコ", r["seed_text"])
        self.assertIn("秘部=おまんこ", r["seed_text"])
        out, applied = server.apply_choice_decensor([{"label": "x", "text": "後ろの穴"}])
        self.assertEqual(out[0]["text"], "後ろの穴")   # 初期セットは効かない
        self.assertEqual(applied, [])

    def make_message(self, slot="erotic"):
        s = server.create_session({"title": "api"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "本文", "ts": server.now()})
        server.save(s)
        server._rescue_into_message(s["id"], mid, slot, "t", {"choices": [
            {"label": "後ろの穴", "text": "秘所を責める"},
        ]})
        return s["id"], mid

    def test_undo_restores_and_bumps_rev(self):
        sid, mid = self.make_message()
        code, r = self.post("/api/message/undo-decensor", {"sid": sid, "id": mid})
        self.assertEqual(code, 200)
        self.assertTrue(r.get("ok"))
        s = server.load(sid)
        self.assertEqual(server.find_msg(s, mid)["choices_erotic"][0]["label"], "後ろの穴")
        self.assertIsNone(server.find_msg(s, mid).get("choices_decensor"))
        code, r = self.post("/api/message/undo-decensor", {"sid": sid, "id": mid})
        self.assertEqual(code, 404)  # 2回目は戻せない

    def test_undo_refuses_when_choices_updated(self):
        sid, mid = self.make_message()
        s = server.load(sid)
        server.find_msg(s, mid)["choices_erotic_updated_ts"] = "2099-01-01T00:00:00"
        server.save(s)
        code, r = self.post("/api/message/undo-decensor", {"sid": sid, "id": mid})
        self.assertEqual(code, 409)

    def test_api_with_context_rules(self):
        code, r = self.post("/api/decensor", {
            "text": "突起|股,割れ目=クリトリス\n突起=乳首\n突起|=不正\n胸=突起"
        })
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 3)  # 突起|股,割れ目, 突起, 胸
        self.assertTrue(any("手がかりが空です" in w for w in r["warnings"]))
        self.assertTrue(any("連鎖します" in w for w in r["warnings"]))
        out, applied = server.apply_choice_decensor([
            {"label": "股の突起", "text": "胸と突起"}
        ])
        self.assertEqual(out[0]["label"], "股のクリトリス")
        self.assertEqual(out[0]["text"], "突起と乳首")  # 胸→突起 (1passなので連鎖せず突起のまま), 突起→乳首
        code, r = self.get("/api/decensor")
        self.assertEqual(code, 200)
        self.assertIn("突起|股,割れ目=クリトリス", r["text"])
        self.assertIn("突起=乳首", r["text"])
        self.assertIn("胸=突起", r["text"])

    def test_undo_text(self):
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "乳首を摘む", "ts": server.now(),
                              "text_decensor": {"applied": [{"from": "突起", "to": "乳首", "count": 1}],
                                                "ts": server.now()}})
        server.save(s)
        server.remember_text_undo(s["id"], mid, "突起を摘む", "乳首を摘む")
        code, r = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid, "kind": "text"})
        self.assertEqual(code, 200)
        m = server.find_msg(server.load(s["id"]), mid)
        self.assertEqual(m["text"], "突起を摘む")
        self.assertNotIn("text_decensor", m)

    def test_undo_text_refuses_after_edit(self):
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "乳首を摘む", "ts": server.now()})
        server.save(s)
        server.remember_text_undo(s["id"], mid, "突起を摘む", "乳首を摘む")
        s2 = server.load(s["id"])
        server.find_msg(s2, mid)["text"] = "手で直した"
        server.save(s2)
        code, r = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid, "kind": "text"})
        self.assertEqual(code, 409)

    def test_undo_text_and_choices_coexist(self):
        # 同じメッセージに本文と候補の記録が両方あっても、片方の undo がもう片方を壊さない
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "乳首", "ts": server.now(),
                              "choices": [{"label": "ケツマンコ", "text": "乳首"}],
                              "choices_updated_ts": "2099-01-01T00:00:00"})
        server.save(s)
        server.remember_text_undo(s["id"], mid, "突起", "乳首")
        server.remember_decensor_undo(s["id"], mid, "normal",
                                      {"applied": [{"from": "後ろの穴", "to": "ケツマンコ", "count": 1}],
                                       "before": [{"label": "後ろの穴", "text": "乳首"}]},
                                      "2099-01-01T00:00:00")
        # 本文の undo → 本文だけ戻る
        code, _ = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid, "kind": "text"})
        self.assertEqual(code, 200)
        m = server.find_msg(server.load(s["id"]), mid)
        self.assertEqual(m["text"], "突起")
        self.assertEqual(m["choices"][0]["label"], "ケツマンコ")
        # 候補の undo はまだ残っている → kind 無し（既定）で戻せる
        code, _ = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid})
        self.assertEqual(code, 200)
        m = server.find_msg(server.load(s["id"]), mid)
        self.assertEqual(m["choices"][0]["label"], "後ろの穴")

    def test_alter_clears_text_decensor_and_undo(self):
        # 手動編集（alter）が行われたとき、text_decensor と undo 枠が即座に解放される
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "乳首を摘む", "ts": server.now(),
                              "text_decensor": {"applied": [{"from": "突起", "to": "乳首", "count": 1}],
                                                "ts": server.now()}})
        server.save(s)
        server.remember_text_undo(s["id"], mid, "突起を摘む", "乳首を摘む")
        self.assertIn((s["id"], mid, "text"), server._decensor_undo)
        code, r = self.post("/api/message/alter", {"sid": s["id"], "id": mid, "text": "手動編集した本文"})
        self.assertEqual(code, 200)
        m = server.find_msg(server.load(s["id"]), mid)
        self.assertEqual(m["text"], "手動編集した本文")
        self.assertNotIn("text_decensor", m)
        self.assertNotIn((s["id"], mid, "text"), server._decensor_undo)

    def test_undo_text_api_edge_cases(self):
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({"id": mid, "role": "assistant", "text": "乳首を摘む", "ts": server.now(),
                              "text_decensor": {"applied": [{"from": "突起", "to": "乳首", "count": 1}],
                                                "ts": server.now()}})
        server.save(s)
        server.remember_text_undo(s["id"], mid, "突起を摘む", "乳首を摘む")
        # 1回目は成功
        code, _ = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid, "kind": "text"})
        self.assertEqual(code, 200)
        # 2回目は404（1回限り）
        code, r = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid, "kind": "text"})
        self.assertEqual(code, 404)
        self.assertEqual(r.get("error"), "戻せる置換がありません")

        # 存在しない mid
        code, r = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": "unknown_mid", "kind": "text"})
        self.assertEqual(code, 404)

        # ユーザーメッセージに対しては404
        u_mid = server.new_id()
        s["messages"].append({"id": u_mid, "role": "user", "text": "こんにちは", "ts": server.now()})
        server.save(s)
        server.remember_text_undo(s["id"], u_mid, "こんにちは", "変更")
        code, r = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": u_mid, "kind": "text"})
        self.assertEqual(code, 404)
        self.assertEqual(r.get("error"), "no such assistant message")

    def test_alter_preserves_choices_undo(self):
        s = server.create_session({"title": "t"})
        mid = server.new_id()
        s["messages"].append({
            "id": mid, "role": "assistant", "text": "乳首を摘む", "ts": server.now(),
            "choices": [{"label": "ケツマンコ", "text": "候補本文"}],
            "choices_updated_ts": "2099-01-01T00:00:00",
            "text_decensor": {"applied": [{"from": "突起", "to": "乳首", "count": 1}], "ts": server.now()},
            "choices_decensor": {"slot": "normal", "applied": [{"from": "後ろの穴", "to": "ケツマンコ", "count": 1}], "ts": server.now(), "target_ts": "2099-01-01T00:00:00"}
        })
        server.save(s)
        server.remember_text_undo(s["id"], mid, "突起を摘む", "乳首を摘む")
        server.remember_decensor_undo(s["id"], mid, "normal",
                                      {"applied": [{"from": "後ろの穴", "to": "ケツマンコ", "count": 1}],
                                       "before": [{"label": "後ろの穴", "text": "候補本文"}]},
                                      "2099-01-01T00:00:00")
        # 本文を手動編集 (alter)
        code, r = self.post("/api/message/alter", {"sid": s["id"], "id": mid, "text": "手動編集後本文"})
        self.assertEqual(code, 200)
        # 本文の undo 枠は消えている
        self.assertNotIn((s["id"], mid, "text"), server._decensor_undo)
        # 候補の undo 枠は無傷で残っている
        self.assertIn((s["id"], mid, "choices"), server._decensor_undo)
        # 候補の undo を実行 -> 正常に戻る
        code, r = self.post("/api/message/undo-decensor", {"sid": s["id"], "id": mid})
        self.assertEqual(code, 200)
        m = server.find_msg(server.load(s["id"]), mid)
        self.assertEqual(m["choices"][0]["label"], "後ろの穴")
        self.assertEqual(m["text"], "手動編集後本文")


if __name__ == "__main__":
    unittest.main()
