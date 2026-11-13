# -*- coding: utf-8 -*-
"""
test_session_cleanup_zombie_prevention.py
チャンネル削除・リセット時のデータ残留およびゾンビ復活防止 完全修正プランの検証テスト
"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import memory_store
import server


class TestSessionCleanupZombiePrevention(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_cleanup_zombie_")

        # バックアップ
        self.orig_DATA = server.DATA
        self.orig_SESS_DIR = server.SESS_DIR
        self.orig_CHOICES_DIR = server.CHOICES_DIR
        self.orig_STATES_DIR = server.STATES_DIR
        self.orig_IMAGES_DIR = server.IMAGES_DIR
        self.orig_SENT = server.SENT
        self.orig_CTX = server.CTX
        self.orig_ARCHIVE_DIR = server.ARCHIVE_DIR
        self.orig_UPLOADS = server.UPLOADS
        self.orig_OUTBOX = server.OUTBOX
        self.orig_DONE = server.DONE
        self.orig_TRASH = server.TRASH
        self.orig_NEW_IDE_DIR = server.NEW_IDE_DIR
        self.orig_AGENTS_LOG = server.AGENTS_LOG
        self.orig_IDE_MOVES = server.IDE_MOVES

        self.orig_MS_DATA = memory_store.DATA
        self.orig_MS_RAG_DIR = memory_store.RAG_DIR
        self.orig_MS_TRASH = memory_store.TRASH

        # テスト用ディレクトリの設定
        server.DATA = os.path.join(self.test_dir, "data")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        server.STATES_DIR = os.path.join(server.DATA, "states")
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        server.SENT = os.path.join(server.DATA, "sent")
        server.CTX = os.path.join(server.DATA, "ctx")
        server.ARCHIVE_DIR = os.path.join(server.DATA, "archive")
        server.UPLOADS = os.path.join(server.DATA, "uploads")
        server.TRASH = os.path.join(server.DATA, "trash")
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        server.IDE_MOVES = os.path.join(server.DATA, "ide_moves.json")

        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        server.DONE = os.path.join(server.OUTBOX, "done")
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")

        for d in (
            server.DATA, server.SESS_DIR, server.CHOICES_DIR, server.STATES_DIR,
            server.IMAGES_DIR, server.SENT, server.CTX, server.ARCHIVE_DIR,
            server.UPLOADS, server.TRASH, server.OUTBOX, server.DONE, server.NEW_IDE_DIR
        ):
            os.makedirs(d, exist_ok=True)

        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = os.path.join(server.DATA, "rag")
        memory_store.TRASH = server.TRASH
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)

        server.deleted_sessions.clear()
        memory_store._deleted_sessions.clear()
        memory_store._memory_cache.clear()
        server.session_revs.clear()
        server.session_cache.clear()
        server.choices_cache.clear()
        server.states_cache.clear()
        server.images_cache.clear()
        server.sent_cache.clear()
        server.session_ide_cache.clear()
        server.summary_cache.clear()
        server.revert_plan.clear()
        server.memory_regen_state.clear()
        server.memory_regen_meta.clear()
        server.agents_state.clear()
        server.msg_to_session.clear()
        server.claimed_at.clear()
        server.repoke_tasks.clear()
        server.queued_images.clear()
        server.queued_memories.clear()
        if hasattr(server, "_choice_notice_q"):
            server._choice_notice_q.clear()
        if hasattr(server, "_decensor_undo"):
            server._decensor_undo.clear()
        with server.rotation_guard:
            server.rotation_queue.clear()

    def tearDown(self):
        server.DATA = self.orig_DATA
        server.SESS_DIR = self.orig_SESS_DIR
        server.CHOICES_DIR = self.orig_CHOICES_DIR
        server.STATES_DIR = self.orig_STATES_DIR
        server.IMAGES_DIR = self.orig_IMAGES_DIR
        server.SENT = self.orig_SENT
        server.CTX = self.orig_CTX
        server.ARCHIVE_DIR = self.orig_ARCHIVE_DIR
        server.UPLOADS = self.orig_UPLOADS
        server.OUTBOX = self.orig_OUTBOX
        server.DONE = self.orig_DONE
        server.TRASH = self.orig_TRASH
        server.NEW_IDE_DIR = self.orig_NEW_IDE_DIR
        server.AGENTS_LOG = self.orig_AGENTS_LOG
        server.IDE_MOVES = self.orig_IDE_MOVES

        memory_store.DATA = self.orig_MS_DATA
        memory_store.RAG_DIR = self.orig_MS_RAG_DIR
        memory_store.TRASH = self.orig_MS_TRASH

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _call_post(self, path, body):
        class FakeHandler:
            def __init__(self, p, b):
                self.path = p
                self._body = b
                self.result = None
                self.status = 200

            def blocked(self, u, post=True):
                return False

            def body(self):
                return self._body

            def send_json(self, data, code=200):
                self.result = data
                self.status = code
                return data

            def send_error(self, code, msg=None):
                self.status = code
                self.result = {"error": msg or str(code)}

        h = FakeHandler(path, body)
        server.H.do_POST(h)
        return h.status, h.result

    def test_memory_store_delete_session_memories(self):
        """memory_store.delete_session_memories が本体・残存tmp・corruptファイルを一掃し、ゾンビ書き込みを遮断すること"""
        sid = "test_mem_clean"
        mem_file = memory_store.memory_path(sid)
        memory_store.save_memories(sid, {"memories": [{"id": "m1", "content": "約束"}]})
        self.assertTrue(os.path.exists(mem_file))

        # 残存 .tmp_* と .corrupt_* ファイルを作成
        tmp_file = mem_file + ".tmp_123_456"
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write("tmp")
        corrupt_file = mem_file + ".corrupt_12345"
        with open(corrupt_file, "w", encoding="utf-8") as f:
            f.write("corrupt")

        self.assertTrue(os.path.exists(tmp_file))
        self.assertTrue(os.path.exists(corrupt_file))

        # 削除実行
        res = memory_store.delete_session_memories(sid, to_trash=True)
        self.assertTrue(res)
        self.assertFalse(os.path.exists(mem_file))
        self.assertFalse(os.path.exists(tmp_file))
        self.assertFalse(os.path.exists(corrupt_file))

        # TRASH に移動されていること
        trash_files = [f for f in os.listdir(memory_store.TRASH) if f.startswith(f"{sid}_")]
        self.assertTrue(len(trash_files) >= 1)

        # 削除済みフラグが立っており、save_memories によるゾンビ復活が遮断されること
        self.assertIn(sid, memory_store._deleted_sessions)
        memory_store.save_memories(sid, {"memories": [{"id": "m2", "content": "復活記憶"}]})
        self.assertFalse(os.path.exists(mem_file))
        self.assertNotIn(sid, memory_store._memory_cache)

        # unmark すると再作成・保存できるようになること
        memory_store.unmark_session_deleted(sid)
        self.assertNotIn(sid, memory_store._deleted_sessions)
        memory_store.save_memories(sid, {"memories": [{"id": "m3", "content": "正規記憶"}]})
        self.assertTrue(os.path.exists(mem_file))

    def test_delete_session_all_complete_sweep(self):
        """delete_session_all が11種類の全ファイル群、台帳、インメモリ状態を一掃すること"""
        sid = "sid_sweep_all"
        mid1 = "msg_101"
        mid2 = "msg_102"

        # 1. セッション本体
        s = {
            "id": sid,
            "setting": {"title": "スイープテスト", "first_message": "初めまして"},
            "messages": [
                {"id": mid1, "role": "user", "text": "こんにちは"},
                {"id": mid2, "role": "assistant", "text": "よろしく"}
            ]
        }
        server.save(s)

        # 2. 分離データ (choices, states, images)
        server.save_choices_data(sid, {mid1: [{"text": "選択肢1"}]})
        server.save_states_data(sid, {mid2: {"mood": "happy"}})
        server.save_images_data(sid, {mid2: {"prompt": "1girl"}})

        # 3. RAG記憶
        memory_store.save_memories(sid, {"memories": [{"id": "rag1", "content": "メモ"}]})

        # 4. sent 履歴
        server.write_sent(sid, {"ide": "ide-1", "msgs": [mid1, mid2]})

        # 5. ctx ファイル
        ctx_hits = server.rag_hits_path(sid)
        server.write_json(ctx_hits, {mid2: {"hits": 1}})
        ctx_md = os.path.join(server.CTX, f"{sid}_recent.md")
        with open(ctx_md, "w", encoding="utf-8") as f:
            f.write("# recent")

        # 6. archive ファイル
        arc_file = os.path.join(server.ARCHIVE_DIR, f"{sid}_rot1_turn10.json")
        server.write_json(arc_file, {"id": sid, "messages": [{"id": "arc_mid_1", "role": "user", "text": "old"}]})

        # 7. uploads ディレクトリ
        up_dir = os.path.join(server.UPLOADS, sid)
        os.makedirs(up_dir, exist_ok=True)
        with open(os.path.join(up_dir, "test.png"), "w", encoding="utf-8") as f:
            f.write("image bytes")

        # 8. outbox / outbox/done ファイル
        out_f1 = os.path.join(server.OUTBOX, f"{sid}_reply_1.json")
        out_f2 = os.path.join(server.OUTBOX, f"1700000000_{sid}_reply_2.json")
        done_f1 = os.path.join(server.DONE, f"{sid}_done_1.json")
        done_f2 = os.path.join(server.DONE, f"1700000000_{sid}_done_2.json")
        for fpath in (out_f1, out_f2, done_f1, done_f2):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("{}")

        # 9. AGENTS.md / choices/AGENTS.md / ルールファイル / agents_md.json
        c_folder = os.path.join(server.channel_folder(s), "choices")
        os.makedirs(os.path.join(c_folder, ".agents", "rules"), exist_ok=True)
        c_agents = os.path.join(c_folder, server.AGENTS_NAME)
        with open(c_agents, "w", encoding="utf-8") as f:
            f.write(f"{server.AGENTS_MARK} channel={sid}_choices -->\n# choices")
        c_rule = os.path.join(c_folder, ".agents", "rules", "choices-filter-avoidance.md")
        with open(c_rule, "w", encoding="utf-8") as f:
            f.write("# rule")

        server.write_json(server.AGENTS_LOG, {sid: os.path.join(server.channel_folder(s), server.AGENTS_NAME)})
        with open(os.path.join(server.channel_folder(s), server.AGENTS_NAME), "w", encoding="utf-8") as f:
            f.write(f"{server.AGENTS_MARK} channel={sid} -->\n# main")

        # 10. ide_moves.json
        server.write_json(server.IDE_MOVES, {"ide-1": {"folder": "session", "sid": sid, "title": "t"}})

        # 11. インメモリキャッシュ & キューへの投入
        server.claimed_at[mid1] = time.time()
        server.claimed_at[f"{mid1}:choices"] = time.time()
        server.repoke_tasks.add(mid1)
        server.repoke_tasks.add(f"{mid1}:choices")
        server.msg_to_session[mid1] = sid
        server.msg_to_session[mid2] = sid
        server.remember_text_undo(sid, mid1, "before", "after")
        server.queued_images.append((sid, mid2))
        server.queued_images.append(("other_sid", "other_mid"))
        server.queued_memories.append(("explicit", sid, "char", [], [], 1, mid2, None))
        server.queued_memories.append(("explicit", "other_sid", "char", [], [], 1, "other_mid", None))
        server.enqueue_choices_notice_check(sid, mid1, "ide-1", "now")
        server.enqueue_choices_notice_check("other_sid", "other_mid", "ide-2", "now")
        server.rotation_schedule(sid, 10.0)

        # 実行: delete_session_all
        server.delete_session_all(sid, to_trash=True)

        # 検証 1: ファイルが一掃されたこと
        self.assertFalse(os.path.exists(server.spath(sid)))
        self.assertFalse(os.path.exists(server.choices_path(sid)))
        self.assertFalse(os.path.exists(server.states_path(sid)))
        self.assertFalse(os.path.exists(server.images_path(sid)))
        self.assertFalse(os.path.exists(memory_store.memory_path(sid)))
        self.assertFalse(os.path.exists(server.sent_path(sid)))
        self.assertFalse(os.path.exists(ctx_hits))
        self.assertFalse(os.path.exists(ctx_md))
        self.assertFalse(os.path.exists(arc_file))
        self.assertFalse(os.path.exists(up_dir))
        self.assertFalse(os.path.exists(out_f1))
        self.assertFalse(os.path.exists(out_f2))
        self.assertFalse(os.path.exists(done_f1))
        self.assertFalse(os.path.exists(done_f2))
        self.assertFalse(os.path.exists(c_agents))
        self.assertFalse(os.path.exists(c_rule))

        # 検証 2: 台帳から削除されたこと
        self.assertNotIn(sid, server.read_json(server.AGENTS_LOG, {}))
        self.assertNotIn("ide-1", server.read_json(server.IDE_MOVES, {}))

        # 検証 3: TRASH に完全スナップショットと各種ファイルが退避されたこと
        trash_contents = os.listdir(server.TRASH)
        del_snaps = [f for f in trash_contents if f.startswith(f"{sid}_") and f.endswith("_delete.json")]
        self.assertEqual(len(del_snaps), 1, "完全スナップショットが TRASH に保存されていること")
        snap_data = server.read_json(os.path.join(server.TRASH, del_snaps[0]), {})
        # スナップショット内に states / images が結合されていること
        snap_m2 = next(m for m in snap_data["messages"] if m["id"] == mid2)
        self.assertEqual(snap_m2.get("state"), {"mood": "happy"})
        self.assertEqual(snap_m2.get("image", {}).get("prompt"), "1girl")

        # 検証 4: インメモリキャッシュと逆引き・キューから完全にパージされたこと
        self.assertNotIn(sid, server.session_cache)
        self.assertNotIn(sid, server.choices_cache)
        self.assertNotIn(sid, server.states_cache)
        self.assertNotIn(sid, server.images_cache)
        self.assertNotIn(sid, server.sent_cache)
        self.assertNotIn(sid, server.session_revs)
        self.assertNotIn(sid, server.rotation_queue)
        self.assertNotIn(mid1, server.msg_to_session)
        self.assertNotIn(mid2, server.msg_to_session)
        self.assertNotIn(mid1, server.claimed_at)
        self.assertNotIn(f"{mid1}:choices", server.claimed_at)
        self.assertNotIn(mid1, server.repoke_tasks)
        self.assertNotIn((sid, mid1, "text"), server._decensor_undo)

        # キューに残ったのは他セッションのみであること
        self.assertEqual(list(server.queued_images), [("other_sid", "other_mid")])
        self.assertEqual(len(server.queued_memories), 1)
        self.assertEqual(server.queued_memories[0][1], "other_sid")
        self.assertEqual(len(server._choice_notice_q), 1)
        self.assertEqual(server._choice_notice_q[0]["sid"], "other_sid")

    def test_zombie_prevention_guards(self):
        """削除済みセッションに対する書き込み・非同期タスクが防波堤ガードで遮断されること"""
        sid = "sid_zombie_guard"
        server.deleted_sessions.add(sid)
        memory_store.mark_session_deleted(sid)

        # 1. 各種 save 関数が何も書き込まないこと
        server.save({"id": sid, "messages": []})
        self.assertFalse(os.path.exists(server.spath(sid)))

        server.save_choices_data(sid, {"m1": []})
        self.assertFalse(os.path.exists(server.choices_path(sid)))

        server.save_states_data(sid, {"m1": {}})
        self.assertFalse(os.path.exists(server.states_path(sid)))

        server.save_images_data(sid, {"m1": {}})
        self.assertFalse(os.path.exists(server.images_path(sid)))

        server.write_sent(sid, {"ide": "ide-z"})
        self.assertFalse(os.path.exists(server.sent_path(sid)))

        memory_store.save_memories(sid, {"memories": []})
        self.assertFalse(os.path.exists(memory_store.memory_path(sid)))

        # 2. _rot_archiving が削除済みセッションなら何もしないこと
        res_rot = server._rot_archiving(sid)
        self.assertIsNone(res_rot)

        # 3. run_memory_regeneration が即座に中止されること
        server.memory_regen_state[sid] = {"running": True}
        server.run_memory_regeneration(sid, [[{"role": "user", "text": "約束"}]], "キャラ")
        self.assertNotIn(sid, server.memory_regen_state)
        self.assertFalse(os.path.exists(memory_store.memory_path(sid)))

    def test_half_broken_session_api_delete(self):
        """セッション本体が破損・消失していても /api/session/delete で他ファイル・キャッシュが一掃されること"""
        sid = "sid_broken_test"

        # セッション本体 (data/sessions/<sid>.json) は作らない！
        # 代わりに分離データや sent、memory だけが存在する「半壊」状態を作る
        server.save_choices_data(sid, {"m1": [{"text": "残骸"}]})
        server.save_states_data(sid, {"m1": {"mood": "broken"}})
        memory_store.save_memories(sid, {"memories": [{"id": "r1", "content": "残骸記憶"}]})

        self.assertTrue(os.path.exists(server.choices_path(sid)))
        self.assertTrue(os.path.exists(server.states_path(sid)))
        self.assertTrue(os.path.exists(memory_store.memory_path(sid)))
        self.assertFalse(os.path.exists(server.spath(sid)))

        # API 経由で削除
        code, res = self._call_post("/api/session/delete", {"sid": sid})
        self.assertEqual(code, 200)
        self.assertTrue(res.get("ok"))

        # 残骸ファイルが一掃されていること
        self.assertFalse(os.path.exists(server.choices_path(sid)))
        self.assertFalse(os.path.exists(server.states_path(sid)))
        self.assertFalse(os.path.exists(memory_store.memory_path(sid)))

        # 不正な sid の場合は 400 エラーになること
        code_bad, res_bad = self._call_post("/api/session/delete", {"sid": "../malicious"})
        self.assertEqual(code_bad, 400)
        self.assertIn("error", res_bad)

    def test_api_reset_cleans_ephemeral_and_separates_data(self):
        """/api/reset が手動記憶を保護しつつ、分離データ・一時ファイル・インメモリ逆引きを完全に初期化すること"""
        sid = "sid_reset_test"
        mid1 = "m_res_1"
        mid2 = "m_res_2"

        s = {
            "id": sid,
            "setting": {
                "title": "リセットテスト",
                "initial_memo": "初期メモ",
                "memo": "途中メモ",
                "first_message": "始まりの言葉",
                "total_turns": 15,
                "rotation_count": 1
            },
            "messages": [
                {"id": mid1, "role": "user", "text": "こんにちは"},
                {"id": mid2, "role": "assistant", "text": "はいよ"}
            ]
        }
        server.save(s)

        # 記憶: 自動記憶と手動記憶を保存
        memory_store.save_memories(sid, {
            "memories": [
                {"id": "auto_1", "source": "auto", "content": "自動記憶"},
                {"id": "man_1", "source": "manual", "content": "手動記憶", "status": "fulfilled"}
            ]
        })

        # 分離データ
        server.save_choices_data(sid, {mid1: [{"text": "選択肢"}]})
        server.save_states_data(sid, {mid2: {"mood": "calm"}})
        server.save_images_data(sid, {mid2: {"prompt": "cat"}})

        # 一時ファイル (sent, ctx, archive, uploads, outbox)
        server.write_sent(sid, {"ide": "ide-old", "msgs": [mid1, mid2]})
        server.write_json(server.rag_hits_path(sid), {mid2: {"hits": 2}})
        arc_p = os.path.join(server.ARCHIVE_DIR, f"{sid}_rot1_turn10.json")
        server.write_json(arc_p, {"messages": [{"id": "arc_mid_old"}]})
        up_dir = os.path.join(server.UPLOADS, sid)
        os.makedirs(up_dir, exist_ok=True)
        with open(os.path.join(up_dir, "pic.jpg"), "w", encoding="utf-8") as f:
            f.write("pic")
        out_f = os.path.join(server.OUTBOX, f"{sid}_test.json")
        with open(out_f, "w", encoding="utf-8") as f:
            f.write("{}")

        # インメモリ逆引き
        server.msg_to_session[mid1] = sid
        server.claimed_at[mid1] = time.time()
        server.repoke_tasks.add(mid1)
        server.queued_images.append((sid, mid2))

        # API リセット実行
        code, res = self._call_post("/api/reset", {"sid": sid})
        self.assertEqual(code, 200)
        self.assertTrue(res.get("ok"))

        # リセット後のセッション状態確認
        s_after = server.load(sid)
        self.assertEqual(s_after["setting"]["memo"], "初期メモ")
        self.assertEqual(s_after["setting"]["total_turns"], 0)
        self.assertEqual(s_after["setting"]["rotation_count"], 0)
        # first_message が1件だけ配置されていること
        self.assertEqual(len(s_after["messages"]), 1)
        self.assertEqual(s_after["messages"][0]["text"], "始まりの言葉")
        new_fm_id = s_after["messages"][0]["id"]
        self.assertNotEqual(new_fm_id, mid1)
        self.assertNotEqual(new_fm_id, mid2)

        # 分離データが空 ({}) に初期化されていること
        self.assertEqual(server.load_choices_data(sid), {})
        self.assertEqual(server.load_states_data(sid), {})
        self.assertEqual(server.load_images_data(sid), {})

        # 記憶: 手動記憶だけが残り（fulfilled -> active）、自動記憶は消去されていること
        mems = memory_store.load_memories(sid).get("memories", [])
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["id"], "man_1")
        self.assertEqual(mems[0]["status"], "active")

        # 一時ファイルが一掃されていること
        self.assertFalse(os.path.exists(server.rag_hits_path(sid)))
        self.assertFalse(os.path.exists(arc_p))
        self.assertFalse(os.path.exists(up_dir))
        self.assertFalse(os.path.exists(out_f))

        # sent がリセットされ reset: True が記録されていること
        sent_after = server.read_sent(sid)
        self.assertTrue(sent_after.get("reset"))
        self.assertEqual(sent_after.get("why"), "チャンネルリセット")

        # インメモリ逆引き・タスク・キューから旧midが一掃されていること
        self.assertNotIn(mid1, server.msg_to_session)
        self.assertNotIn(mid2, server.msg_to_session)
        self.assertNotIn(mid1, server.claimed_at)
        self.assertNotIn(mid1, server.repoke_tasks)
        self.assertEqual(len(server.queued_images), 0)

    def test_edge_cases_trash_collision_and_direct_remove(self):
        """TRASH 名衝突時のサフィックス加算および to_trash=False による直接消去の検証"""
        sid = "sid_collision"
        mem_file = memory_store.memory_path(sid)
        memory_store.save_memories(sid, {"memories": [{"id": "m1", "content": "1"}]})

        # 既に TRASH に同名ファイルが存在する状態を作る
        ts = int(time.time())
        os.makedirs(memory_store.TRASH, exist_ok=True)
        dst_first = os.path.join(memory_store.TRASH, f"{sid}_{ts}_memories.json")
        with open(dst_first, "w", encoding="utf-8") as f:
            f.write("collision_existing")

        # to_trash=True で削除実行 -> 衝突回避で _1 が作られること
        memory_store.delete_session_memories(sid, to_trash=True)
        trash_files = [f for f in os.listdir(memory_store.TRASH) if f.startswith(f"{sid}_{ts}_")]
        self.assertGreaterEqual(len(trash_files), 2)

        # to_trash=False のテスト
        sid2 = "sid_direct_remove"
        memory_store.unmark_session_deleted(sid2)
        mem_file2 = memory_store.memory_path(sid2)
        memory_store.save_memories(sid2, {"memories": [{"id": "m2", "content": "2"}]})
        self.assertTrue(os.path.exists(mem_file2))
        memory_store.delete_session_memories(sid2, to_trash=False)
        self.assertFalse(os.path.exists(mem_file2))
        self.assertFalse(any(f.startswith(f"{sid2}_") for f in os.listdir(memory_store.TRASH)))

    def test_edge_cases_invalid_sid_inputs(self):
        """無効な session_id（空、空白、不正記号、パス走査文字）のガード検証"""
        for bad_sid in ("", "   ", None, "sid/with/slash", "../traversal", "sid*invalid"):
            self.assertFalse(memory_store.delete_session_memories(bad_sid))
            code, res = self._call_post("/api/session/delete", {"sid": bad_sid} if bad_sid else {})
            self.assertEqual(code, 400)
            self.assertIn("error", res)

    def test_edge_cases_worker_cancellation_during_regeneration(self):
        """記憶再生成ループ中にセッションが削除またはリセットされた場合の即時中断・ゾンビ防止検証"""
        sid = "sid_worker_cancel"
        s = {
            "id": sid,
            "setting": {"title": "ワーカーキャンセルテスト"},
            "messages": [
                {"id": f"m_{i}", "role": "user" if i % 2 == 0 else "assistant", "text": f"発言 {i} 約束"}
                for i in range(10)
            ]
        }
        server.save(s)

        chunks = [[s["messages"][0], s["messages"][1]], [s["messages"][2], s["messages"][3]]]

        # 再生成状態をセット
        server.memory_regen_state[sid] = {"running": True}

        # チャンク実行の直前に reset により running = False にされた状況をシミュレート
        server.memory_regen_state[sid]["running"] = False

        server.run_memory_regeneration(sid, chunks, "キャラ", reset=False)

        # 中断され、memory_regen_state から pop されていること
        self.assertNotIn(sid, server.memory_regen_state)

        # 今度はセッション削除 (sid in deleted_sessions) の状況
        server.deleted_sessions.add(sid)
        server.memory_regen_state[sid] = {"running": True}
        server.run_memory_regeneration(sid, chunks, "キャラ", reset=False)
        self.assertNotIn(sid, server.memory_regen_state)
        # 記憶ファイルが残存しないこと
        self.assertFalse(os.path.exists(memory_store.memory_path(sid)))

    def test_edge_cases_rot_archiving_zombie_prevent(self):
        """_rot_archiving がセッション削除直後のタイミングで書き込みを遮断・掃除すること"""
        sid = "sid_rot_zombie"
        s = {
            "id": sid,
            "setting": {
                "title": "ローテーションゾンビテスト",
                "total_turns": 30,
                "rotation": {"phase": "archiving", "rot_no": 1}
            },
            "messages": [{"id": "m1", "role": "user", "text": "turn 30"}]
        }
        server.save(s)

        # 削除済みフラグを立てる
        server.deleted_sessions.add(sid)

        res = server._rot_archiving(sid)
        self.assertIsNone(res)

        # アーカイブファイルが作成されていないこと
        arc_files = [f for f in os.listdir(server.ARCHIVE_DIR) if f.startswith(f"{sid}_")]
        self.assertEqual(len(arc_files), 0)

    def test_edge_cases_reset_with_no_first_message(self):
        """first_message が未設定のチャンネルでの /api/reset でも正常に空メッセージで初期化されること"""
        sid = "sid_reset_no_fm"
        s = {
            "id": sid,
            "setting": {"title": "FMなし", "initial_memo": "init"},
            "messages": [{"id": "m1", "role": "user", "text": "u"}]
        }
        server.save(s)

        code, res = self._call_post("/api/reset", {"sid": sid})
        self.assertEqual(code, 200)
        self.assertTrue(res.get("ok"))

        s_after = server.load(sid)
        self.assertEqual(len(s_after["messages"]), 0)

    def test_write_sent_blocked_when_session_file_not_found(self):
        """deleted_sessions に入っていなくても、セッションファイルが存在しない場合は write_sent がファイルを生成しないこと"""
        non_sid = "sid_non_existent_sent"
        sent_f = server.sent_path(non_sid)
        self.assertFalse(os.path.exists(sent_f))
        self.assertNotIn(non_sid, server.deleted_sessions)

        server.write_sent(non_sid, {"ide": "ide-x", "msgs": []})
        self.assertFalse(os.path.exists(sent_f), "存在しないセッションへの write_sent は遮断されファイルを作らないこと")
        self.assertNotIn(non_sid, server.sent_cache)

    def test_remove_residual_temps_handles_bare_sid_tmp(self):
        """_remove_residual_temps が <sid>.tmp, <sid>.<pid>.tmp, <sid>.json.<pid>.tmp を漏れなく消去すること"""
        sid = "sid_tmp_cleanup"
        p = server.spath(sid)

        tmp1 = os.path.join(server.SESS_DIR, f"{sid}.tmp")
        tmp2 = os.path.join(server.SESS_DIR, f"{sid}.9999.tmp")
        tmp3 = os.path.join(server.SESS_DIR, f"{sid}.json.8888.tmp")
        tmp4 = os.path.join(server.SESS_DIR, f"{sid}.json.tmp")

        for f in (tmp1, tmp2, tmp3, tmp4):
            with open(f, "w", encoding="utf-8") as fp:
                fp.write("tmp")

        server._remove_residual_temps(p)

        for f in (tmp1, tmp2, tmp3, tmp4):
            self.assertFalse(os.path.exists(f), f"{f} が削除されていること")

    def test_delete_session_all_snapshot_collision(self):
        """同秒内に delete_session_all が実行されても TRASH の完全スナップショットが衝突回避サフィックスで上書き保存されないこと"""
        sid = "sid_snap_collision"
        s = {
            "id": sid,
            "setting": {"title": "スナップショット衝突テスト"},
            "messages": [{"id": "m1", "role": "user", "text": "hi"}]
        }
        server.save(s)

        ts = int(time.time())
        os.makedirs(server.TRASH, exist_ok=True)
        # 既存のスナップショットを同名で先行作成
        existing_dst = os.path.join(server.TRASH, f"{sid}_{ts}_delete.json")
        with open(existing_dst, "w", encoding="utf-8") as fp:
            fp.write('{"marker": "original"}')

        server.delete_session_all(sid, to_trash=True)

        # 元のファイルが保持され、かつ新しいバックアップが _1 等で保存されていること
        orig_content = server.read_json(existing_dst, {})
        self.assertEqual(orig_content.get("marker"), "original")

        trash_snaps = [f for f in os.listdir(server.TRASH) if f.startswith(f"{sid}_{ts}_") and f.endswith("_delete.json")]
        self.assertGreaterEqual(len(trash_snaps), 2, "衝突回避サフィックス付きで両方のスナップショットが残ること")

    def test_delete_session_all_cleans_untracked_agents_md(self):
        """AGENTS_LOG に未登録でも自チャンネルが owner の AGENTS.md が確実に trash へ退避されること"""
        sid = "sid_untracked_agents"
        s = {
            "id": sid,
            "setting": {"title": "未登録AGENTSテスト"},
            "messages": []
        }
        server.save(s)

        c_base = server.channel_folder(s)
        agents_file = os.path.join(c_base, server.AGENTS_NAME)
        with open(agents_file, "w", encoding="utf-8") as fp:
            fp.write(f"{server.AGENTS_MARK} channel={sid} -->\n# untracked")

        # AGENTS_LOG には登録しない
        log = server.read_json(server.AGENTS_LOG, {})
        log.pop(sid, None)
        server.write_json(server.AGENTS_LOG, log)

        self.assertTrue(os.path.exists(agents_file))
        server.delete_session_all(sid, to_trash=True)
        self.assertFalse(os.path.exists(agents_file), "AGENTS_LOG 未登録でも自チャンネル所有の AGENTS.md は退避されること")

    def test_outbox_processing_blocked_for_deleted_session(self):
        """deleted_sessions にあるチャンネル宛ての outbox 応答は処理されず .nosession に退避されること"""
        sid = "sid_outbox_deleted"
        server.deleted_sessions.add(sid)

        out_file = os.path.join(server.OUTBOX, f"{sid}_reply_del.json")
        with open(out_file, "w", encoding="utf-8") as fp:
            json.dump({"session": sid, "reply_to": "m1", "text": "ゾンビ返答"}, fp)

        imported = server.process_outbox()
        self.assertFalse(os.path.exists(out_file))
        nosess = [f for f in os.listdir(server.DONE) if f.startswith(f"{sid}_reply_del") and f.endswith(".nosession")]
        self.assertEqual(len(nosess), 1)

    def test_clear_auto_memories_trash_collision(self):
        """clear_auto_memories が TRASH 退避時に同秒衝突回避ループを持つこと"""
        sid = "sid_clear_auto_col"
        memory_store.save_memories(sid, {"memories": [{"id": "m1", "source": "auto", "content": "auto"}]})

        ts = int(time.time())
        os.makedirs(memory_store.TRASH, exist_ok=True)
        first_trash = os.path.join(memory_store.TRASH, f"{sid}_{ts}_memories.json")
        with open(first_trash, "w", encoding="utf-8") as fp:
            fp.write("existing")

        memory_store.clear_auto_memories(sid)

        trash_files = [f for f in os.listdir(memory_store.TRASH) if f.startswith(f"{sid}_{ts}_")]
        self.assertGreaterEqual(len(trash_files), 2)


if __name__ == "__main__":
    unittest.main()
