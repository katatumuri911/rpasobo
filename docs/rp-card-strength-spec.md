# 背景表示「カードの残り具合」スライダー 仕様書

- 対象: `public/index.html` / `public/style.css` / `public/app.js` / `test/test_rp_card_strength.py`
- 状態: 実装前（本仕様で確定）

---

## 1. 目的とゴール

### 目的

背景表示（RP）モードで、「生成画像をどこまで見せ、本文カードをどこまで読ませるか」を
**1本のスライダー**で調整できるようにする。

現状は段ごとに透過値がバラバラで（本文 `.38` / 自分の発言 `.88` / ツール列 `.85` / 入力欄は不透明）、
ユーザーが「画像をメインに見たい」と思っても、本文カードが濃すぎて調整する手段がない。

### ゴール

1. サイドメニュー下の1行で、1タップで調整に入れる。
2. 調整中は背景画像と本文が**全面で見える**（モバイルのドロワーが完全に透明になる）。
3. 0% まで下げるとカードの塗り・枠・影が消え、**文字だけ**になる。
4. 100% まで上げるとカードがほぼ不透明になり、通常モードに近い読みやすさになる。
5. 操作系（送信・停止・浮遊ボタン・状態色）は**一切変化しない**。
6. 値は再読込後も保持される。
7. **50%（既定）で機能追加前と完全に同じ見た目**（回帰ゼロ）。

### 非ゴール

- 段ごとの個別スライダー（1本に統一する。段別の微調整は将来の詳細設定）。
- 通常モード（背景画像なし）での効果。`body` がベタ色のため意味がなく、UI は無効化する。
- **文字そのものを隠す機能**（画像だけを見るモード）。0% でも本文は残る。
  画像を大きく見たい場合は、既存の「本文タップ＝ライトボックス拡大」を使う。
- 画像ごと・チャンネルごとの個別保存（保存は全体で1値）。

---

## 2. 用語

| 用語 | 意味 |
|---|---|
| カードの残り具合 | スライダーの値（0〜100%）。「透過度」とは呼ばない（向きと衝突するため） |
| 段（tier） | 透過を適用する面のグループ。A〜F の6段＋補助段 |
| 床（F） / 標準（C） / 天井（U） | 各段の 0% / 50% / 100% における背景 alpha |
| peek | スライダーをドラッグしている間だけ、モバイルのドロワーを完全に透明にする状態 |
| crisp / thin | 縁取りを外す（濃い）／縁取りを足す（薄い）閾値のクラス |

---

## 3. UI 仕様

### 配置

`#sidebar` の直下、`#sideBottomSection` の**外**（直後の兄弟）に `#sideOpacityRow` を置く。

> 理由: `#sideBottomSection` は `/* モバイル専用の小物（デスクトップでは出さない） */` の
> `display: none` 対象で、768px 超では表示されない。中に置くとデスクトップでスライダーが消える。

```
#sidebar
 ├ .side-head
 ├ #sessionFilter
 ├ #sessionList
 ├ #sideBottomSection  ← 768px 超では display:none
 └ #sideOpacityRow     ← 常時表示（新規）
```

### 構成

```html
<div id="sideOpacityRow" class="side-opacity-row" data-no-drawer-swipe>
  <div class="rop-head">
    <span class="rop-label">カード</span>
    <span id="rpCardValue" class="rop-val" aria-live="polite">50%</span>
    <button id="rpCardReset" class="rop-reset" type="button"
            title="標準（今の見た目）に戻す">標準</button>
  </div>
  <input id="rpCardStrength" class="rop-range" type="range"
         min="0" max="100" step="5" value="50"
         aria-label="本文カードの濃さ" aria-valuetext="50%"
         data-no-drawer-swipe>
  <div class="rop-ends" aria-hidden="true"><span>文字だけ</span><span>くっきり</span></div>
</div>
```

### 値

