"""
長期記憶（RAG）システム - memory_store.py
完全ローカル・単一JSONファイルによるキャラクター長期記憶ストレージ＆ハイブリッド検索エンジン
"""
import collections
import copy
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
RAG_DIR = os.path.join(DATA, "rag")
TRASH = os.path.join(DATA, "trash")
MODELS_DIR = os.environ.get("RPCHAT_MODELS", os.path.join(ROOT, "models"))

STOP_WORDS = {
    "ユーザー", "こと", "もの", "ため", "よう", "さん", "ちゃん", "くん",
    "日", "時", "今", "今日", "明日", "行く", "来る", "する", "なる",
    "いる", "ある", "言う", "思う", "見る"
}

PROMISE_KEYWORDS = ("約束", "予定", "誓約", "誓い", "行く約束", "会う約束")
USER_FACT_KEYWORDS = ("好き", "嫌い", "好み", "苦手", "趣味", "タイプ", "誕生日", "実は", "飼って", "秘密", "個人情報")
CHAR_FACT_KEYWORDS = ("実は", "秘密", "トラウマ", "過去", "家族", "体質", "弱点", "本当は", "コンプレックス", "生い立ち")
RELATIONSHIP_KEYWORDS = ("関係", "距離", "好意", "好き", "信頼", "依存", "恋人", "友達", "仲")
CALLING_KEYWORDS = (
    "呼び方", "呼び名", "呼称", "名前で呼", "名前を呼",
    "って呼", "と呼", "とお呼び", "ってお呼び",
    "呼び捨て", "あだ名", "敬語", "タメ口",
    "口調", "一人称", "二人称",
    "さん付け", "ちゃん付け", "くん付け", "様付け",
)
ITEM_KEYWORDS = ("もらった", "あげた", "プレゼント", "合鍵", "指輪", "ネックレス", "首輪", "アナルプラグ", "プラグ", "拘束具", "キスマーク", "傷", "痕", "持ち物", "アイテム")
RULE_KEYWORDS = ("ルール", "規則", "禁止", "門限", "約束", "掟", "校則", "地雷", "タブー", "秘密", "約束事")
EVENT_KEYWORDS = ("事件", "出来事", "雨宿り", "旅行", "告白", "助けた", "救った", "出会った", "記念", "初めて", "お泊まり", "デート")

CATEGORY_LABELS = {
    "calling": "呼称・口調",
    # 旧 rule / taboo / rule_taboo は中身が同じ（守るべき決まり）なので rule に統合した
    "rule": "ルール・禁忌",
    "item": "アイテム・所持品",
    "status": "状態・痕跡",
    "promise": "約束・予定",
    "char_fact": "キャラの秘密",
    "user_fact": "ユーザーの好み",
    "relationship": "関係性の変化",
    "event": "重要出来事"
}

CATEGORY_ALIASES = {
    "calling": "calling",
    "call": "calling",
    "call_name": "calling",
    "呼称": "calling",
    "呼び方": "calling",
    "口調": "calling",
    "rule": "rule",
    "ルール": "rule",
    "校則": "rule",
    "掟": "rule",
    "規則": "rule",
    "taboo": "rule",
    "タブー": "rule",
    "禁忌": "rule",
    "地雷": "rule",
    "禁止": "rule",
    "rule_taboo": "rule",
    "規則・禁忌": "rule",
    "ルール・禁忌": "rule",
    "item": "item",
    "アイテム": "item",
    "所持品": "item",
    "持ち物": "item",
    "プレゼント": "item",
    "status": "status",
    "状態": "status",
    "痕跡": "status",
    # 旧 item_status は「物」と「痕跡」の混成だったので、中身を見て item / status に振り分ける
    "item_status": "item_status",
    "アイテム・痕跡": "item_status",
    "promise": "promise",
    "約束": "promise",
    "予定": "promise",
    "誓約": "promise",
    "char_fact": "char_fact",
    "char": "char_fact",
    "character": "char_fact",
    "secret": "char_fact",
    "キャラ": "char_fact",
    "キャラの秘密": "char_fact",
    "user_fact": "user_fact",
    "user": "user_fact",
    "ユーザー": "user_fact",
    "ユーザーの好み": "user_fact",
    "好み": "user_fact",
    "relationship": "relationship",
    "relation": "relationship",
    "rel": "relationship",
    "関係": "relationship",
    "関係性": "relationship",
    "event": "event",
    "出来事": "event",
    "事件": "event",
}

VALID_CATEGORIES = set(CATEGORY_LABELS.keys())

# --- 想起枠（Tier2）の採用条件 -------------------------------------------------
# 類似想起枠に採用する最低スコア（0.38 → 0.42 → 0.45 → 0.53: 記憶蓄積時の上限張り付きとノイズ記憶を落とす）
TIER2_MIN_SCORE = 0.53          # 0.45 → 0.53（弱い一致を落とし、関連度の高い想起に絞る）
# 同じ内容が並んだ場合に落とすしきい値（0で無効）。Tier1（常時枠）は落とさない
TIER2_DEDUP_SIM = 0.88
# ルール（禁忌・催眠暗示など）は「忘れると設定が破綻する常時前提」だが、全部入れると重いので
# Tier1 に入れるのは最大5件（v1.1 の 3件 → 4件 → 5件）。スコア順で埋める
RULE_TIER1_CAP = 5

# --- rule の「常時前提」判定 -------------------------------------------------
# rule 枠は「今の場面に似ているか」ではなく「恒久的な決まりか」で選ぶ。
# 地の文（その場の反応・情景描写）が rule として自動抽出されると、毎ターン
# 「常時確認・遵守事項」として現在の描写と矛盾する制約が渡り、実際の催眠ルールを
# 4 枠から追い出してしまう（ラピ 19 rule で実測）。
# 「催眠」「暗示」という語だけでは決まりと判定しない（“催眠、ラピのアナルに指が入る。”の
# ような断片や、“刷り込まれた暗示に身体が反応して…”の反応描写まで常時枠に入る）。
# 常識・決まり・誤認などの言い回しを要求する。
# strong = それだけで決まり文と見なせる言い回し
_RULE_PREMISE_STRONG = (
    "常識", "当たり前", "誤認", "と認識して", "決まり", "決まり事",
    "ルール", "規則", "校則", "門限", "掟", "タブー", "禁忌", "禁止", "地雷",
    "約束事", "催眠の暗示", "暗示により", "催眠により", "催眠ウイルス",
    "任務として", "当然の任務", "ようになる", "ことになった", "こととする",
)
# weak = 情景描写にも出るので、weak veto（〜てしまう等）があるときは決まりと見なさない
_RULE_PREMISE_WEAK = ("義務", "服従", "従順")
_RULE_STANDING_PATTERNS = _RULE_PREMISE_STRONG + _RULE_PREMISE_WEAK
# 情景描写の目印。strong は決まり文でも常時枠に載せない（“〜のに”“止められない”等）
_RULE_TRANSIENT_STRONG = (
    "のに", "止められ", "抗え", "逆らえ", "零れ", "漏れ", "火照", "疼", "溢れ",
    "溺れ", "喘", "声を上げ", "支配され",
)
# weak は決まりの言い回しが無いときだけ効かす（“〜てしまう”“〜ながら”は決まり文にも出る）
_RULE_TRANSIENT_WEAK = ("てしま", "ながら", "震え")
# 一過性の反応・情景描写の目印（抽出時に rule を作らないための総合判定）
_RULE_TRANSIENT_PATTERNS = _RULE_TRANSIENT_STRONG + _RULE_TRANSIENT_WEAK


def looks_transient_rule(content):
    """その場限りの反応・情景描写か（常時前提として扱ってはいけない文）"""
    t = str(content or "")
    return any(p in t for p in _RULE_TRANSIENT_PATTERNS)


def should_skip_rule_extraction(text, from_user=False):
    """抽出時に rule を作らない文か。

    strong は決まり文でも不可（“〜のに”“止められない”は地の文）。weak は決まりの
    言い回しが無いときだけ効かす（“〜ことが常識で…言ってしまう”のような本物の決まりを
    落とさないため）。ユーザーの発言は命令の意図を尊重して weak では落とさない。
    """
    t = str(text or "")
    if any(p in t for p in _RULE_TRANSIENT_STRONG):
        return True
    if any(p in t for p in _RULE_PREMISE_STRONG):
        return False
    if from_user:
        return False
    return any(p in t for p in _RULE_TRANSIENT_WEAK)


# --- relationship（関係性の変化）の誤爆防止 -----------------------------------
# 「まるで恋人みたい」「恋人のふり」「恋人ごっこ」のような比喩・演技・仮定や、
# 「大好き」「特別」といったその場の感情の叫びは“関係の変化”ではない。
# 8カテゴリの中で relationship は最も誤爆が多く、常時枠と想起枠を感情ノイズで埋める。
# そのため「確定した変化の言い回し」が文に無ければ relationship を作らない。
_REL_SIMILE = (
    "みたい", "かのよう", "ように", "ような気", "ふり", "ごっこ",
    "演技", "妄想", "想像", "錯覚", "ロールプレイ", "たとえ", "例え", "もし",
    "仮に", "仮定", "つもり", "気分", "見立て", "真似", "役を",
)
# 願望・推測・意見（「付き合いたい」「付き合うべき」は確定事実ではない）
_REL_WISH = (
    "したい", "したかった", "しようと", "しようか", "してみたい", "たい", "かもしれ",
    "だろう", "でしょう", "思う", "思っ", "考え", "願", "祈", "憧れ", "いつか",
    "だったら", "ならば", "べき", "はず",
)
# 否定（「付き合っていない」「恋人じゃない」「関係が変わらない」）
_REL_NEG = ("ない", "なかっ", "なく", "ではない", "じゃない", "別れず", "変わらず")
# 申し出・依頼・質問（まだ確定していない）
_REL_PROPOSAL = (
    "付き合おう", "付き合いましょ", "付き合ってください", "付き合ってほし", "付き合って欲し",
    "結婚しよう", "結婚してくれ", "告白しよう", "告白しよっ", "プロポーズしよう",
)
# 関係が実際に動いた確定事実の言い回し
_REL_CHANGE = (
    "付き合", "交際", "恋人同士", "恋人にな", "恋人と言", "両思い", "両想い",
    "告白", "プロポーズ", "婚約", "結婚", "入籍", "別れ", "破局", "振られ",
    "振った", "仲直り", "和解", "復縁", "よりを戻", "関係が変わ", "関係を変",
    "関係が深ま", "絆が", "特別な関係", "信頼を深", "距離が縮", "距離を縮", "距離が近",
    "親密にな", "彼氏にな", "彼女にな",
)


def should_skip_relationship_extraction(text, from_user=False):
    """抽出時に relationship を作らない文か。

    記録してよいのは「関係が実際に変わった確定事実」だけ。比喩（〜みたい／〜のふり／
    ごっこ）・仮定・願望・否定・その場の感情（大好き／特別）だけの文は作らない。
    from_user は将来の拡張用（今はユーザー発言も同じ基準で判定する）。
    """
    t = str(text or "").strip()
    if not t:
        return True
    if t[-1:] in ("？", "?"):
        return True
    if any(p in t for p in _REL_SIMILE):
        return True
    if any(p in t for p in _REL_WISH):
        return True
    if any(p in t for p in _REL_PROPOSAL):
        return True
    if any(p in t for p in _REL_NEG):
        return True
    return not any(p in t for p in _REL_CHANGE)


