"""
test_google_ai.py - Tests for Google AI Studio API integration and server integration.
"""
import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import google_ai
import server


class TestGoogleAIModule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = google_ai.DATA
        self.orig_key_file = google_ai.KEY_FILE
        google_ai.DATA = self.tmp
        google_ai.KEY_FILE = os.path.join(self.tmp, "google_api_key.json")
        google_ai.invalidate_models_cache()

    def tearDown(self):
        google_ai.DATA = self.orig_data
        google_ai.KEY_FILE = self.orig_key_file
        google_ai.invalidate_models_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_key_lifecycle(self):
        # 1. Initially no key
        key, src = google_ai.load_key()
        self.assertEqual(key, "")
        self.assertEqual(src, "")
        self.assertEqual(google_ai.key_hint(), "")

        # 2. Save valid key
        test_key = "AIzaSyFakeKey1234567890"
        google_ai.save_key(test_key)
        key, src = google_ai.load_key()
        self.assertEqual(key, test_key)
        self.assertEqual(src, "file")
        self.assertEqual(google_ai.key_hint(), "7890")

        # 3. Invalid key formats
        with self.assertRaises(ValueError):
            google_ai.save_key("short")
        with self.assertRaises(ValueError):
            google_ai.save_key("has space in key12345")

        # 4. Delete key
        google_ai.delete_key()
        key, src = google_ai.load_key()
        self.assertEqual(key, "")
        self.assertEqual(src, "")

    def test_extract_text_and_thought_filtering(self):
        resp = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"thought": True, "text": "This is thinking..."},
                        {"text": "{\"text\": \"こんにちは\"}"}
                    ]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}
        }
        text, meta = google_ai.extract_text(resp)
        self.assertEqual(text, "{\"text\": \"こんにちは\"}")
        self.assertEqual(meta["finish_reason"], "STOP")
        self.assertEqual(meta["usage"]["promptTokenCount"], 100)

    def test_extract_function_call(self):
        fc_resp = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [
                        {"functionCall": {"name": "fetch_prompt", "args": {}}}
                    ]
                }
            }]
        }
        content, fc = google_ai.extract_function_call(fc_resp, "fetch_prompt")
        self.assertIsNotNone(fc)
        self.assertEqual(fc["name"], "fetch_prompt")

    def test_parse_json_text(self):
        # Raw json
        obj, ok = google_ai.parse_json_text('{"text": "hello", "choices": ["a", "b"]}')
        self.assertTrue(ok)
        self.assertEqual(obj["text"], "hello")

        # Markdown fenced json
        obj, ok = google_ai.parse_json_text('```json\n{"text": "fenced"}\n```')
        self.assertTrue(ok)
        self.assertEqual(obj["text"], "fenced")

        # Text with extra chatter around json
        obj, ok = google_ai.parse_json_text('Here is the json:\n{"text": "surrounded"}\nHope this helps!')
        self.assertTrue(ok)
        self.assertEqual(obj["text"], "surrounded")

        # Non-json
        obj, ok = google_ai.parse_json_text('not a json at all')
        self.assertFalse(ok)
        self.assertIsNone(obj)


class TestGoogleServerIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = google_ai.DATA
        self.orig_key_file = google_ai.KEY_FILE
        google_ai.DATA = self.tmp
        google_ai.KEY_FILE = os.path.join(self.tmp, "google_api_key.json")
        google_ai.invalidate_models_cache()

    def tearDown(self):
        google_ai.DATA = self.orig_data
        google_ai.KEY_FILE = self.orig_key_file
        google_ai.invalidate_models_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_task_priority(self):
        session = {
            "id": "test_sid",
            "messages": [
                {"id": "m1", "role": "assistant", "choices_status": "pending"},
                {"id": "m2", "role": "assistant", "image_refine_status": "pending"},
                {"id": "m3", "role": "user", "status": "pending"},
            ]
        }
        task_type, m = server._google_pick_task(session)
        self.assertEqual(task_type, "user")
        self.assertEqual(m["id"], "m3")

        session["messages"][2]["status"] = "done"
        task_type, m = server._google_pick_task(session)
        self.assertEqual(task_type, "image_refine")
        self.assertEqual(m["id"], "m2")

        session["messages"][1]["image_refine_status"] = "done"
        task_type, m = server._google_pick_task(session)
        self.assertEqual(task_type, "choices")
        self.assertEqual(m["id"], "m1")

    def test_google_status_tracking(self):
        sid = "sid_test_status"
        server._google_status(sid, state="requesting", task="user", model="gemini-2.5-flash")
        with server.google_inflight_guard:
            st = server.google_status.get(sid)
            self.assertIsNotNone(st)
            self.assertEqual(st["state"], "requesting")
            self.assertEqual(st["model"], "gemini-2.5-flash")

        server._google_clear_status(sid)
        with server.google_inflight_guard:
            self.assertNotIn(sid, server.google_status)

    def test_run_google_task_and_outbox_import(self):
        sid = "test_google_sid"
        s = {
            "id": sid,
            "setting": {
                "title": "Google Test Session",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "character": {"name": "TestHero"}
            },
            "messages": [
                {"id": "u1", "role": "user", "text": "こんにちは！", "status": "pending"}
            ]
        }
        server.save(s)

        fake_reply = {
            "text": json.dumps({"text": "はい、こんにちは！今日も元気ですね。"}, ensure_ascii=False),
            "meta": {"finish_reason": "STOP", "usage": {"totalTokenCount": 50}},
            "fetch_called": True,
            "elapsed_ms": 120,
            "error": None
        }

        with patch("google_ai.generate", return_value=fake_reply):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

            outbox_files = [f for f in os.listdir(server.OUTBOX) if f.startswith(f"{sid}_google_user_u1_")]
            self.assertTrue(len(outbox_files) > 0, "Outbox file was not created")

            with server.google_inflight_guard:
                self.assertEqual(server.google_status[sid]["state"], "waiting_import")

            imported = server.process_outbox()
            self.assertTrue(imported)

            updated_s = server.load(sid)
            self.assertEqual(len(updated_s["messages"]), 2)
            asst = updated_s["messages"][1]
            self.assertEqual(asst["role"], "assistant")
            self.assertIn("こんにちは", asst["text"])

            with server.google_inflight_guard:
                self.assertNotIn(sid, server.google_status)

        server.delete_session_all(sid)

    def test_run_google_choices_task(self):
        sid = "test_google_choices_sid"
        s = {
            "id": sid,
            "setting": {
                "title": "Google Choices Session",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "character": {"name": "Hero"}
            },
            "messages": [
                {"id": "u1", "role": "user", "text": "どうする？", "status": "done"},
                {"id": "a1", "role": "assistant", "text": "考え中", "choices_status": "pending"}
            ]
        }
        server.save(s)

        fake_choices = [
            {"label": "話しかける", "text": "「何かあったの？」と聞く"},
            {"label": "見守る", "text": "静かに様子を見守る"}
        ]
        fake_reply = {
            "text": json.dumps({"choices": fake_choices}, ensure_ascii=False),
            "meta": {"finish_reason": "STOP"},
            "fetch_called": False,
            "elapsed_ms": 90,
            "error": None
        }

        with patch("google_ai.generate", return_value=fake_reply):
            key = server._google_task_key(sid, "choices", s["messages"][1])
            server.run_google_task(sid, "choices", "a1", key)

            imported = server.process_outbox()
            self.assertTrue(imported)

            updated_s = server.load(sid)
            a1 = updated_s["messages"][1]
            self.assertNotIn("choices_status", a1)
            self.assertTrue(len(a1.get("choices") or []) > 0)

            with server.google_inflight_guard:
                self.assertNotIn(sid, server.google_status)

        server.delete_session_all(sid)


if __name__ == "__main__":
    unittest.main()
