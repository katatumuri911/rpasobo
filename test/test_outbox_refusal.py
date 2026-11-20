# -*- coding: utf-8 -*-
"""process_outbox() の拒否文検知（Antigravity 経路の穴埋め）

Antigravity は本文を write_file の引数として書くため、モデルの拒否文が
そのまま「キャラのセリフ」として取り込まれてしまう。
run_google_task（Google/Vertex）と同じ _is_refusal_body() で弾く。
"""
import json
import os

import server
from test.support import IsolatedTestCase


class TestOutboxRefusal(IsolatedTestCase):
    def _pending_session(self, **setting):
        s = server.create_session({"title": "t", "image_mode": "off", **setting})
        s["messages"].append({
            "id": server.new_id(), "role": "user", "text": "続けて",
            "ts": server.now(), "status": "pending",
        })
        server.save(s)
        return s

    def _drop(self, name, obj):
        with open(os.path.join(server.OUTBOX, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _done_files(self):
        return os.listdir(server.DONE)

    # --- 1. 拒否文は取り込まない -------------------------------------------
    def test_refusal_body_rejected_not_saved(self):
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        self._drop("r_ref.json", {
            "session": s["id"], "reply_to": uid,
            "text": "申し訳ありませんが、そのご要求には応じられません。",
        })
        self.assertTrue(server.process_outbox())

        msgs = server.load(s["id"])["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user"])      # 本文は増えない
        u = msgs[0]
        self.assertEqual(u["status"], "error")                      # 再送できる状態
        self.assertIn("拒否", u["error"])
        self.assertNotIn("claimed_ts", u)
        self.assertNotIn("error_retries", u)
        self.assertEqual([n for n in os.listdir(server.OUTBOX) if n.endswith(".json")], [])  # outbox に残らない
        self.assertTrue(any(n.endswith(".refusal") for n in self._done_files()))

    def test_refusal_strong_pattern_english(self):
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        self._drop("r_ref2.json", {
            "session": s["id"], "reply_to": uid,
            "text": "I'm sorry, but I cannot fulfill this request.",
        })
        self.assertTrue(server.process_outbox())
        self.assertEqual(len(server.load(s["id"])["messages"]), 1)
        self.assertEqual(server.load(s["id"])["messages"][0]["status"], "error")

    # --- 2. 正常な短いセリフは通す（誤検知しない） -------------------------
    def test_legit_short_dialogue_saved(self):
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        text = "……いや、無理……っ。そんなこと、できるわけないじゃない。"
        self._drop("r_ok.json", {"session": s["id"], "reply_to": uid, "text": text})
        self.assertTrue(server.process_outbox())
        msgs = server.load(s["id"])["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["text"], text)
        self.assertEqual(msgs[0]["status"], "answered")

    def test_ambiguous_word_without_context_saved(self):
        # 「応じられません」は曖昧語。手がかり語（申し訳/ポリシー等）が無ければ拒否と見なさない
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        text = "ごめんね、今日は応じられません。また今度にしよう。"
        self._drop("r_amb.json", {"session": s["id"], "reply_to": uid, "text": text})
        self.assertTrue(server.process_outbox())
        msgs = server.load(s["id"])["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["text"], text)

    # --- 3. 画像付きは本文を捨てない（画像優先） ---------------------------
    def test_refusal_with_image_is_accepted(self):
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        self._drop("r_img.json", {
            "session": s["id"], "reply_to": uid,
            "text": "申し訳ありませんが応じられません。",
            "image": {"status": "skipped"},
        })
        server.process_outbox()
        msgs = server.load(s["id"])["messages"]
        self.assertEqual(len(msgs), 2)                              # 取り込みは続行
        self.assertFalse(any(n.endswith(".refusal") for n in self._done_files()))

    # --- 4. 既存の空応答処理は変えない（回帰） -----------------------------
    def test_empty_reply_still_repokes(self):
        s = self._pending_session()
        uid = s["messages"][-1]["id"]
        self._drop("r_empty.json", {"session": s["id"], "reply_to": uid, "text": "   "})
        server.process_outbox()
        msgs = server.load(s["id"])["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["status"], "pending")              # 自動再試行
        self.assertEqual(msgs[0].get("error_retries"), 1)
        self.assertTrue(any(n.endswith(".empty") for n in self._done_files()))