# 関係記憶は「要約された事実文」だけを許可する。元のセリフ・地の文をそのまま写した
# 記憶（比喩や情景描写のコピー）は、語彙リストにない言い回しでもここで弾ける。
_REL_COPY_MIN_BLOCK = 24   # これ以上の連続一致は引用とみなす
_REL_COPY_RATIO = 0.75     # 記憶本文のうち一致している割合
_COPY_STRIP_RE = re.compile(r"[\s「」『』“”\"'、。．,.!！?？…‥・]+")


def is_narration_copy(content, source_text):
    """記憶が元のセリフ・地の文のコピー（引用）かどうか。"""
    c = _COPY_STRIP_RE.sub("", str(content or ""))
    s = _COPY_STRIP_RE.sub("", str(source_text or ""))
    if not c or not s or len(c) < _REL_COPY_MIN_BLOCK:
        return False
    block = difflib.SequenceMatcher(None, c, s, autojunk=False).find_longest_match(0, len(c), 0, len(s))
    return block.size >= max(_REL_COPY_MIN_BLOCK, int(len(c) * _REL_COPY_RATIO))


def drop_narrated_relationship(items, narration_text):
    """relationship 記憶から「地の文のコピー」を除く（他のカテゴリはそのまま）。"""
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            content = str(it.get("content") or it.get("text") or "").strip()
            if resolve_category(it.get("category") or it.get("type") or "", content) == "relationship":
                if is_narration_copy(content, narration_text):
                    continue
        out.append(it)
    return out


# --- 5段階優先度（ピン留め＋乗算Boost）定義 -----------------------------------
PRIORITY_LEVELS = (
    {
        "key": "pinned",
        "label": "ピン留め",
        "short": "📌 ピン留め",
        "mult": 1.00,
        "min_score": 0.00,
        "pinned": True,
        "desc": "スコアに関係なく常時前提枠（Tier 1・最大5枠）に最優先で固定"
    },
    {
        "key": "highest",
        "label": "最優先",
        "short": "▲▲ 最優先",
        "mult": 1.25,
        "min_score": TIER2_MIN_SCORE,
        "pinned": False,
        "desc": "関連時にスコア×1.25。単語の直接一致がなくても文脈が近ければ優先想起"
    },
    {
        "key": "high",
        "label": "優先",
        "short": "▲ 優先",
        "mult": 1.12,
        "min_score": TIER2_MIN_SCORE,
        "pinned": False,
        "desc": "関連時にスコア×1.12。惜しくも足切りで落ちやすい記憶を救済"
    },
    {
        "key": "default",
        "label": "標準",
        "short": "― 標準",
        "mult": 1.00,
        "min_score": TIER2_MIN_SCORE,
        "pinned": False,
        "desc": "通常のハイブリッド検索スコア（×1.00）"
    },
    {
        "key": "low",
        "label": "抑制",
        "short": "▼ 抑制",
        "mult": 0.82,
        "min_score": 0.60,
        "pinned": False,
        "desc": "スコア×0.82＆足切り厳格化(0.60)。明確に話題が出た時だけ想起"
    },
)

PRIORITY_BY_KEY = {p["key"]: p for p in PRIORITY_LEVELS}

# 日本語表示名・旧キー・数値文字列からの正規化マッピング
PRIORITY_ALIASES = {
    "pinned": "pinned", "pin": "pinned", "固定": "pinned", "ピン留め": "pinned", "ピン": "pinned", "常時": "pinned",
    "highest": "highest", "very_high": "highest", "top": "highest", "最優先": "highest", "超高": "highest", "+2": "highest", "2": "highest",
    "high": "high", "slightly_high": "high", "優先": "high", "少し優先": "high", "やや優先": "high", "高": "high", "少し高": "high", "+1": "high", "1": "high",
    "default": "default", "normal": "default", "標準": "default", "デフォルト": "default", "普通": "default", "0": "default", "": "default",
    "low": "low", "slightly_low": "low", "lowest": "low", "mute": "low", "抑制": "low", "低": "low", "少し低": "low", "超低": "low", "-1": "low", "-2": "low",
}

# 無関係な記憶がカテゴリ下駄＋乗算で浮上するのを防ぐ最低関連度ライン
MIN_VEC_FOR_POSITIVE_BOOST = 0.35

# 旧仕様互換用定数
MEMORY_BOOST_MAX = 0.5


def resolve_priority_key(priority=None, boost=None):
    """priority（キー/日本語名）または旧 boost（数値）から正規キー（pinned/highest/high/default/low）を返す。"""
    if priority is not None and str(priority).strip() != "":
        raw = str(priority).strip()
        norm = PRIORITY_ALIASES.get(raw.lower()) or PRIORITY_ALIASES.get(raw)
        if norm in PRIORITY_BY_KEY:
            return norm
        try:
            boost = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"invalid priority: {priority}")

    if boost is not None and str(boost).strip() != "":
        try:
            b = float(boost)
        except (TypeError, ValueError):
            raise ValueError(f"invalid boost: {boost}")
        if b >= 0.08:
            return "pinned"
        if b >= 0.03:
            return "highest"
        if b > 1e-6:
            return "high"
        if b < -1e-6:
            return "low"
        return "default"

    return "default"


def memory_priority_key(mem):
    """記憶辞書から正規化された priority キーを取得する。"""
    if not isinstance(mem, dict):
        return "default"
    p = mem.get("priority")
    if p in PRIORITY_BY_KEY:
        return p
    try:
        return resolve_priority_key(priority=p, boost=mem.get("boost"))
    except ValueError:
        return "default"


def memory_priority_info(mem):
    """記憶辞書に対応する PRIORITY_LEVELS の定義辞書を返す。"""
    return PRIORITY_BY_KEY.get(memory_priority_key(mem), PRIORITY_BY_KEY["default"])


def is_pinned_memory(mem):
    """ピン留め（常時固定）された記憶か（active なものに限る）。"""
    if not isinstance(mem, dict):
        return False
    if mem.get("status", "active") != "active":
        return False
    return memory_priority_key(mem) == "pinned"


def apply_priority_field(mem, priority=None, boost=None):
    """記憶辞書 mem に正規化した priority を設定する（default 時はキーを削除してスリム化）。"""
    p_key = resolve_priority_key(priority=priority, boost=boost)
    mem.pop("boost", None)  # 旧 boost 数値フィールドは priority へ統合して削除
    if p_key == "default":
        mem.pop("priority", None)
    else:
        mem["priority"] = p_key
    return mem


def memory_boost(mem):
    """旧仕様との後方互換用：priority キーに応じた擬似 boost 値を返す。"""
    p_key = memory_priority_key(mem)
    return {"pinned": 0.10, "highest": 0.05, "high": 0.02, "default": 0.0, "low": -0.05}.get(p_key, 0.0)


def rule_standing_level(content, source="", priority_key="default", is_other=False):
    """保護層（Tier 1）における優先ランクを返す。
    4 = 自セッションのピン留め（最優先保護）
    3 = 自セッションの恒久ルール、または自セッションの「最優先(highest)」「優先(high)」ルール
    2 = 他セッションのピン留め、または他セッションの恒久ルール・「最優先」「優先」ルール
    1 = 手書き（manual）で一過性でないルール（または強一過性語を含むが最優先指定されたルール）
    0 = 常時枠に載せない（「抑制(low)」指定、または地の文の一過性描写）
    """
    if priority_key == "pinned":
        return 2 if is_other else 4
    if priority_key == "low":
        return 0
    t = str(content or "")
    if not t:
        return 0
    if any(p in t for p in _RULE_TRANSIENT_STRONG):
        return 1 if priority_key == "highest" else 0
    if priority_key in ("highest", "high"):
        return 2 if is_other else 3
    weak_veto = any(p in t for p in _RULE_TRANSIENT_WEAK)
    if any(p in t for p in _RULE_PREMISE_STRONG):
        return 2 if is_other else 3
    if any(p in t for p in _RULE_PREMISE_WEAK):
        if weak_veto:
            return 1
        return 2 if is_other else 3
    if weak_veto:
        return 1 if str(source or "").strip() == "manual" else 0
    if str(source or "").strip() == "manual":
        return 1
    return 0


def is_standing_rule(content, source="", priority_key="default", is_other=False):
    """恒久的な決まり（催眠暗示・常識・禁忌など）として rule 枠に載せてよいか"""
    return rule_standing_level(content, source, priority_key=priority_key, is_other=is_other) > 0


MEMORY_SOFT_BUDGET_BYTES = 1800 # 記憶セクションの目標（2000→1800: ラピ等の8000B環境で9400B inline死守のため引き下げ）
RECALL_BUDGET_BYTES      = 600  # 可変層の予算
MEMORY_MIN_RULES         = 2    # フロア（ルール）
MEMORY_MIN_RECALL        = 1    # フロア（想起）

# 常時の下駄。rule は 0.0（No-Op のため）。calling の常時下駄は廃止（Tier 2 移行に伴い他カテゴリと公平化）。
# 既存の `bonus`（クエリにカテゴリ語がある時 +0.15 / if-elif連鎖）とは別枠。
CATEGORY_SCORE_BOOST = {"promise": 0.10, "item": 0.03, "status": 0.02}
# 類似度・キーワード一致に掛けるデバフ。relationship は感情語（好き・大切等）を
# 共有しやすく、情景コピーが混ざるとクエリと異常に似て上位を占めるため軽く減衰させる。
# クエリにカテゴリ語（恋人・付き合い等）がある場合の bonus は別途加算するので、
# 「関係の話をしている時」の想起は従来どおり残る。
CATEGORY_SCORE_MULT = {"relationship": 0.90}

# --- 自動抽出の保存条件 -------------------------------------------------------
# 1ターンで新規に追加する記憶の上限（既存記憶へのマージは数えない）
MAX_NEW_MEMORIES_PER_TURN = 3
# 上限に達したときに残す優先順（小さいほど残す）
MEMORY_PRIORITY = {
    "promise": 0, "rule": 1, "calling": 2,
    "item": 3, "status": 3,
    "event": 4, "relationship": 4,
    "user_fact": 5, "char_fact": 5,
}
# カテゴリ（系統）別の上限。同じ系統の記憶で枠が埋まらないようにする
# event / relationship は 3 だと想起枠（limit=10）より先に埋まってしまうため 5 / 4 に緩和。
# 検索時間は記憶件数に比例するだけで、この上限を上げてもほぼ変わらない（46件で約8ms）。
GROUP_CAP_DEFAULT = 2
GROUP_CAPS = {
    "calling": 2,
    "promise": 2,
    "event": 5,
    "relationship": 4,
    "user_fact": 2,
    "char_fact": 2,
    "rule": 4,
    # item / status は以前 item_status をまとめて 4 件までにしていた。
    # 分けた今は「物 2 件＋痕跡 2 件」までとし、片方で枠を埋めないようにする。
    "item": 2,
    "status": 2,
}

# 一時的な記憶の寿命（ターン数）。これを過ぎたら検索対象から外す（データは消さない）
TRANSIENT_TURNS = {"status": 20, "item": 60}

