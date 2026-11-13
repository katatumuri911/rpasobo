# RP Image Chat — Vertex AI プロバイダ追加 完全実装仕様書（v6 / FINAL）

対象ファイル: `google_ai.py` / `server.py` / `public/index.html` / `public/app.js` / `start.bat` / `start_mobile.bat` / `.env.example` / `test/test_vertex_ai.py`

前提: 既存 2 プロバイダ（`antigravity` / `google` = Google AI Studio）は**完全非回帰**。Vertex AI（`vertex`）は **`google` と同じ API ワーカー / Outbox パイプラインに合流**させ、本文・選択肢（通常 / 🔞erotic）・画像プロンプト推敲（`image_refine`）・状態更新（`state`）・RAG記憶検索を共通動作させる。

---

## 1. 目的と最終ゴール

### 1.1 背景

現状のプロバイダは 2 つ。

1. **`antigravity`**: 外部IDEセッションとファイルベース（`inbox/`・`outbox/`・`sent` 台帳）で連携。
2. **`google`**: `GEMINI_API_KEY` でバックグラウンドスレッドから Gemini API を直接呼び、結果を `outbox/` に書いて合流。

本改修はここに第3のプロバイダ **Google Cloud Vertex AI (`vertex`)** を追加し、GCP サービスアカウント JSON または ADC で Vertex 上の Gemini を利用可能にする。

### 1.2 達成すべき最終状態

1. **UI からの設定と切り替え**
   - 設定画面（`#aiProvider`）と下部モデルセレクタ（`#ideModel`）の両方から Vertex を選べる。
   - 未設定時は `checked()` が伝える `vertex_not_configured` で `#vertexConfigModal` が自動で開く。
   - SA JSON のファイル選択／貼り付け時に `project_id` を自動抽出し、保存時にトークン取得＋モデル一覧APIの疎通確認まで**原子的に**完了する。
   - `data/vertex_sa.json`（または ADC）と `.env`（`VERTEX_PROJECT_ID` / `VERTEX_LOCATION`）でモデル一覧を取得し、任意のモデルID（`gemini-2.0-flash-001` 等）を指定できる。
   - `google_fetch` / `google_safety_off` / `google_max_output_tokens` は API プロバイダ共通設定として `google` と `vertex` で共有・同期する。
2. **共通パイプラインでの完全動作**
   - `vertex` も `google` と同じワーカー（`run_google_task`）と `outbox/` パイプラインに合流。
   - `user`（本文＋`state` 更新）／`choices`（通常8択・🔞erotic8択）／`image_refine`／RAG 検索と `save_rag_hits` がすべて共通動作。
3. **SDK 優先＋REST 透過フォールバック（`generate()` 非破壊）**
   - `google-genai` があれば `genai.Client` を優先し、未導入・署名付き Part（`thoughtSignature`）・構造不一致・SDK 例外時は `urllib` の REST へ透過フォールバック。
   - 分岐は `request()` / `list_models()` の内部に閉じ込め、**現行 `generate()` の構造・引数・戻り値（`fetch_called` / `elapsed_ms` / `first_status` / `second_status` / `model_content` 保持）を 100% 維持**する。
4. **Antigravity からの完全隔離と安全な復帰**
   - API プロバイダ使用中は poke・自動作成・ローテーション・`sent` 台帳更新・失効スイーパの誤発動・`memory_extract` の無限リトライを一切行わない（判定は `is_api_provider(st)` に統一）。
   - 切替・Vertex 設定削除時は進行中タスクを `pending` に戻し、`sent` に `reset: True` を記録する。

### 1.3 変更対象

| ファイル | 内容 |
|---|---|
| `google_ai.py` | URL構築ヘルパー、Vertex 認証（Singleflight＋失敗短縮キャッシュ）、原子的 `.env`/SA 保存、SDK/REST 透過、モデル一覧、Safety/Thinking 制御 |
| `server.py` | `is_api_provider` 統一、`load()` スイーパ保護、ワーカー改修、Vertex 用エンドポイント、`/api/setting` の keep、削除時クリーンアップ |
| `public/index.html` | `#vertexBox` と `#vertexConfigModal` |
| `public/app.js` | `checked()` の `code` 伝搬、状態管理、全イベント配線、`syncGoogleSettingsUI`、`#ideModel`/`#choicesIdeModel` の3プロバイダ化、進捗表示 |
| `start.bat` / `start_mobile.bat` / `.env.example` | `google-genai` / `cryptography` の導入と環境変数テンプレート |
| `test/test_vertex_ai.py` | 新規テスト（全24ケース） |

---

## 2. 不変条件（Invariants）

優先順位は **既存非回帰 > 新機能**。迷ったら既存コードを変えない。

1. **`merge_defaults` の許可リスト**: `server.py:926` 付近を `("antigravity", "google", "vertex")` に拡張する。
2. **`transport="google"` の共用**: `build_context(..., transport="google")` を新設しない。
3. **Vertex 既定モデル**: `default_model()` は API キーを要求するため、Vertex では必ず `default_vertex_model()`（`gemini-2.5-flash`）。
4. **モデルID検証**: `/api/ai/provider`・`/api/ide/model`・`/api/send` すべてで `google_ai.MODEL_ID_RE.fullmatch(model)`。
5. **プロバイダ判定**: Antigravity と API プロバイダの分岐は必ず `is_api_provider(st)`。
6. **URL リテラル**: コードブロック内にスキーム付きの URL 文字列を直接書かず、必ず `_https()` / `_http()` / `BASE` / `GOOGLE_TOKEN_URI` / `CLOUD_PLATFORM_SCOPE` を使う（Markdown の自動リンク化による破損防止）。
7. **`load(sid, readonly=True)` の戻り値は絶対に書き換えない**（キャッシュ本体の参照）。
8. **ロック順序**: `lock` → `google_inflight_guard` の一方向のみ。guard 保持中に `lock` を取らない。

---

## 3. `google_ai.py`

### 3.1 定数・URL ヘルパー・SDK 判定・ロック・キャッシュ（`google_ai.py:19-47` を置換）

> **重要（F-8）**: 既存 `EXCLUDE_MODEL_PARTS` を必ずこのブロックに含め、**その直後**に `VERTEX_EXCLUDE_MODEL_PARTS` を定義すること（順序を逆にすると `NameError`）。

```python
import base64
import copy
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from google import genai
    from google.genai import types as genai_types
    from google.genai import errors as genai_errors
    HAS_GENAI_SDK = True
except ImportError:
    genai = genai_types = genai_errors = None
    HAS_GENAI_SDK = False


def _https(host_prefix, path="", tld=".com"):
    """ドキュメントの自動リンク化を防ぎつつ正確な https URL を組み立てる"""
    return "https:" + "//" + host_prefix + tld + path


def _http(host_prefix, path="", tld=".internal"):
    return "http:" + "//" + host_prefix + tld + path


BASE = _https("generativelanguage.googleapis", "/v1beta")
GOOGLE_TOKEN_URI = _https("oauth2.googleapis", "/token")
CLOUD_PLATFORM_SCOPE = _https("www.googleapis", "/auth/cloud-platform")
GCE_METADATA_TOKEN_URL = _http(
    "metadata.google",
    "/computeMetadata/v1/instance/service-accounts/default/token",
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
ENV_FILE = os.environ.get("RPCHAT_ENV_FILE", os.path.join(ROOT, ".env"))
KEY_FILE = ENV_FILE
VERTEX_DEFAULT_LOCATION = "global"
VERTEX_DEFAULT_MODEL = "gemini-2.5-flash"

SAFETY_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_JAILBREAK",
]
# Vertex AI は HARM_CATEGORY_JAILBREAK 非対応（HTTP 400）のため先頭4カテゴリのみ
VERTEX_SAFETY_CATEGORIES = SAFETY_CATEGORIES[:4]

BLOCK_FINISH_REASONS = (
    "SAFETY", "PROHIBITED_CONTENT", "PUP_LIMITED_DISABLED",
    "BLOCKLIST", "SPII", "MODEL_ARMOR", "RECITATION", "IMAGE_SAFETY",
)

# 既存（Google AI Studio）用。互換のため無改造で維持する
EXCLUDE_MODEL_PARTS = (
    "-image", "embedding", "tts", "-live", "transcribe", "robotics", "lyria",
    "omni", "deep-research", "computer-use", "nano-banana",
)
# Vertex 専用（既存タプルを汚さない）
VERTEX_EXCLUDE_MODEL_PARTS = tuple(EXCLUDE_MODEL_PARTS) + ("gemini-1.0", "gemini-1.5")

VERTEX_CURATED_MODELS = [
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "inputTokenLimit": 1048576,
     "outputTokenLimit": 65536, "thinking": True},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "inputTokenLimit": 1048576,
     "outputTokenLimit": 65536, "thinking": True},
    {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite", "inputTokenLimit": 1048576,
     "outputTokenLimit": 65536, "thinking": True},
    {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "inputTokenLimit": 1048576,
     "outputTokenLimit": 8192, "thinking": False},
]

MODEL_ID_RE = re.compile(r"\A[A-Za-z0-9._@-]+\Z")
PROJECT_ID_RE = re.compile(r"\A(?:[a-z0-9.-]+:)?[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
LOCATION_RE = re.compile(r"\A[a-z0-9-]+\Z")

_env_lock = threading.RLock()

# 既存（Google AI Studio）キャッシュは既存テストが直接参照するため構造を変えない
_models_cache = {"ts": 0.0, "key_hint": "", "models": [], "error": ""}
_models_cache_by_key = {}
_models_lock = threading.Lock()

_vertex_auth_lock = threading.Lock()
_vertex_refresh_event = None
_vertex_token_cache = {
    "access_token": "", "expires_at": 0.0, "refreshed_at": 0.0,
    "fingerprint": "", "auth_type": "", "quota_project": "",
    "last_error": None, "error_at": 0.0,
}
_sdk_client_lock = threading.Lock()
_sdk_client_cache = {}
_sdk_broken = {"google": False, "vertex": False}


def _transport_mode():
    """RPCHAT_GEMINI_TRANSPORT = auto | sdk | rest（未設定は auto）"""
    try:
        raw = (os.environ.get("RPCHAT_GEMINI_TRANSPORT")
               or read_env_file().get("RPCHAT_GEMINI_TRANSPORT") or "auto")
    except Exception:
        raw = "auto"
    mode = str(raw).strip().lower()
    return mode if mode in ("auto", "sdk", "rest") else "auto"


def _should_use_sdk(provider="google"):
    if not HAS_GENAI_SDK:
        return False
    if os.environ.get("RPCHAT_GEMINI_SDK_BROKEN") == "1" or _sdk_broken.get(provider):
        return False
    return _transport_mode() in ("auto", "sdk")


def configured():
    """Google AI Studio の API キーが設定済みか"""
    key, _ = load_key()
    return bool(key)
```

### 3.2 `.env` の排他更新と Vertex 設定の原子的保存

- `save_key` / `delete_key`（`google_ai.py:111-180`）の**ファイル読み書き全体**を `with _env_lock:` で囲む（既存の挙動・書式は変更しない）。
- `save_vertex_config` は「全検証 → 両方の tmp 作成 → SA replace → .env replace（失敗時は SA を復元）」。

```python
def _stage_env_vars_tmp(updates, removals=(), path=None):
    """_env_lock 下で .env の更新内容を一時ファイルに書き出し (tmp, target) を返す"""
    target = path or ENV_FILE
    lines = []
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
        except OSError:
            lines = []

    updates = {k: str(v) for k, v in (updates or {}).items() if v is not None}
    remove_set = set(removals or ()) | set(updates.keys())
    seen, new_lines = set(), []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in updates and k not in seen:
            new_lines.append(f"{k}={updates[k]}\n")
            seen.add(k)
        elif k in remove_set:
            continue
        else:
            new_lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"{k}={v}\n")

    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    return tmp, target


def _update_env_vars(updates, removals=(), path=None):
    """_env_lock 下で .env を原子的に更新し os.environ にも反映する"""
    with _env_lock:
        tmp, target = _stage_env_vars_tmp(updates, removals=removals, path=path)
        os.replace(tmp, target)
        for k in removals or ():
            os.environ.pop(k, None)
        for k, v in (updates or {}).items():
            if v is not None:
                os.environ[k] = str(v)
```

