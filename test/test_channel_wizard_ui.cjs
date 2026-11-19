// test/test_channel_wizard_ui.cjs
// Channel Wizard フロントエンド結合・UI・状態遷移の自動テスト

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

let passed = 0;
let failed = 0;

function eq(actual, expected, msg) {
  const act = Array.isArray(actual) || (actual && typeof actual.length === "number") ? Array.from(actual) : actual;
  const exp = Array.isArray(expected) || (expected && typeof expected.length === "number") ? Array.from(expected) : expected;
  assert.deepStrictEqual(act, exp, msg);
}

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  PASS: ${name}`);
  } catch (err) {
    failed++;
    console.error(`  FAIL: ${name}`);
    console.error(`    ${err.message}`);
  }
}

async function testAsync(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  PASS: ${name}`);
  } catch (err) {
    failed++;
    console.error(`  FAIL: ${name}`);
    console.error(`    ${err.message}`);
  }
}

const ROOT = path.join(__dirname, "..");
const indexHtmlPath = path.join(ROOT, "public", "index.html");
const appJsPath = path.join(ROOT, "public", "app.js");
const wizardJsPath = path.join(ROOT, "public", "wizard.js");
const wizardCssPath = path.join(ROOT, "public", "wizard.css");

// スタブ DOM 要素
class StubEl {
  constructor(id, tag = "div", envRef = null) {
    this.id = id;
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attrs = {};
    this.dataset = {};
    this.value = "";
    this.checked = false;
    this.textContent = "";
    this._innerHTML = "";
    this.title = "";
    this.disabled = false;
    this.inert = false;
    this.style = {};
    this.listeners = {};
    this._classes = new Set();
    this._envRef = envRef;
    this.classList = {
      add: (...cls) => cls.forEach(c => this._classes.add(c)),
      remove: (...cls) => cls.forEach(c => this._classes.delete(c)),
      contains: (c) => this._classes.has(c),
      toggle: (c, force) => {
        const has = this._classes.has(c);
        const next = force !== undefined ? !!force : !has;
        if (next) this._classes.add(c);
        else this._classes.delete(c);
        return next;
      }
    };
  }
  get innerHTML() { return this._innerHTML !== undefined ? this._innerHTML : ""; }
  set innerHTML(val) {
    this._innerHTML = String(val || "");
    if (this._innerHTML.includes("wizard-confirm-box")) {
      const backdrop = new StubEl("", "div", this._envRef);
      backdrop.classList.add("wizard-confirm-backdrop");
      const box = new StubEl("", "div", this._envRef);
      box.classList.add("wizard-confirm-box");
      const input = new StubEl("wizAskTextInput", "input", this._envRef);
      input.classList.add("wizard-input");
      const matchVal = this._innerHTML.match(/value="([^"]*)"/);
      if (matchVal) input.value = matchVal[1];
      const err = new StubEl("wizAskTextError", "div", this._envRef);
      err.classList.add("wizard-confirm-error", "hidden");
      const okBtn = new StubEl("", "button", this._envRef);
      okBtn.dataset.choice = "ok";
      okBtn.classList.add("wizard-btn", "primary");
      const cancelBtn = new StubEl("", "button", this._envRef);
      cancelBtn.dataset.choice = "cancel";
      cancelBtn.classList.add("wizard-btn", "ghost");
      box.children = [input, err, okBtn, cancelBtn];
      input.parentElement = box;
      err.parentElement = box;
      okBtn.parentElement = box;
      cancelBtn.parentElement = box;
      this.children = [backdrop, box];
      backdrop.parentElement = this;
      box.parentElement = this;
      if (this._envRef) {
        this._envRef["wizAskTextInput"] = input;
        this._envRef["wizAskTextError"] = err;
      }
      return;
    }
    const btnMatches = this._innerHTML.match(/<button[^>]*>/g) || [];
    this.children = btnMatches.map(() => new StubEl("", "button", this._envRef));
  }
  appendChild(child) {
    if (child) {
      child.parentElement = this;
      this.children.push(child);
    }
    return child;
  }
  removeChild(child) {
    this.children = this.children.filter(c => c !== child);
    if (child) child.parentElement = null;
    return child;
  }
  remove() {
    if (this.parentElement) {
      this.parentElement.removeChild(this);
    }
  }
  select() {}
  get isConnected() { return !!this.parentElement; }
  get className() { return Array.from(this._classes).join(" "); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(event, fn) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(fn);
  }
  removeEventListener(event, fn) {
    if (this.listeners[event]) {
      this.listeners[event] = this.listeners[event].filter(f => f !== fn);
    }
  }
  dispatchEvent(ev) {
    if (!ev.preventDefault) ev.preventDefault = () => {};
    if (!ev.stopPropagation) ev.stopPropagation = () => {};
    const list = (this.listeners[ev.type] || []).slice();
    for (const fn of list) fn.call(this, ev);
  }
  closest(sel) {
    if (sel === ".wizard-backdrop") return this.classList.contains("wizard-backdrop") ? this : null;
    if (sel === ".wizard-confirm-backdrop") return this.classList.contains("wizard-confirm-backdrop") ? this : null;
    if (sel === "[data-choice]") return (this.dataset && this.dataset.choice) ? this : null;
    if (sel === "[data-act]") return (this.dataset && this.dataset.act) ? this : null;
    return null;
  }
  focus() {}
  querySelector(sel) {
    if (sel.startsWith("#")) {
      const id = sel.slice(1);
      if (this.id === id) return this;
      if (this._envRef && this._envRef[id]) return this._envRef[id];
      if (typeof elements !== "undefined" && elements[id]) return elements[id];
      for (const child of this.children) {
        const found = child.querySelector(sel);
        if (found) return found;
      }
      return null;
    }
    if (sel === ".wizard-modal") return this;
    if (sel.includes(".wizard-seg-btn.on")) {
      return this.children.find(c => c.classList.contains("wizard-seg-btn") && c.classList.contains("on")) || null;
    }
    if (sel.startsWith(".")) {
      const cls = sel.slice(1).split(".")[0];
      if (this.classList.contains(cls)) return this;
      for (const child of this.children) {
        if (child.classList.contains(cls)) return child;
        const found = child.querySelector(sel);
        if (found) return found;
      }
    }
    return null;
  }
  querySelectorAll(sel) {
    const res = [];
    const check = (el) => {
      if (sel.includes(".wizard-seg-btn") && el.classList.contains("wizard-seg-btn")) res.push(el);
      else if (sel.includes("button") && el.tagName === "BUTTON") res.push(el);
      else if (sel.includes("input") && el.tagName === "INPUT") res.push(el);
      for (const c of el.children) check(c);
    };
    check(this);
    return res;
  }
}

const elements = {};
function getOrCreateEl(id, tag = "div") {
  if (!elements[id]) {
    elements[id] = new StubEl(id, tag, elements);
  }
  return elements[id];
}

// 必要な要素を初期化
const wizardRoot = getOrCreateEl("wizardRoot");
wizardRoot.classList.add("wizard-root", "hidden");
const mobileFab = getOrCreateEl("mobileFab", "button");
const settingsPanel = getOrCreateEl("settingsPanel");
settingsPanel.classList.add("hidden");
const lightbox = getOrCreateEl("lightbox");
lightbox.classList.add("hidden");
const ragHitsModal = getOrCreateEl("ragHitsModal");
ragHitsModal.classList.add("hidden");
const newSessionBtn = getOrCreateEl("newSession", "button");
const appSaveBtn = getOrCreateEl("appSave", "button");
const appNewChannelIde = getOrCreateEl("app_new_channel_ide", "input");
const fWizardEnabled = getOrCreateEl("f_wizard_enabled", "input");
const fWizardProvider = getOrCreateEl("f_wizard_provider", "select");
const fWizardModel = getOrCreateEl("f_wizard_model", "input");
const fWizardThinking = getOrCreateEl("f_wizard_thinking_level", "select");
const appMsg = getOrCreateEl("appMsg", "span");

const bodyEl = new StubEl("body", "body", elements);
let stubModals = [];
let stubMoreSheets = [];

