"""AIチャンネル作成ウィザード（server.py から切り出し）"""
import copy
from contextlib import contextmanager
import json
import os
import random
import re
import threading
import time
from urllib.parse import parse_qs
import uuid

import google_ai
from image_engine import find_char, split_tags
from rpchat.story_time import (
    _safe_day,
    infer_initial_period,
    normalize_period,
)
from rpchat.util import _as_int

_deps, _getters = {}, {}


def bind_deps(**kw):
    for k, v in kw.items():
        if k in globals() and k not in _deps:   # 自モジュール定義名・import名を守る
            raise RuntimeError(f"bind_deps: {k} はモジュール内で定義済み（衝突）")
        _deps[k] = v
        globals()[k] = v


def bind_getters(**kw):
    for k, v in kw.items():
        if not callable(v):
            raise TypeError(f"bind_getters: {k} はゼロ引数関数で渡す")
        _getters[k] = v


def _g(name):
    f = _getters.get(name)
    if f is None:
        raise RuntimeError(f"rpchat.channel_wizard: bind_getters({name}=...) が未設定")
    return f()


REQUIRED_DEPS = (
    "NO_FOLDER",
    "NO_IDE",
    "RESET_PROGRESS",
    "RESET_STORY",
    "_epoch",
    "_is_filter_error_msg",
    "app_settings",
    "create_session",
    "find_msg",
    "get_poke_lock",
    "load",
    "lock",
    "merge_defaults",
    "new_channel_setting",
    "new_id",
    "now",
    "read_json",
    "save",
    "spath",
    "write_json",
)

REQUIRED_GETTERS = (
    "APP_SETTINGS",
    "WIZARD_DIR",
    "WIZARD_IDE_DIR",
    "WIZARD_TAGS_PATH",
    "char_presets",
    "ide_poke",
    "image_event",
    "queued_images",
)


def missing_deps():
    return [n for n in REQUIRED_DEPS if n not in _deps] + [
        n for n in REQUIRED_GETTERS if n not in _getters
    ]


# ============================================================
# AIチャンネル作成ウィザード
# ============================================================
WIZARD_ID_RE = re.compile(r"^wiz_[0-9a-f]{10}$")
WIZARD_JOB_RE = re.compile(r"^wj_[0-9a-f]{10}$")

WIZARD_MAX_QUESTIONS = 6
WIZARD_MAX_ROUNDS = 2
WIZARD_QUESTIONS_PER_ROUND = 3
WIZARD_AI_CALL_LIMIT = 8
WIZARD_DRAFT_LIMIT = 20
WIZARD_DRAFT_TTL = 7 * 24 * 60 * 60
WIZARD_CREATED_TTL = 24 * 60 * 60
WIZARD_JOB_STALE = 600
WIZARD_JOB_KEEP = 3600

REQUIRED_SLOTS = ["partner_type", "world", "tone", "relationship", "player_role"]
ASKABLE_PRIORITY = [
    "partner_name",
    "relationship", "start_point", "player_role",
    "world", "tone", "ending", "intensity", "goal",
]

WIZARD_SLOTS = {
    "rough_idea":      {"label": "作りたい話", "type": "text", "max": 300},
    "partner_type":    {"label": "相手の種類", "type": "enum",
                        "values": ["original", "copyright"]},
    "work":            {"label": "作品・世界観", "type": "text", "max": 60},
    "partner_name":    {"label": "相手の名前", "type": "text", "max": 40},
    "player_type":     {"label": "あなた", "type": "enum",
                        "values": ["original", "copyright", "existing"]},
    "player_work":     {"label": "あなたの作品", "type": "text", "max": 60},
    "player_name":     {"label": "あなたの名前", "type": "text", "max": 40},
    "player_profile":  {"label": "あなたの設定", "type": "text", "max": 300},
    "start_point":     {"label": "物語の開始地点", "type": "text", "max": 80},
    "canon_fidelity":  {"label": "原作準拠度", "type": "enum",
                        "values": ["canon", "mixed", "au"]},
    "relationship":    {"label": "関係性", "type": "multi", "max": 3},
    "world":           {"label": "世界観", "type": "multi", "max": 2},
    "tone":            {"label": "雰囲気", "type": "multi", "max": 2},
    "kinks":           {"label": "特殊要素", "type": "multi", "max": 4},
    "intensity":       {"label": "性的濃度", "type": "enum",
                        "values": ["none", "soft", "medium", "hard"]},
    "player_role":     {"label": "あなたの立場", "type": "multi", "max": 2},
    "ending":          {"label": "結末", "type": "enum",
                        "values": ["happy", "bittersweet", "dark", "open"]},
    "goal":            {"label": "目的・ゴール", "type": "text", "max": 100},
    "pov":             {"label": "視点", "type": "enum",
                        "values": ["first", "second", "third"]},
    "style":           {"label": "文体", "type": "enum",
                        "values": ["novel", "light", "script", "chat"]},
    "length":          {"label": "長さ", "type": "enum",
                        "values": ["short", "medium", "long"]},
    "project":         {"label": "プロジェクト", "type": "text", "max": 40},
}

SLOT_LABEL = {k: v["label"] for k, v in WIZARD_SLOTS.items()}

WIZARD_SLOT_DEFAULTS = {
    "player_type": "original",
    "player_name": "あなた",
    "intensity": "soft",
    "pov": "second",
    "style": "novel",
    "length": "medium",
    "ending": "open",
    "start_point": "出会った直後",
    "canon_fidelity": "mixed",
}

# 親スロットが変わったら、子スロットを消す
INVALIDATES = {
    "partner_type": ["work", "partner_name", "start_point", "canon_fidelity"],
    "work":         ["partner_name", "start_point", "canon_fidelity"],
    "player_type":  ["player_work", "player_name", "player_profile"],
    "player_work":  ["player_name", "player_profile"],
}

# AIのeffectsを適用する順序（親→子）
APPLY_ORDER = [
    "partner_type", "work", "player_type", "player_work",
    "player_name", "partner_name", "player_profile", "start_point",
    "canon_fidelity", "relationship", "world", "tone", "kinks",
    "intensity", "player_role", "ending", "goal", "pov", "style",
    "length", "rough_idea", "project",
]

SOURCE_ORDER = {
    # 「おまかせ」はユーザーの明示選択なので、タグ由来の derived より強い。
    "unset": 0, "derived": 2, "auto": 3, "answer": 4, "user": 5, "locked": 6,
}

COMPOSE_SETTING_KEYS = [
    "title", "summary", "instructions", "scenario", "first_message",
    "character", "chars", "elapsed_time", "user_name", "project",
]

# AIの質問effectsから変更させない（元チャンネルからの引き継ぎを守る）
PROTECTED_AI_SLOTS = {"player_name", "project"}

wizard_lock = threading.RLock()
wizard_create_lock = threading.Lock()  # 同じ下書きの二重作成を防ぐ
wizard_jobs = {}
wizard_tags_cache = {"ts": 0.0, "data": None, "index": {}}
wizard_model_cache = {}


class WizardError(Exception):
    def __init__(self, message, code="wizard_error", retryable=False, status=400):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status


def _wiz_uid(prefix):
    return prefix + uuid.uuid4().hex[:10]


def wizard_path(wid):
    if not WIZARD_ID_RE.match(str(wid or "")):
        raise ValueError("bad wizard id")
    return os.path.join(_g("WIZARD_DIR"), wid + ".json")


def wizard_load(wid):
    try:
        d = read_json(wizard_path(wid), None)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if _as_int(d.get("schema_version"), 0) != 1:
        return None
    if not isinstance(d.get("slots"), dict):
        d["slots"] = {}
    if not isinstance(d.get("selected_tags"), list):
        d["selected_tags"] = []
    if not isinstance(d.get("questions"), list):
        d["questions"] = []
    return d


def wizard_save(d, bump=True):
    """bump=False: last_job / ai_calls / status など、クライアントの rev を動かしたくない内部更新用"""
    with wizard_lock:
        if bump:
            d["rev"] = _as_int(d.get("rev"), 0) + 1
        d["updated"] = now()
        os.makedirs(_g("WIZARD_DIR"), exist_ok=True)
        write_json(wizard_path(d["id"]), d)


def wizard_ids():
    if not os.path.isdir(_g("WIZARD_DIR")):
        return []
    out = []
    for f in os.listdir(_g("WIZARD_DIR")):
        if f.endswith(".json") and WIZARD_ID_RE.match(f[:-5]):
            out.append(f[:-5])
    return out


def wizard_ids_sorted():
    out = []
    for wid in wizard_ids():
        d = wizard_load(wid)
        if d:
            # updated は秒単位なので、同じ秒に作った下書きの順序が不定になる。
            # ファイルの更新時刻を第2キーにして「最後に触った下書き」を一意に決める。
            try:
                mt = os.path.getmtime(wizard_path(wid))
            except OSError:
                mt = 0.0
            out.append((wid, d, mt))
    out.sort(key=lambda x: (str(x[1].get("updated") or ""), x[2]), reverse=True)
    return [(wid, d) for wid, d, _ in out]


def wizard_sweep():
    with wizard_lock:
        now_t = time.time()
        # 終了したジョブをいつまでもメモリに残さない
        for jid, j in list(wizard_jobs.items()):
            age = now_t - _as_int((j or {}).get("ts_epoch"), 0)
            if (j or {}).get("status") in ("done", "error") and age > WIZARD_JOB_KEEP:
                wizard_jobs.pop(jid, None)
    if not os.path.isdir(_g("WIZARD_DIR")):
        return 0
    created = []
    live = []
    for wid in wizard_ids():
        d = wizard_load(wid)
        if not d:
            continue
        (created if d.get("status") == "created" else live).append((wid, d))

    def _age(d):
        return time.time() - (_epoch(d.get("updated")) or 0)

    def _evict_rank(d):
        """先に消してよい順。空の下書き・ユーザーが断った下書き → 内容のある下書き。"""
        if d.get("status") == "dismissed" or not wizard_draft_has_input(d):
            return 0
        return 1

    drop = set()
    for wid, d in live:
        if _age(d) > WIZARD_DRAFT_TTL:
            drop.add(wid)
    # 件数上限は「生きている下書き」だけで数える（作成済みは TTL で消える）
    rest = [(wid, d) for wid, d in live if wid not in drop]
    rest.sort(key=lambda x: (_evict_rank(x[1]), str(x[1].get("updated") or "")))
    over = len(rest) - WIZARD_DRAFT_LIMIT
    for wid, _d in rest:
        if over <= 0:
            break
        drop.add(wid)
        over -= 1
    for wid, d in created:
        if _age(d) > WIZARD_CREATED_TTL:
            drop.add(wid)
    removed = 0
    for wid in drop:
        try:
            os.remove(wizard_path(wid))
            removed += 1
        except OSError:
            pass
    return removed


def wizard_tags():
    with wizard_lock:
        t = time.time()
        if wizard_tags_cache["data"] is not None and t - wizard_tags_cache["ts"] < 300:
            return wizard_tags_cache["data"]
        raw = read_json(_g("WIZARD_TAGS_PATH"), {})
        if not isinstance(raw, dict):
            raw = {}
        tags = []
        for x in (raw.get("tags") or []):
            if not isinstance(x, dict) or not x.get("id"):
                continue
            tag = dict(x)
            # 仕様書の desc / 実装の description の両方を受ける
            tag["description"] = str(tag.get("description") or tag.get("desc") or "")
            c = tag.get("conflicts_ng") or []
            tag["conflicts_ng"] = (split_tags(c) if isinstance(c, str) else list(c))[:20]
            tags.append(tag)
        ng = [x for x in (raw.get("ng_presets") or []) if isinstance(x, dict) and x.get("id")]
        axes_raw = raw.get("axes")
        if isinstance(axes_raw, dict):
            axes = axes_raw
        elif isinstance(axes_raw, list):
            # 仕様書の [{id,label,max}] 形式も受ける
            axes = {str(a.get("id")): {"label": a.get("label") or a.get("id"),
                                       "max": a.get("max")}
                    for a in axes_raw if isinstance(a, dict) and a.get("id")}
        else:
            axes = {}
        data = {
            "version": _as_int(raw.get("version"), 1),
            "axes": axes,
            "tags": tags,
            "ng_presets": ng,
            "examples": [str(x) for x in (raw.get("examples") or []) if str(x).strip()],
        }
        index = {x["id"]: x for x in tags}
        wizard_tags_cache.update({"ts": t, "data": data, "index": index})
        return data


def wizard_tag_by_id(tid):
    wizard_tags()
    return wizard_tags_cache["index"].get(str(tid or ""))


def wizard_ng_labels(d):
    labels = []
    presets = {x["id"]: x for x in wizard_tags().get("ng_presets", [])}
    for nid in (d.get("selected_ng") or []):
        if nid in presets:
            labels.append(presets[nid].get("label") or nid)
    labels.extend(str(x) for x in (d.get("custom_ng") or []) if str(x).strip())
    return list(dict.fromkeys(labels))


