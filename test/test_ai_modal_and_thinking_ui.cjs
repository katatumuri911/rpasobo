// test_ai_modal_and_thinking_ui.cjs
// Acceptance criteria verification for Gemini Thinking Level & AI Settings Modal Consolidation
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let passed = 0;
let failed = 0;
function assert(desc, cond) {
  if (cond) {
    console.log(`  PASS: ${desc}`);
    passed++;
  } else {
    console.error(`  FAIL: ${desc}`);
    failed++;
  }
}

console.log("=== 1. Checking DOM structure in public/index.html ===");
const html = fs.readFileSync(path.join(__dirname, "..", "public", "index.html"), "utf8");

// Helper to count occurrences of id="foo"
function countId(id) {
  const matches = html.match(new RegExp(`id=["']${id}["']`, "g"));
  return matches ? matches.length : 0;
}

assert("#aiToolModal exists and is unique", countId("aiToolModal") === 1);
assert("#openAiToolBtn exists in settingsPanel", countId("openAiToolBtn") === 1);
assert("#aiSummaryRow exists and is unique", countId("aiSummaryRow") === 1);
assert("#aiSummaryText exists and is unique", countId("aiSummaryText") === 1);
assert("#sideAiSummaryRow is removed from sidebar", countId("sideAiSummaryRow") === 0);
assert("#sideAiSummaryText is removed from sidebar", countId("sideAiSummaryText") === 0);
assert("#sideModelContainer is removed from sidebar", countId("sideModelContainer") === 0);
assert("#agQuotaBadge is removed from composer", countId("agQuotaBadge") === 0);
assert(".send-quota-layer exists in #sendBtn", html.includes('class="send-quota-layer"'));
assert("#footThinkingSection exists", countId("footThinkingSection") === 1);
assert("#footThinkingBar exists", countId("footThinkingBar") === 1);
assert("#footChoicesThinkingSelect exists", countId("footChoicesThinkingSelect") === 1);
assert("#googleThinkingPills exists", countId("googleThinkingPills") === 1);
assert("#vertexThinkingPills exists", countId("vertexThinkingPills") === 1);
assert("#choicesThinkingPills exists", countId("choicesThinkingPills") === 1);
assert("#choicesIdeCard exists", countId("choicesIdeCard") === 1);
assert("#choicesIdeModel exists exactly once (no duplicate IDs)", countId("choicesIdeModel") === 1);
assert("#agMainCard exists and is unique", countId("agMainCard") === 1);
assert("#agModel exists and is unique", countId("agModel") === 1);
assert("#agModelsReload exists and is unique", countId("agModelsReload") === 1);
assert("#agModelSubInfo exists and is unique", countId("agModelSubInfo") === 1);
const agIdx = html.indexOf('id="agMainCard"');
const choicesIdx = html.indexOf('id="choicesIdeCard"');
assert("#agMainCard precedes #choicesIdeCard in DOM", agIdx !== -1 && choicesIdx !== -1 && agIdx < choicesIdx);
assert("#modelSheet exists (compatibility retained)", countId("modelSheet") === 1);
assert("#modelSheetSlot exists (compatibility retained)", countId("modelSheetSlot") === 1);
assert("#googleKeyModal stub exists", countId("googleKeyModal") === 1);
assert("#vertexConfigModal stub exists", countId("vertexConfigModal") === 1);

