"""
test_google_ai.py - Tests for Google AI Studio API integration and server integration.
"""
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

    # ---------------------------------------------------------------- 拒否文の検出

    def test_looks_like_refusal_missing_patterns(self):
        """実測で見逃していた「応じられません」系を拒否として拾える"""
        for t in (
            "申し訳ありませんが、その要求には応じられません。",
            "その内容には応じられません。",
            "このリクエストには応じることはできません。",
            "性的な描写を含むロールプレイの生成には応じることができません。",
            "そのシナリオの生成やロールプレイには参加できません。",
            "恐れ入りますが、対応いたしかねます。",
            "I'm not able to help with that.",
        ):
            self.assertTrue(server._looks_like_refusal(t), t)
        # 通常の本文・セリフは拒否扱いしない
        for t in ("「それは応じられません」と彼女は首を振った。", "了解した。行こう。"):
            self.assertFalse(server._is_refusal_body(t), t)

    def test_is_refusal_body_only_short(self):
        """拒否文は短い。長い本文中の「応じられません」は誤検知しない"""
        short = "申し訳ありませんが、その要求には応じられません。"
        self.assertTrue(server._is_refusal_body(short))
        self.assertTrue(server._is_refusal_body(
            "申し訳ありませんが、その要求には応じられません。露骨な性的な描写を含むコンテンツの生成には対応できません。"))
        long_body = ("それは応じられません、と彼女は静かに言った。" + "あ" * 400)
        self.assertFalse(server._is_refusal_body(long_body))
        self.assertFalse(server._is_refusal_body(""))
        # キャラのセリフとしての「応じられません」は拒否ではない
        self.assertFalse(server._is_refusal_body("「それは応じられません」と彼女は首を振った。"))
        self.assertFalse(server._is_refusal_body("「……うん」と小さくうなずいた。"))
        # 実測で返ってきた拒否文（「が」バージョンも拾う）
        self.assertTrue(server._is_refusal_body(
            "申し訳ありませんが、性的な描写や過激な暴力を含むロールプレイの生成には応じることができません。"))

    def test_run_google_task_rejects_refusal_in_json_text(self):
        """{\"text\": 拒否文} が本文として保存されない（実測で起きていた事故）"""
        sid = "test_google_refusal_sid"
        s = {
            "id": sid,
            "setting": {
                "title": "Google Refusal Session",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "character": {"name": "TestHero"}
            },
            "messages": [
                {"id": "u1", "role": "user", "text": "続きを書いて", "status": "pending"}
            ]
        }
        server.save(s)

        fake_reply = {
            "text": json.dumps(
                {"text": "申し訳ありませんが、その要求には応じられません。"}, ensure_ascii=False),
            "meta": {"finish_reason": "STOP"},
            "fetch_called": True,
            "elapsed_ms": 90,
            "error": None
        }

        with patch("google_ai.generate", return_value=fake_reply):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)

            # 本文として書き出されていない
            outbox_files = [f for f in os.listdir(server.OUTBOX)
                            if f.startswith(f"{sid}_google_user_u1_")]
            self.assertEqual(outbox_files, [])

            updated_s = server.load(sid)
            self.assertEqual(len(updated_s["messages"]), 1, "拒否文が本文として追加された")
            u1 = updated_s["messages"][0]
            self.assertEqual(u1.get("status"), "pending")   # 再試行に回る
            self.assertEqual(int(u1.get("error_retries") or 0), 1)
            self.assertIn("拒否", str(u1.get("google_retry") or ""))

        server.delete_session_all(sid)

    def test_google_reply_gets_decensored_via_outbox(self):
        """Google/Vertex 経路の返答も outbox 経由で decensor_reply が効くか（実挙動の確認）"""
        import decensor_engine
        os.makedirs(server.DATA, exist_ok=True)
        with open(os.path.join(server.DATA, "decensor.json"), "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "words": {"突起": "乳首", "尻肉": "尻たぶ"}}, f, ensure_ascii=False)
        decensor_engine._DECENSOR_CACHE.update(key=None, words={}, compiled=None)

        sid = "test_google_decensor_sid"
        s = {
            "id": sid,
            "setting": {"title": "t", "ai_provider": "google", "google_model": "gemini-2.5-flash",
                        "character": {"name": "H"}, "image_mode": "off", "decensor_reply": True},
            "messages": [{"id": "u1", "role": "user", "text": "続けて", "status": "pending"}],
        }
        server.save(s)
        fake = {"text": json.dumps({"text": "突起を摘み、尻肉を揉む。"}, ensure_ascii=False),
                "meta": {"finish_reason": "STOP"}, "fetch_called": True, "elapsed_ms": 80, "error": None}
        with patch("google_ai.generate", return_value=fake):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)
            self.assertTrue(server.process_outbox())
        m = server.load(sid)["messages"][-1]
        print("BODY:", repr(m["text"]))
        self.assertEqual(m["text"], "乳首を摘み、尻たぶを揉む。")
        server.delete_session_all(sid)

    def test_run_google_task_keeps_normal_short_dialogue(self):
        """短いセリフは拒否扱いされず本文になる"""
        sid = "test_google_short_sid"
        s = {
            "id": sid,
            "setting": {
                "title": "Google Short Session",
                "ai_provider": "google",
                "google_model": "gemini-2.5-flash",
                "character": {"name": "TestHero"}
            },
            "messages": [
                {"id": "u1", "role": "user", "text": "うなずいて", "status": "pending"}
            ]
        }
        server.save(s)
        fake_reply = {
            "text": json.dumps({"text": "「……うん」と小さくうなずいた。"}, ensure_ascii=False),
            "meta": {"finish_reason": "STOP"},
            "fetched_called": False,
            "fetch_called": True,
            "elapsed_ms": 80,
            "error": None
        }
        with patch("google_ai.generate", return_value=fake_reply):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)
            self.assertTrue(server.process_outbox())
            updated_s = server.load(sid)
            self.assertEqual(len(updated_s["messages"]), 2)
            self.assertIn("うなずいた", updated_s["messages"][1]["text"])
        server.delete_session_all(sid)