# 装着物が外れたことの検出に使う動詞（「プレゼントされた」等の贈与文を解除対象にしないためのガード）
ITEM_VERBS = ("装着", "挿入", "付けている", "つけている", "嵌め", "はめ", "咥え", "くわえ", "身につけ")

# 旧 item_status（物と痕跡の混成）を item / status に振り分けるための語
ITEM_OBJECT_WORDS = ("指輪", "ネックレス", "ペンダント", "ブローチ", "アンクレット", "合鍵", "首輪",
                     "プラグ", "拘束具", "お守り", "ピアス", "ブレスレット", "バイブ", "装具", "玩具", "プレゼント")


def resolve_category(raw_cat, content=""):
    """カテゴリ名を正規化する。旧 item_status は中身で item / status に振り分ける。"""
    c = str(raw_cat or "").strip().lower()
    c = CATEGORY_ALIASES.get(c, c)
    if c == "item_status":
        text = content or ""
        return "item" if any(w in text for w in ITEM_OBJECT_WORDS) else "status"
    return c

_store_lock = threading.RLock()
_tokenizer = None
_tokenizer_lock = threading.Lock()
_embedder = None
_embedder_lock = threading.Lock()


def get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        with _tokenizer_lock:
            if _tokenizer is None:
                from janome.tokenizer import Tokenizer
                _tokenizer = Tokenizer()
    return _tokenizer


def get_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from fastembed import TextEmbedding
                os.makedirs(MODELS_DIR, exist_ok=True)
                _embedder = TextEmbedding(
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                    cache_dir=MODELS_DIR
                )
    return _embedder


def compute_hash(content):
    if not isinstance(content, str):
        content = ""
    return hashlib.md5(content.strip().encode("utf-8")).hexdigest()[:8]


def extract_tokens(text):
    if not text or not isinstance(text, str):
        return []
    try:
        tokenizer = get_tokenizer()
        tokens = []
        for token in tokenizer.tokenize(text):
            pos = token.part_of_speech.split(",")
            pos0 = pos[0]
            pos1 = pos[1] if len(pos) > 1 else ""

            word = None
            if pos0 == "名詞":
                if pos1 not in ("非自立", "代名詞", "数"):
                    word = token.surface
            elif pos0 == "動詞" and pos1 == "自立":
                word = token.base_form if token.base_form != "*" else token.surface
            elif pos0 == "形容詞" and pos1 == "自立":
                word = token.base_form if token.base_form != "*" else token.surface

            if word and word not in STOP_WORDS and len(word.strip()) > 0:
                tokens.append(word.strip())
        return list(dict.fromkeys(tokens))
    except Exception as e:
        print(f"[memory_store] extract_tokens error: {e}")
        return []


_COMPACT_JSON_RE = re.compile(r'(?<!\\)"(tokens|embedding)":\s*\[([^\]]*?)\]', flags=re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r',\s*([\]}])')
_memory_cache = {}  # session_id -> {"mtime": float, "size": int, "data": dict}
_deleted_sessions = set()


def mark_session_deleted(session_id):
    """セッション削除時に呼び出し、非同期ワーカーからの遅延書き込みを遮断する"""
    if session_id and isinstance(session_id, str):
        sid = session_id.strip()
        with _store_lock:
            _deleted_sessions.add(sid)
            _memory_cache.pop(sid, None)


def unmark_session_deleted(session_id):
    """同一IDでセッションが復元・再作成された場合に遮断を解除する"""
    if session_id and isinstance(session_id, str):
        with _store_lock:
            _deleted_sessions.discard(session_id.strip())
_query_cache = collections.OrderedDict()  # text -> (tokens, embedding) の LRU キャッシュ
_query_cache_lock = threading.Lock()
_feature_compute_lock = threading.Lock()


def get_query_features(text):
    """クエリの形態素トークンとEmbeddingをLRUキャッシュ付きで取得する（二重推論防止ロック付き）"""
    if not text or not isinstance(text, str):
        return [], []
    text = text.strip()
    if not text:
        return [], []
    with _query_cache_lock:
        cached = _query_cache.get(text)
        if cached is not None:
            _query_cache.move_to_end(text)
            return list(cached[0]), list(cached[1])

    with _feature_compute_lock:
        with _query_cache_lock:
            cached = _query_cache.get(text)
            if cached is not None:
                _query_cache.move_to_end(text)
                return list(cached[0]), list(cached[1])
        tokens = extract_tokens(text)
        vec = get_embedding(text)
        if vec:
            with _query_cache_lock:
                _query_cache[text] = (tokens, vec)
                _query_cache.move_to_end(text)
                while len(_query_cache) > 128:
                    _query_cache.popitem(last=False)
        return list(tokens), list(vec)


def warmup():
    """サーバー起動時にバックグラウンドでJanomeの初回tokenizeとFastEmbedの初回推論を実行しておく"""
    try:
        extract_tokens("初期ウォームアップ")
        get_embedding("初期ウォームアップ")
    except Exception as e:
        print(f"[memory_store] warmup error: {e}")


def get_embeddings_batch(texts):
    """複数テキストをまとめて1回の推論でベクトル化する"""
    if not texts:
        return []
    embedder = get_embedder()
    results = []
    for vec in embedder.embed(texts):
        norm = math.sqrt(sum(float(x) * float(x) for x in vec))
        if norm > 0:
            results.append([round(float(x) / norm, 4) for x in vec])
        else:
            results.append([round(float(x), 4) for x in vec])
    return results


def get_embedding(text):
    if not text or not isinstance(text, str):
        return []
    try:
        res = get_embeddings_batch([text])
        return res[0] if res else []
    except Exception as e:
        print(f"[memory_store] get_embedding error: {e}")
        return []


def cosine_similarity(v1, v2):
    """get_embeddingで正規化済みのため、内積のみで高速にコサイン類似度を算出する"""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    sim = sum(a * b for a, b in zip(v1, v2))
    return max(0.0, min(1.0, float(sim)))


def memory_path(session_id):
    os.makedirs(RAG_DIR, exist_ok=True)
    return os.path.join(RAG_DIR, f"{session_id}_memories.json")


def to_compact_json(data):
    s = json.dumps(data, ensure_ascii=False, indent=2)

    def repl(m):
        key = m.group(1)
        items = [x.strip() for x in m.group(2).split(",") if x.strip()]
        return f'"{key}": [' + ", ".join(items) + "]"

    return _COMPACT_JSON_RE.sub(repl, s)


def load_memories(session_id, character_name=""):
    path = memory_path(session_id)
    with _store_lock:
        try:
            st = os.stat(path)
        except OSError:
            _memory_cache.pop(session_id, None)
            return {
                "session_id": session_id,
                "character_name": character_name,
                "updated_at": time.time(),
                "memories": []
            }

        cached = _memory_cache.get(session_id)
        if cached and cached["mtime"] == st.st_mtime and cached["size"] == st.st_size:
            data = copy.deepcopy(cached["data"])
            if character_name and not data.get("character_name"):
                data["character_name"] = character_name
            return data

        try:
            with open(path, encoding="utf-8") as f:
                raw_text = f.read()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                # Attempt to repair common trailing comma errors from Notepad edits
                cleaned = _TRAILING_COMMA_RE.sub(r'\1', raw_text)
                data = json.loads(cleaned)
            if not isinstance(data, dict):
                raise ValueError("JSON is not an object")
        except Exception as e:
            print(f"[memory_store] error loading {path}: {e}")
            try:
                import shutil
                corrupt_backup = path + f".corrupt_{int(time.time())}"
                shutil.copy2(path, corrupt_backup)
                print(f"[memory_store] backed up corrupted memories to {corrupt_backup}")
            except Exception:
                pass
            return {
                "session_id": session_id,
                "character_name": character_name,
                "updated_at": time.time(),
                "memories": []
            }

        data.setdefault("session_id", session_id)
        if character_name and not data.get("character_name"):
            data["character_name"] = character_name
        data.setdefault("updated_at", time.time())
        data.setdefault("memories", [])

        # Check content_hash for each memory (detect Notepad manual edits)
        needs_resave = False
        valid_memories = []
        for mem in data["memories"]:
            if not isinstance(mem, dict):
                continue
            content = mem.get("content", "")
            if not content:
                continue
            if not mem.get("id"):
                mem["id"] = "mem_" + uuid.uuid4().hex[:8]
                needs_resave = True
            if not mem.get("session_id"):
                mem["session_id"] = session_id
                needs_resave = True
            raw_cat = str(mem.get("category") or "").strip().lower()
            norm_cat = resolve_category(raw_cat, content)
            if norm_cat in VALID_CATEGORIES:
                if mem.get("category") != norm_cat:
                    mem["category"] = norm_cat
                    needs_resave = True
            else:
                mem["category"] = "promise"
                needs_resave = True
            if not mem.get("source"):
                mem["source"] = "manual"
                needs_resave = True
            now_ts = time.time()
            if not mem.get("created_at"):
                mem["created_at"] = now_ts
                needs_resave = True
            current_hash = compute_hash(content)
            if mem.get("content_hash") != current_hash or not mem.get("embedding") or not mem.get("tokens"):
                mem["content_hash"] = current_hash
                mem["tokens"] = extract_tokens(content)
                mem["embedding"] = get_embedding(content)
                mem["updated_at"] = now_ts
                needs_resave = True
            if not mem.get("status"):
                mem["status"] = "active"
                needs_resave = True
            if not isinstance(mem.get("history"), list):
                mem["history"] = []
                needs_resave = True
            if not mem.get("elapsed_time"):
                try:
                    sess_path = os.path.join(DATA, "sessions", f"{session_id}.json")
                    if os.path.exists(sess_path):
                        with open(sess_path, encoding="utf-8") as sf:
                            s_data = json.load(sf)
                            msg_map = {m["id"]: m for m in s_data.get("messages", []) if isinstance(m, dict) and m.get("id")}
                            mid = mem.get("mid")
                            m_obj = msg_map.get(mid) if mid else None
                            el = (m_obj.get("elapsed_time") if m_obj else None) or s_data.get("setting", {}).get("elapsed_time")
                            if el:
                                mem["elapsed_time"] = copy.deepcopy(el)
                                needs_resave = True
                except Exception:
                    pass
                if not mem.get("elapsed_time"):
                    mem["elapsed_time"] = {"day": 1, "period": "午前"}
                    needs_resave = True
            if "priority" in mem or "boost" in mem:
                orig_p = mem.get("priority")
                orig_b = mem.get("boost")
                try:
                    apply_priority_field(mem, priority=orig_p, boost=orig_b)
                except ValueError:
                    mem.pop("priority", None)
                    mem.pop("boost", None)
                if mem.get("priority") != orig_p or ("boost" in mem) != (orig_b is not None):
                    needs_resave = True
            valid_memories.append(mem)

        data["memories"] = valid_memories
        if needs_resave:
            save_memories(session_id, data)
        else:
            _memory_cache[session_id] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "data": copy.deepcopy(data)
            }

        return data


