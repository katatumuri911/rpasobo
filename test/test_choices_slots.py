# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
import wait_input
import ide_poke
import threading
import urllib.request

# serve_forever の待ち時間。既定の 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒ブロックする
_POLL = 0.02


class StubIDE:
    @staticmethod
    def errors_since(cid, since="", kinds=None, limit=200):
        return [{"kind": "filter", "text": "content safety", "at": since}]


class TestChoicesSlots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_choices_")
        self.orig_data_dir = server.DATA
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_sess_dir = server.SESS_DIR
        self.orig_images_dir = server.IMAGES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_sent = server.SENT
        self.orig_ctx = server.CTX
        self.orig_trash = server.TRASH
        self.orig_outbox = server.OUTBOX
        self.orig_done = server.DONE
        self.orig_wait_outbox = wait_input.OUTBOX
        self.orig_poke = server.ide_poke
        server.ide_poke = StubIDE

        server.DATA = os.path.join(self.tmp, "data")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        server.STATES_DIR = os.path.join(server.DATA, "states")
        server.SENT = os.path.join(server.DATA, "sent")
        server.CTX = os.path.join(server.DATA, "ctx")
        server.TRASH = os.path.join(server.DATA, "trash")
        server.OUTBOX = os.path.join(self.tmp, "outbox")
        server.DONE = os.path.join(self.tmp, "outbox", "done")
        wait_input.OUTBOX = server.OUTBOX

        for d in (
            server.CHOICES_DIR, server.SESS_DIR, server.IMAGES_DIR, server.STATES_DIR,
            server.SENT, server.CTX, server.TRASH, server.OUTBOX, server.DONE
        ):
            os.makedirs(d, exist_ok=True)

        server.session_cache.clear()
        server.choices_cache.clear()
        # 婉曲→直接の辞書を切っておく（辞書の中身にテスト結果が左右されないように）
        server.write_json(server.decensor_path(), {"enabled": False, "words": {}})
        server._DECENSOR_CACHE.update(key=None, words={})

    def tearDown(self):
        server.DATA = self.orig_data_dir
        server.CHOICES_DIR = self.orig_choices_dir
        server.SESS_DIR = self.orig_sess_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.STATES_DIR = self.orig_states_dir
        server.SENT = self.orig_sent
        server.CTX = self.orig_ctx
        server.TRASH = self.orig_trash
        server._DECENSOR_CACHE.update(key=None, words={})
        server.OUTBOX = self.orig_outbox
        server.DONE = self.orig_done
        wait_input.OUTBOX = self.orig_wait_outbox
        server.ide_poke = self.orig_poke

        server.session_cache.clear()
        server.choices_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_placeholders_filled(self):
        # プレースホルダーの置換と文字長降順置換
        norm, left = server.apply_choice_placeholders([
            {"label": "__JP_AN__責め", "text": "「__JP_HB__を見せろ」"}
        ])
        self.assertEqual(norm[0]["label"], "アナル責め")
        self.assertEqual(norm[0]["text"], "「秘部を見せろ」")
        self.assertEqual(left, 0)

    def test_unknown_placeholder_count(self):
        norm, left = server.apply_choice_placeholders([
            {"label": "x", "text": "__JP_UNKNOWN__と__JP_PS__"}
        ])
        self.assertEqual(left, 1)
        self.assertIn("オマンコ", norm[0]["text"])

    def test_slot_routing_fallback(self):
        # 1) 返信JSONの slot
        self.assertEqual(server.choices_slot_of({"slot": "erotic"}, {}), "erotic")
        self.assertEqual(server.choices_slot_of({"slot": "normal"}, {}), "normal")
        # 2) メッセージに残した choices_slot
        self.assertEqual(server.choices_slot_of({}, {"choices_slot": "erotic", "choices_type": "normal"}), "erotic")
        # 3) choices_type
        self.assertEqual(server.choices_slot_of({}, {"choices_type": "erotic"}), "erotic")
        self.assertEqual(server.choices_slot_of({}, {"choices_type": "normal"}), "normal")
        # 4) outbox ファイル名
        self.assertEqual(server.choices_slot_of({}, {}, "sid_choices_erotic_123.json"), "erotic")
        self.assertEqual(server.choices_slot_of({}, {}, "sid_reply_123.json"), "normal")
        self.assertEqual(server.choices_slot_of({}, {}), "normal")

    def test_both_slots_persistence(self):
        s = server.create_session({"title": "slot_test"})
        sid = s["id"]
        # Add assistant message with both normal and erotic choices
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "こんにちは",
            "ts": server.now(),
            "choices": [{"label": "n1", "text": "通常1"}],
            "choices_updated_ts": server.now(),
            "choices_erotic": [{"label": "e1", "text": "過激1"}],
            "choices_erotic_updated_ts": server.now(),
        }
        s["messages"].append(m)
        server.save(s)

        # Clear cache to force reload from disk
        server.session_cache.clear()
        server.choices_cache.clear()

        loaded = server.load(sid)
        msg_loaded = server.find_msg(loaded, m["id"])
        self.assertIsNotNone(msg_loaded)
        self.assertEqual(len(msg_loaded.get("choices") or []), 1)
        self.assertEqual(msg_loaded["choices"][0]["text"], "通常1")
        self.assertEqual(len(msg_loaded.get("choices_erotic") or []), 1)
        self.assertEqual(msg_loaded["choices_erotic"][0]["text"], "過激1")
        self.assertTrue(msg_loaded.get("choices_updated_ts"))
        self.assertTrue(msg_loaded.get("choices_erotic_updated_ts"))

        # Verify session.json does NOT contain choices or choices_erotic
        with open(server.spath(sid), encoding="utf-8") as f:
            raw_sess = f.read()
        self.assertNotIn('"choices_erotic"', raw_sess)
        self.assertNotIn('"choices"', raw_sess)

        # Verify choices/<sid>.json DOES contain choices and erotic
        with open(server.choices_path(sid), encoding="utf-8") as f:
            cdata = json.load(f)
        self.assertIn(m["id"], cdata)
        self.assertEqual(cdata[m["id"]]["choices"][0]["text"], "通常1")
        self.assertEqual(cdata[m["id"]]["erotic"][0]["text"], "過激1")

    def test_choices_deletion_cleans_cdata(self):
        # Validates Bug B3 fix: when choices removed from message, cdata is updated
        s = server.create_session({"title": "cdata_clean_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
            "choices": [{"label": "n", "text": "通常"}],
            "choices_erotic": [{"label": "e", "text": "エロ"}],
        }
        s["messages"].append(m)
        server.save(s)

        cdata_file = server.choices_path(sid)
        with open(cdata_file, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("choices", d[m["id"]])
        self.assertIn("erotic", d[m["id"]])

        # Remove erotic choices from memory and save
        m.pop("choices_erotic", None)
        server.save(s)

        server.choices_cache.clear()
        with open(cdata_file, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("choices", d[m["id"]])
        self.assertNotIn("erotic", d[m["id"]])

        # Remove normal choices too and save
        m.pop("choices", None)
        server.save(s)

        server.choices_cache.clear()
        with open(cdata_file, encoding="utf-8") as f:
            d = json.load(f)
        self.assertNotIn(m["id"], d)

    def test_prune_removes_updated_ts(self):
        # Validates Bug B4 fix: pruning removes all 4 choice keys from in-memory messages
        s = server.create_session({"title": "prune_test"})
        sid = s["id"]
        for i in range(7):
            s["messages"].append({
                "id": f"asst_{i}",
                "role": "assistant",
                "text": f"turn {i}",
                "ts": server.now(),
                "choices": [{"label": f"c{i}", "text": f"text{i}"}],
                "choices_updated_ts": server.now(),
                "choices_erotic": [{"label": f"e{i}", "text": f"erotic{i}"}],
                "choices_erotic_updated_ts": server.now(),
            })
        server.save(s)

        msg0 = server.find_msg(s, "asst_0")
        msg1 = server.find_msg(s, "asst_1")
        msg6 = server.find_msg(s, "asst_6")

        for k in ("choices", "choices_updated_ts", "choices_erotic", "choices_erotic_updated_ts"):
            self.assertNotIn(k, msg0, f"{k} should be pruned from msg0")
            self.assertNotIn(k, msg1, f"{k} should be pruned from msg1")

        self.assertIn("choices", msg6)
        self.assertIn("choices_updated_ts", msg6)
        self.assertIn("choices_erotic", msg6)
        self.assertIn("choices_erotic_updated_ts", msg6)

    def test_suspect_notice_not_overwritten_by_filter(self):
        # Validates Bug B1 fix: explicit hits == 0 keeps suspect notice instead of upgrading to filter
        s = server.create_session({"title": "notice_test"})
        sid = s["id"]
        # Case 1-A: 初期通知なしから suspect が確定すること
        m_none = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テストなし",
            "ts": server.now(),
            "choices_erotic": [{"label": "健全", "text": "手をつなぐだけ"}],  # explicit hits == 0
        }
        # Case 1-B: timeout 通知から suspect へ昇格・確定すること
        m_timeout = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テストタイムアウト",
            "ts": server.now(),
            "choices_erotic": [{"label": "健全", "text": "手をつなぐだけ"}],
            "choices_notice": {"kind": "timeout", "slot": "erotic", "task": "2026-11-08T12:00:00", "ts": server.now()},
        }
        # Case 1-C: 既存 suspect 通知が filter で上書きされないこと
        m_suspect = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト既存",
            "ts": server.now(),
            "choices_erotic": [{"label": "健全", "text": "手をつなぐだけ"}],
            "choices_notice": {"kind": "suspect", "slot": "erotic", "task": "2026-11-08T12:00:00", "ts": server.now()},
        }
        s["messages"] = [m_none, m_timeout, m_suspect]
        server.save(s)

        for target_m in (m_none, m_timeout, m_suspect):
            job = {
                "sid": sid,
                "mid": target_m["id"],
                "ide": "mock_ide",
                "since": "2026-11-08T12:00:00",
                "slot": "erotic",
                "timeout": False,
                "rescue": False,
                "explicit_hits": 0,
            }
            server._process_choices_notice(job)

        reloaded = server.load(sid)
        for target_m in (m_none, m_timeout, m_suspect):
            rm = server.find_msg(reloaded, target_m["id"])
            self.assertEqual(rm.get("choices_notice", {}).get("kind"), "suspect")

    def test_timeout_notice_upgraded_to_filter_when_explicit_hits_gt_zero(self):
        # timeout 通知がある状態で filtered=True かつ explicit_hits > 0 のとき filter へ昇格すること
        s = server.create_session({"title": "timeout_to_filter_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
            "choices_erotic": [{"label": "肉棒責め", "text": "肉棒をしゃぶる"}],
            "choices_notice": {"kind": "timeout", "slot": "erotic", "task": "2026-11-08T12:00:00", "ts": server.now()},
        }
        s["messages"].append(m)
        server.save(s)

        job = {
            "sid": sid,
            "mid": m["id"],
            "ide": "mock_ide",
            "since": "2026-11-08T12:00:00",
            "slot": "erotic",
            "timeout": False,
            "rescue": False,
            "explicit_hits": 1,
        }
        server._process_choices_notice(job)

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, m["id"])
        self.assertEqual(rm.get("choices_notice", {}).get("kind"), "filter")

    def test_filter_notice_confirmed_when_explicit_hits_gt_zero(self):
        # When filtered=True and explicit_hits > 0, notice is set to filter
        s = server.create_session({"title": "filter_notice_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
            "choices_erotic": [{"label": "肉棒責め", "text": "肉棒をしゃぶる"}],
        }
        s["messages"].append(m)
        server.save(s)

        job = {
            "sid": sid,
            "mid": m["id"],
            "ide": "mock_ide",
            "since": "2026-11-08T12:00:00",
            "slot": "erotic",
            "timeout": False,
            "rescue": False,
            "explicit_hits": 1,
        }
        server._process_choices_notice(job)

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, m["id"])
        self.assertEqual(rm.get("choices_notice", {}).get("kind"), "filter")

    def test_placeholder_notice_not_overwritten_by_filter(self):
        # Validates #7 ranking: placeholder (5) > filter (3). Filter cannot overwrite placeholder.
        s = server.create_session({"title": "placeholder_rank_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
            "choices_erotic": [{"label": "肉棒責め", "text": "肉棒をしゃぶる"}],
            "choices_notice": {
                "kind": "placeholder", "slot": "erotic",
                "left": 0, "replaced": 1,
                "task": "2026-11-08T12:00:00", "ts": server.now()
            }
        }
        s["messages"].append(m)
        server.save(s)

        job = {
            "sid": sid,
            "mid": m["id"],
            "ide": "mock_ide",
            "since": "2026-11-08T12:00:00",
            "slot": "erotic",
            "timeout": False,
            "rescue": False,
            "explicit_hits": 1,
        }
        server._process_choices_notice(job)

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, m["id"])
        self.assertEqual(rm.get("choices_notice", {}).get("kind"), "placeholder")

    def test_suspect_exclude_words(self):
        # #5: 赤面 / 濡れ are not counted as explicit hits
        sanitized = [{"label": "赤面", "text": "「もう、濡れちゃった…」"}]
        self.assertEqual(server.choices_explicit_hits(sanitized), 0)

        explicit = [{"label": "肉棒", "text": "秘部を突く"}]
        self.assertGreater(server.choices_explicit_hits(explicit), 0)

    def test_normal_choices_does_not_enqueue_ide_check(self):
        # #4: Normal choices import should NOT enqueue choices notice check
        s = server.create_session({"title": "normal_enqueue_test"})
        sid = s["id"]
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "本文",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_type": "normal",
            "choices_slot": "normal",
            "choices_ts": server.now(),
        }
        s["messages"].append(asst_m)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_choices_normal_123.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": asst_m["id"],
                "slot": "normal",
                "choices": [{"label": "通常1", "text": "通常の返答1"}]
            }, f)

        server._choice_notice_q.clear()
        server.process_outbox()

        # Should NOT have enqueued any notice check for normal choices
        self.assertEqual(len(server._choice_notice_q), 0)

    def test_erotic_choices_enqueues_ide_check(self):
        # Erotic choices import SHOULD enqueue choices notice check
        s = server.create_session({"title": "erotic_enqueue_test"})
        sid = s["id"]
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "本文",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_type": "erotic",
            "choices_slot": "erotic",
            "choices_ts": server.now(),
        }
        s["messages"].append(asst_m)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_choices_erotic_123.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": asst_m["id"],
                "slot": "erotic",
                "choices": [{"label": "肉棒責め", "text": "肉棒をしゃぶる"}]
            }, f)

        server._choice_notice_q.clear()
        server.process_outbox()

        # Should have enqueued notice check for erotic choices
        self.assertEqual(len(server._choice_notice_q), 1)
        job = server._choice_notice_q.popleft()
        self.assertEqual(job["slot"], "erotic")

    def test_rescued_notice_not_overwritten_by_filter(self):
        # Validates Bug B2 fix: .tmp rescue keeps rescued notice and is not overwritten
        s = server.create_session({"title": "rescue_notice_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
        }
        s["messages"].append(m)
        server.save(s)

        task_ts = "2026-11-08T12:00:00"
        tmp_name = f"{sid}_choices_erotic_12345.json.tmp"
        tmp_path = os.path.join(server.OUTBOX, tmp_name)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({
                "reply_to": m["id"],
                "slot": "erotic",
                "choices": [{"label": "肉棒責め", "text": "肉棒をしゃぶる"}]
            }, f)
        # Set mtime to 20 seconds ago so it qualifies for rescue (> 15 seconds)
        old_time = time.time() - 20
        os.utime(tmp_path, (old_time, old_time))

        job = {
            "sid": sid,
            "mid": m["id"],
            "ide": "",
            "since": task_ts,
            "slot": "erotic",
            "timeout": True,
            "rescue": True,
            "explicit_hits": None,
        }
        server._process_choices_notice(job)

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, m["id"])
        self.assertIsNotNone(rm.get("choices_erotic"))
        self.assertEqual(rm.get("choices_notice", {}).get("kind"), "rescued")

    def test_clone_session_and_clean_import(self):
        s = {
            "id": "test_s",
            "messages": [{
                "id": "m1",
                "role": "assistant",
                "choices": [{"label": "a", "text": "b"}],
                "choices_erotic": [{"label": "c", "text": "d"}],
                "choices_notice": {"kind": "filter", "slot": "erotic"},
            }]
        }
        c = server.clone_session(s)
        self.assertEqual(c["messages"][0]["choices_erotic"][0]["label"], "c")
        self.assertEqual(c["messages"][0]["choices_notice"]["kind"], "filter")
        c["messages"][0]["choices_erotic"][0]["label"] = "modified"
        self.assertEqual(s["messages"][0]["choices_erotic"][0]["label"], "c")

        imported_st, imported_msgs = server.clean_import({
            "setting": {"title": "import_test"},
            "messages": [{
                "role": "assistant",
                "text": "hello",
                "choices": [{"label": "n", "text": "norm"}],
                "choices_erotic": [{"label": "e", "text": "ero"}],
            }]
        })
        self.assertEqual(len(imported_msgs), 1)
        self.assertEqual(imported_msgs[0]["choices"][0]["text"], "norm")
        self.assertEqual(imported_msgs[0]["choices_erotic"][0]["text"], "ero")

    def test_inline_and_staged_choices_have_updated_ts(self):
        # Validates Bug B5 fix: inline and staged choices include choices_updated_ts
        s = server.create_session({"title": "inline_test"})
        sid = s["id"]
        user_m = {"id": server.new_id(), "role": "user", "text": "hello", "ts": server.now(), "status": "pending"}
        s["messages"].append(user_m)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_reply_123.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": user_m["id"],
                "text": "AI応答",
                "choices": [{"label": "c", "text": "text"}]
            }, f)

        server.process_outbox()

        reloaded = server.load(sid)
        asst_m = reloaded["messages"][-1]
        self.assertEqual(asst_m["role"], "assistant")
        self.assertTrue(asst_m.get("choices_updated_ts"), "Inline choices must have choices_updated_ts")

        # Staged choices merged with assistant reply
        s2 = server.create_session({"title": "staged_test"})
        sid2 = s2["id"]
        user_m2 = {
            "id": server.new_id(),
            "role": "user",
            "text": "hello 2",
            "ts": server.now(),
            "status": "pending",
            "staged_choices": [{"label": "staged", "text": "staged text"}]
        }
        s2["messages"].append(user_m2)
        server.save(s2)

        outbox_file2 = os.path.join(server.OUTBOX, f"{sid2}_reply_456.json")
        with open(outbox_file2, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid2,
                "reply_to": user_m2["id"],
                "text": "AI応答 2"
            }, f)

        server.process_outbox()

        reloaded2 = server.load(sid2)
        asst_m2 = reloaded2["messages"][-1]
        self.assertEqual(asst_m2["role"], "assistant")
        self.assertEqual(asst_m2["choices"][0]["text"], "staged text")
        self.assertTrue(asst_m2.get("choices_updated_ts"), "Staged choices must have choices_updated_ts when merged")

    def test_process_outbox_slot_routing_and_notice(self):
        s = server.create_session({"title": "outbox_slot_test"})
        sid = s["id"]
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "本文",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_type": "erotic",
            "choices_slot": "erotic",
            "choices_ts": server.now(),
        }
        s["messages"].append(asst_m)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_choices_erotic_789.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": asst_m["id"],
                "slot": "erotic",
                "choices": [{"label": "__JP_UNKNOWN__", "text": "「__JP_HB__を見せて」"}]
            }, f)

        server.process_outbox()

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, asst_m["id"])
        self.assertIsNotNone(rm.get("choices_erotic"))
        self.assertEqual(rm["choices_erotic"][0]["text"], "「秘部を見せて」")
        self.assertIsNotNone(rm.get("choices_notice"))
        self.assertEqual(rm["choices_notice"]["kind"], "placeholder")
        self.assertEqual(rm["choices_notice"]["slot"], "erotic")
        self.assertEqual(rm["choices_notice"]["left"], 1)
        self.assertNotIn("choices_status", rm)

    def test_parallel_erotic_auto_staged_merge(self):
        """🔞既定のチャンネル: 先読みが erotic で走り、応答に合流するとき erotic 側へ入る"""
        self.assertEqual(server.parallel_choices_type({}), "normal")
        self.assertEqual(server.parallel_choices_type({"choices_erotic_auto": True}), "erotic")

        s = server.create_session({
            "title": "auto_erotic",
            "suggest_choices": True,
            "choices_erotic_auto": True,
            "ide_session": "main-ide",
            "choices_ide_session": "choices-ide",
        })
        sid = s["id"]
        user_m = {
            "id": server.new_id(), "role": "user", "text": "ああ", "ts": server.now(),
            "status": "pending", "choices_status": "pending",
            "choices_type": server.parallel_choices_type(s["setting"]), "choices_ts": server.now(),
        }
        user_m["choices_orig_id"] = user_m["id"]
        s["messages"].append(user_m)
        server.save(s)

        # 先読み（🔞）が先に届く → staged に slot 付きで積まれる
        with open(os.path.join(server.OUTBOX, f"{sid}_choices_erotic_1.json"), "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": user_m["id"], "slot": "erotic", "type": "choices",
                       "choices": [{"label": "a", "text": "「いくよ」"}]}, f)
        server.process_outbox()
        staged = server.find_msg(server.load(sid), user_m["id"])
        self.assertEqual(staged.get("staged_choices_slot"), "erotic")

        # 本文の応答 → 合流先は choices_erotic
        with open(os.path.join(server.OUTBOX, f"{sid}_reply_1.json"), "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": user_m["id"], "text": "本文"}, f)
        server.process_outbox()

        asst = server.load(sid)["messages"][-1]
        self.assertEqual(asst["role"], "assistant")
        self.assertEqual(len(asst.get("choices") or []), 0)
        self.assertEqual(len(asst.get("choices_erotic") or []), 1)
        self.assertTrue(asst.get("choices_erotic_updated_ts"))

    def test_ide_poke_ts(self):
        t_posix = ide_poke._ts("2026-11-08T12:00:00")
        self.assertIsNotNone(t_posix)
        t_utc = ide_poke._ts("2026-11-08T12:00:00Z")
        self.assertIsNotNone(t_utc)
        self.assertIsNone(ide_poke._ts("invalid-time"))

    def test_wait_input_tag_logic(self):
        ctx = {"mode": "choices", "choices_type": "erotic"}
        task_type = None
        tag = task_type or ctx.get("mode") or "reply"
        if ctx.get("mode") in ("choices", "choices_erotic") and \
                (ctx.get("choices_type") == "erotic" or ctx.get("mode") == "choices_erotic"):
            tag = "choices_erotic"
        self.assertEqual(tag, "choices_erotic")

    def test_known_placeholder_sets_notice(self):
        # AIが置換し忘れた既知プレースホルダーがサーバー補填された場合、left=0でもplaceholder通知が付くこと
        s = server.create_session({"title": "known_ph_test"})
        sid = s["id"]
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "本文",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_type": "erotic",
            "choices_slot": "erotic",
            "choices_ts": server.now(),
        }
        s["messages"].append(asst_m)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_choices_erotic_known.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": asst_m["id"],
                "slot": "erotic",
                "choices": [{"label": "手コキ", "text": "「__JP_HB__を見せて」"}]  # 既知語のみ
            }, f)

        server.process_outbox()

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, asst_m["id"])
        self.assertIsNotNone(rm.get("choices_erotic"))
        self.assertEqual(rm["choices_erotic"][0]["text"], "「秘部を見せて」")
        self.assertIsNotNone(rm.get("choices_notice"))
        self.assertEqual(rm["choices_notice"]["kind"], "placeholder")
        self.assertEqual(rm["choices_notice"].get("replaced"), 1)
        self.assertEqual(rm["choices_notice"].get("left"), 0)

    def test_rescue_skips_invalid_or_mismatched_tmp_files(self):
        # より新しい .tmp ファイルが破損または別mid宛てでも、有効な候補を正しく救済できること
        s = server.create_session({"title": "rescue_multi_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
        }
        s["messages"].append(m)
        server.save(s)

        task_ts = "2026-11-08T12:00:00"
        t_base = time.time() - 30

        # 有効な .tmp ファイル (古め)
        valid_tmp = os.path.join(server.OUTBOX, f"{sid}_choices_erotic_valid.json.tmp")
        with open(valid_tmp, "w", encoding="utf-8") as f:
            json.dump({
                "reply_to": m["id"],
                "slot": "erotic",
                "choices": [{"label": "肉棒責め", "text": "肉棒をしゃぶる"}]
            }, f)
        os.utime(valid_tmp, (t_base, t_base))

        # 破損した .tmp ファイル (新しめ)
        broken_tmp = os.path.join(server.OUTBOX, f"{sid}_choices_erotic_broken.json.tmp")
        with open(broken_tmp, "w", encoding="utf-8") as f:
            f.write("INVALID JSON CONTENT")
        os.utime(broken_tmp, (t_base + 5, t_base + 5))

        # 別mid宛ての .tmp ファイル (さらに新しめ)
        other_tmp = os.path.join(server.OUTBOX, f"{sid}_choices_erotic_other.json.tmp")
        with open(other_tmp, "w", encoding="utf-8") as f:
            json.dump({
                "reply_to": "other_mid",
                "slot": "erotic",
                "choices": [{"label": "別候補", "text": "別のテキスト"}]
            }, f)
        os.utime(other_tmp, (t_base + 10, t_base + 10))

        job = {
            "sid": sid,
            "mid": m["id"],
            "ide": "",
            "since": task_ts,
            "slot": "erotic",
            "timeout": True,
            "rescue": True,
            "explicit_hits": None,
        }
        server._process_choices_notice(job)

        reloaded = server.load(sid)
        rm = server.find_msg(reloaded, m["id"])
        self.assertIsNotNone(rm.get("choices_erotic"))
        self.assertEqual(rm.get("choices_notice", {}).get("kind"), "rescued")
        self.assertEqual(rm["choices_erotic"][0]["label"], "肉棒責め")

    def test_save_cleans_orphan_updated_ts_in_memory(self):
        # メッセージが直近5ターンにあっても、スロット削除時に対応する updated_ts がメモリから消えること
        s = server.create_session({"title": "orphan_ts_test"})
        sid = s["id"]
        m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
            "choices": [{"label": "n", "text": "通常"}],
            "choices_updated_ts": server.now(),
            "choices_erotic": [{"label": "e", "text": "エロ"}],
            "choices_erotic_updated_ts": server.now(),
        }
        s["messages"].append(m)
        server.save(s)

        # erotic choices のみをメモリから pop して save
        m.pop("choices_erotic", None)
        server.save(s)

        # choices_erotic_updated_ts もメモリから消えていること
        self.assertNotIn("choices_erotic_updated_ts", m)
        self.assertIn("choices_updated_ts", m)
        self.assertIn("choices", m)


