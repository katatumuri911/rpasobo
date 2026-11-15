# -*- coding: utf-8 -*-
"""relationship（関係性の変化）の誤爆防止テスト

観点:
- 「恋人のように」「恋人のふり」「ごっこ」等の比喩・演技から relationship を作らない
- 「大好き」「特別」等のその場の感情の叫びだけでは relationship を作らない
- 「恋人同士になった」「告白して付き合った」等の確定した変化は従来どおり記録する
- 保存経路（save_extracted_memories）と想起経路（search_memories）の両方で効く
- 手動追加した relationship は想起時に消さない
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import memory_extract as me
import memory_store as ms

SID = "relguard"


class TestRelationshipGuardUnit(unittest.TestCase):
    """should_skip_relationship_extraction の判定"""

    def test_simile_and_pretend_are_skipped(self):
        for text in (
            "アクアは、まるで恋人のようにカズマに寄り添った。",
            "アクアは恋人のふりをしてカズマの腕に抱きついた。",
            "二人は恋人ごっこをして遊んだ。",
            "付き合うことになったみたいだね。",
            "もし恋人だったらなあ。",
        ):
            self.assertTrue(ms.should_skip_relationship_extraction(text), text)

    def test_emotion_only_is_skipped(self):
        for text in (
            "アクアはカズマのことが大好きだと自覚した。",
            "ラピは指揮官の特別なご命令に従う。",
            "私、もっともっと指揮官様のことが大好きになっちゃいますぅ……っ",
            "ラピは指揮官への服従と愛着を深めた。",
        ):
            self.assertTrue(ms.should_skip_relationship_extraction(text), text)

    def test_negative_and_wish_are_skipped(self):
        for text in (
            "二人は付き合っていない。",
            "二人の関係は変わらない。",
            "アクアはカズマに告白するつもりだ。",
            "アクアは特別な関係になりたいと思った。",
            "二人で付き合おう。",
            "アクアは「付き合ってください」と言った。",
            "恋人になれるかな？",
        ):
            self.assertTrue(ms.should_skip_relationship_extraction(text), text)

    def test_confirmed_change_is_kept(self):
        for text in (
            "アクアはカズマと恋人同士になった。",
            "アクアはカズマに告白して付き合うことになった。",
            "ラピと指揮官は仲直りした。",
            "アクアはカズマと別れた。",
            "アクアはカズマに「大好きだ」と告白して付き合った。",
        ):
            self.assertFalse(ms.should_skip_relationship_extraction(text), text)

    def test_narration_copy_detection(self):
        source = "アクアは嬉しそうに目を輝かせ、カズマの大きな手のひらへちょこんと手を重ねた。"
        # 地の文の丸写し → コピー
        self.assertTrue(ms.is_narration_copy(
            "アクアは嬉しそうに目を輝かせ、カズマの大きな手のひらへちょこんと手を重ねた", source))
        # 要約された三人称事実文 → コピーではない
        self.assertFalse(ms.is_narration_copy("アクアはカズマと恋人同士になった", source))
        # 短い記憶はコピー判定しない
        self.assertFalse(ms.is_narration_copy("恋人同士", source))


class TestRelationshipGuardExtraction(unittest.TestCase):
    """キーワード抽出（フォールバック）が relationship を作らないこと"""

    def _rel(self, asst_text="", user_text=""):
        return [m for m in me.extract_memories_from_text(
            user_text=user_text, asst_text=asst_text,
            char_name="アクア", user_name="カズマ",
        ) if m["category"] == "relationship"]

    def test_simile_line_produces_no_relationship(self):
        self.assertEqual(self._rel(asst_text="アクアは恋人のようにカズマに微笑んだ。"), [])

    def test_emotion_line_produces_no_relationship(self):
        self.assertEqual(self._rel(asst_text="アクアは「あなたのこと、大好きになっちゃいそう」と言った。"), [])

    def test_narration_never_produces_relationship(self):
        """地の文（キャラ側の本文）からは relationship を作らない（B）"""
        self.assertEqual(self._rel(asst_text="アクアはカズマと恋人同士になった。"), [])
        self.assertEqual(self._rel(asst_text="アクアはカズマと付き合うことになった。"), [])

    def test_user_statement_produces_relationship(self):
        rel = self._rel(user_text="実は昨日、アクアと付き合うことになったんだ。")
        self.assertEqual(len(rel), 1)
        self.assertIn("付き合うことになった", rel[0]["content"])

    def test_user_proposal_produces_no_relationship(self):
        self.assertEqual(self._rel(user_text="アクア、俺と付き合おう。"), [])


class TestRelationshipGuardStore(unittest.TestCase):
    """保存・想起の両経路でノイズ relationship が混ざらないこと"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_rel_guard_")
        self.orig = (ms.DATA, ms.RAG_DIR, ms.TRASH)
        ms.DATA = self.tmp
        ms.RAG_DIR = os.path.join(self.tmp, "rag")
        ms.TRASH = os.path.join(self.tmp, "trash")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()
        self._orig_embed = (ms.get_embeddings_batch, ms.get_embedding)
        ms.get_embeddings_batch = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        ms.get_embedding = lambda text: [1.0, 0.0, 0.0]

    def tearDown(self):
        ms.get_embeddings_batch, ms.get_embedding = self._orig_embed
        ms.DATA, ms.RAG_DIR, ms.TRASH = self.orig
        ms._memory_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self, items):
        return ms.save_extracted_memories(
            SID, "アクア", items, turn_idx=5, mid="m5",
            elapsed_time={"day": 1, "period": "午前"}, max_new=5,
        ).get("memories", [])

    def test_simile_not_saved(self):
        mems = self._save([{"category": "relationship",
                            "content": "アクアは恋人のふりをしてカズマにキスした"}])
        self.assertEqual(mems, [])

    def test_confirmed_change_saved(self):
        mems = self._save([{"category": "relationship",
                            "content": "アクアはカズマと恋人同士になった"}])
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["category"], "relationship")

    def test_narrated_copy_dropped(self):
        """モデルが地の文を丸写しした relationship はキュー投入前に落とす（C）"""
        narration = "アクアは嬉しそうに目を輝かせ、カズマの大きな手のひらへちょこんと手を重ねた。"
        items = [{"category": "relationship", "content": narration},
                 {"category": "relationship", "content": "アクアはカズマと恋人同士になった"},
                 {"category": "event", "content": narration}]
        out = ms.drop_narrated_relationship(items, narration)
        self.assertEqual([x["category"] for x in out], ["relationship", "event"])
        self.assertIn("恋人同士", out[0]["content"])

    def test_legacy_noise_not_recalled(self):
        """旧バージョンが作った誤爆 relationship は想起対象から外れる（データは残す）"""
        data = ms.load_memories(SID)
        data["memories"] = [{
            "id": "mem_old_noise", "session_id": SID, "turn_idx": 1, "mid": None,
            "elapsed_time": {"day": 1, "period": "午前"},
            "source": "auto", "category": "relationship", "status": "active",
            "content": "アクアは大好きな恋人に愛撫されているみたいに胸がきゅんと満たされた",
            "history": [], "content_hash": "h", "created_at": 1, "updated_at": 1,
            "tokens": ["恋人", "大好き"], "embedding": [1.0, 0.0, 0.0],
        }]
        ms.save_memories(SID, data)

        hits = ms.search_memories(SID, "恋人の話をしよう", current_turn=10, limit=10)
        self.assertEqual([h for h in hits if h.get("category") == "relationship"], [])
        # データ自体は消さない
        self.assertEqual(len(ms.load_memories(SID).get("memories", [])), 1)

    def test_manual_relationship_not_recalled_filtered(self):
        ms.add_manual_memory(SID, "アクア", "relationship", "アクアはカズマのことが大好き")
        hits = ms.search_memories(SID, "大好き 恋人", current_turn=10, limit=10)
        self.assertEqual(len([h for h in hits if h.get("category") == "relationship"]), 1)

    def test_relationship_score_is_debuffed(self):
        """同じ類似度・同じキーワード一致なら relationship は他カテゴリより低いスコアになる"""
        q_tokens = ["恋人", "付き合う"]
        q_vec = [1.0, 0.0, 0.0]
        base = {
            "id": "x", "status": "active", "source": "auto", "turn_idx": 5,
            "tokens": q_tokens, "embedding": q_vec,
        }
        rel = dict(base, category="relationship", content="アクアはカズマと恋人同士になった")
        ev = dict(base, category="event", content="アクアはカズマと恋人同士になった")
        # クエリに relationship 語が無い時（＝感情語の一致だけで浮上する場面）に差が出る
        s_rel = ms.compute_memory_score(rel, q_tokens, q_vec, "キス 抱きしめる", current_turn=10)
        s_ev = ms.compute_memory_score(ev, q_tokens, q_vec, "キス 抱きしめる", current_turn=10)
        self.assertLess(s_rel, s_ev)
        # クエリが relationship の話題なら bonus で従来どおり想起され得る
        s_rel_q = ms.compute_memory_score(rel, q_tokens, q_vec, "恋人 付き合う", current_turn=10)
        self.assertGreaterEqual(s_rel_q, s_ev)


if __name__ == "__main__":
    unittest.main()
