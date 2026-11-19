# チャンネルウィザード 2/7「どんな話にしますか？」と作成モード 仕様書

- 対象: `public/wizard.js` / `public/wizard.css` / `public/index.html` / `public/app.js` / `server.py`
- テスト: `test/test_channel_wizard_ui.cjs` / `test/test_wizard_server.py`
- 状態: 実装前（本仕様で確定）
- 関連: `docs/wizard-back-nav-spec.md`（戻る・入力退避）、`docs/channel-wizard-spec.md`

---

## 0. 確定事項（ユーザー判断）

| # | 決定 |
|---|---|
| D1 | 2/7 の選択肢は **3択**「AIと相談」「AIにおまかせ」「手動で作成」 |
| D2 | **「AIにおまかせ」でもプレビュー（7/7）は必須**。最終確認は必ず行う |
| D3 | 「作成前にプレビューを確認する」チェック（`draft.confirm` / `wizard_confirm`）は **コードごと全削除** |
| D4 | 「手動で作成」のベースは **1/7「チャンネルの引継ぎ」で選んだ内容に従う**（引継ぎを選んだ＝引き継ぐ／初期設定を選んだ＝初期設定） |
| D5 | チャンネル名は **ウィザード内の小ダイアログで入力**。ネイティブ `prompt()` は廃止 |
| D6 | 手動で作成した後は **設定パネルを自動で開く** |
| D7 | 一言アイデアの入力は、**どの作成モードでも参照される**（現状は「AIにおまかせ」で無視されているバグを修正） |
| D8 | **D2 の帰結として、AIを使わない（テンプレ）経路もプレビューを通す**（§4.2 / §7.4） |

---

## 1. 目的 / 非ゴール

### 目的

1. 2/7 を「作り方の選択」画面として成立させる（AI選択より前にAI前提の設定を聞かない）。
2. 「確認せずに作る」を**ボタン選択**で表現し、設定オプション（チェックボックス）を廃止する。
3. 「手動で作成」を、1/7 のベース選択と矛盾しない作成経路にする（引継ぎ／初期設定）。
4. 「AIにおまかせ」でも入力した一言が生成に使われるようにする。
5. ネイティブダイアログ（`confirm` / `prompt`）をウィザード内UIに統一する。

### 非ゴール

- 質問画面（6/7）の「おまかせ」ボタンの挙動変更
- 1/7 のベース再選択（`base_locked` の解除）
- 開幕画像・タグ辞書・画像設定の仕様変更
- 手動で作成したチャンネルに対する AI 設定生成（テンプレ生成もしない＝完全な空チャンネル）
- 作成済み下書きの履歴閲覧
- タグ画面の「情報が揃いました。追加の質問をしますか？」（ネイティブ `confirm`）の置き換え。
  同じ理由で残っているネイティブダイアログだが、本仕様の対象外（別対応）。

---

## 2. 現状の問題（根拠）

| # | 問題 | 根拠 |
|---|---|---|
| P1 | 「作成前にプレビューを確認する」は **AI生成後の停止／即作成**しか切り替えない。AIなし（テンプレ）経路では無視される | `wizard.js:1570-1600`（`pollJob`）、`wizard.js:1676-1685`（`startCreateChain`）、`wizard.js:1630`（`resumeJob`）。AIなし分岐は `doCreate({}, true)` |
| P2 | 既定ON＝プレビュー必須と同じ。OFFにすると生成結果を一度も見ずに作成される | 同上。既定: `server.py:221` `DEFAULT_APP["wizard_confirm"]=True` |
| P3 | 「AIにおまかせ」は**入力した一言を捨てる** | `wizard.js:1083` `const text = auto ? "" : val("#wizIdea")` ＋ `server.py:4665` `if auto: wslot_set_auto(d, "rough_idea")`。プロンプトでは `- 作りたい話: （おまかせ）` になる |
| P4 | 「空のチャンネル」は名前が不明瞭で、実体は**ネイティブ `prompt()` ＋現在のフォーム設定のコピー** | `wizard.js:1784` → `app.js:10774-10797` `legacyNewSession()` |
| P5 | 「空のチャンネル」のベースが 1/7 の選択と無関係（常に「いま開いているチャンネル」をコピー） | `app.js:10776-10779` |
| P6 | 2/7 のフッターに3ボタンがあり、画面の主目的（作り方の選択）と視覚的な主従が逆 | `wizard.js:1075-1079` |