```python
def parse_and_validate_sa_json(raw_text):
    """SA JSON を検証・正規化。秘密鍵はエラー文へ一切出さない"""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("サービスアカウントJSONが空です")
    cleaned = raw_text.encode("utf-8").decode("utf-8-sig").strip()
    try:
        info = json.loads(cleaned)
    except Exception:
        raise ValueError("サービスアカウントJSONの形式が不正です（JSONとして解釈できません）")
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise ValueError("type が 'service_account' のJSONキーを指定してください")
    for k in ("project_id", "client_email", "private_key", "token_uri"):
        if not str(info.get(k) or "").strip():
            raise ValueError(f"サービスアカウントJSONに {k} が含まれていません")
    pk = str(info["private_key"]).replace("\\n", "\n").strip()
    if "-----BEGIN " not in pk or "PRIVATE KEY-----" not in pk:
        raise ValueError("サービスアカウントJSONの private_key がPEM形式ではありません")
    try:
        from cryptography.hazmat.primitives import serialization
        serialization.load_pem_private_key(pk.encode("utf-8"), password=None)
    except ImportError:
        pass
    except Exception:
        raise ValueError("サービスアカウントJSONの秘密鍵を読み込めません（破損している可能性があります）")
    out = dict(info)
    out["private_key"] = pk
    return out


def _default_adc_path():
    env_adc = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if env_adc and os.path.isfile(env_adc):
        return env_adc
    if os.name == "nt":
        p = os.path.join(os.environ.get("APPDATA") or "", "gcloud",
                         "application_default_credentials.json")
    else:
        p = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    return p if os.path.isfile(p) else ""


def _mask_email(email):
    email = str(email or "").strip()
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) > 4:
        local = local[:2] + "…" + local[-2:]
    return f"{local}@{domain}"


def _vertex_sa_path(env_path=None):
    """.env の VERTEX_SA_JSON_PATH（実在時）か data/vertex_sa.json の実パス"""
    env_vars = read_env_file(env_path or ENV_FILE)
    cfg_path = (env_vars.get("VERTEX_SA_JSON_PATH")
                or os.environ.get("VERTEX_SA_JSON_PATH") or "").strip()
    if cfg_path and os.path.isfile(cfg_path):
        return cfg_path
    default_sa = os.path.join(DATA, "vertex_sa.json")
    return default_sa if os.path.isfile(default_sa) else ""


def load_vertex_config(env_path=None):
    """.env -> os.environ -> SA JSON の順で解決。ADC の quota_project_id は自動採用しない"""
    env_vars = read_env_file(env_path or ENV_FILE)
    project_id = (env_vars.get("VERTEX_PROJECT_ID")
                  or os.environ.get("VERTEX_PROJECT_ID") or "").strip()
    location = (env_vars.get("VERTEX_LOCATION") or os.environ.get("VERTEX_LOCATION")
                or VERTEX_DEFAULT_LOCATION).strip() or VERTEX_DEFAULT_LOCATION
    sa_path = _vertex_sa_path(env_path)

    auth_type, email_hint, fp_src, adc_project_hint = "", "", "", ""
    if sa_path:
        try:
            with open(sa_path, "r", encoding="utf-8-sig") as f:
                raw = f.read()
            info = json.loads(raw)
            if isinstance(info, dict) and info.get("type") == "service_account":
                auth_type = "service_account"
                email_hint = _mask_email(info.get("client_email") or "")
                if not project_id:
                    project_id = str(info.get("project_id") or "").strip()
                fp_src = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        except Exception:
            auth_type = ""
    if not auth_type:
        adc = _default_adc_path()
        if adc:
            try:
                with open(adc, "r", encoding="utf-8-sig") as f:
                    raw = f.read()
                info = json.loads(raw)
                if isinstance(info, dict) and info.get("type") in ("authorized_user", "service_account"):
                    auth_type = "adc"
                    email_hint = _mask_email(info.get("client_email") or "ADC")
                    adc_project_hint = str(info.get("project_id")
                                           or info.get("quota_project_id") or "").strip()
                    fp_src = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            except Exception:
                pass

    source = ".env" if ("VERTEX_PROJECT_ID" in env_vars or "VERTEX_LOCATION" in env_vars
                        or "VERTEX_SA_JSON_PATH" in env_vars) else ("env" if project_id else "")
    return {
        "configured": bool(auth_type and project_id
                           and PROJECT_ID_RE.fullmatch(project_id)
                           and LOCATION_RE.fullmatch(location)),
        "auth_type": auth_type,
        "project_id": project_id,
        "adc_project_hint": adc_project_hint,
        "location": location,
        "sa_basename": os.path.basename(sa_path) if sa_path else "",
        "client_email_hint": email_hint,
        "source": source,
        "fingerprint": f"{auth_type}:{fp_src}:{project_id}:{location}",
        "sdk_available": HAS_GENAI_SDK,
    }


def vertex_configured():
    return bool(load_vertex_config().get("configured"))


def _has_any_vertex_auth(sa_json=None, env_path=None):
    if sa_json is not None and str(sa_json).strip():
        return True
    if _vertex_sa_path(env_path):
        return True
    return bool(load_vertex_config(env_path).get("auth_type"))


def save_vertex_config(sa_json=None, project_id=None, location=None, env_path=None):
    """全検証通過後に tmp を2つ作り、SA -> .env の順に replace。.env 失敗時は SA を復元する"""
    with _env_lock:
        if not _has_any_vertex_auth(sa_json, env_path):
            raise ValueError("認証情報が見つかりません。サービスアカウントJSONを選択するか貼り付けてください"
                             "（または gcloud auth application-default login を実行してください）")

        sa_info = None
        if sa_json is not None and str(sa_json).strip():
            sa_info = parse_and_validate_sa_json(str(sa_json))

        cur = load_vertex_config(env_path)
        proj = (str(project_id or "").strip()
                or (str(sa_info.get("project_id") or "").strip() if sa_info else "")
                or cur.get("project_id") or cur.get("adc_project_hint") or "")
        loc = (str(location or "").strip() or cur.get("location") or VERTEX_DEFAULT_LOCATION)
        if not proj or not PROJECT_ID_RE.fullmatch(proj):
            raise ValueError("プロジェクトIDの形式が不正です（英小文字で始まる英小文字・数字・ハイフン、6〜30文字）")
        if not loc or not LOCATION_RE.fullmatch(loc):
            raise ValueError("リージョンの形式が不正です（例: global, us-central1, asia-northeast1）")

        default_sa = os.path.join(DATA, "vertex_sa.json")
        sa_tmp = env_tmp = None
        old_sa_bytes = None
        had_old_sa = os.path.isfile(default_sa)
        removals = ("VERTEX_SA_JSON_PATH",) if sa_info is not None else ()
        updates = {"VERTEX_PROJECT_ID": proj, "VERTEX_LOCATION": loc}

        try:
            if sa_info is not None:
                os.makedirs(DATA, exist_ok=True)
                if had_old_sa:
                    try:
                        with open(default_sa, "rb") as f:
                            old_sa_bytes = f.read()
                    except OSError:
                        old_sa_bytes = None
                sa_tmp = f"{default_sa}.{os.getpid()}.{threading.get_ident()}.tmp"
                with open(sa_tmp, "w", encoding="utf-8") as f:
                    json.dump(sa_info, f, ensure_ascii=False, indent=2)
                try:
                    os.chmod(sa_tmp, 0o600)
                except OSError:
                    pass
            env_tmp, env_target = _stage_env_vars_tmp(updates, removals=removals, path=env_path)
            if sa_tmp is not None:
                os.replace(sa_tmp, default_sa)
                sa_tmp = None
            try:
                os.replace(env_tmp, env_target)
            except Exception:
                # F-10: .env 更新に失敗したら SA を元に戻す
                if sa_info is not None:
                    try:
                        if old_sa_bytes is not None:
                            with open(default_sa, "wb") as f:
                                f.write(old_sa_bytes)
                        elif os.path.exists(default_sa):
                            os.remove(default_sa)
                    except OSError:
                        pass
                raise
            env_tmp = None
            for k in removals:
                os.environ.pop(k, None)
            for k, v in updates.items():
                os.environ[k] = v
        finally:
            for p in (sa_tmp, env_tmp):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        invalidate_vertex_token()
        invalidate_models_cache()
        return load_vertex_config(env_path)


def delete_vertex_config(env_path=None):
    with _env_lock:
        default_sa = os.path.join(DATA, "vertex_sa.json")
        if os.path.exists(default_sa):
            try:
                os.remove(default_sa)
            except OSError:
                pass
        _update_env_vars({}, removals=("VERTEX_PROJECT_ID", "VERTEX_LOCATION", "VERTEX_SA_JSON_PATH"),
                         path=env_path)
        invalidate_vertex_token()
        invalidate_models_cache()
```

### 3.3 アクセストークン（Singleflight ＋ 失敗3秒キャッシュ）

```python
def invalidate_vertex_token():
    with _vertex_auth_lock:
        _vertex_token_cache.update(
            access_token="", expires_at=0.0, refreshed_at=0.0,
            fingerprint="", auth_type="", quota_project="",
            last_error=None, error_at=0.0,
        )
    with _sdk_client_lock:
        _sdk_client_cache.clear()
        _sdk_broken["vertex"] = False


def _token_http_post(url, fields, timeout=15):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _jwt_assertion(sa_info, scope, token_uri, now_ts):
    """cryptography で RS256 署名した JWT を生成"""
    header = {"alg": "RS256", "typ": "JWT"}
    if sa_info.get("private_key_id"):
        header["kid"] = sa_info["private_key_id"]
    claims = {
        "iss": sa_info["client_email"], "scope": scope, "aud": token_uri,
        "iat": int(now_ts) - 60, "exp": int(now_ts) + 3600,
    }

    def b64(d):
        return base64.urlsafe_b64encode(
            json.dumps(d, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")

    signing_input = f"{b64(header)}.{b64(claims)}".encode("ascii")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(sa_info["private_key"].encode("utf-8"), password=None)
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


def _google_auth_request():
    from google.auth.transport.requests import Request
    return Request()


def _is_transient_auth_exc(e):
    """トークン取得時の例外が一時的な通信障害か"""
    if isinstance(e, urllib.error.HTTPError):
        return _retryable(e.code)
    if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
        return True
    msg = str(e or "").lower()
    if any(k in msg for k in ("invalid_grant", "invalid_client", "unauthorized",
                              "forbidden", "private_key", "pem")):
        return False
    return any(k in msg for k in ("timed out", "timeout", "connection",
                                  "503", "502", "500", "429", "reset"))


def _fetch_vertex_token_unlocked(scope, timeout=15):
    """ロック外で実行する実際のトークン取得 I/O。戻りは (token, auth_type, quota, ttl)"""
    token, auth_type, quota = "", "", ""
    ttl = 3600
    info = None
    sa_path = _vertex_sa_path()
    if sa_path:
        try:
            with open(sa_path, "r", encoding="utf-8-sig") as f:
                info = json.loads(f.read())
        except Exception as e:
            raise GoogleAPIError(f"サービスアカウントJSONを読み込めません: {e}", retryable=False)

    # 1) google-auth
    if info is not None:
        try:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_info(info, scopes=[scope])
            creds.refresh(_google_auth_request())
            token, auth_type = creds.token, "service_account"
            if getattr(creds, "expiry", None):
                ttl = max(60, int(creds.expiry.timestamp() - time.time()))
        except ImportError:
            pass
        except Exception as e:
            raise GoogleAPIError(f"サービスアカウントの認証に失敗しました: {e}",
                                 retryable=_is_transient_auth_exc(e))

    # 2) cryptography で JWT 署名
    if not token and info is not None:
        try:
            token_uri = info.get("token_uri") or GOOGLE_TOKEN_URI
            assertion = _jwt_assertion(info, scope, token_uri, time.time())
            d = _token_http_post(token_uri, {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }, timeout=timeout)
            token, auth_type = d["access_token"], "service_account"
            ttl = int(d.get("expires_in") or 3600)
        except ImportError:
            raise GoogleAPIError("サービスアカウント認証には google-auth か cryptography が必要です"
                                 "（start.bat で自動インストールされます）", retryable=False)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            raise GoogleAPIError(f"アクセストークンを取得できません (HTTP {e.code}): {raw[:200]}",
                                 status=e.code, retryable=_retryable(e.code))
        except Exception as e:
            raise GoogleAPIError(f"アクセストークンを取得できません: {e}",
                                 retryable=_is_transient_auth_exc(e))

    # 3) ADC（authorized_user / service_account / GCE メタデータ）
    if not token:
        adc = _default_adc_path()
        if adc:
            try:
                with open(adc, "r", encoding="utf-8-sig") as f:
                    a = json.loads(f.read())
            except Exception as e:
                raise GoogleAPIError(f"ADC認証ファイルを読み込めません: {e}", retryable=False)
            try:
                if a.get("type") == "authorized_user" and a.get("refresh_token"):
                    d = _token_http_post(GOOGLE_TOKEN_URI, {
                        "grant_type": "refresh_token",
                        "client_id": a.get("client_id"),
                        "client_secret": a.get("client_secret"),
                        "refresh_token": a.get("refresh_token"),
                    }, timeout=timeout)
                    token, auth_type = d["access_token"], "adc"
                    quota = str(a.get("quota_project_id") or "")
                    ttl = int(d.get("expires_in") or 3600)
                elif a.get("type") == "service_account":
                    token_uri = a.get("token_uri") or GOOGLE_TOKEN_URI
                    assertion = _jwt_assertion(a, scope, token_uri, time.time())
                    d = _token_http_post(token_uri, {
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    }, timeout=timeout)
                    token, auth_type = d["access_token"], "adc"
                    ttl = int(d.get("expires_in") or 3600)
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                raise GoogleAPIError(
                    f"ADCの認証トークン取得に失敗しました（gcloud auth application-default login を再実行してください）: {raw[:200]}",
                    status=e.code, retryable=_retryable(e.code),
                )
            except Exception as e:
                raise GoogleAPIError(f"ADC認証トークンの取得に失敗しました: {e}",
                                     retryable=_is_transient_auth_exc(e))
        if not token:
            try:
                req = urllib.request.Request(GCE_METADATA_TOKEN_URL,
                                             headers={"Metadata-Flavor": "Google"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    d = json.loads(r.read().decode("utf-8"))
                    token, auth_type = d["access_token"], "adc"
                    ttl = int(d.get("expires_in") or 3600)
            except Exception:
                pass

    if not token:
        raise GoogleAPIError("Vertex AI の認証情報が見つかりません（サービスアカウントJSON か ADC を設定してください）",
                             retryable=False)
    return token, auth_type, quota, ttl


def get_vertex_access_token(force_refresh=False, timeout=15):
    """Singleflight（通信中はロックを持たない）＋ 失敗3秒キャッシュ。戻りは (token, auth_type, quota)"""
    global _vertex_refresh_event
    scope = CLOUD_PLATFORM_SCOPE
    fp = load_vertex_config().get("fingerprint") or ""
    waited = False

    while True:
        with _vertex_auth_lock:
            c = _vertex_token_cache
            now_ts = time.time()
            if c["access_token"] and c["fingerprint"] == fp and c["expires_at"] - 300 > now_ts:
                if not force_refresh or (now_ts - c.get("refreshed_at", 0.0) < 5.0):
                    return c["access_token"], c["auth_type"], c["quota_project"]
            if (waited or not force_refresh) and c.get("last_error") is not None \
                    and c["fingerprint"] == fp:
                if now_ts - c.get("error_at", 0.0) < 3.0:
                    raise c["last_error"]
            if _vertex_refresh_event is None:
                my_event = threading.Event()
                _vertex_refresh_event = my_event
                break
            wait_event = _vertex_refresh_event
        wait_event.wait(timeout=max(1.0, float(timeout)))
        waited = True
        force_refresh = False

    try:
        token, auth_type, quota, ttl = _fetch_vertex_token_unlocked(scope, timeout=timeout)
        with _vertex_auth_lock:
            now_ts = time.time()
            _vertex_token_cache.update(
                access_token=token, expires_at=now_ts + ttl, refreshed_at=now_ts,
                fingerprint=fp, auth_type=auth_type, quota_project=quota,
                last_error=None, error_at=0.0,
            )
            return token, auth_type, quota
    except GoogleAPIError as e:
        with _vertex_auth_lock:
            _vertex_token_cache.update(fingerprint=fp, last_error=e, error_at=time.time())
        raise
    finally:
        with _vertex_auth_lock:
            if _vertex_refresh_event is my_event:
                _vertex_refresh_event = None
            my_event.set()
```

### 3.4 エンドポイント・SDK ラッパー・`list_models`・`request`

