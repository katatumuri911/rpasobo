# チャンネルウィザード「戻る」ナビゲーション／入力退避 仕様書

- 対象: `public/index.html` / `public/wizard.css` / `public/wizard.js` / `server.py`（`/api/wizard/job_cancel` のみ）
- テスト: `test/test_channel_wizard_ui.cjs` / `test/test_wizard_server.py`
- 状態: 実装前（本仕様で確定）
- 関連: `docs/channel-wizard-spec.md`（5.7 フッター「戻る」、11 UI 受け入れ基準）

---

## 0. なぜ必要か（実測した現状）

| # | 事実 | 根拠 |
|---|---|---|
| 1 | ヘッダーの `‹` は 1/7・2/7・creating で `display:none`。他画面でも実インク約 7×5.6px（明るい画素 11 個）で、ボタンとして知覚できない | ブラウザ実測（`#wizardBack` の rect 21.7×35.9 / style `none` / 画素解析） |
| 2 | フッターに戻る系は 1 つも無い。当初設計（`channel-wizard-spec.md:237`）には「作成 / 全部作り直す / 戻る」と明記されている | `wizard.js` の各 `setFoot` |
| 3 | 3/7 で `‹` を押すと 2/7「作成AIを選ぶ」へ飛び、**その画面で `‹` が `display:none` になる**（戻る手段が消える） | 実測 `titleAfterBack="作成AIを選ぶ"` / `backClass="hidden"` |
| 4 | `goBack()` は保存を一切呼ばない。`close()` も step しか保存しないのに「下書きとして保存されます」と表示する | `wizard.js:282-292`, `wizard.js:262-280` |
| 5 | パートナー画面の「おまかせ」は入力済みを無確認で破棄する（`partner_type/work/partner_name/player_type/player_name/start_point` を auto 化、`INVALIDATES` で `player_work/profile` も消える） | `wizard.js:652-661`, `server.py:2862-2869` |
| 6 | `/api/wizard/slots` と `/api/wizard/tags` は**無変更でも** `compose_stale=True` にする | `server.py:4596-4598`, `server.py:4619-4620` |
| 7 | タグ自動保存が競合すると `apiCall` が画面全体を再描画し、未保存の DOM を捨てる | `wizard.js:33-45`, `wizard.js:302-320` |

---

## 1. 目的 / 非ゴール

### 目的

1. **常に視認できる「戻る」**をフッター左に置く（ラベル付き、全画面で一貫）。
2. **保存対象の画面（idea / partner / tags / preview）では**、戻る／閉じる／Esc／背景クリックのどの経路でも入力が消えない。questions の回答・model の選択は「答える」「保存して次へ」でのみ保存し、破棄する場合は画面別の確認文で明示する（§6.6, §7.2, §7.3）。
3. 戻り先をハードコードではなく**履歴**で決める（`idea → model` の強制遷移、model の戻り先消失、`done` の無反応を同時に解消）。
4. 生成中に戻る場合の**ジョブ中断を原子的**にし、直後の保存が `wizard_busy` で失敗しないようにする。
5. 上記を**既存の画面・API・スロット意味論を変えずに**達成する（`/slots`・`/tags` の `compose_stale` を無駄に立てない）。

### 非ゴール（別チケット）

- 引き継ぎ元の再選択（`base_locked` の解除）
- おまかせ時にサーバー側でチャンネル作成まで完了させる件（P4）
- 画像タグ編集 / Danbooru / タグ拡充 / `ng_minor` / 質問カウント / モデル表示名
- 質問1問ごとの「前の質問へ」（当初仕様 `channel-wizard-spec.md:212` だが、回答履歴の保存が必要なため別設計）
- ジョブのディスク永続化

---

## 2. 設計原則（不変条件）

- **I1**: 常に `navStack[navStack.length-1] === screen`。
- **I2**: `navStack` に重複を許さない（`navigate` は「存在すれば切り詰め、無ければ push」）。
- **I3**: `creating` はジョブ決着時に必ずスタックから除去する（戻るで再ジョブしない）。
- **I4**: サーバー書き込み後は必ず返却 `draft` を採用してから次の書き込みを行う（rev の連鎖）。
- **I5**: 退避書き込みは**差分がある時だけ**行う（`compose_stale` の無駄な無効化を防ぐ）。
- **I6**: 同時実行は「ユーザー操作 1 件（`pending`）」＋「タグ保存 1 件（`tagSavePromise`）」まで。
- **I7**: `screen === "creating"` は `jobId` を持つ。再開時は `resumeJob()` が `/job/{id}` で queued/running を確認してから `jobId` を設定する（I9）。
- **I8**: `open()` / `closeNow()` で一時状態（stack / snap / pending / jobId / tagTimer / tagDirty / pendingTags / returnTo / rendering / resumePromise）を必ず初期化する。
- **I9**: `jobId` が非空でよいのは `creating` / `questions` の表示中だけ。他画面へ抜ける時はキャンセルか完了で必ずクリアする。
  - `jobId` は「開始APIが成功した」または「`/api/wizard/job/{id}` が queued/running を返した」時だけ設定する。**`draft.last_job` を直接代入しない**（`last_job` は完了後も残る履歴ポインタで、生存フラグではない）。
  - 根拠: `renderCreating` / `renderQuestions` は `jobId` が残っているとポールを再開せず、本文も描画しないため、遷移で持ち越すとスピナーで固まる（既存バグ）。

---

## 3. 状態モデル

```js
// 既存
let screen = "idea", busy = false, jobId = "", jobKind = "", jobTimer = null;
let pendingTags = null, tagTimer = null, draft = null, wmeta = null, autoCreateOnce = false;

// 追加
let navStack = [];        // 画面名の履歴（末尾が現在地）
let returnTo = "tags";    // compose ジョブ完了後に戻す親画面
let pending = "";         // 実行中のユーザー操作名（"back" / "next" / "create" / "close" ...）
let tagSavePromise = null;// タグ保存の直列化
let tagDirty = false;     // 前回保存後にタグ/自由入力が変わった
let snap = {};            // 退避失敗時のみ使う画面スナップショット
let renderSeq = 0;        // 非同期rendererの世代（古いawait後の書き込みを捨てる）
let rendering = false;    // 現在画面の初回描画が終わるまで true
let formSeq = 0;          // 入力の版（保存中の追記検出）
let resumePromise = null; // ジョブ再開の二重実行防止
```

`snap` の役割は**失敗時フォールバックのみ**。通常は「離脱時にサーバーへ差分保存 → 戻ったらサーバー値で描画」で足りる。

---

## 4. 画面遷移

### 4.1 基本操作

