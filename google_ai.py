"""
Google AI Studio (Gemini Developer API) および Google Cloud Vertex AI プロバイダ用のクライアント。

- Google AI Studio: GEMINI_API_KEY で直接呼び出し
- Vertex AI: サービスアカウントJSON または ADC で Vertex 上の Gemini を呼び出し
- SDK（google-genai）優先＋REST 透過フォールバック
"""
import base64
import copy
import enum
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
# Gemini 2.x / 一部 3.x は現行の Vertex で 404（提供終了・リージョン限定）になるため、
# 既定は「実在が確認できる新しい Flash 系」を優先し、後の probe（実疎通）で確定させる。
VERTEX_PREFERRED_MODELS = (
    "gemini-4-hydrogen",
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
    "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite",
    "gemini-2.5-flash", "gemini-2.0-flash",
)
# 未プローブ時のフォールバック既定値は確実に存在する安定モデルにする
VERTEX_DEFAULT_MODEL = "gemini-2.5-flash"


def _is_thinking_model(model_id):
    low = str(model_id or "").lower()
    return any(k in low for k in ("2.5", "3.", "3-", "4.", "4-", "thinking"))


VALID_THINKING_LEVELS = ("auto", "minimal", "low", "medium", "high", "xhigh")
VALID_CHOICES_THINKING_LEVELS = ("low", "minimal", "medium", "high", "auto", "same")


def _thinking_family(model_id):
    """モデル名から思考パラメータの世代・対応範囲を判定する
    戻り値:
      'none'            : 思考非対応、または 2.0-thinking 等の level/budget 非対応モデル、Gemma等
      'budget_25_flash' : Gemini 2.5 Flash 系 (thinkingBudget: 0〜24576)
      'budget_25_pro'   : Gemini 2.5 Pro 系 (thinkingBudget: 128〜32768)
      'level_3_flash'   : Gemini 3.0〜3.7 Flash 系 (MINIMAL / LOW / MEDIUM / HIGH)
      'level_38_4'      : Gemini 3.x Pro 系、3.8+ Flash、4 Carbon、-latest 等 (LOW / MEDIUM / HIGH)
      'level_hydrogen'  : Gemini 4 Hydrogen 系 (LOW / MEDIUM / HIGH / XHIGH)
    """
    low = str(model_id or "").lower()

    # 1. 思考パラメータ非対応モデル（2.0系、1.x系、Gemma系）の完全除外
    # ※ _is_thinking_model("gemma-3-27b") は "3-" により True になるため、ここで確実に none に落とす
    if any(k in low for k in ("2.0", "2-0", "1.5", "1-5", "1.0", "1-0", "gemma", "nano", "embedding")):
        return "none"

    # 思考モデル判定（_is_thinking_model または 思考世代の最新エイリアス）
    is_latest_thinking = any(
        k in low for k in ("flash-latest", "flash-lite-latest", "pro-latest", "thinking-latest")
    )
    if not (_is_thinking_model(low) or "2-5" in low or is_latest_thinking):
        return "none"

    # 2. Gemini 2.5 系 (thinkingBudget)
    if "2.5" in low or "2-5" in low:
        return "budget_25_flash" if "flash" in low else "budget_25_pro"

    # 3. Gemini 4 Hydrogen 系 (XHIGH 対応)
    if "hydrogen" in low:
        return "level_hydrogen"

    # 4. Gemini 3.8+ / 4.x / -latest 系 (MINIMAL非対応、LOW / MEDIUM / HIGH)
    # ※ 3.8, 3.9, 4.x, および最新エイリアス（flash-lite-latest は 3.x Flash-Lite 相当として MINIMAL 対応の 5 へ流す）
    if any(k in low for k in ("3.8", "3-8", "3.9", "3-9", "4.", "4-", "pro-latest")) or (
        "flash-latest" in low and "lite" not in low
    ):
        return "level_38_4"

    # 5. Gemini 3.0〜3.7 Flash / Flash-Lite 系 (MINIMAL 対応)
    if "flash" in low:
        return "level_3_flash"

    # 6. その他の 3.x Pro 等
    return "level_38_4"


def resolve_thinking_config(model_id, level="auto"):
    """モデルと指定レベルから (thinkingConfig辞書またはNone, 実効レベル文字列, 推奨最小maxOutputTokens) を返す。
    非対応の段は HTTP 400 を防ぐため自動で有効な隣接段へ丸める。
    """
    fam = _thinking_family(model_id)
    lv = str(level or "auto").strip().lower()
    if lv not in VALID_THINKING_LEVELS:
        lv = "auto"

    if fam == "none":
        return None, "none", 0

    # 1. Gemini 2.5 系: thinkingBudget (トークン数) に変換
    if fam in ("budget_25_flash", "budget_25_pro"):
        if lv == "auto":
            return None, "auto(budget)", 0
        min_b = 0 if fam == "budget_25_flash" else 128
        budget_map = {
            "minimal": min_b,
            "low": 1024,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
        }
        b = budget_map.get(lv, 4096)
        # 思考バジェットが maxOutputTokens 以上になると HTTP 400 または本文空切れになるため、
        # b > 0 のときは必ず「思考バジェット + 本文用余白(4096)」かつ最低 8192 以上を確保する
        min_out = max(8192, b + 4096) if b > 0 else 0
        return {"thinkingBudget": b}, f"budget:{b}", min_out

    # 2. Gemini 3.x / 4.x 系: thinkingLevel に変換
    default_lv = "HIGH" if fam == "level_hydrogen" else "MEDIUM"
    if lv == "auto":
        return None, f"auto({default_lv})", 0

    eff = lv.upper()
    # MINIMAL は 3.0〜3.7 Flash 系のみ対応。Pro 系や 3.8 / 4 系では LOW に丸める
    if eff == "MINIMAL" and fam != "level_3_flash":
        eff = "LOW"
    # XHIGH は Hydrogen のみ対応。他モデルでは HIGH に丸める
    if eff == "XHIGH" and fam != "level_hydrogen":
        eff = "HIGH"

    if eff == "XHIGH":
        min_out = 32768
    elif eff == "HIGH":
        min_out = 16384
    else:
        min_out = 0

    return {"thinkingLevel": eff}, eff, min_out


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

