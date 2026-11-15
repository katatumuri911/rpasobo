const fs = require("fs");
const path = require("path");
const vm = require("vm");

let passCount = 0;
let failCount = 0;

function assert(msg, condition) {
  if (condition) {
    console.log(`  PASS: ${msg}`);
    passCount++;
  } else {
    console.error(`  FAIL: ${msg}`);
    failCount++;
  }
}

console.log("=== 1. Checking DOM structure in public/index.html ===");
const html = fs.readFileSync(path.join(__dirname, "..", "public", "index.html"), "utf8");

assert("#favModelBtn exists in index.html", html.includes('id="favModelBtn"'));
assert("#favModelLabel exists in index.html", html.includes('id="favModelLabel"'));
assert("#favModelSheet exists in index.html", html.includes('id="favModelSheet"'));
assert("#favModelList exists in index.html", html.includes('id="favModelList"'));
assert("#favModelCurrent exists in index.html", html.includes('id="favModelCurrent"'));
assert("#favCount exists in index.html", html.includes('id="favCount"'));
assert("#favSaveArea exists in index.html", html.includes('id="favSaveArea"'));
assert("#favModelEmpty exists in index.html", html.includes('id="favModelEmpty"'));
assert("#favEmptyOpenSettings exists in index.html", html.includes('id="favEmptyOpenSettings"'));
assert("#favUndoBar exists in index.html", html.includes('id="favUndoBar"'));
assert("#favUndoBtn exists in index.html", html.includes('id="favUndoBtn"'));
assert("#favUndoText exists in index.html", html.includes('id="favUndoText"'));
assert("#favOpenAiModalBtn exists in index.html", html.includes('id="favOpenAiModalBtn"'));
assert("#favModelSheetClose exists in index.html", html.includes('id="favModelSheetClose"'));
assert("#favNameModal exists in index.html", html.includes('id="favNameModal"'));
assert("#favPickerModal exists in index.html", html.includes('id="favPickerModal"'));
assert("#favDeleteModal exists in index.html", html.includes('id="favDeleteModal"'));

console.log("\n=== 2. Checking CSS rules in public/style.css ===");
const css = fs.readFileSync(path.join(__dirname, "..", "public", "style.css"), "utf8");

assert(".fav-model-sheet has height: auto", css.includes(".modal-content.compact.fav-model-sheet") && css.includes("height: auto;"));
assert(".fav-row-apply is removed (no 切替 label)", !css.includes(".fav-row-apply"));
assert(".fav-row-delete exists in style.css", css.includes(".fav-row-delete"));
assert(".fav-row-edit exists in style.css", css.includes(".fav-row-edit"));
assert(".fav-model-body exists in style.css", css.includes(".fav-model-body"));
assert(".fav-undo-bar exists in style.css", css.includes(".fav-undo-bar"));
assert(".fav-cur-row (現在の組み合わせの ＞ 行) exists in style.css", css.includes(".fav-cur-row"));
assert(".fav-name-field (名前モーダル) exists in style.css", css.includes(".fav-name-field"));
assert(".fav-pick-item (ピッカー行) exists in style.css", css.includes(".fav-pick-item"));
assert(".fav-delete-card (削除確認) exists in style.css", css.includes(".fav-delete-card"));
assert("old .fav-config-row is removed", !css.includes(".fav-config-row"));
assert("old .fav-save-editor is removed", !css.includes(".fav-save-editor"));
assert("#favEmptyOpenSettings is styled via .fav-empty-btn", css.includes(".fav-empty-btn") && html.includes('id="favEmptyOpenSettings" type="button" class="fav-empty-btn"'));

console.log("\n=== 3. Testing Model Favorites Logic & Mock UI in VM Sandbox ===");
const appJs = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8").replace(/\r\n/g, "\n");

assert("#favModelBtn pointerdown prevents blur in app.js", appJs.includes('favBtn.addEventListener("pointerdown"') && appJs.includes("e.preventDefault()"));

