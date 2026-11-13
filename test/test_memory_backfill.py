# -*- coding: utf-8 -*-
"""フェーズ② 重要記憶の後追い抽出のテスト

- needs_memory_backfill() の判定（重要語の有無）
- process_outbox の配線（キーワードで拾えたら即保存・拾えなければ pending にする）
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import server
import memory_store
import wait_input


class TestNeedsBackfill(unittest.TestCase):
    def test_hypnosis_triggers(self):
        self.assertTrue(server.needs_memory_backfill(
            "催眠「ラピは尻を叩かれると犬語になる」", "「わんっ」"))

    def test_chitchat_does_not_trigger(self):
        self.assertFalse(server.needs_memory_backfill("おはよう", "「おはようございます」"))

    def test_hint_in_reply_only(self):
        self.assertTrue(server.needs_memory_backfill("ふーん", "「それは命令ですか？」"))

    def test_keyword_extraction_covers_first(self):
        # キーワードで拾える場合は後追い抽出に回さない（呼び出し側の条件）
        got = server.extract_memories_from_text(
            "催眠「アナルに入れると犬になる」", "「くぅん」", char_name="ラピ", user_name="指揮官")
        self.assertTrue(got)

    def test_rollback_switch_disables_backfill(self):
        # BACKFILL_HINTS = () にすれば従来動作（キーワードのみ）に戻る
        orig = server.BACKFILL_HINTS
        try:
            server.BACKFILL_HINTS = ()
            self.assertFalse(server.needs_memory_backfill("催眠", "「わんっ」"))
        finally:
            server.BACKFILL_HINTS = orig


class TestBackfillWiring(unittest.TestCase):
    """process_outbox が同期キーワード抽出 → 0件なら pending にすることを確かめる"""

    def setUp(self):
        self.orig_backfill_env = os.environ.get("RPCHAT_MEMORY_BACKFILL")
        os.environ["RPCHAT_MEMORY_BACKFILL"] = "1"
        self.test_dir = tempfile.mkdtemp(prefix="test_backfill_")
        self.orig = {
            "DATA": server.DATA, "SESS_DIR": server.SESS_DIR, "OUTBOX": server.OUTBOX,
            "DONE": server.DONE, "NEW_IDE_DIR": server.NEW_IDE_DIR, "AGENTS_LOG": server.AGENTS_LOG,
            "CTX": server.CTX, "RAG_DIR": server.RAG_DIR,
            "ms_DATA": memory_store.DATA, "ms_RAG": memory_store.RAG_DIR,
        }
        server.DATA = os.path.join(self.test_dir, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        server.STATES_DIR = os.path.join(server.DATA, "states")
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        server.SENT = os.path.join(server.DATA, "sent")
        server.ARCHIVE_DIR = os.path.join(server.DATA, "archive")
        server.TRASH = os.path.join(server.DATA, "trash")
        server.CTX = os.path.join(server.DATA, "ctx")
        server.RAG_DIR = os.path.join(server.DATA, "rag")
        for d in (server.SESS_DIR, server.CHOICES_DIR, server.STATES_DIR, server.IMAGES_DIR,
                  server.SENT, server.ARCHIVE_DIR, server.TRASH, server.CTX):
            os.makedirs(d, exist_ok=True)
        # IDE 側のフォルダは import 時に確定するので、本物の session/ を触らないよう差し替える
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = os.path.join(server.DATA, "rag")
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)
        server.OUTBOX = wait_input.OUTBOX = os.path.join(self.test_dir, "outbox")
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.summary_cache.clear()
        server.queued_memories.clear()
        server.memory_event.clear()
        memory_store._memory_cache.clear()
        server._memory_worker_stop = False

    def tearDown(self):
        if self.orig_backfill_env is None:
            os.environ.pop("RPCHAT_MEMORY_BACKFILL", None)
        else:
            os.environ["RPCHAT_MEMORY_BACKFILL"] = self.orig_backfill_env
        server._memory_worker_stop = True
        server.memory_event.set()
        server.DATA = self.orig["DATA"]
        server.SESS_DIR = self.orig["SESS_DIR"]
        server.NEW_IDE_DIR = self.orig["NEW_IDE_DIR"]
        server.AGENTS_LOG = self.orig["AGENTS_LOG"]
        server.OUTBOX = wait_input.OUTBOX = self.orig["OUTBOX"]
        server.DONE = self.orig["DONE"]
        server.CTX = self.orig["CTX"]
        server.RAG_DIR = self.orig["RAG_DIR"]
        memory_store.DATA = self.orig["ms_DATA"]
        memory_store.RAG_DIR = self.orig["ms_RAG"]
        server.queued_memories.clear()
        memory_store._memory_cache.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _reply(self, user_text, asst_text, s=None, n=1):
        if s is None:
            s = server.create_session({"title": "テスト会話", "character": {"name": "ラピ"}})
        sid = s["id"]
        user_msg = {"id": f"u{n}", "role": "user", "text": user_text,
                    "ts": server.now(), "status": "pending"}
        s["messages"].append(user_msg)
        server.save(s)
        path = os.path.join(server.OUTBOX, f"{sid}_asst_{n}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": user_msg["id"], "text": asst_text},
                      f, ensure_ascii=False)
        self.assertTrue(server.process_outbox())
        return sid, server.load(sid, readonly=True)

    def test_keyword_hit_queues_explicit(self):
        """重要語がありキーワードで拾えたら、従来どおり explicit で即保存キューに乗る"""
        sid, s = self._reply("ラピ、今度の日曜日にカフェに行く約束をしよう",
                             "うん、日曜日にカフェに行く約束ね")
        asst = s["messages"][-1]
        self.assertNotIn("memory_status", asst)   # LLM 後追い抽出には回さない
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        self.assertEqual(task[0], "explicit")
        self.assertEqual(task[1], sid)
        self.assertTrue(task[3])                  # 抽出済みの記憶が入っている
        self.assertEqual(task[6], asst["id"])

    def test_default_disabled_does_not_set_pending(self):
        """既定（RPCHAT_MEMORY_BACKFILL=0）ではヒント語があっても memory_status を pending にしない"""
        os.environ.pop("RPCHAT_MEMORY_BACKFILL", None)
        self.assertFalse(server.memory_backfill_on())
        sid, s = self._reply("これ、覚えておいてね", "「はい、覚えておきます」")
        asst = s["messages"][-1]
        self.assertNotIn("memory_status", asst)
        self.assertNotIn("memory_ts", asst)
        self.assertEqual(len(server.queued_memories), 0)

    def test_hints_without_keyword_sets_pending(self):
        """重要語があるのに1件も取れない時だけ pending（LLM 後追い抽出）にする"""
        sid, s = self._reply("これ、覚えておいてね", "「はい、覚えておきます」")
        asst = s["messages"][-1]
        self.assertEqual(asst.get("memory_status"), "pending")
        # memory_ts は ISO 文字列（float だと age() が TypeError で落ちる）
        self.assertIsInstance(asst.get("memory_ts"), str)
        self.assertRegex(asst["memory_ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertEqual(len(server.queued_memories), 0)

    def test_chitchat_does_nothing(self):
        """重要語も無く拾えないターンは、キューも pending も増やさない（追加コスト0）"""
        sid, s = self._reply("おはよう", "「おはようございます。いい天気ですね」")
        asst = s["messages"][-1]
        self.assertNotIn("memory_status", asst)
        self.assertNotIn("memory_ts", asst)
        self.assertEqual(len(server.queued_memories), 0)

    def test_no_double_pending(self):
        """既に pending があるセッションでは積まない（キュー詰まり防止）"""
        sid, s = self._reply("これ、覚えておいてね", "「はい、覚えておきます」", n=1)
        self.assertEqual(s["messages"][-1].get("memory_status"), "pending")
        # 2ターン目も重要語あり・キーワード0件
        _, s2 = self._reply("これも覚えておいて", "「承知しました」", s=s, n=2)
        self.assertNotIn("memory_status", s2["messages"][-1])
        self.assertEqual(len(server.queued_memories), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