# 一覧APIが失敗しても選択肢を出すための予備（実際の提供状況は probe で判定する）
VERTEX_CURATED_MODELS = [
    {"id": m, "label": m, "inputTokenLimit": 1048576, "outputTokenLimit": 65536,
     "thinking": _is_thinking_model(m)}
    for m in VERTEX_PREFERRED_MODELS
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

# Vertex の「そのリージョンで本当に generateContent できるか」の実測キャッシュ
#   _vertex_probe_cache[(fingerprint, location, model)] = {"ok":bool, "status":int, "error":str, "ts":float}
_vertex_probe_cache = {}
_vertex_probe_lock = threading.Lock()
VERTEX_PROBE_TTL = 600.0
# 404 NOT_FOUND 時に自動で切り替えたリージョン（fingerprint -> location）
_vertex_location_override = {}
_vertex_location_lock = threading.Lock()


class GoogleAPIError(Exception):
    def __init__(self, message, status=None, code="", retryable=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = bool(retryable)


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


def configured():
    """Google AI Studio の API キーが設定済みか"""
    key, _ = load_key()
    return bool(key)


def save_key(key, path=None):
    key = (key or "").strip()
    if not key or len(key) < 10 or any(c.isspace() for c in key):
        raise ValueError("APIキーの形式が不正です")

    with _env_lock:
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
    with _env_lock:
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


def _default_adc_path(env_path=None):
    env_adc = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
               or read_env_file(env_path or ENV_FILE).get("GOOGLE_APPLICATION_CREDENTIALS")
               or "").strip()
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
        adc = _default_adc_path(env_path)
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
    _fp = f"{auth_type}:{fp_src}:{project_id}:{location}"
    with _vertex_location_lock:
        eff_loc = _vertex_location_override.get(_fp) or location
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
        "effective_location": eff_loc,
        "location_note": (f"設定は {location} ですが、404 のため {eff_loc} を使用中"
                          if eff_loc and eff_loc != location else ""),
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
    pk = str(sa_info.get("private_key") or "").replace("\\n", "\n").strip().encode("utf-8")
    key = serialization.load_pem_private_key(pk, password=None)
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
                    quota = str(a.get("project_id") or a.get("quota_project_id") or "")
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
                    quota = ""
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


def key_hint(key=None):
    key = (load_key()[0] if key is None else key) or ""
    return key[-4:] if len(key) >= 4 else ""


class _SdkFallbackToRest(Exception):
    """SDK 変換で情報が失われる payload を REST へフォールバックさせる内部例外"""


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


def effective_vertex_location(cfg=None):
    """実際に使うリージョン。404 時に自動切替した global を優先する"""
    cfg = cfg or load_vertex_config()
    loc = (cfg.get("location") or VERTEX_DEFAULT_LOCATION).strip()
    with _vertex_location_lock:
        return _vertex_location_override.get(cfg.get("fingerprint") or "") or loc


def _set_vertex_location_override(fingerprint, location):
    if not fingerprint or not location:
        return
    with _vertex_location_lock:
        if _vertex_location_override.get(fingerprint) != location:
            _vertex_location_override[fingerprint] = location
            print(f"[vertex] 404 NOT_FOUND のため location を '{location}' に切り替えました")


def _clear_vertex_location_override(fingerprint=None):
    with _vertex_location_lock:
        if fingerprint is None:
            _vertex_location_override.clear()
        else:
            _vertex_location_override.pop(fingerprint, None)


def _is_model_not_found(status, msg):
    """404（モデル未提供 / リージョン違い / 権限なし）かどうか"""
    if int(status or 0) == 404:
        return True
    low = str(msg or "").lower()
    return ("not_found" in low or "was not found" in low) and "model" in low


def _json_default(obj):
    """json.dumps の default 引数用。bytes や Enum を安全に直列化する"""
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if hasattr(obj, "to_json_dict") and callable(obj.to_json_dict):
        return obj.to_json_dict()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    return str(obj)


def _vertex_rest_post(location, model, payload, timeout=60, cfg=None):
    """probe 専用の素の REST POST（SDK・フォールバックを経由しない）"""
    cfg = cfg or load_vertex_config()
    pid = cfg.get("project_id") or ""
    url = vertex_generate_url(pid, location, model)
    tok, auth_type, quota = get_vertex_access_token(timeout=timeout)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}
    if auth_type == "adc":
        headers["x-goog-user-project"] = quota or pid
    body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace"))


def probe_vertex_model(model, location=None, timeout=10, refresh=False, cfg=None):
    """指定モデルがそのリージョンで実際に generateContent できるかを実測する。
    200/400 は「モデルは存在する」（=使用可能）、404 は「無い・権限無し・リージョン違い）。
    戻りは dict: {"id", "location", "available", "status", "error"}
    """
    cfg = cfg or load_vertex_config()
    if not cfg.get("configured"):
        return {"id": model, "location": location or "", "available": False,
                "status": 0, "error": "Vertex AIの認証またはプロジェクトIDが未設定です"}
    model = (model or "").strip()
    loc = (location or effective_vertex_location(cfg)).strip() or VERTEX_DEFAULT_LOCATION
    ck = (cfg.get("fingerprint") or "", loc, model)
    now_ts = time.time()
    with _vertex_probe_lock:
        hit = _vertex_probe_cache.get(ck)
        if hit and not refresh and now_ts - hit["ts"] < VERTEX_PROBE_TTL:
            return {k: hit[k] for k in ("id", "location", "available", "status", "error")}

    payload = {
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 1},
    }
    status, error = 0, ""
    try:
        status, _ = _vertex_rest_post(loc, model, payload, timeout=timeout, cfg=cfg)
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read().decode("utf-8", "replace")
        error = _error_message(e.code, raw)[0]
    except Exception as e:
        status, error = 0, f"確認できません: {e}"
    available = status in (200, 400, 429)   # 400 や 429（レート制限）でもモデル自体は存在する
    out = {"id": model, "location": loc, "available": available,
           "status": status, "error": "" if available else error}

    if status == 404 and loc != VERTEX_DEFAULT_LOCATION:
        g_out = probe_vertex_model(model, location=VERTEX_DEFAULT_LOCATION,
                                   timeout=timeout, refresh=refresh, cfg=cfg)
        if g_out.get("available"):
            _set_vertex_location_override(cfg.get("fingerprint"), VERTEX_DEFAULT_LOCATION)
            with _vertex_probe_lock:
                _vertex_probe_cache[ck] = dict(g_out, ts=now_ts)
            return g_out

    if status in (200, 400, 403, 404):
        with _vertex_probe_lock:
            _vertex_probe_cache[ck] = dict(out, ts=now_ts)

    return out