```python
class _SdkFallbackToRest(Exception):
    """SDK 変換で情報が失われる payload を REST へフォールバックさせる内部例外"""


def default_vertex_model():
    return VERTEX_DEFAULT_MODEL


def _vertex_host_prefix(location):
    """F-4: ホスト名の前段（.com は _https が付ける）"""
    loc = (location or VERTEX_DEFAULT_LOCATION).strip()
    return "aiplatform.googleapis" if loc == "global" else f"{loc}-aiplatform.googleapis"


def vertex_host(location):
    return _vertex_host_prefix(location) + ".com"


def vertex_generate_url(project_id, location, model):
    loc = (location or VERTEX_DEFAULT_LOCATION).strip()
    proj = urllib.parse.quote((project_id or "").strip(), safe="")
    loc_q = urllib.parse.quote(loc, safe="")
    mdl = urllib.parse.quote((model or "").strip(), safe="@-._")
    return _https(_vertex_host_prefix(loc),
                  f"/v1beta1/projects/{proj}/locations/{loc_q}"
                  f"/publishers/google/models/{mdl}:generateContent")


def invalidate_models_cache():
    with _models_lock:
        _models_cache.update(ts=0.0, key_hint="", models=[], error="")
        _models_cache_by_key.clear()


def last_models_error(provider="vertex"):
    """現在の設定 fingerprint に対応するモデル一覧エラーのみを返す"""
    if provider == "vertex":
        cfg = load_vertex_config()
        ck = ("vertex", cfg["fingerprint"], cfg["project_id"], cfg["location"])
        with _models_lock:
            return (_models_cache_by_key.get(ck) or {}).get("error") or ""
    with _models_lock:
        return _models_cache.get("error") or ""


def _sdk_client(provider, cfg=None, api_key=""):
    if not _should_use_sdk(provider):
        return None
    if provider == "vertex":
        key = ("vertex", cfg["fingerprint"], cfg["project_id"], cfg["location"])
    else:
        key = ("google", (api_key or "")[-8:])
    with _sdk_client_lock:
        cli = _sdk_client_cache.get(key)
        if cli is not None:
            return cli
        try:
            if provider == "vertex":
                creds = None
                try:
                    from google.oauth2 import service_account
                    sa = _vertex_sa_path()
                    if sa and os.path.isfile(sa):
                        creds = service_account.Credentials.from_service_account_file(
                            sa, scopes=[CLOUD_PLATFORM_SCOPE])
                except Exception:
                    creds = None
                cli = genai.Client(vertexai=True, project=cfg["project_id"],
                                   location=cfg["location"], credentials=creds)
            else:
                cli = genai.Client(api_key=api_key)
        except (TypeError, AttributeError, ImportError, ValueError) as e:
            _sdk_broken[provider] = True
            print(f"[google] genai.Client({provider}) 初期化非互換のため REST に切り替えます: {e}")
            return None
        except Exception as e:
            print(f"[google] genai.Client({provider}) 作成失敗（今回は REST を使用）: {e}")
            return None
        for old_k in [k for k in _sdk_client_cache if k[0] == provider]:
            _sdk_client_cache.pop(old_k, None)
        _sdk_client_cache[key] = cli
        return cli


def _sdk_response_to_dict(resp):
    """SDK 応答を REST(camelCase) 互換 dict に変換する。snake_case のみの不完全 dict は弾く"""
    for attr in ("to_json_dict", "model_dump"):
        fn = getattr(resp, attr, None)
        if not callable(fn):
            continue
        for kwargs in ({"by_alias": True, "exclude_none": True}, {"by_alias": True}, {}):
            try:
                d = fn(**kwargs)
                if not isinstance(d, dict):
                    continue
                cands = d.get("candidates")
                if isinstance(cands, list) and cands:
                    c0 = cands[0] if isinstance(cands[0], dict) else {}
                    if "finish_reason" in c0 and "finishReason" not in c0:
                        continue
                    content = c0.get("content")
                    if isinstance(content, dict):
                        parts = content.get("parts") or []
                        if any(isinstance(p, dict) and ("function_call" in p or "inline_data" in p)
                               for p in parts):
                            continue
                    if "content" in c0 or "finishReason" in c0:
                        return d
                elif "promptFeedback" in d and "prompt_feedback" not in d:
                    return d
            except TypeError:
                continue
            except Exception:
                break
    return None


def _sdk_payload_to_kwargs(payload, timeout=180):
    """REST payload を SDK 引数へ変換。署名・未知キー・複合 Part があれば REST へフォールバック"""
    t = genai_types
    known_part_keys = {"text", "functionCall", "functionResponse", "inlineData"}

    def to_part(p):
        if not isinstance(p, dict) or not p:
            raise _SdkFallbackToRest("invalid part dict")
        if set(p.keys()) - known_part_keys or len(p) != 1:
            raise _SdkFallbackToRest("complex or signed part requires REST")
        if isinstance(p.get("text"), str):
            return t.Part(text=p["text"])
        if isinstance(p.get("functionCall"), dict):
            fc = p["functionCall"]
            if set(fc.keys()) - {"name", "args"}:
                raise _SdkFallbackToRest("functionCall has extra fields")
            return t.Part(function_call=t.FunctionCall(name=fc.get("name"), args=fc.get("args") or {}))
        if isinstance(p.get("functionResponse"), dict):
            fr = p["functionResponse"]
            if set(fr.keys()) - {"name", "response"}:
                raise _SdkFallbackToRest("functionResponse has extra fields")
            return t.Part(function_response=t.FunctionResponse(name=fr.get("name"),
                                                              response=fr.get("response") or {}))
        if isinstance(p.get("inlineData"), dict):
            inline = p["inlineData"]
            raw = base64.b64decode(inline.get("data") or "")
            mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            return t.Part.from_bytes(data=raw, mime_type=mime)
        raise _SdkFallbackToRest("unsupported part structure")

    contents = []
    for c in payload.get("contents") or []:
        raw_parts = c.get("parts") or []
        if not raw_parts:
            raise _SdkFallbackToRest("empty parts in content")
        contents.append(t.Content(role=c.get("role") or "user",
                                  parts=[to_part(p) for p in raw_parts]))

    gc = dict(payload.get("generationConfig") or {})
    cfg = {}
    if payload.get("systemInstruction"):
        sys_parts = [p.get("text") for p in (payload["systemInstruction"].get("parts") or [])
                     if isinstance(p.get("text"), str)]
        if sys_parts:
            cfg["system_instruction"] = "\n".join(sys_parts)
    if gc.get("maxOutputTokens") is not None:
        cfg["max_output_tokens"] = int(gc["maxOutputTokens"])
    if gc.get("responseMimeType"):
        cfg["response_mime_type"] = gc["responseMimeType"]
    if gc.get("responseSchema"):
        cfg["response_schema"] = gc["responseSchema"]
    if gc.get("thinkingConfig"):
        tc = gc["thinkingConfig"]
        cfg["thinking_config"] = t.ThinkingConfig(thinking_budget=int(tc.get("thinkingBudget") or 0))
    temp = gc.get("temperature") if "temperature" in gc else payload.get("temperature")
    if temp is not None:
        cfg["temperature"] = float(temp)
    if gc.get("topP") is not None:
        cfg["top_p"] = float(gc["topP"])
    if gc.get("topK") is not None:
        cfg["top_k"] = int(gc["topK"])
    if gc.get("candidateCount") is not None:
        cfg["candidate_count"] = int(gc["candidateCount"])
    if gc.get("stopSequences") is not None:
        cfg["stop_sequences"] = list(gc["stopSequences"])
    if gc.get("presencePenalty") is not None:
        cfg["presence_penalty"] = float(gc["presencePenalty"])
    if gc.get("frequencyPenalty") is not None:
        cfg["frequency_penalty"] = float(gc["frequencyPenalty"])

    safety = payload.get("safetySettings")
    if safety:
        cfg["safety_settings"] = [t.SafetySetting(category=s["category"], threshold=s["threshold"])
                                  for s in safety if s.get("category") and s.get("threshold")]

    tools = payload.get("tools")
    if tools:
        decls = []
        for tool in tools:
            for d in tool.get("functionDeclarations") or []:
                decls.append(t.FunctionDeclaration(
                    name=d.get("name"),
                    description=d.get("description") or "",
                    parameters=d.get("parameters") or {"type": "OBJECT", "properties": {}},
                ))
        if decls:
            cfg["tools"] = [t.Tool(function_declarations=decls)]
            cfg["automatic_function_calling"] = t.AutomaticFunctionCallingConfig(disable=True)
    tc = ((payload.get("toolConfig") or {}).get("functionCallingConfig") or {})
    if tc.get("mode"):
        cfg["tool_config"] = t.ToolConfig(function_calling_config=t.FunctionCallingConfig(
            mode=tc["mode"], allowed_function_names=tc.get("allowedFunctionNames") or None))

    if timeout and hasattr(t, "HttpOptions"):
        try:
            cfg["http_options"] = t.HttpOptions(timeout=int(timeout * 1000))
        except Exception:
            pass

    return contents, (t.GenerateContentConfig(**cfg) if cfg else None)


def _sdk_generate(model, payload, provider, timeout=180):
    """SDK 経由の generateContent。戻りは (status, rest_compat_dict) または (None, None)"""
    cfg = load_vertex_config() if provider == "vertex" else None
    api_key = "" if provider == "vertex" else load_key()[0]
    cli = _sdk_client(provider, cfg, api_key)
    if cli is None:
        return None, None
    try:
        contents, config = _sdk_payload_to_kwargs(payload, timeout=timeout)
        resp = cli.models.generate_content(model=model, contents=contents, config=config)
        d = _sdk_response_to_dict(resp)
        if not d:
            return None, None
        return 200, d
    except _SdkFallbackToRest:
        return None, None
    except genai_errors.APIError as e:
        code = int(getattr(e, "code", 0) or 0)
        raise GoogleAPIError(_error_message(code, str(e))[0], status=code,
                             code=str(getattr(e, "status", "") or ""), retryable=_retryable(code))
    except Exception as e:
        print(f"[google] SDK 呼び出しから REST へフォールバックします: {e}")
        return None, None


def list_models(refresh=False, timeout=30, *, provider="google"):
    if provider == "vertex":
        cfg = load_vertex_config()
        if not cfg.get("configured"):
            raise GoogleAPIError("Vertex AIの認証またはプロジェクトIDが未設定です", code="NO_VERTEX_CRED")
        ck = ("vertex", cfg["fingerprint"], cfg["project_id"], cfg["location"])
        with _models_lock:
            cached = _models_cache_by_key.get(ck)
            if not refresh and cached and cached.get("models") and time.time() - cached["ts"] < 600:
                return list(cached["models"])

        by_id = {m["id"]: dict(m) for m in VERTEX_CURATED_MODELS}
        err = ""
        rows_raw = []
        if _should_use_sdk("vertex"):
            try:
                cli = _sdk_client("vertex", cfg)
                if cli is not None:
                    for m in cli.models.list():
                        rows_raw.append({
                            "name": getattr(m, "name", "") or "",
                            "displayName": getattr(m, "display_name", "") or "",
                            "inputTokenLimit": getattr(m, "input_token_limit", None),
                            "outputTokenLimit": getattr(m, "output_token_limit", None),
                        })
            except Exception as e:
                err = f"SDK のモデル一覧を読めません: {e}"
        if not rows_raw:
            try:
                tok, auth_type, quota = get_vertex_access_token(timeout=timeout)
                headers = {"Authorization": f"Bearer {tok}"}
                if auth_type == "adc":
                    headers["x-goog-user-project"] = quota or cfg["project_id"]
                req = urllib.request.Request(
                    _https(_vertex_host_prefix(cfg["location"]),
                           "/v1beta1/publishers/google/models?pageSize=1000"),
                    headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                rows_raw = data.get("publisherModels") or data.get("models") or []
                err = ""
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                err = _error_message(e.code, raw)[0]
            except Exception as e:
                err = f"モデル一覧を取得できません: {e}"

        for m in rows_raw or []:
            name = str(m.get("name") or "")
            mid = name.rsplit("/", 1)[-1] if "/" in name else name
            if not mid:
                continue
            low = mid.lower()
            if not (low.startswith("gemini-") or low.startswith("gemma-")):
                continue
            if any(p in low for p in VERTEX_EXCLUDE_MODEL_PARTS):
                continue
            methods = m.get("supportedGenerationMethods")
            if isinstance(methods, list) and methods and "generateContent" not in methods:
                continue
            cur = by_id.get(mid) or {}
            by_id[mid] = {
                "id": mid,
                "label": m.get("displayName") or cur.get("label") or mid,
                "inputTokenLimit": m.get("inputTokenLimit") or cur.get("inputTokenLimit") or 1048576,
                "outputTokenLimit": m.get("outputTokenLimit") or cur.get("outputTokenLimit") or 65536,
                "thinking": bool(m["thinking"] if "thinking" in m else ("2.5" in low or "3." in low)),
            }
        rows = sorted(by_id.values(), key=lambda x: x["id"])
        with _models_lock:
            _models_cache_by_key[ck] = {"ts": time.time(), "models": list(rows), "error": err}
        return rows

    # provider == "google"（既存処理そのまま）
    key, _ = load_key()
    if not key:
        raise GoogleAPIError("APIキーが未設定です", code="NO_KEY")
    hint = key_hint(key)
    with _models_lock:
        if (not refresh and _models_cache["models"] and _models_cache["key_hint"] == hint
                and time.time() - _models_cache["ts"] < 600):
            return list(_models_cache["models"])
    req = urllib.request.Request(f"{BASE}/models?pageSize=1000", headers={"x-goog-api-key": key})
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
        if "generateContent" not in [str(x) for x in (m.get("supportedGenerationMethods") or [])]:
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


def request(model, payload, timeout=180, *, provider="google"):
    """REST でも SDK でも同じ (status, camelCase dict) を返す"""
    if provider == "vertex":
        cfg = load_vertex_config()
        if not cfg.get("configured"):
            raise GoogleAPIError("Vertex AIの認証またはプロジェクトIDが未設定です", code="NO_VERTEX_CRED")

    if _should_use_sdk(provider):
        status, data = _sdk_generate(model, payload, provider, timeout=timeout)
        if status is not None:
            return status, data

    if provider == "vertex":
        url = vertex_generate_url(cfg["project_id"], cfg["location"], model)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for attempt in range(2):
            tok, auth_type, quota = get_vertex_access_token(force_refresh=(attempt > 0), timeout=timeout)
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}
            if auth_type == "adc":
                headers["x-goog-user-project"] = quota or cfg["project_id"]
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status, json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                if e.code == 401 and attempt == 0:
                    invalidate_vertex_token()
                    continue
                msg, code = _error_message(e.code, raw)
                if e.code == 404:
                    msg += (f"（モデル '{model}' がリージョン '{cfg['location']}' で利用できない可能性があります。"
                            "'global' など別リージョンか別モデルをお試しください）")
                raise GoogleAPIError(msg, status=e.code, code=code, retryable=_retryable(e.code))
            except GoogleAPIError:
                raise
            except Exception as e:
                raise GoogleAPIError(f"Vertex AI に接続できません: {e}", retryable=True)

    # provider == "google"（既存実装そのまま）
    key, _ = load_key()
    if not key:
        raise GoogleAPIError("APIキーが未設定です", code="NO_KEY")
    url = f"{BASE}/models/{urllib.parse.quote(model, safe='')}:generateContent"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        msg, code = _error_message(e.code, raw)
        raise GoogleAPIError(msg, status=e.code, code=code, retryable=_retryable(e.code))
    except GoogleAPIError:
        raise
    except Exception as e:
        raise GoogleAPIError(f"Google API に接続できません: {e}", retryable=True)


def _retryable(status):
    """既存判定を維持（401 は含めない）"""
    s = int(status or 0)
    return s in (408, 429) or 500 <= s <= 599
```

### 3.5 `generate()` の差分改修

**現行 `generate()` のロジック・戻り値キーは一切変えない**（`model` 未設定チェック、既定 `system` 文、1段目専用 `system` 文、画像時 `fetch` 抑止、`extract_function_call` のタプル受け取り、`model_content` そのまま送信、空応答/`MAX_TOKENS` の扱い、`fetch_called` / `elapsed_ms` / `first_status` / `second_status`）。適用するのは以下 5 点のみ。

```python
# Safety しきい値の段階（F-7: Vertex に OFF は絶対送らない。除去より緩い値へ落とす）
_SAFETY_LADDER = {
    "vertex": ("BLOCK_NONE", "BLOCK_ONLY_HIGH", None),   # None = safetySettings を外す
    "google": ("OFF", "BLOCK_NONE", None),
}


def _safety_settings(on, provider="google", threshold=None):
    if not on:
        return None
    cats = VERTEX_SAFETY_CATEGORIES if provider == "vertex" else SAFETY_CATEGORIES
    if provider == "vertex":
        th = "BLOCK_NONE"
    else:
        th = threshold or "OFF"
    return [{"category": c, "threshold": th} for c in cats]


def _is_safety_400(msg):
    low = str(msg or "").lower()
    return any(w in low for w in ("safety", "threshold", "harm_category",
                                  "harmcategory", "block_none"))


def _is_tool_400(msg):
    """Function Calling 起因の 400 のみ判定（'tool' 単体は含めない）"""
    low = str(msg or "").lower()
    return any(w in low for w in (
        "functionresponse", "function_response", "functioncall", "function_call",
        "functiondeclarations", "function_declarations", "toolconfig", "tool_config",
    ))


def _is_thinking_400(msg):
    low = str(msg or "").lower()
    return any(w in low for w in ("thinking", "thinking_config", "thinkingconfig",
                                  "thinking_budget", "thinkingbudget"))


def _step1_generation_config(model):
    """1段目（fetch_prompt 呼び出し）の generationConfig"""
    low = str(model or "").lower()
    if "2.5-flash" in low:
        return {"maxOutputTokens": 1024, "thinkingConfig": {"thinkingBudget": 0}}
    if ("2.5" in low) or ("3." in low):
        return {"maxOutputTokens": 2048, "thinkingConfig": {"thinkingBudget": 128}}
    return {"maxOutputTokens": 256}


def _step2_generation_config(model, max_output_tokens, provider="google"):
    """2段目・直接生成。Vertex の思考モデルは思考で JSON が切れないよう下限を上げる"""
    tok = int(max_output_tokens or 8192)
    low = str(model or "").lower()
    if provider == "vertex" and (("2.5" in low) or ("3." in low)):
        tok = max(tok, 16384)
    return {"maxOutputTokens": tok, "responseMimeType": "application/json"}


def _next_safety_payload(provider, cur_payload):
    """400 時の次の safetySettings を持つ payload を返す。
    現在値が段階に無い場合は先頭（=プロバイダ既定の最緩値）へ、既に最終段なら None（=これ以上不可）。
    """
    ladder = _SAFETY_LADDER.get(provider, _SAFETY_LADDER["google"])
    cur = (cur_payload.get("safetySettings") or [])
    cur_th = cur[0].get("threshold") if cur and isinstance(cur[0], dict) else None
    if cur_th in ladder:
        idx = ladder.index(cur_th)
    else:
        idx = -1                       # 未知の値（外部 .env 等）は最緩値から始め直す
    if idx >= len(ladder) - 1:
        return None                    # これ以上フォールバックできない
    nxt = ladder[idx + 1]
    out = copy.deepcopy(cur_payload)
    if nxt is None:
        out.pop("safetySettings", None)
    else:
        out["safetySettings"] = _safety_settings(True, provider=provider, threshold=nxt)
    return out


def _request_with_fallbacks(model, payload, timeout, provider, safety_off):
    """初回＋最大2回の 400 フォールバック（thinking / safety）＝最大3リクエスト"""
    cur_payload = payload
    for _ in range(2):
        try:
            return request(model, cur_payload, timeout=timeout, provider=provider)
        except GoogleAPIError as e:
            if e.status != 400:
                raise
            err_str = str(e)
            next_payload = None
            if _is_thinking_400(err_str) and isinstance(cur_payload.get("generationConfig"), dict) \
                    and "thinkingConfig" in cur_payload["generationConfig"]:
                next_payload = copy.deepcopy(cur_payload)
                next_payload["generationConfig"].pop("thinkingConfig", None)
            elif safety_off and _is_safety_400(err_str) and cur_payload.get("safetySettings"):
                next_payload = _next_safety_payload(provider, cur_payload)
            if next_payload is None:
                raise
            cur_payload = next_payload
    return request(model, cur_payload, timeout=timeout, provider=provider)
```

