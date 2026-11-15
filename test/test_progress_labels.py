# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import ide_poke
import server


def _tc(name, **args):
    return {"name": name, "argumentsJson": json.dumps(args)}


class TestProgressLabels(unittest.TestCase):
    def test_tool_kind_of(self):
        cases = [
            ("run_command", "command"),
            ("view_file", "read"),
            ("read_file", "read"),
            ("read_text_file", "read"),
            ("write_to_file", "write"),
            ("write_file", "write"),
            ("replace_file_content", "write"),
            ("grep_search", "search"),
            ("list_dir", "list"),
            ("update_plan", "plan"),
            ("mystery_tool", "tool"),
            ("", "tool"),
            (None, "tool"),
        ]
        for name, want in cases:
            with self.subTest(name=name):
                self.assertEqual(ide_poke.tool_kind_of(name), want)

    def test_filter_label_and_server_filter_name(self):
        cases = [
            ("content safety filters", "コンテンツ安全フィルタ"),
            ("STOP_REASON_CONTENT_FILTER", "コンテンツフィルタ（生成停止）"),
            ("Prohibited Use: ...", "禁止コンテンツ（解除不可）"),
            ("PROHIBITED_CONTENT", "禁止コンテンツ（解除不可）"),
            ("IMAGE_SAFETY", "画像の安全フィルタ"),
            ("model output error", "モデル出力エラー"),
            ("429 Resource has been exhausted (check quota)", ""),
            ("HTTP 500", ""),
            ("", ""),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(ide_poke.filter_label(text), want)
                self.assertEqual(server.filter_name_of(text), want)

    def test_tool_line_marks_wait_command_internal(self):
        it = ide_poke._tool_line(_tc("run_command",
                                     CommandLine='python "C:\\x\\wait_input.py" --ide abc --wait 8'))
        self.assertTrue(it["internal"])
        self.assertEqual(it["tool_kind"], "command")
        self.assertEqual(it["detail"], "")

    def test_tool_line_hides_outbox_path(self):
        for tool_name, key in (("write_to_file", "AbsolutePath"), ("write_file", "Path")):
            it = ide_poke._tool_line(_tc(tool_name, **{key: "C:\\app\\outbox\\abc_reply_1.json"}))
            self.assertEqual(it["tool_kind"], "write")
            self.assertFalse(it["internal"])
            self.assertEqual(it["detail"], "")

    def test_tool_line_keeps_read_filename(self):
        for tool_name, key in (("view_file", "AbsolutePath"), ("read_file", "Path")):
            it = ide_poke._tool_line(_tc(tool_name, **{key: "C:\\app\\data\\ctx\\abc.md"}))
            self.assertEqual(it["tool_kind"], "read")
            self.assertEqual(it["detail"], "abc.md")

    def test_progress_contract(self):
        """kind の値は既存のまま。internal / step / filter_name が付く。"""
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"toolCalls": [_tc("run_command",
                                                   CommandLine="python wait_input.py --ide x --wait 8")]},
             "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
             "errorMessage": {"error": {"shortError": "content safety filters blocked the request"}},
             "status": "CORTEX_STEP_STATUS_DONE"},
        ]
        with patch.object(ide_poke, "locate",
                          return_value=(1, "tok", {"stepCount": len(steps),
                                                   "lastUserInputStepIndex": 0,
                                                   "status": "CASCADE_RUN_STATUS_IDLE"})), \
             patch.object(ide_poke, "_call", return_value=(200, {"steps": steps})):
            res, err = ide_poke.progress("cid")
        self.assertIsNone(err)
        items = res["items"]
        self.assertEqual([i["kind"] for i in items], ["tool", "error"])
        self.assertTrue(items[0]["internal"])
        self.assertEqual(items[-1]["filter_name"], "コンテンツ安全フィルタ")
        self.assertTrue(all("step" in i and "call" in i for i in items))

    def test_progress_stop_reason_content_filter(self):
        """PLANNER_RESPONSE の stopReason == STOP_REASON_CONTENT_FILTER も error 行として filter_name が付く。"""
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"stopReason": "STOP_REASON_CONTENT_FILTER"},
             "status": "CORTEX_STEP_STATUS_DONE"},
        ]
        with patch.object(ide_poke, "locate",
                          return_value=(1, "tok", {"stepCount": len(steps),
                                                   "lastUserInputStepIndex": 0,
                                                   "status": "CASCADE_RUN_STATUS_IDLE"})), \
             patch.object(ide_poke, "_call", return_value=(200, {"steps": steps})):
            res, err = ide_poke.progress("cid")
        self.assertIsNone(err)
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["kind"], "error")
        self.assertEqual(res["items"][0]["filter_name"], "コンテンツフィルタ（生成停止）")

    def test_node_progress_labels_js(self):
        script = os.path.join(ROOT_DIR, "test", "test_progress_labels.cjs")
        cp = subprocess.run(["node", script], capture_output=True, text=True, cwd=ROOT_DIR)
        self.assertEqual(cp.returncode, 0, f"Node test failed:\n{cp.stdout}\n{cp.stderr}")


if __name__ == "__main__":
    unittest.main()
