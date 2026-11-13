# -*- coding: utf-8 -*-
"""コンテキスト検証ツール（サイズ・凍結セクションSHA1・切り捨てリスクの検証）"""
import os
import sys
import json
import hashlib
import tempfile
import shutil

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DATA = os.path.join(ROOT_DIR, "data")
BASELINE_PATH = os.environ.get("RPCHAT_BASELINE") or os.path.join(ROOT_DIR, "tools", "baseline.json")

# §8.1 副作用ガード（import 前に一時ディレクトリ設定）
_tmp = tempfile.mkdtemp(prefix="rpchat_check_")
os.environ["RPCHAT_DATA"] = _tmp
os.environ["RPCHAT_PERF_LOG"] = "0"

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server

# 副作用4を切る
server.sync_agents = lambda s: False

# 一時ディレクトリに既存のセッション・RAG・状態データをコピー（読み取り専用元データとして利用）
for sub in ("sessions", "rag", "states", "sent"):
    src_sub = os.path.join(REAL_DATA, sub)
    dst_sub = os.path.join(_tmp, sub)
    if os.path.exists(src_sub):
        shutil.copytree(src_sub, dst_sub, dirs_exist_ok=True)


def sha1_text(t):
    return hashlib.sha1((t or "").encode("utf-8")).hexdigest()


def inspect_memo(sid):
    try:
        s = server.load(sid)
    except Exception:
        return None
    memo = s.get("setting", {}).get("memo") or ""
    res = {"sid": sid}
    res.update(server.memo_stats(memo))
    return res


def inspect_channel(sid):
    try:
        s = server.load(sid)
    except Exception:
        return None
    memo = s.get("setting", {}).get("memo") or ""
    mb = len(memo.encode("utf-8"))
    msgs = s.get("messages", [])
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    if not user_msgs:
        return None
    target_m = user_msgs[-1]
    idx = msgs.index(target_m)
    ide = s.get("setting", {}).get("ide_session")
    if not ide:
        return None

    # target_m のターンを再現するため、target_m より前の発言を送信済みとしてモック
    prior_sent = server.read_sent(sid)
    prior_sent["ide"] = ide
    prior_sent["reset"] = False
    prior_sent["why"] = ""
    prior_sent["msgs"] = [server.msg_sig(x) for x in msgs[:idx]]
    if "sigs" in prior_sent and isinstance(prior_sent["sigs"], dict):
        if "memo" in prior_sent["sigs"]:
            prior_sent["sigs"]["memo"] = server.sig_of(memo)
    if "card_sig" in prior_sent and isinstance(prior_sent["card_sig"], dict):
        if "output.md" in prior_sent["card_sig"]:
            prior_sent["card_sig"]["output.md"] = server.sig_of(server.output_text(s.get("setting", {}), is_diff=True))
    server.write_sent(sid, prior_sent)

    max_payload_bytes = 0
    frozen = {}
    spec_text = ""
    spec_json_ok = False
    last_ctx = None
    all_cards_found = {"instructions": False, "chara": False, "output": False}

    for phase in (0, 1, 2):
        prior_sent["card_phase"] = phase
        server.write_sent(sid, prior_sent)
        ctx = server.build_context(s, target_m)
        last_ctx = ctx

        body_raw = "".join(f"\n--- {sec.get('label', '')} ---\n{sec.get('text', '')}\n" for sec in ctx.get("sections", []))
        if ctx.get("same"):
            body_raw += "\n（前回と同じなので省略: " + "、".join(ctx["same"]) + "）\n"

        rules_text = (ctx.get("rules") or {}).get("text", "")
        rules_b = len(f"\n--- {(ctx.get('rules') or {}).get('label', '')} ---\n{rules_text}\n".encode("utf-8")) if rules_text else 0
        compact = server.compact_on()
        boiler_b = server.BOILER_COMPACT_BYTES if compact else server.BOILER_FULL_BYTES
        p_bytes = len(body_raw.encode("utf-8")) + boiler_b + rules_b
        if p_bytes > max_payload_bytes:
            max_payload_bytes = p_bytes

        # 凍結セクションの抽出
        for sec in ctx.get("sections", []):
            lbl = sec.get("label", "")
            if "指示" in lbl:
                all_cards_found["instructions"] = True
            if "キャラクター" in lbl:
                all_cards_found["chara"] = True
            if "応答の書き方" in lbl:
                all_cards_found["output"] = True

            for key in ("指示", "キャラクター", "ユーザーの発言", "作中経過時間"):
                if key in lbl and key not in frozen:
                    frozen[key] = {
                        "sha1": sha1_text(sec.get("text", "")),
                        "chars": len(sec.get("text", "")),
                    }

        # spec (応答の書き方) の検査
        spec_sec = next((sec for sec in ctx.get("sections", []) if "応答の書き方" in sec.get("label", "")), None)
        if spec_sec:
            spec_text = spec_sec.get("text", "")
            if "```json\n" in spec_text and "\n```" in spec_text:
                try:
                    start_idx = spec_text.find("```json\n") + 8
                    end_idx = spec_text.find("\n```", start_idx)
                    json.loads(spec_text[start_idx:end_idx])
                    spec_json_ok = True
                except Exception:
                    spec_json_ok = False

    if not spec_text:
        # memo_due や余白制約で定期カード再送が見送られた場合は、チャンネル設定から生成される spec を検証
        spec_text = server.output_text(s.get("setting", {}), is_diff=True)
        if "```json\n" in spec_text and "\n```" in spec_text:
            try:
                start_idx = spec_text.find("```json\n") + 8
                end_idx = spec_text.find("\n```", start_idx)
                json.loads(spec_text[start_idx:end_idx])
                spec_json_ok = True
            except Exception:
                spec_json_ok = False

    return {
        "sid": sid,
        "title": s.get("setting", {}).get("title") or sid,
        "mode": last_ctx.get("mode") if last_ctx else None,
        "payload_bytes": max_payload_bytes,
        "compact": server.compact_on(),
        "frozen": frozen,
        "spec_json_ok": spec_json_ok,
        "spec_chars": len(spec_text),
        "spec_has_required_words": all(w in spec_text for w in [
            "催眠", "一人称視点", "pose と expression は毎回必ず", "image.characters", "memories", "reply_to"
        ]),
        "spilled": max_payload_bytes > server.LONG_BYTES,
        "all_cards_found": all_cards_found,
        "memo_bytes": mb,
        "memo_over_target": mb > server.MEMO_TARGET_BYTES,
        "memo_over_budget": mb > server.MEMO_HARD_BYTES,
    }


