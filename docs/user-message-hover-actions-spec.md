# ユーザー発言カードのホバー操作 / モバイル長押し操作 実装仕様書

対象ファイル: `public/app.js` / `public/style.css` / `public/index.html` / `test/test_more_menu.cjs` / `test/test_user_msg_actions.cjs`（新規）
前提: **AI応答カードの操作は一切変えない**。既存の `⋯` メニュー・モバイルシート・FAB・背景表示（RP）は非回帰。

---

## 1. 目的と最終ゴール

### 1.1 現状

- `createMessageWrap()` が全メッセージの下に `.choices-container` を必ず作り、その中に `⋯`（`.btn-more`）を**常時**表示している。
- ユーザー発言の操作（編集・コピー・削除など）は、この `⋯` を開かないと触れない。
- ユーザー発言カードは `#chatLog.rp` では `rgba(59, 91, 219, .88)` の半透明ブルー、通常時は `#3b5bdb`（`.msg.user`）。

現行コードの位置（行番号は目安。関数名 / セレクタ名を優先）:

| 箇所 | 目安 |
|---|---|
| `⋯` セクション開始マーカー | `public/app.js` 3107 行付近 |
| `textDecensorLine`（新ブロックはこの直前） | 3314 行付近 |
| `closeMoreMenu` | 3162 行付近 |
| `buildMoreMenu`（コピー項目） | 3186 / 3258 行付近 |
| `toggleMoreMenu` | 3282 行付近 |
| `createMessageWrap` | 3357 行付近 |
| `.choices-container` 生成 / `⋯` 追加 | 3416 / 3556 行付近 |
| `.choices-container` / `.btn-more` / `.more-sheet` | `public/style.css` 1370 / 1940 / 2013 行付近 |
| 既存の coarse 向けリセット | `public/style.css` 2464 行付近 |
| CSS / JS のバージョン | `public/index.html` 9 / 814 行 |

### 1.2 あるべき姿

1. **PC（マウスホバーできる環境）**
   - ユーザー発言カードにマウスを乗せたときだけ、カード直下の**右下**に
     `編集アイコン` / `コピーアイコン` / `⋯` の 3 ボタンがフェードインする。
   - マウスが離れたら 3 ボタンは消える。
   - ホバーでボタンが出ても**下の会話が跳ねない**（レイアウトシフト 0）。
   - **送信中（フッターの `■` / 発言内の `■ 停止` が見えている間）は、その発言の 3 ボタンを出さない。**
     `■ 停止` だけを残し、編集・コピー・⋯・長押しをすべて止める（4.8）。
2. **スマホ（タッチ環境）**
   - 普段はボタンを一切見せない。
   - ユーザー発言カードを**長押し（ロングタップ）**すると、画面下から既存の `⋯` シート（モーダル）が開き、
     `✏️ 編集して再送` / `📋 コピー` / `分岐` / `⏪ 巻き戻し` / `🗑️ 削除` を選べる。
   - 長押し直後の合成クリックでシートの項目（特に「削除」）を誤爆しない。
3. **AI応答カード**
   - 従来どおり、画像操作・返答候補・`⋯` が常時見えている。**1 ピクセルも変えない。**
4. **ステータス系ボタン（例外）**
   - ユーザー発言でも `■ 停止`（送信中）・`送信エラー + ✏ 再送` は常時表示のまま。
     これらは「操作」ではなく「今の状態を脱出するためのボタン」であり、隠すと復帰できなくなるため。

### 1.3 スコープ外

- サーバー（`server.py`）の変更。API 追加。DB/JSON スキーマ変更。
- `⋯` メニューの項目追加・並び替え・文言変更（コピーにトーストを付ける点だけ例外）。
- AI応答カードのホバー化・長押し化。
- 右クリック（PC）でメニューを開く機能。
- 複数選択・一括操作。ユーザー発言のテキスト選択仕様の変更（タッチ非搭載 PC は現状維持。タッチ搭載端末は 4.5 参照）。

---

## 2. 完成イメージ

```
                         ┌────────────────────────┐
                         │ ユーザーの本文…………    │  ← 青いカード (.msg.user)
                         └────────────────────────┘
                                        ✎   ⧉   ⋯   ← ホバー時だけ / 右下
```

- 3 ボタンは**メッセージブロックの直下**（本文カード、添付、「改変済」ラベル等の後）、カードの右端に揃える（RP モードでも同じ）。
- 非ホバー時はボタンが見えないだけで、行の高さ（26px）は確保する。
  これによりホバーイン/アウトでメッセージ一覧が上下に動かない。
- スマホではこの行自体を `display:none` にして縦スペースを回収する。

---

## 3. 不変条件（Invariants）

実装中に迷ったら「既存非回帰 > 新機能」。以下は破ってはいけない。

1. `createMessageWrap(m, isLast, s)` の**引数・戻り値**を変えない。
2. `m.role === "assistant"` の DOM・CSS・操作は現行と同一。
   - `⋯` は `.choices-container` の直接の子のまま。ホバーグループに入れない。
3. `buildMoreMenu()` の項目（ラベル・順序・出し分け条件）を変えない。
   - 変更してよいのは「📋 コピー」の実行内容を `copyMessageText(m)` に置き換えることだけ。
4. `toggleMoreMenu(anchor, m, isLast, s)` を**第 5 引数なし**で呼んだときの挙動（PC ポップオーバー / 幅 768px 以下はシート、外側クリック・Esc・スクロール追従・`updateFabState()`）を変えない。
5. `isOverlayOpen()` / `updateFabState()` / FAB の導出ロジックを変えない。
   - モバイルの長押しは**既存の `.more-sheet` を再利用**する（新しいモーダルを作らない）。
6. 隠れているボタンは `pointer-events: none`。クリックを奪わない。
7. 長押しは**ユーザー発言のみ**。AI応答では長押しを開始しない。
8. 破壊的操作（削除・巻き戻し・再生成）の既存 `confirm()` を外さない。
9. `test/test_more_menu.cjs` の抽出範囲（`// ---- その他の操作（⋯）` 〜 `function createMessageWrap(`）に、
   **トップレベルで DOM を触るコードを置かない**（テストが即死する）。
   - `initUserMsgLongPress()` の呼び出しは下部の初期化 IIFE 内で行う。
10. `style.css` / `app.js` の `?v=` を更新する（`index.html`）。
11. 送信中（`m.status` が `pending` / `processing`）のユーザー発言には、
    ホバーグループ / `⋯` / 長押しシート / 編集を出さない。
    - `■ 停止` と、エラー時の `✏ 再送` はステータス脱出のための例外として常時表示する。

---

## 4. 振る舞い仕様

### 4.1 デスクトップ（`hover: hover` かつ `pointer: fine`）

| 状態 | 3 ボタン |
|---|---|
| マウスがユーザー発言カード（`.msg-wrap.user`）の上 | `opacity:1` / `pointer-events:auto` |
| マウスが外れた | `opacity:0` / `pointer-events:none`（高さは維持） |
| キーボードで 3 ボタンのどれかにフォーカス | `.kbd-focus` を付けて表示 |
| `⋯` を開いている間 | `.actions-pinned` を付けて表示を維持（ポップオーバーへマウスを移動しても消えない） |
| `⋯` を閉じた | `.actions-pinned` を外す（マウスが外れていれば消える） |
| その発言が送信中（`m.status` が `pending` / `processing`） | 3 ボタンは `display:none`。`■ 停止` だけが出る |

- 表示/非表示は `opacity` のトランジション（140ms）。`display` は変えない。
- マウス操作で `⋯` を押したあと外側をクリックして閉じても、ボタンが残らない
  （`:focus-within` は使わず、**キーボード由来のフォーカスだけ** `.kbd-focus` を付ける）。
- 3 ボタンの順序は DOM 順 = 視覚順で **編集 → コピー → ⋯**（⋯ が一番右）。
- ホバー判定は `.msg-wrap.user`（カード＋直下の操作行）。操作行にマウスが乗っても表示が維持される。

### 4.2 編集ボタン

- 既存の `openEdit(m)` を呼ぶ。ユーザー発言なので `editTitle = "過去の入力を編集"`、
  `editSave = "編集して再送"`、保存時は既存の確認 `confirm` が出る。
- `m.text` が空（前後空白のみを含む）のときは**編集ボタンを出さない**（保存しても何も起きないため）。
- **送信中は編集できない。** `openEdit(m)` の先頭で `hasWaitingInput()` を確認し、true なら
  `toast("応答中は編集できません。停止してから操作してください。", 4000)` を出して開かない（7.10）。
  `saveEdit()` でも保存直前に同じ確認をする（モーダルを開いた後に送信が始まるケース）。

