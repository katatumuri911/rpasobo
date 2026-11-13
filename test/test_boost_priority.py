# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.request
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
import memory_store as ms


class TestBoostPriorityUnit(unittest.TestCase):
    def test_resolve_priority_key_defaults(self):
        self.assertEqual("default", ms.resolve_priority_key())
        self.assertEqual("default", ms.resolve_priority_key(None, None))
        self.assertEqual("default", ms.resolve_priority_key("", ""))
        self.assertEqual("default", ms.resolve_priority_key("   "))

    def test_resolve_priority_key_direct_and_aliases(self):
        # pinned
        for k in ("pinned", "pin", "固定", "ピン留め", "ピン", "常時"):
            self.assertEqual("pinned", ms.resolve_priority_key(k))
            self.assertEqual("pinned", ms.resolve_priority_key(k.upper()))
        # highest
        for k in ("highest", "very_high", "top", "最優先", "超高", "+2", "2"):
            self.assertEqual("highest", ms.resolve_priority_key(k))
        # high
        for k in ("high", "slightly_high", "優先", "少し優先", "やや優先", "高", "少し高", "+1", "1"):
            self.assertEqual("high", ms.resolve_priority_key(k))
        # default
        for k in ("default", "normal", "標準", "デフォルト", "普通", "0", ""):
            self.assertEqual("default", ms.resolve_priority_key(k))
        # low
        for k in ("low", "slightly_low", "lowest", "mute", "抑制", "低", "少し低", "超低", "-1", "-2"):
            self.assertEqual("low", ms.resolve_priority_key(k))

    def test_resolve_priority_key_boost_floats(self):
        self.assertEqual("pinned", ms.resolve_priority_key(boost=0.10))
        self.assertEqual("pinned", ms.resolve_priority_key(boost=0.08))
        self.assertEqual("highest", ms.resolve_priority_key(boost=0.05))
        self.assertEqual("highest", ms.resolve_priority_key(boost=0.03))
        self.assertEqual("high", ms.resolve_priority_key(boost=0.02))
        self.assertEqual("high", ms.resolve_priority_key(boost=1e-5))
        self.assertEqual("default", ms.resolve_priority_key(boost=0.0))
        self.assertEqual("low", ms.resolve_priority_key(boost=-0.05))
        self.assertEqual("low", ms.resolve_priority_key(boost=-1e-5))

    def test_resolve_priority_key_numeric_string_priority(self):
        self.assertEqual("pinned", ms.resolve_priority_key(priority="0.10"))
        self.assertEqual("highest", ms.resolve_priority_key(priority="0.05"))
        self.assertEqual("low", ms.resolve_priority_key(priority="-0.05"))

    def test_resolve_priority_key_invalid(self):
        with self.assertRaises(ValueError):
            ms.resolve_priority_key(priority="invalid_key_xyz")
        with self.assertRaises(ValueError):
            ms.resolve_priority_key(boost="not_a_number")

    def test_apply_priority_field_and_memory_priority_key(self):
        mem = {"content": "test", "boost": 0.05}
        ms.apply_priority_field(mem, priority="highest")
        self.assertEqual("highest", mem.get("priority"))
        self.assertNotIn("boost", mem)
        self.assertEqual("highest", ms.memory_priority_key(mem))

        # default deletes priority field
        ms.apply_priority_field(mem, priority="default")
        self.assertNotIn("priority", mem)
        self.assertEqual("default", ms.memory_priority_key(mem))

        # Backward compatibility with legacy boost in dict
        legacy = {"content": "legacy", "boost": 0.08}
        self.assertEqual("pinned", ms.memory_priority_key(legacy))

    def test_memory_priority_info_and_boost(self):
        p_info = ms.memory_priority_info({"priority": "highest"})
        self.assertEqual(1.25, p_info["mult"])
        self.assertEqual(ms.TIER2_MIN_SCORE, p_info["min_score"])
        self.assertFalse(p_info["pinned"])

        p_low = ms.memory_priority_info({"priority": "low"})
        self.assertEqual(0.82, p_low["mult"])
        self.assertEqual(0.60, p_low["min_score"])

        self.assertEqual(0.10, ms.memory_boost({"priority": "pinned"}))
        self.assertEqual(0.05, ms.memory_boost({"priority": "highest"}))
        self.assertEqual(0.02, ms.memory_boost({"priority": "high"}))
        self.assertEqual(0.00, ms.memory_boost({"priority": "default"}))
        self.assertEqual(-0.05, ms.memory_boost({"priority": "low"}))

    def test_is_pinned_memory(self):
        self.assertTrue(ms.is_pinned_memory({"priority": "pinned", "status": "active"}))
        self.assertFalse(ms.is_pinned_memory({"priority": "pinned", "status": "fulfilled"}))
        self.assertFalse(ms.is_pinned_memory({"priority": "highest", "status": "active"}))
        self.assertFalse(ms.is_pinned_memory("not_a_dict"))


class TestBoostPriorityIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_boost_prio_")
        self.orig_data = ms.DATA
        self.orig_rag = ms.RAG_DIR
        ms.DATA = self.test_dir
        ms.RAG_DIR = os.path.join(self.test_dir, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()

    def tearDown(self):
        ms.DATA = self.orig_data
        ms.RAG_DIR = self.orig_rag
        ms._memory_cache.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_memories_normalizes_priority_and_cleans_boost(self):
        raw_data = {
            "session_id": "sid_norm",
            "memories": [
                {"id": "m1", "category": "rule", "content": "ルールA", "boost": 0.08},
                {"id": "m2", "category": "item", "content": "アイテムB", "priority": "最優先"},
                {"id": "m3", "category": "status", "content": "状態C", "priority": "default", "boost": 0.0},
            ]
        }
        ms.save_memories("sid_norm", raw_data)
        ms._memory_cache.clear()
        loaded = ms.load_memories("sid_norm")
        mems = {m["id"]: m for m in loaded["memories"]}
        self.assertEqual("pinned", mems["m1"].get("priority"))
        self.assertNotIn("boost", mems["m1"])
        self.assertEqual("highest", mems["m2"].get("priority"))
        self.assertNotIn("priority", mems["m3"])
        self.assertNotIn("boost", mems["m3"])

    def test_minimum_relevance_guard(self):
        """s_kw == 0 and s_vec < 0.35 guards positive multiplier from false triggering"""
        # Add a memory with highest priority
        ms.add_manual_memory("s_guard", "ラピ", "event", "まったく無関係な記憶の内容", priority="highest")
        # Search with completely unrelated query where vector similarity is low and no keyword match
        hits = ms.search_memories("s_guard", "量子力学と一般相対性理論の統合", current_turn=10)
        # s_vec < 0.35 and s_kw == 0 -> effective_mult is 1.0, raw_score < 0.53, so it does not appear in Tier 2
        self.assertEqual(0, len(hits))

    def test_pinned_memory_forced_into_tier1(self):
        """Pinned memories are forced into Tier 1 regardless of score"""
        ms.add_manual_memory("s_pin", "ラピ", "event", "量子力学とは無関係なピン留め記憶", priority="pinned")
        hits = ms.search_memories("s_pin", "量子力学と一般相対性理論の統合", current_turn=10)
        self.assertEqual(1, len(hits))
        self.assertEqual(1, hits[0].get("_tier"))
        self.assertEqual("pinned", hits[0].get("priority"))

    def test_pinned_overflow_into_tier2_head(self):
        """5+ pinned memories overflow into Tier 2 without cutoff or group cap"""
        for i in range(6):
            ms.add_manual_memory("s_pin_over", "ラピ", "event", f"ピン留め記憶番号_{i}", priority="pinned")
        hits = ms.search_memories("s_pin_over", "無関係クエリ", current_turn=10)
        # Tier 1 can hold at most RULE_TIER1_CAP (5), so 5 in Tier 1 and 1 in Tier 2
        tier1 = [h for h in hits if h.get("_tier") == 1]
        tier2 = [h for h in hits if h.get("_tier") == 2]
        self.assertEqual(5, len(tier1))
        self.assertEqual(1, len(tier2))
        self.assertTrue(all(h.get("priority") == "pinned" for h in hits))

    def test_prioritized_exempt_from_ttl_and_2turn_auto(self):
        """pinned/highest/high are exempt from TTL and 2-turn auto exclusion"""
        data = {
            "session_id": "s_ttl",
            "memories": [
                {"id": "m_ttl_old", "source": "auto", "category": "status", "content": "古い痕跡", "turn_idx": 1, "priority": "high"},
                {"id": "m_ttl_norm", "source": "auto", "category": "status", "content": "普通の古い痕跡", "turn_idx": 1},
                {"id": "m_recent_prio", "source": "auto", "category": "event", "content": "直近の重要事態", "turn_idx": 9, "priority": "highest"},
                {"id": "m_recent_norm", "source": "auto", "category": "event", "content": "直近の普通事態", "turn_idx": 9},
            ]
        }
        ms.save_memories("s_ttl", data)
        ms._memory_cache.clear()
        hits = ms.search_memories("s_ttl", "古い痕跡", current_turn=50)
        hit_ids = {h["id"] for h in hits}
        self.assertIn("m_ttl_old", hit_ids)
        self.assertNotIn("m_ttl_norm", hit_ids)

    def test_delete_after_turn_and_delete_by_mid_pinned_protection(self):
        """Pinned memories are preserved during rollback deletion"""
        data = {
            "session_id": "s_roll",
            "memories": [
                {"id": "m_auto_pin", "source": "auto", "category": "rule", "content": "ピン留めルール", "turn_idx": 5, "mid": "m5", "priority": "pinned"},
                {"id": "m_auto_norm", "source": "auto", "category": "rule", "content": "通常ルール", "turn_idx": 5, "mid": "m5"},
            ]
        }
        ms.save_memories("s_roll", data)
        ms.delete_after_turn("s_roll", 3)
        loaded = ms.load_memories("s_roll")
        ids = [m["id"] for m in loaded["memories"]]
        self.assertIn("m_auto_pin", ids)
        self.assertNotIn("m_auto_norm", ids)

    def test_history_restores_priority(self):
        """Rollback restores priority from history when current priority is empty"""
        now = time.time()
        m = {
            "id": "m_hist",
            "source": "auto",
            "category": "rule",
            "content": "改変後ルール",
            "turn_idx": 5,
            "mid": "m5",
            "history": [
                {
                    "content": "旧ルール本文",
                    "turn_idx": 2,
                    "mid": "m2",
                    "priority": "high",
                    "archived_at": now - 100
                }
            ]
        }
        ms.save_memories("s_hist", {"session_id": "s_hist", "memories": [m]})
        ms.delete_after_turn("s_hist", 3)
        loaded = ms.load_memories("s_hist")
        res = loaded["memories"][0]
        self.assertEqual("旧ルール本文", res["content"])
        self.assertEqual("high", res.get("priority"))

    def test_clear_auto_memories_preserves_prioritized(self):
        """clear_auto_memories preserves memories with custom priority"""
        data = {
            "session_id": "s_clear",
            "memories": [
                {"id": "m_norm", "source": "auto", "category": "event", "content": "普通記憶"},
                {"id": "m_prio", "source": "auto", "category": "event", "content": "抑制記憶", "priority": "low"},
                {"id": "m_pin", "source": "auto", "category": "event", "content": "ピン留め記憶", "priority": "pinned"},
            ]
        }
        ms.save_memories("s_clear", data)
        ms.clear_auto_memories("s_clear")
        loaded = ms.load_memories("s_clear")
        ids = [x["id"] for x in loaded["memories"]]
        self.assertNotIn("m_norm", ids)
        self.assertIn("m_prio", ids)
        self.assertIn("m_pin", ids)

    def test_format_prompt_memories_pinned_and_trim_protection(self):
        """format_prompt_memories protects pinned rules from soft trimming"""
        mems = [
            {"id": "p1", "category": "rule", "content": "ピン留めされた絶対遵守ルール", "priority": "pinned"},
            {"id": "r1", "category": "rule", "content": "通常の常識ルール", "priority": "default"},
            {"id": "rec1", "category": "event", "content": "想起イベントA", "score": 0.8},
            {"id": "rec2", "category": "event", "content": "想起イベントB", "score": 0.7},
        ]
        stats = {}
        # Soft budget tiny enough to trigger aggressive trimming
        text = ms.format_prompt_memories(mems, current_turn=1, soft_budget=50, stats=stats)
        self.assertIn("ピン留めされた絶対遵守ルール", text)
        self.assertIn("p1", stats["included_ids"])


class TestBoostPriorityServerAPI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_boost_api_")
        self.orig_data = server.DATA
        self.orig_sess = server.SESS_DIR
        self.orig_ms_data = ms.DATA
        self.orig_ms_rag = ms.RAG_DIR

        server.DATA = ms.DATA = os.path.join(self.test_dir, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        ms.RAG_DIR = os.path.join(server.DATA, "rag")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        os.makedirs(ms.RAG_DIR, exist_ok=True)

        server.session_cache.clear()
        ms._memory_cache.clear()

        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.orig_data
        server.SESS_DIR = self.orig_sess
        ms.DATA = self.orig_ms_data
        ms.RAG_DIR = self.orig_ms_rag
        server.session_cache.clear()
        ms._memory_cache.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def req(self, method, path, payload=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(r, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_api_crud_priority(self):
        sid = "sess_prio_api"
        # 1. GET /api/rag/memories returns priority_levels
        res = self.req("GET", f"/api/rag/memories?session_id={sid}")
        self.assertTrue(res["ok"])
        self.assertIn("priority_levels", res)
        p_keys = [p["key"] for p in res["priority_levels"]]
        self.assertEqual(["pinned", "highest", "high", "default", "low"], p_keys)

        # 2. POST /api/rag/memories with priority
        create_res = self.req("POST", "/api/rag/memories", {
            "session_id": sid,
            "category": "rule",
            "content": "APIから追加した最優先ルール",
            "priority": "highest"
        })
        self.assertTrue(create_res["ok"])
        mem = create_res["memory"]
        self.assertEqual("highest", mem.get("priority"))
        mem_id = mem["id"]

        # 3. PUT /api/rag/memories/<mem_id> updates priority to pinned
        put_res = self.req("PUT", f"/api/rag/memories/{mem_id}", {
            "session_id": sid,
            "priority": "pinned"
        })
        self.assertTrue(put_res["ok"])
        self.assertEqual("pinned", put_res["memory"].get("priority"))

        # Verify load retains pinned
        get_after = self.req("GET", f"/api/rag/memories?session_id={sid}")
        m_after = next(m for m in get_after["memories"] if m["id"] == mem_id)
        self.assertEqual("pinned", m_after.get("priority"))


if __name__ == "__main__":
    unittest.main()
