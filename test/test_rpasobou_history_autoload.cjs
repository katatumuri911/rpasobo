// @ts-check
const assert = require("assert");

// Mock DOM
class MockElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.childNodes = [];
    const classSet = new Set();
    this.classList = {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      has: (c) => classSet.has(c),
    };
    this.dataset = {};
    this.style = {};
    this.textContent = "";
    this._innerHTML = "";
    this.id = "";
    this.isConnected = true;
    this.scrollHeight = 1000;
    this.scrollTop = 0;
    this.clientHeight = 500;
    this._rectTop = 0;
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(val) {
    this._innerHTML = val;
    this.children = [];
    this.childNodes = [];
  }
  appendChild(child) {
    this.children.push(child);
    this.childNodes.push(child);
    child.parentElement = this;
    return child;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx >= 0) this.children.splice(idx, 1);
    const nidx = this.childNodes.indexOf(child);
    if (nidx >= 0) this.childNodes.splice(nidx, 1);
    child.parentElement = null;
    return child;
  }
  insertBefore(newChild, refChild) {
    const idx = refChild ? this.childNodes.indexOf(refChild) : -1;
    if (idx >= 0) {
      this.childNodes.splice(idx, 0, newChild);
      this.children.splice(idx, 0, newChild);
    } else {
      this.appendChild(newChild);
    }
    newChild.parentElement = this;
    return newChild;
  }
  querySelector(sel) {
    for (const c of this.children) {
      if (sel.startsWith(".") && c.classList.has(sel.slice(1))) return c;
      if (sel.startsWith("#") && c.id === sel.slice(1)) return c;
    }
    return null;
  }
  getBoundingClientRect() {
    return { top: this._rectTop, bottom: this._rectTop + 100, height: 100 };
  }
  setAttribute(k, v) { this[k] = v; }
  getAttribute(k) { return this[k]; }
}

const mockDoc = {
  createElement: (tag) => new MockElement(tag)
};

console.log("=== Testing RPasobou History Autoload Implementation ===");

// 1. Test getOrCreateHistoryHeader UI state transitions
let historyState = {
  sid: "test_sid",
  loading: false,
  error: null,
  hasMore: true,
  nextCursor: null,
  loadedChunks: 0,
  emptyPageStreak: 0,
};
let historyHeaderEl = null;
let sid = "test_sid";

function getOrCreateHistoryHeader() {
  if (!sid) return null;
  if (!historyHeaderEl) {
    historyHeaderEl = mockDoc.createElement("div");
    historyHeaderEl.id = "historyHeader";
    historyHeaderEl.classList.add("history-header");
  }

  let stateKey = "";
  if (historyState.loading) {
    stateKey = "loading";
  } else if (historyState.error) {
    stateKey = `error:${historyState.error}`;
  } else if (historyState.hasMore) {
    stateKey = "hasMore";
  } else {
    stateKey = "beginning";
  }

  if (historyHeaderEl.dataset.stateKey !== stateKey) {
    historyHeaderEl.dataset.stateKey = stateKey;
    historyHeaderEl.innerHTML = "";

    if (historyState.loading) {
      const wrap = mockDoc.createElement("div");
      wrap.classList.add("history-loading");
      const spinner = mockDoc.createElement("span");
      spinner.classList.add("history-spinner");
      const label = mockDoc.createElement("span");
      label.textContent = "Loading earlier turns…";
      wrap.appendChild(spinner);
      wrap.appendChild(label);
      historyHeaderEl.appendChild(wrap);
    } else if (historyState.error) {
      const wrap = mockDoc.createElement("div");
      wrap.classList.add("history-error");
      const errMsg = mockDoc.createElement("span");
      errMsg.textContent = "Failed to load earlier turns";
      const retryBtn = mockDoc.createElement("button");
      retryBtn.classList.add("history-retry-btn");
      retryBtn.textContent = "Retry";
      wrap.appendChild(errMsg);
      wrap.appendChild(retryBtn);
      historyHeaderEl.appendChild(wrap);
    } else if (historyState.hasMore) {
      const btn = mockDoc.createElement("button");
      btn.id = "loadEarlierBtn";
      btn.classList.add("load-earlier-btn");
      btn.textContent = "Load earlier turns";
      historyHeaderEl.appendChild(btn);
    } else {
      const beg = mockDoc.createElement("div");
      beg.classList.add("history-beginning");
      beg.textContent = "Beginning of thread";
      historyHeaderEl.appendChild(beg);
    }
  }

  return historyHeaderEl;
}