```js
function render(name) {                 // 画面を描くだけ。スタックは触らない
  stopPoll();
  const seq = ++renderSeq;              // ★非同期rendererはこの世代を確認する
  rendering = true;
  screen = name;
  formSeq = 0;
  updateChrome();
  persistStep(name);
  const finish = () => { if (seq === renderSeq) rendering = false; };
  const fn = ({
    base: renderBase, model: renderModel, idea: renderIdea, partner: renderPartner,
    tags: renderTags, questions: renderQuestions, preview: renderPreview,
    creating: renderCreating, done: renderDone,
  })[name];
  const p = fn ? fn() : null;
  if (p && typeof p.then === "function") p.then(finish, finish);
  else finish();
}

function navigate(name) {               // 通常の遷移
  // ★ screen ではなく「スタックの先頭」と比較する。
  //   settleTo で履歴を畳んだ直後は screen と先頭がズレるため、
  //   screen 比較だと I1（先頭 === screen）が壊れる。
  if (navStack[navStack.length - 1] !== name) {
    const i = navStack.lastIndexOf(name);
    if (i >= 0) navStack = navStack.slice(0, i + 1);  // 既知の画面へは戻る扱い
    else navStack.push(name);
  }
  render(name);
}

function backRender() {                 // 戻る（pop 済みの前提）
  render(navStack[navStack.length - 1]);
}

function settleTo(name, parent) {       // ジョブ決着など、履歴を畳んでから進む
  const i = navStack.lastIndexOf(parent);
  navStack = i >= 0 ? navStack.slice(0, i + 1) : [parent];
  navigate(name);
}

function resetTo(name) {                // 単一画面にリセット（戻り先を作らない用途）
  navStack = [name];
  render(name);
}

const ENTRY_STACK = {
  base: ["base"], model: ["model"], idea: ["idea"],
  partner: ["idea", "partner"], tags: ["partner", "tags"],
  questions: ["tags", "questions"], preview: ["tags", "preview"],
  creating: ["tags", "creating"], done: ["done"],
};
function enterScreen(name) {             // 再開・初回（§4.3 の合成スタック）
  navStack = (ENTRY_STACK[name] || [name]).slice();
  render(name);
}

function chromeFor(name) {               // 本文だけエラー表示。スタックは整合させる
  renderSeq += 1;                        // 進行中の非同期rendererを無効化
  rendering = false;
  if (navStack[navStack.length - 1] !== name) {
    const i = navStack.lastIndexOf(name);
    if (i >= 0) navStack = navStack.slice(0, i + 1); else navStack.push(name);
  }
  screen = name;
  updateChrome();
  persistStep(name);
}
```

- `show(name)` は**廃止**し、全呼び出しを `navigate()` に置換する（`window.RPWizard.showModel` の `show("model")` も `navigate` に直す）。
- 初回・再開は `enterScreen()`、単一画面へ畳む用途は `resetTo()` を使う。
- `chromeFor(name)` は「本文だけエラー表示」に使うが、**スタックは `navigate` と同じ規則で整合させる**（I1 を壊さない）。

### 4.2 戻る可否と戻り先

| 画面 | 戻る | 戻り先 | 退避 | 備考 |
|---|---|---|---|---|
| `base` | 非表示（ルート） | — | なし | 選択は `saveBase` で即保存済み |
| `model` | あり | スタックの親（チップで開いた場合）。ルートなら非表示 | なし（破棄） | アプリ全体設定なので戻るでは保存しない |
| `idea` | あり（ルート時は非表示） | `base` or 親 | `rough_idea` / `confirm` | |
| `partner` | あり | `idea` | スロット差分 + プリセット | §6.2 の規則 |
| `tags` | あり | `partner` | タグ（dirty 時のみ） | §6.3 |
| `questions` | あり | `tags` | なし | 実行中（`jobId` 非空）の時だけキャンセル確認 |
| `preview` | あり | 親（通常 `tags`） | overrides 差分 | 検証失敗でも遷移（snap 保持） |
| `creating` | あり（＝中止） | `returnTo` | なし | `jobId` があればキャンセル確認。**キャンセル失敗時は遷移しない** |
| `done` | 非表示 | — | — | 終端 |

```js
function canBack() {
  return !busy && !pending && !rendering && navStack.length > 1 && screen !== "done";
}
```

### 4.3 再開時の合成スタック

`entryScreen()` の結果に対して、**論理的な親**を積んだ状態で開始する（再開直後でも戻るが機能する）。

| entry | 初期 `navStack` |
|---|---|
| `base` | `["base"]` |
| `model` | `["model"]` |
| `idea` | `["idea"]` |
| `partner` | `["idea", "partner"]` |
| `tags` | `["partner", "tags"]` |
| `questions` | `["tags", "questions"]` |
| `preview` | `["tags", "preview"]` |
| `creating` | `["tags", "creating"]` |
| `done` | `["done"]` |

> 再開時に `base` を積まない理由: `base` は `base_locked` で操作できないため、戻り先として出すと行き止まりになる。
> 実装は `enterScreen()`（§4.1）を使う。`resetTo()` は単一画面へ畳む用途だけに残す。

### 4.4 例外遷移

| 起点 | 遷移 | 実装 |
|---|---|---|
| model の「保存して次へ / AIを使わずに作る」 | スタックの親へ戻る。親が `base` かルートなら `idea` へ | `closeModel()` |
| `idea-auto` → compose 完了 | `returnTo` に畳んで `preview` | `settleTo("preview", returnTo)` |
| `tags-next` → questions 完了 | `questions` のまま | `navigate("questions")` |
| ジョブ error | 画面維持＋本文エラー | `chromeFor()`（既存） |
| `doCreate` 失敗 | `preview`（compose あり）/ `tags` | `settleTo()` で重複を作らない |
| 再開後のジョブ決着 | entry にリセット | `enterScreen(entryScreen())` |

`returnTo` の決定（compose 開始時）:

```js
const parent = navStack[navStack.length - 2] || "tags";
returnTo = ["questions", "preview", "creating"].includes(screen) ? parent : screen;
```

`closeModel()`（`saveModel` / `model-skip` の遷移先）:

```js
function closeModel() {
  const parent = navStack[navStack.length - 2];
  // base は base_locked で操作できないため戻り先にしない（行き止まり防止）
  if (!parent || parent === "base") return resetTo("idea");
  return navigate(parent);
}
```

---

## 5. 戻るボタン UI 仕様

### 5.1 マークアップ（静的。再描画で消えない）

```html
<footer class="wizard-foot">
  <button type="button" id="wizardBack" class="wizard-btn ghost wizard-back">戻る</button>
  <div id="wizardFoot"></div>
</footer>
```

- ヘッダーの `‹` は**削除**する（戻る導線を1本化）。
- 既存テスト `test_channel_wizard_ui.cjs:193` は `id="wizardBack"` の存在を見るため、**id を維持**すれば緑のまま。
- 静的要素なので `init()` の `onclick` バインドは1回だけでよい。

### 5.2 スタイル

```css
.wizard-foot { justify-content: flex-start; }
.wizard-back { margin-right: auto; }
#wizardFoot { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; margin-left: auto; }
@media (max-width: 640px) { .wizard-foot { row-gap: 8px; } }
```

- 親の `justify-content: flex-end` は**必ず外す**。右寄せは子の `margin-left:auto` が担う。両方に残すと戻るボタンが右へ寄る。
- 360px 幅で「戻る」＋3ボタンが2段になっても崩れないこと（§11）。
- ラベルは記号のみにしない（`‹` は使わない）。

### 5.3 状態

| 条件 | 表示 |
|---|---|
| `canBack()` | 表示・有効 |
| `!canBack()` かつ `navStack.length > 1`（busy/pending/rendering） | 表示・`disabled` |
| `navStack.length <= 1` | `hidden` |
| `done` | `hidden` |

`updateChrome()` で `hidden` / `disabled` / `aria-disabled` を更新する。

### 5.4 フォーカス