def servable_vertex_models(location=None, timeout=10, refresh=False, models=None):
    """一覧の各モデルを実測して available を付ける（モデル×リージョンの真の提供状況）"""
    cfg = load_vertex_config()
    loc = (location or effective_vertex_location(cfg)).strip() or VERTEX_DEFAULT_LOCATION
    rows = list(models if models is not None else list_models(refresh=refresh, timeout=timeout, provider="vertex", probe=False))
    if not rows:
        return []

    def one(m):
        mid = m["id"] if isinstance(m, dict) else str(m)
        info = probe_vertex_model(mid, loc, timeout=timeout, refresh=refresh, cfg=cfg)
        base = m if isinstance(m, dict) else {"id": mid, "label": mid}
        return dict(base, **{"available": info["available"], "probe_status": info["status"],
                             "probe_error": info["error"], "location": info.get("location") or loc})

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as ex:
            out = list(ex.map(one, rows))
    except Exception:
        out = [one(m) for m in rows]
    ok_rows = [m for m in out if m.get("available")]
    return ok_rows or out


def default_vertex_model():
    """設定未指定時のモデル。実測で使えると分かっているものを優先し、
    不明なら優先リストの先頭を返す（ネットワークは呼ばない）"""
    cfg = load_vertex_config()
    fp = cfg.get("fingerprint") or ""
    loc = effective_vertex_location(cfg)
    ok_ids = set()
    with _vertex_probe_lock:
        for (cfp, cloc, cid), v in _vertex_probe_cache.items():
            if cfp == fp and cloc == loc and v.get("available"):
                ok_ids.add(cid)
    if ok_ids:
        for p in VERTEX_PREFERRED_MODELS:
            if p in ok_ids:
                return p
        return sorted(ok_ids)[0]
    return VERTEX_DEFAULT_MODEL


def invalidate_models_cache():
    with _models_lock:
        _models_cache.update(ts=0.0, key_hint="", models=[], error="")
        _models_cache_by_key.clear()
    with _vertex_probe_lock:
        _vertex_probe_cache.clear()
    _clear_vertex_location_override()


def last_models_error(provider="vertex"):
    """現在の設定 fingerprint に対応するモデル一覧エラーのみを返す"""
    if provider == "vertex":
        cfg = load_vertex_config()
        loc = effective_vertex_location(cfg)
        ck = ("vertex", cfg.get("fingerprint") or "", cfg.get("project_id") or "", loc)
        with _models_lock:
            return (_models_cache_by_key.get(ck) or {}).get("error") or ""
    with _models_lock:
        return _models_cache.get("error") or ""


def _sdk_client(provider, cfg=None, api_key=""):
    if not _should_use_sdk(provider):
        return None
    if provider == "vertex":
        cfg = cfg or load_vertex_config()
        loc = effective_vertex_location(cfg)
        fp = cfg.get("fingerprint") or ""
        pid = cfg.get("project_id") or ""
        key = ("vertex", fp, pid, loc)
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
                cli = genai.Client(vertexai=True, project=pid,
                                   location=loc, credentials=creds)
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


def _sanitize_sdk_value(v):
    """SDK 応答に含まれる bytes（thoughtSignature 等）や Enum を JSON 直列化可能な型へ再帰変換"""
    if isinstance(v, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(v)).decode("ascii")
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _sanitize_sdk_value(sub) for k, sub in v.items()}
    if isinstance(v, list):
        return [_sanitize_sdk_value(sub) for sub in v]
    if isinstance(v, tuple):
        return tuple(_sanitize_sdk_value(sub) for sub in v)
    if isinstance(v, (set, frozenset)):
        return [_sanitize_sdk_value(sub) for sub in v]
    return v


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
                        if any(isinstance(p, dict) and (
                            "function_call" in p or "inline_data" in p or "thought_signature" in p
                        ) for p in parts):
                            continue
                    if "content" in c0 or "finishReason" in c0:
                        return _sanitize_sdk_value(d)
                elif "promptFeedback" in d and "prompt_feedback" not in d:
                    return _sanitize_sdk_value(d)
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
        tc_cls = getattr(t, "ThinkingConfig", None)
        if tc_cls is None:
            raise _SdkFallbackToRest("SDK ThinkingConfig not available in genai_types")
        if "thinkingLevel" in tc and tc["thinkingLevel"]:
            fields = (getattr(tc_cls, "model_fields", None)
                      or getattr(tc_cls, "__fields__", None) or {})
            if fields and "thinking_level" not in fields:
                raise _SdkFallbackToRest("SDK ThinkingConfig does not support thinking_level")
            try:
                tc_obj = tc_cls(thinking_level=str(tc["thinkingLevel"]))
            except Exception:
                raise _SdkFallbackToRest("SDK ThinkingConfig raised error on thinking_level")
            if getattr(tc_obj, "thinking_level", None) is None:
                raise _SdkFallbackToRest("SDK ThinkingConfig ignored thinking_level")
            cfg["thinking_config"] = tc_obj
        elif "thinkingBudget" in tc and tc["thinkingBudget"] is not None:
            cfg["thinking_config"] = tc_cls(thinking_budget=int(tc.get("thinkingBudget", 0)))
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
        if code == 404:
            # 404 はリージョン違いのことがあるので REST 側のリージョン自動切替に任せる
            print(f"[vertex] SDK 404 ({model}) -> REST に切り替えてリージョンを検証します")
            return None, None
        raise GoogleAPIError(_error_message(code, str(e))[0], status=code,
                             code=str(getattr(e, "status", "") or ""), retryable=_retryable(code))
    except Exception as e:
        print(f"[google] SDK 呼び出しから REST へフォールバックします: {e}")
        return None, None


