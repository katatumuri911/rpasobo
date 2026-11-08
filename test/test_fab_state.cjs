// FAB の状態導出（updateFabState）の単体テスト。
// public/app.js から updateFabState だけを抜き出し、依存をスタブ化して検証する。
// 実行: node test/test_fab_state.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8").replace(/\r\n/g, "\n");
const START = "function updateFabState() {";
const END = "\n\n// FAB/Esc 共通";
const s0 = src.indexOf(START);
const s1 = src.indexOf(END, s0);
if (s0 < 0 || s1 < 0) throw new Error("updateFabState を抽出できません");
const moduleSrc = src.slice(s0, s1);

// ---------------------------------------------------------------- スタブ DOM
class El {
  constructor(id) {
    this.id = id;
    this.textContent = "";
    this.title = "";
    this.attrs = {};
    this._classes = new Set();
    this.classList = {
      add: (...c) => c.forEach((x) => this._classes.add(x)),
      remove: (...c) => c.forEach((x) => this._classes.delete(x)),
      contains: (c) => this._classes.has(c),
      toggle: (c, on) => {
        const v = on === undefined ? !this._classes.has(c) : !!on;
        v ? this._classes.add(c) : this._classes.delete(c);
        return v;
      },
    };
  }
  get className() { return [...this._classes].join(" "); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  setAttribute(k, v) { this.attrs[k] = v; }
}

const fab = new El("mobileFab");
fab.className = "mobile-fab";
const settingsPanel = new El("settingsPanel");
settingsPanel.classList.add("hidden");
const lightbox = new El("lightbox");
lightbox.classList.add("hidden");
const body = new El("body");

const els = { mobileFab: fab, settingsPanel, lightbox };
let moreSheets = [];
let modals = [];
const documentStub = {
  body,
  querySelectorAll: (sel) => {
    if (sel === ".more-sheet") return moreSheets;
    if (sel === ".modal:not(.hidden)") return modals;
    return [];
  },
};

let mobileOn = true;
let typingOn = false;
const sandbox = {
  console,
  document: documentStub,
  $: (id) => els[id] || null,
  isMobileLayout: () => mobileOn,
  isFooterFocused: () => typingOn,
  syncFootBarPlacement: () => {},
  syncActionControlsVisibility: () => {},
};
vm.createContext(sandbox);
vm.runInContext(moduleSrc, sandbox, { filename: "app.js#fab-state" });
const updateFabState = sandbox.updateFabState;
if (typeof updateFabState !== "function") throw new Error("updateFabState を取得できません");

let pass = 0, fail = 0;
const t = (name, cond, extra) => {
  if (cond) { pass++; console.log("  PASS " + name); }
  else { fail++; console.log("  FAIL " + name + (extra !== undefined ? "  -> " + JSON.stringify(extra) : "")); }
};
const hidden = () => fab.classList.contains("hidden") === false && fab.classList.contains("fab-hidden");
const isClose = () => fab.classList.contains("is-close");
const reset = () => {
  fab.className = "mobile-fab";
  settingsPanel.classList.add("hidden");
  lightbox.classList.add("hidden");
  body.classList.remove("side-open");
  moreSheets = [];
  modals = [];
  mobileOn = true;
  typingOn = false;
};

console.log("[1] 通常状態");
reset();
updateFabState();
t("モバイル・何も開いていない -> 表示", !hidden());
t("-> ☰", fab.textContent === "☰", fab.textContent);
t("-> is-close なし", !isClose());
t("-> aria-label メニュー", fab.attrs["aria-label"] === "メニュー");

console.log("[2] FABが上に乗るレイヤー（✕で閉じる）");
for (const [name, setup] of [
  ["ドロワー", () => body.classList.add("side-open")],
  ["設定", () => settingsPanel.classList.remove("hidden")],
]) {
  reset(); setup(); updateFabState();
  t(`${name} 中 -> 表示`, !hidden());
  t(`${name} 中 -> ✕`, fab.textContent === "✕", fab.textContent);
  t(`${name} 中 -> is-close`, isClose());
}

console.log("[3] FABを覆う / 自前の閉じ導線があるレイヤー（非表示）");
for (const [name, setup] of [
  ["⋯シート", () => { moreSheets = [new El("sheet")]; }],
  ["lightbox", () => lightbox.classList.remove("hidden")],
  ["入力中", () => { typingOn = true; }],
  ["デスクトップ", () => { mobileOn = false; }],
  ["モーダル", () => { modals = [new El("m")]; }],
  ["モーダル2枚", () => { modals = [new El("ide"), new El("fork")]; }],
]) {
  reset(); setup(); updateFabState();
  t(`${name} -> 非表示`, hidden());
}

console.log("[4] 残留しない（条件が消えたら復帰）");
reset();
typingOn = true; updateFabState();
t("入力中は非表示", hidden());
typingOn = false; updateFabState();
t("入力を離すと復帰", !hidden());
reset();
moreSheets = [new El("sheet")]; updateFabState();
t("⋯シートで非表示", hidden());
moreSheets = []; updateFabState();
t("⋯シートを閉じると復帰", !hidden());

console.log("[5] 未知クラス（RP色など）を保持する");
reset();
fab.className = "mobile-fab rp-highlight";
updateFabState();
t("rp-highlight が残る", fab.classList.contains("rp-highlight"));
t("fab-hidden が正しく付く", !hidden());
body.classList.add("side-open"); updateFabState();
t("rp-highlight は ✕ になっても残る", fab.classList.contains("rp-highlight"));

console.log("[6] モーダルを閉じると復帰する");
reset();
modals = [new El("ide"), new El("fork")];
updateFabState();
t("モーダル2枚 -> 非表示", hidden());
modals = [];
updateFabState();
t("モーダルを閉じると表示に戻る", !hidden());
t("-> ☰ に戻る", fab.textContent === "☰", fab.textContent);

console.log(`\n===== PASS ${pass} / FAIL ${fail} =====`);
process.exit(fail ? 1 : 0);