- 画面が**変わった時だけ** `#wizardTitle`（`tabindex="-1"`）へ `focus({preventScroll:true})`。
- 同一画面の再描画ではフォーカスを動かさない（入力中に奪わない）。
- `trapFocus()` の初期フォーカスも `#wizardTitle` に変更（現状はヘッダー先頭のボタン）。
- **タイトルは `tabindex="-1"` なので通常のTab候補から外れる**。Tabトラップは「`activeElement === #wizardTitle`」の時も明示的に処理する（Shift+Tab→最後、Tab→先頭）。これを入れないと、タイトルにフォーカスがある間だけモーダル外へ抜ける。

---

## 6. 入力退避（保存）仕様

### 6.1 方針

- **「次へ」は既存どおり**（`saveIdea` / `savePartner` / `saveTagsAndGo` / `savePreview`）。意味論は変えない。
- ただし既存の保存関数の**成功時にも `delete snap[画面]`** を追加する（§7.4。古い退避値の上書き防止）。`saveTagsAndGo` は `drainTagSaves()` に差し替える（§6.3）。
- **「戻る」「閉じる」は差分退避**。画面の値が `draft` と異なる項目だけ送る。
- 送信後は返却 `draft` を採用する（I4）。
- 失敗時は `snap[画面]` に値を保持して遷移し、トーストで知らせる（閉じるだけは §8）。

```js
// values 省略時は現在の DOM から読む（戻る時）。
// snap を渡すとその値で再送する（閉じる時の再試行）。
// 例外は投げない。{ ok, changed, error } を返す。
async function flushOne(name, values) {
  if (name === "idea")    return await flushIdea(values);
  if (name === "partner") return await flushPartner(values);
  if (name === "tags")    return await flushTags(values);
  if (name === "preview") return await flushPreview(values);
  return { ok: true, changed: false };
}

async function flushScreen(name, values) {
  const seq = formSeq;
  try {
    const r = await flushOne(name, values);
    // 保存中に追記されたら「成功」にしない（inert 非対応ブラウザ用の保険）
    if (!values && seq !== formSeq) {
      snap[name] = readScreenValues(name);
      return { ok: false, changed: true, error: "入力が更新されました" };
    }
    return r;
  } catch (e) {
    snap[name] = values || readScreenValues(name);  // 失敗時だけ保持
    return { ok: false, changed: true, error: e.message };
  }
}

// 追記があったら最大2回まで送り直す。安定しなければ遷移しない。
async function flushStable(name, tries = 2) {
  for (let i = 0; i < tries; i++) {
    const r = await flushScreen(name);
    if (r.ok) return r;
    if (r.error !== "入力が更新されました") return r;
  }
  return { ok: false, changed: true, error: "入力が確定しません。もう一度お試しください" };
}
```

`snap` の形（画面ごとに固定）:

| 画面 | 形 |
|---|---|
| `idea` | `{ idea, confirm }` |
| `partner` | `{ values, touched, preset }` |
| `tags` | `{ tags, ng, custom }` |
| `preview` | 差分 patch（`character` はネスト） |

### 6.2 partner（最重要）

送信規則:

| 項目 | 送信条件 | 空のとき | 備考 |
|---|---|---|---|
| `partner_type` | この画面でセグメントを**クリックした時のみ** | 送らない | `auto` は「操作」であり値ではない。**絶対に送らない**（全消し防止） |
| `work` / `partner_name` / `player_work` / `player_name` / `player_profile` / `start_point` / `goal` / `project` | DOM 値 ≠ `draft.slots[k].value` | `{value:"", source:"auto"}` | 空＝おまかせ（既存 `textSlot` と同じ） |
| `player_type` / `canon_fidelity` | この画面でセレクトを**変更した時のみ**（親変更時は子として再送） | 送らない | 初期表示が既定値のため、未操作を差分と誤認しない |
| `ending` | 変更時のみ | auto | `""` は既存の「おまかせ」 |
| プリセット | 変更時のみ `/answer {question_id:"fixed_preset"}` | `option_ids:[]`（解除） | `/slots` がプリセットを消すので**必ず後**に送る |

- セグメント／セレクトの「触った」判定は `renderPartner()` 内で `const touched = new Set()` をリセットし、`change`/`click` で追加する。
- `partner_type` を `auto` にした場合、`touched` には入るが**送信対象外**。戻った時は保存済みの値（または未設定）が表示される。
  - 理由: `savePartner(true)` の全消しを「戻る」で誘発しないため。
- 次へ（`partner-next`）は**現行どおり**全項目を送る（確定操作）。戻る（退避）とは非対称だが、意図的な設計とする。
- **親変更時の子スロット（決定）**: `work` / `partner_type` / `player_type` / `player_work` を変更すると `INVALIDATES`（`server.py:2883-2888`）で子が消える。差分退避でこれを放置すると「次へ」と結果が変わり、無言で名前・開始地点・原作準拠度が消える。
  - そこで `buildPartnerPatch()` は、**変更された親キーの子（`INVALIDATES`）を現在のフォーム値で patch に追加**する（子自身が patch にある場合は除く）。空の子は `auto` で送る。
  - `APPLY_ORDER`（`server.py:2891-2897`）は親→子なので、子の再送で復元される。「次へ」（全項目送信）と同じ結果になる。
  - これにより「作品を変えたら名前も自動で無効」は**退避では起きない**。無効にしたい場合はユーザーが名前を空にする（空は `auto` として送られる）。この非対称は意図的な決定であり、§11 でテストする。

```js
// readPartnerForm() は { values, touched:Set, preset } を返す（DOM からの読み出しを1箇所に集約）
async function flushPartner(snapValues) {
  const v = snapValues || readPartnerForm();
  const patch = buildPartnerPatch(v);          // 差分のみ。touched を考慮
  let changed = false;
  if (Object.keys(patch).length) {
    await saveSlots(patch, "partner");        // draft 更新
    changed = true;
  }
  if (v.preset !== String(draft.partner_preset_id || "")) {
    // /slots が partner_preset_id を消すので、必ず slots の後に送る
    const r = await apiCall("/api/wizard/answer", {
      draft_id: draft.id, rev: draft.rev, question_id: "fixed_preset",
      option_ids: v.preset ? [v.preset] : [], step: "partner",
    });
    if (r && r.draft) draft = r.draft;
    changed = true;
  }
  return { ok: true, changed };
}
```

### 6.3 tags

```js
function markTagsDirty() { tagDirty = true; }

function scheduleTagSave() {
  if (tagTimer) clearTimeout(tagTimer);
  tagDirty = true;
  tagTimer = setTimeout(() => {
    tagTimer = null;                       // ★発火時に必ず消す（残すと毎回再送＝compose_stale 再発）
    saveTagsToServer().catch(e => say(e.message));
  }, 500);
}

// 進行中の保存と未保存の変更を、最大3巡で追い込む
async function drainTagSaves() {
  if (!pendingTags) return true;
  for (let i = 0; i < 3; i++) {
    if (tagSavePromise) await tagSavePromise;
    if (!tagDirty) return true;
    await saveTagsToServer();
  }
  return !tagDirty;
}

async function flushTags(snapValues) {
  // snapValues は「前回失敗した内容」なので dirty 判定をバイパスする
  if (!snapValues && !tagDirty && !tagTimer) return { ok: true, changed: false };  // ★無変更なら絶対に送らない
  if (tagTimer) { clearTimeout(tagTimer); tagTimer = null; }
  if (snapValues) {
    pendingTags = { tags: snapValues.tags.slice(), ng: snapValues.ng.slice(), custom: snapValues.custom };
    tagDirty = true;
  } else if (pendingTags) {
    const el = $("#wizCustomNg");
    if (el) pendingTags.custom = el.value;
  }
  if (!pendingTags) return { ok: false, changed: false, error: "タグが見つかりません" };
  await drainTagSaves();
  return { ok: true, changed: true };
}
```

