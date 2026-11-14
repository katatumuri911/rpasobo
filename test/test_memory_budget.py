# -*- coding: utf-8 -*-
"""記憶（RAG）予算制御 実装プラン v1.1 の単体テスト (M1〜M13)"""
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


class TestMemoryBudgetUnit(unittest.TestCase):
    """format_prompt_memories および 定数・スコア選抜のユニットテスト"""

    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_m1_rule_cap_is_five(self):
        """M1: ルール6件 → 5件のみ・6件目が本文に無い"""
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


    def test_m2_manual_not_prioritized(self):
        """M2: manual 低スコア < auto 高スコアの順"""
        tmp = tempfile.mkdtemp(prefix="test_ms_")
        orig = (ms.DATA, ms.RAG_DIR)
        ms.DATA = tmp
        ms.RAG_DIR = os.path.join(tmp, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()
        try:
            # 2つのルール: 1つは手動(スコア低め想定)、1つは自動(高一致想定)
            ms.add_manual_memory("s_m2", "ラピ", "rule", "低関連ルール 手動")
            ms.save_extracted_memories(
                "s_m2", "ラピ",
                [{"category": "rule", "content": "高関連ルール 自動 門限 禁止"}],
                turn_idx=10, mid="m10"
            )
            hits = ms.search_memories("s_m2", "門限のルールについて", current_turn=10)
            rule_hits = [h for h in hits if h.get("category") == "rule"]
            self.assertGreaterEqual(len(rule_hits), 2)
            # 自動の高スコアが先頭に来ること
            self.assertIn("高関連ルール", rule_hits[0]["content"])
            self.assertIn("低関連ルール", rule_hits[1]["content"])
        finally:
            ms.DATA, ms.RAG_DIR = orig
            ms._memory_cache.clear()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_m3_promise_not_guaranteed(self):
        """M3: 約束保証枠廃止・無関連の約束は載らない / 関連時は載る"""
        tmp = tempfile.mkdtemp(prefix="test_prom_")
        orig = (ms.DATA, ms.RAG_DIR)
        ms.DATA = tmp
        ms.RAG_DIR = os.path.join(tmp, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()
        try:
            ms.save_extracted_memories(
                "s_m3", "ラピ",
                [
                    {"category": "promise", "content": "公園でアイスを食べる約束"},
                    {"category": "promise", "content": "図書館で宿題をする約束"},
                ],
                turn_idx=10, mid="m10"
            )
            # クエリがまったく無関係な場合、約束が強制枠で2件載ることはない（スコア不足で落ちる）
            hits_unrelated = ms.search_memories("s_m3", "明日の天候と風速", current_turn=10)
            prom_unrelated = [h for h in hits_unrelated if h.get("category") == "promise"]
            self.assertEqual(0, len(prom_unrelated))

            # クエリに関連する場合、約束が検索される
            hits_related = ms.search_memories("s_m3", "アイスを食べる約束について", current_turn=10)
            prom_related = [h for h in hits_related if h.get("category") == "promise"]
            self.assertGreaterEqual(len(prom_related), 1)
            self.assertIn("アイス", prom_related[0]["content"])
        finally:
            ms.DATA, ms.RAG_DIR = orig
            ms._memory_cache.clear()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_m4_floor_keeps_two_rules_one_recall(self):
        """M4: 極小 soft ではルール保護優先で recall は 0 まで削られ、floor_hit=True"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "50"  # 極小予算
        mems = [
            {"id": "r1", "category": "rule", "content": "ルールその一", "score": 0.9},
            {"id": "r2", "category": "rule", "content": "ルールその二", "score": 0.8},
            {"id": "r3", "category": "rule", "content": "ルールその三", "score": 0.7},
            {"id": "e1", "category": "event", "content": "出来事その一", "score": 0.85},
            {"id": "e2", "category": "event", "content": "出来事その二", "score": 0.75},
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=5, stats=stats)
        self.assertEqual(2, stats["rules"])
        self.assertEqual(0, stats["recall"])
        self.assertTrue(stats["floor_hit"])
        self.assertIn("ルールその一", text)
        self.assertIn("ルールその二", text)
        self.assertNotIn("ルールその三", text)
        self.assertNotIn("出来事その一", text)
        self.assertNotIn("出来事その二", text)

    def test_m5_oversized_first_recall_still_selected(self):
        """M5: 初回候補が budget 超過でも 1件は載る（②の回帰テスト）"""
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "100"
        # 1件目が100Bを超える巨大な記憶
        huge_content = "とても長い思い出の記憶" * 20  # ~360 bytes
        mems = [
            {"id": "e_huge", "category": "event", "content": huge_content, "score": 0.95},
            {"id": "e_small", "category": "event", "content": "小さな出来事", "score": 0.5},
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=5, stats=stats)
        self.assertGreaterEqual(stats["recall"], 1)
        self.assertIn("とても長い思い出の記憶", text)

    def test_m6_no_dict_typeerror_on_trim(self):
        """M6: トリムで TypeError (int -= dict) が出ない（①の回帰テスト）"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "300"
        mems = [
            {"id": "r1", "category": "rule", "content": "ルール長い" * 15, "score": 0.9},
            {"id": "r2", "category": "rule", "content": "ルール長い" * 15, "score": 0.8},
            {"id": "r3", "category": "rule", "content": "ルール長い" * 15, "score": 0.7},
            {"id": "e1", "category": "event", "content": "出来事長い" * 15, "score": 0.85},
            {"id": "e2", "category": "event", "content": "出来事長い" * 15, "score": 0.65},
        ]
        stats = {}
        try:
            ms.format_prompt_memories(mems, current_turn=5, stats=stats)
        except TypeError as e:
            self.fail(f"format_prompt_memories raised TypeError during trim: {e}")

    def test_m7_no_rule_in_recall_block(self):
        """M7: rule が t2 ブロック（過去の記憶）に現れない（③）"""
        rules = [
            {"id": f"r{i}", "category": "rule", "content": f"極秘ルール_{i}", "score": 0.95 - i * 0.05}
            for i in range(6)
        ]
        text = ms.format_prompt_memories(rules, current_turn=5)
        # t2 ブロックの注記（過去の会話から思い出した内容）がないこと
        self.assertNotIn("過去の会話から思い出した内容", text)
        self.assertIn("極秘ルール_4", text)
        self.assertNotIn("極秘ルール_5", text)

    def test_m10_stats_defaults_on_empty(self):
        """M10: 空入力でも stats が全キーを持つ（⑪）"""
        stats = {}
        text = ms.format_prompt_memories([], current_turn=1, stats=stats)
        self.assertEqual("", text)
        expected_keys = {
            "included_ids", "rules", "calling", "recall",
            "bytes", "trimmed_rules", "trimmed_recall", "floor_hit"
        }
        self.assertTrue(expected_keys.issubset(stats.keys()))
        self.assertEqual([], stats["included_ids"])
        self.assertEqual(0, stats["rules"])
        self.assertEqual(0, stats["calling"])
        self.assertEqual(0, stats["recall"])
        self.assertEqual(0, stats["bytes"])
        self.assertEqual(0, stats["trimmed_rules"])
        self.assertEqual(0, stats["trimmed_recall"])
        self.assertFalse(stats["floor_hit"])

    def test_m12_recall_budget_600(self):
        """M12: 想起合計 <= 600B（フロア時を除く）"""
        # 10件の想起候補（各約120B）
        mems = [
            {"id": f"e{i}", "category": "event", "content": f"短いイベント記録番号_{i}_{'あ'*20}", "score": 0.9 - i * 0.05}
            for i in range(10)
        ]
        stats = {}
        ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        # 予算 600B のため、10件全部は入らず、4〜5件程度に制限されること
        self.assertLess(stats["recall"], 10)
        self.assertGreaterEqual(stats["recall"], 1)

    def test_m13_notes_compacted(self):
        """M13: compact 時に旧注記（186B/170B）が無い"""
        os.environ["RPCHAT_COMPACT"] = "1"
        mems = [
            {"id": "r1", "category": "rule", "content": "ルール", "score": 0.9},
            {"id": "e1", "category": "event", "content": "出来事", "score": 0.9},
        ]
        text_compact = ms.format_prompt_memories(mems, current_turn=1)
        self.assertIn("※現在の前提（ピン留めされた呼称・ルールなど）。常に守る。", text_compact)
        self.assertIn("※過去の会話から思い出した内容。自然に触れてよい（IDは出すな）。", text_compact)
        # 旧注記が無い
        self.assertNotIn("※以下は現在の呼び方・ルール・所持品・身体状態など", text_compact)
        self.assertNotIn("話の流れに合わせて自然に言及・引用し", text_compact)

        # non-compact の場合
        os.environ["RPCHAT_COMPACT"] = "0"
        text_full = ms.format_prompt_memories(mems, current_turn=1)
        self.assertIn("※以下は現在の前提となる確定事項（ピン留めされた呼称、ルールなど）です。", text_full)
        self.assertIn("話の流れに合わせて自然に言及・引用し", text_full)

    def test_soft_budget_zero_disables_all_budget(self):
        """⑤ RPCHAT_MEM_SOFT_BUDGET=0 で記憶出力なし (soft=0 早期リターン・floor_hit=True)"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "0"
        mems = [
            {"id": f"e{i}", "category": "event", "content": f"イベント_{i}_{'あ'*25}", "score": 0.9 - i * 0.05}
            for i in range(10)
        ]
        stats = {}
        res = ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        self.assertEqual("", res)
        self.assertEqual(0, stats["recall"])
        self.assertTrue(stats["floor_hit"])


class TestMemoryBudgetIntegration(unittest.TestCase):
    """server.build_context との連携テスト (M8, M9, M11)"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_srv_rag_")
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

    def test_m8_mem_ids_are_only_shown(self):
        """M8: write_sent.mem_ids == included_ids + carried（トリムされたIDは入らない）"""
        s = server.create_session({"title": "M8テスト", "ide_session": "ide-1"})
        m = make_msg("user", "テスト", mid="u1")
        s["messages"].append(m)
        server.save(s)

        # 6つのルール（上位5件のみ採用・RULE_TIER1_CAP=5）＋ 2つのイベント（1つのみ予算内）
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "100"

        mems = [
            {"id": "r1", "_tier": 1, "category": "rule", "content": "ルール1", "score": 0.95},
            {"id": "r2", "_tier": 1, "category": "rule", "content": "ルール2", "score": 0.90},
            {"id": "r3", "_tier": 1, "category": "rule", "content": "ルール3", "score": 0.85},
            {"id": "r4", "_tier": 1, "category": "rule", "content": "ルール4", "score": 0.80},
            {"id": "r5", "_tier": 1, "category": "rule", "content": "ルール5", "score": 0.75},
            {"id": "r6", "_tier": 1, "category": "rule", "content": "ルール6", "score": 0.70},
            {"id": "e1", "_tier": 2, "category": "event", "content": "イベント1 " * 4, "score": 0.88},
            {"id": "e2", "_tier": 2, "category": "event", "content": "イベント2 " * 20, "score": 0.60},
        ]
        with patch("memory_store.search_memories", return_value=mems):
            server.build_context(s, m)

        sent = server.read_sent(s["id"])
        recorded_ids = set(sent.get("mem_ids") or [])
        # r1, r2, r3, r4, r5, e1 のみ含まれ、r6 (cap超過) と e2 (予算オーバー) は含まれない
        self.assertEqual({"r1", "r2", "r3", "r4", "r5", "e1"}, recorded_ids)

    def test_m9_mem_ids_kept_on_search_error(self):
        """M9: 検索例外時に前回 mem_ids を維持（⑩）"""
        s = server.create_session({"title": "M9テスト", "ide_session": "ide-1"})
        m1 = make_msg("user", "1回目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        # 前回の sent に mem_ids を記録
        server.write_sent(s["id"], {"ide": "ide-1", "msgs": [], "mem_ids": ["prev_m1", "prev_m2"]})

        m2 = make_msg("user", "2回目（検索エラー発生）", mid="u2")
        s["messages"].append(m2)
        server.save(s)

        with patch("memory_store.search_memories", side_effect=RuntimeError("Search failed")):
            ctx = server.build_context(s, m2)

        self.assertIsNotNone(ctx)
        sent = server.read_sent(s["id"])
        # 前回のが維持されていること
        self.assertEqual(["prev_m1", "prev_m2"], sent.get("mem_ids"))

    def test_m11_dropped_memory_returns_next_turn(self):
        """M11: 削られた記憶が carried 化しない（次ターンに新規として再評価される）"""
        s = server.create_session({"title": "M11テスト", "ide_session": "ide-1"})
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        # e1 は採用、e2 はトリムされて落ちたとする
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "500"
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "150"
        mems = [
            {"id": "e1", "_tier": 2, "category": "event", "content": "イベント1 " * 8, "score": 0.9},
            {"id": "e2", "_tier": 2, "category": "event", "content": "イベント2 " * 8, "score": 0.8},
        ]
        with patch("memory_store.search_memories", return_value=mems):
            server.build_context(s, m1)

        sent1 = server.read_sent(s["id"])
        self.assertIn("e1", sent1.get("mem_ids", []))
        self.assertNotIn("e2", sent1.get("mem_ids", []))

        # 2ターン目: AI 応答後にユーザー発言
        a1 = make_msg("assistant", "応答", mid="a1")
        s["messages"].append(a1)
        server.save(s)
        server.note_reply(s, m1["id"], a1)

        m2 = make_msg("user", "2ターン目", mid="u2")
        s["messages"].append(m2)
        server.save(s)

        # 2ターン目で e2 のスコアが上がり、e1 が無くなった場合
        mems_turn2 = [
            {"id": "e2", "_tier": 2, "category": "event", "content": "イベント2 " * 8, "score": 0.95},
        ]
        with patch("memory_store.search_memories", return_value=mems_turn2):
            ctx2 = server.build_context(s, m2)

        rag_sec = next(sec for sec in ctx2["sections"] if "過去の記憶" in sec["label"])
        # e2 は carried ではなく新規全文として出力される
        self.assertIn("イベント2", rag_sec["text"])
        self.assertNotIn("前回と同じ前提: 1件 — 「イベント2", rag_sec["text"])


    def test_calling_separate_from_rules_in_stats(self):
        """⑦ stats['rules'] に calling が混入せず分離されていること（非ピン留めは recall に計上、ピン留めは calling に計上）"""
        mems = [
            {"id": "c1", "category": "calling", "content": "指揮官", "score": 0.95},
            {"id": "r1", "category": "rule", "content": "ルール1", "score": 0.90},
            {"id": "r2", "category": "rule", "content": "ルール2", "score": 0.85},
        ]
        stats = {}
        ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        # 非ピン留めの calling は Tier 2（recall）へ移るため、Tier 1 内の calling は 0
        self.assertEqual(0, stats["calling"])
        self.assertEqual(2, stats["rules"])
        self.assertEqual(2, stats["tier1"])
        self.assertEqual(1, stats["recall"])
        self.assertIn("c1", stats["included_ids"])
        self.assertIn("r1", stats["included_ids"])
        self.assertIn("r2", stats["included_ids"])

        # ピン留めされた calling は Tier 1 に入り stats['calling'] に計上される
        stats_pin = {}
        mems_pin = [
            {"id": "c_pin", "category": "calling", "content": "マスター", "score": 0.95, "priority": "pinned"},
            {"id": "r1", "category": "rule", "content": "ルール1", "score": 0.90},
            {"id": "r2", "category": "rule", "content": "ルール2", "score": 0.85},
        ]
        ms.format_prompt_memories(mems_pin, current_turn=1, stats=stats_pin)
        self.assertEqual(1, stats_pin["calling"])
        self.assertEqual(2, stats_pin["rules"])
        self.assertEqual(3, stats_pin["tier1"])
        self.assertEqual(0, stats_pin["recall"])
        self.assertIn("c_pin", stats_pin["included_ids"])

    def test_missing_fields_tolerance(self):
        """None スコアや ID 欠落などの不完全な辞書でも例外が出ない"""
        mems = [
            {"content": "内容のみ"},
            {"id": "r_none_score", "category": "rule", "content": "スコア無しルール", "score": None},
            {"id": "e_none_score", "category": "event", "content": "スコア無しイベント", "score": None},
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        self.assertIn("内容のみ", text)
        self.assertIn("スコア無しルール", text)
        self.assertEqual(1, stats["rules"])
        self.assertEqual(2, stats["recall"])

    def test_stats_none_is_supported(self):
        """stats=None の呼び出しでも正常に整形される"""
        mems = [{"id": "r1", "category": "rule", "content": "ルール", "score": 0.9}]
        text = ms.format_prompt_memories(mems, current_turn=1, stats=None)
        self.assertIn("ルール", text)

    def test_category_aliases_resolved_in_formatting(self):
        """taboo や 呼称 などのエイリアスが想起枠へ漏れず正しく保護層・想起層に振り分けられる"""
        mems = [
            {"id": "t1", "category": "taboo", "content": "禁忌事項の絶対遵守", "score": 0.9},
            {"id": "c1", "category": "呼称", "content": "お兄ちゃん", "score": 0.85},
            {"id": "i1", "category": "item_status", "content": "合鍵を首から下げている", "score": 0.80},
        ]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        self.assertEqual(1, stats["rules"])
        self.assertEqual(0, stats["calling"])
        self.assertEqual(2, stats["recall"])
        self.assertEqual(1, stats["tier1"])
        self.assertIn("禁忌事項の絶対遵守", text)
        self.assertIn("お兄ちゃん", text)
        self.assertIn("合鍵を首から下げている", text)
        # taboo はルール・禁忌見出しの下にあり、想起注記の下には入らない
        blocks = text.split("\n\n")
        self.assertIn("禁忌事項の絶対遵守", blocks[0])
        self.assertNotIn("禁忌事項の絶対遵守", blocks[1])
        # 呼称（非ピン留め）は想起注記の下に入る
        self.assertIn("お兄ちゃん", blocks[1])
        self.assertNotIn("お兄ちゃん", blocks[0])

    def test_robustness_malformed_inputs(self):
        """None/文字列混入、不正なscore、不正な環境変数でも例外が出ない"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "invalid_int"
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "bad"
        mems = [
            None,
            "不正文字列",
            {"id": "r1", "category": "rule", "content": "正常ルール", "score": "bad_score"},
            {"id": "r2", "category": "rule", "content": "スコアNone", "score": None},
        ]
        carried = [None, "bad_carried", {"id": "c1", "content": "正常carried"}]
        stats = {}
        text = ms.format_prompt_memories(mems, current_turn="not_int", carried_memories=carried, stats=stats)
        self.assertIn("正常ルール", text)
        self.assertIn("正常carried", text)
        self.assertEqual(2, stats["rules"])

    def test_included_ids_deduplication(self):
        """重複IDが渡されても stats['included_ids'] は重複しない"""
        mems = [
            {"id": "r1", "category": "rule", "content": "ルール1", "score": 0.9},
            {"id": "r1", "category": "rule", "content": "ルール1重複", "score": 0.8},
        ]
        stats = {}
        ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        self.assertEqual(["r1"], stats["included_ids"])

    def test_retrieved_schema_complete_on_search_error(self):
        """検索例外時でも ctx['retrieved'] が全キー（included, mem_bytes等）を保持する"""
        s = server.create_session({"title": "ErrorSchemaテスト", "ide_session": "ide-1"})
        m = make_msg("user", "テスト", mid="u1")
        s["messages"].append(m)
        server.save(s)

        with patch("memory_store.search_memories", side_effect=RuntimeError("Search error")):
            ctx = server.build_context(s, m)

        ret = ctx.get("retrieved") or {}
        expected_keys = {"count", "tier1", "tier2", "ids", "query", "included", "mem_bytes", "trim_rules", "trim_recall", "floor_hit"}
        self.assertTrue(expected_keys.issubset(ret.keys()), f"Missing keys: {expected_keys - set(ret.keys())}")
        self.assertEqual(0, ret["mem_bytes"])
        self.assertEqual(0, ret["included"])
        self.assertFalse(ret["floor_hit"])

    def test_carried_lifecycle_turn3_becomes_carried(self):
        """1ターン目に削られた記憶が2ターン目で表示され、3ターン目で正常にcarried化するサイクル"""
        s = server.create_session({"title": "Lifecycleテスト", "ide_session": "ide-1"})
        m1 = make_msg("user", "1ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        # 1ターン目: e1採用、e2トリム
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "500"
        os.environ["RPCHAT_MEM_RECALL_BUDGET"] = "150"
        mems_t1 = [
            {"id": "e1", "_tier": 2, "category": "event", "content": "イベント1 " * 8, "score": 0.9},
            {"id": "e2", "_tier": 2, "category": "event", "content": "イベント2 " * 8, "score": 0.8},
        ]
        with patch("memory_store.search_memories", return_value=mems_t1):
            server.build_context(s, m1)

        # 2ターン目: e2 が採用された
        a1 = make_msg("assistant", "応答1", mid="a1")
        s["messages"].append(a1)
        server.save(s)
        server.note_reply(s, m1["id"], a1)

        m2 = make_msg("user", "2ターン目", mid="u2")
        s["messages"].append(m2)
        server.save(s)

        mems_t2 = [
            {"id": "e2", "_tier": 2, "category": "event", "content": "イベント2 " * 8, "score": 0.95},
        ]
        with patch("memory_store.search_memories", return_value=mems_t2):
            ctx2 = server.build_context(s, m2)

        sent2 = server.read_sent(s["id"])
        self.assertIn("e2", sent2.get("mem_ids", []))

        # 3ターン目: e2 が再びヒット -> 2ターン目で表示済みなので今回は carried になること
        a2 = make_msg("assistant", "応答2", mid="a2")
        s["messages"].append(a2)
        server.save(s)
        server.note_reply(s, m2["id"], a2)

        m3 = make_msg("user", "3ターン目", mid="u3")
        s["messages"].append(m3)
        server.save(s)

        with patch("memory_store.search_memories", return_value=mems_t2):
            ctx3 = server.build_context(s, m3)

        rag_sec3 = next(sec for sec in ctx3["sections"] if "過去の記憶" in sec["label"])
        self.assertIn("前回と同じ前提: 1件 — 「イベント2", rag_sec3["text"])


class TestRuleStandingV12(unittest.TestCase):
    """v1.2: 地の文（一過性の情景描写）を rule の常時枠に上げない

    背景: ラピの記憶 19 rule のうち 8 件が「〜のに」「〜てしまう」型の地の文で、
    クエリ類似度でソートしていたため 4 枠を占領し、実際の催眠ルールが落ちていた。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_rule_")
        self.orig = (ms.DATA, ms.RAG_DIR)
        ms.DATA = self.tmp
        ms.RAG_DIR = os.path.join(self.tmp, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()

    def tearDown(self):
        ms.DATA, ms.RAG_DIR = self.orig
        ms._memory_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put_rule(self, sid, mem_id, content, source="auto", turn_idx=5):
        """保存時の降格が入る前に保存された rule を再現する（既存データ想定）"""
        try:
            tokens = ms.extract_tokens(content)
        except Exception:
            tokens = []
        data = ms.load_memories(sid, "ラピ")
        data.setdefault("memories", []).append({
            "id": mem_id, "session_id": sid, "turn_idx": turn_idx,
            "elapsed_time": {"day": 1, "period": "午前"},
            "source": source, "category": "rule", "status": "active",
            "content": content, "history": [],
            "content_hash": ms.compute_hash(content),
            "created_at": 1.0, "updated_at": 1.0,
            "tokens": tokens, "embedding": [],
        })
        ms.save_memories(sid, data)

    def test_is_standing_rule_helpers(self):
        self.assertTrue(ms.is_standing_rule("ラピは挨拶することが常識だと刷り込まれた。"))
        self.assertTrue(ms.is_standing_rule("その場の反応", source="manual"))
        self.assertFalse(ms.is_standing_rule("声を出してはいけないのに、吐息が零れ落ちる。"))
        self.assertFalse(ms.is_standing_rule("催眠、ラピのアナルに指が入る。"))
        self.assertTrue(ms.looks_transient_rule("身体が熱く火照ってしまう"))

    def test_manual_flag_does_not_bypass_transient_veto(self):
        """manual は「編集した」以上の意味を持たない（少し直すだけで manual になるため）"""
        narrative = "ラピは声を出してはいけないのに、口元から甘い吐息が零れ落ちる。"
        self.assertEqual(0, ms.rule_standing_level(narrative, "manual"))
        self.assertEqual(0, ms.rule_standing_level(narrative, "auto"))
        self.assertEqual(3, ms.rule_standing_level("ラピは挨拶することが常識だと刷り込まれた。", "auto"))
        self.assertEqual(2, ms.rule_standing_level("ラピは挨拶することが常識だと刷り込まれた。", "auto", is_other=True))
        self.assertEqual(1, ms.rule_standing_level("敬語で話すこと", "manual"))

    def test_manual_edited_narration_still_loses_rule_slot(self):
        """手動修正で source=manual になった地の文が、自動抽出の恒久ルールを追い越さない"""
        narrative = "ラピは声を出してはいけないのに、爪先立ちのままお尻を振り続けるのを止められない。"
        self._put_rule("s_edit", "mem_narr", narrative)
        ms.save_extracted_memories(
            "s_edit", "ラピ",
            [{"category": "rule",
              "content": "催眠により、ラピはアナルに指が入ると発情したメス犬になることが常識だと刷り込まれた。"}],
            turn_idx=5, mid="m5",
        )
        # 画面から一言だけ直した状況を再現（update_memory は source を manual に昇格する）
        ms.update_memory("s_edit", "mem_narr", content=narrative)
        edited = next(m for m in ms.load_memories("s_edit", "ラピ")["memories"] if m["id"] == "mem_narr")
        self.assertEqual("manual", edited.get("source"))
        self.assertEqual(0, ms.rule_standing_level(edited["content"], edited["source"]))

        hits = ms.search_memories("s_edit", "爪先立ちのままお尻を振り続けて声を出してはいけない", current_turn=6)
        rule_hits = [h for h in hits if h.get("category") == "rule"]
        self.assertIn("常識だと刷り込まれた", rule_hits[0]["content"])
        self.assertIn("止められない", rule_hits[1]["content"])

    def test_standing_rule_wins_rule_slot_over_matching_narration(self):
        """現在の場面に似ている地の文より、恒久ルールを rule 枠の先頭に置く"""
        self._put_rule("s_rule", "mem_narr",
                       "ラピは声を出してはいけないのに、爪先立ちのままお尻を振り続けるのを止められない。",
                       source="manual")
        ms.save_extracted_memories(
            "s_rule", "ラピ",
            [{"category": "rule",
              "content": "催眠により、ラピはアナルに指が入ると発情したメス犬になることが常識だと刷り込まれた。"}],
            turn_idx=5, mid="m5",
        )
        hits = ms.search_memories("s_rule", "爪先立ちのままお尻を振り続けて声を出してはいけない", current_turn=6)
        rule_hits = [h for h in hits if h.get("category") == "rule"]
        self.assertGreaterEqual(len(rule_hits), 2)
        self.assertIn("常識だと刷り込まれた", rule_hits[0]["content"])
        self.assertIn("止められない", rule_hits[1]["content"])

    def test_transient_sentence_not_extracted_as_rule(self):
        """「してはいけない」「暗示＋絶対」の地の文は rule 抽出しない（恒久ルールは拾う）"""
        def rules_of(asst_text):
            return [x for x in server.extract_memories_from_text("", asst_text, "ラピ", "指揮官")
                    if x["category"] == "rule"]

        self.assertEqual([], rules_of("声を出してはいけないのに、口元を押さえる指の隙間から甘い吐息が零れ落ちる。"))
        self.assertEqual([], rules_of("ラピは催眠で刷り込まれた絶対の服従心が身体を勝手に動かしてしまう。"))
        self.assertEqual(1, len(rules_of("催眠により、ラピは出迎えの際はI字バランスで挨拶することが常識だと刷り込まれた。")))

    def test_narrative_rule_is_saved_as_status(self):
        """AI が rule として出した情景描写は、保存時に status（TTLあり）へ降格される"""
        ms.save_extracted_memories(
            "s_demote", "ラピ",
            [{"category": "rule",
              "content": "ラピは声を出してはいけないのに、口元を押さえる指の隙間から甘い吐息が零れ落ちる。"}],
            turn_idx=5, mid="m5",
        )
        saved = ms.load_memories("s_demote", "ラピ")["memories"]
        self.assertEqual(1, len(saved))
        self.assertEqual("status", saved[0]["category"])


if __name__ == "__main__":
    unittest.main()