def wizard_norm_label(x):
    return re.sub(r"[\s_\-・/]+", "", str(x or "").casefold())


def wizard_ng_conflicts(d):
    """NG の ID と表示名の両方。表記ゆれは正規化して比較する。"""
    out = set()
    presets = {x.get("id"): x for x in wizard_tags().get("ng_presets", [])}
    for nid in (d.get("selected_ng") or []):
        out.add(wizard_norm_label(nid))
        if nid in presets:
            out.add(wizard_norm_label(presets[nid].get("label")))
    for x in (d.get("custom_ng") or []):
        out.add(wizard_norm_label(x))
    out.discard("")
    return out


def wizard_tag_hits_ng(tag, ng_set):
    if not isinstance(tag, dict):
        return False
    cand = [tag.get("label")]
    for key in ("conflicts_ng", "conflicts"):
        v = tag.get(key) or []
        if isinstance(v, str):
            v = split_tags(v)
        cand.extend(v)
    return any(wizard_norm_label(x) in ng_set for x in cand if str(x or "").strip())


def wizard_clean_tags(tags):
    try:
        raw = split_tags(tags) if isinstance(tags, str) else (tags or [])
    except Exception:
        raw = []
    drop = (
        "masterpiece", "best quality", "high quality", "highres", "absurdres",
        "official art", "1girl", "1boy", "solo", "looking at viewer",
        "full body", "upper body", "close-up", "from behind", "from side",
        "school uniform", "serafuku", "uniform", "dress", "outfit",
        "standing", "sitting", "lying", "walking", "running",
    )
    drop_set = set(drop)
    out = []
    for x in raw:
        s = str(x or "").strip()
        low = s.lower()
        # 部分一致だと headdress / flying / han solo を誤爆するため完全一致で落とす
        if not s or low in drop_set:
            continue
        if s not in out:
            out.append(s)
    return out[:40]


def wizard_tag_lines(d):
    out = []
    for tid in (d.get("selected_tags") or []):
        tag = wizard_tag_by_id(tid)
        if tag:
            out.append(f"- {tag.get('label')}: {tag.get('description') or ''}")
    return "\n".join(out) or "- なし"


def wslot(d, key):
    return (d.get("slots") or {}).get(key) or {}


def wslot_source(d, key):
    return wslot(d, key).get("source") or "unset"


def wslot_value(d, key):
    return wslot(d, key).get("value")


def wizard_effective_value(d, key):
    v = wslot_value(d, key)
    if v not in (None, "", []):
        return v
    if key == "player_name":
        base = d.get("base_setting") or {}
        return str(base.get("user_name") or "").strip() or WIZARD_SLOT_DEFAULTS["player_name"]
    if key == "project":
        base = d.get("base_setting") or {}
        return str(base.get("project") or "").strip()
    return copy.deepcopy(WIZARD_SLOT_DEFAULTS.get(key))


def wiz_list(v):
    if isinstance(v, list):
        return v
    if v in (None, ""):
        return []
    return [v]


def wizard_plain_text(v, limit=2000):
    s = str(v or "").replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch in "\n\t" or ord(ch) >= 32)
    s = re.sub(r"[|<>]", "", s)
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    return s.strip()[:limit]


def wizard_norm_value(spec, v):
    typ = spec.get("type")
    if typ == "text":
        return wizard_plain_text(v, _as_int(spec.get("max"), 120))
    if typ == "enum":
        s = str(v or "").strip()
        return s if s in (spec.get("values") or []) else ""
    if typ == "multi":
        vals = []
        for x in wiz_list(v):
            s = wizard_plain_text(x, 40)
            if s and s not in vals:
                vals.append(s)
        return vals[: _as_int(spec.get("max"), 4)]
    return ""


def wslot_set(d, key, value, source):
    if key not in WIZARD_SLOTS:
        return False
    if source != "locked" and wslot_source(d, key) == "locked":
        return False
    if SOURCE_ORDER.get(source, 0) < SOURCE_ORDER.get(wslot_source(d, key), 0):
        return False
    spec = WIZARD_SLOTS[key]
    val = wizard_norm_value(spec, value)
    if val in (None, "", []) and source != "auto":
        return False
    prev = wslot_value(d, key)
    d.setdefault("slots", {})[key] = {"value": val, "source": source}
    if prev != val:
        for dep in INVALIDATES.get(key, []):
            if wslot_source(d, dep) != "locked":
                d["slots"].pop(dep, None)
        d["compose_stale"] = True
    return True


def wslot_set_auto(d, key):
    if key not in WIZARD_SLOTS:
        return
    d.setdefault("slots", {})[key] = {"value": "", "source": "auto"}
    for dep in INVALIDATES.get(key, []):
        if wslot_source(d, dep) != "locked":
            d["slots"].pop(dep, None)
    d["compose_stale"] = True


def wizard_filled(d, key):
    s = wslot(d, key)
    src = s.get("source") or "unset"
    if src == "auto":
        return True
    return src in ("locked", "user", "answer", "derived") and s.get("value") not in (None, "", [])


def wizard_missing_required(d):
    return [k for k in REQUIRED_SLOTS if not wizard_filled(d, k)]


def wizard_ready(d):
    return not wizard_missing_required(d)


def wizard_askable_slots(d):
    return [k for k in ASKABLE_PRIORITY
            if k in WIZARD_SLOTS and (
                wslot_source(d, k) == "unset" or (
                    wslot_source(d, k) == "auto" and not wslot_value(d, k)
                )
            )]


def wizard_recompute_derived(d):
    """derived だけ再計算する。user/answer/locked/auto は保持する。"""
    overflow = []
    for k, s in list((d.get("slots") or {}).items()):
        if (s or {}).get("source") == "derived":
            d["slots"].pop(k, None)
    for tid in (d.get("selected_tags") or []):
        tag = wizard_tag_by_id(tid)
        if not tag:
            continue
        for key, val in (tag.get("effects") or {}).items():
            if key not in WIZARD_SLOTS:
                continue
            # auto はユーザーの「おまかせ」なので derived で上書きしない。
            # 既に derived の項目は複数タグの効果をマージする。
            if wslot_source(d, key) not in ("unset", "derived"):
                continue
            spec = WIZARD_SLOTS[key]
            if spec.get("type") == "multi":
                cur = wslot_value(d, key)
                merged = list(dict.fromkeys(wiz_list(cur) + wiz_list(val)))
                mx = _as_int(spec.get("max"), 4)
                if len(merged) > mx:
                    overflow.append(key)
                merged = merged[:mx]
                if merged:
                    wslot_set(d, key, merged, "derived")
            else:
                nv = wizard_norm_value(spec, val)
                if nv:
                    wslot_set(d, key, nv, "derived")
    d["compose_stale"] = True
    return list(dict.fromkeys(overflow))


def wizard_slots_lines(d, only_decided=False):
    lines = []
    for key in APPLY_ORDER:
        if key not in WIZARD_SLOTS:
            continue
        src = wslot_source(d, key)
        val = wslot_value(d, key)
        if only_decided and (src == "unset" or (src == "auto" and not val)):
            continue
        if isinstance(val, list):
            shown = "、".join(str(x) for x in val)
        else:
            shown = str(val or "")
        if src == "auto":
            shown = shown or "（おまかせ）"
        elif not shown:
            shown = "（未設定）"
        lines.append(f"- {SLOT_LABEL[key]}: {shown} [{src}]")
    return "\n".join(lines)


def wizard_question_prompt(d):
    ng = "、".join(wizard_ng_labels(d)) or "なし"
    return f"""あなたは日本語のインタラクティブ小説の編集者です。
以下の「決定済み」を変えない範囲で、物語を良くする質問を最大{WIZARD_QUESTIONS_PER_ROUND}問作ってください。
質問は必ず未設定のスロットだけにしてください。すでに決まっていることを聞き直さないでください。

# 決定済み
{wizard_slots_lines(d, only_decided=True)}

# 選択済みタグ
{wizard_tag_lines(d)}

# NG（絶対に避ける）
{ng}

# 質問にできるスロット（優先順）
{', '.join(wizard_askable_slots(d)) or 'なし'}

# 出力（JSONのみ）
{{
  "questions": [
    {{
      "slot": "relationship",
      "text": "二人の関係は？",
      "why": "物語の距離感が決まるため",
      "options": [
        {{
          "label": "幼馴染",
          "description": "長年の距離感が近い",
          "effects": {{"relationship": ["幼馴染"], "tone": ["日常"]}},
          "invalidates": []
        }}
      ]
    }}
  ]
}}

# ルール
- 各質問のoptionsは4〜6個。
- effectsのキーはスロット名のみ。relationship/world/tone/kinks/player_roleは配列。
- effectsには必ず質問のslot自身を含める。
- NGに触れる選択肢は作らない。
- 説明は日本語。JSON以外を出力しない。"""


def wizard_sanitize_options(d, slot, options):
    out = []
    ng_labels = wizard_ng_conflicts(d)
    spec = WIZARD_SLOTS[slot]
    for i, o in enumerate(options or []):
        if not isinstance(o, dict):
            continue
        label = wizard_plain_text(o.get("label"), 20)
        if not label:
            continue
        if any(x and x in label.lower() for x in ng_labels):
            continue
        effects = {}
        for k, v in (o.get("effects") or {}).items():
            if k in PROTECTED_AI_SLOTS or k == "ng" or k not in WIZARD_SLOTS:
                continue
            nv = wizard_norm_value(WIZARD_SLOTS[k], v)
            if nv not in (None, "", []):
                effects[k] = nv
        # 質問スロット自身を必ず含める
        if slot not in effects:
            effects[slot] = label if spec.get("type") != "multi" else [label]
        inv = [x for x in (o.get("invalidates") or []) if x in WIZARD_SLOTS]
        out.append({
            "id": f"o{i + 1}",
            "label": label,
            "description": wizard_plain_text(o.get("description"), 80),
            "effects": effects,
            "invalidates": inv,
        })
        if len(out) >= 6:
            break
    return out


def wizard_sanitize_questions(d, raw_questions):
    askable = set(wizard_askable_slots(d))
    out, seen_slots, seen_ids = [], set(), set()
    for q in (raw_questions or []):
        if not isinstance(q, dict):
            continue
        slot = str(q.get("slot") or "")
        if slot not in askable or slot in seen_slots:
            continue
        src = wslot_source(d, slot)
        if not (src == "unset" or (src == "auto" and not wslot_value(d, slot))):
            continue
        text = wizard_plain_text(q.get("text"), 120)
        if not text:
            continue
        opts = wizard_sanitize_options(d, slot, q.get("options"))
        if len(opts) < 3:
            continue
        qid = f"q_{slot}"
        n = 2
        while qid in seen_ids or qid in (d.get("asked_question_ids") or []):
            qid = f"q_{slot}_{n}"
            n += 1
        seen_ids.add(qid)
        seen_slots.add(slot)
        out.append({
            "id": qid,
            "slot": slot,
            "text": text,
            "why": wizard_plain_text(q.get("why"), 60),
            "options": opts,
        })
        if len(out) >= WIZARD_QUESTIONS_PER_ROUND:
            break
    return out


def wizard_compose_prompt(d):
    ng = "、".join(wizard_ng_labels(d)) or "なし"
    skeleton = wizard_template_compose(d)[0]
    return f"""あなたは日本語のインタラクティブ小説の設定ライターです。
以下の材料から、新しいチャンネルの設定をJSONで作ってください。

# 材料
{wizard_slots_lines(d, only_decided=False)}

# 選択済みタグ
{wizard_tag_lines(d)}

# NG（絶対に避ける）
{ng}

# 参考の骨子（これをそのまま使わず、文章を豊かにする）
{json.dumps(skeleton, ensure_ascii=False, indent=2)}

# 出力（JSONのみ）
{{
  "title": "40文字以内",
  "summary": "300文字以内",
  "instructions": "400〜1200文字。本文AIへの指示。",
  "scenario": "800文字以内。物語の開始状況。",
  "first_message": "相手の第一声。500文字以内。",
  "character": {{
    "name": "相手の名前",
    "age": "24",
    "profile": "外見・性格・背景",
    "speech": "口調のサンプル"
  }},
  "chars": [
    {{"name": "相手の名前", "tags": ["1girl", "long hair"]}}
  ],
  "opening_image": ["classroom", "morning", "looking at viewer"],
  "elapsed_time": {{"day": 1, "period": "午前"}},
  "warnings": [],
  "title_alternatives": ["案1", "案2", "案3"]
}}

# ルール
- 全員18歳以上の成人として扱う。18歳未満の年齢は「なし」にする。
- 版権キャラは原作の設定を尊重する。未成年キャラは年齢を「なし」にする。
- NGと特殊要素が衝突したらNGを優先する。
- charsのtagsは画像生成用。quality/服装/ポーズ/構図タグは入れない。
- opening_imageは開幕画像の場面タグだけ（英語、最大12個）。人物の髪色・服装などの外見は入れない。
- JSON以外を出力しない。"""