- `toggleTag` / `toggleNg` は変更後に `scheduleTagSave()`（内部で `tagDirty = true`）。
- `#wizCustomNg` の `input` でも `scheduleTagSave()` を張る（現行は自動保存が無い）。
- `renderTags()` は `pendingTags` が `null` の時だけ初期化し、`#wizCustomNg` に input を張る。
- `saveTagsAndGo()` は `await saveTagsToServer()` ではなく **`await drainTagSaves()`** にしてから次へ進む（送信中の追記を取りこぼさない）。
- `saveTagsToServer()` は直列化し、**先頭で `pendingTags` の null をガード**する（§7.5）。

### 6.4 preview

- `collectOverrides()` の各キーを `draft.compose.setting`（`user_name` / `project` は描画時と同じ式）と比較し、**変更分だけ**送る。
- `/api/wizard/preview` は `wizard_validate_compose` を通す。必須項目を空にした場合などは `bad_compose` で失敗する。
- **戻る時は失敗しても遷移する**（`snap.preview = patch`、トースト）。閉じる時のみ §8 の確認を出す。
- `snap.preview` は差分なので、描画時は `setting` にマージする（`character` はネストマージ）。
- `recompose` / ジョブ完了で compose が差し替わったら `delete snap.preview`。

### 6.5 idea

- `#wizIdea` と `draft.slots.rough_idea.value` を比較。差分があれば `/api/wizard/slots`（`/answer` ではなく `/slots`。step 更新は `persistStep` が行う）。
- 空 → `{value:"", source:"auto"}`（＝おまかせでクリア）。
- `#wizConfirm` と `draft.confirm` が違えば `/api/wizard/step`（`confirm` は bump しない）。

### 6.6 questions / model / base

- 退避なし。questions の自由入力・選択は破棄される（仕様として明記）。
- model の変更は「保存して次へ」でのみ確定（戻る＝破棄）。

### 6.7 共通ヘルパー（実装の骨子）

```js
// ---- idea ----
function readIdeaForm() {
  return { idea: val("#wizIdea"), confirm: isChecked("#wizConfirm") };
}
async function flushIdea(v) {
  v = v || readIdeaForm();
  const cur = String(((draft.slots || {}).rough_idea || {}).value || "");
  let changed = false;
  if (v.idea !== cur) {
    await saveSlots({ rough_idea: v.idea ? { value: v.idea, source: "user" }
                                       : { value: "", source: "auto" } }, "idea");
    changed = true;
  }
  if (v.confirm !== !!draft.confirm) {
    const r = await apiCall("/api/wizard/step", {
      draft_id: draft.id, rev: draft.rev, step: "idea", confirm: v.confirm,
    });
    if (r && r.draft) draft = r.draft;
    changed = true;
  }
  return { ok: true, changed };
}

// ---- partner ----
function readPartnerForm() {
  const segBox = $("#wizPartnerType");
  const values = {
    // DOM がまだ無い（非同期描画中）場合は draft にフォールバックして例外を出さない
    partner_type: segBox
      ? (($(".wizard-seg-btn.on", segBox) || {}).dataset?.pt || "")
      : String(((draft.slots || {}).partner_type || {}).value || ""),
    work: val("#wizWork"), partner_name: val("#wizPartnerName"),
    player_type: val("#wizPlayerType"), player_work: val("#wizPlayerWork"),
    player_name: val("#wizPlayerName"), player_profile: val("#wizPlayerProfile"),
    start_point: val("#wizStart"), canon_fidelity: val("#wizCanon"),
    goal: val("#wizGoal"), ending: val("#wizEnding"), project: val("#wizProject"),
  };
  return { values, touched: new Set(touchedKeys), preset: val("#wizPreset") };
}
// server.py の INVALIDATES と同じ対応（親→子）
const PARTNER_INVALIDATES = {
  partner_type: ["work", "partner_name", "start_point", "canon_fidelity"],
  work: ["partner_name", "start_point", "canon_fidelity"],
  player_type: ["player_work", "player_name", "player_profile"],
  player_work: ["player_name", "player_profile"],
};

function buildPartnerPatch(v) {
  const patch = {};
  const prevOf = (k) => k === "project"
    ? String(((draft.slots || {}).project || {}).value || (draft.defaults || {}).project || "")
    : String(((draft.slots || {})[k] || {}).value || "");
  const put = (k, next) => {
    if (next === prevOf(k)) return;
    patch[k] = next ? { value: next, source: "user" } : { value: "", source: "auto" };
  };
  // 自由入力（未操作なら DOM 値 == draft なので差分ゼロ）
  for (const k of ["work", "partner_name", "player_work", "player_name",
                   "player_profile", "start_point", "goal", "project", "ending"]) {
    put(k, v.values[k]);
  }
  // 初期表示が既定値のセレクトは「触った時だけ」
  for (const k of ["player_type", "canon_fidelity"]) {
    if (v.touched.has(k)) put(k, v.values[k]);
  }
  // partner_type は original/copyright を明示した時だけ。auto は「操作」なので送らない
  if (v.touched.has("partner_type") && v.values.partner_type
      && v.values.partner_type !== "auto") {
    put("partner_type", v.values.partner_type);
  }
  // ★親を変えた時は、消える子の現在値も一緒に送って復元する（「次へ」と同じ結果）
  const preserve = new Set();
  for (const k of Object.keys(patch)) {
    for (const dep of (PARTNER_INVALIDATES[k] || [])) {
      if (!(dep in patch)) preserve.add(dep);
    }
  }
  for (const k of preserve) {
    const next = v.values[k];
    patch[k] = next ? { value: next, source: "user" } : { value: "", source: "auto" };
  }
  return patch;
}

// ---- preview ----
function buildPreviewPatch(v) {
  const c = (draft.compose && draft.compose.setting) || {};
  const base = (k) => k === "user_name" ? (c.user_name || (draft.defaults || {}).user_name || "あなた")
                    : k === "project"   ? (c.project   || (draft.defaults || {}).project   || "")
                    : (c[k] || "");
  const patch = {};
  for (const k of ["title", "summary", "scenario", "first_message",
                   "instructions", "user_name", "project"]) {
    if (v[k] !== base(k)) patch[k] = v[k];
  }
  const ch = v.character || {}, cch = c.character || {}, dch = {};
  for (const k of ["name", "age", "profile", "speech"]) {
    if (ch[k] !== (cch[k] || "")) dch[k] = ch[k];
  }
  if (Object.keys(dch).length) patch.character = dch;   // 部分パッチでよい（server がマージする）
  return patch;
}
async function flushPreview(v) {
  const patch = v || buildPreviewPatch(collectOverrides());
  if (!Object.keys(patch).length) return { ok: true, changed: false };
  const r = await apiCall("/api/wizard/preview", {
    draft_id: draft.id, rev: draft.rev, setting: patch,
  });
  if (!r || r.error) throw new Error(r.error || "プレビューを保存できません");
  draft = r.draft;
  return { ok: true, changed: true };
}

// ---- 失敗時の退避値を読む ----
function readScreenValues(name) {
  if (name === "idea")    return readIdeaForm();
  if (name === "partner") return readPartnerForm();
  if (name === "tags")    return pendingTags
    ? { tags: pendingTags.tags.slice(), ng: pendingTags.ng.slice(), custom: pendingTags.custom }
    : null;
  if (name === "preview") return buildPreviewPatch(collectOverrides());
  return null;
}
```

