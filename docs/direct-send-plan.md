# 合図へのコンテキスト直送 実装プラン ［v1］

対象: `server.py` / `wait_input.py` / `public/index.html` / `public/app.js`
目的: `run_command`（wait_input.py の起動）と `read_file`（長い ctx の読み込み）を消し、
      1ターンあたり **-4秒（毎回）／-8.5秒（32%のターン）** を狙う。
方針: **フラグOFFで完全に従来動作**。rpc限定。失敗時は従来方式へ自動フォールバック。

> 行番号は執筆時点のもの。別作業でシフトするので**関数名アンカー**で探すこと。

---

## 0. 非目標（やらないこと）

| やらない | 理由 |
|---|---|
| `state` を本文から切り離す（A/B案） | state は本文＋会話の複合。切り出すと必ず本文の後ろに並び、画像生成が15〜20秒遅れる |
| `state` の差分強制（C案） | 効果は平均-2〜4秒で、リスク（書き漏らしが永続）に対して見合わない。今回は触らない |
| `wait_input.py` を全廃 | 選択肢・画像推敲・記憶抽出・統合のサブタスクは従来のまま。まず本文ターンだけ直送 |
| 長い ctx を圧縮する（④） | 直送で全文モードも送れるため不要（§12 の実測） |
| windowモード対応 | キー入力で48KBは打てない。rpc限定（§10） |

---

## 1. 全体像

```
[現状]
/api/send → pending → 合図「 」→ AI が run_command(4s) → wait_input.py
          →（長いと read_file 8.5s）→ write_file(20s) → 取込

[直送]
/api/send → pending → poke_worker
   ├ build_context(record=False)      … 送信直前に毎回
   ├ render_input_text(direct=True)   … show() と同一文面
   ├ send_to_conversation(全文)
   └ 成功後: status=processing, direct/direct_ts, write_sent
             → AI は write_file(20s) だけ → 取込
```

---

## 2. 監査で見つかった穴と対応

| # | 穴 | 対応 |
|---|---|---|
| A1 | `show()` はコンテキスト本体とは別に outbox パス・reply_to・決まり・思考・末尾ルールを出している。これを送らないとAIはどこに書くか分からない | §3 共通レンダラをサーバーに置き、wait_input も直送も同じ文字列を使う |
| A2 | `poke_worker` は processing の入力を約1.5秒ごとに再合図する。直送だと全文再送で多重ターン。さらに直送済みは `status=processing` なので `direct_poke` の再送ガードに弾かれ、再送は必ず失敗する | §8 `direct_ts` で通常pokeから除外。**RUNNINGを一度確認した後のIDLE 5秒**、または**送信から20秒後のIDLE 5秒**で「返事なし」と判定し、直送を最大2回まで再送（Geminiのフィルターは同じ文でも通ることがある）。それでもダメなら `reset=True`＋`direct_off` で既存の「left」経路（wait_input）へ |
| A3 | retry の間に前の返事が取り込まれ、古い差分を送ると文脈が1ターン欠ける | §7 送信直前に毎回 build。retry でも作り直す |
| A4 | `build_context` は末尾で `write_sent` する。送信失敗時に記録すると次回から差分だけになり過去を失う | §4 `record=False` にし、送信成功後だけ `write_sent` |
| A5 | `/api/pending?check=1` は processing のユーザー発言を「未応答」として返す。直送分が別タスクの wait_input で再表示される | §9 `direct` フラグを付けて `left` 走査から除外 |
| A6 | windowモードは改行を潰してキー入力するため長文を送れない | §11 `POKE_MODE == "rpc"` 限定 |
| A7 | 直送RPC中に停止されると、停止時点は `pending` なので `reset` が刻まれない。しかしIDEには届いているため、次の差分送信で文脈がズレる | §7 送信後にメッセージ消失を検知したら `reset=True` を書く（停止の3タイミングすべてで整合） |
| A8 | `direct_watch` の掃除を `cands`（IDE1つにつき1件）でやると、同一IDEの複数チャンネル共有やタスク切り替えで監視対象が外れた瞬間に watchdog が途絶する | §8 セッション走査で「processing かつ direct_ts」の全IDを `direct_live` として集め、**走査直後**に掃除する |
| A9 | 直送文の「wait_input.py は実行しない」と、フォールバックの「実行せよ」が同じ会話履歴に同居し、AIが混乱しうる | §8 `send_poke(prefix=...)` で「前の指示は無効。今度は従来方式」と**前言を撤回**してからコマンドを送る |

