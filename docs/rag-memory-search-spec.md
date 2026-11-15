# 実装仕様書：RAG記憶設定画面における「キーワード検索による記憶カード絞り込み機能」

## 1. 目的と最終ゴール

### 1.1 背景と課題（Background）
本アプリケーションの長期記憶（RAG）システムは、会話進行に伴い自動抽出された記憶およびユーザーの手動追加記憶が単一セッション内に数十〜数百件規模で蓄積される。
現状の記憶モーダル（`#ragModal`）では、カテゴリタブ（呼称、ルール、アイテム、約束等）による大まかな分類と追加日時の昇順/降順ソートしか備わっておらず、特定の単語（例: 「水族館」「指輪」「門限」「合鍵」）や特定のターン（例: `T12`）に関する記憶を探す際、カードを大量にスクロールして目視探索しなければならず、UX上の大きな負荷となっている。

### 1.2 目的（Objective）
記憶ビュー（`#ragMemView`）のヘッダー領域に、インクリメンタルなキーワード検索バーを新設する。
ユーザーが思いついたキーワードを入力するだけで、カテゴリをまたいで目的の記憶カードを即座に絞り込み、確認・編集・削除できるようにする。

### 1.3 最終ゴール（Goals / Success Metrics）
1. **完全クライアントサイド動作による0ms超高速レスポンス**:
   モーダル読込時にフロントエンド（JavaScriptの `ragAllMemories`）へ展開済みのデータを対象に絞り込みを行うため、サーバーAPIへの再問い合わせ（ネットワーク通信）を一切発生させず、入力と同時に即座に画面へ反映する。
2. **日本語RP（ロールプレイ）に最適化されたマッチング精度**:
   英数字の大文字/小文字・全角/半角の吸収はもちろん、ひらがな／カタカナの相互マッチング（例：「あいす」で「アイス」がヒット）、全角・半角スペースによる複数キーワードAND検索を実現する。
3. **XSS脆弱性ゼロ・位置ズレゼロの安全なキーワードハイライト**:
   検索にヒットした理由が一目でわかるよう本文中の該当箇所を `<mark>` 表示するが、危険な `innerHTML` による文字列置換を行わず、DOM APIと文字コード単位のオフセットマッピングを用いてサロゲートペアや合成文字（濁点・組文字）によるハイライト位置ズレを防止する。
4. **既存機能（カテゴリ絞り込み・ピン留め・ソート）との完全な直交共存**:
   「約束カテゴリ」×「キーワード検索」のような掛け合わせ絞り込みが違和感なく機能し、ピン留め（`pinned`）最上位固定および新旧ソートの順序規則を乱さない。
5. **モバイル（768px以下）および仮想キーボード展開時の完全レスポンシブ**:
   画面縦幅が極めて制限されるスマホ環境（特にキーボード展開時 `body.kb-open`）においても、不要な説明文を自動的に退避させ、カード一覧領域のスクロールを死守する。
6. **行き止まり（Dead-end）のないEmpty State設計**:
   特定カテゴリで検索して0件になった場合でも、全カテゴリでのヒット件数を先読みして提示し、「すべてのカテゴリで表示」リンクからワンタップで救済する。

---

## 2. アーキテクチャ設計方針と技術的判断

### 2.1 完全クライアントサイド動作の採用
* **判定**: 採用
* **理由**: 1チャンネルあたりの記憶件数は通常数十件、極めて長期のセッションでも数百件程度である。この件数規模における文字列探索・DOM更新はモダンブラウザ環境で数ミリ秒未満で完了する。サーバー側へ毎文字APIリクエストを投げる方式と比較して、サーバー負荷・ネットワーク遅延・API並行処理（Race Condition）の懸念を完全に排除できる。

### 2.2 状態管理とライフサイクル設計
検索クエリ（`ragMemSearchQuery`）の「保持」と「破棄」の境界線を明確にする。
* **クエリを保持すべきケース**:
  * 記憶カードの編集フォームを開き、保存またはキャンセルして一覧へ戻ったとき（`openRagMemEditor` ⇔ `closeRagMemEditor`）。
  * カード一覧上で優先度ドロップダウン（`createInlinePrioritySelect`）を変更したとき。
  * カード単体を削除（🗑）したとき。
  * 会話ジャンプ（💬）でメッセージ位置を確認した後、再度モーダルを開いたとき（同一セッション内）。
  * 上部タブで「📚 知識」と「🧠 記憶」を行き来したとき。
* **クエリを破棄（初期化）すべきケース**:
  * **別のチャンネル（異なる `sid`）へ切り替えたとき**（前回チャンネルの検索語が残留して0件表示になる事故を防止）。
  * 「🗑 全消しして作り直す」を実行したとき。

### 2.3 セキュリティ方針
* 記憶本文（`mem.content`）はユーザーやAIが生成した任意の文字列が含まれるため、`<script>` や HTML特殊文字（`<`, `>`, `&`, `"`）を含む可能性がある。
* 本仕様では **`innerHTML` によるハイライトタグ差し込みを全面禁止**とし、`DocumentFragment`、`document.createTextNode()`、`document.createElement("mark")` を用いた安全なノード構築を行う。

---

## 3. 画面レイアウト仕様（UI / UX）

### 3.1 配置場所
記憶ビュー（`#ragMemView`）内において、操作ツールバー（手動追加・再抽出等）およびその補足説明文（`#ragMemRegenHint`）の直下、かつカテゴリタブ（`#ragMemFilterTabs`）の直上に配置する。

