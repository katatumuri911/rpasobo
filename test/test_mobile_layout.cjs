// モバイル レイアウト仕様 v1.0 の小物（状態チップ・ツール（旧「操作を表示」）/reveal・footBar 移送・
// 作中の時間モーダルのクランプ）の自動テスト。
// public/app.js から該当モジュールを抜き出し、スタブ DOM 上で振る舞いを検証する。
// 実行: node test/test_mobile_layout.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("node:assert");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8").replace(/\r\n/g, "\n");
function slice(startMarker, endMarker) {
  const a = src.indexOf(startMarker);
  const b = src.indexOf(endMarker, a);
  if (a < 0 || b < 0) throw new Error("抽出失敗: " + startMarker);
  return src.slice(a, b);
}
const helper = slice("// --- モバイル判定・短縮文言", "// スマホ向けレスポンシブ・UIヘルパー");
const footbar = slice("function syncFootBarPlacement() {", "\nasync function api(");
// 中央の「送信 / ツール」ボタンの押下処理（押した瞬間の役割を覚えるか）
const actionToggleSrc = slice("// 中央ボタン: 入力中は「送信」、それ以外は「ツール」", "if ($(\"revealRpToggle\"))");
const clampday = slice("function clampDay(n) {", "\n\nfunction renderElapsedPeriodPicker");
if (!helper.includes("function syncActionControlsVisibility")) throw new Error("ヘルパーを抽出できません");

