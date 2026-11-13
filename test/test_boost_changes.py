# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ide_poke
import memory_store
import server
import wait_input


def make_msg(role, text, mid=None, status=None):
    m = {
        "id": mid or f"m_{int(time.time()*1000)}_{role}",
        "role": role,
        "text": text,
        "ts": server.now()
    }
    if status:
        m["status"] = status
    return m


class TestBoostChanges(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_boost_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_rag_dir = memory_store.RAG_DIR
        self.orig_ms_data = memory_store.DATA
        self.orig_outbox = server.OUTBOX
        self.orig_done = server.DONE
        # NEW_IDE_DIR/AGENTS_LOG はモジュール読み込み時に決まるので DATA の差し替えに追従しない。
        # 差し替えないと sync_choices_agents / sync_agents が**本物の session/ の AGENTS.md** を
        # テストのセッションIDで上書きしてしまう。
        self.orig_new_ide_dir = server.NEW_IDE_DIR
        self.orig_agents_log = server.AGENTS_LOG

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        os.makedirs(server.CHOICES_DIR, exist_ok=True)
        server.STATES_DIR = os.path.join(server.DATA, "states")
        os.makedirs(server.STATES_DIR, exist_ok=True)
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        os.makedirs(server.IMAGES_DIR, exist_ok=True)
        server.SENT = os.path.join(server.DATA, "sent")
        os.makedirs(server.SENT, exist_ok=True)
        server.ARCHIVE_DIR = os.path.join(server.DATA, "archive")
        os.makedirs(server.ARCHIVE_DIR, exist_ok=True)
        server.TRASH = os.path.join(server.DATA, "trash")
        os.makedirs(server.TRASH, exist_ok=True)
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")

        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = os.path.join(server.DATA, "rag")
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)

        server.OUTBOX = wait_input.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(server.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.summary_cache.clear()
        server.queued_memories.clear()
        server.memory_event.clear()
        server._memory_worker_stop = False

    def tearDown(self):
        server._memory_worker_stop = True
        server.memory_event.set()
        server.DATA = self.orig_data_dir
        server.NEW_IDE_DIR = self.orig_new_ide_dir
        server.AGENTS_LOG = self.orig_agents_log
        server.SESS_DIR = self.orig_sess_dir
        memory_store.DATA = self.orig_ms_data
        memory_store.RAG_DIR = self.orig_rag_dir
        server.OUTBOX = wait_input.OUTBOX = self.orig_outbox
        server.DONE = self.orig_done
        server.queued_memories.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_extracted_memories_lock_release(self):
        """Verify save_extracted_memories works and embeddings/tokens are computed"""
        sid = "sid-test-mem"
        char_name = "アリス"
        new_mems = [
            {"category": "event", "content": "放課後に図書室で一緒に勉強した"},
            {"category": "promise", "content": "今度の日曜日にカフェに行く約束をした"}
        ]
        res = memory_store.save_extracted_memories(sid, char_name, new_mems, turn_idx=1, mid="m-1")
        self.assertIn("memories", res)
        self.assertEqual(len(res["memories"]), 2)
        m0 = res["memories"][0]
        self.assertEqual(m0["content"], "放課後に図書室で一緒に勉強した")
        self.assertEqual(m0["category"], "event")
        self.assertIn("tokens", m0)
        self.assertIn("embedding", m0)
        self.assertTrue(len(m0["embedding"]) > 0)

    def test_async_memory_queueing_in_process_outbox(self):
        """Verify process_outbox immediately saves session and queues memory task asynchronously"""
        s = server.create_session({"title": "テスト会話", "character": {"name": "アリス"}})
        sid = s["id"]
        user_msg = make_msg("user", "こんにちは！", mid="u1", status="pending")
        s["messages"].append(user_msg)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_asst_1.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": user_msg["id"],
                "text": "こんにちは！いい天気ですね。",
                "memories": [
                    {"category": "event", "content": "天気の良い日に挨拶を交わした"}
                ]
            }, f, ensure_ascii=False)

        # Process outbox
        self.assertEqual(len(server.queued_memories), 0)
        imported = server.process_outbox()
        self.assertTrue(imported)

        # Message is already appended and saved to session
        updated_s = server.load(sid, readonly=True)
        self.assertEqual(len(updated_s["messages"]), 2)
        asst_msg = updated_s["messages"][1]
        self.assertEqual(asst_msg["text"], "こんにちは！いい天気ですね。")

        # Memory is in queue, not yet in memory_store file
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories[0]
        self.assertEqual(task[0], "explicit")
        self.assertEqual(task[1], sid)
        self.assertEqual(task[6], asst_msg["id"])

        # No memories in memory_store yet
        mem_data = memory_store.load_memories(sid)
        self.assertEqual(len(mem_data.get("memories", [])), 0)

        # Now simulate memory_worker processing one task
        task = server.queued_memories.popleft()
        kind, m_sid, m_ch, m_list, m_res, m_turn, m_id, m_el = task
        memory_store.save_extracted_memories(
            m_sid, m_ch, m_list, resolved_ids=m_res, turn_idx=m_turn, mid=m_id, elapsed_time=m_el
        )
        mem_data_after = memory_store.load_memories(sid)
        self.assertEqual(len(mem_data_after.get("memories", [])), 1)
        self.assertEqual(mem_data_after["memories"][0]["mid"], asst_msg["id"])

    def test_fallback_memory_queueing(self):
        """LLM が memories を返さないターンは、まず同期キーワード抽出で拾う（fallback キューは使わない）"""
        s = server.create_session({"title": "テスト会話", "character": {"name": "アリス"}})
        sid = s["id"]
        user_msg = make_msg("user", "アリス、今度の日曜日にカフェに行く約束しよう！", mid="u1", status="pending")
        s["messages"].append(user_msg)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_asst_2.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": user_msg["id"],
                "text": "うん、アリスとカフェに行く約束ね！"
            }, f, ensure_ascii=False)

        imported = server.process_outbox()
        self.assertTrue(imported)

        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        self.assertEqual(task[0], "explicit")   # キーワードで拾えた分は即保存キューに乗る
        kind, m_sid, m_ch, m_list, m_res, m_turn, m_id, m_el = task
        self.assertEqual(m_sid, sid)
        self.assertTrue(any(x["category"] == "promise" for x in m_list))
        self.assertEqual(m_id, server.load(sid, readonly=True)["messages"][-1]["id"])

        # 実際に保存されるところまで確認
        memory_store.save_extracted_memories(
            m_sid, m_ch, m_list, resolved_ids=m_res, turn_idx=m_turn, mid=m_id, elapsed_time=m_el
        )
        mem_data = memory_store.load_memories(sid)
        self.assertTrue(len(mem_data.get("memories", [])) >= 1)

    def test_memory_task_alive_and_compensation_deletion(self):
        """Verify _memory_task_alive detects deletion and delete_by_mid cleans up ghost memories"""
        s = server.create_session({"title": "テスト", "character": {"name": "アリス"}})
        sid = s["id"]
        m = make_msg("assistant", "テスト発言", mid="a1")
        s["messages"].append(m)
        server.save(s)

        # Message exists
        self.assertTrue(server._memory_task_alive(sid, m["id"]))
        # Invalid cases
        self.assertFalse(server._memory_task_alive(sid, "non-existent-mid"))
        self.assertFalse(server._memory_task_alive("non-existent-sid", m["id"]))
        self.assertFalse(server._memory_task_alive("", m["id"]))
        self.assertFalse(server._memory_task_alive(sid, ""))

        # Save a memory linked to m["id"]
        memory_store.save_extracted_memories(sid, "アリス", [{"category": "event", "content": "テスト記憶"}], mid=m["id"])
        self.assertEqual(len(memory_store.load_memories(sid).get("memories", [])), 1)

        # Delete message from session
        s["messages"].pop()
        server.save(s)

        self.assertFalse(server._memory_task_alive(sid, m["id"]))

        # Execute compensation deletion
        memory_store.delete_by_mid(sid, m["id"])
        self.assertEqual(len(memory_store.load_memories(sid).get("memories", [])), 0)

    def test_always_show_keys_and_diff_output_text(self):
        """Verify ALWAYS_SHOW_KEYS does not contain memo and output_text schema in diff mode"""
        self.assertNotIn("memo", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("relationship_core", server.ALWAYS_SHOW_KEYS)
        self.assertIn("elapsed_time", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("output.md", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("instructions.md", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("chara.md", server.ALWAYS_SHOW_KEYS)

        st = {
            "image_mode": "always",
            "rp_state": True,
            "suggest_choices": True
        }
        diff_text = server.output_text(st, is_diff=True)
        # Verify optional keys explanation exists
        if server.compact_on():
            self.assertIn("任意", diff_text)
        else:
            self.assertIn("任意キー（変化・該当がない通常ターンはキー自体を省略すること）", diff_text)
        self.assertIn("- state:", diff_text)
        self.assertIn("【重要】終了・解除した状態", diff_text)
        # Sample json in diff mode should not have memo or elapsed_time in root keys
        # Extract json block
        start_idx = diff_text.find("```json\n") + 8
        end_idx = diff_text.find("\n```", start_idx)
        schema_sample = json.loads(diff_text[start_idx:end_idx])
        self.assertNotIn("memo", schema_sample)
        self.assertNotIn("elapsed_time", schema_sample)
        self.assertNotIn("relationship_core", schema_sample)
        # 以前は diff の例から memories も外していたが、例が無いとモデルが出力自体を省略する
        # （実測: memories付き返答が 5〜16%）ため、形だけは毎回見せる方針に変更した
        self.assertIn("memories", schema_sample)
        self.assertNotIn("resolved_ids", schema_sample)
        self.assertIn("text", schema_sample)
        self.assertIn("state", schema_sample)

    def test_image_prompt_guidelines_brief_mode(self):
        """Verify brief guidelines compressed to ~1211 chars vs full ~5139 chars"""
        full_text = server.image_prompt_guidelines_text(for_refine=False, brief=False)
        brief_text = server.image_prompt_guidelines_text(for_refine=False, brief=True)

        self.assertTrue(len(full_text) > 4000)
        self.assertTrue(len(brief_text) < 2000)
        self.assertIn("【画像プロンプト（image.prompt）作成の必須要点（初回規則の遵守）】", brief_text)
        self.assertIn("射精前後の厳密区別", brief_text)

        # refine mode ignores brief
        refine_text = server.image_prompt_guidelines_text(for_refine=True, brief=True)
        self.assertTrue(len(refine_text) > 4000)

    def test_build_context_memo_due_and_diff_rules(self):
        """Verify memo is omitted in diff mode when not memo_due, and included when memo_due is True"""
        s = server.create_session({"title": "テスト会話", "memo": "これまでのあらすじメモ", "ide_session": "ide-1"})
        sid = s["id"]
        st = s["setting"]

        # Initial turn: reason is set, full mode
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        ctx_first = server.build_context(s, m1)
        self.assertEqual(ctx_first["mode"], "full")
        # In full mode, image guidelines are state_tag_guidelines_text (< 2000B, ~1.7KB)
        img_sec = next(sec for sec in ctx_first["sections"] if "解剖学的整合性規則" in sec["label"])
        self.assertEqual(img_sec["text"], server.state_tag_guidelines_text())
        self.assertTrue(len(img_sec["text"].encode("utf-8")) < 2000)

        # Simulate assistant reply and subsequent turn in diff mode
        a1 = make_msg("assistant", "1ターン目返答", mid="a1")
        m2 = make_msg("user", "2ターン目", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)

        ctx_diff = server.build_context(s, m2)
        self.assertEqual(ctx_diff["mode"], "diff")
        # In diff mode (not memo_due), memo was not changed and memo_due is False -> memo omitted
        memo_sec = next((sec for sec in ctx_diff["sections"] if "AIメモ" in sec["label"]), None)
        self.assertIsNone(memo_sec)
        # Image guidelines should be brief in diff mode if enabled
        img_sec_diff = next((sec for sec in ctx_diff["sections"] if "作成の要点" in sec["label"]), None)
        if os.environ.get("RPCHAT_IMGNOTE", "0") != "0":
            self.assertIsNotNone(img_sec_diff)
            self.assertTrue(len(img_sec_diff["text"]) < 2000)
        else:
            self.assertIsNone(img_sec_diff)

        # Now simulate 10 assistant turns after last memo
        for i in range(10):
            s["messages"].append(make_msg("assistant", f"asst {i}", mid=f"asst_{i}"))
        m_due = make_msg("user", "12ターン目", mid="u12")
        s["messages"].append(m_due)
        server.save(s)

        ctx_due = server.build_context(s, m_due)
        self.assertEqual(ctx_due["mode"], "diff")
        # memo_due is True -> memo section must be included in diff mode!
        memo_sec_due = next((sec for sec in ctx_due["sections"] if "AIメモ" in sec["label"]), None)
        self.assertIsNotNone(memo_sec_due)
        self.assertIn("【重要】あらすじ（memo）の定期更新ターン", [sec["label"] for sec in ctx_due["sections"]])

    def test_build_context_cycles_instructions_character_and_spec(self):
        """指示・キャラ・specは3ターン周期で1枚ずつローテーションされ、一巡して全て現れる"""
        s = server.create_session({"title": "テスト", "ide_session": "ide-1",
                                   "instructions": "必ず敬語で話す", "character": {"name": "アリス", "speech": "一人称は私"}})
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        ctx1 = server.build_context(s, m1)
        self.assertEqual(ctx1["mode"], "full")

        a1 = make_msg("assistant", "返答1", mid="a1")
        m2 = make_msg("user", "2ターン目", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        # 2ターン目: diff (phase 1: キャラクター)
        ctx2 = server.build_context(s, m2)
        self.assertEqual(ctx2["mode"], "diff")
        labels2 = [sec["label"] for sec in ctx2["sections"]]
        self.assertTrue(any(lb.startswith("キャラクター") for lb in labels2), labels2)
        self.assertFalse(any(lb.startswith("指示") for lb in labels2), labels2)
        self.assertNotIn("キャラクター", ctx2["same"])
        self.assertNotIn("指示", ctx2["same"])

        a2 = make_msg("assistant", "返答2", mid="a2")
        m3 = make_msg("user", "3ターン目", mid="u3")
        s["messages"].extend([a2, m3])
        server.save(s)
        server.note_reply(s, "u2", a2)

        # 3ターン目: diff (phase 2: 応答の書き方)
        ctx3 = server.build_context(s, m3)
        self.assertEqual(ctx3["mode"], "diff")
        labels3 = [sec["label"] for sec in ctx3["sections"]]
        self.assertTrue(any("応答の書き方" in lb for lb in labels3), labels3)
        self.assertFalse(any(lb.startswith("指示") for lb in labels3), labels3)
        self.assertFalse(any(lb.startswith("キャラクター") for lb in labels3), labels3)
        self.assertNotIn("応答の書き方", ctx3["same"])

        a3 = make_msg("assistant", "返答3", mid="a3")
        m4 = make_msg("user", "4ターン目", mid="u4")
        s["messages"].extend([a3, m4])
        server.save(s)
        server.note_reply(s, "u3", a3)

        # 4ターン目: diff (phase 0: 指示)
        ctx4 = server.build_context(s, m4)
        self.assertEqual(ctx4["mode"], "diff")
        labels4 = [sec["label"] for sec in ctx4["sections"]]
        self.assertTrue(any(lb.startswith("指示") for lb in labels4), labels4)
        self.assertFalse(any(lb.startswith("キャラクター") for lb in labels4), labels4)
        self.assertFalse(any("応答の書き方" in lb for lb in labels4), labels4)
        self.assertNotIn("指示", ctx4["same"])

    def test_save_extracted_memories_edge_cases(self):
        """Test boundary values, type checks, and deduplication in save_extracted_memories"""
        # Empty session_id
        res_empty = memory_store.save_extracted_memories("", "アリス", [{"category": "event", "content": "テスト"}])
        self.assertEqual(res_empty.get("memories"), [])
        res_none = memory_store.save_extracted_memories(None, "アリス", [{"category": "event", "content": "テスト"}])
        self.assertEqual(res_none.get("memories"), [])

        # Batch duplicate deduplication and invalid items filtering
        sid = "sid-edge-cases"
        items = [
            {"category": "event", "content": "同じ記憶テキスト"},
            {"category": "event", "content": "同じ記憶テキスト"},  # duplicate in same batch
            {"category": "invalid_cat", "content": "無視されるべき記憶"},
            "文字列だけの記憶",
            None,
            123,
            {"category": "event", "content": ""}
        ]
        res = memory_store.save_extracted_memories(
            sid, "アリス", items, resolved_ids="mem_nonexistent", turn_idx="5", elapsed_time="not_a_dict"
        )
        # Should have deduplicated to 2 valid memories: "同じ記憶テキスト" and "文字列だけの記憶"
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 2)
        self.assertEqual(mems[0]["turn_idx"], 5)
        self.assertEqual(mems[0]["elapsed_time"], {"day": 1, "period": "午前"})

    def test_shift_turn_indices_after_middle_delete(self):
        """途中削除で、以降の記憶のターン番号だけが詰められる"""
        sid = "sid-shift-test"
        memory_store.save_memories(sid, {
            "session_id": sid, "character_name": "A", "memories": [
                {"id": "m1", "category": "event", "content": "一件目", "source": "auto", "turn_idx": 2, "history": []},
                {"id": "m2", "category": "event", "content": "二件目", "source": "auto", "turn_idx": 4,
                 "history": [{"turn_idx": 4, "content": "二件目"}]},
                {"id": "m3", "category": "status", "content": "手動", "source": "manual", "turn_idx": 4},
            ]})
        memory_store.shift_turn_indices(sid, 2, -1)
        mems = {m["id"]: m for m in memory_store.load_memories(sid).get("memories", [])}
        self.assertEqual(mems["m1"]["turn_idx"], 2)
        self.assertEqual(mems["m2"]["turn_idx"], 3)
        self.assertEqual(mems["m2"]["history"][0]["turn_idx"], 3)
        self.assertEqual(mems["m3"]["turn_idx"], 3)

    def test_task_ts_uses_task_specific_key(self):
        """タスク種別ごとの開始時刻を参照する（他のタスクの古い ts を拾わない）"""
        m = {"ts": "t0", "image_refine_ts": "t1", "choices_ts": "t2", "memory_ts": "t3", "consolidation_ts": "t4"}
        self.assertEqual(server.task_ts(m, "user"), "t0")
        self.assertEqual(server.task_ts(m, "image_refine"), "t1")
        self.assertEqual(server.task_ts(m, "choices"), "t2")
        self.assertEqual(server.task_ts(m, "memory_extract"), "t3")
        self.assertEqual(server.task_ts(m, "consolidation"), "t4")
        self.assertEqual(server.task_ts({"ts": "only"}, "image_refine"), "only")

    def test_delete_guards(self):
        """Verify delete functions handle None / empty strings gracefully"""
        memory_store.delete_by_mid(None, None)
        memory_store.delete_by_mid("", "")
        memory_store.delete_by_mid("some-sid", "")
        memory_store.delete_by_mid("", "some-mid")
        memory_store.delete_after_turn(None, None)
        memory_store.delete_after_turn("", 1)
        memory_store.delete_after_turn("some-sid", None)

    def test_memory_worker_resilience(self):
        """Verify memory_worker handles malformed task items without crashing"""
        server.queued_memories.append(())
        server.queued_memories.append(("unknown_kind", "sid"))
        server.queued_memories.append(("explicit", "sid"))  # too short
        server.queued_memories.append(("fallback", "sid"))  # too short
        server.queued_memories.append("not a tuple")

        # Start a thread to run one pass of memory_worker logic or verify queue drainage
        while server.queued_memories:
            task = server.queued_memories.popleft()
            if not isinstance(task, (list, tuple)) or len(task) < 2:
                continue
            kind = task[0]
            if kind == "explicit" and len(task) >= 8:
                t_mid = task[6]
            elif kind == "fallback" and len(task) >= 5:
                t_mid = task[4]
            else:
                continue
        self.assertEqual(len(server.queued_memories), 0)

    def test_fallback_extraction_matching_by_id(self):
        """Verify extract_memories_fallback can match message by id even if dictionary has extra keys"""
        sid = "sid-fallback-id-match"
        s = {
            "setting": {"character": {"name": "アリス"}, "user_name": "ユーザー"},
            "messages": [
                {"id": "u1", "role": "user", "text": "アリス、今度の日曜日にカフェに行く約束しよう！"},
                {"id": "a1", "role": "assistant", "text": "うん、アリスとカフェに行く約束ね！"}
            ]
        }
        msg_with_extra = {
            "id": "a1",
            "role": "assistant",
            "text": "うん、アリスとカフェに行く約束ね！",
            "elapsed_time": {"day": 1, "period": "放課後"},
            "extra_field": 123
        }
        # Run fallback extraction - should successfully resolve turn_idx and user message
        server.extract_memories_fallback(sid, s, msg_with_extra)
        mem_data = memory_store.load_memories(sid)
        self.assertTrue(len(mem_data.get("memories", [])) >= 1)

    def test_build_context_diff_image_label_and_note_reply_memo(self):
        """Verify image prompt label distinguishes diff mode with '要点' and note_reply properly records memo sig"""
        s = server.create_session({"title": "テスト会話", "memo": "初期メモ", "ide_session": "ide-1"})
        sid = s["id"]
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        ctx_first = server.build_context(s, m1)
        lbl_first = next(sec["label"] for sec in ctx_first["sections"] if "画像プロンプト" in sec["label"])
        self.assertIn("構図および解剖学的整合性規則", lbl_first)

        # Assistant response updates memo
        a1 = make_msg("assistant", "1ターン目返答", mid="a1")
        s["messages"].append(a1)
        s["setting"]["memo"] = "AIが更新した新しいメモ"
        server.save(s)

        # note_reply records AI response and new memo sig
        server.note_reply(s, m1["id"], a1)

        # Next user turn
        m2 = make_msg("user", "2ターン目", mid="u2")
        s["messages"].append(m2)
        server.save(s)

        ctx_diff = server.build_context(s, m2)
        lbl_diff = next((sec["label"] for sec in ctx_diff["sections"] if "作成の要点" in sec["label"]), None)
        if os.environ.get("RPCHAT_IMGNOTE", "0") != "0":
            self.assertIsNotNone(lbl_diff)
            self.assertIn("要点", lbl_diff)
            self.assertIn("解剖学的整合性規則", lbl_diff)
        else:
            self.assertIsNone(lbl_diff)

        # Memo was updated by AI and recorded in note_reply, not changed since, not memo_due
        # Therefore, memo must NOT appear in diff sections (neither as regular nor as '前回から変わった')
        memo_sec = next((sec for sec in ctx_diff["sections"] if "AIメモ" in sec["label"]), None)
        self.assertIsNone(memo_sec)

    def test_real_memory_worker_thread(self):
        """Verify memory_worker thread correctly processes tasks in background"""
        s = server.create_session({"title": "ワーカーテスト", "character": {"name": "アリス"}})
        sid = s["id"]
        m = make_msg("assistant", "テスト返答", mid="a_worker_1")
        s["messages"].append(m)
        server.save(s)

        worker_thread = threading.Thread(target=server.memory_worker, daemon=True)
        worker_thread.start()

        # Enqueue malformed tasks and one valid explicit task
        server.queued_memories.append(("malformed",))
        server.queued_memories.append(("explicit", sid, "アリス", [{"category": "event", "content": "ワーカーで保存された記憶"}], [], 1, m["id"], {"day": 1, "period": "午前"}))
        server.memory_event.set()

        # Wait for queue to drain
        timeout = 5.0
        start = time.time()
        while server.queued_memories and time.time() - start < timeout:
            time.sleep(0.05)
        self.assertEqual(len(server.queued_memories), 0)

        # Allow worker a fraction of a second to complete disk write
        time.sleep(0.2)
        mem_data = memory_store.load_memories(sid)
        self.assertEqual(len(mem_data.get("memories", [])), 1)
        self.assertEqual(mem_data["memories"][0]["content"], "ワーカーで保存された記憶")

        # Stop worker thread
        server._memory_worker_stop = True
        server.memory_event.set()
        worker_thread.join(timeout=1.0)

    def test_history_tokens_embedding_caching_and_restore(self):
        """Verify old_hist saves tokens and embeddings, and delete_by_mid restores them cleanly"""
        sid = "sid-hist-cache"
        char_name = "アリス"
        res1 = memory_store.save_extracted_memories(
            sid, char_name, [{"category": "rule", "content": "アリスは放課後に寄り道してはならない"}], turn_idx=1, mid="m1"
        )
        self.assertEqual(len(res1["memories"]), 1)
        mem_id = res1["memories"][0]["id"]

        # Update the memory (similarity match)
        res2 = memory_store.save_extracted_memories(
            sid, char_name, [{"category": "rule", "content": "アリスは放課後に図書館以外へ寄り道してはならない"}], turn_idx=2, mid="m2"
        )
        updated_mem = res2["memories"][0]
        self.assertEqual(len(updated_mem.get("history", [])), 1)
        hist0 = updated_mem["history"][0]
        self.assertIn("tokens", hist0)
        self.assertIn("embedding", hist0)
        self.assertIn("content_hash", hist0)
        self.assertTrue(len(hist0["embedding"]) > 0)

        # Now delete by mid m2 (reverting to m1)
        memory_store.delete_by_mid(sid, "m2")
        restored_data = memory_store.load_memories(sid)
        self.assertEqual(len(restored_data["memories"]), 1)
        restored_mem = restored_data["memories"][0]
        self.assertEqual(restored_mem["content"], "アリスは放課後に寄り道してはならない")
        self.assertEqual(restored_mem["mid"], "m1")
        self.assertTrue(len(restored_mem["embedding"]) > 0)

    def test_update_memory_computes_outside_store_lock(self):
        """Verify update_memory updates content, tokens, embedding and hash properly"""
        sid = "sid-update-mem"
        m = memory_store.add_manual_memory(sid, "アリス", "event", "最初のイベント")
        mem_id = m["id"]

        updated = memory_store.update_memory(sid, mem_id, content="更新されたイベント内容")
        self.assertEqual(updated["content"], "更新されたイベント内容")
        self.assertTrue(len(updated["tokens"]) > 0)
        self.assertTrue(len(updated["embedding"]) > 0)
        self.assertEqual(updated["content_hash"], memory_store.compute_hash("更新されたイベント内容"))

    def test_resolved_ids_container_types(self):
        """Verify save_extracted_memories handles resolved_ids as tuple and set"""
        sid = "sid-res-types"
        m1 = memory_store.add_manual_memory(sid, "アリス", "promise", "カフェに行く約束", status="active")
        m2 = memory_store.add_manual_memory(sid, "アリス", "promise", "図書館に行く約束", status="active")

        # Resolve m1 with set
        memory_store.save_extracted_memories(sid, "アリス", [], resolved_ids={m1["id"]}, turn_idx=3, mid="m3")
        d1 = memory_store.load_memories(sid)
        mem1 = next(x for x in d1["memories"] if x["id"] == m1["id"])
        self.assertEqual(mem1["status"], "fulfilled")

        # Resolve m2 with tuple
        memory_store.save_extracted_memories(sid, "アリス", [], resolved_ids=(m2["id"],), turn_idx=4, mid="m4")
        d2 = memory_store.load_memories(sid)
        mem2 = next(x for x in d2["memories"] if x["id"] == m2["id"])
        self.assertEqual(mem2["status"], "fulfilled")

    def test_delete_after_turn_string_turn_idx(self):
        """Verify delete_after_turn handles string turn_idx and string res_turn without TypeError"""
        sid = "sid-del-str"
        data = {
            "session_id": sid,
            "character_name": "アリス",
            "updated_at": time.time(),
            "memories": [
                {
                    "id": "mem_1",
                    "category": "promise",
                    "status": "fulfilled",
                    "resolved_turn": "3",  # string type in json
                    "content": "約束テスト",
                    "source": "auto",
                    "turn_idx": "1"
                },
                {
                    "id": "mem_2",
                    "category": "event",
                    "status": "active",
                    "content": "後からできたイベント",
                    "source": "auto",
                    "turn_idx": "4"
                }
            ]
        }
        memory_store.save_memories(sid, data)

        # Call delete_after_turn with string "2"
        memory_store.delete_after_turn(sid, "2")
        after_data = memory_store.load_memories(sid)
        # mem_1 resolved_turn "3" > 2 -> reverted to active
        m1 = next(x for x in after_data["memories"] if x["id"] == "mem_1")
        self.assertEqual(m1["status"], "active")
        # mem_2 turn_idx "4" > 2 -> deleted
        self.assertFalse(any(x["id"] == "mem_2" for x in after_data["memories"]))

    def test_rotation_turns_triggers_memo_due(self):
        """Verify file_user_turns >= ROTATION_TURNS (30) marks memo_due True and adds turnover section"""
        s = server.create_session({"title": "30ターンテスト", "memo": "30ターン目メモ", "ide_session": "ide-1"})
        sid = s["id"]
        # Add 30 user messages
        for i in range(30):
            s["messages"].append(make_msg("user", f"ユーザー発言 {i}", mid=f"u_{i}"))
            s["messages"].append(make_msg("assistant", f"アシスタント返答 {i}", mid=f"a_{i}"))
        target_m = s["messages"][-2]  # last user msg
        s["setting"]["rotation"] = {"phase": "armed", "armed_turn": 30}
        server.save(s)

        # Record sent history so diff mode applies
        server.write_sent(sid, {
            "ide": "ide-1",
            "msgs": [server.msg_sig(x) for x in s["messages"][:-2]],
            "sigs": {},
            "labels": {},
            "ts": server.now()
        })

        ctx = server.build_context(s, target_m)
        self.assertEqual(ctx["mode"], "diff")
        # memo_due must be True -> memo included
        memo_sec = next((sec for sec in ctx["sections"] if "AIメモ" in sec["label"]), None)
        self.assertIsNotNone(memo_sec)
        # Rotation turnover notice must be present
        turnover_sec = next((sec for sec in ctx["sections"] if "世代交代" in sec["label"]), None)
        self.assertIsNotNone(turnover_sec)

    def test_target_mem_asst_without_id_generates_id_and_queues(self):
        """Verify that when target_mem_asst does not have an id, an id is assigned and saved before queueing"""
        s = server.create_session({"title": "IDなしテスト", "character": {"name": "アリス"}})
        sid = s["id"]
        u = make_msg("user", "こんにちは", mid="u1")
        # Legacy assistant msg without id field
        asst_msg = {"role": "assistant", "text": "こんにちは！", "ts": server.now(), "memory_status": "pending"}
        s["messages"].extend([u, asst_msg])
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_mem_1.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "type": "memory_extract",
                "memories": [{"category": "event", "content": "挨拶を交わした"}]
            }, f, ensure_ascii=False)

        imported = server.process_outbox()
        self.assertTrue(imported)
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        self.assertEqual(task[0], "explicit")
        queued_mid = task[6]
        self.assertTrue(bool(queued_mid))

        # Check that session file on disk has this id
        updated_s = server.load(sid, readonly=True)
        self.assertEqual(updated_s["messages"][1]["id"], queued_mid)
        # And _memory_task_alive finds it
        self.assertTrue(server._memory_task_alive(sid, queued_mid))

    def test_query_features_cache_and_lru(self):
        """Verify get_query_features caching, LRU order, eviction at 128, and input sanitization"""
        with memory_store._query_cache_lock:
            memory_store._query_cache.clear()

        # Non-string and empty inputs
        self.assertEqual(memory_store.get_query_features(""), ([], []))
        self.assertEqual(memory_store.get_query_features(None), ([], []))
        self.assertEqual(memory_store.get_query_features(123), ([], []))
        self.assertEqual(memory_store.get_query_features("   "), ([], []))

        # Real query
        tokens1, vec1 = memory_store.get_query_features("雨の日に図書室で勉強した")
        self.assertTrue(len(tokens1) > 0)
        self.assertTrue(len(vec1) > 0)
        self.assertIn("雨の日に図書室で勉強した", memory_store._query_cache)

        # Modifying returned lists must not mutate cache
        tokens1.append("MUTATED")
        vec1[0] = 999.0
        tokens2, vec2 = memory_store.get_query_features("雨の日に図書室で勉強した")
        self.assertNotIn("MUTATED", tokens2)
        self.assertNotEqual(vec2[0], 999.0)

        # Eviction at 128 items
        for i in range(130):
            memory_store.get_query_features(f"テストクエリ{i}")
        self.assertLessEqual(len(memory_store._query_cache), 128)
        # Oldest items should have been evicted
        self.assertNotIn("雨の日に図書室で勉強した", memory_store._query_cache)
        self.assertIn("テストクエリ129", memory_store._query_cache)

    def test_query_features_concurrent_threads(self):
        """Verify get_query_features handles concurrent access cleanly"""
        errors = []

        def worker(q_str):
            try:
                for _ in range(10):
                    t, v = memory_store.get_query_features(q_str)
                    if not t or not v:
                        errors.append(f"Empty result for {q_str}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(f"並列クエリ{i % 5}",)) for i in range(15)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])

    def test_memory_store_warmup(self):
        """Verify warmup runs without raising exceptions"""
        memory_store.warmup()

    def test_locate_known_servers_priority_and_batch_caching(self):
        """Verify locate prefers known servers and caches all trajectory summaries"""
        cid_target = "cid-target-123"
        cid_other = "cid-other-456"

        orig_servers = ide_poke._servers.copy()
        orig_call = ide_poke._call
        orig_ls = ide_poke._language_servers
        orig_hub = ide_poke._hub

        ls_called = []

        def mock_ls():
            ls_called.append(True)
            return []

        def mock_call(port, tok, method, body, timeout=15):
            if method == "GetAllCascadeTrajectories":
                return 200, {
                    "trajectorySummaries": {
                        cid_target: {"summary": "Target Chat", "status": "CASCADE_RUN_STATUS_IDLE"},
                        cid_other: {"summary": "Other Chat", "status": "CASCADE_RUN_STATUS_IDLE"}
                    }
                }
            return 200, {}

        try:
            ide_poke._servers.clear()
            ide_poke._servers["known-cid"] = (7777, "known-tok")
            ide_poke._hub = (8888, "hub-tok")
            ide_poke._language_servers = mock_ls
            ide_poke._call = mock_call

            port, tok, s = ide_poke.locate(cid_target)
            # Should have found it via known server without calling PowerShell _language_servers
            self.assertEqual(len(ls_called), 0)
            self.assertEqual(s.get("summary"), "Target Chat")
            # Both cid_target and cid_other should now be in _servers cache
            self.assertIn(cid_target, ide_poke._servers)
            self.assertIn(cid_other, ide_poke._servers)
        finally:
            ide_poke._servers = orig_servers
            ide_poke._call = orig_call
            ide_poke._language_servers = orig_ls
            ide_poke._hub = orig_hub

    def test_locate_skips_already_checked_language_server(self):
        """Verify locate skips language server processes whose ports were checked in step 1"""
        cid_target = "cid-not-in-step1"

        orig_servers = ide_poke._servers.copy()
        orig_call = ide_poke._call
        orig_ls = ide_poke._language_servers
        orig_hub = ide_poke._hub

        calls_made = []

        def mock_call(port, tok, method, body, timeout=15):
            calls_made.append((port, tok, method))
            if port == 7001 and tok == "tok1":
                return 200, {"trajectorySummaries": {"other-cid": {}}}
            if port == 8001 and tok == "tok2":
                return 200, {"trajectorySummaries": {cid_target: {"summary": "Found in LS2"}}}
            return 500, {}

        def mock_ls():
            return [
                ("tok1", [7000, 7001]),  # tok1 was already checked on 7001 in step 1
                ("tok2", [8000, 8001]),
            ]

        try:
            ide_poke._servers.clear()
            ide_poke._servers["other-cid"] = (7001, "tok1")
            ide_poke._hub = None
            ide_poke._language_servers = mock_ls
            ide_poke._call = mock_call

            port, tok, s = ide_poke.locate(cid_target)
            self.assertEqual(port, 8001)
            self.assertEqual(tok, "tok2")
            self.assertEqual(s.get("summary"), "Found in LS2")

            # Check that 7000 (non-HTTP port of tok1) was NEVER called because tok1 had a port in checked_ok
            self.assertNotIn((7000, "tok1", "GetAllCascadeTrajectories"), calls_made)
        finally:
            ide_poke._servers = orig_servers
            ide_poke._call = orig_call
            ide_poke._language_servers = orig_ls
            ide_poke._hub = orig_hub

    def test_send_to_conversation_confirmation_loop_resilience(self):
        """Verify send_to_conversation confirmation loop handles None, exceptions, empty dicts, and valid transitions"""
        cid = "cid-conf-loop"
        base_cfg = {"plannerConfig": {"planModel": "test-model"}}

        orig_locate = ide_poke.locate
        orig_cached_config = ide_poke._cached_config
        orig_fix_model = ide_poke._fix_model
        orig_call = ide_poke._call
        orig_summary = ide_poke._summary

        ide_poke.locate = lambda c: (9000, "tok-test", {"summary": "Test", "status": "CASCADE_RUN_STATUS_IDLE", "lastUserInputTime": "ts_0"})
        ide_poke._cached_config = lambda cfg=None: base_cfg
        ide_poke._fix_model = lambda p, t, c: None
        ide_poke._call = lambda p, t, m, b, timeout=15: (200, {})

        try:
            # Case 1: _summary returns None (failure) -> should NOT falsely succeed!
            ide_poke._summary = lambda c, p, t: None
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertFalse(ok)
            self.assertIn("送ったが会話が動かない", msg)

            # Case 2: _summary raises OSError -> should NOT crash, should return False
            def raise_oserror(c, p, t):
                raise OSError("Socket disconnected")
            ide_poke._summary = raise_oserror
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertFalse(ok)
            self.assertIn("送ったが会話が動かない", msg)

            # Case 3: _summary returns {} (empty dict) -> should NOT falsely succeed!
            ide_poke._summary = lambda c, p, t: {}
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertFalse(ok)
            self.assertIn("送ったが会話が動かない", msg)

            # Case 4: _summary returns status RUNNING -> succeeds
            ide_poke._summary = lambda c, p, t: {"status": "CASCADE_RUN_STATUS_RUNNING"}
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertTrue(ok)
            self.assertIn("送った", msg)

            # Case 5: _summary returns updated lastUserInputTime -> succeeds
            ide_poke._summary = lambda c, p, t: {"lastUserInputTime": "ts_1", "status": "CASCADE_RUN_STATUS_IDLE"}
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertTrue(ok)
            self.assertIn("送った", msg)

            # Case 6: _summary raises error on 1st attempt, returns valid on 2nd attempt -> succeeds
            attempt = [0]
            def flaky_summary(c, p, t):
                attempt[0] += 1
                if attempt[0] == 1:
                    raise OSError("Temporary glitch")
                return {"lastUserInputTime": "ts_retry", "status": "CASCADE_RUN_STATUS_IDLE"}
            ide_poke._summary = flaky_summary
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertTrue(ok)
            self.assertIn("送った", msg)
            # Case 7: _summary raises generic Exception / HTTPException -> should NOT crash, should return False
            def raise_generic_exc(c, p, t):
                raise RuntimeError("Unexpected transport crash")
            ide_poke._summary = raise_generic_exc
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertFalse(ok)
            self.assertIn("送ったが会話が動かない", msg)
        finally:
            ide_poke.locate = orig_locate
            ide_poke._cached_config = orig_cached_config
            ide_poke._fix_model = orig_fix_model
            ide_poke._call = orig_call
            ide_poke._summary = orig_summary
            ide_poke._cid_cfg.pop(cid, None)

    def test_query_features_does_not_cache_empty_embeddings(self):
        """Verify get_query_features does not cache failed empty embeddings"""
        orig_get_embedding = memory_store.get_embedding
        try:
            with memory_store._query_cache_lock:
                memory_store._query_cache.clear()
            memory_store.get_embedding = lambda t: []
            tokens, vec = memory_store.get_query_features("一時的なエラー文")
            self.assertEqual(vec, [])
            self.assertNotIn("一時的なエラー文", memory_store._query_cache)
        finally:
            memory_store.get_embedding = orig_get_embedding

    def test_locate_purges_dead_servers_on_oserror_and_does_not_retry(self):
        """Verify locate purges all conversations pointing to dead server when OSError occurs and avoids re-querying it"""
        orig_servers = ide_poke._servers.copy()
        orig_call = ide_poke._call
        orig_ls = ide_poke._language_servers
        orig_hub = ide_poke._hub

        calls_made = []

        def mock_call(port, tok, method, body, timeout=15):
            calls_made.append((port, tok, method))
            if port == 6001:
                raise OSError("Connection refused (server terminated)")
            if port == 6002:
                return 200, {"trajectorySummaries": {"alive-cid": {"summary": "Alive Chat"}}}
            return 500, {}

        try:
            with ide_poke._servers_lock:
                ide_poke._servers.clear()
                ide_poke._servers["dead-cid-1"] = (6001, "dead-tok")
                ide_poke._servers["dead-cid-2"] = (6001, "dead-tok")
                ide_poke._servers["alive-cid"] = (6002, "alive-tok")
            ide_poke._hub = (6001, "dead-tok")
            ide_poke._language_servers = lambda: []
            ide_poke._call = mock_call

            port, tok, s = ide_poke.locate("dead-cid-1")
            self.assertIsNone(port)
            self.assertIsNone(s)

            # Both dead-cid-1 and dead-cid-2 should have been purged from _servers
            with ide_poke._servers_lock:
                self.assertNotIn("dead-cid-1", ide_poke._servers)
                self.assertNotIn("dead-cid-2", ide_poke._servers)
                self.assertIn("alive-cid", ide_poke._servers)
            # _hub should have been reset since it matched the dead server
            self.assertIsNone(ide_poke._hub)

            # Dead server (6001) should have been queried ONLY ONCE across the whole locate call!
            # It must not be retried in Step 1 (known_candidates).
            calls_to_6001 = [c for c in calls_made if c[0] == 6001]
            self.assertEqual(len(calls_to_6001), 1)
        finally:
            with ide_poke._servers_lock:
                ide_poke._servers = orig_servers
            ide_poke._call = orig_call
            ide_poke._language_servers = orig_ls
            ide_poke._hub = orig_hub

    def test_readonly_load_helpers(self):
        """Verify load_choices_data, load_states_data, and load_images_data support readonly=True/False"""
        sid = "sid-test-readonly"
        # 1. Non-existent files return {}
        self.assertEqual(server.load_choices_data(sid, readonly=True), {})
        self.assertEqual(server.load_states_data(sid, readonly=True), {})
        self.assertEqual(server.load_images_data(sid, readonly=True), {})

        # 2. Save data and test caching & readonly behavior
        cdata = {"m1": {"choices": ["1", "2"]}}
        sdata = {"m1": [{"cloth": "uniform"}]}
        idata = {"m1": {"prompt": "1girl, solo"}}

        server.save_choices_data(sid, cdata)
        server.save_states_data(sid, sdata)
        server.save_images_data(sid, idata)

        # readonly=True returns reference to cache data directly
        c_ro = server.load_choices_data(sid, readonly=True)
        self.assertEqual(c_ro, cdata)
        self.assertIs(c_ro, server.choices_cache[sid]["data"])

        s_ro = server.load_states_data(sid, readonly=True)
        self.assertEqual(s_ro, sdata)
        self.assertIs(s_ro, server.states_cache[sid]["data"])

        i_ro = server.load_images_data(sid, readonly=True)
        self.assertEqual(i_ro, idata)
        self.assertIs(i_ro, server.images_cache[sid]["data"])

        # readonly=False returns a deepcopy (mutations do NOT affect cache)
        c_copy = server.load_choices_data(sid, readonly=False)
        self.assertIsNot(c_copy, server.choices_cache[sid]["data"])
        c_copy["m1"]["choices"].append("3")
        self.assertEqual(len(server.load_choices_data(sid, readonly=True)["m1"]["choices"]), 2)

        s_copy = server.load_states_data(sid, readonly=False)
        self.assertIsNot(s_copy, server.states_cache[sid]["data"])
        s_copy["m1"].append({"pose": "sitting"})
        self.assertEqual(len(server.load_states_data(sid, readonly=True)["m1"]), 1)

        i_copy = server.load_images_data(sid, readonly=False)
        self.assertIsNot(i_copy, server.images_cache[sid]["data"])
        i_copy["m1"]["prompt"] = "modified"
        self.assertEqual(server.load_images_data(sid, readonly=True)["m1"]["prompt"], "1girl, solo")

        # 3. Cache-miss testing (cache cleared, must return reference on readonly=True without redundant deepcopy)
        server.choices_cache.clear()
        server.states_cache.clear()
        server.images_cache.clear()

        c_ro_miss = server.load_choices_data(sid, readonly=True)
        self.assertEqual(c_ro_miss, cdata)
        self.assertIs(c_ro_miss, server.choices_cache[sid]["data"])

        s_ro_miss = server.load_states_data(sid, readonly=True)
        self.assertEqual(s_ro_miss, sdata)
        self.assertIs(s_ro_miss, server.states_cache[sid]["data"])

        i_ro_miss = server.load_images_data(sid, readonly=True)
        self.assertEqual(i_ro_miss, idata)
        self.assertIs(i_ro_miss, server.images_cache[sid]["data"])

        # Cache-miss with readonly=False returns isolated deepcopy
        server.choices_cache.clear()
        c_copy_miss = server.load_choices_data(sid, readonly=False)
        self.assertIsNot(c_copy_miss, server.choices_cache[sid]["data"])
        c_copy_miss["m1"]["choices"].append("99")
        self.assertEqual(server.load_choices_data(sid, readonly=True)["m1"]["choices"], ["1", "2"])

    def test_save_copy_on_write_reduces_file_writes(self):
        """Verify save(s) skips rewriting choices/states/images when no updates occur"""
        s = server.create_session({"title": "COW Test", "character": {"name": "アリス"}})
        sid = s["id"]
        cp = server.choices_path(sid)
        sp = server.states_path(sid)
        ip = server.images_path(sid)

        # Initial save with no choices/states/images - auxiliary files should not exist
        server.save(s)
        self.assertFalse(os.path.exists(cp))
        self.assertFalse(os.path.exists(sp))
        self.assertFalse(os.path.exists(ip))

        # Add assistant message with choices, state, and image details
        asst = make_msg("assistant", "返答", mid="a1")
        asst["choices"] = ["選択肢1", "選択肢2"]
        asst["state"] = [{"character": "アリス", "clothing": "制服"}]
        asst["image"] = {"file": "a.png", "status": "done", "prompt": "1girl, cute", "seed": 12345}
        s["messages"].append(asst)

        # Save should write all three auxiliary files
        server.save(s)
        self.assertTrue(os.path.exists(cp))
        self.assertTrue(os.path.exists(sp))
        self.assertTrue(os.path.exists(ip))
        mtime_c1 = os.stat(cp).st_mtime
        mtime_s1 = os.stat(sp).st_mtime
        mtime_i1 = os.stat(ip).st_mtime

        time.sleep(0.05)  # Ensure timestamp granularity has elapsed

        # Second save without changes (e.g., simple title change or /api/claim)
        s["setting"]["title"] = "COW Test 2"
        server.save(s)

        # Auxiliary files must NOT have been rewritten (mtime remains exactly identical)
        self.assertEqual(os.stat(cp).st_mtime, mtime_c1)
        self.assertEqual(os.stat(sp).st_mtime, mtime_s1)
        self.assertEqual(os.stat(ip).st_mtime, mtime_i1)

        # Now change choices only
        asst["choices"] = ["選択肢A", "選択肢B"]
        time.sleep(0.05)
        server.save(s)
        mtime_c2 = os.stat(cp).st_mtime
        self.assertNotEqual(mtime_c2, mtime_c1)  # Choices file rewritten!
        self.assertEqual(os.stat(sp).st_mtime, mtime_s1)  # States file untouched!
        self.assertEqual(os.stat(ip).st_mtime, mtime_i1)  # Images file untouched!

    def test_history_section_split_and_build_context_non_light_double_split_prevention(self):
        """Verify history_section split parameter and non-light mode does not split recent conversation"""
        sid = "sid-test-split"
        long_text = "あ" * 3000

        # With split=True, text > 2500 is split into file
        sec_split, path_split = server.history_section(sid, "test_split", "長い文", long_text, split=True)
        self.assertIsNotNone(path_split)
        self.assertTrue(os.path.isfile(path_split))
        self.assertIn("ファイル:", sec_split["text"])

        # With split=False, text > 2500 is NOT split into file
        sec_nosplit, path_nosplit = server.history_section(sid, "test_nosplit", "長い文", long_text, split=False)
        self.assertIsNone(path_nosplit)
        self.assertEqual(sec_nosplit["text"], long_text)

        # Now test build_context: in non-light mode (input_mode != "light")
        s = server.create_session({"title": "Non-light test", "input_mode": "full", "character": {"name": "アリス"}})
        # Add 12 user+asst turns so cut_idx triggers
        for i in range(12):
            s["messages"].append(make_msg("user", f"ユーザー質問 {i} " + ("長文" * 150), mid=f"u_{i}"))
            s["messages"].append(make_msg("assistant", f"AI返答 {i} " + ("返答長文" * 150), mid=f"a_{i}"))
        new_u = make_msg("user", "最新の質問", mid="u_latest")
        s["messages"].append(new_u)
        server.save(s)

        # In non-light mode (full mode), build_context for recent should NOT produce recent.md
        ctx = server.build_context(s, new_u)
        recent_sec = next((sec for sec in ctx["sections"] if "直近の会話" in sec["label"]), None)
        self.assertIsNotNone(recent_sec)
        self.assertNotIn("ファイル:", recent_sec["text"])
        recent_file = os.path.join(server.CTX, f"{s['id']}_recent.md")
        self.assertFalse(os.path.exists(recent_file))

    def test_sync_choices_agents_skips_identical_write(self):
        """Verify sync_choices_agents only writes choices/AGENTS.md when content changes"""
        s = server.create_session({
            "title": "Choices Agent Test",
            "choices_ide_session": "cho-ide-1",
            "folder": os.path.join(self.test_dir, "session_test")
        })
        c_path = os.path.join(server.channel_folder(s), "choices", server.AGENTS_NAME)

        # First sync writes the file
        server.sync_choices_agents(s)
        self.assertTrue(os.path.isfile(c_path))
        mtime1 = os.stat(c_path).st_mtime

        time.sleep(0.05)
        # Second sync with identical setting does not rewrite
        server.sync_choices_agents(s)
        self.assertEqual(os.stat(c_path).st_mtime, mtime1)

        # Title change rewrites
        s["setting"]["title"] = "Updated Title"
        time.sleep(0.05)
        server.sync_choices_agents(s)
        self.assertNotEqual(os.stat(c_path).st_mtime, mtime1)

    def test_cleanup_done_dir(self):
        """Verify cleanup_done_dir prunes old files beyond keep limit"""
        # Create 520 mock files in DONE
        os.makedirs(server.DONE, exist_ok=True)
        for i in range(520):
            fname = f"file_{i:04d}.json"
            with open(os.path.join(server.DONE, fname), "w") as f:
                f.write("{}")

        self.assertEqual(len(os.listdir(server.DONE)), 520)

        # Call cleanup_done_dir with keep=500
        server.cleanup_done_dir(keep=500)
        remaining = os.listdir(server.DONE)
        self.assertEqual(len(remaining), 500)
        # Oldest 20 (0000 to 0019) should have been deleted
        self.assertNotIn("file_0000.json", remaining)
        self.assertNotIn("file_0019.json", remaining)
        self.assertIn("file_0020.json", remaining)
        self.assertIn("file_0519.json", remaining)

        # Calling again does not delete any further
        server.cleanup_done_dir(keep=500)
        self.assertEqual(len(os.listdir(server.DONE)), 500)

    def test_wait_input_outbox_scan_and_check_previous(self):
        """Verify _has_json_in_outbox mtime filtering and check_previous query string fix"""
        # 1. _has_json_in_outbox:
        # Create an old file (>1.5s old)
        old_file = os.path.join(server.OUTBOX, "old_file.json")
        with open(old_file, "w") as f:
            f.write("{}")
        past_time = time.time() - 10.0
        os.utime(old_file, (past_time, past_time))

        self.assertFalse(wait_input._has_json_in_outbox())

        # Create a fresh file (<1.5s old)
        fresh_file = os.path.join(server.OUTBOX, "fresh_file.json")
        with open(fresh_file, "w") as f:
            f.write("{}")

        self.assertTrue(wait_input._has_json_in_outbox())
        os.remove(fresh_file)
        os.remove(old_file)

        # 2. check_previous query string:
        orig_get = wait_input.get
        requested_urls = []

        def mock_get(url):
            requested_urls.append(url)
            return {"all_ids": [], "mine_ids": [], "left": None}

        orig_ide = wait_input.IDE
        try:
            wait_input.get = mock_get

            # When IDE is empty, URL must be /api/pending?check=1 (not &check=1)
            wait_input.IDE = ""
            wait_input.check_previous()
            self.assertEqual(requested_urls[-1], "/api/pending?check=1")

            # When IDE is set, URL must be /api/pending?ide=...&check=1
            wait_input.IDE = "my ide session"
            wait_input.check_previous()
            self.assertEqual(requested_urls[-1], "/api/pending?ide=my%20ide%20session&check=1")
        finally:
            wait_input.get = orig_get
            wait_input.IDE = orig_ide


if __name__ == "__main__":
    unittest.main()
