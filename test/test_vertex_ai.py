import base64
import copy
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import google_ai
import server


SAMPLE_SA = {
    "type": "service_account",
    "project_id": "demo-gcp-proj",
    "private_key_id": "key123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC7\n-----END PRIVATE KEY-----\n",
    "client_email": "rpchat-bot@demo-gcp-proj.iam.gserviceaccount.com",
    "token_uri": google_ai.GOOGLE_TOKEN_URI,
}


class TestVertexAI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_data = google_ai.DATA
        self.orig_env = google_ai.ENV_FILE
        self.orig_srv_data = server.DATA
        self.orig_srv_sess = server.SESS_DIR
        google_ai.DATA = self.tmp.name
        google_ai.ENV_FILE = os.path.join(self.tmp.name, ".env")
        server.DATA = self.tmp.name
        server.SESS_DIR = os.path.join(self.tmp.name, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        os.environ["RPCHAT_GEMINI_TRANSPORT"] = "rest"
        for k in ("VERTEX_PROJECT_ID", "VERTEX_LOCATION", "VERTEX_SA_JSON_PATH"):
            os.environ.pop(k, None)
        google_ai.invalidate_vertex_token()
        google_ai.invalidate_models_cache()

    def tearDown(self):
        google_ai.DATA = self.orig_data
        google_ai.ENV_FILE = self.orig_env
        server.DATA = self.orig_srv_data
        server.SESS_DIR = self.orig_srv_sess
        for k in ("RPCHAT_GEMINI_TRANSPORT", "VERTEX_PROJECT_ID",
                  "VERTEX_LOCATION", "VERTEX_SA_JSON_PATH"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    # 1: SA JSON の検証・正規化と設定保存
    def test_1_validate_and_save_sa_json(self):
        with self.assertRaises(ValueError):
            google_ai.parse_and_validate_sa_json("not-a-json")
        with self.assertRaises(ValueError):
            google_ai.parse_and_validate_sa_json(json.dumps({"type": "authorized_user"}))

        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            cfg = google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                               project_id="", location="asia-northeast1")
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["project_id"], "demo-gcp-proj")
        self.assertEqual(cfg["location"], "asia-northeast1")
        self.assertEqual(cfg["sa_basename"], "vertex_sa.json")
        self.assertNotIn("sa_path", cfg)

    # 2: トークン 401 時の1回再試行
    def test_2_token_cache_and_401_retry(self):
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                         project_id="demo-gcp-proj", location="global")
        calls = []

        def fake_urlopen(req, timeout=15):
            calls.append(req.get_header("Authorization"))
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                             io.BytesIO(b'{"error":{"message":"expired"}}'))
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'
            resp.__enter__.return_value = resp
            return resp

        tokens = [("tok-1", "service_account", ""), ("tok-2", "service_account", "")]
        with patch("google_ai.get_vertex_access_token",
                   side_effect=lambda force_refresh=False, timeout=15: tokens.pop(0)), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            st, data = google_ai.request("gemini-2.5-flash", {"contents": []}, provider="vertex")
        self.assertEqual(st, 200)
        self.assertEqual(calls, ["Bearer tok-1", "Bearer tok-2"])

    # 3: URL 生成（自動リンク化の影響を受けない）と Safety 既定
    def test_3_vertex_url_and_safety_categories(self):
        u_global = google_ai.vertex_generate_url("my-proj", "global", "gemini-2.0-flash-001")
        self.assertTrue(u_global.startswith(
            google_ai._https("aiplatform.googleapis",
                             "/v1beta1/projects/my-proj/locations/global/")))
        self.assertIn("gemini-2.0-flash-001:generateContent", u_global)
        self.assertNotIn("[", u_global)
        self.assertNotIn("](", u_global)

        u_reg = google_ai.vertex_generate_url("my-proj", "asia-northeast1", "gemini-2.5-pro")
        self.assertTrue(u_reg.startswith(
            google_ai._https("asia-northeast1-aiplatform.googleapis", "/")))

        v_safety = google_ai._safety_settings(True, provider="vertex", threshold="OFF")
        self.assertEqual(len(v_safety), 4)
        self.assertTrue(all(x["category"] != "HARM_CATEGORY_JAILBREAK" for x in v_safety))
        self.assertTrue(all(x["threshold"] == "BLOCK_NONE" for x in v_safety))
        g_safety = google_ai._safety_settings(True, provider="google")
        self.assertEqual(len(g_safety), 5)
        self.assertTrue(all(x["threshold"] == "OFF" for x in g_safety))

    # 4: merge_defaults とモデル解決
    def test_4_merge_defaults_and_active_model(self):
        st = server.merge_defaults({"ai_provider": "vertex", "vertex_model": "gemini-2.5-pro",
                                    "vertex_choices_model": "gemini-2.5-flash-lite"})
        self.assertEqual(st["ai_provider"], "vertex")
        self.assertTrue(server.is_api_provider(st))
        self.assertFalse(server.is_api_provider({"ai_provider": "antigravity"}))
        self.assertTrue(server.is_api_provider({"ai_provider": "google"}))
        self.assertEqual(server.active_api_model(st, "user"), "gemini-2.5-pro")
        self.assertEqual(server.active_api_model(st, "choices"), "gemini-2.5-flash-lite")
        self.assertEqual(server._api_model_setting(st, "choices"), "gemini-2.5-flash-lite")
        st_empty = server.merge_defaults({"ai_provider": "vertex"})
        self.assertEqual(server.active_api_model(st_empty, "user"), "gemini-2.5-flash")
        self.assertEqual(server._api_model_setting(st_empty, "user"), "")

    # 5: 正規表現（fullmatch・末尾改行拒否）
    def test_5_regex_validation(self):
        self.assertIsNotNone(google_ai.MODEL_ID_RE.fullmatch("gemini-2.0-flash-001"))
        self.assertIsNotNone(google_ai.MODEL_ID_RE.fullmatch("gemini-2.0-flash@001"))
        self.assertIsNone(google_ai.MODEL_ID_RE.fullmatch("gemini-2.0-flash\n"))
        self.assertIsNone(google_ai.MODEL_ID_RE.fullmatch("bad/model"))
        self.assertIsNotNone(google_ai.PROJECT_ID_RE.fullmatch("my-gcp-project-123"))
        self.assertIsNone(google_ai.PROJECT_ID_RE.fullmatch("123-starts-with-number"))
        self.assertIsNone(google_ai.PROJECT_ID_RE.fullmatch("my-gcp-project\n"))

    # 6: 認証ゼロでは .env を書かない
    def test_6_save_requires_auth(self):
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        with patch("google_ai._default_adc_path", return_value=""):
            with self.assertRaises(ValueError):
                google_ai.save_vertex_config(sa_json=None, project_id="my-proj", location="global")
            self.assertFalse(os.path.exists(google_ai.ENV_FILE))
            self.assertNotIn("VERTEX_PROJECT_ID", os.environ)

    # 7: 保存の原子性（不正 project_id で既存 SA を壊さない）と旧パス消去
    def test_7_atomic_save_and_cleanup_old_sa_path(self):
        google_ai._update_env_vars({"VERTEX_SA_JSON_PATH": "/old/custom/sa.json"})
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            cfg = google_ai.save_vertex_config(sa_json="{}", project_id="my-proj", location="global")
        env = google_ai.read_env_file()
        self.assertNotIn("VERTEX_SA_JSON_PATH", env)
        self.assertEqual(cfg["project_id"], "my-proj")

        bad_sa = dict(SAMPLE_SA, client_email="other@demo-gcp-proj.iam.gserviceaccount.com")
        with patch("google_ai.parse_and_validate_sa_json", return_value=bad_sa):
            with self.assertRaises(ValueError):
                google_ai.save_vertex_config(sa_json="{}", project_id="123-invalid-proj",
                                             location="global")
        with open(os.path.join(self.tmp.name, "vertex_sa.json"), "r", encoding="utf-8") as f:
            saved_sa = json.load(f)
        self.assertEqual(saved_sa["client_email"], SAMPLE_SA["client_email"])

    # 8: 設定削除後は ADC があっても configured=False
    def test_8_delete_config_with_adc_becomes_unconfigured(self):
        adc_file = os.path.join(self.tmp.name, "adc.json")
        with open(adc_file, "w", encoding="utf-8") as f:
            json.dump({"type": "authorized_user", "client_id": "c", "client_secret": "s",
                       "refresh_token": "r", "quota_project_id": "adc-quota-proj"}, f)
        with patch("google_ai._default_adc_path", return_value=adc_file):
            google_ai.save_vertex_config(sa_json=None, project_id="my-proj", location="global")
            self.assertTrue(google_ai.vertex_configured())
            google_ai.delete_vertex_config()
            cfg = google_ai.load_vertex_config()
            self.assertFalse(cfg["configured"])
            self.assertEqual(cfg["adc_project_hint"], "adc-quota-proj")

    # 9: choices の slot 明示とファイル名
    def test_9_choices_slot_is_explicit(self):
        s = {"id": "sid_slot",
             "setting": {"ai_provider": "vertex", "vertex_model": "gemini-2.5-flash",
                         "choices_erotic_auto": True},
             "messages": [{"id": "a1", "role": "assistant", "text": "x",
                           "choices_status": "pending", "choices_type": "erotic"}]}
        with patch("server.load", return_value=s), patch("server.list_ids", return_value=["sid_slot"]):
            tasks = server._google_pick_tasks()
            self.assertEqual([t[2] for t in tasks], ["choices"])
        self.assertEqual(server._google_choice_type(s["messages"][0]), "erotic")
        name = server._google_write_outbox("sid_slot", "a1", "choices", {"choices": []}, slot="erotic")
        try:
            self.assertIn("_choices_erotic_", name)
            self.assertEqual(server.choices_slot_of({"choices": []}, {}, name), "erotic")
        finally:
            p = os.path.join(server.OUTBOX, name)
            if os.path.exists(p):
                os.remove(p)

    # 10: choices 出力テキストの3分岐と outbox 指示除去
    def test_10_choices_output_text_branches(self):
        s = {"id": "sid_ot", "setting": {"suggest_choices": True}, "messages": []}
        a = {"id": "a1", "role": "assistant", "text": "x"}
        u = {"id": "u1", "role": "user", "text": "y"}
        s["messages"] = [u, a]
        regen = server._google_choices_output_text(s, a, erotic=True)
        self.assertIn("choices", regen)
        self.assertNotIn("outbox/", regen)
        par = server._google_choices_output_text(s, u, erotic=False)
        self.assertNotIn("outbox/", par)
        self.assertIn("JSON出力ルール", par)

    # 11: API プロバイダでは sent 台帳を触らない
    def test_11_note_reply_skips_api_provider(self):
        s = {"id": "sid_sent", "setting": {"ai_provider": "vertex"}, "messages": []}
        with patch("server.read_sent") as rd, patch("server.write_sent") as wr:
            server.note_reply(s, "u1", {"id": "a1", "role": "assistant", "text": "x"})
            rd.assert_not_called()
            wr.assert_not_called()
        with patch("server.read_sent") as rd2, patch("server.write_sent") as wr2:
            server.mark_choices_sent(s)
            rd2.assert_not_called()
            wr2.assert_not_called()

    # 12: 削除時のパージ／仮想 sid でも status が動く
    def test_12_delete_purges_and_virtual_sid_status_works(self):
        v_sid = "sid_virtual_test"
        server.deleted_sessions.discard(v_sid)
        server._set_google_status(v_sid, "user", "requesting", model="gemini-2.5-flash")
        self.assertIn(v_sid, server.google_status)
        server._google_clear_status(v_sid, "user")
        self.assertNotIn(v_sid, server.google_status)
        server._set_google_status(v_sid, "user", "requesting")
        server.delete_session_all(v_sid, to_trash=False)
        self.assertNotIn(v_sid, server.google_status)
        server._set_google_status(v_sid, "user", "requesting")
        self.assertNotIn(v_sid, server.google_status)
        server.deleted_sessions.discard(v_sid)

    # 12b (F-2): 既存 _google_clear_status のタスクマップ方式が維持されている
    def test_12b_clear_status_keeps_user_entry(self):
        sid = "sid_clear_keep"
        server.deleted_sessions.discard(sid)
        server._set_google_status(sid, "user", "requesting", model="m-a")
        server._set_google_status(sid, "choices", "waiting_import", model="m-b")
        server._google_clear_status(sid, "choices")
        top = server.google_status[sid]
        self.assertEqual(top["task_type"], "user")
        self.assertNotIn("choices", top.get("tasks", {}))
        server._google_clear_status(sid)

    # 13: transport=rest で REST 経路を通る
    def test_13_transport_switch_rest(self):
        os.environ["RPCHAT_GEMINI_TRANSPORT"] = "rest"
        self.assertFalse(google_ai._should_use_sdk("google"))
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'
        resp.__enter__.return_value = resp
        with patch("google_ai.load_key", return_value=("AIzaSyDummyKey12345", "env")), \
             patch("urllib.request.urlopen", return_value=resp) as uo:
            st, data = google_ai.request("gemini-2.5-flash", {"contents": []}, provider="google")
            self.assertEqual(st, 200)
            uo.assert_called_once()

    # 14: Vertex に OFF を送らない／thinking と safety の段階救済
    def test_14_fallbacks_never_send_off_to_vertex(self):
        payload = {"contents": [],
                   "generationConfig": {"maxOutputTokens": 2048,
                                        "thinkingConfig": {"thinkingBudget": 128}},
                   "safetySettings": google_ai._safety_settings(True, provider="vertex")}
        seen = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            seen.append(copy.deepcopy(pl))
            if len(seen) == 1:
                raise google_ai.GoogleAPIError("Invalid thinkingConfig thinking_budget",
                                               status=400, retryable=False)
            if len(seen) == 2:
                raise google_ai.GoogleAPIError("Invalid safety threshold BLOCK_NONE",
                                               status=400, retryable=False)
            return 200, {"candidates": []}

        with patch("google_ai.request", side_effect=fake_req):
            st, _ = google_ai._request_with_fallbacks("gemini-2.5-pro", payload, 30, "vertex", True)
        self.assertEqual(st, 200)
        self.assertEqual(len(seen), 3)
        for pl in seen:
            for item in pl.get("safetySettings") or []:
                self.assertNotEqual(item.get("threshold"), "OFF")
        self.assertNotIn("thinkingConfig", seen[1]["generationConfig"])
        # F-7: BLOCK_NONE が通らない場合は BLOCK_ONLY_HIGH に落ちる（丸ごと外さない）
        self.assertEqual(seen[2]["safetySettings"][0]["threshold"], "BLOCK_ONLY_HIGH")

    # 15: generate(provider="vertex") の2段 fetch と thoughtSignature 保持
    def test_15_generate_vertex_e2e_preserves_thought_signature(self):
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                         project_id="demo-gcp-proj", location="global")
        step1 = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "thinking...", "thought": True, "thoughtSignature": "sig-xyz-999"},
            {"functionCall": {"name": "fetch_prompt", "args": {}}}]},
            "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"text":"こんにちは"}'}]}, "finishReason": "STOP"}]}
        captured = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            captured.append((provider, copy.deepcopy(pl)))
            return (200, step1) if len(captured) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "本文プロンプト", "システム指示",
                                     fetch=True, provider="vertex")
        self.assertIsNone(res.get("error"))          # F-9
        self.assertTrue(res.get("fetch_called"))
        self.assertIn("elapsed_ms", res)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0][0], "vertex")
        self.assertEqual(captured[1][1]["contents"][1]["parts"][0].get("thoughtSignature"),
                         "sig-xyz-999")

    # 16: 2段目 400（tools 起因）で直接生成へフォールバック
    def test_16_generate_step2_400_falls_back_to_direct(self):
        step1 = {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "fetch_prompt", "args": {}}}]},
            "finishReason": "STOP"}]}
        direct = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"text":"直接生成OK"}'}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            if len(calls) == 1:
                return 200, step1
            if len(calls) == 2:
                raise google_ai.GoogleAPIError("functionResponse not supported",
                                               status=400, retryable=False)
            return 200, direct

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-pro", "プロンプト", fetch=True, provider="vertex")
        self.assertIsNone(res.get("error"))          # F-9
        self.assertFalse(res.get("fetch_called"))
        self.assertEqual(len(calls), 3)

    # 17: list_models(vertex) で SDK 失敗 → REST 成功時にエラーが消える
    def test_17_list_models_clears_sdk_error_on_rest_success(self):
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                         project_id="demo-gcp-proj", location="global")
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = (b'{"publisherModels":[{"name":"publishers/google/models/gemini-2.5-flash",'
                                  b'"displayName":"Gemini 2.5 Flash"}]}')
        resp.__enter__.return_value = resp
        with patch("google_ai._should_use_sdk", return_value=True), \
             patch("google_ai._sdk_client", side_effect=RuntimeError("SDK list failed")), \
             patch("google_ai.get_vertex_access_token",
                   return_value=("tok", "service_account", "")), \
             patch("urllib.request.urlopen", return_value=resp):
            models = google_ai.list_models(refresh=True, provider="vertex")
        self.assertTrue(any(m["id"] == "gemini-2.5-flash" for m in models))
        self.assertEqual(google_ai.last_models_error(provider="vertex"), "")

    # 18: SDK 変換（署名検知・snake_case 拒否）
    def test_18_sdk_signature_and_snake_case_fallback(self):
        snake = MagicMock()
        snake.to_json_dict.return_value = {"candidates": [{"content": {"parts": [
            {"function_call": {"name": "fetch_prompt"}}]}, "finish_reason": "STOP"}]}
        self.assertIsNone(google_ai._sdk_response_to_dict(snake))
        camel = MagicMock()
        camel.to_json_dict.return_value = {"candidates": [{"content": {"parts": [
            {"text": "ok"}]}, "finishReason": "STOP"}]}
        self.assertIsNotNone(google_ai._sdk_response_to_dict(camel))

    # 19 (F-1): inflight キーが _google_pick_tasks と完全一致する
    def test_19_choices_inflight_key_matches_pick_tasks(self):
        sid = "sid_inflight_test"
        m = {"id": "a_new", "role": "assistant", "choices_status": "processing",
             "choices_type": "erotic", "choices_orig_id": "u_orig"}
        key = server._google_inflight_key(sid, "choices", m)
        self.assertEqual(key, f"{sid}:choices:erotic:u_orig")

        s = {"id": sid,
             "setting": {"ai_provider": "vertex", "vertex_model": "gemini-2.5-flash"},
             "messages": [m]}
        with patch("server.load", return_value=s), patch("server.list_ids", return_value=[sid]):
            tasks = server._google_pick_tasks()
        self.assertEqual([t[3] for t in tasks], [key])

        with server.google_inflight_guard:
            server.google_inflight.add(key)
        try:
            self.assertTrue(server._is_google_task_inflight(sid, "choices", m))
            self.assertEqual(server._google_inflight_key(sid, "user", {"id": "u1"}),
                             f"{sid}:user:u1")
            self.assertEqual(server._google_inflight_key(sid, "image_refine", {"id": "a1"}),
                             f"{sid}:image_refine:a1")
        finally:
            with server.google_inflight_guard:
                server.google_inflight.discard(key)

    # 20: processing → pending の巻き戻し
    def test_20_reset_session_api_tasks_to_pending(self):
        s = {"id": "sid_rst", "setting": {"ai_provider": "vertex"},
             "messages": [
                 {"id": "u1", "role": "user", "status": "processing",
                  "claimed_ts": "2026-01-01", "google_retry": "err"},
                 {"id": "a1", "role": "assistant", "choices_status": "processing",
                  "choices_claimed_ts": "2026-01-01"}]}
        server._reset_session_api_tasks_to_pending(s)
        self.assertEqual(s["messages"][0]["status"], "pending")
        self.assertNotIn("claimed_ts", s["messages"][0])
        self.assertNotIn("google_retry", s["messages"][0])
        self.assertEqual(s["messages"][1]["choices_status"], "pending")

    # 21 (2-7): memory_extract / consolidation が _google_fail で error になる
    def test_21_memory_task_fail_does_not_loop(self):
        s = {"id": "sid_mem", "setting": {"ai_provider": "vertex"},
             "messages": [{"id": "a1", "role": "assistant", "memory_status": "processing"},
                          {"id": "a2", "role": "assistant", "consolidation_status": "processing"}]}
        with patch("server.load", return_value=s), patch("server.save"):
            server._google_fail("sid_mem", "memory_extract", "a1", "unsupported", retryable=False)
            server._google_fail("sid_mem", "consolidation", "a2", "unsupported", retryable=False)
        self.assertEqual(s["messages"][0]["memory_status"], "error")
        self.assertEqual(s["messages"][1]["consolidation_status"], "error")

    # 22: app.js の checked() が code を伝搬する（静的検証）
    def test_22_app_js_checked_propagates_error_code(self):
        path = os.path.join(google_ai.ROOT, "public", "app.js")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                js = f.read()
            self.assertIn("result.code", js)
            self.assertIn("err.code", js)
            for needle in ("vertexStatusLoaded", "vertexModels", "syncGoogleSettingsUI",
                           "vertex:${", "vertex:"):
                self.assertIn(needle, js)

    # 23: _api_model_setting はネットワークを叩かない（ロック内で安全）
    def test_23_api_model_setting_has_no_network(self):
        st = {"ai_provider": "google", "google_model": "", "google_choices_model": ""}
        with patch("google_ai.list_models", side_effect=AssertionError("network called")):
            self.assertEqual(server._api_model_setting(st, "user"), "")
            self.assertEqual(server._api_model_setting(st, "choices"), "")

    # 24: 失効スイーパ保護（choices キー）が実際に効く
    def test_24_load_sweeper_skips_inflight_choices(self):
        sid = "sid_sweep"
        m = {"id": "a1", "role": "assistant", "text": "x",
             "choices_status": "processing", "choices_type": "normal",
             "choices_claimed_ts": "2000-01-01T00:00:00", "choices_ts": "2000-01-01T00:00:00"}
        key = server._google_inflight_key(sid, "choices", m)
        with server.google_inflight_guard:
            server.google_inflight.add(key)
        try:
            self.assertTrue(server._is_google_task_inflight(sid, "choices", m))
        finally:
            with server.google_inflight_guard:
                server.google_inflight.discard(key)
        self.assertFalse(server._is_google_task_inflight(sid, "choices", m))

    # 25: 1段目で functionCall が取れなかった場合に直接生成へフォールバック
    def test_25_generate_step1_no_fc_falls_back_to_direct(self):
        step1_no_fc = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "fetchは不要です"}]}, "finishReason": "STOP"}]}
        direct = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"text":"直接生成の返答"}'}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            return (200, step1_no_fc) if len(calls) == 1 else (200, direct)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "本文プロンプト", "システム指示",
                                     fetch=True, provider="vertex")
        self.assertIsNone(res.get("error"))
        self.assertFalse(res.get("fetch_called"))
        self.assertEqual(res.get("text"), '{"text":"直接生成の返答"}')
        self.assertEqual(len(calls), 2)

    # 26: .env ファイルに定義された GOOGLE_APPLICATION_CREDENTIALS の解決
    def test_26_adc_from_env_file(self):
        fake_adc = os.path.join(self.tmp.name, "my_adc.json")
        with open(fake_adc, "w", encoding="utf-8") as f:
            f.write("{}")
        google_ai._update_env_vars({"GOOGLE_APPLICATION_CREDENTIALS": fake_adc})
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        path = google_ai._default_adc_path()
        self.assertEqual(path, fake_adc)

    # 27: 秘密鍵にエスケープされた \\n が含まれていても _jwt_assertion が動作する
    def test_27_jwt_assertion_escaped_newlines(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = priv_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        sa = dict(SAMPLE_SA, private_key=pem.replace("\n", "\\n"))
        sig = google_ai._jwt_assertion(sa, google_ai.CLOUD_PLATFORM_SCOPE,
                                       google_ai.GOOGLE_TOKEN_URI, 1000000)
        self.assertIsInstance(sig, str)
        self.assertEqual(sig.count("."), 2)

    # 28: app.js の checked() が models や status を保持しつつエラー伝搬すること
    def test_28_app_js_checked_preserves_models_and_status(self):
        path = os.path.join(google_ai.ROOT, "public", "app.js")
        with open(path, "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("Array.isArray(result.models)", js)
        self.assertIn("result.status", js)

    # 29: server.py の Vertex AI HTTP エンドポイント動作
    def test_29_server_vertex_http_endpoints(self):
        class FakeHandler:
            def __init__(self, path, body=None, query=None):
                self.path = path
                self._body = body or {}
                self._query = query or {}
                self.status = 200
                self.result = None

            def blocked(self, u, post=False):
                return False

            def body(self):
                return self._body

            def query(self):
                return self._query

            def send_json(self, data, code=200):
                self.status = code
                self.result = data
                return data

            def send_error(self, code, msg=None):
                self.status = code
                self.result = {"error": msg or str(code)}

        # 1. GET /api/vertex/status (未設定)
        h = FakeHandler("/api/vertex/status")
        server.H.do_GET(h)
        self.assertEqual(h.status, 200)
        self.assertFalse(h.result.get("configured"))

        # 2. POST /api/vertex/config (不正 project_id -> 400)
        h = FakeHandler("/api/vertex/config", {"sa_json": json.dumps(SAMPLE_SA), "project_id": "123-bad"})
        server.H.do_POST(h)
        self.assertEqual(h.status, 400)
        self.assertIn("error", h.result)

        # 3. POST /api/vertex/config (正常保存)
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA), \
             patch("google_ai.get_vertex_access_token", return_value=("tok", "sa", "")), \
             patch("google_ai.list_models", return_value=[]), \
             patch("google_ai.probe_vertex_model", return_value={"id": "gemini-2.5-flash", "available": True, "status": 200, "error": ""}), \
             patch("google_ai.servable_vertex_models", return_value=[{"id": "gemini-2.5-flash", "available": True}]):
            h = FakeHandler("/api/vertex/config", {"sa_json": json.dumps(SAMPLE_SA), "project_id": "demo-gcp-proj", "location": "global"})
            server.H.do_POST(h)
        self.assertEqual(h.status, 200)
        self.assertTrue(h.result.get("ok"))
        self.assertTrue(h.result["status"]["configured"])

        # 3b. GET /api/vertex/models (probe=None の既定プローブ動作)
        with patch("google_ai.list_models", return_value=[{"id": "gemini-2.5-flash", "available": True}]) as mock_lm:
            h = FakeHandler("/api/vertex/models")
            server.H.do_GET(h)
            self.assertEqual(h.status, 200)
            self.assertTrue(h.result.get("probed"))
            mock_lm.assert_called_once_with(refresh=False, provider="vertex", probe=None)

        # 4. GET /api/vertex/status (設定後)
        h = FakeHandler("/api/vertex/status")
        server.H.do_GET(h)
        self.assertEqual(h.status, 200)
        self.assertTrue(h.result.get("configured"))
        self.assertNotIn("fingerprint", h.result)

        # 5. POST /api/ide/model (vertex モデル設定)
        s = server.create_session(setting={"title": "Endpoint Test"})
        sid = s["id"]
        try:
            h = FakeHandler("/api/ide/model", {"sid": sid, "model": "gemini-2.5-pro", "target": "main", "provider": "vertex"})
            server.H.do_POST(h)
            self.assertEqual(h.status, 200)
            with server.lock:
                cur_s = server.load(sid)
            self.assertEqual(cur_s["setting"]["vertex_model"], "gemini-2.5-pro")

            h = FakeHandler("/api/ide/model", {"sid": sid, "model": "gemini-2.5-flash-lite", "target": "choices", "provider": "vertex"})
            server.H.do_POST(h)
            self.assertEqual(h.status, 200)
            with server.lock:
                cur_s = server.load(sid)
            self.assertEqual(cur_s["setting"]["vertex_choices_model"], "gemini-2.5-flash-lite")

            # 6. POST /api/ai/provider (vertex へ切替)
            h = FakeHandler("/api/ai/provider", {"sid": sid, "provider": "vertex"})
            server.H.do_POST(h)
            self.assertEqual(h.status, 200)
            self.assertEqual(h.result["provider"], "vertex")
            self.assertEqual(h.result["model"], "gemini-2.5-pro")

            # 7. POST /api/vertex/config/delete (削除で Antigravity に戻る)
            h = FakeHandler("/api/vertex/config/delete", {})
            server.H.do_POST(h)
            self.assertEqual(h.status, 200)
            with server.lock:
                cur_s = server.load(sid)
            self.assertEqual(cur_s["setting"]["ai_provider"], "antigravity")
        finally:
            server.delete_session_all(sid, to_trash=False)

    # 30: _error_message がリスト形式のエラー JSON も処理できる
    def test_30_error_message_handles_json_list(self):
        raw = json.dumps([{"error": {"status": "INVALID_ARGUMENT", "message": "Field required"}}])
        msg, code = google_ai._error_message(400, raw)
        self.assertEqual(code, "INVALID_ARGUMENT")
        self.assertIn("Field required", msg)

    # 31: probe_vertex_model のステータス判定とキャッシュ制御
    def test_31_probe_vertex_model_statuses(self):
        cfg = {"project_id": "test-proj", "location": "global", "configured": True, "fingerprint": "fp1"}
        google_ai.invalidate_models_cache()

        # status 200 -> available=True, cached
        with patch("google_ai._vertex_rest_post", return_value=(200, {})):
            res = google_ai.probe_vertex_model("gemini-2.5-flash", cfg=cfg)
            self.assertTrue(res["available"])
            self.assertEqual(res["status"], 200)
            with google_ai._vertex_probe_lock:
                self.assertIn(("fp1", "global", "gemini-2.5-flash"), google_ai._vertex_probe_cache)

        # status 400 -> available=True, cached
        with patch("google_ai._vertex_rest_post", return_value=(400, {})):
            res = google_ai.probe_vertex_model("gemini-2.5-pro", cfg=cfg)
            self.assertTrue(res["available"])
            self.assertEqual(res["status"], 400)
            with google_ai._vertex_probe_lock:
                self.assertIn(("fp1", "global", "gemini-2.5-pro"), google_ai._vertex_probe_cache)

        # status 429 -> available=True, NOT cached
        google_ai.invalidate_models_cache()
        with patch("google_ai._vertex_rest_post", return_value=(429, {})):
            res = google_ai.probe_vertex_model("gemini-4-hydrogen", cfg=cfg)
            self.assertTrue(res["available"])
            self.assertEqual(res["status"], 429)
            with google_ai._vertex_probe_lock:
                self.assertNotIn(("fp1", "global", "gemini-4-hydrogen"), google_ai._vertex_probe_cache)

        # status 404 -> available=False, cached
        google_ai.invalidate_models_cache()
        import urllib.error
        err_404 = urllib.error.HTTPError("http://test", 404, "Not Found", {}, None)
        err_404.read = lambda: b'{"error": {"code": 404, "message": "Model not found"}}'
        with patch("google_ai._vertex_rest_post", side_effect=err_404):
            res = google_ai.probe_vertex_model("nonexistent-model", cfg=cfg)
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], 404)
            with google_ai._vertex_probe_lock:
                self.assertIn(("fp1", "global", "nonexistent-model"), google_ai._vertex_probe_cache)

        # status 403 -> available=False, cached
        google_ai.invalidate_models_cache()
        err_403 = urllib.error.HTTPError("http://test", 403, "Forbidden", {}, None)
        err_403.read = lambda: b'{"error": {"code": 403, "message": "Permission denied"}}'
        with patch("google_ai._vertex_rest_post", side_effect=err_403):
            res = google_ai.probe_vertex_model("forbidden-model", cfg=cfg)
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], 403)
            with google_ai._vertex_probe_lock:
                self.assertIn(("fp1", "global", "forbidden-model"), google_ai._vertex_probe_cache)

        # status 0 (network exception) -> available=False, NOT cached
        google_ai.invalidate_models_cache()
        with patch("google_ai._vertex_rest_post", side_effect=Exception("connection drop")):
            res = google_ai.probe_vertex_model("drop-model", cfg=cfg)
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], 0)
            with google_ai._vertex_probe_lock:
                self.assertNotIn(("fp1", "global", "drop-model"), google_ai._vertex_probe_cache)

    # 32: servable_vertex_models の無限再帰防止と利用可能モデルのフィルタ
    def test_32_servable_vertex_models_no_recursion_and_filters(self):
        cfg = {"project_id": "test-proj", "location": "global", "configured": True, "fingerprint": "fp1"}
        raw_models = [
            {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
            {"id": "gemini-404-model", "label": "Gemini 404 Model"},
        ]

        def fake_probe(model_id, loc, **kwargs):
            if model_id == "gemini-2.5-flash":
                return {"id": model_id, "available": True, "status": 200, "error": ""}
            return {"id": model_id, "available": False, "status": 404, "error": "Not Found"}

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai.effective_vertex_location", return_value="global"), \
             patch("google_ai.probe_vertex_model", side_effect=fake_probe), \
             patch.object(google_ai, "list_models", wraps=google_ai.list_models):
            # probe=None (既定) で呼び出したときに無限再帰せず、available=True のみ返ること
            models = google_ai.list_models(provider="vertex", probe=None)
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["id"], "gemini-2.5-flash")
            self.assertTrue(models[0]["available"])

        # 全滅時（全て available=False）はデバッグ用に全件返すこと
        def fake_probe_all_down(model_id, loc, **kwargs):
            return {"id": model_id, "available": False, "status": 404, "error": "Not Found"}

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai.effective_vertex_location", return_value="global"), \
             patch("google_ai.probe_vertex_model", side_effect=fake_probe_all_down):
            models = google_ai.servable_vertex_models(models=raw_models)
            self.assertEqual(len(models), 2)
            self.assertFalse(models[0]["available"])
            self.assertFalse(models[1]["available"])

        # 文字列リスト入力でも TypeError にならず正常処理されること
        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai.effective_vertex_location", return_value="global"), \
             patch("google_ai.probe_vertex_model", side_effect=fake_probe):
            models_str = google_ai.servable_vertex_models(models=["gemini-2.5-flash", "gemini-404-model"])
            self.assertEqual(len(models_str), 1)
            self.assertEqual(models_str[0]["id"], "gemini-2.5-flash")

    # 33: _is_thinking_model および思考モデル向け generationConfig
    def test_33_is_thinking_model(self):
        self.assertTrue(google_ai._is_thinking_model("gemini-2.5-flash"))
        self.assertTrue(google_ai._is_thinking_model("gemini-2.5-pro"))
        self.assertTrue(google_ai._is_thinking_model("gemini-3.7-flash"))
        self.assertTrue(google_ai._is_thinking_model("gemini-3-flash-preview"))
        self.assertTrue(google_ai._is_thinking_model("gemini-4-hydrogen"))
        self.assertTrue(google_ai._is_thinking_model("gemini-4.0-flash"))
        self.assertTrue(google_ai._is_thinking_model("gemini-2.0-flash-thinking-exp-0121"))
        self.assertTrue(google_ai._is_thinking_model("gemini-thinking-exp"))
        self.assertFalse(google_ai._is_thinking_model("gemini-2.0-flash"))
        self.assertFalse(google_ai._is_thinking_model("gemini-1.5-pro"))
        self.assertFalse(google_ai._is_thinking_model(""))
        self.assertFalse(google_ai._is_thinking_model(None))

        # step1 generationConfig
        c_flash = google_ai._step1_generation_config("gemini-2.5-flash")
        self.assertEqual(c_flash["maxOutputTokens"], 1024)
        self.assertEqual(c_flash["thinkingConfig"]["thinkingBudget"], 0)

        c_hydro = google_ai._step1_generation_config("gemini-4-hydrogen")
        self.assertEqual(c_hydro["maxOutputTokens"], 2048)
        self.assertEqual(c_hydro["thinkingConfig"]["thinkingBudget"], 128)

        c_20 = google_ai._step1_generation_config("gemini-2.0-flash")
        self.assertEqual(c_20["maxOutputTokens"], 256)
        self.assertNotIn("thinkingConfig", c_20)

        # step2 generationConfig
        c2 = google_ai._step2_generation_config("gemini-4-hydrogen", 4096, provider="vertex")
        self.assertGreaterEqual(c2["maxOutputTokens"], 16384)

        c2_non_vertex = google_ai._step2_generation_config("gemini-4-hydrogen", 4096, provider="google")
        self.assertEqual(c2_non_vertex["maxOutputTokens"], 4096)

    # 34: GCE メタデータフォールバック時の UnboundLocalError 防止
    def test_34_gce_metadata_token_no_unbound_local(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'{"access_token": "gce-token-xyz", "expires_in": 3600}'

        with patch("google_ai._vertex_sa_path", return_value=None), \
             patch("google_ai._default_adc_path", return_value=None), \
             patch("urllib.request.urlopen", return_value=FakeResponse()):
            tok, auth_type, quota, ttl = google_ai._fetch_vertex_token_unlocked(google_ai.CLOUD_PLATFORM_SCOPE)
            self.assertEqual(tok, "gce-token-xyz")
            self.assertEqual(auth_type, "adc")
            self.assertEqual(quota, "")
            self.assertEqual(ttl, 3600)

    # 35: default_vertex_model の優先順位と未プローブ時フォールバック
    def test_35_default_vertex_model_selection(self):
        cfg = {"project_id": "test-proj", "location": "global", "configured": True, "fingerprint": "fp_test"}
        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai.effective_vertex_location", return_value="global"):
            # キャッシュ未登録時は VERTEX_DEFAULT_MODEL ("gemini-2.5-flash") を返す
            google_ai.invalidate_models_cache()
            self.assertEqual(google_ai.default_vertex_model(), "gemini-2.5-flash")

            # gemini-4-hydrogen が利用可能なら VERTEX_PREFERRED_MODELS[0] として優先選択される
            with google_ai._vertex_probe_lock:
                google_ai._vertex_probe_cache[("fp_test", "global", "gemini-4-hydrogen")] = {
                    "available": True, "ts": time.time(), "id": "gemini-4-hydrogen", "location": "global"
                }
                google_ai._vertex_probe_cache[("fp_test", "global", "gemini-2.5-flash")] = {
                    "available": True, "ts": time.time(), "id": "gemini-2.5-flash", "location": "global"
                }
            self.assertEqual(google_ai.default_vertex_model(), "gemini-4-hydrogen")

            # gemini-4-hydrogen が利用不可 (404) のときは gemini-2.5-flash が選ばれる
            with google_ai._vertex_probe_lock:
                google_ai._vertex_probe_cache[("fp_test", "global", "gemini-4-hydrogen")]["available"] = False
            self.assertEqual(google_ai.default_vertex_model(), "gemini-2.5-flash")

            # 優先リスト外の独自モデルのみが利用可能な場合はソート順で先頭が選ばれる
            with google_ai._vertex_probe_lock:
                google_ai._vertex_probe_cache.clear()
                google_ai._vertex_probe_cache[("fp_test", "global", "custom-model-z")] = {
                    "available": True, "ts": time.time(), "id": "custom-model-z", "location": "global"
                }
            self.assertEqual(google_ai.default_vertex_model(), "custom-model-z")

    # 36: SDK 404 時の REST global 切替と _vertex_location_override の SDK への伝播
    def test_36_sdk_404_rest_global_fallback_and_override(self):
        cfg = {"project_id": "test-proj", "location": "us-central1", "configured": True, "fingerprint": "fp_reg"}
        google_ai.invalidate_vertex_token()
        google_ai._clear_vertex_location_override()

        from google.genai import errors as genai_errors
        fake_sdk_404 = genai_errors.APIError(404, "404 NOT_FOUND. {'error': {'code': 404, 'message': 'Model not found in us-central1'}}")

        import urllib.error
        err_404 = urllib.error.HTTPError("http://test-us", 404, "Not Found", {}, None)
        err_404.read = lambda: b'{"error": {"code": 404, "message": "Model not found in us-central1"}}'

        class FakeRestSuccess:
            status = 200
            def read(self):
                return b'{"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def fake_urlopen(req, timeout=None):
            if "us-central1" in req.full_url:
                raise err_404
            if "global" in req.full_url or "aiplatform.googleapis.com" in req.full_url:
                return FakeRestSuccess()
            raise RuntimeError(f"Unexpected URL: {req.full_url}")

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai._should_use_sdk", return_value=True), \
             patch("google_ai._sdk_generate", side_effect=[(None, None), (200, {"candidates": [{"content": {"parts": [{"text": "from-sdk-global"}]}}]})]), \
             patch("google_ai.get_vertex_access_token", return_value=("tok", "sa", "")), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):

            # 1回目: SDK が 404 で None, None -> REST us-central1 (404) -> REST global (200)
            status, data = google_ai.request("gemini-4-hydrogen", {"contents": []}, provider="vertex")
            self.assertEqual(status, 200)

            # global で成功したため location override が記録されていること
            self.assertEqual(google_ai.effective_vertex_location(cfg), "global")

            # 2回目: SDK は effective_vertex_location を使うため global 向けクライアントになり成功
            status2, data2 = google_ai.request("gemini-4-hydrogen", {"contents": []}, provider="vertex")
            self.assertEqual(status2, 200)
            self.assertEqual(data2["candidates"][0]["content"]["parts"][0]["text"], "from-sdk-global")

        # global でも 404 の場合は override が残らず例外になること
        google_ai._clear_vertex_location_override()
        err_global_404 = urllib.error.HTTPError("http://test-global", 404, "Not Found", {}, None)
        err_global_404.read = lambda: b'{"error": {"code": 404, "message": "Model not found in global"}}'

        def fake_urlopen_all_404(req, timeout=None):
            raise err_global_404

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai._should_use_sdk", return_value=True), \
             patch("google_ai._sdk_generate", return_value=(None, None)), \
             patch("google_ai.get_vertex_access_token", return_value=("tok", "sa", "")), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen_all_404):
            with self.assertRaises(google_ai.GoogleAPIError) as cm:
                google_ai.request("gemini-4-hydrogen", {"contents": []}, provider="vertex")
            self.assertEqual(cm.exception.status, 404)
            # 誤った override が残っていないこと
            self.assertEqual(google_ai.effective_vertex_location(cfg), "us-central1")

    # 37: SDK bytes/Enum サニタイズおよび json.dumps default
    def test_37_sdk_sanitize_bytes_and_enum(self):
        import enum

        class DummyFinishReason(enum.Enum):
            STOP = "STOP"
            MAX_TOKENS = "MAX_TOKENS"

        # 1. _sanitize_sdk_value 単体テスト
        val_bytes = b"my_signature_bytes_123"
        sanitized = google_ai._sanitize_sdk_value({
            "thoughtSignature": val_bytes,
            "finishReason": DummyFinishReason.STOP,
            "nested": [{"data": b"nested_bytes"}]
        })
        self.assertEqual(sanitized["thoughtSignature"], base64.b64encode(val_bytes).decode("ascii"))
        self.assertEqual(sanitized["finishReason"], "STOP")
        self.assertEqual(sanitized["nested"][0]["data"], base64.b64encode(b"nested_bytes").decode("ascii"))

        # 2. _sdk_response_to_dict での bytes/Enum サニタイズ
        mock_resp = MagicMock()
        mock_resp.to_json_dict.return_value = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "thinking...", "thought": True, "thoughtSignature": val_bytes}
                    ]
                },
                "finishReason": DummyFinishReason.STOP,
            }]
        }
        res_dict = google_ai._sdk_response_to_dict(mock_resp)
        self.assertIsNotNone(res_dict)
        c0 = res_dict["candidates"][0]
        self.assertEqual(c0["finishReason"], "STOP")
        part0 = c0["content"]["parts"][0]
        self.assertEqual(part0["thoughtSignature"], base64.b64encode(val_bytes).decode("ascii"))

        # 3. snake_case ガードに "thought_signature" が効いていること
        mock_snake_resp = MagicMock()
        mock_snake_resp.to_json_dict.return_value = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "thinking...", "thought": True, "thought_signature": val_bytes}
                    ]
                },
                "finishReason": "STOP",
            }]
        }
        self.assertIsNone(google_ai._sdk_response_to_dict(mock_snake_resp))

        # 4. _json_default が bytes と Enum を直列化できること
        val_ba = bytearray(b"bytearray_123")
        dumped = json.dumps({
            "sig": val_bytes,
            "ba": val_ba,
            "reason": DummyFinishReason.MAX_TOKENS,
            "tags": {"tagA", "tagB"},
        }, default=google_ai._json_default)
        loaded = json.loads(dumped)
        self.assertEqual(loaded["sig"], base64.b64encode(val_bytes).decode("ascii"))
        self.assertEqual(loaded["ba"], base64.b64encode(b"bytearray_123").decode("ascii"))
        self.assertEqual(loaded["reason"], "MAX_TOKENS")
        self.assertEqual(set(loaded["tags"]), {"tagA", "tagB"})

    # 38: probe_vertex_model の 404 時 global 自動フォールバック
    def test_38_probe_vertex_model_404_global_fallback(self):
        cfg = {"project_id": "test-proj", "location": "us-central1", "configured": True, "fingerprint": "fp_probe"}
        google_ai.invalidate_models_cache()
        google_ai._clear_vertex_location_override()

        def fake_rest_post(loc, model, payload, timeout=10, cfg=None):
            if loc == "us-central1":
                import urllib.error
                err = urllib.error.HTTPError("http://us", 404, "Not Found", {}, None)
                err.read = lambda: b'{"error": {"code": 404, "message": "Model not found in us-central1"}}'
                raise err
            if loc == "global":
                return 200, {"candidates": [{"content": {"parts": [{"text": "pong"}]}}]}
            raise RuntimeError(f"Unexpected loc: {loc}")

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai._vertex_rest_post", side_effect=fake_rest_post):
            # us-central1 で 404 だが global で 200 -> global にフォールバックして available=True
            res = google_ai.probe_vertex_model("gemini-4-hydrogen", location="us-central1", cfg=cfg)
            self.assertTrue(res["available"])
            self.assertEqual(res["location"], "global")
            self.assertEqual(res["status"], 200)

            # location override が記録されていること
            self.assertEqual(google_ai.effective_vertex_location(cfg), "global")

            # 2回目呼び出し（キャッシュヒット時）も available=True (global) が一貫して返ること（キャッシュ汚染防止）
            res2 = google_ai.probe_vertex_model("gemini-4-hydrogen", location="us-central1", cfg=cfg)
            self.assertTrue(res2["available"])
            self.assertEqual(res2["location"], "global")
            self.assertEqual(res2["status"], 200)

        # global でも 404 の場合は override されず available=False になること
        google_ai._clear_vertex_location_override()
        google_ai.invalidate_models_cache()

        def fake_rest_post_all_404(loc, model, payload, timeout=10, cfg=None):
            import urllib.error
            err = urllib.error.HTTPError(f"http://{loc}", 404, "Not Found", {}, None)
            err.read = lambda: b'{"error": {"code": 404, "message": "Not found"}}'
            raise err

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai._vertex_rest_post", side_effect=fake_rest_post_all_404):
            res = google_ai.probe_vertex_model("gemini-4-hydrogen", location="us-central1", cfg=cfg)
            self.assertFalse(res["available"])
            self.assertEqual(res["status"], 404)
            self.assertEqual(google_ai.effective_vertex_location(cfg), "us-central1")

    # 39: generate() 末尾の INTERNAL_ERROR ガード
    def test_39_generate_internal_error_guard(self):
        with patch("google_ai.configured", return_value=True), \
             patch("google_ai.request", side_effect=TypeError("unexpected typing crash")):
            res = google_ai.generate("gemini-2.5-flash", "プロンプト", provider="google")
            self.assertEqual(res["code"], "INTERNAL_ERROR")
            self.assertEqual(res["http_status"], 500)
            self.assertIn("内部エラー", res["error"])
            self.assertFalse(res["retryable"])

    # 40: _is_tool_400 の自然言語スペース区切り判定
    def test_40_is_tool_400_natural_language(self):
        self.assertTrue(google_ai._is_tool_400("Error: function call execution failed"))
        self.assertTrue(google_ai._is_tool_400("Missing function response in turn"))
        self.assertTrue(google_ai._is_tool_400("Thought signature mismatch"))
        self.assertTrue(google_ai._is_tool_400("Invalid tool config for mode"))
        self.assertTrue(google_ai._is_tool_400("Empty function declarations provided"))
        self.assertFalse(google_ai._is_tool_400("Quota limit exceeded"))
        self.assertFalse(google_ai._is_tool_400("Internal server error 500"))

    # 41: list_models と last_models_error の effective_vertex_location 統一
    def test_41_list_models_and_last_error_effective_location(self):
        cfg = {"project_id": "test-proj", "location": "us-central1", "configured": True, "fingerprint": "fp_eff"}
        google_ai.invalidate_models_cache()
        google_ai._set_vertex_location_override("fp_eff", "global")

        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"publisherModels": [{"name": "publishers/google/models/gemini-2.5-flash"}]}'
        resp.__enter__.return_value = resp

        requested_urls = []
        def fake_urlopen(req, timeout=None):
            requested_urls.append(req.full_url)
            return resp

        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai._should_use_sdk", return_value=False), \
             patch("google_ai.get_vertex_access_token", return_value=("tok", "sa", "")), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            models = google_ai.list_models(refresh=True, provider="vertex", probe=False)
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["id"], "gemini-2.5-flash")
            # リクエスト先 URL が global ホストになっていること
            self.assertTrue(any("aiplatform.googleapis.com" in u and "us-central1" not in u for u in requested_urls))

            # キャッシュキーが ("vertex", "fp_eff", "test-proj", "global") で引けること
            with google_ai._models_lock:
                ck = ("vertex", "fp_eff", "test-proj", "global")
                self.assertIn(ck, google_ai._models_cache_by_key)

            # last_models_error も同じキーを参照してエラーなしを返すこと
            self.assertEqual(google_ai.last_models_error(provider="vertex"), "")

    # 42: _sdk_client の cfg=None ガード
    def test_42_sdk_client_cfg_none_guard(self):
        cfg = {"project_id": "demo-gcp-proj", "location": "asia-east1", "configured": True, "fingerprint": "fp_cfg_none"}
        with patch("google_ai.load_vertex_config", return_value=cfg), \
             patch("google_ai.effective_vertex_location", return_value="asia-east1"), \
             patch("google_ai._should_use_sdk", return_value=True):
            with google_ai._sdk_client_lock:
                google_ai._sdk_client_cache.clear()
            # cfg を渡さない (None) でも TypeError にならずクライアントが取得できること
            cli = google_ai._sdk_client("vertex", None)
            self.assertIsNotNone(cli)

            # 部分 dict (fingerprint 等の欠落) でも KeyError にならずクライアントが取得できること
            cli_partial = google_ai._sdk_client("vertex", {"project_id": "demo-partial", "location": "global"})
            self.assertIsNotNone(cli_partial)


if __name__ == "__main__":
    unittest.main()

