// モバイル レイアウト仕様 v1.0 の小物（状態チップ・ツール（旧「操作を表示」）/reveal・footBar 移送・
// 作中の時間モーダルのクランプ）の自動テスト。
// public/app.js から該当モジュールを抜き出し、スタブ DOM 上で振る舞いを検証する。
// 実行: node test/test_mobile_layout.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");

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

console.log("[2] ツール（旧「操作を表示」） / reveal はオーバーレイ中に出さない");
sandbox.syncActionControlsVisibility();
t("モバイル+RP+オーバーレイ無し -> トグル表示", !els.mobileActionToggle.classList.contains("hidden"));
t("reveal は隠れたまま", els.mobileReveal.classList.contains("hidden"));
els.mobileReveal.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("reveal 表示中もトグルは押せる（隠さない）", !els.mobileActionToggle.classList.contains("hidden"));
t("reveal 表示中はトグルが「✕ 閉じる」になる", els.mobileActionToggle.textContent === "✕ 閉じる");
els.mobileReveal.classList.add("hidden");
sandbox.syncActionControlsVisibility();
t("reveal を閉じたら「ツール」に戻る", els.mobileActionToggle.textContent === "ツール");
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
t("RPオフでもトグルは表示（🖼でRPを切っても消えない）", !els.mobileActionToggle.classList.contains("hidden"));
t("RPオフで 🖼 の .on が外れる", !els.revealRpToggle.classList.contains("on"));
sandbox.rpMode = true;
sandbox.syncActionControlsVisibility();
t("RPオンで 🖼 が .on になる", els.revealRpToggle.classList.contains("on"));

els.mobileReveal.classList.add("hidden");
documentStub.activeElement = els.attachList;   // footer の中にフォーカスがある状態
sandbox.syncActionControlsVisibility();
t("入力中でもトグルは隠さない", !els.mobileActionToggle.classList.contains("hidden"));
els.mobileReveal.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("入力中はメニューを畳む", els.mobileReveal.classList.contains("hidden"));
documentStub.activeElement = null;
els.mobileReveal.classList.add("hidden");
sandbox.syncActionControlsVisibility();

console.log("[2b] 中央の「ツール」は入力中だけ青い「送信」に入れ替わる（左手でも届く送信）");
els.messageInput.value = "";
sandbox.attaching = [];
documentStub.activeElement = els.messageInput;   // 入力中（フォーカスあり）
sandbox.syncActionControlsVisibility();
t("入力中でも空ならツール", els.mobileActionToggle.textContent === "ツール");
t("空なら send-mode は付かない", !els.mobileActionToggle.classList.contains("send-mode"));
els.messageInput.value = "こんにちは";
sandbox.syncActionControlsVisibility();
t("入力中に文字あり -> 送信", els.mobileActionToggle.textContent === "送信");
t("入力中に文字あり -> send-mode（青くなる）", els.mobileActionToggle.classList.contains("send-mode"));
sandbox.sending = true;
sandbox.syncActionControlsVisibility();
t("送信中は押しても無反応の送信を出さない", els.mobileActionToggle.textContent === "ツール");
sandbox.sending = false;
sandbox.hasWaitingInput = () => true;
sandbox.syncActionControlsVisibility();
t("応答待ち中もツール", els.mobileActionToggle.textContent === "ツール");
sandbox.hasWaitingInput = () => false;
els.messageInput.value = "";
sandbox.attaching = [{ name: "a.png", file: {} }];
sandbox.syncActionControlsVisibility();
t("添付だけでも送信", els.mobileActionToggle.textContent === "送信");
// 入力を止めれば（キーボードを閉じれば）ツールに戻る＝レアケースのツールはこれで開ける
documentStub.activeElement = null;
els.messageInput.value = "こんにちは";
sandbox.attaching = [];
sandbox.syncActionControlsVisibility();
t("入力を止めたらツールに戻る（文字が残っていても）", els.mobileActionToggle.textContent === "ツール");
t("入力を止めたら send-mode も外れる", !els.mobileActionToggle.classList.contains("send-mode"));
documentStub.activeElement = null;
sandbox.attaching = [];
els.messageInput.value = "こんにちは";
els.mobileReveal.classList.remove("hidden");
sandbox.syncActionControlsVisibility();
t("メニューを開いている間は「✕ 閉じる」が優先", els.mobileActionToggle.textContent === "✕ 閉じる");
els.mobileReveal.classList.add("hidden");
els.messageInput.value = "";
sandbox.syncActionControlsVisibility();

console.log("[3] footBar は「今見えている状態」から配置を導出する");
mobileOn = false;
sandbox.syncFootBarPlacement();
t("デスクトップ: footer へ", els.footBar.parentElement === footer);
mobileOn = true;
sandbox.syncFootBarPlacement();
t("モバイル: ドロワー下段へ", els.footBar.parentElement === els.sideModelContainer);
els.modelSheet.classList.remove("hidden");
sandbox.syncFootBarPlacement();
t("シート開: シート内へ", els.footBar.parentElement === els.modelSheetSlot);
els.modelSheet.classList.add("hidden");
sandbox.syncFootBarPlacement();
t("シート閉（背景タップ相当）: ドロワー下段へ戻る", els.footBar.parentElement === els.sideModelContainer);
mobileOn = false;
sandbox.syncFootBarPlacement();
t("デスクトップ復帰: footer へ", els.footBar.parentElement === footer);
t("attachList より前に戻す", footer.children.indexOf(els.footBar) < footer.children.indexOf(els.attachList));

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
t("押している間は「送信」のまま（ツールに戻らない）", els.mobileActionToggle.textContent === "送信");
els.mobileActionToggle.fire("pointerup", { clientX: 100, clientY: 100 });
t("指を離すと send() が呼ばれる（click が別要素に落ちても送れる）", sendCount === 1);
els.mobileActionToggle.onclick();   // click が来ても
t("click が重なっても二重送信しない", sendCount === 1);
t("ツールのメニューは開かない", els.mobileReveal.classList.contains("hidden"));

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
t("前提: 入力していないときは「ツール」", els.mobileActionToggle.textContent === "ツール");
els.mobileActionToggle.fire("pointerdown", { button: 0 });
els.mobileActionToggle.fire("pointerup");
els.mobileActionToggle.onclick();
t("ツールではメニューが開く", !els.mobileReveal.classList.contains("hidden"));
t("ツールでは send() を呼ばない", sendCount === 1);

console.log(`\n===== PASS ${pass} / FAIL ${fail} =====`);
process.exit(fail ? 1 : 0);
