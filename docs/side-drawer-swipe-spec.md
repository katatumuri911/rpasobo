# 仕様書：サイドドロワー フリック／スワイプ開閉（確定版 v5）

## 1. 目的・ゴール・非ゴール

### 1.1 目的
モバイル（タッチ）環境のサイドドロワー（`#sidebar`）が FAB（`#mobileFab`）やボタンタップでしか開閉できず、片手操作時に指が届かない。**画面左端／チャット領域（`#app`）からのドラッグ追従と高速フリックで開閉できるようにし、縦スクロール・テキスト入力・コード閲覧・既存スワイプUI・モーダル・全画面パネル・ピンチズームを一切妨害しない**。

### 1.2 ゴール
| ID | ゴール | 達成条件 |
|---|---|---|
| G-1 | 追従 | ドラッグ中 `transform` が指の水平移動に 1:1 追従し、オーバーレイ（`#sideBackdrop`）不透明度が開度に連動 |
| G-2 | フリック | デッドゾーン突破後の直近100msの速度で開閉確定（逆フリックで確実にキャンセル可能） |
| G-3 | スナップ | フリックでない場合、閉→開は35%以上、開→閉は65%以下で確定（30%ヒステリシス） |
| G-4 | 排他 | 縦スクロール・テキスト選択・横スクロールUI・入力欄・モーダル・全画面設定・デスクトップでは絶対に動かない（指を離すまで判定不変） |
| G-5 | 一元化 | タップ開閉とスワイプ開閉が同一アダプタ経由。`body.side-open` / `.open` / `aria-expanded` / `aria-hidden` / `inert` / `#sideBackdrop` / `updateFabState()` が常に一致 |
| G-6 | 残留ゼロ | 終了後・destroy 後・別経路の開閉後も、インライン style と状態クラス（`is-dragging` / `is-peeking`）が残らない |
| G-7 | 回帰なし | 既存テスト全件＋新規24件がパス。デスクトップ表示（`inert` 解除含む）・既存タップ操作・ピンチズームが不変 |

### 1.3 非ゴール
デスクトップのマウスドラッグ／Pointer・Pen最適化／右端スワイプ／上下ドロワー／ピンチ操作の追加／ドロワー内デザイン変更／Safari「戻る」ジェスチャ自体の無効化。

---

## 2. Pre-flight findings

実コードとマッピングされた確定値（9項目）：
1. **ドロワー要素・実クラス名**：`#sidebar`（クラスなし、`public/index.html` L12）
2. **オーバーレイ要素・実クラス名**：`#sideBackdrop`（クラスなし、`public/index.html` L347）
3. **開閉ボタン・FAB**：`#sideToggle`（初期 `aria-expanded` なし、`public/index.html` L41）、モバイル開閉は `#mobileFab`（`z-index: 65`、`public/index.html` L330）
4. **開閉状態の真実源**：`document.body.classList.contains('side-open')`（`applyDrawerState` は `body.side-open` と要素の `.open` を両方同期）
5. **`app.js` の初期化方式**：トップレベルでイベント登録しつつ、末尾の即時 async 関数 `(async () => { ... })()` で初期化
6. **テストの DOM 構築・`app.js` 読込方式**：`fs.readFileSync` ＋ `slice()` ＋ `vm.runInContext` ＋ 自作スタブ DOM（`jsdom` 不使用）
7. **既存 z-index と開閉方式**：`#sideBackdrop: 45` / `#sidebar: 50` / `#settingsPanel: 55` / `#mobileFab: 65` / `#toast: 68`。旧開閉は `#sidebar { left: -320px; transition: left .22s ... }` かつ `body.side-open #sidebar { left: 0 }`
8. **`<meta name="viewport">`**：`width=device-width, initial-scale=1.0, viewport-fit=cover, interactive-widget=resizes-content`（ピンチズーム許可）
9. **除外すべき既存UI**：`textarea`, `input`, `select`, `option`, `[contenteditable]`, `[data-no-drawer-swipe]`, `#prompt-toolbar`, `.prompt-toolbar`, `.choices-box`, `.choices-panel`, `.choices-toggle-btn`, `.choices-switch`, `.choices-reroll-btn`, `.choice-item-btn`, `.more-menu`, `.more-sheet`, `.more-backdrop`, `.user-msg-action-pop`, `#toast`, `#mobileFab`, `#mobileTurnCluster`, `#mobileActionToggle`, `#mobileReveal`, `pre`, `code`, `table` ＋ 全画面レイヤー（`#settingsPanel:not(.hidden)`, `#lightbox:not(.hidden)`, `.more-sheet`, `.fav-sheet-backdrop`）

