# -*- coding: utf-8 -*-
"""本番に近い結合テスト（実 HTTP サーバー + 実ファイル + 実 outbox 経路）。

本番との違いは「ブラウザ」と「IDE エージェント」と「LLM」だけ:
  - ブラウザの代わりに HTTP（本物のハンドラ／本物の静的配信を通す）
  - IDE エージェントの代わりに outbox へ返信 JSON を置く（本番と同じ合図・同じ取り込み経路）
  - LLM（google/vertex）は使わない（既定の ai_provider=antigravity は外のエージェントが答える）
データはすべてテンポラリに隔離されるので、本物の data/ ・session/ ・outbox/ は触らない。

遅いテストを足さないための方針:
  - 常時実行するテストは 1 プロセス内で完結させる（サーバー起動の 2.6 秒を払わない）
  - 本物の `python server.py` を起動する試験だけ RPCHAT_SLOW_TESTS=1 で実行する
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
from test.support import IsolatedTestCase

# serve_forever の待ち時間（既定 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒待つ）
_POLL = 0.02


def _tail(path, limit=2000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return "（ログを読めません）"
    return text[-limit:]


class ProductionLoopTest(IsolatedTestCase):
    """ブラウザ相当の HTTP クライアントとエージェント相当の outbox 書き込みで 1 ターンを回す"""

    def setUp(self):
        super().setUp()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.http_thread = threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.http_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.http_thread.join(timeout=2)
        super().tearDown()

    # ---------------------------------------------------------------- HTTP
    def http_get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return r.status, dict(r.headers), r.read()

    def get_json(self, path):
        _, _, body = self.http_get(path)
        return json.loads(body.decode("utf-8"))

    def post_json(self, path, data):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    # ------------------------------------------------- エージェント側の書き込み
    def drop_agent_reply(self, sid, mid, **fields):
        """本番のエージェントと同じ場所・同じ名前で返信 JSON を置く"""
        body = {"session": sid, "reply_to": mid}
        body.update(fields)
        path = os.path.join(server.OUTBOX, f"{sid}_reply_{mid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        return path

    def new_channel(self, title="本番相当チャンネル"):
        status, res = self.post_json("/api/session/new", {"title": title})
        self.assertEqual(status, 200, res)
        self.assertTrue(res.get("ok"), res)
        return res["sid"]

    # ---------------------------------------------------------------- テスト
    def test_01_page_and_static_assets_are_served(self):
        status, headers, body = self.http_get("/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("app.js", html)          # 画面が読み込む本物の JS

        status, _, js = self.http_get("/app.js")
        self.assertEqual(status, 200)
        self.assertGreater(len(js), 10000)     # 空や 404 ページではないこと

        # 存在しないパスは 404、public/ の外は 403（パストラバーサル防止が本番と同じ）
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.http_get("/no_such_file_here.js")
        self.assertEqual(ctx.exception.code, 404)

    def test_02_one_turn_browser_to_agent_and_back(self):
        sid = self.new_channel()

        # 1. ブラウザ相当: 発言を送る
        status, res = self.post_json("/api/send", {"sid": sid, "text": "灯台守の少女に話しかける"})
        self.assertEqual(status, 200, res)
        self.assertTrue(res.get("ok"), res)
        mid = res["id"]

        s = self.get_json(f"/api/session?sid={sid}")
        self.assertEqual(s["messages"][-1]["id"], mid)
        self.assertEqual(s["messages"][-1]["role"], "user")
        self.assertEqual(s["messages"][-1]["status"], "pending")

        # 2. エージェント相当: /api/pending で本文を受け取る（wait_input.py と同じ経路）
        pending = self.get_json("/api/pending?check=1")
        self.assertEqual(pending.get("session"), sid, pending)
        got = pending.get("pending") or {}
        self.assertEqual(got.get("id"), mid, f"未応答の発言が /api/pending に出ていない: {pending}")
        self.assertEqual(got.get("text"), "灯台守の少女に話しかける")
        self.assertEqual(pending.get("task_type"), "user")

        status, claimed = self.post_json("/api/claim", {"sid": sid, "id": mid, "type": "user"})
        self.assertEqual(status, 200, claimed)
        self.assertTrue(claimed.get("ok"), claimed)
        self.assertEqual(server.load(sid)["messages"][-1]["status"], "processing")

        # 3. エージェント相当: outbox に返信を書く → 本番の取り込み経路で反映される
        self.drop_agent_reply(sid, mid, text="少女は灯台の明かりを指さして微笑んだ。", memories=[])
        self.assertTrue(server.process_outbox())

        s = self.get_json(f"/api/session?sid={sid}")
        self.assertEqual(len(s["messages"]), 2, s["messages"])
        self.assertEqual(s["messages"][0]["status"], "answered")
        self.assertEqual(s["messages"][1]["role"], "assistant")
        self.assertIn("灯台の明かり", s["messages"][1]["text"])
        self.assertEqual(s["setting"].get("total_turns"), 1)
        self.assertEqual(s.get("rev", 0) > 0, True)

        # 4. ディスク上にも残っている（再起動しても読める = 永続化の確認）
        with open(server.spath(sid), encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(len(on_disk["messages"]), 2)
        self.assertEqual(on_disk["messages"][1]["role"], "assistant")

        # 5. ブラウザ相当: 一覧にタイトルつきで出る
        listed = self.get_json("/api/sessions")["sessions"]
        self.assertIn(sid, [x["id"] for x in listed])
        self.assertEqual(next(x for x in listed if x["id"] == sid)["title"], "本番相当チャンネル")

    def test_03_export_import_roundtrip_preserves_conversation(self):
        sid = self.new_channel("書き出し元")
        _, res = self.post_json("/api/send", {"sid": sid, "text": "書き出しテストの発言"})
        mid = res["id"]
        self.drop_agent_reply(sid, mid, text="書き出しテストの返答", memories=[])
        server.process_outbox()

        status, headers, body = self.http_get(f"/api/session/export?sid={sid}")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        exported = json.loads(body.decode("utf-8"))
        self.assertEqual(len(exported["messages"]), 2)

        status, res = self.post_json("/api/session/import", {"data": exported})
        self.assertEqual(status, 200, res)
        new_sid = res["sid"]
        self.assertNotEqual(new_sid, sid)

        imported = self.get_json(f"/api/session?sid={new_sid}")
        self.assertEqual([m["text"] for m in imported["messages"]],
                         ["書き出しテストの発言", "書き出しテストの返答"])

    def test_04_delete_channel_moves_to_trash(self):
        sid = self.new_channel("消すチャンネル")
        status, res = self.post_json("/api/session/delete", {"sid": sid})
        self.assertEqual(status, 200, res)
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(os.path.exists(server.spath(sid)))
        self.assertTrue(os.path.isdir(server.TRASH))
        self.assertTrue(any(sid in name for name in os.listdir(server.TRASH)),
                        f"ゴミ箱に退避されていない: {os.listdir(server.TRASH)}")


class ProductionBootTest(unittest.TestCase):
    """本物の `python server.py` を空のデータディレクトリで起動する（低速テスト）。"""

    def test_01_boot_with_empty_data_dir(self):
        if os.environ.get("RPCHAT_SLOW_TESTS") != "1":
            self.skipTest("低速テスト（本物のサーバー起動に約 2.6 秒）。RPCHAT_SLOW_TESTS=1 で実行")

        tmp = tempfile.mkdtemp(prefix="rp_e2e_boot_")
        data = os.path.join(tmp, "data")
        ide = os.path.join(tmp, "session")
        outbox = os.path.join(tmp, "outbox")
        os.makedirs(data, exist_ok=True)
        os.makedirs(outbox, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "RPCHAT_DATA": data,
            "RPCHAT_PORT": "0",           # 空きポートは下で決め直す
            "RPCHAT_NEW_IDE_DIR": ide,
            "PYTHONIOENCODING": "utf-8",
        })
        # 空きポートを先に確保する
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env["RPCHAT_PORT"] = str(port)

        proc = subprocess.Popen([sys.executable, os.path.join(ROOT_DIR, "server.py")],
                                cwd=ROOT_DIR, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 30
            ok = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                        ok = r.status == 200
                    if ok:
                        break
                except Exception:
                    time.sleep(0.1)
            self.assertTrue(ok, "本物のサーバーが起動しない")

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sessions", timeout=10) as r:
                listed = json.loads(r.read().decode("utf-8"))["sessions"]
            self.assertEqual(len(listed), 1, f"初回起動で既定チャンネルが 1 つ作られていない: {listed}")

            # 空のデータディレクトリから本番と同じ場所にファイルが作られている
            self.assertTrue(os.path.isdir(os.path.join(data, "sessions")))
            self.assertTrue(os.path.isdir(os.path.join(data, "rag")))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
