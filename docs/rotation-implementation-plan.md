# 世代交代（30ターン自動ローテーション）実装プラン ［最終版 v3］

対象: `server.py` / `ide_poke.py` / `public/app.js` / `test/`
前提: 既存の `session.json` 資産（`rotation_count` / `total_turns` / `last_rotation_turn` / `turns_since_rotation` / `rotation_notice`）を壊さない。
このドキュメントは **実装手順書**。コードはコピペ可能な完成形で書く。

> 行番号は執筆時点のもの。別作業（`sanitize_prompt` / `tag_dictionary.json` / `server.py` の他修正）で
> シフトするので、**必ず関数名アンカーで探すこと**。

**v3 の変更点（この版で直した12件）は §10 に一覧。**
v3 で潰した主な実バグ:

1. 疎通テストの送信失敗で試行回数が増えず **会話作成の無限ループ**（§P6）
2. テスト直後に `last_reply` を読む **レース**でブートストラップの返事を合格判定（§P6 の `after_step`）
3. `prune` 後に `total_turns` を式で再計算すると **記憶インデックスが巻き戻る**（§P0-1）
4. 予備会話の再利用で **反対側（choices）の予備まで捨てる**（§P4-3）
5. `_new_ide_in_progress` のキーに `assign` を混ぜると **排他が効かない**（§P4-1）

---

## 0. 非目標（やらないこと）

| やらない | 理由 |
|---|---|
| `total_turns` を常に `rotation_count * 20 + in_file` で**再計算**する | `calc_turn_idx`(382) / `search_memories`(444) / `delete_after_turn`(8810) が「現在の通算ターン」として使う。回転が1ターンでも遅延すると値が巻き戻り **過去ターンの記憶を削除**する。`total_turns` は単調増加カウンタ。式は「0 のときの補填」だけに使う（§P0-1） |
| cutover を「pending が無ければ即」「N秒で打ち切り」 | 打ち切ると旧会話が処理中の発言に新会話も `/api/pending` で食いつき **二重応答**。cutover は「未応答ゼロ」が条件、無理なら回転ごと中止する |
| 新会話に「この30ターンを要約して」と送る | クリーンな新セッションが汚染される。要約は本文ターンの `memo` を使う |
| 30ターン目のプロンプトに退避・新規会話の指示を書く | AI にサーバの仕事をさせる。30ターン目でも AI の仕事は「本文＋memo」だけ |
| `rotation` を `NO_IDE` に足す | `NO_IDE` は「分岐/コピーで持ち越さない」用途。`dict(new, **keep)` に `None` が混ざる事故になる（§P0-6 で個別対応） |
| 回転中に再生成/巻き戻しを **拒否** する | ユーザー操作が正義。回転を中止して操作を通す（§P8） |

---

## 1. 全体像

```
POST /api/send (30ターン目)                … P2: rotation を armed（数ミリ秒）
      │
      ├─ poke → AI が wait_input.py → 本文＋memo を outbox に書く
      │
process_outbox: save(s) = 本文が確定・UI に即表示
      │                                  … P2: rotation_note_body() = armed→archiving（数ミリ秒・RPCなし）
      │
rotation_worker（専用スレッド・非ブロッキング 1 tick 1 小ステップ）
      ├─ archiving          : スナップショット退避（rotation キーを除去して書く）
      ├─ settle             : 旧会話が running でなくなるのを最大15秒待つ（超えたら強行）
      ├─ spawn_main         : StartCascade（assign しない）
      ├─ spawn_choices      : 同上（並列 choices が有効なときだけ）
      ├─ bootstrap_main     : poke_text(full=True, wait=5) を新会話へ
      ├─ bootstrap_choices  : 同上
      ├─ verify_ready       : ブートストラップのターンが終わるのを待つ＋コマンド実行の確認
      ├─ verify_test        : 「準備完了」テスト → 合否（§6）
      └─ cutover            : 未応答ゼロを待って assignment を原子的に切替＋剪定
                                   … 失敗時は failed（旧会話のまま・skip_until 設定）
```

### 不変条件

| # | 内容 |
|---|---|
| **I1** | 本文 `save()` より前に RPC を1つも実行しない（`rotation_note_body` は RPC 禁止） |
| **I2** | worker は HTTP API / outbox / poke をブロックしない（`lock` を跨いで RPC しない） |
| **I3** | cutover まで `ide_session` は旧会話のまま（担当不在の窓を作らない） |
| **I4** | 全フェーズは `session.json` に永続化・冪等（再起動/二重発火/失敗で復帰） |
| **I5** | 新会話は疎通検証を通ってから cutover |
| **I6** | 30ターン目の全景約要求は1回だけ（`armed_turn` / `rotation_skip_until`） |
| **I7** | 未応答のまま assignment を切り替えない（cutover の `rotation_idle`）。二重応答は P0-9 の「宛先の無い本文は捨てる」で防ぐ |
| **I8** | アーカイブは「回転前の自己完結スナップショット」（`rotation` を含まない） |
| **I9** | 失敗時は必ず旧会話のままに戻す |
| **I10** | 試行回数（残機）は**減るだけ**。待っているだけ（busy）は減らさない |
| **I11** | 予備会話（`rotation_reserve`）に載るのは「まだ回転に使っていない会話」だけ |
| **I12** | `total_turns` は単調増加。剪定しても値を書き換えない |

---

## 2. データモデル

### 2-1. `setting.rotation`（`phase` が無ければ回転していない）

```json
{
  "phase": "armed",
  "rot_no": 4,
  "armed_turn": 30,
  "old_ide": "…", "old_ide_title": "…", "old_model": "…",
  "old_choices_ide": "…", "old_choices_ide_title": "…", "old_choices_model": "…",
  "new_ide": "…", "new_ide_title": "…",
  "new_choices_ide": "…", "new_choices_ide_title": "…",
  "attempts": {
    "spawn": 1, "spawn_choices": 0,
    "bootstrap_main": 1, "bootstrap_choices": 0,
    "verify": 1, "test_sent": true, "verify_poll": 3, "verify_after_step": 12,
    "ready_poll": 0
  },
  "archive": "data/archive/<sid>_rot4_turn90.json",
  "settle_since": "…", "wait_since": "…",
  "error": "",
  "since": "2026-11-07T21:09:41", "updated": "2026-11-07T21:09:41"
}
```

- `attempts.verify` は **累積ラウンド数**（初回=1、リトライで 2,3…）。**絶対に 0 に戻さない**（I10）。
- `attempts.verify_after_step` は送信直前の `stepCount`。`last_reply()` はこれ以前の返事を無視する（§P6）。

### 2-2. `setting.rotation_skip_until`（int・in-file ユーザーターン数）

失敗/中止後、この数に達するまで再武装しない（`ROT_SKIP_TURNS=3`）。
cutover と `/api/reset` で 0 に戻す。

### 2-3. `setting.rotation_reserve`（失敗時に作ってあった会話の再利用候補）

```json
{"ide": "…", "choices_ide": "…", "title": "新規会話 11:48", "ts": "2026-11-07T21:10:03"}
```

`rotation_fail` が書き、次の回転の `spawn_main`/`spawn_choices` が `locate()` で生存確認して adoption する
（中止のたびに会話が増えるのを防ぐ）。**使った側のキーだけ** `pop` する（I11）。
cutover・`/api/reset`・インポートで丸ごと消す。

### 2-4. 新定数（`ROTATION_TURNS` の直後に置く）

```python
# ROTATION_PERIOD は「旧セッションの total_turns 補填」専用に残す（恒常的な再計算には使わない）
ROTATION_PERIOD = ROTATION_TURNS - ROTATION_KEEP_TURNS   # 既存定義のまま

ROT_TICK = 0.3                 # worker の刻み（秒）
ROT_SETTLE_SEC = 15            # 旧会話が落ち着くのを待つ上限
ROT_SPAWN_TRIES = 3            # 新規会話の作成試行回数
ROT_BOOTSTRAP_TRIES = 6        # ブートストラップ送信の試行回数
ROT_BOOTSTRAP_WAIT = 5         # 予備実行用の wait_input.py --wait（30 だと無駄に30秒待つ）
ROT_VERIFY_TEXT = "準備完了"
ROT_VERIFY_ROUNDS = 2          # 疎通テストの総ラウンド数（初回を含む。2 = 初回+やり直し1回）
ROT_VERIFY_POLL = 2.0          # 検証ポーリング間隔
ROT_VERIFY_TIMEOUT = 120       # 1ラウンドの上限
ROT_WATCHDOG_SEC = 600         # cutover 待ちの上限（超えたら中止）
ROT_SKIP_TURNS = 3             # 中止後、再武装しないターン数

ROTATION_ENABLED = os.environ.get("RPCHAT_ROTATION", "1") != "0"  # 切り戻し用（武装を止める）

rotation_event = threading.Event()  # worker 起床（phase 書き込みの後に set する）
rotation_queue = {}                 # sid -> next_at(monotonic)
rotation_guard = threading.Lock()   # rotation_queue の保護（lock とは別）
```

> `RPCHAT_ROTATION=0` は「新規の武装を止める」非常停止。進行中の回転は I3/I9 により安全に完走/中止できる。