class TestChoicesSlotsApi(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_choices_api_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_outbox = wait_input.OUTBOX
        self.orig_s_outbox = server.OUTBOX
        self.orig_s_done = server.DONE
        self.orig_agents_log = server.AGENTS_LOG
        self.orig_new_ide_dir = server.NEW_IDE_DIR
        self.orig_ide_known = server.IDE_KNOWN

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.write_json(server.decensor_path(), {"enabled": False, "words": {}})
        server._DECENSOR_CACHE.update(key=None, words={})
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        os.makedirs(server.CHOICES_DIR, exist_ok=True)
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.IDE_KNOWN = os.path.join(server.DATA, "ide_known.json")
        server.known_ides = None
        server.agents_state.clear()

        wait_input.OUTBOX = server.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(wait_input.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.choices_cache.clear()

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
        server.CHOICES_DIR = self.orig_choices_dir
        server.DONE = self.orig_s_done
        server.AGENTS_LOG = self.orig_agents_log
        server.NEW_IDE_DIR = self.orig_new_ide_dir
        server.IDE_KNOWN = self.orig_ide_known
        wait_input.OUTBOX = server.OUTBOX = self.orig_s_outbox
        server.session_cache.clear()
        server.choices_cache.clear()
        server._DECENSOR_CACHE.update(key=None, words={})
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def api_post(self, path, payload):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_api_send_preserves_ongoing_erotic_choices(self):
        # #1: /api/send does not wipe choices fields from ongoing erotic choices
        s = server.create_session({"title": "send_preserves_erotic"})
        sid = s["id"]
        t_active = server.now()
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "前回の応答",
            "ts": server.now(),
            "choices_status": "processing",
            "choices_type": "erotic",
            "choices_slot": "erotic",
            "choices_ts": t_active,
            "choices_claimed_ts": t_active,
        }
        user_old = {
            "id": server.new_id(),
            "role": "user",
            "text": "古い入力",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_type": "normal",
            "choices_ts": t_active,
        }
        s["messages"] = [user_old, asst_m]
        server.save(s)

        res = self.api_post("/api/send", {"sid": sid, "text": "新しいユーザー発言"})
        self.assertTrue(res.get("ok"))

        reloaded = server.load(sid)
        reloaded_asst = server.find_msg(reloaded, asst_m["id"])
        self.assertEqual(reloaded_asst.get("choices_status"), "processing")
        self.assertEqual(reloaded_asst.get("choices_type"), "erotic")
        self.assertEqual(reloaded_asst.get("choices_slot"), "erotic")
        self.assertEqual(reloaded_asst.get("choices_ts"), t_active)
        self.assertEqual(reloaded_asst.get("choices_claimed_ts"), t_active)

        reloaded_old_u = server.find_msg(reloaded, user_old["id"])
        self.assertNotIn("choices_status", reloaded_old_u)
        self.assertNotIn("choices_type", reloaded_old_u)

    def test_api_claim_supports_choices_slot(self):
        # #2: /api/claim falls back to choices_slot when choices_type is missing
        s = server.create_session({"title": "claim_slot_test"})
        sid = s["id"]
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "本文",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_slot": "erotic",
        }
        s["messages"].append(asst_m)
        server.save(s)

        res = self.api_post("/api/claim", {"sid": sid, "id": asst_m["id"], "type": "choices", "context": True})
        self.assertIn("context", res)
        self.assertEqual(res["context"].get("mode"), "choices_erotic")
        self.assertEqual(res["context"].get("choices_type"), "erotic")

    def test_api_send_preserves_ongoing_erotic_choices_pending(self):
        # #1: /api/send does not wipe pending erotic choices (未claim状態での送信)
        s = server.create_session({"title": "send_preserves_pending_erotic"})
        sid = s["id"]
        t_active = server.now()
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "前回の応答",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_type": "erotic",
            "choices_slot": "erotic",
            "choices_ts": t_active,
        }
        s["messages"] = [asst_m]
        server.save(s)

        res = self.api_post("/api/send", {"sid": sid, "text": "新規発言"})
        self.assertTrue(res.get("ok"))

        reloaded = server.load(sid)
        reloaded_asst = server.find_msg(reloaded, asst_m["id"])
        self.assertEqual(reloaded_asst.get("choices_status"), "pending")
        self.assertEqual(reloaded_asst.get("choices_type"), "erotic")
        self.assertEqual(reloaded_asst.get("choices_slot"), "erotic")
        self.assertEqual(reloaded_asst.get("choices_ts"), t_active)

    def test_api_send_cleans_choices_slot_on_non_erotic_or_finished_messages(self):
        # /api/send で進行中ではない通常スロットの choices_slot はきれいに pop されること
        s = server.create_session({"title": "clean_slot_test"})
        sid = s["id"]
        old_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "過去の応答",
            "ts": server.now(),
            "choices_slot": "normal",
        }
        s["messages"] = [old_m]
        server.save(s)

        res = self.api_post("/api/send", {"sid": sid, "text": "送信"})
        self.assertTrue(res.get("ok"))

        reloaded = server.load(sid)
        reloaded_old = server.find_msg(reloaded, old_m["id"])
        self.assertNotIn("choices_slot", reloaded_old)

    def test_api_context_supports_choices_slot(self):
        # #2: /api/context falls back to choices_slot when choices_type is missing
        s = server.create_session({"title": "context_slot_test"})
        sid = s["id"]
        asst_m = {
            "id": server.new_id(),
            "role": "assistant",
            "text": "本文",
            "ts": server.now(),
            "choices_status": "pending",
            "choices_slot": "erotic",
        }
        s["messages"].append(asst_m)
        server.save(s)

        res = self.api_post("/api/context", {"sid": sid, "id": asst_m["id"], "type": "choices"})
        self.assertEqual(res.get("mode"), "choices_erotic")
        self.assertEqual(res.get("choices_type"), "erotic")

    def test_ui_choices_notice_text_and_box_node(self):
        # #3, #8: choicesNoticeText 文面および choicesBox ゼロ件描画の node テスト
        import subprocess
        script = os.path.join(ROOT_DIR, "test", "test_choices_ui.cjs")
        cp = subprocess.run(["node", script], capture_output=True, text=True, cwd=ROOT_DIR)
        self.assertEqual(cp.returncode, 0, f"Node UI tests failed:\n{cp.stdout}\n{cp.stderr}")



if __name__ == "__main__":
    unittest.main()