条件付きの考慮:

| 項目 | 状況 |
|---|---|
| AGENTS.md | 全チャンネル `agents_md=False` なので今は衝突しない。ONにした場合は直送文の先頭で「今回は wait_input.py を実行しない」と明示（§3） |
| choices 等のサブタスク | 従来のまま4秒残る。全チャンネルで choices は別IDEなので干渉しない |
| light モード | ファイルの場所を渡す方式なので直送の効果なし。現状は全チャンネル `diff` |
| 連投 | processing で直列化される。2通目を先に build しない（送信直前 build で担保） |
| 停止 | `direct_ts` はメッセージ削除で消える。watchdog は候補から外れて自然消滅（§10） |
| テスト会話 | Antigravity に「【テスト】長文切り詰め検証」が残っている（手動削除） |

---

## 3. `server.py` — 共通レンダラ（A1）

`build_context` の近くに追加。`wait_input.show()` の完全移植。

```python
CTX_DIR = os.path.join(DATA, "ctx")
LONG_CTX, LONG_CTX_LINES = 6500, 220   # wait_input.py と同じ値

def render_input_text(sid, m, ctx, task_type=None, ide="", note="", direct=False):
    """wait_input.py が表示する文面を組み立てる。表示と直送で同じ文字列を使う。
    direct=True: ファイルに逃がさず全部inline、末尾の指示を直送用に差し替える"""
    title = ctx.get("title") or sid
    out = ["=== NEW INPUT ==="]
    if direct:
        out.append("（このメッセージだけで応答する。wait_input.py は実行しない。"
                   "AGENTS.md や過去の手順より、この指示を優先する）")
    if note:
        out.append(f"!!! {note}")
    tag = task_type or ctx.get("mode") or "reply"
    tag = re.sub(r"[^a-zA-Z0-9_-]", "", str(tag)) or "reply"
    outbox = os.path.join(OUTBOX, f"{sid}_{tag}_{int(time.time())}.json")
    out += [f"session : {sid}", f"title   : {title}", f"reply_to: {m['id']}"]

    mode = ctx.get("mode")
    if mode == "full":
        out.append(f"内容    : 全部（{ctx['reason']}）。これまでに覚えているこのチャンネルの内容より、下の内容を正とする")
        if ctx.get("files"):
            out.append("「ファイル:」とある項目は、応答を書く前にそのファイルを全部読む")
    elif mode == "image_refine":
        out.append("内容    : 画像プロンプトのAI推敲・構図および状態整合性の最適化。意味重複の徹底排除、上限30〜40個、重要度ソート、時系列厳守。本文や会話応答は出力せず、推敲・整理したDanbooruプロンプトのJSON（type: image_refine）のみを outbox に出力する")
    elif mode == "memory_extract":
        out.append("内容    : 長期記憶の自動要約・抽出。本文や画像は出力せず、抽出した記憶のJSON（type: memory_extract）のみを outbox に出力する")
    elif mode == "consolidation":
        out.append("内容    : 世代交代の定期統合。本文や画像は出力せず、memo / elapsed_time / state のみを出力する")
    elif mode in ("choices_normal",) or (mode == "choices" and ctx.get("choices_type") != "erotic"):
        out.append("内容    : 返答候補（choices）の自動生成。本文や画像は出力せず、文脈に沿った自然な8つのchoicesのみを outbox に出力する")
    elif mode in ("choices_erotic", "choices") or ctx.get("choices_type") == "erotic":
        out.append("内容    : 返答候補（choices）のドスケベ再生成。本文や画像は出力せず、8つのchoicesのみを outbox に出力する")
    elif ctx.get("light"):
        out.append("内容    : 前回の続き。設定・会話はこの会話でもう見たものを使う。下は今回の発言（と、変わった設定があればその分）だけ")
        if ctx.get("files"):
            out.append("「ファイル:」とある項目は、応答を書く前にそのファイルを全部読む")
    else:
        out.append("内容    : 前回からの続き。設定と会話は前に表示したものをそのまま使い、増えた分・変わった分だけ下に出す")

    is_subtask = mode in ("choices", "choices_normal", "choices_erotic",
                          "image_refine", "memory_extract", "consolidation")
    if is_subtask:
        out.append("決まり  : 読むのはこの表示だけ。ほかのファイル・コマンド・API・プロセスは一切見ずに、指定された JSON のみを直ちに outbox に書くこと。")
    else:
        read_cmd = f'python "{os.path.join(ROOT, "read_past_messages.py")}" --sid {sid} --mid <確認したい記憶のmid> --window 5'
        out.append("決まり  : 読むのはこの表示と、指定されたファイルだけ。ほかのフォルダの一覧・ファイル・API・プロセスは見ない。\n"
                   "ただし、【過去の記憶・約束】にある出来事の当時の具体的なセリフや生々しいニュアンスを原文で確認したい時のみ、"
                   "以下のコマンドを実行して前後の生ログを参照してよい（要約だけで十分な場合は実行不要）：\n"
                   f"  {read_cmd}\n"
                   "分からないこと（演じるキャラが決まっていない等）は探さずに、応答の text でユーザーに短く質問する（このときだけメタな質問でよい）")
    out.append("思考    : 思考では、この表示の内容（設定・会話・ユーザーの発言）も、これから書く応答の本文も、復唱・書き写しをしない。"
               "触れる必要があるときは短い要約にとどめる。応答の本文は outbox の JSON に直接書く")
    for x in ctx.get("missing") or []:
        out.append(f"!!! 設定ファイルが読めなかった: {x}")

    body = "".join(f"\n--- {sec['label']} ---\n{sec['text']}\n" for sec in ctx.get("sections", []))
    if ctx.get("same"):
        body += "\n（前回と同じなので省略: " + "、".join(ctx["same"]) + "）\n"

    if not direct and not ctx.get("light") and (len(body) > LONG_CTX or body.count("\n") > LONG_CTX_LINES):
        os.makedirs(CTX_DIR, exist_ok=True)
        path = os.path.join(CTX_DIR, f"{sid}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title} / reply_to {m['id']}\n{body}")
        out.append(f"\n設定・会話・発言はファイルに書いた（{body.count(chr(10)) + 1}行）。"
                   "このコマンドの表示ではなく、必ずこのファイルを最初から最後まで読んでから応答する:")
        out.append(f"  {path}")
        out.append("（/api/context などを自分で呼んで集め直さないこと。呼ぶと送った扱いになり、次からは差分しか返らない）")
        for sec in ctx["sections"]:
            if sec["label"].startswith("ユーザーが今回の発言に添付"):
                out.append(f"\n--- {sec['label']} ---\n{sec['text']}")
        last = ctx["sections"][-1] if ctx["sections"] else None
        if last and len(last["text"]) <= 1500:
            out.append(f"\n--- {last['label']} ---\n{last['text']}")
    else:
        out.append(body)

    out.append("")
    out.append(f"応答は {outbox} に JSON で書く（書き方は「応答の書き方」のとおり）")
    if direct:
        out.append("書いたら待機はしないで、このターンを終える。この会話への返事は書かない（問題があったときだけ、その内容を短く書く）")
        out.append("次の入力は、この会話に新しい指示として届く。wait_input.py をバックグラウンドで常駐させないこと")
    elif ide:
        me = f'python "{os.path.join(ROOT, "wait_input.py")}" --ide {ide}'
        out.append("書いたら待機はしないで、このターンを終える。この会話への返事は半角スペース1文字だけにする（問題があったときだけ、その内容を書く）")
        out.append("wait_input.py をバックグラウンドで常駐させないこと（受け取った入力を誰も読めなくなる）")
        out.append(f"次の入力があると、チャットからこの会話にスペース1文字（合図）が届く。届いたら {me} --wait 30 を実行する（前回の応答が届いたかもそこで確認される）")
    else:
        out.append("書いたら wait_input.py をもう一度実行せずに、このターンを終える（待ち続けると、入力がなくても使用量が減っていく）。"
                   "次の入力が来たら、ユーザーがこの会話で知らせる")
    if ctx.get("rules"):
        out.append(f"\n--- {ctx['rules']['label']} ---\n{ctx['rules']['text']}")
    return "\n".join(out) + "\n"


def with_render(s, m, ctx, task_type=None, ide="", note="", direct=False):
    """build_context 系の戻り値に表示文を付ける（/api/context, /api/claim, 直送で共用）"""
    if isinstance(ctx, dict) and "sections" in ctx:
        ctx["render"] = render_input_text(s["id"], m, ctx, task_type, ide, note, direct)
    return ctx
```