| 項目 | 値 |
|---|---|
| 範囲 / 刻み | 0〜100 / `step=5` |
| 既定 | **50**（定数 `RP_CARD_DEFAULT`。後から調整する） |
| 保存 | `localStorage` キー `rpCardStrength`（文字列）。`change`（離した時点）で書き込む |
| 無効化 | 背景表示 OFF のとき `#rpCardStrength` と `#rpCardReset` を `disabled`、行に `.is-off`（`opacity: .45`） |

### 両端ラベル

左「文字だけ」／右「くっきり」を必ず表示する。これが無いと
「100% なのに透けている」と誤解される（100% = 天井であり、完全な不透明ではない段もある）。

---

## 4. 変換式

段ごとに **床 F / 標準 C / 天井 U** の3点を持ち、区間ごとに線形補間する。

```
t = value / 100
TC = 0.5（標準の位置）

t <= TC :  alpha = F + (C - F) * (t / TC)
t >  TC :  alpha = C + (U - C) * ((t - TC) / (1 - TC))
```

`t` は 0〜1 にクランプする。alpha は小数第3位で丸める。

### なぜ2点（床と天井）ではだめか

2点アンカーで「TC の位置が標準 C」になる天井は `U = F + (C - F) / TC` で求まるが、
自分の発言（`F=.12, C=.88`）を `TC=0.5` に通すと `U = .12 + .76/.5 = 1.64` となり **1 を超える**。
`TC` を 0.86 まで上げれば収まるが、そのとき本文は `TC=0.40` を要求し、段ごとに位置がずれて
1本のスライダーでは成立しない。よって中央アンカー付きの区間線形が必要。

### 装飾の係数

枠線・影は塗りと独立に、**標準より薄い側でだけ**一緒に薄くする（濃い側では 1 のまま）。

```
k = min(1, t / TC)
--rp-edge   = rgba(255,255,255, 0.10 * k)   /* 本文カードの枠 */
--rp-shadow = rgba(0,0,0, 0.45 * k)         /* 自分の発言の影 */
```

> 塗りだけ 0 にすると「中身が空の枠」「影だけの塊」が残り、0% が最もみっともなくなる。

---

## 5. 段の定義

| 段 | tier キー | 対象セレクタ | F | C | U | rgb | CSS 変数 |
|---|---|---|---|---|---|---|---|
| A 本文 | `text` | `#chatLog.rp .msg.assistant` | 0.00 | 0.38 | 0.95 | 16,16,24 | `--rp-bg-text` |
| B 自分 | `user` | `#chatLog.rp .msg.user` | 0.12 | 0.88 | 0.98 | 59,91,219 | `--rp-bg-user` |
| C コード | `code` | `.msg.md pre` | 0.20 | 0.35 | 0.60 | 0,0,0 | `--rp-bg-code` |
| C インライン | `inline` | `.msg.md code` | 0.20 | 0.30 | 0.55 | 0,0,0 | `--rp-bg-inline` |
| C' 添付 | `file` | `.msg-files a.file` | 0.35 | 1.00 | 1.00 | 44,44,50 | `--rp-bg-file` |
| D 操作 | `tool` | `⋯` / `🔁` / `🎨` / `.choice-item-btn` / `.msg-hover-actions` / `.img-tools button` | 0.40 | 0.85 | 0.95 | 30,30,38 | `--rp-bg-tool` |
| D' 操作（別基調） | `tool-alt` | `#chatLog.rp .choices-toggle-btn`（通常時） | 0.40 | 0.70 | 0.95 | 30,30,38 | `--rp-bg-tool-alt` |
| E 入力欄 | `input` | `.foot-input` | 0.60 | 1.00 | 1.00 | 43,43,51 | `--rp-bg-input` |
| E フォーカス | `input-focus` | `.foot-input:focus-within` | 0.60 | 1.00 | 1.00 | 48,48,58 | `--rp-bg-input-focus` |
| E チップ | `chip` | `.composer-chip` | 0.60 | 1.00 | 1.00 | 51,51,61 | `--rp-bg-chip` |
| E 添付チップ | `attach` | `.attach-chip` | 0.60 | 1.00 | 1.00 | 44,44,50 | `--rp-bg-attach` |

