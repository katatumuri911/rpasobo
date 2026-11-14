"""選択肢プレースホルダ置換 & 婉曲表現（decensor）復元（server.py から切り出し）"""
import collections
import copy
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
CHOICES_RULES_SRC = os.path.join(ROOT, "rules", "choices-filter-avoidance.md")

# DATA はテストが server.DATA を一時ディレクトリへ差し替えるため、
# 値のコピーではなく「呼び出し時に最新値を返す関数」を server.py から注入できるようにする。
_data_ref = None


def bind_paths(data=None):
    global _data_ref
    if data is not None:
        _data_ref = data


def data_dir():
    return _data_ref() if _data_ref else DATA


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- 選択肢スロット（normal / erotic）
CHOICE_SLOT_FIELDS = {
    "normal": ("choices", "choices_updated_ts"),
    "erotic": ("choices_erotic", "choices_erotic_updated_ts"),
}


CHOICE_CDATA_KEYS = {
    "normal": ("choices", "updated_ts"),
    "erotic": ("erotic", "erotic_updated_ts"),
}


_PLACEHOLDER_CACHE = {"mtime": None, "map": {}}


_FALLBACK_PLACEHOLDERS = {
    "__JP_PN__": "ペニス", "__JP_NB__": "肉棒", "__JP_IK__": "陰茎", "__JP_GK__": "亀頭",
    "__JP_PS__": "オマンコ", "__JP_MK__": "マンコ", "__JP_HB__": "秘部", "__JP_CS__": "膣",
    "__JP_IN__": "陰唇", "__JP_CL__": "クリトリス", "__JP_AN__": "アナル", "__JP_KM__": "肛門",
    "__JP_FL__": "フェラ", "__JP_ND__": "中出し", "__JP_SJ__": "射精", "__JP_SK__": "性交",
    "__JP_IR__": "イラマチオ", "__JP_KN__": "クンニ", "__JP_OB__": "オナホ",
    "__JP_BL__": "調教", "__JP_SP__": "スパンキング", "__JP_BD__": "拘束", "__JP_RO__": "蝋燭",
    "__JP_TY__": "尿道", "__JP_UR__": "放尿", "__JP_SC__": "脱糞", "__JP_EN__": "異物",
    "__JP_LO__": "愛液", "__JP_SZ__": "精液", "__JP_NM__": "濡れ", "__JP_AK__": "赤面", "__JP_ZO__": "絶頂",
}


def choices_placeholder_map():
    """rules/choices-filter-avoidance.md の `__XX__`=語 を対応表にする。フォールバック辞書を基底にしてマージ"""
    try:
        mt = os.stat(CHOICES_RULES_SRC).st_mtime
    except OSError:
        return dict(_FALLBACK_PLACEHOLDERS)
    if _PLACEHOLDER_CACHE["mtime"] == mt:
        return _PLACEHOLDER_CACHE["map"]
    mp = dict(_FALLBACK_PLACEHOLDERS)
    try:
        with open(CHOICES_RULES_SRC, encoding="utf-8-sig") as f:
            for ph, w in re.findall(r"`?(__[A-Z0-9_]+__)`?\s*=\s*([^,、`\s]+)", f.read()):
                w = w.strip("「」\"'")
                if w:
                    mp[ph] = w
    except OSError:
        pass
    _PLACEHOLDER_CACHE.update(mtime=mt, map=mp)
    return mp


_SUSPECT_EXCLUDE = {"赤面", "濡れ"}


def choices_explicit_words():
    """「差し替え疑い」判定用の露骨語。§2-①（性器・行為）だけを使う（若年連想語、日常語の赤面・濡れは除外）"""
    txt = ""
    try:
        with open(CHOICES_RULES_SRC, encoding="utf-8-sig") as f:
            txt = f.read()
    except OSError:
        return [w for w in _FALLBACK_PLACEHOLDERS.values() if w not in _SUSPECT_EXCLUDE]
    sec = txt.split("### ①", 1)[-1].split("### ②", 1)[0] if "### ①" in txt else txt
    words = [w.strip("「」\"'") for _, w in re.findall(r"`?(__[A-Z0-9_]+__)`?\s*=\s*([^,、`\s]+)", sec)]
    words = [w for w in words if w not in _SUSPECT_EXCLUDE]
    return words or [w for w in _FALLBACK_PLACEHOLDERS.values() if w not in _SUSPECT_EXCLUDE]