---

## 4. `build_context` に `record` を追加（A4）

```python
def build_context(s, m, record=True):
    ...
    sent = {
        "ide": ide,
        "choices_ide": old.get("choices_ide", ""),  # 選択肢セッションの送信履歴を消さない
        "sigs": {key: sig_of(ct) for _, key, ct in parts},
        "labels": {key: lb for lb, key, _ in parts},
        "msgs": [msg_sig(x) for x in msgs[:idx + 1]],
        "reset": bool(old.get("reset")),     # AI が実際に応答するまで「送った」ことにしない
        "why": old.get("why", ""),
        "ts": now(),
    }
    if record:
        write_sent(s["id"], sent)
    ...
    ctx = {"mode": "full" if reason else "diff", "reason": reason, "sections": sections,
           "same": same, "files": files, "light": light, "missing": missing, "rules": rules,
           "title": st.get("title")}
    if not record:
        ctx["_sent"] = sent
    return ctx
```

既存の `/api/claim` 経由は `record=True` のまま（挙動不変）。直送だけ `record=False`。

---

## 5. `/api/context` / `/api/claim` が `render` を返す

`/api/context` は全分岐を `with_render` で包む。

```python
ide = d.get("ide") or ""
note = d.get("note") or ""
if req_type == "user" or (not req_type and m.get("role") == "user"):
    return self.send_json(with_render(s, m, build_context(s, m), None, ide, note))
elif req_type == "image_refine":
    return self.send_json(with_render(s, m, build_image_refine_context(s, m, base_prompt=m.get("image_refine_prompt")),
                                      "image_refine", ide, note))
elif req_type == "choices":
    if m.get("role") == "user":
        return self.send_json(with_render(s, m, build_parallel_choices_context(s, m), "choices", ide, note))
    ctype = m.get("choices_type") or d.get("choices_type") or "normal"
    if ctype == "erotic":
        return self.send_json(with_render(s, m, build_choices_context(s, m), "choices", ide, note))
    return self.send_json(with_render(s, m, build_normal_choices_context(s, m), "choices", ide, note))
elif req_type == "consolidation":
    return self.send_json(with_render(s, m, build_consolidation_context(s, m), "consolidation", ide, note))
elif req_type == "memory_extract":
    return self.send_json(with_render(s, m, build_memory_extract_context(s, m), "memory_extract", ide, note))
```

