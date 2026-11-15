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
        self.orig_env_file = google_ai.ENV_FILE
        self.orig_key_file = google_ai.KEY_FILE
        self.orig_gemini_key = os.environ.get("GEMINI_API_KEY")
        self.orig_google_key = os.environ.get("GOOGLE_API_KEY")
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

        self.orig_srv_dirs = {
            "DATA": server.DATA,
            "SESS_DIR": server.SESS_DIR,
            "OUTBOX": server.OUTBOX,
            "DONE": server.DONE,
            "TRASH": server.TRASH,
        }

        test_data = os.path.join(self.tmp, "data")
        server.DATA = test_data
        server.SESS_DIR = os.path.join(test_data, "sessions")
        server.OUTBOX = os.path.join(self.tmp, "outbox")
        server.DONE = os.path.join(server.OUTBOX, "done")
        server.TRASH = os.path.join(test_data, "trash")

        for d in (test_data, server.SESS_DIR, server.OUTBOX, server.DONE, server.TRASH):
            os.makedirs(d, exist_ok=True)

        google_ai.DATA = self.tmp
        google_ai.ENV_FILE = os.path.join(self.tmp, ".env")
        google_ai.KEY_FILE = google_ai.ENV_FILE
        google_ai.invalidate_models_cache()

    def tearDown(self):
        google_ai.DATA = self.orig_data
        google_ai.ENV_FILE = self.orig_env_file
        google_ai.KEY_FILE = self.orig_key_file
        for k, v in self.orig_srv_dirs.items():
            setattr(server, k, v)
        if self.orig_gemini_key is not None:
            os.environ["GEMINI_API_KEY"] = self.orig_gemini_key
        else:
            os.environ.pop("GEMINI_API_KEY", None)
        if self.orig_google_key is not None:
            os.environ["GOOGLE_API_KEY"] = self.orig_google_key
        else:
            os.environ.pop("GOOGLE_API_KEY", None)
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
        self.assertEqual(src, ".env")
        self.assertEqual(google_ai.key_hint(), "7890")
        with open(google_ai.ENV_FILE, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn(f"GEMINI_API_KEY={test_key}", content)

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
        self.assertNotIn("GEMINI_API_KEY", os.environ)

    def test_env_preserves_other_variables(self):
        # 既存の環境変数やコメント行がある .env
        initial_content = (
            "# Existing config\n"
            "RPCHAT_PORT=8200\n"
            "CUSTOM_VAR=\"hello_world\"\n"
        )
        with open(google_ai.ENV_FILE, "w", encoding="utf-8") as f:
            f.write(initial_content)

        test_key = "AIzaSyFakeKey9876543210"
        google_ai.save_key(test_key)

        # 保存後、他の変数が維持されていること
        env_dict = google_ai.read_env_file()
        self.assertEqual(env_dict.get("RPCHAT_PORT"), "8200")
        self.assertEqual(env_dict.get("CUSTOM_VAR"), "hello_world")
        self.assertEqual(env_dict.get("GEMINI_API_KEY"), test_key)

        with open(google_ai.ENV_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
            self.assertIn("# Existing config", raw)
            self.assertIn("RPCHAT_PORT=8200", raw)

        # 削除後も他の変数が維持されていること
        google_ai.delete_key()
        env_dict = google_ai.read_env_file()
        self.assertEqual(env_dict.get("RPCHAT_PORT"), "8200")
        self.assertEqual(env_dict.get("CUSTOM_VAR"), "hello_world")
        self.assertNotIn("GEMINI_API_KEY", env_dict)

    def test_load_key_fallback_to_os_environ(self):
        # .env が空でも os.environ にあれば拾うこと
        if os.path.exists(google_ai.ENV_FILE):
            os.remove(google_ai.ENV_FILE)
        os.environ["GEMINI_API_KEY"] = "AIzaSyEnvKey12345678"
        key, src = google_ai.load_key()
        self.assertEqual(key, "AIzaSyEnvKey12345678")
        self.assertEqual(src, "env")

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
        self.orig_env_file = google_ai.ENV_FILE
        self.orig_key_file = google_ai.KEY_FILE
        self.orig_gemini_key = os.environ.get("GEMINI_API_KEY")
        self.orig_google_key = os.environ.get("GOOGLE_API_KEY")
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "IMAGES_DIR", "CHOICES_DIR", "RAG_DIR", "SENT", "CTX", "TRASH", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)

        google_ai.DATA = self.tmp
        google_ai.ENV_FILE = os.path.join(self.tmp, ".env")
        google_ai.KEY_FILE = google_ai.ENV_FILE
        google_ai.invalidate_models_cache()

    def tearDown(self):
        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        google_ai.DATA = self.orig_data
        google_ai.ENV_FILE = self.orig_env_file
        google_ai.KEY_FILE = self.orig_key_file
        if self.orig_gemini_key is not None:
            os.environ["GEMINI_API_KEY"] = self.orig_gemini_key
        else:
            os.environ.pop("GEMINI_API_KEY", None)
        if self.orig_google_key is not None:
            os.environ["GOOGLE_API_KEY"] = self.orig_google_key
        else:
            os.environ.pop("GOOGLE_API_KEY", None)
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
