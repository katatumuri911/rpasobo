// test/test_poll_error_ui.cjs
// (U-02) サーバーエラー時の強制送還抑止・エラー表示・復帰処理の自動検証テスト
// 実行: node test/test_poll_error_ui.cjs

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

let passedCount = 0;
let totalCount = 0;

function it(description, fn) {
  totalCount++;
  try {
    fn();
    console.log(`  PASS: ${description}`);
    passedCount++;
  } catch (err) {
    console.error(`  FAIL: ${description}`);
    console.error(err);
    process.exitCode = 1;
  }
}

async function itAsync(description, fn) {
  totalCount++;
  try {
    await fn();
    console.log(`  PASS: ${description}`);
    passedCount++;
  } catch (err) {
    console.error(`  FAIL: ${description}`);
    console.error(err);
    process.exitCode = 1;
  }
}

console.log("=== 1. HTML & CSS Static Attribute Verification ===");

const indexHtml = fs.readFileSync(path.join(__dirname, "..", "public", "index.html"), "utf8");
const styleCss = fs.readFileSync(path.join(__dirname, "..", "public", "style.css"), "utf8");

it("index.html: #statusPill has role='status' and aria-live='polite'", () => {
  const match = indexHtml.match(/<span[^>]*id=["']statusPill["'][^>]*>/);
  assert(match, "statusPill element found");
  assert(match[0].includes('role="status"'), "statusPill has role='status'");
  assert(match[0].includes('aria-live="polite"'), "statusPill has aria-live='polite'");
});

it("index.html: #mobileStatusChip has role='status' and aria-live='polite'", () => {
  const match = indexHtml.match(/<span[^>]*id=["']mobileStatusChip["'][^>]*>/);
  assert(match, "mobileStatusChip element found");
  assert(match[0].includes('role="status"'), "mobileStatusChip has role='status'");
  assert(match[0].includes('aria-live="polite"'), "mobileStatusChip has aria-live='polite'");
});

it("style.css: #mobileStatusChip.err has pointer-events: auto and cursor: pointer", () => {
  const match = styleCss.match(/#mobileStatusChip\.err\s*\{([^}]+)\}/);
  assert(match, "#mobileStatusChip.err rule found");
  assert(match[1].includes("pointer-events: auto"), "has pointer-events: auto");
  assert(match[1].includes("cursor: pointer"), "has cursor: pointer");
});

it("style.css: .pill.err has cursor: pointer", () => {
  const match = styleCss.match(/\.pill\.err\s*\{([^}]+)\}/);
  assert(match, ".pill.err rule found");
  assert(match[1].includes("cursor: pointer"), ".pill.err has cursor: pointer");
});

console.log("=== 2. Functional Environment Setup & DOM Stubs ===");

class FakeElement {
  constructor(id, tagName = "div") {
    this.id = id;
    this.tagName = tagName.toUpperCase();
    this.className = "";
    this.textContent = "";
    this.title = "";
    this.value = "";
    this.placeholder = "";
    this.style = {};
    this.listeners = {};
    const self = this;
    this.classList = {
      _set: () => new Set(self.className.split(/\s+/).filter(Boolean)),
      add: (...cls) => {
        const s = self.classList._set();
        cls.forEach((c) => s.add(c));
        self.className = [...s].join(" ");
      },
      remove: (...cls) => {
        const s = self.classList._set();
        cls.forEach((c) => s.delete(c));
        self.className = [...s].join(" ");
      },
      contains: (c) => self.classList._set().has(c),
      toggle: (c, force) => {
        const s = self.classList._set();
        const cur = s.has(c);
        const next = typeof force === "boolean" ? force : !cur;
        if (next) s.add(c);
        else s.delete(c);
        self.className = [...s].join(" ");
        return next;
      }
    };
  }
  addEventListener(evt, fn) {
    (this.listeners[evt] = this.listeners[evt] || []).push(fn);
  }
  click() {
    (this.listeners["click"] || []).forEach((fn) => fn({ target: this }));
  }
  keydown(key) {
    (this.listeners["keydown"] || []).forEach((fn) => fn({ target: this, key, preventDefault: () => {} }));
  }
}

function createTestContext() {
  const elements = {
    statusPill: new FakeElement("statusPill", "span"),
    mobileStatusChip: new FakeElement("mobileStatusChip", "span"),
    sideStatusPill: new FakeElement("sideStatusPill", "span"),
    title: new FakeElement("title", "h1"),
    sideTitle: new FakeElement("sideTitle", "div"),
    ragCount: new FakeElement("ragCount", "span"),
    sideRagCount: new FakeElement("sideRagCount", "span"),
    messageInput: new FakeElement("messageInput", "textarea"),
    rpToggle: new FakeElement("rpToggle", "button"),
    sideRpToggle: new FakeElement("sideRpToggle", "button"),
    log: new FakeElement("log", "div"),
  };
  elements.statusPill.className = "pill ok";
  elements.statusPill.textContent = "接続中…";

  const toasts = [];
  const toastFn = (msg, opts) => {
    toasts.push({ msg, opts });
  };

  const context = {
    console,
    Date,
    JSON,
    Set,
    setTimeout,
    clearTimeout,
    toasts,
    toast: toastFn,
    elements,
    $: (id) => elements[id] || null,
    document: {
      hidden: false,
      getElementById: (id) => elements[id] || null,
      querySelector: () => null,
      body: { classList: elements.log.classList },
    },
    window: {
      matchMedia: (query) => ({
        matches: query.includes("360px") ? context.isTiny : false,
        addEventListener: () => {},
        addListener: () => {},
      })
    },
    isTiny: false,
    mqTiny: {
      get matches() { return context.isTiny; },
      addEventListener: () => {},
      addListener: () => {},
    },
    rpMode: false,
    sid: "test-session-123",
    lastRev: -1,
    state: null,
    historyState: { sid: null, hasMore: true, loadedChunks: 0, nextCursor: null },
    markSeen: () => {},
    switchToCalls: [],
    switchTo: async (id) => {
      context.switchToCalls.push(id);
      context.sid = id;
    },
    refreshSessionsCalls: 0,
    refreshSessions: async () => {
      context.refreshSessionsCalls++;
    },
    sessions: [{ id: "test-session-123", rev: 1, title: "テスト" }],
    seen: {},
    renderSessions: () => {},
    apiResponse: null,
    api: async (url) => {
      if (typeof context.apiResponse === "function") {
        return context.apiResponse(url);
      }
      return context.apiResponse;
    },
    syncActionControlsVisibility: () => {},
    syncSendButton: () => {},
    announceArrivals: () => {},
    fillForm: () => {},
    renderChars: () => {},
    checkPromptModalRefine: () => {},
    checkDecensorDiff: () => {},
    resetHistoryStateForSession: () => {},
    resetDecensorToast: () => {},
    renderLastAsstId: "",
    formDirty: false,
    formSid: "",
    choicesNoticeToasted: new Set(),
    lastRotationNotice: "",
    flash: () => {},
    lastAutoScrolledAsstId: "",
    navHoldBottom: false,
    pinnedBottom: true,
    log: elements.log,
  };

  const appJsSrc = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

  // Extract SHORT_STATUS, initStatusPillErrorToast, updateHeaderAndSideStatus, render, poll
  const codeToRun = `
${appJsSrc.slice(appJsSrc.indexOf("const SHORT_STATUS ="), appJsSrc.indexOf("const PLACEHOLDER_DESKTOP ="))}
${appJsSrc.slice(appJsSrc.indexOf("function initStatusPillErrorToast()"), appJsSrc.indexOf("function updateHeaderAndSideStatus()"))}
${appJsSrc.slice(appJsSrc.indexOf("function updateHeaderAndSideStatus()"), appJsSrc.indexOf("function syncFootBarPlacement()"))}
function render() {
  const waitingInput = state?.messages?.some((m) => m.role === "user" && (m.status === "pending" || m.status === "processing"));
  const waitingChoices = false;
  const waitingRefine = false;
  const waitingMemory = false;
  const pill = $("statusPill");
  pill.title = "";
  pill.className = "pill " + (waitingInput ? "wait" : "ok");
  pill.textContent = waitingInput ? "AI応答待ち" : "入力待機";
  updateHeaderAndSideStatus();
}
${appJsSrc.slice(appJsSrc.indexOf("async function refreshSessions()"), appJsSrc.indexOf("async function switchTo(")).replace("async function refreshSessions()", "async function realRefreshSessions()")}
${appJsSrc.slice(appJsSrc.indexOf("async function poll()"), appJsSrc.indexOf("let progressHtml ="))}
`;

  vm.createContext(context);
  vm.runInContext(codeToRun, context);
  return context;
}

(async () => {
  console.log("=== 3. Error Handling and Forced Switch Prevention ===");

  await itAsync("Server error (500 / locked) does NOT switch session or clear sid", async () => {
    const ctx = createTestContext();
    ctx.elements.messageInput.value = "ユーザーが入力中の大事な長文";
    ctx.sid = "active-chat-id";
    ctx.apiResponse = { error: "no such session (temporary lock)" };

    await ctx.poll();

    assert.strictEqual(ctx.sid, "active-chat-id", "sid should remain unchanged");
    assert.strictEqual(ctx.switchToCalls.length, 0, "switchTo must not be called");
    assert.strictEqual(ctx.refreshSessionsCalls, 0, "refreshSessions must not be called on poll error");
    assert.strictEqual(ctx.elements.messageInput.value, "ユーザーが入力中の大事な長文", "input text must be preserved");
    assert.strictEqual(ctx.elements.statusPill.className, "pill err", "statusPill should be pill err");
    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました", "statusPill text should be エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラー: no such session (temporary lock)", "tooltip should contain error detail");
  });

  await itAsync("Object error format is sanitized and does not produce [object Object]", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: { message: "Internal Server Error 500", code: 500 } };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラー: Internal Server Error 500");
    assert(!ctx.elements.statusPill.title.includes("[object Object]"), "title must not be [object Object]");
  });

  await itAsync("Object error without message serializes to JSON string", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: { code: 503, reason: "Service Unavailable" } };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert(ctx.elements.statusPill.title.includes("503"), "title must serialize json");
    assert(!ctx.elements.statusPill.title.includes("[object Object]"), "title must not be [object Object]");
  });

  await itAsync("Proxy/gateway error with detail (e.g. { detail: '502 Bad Gateway' }) preserves state and shows error", async () => {
    const ctx = createTestContext();
    ctx.state = { id: "test-session-123", rev: 1, messages: [{ role: "user", text: "hello" }] };
    ctx.lastRev = "test-session-123:1";
    ctx.apiResponse = { detail: "502 Bad Gateway" };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.className, "pill err");
    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラー: 502 Bad Gateway");
    assert.strictEqual(ctx.state.id, "test-session-123", "state must NOT be corrupted by detail error response");
    assert.strictEqual(ctx.lastRev, "test-session-123:1", "lastRev must NOT be corrupted");
  });

  await itAsync("Response without id or messages (e.g. { message: '503 Service Unavailable' }) is safely treated as error", async () => {
    const ctx = createTestContext();
    ctx.state = { id: "test-session-123", rev: 1, messages: [] };
    ctx.lastRev = "test-session-123:1";
    ctx.apiResponse = { message: "503 Service Unavailable" };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.className, "pill err");
    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラー: 503 Service Unavailable");
    assert.strictEqual(ctx.state.id, "test-session-123", "state must NOT be corrupted");
  });

  await itAsync("Null or non-object API response does not throw and sets status to error", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = null;

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.className, "pill err");
    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラーが発生しました");
  });

  await itAsync("Empty object error { error: {} } displays fallback without ugly エラー: {}", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: {} };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラーが発生しました", "must not show エラー: {}");
  });

  await itAsync("Error string already prefixed with エラー: is not doubly prefixed", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: "エラー: 一時的なファイル共有違反です" };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.textContent, "エラーが発生しました");
    assert.strictEqual(ctx.elements.statusPill.title, "エラー: 一時的なファイル共有違反です", "must not duplicate エラー: prefix");
  });

  console.log("=== 4. Status Propagation to Header, Side, and Mobile Chip ===");

  await itAsync("Header, side status pill, and mobile chip receive class and tooltip", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: "database is locked" };

    await ctx.poll();

    assert.strictEqual(ctx.elements.sideStatusPill.className, "pill err");
    assert.strictEqual(ctx.elements.sideStatusPill.title, "エラー: database is locked");
    assert.strictEqual(ctx.elements.mobileStatusChip.className, "pill err");
    assert.strictEqual(ctx.elements.mobileStatusChip.title, "エラー: database is locked");
    assert.strictEqual(ctx.elements.mobileStatusChip.textContent, "エラーが発生しました");
  });

  await itAsync("Mobile chip shows shortened 'エラー' on tiny (<=360px) screen", async () => {
    const ctx = createTestContext();
    ctx.isTiny = true;
    ctx.apiResponse = { error: "database is locked" };

    await ctx.poll();

    assert.strictEqual(ctx.elements.mobileStatusChip.textContent, "エラー", "tiny screen should use SHORT_STATUS");
  });

  console.log("=== 5. Network Disconnect (catch) Transition ===");

  await itAsync("Fetch throw / network exception updates statusPill and title", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = async () => {
      throw new Error("Failed to fetch: Connection refused");
    };

    await ctx.poll();

    assert.strictEqual(ctx.elements.statusPill.className, "pill err");
    assert.strictEqual(ctx.elements.statusPill.textContent, "サーバー未接続");
    assert.strictEqual(ctx.elements.statusPill.title, "未接続: Failed to fetch: Connection refused");
    assert.strictEqual(ctx.elements.mobileStatusChip.title, "未接続: Failed to fetch: Connection refused");
  });

  console.log("=== 6. Recovery from Error & State Consistency ===");

  await itAsync("Recovery with s.unchanged clears title and restores state properly (waiting vs ok)", async () => {
    const ctx = createTestContext();
    ctx.state = {
      id: "active-chat-id",
      rev: 5,
      messages: [
        { role: "user", content: "Hello", status: "pending" }
      ]
    };
    ctx.lastRev = "active-chat-id:5";

    // First trigger error
    ctx.apiResponse = { error: "temporary glitch" };
    await ctx.poll();
    assert.strictEqual(ctx.elements.statusPill.className, "pill err");

    // Second poll returns unchanged (server recovered)
    ctx.apiResponse = { unchanged: true };
    await ctx.poll();

    // Because state has pending message, render() correctly sets AI応答待ち, NOT fake 入力待機!
    assert.strictEqual(ctx.elements.statusPill.className, "pill wait", "should recover to true state wait");
    assert.strictEqual(ctx.elements.statusPill.textContent, "AI応答待ち", "should preserve AI応答待ち");
    assert.strictEqual(ctx.elements.statusPill.title, "", "title must be cleared on recovery");
    assert.strictEqual(ctx.elements.mobileStatusChip.title, "", "mobile title must be cleared");
  });

  await itAsync("Recovery with full state when rev did not change (key === lastRev)", async () => {
    const ctx = createTestContext();
    ctx.state = {
      id: "active-chat-id",
      rev: 5,
      messages: [{ role: "assistant", content: "Done" }]
    };
    ctx.lastRev = "active-chat-id:5";

    // First poll: error occurs
    ctx.apiResponse = { error: "server busy" };
    await ctx.poll();
    assert.strictEqual(ctx.elements.statusPill.className, "pill err");

    // Second poll: server returns rev: 5 (unchanged rev, but full payload)
    ctx.apiResponse = {
      id: "active-chat-id",
      rev: 5,
      messages: [{ role: "assistant", content: "Done" }]
    };
    await ctx.poll();

    // key === lastRev, but statusPill had .err, so poll() re-rendered and recovered
    assert.strictEqual(ctx.elements.statusPill.className, "pill ok", "statusPill should recover to ok");
    assert.strictEqual(ctx.elements.statusPill.textContent, "入力待機");
    assert.strictEqual(ctx.elements.statusPill.title, "", "title should be cleared");
  });

  console.log("=== 7. Click/Tap Interaction & Toast on Mobile/Desktop ===");

  await itAsync("Clicking statusPill in error state triggers toast with error details", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: "504 Gateway Timeout" };
    await ctx.poll();

    assert.strictEqual(ctx.toasts.length, 0, "no toast yet");

    ctx.elements.statusPill.click();

    assert.strictEqual(ctx.toasts.length, 1, "toast should be fired");
    assert.strictEqual(ctx.toasts[0].msg, "エラー: 504 Gateway Timeout");
    assert.strictEqual(ctx.toasts[0].opts.kind, "err");
    assert.strictEqual(ctx.toasts[0].opts.ms, 4000);
  });

  await itAsync("Clicking mobileStatusChip in error state triggers toast", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: "Network timeout" };
    await ctx.poll();

    ctx.toasts.length = 0;
    ctx.elements.mobileStatusChip.click();

    assert.strictEqual(ctx.toasts.length, 1);
    assert.strictEqual(ctx.toasts[0].msg, "エラー: Network timeout");
  });

  await itAsync("Clicking statusPill in OK or WAIT state does NOT trigger toast", async () => {
    const ctx = createTestContext();
    ctx.elements.statusPill.className = "pill ok";
    ctx.updateHeaderAndSideStatus();

    ctx.toasts.length = 0;
    ctx.elements.statusPill.click();
    assert.strictEqual(ctx.toasts.length, 0, "ok state must not trigger toast");

    ctx.elements.statusPill.className = "pill wait";
    ctx.updateHeaderAndSideStatus();
    ctx.elements.statusPill.click();
    assert.strictEqual(ctx.toasts.length, 0, "wait state must not trigger toast");
  });

  await itAsync("Keyboard Enter and Space on statusPill in error state triggers toast", async () => {
    const ctx = createTestContext();
    ctx.apiResponse = { error: "504 Gateway Timeout" };
    await ctx.poll();

    ctx.toasts.length = 0;
    ctx.elements.statusPill.keydown("Enter");
    assert.strictEqual(ctx.toasts.length, 1, "Enter key must trigger toast");
    assert.strictEqual(ctx.toasts[0].msg, "エラー: 504 Gateway Timeout");

    ctx.toasts.length = 0;
    ctx.elements.statusPill.keydown(" ");
    assert.strictEqual(ctx.toasts.length, 1, "Space key must trigger toast");
  });

  console.log("=== 8. Polling Delay Backoff During Error State ===");

  it("Delay calculation backs off during error state", () => {
    const computeDelay = (isErr, isWaiting, hidden) => {
      return isErr ? (hidden ? 5000 : 1500) : (hidden ? (isWaiting ? 1000 : 5000) : (isWaiting ? 100 : 1500));
    };

    // Normal state
    assert.strictEqual(computeDelay(false, true, false), 100, "waiting in foreground = 100ms");
    assert.strictEqual(computeDelay(false, false, false), 1500, "idle in foreground = 1500ms");
    assert.strictEqual(computeDelay(false, true, true), 1000, "waiting in background = 1000ms");
    assert.strictEqual(computeDelay(false, false, true), 5000, "idle in background = 5000ms");

    // Error state: Storm prevention!
    assert.strictEqual(computeDelay(true, true, false), 1500, "waiting during error in foreground MUST backoff to 1500ms");
    assert.strictEqual(computeDelay(true, false, false), 1500, "idle during error in foreground = 1500ms");
    assert.strictEqual(computeDelay(true, true, true), 5000, "waiting during error in background MUST backoff to 5000ms");
    assert.strictEqual(computeDelay(true, false, true), 5000, "idle during error in background = 5000ms");
  });

  console.log("=== 9. refreshSessions Resilience Against Server Glitches ===");

  await itAsync("refreshSessions does NOT wipe sessions array or switch channel when /api/sessions fails", async () => {
    const ctx = createTestContext();
    ctx.sessions = [{ id: "active-chat-id", title: "私のチャンネル", rev: 3 }];
    ctx.sid = "active-chat-id";
    ctx.apiResponse = { error: "database is locked (transient)" };

    await ctx.realRefreshSessions();

    assert.strictEqual(ctx.sessions.length, 1, "sessions must NOT be wiped to empty array");
    assert.strictEqual(ctx.sessions[0].id, "active-chat-id");
    assert.strictEqual(ctx.switchToCalls.length, 0, "switchTo must NOT be called on transient error");
  });

  await itAsync("refreshSessions switches when active session is legitimately missing from valid list", async () => {
    const ctx = createTestContext();
    ctx.sessions = [{ id: "active-chat-id", title: "私のチャンネル", rev: 3 }];
    ctx.sid = "active-chat-id";
    ctx.apiResponse = { sessions: [{ id: "other-channel", title: "残った会話", rev: 1 }] };

    await ctx.realRefreshSessions();

    assert.strictEqual(ctx.sessions.length, 1);
    assert.strictEqual(ctx.sessions[0].id, "other-channel");
    assert.strictEqual(ctx.switchToCalls.length, 1, "switchTo must be called when session was legitimately deleted");
    assert.strictEqual(ctx.switchToCalls[0], "other-channel");
  });

  console.log(`\nAll ${totalCount} tests passed! (${passedCount}/${totalCount})`);
  if (passedCount !== totalCount) {
    process.exit(1);
  }
})();