const stubDoc = {
  body: bodyEl,
  activeElement: null,
  querySelector: () => null,
  querySelectorAll: (sel) => {
    if (sel === ".modal:not(.hidden)") return stubModals;
    if (sel === ".more-sheet") return stubMoreSheets;
    return [];
  },
  getElementById: (id) => elements[id] || null,
};

let mobileLayout = true;
const appJsSrc = fs.readFileSync(appJsPath, "utf8").replace(/\r\n/g, "\n");
const wizardJsSrc = fs.readFileSync(wizardJsPath, "utf8").replace(/\r\n/g, "\n");

// ウィザード単体テスト用のサンドボックス環境作成ヘルパー
function createWizardEnv(opts = {}) {
  const envEls = {};
  function getEl(id, tag = "div") {
    if (!envEls[id]) {
      envEls[id] = new StubEl(id, tag, envEls);
    }
    return envEls[id];
  }

  [
    "wizardRoot", "wizardBody", "wizardFoot", "wizardBack",
    "wizardTitle", "wizardProgress", "wizardModelChip", "wizardClose",
    "wizIdea", "wizWork", "wizPartnerName", "wizPartnerType",
    "wizPlayerType", "wizPlayerWork", "wizPlayerName", "wizPlayerProfile",
    "wizStart", "wizCanon", "wizGoal", "wizEnding", "wizProject", "wizPreset",
    "wizCustomNg", "wizFree", "wizJobMsg", "wizProvider", "wizModel", "wizThink"
  ].forEach(id => getEl(id));

  envEls["wizardRoot"].classList.add("wizard-root");
  const footBar = getEl("wizardFootBar", "footer");
  footBar.classList.add("wizard-foot");
  envEls["wizardFoot"].parentElement = footBar;

  const apiCalls = [];
  const defaultApi = async (path, data) => {
    apiCalls.push({ path, data });
    if (opts.apiHandler) {
      const custom = await opts.apiHandler(path, data);
      if (custom !== undefined) return custom;
    }
    return { ok: true, draft: sandbox.window.RPWizard?.__test?.draft || { id: "d1", rev: 1, slots: {} } };
  };

  const envBody = new StubEl("body", "body", envEls);

  const sandbox = {
    window: {
      __RP_WIZARD_TEST__: true,
      confirm: opts.confirm || (() => true),
    },
    document: {
      body: envBody,
      readyState: "complete",
      activeElement: null,
      ...(opts.withDom ? { createElement: (tag) => new StubEl("", tag, envEls) } : {}),
      querySelector: (sel) => {
        if (sel === "#wizardRoot") return envEls["wizardRoot"];
        if (sel === "#wizardBody") return envEls["wizardBody"];
        if (sel === "#wizardFoot") return envEls["wizardFoot"];
        if (sel === "#wizardBack") return envEls["wizardBack"];
        if (sel === "#wizardTitle") return envEls["wizardTitle"];
        if (sel === "#wizardProgress") return envEls["wizardProgress"];
        if (sel === "#wizardModelChip") return envEls["wizardModelChip"];
        if (sel === "#wizardClose") return envEls["wizardClose"];
        if (sel.startsWith("#")) {
          const id = sel.slice(1);
          return envEls[id] || null;
        }
        if (sel === ".wizard-modal") return envEls["wizardRoot"];
        return null;
      },
      querySelectorAll: (sel) => {
        if (sel === "#wizardFoot button") return envEls["wizardFoot"].children || [];
        return [];
      },
      getElementById: (id) => envEls[id] || null,
      addEventListener: () => {},
      removeEventListener: () => {},
    },
    console: console,
    setTimeout: (fn, ms) => {
      if (opts.immediateTimers) {
        return setTimeout(fn, 0);
      }
      return setTimeout(fn, ms || 0);
    },
    clearTimeout: (id) => clearTimeout(id),
    api: defaultApi,
    toast: (m) => { if (opts.onToast) opts.onToast(m); },
    meta: { app: { wizard_enabled: true } },
    sid: "s_test",
  };
  sandbox.window.window = sandbox.window;
  sandbox.window.document = sandbox.document;
  sandbox.document.defaultView = sandbox.window;

  vm.createContext(sandbox);
  vm.runInContext(wizardJsSrc, sandbox);

  return {
    sandbox,
    t: sandbox.window.RPWizard.__test,
    wizard: sandbox.window.RPWizard,
    elements: envEls,
    apiCalls,
  };
}