---

## 3. 2/7 画面仕様

### 3.1 レイアウト

```
[見出し] どんな話にしますか？            2 / 7
────────────────────────────────────────
やりたいことを一言で
[ textarea #wizIdea  (maxlength=300, 任意) ]
[ 例チップ（既存 examples） ]

作り方
[ AIと相談    質問に答えながら、あらすじ・キャラ・第一声を作ります ]
[ AIにおまかせ  一言から残りをAIが埋めて、確認してから作成します   ]
[ 手動で作成    AIを使わず、空のチャンネルを作って設定画面で書きます ]
────────────────────────────────────────
[戻る]                    （フッター左の既存「戻る」のみ）
```

- 3択は **本文内のカード**（全幅・縦積み）。`<button type="button" class="wizard-choice" data-act="...">`。
- クリックハンドラは `[data-opt]` → `[data-tag]` → `[data-ng]` → `[data-act]` の順に見る（`wizard.js:1754-1765`）。
  **カードには `data-opt` / `data-tag` / `data-ng` を付けない**（付けると `data-act` が無視される）。
- 見た目は `.wizard-option` に似せるが、選択状態（`.on`）を持たない別クラス `.wizard-choice` を新設する
  （既存の `.wizard-option` 用トグル・スタイルを誤適用しないため）。
- 説明文は `<span>`。CSS 追加: `.wizard-choice`（`.wizard-option` と同等の見た目）と `.wizard-choice span`。
- チェックボックス `#wizConfirm`（作成前にプレビューを確認する）は**削除**。

### 3.2 フッターの扱い（空バー対策）

- `#wizardBack`（戻る）は `footer.wizard-foot` の**中**にある（`index.html:987-990`）ため、
  通常の 2/7（`navStack = [base, idea]`）では戻るが表示され、フッターは空にならない。
- 下書き再開などで 2/7 に直接入った場合（`ENTRY_STACK.idea = ["idea"]`）は戻るが非表示になり、
  フッターが空バーとして残る。この場合だけバーを隠す。

```js
// setFoot() の最後と updateChrome() の戻る表示切替のあとに呼ぶ
function syncFootBar() {
  const f = foot();
  if (!f) return;
  const bar = f.parentElement;                  // footer.wizard-foot
  if (!bar || !bar.classList) return;
  const back = $("#wizardBack");
  const backHidden = !back || back.classList.contains("hidden");
  const empty = !f.children || f.children.length === 0;
  bar.classList.toggle("hidden", empty && backHidden);
}
```

- `setFoot(html)` は `f.innerHTML = html || ""` のあとに `syncFootBar()` を呼ぶ。
- `updateChrome()` は `#wizardBack` の `hidden` を切り替えたあとに `syncFootBar()` を呼ぶ。
- フッターに項目がある画面・戻るが出ている画面では従来どおり表示される。

### 3.3 文言（確定）

| data-act | ラベル | 説明 |
|---|---|---|
| `idea-consult` | AIと相談 | 質問に答えながら、あらすじ・キャラ・第一声を作ります |
| `idea-auto` | AIにおまかせ | 一言から残りをAIが埋めて、確認してから作成します |
| `idea-manual` | 手動で作成 | AIを使わず、空のチャンネルを作って設定画面で書きます |

- 画面見出し「どんな話にしますか？」／カード見出し「やりたいことを一言で」は据え置き。
- 注記「空でも作れます。わからないことはAIが「おまかせ」で補います。」は据え置き。

