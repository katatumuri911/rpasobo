# -*- coding: utf-8 -*-
"""memory_extract スリム化のユニットテスト (docs/memory-extract-slim-spec.md に準拠)"""
import json
import os
import re
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server


class TestMemoryExtractSlim(unittest.TestCase):
    """build_memory_extract_context のスリム化検証"""

    def setUp(self):
        self.session = {
            "id": "test_session_12345",
            "setting": {
                "title": "テスト会話",
                "user_name": "ナオキ",
                "character": {
                    "name": "アリス",
                },
            },
            "messages": [
                {
                    "id": "msg_user_001",
                    "role": "user",
                    "text": "アリス、今度の日曜日に新しくできた駅前の水族館に行かない？",
                    "ts": "2026-11-10T10:00:00",
                },
                {
                    "id": "msg_asst_001",
                    "role": "assistant",
                    "text": "ええ、喜んで！日曜日ね、約束だよ。イルカショーも一緒に見ようね。",
                    "ts": "2026-11-10T10:00:05",
                },
            ],
        }
        self.asst_msg = self.session["messages"][1]

    def test_past_memories_not_loaded_and_not_in_sections(self):
        """過去記憶の読み込みが完全撤廃され、セクションにも含まれないこと"""
        with patch("memory_store.load_memories") as mock_load:
            ctx = server.build_memory_extract_context(self.session, self.asst_msg)
            # memory_store.load_memories は一切呼ばれないこと
            mock_load.assert_not_called()

        labels = [sec["label"] for sec in ctx["sections"]]
        # 過去記憶の一覧セクションが存在しないこと
        for label in labels:
            self.assertNotIn("有効な約束", label)
            self.assertNotIn("resolved_ids", label)

        # セクションは「指示」「会話」「応答形式」の3点のみ
        self.assertEqual(len(labels), 3)
        self.assertEqual(labels[0], "長期記憶の要約・抽出タスク（指示）")
        self.assertEqual(labels[1], "直前の会話（抽出対象）")
        self.assertEqual(labels[2], "応答の書き方（outbox の JSON）")

    def test_payload_size_under_limit(self):
        """全体のコンテキストサイズが 3,500B 未満であること（実測 2,200B〜2,800B を想定）"""
        ctx = server.build_memory_extract_context(self.session, self.asst_msg)
        raw_json = json.dumps(ctx, ensure_ascii=False)
        utf8_bytes = len(raw_json.encode("utf-8"))
        char_count = len(raw_json)

        # 3,500B 未満の安全マージン確認 (LONG_BYTES = 9,400B に対し十分小さく、2,200B〜2,800B の安全圏)
        self.assertLess(utf8_bytes, 3500, f"UTF-8 bytes exceeded 3,500B: {utf8_bytes}B")
        self.assertGreater(utf8_bytes, 1800, f"UTF-8 bytes unexpectedly small: {utf8_bytes}B")
        self.assertLess(char_count, 3500, f"Character count exceeded 3,500: {char_count}")

        # 指示文単体の文字数・バイト数がスリム化されていること（旧仕様 約3,340B から大幅削減）
        inst_text = ctx["sections"][0]["text"]
        inst_bytes = len(inst_text.encode("utf-8"))
        self.assertLess(inst_bytes, 1500, f"Instruction bytes too large: {inst_bytes}B")
        self.assertLess(len(inst_text), 600, f"Instruction char count too large: {len(inst_text)}")

    def test_payload_size_with_typical_exchange(self):
        """標準的な会話長（ユーザー100文字・返答200文字）でも 3,500B 未満に収まること"""
        session_typical = {
            "id": "sess_typical_payload_test",
            "setting": {
                "title": "標準会話テスト",
                "user_name": "ユーザー",
                "character": {"name": "ヒロイン"},
            },
            "messages": [
                {
                    "id": "u_typical",
                    "role": "user",
                    "text": "明日の放課後、図書室で一緒に勉強しようよ。テスト範囲の数学を教えてほしいんだ。" * 2,  # ~100文字
                },
                {
                    "id": "a_typical",
                    "role": "assistant",
                    "text": "うん、いいよ！明日の放課後ね。図書室の窓際の席で待ってるね。数学なら得意だから何でも聞いて！約束だよ。" * 3,  # ~180文字
                },
            ],
        }
        ctx = server.build_memory_extract_context(session_typical, session_typical["messages"][1])
        raw_json = json.dumps(ctx, ensure_ascii=False)
        utf8_bytes = len(raw_json.encode("utf-8"))
        self.assertLess(utf8_bytes, 3500, f"Typical exchange exceeded 3,500B: {utf8_bytes}B")

    def test_instruction_formatting_and_categories(self):
        """指示文に9種カテゴリおよび三人称完全自立文ルールが含まれること"""
        ctx = server.build_memory_extract_context(self.session, self.asst_msg)
        inst_text = ctx["sections"][0]["text"]

        self.assertIn("【タスク: 長期記憶の要約・抽出】", inst_text)
        self.assertIn("アリス", inst_text)
        self.assertIn("ナオキ", inst_text)

        # 9カテゴリすべて網羅
        for cat in [
            "rule", "promise", "item", "status", "calling",
            "char_fact", "user_fact", "relationship", "event"
        ]:
            self.assertIn(cat, inst_text)

        # 記述ルールの検証
        self.assertIn("三人称完全文", inst_text)
        self.assertIn("空配列 []", inst_text)

    def test_exchange_formatting(self):
        """直前1往復の会話が正確にフォーマットされていること"""
        ctx = server.build_memory_extract_context(self.session, self.asst_msg)
        exchange_text = ctx["sections"][1]["text"]

        self.assertIn("【ナオキの発言】", exchange_text)
        self.assertIn(self.session["messages"][0]["text"], exchange_text)
        self.assertIn("【アリスの返答】", exchange_text)
        self.assertIn(self.session["messages"][1]["text"], exchange_text)

    def test_exchange_without_user_msg(self):
        """直前のユーザー発言が存在しない初期状態でも破綻しないこと"""
        session_single = {
            "id": "single_sess",
            "setting": {"user_name": "ナオキ", "character": {"name": "アリス"}},
            "messages": [
                {"id": "msg_0", "role": "assistant", "text": "はじめまして！"}
            ],
        }
        ctx = server.build_memory_extract_context(session_single, session_single["messages"][0])
        exchange_text = ctx["sections"][1]["text"]
        self.assertNotIn("【ナオキの発言】", exchange_text)
        self.assertIn("【アリスの返答】\nはじめまして！", exchange_text)

    def test_json_response_schema(self):
        """応答例の JSON が compact スキーマに合致し、resolved_ids が空配列であること"""
        ctx = server.build_memory_extract_context(self.session, self.asst_msg)
        resp_text = ctx["sections"][2]["text"]

        # コードブロックから JSON を抽出
        match = re.search(r"```json\s*(.*?)\s*```", resp_text, re.DOTALL)
        self.assertIsNotNone(match, "JSON codeblock not found in response instruction")

        parsed = json.loads(match.group(1))
        self.assertEqual(parsed["session"], self.session["id"])
        self.assertEqual(parsed["reply_to"], self.asst_msg["id"])
        self.assertEqual(parsed["type"], "memory_extract")
        self.assertEqual(parsed["resolved_ids"], [])
        self.assertIsInstance(parsed["memories"], list)
        self.assertGreaterEqual(len(parsed["memories"]), 1)
        self.assertIn("category", parsed["memories"][0])
        self.assertIn("content", parsed["memories"][0])

    def test_meta_fields(self):
        """light: True およびその他メタフィールドが正しく設定されていること"""
        ctx = server.build_memory_extract_context(self.session, self.asst_msg)
        self.assertTrue(ctx.get("light"), "light flag must be True")
        self.assertEqual(ctx.get("mode"), "memory_extract")
        self.assertEqual(ctx.get("reason"), "長期記憶の要約・抽出")
        self.assertEqual(ctx.get("title"), self.session["setting"]["title"])
        self.assertEqual(ctx.get("same"), [])
        self.assertEqual(ctx.get("files"), [])
        self.assertEqual(ctx.get("missing"), [])
        self.assertIsNone(ctx.get("rules"))

    def test_fallback_when_setting_empty_or_none(self):
        """setting に user_name や character がない場合、None の場合、setting キー欠落時のフォールバック動作"""
        for empty_setting in ({}, None):
            empty_session = {
                "id": "empty_sess",
                "setting": empty_setting,
                "messages": [],
            }
            msg = {"id": "m_ext", "role": "assistant", "text": "テスト返答"}
            ctx = server.build_memory_extract_context(empty_session, msg)
            inst_text = ctx["sections"][0]["text"]
            self.assertIn("あなた", inst_text)
            self.assertIn("ユーザー", inst_text)
            self.assertIn("【あなたの返答】\nテスト返答", ctx["sections"][1]["text"])

        # setting キー自体が欠落している場合
        session_no_setting = {"id": "no_setting", "messages": []}
        ctx2 = server.build_memory_extract_context(session_no_setting, {"id": "m1", "role": "assistant", "text": "OK"})
        self.assertIn("あなた", ctx2["sections"][0]["text"])
        self.assertIn("【あなたの返答】\nOK", ctx2["sections"][1]["text"])

        # messages キー自体が欠落または None の場合
        session_no_msgs = {"id": "no_msgs", "setting": {}}
        ctx3 = server.build_memory_extract_context(session_no_msgs, {"id": "m1", "role": "assistant", "text": "OK"})
        self.assertIn("【あなたの返答】\nOK", ctx3["sections"][1]["text"])

    def test_message_not_in_messages_list(self):
        """メッセージが messages 配列に含まれていない場合でも、直前のユーザー発言を正しく拾うこと"""
        session = {
            "id": "sess_orphan",
            "setting": {"user_name": "U", "character": {"name": "C"}},
            "messages": [
                {"id": "m1", "role": "user", "text": "こんにちは"},
            ],
        }
        orphan_msg = {"id": "m_orphan", "role": "assistant", "text": "こんにちは！"}
        ctx = server.build_memory_extract_context(session, orphan_msg)
        self.assertEqual(ctx["mode"], "memory_extract")
        exchange_text = ctx["sections"][1]["text"]
        self.assertIn("【Uの発言】\nこんにちは", exchange_text)
        self.assertIn("【Cの返答】\nこんにちは！", exchange_text)

    def test_message_not_in_messages_list_with_history(self):
        """会話履歴が複数ある状態で未登録の返答が渡された場合、直近のユーザー発言が対話相手になること"""
        session = {
            "id": "sess_history",
            "setting": {"user_name": "U", "character": {"name": "C"}},
            "messages": [
                {"id": "u1", "role": "user", "text": "1ターン目の発言"},
                {"id": "a1", "role": "assistant", "text": "1ターン目の返答"},
                {"id": "u2", "role": "user", "text": "2ターン目の発言（直前）"},
            ],
        }
        orphan_msg = {"id": "m_new", "role": "assistant", "text": "2ターン目の返答"}
        ctx = server.build_memory_extract_context(session, orphan_msg)
        exchange_text = ctx["sections"][1]["text"]
        # 直前の u2 が採用され、古い u1 は含まれないこと
        self.assertIn("【Uの発言】\n2ターン目の発言（直前）", exchange_text)
        self.assertNotIn("1ターン目の発言", exchange_text)
        self.assertIn("【Cの返答】\n2ターン目の返答", exchange_text)

    def test_message_without_id_or_none_text(self):
        """id 欠落や text が None の場合でも安全にフォールバックすること"""
        session = {
            "setting": {"user_name": "ナオキ", "character": {"name": "アリス"}},
            "messages": [
                {"role": "user", "text": None},
            ],
        }
        msg_none_text = {"role": "assistant", "text": None}
        ctx = server.build_memory_extract_context(session, msg_none_text)
        exchange_text = ctx["sections"][1]["text"]
        # 'None' という文字列が出力されず空文字として扱われること
        self.assertNotIn("None", exchange_text)
        self.assertIn("【ナオキの発言】\n", exchange_text)
        self.assertIn("【アリスの返答】\n", exchange_text)

        # JSON 応答例にデフォルトのプレースホルダーが使用されること
        resp_text = ctx["sections"][2]["text"]
        match = re.search(r"```json\s*(.*?)\s*```", resp_text, re.DOTALL)
        self.assertIsNotNone(match)
        parsed = json.loads(match.group(1))
        self.assertEqual(parsed["session"], "<session_id>")
        self.assertEqual(parsed["reply_to"], "<message_id>")

    def test_invalid_input_types_graceful(self):
        """s や m に None や不正な型が渡されてもクラッシュしないこと"""
        ctx1 = server.build_memory_extract_context(None, None)
        self.assertEqual(ctx1["mode"], "memory_extract")
        self.assertEqual(len(ctx1["sections"]), 3)

        ctx2 = server.build_memory_extract_context("invalid", 123)
        self.assertEqual(ctx2["mode"], "memory_extract")
        self.assertEqual(len(ctx2["sections"]), 3)


if __name__ == "__main__":
    unittest.main()