### 4.3 コピーボタン

- `m.text`（画面に出ている本文。Markdown の生テキスト）をコピーする。
- 成功で `toast("コピーしました", 1800)`、失敗で `toast("コピーできませんでした", 4000)`。
- `navigator.clipboard` が使えない環境（LAN の `http://` は `isSecureContext === false`）でも動くよう、
  `textarea` + `document.execCommand("copy")` にフォールバックする。
- `m.text` が空のときは**コピーボタンを出さない**。
- `⋯` メニュー内の「📋 コピー」も同じ `copyMessageText(m)` を使う（挙動を 2 系統に分けない）。

### 4.4 ⋯ ボタン

- デスクトップ: 既存の `toggleMoreMenu(anchor, m, isLast, s)`。位置・フリップ・外側クリック・Esc・スクロール追従は現行のまま。
- ボタンには `aria-haspopup="true"` / `aria-expanded` を現行どおり付ける。
- スマホ: ボタン自体は非表示。長押しで同じシートを開く（4.6）。
- 送信中（`pending` / `processing`）のその発言では、`⋯` を含むホバーグループごと出さない（4.8）。
  古い発言の `⋯` は開けるが、編集項目は 7.10 のガードでトーストになる。

### 4.5 スマホ（タッチ / `pointer: coarse`）

| 状態 | 表示 |
|---|---|
| 通常 | 3 ボタンと、それを含む操作行は**見えない**（`display:none`）。停止/エラー等のステータス行は例外 |
| 長押し 520ms 成立 | 画面下から `⋯` シート（既存 `.more-sheet`）が出る |
| 長押し中に 10px 以上動いた | キャンセル（スクロール優先） |
| 指を離すのが 520ms 未満 | キャンセル（ただのタップ） |
| 長押し直後の合成 click | 1 回だけ握りつぶす |
| シート表示中 | backdrop タップ / Esc で閉じる（既存どおり） |

- 対象は `.msg-wrap.user` 全体（本文カード・添付・状態バッジ）。
  ただし `button` / `input` / `select` / `textarea` / `[contenteditable]` の上では開始しない
  （「■ 停止」「✏ 再送」などを長押ししてもシートを出さない）。
- 本文中のリンク・添付画像の上でも長押しは有効（チャットアプリ一般の挙動に合わせる）。
  通常タップのリンク遷移・画像拡大は従来どおり。
- 長押し対象の誤操作を避けるため、**タッチ入力がある端末（ハイブリッド含む / `any-pointer: coarse`）**では
  ユーザー発言内のテキスト選択と iOS のコールアウト（`-webkit-touch-callout`）を無効化する。
  コピーはホバーのコピーボタン / シートの「📋 コピー」で行う。
- AI応答カードのテキスト選択は**無効化しない**。
- PC 幅 768px 以下でも、マウス（`pointer: fine`）ならホバーで出す（スマホ判定は幅ではなくポインタ能力で行う）。
- 幅 768px 超のタブレットで長押しした場合は、**必ずシート**（`{ sheet: true }`）を出す。
- オーバーレイ（設定・モーダル・ライトボックス・既に開いているシート・ドロワー）が開いている間は長押しを開始しない
  （`isOverlayOpen()` を利用）。
- 長押しした指を**離さずに**シート内をスクロールすることはできない（発火後の `touchmove` を止めるため）。
  シートをスクロールするときは一度指を離す。シートは最大 60vh で、通常の項目数（6 個以下）はスクロール不要。
- 2 本指以上の同時操作（長押し中に別の指でシートを触る等）は対象外。誤爆防止を優先する。

### 4.6 スマホの「モーダル」は既存 `⋯` シートを再利用する

新規モーダルは作らない。理由:

- 既に `✏️ 編集して再送` / `📋 コピー` / `分岐` / `⏪ 巻き戻し` / `🗑️ 削除` を持っている。
- `backdrop`・Esc・FAB の退避（`updateFabState`）・z-index（66/67）が検証済み。
- 項目の出し分け（最後の発言では巻き戻しを出さない等）が既存ロジックのまま使える。

長押しでは `toggleMoreMenu(wrap, m, isLast, state, { sheet: true, pinWrap: wrap })` を呼ぶ。
`anchor` はボタンではないので `aria-expanded` は付けない（`toggleMoreMenu` 側で `BUTTON` のときだけ付ける）。

### 4.7 アクセシビリティ（キーボード / スクリーンリーダー）

- デスクトップ: 3 ボタンは `opacity:0` でも DOM に残るので Tab で到達できる。
  キーボードでフォーカスが入った瞬間に `.kbd-focus` で表示する。
- アイコンボタンには `title` と `aria-label` を必ず付ける。
  - 編集: `この入力を編集して再送`
  - コピー: `本文をコピー`
  - ⋯: `その他の操作`
- スマホ: 3 ボタンは `display:none` で Tab / SR からも消えるため、ユーザー発言ごとに
  **視覚的には見えないがフォーカス可能な `⋯ 操作` ボタン**（`.msg-more-a11y`）を 1 つ置く。
  スクリーンリーダーのダブルタップ / 外付けキーボードの Enter でシートが開く。
  フォーカス時だけ実体化して見える（`:focus`）。
- Esc でシート/ポップオーバーを閉じたときは、開いたボタンにフォーカスを戻す（あれば）。

### 4.8 送信中（`■ 停止` が見えている間）の扱い

`m.status === "pending" || m.status === "processing"` のユーザー発言は「送信中」とみなす。

| 対象 | 送信中 |
|---|---|
| その発言のホバーグループ（編集 / コピー / ⋯） | 出さない（`display:none`） |
| その発言の `.msg-more-a11y` | 出さない（`display:none`） |
| その発言の長押しシート | 開かない（`pointerdown` と `openUserMsgSheet` で拒否） |
| その発言の `⋯`（どの経路でも） | 開かない（`toggleMoreMenu` の先頭で拒否） |
| `■ 停止`（`.btn-stop-wait`） | 常に表示（押すと `stopTurn(m.id)`） |
| 古い発言の編集（ホバー / `⋯`） | `openEdit` がトーストで拒否（コピー・分岐・削除は可） |
| AI応答カード | 一切変更しない（従来どおり） |

- 判定は **`m.status`** で行う。フッターの `#stopBtn` は `hasWaitingInput()`（全発言のいずれかが pending/processing）で
  出るが、カード側は「その発言が送信中か」で判定する。両者は通常一致する。
- `■ 停止` とホバーグループは**同じバー**に載る。グループを `display:none` にしてもバーの高さは
  `■ 停止`（26px）で維持されるため、**送信中にレイアウトシフトは起きない**。
  応答が返ると `■ 停止` が消えてグループが出るが、バーの高さは変わらない。
- 送信中の発言を長押ししても、シートは開かない。`■ 停止` で止めてから操作する。

---

## 5. DOM 仕様

### 5.1 変更後（ユーザー発言）

```
div.msg-wrap.user[data-mid="<message id>"][.is-busy]
├── div.msg.md.user                       ← 青いカード（本文）
├── div.msg-files                          ← 添付（任意）
├── span.edited-label                      ← 改変済（任意）
├── div.msg-notice                         ← 婉曲表現の復元（任意・現状は assistant のみ）
├── div.gen-image                          ← 画像（任意）
├── div.choices-container[.hover-only-bar]
│   ├── （■ 停止 / 送信エラー + ✏ 再送 / 画像ツール / サムネ）  ← あるときだけ常時表示
│   └── div.msg-hover-actions[role=group][aria-label="この発言の操作"]
│       ├── button.msg-action-btn  (編集 / inline SVG)
│       ├── button.msg-action-btn  (コピー / inline SVG)
│       └── button.btn-more.msg-action-btn (⋯)
└── button.msg-more-a11y           ← CSS でタッチ端末のみ表示（SR / キーボード用）
```

- `wrap.dataset.mid = m.id` を追加（長押し時に現在の `state` からメッセージを引き直すため）。
- `m.role === "user" && (m.status === "pending" || m.status === "processing")` のとき
  `wrap.classList.add("is-busy")`。`.is-busy` の間は `.msg-hover-actions` と `.msg-more-a11y` を
  CSS で `display:none` にする（バー自体は `■ 停止` のため残る）。
- `.hover-only-bar` は「バーの子がホバーグループ 1 つだけ」のときだけ付与。
- `.msg-more-a11y` は全端末で DOM に作ってよい（PC では CSS で `display:none`）。

### 5.2 変更後（AI応答・不変）