---

## 3. 定数表（`DRAWER_SWIPE_CONFIG`）

| 定数名 | 設定値 | 役割・備考 |
|---|---|---|
| `EDGE_ZONE_PX` | 40 | 左端スワイプ判定領域幅（px）。iOS 戻るジェスチャ競合時は 24-32px に調整 |
| `DEADZONE_PX` | 10 | ジェスチャ判定開始の最小移動距離（px） |
| `DIR_RATIO_EDGE` | 1.5 | 左端開始（または開状態）での水平/垂直方向比率の要求値（`absDx >= absDy * 1.5`） |
| `DIR_RATIO_BODY` | 2.2 | チャット領域開始での水平/垂直方向比率の要求値（`absDx >= absDy * 2.2`） |
| `VELOCITY_WINDOW_MS` | 100 | フリック速度計算に使用する時間窓（ms） |
| `STATIONARY_TIMEOUT_MS` | 90 | 指が静止したとみなすタイムアウト（ms）。90ms 超過で速度は 0 にリセット |
| `FLICK_VELOCITY_PX_MS` | 0.45 | フリック成立とみなす最小速度閾値（px/ms） |
| `FLICK_MIN_DELTA_PX` | 18 | フリック成立とみなす窓内最小移動距離（px） |
| `SNAP_OPEN_RATIO` | 0.35 | 低速ドラッグ時、閉→開へのスナップ確定開度比率（35%） |
| `SNAP_CLOSE_RATIO` | 0.65 | 低速ドラッグ時、開→閉へのスナップ確定開度比率（65%） |
| `CLICK_SUPPRESS_MS` | 350 | ドラッグ終了後のゴーストクリック抑止期間（ms） |
| `DEFAULT_DRAWER_WIDTH` | 280 | ドロワー幅取得不可時のフォールバック幅（px） |
| `SAMPLE_KEEP_MS` | 150 | サンプル履歴の最大保持期間（ms） |
| `SAMPLE_MAX` | 8 | サンプル履歴の最大件数 |
| `MIN_PHYSICAL_DT_MS` | 16 | 物理的に有効な最小サンプル間隔（ms）。16ms 未満は速度計算から除外 |
| `MOBILE_MEDIA_QUERY` | `(max-width: 768px)` | モバイルレイアウト判定メディアクエリ |

---

## 4. 状態機械（状態遷移表）

```
[IDLE]
  │
  ├─ touchstart (有効開始条件) ───────────────────────────> [PENDING]
  ├─ touchstart (除外要素/選択/モーダル/デスクトップ) ──────> [IGNORED]
  └─ touchmove / touchend ───────────────────────────────> [IDLE] (何もしない)

[PENDING]
  │
  ├─ touchmove (hypot >= DEADZONE_PX & dx <= 0 & !startedOpen) ───> [IGNORED]
  ├─ touchmove (hypot >= DEADZONE_PX & absDy >= absDx * ratio) ────> [SCROLLING_Y]
  ├─ touchmove (hypot >= DEADZONE_PX & absDx >= absDy * ratio) ────> [DRAGGING_X] (dragOriginX = curX)
  ├─ touchmove (テキスト選択発生) ─────────────────────────────> [IGNORED]
  └─ touchend / touchcancel ─────────────────────────────> [IDLE]

[DRAGGING_X]
  │
  ├─ touchmove ──────────> [DRAGGING_X] (1:1 追従 transform/opacity 更新)
  ├─ touchend ───────────> settle() ───> [IDLE] (suppressClickUntil 武装)
  ├─ touchcancel ────────> settle(progress >= 0.5) ───> [IDLE]
  ├─ resize / contextmenu ──> abortSwipeGesture() ───> [IDLE]
  └─ window blur / hidden ──> abortSwipeGesture() ───> [IDLE]

[SCROLLING_Y]
  │
  └─ touchend / touchcancel ──> [IDLE]

[IGNORED]
  │
  └─ touchend / touchcancel ──> [IDLE]
```

---

## 5. 不変条件 I-1〜I-7

