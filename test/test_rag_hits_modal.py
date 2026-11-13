"""test/test_rag_hits_modal.py - 索引記憶ランキング表示機能（バックエンド）のテスト
"""
import os
import shutil
import sys
import tempfile
import unittest
import json
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server
import memory_store


class FakeGetHandler:
    def __init__(self, p):
        self.path = p
        self.result = None
        self.status = 200

    def blocked(self, u, post=False):
        return False

    def send_json(self, data, code=200):
        self.result = data
        self.status = code
        return data

    def send_error(self, code, msg=None):
        self.status = code
        self.result = {"error": msg or str(code)}


class TestRagHitsModalBackend(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_ctx = server.CTX
        self.orig_rag_dir = memory_store.RAG_DIR
        server.CTX = os.path.join(self.test_dir, "ctx")
        memory_store.RAG_DIR = os.path.join(self.test_dir, "rag")
        os.makedirs(server.CTX, exist_ok=True)
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)
        self.sid = "test-session-rag-hits"

    def tearDown(self):
        server.CTX = self.orig_ctx
        memory_store.RAG_DIR = self.orig_rag_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _call_get(self, path):
        h = FakeGetHandler(path)
        server.H.do_GET(h)
        return h.status, h.result

    def test_resolve_rag_hits_for_modal_invalid_sid(self):
        """不正な sid の場合は None を返す"""
        self.assertIsNone(server.resolve_rag_hits_for_modal(""))
        self.assertIsNone(server.resolve_rag_hits_for_modal("bad sid/with/slash"))
        self.assertIsNone(server.resolve_rag_hits_for_modal(12345))

    def test_resolve_rag_hits_for_modal_empty_file(self):
        """hits ファイルが存在しないか空の場合は None を返す"""
        self.assertIsNone(server.resolve_rag_hits_for_modal(self.sid, "msg1"))

    def test_resolve_rag_hits_for_modal_direct_hit(self):
        """mid が直接ヒットする場合"""
        data = {
            "u1": {"ids": ["m1", "m2"], "scores": {"m1": 0.9, "m2": 0.5}, "tier1": 1, "tier2": 1, "ts": "2026-01-01T00:00:00"}
        }
        server.write_json(server.rag_hits_path(self.sid), data)
        hit = server.resolve_rag_hits_for_modal(self.sid, "u1")
        self.assertEqual(hit, data["u1"])

    def test_resolve_rag_hits_for_modal_assistant_mid_resolves_to_preceding_user(self):
        """AI応答の mid が渡された場合、直前のユーザー発言 umid から rag_hits を引く"""
        data = {
            "u_msg_10": {"ids": ["mem_rule_1"], "scores": {"mem_rule_1": 0.88}, "tier1": 1, "tier2": 0, "ts": "2026-01-01T01:00:00"}
        }
        server.write_json(server.rag_hits_path(self.sid), data)

        session_data = {
            "id": self.sid,
            "messages": [
                {"id": "u_msg_10", "role": "user", "text": "こんにちは"},
                {"id": "a_msg_11", "role": "assistant", "text": "はい、こんにちは"},
            ]
        }
        with patch("server.load", return_value=session_data):
            hit = server.resolve_rag_hits_for_modal(self.sid, "a_msg_11")
            self.assertIsNotNone(hit)
            self.assertEqual(hit["ids"], ["mem_rule_1"])

    def test_resolve_rag_hits_for_modal_fallback_latest(self):
        """mid が見つからない・空の場合は最新 (ts が最大) のエントリを返す"""
        data = {
            "u1": {"ids": ["m1"], "scores": {"m1": 0.3}, "ts": "2026-01-01T01:00:00"},
            "u2": {"ids": ["m2"], "scores": {"m2": 0.9}, "ts": "2026-01-01T02:00:00"},
            "u0": {"ids": ["m0"], "scores": {"m0": 0.1}, "ts": "2026-01-01T00:30:00"},
        }
        server.write_json(server.rag_hits_path(self.sid), data)
        hit = server.resolve_rag_hits_for_modal(self.sid, "")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["ids"], ["m2"])

    def test_api_rag_hits_endpoint(self):
        """GET /api/rag/hits エンドポイントのテスト"""
        # 記憶ストアに記憶を作成
        memories = [
            {
                "id": "mem_call_1",
                "category": "calling",
                "status": "active",
                "elapsed_time": {"day": 2, "period": "昼"},
                "content": "お兄ちゃんと呼ぶこと",
            },
            {
                "id": "mem_rule_1",
                "category": "rule",
                "status": "active",
                "elapsed_time": {"day": 1, "period": "朝"},
                "content": "部屋に入るときはノックする",
            },
            {
                "id": "mem_ev_1",
                "category": "event",
                "status": "active",
                "elapsed_time": None,
                "content": "一緒にアイスを食べた",
            },
            {
                "id": "mem_ev_2",
                "category": "event",
                "status": "fulfilled",
                "elapsed_time": "3日目・夜",
                "content": "宿題を手伝ってもらった",
            }
        ]
        mem_file = memory_store.memory_path(self.sid)
        server.write_json(mem_file, {"session_id": self.sid, "memories": memories})

        # rag_hits を作成: tier1 = 2件 (mem_call_1, mem_rule_1), tier2 = 3件 (mem_ev_1, mem_ev_deleted, mem_ev_2)
        # mem_ev_deleted は memories.json に存在しない（削除済み）ID
        hits_data = {
            "u100": {
                "ids": ["mem_call_1", "mem_rule_1", "mem_ev_1", "mem_ev_deleted", "mem_ev_2"],
                "scores": {
                    "mem_call_1": 0.95231,
                    "mem_rule_1": 0.72149,
                    "mem_ev_1": 0.41238,
                    "mem_ev_deleted": 0.88123,
                    # mem_ev_2 はスコア未記録（None）
                },
                "tier1": 2,
                "tier2": 3,
                "ts": "2026-01-01T12:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)

        # 1. 無効な sid
        status, res = self._call_get("/api/rag/hits?sid=bad%20sid!&mid=u100")
        self.assertEqual(status, 400)
        self.assertIn("error", res)

        # 2. 正常リクエスト
        status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=u100")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("tier1_count"), 2)
        self.assertEqual(res.get("tier2_count"), 3)

        t1 = res.get("tier1")
        t2 = res.get("tier2")
        self.assertEqual(len(t1), 2)
        self.assertEqual(len(t2), 3)

        # Tier 1: スコア降順（0.9523 > 0.7215）
        self.assertEqual(t1[0]["id"], "mem_call_1")
        self.assertEqual(t1[0]["score"], 0.9523)
        self.assertEqual(t1[0]["category"], "calling")
        self.assertEqual(t1[0]["elapsed_time"], {"day": 2, "period": "昼"})
        self.assertFalse(t1[0]["missing"])

        self.assertEqual(t1[1]["id"], "mem_rule_1")
        self.assertEqual(t1[1]["score"], 0.7215)
        self.assertEqual(t1[1]["category"], "rule")
        self.assertFalse(t1[1]["missing"])

        # Tier 2: mem_ev_deleted (0.8812) > mem_ev_1 (0.4124) > mem_ev_2 (None / 0.0)
        self.assertEqual(t2[0]["id"], "mem_ev_deleted")
        self.assertEqual(t2[0]["score"], 0.8812)
        self.assertTrue(t2[0]["missing"])
        self.assertEqual(t2[0]["content"], "（参照不可: mem_ev_deleted）")

        self.assertEqual(t2[1]["id"], "mem_ev_1")
        self.assertEqual(t2[1]["score"], 0.4124)
        self.assertFalse(t2[1]["missing"])

        self.assertEqual(t2[2]["id"], "mem_ev_2")
        self.assertIsNone(t2[2]["score"])
        self.assertEqual(t2[2]["elapsed_time"], "3日目・夜")
        self.assertEqual(t2[2]["status"], "fulfilled")
        self.assertFalse(t2[2]["missing"])

    def test_api_rag_hits_edge_cases(self):
        """空データ、破損値、クランプ、無効スコア等のエッジケーステスト"""
        # 1. hits ファイルはあるが ids が空
        hits_data = {
            "u_empty": {
                "ids": [],
                "scores": {},
                "tier1": 0,
                "tier2": 0,
                "ts": "2026-01-01T00:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)
        status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=u_empty")
        self.assertEqual(status, 200)
        self.assertEqual(res["tier1_count"], 0)
        self.assertEqual(res["tier2_count"], 0)
        self.assertEqual(res["tier1"], [])
        self.assertEqual(res["tier2"], [])

        # 2. tier1 が ids 長を超えている場合のクランプ、および scores に不正な値が入っている場合
        hits_data2 = {
            "u_clamp": {
                "ids": ["mem_corrupt", "mem_none"],
                "scores": {"mem_corrupt": "not_a_number", "mem_none": None},
                "tier1": "not_an_int",
                "tier2": "invalid",
                "ts": "2026-01-01T01:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data2)
        status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=u_clamp")
        self.assertEqual(status, 200)
        self.assertEqual(res["tier1_count"], 0)
        self.assertEqual(res["tier2_count"], 2)
        self.assertEqual(len(res["tier2"]), 2)
        self.assertIsNone(res["tier2"][0]["score"])
        self.assertIsNone(res["tier2"][1]["score"])

    def test_api_rag_hits_negative_and_int_key_scores(self):
        """負のスコアの降順ソート順序 (正 > 0 > 負 > None) および数値キー耐性のテスト"""
        hits_data = {
            "u_neg": {
                "ids": ["101", "102", "103", "104"],
                "scores": {
                    101: -0.25,     # 数値キー & 負数
                    "102": 0.85,    # 正数
                    "103": 0.0,     # ゼロ
                    # 104 は None
                },
                "tier1": 4,
                "tier2": 0,
                "ts": "2026-01-01T02:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)
        status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=u_neg")
        self.assertEqual(status, 200)
        t1 = res["tier1"]
        self.assertEqual([x["id"] for x in t1], ["102", "103", "101", "104"])
        self.assertEqual(t1[0]["score"], 0.85)
        self.assertEqual(t1[1]["score"], 0.0)
        self.assertEqual(t1[2]["score"], -0.25)
        self.assertIsNone(t1[3]["score"])

    def test_build_context_scores_populated(self):
        """build_context で matched_memories があるとき scores が rag_hits に記録される"""
        mock_mem = [
            {"id": "m1", "score": 0.8912, "_tier": 1, "category": "calling", "content": "テスト1"},
            {"id": "m2", "score": 0.6543, "_tier": 2, "category": "event", "content": "テスト2"}
        ]
        s = {
            "id": self.sid,
            "setting": {"name": "Bot"},
            "messages": [
                {"id": "u1", "role": "user", "text": "hello"}
            ]
        }
        with patch("memory_store.search_memories", return_value=mock_mem), \
             patch("server.build_expanded_rag_query", return_value="hello query"), \
             patch("memory_store.format_prompt_memories", return_value="formatted memories"):
            ctx = server.build_context(s, s["messages"][0])
            retrieved = ctx.get("retrieved")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.get("count"), 2)
            self.assertEqual(retrieved.get("tier1"), 1)
            self.assertEqual(retrieved.get("tier2"), 1)
            self.assertEqual(retrieved.get("ids"), ["m1", "m2"])
            self.assertEqual(retrieved.get("scores"), {"m1": 0.8912, "m2": 0.6543})

    def test_api_rag_hits_fallback_scores_from_query(self):
        """rag_hits に scores が保存されていない過去ログでも、query から動的にスコアを補填して返す"""
        memories = [
            {"id": "m_call", "category": "calling", "status": "active", "content": "お兄ちゃんと呼ぶこと"},
            {"id": "m_rule", "category": "rule", "status": "active", "content": "部屋に入るときはノックする"},
        ]
        server.write_json(memory_store.memory_path(self.sid), {"session_id": self.sid, "memories": memories})

        # scores が存在しない過去の hits データ
        hits_data = {
            "u_legacy": {
                "ids": ["m_call", "m_rule"],
                "query": "お兄ちゃんノックして入って",
                "tier1": 1,
                "tier2": 1,
                "ts": "2026-01-01T05:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)

        status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=u_legacy")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        t1 = res.get("tier1")
        t2 = res.get("tier2")
        self.assertEqual(len(t1), 1)
        self.assertEqual(len(t2), 1)
        self.assertIsNotNone(t1[0]["score"])
        self.assertIsInstance(t1[0]["score"], float)
        self.assertGreater(t1[0]["score"], 0.0)
        self.assertIsNotNone(t2[0]["score"])
        self.assertIsInstance(t2[0]["score"], float)
        self.assertGreater(t2[0]["score"], 0.0)

        # ディスク上の rag_hits にも scores が永続化されたことを検証
        persisted = server.read_json(server.rag_hits_path(self.sid), {}).get("u_legacy")
        self.assertIsNotNone(persisted.get("scores"))
        self.assertIn("m_call", persisted["scores"])
        self.assertIn("m_rule", persisted["scores"])

    def test_api_rag_hits_fallback_scores_from_session_message(self):
        """scores も query も存在しない場合、セッションのメッセージ本文からスコアを補填する"""
        memories = [
            {"id": "m_test", "category": "rule", "status": "active", "content": "夜更かし禁止"},
        ]
        server.write_json(memory_store.memory_path(self.sid), {"session_id": self.sid, "memories": memories})

        hits_data = {
            "u_no_query": {
                "ids": ["m_test"],
                "tier1": 1,
                "tier2": 0,
                "ts": "2026-01-01T06:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)

        session_data = {
            "id": self.sid,
            "messages": [
                {"id": "u_no_query", "role": "user", "text": "夜更かししちゃだめ？"},
                {"id": "a_reply", "role": "assistant", "text": "だめですよ"},
            ]
        }
        with patch("server.load", return_value=session_data):
            status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=a_reply")
            self.assertEqual(status, 200)
            t1 = res.get("tier1")
            self.assertEqual(len(t1), 1)
            self.assertIsNotNone(t1[0]["score"])
            self.assertIsInstance(t1[0]["score"], float)
            self.assertGreater(t1[0]["score"], 0.0)

    def test_backfill_rag_hits_scores(self):
        """backfill_rag_hits_scores が scores の欠落した全エントリを一括補填する"""
        memories = [
            {"id": "m_bf1", "category": "event", "status": "active", "content": "川で魚釣りをした"},
            {"id": "m_bf2", "category": "calling", "status": "active", "content": "先輩と呼ぶこと"},
        ]
        server.write_json(memory_store.memory_path(self.sid), {"session_id": self.sid, "memories": memories})

        hits_data = {
            "u_bf1": {
                "ids": ["m_bf1", "m_bf2"],
                "query": "川で先輩と魚を釣る",
                "tier1": 1,
                "tier2": 1,
                "ts": "2026-01-01T07:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)

        server.backfill_rag_hits_scores(self.sid)

        updated = server.read_json(server.rag_hits_path(self.sid), {}).get("u_bf1")
        self.assertIsNotNone(updated.get("scores"))
        self.assertIn("m_bf1", updated["scores"])
        self.assertIn("m_bf2", updated["scores"])
        self.assertIsInstance(updated["scores"]["m_bf1"], float)

    def test_compute_scores_for_memories(self):
        """memory_store.compute_scores_for_memories が各記憶のスコアを正しく返す"""
        memories = [
            {"id": "m_score_1", "category": "rule", "content": "走らないこと", "tokens": ["走る", "ない"]},
            {"id": "m_score_2", "category": "calling", "content": "先生と呼ぶこと", "tokens": ["先生", "呼ぶ"]},
        ]
        res = memory_store.compute_scores_for_memories(memories, "先生、廊下を走らないで")
        self.assertIn("m_score_1", res)
        self.assertIn("m_score_2", res)
        self.assertIsInstance(res["m_score_1"], float)
        self.assertIsInstance(res["m_score_2"], float)
        self.assertGreater(res["m_score_1"], 0.0)
        self.assertGreater(res["m_score_2"], 0.0)

    def test_compute_scores_for_memories_empty_query(self):
        """クエリが空または記憶リストが空の場合、クエリ関連度を算出できないため空辞書を安全に返す"""
        memories = [
            {"id": "m_empty_1", "category": "rule", "content": "走らないこと"},
            {"id": "m_empty_2", "category": "calling", "content": "先輩と呼ぶこと"},
        ]
        self.assertEqual(memory_store.compute_scores_for_memories(memories, ""), {})
        self.assertEqual(memory_store.compute_scores_for_memories([], "テスト"), {})

    def test_api_rag_hits_shared_memories_resolved(self):
        """shared_memories が有効な場合、他セッションの記憶も mem_map に解決され missing にならずスコアも算出される"""
        other_sid = "test-other-sid"
        other_memories = [
            {"id": "m_shared_1", "category": "item", "status": "active", "content": "大切なペンダント", "elapsed_time": "1日目・昼"}
        ]
        server.write_json(memory_store.memory_path(other_sid), {
            "session_id": other_sid,
            "character_name": "アクア",
            "memories": other_memories
        })

        session_data = {
            "id": self.sid,
            "setting": {"character": {"name": "アクア"}, "shared_memories": True},
            "messages": [{"id": "u_msg_shared", "role": "user", "text": "ペンダント持ってる？"}]
        }
        hits_data = {
            "u_msg_shared": {
                "ids": ["m_shared_1"],
                "query": "ペンダント持ってる？",
                "tier1": 0,
                "tier2": 1,
                "ts": "2026-01-01T08:00:00"
            }
        }
        server.write_json(server.rag_hits_path(self.sid), hits_data)

        with patch("server.load", return_value=session_data):
            status, res = self._call_get(f"/api/rag/hits?sid={self.sid}&mid=u_msg_shared")
            self.assertEqual(status, 200)
            t2 = res.get("tier2", [])
            self.assertEqual(len(t2), 1)
            self.assertEqual(t2[0]["id"], "m_shared_1")
            self.assertFalse(t2[0]["missing"])
            self.assertEqual(t2[0]["content"], "大切なペンダント")
            self.assertEqual(t2[0]["category"], "item")
            self.assertIsNotNone(t2[0]["score"])
            self.assertGreater(t2[0]["score"], 0.0)

    def test_resolve_rag_hits_for_modal_archived_message(self):
        """アクティブ会話に無く archive に退避された過去の AI 応答 mid からも対応する rag_hits を引ける"""
        orig_archive_dir = server.ARCHIVE_DIR
        server.ARCHIVE_DIR = os.path.join(self.test_dir, "archive")
        os.makedirs(server.ARCHIVE_DIR, exist_ok=True)
        try:
            hits_data = {
                "u_arc_1": {
                    "ids": ["m_arc_1"],
                    "scores": {"m_arc_1": 0.77},
                    "tier1": 1,
                    "tier2": 0,
                    "ts": "2026-01-01T00:10:00"
                }
            }
            server.write_json(server.rag_hits_path(self.sid), hits_data)

            # アクティブ会話には含まれない
            active_session = {
                "id": self.sid,
                "messages": [{"id": "u_recent", "role": "user", "text": "最近の話"}]
            }
            # archive ファイルに過去メッセージが存在
            arc_file = os.path.join(server.ARCHIVE_DIR, f"{self.sid}_rot1_turn30.json")
            server.write_json(arc_file, {
                "messages": [
                    {"id": "u_arc_1", "role": "user", "text": "過去の話"},
                    {"id": "a_arc_2", "role": "assistant", "text": "昔の返答"}
                ]
            })

            with patch("server.load", return_value=active_session):
                hit = server.resolve_rag_hits_for_modal(self.sid, "a_arc_2")
                self.assertIsNotNone(hit)
                self.assertEqual(hit.get("ids"), ["m_arc_1"])
                self.assertEqual(hit.get("scores"), {"m_arc_1": 0.77})
        finally:
            server.ARCHIVE_DIR = orig_archive_dir


if __name__ == "__main__":
    unittest.main()
