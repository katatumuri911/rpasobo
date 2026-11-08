"""
Unit tests for Context Direct Send (/boost) implementation.
Tests render_input_text, with_render, build_context(record=False),
direct_poke, watchdog, fallback, /api/context, /api/claim, and UI defaults.
"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
import wait_input


class TestDirectSend(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_rp_direct_send_")
        self.orig_data = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_sent = server.SENT
        self.orig_ctx_dir = server.CTX_DIR
        self.orig_outbox = server.OUTBOX
        self.orig_poke_mode = server.POKE_MODE
        self.orig_ide_poke = server.ide_poke

        server.DATA = os.path.join(self.test_dir, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.SENT = os.path.join(server.DATA, "sent")
        server.CTX_DIR = os.path.join(server.DATA, "ctx")
        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        wait_input.OUTBOX = server.OUTBOX
        os.makedirs(server.SESS_DIR, exist_ok=True)
        os.makedirs(server.SENT, exist_ok=True)
        os.makedirs(server.CTX_DIR, exist_ok=True)
        os.makedirs(server.OUTBOX, exist_ok=True)

        server.sent_cache.clear()
        server.POKE_MODE = "rpc"

    def tearDown(self):
        server.DATA = self.orig_data
        server.SESS_DIR = self.orig_sess_dir
        server.SENT = self.orig_sent
        server.CTX_DIR = self.orig_ctx_dir
        server.OUTBOX = self.orig_outbox
        server.POKE_MODE = self.orig_poke_mode
        server.ide_poke = self.orig_ide_poke
        wait_input.OUTBOX = self.orig_outbox
        server.sent_cache.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. DEFAULT_SETTING & direct_send_on
    # -------------------------------------------------------------------------
    def test_default_setting_has_direct_send_false(self):
        self.assertIn("direct_send", server.DEFAULT_SETTING)
        self.assertFalse(server.DEFAULT_SETTING["direct_send"])

    def test_direct_send_on_conditions(self):
        st_off = {"direct_send": False}
        st_on = {"direct_send": True}

        # 1. POKE_MODE != "rpc" -> always False
        server.POKE_MODE = "window"
        self.assertFalse(server.direct_send_on(st_on))
        server.POKE_MODE = "rpc"

        # 2. Env RPCHAT_DIRECT_SEND == "0" -> always False
        with patch.dict(os.environ, {"RPCHAT_DIRECT_SEND": "0"}):
            self.assertFalse(server.direct_send_on(st_on))

        # 3. Env RPCHAT_DIRECT_SEND == "1" -> True regardless of st
        with patch.dict(os.environ, {"RPCHAT_DIRECT_SEND": "1"}):
            self.assertTrue(server.direct_send_on(st_off))

        # 4. Normal flag based on st
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RPCHAT_DIRECT_SEND", None)
            self.assertFalse(server.direct_send_on(st_off))
            self.assertTrue(server.direct_send_on(st_on))

    # -------------------------------------------------------------------------
    # 2. render_input_text & with_render
    # -------------------------------------------------------------------------
    def test_render_input_text_direct_false_vs_true(self):
        sid = "sid-render-1"
        m = {"id": "msg-123", "role": "user", "text": "Hello world"}
        ctx = {
            "mode": "diff",
            "reason": "",
            "title": "My Title",
            "sections": [
                {"label": "ユーザーの発言（これに応答する）", "text": "Hello world"}
            ]
        }

        # direct=False
        text_legacy = server.render_input_text(sid, m, ctx, ide="ide-conv-1", direct=False)
        self.assertIn("=== NEW INPUT ===", text_legacy)
        self.assertIn("session : sid-render-1", text_legacy)
        self.assertIn("reply_to: msg-123", text_legacy)
        self.assertNotIn("このメッセージだけで応答する", text_legacy)
        self.assertIn("--ide ide-conv-1", text_legacy)

        # direct=True
        text_direct = server.render_input_text(sid, m, ctx, ide="ide-conv-1", direct=True)
        self.assertIn("（このメッセージだけで応答する。wait_input.py は実行しない。", text_direct)
        self.assertIn("書いたら待機はしないで、このターンを終える。この会話への返事は書かない", text_direct)
        self.assertIn("次の入力は、この会話に新しい指示として届く。wait_input.py をバックグラウンドで常駐させないこと", text_direct)
        self.assertNotIn("wait_input.py --ide ide-conv-1", text_direct)

    def test_render_input_text_long_context_inlined_on_direct(self):
        sid = "sid-long-ctx"
        m = {"id": "msg-long", "role": "user", "text": "Long prompt"}
        # Create a body > 6500 chars
        large_text = "A" * 7000
        ctx = {
            "mode": "full",
            "reason": "初回",
            "title": "Large Context",
            "sections": [
                {"label": "本文", "text": large_text}
            ]
        }

        # direct=False should write to CTX_DIR/<sid>.md
        text_legacy = server.render_input_text(sid, m, ctx, direct=False)
        self.assertIn("設定・会話・発言はファイルに書いた", text_legacy)
        self.assertTrue(os.path.isfile(os.path.join(server.CTX_DIR, f"{sid}.md")))

        # direct=True should NOT write to file, but inline everything
        text_direct = server.render_input_text(sid, m, ctx, direct=True)
        self.assertNotIn("設定・会話・発言はファイルに書いた", text_direct)
        self.assertIn(large_text, text_direct)

    def test_render_input_text_subtasks(self):
        sid = "sid-subtask"
        m = {"id": "m-sub"}
        subtasks = [
            ("choices", "返答候補（choices）の自動生成"),
            ("image_refine", "画像プロンプトのAI推敲"),
            ("memory_extract", "長期記憶の自動要約・抽出"),
            ("consolidation", "世代交代の定期統合"),
        ]
        for mode, desc in subtasks:
            ctx = {"mode": mode, "sections": []}
            text = server.render_input_text(sid, m, ctx, task_type=mode, direct=True)
            self.assertIn(desc, text)
            self.assertIn("決まり  : 読むのはこの表示だけ", text)
            self.assertNotIn("read_past_messages.py", text)

    def test_with_render(self):
        s = {"id": "s-test-with-render"}
        m = {"id": "m-1"}
        ctx = {"mode": "diff", "sections": [{"label": "test", "text": "hello"}]}
        res = server.with_render(s, m, ctx, ide="my-ide")
        self.assertIn("render", res)
        self.assertIn("session : s-test-with-render", res["render"])

    # -------------------------------------------------------------------------
    # 3. build_context(record=True vs record=False)
    # -------------------------------------------------------------------------
    def test_build_context_record_flag(self):
        sid = "sid-build-ctx-rec"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": "u1", "role": "user", "text": "Hello", "ts": "2026-11-08T00:00:00Z"}
            ]
        }
        s["setting"]["ide_session"] = "conv-1"
        server.save(s)

        # 1. record=False: should NOT write to SENT directory
        sent_file = os.path.join(server.SENT, f"{sid}.json")
        ctx_no_rec = server.build_context(s, s["messages"][0], record=False)
        self.assertFalse(os.path.exists(sent_file))
        self.assertIn("_sent", ctx_no_rec)
        self.assertEqual(ctx_no_rec["_sent"]["ide"], "conv-1")

        # 2. record=True: SHOULD write to SENT directory, and no _sent attached
        ctx_rec = server.build_context(s, s["messages"][0], record=True)
        self.assertTrue(os.path.exists(sent_file))
        self.assertNotIn("_sent", ctx_rec)
        with open(sent_file, encoding="utf-8") as f:
            saved_sent = json.load(f)
        self.assertEqual(saved_sent["ide"], "conv-1")

    # -------------------------------------------------------------------------
    # 4. direct_poke tests
    # -------------------------------------------------------------------------
    def test_direct_poke_success(self):
        sid = "sid-direct-poke-1"
        mid = "msg-user-1"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "Test message", "status": "pending"}
            ]
        }
        s["setting"]["ide_session"] = "ide-test-session"
        server.save(s)

        mock_ide_poke = MagicMock()
        mock_ide_poke.send_to_conversation.return_value = (True, "sent OK", False)
        server.ide_poke = mock_ide_poke

        ok, retry, fb, msg = server.direct_poke("ide-test-session", sid, mid)
        self.assertTrue(ok)
        self.assertFalse(retry)
        self.assertFalse(fb)
        self.assertIn("sent OK", msg)

        # Verify message status updated to processing and direct marked
        s_after = server.load(sid)
        m_after = server.find_msg(s_after, mid)
        self.assertEqual(m_after["status"], "processing")
        self.assertTrue(m_after["direct"])
        self.assertTrue(m_after.get("direct_ts"))
        self.assertIn(mid, server.claimed_at)

        # Verify sent record written
        sent = server.read_sent(sid)
        self.assertEqual(sent["ide"], "ide-test-session")

    def test_direct_poke_exceeds_max_size(self):
        sid = "sid-direct-poke-size"
        mid = "msg-user-size"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "A" * 500, "status": "pending"}
            ]
        }
        s["setting"]["ide_session"] = "ide-test-session"
        server.save(s)

        # Set DIRECT_MAX low
        with patch("server.DIRECT_MAX", 100):
            ok, retry, fb, msg = server.direct_poke("ide-test-session", sid, mid)
            self.assertFalse(ok)
            self.assertFalse(retry)
            self.assertTrue(fb)  # Fallback requested
            self.assertIn("上限 100", msg)

    def test_direct_poke_retry_flow(self):
        sid = "sid-direct-poke-retry"
        mid = "msg-user-retry"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "A retry message", "status": "processing", "direct": True, "direct_ts": server.now()}
            ]
        }
        s["setting"]["ide_session"] = "ide-test-session"
        server.save(s)

        sent_calls = []
        mock_ide_poke = MagicMock()
        def mock_send(ide, text, model=None, media=None):
            sent_calls.append(text)
            return True, "retry sent", False
        mock_ide_poke.send_to_conversation.side_effect = mock_send
        server.ide_poke = mock_ide_poke

        ok, retry, fb, msg = server.direct_poke("ide-test-session", sid, mid, retry=True)
        self.assertTrue(ok)
        self.assertFalse(retry)
        self.assertFalse(fb)
        self.assertTrue(len(sent_calls) > 0)
        self.assertIn("前回の応答は無視する。同じ入力の再送", sent_calls[0])

    def test_direct_poke_retry_after_answered_does_not_corrupt_sent(self):
        """If response arrives while retry RPC is in flight, direct_poke must not overwrite sent or re-add direct"""
        sid = "sid-retry-race"
        mid = "msg-user-race"
        asst_id = "msg-asst-race"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "Hello race", "status": "pending"}
            ]
        }
        s["setting"]["ide_session"] = "ide-test-session"
        server.save(s)

        # 1. Initial direct send succeeds and writes initial sent record with user message
        mock_ide_poke = MagicMock()
        mock_ide_poke.send_to_conversation.return_value = (True, "sent initial", False)
        server.ide_poke = mock_ide_poke
        ok, retry, fb, msg = server.direct_poke("ide-test-session", sid, mid, retry=False)
        self.assertTrue(ok)

        # 2. Simulate retry RPC where assistant reply arrives before retry RPC returns
        def mock_send(ide, text, model=None, media=None):
            # Simulate outbox processor answering the message while RPC is in flight
            with server.lock:
                s_snap = server.load(sid)
                m_snap = server.find_msg(s_snap, mid)
                m_snap["status"] = "answered"
                m_snap.pop("direct", None)
                m_snap.pop("direct_ts", None)
                asst_m = {"id": asst_id, "role": "assistant", "text": "AI reply", "reply_to": mid}
                s_snap["messages"].append(asst_m)
                server.save(s_snap)
                server.note_reply(s_snap, mid, asst_m)
            return True, "retry finished", False
        mock_ide_poke.send_to_conversation.side_effect = mock_send

        ok, retry, fb, msg = server.direct_poke("ide-test-session", sid, mid, retry=True)
        self.assertTrue(ok)
        self.assertIn("再送中に既に応答が取り込まれた", msg)

        # Message status must still be answered and not have direct
        s_after = server.load(sid)
        m_after = server.find_msg(s_after, mid)
        self.assertEqual(m_after["status"], "answered")
        self.assertNotIn("direct", m_after)

        # Sent record must retain the assistant message signature (not wiped by retry)
        sent = server.read_sent(sid)
        asst_sig = server.msg_sig({"id": asst_id, "role": "assistant", "text": "AI reply", "reply_to": mid})
        self.assertIn(asst_sig, sent.get("msgs", []))

    def test_direct_poke_stopped_during_rpc(self):
        """If user clicked stop during RPC, message is deleted, sent must get reset=True"""
        sid = "sid-direct-poke-stop"
        mid = "msg-user-stop"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "To be stopped", "status": "pending"}
            ]
        }
        s["setting"]["ide_session"] = "ide-test-session"
        server.save(s)

        mock_ide_poke = MagicMock()
        def mock_send(ide, text, model=None, media=None):
            # Simulate /api/send/stop removing the message while RPC is in flight
            with server.lock:
                s_del = server.load(sid)
                s_del["messages"] = []
                server.save(s_del)
            return True, "RPC ok", False
        mock_ide_poke.send_to_conversation.side_effect = mock_send
        server.ide_poke = mock_ide_poke

        ok, retry, fb, msg = server.direct_poke("ide-test-session", sid, mid)
        self.assertTrue(ok)
        self.assertIn("送信中に停止された", msg)

        # Sent record must have reset=True so next turn sends full context
        sent = server.read_sent(sid)
        self.assertTrue(sent.get("reset"))

    # -------------------------------------------------------------------------
    # 5. /api/pending check exclusion & fallback left
    # -------------------------------------------------------------------------
    def test_api_pending_excludes_direct_from_left(self):
        sid = "sid-pending-direct"
        mid = "m-direct-proc"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "proc msg", "status": "processing", "direct": True}
            ]
        }
        s["setting"]["ide_session"] = "ide-1"
        server.save(s)

        # Check in /api/pending logic (internal check)
        with server.lock:
            # Emulate the check scan in /api/pending
            left = None
            for m in reversed(s["messages"]):
                if m["role"] == "user" and m.get("status") == "processing" and not m.get("direct"):
                    left = [sid, m, "user"]
                    break
            self.assertIsNone(left)

            # When direct is removed (e.g. fallback), it should be included
            s["messages"][0].pop("direct")
            for m in reversed(s["messages"]):
                if m["role"] == "user" and m.get("status") == "processing" and not m.get("direct"):
                    left = [sid, m, "user"]
                    break
            self.assertIsNotNone(left)
            self.assertEqual(left[0], sid)

    # -------------------------------------------------------------------------
    # 6. process_outbox cleanup
    # -------------------------------------------------------------------------
    def test_process_outbox_cleans_direct_fields(self):
        sid = "sid-outbox-direct"
        mid = "m-user-outbox"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "user message", "status": "processing",
                 "direct": True, "direct_ts": server.now()}
            ]
        }
        server.save(s)

        # Simulate assistant reply arriving in outbox
        rep_file = os.path.join(server.OUTBOX, f"{sid}_reply_{int(time.time())}.json")
        with open(rep_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": mid,
                "text": "Assistant response"
            }, f)

        # Run one step of process_outbox
        server.process_outbox()

        s_after = server.load(sid)
        m_after = server.find_msg(s_after, mid)
        self.assertEqual(m_after["status"], "answered")
        self.assertNotIn("direct", m_after)
        self.assertNotIn("direct_ts", m_after)

    # -------------------------------------------------------------------------
    # 7. wait_input.py show with render
    # -------------------------------------------------------------------------
    def test_wait_input_show_uses_render(self):
        f = io.StringIO()
        ctx = {"render": "=== NEW INPUT ===\nrendered from server\n"}
        with redirect_stdout(f):
            with self.assertRaises(SystemExit) as cm:
                wait_input.show("sid-1", {"id": "m1"}, ctx=ctx)
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(f.getvalue(), "=== NEW INPUT ===\nrendered from server\n")

    def test_wait_input_show_fallback_when_no_render(self):
        """When ctx has no render (mocked tests), falls back to server.render_input_text"""
        f = io.StringIO()
        ctx = {"mode": "diff", "sections": [{"label": "test", "text": "hello"}]}
        with redirect_stdout(f):
            with self.assertRaises(SystemExit) as cm:
                wait_input.show("sid-fallback", {"id": "m-fallback"}, ctx=ctx)
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("=== NEW INPUT ===", f.getvalue())
        self.assertIn("session : sid-fallback", f.getvalue())

    # -------------------------------------------------------------------------
    # 8. Watchdog retry and fallback give-up (real poke_worker execution)
    # -------------------------------------------------------------------------
    def test_poke_worker_watchdog_gives_up_after_retries(self):
        """poke_worker watchdog retries twice then gives up to legacy left when idle persists"""
        sid = "sid-watchdog-giveup"
        mid = "m-giveup"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "Unanswered message", "status": "processing",
                 "direct": True, "direct_ts": server.now()}
            ]
        }
        s["setting"]["direct_send"] = True
        s["setting"]["ide_session"] = "ide-giveup"
        server.save(s)

        sent_pokes = []
        def mock_send_poke(ide, full=False, media=None, model=None, wait=None, prefix=""):
            sent_pokes.append({"ide": ide, "full": full, "prefix": prefix})
            return True, "poke sent", False

        mock_ide = MagicMock()
        mock_ide.idle.return_value = (True, "")
        retry_calls = []
        def mock_direct_poke(ide, sid_arg, mid_arg, retry=False):
            retry_calls.append((ide, mid_arg, retry))
            return True, False, False, "retry sent"

        t_val = [10.0]
        def mock_time():
            return t_val[0]

        # Timeline: born=10 -> t=40 retry1 -> t=50 idle_since=50 -> t=70 retry2 -> t=80 idle_since=80 -> t=100 give up
        timeline = [10.0, 40.0, 50.0, 70.0, 80.0, 100.0]
        idx = [0]
        def stop_loop(*a, **kw):
            if idx[0] < len(timeline):
                t_val[0] = timeline[idx[0]]
                idx[0] += 1
            else:
                raise KeyboardInterrupt()

        with patch("server.ide_poke", mock_ide), \
             patch("server.send_poke", side_effect=mock_send_poke), \
             patch("server.direct_poke", side_effect=mock_direct_poke), \
             patch("time.time", side_effect=mock_time), \
             patch.object(server.poke_event, "wait", side_effect=stop_loop):
            try:
                server.poke_worker()
            except KeyboardInterrupt:
                pass

        self.assertEqual(len(retry_calls), 2)
        self.assertTrue(all(rc[2] for rc in retry_calls))  # retry=True

        s_final = server.load(sid)
        m_final = server.find_msg(s_final, mid)
        self.assertNotIn("direct", m_final)
        self.assertTrue(m_final.get("direct_off"))

        sent_final = server.read_sent(sid)
        self.assertTrue(sent_final.get("reset"))

        self.assertEqual(len(sent_pokes), 1)
        self.assertTrue(sent_pokes[0]["full"])
        self.assertIn("直送に応答がなかった", sent_pokes[0]["prefix"])

    def test_poke_worker_watchdog_gives_up_immediately_on_fallback(self):
        """poke_worker watchdog gives up immediately without infinite loop when direct_poke returns fb=True"""
        sid = "sid-watchdog-fb"
        mid = "m-fb"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "Unanswered message", "status": "processing",
                 "direct": True, "direct_ts": server.now()}
            ]
        }
        s["setting"]["direct_send"] = True
        s["setting"]["ide_session"] = "ide-fb"
        server.save(s)

        sent_pokes = []
        def mock_send_poke(ide, full=False, media=None, model=None, wait=None, prefix=""):
            sent_pokes.append({"ide": ide, "full": full, "prefix": prefix})
            return True, "poke sent", False

        mock_ide = MagicMock()
        mock_ide.idle.return_value = (True, "")
        def mock_direct_poke(ide, sid_arg, mid_arg, retry=False):
            return False, False, True, "size exceeded fallback"

        t_val = [10.0]
        def mock_time():
            return t_val[0]

        timeline = [10.0, 40.0]
        idx = [0]
        def stop_loop(*a, **kw):
            if idx[0] < len(timeline):
                t_val[0] = timeline[idx[0]]
                idx[0] += 1
            else:
                raise KeyboardInterrupt()

        with patch("server.ide_poke", mock_ide), \
             patch("server.send_poke", side_effect=mock_send_poke), \
             patch("server.direct_poke", side_effect=mock_direct_poke), \
             patch("time.time", side_effect=mock_time), \
             patch.object(server.poke_event, "wait", side_effect=stop_loop):
            try:
                server.poke_worker()
            except KeyboardInterrupt:
                pass

        s_final = server.load(sid)
        m_final = server.find_msg(s_final, mid)
        self.assertNotIn("direct", m_final)
        self.assertTrue(m_final.get("direct_off"))

        sent_final = server.read_sent(sid)
        self.assertTrue(sent_final.get("reset"))
        self.assertEqual(len(sent_pokes), 1)

    def test_render_input_text_edge_cases(self):
        """Missing id or empty sections should not raise KeyError or crash"""
        m_empty = {}
        ctx_empty = {"sections": [], "mode": "diff"}
        text = server.render_input_text("sid-edge", m_empty, ctx_empty, direct=True)
        self.assertIn("session : sid-edge", text)
        self.assertIn("reply_to: ", text)


class TestDirectSendAPI(unittest.TestCase):
    def setUp(self):
        import threading
        self.test_dir = tempfile.mkdtemp(prefix="test_rp_direct_api_")
        self.orig_data = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_sent = server.SENT
        self.orig_ctx_dir = server.CTX_DIR
        self.orig_outbox = server.OUTBOX

        server.DATA = os.path.join(self.test_dir, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.SENT = os.path.join(server.DATA, "sent")
        server.CTX_DIR = os.path.join(server.DATA, "ctx")
        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        wait_input.OUTBOX = server.OUTBOX
        os.makedirs(server.SESS_DIR, exist_ok=True)
        os.makedirs(server.SENT, exist_ok=True)
        os.makedirs(server.CTX_DIR, exist_ok=True)
        os.makedirs(server.OUTBOX, exist_ok=True)
        server.sent_cache.clear()
        server.session_cache.clear()

        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.th.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.orig_data
        server.SESS_DIR = self.orig_sess_dir
        server.SENT = self.orig_sent
        server.CTX_DIR = self.orig_ctx_dir
        server.OUTBOX = self.orig_outbox
        wait_input.OUTBOX = self.orig_outbox
        server.sent_cache.clear()
        server.session_cache.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def post_json(self, path, data):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def get_json(self, path):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Content-Type": "application/json"},
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_api_pending_excludes_direct_from_left_http(self):
        """GET /api/pending?check=1 excludes processing messages that have direct=True from left"""
        sid = "sid-api-pending"
        mid = "m-api-pending"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "pending test", "status": "processing", "direct": True}
            ]
        }
        s["setting"]["ide_session"] = "ide-check-1"
        server.save(s)

        res = self.get_json("/api/pending?check=1&ide=ide-check-1")
        self.assertIsNone(res.get("left"))

        # When direct is removed (e.g. fallback to legacy)
        s["messages"][0].pop("direct")
        server.save(s)

        res_after = self.get_json("/api/pending?check=1&ide=ide-check-1")
        self.assertIsNotNone(res_after.get("left"))
        self.assertEqual(res_after["left"][0], sid)
        self.assertEqual(res_after.get("left_type"), "user")

    def test_api_context_returns_render(self):
        sid = "sid-api-ctx"
        mid = "m-api-ctx"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "hello api", "status": "pending"}
            ]
        }
        server.save(s)

        res = self.post_json("/api/context", {"sid": sid, "id": mid, "type": "user", "ide": "test-ide"})
        self.assertIn("render", res)
        self.assertIn("=== NEW INPUT ===", res["render"])
        self.assertIn("session : sid-api-ctx", res["render"])
        self.assertIn("--ide test-ide", res["render"])

    def test_api_claim_returns_render(self):
        sid = "sid-api-claim"
        mid = "m-api-claim"
        s = {
            "id": sid,
            "setting": copy.deepcopy(server.DEFAULT_SETTING),
            "messages": [
                {"id": mid, "role": "user", "text": "hello claim", "status": "pending"}
            ]
        }
        server.save(s)

        res = self.post_json("/api/claim", {"sid": sid, "id": mid, "type": "user", "context": True, "ide": "claim-ide", "note": "special note"})
        self.assertTrue(res.get("ok"))
        self.assertIn("context", res)
        self.assertIn("render", res["context"])
        self.assertIn("!!! special note", res["context"]["render"])
        self.assertIn("--ide claim-ide", res["context"]["render"])


if __name__ == "__main__":
    unittest.main()
