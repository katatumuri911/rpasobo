# -*- coding: utf-8 -*-
"""memo-budget-plan-v1.2.md の各要件・新設計を網羅検証する単体テスト群"""
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
import urllib.request
import urllib.error
from contextlib import redirect_stdout

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
import wait_input
import tools.check_context as check_context

# serve_forever の待ち時間。既定の 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒ブロックする
_POLL = 0.02


def make_msg(role, text, mid=None, **kwargs):
    d = {"id": mid or server.new_id(), "role": role, "text": text, "ts": server.now()}
    d.update(kwargs)
    return d


class TestMemoBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_memo_budget_")
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
        server.session_cache.clear()

        self.orig_env_budget = os.environ.get("RPCHAT_MEMO_BUDGET")
        self.orig_rot_enabled = server.ROTATION_ENABLED

        # HTTP サーバーの立ち上げ（API テスト用）
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.th.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.th.join(timeout=2)

        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        if self.orig_outbox is not None:
            wait_input.OUTBOX = self.orig_outbox
        if self.orig_done is not None:
            wait_input.DONE = self.orig_done
        if self.orig_ctx_dir is not None:
            wait_input.CTX_DIR = self.orig_ctx_dir

        if self.orig_env_budget is not None:
            os.environ["RPCHAT_MEMO_BUDGET"] = self.orig_env_budget
        else:
            os.environ.pop("RPCHAT_MEMO_BUDGET", None)
        server.ROTATION_ENABLED = self.orig_rot_enabled

        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    # 1. memo_stats の閾値判定
    def test_memo_stats_thresholds(self):
        """1,100B, 1,300B, 2,100B の各閾値判定"""
        # 1,100B: 未超過
        t1100 = "あ" * (1100 // 3) + "a" * (1100 % 3)
        self.assertEqual(server.memo_bytes(t1100), 1100)
        s1100 = server.memo_stats(t1100)
        self.assertFalse(s1100["memo_over_target"])
        self.assertFalse(s1100["memo_over_budget"])

        # 1,300B: 目標超過 (1200B超) だがハードバジェット (2000B) 未満
        t1300 = "あ" * (1300 // 3) + "a" * (1300 % 3)
        self.assertEqual(server.memo_bytes(t1300), 1300)
        s1300 = server.memo_stats(t1300)
        self.assertTrue(s1300["memo_over_target"])
        self.assertFalse(s1300["memo_over_budget"])

        # 2,100B: 目標・ハードバジェットともに超過
        t2100 = "あ" * (2100 // 3) + "a" * (2100 % 3)
        self.assertEqual(server.memo_bytes(t2100), 2100)
        s2100 = server.memo_stats(t2100)
        self.assertTrue(s2100["memo_over_target"])
        self.assertTrue(s2100["memo_over_budget"])

        # 境界値: ちょうど 1200B, 2000B
        t1200 = "a" * 1200
        s1200 = server.memo_stats(t1200)
        self.assertFalse(s1200["memo_over_target"])
        self.assertFalse(s1200["memo_over_budget"])

        t2000 = "a" * 2000
        s2000 = server.memo_stats(t2000)
        self.assertTrue(s2000["memo_over_target"])
        self.assertFalse(s2000["memo_over_budget"])

        # 空 / None
        s_none = server.memo_stats(None)
        self.assertEqual(s_none["memo_bytes"], 0)
        self.assertFalse(s_none["memo_over_target"])
        self.assertFalse(s_none["memo_over_budget"])

    # 2. memo_due は rot_armed のみで回転判定を行う
    def test_memo_due_uses_rot_armed_only(self):
        """非IDE・30ターンで memo_due が永久 True にならず、回転 armed の時だけ True になること"""
        s = server.create_session({"title": "非IDEテスト", "ide_session": ""})
        sid = s["id"]
        # 30ターン追加
        for i in range(30):
            s["messages"].append(make_msg("user", f"u{i}", mid=f"u_{i}"))
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
        last_u = s["messages"][-2]
        # 直前の assistant 返答で memo 更新されたものとする
        s["setting"]["last_memo_mid"] = s["messages"][-1]["id"]
        server.save(s)

        # armed ではないため、file_user_turns >= 30 でも memo_due は False
        ctx = server.build_context(s, last_u)
        self.assertFalse(any("定期更新ターン" in sec["label"] or "世代交代" in sec["label"] for sec in ctx["sections"]))

        # armed を設定すると memo_due が True になり世代交代セクションが出る
        s["setting"]["rotation"] = {"phase": "armed", "armed_turn": 30}
        server.save(s)
        ctx_armed = server.build_context(s, last_u)
        turnover_sec = next((sec for sec in ctx_armed["sections"] if "世代交代" in sec["label"]), None)
        self.assertIsNotNone(turnover_sec)

    # 3. armed でなければ世代交代セクションが出ない
    def test_rotation_section_not_shown_without_armed(self):
        """非IDE・ROTATION_ENABLED=1 でも 0 でも、armed なしなら世代交代セクションが出ない"""
        s = server.create_session({"title": "回転テスト", "ide_session": ""})
        for i in range(30):
            s["messages"].append(make_msg("user", f"u{i}", mid=f"u_{i}"))
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
        target_m = s["messages"][-2]
        server.save(s)

        server.ROTATION_ENABLED = True
        ctx = server.build_context(s, target_m)
        self.assertFalse(any("世代交代" in sec["label"] for sec in ctx["sections"]))

        server.ROTATION_ENABLED = False
        ctx2 = server.build_context(s, target_m)
        self.assertFalse(any("世代交代" in sec["label"] for sec in ctx2["sections"]))

    # 4. armed のときは世代交代セクションが出る
    def test_rotation_section_shown_when_armed(self):
        """armed の時に世代交代セクションが出現し、フラグで文面が切り替わる"""
        s = server.create_session({"title": "armedテスト", "ide_session": "ide-1"})
        sid = s["id"]
        for i in range(30):
            s["messages"].append(make_msg("user", f"u{i}", mid=f"u_{i}"))
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
        target_m = s["messages"][-2]
        s["setting"]["rotation"] = {"phase": "armed", "armed_turn": 30}
        server.save(s)

        # RPCHAT_MEMO_BUDGET=1
        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx1 = server.build_context(s, target_m)
        sec1 = next(sec for sec in ctx1["sections"] if "世代交代" in sec["label"])
        self.assertIn(f"{server.MEMO_TARGET_BYTES}B以内", sec1["text"])
        self.assertIn("1日1行に圧縮", sec1["text"])
        self.assertIn("【memoの予算と構成】", sec1["text"])

        # RPCHAT_MEMO_BUDGET=0
        os.environ["RPCHAT_MEMO_BUDGET"] = "0"
        ctx0 = server.build_context(s, target_m)
        sec0 = next(sec for sec in ctx0["sections"] if "世代交代" in sec["label"])
        self.assertIn("詳細なあらすじ", sec0["text"])
        self.assertNotIn("【memoの予算と構成】", sec0["text"])

    # 5. 既存の定期更新ラベル維持とガイド追記
    def test_existing_memo_reminder_label_kept(self):
        """10ターン経過時に「【重要】あらすじ（memo）の定期更新ターン」ラベルが残り、予算フラグでガイドが追記される"""
        s = server.create_session({"title": "定期更新テスト", "ide_session": "ide-1"})
        s["messages"].append(make_msg("user", "u0", mid="u_0"))
        for i in range(10):
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
            s["messages"].append(make_msg("user", f"u{i+1}", mid=f"u_{i+1}"))
        target_m = s["messages"][-1]
        server.save(s)

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx_on = server.build_context(s, target_m)
        sec_on = next(sec for sec in ctx_on["sections"] if sec["label"] == "【重要】あらすじ（memo）の定期更新ターン")
        self.assertIn("【memoの予算と構成】", sec_on["text"])
        self.assertIn(f"前回のあらすじ更新から10ターン経過しました", sec_on["text"])

        os.environ["RPCHAT_MEMO_BUDGET"] = "0"
        ctx_off = server.build_context(s, target_m)
        sec_off = next(sec for sec in ctx_off["sections"] if sec["label"] == "【重要】あらすじ（memo）の定期更新ターン")
        self.assertNotIn("【memoの予算と構成】", sec_off["text"])

    # 6. ガイドが別セクションとして重複しない
    def test_memo_guide_not_duplicated(self):
        """10ターン経過時に【memo更新の書き方】等の別セクションが出ないこと"""
        s = server.create_session({"title": "重複防止テスト", "ide_session": "ide-1"})
        s["messages"].append(make_msg("user", "u0", mid="u_0"))
        for i in range(10):
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
            s["messages"].append(make_msg("user", f"u{i+1}", mid=f"u_{i+1}"))
        target_m = s["messages"][-1]
        server.save(s)

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx = server.build_context(s, target_m)
        labels = [sec["label"] for sec in ctx["sections"]]
        self.assertNotIn("【memo更新の書き方】", labels)
        self.assertNotIn("【memoの予算と構成】", labels)
        self.assertEqual(labels.count("【重要】あらすじ（memo）の定期更新ターン"), 1)

    # 7. consolidation 履歴が target == last_memo_mid のとき空になりフォールバック文言が入る
    def test_consolidation_history_empty_when_target_has_memo(self):
        """last_memo_mid == idx のとき履歴0件となり空ログフォールバックが入る"""
        s = server.create_session({"title": "consolidationテスト", "character": {"name": "ラピ"}})
        m_asst = make_msg("assistant", "直前の応答", mid="a_last")
        s["messages"].append(make_msg("user", "u1", mid="u_1"))
        s["messages"].append(m_asst)
        s["setting"]["last_memo_mid"] = "a_last"
        server.save(s)

        hist = server.consolidation_history(s, 1)
        self.assertEqual(len(hist), 0)

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx = server.build_consolidation_context(s, m_asst)
        log_sec = next(sec for sec in ctx["sections"] if "会話ログ" in sec["label"])
        self.assertIn("前回memo更新以降の新規会話はありません", log_sec["text"])

    # 8. consolidation 履歴が最大10ユーザーターン
    def test_consolidation_history_max_10_turns(self):
        """前回memo更新がない、または古い場合、最大10ユーザーターンに制限される"""
        s = server.create_session({"title": "履歴上限テスト"})
        for i in range(25):
            s["messages"].append(make_msg("user", f"ユーザー {i}", mid=f"u_{i}"))
            s["messages"].append(make_msg("assistant", f"アシスタント {i}", mid=f"a_{i}"))
        server.save(s)

        hist = server.consolidation_history(s, len(s["messages"]) - 1)
        user_msgs = [m for m in hist if m["role"] == "user"]
        self.assertEqual(len(user_msgs), 10)
        self.assertEqual(user_msgs[0]["id"], "u_15")
        self.assertEqual(user_msgs[-1]["id"], "u_24")

    # 9. consolidation が memo を保存したら last_memo_mid が更新される
    def test_consolidation_sets_last_memo_mid(self):
        """consolidation 応答の取り込みで last_memo_mid, memo_ts, memo_stats が正しく記録される"""
        s = server.create_session({"title": "cons outbox テスト"})
        sid = s["id"]
        a1 = make_msg("assistant", "直前応答", mid="asst_cons_target")
        s["messages"].extend([make_msg("user", "u1", mid="u1"), a1])
        server.save(s)

        out_path = os.path.join(server.OUTBOX, f"{sid}_cons_reply.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "asst_cons_target",
                "type": "consolidation",
                "memo": "新しく統合されたmemo全文",
                "elapsed_time": {"day": 2, "period": "早朝"},
            }, f)

        server.process_outbox()
        s_after = server.load(sid)
        st = s_after["setting"]
        self.assertEqual(st["memo"], "新しく統合されたmemo全文")
        self.assertEqual(st["last_memo_mid"], "asst_cons_target")
        self.assertTrue(st.get("memo_ts"))
        self.assertEqual(st["memo_bytes"], len("新しく統合されたmemo全文".encode("utf-8")))
        self.assertFalse(st["memo_over_budget"])

    # 10. consolidation の ctx_file と force_spill
    def test_consolidation_uses_ctx_file_and_force_spill(self):
        """RPCHAT_MEMO_BUDGET=1 のとき force_spill=True, ctx_file 指定、0 のとき light=True"""
        s = server.create_session({"title": "spillフラグテスト"})
        m = make_msg("assistant", "返答", mid="a1")
        s["messages"].extend([make_msg("user", "u1", mid="u1"), m])
        server.save(s)

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx1 = server.build_consolidation_context(s, m)
        self.assertFalse(ctx1["light"])
        self.assertTrue(ctx1["force_spill"])
        self.assertEqual(ctx1["ctx_file"], f"{s['id']}_consolidation.md")

        os.environ["RPCHAT_MEMO_BUDGET"] = "0"
        ctx0 = server.build_consolidation_context(s, m)
        self.assertTrue(ctx0["light"])
        self.assertFalse(ctx0["force_spill"])
        self.assertIsNone(ctx0["ctx_file"])

    # 11. 手動編集直後の memo_due 抑制
    def test_manual_memo_edit_suppresses_memo_due(self):
        """手動編集直後は memo_due=False となり、10アシスタントターン後に True になる"""
        s = server.create_session({"title": "手動編集抑制テスト", "ide_session": "ide-1"})
        sid = s["id"]
        # 10アシスタントターン作成（通常なら memo_due 発火）
        s["messages"].append(make_msg("user", "u0", mid="u_0"))
        for i in range(10):
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
            s["messages"].append(make_msg("user", f"u{i+1}", mid=f"u_{i+1}"))
        server.save(s)

        # 手動編集前は memo_due が発火する
        ctx_before = server.build_context(s, s["messages"][-1])
        self.assertTrue(any("定期更新ターン" in sec["label"] for sec in ctx_before["sections"]))

        # 手動編集を /api/setting 経由で実行
        setting_data = dict(s["setting"], memo="手動で短縮・整理した最新メモ")
        code, resp = self.post("/api/setting", {"setting": setting_data, "sid": sid})
        self.assertEqual(code, 200)

        s_edited = server.load(sid)
        self.assertEqual(s_edited["setting"]["memo_manual_edit_mid"], "a_9")

        # 手動編集直後のターンでは memo_due が抑制される
        next_u = make_msg("user", "手動編集直後の発言", mid="u_after_edit")
        s_edited["messages"].append(next_u)
        server.save(s_edited)

        ctx_after_edit = server.build_context(s_edited, next_u)
        self.assertFalse(any("定期更新ターン" in sec["label"] for sec in ctx_after_edit["sections"]))

        # 手動編集後さらに10アシスタントターン進める
        for i in range(10):
            s_edited["messages"].append(make_msg("assistant", f"a_new_{i}", mid=f"a_new_{i}"))
            s_edited["messages"].append(make_msg("user", f"u_new_{i}", mid=f"u_new_{i}"))
        server.save(s_edited)

        target_due = s_edited["messages"][-1]
        ctx_due = server.build_context(s_edited, target_due)
        self.assertTrue(any("定期更新ターン" in sec["label"] for sec in ctx_due["sections"]))

    # 12. 手動編集は last_memo_mid を動かさない
    def test_manual_memo_edit_does_not_move_last_memo_mid(self):
        """手動編集を行っても consolidation 用の last_memo_mid は書き換わらない"""
        s = server.create_session({"title": "last_memo_mid 不変テスト"})
        sid = s["id"]
        s["setting"]["last_memo_mid"] = "mid_initial_anchor"
        s["messages"].append(make_msg("assistant", "a1", mid="a_1"))
        server.save(s)

        setting_data = dict(s["setting"], memo="新しい手動メモ")
        code, resp = self.post("/api/setting", {"setting": setting_data, "sid": sid})
        self.assertEqual(code, 200)

        s_loaded = server.load(sid)
        self.assertEqual(s_loaded["setting"]["last_memo_mid"], "mid_initial_anchor")
        self.assertEqual(s_loaded["setting"]["memo_manual_edit_mid"], "a_1")

    # 13. reset で memo 状態がクリアされ初期値から再計算
    def test_reset_clears_memo_state(self):
        """reset で memo_over_budget や memo_manual_edit_mid が消え初期memoの統計に更新される"""
        s = server.create_session({"title": "リセットテスト"})
        sid = s["id"]
        s["setting"]["initial_memo"] = "初期の短いあらすじ"
        s["setting"]["memo"] = "あ" * 1000  # 3000B (ハード超過)
        s["setting"]["last_memo_mid"] = "old_mid"
        s["setting"]["memo_manual_edit_mid"] = "old_manual_mid"
        s["setting"]["memo_over_budget"] = True
        s["setting"]["memo_bytes"] = 3000
        server.save(s)

        code, resp = self.post("/api/reset", {"sid": sid})
        self.assertEqual(code, 200)

        s_reset = server.load(sid)
        st = s_reset["setting"]
        self.assertEqual(st["memo"], "初期の短いあらすじ")
        self.assertNotIn("last_memo_mid", st)
        self.assertNotIn("memo_manual_edit_mid", st)
        self.assertNotIn("memo_manual_edit_ts", st)
        self.assertFalse(st["memo_over_budget"])
        self.assertFalse(st["memo_over_target"])
        self.assertEqual(st["memo_bytes"], len("初期の短いあらすじ".encode("utf-8")))

    # 14. memo_as_initial API
    def test_memo_as_initial_api(self):
        """memo_as_initial: true で現在の memo が initial_memo に保存されること"""
        s = server.create_session({"title": "初期保存テスト"})
        sid = s["id"]
        s["messages"].append(make_msg("user", "会話開始後の発言", mid="u1"))
        s["setting"]["initial_memo"] = "古い初期メモ"
        server.save(s)

        setting_payload = dict(s["setting"], memo="確定版の初期あらすじ")
        code, resp = self.post("/api/setting", {
            "sid": sid,
            "setting": setting_payload,
            "memo_as_initial": True,
        })
        self.assertEqual(code, 200)

        s_saved = server.load(sid)
        st = s_saved["setting"]
        self.assertEqual(st["memo"], "確定版の初期あらすじ")
        self.assertEqual(st["initial_memo"], "確定版の初期あらすじ")
        self.assertNotIn("memo_as_initial", st)

    # 15. wait_input の subtask 文言が指定ファイル許可になっている
    def test_consolidation_subtask_allows_spill_file(self):
        """wait_input.py の subtask 文言において consolidation は「指定されたファイルがある場合」と表示"""
        sid = "sid-subtask-test"
        m = {"id": "m1"}

        ctx_cons = {"mode": "consolidation", "sections": []}
        f_cons = io.StringIO()
        with redirect_stdout(f_cons):
            try:
                wait_input.show(sid, m, ctx=ctx_cons)
            except SystemExit:
                pass
        out_cons = f_cons.getvalue()
        self.assertIn("明示されたファイルパスを読む以外のファイル閲覧・検索", out_cons)

        ctx_choices = {"mode": "choices", "sections": []}
        f_choices = io.StringIO()
        with redirect_stdout(f_choices):
            try:
                wait_input.show(sid, m, ctx=ctx_choices)
            except SystemExit:
                pass
        out_choices = f_choices.getvalue()
        self.assertIn("追加の run_command や read_file は一切使わず", out_choices)
        self.assertNotIn("明示されたファイルパスを読む以外のファイル閲覧・検索", out_choices)

    # 16. 超過memoの短縮リマインド（memo_due でないときだけ出現）
    def test_memo_over_budget_reminder_section(self):
        """2000B超過の memo は、通常ターン (memo_due=False) で短縮リマインドが出現し、memo_due 時は重複しない"""
        s = server.create_session({"title": "短縮リマインドテスト", "ide_session": "ide-1"})
        sid = s["id"]
        s["setting"]["memo"] = "あ" * 700  # 2100B > 2000B
        s["setting"].update(server.memo_stats(s["setting"]["memo"]))
        self.assertTrue(s["setting"]["memo_over_budget"])

        u1 = make_msg("user", "u1", mid="u_1")
        a1 = make_msg("assistant", "a1", mid="a_1")
        u2 = make_msg("user", "u2", mid="u_2")
        s["messages"].extend([u1, a1, u2])
        # last_memo_mid を a1 にして asst_after = 0 (memo_due=False)
        s["setting"]["last_memo_mid"] = "a_1"
        server.save(s)

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx = server.build_context(s, u2)
        remind_sec = next((sec for sec in ctx["sections"] if "【memo短縮】" in sec["label"]), None)
        self.assertIsNotNone(remind_sec)
        self.assertIn(f"前回のmemoが{server.MEMO_HARD_BYTES}Bを超えています", remind_sec["text"])

        # memo_due が発火した場合は【memo短縮】が重複しない（定期更新ガイドに包含）
        for i in range(10):
            s["messages"].append(make_msg("assistant", f"a_extra_{i}", mid=f"ae_{i}"))
            s["messages"].append(make_msg("user", f"u_extra_{i}", mid=f"ue_{i}"))
        server.save(s)
        ctx_due = server.build_context(s, s["messages"][-1])
        remind_sec_due = next((sec for sec in ctx_due["sections"] if "【memo短縮】" in sec["label"]), None)
        self.assertIsNone(remind_sec_due)
        self.assertTrue(any("定期更新ターン" in sec["label"] for sec in ctx_due["sections"]))

    # 17. 新規セッション・複製で親の超過memo状態が引き継がれず、短縮リマインドが誤爆しない
    def test_clone_and_new_session_clears_memo_over_budget_and_stats(self):
        """親が memo_over_budget=True でも、copy_from で複製された新チャンネルは memo='' かつ memo_over_budget=False となる"""
        parent = server.create_session({
            "title": "親チャンネル",
            "memo": "あ" * 1000,
            "memo_over_budget": True,
            "memo_bytes": 3000,
            "memo_manual_edit_mid": "old_edit_mid",
            "memo_manual_edit_ts": server.now(),
        })
        parent_id = parent["id"]

        code, resp = self.post("/api/session/new", {"copy_from": parent_id})
        self.assertEqual(code, 200)
        cloned_sid = resp["sid"]
        cloned = server.load(cloned_sid)
        cst = cloned["setting"]

        self.assertEqual(cst["memo"], "")
        self.assertFalse(cst["memo_over_budget"])
        self.assertFalse(cst["memo_over_target"])
        self.assertEqual(cst["memo_bytes"], 0)
        self.assertNotIn("memo_manual_edit_mid", cst)
        self.assertNotIn("memo_manual_edit_ts", cst)

        # 最初のターンで【memo短縮】が誤爆しないこと
        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        u1 = make_msg("user", "第1発言", mid="u_clone_1")
        cloned["messages"].append(u1)
        server.save(cloned)
        ctx = server.build_context(cloned, u1)
        self.assertFalse(any("【memo短縮】" in sec["label"] for sec in ctx["sections"]))

    # 18. create_session および restore_latest_snapshot の memo_stats 即時計算
    def test_create_session_and_restore_snapshot_computes_memo_stats(self):
        """create_session や restore_latest_snapshot 実行時に memo_stats が正しく算出・追従されること"""
        big_memo = "あ" * 800  # 2400B
        s = server.create_session({"title": "大容量メモ初期設定", "memo": big_memo})
        self.assertEqual(s["setting"]["memo_bytes"], len(big_memo.encode("utf-8")))
        self.assertTrue(s["setting"]["memo_over_budget"])
        self.assertTrue(s["setting"]["memo_over_target"])

        # restore_latest_snapshot（メッセージ全消去時）で初期memoの統計に更新
        s["setting"]["initial_memo"] = "短い初期memo"
        server.restore_latest_snapshot(s)
        self.assertEqual(s["setting"]["memo"], "短い初期memo")
        self.assertEqual(s["setting"]["memo_bytes"], len("短い初期memo".encode("utf-8")))
        self.assertFalse(s["setting"]["memo_over_budget"])

    # 19. /api/setting は内部memoアンカーをクライアント値で上書きしない
    def test_setting_cannot_overwrite_internal_memo_state(self):
        """古い state.setting を送っても last_memo_mid / manual マーカーは維持し、統計は再計算する"""
        big_memo = "あ" * 800  # 2400B
        s = server.create_session({"title": "内部memo保護テスト", "memo": big_memo})
        sid = s["id"]
        s["setting"]["last_memo_mid"] = "server_anchor"
        s["setting"]["memo_manual_edit_mid"] = "server_manual"
        s["setting"]["memo_manual_edit_ts"] = "2026-01-01T00:00:00"
        server.save(s)

        stale = dict(s["setting"],
                     title="変更後のタイトル",
                     last_memo_mid="stale_anchor",
                     memo_manual_edit_mid="stale_manual",
                     memo_manual_edit_ts="1999-01-01T00:00:00",
                     memo_bytes=0,
                     memo_over_target=False,
                     memo_over_budget=False)
        code, resp = self.post("/api/setting", {"sid": sid, "setting": stale})
        self.assertEqual(code, 200)

        st = server.load(sid)["setting"]
        self.assertEqual(st["title"], "変更後のタイトル")
        self.assertEqual(st["last_memo_mid"], "server_anchor")
        self.assertEqual(st["memo_manual_edit_mid"], "server_manual")
        self.assertEqual(st["memo_manual_edit_ts"], "2026-01-01T00:00:00")
        self.assertEqual(st["memo_bytes"], len(big_memo.encode("utf-8")))
        self.assertTrue(st["memo_over_target"])
        self.assertTrue(st["memo_over_budget"])

    # 20. 旧セッションのmemo統計をload時に再計算する
    def test_load_recomputes_memo_stats_for_old_session(self):
        """memo_bytes / memo_over_* を持たない旧JSONでも、load時に本文から統計を作り直す"""
        big_memo = "あ" * 800  # 2400B
        s = server.create_session({"title": "旧セッション統計テスト", "memo": big_memo})
        sid = s["id"]
        path = os.path.join(server.SESS_DIR, sid + ".json")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for k in ("memo_bytes", "memo_over_target", "memo_over_budget"):
            raw["setting"].pop(k, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        server.session_cache.clear()

        st = server.load(sid)["setting"]
        self.assertEqual(st["memo_bytes"], len(big_memo.encode("utf-8")))
        self.assertTrue(st["memo_over_target"])
        self.assertTrue(st["memo_over_budget"])

    # 21. restore_latest_snapshot がmemoアンカーも復元する
    def test_restore_snapshot_updates_memo_anchor(self):
        """memoを持つassistantへ巻き戻したら last_memo_mid もそのassistantへ合わせ、手動マーカーを消す"""
        s = server.create_session({"title": "memoアンカー復元テスト", "memo": "古いmemo"})
        a = make_msg("assistant", "巻き戻し先の応答", mid="a_restore", memo="復元されたmemo")
        s["messages"].append(a)
        s["setting"]["last_memo_mid"] = "old_anchor"
        s["setting"]["memo_manual_edit_mid"] = "old_manual"
        s["setting"]["memo_manual_edit_ts"] = "2026-01-01T00:00:00"

        server.restore_latest_snapshot(s)

        self.assertEqual(s["setting"]["memo"], "復元されたmemo")
        self.assertEqual(s["setting"]["last_memo_mid"], "a_restore")
        self.assertNotIn("memo_manual_edit_mid", s["setting"])
        self.assertNotIn("memo_manual_edit_ts", s["setting"])
        self.assertEqual(s["setting"]["memo_bytes"], len("復元されたmemo".encode("utf-8")))

    # 22. /api/setting はサーバー側が未設定でもクライアントから内部アンカーを注入できない
    def test_setting_cannot_inject_memo_internal_when_unset(self):
        """サーバー側が last_memo_mid を持たないときでも、クライアントから注入できない"""
        s = server.create_session({"title": "内部アンカー未設定チャンネル"})
        sid = s["id"]
        self.assertNotIn("last_memo_mid", s["setting"])
        self.assertNotIn("memo_manual_edit_mid", s["setting"])

        rogue_setting = dict(s["setting"],
                             last_memo_mid="injected_mid",
                             memo_manual_edit_mid="injected_manual_mid",
                             memo_bytes=9999,
                             memo_over_budget=True)
        code, resp = self.post("/api/setting", {"sid": sid, "setting": rogue_setting})
        self.assertEqual(code, 200)

        st = server.load(sid)["setting"]
        self.assertNotIn("last_memo_mid", st)
        self.assertNotIn("memo_manual_edit_mid", st)
        self.assertEqual(st["memo_bytes"], 0)
        self.assertFalse(st["memo_over_budget"])

    # 23. process_outbox が msg["memo"] を保持し、restore_latest_snapshot が孤立アンカーを除去する
    def test_restore_snapshot_clears_orphan_anchor_and_keeps_msg_memo(self):
        """AI返答のmemoがメッセージ自体に保存され、孤立したlast_memo_midはrestore時に除去される"""
        s = server.create_session({"title": "孤立アンカー除去テスト"})
        sid = s["id"]
        u1 = make_msg("user", "u1", mid="u1", status="pending")
        s["messages"].append(u1)
        server.save(s)

        out_path = os.path.join(server.OUTBOX, f"{sid}_asst_reply.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": "u1",
                "text": "返答本文",
                "memo": "AIが生成したメモ全文",
            }, f)
        server.process_outbox()

        # キャッシュクリア後（再起動後）でもディスクから msg["memo"] が復元されること
        server.session_cache.clear()
        s_loaded = server.load(sid)
        asst_msg = s_loaded["messages"][-1]
        self.assertEqual(asst_msg["memo"], "AIが生成したメモ全文")
        self.assertEqual(s_loaded["setting"]["last_memo_mid"], asst_msg["id"])

        # 次のターンを追加（memoなし）
        u2 = make_msg("user", "u2", mid="u2")
        a2 = make_msg("assistant", "a2", mid="a2")  # memoなし
        s_loaded["messages"].extend([u2, a2])
        # 意図的に last_memo_mid を会話から削除された古いIDに変更
        s_loaded["setting"]["last_memo_mid"] = "deleted_msg_id"
        server.save(s_loaded)

        server.restore_latest_snapshot(s_loaded)
        # 会話内に memo を持つメッセージ (asst_msg) が存在するので、そのIDへ修復される
        self.assertEqual(s_loaded["setting"]["last_memo_mid"], asst_msg["id"])
        self.assertEqual(s_loaded["setting"]["memo"], "AIが生成したメモ全文")

        # さらに memo を持つメッセージすら削除された場合
        s_loaded["messages"] = [u2, a2]
        s_loaded["setting"]["last_memo_mid"] = "deleted_msg_id"
        server.restore_latest_snapshot(s_loaded)
        # 孤立アンカーは綺麗に除去される
        self.assertNotIn("last_memo_mid", s_loaded["setting"])

    # 24. 手動編集後の定期更新文言が「前回のmemo編集から」になる
    def test_memo_periodic_reminder_wording_distinguishes_manual_edit(self):
        """手動編集後の定期更新ターンでは『前回のmemo編集からNターン』と表示される"""
        s = server.create_session({"title": "手動編集文言テスト", "ide_session": "ide-1"})
        sid = s["id"]
        s["messages"].append(make_msg("user", "u0", mid="u_0"))
        # 手動編集
        s["setting"]["memo"] = "手動で整えたあらすじ"
        s["setting"]["memo_manual_edit_mid"] = "u_0"
        s["setting"]["memo_manual_edit_ts"] = server.now()
        server.save(s)

        # 手動編集後10アシスタントターン経過
        for i in range(10):
            s["messages"].append(make_msg("assistant", f"a{i}", mid=f"a_{i}"))
            s["messages"].append(make_msg("user", f"u{i+1}", mid=f"u_{i+1}"))
        server.save(s)

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx = server.build_context(s, s["messages"][-1])
        sec = next(sec for sec in ctx["sections"] if sec["label"] == "【重要】あらすじ（memo）の定期更新ターン")
        self.assertIn("前回のmemo編集から10ターン経過しました", sec["text"])
        self.assertNotIn("前回のあらすじ更新から", sec["text"])

    # 25. check_context.inspect_memo の検証
    def test_inspect_memo_utility(self):
        """inspect_memo がセッションの memo 統計を正しく取得すること"""
        s = server.create_session({"title": "inspect_memoテスト", "memo": "あ" * 500})
        sid = s["id"]
        info = check_context.inspect_memo(sid)
        self.assertIsNotNone(info)
        self.assertEqual(info["sid"], sid)
        self.assertEqual(info["memo_bytes"], 1500)
        self.assertTrue(info["memo_over_target"])
        self.assertFalse(info["memo_over_budget"])

    # 26. memoを持たないメッセージの途中削除後に memo_as_initial を実行するフロー
    def test_delete_turn_without_memo_then_memo_as_initial(self):
        """過去メッセージにmemoを持つassistantが不在の状態でメッセージ削除後にmemo_as_initialが正常に働くこと"""
        s = server.create_session({"title": "エッジケーステスト", "memo": "手動で調整したあらすじ"})
        sid = s["id"]
        u1 = make_msg("user", "u1", mid="u1")
        a1 = make_msg("assistant", "a1", mid="a1")  # memoなし
        u2 = make_msg("user", "u2", mid="u2")
        a2 = make_msg("assistant", "a2", mid="a2")  # memoなし
        s["messages"].extend([u1, a1, u2, a2])
        server.save(s)

        # 最後のassistant (a2) を削除
        code, resp = self.post("/api/delete", {"sid": sid, "id": "a2"})
        self.assertEqual(code, 200)

        s_after = server.load(sid)
        self.assertEqual(s_after["setting"]["memo"], "手動で調整したあらすじ")
        self.assertNotIn("memo_manual_edit_mid", s_after["setting"])

        # memo_as_initial を呼び出し
        code, resp = self.post("/api/setting", {
            "sid": sid,
            "setting": s_after["setting"],
            "memo_as_initial": True,
        })
        self.assertEqual(code, 200)

        s_final = server.load(sid)
        self.assertEqual(s_final["setting"]["memo"], "手動で調整したあらすじ")
        self.assertEqual(s_final["setting"]["initial_memo"], "手動で調整したあらすじ")
        self.assertEqual(s_final["setting"]["memo_bytes"], len("手動で調整したあらすじ".encode("utf-8")))

    # 27. RPCHAT_MEMO_BUDGET=0 のときはハード上限超過でも短縮リマインドが出ない
    def test_memo_budget_off_suppresses_over_budget_section(self):
        """RPCHAT_MEMO_BUDGET=0 のときは memo が 2,000B を超えていても 【memo短縮】 セクションを注入しない"""
        s = server.create_session({"title": "予算無効化テスト", "ide_session": "ide-1"})
        sid = s["id"]
        s["setting"]["memo"] = "あ" * 800  # 2400B
        s["setting"].update(server.memo_stats(s["setting"]["memo"]))
        u1 = make_msg("user", "u1", mid="u_1")
        a1 = make_msg("assistant", "a1", mid="a_1")
        u2 = make_msg("user", "u2", mid="u_2")
        s["messages"].extend([u1, a1, u2])
        s["setting"]["last_memo_mid"] = "a_1"
        server.save(s)

        os.environ["RPCHAT_MEMO_BUDGET"] = "0"
        ctx = server.build_context(s, u2)
        self.assertFalse(any("【memo短縮】" in sec["label"] for sec in ctx["sections"]))

        os.environ["RPCHAT_MEMO_BUDGET"] = "1"
        ctx1 = server.build_context(s, u2)
        self.assertTrue(any("【memo短縮】" in sec["label"] for sec in ctx1["sections"]))


if __name__ == "__main__":
    unittest.main()
