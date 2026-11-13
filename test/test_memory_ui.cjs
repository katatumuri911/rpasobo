// test/test_memory_ui.cjs - 記憶カードUIの優先度ドロップダウン統合テスト
// 実行: node test/test_memory_ui.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

// スタブ DOM
class StubEl {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this._textContent = "";
    this.title = "";
    this.style = {};
    this.attrs = {};
    this.listeners = {};
    this.onclick = null;
    this.onchange = null;
    this.value = "";
    this.disabled = false;
    this.type = "";
    const self = this;
    this.classList = {
      add(...cls) {
        const cur = new Set(self.className.split(/\s+/).filter(Boolean));
        cls.forEach(c => cur.add(c));
        self.className = Array.from(cur).join(" ");
      },
      remove(...cls) {
        const cur = new Set(self.className.split(/\s+/).filter(Boolean));
        cls.forEach(c => cur.delete(c));
        self.className = Array.from(cur).join(" ");
      },
      contains(c) {
        return self.className.split(/\s+/).includes(c);
      },
      toggle(c, force) {
        if (force === undefined) force = !this.contains(c);
        if (force) this.add(c); else this.remove(c);
      }
    };
  }
  get textContent() {
    return this._textContent;
  }
  set textContent(v) {
    this._textContent = String(v);
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(child) {
    if (child) {
      child.parentNode = this;
      this.children.push(child);
    }
    return child;
  }
  append(...children) {
    for (const c of children) this.appendChild(c);
  }
  addEventListener(evt, fn) {
    if (!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push(fn);
  }
  querySelector(sel) {
    for (const c of this.children) {
      if (sel.startsWith(".") && c.classList.contains(sel.slice(1))) return c;
      const found = c.querySelector(sel);
      if (found) return found;
    }
    return null;
  }
  querySelectorAll(sel) {
    const res = [];
    for (const c of this.children) {
      if (sel.startsWith(".") && c.classList.contains(sel.slice(1))) res.push(c);
      res.push(...c.querySelectorAll(sel));
    }
    return res;
  }
}

const doc = {
  createElement(tag) { return new StubEl(tag); },
  getElementById(id) { return null; }
};

const sandbox = {
  window: {},
  location: { hash: "", search: "", href: "" },
  navigator: { userAgent: "test" },
  URLSearchParams,
  Event: class Event {},
  CustomEvent: class CustomEvent {},
  document: doc,
  console,
  setTimeout: () => {},
  clearTimeout: () => {},
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }),
  alert: () => {},
  confirm: () => true,
  checked: () => Promise.resolve({}),
  scrollToMessage: () => {},
  openRagMemEditor: () => {},
  loadRagMemories: () => Promise.resolve(),
  sid: "test_session",
  $: () => new StubEl("div")
};
sandbox.window = sandbox;

// 対象コードを抽出: MEMORY_PRIORITY_LEVELS から renderRagMemories の終わりまで
const s0 = src.indexOf("const MEMORY_PRIORITY_LEVELS = [");
const s1 = src.indexOf("function openRagMemEditor(");
if (s0 < 0 || s1 < 0) throw new Error("対象コードの抽出に失敗しました");
const targetCode = src.slice(s0, s1);

const exportShim = `
;globalThis.MEMORY_PRIORITY_LEVELS = MEMORY_PRIORITY_LEVELS;
globalThis.resolveMemoryPriorityKey = resolveMemoryPriorityKey;
globalThis.createInlinePrioritySelect = createInlinePrioritySelect;
globalThis.renderRagMemories = renderRagMemories;
`;
vm.createContext(sandbox);
vm.runInContext(targetCode + exportShim, sandbox);

console.log("--- 1. MEMORY_PRIORITY_LEVELS actionLabel テスト ---");
const levels = sandbox.MEMORY_PRIORITY_LEVELS;
assert(Array.isArray(levels), "MEMORY_PRIORITY_LEVELS が配列であること");
const keyMap = {};
for (const l of levels) keyMap[l.key] = l.actionLabel;

