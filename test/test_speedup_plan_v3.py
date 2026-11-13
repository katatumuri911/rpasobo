# -*- coding: utf-8 -*-
"""speedup-plan-v3.md の各要件・新設計を網羅検証する単体テスト群"""
import copy
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
import tools.check_context as check_context


def make_msg(role, text, mid=None, **kwargs):
    d = {"id": mid or server.new_id(), "role": role, "text": text, "ts": server.now()}
    d.update(kwargs)
    return d


class TestSpeedupPlanV3(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_sp3_")
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "RAG_DIR", "SENT", "CTX", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)
        self.orig_outbox = getattr(wait_input, "OUTBOX", None)
        self.orig_done = getattr(wait_input, "DONE", None)
        self.orig_ctx_dir = getattr(wait_input, "CTX_DIR", None)
        wait_input.OUTBOX = server.OUTBOX
        wait_input.DONE = server.DONE
        wait_input.CTX_DIR = server.CTX

        server.repoke_tasks.clear()

    def tearDown(self):
        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        if self.orig_outbox is not None:
            wait_input.OUTBOX = self.orig_outbox
        if self.orig_done is not None:
            wait_input.DONE = self.orig_done
        if self.orig_ctx_dir is not None:
            wait_input.CTX_DIR = self.orig_ctx_dir
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("RPCHAT_PERF_LOG", None)
        os.environ.pop("RPCHAT_CARD_CYCLE", None)
        os.environ.pop("RPCHAT_CORE_ANCHOR", None)

    def test_perf_log_written_on_small_diff(self):
        """[Step 1] inline サイズの diff でも perf.log が書かれる（LONG NameError の回帰）"""
        os.environ["RPCHAT_PERF_LOG"] = "1"
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "一人称は私"},
        })
        m1 = make_msg("user", "こんにちは", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "返答", mid="a1")
        m2 = make_msg("user", "2回目", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        self.assertEqual(ctx["mode"], "diff")

        perf_file = os.path.join(server.DATA, "perf.log")
        self.assertTrue(os.path.exists(perf_file))
        with open(perf_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertGreaterEqual(len(lines), 2)
        diff_perf = lines[-1]
        self.assertEqual(diff_perf["mode"], "diff")
        self.assertFalse(diff_perf["spilled"])
        self.assertLessEqual(diff_perf["est_payload_bytes"], 9400)

    def test_spilled_matches_wait_input_for_light(self):
        """[Step 1] light モードでは spilled=False になる"""
        os.environ["RPCHAT_PERF_LOG"] = "1"
        s = server.create_session({
            "title": "テストlight",
            "ide_session": "ide-1",
            "input_mode": "light",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "一人称は私"},
        })
        m1 = make_msg("user", "こんにちは", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "返答", mid="a1")
        m2 = make_msg("user", "2回目", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        self.assertTrue(ctx["light"])
        perf_file = os.path.join(server.DATA, "perf.log")
        with open(perf_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        diff_perf = lines[-1]
        self.assertFalse(diff_perf["spilled"])

    def test_perf_est_includes_card(self):
        """[Step 1] est にカード分が入る"""
        os.environ["RPCHAT_PERF_LOG"] = "1"
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文" * 50,
            "character": {"name": "アリス", "speech": "口調" * 50},
        })
        m1 = make_msg("user", "こんにちは", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "返答", mid="a1")
        m2 = make_msg("user", "2回目", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        card_sec = ctx["sections"][-1]
        perf_file = os.path.join(server.DATA, "perf.log")
        with open(perf_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        diff_perf = lines[-1]
        # est_payload_bytes includes card bytes
        card_b = len(card_sec["text"].encode("utf-8"))
        self.assertGreater(diff_perf["est_payload_bytes"], card_b)

    def test_check_context_all_phases(self):
        """[Step 2] 3位相を強制して spec/frozen を検査"""
        s = server.create_session({
            "title": "3位相テスト",
            "ide_session": "ide-test",
            "instructions": "敬語で話す",
            "character": {"name": "アリス", "speech": "一人称は私"},
        })
        sid = s["id"]
        m1 = make_msg("user", "こんにちは", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "こんにちは！", mid="a1")
        m2 = make_msg("user", "質問です", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        info = check_context.inspect_channel(sid)
        self.assertIsNotNone(info)
        self.assertTrue(info["spec_json_ok"])
        self.assertTrue(info["all_cards_found"]["instructions"])
        self.assertTrue(info["all_cards_found"]["chara"])
        self.assertTrue(info["all_cards_found"]["output"])
        self.assertIn("指示", info["frozen"])
        self.assertIn("キャラクター", info["frozen"])

    def test_card_cycle_is_deterministic(self):
        """0→1→2→0 と巡回"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        phases_seen = []
        for i in range(2, 6):
            ai_mid = f"a{i-1}"
            u_mid = f"u{i}"
            prev_u = f"u{i-1}"
            ai_msg = make_msg("assistant", f"reply{i-1}", mid=ai_mid)
            u_msg = make_msg("user", f"msg{i}", mid=u_mid)
            s["messages"].extend([ai_msg, u_msg])
            server.save(s)
            server.note_reply(s, prev_u, ai_msg)

            ctx = server.build_context(s, u_msg)
            last_sec = ctx["sections"][-1]
            if "指示" in last_sec["label"]:
                phases_seen.append(0)
            elif "キャラクター" in last_sec["label"]:
                phases_seen.append(1)
            elif "応答の書き方" in last_sec["label"]:
                phases_seen.append(2)

        # First diff turn after initial full is phase 1, then 2, then 0, then 1
        self.assertEqual(phases_seen, [1, 2, 0, 1])

    def test_card_appended_at_end(self):
        """カードが sections の最後（ユーザー発言の後）に来る"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        labels = [sec["label"] for sec in ctx["sections"]]
        user_idx = next(i for i, lb in enumerate(labels) if "ユーザーの発言" in lb)
        # Card should be at the very end, strictly after user message
        self.assertEqual(user_idx, len(labels) - 2)
        card_sec = ctx["sections"][-1]
        self.assertIn("キャラクター", card_sec["label"])

    def test_card_not_sent_in_full(self):
        """full ターンではカードを出さない（指示の二重送信なし）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        ctx = server.build_context(s, m1)
        self.assertEqual(ctx["mode"], "full")
        labels = [sec["label"] for sec in ctx["sections"]]
        for card_label in server.CARD_LABELS.values():
            self.assertNotIn(card_label, labels)

    def test_card_not_in_same(self):
        """カードキーが same に載らない"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        self.assertEqual(ctx["mode"], "diff")
        for key_word in ("指示", "キャラクター", "応答の書き方"):
            self.assertNotIn(key_word, ctx.get("same", []))

    def test_card_keys_kept_in_parts(self):
        """sigs/labels に3キーが残る（gone 誤爆なし）"""
        s = server.create_session({
            "title": "テストlight",
            "ide_session": "ide-1",
            "input_mode": "light",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        sent = server.read_sent(s["id"])
        for k in server.CARD_KEYS:
            self.assertIn(k, sent.get("sigs", {}))
            self.assertIn(k, sent.get("labels", {}))

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        # Check no "使わなくなった設定" containing CARD_KEYS
        gone_sec = next((sec for sec in ctx["sections"] if "使わなくなった設定" in sec["label"]), None)
        if gone_sec:
            for k in server.CARD_KEYS:
                self.assertNotIn(k, gone_sec["text"])

    def test_card_phase_advances_only_on_import(self):
        """空応答／.bad では前進しない"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1", status="pending")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        # Advance to phase 1 via valid reply
        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2", status="pending")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        sent_before = server.read_sent(s["id"])
        self.assertEqual(sent_before.get("card_phase"), 1)

        # Simulate empty reply for u2
        empty_reply = {
            "session": s["id"],
            "reply_to": "u2",
            "text": "",
        }
        empty_path = os.path.join(server.OUTBOX, f"{s['id']}_reply_u2.json")
        server.write_json(empty_path, empty_reply)
        server.process_outbox()

        sent_after_empty = server.read_sent(s["id"])
        # Should not have advanced to phase 2 (on valid import it would be 2)
        self.assertEqual(sent_after_empty.get("card_phase"), 1)
        self.assertTrue(sent_after_empty.get("reset"))

    def test_phase_starts_at_one_after_full(self):
        """reset/full 後の次カードはキャラ（指示の重複なし）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "a1", mid="a1")
        s["messages"].append(a1)
        server.save(s)
        server.note_reply(s, "u1", a1)

        # Set reset = True
        sent = server.read_sent(s["id"])
        sent["reset"] = True
        sent["why"] = "テストリセット"
        server.write_sent(s["id"], sent)

        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].append(m2)
        server.save(s)

        # Full turn because reset=True
        ctx_full = server.build_context(s, m2)
        self.assertEqual(ctx_full["mode"], "full")

        a2 = make_msg("assistant", "a2", mid="a2")
        m3 = make_msg("user", "m3", mid="u3")
        s["messages"].extend([a2, m3])
        server.save(s)
        server.note_reply(s, "u2", a2)

        # Diff turn after full: should be phase 1 (キャラ)
        ctx_diff = server.build_context(s, m3)
        self.assertEqual(ctx_diff["mode"], "diff")
        card_sec = ctx_diff["sections"][-1]
        self.assertIn("キャラクター", card_sec["label"])
        self.assertNotIn("指示", card_sec["label"])

    def test_same_mid_retry_keeps_phase(self):
        """同一 mid の再取得（/api/claim）で位相が動かない"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        # First call
        ctx1 = server.build_context(s, m2)
        phase1 = server.read_sent(s["id"]).get("card_phase")

        # Second call (retry)
        ctx2 = server.build_context(s, m2)
        phase2 = server.read_sent(s["id"]).get("card_phase")

        self.assertEqual(phase1, phase2)
        self.assertEqual(ctx1["sections"][-1]["label"], ctx2["sections"][-1]["label"])

    def test_reset_cleared_even_if_reply_to_missing(self):
        """reply_to 欠落の応答でも reset が消える（永久 full の防止）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
        })
        sent = server.read_sent(s["id"])
        sent["reset"] = True
        sent["why"] = "テスト"
        server.write_sent(s["id"], sent)

        # Call note_reply with mismatching/missing reply_to
        server.note_reply(s, "non_existent_mid", make_msg("assistant", "応答"))

        after_sent = server.read_sent(s["id"])
        self.assertNotIn("reset", after_sent)
        self.assertNotIn("why", after_sent)

    def test_write_sent_preserves_only_card_keys(self):
        """write_sent 後に msgs/sigs が復活しない（全消し経路が壊れない）が、card_phase/card_for_mid は引き継がれる"""
        sid = "test_clear_preserve"
        server.save({"id": sid, "messages": []})
        sent_orig = {
            "ide": "ide-old",
            "msgs": [["u1", "sig1"]],
            "sigs": {"summary": "abc"},
            "labels": {"summary": "概要"},
            "card_phase": 2,
            "card_for_mid": "u1",
        }
        server.write_sent(sid, sent_orig)

        # Clear sent data as done by /api/context/reset or cutover (with reset=True)
        cleared_obj = {"ide": "", "msgs": [], "reset": True, "ts": server.now()}
        server.write_sent(sid, cleared_obj)

        read_back = server.read_sent(sid)
        self.assertEqual(read_back["msgs"], [])
        self.assertNotIn("sigs", read_back)
        self.assertNotIn("labels", read_back)
        # card_phase is reset to 1 on reset=True (§3.4, §7-4), card_for_mid is preserved
        self.assertEqual(read_back.get("card_phase"), 1)
        self.assertEqual(read_back.get("card_for_mid"), "u1")

        # Without reset=True, card_phase is preserved from prior state
        server.write_sent(sid, {"ide": "ide-2", "msgs": [], "ts": server.now()})
        read_back2 = server.read_sent(sid)
        self.assertEqual(read_back2["msgs"], [])
        self.assertEqual(read_back2.get("card_phase"), 1)
        self.assertEqual(read_back2.get("card_for_mid"), "u1")

    def test_core_anchor_uses_channel(self):
        """核がチャンネルの口調・概要から生成される（80/60字クリップ）"""
        st = {
            "summary": "あ" * 100,
            "character": {
                "name": "アリス",
                "speech": "セリフ前" + "い" * 100 + "【セリフ例】ここから下はカット",
            },
        }
        anchor = server.core_anchor(st)
        self.assertIn("口調: ", anchor)
        self.assertIn("世界観: ", anchor)
        self.assertNotIn("【セリフ例", anchor)
        # Check clips
        lines = anchor.split("\n")
        speech_line = next(line for line in lines if line.startswith("口調: "))
        summ_line = next(line for line in lines if line.startswith("世界観: "))
        self.assertLessEqual(len(speech_line.replace("口調: ", "")), 80)
        self.assertLessEqual(len(summ_line.replace("世界観: ", "")), 60)

    def test_core_anchor_respects_modes(self):
        """image_mode=off / rp_state=False / 並列choices で該当キーを要求しない"""
        st1 = {
            "image_mode": "off",
            "rp_state": False,
            "suggest_choices": False,
        }
        anchor1 = server.core_anchor(st1)
        self.assertIn("text", anchor1)
        self.assertNotIn("state", anchor1)
        self.assertNotIn("image", anchor1)
        self.assertNotIn("choices", anchor1)

        st2 = {
            "image_mode": "always",
            "rp_state": True,
            "suggest_choices": True,
        }
        anchor2 = server.core_anchor(st2)
        self.assertIn("text", anchor2)
        self.assertIn("state", anchor2)
        self.assertIn("image", anchor2)
        self.assertIn("choices", anchor2)

        st3 = {
            "image_mode": "always",
            "rp_state": True,
            "suggest_choices": True,
            "choices_ide_session": "ide-cho",
            "ide_session": "ide-main",
        }
        anchor3 = server.core_anchor(st3)
        self.assertNotIn("choices", anchor3)

    def test_core_anchor_requires_session_reply_to(self):
        """核に session/reply_to の指示が含まれる"""
        st = {}
        anchor = server.core_anchor(st)
        self.assertIn("session", anchor)
        self.assertIn("reply_to", anchor)

    def test_state_required_fields_without_spec(self):
        """spec を送らないターンでも state の必須項目が揃う"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "口調"},
            "rp_state": True,
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        # 2ターン目: diff (phase 1: キャラクター, no spec)
        ctx = server.build_context(s, m2)
        rules_text = (ctx.get("rules") or {}).get("text", "")
        # Core anchor is in rules_text
        self.assertIn("state", rules_text)

    def test_payload_by_channel(self):
        """各chの est が baseline 表に収まる（千束は spill 許容）"""
        baseline_path = os.path.join(ROOT_DIR, "tools", "baseline.json")
        if not os.path.exists(baseline_path):
            self.skipTest("baseline.json not found")
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)

        for sid, info in baseline.items():
            self.assertFalse(info["spilled"], f"Channel {info.get('title')} ({sid}) spilled: {info['payload_bytes']}")
            self.assertLessEqual(info["payload_bytes"], 9400)

    def test_bad_outbox_marks_error_after_3(self):
        """.bad の3ストライク"""
        s = server.create_session({
            "title": "テストbad",
            "ide_session": "ide-1",
        })
        m1 = make_msg("user", "テスト", mid="u_bad", status="pending")
        s["messages"].append(m1)
        server.save(s)

        for i in range(1, 4):
            bad_path = os.path.join(server.OUTBOX, f"{s['id']}_reply_u_bad_{i}.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write('{"invalid json: ' + str(i))
            # Set mtime back so it gets processed
            os.utime(bad_path, (time.time() - 20, time.time() - 20))
            server.process_outbox()
            s_loaded = server.load(s["id"])
            m_loaded = next(m for m in s_loaded["messages"] if m["id"] == "u_bad")
            if i < 3:
                self.assertEqual(m_loaded["status"], "pending")
                self.assertEqual(m_loaded.get("error_retries"), i)
            else:
                self.assertEqual(m_loaded["status"], "error")
                self.assertEqual(m_loaded.get("error_retries"), 3)
                self.assertIn("壊れていました", m_loaded.get("error", ""))

    def test_bad_outbox_all_pending_on_unknown_mid(self):
        """cand_mid 不明時に未応答すべてが対象"""
        s = server.create_session({
            "title": "テストunknown",
            "ide_session": "ide-1",
        })
        m1 = make_msg("user", "m1", mid="u1", status="pending")
        m2 = make_msg("user", "m2", mid="u2", status="pending")
        s["messages"].extend([m1, m2])
        server.save(s)

        # File name has sid but no mid, content is invalid JSON with no cand_mid
        bad_path = os.path.join(server.OUTBOX, f"{s['id']}.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write('{"invalid": ')
        os.utime(bad_path, (time.time() - 20, time.time() - 20))
        server.process_outbox()

        s_loaded = server.load(s["id"])
        m1_loaded = next(m for m in s_loaded["messages"] if m["id"] == "u1")
        m2_loaded = next(m for m in s_loaded["messages"] if m["id"] == "u2")
        self.assertEqual(m1_loaded.get("error_retries"), 1)
        self.assertEqual(m2_loaded.get("error_retries"), 1)

    def test_always_show_keys_updated(self):
        """output.md を外した前提に ALWAYS_SHOW_KEYS を更新"""
        self.assertNotIn("output.md", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("instructions.md", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("chara.md", server.ALWAYS_SHOW_KEYS)
        self.assertIn("elapsed_time", server.ALWAYS_SHOW_KEYS)
        self.assertIn("chars", server.ALWAYS_SHOW_KEYS)
        self.assertEqual(server.CARD_KEYS, ("instructions.md", "chara.md", "output.md"))


if __name__ == "__main__":
    unittest.main()