def list_models(refresh=False, timeout=30, *, provider="google", probe=None):
    if provider == "vertex":
        if probe is None or probe:
            return servable_vertex_models(timeout=timeout, refresh=refresh)
        cfg = load_vertex_config()
        if not cfg.get("configured"):
            raise GoogleAPIError("Vertex AIの認証またはプロジェクトIDが未設定です", code="NO_VERTEX_CRED")
        loc = effective_vertex_location(cfg)
        ck = ("vertex", cfg.get("fingerprint") or "", cfg.get("project_id") or "", loc)
        with _models_lock:
            cached = _models_cache_by_key.get(ck)
            if not refresh and cached and cached.get("models") and time.time() - cached["ts"] < 600:
                return list(cached["models"])

        by_id = {}
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
                    _https(_vertex_host_prefix(loc),
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
            name = str(m.get("name") or m.get("id") or "")
            mid = name.rsplit("/", 1)[-1] if "/" in name else name
            if not mid:
                continue
            low = mid.lower()
            if not low.startswith("gemini-"):
                continue
            if any(p in low for p in VERTEX_EXCLUDE_MODEL_PARTS):
                continue
            methods = m.get("supportedGenerationMethods")
            if isinstance(methods, list) and methods and "generateContent" not in methods:
                continue
            by_id[mid] = {
                "id": mid,
                "label": m.get("displayName") or mid,
                "inputTokenLimit": m.get("inputTokenLimit") or 1048576,
                "outputTokenLimit": m.get("outputTokenLimit") or 65536,
                "thinking": bool(m["thinking"] if "thinking" in m else _is_thinking_model(low)),
            }

        if not by_id:
            by_id = {m["id"]: dict(m) for m in VERTEX_CURATED_MODELS}

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


def default_model():
    rows = list_models()
    for pat in (r"^gemini-4-hydrogen$", r"^gemini-3\.8-flash$", r"^gemini-flash-latest$",
                r"^gemini-3\.6-flash$", r"^gemini-3\.5-flash$", r"^gemini-3\.5-flash-lite$"):
        for r in rows:
            if re.search(pat, r["id"]):
                return r["id"]
    return rows[0]["id"] if rows else ""


def _error_message(status, raw):
    text = str(raw or "")
    d = None
    try:
        d = json.loads(text)
    except Exception:
        d = None
    if d is None:
        # SDK の APIError は str(e) が "404 NOT_FOUND. {'error': {...}}" のような
        # 非JSON（Python repr）になるため、中の辞書を拾い直す。
        start = text.find("{")
        if start >= 0:
            frag = text[start:text.rfind("}") + 1]
            try:
                d = json.loads(frag)
            except Exception:
                try:
                    import ast
                    d = ast.literal_eval(frag)
                except Exception:
                    d = None
    if isinstance(d, list) and d and isinstance(d[0], dict):
        d = d[0]
    err = d.get("error") if isinstance(d, dict) else None
    if isinstance(err, dict):
        return (f"HTTP {status}: {err.get('status') or ''} {err.get('message') or ''}").strip(), \
            str(err.get("status") or "")
    # 先頭の "404 NOT_FOUND. " を落として冗長なダンプを避ける
    head = re.sub(r"^\s*\d{3}\s+[A-Z_]+\s*[.:]?\s*", "", text).strip()
    return f"HTTP {status}: {head[:300]}", ""


def _retryable(status):
    """既存判定を維持（401 は含めない）"""
    s = int(status or 0)
    return s in (408, 429) or 500 <= s <= 599


def _vertex_model_missing_message(model, loc_used, loc_plan, detail, cfg):
    """404 をユーザーが直せる文面にする（生のJSONダンプを見せない）"""
    tried = " → ".join(loc_plan) if len(loc_plan) > 1 else loc_used
    lines = [f"Vertex AI でモデル '{model}' を利用できません（試したリージョン: {tried}）。"]
    ok = []
    with _vertex_probe_lock:
        for (cfp, cloc, cid), v in _vertex_probe_cache.items():
            if cfp == (cfg or {}).get("fingerprint") and v.get("available"):
                ok.append(cid)
    if ok:
        ok = sorted(set(ok))
        lines.append("この環境で実際に応答したモデル: " + ", ".join(ok[:8]))
    lines.append("対処: 「モデル一覧を更新」で実際に使えるモデルを選び直してください。"
                 "リージョンは通常 'global' を推奨します。")
    if detail:
        lines.append("詳細: " + str(detail)[:300])
    return "\n".join(lines)


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
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        # 設定リージョンで 404（多くは「そのリージョンには無い」）なら global で再挑戦する。
        # 新しい Gemini は global エンドポイントのみ提供のことがあるため。
        first_loc = effective_vertex_location(cfg)
        loc_plan = [first_loc]
        if first_loc != VERTEX_DEFAULT_LOCATION:
            loc_plan.append(VERTEX_DEFAULT_LOCATION)
        not_found = None
        for loc in loc_plan:
            url = vertex_generate_url(cfg["project_id"], loc, model)
            retry_other_loc = False
            for attempt in range(2):
                tok, auth_type, quota = get_vertex_access_token(
                    force_refresh=(attempt > 0), timeout=timeout)
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}
                if auth_type == "adc":
                    headers["x-goog-user-project"] = quota or cfg["project_id"]
                req = urllib.request.Request(url, data=body, method="POST", headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        if loc != first_loc:
                            _set_vertex_location_override(cfg.get("fingerprint"), loc)
                        return r.status, json.loads(r.read().decode("utf-8", "replace"))
                except urllib.error.HTTPError as e:
                    raw = e.read().decode("utf-8", "replace")
                    if e.code == 401 and attempt == 0:
                        invalidate_vertex_token()
                        continue
                    msg, code = _error_message(e.code, raw)
                    if _is_model_not_found(e.code, msg):
                        not_found = (e.code, msg, loc)
                        retry_other_loc = True
                        break
                    raise GoogleAPIError(msg, status=e.code, code=code,
                                         retryable=_retryable(e.code))
                except GoogleAPIError:
                    raise
                except Exception as e:
                    raise GoogleAPIError(f"Vertex AI に接続できません: {e}", retryable=True)
            if not retry_other_loc:
                break
        loc_used, detail = (not_found[2], not_found[1]) if not_found else (first_loc, "")
        raise GoogleAPIError(
            _vertex_model_missing_message(model, loc_used, loc_plan, detail, cfg),
            status=404, code="MODEL_NOT_FOUND", retryable=False)

    # provider == "google"（既存実装そのまま）
    key, _ = load_key()
    if not key:
        raise GoogleAPIError("APIキーが未設定です", code="NO_KEY")
    url = f"{BASE}/models/{urllib.parse.quote(model, safe='')}:generateContent"
    body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
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


def extract_text(data):
    """GenerateContentResponse から本文テキストとメタ情報を取る。thought は無視。"""
    meta = {"finish_reason": None, "block_reason": None, "safety_ratings": [], "usage": {}}
    if not isinstance(data, dict):
        return "", meta
    um = data.get("usageMetadata") or data.get("usage_metadata")
    if isinstance(um, dict):
        meta["usage"] = dict(um)
        # 思考トークン数を camelCase / snake_case の双方から参照可能にする
        if "thoughts_token_count" in meta["usage"] and "thoughtsTokenCount" not in meta["usage"]:
            meta["usage"]["thoughtsTokenCount"] = meta["usage"]["thoughts_token_count"]
        elif "thoughtsTokenCount" in meta["usage"] and "thoughts_token_count" not in meta["usage"]:
            meta["usage"]["thoughts_token_count"] = meta["usage"]["thoughtsTokenCount"]
    pf = data.get("promptFeedback") or data.get("prompt_feedback")
    if isinstance(pf, dict):
        meta["block_reason"] = pf.get("blockReason") or pf.get("block_reason")
        sr = pf.get("safetyRatings") or pf.get("safety_ratings")
        if isinstance(sr, list):
            meta["safety_ratings"] = sr
    cands = data.get("candidates")
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        c0 = cands[0]
        meta["finish_reason"] = c0.get("finishReason") or c0.get("finish_reason")
        sr = c0.get("safetyRatings") or c0.get("safety_ratings")
        if isinstance(sr, list):
            meta["safety_ratings"] = sr
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
        th = threshold if threshold in ("BLOCK_NONE", "BLOCK_ONLY_HIGH") else "BLOCK_NONE"
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
        "functionresponse", "function_response", "function response",
        "functioncall", "function_call", "function call",
        "functiondeclarations", "function_declarations", "function declarations",
        "toolconfig", "tool_config", "tool config",
        "thoughtsignature", "thought_signature", "thought signature",
    ))


