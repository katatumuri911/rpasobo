# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
import ide_poke
import memory_store

# serve_forever の待ち時間。既定の 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒ブロックする
_POLL = 0.02


class TestRotationPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_rot_pipe_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_images_dir = server.IMAGES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_outbox = server.OUTBOX
        self.orig_done = server.DONE
        self.orig_rag_dir = server.RAG_DIR
        self.orig_archive_dir = server.ARCHIVE_DIR
        self.orig_sent = server.SENT
        self.orig_trash = server.TRASH
        self.orig_memory_store_data = memory_store.DATA
        self.orig_ide_known = server.IDE_KNOWN
        self.orig_rotation_enabled = server.ROTATION_ENABLED
        # AGENTS_LOG と NEW_IDE_DIR はモジュール読み込み時に決まるので、DATA を差し替えても
        # 追従しない。差し替えないと、このテストが**本物の session/AGENTS.md と
        # data/agents_md.json を上書きしてしまう**（テストのセッションIDが実データに残る）。
        self.orig_agents_log = server.AGENTS_LOG
        self.orig_new_ide_dir = server.NEW_IDE_DIR

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        os.makedirs(server.IMAGES_DIR, exist_ok=True)
        server.STATES_DIR = os.path.join(server.DATA, "states")
        os.makedirs(server.STATES_DIR, exist_ok=True)
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        os.makedirs(server.CHOICES_DIR, exist_ok=True)
        server.SENT = os.path.join(server.DATA, "sent")
        os.makedirs(server.SENT, exist_ok=True)
        server.ARCHIVE_DIR = os.path.join(server.DATA, "archive")
        os.makedirs(server.ARCHIVE_DIR, exist_ok=True)
        server.TRASH = os.path.join(server.DATA, "trash")
        os.makedirs(server.TRASH, exist_ok=True)
        server.RAG_DIR = os.path.join(server.DATA, "rag")
        os.makedirs(server.RAG_DIR, exist_ok=True)
        server.IDE_KNOWN = os.path.join(server.DATA, "ide_known.json")
        server.known_ides = None
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.agents_state.clear()

        memory_store.DATA = server.DATA

        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(server.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.session_revs.clear()
        server.queued_images.clear()
        with server.rotation_guard:
            server.rotation_queue.clear()
        with server._new_ide_guard:
            server._new_ide_in_progress.clear()

        # HTTP サーバー起動
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=2)

        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.STATES_DIR = self.orig_states_dir
        server.CHOICES_DIR = self.orig_choices_dir
        server.OUTBOX = self.orig_outbox
        server.DONE = self.orig_done
        server.RAG_DIR = self.orig_rag_dir
        server.ARCHIVE_DIR = self.orig_archive_dir
        server.SENT = self.orig_sent
        server.TRASH = self.orig_trash
        server.IDE_KNOWN = self.orig_ide_known
        server.ROTATION_ENABLED = self.orig_rotation_enabled
        server.AGENTS_LOG = self.orig_agents_log
        server.NEW_IDE_DIR = self.orig_new_ide_dir
        server.agents_state.clear()
        server.known_ides = None
        memory_store.DATA = self.orig_memory_store_data

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _post(self, path, data):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server_port}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.server_port}{path}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _create_dummy_session(self, user_turns=0, ide_session="ide-test-1", asst_replies=True):
        st = {"title": "Test Session", "ide_session": ide_session, "ide_model": "test-model"}
        msgs = []
        for i in range(user_turns):
            mid_u = f"u_{i + 1}"
            msgs.append({"id": mid_u, "role": "user", "text": f"User msg {i + 1}", "status": "answered", "ts": server.now()})
            if asst_replies:
                mid_a = f"a_{i + 1}"
                msgs.append({"id": mid_a, "role": "assistant", "text": f"Asst msg {i + 1}", "ts": server.now()})
        s = server.create_session(st, msgs)
        s["setting"]["total_turns"] = user_turns
        server.save(s)
        return s

    # -------------------------------------------------------------
    # T1: rotation_maybe_arm が29で False / 30で True / 二重呼び出しで1回だけ
    # -------------------------------------------------------------
    def test_T1_rotation_maybe_arm(self):
        s = self._create_dummy_session(user_turns=29)
        self.assertFalse(server.rotation_maybe_arm(s))
        self.assertEqual(server.rotation_state(s), {})

        # 30ターン目を追加
        s["messages"].append({"id": "u_30", "role": "user", "text": "Turn 30", "status": "pending"})
        self.assertTrue(server.rotation_maybe_arm(s))
        r = server.rotation_state(s)
        self.assertEqual(r.get("phase"), "armed")
        self.assertEqual(r.get("armed_turn"), 30)
        self.assertEqual(r.get("rot_no"), 1)

        # 二重呼び出しで False
        self.assertFalse(server.rotation_maybe_arm(s))

    # -------------------------------------------------------------
    # T2: rotation_skipping 中は武装しない／非常停止（ROTATION_ENABLED=False）でも武装しない
    # -------------------------------------------------------------
    def test_T2_rotation_skipping_and_disabled(self):
        # 非常停止
        server.ROTATION_ENABLED = False
        s = self._create_dummy_session(user_turns=30)
        self.assertFalse(server.rotation_maybe_arm(s))
        self.assertEqual(server.rotation_state(s), {})
        server.ROTATION_ENABLED = True

        # スキップ期間中
        s2 = self._create_dummy_session(user_turns=30)
        s2["setting"]["rotation_skip_until"] = 33
        self.assertTrue(server.rotation_skipping(s2))
        self.assertFalse(server.rotation_maybe_arm(s2))
        self.assertEqual(server.rotation_state(s2), {})

    # -------------------------------------------------------------
    # T3: rotation_note_body で 未応答があっても archiving へ進む（idle 要求は cutover のみ）
    # -------------------------------------------------------------
    def test_T3_rotation_note_body_proceeds_even_with_pending(self):
        s = self._create_dummy_session(user_turns=30)
        # ユーザー発言に pending が残っている状態
        s["messages"][-1]["status"] = "pending"
        server.save(s)

        server.rotation_note_body(s["id"])
        s_loaded = server.load(s["id"])
        r = server.rotation_state(s_loaded)
        self.assertEqual(r.get("phase"), "archiving")
        self.assertTrue(r.get("settle_since"))

    # -------------------------------------------------------------
    # T4: アーカイブに rotation / rotation_notice / rotation_reserve が無い
    # -------------------------------------------------------------
    def test_T4_archive_strips_rotation_fields(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "archiving",
            "rot_no": 1,
            "old_ide": "ide-1",
            "attempts": {}
        }
        s["setting"]["rotation_notice"] = "通知テキスト"
        s["setting"]["rotation_skip_until"] = 33
        s["setting"]["rotation_reserve"] = {"ide": "res-1"}
        server.save(s)

        delay = server._rot_archiving(s["id"])
        self.assertEqual(delay, 0.0)

        s_loaded = server.load(s["id"])
        arc_path = s_loaded["setting"]["rotation"].get("archive")
        self.assertTrue(os.path.exists(arc_path))

        with open(arc_path, "r", encoding="utf-8") as f:
            arc_data = json.load(f)

        ast = arc_data["setting"]
        self.assertNotIn("rotation", ast)
        self.assertNotIn("rotation_notice", ast)
        self.assertNotIn("rotation_skip_until", ast)
        self.assertNotIn("rotation_reserve", ast)

    # -------------------------------------------------------------
    # T5: _rot_archiving を2回呼んでもファイルが増えない・内容が壊れない
    # -------------------------------------------------------------
    def test_T5_rot_archiving_idempotent(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {"phase": "archiving", "rot_no": 1, "old_ide": "ide-1"}
        server.save(s)

        server._rot_archiving(s["id"])
        arc_files_1 = os.listdir(server.ARCHIVE_DIR)
        self.assertEqual(len(arc_files_1), 1)

        # 2回目呼び出し
        server.rotation_put(s["id"], phase="archiving")
        server._rot_archiving(s["id"])
        arc_files_2 = os.listdir(server.ARCHIVE_DIR)
        self.assertEqual(len(arc_files_2), 1)

    # -------------------------------------------------------------
    # T6: prune_session_messages 後も total_turns が変わらない／calc_turn_idx が連番
    # -------------------------------------------------------------
    def test_T6_prune_preserves_total_turns(self):
        s = self._create_dummy_session(user_turns=35)
        self.assertEqual(s["setting"]["total_turns"], 35)

        server.prune_session_messages(s, keep_turns=server.ROTATION_KEEP_TURNS)
        user_msgs = [m for m in s["messages"] if m.get("role") == "user"]
        self.assertEqual(len(user_msgs), server.ROTATION_KEEP_TURNS)
        # I12: total_turns は触らない
        self.assertEqual(s["setting"]["total_turns"], 35)

        # calc_turn_idx による番号の連続性
        st_total = s["setting"]["total_turns"]
        idx_last = server.calc_turn_idx(s["messages"], len(s["messages"]) - 1, st_total)
        self.assertEqual(idx_last, 35)

    # -------------------------------------------------------------
    # T7: /api/send → total_turns が +1（単調増加）
    # -------------------------------------------------------------
    def test_T7_send_increments_total_turns(self):
        s = self._create_dummy_session(user_turns=10)
        s["setting"]["total_turns"] = 30
        server.save(s)

        resp = self._post("/api/send", {"sid": s["id"], "text": "Next message"})
        self.assertTrue(resp.get("ok"))

        s_loaded = server.load(s["id"])
        self.assertEqual(s_loaded["setting"]["total_turns"], 31)

    # -------------------------------------------------------------
    # T8: total_turns=0＋rotation_count=2＋in_file=11 で send → 51 (total_turns_base)
    # -------------------------------------------------------------
    def test_T8_total_turns_base_fallback(self):
        s = self._create_dummy_session(user_turns=10)
        s["setting"]["total_turns"] = 0
        s["setting"]["rotation_count"] = 2
        server.save(s)

        # 11通目を送信 -> prev_total は 2 * 20 + 10 = 50, 新規は max(50, 10) + 1 = 51
        resp = self._post("/api/send", {"sid": s["id"], "text": "11th message"})
        self.assertTrue(resp.get("ok"))

        s_loaded = server.load(s["id"])
        self.assertEqual(s_loaded["setting"]["total_turns"], 51)

    # -------------------------------------------------------------
    # T9: truncate で total_turns が removed 分だけ戻る（0未満にならない）
    # -------------------------------------------------------------
    def test_T9_truncate_decrements_total_turns(self):
        s = self._create_dummy_session(user_turns=15)
        s["setting"]["total_turns"] = 35
        server.save(s)

        # 13番目のユーザー発言以降を削除（3ターン削除）
        target_u = [m for m in s["messages"] if m.get("role") == "user"][11]  # 12th user msg
        resp = self._post("/api/message/truncate", {"sid": s["id"], "id": target_u["id"]})
        self.assertTrue(resp.get("ok"))

        s_loaded = server.load(s["id"])
        # 15 - 12 = 3ターン削除 -> 35 - 3 = 32
        self.assertEqual(s_loaded["setting"]["total_turns"], 32)

    # -------------------------------------------------------------
    # T9b: drop=true の truncate（巻き戻し）はそのユーザー発言ごと消して本文・添付を返す
    # -------------------------------------------------------------
    def test_T9b_truncate_drop_removes_user_message(self):
        s = self._create_dummy_session(user_turns=15)
        s["setting"]["total_turns"] = 35
        target_u = [m for m in s["messages"] if m.get("role") == "user"][11]  # 12th user msg
        target_u["files"] = [{"name": "pic.png", "path": "p1.png", "type": "image/png", "size": 10}]
        server.save(s)

        resp = self._post("/api/message/truncate", {"sid": s["id"], "id": target_u["id"], "drop": True})
        self.assertTrue(resp.get("ok"))
        self.assertTrue(resp.get("drop"))
        self.assertEqual(resp.get("text"), target_u["text"])
        self.assertEqual([f["name"] for f in resp.get("files")], ["pic.png"])

        s_loaded = server.load(s["id"])
        ids = [m["id"] for m in s_loaded["messages"]]
        self.assertNotIn(target_u["id"], ids)          # その発言も消える
        self.assertEqual(ids[-1], "a_11")               # 直前の AI 応答までが残る
        self.assertEqual(len(ids), 22)
        self.assertEqual(s_loaded["setting"]["total_turns"], 31)  # 35 - 4（u_12〜u_15 を丸ごと消す）

    # -------------------------------------------------------------
    # T9c: drop=true はユーザー発言だけ / 応答待ちがある間は 409
    # -------------------------------------------------------------
    def test_T9c_truncate_drop_guards(self):
        s = self._create_dummy_session(user_turns=15)
        asst = [m for m in s["messages"] if m.get("role") == "assistant"][5]
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("/api/message/truncate", {"sid": s["id"], "id": asst["id"], "drop": True})
        self.assertEqual(cm.exception.code, 400)

        s = server.load(s["id"])
        target_u = [m for m in s["messages"] if m.get("role") == "user"][11]
        last_user = [m for m in s["messages"] if m.get("role") == "user"][-1]
        last_user["status"] = "processing"
        server.save(s)
        with self.assertRaises(urllib.error.HTTPError) as cm2:
            self._post("/api/message/truncate", {"sid": s["id"], "id": target_u["id"], "drop": True})
        self.assertEqual(cm2.exception.code, 409)
        s_loaded = server.load(s["id"])
        self.assertIn(target_u["id"], [m["id"] for m in s_loaded["messages"]])  # 消えていない

    # -------------------------------------------------------------
    # T9d: drop しない truncate は従来どおり（その発言は残る）
    # -------------------------------------------------------------
    def test_T9d_truncate_keeps_message_without_drop(self):
        s = self._create_dummy_session(user_turns=4)
        target_u = [m for m in s["messages"] if m.get("role") == "user"][1]
        resp = self._post("/api/message/truncate", {"sid": s["id"], "id": target_u["id"]})
        self.assertTrue(resp.get("ok"))
        s_loaded = server.load(s["id"])
        self.assertEqual([m["id"] for m in s_loaded["messages"]], ["u_1", "a_1", "u_2"])

    # -------------------------------------------------------------
    # T10: _rot_cutover は未応答があると切り替えない（I7）
    # -------------------------------------------------------------
    def test_T10_cutover_waits_for_idle(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "cutover",
            "rot_no": 1,
            "old_ide": "ide-old",
            "new_ide": "ide-new",
            "new_ide_title": "新会話",
        }
        s["setting"]["ide_session"] = "ide-old"
        # 未応答のユーザー発言
        s["messages"].append({"id": "u_pending", "role": "user", "text": "pending", "status": "processing"})
        server.save(s)

        delay = server._rot_cutover(s["id"])
        self.assertEqual(delay, 1.0)  # 待機

        s_loaded = server.load(s["id"])
        self.assertEqual(s_loaded["setting"]["ide_session"], "ide-old")
        self.assertEqual(server.rotation_phase(s_loaded), "cutover")

    # -------------------------------------------------------------
    # T11: rotation_idle が choices/memory pending を無視する
    # -------------------------------------------------------------
    def test_T11_rotation_idle_ignores_choices_and_memory(self):
        s = self._create_dummy_session(user_turns=10)
        s["messages"].append({
            "id": "a_ch", "role": "assistant", "text": "asst",
            "choices_status": "pending", "memory_status": "processing"
        })
        self.assertTrue(server.rotation_idle(s))

        # user pending があれば False
        s["messages"].append({"id": "u_pend", "role": "user", "status": "pending"})
        self.assertFalse(server.rotation_idle(s))

    # -------------------------------------------------------------
    # T12: _rot_verify_test: 空応答→リトライ / スペース＋known→合格 / after_step 前の返事は無視
    # -------------------------------------------------------------
    def test_T12_verify_test_logic(self):
        s = self._create_dummy_session(user_turns=30)
        cid = "conv-new-123"
        s["setting"]["rotation"] = {
            "phase": "verify_test",
            "rot_no": 1,
            "new_ide": cid,
            "old_model": "model-1",
            "attempts": {"verify": 1, "test_sent": True, "verify_poll": 0, "verify_after_step": 10}
        }
        server.save(s)

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server, "ide_is_known", return_value=True):

            # (1) 空応答 -> 待機
            with patch.object(server.ide_poke, "last_reply", return_value=("", "", "ok")):
                delay = server._rot_verify_test(s["id"])
                self.assertEqual(delay, server.ROT_VERIFY_POLL)

            # (2) after_step 以前の返事は last_reply が無視する設計
            # ide_poke.last_reply の after_step フィルタリング自体を検証
            mock_steps = [
                {"type": "PLANNER_RESPONSE", "plannerResponse": {"response": f"前の返事 {i}"}}
                for i in range(11)  # steps 0..10
            ] + [
                {"type": "PLANNER_RESPONSE", "plannerResponse": {"response": " "}}  # step 11 (> 10)
            ]
            with patch.object(server.ide_poke, "locate", return_value=(1234, "tok", {"stepCount": 15})), \
                 patch.object(server.ide_poke, "_call", return_value=(200, {"steps": mock_steps})):
                txt, err, status = server.ide_poke.last_reply(cid, after_step=10)
                self.assertEqual(txt, " ")
                # after_step=11 なら step 11 も除外されて空になる
                txt_empty, _, _ = server.ide_poke.last_reply(cid, after_step=11)
                self.assertEqual(txt_empty, "")

            # (3) 半角スペース＋known -> 合格して cutover へ
            with patch.object(server.ide_poke, "last_reply", return_value=(" ", "", "ok")):
                delay = server._rot_verify_test(s["id"])
                self.assertEqual(delay, 0.0)
                s_loaded = server.load(s["id"])
                self.assertEqual(server.rotation_phase(s_loaded), "cutover")

    # -------------------------------------------------------------
    # T12b: 新規会話（stepCount=0）でもブートストラップの返事で疎通テストを省略できる
    #       （旧: if b_step: で 0 が偽になり、必ず疎通テストが走っていた）
    # -------------------------------------------------------------
    def _verify_session(self, attempts=None):
        s = self._create_dummy_session(user_turns=3)
        s["setting"]["rotation"] = {
            "phase": "verify_test", "rot_no": 1, "new_ide": "conv-new-0",
            "old_model": "model-1",
            "attempts": attempts if attempts is not None else {
                "verify": 0, "test_sent": False, "bootstrap_after_step_main": 0
            },
        }
        server.save(s)
        return s

    def test_T12b_skip_test_when_bootstrap_step_is_zero(self):
        s = self._verify_session()
        calls = {"send": 0}

        def _boom(*a, **k):
            calls["send"] += 1
            raise AssertionError("疎通テストを送ってしまった（stepCount=0 の判定漏れ）")

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 0}), \
             patch.object(server.ide_poke, "last_reply", return_value=("準備完了", "", "ok")), \
             patch.object(server, "ide_is_known", return_value=True), \
             patch.object(server, "send_poke_text", side_effect=_boom):
            delay = server._rot_verify_test(s["id"])

        self.assertEqual(delay, 0.0)
        self.assertEqual(calls["send"], 0)
        self.assertEqual(server.rotation_phase(server.load(s["id"])), "cutover")

    # -------------------------------------------------------------
    # T12c: ブートストラップの返事が無いときは、従来どおり疎通テストを送る（フォールバック）
    # -------------------------------------------------------------
    def test_T12c_sends_test_when_bootstrap_reply_missing(self):
        s = self._verify_session()
        calls = {"send": 0, "text": ""}

        def _ok(cid, text, **k):
            calls["send"] += 1
            calls["text"] = text
            return True, "ok", False

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 0}), \
             patch.object(server.ide_poke, "last_reply", return_value=("", "", "ok")), \
             patch.object(server, "ide_is_known", return_value=True), \
             patch.object(server, "send_poke_text", side_effect=_ok):
            delay = server._rot_verify_test(s["id"])

        self.assertEqual(calls["send"], 1)
        self.assertIn("準備完了", calls["text"])
        self.assertEqual(delay, server.ROT_VERIFY_POLL)
        self.assertEqual(server.rotation_phase(server.load(s["id"])), "verify_test")

    # -------------------------------------------------------------
    # T12d: bootstrap_after_step の main / choices キーが分離されている（choices が main を上書きしない）
    # -------------------------------------------------------------
    def test_T12d_bootstrap_step_keys_are_separated(self):
        s = self._verify_session({
            "verify": 0, "test_sent": False,
            "bootstrap_after_step_main": 0, "bootstrap_after_step_choices": 7,
        })
        seen = {}

        def _lr(cid, after_step=0):
            seen["after_step"] = after_step
            return "準備完了", "", "ok"

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 9}), \
             patch.object(server.ide_poke, "last_reply", side_effect=_lr), \
             patch.object(server, "ide_is_known", return_value=True):
            server._rot_verify_test(s["id"])

        self.assertEqual(seen["after_step"], 0, "main の b_step で判定されていない（choices に上書きされている）")

    # -------------------------------------------------------------
    # T13: テスト送信失敗（retry=False）で verify が +1 され、ROT_VERIFY_ROUNDS で必ず rotation_fail
    # -------------------------------------------------------------
    def test_T13_verify_failure_limits_to_rounds(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "verify_test",
            "rot_no": 1,
            "new_ide": "conv-fail-1",
            "attempts": {"verify": 0, "test_sent": False}
        }
        server.save(s)

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 5}), \
             patch.object(server, "send_poke_text", return_value=(False, "send failed", False)):

            # 1回目失敗 -> verify=1, phase="spawn_main"
            server._rot_verify_test(s["id"])
            s_loaded = server.load(s["id"])
            self.assertEqual(s_loaded["setting"]["rotation"]["attempts"]["verify"], 1)
            self.assertEqual(server.rotation_phase(s_loaded), "spawn_main")

            # 2回目失敗 -> verify=2, ROT_VERIFY_ROUNDS(2) に達して rotation_fail
            s_loaded["setting"]["rotation"]["phase"] = "verify_test"
            s_loaded["setting"]["rotation"]["new_ide"] = "conv-fail-2"
            server.save(s_loaded)

            server._rot_verify_test(s["id"])
            s_final = server.load(s["id"])
            self.assertEqual(server.rotation_phase(s_final), "")  # fail で rotation 削除
            self.assertIn("⚠️", s_final["setting"].get("rotation_notice", ""))

    # -------------------------------------------------------------
    # T14: _rot_verify_retry は verify を 0 に戻さない (I10)
    # -------------------------------------------------------------
    def test_T14_verify_retry_preserves_attempts(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "verify_test",
            "rot_no": 1,
            "new_ide": "conv-drop-1",
            "attempts": {"verify": 1}
        }
        server.save(s)

        with patch.object(server.ide_poke, "rename") as mock_rename:
            server._rot_verify_retry(s["id"], "初回検証不合格")
            mock_rename.assert_called_once()

        s_loaded = server.load(s["id"])
        self.assertEqual(s_loaded["setting"]["rotation"]["attempts"]["verify"], 1)
        self.assertEqual(server.rotation_phase(s_loaded), "spawn_main")
        self.assertEqual(s_loaded["setting"]["rotation"]["new_ide"], "")

    # -------------------------------------------------------------
    # T15: _rot_spawn: 予備の ide だけ消費し choices_ide が残る／死んだ予備は消える／"既に作成中"で残機減らない
    # -------------------------------------------------------------
    def test_T15_rot_spawn_reserve_and_lock(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {"phase": "spawn_main", "rot_no": 1, "attempts": {"spawn": 0}}
        s["setting"]["rotation_reserve"] = {
            "ide": "reserve-main",
            "choices_ide": "reserve-choices",
            "title": "予備"
        }
        server.save(s)

        # 予備が生きていればそれを採用し、choices_ide だけ残す
        with patch.object(server.ide_poke, "locate", return_value=(1234, "tok", {})):
            server._rot_spawn(s["id"], "main")
            s_loaded = server.load(s["id"])
            self.assertEqual(s_loaded["setting"]["rotation"]["new_ide"], "reserve-main")
            self.assertEqual(s_loaded["setting"]["rotation_reserve"].get("choices_ide"), "reserve-choices")
            self.assertNotIn("ide", s_loaded["setting"]["rotation_reserve"])
            self.assertEqual(server.rotation_phase(s_loaded), "spawn_choices")

        # 死んだ予備は消える
        s_dead = self._create_dummy_session(user_turns=30)
        s_dead["setting"]["rotation"] = {"phase": "spawn_main", "rot_no": 1, "attempts": {"spawn": 0}}
        s_dead["setting"]["rotation_reserve"] = {"ide": "dead-main"}
        server.save(s_dead)

        with patch.object(server.ide_poke, "locate", return_value=(None, None, None)), \
             patch.object(server, "new_ide", return_value=("new-spawned", "新規", "ok", "folder")):
            server._rot_spawn(s_dead["id"], "main")
            s_dead_loaded = server.load(s_dead["id"])
            self.assertNotIn("rotation_reserve", s_dead_loaded["setting"])

        # "既に作成中" のときは残機を減らさない (I10)
        s_busy = self._create_dummy_session(user_turns=30)
        s_busy["setting"]["rotation"] = {"phase": "spawn_main", "rot_no": 1, "attempts": {"spawn": 1}}
        server.save(s_busy)

        with patch.object(server, "new_ide", return_value=(None, None, "既に作成中", None)):
            delay = server._rot_spawn(s_busy["id"], "main")
            self.assertEqual(delay, 2.0)
            s_busy_loaded = server.load(s_busy["id"])
            self.assertEqual(s_busy_loaded["setting"]["rotation"]["attempts"]["spawn"], 1)

    # -------------------------------------------------------------
    # T16: new_ide(assign=False) が ide_session を変えない (I3)
    # -------------------------------------------------------------
    def test_T16_new_ide_assign_false(self):
        s = self._create_dummy_session(user_turns=30, ide_session="current-ide")
        s["setting"]["rotation"] = {"phase": "spawn_main", "rot_no": 1}
        server.save(s)

        with patch.object(server.ide_poke, "new_conversation", return_value=("new-cid-999", "ok")):
            cid, title, msg, folder = server.new_ide(s["id"], assign=False)
            self.assertEqual(cid, "new-cid-999")

        s_loaded = server.load(s["id"])
        # ide_session は依然として旧会話のまま！ (I3)
        self.assertEqual(s_loaded["setting"]["ide_session"], "current-ide")
        # rotation 内に記録されている
        self.assertEqual(s_loaded["setting"]["rotation"]["new_ide"], "new-cid-999")

    # -------------------------------------------------------------
    # T17: _new_ide_in_progress が (sid, target) で排他（assign 違いでも同時に作らない）
    # -------------------------------------------------------------
    def test_T17_new_ide_guard_excludes_assign(self):
        s = self._create_dummy_session()
        key = (s["id"], "main")

        with server._new_ide_guard:
            server._new_ide_in_progress.add(key)

        try:
            cid, title, msg, folder = server.new_ide(s["id"], assign=False)
            self.assertIsNone(cid)
            self.assertEqual(msg, "既に作成中")
        finally:
            with server._new_ide_guard:
                server._new_ide_in_progress.discard(key)

    # -------------------------------------------------------------
    # T18: write_sent の reset が build_context で消えない／note_reply で消える
    # -------------------------------------------------------------
    def test_T18_write_sent_reset_lifecycle(self):
        s = self._create_dummy_session(user_turns=1)
        m = s["messages"][0]

        # reset=True で保存
        server.write_sent(s["id"], {"ide": "ide-1", "msgs": [], "reset": True, "why": "ローテーション"})

        # build_context 実行
        ctx = server.build_context(s, m)
        self.assertEqual(ctx["mode"], "full")

        # 送信履歴で reset が残っていること（AI がまだ応答していないため差分モードに落ちない）
        sent_after_build = server.read_sent(s["id"])
        self.assertTrue(sent_after_build.get("reset"))

        # note_reply 実行（応答確定）で reset が解除される
        asst_m = {"id": "a_new", "role": "assistant", "text": "Hello"}
        s["messages"].append(asst_m)
        server.note_reply(s, m["id"], asst_m)

        sent_after_reply = server.read_sent(s["id"])
        self.assertNotIn("reset", sent_after_reply)

    # -------------------------------------------------------------
    # T19: repair_outbox_text が実例（"inner: "…）を修復し、生改行入り JSON を strict=False で読む
    # -------------------------------------------------------------
    def test_T19_repair_outbox_text(self):
        # 実例: "inner: "あぁぁっ……"
        bad_json = '{"text": "本文", "inner: "心の声", "memo": "メモ",}'
        res = server.repair_outbox_text(bad_json)
        self.assertIsNotNone(res)
        obj, notes = res
        self.assertEqual(obj.get("inner"), "心の声")
        self.assertEqual(obj.get("text"), "本文")

        # 生改行を含む JSON が strict=False で parse できること
        raw_newline = '{"text": "1行目\n2行目"}'
        parsed = json.loads(raw_newline, strict=False)
        self.assertIn("1行目", parsed["text"])

        # 完全に破損した文字列は None
        garbage = "not a json at all {{"
        self.assertIsNone(server.repair_outbox_text(garbage))

    # -------------------------------------------------------------
    # T20: rotation_fail 後 3ターンは再武装しない (skip_until)
    # -------------------------------------------------------------
    def test_T20_rotation_fail_skips_turns(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {"phase": "spawn_main", "rot_no": 1}
        server.save(s)

        server.rotation_fail(s["id"], "テスト失敗理由")

        s_loaded = server.load(s["id"])
        self.assertEqual(s_loaded["setting"]["rotation_skip_until"], 33)
        self.assertTrue(server.rotation_skipping(s_loaded))

        # 30, 31, 32ターン目まではスキップ
        self.assertFalse(server.rotation_maybe_arm(s_loaded))

        # 33ターン目以降は武装可能
        s_loaded["messages"].append({"id": "u_31", "role": "user", "text": "t31"})
        s_loaded["messages"].append({"id": "u_32", "role": "user", "text": "t32"})
        s_loaded["messages"].append({"id": "u_33", "role": "user", "text": "t33"})
        self.assertFalse(server.rotation_skipping(s_loaded))
        self.assertTrue(server.rotation_maybe_arm(s_loaded))

    # -------------------------------------------------------------
    # T21: 全フェーズのステップ実行（archiving -> cutover）
    # -------------------------------------------------------------
    def test_T21_full_pipeline_step_by_step(self):
        s = self._create_dummy_session(user_turns=30, ide_session="conv-old")
        sid = s["id"]
        server.rotation_maybe_arm(s)
        server.save(s)
        self.assertEqual(server.rotation_phase(s), "armed")

        # 本文確定
        server.rotation_note_body(sid)
        s = server.load(sid)
        self.assertEqual(server.rotation_phase(s), "archiving")

        # 1. archiving
        delay = server.rotation_step(sid)
        self.assertEqual(delay, 0.0)
        s = server.load(sid)
        self.assertEqual(server.rotation_phase(s), "settle")
        self.assertTrue(os.path.exists(s["setting"]["rotation"]["archive"]))

        # 2. settle
        with patch.object(server.ide_poke, "progress", return_value=({"running": False}, None)):
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "spawn_main")

        # 3. spawn_main
        with patch.object(server.ide_poke, "new_conversation", return_value=("conv-new-777", "ok")):
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "spawn_choices")
            self.assertEqual(s["setting"]["rotation"]["new_ide"], "conv-new-777")
            # choices なしチャンネルなので bootstrap_main へ
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "bootstrap_main")

        # 4. bootstrap_main
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server, "send_poke", return_value=(True, "ok", False)):
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "bootstrap_choices")
            # choices なしなので verify_ready へ
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "verify_ready")

        # 5. verify_ready (ide_is_known = True)
        server.mark_known("conv-new-777")
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")):
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "verify_test")

        # 6. verify_test (送信前 -> 送信後 -> 合格)
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 10}), \
             patch.object(server, "send_poke_text", return_value=(True, "ok", False)):
            # 送信ステップ
            delay = server.rotation_step(sid)
            self.assertEqual(delay, server.ROT_VERIFY_POLL)
            s = server.load(sid)
            self.assertTrue(s["setting"]["rotation"]["attempts"]["test_sent"])

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "last_reply", return_value=("準備完了", "", "ok")):
            delay = server.rotation_step(sid)
            self.assertEqual(delay, 0.0)
            s = server.load(sid)
            self.assertEqual(server.rotation_phase(s), "cutover")

        # 7. cutoverは睡眠プロセス（consolidation）の完了を待つ（作中時間はそこで確定する）
        pend = [m for m in server.load(sid)["messages"] if m.get("consolidation_status") == "pending"]
        self.assertEqual(len(pend), 1, "世代交代アーム時に睡眠プロセスが積まれていること")
        with patch.object(server.ide_poke, "rename") as mock_rename:
            self.assertEqual(server.rotation_step(sid), 1.0)
            mock_rename.assert_not_called()
        # 睡眠プロセスの応答が取り込まれてマーカーが消えたら切り替わる
        s = server.load(sid)
        for m in s["messages"]:
            m.pop("consolidation_status", None)
            m.pop("consolidation_ts", None)
        server.save(s)
        with patch.object(server.ide_poke, "rename") as mock_rename:
            delay = server.rotation_step(sid)
            self.assertIsNone(delay)  # 完了
            mock_rename.assert_called_once()

        s_final = server.load(sid)
        st = s_final["setting"]
        self.assertEqual(st["ide_session"], "conv-new-777")
        self.assertEqual(st["rotation_count"], 1)
        self.assertEqual(st["last_rotation_turn"], 30)
        self.assertEqual(st["turns_since_rotation"], 0)
        self.assertIn("🔄 セッションを世代交代しました", st["rotation_notice"])
        self.assertNotIn("rotation", st)
        self.assertEqual(len([m for m in s_final["messages"] if m.get("role") == "user"]), 10)
        sent = server.read_sent(sid)
        self.assertTrue(sent.get("reset"))

    # -------------------------------------------------------------
    # T22: rotation_resume_scan が armed を拾って未知フェーズ中断させないこと
    # -------------------------------------------------------------
    def test_T22_rotation_resume_scan_ignores_armed(self):
        s = self._create_dummy_session(user_turns=30)
        sid = s["id"]
        server.rotation_maybe_arm(s)
        server.save(s)
        self.assertEqual(server.rotation_phase(s), "armed")

        server._rot_scan_at = 0.0
        server.rotation_resume_scan()
        with server.rotation_guard:
            self.assertNotIn(sid, server.rotation_queue)

        # 万一 armed のまま rotation_step が呼ばれても rotation_fail せず None を返すこと
        res = server.rotation_step(sid)
        self.assertIsNone(res)
        s_after = server.load(sid)
        self.assertEqual(server.rotation_phase(s_after), "armed")
        self.assertNotIn("⚠️", s_after["setting"].get("rotation_notice", ""))

    # -------------------------------------------------------------
    # T23: 選択肢専用会話（choices）ありチャンネルの完全ステップ実行と AGENTS.md 同期
    # -------------------------------------------------------------
    def test_T23_full_pipeline_with_choices(self):
        s = self._create_dummy_session(user_turns=30, ide_session="conv-main-old")
        sid = s["id"]
        s["setting"]["choices_ide_session"] = "conv-cho-old"
        s["setting"]["choices_ide_title"] = "旧選択肢"
        s["setting"]["choices_ide_model"] = "model-choices"
        s["setting"]["agents_md"] = True
        server.save(s)

        server.rotation_maybe_arm(s)
        server.save(s)
        server.rotation_note_body(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "archiving")

        # 1. archiving
        server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "settle")

        # 2. settle
        with patch.object(server.ide_poke, "progress", return_value=({"running": False}, None)):
            server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "spawn_main")

        # 3. spawn_main
        with patch.object(server.ide_poke, "new_conversation", return_value=("conv-main-new", "ok")):
            server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "spawn_choices")

        # 4. spawn_choices
        with patch.object(server.ide_poke, "new_conversation", return_value=("conv-cho-new", "ok")), \
             patch.object(server.ide_poke, "rename"):
            server.rotation_step(sid)
        s_cho = server.load(sid)
        self.assertEqual(server.rotation_phase(s_cho), "bootstrap_main")
        self.assertEqual(s_cho["setting"]["rotation"]["new_choices_ide"], "conv-cho-new")

        # 5. bootstrap_main
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server, "send_poke", return_value=(True, "ok", False)):
            server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "bootstrap_choices")

        # 6. bootstrap_choices
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server, "send_poke", return_value=(True, "ok", False)):
            server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "verify_ready")

        # 7. verify_ready
        server.mark_known("conv-main-new")
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")):
            server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "verify_test")

        # 8. verify_test (send & verify OK)
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 10}), \
             patch.object(server, "send_poke_text", return_value=(True, "ok", False)):
            server.rotation_step(sid)

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "last_reply", return_value=("準備完了", "", "ok")):
            server.rotation_step(sid)
        self.assertEqual(server.rotation_phase(server.load(sid)), "cutover")

        # 9. cutover (睡眠プロセスの完了を待ってから、AGENTS.md & choices AGENTS.md written, both old renamed)
        with patch.object(server.ide_poke, "rename") as mock_rename:
            self.assertEqual(server.rotation_step(sid), 1.0)
            mock_rename.assert_not_called()
        s = server.load(sid)
        for m in s["messages"]:
            m.pop("consolidation_status", None)
            m.pop("consolidation_ts", None)
        server.save(s)
        with patch.object(server.ide_poke, "rename") as mock_rename:
            delay = server.rotation_step(sid)
            self.assertIsNone(delay)
            # old_ide と old_choices_ide の両方が rename される
            self.assertEqual(mock_rename.call_count, 2)

        s_final = server.load(sid)
        st = s_final["setting"]
        self.assertEqual(st["ide_session"], "conv-main-new")
        self.assertEqual(st["choices_ide_session"], "conv-cho-new")

        # choices/AGENTS.md が更新されていること
        # （共有フォルダなので会話IDは書かない。選択肢会話も初回以降はスペース合図で動く前提の文言）
        c_folder = os.path.join(server.channel_folder(s_final), "choices")
        c_path = os.path.join(c_folder, server.AGENTS_NAME)
        self.assertTrue(os.path.exists(c_path))
        with open(c_path, "r", encoding="utf-8") as f:
            c_agents_content = f.read()
        self.assertIn(f"channel={sid}_choices", c_agents_content)
        self.assertNotIn("--ide conv-cho-new", c_agents_content)  # 固定IDは書かない
        self.assertIn("合図が半角スペース1文字だけなら", c_agents_content)

    # -------------------------------------------------------------
    # T24: /api/message/alter（アシスタント発言編集）でも回転が中止されること
    # -------------------------------------------------------------
    def test_T24_alter_message_aborts_rotation(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {"phase": "spawn_main", "rot_no": 1}
        server.save(s)

        asst_m = [m for m in s["messages"] if m.get("role") == "assistant"][-1]
        resp = self._post("/api/message/alter", {"sid": s["id"], "id": asst_m["id"], "text": "変更された発言"})
        self.assertTrue(resp.get("ok"))

        s_loaded = server.load(s["id"])
        self.assertEqual(server.rotation_phase(s_loaded), "")
        self.assertIn("⚠️", s_loaded["setting"].get("rotation_notice", ""))

    # -------------------------------------------------------------
    # T25: choices_ide のタイトルが rotation_reserve で失われないこと
    # -------------------------------------------------------------
    def test_T25_choices_title_preserved_in_reserve(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "verify_test",
            "rot_no": 1,
            "new_ide": "fail-main-cid",
            "new_ide_title": "メインタイトル",
            "new_choices_ide": "good-choices-cid",
            "new_choices_ide_title": "【選択肢】タイトル123",
            "attempts": {"verify": 2}
        }
        server.save(s)

        # verify_retry で最終失敗 -> rotation_fail
        with patch.object(server.ide_poke, "rename"):
            server._rot_verify_retry(s["id"], "疎通テストNG")

        s_loaded = server.load(s["id"])
        res = s_loaded["setting"].get("rotation_reserve", {})
        self.assertEqual(res.get("choices_ide"), "good-choices-cid")
        self.assertEqual(res.get("choices_title"), "【選択肢】タイトル123")
        self.assertEqual(res.get("title"), "【選択肢】タイトル123")

        # 次回回転で choices_ide を再利用したとき、タイトルが復元されること
        s_next = self._create_dummy_session(user_turns=30)
        s_next["setting"]["rotation"] = {"phase": "spawn_choices", "rot_no": 2, "old_choices_ide": "old-cho"}
        s_next["setting"]["rotation_reserve"] = res
        server.save(s_next)

        with patch.object(server.ide_poke, "locate", return_value=(1234, "tok", {})):
            server._rot_spawn(s_next["id"], "choices")

        s_next_loaded = server.load(s_next["id"])
        r = s_next_loaded["setting"]["rotation"]
        self.assertEqual(r.get("new_choices_ide"), "good-choices-cid")
        self.assertEqual(r.get("new_choices_ide_title"), "【選択肢】タイトル123")

    # -------------------------------------------------------------
    # T26: /api/session/delete で rotation_queue から即座に削除されること
    # -------------------------------------------------------------
    def test_T26_deleted_session_cleans_rotation_queue(self):
        s = self._create_dummy_session(user_turns=30)
        sid = s["id"]
        server.rotation_schedule(sid, 10.0)

        with server.rotation_guard:
            self.assertIn(sid, server.rotation_queue)

        resp = self._post("/api/session/delete", {"sid": sid})
        self.assertTrue(resp.get("ok"))

        with server.rotation_guard:
            self.assertNotIn(sid, server.rotation_queue)

    # -------------------------------------------------------------
    # T27: 担当を切り替えても、未応答の発言が新しい会話に届くこと（廃止した刻印の退行防止）
    #      以前は ide_pending_for が合図した会話を指したままで、/api/pending は現在の担当にも
    #      旧い担当にも渡さず、/api/claim も「別の会話が処理中」で拒否し続けていた（永久に未応答）。
    # -------------------------------------------------------------
    def test_T27_pending_reachable_after_reassign(self):
        s = self._create_dummy_session(user_turns=1, ide_session="ide-A")
        sid = s["id"]
        with server.lock:
            s = server.load(sid)
            # 旧担当に合図済みだった状態（過去のバージョンが書いた刻印が残っている）
            s["messages"].append({"id": "u_pend", "role": "user", "text": "テスト発言",
                                   "status": "pending", "ts": server.now(), "ide_pending_for": "ide-A"})
            server.save(s)

        # この時点では A が担当なので A に届く（刻印と担当が一致している）
        self.assertTrue(self._get("/api/pending?ide=ide-A").get("pending"))
        # 担当でない B には届かない
        self.assertFalse(self._get("/api/pending?ide=ide-B").get("pending"))

        # 担当を B に変える
        self._post("/api/ide/assign", {"sid": sid, "ide": "ide-B", "title": "B", "target": "main"})

        pend = self._get("/api/pending?ide=ide-B").get("pending")
        self.assertTrue(pend, "担当を変えたら新しい会話に未応答の発言が届くべき")
        self.assertEqual(pend.get("id"), "u_pend")
        r = self._post("/api/claim", {"sid": sid, "id": "u_pend", "ide": "ide-B", "type": "user"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(server.load(sid)["messages"][-1]["status"], "processing")

    # -------------------------------------------------------------
    # T28: 宛先(reply_to)の無い本文は、未応答が無ければ捨てる（孤児の発言を作らない）
    # -------------------------------------------------------------
    def test_T28_orphan_body_is_stale(self):
        s = self._create_dummy_session(user_turns=2, ide_session="ide-A")
        sid = s["id"]
        before = len(server.load(sid)["messages"])
        p = os.path.join(server.OUTBOX, "orphan.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": "", "text": "準備完了"}, f, ensure_ascii=False)
        server.process_outbox()
        self.assertEqual(len(server.load(sid)["messages"]), before, "宛先の無い本文が孤児として追加された")
        self.assertFalse(os.path.exists(p), "捨てた本文は outbox に残るべきでない")

    # -------------------------------------------------------------
    # T29: ブートストラップの「応答中待ち」に上限がある（固まっても回転が止まらない）
    # -------------------------------------------------------------
    def test_T29_bootstrap_idle_wait_timeout(self):
        s = self._create_dummy_session(user_turns=30)
        sid = s["id"]
        limit = int(server.ROT_VERIFY_TIMEOUT // server.ROT_VERIFY_POLL) + 1

        s = server.load(sid)
        s["setting"]["rotation"] = {"phase": "bootstrap_main", "rot_no": 1, "new_ide": "conv-new",
                                    "attempts": {"bootstrap_wait": limit}}
        server.save(s)
        with patch.object(server.ide_poke, "idle", return_value=(False, "CASCADE_RUN_STATUS_RUNNING")):
            self.assertIsNone(server._rot_bootstrap(sid, "main"))
        st = server.load(sid)["setting"]
        self.assertNotIn("rotation", st)
        self.assertTrue(st.get("rotation_notice"))

        # choices 側は中止せず、choices をあきらめて verify_ready へ抜ける
        s = server.load(sid)
        s["setting"].pop("rotation_notice", None)
        s["setting"]["rotation"] = {"phase": "bootstrap_choices", "rot_no": 1,
                                    "new_choices_ide": "conv-cho",  # new_ide は上で消えている
                                    "attempts": {"bootstrap_wait": limit}}
        server.save(s)
        with patch.object(server.ide_poke, "idle", return_value=(False, "CASCADE_RUN_STATUS_RUNNING")):
            server._rot_bootstrap(sid, "choices")
        self.assertEqual(server.rotation_phase(server.load(sid)), "verify_ready")

    # -------------------------------------------------------------
    # T30: 疎通テストの「送信前の応答中待ち」にも上限がある
    # -------------------------------------------------------------
    def test_T30_verify_test_pre_send_wait_timeout(self):
        s = self._create_dummy_session(user_turns=30)
        sid = s["id"]
        limit = int(server.ROT_VERIFY_TIMEOUT // server.ROT_VERIFY_POLL) + 1
        s = server.load(sid)
        s["setting"]["rotation"] = {"phase": "verify_test", "rot_no": 1, "new_ide": "conv-new",
                                    "attempts": {"verify": 1, "test_sent": False, "verify_wait": limit}}
        server.save(s)
        with patch.object(server.ide_poke, "idle", return_value=(False, "CASCADE_RUN_STATUS_RUNNING")), \
             patch.object(server.ide_poke, "rename"):
            server._rot_verify_test(sid)
        st = server.load(sid)["setting"]
        r = st.get("rotation") or {}
        self.assertEqual(r.get("attempts", {}).get("verify"), 1, "タイムアウトで残機が増えてはいけない")
        self.assertEqual(r.get("phase"), "spawn_main", "失敗した会話を捨てて作り直しに戻るべき")

    # -------------------------------------------------------------
    # T31: ブートストラップ文が AGENTS.md の古い --ide を打ち消す（通常の合図には入れない）
    # -------------------------------------------------------------
    def test_T31_bootstrap_text_overrides_agents_md(self):
        txt = server.poke_text("conv-new", full=True, wait=server.ROT_BOOTSTRAP_WAIT)
        self.assertIn("AGENTS.md", txt)
        self.assertIn("--ide conv-new", txt)
        normal = server.poke_text("conv-new", full=True)
        self.assertNotIn("AGENTS.md", normal)

    # -------------------------------------------------------------
    # T31b: agents_md がオフのチャンネルでは、AGENTS.md の注意文を出さない
    # （アプリが AGENTS.md を管理していないのに「書いてある」と伝えるのは誤誘導）
    # -------------------------------------------------------------
    def test_T31b_no_agents_notice_when_agents_md_off(self):
        s = self._create_dummy_session(user_turns=1)
        s["setting"]["ide_session"] = "conv-noagents"
        s["setting"]["agents_md"] = False
        server.save(s)
        txt = server.poke_text("conv-noagents", full=True, wait=server.ROT_BOOTSTRAP_WAIT)
        self.assertNotIn("AGENTS.md", txt)
        self.assertIn("--ide conv-noagents", txt)
        # オンに戻せば従来どおり注意文が出る
        s["setting"]["agents_md"] = True
        server.save(s)
        txt2 = server.poke_text("conv-noagents", full=True, wait=server.ROT_BOOTSTRAP_WAIT)
        self.assertIn("AGENTS.md", txt2)

    # -------------------------------------------------------------
    # T12b: ブートストラップのターンに返事があれば、疎通テストを送らずに合格
    # -------------------------------------------------------------
    def test_T12b_verify_skipped_when_bootstrap_replied(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "verify_test", "rot_no": 1, "new_ide": "conv-new-456",
            "old_model": "model-1",
            "attempts": {"verify": 0, "test_sent": False, "bootstrap_after_step": 7},
        }
        server.save(s)
        called = {"send": 0}
        def fake_send(*a, **kw):
            called["send"] += 1
            return (True, "ok", False)
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 9}), \
             patch.object(server.ide_poke, "last_reply", return_value=("（待機）", "", "ok")), \
             patch.object(server, "ide_is_known", return_value=True), \
             patch.object(server, "send_poke_text", side_effect=fake_send):
            delay = server._rot_verify_test(s["id"])
        self.assertEqual(delay, 0.0)
        self.assertEqual(called["send"], 0, "疎通テストを送ってはいけない")
        self.assertEqual(server.rotation_phase(server.load(s["id"])), "cutover")

    # -------------------------------------------------------------
    # T12c: ブートストラップの返事が無ければ従来どおり疎通テストを送る
    # -------------------------------------------------------------
    def test_T12c_verify_sends_when_no_bootstrap_reply(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["rotation"] = {
            "phase": "verify_test", "rot_no": 1, "new_ide": "conv-new-789",
            "old_model": "model-1",
            "attempts": {"verify": 0, "test_sent": False, "bootstrap_after_step": 7},
        }
        server.save(s)
        called = {"send": 0}
        def fake_send(*a, **kw):
            called["send"] += 1
            return (True, "ok", False)
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 9}), \
             patch.object(server.ide_poke, "last_reply", return_value=("", "", "ok")), \
             patch.object(server, "ide_is_known", return_value=True), \
             patch.object(server, "send_poke_text", side_effect=fake_send):
            delay = server._rot_verify_test(s["id"])
        self.assertEqual(delay, server.ROT_VERIFY_POLL)
        self.assertEqual(called["send"], 1, "返事が無いので疎通テストを送るべき")

    # -------------------------------------------------------------
    # T32: 世代交代アーム時に睡眠プロセス（作中時間確定）が1件だけ積まれる
    # -------------------------------------------------------------
    def test_T32_consolidation_enqueued_once_on_arm(self):
        s = self._create_dummy_session(user_turns=30)
        self.assertTrue(server.rotation_maybe_arm(s))
        pend = [m for m in s["messages"] if m.get("consolidation_status") == "pending"]
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["id"], "a_30")          # 直近のアシスタント発言に積む
        self.assertIsInstance(pend[0]["consolidation_ts"], str)   # age() 用に ISO 文字列
        # 二重発行しない（冪等）
        self.assertFalse(server.enqueue_consolidation(s))
        self.assertEqual(len([m for m in s["messages"] if m.get("consolidation_status") == "pending"]), 1)

    # -------------------------------------------------------------
    # T33: 睡眠プロセス中は cutover しない／コンテキストに既存memoと作中時間が入る
    # -------------------------------------------------------------
    def test_T33_consolidation_blocks_cutover_and_context_has_memo(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["memo"] = "1日目: 放課後に告白した"
        s["setting"]["elapsed_time"] = {"day": 1, "period": "夕方"}
        server.save(s)
        self.assertTrue(server.rotation_maybe_arm(s))
        server.save(s)
        s = server.load(s["id"])
        self.assertFalse(server.rotation_idle(s), "睡眠プロセスの応答待ちの間は切り替えない")

        m = next(x for x in s["messages"] if x.get("consolidation_status") == "pending")
        ctx = server.build_consolidation_context(s, m)
        self.assertEqual(ctx["mode"], "consolidation")
        texts = "\n".join(sec.get("text", "") for sec in ctx["sections"])
        self.assertIn("放課後に告白した", texts)   # 既存memoを引き継いで書き直す
        self.assertIn("1日目・夕方", texts)       # 直前の作中時間を提示
        self.assertIn("作中時間は既に確定している。時間帯・日付は変更しない", texts)
        self.assertIn("elapsed_time は出力不要", texts)
    # -------------------------------------------------------------
    # T34: 睡眠プロセスの応答が memo / state に反映され、作中時間は維持され、回転が再開する
    # -------------------------------------------------------------
    def test_T34_consolidation_reply_applies_time_and_resumes_rotation(self):
        s = self._create_dummy_session(user_turns=30)
        s["setting"]["memo"] = "旧あらすじ"
        s["setting"]["elapsed_time"] = {"day": 1, "period": "夜"}
        s["setting"]["turns_in_period"] = 7
        server.save(s)
        self.assertTrue(server.rotation_maybe_arm(s))
        server.save(s)
        sid = s["id"]
        pend = next(m for m in server.load(sid)["messages"] if m.get("consolidation_status") == "pending")

        rep = {
            "session": sid,
            "reply_to": pend["id"],
            "type": "consolidation",
            "memo": "新あらすじ（1日目夜の告白）",
            "elapsed_time": {"day": 2, "period": "早朝"},
            "state": [{"name": "アリス", "outfit": "パジャマ", "place": "寝室"}],
        }
        path = os.path.join(server.OUTBOX, f"{sid}_cons.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        with patch.object(server, "rotation_note_body") as mock_note:
            self.assertTrue(server.process_outbox())
        mock_note.assert_called_once_with(sid)   # 睡眠プロセスの完了で回転を進める

        s2 = server.load(sid)
        self.assertEqual(s2["setting"]["memo"], "新あらすじ（1日目夜の告白）")
        self.assertEqual(s2["setting"]["elapsed_time"], {"day": 1, "period": "夜"})
        self.assertEqual(s2["setting"]["turns_in_period"], 7)
        self.assertFalse(any(m.get("consolidation_status") for m in s2["messages"]))
        self.assertTrue(server.rotation_idle(s2))          # cutover 可能に戻る


if __name__ == "__main__":
    unittest.main()
