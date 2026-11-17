// test/test_channel_wizard_ui.cjs
// Channel Wizard フロントエンド結合・UI・状態遷移の自動テスト

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

let passed = 0;
let failed = 0;

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
  constructor(id, tag = "div") {
    this.id = id;
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.attrs = {};
    this.dataset = {};
    this.value = "";
    this.checked = false;
    this.textContent = "";
    this.title = "";
    this.listeners = {};
    this._classes = new Set();
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
  get className() { return Array.from(this._classes).join(" "); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
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
    const list = this.listeners[ev.type] || [];
    for (const fn of list) fn.call(this, ev);
  }
  closest() { return null; }
}

const elements = {};
function getOrCreateEl(id, tag = "div") {
  if (!elements[id]) {
    elements[id] = new StubEl(id, tag);
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
const fWizardConfirm = getOrCreateEl("f_wizard_confirm", "input");
const appMsg = getOrCreateEl("appMsg", "span");

const bodyEl = new StubEl("body", "body");
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

  test("<head> 内に wizard.css?v=1 が style.css の後に追加されている", () => {
    const styleIdx = indexHtml.indexOf('<link rel="stylesheet" href="style.css?v=161">');
    const wizardCssIdx = indexHtml.indexOf('<link rel="stylesheet" href="wizard.css?v=1">');
    assert(styleIdx !== -1, "style.css link found");
    assert(wizardCssIdx !== -1, "wizard.css link found");
    assert(wizardCssIdx > styleIdx, "wizard.css must come after style.css");
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

  test("アプリ全体設定内にチャンネル作成AI設定ボックスが存在する", () => {
    assert(indexHtml.includes('id="f_wizard_enabled"'), "#f_wizard_enabled present");
    assert(indexHtml.includes('id="f_wizard_provider"'), "#f_wizard_provider present");
    assert(indexHtml.includes('id="f_wizard_model"'), "#f_wizard_model present");
    assert(indexHtml.includes('id="f_wizard_thinking_level"'), "#f_wizard_thinking_level present");
    assert(indexHtml.includes('id="f_wizard_confirm"'), "#f_wizard_confirm present");

    const appSaveIdx = indexHtml.indexOf('id="appSave"');
    const wizardBoxIdx = indexHtml.indexOf('id="f_wizard_enabled"');
    const decensorBoxIdx = indexHtml.indexOf('id="app_decensor"');
    assert(appSaveIdx < wizardBoxIdx, "wizard settings box is placed after #appSave");
    assert(wizardBoxIdx < decensorBoxIdx, "wizard settings box is placed before app_decensor");
  });

  test("<script> タグの更新（app.js?v=138 と wizard.js?v=1 の追加）", () => {
    assert(!indexHtml.includes('app.js?v=137'), "v=137 is replaced");
    assert(indexHtml.includes('<script src="app.js?v=138"></script>'), "app.js?v=138 present");
    assert(indexHtml.includes('<script src="wizard.js?v=1"></script>'), "wizard.js?v=1 present");

    const appJsScriptIdx = indexHtml.indexOf('<script src="app.js?v=138"></script>');
    const wizardJsScriptIdx = indexHtml.indexOf('<script src="wizard.js?v=1"></script>');
    assert(wizardJsScriptIdx > appJsScriptIdx, "wizard.js must be loaded after app.js");
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

  // [3.4] switchTo, refreshSessions, callLegacyNewSession のグローバル公開
  test("window.switchTo, window.refreshSessions, window.callLegacyNewSession の公開構文", () => {
    assert(appJsSrc.includes("window.switchTo = switchTo;"), "window.switchTo is assigned");
    assert(appJsSrc.includes("window.refreshSessions = refreshSessions;"), "window.refreshSessions is assigned");
    assert(appJsSrc.includes("window.callLegacyNewSession = legacyNewSession;"), "window.callLegacyNewSession is assigned");
  });

  // [3.5] newSession.onclick の振る舞い検証
  await testAsync("newSession.onclick が RPWizard.open() に委譲し、無効時や例外時に legacyNewSession にフォールバックする", async () => {
    const startIdx = appJsSrc.indexOf("async function legacyNewSession() {");
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
      prompt: () => null, // prompt キャンセル
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
    // legacyNewSession の定義を注入して実行回数をカウント
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
    fWizardConfirm.checked = false;

    await elements["appSave"].onclick();

    assert(sentPayload, "payload was sent to /api/app-settings");
    assert.strictEqual(sentPayload.new_channel_ide, true);
    assert.strictEqual(sentPayload.wizard_enabled, true);
    assert.strictEqual(sentPayload.wizard_provider, "vertex");
    assert.strictEqual(sentPayload.wizard_model, "gemini-2.5-flash");
    assert.strictEqual(sentPayload.wizard_thinking_level, "medium");
    assert.strictEqual(sentPayload.wizard_confirm, false);
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