OPENING_SCENE_DROP = (
    "masterpiece", "best quality", "high quality", "highres", "absurdres",
    "official art", "school uniform", "serafuku", "uniform", "dress", "outfit",
)


def wizard_clean_scene_tags(tags):
    """開幕画像の場面タグ。人物の外見は chars 側に任せる。"""
    try:
        raw = split_tags(tags) if isinstance(tags, str) else (tags or [])
    except Exception:
        raw = []
    out = []
    for x in raw:
        s = wizard_plain_text(x, 60)
        low = s.lower()
        if not s or not re.search(r"[a-zA-Z0-9]", s):
            continue
        if any(w in low for w in OPENING_SCENE_DROP):
            continue
        if re.search(r"\b(hair|eyes?|eyebrows?|skin|breasts?|nipples?|penis|pussy|uniform|dress|shirt|skirt|panties|bra|lingerie|outfit)\b", low):
            continue
        if s not in out:
            out.append(s)
    return out[:12]


def wizard_opening_tags(d):
    """タグカタログの image_tags から、開幕画像の場面タグを作る。"""
    out = []
    for tid in (d.get("selected_tags") or []):
        tag = wizard_tag_by_id(tid) or {}
        raw = tag.get("image_tags") or []
        if isinstance(raw, str):
            raw = split_tags(raw)
        for x in raw:
            out.append(x)
    return wizard_clean_scene_tags(out)


WIZARD_COUNT_RE = re.compile(r"(\d+\+?\s*(girl|boy|other)s?|multiple (girls|boys|others)|solo( focus)?)", re.I)


def wizard_count_tag(tags, chars):
    """開幕画像に必ず1つ入れる人数タグ。AI/プリセット優先、無ければ1girl。"""
    if isinstance(tags, str):
        tags = split_tags(tags)
    for x in (tags or []):
        s = str(x).strip()
        if WIZARD_COUNT_RE.fullmatch(s):
            return s
    for c in chars or []:
        for t in split_tags(c.get("tags")):
            s = str(t).strip()
            if WIZARD_COUNT_RE.fullmatch(s):
                return s
    return "1girl"


def wizard_opening_message_id(s):
    for m in (s.get("messages") or []):
        im = m.get("image") or {}
        if m.get("role") == "assistant" and im.get("opening"):
            return str(m.get("id") or "")
    return ""


def wizard_enqueue_opening_image(s, setting, tags=None):
    """作成をブロックせず、最初のassistant発言に開幕画像を積む。戻り値は message id。"""
    if (setting.get("image_mode") or "always") == "off":
        return ""
    first = next((m for m in (s.get("messages") or [])
                  if m.get("role") == "assistant"), None)
    if not first or not first.get("id"):
        return ""
    chars = [c for c in (setting.get("chars") or [])
             if isinstance(c, dict) and str(c.get("name") or "").strip()][:3]
    names = [str(c.get("name"))[:40] for c in chars]
    if not names:
        # 画像タグが空でも、第一声のキャラ名は画像メタに残す
        ch = setting.get("character") if isinstance(setting.get("character"), dict) else {}
        lead = str(ch.get("name") or "").strip()
        if lead:
            names = [lead[:40]]
    blocked = set()
    for c in chars:
        for t in split_tags(c.get("tags")):
            ts = str(t).strip()
            if WIZARD_COUNT_RE.fullmatch(ts):
                continue
            blocked.add(ts.lower())
    scene = wizard_clean_scene_tags(tags)
    scene = [t for t in scene if t.strip().lower() not in blocked]
    if not scene:
        scene = ["indoors", "morning", "looking at viewer"]
    if not any(WIZARD_COUNT_RE.fullmatch(t.strip()) for t in scene):
        scene.insert(0, wizard_count_tag(tags, chars))
    image_cfg = setting.get("image") if isinstance(setting.get("image"), dict) else {}
    img = {
        "prompt": ", ".join(scene),
        "negative": str(image_cfg.get("base_negative") or ""),
        "raw": False,
        "seed": random.randint(0, 2**32 - 1),
        "status": "queued",
        "chars": names,
        "opening": True,
    }
    with lock:
        try:
            cur = load(s["id"])
        except Exception:
            return ""
        cm = find_msg(cur, first["id"])
        if not cm or cm.get("image"):
            return ""
        cm["image"] = img
        save(cur)
        _g("queued_images").append((s["id"], first["id"]))
        _g("image_event").set()
    return str(first["id"])


def wizard_is_school_context(d):
    """ドラフトの設定から学園・学校モノであるかを判定する。"""
    if "world_school" in (d.get("selected_tags") or []):
        return True
    pattern = re.compile(
        r"(学園|学校|高校|中学校|生徒|クラスメイト|同級生|後輩|先輩|部活|放課後|制服|校舎|教室|school|classroom)",
        re.I
    )
    for k in ("rough_idea", "work", "world", "relationship", "player_role", "partner_name"):
        val = wslot_value(d, k)
        for s in wiz_list(val):
            if s and pattern.search(str(s)):
                return True
    return False


def wizard_template_compose(d):
    """AIが使えなくても必ず非空の設定を作る。"""
    is_school = wizard_is_school_context(d)

    work = str(wslot_value(d, "work") or "").strip()[:60]
    raw_name = str(wslot_value(d, "partner_name") or "").strip()
    if raw_name:
        name = raw_name[:40]
    else:
        name = f"{work}のお相手" if work else "お相手"

    raw_world = "、".join(wiz_list(wslot_value(d, "world"))).strip()
    if raw_world:
        world = raw_world
    else:
        world = f"{work}の世界" if work else "現代"

    rel = "、".join(wiz_list(wslot_value(d, "relationship"))) or "特別な関係"
    tone = "、".join(wiz_list(wslot_value(d, "tone"))) or "穏やか"
    role = "、".join(wiz_list(wslot_value(d, "player_role"))) or "主人公"
    kinks = "、".join(wiz_list(wslot_value(d, "kinks"))) or "なし"
    start = str(wizard_effective_value(d, "start_point") or "出会った直後")
    pov = str(wizard_effective_value(d, "pov") or "second")
    style = str(wizard_effective_value(d, "style") or "novel")
    length = str(wizard_effective_value(d, "length") or "medium")
    player = str(wizard_effective_value(d, "player_name") or "あなた")
    player_profile = str(wizard_effective_value(d, "player_profile") or "")

    goal_raw = str(wizard_effective_value(d, "goal") or "おまかせ")
    goal_summary = "自由な対話と関係の進展" if goal_raw == "おまかせ" else goal_raw
    goal_action = "二人の関係を深めていく" if goal_raw == "おまかせ" else f"{goal_raw}に向かう"

    ending_label = {
        "happy": "ハッピーエンド",
        "bittersweet": "ビターエンド",
        "dark": "ダークエンド",
        "open": "オープン",
    }.get(str(wizard_effective_value(d, "ending") or ""), "おまかせ")
    ng = "、".join(wizard_ng_labels(d)) or "なし"

    # タイトル生成（作品名の重複防止）
    if work:
        if name == f"{work}のお相手":
            title = f"{work} — お相手との{rel}"
        elif name.startswith(work):
            stripped = name[len(work):].lstrip(" の")
            title = f"{work} — {stripped or 'お相手'}との{rel}"
        else:
            title = f"{work} — {name}との{rel}"
    else:
        title = f"{name}との{rel}"
    title = title[:40] or "新しい物語"

    # rough_idea の反映
    rough_idea = str(wslot_value(d, "rough_idea") or "").strip()
    idea_prefix = (rough_idea if rough_idea.endswith(("。", "！", "!", "？", "?", "\n")) else (rough_idea + "。")) if rough_idea else ""

    summary_core = (
        f"{world}を舞台に、{name}との{rel}を描く物語。"
        f"あなたは{role}として、{start}の状況から関わっていく。"
        f"目的は{goal_summary}。雰囲気は{tone}。"
    )
    summary = f"{idea_prefix}{summary_core}"[:300] if idea_prefix else summary_core[:300]

    scenario_core = (
        f"{world}。{name}はあなたの目の前にいる。"
        f"二人の関係は{rel}。あなたの立場は{role}。"
        f"物語は{start}から始まり、{goal_action}。"
        f"結末は{ending_label}。{tone}空気の中で、{name}との距離が変わり始める。"
    )
    scenario = f"{idea_prefix}{scenario_core}"[:2000] if idea_prefix else scenario_core[:2000]

    first = f"「……{name}です。よろしくね」\n{name}は少しだけ視線を逸らして、そう言った。"
    instructions = (
        f"あなたは日本語のインタラクティブ小説の語り手です。"
        f"視点は{pov}、文体は{style}、長さは{length}。"
        f"{name}の性格と口調を一貫させ、{tone}雰囲気を保つ。"
        f"物語の目的・ゴール: {goal_summary}。結末: {ending_label}。"
        f"あなたの設定: {player_profile or 'おまかせ'}。"
        f"特殊要素: {kinks}。NG: {ng}。"
        f"全員18歳以上の成人として描写する。"
    )[:2000]
    chars = wizard_build_chars(d, [], name)
    opening_image = wizard_opening_tags(d) or (
        ["classroom", "morning", "looking at viewer"] if is_school else ["indoors", "morning", "looking at viewer"]
    )
    warnings = ["AIを使わずにテンプレートで設定を生成しました。プレビューで調整してください。"]
    if not chars[0].get("tags"):
        if is_school:
            chars[0]["tags"] = ["1girl", "school uniform", "looking at viewer"]
        else:
            chars[0]["tags"] = ["1girl", "looking at viewer"]
        warnings.append("画像タグが空です。必要ならキャラプリセットを選んでください。")
    period = "午前"
    try:
        period = infer_initial_period(scenario + " " + first) or "午前"
    except Exception:
        pass
    setting = {
        "title": title,
        "summary": summary,
        "instructions": instructions,
        "scenario": scenario,
        "first_message": first,
        "character": {
            "name": name,
            "age": "なし" if is_school else "24",
            "profile": f"{name}。{rel}としてあなたの前に現れる。{tone}雰囲気を持つ。",
            "speech": f"「……{player}、どうする？」",
        },
        "chars": chars,
        "opening_image": opening_image,
        "elapsed_time": {"day": 1, "period": normalize_period(period)},
        "user_name": player,
        "project": str(wizard_effective_value(d, "project") or "")[:40],
    }
    return setting, warnings


def wizard_partner_preset(d):
    if d.get("partner_preset_off"):
        return None
    pid = str(d.get("partner_preset_id") or "").strip()
    name = str(wslot_value(d, "partner_name") or "").strip()
    presets = []
    try:
        presets = _g("char_presets")() or []
    except Exception:
        presets = []
    if pid:
        for p in presets:
            if pid in (str(p.get("id") or ""), str(p.get("name") or "")):
                return p
    if name:
        for p in presets:
            if str(p.get("name") or "") == name:
                return p
        try:
            return find_char(presets, name, fuzzy=True)
        except TypeError:
            try:
                return find_char(name)
            except Exception:
                return None
        except Exception:
            return None
    return None


def wizard_preset_tags(preset):
    """既存の char_presets のタグを、そのまま見た目の正として使う。"""
    out = []
    for t in split_tags((preset or {}).get("tags")):
        s = wizard_plain_text(t, 60)
        if s and s not in out:
            out.append(s)
    return out[:40]


def wizard_build_chars(d, raw_chars, name):
    """内部では list[dict] を返す。最終段で wizard_normalize_chars() が文字列化する。"""
    item = {}
    if isinstance(raw_chars, list):
        for x in raw_chars:
            if isinstance(x, dict) and str(x.get("name") or "").strip() == name:
                item = x
                break
        if not item:
            for x in raw_chars:
                if isinstance(x, dict):
                    item = x
                    break
    elif isinstance(raw_chars, dict):
        item = raw_chars

    preset = wizard_partner_preset(d)
    preset_tags = wizard_preset_tags(preset)
    ai_tags = wizard_clean_tags(item.get("tags"))
    if preset_tags:
        tags, src = preset_tags, "preset"
    else:
        tags = ai_tags
        src = "ai" if ai_tags else "template"
    work = str(wslot_value(d, "work") or "").strip()[:60]
    default_name = f"{work}のお相手" if work else "お相手"
    return [{
        "id": new_id(),
        "name": wizard_plain_text(name or (preset or {}).get("name") or default_name, 40),
        "tags": tags,
        "source": src,
        "locked": src == "preset",
    }]