`generate()` への適用（これ以外は変更しない）:

1. シグネチャに `provider="google"` を追加
   `def generate(model, prompt, system="", *, safety_off=True, max_output_tokens=8192, fetch=True, images=None, timeout=180, fetch_name="fetch_prompt", provider="google"):`
2. Safety 設定を `_safety_settings(safety_off, provider=provider)` に置換。
3. 1段目の `generationConfig` を `_step1_generation_config(model)`、2段目と直接生成を `_step2_generation_config(model, max_output_tokens, provider=provider)` に置換。
4. `request(model, ...)` を `_request_with_fallbacks(model, ..., timeout, provider, safety_off)` に置換。
5. `provider == "vertex"` のとき、1段目で `fc` が取れなかった場合（`BLOCK_FINISH_REASONS`／`block_reason` の場合は除く）または2段目で `e.status == 400 and _is_tool_400(str(e))` の場合は、`fetch=False` 相当の直接生成へフォールバックし `fetch_called=False` で返す（`first_status` / `elapsed_ms` は維持）。

---

## 4. `server.py`

### 4.1 設定・ヘルパー

1. `DEFAULT_SETTING`（492行付近）に `"vertex_model": ""` と `"vertex_choices_model": ""`。
2. `NO_IDE`（527行付近）に `"vertex_choices_model": ""`。
3. `merge_defaults`（926行付近）:
```python
if out.get("ai_provider") not in ("antigravity", "google", "vertex"):
    out["ai_provider"] = "antigravity"
```
4. ヘルパー定義（`_google_model` の手前に置く）:
```python
def is_api_provider(st):
    """Google AI Studio と Vertex AI は同じ API ワーカー / outbox 経路を使う"""
    return isinstance(st, dict) and st.get("ai_provider") in ("google", "vertex")


def api_provider_of(st):
    """必ず is_api_provider(st) が True の文脈で呼ぶこと"""
    p = (st or {}).get("ai_provider")
    return p if p in ("google", "vertex") else "google"


def active_api_model(st, task_type="user"):
    """本文・選択肢・推敲のモデル解決（旧 _google_model）"""
    st = st or {}
    if st.get("ai_provider") == "vertex":
        if task_type == "choices" and (st.get("vertex_choices_model") or "").strip():
            return st["vertex_choices_model"].strip()
        return (st.get("vertex_model") or "").strip() or google_ai.default_vertex_model()
    if task_type == "choices" and (st.get("google_choices_model") or "").strip():
        return st["google_choices_model"].strip()
    model = (st.get("google_model") or "").strip()
    if model:
        return model
    try:
        return google_ai.default_model()
    except Exception:
        return ""


# 既存テスト（test_google_parallel_choices.py:39）との互換維持
_google_model = active_api_model


def _api_model_setting(st, task_type="user"):
    """F-5: 設定値だけを比較する（ネットワークを叩かない・ロック内で安全）"""
    st = st or {}
    if st.get("ai_provider") == "vertex":
        if task_type == "choices":
            return ((st.get("vertex_choices_model") or "").strip()
                    or (st.get("vertex_model") or "").strip())
        return (st.get("vertex_model") or "").strip()
    if task_type == "choices":
        return ((st.get("google_choices_model") or "").strip()
                or (st.get("google_model") or "").strip())
    return (st.get("google_model") or "").strip()


def _is_filter_error_msg(msg):
    low = str(msg or "").lower()
    return any(w in low for w in (
        "ブロック", "フィルタ", "safety", "prohibited_content", "prohibited",
        "blocklist", "spii", "model_armor", "recitation", "image_safety",
        "pup_limited_disabled",
    ))


def _google_inflight_key(sid, task_type, m):
    """F-1: _google_pick_tasks と完全一致する inflight キーを作る唯一の関数"""
    m = m or {}
    mid = m.get("id")
    if not mid:
        return ""
    if task_type == "choices":
        ctype = str(m.get("choices_type") or m.get("choices_slot") or "normal").lower()
        return _google_task_key(sid, m.get("choices_orig_id") or mid, f"choices:{ctype}")
    return _google_task_key(sid, mid, task_type)


def _google_inflight_has(tkey):
    if not tkey:
        return False
    with google_inflight_guard:
        return tkey in google_inflight


def _is_google_task_inflight(sid, task_type, m):
    return _google_inflight_has(_google_inflight_key(sid, task_type, m))
```

### 4.2 Antigravity からの隔離・`load()` スイーパ保護

#### A. `ai_provider == "google"` / `!= "google"` を `is_api_provider(st)` に置換（全箇所）

| # | 関数 / エンドポイント | 行番号目安 |
|---|---|---|
| 1 | `has_parallel_choices_ide` | 955 |
| 2 | `rotation_maybe_arm` | 2089 |
| 3 | `choices_ide_of` | 8227 |
| 4 | `plan_revert` | 8568 |
| 5 | `resume_scan` / `rotation_step` | 8873 / 8891 |
| 6 | `ide_auto_step`（2箇所） | 9472 / 9477 |
| 7 | `poke_worker` | 9568 |
| 8 | `_google_pick_tasks` | 11830 |
| 9 | `google_reset_processing` | 11790 |
| 10 | `/api/pending` | 12479 |
| 11 | `/api/ide/progress` | 12617 |
| 12 | `/api/send`（検証・起床） | 13755 / 13798 |
| 13 | `/api/send/stop`（巻き戻し条件） | 13822 |
| 14 | `/api/message/edit` / `truncate`（起床） | 13981 |
| 15 | `/api/ai/provider` | 14101-14176 |
| 16 | `/api/claim`（409） | 14191 |

#### B. 新規ガード

1. **`load()` の失効スイーパ（1608-1660行付近）**（F-1 修正版）: `is_api_provider(st)` のチャンネルでは、`_is_google_task_inflight(sid, <task>, m)` が True の間はその `status` を `pending` に戻さない。API プロバイダでは `enqueue_choices_notice_check` を呼ばない。

```python
# load() 内、各失効チェックの直前に差し込む
api_prov = is_api_provider(st)
...
if m.get("role") == "user" and m.get("status") == "processing":
    t_ref = m.get("claimed_ts") or m.get("ts")
    if t_ref and age(t_ref) > 180 and not (api_prov and _is_google_task_inflight(sid, "user", m)):
        ... 既存の pending 復帰 ...
...
if m.get("choices_status") in ("pending", "processing"):
    if api_prov and _is_google_task_inflight(sid, "choices", m):
        pass                      # 実行中はスイープしない
    else:
        ... 既存のタイムアウト処理（enqueue_choices_notice_check は api_prov のとき呼ばない） ...
...
if m.get("image_refine_status") in ("pending", "processing"):
    if not (api_prov and _is_google_task_inflight(sid, "image_refine", m)):
        ... 既存処理 ...
```

2. **`process_outbox`（10952行付近）**: `is_api_provider(s["setting"])` のとき `memory_status` / `consolidation_status` を `pending` で起票しない。

3. **`/api/ide/poke`（12981行付近）**:
```python
if is_api_provider((s.get("setting") if isinstance(s, dict) else None) or {}):
    return self.send_json({"ok": False,
                           "msg": "APIプロバイダ（Google/Vertex）では会話への合図は使いません"})
```

4. **`/api/ide/progress`（12617行付近）**:
```python
if is_api_provider(st):
    prov = api_provider_of(st)
    with google_inflight_guard:
        gs = dict(google_status.get(s["id"]) or {})
    running = bool(gs.get("state") in ("requesting", "retry"))
    label = "Vertex AI" if prov == "vertex" else "Google API"
    note = gs.get("error") or (f"{label}で生成中" if running else f"{label}の応答待ち")
    return self.send_json({"items": [], "running": running, "provider": prov,
                           "google": gs, "note": note})
```

### 4.3 ワーカーとプロンプト

- **`google_reset_processing()`（F-11 修正版）**: `is_api_provider(st)` に置換し、API プロバイダでは `memory_status` / `consolidation_status` を **`pending` に戻さず除去**する（拾う主体がいないため）。変更があった場合は `google_event.set()` でワーカーを起こす（起動直後に溜まった滞留を掃除させるため）。

```python
def google_reset_processing():
    """再起動時に API ワーカーの processing を pending に戻す（HTTP リクエストは失われているため）"""
    try:
        sids = list_ids()
    except OSError:
        return
    woke = False
    for sid in sids:
        try:
            with lock:
                s = load(sid)
                st = s.get("setting") or {}
                if not is_api_provider(st):
                    continue
                changed = False
                for m in s.get("messages") or []:
                    if m.get("role") == "user" and m.get("status") == "processing":
                        m["status"] = "pending"
                        m.pop("claimed_ts", None)
                        changed = True
                    if m.get("choices_status") == "processing":
                        m["choices_status"] = "pending"
                        m.pop("choices_claimed_ts", None)
                        changed = True
                    if m.get("image_refine_status") == "processing":
                        m["image_refine_status"] = "pending"
                        m.pop("image_refine_claimed_ts", None)
                        changed = True
                    # API プロバイダは記憶タスクを拾わないので pending に戻さず除去する
                    if m.get("memory_status") in ("pending", "processing"):
                        m.pop("memory_status", None)
                        m.pop("memory_ts", None)
                        m.pop("memory_claimed_ts", None)
                        changed = True
                    if m.get("consolidation_status") in ("pending", "processing"):
                        m.pop("consolidation_status", None)
                        m.pop("consolidation_ts", None)
                        m.pop("consolidation_claimed_ts", None)
                        changed = True
                if changed:
                    save(s)
                    woke = True
        except Exception as e:
            print(f"[google] reset error {sid}: {e}")
    if woke:
        google_event.set()
```

- **`_google_pick_tasks()`（F-6 修正版・3種のみ）**:

```python
def _google_pick_tasks():
    """実行可能な API タスクを返す。user / choices / image_refine の3種のみ"""
    tasks = []
    stale_mem = []          # F-6: readonly のセッションは書き換えず、後でロック内で処理する
    now_mono = time.monotonic()
    with lock:
        for sid in list_ids():
            try:
                s = load(sid, readonly=True)     # 参照。書き換え禁止
            except Exception:
                continue
            st = s.get("setting") or {}
            if not is_api_provider(st):
                continue
            for m in s.get("messages") or []:
                if not m.get("id"):
                    continue
                if m.get("role") == "user" and m.get("status") in ("pending", "processing"):
                    tkey = _google_inflight_key(sid, "user", m)
                    if now_mono >= google_retry_at.get(tkey, 0.0) and not _google_inflight_has(tkey):
                        tasks.append((sid, m["id"], "user", tkey))
                if m.get("choices_status") in ("pending", "processing"):
                    tkey = _google_inflight_key(sid, "choices", m)
                    if now_mono >= google_retry_at.get(tkey, 0.0) and not _google_inflight_has(tkey):
                        tasks.append((sid, m["id"], "choices", tkey))
                if m.get("image_refine_status") in ("pending", "processing"):
                    tkey = _google_inflight_key(sid, "image_refine", m)
                    if now_mono >= google_retry_at.get(tkey, 0.0) and not _google_inflight_has(tkey):
                        tasks.append((sid, m["id"], "image_refine", tkey))
                if (m.get("memory_status") in ("pending", "processing")
                        or m.get("consolidation_status") in ("pending", "processing")):
                    stale_mem.append((sid, m["id"]))
        # 2段階目: ロック内で読み直して滞留ステータスを落とす（無限リトライ防止）
        for sid, mid in stale_mem:
            try:
                s2 = load(sid)
            except Exception:
                continue
            m2 = find_msg(s2, mid)
            if not m2:
                continue
            m2.pop("memory_status", None); m2.pop("memory_ts", None)
            m2.pop("memory_claimed_ts", None)
            m2.pop("consolidation_status", None); m2.pop("consolidation_ts", None)
            m2.pop("consolidation_claimed_ts", None)
            save(s2)
    return tasks
```

- **`_google_fail()`（S1 / S6 / memory 停止）**: 現行の `error_retries` 方式を維持し、以下を追加。
  - `claimed_ts` を `pop` する前に退避（S1）。
  - `choices` のフィルタブロック時（S6）: assistant なら自身の id、user なら**後続に assistant が既にあればその最新 id**、無く status が pending/processing のときだけ `staged_choices_notice`。
  - `memory_extract` / `consolidation` は `"error"` にしてループを止める。

```python
def _google_fail(sid, task_type, mid, msg, retryable=False, key=None):
    filter_slot = None
    filter_task_ts = ""
    target_mid = ""
    attempts = 0
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return
        m = find_msg(s, mid)
        if not m and task_type == "choices":
            m = next((x for x in reversed(s.get("messages", []))
                      if x.get("choices_orig_id") == mid), None)
        if not m:
            return
        attempts = int(m.get("error_retries") or 0)
        claimed_ts = m.get("choices_claimed_ts") if task_type == "choices" else ""
        if retryable and attempts < 2:
            m["error_retries"] = attempts + 1
            if task_type == "user":
                m["status"] = "pending"; m.pop("claimed_ts", None)
            elif task_type == "choices":
                m["choices_status"] = "pending"; m.pop("choices_claimed_ts", None)
            elif task_type == "image_refine":
                m["image_refine_status"] = "pending"; m.pop("image_refine_claimed_ts", None)
            m["google_retry"] = msg
            if key:
                with google_inflight_guard:
                    google_retry_at[key] = time.monotonic() + min(30, 2 ** (attempts + 1))
        else:
            if task_type == "user":
                m["status"] = "error"; m["error"] = msg; m.pop("claimed_ts", None)
            elif task_type == "choices":
                m["choices_status"] = "error"; m["choices_error"] = msg
                m.pop("choices_claimed_ts", None)
                if _is_filter_error_msg(msg):
                    slot = str(m.get("choices_slot") or m.get("choices_type") or "normal").lower()
                    filter_slot = slot if slot in ("normal", "erotic") else "normal"
                    filter_task_ts = claimed_ts or m.get("choices_ts") or now()
                    if m.get("role") == "assistant":
                        target_mid = m["id"]
                    else:
                        msgs = s.get("messages") or []
                        idx = next((i for i, x in enumerate(msgs) if x.get("id") == m.get("id")), -1)
                        ans = (next((x for x in reversed(msgs[idx + 1:])
                                     if x.get("role") == "assistant"), None)
                               if idx >= 0 else None)
                        if ans and ans.get("id"):
                            target_mid = ans["id"]
                        elif m.get("status") in ("pending", "processing"):
                            m["staged_choices_notice"] = {"kind": "filter", "slot": filter_slot,
                                                          "task": filter_task_ts, "ts": now()}
            elif task_type == "image_refine":
                m["image_refine_status"] = "error"; m["image_refine_error"] = msg
                m.pop("image_refine_claimed_ts", None)
            elif task_type == "memory_extract":
                m["memory_status"] = "error"; m.pop("memory_claimed_ts", None)
            elif task_type == "consolidation":
                m["consolidation_status"] = "error"; m.pop("consolidation_claimed_ts", None)
            m.pop("google_retry", None)
        save(s)
    if filter_slot and target_mid:
        _set_choices_notice(sid, target_mid, "filter", filter_slot, filter_task_ts)
    _set_google_status(sid, task_type, "retry" if retryable and attempts < 2 else "error", error=msg)
```

- **`_cancel_inflight_task()`（新規）**:

```python
def _cancel_inflight_task(sid, mid, task_type):
    """破棄時に processing を pending に戻し、google_status を消す"""
    with lock:
        if sid not in deleted_sessions and os.path.exists(spath(sid)):
            try:
                s = load(sid)
                m = find_msg(s, mid)
                if not m and task_type == "choices":
                    m = next((x for x in reversed(s.get("messages", []))
                              if x.get("choices_orig_id") == mid), None)
                if m:
                    if task_type == "user" and m.get("status") == "processing":
                        m["status"] = "pending"; m.pop("claimed_ts", None); save(s)
                    elif task_type == "choices" and m.get("choices_status") == "processing":
                        m["choices_status"] = "pending"; m.pop("choices_claimed_ts", None); save(s)
                    elif task_type == "image_refine" and m.get("image_refine_status") == "processing":
                        m["image_refine_status"] = "pending"
                        m.pop("image_refine_claimed_ts", None); save(s)
            except Exception:
                pass
    _google_clear_status(sid, task_type)
```

