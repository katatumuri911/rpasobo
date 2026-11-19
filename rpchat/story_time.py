"""Story time progression logic and scene judgment."""

import copy
import json
import re

from rpchat.util import _as_int, _safe_int_env

# 作中時間の進行（サーバーが「番」、AIが「判断と描写」）
TIME_FIRST_TURNS = max(1, _safe_int_env("RPCHAT_TIME_FIRST", 30) or 30)
TIME_EVERY_TURNS = max(1, _safe_int_env("RPCHAT_TIME_EVERY", 20) or 20)
_esc_env = _safe_int_env("RPCHAT_TIME_ESCALATE_AFTER", 3)
TIME_ESCALATE_AFTER = max(0, 3 if _esc_env is None else _esc_env)
TIME_FORCE_AFTER = max(1, _safe_int_env("RPCHAT_TIME_FORCE_AFTER", 10) or 10)

VALID_PERIODS = ("早朝", "午前", "昼", "夕方", "夜", "深夜")
PERIODS = VALID_PERIODS

_PERIOD_ALIASES = {
    "朝": "早朝", "あさ": "早朝", "朝方": "早朝", "明け方": "早朝", "夜明け": "早朝",
    "午前": "午前",
    "昼": "昼", "午後": "昼", "昼下がり": "昼", "昼過ぎ": "昼", "正午": "昼",
    "夕方": "夕方", "夕暮れ": "夕方", "夕刻": "夕方", "日暮れ": "夕方", "放課後": "夕方",
    "夜": "夜", "晩": "夜", "夜間": "夜",
    "深夜": "深夜", "夜中": "深夜", "真夜中": "深夜", "未明": "深夜", "夜更け": "深夜",
}


def period_index(p):
    s = _PERIOD_ALIASES.get(str(p or "").strip(), str(p or "").strip())
    try:
        return PERIODS.index(s)
    except ValueError:
        return -1


def normalize_period(p):
    idx = period_index(p)
    return PERIODS[idx] if idx >= 0 else "午前"


def _safe_day(v, default=1):
    return max(1, _as_int(v, default))