def save_baseline():
    results = {}
    sess_dir = os.path.join(REAL_DATA, "sessions")
    if not os.path.exists(sess_dir):
        print(f"No session directory found at {sess_dir}")
        return
    for fname in os.listdir(sess_dir):
        if fname.endswith(".json"):
            sid = fname[:-5]
            info = inspect_channel(sid)
            if info:
                results[sid] = info
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Saved baseline for {len(results)} channels to {BASELINE_PATH}")


def check_baseline():
    if not os.path.exists(BASELINE_PATH):
        print(f"Baseline not found at {BASELINE_PATH}. Run --save first.")
        sys.exit(1)
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    all_ok = True
    compact = server.compact_on()

    if not compact:
        print("[FAIL] サイズ未減少: RPCHAT_COMPACT=0 のためロールバック状態です")
        sys.exit(1)

    for sid, base in baseline.items():
        if sid.startswith("__"):
            continue
        curr = inspect_channel(sid)
        if not curr:
            continue

        # 1. 凍結セクションのSHA1一致確認
        for k, f_base in base.get("frozen", {}).items():
            f_curr = curr.get("frozen", {}).get(k)
            if not f_curr or f_curr["sha1"] != f_base["sha1"]:
                print(f"[{sid}] 凍結セクション不一致 ({k}): base={f_base['sha1']} curr={f_curr.get('sha1') if f_curr else None}")
                all_ok = False

        # 2. mode 確認
        if curr["mode"] != base["mode"]:
            print(f"[{sid}] mode 不一致: base={base['mode']} curr={curr['mode']}")
            all_ok = False

        # 3. サイズ確認（安全目標: payload_bytes <= 9400、またはラピ等で減少）
        if curr["payload_bytes"] > 9400 and not curr["spilled"]:
            print(f"[{sid}] 警告: payload_bytes ({curr['payload_bytes']}) > 9400")
        if curr["payload_bytes"] > base["payload_bytes"]:
            print(f"[{sid}] サイズ増加: base={base['payload_bytes']} curr={curr['payload_bytes']}")
            all_ok = False
        if curr.get("memo_bytes", 0) > 1500:
            print(f"[{sid}] 警告: memo_bytes ({curr['memo_bytes']}) > 1500")

        # 4. spec 確認
        if not curr["spec_json_ok"]:
            print(f"[{sid}] spec のサンプルJSONがパース不可")
            all_ok = False
        if not curr["spec_has_required_words"]:
            print(f"[{sid}] spec に必須単語が欠落しています")
            all_ok = False

    # 5. 非IDEを含む全チャンネルのmemo監視 (inspect_memo)
    sess_dir = os.path.join(REAL_DATA, "sessions")
    if os.path.exists(sess_dir):
        for fname in os.listdir(sess_dir):
            if fname.endswith(".json"):
                sid = fname[:-5]
                m_info = inspect_memo(sid)
                if not m_info:
                    continue
                mb = m_info.get("memo_bytes", 0)
                if mb > server.MEMO_HARD_BYTES:
                    print(f"[{sid}] ハード上限超過: memo_bytes ({mb}) > {server.MEMO_HARD_BYTES}")
                    all_ok = False
                elif mb > 1500:
                    print(f"[{sid}] 警告: memo_bytes ({mb}) > 1500")

    if all_ok:
        print("ALL CHECKS PASSED")
        sys.exit(0)
    else:
        print("SOME CHECKS FAILED")
        sys.exit(1)


def main():
    try:
        if "--save" in sys.argv:
            save_baseline()
        elif "--check" in sys.argv:
            check_baseline()
        else:
            print("Usage: python tools/check_context.py [--save | --check]")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