### 設計意図

- **天井の伸びしろが大きいのは A だけ**（`.38 → .95`）。B/D/E は既に濃いので上に余地がなく、
  これは正しい。「今の見た目が薄い」と感じる原因は本文カードなので、そこにだけ大きな余地を作る。
- **C は独立した床/天井を持つ**（`A + 0.10` のような相対指定にしない）。相対にすると
  外側の値でコードブロックの濃さが変わる。コードは「読む面」なので 0% でも `.20` 残す。
- **C'/E は `C = 1.00`**（＝現状はベタ）。50% で RGBA の alpha 1 になり、現状と完全に一致する。
- **B の床は .12**。0 にすると自分/AI の区別が「右寄せか」だけになるため、うっすら青を残す。

> **tier キーは CSS 変数の一部になる（`--rp-bg-<key>`）。CSS カスタムプロパティは大文字小文字を
> 区別するので、キーは必ずケバブケースで書く（`input-focus`。`inputFocus` は不可）。**

---

## 6. 対象外（F段）と理由

| 対象 | 理由 |
|---|---|
| `#sendBtn`（送信） | 主操作。青が薄くなると押す場所が分からなくなる |
| `.btn-stop-wait`（停止） | 危険色。赤が赤に見えなくなり、状態の signal が死ぬ |
| `.ag-quota` / `.ag-quota.is-low` | 状態色（残量） |
| `.img-status-badge` / `.gen-image .placeholder` / `.btn-img-refine.busy` / `.choices-loading-hint` | 進捗・エラー表示 |
| `.mobile-fab` / `#mobileTurnCluster` / `#mobileScrollLatest` / `#mobileActionToggle` | `background: .62` × `opacity: .62` の二重掛けで実効 `.38`。これ以上下げると押せる場所として成立しない |
| `.summary-card` / `#progress` / `.meta` / `.typing` / `.edited-label` | 面を持たない（文字のみ）。alpha を動かす対象が存在しない。可読性は `--rp-outline` で担保 |
| `.choices-toggle-btn.is-open` / `.is-erotic` / `.choices-switch.is-erotic` | 選択状態・モード色（グラデーション・発光） |
| モーダル / トースト / ライトボックス / ドロワー / `.modal-actions` | フォーカス面。コントラストを落とさない |

---

## 7. 縁取り（text-shadow）の切り替え

`--rp-outline` は「薄いカードの上で白文字を読ませる」ための8方向の黒縁取り。
濃い面では汚いので、閾値で切り替える。

| クラス | 条件 | 効果 |
|---|---|---|
| `body.rp-crisp` | `t >= 0.7` | 本文カードの `text-shadow: none` |
| `body.rp-thin` | `t < 0.3` | 自分の発言に `text-shadow: var(--rp-outline)` を付与 |

- **既定 50% ではどちらも付かない**（＝現状と同じ。`.msg.user` に元々 `text-shadow` は無い）。
- `rp-thin` の閾値を 0.3 にしたのは、0.4 だと α=.73 の時点で黒縁が出てポップが見えるため。
  α(.3)=.576 / α(.25)=.50 と、既にかなり薄い領域で切り替わる。
- `.msg.assistant` は現状も縁取り付きなので、`rp-crisp` でのみ外す（50% は現状維持）。

---

## 8. peek 仕様

スライダーを**指でドラッグしている間だけ**、モバイルのドロワーを完全に透明にして本文を全面で見せる。

### 状態

- `body.rp-peek` の付与／解除のみ。タイマーで自動解除しない（指が止まっても維持）。

### 開始

- `pointerdown` で `rpPeekPointerId` を捕捉し、**そのポインタで `input` が発火したとき**。
- `pointerdown` だけでは開始しない（触っただけでは退かさない）。
- **ポインタ捕捉中のみ許可**。キーボード（矢印キー）で `input` が発火しても開始しない
  → `pointerup` が来ず解除不能になるため。
