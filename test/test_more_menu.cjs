// 「⋯（その他の操作）」メニューの自動テスト。
// public/app.js からモジュールをそのまま抜き出し、スタブ DOM 上で振る舞いを検証する。
// 実行: node test/test_more_menu.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");
const START = "// ---------------------------------------------------------------- その他の操作（⋯）";
const END = "\nfunction createMessageWrap(";
const s0 = src.indexOf(START);
const s1 = src.indexOf(END);
if (s0 < 0 || s1 < 0 || s1 < s0) throw new Error("モジュールを抽出できません");
const moduleSrc = src.slice(s0, s1);
if (!moduleSrc.includes("function toggleMoreMenu")) throw new Error("toggleMoreMenu が抽出範囲に無い");

// ---------------------------------------------------------------- スタブ DOM
class El {
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
    this._rect = { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
    this._w = 0;
    this._h = 0;
    const self = this;
    const set = (str) => new Set(String(str || "").split(/\s+/).filter(Boolean));
    this.classList = {
      add: (...c) => { const s = set(self.className); c.forEach((x) => s.add(x)); self.className = [...s].join(" "); },
      remove: (...c) => { const s = set(self.className); c.forEach((x) => s.delete(x)); self.className = [...s].join(" "); },
      contains: (c) => set(self.className).has(c),
    };
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; }
  contains(n) { if (n === this) return true; return this.children.some((c) => c.contains && c.contains(n)); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { const a = this.listeners[t] || []; const i = a.indexOf(f); if (i >= 0) a.splice(i, 1); }
  getBoundingClientRect() { return this._rect; }
  get offsetWidth() { return this._w; }
  get offsetHeight() { return this._h; }
  get isConnected() { return true; }
  closest(sel) {
    let cur = this;
    while (cur) {
      if (typeof sel === "string") {
        const parts = sel.split(/\s*,\s*/);
        for (const p of parts) {
          if (p.startsWith(".")) {
            const classes = p.split(".").filter(Boolean);
            if (classes.length > 0 && cur.classList && classes.every((c) => cur.classList.contains(c))) {
              return cur;
            }
          } else if (cur.tagName && cur.tagName.toLowerCase() === p.toLowerCase()) {
            return cur;
          }
        }
      }
      cur = cur.parentNode;
    }
    return null;
  }
  focus() { this._focused = true; }
  select() { this._selected = true; }
  setSelectionRange(s, e) { this._range = [s, e]; }
}

const docL = {};
const body = new El("body");
const documentStub = {
  body,
  hidden: false,
  createElement: (t) => new El(t),
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (t, f) => { (docL[t] = docL[t] || []).push(f); },
  removeEventListener: (t, f) => { const a = docL[t] || []; const i = a.indexOf(f); if (i >= 0) a.splice(i, 1); },
  execCommand: () => false,
};
const fireDoc = (t, e) => (docL[t] || []).slice().forEach((f) => f(e));
const docCount = (t) => (docL[t] || []).length;

const winL = {};
const sandbox = {
  console,
  document: documentStub,
  navigator: { clipboard: { writeText: () => {} } },
  isSecureContext: true,
  alert: () => {},
  confirm: () => true,
  innerWidth: 1200,
  innerHeight: 800,
  addEventListener: (t, f) => { (winL[t] = winL[t] || []).push(f); },
  removeEventListener: (t, f) => { const a = winL[t] || []; const i = a.indexOf(f); if (i >= 0) a.splice(i, 1); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
// アプリ側のヘルパー（呼ばれたら記録だけする）
const calls = [];
["openEdit", "openState", "openPrompt", "openLightbox", "imgUrl", "poll", "post", "checked", "flash", "toast",
 "revertIde", "switchTo", "refreshSessions", "loadIdeSessions", "syncActionControlsVisibility", "updateFabState"].forEach((n) => {
  sandbox[n] = (...a) => { calls.push([n, ...a]); };
});
sandbox.imgUrl = (f) => "/img?f=" + f;
sandbox.sid = "sid1";
sandbox.state = { setting: {} };

vm.createContext(sandbox);
vm.runInContext(moduleSrc, sandbox, { filename: "app.js#more-menu" });
const T = { toggle: sandbox.toggleMoreMenu, build: sandbox.buildMoreMenu, close: sandbox.closeMoreMenu };
if (typeof T.toggle !== "function" || typeof T.build !== "function" || typeof T.close !== "function") {
  throw new Error("関数を取得できませんでした");
}

// ---------------------------------------------------------------- テスト
let pass = 0, fail = 0;
const ok = (cond, name, extra) => {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (extra !== undefined ? "  -> " + JSON.stringify(extra) : "")); }
};
const labelsOf = (menu) => menu.children.map((c) => (c.tagName === "HR" ? "—" : c.textContent));
const menusInBody = () => body.children.filter((c) => c.classList.contains("more-menu"));
const backdropsInBody = () => body.children.filter((c) => c.classList.contains("more-backdrop"));
const reset = () => { T.close(); body.children.length = 0; delete docL.click; delete docL.keydown; delete docL.scroll; delete winL.resize; };

const mkMsg = (over = {}) => ({ id: "m1", role: "assistant", text: "hello", ...over });
const mkState = (msgs, over = {}) => ({ messages: msgs, setting: { ...(over.setting || {}) } });

console.log("\n[0] シートは本当に画面幅いっぱいか（左下に 260px の箱で貼り付かない）");
{
  const css = fs.readFileSync(path.join(__dirname, "..", "public", "style.css"), "utf8");
  const sheet = (css.match(/\.more-sheet\s*\{[^}]*\}/) || [""])[0];
  ok(/max-width:\s*none/.test(sheet), ".more-sheet が .more-menu の max-width を打ち消す", sheet.slice(0, 120));
  ok(/(^|;|\s)width:\s*100%/.test(sheet), ".more-sheet は width:100%", sheet.slice(0, 120));
}