// ---------------------------------------------------------------- スタブ DOM
class El {
  constructor(id, tag = "div") {
    this.id = id;
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.textContent = "";
    this.value = "";
    this.dataset = {};
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
  appendChild(c) {
    if (c.parentElement) c.parentElement.children = c.parentElement.children.filter((x) => x !== c);
    c.parentElement = this;
    this.children.push(c);
    return c;
  }
  insertBefore(c, ref) {
    if (c.parentElement) c.parentElement.children = c.parentElement.children.filter((x) => x !== c);
    c.parentElement = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  get firstChild() { return this.children[0] || null; }
  contains(n) { return this === n || this.children.some((c) => c.contains(n)); }
  setAttribute() {}
  addEventListener(type, fn) {
    this._listeners = this._listeners || {};
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  fire(type, ev = {}) {
    const list = (this._listeners && this._listeners[type]) || [];
    let prevented = false;
    for (const fn of list) fn({ button: 0, preventDefault: () => { prevented = true; }, ...ev });
    return prevented;
  }
}

const els = {};
const mk = (id, tag) => (els[id] = new El(id, tag));
mk("settingsPanel").classList.add("hidden");
mk("mobileReveal", "div").classList.add("hidden");
mk("mobileActionToggle", "button").classList.add("hidden");
mk("revealModel", "button").classList.add("hidden");
mk("mobileFab", "button");
mk("footBar", "div");
mk("modelSheet", "div").classList.add("hidden");
mk("elapsedModal", "div").classList.add("hidden");
mk("modelSheetSlot", "div");
mk("sideModelContainer", "div");
mk("modelSheetLeft", "div").classList.add("hidden");
mk("revealRpToggle", "button");
mk("revealBackdrop", "div").classList.add("hidden");
mk("ideModel", "select");
mk("elapsedDayInput", "input");
mk("messageInput", "textarea");

const footer = new El("footer", "footer");
els.attachList = footer.appendChild(new El("attachList", "div"));   // footer.firstChild 兼 $("attachList")
footer.appendChild(els.messageInput);   // isFooterFocused の判定用（フッターの中にある）

const body = new El("body", "body");
let moreSheets = [];
const documentStub = {
  body,
  activeElement: null,
  querySelector: (sel) => (sel === "footer" ? footer : null),
  querySelectorAll: (sel) => {
    if (sel === ".modal:not(.hidden)") return [els.modelSheet].filter((e) => !e.classList.contains("hidden"));
    if (sel === ".more-sheet") return moreSheets;
    return [];
  },
};

let mobileOn = true;
let tinyOn = false;
const timers = [];   // showReveal が自動非表示タイマーを張っていないかを見る
const sandbox = {
  console,
  document: documentStub,
  window: { matchMedia: (q) => ({ get matches() { return q.includes("360") ? tinyOn : mobileOn; }, media: q }) },
  setTimeout: (...a) => { timers.push(a); return { f: a[0] }; },
  clearTimeout: () => {},
  state: { setting: { ide_session: "abc" } },
  // 中央ボタン（送信/ツール）の出し分けが見る状態。抽出した断片だけでは未定義なので明示する
  sending: false,
  stopping: false,
  attaching: [],
  hasWaitingInput: () => false,
  rpMode: true,
  dirtyFields: new Set(),
  PERIODS: ["早朝", "午前", "昼", "夕方", "夜", "深夜"],
  PERIOD_EMOJIS: {},
  toast: () => {},
  loadModels: () => {},
};
sandbox.$ = (id) => els[id] || null;
sandbox.window.innerWidth = 400;
vm.createContext(sandbox);
vm.runInContext(helper, sandbox, { filename: "app.js#mobile-helpers" });
vm.runInContext(footbar, sandbox, { filename: "app.js#footbar" });
vm.runInContext(clampday, sandbox, { filename: "app.js#clampday" });
let sendCount = 0;
let fakeNow = 1000;   // 二重送信防止の抑止時間（600ms）をテスト内で進める
sandbox.Date = { now: () => fakeNow };
sandbox.send = () => { sendCount++; };   // 実際の送信は通信するので回数だけ見る
vm.runInContext(actionToggleSrc, sandbox, { filename: "app.js#action-toggle" });

let pass = 0;
let fail = 0;
const t = (name, cond) => { cond ? (pass++, console.log("  PASS " + name)) : (fail++, console.log("  FAIL " + name)); };

console.log("[1] clampDay は 1〜99999 に丸める");
t("0 -> 1", sandbox.clampDay(0) === 1);
t("空(NaN) -> 1", sandbox.clampDay(NaN) === 1);
t("-5 -> 1", sandbox.clampDay(-5) === 1);
t("999999 -> 99999", sandbox.clampDay(999999) === 99999);
t("3.9 -> 3", sandbox.clampDay(3.9) === 3);

console.log("[2] 中央ボタンは入力中だけ送信（ツールは + のメニュー）");
sandbox.syncActionControlsVisibility();
t("アイドル時は中央ボタンを出さない", els.mobileActionToggle.classList.contains("hidden"));
t("reveal は隠れたまま", els.mobileReveal.classList.contains("hidden"));
els.mobileReveal.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("reveal を開いても中央ボタンは出さない", els.mobileActionToggle.classList.contains("hidden"));
els.mobileReveal.classList.add("hidden");
body.classList.add("side-open");
sandbox.syncActionControlsVisibility();
t("ドロワー中は隠す", els.mobileActionToggle.classList.contains("hidden"));
body.classList.remove("side-open");
els.settingsPanel.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("設定パネル中は隠す", els.mobileActionToggle.classList.contains("hidden"));
els.settingsPanel.classList.add("hidden");
moreSheets = [new El("sheet", "div")];
sandbox.syncActionControlsVisibility();
t("⋯シート中は隠す", els.mobileActionToggle.classList.contains("hidden"));
moreSheets = [];
els.modelSheet.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("モデルシート中は隠す", els.mobileActionToggle.classList.contains("hidden"));
els.modelSheet.classList.add("hidden");
sandbox.rpMode = false;
sandbox.syncActionControlsVisibility();
t("RPオフでもアイドル時は非表示", els.mobileActionToggle.classList.contains("hidden"));
t("RPオフで 🖼 の .on が外れる", !els.revealRpToggle.classList.contains("on"));
sandbox.rpMode = true;
sandbox.syncActionControlsVisibility();
t("RPオンで 🖼 が .on になる", els.revealRpToggle.classList.contains("on"));

els.mobileReveal.classList.add("hidden");
documentStub.activeElement = els.attachList;   // footer の中にフォーカスがある状態
sandbox.syncActionControlsVisibility();
t("入力中でも空なら中央ボタンは出さない", els.mobileActionToggle.classList.contains("hidden"));
els.mobileReveal.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("入力中はメニューを畳む", els.mobileReveal.classList.contains("hidden"));
documentStub.activeElement = null;
els.mobileReveal.classList.add("hidden");
sandbox.syncActionControlsVisibility();

console.log("[2b] 中央の「送信」は入力中だけ出る（左手でも届く送信）");
els.messageInput.value = "";
sandbox.attaching = [];
documentStub.activeElement = els.messageInput;   // 入力中（フォーカスあり）
sandbox.syncActionControlsVisibility();
t("入力中でも空なら非表示", els.mobileActionToggle.classList.contains("hidden"));
t("空なら send-mode は付かない", !els.mobileActionToggle.classList.contains("send-mode"));
els.messageInput.value = "こんにちは";
sandbox.syncActionControlsVisibility();
t("入力中に文字あり -> 送信", els.mobileActionToggle.textContent === "送信");
t("入力中に文字あり -> 表示", !els.mobileActionToggle.classList.contains("hidden"));
t("入力中に文字あり -> send-mode（青くなる）", els.mobileActionToggle.classList.contains("send-mode"));
sandbox.sending = true;
sandbox.syncActionControlsVisibility();
t("送信中は押しても無反応の送信を出さない", els.mobileActionToggle.classList.contains("hidden"));
sandbox.sending = false;
sandbox.hasWaitingInput = () => true;
sandbox.syncActionControlsVisibility();
t("応答待ち中も非表示", els.mobileActionToggle.classList.contains("hidden"));
sandbox.hasWaitingInput = () => false;
els.messageInput.value = "";
sandbox.attaching = [{ name: "a.png", file: {} }];
sandbox.syncActionControlsVisibility();
t("添付だけでも送信", els.mobileActionToggle.textContent === "送信");
// 入力を止めたら非表示に戻る（ツールは + から開く）
documentStub.activeElement = null;
els.messageInput.value = "こんにちは";
sandbox.attaching = [];
sandbox.syncActionControlsVisibility();
t("入力を止めたら非表示に戻る（文字が残っていても）", els.mobileActionToggle.classList.contains("hidden"));
t("入力を止めたら send-mode も外れる", !els.mobileActionToggle.classList.contains("send-mode"));
documentStub.activeElement = null;
sandbox.attaching = [];
els.messageInput.value = "こんにちは";
els.mobileReveal.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("メニューを開いている間も中央ボタンは出さない", els.mobileActionToggle.classList.contains("hidden"));
els.mobileReveal.classList.add("hidden");
els.messageInput.value = "";
sandbox.syncActionControlsVisibility();

console.log("[3] footBar は常に footer（ドロワー・シートへの移送は廃止）");
mobileOn = false;
sandbox.syncFootBarPlacement();
t("デスクトップ: footer へ", els.footBar.parentElement === footer);
mobileOn = true;
els.footBar.parentElement = null;
sandbox.syncFootBarPlacement();
t("モバイルでも footer へ（ドロワー下段は廃止）", els.footBar.parentElement === footer);
t("attachList より前に置く", footer.children.indexOf(els.footBar) < footer.children.indexOf(els.attachList));

mobileOn = true;
console.log("[4] 🤖 はチャンネル設定があるとき出す（AG / Google）");
sandbox.state = null;
sandbox.syncActionControlsVisibility();
t("state 無し -> .hidden で隠す", els.revealModel.classList.contains("hidden"));
sandbox.state = { setting: {} };
els.revealModel.classList.add("hidden");
sandbox.syncActionControlsVisibility();
t("setting 有り -> 表示", !els.revealModel.classList.contains("hidden"));

console.log("[5] reveal は時間経過で消えない");
mobileOn = true;   // セクション[3] でデスクトップにしていたのを戻す
sandbox.rpMode = true;
timers.length = 0;
els.mobileReveal.classList.add("hidden");
sandbox.showReveal();
t("showReveal で表示される", !els.mobileReveal.classList.contains("hidden"));
t("showReveal でタップ閉じ用の受け皿も出る", !els.revealBackdrop.classList.contains("hidden"));
t("自動非表示タイマーを張らない", timers.length === 0);
sandbox.hideReveal();
t("hideReveal で隠れる", els.mobileReveal.classList.contains("hidden"));
t("hideReveal で受け皿も隠れる", els.revealBackdrop.classList.contains("hidden"));

console.log("[6] モバイル表示は id セレクタで上書きする（class だと display:none に負ける）");
const css = fs.readFileSync(path.join(__dirname, "..", "public", "style.css"), "utf8");
// 最後の @media (max-width: 768px) ブロックを波括弧対応で切り出す（モバイル小物の定義がある場所）
function lastMediaBlock(text) {
  const at = text.lastIndexOf("@media (max-width: 768px)");
  if (at < 0) throw new Error("モバイル用 @media が無い");
  const open = text.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return text.slice(open + 1, i);
  }
  throw new Error("@media ブロックの閉じ括弧が無い");
}
const mediaBlock = lastMediaBlock(css);
function declares(block, selector, prop) {
  const at = block.indexOf(selector);
  if (at < 0) return false;
  const open = block.indexOf("{", at);
  const close = block.indexOf("}", open);
  return open >= 0 && close > open && new RegExp(prop + "\\s*:").test(block.slice(open, close));
}
function declaresNumeric(block, selector, prop, minPx) {
  const at = block.indexOf(selector);
  if (at < 0) return false;
  const open = block.indexOf("{", at);
  const close = block.indexOf("}", open);
  if (open < 0 || close <= open) return false;
  const decl = block.slice(open, close);
  const m = decl.match(new RegExp(prop + "\\s*:\\s*([^;]+)"));
  if (!m) return false;
  const value = m[1];
  /* calc(...) でもよい（入力カードの高さ --footer-h を含む式を許す）。
     値の中の px のどれかが下限以上なら「下余白あり」とみなす */
  const nums = [...value.matchAll(/(\d+(?:\.\d+)?)px/g)].map((x) => parseFloat(x[1]));
  return nums.some((n) => n >= minPx);
}
for (const id of ["mobileActionToggle", "mobileReveal", "sideBottomSection", "revealBackdrop"]) {
  t(`#${id} が @media 内で id セレクタで display を宣言している`, declares(mediaBlock, `#${id}`, "display"));
}
// 浮いているボタンのぶんの下余白がある（値は style.css 側で調整。消し忘れ防止の下限）
t("#chatLog に下余白がある（浮きボタン用）", declaresNumeric(mediaBlock, "#chatLog", "padding-bottom", 24));

console.log("[7] 中央の「送信」は押した瞬間の役割で動く（押下でフォーカスが外れても送信される）");
mobileOn = true;
sandbox.rpMode = true;
documentStub.activeElement = els.messageInput;
els.messageInput.value = "こんにちは";
sandbox.attaching = [];
els.mobileReveal.classList.add("hidden");
sandbox.syncActionControlsVisibility();
t("前提: 入力中は「送信」", els.mobileActionToggle.textContent === "送信");
// 指を置いた瞬間に入力欄からフォーカスが外れる（ブラウザの実際の順序）
const prevented = els.mobileActionToggle.fire("pointerdown", { button: 0, clientX: 100, clientY: 100 });
t("送信の押下では入力欄のフォーカスを手放さない（preventDefault）", prevented === true);
documentStub.activeElement = els.mobileActionToggle;   // もし blur してもボタンは footer の外
t("押している間は「送信」のまま（非表示に戻らない）", els.mobileActionToggle.textContent === "送信");
els.mobileActionToggle.fire("pointerup", { clientX: 100, clientY: 100 });
t("指を離すと send() が呼ばれる（click が別要素に落ちても送れる）", sendCount === 1);
els.mobileActionToggle.onclick();   // click が来ても
t("click が重なっても二重送信しない", sendCount === 1);
t("送信後にメニューは開かない", els.mobileReveal.classList.contains("hidden"));

// 指を大きく動かした（スクロール）ときは送らない
els.messageInput.value = "こんにちは";
documentStub.activeElement = els.messageInput;
sandbox.attaching = [];
sandbox.syncActionControlsVisibility();
els.mobileActionToggle.fire("pointerdown", { button: 0, clientX: 100, clientY: 100 });
els.mobileActionToggle.fire("pointermove", { clientX: 100, clientY: 50 });
els.mobileActionToggle.fire("pointerup", { clientX: 100, clientY: 50 });
els.mobileActionToggle.onclick();
t("スクロールした指では送らない", sendCount === 1);

fakeNow += 5000;   // スクロール判定の抑止時間を過ぎたあとのタップ
els.messageInput.value = "";
documentStub.activeElement = null;
sandbox.syncActionControlsVisibility();
t("前提: 入力していないときは非表示", els.mobileActionToggle.classList.contains("hidden"));
els.mobileActionToggle.fire("pointerdown", { button: 0 });
els.mobileActionToggle.fire("pointerup");
els.mobileActionToggle.onclick();
t("アイドル時はメニューを開かない", els.mobileReveal.classList.contains("hidden"));
t("アイドル時は send() を呼ばない", sendCount === 1);

console.log("[8] サイドドロワー スワイプ開閉（T1〜T26）");

/* --- 時計（I-7: 各テスト冒頭でリセット、テスト内は単調増加） --- */
let swipeClock = 0;
function resetClock() { swipeClock = 0; }

/* --- app.js からスワイプモジュールのみ抽出（末尾の即時 async 起動を実行しない） --- */
const SWIPE_START = '/* ============================================================\n * ドロワー開閉の単一窓口';
const SWIPE_END = '\n/* テスト用フック（副作用なし・app.js 末尾で公開） */';

function createSwipeStubEnv() {
  const docListeners = {};
  const winListeners = {};
  let selectionStub = { isCollapsed: true, toString: () => '' };
  let mobileViewportMatch = true;

  class StubEl {
    constructor(id, tag = 'div', ownerDoc = null) {
      this.id = id;
      this.tagName = tag.toUpperCase();
      this.ownerDocument = ownerDoc;
      this.children = [];
      this.parentElement = null;
      this.style = { transform: '', opacity: '', overflowX: '' };
      this.dataset = {};
      this.attrs = {};
      this.inert = false;
      this._classes = new Set();
      this._listeners = {};
      this._rect = { width: 0, height: 0, left: 0, top: 0 };
      this._rects = 1;                 // 実ブラウザの getClientRects() の代わり（0 なら未レイアウト）
      this._scrollWidth = 0;
      this._clientWidth = 0;
      this.classList = {
        add: (...c) => c.forEach((x) => this._classes.add(x)),
        remove: (...c) => c.forEach((x) => this._classes.delete(x)),
        contains: (c) => this._classes.has(c),
        toggle: (c, on) => {
          const v = on === undefined ? !this._classes.has(c) : !!on;
          v ? this._classes.add(c) : this._classes.delete(c);
          return v;
        }
      };
    }
    get offsetWidth() { return this._rect.width; }
    get scrollWidth() { return this._scrollWidth; }
    get clientWidth() { return this._clientWidth; }
    getBoundingClientRect() { return this._rect; }
    getClientRects() { return this._rects === 0 ? [] : [{ width: 100, height: 100, left: 0, top: 0 }]; }
    appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
    contains(n) { return this === n || this.children.some((c) => c.contains(n)); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
    focus() {}
    blur() {}
    addEventListener(type, fn, opts) {
      const cap = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
      (this._listeners[type] = this._listeners[type] || []).push({ fn, capture: cap, once: Boolean(opts && opts.once) });
    }
    removeEventListener(type, fn, opts) {
      const cap = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
      if (!this._listeners[type]) return;
      this._listeners[type] = this._listeners[type].filter((l) => !(l.fn === fn && l.capture === cap));
    }
    matchesSelector(sel) {
      for (const part of sel.split(/\s*,\s*/)) {
        if (!part) continue;
        if (part.startsWith('#') && this.id === part.slice(1)) return true;
        if (part.startsWith('.') && this._classes.has(part.slice(1))) return true;
        if (part === '[data-no-drawer-swipe]' && this.hasAttribute('data-no-drawer-swipe')) return true;
        if (part === '[contenteditable="true"]' && this.getAttribute('contenteditable') === 'true') return true;
        if (part === '[contenteditable=""]' && this.getAttribute('contenteditable') === '') return true;
        if (part === '[role="button"]' && this.getAttribute('role') === 'button') return true;
        if (/^[a-z]+$/i.test(part) && this.tagName === part.toUpperCase()) return true;
      }
      return false;
    }
    closest(sel) {
      let cur = this;
      while (cur) {
        if (cur.matchesSelector(sel)) return cur;
        cur = cur.parentElement;
      }
      return null;
    }
    createText(text = '') {
      const node = {
        nodeType: 3,
        textContent: text,
        parentElement: this,
        dispatchEvent: (ev) => {
          ev.target = node;
          return this.dispatchEvent(ev);
        }
      };
      this.children.push(node);
      return node;
    }
    dispatchEvent(ev) {
      if (!ev.target) ev.target = this;
      const chain = [];
      let cur = this;
      while (cur) { chain.unshift(cur); cur = cur.parentElement; }
      let stopped = false, immediateStopped = false;
      ev.preventDefault = () => { if (ev.cancelable) ev.defaultPrevented = true; };
      ev.stopPropagation = () => { stopped = true; };
      ev.stopImmediatePropagation = () => { stopped = true; immediateStopped = true; };

      const invoke = (list, capFilter) => {
        for (const item of list.slice()) {
          if (item.capture !== capFilter) continue;
          if (item.once) {
            const idx = list.indexOf(item);
            if (idx >= 0) list.splice(idx, 1);
          }
          item.fn(ev);
          if (immediateStopped) return;
        }
      };

      // Capture: window -> document -> ancestors
      invoke(winListeners[ev.type] || [], true);
      if (stopped) return !ev.defaultPrevented;
      invoke(docListeners[ev.type] || [], true);
      if (stopped) return !ev.defaultPrevented;
      for (const el of chain) {
        invoke(el._listeners[ev.type] || [], true);
        if (stopped) return !ev.defaultPrevented;
      }
      // Bubble: target -> ancestors -> document -> window
      for (let i = chain.length - 1; i >= 0; i--) {
        invoke(chain[i]._listeners[ev.type] || [], false);
        if (stopped) return !ev.defaultPrevented;
      }
      invoke(docListeners[ev.type] || [], false);
      if (stopped) return !ev.defaultPrevented;
      invoke(winListeners[ev.type] || [], false);
      return !ev.defaultPrevented;
    }
  }

  const doc = {
    hidden: false,
    activeElement: null,
    body: null,
    documentElement: null,
    addEventListener(t, fn, opts) {
      const cap = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
      (docListeners[t] = docListeners[t] || []).push({ fn, capture: cap });
    },
    removeEventListener(t, fn, opts) {
      const cap = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
      if (!docListeners[t]) return;
      docListeners[t] = docListeners[t].filter((l) => !(l.fn === fn && l.capture === cap));
    },
    getElementById(id) { return byId[id] || null; },
    querySelectorAll(sel) {
      if (sel.includes('.modal') && !modal.classList.contains('hidden')) return [modal];
      if (sel.includes('#settingsPanel') && !settingsPanel.classList.contains('hidden')) return [settingsPanel];
      if (sel.includes('[role="dialog"]') || sel.includes('[aria-modal="true"]')) return dialogStubs;
      return [];
    }
  };
  doc.documentElement = new StubEl('html', 'html', doc);
  doc.body = new StubEl('body', 'body', doc);
  doc.documentElement.appendChild(doc.body);

  const drawer = new StubEl('sidebar', 'aside', doc);
  drawer._rect.width = 280;
  drawer.setAttribute('aria-hidden', 'true');
  const closeInside = new StubEl('close-inside', 'button', doc);
  drawer.appendChild(closeInside);
  const overlay = new StubEl('sideBackdrop', 'div', doc);
  const app = new StubEl('app', 'div', doc);
  const toggle = new StubEl('sideToggle', 'button', doc);
  toggle.setAttribute('aria-expanded', 'false');
  const composer = new StubEl('messageInput', 'textarea', doc);
  const promptToolbar = new StubEl('prompt-toolbar', 'div', doc);
  const codeBlock = new StubEl('code1', 'code', doc);
  const hscroll = new StubEl('hscroll', 'div', doc);
  hscroll.style.overflowX = 'auto';
  hscroll._scrollWidth = 320;
  hscroll._clientWidth = 100;
  const hscrollInner = new StubEl('hscroll-inner', 'span', doc);
  hscroll.appendChild(hscrollInner);
  const noSwipe = new StubEl('no-swipe', 'div', doc);
  noSwipe.setAttribute('data-no-drawer-swipe', '1');
  const modal = new StubEl('modal', 'div', doc);
  modal.classList.add('modal', 'hidden');
  const settingsPanel = new StubEl('settingsPanel', 'section', doc);
  settingsPanel.classList.add('hidden');

  // 常設だが祖先 .modal.hidden の中にいて実際には描画されないダイアログ（実 DOM と同じ形）。
  // 自分の computed display は block のままなので、矩形の有無で判定しないと誤検知する。
  let dialogStubs = [];
  const hiddenAncestorDialog = new StubEl('fav-model-sheet-content', 'div', doc);
  hiddenAncestorDialog.classList.add('modal-content');
  hiddenAncestorDialog.setAttribute('role', 'dialog');
  hiddenAncestorDialog.setAttribute('aria-modal', 'true');
  hiddenAncestorDialog._rects = 0;
  const renderedDialog = new StubEl('rendered-dialog', 'div', doc);
  renderedDialog.classList.add('modal-content');
  renderedDialog.setAttribute('role', 'dialog');
  renderedDialog.setAttribute('aria-modal', 'true');
  renderedDialog._rects = 1;

  app.appendChild(toggle);
  app.appendChild(composer);
  app.appendChild(promptToolbar);
  app.appendChild(codeBlock);
  app.appendChild(hscroll);
  app.appendChild(noSwipe);
  app.appendChild(modal);
  app.appendChild(settingsPanel);
  doc.body.appendChild(drawer);
  doc.body.appendChild(overlay);
  doc.body.appendChild(app);

  const byId = {
    sidebar: drawer, sideBackdrop: overlay, app: app, sideToggle: toggle,
    messageInput: composer, 'prompt-toolbar': promptToolbar, hscroll: hscroll,
    'hscroll-inner': hscrollInner, 'no-swipe': noSwipe, modal: modal,
    settingsPanel: settingsPanel, 'close-inside': closeInside
  };

  const win = {
    innerWidth: 400,
    location: { search: '' },
    matchMedia: () => ({ matches: mobileViewportMatch, addListener() {}, removeListener() {} }),
    getSelection: () => selectionStub,
    getComputedStyle: (el) => ({
      display: el.classList.contains('hidden') ? 'none' : 'block',
      visibility: 'visible',
      opacity: '1',
      overflowX: el.style.overflowX || 'visible',
      width: '280px',
      transitionDuration: '0s'
    }),
    addEventListener(t, fn, opts) {
      const cap = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
      (winListeners[t] = winListeners[t] || []).push({ fn, capture: cap });
    },
    removeEventListener(t, fn, opts) {
      const cap = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
      if (!winListeners[t]) return;
      winListeners[t] = winListeners[t].filter((l) => !(l.fn === fn && l.capture === cap));
    }
  };

  const sb = {
    console, setTimeout, clearTimeout,
    document: doc, window: win,
    $: (id) => byId[id] || null,
    isMobileLayout: () => mobileViewportMatch,
    closeMoreMenu: () => {},
    hideReveal: () => {},
    updateFabState: () => {},
    performance: { now: () => swipeClock },
    Date
  };
  sb.globalThis = sb;
  vm.createContext(sb);

  const swipeModuleSrc = slice(SWIPE_START, SWIPE_END) +
    '\nwindow.__appTestHooks = { initDrawerSwipe, initAppDrawerSwipe, applyDrawerState, DRAWER_SWIPE_CONFIG, setDrawerOpen, syncDrawerDesktopAccessibility, ensureDrawerAdapter };';
  vm.runInContext(swipeModuleSrc, sb, { filename: 'app.js#drawer-swipe' });

  return {
    sb, doc, win, hooks: win.__appTestHooks, winListeners, docListeners,
    drawer, overlay, toggle, main: app,
    composer, promptToolbar, codeBlock, hscrollInner, noSwipe, modal, closeInside,
    hiddenAncestorDialog, renderedDialog,
    setDialogs: (list) => { dialogStubs = list || []; },
    setMobileViewport: (v) => { mobileViewportMatch = v; },
    setSelection: (s) => { selectionStub = s; }
  };
}

function dispatchTouch(target, type, { id = 0, x, y, allTouches = null }) {
  const touch = { identifier: id, clientX: x, clientY: y, target };
  const ev = {
    type, bubbles: true, cancelable: true, defaultPrevented: false,
    changedTouches: [touch],
    touches: allTouches !== null ? allTouches : ((type === 'touchend' || type === 'touchcancel') ? [] : [touch]),
    _testTimeStamp: swipeClock
  };
  target.dispatchEvent(ev);
  return ev;
}

function dispatchClick(target) {
  const ev = { type: 'click', bubbles: true, cancelable: true, defaultPrevented: false, _testTimeStamp: swipeClock };
  target.dispatchEvent(ev);
  return ev;
}

function buildFixture() {
  const env = createSwipeStubEnv();
  const state = { open: false, calls: 0, dragStarts: 0 };
  const fx = {
    ...env, state, transitionMs: 0,
    setOpenDirect: (open) => {
      state.open = open;
      env.hooks.applyDrawerState({ drawerEl: env.drawer, overlayEl: env.overlay, toggleEl: env.toggle, open });
    },
    tapToggle: () => {
      const next = !state.open;
      if (fx.controller) fx.controller.flush();           // P-19
      state.calls += 1;
      state.open = next;
      env.hooks.applyDrawerState({ drawerEl: env.drawer, overlayEl: env.overlay, toggleEl: env.toggle, open: next });
    }
  };
  fx.controller = env.hooks.initDrawerSwipe({
    drawerEl: env.drawer, overlayEl: env.overlay, chatAreaEl: env.main,
    isOpen: () => state.open,
    setOpen: (open) => {
      state.calls += 1; state.open = open;
      env.hooks.applyDrawerState({ drawerEl: env.drawer, overlayEl: env.overlay, toggleEl: env.toggle, open });
    },
    onDragStart: () => { state.dragStarts += 1; },
    isMobileViewport: () => env.win.matchMedia().matches,
    now: () => swipeClock,
    getDrawerWidth: () => 280,
    getTransitionDurationMs: () => fx.transitionMs,
    force: true
  });
  return fx;
}

function teardown(fx) {
  if (fx && fx.controller && typeof fx.controller.destroy === 'function') {
    fx.controller.destroy();
  }
}

// --- T1: 左端フリック開 ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 100; dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 115; dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 140; dispatchTouch(fx.main, 'touchmove', { x: 65, y: 202 });
  swipeClock = 145; dispatchTouch(fx.main, 'touchend', { x: 65, y: 202 });
  const dbg = fx.controller.getDebugState();
  const cond = dbg.path === 'flick' &&
    Math.abs(dbg.progress - 0.125) < 0.001 &&   // 35/280 = 0.125
    Math.abs(dbg.vx - (35 / 30)) < 0.02 &&      // 35/30 ≈ 1.17
    fx.state.open === true &&
    fx.state.dragStarts === 1;
  t("T1: 左端フリック開", cond);
  teardown(fx);
}

// --- T2: 開→左フリック閉 ---
{
  resetClock();
  const fx = buildFixture();
  fx.setOpenDirect(true);
  swipeClock = 200; dispatchTouch(fx.drawer, 'touchstart', { x: 220, y: 200 });
  swipeClock = 215; dispatchTouch(fx.drawer, 'touchmove', { x: 205, y: 200 });
  swipeClock = 240; dispatchTouch(fx.drawer, 'touchmove', { x: 160, y: 200 });
  swipeClock = 245; dispatchTouch(fx.drawer, 'touchend', { x: 160, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = dbg.path === 'flick' &&
    Math.abs(dbg.vx - (-1.5)) < 0.01 &&         // -45/30 = -1.50
    dbg.progress > 0.65 &&                      // 235/280 ≈ 0.839 > 0.65
    fx.state.open === false;
  t("T2: 開→左フリック閉", cond);
  teardown(fx);
}

// --- T3: 逆フリックでキャンセル ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;   dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 20;  dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 300; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
  swipeClock = 330; dispatchTouch(fx.main, 'touchmove', { x: 160, y: 200 });
  swipeClock = 335; dispatchTouch(fx.main, 'touchend', { x: 160, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = Math.abs(dbg.vx - (-40 / 35)) < 0.02 &&  // -40/35 ≈ -1.14
    fx.state.open === false;                              // 総変位+145でも閉
  t("T3: 逆フリックでキャンセル", cond);
  teardown(fx);
}

// --- T4: 静止後の離指 ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;   dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 15;  dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 40;  dispatchTouch(fx.main, 'touchmove', { x: 70, y: 200 });
  swipeClock = 160; dispatchTouch(fx.main, 'touchend', { x: 70, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = dbg.vx === 0 && dbg.path === 'ratio' && fx.state.open === false;
  t("T4: 静止後の離指", cond);
  teardown(fx);
}

// --- T5: 初期ジャンプ防止 ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 15, y: 100 });
  swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 30, y: 100 });
  const cond = fx.drawer.style.transform === 'translate3d(-280px, 0, 0)' &&
    fx.overlay.style.opacity === '0' &&
    fx.controller.getState() === 'DRAGGING_X';
  t("T5: 初期ジャンプ防止", cond);
  teardown(fx);
}

// --- T6a: 低速ドラッグ開 ---
{
  resetClock();
  const fx = buildFixture();
  const pts = [15, 40, 69, 98, 127, 156];
  swipeClock = 0; dispatchTouch(fx.main, 'touchstart', { x: pts[0], y: 200 });
  for (let i = 1; i < pts.length; i++) {
    swipeClock = i * 100;
    dispatchTouch(fx.main, 'touchmove', { x: pts[i], y: 200 });
  }
  swipeClock = 560; dispatchTouch(fx.main, 'touchend', { x: 156, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = dbg.path === 'ratio' &&
    Math.abs(dbg.progress - (116 / 280)) < 0.005 &&  // 116/280 ≈ 0.414
    fx.state.open === true;
  t("T6a: 低速ドラッグ開", cond);
  teardown(fx);
}

// --- T6b: 低速ドラッグ閉 ---
{
  resetClock();
  const fx = buildFixture();
  const pts = [15, 40, 65, 80];
  swipeClock = 0; dispatchTouch(fx.main, 'touchstart', { x: pts[0], y: 200 });
  for (let i = 1; i < pts.length; i++) {
    swipeClock = i * 100;
    dispatchTouch(fx.main, 'touchmove', { x: pts[i], y: 200 });
  }
  swipeClock = 310; dispatchTouch(fx.main, 'touchend', { x: 80, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = Math.abs(dbg.progress - (40 / 280)) < 0.005 &&  // 40/280 ≈ 0.143
    fx.state.open === false;
  t("T6b: 低速ドラッグ閉", cond);
  teardown(fx);
}

// --- T7: 縦スクロールラッチ ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;   dispatchTouch(fx.main, 'touchstart', { x: 20, y: 200 });
  swipeClock = 30;  dispatchTouch(fx.main, 'touchmove', { x: 25, y: 230 });
  const midScrollY = fx.controller.getState() === 'SCROLLING_Y';
  swipeClock = 100; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 235 });
  const cond = midScrollY &&
    !fx.drawer.classList.contains('is-dragging') &&
    fx.drawer.style.transform === '' &&
    (() => {
      swipeClock = 120; dispatchTouch(fx.main, 'touchend', { x: 200, y: 235 });
      return fx.controller.getState() === 'IDLE';
    })();
  t("T7: 縦スクロールラッチ", cond);
  teardown(fx);
}

// --- T8: マルチタッチ耐性 ---
{
  resetClock();
  const fx = buildFixture();
  const t1b = { identifier: 1, clientX: 30, clientY: 200, target: fx.main };
  const t2  = { identifier: 2, clientX: 120, clientY: 150, target: fx.main };
  const t1c = { identifier: 1, clientX: 70, clientY: 200, target: fx.main };

  swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { id: 1, x: 15, y: 200 });
  swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { id: 1, x: 30, y: 200, allTouches: [t1b] });
  swipeClock = 40; dispatchTouch(fx.main, 'touchstart', { id: 2, x: 120, y: 150, allTouches: [t1b, t2] });
  swipeClock = 60; dispatchTouch(fx.main, 'touchend', { id: 2, x: 120, y: 150, allTouches: [t1b] });
  swipeClock = 80; dispatchTouch(fx.main, 'touchmove', { id: 1, x: 70, y: 200, allTouches: [t1c] });

  const activeId = fx.controller.getDebugState().activeTouchId === 1;
  const stateDrag = fx.controller.getState() === 'DRAGGING_X';
  const transformMatch = fx.drawer.style.transform === 'translate3d(-240px, 0, 0)';

  swipeClock = 90; dispatchTouch(fx.main, 'touchend', { id: 1, x: 70, y: 200, allTouches: [] });
  const cond = activeId && stateDrag && transformMatch && fx.state.open === true;
  t("T8: マルチタッチ耐性", cond);
  teardown(fx);
}

// --- T9: 除外ガード（8サブ） ---
{
  let allGuardOk = true;

  // ① #messageInput
  {
    resetClock(); const fx = buildFixture();
    swipeClock = 0;  dispatchTouch(fx.composer, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.composer, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.composer, 'touchend', { x: 200, y: 200 });
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || fx.controller.getState() !== 'IDLE') allGuardOk = false;
    teardown(fx);
  }
  // ② #prompt-toolbar
  {
    resetClock(); const fx = buildFixture();
    swipeClock = 0;  dispatchTouch(fx.promptToolbar, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.promptToolbar, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.promptToolbar, 'touchend', { x: 200, y: 200 });
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || fx.controller.getState() !== 'IDLE') allGuardOk = false;
    teardown(fx);
  }
  // ③ code
  {
    resetClock(); const fx = buildFixture();
    swipeClock = 0;  dispatchTouch(fx.codeBlock, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.codeBlock, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.codeBlock, 'touchend', { x: 200, y: 200 });
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || fx.controller.getState() !== 'IDLE') allGuardOk = false;
    teardown(fx);
  }
  // ④ #hscroll-inner
  {
    resetClock(); const fx = buildFixture();
    swipeClock = 0;  dispatchTouch(fx.hscrollInner, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.hscrollInner, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.hscrollInner, 'touchend', { x: 200, y: 200 });
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || fx.controller.getState() !== 'IDLE') allGuardOk = false;
    teardown(fx);
  }
  // ⑤ [data-no-drawer-swipe]
  {
    resetClock(); const fx = buildFixture();
    swipeClock = 0;  dispatchTouch(fx.noSwipe, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.noSwipe, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.noSwipe, 'touchend', { x: 200, y: 200 });
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || fx.controller.getState() !== 'IDLE') allGuardOk = false;
    teardown(fx);
  }
  // ⑥ #modal active
  {
    resetClock(); const fx = buildFixture();
    fx.modal.classList.remove('hidden'); fx.modal.classList.add('active');
    swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.main, 'touchend', { x: 200, y: 200 });
    const st = fx.controller.getState();
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || (st !== 'IDLE' && st !== 'IGNORED')) allGuardOk = false;
    teardown(fx);
  }
  // ⑦ getSelection 非collapsed
  {
    resetClock(); const fx = buildFixture();
    fx.setSelection({ isCollapsed: false, toString: () => 'selected text' });
    swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.main, 'touchend', { x: 200, y: 200 });
    const st = fx.controller.getState();
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || (st !== 'IDLE' && st !== 'IGNORED')) allGuardOk = false;
    teardown(fx);
  }
  // ⑧ setMobileViewport(false)
  {
    resetClock(); const fx = buildFixture();
    fx.setMobileViewport(false);
    swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 100, y: 200 });
    swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
    swipeClock = 30; dispatchTouch(fx.main, 'touchend', { x: 200, y: 200 });
    const st = fx.controller.getState();
    if (fx.state.open !== false || fx.drawer.classList.contains('is-dragging') || (st !== 'IDLE' && st !== 'IGNORED')) allGuardOk = false;
    teardown(fx);
  }
  // ⑨ Safari iOS Textノード (nodeType === 3) は除外されず追跡される
  {
    resetClock(); const fx = buildFixture();
    const textNode = fx.main.createText('chat message');
    swipeClock = 0;  dispatchTouch(textNode, 'touchstart', { x: 15, y: 200 });
    const st = fx.controller.getState();
    if (st !== 'PENDING') allGuardOk = false;
    teardown(fx);
  }

  t("T9: 除外ガード（8サブ）", allGuardOk);
}

