# -*- coding: utf-8 -*-
"""RAG「呼称・口調（calling）」カテゴリー Tier 2 移行・抑制 仕様書の受け入れ条件テスト"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import memory_store as ms
import server


class TestRagCallingTier2(unittest.TestCase):
    """docs/rag-calling-tier2-spec.md の全受け入れ条件および詳細仕様のテスト"""

    def setUp(self):
        self.orig_env = os.environ.copy()
        self.test_dir = tempfile.mkdtemp(prefix="test_calling_tier2_")
        self.orig_rag_dir = ms.RAG_DIR
        ms.RAG_DIR = os.path.join(self.test_dir, "rag")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        self.sid = "test_sid_calling"

    def tearDown(self):
        ms.RAG_DIR = self.orig_rag_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self.orig_env)

    # --- 条件 1: ピン留めしていない calling はスコア 0.53 未満で除外 ---
    def test_unpinned_calling_filtered_below_min_score(self):
        """ピン留めしていない calling はスコア 0.53 未満なら search_memories で返されない"""
        ms.add_manual_memory(self.sid, "キャラ", "calling", "ユーザーをマスターと呼ぶ")
        ms.add_manual_memory(self.sid, "キャラ", "rule", "部屋の門限ルールを守ること")

        # "部屋の門限ルール" で検索（calling にはキーワードボーナスも類似度もない）
        res = ms.search_memories(self.sid, "部屋の門限ルール")
        res_cats = [m.get("category") for m in res]
        self.assertNotIn("calling", res_cats)
        self.assertIn("rule", res_cats)

    def test_calling_score_boost_removed(self):
        """calling の CATEGORY_SCORE_BOOST が廃止され、不要な下駄を履かない"""
        self.assertNotIn("calling", ms.CATEGORY_SCORE_BOOST)

    # --- 条件 2: ピン留め calling は Tier 1 の4枠に入り、予算超過時も保護 ---
    def test_pinned_calling_in_tier1_and_protected_from_budget_trim(self):
        """ピン留め calling は Tier 1 に入り、soft_budget 逼迫時でもトリムされない"""
        memories = [
            {
                "id": "c_pinned",
                "category": "calling",
                "status": "active",
                "priority": "pinned",
                "content": "ご主人様と呼ぶ",
                "score": 0.3,  # 低スコアでもピン留めなら Tier 1
            },
            {
                "id": "e_long_1",
                "category": "event",
                "status": "active",
                "content": "非常に長いイベント記憶その1" * 15,
                "score": 0.9,
            },
            {
                "id": "e_long_2",
                "category": "event",
                "status": "active",
                "content": "非常に長いイベント記憶その2" * 15,
                "score": 0.85,
            }
        ]
        stats = {}
        # 予算を小さく絞ってイベントをトリムさせる
        text = ms.format_prompt_memories(memories, current_turn=10, stats=stats, soft_budget=300)
        self.assertEqual(1, stats["calling"])
        self.assertEqual(1, stats["tier1"])
        self.assertIn("c_pinned", stats["included_ids"])
        self.assertIn("ご主人様と呼ぶ", text)
        self.assertGreater(stats["trimmed_recall"], 0)

    # --- 条件 3: ピン留めが6件以上ある場合、溢れた分は Tier 2 で救済（スコア順） ---
    def test_pinned_overflow_rescued_in_tier2_in_score_order(self):
        """ピン留めが6件以上ある場合、Tier 1 から溢れた分は Tier 2 で救済され、スコア順に配置される"""
        for i in range(6):
            cat = "calling" if i == 5 else "rule"
            ms.add_manual_memory(self.sid, "キャラ", cat, f"ピン留め記憶_{i}", priority="pinned")

        res = ms.search_memories(self.sid, "ピン留め記憶", limit=10)
        self.assertEqual(6, len(res))
        tier1_list = [m for m in res if m.get("_tier") == 1]
        tier2_list = [m for m in res if m.get("_tier") == 2]
        self.assertEqual(5, len(tier1_list))
        self.assertEqual(1, len(tier2_list))
        # 6件目も救済されて結果に含まれる
        self.assertEqual(6, len(set(m["id"] for m in res)))

        # format_prompt_memories においても溢れたピン留め記憶は「Tier 2 の先頭」ではなくスコア順に配置される
        mems = [
            {"id": "r1", "category": "rule", "status": "active", "priority": "pinned", "content": "R1", "score": 0.90},
            {"id": "r2", "category": "rule", "status": "active", "priority": "pinned", "content": "R2", "score": 0.80},
            {"id": "r3", "category": "rule", "status": "active", "priority": "pinned", "content": "R3", "score": 0.70},
            {"id": "r4", "category": "rule", "status": "active", "priority": "pinned", "content": "R4", "score": 0.60},
            {"id": "r5", "category": "rule", "status": "active", "priority": "pinned", "content": "R5", "score": 0.50},
            {"id": "c6_pinned", "category": "calling", "status": "active", "priority": "pinned", "content": "C6_PIN", "score": 0.20},
            {"id": "e7_high", "category": "event", "status": "active", "content": "E7_HIGH", "score": 0.85},
        ]
        stats = {}
        ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        included = stats["included_ids"]
        self.assertEqual(["r1", "r2", "r3", "r4", "r5"], included[:5])
        # Tier 2 内ではスコア降順（e7_high: 0.85 が c6_pinned: 0.20 より前）
        self.assertEqual(["e7_high", "c6_pinned"], included[5:])

        # 予算トリム時: Tier 2 内の低スコアな c6_pinned が e7_high より先にトリムされる
        stats_trim = {}
        ms.format_prompt_memories(mems, current_turn=1, stats=stats_trim, soft_budget=600)
        self.assertIn("e7_high", stats_trim["included_ids"])
        self.assertNotIn("c6_pinned", stats_trim["included_ids"])
        self.assertGreater(stats_trim["trimmed_recall"], 0)

    # --- 条件 4: calling の low は最終スコア 0.60 未満では注入されない ---
    def test_calling_low_priority_threshold_060(self):
        """抑制(low)の calling は、スコア×0.82 後の最終スコアが 0.60 未満なら想起されない"""
        mem = {"id": "c_low", "category": "calling", "priority": "low"}
        p_info = ms.memory_priority_info(mem)
        self.assertEqual(0.60, p_info["min_score"])
        self.assertEqual(0.82, p_info["mult"])

    # --- 条件 5: fulfilled の calling は Tier 1 / Tier 2 / pool に出ない ---
    def test_fulfilled_calling_completely_excluded(self):
        """fulfilled の calling は Tier 1 / Tier 2 のどこにも想起されない"""
        ms.add_manual_memory(self.sid, "キャラ", "calling", "旧呼称: 坊っちゃんとお呼びする", status="fulfilled")
        ms.add_manual_memory(self.sid, "キャラ", "calling", "新呼称: 先生とお呼びする", status="active")

        res = ms.search_memories(self.sid, "とお呼びする 呼び方")
        res_contents = [m.get("content", "") for m in res]
        self.assertFalse(any("坊っちゃん" in c for c in res_contents))
        self.assertTrue(any("先生" in c for c in res_contents))

        # format_prompt_memories でも pool に入らない
        memories = [
            {"id": "c_ful", "category": "calling", "status": "fulfilled", "content": "旧呼称: 坊っちゃん", "score": 0.9},
            {"id": "c_act", "category": "calling", "status": "active", "content": "新呼称: 先生", "score": 0.9},
        ]
        stats = {}
        text = ms.format_prompt_memories(memories, current_turn=1, stats=stats)
        self.assertNotIn("c_ful", stats["included_ids"])
        self.assertNotIn("坊っちゃん", text)
        self.assertIn("c_act", stats["included_ids"])

    def test_fulfilled_calling_not_merged_on_extract(self):
        """save_extracted_memories で fulfilled の calling にはマージされず新規追加される"""
        memories = [
            {
                "id": "c_old_fulfilled",
                "category": "calling",
                "status": "fulfilled",
                "source": "auto",
                "content": "先輩と呼ぶ",
                "tokens": ["先輩", "呼ぶ"],
                "embedding": [0.99] * 128,
            }
        ]
        ms.save_memories(self.sid, {"session_id": self.sid, "memories": memories})

        # 同じ文脈の抽出を試みる
        extracted = [{"category": "calling", "content": "先輩と呼ぶ"}]
        data = ms.save_extracted_memories(self.sid, "キャラ", extracted, turn_idx=2)
        # fulfilled にマージされず、新規追加（1件）されるため合計2件になる
        all_mems = data.get("memories", [])
        self.assertEqual(2, len(all_mems))
        # 古いものは fulfilled のまま
        old_mem = next(m for m in all_mems if m["id"] == "c_old_fulfilled")
        self.assertEqual("fulfilled", old_mem["status"])
        # 新しいものは active
        new_mem = next(m for m in all_mems if m["id"] != "c_old_fulfilled")
        self.assertEqual("active", new_mem["status"])

    # --- 条件 6: rule の Tier 1 配置ロジック維持（level 0 も空き枠があれば入る） ---
    def test_rule_level_0_enters_tier1_if_capacity_permits(self):
        """rule_standing_level が 0 の rule でも他に候補が少なければ Tier 1 に入る"""
        ms.add_manual_memory(self.sid, "キャラ", "rule", "今だけ静かにするルール")
        res = ms.search_memories(self.sid, "静かにするルール", limit=5)
        self.assertEqual(1, len(res))
        self.assertEqual(1, res[0]["_tier"])

    # --- 条件 7: "名前で呼んで" などが抽出・クエリボーナスの両方で calling 扱い ---
    def test_calling_query_bonus_keywords(self):
        """CALLING_KEYWORDS の語を含むクエリで calling 記憶にボーナスが付く"""
        bonus_kw = "名前で呼んで"
        tokens, _ = ms.get_query_features(bonus_kw)
        mem = {"id": "c1", "category": "calling", "content": "お兄ちゃんと呼ぶ", "tokens": ["お兄ちゃん", "呼ぶ"]}
        score = ms.compute_memory_score(mem, tokens, [], bonus_kw)
        # クエリボーナス +0.15 が適用されている
        self.assertGreaterEqual(score, 0.15)

    def test_calling_keywords_server_sync(self):
        """CALLING_KEYWORDS に必要なキーワードがすべて揃っていること"""
        required = [
            "呼び方", "呼び名", "呼称", "名前で呼", "名前を呼",
            "って呼", "と呼", "とお呼び", "ってお呼び",
            "呼び捨て", "あだ名", "敬語", "タメ口",
            "口調", "一人称", "二人称",
            "さん付け", "ちゃん付け", "くん付け", "様付け"
        ]
        for kw in required:
            self.assertIn(kw, ms.CALLING_KEYWORDS)

    # --- 条件 8: 招致表現（部屋にお呼びしました、医者をお呼びします）が誤抽出されない ---
    def test_summoning_expressions_not_extracted_as_calling(self):
        """「部屋にお呼びしました」「医者をお呼びします」は calling として抽出されない"""
        extracted = server.extract_memories_from_text(
            user_text="部屋にお呼びしました。お兄ちゃん、ご飯ですよ。",
            asst_text="医者をお呼びしますね。すぐ人を呼びます。"
        )
        calling_extracted = [e for e in extracted if e["category"] == "calling"]
        self.assertEqual(0, len(calling_extracted))

    def test_legitimate_calling_expressions_extracted(self):
        """「名前で呼んで」「マスターとお呼びします」は calling として抽出される"""
        extracted = server.extract_memories_from_text(
            user_text="私のことは名前で呼んでね。",
            asst_text="かしこまりました、これからはマスターとお呼びします。"
        )
        calling_extracted = [e for e in extracted if e["category"] == "calling"]
        self.assertGreaterEqual(len(calling_extracted), 2)
        contents = [e["content"] for e in calling_extracted]
        self.assertTrue(any("名前で呼んで" in c for c in contents))
        self.assertTrue(any("とお呼び" in c for c in contents))

    # --- 条件 9: Compact モード時でも ID: mem_... が付与される ---
    def test_compact_mode_retains_calling_id(self):
        """Compact モードでも calling 記憶に ID が付与される"""
        os.environ["RPCHAT_COMPACT"] = "1"
        memories = [
            {"id": "mem_call_123", "category": "calling", "content": "マスターと呼ぶ", "score": 0.8}
        ]
        stats = {}
        text = ms.format_prompt_memories(memories, current_turn=1, stats=stats)
        self.assertIn("ID: mem_call_123", text)
        self.assertIn("mem_call_123", stats["included_ids"])

    # --- 条件 10: 直近2ターンの auto 記憶除外が calling にも適用される ---
    def test_recent_auto_calling_excluded(self):
        """直近2ターンの auto calling（非ピン留め・非優先）は検索除外される"""
        # 手動で auto 記憶を保存
        data = {
            "session_id": self.sid,
            "memories": [
                {
                    "id": "c_auto_recent",
                    "category": "calling",
                    "status": "active",
                    "source": "auto",
                    "turn_idx": 9,  # current_turn=10 なので 1 ターン前
                    "content": "先輩と呼ぶことにする",
                },
                {
                    "id": "r_auto_recent",
                    "category": "rule",
                    "status": "active",
                    "source": "auto",
                    "turn_idx": 9,
                    "content": "部屋に入らないルール",
                }
            ]
        }
        ms.save_memories(self.sid, data)

        # current_turn=10 で検索
        res = ms.search_memories(self.sid, "先輩と呼ぶ ルール", current_turn=10)
        res_ids = [m["id"] for m in res]
        # calling は直近2ターン除外される
        self.assertNotIn("c_auto_recent", res_ids)
        # rule は直近2ターンでも除外免除される
        self.assertIn("r_auto_recent", res_ids)

    # --- Tier 2 内の calling 上限（GROUP_CAPS['calling'] = 2）---
    def test_tier2_calling_group_cap_is_two_independent_of_tier1(self):
        """Tier 1 にピン留め calling があっても、Tier 2 で calling は最大2件想起できる"""
        ms.add_manual_memory(self.sid, "キャラ", "calling", "ピン留め呼称: マスターとお呼びする", priority="pinned")
        ms.add_manual_memory(self.sid, "キャラ", "calling", "想起呼称1: 先生とお呼びする")
        ms.add_manual_memory(self.sid, "キャラ", "calling", "想起呼称2: 先輩とお呼びする")
        ms.add_manual_memory(self.sid, "キャラ", "calling", "想起呼称3: 指揮官とお呼びする")

        res = ms.search_memories(self.sid, "とお呼びする 呼び方 呼称", limit=10)
        # Tier 1 にピン留め呼称
        tier1 = [m for m in res if m.get("_tier") == 1]
        tier2 = [m for m in res if m.get("_tier") == 2]
        self.assertEqual(1, len(tier1))
        self.assertIn("ピン留め呼称: マスターとお呼びする", tier1[0]["content"])
        # Tier 2 には最大2件の calling
        tier2_callings = [m for m in tier2 if m.get("category") == "calling"]
        self.assertEqual(2, len(tier2_callings))

    # --- エッジケース 1: 4件のピン留め記憶が存在する状態での resolved_ids 呼称更新 ---
    def test_transition_calling_with_resolved_ids_and_pinned_rules(self):
        """ピン留めが4件ある状態で旧呼称が resolved_ids で解消され新呼称が抽出された場合、旧呼称は完全除外され新呼称が Tier 2 に入る"""
        for i in range(4):
            ms.add_manual_memory(self.sid, "キャラ", "rule", f"固定規則_{i}", priority="pinned")

        data1 = ms.save_extracted_memories(
            self.sid, "キャラ",
            [{"category": "calling", "content": "旧呼称: 坊っちゃんと呼ぶ"}],
            turn_idx=1
        )
        old_id = data1["memories"][-1]["id"]

        data2 = ms.save_extracted_memories(
            self.sid, "キャラ",
            [{"category": "calling", "content": "新呼称: 先生とお呼びする"}],
            resolved_ids=[old_id],
            turn_idx=2
        )

        res = ms.search_memories(self.sid, "先生とお呼びする 呼び方", current_turn=5)
        t1 = [m for m in res if m.get("_tier") == 1]
        t2 = [m for m in res if m.get("_tier") == 2]

        self.assertEqual(4, len(t1))
        self.assertFalse(any("坊っちゃん" in m["content"] for m in res))
        self.assertTrue(any("先生とお呼びする" in m["content"] for m in t2))

    # --- エッジケース 2: Tier 1 がピン留め calling のみで rules が 0件の場合 ---
    def test_tier1_only_calling_stats_and_header(self):
        """Tier 1 にピン留め calling のみ存在する場合、stats['rules']==0, stats['calling']==1, stats['tier1']==1 となりヘッダーが出力される"""
        ms.add_manual_memory(self.sid, "キャラ", "calling", "マスターとお呼びする", priority="pinned")
        res = ms.search_memories(self.sid, "マスターとお呼びする 呼び方", current_turn=1)
        t1 = [m for m in res if m.get("_tier") == 1]
        self.assertEqual(1, len(t1))

        stats = {}
        text = ms.format_prompt_memories(res, current_turn=1, stats=stats)
        self.assertEqual(1, stats["tier1"])
        self.assertEqual(0, stats["rules"])
        self.assertEqual(1, stats["calling"])
        self.assertEqual(0, stats["recall"])
        self.assertIn("【作中の前提・制約（常時確認・遵守事項）】", text)
        self.assertIn("マスターとお呼びする", text)


if __name__ == "__main__":
    unittest.main()
