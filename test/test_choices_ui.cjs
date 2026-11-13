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
// 3-1: 復元したことを知らせる行と「戻す」ボタン
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
const decEl = boxDec.querySelector(".choices-notice");
assert(decEl, "復元の .choices-notice 要素が存在すること");
assert(decEl.classList.contains("is-ok"), "復元の行は is-ok クラスを持つこと");
assert.strictEqual(decEl.textContent, "💬 の候補: 婉曲表現を3か所 自動で戻しました");
assert(decEl.title.includes("秘所 → おまんこ"), "内訳は title に入ること");
assert(boxDec.querySelector(".choices-undo-btn"), "「戻す」ボタンが付くこと");

// 3-2: スロットが違うときは出さない（🔞側を見ているのに💬の復元記録を出さない）
const mDecOther = { ...mDec, id: "msg_dec_2", choices_decensor: { ...mDec.choices_decensor, slot: "erotic" } };
const boxDecOther = choicesBox(mDecOther);
assert(!boxDecOther.querySelector(".choices-notice"), "別スロットの復元記録は表示しない");
console.log("  PASS: 復元の通知行と戻すボタン");

console.log("--- 4. textDecensorLine: 本文復元の通知行と戻すボタン ---");
const sDec0 = src.indexOf("function textDecensorLine(");
const sDec1 = src.indexOf("function createMessageWrap(");
if (sDec0 < 0 || sDec1 < 0 || sDec1 <= sDec0) {
  throw new Error("textDecensorLine モジュールを抽出できません");
}

(async () => {
  const decSrc = src.slice(sDec0, sDec1);
  let apiCalls = [];
  let pollCalled = false;
  let alertMessages = [];

  const context = {
    document: {
      createElement: (tag) => new StubEl(tag),
    },
    api: async (path, body) => {
      apiCalls.push({ path, body });
      return { ok: true };
    },
    poll: async () => {
      pollCalled = true;
    },
    alert: (msg) => {
      alertMessages.push(msg);
    },
  };
  vm.runInNewContext(decSrc, context);

  // 4-1: DOM 生成と件数集計
  const m1 = {
    id: "msg_body_1",
    role: "assistant",
    text_decensor: {
      applied: [
        { from: "突起", to: "乳首", count: 2 },
        { from: "尻肉", to: "尻たぶ", count: 1 },
      ],
    },
  };
  const wrap = new StubEl("div");
  const el1 = context.textDecensorLine(m1, { id: "s1" });
  wrap.appendChild(el1);

  assert.strictEqual(el1.className, "msg-notice");
  assert.strictEqual(el1.textContent, "婉曲表現を3か所 自動で戻しました");
  assert(el1.title.includes("突起 → 乳首（2）"));
  const btn1 = el1.querySelector(".choices-undo-btn");
  assert(btn1, "「戻す」ボタンが付くこと");
  assert.strictEqual(btn1.textContent, "戻す");
  assert(btn1.title.includes("戻します"), "ボタンに説明ツールチップが付くこと");

  // 4-2: 成功時のクリック（API送信・ローカル即時反映・poll同期）
  await btn1.onclick({ stopPropagation: () => {} });
  assert.strictEqual(btn1.disabled, true, "クリック時に disabled 化されること");
  assert.strictEqual(apiCalls.length, 1);
  assert.strictEqual(apiCalls[0].path, "/api/message/undo-decensor");
  assert.strictEqual(apiCalls[0].body.sid, "s1");
  assert.strictEqual(apiCalls[0].body.id, "msg_body_1");
  assert.strictEqual(apiCalls[0].body.kind, "text");
  assert.strictEqual(m1.text_decensor, null, "成功時にローカルの text_decensor がクリアされること");
  assert.strictEqual(el1.parentNode, null, "成功時に通知行が wrap から削除されること");
  assert.strictEqual(pollCalled, true, "成功時に poll() が呼ばれること");

  // 4-3: 404時（記録切れ・ゾンビボタン）: 静かにボタンのみ消去、alert なし
  context.api = async () => ({ error: "戻せる置換がありません" });
  const m2 = {
    id: "msg_body_2",
    role: "assistant",
    text_decensor: { applied: [{ from: "突起", to: "乳首", count: 1 }] },
  };
  const el2 = context.textDecensorLine(m2, { id: "s1" });
  wrap.appendChild(el2);
  const btn2 = el2.querySelector(".choices-undo-btn");
  alertMessages = [];
  await btn2.onclick();
  assert.strictEqual(btn2.parentNode, null, "404時にボタンが DOM から消去されること");
  assert.strictEqual(el2.parentNode, wrap, "404時も通知行自体は残ること");
  assert.strictEqual(alertMessages.length, 0, "404時に alert は抑制されること");

  // 4-4: 409時（編集済み競合）: ボタン消去 ＋ alert 表示
  context.api = async () => ({ error: "本文が編集されているので戻せません" });
  const m3 = {
    id: "msg_body_3",
    role: "assistant",
    text_decensor: { applied: [{ from: "突起", to: "乳首", count: 1 }] },
  };
  const el3 = context.textDecensorLine(m3, { id: "s1" });
  wrap.appendChild(el3);
  const btn3 = el3.querySelector(".choices-undo-btn");
  alertMessages = [];
  await btn3.onclick();
  assert.strictEqual(btn3.parentNode, null, "409時にボタンが DOM から消去されること");
  assert.strictEqual(alertMessages.length, 1, "409時は alert が出ること");
  assert(alertMessages[0].includes("本文が編集されているので戻せません"));

  // 4-5: 通信例外発生時: クラッシュせず安全にボタン消去
  context.api = async () => { throw new Error("fetch failed"); };
  const m4 = {
    id: "msg_body_4",
    role: "assistant",
    text_decensor: { applied: [{ from: "突起", to: "乳首", count: 1 }] },
  };
  const el4 = context.textDecensorLine(m4, { id: "s1" });
  wrap.appendChild(el4);
  const btn4 = el4.querySelector(".choices-undo-btn");
  await btn4.onclick();
  assert.strictEqual(btn4.parentNode, null, "例外時にボタンが安全に消去されること");

  console.log("  PASS: textDecensorLine 通知行生成・件数集計・undo実行・ゾンビ対策・例外保護");
  console.log("ALL choices UI tests PASSED!");
})().catch((err) => {
  console.error("FAIL:", err);
  process.exit(1);
});
