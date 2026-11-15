import os
import shutil
import tempfile
import unittest
import server
import google_ai


class FakeHandler:
    def __init__(self, path, body=None):
        self.path = path
        self._body = body or {}
        self.status = 200
        self.result = None

    def blocked(self, u, post=False):
        return False

    def body(self):
        return self._body

    def send_json(self, data, code=200):
        self.status = code
        self.result = data
        return data


class TestModelFavorites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = server.DATA
        self.orig_sess = server.SESS_DIR
        server.DATA = self.tmp
        server.SESS_DIR = os.path.join(self.tmp, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)

    def tearDown(self):
        server.DATA = self.orig_data
        server.SESS_DIR = self.orig_sess
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fav_name_validation(self):
        self.assertEqual(server._fav_name("お気に入り1"), "お気に入り1")
        self.assertEqual(server._fav_name("123456789012"), "123456789012")  # 12 chars
        with self.assertRaises(ValueError):
            server._fav_name("")
        with self.assertRaises(ValueError):
            server._fav_name("   ")
        with self.assertRaises(ValueError):
            server._fav_name("1234567890123")  # 13 chars
        with self.assertRaises(ValueError):
            server._fav_name("改行\n入り")  # control char
        with self.assertRaises(ValueError):
            server._fav_name("タブ\t入り")  # control char

    def test_fav_model_validation(self):
        # required=True
        self.assertEqual(server._fav_model("gemini-2.5-flash", "google", "本文", required=True), "gemini-2.5-flash")
        with self.assertRaises(ValueError):
            server._fav_model("", "google", "本文", required=True)
        # required=False (choices)
        self.assertEqual(server._fav_model("", "google", "選択肢", required=False), "")
        self.assertEqual(server._fav_model("gemini-2.5-flash-lite", "google", "選択肢", required=False), "gemini-2.5-flash-lite")
        # invalid model format
        with self.assertRaises(ValueError):
            server._fav_model("invalid model format!", "google", "本文", required=True)
        # antigravity allows empty even if required
        self.assertEqual(server._fav_model("", "antigravity", "本文", required=True), "")

    def test_norm_fav(self):
        # Google with valid fields
        fav = server._norm_fav({
            "name": "高機能Pro",
            "provider": "google",
            "main": "gemini-2.5-pro",
            "choices": "gemini-2.5-flash",
            "thinking": "high",
            "choices_thinking": "low",
        })
        self.assertEqual(fav["name"], "高機能Pro")
        self.assertEqual(fav["provider"], "google")
        self.assertEqual(fav["main"], "gemini-2.5-pro")
        self.assertEqual(fav["choices"], "gemini-2.5-flash")
        self.assertEqual(fav["thinking"], "high")
        self.assertEqual(fav["choices_thinking"], "low")

        # Antigravity clears thinking
        ag_fav = server._norm_fav({
            "name": "AG連携",
            "provider": "antigravity",
            "main": "gemini-2.5-pro",
            "thinking": "high",
            "choices_thinking": "low",
        })
        self.assertEqual(ag_fav["thinking"], "")
        self.assertEqual(ag_fav["choices_thinking"], "")

        # Invalid provider
        with self.assertRaises(ValueError):
            server._norm_fav({"name": "X", "provider": "openai", "main": "gpt-4"})

    def test_load_and_save_favorites(self):
        f1 = {
            "id": "fav_1", "name": "Fav 1", "provider": "google",
            "main": "gemini-2.5-flash", "choices": "", "thinking": "auto", "choices_thinking": "low"
        }
        f2 = {
            "id": "fav_2", "name": "Fav 2", "provider": "vertex",
            "main": "gemini-2.5-pro", "choices": "", "thinking": "low", "choices_thinking": "low"
        }
        server.save_model_favorites([f1, f2])

        loaded = server.load_model_favorites()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["id"], "fav_1")
        self.assertEqual(loaded[0]["order"], 0)
        self.assertEqual(loaded[1]["id"], "fav_2")
        self.assertEqual(loaded[1]["order"], 1)

    def test_http_api_crud_and_restore(self):
        # 1. GET initially empty
        h_get = FakeHandler("/api/model-favorites")
        server.H.do_GET(h_get)
        self.assertEqual(h_get.status, 200)
        self.assertEqual(h_get.result["favorites"], [])

        # 2. POST create
        fav1_payload = {
            "name": "日常会話",
            "provider": "google",
            "main": "gemini-2.5-flash",
            "choices": "",
            "thinking": "auto",
            "choices_thinking": "low"
        }
        h_post1 = FakeHandler("/api/model-favorites", {"action": "create", "favorite": fav1_payload})
        server.H.do_POST(h_post1)
        self.assertEqual(h_post1.status, 200)
        self.assertTrue(h_post1.result["ok"])
        fav1 = h_post1.result["favorite"]
        self.assertEqual(fav1["name"], "日常会話")
        self.assertTrue(fav1["id"].startswith("fav_"))
        self.assertEqual(fav1["order"], 0)

        # 3. Duplicate name returns 409
        h_dup_name = FakeHandler("/api/model-favorites", {
            "action": "create",
            "favorite": dict(fav1_payload, main="gemini-2.5-pro")
        })
        server.H.do_POST(h_dup_name)
        self.assertEqual(h_dup_name.status, 409)
        self.assertIn("同じ名前", h_dup_name.result["error"])

        # 4. Duplicate combination returns 409
        h_dup_comb = FakeHandler("/api/model-favorites", {
            "action": "create",
            "favorite": dict(fav1_payload, name="別名だが同設定")
        })
        server.H.do_POST(h_dup_comb)
        self.assertEqual(h_dup_comb.status, 409)
        self.assertIn("同じ組み合わせ", h_dup_comb.result["error"])

        # 5. Create second favorite
        fav2_payload = {
            "name": "精密考察",
            "provider": "google",
            "main": "gemini-2.5-pro",
            "choices": "gemini-2.5-flash-lite",
            "thinking": "high",
            "choices_thinking": "medium"
        }
        h_post2 = FakeHandler("/api/model-favorites", {"action": "create", "favorite": fav2_payload})
        server.H.do_POST(h_post2)
        self.assertEqual(h_post2.status, 200)
        fav2 = h_post2.result["favorite"]
        self.assertEqual(fav2["order"], 1)

        # 6. Update fav1 name
        h_upd = FakeHandler("/api/model-favorites", {
            "action": "update",
            "id": fav1["id"],
            "favorite": dict(fav1_payload, name="超高速Flash")
        })
        server.H.do_POST(h_upd)
        self.assertEqual(h_upd.status, 200)
        self.assertEqual(h_upd.result["favorite"]["name"], "超高速Flash")

        # 7. Delete fav1 (at index 0)
        h_del = FakeHandler("/api/model-favorites", {"action": "delete", "id": fav1["id"]})
        server.H.do_POST(h_del)
        self.assertEqual(h_del.status, 200)
        self.assertEqual(h_del.result["deleted"]["id"], fav1["id"])
        self.assertEqual(h_del.result["index"], 0)
        self.assertEqual(len(h_del.result["favorites"]), 1)
        self.assertEqual(h_del.result["favorites"][0]["id"], fav2["id"])
        self.assertEqual(h_del.result["favorites"][0]["order"], 0)

        # 8. Restore deleted fav1 at index 0
        h_restore = FakeHandler("/api/model-favorites", {
            "action": "restore",
            "favorite": h_del.result["deleted"],
            "index": 0
        })
        server.H.do_POST(h_restore)
        self.assertEqual(h_restore.status, 200)
        reloaded = server.load_model_favorites()
        self.assertEqual(len(reloaded), 2)
        self.assertEqual(reloaded[0]["id"], fav1["id"])
        self.assertEqual(reloaded[0]["order"], 0)
        self.assertEqual(reloaded[1]["id"], fav2["id"])
        self.assertEqual(reloaded[1]["order"], 1)

    def test_provider_api_choices_model_and_strict(self):
        sid = "test_provider_strict_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "Strict Provider Test",
                "ai_provider": "antigravity",
                "ide_model": "",
            }),
            "messages": []
        }
        server.save(s)

        # 1. Antigravity switch with choices_model
        h1 = FakeHandler("/api/ai/provider", {
            "sid": sid,
            "provider": "antigravity",
            "model": "claude-sonnet",
            "choices_model": "claude-haiku",
            "strict": True,
        })
        server.H.do_POST(h1)
        self.assertEqual(h1.status, 200)
        self.assertEqual(h1.result["ide_model"], "claude-sonnet")
        self.assertEqual(h1.result["choices_ide_model"], "claude-haiku")

        # 2. Strict conflict: rotation in progress
        s_rot = server.load(sid)
        s_rot["setting"]["rotation"] = {"phase": "preparing"}
        server.save(s_rot)

        h_strict_rot = FakeHandler("/api/ai/provider", {
            "sid": sid,
            "provider": "antigravity",
            "model": "claude-opus",
            "strict": True,
        })
        server.H.do_POST(h_strict_rot)
        self.assertEqual(h_strict_rot.status, 409)
        self.assertEqual(h_strict_rot.result.get("code"), "busy_rotation")

        # 3. Strict conflict: background memory task
        s_mem = server.load(sid)
        s_mem["setting"].pop("rotation", None)
        s_mem["messages"].append({
            "id": "m1",
            "role": "assistant",
            "memory_status": "processing"
        })
        server.save(s_mem)

        h_strict_mem = FakeHandler("/api/ai/provider", {
            "sid": sid,
            "provider": "antigravity",
            "model": "claude-opus",
            "strict": True,
        })
        server.H.do_POST(h_strict_mem)
        self.assertEqual(h_strict_mem.status, 409)
        self.assertEqual(h_strict_mem.result.get("code"), "busy_background")

    def test_setting_preserves_google_choices_model(self):
        sid = "test_setting_keep_sid"
        s = {
            "id": sid,
            "setting": server.merge_defaults({
                "title": "Keep Choices Test",
                "ai_provider": "google",
                "google_model": "gemini-2.5-pro",
                "google_choices_model": "gemini-2.5-flash-lite",
            }),
            "messages": []
        }
        server.save(s)

        # Save settings without google_choices_model in incoming setting
        h = FakeHandler("/api/setting", {
            "sid": sid,
            "setting": {"title": "New Title"}
        })
        server.H.do_POST(h)
        self.assertEqual(h.status, 200)

        saved = server.load(sid)
        self.assertEqual(saved["setting"]["title"], "New Title")
        self.assertEqual(saved["setting"]["google_choices_model"], "gemini-2.5-flash-lite")

    def test_model_favorites_edge_cases(self):
        # 1. Invalid action -> 400
        h_bad_act = FakeHandler("/api/model-favorites", {"action": "unknown"})
        server.H.do_POST(h_bad_act)
        self.assertEqual(h_bad_act.status, 400)
        self.assertIn("action", h_bad_act.result["error"])

        # 2. Missing favorite payload on create -> 400
        h_bad_create = FakeHandler("/api/model-favorites", {"action": "create"})
        server.H.do_POST(h_bad_create)
        self.assertEqual(h_bad_create.status, 400)

        # 3. Update non-existent -> 404
        h_upd_404 = FakeHandler("/api/model-favorites", {
            "action": "update",
            "id": "nonexistent_id",
            "favorite": {"name": "Test", "provider": "google", "main": "gemini-2.5-flash"}
        })
        server.H.do_POST(h_upd_404)
        self.assertEqual(h_upd_404.status, 404)

        # 4. Delete non-existent -> 404
        h_del_404 = FakeHandler("/api/model-favorites", {"action": "delete", "id": "nonexistent_id"})
        server.H.do_POST(h_del_404)
        self.assertEqual(h_del_404.status, 404)

        # 5. Restore clamping with negative index and large index
        f1 = {
            "name": "初期モデル1", "provider": "antigravity", "main": "", "choices": "",
            "thinking": "", "choices_thinking": ""
        }
        h_c1 = FakeHandler("/api/model-favorites", {"action": "create", "favorite": f1})
        server.H.do_POST(h_c1)
        self.assertEqual(h_c1.status, 200)

        # Restore with negative index (-5) -> clamped to 0
        f_neg = {
            "name": "先頭挿入", "provider": "google", "main": "gemini-2.5-flash", "choices": "",
            "thinking": "auto", "choices_thinking": "low"
        }
        h_rest_neg = FakeHandler("/api/model-favorites", {
            "action": "restore", "favorite": f_neg, "index": -5
        })
        server.H.do_POST(h_rest_neg)
        self.assertEqual(h_rest_neg.status, 200)
        favs = server.load_model_favorites()
        self.assertEqual(favs[0]["name"], "先頭挿入")
        self.assertEqual(favs[0]["order"], 0)

        # Restore with large index (999) -> clamped to end
        f_large = {
            "name": "末尾挿入", "provider": "vertex", "main": "gemini-2.5-pro", "choices": "",
            "thinking": "low", "choices_thinking": "low"
        }
        h_rest_large = FakeHandler("/api/model-favorites", {
            "action": "restore", "favorite": f_large, "index": 999
        })
        server.H.do_POST(h_rest_large)
        self.assertEqual(h_rest_large.status, 200)
        favs = server.load_model_favorites()
        self.assertEqual(favs[-1]["name"], "末尾挿入")

        # 6. Restore duplicate name or combo -> 409
        h_rest_dup_name = FakeHandler("/api/model-favorites", {
            "action": "restore",
            "favorite": dict(f_large, main="gemini-2.5-flash"),  # different model, same name
            "index": 0
        })
        server.H.do_POST(h_rest_dup_name)
        self.assertEqual(h_rest_dup_name.status, 409)

        h_rest_dup_comb = FakeHandler("/api/model-favorites", {
            "action": "restore",
            "favorite": dict(f_large, name="別名"),  # different name, same combo
            "index": 0
        })
        server.H.do_POST(h_rest_dup_comb)
        self.assertEqual(h_rest_dup_comb.status, 409)

        # 7. choicesThinking camelCase support in _norm_fav and _fav_same
        f_camel = server._norm_fav({
            "name": "キャメル", "provider": "google", "main": "gemini-2.5-flash",
            "thinking": "auto", "choicesThinking": "same"
        })
        self.assertEqual(f_camel["choices_thinking"], "same")
        self.assertTrue(server._fav_same(f_camel, {
            "provider": "google", "main": "gemini-2.5-flash", "choices": "",
            "thinking": "auto", "choices_thinking": "same"
        }))


if __name__ == "__main__":
    unittest.main()