`/api/claim` の `res["context"]` を作る全分岐も同じ形にする。`task_type` は
`req_type if req_type != "user" else None` を渡す（outbox のタグ用）。

```python
if d.get("context") and m:
    task_type = req_type if req_type != "user" else None
    if req_type == "user":
        ctx = build_context(s, m)
    elif req_type == "image_refine":
        ctx = build_image_refine_context(s, m, base_prompt=m.get("image_refine_prompt"))
    ...
    res["context"] = with_render(s, m, ctx, task_type, d.get("ide") or "", d.get("note") or "")
```

---

## 6. `wait_input.py` — 表示を `render` に置換

```python
def show(sid, m, note="", ctx=None, task_type=None):
    if not ctx:
        payload = {"sid": sid, "id": m["id"], "ide": IDE, "note": note}
        if task_type:
            payload["type"] = task_type
        ctx = post("/api/context", payload)
    text = ctx.get("render")
    if not text:
        print("（サーバーが表示文を返さない: server.py と wait_input.py の版が違う可能性）", file=sys.stderr)
        sys.exit(1)
    print(text, end="")
    sys.exit(0)
```

`check_previous` の `left` 側の claim にも `"ide": IDE` と `"note"` を渡す:

```python
res = post("/api/claim", {"sid": sid, "id": m["id"], "type": left_type, "context": True,
                          "ide": IDE, "note": "この入力は受け取り済みだが、応答がチャットに届いていない。もう一度 outbox に書き直すこと"})
show(sid, m, ctx=res.get("context"), task_type=left_type)
```

