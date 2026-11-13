// test/test_prompt_toolbar_dom.cjs
// DOM integration and interaction tests for PromptToolbar
const assert = require("assert");
const path = require("path");

class MockClassList {
  constructor() {
    this._set = new Set();
  }
  add(...classes) {
    classes.forEach((c) => this._set.add(c));
  }
  remove(...classes) {
    classes.forEach((c) => this._set.delete(c));
  }
  contains(c) {
    return this._set.has(c);
  }
  toggle(c, force) {
    if (force !== undefined) {
      force ? this._set.add(c) : this._set.delete(c);
      return force;
    }
    if (this._set.has(c)) {
      this._set.delete(c);
      return false;
    } else {
      this._set.add(c);
      return true;
    }
  }
}

class MockElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.classList = new MockClassList();
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this._value = "";
    this._innerHTML = "";
    this.textContent = "";
    this.type = "";
    this.checked = false;
    this.disabled = false;
  }
  get className() {
    return Array.from(this.classList._set).join(" ");
  }
  set className(val) {
    this.classList._set.clear();
    String(val || "").split(/\s+/).filter(Boolean).forEach((c) => this.classList.add(c));
  }
  get value() {
    return this._value;
  }
  set value(v) {
    this._value = String(v);
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(h) {
    this._innerHTML = String(h);
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
  }
  getAttribute(k) {
    return this.attributes[k] || null;
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(newChild, refChild) {
    newChild.parentNode = this;
    const idx = this.children.indexOf(refChild);
    if (idx >= 0) {
      this.children.splice(idx, 0, newChild);
    } else {
      this.children.push(newChild);
    }
    return newChild;
  }
  get previousElementSibling() {
    if (!this.parentNode) return null;
    const idx = this.parentNode.children.indexOf(this);
    return idx > 0 ? this.parentNode.children[idx - 1] : null;
  }
  closest(sel) {
    let cur = this;
    while (cur) {
      if (sel.startsWith(".") && cur.classList && cur.classList.contains(sel.slice(1))) return cur;
      if (sel.startsWith("#") && cur.id === sel.slice(1)) return cur;
      cur = cur.parentNode;
    }
    return null;
  }
  addEventListener(evt, fn) {
    if (!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push(fn);
  }
  dispatchEvent(evt) {
    if (!evt.preventDefault) evt.preventDefault = () => {};
    if (!evt.stopPropagation) evt.stopPropagation = () => {};
    const list = this.listeners[evt.type] || [];
    for (const fn of list) {
      fn(evt);
    }
  }
  querySelector(sel) {
    for (const c of this.children) {
      if (sel.startsWith(".") && c.classList.contains(sel.slice(1))) return c;
      if (sel.startsWith("#") && c.id === sel.slice(1)) return c;
      const sub = c.querySelector(sel);
      if (sub) return sub;
    }
    return null;
  }
  querySelectorAll(sel) {
    const res = [];
    for (const c of this.children) {
      if (sel.startsWith(".") && c.classList.contains(sel.slice(1))) res.push(c);
      if (c.querySelectorAll) res.push(...c.querySelectorAll(sel));
    }
    return res;
  }
  focus() {}
  select() {}
}

const mockDoc = {
  createElement: (tag) => new MockElement(tag),
  createTextNode: (t) => {
    const el = new MockElement("text");
    el.textContent = t;
    return el;
  },
  body: new MockElement("body"),
  addEventListener: () => {},
  querySelectorAll: (sel) => mockDoc.body.querySelectorAll(sel),
};
mockDoc.body.contains = (el) => true;

global.document = mockDoc;
global.HTMLTextAreaElement = MockElement;
global.Event = class Event {
  constructor(type) {
    this.type = type;
    this.preventDefault = () => {};
    this.stopPropagation = () => {};
  }
};
global.localStorage = {
  _data: {},
  getItem(k) {
    return this._data[k] !== undefined ? this._data[k] : null;
  },
  setItem(k, v) {
    this._data[k] = String(v);
  },
};

const savedFetches = [];
global.fetch = async (url, opts) => {
  savedFetches.push({ url, opts, body: JSON.parse(opts.body) });
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};

// Re-require PromptToolbar with mocked DOM
delete require.cache[require.resolve("../public/prompt_toolbar.js")];
const PromptToolbar = require("../public/prompt_toolbar.js");

(async function run() {
  console.log("Running PromptToolbar DOM integration tests...");

  // Setup container and textarea
  const container = document.createElement("div");
  const textarea = document.createElement("textarea");
  textarea.value = "1girl, from behind, bedroom";
  container.appendChild(textarea);
  document.body.appendChild(container);

  // Attach toolbar
  let savedMode = null;
  let savedKeep = null;
  const tb = PromptToolbar.attach(textarea, {
    sessionId: "sess_123",
    channelSetting: { image: { pov_mode: "auto", pov_keep: true } },
    onSaveSetting: async (m, k) => {
      savedMode = m;
      savedKeep = k;
    },
  });

  assert(tb !== null, "Toolbar should be attached");
  assert(textarea.dataset.rpToolbarAttached === "1");

  // 1. Check toolbar elements
  assert(tb.poseDropdown, "Pose dropdown exists");
  assert(tb.angleDropdown, "Angle dropdown exists");
  assert(tb.povBtn, "POV toggle button exists");
  assert(tb.povDefaultSelect, "POV default select exists");
  assert(tb.keepCheck, "POV keep checkbox exists");
  assert(tb.smartReplaceCheckbox, "Smart replace checkbox exists");
  assert(tb.undoBtn, "Undo button exists");

  // 2. Initial POV button state (textarea has no pov)
  assert.strictEqual(tb.povBtn.classList.contains("is-active"), false);
  assert.strictEqual(tb.povBtn.getAttribute("aria-pressed"), "false");
  assert.strictEqual(tb.undoBtn.disabled, true, "Undo button should initially be disabled");

  // 3. Test POV button click -> toggles POV ON and records Undo
  tb.povBtn.dispatchEvent(new Event("click"));
  assert.strictEqual(textarea.value, "1girl, pov, from behind, bedroom");
  assert.strictEqual(tb.povBtn.classList.contains("is-active"), true);
  assert.strictEqual(tb.povBtn.getAttribute("aria-pressed"), "true");
  assert(tb.povBtn.innerHTML.includes("is-on"), "POV dot should be active (is-on)");
  assert.strictEqual(tb.undoBtn.disabled, false, "Undo button should now be enabled");

  // Allow async savePovPreferences microtask to settle
  await new Promise((r) => setTimeout(r, 20));

  // Check pov_mode was synced because keepCheck was checked
  assert.strictEqual(savedMode, "on");

  // 4. Test Toolbar Undo click -> restores previous value
  tb.undoBtn.dispatchEvent(new Event("click"));
  assert.strictEqual(textarea.value, "1girl, from behind, bedroom");
  assert.strictEqual(tb.povBtn.classList.contains("is-active"), false);
  assert.strictEqual(tb.undoBtn.disabled, true, "Undo button should now be disabled");

  // 5. Test Pose selection via dropdown item click
  // Find a menu item in poseDropdown
  const poseItems = tb.poseDropdown.querySelectorAll(".rp-pt-menu-item");
  assert(poseItems.length >= 28, `Expected >= 28 pose items, found ${poseItems.length}`);
  // Click the first pose item (まんぐり返し)
  poseItems[0].dispatchEvent(new Event("click"));
  assert(textarea.value.includes("folded, lying, on back"), "Pose tags should be inserted");
  assert(textarea.value.includes("bedroom"), "Background should be kept");
  assert.strictEqual(tb.undoBtn.disabled, false, "Undo should be enabled after pose insertion");

  // 6. Test Angle selection via dropdown item click
  const angleItems = tb.angleDropdown.querySelectorAll(".rp-pt-menu-item");
  assert.strictEqual(angleItems.length, 11, `Expected 11 angle items, found ${angleItems.length}`);

  // Find "お尻にフォーカス"
  let assFocusItem = null;
  let wideShotItem = null;
  for (const item of angleItems) {
    const lbl = item.querySelector(".rp-pt-item-label");
    if (lbl && lbl.textContent.includes("お尻にフォーカス")) assFocusItem = item;
    if (lbl && lbl.textContent.includes("超パース全身")) wideShotItem = item;
  }
  assert(assFocusItem, "Found ass focus angle item");
  assert(wideShotItem, "Found wide shot angle item");

  // Click ass focus item
  assFocusItem.dispatchEvent(new Event("click"));
  assert(textarea.value.includes("ass focus"), "Ass focus angle should be inserted");

  // Now click wide shot angle item (angle_shot cascade clears ass focus)
  wideShotItem.dispatchEvent(new Event("click"));
  assert(!textarea.value.includes("ass focus"), "Angle shot cascade should remove ass focus");
  assert(textarea.value.includes("full body"), "Full body should be present");

  // 7. Test Undo multiple steps
  tb.undoBtn.dispatchEvent(new Event("click")); // reverts wide shot
  assert(textarea.value.includes("ass focus"), "Should revert back to ass focus state");

  tb.undoBtn.dispatchEvent(new Event("click")); // reverts ass focus
  assert(!textarea.value.includes("ass focus"), "Ass focus removed");

  tb.undoBtn.dispatchEvent(new Event("click")); // reverts pose
  assert(!textarea.value.includes("folded"), "Pose reverted");

  assert.strictEqual(textarea.value, "1girl, from behind, bedroom");
  assert.strictEqual(tb.undoBtn.disabled, true);

  // 8. Test PromptToolbar.syncTextareaUI
  textarea.value = "1girl, pov, smile";
  PromptToolbar.syncTextareaUI(textarea);
  assert.strictEqual(tb.povBtn.classList.contains("is-active"), true);

  console.log("All DOM integration and Undo tests passed successfully!");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
