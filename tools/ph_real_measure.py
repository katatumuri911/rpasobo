# -*- coding: utf-8 -*-
"""実チャンネルで入力プレースホルダ変換の実測を行う（⑥）。

各ターン: POST /api/send → セッションが更新されるまでポーリング → 結果と ph.log を突き合わせる。
使い方: python tools/ph_real_measure.py [--sid SID] [--n 10] [--skip 0]
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

BASE = "http://127.0.0.1:8200"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTEST = os.path.join(ROOT, "data", "attest", "ph_real")

UTTERANCES = [
    "たっぷりと大人のキスをしてユウカをその気にさせる",
    "スカートの中に手を入れて、ゆっくりと太ももを撫で上げる",
    "そのまま下着の上から割れ目をなぞって、濡れ具合を確かめる",
    "下着を横にずらして、指を一本ゆっくりと挿し入れる",
    "クリトリスを親指で押しつぶすように円を描いて刺激する",
    "服を脱がせて、胸を揉みながら乳首を舌先で転がす",
    "太ももを開かせて、顔を近づけて舐め上げる",
    "自分のものを取り出して、入り口に押し当てて焦らす",
    "一気に奥まで貫いて、ゆっくりと腰を動かし始める",
    "中に出すまで激しく突いて、耳元で好きだと囁く",
]


def post(path, obj, timeout=60):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_session(sid):
    for _ in range(6):
        try:
            with open(os.path.join(ROOT, "data", "sessions", f"{sid}.json"), encoding="utf-8-sig") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            time.sleep(0.3)
    return {"messages": []}


def wait_turn(sid, n_before, timeout=300):
    """user+assistant が増えるか、user が error になるまで待つ"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = load_session(sid)
        msgs = s.get("messages") or []
        if len(msgs) <= n_before:
            time.sleep(2)
            continue
        last = msgs[-1]
        if last.get("role") == "assistant":
            return "ok", time.time() - t0, msgs
        if last.get("role") == "user" and last.get("status") == "error":
            return "error", time.time() - t0, msgs
        time.sleep(2)
    return "timeout", time.time() - t0, load_session(sid).get("messages") or []


def ph_rows(sid, after_iso):
    path = os.path.join(ROOT, "data", "ph.log")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("sid") == sid and str(r.get("ts") or "") >= after_iso:
                rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid", default="cfa6fdd2fa")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    os.makedirs(ATTEST, exist_ok=True)
    st = (load_session(args.sid).get("setting") or {})
    print(f"sid={args.sid} provider={st.get('ai_provider')} model={st.get('vertex_model') or st.get('google_model')} "
          f"ph={st.get('google_body_ph', '(既定True)')} cache={st.get('google_fetch_cache')}")
    print("")

    results = []
    for i, text in enumerate(UTTERANCES[args.skip:args.skip + args.n], start=args.skip + 1):
        s = load_session(args.sid)
        n_before = len(s.get("messages") or [])
        t_send = time.time()
        t_send_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        try:
            r = post("/api/send", {"sid": args.sid, "text": text})
        except Exception as e:
            print(f"#{i:2d} POST失敗: {e}")
            results.append({"i": i, "result": "post_failed", "sec": 0, "len": 0, "ph": None})
            continue
        if r.get("error"):
            print(f"#{i:2d} APIエラー: {r['error']}")
            results.append({"i": i, "result": "api_error", "sec": 0, "len": 0, "ph": None})
            continue
        result, sec, msgs = wait_turn(args.sid, n_before, timeout=args.timeout)
        body = ""
        if result == "ok":
            body = msgs[-1].get("text") or ""
            with open(os.path.join(ATTEST, f"{args.tag}{i:02d}.txt"), "w", encoding="utf-8") as f:
                f.write(text + "\n\n----\n\n" + body)
        rows = ph_rows(args.sid, t_send_iso)
        ph = rows[-1] if rows else None
        results.append({"i": i, "result": result, "sec": sec, "len": len(body), "ph": ph,
                        "err": (msgs[-1].get("error") if result != "ok" and msgs else "")})
        phs = (f"applied={ph['applied']} restored={ph['restored']} left={ph['left']} "
               f"{ph['len_before']}→{ph['len_after']}字 {ph['ms']}ms") if ph else "ph行なし"
        print(f"#{i:2d} {result:7s} {sec:6.1f}s 本文{len(body):5d}字 | {phs}")
        time.sleep(2)

    ok = [r for r in results if r["result"] == "ok"]
    print("\n=== 集計 ===")
    print(f"通過 {len(ok)}/{len(results)}")
    if ok:
        secs = sorted(r["sec"] for r in ok)
        med = secs[len(secs) // 2]
        print(f"所要 中央{med:.1f}s / 最小{secs[0]:.1f}s / 最大{secs[-1]:.1f}s")
        lens = sorted(r["len"] for r in ok)
        print(f"本文長 中央{lens[len(lens) // 2]}字")
    lefts = [r["ph"]["left"] for r in results if r["ph"]]
    if lefts:
        print(f"記号残り合計 {sum(lefts)}")
    json.dump([{k: v for k, v in r.items()} for r in results],
              open(os.path.join(ATTEST, f"_summary{args.tag}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
