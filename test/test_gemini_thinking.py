"""
test_gemini_thinking.py - Unit tests for Gemini thinking level resolution,
model normalization, task-specific thinking resolution, SDK fallback, and API endpoints.
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


class TestGeminiThinkingResolution(unittest.TestCase):
    def test_ac2_model_thinking_family_and_resolution(self):
        # 1. gemini-4-hydrogen
        # minimal -> LOW
        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-4-hydrogen", "minimal")
        self.assertEqual(eff, "LOW")
        self.assertEqual(tc, {"thinkingLevel": "LOW"})
        self.assertEqual(min_out, 0)
        # xhigh -> XHIGH, min_out=32768
        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-4-hydrogen", "xhigh")
        self.assertEqual(eff, "XHIGH")
        self.assertEqual(tc, {"thinkingLevel": "XHIGH"})
        self.assertGreaterEqual(min_out, 32768)

        # 2. gemini-3.8-flash / gemini-3-pro / gemini-flash-latest
        for m in ("gemini-3.8-flash", "gemini-3-pro", "gemini-flash-latest"):
            tc, eff, _ = google_ai.resolve_thinking_config(m, "minimal")
            self.assertEqual(eff, "LOW", f"{m} minimal should round to LOW")
            self.assertEqual(tc, {"thinkingLevel": "LOW"})
            tc, eff, _ = google_ai.resolve_thinking_config(m, "xhigh")
            self.assertEqual(eff, "HIGH", f"{m} xhigh should round to HIGH")
            self.assertEqual(tc, {"thinkingLevel": "HIGH"})

        # 3. gemini-3.5-flash / gemini-3.1-flash-lite / gemini-flash-lite-latest
        for m in ("gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"):
            tc, eff, _ = google_ai.resolve_thinking_config(m, "minimal")
            self.assertEqual(eff, "MINIMAL", f"{m} minimal should be MINIMAL")
            self.assertEqual(tc, {"thinkingLevel": "MINIMAL"})
            tc, eff, _ = google_ai.resolve_thinking_config(m, "xhigh")
            self.assertEqual(eff, "HIGH", f"{m} xhigh should round to HIGH")
            self.assertEqual(tc, {"thinkingLevel": "HIGH"})

        # 4. gemini-2.5-flash
        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-2.5-flash", "minimal")
        self.assertEqual(tc, {"thinkingBudget": 0})
        self.assertEqual(eff, "budget:0")
        self.assertEqual(min_out, 0)

        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-2.5-flash", "low")
        self.assertEqual(tc, {"thinkingBudget": 1024})
        self.assertEqual(eff, "budget:1024")
        self.assertEqual(min_out, 8192)

        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-2.5-flash", "medium")
        self.assertEqual(tc, {"thinkingBudget": 4096})
        self.assertEqual(eff, "budget:4096")
        self.assertEqual(min_out, 8192)

        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-2.5-flash", "high")
        self.assertEqual(tc, {"thinkingBudget": 8192})
        self.assertEqual(eff, "budget:8192")
        self.assertEqual(min_out, 12288)

        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-2.5-flash", "xhigh")
        self.assertEqual(tc, {"thinkingBudget": 16384})
        self.assertEqual(eff, "budget:16384")
        self.assertEqual(min_out, 20480)

        # 5. gemini-2.5-pro
        tc, eff, min_out = google_ai.resolve_thinking_config("gemini-2.5-pro", "minimal")
        self.assertEqual(tc, {"thinkingBudget": 128})
        self.assertEqual(eff, "budget:128")
        self.assertEqual(min_out, 8192)

        # 5b. Hyphenated 2-5 variants (gemini-2-5-flash, gemini-2-5-pro)
        self.assertEqual(google_ai._thinking_family("gemini-2-5-flash"), "budget_25_flash")
        self.assertEqual(google_ai._thinking_family("gemini-2-5-pro"), "budget_25_pro")
        tc_h, eff_h, min_h = google_ai.resolve_thinking_config("gemini-2-5-flash", "low")
        self.assertEqual(tc_h, {"thinkingBudget": 1024})
        self.assertEqual(eff_h, "budget:1024")
        self.assertEqual(min_h, 8192)

        # 6. Non-thinking models
        for m in ("gemini-2.0-flash", "gemini-2.0-flash-thinking-exp", "gemma-3-27b", "gemini-1.5-flash-latest"):
            for lv in ("minimal", "low", "medium", "high", "xhigh", "auto"):
                tc, eff, min_out = google_ai.resolve_thinking_config(m, lv)
                self.assertIsNone(tc, f"{m} {lv} should have no thinkingConfig")
                self.assertEqual(eff, "none")
                self.assertEqual(min_out, 0)

    def test_ac3_auto_backward_compatibility(self):
        # When auto, _step2_generation_config must not include thinkingConfig
        cfg = google_ai._step2_generation_config("gemini-3.8-flash", 8192, provider="google", thinking_level="auto")
        self.assertNotIn("thinkingConfig", cfg)
        self.assertEqual(cfg["maxOutputTokens"], 8192)

        cfg_vertex = google_ai._step2_generation_config("gemini-3.8-flash", 8192, provider="vertex", thinking_level="auto")
        self.assertNotIn("thinkingConfig", cfg_vertex)
        # vertex with thinking model has floor 16384
        self.assertEqual(cfg_vertex["maxOutputTokens"], 16384)

    def test_step2_token_floor_elevation(self):
        # gemini-2.5-flash with high -> min_out is 12288
        cfg = google_ai._step2_generation_config("gemini-2.5-flash", 4096, provider="google", thinking_level="high")
        self.assertGreaterEqual(cfg["maxOutputTokens"], 12288)
        self.assertEqual(cfg["thinkingConfig"], {"thinkingBudget": 8192})

        # gemini-4-hydrogen with xhigh -> min_out is 32768
        cfg2 = google_ai._step2_generation_config("gemini-4-hydrogen", 8192, provider="google", thinking_level="xhigh")
        self.assertGreaterEqual(cfg2["maxOutputTokens"], 32768)
        self.assertEqual(cfg2["thinkingConfig"], {"thinkingLevel": "XHIGH"})

    def test_generate_timeout_extension(self):
        # generate with XHIGH or budget:16384 extends timeout to at least 240
        called_timeout = []
        def fake_req(model, payload, timeout=180, provider="google"):
            called_timeout.append(timeout)
            return 200, {"candidates": [{"content": {"parts": [{"text": "{\"ok\": true}"}]}}]}

        with patch("google_ai.request", side_effect=fake_req):
            google_ai.generate("gemini-4-hydrogen", "prompt", fetch=False, timeout=180, thinking_level="xhigh")
            self.assertTrue(len(called_timeout) > 0)
            self.assertGreaterEqual(called_timeout[0], 240)

        called_timeout.clear()
        with patch("google_ai.request", side_effect=fake_req):
            google_ai.generate("gemini-2.5-flash", "prompt", fetch=False, timeout=180, thinking_level="xhigh")
            self.assertTrue(len(called_timeout) > 0)
            self.assertGreaterEqual(called_timeout[0], 240)

    def test_ac4_sdk_fallback_to_rest(self):
        # Test _sdk_payload_to_kwargs with dummy types
        class DummyThinkingConfigNoLevel:
            model_fields = {"thinking_budget": None}
            def __init__(self, **kwargs):
                if "thinking_level" in kwargs:
                    raise TypeError("Unexpected thinking_level")
                self.thinking_budget = kwargs.get("thinking_budget")

        class DummyTypes:
            ThinkingConfig = DummyThinkingConfigNoLevel
            Part = MagicMock()
            Content = MagicMock()

        orig_types = google_ai.genai_types
        try:
            google_ai.genai_types = DummyTypes
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {
                    "thinkingConfig": {"thinkingLevel": "HIGH"}
                }
            }
            with self.assertRaises(google_ai._SdkFallbackToRest):
                google_ai._sdk_payload_to_kwargs(payload)
        finally:
            google_ai.genai_types = orig_types

    def test_ac5_active_thinking_level_tasks(self):
        # 本文が minimal または low のとき、consolidation は medium
        st1 = {"thinking_level": "minimal", "choices_thinking_level": "low"}
        self.assertEqual(server.active_thinking_level(st1, "user"), "minimal")
        self.assertEqual(server.active_thinking_level(st1, "consolidation"), "medium")

        st2 = {"thinking_level": "low", "choices_thinking_level": "low"}
        self.assertEqual(server.active_thinking_level(st2, "consolidation"), "medium")

        st3 = {"thinking_level": "high", "choices_thinking_level": "low"}
        self.assertEqual(server.active_thinking_level(st3, "consolidation"), "high")

        # 本文が xhigh かつ 補助タスクが same のとき
        # choices / image_refine は high、memory_extract / memory_regen は medium に制限
        st_x = {"thinking_level": "xhigh", "choices_thinking_level": "same"}
        self.assertEqual(server.active_thinking_level(st_x, "choices"), "high")
        self.assertEqual(server.active_thinking_level(st_x, "image_refine"), "high")
        self.assertEqual(server.active_thinking_level(st_x, "memory_extract"), "medium")
        self.assertEqual(server.active_thinking_level(st_x, "memory_regen"), "medium")

        # 補助タスクが直接 high のときも memory_extract は medium に制限
        st_h = {"thinking_level": "auto", "choices_thinking_level": "high"}
        self.assertEqual(server.active_thinking_level(st_h, "choices"), "high")
        self.assertEqual(server.active_thinking_level(st_h, "memory_extract"), "medium")
        self.assertEqual(server.active_thinking_level(st_h, "memory_regen"), "medium")

    def test_extract_text_thoughts_token_count(self):
        # 1. camelCase usageMetadata with thoughtsTokenCount
        data_camel = {
            "candidates": [{"content": {"parts": [{"text": "camel response"}]}}],
            "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 100, "thoughtsTokenCount": 42}
        }
        txt1, meta1 = google_ai.extract_text(data_camel)
        self.assertEqual(txt1, "camel response")
        self.assertEqual(meta1["usage"]["thoughtsTokenCount"], 42)
        self.assertEqual(meta1["usage"]["thoughts_token_count"], 42)

        # 2. snake_case usage_metadata with thoughts_token_count
        data_snake = {
            "candidates": [{"content": {"parts": [{"text": "snake response"}]}}],
            "usage_metadata": {"prompt_token_count": 60, "candidates_token_count": 120, "thoughts_token_count": 88}
        }
        txt2, meta2 = google_ai.extract_text(data_snake)
        self.assertEqual(txt2, "snake response")
        self.assertEqual(meta2["usage"]["thoughtsTokenCount"], 88)
        self.assertEqual(meta2["usage"]["thoughts_token_count"], 88)


class FakeHandler:
    def __init__(self, path, body=None):
        self.path = path
        self._body = body or {}
        self.status = 200
        self.result = None

    def blocked(self, u, post=False):
        return False

    def body(self):
        return self._body

    def send_json(self, data, code=200):
        self.status = code
        self.result = data
        return data


class TestGeminiThinkingApiAndKeep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = server.DATA
        server.DATA = self.tmp
        server.SESSIONS_DIR = os.path.join(self.tmp, "sessions")
        os.makedirs(server.SESSIONS_DIR, exist_ok=True)

    def tearDown(self):
        server.DATA = self.orig_data
        server.SESSIONS_DIR = os.path.join(self.orig_data, "sessions")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_merge_defaults_thinking_levels(self):
        st = server.merge_defaults({"thinking_level": "HIGH", "choices_thinking_level": "SAME"})
        self.assertEqual(st["thinking_level"], "high")
        self.assertEqual(st["choices_thinking_level"], "same")

        # Invalid fallback
        st_bad = server.merge_defaults({"thinking_level": "invalid_level", "choices_thinking_level": "bad"})
        self.assertEqual(st_bad["thinking_level"], "auto")
        self.assertEqual(st_bad["choices_thinking_level"], "low")

    def test_ac9_setting_keep_protection(self):
        sid = "test_keep_thinking_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "Keep Test",
                "ai_provider": "google",
                "thinking_level": "high",
                "choices_thinking_level": "minimal",
            }),
            "messages": []
        }
        server.save(s)

        # Execute real server.H.do_POST /api/setting without thinking_level in form
        client_form = {"sid": sid, "setting": {"title": "Updated Title"}}
        h = FakeHandler("/api/setting", client_form)
        server.H.do_POST(h)
        self.assertEqual(h.status, 200)

        # Verify that thinking_level and choices_thinking_level were protected by keep dict
        saved = server.load(sid)
        self.assertEqual(saved["setting"]["title"], "Updated Title")
        self.assertEqual(saved["setting"]["thinking_level"], "high")
        self.assertEqual(saved["setting"]["choices_thinking_level"], "minimal")

    def test_ac10_thinking_api_and_provider_api(self):
        sid = "test_api_thinking_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "API Test",
                "ai_provider": "google",
                "thinking_level": "auto",
                "choices_thinking_level": "low",
            }),
            "messages": []
        }
        server.save(s)

        # 1. POST /api/ai/thinking valid update via real H.do_POST
        h1 = FakeHandler("/api/ai/thinking", {"sid": sid, "thinking_level": "medium", "choices_thinking_level": "high"})
        server.H.do_POST(h1)
        self.assertEqual(h1.status, 200)
        self.assertTrue(h1.result.get("ok"))
        self.assertEqual(h1.result.get("thinking_level"), "medium")
        self.assertEqual(h1.result.get("choices_thinking_level"), "high")

        saved = server.load(sid)
        self.assertEqual(saved["setting"]["thinking_level"], "medium")
        self.assertEqual(saved["setting"]["choices_thinking_level"], "high")

        # 2. POST /api/ai/thinking invalid thinking_level returns 400
        h2 = FakeHandler("/api/ai/thinking", {"sid": sid, "thinking_level": "super_deep"})
        server.H.do_POST(h2)
        self.assertEqual(h2.status, 400)
        self.assertIn("無効な thinking_level", h2.result.get("error", ""))

        # 3. POST /api/ai/thinking invalid choices_thinking_level returns 400
        h3 = FakeHandler("/api/ai/thinking", {"sid": sid, "choices_thinking_level": "ultra"})
        server.H.do_POST(h3)
        self.assertEqual(h3.status, 400)
        self.assertIn("無効な choices_thinking_level", h3.result.get("error", ""))

        # 4. POST /api/ai/provider accepts thinking_level and choices_thinking_level
        with patch("google_ai.load_key", return_value=("fake_key", "fake_source")), \
             patch("google_ai.default_model", return_value="gemini-2.5-flash"):
            h4 = FakeHandler("/api/ai/provider", {
                "sid": sid,
                "provider": "google",
                "model": "gemini-2.5-flash",
                "thinking_level": "low",
                "choices_thinking_level": "same"
            })
            server.H.do_POST(h4)
            self.assertEqual(h4.status, 200)
            self.assertEqual(h4.result.get("thinking_level"), "low")
            self.assertEqual(h4.result.get("choices_thinking_level"), "same")

        saved2 = server.load(sid)
        self.assertEqual(saved2["setting"]["thinking_level"], "low")
        self.assertEqual(saved2["setting"]["choices_thinking_level"], "same")


if __name__ == "__main__":
    unittest.main()