### 2-5. 共通ヘルパ

```python
def user_turns_of(msgs):
    return sum(1 for m in msgs if m.get("role") == "user")


def total_turns_base(st, file_turns):
    """通算ターン番号の基準。total_turns が未設定(0)の旧セッションだけ回転回数から補う。
    既存の読み取り側フォールバック (rot_cnt * ROTATION_PERIOD + in_file) と**同じ式**にすること。"""
    total = int(st.get("total_turns") or 0)
    if total > 0:
        return total
    return (int(st.get("rotation_count") or 0) * ROTATION_PERIOD) + int(file_turns)


def rotation_state(s):
    r = s["setting"].get("rotation")
    return r if isinstance(r, dict) and r.get("phase") else {}


def rotation_phase(s):
    return rotation_state(s).get("phase", "")


def rotation_idle(s):
    """cutover を止めるべき「未応答のユーザー発言」があるか（choices/memo/image は止めない）"""
    for m in s.get("messages", []):
        if m.get("role") == "user" and m.get("status") in ("pending", "processing"):
            return False
    return True


def rotation_skipping(s):
    """中止直後のスキップ期間中か（in-file ターン数で判定）"""
    until = int(s["setting"].get("rotation_skip_until") or 0)
    return bool(until) and user_turns_of(s.get("messages", [])) < until


def ide_is_known(ide):
    global known_ides
    if known_ides is None:
        known_ides = set(read_json(IDE_KNOWN, []))
    return ide in known_ides


def rotation_put(sid, **kw):
    """rotation を部分更新して保存（ロック内。**RPC 禁止**）"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return {}
        r = rotation_state(s)
        if not r:
            return {}
        r.update(kw)
        r["updated"] = now()
        s["setting"]["rotation"] = r
        save(s)
        return clone_session(s)


def rotation_schedule(sid, delay=0.0):
    with rotation_guard:
        rotation_queue[sid] = time.monotonic() + max(0.0, delay)
    rotation_event.set()          # phase 書き込みの後で呼ぶこと


def rotation_fail(sid, why, log=True):
    """中止して旧会話のまま安全側に戻す（I9）。作ってあった予備会話は再利用候補として残す（I11）。"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return
        st = s["setting"]
        r = rotation_state(s)
        if not r:
            return
        if r.get("new_ide") or r.get("new_choices_ide"):
            st["rotation_reserve"] = {
                "ide": r.get("new_ide") or "",
                "choices_ide": r.get("new_choices_ide") or "",
                "title": r.get("new_ide_title") or "",
                "ts": now(),
            }
        st["rotation_skip_until"] = user_turns_of(s["messages"]) + ROT_SKIP_TURNS
        st["rotation_notice"] = f"⚠️ 世代交代の準備に失敗しました（{now()[11:16]} / {why}）"
        st.pop("rotation", None)
        save(s)
    if log:
        print(f"[rot] {sid[:6]} failed: {why}")
    with rotation_guard:
        rotation_queue.pop(sid, None)
```

---

## 3. フェーズ表（条件 / 次 / 失敗時）

| phase | 条件 | やること | 次 | 失敗 |
|---|---|---|---|---|
| `armed` | send でセット | 何もしない（プロンプトに節を出すだけ） | `archiving`（本文到着時） | — |
| `archiving` | 常に | スナップショット（`rotation`/`rotation_notice`/`rotation_skip_until` を除去）。既存ファイルは skip | `settle` | 警告して続行 |
| `settle` | `old_ide` あり | `ide_poke.progress(old_ide).running` が False になるまで最大 `ROT_SETTLE_SEC` | `spawn_main` | 超えたら強行 |
| `spawn_main` | 常に | `new_ide(assign=False)`。cid を即永続化。作成済みなら adoption。予備あれば再利用 | `spawn_choices` | `ROT_SPAWN_TRIES` 超で `rotation_fail` |
| `spawn_choices` | `old_choices_ide` あり | 同上。無ければ何もせず次へ | `bootstrap_main` | 失敗しても choices 無しで続行 |
| `bootstrap_main` | `new_ide` 生存 | idle 待ち→`send_poke(full=True, wait=5, model=old_model)` | `bootstrap_choices` | `ROT_BOOTSTRAP_TRIES` 超で `rotation_fail` |
| `bootstrap_choices` | `new_choices_ide` あり | 同上 | `verify_ready` | 続行 |
| `verify_ready` | 常に | ブートストラップのターンが終わるのを待つ。`ide_known` 未登録なら `bootstrap_main` へ戻す | `verify_test` | タイムアウトで `rotation_fail` |
| `verify_test` | 常に | テスト送信→idle 待ち→`last_reply(after_step)`→合否（§6） | `cutover` | `【失敗】` にリネームして `spawn_main`（`ROT_VERIFY_ROUNDS` まで） |
| `cutover` | `rotation_idle(s)` | assignment 切替＋剪定＋notice＋`write_sent(reset)` | `done`（rotation 削除） | `ROT_WATCHDOG_SEC` 超で `rotation_fail` |

---

## 4. 実装パッチ（この順で入れる）

### P0. 前提バグ修正（回転と独立に効く・先に入れる）

#### P0-1. `total_turns` を単調増加にする（I12）

**A. `/api/send` の末尾**（現行の `file_user_turns` / `rot_cnt` / `cur_total` の `if-else` を丸ごと差し替え）

```python
                s["messages"].append(m)
                # total_turns は「通算ユーザーターン番号」の唯一の真実。増えるのは送信時だけ。
                # 0 のときだけ total_turns_base で補填（旧セッションの巻き戻り防止）
                in_file = user_turns_of(s["messages"])
                prev_total = total_turns_base(s["setting"], max(0, in_file - 1))
                s["setting"]["total_turns"] = max(prev_total, in_file - 1) + 1
                rotation_maybe_arm(s)      # ← P2-1（30ターン目なら armed / 数ミリ秒）
                save(s)
```

**B. `/api/message/{edit,regenerate,truncate}`**（`if p in (...)` のブロック。
`idx < 0` チェックの直後、**`s["messages"]` を書き換える前**に `before_turns` を取る）

```python
            if p in ("/api/message/edit", "/api/message/regenerate", "/api/message/truncate"):
                idx = next((i for i, mm in enumerate(s["messages"]) if mm["id"] == d.get("id")), -1)
                if idx < 0:
                    return self.send_json({"error": "no such message"}, 404)
                if rotation_state(s):                       # §P8: 回転は中止して操作を通す
                    rotation_fail(s["id"], "会話の書き換えが行われた")
                before_turns = user_turns_of(s["messages"])  # ← 追加（書き換え前）
                new_user, rv = None, False
                ...
```

末尾の現行ブロック（`remaining_turn` / `rot_cnt` / `total_turns` 再計算）を:

```python
                remaining_turn = user_turns_of(s["messages"])
                # 削除したユーザーターン数だけ通算を戻す（in_file から再計算しない＝巻き戻り事故の防止）
                removed = max(0, before_turns - remaining_turn)
                prev_total = total_turns_base(s["setting"], before_turns)
                s["setting"]["total_turns"] = max(0, prev_total - removed)
                current_total = s["setting"]["total_turns"]
```

> **削除対象**: `/api/send` と truncate 系の `ROTATION_PERIOD` 再計算（2箇所）。
> **残す**: 読み取り専用フォールバック（`st.get("total_turns") or (rot*PERIOD + len(user_msgs))`）と
> `total_turns_base`。`grep -n "ROTATION_PERIOD"` で「補填とフォールバック以外に無い」ことを確認する。
>
> **実装順の注意**: このブロックは `total_turns_base` / `rotation_state` / `rotation_fail` / `rotation_maybe_arm`
> を呼ぶ。P0-1 を先に入れる場合は **§2-5 と P2-1 の関数も同じコミットで置く**
> （未定義のままだと `/api/send` が `NameError` で落ちる）。

#### P0-2. `clone_session` に `rotation` / `rotation_reserve` を追加

```python
        if "rotation" in st and isinstance(st["rotation"], dict):
            st["rotation"] = copy.deepcopy(st["rotation"])
        if "rotation_reserve" in st and isinstance(st["rotation_reserve"], dict):
            st["rotation_reserve"] = dict(st["rotation_reserve"])
```

（無いとアーカイブ作成時に `snap["setting"].pop("rotation")` が **本体の setting を壊す**）

#### P0-3. `write_sent` の `reset` 消費が早すぎるバグ（重要）

`build_context` の `write_sent(...)` で `reset` を消さない:

```python
    write_sent(s["id"], {
        "ide": ide,
        "choices_ide": old.get("choices_ide", ""),
        "sigs": {key: sig_of(ct) for _, key, ct in parts},
        "labels": {key: lb for lb, key, _ in parts},
        "msgs": [msg_sig(x) for x in msgs[:idx + 1]],
        "reset": bool(old.get("reset")),     # AI が実際に応答するまで「送った」ことにしない
        "why": old.get("why", ""),
        "ts": now(),
    })
```

`note_reply` の末尾で解除:

```python
    old.pop("reset", None)
    old.pop("why", None)
    write_sent(s["id"], old)
```