---

## 7. 非同期・競合仕様

### 7.1 操作ガード

```js
async function runAction(name, fn) {
  if (busy || pending) return;
  pending = name; updateChrome(); updateFootDisabled();
  const bodyEl = $("#wizardBody");
  if (bodyEl) bodyEl.inert = true;          // ★保存中は本文を編集させない
  try { return await fn(); }
  finally {
    if (bodyEl) bodyEl.inert = false;
    pending = ""; updateChrome(); updateFootDisabled();
  }
}
```

- 戻る・次へ・作成・閉じるはすべて `runAction` 経由（二重クリック・戻る連打の防止）。
- タグ自動保存は `pending` に含めない（バックグラウンド）。
- `pending` 中はフッターの全ボタン（`#wizardFoot button`）・戻る・モデルチップを `disabled` にする。
- `#wizardBody` の `input` / `change` で `formSeq += 1`（`inert` 非対応ブラウザでも保存中の追記を検出できるようにする）。
- `inert` 非対応なら `pointer-events:none` を併用する（キーボード入力は `formSeq` で検出）。

### 7.2 戻る手順

```js
async function goBack() {
  if (!canBack()) return;
  await runAction("back", async () => {
    const running = !!jobId;                        // last_job は履歴。生存判定に使わない（I9）
    if (running && (screen === "creating" || screen === "questions")) {
      if (!window.confirm("生成を中止して戻りますか？")) return;
      const c = await cancelJob();                  // { ok, draft, error }
      if (!c.ok) { say(c.error || "中止できません。通信を確認してください"); return; }  // ★遷移しない
      if (c.draft && c.draft.status === "created") return enterScreen("done");
      if (c.draft && c.draft.compose && !c.draft.compose_stale) return settleTo("preview", returnTo);
      return settleTo(returnTo, returnTo);          // 画面を戻す
    }
    if (screen === "questions"
        && (val("#wizFree") || (foot() && foot().dataset.picked))
        && !window.confirm("入力中の回答は破棄されます。戻りますか？")) return;
    const r = await flushStable(screen);            // 追記があれば再送。失敗しても snap 保持で続行
    if (r.ok) delete snap[screen];                  // 成功時は古い退避値を捨てる
    navStack.pop();
    backRender();
  });
}

// キャンセルは「サーバーで確認できた時だけ成功」。失敗時は画面を動かさない。
async function cancelJob() {
  const jid = jobId;
  if (!jid) return { ok: true, draft };
  const r = await apiCall("/api/wizard/job_cancel", { job_id: jid, draft_id: draft.id });
  if (!r || r.ok === false || !r.draft) {
    return { ok: false, error: (r && r.error) || "生成を中止できませんでした" };
  }
  jobId = ""; autoCreateOnce = false; stopPoll();
  draft = r.draft;                                  // ★返却 draft を必ず採用（I4）
  return { ok: true, draft: r.draft };
}
```

- `apiCall` が例外を投げた場合も `runAction` が中断し、**遷移しない**（§8）。

### 7.3 閉じる手順

```js
async function close(force) {
  if (force) return closeNow();                     // 作成成功後の内部終了（再入ガードを通さない）
  if (busy || pending || rendering) return;        // ✕ 連打・描画中の DOM 読みを弾く
  await runAction("close", async () => {
    const running = !!jobId;
    const msg = running
      ? "生成は続きます。チャンネルの作成は次に開いたときに行ってください。閉じますか？"
      : (screen === "questions" || screen === "model")
        ? "この画面の入力は保存されません。閉じますか？"
        : "入力中の内容を下書きに保存して閉じますか？";
    if (!window.confirm(msg)) return;
    let r = await flushStable(screen);
    if (r.ok) delete snap[screen];
    // 過去に退避し損ねた画面も再送（依存の弱い順。現在画面は上で処理済み）
    for (const name of ["idea", "partner", "tags", "preview"]) {
      if (name === screen || !snap[name]) continue;
      const rr = await flushScreen(name, snap[name]);
      if (rr.ok) delete snap[name]; else r = rr;
    }
    if (!r.ok) {
      if (!window.confirm(`保存できませんでした（${r.error}）。\nこの画面の変更を破棄して閉じますか？`)) return;
    }
    closeNow();
  });
}

function closeNow() {
  autoCreateOnce = false; stopPoll();
  if (tagTimer) { clearTimeout(tagTimer); tagTimer = null; }
  // 作成済みに step を送ると 409 created。ジョブ中も、既に同じ step なら送らない。
  if (draft && draft.status !== "created" && !jobId && (draft.step || "") !== screen) {
    api("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step: screen }).catch(() => {});
  }
  /* 既存の非表示処理・releaseFocus()・updateFabState() */
  navStack = []; snap = {}; pendingTags = null; tagDirty = false; rendering = false;
}
```

- `force=true` は `doCreate` 成功後・`open-created`・`empty` からのみ。確認も退避もしない。**`busy` 中でも閉じられる**のが要点。
- `close()`（ユーザー操作）は `runAction("close")` と `busy || pending || rendering` ガードを通る。**描画中は ✕/Esc/背景クリックを無視**する（DOM が前画面のままなので退避しない）。
- 確認文は画面別。questions / model は「保存されません」。
- `closeNow()` は `pending` を触らない（`runAction` の finally が担当）。

### 7.4 rev（409）

- `apiCall` の `code === "rev"` 分岐から**画面再描画を削除**する。`draft` の更新・`initPendingTags()`・トーストのみ（`pendingTags = null` にしない。§7.5 の保存が null 参照で落ちる）。
- 各保存関数は 409 を受けたら**新 rev で1回だけ再試行**する。
- それでも失敗したら `snap[画面]` を残して遷移（戻る）／中止（閉じる）。
- **画面Xの保存が成功したら、戻る・閉じる・次へを問わず必ず `delete snap[X]`**（古い退避値が新しい保存を上書きするのを防ぐ）。
- `snap` がある画面は `draft` より `snap` を優先して描画する。形は §6.1 のとおり:
  `snap.partner.values[k]`、`snap.idea.idea`、`snap.tags.tags`、`snap.preview[k]`。

### 7.5 タグ保存の直列化

```js
function saveTagsToServer() {
  if (!pendingTags) return Promise.resolve();        // ★close/open との競合ガード
  if (tagSavePromise) return tagSavePromise;
  const sent = { tags: pendingTags.tags.slice(), ng: pendingTags.ng.slice(), custom: pendingTags.custom };
  tagSavePromise = (async () => {
    const r = await apiCall("/api/wizard/tags", {
      draft_id: draft.id, rev: draft.rev,
      selected_tags: sent.tags, selected_ng: sent.ng, custom_ng: sent.custom,
    });
    if (!r || r.error) throw new Error(r.error || "タグを保存できません");
    draft = r.draft;
    delete snap.tags;                                // ★成功したら古い退避値を捨てる
    if (!pendingTags) return;                        // close 済みなら画面同期は不要
    // サーバーがNG衝突で外したタグだけ反映。送信後に増えたトグルは消さない
    const dropped = sent.tags.filter(t => !draft.selected_tags.includes(t));
    pendingTags.tags = pendingTags.tags.filter(t => !dropped.includes(t));
    syncTagChips();
    // 失敗時に dirty を失わないよう、成功後に「送った値 vs 現在値」で再計算する
    tagDirty = pendingTags.tags.join() !== sent.tags.join()
            || pendingTags.ng.join()   !== sent.ng.join()
            || pendingTags.custom      !== sent.custom;
  })().finally(() => { tagSavePromise = null; });
  return tagSavePromise;
}
```