旧 `show()` 本体（約90行）は削除する。

---

## 7. 直送関数（A3: 送信直前に毎回 build）

`poke_worker` の近くに追加。

```python
DIRECT_MAX = int(os.environ.get("RPCHAT_DIRECT_MAX", 40000))

def direct_send_on(st):
    if POKE_MODE != "rpc":
        return False
    env = os.environ.get("RPCHAT_DIRECT_SEND")
    if env == "0":
        return False
    return env == "1" or bool(st.get("direct_send"))

def direct_poke(ide, sid, mid, retry=False):
    """(ok, retry, fallback, msg)
    retry=True: 直送済み(processing/direct)の再送。
    Gemini のフィルターは同じ文面でも通ることがあるので、打ち切り前に再送する"""
    with lock:
        s = load(sid)
        m = find_msg(s, mid)
        if not m:
            return False, False, False, "消えている"
        if retry:
            if not (m.get("status") == "processing" and m.get("direct")):
                return False, False, False, "再送の対象ではない"
        elif m.get("status") != "pending":
            return False, False, False, "もう受け取られている"
        ctx = build_context(s, m, record=False)
        text = render_input_text(sid, m, ctx, None, ide, direct=True)
        if retry:  # 前回の拒否文に引きずられないよう、先頭で打ち消す（本文は変えない）
            text = ("（前回の応答は無視する。同じ入力の再送。前回の返事には触れず、"
                    "この指示だけに従って outbox に JSON を書く）\n" + text)
        if len(text) > DIRECT_MAX:
            return False, False, True, f"{len(text)}字（上限 {DIRECT_MAX}）"
        need_media = bool(m.get("files")) and not m.get("pasted")
    media = paste_media([m]) if need_media else []
    ok, msg, retry_send = ide_poke.send_to_conversation(ide, text, ide_model_of(ide), media or None)
    if not ok:
        return False, retry_send, False, msg
    with lock:
        s = load(sid)
        m = find_msg(s, mid)
        if not m:
            # 送信中に停止された: IDE には届いているので、次は全部送り直す
            sent = read_sent(sid)
            sent.update({"ide": "", "msgs": [], "reset": True, "ts": now(),
                         "why": "送信中に停止された（その発言と応答は無かったものとして扱う）"})
            write_sent(sid, sent)
            return True, False, False, "送信中に停止された（次は全部送り直す）"
        if not retry:
            if m.get("status") != "pending":
                # 編集・作り直しなどで入力が変わった。安全側で全部送り直す
                sent = read_sent(sid)
                sent.update({"ide": "", "msgs": [], "reset": True, "ts": now(),
                             "why": "送信中に入力が変わった（全部送り直す）"})
                write_sent(sid, sent)
                return True, False, False, "送信中に入力が変わった"
            m["status"] = "processing"
            claimed_at[mid] = time.time()
        m["direct"] = True
        m["direct_ts"] = now()
        if media:
            m["pasted"] = True
        save(s)
        write_sent(sid, ctx["_sent"])
    return True, False, False, msg
```