def wizard_normalize_chars(chars):
    """本体の chars は tags がカンマ区切り文字列、id/source 必須。"""
    out = []
    for c in chars or []:
        if not isinstance(c, dict):
            continue
        name = wizard_plain_text(c.get("name"), 40)
        if not name:
            continue
        locked = bool(c.get("locked"))
        src = str(c.get("source") or ("preset" if locked else "ai"))
        if src in ("preset", "template") or locked:
            # プリセットやテンプレートは固定の正。服装・人数・品質タグも削らない。
            tags = ", ".join(wizard_preset_tags(c))
        else:
            tags = ", ".join(wizard_clean_tags(c.get("tags")))
            if not tags:
                tags = ", ".join(wizard_preset_tags(c))
        if not tags:
            continue
        out.append({
            "id": str(c.get("id") or new_id()),
            "name": name,
            "tags": tags,
            "locked": locked,
            "source": src,
        })
    return out


def wizard_age_is_minor(age):
    s = str(age or "")
    # 「17歳」のような明示年齢を最優先
    m = re.search(r"(\d+)\s*(歳|才)", s)
    if m:
        return int(m.group(1)) < 18
    # 「大学1年」の1を年齢と誤認しない
    if re.search(r"(大学|短大|専門学校|社会人|成人|大人)", s):
        return False
    # 数字だけでなく「高校生」「高2」などの表現も未成年として扱う
    # 「小生」「中生」のような一般語を誤爆しないよう、学校段階の語だけで判定する
    if re.search(r"(小学生|中学生|高校生|女子高生|男子高生|女子中学生|男子中学生|小学|中学|高校|中等部|高等部|幼女|幼児|JK|JC|JS|小[1-6]|中[1-3]|高[1-3])", s, re.I):
        return True
    m = re.search(r"\d+", s)
    return bool(m and int(m.group()) < 18)


def wizard_age_warning(age):
    s = str(age or "").strip()
    if s in ("なし", "未設定"):
        return ""
    if wizard_age_is_minor(s):
        return "18歳未満の年齢が含まれています。年齢を「なし」にします。"
    if not re.search(r"\d+", s):
        return "年齢が不明です。全員18歳以上の成人として扱います。"
    return ""


def wizard_sanitize_compose(d, raw):
    if not isinstance(raw, dict):
        raise WizardError("設定のJSONが不正です", code="bad_json")
    warnings = []
    ch = raw.get("character")
    if not isinstance(ch, dict):
        ch = {}
    work = str(wslot_value(d, "work") or "").strip()[:60]
    default_name = f"{work}のお相手" if work else "お相手"
    name = wizard_plain_text(ch.get("name"), 40) or str(wslot_value(d, "partner_name") or default_name)
    age = wizard_plain_text(ch.get("age"), 10) or "24"
    aw = wizard_age_warning(age)
    if aw:
        warnings.append(aw)
        if wizard_age_is_minor(age):
            age = "なし"
        elif not re.search(r"\d+", str(age or "")):
            age = "24"

    setting = {
        "title": wizard_plain_text(raw.get("title"), 40),
        "summary": wizard_plain_text(raw.get("summary"), 300),
        "instructions": wizard_plain_text(raw.get("instructions"), 2000),
        "scenario": wizard_plain_text(raw.get("scenario"), 2000),
        "first_message": wizard_plain_text(raw.get("first_message"), 1200),
        "character": {
            "name": name,
            "age": age,
            "profile": wizard_plain_text(ch.get("profile"), 600),
            "speech": wizard_plain_text(ch.get("speech"), 400),
        },
    }
    setting["chars"] = wizard_build_chars(d, raw.get("chars"), name)
    opening_image = wizard_clean_scene_tags(raw.get("opening_image"))
    if opening_image:
        setting["opening_image"] = opening_image

    et = raw.get("elapsed_time")
    day = _safe_day(et.get("day") if isinstance(et, dict) else 1, 1)
    period = normalize_period((et.get("period") if isinstance(et, dict) else "") or "午前")
    setting["elapsed_time"] = {"day": day, "period": period}
    setting["user_name"] = str(wizard_effective_value(d, "player_name") or "あなた")[:40]
    setting["project"] = str(wizard_effective_value(d, "project") or "")[:40]

    text = " ".join([setting["summary"], setting["scenario"], setting["first_message"]])
    for ng in wizard_ng_labels(d):
        if ng and ng.lower() in text.lower():
            warnings.append(f"NG「{ng}」に触れる可能性があります。プレビューで確認してください。")

    for w in (raw.get("warnings") or []):
        s = wizard_plain_text(w, 120)
        if s:
            warnings.append(s)
    alts = [wizard_plain_text(x, 40) for x in (raw.get("title_alternatives") or [])]
    warnings = list(dict.fromkeys(warnings))
    meta = {"source": "ai", "warnings": warnings, "title_alternatives": [x for x in alts if x][:3]}
    return setting, meta


def wizard_validate_compose(setting):
    missing = []
    for k in ("title", "summary", "instructions", "scenario", "first_message"):
        if not str((setting or {}).get(k) or "").strip():
            missing.append(k)
    ch = (setting or {}).get("character") or {}
    if not isinstance(ch, dict):
        missing.append("character")
    else:
        for k in ("name", "profile", "speech"):
            if not str(ch.get(k) or "").strip():
                missing.append(f"character.{k}")
    return missing


def wizard_build_final_setting(d, overrides=None):
    comp = d.get("compose") or {}
    setting = comp.get("setting")
    if not isinstance(setting, dict):
        raise WizardError("設定がまだ生成されていません", code="wizard_not_composed")
    if not isinstance(overrides, dict):
        overrides = {}

    base = merge_defaults(copy.deepcopy(d.get("base_setting") or {}))
    base.update(copy.deepcopy(RESET_PROGRESS))
    base.update(copy.deepcopy(RESET_STORY))
    base.update(copy.deepcopy(NO_IDE))
    base.update(copy.deepcopy(NO_FOLDER))
    for k in ("initial_elapsed_time", "rotation", "rotation_notice",
              "rotation_skip_until", "rotation_reserve"):
        base.pop(k, None)

    final = copy.deepcopy(setting)
    old_name = str((final.get("character") or {}).get("name") or "").strip()
    for k in ("title", "summary", "scenario", "first_message", "instructions"):
        if k in overrides:
            final[k] = wizard_plain_text(overrides.get(k), {"title": 40, "summary": 300}.get(k, 2000))
    if isinstance(overrides.get("character"), dict):
        ch = dict(final.get("character") or {})
        for k, lim in (("name", 40), ("age", 10), ("profile", 600), ("speech", 400)):
            if k in overrides["character"]:
                ch[k] = wizard_plain_text(overrides["character"].get(k), lim)
        final["character"] = ch
    if isinstance(overrides.get("chars"), list):
        chars = []
        for x in overrides["chars"][:3]:
            if not isinstance(x, dict):
                continue
            chars.append({
                "id": new_id(),
                "name": wizard_plain_text(x.get("name"), 40),
                "tags": wizard_clean_tags(x.get("tags")),
                "source": "user",
                "locked": bool(x.get("locked")),
            })
        if chars:
            final["chars"] = chars

    name = str((final.get("character") or {}).get("name") or "")
    user_name = ((wizard_plain_text(overrides.get("user_name"), 40) or "あなた")
                 if "user_name" in overrides else
                 str(wizard_effective_value(d, "player_name") or "あなた")[:40])
    final["user_name"] = user_name

    # プレビューで名前を変えたら、chars と第一声などにも追従させる
    chars_list = final.get("chars")
    if name and isinstance(chars_list, list) and chars_list:
        first_char = chars_list[0]
        if isinstance(first_char, dict):
            first_char["name"] = name
    if old_name and name and old_name != name:
        for k in ("first_message", "scenario", "summary", "instructions"):
            if isinstance(final.get(k), str):
                final[k] = final[k].replace(old_name, name)

    # instructions はプレビューで編集されても、確定事項を必ず末尾に入れる
    base_instr = str(final.get("instructions") or "").strip()
    base_instr = re.sub(r"\n\n# 確定事項\n(?:- .*\n?)*", "", base_instr).strip()
    facts_lines = ["\n\n# 確定事項"]
    is_placeholder_name = (not name) or name in ("ヒロイン", "お相手") or name.endswith("のお相手")
    if not is_placeholder_name:
        facts_lines.append(f"- 相手の名前: {name}")
    facts_lines.append(f"- あなたの名前: {user_name}")
    facts_lines.append(f"- あらすじ: {final.get('summary', '')}")
    goal_val = wizard_effective_value(d, "goal") or "おまかせ"
    if goal_val == "おまかせ":
        goal_val = "自由な対話と関係の進展"
    facts_lines.append(f"- 目的・ゴール: {goal_val}")
    facts_lines.append(f"- NG: {'、'.join(wizard_ng_labels(d)) or 'なし'}")
    facts = "\n".join(facts_lines)
    # 確定事項が長い instructions の末尾切れで消えないよう、facts の分を先に確保する
    final["instructions"] = (base_instr[:max(0, 2000 - len(facts))] + facts)[:2000]

    # プレビューで年齢を書き換えた場合も成人チェックする
    age_warn = wizard_age_warning((final.get("character") or {}).get("age"))
    if age_warn:
        age_val = (final.get("character") or {}).get("age")
        if wizard_age_is_minor(age_val):
            final.setdefault("character", {})["age"] = "なし"
        elif not re.search(r"\d+", str(age_val or "")):
            final.setdefault("character", {})["age"] = "24"

    # プレビューで必須項目を空にした場合は作成しない
    miss = wizard_validate_compose(final)
    if miss:
        raise WizardError("必須項目が空です: " + ", ".join(miss), code="bad_compose")

    for k in COMPOSE_SETTING_KEYS:
        if k in final and final[k] not in (None, "", []):
            base[k] = final[k]
    base["chars"] = wizard_normalize_chars(final.get("chars"))
    if "project" in overrides:
        base["project"] = wizard_plain_text(overrides.get("project"), 40)
    else:
        base["project"] = str(wizard_effective_value(d, "project")
                              or base.get("project") or "")[:40]
    base["user_name"] = user_name
    if not base.get("title"):
        base["title"] = "新しいチャンネル"

    # 新規環境（ベースチャンネルなし）は作成AIを本文AIにも引き継ぐ
    if not (d.get("base_setting") or {}) and not d.get("base_sid"):
        mc = d.get("model") or {}
        if mc.get("provider") in ("google", "vertex") and mc.get("model"):
            base["ai_provider"] = mc["provider"]
            if mc["provider"] == "google":
                base["google_model"] = mc["model"]
            else:
                base["vertex_model"] = mc["model"]
            base["thinking_level"] = mc.get("thinking_level") or "high"

    warnings = list(comp.get("warnings") or [])
    if age_warn:
        warnings.append(age_warn)
    warnings += [f"{SLOT_LABEL.get(k, k)}はおまかせで埋めました" for k in (comp.get("auto_slots") or [])]
    return base, list(dict.fromkeys(warnings))


def wizard_ai_enabled(d):
    mc = d.get("model") or {}
    if not mc.get("ai_enabled", True):
        return False
    return str(mc.get("provider") or "") != "none"


def wizard_error_code(msg, http=0):
    m = str(msg or "").lower()
    if http in (401, 403) or "api key" in m or "permission" in m or "unauthenticated" in m:
        return "no_key"
    if http == 404 or "not found" in m or "not_found" in m:
        return "model_not_found"
    try:
        if _is_filter_error_msg(msg):
            return "safety"
    except Exception:
        pass
    if "timeout" in m or "timed out" in m:
        return "timeout"
    if "quota" in m or "rate" in m or http == 429:
        return "quota"
    return "ai_error"


def wizard_generate(model, prompt, system="", max_tokens=4096, kind="compose", job=None):
    mc = model or {}
    if not mc.get("ai_enabled", True) or str(mc.get("provider") or "") == "none":
        raise WizardError("AIは無効です", code="ai_disabled")
    provider = str(mc.get("provider") or "").strip().lower()
    mid = str(mc.get("model") or "").strip()
    thinking = str(mc.get("thinking_level") or "high").strip().lower()
    if kind == "questions":
        thinking = "low"
    if provider == "antigravity":
        return wizard_ide_generate(mc, prompt, kind, job)
    if provider not in ("google", "vertex"):
        raise WizardError("作成AIのプロバイダーが未設定です", code="no_model")
    try:
        if provider == "google" and not mid:
            mid = google_ai.default_model()
        if provider == "vertex" and not mid:
            mid = google_ai.default_vertex_model()
    except Exception as e:
        raise WizardError(f"モデルを取得できません: {e}", code="model_not_found")
    try:
        res = google_ai.generate(
            mid, prompt, system=system, safety_off=True,
            max_output_tokens=max_tokens, fetch=False,
            timeout=180, provider=provider, thinking_level=thinking,
        )
    except Exception as e:
        code = wizard_error_code(str(e))
        raise WizardError(str(e), code=code, retryable=code in ("timeout", "quota", "ai_error"), status=502)
    if isinstance(res, str):
        text = res
        meta = {"provider": provider, "model": mid, "thinking_level": thinking}
    elif isinstance(res, dict):
        if res.get("error"):
            msg = str(res.get("error"))
            code = wizard_error_code(msg, _as_int(res.get("http_status"), 0))
            raise WizardError(msg, code=code, retryable=code in ("timeout", "quota", "ai_error"), status=502)
        text = str(res.get("text") or res.get("output") or "")
        meta = {"provider": provider, "model": mid, "thinking_level": thinking}
    else:
        raise WizardError("AIの応答形式が不正です", code="ai_error", retryable=True, status=502)
    if not text.strip():
        raise WizardError("AIが空の応答を返しました", code="ai_error", retryable=True, status=502)
    return text, meta