```text
[ 🧠 記憶 | 📚 知識 ]                                                     [✕]
-----------------------------------------------------------------------------
[＋手動で追加]  [🔄 会話ログから記憶を追加]  [🗑 全消しして作り直す]
🔄 は会話ログを再分析して記憶を追加します...
-----------------------------------------------------------------------------
┌────────────────────────────────────────────────────────────┐
│ 🔍 [ 記憶を検索... (空白でAND)                   ]   [✕]  │ 3 / 42 件
└────────────────────────────────────────────────────────────┘
[全て] [呼称] [ルール・禁忌] [アイテム] [状態] [約束] ...       [⬇ 新しい順]
-----------------------------------------------------------------------------
┌─ [約束] ─────────────────────────────── [⭐通常] [💬] [✏️] [🗑] ─┐
│ 🤖 T12 2日目・夜                                                  │
│ 日曜日に一緒に駅前の<mark>水族館</mark>へ行く約束をした。           │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 UIコンポーネント詳細仕様
1. **検索ボックスコンテナ（`.rag-mem-search-bar`）**:
   * 幅100%いっぱいに広がり、入力欄ラップ・クリアボタン・件数バッジを横並び（`flex`）で収める。
2. **虫眼鏡アイコン（`.rag-search-icon`）**:
   * 入力欄の左端内側に `position: absolute` で配置。装飾用のため `aria-hidden="true"`、クリック判定を拾わないよう `pointer-events: none` を指定。
3. **入力フィールド（`#ragMemSearchInput`）**:
   * `type="text"` を指定（※`type="search"` はブラウザ標準の ✕ ボタンと独自クリアボタンが二重描画されるため不可）。
   * プレースホルダー: `記憶を検索... (空白でAND)`（スマホ幅でも見切れない短めの表記）。
   * 背景色 `#1a1a1e`、枠線 `1px solid #383842`、角丸 `6px`、文字色 `#e6e6e6`。
   * フォーカス時: 枠線色を `#6889f5` にハイライト。
   * 検索語入力中（`.has-query`）: 枠線色を `#4c6ef5`、背景を `#1d1f2a` に変化させ、フィルタ適用中であることを明示。
4. **クリアボタン（`#ragMemSearchClear`）**:
   * 入力値が1文字以上ある時のみ表示（`.hidden` クラスを着脱）。
   * 入力欄の右端内側に配置（`width: 26px; height: 26px;`、スマホでは押しやすさのため `30px`）。
   * クリック時に入力値を即座に消去し、フォーカスを入力欄に戻した上で全件表示を復元。
   * ※既存のグローバルCSS `button:hover { background: #485477 }` に上書きされないよう、ホバー時背景を透過（`rgba(255,255,255,0.08)`）に固定。
5. **件数バッジ（`#ragMemSearchCount`）**:
   * 入力欄の右外側に配置（`white-space: nowrap`）。
   * 検索中またはカテゴリ選択中: `3 / 42 件`（絞り込み件数 / 全件）。
   * 絞り込みなし（全件表示中）: `42 件`。
   * 読み込み中・エラー時: 空文字（非表示）。

### 3.3 モバイル（768px以下）およびキーボード展開時の最適化
* **iOSズーム防止**: `#ragMemSearchInput` の `font-size` を `16px` に指定（iOS Safariが16px未満のフォーム入力時に画面を自動拡大してUIが破綻するのを防ぐ）。
* **スクロール領域の死守（`display: flex` 化）**:
  * 既存の `#ragMemView` に `display: flex; flex-direction: column; flex: 1; min-height: 0;` を付与し、上部（ボタン・検索・タブ）を固定し、カードリスト（`#ragMemEntries`）のみを独立スクロールさせる。
* **キーボード表示中（`body.kb-open`）の省スペース化**:
  * モバイルでは長文の補足説明文 `#ragMemRegenHint` を非表示化する。
  * 仮想キーボード展開時（`body.kb-open` かつ `#ragMemSearchInput` フォーカス中）は、一時的に上部の `.rag-mem-toolbar`（手動追加・再生成ボタン群）も非表示化し、キーボード上の狭い領域（260〜320px）すべてを検索結果のカード表示に充てる。

---

## 4. 検索機能・マッチングロジック詳細仕様

### 4.1 検索対象フィールド
1枚のカードにつき、以下の要素を結合した文字列を探索対象とする。

| フィールド | 対象プロパティ / 生成規則 | 検索例 | 備考 |
| :--- | :--- | :--- | :--- |
| **記憶本文** | `mem.content` | 「水族館」「指輪」 | 最重要 |
| **ターン番号** | `mem.turn_idx != null ? "T" + mem.turn_idx : ""` | 「T12」「T0」 | 0ターン目も考慮 |
| **作中時間** | カード表示と完全同一の中黒（`・`）区切り文字列（例: `2日目・夜`） | 「2日目」「夜」 | `formatRagMemMeta` と共有 |
| **キャラ名** | `mem.character_name ? "[" + mem.character_name + "]" : ""` | 「アリス」 | 共有記憶等で存在時 |
| **変更履歴** | `mem.history[].content`（オプション判定） | 過去の変更前の文言 | ヒット時は `<details>` を自動展開 |

### 4.2 日本語正規化・マッチング規則
* **正規化順序（厳守）**:
  1. Unicode NFKC 正規化（`str.normalize("NFKC")`）により、全角英数字・半角カナ・全角記号・全角スペースを標準化。
  2. 小文字化（`.toLowerCase()`）により、アルファベットのケースを統一。
  3. 全角カタカナ（`U+30A1..U+30F6`）を文字コード引き算（`- 0x60`）により「ひらがな」へ統一。
* **複数キーワードAND検索**:
  * 正規化後の文字列を半角スペース（`/\s+/`）で分割し、空要素を除去。
  * 分割されたすべての単語が対象文字列に含まれる場合のみ合格（`words.every(...)`）。

