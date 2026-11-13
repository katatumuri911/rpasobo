# -*- coding: utf-8 -*-
"""speedup-fix-plan-v31.md の追加修正 (F1-F4, F6, F8-B, G1, G3, G9, G10) 単体テスト群"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
import wait_input


def make_msg(role, text, mid=None, **kwargs):
    d = {"id": mid or server.new_id(), "role": role, "text": text, "ts": server.now()}
    d.update(kwargs)
    return d


class TestSpeedupV31(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_sp31_")
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

        server.queued_memories.clear()
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

    def test_T1_consolidation_time_rule(self):
        """T1: 睡眠プロセスのプロンプトに禁止レベルの時間進行ルールが含まれる (F1)"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "character": {"name": "アリス"},
        })
        m = make_msg("user", "こんにちは")
        s["messages"].append(m)
        server.save(s)

        ctx = server.build_consolidation_context(s, m)
        texts = "\n".join(sec.get("text", "") for sec in ctx.get("sections", []))
        self.assertIn("【禁止】戦闘中、セックス中では、時間帯を進めてはならない", texts)
        self.assertIn("day は日付が変わった時だけ +1", texts)

    def test_T2_memories_merge_rule_and_event(self):
        """T2: モデルが rule を返したターンでもキーワード抽出の event が併存登録される (F2)"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "character": {"name": "ラピ"},
        })
        u1 = make_msg("user", "ラピ、今度の日曜日にカフェに行く約束をしよう", mid="u1", status="pending")
        s["messages"].append(u1)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{s['id']}_reply_1.json")
        rep = {
            "session": s["id"],
            "reply_to": "u1",
            "text": "「了解しました。カフェに行きましょう」",
            "memories": [
                {"category": "rule", "content": "命令には絶対服従すること"}
            ]
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        self.assertEqual(task[0], "explicit")
        self.assertEqual(task[1], s["id"])
        combined_mems = task[3]
        cats = [m.get("category") for m in combined_mems]
        self.assertIn("rule", cats)
        # キーワード抽出による約束／イベントが補完されていること
        self.assertTrue("promise" in cats or "event" in cats)
        # モデルの rule 内容が維持されていること
        rule_item = next(m for m in combined_mems if m.get("category") == "rule")
        self.assertEqual(rule_item.get("content"), "命令には絶対服従すること")

    def test_T3_verify_skip_with_zero_step(self):
        """T3: bootstrap_after_step_main=0（新規会話）でも疎通テストがスキップされる (F3)"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "character": {"name": "アリス"},
        })
        s["setting"]["rotation"] = {
            "phase": "verify_test", "rot_no": 1, "new_ide": "conv-new-zero",
            "old_model": "model-1",
            "attempts": {"verify": 0, "test_sent": False, "bootstrap_after_step_main": 0},
        }
        server.save(s)

        called = {"send": 0}
        def fake_send(*a, **kw):
            called["send"] += 1
            return (True, "ok", False)

        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 1}), \
             patch.object(server.ide_poke, "last_reply", return_value=("（準備完了）", "", "ok")), \
             patch.object(server, "ide_is_known", return_value=True), \
             patch.object(server, "send_poke_text", side_effect=fake_send):
            delay = server._rot_verify_test(s["id"])

        self.assertEqual(delay, 0.0)
        self.assertEqual(called["send"], 0, "疎通テストを送ってはいけない（スキップされるべき）")
        self.assertEqual(server.rotation_phase(server.load(s["id"])), "cutover")

    def test_T4_card_changed_sent_immediately(self):
        """T4: 指示を編集した直後のターンで周期を待たずに指示カードが送信される (F4)"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "初期指示",
            "character": {"name": "アリス", "speech": "一人称は私"},
        })
        m1 = make_msg("user", "こんにちは", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        # Turn 1: full send
        ctx1 = server.build_context(s, m1)
        self.assertEqual(ctx1["mode"], "full")

        # Assistant replies
        a1 = make_msg("assistant", "こんにちは！", mid="a1")
        m2 = make_msg("user", "指示変えたよ", mid="u2")
        s["messages"].extend([a1, m2])
        # Edit instructions
        s["setting"]["instructions"] = "更新された指示"
        server.save(s)
        server.note_reply(s, "u1", a1)

        # Turn 2: diff mode, but instructions was changed -> must be sent immediately
        ctx2 = server.build_context(s, m2)
        self.assertEqual(ctx2["mode"], "diff")
        sent_labels = [sec.get("label", "") for sec in ctx2.get("sections", [])]
        self.assertTrue(any("指示" in lb for lb in sent_labels), f"指示カードが含まれていない: {sent_labels}")

    def test_T6_card_phase_none_no_crash(self):
        """T6: card_phase=None や不正な値でも例外にならず安全にフォールバックする (F6)"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "1回目", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        # Corrupt card_phase in sent
        sent = server.read_sent(s["id"])
        sent["card_phase"] = None
        server.write_sent(s["id"], sent)

        a1 = make_msg("assistant", "返答", mid="a1")
        m2 = make_msg("user", "2回目", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)

        # note_reply should not crash with card_phase=None
        server.note_reply(s, "u1", a1)

        # build_context should not crash with card_phase=None
        sent["card_phase"] = None
        server.write_sent(s["id"], sent)
        ctx = server.build_context(s, m2)
        self.assertIsNotNone(ctx)

    def test_T9_bootstrap_step_keyed_by_target(self):
        """T9: bootstrap_main の stepCount が choices の bootstrap で上書きされない (G1)"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-main",
            "character": {"name": "アリス"},
        })
        s["setting"]["rotation"] = {
            "phase": "bootstrap_main", "rot_no": 1,
            "new_ide": "conv-main", "new_choices_ide": "conv-choices",
            "old_model": "model-1",
            "attempts": {},
        }
        server.save(s)

        sid = s["id"]
        # main stepCount = 2
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 2}), \
             patch.object(server, "send_poke", return_value=(True, "ok", False)):
            server._rot_bootstrap(sid, "main")

        r1 = server.rotation_state(server.load(sid))
        self.assertEqual(r1["attempts"].get("bootstrap_after_step_main"), 2)

        # choices stepCount = 10
        with patch.object(server.ide_poke, "idle", return_value=(True, "IDLE")), \
             patch.object(server.ide_poke, "summary", return_value={"stepCount": 10}), \
             patch.object(server, "send_poke", return_value=(True, "ok", False)):
            server._rot_bootstrap(sid, "choices")

        r2 = server.rotation_state(server.load(sid))
        # main is still 2, choices is 10!
        self.assertEqual(r2["attempts"].get("bootstrap_after_step_main"), 2)
        self.assertEqual(r2["attempts"].get("bootstrap_after_step_choices"), 10)

    def test_T10_no_two_cards_same_turn(self):
        """T10: 変更カードを送ったターンに周期カードを追加しない（同じターンに2枚送らない）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "初期指示",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        # Edit instructions
        s["setting"]["instructions"] = "編集後指示"
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx = server.build_context(s, m2)
        labels = [sec.get("label", "") for sec in ctx["sections"]]
        # Count CARD_KEYS sections present
        card_count = sum(1 for lb in labels if "指示" in lb or "キャラクター" in lb or "応答の書き方" in lb)
        self.assertEqual(card_count, 1, f"変更カード送信ターンに2枚以上のカードが含まれている: {labels}")

    def test_T11_card_sig_only_when_sent(self):
        """T11: 送っていないカードの card_sig は更新されない（次回の変化検知が効く）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "初期指示",
            "character": {"name": "アリス", "speech": "口調"},
        })
        m1 = make_msg("user", "m1", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)
        sent1 = server.read_sent(s["id"])
        chara_sig_init = sent1.get("card_sig", {}).get("chara.md")

        a1 = make_msg("assistant", "a1", mid="a1")
        m2 = make_msg("user", "m2", mid="u2")
        s["messages"].extend([a1, m2])
        # Only edit instructions
        s["setting"]["instructions"] = "指示のみ変更"
        server.save(s)
        server.note_reply(s, "u1", a1)

        server.build_context(s, m2)
        sent2 = server.read_sent(s["id"])
        # chara.md was NOT sent, its card_sig must remain unchanged
        self.assertEqual(sent2.get("card_sig", {}).get("chara.md"), chara_sig_init)
        # instructions.md was sent, its card_sig must be updated
        self.assertEqual(sent2.get("card_sig", {}).get("instructions.md"), server.sig_of("指示のみ変更"))

    def test_T12_model_memory_not_overwritten(self):
        """T12: モデルの rule を出したターンに同一カテゴリの抽出文で上書きされない（単一キュー）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "character": {"name": "ラピ"},
        })
        u1 = make_msg("user", "これからは絶対に遅刻してはならないというルールを守って", mid="u1", status="pending")
        s["messages"].append(u1)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{s['id']}_reply_1.json")
        rep = {
            "session": s["id"],
            "reply_to": "u1",
            "text": "「分かりました。遅刻は絶対にしません」",
            "memories": [
                {"category": "rule", "content": "モデル精密ルール: 集合時刻の10分前には到着すること"}
            ]
        }
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)

        server.process_outbox()
        self.assertEqual(len(server.queued_memories), 1)
        task = server.queued_memories.popleft()
        combined_mems = task[3]

        rules = [m for m in combined_mems if m.get("category") == "rule"]
        self.assertEqual(len(rules), 1, "モデルが出した rule カテゴリは抽出側で二重追加・上書きされない")
        self.assertEqual(rules[0].get("content"), "モデル精密ルール: 集合時刻の10分前には到着すること")

    def test_T13_full_send_records_card_sig(self):
        """T13: full 送信の次の diff ターンでカードが一斉送信されない（spill 防止）"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "一人称は私"},
        })
        m1 = make_msg("user", "1回目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        # Turn 1: full send
        ctx1 = server.build_context(s, m1)
        self.assertEqual(ctx1["mode"], "full")
        sent1 = server.read_sent(s["id"])
        self.assertIn("card_sig", sent1)
        self.assertIn("instructions.md", sent1["card_sig"])
        self.assertIn("chara.md", sent1["card_sig"])
        self.assertIn("output.md", sent1["card_sig"])

        # Assistant replies
        a1 = make_msg("assistant", "返答", mid="a1")
        m2 = make_msg("user", "2回目（設定変更なし）", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        # Turn 2: diff send
        ctx2 = server.build_context(s, m2)
        self.assertEqual(ctx2["mode"], "diff")
        labels = [sec.get("label", "") for sec in ctx2.get("sections", [])]
        card_count = sum(1 for lb in labels if "指示" in lb or "キャラクター" in lb or "応答の書き方" in lb)
        self.assertEqual(card_count, 1, f"full直後のdiffでカードが一斉送信されている (count={card_count}): {labels}")

    def test_legacy_session_card_sig_migration_no_spill(self):
        """旧セッション（card_sig 未保存・sigs のみ存在）からの初回 diff で output.md が誤検知送信されない"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示",
            "character": {"name": "アリス", "speech": "私"},
        })
        m1 = make_msg("user", "1回目", mid="u1")
        s["messages"].append(m1)
        server.save(s)

        # 旧バージョン v3.0 で保存された sent を再現（sigs のみで card_sig は未保存）
        parts, _ = server.settings_parts(s["setting"])
        server.write_sent(s["id"], {
            "ide": "ide-1",
            "sigs": {key: server.sig_of(ct) for _, key, ct in parts},
            "labels": {key: lb for lb, key, _ in parts},
            "msgs": [server.msg_sig(m1)],
            "reset": False,
            "card_phase": 0,
        })

        a1 = make_msg("assistant", "返答", mid="a1")
        m2 = make_msg("user", "2回目（設定変更なし）", mid="u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)

        # 初回 diff ターン
        ctx = server.build_context(s, m2)
        self.assertEqual(ctx["mode"], "diff")
        sent_labels = [sec.get("label", "") for sec in ctx.get("sections", [])]
        # output.md が「変更」として誤判定送信されていないこと
        self.assertFalse(any("応答の書き方" in lb for lb in sent_labels),
                         f"output.md が誤検知送信されている: {sent_labels}")
        # card_phase=1 に応じた周期カード（キャラクター）が1枚だけ正常送信されていること
        card_count = sum(1 for lb in sent_labels if "指示" in lb or "キャラクター" in lb or "応答の書き方" in lb)
        self.assertEqual(card_count, 1, f"カード件数が1枚でない: {sent_labels}")
        self.assertTrue(any("キャラクター" in lb for lb in sent_labels))

    def test_card_deleted_and_readded_sent_immediately(self):
        """カード設定を削除した後に同内容で再設定された場合も、変更として即時送信される"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "instructions": "指示文ABC",
            "character": {"name": "アリス", "speech": "私"},
        })
        m1 = make_msg("user", "1回目", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        a1 = make_msg("assistant", "返答1", mid="a1")
        m2 = make_msg("user", "2回目（指示削除）", mid="u2")
        s["messages"].extend([a1, m2])
        # 指示を空にする
        s["setting"]["instructions"] = ""
        server.save(s)
        server.note_reply(s, "u1", a1)

        ctx2 = server.build_context(s, m2)
        self.assertEqual(ctx2["mode"], "diff")

        a2 = make_msg("assistant", "返答2", mid="a2")
        m3 = make_msg("user", "3回目（指示を元に戻す）", mid="u3")
        s["messages"].extend([a2, m3])
        # 指示を再度同じ内容で設定
        s["setting"]["instructions"] = "指示文ABC"
        server.save(s)
        server.note_reply(s, "u2", a2)

        # 指示カードが即時送信されること
        ctx3 = server.build_context(s, m3)
        self.assertEqual(ctx3["mode"], "diff")
        labels3 = [sec.get("label", "") for sec in ctx3.get("sections", [])]
        self.assertTrue(any("指示" in lb for lb in labels3),
                        f"再設定された指示カードが送信されていない: {labels3}")

    def test_F8_B_backfill_trigger_only_when_empty_and_hint_present(self):
        """F8-B: 既定では memory_status は立たず、RPCHAT_MEMORY_BACKFILL=1 の時のみ pending になる"""
        s = server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "character": {"name": "ラピ"},
        })
        # 1. 既定（OFF）では pending にならないことを確認
        u0 = make_msg("user", "この設定を覚えておいてね", mid="u0", status="pending")
        s["messages"].append(u0)
        server.save(s)
        outbox_file0 = os.path.join(server.OUTBOX, f"{s['id']}_reply_bf0.json")
        with open(outbox_file0, "w", encoding="utf-8") as f:
            json.dump({"session": s["id"], "reply_to": "u0", "text": "「承知しました」", "memories": []}, f, ensure_ascii=False)
        server.process_outbox()
        s_off = server.load(s["id"], readonly=True)
        self.assertIsNone(s_off["messages"][-1].get("memory_status"))

        # 2. RPCHAT_MEMORY_BACKFILL=1 指定時は温存コードどおり pending になることを確認
        with patch.dict(os.environ, {"RPCHAT_MEMORY_BACKFILL": "1"}):
            s = server.load(s["id"])
            u1 = make_msg("user", "この設定を覚えておいてね", mid="u1", status="pending")
            s["messages"].append(u1)
            server.save(s)
            outbox_file1 = os.path.join(server.OUTBOX, f"{s['id']}_reply_bf1.json")
            with open(outbox_file1, "w", encoding="utf-8") as f:
                json.dump({"session": s["id"], "reply_to": "u1", "text": "「はい、しっかりと頭に入れておきます」", "memories": []}, f, ensure_ascii=False)
            server.process_outbox()
            s_after = server.load(s["id"], readonly=True)
            asst_msg = [m for m in s_after["messages"] if m.get("role") == "assistant"][-1]
            self.assertEqual(asst_msg.get("memory_status"), "pending")

            # 3. RPCHAT_MEMORY_BACKFILL=1 でもモデルが memories を出力した時は pending にならず保存キューに入る
            u2 = make_msg("user", "次の約束はどうする？", mid="u2", status="pending")
            s_after_mut = server.load(s["id"])
            s_after_mut["messages"].append(u2)
            server.save(s_after_mut)
            outbox_file2 = os.path.join(server.OUTBOX, f"{s['id']}_reply_bf2.json")
            with open(outbox_file2, "w", encoding="utf-8") as f:
                json.dump({"session": s["id"], "reply_to": "u2", "text": "「了解です」",
                           "memories": [{"category": "rule", "content": "命令遵守"}]}, f, ensure_ascii=False)
            server.process_outbox()
            self.assertEqual(len(server.queued_memories), 1)
            s_after2 = server.load(s["id"], readonly=True)
            asst_msg2 = [m for m in s_after2["messages"] if m.get("role") == "assistant"][-1]
            self.assertIsNone(asst_msg2.get("memory_status"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