> これが無いと、**ポーク再送や初回失敗のあとに差分モード**になり、新会話が何も知らないまま応答する。

#### P0-4. インポート時の除去（`create_session` 付近）

```python
        st.pop("rotation_notice", None)
        st.pop("last_memo_mid", None)
        st.pop("rotation", None)              # 進行中の回転は持ち込まない
        st.pop("rotation_skip_until", None)
        st.pop("rotation_reserve", None)
```

#### P0-5. `/api/reset`

```python
                s["setting"].pop("rotation_notice", None)
                s["setting"].pop("rotation", None)
                s["setting"].pop("rotation_reserve", None)
                s["setting"]["rotation_skip_until"] = 0
```

#### P0-6. `/api/setting`（フォーム保存で回転状態を壊さない）

`keep` に追加（`NO_IDE` には入れない）:

```python
                keep["rotation"] = s["setting"].get("rotation")
                keep["rotation_skip_until"] = s["setting"].get("rotation_skip_until", 0)
                keep["rotation_reserve"] = s["setting"].get("rotation_reserve")
```

`merge_defaults` 後、`None` を掃除:

```python
                s["setting"] = merge_defaults(dict(new, **keep))
                for _k in ("rotation", "rotation_skip_until", "rotation_reserve"):
                    if s["setting"].get(_k) is None:
                        s["setting"].pop(_k, None)
```

#### P0-7. `ide_pending_for`（二重応答の保険）→ **実装後に廃止（重要な訂正）**

当初は「発言に『合図した会話』を刻み、その会話以外には渡さない」設計にしたが、**実装して分かった
欠陥があるため廃止した**。経緯と結論だけ残す（v4）。

**なぜ廃止したか**（実測で確認）:

- `/api/pending` は `for sid` の先頭で「`ide` がそのチャンネルの担当（`ide_session`）でなければ
  `continue`」している。つまり所有権フィルタに到達するのは**常に現在の担当会話だけ**。
- したがって刻印が担当と食い違うのは「合図を送った後に担当を切り替えた」とき**しか**ありえない。
- その状態で刻印を守ると、その発言は **旧担当（もう配信対象外）にも新担当にも渡らず、永久に未応答**になる。
- 実際に再現した（`poke_worker` が A に合図 → `/api/ide/assign` で B に変更）:
  - `/api/pending?ide=B` は 45 秒たっても `pending` を返さない
  - `/api/claim` は `"別の会話が処理中"` で拒否し続ける
  - 理由: `poke_worker` 側も `done`（永続）と `again`（full 合図で追加）で再合図しないため、
    配信路は `/api/pending` だけ。そこを刻印が塞ぐと詰む。
- 二重応答は元々 `process_outbox` の「宛先の無い本文は捨てる」判定（P0-9）で防げている。
  1つの発言に2つの会話が本文を書いても、2通目は `answered` 済みで捨てられる。

**最終的な実装（v4）**:

1. `poke_worker` の刻印書き込みを**削除**（`lock` の外で `load`→`save` していたため、
   同時に走る `/api/send` の発言を巻き戻して消す事故もあった）
2. `/api/pending` の `_claimable` を**削除**（`p_user` / `left` の抽出から外す）
3. `/api/claim` の「別の会話が処理中」拒否を**削除**
4. 取り込み時の `m.pop("ide_pending_for", None)` だけ**残す**（旧 `session.json` に残った刻印の掃除）

> 二重応答の防止は **P0-9（宛先の無い本文は捨てる）** が担う。担当は1チャンネルに1つしか
> 無いので、担当が変わった瞬間に古い会話へ配信されることはない（`/api/pending` の担当判定）。

#### P0-9. 宛先の無い本文（reply_to 無し）と壊れた JSON を捨てる

**A. 宛先の無い本文（二重応答の防波堤）**

`process_outbox` の本文取り込みは、`reply_to` が空だと宛先判定をすり抜けて、**未応答の発言が
1つも無くても assistant 発言を1件追加**していた（孤児の発言）。世代交代の疎通テストは新しい会話に
本物のメッセージを送るので、AI が outbox を書くとこれが起きやすい。

```python
            # 宛先の無い本文は捨てる。(1) 未応答のユーザー発言が1つも無い (2) reply_to があるのに
            # その発言が未応答でない（既に答えた・巻き戻した）。
            pending_users = [m for m in s["messages"] if m.get("role") == "user"
                             and m.get("status") in ("pending", "processing")]
            if not pending_users or (reply_to and not any(m["id"] == reply_to for m in pending_users)):
                print(f"[outbox] stale reply {name}")
                safe_move(path, os.path.join(DONE, name + ".stale"))
                continue
```

これが**二重応答の唯一の防波堤**（P0-7 の刻印を廃止したため）。同じ発言に2つの会話が本文を
書いても、先に取り込まれた時点で `status` が `answered` になり、2通目はここで捨てられる。

**B. 壊れた JSON**

実例: `outbox/done/51a9031728_user_1794019134.json.bad`（2026-11-07 11:39）

```
"inner: "あぁぁっ……指揮官様のが…   ← 閉じ引用符の欠落（"inner: → "inner": ）
```

`json.loads` が失敗し、**本文まで丸ごと `.bad` として捨てられる**（ユーザーからは「返事が来ない」）。
30ターン目の本文がこれに当たると、回転も起きないまま返答が消える。

```python
_OUTBOX_KEY_COLON = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"')


def repair_outbox_text(txt):
    """AI が書き損じた JSON を最小限だけ直す。直せなければ None。
    - "key: "value"  → "key": "value"    (閉じ引用符の欠落)
    - 末尾カンマ
    生の改行/タブは json.loads(strict=False) 側で吸収する。"""
    fixed, notes = txt, []
    f2 = _OUTBOX_KEY_COLON.sub(r'"\1": "', fixed)
    if f2 != fixed:
        notes.append("key: の閉じ引用符欠落")
        fixed = f2
    f3 = re.sub(r",(\s*[}\]])", r"\1", fixed)
    if f3 != fixed:
        notes.append("末尾カンマ")
        fixed = f3
    if not notes:
        return None
    try:
        obj = json.loads(fixed, strict=False)
    except ValueError:
        return None
    return (obj, notes) if isinstance(obj, dict) else None
```

`process_outbox` の読み込み部分:

```python
        try:
            with open(path, encoding="utf-8-sig") as f:
                raw = f.read()
            try:
                rep = json.loads(raw, strict=False)      # 文字列内の生改行を許す
                if not isinstance(rep, dict):
                    raise ValueError("JSON がオブジェクトではない")
            except ValueError:
                repaired = repair_outbox_text(raw)
                if not repaired:
                    raise
                rep, notes = repaired
                print(f"[outbox] repaired {name}: {', '.join(notes)}")
        except Exception as e:
            ...  # 既存の .bad 退避のまま（ログに理由を出す）
```

> **注意**: 修復は「取り込めるものは取り込む」だけの改善。途中で切れた JSON は本文が欠けるので、
> 失敗時の `.bad` 退避とログは必ず残すこと。`"key: "` が**文字列値の中**に現れると誤変換しうるが、
> 元が壊れている場合しか走らないので実害は小さい。

---

### P1. 基盤（worker スレッド）

`__main__` のスレッド起動に追加（`outbox_worker` の直後）:

```python
    threading.Thread(target=rotation_worker, daemon=True).start()
```

```python
def rotation_worker():
    """世代交代の進行役。1 tick で各セッションの「1小ステップ」だけ進める（RPC で他を止めない）"""
    while True:
        rotation_event.wait(timeout=ROT_TICK)
        rotation_event.clear()
        now_m = time.monotonic()
        with rotation_guard:
            due = [sid for sid, at in list(rotation_queue.items()) if at <= now_m]
        for sid in due:
            try:
                delay = rotation_step(sid)
            except Exception as e:
                print(f"[rot] {sid[:6]} step error: {e}")
                delay = ROT_TICK
            with rotation_guard:
                if sid in rotation_queue:      # fail 等で自分で消している場合がある
                    if delay is None:
                        rotation_queue.pop(sid, None)
                    else:
                        rotation_queue[sid] = time.monotonic() + float(delay)
        rotation_resume_scan()


_rot_scan_at = 0.0


def rotation_resume_scan():
    """phase が残っているのに queue に無いセッションを拾う（再起動・取りこぼし対策）。
    poke_worker が既に全走査しているので 5 秒に1回で十分（I/O とロック競合を増やさない）"""
    global _rot_scan_at
    if time.monotonic() - _rot_scan_at < 5.0:
        return
    _rot_scan_at = time.monotonic()
    try:
        sids = list_ids()
    except Exception:
        return
    with rotation_guard:
        queued = set(rotation_queue)
    for sid in sids:
        if sid in queued:
            continue
        try:
            s = load(sid, readonly=True)
        except (OSError, ValueError):
            continue
        if rotation_phase(s):
            rotation_schedule(sid, 0.0)


ROTATION_STEPS = {}   # phase -> handler(sid) -> None(終了/中止) or 次回までの秒数


def rotation_step(sid):
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return None
        r = rotation_state(s)
        if not r:
            return None
        phase = r.get("phase")
    fn = ROTATION_STEPS.get(phase)
    if fn is None:
        # 未知 phase（typo・旧版の残骸）は回転ごと中止。放置すると resume_scan が拾い続ける
        rotation_fail(sid, f"未知のフェーズ: {phase}")
        return None
    return fn(sid)
```