- **I-1**: `PENDING` / `DRAGGING_X` 以外の状態では `touchmove` が一切の DOM 更新を行わない
- **I-2**: `activeTouchId` の指だけがジェスチャを駆動し、2本目以降の指では状態遷移しない
- **I-3**: ロックフレーム（`PENDING` → `DRAGGING_X`）の移動量は 0（`dragOriginX = curX`）
- **I-4**: フリック成立時は位置スナップより速度方向（`vx > 0`）が常に優先される
- **I-5**: `settle` 完了後・`abort` 後・`flush` 後・`destroy` 後はインライン style と `is-dragging` / `is-peeking` クラスが一切残らない
- **I-6**: 除外対象（入力欄・横スクロール要素・選択中・モーダル・デスクトップ）では指を離すまで `IGNORED` が維持される
- **I-7**: タイムスタンプは `getNow(e)` 単調時刻ソースに統一する

---

## 6. 計算規則

1. **開度計算**：
   - 閉状態開始時：`translateX = Math.max(-w, Math.min(0, -w + dragDx))`
   - 開状態開始時：`translateX = Math.max(-w, Math.min(0, dragDx))`
   - 開度：`progress = Math.max(0, Math.min(1, 1 - Math.abs(translateX) / w))`
2. **フリック速度計算**：
   - 時間窓（直近 100ms）内の最古サンプルを `anchor` とする。
   - `dt = now - anchor.t`、`dxWindow = endX - anchor.x`
   - `dt >= 16ms` の場合、`vx = dxWindow / dt`
   - `|vx| >= 0.45 px/ms` かつ `|dxWindow| >= 18px` の場合、`path = 'flick'`
3. **静止判定**：
   - `now - lastSample.t > 90ms` の場合、指が静止したと判定し `vx = 0`（開度判定へフォールバック）

---

## 7. 判定マトリクス

| 開始状態 | 条件 | 結果 |
|---|---|---|
| 閉 | `vx >= +0.45` かつ `\|dxWindow\| >= 18` | 開 |
| 閉 | フリック不成立 | `progress >= 0.35` → 開 / 未満 → 閉 |
| 開 | `vx <= -0.45` かつ `\|dxWindow\| >= 18` | 閉 |
| 開 | `vx >= +0.45` かつ `\|dxWindow\| >= 18` | 開のまま |
| 開 | フリック不成立 | `progress > 0.65` → 開のまま / 以下 → 閉 |
| 閉 | 開始直後に左方向（`dx <= 0`） | IGNORED（ラッチ） |
| — | `touchcancel` | `progress >= 0.5` → 開 |
| — | 静止（`now - lastMove > 90ms`） | `vx = 0` として開度判定 |

---

## 8. 除外要素リスト

1. **実セレクタ**：
   - フォーム部品：`textarea`, `input`, `select`, `option`, `[contenteditable="true"]`, `[contenteditable=""]`
   - マークされた要素：`[data-no-drawer-swipe]`
   - ツールバー・選択肢：`#prompt-toolbar`, `.prompt-toolbar`, `.choices-container`, `.choice-slot`, `.choices`, `.choices-box`, `.choices-panel`, `.choices-toggle-btn`, `.choices-switch`, `.choices-reroll-btn`, `.choice-item-btn`
   - メニュー・シート：`.more-menu`, `.more-sheet`, `.more-backdrop`, `.msg-actions`, `.user-msg-actions`, `.user-msg-action-pop`
   - トースト・FAB・操作クラスタ：`.toast-container`, `.toast`, `#toast`, `#mobileFab`, `#mobileTurnCluster`, `#mobileActionToggle`, `#mobileReveal`
   - コード・テーブル：`pre`, `code`, `table`
   - 横スクロールコンテナ：`overflow-x: auto|scroll` かつ `scrollWidth > clientWidth + 2`
2. **モーダル・全画面レイヤー**：
   - `.modal.active`, `.modal.open`, `.modal:not(.hidden)`, `[role="dialog"]:not(.hidden)`, `[aria-modal="true"]`, `#settingsPanel:not(.hidden)`, `#lightbox:not(.hidden)`, `.more-sheet`, `.fav-sheet-backdrop`
3. **アクティブテキスト選択**：
   - `window.getSelection()` が非 collapsed かつ非空