- `rpMode` が ON かつ `isMobileLayout()` が true のときのみ（**デスクトップでは peek しない**。
  サイドが会話の横にあり、透明にしても空の帯が残るだけ）。

### 終了

`pointerup` / `pointercancel` / `blur` / `visibilitychange`（`hidden`）。
`pointerup` は要素ではなく **`window` の capture** で拾う（画面外リリース対策）。
別ポインタの `pointerup` では解除しない（`event.pointerId` を照合）。

### 最短表示 150ms

タップ（値が変わらない／1回だけ）で透過が一瞬点滅するのを防ぐ。
解除要求時、経過が 150ms 未満なら残り時間だけ遅らせてから外す。

> **タイマーの後始末を `contains("rp-peek")` の判定より前に置くこと。**
> 早期 return すると、150ms 以内に再度ドラッグを始めたときに前回のタイマーが残り、
> ドラッグ中にドロワーが不透明へ戻る。

### CSS

- `#sidebar`: `background: transparent`、`border-right-color: transparent`（**幅は残す**／1px のリフロー防止）、
  `box-shadow: none`
- `#sideBackdrop`: `background: transparent`、`backdrop-filter: none`。
  **`pointer-events` は維持**（下の会話への誤タップ＝本文タップのライトボックス誤爆を防ぐ）
- 中身: `body.rp-peek #sidebar > :not(#sideOpacityRow) { opacity: 0; pointer-events: none; }`
- **`display: none` は禁止**。行が詰まってスライダーが指の下で動く。
- **`opacity` は親に掛けない**（`#sidebar` 全体を 0 にすると子では戻せない）。対象は兄弟要素のみ。
- スライダー行だけ `rgba(16,16,24,.72)` ＋ `blur(8px)` ＋ 枠のピルにする
  （完全透明だと明るい画像の上でつまみが見えない）。
- 配置は**既存の `@media (max-width: 768px)` ブロック内**。新規のメディアクエリを作らない
  （`test_mobile_layout.cjs` の CSS 走査テストを壊さないため）。

---

## 9. 実装制約

1. **命名**: tier キーはケバブケース。`--rp-bg-<key>` が CSS 変数名になる。
2. **値の置換のみ**: 既存の状態ルール（`:hover` / `.is-*` / `[aria-expanded]`）を壊さないため、
   新しい上書きブロックを増やさず、既存宣言の値だけを `var(--rp-bg-*, <現状値>)` に置換する。
3. **タッチ用ホバー復帰も同じ変数にする**: `@media (hover: none), (pointer: coarse)` 内の
   `rgba(30, 30, 38, .85)` / `.70` のハードコードは、対応する段の変数へ置換する
   （そうしないとタップした瞬間だけ濃さが戻る）。
   - `.choice-item-btn:hover:not(.is-selected)` → `tool`
   - `.choices-toggle-btn:hover:not(.is-open):not(.is-erotic)` → `tool-alt`
   - `.btn-img-refine:hover` / `.btn-prompt:hover` / `.btn-edit-resend:hover` → `tool`
   - `.btn-more:hover:not([aria-expanded="true"])` → `tool`
4. **危険色はグループから切り離す**: `#chatLog.rp .choices-container .btn-stop-wait` は
   `.btn-img-refine` などと同じセレクタ群にいるので、変数化する際に別ルールへ分ける。
5. **変数は `body` に完成した `rgba()` 文字列で設定**する。`rpMode` OFF では `removeProperty` して
   フォールバック（現状値）に戻す（通常モードへ漏らさない）。
6. **テスト用マーカーを動かさない**: `/* == RP-CARD-BEGIN == */` 〜 `/* == RP-CARD-END == */`、
   `/* == RP-CARD-CSS-BEGIN == */` 〜 `/* == RP-CARD-CSS-END == */`。
   また `test/test_mobile_layout.cjs` が `slice()` で参照している既存コメント行を編集しない。
