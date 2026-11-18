// 選択肢 UI (choicesNoticeText / choicesBox / hasChoices) の単体テスト (スタブ DOM)
// 実行: node test/test_choices_ui.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

// 抽出: choicesNoticeText と choicesBox
const s0 = src.indexOf("function choicesNoticeText(n) {");
const s1 = src.indexOf("\n// ---------------------------------------------------------------- キャラの状態");
if (s0 < 0 || s1 < 0 || s1 < s0) throw new Error("choicesBox / choicesNoticeText モジュールを抽出できません");
const moduleSrc = src.slice(s0, s1);

// スタブ DOM
class StubEl {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.textContent = "";
    this.title = "";
    this.style = {};
    this.attrs = {};
    this.listeners = {};
    this.onclick = null;
    const self = this;
    const set = (str) => new Set(String(str || "").split(/\s+/).filter(Boolean));
    this.classList = {
      add: (...c) => { const s = set(self.className); c.forEach((x) => s.add(x)); self.className = [...s].join(" "); },
      remove: (...c) => { const s = set(self.className); c.forEach((x) => s.delete(x)); self.className = [...s].join(" "); },
      toggle: (c, v) => {
        const s = set(self.className);
        const force = v !== undefined ? !!v : !s.has(c);
        if (force) s.add(c); else s.delete(c);
        self.className = [...s].join(" ");
        return force;
      },
      contains: (c) => set(self.className).has(c),
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) {
    if (!c) return c;
    if (c.isFragment) {
      c.children.forEach((child) => this.appendChild(child));
      c.children = [];
      return c;
    }
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  remove() {
    if (this.parentNode && Array.isArray(this.parentNode.children)) {
      this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
      this.parentNode = null;
    }
  }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  querySelectorAll(sel) {
    const res = [];
    const check = (el) => {
      if (sel.startsWith(".") && el.classList && el.classList.contains(sel.slice(1))) res.push(el);
      else if (sel.toLowerCase() === el.tagName.toLowerCase()) res.push(el);
      el.children.forEach(check);
    };
    this.children.forEach(check);
    return res;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
}

class StubFrag {
  constructor() {
    this.isFragment = true;
    this.children = [];
  }
  appendChild(c) { this.children.push(c); return c; }
}

const sandbox = {
  console,
  document: {
    createElement: (t) => new StubEl(t),
    createDocumentFragment: () => new StubFrag(),
    getElementById: () => null,
    querySelectorAll: () => [],
  },
  $: () => null,
  displayChoicesMode: new Map(),
  openChoicesSet: new Set(),
  userClosedChoicesSet: new Set(),
  selectedChoiceMap: new Map(),
  lastSelectedChoiceText: "",
  render: () => {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(moduleSrc, sandbox, { filename: "app.js#choices" });

const { choicesNoticeText, choicesBox } = sandbox;
if (typeof choicesNoticeText !== "function" || typeof choicesBox !== "function") {
  throw new Error("関数 choicesNoticeText / choicesBox のロードに失敗しました");
}

console.log("--- 1. choicesNoticeText のテスト (#3) ---");
// 1-1: erotic notices
assert.strictEqual(choicesNoticeText({ kind: "filter", slot: "erotic" }), "🔞 フィルタで1回停止 → 自動で回避して出力");
assert.strictEqual(choicesNoticeText({ kind: "suspect", slot: "erotic" }), "🔞 の候補: 健全な内容に差し替わった可能性あり");
assert.strictEqual(choicesNoticeText({ kind: "placeholder", slot: "erotic" }), "🔞 の候補: プレースホルダーを一部自動置換しました");
assert.strictEqual(choicesNoticeText({ kind: "timeout", slot: "erotic" }), "🔞 の候補: 生成がタイムアウト（応答が届いていません）");
assert.strictEqual(choicesNoticeText({ kind: "rescued", slot: "erotic" }), "🔞 の候補: 生成結果を回収しました（.tmp から復元）");

// 1-2: normal notices
assert.strictEqual(choicesNoticeText({ kind: "filter", slot: "normal" }), "💬 フィルタで1回停止 → 自動で回避して出力");
assert.strictEqual(choicesNoticeText({ kind: "suspect", slot: "normal" }), "💬 の候補: 健全な内容に差し替わった可能性あり");
assert.strictEqual(choicesNoticeText({ kind: "placeholder", slot: "normal" }), "💬 の候補: プレースホルダーを一部自動置換しました");
assert.strictEqual(choicesNoticeText({ kind: "timeout", slot: "normal" }), "💬 の候補: 生成がタイムアウト（応答が届いていません）");
assert.strictEqual(choicesNoticeText({ kind: "rescued", slot: "normal" }), "💬 の候補: 生成結果を回収しました（.tmp から復元）");
console.log("  PASS: choicesNoticeText 全スロット・全種別");

console.log("--- 2. choicesBox: 選択肢ゼロ時のタイムアウト / 通知表示 (#8) ---");
// 2-1: 選択肢ゼロ＋choices_timeout: true
const mTimeout = {
  id: "msg_timeout_1",
  role: "assistant",
  choices: [],
  choices_erotic: [],
  choices_timeout: true,
  choices_slot: "erotic",
};
const boxTimeout = choicesBox(mTimeout);
assert.strictEqual(boxTimeout.isFragment, undefined, "Fragment ではなく container を返すべき");
const noticeEl = boxTimeout.querySelector(".choices-notice");
assert(noticeEl, ".choices-notice 要素が存在すること");
assert.strictEqual(noticeEl.textContent, "🔞 の候補: 生成がタイムアウト（応答が届いていません）");

// 2-2: 選択肢ゼロ＋choices_notice (suspect)
const mSuspect = {
  id: "msg_suspect_1",
  role: "assistant",
  choices: [],
  choices_erotic: [],
  choices_notice: { kind: "suspect", slot: "erotic" },
};
const boxSuspect = choicesBox(mSuspect);
const suspectNoticeEl = boxSuspect.querySelector(".choices-notice");
assert(suspectNoticeEl, "suspect の .choices-notice 要素が存在すること");
assert.strictEqual(suspectNoticeEl.textContent, "🔞 の候補: 健全な内容に差し替わった可能性あり");
assert(suspectNoticeEl.classList.contains("is-warn"), "suspect には is-warn クラスが付与されること");

// 2-3: 選択肢ゼロ＋通知なし
const mEmpty = {
  id: "msg_empty_1",
  role: "assistant",
  choices: [],
  choices_erotic: [],
};
const boxEmpty = choicesBox(mEmpty);
assert(boxEmpty.isFragment || boxEmpty.children.length === 0, "通知も選択肢も無ければ空フラグメントを返すこと");
console.log("  PASS: choicesBox ゼロ件表示・早期 return");

console.log("--- 3. choicesBox: 婉曲表現の自動復元 (#decensor) ---");
// 3-1: choices_decensor があっても復元行・戻すボタンが出ず、候補パネル本体は出ること
const mDec = {
  id: "msg_dec_1",
  role: "assistant",
  choices: [{ label: "a", text: "b" }],
  choices_erotic: [],
  choices_decensor: {
    slot: "normal",
    applied: [
      { from: "秘所", to: "おまんこ", count: 2 },
      { from: "膣", to: "ナカ", count: 1 },
    ],
    target_ts: "t",
  },
};
const boxDec = choicesBox(mDec);
assert.strictEqual(boxDec.querySelector(".choices-notice"), null, "choices_decensor があっても .choices-notice は出ないこと");
assert.strictEqual(boxDec.querySelector(".choices-undo-btn"), null, ".choices-undo-btn は出ないこと");
assert(boxDec.querySelector(".choices-panel"), "候補本体（.choices-panel）は従来どおり出ること");

// 3-2: choices: [] で choices_decensor だけのメッセージでは空フラグメントを返すこと
const mDecOnly = {
  id: "msg_dec_only",
  role: "assistant",
  choices: [],
  choices_erotic: [],
  choices_decensor: {
    slot: "normal",
    applied: [{ from: "秘所", to: "おまんこ", count: 1 }],
  },
};
const boxDecOnly = choicesBox(mDecOnly);
assert(boxDecOnly.isFragment || boxDecOnly.children.length === 0, "choices: [] で choices_decensor だけなら空フラグメントを返すこと");
console.log("  PASS: 復元行・戻すボタンが出ない");

console.log("--- 4. チャンネル既定チップ（💬既定 / 🔞既定）と保存経路 ---");
(async () => {
  const chipMsg = (id, extra) => Object.assign({
    id, role: "assistant", choices: [{ label: "a", text: "b" }], choices_erotic: [],
  }, extra || {});

  // 4-1: 既定オフでもチップは常に出る（今まではオン時しかバッジが出なかった）
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: true } };
  const boxOff = choicesBox(chipMsg("msg_chip_off"));
  const chipOff = boxOff.querySelector(".choices-auto-chip");
  assert(chipOff, "既定オフでも .choices-auto-chip が出ること");
  assert.strictEqual(chipOff.querySelector(".choices-auto-ic").textContent, "💬");
  assert.strictEqual(chipOff.querySelector(".choices-auto-lb").textContent, "既定");
  assert.strictEqual(chipOff.getAttribute("role"), "switch");
  assert.strictEqual(chipOff.getAttribute("aria-checked"), "false");
  assert.strictEqual(chipOff.getAttribute("aria-label"), "このチャンネルは💬既定");
  assert.strictEqual(chipOff.disabled, false);
  assert(!chipOff.classList.contains("is-erotic"), "オフでは is-erotic を付けない");

  // 4-2: チップはスイッチの左（A案の並び）
  const headRow = boxOff.querySelector(".choices-head-row");
  assert(headRow, ".choices-head-row が取れること");
  const iChip = headRow.children.indexOf(chipOff);
  const iSw = headRow.children.findIndex((c) => c.classList && c.classList.contains("choices-switch"));
  assert(iChip >= 0 && iSw >= 0 && iChip < iSw, "チップはスイッチの左に並ぶこと");

  // 4-3: 既定オンなら 🔞既定 / is-erotic
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: true, suggest_choices: true } };
  const chipOn = choicesBox(chipMsg("msg_chip_on")).querySelector(".choices-auto-chip");
  assert.strictEqual(chipOn.querySelector(".choices-auto-ic").textContent, "🔞");
  assert.strictEqual(chipOn.getAttribute("aria-checked"), "true");
  assert.strictEqual(chipOn.getAttribute("aria-label"), "このチャンネルは🔞既定");
  assert(chipOn.classList.contains("is-erotic"));

  // 4-4: 生成中は押せない（スイッチと同じ条件）
  const chipBusy = choicesBox(chipMsg("msg_chip_busy", { choices_status: "pending" })).querySelector(".choices-auto-chip");
  assert.strictEqual(chipBusy.disabled, true);
  assert.strictEqual(chipBusy.title, "生成中です");

  // 4-5: 8択の生成そのものがオフなら押せない（理由を title に出す）
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: false } };
  const chipS8 = choicesBox(chipMsg("msg_chip_s8")).querySelector(".choices-auto-chip");
  assert.strictEqual(chipS8.disabled, true);
  assert(chipS8.title.includes("8択"), "title に 8択 がオフである理由を出すこと");

  console.log("  PASS: チップの常時表示・並び・aria・disabled");

  // 4-6: 保存経路（設定パネルと共通）とトースト文言
  const toasts = [];
  sandbox.toast = (msg, opts) => toasts.push([msg, opts || {}]);
  sandbox.post = async () => ({ ok: true });
  sandbox.poll = async () => {};
  sandbox.flash = (msg) => toasts.push(["flash:" + msg, {}]);
  const lastToast = () => toasts[toasts.length - 1];
  const setAuto = sandbox.setChoicesEroticAuto;
  assert.strictEqual(typeof setAuto, "function", "setChoicesEroticAuto がテストの抽出範囲に入っていること");

  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: true, choices_ide_session: "c", ide_session: "m" } };
  assert.strictEqual(await setAuto(true), true, "オンで保存できること");
  assert.strictEqual(sandbox.state.setting.choices_erotic_auto, true, "state.setting に反映されること");
  assert.strictEqual(lastToast()[0], "次のターンから🔞選択肢のみ作成");
  assert.strictEqual(lastToast()[1].dedupe, false, "連打しても鳴るよう dedupe:false を渡すこと");

  assert.strictEqual(await setAuto(false), true, "オフで保存できること");
  assert.strictEqual(sandbox.state.setting.choices_erotic_auto, false);
  assert.strictEqual(lastToast()[0], "次のターンから💬選択肢のみ作成");

  // 4-7: 8択の生成がオフならオンにできない（設定は変わらない）
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: false } };
  assert.strictEqual(await setAuto(true), false);
  assert.strictEqual(sandbox.state.setting.choices_erotic_auto, false, "失敗時は設定を変えないこと");
  assert(lastToast()[0].startsWith("flash:"));

  // 4-8: 選択肢担当の会話が未設定なら効かないことを伝える
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: true } };
  assert.strictEqual(await setAuto(true), true);
  assert(lastToast()[0].includes("選択肢担当の会話"), "並列先読みが効かないことを警告すること");

  // 4-9: state が無くても落ちない（単体テスト環境・セッション切替直後）
  sandbox.state = undefined;
  assert.strictEqual(await setAuto(true), false);
  const chipNoState = choicesBox(chipMsg("msg_chip_nostate")).querySelector(".choices-auto-chip");
  const p = chipNoState.listeners.click[0]({ stopPropagation() {} });
  assert(p && typeof p.then === "function", "click ハンドラは Promise を返すこと");
  await p;

  console.log("  PASS: 保存経路・トースト文言・失敗時・state 無し");

  // 4-10: 押した瞬間に見た目が切り替わる（保存の完了を待たない）
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: true, choices_ide_session: "c", ide_session: "m" } };
  let releasePost;
  sandbox.post = () => new Promise((res) => { releasePost = () => res({ ok: true }); });
  const chipPaint = choicesBox(chipMsg("msg_chip_paint")).querySelector(".choices-auto-chip");
  const pPaint = chipPaint.listeners.click[0]({ stopPropagation() {} });
  // まだ await していない（＝POST が返っていない）時点で見た目が変わっていること
  assert(chipPaint.classList.contains("is-erotic"), "押した直後に is-erotic が付くこと");
  assert.strictEqual(chipPaint.querySelector(".choices-auto-ic").textContent, "🔞");
  assert.strictEqual(chipPaint.getAttribute("aria-checked"), "true");
  assert.strictEqual(chipPaint.getAttribute("aria-label"), "このチャンネルは🔞既定");
  releasePost();
  await pPaint;
  assert.strictEqual(sandbox.state.setting.choices_erotic_auto, true, "保存もされること");

  // 4-11: 保存に失敗したら見た目を元に戻す
  sandbox.state = { id: "s1", setting: { choices_erotic_auto: false, suggest_choices: true, choices_ide_session: "c", ide_session: "m" } };
  sandbox.post = async () => { throw new Error("boom"); };
  const chipFail = choicesBox(chipMsg("msg_chip_fail")).querySelector(".choices-auto-chip");
  await chipFail.listeners.click[0]({ stopPropagation() {} });
  assert(!chipFail.classList.contains("is-erotic"), "失敗したら is-erotic を外すこと");
  assert.strictEqual(chipFail.querySelector(".choices-auto-ic").textContent, "💬");
  assert.strictEqual(chipFail.getAttribute("aria-checked"), "false");
  assert.strictEqual(sandbox.state.setting.choices_erotic_auto, false, "失敗時は設定を変えないこと");

  console.log("  PASS: 押した瞬間に見た目が切り替わる（楽観更新・失敗時は戻す）");

  console.log("--- 5. 選択肢クリック時の peekAgQuota(1000) 呼び出しと入力欄への反映 ---");
  let inputVal = "";
  let focused = false;
  let dispatchedEvents = [];
  const fakeInput = {
    get value() { return inputVal; },
    set value(v) { inputVal = v; },
    dispatchEvent(e) { dispatchedEvents.push(e.type); },
    focus() { focused = true; },
    setSelectionRange(s, e) {},
  };
  sandbox.$ = (id) => (id === "messageInput" ? fakeInput : null);
  sandbox.autoResizeInput = () => {};
  sandbox.Event = class Event { constructor(t) { this.type = t; } };
  let peekAgQuotaDuration = null;
  sandbox.peekAgQuota = (d) => { peekAgQuotaDuration = d; };

  const mChoiceClick = {
    id: "msg_choice_click",
    role: "assistant",
    choices: [
      { label: "1", text: "選択肢テキスト1" },
      { label: "2", text: "選択肢テキスト2" },
    ],
    choices_erotic: [],
  };
  const boxChoice = choicesBox(mChoiceClick);
  const choiceBtns = boxChoice.querySelectorAll(".choice-item-btn");
  assert.strictEqual(choiceBtns.length, 2, "選択肢ボタンが2つ生成されること");

  // 1番目の選択肢をクリック
  peekAgQuotaDuration = null;
  focused = false;
  dispatchedEvents = [];
  choiceBtns[0].listeners.click[0]();

  assert.strictEqual(inputVal, "選択肢テキスト1", "入力欄に選択肢テキストが入ること");
  assert(dispatchedEvents.includes("input"), "input イベントが発火すること");
  assert.strictEqual(focused, true, "入力欄が focus されること");
  assert.strictEqual(choiceBtns[0].classList.contains("is-selected"), true, "クリックしたボタンに is-selected が付くこと");
  assert.strictEqual(choiceBtns[1].classList.contains("is-selected"), false, "他方のボタンには is-selected が付かないこと");
  assert.strictEqual(peekAgQuotaDuration, 1000, "peekAgQuota(1000) が呼ばれること");

  // 2番目の選択肢をクリック
  peekAgQuotaDuration = null;
  choiceBtns[1].listeners.click[0]();
  assert.strictEqual(inputVal, "選択肢テキスト2", "入力欄に2番目の選択肢テキストが入ること");
  assert.strictEqual(choiceBtns[0].classList.contains("is-selected"), false, "1番目から is-selected が外れること");
  assert.strictEqual(choiceBtns[1].classList.contains("is-selected"), true, "2番目に is-selected が付くこと");
  assert.strictEqual(peekAgQuotaDuration, 1000, "再度 peekAgQuota(1000) が呼ばれること");

  console.log("  PASS: 選択肢クリックで入力欄更新・focus・peekAgQuota(1000) 呼び出し");

  console.log("ALL choices UI tests PASSED!");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
