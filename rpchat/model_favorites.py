"""Model favorites management and API handlers."""

import os
import unicodedata
import uuid

import google_ai

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


MODEL_FAVORITES_VERSION = 1
FAV_PROVIDERS = ("google", "vertex", "antigravity")


def model_favorites_path():
    # DATA はテストが差し替えるので、定数ではなく毎回組み立てる
    return os.path.join(_dep("data"), "model_favorites.json")


def load_model_favorites():
    d = _dep("read_json")(model_favorites_path(), {})
    raw = d.get("favorites") if isinstance(d, dict) else []
    out = []
    for i, f in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(f, dict):
            continue
        f = dict(f)
        try:
            f["order"] = int(f.get("order", i))
        except (TypeError, ValueError):
            f["order"] = i
        out.append(f)
    out.sort(key=lambda x: (x.get("order", 0), str(x.get("id", ""))))
    return out


def save_model_favorites(favs):
    os.makedirs(_dep("data"), exist_ok=True)
    for i, f in enumerate(favs):
        f["order"] = i
    _dep("write_json")(model_favorites_path(), {
        "version": MODEL_FAVORITES_VERSION,
        "favorites": favs,
    })


def _fav_name(v):
    name = unicodedata.normalize("NFC", str(v or "")).strip()
    if not name:
        raise ValueError("お気に入り名を入れてください")
    if len(name) > 12:
        raise ValueError("お気に入り名は全角12文字までです")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("お気に入り名に使えない文字があります")
    return name


def _fav_model(v, provider, label, required=True):
    model = str(v or "").strip()
    if required and provider != "antigravity" and not model:
        raise ValueError(f"{label}のモデルを選んでください")
    if model and not google_ai.MODEL_ID_RE.fullmatch(model):
        raise ValueError(f"{label}のモデルIDが不正です")
    return model


def _norm_fav(f):
    if not isinstance(f, dict):
        raise ValueError("お気に入りデータが不正です")
    provider = str(f.get("provider") or "").strip().lower()
    if provider not in FAV_PROVIDERS:
        raise ValueError("プロバイダーが不正です")

    if provider == "antigravity":
        thinking = ""
        choices_thinking = ""
    else:
        thinking = str(f.get("thinking") or "auto").strip().lower()
        if thinking not in google_ai.VALID_THINKING_LEVELS:
            raise ValueError("本文の思考レベルが不正です")
        choices_raw = f.get("choices_thinking") or f.get("choicesThinking") or "low"
        choices_thinking = str(choices_raw).strip().lower()
        if choices_thinking not in google_ai.VALID_CHOICES_THINKING_LEVELS:
            raise ValueError("選択肢などの思考レベルが不正です")

    return {
        "name": _fav_name(f.get("name")),
        "provider": provider,
        "main": _fav_model(f.get("main"), provider, "本文", required=True),
        "choices": _fav_model(f.get("choices"), provider, "選択肢", required=False),
        "thinking": thinking,
        "choices_thinking": choices_thinking,
    }


def _fav_same(a, b):
    keys = ("provider", "main", "choices", "thinking")
    if not all((a.get(k) or "") == (b.get(k) or "") for k in keys):
        return False
    ct_a = a.get("choices_thinking") or a.get("choicesThinking") or ""
    ct_b = b.get("choices_thinking") or b.get("choicesThinking") or ""
    return ct_a == ct_b


def _new_id():
    return uuid.uuid4().hex[:10]


def api_get(h):
    with _dep("lock"):
        return h.send_json({"ok": True, "favorites": load_model_favorites()})


def api_post(h, d):
    action = str(d.get("action") or "").strip().lower()
    try:
        with _dep("lock"):
            favs = load_model_favorites()

            if action == "create":
                f = _norm_fav(d.get("favorite") or {})
                if any(x.get("name", "").casefold() == f["name"].casefold() for x in favs):
                    return h.send_json({"error": "同じ名前のお気に入りがあります"}, 409)
                if any(_fav_same(x, f) for x in favs):
                    return h.send_json({"error": "同じ組み合わせのお気に入りがあります"}, 409)
                f["id"] = "fav_" + _new_id()
                f["order"] = len(favs)
                favs.append(f)
                save_model_favorites(favs)
                return h.send_json({"ok": True, "favorites": favs, "favorite": f})

            if action == "update":
                fid = str(d.get("id") or "").strip()
                idx = next((i for i, x in enumerate(favs) if x.get("id") == fid), -1)
                if idx < 0:
                    return h.send_json({"error": "お気に入りが見つかりません"}, 404)
                old = favs[idx]
                f = _norm_fav(d.get("favorite") or {})
                if any(x.get("id") != fid and x.get("name", "").casefold() == f["name"].casefold() for x in favs):
                    return h.send_json({"error": "同じ名前のお気に入りがあります"}, 409)
                if any(x.get("id") != fid and _fav_same(x, f) for x in favs):
                    return h.send_json({"error": "同じ組み合わせのお気に入りがあります"}, 409)
                f["id"] = fid
                f["order"] = old.get("order", idx)
                favs[idx] = f
                save_model_favorites(favs)
                return h.send_json({"ok": True, "favorites": favs, "favorite": f})

            if action == "delete":
                fid = str(d.get("id") or "").strip()
                idx = next((i for i, x in enumerate(favs) if x.get("id") == fid), -1)
                if idx < 0:
                    return h.send_json({"error": "お気に入りが見つかりません"}, 404)
                deleted = favs.pop(idx)
                save_model_favorites(favs)
                return h.send_json({
                    "ok": True, "favorites": favs,
                    "deleted": deleted, "index": idx,
                })

            if action == "restore":
                raw = d.get("favorite") or {}
                f = _norm_fav(raw)
                fid = str(raw.get("id") or "").strip()
                if any(x.get("name", "").casefold() == f["name"].casefold() for x in favs):
                    return h.send_json({"error": "同名のお気に入りが追加されています"}, 409)
                if any(_fav_same(x, f) for x in favs):
                    return h.send_json({"error": "同じ組み合わせのお気に入りがあります"}, 409)
                f["id"] = fid if fid and not any(x.get("id") == fid for x in favs) else "fav_" + _new_id()
                raw_idx = d.get("index")
                try:
                    idx_val = int(raw_idx) if raw_idx is not None else len(favs)
                except (TypeError, ValueError):
                    idx_val = len(favs)
                idx = max(0, min(idx_val, len(favs)))
                f["order"] = idx
                favs.insert(idx, f)
                save_model_favorites(favs)
                return h.send_json({"ok": True, "favorites": favs, "favorite": f})

            return h.send_json({"error": "action が不正です"}, 400)
    except ValueError as e:
        return h.send_json({"error": str(e)}, 400)