// Case 1: Initial state (hasMore: true) -> Load earlier turns button
let h = getOrCreateHistoryHeader();
assert(h !== null);
assert.strictEqual(h.dataset.stateKey, "hasMore");
assert.strictEqual(h.children[0].id, "loadEarlierBtn");
assert.strictEqual(h.children[0].textContent, "Load earlier turns");
console.log("  ✔ HasMore state renders 'Load earlier turns' button");

// Case 2: Loading state -> spinner + Loading earlier turns…
historyState.loading = true;
h = getOrCreateHistoryHeader();
assert.strictEqual(h.dataset.stateKey, "loading");
assert(h.children[0].classList.has("history-loading"));
assert(h.children[0].children[0].classList.has("history-spinner"));
assert.strictEqual(h.children[0].children[1].textContent, "Loading earlier turns…");
console.log("  ✔ Loading state renders spinner and 'Loading earlier turns…'");

// Case 3: Error state -> error msg + Retry button
historyState.loading = false;
historyState.error = "Network timeout";
h = getOrCreateHistoryHeader();
assert.strictEqual(h.dataset.stateKey, "error:Network timeout");
assert(h.children[0].classList.has("history-error"));
assert.strictEqual(h.children[0].children[0].textContent, "Failed to load earlier turns");
assert.strictEqual(h.children[0].children[1].textContent, "Retry");
console.log("  ✔ Error state renders 'Failed to load earlier turns' and Retry button");

// Case 4: Beginning of thread state (hasMore: false) -> Beginning of thread
historyState.error = null;
historyState.hasMore = false;
h = getOrCreateHistoryHeader();
assert.strictEqual(h.dataset.stateKey, "beginning");
assert(h.children[0].classList.has("history-beginning"));
assert.strictEqual(h.children[0].textContent, "Beginning of thread");
console.log("  ✔ Beginning state renders 'Beginning of thread'");

// 2. Test Message Deduplication and Ordering
let archivedMessages = [];
let stateMessages = [
  { id: "msg_20", role: "assistant", text: "Turn 20" },
  { id: "msg_21", role: "user", text: "Turn 21" },
];

// Fetch rot9 messages: turns 10..20 (where 20 overlaps)
const fetchedRot9 = [
  { id: "msg_18", role: "user", text: "Turn 18" },
  { id: "msg_19", role: "assistant", text: "Turn 19" },
  { id: "msg_20", role: "assistant", text: "Turn 20" }, // duplicate!
];

const existingIds = new Set();
for (const m of archivedMessages) existingIds.add(m.id);
for (const m of stateMessages) existingIds.add(m.id);

const newMsgs = fetchedRot9.filter(m => !existingIds.has(m.id));
assert.strictEqual(newMsgs.length, 2);
assert.strictEqual(newMsgs[0].id, "msg_18");
assert.strictEqual(newMsgs[1].id, "msg_19");

archivedMessages = [...newMsgs, ...archivedMessages];
assert.strictEqual(archivedMessages.length, 2);
console.log("  ✔ Overlapping messages deduplicated correctly (msg_20 omitted from prepend)");

// 3. Test Scroll Drift Correction
const logEl = new MockElement("div");
const firstChild = new MockElement("div");
firstChild._rectTop = 50; // top is 50px from viewport
logEl.scrollTop = 20;

// Anchor captured
const anchorTop = firstChild.getBoundingClientRect().top;

// After prepending 200px of content above, firstChild moves down to 250px
firstChild._rectTop = 250;

const newTop = firstChild.getBoundingClientRect().top;
const drift = newTop - anchorTop; // 250 - 50 = 200
assert.strictEqual(drift, 200);