- `drainTagSaves()` の先頭でも `if (!pendingTags) return true;` を置く。
- サーバーが NG 衝突でタグを外した場合、`tagDirty` が真になって**もう1回**送るが、2回目で収束する（無限ループにしない）。
- `saveTagsAndGo()` は `await drainTagSaves()` にしてから次へ進む（送信中の追記を取りこぼさない）。

### 7.6 ジョブ完了の競合

- `pollJob` は開始時の `jobId` をローカルに保持し、`await` 後に `jobId !== myJob` なら**何もしない**（キャンセル・画面移動後の遅延反映を防ぐ）。
- **`catch` でも同じ世代チェックをしてから再スケジュール**する。`stopPoll()` は `jobTimer` を1本しか消せないため、これが無いと旧ジョブの失敗応答が新しいポーリング連鎖を作る。
- `renderQuestions` / `renderCreating` の再開は `last_job` を直接 `jobId` に入れず、**`resumeJob()`（`/job/{id}` で status 確認）を通す**。`resumePromise` で二重実行を防ぐ。これにより `!!jobId` を生存判定に使える（§7.2）。
- **`canceled` も終端**。`pollJob` は `jobId=""`・`stopPoll()` にして再スケジュールしない（別タブでキャンセルされた場合）。
- compose の done は**必ず** `settleTo("preview", returnTo)` してから分岐する（`autoCreateOnce` / `draft.confirm` / 即作成）。`creating` を履歴に残さない。
- `doCreate` が失敗して `preview` に戻す場合も `settleTo` を使う（`creating` を挟まない）。

### 7.7 IME

- 退避は「離脱時」に DOM を読むため、変換中に発火しない。
- 画面再描画は遷移時のみ。変換中の再描画は発生しない（`apiCall` の自動再描画を廃止するため）。

---

## 8. 失敗時のポリシー

| 失敗 | 戻る | 閉じる |
|---|---|---|
| 通信エラー / 5xx | 遷移する（`snap` 保持、トースト） | 閉じない → 再試行 or 破棄確認 |
| `rev` 409 | 1回再試行 → だめなら遷移（`snap` 保持） | 再試行 → だめなら破棄確認 |
| `wizard_busy` | ジョブキャンセル後に再試行（キャンセル成功後のみ遷移するので通常は発生しない） | ジョブ継続の注意を出して確認 |
| キャンセル失敗（通信/404） | **遷移しない**。同じ画面で再試行 | 閉じない（注意文） |
| `bad_compose`（preview） | 遷移する（`snap` 保持、トースト） | 「保存できない」＋破棄確認 |
| `bad_answer` 等 | 差分規則により発生しない | 同左 |

---

## 9. サーバー変更: `/api/wizard/job_cancel` の原子化と worker の再検証

現行は `job["cancel"] = True` のみで、ジョブが実際に止まるまで `wizard_job_running()` が真のまま → 直後の `/slots` `/tags` が `wizard_busy` で失敗する。

### 9.1 ルート

`draft_id` がある時だけ新挙動（旧クライアントは `job_id` だけなので従来どおり `bool` を返す＝後方互換）。

```python
if path_only == "/api/wizard/job_cancel":
    jid = str(body.get("job_id") or "")
    wid = str(body.get("draft_id") or "")
    with wizard_lock:
        job = wizard_jobs.get(jid)
        if job:
            job["cancel"] = True
            if job.get("status") in ("queued", "running"):
                job["status"] = "canceled"
        # draft_id が無い旧クライアント: 従来の応答だけ返す（rev を動かさない）
        if not wid:
            return h.send_json({"ok": bool(job)})
        jd = wizard_load(wid)          # 不正IDは ValueError → 400
        if jd and jd.get("last_job") == jid:
            jd["last_job"] = ""
            if jd.get("status") == "composing":
                has = bool((jd.get("compose") or {}).get("setting"))
                jd["status"] = "ready" if has else "interview"
                jd["step"] = "preview" if has else "tags"
            wizard_save(jd)            # rev を進め、走行中ジョブの書き戻しを弾く
        pub = wizard_public(jd, True) if jd else None
    return h.send_json({"ok": bool(jd), "canceled": bool(job), "draft": pub})
```

- ジョブが掃除済み（`wizard_jobs` に無い）でも、`draft_id` があれば `last_job` を照合してクリアする（冪等）。**404で遷移不能にしない。**
- `wizard_job_running()` は即座に偽になる。
- クライアントは返却 `draft` を必ず採用し、`status` / `compose` を見て遷移先を決める（§7.2）。

### 9.2 worker の再検証（必須）

`job_cancel` だけでは足りない。次の分岐は**キャンセル/ rev を再確認せずに保存**しており、キャンセル応答後に同じ rev で書き戻す（lost update）:

- `wizard_do_questions` の `not wizard_ai_enabled` / `done_questions` / 最大ラウンド分岐（`server.py:4259-4278`）
- `wizard_do_compose` の AI なし分岐の最終保存（`server.py:4344-4375`）

また、これらは**最初にロードした古い `d` を保持**したまま `wizard_save(d)` する。§9.1 が rev R→R+1 にしても worker の `d` は rev R のままなので、保存すると **rev は再び R+1** になり、`last_job` と status が巻き戻る。rev 検査では検出できない。

対策:

```python
def _wiz_job_recheck(job, d, expect_rev=None):
    """ロック内で必ず呼ぶ。古いオブジェクトを保存しないための関門。"""
    if not d or d.get("last_job") != job["job_id"]:
        raise WizardError("古いジョブです", code="stale", status=409)
    if job.get("cancel"):
        raise WizardError("キャンセルしました", code="canceled")
    if d.get("rev") != (job.get("base_rev") if expect_rev is None else expect_rev):
        raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)
```

- 保存の直前に `with wizard_lock: d = wizard_load(d["id"]); _wiz_job_recheck(job, d); ...; wizard_save(d)` を必ず置く（既存の `d` を再利用しない）。
- `run()` は開始時に `if j.get("status") == "canceled": return` し、`running` を無条件に立てない。
- `WizardError(code="canceled")` は `j["status"] = "canceled"`（error にしない）。
- compose 失敗時の `finally` は `status == "error"` の時だけ ready/interview に戻す（canceled を上書きしない）。

---

## 10. 実装手順（各コミットで動作する形）

1. **server**: `job_cancel`（`draft_id` 付きのみ新挙動）＋ **worker の全保存点の再検証**＋テスト。`draft_id` 無しは現行応答のままなので後方互換。
2. **index.html / wizard.css**: フッターへ静的 `#wizardBack` を移設（`hidden` のまま）。ヘッダーの `‹` を削除。
3. **wizard.js**: `navStack` / `navigate` / `backRender` / `canBack` / `enterScreen` / `chromeFor`。`show` を `navigate` に置換し、`open()` の開始を `enterScreen(start)` にする。`goBack` を履歴ベースへ（flush なし）。`closeModel`。
4. **wizard.js**: `flushScreen` 一式（idea / partner / tags / preview）と `snap`。`goBack` / `close` に接続。`renderSeq` / `formSeq` / `inert`。
5. **wizard.js**: `runAction` ガード、`apiCall` の rev 再描画廃止＋`initPendingTags`、タグ保存の直列化（`drainTagSaves`）、`pollJob` の世代チェック（catch 含む）、`settleTo`、`closeNow`。
6. **テスト**: 既存2件の stale 修正＋新規。`?v=` を更新。