def wizard_ensure_builder():
    if _g("ide_poke") is None:
        raise WizardError("Antigravityが利用できません", code="antigravity_down")
    with wizard_lock:
        app = app_settings()
        cid = str(app.get("wizard_builder_ide") or "").strip()
        if cid:
            try:
                if _g("ide_poke").summary(cid):
                    return cid
            except Exception:
                pass
        os.makedirs(_g("WIZARD_IDE_DIR"), exist_ok=True)
        res = _g("ide_poke").new_conversation(_g("WIZARD_IDE_DIR"))
        # new_conversation は (会話ID, 説明) か (None, 失敗理由) を返す。
        if isinstance(res, str):
            nid, why = res, ""
        elif isinstance(res, (tuple, list)) and len(res) >= 2:
            nid, why = str(res[0] or ""), str(res[1] or "")
        else:
            nid, why = "", ""
        if not nid:
            raise WizardError(why or "作成AIの会話を作成できませんでした",
                              code="antigravity_down", retryable=True, status=502)
    # デッドロック防止: wizard_lock を解放してから lock で設定を保存する（ロック逆順ネスト防止）
    with lock:
        cur = read_json(_g("APP_SETTINGS"), {})
        cur["wizard_builder_ide"] = nid
        write_json(_g("APP_SETTINGS"), cur)
    return nid


def wizard_ide_generate(mc, prompt, kind="compose", job=None):
    cid = wizard_ensure_builder()
    mid = str(mc.get("model") or "").strip()
    try:
        summ = _g("ide_poke").summary(cid) or {}
        step = _as_int(summ.get("stepCount") or summ.get("step_count"), 0)
    except Exception:
        step = 0

    last_msg = ""
    # 同じ会話に複数の生成を同時に送らない（send_poke と同じ会話ロックを使う）
    with get_poke_lock(cid):
        if job and job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        sent = False
        for attempt in range(3):
            if job and job.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            try:
                res = _g("ide_poke").send_to_conversation(cid, prompt, model=mid or None)
            except Exception as e:
                raise WizardError(str(e), code="antigravity_down", retryable=True, status=502)
            ok = bool(res[0]) if isinstance(res, (tuple, list)) else bool(res)
            msg = str(res[1]) if isinstance(res, (tuple, list)) and len(res) > 1 else ""
            retry = bool(res[2]) if isinstance(res, (tuple, list)) and len(res) > 2 else False
            last_msg = msg or last_msg
            if ok:
                sent = True
                break
            if retry and attempt < 2:
                time.sleep(5)
                continue
            raise WizardError(msg or "送信できませんでした", code="antigravity_down", retryable=retry, status=502)
        if not sent:
            raise WizardError(last_msg or "送信できませんでした", code="antigravity_down", retryable=True, status=502)

        deadline = time.time() + 180
        while time.time() < deadline:
            if job and job.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            time.sleep(1)
            try:
                idle_ok, _st = _g("ide_poke").idle(cid)
            except Exception:
                idle_ok = False
            if not idle_ok:
                continue
            try:
                r = _g("ide_poke").last_reply(cid, after_step=step)
            except Exception:
                r = (None, None, "")
            text = str(r[0] or "") if isinstance(r, (tuple, list)) else str(r or "")
            err = str(r[1] or "") if isinstance(r, (tuple, list)) and len(r) > 1 else ""
            if err:
                last_msg = err
            if text.strip():
                return text, {"provider": "antigravity", "model": mid, "thinking_level": ""}
        raise WizardError(last_msg or "Antigravityの応答がタイムアウトしました", code="timeout", retryable=True, status=502)


def wizard_extract_json(text):
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise WizardError("JSONを読み取れませんでした", code="bad_json")
    try:
        return json.loads(s[a:b + 1])
    except ValueError as e:
        raise WizardError(f"JSONの形式が不正です: {e}", code="bad_json")


def wizard_job_running(jid):
    j = wizard_jobs.get(str(jid or ""))
    return bool(j and j.get("status") in ("queued", "running")
                and time.time() - j.get("ts_epoch", 0) < WIZARD_JOB_STALE)


def _wiz_job_recheck(job, d, expect_rev=None):
    """ロック内で必ず呼ぶ。古いオブジェクトを保存しないための関門。"""
    if not d or str(d.get("last_job") or "") != str(job.get("job_id") or ""):
        raise WizardError("古いジョブです", code="stale", status=409)
    if job.get("cancel") or job.get("status") == "canceled":
        raise WizardError("キャンセルしました", code="canceled")
    if _as_int(d.get("rev"), 0) != _as_int(job.get("base_rev") if expect_rev is None else expect_rev, 0):
        raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)


def wizard_start_job(draft, kind, expected_rev=None):
    if kind not in ("questions", "compose"):
        raise WizardError("ジョブ種別が不正です", code="bad_request")
    with wizard_lock:
        d = wizard_load(draft.get("id"))
        if not d:
            raise WizardError("下書きがありません", code="not_found", status=404)
        if expected_rev is not None and _as_int(expected_rev, -1) != _as_int(d.get("rev"), 0):
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(d, True)
            raise e
        if d.get("status") == "created":
            raise WizardError("作成済みです", code="created", status=409)
        if wizard_job_running(d.get("last_job")):
            raise WizardError("この下書きで別の処理を実行中です", code="wizard_busy", status=409)

        jid = _wiz_uid("wj_")
        job = {
            "job_id": jid,
            "draft_id": d["id"],
            "kind": kind,
            "status": "queued",
            "progress": "質問を考えています" if kind == "questions" else "設定を書いています",
            "result": None,
            "error": None,
            "error_code": "",
            "retryable": False,
            "cancel": False,
            "created": now(),
            "ts_epoch": time.time(),
            "base_rev": d.get("rev", 0),
        }
        wizard_jobs[jid] = job
        d["last_job"] = jid
        if kind == "compose":
            d["status"] = "composing"
            d["step"] = "creating"
        else:
            d["step"] = "questions"
        wizard_save(d, bump=False)
        public = wizard_public(d, True)

    def run():
        j = wizard_jobs.get(jid)
        if not j:
            return
        if j.get("status") == "canceled" or j.get("cancel"):
            return
        j["status"] = "running"
        j["ts_epoch"] = time.time()
        try:
            if j.get("cancel") or j.get("status") == "canceled":
                raise WizardError("キャンセルしました", code="canceled")
            if kind == "questions":
                result = wizard_do_questions(j)
            else:
                result = wizard_do_compose(j)
            if j.get("cancel") or j.get("status") == "canceled":
                raise WizardError("キャンセルしました", code="canceled")
            j["result"] = result
            j["status"] = "done"
        except WizardError as e:
            if e.code == "canceled" or j.get("status") == "canceled" or j.get("cancel"):
                j["status"] = "canceled"
            else:
                j["status"] = "error"
                j["error"] = str(e)
                j["error_code"] = e.code
                j["retryable"] = e.retryable
        except Exception as e:
            if j.get("status") == "canceled" or j.get("cancel"):
                j["status"] = "canceled"
            else:
                j["status"] = "error"
                j["error"] = str(e)
                j["error_code"] = "wizard_error"
                j["retryable"] = True
        finally:
            j["ts_epoch"] = time.time()
            if j.get("kind") == "compose" and j.get("status") == "error":
                with wizard_lock:
                    dd = wizard_load(j["draft_id"])
                    if dd and dd.get("status") == "composing":
                        has = bool((dd.get("compose") or {}).get("setting"))
                        dd["status"] = "ready" if has else "interview"
                        dd["step"] = "preview" if has else "tags"
                        wizard_save(dd, bump=False)
            elif j.get("kind") == "questions" and j.get("status") == "error":
                with wizard_lock:
                    dd = wizard_load(j["draft_id"])
                    if dd:
                        dd["step"] = "tags"
                        wizard_save(dd, bump=False)

    threading.Thread(target=run, daemon=True).start()
    return job, public


def wizard_do_questions(job):
    with wizard_lock:
        d = wizard_load(job["draft_id"])
        _wiz_job_recheck(job, d)

    pending = [q for q in (d.get("questions") or []) if q.get("id") not in (d.get("asked_question_ids") or [])]
    if pending:
        return {"draft": wizard_public(d, True), "questions": pending, "done": False}
    if not wizard_ai_enabled(d):
        with wizard_lock:
            d = wizard_load(job["draft_id"])
            _wiz_job_recheck(job, d)
            d["done_questions"] = True
            d["status"] = "ready" if wizard_ready(d) else "interview"
            wizard_save(d)
        return {"draft": wizard_public(d, True), "questions": [], "done": True}
    if d.get("done_questions") or d.get("round", 0) >= WIZARD_MAX_ROUNDS or not wizard_askable_slots(d):
        with wizard_lock:
            d = wizard_load(job["draft_id"])
            _wiz_job_recheck(job, d)
            d["done_questions"] = True
            d["status"] = "ready" if wizard_ready(d) else "interview"
            wizard_save(d)
        return {"draft": wizard_public(d, True), "questions": [], "done": True}

    with wizard_lock:
        d = wizard_load(job["draft_id"])
        _wiz_job_recheck(job, d)
        if d.get("ai_calls", 0) >= WIZARD_AI_CALL_LIMIT:
            raise WizardError("AI呼び出しの上限に達しました", code="ai_limit", status=429)
        d["ai_calls"] = _as_int(d.get("ai_calls"), 0) + 1
        wizard_save(d, bump=False)

    try:
        text, _meta = wizard_generate(d.get("model") or {}, wizard_question_prompt(d),
                                      system="あなたは質問設計の専門家です。JSONのみを返します。",
                                      max_tokens=2048, kind="questions", job=job)
        raw = wizard_extract_json(text)
        qs = wizard_sanitize_questions(d, raw.get("questions") if isinstance(raw, dict) else [])
    except WizardError as e:
        if job.get("cancel") or job.get("status") == "canceled":
            raise WizardError("キャンセルしました", code="canceled")
        raise

    with wizard_lock:
        d = wizard_load(job["draft_id"])
        _wiz_job_recheck(job, d)
        d["questions"] = qs
        d["round"] = min(_as_int(d.get("round"), 0) + 1, WIZARD_MAX_ROUNDS)
        d["done_questions"] = not qs
        d["status"] = "interview" if qs else ("ready" if wizard_ready(d) else "interview")
        if qs:
            d["step"] = "questions"
        wizard_save(d)
        pub = wizard_public(d, True)
    return {"draft": pub, "questions": qs, "done": not qs}


