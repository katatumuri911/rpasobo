# -*- coding: utf-8 -*-
"""記憶併合の巻き戻し防止（stale拒否）および出所整合のテスト。

§10.3: M1～M8
- 3分岐（event/promise, item/status, rule/calling/fact等）での stale 拒否
- touch=True / touch=False における stale 挙動
- 同一バッチ内重複における新しい出所優先
- elapsed_time: None の保持
- ピン留め・手動記憶の併合除外
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

import memory_store
import server


class TestMemoryMergeStale(unittest.TestCase):
    """§10.3 併合の stale ガード（M1～M8）"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_merge_stale_")
        self.orig_DATA = memory_store.DATA
        self.orig_RAG = memory_store.RAG_DIR
        self.orig_server_DATA = server.DATA
        self.orig_embed = memory_store.get_embeddings_batch

        memory_store.DATA = os.path.join(self.test_dir, "data")
        memory_store.RAG_DIR = os.path.join(memory_store.DATA, "rag")
        server.DATA = memory_store.DATA
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)
        os.makedirs(server.SESS_DIR, exist_ok=True)

        memory_store._memory_cache.clear()
        server.session_cache.clear()

        # 常に高い類似度 (1.0) を返すダミー埋め込み
        memory_store.get_embeddings_batch = lambda texts: [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    def tearDown(self):
        memory_store.DATA = self.orig_DATA
        memory_store.RAG_DIR = self.orig_RAG
        server.DATA = self.orig_server_DATA
        memory_store.get_embeddings_batch = self.orig_embed
        try:
            shutil.rmtree(self.test_dir)
        except Exception:
            pass

    def _seed_memory(self, sid, cat, content, turn_idx, mid="m0", elapsed_time=None, source="auto", pinned=False, priority=None):
        data = memory_store.load_memories(sid, "ラピ")
        mem = {
            "id": "mem_seed_" + cat,
            "session_id": sid,
            "mid": mid,
            "turn_idx": turn_idx,
            "elapsed_time": copy.deepcopy(elapsed_time),
            "source": source,
            "category": cat,
            "status": "active",
            "content": content,
            "history": [],
            "content_hash": memory_store.compute_hash(content),
            "created_at": time.time(),
            "updated_at": time.time(),
            "tokens": memory_store.extract_tokens(content),
            "embedding": [1.0, 0.0, 0.0, 0.0]
        }
        if pinned:
            mem["priority"] = "pinned"
        elif priority:
            mem["priority"] = priority
        data["memories"].append(mem)
        memory_store.save_memories(sid, data)
        return mem

    def test_m1_touch_true_rule_stale_rejected(self):
        """M1: touch=True, 既存turn=60, 新規turn=3 の類似 rule -> 60側を保持、重複追加なし、historyなし"""
        sid = "sess_m1"
        self._seed_memory(sid, "rule", "夜10時以降は部屋から出ないこと", turn_idx=60)

        new_item = {
            "category": "rule",
            "content": "夜10時以降は部屋から出ない約束",
            "_source_turn_idx": 3,
            "_source_mid": "m_old_3"
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=True)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1, "新規追加へフォールスルーしないこと")
        mem = mems[0]
        self.assertEqual(mem["content"], "夜10時以降は部屋から出ないこと", "古い出所による本文の巻き戻しがないこと")
        self.assertEqual(mem["turn_idx"], 60, "ターン番号が 60 のままであること")
        self.assertEqual(len(mem.get("history", [])), 0, "stale 拒否時は history も増えないこと")

    def test_m1b_touch_true_event_stale_rejected(self):
        """M1b: touch=True, 既存turn=4, 新規turn=3 の類似 event（差1以内） -> 4側を保持、追加なし"""
        sid = "sess_m1b"
        self._seed_memory(sid, "event", "指揮官と一緒に海へ行った", turn_idx=4)

        new_item = {
            "category": "event",
            "content": "指揮官と二人で海へ行ったこと",
            "_source_turn_idx": 3,
            "_source_mid": "m_old_3"
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=True)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        mem = mems[0]
        self.assertEqual(mem["content"], "指揮官と一緒に海へ行った")
        self.assertEqual(mem["turn_idx"], 4)
        self.assertEqual(len(mem.get("history", [])), 0)

    def test_m2_stale_rejected_in_other_branches(self):
        """M2: item, status, calling, char_fact でも同様に stale 拒否されること"""
        categories = ["item", "status", "calling", "char_fact"]
        for cat in categories:
            sid = f"sess_m2_{cat}"
            self._seed_memory(sid, cat, f"{cat}の既存本文", turn_idx=50)
            new_item = {
                "category": cat,
                "content": f"{cat}の類似の古い本文",
                "_source_turn_idx": 2,
            }
            res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=True)
            mems = res.get("memories", [])
            self.assertEqual(len(mems), 1, f"{cat} で新規追加へフォールスルーしないこと")
            self.assertEqual(mems[0]["content"], f"{cat}の既存本文", f"{cat} で本文が巻き戻らないこと")
            self.assertEqual(mems[0]["turn_idx"], 50)
            self.assertEqual(len(mems[0].get("history", [])), 0)

    def test_m3_touch_true_newer_turn_merges_successfully(self):
        """M3: touch=True, 既存turn=3, 新規turn=60 -> 正常に併合され本文・turn_idxが進み history が記録される"""
        sid = "sess_m3"
        self._seed_memory(sid, "rule", "夜10時以降は部屋から出ないこと", turn_idx=3, mid="m3")

        new_item = {
            "category": "rule",
            "content": "夜10時以降は指揮官の許可なく部屋から出てはならない",
            "_source_turn_idx": 60,
            "_source_mid": "m60"
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=True)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        mem = mems[0]
        self.assertEqual(mem["content"], "夜10時以降は指揮官の許可なく部屋から出てはならない")
        self.assertEqual(mem["turn_idx"], 60)
        self.assertEqual(mem["mid"], "m60")
        self.assertEqual(len(mem.get("history", [])), 1)
        self.assertEqual(mem["history"][0]["turn_idx"], 3)

    def test_m4_touch_false_newer_turn_merges_content_keeps_metadata(self):
        """M4: touch=False, 既存turn=3, 新規turn=99 -> 既存の turn_idx/mid/elapsed_time を保持し本文は更新"""
        sid = "sess_m4"
        self._seed_memory(sid, "rule", "古いルール", turn_idx=3, mid="m3",
                          elapsed_time={"day": 1, "period": "朝"})

        new_item = {
            "category": "rule",
            "content": "更新された新ルール",
            "_source_turn_idx": 99,
            "_source_mid": "m99",
            "_source_elapsed_time": {"day": 10, "period": "夜"}
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=False)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        mem = mems[0]
        self.assertEqual(mem["content"], "更新された新ルール")
        self.assertEqual(mem["turn_idx"], 3, "touch=False では turn_idx は上書きされない")
        self.assertEqual(mem["mid"], "m3", "touch=False では mid は上書きされない")
        self.assertEqual(mem["elapsed_time"], {"day": 1, "period": "朝"}, "touch=False では時刻は上書きされない")
        self.assertEqual(len(mem.get("history", [])), 1)

    def test_m4b_touch_false_older_turn_stale_rejected(self):
        """M4b: touch=False, 既存turn=60, 新規turn=3 -> 本文も巻き戻さず history も増やさない（非破壊再生成でもstale拒否）"""
        sid = "sess_m4b"
        self._seed_memory(sid, "rule", "60ターンの新しい本文", turn_idx=60, mid="m60")

        new_item = {
            "category": "rule",
            "content": "3ターンの古い本文",
            "_source_turn_idx": 3,
            "_source_mid": "m3"
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=False)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        mem = mems[0]
        self.assertEqual(mem["content"], "60ターンの新しい本文", "touch=False でも古い出所なら本文は上書きされない")
        self.assertEqual(mem["turn_idx"], 60)
        self.assertEqual(len(mem.get("history", [])), 0)

    def test_m5_existing_turn_none_backfilled(self):
        """M5: 既存turn=None, 新規turn=3 -> 有効値で補完され例外なし"""
        sid = "sess_m5"
        self._seed_memory(sid, "rule", "ルール本文", turn_idx=None)

        new_item = {
            "category": "rule",
            "content": "ルール本文の更新",
            "_source_turn_idx": 3
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=False)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["turn_idx"], 3)

    def test_m6_source_elapsed_time_none_persists(self):
        """M6: 新しい出所時刻が None の場合、既存の時刻を消さず、新規記憶なら None が保持される"""
        sid = "sess_m6"
        # 1. 併合時に既存の有効な時刻を消さない
        self._seed_memory(sid, "rule", "既存ルール", turn_idx=3, elapsed_time={"day": 1, "period": "昼"})
        update_item = {
            "category": "rule",
            "content": "既存ルールの更新",
            "_source_turn_idx": 5,
            "_source_elapsed_time": None
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [update_item], touch=True)
        self.assertEqual(res["memories"][0]["elapsed_time"], {"day": 1, "period": "昼"})

        # 2. 新規記憶（類似なし）で _source_elapsed_time=None が保存され、再読込しても None のまま
        new_sid = "sess_m6_new"
        # 類似判定を避けるため embedding を一時的に直交化
        memory_store.get_embeddings_batch = lambda texts: [[0.0, 1.0, 0.0, 0.0] for _ in texts]
        brand_new_item = {
            "category": "event",
            "content": "全く新しい未知の出来事",
            "_source_turn_idx": 10,
            "_source_elapsed_time": None
        }
        res2 = memory_store.save_extracted_memories(new_sid, "ラピ", [brand_new_item])
        self.assertIsNone(res2["memories"][0]["elapsed_time"])

        reloaded = memory_store.load_memories(new_sid)
        self.assertIsNone(reloaded["memories"][0]["elapsed_time"])

    def test_m7_pinned_and_manual_priority_excluded_from_merge(self):
        """M7: ピン留め／手動優先記憶は類似一致しても併合されず、新規記憶として追加される"""
        sid = "sess_m7"
        self._seed_memory(sid, "rule", "絶対的な規則", turn_idx=5, pinned=True)

        new_item = {
            "category": "rule",
            "content": "絶対的な規則の修正",
            "_source_turn_idx": 10
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=True)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 2, "ピン留め記憶とは併合されず別個に追加されること")

    def test_m8_same_batch_duplicate_prefers_newer_turn(self):
        """M8: 同一バッチ内に同じ本文が古いturn→新しいturnの順で2回含まれる場合、新しいturnが残る"""
        sid = "sess_m8"
        items = [
            {"category": "event", "content": "同文のイベント", "_source_turn_idx": 2, "_source_mid": "m2"},
            {"category": "event", "content": "同文のイベント", "_source_turn_idx": 15, "_source_mid": "m15"},
        ]

        # 1. filter_new_items 経由での縮退
        filtered = memory_store.filter_new_items(sid, items)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["_source_turn_idx"], 15)
        self.assertEqual(filtered[0]["_source_mid"], "m15")

        # 2. save_extracted_memories 直接投入での縮退
        res = memory_store.save_extracted_memories(sid, "ラピ", items)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["turn_idx"], 15)
        self.assertEqual(mems[0]["mid"], "m15")

    def test_m9_same_batch_stale_first_then_newer_merges_without_duplication(self):
        """M9: 同一バッチ内で古いstale項目が先、新しい項目が後に来た場合、stale拒否後に新しい項目が正しく併合され重複が増えない"""
        sid = "sess_m9"
        self._seed_memory(sid, "rule", "夜10時以降は部屋から出ないこと", turn_idx=60)

        # 異なる文面だが類似度が 1.0 になるため併合を試みる2項目
        items = [
            {"category": "rule", "content": "夜10時以降は部屋から出ない約束A", "_source_turn_idx": 3, "_source_mid": "m3"},
            {"category": "rule", "content": "夜10時以降は部屋から出ない約束B", "_source_turn_idx": 70, "_source_mid": "m70"}
        ]
        res = memory_store.save_extracted_memories(sid, "ラピ", items, touch=True)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1, "stale 拒否によって後続の新項目が新規追加にフォールスルーして増えないこと")
        self.assertEqual(mems[0]["content"], "夜10時以降は部屋から出ない約束B")
        self.assertEqual(mems[0]["turn_idx"], 70)
        self.assertEqual(mems[0]["mid"], "m70")

    def test_m10_existing_invalid_turn_corrected_in_touch_false(self):
        """M10: 既存ターンが不正値（文字列等）で touch=False の場合でも有効値で補完される"""
        sid = "sess_m10"
        self._seed_memory(sid, "rule", "既存ルール本文", turn_idx="invalid_string")

        new_item = {
            "category": "rule",
            "content": "ルール本文の更新",
            "_source_turn_idx": 5
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [new_item], touch=False)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["turn_idx"], 5, "不正値が整数の 5 に補正されること")

    def test_m11_filter_new_items_supports_turn_idx_key(self):
        """M11: filter_new_items が _source_turn_idx だけでなく turn_idx キーも考慮して最新を優先する"""
        sid = "sess_m11"
        items = [
            {"category": "rule", "content": "重複ルール", "turn_idx": 2},
            {"category": "rule", "content": "重複ルール", "turn_idx": 8},
        ]
        filtered = memory_store.filter_new_items(sid, items)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["turn_idx"], 8)

    def test_m12_event_recent_turn_comparison_type_robustness(self):
        """M12: event カテゴリで既存記憶の turn_idx が文字列型（'5' や 'corrupted'）でも例外なく比較・処理される"""
        sid = "sess_m12"
        # 1. 既存記憶の turn_idx が数値文字列 "5"
        self._seed_memory(sid, "event", "海辺で貝殻を拾った", turn_idx="5")
        item = {
            "category": "event",
            "content": "海辺で綺麗な貝殻を拾った",
            "_source_turn_idx": 6
        }
        res = memory_store.save_extracted_memories(sid, "ラピ", [item], touch=True)
        mems = res.get("memories", [])
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["turn_idx"], 6)

        # 2. 既存記憶の turn_idx が破損文字列 "corrupted" -> TypeError で例外にならず安全に処理される
        sid2 = "sess_m12_corrupt"
        self._seed_memory(sid2, "event", "古い出来事", turn_idx="corrupted")
        item2 = {
            "category": "event",
            "content": "新しい出来事",
            "_source_turn_idx": 10
        }
        res2 = memory_store.save_extracted_memories(sid2, "ラピ", [item2], touch=True)
        self.assertEqual(len(res2.get("memories", [])), 2)


if __name__ == "__main__":
    unittest.main()
