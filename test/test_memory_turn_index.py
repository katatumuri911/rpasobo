# -*- coding: utf-8 -*-
"""Tier A および共通ターン計算のテスト。

§10.1: T1～T7 (共通関数・異常系・互換性)
§10.2: A1～A6 (Tier A 実経路・出所整合・作中時刻 null 存続)
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rpchat.turn_index import (
    calc_turn_idx,
    turn_index_base,
    build_source_index,
    resolve_source,
)
import read_past_messages
import server
import memory_store


class TestTurnIndexCalculation(unittest.TestCase):
    """§10.1 共通ターン計算（T1～T7）"""

    def test_t1_60_turns_3rd_assistant(self):
        """T1: 60ユーザー分の会話で3ターン目assistant -> 通算ターン 3"""
        msgs = []
        for u in range(1, 61):
            msgs.append({"role": "user", "text": f"user {u}"})
            msgs.append({"role": "assistant", "text": f"asst {u}"})
        # 3ターン目 assistant はインデックス 5
        self.assertEqual(calc_turn_idx(msgs, 5, total_turns=60), 3)

    def test_t2_total_60_file_has_41_to_60_2nd_user(self):
        """T2: total=60, ファイルには過去41～60の20ユーザー分, 2番目user -> 42"""
        msgs = []
        for u in range(41, 61):
            msgs.append({"role": "user", "text": f"user {u}"})
            msgs.append({"role": "assistant", "text": f"asst {u}"})
        # 2番目 user はインデックス 2 (user 42)
        self.assertEqual(calc_turn_idx(msgs, 2, total_turns=60), 42)
        # その直後の assistant はインデックス 3 -> 42
        self.assertEqual(calc_turn_idx(msgs, 3, total_turns=60), 42)

    def test_t3_legacy_total_0_rotation_2_file_10_3rd_user(self):
        """T3: total=0, rotation_count=2, ファイル10user, 3番目のuser -> 43 (3に後退させない)"""
        setting = {"total_turns": 0, "rotation_count": 2}
        msgs = []
        for u in range(1, 11):
            msgs.append({"role": "user", "text": f"user {u}"})
            msgs.append({"role": "assistant", "text": f"asst {u}"})
        base_total = turn_index_base(setting, 10, rotation_period=20)
        self.assertEqual(base_total, 50)
        # 3番目 user (index 4) またはその直後の asst (index 5)
        self.assertEqual(calc_turn_idx(msgs, 4, base_total), 43)
        self.assertEqual(calc_turn_idx(msgs, 5, base_total), 43)

    def test_t4_assistant_before_any_user(self):
        """T4: 先行userが無い単独assistant -> 0"""
        msgs = [
            {"role": "assistant", "text": "初期挨拶"},
            {"role": "user", "text": "こんにちは"},
            {"role": "assistant", "text": "返答"},
        ]
        self.assertEqual(calc_turn_idx(msgs, 0, total_turns=1), 0)
        self.assertEqual(calc_turn_idx(msgs, 1, total_turns=1), 1)
        self.assertEqual(calc_turn_idx(msgs, 2, total_turns=1), 1)

    def test_t5_metadata_discrepancy_fallback(self):
        """T5: total < file_user_count の不整合 -> 例外なく相対番号へフォールバックし、警告ログを抑制して出力"""
        from rpchat.turn_index import _warned_mismatches
        _warned_mismatches.clear()
        msgs = [{"role": "user", "text": f"u{i}"} for i in range(10)]
        # total_turns=5 なのに file には 10 ユーザーある不整合
        with self.assertLogs("rpchat.turn_index", level="WARNING") as cm:
            idx1 = calc_turn_idx(msgs, 5, total_turns=5)
            self.assertEqual(idx1, 6)
            # 2回目の呼び出しでは警告が抑制される（ログ件数が増えない）
            idx2 = calc_turn_idx(msgs, 7, total_turns=5)
            self.assertEqual(idx2, 8)
        self.assertEqual(len(cm.output), 1, "同一不整合の警告ログが1回に抑制されていること")

    def test_t6_pruned_exchanges_and_consecutive_users(self):
        """T6: prune済みで連続user、user/assistant混在 -> assistantは直前userと同番号"""
        msgs = [
            {"role": "user", "text": "u1"},
            {"role": "user", "text": "u2"},
            {"role": "assistant", "text": "a2"},
            {"role": "user", "text": "u3"},
            {"role": "assistant", "text": "a3_1"},
            {"role": "assistant", "text": "a3_2"},
        ]
        # total_turns=20, file user count=3
        # u1 (idx 0) -> 20 - (3 - 1) = 18
        # u2 (idx 1) -> 20 - (3 - 2) = 19
        # a2 (idx 2) -> 19
        # u3 (idx 3) -> 20 - (3 - 3) = 20
        # a3_1 (idx 4) -> 20
        # a3_2 (idx 5) -> 20
        self.assertEqual(calc_turn_idx(msgs, 0, total_turns=20), 18)
        self.assertEqual(calc_turn_idx(msgs, 1, total_turns=20), 19)
        self.assertEqual(calc_turn_idx(msgs, 2, total_turns=20), 19)
        self.assertEqual(calc_turn_idx(msgs, 3, total_turns=20), 20)
        self.assertEqual(calc_turn_idx(msgs, 4, total_turns=20), 20)
        self.assertEqual(calc_turn_idx(msgs, 5, total_turns=20), 20)

    def test_t7_read_past_messages_standalone(self):
        """T7: read_past_messages.py をヘビー依存のない環境で import 可能"""
        self.assertTrue(callable(read_past_messages.calc_turn_idx))
        msgs = [{"role": "user", "text": "u1"}, {"role": "assistant", "text": "a1"}]
        self.assertEqual(read_past_messages.calc_turn_idx(msgs, 1, total_turns=1), 1)

    def test_t8_resolve_source_with_idless_message_identity(self):
        """T8: IDを持たないメッセージでも、配列上の同一性(id(msg))で出所が特定できる。別インスタンスや配列外は除外される"""
        asst_msg = {"role": "assistant", "text": "こんにちは！カフェに行こう", "elapsed_time": {"day": 2, "period": "昼"}}
        msgs = [
            {"role": "user", "text": "こんにちは"},
            asst_msg
        ]
        source_index = build_source_index(msgs, {"total_turns": 1}, rotation_period=20)

        # 配列内の同一オブジェクトインスタンス -> 解決可能
        src = resolve_source(source_index, asst_msg)
        self.assertIsNotNone(src, "配列内のIDなしメッセージは同一性から出所を特定できること")
        self.assertEqual(src["turn_idx"], 1)
        self.assertEqual(src["elapsed_time"], {"day": 2, "period": "昼"})

        # copy した別インスタンス（id(msg) が異なるIDなしメッセージ） -> 出所不明 (None)
        asst_copy = copy.deepcopy(asst_msg)
        src_copy = resolve_source(source_index, asst_copy)
        self.assertIsNone(src_copy, "別インスタンスのIDなしメッセージは誤帰属防止のため除外(None)されること")

        # 同一文が複数ターンにある場合でも、オブジェクト同一性で正しくそれぞれのターンに解決される
        asst_dup1 = {"role": "assistant", "text": "（うなずく）"}
        asst_dup2 = {"role": "assistant", "text": "（うなずく）"}
        msgs2 = [
            {"id": "u1", "role": "user", "text": "問1"},
            asst_dup1,
            {"id": "u2", "role": "user", "text": "問2"},
            asst_dup2,
        ]
        source_index2 = build_source_index(msgs2, {"total_turns": 2}, rotation_period=20)
        self.assertEqual(resolve_source(source_index2, asst_dup1)["turn_idx"], 1)
        self.assertEqual(resolve_source(source_index2, asst_dup2)["turn_idx"], 2)

    def test_t9_resolve_source_integer_id(self):
        """T9: 整数IDを持つメッセージでも、int / str の双方で resolve_source できる"""
        msgs = [
            {"id": 42, "role": "user", "text": "u42"},
            {"id": 43, "role": "assistant", "text": "a43", "elapsed_time": {"day": 1, "period": "朝"}}
        ]
        source_index = build_source_index(msgs, {"total_turns": 1}, rotation_period=20)
        src_by_int = resolve_source(source_index, 43)
        src_by_str = resolve_source(source_index, "43")
        self.assertIsNotNone(src_by_int)
        self.assertIsNotNone(src_by_str)
        self.assertEqual(src_by_int["turn_idx"], 1)
        self.assertEqual(src_by_str["turn_idx"], 1)
        self.assertEqual(src_by_int["mid"], "43")


class TestTierAPathways(unittest.TestCase):
    """§10.2 Tier A 実経路の検証（A1～A6）"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_tier_a_")
        self.orig_DATA = server.DATA
        self.orig_SESS_DIR = server.SESS_DIR
        self.orig_RAG_DIR = server.RAG_DIR
        self.orig_OUTBOX = server.OUTBOX
        self.orig_DONE = server.DONE
        self.orig_SENT = server.SENT
        self.orig_IMAGES_DIR = server.IMAGES_DIR
        self.orig_ms_DATA = memory_store.DATA
        self.orig_ms_RAG = memory_store.RAG_DIR
        self.orig_embed = memory_store.get_embeddings_batch

        server.DATA = os.path.join(self.test_dir, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.RAG_DIR = os.path.join(server.DATA, "rag")
        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        server.DONE = os.path.join(self.test_dir, "done")
        server.SENT = os.path.join(self.test_dir, "sent")
        server.IMAGES_DIR = os.path.join(self.test_dir, "images")
        for d in (server.SESS_DIR, server.RAG_DIR, server.OUTBOX, server.DONE, server.SENT, server.IMAGES_DIR):
            os.makedirs(d, exist_ok=True)
        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = server.RAG_DIR

        # 決定的なダミー埋め込み
        memory_store.get_embeddings_batch = lambda texts: [[0.05 * (i + 1)] * 8 for i in range(len(texts))]

        server.session_cache.clear()
        memory_store._memory_cache.clear()

    def tearDown(self):
        server.DATA = self.orig_DATA
        server.SESS_DIR = self.orig_SESS_DIR
        server.RAG_DIR = self.orig_RAG_DIR
        server.OUTBOX = self.orig_OUTBOX
        server.DONE = self.orig_DONE
        server.SENT = self.orig_SENT
        server.IMAGES_DIR = self.orig_IMAGES_DIR
        memory_store.DATA = self.orig_ms_DATA
        memory_store.RAG_DIR = self.orig_ms_RAG
        memory_store.get_embeddings_batch = self.orig_embed
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def test_a1_and_a2_fallback_timeout_sweep(self):
        """A1, A2: 3ターン目 assistant のタイムアウトスイープで出所（turn_idx=3, mid, elapsed_time）が保存され、status が消去される"""
        sid = "sess_a1_a2"
        now = time.time()
        msgs = []
        for u in range(1, 11):
            msgs.append({"id": f"u{u}", "role": "user", "text": f"ユーザー発言 {u}", "turn": u})
            a_msg = {
                "id": f"a{u}",
                "role": "assistant",
                "text": f"アシスタント返答 {u}",
                "turn": u,
                "elapsed_time": {"day": 1, "period": "朝" if u <= 2 else ("放課後" if u == 3 else "夜")}
            }
            if u == 3:
                a_msg["text"] = "うん、カフェに行く約束ね！絶対に忘れないよ。"
                a_msg["memory_status"] = "pending"
                a_msg["memory_ts"] = now - 300  # 180sタイムアウト超過
            msgs.append(a_msg)

        s = {
            "id": sid,
            "setting": {
                "character": {"name": "アリス"},
                "user_name": "指揮官",
                "total_turns": 10,
                "elapsed_time": {"day": 5, "period": "夜"},  # 現在時刻は5日目
            },
            "messages": msgs
        }
        server.save(s)

        # server.load(sid) でタイムアウトスイープを発火
        loaded_s = server.load(sid)

        # A1: 保存された記憶の検証
        mem_data = memory_store.load_memories(sid, "アリス")
        mems = mem_data.get("memories", [])
        self.assertTrue(len(mems) >= 1, "フォールバックで記憶が保存されていること")
        target_mem = next((m for m in mems if "カフェ" in m.get("content", "")), None)
        self.assertIsNotNone(target_mem)
        self.assertEqual(target_mem.get("turn_idx"), 3, "turn_idx が現在ターン10ではなく3であること")
        self.assertEqual(target_mem.get("mid"), "a3", "mid が a3 であること")
        self.assertEqual(target_mem.get("elapsed_time"), {"day": 1, "period": "放課後"}, "作中時刻が現在時刻ではなくメッセージ時点であること")

        # A2: ステータスの後始末
        msg_a3 = next(m for m in loaded_s["messages"] if m["id"] == "a3")
        self.assertNotIn("memory_status", msg_a3)
        self.assertNotIn("memory_ts", msg_a3)
        self.assertNotIn("memory_claimed_ts", msg_a3)

    def test_a3_unregistered_message_not_saved(self):
        """A3: messages に存在しないIDのメッセージは保存せず終了する"""
        sid = "sess_a3"
        s = {
            "id": sid,
            "setting": {"character": {"name": "アリス"}, "total_turns": 5},
            "messages": [
                {"id": "u1", "role": "user", "text": "こんにちは"},
                {"id": "a1", "role": "assistant", "text": "こんにちは"},
            ]
        }
        phantom_msg = {
            "id": "ghost_msg",
            "role": "assistant",
            "text": "アリスと海へ行く約束をした",
            "elapsed_time": {"day": 1, "period": "朝"}
        }
        server.extract_memories_fallback(sid, s, phantom_msg)
        mems = memory_store.load_memories(sid).get("memories", [])
        self.assertEqual(len(mems), 0, "存在しないメッセージからは記憶を保存しないこと")

    def test_a4_message_without_id(self):
        """A4: IDを持たないメッセージは配列内に存在すれば正しく処理され、無ければ保存しない"""
        sid = "sess_a4"
        asst_msg = {
            "role": "assistant",
            "text": "アリスとカフェへ行く約束をした",
            "elapsed_time": {"day": 1, "period": "朝"}
        }
        s = {
            "id": sid,
            "setting": {"character": {"name": "アリス"}, "total_turns": 1},
            "messages": [
                {"role": "user", "text": "約束しよう"},
                asst_msg,
            ]
        }
        # 配列内にある場合
        server.extract_memories_fallback(sid, s, asst_msg)
        mems = memory_store.load_memories(sid).get("memories", [])
        self.assertTrue(len(mems) >= 1)
        for m in mems:
            self.assertEqual(m["turn_idx"], 1)

        # 配列外にあるIDなしメッセージ
        memory_store.delete_session_memories(sid, to_trash=False)
        orphan_msg = {
            "role": "assistant",
            "text": "アリスとカフェへ行く約束をした",
            "elapsed_time": {"day": 1, "period": "朝"}
        }
        server.extract_memories_fallback(sid, s, orphan_msg)
        mems = memory_store.load_memories(sid).get("memories", [])
        self.assertEqual(len(mems), 0)

    def test_a5_legacy_session_new_message_registration(self):
        """A5: total_turns=0, rotation_count=2, file=10user で新規assistantを追記時、process_outbox 実経路で turn_idx が 50 になる"""
        s = server.create_session({"title": "旧セッション", "character": {"name": "アリス"}})
        sid = s["id"]
        s["setting"]["total_turns"] = 0
        s["setting"]["rotation_count"] = 2

        # 10 user messages in file: u1..u9 with a1..a9, and u10 waiting for reply
        for i in range(1, 10):
            s["messages"].append({"id": f"u{i}", "role": "user", "text": f"発言 {i}"})
            s["messages"].append({"id": f"a{i}", "role": "assistant", "text": f"返答 {i}"})
        s["messages"].append({"id": "u10", "role": "user", "text": "最新の発言", "status": "pending"})
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_asst_10.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "u10",
                "text": "最新の返答です。",
                "memories": [
                    {"category": "event", "content": "10ターン目の出来事"}
                ]
            }, f, ensure_ascii=False)

        server.queued_memories.clear()
        saved_calls = []
        orig_save = memory_store.save_extracted_memories

        def mock_save(*args, **kwargs):
            saved_calls.append((args, kwargs))
            return orig_save(*args, **kwargs)

        try:
            memory_store.save_extracted_memories = mock_save
            imported = server.process_outbox()
            self.assertTrue(imported)

            # 1. server.process_outbox 内で queued_memories に入ったタスクの turn_idx が 50 であること
            self.assertEqual(len(server.queued_memories), 1)
            task = server.queued_memories[0]
            # task = ("explicit", sid, ch_name, combined, resolved, turn_idx, msg["id"], el_snapshot)
            self.assertEqual(task[5], 50, "queued_memories の turn_idx が 50（10へ後退しない）であること")

            # 2. ワーカー処理（または save_extracted_memories 呼び出し）を通しても turn_idx=50 が渡されること
            t_kind, m_sid, m_ch, m_list, m_res, m_turn, m_id, m_el = server.queued_memories.popleft()
            memory_store.save_extracted_memories(
                m_sid, m_ch, m_list, resolved_ids=m_res, turn_idx=m_turn, mid=m_id, elapsed_time=m_el
            )
            self.assertTrue(len(saved_calls) >= 1)
            self.assertEqual(saved_calls[-1][1].get("turn_idx"), 50)

            # 3. 実際に保存された記憶の turn_idx が 50 であること
            mems = memory_store.load_memories(sid).get("memories", [])
            self.assertTrue(len(mems) >= 1)
            self.assertEqual(mems[0]["turn_idx"], 50)
        finally:
            memory_store.save_extracted_memories = orig_save

    def test_a6_unknown_elapsed_time_persists_null(self):
        """A6: 作中時刻スナップショットが無いメッセージは None で保存され、再読込しても None のまま維持される"""
        sid = "sess_a6"
        s = {
            "id": sid,
            "setting": {
                "character": {"name": "アリス"},
                "total_turns": 4,
                "elapsed_time": {"day": 3, "period": "昼"},  # セッション現在時刻
            },
            "messages": [
                {"id": "u1", "role": "user", "text": "約束しよう"},
                {
                    "id": "a1",
                    "role": "assistant",
                    "text": "アリスとカフェへ行く約束をした",
                    # elapsed_time / snapshot なし
                }
            ]
        }
        server.save(s)
        server.extract_memories_fallback(sid, s, s["messages"][1])

        # 保存直後の記憶
        mem_data = memory_store.load_memories(sid)
        mems = mem_data.get("memories", [])
        self.assertTrue(len(mems) >= 1)
        for mem in mems:
            self.assertIsNone(mem.get("elapsed_time"), "現在の作中時刻 (3日目・昼) で補填されず None であること")
            self.assertEqual(mem.get("turn_idx"), 4)

        # 再度 load_memories を呼んでも None が維持され、デフォルト補完されないこと
        reloaded = memory_store.load_memories(sid)
        for mem in reloaded.get("memories", []):
            self.assertIsNone(mem.get("elapsed_time"))

        # format_prompt_memories で日付表示ではなく「約Nターン前」にフォールバックすること
        formatted = memory_store.format_prompt_memories(reloaded["memories"], current_turn=7)
        self.assertIn("約3ターン前", formatted)
        self.assertNotIn("日目", formatted)


if __name__ == "__main__":
    unittest.main()