### 3.4 操作と無効化

- 3択カードは `runAction()` 経由で実行（`pending` 中は `#wizardBody[inert]` のため二重タップ不可）。
- 通信失敗時はカードを再度押せる状態に戻し、`say()`（トースト）で理由を表示。
- Tab 順: アイデア欄 → 例チップ → カード3つ（上から）。

---

## 4. フロー仕様

| 選択 | 遷移 |
|---|---|
| AIと相談 | 2/7 アイデア → 3/7 使用するAI → 4/7 相手 → 5/7 関係性 → 6/7 質問 → **7/7 プレビュー** → 作成 |
| AIにおまかせ | 2/7 アイデア → 3/7 使用するAI → 生成（質問ステップは飛ばす） → **7/7 プレビュー** → 作成 |
| 手動で作成 | 2/7 アイデア → **チャンネル名ダイアログ → 即作成 → 設定パネル**（3/7 以降を通らない） |

- 進捗表示は `base 1/7, idea 2/7, model 3/7, partner 4/7, tags 5/7, questions 6/7, preview 7/7`（現行どおり）。
- **プレビューは全経路で必須**（D2, D8）。`doCreate()` はプレビュー画面の「この内容で作成」からのみ呼ぶ。
- 「AIにおまかせ」でも 3/7（使用するAIを選ぶ）は表示する（最後の内容確認と併せ、AIを選ばせる）。
- 戻る: 4/7 → 3/7 → 2/7。戻った時点で `ideaMode` は破棄し、選び直しにする。

### 4.1 内部状態

- `autoCreateOnce`（生成後に即作成するフラグ）は**全廃**。
- 代わりに `ideaMode = "consult" | "auto" | ""` を持つ。
  - 2/7 のカードで設定。`open()` / `closeNow()` / 3/7 から戻る / 作成完了で `""` に戻す。
  - 下書きには保存しない（再開時は 2/7 で選び直し）。
  - 3/7 `saveModel()` は `ideaMode === "auto"` なら `startCreateChain(false)`、それ以外は `navigate("partner")`。

### 4.2 AIを使わない経路（D8）

「3/7 で AIを使わずに作る」を選んだ場合と、モデル未設定で `provider === "none"` の場合も、
**テンプレ生成 → 7/7 プレビュー → 作成** にする（現行の「即作成」をやめる）。

- `startCreateChain()` の AIなし短絡（`doCreate({}, true)`）を削除し、`startJob("compose")` に統一する。
  サーバー側は `wizard_do_compose()` が `wizard_ai_enabled(d) === false` のときテンプレで設定を作る
  （`server.py:4358-4376`、既存テスト `test_ai_disabled_uses_template`）。
- プレビュー画面の「作り直す」は AIなし時に**非表示**（同じテンプレが再生成されるだけで意味がない）。

---

## 5. 一言アイデア参照バグの修正

### 5.1 クライアント（`saveIdea`）

```js
// 変更前: 呼び出し側が auto を渡し、auto 時は入力を捨てていた
async function saveIdea(auto) {
  const text = auto ? "" : val("#wizIdea");
  ...
}
// 変更後: 引数なし。空のときだけ「おまかせ」
async function saveIdea() {
  const text = val("#wizIdea");
  const autoFill = !text;      // 空欄のときだけ AI に任せる
  ... apiCall("/api/wizard/answer", {
        draft_id, rev, question_id: "fixed_idea",
        custom_text: text, auto: autoFill, step: "idea",
      })
}
```

- サーバー `/api/wizard/answer` の `auto` の意味は変更しない（`auto:true` → `wslot_set_auto`、`auto:false` → `wslot_set(..., "user")`）。
- `readIdeaForm()` の戻り値を `{ idea }` に変更（`confirm` を削除）。`renderIdea()` は `snap.idea.idea` のみ参照。
- 呼び出し箇所: `idea-consult` / `idea-auto` の両方から `saveIdea()` を呼ぶ（引数で挙動を変えない）。

