# トースト通知 / 婉曲表現の自動復元通知 実装仕様書

対象ファイル: `public/index.html` / `public/style.css` / `public/app.js` / `test/test_choices_ui.cjs` / `test/test_user_msg_actions.cjs` / `test/test_toast.cjs`（新規）
前提: 既存の `toast()` 呼び出し（約50箇所）を壊さない。設定パネル内の `flash()` は原則として現状維持。Phase 1 ではサーバーAPI・セッションJSONのスキーマを変更しない。

---

## 1. 目的と最終ゴール

### 1.1 現状と課題

- `#toast` は画面下部（`bottom: 90px`）にあり、`z-index: 60`。モバイルの下部操作クラスタ（62〜64）・FAB（65）・`⋯` シート（66〜67）やモーダル（60、DOM順で後）・サブモーダル（70〜80）の下に隠れ、下端の操作ボタンとも重なる。設定パネル（55）よりは上だが、設定の内容を覆う。
- 旧トーストはテキストだけで、成功・注意・失敗の区別がない。デフォルト表示時間は6秒で、成功通知には長すぎる。
- 婉曲表現の自動復元は、本文ではメッセージ直下の `.msg-notice`、候補では `.choices-notice.is-ok` として**常設**される。どちらにも「戻す」が付くが、記録はメモリ上に20件だけ（`decensor_engine.py` の `_DECENSOR_UNDO_MAX = 20`）で、再起動・20件超過後は機能しない。
- 通知の出どころが `toast()` / `flash()` / `alert()` の3系統に分散している。

### 1.2 あるべき姿

1. 画面上部の常設チップ（ステータス・日時）のすぐ下に、種別付きの小さなピルが**1つだけ**出る。
2. 通常は2〜3秒、エラーは6秒（既存の長文エラーは明示指定の10〜15秒を維持）で自動的に消え、タップでも即座に消える。画面のレイアウトを動かさない。
3. 通知は「**今見ている場所の外で結果が起きた操作**」に限定する。その場で結果が見える操作（設定パネル内の保存、トグル、生成中表示）は通知しない。
4. 婉曲表現は常設行をやめ、**届いた時に1回だけ**トーストで知らせる。「戻す」ボタンは画面から削除する。
5. 既存の `toast(msg)` / `toast(msg, ミリ秒)` の呼び出しは、P1で明示的に変更するものを除き、文言・秒数とも変えない。

### 1.3 スコープ

**今回やること（Phase 1）**

- トーストの位置・見た目・アニメーション・種別・表示時間・重複制御の刷新
- 本文と候補の婉曲復元をトースト化し、常設行と「戻す」を削除
- P1 の通知調整（コピーの種別付け、編集/改変の保存、思考レベル）

**今回やらないこと**

- Web Push（`public/sw.js`）の変更
- `alert()` 75箇所の一括トースト化
- サーバーAPI・セッションJSONのスキーマ変更
- Undo ボタン付き snackbar（Material 型）の導入
- 設定パネル内の `flash()` のトースト化
- 既存の長文エラー（Antigravity の改行入りエラー等）の全面的な作り直し

---

## 2. 通知の分類方針

通知を3層に分ける。トーストは一番上の「一過性の結果」だけを担当する。

| 層 | 役割 | 例 | 消えること |
|---|---|---|---|
| トースト | 一過性の結果。消えても困らない | コピー、保存、切替、自動置換の事後報告 | 2〜6秒で消える（`progress` を除く） |
| インライン通知 | 消えては困る注意・状態 | 候補の timeout / suspect、画像エラー、未保存 | 状態が続く限り残す |
| Web Push | アプリを見ていない時 | 応答完了（既存） | OS の通知領域 |

### 2.1 トーストに向くもの

- クリップボードなど、画面に結果が残らない操作
- モーダルを閉じた後の確定（保存・削除・適用）
- 数十秒かかった非同期処理の完了
- 自動処理の事後報告（婉曲置換など）
- 失敗したが、画面は元のままで操作を続けられる

### 2.2 トーストに向かないもの

- トグル・選択・入力欄など、**状態そのものが見えている**操作
- 確認や判断が必要なもの（`confirm`、APIキー未設定の対処）
- 詳細を読む必要がある長文エラー（改行入り、スタックトレース、パス）
- ポーリング・生成中・添付サムネイルなど高頻度のもの
- 消えると困る注意（候補の timeout / suspect、未保存変更）→ インラインか常設バナー

---

## 3. トースト UI 仕様

### 3.1 配置

- `position: fixed`、`left: 50%`、`top: calc(max(10px, env(safe-area-inset-top)) + 46px)`
- 通常端末では `top: 56px`。モバイルのチップは `top: 10px` ＋ `min-height: 35px` なので、その下端45pxから11px下になる。
- ノッチ端末では `env(safe-area-inset-top)` に追従する（例: 47px ＋ 46px ＝ 93px）。
- PC ではヘッダー（高さ約44px）のすぐ下、チャット本文の上に浮く。レイアウトシフトは0。
- 画面幅に応じて `max-width: min(420px, calc(100vw - 32px))`。中央寄せ。

```text
【モバイル】
 ┌──────────────────────────────────────────┐
 │ [接続中…]                    [📅 1日目] │ ← チップ（固定）
 ├──────────────────────────────────────────┤
 │        ╭────────────────────────╮        │
 │        │ ✓ 婉曲表現を3か所 戻しました │        │ ← #toast (top: 56px)
 │        ╰────────────────────────╯        │
 │                                          │
 │  (会話ログ)                               │
```

### 3.2 見た目

- 形状: カプセル型（`border-radius: 9999px`）
- 背景: すりガラス調ダーク（`rgba(28, 30, 42, .94)` ＋ `backdrop-filter: blur(8px)`）
- ボーダー: `1px solid rgba(255, 255, 255, .14)`
- 影: `0 6px 20px rgba(0, 0, 0, .45)`
- 文字: 13px / `font-weight: 500` / `line-height: 1.4`
- 目標文字数: **18文字以内**（360px幅で1行）。2行までは許容。3行以上になる長文は上部ピルに送らない（3.7 の下部フォールバック）。
- アイコンは左に1つ。種別色は既存の `.pill.ok / .wait / .err` と同じパレットを使う。

### 3.3 種別（kind）

| kind | アイコン | 既定時間 | アイコン色 | 用途 |
|---|---|---|---|---|
| `ok` | `✓` | 2000ms | `#8fd6a0` | コピー、保存、置換、変更完了 |
| `info` | なし | 2400ms | — | 対象の切替など、成功と言い切らないもの |
| `warn` | `!` | 3600ms | `#ffd98f` | 応答中の操作禁止、戻せない |
| `err` | `×` | 6000ms | `#ffb8b8` | 保存失敗、通信失敗 |
| `progress` | スピナー | 自動消去なし | `#cfd3ff` | 巻き戻し中、新規会話作成中 |

- `progress` は呼び出し側が完了時に別のトーストで置き換える。`ms: 0` と同じ扱い。
- `err` でも、詳細が長い場合は上部ピルに全文を入れない（3.7 の下部フォールバックへ）。

### 3.4 アニメーション