def save_memories(session_id, data):
    path = memory_path(session_id)
    tmp_path = path + f".tmp_{os.getpid()}_{threading.get_ident()}"
    with _store_lock:
        if session_id in _deleted_sessions:
            _memory_cache.pop(session_id, None)
            return
        data["updated_at"] = time.time()
        formatted = to_compact_json(data)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(formatted)
        for _ in range(10):
            try:
                os.replace(tmp_path, path)
                break
            except OSError:
                time.sleep(0.01)
        else:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
        try:
            st = os.stat(path)
            _memory_cache[session_id] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "data": copy.deepcopy(data)
            }
        except OSError:
            _memory_cache.pop(session_id, None)


# 記憶ごとの小さな押し上げ（0〜MEMORY_BOOST_MAX）。
# 地の文対策で順位が固まった rule 4 枠を、必要な1件だけ「ほんの少し」動かす手動ノブ。
# 設定方法: 記憶 JSON の要素に "boost": 0.05 を足すか、
#           POST /api/rag/memories/<id> に {"session_id": ..., "boost": 0.05}
MEMORY_BOOST_MAX = 0.5


def compute_memory_score(mem, query_tokens, query_vec, query_text, current_turn=None):
    """記憶とクエリの特徴量から最終スコア（final_score: float）を算出する。"""
    bonus = 0.0
    content = mem.get("content", "")
    cat = resolve_category(mem.get("category", ""), content)
    if cat == "promise" and any(kw in query_text for kw in PROMISE_KEYWORDS):
        bonus = 0.15
    elif cat == "user_fact" and any(kw in query_text for kw in USER_FACT_KEYWORDS):
        bonus = 0.15
    elif cat == "char_fact" and any(kw in query_text for kw in CHAR_FACT_KEYWORDS):
        bonus = 0.15
    elif cat == "calling" and any(kw in query_text for kw in CALLING_KEYWORDS):
        bonus = 0.15
    elif cat in ("item", "status") and any(kw in query_text for kw in ITEM_KEYWORDS):
        bonus = 0.15
    elif cat == "rule" and any(kw in query_text for kw in RULE_KEYWORDS):
        bonus = 0.15
    elif cat == "relationship" and any(kw in query_text for kw in RELATIONSHIP_KEYWORDS):
        bonus = 0.15
    elif cat == "event" and any(kw in query_text for kw in EVENT_KEYWORDS):
        bonus = 0.15

    mem_tokens = mem.get("tokens") or []
    if not mem_tokens and content:
        try:
            mem_tokens = tokenize_japanese(content)
        except Exception:
            mem_tokens = []

    matched_count = 0
    if query_tokens and mem_tokens:
        query_tokens_set = set(query_tokens)
        mem_tokens_set = set(mem_tokens)
        exact_matches = query_tokens_set.intersection(mem_tokens_set)
        matched_count = len(exact_matches)
        for qt in query_tokens:
            if qt not in exact_matches and any(len(qt) >= 2 and len(mt) >= 2 and (qt in mt or mt in qt) for mt in mem_tokens):
                matched_count += 1
    overlap_q = (matched_count / max(1, len(query_tokens))) if query_tokens else 0.0
    overlap_m = (matched_count / max(1, len(mem_tokens))) if mem_tokens else 0.0
    kw_ratio = max(overlap_q, overlap_m)
    s_kw = min(1.0, max(0.0, kw_ratio))

    mem_vec = mem.get("embedding") or []
    if not mem_vec and content:
        try:
            mem_vec = get_text_embedding(content)
        except Exception:
            mem_vec = []
    s_vec = cosine_similarity(query_vec, mem_vec)

    s_base = (0.70 * s_vec + 0.30 * s_kw) * CATEGORY_SCORE_MULT.get(cat, 1.0) + bonus

    # Recency Boost (0.0 for shared memories, 0.0-1.0 clamped ratio for own session)
    if mem.get("_is_other") or current_turn is None or current_turn <= 0:
        recency_boost = 0.0
    else:
        turn_idx_val = mem.get("turn_idx") or 0
        ratio = max(0.0, min(1.0, turn_idx_val / max(1, current_turn)))
        recency_boost = 0.05 * ratio

    raw_score = s_base + recency_boost + CATEGORY_SCORE_BOOST.get(cat, 0.0)

    # --- 乗算Boost（最低関連度ガード付き） ---
    p_info = memory_priority_info(mem)
    mult = p_info["mult"]

    if mult > 1.0 and s_kw == 0.0 and s_vec < MIN_VEC_FOR_POSITIVE_BOOST:
        effective_mult = 1.0
    else:
        effective_mult = mult

    final_score = raw_score * effective_mult
    return final_score


def load_memories_map(session_id, character_name="", shared_memories=False):
    """自セッション（および必要に応じて同名キャラの共通記憶）の記憶を {id: mem} の辞書として返す"""
    mem_map = {}
    try:
        own_data = load_memories(session_id, character_name)
        for m in own_data.get("memories", []):
            if isinstance(m, dict) and m.get("id"):
                mem_map[str(m["id"])] = dict(m, _is_other=False)
    except Exception as e:
        print(f"[memory_store] load_memories_map own error: {e}")

    if shared_memories and character_name:
        try:
            for fname in sorted(os.listdir(RAG_DIR)):
                if not fname.endswith("_memories.json"):
                    continue
                other_sid = fname[:-len("_memories.json")]
                if other_sid == session_id:
                    continue
                try:
                    other_data = load_memories(other_sid)
                    if other_data.get("character_name") == character_name:
                        for om in other_data.get("memories", []):
                            omid = str(om.get("id") or "")
                            if omid and omid not in mem_map:
                                mem_map[omid] = dict(om, _is_other=True)
                except Exception:
                    continue
        except Exception as e:
            print(f"[memory_store] load_memories_map shared error: {e}")
    return mem_map


def compute_scores_for_memories(memories, query_text="", current_turn=None):
    """記憶のリストとクエリ文字列から、各記憶のスコア辞書 {mem_id: round(score, 4)} を計算して返す"""
    query_text = (query_text or "").strip()
    if not query_text or not memories:
        return {}
    query_tokens, query_vec = get_query_features(query_text)
    scores = {}
    for m in memories:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        sc = compute_memory_score(m, query_tokens, query_vec, query_text, current_turn=current_turn)
        scores[str(m["id"])] = round(float(sc), 4)
    return scores


