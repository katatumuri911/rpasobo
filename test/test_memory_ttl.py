# -*- coding: utf-8 -*-
"""フェーズ③ 一時記憶の寿命（TTL）と装着物の解除検出のテスト"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import memory_store as ms


class _TempStore(unittest.TestCase):
    """data/rag を一時フォルダへ差し替える共通の足場"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_ttl_")
        self.orig = (ms.DATA, ms.RAG_DIR)      # RAG_DIR は import 時に確定しているので両方差し替える
        ms.DATA = self.tmp
        ms.RAG_DIR = os.path.join(self.tmp, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()

    def tearDown(self):
        ms.DATA, ms.RAG_DIR = self.orig
        ms._memory_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, cat, turn, text, source="auto"):
        if source == "manual":
            mem = ms.add_manual_memory("s1", "ラピ", cat, text)
            data = ms.load_memories("s1")
            for m in data.get("memories", []):
                if m.get("id") == mem["id"]:
                    m["turn_idx"] = turn
            ms.save_memories("s1", data)
            return
        ms.save_extracted_memories("s1", "ラピ", [{"category": cat, "content": text}],
                                   turn_idx=turn, mid=f"m{turn}", max_new=5)


class TestTransientTTL(_TempStore):
    def test_old_status_dropped(self):
        self._seed("status", 1, "ラピの首筋にキスマークが付いている")
        self._seed("rule", 1, "ラピは夜10時以降に部屋から出てはならない")
        hits = ms.search_memories("s1", "キスマークはまだある？", current_turn=100)
        self.assertFalse([h for h in hits if h["category"] == "status"])

    def test_fresh_status_kept(self):
        self._seed("status", 95, "ラピの首筋にキスマークが付いている")
        hits = ms.search_memories("s1", "キスマークはまだある？", current_turn=100)
        self.assertTrue([h for h in hits if h["category"] == "status"])

    def test_rule_never_expires(self):
        self._seed("rule", 1, "ラピは夜10時以降に部屋から出てはならない")
        hits = ms.search_memories("s1", "夜10時のルールは？", current_turn=500)
        self.assertTrue([h for h in hits if h["category"] == "rule"])

    def test_expired_status_is_not_deleted(self):
        """TTL は検索対象から外すだけで、データは消さない（後から見返せる）"""
        self._seed("status", 1, "ラピの首筋にキスマークが付いている")
        ms.search_memories("s1", "キスマークはまだある？", current_turn=100)
        data = ms.load_memories("s1")
        self.assertEqual(len([m for m in data["memories"] if m["category"] == "status"]), 1)

    def test_item_ttl_is_longer_than_status(self):
        """item は 60 ターンまで残る（status の 20 ターンより長い）"""
        self._seed("item", 1, "ラピはアナルプラグを装着している")
        self.assertTrue([h for h in ms.search_memories("s1", "プラグは？", current_turn=50)
                         if h["category"] == "item"])
        self.assertFalse([h for h in ms.search_memories("s1", "プラグは？", current_turn=100)
                          if h["category"] == "item"])

    def test_manual_status_never_expires(self):
        """手動で書いた記憶はユーザーの意思なので TTL で落とさない"""
        self._seed("status", 1, "ラピの首筋にキスマークが付いている", source="manual")
        hits = ms.search_memories("s1", "キスマークはまだある？", current_turn=100)
        self.assertTrue([h for h in hits if h["category"] == "status"])


class TestResolveItems(_TempStore):
    def _items(self):
        return [m for m in ms.load_memories("s1")["memories"] if m["category"] == "item"]

    def test_removed_equipped_item_is_fulfilled(self):
        self._seed("item", 3, "ラピはアナルプラグを装着している")
        n = ms.resolve_items("s1", ["アナルプラグ"], turn_idx=5, mid="m5", character_name="ラピ")
        self.assertEqual(n, 1)
        mem = self._items()[0]
        self.assertEqual(mem["status"], "fulfilled")
        self.assertEqual(mem["resolved_turn"], 5)
        self.assertEqual(mem["resolved_mid"], "m5")   # 巻き戻しで active に戻す鍵

    def test_gift_is_not_resolved(self):
        """「プレゼントされた」等の贈与文は装着解除の対象にしない"""
        self._seed("item", 3, "ラピは指揮官から首輪をプレゼントされた")
        self.assertEqual(ms.resolve_items("s1", ["首輪"], turn_idx=5, mid="m5"), 0)
        self.assertEqual(self._items()[0]["status"], "active")

    def test_unrelated_item_is_untouched(self):
        self._seed("item", 3, "ラピは指輪を装着している")
        self.assertEqual(ms.resolve_items("s1", ["アナルプラグ"], turn_idx=5, mid="m5"), 0)
        self.assertEqual(self._items()[0]["status"], "active")

    def test_manual_item_is_untouched(self):
        self._seed("item", 3, "ラピはアナルプラグを装着している", source="manual")
        self.assertEqual(ms.resolve_items("s1", ["アナルプラグ"], turn_idx=5, mid="m5"), 0)
        self.assertEqual(self._items()[0]["status"], "active")

    def test_rollback_reverts_to_active(self):
        """解決を書いたターンを消したら active に戻る（delete_by_mid の裏取り）"""
        self._seed("item", 3, "ラピはアナルプラグを装着している")
        ms.resolve_items("s1", ["アナルプラグ"], turn_idx=5, mid="m5")
        ms.delete_by_mid("s1", "m5")
        self.assertEqual(self._items()[0]["status"], "active")

    def test_empty_names_is_noop(self):
        self._seed("item", 3, "ラピはアナルプラグを装着している")
        self.assertEqual(ms.resolve_items("s1", [], turn_idx=5, mid="m5"), 0)
        self.assertEqual(ms.resolve_items("s1", None, turn_idx=5, mid="m5"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