```
div.msg-wrap.assistant
├── ...（現行どおり）
└── div.choices-container
    ├── 画像ツール / 返答候補 / サムネ / エラー
    └── button.btn-more   ← 直接の子。ホバーグループに入れない
```

---

## 6. CSS 仕様（`public/style.css`）

### 6.1 `.msg-wrap` に `position: relative` を追加

`.msg-more-a11y` の sr-only 配置基準にする。既存の子に absolute 要素はないため副作用なし。

```css
.msg-wrap { position: relative; }
```

### 6.2 追加ブロック（`.btn-more` / `.more-sheet` の定義の近くに置く）

```css
/* --- ユーザー発言カードのホバー操作（編集 / コピー / ⋯） --- */
.msg-hover-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  order: 2;
  opacity: 0;
  pointer-events: none;
  transform: translateY(2px);
  transition: opacity .14s ease, transform .14s ease;
}
.msg-hover-actions .msg-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  min-width: 26px;
  padding: 0;
  box-sizing: border-box;          /* border 1px を含めて 26px に揃える */
  color: #ccc;
  background: rgba(255, 255, 255, .06);
  border: 1px solid rgba(255, 255, 255, .15);
  border-radius: 50%;
  cursor: pointer;
  font-family: inherit;
  font-size: 16px;
  line-height: 1;
  -webkit-tap-highlight-color: transparent;
  transition: background .15s ease, border-color .15s ease, color .15s ease;
}
.msg-hover-actions .msg-action-btn svg {
  width: 15px;
  height: 15px;
  display: block;
  fill: currentColor;
}
.msg-hover-actions .msg-action-btn:hover,
.msg-hover-actions .btn-more[aria-expanded="true"] {
  background: rgba(255, 255, 255, .12);
  border-color: rgba(255, 255, 255, .28);
  color: #fff;
}
.msg-hover-actions .msg-action-btn:focus-visible {
  outline: 2px solid #8ea2ff;
  outline-offset: 2px;
}

/* 表示条件: マウスホバー / キーボードフォーカス / ⋯ を開いている間 */
@media (hover: hover) and (pointer: fine) {
  .msg-wrap.user:hover .msg-hover-actions {
    opacity: 1;
    pointer-events: auto;
    transform: none;
  }
}
.msg-wrap.user.kbd-focus .msg-hover-actions,
.msg-wrap.user.actions-pinned .msg-hover-actions {
  opacity: 1;
  pointer-events: auto;
  transform: none;
}
@media (prefers-reduced-motion: reduce) {
  .msg-hover-actions { transition: none; transform: none; }
}

/* 背景表示（RP）でも読めるよう、少し濃い下地を敷く */
#chatLog.rp .msg-hover-actions .msg-action-btn,
#chatLog.rp .msg-hover-actions .btn-more {
  background: rgba(16, 16, 24, .62);
  border-color: rgba(255, 255, 255, .28);
  color: #fff;
}
#chatLog.rp .msg-hover-actions .msg-action-btn:hover,
#chatLog.rp .msg-hover-actions .btn-more:hover,
#chatLog.rp .msg-hover-actions .btn-more[aria-expanded="true"] {
  background: rgba(59, 91, 219, .85);
  border-color: #5c7cfa;
}

/* タッチ端末: ボタンは常に隠す（長押しでシート）。予約行ごと畳む */
@media (hover: none), (pointer: coarse) {
  .msg-wrap.user .msg-hover-actions { display: none; }
  .msg-wrap.user .choices-container.hover-only-bar { display: none; }

}

/* タッチ入力がある端末（タッチ対応ノートPC等のハイブリッドを含む）では、
   指の長押しをテキスト選択・iOS のコールアウトより優先させる。
   user-select は継承前提にできないので、本文・添付・リンク・画像まで明示する。
   ※ マウス操作でもユーザー発言のテキスト選択はできなくなるが、
      コピーはホバーのコピーボタン / ⋯ の「📋 コピー」で行える。 */
@media (any-pointer: coarse) {
  .msg-wrap.user,
  .msg-wrap.user * {
    -webkit-touch-callout: none;
    -webkit-user-select: none;
    user-select: none;
  }
}

/* タッチ端末用の読み上げ / キーボード導線（普段は見えない） */
.msg-more-a11y { display: none; }
@media (hover: none), (pointer: coarse) {
  .msg-more-a11y {
    display: block;              /* ← base の display:none を上書きする */
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }
  .msg-more-a11y:focus {
    position: static;
    width: auto;
    height: auto;
    margin: 6px 0 0;
    padding: 4px 10px;
    overflow: visible;
    clip: auto;
    clip-path: none;
    color: #ccc;
    background: rgba(255, 255, 255, .06);
    border: 1px solid rgba(255, 255, 255, .15);
    border-radius: 14px;
    font: inherit;
    font-size: .78rem;
  }
}

/* 送信中（■ 停止 が見えている間）は、その発言の操作ボタンを出さない。
   ■ 停止 はステータス行なので残す。 */
.msg-wrap.user.is-busy .msg-hover-actions,
.msg-wrap.user.is-busy .msg-more-a11y {
  display: none;
}
```

ポイント:

- 高さは `opacity` で隠すだけ。`display:none` にするとホバーのたびに下のメッセージが跳ねる。
- `pointer-events:none` により、消えている間はクリックを奪わない。
- 既存の `.choices-container .btn-more` の `order:2` はグループ内では無害。**AI応答用の `.btn-more` の見た目は変えない**
  （`.msg-action-btn` はホバーグループ内の ⋯ にだけ後から付与する）。
- ボタンの非表示は `@media (hover: none), (pointer: coarse)`、選択抑止は `@media (any-pointer: coarse)` の
  **2 つのメディアクエリ**に分ける。前者はタッチ主体の端末、後者はタッチ対応ノートPC等のハイブリッドも含む。
- `@media (hover: none), (pointer: coarse)` の判定で、タッチ端末の「タップ後に hover が残る」現象も防ぐ。
- 既存の 2464 行付近の `@media (hover: none), (pointer: coarse)` ブロックと競合しない位置に置く
  （`.btn-more:hover` のリセットより後でもよいが、`.msg-hover-actions` は `display:none` なので影響しない）。

---

## 7. JavaScript 仕様（`public/app.js`）

### 7.1 追加する定数・ヘルパー（`// ---- その他の操作（⋯）` セクション内、`textDecensorLine` より前）

> 新規ヘルパーは、テストから抽出しやすいように次のマーカーで囲む。
> `// ---- ユーザー発言カードの操作（テスト抽出開始）`
> `// ---- ユーザー発言カードの操作（テスト抽出終了）`
> 区間内には `MSG_ICON_*` / `isBusyUserMsg` / `copyText` / `copyMessageText` / `makeMoreButton` /
> `buildMsgHoverActions` / `LONG_PRESS_*` / `createLongPressController` / `openUserMsgSheet` を置く。
> `initUserMsgLongPress`（DOM 配線）は区間の直後、かつ **`function textDecensorLine(` より前**に置く（`test_choices_ui.cjs` の抽出範囲に入れないため）。

```js
// ユーザー発言カードの操作アイコン（Material Symbols Outlined / Apache License 2.0）
const MSG_ICON_EDIT =
  '<svg viewBox="0 -960 960 960" aria-hidden="true" focusable="false">' +
  '<path d="M200-200h57l391-391-57-57-391 391v57Zm-80 80v-170l528-527q12-11 26.5-17t30.5-6q16 0 31 6t26 18l55 56q12 11 17.5 26t5.5 30q0 16-5.5 30.5T817-647L290-120H120Zm640-584-56-56 56 56Zm-141 85-28-29 57 57-29-28Z"/>' +
  '</svg>';
const MSG_ICON_COPY =
  '<svg viewBox="0 -960 960 960" aria-hidden="true" focusable="false">' +
  '<path d="M360-240q-33 0-56.5-23.5T280-320v-480q0-33 23.5-56.5T360-880h360q33 0 56.5 23.5T800-800v480q0 33-23.5 56.5T720-240H360Zm0-80h360v-480H360v480ZM200-80q-33 0-56.5-23.5T120-160v-560h80v560h440v80H200Zm160-240v-480 480Z"/>' +
  '</svg>';

// 送信中（■ 停止 が見えている間）のユーザー発言か。
// createMessageWrap / toggleMoreMenu / openUserMsgSheet で共通に使う。
function isBusyUserMsg(m) {
  return !!(m && m.role === "user" && (m.status === "pending" || m.status === "processing"));
}

// LAN の http でもコピーできるようにする（navigator.clipboard は secure context 限定）
async function copyText(text) {
  const t = String(text == null ? "" : text);
  if (!t) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(t);
      return true;
    }
  } catch (e) { /* fallback へ */ }
  // readonly textarea + select() + setSelectionRange() で iOS / Android / PC 共通対応。
  // readonly によりモバイルで仮想キーボードを出さず、改行も保持し、スタブ DOM でも createRange 不要で動く。
  let box = null;
  const prevFocus = typeof document !== "undefined" ? document.activeElement : null;
  try {
    box = document.createElement("textarea");
    box.value = t;
    if (typeof box.setAttribute === "function") box.setAttribute("readonly", "");
    box.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;pointer-events:none;font-size:16px;-webkit-user-select:text;user-select:text;";
    document.body.appendChild(box);
    if (typeof box.focus === "function") box.focus({ preventScroll: true });
    if (typeof box.select === "function") box.select();
    if (typeof box.setSelectionRange === "function") box.setSelectionRange(0, t.length);
    return typeof document.execCommand === "function" ? !!document.execCommand("copy") : false;
  } catch (e) {
    return false;
  } finally {
    if (box && box.parentNode) box.parentNode.removeChild(box);   // 失敗時も DOM を残さない
    if (prevFocus && prevFocus !== box && typeof prevFocus.focus === "function") {
      try { prevFocus.focus({ preventScroll: true }); } catch (e2) {}
    }
  }
}

async function copyMessageText(m) {
  const ok = await copyText(m && m.text ? m.text : "");
  toast(ok ? "コピーしました" : "コピーできませんでした", ok ? 1800 : 4000);
}
```