- 出現: `scale(.92) → 1`、`opacity 0 → 1`、200ms、`cubic-bezier(.16, 1, .3, 1)`
- 退場: `scale(1) → .96`、`opacity 1 → 0`、280ms
- 移動距離は0（translateY は使わない）。画面がガタつかないことを優先。
- タップ時も同じ280msで退場する。200msで `hidden` にしない（アニメーションが切れる）。
- `prefers-reduced-motion: reduce` ではアニメーションを無効化し、即時表示・即時非表示にする。

### 3.5 ライフサイクル

```text
hidden
  └─ toast(msg) ─→ visible（出現アニメーション）
        ├─ ms 経過 ─→ fading（280ms）─→ hidden
        ├─ タップ ─→ fading（280ms）─→ hidden
        └─ 次の toast() ─→ タイマー破棄・visible のまま内容更新
```

- 画面に出るトーストは**常に1つ**。積み重ねない。
- 新しい通知が来たら、旧通知のタイマーを破棄し、同じ要素の内容を差し替える。
- 同一文言・同一種別が表示中（`.hidden` でも `.fade-out` でもない状態）に1.2秒以内に再要求されたら、**再アニメーションせずタイマーだけ延長**する（コピー連打対策）。手動消去（`dismissToast()`）後は `toastLastAt = 0` に戻し、直後の再操作は新規表示とする。
- `hidden` は `display: none`。DOM からは削除しない。

### 3.6 z-index

`z-index` は通常のトーストが **68**、長文フォールバックが **61**。

| 要素 | z-index | トーストとの関係 |
|---|---|---|
| モバイル上部チップ（`#mobileStatusChip` / `#mobileElapsedChip`） | 10 | トーストが上（位置もチップの下） |
| モバイル設定パネル（`#settingsPanel`） | 55 | トーストが上 |
| `.modal`（通常モーダル） | 60 | トーストが上 |
| サブモーダル（`.modal.fav-submodal` / `#ragHitsModal`） | 61（旧70から整理） | 上部トーストが上。`.modal`（60）より上 |
| お気に入り名サブモーダル（`#favNameModal.fav-submodal`） | 62（旧80から整理） | 上部トーストが上。`#favPickerModal`（61）より上 |
| 長文フォールバック（`#toast[data-long="1"]`） | 61 | 下部用。モバイル操作ボタン（62〜65）・`⋯` シート（66〜67）より下、`.modal`（60）より上 |
| モバイル下部操作クラスタ / スクリム / FAB | 62〜65 | 上部トーストが上、長文フォールバックは下（モーダル表示中は JS `updateFabState()` で非表示） |
| モバイル `⋯` シート / PC `⋯` ポップアップ（`.more-backdrop` / `.more-sheet` / `.more-popup`） | 66〜67 | 上部トーストが上、長文フォールバックは下 |
| **上部トースト** | **68** | — |
| ライトボックス（`#lightbox`） | 70 | トーストは下（画像拡大を邪魔しない） |
| 時間経過メニュー / 候補ドロップダウン | 1000 | トーストは下 |

`z-index: 100` にするとライトボックスの上に出てしまうため採用しない。**確定（今回）**: ライトボックス（70）だけは画像閲覧を優先し、トーストを下に残す（ユーザー指定の 68 の範囲）。

> [!IMPORTANT]
> **サブモーダルの z-index 整理（考慮漏れ修正）**
> 既存の `public/style.css` では `.modal.fav-submodal`（`#favPickerModal` / `#favDeleteModal`）と `#ragHitsModal` が `z-index: 70`、`#favNameModal.fav-submodal` が `z-index: 80` になっていた。このままでは、`#favPickerModal` 内で思考レベルチップを切り替えた際の P1 トースト（`ThinkingUI.setThinking()` → `toast("思考: 高", { kind: "ok" })`）や、`#favNameModal` 内の入力バリデーション・保存失敗トーストが、サブモーダルの暗幕（`z-index: 70/80`）の背面に隠れてしまう。
> これらは `.modal`（`60`）の上に重ねるためのサブモーダルであり（開いている間は `isOverlayOpen() === true` によりモバイル下部 UI `62〜65` は非表示）、`70/80` に置く必要がないため、`.modal.fav-submodal` と `#ragHitsModal` を **`61`**、`#favNameModal.fav-submodal` を **`62`** に変更する。これにより `#toast`（`68`）＜ `#lightbox`（`70`）の不変条件を維持したまま、全モーダル・サブモーダル上で上部トーストが確実に前面へ出る。

`z-index: 68` は `.modal`（60）およびサブモーダル（61〜62）より上なので、モーダル表示中もトーストは前面に出る。AIツールモーダルなど、上端のヘッダーと重なるのが気になる場合は、そのモーダル内の操作結果をインライン表示に切り替える（Phase 2 で判断）。設定パネル内の操作結果は `flash()` を使うため、この重なりは起きない（背景で走る婉曲トーストだけは例外）。

### 3.7 長文フォールバック（既存の長文エラー保護）

既存の `toast()` には、Antigravity の失敗理由など改行入りの長文が残っている。これを上部の18文字ピルに出すと3行以上になり、チャットの先頭を覆う。Phase 1 では文言を削らないため、**長文だけ従来どおり下部に出す**。

- 判定: `msg` に改行が含まれる、または48文字を超える場合は `data-long="1"`。
- 見た目: `top: auto; bottom: 90px; border-radius: 12px; padding: 10px 14px; align-items: flex-start; max-width: min(560px, calc(100vw - 32px))`（複数行でも先頭行にアイコンが揃うよう `flex-start` にする）。
- 重なり: `z-index: 61`。旧トーストと同じく、モバイルの下部操作クラスタ（62〜65）や `⋯` シート（66〜67）より下に置き、下部のボタンを塞がない。
- それ以外（通常の通知）は上部のカプセル。
- 長文エラーをモーダルやインラインに移すのは Phase 3。Phase 1 では位置だけ退避する。

**確定（今回）**: この下部フォールバック（61）を Phase 1 の仕様として許容する。長文エラーの移行先は Phase 3 のまま。

```css
#toast[data-long="1"] {
  top: auto;
  bottom: 90px;
  z-index: 61;
  max-width: min(560px, calc(100vw - 32px));
  padding: 10px 14px;
  border-radius: 12px;
  align-items: flex-start;
  text-align: left;
}
#toast[data-long="1"] .toast-icon {
  margin-top: 2px;
}
```

---

## 4. 実装仕様

### 4.1 HTML（`public/index.html`）

現在の `<div id="toast" class="hidden"></div>` を次に置き換える。

```html
<div id="toast" class="hidden" data-kind="info" role="status" aria-live="polite" aria-atomic="true">
  <span id="toastIcon" class="toast-icon" aria-hidden="true"></span>
  <span id="toastMsg" class="toast-msg"></span>
</div>
```

- アイコンは `aria-hidden="true"`。読み上げはメッセージ本文だけにする。
- `role="status"` / `aria-live="polite"` で、操作を中断せずに読み上げる。

### 4.2 CSS（`public/style.css`）

1. 既存の `#toast { ... }`（45行目付近）を次で置き換える。
2. `.modal.fav-submodal`（1210行目付近）の `z-index: 70` を `z-index: 61` に、`#favNameModal.fav-submodal`（1217行目付近）の `z-index: 80` を `z-index: 62` に、`#ragHitsModal`（4055行目付近）の `z-index: 70` を `z-index: 61` に変更する。
3. `.choices-notice.is-ok`・`.choices-undo-btn`・`.choices-undo-btn:disabled`・`.msg-notice`（3883〜3907行目付近）を削除し、`#chatLog.rp :is(.msg-notice, .choices-notice)`（3910行目付近）を `#chatLog.rp .choices-notice` に変更する。

