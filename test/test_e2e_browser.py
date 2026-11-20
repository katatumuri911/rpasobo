# -*- coding: utf-8 -*-
"""実ブラウザ（headless Chrome/Edge）で本物の画面を操作する E2E。

`test_e2e_production.py` が HTTP レベルの結合を見るのに対し、こちらは
**本物のブラウザで本物の index.html / app.js を動かし、ボタンを実際にクリックする**。
確認できること:
  - JS が実行時にエラーを出さないこと（pageerror / console error を監視）
  - ボタン押下 → 画面が変わること（ウィザードが開く／設定パネルが開閉する）
  - 入力して送信ボタンを押す → 発言が吹き出しとして描画されること
  - エージェントの返信（outbox）→ 画面が自動で追随して描画すること
  - 再読込しても会話が描画されること（永続化 + 初期描画）

実行条件（既定ではスキップ。テストを遅くしないため）:
    RPCHAT_BROWSER_E2E=1 python -m unittest test.test_e2e_browser
    RPCHAT_SLOW_TESTS=1  python tools/run_tests.py     # 全体実行に含める
必要なもの: playwright（pip install playwright）と Chrome か Edge（既存のものを使う）。
ブラウザ本体のダウンロードは不要（channel="chrome" / "msedge" を使う）。
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
from test.support import IsolatedTestCase

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:  # playwright 未導入でもテストスイートは緑のまま
    HAVE_PLAYWRIGHT = False

_POLL = 0.02
# 既存のブラウザを使う（無ければ playwright 同梱の chromium を試す）
_CHANNELS = ("chrome", "msedge", None)


def browser_e2e_enabled():
    return (os.environ.get("RPCHAT_SLOW_TESTS") == "1"
            or os.environ.get("RPCHAT_BROWSER_E2E") == "1")


class BrowserE2ETest(IsolatedTestCase):
    """本物のブラウザで本物の UI を操作する"""

    @classmethod
    def setUpClass(cls):
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest("playwright 未導入（pip install playwright）")
        if not browser_e2e_enabled():
            raise unittest.SkipTest("ブラウザ E2E は既定でスキップ。RPCHAT_BROWSER_E2E=1 で実行")
        cls._pw = sync_playwright().start()
        cls._browser = None
        tries = []
        for channel in _CHANNELS:
            try:
                kwargs = {"headless": True}
                if channel:
                    kwargs["channel"] = channel
                cls._browser = cls._pw.chromium.launch(**kwargs)
                cls._channel = channel or "chromium"
                break
            except Exception as e:  # noqa: BLE001 - 次の候補へ
                tries.append(f"{channel or 'chromium'}: {e}")
        if cls._browser is None:
            cls._pw.stop()
            raise unittest.SkipTest("利用できるブラウザが無い: " + " / ".join(tries))

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        super().setUp()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.http_thread = threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.http_thread.start()

        self.context = self._browser.new_context(viewport={"width": 1280, "height": 900},
                                                 locale="ja-JP")
        self.page = self.context.new_page()
        self.page_errors = []
        self.console_errors = []
        self.dialogs = []
        self.accept_dialogs = False
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        self.page.on("console", lambda m: self.console_errors.append(m.text)
                     if m.type == "error" else None)
        self.page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog):
        """confirm/alert を記録し、必要なものだけ OK する（既定はキャンセル）"""
        self.dialogs.append(f"{dialog.type}: {dialog.message}")
        if self.accept_dialogs:
            dialog.accept()
        else:
            dialog.dismiss()

    def tearDown(self):
        self.context.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.http_thread.join(timeout=2)
        super().tearDown()

    # ---------------------------------------------------------------- 補助
    def api(self, path, data=None):
        url = self.base + path
        if data is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def open_ui(self):
        self.page.goto(self.base + "/", wait_until="domcontentloaded", timeout=30000)
        # 起動直後はチャンネル一覧 → 先頭を自動で開く。描画が終わるまで待つ
        self.page.wait_for_selector("#chatLog", state="attached", timeout=15000)
        self.page.wait_for_function("() => document.querySelector('#sessionList')?.children.length > 0",
                                    timeout=15000)

    def open_last_assistant_menu(self):
        """最後の AI 応答をホバーして ⋯ を押し、出てきたメニューを返す"""
        page = self.page
        last = page.locator("#chatLog .msg-wrap.assistant").last
        last.hover()
        last.locator(".btn-more").click(timeout=10000)
        menu = page.locator(".more-menu, .more-list, [role='menu']").last
        menu.wait_for(state="visible", timeout=10000)
        return menu

    def send_and_reply(self, text="桟橋で少女に声をかける", reply="少女は振り向いて、小さく手を振った。"):
        """ブラウザ相当の送信 → エージェント相当の返信 までを回す"""
        page = self.page
        page.fill("#messageInput", text)
        page.click("#sendBtn")
        page.wait_for_selector("#chatLog .msg-wrap.user", timeout=15000)
        state = self.api(f"/api/session?sid={self.sid}")
        mid = state["messages"][-1]["id"]
        self.drop_agent_reply(self.sid, mid, reply)
        self.assertTrue(server.process_outbox())
        page.wait_for_selector("#chatLog .msg-wrap.assistant", timeout=20000)
        return mid

    def drop_agent_reply(self, sid, mid, text):
        path = os.path.join(server.OUTBOX, f"{sid}_reply_{mid}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"session": sid, "reply_to": mid, "text": text, "memories": []},
                      f, ensure_ascii=False)
        return path

    # ---------------------------------------------------------------- テスト
    def test_01_buttons_open_and_close_real_panels(self):
        self.api("/api/session/new", {"title": "ブラウザE2E"})
        self.open_ui()
        page = self.page

        # 「新規チャンネル」ボタン → 本物のウィザードが開く
        self.assertFalse(page.locator("#wizardRoot").is_visible())
        page.click("#newSession")
        page.wait_for_selector("#wizardRoot:not(.hidden)", timeout=10000)
        # ✕ で閉じる → 本物の確認ダイアログ → 「保存せずに閉じる」で閉じる
        page.click("#wizardClose")
        page.locator(".wizard-confirm-btns button:has-text('保存せずに閉じる')").first.click(timeout=10000)
        page.wait_for_selector("#wizardRoot.hidden", state="attached", timeout=10000)

        # 設定パネルの開閉（トグルボタンを押す → 表示 → もう一度で閉じる）
        self.assertFalse(page.locator("#settingsPanel").is_visible())
        page.click("#settingsToggle")
        page.wait_for_selector("#settingsPanel:not(.hidden)", timeout=10000)
        page.click("#settingsToggle")
        page.wait_for_selector("#settingsPanel.hidden", state="attached", timeout=10000)

        # 記憶（RAG）モーダル: 開くボタン → 閉じるボタン
        page.click("#ragToggle")
        page.wait_for_selector("#ragModal:not(.hidden)", timeout=10000)
        page.click("#ragClose")
        page.wait_for_selector("#ragModal.hidden", state="attached", timeout=10000)

        self.assertEqual(self.page_errors, [], f"画面の JS がエラーを出した: {self.page_errors}")

    def test_02_send_by_button_and_agent_reply_renders(self):
        sid = self.api("/api/session/new", {"title": "ブラウザ送信"})["sid"]
        self.open_ui()
        page = self.page

        # 入力して送信ボタンを押す → 吹き出しとして描画される
        text = "桟橋で少女に声をかける"
        page.fill("#messageInput", text)
        page.click("#sendBtn")
        page.wait_for_selector("#chatLog .msg-wrap.user", timeout=15000)
        self.assertIn(text, page.locator("#chatLog").inner_text())

        state = self.api(f"/api/session?sid={sid}")
        self.assertEqual(state["messages"][-1]["role"], "user")
        self.assertEqual(state["messages"][-1]["status"], "pending")
        mid = state["messages"][-1]["id"]

        # エージェントの返信（outbox）→ 画面が自動で追随して描画する
        reply = "少女は振り向いて、小さく手を振った。"
        self.drop_agent_reply(sid, mid, reply)
        # 本番では常駐ワーカー（server.main() の outbox_worker）が拾う。テストでは同じ関数を呼ぶ
        self.assertTrue(server.process_outbox())
        page.wait_for_selector("#chatLog .msg-wrap.assistant", timeout=20000)
        self.assertIn("手を振った", page.locator("#chatLog").inner_text())

        # 再読込しても描画される（永続化 + 初期描画の確認）
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#chatLog .msg-wrap.assistant", timeout=20000)
        self.assertIn("手を振った", page.locator("#chatLog").inner_text())

        self.assertEqual(self.page_errors, [], f"画面の JS がエラーを出した: {self.page_errors}")

    def test_03_edit_and_regenerate_from_message_menu(self):
        self.sid = self.api("/api/session/new", {"title": "ブラウザ操作"})["sid"]
        self.open_ui()
        page = self.page
        self.send_and_reply()

        # ⋯ メニューから「本文を編集」→ 改変して適用
        menu = self.open_last_assistant_menu()
        menu.locator(":text('本文を編集')").last.click(timeout=10000)
        page.wait_for_selector("#editModal:not(.hidden)", timeout=10000)
        page.fill("#editText", "改変後の本文です。")
        page.click("#editSave")
        page.wait_for_selector("#editModal.hidden", state="attached", timeout=15000)
        page.wait_for_function(
            "() => (document.querySelector('#chatLog .msg-wrap.assistant')?.innerText || '').includes('改変後の本文')",
            timeout=15000)
        state = self.api(f"/api/session?sid={self.sid}")
        self.assertEqual(state["messages"][-1]["text"], "改変後の本文です。")

        # ⋯ メニューから「再生成」→ 確認ダイアログを OK → 応答が消えて入力待ちに戻る
        self.accept_dialogs = True
        try:
            menu = self.open_last_assistant_menu()
            menu.locator(":text('再生成')").last.click(timeout=10000)
        finally:
            self.accept_dialogs = False
        page.wait_for_function(
            "() => document.querySelectorAll('#chatLog .msg-wrap.assistant').length === 0",
            timeout=20000)
        state = self.api(f"/api/session?sid={self.sid}")
        self.assertEqual(len(state["messages"]), 1, state["messages"])
        self.assertEqual(state["messages"][0]["role"], "user")
        self.assertEqual(state["messages"][0]["status"], "pending")
        self.assertEqual(self.page_errors, [], f"画面の JS がエラーを出した: {self.page_errors}")

    def test_04_choices_render_and_click_fills_input(self):
        self.sid = self.api("/api/session/new", {"title": "ブラウザ選択肢"})["sid"]
        self.open_ui()
        page = self.page
        self.send_and_reply()

        state = self.api(f"/api/session?sid={self.sid}")
        asst_id = state["messages"][-1]["id"]
        choices = [{"label": "①", "text": "静かに頷いて、灯台のほうを見る。"},
                   {"label": "②", "text": "「一緒に来る？」と手を差し出す。"}]
        path = os.path.join(server.OUTBOX, f"{self.sid}_choices_{asst_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"session": self.sid, "reply_to": asst_id, "type": "choices",
                       "choices": choices}, f, ensure_ascii=False)
        self.assertTrue(server.process_outbox())

        # 候補パネルを開いて、候補ボタンを実際にクリック → 入力欄に入る
        page.click("#chatLog .choices-toggle-btn", timeout=15000)
        page.wait_for_selector("#chatLog .choice-item-btn", timeout=20000)
        self.assertEqual(page.locator("#chatLog .choice-item-btn").count(), 2)
        page.locator("#chatLog .choice-item-btn").first.click(timeout=10000)
        self.assertEqual(page.input_value("#messageInput"), choices[0]["text"])
        self.assertEqual(page.locator("#chatLog .choice-item-btn.is-selected").count(), 1)
        self.assertEqual(self.page_errors, [], f"画面の JS がエラーを出した: {self.page_errors}")

    def test_05_attachment_upload_and_send_shows_in_bubble(self):
        self.sid = self.api("/api/session/new", {"title": "ブラウザ添付"})["sid"]
        self.open_ui()
        page = self.page

        tmp = tempfile.mkdtemp(prefix="rp_attach_")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        path = os.path.join(tmp, "灯台のメモ.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("灯台の明かりは、今夜も消えない。")

        # 添付ボタンが握っている file input にファイルを入れる（実機のファイル選択と同じ経路）
        page.set_input_files("#attachInput", path)
        page.wait_for_selector("#attachList .attach-chip:not(.busy)", timeout=20000)
        self.assertIn("灯台のメモ", page.locator("#attachList").inner_text())

        page.fill("#messageInput", "このメモを読んで")
        page.click("#sendBtn")
        page.wait_for_selector("#chatLog .msg-wrap.user", timeout=15000)

        state = self.api(f"/api/session?sid={self.sid}")
        files = state["messages"][-1].get("files") or []
        self.assertEqual(len(files), 1, files)
        self.assertIn("灯台のメモ", json.dumps(files, ensure_ascii=False))
        self.assertIn("灯台のメモ", page.locator("#chatLog .msg-wrap.user").first.inner_text())
        self.assertEqual(self.page_errors, [], f"画面の JS がエラーを出した: {self.page_errors}")


if __name__ == "__main__":
    unittest.main()