console.log("\n[1] ⋯ メニューの中身（状態ごとの項目）");
{
  const user = { id: "u1", role: "user", text: "hi" };
  const asst = mkMsg({ image: { status: "done", file: "a.png" } });
  const msgs = [user, asst];

  reset();
  const a = T.build(asst, false, mkState(msgs));
  ok(JSON.stringify(labelsOf(a)) === JSON.stringify(
    ["🔍 拡大", "—", "📝 本文を編集", "—", "🔄 再生成", "分岐", "📋 コピー", "—", "⏪ 巻き戻し", "🗑️ 削除"]),
    "A: 画像done+前ユーザー発言+isLast=false", labelsOf(a));

  reset();
  const b = T.build(mkMsg(), true, mkState([asst], { setting: { rp_state: true } }));
  ok(JSON.stringify(labelsOf(b)) === JSON.stringify(
    ["📝 本文を編集", "🧑 キャラの状態", "—", "分岐", "📋 コピー", "—", "🗑️ 削除"]),
    "B: 画像なし+rp_state有効+前ユーザー発言なし+isLast=true", labelsOf(b));

  reset();
  const c = T.build(user, true, mkState([user, asst]));
  ok(JSON.stringify(labelsOf(c)) === JSON.stringify(
    ["✏️ 編集して再送", "—", "分岐", "📋 コピー", "—", "🗑️ 削除"]),
    "C: ユーザー発言（状態/再生成は出さない）", labelsOf(c));

  // 分岐は絵文字だと 🔀（シャッフル）に見えるので、線画のSVGアイコンを前に付ける
  const branchBtn = c.children.find((x) => x.textContent === "分岐");
  ok(!!branchBtn && branchBtn.children.some((x) => x.classList.contains("more-ico")),
    "C: 分岐の頭に SVG アイコン（.more-ico）が付く", branchBtn && branchBtn.children.map((x) => x.className));

  reset();
  const gen = mkMsg({ image: { status: "queued", file: "" } });
  const d = T.build(gen, true, mkState([user, gen]));
  ok(!labelsOf(d).includes("🔍 拡大"), "D: 画像生成中は 🔍 拡大 を出さない", labelsOf(d));

  reset();
  const ref = mkMsg({ image: { status: "done", file: "a.png" }, image_refine_status: "processing" });
  const e = T.build(ref, true, mkState([user, ref]));
  ok(!labelsOf(e).includes("🔍 拡大"), "E: 推敲中は 🔍 拡大 を出さない", labelsOf(e));

  reset();
  const st = mkMsg({ state: [{ name: "A" }] });
  const f = T.build(st, true, mkState([user, st]));
  ok(labelsOf(f).includes("🧑 キャラの状態"), "F: m.state があれば キャラの状態 を出す", labelsOf(f));
}