### 5.2 受け入れ条件

1. 「一言」に入力して「AIにおまかせ」→ `POST /api/wizard/answer` の `custom_text` に入力値、`auto:false`。
2. その下書きの `slots.rough_idea` が `{value: "<入力>", source: "user"}`。
3. compose プロンプトに `- 作りたい話: <入力> [user]` が載る（`（おまかせ）` にならない）。
4. 空欄で押した場合は `auto:true`（従来どおり AI が埋める）。

---

## 6. 手動で作成 仕様

### 6.1 ベース設定（D4）

`/api/wizard/manual` は下書きの 1/7 の選択に従う。

| `draft.inherit_base` | 作成される設定 |
|---|---|
| `true` | `draft.base_setting`（1/7 で選んだ引き継ぎ元チャンネルの設定）をベースにする |
| `false` | 設定を渡さない（`create_session` の初期設定で作成） |

どちらの場合も「新規チャンネル用の初期化」を適用する（`/api/session/new` と同じ）:
- 進捗・ストーリー・メモ・回転・IDE会話・作業フォルダを初期化（`RESET_PROGRESS` / `RESET_STORY` / `NO_IDE` / `NO_FOLDER`）
- 画像設定・参照ファイル・プロジェクト・ユーザー名・本文AI設定などは引き継ぐ
- 形式が古いキー（`DEPRECATED_SETTING_KEYS` / `DEPRECATED_IMAGE_KEYS`）は除去

`inherit_base === true` かつ `base_setting` が空（引き継ぎ元が削除済み等）の場合は、
**初期設定で作成**し、応答 `warnings` に理由を入れる。

タイトルは**常にダイアログの入力値**を使う（引き継ぎ元のタイトルはコピーしない）。

### 6.2 新API `POST /api/wizard/manual`

```jsonc
// request
{ "draft_id": "wiz_xxxxxxxxxx", "rev": 12, "title": "新しいチャンネル" }

// 200 OK
{ "ok": true, "sid": "a1b2c3d4e5", "title": "新しいチャンネル",
  "draft": { /* wizard_public(d, true) */ }, "warnings": [] }

// 409（既作成の再送）
{ "ok": true, "sid": "<既存のsid>", "title": "...", "created": true, ... }
```

サーバー処理（**ロック順序に注意**、実装は `wizard_create` / `_wizard_create_locked` を踏襲）：

1. `wizard_draft_from_request(body)` で draft を読む。
   `status === "created"` かつ `created_sid` のセッションが実在するなら、**同じ sid / title を返す＝冪等**（二重作成防止）。
   セッションが既に削除されている場合は作成し直しへ進む（`created_sid` / `created_title` をクリア）。
2. `rev` 一致検証。不一致は 409 `rev`（`e.draft` を付ける＝`apiCall` の共通処理で最新化）。
3. `wizard_job_running(d.last_job)` は 409 `wizard_busy`。
4. `title = wizard_plain_text(body.title, 40)`。空なら 400 `bad_title`。
5. 二重実行の直列化に **`wizard_create_lock`** を使う（AI作成経路 `wizard_create` と同じ）。
6. `wizard_lock` を**解放した状態**で `create_session(...)` を呼ぶ。
   - `create_session` は内部で `lock` を取る。`wizard_lock` 保持中に `lock` を取ると
     逆順ネストでデッドロックしうるため、`_wizard_create_locked`（`server.py:4847-4860`）と同じ順序にする。
7. 作成後 `with wizard_lock:` で draft を再ロードし、
   `status="created"`, `created_sid=sid`, `created_title=title`, `step="preview"`, `updated=now` にして `wizard_save`。
   - これにより次回 `/api/wizard/start` で「前回の続きから再開しますか？」が出ない（`status` が resume 対象外）。
8. 応答を返す。**開幕画像の生成は行わない**（`wizard_enqueue_opening_image` を呼ばない）。
9. `wizard_route` の POST 側に追加（GET は既存のフォールバックで 404）。