---

### P2. 検知（武装）と本文確定

#### P2-1. `rotation_maybe_arm`（`/api/send` のロック内から呼ぶ・RPC なし）

```python
def rotation_maybe_arm(s):
    """30ターン到達を検知して armed にする（冪等・数ミリ秒）。
    - 既に phase があれば何もしない
    - 非常停止 / 担当 IDE が無い / ide_poke が無い環境では回転しない
    - 中止直後のスキップ期間は武装しない
    """
    if not ROTATION_ENABLED:
        return False
    st = s["setting"]
    if not ide_poke or not (st.get("ide_session") or "").strip():
        return False
    if rotation_state(s):
        return False
    if rotation_skipping(s):
        return False
    in_file = user_turns_of(s.get("messages", []))
    if in_file < ROTATION_TURNS:
        return False
    st["rotation"] = {
        "phase": "armed",
        "rot_no": int(st.get("rotation_count") or 0) + 1,
        "armed_turn": in_file,
        "old_ide": st.get("ide_session") or "",
        "old_ide_title": st.get("ide_title") or "",
        "old_model": st.get("ide_model") or "",
        "old_choices_ide": st.get("choices_ide_session") or "",
        "old_choices_ide_title": st.get("choices_ide_title") or "",
        "old_choices_model": st.get("choices_ide_model") or "",
        "attempts": {},
        "since": now(), "updated": now(),
    }
    print(f"[rot] {s['id'][:6]} armed (turn {in_file}, rot {st['rotation']['rot_no']})")
    return True
```

呼び出しは P0-1 の `/api/send` と P2-4 のみ（二重に書かない）。

#### P2-2. プロンプト（30ターン目の節）

`build_context` の既存ブロック（`if file_user_turns >= ROTATION_TURNS:`）を差し替え:

```python
    # 世代交代の節目（armed のときだけ・同じターンで1回だけ）
    r = rotation_state(s)
    rot_armed = (r.get("phase") == "armed" and file_user_turns <= int(r.get("armed_turn") or 0))
    if rot_armed or (not r and file_user_turns >= ROTATION_TURNS and not rotation_skipping(s)):
        rot_no_next = int(st.get("rotation_count") or 0) + 2
        sections.append({
            "label": f"【最重要】セッション世代交代（通算{total_turns_val}ターン・要約圧縮）のターン",
            "text": (
                f"今回の応答はセッション世代交代の節目（第{rot_no_next}世代への移行・通算{total_turns_val}ターン目）です。\n"
                "返答本文（text）を先に書き、同じ応答の中で memo に "
                "これまでの全会話を総括した詳細なあらすじ・出来事史・現在の状況・関係性を全文出力してください。\n"
                f"次スレッドには直近{ROTATION_KEEP_TURNS}ターンの会話ログと長期記憶（RAG）もそのまま引き継がれるため、"
                "memo には主にそれより前の出来事を整理して書いてください。\n"
                "この応答で、会話ログの退避・新しい会話の作成・ファイル操作などを自分で行う必要はありません"
                "（アプリが応答の取り込み後に自動で行います）。"
            )
        })
    elif len(asst_after) >= MEMO_REMIND_TURNS:
        ...  # 既存の定期リマインド（elif を維持＝30ターン目に二重に出さない）
```

> `file_user_turns <= armed_turn` の条件が I6 の要。ユーザーが30ターン目の返事を待たずに31通目を
> 送っても、31通目のプロンプトに要約要求が二重に出ない。

#### P2-3. 本文確定フック（`process_outbox`）

現行の

```python
            has_pending = ...
            should_rotate = (...) and not has_pending
        if should_rotate:
            try:
                rotate_session(sid)
            except Exception as e:
                print(f"[outbox] rotate_session error {sid[:6]}: {e}")
        poke_event.set()
```

を:

```python
            note_reply(s, reply_to, msg)
            ready_sid = sid          # ロックを出てから回転を進める
        # ---- ロック外 ----
        if ready_sid:
            rotation_note_body(ready_sid)   # 数ミリ秒・RPC なし（I1）
        poke_event.set()
```

`ready_sid = ""` を `for name in sorted(entries):` の直後で初期化すること
（`continue` する分岐が複数あるため）。

```python
def rotation_note_body(sid):
    """本文が確定した直後に呼ぶ（I1: RPC 禁止・数ミリ秒）。
    armed を archiving に進める。armed が無い旧セッションはここで作る。"""
    with lock:
        try:
            s = load(sid)
        except (OSError, ValueError):
            return
        st = s["setting"]
        r = rotation_state(s)
        if not r:
            if not rotation_maybe_arm(s):
                return
            r = rotation_state(s)
        if r.get("phase") != "armed":
            return                      # すでに進行中（冪等）
        if user_turns_of(s["messages"]) < ROTATION_TURNS:
            return
        # 【重要】ここで rotation_idle を要求してはいけない。
        # ユーザーが応答を待たずに連投すると pending が消えず、退避も新規作成も永久に始まらない。
        # 「未応答ゼロ」を要求するのは cutover の1箇所だけ（I7）。
        r["phase"] = "archiving"
        r["settle_since"] = now()
        r["updated"] = now()
        st["rotation"] = r
        save(s)
    rotation_schedule(sid, 0.0)
```

consolidation 分岐の `rotate_ide(sid)` も `rotation_note_body(sid)` に置換し、
`rotate_ide = rotate_session` の別名は `rotate_ide = rotation_note_body` にする（互換）。

#### P2-4. `poke_worker` の全体走査（保険）

`rotate_targets` ブロックを差し替え（通常は P2-1/P2-3 で発火する）:

```python
                    if rotation_state(s):
                        pass
                    elif user_turns_of(msgs) >= ROTATION_TURNS and not has_pending:
                        if rotation_maybe_arm(s):
                            save(s)
```

（`s` はそのループで `load` 済みのものを使用。`rotation_maybe_arm` はロック内専用）

#### P2-5. 旧 `rotate_session` の解体

P3〜P7 が揃ったら `def rotate_session(sid):` 〜 `rotate_ide = rotate_session` を削除する。
途中で残すと新旧が干渉するので「先に呼び出し元を全部外す → 最後に本体削除」（§P9）。

---

### P3. archiving

```python
def _rot_archiving(sid):
    snap = None
    path = ""
    done = False
    with lock:
        s = load(sid)
        st = s["setting"]
        r = rotation_state(s)
        if not r:
            return None
        rot_no = int(r.get("rot_no") or 0)
        total = int(st.get("total_turns") or 0)
        path = os.path.join(ARCHIVE_DIR, f"{sid}_rot{rot_no}_turn{total}.json")
        done = os.path.exists(path)
        if not done:
            snap = clone_session(s)
            sdata = load_states_data(sid, readonly=True)
            idata = load_images_data(sid, readonly=True)
            for m in snap.get("messages", []):
                mid = m.get("id")
                if not mid:
                    continue
                if mid in sdata:
                    m["state"] = copy.deepcopy(sdata[mid])
                if mid in idata:
                    im = m.setdefault("image", {})
                    for k, v in idata[mid].items():
                        im[k] = copy.deepcopy(v)
            # I8: 回転前の自己完結スナップショット（進行中の状態を持ち込まない）
            for k in ("rotation", "rotation_notice", "rotation_skip_until", "rotation_reserve"):
                snap["setting"].pop(k, None)
    if snap is not None:
        try:
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            write_json(path, snap)          # ロック外で書く（大きくても API を止めない）
            print(f"[rot] {sid[:6]} archived {os.path.basename(path)}")
        except OSError as e:
            print(f"[rot] {sid[:6]} archive error: {e}")   # 退避失敗は致命的ではない
            path = ""
    rotation_put(sid, phase="settle", archive=path, settle_since=now())
    return 0.0


ROTATION_STEPS["archiving"] = _rot_archiving
```

> `settle_since` を**ここで**入れる。`since`（armed 時刻）を使うと数十秒経過ケースで待ちゼロになる。

---

### P4. spawning（`new_ide` に `assign=False` を追加）

#### P4-1. `new_ide` の改修

シグネチャ: `def new_ide(sid, auto=False, target="main", assign=True):`

割当部分を分岐:

```python
        with lock:
            s = load(sid)
            st = s["setting"]
            if not assign:
                # 予備作成: 担当はまだ切り替えない（I3）。AGENTS.md も cutover で書く
                r = rotation_state(s)
                if not r:
                    return None, None, "回転が中止された", None
                key_cid = "new_choices_ide" if target == "choices" else "new_ide"
                key_title = "new_choices_ide_title" if target == "choices" else "new_ide_title"
                r[key_cid] = cid
                r[key_title] = title
                r["updated"] = now()
                st["rotation"] = r
                save(s)
                return cid, title, "予備作成", folder
            # 以下は従来どおり（target 分岐・sync_agents）
```