---

## 8. `poke_worker` への組み込み（A2）

> 前提: `poke_text(ide, full, wait, prefix="")` と `send_poke(..., prefix="")` に
> `prefix` を追加し、`text = prefix + poke_text(...)` する（A9 のフォールバック用）。


ループ先頭のローカル:

```python
direct_sids = set()
direct_live = set()  # 監視が必要なメッセージID（全チャンネルの processing かつ direct_ts）
direct_watch = {}    # mid -> {"tries", "idle_since", "seen_running", "born", "next"}
```

セッション走査（`for sid in list_ids()`）で:

```python
st = s["setting"]
if direct_send_on(st):
    direct_sids.add(sid)

# 既存の fresh_sid_map を作っているループに追加
for m in msgs:
    mid = m.get("id")
    if mid:
        fresh_sid_map[mid] = sid
    if m.get("status") == "processing" and m.get("direct_ts") and not m.get("direct_off"):
        direct_live.add(m["id"])
```

`to_poke` を作るループの先頭で、直送済みを通常pokeから外し watchdog に回す:

```python
for ide_k, cand in list(cands.items()):
    task_key, m, task_type, sid = cand

    if (rpc and task_type == "user" and sid in direct_sids
            and m.get("direct_ts") and not m.get("direct_off") and ide_k not in busy):
        w = direct_watch.setdefault(m["id"], {"tries": 0, "idle_since": 0,
                                              "seen_running": False, "born": time.time(), "next": 0})
        if time.time() >= w["next"]:
            if not ide_poke.idle(ide_k)[0]:
                w["seen_running"] = True          # 生成が始まったのを確認
                w["idle_since"] = 0
            else:
                if not w["idle_since"]:
                    w["idle_since"] = time.time()
                idle_for = time.time() - w["idle_since"]
                # 起動ラグ対策: RUNNING を一度見た、または送信から20秒経った
                started = w["seen_running"] or (time.time() - w["born"] >= 20)
                if idle_for >= 5 and started:     # ターンは終わったのに応答が無い
                    w["idle_since"] = 0
                    if w["tries"] < 2:
                        # フィルターは確率的。同じ文面の再送で通ることがある
                        ok, ret, fb, msg = direct_poke(ide_k, sid, m["id"], retry=True)
                        if ok:
                            w["tries"] += 1
                            w["seen_running"] = False
                            w["born"] = time.time()
                        print(f"[direct] retry {w['tries']} {m['id'][:8]} ok={ok} fb={fb} {msg}")
                    else:
                        # 2回ダメ → direct を外して「left」の対象に戻し、wait_input に全文で書き直させる
                        with lock:
                            s2 = load(sid)
                            m2 = find_msg(s2, m["id"])
                            if m2 and m2.get("status") == "processing":
                                m2.pop("direct", None)
                                m2["direct_off"] = True
                                save(s2)
                        old = read_sent(sid)
                        old["reset"] = True
                        old["why"] = "直送に応答が無かったので、全部送り直す"
                        write_sent(sid, old)
                        send_poke(ide_k, full=True,
                                  prefix="（直送に応答がなかった。ここから従来方式に戻す。"
                                         "前の『wait_input.py は実行しない』は無効。次のコマンドを実行すること）\n")
                        print(f"[direct] give up {m['id'][:8]} -> legacy(left)")
                        direct_watch.pop(m["id"], None)
                        quiet[ide_k] = time.time() + 15
            w["next"] = time.time() + 3
        since.pop(task_key, None)
        continue
    ...
```