class TestGoogleAITwoStagePreRefactor(unittest.TestCase):
    """§6.0 リファクタ前の特性テスト: 現行の fetch 2段階経路の挙動を固定する"""

    def test_two_stage_normal(self):
        step1 = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{
                        "functionCall": {"name": "fetch_prompt", "args": {}},
                        "thoughtSignature": "sig123",
                    }],
                },
                "finishReason": "STOP",
            }]
        }
        step2 = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "テスト応答本文"}],
                },
                "finishReason": "STOP",
            }]
        }
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "ユーザープロンプト", "システム指示",
                                     fetch=True, provider="vertex")
        self.assertEqual(len(calls), 2)
        second_pl = calls[1]
        self.assertEqual(len(second_pl["contents"]), 3)
        self.assertEqual(second_pl["contents"][1]["parts"][0].get("thoughtSignature"), "sig123")
        self.assertEqual(second_pl["contents"][2]["parts"][0]["functionResponse"]["response"]["context"],
                         "ユーザープロンプト")
        self.assertTrue(res.get("fetch_called"))
        self.assertEqual(res.get("first_status"), 200)
        self.assertEqual(res.get("second_status"), 200)
        self.assertEqual(res.get("text"), "テスト応答本文")
        self.assertIsNone(res.get("error"))

    def test_two_stage_first_no_call_vertex_direct(self):
        step1_no_call = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "fetchは呼びません"}],
                },
                "finishReason": "STOP",
            }]
        }
        direct = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "直接の応答"}],
                },
                "finishReason": "STOP",
            }]
        }
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1_no_call) if len(calls) == 1 else (200, direct)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex")
        self.assertEqual(len(calls), 2)
        self.assertFalse(res.get("fetch_called"))
        self.assertEqual(res.get("first_status"), 200)
        self.assertIsNone(res.get("second_status"))
        self.assertEqual(res.get("text"), "直接の応答")
        self.assertIsNone(res.get("error"))
        self.assertEqual(res.get("http_status"), 200)

    def test_two_stage_second_tool_400_vertex_direct(self):
        step1 = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}}],
                },
                "finishReason": "STOP",
            }]
        }
        direct = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "直接生成OK"}],
                },
                "finishReason": "STOP",
            }]
        }
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            if len(calls) == 1:
                return 200, step1
            if len(calls) == 2:
                raise google_ai.GoogleAPIError("function_call missing thought_signature",
                                               status=400, retryable=False)
            return 200, direct

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex")
        self.assertEqual(len(calls), 3)
        self.assertFalse(res.get("fetch_called"))
        self.assertEqual(res.get("text"), "直接生成OK")
        self.assertEqual(res.get("http_status"), 200)
        self.assertIsNone(res.get("error"))

    def test_two_stage_first_blocked(self):
        step1_blocked = {
            "promptFeedback": {"blockReason": "SAFETY"},
            "candidates": [],
        }
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return 200, step1_blocked

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex")
        self.assertEqual(len(calls), 1)
        self.assertFalse(res.get("fetch_called"))
        self.assertEqual(res.get("text"), "")
        self.assertEqual(res.get("error"), "prompt blocked: SAFETY")
        self.assertFalse(res.get("retryable"))

    def test_two_stage_second_blocked(self):
        step1 = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}}],
                },
                "finishReason": "STOP",
            }]
        }
        step2_blocked = {
            "candidates": [{
                "content": {"role": "model", "parts": []},
                "finishReason": "SAFETY",
            }],
        }
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2_blocked)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex")
        self.assertEqual(len(calls), 2)
        self.assertTrue(res.get("fetch_called"))
        self.assertEqual(res.get("text"), "")
        self.assertEqual(res.get("error"), "generation blocked: SAFETY")
        self.assertFalse(res.get("retryable"))

    def test_two_stage_empty_response(self):
        step1 = {
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}}],
                },
                "finishReason": "STOP",
            }]
        }
        step2_empty = {
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": ""}]},
                "finishReason": "STOP",
            }],
        }
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2_empty)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex")
        self.assertEqual(len(calls), 2)
        self.assertTrue(res.get("fetch_called"))
        self.assertEqual(res.get("text"), "")
        self.assertTrue(res.get("error", "").startswith("empty response"))
        self.assertTrue(res.get("retryable"))