def apply_choice_placeholders(choices, return_replaced=False):
    """AIが置換し忘れたプレースホルダーをサーバー側で埋める。
    部分一致誤爆を防ぐため文字長降順で置換。戻り値 (choices, 残った未知プレースホルダー数[, 置換数])"""
    mp = choices_placeholder_map()
    sorted_phs = sorted(mp.keys(), key=len, reverse=True)
    out, left, replaced = [], 0, 0
    for c in choices or []:
        if not isinstance(c, dict):
            continue
        item = dict(c)
        label = str(item.get("label") or "")
        text = str(item.get("text") or "")
        for ph in sorted_phs:
            w = mp[ph]
            if ph in label:
                replaced += label.count(ph)
                label = label.replace(ph, w)
            if ph in text:
                replaced += text.count(ph)
                text = text.replace(ph, w)
        left += len(re.findall(r"__[A-Z0-9_]+__", label + " " + text))
        item["label"] = label
        item["text"] = text
        out.append(item)
    if return_replaced:
        return out, left, replaced
    return out, left


# ---------------------------------------------------------------- 婉曲表現 → 直接表現（decensor）
# フィルタを避けてAIが書いた婉曲語（「秘所」「後ろの穴」など）を、受信時に辞書で直接語へ戻す。
# 辞書は data/decensor.json だけが正（全チャンネル共通・手で編集してもよい）。無ければ変換しない。
# 誤爆を避けるため「長い語優先」「1パス置換」だけを守る（「穴」「そこ」など1文字・一般語は入れない）。
_DECENSOR_FILENAME = "decensor.json"


# 画面の「初期セットに戻す」専用のスナップショット。変換には使わない（辞書は data/decensor.json だけ）。
_DECENSOR_SEED_WORDS = {
    "後ろの穴": "ケツマンコ",
    "お尻の穴": "ケツマンコ",
    "後孔": "アナル",
    "不浄の穴": "アナル",
    "肛門": "アナル",
    "前の穴": "おまんこ",
    "前穴": "おまんこ",
    "蜜壺": "肉壺",
    "蜜穴": "おまんこ",
    "秘所": "おまんこ",
    "秘部": "おまんこ",
    "胸の先": "乳首",
    "膣": "ナカ",
    "胸の膨らみ": "おっぱい",
    "双乳": "おっぱい",
    "乳房": "おっぱい",
    "臀部": "ケツ",
    "尻肉": "尻たぶ",
    "肉棒": "おちんぽ",
    "剛直": "おちんぽ",
    "怒張": "おちんぽ",
    "陰茎": "おちんぽ",
    "精液": "ザーメン",
    "白濁液": "ザーメン",
    "突起（クリトリス）": "クリトリス",
    "突起(クリトリス)": "クリトリス",
    "胸の先端の突起": "乳首",
    "突起洗濯バサミ": "乳首クリップ",
    "突起吸引器": "乳首吸引器",
    "突起ピンチ": "乳首ピンチ",
    "突起クリップ": "乳首クリップ",
    "突起ピアス": "乳首ピアス",
    "突起リング": "乳首リング",
    "突起ローター": "乳首ローター",
    "突起吸引": "乳首吸引",
    "乳首の先端の突起": "乳首",
    "クリトリスの突起": "クリトリス",
    "乳首の突起": "乳首",
    "突起した乳首": "勃起した乳首",
    "突起した先端": "勃起した先端",
    "突起した": "勃起した",
    "突起して": "勃起して",
    "突起する": "勃起する",
    "突起し": "勃起し",
    "突起物": "乳首",
    "突起部分": "乳首",
    "胸の突起": "乳首",
    "先端の突起": "乳首",
    "外側の突起": "クリトリス",
    "奥と突起": "奥とクリトリス",
    "割れ目の突起": "クリトリス",
    "前の突起": "クリトリス",
    "クリの突起": "クリトリス",
    "花芯の突起": "クリトリス",
    "芯の突起": "クリトリス",
    "突起": "乳首",
    "胸の頂点": "乳首",
    "胸の先っぽ": "乳首",
    "股の頂点": "クリトリス",
    "割れ目の頂点": "クリトリス",
    "花芯": "クリトリス",
    "肉芽": "クリトリス",
}


