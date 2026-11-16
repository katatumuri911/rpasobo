# -*- coding: utf-8 -*-
import os
import sys
import json
import shutil
import tempfile
import threading
import urllib.request
import urllib.parse
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server

_POLL = 0.02


class TestHistoryAutoloadEndpoint(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_history_autoload_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_images_dir = server.IMAGES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_archive_dir = server.ARCHIVE_DIR

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        os.makedirs(server.IMAGES_DIR, exist_ok=True)
        server.STATES_DIR = os.path.join(server.DATA, "states")
        os.makedirs(server.STATES_DIR, exist_ok=True)
        server.ARCHIVE_DIR = os.path.join(server.DATA, "archive")
        os.makedirs(server.ARCHIVE_DIR, exist_ok=True)

        server.session_cache.clear()
        server.session_revs.clear()

        # Start test HTTP server
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=2)

        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.STATES_DIR = self.orig_states_dir
        server.ARCHIVE_DIR = self.orig_archive_dir
        server.session_cache.clear()
        server.session_revs.clear()

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server_port}{path}")
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status

    def _post(self, path, data):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server_port}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status

    def test_has_history_flag_on_session(self):
        """GET /api/session should include has_history: True when archives exist, False otherwise"""
        sid_with_history = "sess_has_hist"
        sid_no_history = "sess_no_hist"

        # Create session files
        with open(os.path.join(server.SESS_DIR, f"{sid_with_history}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid_with_history, "messages": []}, f)
        with open(os.path.join(server.SESS_DIR, f"{sid_no_history}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid_no_history, "messages": []}, f)

        # Create archive for sid_with_history
        with open(os.path.join(server.ARCHIVE_DIR, f"{sid_with_history}_rot1_turn30.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid_with_history, "messages": []}, f)

        # Query sessions
        res1, status1 = self._get(f"/api/session?sid={sid_with_history}")
        self.assertEqual(status1, 200)
        self.assertTrue(res1.get("has_history"))

        res2, status2 = self._get(f"/api/session?sid={sid_no_history}")
        self.assertEqual(status2, 200)
        self.assertFalse(res2.get("has_history"))

    def test_history_pagination_and_dedup(self):
        """GET /api/session/history returns chunks in reverse rotation order with proper cursors"""
        sid = "sess_multi_rot"

        # Create 3 rotation chunks
        rot1_msgs = [{"id": "m1", "role": "user", "text": "turn 1"}, {"id": "m2", "role": "assistant", "text": "turn 2"}]
        rot2_msgs = [{"id": "m2", "role": "assistant", "text": "turn 2"}, {"id": "m3", "role": "user", "text": "turn 3"}]
        rot3_msgs = [{"id": "m3", "role": "user", "text": "turn 3"}, {"id": "m4", "role": "assistant", "text": "turn 4"}]

        with open(os.path.join(server.ARCHIVE_DIR, f"{sid}_rot1_turn30.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": rot1_msgs}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{sid}_rot2_turn50.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": rot2_msgs}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{sid}_rot3_turn70.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": rot3_msgs}, f)

        # 1. First fetch (no before_rot) -> rot3
        res, status = self._get(f"/api/session/history?sid={sid}")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("rot"), 3)
        self.assertEqual(len(res.get("messages", [])), 2)
        self.assertTrue(res.get("has_more"))
        self.assertEqual(res.get("next_cursor"), 3)

        # 2. Second fetch (before_rot=3) -> rot2
        res, status = self._get(f"/api/session/history?sid={sid}&before_rot=3")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("rot"), 2)
        self.assertTrue(res.get("has_more"))
        self.assertEqual(res.get("next_cursor"), 2)

        # 3. Third fetch (before_rot=2) -> rot1
        res, status = self._get(f"/api/session/history?sid={sid}&before_rot=2")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("rot"), 1)
        self.assertFalse(res.get("has_more"))
        self.assertIsNone(res.get("next_cursor"))

        # 4. Fourth fetch (before_rot=1) -> empty, has_more=False
        res, status = self._get(f"/api/session/history?sid={sid}&before_rot=1")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(len(res.get("messages", [])), 0)
        self.assertFalse(res.get("has_more"))
        self.assertIsNone(res.get("next_cursor"))

    def test_history_missing_sid(self):
        """GET /api/session/history without sid returns 400 error"""
        try:
            self._get("/api/session/history")
            self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            err_data = json.loads(e.read().decode("utf-8"))
            self.assertIn("sid is required", err_data.get("error", ""))

    def test_has_history_ignores_tmp_and_bak_files(self):
        """Non-archive files (.tmp, .bak) should not cause has_history to be True"""
        sid = "sess_tmp_only"
        with open(os.path.join(server.SESS_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": []}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{sid}_rot1_turn30.json.tmp"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": []}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{sid}_rot1_turn30.bak"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": []}, f)

        res, status = self._get(f"/api/session?sid={sid}")
        self.assertEqual(status, 200)
        self.assertFalse(res.get("has_history"))

    def test_history_invalid_sid_characters(self):
        """Path traversal or special character sid returns 400 error"""
        try:
            self._get("/api/session/history?sid=../etc/passwd")
            self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            err_data = json.loads(e.read().decode("utf-8"))
            self.assertIn("sid is required", err_data.get("error", ""))

    def test_branch_preserves_rotation_archives_and_has_history(self):
        """Branching a rotated session should copy archives and set has_history: True on the branch"""
        src_sid = "sess_src_branch"
        src_msgs = [
            {"id": "m1", "role": "user", "text": "turn 51"},
            {"id": "m2", "role": "assistant", "text": "turn 51 reply"}
        ]
        src_setting = {
            "title": "Main Session",
            "rotation_count": 2,
            "total_turns": 51
        }
        with open(os.path.join(server.SESS_DIR, f"{src_sid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "setting": src_setting, "messages": src_msgs}, f)

        # Archives for rot 1 and rot 2
        rot1_msgs = [{"id": "r1m1", "role": "user", "text": "rot1 user"}]
        rot2_msgs = [{"id": "r2m1", "role": "user", "text": "rot2 user"}]
        with open(os.path.join(server.ARCHIVE_DIR, f"{src_sid}_rot1_turn30.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "messages": rot1_msgs}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{src_sid}_rot2_turn50.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "messages": rot2_msgs}, f)

        # Call POST /api/session/branch
        res, status = self._post("/api/session/branch", {"sid": src_sid, "id": "m2"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        new_sid = res.get("sid")
        self.assertTrue(new_sid)
        self.assertNotEqual(new_sid, src_sid)

        # Verify GET /api/session for new_sid has has_history: True
        sess_res, s_status = self._get(f"/api/session?sid={new_sid}")
        self.assertEqual(s_status, 200)
        self.assertTrue(sess_res.get("has_history"))
        self.assertEqual(sess_res.get("setting", {}).get("rotation_count"), 2)

        # Verify GET /api/session/history returns rot 2 and has_more: True
        h_res1, h_status1 = self._get(f"/api/session/history?sid={new_sid}")
        self.assertEqual(h_status1, 200)
        self.assertTrue(h_res1.get("ok"))
        self.assertEqual(h_res1.get("rot"), 2)
        self.assertEqual(h_res1.get("turn"), 50)
        self.assertTrue(h_res1.get("has_more"))
        self.assertEqual(h_res1.get("next_cursor"), 2)
        self.assertEqual(len(h_res1.get("messages", [])), 1)

        # Verify GET /api/session/history?before_rot=2 returns rot 1 and has_more: False
        h_res2, h_status2 = self._get(f"/api/session/history?sid={new_sid}&before_rot=2")
        self.assertEqual(h_status2, 200)
        self.assertTrue(h_res2.get("ok"))
        self.assertEqual(h_res2.get("rot"), 1)
        self.assertEqual(h_res2.get("turn"), 30)
        self.assertFalse(h_res2.get("has_more"))
        self.assertIsNone(h_res2.get("next_cursor"))
        self.assertEqual(len(h_res2.get("messages", [])), 1)

        # Verify copied archive files on disk have updated id matching new_sid
        new_rot1_path = os.path.join(server.ARCHIVE_DIR, f"{new_sid}_rot1_turn30.json")
        new_rot2_path = os.path.join(server.ARCHIVE_DIR, f"{new_sid}_rot2_turn50.json")
        self.assertTrue(os.path.exists(new_rot1_path))
        self.assertTrue(os.path.exists(new_rot2_path))
        with open(new_rot1_path, "r", encoding="utf-8") as f:
            data1 = json.load(f)
            self.assertEqual(data1.get("id"), new_sid)

    def test_branch_session_without_archives_has_no_history(self):
        """Branching an unrotated session should result in has_history: False"""
        src_sid = "sess_src_no_rot"
        src_msgs = [
            {"id": "m1", "role": "user", "text": "turn 1"},
            {"id": "m2", "role": "assistant", "text": "turn 1 reply"}
        ]
        src_setting = {
            "title": "Fresh Session",
            "rotation_count": 0,
            "total_turns": 1
        }
        with open(os.path.join(server.SESS_DIR, f"{src_sid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "setting": src_setting, "messages": src_msgs}, f)

        res, status = self._post("/api/session/branch", {"sid": src_sid, "id": "m2"})
        self.assertEqual(status, 200)
        new_sid = res.get("sid")

        sess_res, s_status = self._get(f"/api/session?sid={new_sid}")
        self.assertEqual(s_status, 200)
        self.assertFalse(sess_res.get("has_history"))

    def test_unchanged_session_response_includes_has_history(self):
        """GET /api/session?sid=...&rev=... (cached unchanged) should return has_history accurately"""
        sid = "sess_unchanged_hist"
        with open(os.path.join(server.SESS_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "rev": 1, "messages": []}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{sid}_rot1_turn30.json"), "w", encoding="utf-8") as f:
            json.dump({"id": sid, "messages": []}, f)

        server.session_revs[sid] = 1

        # Request with matching rev to trigger fast unchanged path
        res, status = self._get(f"/api/session?sid={sid}&rev={sid}:1")
        self.assertEqual(status, 200)
        self.assertTrue(res.get("unchanged"))
        self.assertTrue(res.get("has_history"))

    def test_branch_caps_archives_at_max_rot(self):
        """Branching should only copy archives up to rotation_count"""
        src_sid = "sess_src_cap_rot"
        src_msgs = [{"id": "m1", "role": "user", "text": "t1"}, {"id": "m2", "role": "assistant", "text": "t2"}]
        src_setting = {"title": "Cap Rot Session", "rotation_count": 1, "total_turns": 35}
        with open(os.path.join(server.SESS_DIR, f"{src_sid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "setting": src_setting, "messages": src_msgs}, f)

        # Create archives for rot 1 and orphaned rot 2
        with open(os.path.join(server.ARCHIVE_DIR, f"{src_sid}_rot1_turn30.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "messages": []}, f)
        with open(os.path.join(server.ARCHIVE_DIR, f"{src_sid}_rot2_turn60.json"), "w", encoding="utf-8") as f:
            json.dump({"id": src_sid, "messages": []}, f)

        res, status = self._post("/api/session/branch", {"sid": src_sid, "id": "m2"})
        self.assertEqual(status, 200)
        new_sid = res.get("sid")

        # Verify only rot 1 is copied, not rot 2
        self.assertTrue(os.path.exists(os.path.join(server.ARCHIVE_DIR, f"{new_sid}_rot1_turn30.json")))
        self.assertFalse(os.path.exists(os.path.join(server.ARCHIVE_DIR, f"{new_sid}_rot2_turn60.json")))


if __name__ == "__main__":
    unittest.main()