def wizard_do_compose(job):
    with wizard_lock:
        d = wizard_load(job["draft_id"])
        _wiz_job_recheck(job, d)
        if (d.get("compose") or {}).get("setting") and not d.get("compose_stale"):
            return {"draft": wizard_public(d, True)}

        auto_slots = [k for k, s in (d.get("slots") or {}).items()
                      if (s or {}).get("source") == "auto"]
        missing_before = wizard_missing_required(d)
        if missing_before:
            for k in missing_before:
                if k not in auto_slots:
                    auto_slots.append(k)
                wslot_set_auto(d, k)
            # 自動補完したスロットを即座に永続化（以後の wizard_load で破棄されないよう bump=False で保存）
            wizard_save(d, bump=False)

    if not wizard_ai_enabled(d):
        with wizard_lock:
            d = wizard_load(job["draft_id"])
            _wiz_job_recheck(job, d)
        setting, warnings = wizard_template_compose(d)
        cmeta = {"source": "template", "warnings": warnings,
                 "title_alternatives": [], "auto_slots": auto_slots, "model": copy.deepcopy(d.get("model") or {})}
        with wizard_lock:
            d = wizard_load(job["draft_id"])
            _wiz_job_recheck(job, d)
            d["compose"] = dict(cmeta, setting=setting)
            d["compose_stale"] = False
            d["status"] = "ready"
            d["step"] = "preview"
            d["done_questions"] = True
            d["warnings"] = list(dict.fromkeys((d.get("warnings") or []) + list(warnings)))
            wizard_save(d)
            pub = wizard_public(d, True)
        return {"draft": pub}

    setting = None
    cmeta = None
    last_error = None
    model = copy.deepcopy(d.get("model") or {})
    for attempt in (1, 2):
        with wizard_lock:
            d = wizard_load(job["draft_id"])
            _wiz_job_recheck(job, d)
            if d.get("ai_calls", 0) >= WIZARD_AI_CALL_LIMIT:
                raise WizardError("AI呼び出しの上限に達しました", code="ai_limit", status=429)
            d["ai_calls"] = _as_int(d.get("ai_calls"), 0) + 1
            wizard_save(d, bump=False)

        try:
            prompt = wizard_compose_prompt(d)
            if attempt == 2:
                prompt += "\n\n前回の出力は不正でした。JSONのみを返し、必須項目をすべて埋めてください。"
            text, _meta = wizard_generate(model, prompt,
                                          system="あなたは設定ライターです。JSONのみを返します。",
                                          max_tokens=8192, kind="compose", job=job)
            raw = wizard_extract_json(text)
            candidate, cmeta = wizard_sanitize_compose(d, raw)
            miss = wizard_validate_compose(candidate)
            if miss:
                raise WizardError("必須項目が不足しています: " + ", ".join(miss), code="bad_compose")
            setting = candidate
            break
        except WizardError as e:
            last_error = e
            if job.get("cancel") or job.get("status") == "canceled":
                raise WizardError("キャンセルしました", code="canceled")
            if e.code in ("stale", "canceled"):
                raise
            if attempt == 2 or e.code not in ("bad_json", "bad_compose", "timeout", "ai_error", "quota", "no_key", "safety", "ai_disabled"):
                break
            if e.code == "timeout" and model.get("thinking_level") == "high":
                model["thinking_level"] = "medium"

    if job.get("cancel") or job.get("status") == "canceled":
        raise WizardError("キャンセルしました", code="canceled")

    if setting is None:
        if wizard_ai_enabled(d):
            raise (last_error or WizardError("AIによる設定生成に失敗しました"))
        setting, warnings = wizard_template_compose(d)
        cmeta = {"source": "template", "warnings": warnings, "title_alternatives": []}
    cmeta = dict(cmeta or {})
    cmeta["auto_slots"] = auto_slots
    cmeta["model"] = copy.deepcopy(model)

    with wizard_lock:
        d = wizard_load(job["draft_id"])
        _wiz_job_recheck(job, d)
        d["compose"] = dict(cmeta, setting=setting)
        d["compose_stale"] = False
        d["status"] = "ready"
        d["step"] = "preview"
        d["done_questions"] = True
        d["warnings"] = list(dict.fromkeys((d.get("warnings") or []) + list(cmeta.get("warnings") or [])))
        wizard_save(d)
        pub = wizard_public(d, True)
    return {"draft": pub}


def wizard_new_draft(base, model):
    t = now()
    return {
        "schema_version": 1,
        "id": _wiz_uid("wiz_"),
        "rev": 0,
        "created": t,
        "updated": t,
        "status": "interview",
        "step": "base",
        "base_sid": "",
        "base_title": "",
        "inherit_base": False,
        "base_setting": copy.deepcopy(base or {}),
        "model": copy.deepcopy(model or {}),
        "slots": {},
        "selected_tags": [],
        "selected_ng": ["ng_minor"],
        "custom_ng": [],
        "partner_preset_id": "",
        "partner_preset_off": False,
        "questions": [],
        "asked_slots": [],
        "asked_question_ids": [],
        "round": 0,
        "done_questions": False,
        "compose": None,
        "compose_stale": True,
        "last_job": "",
        "created_sid": "",
        "warnings": [],
        "ai_calls": 0,
    }


def wizard_public(d, full=False):
    missing = wizard_missing_required(d)
    out = {
        "id": d.get("id"),
        "rev": d.get("rev", 0),
        "status": d.get("status", "interview"),
        "step": d.get("step", "idea"),
        "created": d.get("created"),
        "updated": d.get("updated"),
        "base_sid": d.get("base_sid", ""),
        "base_title": str(d.get("base_title") or
                          ((d.get("base_setting") or {}).get("title") or "")),
        "inherit_base": bool(d.get("inherit_base", bool(d.get("base_setting")))),
        "model": copy.deepcopy(d.get("model") or {}),
        "selected_tags": list(d.get("selected_tags") or []),
        "partner_preset_id": str(d.get("partner_preset_id") or ""),
        "partner_preset_off": bool(d.get("partner_preset_off")),
        "selected_ng": list(d.get("selected_ng") or []),
        "custom_ng": list(d.get("custom_ng") or []),
        "round": d.get("round", 0),
        "done_questions": bool(d.get("done_questions")),
        "ready": not missing,
        "missing": missing,
        "last_job": d.get("last_job", ""),
        "created_sid": d.get("created_sid", ""),
        "created_title": d.get("created_title", ""),
        "warnings": list(d.get("warnings") or []),
        "compose_stale": bool(d.get("compose_stale", True)),
        "title_hint": ((d.get("compose") or {}).get("setting") or {}).get("title")
                      or d.get("created_title")
                      or str(wslot_value(d, "work") or "")[:24]
                      or str(wslot_value(d, "rough_idea") or "")[:24]
                      or "新しいチャンネル",
        "defaults": {
            "project": str((d.get("base_setting") or {}).get("project") or ""),
            "user_name": str((d.get("base_setting") or {}).get("user_name") or "あなた"),
        },
    }
    if full:
        out["slots"] = {
            k: {
                "label": WIZARD_SLOTS[k]["label"],
                "value": copy.deepcopy(wslot_value(d, k)),
                "source": wslot_source(d, k),
            }
            for k in WIZARD_SLOTS
        }
        out["questions"] = copy.deepcopy(d.get("questions") or [])
        out["asked_slots"] = list(d.get("asked_slots") or [])
        out["asked_question_ids"] = list(d.get("asked_question_ids") or [])
        out["compose"] = copy.deepcopy(d.get("compose"))
        base = d.get("base_setting") or {}
        out["base_info"] = {
            "ai_provider": base.get("ai_provider"),
            "google_model": base.get("google_model"),
            "vertex_model": base.get("vertex_model"),
            "image_mode": base.get("image_mode") or "always",
            "suggest_choices": base.get("suggest_choices"),
            "project": base.get("project"),
        }
    return out


def wizard_set_base(d, base_sid, inherit):
    """最初の「ベース設定」画面の確定。base_sid は再開判定用に残す。"""
    if d.get("status") == "created":
        raise WizardError("作成済みの下書きは変更できません", code="created", status=409)
    if d.get("slots") or d.get("questions") or d.get("compose"):
        raise WizardError("設定を入力した後はベース設定を変更できません",
                          code="base_locked", status=409)

    base_sid = str(base_sid or "")
    src = {}
    if base_sid:
        try:
            src = (load(base_sid, readonly=True) or {}).get("setting") or {}
        except Exception:
            src = {}

    if inherit:
        if not base_sid:
            raise WizardError("ベースにするチャンネルを選んでください", code="bad_base")
        if not src:
            raise WizardError("ベースにするチャンネルが見つかりません", code="bad_base")
        d["base_setting"] = copy.deepcopy(src)
        d["inherit_base"] = True
    else:
        d["base_setting"] = {}
        d["inherit_base"] = False

    if base_sid:
        d["base_sid"] = base_sid
    d["base_title"] = str(src.get("title") or d.get("base_title") or "")
    d["step"] = "idea"
    d["status"] = "interview"
    d["done_questions"] = False
    d["compose_stale"] = True
    return d


def wizard_model_from_app():
    app = app_settings()
    return {
        "provider": str(app.get("wizard_provider") or "google").strip().lower(),
        "model": str(app.get("wizard_model") or "").strip(),
        "thinking_level": str(app.get("wizard_thinking_level") or "high").strip().lower(),
        "ai_enabled": True,
    }


def wizard_apply_slots(d, slots):
    if not isinstance(slots, dict):
        raise WizardError("slotsが不正です", code="bad_request")
    for key in APPLY_ORDER:
        if key not in slots:
            continue
        item = slots[key]
        if isinstance(item, dict):
            val = item.get("value")
            src = str(item.get("source") or "user")
        else:
            val = item
            src = "user"
        if src not in ("user", "auto", "derived"):
            src = "user"
        if src == "auto":
            wslot_set_auto(d, key)
        else:
            wslot_set(d, key, val, src)
    for key, item in slots.items():
        if key in APPLY_ORDER or key not in WIZARD_SLOTS:
            continue
        val = item.get("value") if isinstance(item, dict) else item
        wslot_set(d, key, val, "user")
    if any(k in slots for k in ("partner_type", "partner_name", "work")):
        d["partner_preset_id"] = ""
        d["partner_preset_off"] = False
    d["compose_stale"] = True


def wizard_apply_tags(d, body):
    ids = [str(x) for x in (body.get("selected_tags") or [])]
    d["selected_tags"] = [x for x in dict.fromkeys(ids) if wizard_tag_by_id(x)]
    presets = {x["id"]: x for x in wizard_tags().get("ng_presets", [])}
    ngs = [str(x) for x in (body.get("selected_ng") or [])]
    ngs = [x for x in dict.fromkeys(ngs) if x in presets]
    if "ng_minor" not in ngs:
        ngs.insert(0, "ng_minor")
    d["selected_ng"] = ngs
    custom = body.get("custom_ng") or []
    if isinstance(custom, str):
        custom = re.split(r"[,、\n]", custom)
    d["custom_ng"] = [wizard_plain_text(x, 30) for x in (custom or []) if wizard_plain_text(x, 30)][:10]
    ng_set = wizard_ng_conflicts(d)
    d["selected_tags"] = [
        x for x in d["selected_tags"]
        if not wizard_tag_hits_ng(wizard_tag_by_id(x), ng_set)
    ]
    wizard_recompute_derived(d)
    d["compose_stale"] = True


def wizard_apply_answer(d, body):
    if d.get("status") == "created":
        raise WizardError("作成済みの下書きは変更できません", code="created", status=409)
    if wizard_job_running(d.get("last_job")):
        raise WizardError("生成中です。完了してから回答してください", code="wizard_busy", status=409)

    qid = str(body.get("question_id") or "")
    auto = bool(body.get("auto"))
    custom = wizard_plain_text(body.get("custom_text"), 300)
    step = str(body.get("step") or d.get("step") or "idea")
    if step in ("base", "idea", "partner", "tags", "questions", "preview", "creating"):
        d["step"] = step

    fixed_slot = {
        "fixed_idea": "rough_idea",
        "fixed_partner_type": "partner_type",
        "fixed_work": "work",
        "fixed_partner_name": "partner_name",
        "fixed_player": "player_type",
        "fixed_player_work": "player_work",
        "fixed_player_name": "player_name",
        "fixed_player_profile": "player_profile",
        "fixed_start_point": "start_point",
        "fixed_canon_fidelity": "canon_fidelity",
        "fixed_goal": "goal",
        "fixed_ending": "ending",
        "fixed_project": "project",
    }
    if qid in fixed_slot:
        key = fixed_slot[qid]
        if auto:
            wslot_set_auto(d, key)
        elif key in ("rough_idea", "project", "work", "partner_name",
                     "player_work", "player_name", "player_profile",
                     "start_point", "goal"):
            wslot_set(d, key, custom, "user")
        else:
            opts = body.get("option_ids") or []
            val = str(opts[0]) if opts else custom
            spec = WIZARD_SLOTS[key]
            if spec.get("type") == "enum" and val not in (spec.get("values") or []):
                raise WizardError("選択肢が不正です", code="bad_answer")
            wslot_set(d, key, val, "user")
        if key in ("partner_type", "partner_name", "work"):
            d["partner_preset_id"] = ""
            d["partner_preset_off"] = False
        return

    if qid == "fixed_tags":
        wizard_apply_tags(d, body)
        return
    if qid == "fixed_ng":
        wizard_apply_tags(d, {
            "selected_tags": d.get("selected_tags") or [],
            "selected_ng": body.get("option_ids") or [],
            "custom_ng": body.get("custom_text") or "",
        })
        return
    if qid == "fixed_preset":
        pid = str((body.get("option_ids") or [""])[0] or "")
        d["partner_preset_id"] = pid
        d["partner_preset_off"] = not bool(pid)
        preset = wizard_partner_preset({**d, "partner_preset_id": pid, "partner_preset_off": d["partner_preset_off"]})
        if preset and not wslot_value(d, "partner_name"):
            wslot_set(d, "partner_name", preset.get("name") or "", "user")
        d["compose_stale"] = True
        return

    q = next((x for x in (d.get("questions") or []) if x.get("id") == qid), None)
    if not q:
        raise WizardError("質問が見つかりません。再読み込みしてください", code="bad_question")
    slot = q.get("slot")
    if slot not in WIZARD_SLOTS:
        raise WizardError("質問のスロットが不正です", code="bad_question")

    if auto:
        wslot_set_auto(d, slot)
    else:
        # 自由入力が非空なら、選択肢より優先する
        if custom:
            opt = {"id": "free", "label": custom[:20],
                   "effects": {slot: custom[:120]}, "invalidates": []}
        else:
            opt = next((o for o in (q.get("options") or [])
                        if o.get("id") in (body.get("option_ids") or [])), None)
        if not opt:
            raise WizardError("選択肢を選ぶか、自由入力してください", code="bad_answer")
        effects = dict(opt.get("effects") or {})
        if slot not in effects:
            effects[slot] = [opt.get("label")] if WIZARD_SLOTS[slot].get("type") == "multi" else opt.get("label")
        for key in APPLY_ORDER:
            if key in effects:
                wslot_set(d, key, effects[key], "answer")
        for key in (opt.get("invalidates") or []):
            if key in WIZARD_SLOTS and wslot_source(d, key) != "locked":
                d["slots"].pop(key, None)

    if slot not in (d.get("asked_slots") or []):
        d.setdefault("asked_slots", []).append(slot)
    if qid not in (d.get("asked_question_ids") or []):
        d.setdefault("asked_question_ids", []).append(qid)
    d["questions"] = [x for x in (d.get("questions") or []) if x.get("id") != qid]
    if not d["questions"] and _as_int(d.get("round"), 0) >= WIZARD_MAX_ROUNDS:
        d["done_questions"] = True
    d["compose_stale"] = True


