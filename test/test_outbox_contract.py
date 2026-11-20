# -*- coding: utf-8 -*-
"""test/test_outbox_contract.py: outbox ターン確定の契約テスト (P6)"""
import json
import os
import re
import time
from unittest.mock import patch

import server
from test.support import IsolatedTestCase


class TestOutboxContract(IsolatedTestCase):
    """process_outbox() によるターン確定処理の振る舞い・副作用・冪等性を検証する"""

    def _create_user_session(self, setting_override=None, status="processing"):
        st = {"ide_session": "mock_ide_conv"}
        if setting_override:
            st.update(setting_override)
        s = server.create_session(st)
        u = server.add_user_msg(s, "こんにちは", status=status)
        server.save(s)
        return s, u

    def test_happy_path_side_effects(self):
        """Case 1: 正常系での user 発言 answered 化、assistant 追加、DONE 移動"""
        s, u = self._create_user_session()
        sid = s["id"]
        out_fname = "reply_case1.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        rep_data = {"session": sid, "reply_to": u["id"], "text": "はい、こんにちは！"}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rep_data, f)

        res = server.process_outbox()
        self.assertTrue(res, "process_outbox should return True on success")

        s_after = server.load(sid)
        # 1. user 発言が answered に変化
        u_after = server.find_msg(s_after, u["id"])
        self.assertEqual(u_after.get("status"), "answered")

        # 2. assistant 発言が追加されている
        asst = s_after["messages"][-1]
        self.assertEqual(asst.get("role"), "assistant")
        self.assertEqual(asst.get("text"), "はい、こんにちは！")

        # 3. outbox ファイルが DONE/<ts>_<name> へ移動している
        self.assertFalse(os.path.exists(out_path), "Outbox file should no longer exist in OUTBOX")
        done_files = [f for f in os.listdir(server.DONE) if f.endswith(out_fname)]
        self.assertEqual(len(done_files), 1, f"Expected exactly one done file for {out_fname}")
        self.assertTrue(
            bool(re.match(rf"^\d+_{re.escape(out_fname)}$", done_files[0])),
            f"Done file name does not match <ts>_<name> pattern: {done_files[0]}"
        )

    def test_turn_counters(self):
        """Case 2: 正常完了時の turns_in_period, time_last_turn 等が正しく更新されること"""
        # 2a: 時間進行なしの場合（turns_in_period + 1, time_last_turn は維持）
        s, u = self._create_user_session(setting_override={"turns_in_period": 0, "time_last_turn": 0})
        sid = s["id"]
        out_fname = "reply_case2.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": "応答です"}, f)

        res = server.process_outbox()
        self.assertTrue(res)

        s_after = server.load(sid)
        st = s_after["setting"]
        self.assertEqual(st.get("turns_in_period"), 1)
        self.assertEqual(st.get("time_last_turn"), 0)
        self.assertIn("total_turns", st)

        # 2b: 時間進行ありの場合（turns_in_period は 0 にリセット、time_last_turn は更新される）
        u2 = server.add_user_msg(s_after, "翌朝になった", status="processing")
        server.save(s_after)
        out_fname2 = "reply_case2_advance.json"
        out_path2 = os.path.join(server.OUTBOX, out_fname2)
        with open(out_path2, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": u2["id"],
                "text": "おはようございます",
                "elapsed_time": {"day": 2, "period": "朝"}
            }, f)

        res2 = server.process_outbox()
        self.assertTrue(res2)

        s_after2 = server.load(sid)
        st2 = s_after2["setting"]
        self.assertEqual(st2.get("turns_in_period"), 0, "turns_in_period must reset to 0 on clock advance")
        self.assertEqual(st2.get("time_last_turn"), 2, "time_last_turn must update to current turn on clock advance")

    def test_idempotent_double_process(self):
        """Case 3: 同一 reply_to の応答再投入時に .stale に移動し二重発言を防止すること"""
        s, u = self._create_user_session()
        sid = s["id"]
        out_fname = "reply_case3.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        # 1回目の処理（正常完了）
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": "応答その1"}, f)
        res1 = server.process_outbox()
        self.assertTrue(res1)
        count_after_first = len(server.load(sid)["messages"])

        # 2回目の処理（同一 reply_to の別ファイルを投入）
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": u["id"], "text": "重複応答"}, f)
        res2 = server.process_outbox()
        self.assertFalse(res2, "Duplicate process_outbox should return False")

        # 発言件数が増加していないこと
        count_after_second = len(server.load(sid)["messages"])
        self.assertEqual(count_after_first, count_after_second, "Message count should not increase on duplicate outbox")

        # DONE/<name>.stale に移動していること
        stale_path = os.path.join(server.DONE, out_fname + ".stale")
        self.assertTrue(os.path.exists(stale_path), f"Expected stale file at {stale_path}")

    def test_image_queue(self):
        """Case 4: image_mode=always かつ image を含む場合 queued_images に登録されること（off の場合は登録されない）"""
        # 4a: image_mode=always の場合
        s, u = self._create_user_session(setting_override={"image_mode": "always"})
        sid = s["id"]
        out_fname = "reply_case4.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": u["id"],
                "text": "画像生成付き応答",
                "image": {"prompt": "masterpiece, 1girl, smiling"}
            }, f)

        res = server.process_outbox()
        self.assertTrue(res)

        s_after = server.load(sid)
        asst = s_after["messages"][-1]

        queued = list(server.queued_images)
        self.assertIn((sid, asst["id"]), queued, f"Queued images should contain ({sid}, {asst['id']})")
        self.assertTrue(server.image_event.is_set(), "image_event must be set")

        # 4b: image_mode=off の場合（キューに追加されないこと）
        server.queued_images.clear()
        server.image_event.clear()
        s_off, u_off = self._create_user_session(setting_override={"image_mode": "off"})
        out_fname_off = "reply_case4_off.json"
        out_path_off = os.path.join(server.OUTBOX, out_fname_off)
        with open(out_path_off, "w", encoding="utf-8") as f:
            json.dump({
                "session": s_off["id"],
                "reply_to": u_off["id"],
                "text": "画像なし設定での応答",
                "image": {"prompt": "1girl"}
            }, f)
        res_off = server.process_outbox()
        self.assertTrue(res_off)
        self.assertEqual(len(server.queued_images), 0, "No image should be queued when image_mode is off")

    def test_rp_state_update(self):
        """Case 5: rp_state=True のとき assistant 発言にキャラ状態 (state) が付与されること（False では付与されない）"""
        # 5a: rp_state=True の場合
        s, u = self._create_user_session(setting_override={"rp_state": True})
        sid = s["id"]
        out_fname = "reply_case5.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": u["id"],
                "text": "状態更新付き応答",
                "state": [{"name": "Alice", "pose": "standing", "expression": ["smile"]}]
            }, f)

        res = server.process_outbox()
        self.assertTrue(res)

        s_after = server.load(sid)
        asst = s_after["messages"][-1]
        self.assertIsNotNone(asst.get("state"), "Assistant message must contain state")
        self.assertTrue(any(c.get("name") == "Alice" for c in asst["state"]), "State should contain Alice")

        # 5b: rp_state=False の場合（state は付与されない）
        s_nostate, u_nostate = self._create_user_session(setting_override={"rp_state": False})
        out_fname_nostate = "reply_case5_nostate.json"
        out_path_nostate = os.path.join(server.OUTBOX, out_fname_nostate)
        with open(out_path_nostate, "w", encoding="utf-8") as f:
            json.dump({
                "session": s_nostate["id"],
                "reply_to": u_nostate["id"],
                "text": "状態無効での応答",
                "state": [{"name": "Bob", "pose": "sitting"}]
            }, f)
        res_nostate = server.process_outbox()
        self.assertTrue(res_nostate)
        asst_nostate = server.load(s_nostate["id"])["messages"][-1]
        self.assertIsNone(asst_nostate.get("state"), "Assistant message must not contain state when rp_state is False")

    def test_unknown_destination_dropped(self):
        """Case 6: 存在しない session ID は .nosession へ、存在しない reply_to は .stale へ移動すること"""
        # 1. 存在しない session ID
        out_nosess = "nosess.json"
        path_nosess = os.path.join(server.OUTBOX, out_nosess)
        with open(path_nosess, "w", encoding="utf-8") as f:
            json.dump({"session": "non_existent_sid_9999", "reply_to": "u_fake", "text": "孤児応答"}, f)

        res1 = server.process_outbox()
        self.assertFalse(res1)
        nosess_done = os.path.join(server.DONE, out_nosess + ".nosession")
        self.assertTrue(os.path.exists(nosess_done), f"Expected nosession file at {nosess_done}")

        # 2. 存在しない reply_to（かつ pending な発言がないセッション）
        s = server.create_session({"ide_session": "mock_ide_conv"})
        server.save(s)
        out_stale = "stale_reply.json"
        path_stale = os.path.join(server.OUTBOX, out_stale)
        with open(path_stale, "w", encoding="utf-8") as f:
            json.dump({"session": s["id"], "reply_to": "u_missing", "text": "宛先不明応答"}, f)

        res2 = server.process_outbox()
        self.assertFalse(res2)
        stale_done = os.path.join(server.DONE, out_stale + ".stale")
        self.assertTrue(os.path.exists(stale_done), f"Expected stale file at {stale_done}")

        # 3. pending な発言はあるが、reply_to が一致しない応答（未応答発言があっても宛先不一致で .stale へ退避）
        s3, u3 = self._create_user_session()
        out_mismatch = "mismatch_reply.json"
        path_mismatch = os.path.join(server.OUTBOX, out_mismatch)
        with open(path_mismatch, "w", encoding="utf-8") as f:
            json.dump({"session": s3["id"], "reply_to": "u_different_id", "text": "宛先不一致応答"}, f)

        res3 = server.process_outbox()
        self.assertFalse(res3)
        mismatch_done = os.path.join(server.DONE, out_mismatch + ".stale")
        self.assertTrue(os.path.exists(mismatch_done), f"Expected mismatch stale file at {mismatch_done}")

    def test_memo_and_elapsed_time(self):
        """Case 7: memo 更新と assistant 発言への elapsed_time スナップショット記録"""
        s, u = self._create_user_session(setting_override={"elapsed_time": {"day": 3, "period": "昼"}})
        sid = s["id"]
        out_fname = "reply_case7.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": u["id"],
                "text": "メモと経過時間",
                "memo": "重要な出来事のあらすじメモ"
            }, f)

        res = server.process_outbox()
        self.assertTrue(res)

        s_after = server.load(sid)
        # 1. setting の memo が更新されていること
        self.assertEqual(s_after["setting"].get("memo"), "重要な出来事のあらすじメモ")

        # 2. assistant 発言に elapsed_time スナップショットが記録されていること
        asst = s_after["messages"][-1]
        self.assertIsNotNone(asst.get("elapsed_time"))
        self.assertEqual(asst["elapsed_time"].get("day"), 3)
        self.assertEqual(asst["elapsed_time"].get("period"), "昼")

    # -------------------------------------------------------------------------
    # F-01 契約テスト群: 読み込み失敗時の UnboundLocalError 及び セッション混入防止
    # -------------------------------------------------------------------------

    def test_f01_first_file_unreadable_no_unbound_error(self):
        """Case 1: 1件目のファイルが読み込み不可でも UnboundLocalError を起こさず .bad へ隔離され、ファイル名からリトライが実行されること"""
        s, u = self._create_user_session(status="processing")
        sid = s["id"]
        uid = u["id"]
        out_fname = f"{sid}_reply_{uid}.json"
        out_path = os.path.join(server.OUTBOX, out_fname)

        with open(out_path, "wb") as f:
            f.write(b"\x80\x81\x82\xff\xfe")

        os.utime(out_path, (time.time() - 20, time.time() - 20))

        res = server.process_outbox()
        self.assertTrue(res, "process_outbox should return True after moving bad file")

        self.assertFalse(os.path.exists(out_path), "File should no longer exist in OUTBOX")
        bad_path = os.path.join(server.DONE, out_fname + ".bad")
        self.assertTrue(os.path.exists(bad_path), "File should have moved to DONE as .bad")

        s_after = server.load(sid)
        u_after = server.find_msg(s_after, uid)
        self.assertEqual(u_after.get("status"), "pending")
        self.assertEqual(u_after.get("error_retries"), 1)

    def test_f01_broken_json_then_unreadable_file_no_cross_contamination(self):
        """Case 2: 1件目が破損JSON、2件目が不可読ファイルの場合に相互汚染なく両セッションそれぞれ正しくリトライされること"""
        s1, u1 = self._create_user_session(status="processing")
        s2, u2 = self._create_user_session(status="processing")

        if s1["id"] < s2["id"]:
            s_a, u_a = s1, u1
            s_b, u_b = s2, u2
        else:
            s_a, u_a = s2, u2
            s_b, u_b = s1, u1

        fname_a = f"{s_a['id']}_reply_{u_a['id']}.json"
        fname_b = f"{s_b['id']}_reply_{u_b['id']}.json"
        path_a = os.path.join(server.OUTBOX, fname_a)
        path_b = os.path.join(server.OUTBOX, fname_b)

        # File A: 読み込めるが JSON 構文破壊 (raw は取得できる)
        with open(path_a, "w", encoding="utf-8") as f:
            f.write(f'{{"session": "{s_a["id"]}", "reply_to": "{u_a["id"]}", "text": "破損')

        # File B: 不正バイナリで読み込み自体に失敗 (raw は取得できず空)
        with open(path_b, "wb") as f:
            f.write(b"\x80\x81\x82\xff\xfe")

        now = time.time()
        os.utime(path_a, (now - 20, now - 20))
        os.utime(path_b, (now - 20, now - 20))

        res = server.process_outbox()
        self.assertTrue(res)

        self.assertFalse(os.path.exists(path_a))
        self.assertFalse(os.path.exists(path_b))
        self.assertTrue(os.path.exists(os.path.join(server.DONE, fname_a + ".bad")))
        self.assertTrue(os.path.exists(os.path.join(server.DONE, fname_b + ".bad")))

        # 両セッションともそれぞれ1回だけリトライされること（セッション混入や2回加算がないこと）
        s_a_after = server.load(s_a["id"])
        s_b_after = server.load(s_b["id"])
        u_a_after = server.find_msg(s_a_after, u_a["id"])
        u_b_after = server.find_msg(s_b_after, u_b["id"])

        self.assertEqual(u_a_after.get("status"), "pending")
        self.assertEqual(u_a_after.get("error_retries"), 1)

        self.assertEqual(u_b_after.get("status"), "pending")
        self.assertEqual(u_b_after.get("error_retries"), 1)

    def test_f01_orphan_unreadable_file_dropped_to_bad(self):
        """Case 3: どのセッションにも属さない孤児不可読ファイルが安全に .bad へ移動しクラッシュしないこと"""
        fname = "orphan_unknown_reply_u1.json"
        path = os.path.join(server.OUTBOX, fname)
        with open(path, "wb") as f:
            f.write(b"\x80\x81\x82\xff\xfe")
        os.utime(path, (time.time() - 20, time.time() - 20))

        res = server.process_outbox()
        self.assertTrue(res)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(os.path.join(server.DONE, fname + ".bad")))

    def test_f01_young_unreadable_file_retained_in_outbox(self):
        """Case 4: 作成後10秒以内の不可読ファイルは OUTBOX に留まり、時期尚早なリトライ消費を行わないこと"""
        s, u = self._create_user_session(status="processing")
        fname = f"{s['id']}_reply_{u['id']}.json"
        path = os.path.join(server.OUTBOX, fname)
        with open(path, "wb") as f:
            f.write(b"\x80\x81\x82\xff\xfe")

        res = server.process_outbox()
        self.assertFalse(res)
        self.assertTrue(os.path.exists(path), "Young file should remain in OUTBOX")
        self.assertFalse(os.path.exists(os.path.join(server.DONE, fname + ".bad")))

        s_after = server.load(s["id"])
        u_after = server.find_msg(s_after, u["id"])
        self.assertEqual(u_after.get("status"), "processing")
        self.assertIsNone(u_after.get("error_retries"))

    def test_f01_unreadable_file_retry_escalation_to_error(self):
        """Case 5: 不可読ファイルが3回連続した場合、3回目で正しく status='error' に昇格すること"""
        s, u = self._create_user_session(status="processing")
        sid = s["id"]
        uid = u["id"]

        for i in range(1, 4):
            fname = f"{sid}_reply_{uid}_{i}.json"
            path = os.path.join(server.OUTBOX, fname)
            with open(path, "wb") as f:
                f.write(b"\x80\x81\x82\xff\xfe")
            os.utime(path, (time.time() - 20, time.time() - 20))

            server.process_outbox()

            s_cur = server.load(sid)
            u_cur = server.find_msg(s_cur, uid)
            if i < 3:
                self.assertEqual(u_cur.get("status"), "pending")
                self.assertEqual(u_cur.get("error_retries"), i)
            else:
                self.assertEqual(u_cur.get("status"), "error")
                self.assertEqual(u_cur.get("error_retries"), 3)
                self.assertIn("壊れていました", u_cur.get("error", ""))

    def test_f01_locked_file_safe_move_failure_no_retry_explosion(self):
        """Case 6: Windows排他ロック等で safe_move が失敗した場合、リトライを加算せず待機すること"""
        s, u = self._create_user_session(status="processing")
        sid = s["id"]
        uid = u["id"]
        fname = f"{sid}_reply_{uid}.json"
        path = os.path.join(server.OUTBOX, fname)

        with open(path, "wb") as f:
            f.write(b"\x80\x81\x82\xff\xfe")
        os.utime(path, (time.time() - 20, time.time() - 20))

        # 排他ロックで移動できない状況をシミュレート
        with patch("shutil.move", side_effect=PermissionError("Permission denied: file locked")):
            for _ in range(5):
                res = server.process_outbox()
                self.assertFalse(res)

                s_chk = server.load(sid)
                u_chk = server.find_msg(s_chk, uid)
                self.assertEqual(u_chk.get("status"), "processing", "Status should not prematurely change while locked")
                self.assertIsNone(u_chk.get("error_retries"), "Retries must not be consumed while locked")
                self.assertTrue(os.path.exists(path), "File must remain in OUTBOX while locked")

        # ロック解除後の通常実行
        res = server.process_outbox()
        self.assertTrue(res)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(os.path.join(server.DONE, fname + ".bad")))

        s_final = server.load(sid)
        u_final = server.find_msg(s_final, uid)
        self.assertEqual(u_final.get("status"), "pending")
        self.assertEqual(u_final.get("error_retries"), 1)

    def test_f01_broken_json_missing_reply_to_resolves_cand_mid_from_filename(self):
        """Case 7: raw に session のみがあり reply_to が無い破損JSONでも、ファイル名から cand_mid を特定し対象発言のみリトライすること"""
        s = server.create_session({"ide_session": "test"})
        u1 = server.add_user_msg(s, "1通目", status="processing")
        u2 = server.add_user_msg(s, "2通目", status="processing")
        server.save(s)

        sid = s["id"]
        # u2 への応答ファイル
        fname = f"{sid}_reply_{u2['id']}.json"
        path = os.path.join(server.OUTBOX, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'{{"session": "{sid}", "text": "途中で途切れた構文エラーJSON')
        os.utime(path, (time.time() - 20, time.time() - 20))

        res = server.process_outbox()
        self.assertTrue(res)

        s_after = server.load(sid)
        m1 = server.find_msg(s_after, u1["id"])
        m2 = server.find_msg(s_after, u2["id"])

        # u1 は巻き添えを受けず processing のまま
        self.assertEqual(m1.get("status"), "processing")
        self.assertIsNone(m1.get("error_retries"))

        # u2 は正しくリトライ対象になる
        self.assertEqual(m2.get("status"), "pending")
        self.assertEqual(m2.get("error_retries"), 1)