**`_new_ide_in_progress` のキーは `(sid, target)` のまま**（`assign` を混ぜない）。
目的は「同じチャンネル・同じ target の会話を二重に作らない」こと。`assign` は作り方の違いでしかなく、
キーに混ぜると**予備作成と通常作成が同時に走れてしまう**（排他が効かない）。

```python
    key = (sid, target)          # assign は入れない（同じ部屋の鍵は1本）
    with _new_ide_guard:
        if key in _new_ide_in_progress:
            return None, None, "既に作成中", None
        _new_ide_in_progress.add(key)
```

#### P4-2. settle

```python
def _rot_settle(sid):
    with lock:
        s = load(sid)
        r = rotation_state(s)
        if not r:
            return None
        old_ide = r.get("old_ide") or ""
        settle_since = r.get("settle_since") or r.get("updated") or now()
    if not old_ide or not ide_poke or not hasattr(ide_poke, "progress"):
        rotation_put(sid, phase="spawn_main")
        return 0.0
    try:
        pg, _ = ide_poke.progress(old_ide)
    except Exception:
        pg = None
    if bool(pg and pg.get("running")) and age(settle_since) < ROT_SETTLE_SEC:
        return 1.0
    rotation_put(sid, phase="spawn_main")
    return 0.0


ROTATION_STEPS["settle"] = _rot_settle
```

> 旧会話が走行中でも `StartCascade` は可能。待つのは「画面のフォーカスが飛ぶ」「走行中に rename する」
> のを避けるためで、`ROT_SETTLE_SEC` を超えたら強行する（止まらないことを優先）。

#### P4-3. spawn（`spawn_main` → `spawn_choices` → `bootstrap_main` 固定）

```python
def _rot_spawn(sid, target):
    nxt = "spawn_choices" if target == "main" else "bootstrap_main"
    key_cid = "new_ide" if target == "main" else "new_choices_ide"
    key_res = "ide" if target == "main" else "choices_ide"
    key_title = "new_ide_title" if target == "main" else "new_choices_ide_title"
    att = {}
    n = 0
    with lock:
        s = load(sid)
        r = rotation_state(s)
        if not r:
            return None
        st = s["setting"]
        # choices を作る必要が無ければ飛ばす（判断をここに一本化：迷子のフェーズを作らない）
        if target == "choices" and not (r.get("old_choices_ide") or ""):
            rotation_put(sid, phase="bootstrap_main")
            return 0.0
        # 再起動などで「作成済みだが phase が進んでいない」場合は adoption（増殖防止）
        if r.get(key_cid):
            rotation_put(sid, phase=nxt)
            return 0.0
        # 予備会話の再利用（I11: 使ったキーだけ棚から下ろす）
        res = st.get("rotation_reserve")
        res = res if isinstance(res, dict) else {}
        res_cid = (res.get(key_res) or "").strip()
        if res_cid and ide_poke:
            alive = False
            try:
                alive = bool(ide_poke.locate(res_cid)[0])
            except Exception:
                alive = False
            res.pop(key_res, None)                  # 生きていても死んでいても棚から下ろす
            if res.get("ide") or res.get("choices_ide"):
                st["rotation_reserve"] = res
            else:
                st.pop("rotation_reserve", None)
            if alive:
                r[key_cid] = res_cid
                r[key_title] = res.get("title") or ""
                st["rotation"] = r
                save(s)
                rotation_put(sid, phase=nxt)
                print(f"[rot] {sid[:6]} reuse reserve {res_cid[:8]} ({target})")
                return 0.0
            save(s)   # 死んでいた予備を消したので確定
        att = r.get("attempts") or {}
        key = "spawn" if target == "main" else "spawn_choices"
        n = int(att.get(key) or 0)
        if n >= ROT_SPAWN_TRIES:
            if target == "choices":
                rotation_put(sid, phase="bootstrap_main")   # choices は諦めて続行
                return 0.0
            rotation_fail(sid, "Antigravity の新規会話を作れない")
            return None
        r["attempts"] = dict(att, **{key: n + 1})
        st["rotation"] = r
        save(s)
    cid, title, msg, _folder = new_ide(sid, target=target, assign=False)
    if not cid:
        if msg == "既に作成中":
            # 他（ide_auto_worker / 手動）が作成中。**待つだけで残機は減らさない**（I10）
            with lock:
                s2 = load(sid)
                r2 = rotation_state(s2)
                if r2:
                    r2["attempts"] = dict(r2.get("attempts") or {}, **{key: n})
                    s2["setting"]["rotation"] = r2
                    save(s2)
            return 2.0
        print(f"[rot] {sid[:6]} {target} 作成失敗: {msg}")
        return 5.0 * (n + 1)            # 5,10,15秒後に再試行（次回は adoption が先に走る）
    rotation_put(sid, phase=nxt)
    return 0.0


ROTATION_STEPS["spawn_main"] = lambda sid: _rot_spawn(sid, "main")
ROTATION_STEPS["spawn_choices"] = lambda sid: _rot_spawn(sid, "choices")
```

> **注意**: `_rot_spawn` は `with lock` の中で `rotation_put`/`rotation_fail` を呼ぶが、
> どちらも短いロック＋ファイルI/Oのみ（RPC なし）。`new_ide` の RPC はロックの外（I2）。

---

### P5. bootstrap

`send_poke` / `poke_text` に `wait` と `model` を追加:

```python
def poke_text(ide, full=False, wait=None):
    ...
    w = 30 if wait is None else int(wait)      # 通常の合図は従来どおり 30 秒
    return (f"（ロールプレイチャットアプリからの合図）このチャットに新しい入力が来た。次のコマンドを実行し、表示に従って応答すること:\n"
            f'python "{script}" --ide {ide} --wait {w}\n'
            f"これ以降、この会話に半角スペース1文字だけが届いたら、それが同じ合図。同じコマンドを実行すること。")


def send_poke(ide, full=False, media=None, model=None, wait=None):
    text = poke_text(ide, full, wait)
    ...
                ok, msg, retry = ide_poke.send_to_conversation(ide, text, model or ide_model_of(ide), media or None)
```

> **重要**: 予備会話はまだ担当チャンネルに紐づかないので `ide_model_of(ide)` が空になり、
> `_borrowed_config` が **別の会話のモデルを借りる**。必ず `model=` を明示する。

```python
def _rot_bootstrap(sid, target):
    nxt = "bootstrap_choices" if target == "main" else "verify_ready"
    with lock:
        s = load(sid)
        r = rotation_state(s)
        if not r:
            return None
        cid = r.get("new_choices_ide") if target == "choices" else r.get("new_ide")
        model = r.get("old_choices_model") if target == "choices" else r.get("old_model")
        att = r.get("attempts") or {}
        key = "bootstrap_" + target
        n = int(att.get(key) or 0)
    if not cid:
        rotation_put(sid, phase=nxt)
        return 0.0
    # 新規会話は status が None のことがある。busy のときは待つ（**残機を減らさない**：I10）
    try:
        ok_idle, _st = ide_poke.idle(cid)
    except Exception:
        ok_idle = True
    if not ok_idle:
        return 2.0
    if n >= ROT_BOOTSTRAP_TRIES:
        if target == "choices":
            rotation_put(sid, phase="verify_ready")   # choices は諦めて続行
            return 0.0
        rotation_fail(sid, "新しい会話にブートストラップを送れない")
        return None
    rotation_put(sid, attempts=dict(att, **{key: n + 1}))   # attempts は丸ごと置換
    ok, msg, retry = send_poke(cid, full=True, model=model or "", wait=ROT_BOOTSTRAP_WAIT)
    if not ok:
        print(f"[rot] {sid[:6]} bootstrap {target} NG: {msg}")
        return 2.0 if retry else 5.0
    rotation_put(sid, phase=nxt)
    return 0.0


ROTATION_STEPS["bootstrap_main"] = lambda sid: _rot_bootstrap(sid, "main")
ROTATION_STEPS["bootstrap_choices"] = lambda sid: _rot_bootstrap(sid, "choices")
```

`send_poke_text`（合図ではなく任意の本文を送る）:

```python
def send_poke_text(ide, text, model=None):
    """合図以外の本文を会話へ送る（疎通テスト用）。(ok, msg, retry)"""
    if not ide_poke:
        return False, "この環境では送れない", False
    with get_poke_lock(ide):
        try:
            return ide_poke.send_to_conversation(ide, text, model or ide_model_of(ide))
        except Exception as e:
            return False, f"送れなかった: {e}", False
```

---

### P6. 検証（準備完了テスト）

`ide_poke.py` に3関数追加:

