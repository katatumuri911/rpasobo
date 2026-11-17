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

    def test_friendly_error_capacity(self):
        label, detail, kind = ide_poke.friendly_error(
            "API error (attempt 7): UNAVAILABLE (code 503): "
            "No capacity available for model gemini-3.8-flash-medium on the server")
        self.assertEqual(label, "503エラー：モデルが混雑（容量不足）")
        self.assertEqual(kind, "capacity")
        self.assertIn("サーバーが混雑", detail)
        self.assertIn("7/9回目", detail)
        self.assertIn("待機するか、別のモデルに切り替えて下さい。", detail)
        self.assertIn("別のプロバイダに切り替えてください。", detail)
        self.assertNotIn("No capacity", detail)

        label, detail, kind = ide_poke.friendly_error("Model unavailable, retrying in 167s (attempt 7/9)")
        self.assertEqual(label, "モデルが一時的に利用不可")
        self.assertEqual(kind, "capacity")
        self.assertIn("167秒後", detail)
        self.assertIn("7/9回目", detail)
        self.assertIn("待機するか、別のモデルに切り替えて下さい。", detail)
        self.assertIn("別のプロバイダに切り替えてください。", detail)
        self.assertNotIn("Model unavailable", detail)

        label, detail, kind = ide_poke.friendly_error("HTTP 400: bad request")
        self.assertEqual(label, "400エラー：送信内容がAIに受け付けられませんでした")
        self.assertEqual(kind, "")
        self.assertIn("プロンプトが長すぎる", detail)
        self.assertIn("別のモデルに切り替えてください。", detail)
        self.assertNotIn("bad request", detail)

        label, detail, kind = ide_poke.friendly_error(
            "API error (code 401): UNAUTHENTICATED: Request had invalid authentication credentials")
        self.assertEqual(label, "401エラー：認証が切れています")
        self.assertIn("ログインし直す", detail)
        self.assertIn("別のプロバイダに切り替えてください。", detail)
        self.assertNotIn("UNAUTHENTICATED", detail)

        label, detail, kind = ide_poke.friendly_error(
            "API error (code 429): RESOURCE_EXHAUSTED: Quota exceeded for quota metric")
        self.assertEqual(label, "429エラー：利用上限に達しました")
        self.assertIn("1〜2分待って", detail)
        self.assertIn("別のモデル", detail)
        self.assertIn("別のプロバイダ", detail)
        self.assertNotIn("RESOURCE_EXHAUSTED", detail)

        label, detail, kind = ide_poke.friendly_error(
            "API error (code 500): INTERNAL: Internal error encountered")
        self.assertEqual(label, "500エラー：AIサーバーで問題が起きました")
        self.assertIn("少し待って", detail)
        self.assertIn("別のモデル", detail)
        self.assertIn("別のプロバイダ", detail)
        self.assertNotIn("INTERNAL", detail)

        label, detail, kind = ide_poke.friendly_error(
            "Something went wrong while contacting the model")
        self.assertEqual(label, "通信エラーが起きました")
        self.assertEqual(kind, "unknown")
        self.assertIn("少し待って", detail)
        self.assertIn("別のモデル", detail)
        self.assertIn("別のプロバイダ", detail)
        self.assertNotIn("Something went wrong", detail)

    def test_progress_capacity_errors_are_merged(self):
        """同じ容量不足の再試行は1行にまとめ、生の英語を出さない。"""
        steps = [
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
        ]
        for n in (1, 2, 3):
            steps.append({
                "type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
                "errorMessage": {"error": {
                    "shortError": f"API error (attempt {n}): UNAVAILABLE (code 503): "
                                  "No capacity available for model gemini-3.8-flash-medium on the server"}},
                "status": "CORTEX_STEP_STATUS_DONE",
            })
        with patch.object(ide_poke, "locate",
                          return_value=(1, "tok", {"stepCount": len(steps),
                                                   "lastUserInputStepIndex": 0,
                                                   "status": "CASCADE_RUN_STATUS_RUNNING"})),              patch.object(ide_poke, "_call", return_value=(200, {"steps": steps})):
            res, err = ide_poke.progress("cid")
        self.assertIsNone(err)
        items = res["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "error")
        self.assertEqual(items[0]["error_kind"], "capacity")
        self.assertEqual(items[0]["attempt"], 3)
        self.assertEqual(items[0]["filter_name"], "503エラー：モデルが混雑（容量不足）")
        self.assertIn("3/9回目", items[0]["detail"])
        self.assertIn("待機するか、別のモデルに切り替えて下さい。", items[0]["detail"])
        self.assertIn("別のプロバイダに切り替えてください。", items[0]["detail"])
        self.assertNotIn("No capacity", items[0]["detail"])
        self.assertIn("No capacity", items[0]["raw_detail"])

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

    def test_progress_returns_previous_turn_history(self):
        """ページを開いた直後でも3件窓を埋められるよう、直前ターンの最後の2件を返す。"""
        steps = [
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"toolCalls": [_tc("write_to_file",
                                                   AbsolutePath=r"C:\app\outbox\a_reply_1.json")]},
             "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"response": "前の返事"}, "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
            {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
             "plannerResponse": {"toolCalls": [_tc("run_command",
                                                   CommandLine="python wait_input.py --ide x --wait 8")]},
             "status": "CORTEX_STEP_STATUS_DONE"},
        ]
        with patch.object(ide_poke, "locate",
                          return_value=(1, "tok", {"stepCount": len(steps),
                                                   "lastUserInputStepIndex": 3,
                                                   "status": "CASCADE_RUN_STATUS_RUNNING"})),              patch.object(ide_poke, "_call", return_value=(200, {"steps": steps})):
            res, err = ide_poke.progress("cid")
        self.assertIsNone(err)
        self.assertEqual([i["kind"] for i in res["items"]], ["tool"])
        self.assertTrue(res["items"][0]["internal"])
        self.assertEqual([i["kind"] for i in res["history"]], ["tool", "reply"])
        self.assertEqual(res["history"][0]["tool_kind"], "write")
        self.assertTrue(all(i["done"] for i in res["history"]))

    def test_node_progress_labels_js(self):
        script = os.path.join(ROOT_DIR, "test", "test_progress_labels.cjs")
        cp = subprocess.run(["node", script], capture_output=True, text=True, cwd=ROOT_DIR)
        self.assertEqual(cp.returncode, 0, f"Node test failed:\n{cp.stdout}\n{cp.stderr}")


if __name__ == "__main__":
    unittest.main()
