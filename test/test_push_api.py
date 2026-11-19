import json
import os
import shutil
import tempfile
import unittest

import server


class FakeHandler:
    def __init__(self, path, body=None, headers=None):
        self.path = path
        self._body = body or {}
        self.headers = headers or {"User-Agent": "TestBrowser/1.0"}
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


class FakeVapidInstance:
    def __init__(self, key="fake_private_key"):
        self.key = key


class FakeVapid:
    @classmethod
    def from_pem(cls, pem_bytes):
        return FakeVapidInstance(pem_bytes.decode("utf-8"))


class FakeWebPushResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeWebPushException(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.response = FakeWebPushResponse(status_code) if status_code is not None else None


class FakeWebPush:
    WebPushException = FakeWebPushException

    def __init__(self):
        self.calls = []
        self.fail_endpoints = {}  # endpoint -> status_code

    def webpush(self, subscription_info, data, vapid_private_key, vapid_claims, ttl):
        endpoint = subscription_info.get("endpoint", "")
        self.calls.append({
            "subscription_info": subscription_info,
            "data": data,
            "vapid_private_key": vapid_private_key,
            "vapid_claims": vapid_claims,
            "ttl": ttl,
        })
        if endpoint in self.fail_endpoints:
            code = self.fail_endpoints[endpoint]
            raise FakeWebPushException(f"Error {code}", status_code=code)


class TestPushApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_data = server.DATA
        self.orig_vapid_file = server.VAPID_FILE
        self.orig_push_subs = server.PUSH_SUBS
        self.orig_vapid = server.Vapid
        self.orig_pywebpush = server.pywebpush

        server.DATA = self.tmp
        server.VAPID_FILE = os.path.join(self.tmp, "vapid_keys.json")
        server.PUSH_SUBS = os.path.join(self.tmp, "push_subscriptions.json")
        self.fake_vapid = FakeVapid
        self.fake_webpush = FakeWebPush()
        server.Vapid = self.fake_vapid
        server.pywebpush = self.fake_webpush

        # Assert all 3 paths are swapped under tmp
        tmp_norm = os.path.abspath(self.tmp)
        self.assertTrue(os.path.abspath(server.DATA).startswith(tmp_norm))
        self.assertTrue(os.path.abspath(server.VAPID_FILE).startswith(tmp_norm))
        self.assertTrue(os.path.abspath(server.PUSH_SUBS).startswith(tmp_norm))

    def tearDown(self):
        server.DATA = self.orig_data
        server.VAPID_FILE = self.orig_vapid_file
        server.PUSH_SUBS = self.orig_push_subs
        server.Vapid = self.orig_vapid
        server.pywebpush = self.orig_pywebpush
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_vapid_key_endpoint_fixed_file_unmodified(self):
        fixed_keys = {
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIG...\n-----END PRIVATE KEY-----",
            "public_key": "BElk_test_fixed_public_key_12345",
        }
        server.write_json(server.VAPID_FILE, fixed_keys)
        with open(server.VAPID_FILE, "rb") as f:
            bytes_before = f.read()

        h = FakeHandler("/api/push/vapid_key")
        server.H.do_GET(h)
        self.assertEqual(h.status, 200)
        self.assertEqual(h.result, {"ok": True, "publicKey": fixed_keys["public_key"]})

        with open(server.VAPID_FILE, "rb") as f:
            bytes_after = f.read()
        self.assertEqual(bytes_before, bytes_after)

    def test_subscribe_and_unsubscribe(self):
        # 1. Subscribe
        sub_body = {
            "subscription": {
                "endpoint": "https://push.example.com/sub/test1",
                "keys": {"p256dh": "pk1", "auth": "ak1"},
            }
        }
        h_sub = FakeHandler("/api/push/subscribe", sub_body, headers={"User-Agent": "CustomAgent/2.0"})
        server.H.do_POST(h_sub)
        self.assertEqual(h_sub.status, 200)
        self.assertEqual(h_sub.result, {"ok": True})

        subs = server.read_json(server.PUSH_SUBS, {})
        self.assertIn("https://push.example.com/sub/test1", subs)
        entry = subs["https://push.example.com/sub/test1"]
        self.assertEqual(entry["endpoint"], "https://push.example.com/sub/test1")
        self.assertEqual(entry["keys"], {"p256dh": "pk1", "auth": "ak1"})
        self.assertEqual(entry["userAgent"], "CustomAgent/2.0")
        self.assertTrue(entry.get("updated"))

        # 2. Unsubscribe
        unsub_body = {
            "unsubscribe": True,
            "endpoint": "https://push.example.com/sub/test1",
        }
        h_unsub = FakeHandler("/api/push/subscribe", unsub_body)
        server.H.do_POST(h_unsub)
        self.assertEqual(h_unsub.status, 200)
        self.assertEqual(h_unsub.result, {"ok": True, "unsubscribed": True})

        subs_after = server.read_json(server.PUSH_SUBS, {})
        self.assertNotIn("https://push.example.com/sub/test1", subs_after)

    def test_push_test_empty_and_success(self):
        fixed_keys = {
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIG...\n-----END PRIVATE KEY-----",
            "public_key": "BElk_test_fixed_public_key_12345",
        }
        server.write_json(server.VAPID_FILE, fixed_keys)

        # 1. Test when no subscriptions (empty) -> 400
        h_test_empty = FakeHandler("/api/push/test")
        server.H.do_GET(h_test_empty)
        self.assertEqual(h_test_empty.status, 400)
        self.assertFalse(h_test_empty.result["ok"])
        self.assertIn("登録されている通知先がありません", h_test_empty.result["error"])

        # Also via POST
        h_test_empty_post = FakeHandler("/api/push/test")
        server.H.do_POST(h_test_empty_post)
        self.assertEqual(h_test_empty_post.status, 400)
        self.assertFalse(h_test_empty_post.result["ok"])

        # 2. Add subscription and test -> 200
        server.write_json(server.PUSH_SUBS, {
            "https://push.example.com/sub/test1": {
                "endpoint": "https://push.example.com/sub/test1",
                "keys": {"p256dh": "pk1", "auth": "ak1"},
                "userAgent": "Agent",
                "updated": "2026-11-19T00:00:00",
            }
        })
        h_test_ok = FakeHandler("/api/push/test")
        server.H.do_GET(h_test_ok)
        self.assertEqual(h_test_ok.status, 200)
        self.assertEqual(h_test_ok.result, {"ok": True, "sent": 1, "total": 1})
        self.assertEqual(len(self.fake_webpush.calls), 1)

    def test_send_push_notification_success_and_expired(self):
        fixed_keys = {
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIG...\n-----END PRIVATE KEY-----",
            "public_key": "BElk_pub",
        }
        server.write_json(server.VAPID_FILE, fixed_keys)
        server.write_json(server.PUSH_SUBS, {
            "https://push.example.com/sub/good": {
                "endpoint": "https://push.example.com/sub/good",
                "keys": {"p256dh": "pk_good", "auth": "ak_good"},
            },
            "https://push.example.com/sub/expired410": {
                "endpoint": "https://push.example.com/sub/expired410",
                "keys": {"p256dh": "pk_bad1", "auth": "ak_bad1"},
            },
            "https://push.example.com/sub/expired404": {
                "endpoint": "https://push.example.com/sub/expired404",
                "keys": {"p256dh": "pk_bad2", "auth": "ak_bad2"},
            },
        })
        self.fake_webpush.fail_endpoints["https://push.example.com/sub/expired410"] = 410
        self.fake_webpush.fail_endpoints["https://push.example.com/sub/expired404"] = 404

        res = server.send_push_notification({"title": "Test Notification"})
        self.assertEqual(res, {"sent": 1, "total": 3})

        # Expired endpoints should have been pruned from PUSH_SUBS
        cur_subs = server.read_json(server.PUSH_SUBS, {})
        self.assertIn("https://push.example.com/sub/good", cur_subs)
        self.assertNotIn("https://push.example.com/sub/expired410", cur_subs)
        self.assertNotIn("https://push.example.com/sub/expired404", cur_subs)

    def test_send_push_notification_pywebpush_not_installed(self):
        server.pywebpush = None
        res = server.send_push_notification({"title": "Test"})
        self.assertEqual(res, {"sent": 0, "total": 0, "error": "pywebpush not installed"})

        # Vapid key GET without Vapid
        server.Vapid = None
        h_vap = FakeHandler("/api/push/vapid_key")
        server.H.do_GET(h_vap)
        self.assertEqual(h_vap.status, 503)
        self.assertEqual(h_vap.result, {"ok": False, "error": "pywebpush not installed"})

        # Test GET without Vapid
        h_test = FakeHandler("/api/push/test")
        server.H.do_GET(h_test)
        self.assertEqual(h_test.status, 503)
        self.assertEqual(h_test.result, {"ok": False, "error": "pywebpush がインストールされていません"})

    def test_bind_deps_type_error_and_unbound_error(self):
        import rpchat.push as push
        with self.assertRaises(TypeError):
            push.bind_deps(vapid_file="not_callable")
        with self.assertRaises(RuntimeError):
            push._dep("unknown_push_dep")

    def test_send_push_notification_status_500_kept(self):
        fixed_keys = {"private_key": "-----BEGIN PRIVATE KEY-----\nMIG...\n-----END PRIVATE KEY-----", "public_key": "pub"}
        server.write_json(server.VAPID_FILE, fixed_keys)
        server.write_json(server.PUSH_SUBS, {
            "https://push.example.com/sub/servererr": {
                "endpoint": "https://push.example.com/sub/servererr",
                "keys": {"p256dh": "pk", "auth": "ak"},
            }
        })
        self.fake_webpush.fail_endpoints["https://push.example.com/sub/servererr"] = 500
        res = server.send_push_notification({"title": "Test 500"})
        self.assertEqual(res, {"sent": 0, "total": 1})
        # 500 does NOT prune
        cur_subs = server.read_json(server.PUSH_SUBS, {})
        self.assertIn("https://push.example.com/sub/servererr", cur_subs)

    def test_send_push_notification_subs_as_list(self):
        fixed_keys = {"private_key": "-----BEGIN PRIVATE KEY-----\nMIG...\n-----END PRIVATE KEY-----", "public_key": "pub"}
        server.write_json(server.VAPID_FILE, fixed_keys)
        server.write_json(server.PUSH_SUBS, [
            {"endpoint": "https://push.example.com/sub/list_good", "keys": {"p256dh": "pk", "auth": "ak"}},
            {"endpoint": "https://push.example.com/sub/list_expired", "keys": {"p256dh": "pk", "auth": "ak"}},
        ])
        self.fake_webpush.fail_endpoints["https://push.example.com/sub/list_expired"] = 410
        res = server.send_push_notification({"title": "Test List"})
        self.assertEqual(res, {"sent": 1, "total": 2})
        cur_subs = server.read_json(server.PUSH_SUBS, [])
        self.assertEqual(len(cur_subs), 1)
        self.assertEqual(cur_subs[0]["endpoint"], "https://push.example.com/sub/list_good")

    def test_subscribe_invalid_and_empty_unsubscribe(self):
        # Invalid object
        h_bad = FakeHandler("/api/push/subscribe", {"subscription": "not a dict"})
        server.H.do_POST(h_bad)
        self.assertEqual(h_bad.status, 400)
        self.assertIn("invalid subscription object", h_bad.result["error"])

        # Unsubscribe with empty endpoint
        h_unsub_empty = FakeHandler("/api/push/subscribe", {"unsubscribe": True, "endpoint": ""})
        server.H.do_POST(h_unsub_empty)
        self.assertEqual(h_unsub_empty.status, 200)
        self.assertTrue(h_unsub_empty.result["ok"])

    def test_send_push_notification_reentrant_lock(self):
        fixed_keys = {"private_key": "-----BEGIN PRIVATE KEY-----\nMIG...\n-----END PRIVATE KEY-----", "public_key": "pub"}
        server.write_json(server.VAPID_FILE, fixed_keys)
        server.write_json(server.PUSH_SUBS, {
            "https://push.example.com/sub/locktest": {
                "endpoint": "https://push.example.com/sub/locktest",
                "keys": {"p256dh": "pk", "auth": "ak"},
            }
        })
        with server.lock:
            # Calling while lock is held must not deadlock because server.lock is an RLock
            v, pub = server.get_vapid()
            self.assertEqual(pub, "pub")
            res = server.send_push_notification({"title": "Reentrancy test"})
            self.assertEqual(res, {"sent": 1, "total": 1})

    def test_subscribe_with_none_headers_and_list_conversion(self):
        server.write_json(server.PUSH_SUBS, [
            {"endpoint": "https://push.example.com/sub/existing", "keys": {"p256dh": "pk0", "auth": "ak0"}}
        ])
        sub_body = {
            "subscription": {
                "endpoint": "https://push.example.com/sub/new",
                "keys": {"p256dh": "pk1", "auth": "ak1"},
            }
        }
        h = FakeHandler("/api/push/subscribe", sub_body, headers=None)
        h.headers = None
        server.H.do_POST(h)
        self.assertEqual(h.status, 200)

        subs = server.read_json(server.PUSH_SUBS, {})
        self.assertIsInstance(subs, dict)
        self.assertIn("https://push.example.com/sub/existing", subs)
        self.assertIn("https://push.example.com/sub/new", subs)
        self.assertEqual(subs["https://push.example.com/sub/new"]["userAgent"], "")

    def test_unsubscribe_when_subs_is_list(self):
        server.write_json(server.PUSH_SUBS, [
            {"endpoint": "https://push.example.com/sub/keep", "keys": {"p256dh": "pk1", "auth": "ak1"}},
            {"endpoint": "https://push.example.com/sub/remove", "keys": {"p256dh": "pk2", "auth": "ak2"}},
        ])
        h = FakeHandler("/api/push/subscribe", {"unsubscribe": True, "endpoint": "https://push.example.com/sub/remove"})
        server.H.do_POST(h)
        self.assertEqual(h.status, 200)

        subs = server.read_json(server.PUSH_SUBS, [])
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["endpoint"], "https://push.example.com/sub/keep")

    def test_push_unbound_dep_raises_runtime_error(self):
        import rpchat.push as push
        saved = dict(push._deps)
        try:
            push._deps.clear()
            with self.assertRaises(RuntimeError):
                push.get_vapid()
            with self.assertRaises(RuntimeError):
                push.send_push_notification({"title": "Fail"})
        finally:
            push._deps.update(saved)


