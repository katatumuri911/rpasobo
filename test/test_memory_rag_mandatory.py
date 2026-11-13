# -*- coding: utf-8 -*-
"""長期記憶（RAG）メイン応答必須化 ＆ memory_extract 不活性化の検証テスト群"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import memory_store
import server
import wait_input


def make_msg(role, text, mid=None, **kwargs):
    d = {"id": mid or server.new_id(), "role": role, "text": text, "ts": server.now()}
    d.update(kwargs)
    return d


class TestMemoryRAGMandatory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_rag_mand_")
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "RAG_DIR", "SENT", "CTX", "OUTBOX", "DONE", "NEW_IDE_DIR"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)
        wait_input.OUTBOX = server.OUTBOX
        wait_input.DONE = server.DONE
        wait_input.CTX_DIR = server.CTX

        self.orig_ms_data = memory_store.DATA
        self.orig_ms_rag = memory_store.RAG_DIR
        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = server.RAG_DIR

        server.session_cache.clear()
        server.queued_memories.clear()
        server.memory_event.clear()
        memory_store._memory_cache.clear()

    def tearDown(self):
        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        wait_input.OUTBOX = server.OUTBOX
        wait_input.DONE = server.DONE
        wait_input.CTX_DIR = server.CTX
        memory_store.DATA = self.orig_ms_data
        memory_store.RAG_DIR = self.orig_ms_rag
        server.queued_memories.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_output_text_mandatory_memories_all_branches(self):
        """全4分岐で memories が必須キー化され、ルール語句・日常エピソードの指示が含まれること"""
        st = {
            "character": {"name": "ラピ"},
            "rp_state": True,
            "image_mode": "always",
            "suggest_choices": True
        }
        # 1. Diff × Compact
        with patch.dict(os.environ, {"RPCHAT_COMPACT": "1"}):
            txt_diff_compact = server.output_text(st, is_diff=True)
            self.assertIn("必須 text / memories", txt_diff_compact)
            self.assertIn("任意 memo", txt_diff_compact)
            self.assertIn("催眠により〜", txt_diff_compact)
            self.assertIn("〜という決まり/ルール/常識", txt_diff_compact)
            self.assertIn("日常の出来事", txt_diff_compact)
            self.assertIn("約6割は []", txt_diff_compact)
            self.assertIn("行為中の動作・体勢変化は state に書き []", txt_diff_compact)
            self.assertIn("シーン全体で1回", txt_diff_compact)
            self.assertIn("rule（三人称1文）", txt_diff_compact)
            self.assertIn("新確定の長期情報のみ三人称1文で出す", txt_diff_compact)
            # JSON パース確認
            s_idx = txt_diff_compact.find("```json\n") + 8
            e_idx = txt_diff_compact.find("\n```", s_idx)
            schema = json.loads(txt_diff_compact[s_idx:e_idx])
            self.assertIn("memories", schema)
            self.assertEqual(schema["memories"][0]["category"], "rule")
            # バイト数が 9,400B 枠を圧迫しないこと
            self.assertLessEqual(len(txt_diff_compact.encode("utf-8")), 3000)

        # 2. Diff × Non-Compact
        with patch.dict(os.environ, {"RPCHAT_COMPACT": "0"}):
            txt_diff_full = server.output_text(st, is_diff=True)
            self.assertIn("- memories（毎回必須・キー省略不可）:", txt_diff_full)
            self.assertIn("【必ず rule で出すもの】", txt_diff_full)
            self.assertIn("【日常の出来事・小話も記録する】", txt_diff_full)
            self.assertIn("【行為・動作の除外徹底】", txt_diff_full)
            self.assertIn("約6割の通常ターンは空配列 []", txt_diff_full)
            self.assertIn("〜という決まり/ルール/常識", txt_diff_full)
            self.assertIn("新しく確定した長期的な情報のみ三人称1文で出すこと", txt_diff_full)
            self.assertIn("1つの場面・行為で記録するのは最初または最後の大きな変化時のみ1回", txt_diff_full)
            s_idx = txt_diff_full.find("```json\n") + 8
            e_idx = txt_diff_full.find("\n```", s_idx)
            schema = json.loads(txt_diff_full[s_idx:e_idx])
            self.assertIn("memories", schema)
            self.assertIn("該当なしは []", schema["memories"][0]["content"])

        # 3. Full × Compact
        with patch.dict(os.environ, {"RPCHAT_COMPACT": "1"}):
            txt_full_compact = server.output_text(st, is_diff=False)
            self.assertIn("必須 text / memories", txt_full_compact)
            self.assertIn("催眠により〜", txt_full_compact)
            self.assertIn("〜という決まり/ルール/常識", txt_full_compact)
            self.assertIn("約6割は []", txt_full_compact)
            self.assertIn("行為中の動作・体勢変化は state に書き []", txt_full_compact)
            self.assertIn("シーン全体で1回", txt_full_compact)
            self.assertIn("rule（三人称1文）", txt_full_compact)
            self.assertIn("新確定の長期情報のみ三人称1文で出す", txt_full_compact)

        # 4. Full × Non-Compact
        with patch.dict(os.environ, {"RPCHAT_COMPACT": "0"}):
            txt_full_full = server.output_text(st, is_diff=False)
            self.assertIn("- memories（毎回必須・キー省略不可）:", txt_full_full)
            self.assertIn("【必ず rule で出すもの】", txt_full_full)
            self.assertIn("【日常の出来事・小話も記録する】", txt_full_full)
            self.assertIn("【行為・動作の除外徹底】", txt_full_full)
            self.assertIn("約6割の通常ターンは空配列 []", txt_full_full)
            self.assertIn("〜という決まり/ルール/常識", txt_full_full)
            self.assertIn("新しく確定した長期的な情報のみ三人称1文で出すこと", txt_full_full)
            self.assertIn("1つの場面・行為で記録するのは最初または最後の大きな変化時のみ1回", txt_full_full)
            self.assertIn("昼休みに屋上で一緒にお弁当を食べ", txt_full_full)

    def test_core_anchor_has_memories(self):
        """core_anchor の必須キー一覧に memories が含まれること"""
        st = {"character": {"name": "ラピ"}, "rp_state": True}
        anchor = server.core_anchor(st)
        self.assertIn("text / memories / state", anchor)

    def test_main_reply_transient_rule_demoted_to_status_protects_local_rule(self):
        """モデルが一過性の地の文を rule で出しても status へ事前降格し、ローカル抽出の本物の rule を保護する"""
        s = server.create_session({
            "title": "テスト会話",
            "character": {"name": "ラピ"},
            "user_name": "指揮官"
        })
        sid = s["id"]
        # ユーザーは本物のルールを設定
        u = make_msg("user", "ラピ、今から敬語を使うことを禁止する", mid="u1", status="pending")
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_reply_1.json")
        rep = {
            "session": sid,
            "reply_to": "u1",
            "text": "「了解した。……これでいいか？」",
            # モデルは一過性の地の文を rule として誤出力
            "memories": [
                {"category": "rule", "content": "ラピはアナルを刺激されて体が震えてしまう"}
            ]
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        combined_mems = task[3]
        # モデルの transient な rule は status に降格
        status_items = [m for m in combined_mems if m["category"] == "status"]
        self.assertEqual(len(status_items), 1)
        self.assertIn("震えてしまう", status_items[0]["content"])
        # ローカル抽出された敬語禁止ルールがブロックされずに rule として保存されること
        rule_items = [m for m in combined_mems if m["category"] == "rule"]
        self.assertEqual(len(rule_items), 1)
        self.assertIn("敬語を使うことを禁止", rule_items[0]["content"])

    def test_main_reply_genuine_rule_retains_rule_and_blocks_same_cat(self):
        """モデルが恒久的な rule を出力した場合は rule を維持し、抽出側の同カテゴリを上書きしない"""
        s = server.create_session({
            "title": "テスト会話",
            "character": {"name": "ラピ"},
            "user_name": "指揮官"
        })
        sid = s["id"]
        u = make_msg("user", "ラピ、催眠にかかれ", mid="u1", status="pending")
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_reply_1.json")
        rep = {
            "session": sid,
            "reply_to": "u1",
            "text": "「はい…指揮官の命令に従います」",
            "memories": [
                {"category": "rule", "content": "指揮官は催眠により、ラピはアナルを刺激されると犬の言葉しか話せなくなると刷り込んだ"}
            ]
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        combined_mems = task[3]
        rule_items = [m for m in combined_mems if m["category"] == "rule"]
        self.assertEqual(len(rule_items), 1)
        self.assertIn("催眠により", rule_items[0]["content"])

    def test_main_reply_memories_and_resolved_ids_normalization(self):
        """memories の文字列や辞書単体、resolved_ids の単一文字列が安全に正規化されること"""
        s = server.create_session({"title": "正規化テスト", "character": {"name": "ラピ"}})
        sid = s["id"]
        u = make_msg("user", "お弁当美味しかったね", mid="u1", status="pending")
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_reply_1.json")
        rep = {
            "session": sid,
            "reply_to": "u1",
            "text": "「はい、また作りましょう」",
            # memories に文字列混在
            "memories": ["昼休みに屋上でお弁当を食べた"],
            # resolved_ids に文字列単体
            "resolved_ids": "mem_promise_123"
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        self.assertEqual(task[3][0]["category"], "event")
        self.assertEqual(task[3][0]["content"], "昼休みに屋上でお弁当を食べた")
        # resolved_ids が文字に分割されず文字列のリストになっていること
        self.assertEqual(task[4], ["mem_promise_123"])

    def test_memory_extract_guard_against_main_reply_without_text(self):
        """ユーザー待ち状態でメイン応答が text 欠落・memories ありの場合、誤って memory_extract に吸収されず empty reply になる"""
        s = server.create_session({"title": "誤吸収防止テスト", "character": {"name": "ラピ"}})
        sid = s["id"]
        # アシスタント過去発言があり、ユーザー発言が pending
        a0 = make_msg("assistant", "前回の発言", mid="a0")
        u1 = make_msg("user", "こんにちは", mid="u1", status="pending")
        s["messages"].extend([a0, u1])
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_reply_1.json")
        # モデルが text を忘れて memories: [] だけ出力したケース
        rep = {
            "session": sid,
            "reply_to": "u1",
            "memories": []
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        # process_outbox が is_empty_reply で処理すること（queued_memories には入らない）
        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 0)
        # u1 は status pending のままであること
        s_check = server.load(sid, readonly=True)
        self.assertEqual(s_check["messages"][-1]["status"], "pending")

    def test_memory_extract_subtask_stale_moves_to_stale_file(self):
        """memory_extract サブタスクで対象のアシスタントが見つからない場合、.stale に移動して無限ループしない"""
        s = server.create_session({"title": "staleテスト", "character": {"name": "ラピ"}})
        sid = s["id"]
        outbox_file = os.path.join(server.OUTBOX, f"{sid}_mem_extract_stale.json")
        rep = {
            "session": sid,
            "reply_to": "non_existent_msg_id",
            "type": "memory_extract",
            "memories": [{"category": "event", "content": "テスト"}]
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertFalse(os.path.exists(outbox_file))
        stale_file = os.path.join(server.DONE, f"{sid}_mem_extract_stale.json.stale")
        self.assertTrue(os.path.exists(stale_file))

    def test_memory_extract_subtask_empty_memories_skips_queue(self):
        """memory_extract サブタスクで memories も resolved_ids も空の場合、queued_memories に投入せず正常完了する"""
        s = server.create_session({"title": "空配列テスト", "character": {"name": "ラピ"}})
        sid = s["id"]
        a1 = make_msg("assistant", "了解です", mid="a1", memory_status="pending")
        s["messages"].append(a1)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_mem_empty.json")
        rep = {
            "session": sid,
            "reply_to": "a1",
            "type": "memory_extract",
            "memories": [],
            "resolved_ids": []
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        imported = server.process_outbox()
        self.assertTrue(imported)
        self.assertEqual(len(server.queued_memories), 0)
        s_after = server.load(sid, readonly=True)
        self.assertNotIn("memory_status", s_after["messages"][-1])

    def test_main_reply_transient_rule_does_not_block_local_status(self):
        """モデルの一過性 rule が status に降格されても、ローカル抽出の本物の status をブロックしない"""
        s = server.create_session({
            "title": "status保護テスト",
            "character": {"name": "ラピ"},
            "user_name": "指揮官"
        })
        sid = s["id"]
        # ユーザー発言に本物の status（キスマーク痕跡）が含まれる
        u = make_msg("user", "ラピの首元につけたキスマークが残っているね", mid="u1", status="pending")
        s["messages"].append(u)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_reply_status_prot.json")
        rep = {
            "session": sid,
            "reply_to": "u1",
            "text": "「はい……消えずに残っています」",
            # モデルは一過性の地の文を rule として誤出力（status へ降格される）
            "memories": [
                {"category": "rule", "content": "ラピはアナルを刺激されて体が震えてしまう"}
            ]
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        combined_mems = task[3]

        # 1. モデルの降格された status が含まれる
        demoted = [m for m in combined_mems if "震えてしまう" in m["content"]]
        self.assertEqual(len(demoted), 1)
        self.assertEqual(demoted[0]["category"], "status")

        # 2. ローカル抽出されたキスマークの status がブロックされずに含まれる
        local_status = [m for m in combined_mems if "キスマーク" in m["content"]]
        self.assertEqual(len(local_status), 1)
        self.assertEqual(local_status[0]["category"], "status")

    def test_memory_extract_subtask_normalizes_single_dict_and_string_resolved_id(self):
        """memory_extract サブタスクで memories が単一辞書、resolved_ids が単一文字列でも安全に正規化される"""
        s = server.create_session({"title": "温存コード正規化テスト", "character": {"name": "ラピ"}})
        sid = s["id"]
        a1 = make_msg("assistant", "了解です", mid="a1", memory_status="pending")
        s["messages"].append(a1)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_mem_single_dict.json")
        rep = {
            "session": sid,
            "reply_to": "a1",
            "type": "memory_extract",
            # 単一辞書（キー配列 ["category", "content"] への誤変換を防止）
            "memories": {"category": "rule", "content": "指揮官の命令は絶対である"},
            # 単一文字列（文字分割 ['m','e','m','_','0','0','1'] を防止）
            "resolved_ids": "mem_001"
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        imported = server.process_outbox()
        self.assertTrue(imported)
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        mems = task[3]
        res_ids = task[4]
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["category"], "rule")
        self.assertEqual(mems[0]["content"], "指揮官の命令は絶対である")
        self.assertEqual(res_ids, ["mem_001"])

    def test_output_text_diff_compact_size_budget(self):
        """Diff×Compact モードのプロンプトが 1,306文字 / 2,567バイト以下（memo予算指定なし基準）を満たすこと"""
        st = {
            "character": {"name": "ラピ"},
            "rp_state": True,
            "image_mode": "always",
            "suggest_choices": True,
            "ide_session": "ide-1",
            "choices_ide_session": "cho-1"
        }
        with patch.dict(os.environ, {"RPCHAT_COMPACT": "1", "RPCHAT_MEMO_BUDGET": "0"}):
            txt = server.output_text(st, is_diff=True)
            chars = len(txt)
            b = len(txt.encode("utf-8"))
            self.assertLessEqual(chars, 1306, f"chars exceeded: {chars}")
            self.assertLessEqual(b, 2567, f"bytes exceeded: {b}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