`dispatch_poke` の先頭:

```python
def dispatch_poke(target_ide, item):
    task_k, m_item, ttype, item_sid = item
    full = target_ide in unseen
    media = paste_media([m_item]) if (rpc and ttype == "user" and m_item.get("files") and not m_item.get("pasted")) else []

    if rpc and ttype == "user" and item_sid in direct_sids and not m_item.get("direct_off"):
        ok, retry, fb, msg = direct_poke(target_ide, item_sid, m_item["id"])
        if not fb:
            if retry:
                quiet[target_ide] = time.time() + 0.5
                return
            print(f"[direct] {'ok' if ok else 'NG'} {item_sid[:8]} {msg}")
            quiet[target_ide] = time.time() + 1.5
            return
        # サイズ超過だけ従来へ（media は既に作っている）

    ok, msg, retry = send_poke(target_ide, full, media)
    ...
```

成功時に `done` へ入れないこと（watchdog が監視を続ける）。`dispatch_poke` の末尾にある
`done.add(task_k)` は直送の early return で通らない。

掃除は**走査直後・送信処理より前**に1回だけ行う（送信後にやると、このループで新しく直送した
メッセージは走査時点で `pending` なので `direct_live` に入っておらず、立てた監視を消してしまう）:

```python
for k in list(direct_watch):
    if k not in direct_live:
        direct_watch.pop(k, None)
```

---

## 9. `left` からの除外（A5）

`/api/pending` の main 側 `check` 走査:

```python
if m["role"] == "user" and m.get("status") == "processing" and not m.get("direct"):
    left = [sid, m, "user"]
    break
```

---

## 10. 停止・取り込み時の後始末

`/api/send/stop`（`claimed_at` を消している箇所）は現状のままでよい（メッセージ自体が消える）。
`process_outbox` の本文取り込みで `status = "answered"` にする箇所に追加:

```python
m.pop("direct", None)
m.pop("direct_ts", None)
```

`direct_watch` は §8 の掃除で自然消滅する。

---

## 11. フラグ・設定UI・フォールバック

既定設定（`"agents_md": False` の近く）:

```python
"direct_send": False,   # 合図にコンテキストを直接載せる（rpcのみ・高速）
```

`public/index.html`（`f_input_mode` の下）:

```html
<label class="check" title="合図のメッセージに会話の内容を直接載せ、AIにwait_input.pyを実行させない（1ターン約4秒短縮。うまく動かないときはオフ）"><input type="checkbox" id="f_direct_send"> 合図に内容を直接載せる（高速）</label>
```

`public/app.js`:

```js
// fillForm
if ($("f_direct_send") && canSet("f_direct_send")) $("f_direct_send").checked = !!st.direct_send;
// readForm
if ($("f_direct_send")) st.direct_send = $("f_direct_send").checked;
```

`merge_defaults` を通るのでバックエンドの追加作業は既定値だけ。

### フォールバック一覧

| 条件 | 動作 |
|---|---|
| `POKE_MODE != "rpc"` | 従来（A6） |
| `RPCHAT_DIRECT_SEND=0` | 従来 |
| チャンネルの `direct_send` がOFF | 従来 |
| 生成文が `DIRECT_MAX` 超 | 従来（ファイル渡し） |
| `send_to_conversation` 失敗 | 0.5秒後に再試行（再build） |
| RUNNING確認後のIDLE 5秒、または送信から20秒後のIDLE 5秒 | 直送を再送（最大2回）。Geminiのフィルターは同じ文面でも通ることがある |
| 再送2回後も応答なし | `reset=True`＋`direct_off` にして既存の「left」経路へ（wait_input が「もう一度書き直すこと」と表示） |

---

## 12. 前提の実測（このプランの根拠）

