# -*- coding: utf-8 -*-
"""Test A: spec 英語化のオフライン計測（課金ゼロ・実サーバー非接続）。

RPCHAT_DATA を一時ディレクトリに切り、実データをコピーして build_context を回す。
通常 / 濡れ場の 2 パターン × 日本語 spec / 英語 spec で payload を再計算し、
キャラ間引き案も併記する。

使い方:
  PYTHONIOENCODING=utf-8 python tools/measure_spec_en.py [sid]
"""
import json
import os
import shutil
import sys
import tempfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DATA = os.path.join(ROOT_DIR, "data")
TMP = tempfile.mkdtemp(prefix="rpchat_measure_")

os.environ["RPCHAT_DATA"] = TMP
os.environ["RPCHAT_PERF_LOG"] = "0"
os.environ["RPCHAT_COMPACT"] = "1"
sys.path.insert(0, ROOT_DIR)

import server  # noqa: E402
from tools.en_spec_text import en_output_text  # noqa: E402

server.sync_agents = lambda s: False
LONG_TARGET = 9400  # wait_input.py の実効閾値

for sub in ("sessions", "rag", "states", "sent", "decensor.json", "writing_style.md"):
    src = os.path.join(REAL_DATA, sub)
    dst = os.path.join(TMP, sub)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    elif os.path.isfile(src):
        shutil.copy2(src, dst)


def b(s):
    return len((s or "").encode("utf-8"))


def sections_payload(ctx):
    body = "".join(f"\n--- {sec.get('label', '')} ---\n{sec.get('text', '')}\n"
                   for sec in ctx.get("sections", []))
    if ctx.get("same"):
        body += "\n（前回と同じなので省略: " + "、".join(ctx["same"]) + "）\n"
    rules = ctx.get("rules") or {}
    rules_b = b(f"\n--- {rules.get('label', '')} ---\n{rules.get('text', '')}\n") if rules else 0
    boiler = server.BOILER_COMPACT_BYTES if server.compact_on() else server.BOILER_FULL_BYTES
    return b(body) + boiler + rules_b, body


def prepare(sid, normal):
    """session に未送信の新規ユーザー発言を 1 件足し、sent を『その手前まで送信済み』にする。"""
    # 毎回、実データから作り直す（前回の追記・状態差し替えを持ち越さない）
    shutil.copy2(os.path.join(REAL_DATA, "sessions", sid + ".json"),
                 os.path.join(TMP, "sessions", sid + ".json"))
    shutil.copy2(os.path.join(REAL_DATA, "states", sid + ".json"),
                 os.path.join(TMP, "states", sid + ".json"))
    server.session_cache.pop(sid, None)
    server.states_cache.pop(sid, None)
    server.sent_cache.pop(sid, None)
    s = server.load(sid)
    msgs = s["messages"]
    new_id = "nrm" + "0" * 5 if normal else "ero" + "0" * 5
    text = ("「今日は少し冷えますね」と窓の外の雨を見つめる。"
            if normal else "「もっと近くで感じさせて」と腰を引き寄せ、耳元に唇を寄せる。")
    msgs.append({"role": "user", "id": new_id, "text": text, "ts": "2026-11-10T00:30:00"})
    with open(os.path.join(TMP, "sessions", sid + ".json"), "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    server.session_cache.pop(sid, None)

    if normal:
        # 直前の assistant 状態を非エロ状態に差し替え（situation に行為タグを入れない）
        states_path = os.path.join(TMP, "states", sid + ".json")
        states = json.load(open(states_path, encoding="utf-8"))
        last_asst = next(m for m in reversed(msgs[:-1]) if m.get("role") == "assistant")
        cur = states.get(last_asst["id"]) or []
        if cur:
            st0 = dict(cur[0])
            st0["action"] = []
            st0["situation"] = ["リビングで指揮官と向かい合って立っている", "落ち着いた様子"]
            states[last_asst["id"]] = [st0]
            with open(states_path, "w", encoding="utf-8") as f:
                json.dump(states, f, ensure_ascii=False)
            server.states_cache.pop(sid, None)
    return s, msgs[-1], len(msgs) - 1


def measure(sid, normal, english):
    s, m, idx = prepare(sid, normal)
    ide = s["setting"].get("ide_session") or ""
    old = server.read_sent(sid)
    old.update({"ide": ide, "reset": False, "why": "",
                "msgs": [server.msg_sig(x) for x in s["messages"][:idx]]})
    server.write_sent(sid, old)
    server.sent_cache.pop(sid, None)

    orig = server.output_text
    if english:
        server.output_text = lambda st, is_diff=False: (en_output_text(st, True)
                                                        if is_diff else orig(st, False))
    try:
        # 1 回目: parts の sigs を現在の文面で作る
        ctx = server.build_context(s, m)
        sent = server.read_sent(sid)
        sent["msgs"] = [server.msg_sig(x) for x in s["messages"][:idx]]
        server.write_sent(sid, sent)
        # 2 回目: 定常 diff として計測
        ctx = server.build_context(s, m)
    finally:
        server.output_text = orig

    payload, _ = sections_payload(ctx)
    rows = []
    for sec in ctx["sections"]:
        rows.append((sec.get("label", ""), b(sec.get("text", ""))))
    return payload, ctx, rows


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else "95a409720c"  # ラピ
    s = server.load(sid)
    st = s["setting"]
    print(f"channel: {st.get('title')} / {sid} / compact={server.compact_on()} / "
          f"rp_state={st.get('rp_state')} / image_mode={st.get('image_mode')}")
    print(f"JP spec(diff): {b(server.output_text(st, True))}B  "
          f"EN spec(diff): {b(en_output_text(st, True))}B")
    print()

    results = {}
    for normal in (True, False):
        label = "通常" if normal else "濡れ場"
        for english in (False, True):
            tag = "EN" if english else "JP"
            payload, ctx, rows = measure(sid, normal, english)
            results[(label, tag)] = (payload, ctx, rows)
            total_secs = sum(v for _, v in rows)
            rules = ctx.get("rules") or {}
            print(f"=== {label} / {tag}: payload {payload}B (LONG_BYTES={LONG_TARGET})"
                  f" [sec={total_secs} rules_bytes={b(rules.get('text',''))} boiler={server.BOILER_COMPACT_BYTES}]")
            for lbl, size in rows:
                print(f"   {size:6d}  {lbl}")
            if ctx.get("same"):
                print(f"   same: {ctx['same']}")
            print()

    print("=== 判定（ラピ）")
    for label in ("通常", "濡れ場"):
        jp = results[(label, "JP")][0]
        en = results[(label, "EN")][0]
        print(f"{label}: JP {jp}B -> EN {en}B ({(en - jp) / jp * 100:+.1f}%, {en - jp:+d}B), "
              f"9400B まで {en - LONG_TARGET:+d}B")
        for extra in ("chara.md", "chara+instructions"):
            drop = sum(size for lbl, size in results[(label, "EN")][2]
                       if ("キャラクター" in lbl and "chara" in extra)
                       or ("指示" in lbl and "instructions" in extra))
            thin = en - drop
            print(f"   EN + {extra} 間引き: {thin}B ({thin - LONG_TARGET:+d}B vs 9400)")
        jp_thin = jp - sum(size for lbl, size in results[(label, "JP")][2]
                           if "キャラクター" in lbl or "指示" in lbl)
        print(f"   JP + キャラ・指示 間引きのみ: {jp_thin}B ({jp_thin - LONG_TARGET:+d}B vs 9400)")
    print(f"\n(temp: {TMP})")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