// --- T10: ゴーストクリック ---
{
  resetClock();
  const fx = buildFixture();
  fx.setOpenDirect(true);
  let listenerCalled = 0;
  fx.closeInside.addEventListener('click', () => { listenerCalled++; });

  swipeClock = 300; dispatchTouch(fx.drawer, 'touchstart', { x: 200, y: 200 });
  swipeClock = 315; dispatchTouch(fx.drawer, 'touchmove', { x: 180, y: 200 });
  swipeClock = 330; dispatchTouch(fx.drawer, 'touchmove', { x: 120, y: 200 });
  swipeClock = 335; dispatchTouch(fx.drawer, 'touchend', { x: 120, y: 200 });

  const closed = fx.state.open === false;
  swipeClock = 435; const c1 = dispatchClick(fx.closeInside);
  swipeClock = 445; const c2 = dispatchClick(fx.closeInside);
  const midCount = listenerCalled;
  swipeClock = 735; const c3 = dispatchClick(fx.closeInside);

  const cond = closed &&
    c1.defaultPrevented === true &&
    c2.defaultPrevented === true &&
    midCount === 0 &&
    listenerCalled === 1;
  t("T10: ゴーストクリック", cond);
  teardown(fx);
}

// --- T11: 純タップ ---
{
  resetClock();
  const fx = buildFixture();
  fx.setOpenDirect(true);
  let listenerCalled = 0;
  fx.closeInside.addEventListener('click', () => { listenerCalled++; });

  swipeClock = 400; dispatchTouch(fx.closeInside, 'touchstart', { x: 200, y: 200 });
  swipeClock = 410; dispatchTouch(fx.closeInside, 'touchend', { x: 200, y: 200 });
  swipeClock = 420; dispatchClick(fx.closeInside);

  t("T11: 純タップ", listenerCalled === 1);
  teardown(fx);
}