def _is_thinking_400(msg):
    low = str(msg or "").lower()
    return any(w in low for w in ("thinking", "thinking_config", "thinkingconfig",
                                  "thinking_budget", "thinkingbudget",
                                  "thinking_level", "thinkinglevel"))


def _step1_generation_config(model):
    """1段目（fetch_prompt 呼び出し）の generationConfig"""
    low = str(model or "").lower()
    if "2.5-flash" in low:
        return {"maxOutputTokens": 1024, "thinkingConfig": {"thinkingBudget": 0}}
    if _is_thinking_model(low):
        return {"maxOutputTokens": 2048, "thinkingConfig": {"thinkingBudget": 128}}
    return {"maxOutputTokens": 256}


def _step2_generation_config(model, max_output_tokens, provider="google", thinking_level="auto"):
    """2段目・直接生成。Vertex の思考モデルや高思考設定時は思考で JSON が切れないよう下限を上げる"""
    tok = int(max_output_tokens or 8192)
    low = str(model or "").lower()
    tc, _, min_out = resolve_thinking_config(model, thinking_level)
    if min_out > 0:
        tok = max(tok, min_out)
    if provider == "vertex" and (_is_thinking_model(low) or "2-5" in low):
        tok = max(tok, 16384)
    cfg = {"maxOutputTokens": tok, "responseMimeType": "application/json"}
    if tc:
        cfg["thinkingConfig"] = tc
    return cfg


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


def _parts_with_images(text, images):
    parts = [{"text": text}]
    for im in images or []:
        mime = im.get("mimeType") or "image/png"
        data = im.get("data") or ""
        if data:
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
    return parts


_SIG_LOCK = threading.RLock()
SIG_CACHE_FILE = os.path.join(DATA, "fetch_signature.json")
SIG_CACHE_VERSION = 1
SIG_MAX_BYTES = 65536            # 1 エントリの上限（超えたら disabled）
SIG_MAX_ENTRIES = 20             # モデル数の上限（超えたら古い順に削除）

# 失敗後の「休み」は時間ではなくターン数で数える（1 ターン約 30 秒なので分単位の待ちは無意味）
SIG_SKIP_TURNS = (1, 3)          # 失敗1回目 → 次の1ターン休む / 2回目 → 次の3ターン休む
SIG_GIVE_UP_AFTER = 3            # 3回失敗したら中止（このプロセスでは使わない。再起動で復活）

_SIG_MEM = None                  # 常駐キャッシュ（読み込みは初回の 1 回だけ）
_SIG_FAIL = {}                   # "provider|model" -> {"streak":n, "skip":k, "given_up":bool}（メモリのみ）
SIG_OK = {}                      # "provider|model" -> 成功回数（メモリのみ）

_FIRST_SYS = ("あなたは日本語ロールプレイの本文生成器です。"
              "今回の応答を書く前に、必ず fetch_prompt を呼び出してコンテキストを取得してください。"
              "取得するまで本文を書いてはいけません。")
_FIRST_USER = "fetch_prompt を呼び出してコンテキストを取得してください。"
_FETCH_DESC = "このチャンネルの設定・会話・今回のユーザー発言を取得する。引数はない。"