assert.strictEqual(keyMap.pinned, "📌固定▼");
assert.strictEqual(keyMap.highest, "📍最優先▼");
assert.strictEqual(keyMap.high, "📍優先▼");
assert.strictEqual(keyMap.default, "📍標準▼");
assert.strictEqual(keyMap.low, "📍抑制▼");
console.log("  PASS: 5段階の actionLabel (📌固定▼ / 📍最優先▼ / 📍優先▼ / 📍標準▼ / 📍抑制▼)");

console.log("--- 2. createInlinePrioritySelect の生成テスト ---");
const selectDefault = sandbox.createInlinePrioritySelect("s1", { id: "m1", priority: "default" });
assert.strictEqual(selectDefault.children.length, 5, "5つのoptionがあること");
const defaultSelectedOpt = selectDefault.children.find(o => o.value === "default");
assert.strictEqual(defaultSelectedOpt.textContent, "📍標準▼");

const selectPinned = sandbox.createInlinePrioritySelect("s2", { id: "m2", priority: "pinned" });
assert(selectPinned.classList.contains("is-pinned"), "pinnedのときにis-pinnedクラスが付与されていること");
const pinnedSelectedOpt = selectPinned.children.find(o => o.value === "pinned");
assert.strictEqual(pinnedSelectedOpt.textContent, "📌固定▼");
console.log("  PASS: createInlinePrioritySelect のクラスとoption表記");

console.log("--- 3. renderRagMemories のカードDOM構造テスト ---");
const container = new StubEl("div");
container.id = "ragMemEntries";
sandbox.$ = (id) => (id === "ragMemEntries" ? container : new StubEl("div"));

sandbox.renderRagMemories([
  { id: "m1", category: "event", priority: "default", mid: "msg_123", content: "テスト記憶1" },
  { id: "m2", category: "rule", priority: "pinned", content: "テスト固定記憶2" }
]);

assert.strictEqual(container.children.length, 2, "2枚のカードが描画されていること");

// 1枚目 (m2: pinned, midなし)
const cardPinned = container.children[0];
assert(cardPinned.classList.contains("rag-entry-pinned"), "固定カードに rag-entry-pinned が付く");
const headPinned = cardPinned.querySelector(".rag-entry-head");
const badgesPinned = headPinned.querySelector(".rag-entry-badges");
const actionsPinned = headPinned.querySelector(".rag-entry-actions");

// badges の中に select が無いこと
assert.strictEqual(badgesPinned.querySelectorAll(".rag-entry-priority-select").length, 0, "badges に select が無いこと");

// actions の中身の順序確認: [prioSelect, jumpBtn, editBtn, delBtn] (midなしでも常に4つ)
const pinnedChildren = actionsPinned.children;
assert.strictEqual(pinnedChildren.length, 4, "midなしの手動記憶でも4つのボタンが並ぶ");
assert.strictEqual(pinnedChildren[0].tagName, "SELECT", "actionsの先頭は優先度セレクト");
assert(pinnedChildren[0].classList.contains("is-pinned"), "セレクトに is-pinned があること");
assert.strictEqual(pinnedChildren[1].textContent, "💬", "2番目は 💬 ボタン");
assert.strictEqual(pinnedChildren[2].textContent, "✏️", "3番目は 編集 ボタン");
assert.strictEqual(pinnedChildren[3].textContent, "🗑", "4番目は 削除 ボタン");

// 2枚目 (m1: default, midあり)
const cardDefault = container.children[1];
const actionsDefault = cardDefault.querySelector(".rag-entry-actions");
const defaultChildren = actionsDefault.children;
assert.strictEqual(defaultChildren.length, 4, "midがある場合も4つの要素 (優先度, 💬, ✏️, 🗑)");
assert.strictEqual(defaultChildren[0].tagName, "SELECT", "1番目は優先度セレクト");
assert.strictEqual(defaultChildren[1].textContent, "💬", "2番目は 💬 ボタン");
assert.strictEqual(defaultChildren[2].textContent, "✏️", "3番目は 編集 ボタン");
assert.strictEqual(defaultChildren[3].textContent, "🗑", "4番目は 削除 ボタン");

console.log("  PASS: 全カード共通のボタン配置順序 [📍標準▼ / 📌固定▼] [💬] [✏️] [🗑]");
console.log("ALL memory UI tests PASSED!");