class TestGoogleFetchSignatureCache(unittest.TestCase):
    """§6.1 単体テスト & §6.2 結合テスト: fetch 署名キャッシュ"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = google_ai.DATA
        self.orig_sig_file = google_ai.SIG_CACHE_FILE
        google_ai.DATA = self.tmp
        google_ai.SIG_CACHE_FILE = os.path.join(self.tmp, "fetch_signature.json")
        google_ai._SIG_MEM = None
        google_ai._SIG_FAIL.clear()
        google_ai.SIG_OK.clear()

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

    def tearDown(self):
        google_ai.DATA = self.orig_data
        google_ai.SIG_CACHE_FILE = self.orig_sig_file
        google_ai._SIG_MEM = None
        google_ai._SIG_FAIL.clear()
        google_ai.SIG_OK.clear()
        for k, v in self.orig_srv_dirs.items():
            setattr(server, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sig_cache_roundtrip(self):
        """put → get で同一 parts が返る"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        got = google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp)
        self.assertIsNotNone(got)
        self.assertEqual(got["parts"], mc["parts"])
        self.assertEqual(got["role"], "model")

    def test_sig_cache_fp_mismatch(self):
        """fp が違えば None"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        got = google_ai.sig_cache_get("vertex", "gemini-3.8-flash", "different_fp")
        self.assertIsNone(got)

    def test_sig_cache_skip_turns(self):
        """1 回目の失敗後、get が 1 ターンだけ None を返し、2 ターン目で署名を返す"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))
        got = google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp)
        self.assertIsNotNone(got)
        self.assertEqual(got["parts"][0]["thoughtSignature"], "sig-abc")

    def test_sig_cache_skip_grows(self):
        """2 回目の失敗後は 3 ターン None が続く"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 1st
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 2nd
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 3)
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 2)
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 1)
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 0)

    def test_sig_cache_give_up_after_third(self):
        """3 回目の失敗で given_up になり、その後は何ターン経っても None"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        self.assertTrue(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["given_up"])
        for _ in range(5):
            self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))

    def test_sig_cache_reset_revives(self):
        """sig_cache_reset() で中止が解け、また使える"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))
        google_ai.sig_cache_reset()
        self.assertNotIn("vertex|gemini-3.8-flash", google_ai._SIG_FAIL)
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        got = google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp)
        self.assertIsNotNone(got)

    def test_sig_cache_parts_dropped_on_second(self):
        """1 回目の失敗では parts が残り、2 回目で消える（第1段から取り直す）"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        e = google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]
        self.assertTrue(len(e["parts"]) > 0)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        e = google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]
        self.assertEqual(e["parts"], [])

    def test_sig_cache_put_does_not_touch_fail_state(self):
        """退避後の put が streak/skip/given_up を変えない（バグ回帰テスト）"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)
        st_before = copy.deepcopy(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"])
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        st_after = google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]
        self.assertEqual(st_before, st_after)

    def test_generate_cache_gives_up_and_recovers_on_restart(self):
        """3 回失敗→以降キャッシュを使わない→_SIG_FAIL.clear()（再起動相当）で復活する"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-abc"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        # 3回失敗させて中止状態にする
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 1
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 2 (drops parts)
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)     # puts valid sig
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 3 (given up)
        self.assertTrue(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["given_up"])

        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-fresh"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "通常応答"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2)

        # 中止後は generate() がキャッシュを使わず 2段階で動作し、fetch_cache は "off"
        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(res["fetch_cache"], "off")

        # 再起動相当（_SIG_FAIL.clear()）で復活
        google_ai._SIG_FAIL.clear()
        calls.clear()

        single_step = {"candidates": [{"content": {"role": "model", "parts": [{"text": "キャッシュヒット応答"}]}, "finishReason": "STOP"}]}
        with patch("google_ai.request", return_value=(200, single_step)):
            res2 = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                      fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res2["fetch_cache"], "hit")
        self.assertEqual(res2["text"], "キャッシュヒット応答")

    def test_generate_cache_lifecycle_full_sequence(self):
        """仕様書 §3.4 のターンA〜ターンI ライフサイクル全シーケンスの検証"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc1 = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-turn-a"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc1, fp)

        step1_fresh = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-fresh-fb"}]}, "finishReason": "STOP"}]}
        step1_new = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-turn-g-new"}]}, "finishReason": "STOP"}]}
        step2_ok = {"candidates": [{"content": {"role": "model", "parts": [{"text": "本文OK"}]}, "finishReason": "STOP"}]}

        # ターンA: 試す → 400 → 退避成功（失敗1回目、skip=1）
        calls_a = []
        def req_a(model, pl, timeout=180, *, provider="google"):
            calls_a.append(copy.deepcopy(pl))
            if len(calls_a) == 1:
                raise google_ai.GoogleAPIError("thought_signature is invalid", status=400, retryable=False)
            if len(calls_a) == 2:
                return 200, step1_fresh
            return 200, step2_ok

        with patch("google_ai.request", side_effect=req_a):
            res_a = google_ai.generate("gemini-3.8-flash", "pA", "sys", fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res_a["fetch_cache"], "hit_fallback")
        self.assertEqual(len(calls_a), 3)
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["streak"], 1)
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 1)

        # ターンB: 休む（skip=1を消費してskip=0へ。キャッシュは上書きしない）
        calls_b = []
        def req_b(model, pl, timeout=180, *, provider="google"):
            calls_b.append(copy.deepcopy(pl))
            return (200, step1_fresh) if len(calls_b) == 1 else (200, step2_ok)

        with patch("google_ai.request", side_effect=req_b):
            res_b = google_ai.generate("gemini-3.8-flash", "pB", "sys", fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res_b["fetch_cache"], "off")
        self.assertEqual(len(calls_b), 2)
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["streak"], 1)
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 0)

        # ターンC: 試す（同じ署名で再挑戦）→ 400 → 失敗2回目（skip=3、署名破棄）
        calls_c = []
        def req_c(model, pl, timeout=180, *, provider="google"):
            calls_c.append(copy.deepcopy(pl))
            if len(calls_c) == 1:
                raise google_ai.GoogleAPIError("thought_signature is invalid", status=400, retryable=False)
            if len(calls_c) == 2:
                return 200, step1_fresh
            return 200, step2_ok

        with patch("google_ai.request", side_effect=req_c):
            res_c = google_ai.generate("gemini-3.8-flash", "pC", "sys", fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res_c["fetch_cache"], "hit_fallback")
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["streak"], 2)
        self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], 3)
        # 署名が破棄されていること
        e = google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]
        self.assertEqual(e.get("parts"), [])

        # ターンD〜F: 3ターン休む（署名は保存されない）
        for turn_name, expected_skip in [("D", 2), ("E", 1), ("F", 0)]:
            calls_rest = []
            def req_rest(model, pl, timeout=180, *, provider="google"):
                calls_rest.append(copy.deepcopy(pl))
                return (200, step1_fresh) if len(calls_rest) == 1 else (200, step2_ok)

            with patch("google_ai.request", side_effect=req_rest):
                r = google_ai.generate("gemini-3.8-flash", f"p{turn_name}", "sys", fetch=True, provider="vertex", fetch_cache=True)
            self.assertEqual(r["fetch_cache"], "off", f"Turn {turn_name} should be off")
            self.assertEqual(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["skip"], expected_skip)
            e = google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]
            self.assertEqual(e.get("parts"), [], f"Turn {turn_name} parts should stay empty")

        # ターンG: ミス → 第1段から取得（新しい署名を保存）
        calls_g = []
        def req_g(model, pl, timeout=180, *, provider="google"):
            calls_g.append(copy.deepcopy(pl))
            return (200, step1_new) if len(calls_g) == 1 else (200, step2_ok)

        with patch("google_ai.request", side_effect=req_g):
            res_g = google_ai.generate("gemini-3.8-flash", "pG", "sys", fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res_g["fetch_cache"], "miss")
        self.assertEqual(len(calls_g), 2)
        # 新しい署名が保存されたこと
        e = google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]
        self.assertEqual(e["parts"][0]["thoughtSignature"], "sig-turn-g-new")

        # ターンH: 新しい署名で試す → 400 → 失敗3回目（中止: given_up=True）
        calls_h = []
        def req_h(model, pl, timeout=180, *, provider="google"):
            calls_h.append(copy.deepcopy(pl))
            if len(calls_h) == 1:
                raise google_ai.GoogleAPIError("thought_signature is invalid", status=400, retryable=False)
            if len(calls_h) == 2:
                return 200, step1_fresh
            return 200, step2_ok

        with patch("google_ai.request", side_effect=req_h):
            res_h = google_ai.generate("gemini-3.8-flash", "pH", "sys", fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res_h["fetch_cache"], "hit_fallback")
        self.assertTrue(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["given_up"])

        # ターンI〜: 中止後（二度と試さない）
        calls_i = []
        def req_i(model, pl, timeout=180, *, provider="google"):
            calls_i.append(copy.deepcopy(pl))
            return (200, step1_fresh) if len(calls_i) == 1 else (200, step2_ok)

        with patch("google_ai.request", side_effect=req_i):
            res_i = google_ai.generate("gemini-3.8-flash", "pI", "sys", fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res_i["fetch_cache"], "off")
        self.assertEqual(len(calls_i), 2)

    def test_acceptance_criterion_no_prompt_or_reply_in_cache(self):
        """受入基準 8: キャッシュファイルにプロンプトや生成本文が含まれないこと"""
        secret_prompt = "TOP_SECRET_USER_PROMPT_987654321"
        secret_reply = "TOP_SECRET_MODEL_REPLY_123456789"
        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-safe"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": secret_reply}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", secret_prompt, "sys",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res["fetch_cache"], "miss")
        self.assertTrue(os.path.exists(google_ai.SIG_CACHE_FILE))
        with open(google_ai.SIG_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(secret_prompt, raw)
        self.assertNotIn(secret_reply, raw)

    def test_generate_cache_disabled_no_disk_io(self):
        """disabled=True のモデルは毎ターン generate() を呼んでもディスク書き込みが発生しない"""
        fp = google_ai._sig_fp("fetch_prompt")
        huge_thought = "X" * 70000
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": huge_thought}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        self.assertTrue(google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]["disabled"])

        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "応答"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req), patch("google_ai._sig_save") as mock_save:
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
            self.assertEqual(res["fetch_cache"], "disabled")
            self.assertEqual(mock_save.call_count, 0)

    def test_sig_cache_disabled_on_oversize(self):
        """64KB 超の応答で disabled=True になり、以後 get が None"""
        fp = google_ai._sig_fp("fetch_prompt")
        huge_thought = "A" * 70000
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": huge_thought}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        e = google_ai._sig_load()["entries"]["vertex|gemini-3.8-flash"]
        self.assertTrue(e["disabled"])
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))

    def test_sig_cache_broken_file(self):
        """壊れた JSON でも例外を出さず空で動く"""
        os.makedirs(os.path.dirname(google_ai.SIG_CACHE_FILE), exist_ok=True)
        with open(google_ai.SIG_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write("{invalid json content!!!")
        google_ai._SIG_MEM = None
        d = google_ai._sig_load()
        self.assertEqual(d, {"version": 1, "entries": {}})
        self.assertIsNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", "any"))

    def test_sig_cache_atomic_write(self):
        """.tmp が残らない"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        self.assertTrue(os.path.exists(google_ai.SIG_CACHE_FILE))
        tmp_files = [f for f in os.listdir(self.tmp) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_generate_cache_hit_single_request(self):
        """request 呼び出しが 1 回、payload の contents が 3 要素でキャッシュ署名を含む、fetch_cache=='hit'、first_status is None"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "cached-sig-99"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "キャッシュヒット応答"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return 200, step2

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "ユーザープロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 1)
        pl = calls[0]
        self.assertEqual(len(pl["contents"]), 3)
        self.assertEqual(pl["contents"][1]["parts"][0]["thoughtSignature"], "cached-sig-99")
        self.assertEqual(res["fetch_cache"], "hit")
        self.assertIsNone(res["first_status"])
        self.assertEqual(res["second_status"], 200)
        self.assertEqual(res["text"], "キャッシュヒット応答")

    def test_generate_cache_miss_two_requests(self):
        """2 回呼ばれ、キャッシュに保存される"""
        fp = google_ai._sig_fp("fetch_prompt")
        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "new-sig-1"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "新規生成応答"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(res["fetch_cache"], "miss")
        self.assertEqual(res["first_status"], 200)
        self.assertEqual(res["second_status"], 200)
        got = google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp)
        self.assertIsNotNone(got)
        self.assertEqual(got["parts"][0]["thoughtSignature"], "new-sig-1")

    def test_generate_cache_400_falls_back(self):
        """1 回目 400 → 2 段階で成功、fetch_cache=='hit_fallback'"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "bad-sig"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "fresh-sig"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "退避成功応答"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            if len(calls) == 1:
                raise google_ai.GoogleAPIError("thought_signature is invalid", status=400, retryable=False)
            if len(calls) == 2:
                return 200, step1
            return 200, step2

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(res["fetch_cache"], "hit_fallback")
        self.assertEqual(res["text"], "退避成功応答")
        self.assertIsNone(res.get("error"))
        # 退避の理由がログと戻り値に残る（原因分析用）
        self.assertIn("thought_signature", res.get("cache_error") or "")

    def test_generate_cache_hit_has_no_cache_error(self):
        """ヒット成功時は cache_error を付けない"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "ok-sig"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "ヒット本文"}]}, "finishReason": "STOP"}]}
        with patch("google_ai.request", side_effect=lambda *a, **k: (200, step2)):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(res["fetch_cache"], "hit")
        self.assertNotIn("cache_error", res)

    def test_generate_cache_budget_guard_keeps_cache_error(self):
        """予算不足で退避できなかった場合も理由と retryable が残る"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "bad-sig"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)

        def fake_req(model, pl, timeout=180, *, provider="google"):
            raise google_ai.GoogleAPIError("thought_signature is invalid", status=400, retryable=False)

        with patch("google_ai.request", side_effect=fake_req), \
                patch("google_ai.time.time", side_effect=[0.0, 0.5, 9999.0, 9999.5]):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True, timeout=10)
        self.assertEqual(res["fetch_cache"], "hit_fallback")
        self.assertTrue(res["retryable"])
        self.assertIn("thought_signature", res.get("cache_error") or "")
        # 退避できなかったので、エラー本文もそのまま返る

    def test_generate_cache_400_google_provider(self):
        """provider='google' でも退避する"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "bad-sig"}], "role": "model"}
        google_ai.sig_cache_put("google", "gemini-2.5-flash", mc, fp)
        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "fresh-sig"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "google退避成功"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            if len(calls) == 1:
                raise google_ai.GoogleAPIError("functionCall is missing a thought_signature", status=400, retryable=False)
            if len(calls) == 2:
                return 200, step1
            return 200, step2

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", "システム",
                                     fetch=True, provider="google", fetch_cache=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(res["fetch_cache"], "hit_fallback")
        self.assertEqual(res["text"], "google退避成功")

    def test_generate_cache_empty_falls_back(self):
        """空応答 → 2 段階で成功、ユーザーにエラーが出ない"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-empty"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        step2_empty = {"candidates": [{"content": {"role": "model", "parts": [{"text": ""}]}, "finishReason": "STOP"}]}
        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "fresh-sig"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "空退避成功"}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            if len(calls) == 1:
                return 200, step2_empty
            if len(calls) == 2:
                return 200, step1
            return 200, step2

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(res["fetch_cache"], "hit_fallback")
        self.assertEqual(res["text"], "空退避成功")
        self.assertIsNone(res.get("error"))

    def test_generate_cache_safety_no_fallback(self):
        """SAFETY 切断は退避せず、リクエスト 1 回で終わる（fetch_cache=='hit'）"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-safe"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        step2_safety = {"candidates": [{"content": {"role": "model", "parts": []}, "finishReason": "SAFETY"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return 200, step2_safety

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["fetch_cache"], "hit")
        self.assertEqual(res["error"], "generation blocked: SAFETY")
        self.assertFalse(res["retryable"])

    def test_generate_cache_429_not_retried(self):
        """429 は 2 段階に退避せず例外を伝播する（リクエスト 1 回）"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-429"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            raise google_ai.GoogleAPIError("Resource exhausted", status=429, retryable=True)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["http_status"], 429)
        self.assertTrue(res["retryable"])
        self.assertEqual(res["fetch_cache"], "hit")

    def test_generate_cache_budget_guard(self):
        """第2段が timeout 近くまでかかった場合、2 段階に退避せず retryable=True で返す"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-budget"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            time.sleep(0.02)  # timeout(0.01秒) を確実に超える最小限の待ち
            raise google_ai.GoogleAPIError("thought_signature invalid", status=400, retryable=False)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True, timeout=0.01)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["fetch_cache"], "hit_fallback")
        self.assertTrue(res["retryable"])

    def test_generate_cache_flag_off_unchanged(self):
        """OFF なら 2 回呼ばれ、キャッシュファイルが作られない"""
        calls = []
        step1 = {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-off"}]}, "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [{"text": "キャッシュOFF応答"}]}, "finishReason": "STOP"}]}

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1) if len(calls) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=False)
        self.assertEqual(len(calls), 2)
        self.assertEqual(res["fetch_cache"], "off")
        self.assertFalse(os.path.exists(google_ai.SIG_CACHE_FILE))

    def test_generate_cache_skipped_with_images(self):
        """images ありでは使われない"""
        calls = []
        direct = {"candidates": [{"content": {"role": "model", "parts": [{"text": "画像付き応答"}]}, "finishReason": "STOP"}]}

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return 200, direct

        images = [{"mimeType": "image/png", "data": "abc"}]
        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=True, provider="vertex", fetch_cache=True, images=images)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["fetch_cache"], "off")

    def test_generate_cache_skip_when_fetch_false(self):
        """fetch=False では使われない（choices / memory 相当）"""
        calls = []
        direct = {"candidates": [{"content": {"role": "model", "parts": [{"text": "fetch=False応答"}]}, "finishReason": "STOP"}]}

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return 200, direct

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-3.8-flash", "プロンプト", "システム",
                                     fetch=False, provider="vertex", fetch_cache=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res["fetch_cache"], "off")

    def test_run_google_task_with_cache_hit(self):
        """フラグ ON・キャッシュありで本文が outbox に落ち、google_status に fetch_cache=='hit'"""
        sid = "test_cache_hit_sid"
        s = {
            "id": sid,
            "setting": {"title": "Cache Hit Session", "ai_provider": "vertex", "vertex_model": "gemini-3.8-flash",
                        "google_fetch": True, "google_fetch_cache": True, "character": {"name": "Hero"}},
            "messages": [{"id": "u1", "role": "user", "text": "こんにちは", "status": "pending"}],
        }
        server.save(s)
        fake_reply = {
            "text": json.dumps({"text": "キャッシュヒット返答"}, ensure_ascii=False),
            "meta": {"finish_reason": "STOP"},
            "fetch_called": True,
            "elapsed_ms": 50,
            "error": None,
            "fetch_cache": "hit",
        }
        with patch("google_ai.generate", return_value=fake_reply):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)
            st = server.google_status.get(sid, {})
            self.assertEqual(st.get("fetch_cache"), "hit")
            self.assertTrue(server.process_outbox())
        server.delete_session_all(sid)

    def test_flag_off_clears_fail_state(self):
        """フラグ OFF で呼ぶと失敗状態が消え、ON に戻すと再びヒットする（OFF→ON 復活の回帰テスト）"""
        fp = google_ai._sig_fp("fetch_prompt")
        mc = {"parts": [{"functionCall": {"name": "fetch_prompt", "args": {}}, "thoughtSignature": "sig-revive"}], "role": "model"}
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 1
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 2 (drops parts)
        google_ai.sig_cache_put("vertex", "gemini-3.8-flash", mc, fp)     # acquired new sig
        google_ai.sig_cache_mark("vertex", "gemini-3.8-flash", ok=False)  # 3 (given up)
        self.assertTrue(google_ai._SIG_FAIL["vertex|gemini-3.8-flash"]["given_up"])

        sid = "test_flag_off_sid"
        s = {
            "id": sid,
            "setting": {"title": "Flag Off Session", "ai_provider": "vertex", "vertex_model": "gemini-3.8-flash",
                        "google_fetch": True, "google_fetch_cache": False, "character": {"name": "Hero"}},
            "messages": [{"id": "u1", "role": "user", "text": "テスト", "status": "pending"}],
        }
        server.save(s)
        fake_reply = {
            "text": json.dumps({"text": "OFF返答"}, ensure_ascii=False),
            "meta": {"finish_reason": "STOP"},
            "fetch_called": True,
            "elapsed_ms": 60,
            "error": None,
            "fetch_cache": "off",
        }
        with patch("google_ai.generate", return_value=fake_reply):
            key = server._google_task_key(sid, "user", s["messages"][0])
            server.run_google_task(sid, "user", "u1", key)
            self.assertNotIn("vertex|gemini-3.8-flash", google_ai._SIG_FAIL)
            self.assertIsNotNone(google_ai.sig_cache_get("vertex", "gemini-3.8-flash", fp))
        server.delete_session_all(sid)


if __name__ == "__main__":
    unittest.main()