```css
/* --- 上部中央トースト --- */
#toast {
  position: fixed;
  left: 50%;
  /* 上部チップ（35px）のすぐ下 */
  top: calc(max(10px, env(safe-area-inset-top)) + 46px);
  z-index: 68;
  transform: translateX(-50%) scale(1);

  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: min(420px, calc(100vw - 32px));
  padding: 8px 16px 8px 14px;
  border-radius: 9999px;

  background: rgba(28, 30, 42, .94);
  color: #f0f0f5;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  text-align: left;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid rgba(255, 255, 255, .14);
  box-shadow: 0 6px 20px rgba(0, 0, 0, .45);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);

  pointer-events: auto;
  cursor: pointer;
  user-select: none;
  -webkit-user-select: none;
  -webkit-tap-highlight-color: transparent;
  animation: toastPopIn .2s cubic-bezier(.16, 1, .3, 1) forwards;
}
#toast.hidden { display: none; }
#toast.fade-out {
  animation: toastFadeOut .28s cubic-bezier(.16, 1, .3, 1) forwards;
  pointer-events: none;
}

#toast .toast-icon {
  flex: 0 0 auto;
  display: inline-block;
  width: 13px;
  text-align: center;
  font-size: 13px;
  line-height: 1;
  font-weight: 700;
}
#toast .toast-msg { min-width: 0; }
#toast[data-kind="info"] { padding-left: 16px; }
#toast[data-kind="info"] .toast-icon { display: none; }
#toast[data-kind="ok"] .toast-icon { color: #8fd6a0; }
#toast[data-kind="warn"] .toast-icon { color: #ffd98f; }
#toast[data-kind="err"] .toast-icon { color: #ffb8b8; }
#toast[data-kind="warn"] { border-color: rgba(255, 210, 122, .30); }
#toast[data-kind="err"] { border-color: rgba(255, 153, 153, .34); }

/* 既存の長文エラーは上部ピルに収まらないので、従来どおり下部に出す */
#toast[data-long="1"] {
  top: auto;
  bottom: 90px;
  z-index: 61;
  max-width: min(560px, calc(100vw - 32px));
  padding: 10px 14px;
  border-radius: 12px;
  align-items: flex-start;
  text-align: left;
}
#toast[data-long="1"] .toast-icon {
  margin-top: 2px;
}

/* 進捗: 小さなスピナー */
#toast[data-kind="progress"] .toast-icon {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(255, 255, 255, .28);
  border-top-color: #cfd3ff;
  border-radius: 50%;
  font-size: 0;
  animation: toastSpin .8s linear infinite;
}

@keyframes toastPopIn {
  0% { opacity: 0; transform: translateX(-50%) scale(.92); }
  100% { opacity: 1; transform: translateX(-50%) scale(1); }
}
@keyframes toastFadeOut {
  0% { opacity: 1; transform: translateX(-50%) scale(1); }
  100% { opacity: 0; transform: translateX(-50%) scale(.96); }
}
@keyframes toastSpin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  #toast { animation: none; }
  #toast.fade-out { animation: none; opacity: 0; }
  #toast[data-kind="progress"] .toast-icon { animation: none; }
}
```

### 4.3 JavaScript（`public/app.js`）

`toast()`（6853行目付近）と `$("toast").onclick`（6861行目付近）を次で置き換える。`let toastTimer` は削除する。

```js
// 設定パネルを閉じていても見える知らせ（タップで閉じる。ms = 0 なら次の知らせまで出したまま）
const TOAST_MS = { ok: 2000, info: 2400, warn: 3600, err: 6000, progress: 0 };
const TOAST_ICON = { ok: "✓", info: "", warn: "!", err: "×", progress: "" };
let toastHideTimer = 0;
let toastFadeTimer = 0;
let toastLastMsg = "";
let toastLastKind = "";
let toastLastAt = 0;

function toast(msg, opts = {}) {
  const el = $("toast");
  if (!el) return;
  const o = (typeof opts === "number") ? { ms: opts } : (opts || {});
  const hasKind = !!(o.kind && TOAST_MS[o.kind] !== undefined);
  const kind = hasKind ? o.kind : "info";
  // 既存の toast(msg) / toast(msg, 4000) は従来どおり6秒。
  // kind を渡したときだけ種別ごとの既定時間を使う。
  const ms = (o.ms !== undefined) ? o.ms : (hasKind ? TOAST_MS[kind] : 6000);
  const text = String(msg == null ? "" : msg);
  const isLong = text.includes("\n") || text.length > 48;

  const now = Date.now();
  // dedupe:false は「同じ文言でも別イベントとして必ず鳴らす」とき（婉曲の集約など）
  // 退場中（fade-out）は非表示扱いとし、再要求時は新規表示として本文・属性を更新する
  const same = o.dedupe !== false
    && text === toastLastMsg
    && kind === toastLastKind
    && (now - toastLastAt < 1200)
    && !el.classList.contains("hidden")
    && !el.classList.contains("fade-out");

  clearTimeout(toastHideTimer);
  clearTimeout(toastFadeTimer);
  toastHideTimer = 0;
  toastFadeTimer = 0;

  // 先に表示してから本文を入れる（live region を可視にしてから更新する）
  el.classList.remove("hidden", "fade-out");
  if (!same) {
    const msgEl = $("toastMsg");
    const iconEl = $("toastIcon");
    if (msgEl) msgEl.textContent = text;
    if (iconEl) iconEl.textContent = TOAST_ICON[kind] || "";
    el.dataset.kind = kind;
    el.dataset.long = isLong ? "1" : "0";
  }
  toastLastMsg = text;
  toastLastKind = kind;
  toastLastAt = now;

  if (ms > 0) {
    toastHideTimer = setTimeout(() => {
      el.classList.add("fade-out");
      toastFadeTimer = setTimeout(() => {
        el.classList.add("hidden");
        el.classList.remove("fade-out");
        toastFadeTimer = 0;
      }, 280);
    }, ms);
  }
}

function dismissToast() {
  const el = $("toast");
  if (!el || el.classList.contains("hidden")) return;
  clearTimeout(toastHideTimer);
  clearTimeout(toastFadeTimer);
  toastHideTimer = 0;
  toastLastAt = 0;
  el.classList.add("fade-out");
  toastFadeTimer = setTimeout(() => {
    el.classList.add("hidden");
    el.classList.remove("fade-out");
    toastFadeTimer = 0;
  }, 280);
}
$("toast").onclick = dismissToast;
```

### 4.4 後方互換

| 呼び方 | 挙動 |
|---|---|
| `toast("保存しました")` | `info`、6000ms（従来どおり） |
| `toast("保存しました", 4000)` | `info`、4000ms（従来どおり） |
| `toast("保存しました", 0)` | `info`、自動消去なし（従来どおり） |
| `toast("保存しました", { kind: "ok" })` | `ok`、2000ms |
| `toast("失敗しました", { kind: "err", ms: 8000 })` | `err`、8000ms（明示指定が優先） |
| `toast("同じ文言", { kind: "ok", dedupe: false })` | `ok`、2000ms。同一文言でも再表示する |