// --- T12: 状態同期 ---
{
  resetClock();
  const fx = buildFixture();
  const initOk = !fx.doc.body.classList.contains('side-open') &&
    !fx.drawer.classList.contains('open') &&
    !fx.overlay.classList.contains('open') &&
    fx.drawer.getAttribute('aria-hidden') === 'true' &&
    fx.toggle.getAttribute('aria-expanded') === 'false';

  fx.setOpenDirect(true);
  const openOk = fx.doc.body.classList.contains('side-open') &&
    fx.drawer.classList.contains('open') &&
    fx.overlay.classList.contains('open') &&
    fx.drawer.getAttribute('aria-hidden') === null &&
    fx.toggle.getAttribute('aria-expanded') === 'true';

  // T2 手順で閉じる
  swipeClock = 200; dispatchTouch(fx.drawer, 'touchstart', { x: 220, y: 200 });
  swipeClock = 215; dispatchTouch(fx.drawer, 'touchmove', { x: 205, y: 200 });
  swipeClock = 240; dispatchTouch(fx.drawer, 'touchmove', { x: 160, y: 200 });
  swipeClock = 245; dispatchTouch(fx.drawer, 'touchend', { x: 160, y: 200 });

  const closeOk = !fx.doc.body.classList.contains('side-open') &&
    !fx.drawer.classList.contains('open') &&
    !fx.overlay.classList.contains('open') &&
    fx.drawer.getAttribute('aria-hidden') === 'true' &&
    fx.toggle.getAttribute('aria-expanded') === 'false' &&
    fx.state.calls === 1;

  t("T12: 状態同期", initOk && openOk && closeOk);
  teardown(fx);
}