4. **デスクトップ環境**：
   - `!isMobileViewport()`

---

## 9. アダプタ契約

1. **`applyDrawerState` の責務**：
   - デスクトップ環境（`!isMobileLayout()`）では常時表示ペインを維持するため、`open: false` であっても `inert` や `aria-hidden="true"` を一切付与しない（チャンネル切替等の `setDrawerOpen(false)` でサイドバーが操作不能になるのを防止）
   - モバイル環境（`isMobileLayout()`）では真実源 `document.body.classList.toggle('side-open', open)` と互換クラス `drawerEl.classList.toggle('open', open)`、`overlayEl.classList.toggle('open', open)` を同期
   - モバイル環境でドロワーの `aria-hidden`（開: 削除 / 閉: 'true'）、`inert`（開: false / 閉: true）を設定
   - モバイル環境でトグルボタンの `aria-expanded`（開: 'true' / 閉: 'false'）を設定
2. **PC・モバイルのアクセシビリティ同期（`syncDrawerDesktopAccessibility`）**：
   - PC幅（`!isMobileLayout()`）：`onLayoutChange()` および初期化時に `drawerEl.removeAttribute('aria-hidden')`、`drawerEl.inert = false`、`drawerEl.removeAttribute('inert')`、`.open` / `body.side-open` 解除を実行
   - モバイル幅（`isMobileLayout()`）：初期化時およびリサイズ時に現在の開閉状態に応じた `applyDrawerState` を実行し、初期状態から閉ドロワーの `inert` / `aria-hidden="true"` / `aria-expanded="false"` を同期
3. **フォーカス制御とUI非表示**：
   - スワイプ以外の開閉時、開く場合は `drawerEl`（tabindex=-1）へフォーカス、閉じる場合は `toggleEl` へフォーカス返却
   - ドロワー状態変更時（`setOpen`）、`closeMoreMenu()` および `hideReveal()` を自動呼び出し
4. **`flush()` の責務**：
   - 保留中の settle / transitionend タイマーを直ちにキャンセルし、インライン style（`transform`, `opacity`）とクラス（`is-peeking`）をリセット

---

## 10. ゴーストクリック抑止

- ドラッグ終了後（`DRAGGING_X` からの離指時のみ）、`suppressClickUntil = now + 350ms` を武装。
- `window` の capture フェーズで `click` イベントを捕捉し、`preventDefault()` と `stopImmediatePropagation()` でキャンセル。
- 単なるタップ操作（`DRAGGING_X` に至らない操作）では抑止窓は武装されない。

---

## 11. アクセシビリティ

- **Reduced Motion**：`@media (prefers-reduced-motion: reduce)` では transition-duration を 0.01s に短縮。`getTransitionMs() <= 50` で settle を即時完了。
- **Inert 属性**：閉状態の `#sidebar` に `inert` を付与し、スクリーンリーダーやキーボードフォーカスが隠蔽ドロワーに入り込まないよう保護。
- **ピンチズーム保護**：`body` や `#app` に `touch-action: none` や `touch-action: pan-y` を付けず、ピンチズームと水平スクロールを奪わない。

---

## 12. テストマトリクスと検証コマンド

全24件（T1〜T24、T6は a/b の2件）の自動テストを `test/test_mobile_layout.cjs` に実装。

検証コマンド：
```powershell
node --check public/app.js; Write-Host "exit=$LASTEXITCODE"
node test/test_mobile_layout.cjs; Write-Host "exit=$LASTEXITCODE"
node test/test_fab_state.cjs; Write-Host "exit=$LASTEXITCODE"
python tools/run_tests.py; Write-Host "exit=$LASTEXITCODE"
```

---

## 13. 既知の制約

1. **iOS 戻るジェスチャ**：Safari の画面左端エッジスワイプによる「戻る」機能と競合する可能性がある。必要に応じて `EDGE_ZONE_PX` を 24-32px に微調整可能。
2. **Pointer イベント非対応**：モバイルタッチブラウザ向けに `TouchEvent`（touchstart / touchmove / touchend / touchcancel）のみを対象とする。
3. **ゴーストクリック 350ms 抑止**：ドラッグ完了後 350ms 以内に同一箇所で発火するクリックをブロックする。
4. **フリック最小間隔**：OS / ブラウザのタイマー精度を考慮し、16ms 未満の間隔は速度計算から除外する。
