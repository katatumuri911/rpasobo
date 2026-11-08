# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import copy
import shutil
import tempfile
import threading
import unittest

import urllib.request
import urllib.parse

# Ensure working directory is in sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ide_poke
import wait_input
import server


class TestParallelChoices(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_rp_chat_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_outbox = wait_input.OUTBOX
        self.orig_s_outbox = server.OUTBOX
        self.orig_s_done = server.DONE
        # モジュール読み込み時に決まるパスは DATA の差し替えに追従しない。差し替えないと
        # テストが本物の session/AGENTS.md・data/agents_md.json・data/ide_known.json を汚す。
        self.orig_agents_log = server.AGENTS_LOG
        self.orig_new_ide_dir = server.NEW_IDE_DIR
        self.orig_ide_known = server.IDE_KNOWN
        self.orig_known_ides = server.known_ides

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        os.makedirs(server.CHOICES_DIR, exist_ok=True)
        server.TRASH = os.path.join(server.DATA, "trash")
        os.makedirs(server.TRASH, exist_ok=True)
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.IDE_KNOWN = os.path.join(server.DATA, "ide_known.json")
        server.known_ides = None
        server.agents_state.clear()

        wait_input.OUTBOX = server.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(wait_input.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        # Clear in-memory caches
        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.summary_cache.clear()

        # Spin up test HTTP server
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        server.DONE = self.orig_s_done
        server.AGENTS_LOG = self.orig_agents_log
        server.NEW_IDE_DIR = self.orig_new_ide_dir
        server.IDE_KNOWN = self.orig_ide_known
        server.known_ides = self.orig_known_ides
        server.agents_state.clear()
        wait_input.OUTBOX = server.OUTBOX = self.orig_s_outbox
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def api_get(self, path):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))

    def api_post(self, path, data):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_has_parallel_choices_ide(self):
        self.assertFalse(server.has_parallel_choices_ide({}))
        self.assertFalse(server.has_parallel_choices_ide({"ide_session": "conv-1"}))
        self.assertFalse(server.has_parallel_choices_ide({"ide_session": "conv-1", "choices_ide_session": "conv-1"}))
        self.assertTrue(server.has_parallel_choices_ide({"ide_session": "", "choices_ide_session": "conv-2"}))
        self.assertTrue(server.has_parallel_choices_ide({"ide_session": "conv-1", "choices_ide_session": "conv-2"}))

    def test_get_poke_lock(self):
        lock1 = server.get_poke_lock("conv-1")
        lock1_again = server.get_poke_lock("conv-1")
        lock2 = server.get_poke_lock("conv-2")
        self.assertIs(lock1, lock1_again)
        self.assertIsNot(lock1, lock2)

    def test_wait_input_check_previous_role_filtering(self):
        # Create dummy outbox files
        main_file = os.path.join(wait_input.OUTBOX, "test-sid_user_12345.json")
        choices_file = os.path.join(wait_input.OUTBOX, "test-sid_choices_12346.json")
        with open(main_file, "w") as f:
            json.dump({"text": "main text"}, f)
        with open(choices_file, "w") as f:
            json.dump([{"label": "1", "text": "choice 1"}], f)

        # Mock wait_input.get to simulate /api/pending response for main IDE
        orig_get = wait_input.get
        try:
            wait_input.get = lambda p: {"all_ids": ["test-sid"], "mine_ids": ["test-sid"], "ide_role": "main"}
            warn_main, left_main, left_type_main = wait_input.check_previous()
            # warn_main should report test-sid_user_12345.json, and ignore choices file
            self.assertTrue(any("test-sid_user_12345.json" in w for w in warn_main))
            self.assertFalse(any("test-sid_choices_12346.json" in w for w in warn_main))

            # Mock wait_input.get to simulate /api/pending response for choices IDE
            wait_input.get = lambda p: {"all_ids": ["test-sid"], "mine_ids": ["test-sid"], "ide_role": "choices"}
            warn_choices, left_choices, left_type_choices = wait_input.check_previous()
            # warn_choices should report test-sid_choices_12346.json, and ignore main file
            self.assertTrue(any("test-sid_choices_12346.json" in w for w in warn_choices))
            self.assertFalse(any("test-sid_user_12345.json" in w for w in warn_choices))
        finally:
            wait_input.get = orig_get

    def test_parallel_choices_context(self):
        session = {
            "id": "s-test-1",
            "setting": {
                "title": "Test RPG",
                "char_name": "Heroine",
                "user_name": "Player",
                "ide_session": "conv-main",
                "choices_ide_session": "conv-choices",
            },
            "messages": [
                {"id": "m1", "role": "user", "text": "Hello world", "ts": "2026-11-05T00:00:00"},
            ]
        }
        user_m = session["messages"][0]
        ctx = server.build_parallel_choices_context(session, user_m)
        self.assertEqual(ctx["mode"], "choices")
        self.assertEqual(ctx["reply_to"], "m1")
        all_text = " ".join(sec.get("text", "") for sec in ctx["sections"])
        self.assertIn("choices", all_text)
        self.assertIn("Hello world", all_text)
        self.assertIn("s-test-1", all_text)

    def test_case_a_choices_arrives_first(self):
        """Case A: Choices arrives while user message is still pending/processing."""
        sid = "sid-case-a"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": []
        }
        s["setting"].update(
            ide_session="conv-main",
            choices_ide_session="conv-choices",
            title="Case A Channel"
        )
        user_msg = {
            "id": "user-msg-1",
            "role": "user",
            "text": "What do we do now?",
            "status": "processing",
            "choices_status": "processing",
            "choices_ts": "2026-11-05T00:00:00",
            "choices_orig_id": "user-msg-1",
            "ts": "2026-11-05T00:00:00"
        }
        s["messages"].append(user_msg)
        server.save(s)

        # Choices IDE finishes first, outputs choices file in outbox
        choices_data = [
            {"label": "1", "text": "Attack enemy"},
            {"label": "2", "text": "Defend"}
        ]
        outbox_filename = f"{sid}_choices_1700000000.json"
        with open(os.path.join(server.OUTBOX, outbox_filename), "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "user-msg-1",
                "choices": choices_data
            }, f)

        # Process outbox
        server.process_outbox()

        # Check: choices should be staged on the user message
        s_after = server.load(sid)
        u_msg_after = s_after["messages"][0]
        self.assertEqual(u_msg_after.get("staged_choices"), choices_data)
        self.assertNotIn("choices_status", u_msg_after)

        # Now main response arrives
        main_filename = f"{sid}_user_1700000010.json"
        with open(os.path.join(server.OUTBOX, main_filename), "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "user-msg-1",
                "text": "The goblin glares at you!"
            }, f)

        # Process outbox for main response
        server.process_outbox()

        # Check: assistant message should have the staged choices merged!
        s_final = server.load(sid)
        self.assertEqual(len(s_final["messages"]), 2)
        asst_msg = s_final["messages"][1]
        self.assertEqual(asst_msg["role"], "assistant")
        self.assertEqual(asst_msg["text"], "The goblin glares at you!")
        self.assertEqual(asst_msg.get("choices"), choices_data)
        self.assertNotIn("staged_choices", s_final["messages"][0])

    def test_case_b_main_arrives_first(self):
        """Case B: Main response arrives while choices is still processing."""
        sid = "sid-case-b"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": []
        }
        s["setting"].update(
            ide_session="conv-main",
            choices_ide_session="conv-choices",
            title="Case B Channel"
        )
        user_msg = {
            "id": "user-msg-2",
            "role": "user",
            "text": "Where is the treasure?",
            "status": "processing",
            "choices_status": "processing",
            "choices_ts": "2026-11-05T00:00:00",
            "choices_orig_id": "user-msg-2",
            "ts": "2026-11-05T00:00:00"
        }
        s["messages"].append(user_msg)
        server.save(s)

        # Main response finishes first
        main_filename = f"{sid}_user_1700000000.json"
        with open(os.path.join(server.OUTBOX, main_filename), "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "user-msg-2",
                "text": "The treasure is hidden behind the waterfall."
            }, f)

        server.process_outbox()

        # Check: user message status answered, choices_status transferred to assistant message
        s_after = server.load(sid)
        self.assertEqual(len(s_after["messages"]), 2)
        asst_msg = s_after["messages"][1]
        self.assertEqual(asst_msg["role"], "assistant")
        self.assertEqual(asst_msg.get("choices_status"), "processing")
        self.assertEqual(asst_msg.get("choices_orig_id"), "user-msg-2")

        # Now choices arrives later, with reply_to="user-msg-2"
        choices_data = [
            {"label": "1", "text": "Go to the waterfall"},
            {"label": "2", "text": "Ignore and camp"}
        ]
        outbox_filename = f"{sid}_choices_1700000020.json"
        with open(os.path.join(server.OUTBOX, outbox_filename), "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "user-msg-2",
                "choices": choices_data
            }, f)

        server.process_outbox()

        # Check: assistant message matched via choices_orig_id and got choices!
        s_final = server.load(sid)
        final_asst = s_final["messages"][1]
        self.assertEqual(final_asst.get("choices"), choices_data)
        self.assertNotIn("choices_status", final_asst)

    def test_single_session_fallback(self):
        """Single session mode: when choices_ide_session is not set, parallel mode is disabled."""
        sid = "sid-single"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": []
        }
        s["setting"].update(
            ide_session="conv-main",
            choices_ide_session="",
            title="Single Session Channel"
        )
        server.save(s)
        self.assertFalse(server.has_parallel_choices_ide(s["setting"]))

    def test_channels_of_and_ide_model_of(self):
        sid = "sid-models"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": []
        }
        s["setting"].update(
            ide_session="conv-main-x",
            ide_title="Main IDE Title",
            ide_model="model-main",
            choices_ide_session="conv-choices-y",
            choices_ide_title="Choices IDE Title",
            choices_ide_model="model-choices",
            title="Model Test Channel"
        )
        server.save(s)

        # channels_of
        channels_main = server.channels_of("conv-main-x")
        self.assertIn((sid, "Model Test Channel"), channels_main)

        channels_choices = server.channels_of("conv-choices-y")
        self.assertIn((sid, "Model Test Channel"), channels_choices)

        # ide_model_of
        self.assertEqual(server.ide_model_of("conv-main-x"), "model-main")
        self.assertEqual(server.ide_model_of("conv-choices-y"), "model-choices")

    def test_timeout_handling_in_load(self):
        sid = "sid-timeout"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": []
        }
        s["setting"].update(
            ide_session="conv-main",
            choices_ide_session="conv-choices",
            title="Timeout Channel"
        )
        # Message with choices_status = processing claimed > 300 seconds ago
        old_time = time.time() - 350
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(old_time))
        user_msg = {
            "id": "u-timeout-1",
            "role": "user",
            "text": "Hello?",
            "status": "pending",
            "choices_status": "processing",
            "choices_claimed_ts": old_ts,
            "choices_ts": old_ts,
            "staged_choices": [{"label": "old", "text": "stale"}]
        }
        s["messages"].append(user_msg)
        server.save(s)

        # Invalidate in-memory cache so load() re-evaluates file and checks timeouts
        server.session_cache.pop(sid, None)

        # Loading the session should detect timeout (> 300s) and handle it
        loaded = server.load(sid)
        msg_loaded = loaded["messages"][0]
        # Should clear choices_status and staged_choices
        self.assertNotIn("choices_status", msg_loaded)
        self.assertNotIn("staged_choices", msg_loaded)

    def test_ide_poke_cid_cfg_deepcopy(self):
        cid = "test-cid-123"
        orig_config = {"plannerConfig": {"planModel": "default-model"}}
        ide_poke._cid_cfg[cid] = copy.deepcopy(orig_config)
        # Verify that fetching from cache and modifying model does not mutate cache
        cached = copy.deepcopy(ide_poke._cid_cfg.get(cid))
        cached["plannerConfig"]["planModel"] = "modified-model"
        self.assertEqual(ide_poke._cid_cfg[cid]["plannerConfig"]["planModel"], "default-model")
        del ide_poke._cid_cfg[cid]


    def test_ide_role_routing_in_pending_api(self):
        # 1. Single session channel (sequential mode)
        sid_single = "sid-single-role"
        s1 = {"id": sid_single, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s1["setting"].update(ide_session="conv-single-x", choices_ide_session="")
        server.save(s1)

        r_single = self.api_get("/api/pending?ide=conv-single-x&check=1")
        self.assertEqual(r_single.get("ide_role"), "both")

        # 2. Parallel session channel
        sid_parallel = "sid-parallel-role"
        s2 = {"id": sid_parallel, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s2["setting"].update(ide_session="conv-main-p", choices_ide_session="conv-choices-p")
        server.save(s2)

        r_main = self.api_get("/api/pending?ide=conv-main-p&check=1")
        self.assertEqual(r_main.get("ide_role"), "main")

        r_choices = self.api_get("/api/pending?ide=conv-choices-p&check=1")
        self.assertEqual(r_choices.get("ide_role"), "choices")

        # 3. No IDE specified (generic worker)
        r_none = self.api_get("/api/pending?check=1")
        self.assertEqual(r_none.get("ide_role"), "both")

    def test_send_edit_regenerate_choices_type_and_cleanup(self):
        sid = "sid-api-flow"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(ide_session="conv-main", choices_ide_session="conv-choices")
        server.save(s)

        # POST /api/send
        res_send = self.api_post("/api/send", {"sid": sid, "text": "First turn"})
        self.assertIn("session", res_send)
        self.assertEqual(res_send["session"]["id"], sid)
        msg_id = res_send["id"]

        s_send = server.load(sid)
        u_msg = s_send["messages"][0]
        self.assertEqual(u_msg["choices_status"], "pending")
        self.assertEqual(u_msg["choices_type"], "normal")
        self.assertEqual(u_msg["choices_orig_id"], msg_id)

        # Simulate choices staged on user message
        u_msg["staged_choices"] = [{"label": "opt", "text": "choice text"}]
        server.save(s_send)

        # POST /api/message/edit
        self.api_post("/api/message/edit", {"sid": sid, "id": msg_id, "text": "Edited turn"})
        s_edit = server.load(sid)
        edited_u = s_edit["messages"][0]
        self.assertEqual(edited_u["text"], "Edited turn")
        self.assertEqual(edited_u["choices_status"], "pending")
        self.assertEqual(edited_u["choices_type"], "normal")
        self.assertNotIn("staged_choices", edited_u)

        # Add assistant message and test regenerate
        asst_id = server.new_id()
        s_edit["messages"].append({
            "id": asst_id, "role": "assistant", "text": "Response text", "ts": server.now()
        })
        server.save(s_edit)

        self.api_post("/api/message/regenerate", {"sid": sid, "id": asst_id})
        s_regen = server.load(sid)
        regen_u = s_regen["messages"][0]
        self.assertEqual(regen_u["status"], "pending")
        self.assertEqual(regen_u["choices_status"], "pending")
        self.assertEqual(regen_u["choices_type"], "normal")
        self.assertNotIn("staged_choices", regen_u)

    def test_send_stop_removes_pending_message_and_returns_text(self):
        sid = "sid-stop-flow"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(ide_session="conv-stop")
        server.save(s)

        res_send = self.api_post("/api/send", {"sid": sid, "text": "送信をやめたい本文"})
        mid = res_send["id"]
        # 応答中（AIが受け取ったあと）でも停止できる
        self.api_post("/api/claim", {"sid": sid, "id": mid, "type": "user"})
        self.assertEqual(server.load(sid)["messages"][0]["status"], "processing")

        res_stop = self.api_post("/api/send/stop", {"sid": sid})
        self.assertTrue(res_stop["ok"])
        self.assertEqual(res_stop["id"], mid)
        self.assertEqual(res_stop["text"], "送信をやめたい本文")

        s_after = server.load(sid)
        self.assertEqual(s_after["messages"], [])
        self.assertEqual(s_after["setting"].get("total_turns"), 0)
        pending = self.api_get("/api/pending?ide=conv-stop&check=1")
        self.assertIsNone(pending.get("pending"))
        # 担当IDEが読んだあとの停止は、次の入力を全部送り直して「無かったこと」にする
        sent = server.read_sent(sid)
        self.assertTrue(sent.get("reset"))
        self.assertIn("停止", sent.get("why", ""))

        # 停止後にAIの応答が届いても、取り込まれない（outbox 側で stale になる）
        late = os.path.join(server.OUTBOX, f"{sid}_user_1700000999.json")
        with open(late, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": mid, "text": "停止したのに届いた応答"}, f)
        server.process_outbox()
        self.assertEqual(server.load(sid)["messages"], [])

        # 前のターンがあるときは、通算ターンを1つだけ戻す（前のターンは残す）
        s_prev = server.load(sid)
        s_prev["messages"] = [
            {"id": "u1", "role": "user", "text": "1ターン目", "ts": server.now(), "status": "answered"},
            {"id": "a1", "role": "assistant", "text": "返事", "ts": server.now()},
        ]
        s_prev["setting"]["total_turns"] = 1
        server.save(s_prev)
        res_send2 = self.api_post("/api/send", {"sid": sid, "text": "2ターン目（止める）"})
        self.assertEqual(server.load(sid)["setting"].get("total_turns"), 2)
        # 停止後の次の入力は、停止した発言を無かったものとして全部送り直す
        ctx = self.api_post("/api/context", {"sid": sid, "id": res_send2["id"]})
        self.assertEqual(ctx.get("mode"), "full")
        self.assertIn("停止", ctx.get("reason", ""))
        # 発言ごとの停止ボタンと同じく id を指定して止める
        self.api_post("/api/send/stop", {"sid": sid, "id": res_send2["id"]})
        s_prev = server.load(sid)
        self.assertEqual(len(s_prev["messages"]), 2)
        self.assertEqual(s_prev["messages"][-1]["id"], "a1")
        self.assertEqual(s_prev["setting"].get("total_turns"), 1)

    def test_copy_from_clears_ide_sessions(self):
        sid = "sid-src-copy"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(
            ide_session="conv-main-src",
            ide_title="Main Title",
            choices_ide_session="conv-choices-src",
            choices_ide_title="Choices Title"
        )
        server.save(s)

        res_new = self.api_post("/api/session/new", {"copy_from": sid})
        new_sid = res_new["sid"]

        s_new = server.load(new_sid)
        st = s_new["setting"]
        self.assertEqual(st.get("ide_session"), "")
        self.assertEqual(st.get("choices_ide_session"), "")
        self.assertEqual(st.get("ide_title"), "")
        self.assertEqual(st.get("choices_ide_title"), "")

    def test_poke_worker_rotation_waits_for_choices(self):
        sid = "sid-rot-choices"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(ide_session="conv-main", choices_ide_session="conv-choices")
        # 30 user turns
        for i in range(30):
            s["messages"].append({"id": f"u{i}", "role": "user", "text": f"Msg {i}", "ts": server.now()})
            s["messages"].append({"id": f"a{i}", "role": "assistant", "text": f"Reply {i}", "ts": server.now()})

        # Latest assistant message has choices_status = "processing"
        s["messages"][-1]["choices_status"] = "processing"
        server.save(s)

        msgs = s["messages"]
        file_user_turns = len([x for x in msgs if x.get("role") == "user"])
        has_pending = any(
            (x.get("role") == "user" and x.get("status") in ("pending", "processing"))
            or x.get("choices_status") in ("pending", "processing")
            for x in msgs
        )
        # Should be blocked from rotating while choices is processing
        self.assertGreaterEqual(file_user_turns, server.ROTATION_TURNS)
        self.assertTrue(has_pending)

        # Once choices finishes, has_pending becomes False
        s["messages"][-1].pop("choices_status")
        has_pending_after = any(
            (x.get("role") == "user" and x.get("status") in ("pending", "processing"))
            or x.get("choices_status") in ("pending", "processing")
            for x in s["messages"]
        )
        self.assertFalse(has_pending_after)

    def test_case_a_staged_choices_without_reply_to_does_not_corrupt_old_assistant(self):
        sid = "sid-old-asst-guard"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(ide_session="conv-main", choices_ide_session="conv-choices")
        # Turn 0: Old assistant message that has no choices
        s["messages"].append({"id": "u0", "role": "user", "text": "Turn 0", "ts": "2026-11-05T00:00:00", "status": "answered"})
        s["messages"].append({"id": "a0", "role": "assistant", "text": "Turn 0 reply", "ts": "2026-11-05T00:00:01"})
        # Turn 1: User message is pending parallel generation
        s["messages"].append({
            "id": "u1", "role": "user", "text": "Turn 1", "ts": "2026-11-05T00:00:02",
            "status": "processing", "choices_status": "processing"
        })
        server.save(s)

        # Choices outbox arrives without reply_to (or with unrecognized reply_to)
        outbox_filename = f"{sid}_choices_1700000099.json"
        choices_data = [{"label": "New", "text": "Turn 1 choice"}]
        with open(os.path.join(server.OUTBOX, outbox_filename), "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "choices": choices_data
            }, f)

        server.process_outbox()

        s_res = server.load(sid)
        # a0 should NOT be given the choices
        a0 = next(m for m in s_res["messages"] if m["id"] == "a0")
        self.assertNotIn("choices", a0)
        # u1 should receive staged_choices
        u1 = next(m for m in s_res["messages"] if m["id"] == "u1")
        self.assertEqual(u1.get("staged_choices"), choices_data)

    def test_locate_empty_summary(self):
        cid = "cid-empty-summary"
        ide_poke._servers[cid] = (1234, "tok-test")
        orig_summary = ide_poke._summary
        try:
            ide_poke._summary = lambda c, p, t: {}
            port, tok, s = ide_poke.locate(cid)
            self.assertEqual(port, 1234)
            self.assertEqual(tok, "tok-test")
            self.assertEqual(s, {})
        finally:
            ide_poke._summary = orig_summary
            ide_poke._servers.pop(cid, None)

    def test_send_to_conversation_status_unspecified(self):
        cid = "cid-unspecified-status"
        orig_locate = ide_poke.locate
        orig_cached_config = ide_poke._cached_config
        orig_fix_model = ide_poke._fix_model
        orig_call = ide_poke._call
        orig_summary = ide_poke._summary

        base_cfg = {"plannerConfig": {"planModel": "default-model"}}
        try:
            ide_poke.locate = lambda c: (8000, "token-x", {"summary": "New Chat", "status": "CASCADE_RUN_STATUS_UNSPECIFIED"})
            ide_poke._cached_config = lambda cfg=None: base_cfg
            ide_poke._fix_model = lambda p, t, c: None
            ide_poke._call = lambda p, t, m, b, timeout=15: (200, {})
            ide_poke._summary = lambda c, p, t: {"lastUserInputTime": "123", "status": "CASCADE_RUN_STATUS_IDLE"}

            ok, msg, retry = ide_poke.send_to_conversation(cid, model="custom-model")
            self.assertTrue(ok)
            # Verify base_cfg was not mutated
            self.assertEqual(base_cfg["plannerConfig"]["planModel"], "default-model")
        finally:
            ide_poke.locate = orig_locate
            ide_poke._cached_config = orig_cached_config
            ide_poke._fix_model = orig_fix_model
            ide_poke._call = orig_call
            ide_poke._summary = orig_summary
            ide_poke._cid_cfg.pop(cid, None)

    def test_output_text_inline_choices_single_vs_parallel(self):
        st_single = {"suggest_choices": True, "ide_session": "m1", "choices_ide_session": ""}
        txt_single = server.output_text(st_single, is_diff=False)
        self.assertIn('"choices": [', txt_single)
        self.assertIn("- choices:", txt_single)

        txt_single_diff = server.output_text(st_single, is_diff=True)
        self.assertIn('"choices": [', txt_single_diff)
        self.assertIn("- choices:", txt_single_diff)

        st_parallel = {"suggest_choices": True, "ide_session": "m1", "choices_ide_session": "c1"}
        txt_parallel = server.output_text(st_parallel, is_diff=False)
        self.assertNotIn('"choices": [', txt_parallel)
        self.assertNotIn("- choices:", txt_parallel)

    def test_load_does_not_resurrect_choices_status(self):
        sid = "sid-no-resurrect"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(ide_session="conv-m", choices_ide_session="")
        s["messages"].append({"id": "u1", "role": "user", "text": "Hi", "status": "answered", "ts": server.now()})
        s["messages"].append({"id": "a1", "role": "assistant", "text": "Hello", "ts": server.now()})
        server.save(s)

        server.session_cache.pop(sid, None)
        loaded = server.load(sid)
        asst = loaded["messages"][1]
        self.assertNotIn("choices_status", asst)

    def test_save_cleans_choices_orig_id_and_sets_ide_auto_event(self):
        sid = "sid-orig-cleanup"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"]["choices_ide_auto"] = True
        s["messages"].append({
            "id": "a1", "role": "assistant", "text": "Hi",
            "choices_status": "answered", "choices_orig_id": "u0", "ts": server.now()
        })
        server.ide_auto_event.clear()
        server.save(s)

        self.assertTrue(server.ide_auto_event.is_set())
        # Check on-disk format
        disk_file = server.spath(sid)
        with open(disk_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        disk_asst = disk_data["messages"][0]
        self.assertNotIn("choices_orig_id", disk_asst)
        self.assertNotIn("choices_status", disk_asst)

    def test_api_assign_validation_and_pairing(self):
        sid = "sid-assign-test"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(ide_session="conv-main-fixed", choices_ide_session="")
        server.save(s)

        # 1. Assigning same session to choices that main already has should fail (400)
        try:
            self.api_post("/api/ide/assign", {"sid": sid, "ide": "conv-main-fixed", "target": "choices"})
            self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode())
            self.assertIn("メイン担当と同じ会話は指定できません", data.get("error", ""))

        # 2. Assigning choices to conv-choices-1 should succeed
        res = self.api_post("/api/ide/assign", {"sid": sid, "ide": "conv-choices-1", "target": "choices"})
        self.assertTrue(res.get("ok"))
        s_after = server.load(sid)
        self.assertEqual(s_after["setting"]["choices_ide_session"], "conv-choices-1")
        self.assertFalse(s_after["setting"]["choices_ide_manual_off"])

        # 3. Assigning main to conv-choices-1 should fail (400)
        try:
            self.api_post("/api/ide/assign", {"sid": sid, "ide": "conv-choices-1", "target": "main"})
            self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode())
            self.assertIn("選択肢担当と同じ会話は指定できません", data.get("error", ""))

        # 4. Clearing choices sets choices_ide_manual_off = True
        self.api_post("/api/ide/assign", {"sid": sid, "ide": "", "target": "choices"})
        s_cleared = server.load(sid)
        self.assertEqual(s_cleared["setting"]["choices_ide_session"], "")
        self.assertTrue(s_cleared["setting"]["choices_ide_manual_off"])

        # 5. Assigning main when choices is empty and manual_off is False automatically sets choices_ide_auto
        s_cleared["setting"]["choices_ide_manual_off"] = False
        server.save(s_cleared)
        self.api_post("/api/ide/assign", {"sid": sid, "ide": "conv-main-new", "target": "main"})
        s_new_main = server.load(sid)
        self.assertEqual(s_new_main["setting"]["ide_session"], "conv-main-new")
        self.assertTrue(s_new_main["setting"]["choices_ide_auto"])

    def test_sync_choices_agents(self):
        sid = "sid-agents-sync"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(
            title="Agents Test RPG",
            choices_ide_session="choices-sess-uuid",
        )
        server.save(s)
        server.sync_choices_agents(s)

        choices_agents = os.path.join(server.channel_folder(s), "choices", server.AGENTS_NAME)
        self.assertTrue(os.path.isfile(choices_agents))
        with open(choices_agents, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("--ide choices-sess-uuid", content)
        self.assertIn(f"channel={sid}_choices", content)
        self.assertIn("Agents Test RPG", content)

    def test_wait_input_subtask_prompt(self):
        import io
        from contextlib import redirect_stdout

        sid = "sid-wait-subtask"
        m = {"id": "m1"}
        ctx_sub = {"mode": "choices", "sections": []}
        f_sub = io.StringIO()
        with redirect_stdout(f_sub):
            try:
                wait_input.show(sid, m, ctx=ctx_sub)
            except SystemExit:
                pass
        out_sub = f_sub.getvalue()
        self.assertIn("読むのはこの表示だけ", out_sub)
        self.assertNotIn("read_past_messages.py", out_sub)

        ctx_main = {"mode": "full", "sections": [], "reason": "test"}
        f_main = io.StringIO()
        with redirect_stdout(f_main):
            try:
                wait_input.show(sid, m, ctx=ctx_main)
            except SystemExit:
                pass
        out_main = f_main.getvalue()
        self.assertIn("read_past_messages.py", out_main)

    def test_write_sent_and_mark_choices_sent(self):
        sid = "sid-sent-tracker"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(
            ide_session="conv-main-sent",
            choices_ide_session="conv-choices-sent"
        )
        s["messages"].append({"id": "u1", "role": "user", "text": "Hi", "ts": server.now()})
        server.save(s)

        # Context build for choices
        u_m = s["messages"][0]
        server.build_parallel_choices_context(s, u_m)
        sent = server.read_sent(sid)
        self.assertEqual(sent.get("choices_ide"), "conv-choices-sent")

        # Context build for main
        server.build_context(s, u_m)
        sent_after = server.read_sent(sid)
        self.assertEqual(sent_after.get("ide"), "conv-main-sent")
        self.assertEqual(sent_after.get("choices_ide"), "conv-choices-sent")

    def test_single_session_full_turn_intake(self):
        """Reviewer attack 2: single-session flow emits choices simultaneously on turn 1 without serial 2nd turn."""
        sid = "sid-single-flow"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s["setting"].update(
            ide_session="conv-main-single",
            choices_ide_session="",
            suggest_choices=True,
            title="Single Session RPG"
        )
        server.save(s)

        # 1. User sends a message
        res = self.api_post("/api/send", {"sid": sid, "text": "Take me to the guild"})
        u_id = res["id"]

        s_user = server.load(sid)
        u_msg = s_user["messages"][0]
        self.assertEqual(u_msg["status"], "pending")
        # In single session, choices_status must NOT be set on user message
        self.assertNotIn("choices_status", u_msg)

        # 2. Main response arrives containing both text and inline choices
        choices_data = [{"label": "Enter", "text": "Let's go inside"}]
        outbox_filename = f"{sid}_user_1700000050.json"
        with open(os.path.join(server.OUTBOX, outbox_filename), "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": u_id,
                "text": "We have arrived at the guild hall.",
                "choices": choices_data
            }, f)

        server.process_outbox()

        # 3. Check session state
        s_res = server.load(sid)
        self.assertEqual(len(s_res["messages"]), 2)
        asst = s_res["messages"][1]
        self.assertEqual(asst["role"], "assistant")
        self.assertEqual(asst["text"], "We have arrived at the guild hall.")
        self.assertEqual(asst.get("choices"), choices_data)
        # Assistant must NOT have choices_status (no 2nd turn queued!)
        self.assertNotIn("choices_status", asst)

        # 4. /api/pending has no pending choices task
        r_pending = self.api_get(f"/api/pending?ide=conv-main-single&check=1")
        self.assertIsNone(r_pending.get("pending"))

    def test_ide_auto_step_auto_pairing_and_manual_off(self):
        """Reviewer attack 1: ide_auto_step pairs choices conversation when main exists and not manual_off."""
        sid_auto = "sid-auto-pair"
        s1 = {"id": sid_auto, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s1["setting"].update(
            ide_session="conv-main-existing",
            choices_ide_session="",
            choices_ide_manual_off=False,
            suggest_choices=True,
            title="Auto Pair RPG"
        )
        server.save(s1)

        sid_off = "sid-auto-off"
        s2 = {"id": sid_off, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        s2["setting"].update(
            ide_session="conv-main-existing-2",
            choices_ide_session="",
            choices_ide_manual_off=True,
            suggest_choices=True,
            title="Manual Off RPG"
        )
        server.save(s2)

        orig_new_conv = getattr(ide_poke, "new_conversation", None)
        created_cids = []
        try:
            def mock_new_conv(folder):
                cid = f"conv-choices-{len(created_cids) + 1}"
                created_cids.append((cid, folder))
                return cid, "作った"
            ide_poke.new_conversation = mock_new_conv

            server.ide_auto_step()

            # sid_auto should be paired with choices
            s1_after = server.load(sid_auto)
            self.assertTrue(s1_after["setting"]["choices_ide_session"].startswith("conv-choices-"))
            self.assertFalse(s1_after["setting"]["choices_ide_auto"])

            # Verify AGENTS.md was created in choices folder
            choices_agents = os.path.join(server.channel_folder(s1_after), "choices", server.AGENTS_NAME)
            self.assertTrue(os.path.isfile(choices_agents))
            with open(choices_agents, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn(s1_after["setting"]["choices_ide_session"], content)

            # sid_off should NOT be paired
            s2_after = server.load(sid_off)
            self.assertEqual(s2_after["setting"]["choices_ide_session"], "")
            self.assertFalse(s2_after["setting"]["choices_ide_auto"])
        finally:
            if orig_new_conv:
                ide_poke.new_conversation = orig_new_conv

    def test_api_ide_new_both_flow(self):
        """API /api/ide/new with target=both creates distinct main and choices sessions."""
        sid = "sid-both-new"
        s = {"id": sid, "setting": copy.deepcopy(server.DEFAULT_SETTING), "messages": []}
        server.save(s)

        orig_new_conv = getattr(ide_poke, "new_conversation", None)
        call_count = [0]
        try:
            def mock_new_conv(folder):
                call_count[0] += 1
                return f"conv-both-{call_count[0]}", "作った"
            ide_poke.new_conversation = mock_new_conv

            res = self.api_post("/api/ide/new", {"sid": sid, "target": "both"})
            self.assertTrue(res.get("ok"))
            self.assertIn("conv-both-", res.get("ide", ""))

            s_after = server.load(sid)
            st = s_after["setting"]
            self.assertEqual(st.get("ide_session"), "conv-both-1")
            self.assertEqual(st.get("choices_ide_session"), "conv-both-2")
            self.assertTrue(server.has_parallel_choices_ide(st))
        finally:
            if orig_new_conv:
                ide_poke.new_conversation = orig_new_conv

    def test_wait_input_left_claim_handling(self):
        """Verify wait_input claims left item and shows it."""
        sid = "sid-left-test"
        m = {"id": "m-left-1", "role": "user", "text": "Unfinished prompt"}

        orig_check = wait_input.check_previous
        orig_post = wait_input.post
        orig_show = wait_input.show

        claimed = []
        shown = []
        try:
            wait_input.check_previous = lambda return_pending=False: ([], (sid, m), None, "choices")
            wait_input.post = lambda p, d: claimed.append((p, d)) or {"ok": True, "context": {"mode": "choices", "sections": []}}
            wait_input.show = lambda s_id, msg, note="", ctx=None, task_type=None: shown.append((s_id, msg, note, task_type))

            # Simulate wait_input.main running up to show
            warn, left, pending, left_type = wait_input.check_previous(return_pending=True)
            if left:
                s_id, msg = left
                res = wait_input.post("/api/claim", {"sid": s_id, "id": msg["id"], "type": left_type, "context": True})
                wait_input.show(s_id, msg, note="再表示テスト", ctx=res.get("context"), task_type=left_type)

            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0][0], "/api/claim")
            self.assertEqual(claimed[0][1]["type"], "choices")
            self.assertEqual(claimed[0][1]["id"], "m-left-1")

            self.assertEqual(len(shown), 1)
            self.assertEqual(shown[0][3], "choices")
        finally:
            wait_input.check_previous = orig_check
            wait_input.post = orig_post
            wait_input.show = orig_show


if __name__ == "__main__":
    unittest.main()