> `copyText` / `copyMessageText` は**呼び出し時だけ** DOM を触る。トップレベルで `document` を参照しない。
> `test_more_menu.cjs` はこのセクションを丸ごと sandbox で実行するため、トップレベルの DOM アクセスは禁止。

### 7.2 `makeMoreButton`（新設・アシスタントと共通）

```js
function makeMoreButton(m, isLast, s) {
  const moreBtn = document.createElement("button");
  moreBtn.type = "button";
  moreBtn.className = "btn-more";
  moreBtn.textContent = "⋯";
  moreBtn.title = "その他の操作";
  moreBtn.setAttribute("aria-haspopup", "true");
  moreBtn.setAttribute("aria-expanded", "false");
  moreBtn.onclick = () => toggleMoreMenu(moreBtn, m, isLast, s);
  return moreBtn;
}
```

### 7.3 `buildMsgHoverActions`（新設）

```js
function buildMsgHoverActions(m, isLast, s) {
  const box = document.createElement("div");
  box.className = "msg-hover-actions";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "この発言の操作");

  const text = String(m && m.text ? m.text : "").trim();

  if (text) {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "msg-action-btn";
    edit.title = "この入力を編集して再送";
    edit.setAttribute("aria-label", "この入力を編集して再送");
    edit.innerHTML = MSG_ICON_EDIT;       // 固定の SVG 文字列だけ
    edit.onclick = (e) => {
      if (e && typeof e.stopPropagation === "function") e.stopPropagation();
      openEdit(m);
    };
    box.appendChild(edit);

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "msg-action-btn";
    copy.title = "本文をコピー";
    copy.setAttribute("aria-label", "本文をコピー");
    copy.innerHTML = MSG_ICON_COPY;
    copy.onclick = (e) => {
      if (e && typeof e.stopPropagation === "function") e.stopPropagation();
      copyMessageText(m);
    };
    box.appendChild(copy);
  }

  const more = makeMoreButton(m, isLast, s);
  more.classList.add("msg-action-btn");   // ホバーグループ内だけ丸ボタン化
  box.appendChild(more);
  return box;
}
```

- `m.text` が空なら「編集」「コピー」を出さない（⋯ は常に出す）。
- アイコンの `innerHTML` は固定文字列のみ。ユーザー入力を入れない（XSS 対策）。

### 7.4 `toggleMoreMenu` / `closeMoreMenu` の拡張

```js
function toggleMoreMenu(anchorBtn, m, isLast, s, opts = {}) {
  // 送信中の発言は操作させない（ホバー / 長押し / a11y のどの経路でもここで止まる）
  if (isBusyUserMsg(m)) {
    toast("応答中は操作できません。停止してから操作してください。", 4000);
    return;
  }
  const already = !!moreOpen.cur && moreOpen.cur.anchor === anchorBtn;
  closeMoreMenu();
  if (already) return;

  const menu = buildMoreMenu(m, isLast, s);
  const pinWrap = opts.pinWrap
    || (anchorBtn && typeof anchorBtn.closest === "function" ? anchorBtn.closest(".msg-wrap") : null);
  const useSheet = opts.sheet === true || isNarrowScreen();   // 長押しは常にシート
  let backdrop = null;

  if (useSheet) {
    backdrop = document.createElement("div");
    backdrop.className = "more-backdrop";
    menu.classList.add("more-sheet");
    document.body.appendChild(backdrop);
    document.body.appendChild(menu);
    backdrop.addEventListener("click", closeMoreMenu);
    moreOpen.cur = { kind: "sheet", el: menu, backdrop, anchor: anchorBtn, pinWrap };
  } else {
    menu.classList.add("more-popup");
    menu.style.visibility = "hidden";
    document.body.appendChild(menu);
    placeMorePopup(menu, anchorBtn);
    menu.style.visibility = "";
    moreOpen.cur = { kind: "popup", el: menu, backdrop: null, anchor: anchorBtn, pinWrap };
  }

  if (pinWrap && pinWrap.classList) pinWrap.classList.add("actions-pinned");

  // aria-expanded は実ボタンのときだけ（長押しの anchor は div）
  if (anchorBtn && anchorBtn.tagName === "BUTTON" && typeof anchorBtn.setAttribute === "function") {
    anchorBtn.setAttribute("aria-expanded", "true");
  }
  document.addEventListener("keydown", moreOnKey, true);
  document.addEventListener("click", moreOnDocClick, true);
  document.addEventListener("scroll", moreOnReflow, true);
  window.addEventListener("resize", moreOnReflow);
  updateFabState();
}
```

```js
function closeMoreMenu() {
  const cur = moreOpen.cur;
  if (!cur) return;
  moreOpen.cur = null;
  unbindMoreGlobal();
  if (cur.pinWrap && cur.pinWrap.classList) cur.pinWrap.classList.remove("actions-pinned");
  if (cur.anchor && cur.anchor.tagName === "BUTTON" && typeof cur.anchor.setAttribute === "function") {
    cur.anchor.setAttribute("aria-expanded", "false");
  }
  if (cur.el && cur.el.parentNode) cur.el.parentNode.removeChild(cur.el);
  if (cur.backdrop && cur.backdrop.parentNode) cur.backdrop.parentNode.removeChild(cur.backdrop);
  updateFabState();
}
```

Esc のフォーカス復帰:

```js
function moreOnKey(e) {
  if (e.key !== "Escape") return;
  e.stopPropagation();
  const anchor = moreOpen.cur && moreOpen.cur.anchor;
  closeMoreMenu();
  if (anchor && anchor.tagName === "BUTTON" && anchor.isConnected !== false) {
    try { anchor.focus(); } catch (err) {}
  }
}
```

- `already` の判定は `anchor` の同一性で行う（現行どおり）。長押しの anchor は `wrap` なので、同じカードを再度長押しすると閉じる。
- `pinWrap` は `opts.pinWrap` 優先。デスクトップの `⋯` から開いた場合は `anchorBtn.closest(".msg-wrap")` で自動的にカードを特定できる。

### 7.5 `createMessageWrap` の末尾を差し替え

```js
  // その他の操作（⋯）:
  //  - AI応答は従来どおり常時表示（ホバーグループに入れない）
  //  - ユーザー発言はホバー/キーボードで出すグループに入れる（モバイルは長押し）
  if (m.role === "user") {
    bar.appendChild(buildMsgHoverActions(m, isLast, s));
    if (bar.children.length === 1) bar.classList.add("hover-only-bar");
    wrap.appendChild(bar);

    const a11y = document.createElement("button");
    a11y.type = "button";
    a11y.className = "msg-more-a11y";
    a11y.textContent = "⋯ 操作";
    a11y.title = "この発言の操作メニューを開く";
    a11y.setAttribute("aria-label", "この発言の操作メニューを開く");
    a11y.setAttribute("aria-haspopup", "true");
    a11y.setAttribute("aria-expanded", "false");
    a11y.onclick = () => toggleMoreMenu(a11y, m, isLast, s, { sheet: true, pinWrap: wrap });
    wrap.appendChild(a11y);
  } else {
    bar.appendChild(makeMoreButton(m, isLast, s));
    wrap.appendChild(bar);
  }

  return wrap;
```