// Mock element helper
class MockElement {
  constructor(id = "") {
    this.id = id;
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.title = "";
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.classList = {
      _classes: new Set(),
      add: (...cls) => cls.forEach((c) => this.classList._classes.add(c)),
      remove: (...cls) => cls.forEach((c) => this.classList._classes.delete(c)),
      toggle: (c, force) => {
        if (force === undefined) {
          if (this.classList._classes.has(c)) this.classList._classes.delete(c);
          else this.classList._classes.add(c);
        } else if (force) {
          this.classList._classes.add(c);
        } else {
          this.classList._classes.delete(c);
        }
      },
      contains: (c) => this.classList._classes.has(c),
    };
    this.listeners = {};
  }

  get className() { return Array.from(this.classList._classes).join(" "); }
  set className(val) {
    this.classList._classes.clear();
    String(val || "").split(/\s+/).filter(Boolean).forEach(c => this.classList._classes.add(c));
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k]; }
  appendChild(child) {
    this.children.push(child);
    if (child && child.dataset) {
      if (!this._byData) this._byData = [];
      this._byData.push(child);
    }
    return child;
  }
  querySelectorAll(sel) {
    if (sel.startsWith("[data-fav-") && this._byData) {
      const key = sel.replace(/[[\]]/g, "").replace("data-fav-", "").replace("-", "");
      return this._byData.filter((c) => Object.keys(c.dataset || {}).some((k) => k.toLowerCase() === "fav" + key));
    }
    return [];
  }
  querySelector(sel) {
    if (sel.startsWith("[data-fav-delete]")) {
      return this.children.find(c => c.dataset && c.dataset.favDelete) || null;
    }
    if (sel.startsWith("[data-fav-edit]")) {
      return this.children.find(c => c.dataset && c.dataset.favEdit) || null;
    }
    return null;
  }
  closest(sel) {
    if (sel.includes("data-fav-delete") && this.dataset && this.dataset.favDelete) return this;
    return null;
  }
  addEventListener(event, fn) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(fn);
  }
  dispatchEvent(event) {
    (this.listeners[event.type] || []).forEach(fn => fn(event));
  }
  focus() {}
  select() {}
}

const mockDom = {
  favModelBtn: new MockElement("favModelBtn"),
  favModelLabel: new MockElement("favModelLabel"),
  favModelSheet: new MockElement("favModelSheet"),
  favModelList: new MockElement("favModelList"),
  favModelCurrent: new MockElement("favModelCurrent"),
  favCount: new MockElement("favCount"),
  favSaveArea: new MockElement("favSaveArea"),
  favModelEmpty: new MockElement("favModelEmpty"),
  favUndoBar: new MockElement("favUndoBar"),
  favUndoText: new MockElement("favUndoText"),
  favUndoBtn: new MockElement("favUndoBtn"),
  favNameModal: new MockElement("favNameModal"),
  favNameInput: new MockElement("favNameInput"),
  favNameCount: new MockElement("favNameCount"),
  favNamePreview: new MockElement("favNamePreview"),
  favNameSave: new MockElement("favNameSave"),
  favNameCancel: new MockElement("favNameCancel"),
  favNameModalTitle: new MockElement("favNameModalTitle"),
  favPickerModal: new MockElement("favPickerModal"),
  favPickerTitle: new MockElement("favPickerTitle"),
  favPickerSub: new MockElement("favPickerSub"),
  favPickerNote: new MockElement("favPickerNote"),
  favPickerList: new MockElement("favPickerList"),
  favPickerThink: new MockElement("favPickerThink"),
  favPickerThinkTitle: new MockElement("favPickerThinkTitle"),
  favPickerThinkChips: new MockElement("favPickerThinkChips"),
  favPickerEff: new MockElement("favPickerEff"),
  favPickerProviderRow: new MockElement("favPickerProviderRow"),
  favPickerChoicesRow: new MockElement("favPickerChoicesRow"),
  favPickerRename: new MockElement("favPickerRename"),
  favDeleteModal: new MockElement("favDeleteModal"),
  favDeleteName: new MockElement("favDeleteName"),
  favDeleteDesc: new MockElement("favDeleteDesc"),
  favDeleteWarn: new MockElement("favDeleteWarn"),
  favDeleteModel: new MockElement("favDeleteModel"),
  favDeleteCancel: new MockElement("favDeleteCancel"),
  favDeleteConfirm: new MockElement("favDeleteConfirm"),
  googleModel: new MockElement("googleModel"),
  choicesIdeModel: new MockElement("choicesIdeModel"),
  choicesIdeCard: new MockElement("choicesIdeCard"),
  aiSummaryText: new MockElement("aiSummaryText"),
  sideAiSummaryText: new MockElement("sideAiSummaryText"),
};
mockDom.favModelSheet.classList.add("hidden");
mockDom.favUndoBar.classList.add("hidden");

