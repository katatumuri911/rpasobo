# 実装プラン: 進捗リストから `python wait_input.py --wait 8` を消し、主要ステップをトースト通知に置き換える

対象リポジトリ: `rp-image-chat-main`
このドキュメントは「これ1枚で実装できる」ことを目的にした作業指示書です。実装者はコードブロックをそのまま貼り付けてください。

**関連ドキュメント（必読）**
- `docs/toast-notification-spec.md` … トーストの正規仕様（kind / 既定時間 / 48文字ルール / dedupe）。**本プランはこれに従う**
- `.agents/rules/choices-filter-avoidance.md` … フィルタ回避ルール（**本プランでは変更しない**）

---

## 目次

1. [目的とゴール](#1-目的とゴール)
2. [受け入れ要件（AC）](#2-受け入れ要件ac)
3. [現状の仕組み（前提知識）](#3-現状の仕組み前提知識)
4. [データ契約（Python → HTTP → JS）](#4-データ契約python--http--js)
5. [文言表（最終）](#5-文言表最終)
6. [変更ファイルと挿入位置の特定](#6-変更ファイルと挿入位置の特定)
7. [実装詳細（コード）](#7-実装詳細コード)
8. [自動テスト](#8-自動テスト)
9. [手動テスト（受け入れ手順）](#9-手動テスト受け入れ手順)
10. [リスク・ロールバック・触らないもの](#10-リスクロールバック触らないもの)
11. [作業順序](#11-作業順序)

---

## 1. 目的とゴール

### 1.1 背景（現状の挙動）

送信すると「応答を書いています…」の下に **進捗リスト**（`#progress`）が出る。この行は

```
public/app.js: pollProgress()
  → GET /api/ide/progress   （server.py）
  → ide_poke.progress()     （Antigravity の会話ステップを読む）
  → {kind, tool, text, detail, done} の配列
```

という経路で作られている。そのためユーザーには次のような「読んでも意味がない／分からない」情報がそのまま見えている。

- `python "C:\…\wait_input.py" --ide <会話ID> --wait 8`
  （＝入力待ちのための内部コマンド。ユーザーには無関係）
- `view_file` / `write_to_file` などの **生のツール名**
- `outbox\…json` などの **内部パス**
- フィルタで止まったときは「エラー」としか出ず、**どのフィルタか分からない**

### 1.2 ゴール（やること）

| ID | ゴール |
|---|---|
| G1 | 進捗リストから `wait_input.py` の待機コマンド行を **出さない**。代わりに送信成功時にトースト `✅ AIに送信しました` を出す |
| G2 | `read` / `write` / 検索 / フォルダ確認 / コマンド実行 などの生表記を **日本語に言い換える**（画面の文字は日本語、詳細はホバーとAPIのみ） |
| G3 | トーストは **主要なステップだけ**（送信・読み込み・書き込み・完了・エラー/フィルタ）に出す。考え中・段取り・コマンド実行は行だけ |
| G4 | ブロック／フィルタ時は **どのフィルタか** をトーストと候補の注意書きに出す |
| G5 | 既存の挙動・API・データを壊さない（`kind` の値を増やさない、既存キーを消さない、`wait_input.py` は無変更） |

### 1.3 非ゴール（やらないこと）

- Antigravity（IDE）側の画面の表示は変えない。
- `wait_input.py` のプロンプト文言・`/api/*` のレスポンス構造は変えない。
- フィルタの閾値・回避ロジック（`.agents/rules/choices-filter-avoidance.md`）は変えない。
- トーストのデザイン変更は原則なし（色分け1行のみ任意）。

---

## 2. 受け入れ要件（AC）

| ID | 要件 | 確認方法 |
|---|---|---|
| AC-1 | 送信すると `✅ AIに送信しました` が出る（選択肢担当が別会話なら `✅ 選択肢のAIに送信しました`） | 目視 |
| AC-2 | 進捗リストに `wait_input.py` / `--ide` / `--wait 8` が **一度も出ない** | DevTools: `document.querySelector("#progress").innerText.includes("wait_input.py") === false` |
| AC-3 | 待機コマンドだけの間は `⏳ 応答を待っています` の1行が出る（空欄で高さが揺れない） | 目視 |
| AC-4 | `read` / `write` が `📖 ファイルを読み込み` / `✍️ 応答を書き込み` と出る（`view_file` 等の生名が出ない） | `#progress` の innerText に `view_file` が無い |
| AC-5 | `outbox\…json` などの内部フルパスが出ない（読み込み側はファイル名だけ） | 目視 |
| AC-6 | トーストは同じステップで **連打しない**（0.8 秒ポーリングで再表示されない）。1ステップ＝1回 | 10秒間のトースト出現回数を数える |
| AC-7 | 応答完了で `✅ 応答ができました`（進捗リストに本文冒頭を出さない） | 目視 |
| AC-8 | フィルタで止まると `⛔ コンテンツ安全フィルタで止まりました` のように **種類名** が出る。候補の注意書きは `🔞 コンテンツ安全フィルタで1回停止 → 自動で回避して出力` | 🔞候補生成で再現 |
| AC-9 | ホバー（`title`）で生のエラー文・詳細が読める（情報を捨てていない） | 目視 |
| AC-10 | Google / Vertex 経路の進捗行の文言が **変わらない**（`🔧 作業` 等に化けない） | provider を google にして送信 |
| AC-11 | `/api/ide/progress` の JSON に `kind` の新しい値が増えていない（`tool`/`think`/`reply`/`error` のみ） | `curl "http://127.0.0.1:8200/api/ide/progress?sid=<sid>"` |
| AC-12 | 既存テストが通る | `python -m pytest -q` |
| AC-13 | トーストの `kind` は `ok`/`info`/`warn`/`err`/`progress` の範囲内（独自 kind を増やさない） | `grep -n 'kind: "' public/app.js` で追加分を確認 |
| AC-14 | トースト文言は48文字以内・改行なし（下部フォールバックに落ちない） | DevTools: `$("toast").dataset.long === "0"` |

---

## 3. 現状の仕組み（前提知識）

| 層 | 場所 | 役割 |
|---|---|---|
| 進捗の生成 | `ide_poke.py: progress(cid)` | Antigravity の言語サーバーから `GetCascadeTrajectorySteps` でステップを読み、`{kind, tool, text, detail, done}` の配列にする |
| ツール行の生成 | `ide_poke.py: _tool_line(tc)` | ツール呼び出し1つを `text`（`toolAction`/`toolSummary`/ツール名）と `detail`（`CommandLine` かファイル名）に変換 |
| フィルタ検出 | `ide_poke.py: errors_since()` / `server.py: _is_filter_error_msg()` | content safety 系のエラー判定（**今回は変更しない**） |
| HTTP | `server.py: /api/ide/progress` | `progress()` の戻りをほぼそのまま返す |
| 表示 | `public/app.js: pollProgress()` | 800ms ごとに取得し `#progress` に `<div class="p-row">` を描く |
| トースト | `public/app.js: toast(msg, {kind, ms})` | 画面右下の通知。`kind` は既存の `info`/`ok`/`err`/`progress` のみ使う |
| 候補の注意 | `public/app.js: choicesNoticeText()` | `m.choices_notice = {kind, slot, ...}` を1行に変換 |

> 注意: `flash()` は設定パネル内のメッセージ（`#settingMsg`）用（`public/app.js` 6779行付近）。**トーストは `toast()`**（同 6792行付近）。

### 3.5 トーストAPIの前提（実装済み・確認済み）

`public/app.js` の `toast(msg, opts)` は以下を実装済み。**本プランは呼び出し側だけを直し、`toast()` 本体は触らない**。

| kind | アイコン | 既定時間 | 本プランでの用途 |
|---|---|---|---|
| `ok` | `✓` | 2000ms | 送信完了・応答完了 |
| `info` | なし | 2400ms | 読み込み・書き込み（進捗） |
| `warn` | `!` | 3600ms | フィルタで止まった（自動で続行している） |
| `err` | `×` | 6000ms | エラー・生成失敗 |
| `progress` | スピナー | 自動消去なし | 本プランでは使わない |

- `ms` は**指定しない**（種別ごとの既定時間に任せる）。連続表示の延長は `toast()` 側が行う。
- **48文字超 or 改行入りは下部フォールバック**に回るため、詳細（ファイル名）は 18 文字に詰める。
- 同一文言・同一 kind が 1.2 秒以内に再要求されると `toast()` が自動でまとめる（`dedupe: false` で解除可）。進捗トーストは自前のキー判定（`step` + `call`）と二重で守る。
- `toast()` は**関数宣言**なので巻き上げられ、`pollProgress()` より後ろの定義でも呼べる。

---

## 4. データ契約（Python → HTTP → JS）

`ide_poke.progress()` の `items[i]`。**追加のみ・既存キーは不変**。

| キー | 型 | 追加? | 意味 |
|---|---|---|---|
| `kind` | `"tool"` / `"think"` / `"reply"` / `"error"` | 既存 | **値を増やさない**（AC-11） |
| `tool` | str | 既存 | Antigravity のツール名（生） |
| `text` | str | 既存 | `toolAction` / `toolSummary`（英語のことがある） |
| `detail` | str | 既存 | ファイル名 / コマンド（内部のものは `""`） |
| `done` | bool | 既存 | 完了したか |
| `tool_kind` | `"read"` / `"write"` / `"search"` / `"list"` / `"plan"` / `"command"` / `"tool"` / `""` | **追加** | 言い換えの分類。`""` は「分類しない（JS は `text` をそのまま使う）」 |
| `internal` | bool | **追加** | `wait_input.py` の待機コマンド。JS は **描画しない** |
| `step` | int | **追加** | 会話の絶対ステップ番号（トーストの重複判定キー） |
| `call` | int | **追加** | 同一ステップ内のツール呼び出し番号 |
| `filter_name` | str | **追加（error 行のみ）** | `コンテンツ安全フィルタ` など。無ければ `""` |

---

## 5. 文言表（最終）

### 5.1 送信完了

- `✅ AIに送信しました`（本文・Antigravity）
- `✅ 選択肢のAIに送信しました`（選択肢担当が別会話のとき）
- Google / Vertex も同じ `✅ AIに送信しました`

### 5.2 進捗行とトースト

| 場面 | 画面（進捗行） | トースト | kind |
|---|---|---|---|
| 送信成功 | — | `✅ AIに送信しました` | `ok` |
| 待機コマンド（内部） | （出さない）→ `⏳ 応答を待っています` | （送信トーストが兼ねる） | — |
| read | `📖 ファイルを読み込み` ＋ ファイル名 | `📖 ○○を読み込んでいます` | `info` |
| write | `✍️ 応答を書き込み` | `✍️ ○○を書き込んでいます` | `info` |
| 検索 / フォルダ / 段取り / コマンド | `🔍 中身を検索` / `📁 フォルダを確認` / `🧭 段取りを整理` / `⚙️ コマンドを実行` | 出さない |
| 考え中 | `… 考えている` | 出さない | — |
| 完了 | `✅ 応答を書き上げた` | `✅ 応答ができました` | `ok` |
| エラー | `⚠️ エラー` | `⚠️ エラー: ○○` | `err` |
| フィルタ | `⛔ コンテンツ安全フィルタで停止` | `⛔ コンテンツ安全フィルタで止まりました` | `warn` |
| 候補の注意書き | — | `🔞 コンテンツ安全フィルタで1回停止 → 自動で回避して出力` |

### 5.3 フィルタの表示名

| 内部の値 | 表示名 |
|---|---|
| `STOP_REASON_CONTENT_FILTER` | コンテンツフィルタ（生成停止） |
| `content safety filters` | コンテンツ安全フィルタ |
| `PROHIBITED_CONTENT` / `Prohibited Use` | 禁止コンテンツ（解除不可） |
| `SAFETY` | 安全性フィルタ |
| `RECITATION` | 引用（著作権）フィルタ |
| `BLOCKLIST` | ブロックリスト |
| `SPII` | 個人情報フィルタ |
| `IMAGE_SAFETY` | 画像の安全フィルタ |
| `model output error` / `Retries remaining` | モデル出力エラー |
| `MODEL_ARMOR` | Model Armor |
| `pup_limited_disabled` | モデルの利用制限 |
| 429 / quota | 利用枠切れ（フィルタではない、と区別して出す） |

---

## 6. 変更ファイルと挿入位置の特定

作業前に以下を実行して行番号を控える。

```bash
grep -n "def _tool_line"        ide_poke.py
grep -n "def progress"          ide_poke.py
grep -n "def _short"            ide_poke.py
grep -n "def _is_filter_error_msg" server.py
grep -n '"kind": "filter"'      server.py
grep -n "ide/progress" -A 25    server.py
grep -n "async function pollProgress" public/app.js
grep -n "let progressHtml"      public/app.js
grep -n "async function send"   public/app.js
grep -n "function choicesNoticeText" public/app.js
grep -n "const openChoicesSet"  public/app.js
grep -n 'progressHtml = ""'     public/app.js
grep -n "\.p-row"               public/style.css
```

### 変更一覧

| ファイル | 変更内容 |
|---|---|
| `ide_poke.py` | 追加: `TOOL_KINDS` `tool_kind_of()` `FILTER_LABELS` `filter_label()` / 差し替え: `_tool_line()` `progress()` |
| `server.py` | 追加: `FILTER_NAME_WORDS` `filter_name_of()` / 1行追加: `choices_notice` に `filter_name`（`_is_filter_error_msg` は**無変更**） |
| `public/progress_labels.js`（新規） | 純粋関数（`stepLabel` / `stepDetail` / `stepRaw`）のみ。Node でテスト可能 |
| `public/app.js` | `pollProgress()` の行生成、送信トースト、`choicesNoticeText()`、notice トースト、リセット処理 |
| `public/index.html` | `progress_labels.js` を `app.js` の前に読む／`?v=` を上げる |
| `public/style.css` | `.p-row.filter` の色（任意） |
| `test_progress_labels.py`（新規） | Python 側: 分類・内部判定・フィルタ名・`progress()` 契約 |
| `test_progress_labels.js`（新規） | JS 側: 言い換え（Node で実行） |

---

## 7. 実装詳細（コード）

### 7.1 `ide_poke.py`

#### (a) `_short()` の下に追加

```python
# 進捗の1行を分類する（画面の言い換えとトーストに使う）
TOOL_KINDS = (
    (("write_to_file", "edit_file", "create_file", "replace_file_content",
      "multi_replace_file_content"), "write"),
    (("view_file", "read_file", "open_file", "read_text_file", "view_file_content"), "read"),
    (("grep_search", "find_by_name", "codebase_search", "search"), "search"),
    (("list_dir", "list_directory"), "list"),
    (("update_plan", "task_boundary"), "plan"),
    (("run_command", "execute_command", "command"), "command"),
)

def tool_kind_of(name):
    """Antigravity のツール名 → read / write / search / list / plan / command / tool"""
    low = str(name or "").lower()
    for keys, kind in TOOL_KINDS:
        if any(k in low for k in keys):
            return kind
    return "tool"

# フィルタの表示名（server.py の API 側エラーと語彙をそろえる）
FILTER_LABELS = (
    ("stop_reason_content_filter", "コンテンツフィルタ（生成停止）"),
    ("prohibited_content", "禁止コンテンツ（解除不可）"),
    ("prohibited use", "禁止コンテンツ（解除不可）"),
    ("content safety", "コンテンツ安全フィルタ"),
    ("model output error", "モデル出力エラー"),
    ("retries remaining", "モデル出力エラー"),
    ("recitation", "引用（著作権）フィルタ"),
    ("blocklist", "ブロックリスト"),
    ("spii", "個人情報フィルタ"),
    ("image_safety", "画像の安全フィルタ"),
    ("model_armor", "Model Armor"),
    ("pup_limited_disabled", "モデルの利用制限"),
    ("safety", "安全性フィルタ"),
)

def filter_label(text):
    """エラー文からフィルタの種類を1つ返す（分からなければ ""）"""
    low = str(text or "").lower()
    for key, label in FILTER_LABELS:
        if key in low:
            return label
    return ""
```

#### (b) `_tool_line()` を差し替え（既存キーは維持、追加のみ）

```python
def _tool_line(tc):
    """ツール呼び出し1つを {tool, tool_kind, text, detail, internal} にする"""
    raw = tc.get("argumentsJson") or "{}"
    try:
        a = json.loads(raw)
    except ValueError:
        a = {}
    a = a if isinstance(a, dict) else {}
    name = tc.get("name") or ""
    detail = ""
    for k in ("CommandLine", "AbsolutePath", "TargetFile", "Url", "query", "Query",
              "SearchPath", "DirectoryPath", "TaskId"):
        if a.get(k):
            v = str(a[k])
            detail = _short(v) if k in ("CommandLine", "Url", "query", "Query", "TaskId") \
                else re.split(r"[\\/]", v.rstrip("\\/"))[-1]
            break
    kind = tool_kind_of(name)
    # 入力待ちの wait_input.py 実行は、ユーザーが読んでも意味がないので印を付ける（画面側で捨てる）
    internal = "wait_input.py" in raw          # 引数キー名が違っても取りこぼさない
    # outbox への書き込みは「応答を書き込み」で足りる（パスは画面に出さない）
    if internal or (kind == "write" and "outbox" in raw):
        detail = ""
    return {"tool": name, "tool_kind": kind, "internal": internal,
            "text": _short(a.get("toolAction") or a.get("toolSummary") or name, 80),
            "detail": detail}
```

#### (c) `progress()` を差し替え（`kind` は既存のまま。`step` / `call` / `filter_name` を追加）

```python
def progress(cid, limit=10):
    """会話のいまのターン（最後のユーザー入力から後）で何をしているか。読むだけ。
    ({running, user_time, steps, items: [{kind, tool, tool_kind, text, detail, internal, done, step, call}]}, None)
    kind は tool（ツール）/ think（考えている）/ reply（返事を書いた）/ error"""
    port, tok, s = locate(cid)
    if not port:
        return None, "この会話を開いている Antigravity が見つからない"
    n = int(s.get("stepCount") or 0)
    first = int(s.get("lastUserInputStepIndex") or 0)
    start = max(first, n - 40)  # 1つのターンで何件も受け取る使い方だとターンが長いので、最近の分だけ
    code, d = _call(port, tok, "GetCascadeTrajectorySteps", {"cascadeId": cid, "stepOffset": start}, timeout=20)
    if code != 200:
        return None, f"会話を読めなかった（{code}）"
    items = []
    for j, x in enumerate(d.get("steps") or []):
        at = start + j
        t = (x.get("type") or "").replace("CORTEX_STEP_TYPE_", "")
        done = (x.get("status") or "").endswith("_DONE")
        if t == "PLANNER_RESPONSE":
            pr = x.get("plannerResponse") or {}
            calls = pr.get("toolCalls") or []
            for k, tc in enumerate(calls):
                items.append(dict(_tool_line(tc), kind="tool", done=False, step=at, call=k))
            if not calls:
                if pr.get("response", "").strip():
                    items.append({"kind": "reply", "tool": "", "tool_kind": "", "internal": False,
                                  "text": "返事を書いた", "detail": _short(pr["response"]),
                                  "done": True, "step": at, "call": 0})
                elif not done:
                    heads = re.findall(r"\*\*(.+?)\*\*", pr.get("thinking") or "")
                    items.append({"kind": "think", "tool": "", "tool_kind": "", "internal": False,
                                  "text": "考えている", "detail": _short(heads[-1] if heads else ""),
                                  "done": False, "step": at, "call": 0})
        elif t == "ERROR_MESSAGE":
            e = (x.get("errorMessage") or {}).get("error") or {}
            msg = e.get("shortError") or e.get("userErrorMessage") or ""
            items.append({"kind": "error", "tool": "", "tool_kind": "", "internal": False,
                          "filter_name": filter_label(msg),      # ★ どのフィルタか
                          "text": "エラー", "detail": _short(msg), "done": True, "step": at, "call": 0})
        elif t not in ("USER_INPUT", "CHECKPOINT", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE"):
            # ツールを実行したステップ（RUN_COMMAND / VIEW_FILE / CODE_ACTION など）。まだ終わっていない最初のツール行に当てる
            tool = next((it for it in items if it["kind"] == "tool" and not it.get("ran")), None)
            if tool:
                tool["ran"], tool["done"] = True, done
    for it in items:
        it.pop("ran", None)
    running = s.get("status") == "CASCADE_RUN_STATUS_RUNNING"
    if not running:
        for it in items:
            it["done"] = True  # 止まった会話に「実行中」は残さない
    return {"running": running, "user_time": s.get("lastUserInputTime") or "",
            "steps": n, "items": items[-limit:]}, None
```

### 7.2 `server.py`

#### (a) `_is_filter_error_msg()` は無変更。その直後に追加

```python
FILTER_NAME_WORDS = (
    ("stop_reason_content_filter", "コンテンツフィルタ（生成停止）"),
    ("prohibited_content", "禁止コンテンツ（解除不可）"),
    ("prohibited", "禁止コンテンツ（解除不可）"),
    ("content safety", "コンテンツ安全フィルタ"),
    ("model output error", "モデル出力エラー"),
    ("retries remaining", "モデル出力エラー"),
    ("recitation", "引用（著作権）フィルタ"),
    ("blocklist", "ブロックリスト"),
    ("spii", "個人情報フィルタ"),
    ("image_safety", "画像の安全フィルタ"),
    ("model_armor", "Model Armor"),
    ("pup_limited_disabled", "モデルの利用制限"),
    ("safety", "安全性フィルタ"),
)

def filter_name_of(msg):
    """API のエラー文からフィルタの種類を返す（分からなければ ""）"""
    low = str(msg or "").lower()
    for w, label in FILTER_NAME_WORDS:
        if w in low:
            return label
    return ""
```

#### (b) `choices_notice` を作っている箇所（`"kind": "filter"` の近く）に `filter_name` を1行追加

```python
        notice = {"kind": "filter", "slot": slot,
                  "filter_name": filter_name_of(err_text),   # ★追加（err_text は既存の変数名に合わせる）
                  }
```

#### (c) `/api/ide/progress` は変更不要

`ide_poke.progress()` の戻りをそのまま返しているため、`items` の辞書ごと届く。
`grep -n "ide/progress" -A 25 server.py` で「items を加工していない」ことだけ確認する。

### 7.3 `public/progress_labels.js`（新規・純粋関数のみ）

```js
// 進捗行の言い換え（DOM に触らない純粋関数だけ。Node でテストできる）
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ProgressLabels = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  // tool_kind がある行だけ言い換える。無い行（API 側の進捗など）は元の文言を尊重する
  const STEP_LABELS = {
    command: "⚙️ コマンドを実行", read: "📖 ファイルを読み込み", write: "✍️ 応答を書き込み",
    search: "🔍 中身を検索", list: "📁 フォルダを確認", plan: "🧭 段取りを整理", tool: "🔧 作業",
  };
  function stepLabel(it) {
    const name = it.filter_name || "";
    if (it.kind === "error") return name ? `⛔ ${name}で停止` : (it.text || "⚠️ エラー");
    if (!it.tool_kind) return it.text || "";
    if (it.kind === "reply") return "✅ 応答を書き上げた";
    if (it.kind === "think") return "… 考えている";
    return STEP_LABELS[it.tool_kind] || it.text || "🔧 作業";
  }
  // 内部（wait_input.py の待機）は詳細も出さない。返事の本文冒頭も進捗には出さない
  function stepDetail(it) {
    if (it.internal || it.kind === "reply") return "";
    return it.detail || "";
  }
  // title（ホバー）用。情報は消さずに残す
  function stepRaw(it) {
    return it.internal ? "" : (it.detail || it.text || "");
  }
  return { STEP_LABELS, stepLabel, stepDetail, stepRaw };
});
```

### 7.4 `public/app.js`

#### (a) 冒頭 `const openChoicesSet = new Set();` の隣に追加

```js
const choicesNoticeToasted = new Set();  // 同じ通知を何度もトーストしない
```

#### (b) `let progressHtml = "", progressSid = "", progressBusy = false;` を差し替え

```js
let progressHtml = "", progressSid = "", progressBusy = false;
let lastStepToastKey = "", stepToastSid = "";

// progress_labels.js が読めていなければ素通し（画面が壊れないように）
const PL = (typeof ProgressLabels !== "undefined") ? ProgressLabels : {
  stepLabel: (it) => it.text || "",
  stepDetail: (it) => (it.internal ? "" : (it.detail || "")),
  stepRaw: (it) => (it.internal ? "" : (it.detail || it.text || "")),
};
const { stepLabel, stepDetail, stepRaw } = PL;

// トーストは18文字目標（48文字超で下部フォールバックに回る）。ファイル名などは詰める
const brief = (s, n = 18) => {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
};

// トーストは主要なステップだけ（送信・読み込み・書き込み・完了・エラー）。新しい行が出た瞬間に1回だけ
const STEP_TOAST_KINDS = new Set(["read", "write"]);
function notifyProgressStep(items) {
  const s = state;
  if (!s) return;
  if (s.id !== stepToastSid) { stepToastSid = s.id; lastStepToastKey = ""; }
  const it = [...items].reverse().find((x) =>
    x.kind === "reply" || x.kind === "error" ||
    (STEP_TOAST_KINDS.has(x.tool_kind) && !x.internal));
  if (!it) return;
  // 0.8秒ごとの再描画で同じ行を出し直さない（step/call は会話の絶対位置なので安定）
  const key = `${it.step ?? ""}:${it.call ?? ""}:${it.kind}:${it.tool_kind || ""}:${it.filter_name || ""}`;
  if (key === lastStepToastKey) return;
  lastStepToastKey = key;
  const d = stepDetail(it);
  if (it.kind === "error")
    return it.filter_name
      ? toast(`⛔ ${it.filter_name}で止まりました`, { kind: "warn" })   // 止まったが自動で続行している
      : toast(`⚠️ エラー${d ? ": " + brief(d) : ""}`, { kind: "err" });
  if (it.kind === "reply") return toast("✅ 応答ができました", { kind: "ok" });
  if (it.tool_kind === "read") return toast(`📖 ${d ? brief(d) : "ファイル"}を読み込んでいます`, { kind: "info" });
  if (it.tool_kind === "write") return toast(`✍️ ${d ? brief(d) : "応答"}を書き込んでいます`, { kind: "info" });
}
```

#### (c) `pollProgress()` の行生成を差し替え

```js
    const items = r.items || [];
    const shown = items.filter((it) => !it.internal);       // wait_input.py の行は画面に出さない
    const rows = shown.map((it, i) => {
      const last = i === shown.length - 1;
      const now = !it.done && last;
      const bad = it.kind === "error";
      const mark = bad ? "✕" : it.kind === "think" ? "…" : it.done ? "✓" : now ? "●" : "・";
      const cls = "p-row" + (now ? " now" : "") + (bad ? " error" : "") + (it.filter_name ? " filter" : "");
      const d = stepDetail(it), raw = stepRaw(it);
      return `<div class="${cls}"${raw ? ` title="${esc(raw)}"` : ""}>`
        + `<span class="p-mark">${mark}</span><span class="p-text">${esc(stepLabel(it))}</span>`
        + (d ? `<span class="p-detail" title="${esc(raw)}">${esc(d)}</span>` : "") + "</div>";
    });
    const onlyWaiting = !!items.length && !shown.length;    // 待機コマンドだけで動いている
    if (r.running && !onlyWaiting && (!shown.length || shown[shown.length - 1].done))
      rows.push(`<div class="p-row now"><span class="p-mark">●</span><span class="p-text">次にすることを考えている</span></div>`);
    if (!rows.length && (onlyWaiting || r.running))
      rows.push(`<div class="p-row now"><span class="p-mark">●</span><span class="p-text">⏳ 応答を待っています</span></div>`);
    if (!rows.length && r.note) rows.push(`<div class="p-note">${esc(r.note)}</div>`);
    progressHtml = rows.join("");
    progressSid = s.id;
    notifyProgressStep(shown);
```

#### (d) `render()` のターン終了処理を差し替え

変更前:

```js
  } else if (progressSid === s.id) progressHtml = "";  // 次の入力で前のターンの表示が一瞬出ないように
```

変更後:

```js
  } else if (progressSid === s.id) { progressHtml = ""; lastStepToastKey = ""; }
```

#### (e) `send()` の成功直後に追加

```js
    if (r.error) return alert("送れませんでした: " + r.error);
    {
      const st = r.session?.setting || state?.setting || {};   // session が無い経路でも落ちない
      const api = ["google", "vertex"].includes(st.ai_provider || "antigravity");
      const par = !api && st.choices_ide_session && st.choices_ide_session !== st.ide_session;
      toast(par ? "✅ 選択肢のAIに送信しました" : "✅ AIに送信しました", { kind: "ok" });
    }
    ta.value = "";
```

#### (f) `choicesNoticeText()` を差し替え

```js
function choicesNoticeText(n) {
  const ic = (n && n.slot === "normal") ? "💬" : "🔞";
  const f = (n && n.filter_name) || "フィルタ";
  switch (n && n.kind) {
    case "filter": return `${ic} ${f}で1回停止 → 自動で回避して出力`;
    case "suspect": return `${ic} の候補: 健全な内容に差し替わった可能性あり${n && n.filter_name ? "（" + n.filter_name + "）" : ""}`;
    case "placeholder": return `${ic} の候補: プレースホルダーを一部自動置換しました`;
    case "timeout": return `${ic} の候補: 生成がタイムアウト（応答が届いていません）`;
    case "rescued": return `${ic} の候補: 生成結果を回収しました（.tmp から復元）`;
    default: return "";
  }
}
```

#### (g) `choicesBox()` の `if (notice) { const txt = choicesNoticeText(notice); if (txt) {` の中に追加

```js
      const nk = msgKey + "|" + notice.kind + "|" + (notice.filter_name || "");
      if (!choicesNoticeToasted.has(nk)) {
        choicesNoticeToasted.add(nk);
        toast(txt, { kind: notice.kind === "filter" ? "warn" : "info" });
      }
```

#### (h) `switchTo()` の `openChoicesSet.clear();` の隣に追加

```js
  choicesNoticeToasted.clear();
```

### 7.5 `public/index.html`

```html
<script src="progress_labels.js?v=1"></script>
<script src="app.js?v=120"></script>      <!-- ?v= を1つ上げる（キャッシュ対策） -->
```

読み込み順は `progress_labels.js` → `app.js`。

### 7.6 `public/style.css`（任意）

```css
/* フィルタで止まった行を色分け */
.p-row.filter .p-mark, .p-row.filter .p-text { color: #f59e0b; }
```

---

## 8. 自動テスト

### 8.1 `test_progress_labels.py`（新規・Python 側）

```python
import json
import pytest

ide_poke = pytest.importorskip("ide_poke")   # Windows 以外ではスキップ


def _tc(name, **args):
    return {"name": name, "argumentsJson": json.dumps(args)}


@pytest.mark.parametrize("name,want", [
    ("run_command", "command"),
    ("view_file", "read"),
    ("read_text_file", "read"),
    ("write_to_file", "write"),
    ("replace_file_content", "write"),
    ("grep_search", "search"),
    ("list_dir", "list"),
    ("update_plan", "plan"),
    ("mystery_tool", "tool"),
    ("", "tool"),
    (None, "tool"),
])
def test_tool_kind_of(name, want):
    assert ide_poke.tool_kind_of(name) == want


@pytest.mark.parametrize("text,want", [
    ("content safety filters", "コンテンツ安全フィルタ"),
    ("STOP_REASON_CONTENT_FILTER", "コンテンツフィルタ（生成停止）"),
    ("Prohibited Use: ...", "禁止コンテンツ（解除不可）"),
    ("PROHIBITED_CONTENT", "禁止コンテンツ（解除不可）"),
    ("model output error", "モデル出力エラー"),
    ("HTTP 500", ""),
    ("", ""),
])
def test_filter_label(text, want):
    assert ide_poke.filter_label(text) == want


def test_tool_line_marks_wait_command_internal():
    it = ide_poke._tool_line(_tc("run_command",
                                 CommandLine='python "C:\\x\\wait_input.py" --ide abc --wait 8'))
    assert it["internal"] is True
    assert it["tool_kind"] == "command"
    assert it["detail"] == ""          # コマンド全文を画面に残さない


def test_tool_line_hides_outbox_path():
    it = ide_poke._tool_line(_tc("write_to_file", AbsolutePath="C:\\app\\outbox\\abc_reply_1.json"))
    assert it["tool_kind"] == "write"
    assert it["internal"] is False
    assert it["detail"] == ""          # outbox のパスは出さない


def test_tool_line_keeps_read_filename():
    it = ide_poke._tool_line(_tc("view_file", AbsolutePath="C:\\app\\data\\ctx\\abc.md"))
    assert it["tool_kind"] == "read"
    assert it["detail"] == "abc.md"    # ファイル名だけ残す


def test_progress_contract(monkeypatch):
    """kind の値は既存のまま。internal / step / filter_name が付く。"""
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE"},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE",
         "plannerResponse": {"toolCalls": [_tc("run_command",
                                               CommandLine="python wait_input.py --ide x --wait 8")]},
         "status": "CORTEX_STEP_STATUS_DONE"},
        {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE",
         "errorMessage": {"error": {"shortError": "content safety filters blocked the request"}},
         "status": "CORTEX_STEP_STATUS_DONE"},
    ]
    monkeypatch.setattr(ide_poke, "locate",
                        lambda cid: (1, "tok", {"stepCount": len(steps),
                                                "lastUserInputStepIndex": 0,
                                                "status": "CASCADE_RUN_STATUS_IDLE"}))
    monkeypatch.setattr(ide_poke, "_call", lambda *a, **k: (200, {"steps": steps}))
    res, err = ide_poke.progress("cid")
    assert err is None
    items = res["items"]
    assert [i["kind"] for i in items] == ["tool", "error"]      # ★ 新しい kind を増やしていない
    assert items[0]["internal"] is True
    assert items[-1]["filter_name"] == "コンテンツ安全フィルタ"
    assert all("step" in i and "call" in i for i in items)
```

### 8.2 `test_progress_labels.js`（新規・Node）

```js
const assert = require("assert");
const L = require("./public/progress_labels.js");

assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "read", text: "view file" }), "📖 ファイルを読み込み");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "write", text: "write" }), "✍️ 応答を書き込み");
assert.strictEqual(L.stepLabel({ kind: "reply", tool_kind: "", text: "返事を書いた" }), "✅ 応答を書き上げた");
assert.strictEqual(L.stepLabel({ kind: "think", tool_kind: "", text: "考えている" }), "… 考えている");
assert.strictEqual(L.stepLabel({ kind: "error", tool_kind: "", text: "エラー", filter_name: "コンテンツ安全フィルタ" }),
  "⛔ コンテンツ安全フィルタで停止");
// ide_poke 由来でない行（API 側）は文言を変えない
assert.strictEqual(L.stepLabel({ kind: "tool", text: "Gemini が生成中" }), "Gemini が生成中");
// 内部コマンドと返信本文は詳細に出さない
assert.strictEqual(L.stepDetail({ internal: true, detail: "python wait_input.py" }), "");
assert.strictEqual(L.stepDetail({ kind: "reply", detail: "本文の冒頭" }), "");
assert.strictEqual(L.stepDetail({ kind: "tool", tool_kind: "read", detail: "abc.md" }), "abc.md");
assert.strictEqual(L.stepRaw({ internal: true, detail: "python wait_input.py" }), "");
console.log("progress_labels: ok");
```

### 8.3 実行コマンド

```bash
python -m py_compile ide_poke.py server.py
node --check public/app.js && node --check public/progress_labels.js
node test_progress_labels.js
python -m pytest -q test_progress_labels.py
python -m pytest -q                 # 既存テスト全体（AC-12）
```

---

## 9. 手動テスト（受け入れ手順）

1. `server.py` を再起動 → チャンネルを開く。
2. 何か送信 → `✅ AIに送信しました`（AC-1）。
   DevTools で `document.querySelector("#progress").innerText` に `wait_input.py` が **無い**（AC-2）。
   待機中は `⏳ 応答を待っています`（AC-3）。
3. 応答中の進捗を観察 → `📖 ○○を読み込み` / `✍️ 応答を書き込み` が出る（AC-4）。
   `outbox` のフルパスが出ない（AC-5）。トーストが連打しない（AC-6）。
   完了で `✅ 応答ができました`（AC-7）。
4. 🔞候補のあるチャンネルで候補生成 → フィルタ名入りのトーストと注意書き（AC-8）。
   行にホバーで生のエラー文（AC-9）。
5. provider を Google にして送信 → 進捗行が従来の文言のまま（AC-10）。
6. `curl "http://127.0.0.1:8200/api/ide/progress?sid=<sid>"` → `kind` が `tool|think|reply|error` のみ（AC-11）。

---

## 10. リスク・ロールバック・触らないもの

### 10.1 リスクと対策

| リスク | 対策 |
|---|---|
| `kind:"filter"` 追加でサーバー側の `kind=="error"` 判定が壊れる | **kind を増やさない**設計（`filter_name` を追加するだけ） |
| トーストの連打／取りこぼし | 重複判定は `items.length` ではなく **`step` + `call`** |
| 待機コマンド判定漏れ（引数キー名違い） | `argumentsJson` 全体で `wait_input.py` を判定 |
| Google/Vertex の進捗文言が化ける | `tool_kind` が無い行は `text` をそのまま表示 |
| `progress_labels.js` 未読込で画面が壊れる | `PL` フォールバック（素通し）を実装 |
| 情報を捨てすぎて調査できない | 画面の文字から消すだけ。`title`（ホバー）と `/api/ide/progress` の生JSONは維持 |
| `progress()` の形を変えてテストが壊れる | **キー追加のみ**。`python -m pytest -q` で確認 |

### 10.2 ロールバック

変更は 6 ファイル（＋新規2テスト・新規1JS）に閉じている。

```bash
git checkout -- ide_poke.py server.py public/app.js public/index.html public/style.css
rm -f public/progress_labels.js test_progress_labels.py test_progress_labels.js
```

### 10.3 触らないもの（重要）

- `wait_input.py`（プロンプト文言・待機ロジック）
- `ide_poke.py`: `errors_since()` / `last_reply()` / `send_to_conversation()` / `revert*` / `fork()`
- `server.py`: 世代交代（rotation）処理、`/api/send` のレスポンス構造、`_is_filter_error_msg()`
- `public/app.js`: `toast()` 本体・`TOAST_MS` / `TOAST_ICON` / `dismissToast()`（`docs/toast-notification-spec.md` で確定済み・実装済み。**呼び出し側だけ**を直す）
- `kind` の値（`tool` / `think` / `reply` / `error` 以外を増やさない）
- `.agents/rules/choices-filter-avoidance.md`（フィルタ回避ルール）

---

## 11. 作業順序

1. `ide_poke.py`（追加関数 → `_tool_line` → `progress`）
2. `test_progress_labels.py` を追加して `pytest` を緑にする（分類・内部判定を確定）
3. `server.py`（`FILTER_NAME_WORDS` / `filter_name_of` / `choices_notice` に1行）
4. `public/progress_labels.js` を新規作成 ＋ `test_progress_labels.js` を緑にする
5. `public/app.js`（PL 参照 → `pollProgress` → `send` トースト → `choicesNoticeText` → notice トースト → リセット）
6. `public/index.html`（script 追加・`?v=` 更新）、`public/style.css`（任意）
7. `python -m py_compile` / `node --check` / `pytest` → 手動テスト（AC-1〜AC-12）

---

## 付録: コミットメッセージ案

```
進捗リストの内部コマンド表示を廃止し、主要ステップをトースト通知に変更

- ide_poke: ツール呼び出しを read/write/... に分類、wait_input.py の待機行を internal でマーク、
  エラー行にフィルタ名(filter_name)を付与（kind の値は据え置き）
- server: フィルタ表示名の解決を追加し、choices_notice に filter_name を追加
- web: 進捗行を日本語化、内部コマンド行を非表示、主要ステップのみトースト通知、
  送信成功トースト、フィルタ名の通知
- test: 分類・内部判定・progress() の契約、JS の言い換えをテスト
```
