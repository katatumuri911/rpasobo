# -*- coding: utf-8 -*-
import copy
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import google_ai
import server


class TestBodyPHUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = server.DATA
        server.DATA = self.tmp
        server.invalidate_body_ph_cache()

    def tearDown(self):
        server.DATA = self.orig_data
        server.invalidate_body_ph_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_1_roundtrip_equality(self):
        """1. ph_apply -> ph_restore で元の文字列に完全一致（往復）"""
        orig = "アナルに肉棒を挿入して中出しし、精液を注ぐ。オマンコも愛液で濡れて絶頂する。"
        applied, pairs = server.ph_apply(orig)
        self.assertNotIn("アナル", applied)
        self.assertNotIn("肉棒", applied)
        self.assertNotIn("中出し", applied)
        self.assertNotIn("精液", applied)
        self.assertNotIn("オマンコ", applied)
        self.assertNotIn("愛液", applied)
        # 濡れ and 絶頂 are in keep, so they remain
        self.assertIn("濡れ", applied)
        self.assertIn("絶頂", applied)

        restored, count, left = server.ph_restore(applied)
        self.assertEqual(restored, orig)
        self.assertEqual(left, 0)
        self.assertGreaterEqual(count, 6)

    def test_2_notation_variations(self):
        """2. 表記ゆれ復元（_JP_AN_ / ＿＿JP_AN＿＿ / __jp_an__ / __JP_AN）"""
        text = "_JP_AN_ と ＿＿JP_AN＿＿ と __jp_an__ と __JP_AN を弄る"
        restored, count, left = server.ph_restore(text)
        self.assertEqual(count, 4)
        self.assertEqual(left, 0)
        self.assertEqual(restored, "アナル と アナル と アナル と アナル を弄る")

    def test_3_idempotent_restore(self):
        """3. 二重複元しない（復元済み文字列をもう一度通しても変化なし）"""
        orig = "彼の__JP_PN__が私の__JP_CS__に入った"
        restored1, count1, left1 = server.ph_restore(orig)
        self.assertEqual(restored1, "彼のペニスが私の膣に入った")
        self.assertEqual(count1, 2)
        self.assertEqual(left1, 0)

        restored2, count2, left2 = server.ph_restore(restored1)
        self.assertEqual(restored2, restored1)
        self.assertEqual(count2, 0)
        self.assertEqual(left2, 0)

    def test_4_max_per_prompt_limit(self):
        """4. 上限 max_per_prompt で打ち切り、skipped が返る"""
        custom_cfg = copy.deepcopy(server.DEFAULT_BODY_PH)
        custom_cfg["max_per_prompt"] = 3
        with open(os.path.join(self.tmp, "body_ph.json"), "w", encoding="utf-8") as f:
            json.dump(custom_cfg, f, ensure_ascii=False)
        server.invalidate_body_ph_cache()

        text = "アナル アナル アナル アナル アナル"  # 5 occurrences
        applied, pairs, skipped = server.ph_apply(text, return_skipped=True)
        self.assertEqual(skipped, 2)
        self.assertEqual(applied.count("__JP_AN__"), 3)
        self.assertEqual(applied.count("アナル"), 2)

    def test_5_keep_words_protected(self):
        """5. keep 語は置換されない（絶頂 / 生徒会 / 制服）"""
        text = "絶頂に達し、生徒会室で制服を着た。"
        applied, pairs = server.ph_apply(text)
        self.assertIn("絶頂", applied)
        self.assertIn("生徒会", applied)
        self.assertIn("制服", applied)
        self.assertNotIn("__JP_ZO__", applied)
        self.assertEqual(pairs, [])

    def test_6_longest_word_priority(self):
        """6. 長い語優先（オマンコ が __JP_MK__ にならない、生徒会 が壊れない）"""
        text = "オマンコとマンコ。生徒会室で生徒と会う。"
        applied, pairs = server.ph_apply(text)
        self.assertIn("__JP_PS__", applied)
        self.assertIn("__JP_MK__", applied)
        self.assertIn("生徒会室で", applied)
        self.assertIn("所属員と会う", applied)
        self.assertNotIn("所属員会", applied)

    def test_7_attribute_euphemism_and_untranslatable_symbol(self):
        """7. 属性語は婉曲語になる（少女→若い女性）、訳せない語は記号（幼女→__MINOR__）"""
        text = "少女と幼女とロリとショタ"
        applied, pairs = server.ph_apply(text)
        self.assertIn("若い女性", applied)
        self.assertIn("__MINOR__", applied)
        self.assertIn("__LOLI__", applied)
        self.assertIn("__SHOTA__", applied)
        self.assertNotIn("少女", applied)
        self.assertNotIn("幼女", applied)
        # pairs only contains symbol replacements (act), not euphemisms
        pair_dict = dict(pairs)
        self.assertIn("幼女", pair_dict)
        self.assertEqual(pair_dict["幼女"], "__MINOR__")
        self.assertNotIn("少女", pair_dict)

        # Restore restores __MINOR__ back to 幼女
        restored, count, left = server.ph_restore(applied)
        self.assertIn("幼女", restored)
        self.assertIn("ロリ", restored)
        self.assertIn("ショタ", restored)
        # 少女 remains 若い女性 when euph_restore is False (default)
        self.assertIn("若い女性", restored)

    def test_8_corrupted_json_fallback(self):
        """8. 辞書ファイル破損時にデフォルトで動く（例外を投げない）"""
        with open(os.path.join(self.tmp, "body_ph.json"), "w", encoding="utf-8") as f:
            f.write("{invalid json syntax !!!")
        server.invalidate_body_ph_cache()

        cfg = server.ph_settings()
        self.assertTrue(cfg.get("enabled"))
        self.assertIn("アナル", cfg.get("act", {}))

        applied, pairs = server.ph_apply("アナル")
        self.assertEqual(applied, "__JP_AN__")
        self.assertEqual(pairs, [("アナル", "__JP_AN__")])

        restored, count, left = server.ph_restore(applied)
        self.assertEqual(restored, "アナル")

    def test_preexisting_placeholder_neutralization(self):
        """ユーザー入力に既存のプレースホルダがあってもアナル等に誤復元されない"""
        user_text = "ユーザー発言: __JP_AN__ という記号をそのまま使う"
        applied, pairs = server.ph_apply(user_text)
        # In applied text, it was neutralized so it won't be replaced
        self.assertEqual(pairs, [])
        # When restored, it unneutralizes back to __JP_AN__ (not アナル)
        restored, count, left = server.ph_restore(applied)
        self.assertEqual(count, 0)
        self.assertEqual(left, 0)
        self.assertEqual(restored, user_text)

    def test_ph_misses_logging(self):
        """未知のプレースホルダは ph_misses.log に記号名のみ記録され、本文は書かれない"""
        text = "生成結果に未知の __JP_UNKNOWN__ が出た"
        restored, count, left = server.ph_restore(text)
        self.assertEqual(left, 1)
        self.assertIn("__JP_UNKNOWN__", restored)

        server.ph_log("user", [], count, left, length=len(restored), raw_len_before=len(text), text=restored)

        misses_path = os.path.join(self.tmp, "ph_misses.log")
        self.assertTrue(os.path.exists(misses_path))
        with open(misses_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("__JP_UNKNOWN__", content)
        self.assertNotIn("生成結果に未知の", content)

        # Same token on same day is not duplicated
        server.ph_log("user", [], count, left, length=len(restored), raw_len_before=len(text), text=restored)
        with open(misses_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len([l for l in lines if "__JP_UNKNOWN__" in l]), 1)

    def test_euph_restore_option(self):
        """euph_restore: true のときは婉曲語も元の語に戻る"""
        custom_cfg = copy.deepcopy(server.DEFAULT_BODY_PH)
        custom_cfg["euph_restore"] = True
        with open(os.path.join(self.tmp, "body_ph.json"), "w", encoding="utf-8") as f:
            json.dump(custom_cfg, f, ensure_ascii=False)
        server.invalidate_body_ph_cache()

        applied, pairs = server.ph_apply("少女")
        self.assertEqual(applied, "若い女性")

        restored, count, left = server.ph_restore(applied)
        self.assertEqual(restored, "少女")


class TestBodyPHIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "IMAGES_DIR", "CHOICES_DIR", "RAG_DIR", "SENT", "CTX", "TRASH", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)

        self.orig_google_data = google_ai.DATA
        google_ai.DATA = self.tmp
        server.invalidate_body_ph_cache()

    def tearDown(self):
        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        google_ai.DATA = self.orig_google_data
        server.invalidate_body_ph_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_9_run_google_task_applies_ph_and_rule_text(self):
        """9. run_google_task を通すと、google_ai.generate に渡る prompt が変換済み＋ルール行付き"""
        sid = "test_ph_prompt_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "PH Test",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "google_body_ph": True,
                "character": {"name": "ユウカ"},
            }),
            "messages": [
                {"id": "u1", "role": "user", "text": "ユウカのアナルに中出しする。", "status": "pending"}
            ]
        }
        server.save(s)

        captured_prompt = []

        def mock_generate(model, prompt, system=None, **kwargs):
            captured_prompt.append(prompt)
            return {
                "text": json.dumps({"text": "はい、わかりました。"}, ensure_ascii=False),
                "meta": {"finish_reason": "STOP", "usage": {}},
                "elapsed_ms": 100,
                "error": None,
            }

        with patch("google_ai.generate", side_effect=mock_generate):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

        self.assertEqual(len(captured_prompt), 1)
        p = captured_prompt[0]
        # In prompt, アナル and 中出し should be placeholderized
        self.assertIn("__JP_AN__", p)
        self.assertIn("__JP_ND__", p)
        self.assertIn(server.PH_RULE_HEADER, p)
        self.assertIn("アナル=__JP_AN__", p)
        self.assertIn("中出し=__JP_ND__", p)

        server.delete_session_all(sid)

    def test_10_response_json_fields_restored_in_outbox(self):
        """10. 応答JSON全体（text / state / inner / memories）が復元されて outbox に書かれる"""
        sid = "test_ph_restore_outbox_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "PH Outbox Test",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "google_body_ph": True,
                "character": {"name": "ユウカ"},
            }),
            "messages": [
                {"id": "u1", "role": "user", "text": "アナル責め", "status": "pending"}
            ]
        }
        server.save(s)

        mock_reply_json = {
            "text": "ユウカの__JP_AN__に触れると、声が漏れた。",
            "state": "__JP_AN__が赤くヒクついている",
            "inner": "私の__JP_AN__が……恥ずかしい",
            "memories": ["__JP_AN__を責められた記憶"]
        }

        fake_res = {
            "text": json.dumps(mock_reply_json, ensure_ascii=False),
            "meta": {"finish_reason": "STOP", "usage": {}},
            "elapsed_ms": 100,
            "error": None,
        }

        with patch("google_ai.generate", return_value=fake_res):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

        outbox_files = [f for f in os.listdir(server.OUTBOX) if f.startswith(f"{sid}_google_user_u1_")]
        self.assertTrue(len(outbox_files) > 0)
        with open(os.path.join(server.OUTBOX, outbox_files[0]), encoding="utf-8") as f:
            saved_rep = json.load(f)

        self.assertIn("ユウカのアナルに触れると", saved_rep.get("text", ""))
        self.assertIn("アナルが赤くヒクついている", saved_rep.get("state", ""))
        self.assertIn("私のアナルが……", saved_rep.get("inner", ""))
        self.assertIn("アナルを責められた記憶", saved_rep.get("memories", [""])[0] if saved_rep.get("memories") else "")
        self.assertNotIn("__JP_AN__", json.dumps(saved_rep, ensure_ascii=False))

        server.delete_session_all(sid)

    def test_11_disabled_setting_byte_identical_prompt(self):
        """11. 設定OFFで prompt がバイト同一（変換もルール行も無し）"""
        sid = "test_ph_off_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "PH Off Test",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "google_body_ph": False,
                "character": {"name": "ユウカ"},
            }),
            "messages": [
                {"id": "u1", "role": "user", "text": "ユウカのアナルを弄る", "status": "pending"}
            ]
        }
        server.save(s)

        expected_prompt, _, _, _ = server._google_build_prompt(s, s["messages"][0], "user")
        captured_prompt = []

        def mock_generate(model, prompt, system=None, **kwargs):
            captured_prompt.append(prompt)
            return {
                "text": json.dumps({"text": "はい"}, ensure_ascii=False),
                "meta": {}, "elapsed_ms": 50, "error": None
            }

        with patch("google_ai.generate", side_effect=mock_generate):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

        self.assertEqual(len(captured_prompt), 1)
        self.assertEqual(captured_prompt[0], expected_prompt)
        self.assertNotIn(server.PH_RULE_HEADER, captured_prompt[0])

        server.delete_session_all(sid)

    def test_12_non_user_tasks_not_transformed(self):
        """12. task_type が choices / image_refine のときは変換されない"""
        sid = "test_ph_non_user_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "PH Non User Test",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "google_body_ph": True,
                "character": {"name": "ユウカ"},
            }),
            "messages": [
                {"id": "u1", "role": "user", "text": "アナル", "status": "done"},
                {"id": "a1", "role": "assistant", "text": "アナル", "choices_status": "pending"}
            ]
        }
        server.save(s)

        captured_prompts = []

        def mock_generate(model, prompt, system=None, **kwargs):
            captured_prompts.append(prompt)
            return {
                "text": json.dumps({"choices": [{"label": "A", "text": "B"}]}, ensure_ascii=False),
                "meta": {}, "elapsed_ms": 50, "error": None
            }

        with patch("google_ai.generate", side_effect=mock_generate):
            key = server._google_task_key(sid, "choices", s["messages"][1])
            server.run_google_task(sid, "choices", "a1", key)

        self.assertEqual(len(captured_prompts), 1)
        self.assertNotIn(server.PH_RULE_HEADER, captured_prompts[0])

        server.delete_session_all(sid)

    def test_13_restored_text_passed_to_decensor(self):
        """13. 復元後の text が decensor に渡る（_decensor_apply の入力を検証）"""
        sid = "test_ph_decensor_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "Decensor Test",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "google_body_ph": True,
                "decensor_reply": True,
                "character": {"name": "ユウカ"},
            }),
            "messages": [
                {"id": "u1", "role": "user", "text": "会話", "status": "pending"}
            ]
        }
        server.save(s)

        fake_res = {
            "text": json.dumps({"text": "彼女の__JP_AN__を撫でる。"}, ensure_ascii=False),
            "meta": {}, "elapsed_ms": 50, "error": None
        }

        with patch("google_ai.generate", return_value=fake_res):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

        # Import outbox
        with patch.object(server, "_decensor_apply", wraps=server._decensor_apply) as spy_decensor:
            server.process_outbox()
            self.assertTrue(spy_decensor.called)
            # The input argument to _decensor_apply must contain アナル (not __JP_AN__)
            first_arg = spy_decensor.call_args[0][0]
            self.assertIn("アナル", first_arg)
            self.assertNotIn("__JP_AN__", first_arg)

        server.delete_session_all(sid)

    def test_14_fetch_cache_on_and_off(self):
        """14. 署名キャッシュON/OFFどちらでも変換後のプロンプトが送られる"""
        for cache_flag in (True, False):
            sid = f"test_ph_cache_{cache_flag}_sid"
            s = {
                "id": sid,
                "setting": server.merge_defaults({
                    "title": "Cache Test",
                    "ai_provider": "google",
                    "google_model": "gemini-2.5-flash",
                    "google_body_ph": True,
                    "google_fetch_cache": cache_flag,
                    "character": {"name": "ユウカ"},
                }),
                "messages": [
                    {"id": "u1", "role": "user", "text": "アナル", "status": "pending"}
                ]
            }
            server.save(s)

            captured = []

            def mock_generate(model, prompt, system=None, **kwargs):
                captured.append(prompt)
                return {
                    "text": json.dumps({"text": "返事"}, ensure_ascii=False),
                    "meta": {}, "elapsed_ms": 50, "error": None
                }

            with patch("google_ai.generate", side_effect=mock_generate):
                key = server._google_task_key(sid, "user", s["messages"][0])
                server.run_google_task(sid, "user", "u1", key)

            self.assertEqual(len(captured), 1)
            self.assertIn("__JP_AN__", captured[0])
            self.assertIn(server.PH_RULE_HEADER, captured[0])

            server.delete_session_all(sid)

    def test_15_ascii_word_boundaries_protect_json(self):
        """15. JSONやJavaScript等の英単語内にJSやJKが含まれても誤置換されない（__JS__ON化の防止）"""
        text = '必ずJSONオブジェクトを返してください。JavaScriptは不要。JKとJSは置換対象。'
        applied, pairs = server.ph_apply(text)
        self.assertIn("JSON", applied)
        self.assertNotIn("__JS__ON", applied)
        self.assertIn("JavaScript", applied)
        self.assertIn("__JK__", applied)
        self.assertIn("__JS__", applied)
        pair_dict = dict(pairs)
        self.assertIn("JK", pair_dict)
        self.assertIn("JS", pair_dict)

        restored, count, left = server.ph_restore(applied)
        self.assertEqual(restored, text)
        self.assertEqual(count, 2)
        self.assertEqual(left, 0)

    def test_16_notation_variations_detected_as_misses(self):
        """16. 未知記号の小文字・全角・右側アンダースコア欠けもleft=1になりph_misses.logに大文字正規化で記録"""
        cases = [
            ("彼女の __jp_unknown__ に触れた", "__JP_UNKNOWN__"),
            ("彼女の ＿＿JP_UNKNOWN＿＿ に触れた", "__JP_UNKNOWN__"),
            ("彼女の __JP_UNKNOWN に触れた", "__JP_UNKNOWN__"),
            ("彼女の _JP_UNKNOWN_ に触れた", "__JP_UNKNOWN__"),
        ]
        for text, expected_miss in cases:
            restored, count, left = server.ph_restore(text)
            self.assertEqual(left, 1, f"Failed for {text}")
            server.ph_log("user", [], count, left, length=len(restored), raw_len_before=len(text), text=restored)

        misses_path = os.path.join(self.tmp, "data", "ph_misses.log")
        if not os.path.exists(misses_path):
            misses_path = os.path.join(self.tmp, "ph_misses.log")
        self.assertTrue(os.path.exists(misses_path))
        with open(misses_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("__JP_UNKNOWN__", content)

        # Markdown italics like _world_ or code variables like _id are NOT counted as misses
        normal_text = "これは _italic_ 表現と _id です。"
        restored_n, count_n, left_n = server.ph_restore(normal_text)
        self.assertEqual(count_n, 0)
        self.assertEqual(left_n, 0)

    def test_17_ph_log_applied_counts_all_occurrences(self):
        """17. ph_log は単語の種類数ではなく置換箇所の総数（applied_count）をログに出力する"""
        text = "アナル アナル アナル"  # 1 unique word, 3 occurrences
        applied, pairs = server.ph_apply(text)
        self.assertEqual(len(pairs), 1)  # 1 unique word pair
        self.assertEqual(getattr(server.ph_apply, "last_applied_count", 0), 3)

        with patch("builtins.print") as mock_print:
            server.ph_log("user", pairs, 3, 0, length=len(text), raw_len_before=len(text))
            self.assertTrue(mock_print.called)
            log_line = mock_print.call_args[0][0]
            self.assertIn("applied=3", log_line)
            self.assertIn("restored=3", log_line)
            self.assertNotIn("applied=1", log_line)


if __name__ == "__main__":
    unittest.main()