def _sig_fp(fetch_name):
    """第1段の内容が変わっていないことを確認するハッシュ"""
    raw = json.dumps({"sys": _FIRST_SYS, "user": _FIRST_USER,
                      "decl": {"name": fetch_name, "description": _FETCH_DESC},
                      "name": fetch_name}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sig_load():
    """常駐キャッシュを返す（初回のみファイルを読む）。壊れていれば空で初期化"""
    global _SIG_MEM
    if _SIG_MEM is None:
        d = None
        try:
            with open(SIG_CACHE_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = None
        if not isinstance(d, dict) or d.get("version") != SIG_CACHE_VERSION \
                or not isinstance(d.get("entries"), dict):
            d = {"version": SIG_CACHE_VERSION, "entries": {}}
        _SIG_MEM = d
    return _SIG_MEM


def _sig_save():
    """常駐キャッシュをファイルへ（原子的更新）。失敗しても生成は止めない"""
    try:
        from server import write_json
        write_json(SIG_CACHE_FILE, _sig_load(), indent=None)
    except Exception as e:
        print(f"[sig] save failed: {e}")


def _sig_drop_parts(key):
    """署名だけ捨てる（次は第1段から取り直す）。失敗状態には触らない"""
    with _SIG_LOCK:
        e = _sig_load()["entries"].get(key)
        if isinstance(e, dict) and e.get("parts"):
            e["parts"] = []
            _sig_save()


def sig_cache_status(provider, model, fp):
    """キャッシュ状態を判定する。
    戻り値:
      'hit'      : 有効な署名あり（sig_cache_get で取得可能）
      'disabled' : エントリが disabled (64KB超等)
      'skip'     : 休み中（skip > 0 または今ターンが休みターン）
      'give_up'  : 3回失敗で中止中
      'miss'     : 署名なし（新規取得・保存対象）
    """
    key = f"{provider}|{model}"
    with _SIG_LOCK:
        st = _SIG_FAIL.get(key)
        if isinstance(st, dict):
            if st.get("given_up"):
                return "give_up"
            if int(st.get("skip") or 0) > 0 or st.get("resting"):
                return "skip"
        e = _sig_load()["entries"].get(key)
        if isinstance(e, dict) and e.get("disabled"):
            return "disabled"
        if not isinstance(e, dict) or e.get("fp") != fp:
            return "miss"
        parts = e.get("parts")
        if not isinstance(parts, list) or not any(isinstance(p, dict) and p.get("thoughtSignature")
                                                  for p in parts):
            return "miss"
        return "hit"


def sig_cache_get(provider, model, fp):
    """有効な署名を返す。休み中・中止後・無効なら None（ファイル IO なし）"""
    key = f"{provider}|{model}"
    with _SIG_LOCK:
        st = _SIG_FAIL.get(key)
        if isinstance(st, dict):
            if st.get("given_up"):
                return None
            if int(st.get("skip") or 0) > 0:
                st["skip"] = int(st["skip"]) - 1     # 1 ターン消費して休む
                st["resting"] = True
                return None
            st.pop("resting", None)
        e = _sig_load()["entries"].get(key)
        if not isinstance(e, dict) or e.get("disabled") or e.get("fp") != fp:
            return None
        parts = e.get("parts")
        if not isinstance(parts, list) or not any(isinstance(p, dict) and p.get("thoughtSignature")
                                                  for p in parts):
            return None
        # 共有状態の破壊を防ぐため複製して返す（payload を組み立てる側が将来 mutable に触っても
        # キャッシュ本体が汚れないようにする。copy は google_ai.py で import 済み）
        return {"parts": copy.deepcopy(parts), "role": e.get("role") or "model"}


def sig_cache_put(provider, model, model_content, fp):
    """取得した署名を保存する。

    失敗状態（_SIG_FAIL）には一切触らない。ここでカウントを戻すと
    「失敗 → フォールバック成功 → 保存で復活 → また失敗」を繰り返し、中止判定に到達しなくなる。
    """
    if not isinstance(model_content, dict) or not isinstance(model_content.get("parts"), list):
        return
    key = f"{provider}|{model}"
    with _SIG_LOCK:
        st = _SIG_FAIL.get(key) or {}
        if st.get("given_up"):
            return
        d = _sig_load()
        existing = d["entries"].get(key)
        if isinstance(existing, dict) and existing.get("disabled"):
            return
        blob = json.dumps(model_content, ensure_ascii=False)
        if len(blob) > SIG_MAX_BYTES:
            # thinking パート等で肥大化した応答は保存しない（以後このキーは試さない）
            d["entries"][key] = {"fp": fp, "saved_ts": time.time(), "disabled": True,
                                 "parts": [], "role": "model"}
            _sig_save()
            return
        d["entries"][key] = dict(model_content, fp=fp, saved_ts=time.time(), disabled=False)
        if len(d["entries"]) > SIG_MAX_ENTRIES:
            order = sorted(d["entries"], key=lambda k: d["entries"][k].get("saved_ts") or 0)
            for k in order[:len(d["entries"]) - SIG_MAX_ENTRIES]:
                d["entries"].pop(k, None)
        _sig_save()


def sig_cache_mark(provider, model, ok):
    """署名の成否を記録する（メモリのみ・ファイルに書かない）"""
    key = f"{provider}|{model}"
    with _SIG_LOCK:
        if ok:
            _SIG_FAIL.pop(key, None)                  # 復活
            SIG_OK[key] = SIG_OK.get(key, 0) + 1
            return
        st = _SIG_FAIL.setdefault(key, {"streak": 0, "skip": 0, "given_up": False})
        st["streak"] = int(st.get("streak") or 0) + 1
        st.pop("resting", None)
        if st["streak"] >= SIG_GIVE_UP_AFTER:
            st["given_up"] = True                     # 中止（再起動 or 設定 OFF→ON で復活）
            st["skip"] = 0
            # 署名は消さない: 設定を OFF→ON したときに「同じ署名でもう一度だけ試す」ため
            print(f"[sig] give up {key} (streak={st['streak']})")
            return
        st["skip"] = SIG_SKIP_TURNS[min(st["streak"] - 1, len(SIG_SKIP_TURNS) - 1)]
        if st["streak"] >= 2:
            _sig_drop_parts(key)                      # 2回目以降は署名を捨てる


def sig_cache_forget_fail_state(provider, model):
    """失敗状態だけ消す（署名は残す）。設定 OFF の間に呼ぶ＝ON に戻すと再挑戦する。

    署名を消さないので、失敗が一過性だった場合は次のターンで即ヒットに戻る（コスト 0）。
    """
    with _SIG_LOCK:
        _SIG_FAIL.pop(f"{provider}|{model}", None)


def sig_cache_reset(provider=None, model=None):
    """手動クリア（中止状態も解除する。アプリ再起動と同じ効果）"""
    with _SIG_LOCK:
        d = _sig_load()
        if provider and model:
            d["entries"].pop(f"{provider}|{model}", None)
            _SIG_FAIL.pop(f"{provider}|{model}", None)
            SIG_OK.pop(f"{provider}|{model}", None)
        else:
            d["entries"] = {}
            _SIG_FAIL.clear()
            SIG_OK.clear()
        _sig_save()


def sig_cache_stats():
    """管理・デバッグ用（ログや REPL から見る。画面には出さない）"""
    with _SIG_LOCK:
        d = _sig_load()
        return {k: {"ok": SIG_OK.get(k, 0),
                    "streak": int((_SIG_FAIL.get(k) or {}).get("streak") or 0),
                    "skip": int((_SIG_FAIL.get(k) or {}).get("skip") or 0),
                    "given_up": bool((_SIG_FAIL.get(k) or {}).get("given_up")),
                    "disabled": bool(v.get("disabled")),
                    "saved_ts": float(v.get("saved_ts") or 0)}
                for k, v in d["entries"].items()}


def _generate_core(model, prompt, system, *, use_fetch, safety, safety_off, timeout, provider,
                   max_output_tokens, thinking_level, fetch_name, t0, images=None,
                   save_sig=False, fp=None):
    """現行の try: ブロック全体（use_fetch=True なら 2 段階、False なら直接生成）。
    戻り値は generate() と同型。振る舞いは現行と 1 ミリも変えない（純粋な切り出し）。
    第1段が成功して fetch を呼んだ直後に、save_sig なら sig_cache_put(...) を 1 行追加する。
    """
    first_status = second_status = None
    fetch_called = False
    try:
        if use_fetch:
            first_payload = {
                "systemInstruction": {"parts": [{"text": _FIRST_SYS}]},
                "contents": [{"role": "user", "parts": [{"text": _FIRST_USER}]}],
                "tools": [{"functionDeclarations": [{
                    "name": fetch_name,
                    "description": _FETCH_DESC,
                }]}],
                "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [fetch_name]}},
                "generationConfig": _step1_generation_config(model),
            }
            if safety:
                first_payload["safetySettings"] = safety
            first_status, first_data = _request_with_fallbacks(model, first_payload, timeout, provider, safety_off)
            model_content, fc = extract_function_call(first_data, fetch_name)
            fetch_called = bool(fc)
            if not fc:
                _, meta = extract_text(first_data)
                is_blocked = (bool(meta.get("block_reason"))
                              or meta.get("finish_reason") in BLOCK_FINISH_REASONS)
                if provider == "vertex" and not is_blocked:
                    # 直接生成へフォールバック
                    direct_payload = {
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": _parts_with_images(prompt, images)}],
                        "generationConfig": _step2_generation_config(model, max_output_tokens, provider=provider, thinking_level=thinking_level),
                    }
                    if safety:
                        direct_payload["safetySettings"] = safety
                    status, data = _request_with_fallbacks(model, direct_payload, timeout, provider, safety_off)
                    text, meta = extract_text(data)
                    err = None
                    if meta.get("block_reason"):
                        err = f"prompt blocked: {meta['block_reason']}"
                    elif meta.get("finish_reason") in BLOCK_FINISH_REASONS:
                        err = f"generation blocked: {meta['finish_reason']}"
                    elif not text:
                        err = f"empty response (finishReason={meta.get('finish_reason')})"
                    return {"text": text, "meta": meta, "fetch_called": False,
                            "first_status": first_status, "second_status": None,
                            "elapsed_ms": int((time.time() - t0) * 1000),
                            "error": err, "retryable": bool(err and "empty response" in err),
                            "http_status": status}

                err = "fetch step: model did not call fetch_prompt"
                if meta.get("block_reason"):
                    err = f"prompt blocked: {meta['block_reason']}"
                elif meta.get("finish_reason") in BLOCK_FINISH_REASONS:
                    err = f"generation blocked: {meta['finish_reason']}"
                return {"text": "", "meta": meta, "fetch_called": False,
                        "first_status": first_status, "second_status": None,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                        "error": err,
                        "retryable": False, "http_status": first_status}

            if save_sig:
                sig_cache_put(provider, model, model_content, fp)

            second_payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [
                    {"role": "user", "parts": [{"text": _FIRST_USER}]},
                    model_content,
                    {"role": "user", "parts": [{"functionResponse": {
                        "name": fetch_name,
                        "response": {"context": prompt},
                    }}]},
                ],
                "generationConfig": _step2_generation_config(model, max_output_tokens, provider=provider, thinking_level=thinking_level),
            }
            if safety:
                second_payload["safetySettings"] = safety

            try:
                second_status, second_data = _request_with_fallbacks(model, second_payload, timeout, provider, safety_off)
            except GoogleAPIError as e:
                if provider == "vertex" and e.status == 400 and _is_tool_400(str(e)):
                    direct_payload = {
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"role": "user", "parts": _parts_with_images(prompt, images)}],
                        "generationConfig": _step2_generation_config(model, max_output_tokens, provider=provider, thinking_level=thinking_level),
                    }
                    if safety:
                        direct_payload["safetySettings"] = safety
                    status, data = _request_with_fallbacks(model, direct_payload, timeout, provider, safety_off)
                    text, meta = extract_text(data)
                    err = None
                    if meta.get("block_reason"):
                        err = f"prompt blocked: {meta['block_reason']}"
                    elif meta.get("finish_reason") in BLOCK_FINISH_REASONS:
                        err = f"generation blocked: {meta['finish_reason']}"
                    elif not text:
                        err = f"empty response (finishReason={meta.get('finish_reason')})"
                    return {"text": text, "meta": meta, "fetch_called": False,
                            "first_status": first_status, "second_status": None,
                            "elapsed_ms": int((time.time() - t0) * 1000),
                            "error": err, "retryable": bool(err and "empty response" in err),
                            "http_status": status}
                raise

            text, meta = extract_text(second_data)
            err = None
            if meta.get("block_reason"):
                err = f"prompt blocked: {meta['block_reason']}"
            elif meta.get("finish_reason") in BLOCK_FINISH_REASONS:
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
                "generationConfig": _step2_generation_config(model, max_output_tokens, provider=provider, thinking_level=thinking_level),
            }
            if safety:
                payload["safetySettings"] = safety
            status, data = _request_with_fallbacks(model, payload, timeout, provider, safety_off)
            text, meta = extract_text(data)
            err = None
            if meta.get("block_reason"):
                err = f"prompt blocked: {meta['block_reason']}"
            elif meta.get("finish_reason") in BLOCK_FINISH_REASONS:
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
    except Exception as e:
        return {"text": "", "meta": {}, "fetch_called": fetch_called,
                "first_status": first_status, "second_status": second_status,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "error": f"内部エラー: {e}", "retryable": False,
                "http_status": 500, "code": "INTERNAL_ERROR"}