### 4.3 入力イベント制御（IME・debounce）
* 日本語変換中（未確定入力中）に画面が激しくガタつくのを防ぐため、`input` イベント時の `e.isComposing` を検出し、変換確定前の再描画は `80ms` の軽量 debounce を適用する。
* `compositionend` イベント時にタイマーをクリアして即座に検索を確定・実行する。
* クリアボタン（✕）の表示/非表示トグルだけは 0ms（同期）で即座に切り替え、操作レスポンスを維持する。
* **Escキーの制御**:
  * 入力欄内で `Escape` キーが押された場合、入力値があれば `e.preventDefault(); e.stopPropagation();` して検索クエリのみをクリア・フォーカス維持する。
  * 入力値が空の状態で `Escape` が押された場合は **何もしない**。
  * ※補足: 現在このアプリには「Esc でモーダルを閉じる」グローバルハンドラは存在しない（`closeTopmostLayer()` は FAB ボタンからのみ呼ばれ、Esc にバインドされた window リスナは ragHitsModal／lightbox 等の別レイヤー専用）。したがって空入力時の Esc でモーダルが閉じることは現行も本実装後もない。将来グローバルな Esc 閉鎖が追加された場合に備え、空入力時は伝播を止めない設計とする。
* **compositionend 直後の二重実行抑止**:
  * ブラウザは `compositionend` の直後に確定文字列で `input` イベント（`isComposing === false`）を再発火する。`compositionend` で即時検索した後に 80ms タイマーが再セットされて二重レンダーになるのを、フラグで1回だけ抑止する。

### 4.4 安全なキーワードハイライト（位置ズレ防止とXSS対策）
* 正規化後の文字列上でマッチ位置を検出しつつ、元テキスト（`mem.content`）の文字境界（サロゲートペア対応）に対応する `[rawStart, rawEnd)` 区間を算出する。
* 複数キーワードが重複・隣接した場合は区間をマージ（結合）する。
* 生成は `DocumentFragment` 上で行い、非マッチ部分は `document.createTextNode()`、マッチ部分は `document.createElement("mark")`（クラス名: `rag-search-highlight`）として追加する。

### 4.5 変更履歴（`history`）ヒット時のUX連動
* 本文には検索語が含まれず、過去の変更履歴（`mem.history[].content`）のみにキーワードが含まれてヒットした場合、カード内のアコーディオン（`<details class="rag-mem-history">`）を **`histDetails.open = true` で自動展開** し、履歴テキスト側にもハイライトを適用する。
* これにより「なぜこのカードがヒットしたのかわからない」というユーザーの混乱を完全に排除する。

### 4.6 既存機能（カテゴリ絞り込み・ソート）との連動
* **カテゴリとの積集合（AND条件）**:
  * フィルタ判定は `カテゴリ一致 AND 検索キーワード一致` とする。
* **ソート順序の維持**:
  * 検索結果の表示配列に対しても、既存の `compareRagMemories`（ピン留め `pinned` 最上位固定 ＋ 昇順/降順）をそのまま適用する。

### 4.7 Empty State（0件時表示）の4分岐制御
絞り込み結果が0件になった場合、状況に応じて以下の4パターンに分岐して表示する。

```text
【パターンA: セッション全体に記憶が0件】
「記憶はまだありません。会話が進むと自動で蓄積されます。」

【パターンB: 特定カテゴリ選択中 ＆ 検索語あり ＆ 他カテゴリにはヒットが存在（救済）】
┌────────────────────────────────────────────────────────────┐
│ 🔍「🤙 約束」カテゴリ内に「水族館」に一致する記憶はありません │
│   （他のカテゴリに 3件 の一致があります）                  │
│                                                            │
│   [すべてのカテゴリで表示 (3件)]   [検索条件をクリア]      │
└────────────────────────────────────────────────────────────┘

【パターンC: 特定カテゴリ選択中 ＆ 検索語あり ＆ 全カテゴリでも0件】
┌────────────────────────────────────────────────────────────┐
│ 🔍 すべてのカテゴリに「ほげほげ」に一致する記憶はありません │
│                                                            │
│   [検索条件をクリア]                                       │
└────────────────────────────────────────────────────────────┘

【パターンD: 「全て」タブ選択中 ＆ 検索語あり で0件】
┌────────────────────────────────────────────────────────────┐
│ 🔍「ほげほげ」に一致する記憶は見つかりませんでした         │
│                                                            │
│   [検索条件をクリア]                                       │
└────────────────────────────────────────────────────────────┘
```

---

## 5. 実装コード詳細

### 5.1 HTML（`public/index.html`）
`#ragMemRegenHint` の直後、`#ragMemFilterTabs` の直前（451〜452行目付近）に以下を追加する。

```html
          <!-- 記憶の検索バー（type="search" はブラウザ標準の×ボタンと干渉するため type="text" を使用） -->
          <div class="rag-mem-search-bar">
            <div class="rag-search-input-wrap">
              <span class="rag-search-icon" aria-hidden="true">🔍</span>
              <input id="ragMemSearchInput" type="text" placeholder="記憶を検索... (空白でAND)" aria-label="記憶をキーワード検索" autocomplete="off" spellcheck="false">
              <button id="ragMemSearchClear" type="button" class="rag-search-clear hidden" title="検索をクリア (Esc)" aria-label="検索をクリア">✕</button>
            </div>
            <span id="ragMemSearchCount" class="rag-search-count" aria-live="polite"></span>
          </div>
```

### 5.2 CSS（`public/style.css`）
1060行目付近（`.rag-filter-tabs` 付近）および 1191行目から始まる `@media (max-width: 768px)` 内（`.rag-entries` のモバイル上書きがある 1299行目付近）に以下を追加・適用する。

※モバイルの `font-size: 16px` は既存の `.rag-modal input { font-size: 16px }`（1336行目付近）でも担保されるが、ID セレクタで明示しておく（将来的なセレクタ変更への防御）。

