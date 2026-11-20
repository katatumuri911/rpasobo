# -*- coding: utf-8 -*-
"""data/fail.log（API失敗の記録）のテスト

原因不明のブロックを後から追えるようにするための観測ログ。
本文は書かない（長さだけ）。
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import google_ai
import server


class TestFailLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "IMAGES_DIR", "CHOICES_DIR",
                     "RAG_DIR", "SENT", "CTX", "TRASH", "OUTBOX", "DONE"):
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

    def _rows(self):
        path = os.path.join(server.DATA, "fail.log")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _session(self, sid):
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "fail log",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "google_body_ph": False,
                "character": {"name": "ユウカ"},
            }),
            "messages": [
                {"id": "u1", "role": "user", "text": "つづきを書いて", "status": "pending"}
            ],
        }
        server.save(s)
        return s

    def test_unit_writes_all_fields(self):
        server._log_google_fail("sid-x", "user", {
            "error": "prompt blocked: PROHIBITED_CONTENT",
            "code": "", "http_status": 400, "first_status": 400, "second_status": None,
            "fetch_called": False, "fetch_cache": "hit", "retryable": False,
            "block_reason": "PROHIBITED_CONTENT", "finish_reason": "SAFETY", "elapsed_ms": 2000,
        }, prompt_len=8800, msgs=24)
        r = self._rows()[-1]
        self.assertEqual(r["sid"], "sid-x")
        self.assertEqual(r["error"], "prompt blocked: PROHIBITED_CONTENT")
        self.assertEqual(r["block_reason"], "PROHIBITED_CONTENT")
        self.assertEqual(r["http_status"], 400)
        self.assertEqual(r["fetch_cache"], "hit")
        self.assertEqual(r["elapsed_ms"], 2000)
        self.assertEqual(r["prompt_len"], 8800)
        self.assertEqual(r["msgs"], 24)
        self.assertFalse(r["retryable"])

    def test_run_google_task_logs_block_and_marks_error(self):
        sid = "test_faillog_sid"
        s = self._session(sid)

        def mock_generate(model, prompt, system=None, **kwargs):
            return {
                "text": "",
                "meta": {"block_reason": "PROHIBITED_CONTENT", "finish_reason": "SAFETY"},
                "error": "prompt blocked: PROHIBITED_CONTENT",
                "http_status": 400, "first_status": 400, "second_status": None,
                "fetch_called": False, "fetch_cache": "hit",
                "retryable": False, "elapsed_ms": 2000,
            }

        with patch("google_ai.generate", side_effect=mock_generate):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

        rows = self._rows()
        self.assertTrue(rows, "fail.log に記録されていない")
        r = rows[-1]
        self.assertEqual(r["sid"], sid)
        self.assertEqual(r["task"], "user")
        self.assertEqual(r["block_reason"], "PROHIBITED_CONTENT")
        self.assertGreater(r["prompt_len"], 0)
        self.assertEqual(r["msgs"], 1)
        # 画面側は従来どおりエラー状態（自動再試行はしない）
        st = server.load(sid)
        self.assertEqual(st["messages"][0]["status"], "error")
        # 画面に出す文言は日本語化され、原因のコードは残る
        self.assertIn("入力がコンテンツフィルタで止められました", st["messages"][0]["error"])
        self.assertIn("PROHIBITED_CONTENT", st["messages"][0]["error"])
        self.assertNotIn("prompt blocked", st["messages"][0]["error"])
        # ログ側は英語のまま（機械可読性を優先）
        self.assertEqual(r["error"], "prompt blocked: PROHIBITED_CONTENT")
        server.delete_session_all(sid)

    def test_success_does_not_write(self):
        sid = "test_faillog_ok_sid"
        s = self._session(sid)

        def mock_generate(model, prompt, system=None, **kwargs):
            return {"text": json.dumps({"text": "本文です。"}, ensure_ascii=False),
                    "meta": {"finish_reason": "STOP"}, "error": None, "elapsed_ms": 10}

        with patch("google_ai.generate", side_effect=mock_generate):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

        self.assertEqual(self._rows(), [])
        server.delete_session_all(sid)


class TestFailText(unittest.TestCase):
    """英語のAPIエラーを画面用の日本語に直す（括弧内のコードは残す）。"""

    def test_prompt_blocked(self):
        t = server._google_fail_text("prompt blocked: PROHIBITED_CONTENT")
        self.assertEqual(
            t,
            "入力がコンテンツフィルタで止められました（PROHIBITED_CONTENT）。"
            "プロバイダを変更すると通ることがあります。")
        self.assertNotIn("数分", t)

    def test_generation_blocked(self):
        t = server._google_fail_text("generation blocked: SAFETY")
        self.assertIn("生成中にコンテンツフィルタで止められました（SAFETY）", t)
        self.assertIn("プロバイダを変更", t)

    def test_fetch_step(self):
        t = server._google_fail_text("fetch step: model did not call fetch_prompt")
        self.assertIn("1回目のAPI呼び出し", t)
        self.assertIn("プロンプトの取得手順（fetch_prompt）", t)
        self.assertIn("もう一度送ってください", t)
        self.assertNotIn("内部処理", t)

    def test_empty_response(self):
        t = server._google_fail_text("empty response (finishReason=STOP)")
        self.assertIn("本文を含まない応答", t)
        self.assertIn("finishReason=STOP", t)
        self.assertIn("再試行", t)

    def test_unknown_message_is_kept(self):
        self.assertEqual(server._google_fail_text("何かの新しいエラー"), "何かの新しいエラー")
        self.assertEqual(server._google_fail_text(""), "")
        self.assertEqual(server._google_fail_text(None), "")

    def test_filter_detection_still_works(self):
        # 日本語化してもフィルタ判定（バッジ・通知）が壊れないこと
        for raw in ("prompt blocked: PROHIBITED_CONTENT", "generation blocked: SAFETY"):
            self.assertTrue(server._is_filter_error_msg(server._google_fail_text(raw)))

    def test_not_marked_as_refusal(self):
        # 「拒否」を含めると通知の種類が refused に変わるため含めない
        for raw in ("prompt blocked: PROHIBITED_CONTENT", "generation blocked: SAFETY"):
            self.assertNotIn("拒否", server._google_fail_text(raw))