def _generate_second(model, prompt, system, model_content, *, safety, safety_off, timeout,
                     provider, max_output_tokens, thinking_level, fetch_name, t0):
    """第2段のみ（署名キャッシュヒット用）。

    `_cache_fail` を付けるのは次の 2 つだけ（それ以外は通常の結果として返す）:
      - 400 かつ `_is_tool_400(str(e))`   ← provider でゲートしない（google でも退避する）
      - 空応答（text=="" かつ block_reason 無し かつ finish_reason がブロック系でない）
    SAFETY / block_reason / 429 / 5xx は `_cache_fail` を付けない（429・5xx は例外を伝播）。
    `_cache_fail` は返す前に `generate()` が `pop` する（`meta` には入れない）。
    """
    second_payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "user", "parts": [{"text": _FIRST_USER}]},
            model_content,
            {"role": "user", "parts": [{"functionResponse": {
                "name": fetch_name,
                "response": {"context": prompt},
            }}]},
        ],
        "generationConfig": _step2_generation_config(model, max_output_tokens, provider=provider, thinking_level=thinking_level),
    }
    if safety:
        second_payload["safetySettings"] = safety

    try:
        second_status, second_data = _request_with_fallbacks(model, second_payload, timeout, provider, safety_off)
    except GoogleAPIError as e:
        if e.status == 400 and _is_tool_400(str(e)):
            return {
                "text": "", "meta": {}, "fetch_called": True,
                "first_status": None, "second_status": 400,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "error": str(e), "retryable": False,
                "http_status": 400, "code": e.code,
                "_cache_fail": "400",
            }
        raise

    text, meta = extract_text(second_data)
    err = None
    if meta.get("block_reason"):
        err = f"prompt blocked: {meta['block_reason']}"
    elif meta.get("finish_reason") in BLOCK_FINISH_REASONS:
        err = f"generation blocked: {meta['finish_reason']}"
    elif not text:
        err = f"empty response (finishReason={meta.get('finish_reason')})"

    res = {
        "text": text, "meta": meta, "fetch_called": True,
        "first_status": None, "second_status": second_status,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "error": err, "retryable": bool(err and "empty response" in err),
        "http_status": second_status,
    }
    if not text and not meta.get("block_reason") and meta.get("finish_reason") not in BLOCK_FINISH_REASONS:
        res["_cache_fail"] = "empty"
    return res