- **`outbox` 出力（④）**: 現行の `_google_write_outbox` に `slot` を追加し、`choices` のファイル名に `_choices_{slot}_` を必ず含める（`choices_slot_of` の第4フォールバックが依存）。既存 `user` のファイル名形式 `{sid}_google_user_{mid}_` は変えない（既存テストが依存）。

```python
def _google_write_outbox(sid, mid, task_type, rep, slot=""):
    os.makedirs(OUTBOX, exist_ok=True)
    if task_type == "choices" and slot in ("normal", "erotic"):
        mark = f"_choices_{slot}_"
    else:
        mark = f"_{task_type}_"
    name = f"{sid}_google{mark}{mid}_{int(time.time() * 1000)}.json"
    path = os.path.join(OUTBOX, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False)
    os.replace(tmp, path)
    return name
```

- **`_google_choices_output_text()`（⑤）**: 3分岐＋`■ JSON出力ルール` / `- outbox/` 以降のみ除去（本文中のコードフェンスは壊さない）。

```python
def _google_choices_output_text(s, m, erotic=False):
    try:
        if m.get("role") == "user":
            base = parallel_choices_output_text(s, m, erotic=erotic)
        elif erotic:
            base = choices_regen_output_text(s, m)
        else:
            base = normal_choices_output_text(s, m)
    except Exception:
        base = ""
    base = re.split(r"\n■ ?JSON出力ルール[\s\S]*|\n- outbox/[\s\S]*", base, maxsplit=1)[0].strip()
    if base:
        return base + "\n\n■ JSON出力ルール\n" + _google_choices_contract()
    return _google_choices_contract()
```

- **`_google_build_prompt()`（⑥）**: `task_type == "user"` 内で `save_rag_hits(s["id"], m.get("id"), ctx.get("retrieved"))` を呼ぶ（`try/except` で握る）。

- **`run_google_task()`（F-5 修正版）**: 開始時の設定を保存し、終了時に削除・プロバイダ・モデル・fingerprint を比較して破棄する。

```python
def run_google_task(sid, mid, task_type=None, key=None):
    if task_type not in ("user", "choices", "image_refine", "memory_extract", "consolidation") \
            and mid in ("user", "choices", "image_refine", "memory_extract", "consolidation"):
        task_type, mid = mid, task_type
    try:
        with lock:
            s = load(sid)
        m = find_msg(s, mid)
        if not m and task_type == "choices":
            m = next((x for x in reversed(s.get("messages", []))
                      if x.get("choices_orig_id") == mid), None)
        if not m:
            return
        st = s.get("setting") or {}
        if not is_api_provider(st):
            _cancel_inflight_task(sid, mid, task_type)
            return
        provider = api_provider_of(st)
        model = active_api_model(st, task_type)
        if not model:
            _google_fail(sid, task_type, mid,
                         f"{'Vertex AI' if provider == 'vertex' else 'Google'}のモデルが未設定です", key=key)
            return
        start_model_setting = _api_model_setting(st, task_type)
        start_fp = google_ai.load_vertex_config().get("fingerprint") if provider == "vertex" else ""
        choices_slot = ""
        if task_type == "choices":
            choices_slot = "erotic" if _google_choice_type(m) == "erotic" else "normal"
        prompt, images, out_type, system = _google_build_prompt(s, m, task_type)
        use_fetch = bool(st.get("google_fetch", True)) and (task_type == "user")
        _set_google_status(sid, task_type, "requesting", provider=provider, model=model,
                           fetch=use_fetch, images=len(images))
        res = google_ai.generate(
            model, prompt, system,
            safety_off=bool(st.get("google_safety_off", True)),
            max_output_tokens=int(st.get("google_max_output_tokens") or 8192),
            fetch=use_fetch, images=images, provider=provider,
        )
        if sid in deleted_sessions:
            return
        cancelled = False
        with lock:
            if sid in deleted_sessions or not os.path.exists(spath(sid)):
                return
            try:
                cur_st = (load(sid).get("setting") or {})
                if (cur_st.get("ai_provider") != provider
                        or _api_model_setting(cur_st, task_type) != start_model_setting
                        or (provider == "vertex"
                            and google_ai.load_vertex_config().get("fingerprint") != start_fp)):
                    cancelled = True
            except Exception:
                return
        if cancelled:
            print(f"[google] cancelled (config/provider changed) {sid} {task_type}")
            _cancel_inflight_task(sid, mid, task_type)
            return
        if res.get("error"):
            _google_fail(sid, task_type, mid, str(res["error"]),
                         retryable=bool(res.get("retryable")), key=key)
            return
        obj, json_ok = google_ai.parse_json_text(res.get("text") or "")
        if not isinstance(obj, dict):
            _google_fail(sid, task_type, mid, "応答をJSONとして解釈できませんでした",
                         retryable=True, key=key)
            return
        # ... 既存の task_type 別分岐 ...
        #   user:   rep = obj から session/reply_to/type を除いたもの + text + memories
        #   choices: rep = {"choices": choices, "slot": choices_slot}
        #            （obj.get("target_id") があれば rep["target_id"]）
        #   image_refine: rep = {"refined_prompt": ...}
        target_mid = m.get("id") or mid
        rep["session"] = sid
        rep["reply_to"] = target_mid
        rep["type"] = out_type
        name = _google_write_outbox(sid, target_mid, task_type, rep, slot=choices_slot)
        _set_google_status(sid, task_type, "waiting_import", model=model,
                           fetch=bool(res.get("fetch_called")), outbox=name,
                           elapsed_ms=res.get("elapsed_ms"),
                           usage=(res.get("meta") or {}).get("usage") or {})
        print(f"[google] queued outbox {name} -> {sid} ({task_type})")
    except Exception as e:
        print(f"[google] task error {sid} {task_type}: {e}")
        _google_fail(sid, task_type, mid, f"Google処理エラー: {e}", retryable=False, key=key)
```

### 4.4 sent 台帳の保護・ロック統一・削除時後始末

- `google_inflight_guard = threading.RLock()` に変更（既存 `Lock`）。ロック順は必ず `with lock, google_inflight_guard:`。
- `_set_google_status` / `_google_clear_status` / `_purge_google_session_state` の **3関数すべて**を同じロック順に統一。ただし **`_google_clear_status` のロジックは現行のタスクマップ方式を維持**（F-2）。

```python
def _set_google_status(sid, task_type, state, **extra):
    if sid in deleted_sessions:
        return
    with lock, google_inflight_guard:
        if sid in deleted_sessions:
            return
        cur = google_status.get(sid) or {}
        by_task = dict(cur.get("tasks") or {})
        entry = {"task_type": task_type, "state": state, "ts": now(), **extra}
        by_task[task_type] = entry
        user_entry = by_task.get("user")
        if user_entry and user_entry.get("state") in ("running", "retrying", "requesting", "retry") \
                and task_type != "user":
            top = dict(user_entry, tasks=by_task)
        else:
            top = dict(entry, tasks=by_task)
        google_status[sid] = top


def _google_clear_status(sid, task_type=None):
    """F-2: 現行のタスクマップ方式を維持し、ロックだけ統一する"""
    if not sid:
        return
    with lock, google_inflight_guard:
        if task_type:
            cur = google_status.get(sid)
            if cur and "tasks" in cur:
                by_task = dict(cur.get("tasks") or {})
                by_task.pop(task_type, None)
                user_entry = by_task.get("user")
                if user_entry and user_entry.get("state") in ("running", "retrying",
                                                              "requesting", "retry"):
                    google_status[sid] = dict(user_entry, tasks=by_task)
                    return
                elif by_task:
                    latest = max(by_task.values(), key=lambda x: x.get("ts", ""))
                    google_status[sid] = dict(latest, tasks=by_task)
                    return
        google_status.pop(sid, None)


def _purge_google_session_state(sid):
    with lock, google_inflight_guard:
        google_status.pop(sid, None)
        for _k in [k for k in google_retry_at if k.startswith(f"{sid}:")]:
            google_retry_at.pop(_k, None)
        for _k in [k for k in google_inflight if k.startswith(f"{sid}:")]:
            google_inflight.discard(_k)


def note_reply(s, reply_to, msg):
    """APIプロバイダ（google/vertex）のターンでは sent 台帳に触らない"""
    if is_api_provider(s.get("setting") or {}):
        return
    ...  # 既存処理


def mark_choices_sent(s):
    if is_api_provider(s.get("setting", {})):
        return
    ...  # 既存処理


def _reset_session_api_tasks_to_pending(s):
    for m in s.get("messages") or []:
        if m.get("status") == "processing":
            m["status"] = "pending"; m.pop("claimed_ts", None)
        m.pop("google_retry", None)
        if m.get("choices_status") == "processing":
            m["choices_status"] = "pending"; m.pop("choices_claimed_ts", None)
        if m.get("image_refine_status") == "processing":
            m["image_refine_status"] = "pending"; m.pop("image_refine_claimed_ts", None)
```

- `delete_session_all()` は `deleted_sessions.add(sid)` の後（同じ `with lock:` 内）で `_purge_google_session_state(sid)` を呼ぶ。
- S6 の後始末: `save()` の `um.get("status") not in ("pending", "processing")` 時のクリーンアップ対象に `um.pop("staged_choices_notice", None)` を追加。`clone_session` と `/api/send` の旧データ掃除にも同キーを追加。

### 4.5 HTTP エンドポイント

#### GET

```python
if p == "/api/vertex/status":
    cfg = google_ai.load_vertex_config()
    cfg.pop("fingerprint", None)
    return self.send_json(cfg)

if p == "/api/vertex/models":
    refresh = q.get("refresh", ["0"])[0] in ("1", "true")
    try:
        models = google_ai.list_models(refresh=refresh, provider="vertex")
    except google_ai.GoogleAPIError as e:
        return self.send_json({"models": [], "error": str(e), "code": e.code}, 200)
    except Exception as e:
        return self.send_json({"models": [], "error": str(e)}, 200)
    err = google_ai.last_models_error(provider="vertex") or None
    return self.send_json({"models": models, "error": err})
```

#### POST

```python
if p == "/api/vertex/config":
    try:
        cfg = google_ai.save_vertex_config(
            sa_json=d.get("sa_json"),
            project_id=d.get("project_id"),
            location=d.get("location"),
        )
    except ValueError as e:
        return self.send_json({"error": str(e)}, 400)
    except OSError as e:
        return self.send_json({"error": f"設定ファイルの保存に失敗しました: {e}"}, 500)
    auth_err = None
    try:
        google_ai.get_vertex_access_token(force_refresh=True, timeout=15)
        google_ai.list_models(refresh=True, timeout=15, provider="vertex")
        auth_err = google_ai.last_models_error(provider="vertex") or None
    except Exception as e:
        auth_err = str(e)
    google_event.set()
    cfg.pop("fingerprint", None)
    return self.send_json({"ok": not bool(auth_err), "status": cfg, "error": auth_err})

if p == "/api/vertex/config/delete":
    try:
        google_ai.delete_vertex_config()
    except OSError as e:
        return self.send_json({"error": f"設定ファイルの削除に失敗しました: {e}"}, 500)
    target_sids = []
    for _sid in list_ids():
        try:
            raw_s = read_json(spath(_sid), {})
            if ((raw_s.get("setting") or {}).get("ai_provider") == "vertex"):
                target_sids.append(_sid)
        except Exception:
            target_sids.append(_sid)
    for _sid in target_sids:
        try:
            with lock:
                _s = load(_sid)
                _st = _s["setting"]
                if _st.get("ai_provider") != "vertex":
                    continue
                _st["ai_provider"] = "antigravity"
                _st["vertex_model"] = ""
                _st["vertex_choices_model"] = ""
                if not (_st.get("ide_session") or "").strip():
                    _st["ide_auto"] = True
                if (_st.get("suggest_choices", True)
                        and not _st.get("choices_ide_manual_off")
                        and not has_choices_ide_session(_st)):
                    _st["choices_ide_auto"] = True
                _reset_session_api_tasks_to_pending(_s)
                save(_s)
                write_sent(_sid, {"ide": "", "choices_ide": "", "msgs": [], "mem_ids": [],
                                  "card_sig": {}, "card_for_mid": None,
                                  "reset": True, "why": "Vertex設定削除", "ts": now()})
                _purge_google_session_state(_sid)
        except Exception as e:
            print(f"[vertex] config delete session reset error ({_sid}): {e}")
    ide_auto_event.set()
    poke_event.set()
    return self.send_json({"ok": True})
```

#### `/api/send`（13755-13805 付近）

```python
prov = s["setting"].get("ai_provider")
if prov == "google":
    ...  # 既存の APIキー・モデル検証をそのまま維持
elif prov == "vertex":
    if not google_ai.vertex_configured():
        return self.send_json({"error": "Vertex AIの認証またはプロジェクトIDが未設定です。",
                               "code": "vertex_not_configured"}, 400)
    v_mdl = (s["setting"].get("vertex_model") or "").strip()
    if not v_mdl or not google_ai.MODEL_ID_RE.fullmatch(v_mdl):
        s["setting"]["vertex_model"] = google_ai.default_vertex_model()
...
if is_api_provider(s["setting"]):
    google_event.set()
else:
    ide_auto_event.set()
    poke_event.set()
```

添付サイズ上限は **`_google_prepare_attachments()`（11631行付近の 45MB）が既存のまま適用**されるので、`/api/send` に新しいサイズ検査は追加しない。

#### `/api/ai/provider`（14101-14176 付近）

```python
if p == "/api/ai/provider":
    sid = d.get("sid") or ""
    provider = str(d.get("provider") or "antigravity").strip().lower()
    if provider not in ("antigravity", "google", "vertex"):
        return self.send_json({"error": "provider は antigravity / google / vertex のいずれかを指定してください"}, 400)
    req_model = str(d.get("model") or "").strip()
    if req_model and not google_ai.MODEL_ID_RE.fullmatch(req_model):
        return self.send_json({"error": "モデルIDの形式が不正です"}, 400)

    if provider == "google":
        key, _ = google_ai.load_key()
        if not key:
            return self.send_json({"error": "Google APIキーが未設定です", "code": "google_no_key"}, 400)
    elif provider == "vertex":
        if not google_ai.vertex_configured():
            return self.send_json({"error": "Vertex AIの認証またはプロジェクトIDが未設定です",
                                   "code": "vertex_not_configured"}, 400)

    with lock:
        s = load(sid)
        st = s["setting"]
        prev = st.get("ai_provider") or "antigravity"
        # 既存の 409 条件と完全一致させる（role 条件も維持）
        if any(
            (m.get("role") == "user" and m.get("status") in ("pending", "processing"))
            or m.get("choices_status") in ("pending", "processing")
            or m.get("image_refine_status") in ("pending", "processing")
            for m in s.get("messages", [])
        ):
            return self.send_json({"error": "応答待ち中はAIプロバイダを切り替えられません。停止してから切り替えてください。"}, 409)

        st["ai_provider"] = provider
        if provider in ("google", "vertex"):
            if provider == "google":
                model = req_model or st.get("google_model") or google_ai.default_model()
                st["google_model"] = model
            else:
                model = req_model or st.get("vertex_model") or google_ai.default_vertex_model()
                st["vertex_model"] = model
            if "fetch" in d:
                st["google_fetch"] = bool(d.get("fetch"))
            if "safety_off" in d:
                st["google_safety_off"] = bool(d.get("safety_off"))
            if "max_output_tokens" in d:
                try:
                    st["google_max_output_tokens"] = max(256, min(65536, int(d.get("max_output_tokens") or 8192)))
                except (TypeError, ValueError):
                    pass
            st["ide_auto"] = False
            st["choices_ide_auto"] = False
            s.pop("rotation", None)
        else:
            if req_model:
                st["ide_model"] = req_model
            model = st.get("ide_model") or ""
            s.pop("rotation", None)
            s.pop("rotation_reserve", None)
            if not (st.get("ide_session") or "").strip():
                st["ide_auto"] = True
            if (st.get("suggest_choices", True)
                    and not st.get("choices_ide_manual_off")
                    and not has_choices_ide_session(st)):
                st["choices_ide_auto"] = True

        if prev != provider:
            _purge_google_session_state(sid)
            write_sent(sid, {"ide": "", "choices_ide": "", "msgs": [], "mem_ids": [],
                             "card_sig": {}, "card_for_mid": None,
                             "reset": True, "why": f"AIプロバイダ切替({prev}->{provider})", "ts": now()})
        save(s)

    if provider in ("google", "vertex"):
        google_event.set()
    else:
        ide_auto_event.set()
        poke_event.set()

    return self.send_json({
        "ok": True,
        "provider": provider,
        "model": st.get("vertex_model") if provider == "vertex" else (
            st.get("google_model") if provider == "google" else st.get("ide_model")),
        "choices_model": st.get("vertex_choices_model") if provider == "vertex" else (
            st.get("google_choices_model") if provider == "google" else st.get("choices_ide_model")),
        "fetch": st.get("google_fetch", True),
        "safety_off": st.get("google_safety_off", True),
        "max_output_tokens": st.get("google_max_output_tokens", 8192),
    })
```