- 既存の全呼び出しは、引数なしの `toast()` でも6秒のまま。**デフォルトを2400msに変えない**（エラーが早く消える事故を防ぐ）。
- 新しい呼び出しだけ `{ kind }` を使う。段階的に移行する。
- 同じ文言を続けて出したいとき（婉曲の別イベントなど）は `{ dedupe: false }` を付ける。既定は1.2秒以内の同一文言をまとめる。
- 48文字超または改行入りの文言は、`kind` に関係なく 3.7 の下部フォールバックに回る。
- `toast()` は関数宣言のまま残す（`typeof toast === "function"` で呼ぶ箇所があるため）。

### 4.5 アクセシビリティ

- `#toast` に `role="status"` / `aria-live="polite"` / `aria-atomic="true"`。
- アイコンは `aria-hidden="true"`。メッセージ本文だけを読み上げる。
- タップで消せるが、フォーカスは奪わない（`tabindex` を付けない）。
- `prefers-reduced-motion` を尊重する。
- `aria-live` は `display:none` の間は読み上げられないことがあるため、`hidden` を外してから本文を代入する（4.3 の順序）。より確実にしたい場合は表示後の `requestAnimationFrame` で代入する。

---

## 5. 通知対象一覧

### 5.1 P1（Phase 1 で実装する）

| 操作 | 現在 | 変更後 | 実装箇所（目安） |
|---|---|---|---|
| 本文コピー | `toast("コピーしました", 1800)` / 失敗 `4000` | `toast("コピーしました", { kind: "ok" })` / 失敗は `toast("コピーできませんでした", { kind: "err", ms: 4000 })` | `copyMessageText()` 4680行付近 |
| 本文の婉曲復元 | メッセージ下に常設＋「戻す」 | 到着時に1回トースト。常設行と「戻す」を削除 | `textDecensorLine()` / `createMessageWrap()` |
| 候補の婉曲復元 | 候補パネル内に常設＋「戻す」 | 到着時に1回トースト。常設行と「戻す」を削除 | `choicesDecensorLine()` / `choicesBox()` |
| 編集・改変の保存 | モーダルが閉じるだけ | `toast("編集して再送しました", { kind: "ok" })` / `toast("本文を改変しました", { kind: "ok" })` | `saveEdit()` 6900行付近 |
| 思考レベル変更 | `toast("思考レベルを変更しました", 1600)` | `toast("思考: 高", { kind: "ok" })`。変更した側だけ表記 | `setThinking()` 3266行付近 |
| Antigravity 巻き戻し・新規会話（進捗〜完了） | `toast(..., 0)` / 完了・失敗は種別なし | 開始は `{ kind: "progress" }`、完了は `{ kind: "ok", ms: 6000 }`、スキップは `{ kind: "warn", ms: 10000 }`、失敗は `{ kind: "err", ms: 15000 }` | `revertIde()` 6864行付近 / `newSession` 9094行付近 |

補足:

- **本文コピー（`copyMessageText()`）**:
```js
async function copyMessageText(m) {
  const ok = await copyText(m && m.text ? m.text : "");
  toast(ok ? "コピーしました" : "コピーできませんでした", ok ? { kind: "ok" } : { kind: "err", ms: 4000 });
}
```

- **確定（今回）**: `saveEdit()` は `r.revert` が true のとき `revertIde()` が `{ kind: "progress" }` の進捗トーストを出す。この場合は `revertIde()` の完了トーストに任せ、成功トーストは出さない。`r.revert` が false のときだけ「編集して再送しました」/「本文を改変しました」を出す。そうしないと完了メッセージが即座に上書きされる。

```js
await revertIde(r, ch);
if (!r?.revert) {
  toast(m.role === "user" ? "編集して再送しました" : "本文を改変しました", { kind: "ok" });
}
```

- **思考レベル変更（`ThinkingUI.setThinking()`）**:
  - `ThinkingUI.LEVEL_LABELS` は `🔥最大` のように絵文字を含む。トーストでは絵文字を使わないため、`{ auto: "自動", minimal: "最小", low: "低", medium: "中", high: "高", xhigh: "最大", same: "本文と同じ" }` の短いラベルに正規化する。
  - **単体テスト抽出の注意（考慮漏れ修正）**: `test/test_ai_modal_and_thinking_ui.cjs` は `const ThinkingUI = { ... };` のブロックだけを正規表現で抽出して別 VM コンテキストで実行する。そのため、短いラベル辞書はトップレベル定数ではなく **`ThinkingUI` のプロパティ（`TOAST_LABELS`）または `setThinking()` 内のローカル定数**として定義すること。
  - メインと候補の思考レベルを両方変更した場合は `思考: 高 / 候補: 低` のように1行にまとめる。片方だけなら `思考: 高` または `候補の思考: 低`。
  - 思考レベルの保存失敗は `toast(e?.message || "思考レベルを保存できませんでした", { kind: "err", ms: 3500 })` に置き換える。既存の3.5秒は変えない。

```js
// ThinkingUI.setThinking() 内の成功・失敗トースト
const TOAST_LABELS = { auto: "自動", minimal: "最小", low: "低", medium: "中", high: "高", xhigh: "最大", same: "本文と同じ" };
const hasM = body.thinking_level !== undefined;
const hasC = body.choices_thinking_level !== undefined;
const mLbl = TOAST_LABELS[state?.setting?.thinking_level] || state?.setting?.thinking_level || "";
const cLbl = TOAST_LABELS[state?.setting?.choices_thinking_level] || state?.setting?.choices_thinking_level || "";
const msg = (hasM && hasC) ? `思考: ${mLbl} / 候補: ${cLbl}`
  : hasM ? `思考: ${mLbl}`
  : hasC ? `候補の思考: ${cLbl}`
  : "思考レベルを変更しました";
if (typeof toast === "function") toast(msg, { kind: "ok" });
```

- **進捗通知（`revertIde()` / `newSession`）**:
  - `toast(..., 0)` を使っている既存の進捗通知は Phase 1 で `{ kind: "progress" }` に置き換える。
  - `revertIde()` / `newSession` の完了メッセージは長い（50文字前後、`data-long="1"` で下部フォールバック）ため、`{ kind: "ok", ms: 6000 }` のように既存の秒数を明示する（`ok` の既定2秒に縮めない）。`v.skipped` は `{ kind: "warn", ms: 10000 }`、例外失敗は `{ kind: "err", ms: 15000 }` で既存の秒数を維持する。

```js
async function revertIde(r, chSid) {
  if (!r?.revert) return;
  toast("Antigravity の会話も巻き戻しています…", { kind: "progress" });
  try {
    const v = await checked("/api/ide/revert", { sid: chSid });
    toast(
      v.skipped ? "Antigravity の会話は巻き戻していません: " + v.skipped : v.msg + "（次の入力では設定と会話を全部送り直します）",
      v.skipped ? { kind: "warn", ms: 10000 } : { kind: "ok", ms: 6000 }
    );
  } catch (e) {
    toast("アプリの発言は変えましたが、Antigravity の会話は巻き戻せませんでした:\n" + e.message, { kind: "err", ms: 15000 });
  }
}

// newSession 内（9094行目付近）
toast("Antigravity の新規会話を作っています…", { kind: "progress" });
try {
  const n = await checked("/api/ide/new", { sid: r.sid, target: "both" });
  toast(`Antigravity の新規会話を作って担当にしました: ${n.title}（${String(n.ide).slice(0, 8)}）`, { kind: "ok", ms: 6000 });
  if (sid === r.sid) await poll();
  refreshSessions(); loadIdeSessions();
} catch (e) {
  toast("チャンネルは作りましたが、Antigravity の新規会話は作れませんでした:\n" + e.message, { kind: "err", ms: 15000 });
}
```