def generate(model, prompt, system="", *, safety_off=True, max_output_tokens=8192,
             fetch=True, images=None, timeout=180, fetch_name="fetch_prompt", provider="google",
             thinking_level="auto", fetch_cache=False):
    """
    戻り値:
      {"text": str, "meta": {...}, "fetch_called": bool, "first_status", "second_status",
       "elapsed_ms": int, "error": str|None, "retryable": bool, "http_status": int|None,
       "fetch_cache": "hit"|"miss"|"off"|"hit_fallback"|"disabled"}

    `fetch_cache` の意味:
      hit          … キャッシュの署名で第2段のみ実行（API 往復 1 回）
      miss         … 署名が無かったので現行 2 段階を実行し、取得した署名を保存した
      off          … キャッシュを使っていない（フラグ OFF / fetch 無し / 休み中 / 中止後）
      hit_fallback … キャッシュで失敗して現行 2 段階に退避した（理由は `cache_error`）
      disabled     … 応答が大きすぎて署名を保存しなかったモデル（以後このキーでは試さない）

    追加キー `cache_error`（文字列）: キャッシュ起因で退避したときだけ入る（400 のメッセージ等）。
    images がある場合は fetch を使わず、1回のリクエストで画像も送る。
    """
    if not model:
        return {"text": "", "meta": {}, "fetch_called": False, "error": "モデルが未設定です",
                "retryable": False, "http_status": None, "elapsed_ms": 0, "fetch_cache": "off"}
    _, eff_level, _ = resolve_thinking_config(model, thinking_level)
    if eff_level in ("XHIGH", "budget:16384"):
        timeout = max(int(timeout or 180), 240)
    system = system or "あなたは日本語ロールプレイの本文生成器です。必ずJSONのみを返します。"
    use_fetch = bool(fetch) and not images
    safety = _safety_settings(safety_off, provider=provider)
    t0 = time.time()
    fp = _sig_fp(fetch_name) if use_fetch else None
    cache_state = "off"
    try:
        if use_fetch and fetch_cache:
            cached = sig_cache_get(provider, model, fp)
            if cached:
                res = _generate_second(model, prompt, system, cached, safety=safety,
                                       safety_off=safety_off, timeout=timeout, provider=provider,
                                       max_output_tokens=max_output_tokens,
                                       thinking_level=thinking_level, fetch_name=fetch_name, t0=t0)
                fail = res.pop("_cache_fail", "")
                if not fail:
                    sig_cache_mark(provider, model, ok=True)
                    res["fetch_cache"] = "hit"
                    return res
                # 署名起因の失敗だけ 2 段階へ退避（SAFETY / block / 429 はここに来ない）
                cache_error = str(res.get("error") or fail)[:200]
                print(f"[sig] cache {fail} ({provider}|{model}): {cache_error}")
                sig_cache_mark(provider, model, ok=False)
                if (time.time() - t0) < timeout:      # 残り予算ガード（最悪 2 倍を防ぐ）
                    save_on_fb = (int((_SIG_FAIL.get(f"{provider}|{model}") or {}).get("streak") or 0) < 2)
                    res2 = _generate_core(model, prompt, system, use_fetch=True, safety=safety,
                                          safety_off=safety_off, timeout=timeout, provider=provider,
                                          max_output_tokens=max_output_tokens,
                                          thinking_level=thinking_level, fetch_name=fetch_name, t0=t0,
                                          images=images, save_sig=save_on_fb, fp=fp)
                    res2["fetch_cache"] = "hit_fallback"
                    res2["cache_error"] = cache_error
                    return res2
                res["fetch_cache"] = "hit_fallback"   # 予算不足：そのまま返し、上位のリトライに任せる
                res["cache_error"] = cache_error
                res["retryable"] = True
                return res
            st_val = sig_cache_status(provider, model, fp)
            if st_val == "disabled":
                cache_state = "disabled"
            elif st_val == "miss":
                cache_state = "miss"
            else:
                cache_state = "off"

        res = _generate_core(model, prompt, system, use_fetch=use_fetch, safety=safety,
                             safety_off=safety_off, timeout=timeout, provider=provider,
                             max_output_tokens=max_output_tokens,
                             thinking_level=thinking_level, fetch_name=fetch_name, t0=t0,
                             images=images,
                             save_sig=bool(use_fetch and fetch_cache and cache_state == "miss"), fp=fp)
        res["fetch_cache"] = cache_state
        return res
    except GoogleAPIError as e:
        is_hit = bool(use_fetch and fetch_cache and ('cached' in locals() and cached))
        return {"text": "", "meta": {}, "fetch_called": bool(use_fetch and fetch_cache and is_hit),
                "first_status": None, "second_status": None,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "error": str(e), "retryable": bool(e.retryable),
                "http_status": e.status, "code": e.code,
                "fetch_cache": "hit" if is_hit else cache_state}
    except Exception as e:
        is_hit = bool(use_fetch and fetch_cache and ('cached' in locals() and cached))
        return {"text": "", "meta": {}, "fetch_called": bool(use_fetch and fetch_cache and is_hit),
                "first_status": None, "second_status": None,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "error": f"内部エラー: {e}", "retryable": False,
                "http_status": 500, "code": "INTERNAL_ERROR",
                "fetch_cache": "hit" if is_hit else cache_state}