#### `/api/ide/model`

`main + google` は**既存通り `ide_model`**（`test_parallel_choices.py` 非回帰）。追加は `vertex` の2分岐のみ。

```python
model = d.get("model") or ""
if not isinstance(model, str):
    return self.send_json({"error": "model must be a string"}, 400)
model = model.strip()
if model and not google_ai.MODEL_ID_RE.fullmatch(model):
    return self.send_json({"error": "モデルIDの形式が不正です"}, 400)
target = d.get("target") or "main"
provider = d.get("provider") or ""
if target == "choices" and provider == "vertex":
    field = "vertex_choices_model"
elif target == "choices" and provider == "google":
    field = "google_choices_model"
elif target == "main" and provider == "vertex":
    field = "vertex_model"
else:
    field = "choices_ide_model" if target == "choices" else "ide_model"
```

#### `/api/setting` の keep

`server.py:14434` 付近の keep 対象に `"vertex_model"` と `"vertex_choices_model"` を追加する（クライアントの古い `state.setting` でモデル設定が消えるのを防ぐ）。

---

## 5. `public/index.html`

1. `#aiProvider` に `<option value="vertex">Google Cloud（Vertex AI）</option>` を追加。
2. `#googleBox` の直後に `#vertexBox` を追加。
3. `#googleKeyModal` の直後に `#vertexConfigModal` を追加。
4. `<script src="app.js?v=104"></script>` に更新。

```html
<div id="vertexBox" class="hidden" style="border:1px solid #333; border-radius:8px; padding:10px; margin-bottom:10px">
  <div class="row" style="justify-content:space-between">
    <strong>Google Cloud（Vertex AI）</strong>
    <span id="vertexKeyState" class="muted">確認中…</span>
  </div>
  <div class="row" style="margin-top:6px">
    <button id="vertexConfigSet" type="button" class="sub">認証・リージョン設定</button>
    <button id="vertexConfigDelete" type="button" class="sub danger">設定削除</button>
    <button id="vertexModelsReload" type="button" class="sub">モデル一覧を更新</button>
  </div>
  <label>Vertexモデル<select id="vertexModel"><option value="">認証・プロジェクトを設定してください</option></select></label>
  <label>カスタムモデルID（任意・一覧にないモデルを指定する場合）
    <input id="vertexCustomModel" type="text" placeholder="例: gemini-2.5-pro / gemini-2.0-flash-001">
  </label>
  <label class="check"><input type="checkbox" id="vertexFetch"> プロンプト取り寄せ（function calling）を使う</label>
  <label class="check"><input type="checkbox" id="vertexSafetyOff"> 調整可能な安全カテゴリをOFFにする</label>
  <label>最大出力トークン<input id="vertexMaxTokens" type="number" min="256" max="65536" step="256" value="8192"></label>
  <div class="row">
    <button id="vertexApply" type="button">Vertex設定を保存</button>
    <span id="vertexApplyMsg" class="muted"></span>
  </div>
</div>

<div id="vertexConfigModal" class="modal hidden">
  <div class="modal-card" style="max-width:520px">
    <h3>Vertex AI 認証・リージョン設定</h3>
    <p class="muted" style="font-size:12px">
      GCPサービスアカウントのJSONキーを選択（または貼り付け）するか、ADC（<code>gcloud auth application-default login</code>）を設定した状態でプロジェクトIDを指定してください。<br>
      ※サービスアカウントには最小権限（<code>Vertex AI ユーザー / roles/aiplatform.user</code>）のみを付与することを推奨します。
    </p>
    <label>サービスアカウントJSONファイルを選択
      <input id="vertexSaFile" type="file" accept=".json,application/json">
    </label>
    <label>またはサービスアカウントJSONを直接貼り付け（任意・既存認証を使う場合は空欄可）
      <textarea id="vertexSaInput" rows="4" placeholder='{"type": "service_account", "project_id": "...", ...}'></textarea>
    </label>
    <label>GCP プロジェクトID（必須）
      <input id="vertexProjectInput" type="text" placeholder="例: my-gcp-project-123">
    </label>
    <label>リージョン（Location）
      <input id="vertexLocationInput" type="text" list="vertexLocationList" value="global" placeholder="global">
      <datalist id="vertexLocationList">
        <option value="global"></option>
        <option value="us-central1"></option>
        <option value="us-east5"></option>
        <option value="asia-northeast1"></option>
        <option value="europe-west1"></option>
      </datalist>
    </label>
    <div id="vertexConfigMsg" class="muted" style="min-height:1.4em; margin:6px 0"></div>
    <div class="row" style="justify-content:flex-end; gap:8px">
      <button id="vertexConfigCancel" type="button" class="sub">キャンセル</button>
      <button id="vertexConfigSave" type="button">保存して認証確認</button>
    </div>
  </div>
</div>
```

---

## 6. `public/app.js`

### 6.1 `checked()` の `code` 伝搬（現行構造を維持）

```javascript
async function checked(path, body, method = null) {
  const result = await api(path, body, method);
  if (result && result.error) {
    const err = new Error(result.error);
    if (result.code) err.code = result.code;
    throw err;
  }
  return result;
}
```

### 6.2 状態管理・モーダル・全イベント配線・`syncGoogleSettingsUI()`

```javascript
let vertexModels = [];
let vertexStatusLoaded = false;
let vertexConfigured = false;
let vertexStatus = {};

function vertexModelOptions(selected) {
  const rows = vertexModels || [];
  const opts = [`<option value="">モデルを選択</option>`]
    .concat(rows.map((m) => `<option value="${esc(m.id)}">${esc(m.label || m.id)}`
      + `${m.outputTokenLimit ? "（出力上限 " + m.outputTokenLimit + "）" : ""}</option>`));
  if (selected && !rows.some((m) => m.id === selected)) {
    opts.push(`<option value="${esc(selected)}">${esc(selected)}（一覧にない）</option>`);
  }
  return opts.join("");
}

async function loadVertexStatus() {
  try {
    const d = await checked("/api/vertex/status");
    vertexStatus = d || {};
    vertexConfigured = !!d.configured;
    updateVertexStateBadge(vertexStatus);
  } catch (e) {
    vertexStatus = {};
    vertexConfigured = false;
    if ($("vertexKeyState")) {
      $("vertexKeyState").className = "err";
      $("vertexKeyState").textContent = e.message;
    }
  } finally {
    vertexStatusLoaded = true;
  }
}

async function loadVertexModels(force = false) {
  try {
    const d = await checked("/api/vertex/models" + (force ? "?refresh=1" : ""));
    vertexModels = d.models || [];
    if ($("vertexApplyMsg")) {
      if (d.error) {
        $("vertexApplyMsg").className = "err";
        $("vertexApplyMsg").textContent = d.error;
      } else if ($("vertexApplyMsg").className === "err") {
        $("vertexApplyMsg").className = "muted";
        $("vertexApplyMsg").textContent = "";
      }
    }
  } catch (e) {
    vertexModels = [];
    if ($("vertexApplyMsg")) {
      $("vertexApplyMsg").className = "err";
      $("vertexApplyMsg").textContent = e.message;
    }
  }
  syncGoogleSettingsUI();
  renderModels();
}

function updateVertexStateBadge(vd) {
  const el = $("vertexKeyState");
  if (!el) return;
  if (vd && vd.configured) {
    el.className = "ok";
    const who = vd.auth_type === "adc" ? "ADC" : (vd.client_email_hint || "SA");
    el.textContent = `設定済み（${vd.project_id} / ${vd.location} / ${who}）`;
  } else {
    el.className = "warn";
    el.textContent = "未設定";
  }
}

function openVertexConfigModal() {
  $("vertexConfigModal")?.classList.remove("hidden");
  if ($("vertexConfigMsg")) {
    $("vertexConfigMsg").className = "muted";
    $("vertexConfigMsg").textContent = "";
  }
  if ($("vertexProjectInput")) {
    $("vertexProjectInput").value = vertexStatus.project_id || vertexStatus.adc_project_hint || "";
  }
  if ($("vertexLocationInput")) {
    $("vertexLocationInput").value = vertexStatus.location || "global";
  }
  if ($("vertexSaInput")) $("vertexSaInput").value = "";
  if ($("vertexSaFile")) $("vertexSaFile").value = "";
  setTimeout(() => $("vertexProjectInput")?.focus(), 50);
}

function closeVertexConfigModal() {
  $("vertexConfigModal")?.classList.add("hidden");
}

async function saveVertexConfig() {
  const btn = $("vertexConfigSave");
  if (btn) btn.disabled = true;
  if ($("vertexConfigMsg")) {
    $("vertexConfigMsg").className = "muted";
    $("vertexConfigMsg").textContent = "確認中…";
  }
  try {
    const body = {
      sa_json: ($("vertexSaInput")?.value || "").trim(),
      project_id: ($("vertexProjectInput")?.value || "").trim(),
      location: ($("vertexLocationInput")?.value || "").trim() || "global",
    };
    const r = await checked("/api/vertex/config", body);
    await loadVertexStatus();
    await loadVertexModels(true);
    if (r.error) {
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = "err";
        $("vertexConfigMsg").textContent = "保存しましたが疎通確認で警告: " + r.error;
      }
    } else {
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = "ok";
        $("vertexConfigMsg").textContent = "保存して認証を確認しました";
      }
      closeVertexConfigModal();
      if (state?.setting) {
        await applyProviderSettings("vertex");
        await poll();
        renderModels();
      }
    }
  } catch (e) {
    if ($("vertexConfigMsg")) {
      $("vertexConfigMsg").className = "err";
      $("vertexConfigMsg").textContent = e.message;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function applyProviderSettings(provider) {
  const p = ["google", "vertex"].includes(provider) ? provider : "antigravity";
  const body = { sid, provider: p };
  if (p === "google") {
    body.model = $("googleModel")?.value || "";
    body.fetch = !!$("googleFetch")?.checked;
    body.safety_off = !!$("googleSafetyOff")?.checked;
    body.max_output_tokens = Number($("googleMaxTokens")?.value || 8192);
  } else if (p === "vertex") {
    const custom = ($("vertexCustomModel")?.value || "").trim();
    body.model = custom || ($("vertexModel")?.value || "");
    body.fetch = !!$("vertexFetch")?.checked;
    body.safety_off = !!$("vertexSafetyOff")?.checked;
    body.max_output_tokens = Number($("vertexMaxTokens")?.value || 8192);
  }
  try {
    const d = await checked("/api/ai/provider", body);
    if (state?.setting && d) {
      state.setting.ai_provider = d.provider || p;
      if (p === "vertex" && d.model) state.setting.vertex_model = d.model;
      if (p === "google" && d.model) state.setting.google_model = d.model;
      if (typeof d.fetch === "boolean") state.setting.google_fetch = d.fetch;
      if (typeof d.safety_off === "boolean") state.setting.google_safety_off = d.safety_off;
      if (d.max_output_tokens) state.setting.google_max_output_tokens = d.max_output_tokens;
    }
    await poll();
    await loadModels();
    renderModels();
    syncGoogleSettingsUI();
    flash(p === "vertex" ? "Vertex AI に切り替えました"
      : p === "google" ? "Google Gemini に切り替えました" : "Antigravity に切り替えました");
  } catch (e) {
    if (e.code === "vertex_not_configured") openVertexConfigModal();
    else if (e.code === "google_no_key") openGoogleKeyModal();
    else alert(e.message);
    renderModels();
    syncGoogleSettingsUI();
  }
}

/* ---- イベント配線 ---- */
if ($("vertexSaFile")) {
  $("vertexSaFile").onchange = async () => {
    const f = $("vertexSaFile").files?.[0];
    if (!f) return;
    try {
      const text = await f.text();
      if ($("vertexSaInput")) $("vertexSaInput").value = text;
      let pid = "";
      try { pid = (JSON.parse(text) || {}).project_id || ""; } catch (_) { pid = ""; }
      if (pid && $("vertexProjectInput") && !$("vertexProjectInput").value.trim()) {
        $("vertexProjectInput").value = pid;
      }
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = pid ? "ok" : "warn";
        $("vertexConfigMsg").textContent = pid
          ? `読み込みました（project_id: ${pid}）`
          : "JSONを読みましたが project_id が見つかりません";
      }
    } catch (e) {
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = "err";
        $("vertexConfigMsg").textContent = e.message;
      }
    }
  };
}
if ($("aiProvider")) {
  $("aiProvider").onchange = async () => {
    const sel = $("aiProvider").value;
    const provider = ["google", "vertex"].includes(sel) ? sel : "antigravity";
    $("googleBox")?.classList.toggle("hidden", provider !== "google");
    $("vertexBox")?.classList.toggle("hidden", provider !== "vertex");
    if (provider === "google") {
      if (!googleStatusLoaded) await loadGoogleStatus();
      if (!googleConfigured) { openGoogleKeyModal(); return; }
      if (!googleModels.length) await loadGoogleModels();
    } else if (provider === "vertex") {
      if (!vertexStatusLoaded) await loadVertexStatus();
      if (!vertexConfigured) { openVertexConfigModal(); return; }
      if (!vertexModels.length) await loadVertexModels();
    }
    await applyProviderSettings(provider);
  };
}
if ($("vertexConfigSet")) $("vertexConfigSet").onclick = openVertexConfigModal;
if ($("vertexConfigCancel")) $("vertexConfigCancel").onclick = closeVertexConfigModal;
if ($("vertexConfigSave")) $("vertexConfigSave").onclick = saveVertexConfig;
if ($("vertexModelsReload")) $("vertexModelsReload").onclick = () => loadVertexModels(true);
if ($("vertexApply")) {
  $("vertexApply").onclick = async () => {
    const custom = ($("vertexCustomModel")?.value || "").trim();
    const model = custom || ($("vertexModel")?.value || "");
    try {
      if (model) await checked("/api/ide/model", { sid, model, target: "main", provider: "vertex" });
      await applyProviderSettings("vertex");
      if ($("vertexApplyMsg")) {
        $("vertexApplyMsg").className = "ok";
        $("vertexApplyMsg").textContent = "保存しました";
      }
    } catch (e) {
      if ($("vertexApplyMsg")) {
        $("vertexApplyMsg").className = "err";
        $("vertexApplyMsg").textContent = e.message;
      }
    }
  };
}
if ($("vertexModel")) {
  $("vertexModel").onchange = () => {
    if ($("vertexCustomModel")) $("vertexCustomModel").value = "";
  };
}
if ($("vertexConfigDelete")) {
  $("vertexConfigDelete").onclick = async () => {
    if (!confirm("Vertex AI の設定（サービスアカウントJSON・プロジェクト・リージョン）を削除します。\nVertex を使っているチャンネルは Antigravity に戻ります。よろしいですか？")) return;
    try {
      await checked("/api/vertex/config/delete", {});
      await loadVertexStatus();
      vertexModels = [];
      await poll();
      renderModels();
      syncGoogleSettingsUI();
      flash("Vertex AI の設定を削除しました");
    } catch (e) { alert(e.message); }
  };
}
if ($("vertexConfigModal")) {
  $("vertexConfigModal").addEventListener("click", (e) => {
    if (e.target === $("vertexConfigModal")) closeVertexConfigModal();
  });
}

/* ---- 既存 syncGoogleSettingsUI を拡張（置換ではなく差分で追記する） ---- */
function syncGoogleSettingsUI() {
  const st = state?.setting || {};
  const prov = ["google", "vertex"].includes(st.ai_provider) ? st.ai_provider : "antigravity";
  if ($("aiProvider") && document.activeElement !== $("aiProvider")) $("aiProvider").value = prov;
  if ($("googleBox")) $("googleBox").classList.toggle("hidden", prov !== "google");
  if ($("vertexBox")) $("vertexBox").classList.toggle("hidden", prov !== "vertex");

  // Google AI Studio（既存処理を維持）
  if ($("googleModel") && document.activeElement !== $("googleModel")) {
    const html = googleModelOptions(st.google_model || "");
    if ($("googleModel").dataset.html !== html) {
      $("googleModel").innerHTML = html;
      $("googleModel").dataset.html = html;
    }
    $("googleModel").value = st.google_model || "";
  }
  if ($("googleFetch")) $("googleFetch").checked = st.google_fetch !== false;
  if ($("googleSafetyOff")) $("googleSafetyOff").checked = st.google_safety_off !== false;
  if ($("googleMaxTokens")) $("googleMaxTokens").value = st.google_max_output_tokens || 8192;

  // Vertex AI
  if ($("vertexModel") && document.activeElement !== $("vertexModel")) {
    const html = vertexModelOptions(st.vertex_model || "");
    if ($("vertexModel").dataset.html !== html) {
      $("vertexModel").innerHTML = html;
      $("vertexModel").dataset.html = html;
    }
    $("vertexModel").value = st.vertex_model || "";
  }
  if ($("vertexCustomModel") && document.activeElement !== $("vertexCustomModel")) {
    const inList = (vertexModels || []).some((m) => m.id === st.vertex_model);
    $("vertexCustomModel").value = (st.vertex_model && !inList) ? st.vertex_model : "";
  }
  if ($("vertexFetch")) $("vertexFetch").checked = st.google_fetch !== false;
  if ($("vertexSafetyOff")) $("vertexSafetyOff").checked = st.google_safety_off !== false;
  if ($("vertexMaxTokens")) $("vertexMaxTokens").value = st.google_max_output_tokens || 8192;
  updateVertexStateBadge(vertexStatus);
}
```