### 5.2 P2（Phase 2 以降。今回は実装しない）

| 操作 | 現在 | 変更後 |
|---|---|---|
| メッセージ巻き戻し | 無音 | 「ここまで戻しました」 |
| メッセージ削除 | 無音 | 「1件削除しました」 |
| 分岐 | `flash()`（`#settingMsg`。設定パネルが閉じていると見えない） | 「分岐しました: {title}」 |
| 画像履歴の切替 | 無音 | 「この画像に切り替えました」 |
| キャラ状態の保存 | モーダルが閉じるだけ | 「状態を保存しました」 |
| チャンネル複製 | 無音 | 「複製しました」 |
| 会話リセット | 無音 | 「会話をリセットしました」 |
| インポート | 無音 | 「取り込みました」 |
| JSON編集の保存 | 無音 | 「保存しました」 |
| モデル切替 | `toast("Google AI Studio: …")` | `toast("モデル: {label}", { kind: "info" })` |
| 記憶・知識の追加/削除 | 無音 | 一覧が見えるため任意。「記憶を保存しました」等 |
| プリセット削除 | 無音 | パネル内なので `flash()` のままでよい |

補足: 分岐の `flash()` はメッセージの `⋯` から実行され、設定パネルが閉じていると見えない。これは「設定パネル内の通知」ではなく、パネルの外の操作なので、Phase 2 でトーストに置き換える。

### 5.3 対象外（トーストにしない）

| 対象 | 理由 |
|---|---|
| 設定パネル内の保存・検証（`flash()` 約55箇所） | その場に結果が見える。PC ではトーストがパネル上端に重なる |
| トグル・セレクト・入力欄 | 状態自体が変わっている |
| 送信・生成中・推敲中・画像生成の進捗 | 状態チップとボタン文言がすでに示している |
| 候補の timeout / suspect | 候補を使えるかどうかに関わる。消えては困る |
| `confirm()` の確認文 | 判断が必要 |
| 長文エラー（改行入り、スタックトレース） | 上部ピルには収まらない。3.7 の下部フォールバックに回し、Phase 3 でモーダル／インラインへ移す |
| 通信エラー（ポーリング） | 連続発生する。状態チップに任せる |
| 添付の追加・削除 | サムネイルが見えている |
| APIキー未設定（現状は `toast`） | Phase 1 では現状維持（約30文字で上部2行）。常設バナー／設定への導線は Phase 3 |

---

## 6. 文言仕様

### 6.1 基本ルール

1. **結果**を書く（「コピーしました」「保存しました」「戻しました」）。
2. **対象**は、背景で終わった処理のときだけ入れる（「チャンネルを複製しました」）。押した直後の操作は対象が自明なので、結果だけでよい（「コピーしました」）。
3. **件数**は、自動処理のときだけ入れる（「3か所」）。
4. **次の行動**は、制約エラーのときだけ入れる（「停止してから」）。
5. 句点は付けない。絵文字は使わない（アイコンで代替）。
6. 置換前後の語、URL、ファイルパス、スタックトレース、時刻、内部IDは入れない。
7. 「元に戻す」ボタンは付けない。タップは「消す」に固定する。

### 6.2 例

| 良い例 | 理由 |
|---|---|
| `コピーしました` | 押した直後なので対象は自明。7文字 |
| `チャンネルを複製しました` | 背景で終わった処理なので対象を入れる |
| `思考: 高` | 何がどう変わったか最短で分かる |
| `婉曲表現を3か所 戻しました` | 自動処理の規模が分かる。14文字 |
| `応答中は操作できません（停止してから）` | 次の行動が分かる |
| `保存できませんでした` | 失敗だけを1行で。詳細は別UI |

| 悪い例 | 理由 |
|---|---|
| `秘所 → おまんこ（2）` | 長い・センシティブ・画面に残す必要がない |
| `エラー: TypeError: Failed to fetch at ...` | 長すぎる。トーストの領域ではない |
| `コピーしました`（何度も連続） | 連打時はタイマー延長に留める（3.5） |
| 「戻す」付きトースト | タップ＝消すと競合する。Undo が要るなら別コンポーネント |

### 6.3 種別ごとの使い分け

- 成功したが対象が自明（コピー、保存）: `ok`
- 成功したが「変更」である（モデル切替、画像切替）: `info`（チェックを付けない）
- 操作は通ったが制約がある（停止してから、戻せない）: `warn`
- 操作が完了しなかった（保存失敗、通信失敗）: `err`
- 時間がかかる処理の開始: `progress`（`ms: 0`）

---

## 7. 婉曲表現の移行仕様（本命）

### 7.1 削除する UI

**本文（assistant メッセージ）**

- `public/app.js:5005` 付近の `textDecensorLine(m, s)` を関数ごと削除する。
- `public/app.js:5082` 付近の次のブロックを削除する。

```js
if (m.role === "assistant" && m.text_decensor?.applied?.length) {
  wrap.appendChild(textDecensorLine(m, s));
}
```

**候補（choices パネル）**

- `public/app.js:6156` 付近の `choicesDecensorLine(dec, mode)` を関数ごと削除する。
- `public/app.js:6220` 付近の次のブロック（`undoBtn` の生成・クリック処理・`wrap.appendChild(decEl)`）を削除する。

```js
// 婉曲表現を戻した記録（通知とは別枠。ここだけ「戻す」できる）
const decEl = choicesDecensorLine(m.choices_decensor, mode);
if (decEl) {
  ...
  wrap.appendChild(decEl);
}
```

- `choicesBox()` の早期 return 条件から `&& !m.choices_decensor` を**外す**。復元行を消した後は、候補が無く復元記録だけのメッセージで空の候補パネルを作らないため（残すと条件が false になり、空の `.choices-container` ができる）。
- **考慮漏れ修正（`startChoicesRegen()` 内の `m.choices_decensor = null`）**: `public/app.js:6319` の `startChoicesRegen(kind)` 先頭にある `m.choices_decensor = null;` は API 呼び出し**前**に `state.messages` の要素を破壊するため、もし `/api/message/regenerate-choices` が `400` 等で失敗すると、直後の `poll()` でサーバー側の残存 `m.choices_decensor` が「新規到着」と誤判定されてトーストが誤発火する。そのため、API 呼び出し前の `m.choices_decensor = null;` は削除し、`const res = await api("/api/message/regenerate-choices", ...)` が**成功したときだけ**（`if (!res || !res.error) m.choices_decensor = null;`）`await poll()` の直前にクリアする（成功時にクリアしておくことで、同一秒・同一置換内容で再生成が完了した場合でも `poll()` の差分で確実に通知できる）。
- `public/style.css:3883` 付近の `.choices-notice.is-ok` と `.choices-undo-btn` / `.choices-undo-btn:disabled` を削除する。
- あわせて `public/style.css:3900` 付近の `.msg-notice` と、`#chatLog.rp :is(.msg-notice, .choices-notice)` の `.msg-notice` を削除する（後者は `#chatLog.rp .choices-notice` に変更）。`.msg-notice` は `textDecensorLine()` 専用で、他に利用箇所はない。

### 7.2 新しい通知