`createMessageWrap` の先頭付近、`wrap.id = "msg-" + m.id;` の直後に追加:

```js
  if (m.id) wrap.dataset.mid = m.id;
  // 送信中は ■ 停止 だけを出す（ホバー / 長押し / ⋯ を出さない）
  if (isBusyUserMsg(m)) wrap.classList.add("is-busy");
```

> `bar.children.length === 1` は、画像ツール・サムネ・エラーバッジ・停止ボタンなどをすべて append した**後**に判定する。
> 送信中でもホバーグループは DOM に残す（CSS で隠す）ため、バーの子は「停止 + グループ」の 2 つ以上になり
> `hover-only-bar` にはならない。`■ 停止` が消えるとグループ 1 つになり、タッチ端末では行ごと畳まれる（既存どおり）。
> `hasImage` のユーザー発言では画像ツールが残るため `hover-only-bar` にならず、常時表示のまま（意図どおり）。

### 7.6 長押しコントローラ（新設）

`// ---- その他の操作（⋯）` セクション内、**`textDecensorLine` より前**（＝ `createMessageWrap` より前）に置く。
**トップレベルでイベント登録しない。** テスト抽出マーカーの内側に置く。

```js
const LONG_PRESS_MS = 520;
const LONG_PRESS_MOVE_PX = 10;
// 長押し直後の「合成 click」を 1 回だけ握りつぶすためのフラグ。
// 時間窓ではなく「指を離した後の次の pointerdown / keydown で解除」するので、
// 合成 click が発生しない端末でも次の意図的なタップを飲み込まない。
let swallowNextClick = false;

function createLongPressController(opts) {
  const delay = opts.delay;
  const tolerance = opts.tolerance;
  let timer = null;
  let sx = 0, sy = 0;
  let target = null;
  let fired = false;

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
  }
  function reset() {
    clearTimer();
    target = null;
    fired = false;
  }

  function trigger() {
    clearTimer();
    if (!target || target.isConnected === false) { reset(); return false; }
    if (opts.isEnabled && !opts.isEnabled()) { reset(); return false; }
    const el = target;
    fired = true;
    swallowNextClick = true;   // 指を離したときの合成 click を 1 回だけ止める
    if (opts.onLongPress(el) === false) {
      swallowNextClick = false;
      reset();
      return false;
    }
    try { if (typeof navigator !== "undefined" && navigator.vibrate) navigator.vibrate(10); } catch (e) {}
    return true;
  }

  function start(x, y, el) {
    if (opts.isEnabled && !opts.isEnabled()) return false;
    reset();
    sx = x; sy = y; target = el; fired = false;
    timer = setTimeout(() => {
      timer = null;
      trigger();
    }, delay);
    return true;
  }

  function move(x, y) {
    if (!target || fired) return;
    if (Math.hypot(x - sx, y - sy) > tolerance) reset();  // スクロール優先
  }

  function cancel() {
    if (fired) return;   // 発火後の scroll 等では状態を壊さない
    reset();
  }

  function end() {
    clearTimer();
    const did = fired;
    target = null;
    fired = false;
    return did;
  }

  return {
    start, move, cancel, end, trigger,
    get active() { return !!target; },
    get fired() { return fired; },
  };
}

function openUserMsgSheet(wrap) {
  if (!state || !wrap) return false;
  const mid = wrap.dataset && wrap.dataset.mid ? wrap.dataset.mid : "";
  const idx = Array.isArray(state.messages) ? state.messages.findIndex((x) => x && x.id === mid) : -1;
  if (idx < 0) return false;
  const m = state.messages[idx];
  if (!m || m.role !== "user") return false;
  if (isBusyUserMsg(m)) return false;   // 送信中は開かない
  toggleMoreMenu(wrap, m, idx === state.messages.length - 1, state, { sheet: true, pinWrap: wrap });
  return true;
}
```

### 7.7 初期化（下部の初期化 IIFE 内で 1 回だけ呼ぶ）

```js
function initUserMsgLongPress() {
  if (!log || typeof log.addEventListener !== "function") return;

  const pickWrap = (t) => (t && typeof t.closest === "function") ? t.closest(".msg-wrap.user") : null;
  const isBlocked = (t) => !!(t && typeof t.closest === "function"
    && t.closest("button, input, select, textarea, [contenteditable]:not([contenteditable='false'])"));

  const lp = createLongPressController({
    delay: LONG_PRESS_MS,
    tolerance: LONG_PRESS_MOVE_PX,
    // 既に別の ⋯ ポップオーバー/シートが開いているなら開始しない
    // （.more-popup は isOverlayOpen() の対象外なので、moreOpen.cur も見る）
    isEnabled: () => !isOverlayOpen() && !moreOpen.cur && !!state,
    onLongPress: (wrap) => openUserMsgSheet(wrap),
  });

  // 長押し成立後のドラッグで下の #chatLog がスクロールしないようにする。
  // ネイティブの touchmove は passive にできないので、ジェスチャ中だけ登録する。
  const lockTouchMove = (e) => {
    if (lp.fired && e.cancelable) e.preventDefault();
  };
  const addLock = () => window.addEventListener("touchmove", lockTouchMove, { passive: false });
  const removeLock = () => window.removeEventListener("touchmove", lockTouchMove);

  let activePointerId = null;

  const begin = (x, y, t) => {
    const wrap = pickWrap(t);
    if (!wrap || isBlocked(t)) return false;
    if (wrap.classList && wrap.classList.contains("is-busy")) return false;   // 送信中は長押ししない
    if (!lp.start(x, y, wrap)) return false;
    addLock();
    return true;
  };

  log.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") return;   // マウスはホバーUIに任せる
    // 2 本目の指（ピンチズーム等）が置かれたら長押しを即キャンセル
    if (e.isPrimary === false || activePointerId !== null) {
      activePointerId = null;
      lp.cancel();
      if (!lp.fired) removeLock();
      return;
    }
    if (begin(e.clientX, e.clientY, e.target)) {
      activePointerId = e.pointerId !== undefined ? e.pointerId : -1;
    }
  });

  // PointerEvent 非対応の古い iOS 用フォールバック
  if (!window.PointerEvent) {
    log.addEventListener("touchstart", (e) => {
      if (!e.touches || e.touches.length !== 1) {
        lp.cancel();
        if (!lp.fired) removeLock();
        return;
      }
      begin(e.touches[0].clientX, e.touches[0].clientY, e.target);
    }, { passive: true });
  }

  window.addEventListener("pointermove", (e) => {
    if (activePointerId !== null && e.pointerId !== undefined && e.pointerId !== activePointerId) return;
    lp.move(e.clientX, e.clientY);
    if (!lp.active && !lp.fired) removeLock();
  });
  const onPointerEnd = (e) => {
    if (activePointerId !== null && e && e.pointerId !== undefined && e.pointerId !== activePointerId) return;
    activePointerId = null;
    lp.end();
    removeLock();
  };
  window.addEventListener("pointerup", onPointerEnd);
  window.addEventListener("pointercancel", onPointerEnd);  // fired 状態も必ず解除

  if (!window.PointerEvent) {
    window.addEventListener("touchend", (e) => {
      if (lp.fired && e.cancelable) e.preventDefault();  // 合成 click を止める
      lp.end();
      removeLock();
    }, { passive: false });
    window.addEventListener("touchcancel", () => { lp.end(); removeLock(); }, { passive: true });
  }

  // Android のネイティブ長押しメニューは、直前にタッチで押した時だけ横取りする。
  // シート表示中は指の下が backdrop になるため、pointerdown は document で拾う。
  let lastTouchDownAt = 0;
  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse") lastTouchDownAt = Date.now();
  }, true);
  const isTouchContextMenu = () => Date.now() - lastTouchDownAt <= 1000;

  // 長押し中/直後は、対象が backdrop でもネイティブメニューを出さない
  document.addEventListener("contextmenu", (e) => {
    if (!isTouchContextMenu()) return;
    if (lp.active || lp.fired || moreOpen.cur) e.preventDefault();
  }, true);

  log.addEventListener("contextmenu", (e) => {
    if (!isTouchContextMenu()) return;                 // マウスの右クリックは通常メニュー
    const wrap = pickWrap(e.target);
    if (!wrap || isBlocked(e.target)) return;
    if (wrap.classList && wrap.classList.contains("is-busy")) { e.preventDefault(); return; }  // 送信中
    if (!lp.active && !lp.fired) return;               // スクロール等で取り消し済み → ネイティブに任せる
    e.preventDefault();                                 // 長押し候補/シート表示中だけ止める
    if (moreOpen.cur || isOverlayOpen()) return;       // 既に開いているなら何もしない
    if (!lp.fired) {
      // 520ms タイマーより先に contextmenu が来た場合も trigger() で同じ発火状態（fired=true）に遷移させる
      lp.trigger();
    }
  });

  // スクロールが始まったら長押しを取り消す（発火後は何もしない）
  log.addEventListener("scroll", () => {
    if (!lp.fired) { lp.cancel(); removeLock(); }
  }, { passive: true });

  // 長押し直後の合成 click を 1 回だけ握りつぶす（シート項目の誤爆防止 & シート即閉じ防止）。
  // pointerdown が .msg.user、pointerup が .more-backdrop/.more-sheet になると合成 click の target が
  // document.body になり、同じ document capture 上の moreOnDocClick が走るとシートが即座に閉じてしまう。
  // stopImmediatePropagation() で同一 document 上の後続 capture リスナー（moreOnDocClick）も確実に止める。
  document.addEventListener("click", (e) => {
    if (!swallowNextClick) return;
    swallowNextClick = false;
    e.preventDefault();
    e.stopPropagation();
    if (typeof e.stopImmediatePropagation === "function") e.stopImmediatePropagation();
  }, true);

  // 指を離した後の新しいジェスチャ / キー操作が始まったら「握りつぶし」を解除する。
  // これにより、合成 click が無い端末でも次の意図的なタップや Enter 操作を飲み込まない。
  // ※ focusin は pointerup → click の間に発火するブラウザがあり、合成 click より前に解除されると危険なため使わない。
  // ※ 長押し継続中（lp.active / lp.fired）は解除しない（2本目の指やキー押下でガードが外れるのを防ぐ）。
  window.addEventListener("pointerdown", () => {
    if (!lp.fired) swallowNextClick = false;
  }, true);
  window.addEventListener("keydown", () => {
    if (!lp.active && !lp.fired) swallowNextClick = false;
  }, true);

  // キーボード由来のフォーカスだけで操作を出す（マウス操作後や Alt+Tab 復帰時に残らないようにする）
  let lastInputWasKeyboard = false;
  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key === "Tab" || e.key === "Enter" || e.key === " " || e.key === "Escape" || (typeof e.key === "string" && e.key.startsWith("Arrow"))) {
      lastInputWasKeyboard = true;
    }
  }, true);
  window.addEventListener("pointerdown", () => { lastInputWasKeyboard = false; }, true);
  log.addEventListener("focusin", (e) => {
    const w = pickWrap(e.target);
    if (w && lastInputWasKeyboard) w.classList.add("kbd-focus");
  });
  log.addEventListener("focusout", (e) => {
    const w = pickWrap(e.target);
    if (w && (!e.relatedTarget || !w.contains(e.relatedTarget))) w.classList.remove("kbd-focus");
  });
}
```

