# -*- coding: utf-8 -*-
"""test/test_comfy_run.py: ComfyUI 連携のソケット管理・バックオフ・エラー処理テスト (F-05)"""
import json
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

import server
import ide_poke
from test.support import IsolatedTestCase


class TestComfyRunRobustness(IsolatedTestCase):
    """comfy_run() の例外保護、ソケット解放、およびバックオフの契約テスト（ファイル I/O 非依存）"""

    def setUp(self):
        super().setUp()
        self.orig_min = server.COMFY_POLL_MIN
        self.orig_max = server.COMFY_POLL_MAX
        # テスト実行を高速化するためポーリング間隔を極小値に設定
        server.COMFY_POLL_MIN = 0.001
        server.COMFY_POLL_MAX = 0.005

    def tearDown(self):
        server.COMFY_POLL_MIN = self.orig_min
        server.COMFY_POLL_MAX = self.orig_max
        super().tearDown()

    def test_prompt_http_error_closed_and_raises(self):
        """Case 1: /prompt 投入時に 400 エラーが発生した場合、ソケットが close され RuntimeError となること"""
        closed = []

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://127.0.0.1:8188/prompt", 400, "Bad Request", {}, None)

            def read(self):
                return b'{"error": "node not found"}'

            def close(self):
                closed.append(True)

        with patch("urllib.request.urlopen", side_effect=FakeHTTPError()):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({"wf": 1}, timeout=5)
            self.assertIn("400", str(cm.exception))
            self.assertIn("node not found", str(cm.exception))
        self.assertEqual(len(closed), 1, "HTTPError must be explicitly closed")

    def test_prompt_network_error_raises(self):
        """Case 1b: /prompt 投入時に URLError（接続拒否等）が発生した場合、RuntimeError となること"""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({"wf": 1}, timeout=5)
            self.assertIn("ComfyUI への接続に失敗しました", str(cm.exception))

    def test_happy_path_success(self):
        """Case 2: 正常完了時、画像ファイル名が正しく返ること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_100"})
        empty_r = make_resp({})
        done_r = make_resp({
            "pid_100": {
                "outputs": {"1": {"images": [{"filename": "result.png", "subfolder": "ch1"}]}}
            }
        })
        calls = [prompt_r, empty_r, done_r]

        def fake_urlopen(req, timeout=None):
            if not calls:
                raise AssertionError("Unexpected urlopen call beyond planned sequence")
            return calls.pop(0)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = server.comfy_run({"wf": 1}, timeout=5)
        self.assertEqual(res, "ch1/result.png")

    def test_polling_http_error_backoff_and_recovery(self):
        """Case 3: ポーリング中に HTTP 500 が発生しても close されて指数バックオフし、その後復帰すること"""
        closed = []

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://127.0.0.1:8188/history/pid_101", 500, "Server Error", {}, None)

            def close(self):
                closed.append(True)

        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_101"})
        done_r = make_resp({
            "pid_101": {
                "outputs": {"1": {"images": [{"filename": "out.png", "subfolder": ""}]}}
            }
        })

        # 1回目: prompt, 2回目: 500エラー, 3回目: 500エラー, 4回目: 未完了空辞書, 5回目: 完了
        sequence = [prompt_r, FakeHTTPError(), FakeHTTPError(), make_resp({}), done_r]

        delays = []
        real_sleep = time.sleep

        def spy_sleep(d):
            delays.append(d)
            real_sleep(d)

        def fake_urlopen(req, timeout=None):
            if not sequence:
                raise AssertionError("Call sequence exhausted")
            item = sequence.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep", side_effect=spy_sleep):
            res = server.comfy_run({}, timeout=5)

        self.assertEqual(res, "out.png")
        self.assertEqual(len(closed), 2, "Polling HTTPErrors must be closed")
        # 期待する遅延推移: 初期 0.001 -> 1回目エラー後 0.002 -> 2回目エラー後 0.004 -> 空辞書受信で 0.001 にリセット
        self.assertEqual(delays, [0.001, 0.002, 0.004, 0.001])

    def test_execution_error(self):
        """Case 4: ComfyUI から execution_error が返された際に例外が上がること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        # 4a: execution_error メッセージがある場合
        prompt_r1 = make_resp({"prompt_id": "pid_err1"})
        err_r1 = make_resp({
            "pid_err1": {
                "status": {
                    "status_str": "error",
                    "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]
                }
            }
        })
        with patch("urllib.request.urlopen", side_effect=[prompt_r1, err_r1]):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertEqual(str(cm.exception), "CUDA out of memory")

        # 4b: status_str が error だが execution_error がない場合
        prompt_r2 = make_resp({"prompt_id": "pid_err2"})
        err_r2 = make_resp({
            "pid_err2": {
                "status": {"status_str": "error", "messages": []}
            }
        })
        with patch("urllib.request.urlopen", side_effect=[prompt_r2, err_r2]):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertEqual(str(cm.exception), "ComfyUI error")

        # 4c: completed=True だが出力画像が存在しない場合
        prompt_r3 = make_resp({"prompt_id": "pid_err3"})
        err_r3 = make_resp({
            "pid_err3": {
                "status": {"completed": True},
                "outputs": {}
            }
        })
        with patch("urllib.request.urlopen", side_effect=[prompt_r3, err_r3]):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertEqual(str(cm.exception), "no image output")

    def test_mock_exhaustion_asserts(self):
        """Case 5: モック呼出回数の超過時に AssertionError がスローされ、CPU スピンしないこと"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_ex"})
        # 計画シーケンスは prompt のみ。ポーリング 1 回目で枯渇して AssertionError
        calls = [prompt_r]

        def fake_urlopen(req, timeout=None):
            if not calls:
                raise AssertionError("Planned sequence exhausted")
            return calls.pop(0)

        t0 = time.time()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(AssertionError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertIn("Planned sequence exhausted", str(cm.exception))
        # 5秒タイムアウトまでスピンせず、即座（0.5秒未満）に停止すること
        self.assertLess(time.time() - t0, 0.5)

    def test_polling_network_error_backoff(self):
        """ポーリング中に URLError や OSError が発生してもバックオフし、上限 COMFY_POLL_MAX でキャップされること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_net"})
        done_r = make_resp({
            "pid_net": {
                "outputs": {"1": {"images": [{"filename": "done.png"}]}}
            }
        })

        # 3 回連続で URLError / OSError
        sequence = [
            prompt_r,
            urllib.error.URLError("Connection reset"),
            OSError("WSAENOBUFS"),
            ValueError("Malformed JSON"),
            done_r
        ]

        delays = []
        real_sleep = time.sleep

        def spy_sleep(d):
            delays.append(d)
            real_sleep(d)

        def fake_urlopen(req, timeout=None):
            if not sequence:
                raise AssertionError("Call sequence exhausted")
            item = sequence.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep", side_effect=spy_sleep):
            res = server.comfy_run({}, timeout=5)

        self.assertEqual(res, "done.png")
        # COMFY_POLL_MIN=0.001, MAX=0.005
        # 0.001 -> 0.002 -> 0.004 -> min(0.008, 0.005)=0.005
        self.assertEqual(delays, [0.001, 0.002, 0.004, 0.005])

    def test_already_running_with_context(self):
        """already_running() が context manager を使用してソケットを解放し、稼働中のポートを返すこと"""
        resp_mock = MagicMock()
        resp_mock.read.return_value = b'{"ok": true}'
        resp_mock.__enter__.return_value = resp_mock
        resp_mock.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=resp_mock) as mock_open:
            port = server.already_running()
            self.assertEqual(port, server.PORT)
            self.assertTrue(resp_mock.__enter__.called)
            self.assertTrue(resp_mock.__exit__.called)

    def test_ide_poke_call_with_context_and_error_handling(self):
        """ide_poke._call() が context manager を使用し、HTTPError 時に with e で解放すること"""
        # 正常系
        resp_mock = MagicMock()
        resp_mock.read.return_value = b'{"status": "ok"}'
        resp_mock.__enter__.return_value = resp_mock
        resp_mock.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=resp_mock):
            status, body = ide_poke._call(1234, "tok", "/test", {})
            self.assertEqual(status, 200)
            self.assertEqual(body, {"status": "ok"})
            self.assertTrue(resp_mock.__enter__.called)
            self.assertTrue(resp_mock.__exit__.called)

        # 異常系 (HTTPError)
        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://127.0.0.1:1234", 404, "Not Found", {}, None)
                self.entered = False
                self.exited = False

            def read(self):
                return b'Not Found Body'

            def __enter__(self):
                self.entered = True
                return self

            def __exit__(self, *args):
                self.exited = True
                self.close()

        err = FakeHTTPError()
        with patch("urllib.request.urlopen", side_effect=err):
            status, msg = ide_poke._call(1234, "tok", "/test", {})
            self.assertEqual(status, 404)
            self.assertIn("Not Found Body", msg)
            self.assertTrue(err.entered)
            self.assertTrue(err.exited)

    def test_timeout_raises(self):
        """タイムアウト超過時に RuntimeError('timeout') がスローされること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_to"})
        # 常に空辞書（未完了）を返す
        def fake_urlopen(req, timeout=None):
            return prompt_r if hasattr(req, "data") and req.data else make_resp({})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=0.01)
            self.assertEqual(str(cm.exception), "timeout")

    def test_throttled_logging_at_10_errors(self):
        """連続10回エラー時にログが出力され、間引きされること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_throttle"})
        done_r = make_resp({
            "pid_throttle": {
                "outputs": {"1": {"images": [{"filename": "done_after_11.png"}]}}
            }
        })

        class DummyError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://127.0.0.1:8188", 503, "Unavailable", {}, None)
            def close(self):
                pass

        sequence = [prompt_r] + [DummyError() for _ in range(11)] + [done_r]

        def fake_urlopen(req, timeout=None):
            if not sequence:
                raise AssertionError("Sequence exhausted")
            item = sequence.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("builtins.print") as mock_print:
            res = server.comfy_run({}, timeout=5)

        self.assertEqual(res, "done_after_11.png")
        # 11回エラー中、10回目にのみ print される
        comfy_logs = [call.args[0] for call in mock_print.call_args_list if "[comfy]" in str(call.args[0])]
        self.assertEqual(len(comfy_logs), 1)
        self.assertIn("x10", comfy_logs[0])
        self.assertIn("503", comfy_logs[0])

    def test_polling_malformed_json_types_resilience(self):
        """Case 11: ポーリング中に ComfyUI から null, 配列, None値等の異常JSONが返ってもクラッシュせず復帰すること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_resilient"})
        null_r = make_resp(None)                 # 辞書ではなく None (TypeError 防止)
        list_r = make_resp(["unexpected_list"]) # 辞書ではなく リスト
        entry_null_r = make_resp({"pid_resilient": None}) # entry が None
        outputs_null_r = make_resp({"pid_resilient": {"outputs": None}}) # outputs が None
        images_null_r = make_resp({"pid_resilient": {"outputs": {"1": {"images": None}}}}) # images が None
        done_r = make_resp({
            "pid_resilient": {
                "outputs": {"1": {"images": [{"filename": "recovered.png", "subfolder": None}]}}
            }
        })

        sequence = [
            prompt_r,
            null_r,
            list_r,
            entry_null_r,
            outputs_null_r,
            images_null_r,
            done_r
        ]

        def fake_urlopen(req, timeout=None):
            if not sequence:
                raise AssertionError("Sequence exhausted")
            return sequence.pop(0)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = server.comfy_run({}, timeout=5)

        self.assertEqual(res, "recovered.png")

    def test_prompt_missing_or_invalid_prompt_id(self):
        """Case 12: /prompt 応答に prompt_id が欠落している場合、RuntimeError となること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        # 12a: prompt_id キーが存在しない
        with patch("urllib.request.urlopen", return_value=make_resp({"error": "node not found"})):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertIn("ComfyUI への接続に失敗しました", str(cm.exception))

        # 12b: prompt_id が None
        with patch("urllib.request.urlopen", return_value=make_resp({"prompt_id": None})):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertIn("ComfyUI への接続に失敗しました", str(cm.exception))

    def test_execution_error_with_none_message(self):
        """Case 13: execution_error の exception_message が None の場合でも AttributeError にならず ComfyUI error となること"""
        def make_resp(data):
            r = MagicMock()
            r.read.return_value = json.dumps(data).encode("utf-8")
            r.__enter__.return_value = r
            r.__exit__.return_value = None
            return r

        prompt_r = make_resp({"prompt_id": "pid_null_msg"})
        err_r = make_resp({
            "pid_null_msg": {
                "status": {
                    "status_str": "error",
                    "messages": [["execution_error", {"exception_message": None}]]
                }
            }
        })

        with patch("urllib.request.urlopen", side_effect=[prompt_r, err_r]):
            with self.assertRaises(RuntimeError) as cm:
                server.comfy_run({}, timeout=5)
            self.assertEqual(str(cm.exception), "ComfyUI error")


if __name__ == "__main__":
    unittest.main()