def search_memories(session_id, query_text, current_turn=None, shared_memories=False, character_name="", limit=10):
    query_text = (query_text or "").strip()
    if not query_text:
        return []

    candidates = []
    own_data = load_memories(session_id, character_name)
    own_memories = own_data.get("memories", [])

    for m in own_memories:
        cat = m.get("category", "")
        p_key = memory_priority_key(m)
        is_prioritized = p_key in ("pinned", "highest", "high")

        # 旧バージョンが作った誤爆 relationship（「〜みたい」等）を想起させない。
        # データは消さず、手動追加・ピン留め・優先指定だけは残す。
        if (resolve_category(cat, m.get("content", "")) == "relationship"
                and m.get("source") != "manual"
                and not is_prioritized
                and should_skip_relationship_extraction(m.get("content"))):
            continue

        ttl = TRANSIENT_TURNS.get(cat)
        # 古い痕跡や外れた装着物は「今の前提」にしない（手動記憶、またはピン留め・最優先・優先にした記憶は残す）
        if ttl and current_turn is not None and m.get("source") != "manual" and not is_prioritized:
            if current_turn - (m.get("turn_idx") or 0) > ttl:
                continue
        # 直近2ターン以内の auto 記憶除外（ピン留め・最優先・優先にした記憶は除外せず即座に対象にする）
        if m.get("source") == "auto" and current_turn is not None and not is_prioritized:
            turn_idx_val = m.get("turn_idx") or 0
            if current_turn - turn_idx_val <= 2:
                # calling は直近ログで維持できるため、免除カテゴリーから外す
                if not (
                    cat in ("promise", "rule", "item", "status")
                    and m.get("status", "active") == "active"
                ):
                    continue
        candidates.append(dict(m, _is_other=False))

    # If shared_memories is True, scan other session files (Read-Only)
    if shared_memories and character_name:
        try:
            for fname in sorted(os.listdir(RAG_DIR)):
                if not fname.endswith("_memories.json"):
                    continue
                other_sid = fname[:-len("_memories.json")]
                if other_sid == session_id:
                    continue
                try:
                    other_data = load_memories(other_sid)
                    if other_data.get("character_name") == character_name:
                        for om in other_data.get("memories", []):
                            if not any(c.get("id") == om.get("id") for c in candidates):
                                candidates.append(dict(om, _is_other=True))
                except Exception:
                    continue
        except Exception as e:
            print(f"[memory_store] error loading shared memories: {e}")

    if not candidates:
        return []

    query_tokens, query_vec = get_query_features(query_text)

    scored = []
    for mem in candidates:
        content = mem.get("content", "")
        cat = resolve_category(mem.get("category", ""), content)
        final_score = compute_memory_score(mem, query_tokens, query_vec, query_text, current_turn=current_turn)

        p_info = memory_priority_info(mem)
        p_key = p_info["key"]

        m_copy = dict(mem)
        m_copy["category"] = cat
        if p_key != "default":
            m_copy["priority"] = p_key
        else:
            m_copy.pop("priority", None)
        m_copy["score"] = round(final_score, 4)
        scored.append({
            "memory": m_copy,
            "score": final_score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 1. Tier 1: 常時前提枠（ピン留め記憶 ＋ active な rule、最大 RULE_TIER1_CAP 件）
    tier1_items = []
    tier1_ids = set()

    # calling 専用の Tier 1 枠は廃止。calling は「ピン留めされている場合だけ」ここに入る。
    standing_candidates = []
    for item in scored:
        m = item["memory"]
        if m["id"] in tier1_ids or m.get("status", "active") != "active":
            continue
        p_key = memory_priority_key(m)
        if p_key == "pinned":
            standing_candidates.append(m)
        elif m.get("category") == "rule" and p_key != "low":
            # level 0 も候補には残す。並び順で最下位になり、空き枠があれば Tier 1 に入る既存挙動。
            standing_candidates.append(m)

    standing_candidates.sort(
        key=lambda m: (
            rule_standing_level(
                m.get("content"),
                m.get("source"),
                memory_priority_key(m),
                is_other=bool(m.get("_is_other")),
            ),
            m.get("score") or 0.0,
        ),
        reverse=True,
    )
    for r in standing_candidates[:RULE_TIER1_CAP]:
        r["_tier"] = 1
        tier1_items.append(r)
        tier1_ids.add(r["id"])

    # 2. Tier 2: 類似度想起枠
    #    Tier 1 の保護枠（最大5件）は limit の外数。Tier 2 は最大 limit 件。
    remaining_capacity = max(0, limit)
    picked_tier2_ids = set()
    tier2_group_counts = collections.Counter()  # Tier 1 の件数は混算しない

    tier2_extra = []
    for item in scored:
        if remaining_capacity <= 0:
            break
        mem = item["memory"]
        mem_id = mem["id"]
        if mem_id in tier1_ids or mem_id in picked_tier2_ids:
            continue
        p_info = memory_priority_info(mem)
        p_key = p_info["key"]
        grp = (mem.get("category") or "")

        # 解消済（fulfilled）の rule / calling は過去の古い設定のため想起しない
        if grp in ("rule", "calling") and mem.get("status", "active") != "active":
            continue

        if p_key != "pinned":
            if item["score"] < p_info["min_score"]:
                continue
            # 通常時 rule は Tier 2 に流さない（calling は Tier 2 での想起を許可）
            if grp == "rule" and p_key != "low":
                continue
            if tier2_group_counts[grp] >= GROUP_CAPS.get(grp, GROUP_CAP_DEFAULT):
                continue
        # p_key == "pinned" は「Tier 1 の5枠から溢れたピン留め」の救済。
        # min_score と GROUP_CAPS は免除するが、予算トリムの保護対象ではない。

        mem["_tier"] = 2
        tier2_group_counts[grp] += 1
        tier2_extra.append(mem)
        picked_tier2_ids.add(mem_id)
        remaining_capacity -= 1

    result = tier1_items + tier2_extra

    # ほぼ同内容の重複を落とす（Tier 1 と ピン留め は必ず残す）
    if TIER2_DEDUP_SIM > 0 and len(result) > 1:
        kept = []
        for mem in result:
            vec = mem.get("embedding") or []
            if mem.get("_tier") != 1 and not is_pinned_memory(mem) and vec and any(
                k.get("embedding") and cosine_similarity(vec, k["embedding"]) >= TIER2_DEDUP_SIM for k in kept
            ):
                continue
            kept.append(mem)
        result = kept

    return result


def filter_new_items(session_id, items):
    """既存記憶の content_hash に無い項目だけを返す（一括再生成の併合モード用）。

    新しい記憶を追加するだけで、既存記憶には一切触れない。手動記憶・自動記憶の区別なく
    同じ内容（content_hash 一致）のものは除外する。入力に同じ内容が複数あっても1件に縮退する。
    """
    if not items:
        return []
    if isinstance(items, dict):
        items = [items]
    elif isinstance(items, str):
        items = [{"category": "event", "content": items}]

    existing_hashes = set()
    if session_id and isinstance(session_id, str):
        try:
            data = load_memories(session_id.strip())
            for m in data.get("memories", []):
                h = m.get("content_hash")
                if not h:
                    h = compute_hash(m.get("content") or "")
                if h:
                    existing_hashes.add(h)
        except Exception as e:
            print(f"[filter_new_items] load_memories error: {e}")

    result = []
    seen = set(existing_hashes)
    for item in items:
        if isinstance(item, str):
            item = {"category": "event", "content": item}
        if not isinstance(item, dict):
            continue
        cat_raw = str(item.get("category") or item.get("type") or item.get("cat") or "").strip().lower()
        content = str(item.get("content") or item.get("text") or item.get("memory") or item.get("detail") or "").strip()
        if not content:
            continue
        cat = resolve_category(cat_raw, content)
        if cat not in VALID_CATEGORIES:
            continue
        h = compute_hash(content)
        if h in seen:
            continue
        seen.add(h)
        new_item = dict(item)
        new_item["category"] = cat
        new_item["content"] = content
        result.append(new_item)
    return result


def _archive_memory_to_history(mem_obj, now_ts):
    """既存記憶の更新前に現在の状態を history へ退避する（priority も保持）"""
    old_hist = {
        "turn_idx": mem_obj.get("turn_idx"),
        "elapsed_time": copy.deepcopy(mem_obj.get("elapsed_time")),
        "mid": mem_obj.get("mid"),
        "content": mem_obj.get("content"),
        "tokens": copy.deepcopy(mem_obj.get("tokens") or []),
        "embedding": copy.deepcopy(mem_obj.get("embedding") or []),
        "content_hash": mem_obj.get("content_hash") or "",
        "archived_at": now_ts
    }
    if mem_obj.get("priority"):
        old_hist["priority"] = mem_obj["priority"]
    mem_obj.setdefault("history", []).append(old_hist)


def save_extracted_memories(session_id, character_name, new_memories, resolved_ids=None, turn_idx=None, mid=None, elapsed_time=None, max_new=None, touch=True):
    """抽出した記憶を保存する。

    touch=False のとき、既存記憶へ併合する場合に turn_idx / mid / elapsed_time / updated_at を
    上書きしない（一括再生成の非破壊モード用）。新規追加時の挙動は touch に関係なく従来どおり。
    """
    if not session_id or not isinstance(session_id, str):
        return {"session_id": session_id or "", "character_name": character_name or "", "memories": [], "updated_at": time.time()}
    session_id = session_id.strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return {"session_id": "", "character_name": character_name or "", "memories": [], "updated_at": time.time()}

    character_name = str(character_name or "").strip()
    if turn_idx is not None:
        try:
            turn_idx = int(turn_idx)
        except (ValueError, TypeError):
            turn_idx = None
    if mid is not None:
        mid = str(mid).strip() or None

    # 1. 重い形態素解析とONNXベクトル推論は _store_lock の外で事前に完了させる（他スレッドの検索・削除をブロックしない）
    if isinstance(new_memories, dict):
        new_memories = [new_memories]
    elif isinstance(new_memories, str):
        new_memories = [{"category": "event", "content": new_memories}]

    valid_items = []
    seen_items = set()
    for item in (new_memories or []):
        if isinstance(item, str):
            item = {"category": "event", "content": item}
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or item.get("type") or item.get("cat") or "").strip().lower()
        content = str(item.get("content") or item.get("text") or item.get("memory") or item.get("detail") or "").strip()
        cat = resolve_category(cat, content)
        if not content or cat not in VALID_CATEGORIES:
            continue
        # 情景描写が rule として入ると常時枠を埋めるので、一過性の状態として保存する
        # （rule としては選抜されない。status は TRANSIENT_TURNS で自然に消える）
        if cat == "rule" and should_skip_rule_extraction(content):
            cat = "status"
        # 比喩（〜みたい／〜のふり）・仮定・感情の叫びから relationship を作らない
        # （最大の誤爆源。誤って作ると Tier1/Tier2 を感情ノイズで埋めるので破棄する）
        if cat == "relationship" and should_skip_relationship_extraction(content):
            continue
        pair = (cat, content)
        if pair in seen_items:
            continue
        seen_items.add(pair)
        valid_items.append(pair)

    prepared_items = []
    if valid_items:
        texts = [c for _, c in valid_items]
        try:
            embeddings = get_embeddings_batch(texts)
        except Exception as e:
            print(f"[save_extracted_memories] get_embeddings_batch error: {e}")
            embeddings = [[] for _ in texts]
        if len(embeddings) != len(texts):
            embeddings = [[] for _ in texts]

        for (cat, content), new_vec in zip(valid_items, embeddings):
            try:
                tokens = extract_tokens(content)
            except Exception as e:
                print(f"[save_extracted_memories] extract_tokens error: {e}")
                tokens = []
            h = compute_hash(content)
            prepared_items.append((cat, content, new_vec, tokens, h))

    if elapsed_time is None and session_id:
        try:
            sess_path = os.path.join(DATA, "sessions", f"{session_id}.json")
            if os.path.exists(sess_path):
                with open(sess_path, encoding="utf-8-sig") as sf:
                    s_data = json.load(sf)
                    elapsed_time = copy.deepcopy(s_data.get("setting", {}).get("elapsed_time"))
        except Exception:
            pass
    if elapsed_time is None or not isinstance(elapsed_time, dict):
        elapsed_time = {"day": 1, "period": "午前"}
    else:
        elapsed_time = copy.deepcopy(elapsed_time)

    SEMANTIC_THRESHOLDS = {
        "calling": 0.70,
        "relationship": 0.70,
        "rule": 0.82,
        "char_fact": 0.75,
        "user_fact": 0.75,
    }

    # 2. _store_lock 内では高速なメモリ上の類似度マージとファイル保存のみを行う
    with _store_lock:
        data = load_memories(session_id, character_name)
        memories = data.get("memories", [])

        # Process resolved_ids non-destructively
        if resolved_ids:
            if isinstance(resolved_ids, (str, int)):
                resolved_ids = [resolved_ids]
            if isinstance(resolved_ids, (list, tuple, set)):
                res_set = {str(rid).strip() for rid in resolved_ids if rid is not None and str(rid).strip()}
                for m in memories:
                    if m.get("id") in res_set:
                        m["status"] = "fulfilled"
                        m["resolved_turn"] = turn_idx
                        m["resolved_mid"] = mid
                        m["resolved_elapsed_time"] = copy.deepcopy(elapsed_time) if elapsed_time else None
                        m["updated_at"] = time.time()

        if prepared_items:
            now_ts = time.time()
            # 通常は1ターン上限。一括再生成などは呼び出し側で緩める
            limit_new = MAX_NEW_MEMORIES_PER_TURN if max_new is None else max(0, int(max_new))
            # 上限を超えるときだけ、優先度の高い順に並べて残す（超えないときは抽出順のまま）
            if len(prepared_items) > limit_new:
                prepared_items.sort(key=lambda x: MEMORY_PRIORITY.get(x[0], 9))
            appended_new = 0
            current_batch_ids = set()
            for cat, content, new_vec, new_tokens, new_hash in prepared_items:
                if cat in ("event", "promise"):
                    best_recent_sim = -1.0
                    best_recent_idx = -1
                    for idx, existing in enumerate(memories):
                        if existing.get("id") in current_batch_ids:
                            continue
                        if is_pinned_memory(existing) or memory_priority_key(existing) != "default":
                            continue
                        if existing.get("category") == cat and existing.get("source") == "auto":
                            if cat == "promise" and existing.get("status", "active") != "active":
                                continue
                            ex_turn = existing.get("turn_idx")
                            if turn_idx is not None and ex_turn is not None and abs(turn_idx - ex_turn) <= 1:
                                sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                                if sim > best_recent_sim:
                                    best_recent_sim = sim
                                    best_recent_idx = idx

                    if best_recent_sim >= 0.85 and best_recent_idx >= 0:
                        _archive_memory_to_history(memories[best_recent_idx], now_ts)
                        memories[best_recent_idx]["content"] = content
                        memories[best_recent_idx]["tokens"] = new_tokens
                        memories[best_recent_idx]["embedding"] = new_vec
                        memories[best_recent_idx]["content_hash"] = new_hash
                        if touch:
                            memories[best_recent_idx]["updated_at"] = now_ts
                            memories[best_recent_idx]["turn_idx"] = turn_idx
                            if mid:
                                memories[best_recent_idx]["mid"] = mid
                            if elapsed_time:
                                memories[best_recent_idx]["elapsed_time"] = copy.deepcopy(elapsed_time)
                    else:
                        if appended_new >= limit_new:
                            continue
                        appended_new += 1
                        new_id = "mem_" + uuid.uuid4().hex[:8]
                        current_batch_ids.add(new_id)
                        memories.append({
                            "id": new_id,
                            "session_id": session_id,
                            "mid": mid,
                            "turn_idx": turn_idx,
                            "elapsed_time": copy.deepcopy(elapsed_time) if elapsed_time else None,
                            "source": "auto",
                            "category": cat,
                            "status": "active",
                            "content": content,
                            "history": [],
                            "content_hash": new_hash,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "tokens": new_tokens,
                            "embedding": new_vec
                        })
                elif cat in ("item", "status"):
                    best_sim = -1.0
                    best_idx = -1
                    for idx, existing in enumerate(memories):
                        if existing.get("id") in current_batch_ids:
                            continue
                        if is_pinned_memory(existing) or memory_priority_key(existing) != "default":
                            continue
                        if existing.get("category") in ("item", "status") and existing.get("source") == "auto":
                            if existing.get("status", "active") != "active":
                                continue
                            sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                            if sim > best_sim:
                                best_sim = sim
                                best_idx = idx

                    if best_idx >= 0 and best_sim >= 0.85:
                        _archive_memory_to_history(memories[best_idx], now_ts)
                        memories[best_idx]["content"] = content
                        memories[best_idx]["tokens"] = new_tokens
                        memories[best_idx]["embedding"] = new_vec
                        memories[best_idx]["content_hash"] = new_hash
                        if touch:
                            memories[best_idx]["updated_at"] = now_ts
                            memories[best_idx]["turn_idx"] = turn_idx
                            if mid:
                                memories[best_idx]["mid"] = mid
                            if elapsed_time:
                                memories[best_idx]["elapsed_time"] = copy.deepcopy(elapsed_time)
                    else:
                        if appended_new >= limit_new:
                            continue
                        appended_new += 1
                        new_id = "mem_" + uuid.uuid4().hex[:8]
                        current_batch_ids.add(new_id)
                        memories.append({
                            "id": new_id,
                            "session_id": session_id,
                            "mid": mid,
                            "turn_idx": turn_idx,
                            "elapsed_time": copy.deepcopy(elapsed_time) if elapsed_time else None,
                            "source": "auto",
                            "category": cat,
                            "status": "active",
                            "content": content,
                            "history": [],
                            "content_hash": new_hash,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "tokens": new_tokens,
                            "embedding": new_vec
                        })
                else:  # calling, relationship, rule, char_fact, user_fact
                    best_sim = -1.0
                    best_idx = -1
                    thresh = SEMANTIC_THRESHOLDS.get(cat, 0.70)
                    for idx, existing in enumerate(memories):
                        if existing.get("id") in current_batch_ids:
                            continue
                        if is_pinned_memory(existing) or memory_priority_key(existing) != "default":
                            continue
                        ex_cat = existing.get("category")
                        if ex_cat == cat and existing.get("source") == "auto":
                            # 解消済（fulfilled）の rule / calling には新しい記憶をマージしない。
                            # ※新規抽出分は「新しい active 記憶」として追加され得る。
                            if cat in ("rule", "calling") and existing.get("status", "active") != "active":
                                continue
                            sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                            if sim > best_sim:
                                best_sim = sim
                                best_idx = idx

                    if best_idx >= 0 and best_sim >= thresh:
                        _archive_memory_to_history(memories[best_idx], now_ts)
                        memories[best_idx]["content"] = content
                        memories[best_idx]["tokens"] = new_tokens
                        memories[best_idx]["embedding"] = new_vec
                        memories[best_idx]["content_hash"] = new_hash
                        if touch:
                            memories[best_idx]["updated_at"] = now_ts
                            memories[best_idx]["turn_idx"] = turn_idx
                            if mid:
                                memories[best_idx]["mid"] = mid
                            if elapsed_time:
                                memories[best_idx]["elapsed_time"] = copy.deepcopy(elapsed_time)
                    else:
                        if appended_new >= limit_new:
                            continue
                        appended_new += 1
                        new_id = "mem_" + uuid.uuid4().hex[:8]
                        current_batch_ids.add(new_id)
                        memories.append({
                            "id": new_id,
                            "session_id": session_id,
                            "mid": mid,
                            "turn_idx": turn_idx,
                            "elapsed_time": copy.deepcopy(elapsed_time) if elapsed_time else None,
                            "source": "auto",
                            "category": cat,
                            "status": "active",
                            "content": content,
                            "history": [],
                            "content_hash": new_hash,
                            "created_at": now_ts,
                            "updated_at": now_ts,
                            "tokens": new_tokens,
                            "embedding": new_vec
                        })

        data["memories"] = memories
        if character_name:
            data["character_name"] = character_name
        data["updated_at"] = time.time()
        save_memories(session_id, data)
        return data


def delete_after_turn(session_id, turn_idx):
    if not session_id or turn_idx is None:
        return
    session_id = str(session_id).strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return
    try:
        turn_idx = int(turn_idx)
    except (ValueError, TypeError):
        return

    with _store_lock:
        data = load_memories(session_id)
        original_memories = data.get("memories", [])
        new_memories = []
        changed = False

        for m in original_memories:
            # 1. If fulfilled promise was resolved after turn_idx, revert status to active
            if m.get("status") == "fulfilled":
                res_turn = m.get("resolved_turn")
                try:
                    res_turn_val = int(res_turn) if res_turn is not None else None
                except (ValueError, TypeError):
                    res_turn_val = None
                if res_turn_val is not None and res_turn_val > turn_idx:
                    m["status"] = "active"
                    m.pop("resolved_turn", None)
                    m.pop("resolved_mid", None)
                    m.pop("resolved_elapsed_time", None)
                    m["updated_at"] = time.time()
                    changed = True

            # 手動記憶、およびユーザーが「📌 ピン留め」した記憶は巻き戻しで削除しない
            if m.get("source") != "auto" or is_pinned_memory(m):
                new_memories.append(m)
                continue

            # 2. Check if the memory itself was created or updated after turn_idx
            m_turn = m.get("turn_idx")
            try:
                m_turn_val = int(m_turn) if m_turn is not None else None
            except (ValueError, TypeError):
                m_turn_val = None

            if m_turn_val is not None and m_turn_val > turn_idx:
                history = m.get("history") or []
                valid_hist = []
                for h in history:
                    try:
                        h_turn = int(h.get("turn_idx")) if h.get("turn_idx") is not None else None
                    except (ValueError, TypeError):
                        h_turn = None
                    if h_turn is not None and h_turn <= turn_idx:
                        valid_hist.append(h)

                if valid_hist:
                    restored = valid_hist[-1]
                    kept_prio = m.get("priority") or restored.get("priority")
                    m["content"] = restored["content"]
                    m["turn_idx"] = restored.get("turn_idx")
                    m["mid"] = restored.get("mid")
                    m["elapsed_time"] = copy.deepcopy(restored.get("elapsed_time"))
                    m["content_hash"] = restored.get("content_hash") or compute_hash(restored["content"])
                    m["tokens"] = copy.deepcopy(restored.get("tokens")) if restored.get("tokens") else extract_tokens(restored["content"])
                    m["embedding"] = copy.deepcopy(restored.get("embedding")) if restored.get("embedding") else get_embedding(restored["content"])
                    m["history"] = valid_hist[:-1]
                    apply_priority_field(m, priority=kept_prio)
                    m["updated_at"] = time.time()
                    new_memories.append(m)
                    changed = True
                else:
                    # Memory was created after turn_idx and has no past history before turn_idx -> drop it
                    changed = True
                    continue
            else:
                new_memories.append(m)

        if changed or len(new_memories) != len(original_memories):
            data["memories"] = new_memories
            data["updated_at"] = time.time()
            save_memories(session_id, data)


def delete_by_mid(session_id, mid):
    if not mid or not session_id:
        return
    session_id = str(session_id).strip()
    mid = str(mid).strip()
    if not session_id or not mid or not all(c.isalnum() or c in "-_" for c in session_id):
        return

    with _store_lock:
        data = load_memories(session_id)
        original_memories = data.get("memories", [])
        new_memories = []
        changed = False

        for m in original_memories:
            # 1. If resolved_mid matches mid, revert to active
            if m.get("status") == "fulfilled" and m.get("resolved_mid") == mid:
                m["status"] = "active"
                m.pop("resolved_turn", None)
                m.pop("resolved_mid", None)
                m.pop("resolved_elapsed_time", None)
                m["updated_at"] = time.time()
                changed = True

            # 手動記憶、およびユーザーが「📌 ピン留め」した記憶は削除しない
            if m.get("source") != "auto" or is_pinned_memory(m):
                new_memories.append(m)
                continue

            # 2. If memory mid matches mid, restore from history if possible
            if m.get("mid") == mid:
                history = m.get("history") or []
                if history:
                    restored = history[-1]
                    kept_prio = m.get("priority") or restored.get("priority")
                    m["content"] = restored["content"]
                    m["turn_idx"] = restored.get("turn_idx")
                    m["mid"] = restored.get("mid")
                    m["elapsed_time"] = copy.deepcopy(restored.get("elapsed_time"))
                    m["content_hash"] = restored.get("content_hash") or compute_hash(restored["content"])
                    m["tokens"] = copy.deepcopy(restored.get("tokens")) if restored.get("tokens") else extract_tokens(restored["content"])
                    m["embedding"] = copy.deepcopy(restored.get("embedding")) if restored.get("embedding") else get_embedding(restored["content"])
                    m["history"] = history[:-1]
                    apply_priority_field(m, priority=kept_prio)
                    m["updated_at"] = time.time()
                    new_memories.append(m)
                    changed = True
                else:
                    changed = True
                    continue
            else:
                new_memories.append(m)

        if changed or len(new_memories) != len(original_memories):
            data["memories"] = new_memories
            data["updated_at"] = time.time()
            save_memories(session_id, data)


def resolve_items(session_id, names, turn_idx=None, mid=None, elapsed_time=None, character_name=""):
    """装着物が外れたとき、その物の item 記憶を fulfilled にする（解除の裏取り）。

    state の equipped_items から消えた物名を受け取り、本文にその物名と装着動詞の両方を含む
    自動抽出の item 記憶だけを解決する（「プレゼントされた」等の贈与文や手動編集は対象外）。
    履歴の巻き戻しで active に戻せるよう、resolved_mid / resolved_turn を既存の解決処理と同じ形で残す。
    """
    if not session_id or not isinstance(session_id, str):
        return 0
    session_id = session_id.strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return 0
    names = [n for n in (names or []) if isinstance(n, str) and n.strip()]
    if not names:
        return 0

    with _store_lock:
        data = load_memories(session_id, character_name)
        changed = 0
        for m in data.get("memories", []):
            if m.get("category") != "item" or m.get("status", "active") != "active":
                continue
            if m.get("source") == "manual" or is_pinned_memory(m):
                continue   # 手で書いた記憶およびピン留め記憶はユーザーの意思を優先する
            body = m.get("content", "")
            if not any(n in body for n in names):
                continue
            if not any(v in body for v in ITEM_VERBS):
                continue   # 「プレゼントされた」等は解除対象にしない
            m["status"] = "fulfilled"     # 既存コードの語彙は active / fulfilled
            m["resolved_turn"] = turn_idx
            m["resolved_mid"] = mid        # 履歴の巻き戻しで active に戻す処理がこれを見る
            m["resolved_elapsed_time"] = copy.deepcopy(elapsed_time) if elapsed_time else None
            m["updated_at"] = time.time()
            changed += 1
        if changed:
            data["updated_at"] = time.time()
            save_memories(session_id, data)
        return changed


def shift_turn_indices(session_id, after_turn, delta=-1):
    """after_turn より後ろの通算ターン番号を delta だけずらす。
    途中のメッセージを削除してターンが詰まったときに、以降の記憶の番号を合わせるために使う。"""
    if not session_id or after_turn is None or not delta:
        return
    session_id = str(session_id).strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return
    try:
        after_turn = int(after_turn)
        delta = int(delta)
    except (ValueError, TypeError):
        return
    if not delta:
        return

    def _shift(v):
        try:
            n = int(v)
        except (ValueError, TypeError):
            return v
        return max(0, n + delta) if n > after_turn else n

    with _store_lock:
        data = load_memories(session_id)
        changed = False
        for m in data.get("memories", []):
            if m.get("turn_idx") is not None:
                nv = _shift(m.get("turn_idx"))
                if nv != m.get("turn_idx"):
                    m["turn_idx"] = nv
                    changed = True
            if m.get("resolved_turn") is not None:
                nv = _shift(m.get("resolved_turn"))
                if nv != m.get("resolved_turn"):
                    m["resolved_turn"] = nv
                    changed = True
            for h in m.get("history") or []:
                if isinstance(h, dict) and h.get("turn_idx") is not None:
                    nv = _shift(h.get("turn_idx"))
                    if nv != h.get("turn_idx"):
                        h["turn_idx"] = nv
                        changed = True
        if changed:
            data["updated_at"] = time.time()
            save_memories(session_id, data)


def add_manual_memory(session_id, character_name, category, content, elapsed_time=None, status="active", priority=None, boost=None):
    content = (content or "").strip()
    if not content:
        raise ValueError("content is required")
    category = resolve_category((category or "").strip(), content)
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category: {category}")

    status = (status or "active").strip().lower()
    if status not in ("active", "fulfilled"):
        status = "active"

    p_key = resolve_priority_key(priority=priority, boost=boost)

    if elapsed_time is None and session_id:
        try:
            sess_path = os.path.join(DATA, "sessions", f"{session_id}.json")
            if os.path.exists(sess_path):
                with open(sess_path, encoding="utf-8-sig") as sf:
                    s_data = json.load(sf)
                    elapsed_time = copy.deepcopy(s_data.get("setting", {}).get("elapsed_time"))
        except Exception:
            pass
    if elapsed_time is None:
        elapsed_time = {"day": 1, "period": "午前"}

    tokens = extract_tokens(content)
    vec = get_embedding(content)
    h = compute_hash(content)
    now_ts = time.time()
    new_id = "mem_" + uuid.uuid4().hex[:8]

    new_mem = {
        "id": new_id,
        "session_id": session_id,
        "mid": None,
        "turn_idx": None,
        "elapsed_time": copy.deepcopy(elapsed_time),
        "source": "manual",
        "category": category,
        "status": status,
        "content": content,
        "history": [],
        "content_hash": h,
        "created_at": now_ts,
        "updated_at": now_ts,
        "tokens": tokens,
        "embedding": vec
    }
    apply_priority_field(new_mem, priority=p_key)

    with _store_lock:
        data = load_memories(session_id, character_name)
        data["memories"].append(new_mem)
        if character_name:
            data["character_name"] = character_name
        save_memories(session_id, data)

    return new_mem


def update_memory(session_id, mem_id, category=None, content=None, status=None, boost=None, priority=None):
    if not session_id or not mem_id:
        raise ValueError("session_id and mem_id are required")
    new_tokens = None
    new_vec = None
    new_hash = None
    if content is not None:
        content = content.strip()
        if not content:
            raise ValueError("content cannot be empty")
        new_hash = compute_hash(content)
        new_tokens = extract_tokens(content)
        new_vec = get_embedding(content)

    has_priority_update = (priority is not None) or (boost is not None)
    if has_priority_update:
        p_key = resolve_priority_key(priority=priority, boost=boost)

    with _store_lock:
        data = load_memories(session_id)
        mem = next((m for m in data.get("memories", []) if m.get("id") == mem_id), None)
        if not mem:
            raise KeyError("no such memory")

        content_or_cat_changed = False
        effective_content = content if content is not None else mem.get("content", "")

        if category is not None:
            norm_cat = resolve_category(category.strip(), effective_content)
            if norm_cat not in VALID_CATEGORIES:
                raise ValueError(f"invalid category: {category}")
            if norm_cat != mem.get("category"):
                mem["category"] = norm_cat
                content_or_cat_changed = True

        if content is not None and content != mem.get("content"):
            mem["content"] = content
            mem["content_hash"] = new_hash
            mem["tokens"] = new_tokens
            mem["embedding"] = new_vec
            content_or_cat_changed = True

        if has_priority_update:
            apply_priority_field(mem, priority=p_key)

        if status is not None:
            status = status.strip().lower()
            if status in ("active", "fulfilled"):
                mem["status"] = status
                if status == "fulfilled":
                    mem["resolved_at"] = time.time()
                elif "resolved_at" in mem:
                    del mem["resolved_at"]

        # 本文またはカテゴリを手動変更した時のみ manual に昇格
        # （優先度やステータスのみの変更時は auto の寿命・巻き戻し連動を維持）
        if (content is not None) or (category is not None):
            mem["source"] = "manual"

        mem["updated_at"] = time.time()
        save_memories(session_id, data)
        return mem


def delete_memory(session_id, mem_id):
    with _store_lock:
        data = load_memories(session_id)
        orig_len = len(data.get("memories", []))
        data["memories"] = [m for m in data.get("memories", []) if m.get("id") != mem_id]
        if len(data["memories"]) == orig_len:
            raise KeyError("no such memory")
        save_memories(session_id, data)
        return True


def clear_auto_memories(session_id):
    with _store_lock:
        path = memory_path(session_id)
        if os.path.exists(path):
            os.makedirs(TRASH, exist_ok=True)
            ts = int(time.time())
            trash_path = os.path.join(TRASH, f"{session_id}_{ts}_memories.json")
            n = 0
            while os.path.exists(trash_path):
                n += 1
                trash_path = os.path.join(TRASH, f"{session_id}_{ts}_{n}_memories.json")
            try:
                shutil.copy2(path, trash_path)
            except Exception as e:
                print(f"[memory_store] error backing up memories to trash: {e}")
        data = load_memories(session_id)
        kept_memories = []
        for m in data.get("memories", []):
            # 手動記憶、またはユーザーが手動で優先度（ピン留め・最優先・優先・抑制）を設定した記憶は保護する
            if m.get("source") == "manual" or memory_priority_key(m) != "default":
                if m.get("status") == "fulfilled":
                    m["status"] = "active"
                    m.pop("resolved_turn", None)
                    m.pop("resolved_mid", None)
                    m.pop("resolved_elapsed_time", None)
                    m["updated_at"] = time.time()
                kept_memories.append(m)
        data["memories"] = kept_memories
        save_memories(session_id, data)
        return data


def delete_session_memories(session_id, to_trash=True):
    """セッション削除時にRAG記憶ファイル（および残存tmp・corruptファイル）を退避・削除し、キャッシュを消去する"""
    if not session_id or not isinstance(session_id, str):
        return False
    session_id = session_id.strip()
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return False

    with _store_lock:
        _deleted_sessions.add(session_id)
        _memory_cache.pop(session_id, None)
        path = memory_path(session_id)

        # 1. 残存 .tmp_* および .corrupt_* ファイルの一括消去
        if os.path.isdir(RAG_DIR):
            prefix = f"{session_id}_memories.json."
            sid_mem = f"{session_id}_memories."
            for fname in os.listdir(RAG_DIR):
                if (
                    fname.startswith(prefix)
                    or fname == f"{session_id}_memories.tmp"
                    or (fname.startswith(sid_mem) and (fname.endswith(".tmp") or ".tmp_" in fname or ".corrupt_" in fname))
                ):
                    try:
                        os.remove(os.path.join(RAG_DIR, fname))
                    except OSError:
                        pass

        if not os.path.exists(path):
            return True

        # 2. 本体ファイルの退避または削除（Windows同秒衝突対策ループ付き）
        if to_trash:
            try:
                os.makedirs(TRASH, exist_ok=True)
                ts = int(time.time())
                dst = os.path.join(TRASH, f"{session_id}_{ts}_memories.json")
                n = 0
                while os.path.exists(dst):
                    n += 1
                    dst = os.path.join(TRASH, f"{session_id}_{ts}_{n}_memories.json")
                shutil.move(path, dst)
                return True
            except Exception as e:
                print(f"[memory_store] error moving memories to trash ({path}): {e}")

        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except OSError as e:
            print(f"[memory_store] error removing {path}: {e}")
            return False


def _safe_int_env(key, default=None):
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            return default


def format_prompt_memories(memories, current_turn, current_elapsed_time=None,
                           carried_memories=None, stats=None,
                           soft_budget=None, recall_budget=None,
                           max_chars=None, max_newlines=None):
    """記憶セクションを組み立てる（予算制御・フロア保護・差分圧縮対応）。

    - 保護層: ピン留め全カテゴリー＋active rule（上位5件）を先に確保
    - 可変層: それ以外をスコア順に recall_budget まで
    - soft 超過時: 可変層 → rule（フロアまで） → 可変層（0件まで）の順に削り、なお超えるなら削らない
    - max_chars / max_newlines 超過時: フロアまで削れば上限内に収まる（＝スピルを回避できる）場合のみ
      同じ保護基準で削り、フロア単独でも超える（どのみちスピルする）場合は削らない
    stats: included_ids / tier1 / rules / calling / recall / bytes / trimmed_rules /
           trimmed_recall / floor_hit / soft_budget / recall_budget
    """
    memories = memories or []
    carried = carried_memories or []

    # 1. 記憶全体の目標予算（soft）の確定
    env_soft = _safe_int_env("RPCHAT_MEM_SOFT_BUDGET", None)
    if env_soft is not None:
        soft = max(0, env_soft)
    elif soft_budget is not None:
        try:
            soft = max(0, int(soft_budget))
        except (ValueError, TypeError):
            try:
                soft = max(0, int(float(soft_budget)))
            except (ValueError, TypeError):
                soft = MEMORY_SOFT_BUDGET_BYTES
    else:
        soft = MEMORY_SOFT_BUDGET_BYTES

    # 2. 可変層（想起枠）の予算（recall_budget）の確定
    env_recall = _safe_int_env("RPCHAT_MEM_RECALL_BUDGET", None)
    if env_recall is not None:
        recall_budget = max(0, env_recall)
    elif env_soft is not None:
        # ★【連動バグ防止】運用者が RPCHAT_MEM_SOFT_BUDGET のみを単独指定した場合、
        # 呼出元から渡された古い引数 recall_budget（dyn_recall）を破棄し、指定された soft に連動させる
        recall_budget = 0 if soft == 0 else max(200, min(800, int(soft * 0.33)))
    elif recall_budget is not None:
        try:
            recall_budget = max(0, int(recall_budget))
        except (ValueError, TypeError):
            try:
                recall_budget = max(0, int(float(recall_budget)))
            except (ValueError, TypeError):
                recall_budget = 0
    elif soft == 0:
        recall_budget = 0
    else:
        # soft に連動（引数なし時は静的定数 RECALL_BUDGET_BYTES）
        recall_budget = RECALL_BUDGET_BYTES if soft_budget is None else max(200, min(800, int(soft * 0.33)))

    if max_chars is not None:
        try:
            max_chars = int(max_chars)
        except (ValueError, TypeError):
            max_chars = None

    if max_newlines is not None:
        try:
            max_newlines = int(max_newlines)
        except (ValueError, TypeError):
            max_newlines = None

    # 1. soft == 0 の早期リターン
    if soft == 0:
        if isinstance(stats, dict):
            stats.clear()
            stats.update({"included_ids": [], "tier1": 0, "rules": 0, "calling": 0, "recall": 0,
                          "bytes": 0, "trimmed_rules": 0, "trimmed_recall": 0,
                          "floor_hit": True, "soft_budget": 0, "recall_budget": 0})
        return ""

    if isinstance(stats, dict):          # ⑪ 早期リターンでも安全
        stats.clear()
        stats.update({"included_ids": [], "tier1": 0, "rules": 0, "calling": 0, "recall": 0,
                      "bytes": 0, "trimmed_rules": 0, "trimmed_recall": 0,
                      "floor_hit": False, "soft_budget": soft, "recall_budget": recall_budget})
    if not memories and not carried:
        return ""

    is_compact    = os.environ.get("RPCHAT_COMPACT", "1") != "0"
    min_rules  = max(0, _safe_int_env("RPCHAT_MEM_MIN_RULES", MEMORY_MIN_RULES))
    min_recall = 0 if recall_budget == 0 else max(0, _safe_int_env("RPCHAT_MEM_MIN_RECALL", MEMORY_MIN_RECALL))

    def _b(s):
        return len(str(s).encode("utf-8"))

    def _format_single(mem):
        content = mem.get("content") or ""
        cat = resolve_category(mem.get("category"), content)
        cat_label = CATEGORY_LABELS.get(cat, "記憶")
        if mem.get("status") == "fulfilled":
            cat_label += "（達成/解消済）"

        mid = mem.get("id")
        mid_str = "" if mid is None else str(mid)
        show_id = (not is_compact) or cat in ("rule", "item", "status", "promise", "calling")
        tag_prefix = f"ID: {mid_str} | {cat_label}" if show_id else cat_label

        el_time = mem.get("elapsed_time")
        if el_time and isinstance(el_time, dict):
            m_day = el_time.get("day", 1)
            m_period = el_time.get("period", "")
            cur_day = current_elapsed_time.get("day") if (current_elapsed_time and isinstance(current_elapsed_time, dict)) else None
            if cur_day is not None and m_day is not None:
                try:
                    diff_d = int(cur_day) - int(m_day)
                except (ValueError, TypeError):
                    diff_d = None
                if diff_d == 0:
                    rel_str = "今日"
                elif diff_d == 1:
                    rel_str = "1日前"
                elif diff_d is not None and diff_d > 1:
                    rel_str = f"{diff_d}日前"
                else:
                    rel_str = ""
                turn_text = f"（{m_day}日目・{m_period}（{rel_str}））" if rel_str else (f"（{m_day}日目・{m_period}）" if m_period else f"（{m_day}日目）")
            elif m_period:
                turn_text = f"（{m_day}日目・{m_period}）"
            else:
                turn_text = f"（{m_day}日目）"
        elif el_time and isinstance(el_time, str) and el_time.strip():
            turn_text = f"（{el_time.strip()}）"
        elif mem.get("_is_other"):
            turn_text = "（重要記憶）"
        elif mem.get("turn_idx") is not None and current_turn is not None:
            try:
                diff = max(0, int(current_turn) - int(mem.get("turn_idx") or 0))
                turn_text = f"（約{diff}ターン前）"
            except (ValueError, TypeError):
                turn_text = "（重要記憶）"
        else:
            turn_text = "（重要記憶）"
        return f"- [{tag_prefix}] {content} {turn_text}"

    # --- 候補展開（idx で一意識別し id=None でも欠落させない） ------------
    rendered = []
    for idx_m, m in enumerate(memories):
        if not isinstance(m, dict):
            continue
        line = _format_single(m)
        content = m.get("content") or ""
        cat = resolve_category(m.get("category"), content)
        try:
            score = float(m.get("score") or 0.0)
        except (ValueError, TypeError):
            score = 0.0
        rendered.append({
            "idx": idx_m,
            "m": m,
            "id": m.get("id"),
            "line": line,
            "b": _b(line) + 1,
            "cat": cat,
            "score": score
        })

    # 1. calling 専用枠は廃止。
    #    ピン留め calling は下の sel_rules に入り、通常 calling は pool（Tier 2）に入る。

    # 2. 保護枠（sel_rules：最大 RULE_TIER1_CAP 件）
    #    ピン留め記憶（全カテゴリー）＋ active な rule（low 以外）から上位5件
    #    level 0 の rule は並び順で最下位になるが、候補からは除外しない（既存挙動）。
    rules = sorted(
        [
            r for r in rendered
            if (r["m"] or {}).get("status", "active") == "active"
            and (
                is_pinned_memory(r["m"])
                or (
                    r["cat"] == "rule"
                    and memory_priority_key(r["m"]) != "low"
                )
            )
        ],
        key=lambda x: (
            rule_standing_level(
                (x["m"] or {}).get("content"),
                (x["m"] or {}).get("source"),
                memory_priority_key(x["m"]),
                is_other=bool((x["m"] or {}).get("_is_other")),
            ),
            x["score"],
        ),
        reverse=True,
    )
    sel_rules = rules[:RULE_TIER1_CAP]
    protected_idxs = {r["idx"] for r in sel_rules}

    # 3. 可変層プール（pool）
    #    通常時 rule は可変層へ漏らさない。
    #    calling は「抑制(low)」「通常」「Tier 1 から溢れたピン留め」を可変層で受け止める。
    pool = sorted(
        [
            r for r in rendered
            if r["idx"] not in protected_idxs
            and not (
                r["cat"] in ("rule", "calling")
                and (r["m"] or {}).get("status", "active") != "active"
            )
            and (
                r["cat"] != "rule"
                or memory_priority_key(r["m"]) == "low"
                or is_pinned_memory(r["m"])
            )
        ],
        key=lambda x: x["score"],
        reverse=True,
    )

    # --- 可変層の選定（フロア死守） ---------------------------------------
    sel_pool, pool_used = [], 0
    if recall_budget != 0:
        for r in pool:
            if recall_budget is not None and (pool_used + r["b"] > recall_budget) and len(sel_pool) >= min_recall:
                continue
            sel_pool.append(r)
            pool_used += r["b"]

    # --- 見出し・注記 -----------------------------------------------------
    t1_head = "【作中の前提・制約（常時確認・遵守事項）】"
    t1_note = ("※現在の前提（ピン留めされた呼称・ルールなど）。常に守る。" if is_compact else
               "※以下は現在の前提となる確定事項（ピン留めされた呼称、ルールなど）です。"
               "ロールプレイ中常に遵守してください。")
    t2_note = ("※過去の会話から思い出した内容。自然に触れてよい（IDは出すな）。" if is_compact else
               "※以下は過去の会話からあなたが思い出した内容です。無理にすべてに触れる必要はありませんが、"
               "話の流れに合わせて自然に言及・引用し、過去の出来事や約束を覚えているキャラクターとして振る舞ってください。"
               "（※記憶IDは内部管理用のため、セリフ内には絶対に出力しないでください）")

    carried_items = [c for c in carried if isinstance(c, dict)]
    carried_line = ""
    if carried_items:
        head = (carried_items[0].get("content") or "")[:20]
        tail = f"「{head}」ほか" if len(carried_items) > 1 else f"「{head}」"
        carried_line = f"（前回と同じ前提: {len(carried_items)}件 — {tail}）"

    def _render_blocks(r_list, p_list):
        blks = []
        if r_list:
            blks.append("\n".join([t1_head, t1_note] + [r["line"] for r in r_list]))
        if p_list or carried_line:
            lns = [t2_note] + [r["line"] for r in p_list]
            if carried_line:
                lns.append(carried_line)
            blks.append("\n".join(lns))
        return "\n\n".join(blks)

    # --- 実バイト再計算 ---------------------------------------------------
    def _calc_total():
        u = 0
        if sel_rules:
            u += _b(t1_head) + 1 + _b(t1_note) + 1 + sum(r["b"] for r in sel_rules)
        if sel_pool or carried_line:
            u += _b(t2_note) + 1 + sum(r["b"] for r in sel_pool)
            if carried_line:
                u += _b(carried_line) + 1
        return u

    # --- 1. ソフト上限トリム（ピン留めされた保護枠はトリムから完全保護） --
    used = _calc_total()
    trimmed_rules = trimmed_recall = 0
    floor_hit = False
    pinned_rules_count = sum(1 for r in sel_rules if is_pinned_memory(r["m"]))
    effective_min_rules = max(min_rules, pinned_rules_count)

    if soft is not None:
        while used > soft and len(sel_pool) > min_recall:
            sel_pool.pop()
            trimmed_recall += 1
            used = _calc_total()
        while used > soft and len(sel_rules) > effective_min_rules:
            sel_rules.pop()
            trimmed_rules += 1
            used = _calc_total()
        while used > soft and sel_pool:
            sel_pool.pop()
            trimmed_recall += 1
            used = _calc_total()
        floor_hit = used > soft

    # --- 2. 文字数・改行数上限トリム（スピルを回避できる場合のみ発動） ----
    if max_chars is not None or max_newlines is not None:
        def _exceeds_char_or_nl(txt):
            if max_chars is not None and len(txt) > max_chars:
                return True
            if max_newlines is not None and txt.count("\n") > max_newlines:
                return True
            return False

        cur_text = _render_blocks(sel_rules, sel_pool)
        if _exceeds_char_or_nl(cur_text):
            # フロア構成（最小限の保護層のみ）にした場合に上限内に収まるかを事前確認する。
            # フロア構成でも超える（＝どのみちスピル確定の）場合は、無意味に Tier 2 を捨てない。
            floor_text = _render_blocks(sel_rules[:effective_min_rules], [])
            if not _exceeds_char_or_nl(floor_text):
                while _exceeds_char_or_nl(_render_blocks(sel_rules, sel_pool)) and len(sel_pool) > min_recall:
                    sel_pool.pop()
                    trimmed_recall += 1
                while _exceeds_char_or_nl(_render_blocks(sel_rules, sel_pool)) and len(sel_rules) > effective_min_rules:
                    sel_rules.pop()
                    trimmed_rules += 1
                while _exceeds_char_or_nl(_render_blocks(sel_rules, sel_pool)) and sel_pool:
                    sel_pool.pop()
                    trimmed_recall += 1

    # --- 出力 -------------------------------------------------------------
    text = _render_blocks(sel_rules, sel_pool)

    if isinstance(stats, dict):
        stats.update({
            "included_ids": list(dict.fromkeys(
                [r["id"] for r in (sel_rules + sel_pool) if r.get("id")]
            )),
            "tier1": len(sel_rules),
            "rules": sum(1 for r in sel_rules if r["cat"] == "rule"),
            "calling": sum(1 for r in sel_rules if r["cat"] == "calling"),
            "recall": len(sel_pool),
            "bytes": _b(text),
            "trimmed_rules": trimmed_rules,
            "trimmed_recall": trimmed_recall,
            "floor_hit": floor_hit,
            "soft_budget": soft,
            "recall_budget": recall_budget,
        })
    return text