// Extract block of favorites code + ThinkingUI from app.js
const favCodeMatch = appJs.match(/\/\/ ---------------------------------------------------------------- お気に入りモデル管理([\s\S]*?)\/\/ ---------------------------------------------------------------- Thinking Level UI & Resolvers/);
if (!favCodeMatch) {
  throw new Error("Could not find model favorites code in public/app.js");
}

const sandbox = {
  console,
  document: {
    activeElement: null,
    createElement: (tag) => new MockElement(),
    addEventListener: () => {},
  },
  $: (id) => mockDom[id] || null,
  esc: (s) => String(s || ""),
  sid: "test-sid",
  state: {
    setting: {
      ai_provider: "google",
      google_model: "gemini-2.5-flash",
      google_choices_model: "",
      thinking_level: "auto",
      choices_thinking_level: "low",
    }
  },
  googleConfigured: true,
  vertexConfigured: false,
  googleModels: [
    { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", shortLabel: "2.5 Flash" },
    { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", shortLabel: "2.5 Pro" },
  ],
  vertexModels: [],
  ideModels: null,
  toast: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  api: async (url, opts) => ({ ok: true, favorites: [] }),
  ThinkingUI: {
    LEVEL_LABELS: { auto: "自動", minimal: "💨最小", low: "⚡低", medium: "⚖️中", high: "🔍高", xhigh: "🔥最大" },
    CHOICES_LEVEL_LABELS: { low: "⚡低 (速・推奨)", minimal: "💨最小", medium: "⚖️中", high: "🔍高", same: "🔗本文と同じ", auto: "自動" },
    render: () => {},
    resolve: () => ({ eff: "MEDIUM", rounded: false, isNone: false }),
    resolveChoices: () => ({ eff: "LOW", rounded: false, isNone: false }),
  },
  syncGoogleSettingsUI: () => {},
  applyProviderSettings: () => {},
};

vm.createContext(sandbox);

vm.runInContext(favCodeMatch[1], sandbox);

sandbox.getModelFavorites = () => vm.runInContext('modelFavorites', sandbox);
sandbox.setModelFavorites = (val) => vm.runInContext('modelFavorites = ' + JSON.stringify(val), sandbox);

// 1. Check choicesThinkingLabel resolves "same" to Japanese
assert("choicesThinkingLabel('same') returns '本文と同じ'", sandbox.choicesThinkingLabel("same") === "本文と同じ");
assert("choicesThinkingLabel('low') returns '⚡低 (速・推奨)'", sandbox.choicesThinkingLabel("low") === "⚡低 (速・推奨)");

// 1b. Antigravity: 内部ID（MODEL_PLACEHOLDER_...）ではなく /api/ide/models の表示名を出す
sandbox.ideModels = {
  default: "MODEL_PLACEHOLDER_M318",
  models: [
    { model: "MODEL_PLACEHOLDER_M318", label: "Gemini 3.8 Flash (High)", left: 0.62 },
    { model: "MODEL_PLACEHOLDER_M319", label: "Gemini 3.8 Flash (Minimal)", left: 0.4 },
  ],
};
assert(
  "Antigravity model id resolves to its label",
  sandbox.shortModelLabel("antigravity", "MODEL_PLACEHOLDER_M318") === "Gemini 3.8 Flash (High)",
  sandbox.shortModelLabel("antigravity", "MODEL_PLACEHOLDER_M318")
);
assert(
  "Antigravity choices id resolves to its label",
  sandbox.shortModelLabel("antigravity", "MODEL_PLACEHOLDER_M319") === "Gemini 3.8 Flash (Minimal)",
  sandbox.shortModelLabel("antigravity", "MODEL_PLACEHOLDER_M319")
);
assert(
  "Antigravity picker rows use the label",
  (() => {
    const rows = sandbox.favModelRows("antigravity");
    return rows.length === 2 && rows[0].id === "MODEL_PLACEHOLDER_M318" && rows[0].label === "Gemini 3.8 Flash (High)";
  })()
);
assert(
  "Unknown Antigravity id falls back to the raw id",
  sandbox.shortModelLabel("antigravity", "MODEL_PLACEHOLDER_M999") === "MODEL_PLACEHOLDER_M999"
);
sandbox.ideModels = null;

// 2. Initial state with no favorites -> custom chip
sandbox.syncComposerModelChip();
assert("Chip shows model name when no favorite matches", mockDom.favModelLabel.textContent === "2.5 Flash");
assert("Chip has is-custom class", mockDom.favModelBtn.classList.contains("is-custom"));
assert("Chip does NOT have is-favorite class", !mockDom.favModelBtn.classList.contains("is-favorite"));

// 3. Add an active favorite and sync
sandbox.setModelFavorites([
  {
    id: "fav_flash",
    name: "高速Flash",
    provider: "google",
    main: "gemini-2.5-flash",
    choices: "",
    thinking: "auto",
    choices_thinking: "low",
    order: 0,
  },
  {
    id: "fav_pro",
    name: "精密Pro",
    provider: "google",
    main: "gemini-2.5-pro",
    choices: "",
    thinking: "high",
    choices_thinking: "low",
    order: 1,
  }
]);

sandbox.syncComposerModelChip();
assert("Chip shows favorite name when matching active config", mockDom.favModelLabel.textContent === "高速Flash");
assert("Chip has is-favorite class", mockDom.favModelBtn.classList.contains("is-favorite"));
assert("Chip does NOT have is-custom class", !mockDom.favModelBtn.classList.contains("is-custom"));
assert("Chip tooltip does not leak 'same' when choices is empty", !mockDom.favModelBtn.title.includes("same"));

// 4. Test renderFavModelSheet
sandbox.renderFavModelSheet();
assert("favModelList is visible", !mockDom.favModelList.classList.contains("hidden"));
assert("favModelEmpty is hidden", mockDom.favModelEmpty.classList.contains("hidden"));
assert("List has 2 rows", mockDom.favModelList.children.length === 2);

const activeRow = mockDom.favModelList.children[0];
const inactiveRow = mockDom.favModelList.children[1];

assert("First row has is-active class", activeRow.classList.contains("is-active"));
assert("First row has aria-selected=true", activeRow.getAttribute("aria-selected") === "true");
assert("First row contains '使用中' badge", activeRow.innerHTML.includes("fav-badge") && activeRow.innerHTML.includes("使用中"));
  assert("First row has no 切替 label", !activeRow.innerHTML.includes("切替"));
assert("Row meta has no 残り (quota) text", !activeRow.innerHTML.includes("残り"));
assert("First row has delete button with data-fav-delete", activeRow.innerHTML.includes('data-fav-delete="fav_flash"'));
assert("First row has ＞ (edit) button with data-fav-edit", activeRow.innerHTML.includes('data-fav-edit="fav_flash"'));
assert(
  "Row title order is star, provider badge, name, then 使用中",
  (() => {
    const html = activeRow.innerHTML.replace(/\s+/g, " ");
    const star = html.indexOf("fav-row-star");
    const badge = html.indexOf("fav-provider-badge");
    const name = html.indexOf("fav-row-name");
    const used = html.indexOf("fav-badge");
    return star >= 0 && badge > star && name > badge && used > name;
  })()
);

assert("Second row does NOT have is-active class", !inactiveRow.classList.contains("is-active"));
assert("Second row has aria-selected=false", inactiveRow.getAttribute("aria-selected") === "false");
assert("Second row does not have '使用中' badge", !inactiveRow.innerHTML.includes("使用中"));

// 5. Test deletion and chip fallback
let apiCalls = [];
sandbox.api = async (url, opts) => {
  const body = (opts && typeof opts === "object" && typeof opts.body === "string")
    ? JSON.parse(opts.body)
    : (opts || {});
  apiCalls.push({ url, body });
  const currentFavs = sandbox.getModelFavorites();
  if (body.action === "delete") {
    const deleted = currentFavs.find(f => f.id === body.id);
    const newFavs = currentFavs.filter(f => f.id !== body.id);
    return { ok: true, favorites: newFavs, deleted, index: 0 };
  }
  if (body.action === "restore") {
    const newFavs = [...currentFavs];
    newFavs.splice(body.index, 0, body.favorite);
    return { ok: true, favorites: newFavs, favorite: body.favorite };
  }
  return { ok: true, favorites: currentFavs };
};

(async () => {
  // 削除は確認モーダルを経由する
  sandbox.deleteModelFavorite("fav_flash");
  assert("Delete opens the confirm modal", !mockDom.favDeleteModal.classList.contains("hidden"));
  assert("Delete modal names the favorite", mockDom.favDeleteName.textContent.includes("高速Flash"));
  assert("Delete modal warns that the favorite is in use", !mockDom.favDeleteWarn.classList.contains("hidden"));
  assert("Delete API is NOT called before confirming", apiCalls.length === 0);

  await sandbox.confirmFavDelete();

  assert("Delete modal is closed after confirming", mockDom.favDeleteModal.classList.contains("hidden"));
  assert("Delete API was called with id fav_flash", apiCalls.some(c => c.body.action === "delete" && c.body.id === "fav_flash"));
  assert("Undo bar is shown", !mockDom.favUndoBar.classList.contains("hidden"));
  assert("Undo bar shows item name", mockDom.favUndoText.textContent.includes("高速Flash"));

  // Verify chip fell back to model name
  assert("Chip fell back to model name after deleting active fav", mockDom.favModelLabel.textContent === "2.5 Flash");
  assert("Chip has is-custom after deletion", mockDom.favModelBtn.classList.contains("is-custom"));
  assert("Current model in state.setting was NOT changed", sandbox.state.setting.google_model === "gemini-2.5-flash");

  // 6. Test restoreDeletedFavorite (Undo)
  await sandbox.restoreDeletedFavorite();
  assert("Restore API was called with index 0", apiCalls.some(c => c.body.action === "restore" && c.body.index === 0));
  assert("Undo bar is hidden after restore", mockDom.favUndoBar.classList.contains("hidden"));
  assert("Restored favorite is active again", mockDom.favModelLabel.textContent === "高速Flash");
  assert("Chip has is-favorite after restore", mockDom.favModelBtn.classList.contains("is-favorite"));

  // 7. Test Empty State
  sandbox.setModelFavorites([]);
  sandbox.renderFavModelSheet();
  assert("favModelList is hidden when favorites is empty", mockDom.favModelList.classList.contains("hidden"));
  assert("favModelEmpty is visible when favorites is empty", !mockDom.favModelEmpty.classList.contains("hidden"));

  // 8. Test Keyboard & Event Handling
  sandbox.setModelFavorites([
    { id: "fav_1", name: "キー操作テスト", provider: "google", main: "gemini-2.5-flash", choices: "", thinking: "auto", choices_thinking: "low", order: 0 }
  ]);
  sandbox.renderFavModelSheet();
  const testRow = mockDom.favModelList.children[0];

  // Enter on row triggers apply (which calls /api/ai/provider)
  apiCalls = [];
  let enterEvt = { type: "keydown", key: "Enter", preventDefault: () => {}, target: testRow };
  testRow.dispatchEvent(enterEvt);
  // Wait a microtask for async applyModelFavorite
  await new Promise(r => setTimeout(r, 10));
  assert("Enter key on row triggers applyModelFavorite", apiCalls.some(c => c.url === "/api/ai/provider"));

  // Key event on delete button does NOT trigger apply
  apiCalls = [];
  const mockDelBtn = {
    dataset: { favDelete: "fav_1" },
    closest: (sel) => sel.includes("data-fav-delete") ? true : null
  };
  let delKeyEvt = { type: "keydown", key: "Enter", preventDefault: () => {}, target: mockDelBtn };
  testRow.dispatchEvent(delKeyEvt);
  await new Promise(r => setTimeout(r, 10));
  assert("Enter key on delete button does NOT trigger applyModelFavorite", apiCalls.length === 0);

  // 9. 常設の「お気に入りを登録」ボタン（名前の変更はピッカー側へ移動）
  sandbox.setModelFavorites([
    { id: "fav_a", name: "登録テスト", provider: "google", main: "gemini-2.5-flash", choices: "", thinking: "auto", choices_thinking: "low", order: 0 }
  ]);
  sandbox.state.setting.google_model = "gemini-2.5-flash";
  sandbox.state.setting.google_choices_model = "";
  sandbox.state.setting.thinking_level = "auto";
  sandbox.state.setting.choices_thinking_level = "low";
  mockDom.favModelSheet.classList.remove("hidden");
  sandbox.renderFavSaveControls();
  const saveBtn = mockDom.favSaveArea.children[0];
  assert("Save area always has exactly one button", mockDom.favSaveArea.children.length === 1);
  assert("Save button is the permanent register button", saveBtn.innerHTML.includes("お気に入りを登録"));
  assert("Save button is not the rename variant", !saveBtn.innerHTML.includes("名前を変える"));
  assert("Save button has no is-edit class", !saveBtn.classList.contains("is-edit"));

  // 10. ピッカー（本文のモデルと思考）の中に名前の変更がある
  sandbox.openFavPicker("main", "current");
  assert("Rename row is visible in main picker for a saved combination", !mockDom.favPickerRename.classList.contains("hidden"));
  assert("Rename row shows the favorite name", mockDom.favPickerRename.textContent.includes("登録テスト"));

  sandbox.closeFavPicker();
  sandbox.state.setting.google_model = "gemini-2.5-pro";
  sandbox.openFavPicker("main", "current");
  assert("Rename row is hidden when the combination is not saved", mockDom.favPickerRename.classList.contains("hidden"));

  sandbox.closeFavPicker();
  sandbox.state.setting.google_model = "gemini-2.5-flash";
  sandbox.openFavPicker("choices", "current");
  assert("Rename row is hidden in the choices picker", mockDom.favPickerRename.classList.contains("hidden"));
  sandbox.closeFavPicker();
  sandbox.state.setting.google_model = "gemini-2.5-pro";

  // 11. Test FavSheet open/close
  sandbox.openFavModelSheet();
  assert("openFavModelSheet removes hidden from sheet", !mockDom.favModelSheet.classList.contains("hidden"));
  sandbox.closeFavModelSheet();
  assert("closeFavModelSheet adds hidden to sheet", mockDom.favModelSheet.classList.contains("hidden"));

  console.log("\n========================================");
  console.log(`RESULTS: ${passCount} PASSED, ${failCount} FAILED`);
  console.log("========================================");

  if (failCount > 0) process.exit(1);
})();
