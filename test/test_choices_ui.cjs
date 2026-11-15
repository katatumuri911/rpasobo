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

console.log("ALL choices UI tests PASSED!");