冪等性のための `created_title` は manual 用に新設（`_wizard_created_result` は `compose.setting.title` 前提のため使わない）。

### 6.3 チャンネル名ダイアログ `askText()`

`askChoice()` と同じオーバーレイ（`.wizard-confirm`）を使う入力版を追加する。

```js
const title = await askText({
  title: "チャンネル名",
  message: "新しいチャンネルの名前を入力してください。",
  value: "新しいチャンネル",   // プリフィル＋全選択
  maxlength: 40,
  okLabel: "作成",
  cancelLabel: "キャンセル",
});   // → string | null
```

- Esc・背景クリック・キャンセル → `null`（**何も作成しない**、2/7 に留まる）。
- Enter → 作成。空文字（trim 後）はインラインエラー「チャンネル名を入力してください」を表示し、閉じない。
- 40 文字超過は入力段階で不可（`maxlength`）。送信値は trim。
- 要素IDは `#wizAskText` / `#wizAskTextTitle`（`askChoice` の `#wizConfirmTitle` と衝突させない）。
- 表示中は `.wizard-modal` を `inert`、`dialogOpen = true`、`dialogDone` に解除関数を登録（`clearDialog()` で `cancel` 扱い）。
- フォーカス: 開いた直後に `input.select()`（`setTimeout(..., 0)`）。閉じたら元の要素へ戻す。
- `askChoice()` と同じく、`document.createElement` が無い環境（テスト用スタブ）では `null` を返す。

### 6.3.1 ウィザードが閉じている状態でも使えること（必須）

`legacyNewSession()`（ウィザード無効時のフォールバック）からも `askText` を呼ぶため、
**`#wizardRoot` が `hidden` のときでもダイアログを表示できる**必要がある。

```js
const r = root();
const host = (r && !r.classList.contains("hidden")) ? r : document.body;
// host が body のときは .wizard-modal の inert 操作をしない
```

- CSS: `.wizard-confirm` は `position: fixed` にする（`#wizardRoot` 内でも `body` 直下でも同じ位置・同じ見た目になる）。
- `body` 直下に出すときは `wrap.classList.add("on-body")` を付け、`.wizard-confirm.on-body { z-index: 1400 }` とする
  （`.wizard-root` は `1200`、`#toast` は `1300`）。ルート内に出すときは従来どおり overlay 内の `z-index: 10` で足りる。
- `body.wizard-open` は付与しない（スクロールロックはウィザード表示中だけの既存仕様を変えない）。

### 6.4 作成後のUI（D6）

1. `askText` の結果で `POST /api/wizard/manual`（`runAction("create", …)`、二重送信防止）。
2. 成功時:
   1. `close(true)`（保存確認ダイアログを出さず即閉じる）
      - `closeNow()` は `draft.step !== screen` のとき `/api/wizard/step` を投げるが、下書きは `created` なので
        409 になり `.catch(() => {})` で無視される（既存挙動。エラー通知は出さない）。
   2. `window.afterChannelCreated(sid)`（§6.5）を呼ぶ
3. 失敗時: ウィザードは **2/7 のまま**、トーストで理由表示。
   - 入力したタイトルは `lastManualTitle` に保持し、再度「手動で作成」を押したときに
     `askText` の `value` にプリフィルする（再入力の手間を省く）。
   - `409 rev`: `apiCall` の共通処理（draft 差し替え＋通知）に従う。
   - `409 wizard_busy`: 「生成中です。完了してから操作してください」。
   - 通信断: 「作成できません」＋再試行可（カード再押下）。

### 6.5 `window.afterChannelCreated(sid)` の抽出（`public/app.js`）

`legacyNewSession()` の作成後処理を共通関数として抽出し、旧経路と手動作成の両方から呼ぶ。

```js
async function afterChannelCreated(sid) { ... }
window.afterChannelCreated = afterChannelCreated;
```