```python
def summary(cid):
    """会話の概要（conversation_summaries の行）。読めなければ None"""
    port, tok, s = locate(cid)
    return s if port else None


def idle(cid):
    """その会話が待機中か。(bool, status文字列)"""
    port, tok, s = locate(cid)
    if not port:
        return False, "not found"
    st = s.get("status") or ""
    return st in (IDLE, "", "CASCADE_RUN_STATUS_UNSPECIFIED"), st


def last_reply(cid, after_step=0):
    """最後の返事（PLANNER_RESPONSE.response の全文）とエラー。
    progress() は 110 字に切り詰めるので検証用に別途用意する。
    after_step: この絶対ステップ番号**以前**の返事は無視する（テスト送信前の返事を拾わない）。"""
    port, tok, s = locate(cid)
    if not port:
        return None, None, "会話が見つからない"
    n = int(s.get("stepCount") or 0)
    start = max(0, n - 30)
    code, d = _call(port, tok, "GetCascadeTrajectorySteps",
                    {"cascadeId": cid, "stepOffset": start}, timeout=20)
    if code != 200:
        return None, None, f"読めなかった（{code}）"
    text, err = "", ""
    for j, x in enumerate(d.get("steps") or []):
        if start + j <= int(after_step or 0):
            continue
        t = (x.get("type") or "").replace("CORTEX_STEP_TYPE_", "")
        if t == "PLANNER_RESPONSE":
            pr = x.get("plannerResponse") or {}
            if not (pr.get("toolCalls") or []):
                rr = (pr.get("response") or "").strip()
                if rr:
                    text = rr
        elif t == "ERROR_MESSAGE":
            e = (x.get("errorMessage") or {}).get("error") or {}
            err = e.get("shortError") or e.get("userErrorMessage") or "error"
    return text, err, "ok"
```

検証ハンドラ:

```python
ROT_VERIFY_SEND = (
    "【疎通テスト】これはアプリの接続テストです。ここではコマンド（wait_input.py 等）を実行しないでください。"
    "ロールプレイ本文も outbox への JSON も書かず、返答は次の5文字だけにしてください:\n"
    f"{ROT_VERIFY_TEXT}\n"
    "（前置き・説明・コードブロック・記号・後書きは一切書かないこと。このテストは以後の会話の流れとは無関係です）"
)


def _rot_verify_ready(sid):
    with lock:
        s = load(sid)
        r = rotation_state(s)
        if not r:
            return None
        cid = r.get("new_ide") or ""
        att = r.get("attempts") or {}
        n = int(att.get("ready_poll") or 0)
        boot = int(att.get("bootstrap_main") or 0)
    if not cid:
        rotation_fail(sid, "新しい会話が無い")
        return None
    try:
        idle_now, _st = ide_poke.idle(cid)
    except Exception:
        idle_now = True
    if not idle_now:
        if n * ROT_VERIFY_POLL > ROT_VERIFY_TIMEOUT:
            rotation_fail(sid, "新しい会話がブートストラップで止まらない")
            return None
        rotation_put(sid, attempts=dict(att, ready_poll=n + 1))
        return ROT_VERIFY_POLL
    # ブートストラップのコマンドが実際に走ったか（/api/pending?ide= 到達で mark_known される）
    if not ide_is_known(cid):
        print(f"[rot] {sid[:6]} bootstrap 未実行 → 送り直し")
        rotation_put(sid, phase="bootstrap_main", attempts=dict(att, ready_poll=0))
        return 0.0
    rotation_put(sid, phase="verify_test", attempts=dict(att, ready_poll=0))
    return 0.0


ROTATION_STEPS["verify_ready"] = _rot_verify_ready


def _rot_verify_test(sid):
    """合否: 必須 = (1) ide_known (2) テスト送信より後の返事がある (3) エラーが無い。
    加点 = 「準備完了」を含む。I10: 残機（verify）は送信結果が出たときだけ +1。"""
    with lock:
        s = load(sid)
        r = rotation_state(s)
        if not r:
            return None
        cid = r.get("new_ide") or ""
        model = r.get("old_model") or ""
        att = r.get("attempts") or {}
        tries = int(att.get("verify") or 0)
        sent = bool(att.get("test_sent"))
        polls = int(att.get("verify_poll") or 0)
        after_step = int(att.get("verify_after_step") or 0)
    if not cid:
        rotation_fail(sid, "新しい会話が無い")
        return None

    # ---- 送信前: ブートストラップのターンが終わるのを待つ ----
    if not sent:
        try:
            idle_now, _ = ide_poke.idle(cid)
        except Exception:
            idle_now = True
        if not idle_now:
            return ROT_VERIFY_POLL
        sm = ide_poke.summary(cid) or {}
        after_step = int(sm.get("stepCount") or 0)     # ← テスト前のステップ番号を控える
        # テスト本文は「合図」ではなく普通のメッセージとして送る（send_poke は使わない）
        ok, msg, retry = send_poke_text(cid, ROT_VERIFY_SEND, model=model)
        if not ok:
            if retry:
                return ROT_VERIFY_POLL                # 会話が応答中。残機は減らさない
            rotation_put(sid, attempts=dict(att, verify=tries + 1, test_sent=False,
                                            verify_poll=0, verify_after_step=after_step))
            return _rot_verify_retry(sid, f"疎通テストを送れない: {msg}")
        rotation_put(sid, attempts=dict(att, verify=tries + 1, test_sent=True,
                                        verify_poll=0, verify_after_step=after_step))
        return ROT_VERIFY_POLL

    # ---- 送信後: まずターンが終わるのを待ってから読む ----
    if polls * ROT_VERIFY_POLL > ROT_VERIFY_TIMEOUT:
        return _rot_verify_retry(sid, "疎通テストの応答が終わらない（タイムアウト）")
    try:
        idle_now, _ = ide_poke.idle(cid)
    except Exception:
        idle_now = True
    if not idle_now:
        rotation_put(sid, attempts=dict(att, verify_poll=polls + 1))
        return ROT_VERIFY_POLL

    try:
        text, err, _ = ide_poke.last_reply(cid, after_step=after_step)
    except Exception as e:
        text, err = None, str(e)
    if err:
        return _rot_verify_retry(sid, f"新しい会話でエラー: {err}")
    if text is None:
        return _rot_verify_retry(sid, "新しい会話の返答を読めない")
    if not text.strip():
        # テスト送信より後の返事がまだ無い（生成中 or 無出力）
        rotation_put(sid, attempts=dict(att, verify_poll=polls + 1))
        return ROT_VERIFY_POLL
    # 合格: コマンドが走っていて（known）、テスト後の返事が空でない。
    # 「準備完了」が無くても、AGENTS.md どおりの半角スペース1文字は正常なので合格にする。
    if not ide_is_known(cid):
        return _rot_verify_retry(sid, "ブートストラップのコマンドが実行されていない")
    print(f"[rot] {sid[:6]} verify OK ({'準備完了' if ROT_VERIFY_TEXT in text else 'space'})")
    rotation_put(sid, phase="cutover", attempts=dict(att, verify_poll=0))
    return 0.0


ROTATION_STEPS["verify_test"] = _rot_verify_test


def _rot_verify_retry(sid, why):
    """検証失敗。累積 verify は**据え置き**（残機は減るだけ）。ロック外で rename する。"""
    drop = None
    do_fail = False
    tries = 0
    with lock:
        s = load(sid)
        r = rotation_state(s)
        if not r:
            return None
        att = r.get("attempts") or {}
        tries = int(att.get("verify") or 0)
        cid = r.get("new_ide") or ""
        if cid:
            drop = (cid, "【失敗】" + (r.get("new_ide_title") or "世代交代の新規会話"))
        r["new_ide"] = ""                 # 失敗会話は予備に回さない（rotation_fail が拾わない）
        r["new_ide_title"] = ""
        r["attempts"] = dict(att, test_sent=False, verify_poll=0, verify_after_step=0)
        r["error"] = why
        do_fail = tries >= ROT_VERIFY_ROUNDS
        if not do_fail:
            r["phase"] = "spawn_main"
        s["setting"]["rotation"] = r
        save(s)
    if do_fail:
        rotation_fail(sid, why)           # ロック内処理のみ（RPC なし）
    if drop and ide_poke and hasattr(ide_poke, "rename"):
        try:
            ide_poke.rename(drop[0], drop[1])     # I2: RPC はロック外
        except Exception as e:
            print(f"[rot] {sid[:6]} rename failed conv: {e}")
    print(f"[rot] {sid[:6]} verify retry ({tries}/{ROT_VERIFY_ROUNDS}): {why}")
    return None if do_fail else 0.0
```

---

### P7. cutover

剪定を関数化（旧 `rotate_session` から抽出）:

```python
def prune_session_messages(s, keep_turns=ROTATION_KEEP_TURNS):
    """直近 keep_turns ユーザーターンだけ残す。states/images/choices は save() が同期プルーニングする。
    total_turns は**触らない**（I12）。"""
    msgs = s.get("messages", [])
    user_indices = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    if len(user_indices) > keep_turns:
        cut = user_indices[-keep_turns]
        s["messages"] = msgs[cut:]
        # 画像の生成待ちが迷子にならないよう、消える mid をキューから落とす
        remain = {m.get("id") for m in s["messages"] if m.get("id")}
        for _ in range(len(queued_images)):
            try:
                q_sid, q_mid = queued_images.popleft()
            except IndexError:
                break
            if q_sid != s.get("id") or q_mid in remain:
                queued_images.append((q_sid, q_mid))
    return s
```

