// ユーザー発言カードのホバー操作・モバイル長押し操作の自動テスト
// 実行: node test/test_user_msg_actions.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const appPath = path.join(__dirname, "..", "public", "app.js");
const stylePath = path.join(__dirname, "..", "public", "style.css");
const appSrc = fs.readFileSync(appPath, "utf8");
const styleSrc = fs.readFileSync(stylePath, "utf8");

// マーカー区間の抽出
const START_MARKER = "// ---- ユーザー発言カードの操作（テスト抽出開始）";
const END_MARKER = "// ---- ユーザー発言カードの操作（テスト抽出終了）";
const s0 = appSrc.indexOf(START_MARKER);
const s1 = appSrc.indexOf(END_MARKER);
if (s0 < 0 || s1 < 0 || s1 <= s0) {
  throw new Error("app.js から操作ブロックを抽出できませんでした（マーカーが見つかりません）");
}
const moduleSrc = appSrc.slice(s0, s1);

// スタブ DOM
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
    this.dataset = {};
    this.onclick = null;
    this._rect = { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
    this._w = 0;
    this._h = 0;
    this._innerHTML = "";
    const self = this;
    const set = (str) => new Set(String(str || "").split(/\s+/).filter(Boolean));
    this.classList = {
      add: (...c) => { const s = set(self.className); c.forEach((x) => s.add(x)); self.className = [...s].join(" "); },
      remove: (...c) => { const s = set(self.className); c.forEach((x) => s.delete(x)); self.className = [...s].join(" "); },
      contains: (c) => set(self.className).has(c),
    };
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(html) {
    this._innerHTML = String(html || "");
    if (this._innerHTML.includes("<svg")) {
      const svg = new El("svg");
      if (this._innerHTML.includes('aria-hidden="true"')) svg.setAttribute("aria-hidden", "true");
      if (this._innerHTML.includes('focusable="false"')) svg.setAttribute("focusable", "false");
      this.children = [svg];
      svg.parentNode = this;
    }
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  contains(n) {
    if (n === this) return true;
    return this.children.some((c) => c.contains && c.contains(n));
  }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) {
    const a = this.listeners[t] || [];
    const i = a.indexOf(f);
    if (i >= 0) a.splice(i, 1);
  }
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
  getBoundingClientRect() { return this._rect; }
  get offsetWidth() { return this._w; }
  get offsetHeight() { return this._h; }
  get isConnected() { return true; }
}

const docL = {};
const body = new El("body");
const documentStub = {
  body,
  hidden: false,
  activeElement: null,
  createElement: (t) => new El(t),
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (t, f) => { (docL[t] = docL[t] || []).push(f); },
  removeEventListener: (t, f) => {
    const a = docL[t] || [];
    const i = a.indexOf(f);
    if (i >= 0) a.splice(i, 1);
  },
  execCommand: () => false,
};