def wizard_apply_preview(d, setting):
    if not isinstance(setting, dict):
        raise WizardError("プレビュー内容が不正です", code="bad_request")
    comp = d.get("compose") or {}
    cur = comp.get("setting")
    if not isinstance(cur, dict):
        raise WizardError("設定がまだ生成されていません", code="wizard_not_composed")
    for k in ("title", "summary", "scenario", "first_message", "instructions"):
        if k in setting:
            cur[k] = wizard_plain_text(setting.get(k), {"title": 40, "summary": 300}.get(k, 2000))
    if isinstance(setting.get("character"), dict):
        ch = dict(cur.get("character") or {})
        for k, lim in (("name", 40), ("age", 10), ("profile", 600), ("speech", 400)):
            if k in setting["character"]:
                ch[k] = wizard_plain_text(setting["character"].get(k), lim)
        cur["character"] = ch
    if isinstance(setting.get("chars"), list):
        chars = []
        for x in setting["chars"][:3]:
            if not isinstance(x, dict):
                continue
            chars.append({
                "id": new_id(),
                "name": wizard_plain_text(x.get("name"), 40),
                "tags": wizard_clean_tags(x.get("tags")),
                "source": "user",
                "locked": bool(x.get("locked")),
            })
        if chars:
            cur["chars"] = chars
    if "project" in setting:
        cur["project"] = wizard_plain_text(setting.get("project"), 40)
    if "user_name" in setting:
        cur["user_name"] = wizard_plain_text(setting.get("user_name"), 40)
    miss = wizard_validate_compose(cur)
    if miss:
        raise WizardError("必須項目が不足しています: " + ", ".join(miss), code="bad_compose")
    comp["setting"] = cur
    d["compose"] = comp
    d["compose_stale"] = False


def _wizard_created_result(d):
    """作成済み下書きの冪等応答。作成済みでなければ None。"""
    sid = d.get("created_sid")
    if not sid:
        return None
    try:
        if not os.path.exists(spath(sid)):
            return None
        s0 = load(sid)
        mid0 = wizard_opening_message_id(s0)
        return {"ok": True, "sid": sid,
                "title": (d.get("compose") or {}).get("setting", {}).get("title", ""),
                "draft": wizard_public(d, True), "created": True,
                "opening_image": bool(mid0), "opening_image_mid": mid0}
    except Exception:
        return None


def wizard_create(d, overrides, use_template=False):
    """同じ下書きを二重に作成しないよう直列化し、最新の下書きで判定する。"""
    with wizard_create_lock:
        fresh = wizard_load(d.get("id")) or d
        if _as_int(fresh.get("rev"), 0) != _as_int(d.get("rev"), -1):
            # 連打・再送・リロードで rev が古くても、作成済みなら同じ sid を返す（二重作成しない）
            done = _wizard_created_result(fresh)
            if done:
                return done
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(fresh, True)
            raise e
        return _wizard_create_locked(fresh, overrides, use_template)


def _wizard_create_locked(d, overrides, use_template=False):
    done = _wizard_created_result(d)
    if done:
        return done
    if d.get("created_sid"):
        d["created_sid"] = ""

    comp = d.get("compose") or {}
    if not isinstance(comp.get("setting"), dict):
        if not use_template:
            raise WizardError("設定がまだ生成されていません", code="wizard_not_composed")
        setting, warnings = wizard_template_compose(d)
        d["compose"] = {
            "setting": setting,
            "source": "template",
            "warnings": warnings,
            "auto_slots": [],
            "title_alternatives": [],
        }
        d["compose_stale"] = False
        d["status"] = "ready"
        d["warnings"] = list(dict.fromkeys((d.get("warnings") or []) + list(warnings)))
        wizard_save(d)

    comp = d.get("compose") or {}
    comp_setting = comp.get("setting") if isinstance(comp.get("setting"), dict) else {}
    opening_tags = wizard_clean_scene_tags(comp_setting.get("opening_image"))
    if not opening_tags:
        opening_tags = wizard_opening_tags(d)

    final, warnings = wizard_build_final_setting(d, overrides)
    warnings = list(dict.fromkeys((d.get("warnings") or []) + list(warnings)))
    s = create_session(final)
    sid = s.get("id")
    if not sid:
        raise WizardError("チャンネル作成に失敗しました", code="create_failed", status=500)
    with wizard_lock:
        d = wizard_load(d["id"]) or d
        d["status"] = "created"
        d["created_sid"] = sid
        d["step"] = "preview"
        d["warnings"] = list(warnings)
        wizard_save(d)
        pub = wizard_public(d, True)

    # 案A: 画像の完成は待たない。最初のassistant発言にqueuedで積むだけ。
    opening_mid = ""
    try:
        opening_mid = wizard_enqueue_opening_image(s, final, opening_tags)
    except Exception:
        warnings.append("開幕画像の生成を開始できませんでした（チャンネルは作成済みです）。")

    return {"ok": True, "sid": sid, "title": (s.get("setting") or {}).get("title", ""),
            "draft": pub, "warnings": warnings,
            "opening_image": bool(opening_mid), "opening_image_mid": opening_mid}


def _wizard_manual_created_result(d):
    """手動作成済み下書きの冪等応答。作成済みでなければ None。"""
    sid = d.get("created_sid")
    if not sid:
        return None
    try:
        if not os.path.exists(spath(sid)):
            return None
        s0 = load(sid, readonly=True)
        title = d.get("created_title") or (s0.get("setting") or {}).get("title", "")
        return {
            "ok": True,
            "sid": sid,
            "title": title,
            "draft": wizard_public(d, True),
            "created": True,
            "warnings": [],
        }
    except Exception:
        return None


def wizard_manual_create(d, body):
    """手動でチャンネルを作成する（AI生成を行わない）。"""
    with wizard_create_lock:
        fresh = wizard_load(d.get("id")) or d
        done = _wizard_manual_created_result(fresh)
        if done:
            return done

        if fresh.get("created_sid") or fresh.get("created_title"):
            with wizard_lock:
                fresh = wizard_load(fresh["id"]) or fresh
                fresh["created_sid"] = ""
                fresh.pop("created_title", None)
                if fresh.get("status") == "created":
                    fresh["status"] = "interview"
                wizard_save(fresh, bump=False)

        if _as_int(fresh.get("rev"), 0) != _as_int(body.get("rev"), -1):
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(fresh, True)
            raise e

        if wizard_job_running(fresh.get("last_job")):
            raise WizardError("生成中です。完了してから操作してください", code="wizard_busy", status=409)

        title = wizard_plain_text(body.get("title"), 40)
        if not title:
            raise WizardError("チャンネル名を入力してください", code="bad_title", status=400)

        inherit = bool(fresh.get("inherit_base"))
        base_st = fresh.get("base_setting")
        warnings = []
        if inherit:
            if isinstance(base_st, dict) and base_st:
                setting = new_channel_setting(base_st)
            else:
                setting = None
                warnings.append("引き継ぎ元の設定が見つかりませんでした。初期設定で作成しました。")
        else:
            setting = None

        if setting is None:
            setting = {"title": title}
        else:
            setting["title"] = title

        s = create_session(setting)
        sid = s.get("id")
        if not sid:
            raise WizardError("チャンネル作成に失敗しました", code="create_failed", status=500)

        with wizard_lock:
            fresh = wizard_load(fresh["id"]) or fresh
            fresh["status"] = "created"
            fresh["created_sid"] = sid
            fresh["created_title"] = title
            fresh["step"] = "preview"
            fresh["updated"] = now()
            fresh["warnings"] = list(dict.fromkeys((fresh.get("warnings") or []) + list(warnings)))
            wizard_save(fresh)
            pub = wizard_public(fresh, True)

        return {
            "ok": True,
            "sid": sid,
            "title": title,
            "draft": pub,
            "warnings": warnings,
        }


def wizard_model_tier(label):
    base = re.sub(r"\s*[\(（].*?[\)）]\s*$", "", str(label or "")).lower()
    for k in ("flash", "mini", "haiku", "lite", "fast", "nano"):
        if k in base:
            return "fast"
    for k in ("pro", "ultra", "opus", "sonnet", "gpt-5", "gpt-4.1", "gemini-3", "gemini-2.5-pro"):
        if k in base:
            return "high"
    return "standard"


def wizard_models_payload(provider):
    provider = str(provider or "google").strip().lower()
    if provider not in ("google", "vertex", "antigravity"):
        raise WizardError("プロバイダーが不正です", code="bad_request")

    now_t = time.time()
    with wizard_lock:
        c = wizard_model_cache.get(provider)
        if c and now_t - c[0] < 300 and not (c[1] or {}).get("error"):
            return c[1]

    app = app_settings()
    current = str(app.get("wizard_model") or "").strip()
    rows, err, configured = [], "", True

    try:
        if provider == "antigravity":
            if _g("ide_poke") is None:
                raise RuntimeError("Antigravityが利用できません")
            res, why = _g("ide_poke").models()          # ← タプル
            if not res:
                configured = False
                err = why or "Antigravityのモデル一覧を読めませんでした"
            else:
                default = str(res.get("default") or "")
                for m in (res.get("models") or []):
                    if not isinstance(m, dict):
                        continue
                    mid = str(m.get("model") or m.get("id") or "")
                    if not mid:
                        continue
                    label = str(m.get("label") or mid)
                    left = m.get("left")
                    rows.append({
                        "id": mid, "label": label,
                        "tier": wizard_model_tier(label),
                        "configured": bool(current and current == mid),
                        "available": left is None or _as_int(left, 1) > 0,
                        "left": left,
                    })
                if default and not any(x["id"] == default for x in rows):
                    rows.insert(0, {
                        "id": default, "label": default,
                        "tier": wizard_model_tier(default),
                        "configured": current == default,
                        "available": True, "left": None,
                    })
        else:
            if provider == "google":
                try:
                    # load_key() は (key, source) を返すため、bool(tuple) では常に True になる。
                    configured = bool(google_ai.load_key()[0])
                except Exception:
                    configured = False
            else:
                try:
                    configured = bool(google_ai.load_vertex_config().get("configured"))
                except Exception:
                    configured = False

            if not configured:
                err = "認証情報が未設定です"
            else:
                raw = google_ai.list_models(provider=provider, probe=False) or []
                for m in raw:
                    if isinstance(m, str):
                        mid, label = m, m
                    elif isinstance(m, dict):
                        mid = str(m.get("id") or m.get("name") or m.get("model") or "")
                        label = str(m.get("label") or m.get("display_name") or m.get("displayName") or mid)
                    else:
                        continue
                    mid = mid.replace("models/", "")
                    if not mid:
                        continue
                    rows.append({
                        "id": mid, "label": label,
                        "tier": wizard_model_tier(label),
                        "configured": bool(current and current == mid),
                        "available": bool(m.get("available", True)) if isinstance(m, dict) else True,
                        "left": None,
                    })
    except Exception as e:
        err = str(e)

    rows.sort(key=lambda x: ({"high": 0, "standard": 1, "fast": 2}.get(x.get("tier"), 1),
                             x.get("label", "")))
    payload = {
        "ok": not bool(err) or bool(rows),
        "provider": provider,
        "models": rows,
        "current": current,
        "configured": configured,
        "recommended": "high" if any(x["tier"] == "high" for x in rows) else "standard",
        "error": err,
    }
    with wizard_lock:
        if err:
            wizard_model_cache.pop(provider, None)
        else:
            wizard_model_cache[provider] = (now_t, payload)
    return payload