`initUserMsgLongPress` は**テスト抽出マーカーの外側**（`// ---- ユーザー発言カードの操作（テスト抽出終了）` の直後、かつ **`function textDecensorLine(` より前**）に置く。
初期化 IIFE（`(async () => { ... })()`）の先頭、`await loadOptions();` より前に追加:

```js
  initUserMsgLongPress();
```

### 7.8 `buildMoreMenu` のコピー項目

```js
item("📋 コピー", () => copyMessageText(m));
```

### 7.9 バージョン更新（`public/index.html`）

```html
<link rel="stylesheet" href="style.css?v=126">
<script src="app.js?v=105"></script>
```

### 7.10 `openEdit` / `saveEdit` の送信中ガード

`openEdit` の先頭（`editTarget = m;` より前）:

```js
function openEdit(m) {
  // 応答中（■ 停止 が見えている間、または送信/停止リクエスト通信中）は、どのユーザー発言も編集させない。
  // 編集すると会話が巻き戻り、生成中の応答が stale として捨てられるため。
  const busyTurn = (typeof hasWaitingInput === "function" && hasWaitingInput())
    || (typeof sending !== "undefined" && sending)
    || (typeof stopping !== "undefined" && stopping);
  if (m && m.role === "user" && busyTurn) {
    toast("応答中は編集できません。停止してから操作してください。", 4000);
    return;
  }
  editTarget = m;
  // ...既存のまま...
}
```

`saveEdit` の `if (!m || !text) return;` の直後:

```js
  // モーダルを開いた後に送信が始まったケースの再確認
  const busyTurn = (typeof hasWaitingInput === "function" && hasWaitingInput())
    || (typeof sending !== "undefined" && sending)
    || (typeof stopping !== "undefined" && stopping);
  if (m.role === "user" && busyTurn) {
    toast("応答中は保存できません。停止してから操作してください。", 4000);
    return;
  }
```

- `hasWaitingInput()` は function 宣言（`function hasWaitingInput()`）なので巻き上げられ、`openEdit` から呼べる。
  `typeof` ガードはテスト用サンドボックス対策。`sending` / `stopping` も併せて見ることで `/api/send` 応答前の通信中も保護する。
- `openEdit` は `✏ 再送`（status `error`）からも呼ばれる。応答中でなければ従来どおり開く。
- サーバーは変更しない。仮に古いクライアントが編集を送っても、生成中の応答は `reply_to` 不一致で
  stale として捨てられ、会話が壊れることはない（既存の防御）。

---

## 8. 長押しのイベントフロー（バグ防止の要）

```
[指を置く]
  pointerdown (pointerType=touch, isPrimary=true)
    → .msg-wrap.user を特定 / button等なら中止 / 2本目の指なら即キャンセル
    → lp.start(x, y, wrap) … 520ms タイマー開始
    → lockTouchMove を登録
[520ms 経過（または Android の contextmenu 先行発火）]
    → lp.trigger() → lp.fired = true / swallowNextClick = true / 触覚
    → openUserMsgSheet(wrap) → シート + backdrop を body に追加
[指を離す]
    → pointerup → lp.end() → lockTouchMove を解除
    → 直後の合成 click は document capture で preventDefault + stopPropagation + stopImmediatePropagation
       （指の下にシートの「削除」が来ていても誤爆せず、target が body になっても moreOnDocClick で即閉じしない）
[次の操作]
    → 指を離した後の新しい pointerdown / keydown で swallowNextClick = false
       （合成 click が無い端末でも、次の意図的なタップを飲み込まない）
```

- 520ms 未満で離した: 通常のタップ。何も起きない（リンク・画像の通常動作は維持）。
- 10px 以上動いた: `lp.reset()`。スクロールは通常どおり。
- 発火後に動いた: `touchmove` を `preventDefault`（ジェスチャ中だけ登録したロック）して下のスクロールを止める。
- `pointercancel` / `touchcancel`: `lp.end()` で **fired 状態も解除**し、スクロールロックを解除する。
- オーバーレイ表示中 (`isOverlayOpen()`) / 既に別の `⋯` が開いている (`moreOpen.cur`) / `state` 未読込: 開始しない。
- ポーリングで `render()` されラップが作り直された: `target.isConnected === false` で発火を中止。
- メッセージが削除済み: `state.messages` に id が無いので `openUserMsgSheet` が中断。

---

## 9. エッジケース・チェックリスト