def period_after(day, idx, steps):
    total = int(idx) + int(steps)
    return max(1, int(day) + total // len(PERIODS)), total % len(PERIODS)


def time_tick_info(st, total_turn):
    st = st if isinstance(st, dict) else {}
    total = max(0, _as_int(total_turn, 0))
    # 未来のカーソルで時計が止まらないようにclamp
    last = min(max(0, _as_int(st.get("time_last_turn"), 0)), total)
    threshold = TIME_FIRST_TURNS if last <= 0 else TIME_EVERY_TURNS
    overdue = total - last - threshold
    return overdue >= 0, overdue, threshold


_TIME_CUE_RE = re.compile(
    r"(翌朝|翌日|次の日|明くる日|翌晩|翌週|翌月|数日後|数時間後|数週間後|何週間後|二日後|数ヶ月後|数カ月後|数か月後|何ヶ月後|何カ月後|何か月後|一週間後|"
    r"三日後|朝まで|夜通し|一晩中|一晩|徹夜|寝落ち|おやすみ|寝よう|眠ろう|就寝|床に就|ベッドに入|眠りにつ|一眠り|仮眠|"
    r"(?:(?:もう|そろそろ|今日は|今夜は|先に|お先に)寝る(?:よ|ね|わ|ぞ|ぜ|かな)?|寝る(?:よ|ね|わ|ぞ|ぜ|かな)|寝ます|眠る(?:よ|ね|わ))|"
    r"目が覚め|起きたら|日が暮れ|日が昇|夜になった|朝になった|夕方になった|夜が明|朝が来|"
    r"その夜|その日の夜|何日か後|時間を飛ば|時間が飛|気づけば|"
    r"next morning|next day|the next day|morning came|night fell|hours later|days later)",
    re.IGNORECASE,
)
_TIME_MENTION_RE = re.compile(
    r"(翌朝|翌日|次の日|明くる日|翌晩|その夜|朝になった|夜になった|夕方になった|"
    r"日が暮れ|日が昇|目が覚め|朝が来|夜が明け|一晩|夜通し|数日後|一週間後|"
    r"next morning|next day|morning came|night fell)"
)
_TIME_ASK_RE = re.compile(r"(今何時|何時|時刻|時間帯|朝か夜|昼か夜)")


def time_skip_cued(text):
    return bool(_TIME_CUE_RE.search(str(text or "")))


def time_asked(text):
    return bool(_TIME_ASK_RE.search(str(text or "")))


def time_transition_mentions(text):
    m = _TIME_MENTION_RE.search(str(text or ""))
    return m.group(1) if m else ""


_SEX_ACTION_TAGS = {
    "penetration", "vaginal penetration", "anal penetration", "sex", "vaginal", "anal",
    "fellatio", "deepthroat", "blowjob", "irrumatio", "cunnilingus", "paizuri", "handjob",
    "fingering", "anal fingering", "masturbation", "orgasm", "creampie", "anal creampie",
    "cum in mouth", "cum on body", "squirting", "nipple stimulation",
    "double penetration", "group sex", "intercourse", "vaginal intercourse", "anal intercourse",
    "oral sex", "titfuck", "footjob", "tribadism", "scissoring", "pegging", "face sitting", "facesitting",
}
_COMBAT_ACTION_TAGS = {
    "fighting", "combat", "battle", "attacking", "slashing", "shooting", "firing",
    "aiming", "holding weapon", "spell casting", "casting spell", "punching", "kicking",
    "sword fight", "dodging", "blocking",
}
_SEX_JA_RE = re.compile(
    r"(挿入|結合|ピストン|抽送|フェラ|口淫|咥え|しゃぶ|クンニ|手コキ|パイズリ|"
    r"絶頂|イッて|射精|中出し|種付け|突かれ|貫かれ|喘ぎ|自慰|オナニー|潮吹き|愛撫|交わり|絡み合|"
    r"おまんこ|マンコ|アナル|ケツ|膣|尻穴|ペニス|チンポ|肉棒|"
    r"行為中|セックス中|エッチ中)"
)
_COMBAT_RE = re.compile(
    r"(戦闘|交戦|戦っている|戦って|斬り合|斬りかか|撃ち合|殴り合|銃撃|射撃|"
    r"爆発|爆炎|詠唱|魔法を放|盾を構|剣を構|武器を構|対峙|敵[がとに]|魔物|モンスター|"
    r"襲いかか|襲撃|悲鳴|絶叫|血しぶき|倒れ伏|追い詰め|逃亡|逃げ回|防御|避けた|かわした|防戦)"
)

_NO_PEN_SET = {"なし", "無し", "特になし", "無", "none", "null", "-", "0", "false", "[]"}


def scene_is_showpiece(states):
    """性的行為・戦闘の最中か。stateだけを見る（本文は見ない＝余韻を塞がない）。
    body_fluids / equipped_items / ahegao も見ない（余韻を塞がないため）。"""
    if isinstance(states, dict):
        states = [states]
    for ch in (states or []):
        if not isinstance(ch, dict):
            continue
        pen_raw = ch.get("penetration")
        if isinstance(pen_raw, (list, tuple)):
            pen_raw = " ".join(str(x) for x in pen_raw)
        pen = str(pen_raw or "").strip().lower()
        if pen and pen not in _NO_PEN_SET:
            return True

        acts = ch.get("action")
        if isinstance(acts, str):
            acts = [acts]
        elif isinstance(acts, dict):
            acts = [acts]
        elif not isinstance(acts, (list, tuple)):
            acts = []
        for a in acts:
            for tok in re.split(r"[,/;]", str(a).lower()):
                t = tok.strip()
                if t in _SEX_ACTION_TAGS or t in _COMBAT_ACTION_TAGS:
                    return True
                words = set(t.split())
                if words & {
                    "penetration", "intercourse", "fellatio", "blowjob", "cunnilingus",
                    "paizuri", "titfuck", "handjob", "footjob", "fingering", "masturbation",
                    "deepthroat", "orgasm", "creampie", "squirting", "tribadism", "pegging",
                    "fighting", "slashing", "shooting",
                }:
                    return True
                if "sex" in words and not (words & {"unisex", "sexy", "homosexual"}):
                    return True

        sit = ch.get("situation")
        if isinstance(sit, str):
            sit = [sit]
        elif isinstance(sit, dict):
            sit = [sit]
        elif not isinstance(sit, (list, tuple)):
            sit = []
        s_text = " ".join(str(x) for x in (list(acts) + list(sit)))
        if _SEX_JA_RE.search(s_text) or _COMBAT_RE.search(s_text):
            return True
    return False


def state_prompt(states):
    if not states:
        return ""
    if isinstance(states, list):
        return json.dumps(states, ensure_ascii=False)
    return str(states)


def apply_reported_time(st, rep_el, *, user_text, gate_states, total_turn):
    """AIのelapsed_timeを検証。戻り値: (適用したか, 理由)"""
    if not isinstance(rep_el, dict):
        return False, "invalid_reported_time"
    st = st if isinstance(st, dict) else {}
    cur = st.get("elapsed_time") if isinstance(st.get("elapsed_time"), dict) else {}
    cur_day = _safe_day(cur.get("day"))
    cur_idx = period_index(cur.get("period"))
    if cur_idx < 0:
        cur_idx = period_index("午前")

    rep_idx = period_index(rep_el.get("period"))
    if rep_idx < 0:
        return False, "unknown_period"

    cue = time_skip_cued(user_text)

    raw_rep_day = _safe_day(rep_el.get("day"), default=cur_day) if rep_el.get("day") is not None else None
    if raw_rep_day is None or raw_rep_day == cur_day:
        if rep_idx < cur_idx:
            if cue or (cur_idx == len(PERIODS) - 1 and rep_idx == 0):
                default_day = cur_day + 1
            else:
                default_day = cur_day
        elif rep_idx == cur_idx and cue and re.search(r"(翌日|次の日|明くる日|数日後|一週間後|next day|the next day)", str(user_text or "")):
            default_day = cur_day + 1
        else:
            default_day = cur_day
    else:
        default_day = raw_rep_day

    rep_day = _safe_day(default_day, default=cur_day)
    step = (rep_day - cur_day) * len(PERIODS) + (rep_idx - cur_idx)
    if step == 0:
        return False, "hold"
    if step < 0:
        return False, "backwards"

    due, _, _ = time_tick_info(st, total_turn)
    if not due and not cue:
        return False, "not_due"

    # ★AIの自力ジャンプは時計を動かさない（+1の通常進行だけ許可）
    if not cue and step > 1:
        return False, "self_jump"
    if not cue and scene_is_showpiece(gate_states):
        return False, "showpiece"

    step = min(step, 30 * len(PERIODS)) if cue else step
    new_day, new_idx = period_after(cur_day, cur_idx, step)
    st["elapsed_time"] = {"day": new_day, "period": PERIODS[new_idx]}
    st["turns_in_period"] = 0
    st["time_last_turn"] = max(0, _as_int(total_turn, 0))
    st["time_silent_until_turn"] = 0
    return True, ""


def force_time_tick(st, total_turn):
    """長期保留時の無言/事後描写用の進行。次ターンで silent か narrate を選ぶ"""
    st = st if isinstance(st, dict) else {}
    cur = st.get("elapsed_time") if isinstance(st.get("elapsed_time"), dict) else {}
    cur_day = _safe_day(cur.get("day"))
    cur_idx = period_index(cur.get("period"))
    if cur_idx < 0:
        cur_idx = period_index("午前")
    new_day, new_idx = period_after(cur_day, cur_idx, 1)
    old = copy.deepcopy(st.get("elapsed_time"))
    st["elapsed_time"] = {"day": new_day, "period": PERIODS[new_idx]}
    st["turns_in_period"] = 0
    st["time_last_turn"] = max(0, _as_int(total_turn, 0))
    # 「強制の次ターン」マーカー。次ターンのsceneでsilent/narrateを決める
    st["time_silent_until_turn"] = max(0, _as_int(total_turn, 0)) + 1
    return old, st["elapsed_time"]


def time_tick_section(st, overdue):
    cur = st.get("elapsed_time") if isinstance(st.get("elapsed_time"), dict) else {}
    cur_day = _safe_day(cur.get("day"))
    cur_idx = period_index(cur.get("period"))
    if cur_idx < 0:
        cur_idx = period_index("午前")
    nd, ni = period_after(cur_day, cur_idx, 1)
    target = json.dumps({"day": nd, "period": PERIODS[ni]}, ensure_ascii=False)
    text = (
        f"現在: {cur_day}日目・{PERIODS[cur_idx]} ／ 次: {nd}日目・{PERIODS[ni]}\n"
        "・会話・移動・休息・場面転換の区切りなら、本文の早い段階で時間帯が進んだことを"
        "地の文（光・空の色・気温・空気・音・体の感覚）で示す。\n"
        "・進めるのは1段階だけ。日付を飛ばさない（「翌朝」「翌日」にしない）。\n"
        "・時刻をセリフで言わない。「夕方になった」のような説明調も避ける。\n"
        "・戦闘・性行為・絶頂・その直前の見せ場の最中なら進めない。"
        "elapsed_time を省略し、本文でも時間に触れない。\n"
        f"・進めたら elapsed_time に {target} を入れ、"
        "state の background（day/night/evening/sunset）と place も現在に合わせる。\n"
        "・AIメモに古い時刻があっても、この現在時刻を優先する。"
    )
    if overdue >= TIME_ESCALATE_AFTER:
        text += f"\n・すでに{overdue}ターン保留。見せ場の最中でなければ、このターン必ず進める。"
    return {"label": "【時間経過】今ターンが進める番", "text": text}


def time_cue_section(st):
    cur = st.get("elapsed_time") if isinstance(st.get("elapsed_time"), dict) else {}
    cur_day = _safe_day(cur.get("day"))
    cur_idx = period_index(cur.get("period"))
    if cur_idx < 0:
        cur_idx = period_index("午前")
    text = (
        f"進行前: {cur_day}日目・{PERIODS[cur_idx]}\n"
        "・ユーザー入力に時間経過（睡眠・翌朝・数日後など）の指示がある。\n"
        "・指示に合わせて時間を進め、変更後の day と period（早朝/午前/昼/夕方/夜/深夜）を"
        " elapsed_time に必ず出力する。\n"
        "・state の background と place も新しい時間帯に合わせる。"
    )
    return {"label": "【時間経過】ユーザーの指示", "text": text}


def time_after_force_section(st, is_showpiece):
    cur = st.get("elapsed_time") if isinstance(st.get("elapsed_time"), dict) else {}
    cur_day = _safe_day(cur.get("day"))
    cur_idx = period_index(cur.get("period"))
    if cur_idx < 0:
        cur_idx = period_index("午前")
    period_name = PERIODS[cur_idx]
    if is_showpiece:
        return {
            "label": "【作中時間】",
            "text": (
                f"現在: {cur_day}日目・{period_name}\n"
                "・行為・戦闘の継続中。時間の経過には一切触れず、そのまま場面を続ける。\n"
                "・state の background は現在の時間帯に合わせる。"
            ),
        }
    return {
        "label": "【時間経過】余韻・区切り",
        "text": (
            f"現在: {cur_day}日目・{period_name}（直前の場面のあいだに時間が進んでいる）\n"
            "・行為や戦闘が落ち着いた流れなら、いつの間にかこの時間帯になっていたことを"
            "地の文（窓の外の光・空気・静けさなど）で一言だけ自然に示す。\n"
            "・まだ行為や戦闘を続けるなら時間には触れない。\n"
            "・時計はすでに進んでいるため elapsed_time は出力不要。"
            "state の background は現在の時間帯に合わせる。"
        ),
    }


def time_question_section(st):
    cur = st.get("elapsed_time") if isinstance(st.get("elapsed_time"), dict) else {}
    cur_day = _safe_day(cur.get("day"))
    cur_idx = period_index(cur.get("period"))
    if cur_idx < 0:
        cur_idx = period_index("午前")
    return {
        "label": "【現在の作中時間】",
        "text": f"{cur_day}日目・{PERIODS[cur_idx]}（時間自体は進めない。elapsed_time は出力しない）",
    }


def infer_initial_period(text):
    if not text:
        return "午前"
    if any(k in text for k in ("深夜", "真夜中", "ベッドの中", "草木も眠る", "午前2時", "午前3時", "未明")):
        return "深夜"
    if any(k in text for k in ("夜", "帰宅", "夕食", "晩御飯", "晩ごはん", "お風呂", "入浴", "夜道", "夜更かし")):
        return "夜"
    if any(k in text for k in ("放課後", "夕方", "黄昏", "夕焼け", "下校", "終礼", "夕暮れ")):
        return "夕方"
    if any(k in text for k in ("昼休み", "昼食", "お弁当", "ランチ", "正午", "お昼", "午後", "5時間目", "6時間目", "昼下がり")):
        return "昼"
    if any(k in text for k in ("早朝", "朝焼け", "夜明け", "目覚まし", "起床", "朝練", "朝の", "朝 ")):
        return "早朝"
    if any(k in text for k in ("朝", "登校", "ホームルーム", "午前", "1時間目", "始業")):
        return "午前"
    return "午前"
