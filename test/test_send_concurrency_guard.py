# -*- coding: utf-8 -*-
"""test/test_send_concurrency_guard.py: 送信排他制御・未応答ガード・outbox二段階照合の包括テスト (F-03)"""
import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import server
from test.support import IsolatedTestCase


class FakeHandler:
    def __init__(self, path, body=None, query=None):
        self.path = path
        self._body = body or {}
        self._query = query or {}
        self.status = 200
        self.result = None

    def blocked(self, u, post=False):
        return False

    def body(self):
        return self._body

    def query(self):
        return self._query

    def send_json(self, data, code=200):
        self.lock_recursion = getattr(server.lock, "_recursion_count", lambda: 0)()
        self.status = code
        self.result = data
        return data

    def send_error(self, code, msg=None):
        self.status = code
        self.result = {"error": msg or str(code)}


class TestSendConcurrencyGuard(IsolatedTestCase):
    """(F-03) 二重送信防止、未応答ガード、outbox安全処理を検証する"""

    def _post(self, path, body):
        h = FakeHandler(path, body)
        server.H.do_POST(h)
        return h

    def _create_test_session(self, sid=None, title="Test Session"):
        st = {"title": title, "ai_provider": "antigravity"}
        s = server.create_session(st)
        if sid:
            # セッションIDを指定したい場合（ファイル名リネーム）
            old_path = server.spath(s["id"])
            s["id"] = sid
            new_path = server.spath(sid)
            if os.path.exists(old_path):
                os.remove(old_path)
            server.save(s)
        return s

    # --------------------------------------------------------------------------
    # 1. 並行送信排他（TOCTOU）テスト
    # --------------------------------------------------------------------------
    def test_concurrent_send_toctou(self):
        """同一セッションへ同時に 2 件の POST /api/send を発行した場合、片方のみ 200、もう片方は 409 となること"""
        s = self._create_test_session()
        sid = s["id"]

        barrier = threading.Barrier(2)
        results = []

        def worker(text):
            barrier.wait()
            h = FakeHandler("/api/send", {"sid": sid, "text": text})
            server.H.do_POST(h)
            results.append(h)

        t1 = threading.Thread(target=worker, args=("Send 1",))
        t2 = threading.Thread(target=worker, args=("Send 2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        statuses = sorted([r.status for r in results])
        self.assertEqual(statuses, [200, 409], f"Expected exactly one 200 and one 409, got {statuses}")

        err_res = next(r for r in results if r.status == 409)
        self.assertEqual(err_res.result.get("code"), "pending_turn_exists")

        s_after = server.load(sid)
        user_msgs = [m for m in s_after["messages"] if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1, "Exactly one user message should have been recorded")

    # --------------------------------------------------------------------------
    # 2. 未応答状態（pending / processing）での /api/send ブロック
    # --------------------------------------------------------------------------
    def test_send_blocked_when_pending_or_processing(self):
        """保留中 (pending / processing) のメッセージがある場合、新たな /api/send は 409 で弾かれること"""
        s = self._create_test_session()
        sid = s["id"]

        # status == "pending"
        self.add_user_msg(s, "1通目", status="pending")
        server.save(s)

        h1 = self._post("/api/send", {"sid": sid, "text": "2通目"})
        self.assertEqual(h1.status, 409)
        self.assertEqual(h1.result.get("code"), "pending_turn_exists")

        # status == "processing"
        s = server.load(sid)
        s["messages"][0]["status"] = "processing"
        server.save(s)

        h2 = self._post("/api/send", {"sid": sid, "text": "3通目"})
        self.assertEqual(h2.status, 409)
        self.assertEqual(h2.result.get("code"), "pending_turn_exists")

        # status == "answered" になれば送信可能
        s = server.load(sid)
        s["messages"][0]["status"] = "answered"
        server.save(s)

        h3 = self._post("/api/send", {"sid": sid, "text": "4通目"})
        self.assertEqual(h3.status, 200)
        self.assertTrue(h3.result.get("ok"))

    # --------------------------------------------------------------------------
    # 3. /api/send/stop と process_outbox() の競合耐性テスト
    # --------------------------------------------------------------------------
    def test_stop_concurrent_with_outbox(self):
        """stop と outbox が同時に動いてもデッドロックやデータ破損せず正常に整合すること"""
        s = self._create_test_session()
        sid = s["id"]
        u = self.add_user_msg(s, "停止対象", status="pending")
        server.save(s)

        # outbox ファイルを用意
        out_fname = f"{sid}_reply.json"
        out_path = os.path.join(server.OUTBOX, out_fname)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": "AI回答"}, f)

        barrier = threading.Barrier(2)
        results = {}

        def do_stop():
            barrier.wait()
            h = FakeHandler("/api/send/stop", {"sid": sid, "id": u["id"]})
            server.H.do_POST(h)
            results["stop"] = h

        def do_outbox():
            barrier.wait()
            results["outbox"] = server.process_outbox()

        t1 = threading.Thread(target=do_stop)
        t2 = threading.Thread(target=do_outbox)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # どちらが勝ってもセッションデータは正しくロードでき、壊れていないこと
        s_after = server.load(sid)
        self.assertIsNotNone(s_after)

        stop_h = results["stop"]
        if stop_h.status == 200:
            # stop が勝った場合: 発言は消去され、outbox ファイルは .stale へ送られるか削除
            self.assertFalse(any(m["id"] == u["id"] for m in s_after["messages"]))
        else:
            # outbox が勝った場合: stop は 409（もう届いている）となり、回答が確定
            self.assertEqual(stop_h.status, 409)
            u_after = server.find_msg(s_after, u["id"])
            self.assertEqual(u_after.get("status"), "answered")

    # --------------------------------------------------------------------------
    # 4. /api/send/stop 単体動作と 409 判定
    # --------------------------------------------------------------------------
    def test_stop_happy_path_and_no_target_409(self):
        """保留中の送信を正しく取り消し、存在しない/回答済みの場合は 409 を返すこと"""
        s = self._create_test_session()
        sid = s["id"]
        u = self.add_user_msg(s, "取り消したい入力", status="pending")
        server.save(s)

        # 正常系: 停止成功
        h1 = self._post("/api/send/stop", {"sid": sid, "id": u["id"]})
        self.assertEqual(h1.status, 200)
        self.assertEqual(h1.result.get("text"), "取り消したい入力")

        s_after = server.load(sid)
        self.assertEqual(len(s_after["messages"]), 0)

        # 既に停止済み（対象なし）での停止要求 -> 409
        h2 = self._post("/api/send/stop", {"sid": sid, "id": u["id"]})
        self.assertEqual(h2.status, 409)

    # --------------------------------------------------------------------------
    # 5. アンダースコア入りセッションIDの outbox 最長一致解決
    # --------------------------------------------------------------------------
    def test_outbox_underscore_session_resolution(self):
        """セッションIDにアンダースコアが含まれていても、誤判定されず正しく照合されること"""
        # sess_test と sess_test_01 の2つを作成
        s_short = self._create_test_session(sid="sess_test", title="Short")
        s_long = self._create_test_session(sid="sess_test_01", title="Long")

        u_long = self.add_user_msg(s_long, "ロング側の発言", status="pending")
        server.save(s_long)

        # JSON 本文に session を含めず、ファイル名のみで照合させる
        out_fname = "sess_test_01_reply_12345.json"
        out_path = os.path.join(server.OUTBOX, out_fname)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"text": "ロング側への回答"}, f)

        res = server.process_outbox()
        self.assertTrue(res)

        s_long_after = server.load("sess_test_01")
        u_after = server.find_msg(s_long_after, u_long["id"])
        self.assertEqual(u_after.get("status"), "answered")
        self.assertEqual(s_long_after["messages"][-1]["text"], "ロング側への回答")

        s_short_after = server.load("sess_test")
        self.assertEqual(len(s_short_after["messages"]), 0)

    # --------------------------------------------------------------------------
    # 6. 二段階トークン照合テスト
    # --------------------------------------------------------------------------
    def test_outbox_two_stage_reply_to_matching(self):
        """タイムスタンプ等の無関係なトークンは先頭フォールバックし、過去回答済み発言IDは .stale へ廃棄すること"""
        s = self._create_test_session()
        sid = s["id"]

        # 過去の回答済み発言
        u_old = self.add_user_msg(s, "過去の発言", status="answered")
        a_old = {"id": server.new_id(), "role": "assistant", "text": "過去の回答", "ts": server.now()}
        s["messages"].append(a_old)

        # 現在の保留中発言
        u_cur = self.add_user_msg(s, "現在の発言", status="pending")
        server.save(s)

        # Case 6a: タイムスタンプ入りファイル名（<sid>_reply_20261120.json）
        # どの発言IDにも一致しないトークン -> reply_to は None を維持し、現在の保留発言にフォールバック
        fname_ts = f"{sid}_reply_20261120.json"
        with open(os.path.join(server.OUTBOX, fname_ts), "w", encoding="utf-8") as f:
            json.dump({"session": sid, "text": "現在発言への正常回答"}, f)

        res = server.process_outbox()
        self.assertTrue(res)

        s_after = server.load(sid)
        u_cur_after = server.find_msg(s_after, u_cur["id"])
        self.assertEqual(u_cur_after.get("status"), "answered")
        self.assertEqual(s_after["messages"][-1]["text"], "現在発言への正常回答")

        # Case 6b: 過去の回答済み発言 ID を含むファイル名（<sid>_<u_old["id"]>_reply.json）
        # 保留中になく過去発言に一致 -> 陳腐化応答として reply_to を設定し、.stale へ直行
        # 新しい保留発言を用意
        u_new = self.add_user_msg(s_after, "最新の発言", status="pending")
        server.save(s_after)

        fname_stale = f"{sid}_{u_old['id']}_reply.json"
        with open(os.path.join(server.OUTBOX, fname_stale), "w", encoding="utf-8") as f:
            json.dump({"session": sid, "text": "遅れて届いた過去回答"}, f)

        server.process_outbox()

        # 最新の保留発言は回答されずに保留のままであること
        s_after2 = server.load(sid)
        u_new_after = server.find_msg(s_after2, u_new["id"])
        self.assertEqual(u_new_after.get("status"), "pending")

        # ファイルが .stale で DONE 配下に移動していること
        stale_files = [f for f in os.listdir(server.DONE) if f.endswith(".stale")]
        self.assertTrue(any(fname_stale in f for f in stale_files))

    # --------------------------------------------------------------------------
    # 7. ループ巻き込み防止テスト（4箇所すべての break 確認）
    # --------------------------------------------------------------------------
    def test_outbox_unconditional_break_all_loops(self):
        """保留発言が複数存在する場合でも、1回の応答で処理されるのは常に先頭の1件のみであること"""
        # 7a: 通常応答 (normal reply)
        s1 = self._create_test_session()
        sid1 = s1["id"]
        u1 = self.add_user_msg(s1, "発言1", status="pending")
        u2 = self.add_user_msg(s1, "発言2", status="pending")
        server.save(s1)

        with open(os.path.join(server.OUTBOX, f"{sid1}_reply1.json"), "w", encoding="utf-8") as f:
            json.dump({"session": sid1, "text": "発言1への回答"}, f)

        server.process_outbox()
        s1_after = server.load(sid1)
        self.assertEqual(server.find_msg(s1_after, u1["id"]).get("status"), "answered")
        self.assertEqual(server.find_msg(s1_after, u2["id"]).get("status"), "pending", "2件目の保留は維持されるべき")

        # 7b: 拒否応答 (refusal reply)
        s2 = self._create_test_session()
        sid2 = s2["id"]
        u21 = self.add_user_msg(s2, "発言21", status="pending")
        u22 = self.add_user_msg(s2, "発言22", status="pending")
        server.save(s2)

        with open(os.path.join(server.OUTBOX, f"{sid2}_refusal.json"), "w", encoding="utf-8") as f:
            json.dump({"session": sid2, "text": "I cannot fulfill this request."}, f)

        server.process_outbox()
        s2_after = server.load(sid2)
        self.assertEqual(server.find_msg(s2_after, u21["id"]).get("status"), "error")
        self.assertEqual(server.find_msg(s2_after, u22["id"]).get("status"), "pending", "2件目の保留は維持されるべき")

        # 7c: 空応答 (empty reply)
        s3 = self._create_test_session()
        sid3 = s3["id"]
        u31 = self.add_user_msg(s3, "発言31", status="pending")
        u32 = self.add_user_msg(s3, "発言32", status="pending")
        server.save(s3)

        with open(os.path.join(server.OUTBOX, f"{sid3}_empty.json"), "w", encoding="utf-8") as f:
            json.dump({"session": sid3, "text": ""}, f)

        server.process_outbox()
        s3_after = server.load(sid3)
        self.assertEqual(server.find_msg(s3_after, u31["id"]).get("error_retries"), 1)
        self.assertEqual(server.find_msg(s3_after, u32["id"]).get("error_retries"), None, "2件目の保留は巻き込まれないべき")

        # 7d: 破損JSON (.bad) で対象メッセージIDが特定できる場合
        s4 = self._create_test_session()
        sid4 = s4["id"]
        u41 = self.add_user_msg(s4, "発言41", status="pending")
        u42 = self.add_user_msg(s4, "発言42", status="pending")
        server.save(s4)

        bad_path = os.path.join(server.OUTBOX, f"{sid4}_broken_{u41['id']}.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("{invalid json...")
        # 10秒以上前のタイムスタンプに設定（.bad 判定条件）
        past = time.time() - 20
        os.utime(bad_path, (past, past))

        server.process_outbox()
        s4_after = server.load(sid4)
        self.assertEqual(server.find_msg(s4_after, u41["id"]).get("error_retries"), 1)
        self.assertEqual(server.find_msg(s4_after, u42["id"]).get("error_retries"), None, "特定発言宛ての破損JSONでは2件目は巻き込まれないこと")

    # --------------------------------------------------------------------------
    # 8. 未応答ガード（応答待ち状態でのメッセージ変更エンドポイントブロック）
    # --------------------------------------------------------------------------
    def test_mutation_endpoints_blocked_when_pending(self):
        """保留発言が存在する間は edit, regenerate, truncate, alter, delete がすべて 409 で拒絶されること"""
        s = self._create_test_session()
        sid = s["id"]

        # 過去の会話ターン
        u_old = self.add_user_msg(s, "過去の発言", status="answered")
        a_old = {"id": server.new_id(), "role": "assistant", "text": "過去の回答", "ts": server.now()}
        s["messages"].append(a_old)

        # 現在の未応答発言
        u_cur = self.add_user_msg(s, "未応答の発言", status="pending")
        server.save(s)

        # 8a: POST /api/message/edit
        h_edit = self._post("/api/message/edit", {"sid": sid, "id": u_old["id"], "text": "編集後のテキスト"})
        self.assertEqual(h_edit.status, 409)
        self.assertEqual(h_edit.result.get("code"), "pending_turn_exists")

        # 8b: POST /api/message/regenerate
        h_regen = self._post("/api/message/regenerate", {"sid": sid, "id": a_old["id"]})
        self.assertEqual(h_regen.status, 409)
        self.assertEqual(h_regen.result.get("code"), "pending_turn_exists")

        # 8c: POST /api/message/truncate (drop=False)
        h_trunc = self._post("/api/message/truncate", {"sid": sid, "id": u_old["id"]})
        self.assertEqual(h_trunc.status, 409)
        self.assertEqual(h_trunc.result.get("code"), "pending_turn_exists")

        # 8d: POST /api/message/truncate (drop=True)
        h_drop = self._post("/api/message/truncate", {"sid": sid, "id": u_old["id"], "drop": True})
        self.assertEqual(h_drop.status, 409)
        self.assertEqual(h_drop.result.get("code"), "pending_turn_exists")

        # 8e: POST /api/message/alter
        h_alter = self._post("/api/message/alter", {"sid": sid, "id": a_old["id"], "text": "改変テキスト"})
        self.assertEqual(h_alter.status, 409)
        self.assertEqual(h_alter.result.get("code"), "pending_turn_exists")

        # 8f: POST /api/delete
        h_del = self._post("/api/delete", {"sid": sid, "id": a_old["id"]})
        self.assertEqual(h_del.status, 409)
        self.assertEqual(h_del.result.get("code"), "pending_turn_exists")

        # 8g: POST /api/message/regenerate-choices
        h_choices = self._post("/api/message/regenerate-choices", {"sid": sid, "id": a_old["id"]})
        self.assertEqual(h_choices.status, 409)
        self.assertEqual(h_choices.result.get("code"), "pending_turn_exists")

        # 会話データが一切変更されていないことを検証
        s_after = server.load(sid)
        self.assertEqual(len(s_after["messages"]), 3)
        self.assertEqual(s_after["messages"][0]["text"], "過去の発言")
        self.assertEqual(s_after["messages"][1]["text"], "過去の回答")
        self.assertEqual(s_after["messages"][2]["text"], "未応答の発言")

    # --------------------------------------------------------------------------
    # 9. 保留解消後の各変更エンドポイントの正常動作確認
    # --------------------------------------------------------------------------
    def test_mutation_endpoints_succeed_when_not_pending(self):
        """保留が解消（answered）された状態では各エンドポイントが正常に動作すること"""
        # 9a: edit
        s1 = self._create_test_session()
        sid1 = s1["id"]
        u1 = self.add_user_msg(s1, "発言1", status="answered")
        server.save(s1)
        h_edit = self._post("/api/message/edit", {"sid": sid1, "id": u1["id"], "text": "編集後"})
        self.assertEqual(h_edit.status, 200)

        # 9b: alter
        s2 = self._create_test_session()
        sid2 = s2["id"]
        self.add_user_msg(s2, "発言2", status="answered")
        a2 = {"id": server.new_id(), "role": "assistant", "text": "AI発言2", "ts": server.now()}
        s2["messages"].append(a2)
        server.save(s2)
        h_alter = self._post("/api/message/alter", {"sid": sid2, "id": a2["id"], "text": "改変後"})
        self.assertEqual(h_alter.status, 200)

        # 9c: regenerate
        s3 = self._create_test_session()
        sid3 = s3["id"]
        self.add_user_msg(s3, "発言3", status="answered")
        a3 = {"id": server.new_id(), "role": "assistant", "text": "AI発言3", "ts": server.now()}
        s3["messages"].append(a3)
        server.save(s3)
        h_regen = self._post("/api/message/regenerate", {"sid": sid3, "id": a3["id"]})
        self.assertEqual(h_regen.status, 200)

        # 9d: truncate (drop=True)
        s4 = self._create_test_session()
        sid4 = s4["id"]
        u4 = self.add_user_msg(s4, "発言4", status="answered")
        server.save(s4)
        h_trunc = self._post("/api/message/truncate", {"sid": sid4, "id": u4["id"], "drop": True})
        self.assertEqual(h_trunc.status, 200)

        # 9e: delete
        s5 = self._create_test_session()
        sid5 = s5["id"]
        u5 = self.add_user_msg(s5, "発言5", status="answered")
        server.save(s5)
        h_del = self._post("/api/delete", {"sid": sid5, "id": u5["id"]})
        self.assertEqual(h_del.status, 200)

    # --------------------------------------------------------------------------
    # 10. 大量並行バースト（10スレッド）テスト
    # --------------------------------------------------------------------------
    def test_concurrent_send_burst_10(self):
        """同一セッションへ同時に 10 件の POST /api/send を同時発行した場合、成功 1 件、失敗 9 件 (409) になること"""
        s = self._create_test_session()
        sid = s["id"]

        barrier = threading.Barrier(10)
        results = []

        def worker(idx):
            barrier.wait()
            h = FakeHandler("/api/send", {"sid": sid, "text": f"Burst send {idx}"})
            server.H.do_POST(h)
            results.append(h)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r.status == 200]
        conflicts = [r for r in results if r.status == 409]

        self.assertEqual(len(successes), 1, "Exactly 1 burst send should succeed")
        self.assertEqual(len(conflicts), 9, "Remaining 9 burst sends should be rejected with 409")
        for c in conflicts:
            self.assertEqual(c.result.get("code"), "pending_turn_exists")

        s_after = server.load(sid)
        user_msgs = [m for m in s_after["messages"] if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), 1, "Only 1 user message should exist in session")

    # --------------------------------------------------------------------------
    # 11. DoS回避（局所 lock 解放後に send_json が呼ばれていることの検証）
    # --------------------------------------------------------------------------
    def test_lock_not_held_during_send_json(self):
        """/api/send, /api/send/stop 等の局所 with lock: 解放後に send_json が実行されること（再帰カウントが2にならないこと）"""
        s = self._create_test_session()
        sid = s["id"]

        # 1. 正常送信時 (200)
        h_send200 = self._post("/api/send", {"sid": sid, "text": "ロック検査送信"})
        self.assertEqual(h_send200.status, 200)
        # do_POST 外枠の 1 以外に、局所 lock のネスト (2以上) がないこと
        self.assertEqual(h_send200.lock_recursion, 1, "send_json must be executed outside the inner with lock (200)")

        # 2. 拒絶時 (409 pending_turn_exists)
        h_send409 = self._post("/api/send", {"sid": sid, "text": "2回目送信"})
        self.assertEqual(h_send409.status, 409)
        self.assertEqual(h_send409.lock_recursion, 1, "send_json must be executed outside the inner with lock (409)")

        # 3. 停止時 (200)
        h_stop = self._post("/api/send/stop", {"sid": sid})
        self.assertEqual(h_stop.status, 200)
        self.assertEqual(h_stop.lock_recursion, 1, "send_json must be executed outside the inner with lock (stop 200)")

    # --------------------------------------------------------------------------
    # 12. アンダースコア入りセッションIDでの破損JSON (.bad) 復旧テスト
    # --------------------------------------------------------------------------
    def test_bad_json_with_underscore_session(self):
        """アンダースコアを含むセッションIDでも、破損JSONが正しく照合され repoke / retry されること"""
        s_target = self._create_test_session(sid="custom_sess_99", title="Custom Underscore")
        u = self.add_user_msg(s_target, "破損テスト入力", status="pending")
        server.save(s_target)

        bad_path = os.path.join(server.OUTBOX, f"{s_target['id']}_reply_9999.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("{broken json content...")
        past = time.time() - 20
        os.utime(bad_path, (past, past))

        server.process_outbox()

        s_after = server.load("custom_sess_99")
        u_after = server.find_msg(s_after, u["id"])
        self.assertEqual(u_after.get("error_retries"), 1)
        self.assertEqual(u_after.get("status"), "pending")

    # --------------------------------------------------------------------------
    # 13. クライアント側（app.js）の静的ガード検証
    # --------------------------------------------------------------------------
    def test_app_js_source_guards(self):
        """app.js が 409 pending_turn_exists を適切にハンドリングし、再生成にも未応答ガードが入っていること"""
        app_js_path = os.path.join(os.path.dirname(__file__), "..", "public", "app.js")
        with open(app_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        # pending_turn_exists のハンドリングが含まれていること
        self.assertIn("pending_turn_exists", content)

        # /api/send の直前で無条件に progressHistory = [] を消していないこと（post の後に移動していること）
        send_idx = content.find('post("/api/send"')
        self.assertGreater(send_idx, 0)
        # post 前の 200 文字に progressHistory = [] が含まれていないこと
        pre_send = content[max(0, send_idx - 250):send_idx]
        self.assertNotIn("progressHistory = [];", pre_send)

        # 再生成メニューに busyTurn ガードが含まれていること
        regen_idx = content.find('"🔄 再生成"')
        self.assertGreater(regen_idx, 0)
        regen_block = content[regen_idx:regen_idx + 350]
        self.assertIn("busyTurn", regen_block)

        # 削除・選択肢再生成・改変に busyTurn ガードが含まれていること
        del_idx = content.find('"🗑️ 削除"')
        self.assertGreater(del_idx, 0)
        self.assertIn("busyTurn", content[del_idx:del_idx + 350])

        choices_regen_idx = content.find('function startChoicesRegen(')
        self.assertGreater(choices_regen_idx, 0)
        self.assertIn("busyTurn", content[choices_regen_idx:choices_regen_idx + 350])

        self.assertIn('toast("応答中は改変できません。停止してから操作してください。"', content)

    # --------------------------------------------------------------------------
    # 14. 複数タブ競合（/api/send/stop と /api/send の同時発行）テスト
    # --------------------------------------------------------------------------
    def test_stop_concurrent_with_send(self):
        """別タブからの /api/send/stop と /api/send がミリ秒単位で同時に走っても不整合が起きないこと"""
        s = self._create_test_session()
        sid = s["id"]
        u1 = self.add_user_msg(s, "停止対象の発言", status="pending")
        server.save(s)

        barrier = threading.Barrier(2)
        results = {}

        def do_stop():
            barrier.wait()
            h = FakeHandler("/api/send/stop", {"sid": sid, "id": u1["id"]})
            server.H.do_POST(h)
            results["stop"] = h

        def do_send():
            barrier.wait()
            h = FakeHandler("/api/send", {"sid": sid, "text": "新規並行送信"})
            server.H.do_POST(h)
            results["send"] = h

        t1 = threading.Thread(target=do_stop)
        t2 = threading.Thread(target=do_send)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        h_stop = results["stop"]
        h_send = results["send"]

        s_after = server.load(sid)
        pending_msgs = [m for m in s_after["messages"] if m.get("role") == "user" and m.get("status") in ("pending", "processing")]
        self.assertLessEqual(len(pending_msgs), 1, "保留中メッセージは絶対に2件以上存在してはならない")

        if h_stop.status == 200 and h_send.status == 200:
            # stop が先に完了し、その後に send が通った場合
            self.assertEqual(len(pending_msgs), 1)
            self.assertEqual(pending_msgs[0]["text"], "新規並行送信")
        elif h_stop.status == 200 and h_send.status == 409:
            # send が先に保留チェックに引っかかり 409、その後に stop が発言を削除した場合
            self.assertEqual(h_send.result.get("code"), "pending_turn_exists")
            self.assertEqual(len(pending_msgs), 0)
        else:
            self.fail(f"予期しないステータスの組み合わせ: stop={h_stop.status}, send={h_send.status}")



if __name__ == "__main__":
    unittest.main()