---

## 11. テスト仕様

### 11.1 自動（追加）

`test/test_channel_wizard_ui.cjs`
- `index.html`: `#wizardBack` が `footer.wizard-foot` 内にあり、`header` 内に無い。
- `wizard.js`: `navStack` / `navigate` / `backRender` / `canBack` / `settleTo` が存在し、`show(` の直接呼び出しが残っていない。
- `runAction` ガード、`apiCall` に `renderPartner(` 等の再描画呼び出しが無い。
- `renderTags` が `pendingTags` を毎回初期化していない。
- `wizard.js?v=` と `wizard.css?v=` が増えている。
- 既存の stale 2件（`style.css?v=161` / `app.js?v=138`）を正規表現化。

スタックの純ロジック（`navigate` / `backRender` / `settleTo` / `enterScreen` / `canBack`）は、`window.__RP_WIZARD_TEST__` が真のときだけ `window.RPWizard.__test` に公開し、`vm` で遷移表を検証する（本番の公開 API は変えない）。

`test/test_channel_wizard_ui.cjs`（挙動）
1. `close(true)` が `busy` 中でも閉じる（`doCreate` 成功経路）。
2. partner 描画待ちに戻っても `readPartnerForm` が例外を出さず、親画面を上書きしない（`renderSeq`）。
3. model 描画待ちの `setFoot` が親画面のフッターを上書きしない。
4. `work` 変更後の退避で `partner_name` / `start_point` / `canon_fidelity` が残る（`PARTNER_INVALIDATES` の再送）。
5. カスタムNGだけ変更 → 戻る／閉じるで `/tags` が呼ばれる。
6. タグ保存中に追加したタグが、次へ・閉じるで失われない（`drainTagSaves`）。
7. 保存中に追記 → `formSeq` 検出で遷移しない（`inert` 非対応の想定）。
8. 退避失敗 → 次へで正常保存 → 閉じる、で古い `snap` が再送されない。
9. キャンセル失敗（`ok:false`）で遷移せず `jobId` が残る。
10. `last_job` だけがある完了済みジョブでキャンセル確認が出ない。
11. questions で選択肢だけ選んだ状態でも破棄確認が出る。
12. 再開の全entryで `enterScreen` の合成スタックが `ENTRY_STACK` と一致する。
13. `pollJob` の catch が旧jobで再スケジュールしない。

`test/test_wizard_server.py`
- `job_cancel`（`draft_id` 付き）が `last_job` を空にし、`status` を `ready`/`interview` にし、rev を進める。
- キャンセル後に古い `rev` で `/slots` → 409 `rev`。
- キャンセル後に `wizard_job_running()` が偽。
- **掃除済みジョブ + `draft_id`** → `ok:true`、`last_job` クリア（冪等）。
- **`draft_id` 無し**（旧クライアント）→ `{"ok": bool}` のみ、rev 不変。
- **AIなしcomposeの最終保存直前キャンセル** → compose が保存されず、`last_job` が復活しない（同一rev lost update の回帰）。
- **AIなしquestionsの done 分岐キャンセル** → 同上。
- `run()` が `canceled` を `running`/`error` で上書きしない。

### 11.2 手動（ブラウザ）

1. 初回: base→idea→partner→tags→preview で各画面の「戻る」が左に出る。
2. partner に名前・設定を入力 → 戻る → 進む → **値が残る**。
3. preview を編集 → 戻る → 進む → 編集が残る。
4. 3/7 で戻る → **2/7 に飛ばない**（base へ）。model をチップで開いた時だけ model へ戻る。
5. model で戻る → 元の画面（ループしない）。
6. 質問生成中に戻る → 確認 → tags に戻り、直後のタグ操作が成功する。
7. ✕ / Esc / 背景クリック → 確認 → 再オープンで入力が残る。
8. 次へ連打 → チャンネルは1つだけ。
9. 375px 幅でフッターが崩れない。
10. 生成中に閉じる → 注意文が出る。
11. 作成成功 → ウィザードが自動で閉じ、新チャンネルが開く（`closeNow`）。
12. partner 到着直後（プリセット読込中）は戻るが disabled。描画完了後に戻っても入力が消えない。
13. カスタムNGだけ入力 → 戻る → 進む → 残る。
14. 質問で選択肢だけ選んで戻る → 破棄確認が出る。

### 11.3 既知の回帰確認

- `base_locked` の画面（戻る→base）で「この設定で続ける」が動く。
- 空のチャンネル / AIなし / テンプレート compose / create 失敗 → preview 復帰。
- 質問2ラウンド目、`q-finish`、`tags-finish`、`recompose`。

---

## 12. 仕様レビュー: 見つけた落とし穴と対策