```css
/* 記憶ビュー本体をFlexカラム化し、上部ツールバー・検索バーを固定して一覧のみスクロール */
#ragMemView:not(.hidden) {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.rag-mem-search-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-shrink: 0;
}
.rag-search-input-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}
.rag-search-icon {
  position: absolute;
  left: 9px;
  font-size: 12px;
  color: #888;
  pointer-events: none;
  line-height: 1;
}
#ragMemSearchInput {
  width: 100%;
  height: 32px;
  padding: 0 30px 0 28px;
  background: #1a1a1e;
  color: #e6e6e6;
  border: 1px solid #383842;
  border-radius: 6px;
  font-size: 13px;
  text-overflow: ellipsis;
  box-sizing: border-box;
}
#ragMemSearchInput:focus {
  border-color: #6889f5;
  outline: none;
}
/* 検索中（クエリあり）の視覚的強調 */
#ragMemSearchInput.has-query {
  border-color: #4c6ef5;
  background: #1d1f2a;
}
button.rag-search-clear {
  position: absolute;
  right: 3px;
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 4px;
  color: #888;
  cursor: pointer;
  padding: 0;
  font-size: 12px;
  line-height: 1;
}
/* グローバル button:hover { background: #485477 } の競合を防止 */
button.rag-search-clear:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.rag-search-count {
  font-size: 12px;
  color: #8a8a94;
  white-space: nowrap;
  flex-shrink: 0;
}
/* キーワードハイライト */
.rag-search-highlight {
  background: rgba(255, 235, 59, 0.25);
  color: #ffe082;
  border-bottom: 1px solid #ffe082;
  border-radius: 2px;
  padding: 0 1px;
}
/* 0件表示スタイル */
.rag-empty-state {
  padding: 24px 16px;
  text-align: center;
  color: #9aa0b0;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
.rag-empty-state-msg {
  line-height: 1.5;
}
.rag-empty-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

/* モバイル対応 (768px以下) */
@media (max-width: 768px) {
  #ragMemSearchInput {
    height: 34px;
    font-size: 16px; /* iOS Safari 自動ズーム防止 */
  }
  button.rag-search-clear {
    width: 30px;
    height: 30px;
  }
  /* スマホでは縦幅節約のため再生成ヒントを畳む */
  #ragMemRegenHint {
    display: none;
  }
  /* キーボード展開中（入力中）はツールバーを畳んでカード表示領域を最大化 */
  body.kb-open #ragModal:has(#ragMemSearchInput:focus) .rag-mem-toolbar {
    display: none;
  }
}
```

### 5.3 JavaScript（`public/app.js`）

#### ① 状態変数と正規化・DOMハイライト・メタ生成ヘルパー
```javascript
let ragMemSearchQuery = "";
let ragMemLastSid = null;
let ragMemSearchTimer = null;

// 日本語文字単位の正規化（NFKC → 小文字化 → 全角カタカナをひらがなへ変換）
function normalizeSearchChar(ch) {
  const nfkc = ch.normalize("NFKC").toLowerCase();
  let out = "";
  for (let i = 0; i < nfkc.length; i++) {
    const code = nfkc.charCodeAt(i);
    // 全角カタカナ (ァ..ヶ, ヴ: U+30A1..U+30F6) → ひらがな (U+3041..U+3096)
    out += (code >= 0x30a1 && code <= 0x30f6) ? String.fromCharCode(code - 0x60) : nfkc[i];
  }
  return out;
}

function normalizeSearchText(str) {
  if (!str) return "";
  const nfkc = String(str).normalize("NFKC").toLowerCase();
  let out = "";
  for (let i = 0; i < nfkc.length; i++) {
    const code = nfkc.charCodeAt(i);
    out += (code >= 0x30a1 && code <= 0x30f6) ? String.fromCharCode(code - 0x60) : nfkc[i];
  }
  return out;
}

function parseSearchWords(query) {
  if (!query) return [];
  return normalizeSearchText(query).trim().split(/\s+/).filter(Boolean);
}

// カード表示用と検索判定用で完全に同一のメタ文字列を生成する
function formatRagMemMeta(mem) {
  const parts = [];
  if (mem.turn_idx != null) parts.push(`T${mem.turn_idx}`);
  if (mem.elapsed_time) {
    if (typeof mem.elapsed_time === "object") {
      const d = mem.elapsed_time.day != null ? `${mem.elapsed_time.day}日目` : "";
      const p = mem.elapsed_time.period || "";
      if (d || p) parts.push(d && p ? `${d}・${p}` : (d || p));
    } else {
      parts.push(String(mem.elapsed_time));
    }
  }
  if (mem.character_name) parts.push(`[${mem.character_name}]`);
  return parts.join(" ");
}

// XSSゼロ＆サロゲートペア・合成文字位置ズレゼロの安全なDOMハイライト生成関数
function appendHighlightedText(container, rawText, normWords) {
  const text = String(rawText || "");
  if (!text || !normWords || normWords.length === 0) {
    container.textContent = text;
    return false;
  }
  const chars = Array.from(text);
  let normFull = "";
  const normToRawStart = [];
  const normToRawEnd = [];
  let rawPos = 0;
  for (let ci = 0; ci < chars.length; ci++) {
    let ch = chars[ci];
    // 濁点・半濁点マーク（半角 U+FF9E/U+FF9F、結合用 U+3099/U+309A）は先行文字と一括で正規化する。
    // 1文字ずつ NFKC すると "ｶﾞ" が "カ"+"゙" に分離してしまい、マッチ判定側（文字列全体の NFKC では "ガ" に合成）
    // と正規化結果が食い違い、ハイライトが付かない不具合が起きるのを防ぐため。
    const nextCh = chars[ci + 1];
    if (nextCh && /[\uFF9E\uFF9F\u3099\u309A]/.test(nextCh)) {
      ch += nextCh;
      ci++;
    }
    const nextRawPos = rawPos + ch.length;
    const normCh = normalizeSearchChar(ch);
    for (let k = 0; k < normCh.length; k++) {
      normToRawStart.push(rawPos);
      normToRawEnd.push(nextRawPos);
    }
    normFull += normCh;
    rawPos = nextRawPos;
  }

  const ranges = [];
  for (const w of normWords) {
    if (!w) continue;
    let from = 0;
    while (from <= normFull.length - w.length) {
      const idx = normFull.indexOf(w, from);
      if (idx === -1) break;
      const rStart = normToRawStart[idx];
      const rEnd = normToRawEnd[idx + w.length - 1];
      if (rStart < rEnd) ranges.push([rStart, rEnd]);
      from = idx + 1;
    }
  }
  if (ranges.length === 0) {
    container.textContent = text;
    return false;
  }
  // 区間ソートとマージ
  ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const merged = [ranges[0].slice()];
  for (let i = 1; i < ranges.length; i++) {
    const prev = merged[merged.length - 1];
    if (ranges[i][0] <= prev[1]) {
      if (ranges[i][1] > prev[1]) prev[1] = ranges[i][1];
    } else {
      merged.push(ranges[i].slice());
    }
  }

  const frag = document.createDocumentFragment();
  let cursor = 0;
  for (const [s, e] of merged) {
    if (cursor < s) frag.appendChild(document.createTextNode(text.slice(cursor, s)));
    const mark = document.createElement("mark");
    mark.className = "rag-search-highlight";
    mark.textContent = text.slice(s, e);
    frag.appendChild(mark);
    cursor = e;
  }
  if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
  container.textContent = "";
  container.appendChild(frag);
  return true;
}

function clearRagMemSearch(focusInput = false) {
  clearTimeout(ragMemSearchTimer);
  ragMemSearchQuery = "";
  const input = $("ragMemSearchInput");
  if (input) {
    input.value = "";
    input.classList.remove("has-query");
    if (focusInput) input.focus();
  }
  $("ragMemSearchClear")?.classList.add("hidden");
}

function setupRagSearchBar() {
  const input = $("ragMemSearchInput");
  const clearBtn = $("ragMemSearchClear");
  if (!input || input._bound) return;
  input._bound = true;

  const applySearch = () => {
    ragMemSearchQuery = input.value.trim();
    input.classList.toggle("has-query", !!ragMemSearchQuery);
    clearBtn?.classList.toggle("hidden", !ragMemSearchQuery);
    renderRagMemories();
  };

  // compositionend 直後にブラウザが再発火する input（isComposing=false）の二重実行を1回だけ抑止
  let composingJustEnded = false;

  input.addEventListener("input", (e) => {
    const hasVal = !!input.value.trim();
    input.classList.toggle("has-query", hasVal);
    clearBtn?.classList.toggle("hidden", !hasVal);
    clearTimeout(ragMemSearchTimer);
    if (e.isComposing) return;
    if (composingJustEnded) { composingJustEnded = false; return; }
    ragMemSearchTimer = setTimeout(applySearch, 80);
  });

  input.addEventListener("compositionend", () => {
    clearTimeout(ragMemSearchTimer);
    composingJustEnded = true;
    applySearch();
  });

  input.addEventListener("keydown", (e) => {
    if ((e.key === "Escape" || e.key === "Esc") && !e.isComposing) {
      if (input.value) {
        e.preventDefault();
        e.stopPropagation();
        clearRagMemSearch(true);
        renderRagMemories();
      }
      // 入力が空のときの Esc は何もしない（伝播は止めない）。
      // 現行アプリには Esc でモーダルを閉じるグローバルハンドラが存在しないため、
      // 伝播してもモーダルは閉じず現行動作と同一。将来の Esc 閉鎖追加に備え伝播は許可する。
    }
  });

  clearBtn?.addEventListener("click", () => {
    clearRagMemSearch(true);
    renderRagMemories();
  });
}
```