const calls = [];
const sandbox = {
  console,
  document: documentStub,
  navigator: {
    clipboard: {
      writeText: async () => {},
    },
    vibrate: () => {},
  },
  isSecureContext: true,
  setTimeout,
  clearTimeout,
  toast: (...a) => { calls.push(["toast", ...a]); },
  openEdit: (...a) => { calls.push(["openEdit", ...a]); },
  toggleMoreMenu: (...a) => { calls.push(["toggleMoreMenu", ...a]); },
  isOverlayOpen: () => false,
  moreOpen: { cur: null },
  state: { messages: [] },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(moduleSrc, sandbox, { filename: "app.js#user-actions" });

let pass = 0, fail = 0;
const ok = (cond, name, extra) => {
  if (cond) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (extra !== undefined ? "  -> " + JSON.stringify(extra) : "")); }
};

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  console.log("\n[A] buildMsgHoverActions (通常ユーザー発言)");
  {
    calls.length = 0;
    const user = { id: "u1", role: "user", text: "こんにちは" };
    const st = { setting: {} };
    const box = sandbox.buildMsgHoverActions(user, false, st);

    ok(box.className === "msg-hover-actions", "コンテナのクラスが msg-hover-actions");
    ok(box.attrs["role"] === "group", "role=group");
    ok(box.attrs["aria-label"] === "この発言の操作", "aria-label=この発言の操作");
    ok(box.children.length === 3, "子が 3 つ（編集・コピー・⋯）", box.children.length);

    // 順序: 編集 -> コピー -> ⋯
    const [edit, copy, more] = box.children;
    ok(edit.classList.contains("msg-action-btn"), "編集ボタンに msg-action-btn");
    ok(edit.title === "この入力を編集して再送", "編集ボタンの title", edit.title);
    ok(edit.attrs["aria-label"] === "この入力を編集して再送", "編集ボタンの aria-label", edit.attrs["aria-label"]);
    ok(edit.innerHTML.includes('aria-hidden="true"') && edit.innerHTML.includes('focusable="false"'), "編集アイコン SVG 属性");

    ok(copy.classList.contains("msg-action-btn"), "コピーボタンに msg-action-btn");
    ok(copy.title === "本文をコピー", "コピーボタンの title", copy.title);
    ok(copy.attrs["aria-label"] === "本文をコピー", "コピーボタンの aria-label", copy.attrs["aria-label"]);
    ok(copy.innerHTML.includes('aria-hidden="true"') && copy.innerHTML.includes('focusable="false"'), "コピーアイコン SVG 属性");

    ok(more.classList.contains("btn-more") && more.classList.contains("msg-action-btn"), "⋯ ボタンに btn-more と msg-action-btn");
    ok(more.textContent === "⋯", "⋯ ボタンの textContent");
    ok(more.title === "その他の操作", "⋯ ボタンの title");
    ok(more.attrs["aria-haspopup"] === "true", "⋯ ボタンの aria-haspopup");
    ok(more.attrs["aria-expanded"] === "false", "⋯ ボタンの aria-expanded=false");

    // クリックイベント
    calls.length = 0;
    edit.onclick({ stopPropagation: () => {} });
    ok(calls.some((c) => c[0] === "openEdit" && c[1] === user), "編集クリックで openEdit(m)");

    calls.length = 0;
    copy.onclick({ stopPropagation: () => {} });
    await wait(10);
    ok(calls.some((c) => c[0] === "toast" && c[1] === "コピーしました"), "コピークリックで copyMessageText(m)");

    calls.length = 0;
    more.onclick();
    ok(calls.some((c) => c[0] === "toggleMoreMenu" && c[1] === more && c[2] === user), "⋯ クリックで toggleMoreMenu");
  }

  console.log("\n[B] buildMsgHoverActions (本文空の発言)");
  {
    const emptyUser = { id: "u2", role: "user", text: "   \n\t " };
    const box = sandbox.buildMsgHoverActions(emptyUser, false, {});
    ok(box.children.length === 1, "本文空なら子は 1 つ（⋯ のみ）", box.children.length);
    ok(box.children[0].classList.contains("btn-more"), "残った子が ⋯ ボタン");
  }

  console.log("\n[C] createLongPressController");
  {
    const el = new El("div");

    // 1. 80ms 後に発火（delay 60ms）
    let firedTarget = null;
    const lp1 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: (target) => { firedTarget = target; },
    });
    lp1.start(100, 100, el);
    ok(lp1.active === true, "start で active");
    await wait(90);
    ok(lp1.fired === true && firedTarget === el, "80ms 後に発火し fired=true");

    // 2. 20ms で end() -> 発火しない
    firedTarget = null;
    const lp2 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: (target) => { firedTarget = target; },
    });
    lp2.start(100, 100, el);
    await wait(20);
    const did2 = lp2.end();
    ok(did2 === false, "未発火時の end() は false を返す");
    await wait(90);
    ok(lp2.fired === false && firedTarget === null, "end() 後はタイマーがキャンセルされ発火しない");

    // 3. 11px 移動 -> キャンセル
    firedTarget = null;
    const lp3 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: (target) => { firedTarget = target; },
    });
    lp3.start(100, 100, el);
    lp3.move(111, 100);
    ok(lp3.active === false, "11px 移動で active=false（キャンセル）");
    await wait(90);
    ok(lp3.fired === false && firedTarget === null, "11px 移動後は発火しない");

    // 4. 9px 移動 -> 許容範囲内で発火
    firedTarget = null;
    const lp4 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: (target) => { firedTarget = target; },
    });
    lp4.start(100, 100, el);
    lp4.move(109, 100);
    await wait(90);
    ok(lp4.fired === true && firedTarget === el, "9px 移動なら 80ms 後に発火する");

    // 5. isEnabled が false -> start() が false
    let enabled = false;
    const lp5 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      isEnabled: () => enabled,
      onLongPress: () => {},
    });
    const sRes = lp5.start(100, 100, el);
    ok(sRes === false && lp5.active === false, "isEnabled=false なら start() は false");

    // 6. trigger() で即時発火＆二重発火なし
    let triggerCount = 0;
    const lp6 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: () => { triggerCount++; },
    });
    lp6.start(100, 100, el);
    const trigOk = lp6.trigger();
    ok(trigOk === true && lp6.fired === true && triggerCount === 1, "trigger() でタイマー満了前に即時発火");
    await wait(90);
    ok(triggerCount === 1, "タイマー満了時に二重発火しない");

    // 7. 発火中の cancel() -> fired のまま
    lp6.cancel();
    ok(lp6.fired === true, "発火中の cancel() でも fired のまま（scroll で壊れない）");

    // 8. end() で解除
    const didEnd = lp6.end();
    ok(didEnd === true && lp6.fired === false && lp6.active === false, "end() で fired=false / active=false");

    // 9. 二重 start でタイマー 1 本
    let count9 = 0;
    const lp9 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: () => { count9++; },
    });
    lp9.start(100, 100, el);
    await wait(25);
    lp9.start(100, 100, el);
    await wait(45);
    ok(count9 === 0, "二重 start 時に 1 本目のタイマーは破棄される");
    await wait(35);
    ok(count9 === 1, "2本目のタイマーで 1 回だけ発火する");

    // 10. onLongPress が例外を投げても swallowNextClick が残留せず reset される
    const lp10 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: () => { throw new Error("crash"); },
    });
    lp10.start(100, 100, el);
    const trigRes = lp10.trigger();
    ok(trigRes === false && lp10.active === false, "onLongPress 例外時は false を返し reset される");

    // 11. target.isConnected === false のときは trigger が false を返し発火しない
    let count11 = 0;
    const disconnectedEl = new El("div");
    Object.defineProperty(disconnectedEl, "isConnected", { get: () => false });
    const lp11 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      onLongPress: () => { count11++; },
    });
    lp11.start(100, 100, disconnectedEl);
    await wait(90);
    ok(count11 === 0 && lp11.fired === false, "target.isConnected===false なら発火せず中断");

    // 12. start 後、trigger 前に isEnabled が false になったら発火しない
    let dynamicEnabled = true;
    let count12 = 0;
    const lp12 = sandbox.createLongPressController({
      delay: 60,
      tolerance: 10,
      isEnabled: () => dynamicEnabled,
      onLongPress: () => { count12++; },
    });
    lp12.start(100, 100, el);
    dynamicEnabled = false;
    await wait(90);
    ok(count12 === 0 && lp12.fired === false, "タイマー中に isEnabled が false になれば発火しない");
  }

  console.log("\n[D] openUserMsgSheet");
  {
    const u1 = { id: "u1", role: "user", text: "1" };
    const a1 = { id: "a1", role: "assistant", text: "2" };
    const u2 = { id: "u2", role: "user", text: "3" };
    sandbox.state = { messages: [u1, a1, u2] };

    // u1 (isLast = false)
    calls.length = 0;
    const w1 = new El("div");
    w1.dataset.mid = "u1";
    const ok1 = sandbox.openUserMsgSheet(w1);
    ok(ok1 === true, "openUserMsgSheet(u1) 成功");
    ok(calls.some((c) => c[0] === "toggleMoreMenu" && c[1] === w1 && c[2] === u1 && c[3] === false && c[5]?.sheet === true && c[5]?.pinWrap === w1),
      "toggleMoreMenu が wrap, m, isLast=false, {sheet:true, pinWrap:wrap} で呼ばれる");

    // dataset 無しで getAttribute('data-mid') のみの場合も動作する
    calls.length = 0;
    const w1Fallback = new El("div");
    w1Fallback.dataset = {};
    w1Fallback.setAttribute("data-mid", "u1");
    const okFallback = sandbox.openUserMsgSheet(w1Fallback);
    ok(okFallback === true, "dataset が無くても getAttribute('data-mid') で解決できる");

    // u2 (isLast = true)
    calls.length = 0;
    const w2 = new El("div");
    w2.dataset.mid = "u2";
    const ok2 = sandbox.openUserMsgSheet(w2);
    ok(ok2 === true, "openUserMsgSheet(u2) 成功");
    ok(calls.some((c) => c[0] === "toggleMoreMenu" && c[3] === true), "isLast === true が渡る");

    // a1 (assistant) -> 開かない
    calls.length = 0;
    const wa = new El("div");
    wa.dataset.mid = "a1";
    const oka = sandbox.openUserMsgSheet(wa);
    ok(oka === false && calls.length === 0, "assistant の id では開かない");

    // 存在しない id -> 開かない
    calls.length = 0;
    const wx = new El("div");
    wx.dataset.mid = "unknown";
    const okx = sandbox.openUserMsgSheet(wx);
    ok(okx === false && calls.length === 0, "存在しない id では開かない");
  }

  console.log("\n[E] CSS 静的検証 (public/style.css)");
  {
    ok(styleSrc.includes(".msg-hover-actions") && styleSrc.includes("opacity: 0") && styleSrc.includes("pointer-events: none"),
      ".msg-hover-actions に opacity: 0 と pointer-events: none");
    ok(/@media\s*\(hover:\s*hover\)\s*and\s*\(pointer:\s*fine\)/.test(styleSrc) &&
       styleSrc.includes(".msg-wrap.user:hover .msg-hover-actions"),
      "@media (hover: hover) 内に :hover .msg-hover-actions");
    ok(styleSrc.includes(".msg-wrap.user.kbd-focus .msg-hover-actions") &&
       styleSrc.includes(".msg-wrap.user.actions-pinned .msg-hover-actions"),
      ".actions-pinned / .kbd-focus の表示規則");
    ok(/@media\s*\(hover:\s*none\),\s*\(pointer:\s*coarse\)/.test(styleSrc) &&
       styleSrc.includes(".msg-wrap.user .msg-hover-actions { display: none; }"),
      "@media (hover: none), (pointer: coarse) に .msg-hover-actions { display: none; }");
    ok(/@media\s*\(any-pointer:\s*coarse\)/.test(styleSrc) &&
       styleSrc.includes("-webkit-touch-callout: none;") &&
       styleSrc.includes("user-select: none;"),
      "@media (any-pointer: coarse) に user-select: none と -webkit-touch-callout: none");
    ok(styleSrc.includes(".msg-wrap.user .choices-container.hover-only-bar { display: none; }"),
      ".hover-only-bar の非表示規則");
    ok(styleSrc.includes(".msg-more-a11y"), ".msg-more-a11y の定義が存在する");
  }

  console.log("\n[F] JS 静的検証 (public/app.js)");
  {
    ok(appSrc.includes('document.addEventListener("click"') &&
       appSrc.includes("swallowNextClick = false;") &&
       appSrc.includes("stopImmediatePropagation()"),
      "click capture での swallowNextClick と stopImmediatePropagation");
    ok(appSrc.includes('document.addEventListener("contextmenu"') &&
       appSrc.includes("isTouchContextMenu"),
      "contextmenu でのタッチ由来ネイティブメニュー抑止");
    ok(appSrc.includes('addEventListener("touchmove", lockTouchMove, { passive: false })'),
      "touchmove の passive: false ロック");
    ok(appSrc.includes('if (m.role === "user") {\n    bar.appendChild(buildMsgHoverActions(m, isLast, s));') ||
       appSrc.includes('if (m.role === "user") {\r\n    bar.appendChild(buildMsgHoverActions(m, isLast, s));'),
      "createMessageWrap が m.role === 'user' のときだけ buildMsgHoverActions を呼ぶ");
    ok(appSrc.includes("bar.appendChild(makeMoreButton(m, isLast, s));"),
      "アシスタント側は makeMoreButton を .choices-container に直接 append");
    ok(appSrc.includes("if (isBusyUserMsg(m)) {") &&
       appSrc.includes("応答中は操作できません。停止してから操作してください。"),
      "toggleMoreMenu 先頭に isBusyUserMsg ガード");
    ok(appSrc.includes('if (wrap.classList && wrap.classList.contains("is-busy")) return false;'),
      "initUserMsgLongPress の begin に .is-busy スキップ");
  }

  console.log("\n[G] 送信中（.is-busy）");
  {
    // 1. isBusyUserMsg の判定
    ok(sandbox.isBusyUserMsg({ role: "user", status: "pending" }) === true, "user + pending -> true");
    ok(sandbox.isBusyUserMsg({ role: "user", status: "processing" }) === true, "user + processing -> true");
    ok(sandbox.isBusyUserMsg({ role: "user", status: "answered" }) === false, "user + answered -> false");
    ok(sandbox.isBusyUserMsg({ role: "user", status: "error" }) === false, "user + error -> false");
    ok(sandbox.isBusyUserMsg({ role: "user" }) === false, "user + status なし -> false");
    ok(sandbox.isBusyUserMsg({ role: "assistant", status: "pending" }) === false, "assistant + pending -> false");
    ok(sandbox.isBusyUserMsg(null) === false, "null -> false");

    // 2. toggleMoreMenu の送信中ガード（実ソースから抽出して実行）
    const sTog0 = appSrc.indexOf("function toggleMoreMenu(");
    const sTog1 = appSrc.indexOf(START_MARKER);
    if (sTog0 < 0 || sTog1 <= sTog0) throw new Error("toggleMoreMenu を抽出できません");
    const togSrc = appSrc.slice(sTog0, sTog1);

    const togSandbox = {
      isBusyUserMsg: sandbox.isBusyUserMsg,
      toast: (...a) => { calls.push(["toast", ...a]); },
      moreOpen: { cur: null },
      closeMoreMenu: () => {},
      buildMoreMenu: () => new El("div"),
      isNarrowScreen: () => false,
      document: documentStub,
      window: sandbox,
      updateFabState: () => {},
    };
    vm.createContext(togSandbox);
    vm.runInContext(togSrc, togSandbox);

    calls.length = 0;
    const busyUser = { id: "u_busy", role: "user", status: "pending" };
    togSandbox.toggleMoreMenu(new El("button"), busyUser, false, {});
    ok(calls.some((c) => c[0] === "toast" && c[1].includes("応答中は操作できません")),
      "pending のユーザー発言で toggleMoreMenu を呼ぶと toast で拒否される");

    // 3. openUserMsgSheet は pending の発言で false を返し、toggleMoreMenu を呼ばない
    calls.length = 0;
    sandbox.state = { messages: [busyUser] };
    const wBusy = new El("div");
    wBusy.dataset.mid = "u_busy";
    const rBusy = sandbox.openUserMsgSheet(wBusy);
    ok(rBusy === false && calls.length === 0, "pending の発言は openUserMsgSheet が false で拒否");

    // 4. createMessageWrap に is-busy 付与コードが含まれること
    ok(appSrc.includes('if (isBusyUserMsg(m)) wrap.classList.add("is-busy");'),
      "createMessageWrap に is-busy 付与コードが存在する");

    // 5. openEdit の送信中ガード（実ソースから抽出して実行）
    const sEdit0 = appSrc.indexOf("function openEdit(");
    const sEdit1 = appSrc.indexOf("async function saveEdit(");
    if (sEdit0 < 0 || sEdit1 <= sEdit0) throw new Error("openEdit を抽出できません");
    const editSrc = "var editTarget = null;\n" + appSrc.slice(sEdit0, sEdit1);

    const editSandbox = {
      hasWaitingInput: () => true,
      toast: (...a) => { calls.push(["toast", ...a]); },
      $: () => new El("div"),
      updateFabState: () => {},
    };
    vm.createContext(editSandbox);
    vm.runInContext(editSrc, editSandbox);

    calls.length = 0;
    editSandbox.openEdit({ role: "user", text: "hi" });
    ok(calls.some((c) => c[0] === "toast" && c[1].includes("応答中は編集できません")),
      "hasWaitingInput=true で openEdit を呼ぶと toast で拒否される");
    ok(editSandbox.editTarget === null, "ブロックされたとき editTarget は変更されない");

    // 6. saveEdit のガード文字列
    ok(appSrc.includes("if (m.role === \"user\" && busyTurn) {\n    toast(\"応答中は保存できません。停止してから操作してください。\", 4000);") ||
       appSrc.includes("if (m.role === \"user\" && busyTurn) {\r\n    toast(\"応答中は保存できません。停止してから操作してください。\", 4000);"),
      "saveEdit に送信中ガードが存在する");

    // 7. CSS の is-busy ルール
    ok(styleSrc.includes(".msg-wrap.user.is-busy .msg-hover-actions") &&
       styleSrc.includes(".msg-wrap.user.is-busy .msg-more-a11y") &&
       styleSrc.includes("display: none;"),
      "CSS に .msg-wrap.user.is-busy での非表示ルールが存在する");
  }

  console.log("\n[H] エッジケース検証 (copyText 境界値 / TextNode / 冪等初期化)");
  {
    // 1. copyText(null) / copyText("") は即時 false を返す
    const rNull = await sandbox.copyText(null);
    const rEmpty = await sandbox.copyText("");
    ok(rNull === false && rEmpty === false, "copyText は空・null 入力で安全に false を返す");

    // 2. initUserMsgLongPress の抽出と実行検証
    const sInit0 = appSrc.indexOf("let userMsgLongPressInitialized = false;");
    const sInit1 = appSrc.indexOf("function createMessageWrap(");
    if (sInit0 < 0 || sInit1 <= sInit0) throw new Error("initUserMsgLongPress を抽出できません");
    const initSrc = appSrc.slice(sInit0, sInit1);

    const logListeners = {};
    const winListeners = {};
    const docListeners = {};
    const testLog = {
      addEventListener: (t, f) => { (logListeners[t] = logListeners[t] || []).push(f); },
      removeEventListener: () => {},
    };
    const testDoc = {
      addEventListener: (t, f) => { (docListeners[t] = docListeners[t] || []).push(f); },
      removeEventListener: () => {},
    };
    const testWin = {
      addEventListener: (t, f) => { (winListeners[t] = winListeners[t] || []).push(f); },
      removeEventListener: (t, f) => {
        const a = winListeners[t] || [];
        const i = a.indexOf(f);
        if (i >= 0) a.splice(i, 1);
      },
      PointerEvent: true,
    };
    const initSandbox = {
      log: testLog,
      document: testDoc,
      window: testWin,
      state: { messages: [{ id: "u_txt", role: "user", text: "text test" }] },
      isOverlayOpen: () => false,
      moreOpen: { cur: null },
      createLongPressController: sandbox.createLongPressController,
      openUserMsgSheet: sandbox.openUserMsgSheet,
      toggleMoreMenu: (...a) => { calls.push(["toggleMoreMenu", ...a]); },
      LONG_PRESS_MS: 520,
      LONG_PRESS_MOVE_PX: 10,
    };
    vm.createContext(initSandbox);
    vm.runInContext(initSrc, initSandbox);

    // 冪等性: 2 回呼んでもリスナー数は増えない
    initSandbox.initUserMsgLongPress();
    const pdownCount1 = (logListeners["pointerdown"] || []).length;
    initSandbox.initUserMsgLongPress();
    const pdownCount2 = (logListeners["pointerdown"] || []).length;
    ok(pdownCount1 === 1 && pdownCount2 === 1, "initUserMsgLongPress は二重呼び出しでリスナーを重複登録しない");

    // TextNode（nodeType === 3）をタップした場合の wrap 特定
    const wrapEl = new El("div");
    wrapEl.className = "msg-wrap user";
    wrapEl.dataset.mid = "u_txt";
    const bubbleEl = new El("div");
    bubbleEl.className = "msg md user";
    wrapEl.appendChild(bubbleEl);

    class StubTextNode {
      constructor(text, parent) {
        this.nodeType = 3;
        this.textContent = text;
        this.parentElement = parent;
      }
    }
    const textNode = new StubTextNode("タップされた本文テキスト", bubbleEl);

    // pointerdown を発火して TextNode から wrap が解決されタイマーが動くことを確認
    const pointerdownFn = logListeners["pointerdown"][0];
    pointerdownFn({ pointerType: "touch", isPrimary: true, clientX: 50, clientY: 50, target: textNode, pointerId: 10 });
    // TextNode 経由で touchmove ロック（window.addEventListener("touchmove")）が追加されているはず
    ok((winListeners["touchmove"] || []).length > 0, "TextNode をタップしても parentElement から wrap を特定してジェスチャを開始できる");

    // ボタン内の TextNode（例: ■ 停止 の文字）をタップしたときは blocked 判定されること
    const btnEl = new El("button");
    btnEl.className = "btn-stop-wait";
    wrapEl.appendChild(btnEl);
    const btnTextNode = new StubTextNode("■ 停止", btnEl);

    // 既存ジェスチャをリセット
    (winListeners["pointerup"] || [])[0]({ pointerId: 10 });

    pointerdownFn({ pointerType: "touch", isPrimary: true, clientX: 50, clientY: 50, target: btnTextNode, pointerId: 11 });
    ok((winListeners["touchmove"] || []).length === 0, "ボタン内の TextNode をタップしたときは長押しを開始しない（isBlocked が効く）");
  }

  console.log(`\n===== PASS ${pass} / FAIL ${fail} =====`);
  process.exit(fail === 0 ? 0 : 1);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
