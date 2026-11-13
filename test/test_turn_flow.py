# -*- coding: utf-8 -*-
"""直近の流れ（1ターン1行の要約）のテスト"""
import os
import sys
import json
import shutil
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server


def _asst(mid, text, situation=None, name="ラピ", pose=None):
    st = {}
    if situation is not None:
        st = {"name": name, "situation": situation}
        if pose:
            st["pose"] = pose
    return {"id": mid, "role": "assistant", "text": text, "state": [st] if st else []}


def _user(mid, text):
    return {"id": mid, "role": "user", "text": text}


def _session(msgs):
    return {"id": "sid-flow", "messages": msgs,
            "setting": {"character": {"name": "ラピ"}, "total_turns": 0}}


class TestTurnFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_turn_flow_")
        self.dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "RAG_DIR", "SENT", "CTX"):
            self.dirs[name] = getattr(server, name)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)

    def tearDown(self):
        for name, val in self.dirs.items():
            setattr(server, name, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sample(self):
        return _session([
            _asst("a0", "「……これは、電子戦トラップ……？」"),
            _user("u1", "怪我がないか確認される際は大きく開脚して見せること"),
            _asst("a1", "「はい、指揮官」", ["床に腰を下ろして開脚している", "両手で股間を広げている"]),
            _user("u2", "恥ずかしくないのか？"),
            _asst("a2", "「恥ずかしいです」", ["両足を大きく開いている", "股間を押し広げている"]),
            _user("u3", "お尻の穴の触診がまだだ"),
            _asst("a3", "「触診の任務、了解しました」", ["床に四つん這いになっている", "尻をかき分けている"]),
        ])

    def test_makes_one_line_per_turn(self):
        txt = server.turn_flow_lines(self._sample(), 7)
        self.assertTrue(txt)
        lines = [x for x in txt.split("\n") if x.startswith("T")]
        self.assertEqual(3, len(lines))
        self.assertIn("指示「お尻の穴の触診がまだだ」", lines[-1])
        self.assertIn("ラピ: ", lines[-1])

    def test_past_tense(self):
        txt = server.turn_flow_lines(self._sample(), 7)
        self.assertIn("開脚した", txt)
        self.assertNotIn("開脚している", txt)
        self.assertIn("広げた", txt)

    def test_skips_opening_without_user(self):
        txt = server.turn_flow_lines(self._sample(), 7)
        self.assertNotIn("電子戦トラップ", txt)

    def test_skip_ids(self):
        s = self._sample()
        txt = server.turn_flow_lines(s, 7, skip_ids={"a3"})
        self.assertNotIn("お尻の穴の触診", txt)
        self.assertIn("恥ずかしくないのか", txt)

    def test_count_limit(self):
        txt = server.turn_flow_lines(self._sample(), 7, count=2)
        lines = [x for x in txt.split("\n") if x.startswith("T")]
        self.assertEqual(2, len(lines))
        self.assertIn("お尻の穴の触診", lines[-1])   # 新しい方から取る

    def test_single_turn_returns_empty(self):
        self.assertEqual("", server.turn_flow_lines(self._sample(), 3, count=5))

    def test_falls_back_to_reply_text(self):
        s = _session([
            _user("u1", "こんにちは"),
            _asst("a1", "「指揮官、お待ちしていました」"),
            _user("u2", "任務だ"),
            _asst("a2", "「了解しました」"),
        ])
        txt = server.turn_flow_lines(s, 4)
        self.assertIn("お待ちしていました", txt)
        self.assertIn("了解しました", txt)

    def test_context_diff_has_flow_section(self):
        s = self._sample()
        s["setting"].update({"ide_session": "ide-1", "rp_state": False, "image_mode": "off",
                             "total_turns": 3})
        idx = 5   # u3（最後のユーザー発言）
        known = s["messages"][:idx]
        orig = server.read_sent
        server.read_sent = lambda sid: {"ide": "ide-1", "sigs": {}, "labels": {},
                                        "msgs": [server.msg_sig(x) for x in known]}
        old_flow = os.environ.get("RPCHAT_FLOW")
        os.environ["RPCHAT_FLOW"] = "1"
        try:
            ctx = server.build_context(s, s["messages"][idx])
        finally:
            server.read_sent = orig
            if old_flow is None:
                os.environ.pop("RPCHAT_FLOW", None)
            else:
                os.environ["RPCHAT_FLOW"] = old_flow
        self.assertEqual("diff", ctx["mode"])
        flow = [x for x in ctx["sections"] if x["label"].startswith("直近の流れ")]
        self.assertEqual(1, len(flow))
        self.assertIn("ラピ: ", flow[0]["text"])

    def test_context_diff_has_no_flow_section_by_default(self):
        s = self._sample()
        s["setting"].update({"ide_session": "ide-1", "rp_state": False, "image_mode": "off",
                             "total_turns": 3})
        idx = 5   # u3（最後のユーザー発言）
        known = s["messages"][:idx]
        orig = server.read_sent
        server.read_sent = lambda sid: {"ide": "ide-1", "sigs": {}, "labels": {},
                                        "msgs": [server.msg_sig(x) for x in known]}
        old_flow = os.environ.get("RPCHAT_FLOW")
        os.environ["RPCHAT_FLOW"] = "0"
        try:
            ctx = server.build_context(s, s["messages"][idx])
        finally:
            server.read_sent = orig
            if old_flow is None:
                os.environ.pop("RPCHAT_FLOW", None)
            else:
                os.environ["RPCHAT_FLOW"] = old_flow
        self.assertEqual("diff", ctx["mode"])
        flow = [x for x in ctx["sections"] if x["label"].startswith("直近の流れ")]
        self.assertEqual(0, len(flow))

    def test_context_full_has_no_flow_section(self):
        s = self._sample()
        s["setting"].update({"ide_session": "", "rp_state": False, "image_mode": "off"})
        ctx = server.build_context(s, s["messages"][5])
        self.assertNotEqual("diff", ctx["mode"])
        self.assertFalse([x for x in ctx["sections"] if x["label"].startswith("直近の流れ")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