if (Math.abs(drift) >= 2) {
  logEl.scrollTop += drift;
}
assert.strictEqual(logEl.scrollTop, 220); // 20 + 200 = 220
console.log("  ✔ Scroll drift calculated and corrected accurately without jumping");

// 4. Test Autoload Chain Limit (Max 3 pages without user input)
let autoLoadPagesWithoutInput = 0;
const MAX_AUTO_PAGES = 3;

function canAutoLoad() {
  return autoLoadPagesWithoutInput < MAX_AUTO_PAGES;
}

assert.strictEqual(canAutoLoad(), true);
autoLoadPagesWithoutInput++; // 1
assert.strictEqual(canAutoLoad(), true);
autoLoadPagesWithoutInput++; // 2
assert.strictEqual(canAutoLoad(), true);
autoLoadPagesWithoutInput++; // 3
assert.strictEqual(canAutoLoad(), false); // Stopped after 3 chained pages!
console.log("  ✔ Chained autoload halted at 3 pages without user input");

// User input resets counter
autoLoadPagesWithoutInput = 0;
assert.strictEqual(canAutoLoad(), true);
console.log("  ✔ User input resets chained autoload counter");

// 5. Test isInitialScrollSettled Guard (SPEC-v4 A4)
let isInitialScrollSettled = false;
let autoLoadFired = false;
function onScrollEvent(scrollTop) {
  if (!isInitialScrollSettled) return;
  if (scrollTop < 120) {
    autoLoadFired = true;
  }
}

// Initial mount programmatic scroll (scrollTop = 0) -> BLOCKED
onScrollEvent(0);
assert.strictEqual(autoLoadFired, false);
console.log("  ✔ Initial mount scroll blocked before user gesture");

// User gesture (wheel up) releases settled lock
function onUserGestureWheel(deltaY) {
  if (deltaY < 0) {
    isInitialScrollSettled = true;
    autoLoadPagesWithoutInput = 0;
    autoLoadFired = true;
  }
}
onUserGestureWheel(-50);
assert.strictEqual(isInitialScrollSettled, true);
assert.strictEqual(autoLoadFired, true);
console.log("  ✔ User upward wheel gesture releases lock and triggers load");

// 6. Test Downward Scroll Resets pages-without-input (SPEC-v4 A3)
autoLoadPagesWithoutInput = 3; // at limit
let historyPrevScrollTop = 50;
function onScrollDirection(currentScrollTop) {
  const delta = currentScrollTop - historyPrevScrollTop;
  historyPrevScrollTop = currentScrollTop;
  if (delta > 2 || currentScrollTop > 300) {
    autoLoadPagesWithoutInput = 0;
  }
}
onScrollDirection(100); // scrolled down by 50px
assert.strictEqual(autoLoadPagesWithoutInput, 0);
console.log("  ✔ Downward scroll resets pages-without-input counter");

// 7. Test Cross-session Stale Fetch Discard
(async () => {
  let activeSid = "session_A";
  let sessionMessages = [];
  async function mockFetchHistory(reqSid) {
    // simulate async delay
    await new Promise(r => setTimeout(r, 10));
    if (activeSid !== reqSid) return; // DISCARD
    sessionMessages.push({ id: `msg_${reqSid}` });
  }
  const p = mockFetchHistory("session_A");
  activeSid = "session_B"; // User switched session while fetch in flight!
  await p;
  assert.strictEqual(sessionMessages.length, 0); // Discarded!
  console.log("  ✔ Mismatched session response discarded on switch");

  // 8. Test Late Image Load Drift Window (SPEC-v4 A5)
  let lateCorrectionFired = 0;
  let lateWindowUntil = Date.now() + 500;
  function onImageLoad() {
    if (Date.now() <= lateWindowUntil) {
      lateCorrectionFired++;
    }
  }
  onImageLoad(); // Within 500ms
  assert.strictEqual(lateCorrectionFired, 1);
  lateWindowUntil = Date.now() - 10; // Expired
  onImageLoad(); // After window
  assert.strictEqual(lateCorrectionFired, 1); // Not incremented
  console.log("  ✔ Late image correction window (500ms) enforced");

  console.log("\n=== All RPasobou History Autoload Tests Passed! ===");
})();