// --- T13: 残留なし ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 100; dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 115; dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 140; dispatchTouch(fx.main, 'touchmove', { x: 65, y: 202 });
  swipeClock = 145; dispatchTouch(fx.main, 'touchend', { x: 65, y: 202 });

  const cond = fx.drawer.style.transform === '' &&
    fx.overlay.style.opacity === '' &&
    !fx.drawer.classList.contains('is-dragging') &&
    !fx.overlay.classList.contains('is-dragging') &&
    !fx.overlay.classList.contains('is-peeking') &&
    fx.state.open === true;
  t("T13: 残留なし", cond);
  teardown(fx);
}

// --- T14: 二重初期化/destroy ---
{
  resetClock();
  const fx = buildFixture();
  const second = fx.hooks.initDrawerSwipe({
    drawerEl: fx.drawer, overlayEl: fx.overlay,
    isOpen: () => fx.state.open,
    setOpen: () => { throw new Error('二重登録で上書き禁止'); }
  });
  const sameInst = second === fx.controller;

  // 1回ドラッグ
  swipeClock = 100; dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 115; dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 140; dispatchTouch(fx.main, 'touchmove', { x: 65, y: 202 });
  swipeClock = 145; dispatchTouch(fx.main, 'touchend', { x: 65, y: 202 });
  const calls1 = fx.state.calls === 1;

  fx.controller.destroy();
  const transformClean = fx.drawer.style.transform === '';

  // 再タッチで無反応
  swipeClock = 200; dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 215; dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 240; dispatchTouch(fx.main, 'touchmove', { x: 65, y: 202 });
  swipeClock = 245; dispatchTouch(fx.main, 'touchend', { x: 65, y: 202 });
  const callsStill1 = fx.state.calls === 1;

  t("T14: 二重初期化/destroy", sameInst && calls1 && transformClean && callsStill1);
}