処理内容（現行 `app.js:10783-10796` 相当）:
1. `refreshSessions()` → `switchTo(sid)`
2. `$("settingsPanel").classList.remove("hidden")`（**設定パネルを開く**）
3. `updateFabState()`、`flash("新しいチャンネルを作成しました")`
4. アプリ設定 `meta.app?.new_channel_ide !== false` のとき `/api/ide/new`（`target:"both"`）を実行し、
   結果をトースト。失敗してもチャンネル作成は成功として扱う。
5. 作成直後に `if (sid === window.sid 相当) await poll()` など現行の再取得処理を維持する
   （旧実装は `sid === r.sid` で判定。ヘルパーでは「現在開いているチャンネルが新規 sid と一致するか」で判定する）。
6. `loadIdeSessions()` / `refreshSessions()` の再取得（IDE会話作成後）

### 6.6 ネイティブ `prompt()` の廃止

- `legacyNewSession()` の `prompt("新しいチャンネル名", ...)` を削除し、`window.RPWizard.askText(...)` を使う
  （戻り値 `null` なら中止）。
- `askText` は `window.RPWizard = { open, close, showModel, askText }` として公開する。
  `app.js` 側は `typeof window.RPWizard?.askText === "function"` を確認し、無い場合（古いキャッシュ）は
  既定タイトル「新しいチャンネル」で続行する。
- `data-act="empty"`（空のチャンネル）と `window.callLegacyNewSession` の公開は削除
  （wizard.js からの呼び出しが無くなるため。app.js 内の `legacyNewSession()` 直呼びは残す）。

---

## 7. プレビュー確認オプション（confirm）撤去

### 7.1 削除対象

| ファイル | 対象 |
|---|---|
| `public/index.html` | 設定パネルの `<input id="f_wizard_confirm"> 作成前にプレビューを確認する` 行（`<label class="wizard-check">` ごと） |
| `public/wizard.js` | `#wizConfirm`（2/7）、`readIdeaForm`/`saveIdea`/`renderIdea`/`flushIdea` の `confirm`、`saveModel` の `wizard_confirm` 送信、`loadWizardSettings`/`saveWizardSettings`/`init` の `#f_wizard_confirm` 参照（`1834`/`1846`/`1857`/`1950`）、`startCreateChain`/`pollJob`/`resumeJob` の `draft.confirm` 分岐、`autoCreateOnce` 全廃（`16,379,548,575,1038,1593,1630,1668,1681,1692,1778,1781`）、`data-act="empty"` |
| `public/app.js` | `f_wizard_confirm` の読み書き（`1029`, `4320`）、`legacyNewSession` の `prompt()`、`window.callLegacyNewSession` |
| `server.py` | `DEFAULT_APP["wizard_confirm"]`（221）、`wizard_start` の第3引数（5075）、`wizard_new_draft(base, model, confirm)` の `confirm` 仮引数・保存（4438/4470）、`wizard_public` の `"confirm"`（4497）、`/api/wizard/meta` の `"confirm"`（5110）、`/api/app-settings` の正規化（13510）、`/api/wizard/model`（5181-5182）と `/api/wizard/step`（5192-5193）の `if "confirm" in body` |
| テスト | `test_channel_wizard_ui.cjs` の `fWizardConfirm` 生成（162）・presence 検査（367）・payload 検査（607）、`test_wizard_server.py` の `new_draft()`（93-97, `wizard_new_draft` の引数） |

### 7.2 既存データ互換

- 既存下書きの `confirm`、既存 `data/settings.json` の `wizard_confirm` は**無視**（削除しない）。
  `app_settings()` は `DEFAULT_APP` のキーだけを正規化するため、将来の保存で自然に落ちる。
- `/api/wizard/answer` `/api/wizard/step` `/api/wizard/model` に `confirm` が来ても無視（受理するが保存しない）。

### 7.3 新しい分岐（確定形）