console.log("\n[2] 基本は ⋯ のそば、入らないときだけシート");
{
  const user = { id: "u1", role: "user", text: "hi" };
  const asst = mkMsg();
  const st = mkState([user, asst]);
  const btn = new El("button");
  btn._rect = { top: 300, right: 700, bottom: 330, left: 660, width: 40, height: 30 };

  reset();
  sandbox.innerWidth = 1200;
  T.toggle(btn, asst, true, st);
  ok(menusInBody().length === 1 && menusInBody()[0].classList.contains("more-popup"), "PC: ポップアップが出る");
  ok(backdropsInBody().length === 0, "PC: 背景は出ない");
  ok(menusInBody()[0].style.top === "336px", "PC: ボタンの下に開く (top)", menusInBody()[0].style.top);
  ok(btn.attrs["aria-expanded"] === "true", "PC: aria-expanded=true");
  ok(menusInBody()[0].style.visibility === "", "PC: 位置決め後に表示される");

  // 狭い幅でも、⋯ の上下に余地があるなら「その場」に出す（左下に貼り付かせない）
  reset();
  sandbox.innerWidth = 400;
  sandbox.innerHeight = 800;
  T.toggle(btn, asst, true, st);
  ok(menusInBody().length === 1 && menusInBody()[0].classList.contains("more-popup"), "狭い幅でも余地があればポップアップ", menusInBody().map((x) => x.className));
  ok(backdropsInBody().length === 0, "狭い幅: 背景は出さない（発言が見えたまま）");

  // 画面が低くて上下どちらにも入らないときだけシート
  reset();
  sandbox.innerWidth = 400;
  sandbox.innerHeight = 360;
  const btnMid = new El("button");
  btnMid._rect = { top: 150, right: 60, bottom: 180, left: 20, width: 40, height: 30 };
  T.toggle(btnMid, asst, true, st);
  ok(menusInBody().length === 1 && menusInBody()[0].classList.contains("more-sheet"), "入らない高さ: シートに逃げる", menusInBody().map((x) => x.className));
  ok(backdropsInBody().length === 1, "入らない高さ: 背景が出る");

  // PC 幅でも { sheet: true } を指定するとシートが出る（長押しの経路）
  reset();
  sandbox.innerWidth = 1200;
  sandbox.innerHeight = 800;
  T.toggle(btn, asst, true, st, { sheet: true });
  ok(menusInBody().length === 1 && menusInBody()[0].classList.contains("more-sheet"), "opts.sheet=true: PC幅でもシートが出る");
  ok(backdropsInBody().length === 1, "opts.sheet=true: 背景が出る");

  // 下端では上にフリップ
  reset();
  sandbox.innerWidth = 1200;
  sandbox.innerHeight = 800;
  const btnLow = new El("button");
  btnLow._rect = { top: 740, right: 700, bottom: 770, left: 660, width: 40, height: 30 };
  T.toggle(btnLow, asst, true, st);
  // 既定のメニュー高さ 220 想定 -> 740 - 220 - 6 = 514
  ok(menusInBody()[0].style.top === "514px", "下端では上にフリップ", menusInBody()[0].style.top);
  sandbox.innerHeight = 800;
}