// --- T15: settle中の再タッチ ---
{
  resetClock();
  const fx = buildFixture();
  fx.transitionMs = 250;
  fx.setOpenDirect(true);

  swipeClock = 100; dispatchTouch(fx.drawer, 'touchstart', { x: 220, y: 200 });
  swipeClock = 115; dispatchTouch(fx.drawer, 'touchmove', { x: 205, y: 200 });
  swipeClock = 140; dispatchTouch(fx.drawer, 'touchmove', { x: 160, y: 200 });
  swipeClock = 145; dispatchTouch(fx.drawer, 'touchend', { x: 160, y: 200 });
  assert.strictEqual(fx.drawer.style.transform, 'translate3d(-280px, 0, 0)');

  // settle 保留中に新しいタッチ開始
  swipeClock = 160; dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 175; dispatchTouch(fx.main, 'touchmove', { x: 120, y: 200 });
  swipeClock = 190; dispatchTouch(fx.main, 'touchmove', { x: 150, y: 200 });

  t("T15: settle中の再タッチ", fx.drawer.style.transform === 'translate3d(-250px, 0, 0)');
  teardown(fx);
}

// --- T16: ステイル復帰 ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { id: 0, x: 15, y: 200 });
  swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { id: 0, x: 60, y: 200 });
  assert.strictEqual(fx.controller.getState(), 'DRAGGING_X');

  // touchend 欠落のまま新 touchstart (id:9)
  const t9 = { identifier: 9, clientX: 15, clientY: 200, target: fx.main };
  swipeClock = 40; dispatchTouch(fx.main, 'touchstart', { id: 9, x: 15, y: 200, allTouches: [t9] });

  const cond = fx.controller.getState() === 'PENDING' &&
    fx.controller.getDebugState().activeTouchId === 9;

  // window blur で PENDING 状態が安全に IDLE へリセットされる
  for (const l of fx.winListeners['blur'] || []) l.fn();
  const blurOk = fx.controller.getState() === 'IDLE';

  t("T16: ステイル復帰", cond && blurOk);
  teardown(fx);
}