| # | ケース | 期待 | 対策 |
|---|---|---|---|
| 1 | ホバーで下のメッセージが動く | 動かない | `opacity` のみ。行の高さは維持 |
| 2 | マウスを ⋯ ポップオーバーへ移動 | ボタンが消えない | `.actions-pinned` |
| 3 | マウスで ⋯ を閉じて外側クリック | ボタンが消える | `:focus-within` を使わず `.kbd-focus`（キーボード時のみ） |
| 4 | 消えているボタンのクリック | 反応しない | `pointer-events:none` |
| 5 | タップ後に hover が残る | ボタンが出ない | `@media (hover:none),(pointer:coarse)` で `display:none` |
| 6 | 長押し直後に「削除」誤爆 / シート即閉じ | 起きない | 合成 click を capture で `preventDefault` + `stopPropagation` + `stopImmediatePropagation` で 1 回握りつぶす（指を離した後の pointerdown/keydown で解除） |
| 7 | 長押し後に指を動かしてスクロール | 下が動かない | 発火後は `touchmove` を `preventDefault`（ジェスチャ中だけ非 passive で登録。`contextmenu` 先行発火時も `lp.trigger()` で `fired=true` にする） |
| 8 | スクロールしながらの長押し | シートが出ない | 10px 移動でキャンセル（同時に `removeLock()` で `touchmove` リスナーも解除） |
| 9 | iOS の選択メニュー/虫眼鏡 | 出ない | `user-select:none` + `-webkit-touch-callout:none`（`any-pointer: coarse` のユーザー発言のみ） |
| 10 | Android のネイティブ長押しメニュー | 出ない | `contextmenu` をタッチ直後だけ `preventDefault` し、未発火なら `lp.trigger()` でシートを開く |
| 11 | PC の右クリック | 通常のブラウザメニュー | 直前 1 秒にタッチが無ければ `contextmenu` を触らない |
| 12 | 送信中ユーザー発言 | `■ 停止` だけが見える。3 ボタン / `⋯` / 長押しは出ない | `.is-busy` + `toggleMoreMenu` / `openUserMsgSheet` / `openEdit` ガード |
| 13 | 送信エラー発言 | エラー + `✏ 再送` は見える。3 ボタンは通常どおり（応答中でなければ編集可） | `status === "error"` では `.is-busy` を付けない |
| 14 | 本文が空の発言 | 編集・コピーを出さない | `text.trim()` 判定 |
| 15 | 添付のみ（本文なし） | ホバー/長押しは効く | 対象は `.msg-wrap.user` 全体。画像・リンクも長押し可 |
| 16 | RP（背景表示） | アイコンが読める | `#chatLog.rp` 用の濃い下地 |
| 17 | 320px 幅 | 3 ボタンが収まる | 26px × 3 + gap。行は既存の `flex-wrap` |
| 18 | 幅 768px 超のタブレット長押し | シートが出る | `{ sheet: true }` を強制 |
| 19 | 幅 768px 以下の PC 窓 | ホバーで出る | 幅ではなく `hover`/`pointer` で CSS 判定 |
| 20 | スクリーンリーダー（スマホ） | 操作に到達できる | `.msg-more-a11y` |
| 21 | Tab キー | 3 ボタンに到達し、見える | `opacity:0` のまま DOM に残す + `.kbd-focus`（修飾キーや `Alt+Tab` 復帰では付けない） |
| 22 | Esc で閉じる | フォーカスが開いたボタンへ戻る | `moreOnKey` |
| 23 | ポーリングで再描画 | 開いていたシートは閉じる（既存仕様） | 変更なし。`closeMoreMenu()` が `render()` から呼ばれる |
| 24 | オーバーレイ表示中 / 別の ⋯ 表示中の長押し | 何も起きない | `isOverlayOpen()` + `moreOpen.cur` |
| 25 | コピー（LAN の http） | コピーできる | `readonly textarea` + `execCommand` フォールバック + トースト（元のフォーカスも復元） |
| 26 | カードの外側（余白）ホバー | 出てもよい | `.msg-wrap.user` の箱に含まれるため。実害なし |
| 27 | アシスタント発言 | 一切変わらない | 分岐は `m.role === "user"` のみ |
| 28 | 非常に長い本文 | ボタンはメッセージ末尾の右下（末尾までスクロールして操作） | カード内固定にしない（本文を隠さないため） |
| 29 | 長押し中に別の指で操作（ピンチズーム等） | 長押しを即キャンセル（誤爆防止を優先） | `e.isPrimary === false` または 2 本目の `pointerdown` / `touchstart` で `lp.cancel()` |
| 30 | 長押ししたままシートをスクロール | できない（一度離してから） | 発火後は `touchmove` を抑止 |
| 31 | タッチが主入力 + マウス併用（稀） | ホバー行は非表示。タッチ/ペン長押しは可 | `hover`/`pointer` の primary 判定。必要なら `any-hover` 対応を追加 |
| 32 | 送信中に古い発言を編集 | トーストで拒否 | `openEdit` / `saveEdit` の `hasWaitingInput()` / `sending` / `stopping` ガード |
| 33 | 送信中にフッターの `■` を押す | 停止して入力欄に戻る（既存） | `stopTurn()` は変更しない |

---

## 10. 非回帰（既存機能への影響）

- `test/test_choices_ui.cjs`: `textDecensorLine` 〜 `createMessageWrap` を抽出する。新しい関数（`initUserMsgLongPress` を含む）はすべて
  `textDecensorLine` より**前**に置くため抽出対象外。影響なし。
- `test/test_more_menu.cjs`: 抽出範囲に新関数が入る。**トップレベル副作用なし**を厳守。
  `El` スタブに `closest` が無いため、`toggleMoreMenu` 側は `typeof anchorBtn.closest === "function"` でガードする。
- アシスタントの `⋯` は `makeMoreButton()` 経由でもクラス・ラベル・`aria-*`・`onclick` が同一。
- `⋯` シートの z-index / backdrop / FAB 退避 / `isOverlayOpen()` は変更しない。
- `server.py` / 保存データ / API は変更しない。送信中の編集は **UI だけで拒否**する。
- 既存は送信中でも `⋯` から編集できたが、本仕様では `.is-busy` と `openEdit` ガードで禁止する
  （生成中の応答が捨てられるのを防ぐための、意図的な挙動変更）。

---

## 11. テスト計画

### 11.1 既存テストの更新（`test/test_more_menu.cjs`）

1. `El` スタブに `closest(sel)` と `focus()`、`select()`、`setSelectionRange()` を追加（`closest` は `parentNode` を辿り、`sel` が `.` 始まりなら `cur.classList.contains(sel.slice(1))` で判定する。`className ===` だと `"msg-wrap user"` に一致しないため注意）。
2. `toggleMoreMenu(btn, m, isLast, s, { sheet: true })` を `innerWidth=1200` で呼び、`.more-sheet` + `.more-backdrop` が出ることを追加。
3. `pinWrap`:
   - `wrap` スタブ（`.msg-wrap user`）を渡して開く → `wrap.classList.contains("actions-pinned") === true`
   - `closeMoreMenu()` → `false`
4. Esc: 開いたボタンに `focus()` が呼ばれること（スタブで記録）。
5. [5] のコピー（`.cjs` はトップレベル `await` 不可のため、`[5]` から末尾の集計・`process.exit` までを `(async () => { ... })()` で囲む）:
   - `sandbox.isSecureContext = true`、`navigator.clipboard.writeText` をスパイにし、
     `item.onclick()` の直後に `await new Promise((r) => setTimeout(r, 0))` して
     本文が渡り、`toast("コピーしました", 1800)` が呼ばれること
     （`item.onclick` は `copyMessageText` の Promise を返さないため、1 tick 待つ）。
   - 失敗時（`writeText` が throw、`execCommand` も false）に `toast("コピーできませんでした", 4000)`。
6. 既存の [1]〜[4] がすべてそのまま PASS すること（項目・順序・ポップオーバー位置・リスナー数）。

### 11.2 新規 `test/test_user_msg_actions.cjs`

`public/app.js` の `// ---- ユーザー発言カードの操作（テスト抽出開始）` 〜
`// ---- ユーザー発言カードの操作（テスト抽出終了）` をスタブ DOM で実行して検証
（`node test/test_user_msg_actions.cjs`）。
スタブとして `document` / `navigator` / `toast` / `openEdit` / `toggleMoreMenu` / `state` を用意する。

- [A] `buildMsgHoverActions(user, false, st)`
  - 子が 3 つ（edit / copy / more）、順序が 編集→コピー→⋯。
  - クラス `msg-action-btn`、`title` / `aria-label` が仕様どおり。
  - 編集クリック → `openEdit(m)` が呼ばれる。
  - コピークリック → `copyMessageText(m)` が呼ばれる。
  - ⋯ クリック → `toggleMoreMenu` が呼ばれる。
  - SVG が `focusable="false"` / `aria-hidden="true"`。
- [B] `m.text = "   "` → 子が 1 つ（⋯ のみ）。
- [C] `createLongPressController({ delay: 60, tolerance: 10, ... })`
  - 80ms 後に発火。`fired === true`（タイマーの揺らぎを考慮して余裕を取る）。
  - 20ms で `end()` → その 80ms 後も発火しない。
  - 11px 移動 → 発火しない。
  - 9px 移動 → 80ms 後に発火する。
  - `isEnabled` が false → 発火しない（`start()` が `false` を返す）。
  - `trigger()` でタイマー満了前に即時発火でき、タイマー満了時に二重発火しない。
  - 発火中に `cancel()` → `fired` のまま（scroll で壊れない）。
  - `end()` で `fired === false` / `active === false`。
  - 二重 `start` でタイマーが 1 本だけ。
- [D] `openUserMsgSheet(wrap)`
  - `wrap.dataset.mid` と `state.messages` から `m` / `isLast` を解決。
  - 存在しない id / assistant の id → `toggleMoreMenu` を呼ばず `false` を返す。