console.log("\n[2b] 左右の寄せ（押したボタン側の端を揃え、画面内向きに開く）");
{
  const asst = mkMsg();
  const st = mkState([{ id: "u1", role: "user", text: "hi" }, asst]);

  // AI 発言: ⋯ は左端にある -> メニューの左端を ⋯ の左端に揃える
  const left = new El("button");
  left._rect = { top: 300, right: 46, bottom: 330, left: 12, width: 34, height: 30 };
  reset();
  sandbox.innerWidth = 400;
  sandbox.innerHeight = 800;
  T.toggle(left, asst, true, st);
  ok(menusInBody()[0].style.left === "12px", "左寄せの ⋯ : 左端を揃える", menusInBody()[0].style.left);
  ok(menusInBody()[0].style.transformOrigin === "top left", "左寄せの ⋯ : 出現の起点は左上", menusInBody()[0].style.transformOrigin);

  // ユーザー発言: ⋯ は右端にある -> メニューの右端を ⋯ の右端に揃える
  const right = new El("button");
  right._rect = { top: 300, right: 364, bottom: 330, left: 330, width: 34, height: 30 };
  reset();
  T.toggle(right, asst, true, st);
  ok(menusInBody()[0].style.left === "188px", "右寄せの ⋯ : 右端を揃える（364 - 176）", menusInBody()[0].style.left);
  ok(menusInBody()[0].style.transformOrigin === "top right", "右寄せの ⋯ : 出現の起点は右上", menusInBody()[0].style.transformOrigin);

  // 画面外へはみ出さない（左端 8px クランプ）
  const edge = new El("button");
  edge._rect = { top: 300, right: 20, bottom: 330, left: 4, width: 16, height: 30 };
  reset();
  T.toggle(edge, asst, true, st);
  ok(menusInBody()[0].style.left === "8px", "画面左端でクランプ", menusInBody()[0].style.left);

  // 上に逃げたときの起点は左下
  const low = new El("button");
  low._rect = { top: 740, right: 46, bottom: 770, left: 12, width: 34, height: 30 };
  reset();
  sandbox.innerWidth = 400;
  T.toggle(low, asst, true, st);
  ok(menusInBody()[0].style.transformOrigin === "bottom left", "上に開くときの起点は左下", menusInBody()[0].style.transformOrigin);
}

console.log("\n[3] 開閉");
{
  const user = { id: "u1", role: "user", text: "hi" };
  const asst = mkMsg();
  const st = mkState([user, asst]);
  const btn = new El("button");
  btn._rect = { top: 300, right: 700, bottom: 330, left: 660, width: 40, height: 30 };
  sandbox.innerWidth = 1200;
  const outside = new El("div");

  reset();
  T.toggle(btn, asst, true, st);
  fireDoc("click", { target: outside });
  ok(menusInBody().length === 0, "外側クリックで閉じる");
  ok(btn.attrs["aria-expanded"] === "false", "閉じたら aria-expanded=false");

  reset();
  btn._focused = false;
  T.toggle(btn, asst, true, st);
  fireDoc("keydown", { key: "Escape", stopPropagation: () => {} });
  ok(menusInBody().length === 0, "Esc で閉じる");
  ok(btn._focused === true, "Esc で開いたボタンにフォーカスが戻る");

  // pinWrap の検証
  reset();
  const wrap = new El("div");
  wrap.className = "msg-wrap user";
  wrap.appendChild(btn);
  T.toggle(btn, asst, true, st);
  ok(wrap.classList.contains("actions-pinned"), "開いたとき actions-pinned が付く");
  T.close();
  ok(!wrap.classList.contains("actions-pinned"), "閉じたら actions-pinned が外れる");

  reset();
  T.toggle(btn, asst, true, st);
  fireDoc("click", { target: menusInBody()[0].children[0] });
  ok(menusInBody().length === 1, "メニュー内クリックでは閉じない");

  reset();
  T.toggle(btn, asst, true, st);
  fireDoc("click", { target: btn });
  ok(menusInBody().length === 1, "ボタン自身のクリックでは閉じない（トグルに任せる）");

  reset();
  T.toggle(btn, asst, true, st);
  T.toggle(btn, asst, true, st);
  ok(menusInBody().length === 0, "同じボタンをもう一度押すと閉じる");

  reset();
  T.toggle(btn, asst, true, st);
  const btn2 = new El("button");
  btn2._rect = { top: 100, right: 700, bottom: 130, left: 660, width: 40, height: 30 };
  T.toggle(btn2, asst, true, st);
  ok(menusInBody().length === 1 && menusInBody()[0].parentNode === body, "別のボタンを開くと1つだけになる");

  ok(docCount("click") === 1 && docCount("keydown") === 1 && docCount("scroll") === 1, "リスナーは1組だけ");

  reset();
  ok(docCount("click") === 0 && docCount("keydown") === 0 && docCount("scroll") === 0, "閉じたらリスナーを外す");
  ok((winL.resize || []).length === 0, "閉じたら resize も外す");

  reset();
  let threw = false;
  try { T.close(); } catch (e) { threw = true; }
  ok(!threw, "何も開いていない時の close は安全");
}

