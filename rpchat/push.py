"""Web Push notification engine and handlers."""

import base64
import json

_deps = {}


def bind_deps(**kw):
    """server.py から『呼び出し時に最新値を返す関数』を受け取る（値渡し禁止）。
    テストが server.DATA / server.VAPID_FILE / server.lock / server.read_json を
    差し替えても追従させるため。"""
    for k, v in kw.items():
        if not callable(v):
            raise TypeError(f"rpchat: bind_deps({k}=...) は関数で渡す")
        _deps[k] = v


def _dep(name):
    fn = _deps.get(name)
    if fn is None:  # None を注入された場合と区別する
        raise RuntimeError(f"rpchat: bind_deps({name}=...) が未設定")
    return fn()


def get_vapid():
    """VAPID キーを取得する（なければ生成して data/vapid_keys.json に保存）"""
    Vapid = _dep("vapid")
    if not Vapid:
        return None, None
    with _dep("lock"):
        keys = _dep("read_json")(_dep("vapid_file"), {})
        if isinstance(keys, dict) and keys.get("private_key") and keys.get("public_key"):
            try:
                v = Vapid.from_pem(keys["private_key"].encode("utf-8"))
                return v, keys["public_key"]
            except Exception as e:
                print(f"[push] failed to load vapid keys: {e}")
        try:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            v = Vapid()
            v.generate_keys()
            raw_pub = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            pub_b64 = base64.urlsafe_b64encode(raw_pub).decode("utf-8").rstrip("=")
            priv_pem = v.private_pem().decode("utf-8")
            _dep("write_json")(_dep("vapid_file"), {"private_key": priv_pem, "public_key": pub_b64})
            print("[push] generated new vapid keys")
            return v, pub_b64
        except Exception as e:
            print(f"[push] failed to generate vapid keys: {e}")
            return None, None


def send_push_notification(payload):
    """登録済みのブラウザに Web Push 通知を一括送信する"""
    pywebpush = _dep("webpush")
    Vapid = _dep("vapid")
    if not pywebpush or not Vapid:
        print("[push] pywebpush is not installed; skipping push notification")
        return {"sent": 0, "total": 0, "error": "pywebpush not installed"}
    vapid, _ = get_vapid()
    if not vapid:
        print("[push] vapid keys not available; skipping push")
        return {"sent": 0, "total": 0, "error": "vapid keys not available"}

    with _dep("lock"):
        subs_data = _dep("read_json")(_dep("subs_path"), {})

    if isinstance(subs_data, dict):
        subs = list(subs_data.values())
    elif isinstance(subs_data, list):
        subs = subs_data
    else:
        subs = []

    if not subs:
        print("[push] no active push subscriptions")
        return {"sent": 0, "total": 0}

    data_str = json.dumps(payload, ensure_ascii=False)
    expired_endpoints = set()
    sent_count = 0

    for sub in subs:
        if not isinstance(sub, dict) or not sub.get("endpoint"):
            continue
        endpoint = sub["endpoint"]
        try:
            pywebpush.webpush(
                subscription_info=sub,
                data=data_str,
                vapid_private_key=vapid,
                vapid_claims={"sub": "mailto:rpchat@example.com"},
                ttl=86400,
            )
            sent_count += 1
            print(f"[push] notification sent to {endpoint[:45]}...")
        except pywebpush.WebPushException as ex:
            status_code = getattr(getattr(ex, "response", None), "status_code", None)
            print(f"[push] error sending to {endpoint[:45]}: status={status_code} {ex}")
            if status_code in (404, 410):
                expired_endpoints.add(endpoint)
        except Exception as e:
            print(f"[push] unexpected error sending to {endpoint[:45]}: {e}")

    if expired_endpoints:
        with _dep("lock"):
            cur = _dep("read_json")(_dep("subs_path"), {})
            if isinstance(cur, dict):
                for ep in expired_endpoints:
                    cur.pop(ep, None)
                _dep("write_json")(_dep("subs_path"), cur)
            elif isinstance(cur, list):
                cur = [x for x in cur if isinstance(x, dict) and x.get("endpoint") not in expired_endpoints]
                _dep("write_json")(_dep("subs_path"), cur)
        print(f"[push] removed {len(expired_endpoints)} expired subscriptions")

    return {"sent": sent_count, "total": len(subs)}


def add_subscription(sub, user_agent=""):
    ep = sub["endpoint"]
    with _dep("lock"):
        subs = _dep("read_json")(_dep("subs_path"), {})
        if not isinstance(subs, dict):
            subs = {x["endpoint"]: x for x in subs if isinstance(x, dict) and x.get("endpoint")}
        sub_entry = {
            "endpoint": ep,
            "keys": sub["keys"],
            "userAgent": user_agent or "",
            "updated": _dep("now")(),
        }
        subs[ep] = sub_entry
        _dep("write_json")(_dep("subs_path"), subs)
    print(f"[push] subscribed: {ep[:45]}...")


def remove_subscription(endpoint):
    if not endpoint:
        return
    with _dep("lock"):
        subs = _dep("read_json")(_dep("subs_path"), {})
        if isinstance(subs, dict):
            subs.pop(endpoint, None)
        elif isinstance(subs, list):
            subs = [x for x in subs if isinstance(x, dict) and x.get("endpoint") != endpoint]
        _dep("write_json")(_dep("subs_path"), subs)


def test_payload():
    return {
        "title": "🔔 RP Image Chat テスト通知",
        "body": "プッシュ通知は正常に動作しています。スマホでの画像生成完了時に通知が届きます。",
        "icon": "/icon.png",
        "badge": "/icon.png",
        "tag": "test-push",
        "data": {
            "type": "test",
            "url": "/",
        },
    }


def api_vapid_key(h):
    if not _dep("vapid"):
        return h.send_json({"ok": False, "error": "pywebpush not installed"}, 503)
    v, pub = get_vapid()
    if not pub:
        return h.send_json({"ok": False, "error": "vapid key generation failed"}, 500)
    return h.send_json({"ok": True, "publicKey": pub})


def api_test(h):
    if not _dep("vapid"):
        return h.send_json({"ok": False, "error": "pywebpush がインストールされていません"}, 503)
    payload = test_payload()
    res = send_push_notification(payload)
    if res.get("total", 0) == 0:
        return h.send_json({"ok": False, "error": "登録されている通知先がありません。先に「通知を有効化」を押してください。"}, 400)
    return h.send_json({"ok": True, "sent": res.get("sent", 0), "total": res.get("total", 0)})


def api_subscribe(h, d):
    if d.get("unsubscribe"):
        ep = d.get("endpoint") or ""
        if ep:
            remove_subscription(ep)
        return h.send_json({"ok": True, "unsubscribed": True})
    sub = d.get("subscription") or d
    if not isinstance(sub, dict) or not sub.get("endpoint") or not sub.get("keys"):
        return h.send_json({"error": "invalid subscription object"}, 400)
    hdr = getattr(h, "headers", None)
    if hasattr(hdr, "get"):
        user_agent = hdr.get("User-Agent", "")
    elif isinstance(hdr, dict):
        user_agent = hdr.get("User-Agent", "")
    else:
        user_agent = ""
    add_subscription(sub, user_agent=user_agent)
    return h.send_json({"ok": True})
