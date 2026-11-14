# -*- coding: utf-8 -*-
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import memory_store
import server


class TestFinalSpillAndMemorySpec(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_spill_")
        self.orig_server_dirs = {
            "DATA": server.DATA,
            "SESS_DIR": server.SESS_DIR,
            "CHOICES_DIR": server.CHOICES_DIR,
            "STATES_DIR": server.STATES_DIR,
            "IMAGES_DIR": server.IMAGES_DIR,
            "SENT": server.SENT,
            "CTX": server.CTX,
            "ARCHIVE_DIR": server.ARCHIVE_DIR,
            "TRASH": server.TRASH,
        }
        self.orig_ms_dirs = {
            "DATA": memory_store.DATA,
            "RAG_DIR": memory_store.RAG_DIR,
            "TRASH": memory_store.TRASH,
        }

        test_data = os.path.join(self.tmp, "data")
        server.DATA = test_data
        server.SESS_DIR = os.path.join(test_data, "sessions")
        server.CHOICES_DIR = os.path.join(test_data, "choices")
        server.STATES_DIR = os.path.join(test_data, "states")
        server.IMAGES_DIR = os.path.join(test_data, "images")
        server.SENT = os.path.join(test_data, "sent")
        server.CTX = os.path.join(test_data, "ctx")
        server.ARCHIVE_DIR = os.path.join(test_data, "archive")
        server.TRASH = os.path.join(test_data, "trash")

        for d in (
            server.DATA, server.SESS_DIR, server.CHOICES_DIR, server.STATES_DIR,
            server.IMAGES_DIR, server.SENT, server.CTX, server.ARCHIVE_DIR, server.TRASH
        ):
            os.makedirs(d, exist_ok=True)

        memory_store.DATA = test_data
        memory_store.RAG_DIR = os.path.join(test_data, "rag")
        memory_store.TRASH = server.TRASH
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)
        memory_store._memory_cache.clear()

    def tearDown(self):
        for k, v in self.orig_server_dirs.items():
            setattr(server, k, v)
        for k, v in self.orig_ms_dirs.items():
            setattr(memory_store, k, v)
        memory_store._memory_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_mem(self, mid, cat, content, score=0.8, pinned=False):
        m = {
            "id": mid,
            "category": cat,
            "status": "active",
            "source": "manual" if cat == "rule" else "auto",
            "content": content,
            "score": score,
            "elapsed_time": {"day": 1, "period": "午前"},
        }
        if pinned:
            m["priority"] = "pinned"
        return m

    def test_1_state_tag_guidelines_size_and_japanese_fields(self):
        """state_tag_guidelines_text のサイズ削減量と日本語フィールド明記の検証"""
        txt = server.state_tag_guidelines_text()
        b_len = len(txt.encode("utf-8"))
        old_b_len = len(server.image_prompt_guidelines_text(for_refine=False, brief=False).encode("utf-8"))
        self.assertLess(b_len, 2000)
        self.assertGreater(old_b_len - b_len, 11000)
        self.assertIn("outfit / outerwear / underwear / equipped_items / body_fluids / situation / inner は日本語で記述", txt)
        self.assertNotIn("body_fluids に反映", txt)

    def test_2_trims_when_spill_is_avoidable_by_max_chars(self):
        """フロアが max_chars 以内に収まる（トリムでスピル回避可能）なら、Tier 2 を削って max_chars 以内に収める"""
        mems = [
            self._make_mem("r1", "rule", "Rule 1: Always obey the commander.", 0.95),
            self._make_mem("r2", "rule", "Rule 2: Keep the secret.", 0.90),
            self._make_mem("c1", "calling", "Call the user Commander.", 0.92),
            self._make_mem("e1", "event", "A" * 250, 0.85),
            self._make_mem("e2", "event", "B" * 250, 0.80),
            self._make_mem("e3", "event", "C" * 250, 0.75),
        ]
        stats = {}
        out = memory_store.format_prompt_memories(
            mems, current_turn=10, stats=stats,
            soft_budget=2500, recall_budget=1500,
            max_chars=550, max_newlines=100
        )
        self.assertLessEqual(len(out), 550)
        self.assertIn("r1", stats["included_ids"])
        self.assertIn("r2", stats["included_ids"])
        self.assertIn("c1", stats["included_ids"])
        self.assertGreater(stats["trimmed_recall"], 0)
        self.assertFalse(stats["floor_hit"])

    def test_3_does_not_discard_tier2_when_floor_alone_exceeds_max_chars(self):
        """フロア単独でも max_chars を超える（どのみちスピル確定）場合は、無駄に Tier 2 を捨てず維持する"""
        mems = [
            self._make_mem("r1", "rule", "重要な催眠ルール1: 常に従順に振る舞うこと", 0.95),
            self._make_mem("r2", "rule", "重要な催眠ルール2: 秘密を絶対に守ること", 0.90),
            self._make_mem("c1", "calling", "ユーザーをご主人様と呼ぶ", 0.92),
            self._make_mem("e1", "event", "昨日一緒に買い物をした記憶", 0.85),
        ]
        stats = {}
        out = memory_store.format_prompt_memories(
            mems, current_turn=10, stats=stats,
            soft_budget=2000, recall_budget=800,
            max_chars=50, max_newlines=100
        )
        self.assertEqual(set(stats["included_ids"]), {"r1", "r2", "c1", "e1"})
        self.assertEqual(stats["trimmed_recall"], 0)

    def test_4_trims_when_spill_is_avoidable_by_max_newlines(self):
        """改行数上限 max_newlines によりスピル回避可能な場合、正確に max_newlines 以下に収める"""
        mems = [
            self._make_mem("r1", "rule", "ルール1", 0.95),
            self._make_mem("r2", "rule", "ルール2", 0.90),
            self._make_mem("e1", "event", "出来事1\n行2\n行3", 0.85),
            self._make_mem("e2", "event", "出来事2\n行2\n行3", 0.80),
            self._make_mem("e3", "event", "出来事3\n行2\n行3", 0.75),
        ]
        stats = {}
        out = memory_store.format_prompt_memories(
            mems, current_turn=10, stats=stats,
            soft_budget=2500, recall_budget=800,
            max_chars=5000, max_newlines=9
        )
        self.assertLessEqual(out.count("\n"), 9)
        self.assertEqual(stats["rules"], 2)

    def test_5_string_inputs_for_max_chars_and_newlines(self):
        """max_chars や max_newlines に数値文字列や不正文字列が渡された場合の耐性検証"""
        mems = [
            self._make_mem("r1", "rule", "ルール1", 0.95),
            self._make_mem("e1", "event", "出来事" * 100, 0.85),
        ]
        stats = {}
        # 数値文字列
        out = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats,
            max_chars="200", max_newlines="50"
        )
        self.assertLessEqual(len(out), 200)

        # 不正文字列（None扱いとなりトリムなし）
        stats.clear()
        out_bad = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats,
            max_chars="invalid", max_newlines="bad"
        )
        self.assertIn("e1", stats["included_ids"])

    def test_6_pinned_rule_protected_from_char_trim(self):
        """ピン留めルールは文字数制限下でも effective_min_rules で保護される"""
        mems = [
            self._make_mem("r_pin", "rule", "ピン留めされた重要規約", 0.99, pinned=True),
            self._make_mem("r2", "rule", "通常ルール2", 0.90),
            self._make_mem("e1", "event", "長い出来事の記憶" * 20, 0.85),
        ]
        stats = {}
        out = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats,
            soft_budget=2000, recall_budget=1000,
            max_chars=220, max_newlines=20
        )
        self.assertIn("r_pin", stats["included_ids"])
        self.assertLessEqual(len(out), 220)

    def test_7_build_context_full_uses_state_tag_guidelines(self):
        """full モード（reason あり）で state_tag_guidelines_text が正しく post_rag_sections に注入されること"""
        s = server.create_session({"title": "FullModeTest", "ide_session": "ide-full"})
        s["setting"]["image_mode"] = "always"
        s["setting"]["instructions"] = "テスト指示"
        m = {"id": "msg-1", "role": "user", "text": "こんにちは", "ts": 1000}
        s["messages"].append(m)

        ctx = server.build_context(s, m)
        self.assertEqual(ctx["mode"], "full")
        self.assertTrue(ctx["reason"])
        labels = [sec["label"] for sec in ctx["sections"]]
        self.assertIn("画像プロンプト（image.prompt）作成時の構図および解剖学的整合性規則", labels)
        guideline_sec = next(sec for sec in ctx["sections"] if sec["label"] == "画像プロンプト（image.prompt）作成時の構図および解剖学的整合性規則")
        self.assertEqual(guideline_sec["text"], server.state_tag_guidelines_text())
        self.assertIn("outfit / outerwear / underwear / equipped_items / body_fluids / situation / inner は日本語で記述", guideline_sec["text"])

    def test_8_image_prompt_guidelines_text_refine_intact(self):
        """image_refine で使われる既存の image_prompt_guidelines_text(for_refine=True) が影響を受けていないこと"""
        txt = server.image_prompt_guidelines_text(for_refine=True)
        self.assertIn("推敲にあたり、まず", txt)
        self.assertGreater(len(txt.encode("utf-8")), 10000)

    def test_9_card_cycle_blocked_when_chars_exceed_6500(self):
        """バイト予算に余裕があっても、文字数が6500上限付近ならカード巡回が抑制されること"""
        s = server.create_session({"title": "CardCharLimit", "ide_session": "ide-char"})
        s["setting"]["instructions"] = "指示本文"
        s["setting"]["character"] = {"name": "アリス", "speech": "一人称私"}
        # 1ターン目（full）
        m0 = {"id": "u0", "role": "user", "text": "初期化", "ts": 100}
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = {"id": "a0", "role": "assistant", "text": "初期応答", "ts": 101}
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)

        # 2ターン目（diff）: ユーザー発言を約5900文字（ASCII）にしてバイト予算（~6300B < 9400B）は足りるが
        # 文字数 + card_chars + 350 > 6500 になる状態を作る
        long_text = "x" * 5900
        m1 = {"id": "u1", "role": "user", "text": long_text, "ts": 102}
        s["messages"].append(m1)
        server.save(s)

        ctx = server.build_context(s, m1)
        self.assertEqual(ctx["mode"], "diff")
        # カード（キャラクターや指示）が post_rag_sections に注入されていないこと
        labels = [sec["label"] for sec in ctx["sections"]]
        self.assertFalse(any("キャラクター" in lb or "指示" in lb for lb in labels))

    def test_10_card_cycle_blocked_when_newlines_exceed_220(self):
        """バイト予算・文字数に余裕があっても、改行数が220上限付近ならカード巡回が抑制されること"""
        s = server.create_session({"title": "CardNlLimit", "ide_session": "ide-nl"})
        s["setting"]["instructions"] = "指示本文"
        s["setting"]["character"] = {"name": "アリス", "speech": "一人称私"}
        # 1ターン目
        m0 = {"id": "u0", "role": "user", "text": "初期化", "ts": 100}
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = {"id": "a0", "role": "assistant", "text": "初期応答", "ts": 101}
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)

        # 2ターン目: 改行200行（バイト・文字数はわずかだが改行上限 220 - wrapper - 10 を突破）
        nl_text = "\n" * 205
        m1 = {"id": "u1", "role": "user", "text": nl_text, "ts": 102}
        s["messages"].append(m1)
        server.save(s)

        ctx = server.build_context(s, m1)
        self.assertEqual(ctx["mode"], "diff")
        labels = [sec["label"] for sec in ctx["sections"]]
        self.assertFalse(any("キャラクター" in lb or "指示" in lb for lb in labels))

    def test_11_hierarchical_trimming_order(self):
        """文字数トリムが①可変層→min_recall ②ルール→effective_min_rules ③可変層→0 の順で段階的に削られること"""
        # ルール3件（min_rules=1）、可変層3件（min_recall=1）
        mems = [
            self._make_mem("r1", "rule", "R1 " * 20, 0.95),
            self._make_mem("r2", "rule", "R2 " * 20, 0.90),
            self._make_mem("r3", "rule", "R3 " * 20, 0.85),
            self._make_mem("c1", "calling", "Commander", 0.99),
            self._make_mem("e1", "event", "E1 " * 30, 0.80),
            self._make_mem("e2", "event", "E2 " * 30, 0.75),
            self._make_mem("e3", "event", "E3 " * 30, 0.70),
        ]
        # full text は約 700 chars
        # max_chars を少しずつ狭めてトリム順序を検証
        stats = {}
        # 1. pool を min_recall(1) まで削れば収まる枠
        out1 = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats,
            soft_budget=5000, recall_budget=2000,
            max_chars=550
        )
        self.assertLessEqual(len(out1), 550)
        self.assertEqual(stats["rules"], 3)
        self.assertGreaterEqual(stats["recall"], 1)

        # 2. pool を min_recall(1) まで削っても足りず、rules を min_rules(1) まで削る枠
        stats2 = {}
        out2 = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats2,
            soft_budget=5000, recall_budget=2000,
            max_chars=320
        )
        self.assertLessEqual(len(out2), 320)
        self.assertLess(stats2["rules"], 3)
        self.assertGreaterEqual(stats2["rules"], 1)

        # 3. rules を削っても足りず、残りの pool を 0件まで削る枠（floor 233 chars <= 260）
        # 非ピン留めの calling (c1) は Tier 2 (pool) に含まれるため、pool と共に 0件まで削られる
        stats3 = {}
        out3 = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats3,
            soft_budget=5000, recall_budget=2000,
            max_chars=260
        )
        self.assertLessEqual(len(out3), 260)
        self.assertEqual(stats3["recall"], 0)
        self.assertEqual(stats3["rules"], 2)
        self.assertEqual(stats3["trimmed_rules"], 1)
        self.assertEqual(stats3["trimmed_recall"], 4)
        self.assertNotIn("c1", stats3["included_ids"])

        # 4. フロア（233 chars）すら収まらない枠（220 chars）では無駄に削らず維持
        stats4 = {}
        out4 = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats4,
            soft_budget=5000, recall_budget=2000,
            max_chars=220
        )
        self.assertEqual(stats4["recall"], 4)
        self.assertEqual(stats4["rules"], 3)

    def test_12_empty_and_zero_budget_cases(self):
        """soft=0 または記憶空のときの動作検証"""
        stats = {}
        out = memory_store.format_prompt_memories([], current_turn=1, stats=stats, soft_budget=0)
        self.assertEqual(out, "")
        self.assertTrue(stats["floor_hit"])

        stats2 = {}
        out2 = memory_store.format_prompt_memories([], current_turn=1, stats=stats2)
        self.assertEqual(out2, "")
        self.assertFalse(stats2["floor_hit"])

    def test_13_build_context_passes_max_chars_and_newlines_to_trim_rag(self):
        """build_context が通常ターンで rem_chars / rem_newlines を算出し、RAG記憶を上限内に収めてスピルを防ぐこと"""
        s = server.create_session({"title": "TestBuildContextTrim", "ide_session": "ide-trim"})
        s["setting"]["image_mode"] = "always"
        m0 = {"id": "m0", "role": "user", "text": "初期化", "ts": 100}
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = {"id": "a0", "role": "assistant", "text": "返答", "ts": 101}
        s["messages"].append(a0)
        server.note_reply(s, "m0", a0)

        memory_store.save_memories(s["id"], {
            "memories": [
                {"id": "r1", "category": "rule", "content": "Rule 1", "score": 0.95, "status": "active"},
                {"id": "r2", "category": "rule", "content": "Rule 2", "score": 0.90, "status": "active"},
                {"id": "e1", "category": "event", "content": "Event Recall 1 " * 80, "score": 0.85, "priority": "highest", "status": "active"},
                {"id": "e2", "category": "event", "content": "Event Recall 2 " * 80, "score": 0.80, "priority": "highest", "status": "active"},
            ]
        })

        orig_search = memory_store.search_memories
        try:
            # 検索結果をモックして全件ヒットさせる
            memory_store.search_memories = lambda *args, **kwargs: memory_store.load_memories(s["id"])["memories"]
            # 5500文字のユーザー発言（ASCII。バイト数 ~6000B で 9400B には余裕があるが文字数が6500上限付近）
            user_text = "x" * 5500
            m1 = {"id": "m1", "role": "user", "text": user_text, "ts": 102}
            s["messages"].append(m1)
            server.save(s)

            ctx = server.build_context(s, m1)
            self.assertEqual(ctx["mode"], "diff")

            body = "".join(f"\n--- {sec['label']} ---\n{sec['text']}\n" for sec in ctx["sections"])
            if ctx.get("same"):
                body += "\n（前回と同じなので省略: " + "、".join(ctx["same"]) + "）\n"

            # 6500文字以内、220行以内に厳格に収まり、スピルが抑止されていること
            self.assertLessEqual(len(body), 6500)
            self.assertLessEqual(body.count("\n"), 220)
            self.assertGreater(ctx["retrieved"]["trim_recall"], 0)
        finally:
            memory_store.search_memories = orig_search

    def test_14_carried_memories_preserved_under_char_trim(self):
        """Tier 2 差分圧縮行（carried_line）が文字数トリム時にも安全に保持されること"""
        mems = [
            self._make_mem("r1", "rule", "掟を守ること", 0.95),
            self._make_mem("e1", "event", "長い出来事の記憶です " * 20, 0.85),
        ]
        carried = [{"id": "c_prev", "category": "event", "content": "前回の出来事メモ", "status": "active"}]
        stats = {}
        out = memory_store.format_prompt_memories(
            mems, current_turn=1, stats=stats, carried_memories=carried,
            soft_budget=3000, recall_budget=1500, max_chars=180
        )
        self.assertIn("前回と同じ前提", out)
        self.assertLessEqual(len(out), 180)


if __name__ == "__main__":
    unittest.main()