console.log("\n[4] スクロールでは閉じない（位置を追いかける）");
{
  const user = { id: "u1", role: "user", text: "hi" };
  const asst = mkMsg();
  const st = mkState([user, asst]);
  const btn = new El("button");
  btn._rect = { top: 300, right: 700, bottom: 330, left: 660, width: 40, height: 30 };
  sandbox.innerWidth = 1200;
  reset();
  T.toggle(btn, asst, true, st);
  ok(menusInBody()[0].style.top === "336px", "スクロール前の位置");
  btn._rect = { top: 200, right: 700, bottom: 230, left: 660, width: 40, height: 30 };
  fireDoc("scroll", {});
  ok(menusInBody().length === 1, "スクロールしても開いたまま");
  ok(menusInBody()[0].style.top === "236px", "スクロールで位置を追う", menusInBody()[0].style.top);
}

console.log("\n[5] メニュー項目のクリックで操作が走り、閉じる");
(async () => {
  const user = { id: "u1", role: "user", text: "hi" };
  const asst = mkMsg();
  const st = mkState([user, asst]);
  const btn = new El("button");
  btn._rect = { top: 300, right: 700, bottom: 330, left: 660, width: 40, height: 30 };
  sandbox.innerWidth = 1200;
  reset();
  calls.length = 0;
  T.build(asst, true, st).children.find((c) => c.textContent === "📝 本文を編集").onclick();
  ok(calls.some((c) => c[0] === "openEdit"), "「本文を編集」で openEdit を呼ぶ", calls.map((c) => c[0]));

  // コピー成功
  calls.length = 0;
  sandbox.isSecureContext = true;
  let copied = null;
  sandbox.navigator.clipboard = {
    writeText: async (t) => { copied = t; },
  };
  T.build(asst, true, st).children.find((c) => c.textContent === "📋 コピー").onclick();
  await new Promise((r) => setTimeout(r, 0));
  ok(copied === asst.text, "「コピー」で本文がクリップボードに入る", copied);
  ok(calls.some((c) => c[0] === "toast" && c[1] === "コピーしました"), "「コピー」成功でトーストが出る", calls);

  // コピー失敗
  calls.length = 0;
  sandbox.navigator.clipboard = {
    writeText: async () => { throw new Error("clipboard denied"); },
  };
  sandbox.document.execCommand = () => false;
  T.build(asst, true, st).children.find((c) => c.textContent === "📋 コピー").onclick();
  await new Promise((r) => setTimeout(r, 0));
  ok(calls.some((c) => c[0] === "toast" && c[1] === "コピーできませんでした"), "「コピー」失敗でエラーのトーストが出る", calls);

  console.log(`\n===== PASS ${pass} / FAIL ${fail} =====`);
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