// --- T17: デスクトップ非干渉 ---
{
  resetClock();
  const fx = buildFixture();
  fx.setMobileViewport(false);

  swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
  swipeClock = 30; dispatchTouch(fx.main, 'touchend', { x: 200, y: 200 });

  const cond = fx.controller.getState() === 'IDLE' &&
    fx.state.open === false &&
    fx.drawer.style.transform === '';

  // デスクトップ側でチャンネル切替等（setDrawerOpen(false)）が実行されても sidebar が inert / aria-hidden にならない
  fx.hooks.setDrawerOpen(false, { reason: 'switch' });
  const desktopInteractive = fx.drawer.getAttribute('aria-hidden') === null &&
    fx.drawer.inert === false &&
    !fx.drawer.hasAttribute('inert');

  t("T17: デスクトップ非干渉", cond && desktopInteractive);
  teardown(fx);
}

// --- T18: キルスイッチ＋タップ生存 ---
{
  resetClock();
  const env = createSwipeStubEnv();
  env.doc.body.dataset.drawerSwipe = 'off';
  const ret = env.sb.initAppDrawerSwipe();
  const isNull = ret === null;

  env.hooks.applyDrawerState({ drawerEl: env.drawer, overlayEl: env.overlay, toggleEl: env.toggle, open: true });
  const tapAlive = env.drawer.classList.contains('open') && env.doc.body.classList.contains('side-open');

  // dataset が存在しない（undefined）環境でも例外が発生しないことを検証
  delete env.doc.body.dataset;
  let noDatasetOk = false;
  try {
    env.sb.initAppDrawerSwipe();
    noDatasetOk = true;
  } catch (e) {
    noDatasetOk = false;
  }

  // モバイル初期化時に closed 状態の aria-hidden / inert / aria-expanded が正しく同期されることを検証
  const env2 = createSwipeStubEnv();
  env2.drawer.removeAttribute('aria-hidden');
  env2.toggle.removeAttribute('aria-expanded');
  env2.sb.initAppDrawerSwipe();
  const mobileInitOk = env2.drawer.getAttribute('aria-hidden') === 'true' &&
    env2.drawer.inert === true &&
    env2.toggle.getAttribute('aria-expanded') === 'false';

  t("T18: キルスイッチ＋タップ生存", isNull && tapAlive && noDatasetOk && mobileInitOk);
}