```python
def _rot_cutover(sid):
    r = {}
    cid = ""
    sync_agents_needed = False
    with lock:
        s = load(sid)
        st = s["setting"]
        r = rotation_state(s)
        if not r or r.get("phase") != "cutover":
            return None
        if not rotation_idle(s):
            # I7: 未応答が残っている間は絶対に切り替えない（二重応答の防止）
            ws = r.get("wait_since") or now()
            r["wait_since"] = ws
            st["rotation"] = r
            save(s)
            if age(ws) > ROT_WATCHDOG_SEC:
                rotation_fail(sid, "未応答の発言が残ったまま10分経過")
                return None
            return 1.0
        r.pop("wait_since", None)     # idle に戻ったら待ち開始をクリア（watchdog 誤発火防止）

        cid = r.get("new_ide") or ""
        if not cid:
            r["phase"] = "spawn_main"
            st["rotation"] = r
            save(s)
            return 0.0
        old_ide = st.get("ide_session") or ""
        if old_ide and r.get("old_ide") and old_ide != r.get("old_ide"):
            rotation_fail(sid, "世代交代中に担当の会話が手動で変更された")
            return None

        st["ide_session"] = cid
        st["ide_title"] = r.get("new_ide_title") or ""
        st["ide_auto"] = False
        if r.get("new_choices_ide"):
            st["choices_ide_session"] = r["new_choices_ide"]
            st["choices_ide_title"] = r.get("new_choices_ide_title") or ""
            st["choices_ide_auto"] = False
            if not (st.get("choices_ide_model") or "").strip() and r.get("old_choices_model"):
                st["choices_ide_model"] = r["old_choices_model"]

        # 通算ターンは触らない（I12）。剪定だけ行う
        st["rotation_count"] = int(r.get("rot_no") or 0)
        st["last_rotation_turn"] = int(st.get("total_turns") or 0)
        st["turns_since_rotation"] = 0
        st["rotation_skip_until"] = 0
        st["rotation_notice"] = (
            f"🔄 セッションを世代交代しました（{now()[11:16]} / "
            f"第{int(r.get('rot_no') or 0) + 1}世代へ移行・通算{int(st.get('total_turns') or 0)}ターン）"
        )
        prune_session_messages(s)
        st.pop("rotation", None)
        st.pop("rotation_reserve", None)
        sync_agents_needed = bool(st.get("agents_md"))
        save(s)
        # 送信履歴は切替と同時にリセット（次は全部送る）
        write_sent(sid, {"ide": "", "msgs": [], "reset": True, "ts": now(),
                         "why": "セッション自動ローテーション（世代交代）"})

    # ---- ロック外 ----
    if sync_agents_needed:
        try:
            with lock:
                s2 = load(sid)
                if s2["setting"].get("agents_md"):
                    sync_agents(s2)      # 新しい ide で AGENTS.md を書き直す
                    save(s2)
        except Exception as e:
            print(f"[rot] {sid[:6]} agents error: {e}")
    # 旧会話の rename（実際に差し替えたものだけ。失敗しても運用に影響しない）
    old_roles = [(r.get("old_ide"), r.get("old_ide_title"))]
    if r.get("new_choices_ide"):
        old_roles.append((r.get("old_choices_ide"), r.get("old_choices_ide_title")))
    for oid, otitle in old_roles:
        if oid and ide_poke and hasattr(ide_poke, "rename"):
            try:
                if not (otitle or "").startswith("【旧】"):
                    ide_poke.rename(oid, "【旧】" + (otitle or "旧会話"))
            except Exception as e:
                print(f"[rot] {sid[:6]} old rename error: {e}")
    print(f"[rot] {sid[:6]} cutover -> {cid[:8]} (rot {r.get('rot_no')})")
    poke_event.set()          # 次ターンの合図を即許可
    return None


ROTATION_STEPS["cutover"] = _rot_cutover
```

> **順序が重要**: `sync_agents` は assignment 後（新しい ide で書く）。旧会話の rename は
> cutover 後（失敗しても運用に影響しない）。`old_choices_ide` は**差し替えたときだけ** rename
> （差し替えていない＝現役のままなので rename しない）。

---

### P8. 失敗・中止

- `rotation_fail` は §2-5。`rotation_notice` に出るのでクライアントが flash 表示（`app.js:2384`）。
- `/api/ide/assign` / `/api/ide/new` のハンドラ冒頭で、回転中なら中止（worker が後から上書きしない）:

```python
                if rotation_state(s):
                    rotation_fail(s["id"], "手動で担当の会話が変更された")
```

- 再生成/巻き戻し/編集は **拒否せず、回転を中止して通す**（§P0-1-B に組み込み済み）。

### P9. 旧コード削除

- `rotate_session` 本体と `rotate_ide = rotate_session` を削除（`rotate_ide = rotation_note_body` に）
- `grep -n "ROTATION_PERIOD"` → **残ってよいのは `total_turns_base` と読み取り専用フォールバックだけ**
- `test_new_session_fix.py` の `rotation_notice` 除去テストに `rotation` / `rotation_reserve` を追加

### P10. UI（任意・小さく）

`public/app.js` の `render()` 内、pill の組み立ての直後（`state` はグローバル変数）:

```js
  const rotPhase = state?.setting?.rotation?.phase;
  if (rotPhase && rotPhase !== "armed") {
    pill.className = "pill wait";
    pill.textContent = rotPhase === "cutover" ? "新しいセッションへ切替中…" : "世代交代を準備中…";
  }
```

（`waitingInput` があるときはそちらを優先。`sendBtn.disabled` は変更しない＝旧会話で応答を続けられる）

---

## 5. スレッド / ロックの規則（レビュー用チェックリスト）

| 規則 | 理由 |
|---|---|
| `lock` を保持したまま `ide_poke.*` / `new_ide` / `send_poke` を呼ばない | RPC は最大十数秒かかり、`load()` が `lock` を取るため全 API ポーリングが止まる |
| `lock` を保持したまま大きな `write_json` をしない | 150KB でも全 API が待つ（`_rot_archiving` はロック外で書く） |
| `rotation_event.set()` は phase 書き込みの**後** | 先に set すると worker が古い phase を読む |
| `rotation_queue` の変更は `rotation_guard` のみ | `lock` と混ぜるとロック順序が不定になる |
| worker は `time.sleep` しない（`next_at` を返す） | 1チャンネルの検証が他を止める |
| RPC の結果（cid）は**すぐ**永続化 | 再起動で会話が迷子にならない |
| `_new_ide_in_progress` のキーは `(sid, target)` | `assign` を混ぜると排他が効かない |
| 残機（attempts）は**減るだけ**。busy 待ちは減らさない | 無限作成/無限リトライの防止（I10） |

---

## 6. 検証の合格条件（`verify_test`）

| 判定 | 条件 |
|---|---|
| 必須1 | `ide_is_known(new_ide)` = ブートストラップのコマンドが実際に走った（`mark_known` は `/api/pending?ide=` で呼ばれる） |
| 必須2 | `last_reply(after_step=verify_after_step)` の text が空でない（**テスト送信より後**の返事であること） |
| 必須3 | `last_reply()` の error が空（`ERROR_MESSAGE` ステップが無い） |
| 必須4 | ターンが `idle` に戻っている |
| 加点 | text に `準備完了` を含む |
| 不合格 | 上記が欠ける / タイムアウト（`ROT_VERIFY_TIMEOUT`） |

**半角スペースだけの返答は正常**（AGENTS.md の指示どおり）。ここを「不合格」にすると
正常な会話を捨てて無限に作り直すので注意。

---

## 7. テスト計画

`test/test_rotation_pipeline.py` を新規（`test_new_session_fix.py` の setUp を流用し、`ide_poke` をスタブ化）。