- 本文・候補を問わず、同じ文言にする。
  - 1か所: `婉曲表現を戻しました`
  - 2か所以上: `婉曲表現を3か所 戻しました`
- 種別は `ok`、表示時間は2200ms。
- 「自動で」は付けない（自動であることは自明）。
- スロット（💬 / 🔞）は文言に含めない。表示中のモードと違う候補でも、自動処理が起きた事実は同じため。
- 置換前後の語は出さない。内訳が必要な場合はセッションJSONの `text_decensor.applied` / `choices_decensor.applied` を見る。

### 7.3 発火タイミング（再描画スパム防止）

`createMessageWrap()` や `choicesBox()` の中で `toast()` を呼んではいけない。これらは `poll()` のたびに再実行されるため、同じ通知が何度も鳴る。

`poll()`（`public/app.js:5800` 付近）および `send()` / `stopTurn()` で `state`（revision）が更新された時だけ、前回の `state` と署名比較して1回だけ鳴らす。

```js
function decensorSig(dec) {
  if (!dec || !Array.isArray(dec.applied) || !dec.applied.length) return "";
  return `${dec.slot || ""}:${dec.ts || ""}:${dec.target_ts || ""}:${JSON.stringify(dec.applied)}`;
}

function checkDecensorDiff(prevState, nextState, isFirst) {
  if (isFirst || !nextState || !Array.isArray(nextState.messages)) return;
  const prevById = new Map((prevState?.messages || []).map((x) => [x.id, x]));
  for (const m of nextState.messages) {
    if (!m || m.role !== "assistant") continue;
    const p = prevById.get(m.id);
    const tdSig = decensorSig(m.text_decensor);
    if (tdSig && tdSig !== decensorSig(p?.text_decensor)) announceDecensor(m.text_decensor);
    const cdSig = decensorSig(m.choices_decensor);
    if (cdSig && cdSig !== decensorSig(p?.choices_decensor)) announceDecensor(m.choices_decensor);
  }
}

async function poll() {
  if (!sid) return refreshSessions();
  const want = sid;
  try {
    const revParam = ...;
    const s = await api(...);
    if (want !== sid) return;
    if (s.error) { sid = ""; resetDecensorToast(); return refreshSessions(); }
    if (s.unchanged) { ... }
    const key = s.id + ":" + s.rev;
    if (key !== lastRev) {
      const first = lastRev === -1;
      const prevState = state;
      lastRev = key;
      state = s;
      ...
      render();
      ...
      checkDecensorDiff(prevState, s, first);
      ...
    }
    markSeen();
  } catch (e) { ... }
}
```

- `prevState`（`prevById`）は `state = s` の**前**に退避する。
- `first === true`（初回読み込み・チャンネル切替・JSON直接編集 `rawBtn`）では鳴らさない。`switchTo()` は `lastRev = -1` / `state = null` にするため、切替後の最初の `poll()` は必ず `first` になる。
- **考慮漏れ修正（秒単位 `ts` による同一秒更新の取りこぼし防止）**: `server.py` の `now()` は `"%Y-%m-%dT%H:%M:%S"`（秒単位）のため、`td.ts !== p?.text_decensor?.ts` だけでは同一秒内に2回更新された場合や `ts` 省略データを取りこぼす。`decensorSig()` で `slot`・`ts`・`target_ts`・`JSON.stringify(applied)` を結合して比較することで、同一秒の別スロット／別内容更新も漏れなく検出する。
- `text_decensor` / `choices_decensor` が `null` に戻った場合は `decensorSig()` が `""` を返すため何もしない。
- **考慮漏れ修正（`send()` / `stopTurn()` の `state = r.session` 直接更新）**: `send()`（5950行付近）および `stopTurn()`（6015行付近）は API 返り値で `lastRev` と `state` を直接更新するため、その返り値に復元済みの応答が含まれると後続の `poll()` では `key === lastRev` となり拾えない。`state = r.session` の直前に `checkDecensorDiff(state, r.session, lastRev === -1);` を1行呼ぶことで取りこぼしを完全に防ぐ。
- `staged_choices_dec` はユーザーメッセージに一時的に置かれる先読み用の記録で、UI では表示していない。通知対象外。合流時に `choices_decensor` へ引き継がれるので、そこで1回鳴る。

### 7.4 集約

本文と候補がほぼ同時に届くと、トーストが2回鳴る。800ms の集約ウィンドウで1回にまとめる。

```js
// 実際は switchTo() から参照するため、let state = null の近くで宣言する
let decToastCount = 0;
let decToastTimer = 0;

function resetDecensorToast() {
  clearTimeout(decToastTimer);
  decToastCount = 0;
  decToastTimer = 0;
  if (typeof dismissToast === "function") dismissToast();
}

function announceDecensor(dec) {
  const n = (dec.applied || []).reduce((sum, a) => sum + (a.count || 0), 0);
  if (!n) return;
  decToastCount += n;
  clearTimeout(decToastTimer);
  decToastTimer = setTimeout(() => {
    const total = decToastCount;
    decToastCount = 0;
    decToastTimer = 0;
    // 同じ件数が続いても、800ms を超えた別イベントなら鳴らす
    toast(
      total === 1 ? "婉曲表現を戻しました" : `婉曲表現を${total}か所 戻しました`,
      { kind: "ok", ms: 2200, dedupe: false }
    );
  }, 800);
}
```

- 800ms を超えて届いた候補は、別のトーストとして鳴る（それでよい）。
- `decToastTimer` / `decToastCount` は `switchTo()` から参照するため、`let state = null;` の近く（ファイル上部）で宣言する。`announceDecensor()` の直前でも実行時には動くが、TDZ を避けて早めに置く。
- チャンネル切替時は、前のチャンネルの集約タイマーを破棄し、表示中のトーストも消す。`switchTo()` の先頭で `resetDecensorToast();` を呼ぶ（`progress` は状態チップで確認できる）。
- `deleteSession()` や `poll()` の `if (s.error)` で `sid = ""` にする経路でも、同じく `resetDecensorToast();` を呼ぶ。最後の1チャンネルを削除して `switchTo()` が呼ばれないケースでも、削除直後に前のチャンネルの婉曲通知が出ないようにする。

### 7.5 エッジケース

| ケース | 挙動 |
|---|---|
| 初回読み込み・過去ログ表示 | `first` ガードで鳴らさない |
| チャンネル切替 | `switchTo()` が `state = null` / `lastRev = -1` にするため鳴らさない |
| 同じメッセージの再描画（`s.unchanged`） | `rev` が変わらないので鳴らさない |
| 同じ秒に本文と候補が更新 | 800ms 集約で1回にまとめる |
| 同じ秒に候補が2回更新（`now()` 秒粒度） | `decensorSig()` が `slot`・`target_ts`・`applied` まで比較するため取りこぼさない |
| 本文と候補が800ms以上離れて届く | 2回鳴る（許容） |
| ユーザーが本文を編集して `text_decensor` が消えた | 鳴らさない |
| 応答がエラーで復元が走らなかった | `applied` が空なので鳴らさない |
| 候補の再生成 API（`/api/message/regenerate-choices`）がエラーで失敗 | 成功時のみ `m.choices_decensor = null` にするため、残存記録で誤発火しない |
| 過去の `text_decensor` が残った古いセッションを開く | `first` ガードで鳴らさない |
| `send()` / `stopTurn()` の返り値に復元済みの応答が含まれる | `state = r.session` の直前に `checkDecensorDiff()` を呼ぶため取りこぼさない |
| 先読みの `staged_choices_dec` がユーザーメッセージにある | UI で使わないため通知しない。合流後の `choices_decensor` で1回鳴る |
| 候補が空で `choices_decensor` だけのメッセージ | 空の候補パネルを出さない（7.1 のとおり早期 return から `!m.choices_decensor` を外す） |
| 古いアシスタントの候補が遅れて届いた | 最新以外の候補は UI に出ない。トーストも `m.id === renderLastAsstId` に限ると誤解が少ない（Phase 1 では任意） |
| 長文の復元通知（48文字超） | 3.7 の下部フォールバックに回る |