### 6.3 `renderModels()` / `#ideModel` / `#choicesIdeModel`

1. **`renderModels()`**: `#ideModel` の optgroup を3つにする。既存の `ag:` / `google:` に加え `vertex:` を追加し、`want` を次のように決める。

```javascript
const isAg = !prov || prov === "antigravity";
const isGoogle = prov === "google";
const isVertex = prov === "vertex";
const want = isVertex
  ? ("vertex:" + (state.setting?.vertex_model || "gemini-2.5-flash"))
  : isGoogle
    ? (state.setting?.google_model ? "google:" + state.setting.google_model : "google:")
    : (state.setting?.ide_model ? "ag:" + state.setting.ide_model : "ag:");
```
`vertexModels` が空で `isVertex` の場合は、`st.vertex_model || "gemini-2.5-flash"` だけの1件を `vertex:` 候補として必ず含める（選択状態が外れないように）。
`#choicesIdeModel` は `isVertex` のとき `vertexModels` を並べ、選択値を `st.vertex_choices_model || ""` にする。

2. **`#ideModel.onchange`**: 既存の `const val = $("ideModel").value;` の直後に `vertex:` 分岐を追加。

```javascript
if (val.startsWith("vertex:")) {
  const model = val.slice("vertex:".length);
  if (!model) { renderModels(); return; }
  if (!vertexStatusLoaded) await loadVertexStatus();
  if (!vertexConfigured) { openVertexConfigModal(); renderModels(); return; }
  try {
    await checked("/api/ai/provider", { sid, provider: "vertex", model });
    toast("Vertex AI: " + model);
    await poll();
    renderModels();
    syncGoogleSettingsUI();
  } catch (e) {
    if (e.code === "vertex_not_configured") openVertexConfigModal();
    else alert(e.message);
    renderModels();
  }
  $("ideModel").blur();
  return;
}
```

3. **`#choicesIdeModel.onchange`**: 3プロバイダ対応（`vertex` のとき `vertex_choices_model`）。

4. `updateModelSheetLeft()` / `render()` / `pollProgress()` 内の `ai_provider === "google"` 判定を **すべて** `["google", "vertex"].includes(...)` に変更する（`pollProgress` の早期 return 条件に vertex を含めないと進捗が表示されない）。

---

## 7. `start.bat` / `start_mobile.bat` / `.env.example`

両 bat の既存 `pywebpush` チェック直後に追加（失敗しても継続）。

```bat
REM Google GenAI SDK（任意）と認証署名用 cryptography の確認
python -c "from google import genai" >nul 2>&1
if errorlevel 1 (
  echo Google GenAI SDK ^(google-genai^) をインストールしています...
  python -m pip install google-genai
  if errorlevel 1 echo [警告] google-genai のインストールに失敗しました（REST で動作します）。
)
python -c "import cryptography" >nul 2>&1
if errorlevel 1 (
  echo 認証署名用の cryptography をインストールしています...
  python -m pip install cryptography
  if errorlevel 1 echo [警告] cryptography のインストールに失敗しました（google-auth または ADC で動作します）。
)
```

`.env.example` は既存の `GEMINI_API_KEY=` 行の**直下に追記**（キー行を重複させない）。

```ini
# Google Cloud Vertex AI（UIの「認証・リージョン設定」から保存するのが確実）
# ※サービスアカウントには最小権限（roles/aiplatform.user）のみを付与してください
# VERTEX_PROJECT_ID=my-gcp-project-id
# VERTEX_LOCATION=global
# SA の置き場所は data/vertex_sa.json を自動検出するので通常は指定不要
# VERTEX_SA_JSON_PATH=

# Gemini トランスポート: auto（既定・SDK があれば使う） / sdk / rest
# RPCHAT_GEMINI_TRANSPORT=auto
```

---

## 8. テスト（`test/test_vertex_ai.py` ケース 1〜5）

```python
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

import google_ai
import server


SAMPLE_SA = {
    "type": "service_account",
    "project_id": "demo-gcp-proj",
    "private_key_id": "key123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC7\n-----END PRIVATE KEY-----\n",
    "client_email": "rpchat-bot@demo-gcp-proj.iam.gserviceaccount.com",
    "token_uri": google_ai.GOOGLE_TOKEN_URI,
}


class TestVertexAI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_data = google_ai.DATA
        self.orig_env = google_ai.ENV_FILE
        google_ai.DATA = self.tmp.name
        google_ai.ENV_FILE = os.path.join(self.tmp.name, ".env")
        os.environ["RPCHAT_GEMINI_TRANSPORT"] = "rest"
        for k in ("VERTEX_PROJECT_ID", "VERTEX_LOCATION", "VERTEX_SA_JSON_PATH"):
            os.environ.pop(k, None)
        google_ai.invalidate_vertex_token()
        google_ai.invalidate_models_cache()

    def tearDown(self):
        google_ai.DATA = self.orig_data
        google_ai.ENV_FILE = self.orig_env
        for k in ("RPCHAT_GEMINI_TRANSPORT", "VERTEX_PROJECT_ID",
                  "VERTEX_LOCATION", "VERTEX_SA_JSON_PATH"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    # 1: SA JSON の検証・正規化と設定保存
    def test_1_validate_and_save_sa_json(self):
        with self.assertRaises(ValueError):
            google_ai.parse_and_validate_sa_json("not-a-json")
        with self.assertRaises(ValueError):
            google_ai.parse_and_validate_sa_json(json.dumps({"type": "authorized_user"}))

        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            cfg = google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                               project_id="", location="asia-northeast1")
        self.assertTrue(cfg["configured"])
        self.assertEqual(cfg["project_id"], "demo-gcp-proj")
        self.assertEqual(cfg["location"], "asia-northeast1")
        self.assertEqual(cfg["sa_basename"], "vertex_sa.json")
        self.assertNotIn("sa_path", cfg)

    # 2: トークン 401 時の1回再試行
    def test_2_token_cache_and_401_retry(self):
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                         project_id="demo-gcp-proj", location="global")
        calls = []

        def fake_urlopen(req, timeout=15):
            calls.append(req.get_header("Authorization"))
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                             io.BytesIO(b'{"error":{"message":"expired"}}'))
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'
            resp.__enter__.return_value = resp
            return resp

        tokens = [("tok-1", "service_account", ""), ("tok-2", "service_account", "")]
        with patch("google_ai.get_vertex_access_token",
                   side_effect=lambda force_refresh=False, timeout=15: tokens.pop(0)), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            st, data = google_ai.request("gemini-2.5-flash", {"contents": []}, provider="vertex")
        self.assertEqual(st, 200)
        self.assertEqual(calls, ["Bearer tok-1", "Bearer tok-2"])

    # 3: URL 生成（自動リンク化の影響を受けない）と Safety 既定
    def test_3_vertex_url_and_safety_categories(self):
        u_global = google_ai.vertex_generate_url("my-proj", "global", "gemini-2.0-flash-001")
        self.assertTrue(u_global.startswith(
            google_ai._https("aiplatform.googleapis",
                             "/v1beta1/projects/my-proj/locations/global/")))
        self.assertIn("gemini-2.0-flash-001:generateContent", u_global)
        self.assertNotIn("[", u_global)
        self.assertNotIn("](", u_global)

        u_reg = google_ai.vertex_generate_url("my-proj", "asia-northeast1", "gemini-2.5-pro")
        self.assertTrue(u_reg.startswith(
            google_ai._https("asia-northeast1-aiplatform.googleapis", "/")))

        v_safety = google_ai._safety_settings(True, provider="vertex", threshold="OFF")
        self.assertEqual(len(v_safety), 4)
        self.assertTrue(all(x["category"] != "HARM_CATEGORY_JAILBREAK" for x in v_safety))
        self.assertTrue(all(x["threshold"] == "BLOCK_NONE" for x in v_safety))
        g_safety = google_ai._safety_settings(True, provider="google")
        self.assertEqual(len(g_safety), 5)
        self.assertTrue(all(x["threshold"] == "OFF" for x in g_safety))

    # 4: merge_defaults とモデル解決
    def test_4_merge_defaults_and_active_model(self):
        st = server.merge_defaults({"ai_provider": "vertex", "vertex_model": "gemini-2.5-pro",
                                    "vertex_choices_model": "gemini-2.5-flash-lite"})
        self.assertEqual(st["ai_provider"], "vertex")
        self.assertTrue(server.is_api_provider(st))
        self.assertFalse(server.is_api_provider({"ai_provider": "antigravity"}))
        self.assertTrue(server.is_api_provider({"ai_provider": "google"}))
        self.assertEqual(server.active_api_model(st, "user"), "gemini-2.5-pro")
        self.assertEqual(server.active_api_model(st, "choices"), "gemini-2.5-flash-lite")
        self.assertEqual(server._api_model_setting(st, "choices"), "gemini-2.5-flash-lite")
        st_empty = server.merge_defaults({"ai_provider": "vertex"})
        self.assertEqual(server.active_api_model(st_empty, "user"), "gemini-2.5-flash")
        self.assertEqual(server._api_model_setting(st_empty, "user"), "")

    # 5: 正規表現（fullmatch・末尾改行拒否）
    def test_5_regex_validation(self):
        self.assertIsNotNone(google_ai.MODEL_ID_RE.fullmatch("gemini-2.0-flash-001"))
        self.assertIsNotNone(google_ai.MODEL_ID_RE.fullmatch("gemini-2.0-flash@001"))
        self.assertIsNone(google_ai.MODEL_ID_RE.fullmatch("gemini-2.0-flash\n"))
        self.assertIsNone(google_ai.MODEL_ID_RE.fullmatch("bad/model"))
        self.assertIsNotNone(google_ai.PROJECT_ID_RE.fullmatch("my-gcp-project-123"))
        self.assertIsNone(google_ai.PROJECT_ID_RE.fullmatch("123-starts-with-number"))
        self.assertIsNone(google_ai.PROJECT_ID_RE.fullmatch("my-gcp-project\n"))
```

---

## 9. 追加回帰・E2E テスト（ケース 6〜24）