| # | テスト | 期待 |
|---|---|---|
| T1 | `rotation_maybe_arm` が29で False / 30で True / 二重呼び出しで1回だけ | `phase=="armed"`, `armed_turn==30` |
| T2 | `rotation_skipping` 中は武装しない／非常停止（`ROTATION_ENABLED=False`）でも武装しない | `rotation` なし |
| T3 | import → `rotation_note_body` で **未応答があっても** `archiving` へ進む（idle 要求は cutover のみ） | phase 遷移 |
| T4 | アーカイブに `rotation`/`rotation_notice`/`rotation_reserve` が無い | `assertNotIn` |
| T5 | `_rot_archiving` を2回呼んでもファイルが増えない・内容が壊れない | 冪等 |
| T6 | `prune_session_messages` 後も `total_turns` が変わらない／`calc_turn_idx` が連番 | I12 |
| T7 | `/api/send` → `total_turns` が +1（回転後 30→31） | 単調増加 |
| T8 | `total_turns=0`＋`rotation_count=2`＋in_file=11 で send → 51（37 に戻らない） | `total_turns_base` |
| T9 | truncate で `total_turns` が `removed` 分だけ戻る（0未満にならない） | 巻き戻り無し |
| T10 | `_rot_cutover` は未応答があると切り替えない（`ide_session` 不変） | I7 |
| T11 | `rotation_idle` が choices/memory pending を無視する | 回転が止まらない |
| T12 | `_rot_verify_test`: 空応答→リトライ / スペース＋known→合格 / `after_step` より前の返事は無視 | 誤判定しない |
| T13 | テスト送信失敗（retry=False）で **verify が +1** され、`ROT_VERIFY_ROUNDS` で必ず `rotation_fail` | 無限作成しない |
| T14 | `_rot_verify_retry` は `verify` を 0 に戻さない（2ラウンド目で止まる） | I10 |
| T15 | `_rot_spawn`: 予備の `ide` だけ消費し `choices_ide` が残る／死んだ予備は消える／`"既に作成中"` で残機が減らない | I11 / I10 |
| T16 | `new_ide(assign=False)` が `ide_session` を変えない | I3 |
| T17 | `_new_ide_in_progress` が `(sid,target)` で排他（assign 違いでも同時に作らない） | §P4-1 |
| T18 | `write_sent` の `reset` が `build_context` で消えない／`note_reply` で消える | 差分落ち防止 |
| T19 | `repair_outbox_text` が実例（`"inner: "…`）を修復し、生改行入り JSON を `strict=False` で読む／壊れたままなら `None` | P0-9 |
| T20 | `rotation_fail` 後 3ターンは再武装しない | `skip_until` |
| T21 | 既存 `test_new_session_fix.py` / `test_parallel_choices.py` が全通過 | 回帰なし |
| T22 | `rotation_resume_scan` は `armed` を拾わない（無限ループ防止） | 実装済み |
| T23 | 選択肢ありチャンネルの完全パイプライン（AGENTS.md 同期含む） | 実装済み |
| T27 | 担当Aに合図済み→担当をBに変更→**Bが受け取ってclaimできる**（刻印の退行防止） | 修正の回帰テスト |
| T28 | `reply_to` の無い本文は、未応答が無ければ `.stale` に退避（孤児の発言を作らない） | P0-9 |
| T29 | ブートストラップの「応答中待ち」に上限（main=中止 / choices=続行） | 修正の回帰テスト |
| T30 | 疎通テストの送信前待ちに上限（残機は増やさない） | 修正の回帰テスト |
| T31 | ブートストラップ文だけが AGENTS.md の古い `--ide` を打ち消す | 修正の回帰テスト |

---

## 8. リスクと切り戻し

| リスク | 影響 | 対策 |
|---|---|---|
| `new_ide(assign=False)` の改修ミス | 予備会話が担当に付く | `assign=False` で `st["ide_session"]` を触らない（T16） |
| `total_turns` 改修ミス | 記憶検索のターン番号ずれ | 変更は2箇所だけ（P0-1）。`calc_turn_idx` は無変更（T6/T7/T8） |
| worker の暴走（無限作成） | 会話が増殖 | 残機は減るだけ（I10）＋`_new_ide_in_progress`(T17)＋`ROT_*_TRIES` |
| cutover が永久に来ない | 回転が終わらない | `ROT_WATCHDOG_SEC` で `rotation_fail`（旧会話のまま） |
| 検証の誤判定 | 正常会話を捨てる | §6（スペース許容）＋`after_step`（T12） |
| 既存セッション（`rotation` 無し・30ターン超） | 次ターンで武装される | `rotation_note_body` が `rotation_maybe_arm` を内包 |
| 切り戻し | — | `RPCHAT_ROTATION=0` で武装停止。進行中は I3/I9 で安全に完走/中止。`setting.rotation` を `pop` すれば即座に従来動作 |

---

## 9. 作業順序チェックリスト

1. [ ] P0-1 `total_turns`（`total_turns_base`）＋ `before_turns` → T7/T8/T9
2. [ ] P0-2 `clone_session` に `rotation` / `rotation_reserve`
3. [ ] P0-3 `write_sent` の `reset` 保持 → T18
4. [ ] P0-4/5/6 除去・リセット・フォーム保持
5. [ ] P0-7 刻印の廃止（`poke_worker` の書き込み・`/api/pending` の `_claimable`・`/api/claim` の拒否）→ T27
6. [ ] P0-9 `repair_outbox_text` と「宛先の無い本文は捨てる」→ T19 / T28
7. [ ] P1 定数・ヘルパ・`rotation_worker` 起動
8. [ ] P2-1 `rotation_maybe_arm`＋`/api/send` → T1/T2
9. [ ] P2-2 プロンプト節（`elif` 維持）
10. [ ] P2-3 `rotation_note_body`＋`process_outbox` → T3
11. [ ] P2-4 `poke_worker` の走査差し替え
12. [ ] P3 `_rot_archiving` → T4/T5
13. [ ] P4 `new_ide(assign=False)`＋spawn → T15/T16/T17
14. [ ] P5 `poke_text(wait)`/`send_poke(model)`/bootstrap
15. [ ] P6 `ide_poke.summary|idle|last_reply`＋verify → T12/T13/T14
16. [ ] P7 `prune_session_messages`＋cutover → T6/T10/T11
17. [ ] P8 中止系（assign / new）
18. [ ] P9 旧 `rotate_session` 削除・`ROTATION_PERIOD` 残存確認
19. [ ] P10 UI pill
20. [ ] 全テスト＋実機1回転（§11 のログで確認）

---

## 10. 最終レビュー（v3）で修正した点

| # | v2 の問題 | v3 の修正 |
|---|---|---|
| 1 | 疎通テストの**送信失敗で `verify` が増えず**、`spawn_main` に戻るため会話作成が無限ループ | 送信結果が出た時点で `verify=tries+1` を必ず記録（busy 待ちだけは数えない） |
| 2 | テスト直後に `last_reply` を読むと**ブートストラップの返事**を合格判定しうる（idle 直後の競合） | `verify_after_step` を送信前に記録し、`last_reply(after_step=)` で**それ以前の返事を無視** |
| 3 | `_new_ide_in_progress` のキーに `assign` を混ぜており排他が効かない（§8 の記述も矛盾） | キーは `(sid, target)`。§8 も修正 |
| 4 | `_rot_verify_retry` が**最終失敗時に rename しない**（`return None` で下の処理に落ちない） | フラグ化して、成功/失敗どちらの経路でもロック外 rename |
| 5 | `_rot_verify_retry` の rename が `lock` 内（§5 違反） | ロック外へ移動 |
| 6 | `ROT_VERIFY_TRIES` の意味が曖昧（再試行回数？総回数？） | `ROT_VERIFY_ROUNDS`（初回を含む総ラウンド数）に改名して明記 |
| 7 | `_rot_spawn` の予備再利用で**反対側のキーまで捨てる**／死んだ予備が残る／dict でない値で例外 | `pop(key_res)` のみ・空なら key 削除・`isinstance` ガード・死んだ予備も掃除 |
| 8 | `_rot_spawn` が `"既に作成中"` を失敗として**残機消費** | 残機を戻して 2 秒後に再試行 |
| 9 | choices を作らなかったのに **現役の choices 会話を `【旧】` に rename** | `new_choices_ide` があるときだけ rename |
| 10 | `ide_pending_for` の刻印を `dispatch_poke`（スレッド）で行い、`lock` を二重に取る形 | → v4: 刻印そのものを廃止（§P0-7 の訂正） |
| 11 | `ROTATION_PERIOD` を「計算に使わない」と書きつつ `total_turns_base` で使用（矛盾） | 「補填専用」と明記し、§P9 の grep 条件も修正 |
| 12 | `_rot_archiving` が失敗時に `archive` を前回値で埋める／`snap` をロック外で参照 | `snap` をロック内で作り、書込みだけロック外。失敗時は `archive=""` |
| 13 | `T3` の期待値が「未応答なら進まない」（最終設計と矛盾） | 「未応答でも `archiving` へ進む」に修正 |
| 14 | `P10` の JS が未定義の `s0` を参照 | `state?.setting?.rotation?.phase` |
| 15 | §9 のチェックリストが重複番号・P10 抜け | 20項目に整理 |
| 16 | アーカイブから `rotation_reserve` を除去していなかった | 除去リストに追加（P0-2 の deep copy とセット） |

---

## 11. 実装後に見るログ（受け入れ確認）

```
[rot] 51a90312 armed (turn 30, rot 5)
[rot] 51a90312 archived 51a9031728_rot5_turn110.json
[rot] 51a90312 verify OK (準備完了)
[rot] 51a90312 cutover -> 9f2c1a77 (rot 5)
```

期待タイミング（1回の回転）:

| 区間 | 目安 |
|---|---|
| 本文 `save()` → `archiving` 完了 | < 0.2 秒 |
| `settle`（旧会話が落ち着くまで） | 0〜15 秒（本文生成が終わっていれば 0） |
| `spawn_main`（StartCascade） | 3〜15 秒 |
| `bootstrap_main`（`--wait 5`） | 5〜20 秒 |
| `verify_ready` + `verify_test` | 5〜40 秒 |
| **本文表示から cutover まで（裏方合計）** | **15〜70 秒**（この間もユーザーは旧会話で普通に会話できる） |
| **次のターンの待ち時間** | スペース合図のみ（`--wait 30` の 30 秒待ちが消える） |

確認ポイント:

1. `armed` の時刻 ≦ 本文 `ts` なら **I1 は守れている**（本文表示より前に RPC が無い）
2. 本文 `ts` → `archived` が 1 秒超なら、`process_outbox` のフック位置（ロック内外）を疑う
3. `cutover` の直後に poked ログが出ていれば、次のターンが即応答になっている
4. `verify retry (1/2)` が2回以上出たら **もう失敗ループには入っていない**（`rotation_fail` で止まる）
