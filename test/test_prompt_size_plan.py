# -*- coding: utf-8 -*-
"""prompt-size-plan.md の各要件を網羅検証する単体テスト群"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import memory_store
import server
import wait_input


def make_msg(role, text, mid=None, **kwargs):
    d = {"id": mid or server.new_id(), "role": role, "text": text, "ts": server.now()}
    d.update(kwargs)
    return d


class TestPromptSizePlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_psp_")
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "RAG_DIR", "SENT", "CTX", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)
        self.orig_outbox = getattr(wait_input, "OUTBOX", None)
        self.orig_done = getattr(wait_input, "DONE", None)
        self.orig_ctx_dir = getattr(wait_input, "CTX_DIR", None)
        wait_input.OUTBOX = server.OUTBOX
        wait_input.DONE = server.DONE
        wait_input.CTX_DIR = server.CTX

        server.repoke_tasks.clear()

    def tearDown(self):
        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        if self.orig_outbox is not None:
            wait_input.OUTBOX = self.orig_outbox
        if self.orig_done is not None:
            wait_input.DONE = self.orig_done
        if self.orig_ctx_dir is not None:
            wait_input.CTX_DIR = self.orig_ctx_dir
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("RPCHAT_COMPACT", None)
        os.environ.pop("RPCHAT_FLOW", None)
        os.environ.pop("RPCHAT_PREVPROMPT", None)
        os.environ.pop("RPCHAT_IMGNOTE", None)
        os.environ.pop("RPCHAT_RAGDIFF", None)

    # --- 1. RAG 差分テスト (G-09, G-10, 落とし穴) ---
    def test_rag_diff_tier1_always_full(self):
        """tier1 の記憶が2ターン目以降も全文で出力される（1行に潰れない）"""
        s = server.create_session({"title": "RAGテスト", "ide_session": "ide-1"})
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        t1_mem = {"id": "mem_t1", "_tier": 1, "category": "rule", "content": "指揮官の命令は絶対遵守"}
        t2_mem = {"id": "mem_t2", "_tier": 2, "category": "event", "content": "昨夜の作戦会議"}

        with patch("memory_store.search_memories", return_value=[t1_mem, t2_mem]):
            # Turn 1
            ctx1 = server.build_context(s, m1)
            # AI reply
            a1 = make_msg("assistant", "承知しました", mid="a1")
            s["messages"].append(a1)
            server.save(s)
            server.note_reply(s, m1["id"], a1)

            # Turn 2 (diff mode)
            m2 = make_msg("user", "2ターン目", mid="u2")
            s["messages"].append(m2)
            server.save(s)
            ctx2 = server.build_context(s, m2)

        self.assertEqual("diff", ctx2["mode"])
        rag_sec = next(sec for sec in ctx2["sections"] if "過去の記憶" in sec["label"])
        # tier1 must be full text
        self.assertIn("指揮官の命令は絶対遵守", rag_sec["text"])
        self.assertIn("【作中の前提・制約（常時確認・遵守事項）】", rag_sec["text"])

    def test_rag_diff_tier2_carried_summary(self):
        """tier2 の重複だけが（前回と同じ前提: N件）に圧縮される"""
        s = server.create_session({"title": "RAGテスト", "ide_session": "ide-1"})
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        t2_mem = {"id": "mem_t2", "_tier": 2, "category": "event", "content": "昨夜の作戦会議"}
        with patch("memory_store.search_memories", return_value=[t2_mem]):
            ctx1 = server.build_context(s, m1)
            a1 = make_msg("assistant", "了解", mid="a1")
            s["messages"].append(a1)
            server.save(s)
            server.note_reply(s, m1["id"], a1)

            m2 = make_msg("user", "2ターン目", mid="u2")
            s["messages"].append(m2)
            server.save(s)
            ctx2 = server.build_context(s, m2)

        rag_sec = next(sec for sec in ctx2["sections"] if "過去の記憶" in sec["label"])
        self.assertIn("前回と同じ前提: 1件", rag_sec["text"])
        self.assertIn("昨夜の作戦会議", rag_sec["text"])
        # Should not format as full bullet point "- [記憶]"
        self.assertNotIn("- [記憶] 昨夜の作戦会議", rag_sec["text"])

    def test_rag_diff_skipped_in_full_mode(self):
        """full モード（reason 非空）では差分化せず全件出力し、「前回と同じ前提」行が出ない"""
        s = server.create_session({"title": "RAGテスト", "ide_session": ""})  # ide empty -> full mode
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        t2_mem = {"id": "mem_t2", "_tier": 2, "category": "event", "content": "昨夜の作戦会議"}
        # Even if prev_ids had mem_t2
        server.write_sent(s["id"], {"ide": "", "mem_ids": ["mem_t2"], "msgs": []})

        with patch("memory_store.search_memories", return_value=[t2_mem]):
            ctx = server.build_context(s, m1)

        self.assertEqual("full", ctx["mode"])
        rag_sec = next(sec for sec in ctx["sections"] if "過去の記憶" in sec["label"])
        self.assertNotIn("前回と同じ前提", rag_sec["text"])
        self.assertIn("昨夜の作戦会議", rag_sec["text"])

    def test_rag_diff_mem_ids_persisted(self):
        """build_context 後に sent["mem_ids"] が今回の全ヒットIDと一致（full でも更新される）"""
        s = server.create_session({"title": "RAGテスト", "ide_session": ""})
        m = make_msg("user", "テスト", mid="u1")
        s["messages"].append(m)
        server.save(s)

        t1_mem = {"id": "m_1", "_tier": 1, "category": "rule", "content": "ルール"}
        t2_mem = {"id": "m_2", "_tier": 2, "category": "event", "content": "出来事"}
        with patch("memory_store.search_memories", return_value=[t1_mem, t2_mem]):
            server.build_context(s, m)

        sent = server.read_sent(s["id"])
        self.assertEqual(["m_1", "m_2"], sent.get("mem_ids"))

    def test_rag_diff_no_new_memories(self):
        """新規0件（全部 carried）のターンでも「前回と同じ前提: N件」行が出る"""
        s = server.create_session({"title": "RAGテスト", "ide_session": "ide-1"})
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        t2_mem = {"id": "mem_carried", "_tier": 2, "category": "event", "content": "過去の思い出"}
        parts, _ = server.settings_parts(s["setting"], server.agents_in_use(s), {})
        sigs = {k: server.sig_of(ct) for _, k, ct in parts}
        server.write_sent(s["id"], {"ide": "ide-1", "msgs": [server.msg_sig(m1)], "mem_ids": ["mem_carried"], "sigs": sigs})

        with patch("memory_store.search_memories", return_value=[t2_mem]):
            ctx = server.build_context(s, m1)

        rag_sec = next(sec for sec in ctx["sections"] if "過去の記憶" in sec["label"])
        self.assertIn("前回と同じ前提: 1件", rag_sec["text"])

    def test_rag_search_error_is_safe(self):
        """search_memories が例外を投げても build_context が落ちない（mem_ids は空）"""
        s = server.create_session({"title": "RAGエラーテスト", "ide_session": "ide-1"})
        m = make_msg("user", "テスト", mid="u1")
        s["messages"].append(m)
        server.save(s)

        with patch("memory_store.search_memories", side_effect=RuntimeError("Search failed")):
            ctx = server.build_context(s, m)

        self.assertIsNotNone(ctx)
        sent = server.read_sent(s["id"])
        self.assertEqual([], sent.get("mem_ids"))

    # --- 2. 空本文・応答ロスト対策テスト (§5) ---
    def test_empty_body_check_after_subtasks(self):
        """空本文検知が choices / image_refine の正常返却を破棄しない"""
        s = server.create_session({"title": "サブタスクテスト"})
        sid = s["id"]
        u = make_msg("user", "ユーザー発言", mid="u_sub")
        a = make_msg("assistant", "アシスタント応答", mid="a_sub", choices_status="pending", choices_ts=server.now())
        s["messages"].extend([u, a])
        server.save(s)

        # Choices response has choices but NO text
        ch_file = os.path.join(server.OUTBOX, f"{sid}_choices_123.json")
        with open(ch_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": a["id"],
                "choices": [{"label": "選択1", "text": "テキスト1"}]
            }, f)

        imported = server.process_outbox()
        self.assertTrue(imported)
        # Should not create .empty file
        done_files = os.listdir(server.DONE)
        self.assertFalse(any(f.endswith(".empty") for f in done_files))

    def test_user_processing_timeout(self):
        """processing のまま 180秒超の発言が pending に戻る（pending のままのものは対象外）"""
        s = server.create_session({"title": "タイムアウトテスト"})
        sid = s["id"]
        old_time = time.time() - 200
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old_time))

        u_proc = make_msg("user", "処理中発言", mid="u_proc", status="processing", ts=old_iso, claimed_ts=old_iso)
        u_pend = make_msg("user", "待機中発言", mid="u_pend", status="pending", ts=old_iso)
        s["messages"].extend([u_proc, u_pend])
        server.save(s)

        # Clear cache so load actually parses file
        server.session_cache.pop(sid, None)
        loaded = server.load(sid)

        proc_res = next(m for m in loaded["messages"] if m["id"] == "u_proc")
        pend_res = next(m for m in loaded["messages"] if m["id"] == "u_pend")

        self.assertEqual("pending", proc_res["status"])
        self.assertNotIn("claimed_ts", proc_res)
        self.assertIn("poke_ts", proc_res)
        self.assertIn("u_proc", server.repoke_tasks)

        self.assertEqual("pending", pend_res["status"])
        self.assertNotIn("poke_ts", pend_res)

    def test_cache_invalidated_by_user_timeout(self):
        """期限超の発言があれば _has_expired_subtasks が True（＝メモキャッシュが外れる）"""
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 250))
        s = {
            "messages": [
                {"role": "user", "status": "processing", "claimed_ts": old_iso}
            ]
        }
        self.assertTrue(server._has_expired_subtasks(s))

        # Recent processing should be False
        recent_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 50))
        s_recent = {
            "messages": [
                {"role": "user", "status": "processing", "claimed_ts": recent_iso}
            ]
        }
        self.assertFalse(server._has_expired_subtasks(s_recent))

    def test_repoke_clears_all_four_keys(self):
        """repoke_tasks のキーで done/poked/again/since が全て消える"""
        server.request_repoke("task_123")
        done = {"task_123", "task_other"}
        poked = {"task_123": 1.0, "task_other": 2.0}
        again = {"task_123"}
        since = {"task_123": 1.0, "task_other": 2.0}

        with server.lock:
            if server.repoke_tasks:
                for tk in server.repoke_tasks:
                    done.discard(tk)
                    poked.pop(tk, None)
                    again.discard(tk)
                    since.pop(tk, None)
                server.repoke_tasks.clear()

        self.assertNotIn("task_123", done)
        self.assertNotIn("task_123", poked)
        self.assertNotIn("task_123", again)
        self.assertNotIn("task_123", since)

        self.assertIn("task_other", done)
        self.assertIn("task_other", poked)
        self.assertIn("task_other", since)

    def test_poke_ts_beats_600s_wall(self):
        """poke_ts が新しい発言は age(t_ref) >= 600 で捨てられない"""
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1000))
        new_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10))
        m = {"role": "user", "ts": old_ts, "poke_ts": new_ts}
        task_type = "user"
        t_ref = m.get("poke_ts") or (m.get("image_refine_ts") if task_type == "image_refine" else (m.get("choices_ts") if task_type == "choices" else (m.get("consolidation_ts") if task_type == "consolidation" else (m.get("memory_ts") if task_type == "memory_extract" else m.get("ts")))))
        self.assertTrue(server.age(t_ref) < 600)

    def test_empty_reply_moves_file(self):
        """空本文の応答ファイルが即座に .empty へ退避される（ビジーループ防止）"""
        s = server.create_session({"title": "空本文テスト"})
        sid = s["id"]
        u = make_msg("user", "ユーザー発言", mid="u_emp", status="processing")
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_{u['id']}.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": "   "}, f)

        server.process_outbox()
        self.assertFalse(os.path.exists(outbox_file))
        done_files = os.listdir(server.DONE)
        self.assertTrue(any(f.endswith(".empty") for f in done_files))

    def test_empty_reply_marks_error_after_3(self):
        """空本文3回で status="error"、error 文言が入る（再ポークなし、claimed_ts が消える）"""
        s = server.create_session({"title": "3回空テスト"})
        sid = s["id"]
        u = make_msg("user", "ユーザー発言", mid="u_retries", status="processing", error_retries=2, claimed_ts=server.now())
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_{u['id']}.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": ""}, f)

        server.process_outbox()
        loaded = server.load(sid)
        target = next(m for m in loaded["messages"] if m["id"] == "u_retries")
        self.assertEqual("error", target["status"])
        self.assertEqual(3, target["error_retries"])
        self.assertNotIn("claimed_ts", target)
        self.assertIn("セーフティ制限", target["error"])
        self.assertNotIn("u_retries", server.repoke_tasks)

    def test_empty_body_check_respects_choices_status(self):
        """choices_status のみの返却は空返却として誤判定されない"""
        s = server.create_session({"title": "選択肢ステータステスト"})
        sid = s["id"]
        u = make_msg("user", "発言", mid="u_ch_st", status="processing")
        s["messages"].append(u)
        server.save(s)

        rep_file = os.path.join(server.OUTBOX, f"{sid}_choices_status_123.json")
        with open(rep_file, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "choices_status": "error"}, f)

        # process_outbox should NOT treat it as is_empty_reply (which moves to .empty and marks error)
        server.process_outbox()
        done_files = os.listdir(server.DONE)
        self.assertFalse(any(f.endswith(".empty") for f in done_files))

    def test_bad_outbox_repokes_even_with_timestamp_filename(self):
        """.bad に移動した際、ファイル名末尾がタイムスタンプでも pending/processing ユーザー発言を正しく repoke する"""
        s = server.create_session({"title": "bad再ポークテスト"})
        sid = s["id"]
        u = make_msg("user", "未完了発言", mid="u_bad_test", status="processing", claimed_ts=server.now())
        s["messages"].append(u)
        server.save(s)

        # Filename has timestamp at the end as wait_input creates: {sid}_reply_{ts}.json
        bad_file = os.path.join(server.OUTBOX, f"{sid}_reply_1731145678.json")
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json content that cannot be parsed }")

        # Simulate mtime > 10 seconds so process_outbox moves to .bad
        past_time = time.time() - 20
        os.utime(bad_file, (past_time, past_time))

        server.process_outbox()
        self.assertFalse(os.path.exists(bad_file))
        done_files = os.listdir(server.DONE)
        self.assertTrue(any(f.endswith(".bad") for f in done_files))

        loaded = server.load(sid)
        target = next(m for m in loaded["messages"] if m["id"] == "u_bad_test")
        self.assertEqual("pending", target["status"])
        self.assertNotIn("claimed_ts", target)
        self.assertIn("poke_ts", target)
        self.assertIn("u_bad_test", server.repoke_tasks)

    def test_error_retries_reset_on_success(self):
        """正常取り込みで error_retries が消える"""
        s = server.create_session({"title": "成功リセットテスト"})
        sid = s["id"]
        u = make_msg("user", "ユーザー発言", mid="u_ok", status="processing", error_retries=2)
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_{u['id']}.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": "正常な返答本文です"}, f)

        server.process_outbox()
        loaded = server.load(sid)
        target = next(m for m in loaded["messages"] if m["id"] == "u_ok")
        self.assertEqual("answered", target["status"])
        self.assertNotIn("error_retries", target)

    # --- 3. perf.log & compact & check_context テスト (§6.6, §6.5.1, §8.1) ---
    def test_perf_bytes_include_headers(self):
        """body_bytes が見出し行を含む（sum(text) より 350B 以上大きい）"""
        s = server.create_session({"title": "perfログテスト", "ide_session": "ide-1"})
        sid = s["id"]
        m = make_msg("user", "こんにちは", mid="u_perf")
        s["messages"].append(m)
        server.save(s)

        perf_file = os.path.join(server.DATA, "perf.log")
        if os.path.exists(perf_file):
            os.remove(perf_file)

        os.environ["RPCHAT_PERF_LOG"] = "1"
        try:
            ctx = server.build_context(s, m)
        finally:
            os.environ.pop("RPCHAT_PERF_LOG", None)

        self.assertTrue(os.path.exists(perf_file))
        with open(perf_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        last_perf = lines[-1]
        raw_text_len = sum(len(sec.get("text", "").encode("utf-8")) for sec in ctx["sections"])
        diff = last_perf["body_bytes"] - raw_text_len
        self.assertGreater(diff, 350)

    def test_perf_truncated_risk_false_when_spilled(self):
        """退避（spilled）時は長くても truncated_risk が False"""
        s = server.create_session({"title": "spillテスト", "ide_session": "ide-1"})
        m = make_msg("user", "長文" * 5000, mid="u_long")
        s["messages"].append(m)
        server.save(s)

        perf_file = os.path.join(server.DATA, "perf.log")
        if os.path.exists(perf_file):
            os.remove(perf_file)

        os.environ["RPCHAT_PERF_LOG"] = "1"
        try:
            ctx = server.build_context(s, m)
        finally:
            os.environ.pop("RPCHAT_PERF_LOG", None)

        with open(perf_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        last_perf = lines[-1]
        self.assertTrue(last_perf["spilled"])
        self.assertFalse(last_perf["truncated_risk"])

    def test_compact_off_restores_state_indent(self):
        """RPCHAT_COMPACT=0 で state_block() が indent=1 に戻る"""
        state = [{"name": "ラピ", "pose": ["standing"]}]
        os.environ["RPCHAT_COMPACT"] = "1"
        compact_str = server.state_block(state)
        self.assertNotIn("\n", compact_str)

        os.environ["RPCHAT_COMPACT"] = "0"
        full_str = server.state_block(state)
        self.assertIn("\n", full_str)
        self.assertIn(" ", full_str)

    def test_ctx_reports_compact(self):
        """build_context の戻り値に compact が含まれる"""
        s = server.create_session({"title": "compactテスト", "ide_session": "ide-1"})
        m = make_msg("user", "テスト", mid="u_ctx")
        s["messages"].append(m)
        server.save(s)

        os.environ["RPCHAT_COMPACT"] = "1"
        ctx1 = server.build_context(s, m)
        self.assertTrue(ctx1.get("compact"))

        os.environ["RPCHAT_COMPACT"] = "0"
        ctx0 = server.build_context(s, m)
        self.assertFalse(ctx0.get("compact"))

    def test_check_context_no_side_effects(self):
        """tools/check_context.py の実行後に実 data/sent/ と実セッションが変わっていない

        実データの基準（tools/baseline.json）は会話が進むだけで古くなるため、
        このテストでは一時ベースライン（RPCHAT_BASELINE）を使って --save → --check を連続実行する。

        低速テスト扱い: 実データの全セッションを走査するため、会話量に比例して遅くなる
        （95セッションで約 8.8 秒）。既定ではスキップし、RPCHAT_SLOW_TESTS=1 のときだけ実行する。
        手動で回す場合: RPCHAT_SLOW_TESTS=1 python -m unittest test.test_prompt_size_plan
        """
        if os.environ.get("RPCHAT_SLOW_TESTS") != "1":
            self.skipTest("低速テスト（実データ全件を走査）。RPCHAT_SLOW_TESTS=1 で実行")

        sent_dir = os.path.join(ROOT_DIR, "data", "sent")
        sess_dir = os.path.join(ROOT_DIR, "data", "sessions")
        if not os.path.isdir(sess_dir) or not os.listdir(sess_dir):
            # セッションが無いと --save がベースラインを書かず、--check が必ず失敗する
            self.skipTest("data/sessions が空のため検証対象が無い")

        before_sent = {f: os.path.getmtime(os.path.join(sent_dir, f)) for f in os.listdir(sent_dir)} if os.path.exists(sent_dir) else {}
        before_sess = {f: os.path.getmtime(os.path.join(sess_dir, f)) for f in os.listdir(sess_dir)} if os.path.exists(sess_dir) else {}

        import subprocess
        base = os.path.join(self.tmp, "baseline.json")
        env = dict(os.environ, RPCHAT_BASELINE=base)
        for mode in ("--save", "--check"):
            res = subprocess.run([sys.executable, os.path.join(ROOT_DIR, "tools", "check_context.py"), mode],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(0, res.returncode,
                             f"check_context {mode} stdout={res.stdout} stderr={res.stderr}")

        after_sent = {f: os.path.getmtime(os.path.join(sent_dir, f)) for f in os.listdir(sent_dir)} if os.path.exists(sent_dir) else {}
        after_sess = {f: os.path.getmtime(os.path.join(sess_dir, f)) for f in os.listdir(sess_dir)} if os.path.exists(sess_dir) else {}

        self.assertEqual(before_sent, after_sent)
        self.assertEqual(before_sess, after_sess)

    # --- 作中時間は世代交代（睡眠プロセス）でのみ進める ---
    def test_no_turn_based_time_nudge(self):
        """日常ターンでは時間帯の強制移動（【時間の経過】）を出さない。
        turns_in_period がいくら大きくても発火せず、作中時間は世代交代でのみ確定する。"""
        s = server.create_session({"title": "時間テスト", "ide_session": "ide-1",
                                   "character": {"name": "アリス", "speech": "ですわ"}})
        s["setting"]["turns_in_period"] = 50
        s["messages"].append(make_msg("user", "ねえ", mid="u1"))
        s["messages"].append(make_msg("assistant", "「どうしたの」", mid="a1"))
        s["messages"].append(make_msg("user", "ちょっと", mid="u2"))
        server.save(s)

        ctx = server.build_context(s, s["messages"][-1])
        labels = [sec.get("label") for sec in ctx["sections"]]
        self.assertNotIn("【時間の経過】", labels)
        body = "\n".join(sec.get("text") or "" for sec in ctx["sections"])
        self.assertNotIn("ターン経過しました", body)
        # 自発的に時刻を口に出すのを抑える規則は writing_style.md に残す
        with open(server.WRITING_STYLE, encoding="utf-8") as f:
            self.assertIn("作中時間の経過・時間帯", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