```js
// pollJob: compose 完了時（confirm 分岐と autoCreateOnce 分岐を削除）
if (jobKind === "questions") navigate("questions");
else { delete snap.preview; settleTo("preview", returnTo); }   // 必ずプレビュー

// resumeJob: compose 完了時
enterScreen(entryScreen());   // step=preview なら preview

// startCreateChain
if (draft.compose && !draft.compose_stale) return navigate("preview");
if (withQuestions && !draft.done_questions && draft.round < max) return startJob("questions");
return startJob("compose");     // AIなしでもサーバーがテンプレ生成 → preview
```

### 7.4 プレビュー画面の「作り直す」

- `wizard_ai_enabled(draft)` 相当（`provider === "none"` または `ai_enabled === false`）のときは
  `data-act="recompose"` を**非表示**にする。
- AIありのときは従来どおり。

---

## 8. エッジケース

| 状況 | 期待 |
|---|---|
| 2/7 で二重タップ | `pending` + `#wizardBody[inert]` により 1 回だけ実行 |
| アイデアを入力して「手動で作成」 | 入力は**使わず破棄**（作成後に下書きは `created` になる）。カードの説明文で「AIを使わず…」と明示済み |
| 名ダイアログで Esc / 背景クリック | 何も作らず 2/7 に戻る |
| 名ダイアログで作成 API が失敗 | 2/7 に戻り、次回のダイアログに**同じタイトルをプリフィル**（`lastManualTitle`） |
| 名ダイアログで空文字 | インラインエラー、ダイアログ維持 |
| 作成 API の再送（連打・リトライ） | 既存 `created_sid` を返し**二重作成しない** |
| 作成成功直後の `closeNow()` の step 保存 | 下書きが `created` のため 409。`catch` で無視（UI にエラーを出さない） |
| 作成 API が 409 `rev` | トーストで通知、2/7 に留まる（再押下で再試行） |
| 作成 API が 409 `wizard_busy` | 同上（生成中の旨を表示） |
| `inherit_base=true` だが `base_setting` が空 | 初期設定で作成し、`warnings` で通知 |
| 設定パネルが既に開いている | 開いたまま（二重オープンしない） |
| Antigravity 未有効／未設定 | チャンネル作成は成功。IDE会話作成の失敗はトーストのみ |
| 1/7 で「初期設定で新しく作る」を選んだ | 手動でも引き継がない（`base_setting` を使わない） |
| 2/7 で AIチップを押した | 3/7 を編集モードで開き、保存で 2/7 に戻る（`ideaMode` は不変） |
| AIなし（3/7「AIを使わずに作る」） | テンプレ生成 → 7/7 プレビュー（「作り直す」は非表示） |
| 生成中（compose/questions 実行中）に手動で作成 | 409 `wizard_busy`。※2/7 には通常到達しない |

---

## 9. テスト計画

### 9.1 `test/test_channel_wizard_ui.cjs`（JS）

1. 2/7 の HTML に `data-act="idea-consult" | "idea-auto" | "idea-manual"` の3要素がある。
2. `#wizConfirm` が生成 HTML に含まれない。
3. `#f_wizard_confirm` が `index.html` に含まれない（既存 367 行を置換）。
4. アプリ設定の保存 payload に `wizard_confirm` が**含まれない**（既存 607 行を置換）。
5. 「AIにおまかせ」で入力があるとき `custom_text` が送られ `auto:false`／空欄なら `auto:true`。
6. `startCreateChain()` が compose 済みで**常に preview**（`doCreate` を呼ばない）。
7. `pollJob` の compose 完了で `doCreate` が呼ばれない。
8. `saveModel()` が `ideaMode==="auto"` で `startCreateChain(false)`、`"consult"` で `partner` へ。
9. AIなし時にプレビューの `data-act="recompose"` が無い（7.4）。
10. `setFoot("")` かつ戻る非表示のとき `footer.wizard-foot` に `hidden` が付く（戻るが出ている 2/7 では付かない）。
11. `askText()` がダイアログを返し、Esc/キャンセルで `null`、Enter で入力値を返す（DOMスタブ環境では `null`）。
12. `progressFor` の 1〜7 対応（既存テスト維持）。