```python
    # 6: 認証ゼロでは .env を書かない
    def test_6_save_requires_auth(self):
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        with patch("google_ai._default_adc_path", return_value=""):
            with self.assertRaises(ValueError):
                google_ai.save_vertex_config(sa_json=None, project_id="my-proj", location="global")
            self.assertFalse(os.path.exists(google_ai.ENV_FILE))
            self.assertNotIn("VERTEX_PROJECT_ID", os.environ)

    # 7: 保存の原子性（不正 project_id で既存 SA を壊さない）と旧パス消去
    def test_7_atomic_save_and_cleanup_old_sa_path(self):
        google_ai._update_env_vars({"VERTEX_SA_JSON_PATH": "/old/custom/sa.json"})
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            cfg = google_ai.save_vertex_config(sa_json="{}", project_id="my-proj", location="global")
        env = google_ai.read_env_file()
        self.assertNotIn("VERTEX_SA_JSON_PATH", env)
        self.assertEqual(cfg["project_id"], "my-proj")

        bad_sa = dict(SAMPLE_SA, client_email="other@demo-gcp-proj.iam.gserviceaccount.com")
        with patch("google_ai.parse_and_validate_sa_json", return_value=bad_sa):
            with self.assertRaises(ValueError):
                google_ai.save_vertex_config(sa_json="{}", project_id="123-invalid-proj",
                                             location="global")
        with open(os.path.join(self.tmp.name, "vertex_sa.json"), "r", encoding="utf-8") as f:
            saved_sa = json.load(f)
        self.assertEqual(saved_sa["client_email"], SAMPLE_SA["client_email"])

    # 8: 設定削除後は ADC があっても configured=False
    def test_8_delete_config_with_adc_becomes_unconfigured(self):
        adc_file = os.path.join(self.tmp.name, "adc.json")
        with open(adc_file, "w", encoding="utf-8") as f:
            json.dump({"type": "authorized_user", "client_id": "c", "client_secret": "s",
                       "refresh_token": "r", "quota_project_id": "adc-quota-proj"}, f)
        with patch("google_ai._default_adc_path", return_value=adc_file):
            google_ai.save_vertex_config(sa_json=None, project_id="my-proj", location="global")
            self.assertTrue(google_ai.vertex_configured())
            google_ai.delete_vertex_config()
            cfg = google_ai.load_vertex_config()
            self.assertFalse(cfg["configured"])
            self.assertEqual(cfg["adc_project_hint"], "adc-quota-proj")

    # 9: choices の slot 明示とファイル名
    def test_9_choices_slot_is_explicit(self):
        s = {"id": "sid_slot",
             "setting": {"ai_provider": "vertex", "vertex_model": "gemini-2.5-flash",
                         "choices_erotic_auto": True},
             "messages": [{"id": "a1", "role": "assistant", "text": "x",
                           "choices_status": "pending", "choices_type": "erotic"}]}
        with patch("server.load", return_value=s), patch("server.list_ids", return_value=["sid_slot"]):
            tasks = server._google_pick_tasks()
            self.assertEqual([t[2] for t in tasks], ["choices"])
        self.assertEqual(server._google_choice_type(s["messages"][0]), "erotic")
        name = server._google_write_outbox("sid_slot", "a1", "choices", {"choices": []}, slot="erotic")
        try:
            self.assertIn("_choices_erotic_", name)
            self.assertEqual(server.choices_slot_of({"choices": []}, {}, name), "erotic")
        finally:
            p = os.path.join(server.OUTBOX, name)
            if os.path.exists(p):
                os.remove(p)

    # 10: choices 出力テキストの3分岐と outbox 指示除去
    def test_10_choices_output_text_branches(self):
        s = {"id": "sid_ot", "setting": {"suggest_choices": True}, "messages": []}
        a = {"id": "a1", "role": "assistant", "text": "x"}
        u = {"id": "u1", "role": "user", "text": "y"}
        s["messages"] = [u, a]
        regen = server._google_choices_output_text(s, a, erotic=True)
        self.assertIn("choices", regen)
        self.assertNotIn("outbox/", regen)
        par = server._google_choices_output_text(s, u, erotic=False)
        self.assertNotIn("outbox/", par)
        self.assertIn("JSON出力ルール", par)

    # 11: API プロバイダでは sent 台帳を触らない
    def test_11_note_reply_skips_api_provider(self):
        s = {"id": "sid_sent", "setting": {"ai_provider": "vertex"}, "messages": []}
        with patch("server.read_sent") as rd, patch("server.write_sent") as wr:
            server.note_reply(s, "u1", {"id": "a1", "role": "assistant", "text": "x"})
            rd.assert_not_called()
            wr.assert_not_called()
        with patch("server.read_sent") as rd2, patch("server.write_sent") as wr2:
            server.mark_choices_sent(s)
            rd2.assert_not_called()
            wr2.assert_not_called()

    # 12: 削除時のパージ／仮想 sid でも status が動く
    def test_12_delete_purges_and_virtual_sid_status_works(self):
        v_sid = "sid_virtual_test"
        server.deleted_sessions.discard(v_sid)
        server._set_google_status(v_sid, "user", "requesting", model="gemini-2.5-flash")
        self.assertIn(v_sid, server.google_status)
        server._google_clear_status(v_sid, "user")
        self.assertNotIn(v_sid, server.google_status)
        server._set_google_status(v_sid, "user", "requesting")
        server.delete_session_all(v_sid, to_trash=False)
        self.assertNotIn(v_sid, server.google_status)
        server._set_google_status(v_sid, "user", "requesting")
        self.assertNotIn(v_sid, server.google_status)
        server.deleted_sessions.discard(v_sid)

    # 12b (F-2): 既存 _google_clear_status のタスクマップ方式が維持されている
    def test_12b_clear_status_keeps_user_entry(self):
        sid = "sid_clear_keep"
        server.deleted_sessions.discard(sid)
        server._set_google_status(sid, "user", "requesting", model="m-a")
        server._set_google_status(sid, "choices", "waiting_import", model="m-b")
        server._google_clear_status(sid, "choices")
        top = server.google_status[sid]
        self.assertEqual(top["task_type"], "user")
        self.assertNotIn("choices", top.get("tasks", {}))
        server._google_clear_status(sid)

    # 13: transport=rest で REST 経路を通る
    def test_13_transport_switch_rest(self):
        os.environ["RPCHAT_GEMINI_TRANSPORT"] = "rest"
        self.assertFalse(google_ai._should_use_sdk("google"))
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'
        resp.__enter__.return_value = resp
        with patch("google_ai.load_key", return_value=("AIzaSyDummyKey12345", "env")), \
             patch("urllib.request.urlopen", return_value=resp) as uo:
            st, data = google_ai.request("gemini-2.5-flash", {"contents": []}, provider="google")
            self.assertEqual(st, 200)
            uo.assert_called_once()

    # 14: Vertex に OFF を送らない／thinking と safety の段階救済
    def test_14_fallbacks_never_send_off_to_vertex(self):
        payload = {"contents": [],
                   "generationConfig": {"maxOutputTokens": 2048,
                                        "thinkingConfig": {"thinkingBudget": 128}},
                   "safetySettings": google_ai._safety_settings(True, provider="vertex")}
        seen = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            seen.append(copy.deepcopy(pl))
            if len(seen) == 1:
                raise google_ai.GoogleAPIError("Invalid thinkingConfig thinking_budget",
                                               status=400, retryable=False)
            if len(seen) == 2:
                raise google_ai.GoogleAPIError("Invalid safety threshold BLOCK_NONE",
                                               status=400, retryable=False)
            return 200, {"candidates": []}

        with patch("google_ai.request", side_effect=fake_req):
            st, _ = google_ai._request_with_fallbacks("gemini-2.5-pro", payload, 30, "vertex", True)
        self.assertEqual(st, 200)
        self.assertEqual(len(seen), 3)
        for pl in seen:
            for item in pl.get("safetySettings") or []:
                self.assertNotEqual(item.get("threshold"), "OFF")
        self.assertNotIn("thinkingConfig", seen[1]["generationConfig"])
        # F-7: BLOCK_NONE が通らない場合は BLOCK_ONLY_HIGH に落ちる（丸ごと外さない）
        self.assertEqual(seen[2]["safetySettings"][0]["threshold"], "BLOCK_ONLY_HIGH")

    # 15: generate(provider="vertex") の2段 fetch と thoughtSignature 保持
    def test_15_generate_vertex_e2e_preserves_thought_signature(self):
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                         project_id="demo-gcp-proj", location="global")
        step1 = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "thinking...", "thought": True, "thoughtSignature": "sig-xyz-999"},
            {"functionCall": {"name": "fetch_prompt", "args": {}}}]},
            "finishReason": "STOP"}]}
        step2 = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"text":"こんにちは"}'}]}, "finishReason": "STOP"}]}
        captured = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            captured.append((provider, copy.deepcopy(pl)))
            return (200, step1) if len(captured) == 1 else (200, step2)

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-flash", "本文プロンプト", "システム指示",
                                     fetch=True, provider="vertex")
        self.assertIsNone(res.get("error"))          # F-9
        self.assertTrue(res.get("fetch_called"))
        self.assertIn("elapsed_ms", res)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0][0], "vertex")
        self.assertEqual(captured[1][1]["contents"][1]["parts"][0].get("thoughtSignature"),
                         "sig-xyz-999")

    # 16: 2段目 400（tools 起因）で直接生成へフォールバック
    def test_16_generate_step2_400_falls_back_to_direct(self):
        step1 = {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "fetch_prompt", "args": {}}}]},
            "finishReason": "STOP"}]}
        direct = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"text":"直接生成OK"}'}]}, "finishReason": "STOP"}]}
        calls = []

        def fake_req(model, pl, timeout=180, *, provider="google"):
            calls.append(copy.deepcopy(pl))
            if len(calls) == 1:
                return 200, step1
            if len(calls) == 2:
                raise google_ai.GoogleAPIError("functionResponse not supported",
                                               status=400, retryable=False)
            return 200, direct

        with patch("google_ai.request", side_effect=fake_req):
            res = google_ai.generate("gemini-2.5-pro", "プロンプト", fetch=True, provider="vertex")
        self.assertIsNone(res.get("error"))          # F-9
        self.assertFalse(res.get("fetch_called"))
        self.assertEqual(len(calls), 3)

    # 17: list_models(vertex) で SDK 失敗 → REST 成功時にエラーが消える
    def test_17_list_models_clears_sdk_error_on_rest_success(self):
        with patch("google_ai.parse_and_validate_sa_json", return_value=SAMPLE_SA):
            google_ai.save_vertex_config(sa_json=json.dumps(SAMPLE_SA),
                                         project_id="demo-gcp-proj", location="global")
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = (b'{"publisherModels":[{"name":"publishers/google/models/gemini-2.5-flash",'
                                  b'"displayName":"Gemini 2.5 Flash"}]}')
        resp.__enter__.return_value = resp
        with patch("google_ai._should_use_sdk", return_value=True), \
             patch("google_ai._sdk_client", side_effect=RuntimeError("SDK list failed")), \
             patch("google_ai.get_vertex_access_token",
                   return_value=("tok", "service_account", "")), \
             patch("urllib.request.urlopen", return_value=resp):
            models = google_ai.list_models(refresh=True, provider="vertex")
        self.assertTrue(any(m["id"] == "gemini-2.5-flash" for m in models))
        self.assertEqual(google_ai.last_models_error(provider="vertex"), "")

    # 18: SDK 変換（署名検知・snake_case 拒否）
    def test_18_sdk_signature_and_snake_case_fallback(self):
        snake = MagicMock()
        snake.to_json_dict.return_value = {"candidates": [{"content": {"parts": [
            {"function_call": {"name": "fetch_prompt"}}]}, "finish_reason": "STOP"}]}
        self.assertIsNone(google_ai._sdk_response_to_dict(snake))
        camel = MagicMock()
        camel.to_json_dict.return_value = {"candidates": [{"content": {"parts": [
            {"text": "ok"}]}, "finishReason": "STOP"}]}
        self.assertIsNotNone(google_ai._sdk_response_to_dict(camel))

    # 19 (F-1): inflight キーが _google_pick_tasks と完全一致する
    def test_19_choices_inflight_key_matches_pick_tasks(self):
        sid = "sid_inflight_test"
        m = {"id": "a_new", "role": "assistant", "choices_status": "processing",
             "choices_type": "erotic", "choices_orig_id": "u_orig"}
        key = server._google_inflight_key(sid, "choices", m)
        self.assertEqual(key, f"{sid}:choices:erotic:u_orig")

        s = {"id": sid,
             "setting": {"ai_provider": "vertex", "vertex_model": "gemini-2.5-flash"},
             "messages": [m]}
        with patch("server.load", return_value=s), patch("server.list_ids", return_value=[sid]):
            tasks = server._google_pick_tasks()
        self.assertEqual([t[3] for t in tasks], [key])

        with server.google_inflight_guard:
            server.google_inflight.add(key)
        try:
            self.assertTrue(server._is_google_task_inflight(sid, "choices", m))
            self.assertEqual(server._google_inflight_key(sid, "user", {"id": "u1"}),
                             f"{sid}:user:u1")
            self.assertEqual(server._google_inflight_key(sid, "image_refine", {"id": "a1"}),
                             f"{sid}:image_refine:a1")
        finally:
            with server.google_inflight_guard:
                server.google_inflight.discard(key)

    # 20: processing → pending の巻き戻し
    def test_20_reset_session_api_tasks_to_pending(self):
        s = {"id": "sid_rst", "setting": {"ai_provider": "vertex"},
             "messages": [
                 {"id": "u1", "role": "user", "status": "processing",
                  "claimed_ts": "2026-01-01", "google_retry": "err"},
                 {"id": "a1", "role": "assistant", "choices_status": "processing",
                  "choices_claimed_ts": "2026-01-01"}]}
        server._reset_session_api_tasks_to_pending(s)
        self.assertEqual(s["messages"][0]["status"], "pending")
        self.assertNotIn("claimed_ts", s["messages"][0])
        self.assertNotIn("google_retry", s["messages"][0])
        self.assertEqual(s["messages"][1]["choices_status"], "pending")

    # 21 (2-7): memory_extract / consolidation が _google_fail で error になる
    def test_21_memory_task_fail_does_not_loop(self):
        s = {"id": "sid_mem", "setting": {"ai_provider": "vertex"},
             "messages": [{"id": "a1", "role": "assistant", "memory_status": "processing"},
                          {"id": "a2", "role": "assistant", "consolidation_status": "processing"}]}
        with patch("server.load", return_value=s), patch("server.save"):
            server._google_fail("sid_mem", "memory_extract", "a1", "unsupported", retryable=False)
            server._google_fail("sid_mem", "consolidation", "a2", "unsupported", retryable=False)
        self.assertEqual(s["messages"][0]["memory_status"], "error")
        self.assertEqual(s["messages"][1]["consolidation_status"], "error")

    # 22: app.js の checked() が code を伝搬する（静的検証）
    def test_22_app_js_checked_propagates_error_code(self):
        path = os.path.join(google_ai.ROOT, "public", "app.js")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                js = f.read()
            self.assertIn("result.code", js)
            self.assertIn("err.code", js)
            for needle in ("vertexStatusLoaded", "vertexModels", "syncGoogleSettingsUI",
                           "vertex:${", "vertex:"):
                self.assertIn(needle, js)

    # 23: _api_model_setting はネットワークを叩かない（ロック内で安全）
    def test_23_api_model_setting_has_no_network(self):
        st = {"ai_provider": "google", "google_model": "", "google_choices_model": ""}
        with patch("google_ai.list_models", side_effect=AssertionError("network called")):
            self.assertEqual(server._api_model_setting(st, "user"), "")
            self.assertEqual(server._api_model_setting(st, "choices"), "")

    # 24: 失効スイーパ保護（choices キー）が実際に効く
    def test_24_load_sweeper_skips_inflight_choices(self):
        sid = "sid_sweep"
        m = {"id": "a1", "role": "assistant", "text": "x",
             "choices_status": "processing", "choices_type": "normal",
             "choices_claimed_ts": "2000-01-01T00:00:00", "choices_ts": "2000-01-01T00:00:00"}
        key = server._google_inflight_key(sid, "choices", m)
        with server.google_inflight_guard:
            server.google_inflight.add(key)
        try:
            self.assertTrue(server._is_google_task_inflight(sid, "choices", m))
        finally:
            with server.google_inflight_guard:
                server.google_inflight.discard(key)
        self.assertFalse(server._is_google_task_inflight(sid, "choices", m))
```

既存スイートの非回帰確認（すべてパスすること）:

- `test/test_google_ai.py`
- `test/test_google_parallel_choices.py`（`_google_model` 別名・`test_set_google_status_user_priority`）
- `test/test_parallel_choices.py`（`main + provider=google` → `ide_model`）

---

## 10. 実装ステップ

1. **Step 1**: `server.py` の `DEFAULT_SETTING` / `NO_IDE` / `merge_defaults` / `/api/setting` keep、`google_ai.py` の `_https` / `_http` / `BASE` 群 / `MODEL_ID_RE` / `PROJECT_ID_RE` / `LOCATION_RE` / `EXCLUDE_MODEL_PARTS`（既存維持）＋`VERTEX_EXCLUDE_MODEL_PARTS`。
2. **Step 2**: `google_ai.py` §3.1〜§3.5。`test/test_google_ai.py` がグリーンであること。
3. **Step 3**: `server.py` §4.1〜§4.5。`test/test_google_parallel_choices.py` / `test/test_parallel_choices.py` がグリーンであること。
4. **Step 4**: `public/index.html` / `public/app.js` / `start.bat` / `start_mobile.bat` / `.env.example`。
5. **Step 5**: `test/test_vertex_ai.py`（全24ケース）を作成し、全スイートを実行。

### 10.1 実装直後に必ず実行する検証コマンド

```bat
python -c "import google_ai, server; print(google_ai.BASE); print(google_ai.GOOGLE_TOKEN_URI); print(google_ai.CLOUD_PLATFORM_SCOPE)"
python -c "import pathlib,sys;bad=[x for x in ('google_ai.py','server.py','test/test_vertex_ai.py') if pathlib.Path(x).exists() and chr(91)+'h'+'ttp' in pathlib.Path(x).read_text(encoding='utf-8')];print('link-broken:',bad);sys.exit(1 if bad else 0)"
python -m unittest test.test_google_ai test.test_google_parallel_choices test.test_parallel_choices -v
python -m unittest test.test_vertex_ai -v
```

`link-broken: []` と表示され、`BASE` などが正しい URL として表示されることが完了条件（import が通ることも同時に確認できる）。

---

## 11. 受け入れ確認チェックリスト

- [ ] `google_ai.py` / `server.py` / `test/test_vertex_ai.py` に Markdown リンク記法（角括弧＋丸括弧で囲まれた URL）やノーブレークスペース（`\u00a0`）が無い。`google_ai.BASE` が正しい URL を返す。
- [ ] `python -c "import google_ai, server"` が通る（`IndentationError` / `NameError` が無い）。
- [ ] `/api/vertex/config` に認証情報なしで POST → 400、`.env` 不変。不正 `project_id` → 400 かつ `data/vertex_sa.json` 不変。
- [ ] SA 保存後、`.env` の `VERTEX_SA_JSON_PATH` が消え、`/api/vertex/status` が `configured: true` と `sa_basename` を返す（フルパス・`fingerprint` は返さない）。
- [ ] Vertex へ `threshold: "OFF"` が一切送られない。safety 400 は `BLOCK_ONLY_HIGH` → 除去の順で救済される。
- [ ] Vertex 未設定で `#aiProvider` / `#ideModel` から Vertex を選ぶと `#vertexConfigModal` が自動で開く（`e.code === "vertex_not_configured"`）。
- [ ] `#vertexBox` の表示切替・`#vertexModel` の一覧・`#vertexFetch` / `#vertexSafetyOff` / `#vertexMaxTokens` が `state.setting` と同期する（`syncGoogleSettingsUI`）。
- [ ] `#vertexConfigSet` / `Save` / `Cancel` / `Delete` / `ModelsReload` / `Apply` / `vertexSaFile` がすべて動作する。
- [ ] Vertex 設定削除時、ADC があっても `configured: false`。使用中チャンネルが Antigravity に戻り、`processing` → `pending`、`sent` に `reset: True`。
- [ ] `/api/ai/provider` は同一プロバイダでも応答待ち中は 409。正常時は `"ok": true` を含む。
- [ ] `/api/ide/model` は `main + google` → `ide_model`、`main + vertex` → `vertex_model`、`choices + vertex` → `vertex_choices_model`。
- [ ] 🔞選択肢が `choices_erotic` に入る（`rep["slot"]` と outbox ファイル名の両方）。
- [ ] `_google_clear_status(sid, "choices")` で生成中の `user` 表示が消えない（既存テストが通る）。
- [ ] `_google_inflight_key(sid, "choices", m)` が `{sid}:choices:{ctype}:{orig_mid}` を返し、`_google_pick_tasks` が出すキーと一致する。
- [ ] Vertex の長時間生成中に `load()` が呼ばれても `pending` に戻らず、偽のタイムアウト通知も出ない。
- [ ] `memory_extract` / `consolidation` が API プロバイダで無限リトライしない（`error` に落ちる or 除去される）。
- [ ] Google / Vertex 使用時の本文生成で `save_rag_hits` が呼ばれる。
- [ ] チャンネル削除・プロバイダ切替が生成中に起きても `outbox/` に孤児ファイルが残らず、`google_status` にゾンビが残らない。
