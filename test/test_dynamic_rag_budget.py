# -*- coding: utf-8 -*-
"""記憶（RAG）動的予算制御 仕様書（docs/dynamic-rag-budget-spec.md）のテストスイート (D1〜D18)"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import memory_store as ms
import server


def make_msg(role, text, mid=None, **kwargs):
    m = {"id": mid or server.now(), "role": role, "text": text, "ts": server.now()}
    m.update(kwargs)
    return m


class TestDynamicRAGBudgetUnit(unittest.TestCase):
    """calc_dynamic_rag_budget, format_prompt_memories, _safe_int_env の単体テスト (D1〜D13)"""

    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_d1_calc_dynamic_rag_budget_normal_diff(self):
        """D1: 非RAG 4,200B（通常 Diff）"""
        res = server.calc_dynamic_rag_budget(4200, is_compact=True)
        self.assertEqual((2500, 800, False, 4950, False), res)

    def test_d2_calc_dynamic_rag_budget_heavy_lapi(self):
        """D2: 非RAG 7,600B（ラピ級重量キャラ）"""
        res = server.calc_dynamic_rag_budget(7600, is_compact=True)
        self.assertEqual((1550, 511, False, 1550, False), res)

    def test_d3_calc_dynamic_rag_budget_quality_fallback_tight(self):
        """D3: 非RAG 8,100B（3枚以下転落領域・Quality Fallback有効時）"""
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "1"
        res = server.calc_dynamic_rag_budget(8100, is_compact=True)
        self.assertEqual((2500, 800, False, 1050, True), res)

    def test_d4_calc_dynamic_rag_budget_floor_threatened_fallback(self):
        """D4: 非RAG 8,300B（フロア近傍・超過確実・Quality Fallback有効時）"""
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "1"
        res = server.calc_dynamic_rag_budget(8300, is_compact=True)
        self.assertEqual((2500, 800, True, 850, True), res)

    def test_d5_calc_dynamic_rag_budget_overflow(self):
        """D5: 非RAG 9,600B（単体超過・Quality Fallback有効時）"""
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "1"
        res = server.calc_dynamic_rag_budget(9600, is_compact=True)
        self.assertEqual((2500, 800, True, -450, True), res)

    def test_d6_calc_dynamic_rag_budget_full_mode(self):
        """D6: Fullモード（is_compact=False・Quality Fallback有効時）"""
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "1"
        res = server.calc_dynamic_rag_budget(8100, is_compact=False)
        self.assertEqual((2500, 800, True, 1050, True), res)

    def test_d7_calc_dynamic_rag_budget_fallback_disabled(self):
        """D7: RPCHAT_RAG_QUALITY_FALLBACK=0 による無効化（フロア未満時は想起0・保護層死守）"""
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "0"
        res = server.calc_dynamic_rag_budget(8300, is_compact=True)
        self.assertEqual((850, 0, True, 850, False), res)

    def test_d7b_calc_dynamic_rag_budget_env_soft_override(self):
        """D7b: RPCHAT_MEM_SOFT_BUDGET=1800 設定時のバイパス"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "1800"
        res = server.calc_dynamic_rag_budget(8300, is_compact=True)
        self.assertEqual((1800, 594, False, 850, False), res)

    def test_d7c_calc_dynamic_rag_budget_env_recall_override(self):
        """D7c: RPCHAT_MEM_RECALL_BUDGET 単独設定時の想起枠上書き検証 (§3.5)"""
        # 1. 通常インライン動的制御領域 (other_bytes=4200) での上書き
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "400"
        res = server.calc_dynamic_rag_budget(4200, is_compact=True)
        self.assertEqual((2500, 400, False, 4950, False), res)

        # 2. Quality Fallback領域 (other_bytes=8300) での上書き
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "1"
        res_fb = server.calc_dynamic_rag_budget(8300, is_compact=True)
        self.assertEqual((2500, 400, True, 850, True), res_fb)

    def test_d8_rule_tier1_cap_five(self):
        """D8: ルール6件投入時の Tier 1 上限検証 (RULE_TIER1_CAP=5)"""
        self.assertEqual(5, ms.RULE_TIER1_CAP)
        rules = [
            {"id": f"r{i}", "category": "rule", "content": f"ルール内容_{i}", "score": 0.9 - i * 0.1}
            for i in range(6)
        ]
        stats = {}
        text = ms.format_prompt_memories(rules, current_turn=10, stats=stats)
        self.assertEqual(5, stats["rules"])
        self.assertIn("ルール内容_0", text)
        self.assertIn("ルール内容_1", text)
        self.assertIn("ルール内容_2", text)
        self.assertIn("ルール内容_3", text)
        self.assertIn("ルール内容_4", text)
        self.assertNotIn("ルール内容_5", text)
        self.assertIn("r0", stats["included_ids"])
        self.assertIn("r1", stats["included_ids"])
        self.assertIn("r2", stats["included_ids"])
        self.assertIn("r3", stats["included_ids"])
        self.assertIn("r4", stats["included_ids"])
        self.assertNotIn("r5", stats["included_ids"])

    test_d8_rule_tier1_cap_four = test_d8_rule_tier1_cap_five

    def test_d8b_legacy_memory_budget_migration(self):
        """D8b: test/test_memory_budget.py の cap=5 移行検証"""
        import io
        from test.test_memory_budget import TestMemoryBudgetUnit, TestMemoryBudgetIntegration
        suite = unittest.TestSuite()
        suite.addTest(TestMemoryBudgetUnit("test_m1_rule_cap_is_five"))
        suite.addTest(TestMemoryBudgetUnit("test_m7_no_rule_in_recall_block"))
        suite.addTest(TestMemoryBudgetIntegration("test_m8_mem_ids_are_only_shown"))
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        res = runner.run(suite)
        self.assertTrue(res.wasSuccessful())
        self.assertEqual(0, len(res.failures))
        self.assertEqual(0, len(res.errors))

    def test_d9_format_prompt_memories_dynamic_budget_applied(self):
        """D9: soft_budget=1600 を直接指定"""
        # ルール4件(~1200B) + 想起枠(~528B) > 1600B となるように設定
        mems = [
            {"id": f"r{i}", "category": "rule", "content": f"重要ルール番号_{i}_{'れ'*90}", "score": 0.95 - i * 0.05}
            for i in range(4)
        ] + [
            {"id": f"e{i}", "category": "event", "content": f"過去の思い出出来事_{i}_{'お'*40}", "score": 0.85 - i * 0.05}
            for i in range(10)
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=10, stats=stats, soft_budget=1600)
        text_bytes = len(text.encode("utf-8"))
        self.assertLessEqual(text_bytes, 1600)
        self.assertEqual(stats["soft_budget"], 1600)
        self.assertEqual(stats["bytes"], text_bytes)
        self.assertGreater(stats["trimmed_recall"], 0)

    def test_d10_format_prompt_memories_env_override(self):
        """D10: soft_budget=2500 引数 ＋ RPCHAT_MEM_SOFT_BUDGET=1200"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "1200"
        mems = [
            {"id": f"r{i}", "category": "rule", "content": f"ルール_{i}_{'る'*20}", "score": 0.95 - i * 0.05}
            for i in range(4)
        ] + [
            {"id": f"e{i}", "category": "event", "content": f"出来事_{i}_{'え'*30}", "score": 0.85 - i * 0.05}
            for i in range(8)
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=10, stats=stats, soft_budget=2500)
        text_bytes = len(text.encode("utf-8"))
        self.assertLessEqual(text_bytes, 1200)
        self.assertEqual(stats["soft_budget"], 1200)

    def test_d11_format_prompt_memories_quality_floor_preserved(self):
        """D11: 極小引数 soft_budget=200（ルール保護優先で recall は 0 までトリム）"""
        mems = [
            {"id": f"r{i}", "category": "rule", "content": f"ルール_{i}_{'あ'*20}", "score": 0.9 - i * 0.1}
            for i in range(3)
        ] + [
            {"id": f"e{i}", "category": "event", "content": f"出来事_{i}_{'い'*20}", "score": 0.8 - i * 0.1}
            for i in range(3)
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=5, stats=stats, soft_budget=200)
        self.assertEqual(2, stats["rules"])
        self.assertEqual(0, stats["recall"])
        self.assertTrue(stats["floor_hit"])
        self.assertIn("ルール_0", text)
        self.assertIn("ルール_1", text)
        self.assertNotIn("ルール_2", text)
        self.assertNotIn("出来事_0", text)
        self.assertNotIn("出来事_1", text)

    def test_d12_format_prompt_memories_env_soft_alone_scales_recall(self):
        """D12: soft_budget=900, recall_budget=297 引数 ＋ RPCHAT_MEM_SOFT_BUDGET=2500 単独指定"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "2500"
        mems = [
            {"id": f"r{i}", "category": "rule", "content": f"ルール_{i}", "score": 0.95 - i * 0.05}
            for i in range(2)
        ] + [
            {"id": f"e{i}", "category": "event", "content": f"イベント記録番号_{i}_{'え'*15}", "score": 0.9 - i * 0.05}
            for i in range(8)
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=5, stats=stats, soft_budget=900, recall_budget=297)
        self.assertEqual(800, stats["recall_budget"])
        self.assertEqual(2500, stats["soft_budget"])
        self.assertGreaterEqual(stats["recall"], 4)

    def test_d13_safe_int_env_empty_string_fallback(self):
        """D13: RPCHAT_MEM_SOFT_BUDGET=""（空文字）"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = ""
        self.assertIsNone(ms._safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None))
        self.assertIsNone(server._safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None))

        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "   "
        self.assertIsNone(ms._safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None))
        self.assertIsNone(server._safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None))

        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = ""
        mems = [
            {"id": f"e{i}", "category": "event", "content": f"イベント_{i}_{'あ'*30}", "score": 0.9 - i * 0.05}
            for i in range(10)
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=5, stats=stats, soft_budget=1500)
        self.assertEqual(1500, stats["soft_budget"])
        self.assertLessEqual(len(text.encode("utf-8")), 1500)
        self.assertLess(stats["recall"], 10)

    def test_d13b_safe_int_env_float_string_and_robustness(self):
        """D13b: 浮動小数点文字列 "1500.0" や型変換の堅牢性検証"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "1500.0"
        self.assertEqual(1500, ms._safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None))
        self.assertEqual(1500, server._safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None))

        del os.environ["RPCHAT_MEM_SOFT_BUDGET"]
        stats = {}
        mems = [{"id": "r1", "category": "rule", "content": "ルール", "score": 0.9}]
        ms.format_prompt_memories(mems, current_turn=1, stats=stats, soft_budget="1600.0", recall_budget="500.0")
        self.assertEqual(1600, stats["soft_budget"])
        self.assertEqual(500, stats["recall_budget"])

    def test_d13c_negative_env_guards(self):
        """D13c: 負数の環境変数に対する非負安全クランプ検証"""
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "-50"
        os.environ["RPCHAT_RAG_MARGIN"] = "-100"
        res = server.calc_dynamic_rag_budget(4200, is_compact=True)
        # recall_budget は 0 にクランプされ、負数とならないこと
        self.assertEqual(0, res[1])
        # margin は 0 にクランプされ、headroom = 9400 - 4200 - 0 = 5200
        self.assertEqual(5200, res[3])