### 9.2 `test/test_wizard_server.py`（pytest）

1. `/api/wizard/manual`（`inherit_base=True`）→ 新規セッションが `base_setting` 由来（`user_name` / `project` 等）で、ストーリー・進捗は初期化。
2. `inherit_base=False` → 既定の初期設定で作成。
3. `inherit_base=True` で `base_setting` 空 → 初期設定＋`warnings`。
4. `title` 空 → 400 / 40 文字超は切り詰め（`wizard_plain_text`）で成功。
5. `rev` 不一致 → 409 `rev`。
6. 生成中 → 409 `wizard_busy`。
7. 2回呼ぶと同一 `sid`（冪等・二重作成なし）。
8. 成功後 `/api/wizard/start` で同じ下書きが `resumed` にならない（`status=created`）。
9. `/api/wizard/model` `/api/wizard/step` に `confirm` を送っても draft に保存されない。
10. AIなし compose がテンプレでプレビュー状態（`step=preview`）になる（既存 `test_ai_disabled_uses_template` を維持・拡張）。

### 9.3 手動チェックリスト（ブラウザ）

- [ ] 1/7 引継ぎ → 2/7「AIにおまかせ」（一言入力）→ 3/7 → **7/7 プレビュー**が表示され、`作りたい話` が入力どおり
- [ ] 7/7 で「この内容で作成」→ チャンネルが作成され、内容が一言を反映している
- [ ] 1/7 引継ぎ → 2/7「AIと相談」→ 4/7〜6/7 を経て 7/7 → 作成
- [ ] 1/7 引継ぎ → 2/7「手動で作成」→ 名前ダイアログ → 作成 → 設定パネルが開く／画像設定等が引き継がれている
- [ ] 1/7「初期設定で新しく作る」→ 手動で作成 → 引き継ぎが発生していない
- [ ] 3/7「AIを使わずに作る」→ テンプレのプレビュー → 作成（「作り直す」が無い）
- [ ] 名ダイアログ: Esc・背景クリック・空文字・Enter の挙動
- [ ] 2/7 で「戻る」→ 1/7 に戻れる／3/7 で「戻る」→ 2/7 で選び直せる
- [ ] 旧下書き（`confirm:false`）を開いても必ず 7/7 を通る
- [ ] ウィザード無効時に新規作成 → ネイティブ `prompt()` が出ず、`askText` ダイアログで作成できる

---

## 10. 実装順序

1. **server**: `new_channel_setting()` ヘルパー抽出（`/api/session/new` と共用）→ `/api/wizard/manual` 追加（`wizard_create_lock`・`created_title`・冪等）→ `confirm` 関連撤去 → pytest 追加。
2. **client(wizard)**: `askText()` 追加＋`window.RPWizard.askText` 公開 → `.wizard-choice` と 2/7 3択 → `syncFootBar()` による空フッター非表示 → `saveIdea` の入力保持 → `ideaMode` 導入・`confirm`/`autoCreateOnce` 撤去 → AIなし経路のプレビュー化 → 手動作成の結線。
3. **client(app)**: `window.afterChannelCreated(sid)` 抽出、`legacyNewSession` の `prompt()` を `askText` に差し替え、`f_wizard_confirm` 撤去、`window.callLegacyNewSession` 削除。
   - `legacyNewSession` はウィザードが閉じている状態で呼ばれるため、`askText` の `body` 直下フォールバック（§6.3.1）と `z-index` を先に実装する。
4. **index.html**: 設定パネル行削除、`wizard.js` / `wizard.css` の `?v=` 更新。
5. **tests**: §9 を実装し、`node test/test_channel_wizard_ui.cjs` と `pytest test/test_wizard_server.py` を通す。