// --- T19: DIR_RATIO_BODY ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 100, y: 200 });
  swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 130, y: 220 });
  // dx = 30, dy = 20, 30 < 20 * 2.2 = 44
  t("T19: DIR_RATIO_BODY", fx.controller.getState() === 'SCROLLING_Y');
  teardown(fx);
}

// --- T20: touchcancel ---
{
  resetClock();
  const fx = buildFixture();
  swipeClock = 0;   dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 20;  dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 300; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
  swipeClock = 310; dispatchTouch(fx.main, 'touchcancel', { x: 200, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = dbg.path === 'cancel' && fx.state.open === true;
  t("T20: touchcancel", cond);
  teardown(fx);
}

// --- T21: 開＋右フリック ---
{
  resetClock();
  const fx = buildFixture();
  fx.setOpenDirect(true);
  swipeClock = 0;  dispatchTouch(fx.drawer, 'touchstart', { x: 100, y: 200 });
  swipeClock = 15; dispatchTouch(fx.drawer, 'touchmove', { x: 120, y: 200 });
  swipeClock = 40; dispatchTouch(fx.drawer, 'touchmove', { x: 200, y: 200 });
  swipeClock = 45; dispatchTouch(fx.drawer, 'touchend', { x: 200, y: 200 });
  const dbg = fx.controller.getDebugState();
  const cond = dbg.path === 'flick' && fx.state.open === true && fx.drawer.style.transform === '';
  t("T21: 開＋右フリック", cond);
  teardown(fx);
}

// --- T22: CSS スモーク ---
{
  const cssText = fs.readFileSync(path.join(__dirname, '..', 'public', 'style.css'), 'utf8');
  const bodyNoTouchAction = !/body\s*\{[^}]*touch-action/i.test(cssText);
  const sidebarTransform = /#sidebar\s*\{[^}]*translate3d\(-100%/i.test(cssText) &&
    /#sidebar\s*\{[^}]*transition:\s*transform/i.test(cssText);
  const noTransitionAll = !/#sidebar\s*\{[^}]*transition:\s*all/i.test(cssText) &&
    !/#sideBackdrop\s*\{[^}]*transition:\s*all/i.test(cssText);
  const backdropRules = /#sideBackdrop\s*\{[^}]*touch-action:\s*none/i.test(cssText) &&
    /#sideBackdrop\s*\{[^}]*display:\s*block/i.test(cssText);
  t("T22: CSS スモーク", bodyNoTouchAction && sidebarTransform && noTransitionAll && backdropRules);
}

// --- T23: CSS スコープ（AC-25 の正） ---
{
  let t23Passed = false;
  try {
    const cssText = fs.readFileSync(path.join(__dirname, '..', 'public', 'style.css'), 'utf8');
    let desktop = '';
    let pos = 0;
    let mobileBlockCount = 0;
    while (true) {
      const start = cssText.indexOf('@media (max-width: 768px)', pos);
      if (start < 0) { desktop += cssText.slice(pos); break; }
      desktop += cssText.slice(pos, start);
      let depth = 0, end = -1;
      for (let i = cssText.indexOf('{', start); i < cssText.length; i++) {
        if (cssText[i] === '{') depth++;
        else if (cssText[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
      }
      assert.ok(end > start, 'モバイルブロックの終端が見つかる');
      mobileBlockCount++;
      pos = end + 1;
    }
    assert.ok(mobileBlockCount >= 1, 'モバイルブロックが存在する');
    ['is-dragging', 'is-peeking', 'translate3d(-100%'].forEach(function (needle) {
      assert.strictEqual(desktop.indexOf(needle), -1, 'デスクトップ側にスワイプ用規則が漏れていない: ' + needle);
    });
    t23Passed = true;
  } catch (err) {
    t23Passed = false;
  }
  t("T23: CSS スコープ（AC-25 の正）", t23Passed);
}

// --- T24: settle中のタップ切替（P-19） ---
{
  resetClock();
  const fx = buildFixture();
  fx.transitionMs = 250;
  fx.setOpenDirect(true);

  // T2 手順で閉じる（settle 保留）
  swipeClock = 200; dispatchTouch(fx.drawer, 'touchstart', { x: 220, y: 200 });
  swipeClock = 215; dispatchTouch(fx.drawer, 'touchmove', { x: 205, y: 200 });
  swipeClock = 240; dispatchTouch(fx.drawer, 'touchmove', { x: 160, y: 200 });
  swipeClock = 245; dispatchTouch(fx.drawer, 'touchend', { x: 160, y: 200 });

  const prevCalls = fx.state.calls;
  fx.tapToggle();

  const cond = fx.drawer.style.transform === '' &&
    fx.drawer.classList.contains('open') &&
    fx.doc.body.classList.contains('side-open') &&
    fx.state.calls === prevCalls + 1;
  t("T24: settle中のタップ切替（P-19）", cond);
  teardown(fx);
}

// --- T25: 祖先 display:none の常設ダイアログは「モーダル表示中」と誤判定しない ---
// 実機不具合の回帰テスト：.modal-content[role="dialog"][aria-modal="true"] が
// 非表示の .modal.hidden の中に常設されているため、全タッチが IGNORED になっていた。
{
  resetClock();
  const fx = buildFixture();
  fx.setDialogs([fx.hiddenAncestorDialog]);
  swipeClock = 100; dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  const started = fx.controller.getState() === 'PENDING';
  swipeClock = 115; dispatchTouch(fx.main, 'touchmove', { x: 30, y: 200 });
  swipeClock = 140; dispatchTouch(fx.main, 'touchmove', { x: 80, y: 200 });
  swipeClock = 145; dispatchTouch(fx.main, 'touchend', { x: 80, y: 200 });
  const cond = started && fx.state.open === true;
  t("T25: 非表示祖先の常設ダイアログを無視", cond);
  teardown(fx);
}

// --- T26: 実際に描画されているダイアログは従来どおりスワイプをブロック ---
{
  resetClock();
  const fx = buildFixture();
  fx.setDialogs([fx.renderedDialog]);
  swipeClock = 0;  dispatchTouch(fx.main, 'touchstart', { x: 15, y: 200 });
  swipeClock = 20; dispatchTouch(fx.main, 'touchmove', { x: 200, y: 200 });
  const st = fx.controller.getState();
  const cond = fx.state.open === false && (st === 'IGNORED' || st === 'IDLE');
  t("T26: 表示中ダイアログはブロック", cond);
  teardown(fx);
}

console.log(`\n===== PASS ${pass} / FAIL ${fail} =====`);
process.exit(fail ? 1 : 0);