class TestDynamicRAGBudgetIntegration(unittest.TestCase):
    """server.build_context と perf.log の結合テスト (D14〜D18)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_srv_dyn_rag_")
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "RAG_DIR", "SENT", "CTX", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)
        self.orig_ms_dirs = (ms.DATA, ms.RAG_DIR)
        ms.DATA = self.tmp
        ms.RAG_DIR = os.path.join(self.tmp, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        for k, v in self.orig_dirs.items():
            if v is not None:
                setattr(server, k, v)
        ms.DATA, ms.RAG_DIR = self.orig_ms_dirs
        ms._memory_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_d14_build_context_pipeline_order(self):
        """D14: server.build_context のセクション順序検証 (Pre-RAG -> RAG -> Post-RAG)"""
        s = server.create_session({"title": "D14テスト", "ide_session": "ide-1"})
        s["setting"]["image_mode"] = "always"
        s["setting"]["instructions"] = "指示の本文です。"
        m = make_msg("user", "ユーザー発言本文", mid="u1")
        s["messages"].append(m)
        server.save(s)

        mems = [
            {"id": "r1", "_tier": 1, "category": "rule", "content": "ルール内容1", "score": 0.95},
            {"id": "e1", "_tier": 2, "category": "event", "content": "思い出出来事1", "score": 0.85},
        ]
        with patch("memory_store.search_memories", return_value=mems):
            ctx = server.build_context(s, m)

        labels = [sec["label"] for sec in ctx["sections"]]
        self.assertIn("過去の記憶・約束（関連する記録）", labels)
        self.assertIn("ユーザーの発言（これに応答する）", labels)

        pre_idx = next(i for i, lb in enumerate(labels) if "指示" in lb or "AIメモ" in lb)
        rag_idx = labels.index("過去の記憶・約束（関連する記録）")
        post_idx = labels.index("ユーザーの発言（これに応答する）")

        self.assertLess(pre_idx, rag_idx, "Pre-RAG section must appear before RAG section")
        self.assertLess(rag_idx, post_idx, "RAG section must appear before Post-RAG section")

    def test_d15_build_context_heavy_character_inline_success(self):
        """D15: 非RAG 7,600B のセッション（ラピ級重量キャラでインライン成功・spillなし）"""
        os.environ["RPCHAT_PERF_LOG"] = "1"
        s = server.create_session({"title": "D15ラピテスト", "ide_session": "ide-1"})
        # 1ターン目を送信して差分モードにする
        m0 = make_msg("user", "初期化発言", mid="u0")
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = make_msg("assistant", "初期化応答", mid="a0")
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)
        server.save(s)

        # 2ターン目: other_bytes を ~7,600B に調整
        # 差分モードでユーザー発言を適切に調整して non_rag_bytes ≈ 7600B にする
        # まずダミーで測り、測定前の sent 状態を復元する
        sent_backup = copy.deepcopy(server.read_sent(s["id"]))
        m_probe = make_msg("user", "x", mid="u1")
        s["messages"].append(m_probe)
        ctx_probe = server.build_context(s, m_probe)
        base_other = ctx_probe["retrieved"]["non_rag_bytes"] - len("x".encode("utf-8"))
        needed = 7600 - base_other
        pad_text = "あ" * (needed // 3)

        s["messages"].pop()
        server.write_sent(s["id"], sent_backup)
        m1 = make_msg("user", pad_text, mid="u1")
        s["messages"].append(m1)
        server.save(s)

        mems = [
            {"id": f"r{i}", "_tier": 1, "category": "rule", "content": f"ルール内容_{i}", "score": 0.95 - i * 0.05}
            for i in range(4)
        ] + [
            {"id": f"e{i}", "_tier": 2, "category": "event", "content": f"過去の出来事_{i}", "score": 0.85 - i * 0.05}
            for i in range(4)
        ]
        with patch("memory_store.search_memories", return_value=mems):
            ctx = server.build_context(s, m1)

        ret = ctx["retrieved"]
        # other_bytes は約 7,600B
        self.assertAlmostEqual(7600, ret["non_rag_bytes"], delta=30)
        # abandon_inline は False
        self.assertFalse(ret["abandon_inline"])
        self.assertFalse(ret["floor_threatened"])

        # perf.log の確認: est_payload_bytes <= 9400 (inline維持)
        perf_path = os.path.join(server.DATA, "perf.log")
        self.assertTrue(os.path.isfile(perf_path))
        with open(perf_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        last_perf = lines[-1]
        self.assertLessEqual(last_perf["est_payload_bytes"], 9400)
        self.assertFalse(last_perf["spilled"])
        self.assertFalse(last_perf["rag_abandon_inline"])

    def test_d16_build_context_quality_fallback_execution(self):
        """D16: 非RAG 8,300B のセッション（Quality Fallback発動・上限2500Bフル積載）"""
        os.environ["RPCHAT_PERF_LOG"] = "1"
        os.environ["RPCHAT_RAG_QUALITY_FALLBACK"] = "1"
        s = server.create_session({"title": "D16超重量テスト", "ide_session": "ide-1"})
        m0 = make_msg("user", "初期化発言", mid="u0")
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = make_msg("assistant", "初期化応答", mid="a0")
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)
        server.save(s)

        # other_bytes を ~8,300B に調整
        sent_backup = copy.deepcopy(server.read_sent(s["id"]))
        m_probe = make_msg("user", "x", mid="u1")
        s["messages"].append(m_probe)
        ctx_probe = server.build_context(s, m_probe)
        base_other = ctx_probe["retrieved"]["non_rag_bytes"] - len("x".encode("utf-8"))
        needed = 8300 - base_other
        pad_text = "あ" * (needed // 3)

        s["messages"].pop()
        server.write_sent(s["id"], sent_backup)
        m1 = make_msg("user", pad_text, mid="u1")
        s["messages"].append(m1)
        server.save(s)

        # 5つのルール＋10個のイベント
        mems = [
            {"id": f"r{i}", "_tier": 1, "category": "rule", "content": f"極秘ルール_{i}_{'る'*20}", "score": 0.95 - i * 0.05}
            for i in range(5)
        ] + [
            {"id": f"e{i}", "_tier": 2, "category": "event", "content": f"重大出来事_{i}_{'え'*30}", "score": 0.85 - i * 0.05}
            for i in range(10)
        ]
        with patch("memory_store.search_memories", return_value=mems):
            ctx = server.build_context(s, m1)

        ret = ctx["retrieved"]
        # other_bytes ≈ 8,300B
        self.assertAlmostEqual(8300, ret["non_rag_bytes"], delta=30)
        # Quality Fallback 発動
        self.assertTrue(ret["abandon_inline"])
        self.assertEqual(2500, ret["soft_budget"])
        self.assertEqual(800, ret["recall_budget"])

        # perf.log の確認: spilled == True
        perf_path = os.path.join(server.DATA, "perf.log")
        with open(perf_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        last_perf = lines[-1]
        self.assertTrue(last_perf["spilled"])
        self.assertTrue(last_perf["rag_abandon_inline"])
        self.assertEqual(2500, last_perf["rag_soft_budget"])

    def test_d17_perf_log_contains_dynamic_metrics(self):
        """D17: perf.log に新設動的メトリクス全項目が記録されること"""
        os.environ["RPCHAT_PERF_LOG"] = "1"
        s = server.create_session({"title": "D17メトリクステスト", "ide_session": "ide-1"})
        m = make_msg("user", "メトリクス検証用発言", mid="u1")
        s["messages"].append(m)
        server.save(s)

        mems = [
            {"id": "r1", "_tier": 1, "category": "rule", "content": "ルールA", "score": 0.95},
            {"id": "e1", "_tier": 2, "category": "event", "content": "イベントA", "score": 0.85},
        ]
        with patch("memory_store.search_memories", return_value=mems):
            server.build_context(s, m)

        perf_path = os.path.join(server.DATA, "perf.log")
        self.assertTrue(os.path.isfile(perf_path))
        with open(perf_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        last_perf = lines[-1]

        expected_keys = {
            "rag_non_rag_bytes",
            "rag_dynamic_budget",
            "rag_dynamic_recall",
            "rag_soft_budget",
            "rag_headroom_bytes",
            "rag_floor_threatened",
            "rag_floor_hit",
            "rag_abandon_inline",
        }
        self.assertTrue(expected_keys.issubset(last_perf.keys()))
        self.assertIsInstance(last_perf["rag_non_rag_bytes"], int)
        self.assertIsInstance(last_perf["rag_dynamic_budget"], int)
        self.assertIsInstance(last_perf["rag_dynamic_recall"], int)
        self.assertIsInstance(last_perf["rag_soft_budget"], int)
        self.assertIsInstance(last_perf["rag_headroom_bytes"], int)
        self.assertIsInstance(last_perf["rag_floor_threatened"], bool)
        self.assertIsInstance(last_perf["rag_floor_hit"], bool)
        self.assertIsInstance(last_perf["rag_abandon_inline"], bool)

    def test_d18_existing_test_suite_regression(self):
        """D18: 既存全テスト群の検出とリグレッション検証"""
        import io
        from test import test_memory_budget
        mb_suite = unittest.TestLoader().loadTestsFromModule(test_memory_budget)
        mb_res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(mb_suite)
        self.assertTrue(mb_res.wasSuccessful())
        self.assertEqual(0, len(mb_res.failures))
        self.assertEqual(0, len(mb_res.errors))

        loader = unittest.TestLoader()
        full_suite = loader.discover(os.path.join(ROOT, "test"), pattern="test_*.py")
        self.assertGreaterEqual(full_suite.countTestCases(), 442)


if __name__ == "__main__":
    unittest.main()
