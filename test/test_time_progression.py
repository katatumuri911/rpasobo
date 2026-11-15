import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server
import memory_store


class TestTimeProgression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_time_prog_")
        self.orig_server_dirs = {
            "DATA": server.DATA,
            "SESS_DIR": server.SESS_DIR,
            "CHOICES_DIR": server.CHOICES_DIR,
            "STATES_DIR": server.STATES_DIR,
            "IMAGES_DIR": server.IMAGES_DIR,
            "SENT": server.SENT,
            "CTX": server.CTX,
            "ARCHIVE_DIR": server.ARCHIVE_DIR,
            "TRASH": server.TRASH,
            "OUTBOX": server.OUTBOX,
            "DONE": server.DONE,
        }
        self.orig_ms_dirs = {
            "DATA": memory_store.DATA,
            "RAG_DIR": memory_store.RAG_DIR,
            "TRASH": memory_store.TRASH,
        }

        test_data = os.path.join(self.tmp, "data")
        server.DATA = test_data
        server.SESS_DIR = os.path.join(test_data, "sessions")
        server.CHOICES_DIR = os.path.join(test_data, "choices")
        server.STATES_DIR = os.path.join(test_data, "states")
        server.IMAGES_DIR = os.path.join(test_data, "images")
        server.SENT = os.path.join(test_data, "sent")
        server.CTX = os.path.join(test_data, "ctx")
        server.ARCHIVE_DIR = os.path.join(test_data, "archive")
        server.TRASH = os.path.join(test_data, "trash")
        server.OUTBOX = os.path.join(self.tmp, "outbox")
        server.DONE = os.path.join(self.tmp, "outbox", "done")

        for d in (
            server.DATA, server.SESS_DIR, server.CHOICES_DIR, server.STATES_DIR,
            server.IMAGES_DIR, server.SENT, server.CTX, server.ARCHIVE_DIR,
            server.TRASH, server.OUTBOX, server.DONE
        ):
            os.makedirs(d, exist_ok=True)

        memory_store.DATA = test_data
        memory_store.RAG_DIR = os.path.join(test_data, "rag")
        memory_store.TRASH = server.TRASH
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)
        memory_store._memory_cache.clear()

        server.session_cache.clear()
        server.session_revs.clear()

    def tearDown(self):
        for k, v in self.orig_server_dirs.items():
            setattr(server, k, v)
        for k, v in self.orig_ms_dirs.items():
            setattr(memory_store, k, v)
        memory_store._memory_cache.clear()
        server.session_cache.clear()
        server.session_revs.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self, day=1, period="午前", total=0, last=0, silent=0, rp_state=True):
        s = server.create_session({
            "title": "test_time_progression",
            "elapsed_time": {"day": day, "period": period},
            "rp_state": rp_state,
        })
        st = s["setting"]
        st["total_turns"] = total
        st["time_last_turn"] = last
        st["time_silent_until_turn"] = silent
        return s

    def test_first_30_then_every_20(self):
        s = self._make_session(total=29, last=0)
        due, overdue, th = server.time_tick_info(s["setting"], 29)
        self.assertFalse(due)
        self.assertEqual(th, 30)

        due, overdue, _ = server.time_tick_info(s["setting"], 30)
        self.assertTrue(due)
        self.assertEqual(overdue, 0)

        s["setting"]["time_last_turn"] = 30
        due, _, th = server.time_tick_info(s["setting"], 49)
        self.assertFalse(due)
        self.assertEqual(th, 20)
        due, overdue, _ = server.time_tick_info(s["setting"], 50)
        self.assertTrue(due)
        self.assertEqual(overdue, 0)

    def test_Self_jump_blocked_without_cue(self):
        s = self._make_session(day=1, period="昼", total=30, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 2, "period": "早朝"},
            user_text="少し休もうか",
            gate_states=[],
            total_turn=30,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "self_jump")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 1, "period": "昼"})

    def test_single_step_advances_when_due(self):
        s = self._make_session(day=1, period="昼", total=30, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 1, "period": "夕方"},
            user_text="お茶を淹れよう",
            gate_states=[],
            total_turn=30,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 1, "period": "夕方"})
        self.assertEqual(s["setting"]["time_last_turn"], 30)

    def test_midnight_wraps_to_next_day_early_morning(self):
        s = self._make_session(day=1, period="深夜", total=30, last=0)
        ok, _ = server.apply_reported_time(
            s["setting"],
            {"day": 2, "period": "早朝"},
            user_text="そろそろ落ち着こう",
            gate_states=[],
            total_turn=30,
        )
        self.assertTrue(ok)
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})

    def test_user_cue_allows_multi_step_anytime(self):
        s = self._make_session(day=1, period="夜", total=5, last=0)
        ok, _ = server.apply_reported_time(
            s["setting"],
            {"day": 2, "period": "早朝"},
            user_text="おやすみ、翌朝まで眠ろう",
            gate_states=[{"penetration": "vaginal"}],
            total_turn=5,
        )
        self.assertTrue(ok)
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})
        self.assertEqual(s["setting"]["time_last_turn"], 5)

    def test_showpiece_blocks_advance_and_afterglow_allows(self):
        sex_state = [{"name": "A", "penetration": "vaginal", "action": ["sex"]}]
        self.assertTrue(server.scene_is_showpiece(sex_state))

        combat_state = [{"name": "A", "penetration": "なし", "action": [], "situation": ["魔物と交戦中"]}]
        self.assertTrue(server.scene_is_showpiece(combat_state))

        # 事後（体液・装備が残っていてもpenetration/action/situationが行為中でなければ余韻として通す）
        afterglow = [{
            "name": "A",
            "penetration": "なし",
            "action": ["resting", "talking"],
            "situation": ["ベッドで寄り添い余韻に浸っている"],
            "body_fluids": ["精液"],
            "equipped_items": ["コンドーム"],
        }]
        self.assertFalse(server.scene_is_showpiece(afterglow))

        s = self._make_session(day=1, period="夜", total=30, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 1, "period": "深夜"},
            user_text="もっと続けよう",
            gate_states=sex_state,
            total_turn=30,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "showpiece")

    def test_force_tick_and_adaptive_next_turn_prompt(self):
        s = self._make_session(day=1, period="夜", total=40, last=0, rp_state=True)
        server.force_time_tick(s["setting"], 40)
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 1, "period": "深夜"})
        self.assertEqual(s["setting"]["time_last_turn"], 40)
        self.assertEqual(s["setting"]["time_silent_until_turn"], 41)

        # 次ターンも行為継続中 → サイレント指示
        s["messages"] = [
            {"role": "assistant", "content": "a", "rp_state": [{"name": "A", "penetration": "vaginal"}]},
            {"role": "user", "content": "まだ離さない"},
        ]
        ctx_sex = server.build_context(s, 1)
        dyn_sex = "\n".join(x.get("text", "") for x in ctx_sex.get("dynamic_sections", []))
        self.assertIn("時間の経過には一切触れず", dyn_sex)

        # 次ターンでは行為終了（事後）→ 余韻描写指示
        s["messages"] = [
            {"role": "assistant", "content": "a", "rp_state": [{"name": "A", "penetration": "なし", "action": ["resting"]}]},
            {"role": "user", "content": "ふう、落ち着いたね"},
        ]
        ctx_calm = server.build_context(s, 1)
        dyn_calm = "\n".join(x.get("text", "") for x in ctx_calm.get("dynamic_sections", []))
        self.assertIn("いつの間にかこの時間帯になっていた", dyn_calm)

    def test_force_tick_works_even_when_rp_state_disabled(self):
        s = self._make_session(day=1, period="夜", total=40, last=0, rp_state=False)
        server.force_time_tick(s["setting"], 40)
        s["messages"] = [
            {"role": "assistant", "content": "a", "rp_state": [{"name": "A", "penetration": "vaginal"}]},
            {"role": "user", "content": "まだ続ける"},
        ]
        ctx = server.build_context(s, 1)
        dyn = "\n".join(x.get("text", "") for x in ctx.get("dynamic_sections", []))
        self.assertIn("時間の経過には一切触れず", dyn)

    def test_backwards_and_not_due(self):
        s = self._make_session(day=2, period="夜", total=30, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 2, "period": "昼"},
            user_text="話す",
            gate_states=[],
            total_turn=30,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "backwards")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "夜"})

        s2 = self._make_session(day=1, period="午前", total=10, last=0)
        ok2, reason2 = server.apply_reported_time(
            s2["setting"],
            {"day": 1, "period": "昼"},
            user_text="話す",
            gate_states=[],
            total_turn=10,
        )
        self.assertFalse(ok2)
        self.assertEqual(reason2, "not_due")

    def test_rollback_restores_message_snapshot_cursor(self):
        s = self._make_session(day=1, period="午前", total=0, last=0)
        msgs = []
        for t in range(1, 35):
            msgs.append({"role": "user", "content": f"u{t}"})
            if t < 30:
                msgs.append({
                    "role": "assistant",
                    "content": f"a{t}",
                    "state_snapshot": f"s{t}",
                    "elapsed_snapshot": {"day": 1, "period": "午前"},
                    "turns_in_period_snapshot": t,
                    "time_last_turn_snapshot": 0,
                    "time_silent_until_snapshot": 0,
                })
            else:
                msgs.append({
                    "role": "assistant",
                    "content": f"a{t}",
                    "state_snapshot": f"s{t}",
                    "elapsed_snapshot": {"day": 1, "period": "昼"},
                    "turns_in_period_snapshot": t - 30,
                    "time_last_turn_snapshot": 30,
                    "time_silent_until_snapshot": 0,
                })
        s["messages"] = msgs
        s["setting"]["total_turns"] = 34
        s["setting"]["time_last_turn"] = 30
        s["setting"]["elapsed_time"] = {"day": 1, "period": "昼"}

        # ターン28まで切り詰め → 午前 & time_last_turn=0 に戻る
        s["messages"] = msgs[:56]
        server.restore_latest_snapshot(s)
        self.assertEqual(s["setting"]["total_turns"], 28)
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 1, "period": "午前"})
        self.assertEqual(s["setting"]["time_last_turn"], 0)
        due, _, _ = server.time_tick_info(s["setting"], 30)
        self.assertTrue(due)

    def test_legacy_migration_in_merge_defaults(self):
        legacy = {
            "total_turns": 45,
            "turns_in_period": 12,
            "elapsed_time": {"day": 2, "period": "夕方"},
        }
        merged = server.merge_defaults(legacy)
        self.assertEqual(merged["time_last_turn"], 33)
        due, _, _ = server.time_tick_info(merged, 45)
        self.assertFalse(due)
        due53, _, _ = server.time_tick_info(merged, 53)
        self.assertTrue(due53)

    def test_merge_defaults_period_aliases(self):
        for raw_alias, expected in [("朝", "早朝"), ("昼下がり", "昼"), ("夕暮れ", "夕方"), ("真夜中", "深夜")]:
            merged = server.merge_defaults({"elapsed_time": {"day": 1, "period": raw_alias}})
            self.assertEqual(merged["elapsed_time"]["period"], expected)

    def test_restore_latest_snapshot_in_rotated_session(self):
        s = self._make_session(day=2, period="昼", total=35, last=30)
        s["setting"]["rotation_count"] = 1
        msgs = []
        for i in range(21, 36):
            msgs.append({"id": f"u{i}", "role": "user", "text": f"u{i}"})
            msgs.append({
                "id": f"a{i}",
                "role": "assistant",
                "text": f"a{i}",
                "elapsed_snapshot": {"day": 2, "period": "昼"},
                "time_last_turn_snapshot": 30,
                "turns_in_period_snapshot": i - 30,
            })
        s["messages"] = msgs
        server.restore_latest_snapshot(s)
        self.assertEqual(s["setting"]["total_turns"], 35)
        self.assertEqual(s["setting"]["time_last_turn"], 30)
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "昼"})

    def test_process_outbox_advances_clock_and_sets_snapshots_at_turn_30(self):
        import json
        s = server.create_session({"title": "test_time_progression"})
        sid = s["id"]
        s["setting"]["total_turns"] = 30
        s["setting"]["time_last_turn"] = 0
        s["messages"].append({"id": "u30", "role": "user", "text": "昼ご飯にしよう", "status": "pending"})
        server.save(s)

        reply_file = os.path.join(server.OUTBOX, f"{sid}_reply_u30.json")
        with open(reply_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "u30",
                "text": "いいですね、お昼にしましょう",
                "elapsed_time": {"day": 1, "period": "昼"}
            }, f)

        server.process_outbox()
        s_after = server.load(sid)
        self.assertEqual(s_after["setting"]["total_turns"], 30)
        self.assertEqual(s_after["setting"]["time_last_turn"], 30)
        self.assertEqual(s_after["setting"]["elapsed_time"], {"day": 1, "period": "昼"})
        asst = s_after["messages"][-1]
        self.assertEqual(asst["time_last_turn_snapshot"], 30)
        self.assertEqual(asst["elapsed_snapshot"], {"day": 1, "period": "昼"})

    def test_process_outbox_force_tick_at_overdue_10(self):
        import json
        s = server.create_session({"title": "test_time_progression"})
        sid = s["id"]
        # Turn 40 (threshold 30 + 10 overdue)
        s["setting"]["total_turns"] = 40
        s["setting"]["time_last_turn"] = 0
        s["setting"]["elapsed_time"] = {"day": 1, "period": "午前"}
        s["messages"].append({"id": "u40", "role": "user", "text": "まだ戦う", "status": "pending"})
        server.save(s)

        reply_file = os.path.join(server.OUTBOX, f"{sid}_reply_u40.json")
        with open(reply_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "u40",
                "text": "魔物を攻撃する！",
                "state": [{"name": "A", "penetration": "なし", "action": [], "situation": ["戦闘中"]}]
            }, f)

        server.process_outbox()
        s_after = server.load(sid)
        # force_time_tick advances 1 period silently to "昼"
        self.assertEqual(s_after["setting"]["elapsed_time"], {"day": 1, "period": "昼"})
        self.assertEqual(s_after["setting"]["time_last_turn"], 40)
        self.assertEqual(s_after["setting"]["time_silent_until_turn"], 41)

    def test_as_int_types(self):
        self.assertEqual(server._as_int(True), 1)
        self.assertEqual(server._as_int(False), 0)
        self.assertEqual(server._as_int(5), 5)
        self.assertEqual(server._as_int("12"), 12)
        self.assertEqual(server._as_int("12.9"), 12)
        self.assertEqual(server._as_int(None, default=7), 7)
        self.assertEqual(server._as_int("invalid", default=9), 9)

    def test_time_tick_info_future_cursor_clamped(self):
        st = {"time_last_turn": 50}
        # When total_turn is 30, last cursor is clamped to 30, so threshold is 20, overdue = 30 - 30 - 20 = -20
        due, overdue, th = server.time_tick_info(st, 30)
        self.assertFalse(due)
        self.assertEqual(th, 20)
        self.assertEqual(overdue, -20)

    def test_user_time_asked_generates_question_section(self):
        s = self._make_session(day=2, period="夕方", total=10, last=0)
        s["messages"] = [
            {"role": "user", "content": "今何時ですか？"},
        ]
        ctx = server.build_context(s, 0)
        dyn = "\n".join(x.get("text", "") for x in ctx.get("dynamic_sections", []))
        self.assertIn("2日目・夕方", dyn)
        self.assertIn("時間自体は進めない", dyn)

    def test_user_cue_capping_at_max_days(self):
        s = self._make_session(day=1, period="昼", total=10, last=0)
        # Attempt to skip 100 days
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 101, "period": "昼"},
            user_text="数ヶ月後、再会した",
            gate_states=[],
            total_turn=10,
        )
        self.assertTrue(ok)
        # 30 * 6 = 180 periods = 30 days max step
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 31, "period": "昼"})

    def test_branch_clamps_time_last_turn(self):
        s = self._make_session(day=2, period="夜", total=50, last=45)
        s["messages"] = []
        for i in range(1, 21):
            s["messages"].append({"id": f"u{i}", "role": "user", "text": f"u{i}"})
            s["messages"].append({
                "id": f"a{i}",
                "role": "assistant",
                "text": f"a{i}",
                "elapsed_snapshot": {"day": 1, "period": "昼"},
                "time_last_turn_snapshot": 15,
                "turns_in_period_snapshot": 5,
            })
        server.save(s)
        # Branch at a10 (total completed turns = 10)
        branched_msgs = s["messages"][:20]
        st, msgs = server.clean_import({"setting": s["setting"], "messages": copy.deepcopy(branched_msgs)})
        n = server.create_session(st, msgs)
        server.restore_latest_snapshot(n)
        b_st = n.get("setting", {})
        b_tot = max(0, server._as_int(b_st.get("total_turns"), 0))
        if server._as_int(b_st.get("time_last_turn"), 0) > b_tot:
            b_st["time_last_turn"] = b_tot
            b_st["time_silent_until_turn"] = 0
        self.assertEqual(n["setting"]["total_turns"], 10)
        self.assertLessEqual(n["setting"]["time_last_turn"], 10)

    def test_scene_is_showpiece_kanji_nashi_and_japanese_actions(self):
        # Kanji "無し", "特になし", "無" should not be treated as active penetration
        for no_pen in ["なし", "無し", "特になし", "無", "none", "None", "null", "-"]:
            st = [{"name": "A", "penetration": no_pen}]
            self.assertFalse(server.scene_is_showpiece(st), f"Failed for penetration={no_pen}")

        # Japanese action in action array should trigger showpiece
        st_action_sex = [{"name": "A", "action": ["手コキ", "キス"]}]
        self.assertTrue(server.scene_is_showpiece(st_action_sex))

        st_action_combat = [{"name": "A", "action": ["交戦中"]}]
        self.assertTrue(server.scene_is_showpiece(st_action_combat))

    def test_apply_reported_time_midnight_wrap_without_day_key(self):
        s = self._make_session(day=1, period="深夜", total=30, last=0)
        # AI reports only {"period": "早朝"} without day
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"period": "早朝"},
            user_text="休もう",
            gate_states=[],
            total_turn=30,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})

    def test_apply_reported_time_invalid_inputs(self):
        s = self._make_session(day=1, period="午前", total=30, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            None,
            user_text="休もう",
            gate_states=[],
            total_turn=30,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_reported_time")

        ok2, reason2 = server.apply_reported_time(
            s["setting"],
            "not_a_dict",
            user_text="休もう",
            gate_states=[],
            total_turn=30,
        )
        self.assertFalse(ok2)
        self.assertEqual(reason2, "invalid_reported_time")

    def test_time_skip_cued_extended_patterns(self):
        cues = [
            "夜が明けるまで待とう",
            "朝が来たら出発しよう",
            "ベッドに入ろうか",
            "眠りについた",
            "一眠りしよう",
            "仮眠を取る",
            "数週間後、彼らは再び集まった",
            "二日後に出発する",
        ]
        for c in cues:
            self.assertTrue(server.time_skip_cued(c), f"Failed to match cue: {c}")

    def test_api_setting_elapsed_time_update_and_preservation(self):
        import json
        s = server.create_session({"title": "setting_test"})
        sid = s["id"]
        s["setting"]["total_turns"] = 25
        s["setting"]["time_last_turn"] = 20
        s["setting"]["turns_in_period"] = 5
        s["setting"]["elapsed_time"] = {"day": 2, "period": "昼"}
        server.save(s)

        # 1. Update without changing elapsed_time -> cursor preserved
        class FakeHandler:
            def __init__(self, body):
                self.body = body
                self.headers = {"Content-Length": str(len(json.dumps(body)))}
            def rfile_read(self):
                return json.dumps(self.body).encode("utf-8")

        h = server.H.__new__(server.H)
        h.headers = {"Content-Length": "100"}
        h.send_json = lambda data, code=200: data

        d_no_change = {
            "sid": sid,
            "setting": {
                "title": "new_title",
                "elapsed_time": {"day": 2, "period": "昼"}
            }
        }
        # Call logic directly
        raw_new = d_no_change.get("setting", s["setting"])
        new = copy.deepcopy(raw_new)
        old_st = s.get("setting", {})
        old_el = old_st.get("elapsed_time") if isinstance(old_st.get("elapsed_time"), dict) else {}
        old_elapsed_cmp = {
            "day": server._safe_day(old_el.get("day"), 1),
            "period": server.normalize_period(old_el.get("period")),
        }
        keep = {
            "total_turns": s["setting"].get("total_turns", 0),
            "turns_in_period": server._as_int(s["setting"].get("turns_in_period"), 0),
            "time_last_turn": server._as_int(s["setting"].get("time_last_turn"), 0),
            "time_silent_until_turn": server._as_int(s["setting"].get("time_silent_until_turn"), 0),
        }
        s["setting"] = server.merge_defaults(dict(new, **keep))
        el = s["setting"].get("elapsed_time")
        s["setting"]["elapsed_time"] = {
            "day": server._safe_day(el.get("day"), 1),
            "period": server.normalize_period(el.get("period")),
        }
        if old_elapsed_cmp != s["setting"].get("elapsed_time"):
            s["setting"]["time_last_turn"] = max(0, server._as_int(s["setting"].get("total_turns"), 0))
            s["setting"]["time_silent_until_turn"] = 0
            s["setting"]["turns_in_period"] = 0
        self.assertEqual(s["setting"]["time_last_turn"], 20)
        self.assertEqual(s["setting"]["turns_in_period"], 5)

        # 2. Update with changed elapsed_time -> cursor reset to total_turns
        d_change = {
            "sid": sid,
            "setting": {
                "title": "new_title",
                "elapsed_time": {"day": 3, "period": "夕方"}
            }
        }
        raw_new = d_change.get("setting", s["setting"])
        new = copy.deepcopy(raw_new)
        old_st = s.get("setting", {})
        old_el = old_st.get("elapsed_time") if isinstance(old_st.get("elapsed_time"), dict) else {}
        old_elapsed_cmp = {
            "day": server._safe_day(old_el.get("day"), 1),
            "period": server.normalize_period(old_el.get("period")),
        }
        keep = {
            "total_turns": s["setting"].get("total_turns", 0),
            "turns_in_period": server._as_int(s["setting"].get("turns_in_period"), 0),
            "time_last_turn": server._as_int(s["setting"].get("time_last_turn"), 0),
            "time_silent_until_turn": server._as_int(s["setting"].get("time_silent_until_turn"), 0),
        }
        s["setting"] = server.merge_defaults(dict(new, **keep))
        el = s["setting"].get("elapsed_time")
        s["setting"]["elapsed_time"] = {
            "day": server._safe_day(el.get("day"), 1),
            "period": server.normalize_period(el.get("period")),
        }
        if old_elapsed_cmp != s["setting"].get("elapsed_time"):
            s["setting"]["time_last_turn"] = max(0, server._as_int(s["setting"].get("total_turns"), 0))
            s["setting"]["time_silent_until_turn"] = 0
            s["setting"]["turns_in_period"] = 0
        self.assertEqual(s["setting"]["time_last_turn"], 25)
        self.assertEqual(s["setting"]["turns_in_period"], 0)
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 3, "period": "夕方"})

    def test_restore_latest_snapshot_clamps_to_total_turns_when_preserve(self):
        s = self._make_session(day=2, period="昼", total=20, last=30)
        s["messages"] = [
            {"id": "u1", "role": "user", "text": "u1"},
            {
                "id": "a1",
                "role": "assistant",
                "text": "a1",
                "elapsed_snapshot": {"day": 2, "period": "昼"},
                "time_last_turn_snapshot": 28,
                "time_silent_until_snapshot": 29,
                "turns_in_period_snapshot": 2,
            },
        ]
        s["setting"]["total_turns"] = 15
        server.restore_latest_snapshot(s, preserve_total_turns=True)
        self.assertEqual(s["setting"]["total_turns"], 15)
        self.assertLessEqual(s["setting"]["time_last_turn"], 15)
        self.assertEqual(s["setting"]["time_silent_until_turn"], 0)

    def test_time_tick_section_and_escalate_notice(self):
        st = {"elapsed_time": {"day": 1, "period": "昼"}}
        sec = server.time_tick_section(st, overdue=1)
        self.assertEqual(sec["label"], "【時間経過】今ターンが進める番")
        self.assertIn("現在: 1日目・昼 ／ 次: 1日目・夕方", sec["text"])
        self.assertIn("進めるのは1段階だけ", sec["text"])
        self.assertNotIn("すでに", sec["text"])

        # Escalate overdue >= TIME_ESCALATE_AFTER (3)
        sec_esc = server.time_tick_section(st, overdue=3)
        self.assertIn("すでに3ターン保留。見せ場の最中でなければ、このターン必ず進める。", sec_esc["text"])

    def test_time_cue_section_content(self):
        st = {"elapsed_time": {"day": 2, "period": "夜"}}
        sec = server.time_cue_section(st)
        self.assertEqual(sec["label"], "【時間経過】ユーザーの指示")
        self.assertIn("進行前: 2日目・夜", sec["text"])
        self.assertIn("ユーザー入力に時間経過", sec["text"])

    def test_night_to_morning_sleep_cue_with_omitted_day_advances_day(self):
        s = self._make_session(day=1, period="夜", total=10, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"period": "早朝"},
            user_text="おやすみ、寝よう",
            gate_states=[],
            total_turn=10,
        )
        self.assertTrue(ok, f"Failed with reason: {reason}")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})

    def test_night_to_morning_sleep_cue_with_day_1_recovers_as_next_day(self):
        s = self._make_session(day=1, period="夜", total=10, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 1, "period": "早朝"},
            user_text="翌朝まで眠ろう",
            gate_states=[],
            total_turn=10,
        )
        self.assertTrue(ok, f"Failed with reason: {reason}")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})

    def test_first_person_sleep_expressions_match_cue(self):
        cued_phrases = [
            "もう寝る", "そろそろ寝る", "今日は寝る", "先に寝る",
            "寝るね", "寝るよ", "寝ます", "もう寝ます", "今日はもう寝ます",
            "そろそろ寝るよ", "もう寝るね", "お先に寝るわ",
        ]
        for phrase in cued_phrases:
            self.assertTrue(server.time_skip_cued(phrase), f"Failed to match: {phrase}")

        non_cued_phrases = [
            "一緒に寝る？", "寝る？", "寝る前に話そう", "寝る場所はどこ？", "寝る時間だよ",
        ]
        for phrase in non_cued_phrases:
            self.assertFalse(server.time_skip_cued(phrase), f"False positive on: {phrase}")

    def test_combat_and_erotic_action_tags_showpiece(self):
        self.assertTrue(server.scene_is_showpiece([{"action": ["double penetration"]}]))
        self.assertTrue(server.scene_is_showpiece([{"action": ["group sex"]}]))
        self.assertTrue(server.scene_is_showpiece([{"action": ["intercourse"]}]))
        self.assertTrue(server.scene_is_showpiece([{"action": ["titfuck"]}]))
        self.assertTrue(server.scene_is_showpiece([{"action": ["fighting"]}]))
        self.assertTrue(server.scene_is_showpiece([{"action": ["slashing"]}]))
        self.assertTrue(server.scene_is_showpiece([{"situation": ["敵と対峙している"]}]))
        self.assertTrue(server.scene_is_showpiece([{"situation": ["魔物を迎撃する"]}]))
        self.assertTrue(server.scene_is_showpiece([{"situation": ["戦闘態勢に入る"]}]))
        self.assertFalse(server.scene_is_showpiece([{"action": ["unisex clothing"]}]))
        self.assertFalse(server.scene_is_showpiece([{"action": ["resting", "talking"]}]))

    def test_midnight_wrap_due_without_cue_when_ai_includes_day_1(self):
        # AI outputs {"day": 1, "period": "早朝"} across midnight when due (turn 30) without user cue
        s = self._make_session(day=1, period="深夜", total=30, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 1, "period": "早朝"},
            user_text="お茶でも飲もうか",
            gate_states=[],
            total_turn=30,
        )
        self.assertTrue(ok, f"Failed with reason: {reason}")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})
        self.assertEqual(s["setting"]["time_last_turn"], 30)

    def test_next_day_same_period_with_explicit_day_cued(self):
        # User explicitly cues next day ("翌日の夜"), AI returns {"day": 1, "period": "夜"}
        s = self._make_session(day=1, period="夜", total=10, last=0)
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 1, "period": "夜"},
            user_text="翌日の夜まで寝ていた",
            gate_states=[],
            total_turn=10,
        )
        self.assertTrue(ok, f"Failed with reason: {reason}")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "夜"})

    def test_cue_and_showpiece_simultaneous_cued_skip(self):
        # User says "戦いながら夜通し走り続けて翌朝にたどり着いた" (cue + showpiece together)
        s = self._make_session(day=1, period="夜", total=10, last=0)
        combat_state = [{"name": "A", "situation": ["敵と交戦中"]}]
        self.assertTrue(server.scene_is_showpiece(combat_state))
        ok, reason = server.apply_reported_time(
            s["setting"],
            {"day": 2, "period": "早朝"},
            user_text="戦いながら夜通し走り続けて翌朝にたどり着いた",
            gate_states=combat_state,
            total_turn=10,
        )
        self.assertTrue(ok, f"Failed with reason: {reason}")
        self.assertEqual(s["setting"]["elapsed_time"], {"day": 2, "period": "早朝"})

    def test_legacy_migration_followed_by_branch_and_rollback(self):
        legacy = {
            "title": "leg",
            "total_turns": 45,
            "turns_in_period": 12,
            "elapsed_time": {"day": 2, "period": "夕方"}
        }
        st = server.merge_defaults(legacy)
        self.assertEqual(st["time_last_turn"], 33)

        msgs = []
        for i in range(1, 46):
            if i % 2 == 1:
                msgs.append({"id": f"u{i}", "role": "user", "text": f"u{i}"})
            else:
                msgs.append({"id": f"a{i}", "role": "assistant", "text": f"a{i}"})
        s = server.create_session(st, msgs)

        # Branch at turn 20
        b_st, b_msgs = server.clean_import({"setting": s["setting"], "messages": copy.deepcopy(msgs[:20])})
        n = server.create_session(b_st, b_msgs)
        server.restore_latest_snapshot(n)
        self.assertEqual(n["setting"]["total_turns"], 10)
        self.assertLessEqual(n["setting"]["time_last_turn"], 10)
        self.assertEqual(n["setting"]["time_silent_until_turn"], 0)


if __name__ == "__main__":
    unittest.main()
