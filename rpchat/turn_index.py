# -*- coding: utf-8 -*-
"""通算ユーザーターン番号および出所情報の共通計算モジュール。

標準ライブラリと rpchat.util のみを用い、重い依存（ONNX, memory_store 等）を持たない。
"""
from __future__ import annotations

import copy
import logging
from rpchat.util import _as_int

logger = logging.getLogger(__name__)

# 不整合ログの抑制用。(total_turns, file_total_user) の値ペア単位でプロセス内1回だけ警告する。
_warned_mismatches = set()
_MAX_WARNED_MISMATCHES = 200


def _warn_mismatch_once(total_turns, file_total_user):
    """異常メタデータ（total_turns < file_total_user）を初回のみ警告ログに出力する。

    セッションIDを持たない共通関数から呼ばれるため、抑制の単位は
    (total_turns, file_total_user) の値ペア（プロセス全体）である。
    目的は検出可能性の確保とログ氾濫の防止であり、同じ不整合を2回以上警告しない。
    """
    if len(_warned_mismatches) >= _MAX_WARNED_MISMATCHES:
        _warned_mismatches.clear()
    try:
        key = (int(total_turns), int(file_total_user))
    except (TypeError, ValueError):
        key = (str(total_turns), str(file_total_user))
    if key in _warned_mismatches:
        return
    _warned_mismatches.add(key)
    logger.warning(
        "calc_turn_idx metadata mismatch: total_turns (%s) < file_total_user (%s). "
        "Falling back to relative index.",
        total_turns, file_total_user
    )


def calc_turn_idx(msgs, i, total_turns=None):
    """メッセージリスト msgs 内のインデックス i における通算ユーザーターン番号を計算する。

    ローテーション後のアクティブセッションやアーカイブ snapshot でも正確な通算ターンを返す。
    先行する user 発言が無い場合（先頭アシスタント発言等）は 0 を返す。
    異常メタデータ（total_turns < file_total_user）時はファイル内相対番号にフォールバックする。
    """
    if not msgs or i < 0:
        return 0
    # msgs[:i + 1] 内の user 発言数
    idx_in_file = sum(1 for x in msgs[:i + 1] if isinstance(x, dict) and x.get("role") == "user")
    if not idx_in_file:
        return 0
    t_val = _as_int(total_turns, default=0) if total_turns is not None else 0
    if t_val > 0:
        file_total_user = sum(1 for x in msgs if isinstance(x, dict) and x.get("role") == "user")
        if file_total_user > 0 and t_val >= file_total_user:
            return t_val - (file_total_user - idx_in_file)
        elif file_total_user > 0 and t_val < file_total_user:
            # 異常メタデータ（total_turns < file_total_user）: 相対番号にフォールバック
            _warn_mismatch_once(t_val, file_total_user)
            return idx_in_file
    return idx_in_file


def turn_index_base(setting, file_user_turns, rotation_period):
    """通算ターン番号の基準。total_turns が未設定(0)の旧セッションだけ回転回数から補う。"""
    st = setting if isinstance(setting, dict) else {}
    total = _as_int(st.get("total_turns"), default=0)
    if total > 0:
        return total
    rot_cnt = _as_int(st.get("rotation_count"), default=0)
    rot_period = _as_int(rotation_period, default=20)
    f_turns = _as_int(file_user_turns, default=0)
    return (rot_cnt * rot_period) + f_turns


def resolve_message_elapsed_time(msgs, idx):
    """メッセージ idx の作中時刻を優先順（自前 -> snapshot -> 前方直近）に従って解決する。

    優先順:
    1. 対象メッセージの elapsed_time
    2. 対象メッセージの snapshot_elapsed_time
    3. 対象メッセージの elapsed_snapshot
    4. それより前のメッセージの同上のいずれか（直近の有効値）
    5. どれも無ければ None
    """
    if not msgs or idx < 0:
        return None
    limit = min(idx, len(msgs) - 1)
    for i in range(limit, -1, -1):
        msg = msgs[i]
        if not isinstance(msg, dict):
            continue
        el = msg.get("elapsed_time") or msg.get("snapshot_elapsed_time") or msg.get("elapsed_snapshot")
        if el and isinstance(el, dict):
            return copy.deepcopy(el)
    return None


def build_source_index(messages, setting, rotation_period):
    """セッションの全 messages を先頭から走査し、各メッセージの通算ターンおよび作中時刻マップを構築する。

    時刻の前方向伝播（直近の有効なスナップショットの引き継ぎ）を一度に行い、結果を焼き込む。
    O(n) で走査し、各メッセージの通算ターンを効率的に算出する。
    戻り値:
        dict: message_id (str) -> entry, id(message) (int) -> entry
        entry: {"index": int, "turn_idx": int, "mid": str|None, "elapsed_time": dict|None}
    """
    msgs = messages if isinstance(messages, list) else []
    st = setting if isinstance(setting, dict) else {}
    file_total_user = sum(1 for x in msgs if isinstance(x, dict) and x.get("role") == "user")
    base_total = turn_index_base(st, file_total_user, rotation_period)

    source_index = {}
    last_valid_elapsed = None
    user_cnt = 0

    for idx, msg in enumerate(msgs):
        if not isinstance(msg, dict):
            continue

        if msg.get("role") == "user":
            user_cnt += 1

        # 作中時刻の自前値検査 -> 更新
        el = msg.get("elapsed_time") or msg.get("snapshot_elapsed_time") or msg.get("elapsed_snapshot")
        if el and isinstance(el, dict):
            last_valid_elapsed = copy.deepcopy(el)

        # 通算ターン計算 (O(1))
        if user_cnt == 0:
            t_idx = 0
        elif base_total > 0 and base_total >= file_total_user:
            t_idx = base_total - (file_total_user - user_cnt)
        elif base_total > 0 and base_total < file_total_user:
            _warn_mismatch_once(base_total, file_total_user)
            t_idx = user_cnt
        else:
            t_idx = user_cnt

        raw_mid = msg.get("id")
        m_id_str = str(raw_mid).strip() if (raw_mid is not None and str(raw_mid).strip()) else None
        entry = {
            "index": idx,
            "turn_idx": t_idx,
            "mid": m_id_str,
            "elapsed_time": copy.deepcopy(last_valid_elapsed) if last_valid_elapsed else None,
        }

        if m_id_str:
            source_index[m_id_str] = entry
        source_index[id(msg)] = entry

    return source_index


def resolve_source(source_index, message_or_id):
    """source_index からメッセージIDまたはメッセージオブジェクトの出所情報を引く。

    IDを持たないメッセージは配列上の同一性（id(msg)）で解決し、
    内容一致による推測・誤帰属は行わない（§6.3）。
    同一性でも解決できない場合（別インスタンスのコピー等）は None を返し、
    呼び出し元は出所不明としてその項目を保存対象から除外する。
    """
    if not source_index or not isinstance(source_index, dict) or message_or_id is None:
        return None
    if isinstance(message_or_id, (str, int)):
        mid = str(message_or_id).strip()
        return source_index.get(mid)
    if isinstance(message_or_id, dict):
        m_id = message_or_id.get("id")
        if m_id is not None:
            mid_str = str(m_id).strip()
            if mid_str and mid_str in source_index:
                return source_index[mid_str]
        obj_id = id(message_or_id)
        if obj_id in source_index:
            return source_index[obj_id]
    return None