_DECENSOR_CUE_WINDOW = 16


_DECENSOR_CACHE = {"key": None, "words": {}, "compiled": None}


def decensor_path():
    return os.path.join(data_dir(), _DECENSOR_FILENAME)


def _split_context_key(key):
    """'突起|股,割れ目' -> ('突起', ['股', '割れ目']) / 通常キーは (key, [])"""
    if not isinstance(key, str):
        return "", []
    key = key.replace("｜", "|")
    if "|" not in key:
        return key.strip(), []
    word, cues_str = key.split("|", 1)
    word = word.strip()
    cues = []
    for c in re.split(r"[,、|]", cues_str):
        c = c.strip()
        if c and c not in cues:
            cues.append(c)
    return word, cues


def _decensor_words_from(raw):
    """decensor.json の中身 → 検証済みの対応表。形が違えば None"""
    if not isinstance(raw, dict) or not isinstance(raw.get("words"), dict):
        return None
    words = {}
    for k, v in raw["words"].items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k, v = k.strip(), v.strip()
        if not k or not v or k == v or "__" in k:
            continue
        if "|" in k or "｜" in k:
            word, cues = _split_context_key(k)
            if not word or not cues or word == v:
                continue
            norm_k = f"{word}|{','.join(cues)}"
        else:
            norm_k = k
        words[norm_k] = v[:40]
    return words


def decensor_map():
    """{婉曲語: 直接語}。data/decensor.json だけを見る（無ければ空・辞書はアプリ全体の設定で編集）"""
    p = decensor_path()
    try:
        st = os.stat(p)
        key = (st.st_mtime, st.st_size)
    except OSError:
        key = "missing"
    if _DECENSOR_CACHE["key"] == key:
        return _DECENSOR_CACHE["words"]
    if key == "missing":
        words = {}
    else:
        raw = read_json(p, None)
        words = _decensor_words_from(raw)
        if words is None:  # 壊れていたら空で続行（次に保存し直せば直る）
            print(f"[decensor] 読めない辞書: {p}（変換せずに続行）")
            words = {}
        elif raw.get("enabled") is False:
            words = {}
    _DECENSOR_CACHE.update(key=key, words=words, compiled=None)
    return words


def _decensor_compiled():
    """decensor_map() から (plain, context, pat, keys) を作る（キャッシュ可）"""
    mp = decensor_map()
    cached = _DECENSOR_CACHE.get("compiled")
    if cached is not None and _DECENSOR_CACHE.get("words") is mp and _DECENSOR_CACHE.get("key") is not None:
        return cached
    plain, context = {}, {}
    for idx, (k, v) in enumerate(mp.items()):
        word, cues = _split_context_key(k)
        if not word:
            continue
        if cues:
            context.setdefault(word, []).append((cues, v, idx))
        else:
            plain.setdefault(word, v)
    keys = sorted(set(plain) | set(context), key=lambda x: (-len(x), x))
    pat = re.compile("|".join(re.escape(k) for k in keys)) if keys else None
    compiled = (plain, context, pat, keys)
    _DECENSOR_CACHE["compiled"] = compiled
    return compiled


def decensor_text(words):
    return "\n".join(f"{k}={v}" for k, v in (words or {}).items())


