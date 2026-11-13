# -*- coding: utf-8 -*-
"""ターン終了マークと「outbox へ1回だけ書く」指示の回帰テスト

半角スペースだけを返事にすると Gemini が「空の出力」として拒否し、
同じ本文の再生成（二重 write_file）や余分なモデル呼び出しを招く。
そのためターン終了の返事を見える短いマークに変え、各プロンプトに
「書き込みは1回だけ」を明記した。ここではその指示が残っているかを確かめる。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import server
import wait_input

MARK = "（待機）"


class TestTurnEndMarker(unittest.TestCase):
    def test_marker_constants(self):
        self.assertEqual(server.TURN_END_MARK, MARK)
        self.assertEqual(wait_input.TURN_END_MARK, MARK)

    def test_main_agents_text(self):
        s = {"id": "t1", "setting": {"title": "テスト", "ide_session": "ide-1"}}
        txt = server.agents_text(s)
        self.assertIn(MARK, txt)
        self.assertIn("書き込みは1回だけ", txt)
        self.assertNotIn("返事は半角スペース1文字", txt)

    def test_output_text_write_once_and_stable_filename(self):
        st = server.merge_defaults({})
        txt = server.output_text(st)
        self.assertIn("書き込みは1回だけ", txt)
        # reply_to ベースの安定したファイル名（古い epoch を再利用しない）
        self.assertIn("_reply_<reply_to>.json", txt)

    def test_choices_output_text_write_once_and_stable_filename(self):
        st = server.merge_defaults({})
        s = {"id": "t1", "setting": st}
        user_m = {"id": "u1", "text": "こんにちは"}
        for erotic in (False, True):
            txt = server.parallel_choices_output_text(s, user_m, erotic=erotic)
            self.assertIn("1回だけ", txt)
            self.assertIn("<reply_to>.json", txt)

    def test_choices_rules_use_marker(self):
        path = os.path.join(ROOT, "rules", "choices-filter-avoidance.md")
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn(MARK, txt)
        self.assertNotIn("返事は**半角スペース1文字**", txt)

    def test_wait_input_prints_write_once(self):
        path = os.path.join(ROOT, "wait_input.py")
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("TURN_END_MARK", txt)
        self.assertIn("書き込みは1回だけ", txt)


if __name__ == "__main__":
    unittest.main()