- メイン会話31ターンの中央値: `run_command` **3.97秒**、`read_file` **8.48秒**（10/31ターン）、`write_file` **19.75秒**
- 直送メッセージの上限検証（使い捨て会話・Lowモデル）:

| 送信長 | 保存長 | 一致 | 末尾の合言葉を読めた |
|---|---|---|---|
| 5,851 | 5,851 | ✅ | ✅ |
| 11,828 | 11,828 | ✅ | ✅ |
| 19,826 | 19,826 | ✅ | ✅ |
| 23,789 | 23,789 | ✅ | ✅ |
| 31,787 | 31,787 | ✅ | ✅ |
| 47,826 | 47,826 | ✅ | ✅ |

→ 48KBまで欠けない。全文モード（18〜22KB）も直送可能。**④は不要**。

期待効果:

| ターン | 今 | 直送後 |
|---|---|---|
| 通常（diff 5〜7KB） | 約26秒 | 約19秒 |
| 長い（32%） | 約34秒 | 約21秒 |
| 全文モード | 約40秒 | 約27秒 |

（モデル Low 併用で 12〜16秒）

---

## 13. 受け入れ条件

1. 通常ターンで直送が使われ、`wait_input.py` が実行されない（pokeログ `[direct] ok`）
2. 全文モード（「次は全部送る」）でも直送で返る
3. `RPCHAT_DIRECT_MAX=100` で従来方式に落ちる
4. モデルがチャットに返事してファイルを書かなかった場合、**多重送信しない**（RUNNING確認後のIDLE 5秒、または送信20秒後のIDLE 5秒で最大2回再送 → それでもダメなら「left」経路へ）
5. 返事が来る前に停止 → 次のターンで幽霊応答が出ない
6. 連投2通 → 2通目は1通目の返事を取り込んだ後の文脈で生成される
7. 直送の送信中（pending）に停止しても、次ターンが全文モードになる
8. フラグOFF → 従来と同一動作

---

## 14. テスト手順

1. `RPCHAT_DIRECT_SEND=1` でサーバー起動、1チャンネルだけON
2. 通常メッセージ → 応答が返る／`[direct] ok`／`data/sent` が更新される
3. 「次は全部送る」→ 全文モードでも直送
4. `RPCHAT_DIRECT_MAX=100` で再起動 → 従来方式（wait_input）で返る
5. 会話をIDLEのまま放置 → 送信20秒後＋IDLE 5秒で再送が最大2回走り、その後 `reset=True` の「left」経路に移行する（再送で通ればそこで終わる）
6. 生成中に停止 → 次ターンで幽霊応答なし
6b. **直送RPC中（pending）に停止** → 次ターンが全文モード（`reset=True`）になり、IDEの余分な発言を打ち消せる
7. 2通連投 → 文脈が逆転しない
8. フラグOFF → 従来と同じ表示・動作（`wait_input.py` の出力を旧版と目視比較）

---

## 15. 実装順序（コミット単位）

1. **レンダラ移植のみ**（`render_input_text` + `with_render` + `wait_input.show` 置換）
   → 挙動は変わらない。`wait_input.py` の表示を1回目視で旧版と比較
2. **`build_context(record=False)` + API の `render`**
3. **`direct_poke` + `poke_worker` 組み込み + フラグ/UI**
4. **watchdog + `left` 除外 + 停止後始末**
5. **テスト → 1チャンネルON → 全チャンネルON**

---

## 16. リスクと戻し方

- フラグOFFで即従来動作（コードは残るが分岐しない）
- 最悪 `git revert` で4コミット戻せる
- 計測は `RPCHAT_PERF_LOG=1` の既存ログ＋pokeログの `[direct]` でターンごとに比較
- 想定外: モデルが直送文を「ユーザーの指示」と誤解してチャットに長文を返す →
  直送文の先頭と末尾で「チャットに書かない」を明示。1チャンネル目で必ず確認