#### ② `renderRagMemories()` の改修
既存の [app.js 5475行目付近](file:///c:/Users/nukon/rp-image-chat-main/public/app.js#L5475) を以下のように更新する。

```javascript
function renderRagMemories(memories = null) {
  if (memories !== null) {
    ragAllMemories = memories;
  }
  const list = $("ragMemEntries");
  if (!list) return;
  list.innerHTML = "";

  setupRagSearchBar();

  const normCat = (c) => (c === "taboo" || c === "rule_taboo" ? "rule" : (c === "item_status" ? "item" : (c || "promise")));
  const searchWords = parseSearchWords(ragMemSearchQuery);

  // 1. 各記憶のキーワードマッチ判定関数
  const matchesQuery = (m) => {
    if (searchWords.length === 0) return { matched: true, inHistory: false };
    const metaText = formatRagMemMeta(m);
    const mainNorm = normalizeSearchText([m.content || "", metaText].join(" "));
    const mainMatch = searchWords.every(w => mainNorm.includes(w));
    if (mainMatch) return { matched: true, inHistory: false };

    // 履歴（history）の探索
    // ※作中時間はカード表示（中黒区切り「2日目・夜」）と完全同一形式に統一する。
    //   スペース区切りだと「2日目・夜」という検索語が履歴のみの記憶にヒットしない不整合になる。
    if (m.history && Array.isArray(m.history)) {
      for (const h of m.history) {
        let hEt = "";
        if (h.elapsed_time) {
          if (typeof h.elapsed_time === "object") {
            const hd = h.elapsed_time.day != null ? `${h.elapsed_time.day}日目` : "";
            const hp = h.elapsed_time.period || "";
            hEt = hd && hp ? `${hd}・${hp}` : (hd || hp);
          } else {
            hEt = String(h.elapsed_time);
          }
        }
        const hNorm = normalizeSearchText([h.content || "", hEt].join(" "));
        if (searchWords.every(w => hNorm.includes(w))) {
          return { matched: true, inHistory: true };
        }
      }
    }
    return { matched: false, inHistory: false };
  };

  // 2. フィルタ処理と件数集計
  const matchMap = new Map();
  let allMatchedCount = 0;
  for (const m of ragAllMemories) {
    const res = matchesQuery(m);
    if (res.matched) {
      matchMap.set(m, res);
      allMatchedCount++;
    }
  }

  const filtered = ragAllMemories.filter(m => {
    const c = normCat(m.category);
    if (ragMemFilter !== "all" && c !== ragMemFilter) return false;
    return matchMap.has(m);
  });

  // 件数バッジの更新
  const countEl = $("ragMemSearchCount");
  if (countEl) {
    if (ragMemSearchQuery || ragMemFilter !== "all") {
      countEl.textContent = `${filtered.length} / ${ragAllMemories.length}件`;
    } else {
      countEl.textContent = `${ragAllMemories.length}件`;
    }
  }

  // 3. 0件時の Empty State 表示（4分岐制御）
  if (!filtered || filtered.length === 0) {
    const emptyWrap = document.createElement("div");
    emptyWrap.className = "rag-empty-state";

    if (ragAllMemories.length === 0) {
      emptyWrap.innerHTML = `<p class="muted">記憶はまだありません。会話が進むと自動で蓄積されます（手動追加も可能）。</p>`;
    } else if (ragMemSearchQuery) {
      const msg = document.createElement("div");
      msg.className = "rag-empty-state-msg";
      const qText = `「${ragMemSearchQuery}」`;

      if (ragMemFilter !== "all") {
        const catLabel = CAT_LABELS[ragMemFilter] || ragMemFilter;
        if (allMatchedCount > 0) {
          msg.textContent = `「${catLabel}」カテゴリ内に ${qText} に一致する記憶はありません（他のカテゴリに ${allMatchedCount}件 の一致があります）`;
        } else {
          msg.textContent = `すべてのカテゴリに ${qText} に一致する記憶はありません。`;
        }
      } else {
        msg.textContent = `${qText} に一致する記憶は見つかりませんでした。`;
      }
      emptyWrap.appendChild(msg);

      const actDiv = document.createElement("div");
      actDiv.className = "rag-empty-actions";

      if (ragMemFilter !== "all" && allMatchedCount > 0) {
        const allCatBtn = document.createElement("button");
        allCatBtn.type = "button";
        allCatBtn.className = "main-btn";
        allCatBtn.textContent = `すべてのカテゴリで表示 (${allMatchedCount}件)`;
        allCatBtn.onclick = () => {
          const container = $("ragMemFilterTabs");
          container?.querySelectorAll(".rag-filter-btn").forEach(b => b.classList.toggle("active", b.getAttribute("data-cat") === "all"));
          ragMemFilter = "all";
          renderRagMemories();
        };
        actDiv.appendChild(allCatBtn);
      }

      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "sub";
      clearBtn.textContent = "検索条件をクリア";
      clearBtn.onclick = () => {
        clearRagMemSearch(true);
        renderRagMemories();
      };
      actDiv.appendChild(clearBtn);
      emptyWrap.appendChild(actDiv);
    } else {
      emptyWrap.innerHTML = `<p class="muted">このカテゴリの記憶はありません。</p>`;
    }

    list.appendChild(emptyWrap);
    return;
  }

  // 4. ソート適用
  const displayList = filtered.slice().sort(compareRagMemories);

  // 5. カード描画ループ
  for (const mem of displayList) {
    const cat = normCat(mem.category);
    const pKey = resolveMemoryPriorityKey(mem);
    const card = document.createElement("div");
    card.className = `rag-entry rag-entry-${cat}`;
    if (pKey === "pinned") card.classList.add("rag-entry-pinned");
    if ((mem.status || "active") !== "active") card.classList.add("rag-entry-resolved");

    const header = document.createElement("div");
    header.className = "rag-entry-head";

    const badges = document.createElement("div");
    badges.className = "rag-entry-badges";

    const catBadge = document.createElement("span");
    catBadge.className = `badge-cat badge-cat-${cat}`;
    catBadge.textContent = CAT_LABELS[cat] || cat || "記憶";
    badges.append(catBadge);

    const srcBadge = document.createElement("span");
    srcBadge.className = `badge-source badge-source-${mem.source || "auto"}`;
    const manual = mem.source === "manual";
    srcBadge.textContent = manual ? "✋" : "🤖";
    srcBadge.title = manual ? "手動で追加" : "会話から自動で抽出";
    srcBadge.setAttribute("aria-label", manual ? "手動" : "自動");

    if (mem.status === "fulfilled") {
      const fBadge = document.createElement("span");
      fBadge.className = "badge-status-fulfilled";
      fBadge.textContent = "達成済";
      badges.appendChild(fBadge);
    }

    const meta = document.createElement("span");
    meta.className = "rag-mem-meta";
    meta.textContent = formatRagMemMeta(mem);

    const sub = document.createElement("div");
    sub.className = "rag-entry-sub";
    sub.append(srcBadge, meta);

    const actions = document.createElement("div");
    actions.className = "rag-entry-actions";
    actions.appendChild(createInlinePrioritySelect(sid, mem));

    const jumpBtn = document.createElement("button");
    jumpBtn.type = "button";
    jumpBtn.className = "sub rag-jump-btn";
    jumpBtn.textContent = "💬";
    jumpBtn.setAttribute("aria-label", "会話へ");
    // 現行コードと同じ title 分岐を維持する（mid なしはジャンプ先がないため）
    if (mem.mid) {
      jumpBtn.title = "この記憶が抽出された会話へジャンプ";
      jumpBtn.onclick = () => {
        $("ragModal").classList.add("hidden");
        scrollToMessage(mem.mid);
      };
    } else {
      jumpBtn.title = "会話画面へ戻る";
      jumpBtn.onclick = () => {
        $("ragModal").classList.add("hidden");
      };
    }
    actions.appendChild(jumpBtn);

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "rag-entry-edit";
    editBtn.textContent = "✏️";
    editBtn.title = "編集";
    editBtn.setAttribute("aria-label", "編集"); // 現行コードの aria-label を維持
    editBtn.onclick = () => openRagMemEditor(mem);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "rag-entry-del";
    delBtn.textContent = "🗑";
    delBtn.title = "削除";
    delBtn.setAttribute("aria-label", "削除"); // 現行コードの aria-label を維持
    delBtn.onclick = async () => {
      if (!confirm(`この記憶を削除しますか？\n「${mem.content}」`)) return;
      try {
        await checked(`/api/rag/memories/${encodeURIComponent(mem.id)}?session_id=${encodeURIComponent(sid)}`, null, "DELETE");
        await loadRagMemories();
      } catch (e) {
        alert(e.message);
      }
    };
    actions.append(editBtn, delBtn);
    header.append(badges, actions, sub);

    // 本文の安全ハイライト描画
    const body = document.createElement("div");
    body.className = "rag-mem-content";
    body.style.whiteSpace = "pre-wrap";
    appendHighlightedText(body, mem.content, searchWords);

    card.append(header, body);

    // 変更履歴の描画（履歴ヒット時は自動展開）
    const matchInfo = matchMap.get(mem);
    if (mem.history && mem.history.length > 0) {
      const histDetails = document.createElement("details");
      histDetails.className = "rag-mem-history";
      if (matchInfo && matchInfo.inHistory) {
        histDetails.open = true; // 履歴でヒットしたカードは自動オープン
      }
      const histSummary = document.createElement("summary");
      histSummary.textContent = `変更履歴 (${mem.history.length}件)`;
      histDetails.appendChild(histSummary);

      for (const h of mem.history) {
        const hDiv = document.createElement("div");
        hDiv.className = "rag-mem-history-item";
        let hEt = "";
        if (h.elapsed_time) {
          if (typeof h.elapsed_time === "object") {
            const hd = h.elapsed_time.day != null ? `${h.elapsed_time.day}日目` : "";
            const hp = h.elapsed_time.period || "";
            hEt = ` [${hd && hp ? `${hd}・${hp}` : (hd || hp)}]`;
          } else {
            hEt = ` [${h.elapsed_time}]`;
          }
        }
        const hStatus = h.status === "fulfilled" ? " (達成済)" : "";
        const ts = h.archived_at || h.replaced_at;
        const hTime = ts ? new Date(ts * 1000).toLocaleString() : "";
        const hMeta = document.createElement("span");
        hMeta.className = "muted";
        hMeta.textContent = `${hTime}${hEt}${hStatus}: `;

        const hBody = document.createElement("div");
        appendHighlightedText(hBody, h.content || "", searchWords);

        hDiv.append(hMeta, hBody);
        histDetails.appendChild(hDiv);
      }
      card.appendChild(histDetails);
    }

    list.appendChild(card);
  }
}
```

#### ③ チャンネル切り替え時のライフサイクル連動
[app.js 5397行目の `openRag()` 関数](file:///c:/Users/nukon/rp-image-chat-main/public/app.js#L5397) に以下を追加する。

```javascript
async function openRag() {
  if (!sid) return;
  // チャンネル（sid）が変わっていたら検索語とタブをリセットする
  if (ragMemLastSid !== sid) {
    ragMemLastSid = sid;
    clearRagMemSearch(false);
    ragMemFilter = "all";
    const tabContainer = $("ragMemFilterTabs");
    tabContainer?.querySelectorAll(".rag-filter-btn").forEach(b => {
      b.classList.toggle("active", b.getAttribute("data-cat") === "all");
    });
  }
  $("ragModal").classList.remove("hidden");
  ...
```

※ `ragMemLastSid` 等の状態変数は app.js 5316行目付近の `let ragMemFilter` 宣言群の隣に置く。

#### ④ 「🗑 全消しして作り直す」実行時のクエリ破棄（2.2 の宣言と実装の整合）
[app.js 6304行目の既存ハンドラ](file:///c:/Users/nukon/rp-image-chat-main/public/app.js#L6304) を以下に変更する。

```javascript
if ($("ragMemRegenReset")) $("ragMemRegenReset").onclick = () => {
  clearRagMemSearch(false); // 2.2 ライフサイクル: 全消し実行時は検索語を破棄
  regenerateRagMemories(true);
};
```

#### ⑤ 件数バッジの読み込み中・エラー時非表示（3.2-5 の要件）
`loadRagMemories()`（5454行目付近）の先頭で件数バッジを空にする（既存コードはバッジに触れず、読込中・失敗時に古い件数が残留する）。

```javascript
async function loadRagMemories() {
  if (!sid) return;
  const list = $("ragMemEntries");
  if (!list) return;
  const countEl = $("ragMemSearchCount");
  if (countEl) countEl.textContent = ""; // 読み込み中・エラー時はバッジ非表示（3.2-5）
  list.innerHTML = `<div class="muted" style="padding:12px;">記憶を読み込み中...</div>`;
  try {
    const res = await checked("/api/rag/memories?session_id=" + encodeURIComponent(sid));
    renderRagMemories(res.memories || []); // ここでバッジが再設定される
  } catch (e) {
    list.innerHTML = `<div class="danger" style="padding:12px;">記憶の取得に失敗しました: ${esc(e.message)}</div>`;
    // バッジは空のまま維持
  }
  // ※再構築ステータス確認の既存処理（regenerate/status の確認と startRegenStatusPolling）はそのまま維持
  ...
}
```

---

## 5.4 設計上の既知の制約・判断（実装前の合意事項）

1. **AND 検索のスコープ単位**: 複数単語の AND は「本文＋メタ結合文字列」内で全単語一致、または「履歴の同一エントリ（本文＋作中時間）」内で全単語一致のいずれかを要求する。本文と履歴に単語が分散するケース（例: 単語Aは本文、単語Bは履歴のみ）はヒットしない。実用上問題になる頻度は低く、誤ヒット防止を優先した判断とする。
2. **保存・追加後の絞り込み維持**: 編集・新規追加の保存後も検索条件は維持される（2.2 の方針）。このため、現在の条件に合わない記憶を追加・編集保存すると一覧に直ちには現れない。これは仕様であり、件数バッジ（`N / 全件`）で絞り込み中であることは判別可能とする。
3. **再構築（再抽出）のクエリ維持**: 「🔄 会話ログから記憶を追加（非破壊）」はクエリを維持し、「🗑 全消しして作り直す」は破棄する（5.3-④）。
4. **ハイライト非表示の残存ケース**: 極めて特殊なケース（例: 本文が `う`+結合濁点 `\u3099` でクエリが `ゔ` 単独の場合の逆方向合成）では、カードはヒットするが `<mark>` が付かないことがあり得る。視覚的な不整合のみでクラッシュや XSS にはならず、実用 RP テキストではほぼ発生しないため許容とする。

## 6. テスト要件・テストシナリオ

### 6.1 機能テスト（Functional Testing）
* [ ] **単一キーワード検索**: 「水族館」と入力した際、本文またはメタ情報に含むカードのみが即座に絞り込まれること。
* [ ] **AND検索（複数語）**: 「駅前　水族館」（全角スペース）および「駅前 水族館」（半角スペース）の双方で、両方の単語を含むカードのみがヒットすること。
* [ ] **大文字/小文字・全半角の正規化**: 「apple」で「Apple」「Ａｐｐｌｅ」がヒットし、「T12」で「ｔ１２」「Ｔ１２」がヒットすること。
* [ ] **ひらがな・カタカナ相互マッチ**: 「あいす」で「アイス」「ｱｲｽ」がヒットし、逆に「アイス」で「あいす」がヒットすること。
* [ ] **作中時間・ターンのマッチ**: 「T12」「2日目」「夜」「2日目・夜」で該当カードが正しくヒットすること。
* [ ] **変更履歴（history）マッチ**: 過去の履歴にのみ存在するキーワードを検索した際、カードが表示され、かつ `<details>` が自動で開いて履歴内の単語がハイライトされること。
* [ ] **クリアボタン（✕）動作**: ✕ボタン押下で入力欄が空になり、フォーカスが維持されたまま全件表示へ復帰すること。
* [ ] **Escキー動作**:
  * 入力中に `Escape` を押すと、入力値がクリアされモーダルは閉じないこと。
  * 入力欄が空の状態で `Escape` を押しても何も起きないこと（※現行アプリには Esc でモーダルを閉じるグローバルハンドラが存在しないため。実在しない挙動をテストしないこと）。

### 6.2 既存機能との連携テスト（Integration Testing）
* [ ] **カテゴリ絞り込みとの掛け合わせ**: 「約束」タブ選択中に「水族館」と検索した場合、約束カテゴリ内で水族館を含むカードのみが表示されること。
* [ ] **ソート連動**: 検索結果が複数件ある状態で「⬇ 新しい順」「⬆ 古い順」を切り替えた際、検索結果の絞り込みを維持したまま並び順が正しく反転すること。
* [ ] **ピン留め最上位の維持**: 検索結果の中にピン留め（`pinned`）カードが含まれている場合、新旧ソート設定に関わらずピン留めカードが最上位に配置されること。
* [ ] **優先度インライン変更後の状態維持**: 検索結果カードの優先度セレクトを変更した際、API更新後に検索クエリと絞り込み状態が維持されていること。
* [ ] **カード編集・削除後の状態維持**: 検索結果カードの「✏️ 編集」を開いてキャンセル/保存した際、元の検索結果画面に復帰すること。

### 6.3 セキュリティ・異常系テスト（Security & Edge Cases）
* [ ] **XSS脆弱性検証**: 検索欄に `<script>alert(1)</script>` や `<mark>`、`"` などを入力してもスクリプトが実行されず、カード崩れが起きないこと。
* [ ] **RegExpメタ文字検証**: `[`、`(`、`*`、`+`、`?`、`\` などの正規表現メタ文字を入力しても、JavaScript例外（`SyntaxError`）で停止せず平文としてマッチングすること。
* [ ] **サロゲートペア・合成文字の位置ズレ検証**: 濁点付きカナ（`ガ`、`ｶﾞ`、`カ`+結合濁点 `\u3099`）や絵文字（`🍎`）、組文字（`㌔`）を含む本文を検索した際、**カードがヒットするだけでなくハイライト `<mark>` も実際に付き**、位置が1文字もズレずに正確に対象文字を囲むこと（1文字ずつ正規化すると `ｶﾞ` が合成されず `<mark>` が付かない既知の落とし穴がある。5.3-①の濁点結合ルックアヘッドで対策済み）。

### 6.4 レスポンシブ・モバイル動作テスト（Mobile & Responsive Testing）
* [ ] **iOS Safari 自動ズーム防止**: モバイル幅（375px〜430px）で検索入力欄をタップした際、画面が勝手に拡大ズームしないこと（`font-size: 16px` が適用されていること）。
* [ ] **キーボード展開時（`body.kb-open`）のカード視認性**: 仮想キーボードが表示された際、`.rag-mem-toolbar` およびヒント文が退避し、検索結果のカードがキーボード上部領域に収まってスクロール可能であること。
* [ ] **クリアボタンホバー時のCSS競合防止**: ✕ボタンにマウスホバーまたはタップした際、青い四角い背景（`#485477`）が出現しないこと。

---

## 7. 受け入れ要件（Acceptance Criteria）

| No | 検証項目 | 合格基準 |
| :---: | :--- | :--- |
| **AC-01** | レスポンス速度 | キーワード入力後、体感遅延なし（100ms以内）でカード一覧がリアルタイムに絞り込まれること。 |
| **AC-02** | マッチング仕様 | 全角/半角、大文字/小文字、ひらがな/カタカナ、空白区切りANDがすべて仕様通りに機能すること。 |
| **AC-03** | ハイライト安全性 | `<mark>` ハイライト時に `innerHTML` を使用せず、XSS脆弱性および文字ズレが一切存在しないこと。 |
| **AC-04** | Empty State 救済 | 特定カテゴリで0件かつ他カテゴリに存在する場合、他カテゴリ件数の案内と「すべてのカテゴリで表示」ボタンが表示され、1タップで遷移できること。 |
| **AC-05** | ライフサイクル管理 | 同一チャンネル内での編集・優先度変更・会話ジャンプでは検索語が維持され、チャンネル（`sid`）切り替え時に確実にクリアされること。 |
| **AC-06** | モバイル表示 | 横幅768px以下および仮想キーボード展開時でもレイアウトが破綻せず、カード一覧の独立スクロールが維持されること。 |