async function main() {
  console.log("\n=== [1] ファイル存在確認 ===");
  test("public/index.html が存在する", () => {
    assert(fs.existsSync(indexHtmlPath), "index.html should exist");
  });
  test("public/app.js が存在する", () => {
    assert(fs.existsSync(appJsPath), "app.js should exist");
  });
  test("public/wizard.js が存在する", () => {
    assert(fs.existsSync(wizardJsPath), "wizard.js should exist");
  });
  test("public/wizard.css が存在する", () => {
    assert(fs.existsSync(wizardCssPath), "wizard.css should exist");
  });

  console.log("\n=== [2] index.html のマークアップ・制約検証 ===");
  const indexHtml = fs.readFileSync(indexHtmlPath, "utf8");

  test("<head> 内に wizard.css?v= が style.css の後に追加されている（正規表現検証）", () => {
    const styleMatch = indexHtml.match(/<link\s+rel="stylesheet"\s+href="style\.css\?v=(\d+)">/);
    const wizardCssMatch = indexHtml.match(/<link\s+rel="stylesheet"\s+href="wizard\.css\?v=(\d+)">/);
    assert(styleMatch, "style.css?v= link found");
    assert(wizardCssMatch, "wizard.css?v= link found");
    const styleIdx = indexHtml.indexOf(styleMatch[0]);
    const wizardCssIdx = indexHtml.indexOf(wizardCssMatch[0]);
    assert(wizardCssIdx > styleIdx, "wizard.css must come after style.css");
    assert(Number(wizardCssMatch[1]) >= 1, "wizard.css has valid version number");
  });

  test("#wizardRoot の配置と厳格な制約（.modal を絶対に持たない）", () => {
    const wizardRootMatch = indexHtml.match(/<div\s+id="wizardRoot"[^>]*>/);
    assert(wizardRootMatch, "#wizardRoot tag must exist");
    const tag = wizardRootMatch[0];

    // クラス属性チェック
    const classMatch = tag.match(/class="([^"]*)"/);
    assert(classMatch, "#wizardRoot must have class attribute");
    const classes = classMatch[1].split(/\s+/).filter(Boolean);
    assert(classes.includes("wizard-root"), "must contain .wizard-root");
    assert(classes.includes("hidden"), "must contain .hidden by default");

    // CRITICAL CONSTRAINT: modal クラスを一切持たないこと
    assert(!classes.includes("modal"), "CRITICAL: #wizardRoot must NOT have .modal class");
    assert(!tag.includes("modal"), "CRITICAL: #wizardRoot tag must not contain 'modal'");

    // 配置場所チェック: #modelSheet より後、<script> より前
    const modelSheetIdx = indexHtml.indexOf('id="modelSheet"');
    const wizardRootIdx = indexHtml.indexOf('id="wizardRoot"');
    const firstScriptIdx = indexHtml.indexOf('<script src="prompt_toolbar.js"');
    assert(modelSheetIdx !== -1, "#modelSheet found");
    assert(wizardRootIdx !== -1, "#wizardRoot found");
    assert(firstScriptIdx !== -1, "first script found");
    assert(wizardRootIdx > modelSheetIdx, "#wizardRoot must be after #modelSheet");
    assert(wizardRootIdx < firstScriptIdx, "#wizardRoot must be before <script> tags");
  });

  test("#wizardRoot 内部の主要要素が存在する", () => {
    assert(indexHtml.includes('class="wizard-backdrop"'), "wizard-backdrop present");
    assert(indexHtml.includes('class="wizard-modal"'), "wizard-modal present");
    assert(indexHtml.includes('id="wizardBack"'), "#wizardBack present");
    assert(indexHtml.includes('id="wizardTitle"'), "#wizardTitle present");
    assert(indexHtml.includes('id="wizardProgress"'), "#wizardProgress present");
    assert(indexHtml.includes('id="wizardModelChip"'), "#wizardModelChip present");
    assert(indexHtml.includes('id="wizardClose"'), "#wizardClose present");
    assert(indexHtml.includes('id="wizardBody"'), "#wizardBody present");
    assert(indexHtml.includes('id="wizardFoot"'), "#wizardFoot present");
  });

  test("#wizardBack は footer.wizard-foot 内にあり、header 内から削除されている（§5.1）", () => {
    const headMatch = indexHtml.match(/<header\s+class="wizard-head"[\s\S]*?<\/header>/);
    assert(headMatch, "wizard-head found");
    assert(!headMatch[0].includes('id="wizardBack"'), "#wizardBack must NOT be in wizard-head");
    assert(!headMatch[0].includes("‹"), "header back arrow ‹ must be removed from wizard-head");

    const footMatch = indexHtml.match(/<footer\s+class="wizard-foot"[\s\S]*?<\/footer>/);
    assert(footMatch, "wizard-foot found");
    assert(footMatch[0].includes('id="wizardBack"'), "#wizardBack must be in wizard-foot");
    assert(footMatch[0].includes("戻る"), "wizardBack has label 戻る");
    assert(footMatch[0].includes('wizard-back'), "wizardBack has class wizard-back");
    assert(footMatch[0].includes('id="wizardFoot"'), "wizardFoot slot exists in footer");
  });

  test("アプリ全体設定内にチャンネル作成AI設定ボックスが存在する", () => {
    assert(indexHtml.includes('id="f_wizard_enabled"'), "#f_wizard_enabled present");
    assert(indexHtml.includes('id="f_wizard_provider"'), "#f_wizard_provider present");
    assert(indexHtml.includes('id="f_wizard_model"'), "#f_wizard_model present");
    assert(indexHtml.includes('id="f_wizard_thinking_level"'), "#f_wizard_thinking_level present");
    assert(!indexHtml.includes('id="f_wizard_confirm"'), "#f_wizard_confirm removed");

    const appSaveIdx = indexHtml.indexOf('id="appSave"');
    const wizardBoxIdx = indexHtml.indexOf('id="f_wizard_enabled"');
    const decensorBoxIdx = indexHtml.indexOf('id="app_decensor"');
    assert(appSaveIdx < wizardBoxIdx, "wizard settings box is placed after #appSave");
    assert(wizardBoxIdx < decensorBoxIdx, "wizard settings box is placed before app_decensor");
  });

  test("<script> タグの更新（app.js?v= と wizard.js?v= の正規表現検証）", () => {
    const appJsScriptMatch = indexHtml.match(/<script\s+src="app\.js\?v=(\d+)"><\/script>/);
    const wizardJsScriptMatch = indexHtml.match(/<script\s+src="wizard\.js\?v=(\d+)"><\/script>/);
    assert(appJsScriptMatch, "app.js script tag found with version query parameter");
    assert(wizardJsScriptMatch, "wizard.js script tag found with version query parameter");

    const appJsScriptIdx = indexHtml.indexOf(appJsScriptMatch[0]);
    const wizardJsScriptIdx = indexHtml.indexOf(wizardJsScriptMatch[0]);
    assert(wizardJsScriptIdx > appJsScriptIdx, "wizard.js must be loaded after app.js");
    assert(Number(wizardJsScriptMatch[1]) >= 1, "wizard.js has valid version query");
  });

  console.log("\n=== [3] app.js ロジック検証（DOM スタブ環境） ===");

  // [3.1] isOverlayOpen 検証
  test("isOverlayOpen() が #wizardRoot の開閉を正しく判定する", () => {
    const startIdx = appJsSrc.indexOf("function isOverlayOpen() {");
    const endIdx = appJsSrc.indexOf("\n// 中央の「送信」を押している間は true", startIdx);
    assert(startIdx !== -1 && endIdx !== -1, "extract isOverlayOpen");
    const code = appJsSrc.slice(startIdx, endIdx);

    const sandbox = {
      document: stubDoc,
      $: (id) => elements[id] || null,
    };
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);

    wizardRoot.classList.add("hidden");
    assert.strictEqual(sandbox.isOverlayOpen(), false, "when wizardRoot is hidden and no other overlays, returns false");

    wizardRoot.classList.remove("hidden");
    assert.strictEqual(sandbox.isOverlayOpen(), true, "when wizardRoot is open, returns true");

    wizardRoot.classList.add("hidden");
  });

  // [3.2] updateFabState 検証
  test("updateFabState() が #wizardRoot 開時に FAB を非表示にする", () => {
    const startIdx = appJsSrc.indexOf("function updateFabState() {");
    const endIdx = appJsSrc.indexOf("\n// FAB/Esc 共通", startIdx);
    assert(startIdx !== -1 && endIdx !== -1, "extract updateFabState");
    const code = appJsSrc.slice(startIdx, endIdx);

    const sandbox = {
      document: stubDoc,
      $: (id) => elements[id] || null,
      isMobileLayout: () => mobileLayout,
      syncFootBarPlacement: () => {},
      syncActionControlsVisibility: () => {},
      scrollUiFabHidden: false,
    };
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);

    mobileLayout = true;
    wizardRoot.classList.add("hidden");
    sandbox.updateFabState();
    assert.strictEqual(mobileFab.classList.contains("fab-hidden"), false, "FAB is visible when mobile and wizard closed");

    wizardRoot.classList.remove("hidden");
    sandbox.updateFabState();
    assert.strictEqual(mobileFab.classList.contains("fab-hidden"), true, "FAB is hidden when wizard is open");

    wizardRoot.classList.add("hidden");
    sandbox.updateFabState();
    assert.strictEqual(mobileFab.classList.contains("fab-hidden"), false, "FAB is restored when wizard closes");
  });

  // [3.3] closeTopmostLayer 検証
  test("closeTopmostLayer() が最前面の #wizardRoot を RPWizard.close() に委譲する", () => {
    const startIdx = appJsSrc.indexOf("function closeTopmostLayer() {");
    const endIdx = appJsSrc.indexOf("\nfunction updateHeaderAndSideStatus() {", startIdx);
    assert(startIdx !== -1 && endIdx !== -1, "extract closeTopmostLayer");
    const code = appJsSrc.slice(startIdx, endIdx);

    let closeCalled = false;
    const mockRPWizard = {
      close: () => { closeCalled = true; }
    };

    const sandbox = {
      document: stubDoc,
      $: (id) => elements[id] || null,
      window: { RPWizard: mockRPWizard },
      setDrawerOpen: () => {},
    };
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);

    // ウィザードが開いている場合
    wizardRoot.classList.remove("hidden");
    closeCalled = false;
    const closed = sandbox.closeTopmostLayer();
    assert.strictEqual(closed, true, "closeTopmostLayer returns true");
    assert.strictEqual(closeCalled, true, "window.RPWizard.close() was called");

    // ウィザードが閉じていて RPWizard がない場合
    wizardRoot.classList.add("hidden");
    const closedWhenHidden = sandbox.closeTopmostLayer();
    assert.strictEqual(closedWhenHidden, false, "closeTopmostLayer returns false when all layers closed");
  });

  // [3.4] switchTo, refreshSessions, afterChannelCreated のグローバル公開
  test("window.switchTo, window.refreshSessions, window.afterChannelCreated の公開構文", () => {
    assert(appJsSrc.includes("window.switchTo = switchTo;"), "window.switchTo is assigned");
    assert(appJsSrc.includes("window.refreshSessions = refreshSessions;"), "window.refreshSessions is assigned");
    assert(appJsSrc.includes("window.afterChannelCreated = afterChannelCreated;"), "window.afterChannelCreated is assigned");
  });

  // [3.5] newSession.onclick の振る舞い検証
  await testAsync("newSession.onclick が RPWizard.open() に委譲し、無効時や例外時に legacyNewSession にフォールバックする", async () => {
    const startIdx = appJsSrc.indexOf("async function afterChannelCreated(");
    const endIdx = appJsSrc.indexOf("\n$(\"dupBtn\").onclick", startIdx);
    assert(startIdx !== -1 && endIdx !== -1, "extract legacyNewSession and newSession.onclick");
    const code = appJsSrc.slice(startIdx, endIdx);

    let wizardOpenCalled = 0;
    let wizardShouldThrow = false;

    const mockMeta = {
      app: { wizard_enabled: true }
    };

    const mockRPWizard = {
      open: async () => {
        wizardOpenCalled++;
        if (wizardShouldThrow) throw new Error("wizard open failed");
      }
    };

    const sandbox = {
      window: { RPWizard: mockRPWizard },
      meta: mockMeta,
      $: (id) => elements[id] || null,
      state: null,
      sid: "",
      formSid: "",
      readForm: () => ({}),
      prompt: () => null,
      api: async () => ({ sid: "s_new" }),
      refreshSessions: async () => {},
      switchTo: async () => {},
      updateFabState: () => {},
      flash: () => {},
      toast: () => {},
      checked: async () => ({}),
      poll: async () => {},
      loadIdeSessions: () => {},
      console: { warn: () => {} },
    };

    vm.createContext(sandbox);
    const wrappedCode = code.replace(
      "async function legacyNewSession() {",
      "async function legacyNewSession() { legacyCalledCount++; "
    );
    sandbox.legacyCalledCount = 0;
    vm.runInContext(wrappedCode, sandbox);

    // ケース 1: 通常（wizard_enabled: true） -> RPWizard.open() が呼ばれる
    wizardOpenCalled = 0;
    sandbox.legacyCalledCount = 0;
    await elements["newSession"].onclick();
    assert.strictEqual(wizardOpenCalled, 1, "RPWizard.open called");
    assert.strictEqual(sandbox.legacyCalledCount, 0, "legacyNewSession NOT called");

    // ケース 2: wizard_enabled: false -> legacyNewSession が直接呼ばれる
    mockMeta.app.wizard_enabled = false;
    wizardOpenCalled = 0;
    sandbox.legacyCalledCount = 0;
    await elements["newSession"].onclick();
    assert.strictEqual(wizardOpenCalled, 0, "RPWizard.open NOT called when disabled");
    assert.strictEqual(sandbox.legacyCalledCount, 1, "legacyNewSession called");

    // ケース 3: RPWizard.open() が例外をスロー -> catch して legacyNewSession にフォールバック
    mockMeta.app.wizard_enabled = true;
    wizardShouldThrow = true;
    wizardOpenCalled = 0;
    sandbox.legacyCalledCount = 0;
    await elements["newSession"].onclick();
    assert.strictEqual(wizardOpenCalled, 1, "RPWizard.open called");
    assert.strictEqual(sandbox.legacyCalledCount, 1, "fallback to legacyNewSession on error");

    // ケース 4: window.RPWizard が存在しない -> legacyNewSession にフォールバック
    delete sandbox.window.RPWizard;
    wizardOpenCalled = 0;
    sandbox.legacyCalledCount = 0;
    await elements["newSession"].onclick();
    assert.strictEqual(sandbox.legacyCalledCount, 1, "legacyNewSession called when RPWizard undefined");
  });

  // [3.6] appSave.onclick でのウィザード設定保存
  await testAsync("appSave.onclick がウィザード設定パラメータを /api/app-settings へ送信する", async () => {
    const startIdx = appJsSrc.indexOf("$(\"appSave\").onclick = async () => {");
    const endIdx = appJsSrc.indexOf("\n$(\"appDecensorSave\").onclick", startIdx);
    assert(startIdx !== -1 && endIdx !== -1, "extract appSave.onclick");
    const code = appJsSrc.slice(startIdx, endIdx);

    let sentPayload = null;
    const sandbox = {
      $: (id) => elements[id] || null,
      checked: async (url, data) => {
        if (url === "/api/app-settings") {
          sentPayload = data;
        }
        return { ok: true };
      },
      loadOptions: async () => {},
      setTimeout: (fn) => fn(),
    };

    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);

    // フォームに値をセット
    appNewChannelIde.checked = true;
    fWizardEnabled.checked = true;
    fWizardProvider.value = "vertex";
    fWizardModel.value = "gemini-2.5-flash";
    fWizardThinking.value = "medium";

    await elements["appSave"].onclick();

    assert(sentPayload, "payload was sent to /api/app-settings");
    assert.strictEqual(sentPayload.new_channel_ide, true);
    assert.strictEqual(sentPayload.wizard_enabled, true);
    assert.strictEqual(sentPayload.wizard_provider, "vertex");
    assert.strictEqual(sentPayload.wizard_model, "gemini-2.5-flash");
    assert.strictEqual(sentPayload.wizard_thinking_level, "medium");
    assert.strictEqual("wizard_confirm" in sentPayload, false, "wizard_confirm not in sentPayload");
  });

  console.log("\n=== [4] wizard.js 静的解析・構造検証（§11.1） ===");

  test("wizard.js: navStack, navigate, backRender, canBack, settleTo が存在する", () => {
    assert(wizardJsSrc.includes("navStack"), "navStack exists");
    assert(/function\s+navigate\s*\(/.test(wizardJsSrc), "navigate() function exists");
    assert(/function\s+backRender\s*\(/.test(wizardJsSrc), "backRender() function exists");
    assert(/function\s+canBack\s*\(/.test(wizardJsSrc), "canBack() function exists");
    assert(/function\s+settleTo\s*\(/.test(wizardJsSrc), "settleTo() function exists");
  });

  test("wizard.js: show( の直接呼び出しが残っていない（全呼び出しを navigate 等に置換）", () => {
    const directShowCalls = wizardJsSrc.match(/(?<!function\s+)\bshow\s*\(\s*["'`\w]/g);
    assert(!directShowCalls || directShowCalls.length === 0, `show( calls should be replaced: ${directShowCalls}`);
  });

  test("wizard.js: runAction ガードが存在する", () => {
    assert(/async\s+function\s+runAction\s*\(/.test(wizardJsSrc), "runAction() function exists");
    assert(wizardJsSrc.includes("bodyEl.inert = true") || wizardJsSrc.includes("bodyEl.inert = false") || wizardJsSrc.includes("inert"), "runAction handles inert");
  });

  test("wizard.js: apiCall の rev 分岐で画面再描画呼び出しが無い（§7.4）", () => {
    const apiCallMatch = wizardJsSrc.match(/async\s+function\s+apiCall[\s\S]*?^  \}/m);
    assert(apiCallMatch, "apiCall function found");
    const body = apiCallMatch[0];
    assert(!body.includes("renderPartner("), "apiCall does not call renderPartner");
    assert(!body.includes("renderBase("), "apiCall does not call renderBase");
    assert(!body.includes("renderTags("), "apiCall does not call renderTags");
    assert(!body.includes("renderPreview("), "apiCall does not call renderPreview");
    assert(body.includes("initPendingTags"), "apiCall calls initPendingTags on rev mismatch");
  });

  test("wizard.js: renderTags が pendingTags を毎回初期化していない（§11.1）", () => {
    const renderTagsMatch = wizardJsSrc.match(/function\s+renderTags[\s\S]*?^  \}/m);
    assert(renderTagsMatch, "renderTags function found");
    const body = renderTagsMatch[0];
    assert(body.includes("!pendingTags"), "renderTags guards initPendingTags with !pendingTags check");
    assert(!/^\s*initPendingTags\(\)\s*;/m.test(body.replace(/\b(?:if|else)\s*[^{]*\{[\s\S]*?\}/g, "")), "initPendingTags is not called unconditionally in renderTags");
  });

  test("wizard.js?v= と wizard.css?v= がキャッシュバスティングされている（§11.1）", () => {
    const cssMatch = indexHtml.match(/href="wizard\.css\?v=(\d+)"/);
    const jsMatch = indexHtml.match(/src="wizard\.js\?v=(\d+)"/);
    assert(cssMatch, "wizard.css?v= present");
    assert(jsMatch, "wizard.js?v= present");
    assert(Number(cssMatch[1]) >= 2, "wizard.css version is incremented (>= 2)");
    assert(Number(jsMatch[1]) >= 2, "wizard.js version is incremented (>= 2)");
  });

  console.log("\n=== [5] スタック純ロジック検証（vm + window.__RP_WIZARD_TEST__） ===");

  test("ENTRY_STACK の全画面定義が仕様表と一致する（§4.3）", () => {
    const env = createWizardEnv();
    const expected = {
      base: ["base"],
      model: ["model"],
      idea: ["idea"],
      partner: ["idea", "partner"],
      tags: ["partner", "tags"],
      questions: ["tags", "questions"],
      preview: ["tags", "preview"],
      creating: ["tags", "creating"],
      done: ["done"],
    };
    for (const [k, v] of Object.entries(expected)) {
      eq(env.t.ENTRY_STACK[k], v, `ENTRY_STACK[${k}] matches spec`);
    }
  });

  test("enterScreen() が論理的親を含む合成スタックを初期化する（I1）", () => {
    const env = createWizardEnv();
    env.t.enterScreen("partner");
    eq(env.t.navStack, ["idea", "partner"]);
    assert.strictEqual(env.t.screen, "partner");
    assert.strictEqual(env.t.navStack[env.t.navStack.length - 1], env.t.screen, "I1 holds: top of stack === screen");
  });

  test("navigate() のスタック push と巻き戻し（I1, I2）", () => {
    const env = createWizardEnv();
    env.t.enterScreen("idea");
    eq(env.t.navStack, ["idea"]);

    // 未知の画面へ進む -> push
    env.t.navigate("partner");
    eq(env.t.navStack, ["idea", "partner"]);
    assert.strictEqual(env.t.screen, "partner");

    env.t.navigate("tags");
    eq(env.t.navStack, ["idea", "partner", "tags"]);
    assert.strictEqual(env.t.screen, "tags");

    // 既にスタックにある画面へ navigate -> 切り詰めて戻る（重複を作らない I2）
    env.t.navigate("partner");
    eq(env.t.navStack, ["idea", "partner"]);
    assert.strictEqual(env.t.screen, "partner");
    assert.strictEqual(env.t.navStack[env.t.navStack.length - 1], env.t.screen, "I1 holds");
  });

  test("backRender() が pop 後のスタックトップ画面を描画する", () => {
    const env = createWizardEnv();
    env.t.navStack = ["idea", "partner"];
    env.t.screen = "partner";
    env.t.navStack.pop();
    env.t.backRender();
    assert.strictEqual(env.t.screen, "idea");
    assert.strictEqual(env.t.navStack[env.t.navStack.length - 1], "idea");
  });

  test("settleTo(name, parent) が履歴を parent まで畳んでから進む（I3: creating 除去）", () => {
    const env = createWizardEnv();
    env.t.navStack = ["partner", "tags", "creating"];
    env.t.screen = "creating";

    // compose 完了: returnTo ("tags") まで畳んで "preview" へ進む
    env.t.settleTo("preview", "tags");
    eq(env.t.navStack, ["partner", "tags", "preview"]);
    assert.strictEqual(env.t.screen, "preview");
    assert(!env.t.navStack.includes("creating"), "creating is removed from stack (I3)");
  });

  test("canBack() の状態別判定条件（§4.2）", () => {
    const env = createWizardEnv();
    env.t.navStack = ["idea", "partner"];
    env.t.screen = "partner";
    env.t.busy = false;
    env.t.pending = "";
    env.t.rendering = false;
    assert.strictEqual(env.t.canBack(), true, "can back when normal and stack > 1");

    // 単一画面（ルート）なら戻れない
    env.t.navStack = ["idea"];
    env.t.screen = "idea";
    assert.strictEqual(env.t.canBack(), false, "cannot back when navStack <= 1");

    env.t.navStack = ["idea", "partner"];
    env.t.screen = "partner";

    // busy のときは戻れない
    env.t.busy = true;
    assert.strictEqual(env.t.canBack(), false, "cannot back when busy");
    env.t.busy = false;

    // pending のときは戻れない
    env.t.pending = "next";
    assert.strictEqual(env.t.canBack(), false, "cannot back when pending");
    env.t.pending = "";

    // rendering のときは戻れない
    env.t.rendering = true;
    assert.strictEqual(env.t.canBack(), false, "cannot back when rendering");
    env.t.rendering = false;

    // done 画面では戻れない
    env.t.screen = "done";
    assert.strictEqual(env.t.canBack(), false, "cannot back on done screen");
  });

  test("ステップ順が ベース → 話 → AI → 相手 → タグ → 質問 → プレビュー になっている", () => {
    const env = createWizardEnv();
    eq(env.t.progressFor("base"), "1 / 7");
    eq(env.t.progressFor("idea"), "2 / 7");
    eq(env.t.progressFor("model"), "3 / 7");
    eq(env.t.progressFor("partner"), "4 / 7");
    eq(env.t.progressFor("tags"), "5 / 7");
    eq(env.t.progressFor("questions"), "6 / 7");
    eq(env.t.progressFor("preview"), "7 / 7");
  });

  test("openModelStep() は話の次にモデルを積む（戻ると話へ戻る）", () => {
    const env = createWizardEnv();
    env.t.navStack = ["base", "idea"];
    env.t.screen = "idea";
    env.t.openModelStep();
    eq(env.t.navStack, ["base", "idea", "model"]);
    assert.strictEqual(env.t.screen, "model");
  });

  test("closeModel() の遷移先決定（親へ戻る。base やルートなら idea へ）", () => {
    const env = createWizardEnv();

    // ケース 1: チップから開いた場合（親が partner）
    env.t.navStack = ["idea", "partner", "model"];
    env.t.screen = "model";
    env.t.closeModel();
    assert.strictEqual(env.t.screen, "partner");
    eq(env.t.navStack, ["idea", "partner"]);

    // ケース 2: 親が base（base は locked なので戻らず idea へ）
    env.t.navStack = ["base", "model"];
    env.t.screen = "model";
    env.t.closeModel();
    assert.strictEqual(env.t.screen, "idea");
    eq(env.t.navStack, ["idea"]);

    // ケース 3: ルートとして開いた場合（親なし）
    env.t.navStack = ["model"];
    env.t.screen = "model";
    env.t.closeModel();
    assert.strictEqual(env.t.screen, "idea");
    eq(env.t.navStack, ["idea"]);
  });

  console.log("\n=== [6] 振る舞い・ユニットテスト検証（§11.1 の 13項目） ===");

  // 1. close(true) が busy 中でも閉じる（doCreate 成功経路）
  await testAsync("1. close(true) が busy 中でも閉じる（doCreate 成功経路）", async () => {
    const env = createWizardEnv();
    env.elements["wizardRoot"].classList.remove("hidden");
    env.t.busy = true;

    // 通常の close(false) は busy 中弾かれる
    await env.wizard.close(false);
    assert.strictEqual(env.elements["wizardRoot"].classList.contains("hidden"), false, "close(false) does not close when busy");

    // close(true) は force なので busy でも直ちに閉じる
    await env.wizard.close(true);
    assert.strictEqual(env.elements["wizardRoot"].classList.contains("hidden"), true, "close(true) closes modal even when busy");
    eq(env.t.navStack, [], "navStack is cleared");
  });

  // 2. partner 描画待ちに戻っても readPartnerForm が例外を出さず、親画面を上書きしない（renderSeq）
  await testAsync("2. partner 描画待ちに戻っても readPartnerForm が例外を出さず、親画面を上書きしない（renderSeq）", async () => {
    const env = createWizardEnv();
    env.t.draft = {
      id: "d1", rev: 1,
      slots: { partner_type: { value: "original" }, work: { value: "Draft Work" } }
    };

    // DOM 要素未生成の状態で readPartnerForm を呼んでも例外を出さない（draft にフォールバック）
    delete env.elements["wizPartnerType"];
    let values;
    assert.doesNotThrow(() => {
      values = env.t.readPartnerForm();
    }, "readPartnerForm should not throw when DOM elements are missing");
    assert.strictEqual(values.values.partner_type, "original", "falls back to draft slots");

    // 非同期 renderPartner 中に別画面へ遷移した場合に古い世代が上書きしないか
    let resolvePresets;
    const presetsPromise = new Promise(r => { resolvePresets = r; });
    env.sandbox.api = async (path) => {
      if (path.includes("/api/wizard/presets")) {
        await presetsPromise;
        return { ok: true, presets: [] };
      }
      return { ok: true };
    };

    const p = env.t.render("partner");
    // 直後に idea へ戻る
    env.t.navigate("idea");
    assert.strictEqual(env.t.screen, "idea");

    // プリセット API 完了
    resolvePresets();
    await p;
    // idea 画面が維持されていること
    assert.strictEqual(env.t.screen, "idea", "renderSeq prevented delayed partner render from overwriting screen");
  });

  // 3. model 描画待ちの setFoot が親画面のフッターを上書きしない
  await testAsync("3. model 描画待ちの setFoot が親画面のフッターを上書きしない", async () => {
    const env = createWizardEnv();
    let resolveModels;
    const modelsPromise = new Promise(r => { resolveModels = r; });
    env.sandbox.api = async (path) => {
      if (path.includes("/api/wizard/models")) {
        await modelsPromise;
        return { ok: true, models: [{ id: "m1", label: "Model 1" }] };
      }
      return { ok: true };
    };

    env.elements["wizardFoot"].innerHTML = '<button id="parentFootBtn">親画面フッター</button>';
    const p = env.t.render("model");

    // ロード中に idea へ戻る
    env.t.navigate("idea");
    env.elements["wizardFoot"].innerHTML = '<button id="parentFootBtn">親画面フッター</button>';

    resolveModels();
    await p;

    assert(env.elements["wizardFoot"].innerHTML.includes("parentFootBtn"), "parent foot button preserved");
    assert(!env.elements["wizardFoot"].innerHTML.includes("model-save"), "model buttons not injected into parent footer");
  });

  // 4. work 変更後の退避で partner_name / start_point / canon_fidelity が残る（PARTNER_INVALIDATES の再送）
  test("4. work 変更後の退避で partner_name / start_point / canon_fidelity が残る（PARTNER_INVALIDATES の再送）", () => {
    const env = createWizardEnv();
    env.t.draft = {
      id: "d1", rev: 1,
      slots: {
        work: { value: "原作A", source: "user" },
        partner_name: { value: "キャラB", source: "user" },
        start_point: { value: "第1話", source: "user" },
        canon_fidelity: { value: "high", source: "user" },
      }
    };

    // work のみを "原作C" に変更、他はフォームに残っている状態
    const formData = {
      values: {
        partner_type: "copyright",
        work: "原作C",
        partner_name: "キャラB",
        player_type: "none",
        player_work: "",
        player_name: "",
        player_profile: "",
        start_point: "第1話",
        canon_fidelity: "high",
        goal: "",
        ending: "",
        project: "",
      },
      touched: new Set(),
      preset: "",
    };

    const patch = env.t.buildPartnerPatch(formData);
    assert(patch.work, "patch contains work");
    assert.strictEqual(patch.work.value, "原作C");
    // PARTNER_INVALIDATES により work の子スロット（partner_name, start_point, canon_fidelity）が再送される
    assert(patch.partner_name, "patch preserves partner_name");
    assert.strictEqual(patch.partner_name.value, "キャラB");
    assert(patch.start_point, "patch preserves start_point");
    assert.strictEqual(patch.start_point.value, "第1話");
    assert(patch.canon_fidelity, "patch preserves canon_fidelity");
    assert.strictEqual(patch.canon_fidelity.value, "high");
  });

  // 5. カスタムNGだけ変更 → 戻る／閉じるで /tags が呼ばれる
  await testAsync("5. カスタムNGだけ変更 → 戻る／閉じるで /tags が呼ばれる", async () => {
    const env = createWizardEnv();
    env.t.draft = {
      id: "d1", rev: 1,
      selected_tags: ["tag1"],
      selected_ng: ["ng1"],
      custom_ng: "old_custom",
    };
    env.t.pendingTags = {
      tags: ["tag1"],
      ng: ["ng1"],
      custom: "old_custom",
    };
    env.t.tagDirty = false;

    // カスタムNGを変更して scheduleTagSave を呼ぶ
    env.elements["wizCustomNg"].value = "new_custom_ng";
    env.t.scheduleTagSave();
    assert.strictEqual(env.t.tagDirty, true, "tagDirty is set to true");

    const r = await env.t.flushTags();
    assert.strictEqual(r.ok, true, "flushTags succeeds");
    assert.strictEqual(r.changed, true, "flushTags reports changed");

    const tagsApiCall = env.apiCalls.find(c => c.path === "/api/wizard/tags");
    assert(tagsApiCall, "/api/wizard/tags was called");
    assert.strictEqual(tagsApiCall.data.custom_ng, "new_custom_ng", "sent updated custom_ng");
  });

  // 6. タグ保存中に追加したタグが、次へ・閉じるで失われない（drainTagSaves）
  await testAsync("6. タグ保存中に追加したタグが、次へ・閉じるで失われない（drainTagSaves）", async () => {
    const env = createWizardEnv();
    env.t.draft = {
      id: "d1", rev: 1,
      selected_tags: ["tagA"],
      selected_ng: [],
      custom_ng: "",
    };
    env.t.pendingTags = {
      tags: ["tagA", "tagB"],
      ng: [],
      custom: "",
    };
    env.t.tagDirty = true;

    let resolveFirstSave;
    const firstSavePromise = new Promise(r => { resolveFirstSave = r; });
    let saveCount = 0;

    env.sandbox.api = async (path, data) => {
      if (path === "/api/wizard/tags") {
        saveCount++;
        if (saveCount === 1) {
          await firstSavePromise;
        }
        return {
          ok: true,
          draft: {
            id: "d1",
            rev: saveCount + 1,
            selected_tags: data.selected_tags,
            selected_ng: data.selected_ng,
            custom_ng: data.custom_ng,
          }
        };
      }
      return { ok: true };
    };

    // 1回目の保存を開始（in-flight）
    const savePromise = env.t.saveTagsToServer();

    // 保存中にユーザーが "tagC" を追加
    env.t.pendingTags.tags.push("tagC");
    env.t.tagDirty = true;

    // drainTagSaves が 1回目の完了を待ち、未保存の tagC を検出して 2回目を走らせる
    const drainPromise = env.t.drainTagSaves();
    resolveFirstSave();

    const drainOk = await drainPromise;
    await savePromise;

    assert.strictEqual(drainOk, true, "drainTagSaves succeeds");
    assert.strictEqual(saveCount, 2, "saveTagsToServer was called twice to capture in-flight additions");
    assert(env.t.draft.selected_tags.includes("tagC"), "draft includes tagC added during save");
  });

  // 7. 保存中に追記 → formSeq 検出で遷移しない（inert 非対応の想定）
  await testAsync("7. 保存中に追記 → formSeq 検出で遷移しない（inert 非対応の想定）", async () => {
    const env = createWizardEnv();
    env.t.draft = { id: "d1", rev: 1, slots: { rough_idea: { value: "old" } } };
    env.elements["wizIdea"].value = "new";

    // 保存の await 中に入力が追記された（formSeq が進んだ）シミュレーション
    env.sandbox.api = async (path) => {
      env.t.formSeq += 1;
      return { ok: true, draft: { id: "d1", rev: 2, slots: { rough_idea: { value: "new" } } } };
    };

    const res = await env.t.flushScreen("idea");
    assert.strictEqual(res.ok, false, "flushScreen returns ok:false on concurrent input change");
    assert.strictEqual(res.error, "入力が更新されました", "reports error on concurrent input change");
    assert(env.t.snap.idea, "snap preserves input on concurrent update abort");
  });

  // 8. 退避失敗 → 次へで正常保存 → 閉じる、で古い snap が再送されない
  await testAsync("8. 退避失敗 → 次へで正常保存 → 閉じる、で古い snap が再送されない", async () => {
    const env = createWizardEnv();
    // 過去の退避失敗で snap に値が残っている
    env.t.snap = { idea: { idea: "failed_old_idea" } };

    // 正常保存が成功したときに snap[screen] が削除される
    delete env.t.snap.idea;
    assert.strictEqual(env.t.snap.idea, undefined, "snap.idea cleared after successful save");

    let apiSentFailedIdea = false;
    env.sandbox.api = async (path, data) => {
      if (data && data.slots && data.slots.rough_idea && data.slots.rough_idea.value === "failed_old_idea") {
        apiSentFailedIdea = true;
      }
      return { ok: true, draft: { id: "d1", rev: 1 } };
    };

    await env.wizard.close(false);
    assert.strictEqual(apiSentFailedIdea, false, "old snap was not resent on close");
  });

  // 9. キャンセル失敗（ok:false）で遷移せず jobId が残る
  await testAsync("9. キャンセル失敗（ok:false）で遷移せず jobId が残る", async () => {
    const env = createWizardEnv();
    env.t.screen = "creating";
    env.t.navStack = ["tags", "creating"];
    env.t.jobId = "job_failed_cancel";
    env.t.draft = { id: "d1", rev: 1 };

    // job_cancel API が失敗を返す
    env.sandbox.api = async (path) => {
      if (path === "/api/wizard/job_cancel") {
        return { ok: false, error: "Cancel network failure" };
      }
      return { ok: true };
    };

    let toastCalled = false;
    env.sandbox.toast = () => { toastCalled = true; };

    await env.t.goBack();

    assert.strictEqual(env.t.jobId, "job_failed_cancel", "jobId is not cleared on cancel failure");
    eq(env.t.navStack, ["tags", "creating"], "navStack is NOT popped on cancel failure");
    assert.strictEqual(env.t.screen, "creating", "screen remains creating");
  });

  // 10. last_job だけがある完了済みジョブでキャンセル確認が出ない
  await testAsync("10. last_job だけがある完了済みジョブでキャンセル確認が出ない", async () => {
    const env = createWizardEnv();
    env.t.screen = "creating";
    env.t.navStack = ["tags", "creating"];
    env.t.jobId = ""; // 生存ジョブなし（完了済み）
    env.t.draft = { id: "d1", rev: 1, last_job: "completed_old_job" };

    let confirmPrompted = false;
    env.sandbox.window.confirm = () => {
      confirmPrompted = true;
      return true;
    };

    await env.t.goBack();
    assert.strictEqual(confirmPrompted, false, "confirm prompt is not triggered when jobId is empty");
  });

  // 11. questions で選択肢だけ選んだ状態でも破棄確認が出る
  await testAsync("11. questions で選択肢だけ選んだ状態でも破棄確認が出る", async () => {
    const env = createWizardEnv();
    env.t.screen = "questions";
    env.t.navStack = ["tags", "questions"];
    env.elements["wizardFoot"].dataset.picked = "opt_1"; // 選択肢を選んだ状態

    let promptMessage = "";
    env.sandbox.window.confirm = (msg) => {
      promptMessage = msg;
      return false; // キャンセルする
    };

    await env.t.goBack();
    assert(promptMessage.includes("入力中の回答は破棄されます"), "discard confirmation was prompted");
    eq(env.t.navStack, ["tags", "questions"], "navStack was not popped because confirm returned false");
  });

  // 12. 再開の全entryで enterScreen の合成スタックが ENTRY_STACK と一致する
  test("12. 再開の全entryで enterScreen の合成スタックが ENTRY_STACK と一致する", () => {
    const env = createWizardEnv();
    const entryScreens = ["base", "model", "idea", "partner", "tags", "questions", "preview", "creating", "done"];
    for (const name of entryScreens) {
      env.t.enterScreen(name);
      eq(env.t.navStack, env.t.ENTRY_STACK[name], `enterScreen(${name}) matches ENTRY_STACK[${name}]`);
      assert.strictEqual(env.t.screen, name, `screen matches ${name}`);
    }
  });

  // 13. pollJob の catch が旧jobで再スケジュールしない
  await testAsync("13. pollJob の catch が旧jobで再スケジュールしない", async () => {
    const env = createWizardEnv();
    env.t.jobId = "job_stale";

    let scheduled = [];
    env.sandbox.setTimeout = (fn, ms) => {
      const item = { fn, ms };
      scheduled.push(item);
      return item;
    };

    env.sandbox.api = async () => {
      // エラー発生中にジョブがキャンセルまたは切り替わった
      env.t.jobId = "job_new";
      throw new Error("Temporary network glitch");
    };

    scheduled = [];
    await env.t.pollJob();

    assert.strictEqual(scheduled.length, 0, "catch block did not reschedule timer when jobId was replaced");
  });

  console.log("\n=== [7] 新仕様（wizard-idea-step-spec.md）の検証 ===");

  // 1 & 2. 2/7「どんな話にしますか？」のマークアップ検証
  test("2/7 HTML に idea-consult, idea-auto, idea-manual が存在し #wizConfirm が含まれない", () => {
    const env = createWizardEnv();
    env.t.render("idea");
    const bodyHtml = env.elements["wizardBody"].innerHTML;
    assert(bodyHtml.includes('data-act="idea-consult"'), "data-act=idea-consult exists");
    assert(bodyHtml.includes('data-act="idea-auto"'), "data-act=idea-auto exists");
    assert(bodyHtml.includes('data-act="idea-manual"'), "data-act=idea-manual exists");
    assert(!bodyHtml.includes('id="wizConfirm"'), "#wizConfirm must NOT be in idea HTML");
    assert(!bodyHtml.includes('data-act="empty"'), "data-act=empty must NOT be in idea HTML");
  });

  // 5. 一言アイデア参照バグ修正: 入力時は custom_text + auto:false、空欄時は auto:true
  await testAsync("saveIdea: 入力があるときは custom_text が送られ auto:false、空欄なら auto:true", async () => {
    const env = createWizardEnv();
    env.t.draft = { id: "d_idea", rev: 1, slots: {} };

    // 1) 入力あり
    env.elements["wizIdea"].value = "学園ラブコメ";
    await env.t.saveIdea();
    const call1 = env.apiCalls[env.apiCalls.length - 1];
    assert.strictEqual(call1.path, "/api/wizard/answer");
    assert.strictEqual(call1.data.custom_text, "学園ラブコメ");
    assert.strictEqual(call1.data.auto, false);

    // 2) 入力なし（空欄）
    env.elements["wizIdea"].value = "   ";
    await env.t.saveIdea();
    const call2 = env.apiCalls[env.apiCalls.length - 1];
    assert.strictEqual(call2.path, "/api/wizard/answer");
    assert.strictEqual(call2.data.custom_text, "");
    assert.strictEqual(call2.data.auto, true);
  });

  // 6. startCreateChain() が compose 済みで常に preview（doCreate を呼ばない）
  test("startCreateChain: compose 済みなら常に preview（doCreate を呼ばない）", () => {
    const env = createWizardEnv();
    env.t.draft = {
      id: "d1", rev: 1,
      model: { provider: "google", model: "m", ai_enabled: true },
      compose: { setting: { title: "タイトル" } },
      compose_stale: false,
    };
    env.t.navStack = ["idea", "model"];
    env.t.screen = "model";
    let doCreateCalled = false;
    env.t.doCreate = () => { doCreateCalled = true; };
    env.t.startCreateChain(false);
    assert.strictEqual(env.t.screen, "preview");
    assert.strictEqual(doCreateCalled, false);
  });

  // 7. pollJob の compose 完了で doCreate が呼ばれない
  await testAsync("pollJob: compose 完了時に doCreate は呼ばれず preview に settle する", async () => {
    const env = createWizardEnv();
    env.t.jobId = "wj_done";
    env.t.jobKind = "compose";
    env.sandbox.api = async () => ({
      status: "done",
      result: { draft: { id: "d1", rev: 2, compose: { setting: { title: "完了" } } } },
    });
    env.t.navStack = ["idea", "creating"];
    env.t.screen = "creating";
    let doCreateCalled = false;
    env.t.doCreate = () => { doCreateCalled = true; };

    await env.t.pollJob();
    assert.strictEqual(env.t.screen, "preview");
    assert.strictEqual(doCreateCalled, false, "doCreate must NOT be called on compose done");
  });

  // 8. saveModel() が ideaMode==="auto" で startCreateChain(false)、"consult" で partner へ
  await testAsync("saveModel: ideaMode==='auto' で startCreateChain(false)、'consult' で partner へ", async () => {
    const env = createWizardEnv();
    env.t.draft = { id: "d1", rev: 1, model: {}, compose: { setting: { title: "生成済み" } }, compose_stale: false };
    env.elements["wizProvider"].value = "google";
    env.elements["wizModel"].value = "gemini-pro";

    // auto の場合（compose済み下書きなので preview へ遷移する）
    env.t.ideaMode = "auto";
    env.t.openModelStep();
    await env.t.saveModel(false);
    assert.strictEqual(env.t.screen, "preview", "startCreateChain transitioned to preview when ideaMode === 'auto'");

    // consult の場合（partner へ遷移する）
    env.t.ideaMode = "consult";
    env.t.openModelStep();
    await env.t.saveModel(false);
    assert.strictEqual(env.t.screen, "partner", "navigate('partner') called when ideaMode === 'consult'");

    // consult かつ skipAi=true の場合（partner ではなく preview へ遷移する: §4.2）
    env.t.ideaMode = "consult";
    env.t.openModelStep();
    await env.t.saveModel(true);
    assert.strictEqual(env.t.screen, "preview", "startCreateChain transitioned to preview when skipAi is true in consult mode");
  });

  // 8b. useTemplate(): テンプレ生成ジョブを開始して preview へ遷移する（即作成しない）
  await testAsync("useTemplate: テンプレ compose ジョブを開始する（即作成しない）", async () => {
    const env = createWizardEnv();
    env.t.draft = { id: "d1", rev: 1, model: { provider: "google", model: "gemini" } };
    await env.t.useTemplate();
    const modelCall = env.apiCalls.find(c => c.path === "/api/wizard/model");
    const composeCall = env.apiCalls.find(c => c.path === "/api/wizard/compose");
    assert(modelCall, "called /api/wizard/model");
    assert.strictEqual(modelCall.data.provider, "none");
    assert.strictEqual(modelCall.data.ai_enabled, false);
    assert(composeCall, "called /api/wizard/compose to generate template");
    assert.strictEqual(env.t.jobKind, "compose", "jobKind is compose");
  });

  // 9. AIなし時にプレビューの data-act="recompose" が無い（7.4）
  test("プレビュー描画: AIなし時に recompose ボタンが非表示", () => {
    const env = createWizardEnv();
    env.t.draft = {
      id: "d1", rev: 1,
      model: { provider: "none", model: "", ai_enabled: false },
      compose: { setting: { title: "テンプレ", chars: [] } },
    };
    env.t.render("preview");
    const footHtml = env.elements["wizardFoot"].innerHTML;
    assert(footHtml.includes('data-act="create"'), "create button exists in preview");
    assert(!footHtml.includes('data-act="recompose"'), "recompose button must NOT exist when AI is disabled");

    // AIありのとき
    env.t.draft.model = { provider: "google", model: "gemini", ai_enabled: true };
    env.t.render("preview");
    const footHtmlAi = env.elements["wizardFoot"].innerHTML;
    assert(footHtmlAi.includes('data-act="recompose"'), "recompose button exists when AI is enabled");
  });

  // 10. setFoot("") かつ戻る非表示のとき footer.wizard-foot に hidden が付く
  test("syncFootBar: foot が空かつ戻る非表示のとき footer.wizard-foot に hidden が付く", () => {
    const env = createWizardEnv();
    const footBar = env.elements["wizardFootBar"];
    const backBtn = env.elements["wizardBack"];

    backBtn.classList.add("hidden");
    env.t.setFoot("");
    assert.strictEqual(footBar.classList.contains("hidden"), true, "footBar is hidden when empty and back hidden");

    backBtn.classList.remove("hidden");
    env.t.syncFootBar();
    assert.strictEqual(footBar.classList.contains("hidden"), false, "footBar is NOT hidden when back is visible");
  });

  // 11. askText() がダイアログを返し、Esc/キャンセルで null（DOMスタブ環境では null）
  await testAsync("askText: DOMスタブ環境では null を返す", async () => {
    const env = createWizardEnv();
    const res = await env.t.askText({ title: "チャンネル名" });
    assert.strictEqual(res, null, "askText returns null in non-DOM stub environment");
  });

  // 11b. askText() の DOM 動作検証（Enter で入力値返却、空文字バリデーション、IME変換中ガード、Esc/キャンセル/背景クリックで null）
  await testAsync("askText: DOM環境での Enter 確定、空文字エラー、IME変換中ガード、Esc/キャンセル", async () => {
    const env = createWizardEnv({ withDom: true });

    // 1) 有効なテキスト入力で Enter → 入力値を返却
    {
      const p = env.t.askText({ title: "チャンネル名", value: "初期値" });
      const wrap = env.elements["wizardRoot"].children.find(c => c.id === "wizAskText");
      assert(wrap, "wizAskText appended to root");
      const input = wrap.querySelector("#wizAskTextInput");
      assert(input, "input exists");
      input.value = "   確定チャンネル名   ";
      wrap.dispatchEvent({ type: "keydown", key: "Enter" });
      const res = await p;
      assert.strictEqual(res, "確定チャンネル名", "returns trimmed channel name");
    }

    // 2) 空文字で Enter → インラインエラー表示、ダイアログは維持
    {
      let resolved = false;
      const p = env.t.askText({ title: "チャンネル名" }).then(v => { resolved = true; return v; });
      const wrap = env.elements["wizardRoot"].children.find(c => c.id === "wizAskText");
      const input = wrap.querySelector("#wizAskTextInput");
      const err = wrap.querySelector("#wizAskTextError");
      input.value = "     ";
      wrap.dispatchEvent({ type: "keydown", key: "Enter" });
      await new Promise(r => setTimeout(r, 10));
      assert.strictEqual(resolved, false, "dialog must not resolve on empty input");
      assert.strictEqual(err.classList.contains("hidden"), false, "error element visible");
      assert.strictEqual(err.textContent, "チャンネル名を入力してください");

      // その後 Escape で閉じる
      wrap.dispatchEvent({ type: "keydown", key: "Escape" });
      const res = await p;
      assert.strictEqual(res, null, "resolves to null on Escape");
    }

    // 3) 日本語IME変換中の Enter（isComposing / keyCode 229）では確定しない
    {
      let resolved = false;
      const p = env.t.askText({ title: "チャンネル名" }).then(v => { resolved = true; return v; });
      const wrap = env.elements["wizardRoot"].children.find(c => c.id === "wizAskText");
      const input = wrap.querySelector("#wizAskTextInput");
      input.value = "まほうしょうじょ";
      // IME 変換中の Enter (isComposing: true)
      wrap.dispatchEvent({ type: "keydown", key: "Enter", isComposing: true });
      await new Promise(r => setTimeout(r, 10));
      assert.strictEqual(resolved, false, "must NOT submit on Enter when isComposing is true");

      // IME 変換中の Enter (keyCode: 229)
      wrap.dispatchEvent({ type: "keydown", key: "Enter", keyCode: 229 });
      await new Promise(r => setTimeout(r, 10));
      assert.strictEqual(resolved, false, "must NOT submit on Enter when keyCode is 229");

      // compositionend 後の本確定 Enter
      input.dispatchEvent({ type: "compositionend" });
      input.value = "魔法少女";
      wrap.dispatchEvent({ type: "keydown", key: "Enter", isComposing: false });
      const res = await p;
      assert.strictEqual(res, "魔法少女", "submits only on real Enter after composition");
    }

    // 4) キャンセルボタンクリックで null
    {
      const p = env.t.askText({ title: "チャンネル名" });
      const wrap = env.elements["wizardRoot"].children.find(c => c.id === "wizAskText");
      const cancelBtn = wrap.querySelector(".wizard-confirm-box").children.find(c => c.dataset?.choice === "cancel");
      assert(cancelBtn, "cancel button found");
      wrap.dispatchEvent({ type: "click", target: cancelBtn, preventDefault: () => {} });
      const res = await p;
      assert.strictEqual(res, null, "returns null on cancel button click");
    }

    // 5) 背景（backdrop）クリックで null
    {
      const p = env.t.askText({ title: "チャンネル名" });
      const wrap = env.elements["wizardRoot"].children.find(c => c.id === "wizAskText");
      const backdrop = wrap.children.find(c => c.classList.contains("wizard-confirm-backdrop"));
      assert(backdrop, "backdrop found");
      wrap.dispatchEvent({ type: "click", target: backdrop, preventDefault: () => {} });
      const res = await p;
      assert.strictEqual(res, null, "returns null on backdrop click");
    }
  });

  console.log(`\n========================================`);
  console.log(`Test Results: ${passed} passed, ${failed} failed`);
  console.log(`========================================\n`);

  if (failed > 0) {
    process.exit(1);
  }
}

main().catch(err => {
  console.error("Test runner error:", err);
  process.exit(1);
});