7. **`render()` からの呼び出しは `window.__rpCardReady` でガード**する（初期化前の TDZ 回避）。
8. **`setPointerCapture` は使わない**（ネイティブ range の内部ドラッグと干渉する余地を残さない。
   解除は `window` の capture で担保済み）。
9. **キャッシュバスト**: `index.html` の `style.css?v=` / `app.js?v=` を更新する。

---

## 10. レビューで確定した修正点

| # | 指摘 | 判定 | 反映 |
|---|---|---|---|
| ① | `inputFocus` → `--rp-bg-inputFocus` になり CSS の `--rp-bg-input-focus` と不一致 | **妥当（致命的）** | tier キーを `input-focus` に変更。テストで `--rp-bg-input-focus` の値を検証 |
| ② | 末尾に `@media (max-width: 768px)` を足すと CSS 走査テストが壊れる | 前提は再現できず（T22/T23 は全走査）／**リスクは妥当** | 新規メディアクエリを作らず、既存モバイルブロック内に挿入 |
| ③ | `rpPeekBegin` の早期 return で `clearTimeout` がデッドコード。150ms 以内の再タッチで peek が落ちる | **妥当** | タイマー解除と `rpPeekStartedAt` 更新を `contains` 判定より前へ |
| ④ | Windows で `node -e` の引数・出力が cp932 で壊れる | **妥当** | `tempfile` に UTF-8 で書き、`encoding="utf-8"` で実行（既存 `test_composer_ui.py` と同じ） |
| ⑤ | タッチ用 `@media (hover: none)` に `.85` のハードコードが残る | **妥当（4グループ）** | 上記 9-3 のとおり変数化 |
| ⑥ | `.msg.user` の常時縁取りが 50% の見た目を変える | **妥当** | `body.rp-thin`（`t < 0.3`）で付与。50% では付かない |
| ⑦ | `.choices-toggle-btn` の扱い | **妥当（指摘より深い）** | 通常時は `.70` 基調なので `tool-alt` 段を新設。`.is-open` / `.is-erotic` は F |
| ⑧ | 背景表示 OFF で「標準」ボタンが押せる | **妥当** | `#rpCardReset` も `disabled`。CSS は `:hover:not(:disabled)` |
| ⑨ | `rpMode` OFF 時に `rp-peek` が残りうる | **妥当** | OFF 分岐で `rp-peek` と `rpPeekTimer` を強制解除 |
| ⑩ | 行全体に `data-no-drawer-swipe` が必要／`setPointerCapture` は外す | **妥当** | 行に付与。`setPointerCapture` を削除 |

---

## 11. テスト

### 自動（`python tools/run_tests.py` が discover する `test/test_rp_card_strength.py`）

`tools/run_tests.py` は `test/test_*.py` のみを discover する。`.cjs` は自動実行されないため、
振る舞いのテストも Python から `node` を呼ぶ形式で書く。