### 7.6 「戻す」API の扱い

- 画面から「戻す」を削除する。`/api/message/undo-decensor` は Phase 1 では**残す**。
  - サーバー側のテスト（`test/test_decensor.py` の undo ケース）を壊さないため。
  - 将来「⋯」メニューに条件付きで戻すを置く可能性を残すため。
- `decensor_engine.py` の `_decensor_undo` / `_DECENSOR_UNDO_MAX = 20` も Phase 1 では残す。UI から使われなくなるだけ。
- 完全撤去（API・記録・テストの削除）は Phase 3 で判断する。

### 7.7 テストの更新

**`test/test_choices_ui.cjs`**

- セクション3（`choicesBox` の婉曲復元）を、次の検証に置き換える。
  - `choices_decensor` があっても `.choices-notice` が出ないこと
  - `.choices-undo-btn` が出ないこと
  - 候補本体（`.choices-panel` など）は従来どおり出ること
  - `choices: []` で `choices_decensor` だけのメッセージでは、空の候補パネルを出さず空フラグメントを返すこと
  - ログは `PASS: 復元行・戻すボタンが出ない` に変更する。
- セクション4（`textDecensorLine` の単体テスト）は、関数ごと削除するため丸ごと削除する。
  - セクション4の非同期 IIFE ごと消えるため、最後の `console.log("ALL choices UI tests PASSED!");` はトップレベルに移す（失敗時は `assert` が throw して終了コード1）。
- 抽出範囲の終端は `// ---------------------------------------------------------------- キャラの状態` のままでよい（`choicesDecensorLine` を消しても範囲内）。

**`test/test_user_msg_actions.cjs`**

- `initUserMsgLongPress` の抽出終端を `function textDecensorLine(` から `function createMessageWrap(` に変更する（537行目付近）。
- コピー文言は `コピーしました` のまま変更しないため、199行目付近の期待値はそのまま。

**`test/test_more_menu.cjs`**

- コピー文言は `コピーしました` のまま変更しないため、修正不要。既存の回帰テストとしてそのまま実行する。

**`test/test_ai_modal_and_thinking_ui.cjs`**

- `ThinkingUI` の正規表現抽出テストおよび `setThinking()` の回帰テストとしてそのまま実行する（`TOAST_LABELS` を `ThinkingUI` 内部または `setThinking()` 内に置くことで既存の抽出がそのまま通る）。

**`test/test_toast.cjs`（新規）**

`app.js` から `const TOAST_MS` 〜 `$("toast").onclick = dismissToast;`（および `decensorSig` / `announceDecensor` / `checkDecensorDiff`）を抽出し、スタブ DOM / スタブタイマーで検証する。

- `toast(msg)` → 6000ms のタイマーが1本
- `toast(msg, 4000)` → 4000ms
- `toast(msg, { kind: "ok" })` → 2000ms、`data-kind="ok"`、アイコン `✓`
- `toast(msg, { kind: "progress" })` → タイマーなし
- `toast(msg, 0)` → タイマーなし
- 48文字超または改行入り → `data-long="1"`、48文字以下 → `data-long="0"`
- 同一文言の連続呼び出し → タイマーが延長され、本文の再代入が起きない
- `dedupe:false` を付けた同一文言 → 2回目も本文が再代入され、タイマーがリセットされる
- タップ（`dismissToast`） → `fade-out` が付き、280ms 後に `hidden`。`fade-out` 中または消去直後に同一文言を呼ぶと新規表示として本文が再代入される
- 新しい通知が来たら古いタイマーが破棄される
- 通知の表示（`hidden` 解除）が本文の代入より先であること
- `announceDecensor` / `checkDecensorDiff`: 初回（`isFirst=true`）では鳴らないこと、800ms 以内の本文＋候補が1回に合산されること、同一秒でも `applied` や `slot` が変われば検知されること

---

## 8. 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `public/index.html` | `#toast` の子要素（アイコン・本文）と `role` / `aria-live` を追加。`style.css?v=` / `app.js?v=` を更新 |
| `public/style.css` | `#toast` の刷新、`toast*` キーフレーム追加、サブモーダル（`.modal.fav-submodal` / `#favNameModal.fav-submodal` / `#ragHitsModal`）の `z-index` を `61〜62` に整理、`.choices-notice.is-ok` / `.choices-undo-btn` / `.msg-notice` を削除 |
| `public/app.js` | `toast()` / タップ処理の置き換え（種別・長文フォールバック・重複制御）、`decensorSig()` / `announceDecensor()` / `checkDecensorDiff()` / `resetDecensorToast()` 追加、`poll()` / `send()` / `stopTurn()` の差分検出、`textDecensorLine` / `choicesDecensorLine` と付随ブロックの削除、`startChoicesRegen` の `choices_decensor` クリア位置修正、P1 の種別付け・文言変更 |
| `test/test_choices_ui.cjs` | 婉曲行・戻すボタンのテストを削除／反転 |
| `test/test_user_msg_actions.cjs` | 抽出終端の更新（コピー文言は変更なし） |
| `test/test_toast.cjs` | 新規。トーストおよび婉曲差分・集約の単体テスト |

サーバー（`server.py` / `decensor_engine.py`）は Phase 1 では変更しない。

---

## 9. 不変条件

1. `toast(msg)` / `toast(msg, ミリ秒)` の既存呼び出しは、P1で明示的に変更するものを除き、文言・秒数とも変えない。デフォルトは6000msのまま。
2. `flash()`（`#settingMsg`）の挙動を変えない。設定パネル内の通知はインラインのまま。
3. 初回読み込み・チャンネル切替・`poll()` の `s.unchanged` では、婉曲トーストを鳴らさない。
4. `text_decensor` / `choices_decensor` のデータ構造とサーバーAPIを変えない。
5. 破壊的操作の `confirm()` を外さない。
6. トーストは同時に1つ。DOM を積み重ねない。
7. トーストの表示・非表示でレイアウトを動かさない（`position: fixed` を維持）。
8. 上部トーストの z-index は68、長文フォールバックは61。ライトボックス（70）より前面にしない。サブモーダルは61〜62に収め、上部トースト（68）を隠さない。
9. 長文（改行入り、または48文字超）は上部ピルに送らない。3.7 の下部フォールバックに回す。
10. `style.css` / `app.js` の `?v=` を更新する。

---

## 10. テスト観点

### 10.1 自動テスト