def parse_decensor_text(text):
    """テキストエリア（1行1組「婉曲=直接」または「語|手がかり1,手がかり2=直接」）→ (対応表, 警告)。
    __XX__ は捨て、語本体が1文字の語は警告して受け入れる"""
    words, warnings = {}, []
    for i, line in enumerate(str(text or "").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("＃"):
            continue
        parts = re.split(r"\s*[=＝]\s*", s, maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            warnings.append(f"{i}行目: 「婉曲=直接」の形ではありません: {s[:20]}")
            continue
        frm, to = parts[0].strip(), parts[1].strip()
        if "__" in frm:
            warnings.append(f"{i}行目: プレースホルダー（__XX__）は入れられません: {frm}")
            continue
        if "|" in frm or "｜" in frm:
            word, cues = _split_context_key(frm)
            if not word:
                warnings.append(f"{i}行目: 語本体が空です: {frm}")
                continue
            if not cues:
                warnings.append(f"{i}行目: 手がかりが空です: {frm}")
                continue
            if len(word) < 2:
                warnings.append(f"{i}行目: 1文字の語は誤爆しやすいので注意: {word}")
            if word == to:
                continue
            norm_frm = f"{word}|{','.join(cues)}"
        else:
            if len(frm) < 2:
                warnings.append(f"{i}行目: 1文字の語は誤爆しやすいので注意: {frm}")
            if frm == to:
                continue
            norm_frm = frm
        words[norm_frm] = to
    target_words = sorted({_split_context_key(k)[0] for k in words}, key=len, reverse=True)
    for frm, to in words.items():
        frm_word, _ = _split_context_key(frm)
        inner = [w for w in target_words if (w != frm_word or w != to) and w in to]
        if inner:
            warnings.append(f"「{frm}→{to}」は「{inner[0]}」を含むため、保存し直して再適用すると連鎖します")
    return words, warnings


def _decensor_apply(text, compiled=None):
    """文字列に辞書を1回だけ適用。戻り値 (text, [{"from","to","count"}])。
    compiled を渡せば _decensor_compiled() / os.stat の再呼び出しを省略可能。"""
    if compiled is None:
        compiled = _decensor_compiled()
    plain, context, pat, _ = compiled
    if not pat or not isinstance(text, str) or not text:
        return text, []
    hits = collections.Counter()

    def choose(word, s, start, end):
        best = None
        for cues, to, order in context.get(word, []):
            for cue in cues:
                for m_cue in re.finditer(re.escape(cue), s):
                    cue_start, cue_end = m_cue.start(), m_cue.end()
                    if max(start, cue_start) < min(end, cue_end):
                        continue
                    gap = (start - cue_end) if start >= cue_end else (cue_start - end)
                    if gap <= _DECENSOR_CUE_WINDOW:
                        cand = (gap, order, to)
                        if best is None or cand < best:
                            best = cand
        if best is not None:
            return best[2]
        return plain.get(word)

    def rep(m):
        word = m.group(0)
        to = choose(word, m.string, m.start(), m.end()) if word in context else plain.get(word)
        if not to:
            return word
        hits[(word, to)] += 1
        return to

    return pat.sub(rep, text), [{"from": k, "to": v, "count": n} for (k, v), n in hits.items()]


def apply_choice_decensor(choices):
    compiled = _decensor_compiled()
    plain, context, pat, _ = compiled
    if not pat:
        return choices, []          # 現行と同じ（辞書OFF時はコピーもしない）
    total = collections.Counter()
    out = []
    for c in choices or []:
        if not isinstance(c, dict):
            continue
        item = dict(c)
        for f in ("label", "text"):
            v = item.get(f)
            if isinstance(v, str) and v:
                item[f], applied = _decensor_apply(v, compiled=compiled)
                for a in applied:
                    total[(a["from"], a["to"])] += a["count"]
        out.append(item)
    return out, [{"from": k, "to": v, "count": n} for (k, v), n in total.items()]


def finalize_choices(choices, st=None):
    """プレースホルダー埋め → 婉曲語の復元をまとめて行う。戻り値 (choices, info)
    info = {"left", "replaced", "applied", "before"}（before は復元前の choices。復元しなければ None）"""
    filled, left, replaced = apply_choice_placeholders(choices, return_replaced=True)
    enabled = True if st is None else bool((st or {}).get("decensor_choices", True))
    before, applied = None, []
    if enabled and filled:
        snapshot = [dict(c) for c in filled]
        filled, applied = apply_choice_decensor(filled)
        if applied:
            before = snapshot
    return filled, {"left": left, "replaced": replaced, "applied": applied, "before": before}


def set_choices_decensor(m, slot, info, target_ts):
    """復元した内容をメッセージに記録する（choices_notice とは別枠。非同期の通知上書きに影響されない）"""
    if info and info.get("applied"):
        m["choices_decensor"] = {
            "slot": slot, "applied": info["applied"], "ts": now(), "target_ts": target_ts,
        }
    else:
        m.pop("choices_decensor", None)


_decensor_undo = {}   # (sid, mid, kind) -> {"kind", ...}（1回だけ戻せるように取っておく）


_DECENSOR_UNDO_MAX = 20


def remember_text_undo(sid, mid, before, after):
    if not sid or not mid or not isinstance(before, str) or before == after:
        return
    _decensor_undo.pop((sid, mid, "text"), None)
    _decensor_undo[(sid, mid, "text")] = {
        "kind": "text", "before": before, "after": after, "ts": time.time(),
    }
    while len(_decensor_undo) > _DECENSOR_UNDO_MAX:
        _decensor_undo.pop(next(iter(_decensor_undo)), None)


def remember_decensor_undo(sid, mid, slot, info, target_ts):
    if not sid or not mid or not info or not info.get("applied") or not info.get("before"):
        return
    _decensor_undo.pop((sid, mid, "choices"), None)
    _decensor_undo[(sid, mid, "choices")] = {
        "kind": "choices", "slot": slot, "before": copy.deepcopy(info["before"]),
        "target_ts": target_ts, "ts": time.time(),
    }
    while len(_decensor_undo) > _DECENSOR_UNDO_MAX:
        _decensor_undo.pop(next(iter(_decensor_undo)), None)


def choices_explicit_hits(choices):
    """露骨語がいくつ含まれるか（🔞なのに0件なら「差し替え疑い」）。復元後の語も露骨語として数える"""
    words = set(choices_explicit_words()) | set(decensor_map().values())
    body = " ".join(f"{c.get('label') or ''} {c.get('text') or ''}" for c in choices or [])
    return sum(1 for w in words if w and w in body)


def choices_slot_of(rep, msg, filename=""):
    """返信をどちらのスロットに入れるか（4段フォールバック）
    1) 返信JSONの slot  2) メッセージに残した choices_slot  3) choices_type  4) outbox ファイル名"""
    for v in (rep.get("slot"), (msg or {}).get("choices_slot"), (msg or {}).get("choices_type")):
        v = str(v or "").strip().lower()
        if v in ("normal", "erotic"):
            return v
    return "erotic" if "_choices_erotic_" in (filename or "") else "normal"

# server.py 側の `from decensor_engine import *` で再エクスポートする名前
__all__ = [
    'CHOICE_SLOT_FIELDS', 'CHOICE_CDATA_KEYS', '_PLACEHOLDER_CACHE', '_FALLBACK_PLACEHOLDERS',
    'choices_placeholder_map', '_SUSPECT_EXCLUDE', 'choices_explicit_words',
    'apply_choice_placeholders', '_DECENSOR_FILENAME', '_DECENSOR_SEED_WORDS',
    '_DECENSOR_CUE_WINDOW', '_DECENSOR_CACHE', 'decensor_path', '_split_context_key',
    '_decensor_words_from', 'decensor_map', '_decensor_compiled', 'decensor_text',
    'parse_decensor_text', '_decensor_apply', 'apply_choice_decensor', 'finalize_choices',
    'set_choices_decensor', '_decensor_undo', '_DECENSOR_UNDO_MAX', 'remember_text_undo',
    'remember_decensor_undo', 'choices_explicit_hits', 'choices_slot_of',
]