| ID | 内容 |
|---|---|
| T1 | 50% で各段が C に一致（`text=.38` / `user=.88`） |
| T2 | 0% で各段が F に一致（`text=0` / `user=.12` / `tool=.40` / `input=.60`） |
| T3 | 100% で各段が U に一致（`text=.95` / `user=.98`） |
| T4 | 0→100 が単調増加（5刻み） |
| T5 | 範囲外（-20 / 140）がクランプされる |
| T6 | 未設定・空文字の既定が 50、200 は 100 にクランプ |
| T7 | 0% で `--rp-bg-text` / `--rp-edge` / `--rp-shadow` が alpha 0、`rp-crisp` が付かない |
| T8 | 100% で `--rp-bg-text` が `rgba(16,16,24,0.95)`、`rp-crisp` が付く |
| T9 | **50% で `--rp-bg-input-focus` が設定される（①の回帰）** |
| T10 | `change` で `localStorage` に保存、`#rpCardReset` で 50 に戻る |
| T11 | ポインタ無しの `input` で peek が出ない（キーボード対策） |
| T12 | ポインタ有りで peek が出る。別ポインタの `pointerup` では解除されない |
| T13 | 解除直後（150ms 未満）は `rp-peek` が残る（点滅しない） |
| T14 | 150ms 以内の再タッチでタイマーが解除され、ドラッグ中に落ちない（③の回帰） |
| T15 | デスクトップ（`isMobileLayout()=false`）で peek が出ない |
| T16 | `rpMode` OFF で `disabled` になり、変数が `removeProperty` され、`rp-peek` が消える |
| P1 | CSS: 本文/自分/コード/添付/入力欄/入力欄フォーカスが `var(--rp-bg-*)` を使っている |
| P2 | CSS: 送信・停止・クォータ・FAB が `--rp-bg-` を使っていない |
| P3 | CSS: タッチ用ホバー復帰が `--rp-bg-tool` / `--rp-bg-tool-alt` を使っている（⑤） |
| P4 | HTML: `#sideOpacityRow` が `#sideBottomSection` の外にある |
| P5 | CSS: `.rop-range` に `touch-action: none`、行に `data-no-drawer-swipe` |
| P6 | CSS: peek ブロックに `display: none` が無い／`#sidebar > :not(#sideOpacityRow)` を使う |
| P7 | HTML: `?v=` が更新されている |

### 手動（実機）

| ID | 内容 |
|---|---|
| M1 | ドラッグ中に指を止めても透明が維持される |
| M2 | 離すと戻る。スライダーが指の下から動かない |
| M3 | 0 / 50 / 100% で本文・候補・コードが読める。0% で枠・影が残らない |
| M4 | 明るい画像・暗い画像の両方で、スライダーのつまみが見える |
| M5 | peek 中に会話が誤タップされない（ライトボックスが開かない） |
| M6 | 入力欄フォーカス時もスライダーに追従する（①） |
| M7 | キーボード（矢印キー）で値を変えてもサイドが消えたままにならない |
| M8 | 音声読み上げで「本文カードの濃さ 50%」と読まれる |

---

## 12. 受け入れ要件

- [ ] サイドに「カード 50%」の行が常時あり、デスクトップでもモバイルでも表示される
- [ ] 背景表示 ON で操作でき、OFF では `disabled`（「標準」ボタンも含む）
- [ ] **50%（既定）で機能追加前と同じ見た目**（枠・影・縁取りを含む）
- [ ] 0% でカードの塗り・枠・影が消え、文字だけになる
- [ ] 100% で本文がほぼ不透明になり、通常モードに近い読みやすさになる
- [ ] 100% でも自分の発言（青）と本文の区別が保たれる
- [ ] 70% 以上で黒縁取りが外れ、文字が汚く見えない／30% 未満で自分の発言に縁取りが付く
- [ ] ドラッグ開始でドロワーが完全に透明になり、本文が全面で読める
- [ ] 指を止めても透明のまま、離すと戻る。150ms 以内の再タッチでも落ちない
- [ ] スライダーはドラッグ中も指の下から動かない
- [ ] 送信・停止・クォータ・FAB・エラー・モーダルは一切変化しない
- [ ] キーボードで値を変えてもサイドが消えたままにならない
- [ ] 入力欄をタップしてもスライダーの値に追従する
- [ ] 値が再読込後も保持される
- [ ] `python tools/run_tests.py` が全緑、`node test/test_mobile_layout.cjs` が全緑

---

## 13. 未決・将来

- 既定値 50 は `RP_CARD_DEFAULT` の1定数。実機で見て調整する。
- B段の床 `.12` は「自分の発言の手がかりを残す」ための値。0 にするかは実機判断。
- 段別の個別スライダー（詳細設定）／チャンネル個別保存は将来。
- 「文字を一時的に隠して画像だけ見る」モードは非ゴール（別機能として検討）。