- `test/test_toast.cjs`（新規）: 4.3 および 7.3〜7.4 の仕様をすべて検証
- `test/test_choices_ui.cjs`: 婉曲行・戻すボタンが**出ない**こと。候補本体は出ること
- `test/test_user_msg_actions.cjs`: 長押し初期化の抽出が通ること。コピー文言は `コピーしました` のまま
- `test/test_more_menu.cjs`: コピー文言 `コピーしました` の既存回帰テストがそのまま通ること
- `test/test_ai_modal_and_thinking_ui.cjs`: `ThinkingUI.setThinking()` の変更後も抽出・単体テストが通ること
- `test/test_decensor.py`: サーバー側の undo は変更なしで全件通ること

実行:

```bash
node test/test_toast.cjs
node test/test_choices_ui.cjs
node test/test_user_msg_actions.cjs
node test/test_more_menu.cjs
node test/test_ai_modal_and_thinking_ui.cjs
python -m unittest test.test_decensor
```

### 10.2 手動確認

| 環境 | 確認内容 |
|---|---|
| モバイル 360×640 | トーストがチップの下（y≈56）に出る。チップを隠さない。タップで消える。2.2秒で消える |
| ノッチ端末（safe-area あり） | トーストがノッチ／ステータスバーの下に出る |
| PC 幅 900px | ヘッダーの下に浮く。チャットが跳ねない |
| 設定パネルを開いた状態 | 設定内の保存は `flash()` のまま。背景の婉曲トーストだけは上に出る（2.2秒で消えるため許容） |
| ライトボックス表示中 | トーストが画像の上に出ない（z-index 68 < 70） |
| 長文エラー | 上部ピルではなく下部（`bottom: 90px`）に出る |
| 婉曲復元（本文のみ） | 到着時に1回だけ「婉曲表現を…戻しました」。常設行が消えている |
| 婉曲復元（本文＋候補） | 800ms 以内に届けば1回にまとまる。常設行が消えている |
| ページ再読み込み | 過去ログの婉曲分が一斉に鳴らない |
| チャンネル切替 | 切替直後に鳴らない。前のチャンネルの表示中トーストも消える |
| `prefers-reduced-motion` | 拡大／縮小せず、即時表示・即時非表示 |
| スクリーンリーダー | 「コピーしました」等が polite で読み上げられる。アイコンは読まれない |

---

## 11. 実装順

1. **トースト本体**: HTML / CSS / JS を差し替え、`test/test_toast.cjs` を追加
2. **婉曲の移行**: UI削除 → `announceDecensor()` → `poll()` 差分 → テスト更新
3. **P1 の種別付けと文言**: コピーの `kind`、編集/改変の保存、思考レベル
4. 手動確認（モバイル／PC／ライトボックス／設定パネル）
5. 問題がなければ Phase 2（5.2 の P2）を別コミットで追加

Phase 1 のコミットは「トースト本体」「婉曲移行」「P1の種別付け」を分ける。トースト本体だけで見た目を確認でき、婉曲移行だけで通知の重複・初回ガードを確認できる。

---

## 12. 未決事項

### 12.1 「モバイル設定パネル表示中に背景トーストを隠すか」とは

スマホで設定パネル（`z-index: 55` の全画面シート）を開いている最中に、設定とは関係ない背景の処理（例: 婉曲表現の自動復元）が終わってトーストが出る場合を指す。

- 今の案: トーストは `z-index: 68` なので、設定パネル（55）の**上**に出る。設定を編集中でも、背景で何かが起きたことは分かる。ただし2.2秒だけ、設定パネルの上端（最初の行）を覆うことがある。
- 隠す案: 設定パネルが開いている間だけトーストを `z-index: 54` に下げる。54 はパネル（55）より下なので、パネルがトーストを覆い、設定の編集を邪魔しない。トースト自体は消えず、タイマーも進む。パネルを閉じたときにまだ時間が残っていれば、その残りだけ見える（2.2秒なら、ほぼ消えていることが多い）。
- なぜ `:has` か: CSS は通常「子の状態」から親を装飾できない。`:has()` を使うと「設定パネルが開いている `body`」を条件にできる。
- なぜ `max-width: 768px` か: この問題はスマホの全画面パネルのとき。PC はパネルが横並びで、トーストが上端に出ても許容しやすい。
- 注意: このルールは設定パネルが開いている間の**すべての**トーストを隠す（長文エラーも含む）。背景トーストだけ隠したい場合は、`data-bg="1"` のような印を付けて `#toast[data-bg="1"]` に限定する（Phase 2 で必要なら）。
- ブラウザ対応: `:has()` は現行の Chrome / Edge / Safari / Firefox で使える。未対応のブラウザではこのルールが無視され、今までどおり上に出る（フォールバック）。

```css
@media (max-width: 768px) {
  /* 設定パネル（55）が開いている間は、トーストをその下（54）に隠す */
  body:has(#settingsPanel:not(.hidden)) #toast { z-index: 54; }
}
```

Phase 1 では「上に出す」で進め、実際に邪魔かどうかを触って判断してから Phase 2 で決める。

### 12.2 未決一覧

| 項目 | 現時点の案 | 決めるタイミング |
|---|---|---|
| 長文エラーの移行先 | モーダルか、該当メッセージ内のエラーバッジ | Phase 3 |
| `alert()` の一括トースト化 | しない。短い通信エラーだけ個別に移行 | Phase 3 |
| 進捗トーストの最大表示時間 | 設けない（呼び出し側が完了時に置き換える） | 運用で問題が出たら再検討 |
| モバイル設定パネル表示中に背景トーストを隠すか | 今は上に出す（2.2秒で消えるため許容）。気になるなら 12.1 の `:has` でパネルの下（54）に隠す | Phase 2 |

---

## 13. 決定ログ（今回の確認）

| 決定 | 内容 | 反映箇所 |
|---|---|---|
| `saveEdit()` の `r.revert` | true のときは成功トーストを出さない（`revertIde()` の完了トーストに任せる） | 5.1 |
| ライトボックス | `z-index: 70` の下に置く。画像拡大を優先する | 3.6 |
| 長文フォールバック | 下部 `z-index: 61` を Phase 1 の仕様として許容する（複数行の上揃え `align-items: flex-start` を追加） | 3.7, 4.2 |
| サブモーダルの `z-index` 整理 | `.modal.fav-submodal`（70→61）・`#favNameModal.fav-submodal`（80→62）・`#ragHitsModal`（70→61）に下げ、サブモーダル内操作のトーストが背面に隠れるバグを防ぐ | 3.6, 4.2, 8, 9 |
| 退場中（`.fade-out`）の連打判定 | `same` 判定に `!el.classList.contains("fade-out")` を加え、`dismissToast()` で `toastLastAt = 0` にリセットする | 3.5, 4.3, 7.7 |
| `ThinkingUI.setThinking()` のラベル辞書配置 | `test/test_ai_modal_and_thinking_ui.cjs` の正規表現抽出を壊さないよう、短縮ラベル辞書は `ThinkingUI` 内部（または `setThinking()` 内）に定義する | 5.1, 7.7, 10.1 |
| `startChoicesRegen()` の `choices_decensor` クリア | API 呼び出し前の破壊的クリアをやめ、`/api/message/regenerate-choices` 成功時のみ `m.choices_decensor = null` にする（失敗時の誤発火防止） | 7.1, 7.5, 8 |
| 婉曲差分の署名比較と直接更新経路 | 秒単位 `ts` の同一秒更新でも取りこぼさないよう `decensorSig()` で比較し、`send()` / `stopTurn()` の `state = r.session` 直前にも `checkDecensorDiff()` を呼ぶ | 7.3, 7.5, 8 |