- [E] CSS 静的検証（`public/style.css` を文字列で読む）
  - `.msg-hover-actions` に `opacity: 0` と `pointer-events: none`。
  - `@media (hover: hover)` 内に `:hover .msg-hover-actions`。
  - `.actions-pinned` / `.kbd-focus` の表示規則。
  - `@media (hover: none), (pointer: coarse)` に `.msg-hover-actions { display: none; }`。
  - `@media (any-pointer: coarse)` に `user-select: none` と `-webkit-touch-callout: none`。
  - `.hover-only-bar` の非表示規則。
  - `.msg-more-a11y` の存在。
- [F] JS 静的検証（`public/app.js` を文字列で読む）
  - `document.addEventListener("click", ...)` の capture 抑止（`swallowNextClick` と `stopImmediatePropagation`）がある。
  - `document.addEventListener("contextmenu", ...)` のネイティブメニュー抑止がある。
  - `touchmove` のロック（`passive: false`）がある。
  - `createMessageWrap` が `m.role === "user"` のときだけ `buildMsgHoverActions` を呼ぶ。
  - アシスタント側は `makeMoreButton` を `.choices-container` に直接 append する。
  - `toggleMoreMenu` の先頭に `isBusyUserMsg(m)` のガードがある。
  - `initUserMsgLongPress` の `begin` に `.is-busy` のスキップがある。
- [G] 送信中（`.is-busy`）
  - `isBusyUserMsg` が `pending` / `processing` のユーザー発言で true、
    `answered` / `error` / 未設定 / assistant で false。
  - `toggleMoreMenu` はマーカー外なので、`function toggleMoreMenu(` 〜 `// ---- ユーザー発言カードの操作（テスト抽出開始）`（または `test_more_menu.cjs` と同じ抽出範囲）を別途抽出し、pending のユーザー発言で呼ぶと `toast` が呼ばれ、`.more-menu` が作られないことを確認する。
  - `openUserMsgSheet` を pending のユーザー発言で呼ぶと、`toggleMoreMenu` が呼ばれず `false` を返す。
  - `createMessageWrap` はマーカー外なので、ソースに
    `if (isBusyUserMsg(m)) wrap.classList.add("is-busy");` が含まれることを静的に確認する
    （巨大な関数を再実行する必要はない）。
  - `openEdit` もマーカー外なので、`function openEdit(` 〜 `async function saveEdit(` を別途抽出し、
    `hasWaitingInput() === true` で `openEdit({ role: "user" })` を呼ぶと `toast` が呼ばれ、
    `editTarget` が変わらないこと（ブロックされる経路だけ）を確認する。
    `saveEdit` は `alert` / `confirm` が要るため、ガード文字列の静的確認でもよい。
  - CSS に `.msg-wrap.user.is-busy .msg-hover-actions` と `.msg-wrap.user.is-busy .msg-more-a11y` の
    `display: none` がある。

### 11.3 回帰テスト（手動実行）

```
node test/test_more_menu.cjs
node test/test_user_msg_actions.cjs
node test/test_choices_ui.cjs
node test/test_mobile_layout.cjs
node test/test_fab_state.cjs
python tools/run_tests.py
```

### 11.4 手動 QA マトリクス

| 環境 | 確認 |
|---|---|
| Chrome / Edge / Firefox / Safari (PC) | ホバー表示、レイアウトシフト無し、⋯ ピン留め、Esc、Tab、コピー |
| Safari (iPhone) | 長押しでシート、選択メニューが出ない、削除誤爆なし、スクロール、コピー |
| Chrome (Android) | 長押しでシート、contextmenu 抑止、戻る操作、削除誤爆なし |
| iPad + タッチ | 768px 超でもシート |
| Surface 等（タッチ + マウス） | マウスはホバー、指は長押し |
| RP モード | アイコンのコントラスト、背景画像上での視認性 |
| 320px 幅 | 3 ボタンがはみ出さない |
| 送信中 / エラー | `■ 停止` / `✏ 再送` が常に見える。送信中の発言には 3 ボタン / `⋯` / 長押しが出ない。古い発言の編集はトーストで拒否 |
| AI応答 | 画像操作・候補・⋯ が従来どおり常時表示 |

---

## 12. 実装手順（この順で）

1. `public/style.css`
   - `.msg-wrap { position: relative; }`
   - 6.2 のブロックを追加（**ボタン非表示の `(hover:none),(pointer:coarse)`** と
     **選択抑止の `(any-pointer: coarse)`** の 2 つ、および **`.is-busy` の非表示規則**）。
2. `public/app.js`（`// ---- その他の操作（⋯）` セクション、`textDecensorLine` より前）
   - 7.1〜7.3（アイコン / コピー / `makeMoreButton` / `buildMsgHoverActions`）を追加。
   - 7.6（長押しコントローラ / `openUserMsgSheet`）を追加。
   - ここまでを次の 2 行で囲む:
     `// ---- ユーザー発言カードの操作（テスト抽出開始）`
     `// ---- ユーザー発言カードの操作（テスト抽出終了）`
   - 7.7（`initUserMsgLongPress`）は**マーカーの直後（かつ `function textDecensorLine(` より前）**に追加し、下部 IIFE で 1 回呼ぶ。
   - 7.4（`toggleMoreMenu` / `closeMoreMenu` / `moreOnKey`）を拡張。
   - 7.5（`createMessageWrap` 末尾）を差し替え、`wrap.dataset.mid` と `is-busy` を追加。
   - 7.8（コピー項目）を差し替え。
   - 7.10（`openEdit` / `saveEdit` の送信中ガード）を追加。
   - 7.4 の `toggleMoreMenu` 先頭、7.6 の `openUserMsgSheet`、7.7 の `begin` / `contextmenu` に
     `pending` / `processing` の拒否を追加。
3. `public/index.html` の `?v=` を更新。
4. `test/test_more_menu.cjs` を更新。
5. `test/test_user_msg_actions.cjs` を追加。
6. 11.3 のテストと 11.4 の手動 QA。

---

## 13. 受け入れ基準（Definition of Done）

- [ ] PC: ユーザー発言カードをホバーすると、右下に 編集 / コピー / ⋯ がフェードインする。
- [ ] PC: ホバーを外すと 3 ボタンが消える。ホバー前後で他のメッセージの位置が 1px も動かない。
- [ ] PC: 3 ボタンはキーボード（Tab）で到達でき、フォーカス時に見える。
- [ ] PC: ⋯ を開いている間はボタンが消えない。Esc / 外側クリックで閉じる。Esc のときは開いたボタンにフォーカスが戻る。
- [ ] 編集ボタンで `openEdit` が開き、保存で既存の「編集して再送」フローになる。
- [ ] コピーで本文がクリップボードに入り、トーストが出る（LAN の http でも動く）。
- [ ] スマホ: 通常は 3 ボタンが見えない。ユーザー発言を長押しすると既存の ⋯ シートが開く。
- [ ] スマホ: 長押し後に指を離しても、シートの項目が誤爆しない（特に削除）。
- [ ] スマホ: 長押し中に 10px 以上動かすとシートが出ず、スクロールできる。
- [ ] スマホ: 送信中 / エラー時の `■ 停止` / `✏ 再送` は常に見える。
- [ ] 送信中（`■ 停止` が見えている間）: その発言に 3 ボタン / `⋯` / 長押しが出ず、編集もできない。
      古い発言の編集はトーストで拒否される。AI応答カードは不変。
- [ ] スマホ: ユーザー発言の長押しで iOS の選択メニュー / 虫眼鏡が出ない。AI応答の本文は選択できる。
- [ ] 添付のみ（本文なし）の発言でも、ホバー / 長押しで操作できる。
- [ ] RP（背景表示）モードでも 3 ボタンのアイコンが読める。
- [ ] キーボード / スクリーンリーダーでユーザー発言の操作に到達できる。
- [ ] AI応答カードの DOM・表示・操作が現行と同一（既存テスト + 目視）。
- [ ] `test/test_more_menu.cjs` の既存ケースがすべて PASS。
- [ ] 新規 `test/test_user_msg_actions.cjs` がすべて PASS。
- [ ] `python tools/run_tests.py` が OK。
- [ ] `style.css?v=126` / `app.js?v=105` に更新済み。

---

## 14. 補足: なぜカードの上に重ねないか

「カードの右下」をカード内のオーバーレイにすると、本文の最終行を隠すか、隠さないために
常時 `padding-bottom` を確保してカードが縦に間延びする。さらにホバーで padding を変えるとレイアウトシフトが起きる。
本仕様では「カード直下の右寄せ・高さ予約・opacity 切替」とすることで、

- カードの右下（右端）にボタンが揃う
- 本文を隠さない
- ホバーで他のメッセージが動かない

の 3 つを同時に満たす。スマホでは行ごと畳むため、縦スペースの無駄も残らない。