// Verify segment buttons in #footThinkingBar
const segButtons = (html.match(/class=["'][^"']*segment-btn[^"']*["']/g) || []).length;
assert("footThinkingBar has 6 segment buttons", segButtons === 6);

// Verify pills in #googleThinkingPills and #vertexThinkingPills
const googlePills = html.match(/id=["']googleThinkingPills["'][\s\S]*?id=["']google/);
assert("googleThinkingPills contains all 6 levels",
  googlePills &&
  googlePills[0].includes('data-level="auto"') &&
  googlePills[0].includes('data-level="minimal"') &&
  googlePills[0].includes('data-level="low"') &&
  googlePills[0].includes('data-level="medium"') &&
  googlePills[0].includes('data-level="high"') &&
  googlePills[0].includes('data-level="xhigh"')
);

console.log("\n=== 2. Testing ThinkingUI logic and resolvers in public/app.js ===");
const appJs = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8").replace(/\r\n/g, "\n");

// Extract ThinkingUI definition from app.js
const thinkingMatch = appJs.match(/const ThinkingUI = \{([\s\S]*?)\n\};\n\n\/\/ -/);
if (!thinkingMatch) {
  throw new Error("Could not extract ThinkingUI from public/app.js");
}
const thinkingCode = "ThinkingUI = {" + thinkingMatch[1] + "\n};";

// Set up minimal VM sandbox
const sandbox = {
  console,
  document: { activeElement: null },
  sid: "dummy-sid",
  ThinkingUI: null,
  state: { setting: { ai_provider: "google", google_model: "gemini-2.5-flash", thinking_level: "auto", choices_thinking_level: "low" } },
  googleConfigured: true,
  vertexConfigured: false,
  vertexStatus: {},
  $: () => null,
  api: async () => ({ ok: true })
};
vm.createContext(sandbox);
vm.runInContext(thinkingCode, sandbox);
const T = sandbox.ThinkingUI;

// AC-1: Family resolution tests
assert("gemini-2.5-flash -> budget_25_flash", T.thinkingFamily("gemini-2.5-flash") === "budget_25_flash");
assert("gemini-2.5-pro -> budget_25_pro", T.thinkingFamily("gemini-2.5-pro") === "budget_25_pro");
assert("gemini-2-5-flash (hyphenated) -> budget_25_flash", T.thinkingFamily("gemini-2-5-flash") === "budget_25_flash");
assert("gemini-2-5-pro (hyphenated) -> budget_25_pro", T.thinkingFamily("gemini-2-5-pro") === "budget_25_pro");
assert("gemini-3-flash -> level_3_flash", T.thinkingFamily("gemini-3-flash") === "level_3_flash");
assert("gemini-3-flash-lite -> level_3_flash", T.thinkingFamily("gemini-3-flash-lite") === "level_3_flash");
assert("gemini-flash-lite-latest -> level_3_flash", T.thinkingFamily("gemini-flash-lite-latest") === "level_3_flash");
assert("gemini-3-pro -> level_38_4", T.thinkingFamily("gemini-3-pro") === "level_38_4");
assert("gemini-3.8-flash -> level_38_4", T.thinkingFamily("gemini-3.8-flash") === "level_38_4");
assert("gemini-4-carbon -> level_38_4", T.thinkingFamily("gemini-4-carbon") === "level_38_4");
assert("gemini-flash-latest -> level_38_4", T.thinkingFamily("gemini-flash-latest") === "level_38_4");
assert("gemini-4-hydrogen -> level_hydrogen", T.thinkingFamily("gemini-4-hydrogen") === "level_hydrogen");
assert("gemini-2.0-flash -> none", T.thinkingFamily("gemini-2.0-flash") === "none");
assert("gemini-1.5-pro -> none", T.thinkingFamily("gemini-1.5-pro") === "none");
assert("gemma-3-27b -> none", T.thinkingFamily("gemma-3-27b") === "none");
assert("text-embedding-004 -> none", T.thinkingFamily("text-embedding-004") === "none");

// AC-1 / AC-2: ThinkingConfig resolution tests
const r1 = T.resolve("gemini-3-flash", "minimal");
assert("3-flash + minimal -> MINIMAL (no rounding)", r1.eff === "MINIMAL" && !r1.rounded && r1.minOut === 0);

const r2 = T.resolve("gemini-3-pro", "minimal");
assert("3-pro + minimal -> LOW (rounded: true)", r2.eff === "LOW" && r2.rounded === true && r2.minOut === 0);

const r3 = T.resolve("gemini-3-pro", "xhigh");
assert("3-pro + xhigh -> HIGH (rounded: true)", r3.eff === "HIGH" && r3.rounded === true && r3.minOut === 16384);

const r4 = T.resolve("gemini-4-hydrogen", "xhigh");
assert("4-hydrogen + xhigh -> XHIGH (no rounding)", r4.eff === "XHIGH" && !r4.rounded && r4.minOut === 32768);

const r5 = T.resolve("gemini-2.5-flash", "minimal");
assert("2.5-flash + minimal -> budget:0", r5.eff === "budget:0" && r5.minOut === 0);

const r6 = T.resolve("gemini-2.5-pro", "minimal");
assert("2.5-pro + minimal -> budget:128 (minOut 8192)", r6.eff === "budget:128" && r6.minOut === 8192);

const r7 = T.resolve("gemini-2.5-flash", "low");
assert("2.5-flash + low -> budget:1024 (minOut 8192)", r7.eff === "budget:1024" && r7.minOut === 8192);

const r8 = T.resolve("gemini-2.5-flash", "xhigh");
assert("2.5-flash + xhigh -> budget:16384 (minOut 20480)", r8.eff === "budget:16384" && r8.minOut === 20480);

const r9 = T.resolve("gemini-2.0-flash", "high");
assert("2.0-flash -> isNone: true", r9.isNone === true);

// AC-1 / AC-3: Auxiliary choices resolution tests
const rc1 = T.resolveChoices("high", "same", "gemini-3-pro");
assert("Choices 'same' inherits mainLevel (HIGH)", rc1.eff === "HIGH");

const rc2 = T.resolveChoices("xhigh", "same", "gemini-4-hydrogen");
assert("Choices 'same' with xhigh clamps to HIGH", rc2.eff === "HIGH");

const rc3 = T.resolveChoices("auto", "low", "gemini-2.5-flash");
assert("Choices 'low' on 2.5-flash -> budget:1024", rc3.eff === "budget:1024");

// AC-1 / AC-3: Distinct auxiliary model resolution (e.g. main=3.8-flash, aux=3-flash)
const rc4 = T.resolveChoices("low", "minimal", "gemini-3-flash");
assert("Choices 'minimal' on 3-flash -> MINIMAL (not rounded to LOW)", rc4.eff === "MINIMAL" && !rc4.rounded);
const rc5 = T.resolveChoices("minimal", "same", "gemini-3.8-flash");
assert("Choices 'same' on 3.8-flash with main minimal -> LOW (rounded: true)", rc5.eff === "LOW" && rc5.rounded);

console.log("\n=== 3. Testing UI Mock DOM & Bi-directional State Sync ===");
// Stub DOM elements
class MockEl {
  constructor(id) {
    this.id = id;
    this.textContent = "";
    this.value = "";
    this.dataset = {};
    const classes = new Set();
    this.classList = {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle: (c, v) => {
        if (v === undefined) v = !classes.has(c);
        v ? classes.add(c) : classes.delete(c);
        return v;
      }
    };
    this.children = [];
  }
  querySelectorAll(sel) {
    return this.children;
  }
}

const elements = {};
function getOrCreate(id) {
  if (!elements[id]) elements[id] = new MockEl(id);
  return elements[id];
}

// Populate elements
const ids = [
  "aiSummaryText", "footThinkingSection", "footThinkingEff",
  "footChoicesThinkingSelect", "googleCardsSection", "googleThinkingPills",
  "googleThinkingBadge", "googleThinkingGuide", "choicesIdeCard",
  "choicesThinkingPills", "choicesThinkingBadge", "choicesThinkingGuide",
  "googleMaxTokens", "googleMaxTokensBadge", "vertexCardsSection",
  "vertexThinkingPills", "vertexThinkingBadge", "vertexThinkingGuide",
  "vertexMaxTokens", "vertexMaxTokensBadge"
];
ids.forEach(id => getOrCreate(id));

// Add buttons to footThinkingBar
const footBar = getOrCreate("footThinkingSection");
["auto", "minimal", "low", "medium", "high", "xhigh"].forEach(lv => {
  const btn = new MockEl("seg_" + lv);
  btn.dataset.level = lv;
  footBar.children.push(btn);
});

// Add buttons to googleThinkingPills
const gPills = getOrCreate("googleThinkingPills");
["auto", "minimal", "low", "medium", "high", "xhigh"].forEach(lv => {
  const btn = new MockEl("pill_" + lv);
  btn.dataset.level = lv;
  gPills.children.push(btn);
});

// Add buttons to choicesThinkingPills
const cPills = getOrCreate("choicesThinkingPills");
["low", "minimal", "medium", "high", "same", "auto"].forEach(clv => {
  const btn = new MockEl("cpill_" + clv);
  btn.dataset.choicesLevel = clv;
  cPills.children.push(btn);
});

sandbox.$ = (id) => elements[id] || null;

// Run ThinkingUI.render with setting
sandbox.state.setting = {
  ai_provider: "google",
  google_model: "gemini-3.8-flash",
  thinking_level: "minimal",
  choices_thinking_level: "low"
};
sandbox.googleConfigured = true;

T.render.call(T, sandbox.state.setting);

assert("aiSummaryText is rendered for Google",
  elements.aiSummaryText.textContent.includes("AI Studio: gemini-3.8-flash") &&
  elements.aiSummaryText.textContent.includes("💨最小")
);

T.render.call(T, { ai_provider: "antigravity" });
T.render.call(T, sandbox.state.setting);

assert("footThinkingBar segment 'minimal' is active and marked as rounded (3.8-flash)",
  elements.footThinkingSection.children.find(b => b.dataset.level === "minimal").classList.contains("active") &&
  elements.footThinkingSection.children.find(b => b.dataset.level === "minimal").classList.contains("is-rounded")
);

assert("footThinkingEff indicates rounded execution (LOW)",
  elements.footThinkingEff.textContent.includes("LOW (丸め)")
);

assert("googleThinkingBadge indicates minimal is rounded",
  elements.googleThinkingBadge.textContent.includes("LOW") &&
  elements.googleThinkingBadge.textContent.includes("丸め")
);

assert("choicesThinkingBadge indicates LOW",
  elements.choicesThinkingBadge.textContent.includes("LOW")
);

// Test unauthenticated lock
sandbox.googleConfigured = false;
T.render.call(T, sandbox.state.setting);
assert("googleCardsSection gets .is-unauthenticated when not configured",
  elements.googleCardsSection.classList.contains("is-unauthenticated")
);
assert("choicesIdeCard gets .is-unauthenticated when not configured",
  elements.choicesIdeCard.classList.contains("is-unauthenticated")
);

// Test token ceiling warning
sandbox.googleConfigured = true;
sandbox.state.setting.thinking_level = "high"; // HIGH needs minOut 16384 for 3.x
elements.googleMaxTokens.value = "8192";
T.render.call(T, sandbox.state.setting);
assert("googleMaxTokensBadge alerts floor elevation (8192 < 16384)",
  elements.googleMaxTokensBadge.textContent.includes("下限 16384 に自動底上げ")
);

elements.googleMaxTokens.value = "32768";
T.render.call(T, sandbox.state.setting);
assert("googleMaxTokensBadge shows recommendation when >= minOut",
  elements.googleMaxTokensBadge.textContent.includes("推奨下限: 16384")
);

// Test setThinking updates state and sends API request
let apiCalledWith = null;
sandbox.api = async (path, body) => {
  apiCalledWith = { path, body };
  return { ok: true, thinking_level: body.thinking_level, choices_thinking_level: body.choices_thinking_level };
};

(async () => {
  await T.setThinking.call(T, "medium", "minimal");
  assert("setThinking updates state.setting.thinking_level", sandbox.state.setting.thinking_level === "medium");
  assert("setThinking updates state.setting.choices_thinking_level", sandbox.state.setting.choices_thinking_level === "minimal");
  assert("setThinking calls /api/ai/thinking", apiCalledWith.path === "/api/ai/thinking");
  assert("setThinking sends proper body payload",
    apiCalledWith.body.thinking_level === "medium" &&
    apiCalledWith.body.choices_thinking_level === "minimal"
  );

  console.log(`\n========================================`);
  console.log(`RESULTS: ${passed} PASSED, ${failed} FAILED`);
  console.log(`========================================`);
  if (failed > 0) process.exit(1);
})();
