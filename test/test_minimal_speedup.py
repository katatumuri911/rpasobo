# -*- coding: utf-8 -*-
"""最小高速化プラン（style rule / core_anchor auto / read_file 計測）の回帰テスト"""
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
import ide_poke


def make_msg(role, text, mid):
    return {"id": mid, "role": role, "text": text, "ts": server.now()}


class TestMinimalSpeedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_min_speed_")
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "RAG_DIR", "SENT", "CTX", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)
        os.environ["RPCHAT_CORE_ANCHOR"] = "auto"

    def tearDown(self):
        for name, val in self.orig_dirs.items():
            if val is not None:
                setattr(server, name, val)
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("RPCHAT_CORE_ANCHOR", None)
        os.environ.pop("RPCHAT_PERF_LOG", None)

    def _session(self):
        return server.create_session({
            "title": "テスト",
            "ide_session": "ide-1",
            "summary": "夏の海辺の町の物語。",
            "instructions": "指示文",
            "character": {"name": "アリス", "speech": "一人称は私。ツンデレ。"},
        })

    def test_style_rule_moved_to_writing_style_file(self):
        """本文の書き方は writing_style.md に集約。末尾の rules は口調・世界観アンカーだけ"""
        s = self._session()
        m = make_msg("user", "こんにちは", "u1")
        s["messages"].append(m)
        server.save(s)
        # full は全設定を読むのでアンカーも省略
        self.assertIsNone(server.build_context(s, m)["rules"])

        a1 = make_msg("assistant", "返答", "a1")
        m2 = make_msg("user", "続き", "u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)
        text = server.build_context(s, m2)["rules"]["text"]
        self.assertIn("口調:", text)
        self.assertNotIn("セリフ", text)

        with open(server.WRITING_STYLE, encoding="utf-8") as f:
            style = f.read()
        self.assertIn("2〜3個", style)
        self.assertIn("前の応答がセリフ1個だけ", style)
        self.assertNotIn("2〜4個", style)

    def test_writing_style_remind_every_three_turns(self):
        """執筆スタイルは差分モードで3ターンに1回だけ再送される"""
        s = self._session()
        m = make_msg("user", "u1", "u1")
        s["messages"].append(m)
        server.save(s)
        server.build_context(s, m)  # full（ここで全部読む）
        shown = []
        for i in range(2, 8):
            a = make_msg("assistant", f"a{i - 1}", f"a{i - 1}")
            u = make_msg("user", f"u{i}", f"u{i}")
            s["messages"].extend([a, u])
            server.save(s)
            server.note_reply(s, f"u{i - 1}", a)
            ctx = server.build_context(s, u)
            self.assertEqual(ctx["mode"], "diff")
            shown.append(any("執筆スタイル" in sec["label"] for sec in ctx["sections"]))
        self.assertEqual(shown, [False, False, True, False, False, True])

    def test_core_anchor_auto_full_skip_diff_keep(self):
        s = self._session()
        m1 = make_msg("user", "こんにちは", "u1")
        s["messages"].append(m1)
        server.save(s)
        full_rules = server.build_context(s, m1)["rules"]
        self.assertIsNone(full_rules)  # full は全設定を読むのでアンカー不要

        a1 = make_msg("assistant", "返答", "a1")
        m2 = make_msg("user", "続き", "u2")
        s["messages"].extend([a1, m2])
        server.save(s)
        server.note_reply(s, "u1", a1)
        diff_text = server.build_context(s, m2)["rules"]["text"]
        self.assertIn("口調:", diff_text)

    def test_tool_usage_since_detects_read_file_but_not_open_url(self):
        steps = {"steps": [
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"toolCalls": [{"name": "view_file"}]}},
            {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"toolCalls": [{"name": "write_file"}]}},
        ]}
        with patch.object(ide_poke, "locate", return_value=(123, "tok", {"stepCount": 3, "lastUserInputStepIndex": 0})), \
             patch.object(ide_poke, "_call", return_value=(200, steps)):
            usage = ide_poke.tool_usage_since("cid")
        self.assertTrue(usage["ok"])
        self.assertTrue(usage["read_file"])
        self.assertIn("view_file", usage["names"])

        only_open = {"steps": [
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"toolCalls": [{"name": "open_url"}]}},
        ]}
        with patch.object(ide_poke, "locate", return_value=(123, "tok", {"stepCount": 1, "lastUserInputStepIndex": 0})), \
             patch.object(ide_poke, "_call", return_value=(200, only_open)):
            usage2 = ide_poke.tool_usage_since("cid")
        self.assertTrue(usage2["ok"])
        self.assertFalse(usage2["read_file"])

    def test_log_main_turn_records_latency_and_read_file(self):
        os.environ["RPCHAT_PERF_LOG"] = "1"
        s = self._session()
        m = make_msg("user", "こんにちは", "u1")
        s["messages"].append(m)
        server.save(s)
        outbox = os.path.join(server.OUTBOX, "dummy.json")
        with open(outbox, "w", encoding="utf-8") as f:
            f.write("{}")
        m["claimed_ts"] = server.now()
        with patch.object(server.ide_poke, "tool_usage_since",
                          return_value={"ok": True, "read_file": True, "total": 2,
                                        "names": ["view_file", "write_file"]}):
            server._log_main_turn(s["id"], m, outbox, "ide-1")
        with open(os.path.join(server.DATA, "perf.log"), encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        turn = [r for r in rows if r.get("event") == "turn"][-1]
        self.assertTrue(turn["read_file"])
        self.assertTrue(turn["read_file_checked"])
        self.assertIsNotNone(turn["latency_ms"])
        self.assertIn("view_file", turn["tool_names"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
