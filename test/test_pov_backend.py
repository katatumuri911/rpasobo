# -*- coding: utf-8 -*-
import unittest
import server
import tempfile
import shutil
import os
import threading
import json
import urllib.request

# serve_forever の待ち時間。既定の 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒ブロックする
_POLL = 0.02


class TestPovBackend(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_pov_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.session_cache.clear()

        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _post(self, path, payload):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_normalize_tag_key(self):
        self.assertEqual(server.normalize_tag_key("  (pov:1.2) "), "pov")
        self.assertEqual(server.normalize_tag_key("[[[from behind]]]"), "from behind")
        self.assertEqual(server.normalize_tag_key("<lora:something:0.8>"), "<lora:something:0.8>")
        self.assertEqual(server.normalize_tag_key(""), "")

    def test_is_count_tag_key(self):
        self.assertTrue(server.is_count_tag_key("1girl"))
        self.assertTrue(server.is_count_tag_key("2girls"))
        self.assertTrue(server.is_count_tag_key("3boys"))
        self.assertTrue(server.is_count_tag_key("solo"))
        self.assertFalse(server.is_count_tag_key("pov"))
        self.assertFalse(server.is_count_tag_key("from behind"))

    def test_toggle_pov_in_prompt_off(self):
        p = "1girl, pov, from behind"
        res = server.toggle_pov_in_prompt(p, False)
        self.assertEqual(res, "1girl, from behind")

        p2 = "masterpiece, 1girl, (pov:1.1), smile"
        res2 = server.toggle_pov_in_prompt(p2, False)
        self.assertEqual(res2, "masterpiece, 1girl, smile")

    def test_toggle_pov_in_prompt_on(self):
        # 1. After count tag block
        p1 = "1girl, from behind"
        res1 = server.toggle_pov_in_prompt(p1, True)
        self.assertEqual(res1, "1girl, pov, from behind")

        # 2. After multiple count tags
        p2 = "1girl, 1boy, sitting"
        res2 = server.toggle_pov_in_prompt(p2, True)
        self.assertEqual(res2, "1girl, 1boy, pov, sitting")

        # 3. No count tag, but quality tag present in first 8 tokens
        p3 = "masterpiece, best quality, bedroom"
        res3 = server.toggle_pov_in_prompt(p3, True)
        self.assertEqual(res3, "masterpiece, best quality, pov, bedroom")

        # 4. No count tag and no quality tag -> prepended at index 0
        p4 = "bedroom, night"
        res4 = server.toggle_pov_in_prompt(p4, True)
        self.assertEqual(res4, "pov, bedroom, night")

    def test_apply_pov_mode_to_prompt(self):
        setting = {"image": {"pov_mode": "on"}}
        self.assertEqual(server.apply_pov_mode_to_prompt("1girl, smile", setting), "1girl, pov, smile")

        setting["image"]["pov_mode"] = "off"
        self.assertEqual(server.apply_pov_mode_to_prompt("1girl, pov, smile", setting), "1girl, smile")

        setting["image"]["pov_mode"] = "auto"
        self.assertEqual(server.apply_pov_mode_to_prompt("1girl, pov, smile", setting), "1girl, pov, smile")
        self.assertEqual(server.apply_pov_mode_to_prompt("1girl, smile", setting), "1girl, smile")

    def test_apply_pov_mode_to_state_camera(self):
        state = [{"name": "heroine", "camera": ["from behind", "ass focus"]}]
        setting = {"image": {"pov_mode": "on"}}
        server.apply_pov_mode_to_state_camera(state, setting)
        self.assertEqual(state[0]["camera"], ["pov", "from behind", "ass focus"])

        setting["image"]["pov_mode"] = "off"
        server.apply_pov_mode_to_state_camera(state, setting)
        self.assertEqual(state[0]["camera"], ["from behind", "ass focus"])

        # String format camera
        state2 = [{"name": "heroine", "camera": "from behind, ass focus"}]
        setting["image"]["pov_mode"] = "on"
        server.apply_pov_mode_to_state_camera(state2, setting)
        self.assertEqual(state2[0]["camera"], ["pov", "from behind", "ass focus"])

    def test_api_session_image_pov(self):
        s = server.create_session()
        sid = s["id"]
        self.assertEqual(s["setting"]["image"]["pov_mode"], "auto")
        self.assertEqual(s["setting"]["image"]["pov_keep"], True)

        resp = self._post("/api/session/image_pov", {"sid": sid, "pov_mode": "on", "pov_keep": False})
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("pov_mode"), "on")
        self.assertEqual(resp.get("pov_keep"), False)

        reloaded = server.load(sid)
        self.assertEqual(reloaded["setting"]["image"]["pov_mode"], "on")
        self.assertEqual(reloaded["setting"]["image"]["pov_keep"], False)


if __name__ == "__main__":
    unittest.main()