def wizard_test_model(body):
    provider = str((body or {}).get("provider") or "").strip().lower()
    model = str((body or {}).get("model") or "").strip()
    if provider not in ("google", "vertex", "antigravity"):
        raise WizardError("プロバイダーが不正です", code="bad_request")
    if provider == "antigravity":
        cid = wizard_ensure_builder()
        try:
            ok_idle, _st = _g("ide_poke").idle(cid)
        except Exception:
            ok_idle = False
        if not ok_idle:
            raise WizardError("Antigravityの会話が応答中です。少し待ってから試してください。",
                              code="antigravity_down", retryable=True, status=502)
        return {"ok": True, "note": "Antigravityとの接続を確認しました。"}
    text, meta = wizard_generate(
        {"provider": provider, "model": model, "thinking_level": "minimal"},
        'JSONで {"ok": true, "message": "接続OK"} とだけ返してください。',
        system="JSONのみを返します。", max_tokens=128, kind="test",
    )
    raw = wizard_extract_json(text)
    if not isinstance(raw, dict):
        raise WizardError("テスト応答が不正です", code="bad_json", status=502)
    return {"ok": True, "model": meta, "response": raw}


def wizard_draft_from_request(body):
    wid = str((body or {}).get("draft_id") or "")
    if not WIZARD_ID_RE.match(wid):
        raise WizardError("下書きIDが不正です", code="bad_request")
    d = wizard_load(wid)
    if not d:
        raise WizardError("下書きが見つかりません", code="not_found", status=404)
    return d


def _wizard_slot_filled(v):
    """スロットが「入力済み」か。AI 待ちの空プレースホルダ（auto + 空）は入力とみなさない。"""
    if not isinstance(v, dict):
        return bool(v)
    if str(v.get("source") or "") in ("user", "locked"):
        return True
    val = v.get("value")
    return val not in (None, "", [])


def wizard_draft_has_input(d):
    """ユーザーが何か進めた下書きか。

    何も入力していない下書きで「前回の続きから再開しますか？」を出さないための判定。
    （保存せずに閉じただけの下書きは、次に開いたとき黙って使い回す）

    step は「どの画面を見ていたか」の UI 状態で、1/7 を選んだだけで step="idea" に
    なるため、入力の有無には数えない（数えると空の下書きでも毎回聞いてしまう）。
    """
    if not d:
        return False
    if any(_wizard_slot_filled(v) for v in (d.get("slots") or {}).values()):
        return True
    if d.get("questions") or d.get("asked_question_ids") or d.get("asked_slots"):
        return True
    if d.get("compose"):
        return True
    if str(d.get("partner_preset_id") or ""):
        return True
    if d.get("selected_tags") or d.get("custom_ng"):
        return True
    if str(d.get("last_job") or "") and wizard_job_running(d.get("last_job")):
        return True
    return False


def wizard_start(body):
    force = bool((body or {}).get("force_new"))
    base_sid = str((body or {}).get("base_sid") or "")
    dismiss_id = str((body or {}).get("dismiss_id") or "")
    base = None
    if base_sid:
        try:
            base = (load(base_sid, readonly=True) or {}).get("setting")
        except Exception:
            base = None

    # 再開候補の探索と新規作成を同じロック内で行う（2タブ同時オープンで
    # 空の下書きが二重に作られるのを防ぐ）。
    with wizard_lock:
        if not force:
            matches = []
            for wid, d in wizard_ids_sorted():
                if d.get("base_sid", "") != base_sid:
                    continue
                if d.get("status") not in ("interview", "composing", "ready"):
                    continue
                if d.get("status") == "composing" and not wizard_job_running(d.get("last_job")):
                    has = bool((d.get("compose") or {}).get("setting"))
                    d["status"] = "ready" if has else "interview"
                    d["step"] = "preview" if has else "tags"
                    wizard_save(d, bump=False)
                matches.append((wid, d))
            # 内容のある下書きがあれば（新しい順で最初の1件）再開をたずねる
            for wid, d in matches:
                if wizard_draft_has_input(d):
                    return {"ok": True, "resumed": True, "draft": wizard_public(d, True)}
            # 入力が何もない下書きは「再開しますか？」を出さずにそのまま使い回す
            if matches:
                return {"ok": True, "resumed": False, "draft": wizard_public(matches[0][1], True)}

        d = wizard_new_draft(base, wizard_model_from_app())
        d["base_sid"] = base_sid
        d["base_title"] = str((base or {}).get("title") or "")
        d["inherit_base"] = bool(base)
        wizard_save(d)
        if dismiss_id and WIZARD_ID_RE.match(dismiss_id):
            old = wizard_load(dismiss_id)
            # 「新規作成」を選んだ下書きは再び尋ねないように退避する（削除はしない）
            if old and old.get("status") != "created" and not wizard_job_running(old.get("last_job")):
                old["status"] = "dismissed"
                wizard_save(old, bump=False)
        wizard_sweep()
        return {"ok": True, "resumed": False, "draft": wizard_public(d, True)}


@contextmanager
def wizard_write(body, bump=True, allow_running=False):
    """rev 確認・状態確認・保存を同じロック内で行う（read-modify-write 競合対策）。"""
    with wizard_lock:
        d = wizard_draft_from_request(body)
        if _as_int(body.get("rev"), -1) != _as_int(d.get("rev"), 0):
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(d, True)
            raise e
        if d.get("status") == "created":
            raise WizardError("作成済みの下書きは変更できません", code="created", status=409)
        if not allow_running and wizard_job_running(d.get("last_job")):
            raise WizardError("生成中です。完了してから操作してください", code="wizard_busy", status=409)
        yield d
        wizard_save(d, bump=bump)


def wizard_route(h, path, body, method):
    try:
        path_only, _, qs = path.partition("?")
        if method == "GET":
            if path_only == "/api/wizard/meta":
                app = app_settings()
                return h.send_json({
                    "ok": True,
                    "enabled": bool(app.get("wizard_enabled", True)),
                    "configured": bool(str(app.get("wizard_model") or "").strip()) or str(app.get("wizard_provider") or "") == "antigravity",
                    "current": wizard_model_from_app(),
                    "max_questions": WIZARD_MAX_QUESTIONS,
                    "max_rounds": WIZARD_MAX_ROUNDS,
                    "tags": wizard_tags(),
                    "defaults": {
                        "project": str(app.get("project") or ""),
                        "user_name": "あなた",
                    },
                })
            if path_only == "/api/wizard/models":
                q = parse_qs(qs or "")
                provider = (q.get("provider") or ["google"])[0]
                return h.send_json(wizard_models_payload(provider))
            if path_only == "/api/wizard/list":
                rows = []
                for wid, d in wizard_ids_sorted():
                    rows.append({
                        "id": wid,
                        "title": wizard_public(d).get("title_hint"),
                        "status": d.get("status"),
                        "updated": d.get("updated"),
                        "created_sid": d.get("created_sid") or "",
                    })
                return h.send_json({"ok": True, "drafts": rows})
            m = re.match(r"^/api/wizard/job/(wj_[0-9a-f]{10})$", path_only)
            if m:
                job = wizard_jobs.get(m.group(1))
                if not job:
                    return h.send_json({"ok": False, "error": "ジョブが見つかりません", "code": "not_found"}, 404)
                out = {k: v for k, v in job.items() if k != "cancel"}
                return h.send_json(out)
            m = re.match(r"^/api/wizard/(wiz_[0-9a-f]{10})$", path_only)
            if m:
                d = wizard_load(m.group(1))
                if not d:
                    return h.send_json({"ok": False, "error": "下書きが見つかりません", "code": "not_found"}, 404)
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            return h.send_json({"ok": False, "error": "not found"}, 404)

        if method == "POST":
            if path_only == "/api/wizard/start":
                return h.send_json(wizard_start(body))
            if path_only == "/api/wizard/base":
                with wizard_write(body) as d:
                    wizard_set_base(d, body.get("base_sid"), bool(body.get("inherit")))
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/answer":
                with wizard_write(body) as d:
                    wizard_apply_answer(d, body)
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/slots":
                with wizard_write(body) as d:
                    wizard_apply_slots(d, body.get("slots") or {})
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/tags":
                with wizard_write(body) as d:
                    wizard_apply_tags(d, body)
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/model":
                with wizard_write(body) as d:
                    provider = str(body.get("provider") or "").strip().lower()
                    if provider not in ("google", "vertex", "antigravity", "none"):
                        raise WizardError("プロバイダーが不正です", code="bad_request")
                    d["model"] = {
                        "provider": provider,
                        "model": str(body.get("model") or "").strip(),
                        "thinking_level": str(body.get("thinking_level") or "").strip().lower(),
                        "ai_enabled": bool(body.get("ai_enabled", provider != "none")),
                    }
                    d["compose_stale"] = True
                    # 選択したモデルの configured 表示を次の一覧取得で更新する
                    wizard_model_cache.clear()
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/step":
                with wizard_write(body, bump=False, allow_running=True) as d:
                    step = str(body.get("step") or "").strip()
                    if step in ("base", "idea", "partner", "tags", "questions", "preview", "creating"):
                        d["step"] = step
                    # step はUI状態。ジョブ実行中の画面遷移でも 409 wizard_busy にせず、rev も上げない。
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/preview":
                with wizard_write(body) as d:
                    wizard_apply_preview(d, body.get("setting") or {})
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/questions":
                d = wizard_draft_from_request(body)
                job, pub = wizard_start_job(d, "questions", body.get("rev"))
                return h.send_json({"ok": True, "job_id": job["job_id"], "draft": pub})
            if path_only == "/api/wizard/compose":
                d = wizard_draft_from_request(body)
                job, pub = wizard_start_job(d, "compose", body.get("rev"))
                return h.send_json({"ok": True, "job_id": job["job_id"], "draft": pub})
            if path_only == "/api/wizard/job_cancel":
                jid = str(body.get("job_id") or "")
                wid = str(body.get("draft_id") or "")
                with wizard_lock:
                    job = wizard_jobs.get(jid)
                    if job:
                        job["cancel"] = True
                        if job.get("status") in ("queued", "running"):
                            job["status"] = "canceled"
                    # draft_id が無い旧クライアント: 従来の応答だけ返す（rev を動かさない）
                    if not wid:
                        return h.send_json({"ok": bool(job)})
                    if not WIZARD_ID_RE.match(wid):
                        raise ValueError("bad wizard id")
                    jd = wizard_load(wid)
                    if jd and jd.get("last_job") == jid:
                        jd["last_job"] = ""
                        if jd.get("status") == "composing":
                            has = bool((jd.get("compose") or {}).get("setting"))
                            jd["status"] = "ready" if has else "interview"
                            jd["step"] = "preview" if has else "tags"
                        wizard_save(jd)            # rev を進め、走行中ジョブの書き戻しを弾く
                    pub = wizard_public(jd, True) if jd else None
                return h.send_json({"ok": bool(jd), "canceled": bool(job), "draft": pub})
            if path_only == "/api/wizard/create":
                d = wizard_draft_from_request(body)
                # rev の検証は wizard_create 内（同じロック内）で行う。
                # 作成済みなら rev が古くても同じ sid を返し、二重作成しない。
                return h.send_json(wizard_create(d, body.get("overrides") or {},
                                                 bool(body.get("use_template"))))
            if path_only == "/api/wizard/manual":
                d = wizard_draft_from_request(body)
                return h.send_json(wizard_manual_create(d, body))
            if path_only == "/api/wizard/delete":
                d = wizard_draft_from_request(body)
                if d.get("last_job"):
                    job = wizard_jobs.get(d["last_job"])
                    if job:
                        job["cancel"] = True
                        if job.get("status") in ("queued", "running"):
                            job["status"] = "canceled"
                try:
                    os.remove(wizard_path(d["id"]))
                except OSError:
                    pass
                return h.send_json({"ok": True})
            if path_only == "/api/wizard/test":
                return h.send_json(wizard_test_model(body))
            return h.send_json({"ok": False, "error": "not found"}, 404)
        return h.send_json({"ok": False, "error": "method not allowed"}, 405)
    except WizardError as e:
        payload = {"ok": False, "error": str(e), "code": e.code, "retryable": e.retryable}
        if getattr(e, "draft", None):
            payload["draft"] = e.draft
        return h.send_json(payload, e.status)
    except ValueError as e:
        return h.send_json({"ok": False, "error": str(e), "code": "bad_request"}, 400)
    except Exception as e:
        return h.send_json({"ok": False, "error": str(e), "code": "wizard_error", "retryable": True}, 500)