| # | 落とし穴 | 対策（本仕様での扱い） |
|---|---|---|
| 1 | `/slots` `/tags` は無変更でも `compose_stale=True` → 戻るだけでプレビューが破棄される | **差分がある時だけ送る**（I5, §6） |
| 2 | partner の `auto` を退避保存すると全消し | `auto` は送らない（§6.2） |
| 3 | `player_type`/`canon` は初期表示が既定値で、未操作でも差分扱い | 「触った」フラグでのみ送る（§6.2） |
| 4 | 空文字は `user` で保存できない | 空は `auto`（＝おまかせ）として送る（§6.2） |
| 5 | `/slots` が `partner_preset_id` を消す | プリセットは変更時のみ、`/slots` の**後**に送る（§6.2） |
| 6 | preview の検証失敗で戻れなくなる | 戻るは遷移優先＋`snap` 保持。閉じる時だけ確認（§6.4, §8） |
| 7 | タグ保存中に増えたトグルが応答で消える | 直列化＋差分マージ＋dirty 再送（§7.5） |
| 8 | デバウンスが離脱後に発火 | 離脱時に `clearTimeout`＋await（§6.3） |
| 9 | 次へ連打で二重送信（既存） | `runAction` ガード（§7.1） |
| 10 | 戻る連打 | 同上＋pop 後の再描画のみ |
| 11 | `creating` が履歴に残り、戻るで再ジョブ | 決着時に `settleTo` で除去（I3, §4.4） |
| 12 | model の「保存して次へ」が常に idea へ（ループ／履歴破壊） | `closeModel()` で親へ（§4.4） |
| 13 | 再開直後に戻り先が無い | 合成スタック表（§4.3） |
| 14 | `done` の戻るが無反応（既存） | 非表示（§5.3） |
| 15 | キャンセル直後の保存が `wizard_busy` | サーバーで原子的キャンセル（§9） |
| 16 | キャンセルと完了の競合 | 返却 `draft` で分岐（§7.2） |
| 17 | 409 の全画面再描画で入力消失 | 再描画廃止＋`snap`＋1回再試行（§7.4） |
| 18 | 閉じる確認文が虚偽 | 実態に合わせる＋失敗時の二次確認（§7.3） |
| 19 | `open()` で一時状態が残る | I8 で全クリア |
| 20 | `pollJob` の遅延応答がキャンセル後に反映 | 世代チェック（§7.6） |
| 21 | `?v=` を上げ忘れて旧JSが残る | リリース時に `wizard.js?v=2` / `wizard.css?v=2` |
| 22 | フッターの折り返し | 360/375px で確認、`row-gap`（§5.2） |
| 23 | 再描画でフォーカス消失 | 画面遷移時のみタイトルへ（§5.4） |
| 24 | Esc/背景クリックが flush を待たない | すべて `close()` に集約（§7.3） |
| 25 | エラー画面で `setFoot` が更新されず古いフッターが残る（既存） | エラー表示時も `setFoot("")` を呼ぶ |
| 26 | `persistStep` と flush の rev 順序 | flush → `render`（`persistStep`）の順 |
| 27 | `recompose` 後に古い preview 編集が残る | `delete snap.preview` |
| 28 | 再開時 `creating` で死んだジョブを無限ポーリング（既存） | `resumeJob()` の `/job/{id}` status 確認で解消（§7.6） |
| 29 | ユーザーの `close` がガードを通らず Esc 連打で二重 flush | `close` は `runAction("close")`＋`busy/pending/rendering` ガード。`force` は `closeNow` へ分離（§7.3） |
| 30 | `settleTo` を通らない `doCreate` 失敗で `creating` が履歴に残る | 失敗時の `preview` 復帰も `settleTo` |
| 31 | model 保存後、親が `base` だとロック画面へ戻ってしまう | 親が `base`／ルートなら `idea` へ（`closeModel`） |
| 32 | 親の `justify-content:flex-end` が残り、戻るが右へ寄る | 親は `flex-start`、右寄せは子の `margin-left:auto` |
| 33 | タグ保存の開始時に `tagDirty=false` にすると、失敗時に dirty が消える | 成功後に「送った値 vs 現在値」で再計算（§7.5） |
| 34 | `draft.last_job` は完了後も残る履歴ポインタ。生存判定に使うと完了済みで誤確認 | `!!jobId`（開始成功 or `/job/{id}` 確認済み）だけを生存判定に使う（§7.2, I9） |
| 35 | 退避に失敗した別画面の `snap` を閉じる時に捨てる | 閉じる時に `snap` を順に再送（§7.3） |
| 36 | `preview` の `snap` は差分なので、そのまま描画すると欠ける | `setting` へネストマージして描画（§6.4） |
| 37 | `navigate` を `screen` 比較にすると、`settleTo` 直後にスタック先頭と `screen` がズレて I1 が壊れる | **スタック先頭と比較**する（§4.1） |
| 38 | 閉じる時、現在画面の成功後に古い `snap[screen]` を再送して新しい値を上書きする | 成功時に `delete snap[screen]`、ループでは `name === screen` を除外 |
| 39 | `snap.tags` があるのに `tagDirty` が偽だと再送されない | `snapValues` 指定時は dirty 判定をバイパス（§6.3） |
| 40 | `jobId` を持ったまま `creating` / `questions` を再入すると、ポールも描画もされずスピナーで固まる（既存） | I9: 他画面へ抜ける時は必ず `jobId` をクリア |
| 41 | `close(true)` が `busy`/`pending` ガードで拒否され、作成後に閉じない | `force` は `closeNow()` へ直行（§7.3） |
| 42 | 非同期 `renderPartner`/`renderModel` の await 後に古い画面を描く | `renderSeq` 世代チェック＋`rendering` で戻るを抑止（§4.1） |
| 43 | 描画待ちの `readPartnerForm` が `null.querySelector` で落ちる | `segBox` ガード＋draft フォールバック（§6.7） |
| 44 | 親スロット変更で子が消え、次へと結果が違う | `PARTNER_INVALIDATES` の子を patch に再送（§6.2） |
| 45 | `tagDirty` を立てる箇所がなく、戻るでタグ/カスタムNGが保存されない | `scheduleTagSave` で dirty（§6.3） |
| 46 | `tagTimer` が発火後も残り、毎回 `/tags` を送って `compose_stale` | 発火時に `tagTimer=null`（§6.3） |
| 47 | 保存中の本文編集が成功扱いで捨てられる | `inert` ＋ `formSeq` 再送（§6.1, §7.1） |
| 48 | 古い `snap` が後続の正常保存を上書き | 成功時に `delete snap[X]`（§7.4） |
| 49 | キャンセル失敗でも遷移して `wizard_busy` 連鎖 | `ok` の時だけ遷移（§7.2, §8） |
| 50 | AIなし分岐が古い `d` を同一revで保存し、`last_job` を復活 | `_wiz_job_recheck` を全保存点に（§9.2） |
| 51 | `apiCall` の409で `pendingTags=null` のまま再描画を外すとタグ操作が落ちる | `initPendingTags()` に置換（§7.4） |
| 52 | §7.4の `snap.partner?.[k]` は形が違う | `snap.partner.values[k]` 等（§6.1） |
| 53 | 再開composeで `returnTo` 未設定 | 再開時も `returnTo` を計算（§7.6） |
| 54 | `close(true)` が作成済みに `/step` を送って409 | `status==="created"` は送らない（§7.3） |
| 55 | `pollJob` が `canceled` を終端扱いせず、別タブのキャンセル後も回り続ける | `canceled` で `jobId=""`・`stopPoll()`（§7.6） |

---

## 13. 未解決の判断事項（⚠ 実装前に決める）

1. **閉じる時のジョブ扱い**: 継続（現行）か中止か。本仕様は「継続＋注意文」。中止にすると `close` がキャンセル確認を追加する。
   - 注意: 継続の場合、`!confirm` の自動作成はサーバー側で行われないため、閉じたままではチャンネルが完成しない（P4 の既知課題）。
2. **partner の「おまかせ」ボタン**: 本仕様では温存（ただし戻るでは発動しない）。撤去するなら別チケット。
3. **model 変更の破棄**: 戻るで破棄してよいか（グローバル設定のため）。保存にすると他チャンネルへ影響する。
4. **`base` を戻り先に出すか**: 本仕様は再開時に出さない（`base_locked` の行き止まり回避）。

### 決定済み（今回のレビューで確定）

5. **`INVALIDATES` の子は退避で保持する**（§6.2）。「次へ」と同じ結果にし、無言の消失を防ぐ。自動無効化を優先したい場合は、この決定を覆して `buildPartnerPatch` の `preserve` を外す。
6. **questions / model の破棄は確認文で明示する**（§7.3）。目的2は保存対象画面に限定した（§1）。
7. **`force` close は内部専用**（`doCreate` 成功後・`open-created`・`empty`）。公開 `RPWizard.close(true)` は互換のため残すが、UI からは呼ばない。
8. **`job_cancel` は `draft_id` 付きのみ新挙動**（§9.1）。旧クライアントは rev を動かさないので後方互換。
9. **`jobId` を生存判定の唯一の根拠にする**（§7.2, I9）。`draft.last_job` は履歴としてのみ使う。

---

## 14. 同梱候補（独立コミット・1〜2行）

- `#wizTagWarn`（空のまま常時描画されるオレンジ帯）を削除、または `.wizard-warn:empty{display:none}`。
- 進捗の分母からスキップされる `model` を除外（`1/7 → 3/7` の飛びを解消）。
- `close()` の step 保存を `closeNow()` に一本化（`status==="created"`・ジョブ中・`draft.step===screen` は送らない）。
