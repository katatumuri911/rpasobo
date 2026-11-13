"""
Google AI Studio (Gemini Developer API) プロバイダ用の最小クライアント。

- APIキーは .env ファイル（GEMINI_API_KEY）で管理する（セッションJSONには入れない）
- models.list の結果をキャッシュする
- generateContent を直接呼ぶモードと、function calling で
  「モデルにコンテキストを取りに来させる」fetch モードの両方を提供する
- 安全設定を OFF にしても PROHIBITED_CONTENT は解除できない
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
ENV_FILE = os.environ.get("RPCHAT_ENV_FILE", os.path.join(ROOT, ".env"))
KEY_FILE = ENV_FILE  # 後方互換性のため維持
SAFETY_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_JAILBREAK",
]
EXCLUDE_MODEL_PARTS = (
    "-image", "embedding", "tts", "-live", "transcribe", "robotics", "lyria",
    "omni", "deep-research", "computer-use", "nano-banana",
)

_models_cache = {"ts": 0.0, "key_hint": "", "models": [], "error": ""}
_models_lock = threading.Lock()


class GoogleAPIError(Exception):
    def __init__(self, message, status=None, code="", retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = bool(retryable)


def read_env_file(path=None):
    """指定された .env ファイルを辞書として読み込む。"""
    target = path or ENV_FILE
    if not os.path.exists(target):
        return {}
    res = {}
    try:
        with open(target, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    if k:
                        res[k] = v
    except Exception:
        pass
    return res


def load_env(path=None, override=False):
    """指定された .env ファイルの環境変数を os.environ に反映する。"""
    env_vars = read_env_file(path)
    for k, v in env_vars.items():
        if override or k not in os.environ:
            os.environ[k] = v
    return env_vars


# 初期化時に .env を読み込む
load_env()


def _cleanup_legacy_key_file():
    legacy_file = os.path.join(DATA, "google_api_key.json")
    if os.path.exists(legacy_file):
        try:
            os.remove(legacy_file)
        except OSError:
            pass


# 旧ファイルがあればクリーンアップ
_cleanup_legacy_key_file()


def load_key(path=None):
    """(key, source) を返す。.env が優先、なければプロセスの環境変数。"""
    env_file_vars = read_env_file(path or ENV_FILE)
    file_key = (env_file_vars.get("GEMINI_API_KEY") or env_file_vars.get("GOOGLE_API_KEY") or "").strip()
    if file_key:
        return file_key, ".env"
    env = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if env:
        return env, "env"
    return "", ""


def save_key(key, path=None):
    key = (key or "").strip()
    if not key or len(key) < 10 or any(c.isspace() for c in key):
        raise ValueError("APIキーの形式が不正です")

    target = path or ENV_FILE
    lines = []
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k == "GEMINI_API_KEY":
                new_lines.append(f"GEMINI_API_KEY={key}\n")
                found = True
                continue
            elif k == "GOOGLE_API_KEY":
                if not found:
                    new_lines.append(f"GEMINI_API_KEY={key}\n")
                    found = True
                continue
        new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"GEMINI_API_KEY={key}\n")

    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.replace(tmp, target)

    os.environ["GEMINI_API_KEY"] = key
    os.environ.pop("GOOGLE_API_KEY", None)
    _cleanup_legacy_key_file()
    invalidate_models_cache()


def delete_key(path=None):
    target = path or ENV_FILE
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                        continue
                new_lines.append(line)

            tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            os.replace(tmp, target)
        except OSError:
            pass

    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)
    _cleanup_legacy_key_file()
    invalidate_models_cache()


def key_hint(key=None):
    key = (load_key()[0] if key is None else key) or ""
    return key[-4:] if len(key) >= 4 else ""


def invalidate_models_cache():
    with _models_lock:
        _models_cache.update(ts=0.0, key_hint="", models=[], error="")


def list_models(refresh=False, timeout=30):
    key, _ = load_key()
    if not key:
        raise GoogleAPIError("APIキーが未設定です", code="NO_KEY")
    hint = key_hint(key)
    with _models_lock:
        if (not refresh and _models_cache["models"]
                and _models_cache["key_hint"] == hint
                and time.time() - _models_cache["ts"] < 600):
            return list(_models_cache["models"])
    url = f"{BASE}/models?pageSize=1000"
    req = urllib.request.Request(url, headers={"x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        msg, code = _error_message(e.code, raw)
        raise GoogleAPIError(msg, status=e.code, code=code, retryable=_retryable(e.code))
    except Exception as e:
        raise GoogleAPIError(f"モデル一覧を取得できません: {e}", retryable=True)

    rows = []
    for m in data.get("models") or []:
        name = str(m.get("name") or "")
        mid = name.split("/", 1)[-1] if name.startswith("models/") else name
        if not mid:
            continue
        methods = [str(x) for x in (m.get("supportedGenerationMethods") or [])]
        if "generateContent" not in methods:
            continue
        low = mid.lower()
        if not (low.startswith("gemini-") or low.startswith("gemma-")):
            continue
        if any(p in low for p in EXCLUDE_MODEL_PARTS):
            continue
        rows.append({
            "id": mid,
            "label": m.get("displayName") or mid,
            "inputTokenLimit": m.get("inputTokenLimit"),
            "outputTokenLimit": m.get("outputTokenLimit"),
            "thinking": bool(m.get("thinking")),
        })
    rows.sort(key=lambda x: x["id"])
    with _models_lock:
        _models_cache.update(ts=time.time(), key_hint=hint, models=list(rows), error="")
    return rows


def default_model():
    rows = list_models()
    for pat in (r"^gemini-4-hydrogen$", r"^gemini-3\.8-flash$", r"^gemini-flash-latest$",
                r"^gemini-3\.6-flash$", r"^gemini-3\.5-flash$", r"^gemini-3\.5-flash-lite$"):
        for r in rows:
            if re.search(pat, r["id"]):
                return r["id"]
    return rows[0]["id"] if rows else ""


def _error_message(status, raw):
    try:
        d = json.loads(raw)
        err = d.get("error") if isinstance(d, dict) else None
        if isinstance(err, dict):
            return (f"HTTP {status}: {err.get('status') or ''} {err.get('message') or ''}").strip(), str(err.get("status") or "")
    except Exception:
        pass
    return f"HTTP {status}: {raw[:500]}", ""


def _retryable(status):
    return status in (408, 429) or 500 <= int(status or 0) <= 599


def request(model, payload, timeout=180):
    key, _ = load_key()
    if not key:
        raise GoogleAPIError("APIキーが未設定です", code="NO_KEY")
    url = f"{BASE}/models/{urllib.parse.quote(model, safe='')}:generateContent"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        msg, code = _error_message(e.code, raw)
        raise GoogleAPIError(msg, status=e.code, code=code, retryable=_retryable(e.code))
    except Exception as e:
        raise GoogleAPIError(f"Google API に接続できません: {e}", retryable=True)


def extract_text(data):
    """GenerateContentResponse から本文テキストとメタ情報を取る。thought は無視。"""
    meta = {"finish_reason": None, "block_reason": None, "safety_ratings": [], "usage": {}}
    if not isinstance(data, dict):
        return "", meta
    um = data.get("usageMetadata")
    if isinstance(um, dict):
        meta["usage"] = um
    pf = data.get("promptFeedback")
    if isinstance(pf, dict):
        meta["block_reason"] = pf.get("blockReason")
        if isinstance(pf.get("safetyRatings"), list):
            meta["safety_ratings"] = pf["safetyRatings"]
    cands = data.get("candidates")
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        c0 = cands[0]
        meta["finish_reason"] = c0.get("finishReason")
        if isinstance(c0.get("safetyRatings"), list):
            meta["safety_ratings"] = c0["safetyRatings"]
        texts = []
        for p in (c0.get("content") or {}).get("parts") or []:
            if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str):
                texts.append(p["text"])
        if texts:
            return "".join(texts), meta
    return "", meta


def extract_function_call(data, name="fetch_prompt"):
    if not isinstance(data, dict):
        return None, None
    cands = data.get("candidates")
    if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict):
        return None, None
    content = cands[0].get("content") or {}
    for p in content.get("parts") or []:
        if isinstance(p, dict):
            fc = p.get("functionCall")
            if isinstance(fc, dict) and fc.get("name") == name:
                return content, fc
    return content, None


def parse_json_text(raw):
    raw = (raw or "").strip()
    if not raw:
        return None, False
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw, strict=False)
        if isinstance(obj, dict):
            return obj, True
    except Exception:
        pass
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(raw[i:j + 1], strict=False)
            if isinstance(obj, dict):
                return obj, True
        except Exception:
            pass
    return None, False


def _safety_settings(on):
    return [{"category": c, "threshold": "OFF"} for c in SAFETY_CATEGORIES] if on else None


def _parts_with_images(text, images):
    parts = [{"text": text}]
    for im in images or []:
        mime = im.get("mimeType") or "image/png"
        data = im.get("data") or ""
        if data:
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
    return parts


def generate(model, prompt, system="", *, safety_off=True, max_output_tokens=8192,
             fetch=True, images=None, timeout=180, fetch_name="fetch_prompt"):
    """
    戻り値:
      {"text": str, "meta": {...}, "fetch_called": bool, "first_status", "second_status",
       "elapsed_ms": int, "error": str|None, "retryable": bool, "http_status": int|None}
    images がある場合は fetch を使わず、1回のリクエストで画像も送る。
    """
    if not model:
        return {"text": "", "meta": {}, "fetch_called": False, "error": "モデルが未設定です", "retryable": False, "http_status": None, "elapsed_ms": 0}
    system = system or "あなたは日本語ロールプレイの本文生成器です。必ずJSONのみを返します。"
    use_fetch = bool(fetch) and not images
    safety = _safety_settings(safety_off)
    t0 = time.time()
    first_status = second_status = None
    fetch_called = False
    try:
        if use_fetch:
            first_payload = {
                "systemInstruction": {"parts": [{"text": (
                    "あなたは日本語ロールプレイの本文生成器です。"
                    "今回の応答を書く前に、必ず fetch_prompt を呼び出してコンテキストを取得してください。"
                    "取得するまで本文を書いてはいけません。"
                )}]},
                "contents": [{"role": "user", "parts": [{"text": "fetch_prompt を呼び出してコンテキストを取得してください。"}]}],
                "tools": [{"functionDeclarations": [{
                    "name": fetch_name,
                    "description": "このチャンネルの設定・会話・今回のユーザー発言を取得する。引数はない。",
                }]}],
                "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [fetch_name]}},
                "generationConfig": {"maxOutputTokens": 256},
            }
            if safety:
                first_payload["safetySettings"] = safety
            first_status, first_data = request(model, first_payload, timeout=timeout)
            model_content, fc = extract_function_call(first_data, fetch_name)
            fetch_called = bool(fc)
            if not fc:
                _, meta = extract_text(first_data)
                return {"text": "", "meta": meta, "fetch_called": False,
                        "first_status": first_status, "second_status": None,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                        "error": "fetch step: model did not call fetch_prompt",
                        "retryable": False, "http_status": first_status}
            second_payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [
                    {"role": "user", "parts": [{"text": "fetch_prompt を呼び出してコンテキストを取得してください。"}]},
                    model_content,
                    {"role": "user", "parts": [{"functionResponse": {
                        "name": fetch_name,
                        "response": {"context": prompt},
                    }}]},
                ],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_output_tokens},
            }
            if safety:
                second_payload["safetySettings"] = safety
            second_status, second_data = request(model, second_payload, timeout=timeout)
            text, meta = extract_text(second_data)
            err = None
            if meta.get("block_reason"):
                err = f"prompt blocked: {meta['block_reason']}"
            elif meta.get("finish_reason") in ("SAFETY", "PROHIBITED_CONTENT", "PUP_LIMITED_DISABLED"):
                err = f"generation blocked: {meta['finish_reason']}"
            elif not text:
                err = f"empty response (finishReason={meta.get('finish_reason')})"
            return {"text": text, "meta": meta, "fetch_called": True,
                    "first_status": first_status, "second_status": second_status,
                    "elapsed_ms": int((time.time() - t0) * 1000),
                    "error": err, "retryable": bool(err and "empty response" in err),
                    "http_status": second_status}
        else:
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": _parts_with_images(prompt, images)}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_output_tokens},
            }
            if safety:
                payload["safetySettings"] = safety
            status, data = request(model, payload, timeout=timeout)
            text, meta = extract_text(data)
            err = None
            if meta.get("block_reason"):
                err = f"prompt blocked: {meta['block_reason']}"
            elif meta.get("finish_reason") in ("SAFETY", "PROHIBITED_CONTENT", "PUP_LIMITED_DISABLED"):
                err = f"generation blocked: {meta['finish_reason']}"
            elif not text:
                err = f"empty response (finishReason={meta.get('finish_reason')})"
            return {"text": text, "meta": meta, "fetch_called": False,
                    "first_status": status, "second_status": None,
                    "elapsed_ms": int((time.time() - t0) * 1000),
                    "error": err, "retryable": bool(err and "empty response" in err),
                    "http_status": status}
    except GoogleAPIError as e:
        return {"text": "", "meta": {}, "fetch_called": fetch_called,
                "first_status": first_status, "second_status": second_status,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "error": str(e), "retryable": bool(e.retryable),
                "http_status": e.status, "code": e.code}
