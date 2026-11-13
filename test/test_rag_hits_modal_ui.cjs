// test/test_rag_hits_modal_ui.cjs - 索引記憶ランキング表示UIの単体テスト
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const appJsSrc = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

// スタブ DOM 要素
class StubEl {
  constructor(tag = "div") {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this._textContent = "";
    this.title = "";
    this.style = {};
    this.attrs = {};
    this.listeners = {};
    this.onclick = null;
    this.onkeydown = null;
    this.onchange = null;
    this.value = "";
    this.disabled = false;
    this.readOnly = false;
    this.type = "";
    this.scrollTop = 0;
    this.tabIndex = -1;
    this._innerHTML = "";

    const self = this;
    this.classList = {
      add(...cls) {
        const cur = new Set(self.className.split(/\s+/).filter(Boolean));
        cls.forEach(c => cur.add(c));
        self.className = Array.from(cur).join(" ");
      },
      remove(...cls) {
        const cur = new Set(self.className.split(/\s+/).filter(Boolean));
        cls.forEach(c => cur.delete(c));
        self.className = Array.from(cur).join(" ");
      },
      contains(c) {
        return self.className.split(/\s+/).includes(c);
      },
      toggle(c, force) {
        if (force === undefined) force = !this.contains(c);
        if (force) this.add(c); else this.remove(c);
      }
    };
  }

  get textContent() {
    if (this._textContent) return this._textContent;
    return this.children.map(c => c.textContent).join("");
  }
  set textContent(v) {
    this._textContent = String(v);
    this.children = [];
  }

  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(v) {
    this._innerHTML = String(v);
    this.children = [];
  }

  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }

  appendChild(child) {
    if (child) {
      child.parentNode = this;
      this.children.push(child);
    }
    return child;
  }

  append(...items) {
    for (const item of items) {
      if (item) this.appendChild(item);
    }
  }

  addEventListener(evt, fn, opts) {
    if (!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push({ fn, opts });
  }

  dispatchEvent(evt) {
    const list = this.listeners[evt.type] || [];
    for (const item of list) {
      item.fn.call(this, evt);
    }
  }

  querySelector(sel) {
    // 簡易セレクタ
    if (sel.startsWith(".")) {
      const cls = sel.slice(1);
      if (this.classList.contains(cls)) return this;
      for (const c of this.children) {
        const found = c.querySelector(sel);
        if (found) return found;
      }
    }
    return null;
  }

  querySelectorAll(sel) {
    const res = [];
    if (sel.startsWith(".")) {
      const cls = sel.slice(1);
      if (this.classList.contains(cls)) res.push(this);
    }
    for (const c of this.children) {
      res.push(...c.querySelectorAll(sel));
    }
    return res;
  }
}

// テスト用コンテキスト環境構築
const elements = {};
function getOrCreate(id, tag = "div") {
  if (!elements[id]) elements[id] = new StubEl(tag);
  return elements[id];
}

const documentStub = {
  createElement(tag) {
    return new StubEl(tag);
  },
  getElementById(id) {
    return getOrCreate(id);
  },
  querySelector(sel) {
    return new StubEl("div");
  },
  addEventListener(evt, fn, opts) {},
  querySelectorAll(sel) {
    const all = [];
    for (const k of Object.keys(elements)) {
      all.push(...elements[k].querySelectorAll(sel));
      if (sel === ".modal:not(.hidden)") {
        if (elements[k].classList.contains("modal") && !elements[k].classList.contains("hidden")) {
          all.push(elements[k]);
        }
      }
    }
    return all;
  },
  body: new StubEl("body")
};

const windowStub = {
  location: { hash: "", search: "", href: "" },
  listeners: {},
  addEventListener(evt, fn, opts) {
    if (!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push({ fn, opts });
  }
};

const domIds = [
  "ragHitsModal", "ragHitsClose", "ragHitsBody",
  "textModal", "textTitle", "textHint", "textMeta", "textBody", "textCancel", "textSave",
  "settingsPanel", "mobileFab", "lightbox"
];
for (const id of domIds) {
  getOrCreate(id);
}
getOrCreate("ragHitsModal").classList.add("modal", "hidden");
getOrCreate("textModal").classList.add("modal", "hidden");
getOrCreate("settingsPanel").classList.add("hidden");
getOrCreate("lightbox").classList.add("hidden");

// app.js から対象関数を抽出して評価
const vm = require("vm");
const sandbox = {
  $: (id) => getOrCreate(id),
  document: documentStub,
  window: windowStub,
  location: windowStub.location,
  navigator: { userAgent: "test" },
  URLSearchParams,
  Event: class Event {},
  CustomEvent: class CustomEvent {},
  setTimeout: () => {},
  clearTimeout: () => {},
  setInterval: () => {},
  clearInterval: () => {},
  alert: () => {},
  confirm: () => true,
  updateFabState: () => {},
  poll: async () => {},
  checked: async () => ({ ok: true }),
  stateText: () => "",
  parseState: () => [],
  fetch: null,
  console: console,
  CAT_LABELS: {
    calling: "🏷️ 呼称",
    rule: "📜 ルール・禁忌",
    item: "🎁 アイテム",
    status: "💫 状態",
    promise: "🤙 約束",
    char_fact: "🗝️ キャラ",
    user_fact: "👤 好み",
    relationship: "💖 関係",
    event: "⚡ 事件",
  }
};
windowStub.window = windowStub;

// closeTopmostLayer, openText, buildRagHitCardNode, openRagHitsModal, closeRagHitsModal を sandbox で実行
vm.createContext(sandbox);

// app.js から関数定義を読み込む（ファイル全体ではなく必要な関数群を実行）
vm.runInContext(appJsSrc, sandbox);

console.log("--- 1. closeTopmostLayer テスト ---");
{
  const hitsModal = sandbox.$("ragHitsModal");
  const textModal = sandbox.$("textModal");

  // 両方開いている状態
  hitsModal.classList.remove("hidden");
  textModal.classList.remove("hidden");

  // 1回目の closeTopmostLayer: ragHitsModal のみが閉じられ、textModal は開いたまま
  const closed1 = sandbox.closeTopmostLayer();
  assert.strictEqual(closed1, true, "closeTopmostLayer は true を返す");
  assert.strictEqual(hitsModal.classList.contains("hidden"), true, "ragHitsModal が閉じた");
  assert.strictEqual(textModal.classList.contains("hidden"), false, "textModal は開いたまま");

  // 2回目の closeTopmostLayer: 残りの textModal が閉じる
  const closed2 = sandbox.closeTopmostLayer();
  assert.strictEqual(closed2, true);
  assert.strictEqual(textModal.classList.contains("hidden"), true, "textModal も閉じた");

  console.log("  PASS: closeTopmostLayer は ragHitsModal を最優先で個別に閉じる");
}

console.log("--- 2. openText の meta クリック可能装飾テスト ---");
{
  const metaEl = sandbox.$("textMeta");
  let clicked = false;
  const onMetaClick = () => { clicked = true; };

  // onMetaClick あり
  sandbox.openText("タイトル", "ヒント", "本文", () => {}, "🧠 （重要2・関連1）", onMetaClick);
  assert.strictEqual(metaEl.textContent, "🧠 （重要2・関連1）");
  assert.strictEqual(metaEl.classList.contains("is-clickable"), true, "is-clickable クラスが付与される");
  assert.strictEqual(metaEl.getAttribute("role"), "button", "role=button が付与される");
  assert.strictEqual(metaEl.tabIndex, 0, "tabIndex が 0 になる");
  assert.strictEqual(metaEl.title, "参照した記憶の内訳を見る");

  // クリック発火
  metaEl.onclick({ stopPropagation: () => {} });
  assert.strictEqual(clicked, true, "onclick ハンドラが実行される");

  // Enter キー発火
  let keyTriggered = false;
  sandbox.openText("タイトル", "ヒント", "本文", () => {}, "🧠 （重要2・関連1）", () => { keyTriggered = true; });
  metaEl.onkeydown({ key: "Enter", preventDefault: () => {}, stopPropagation: () => {} });
  assert.strictEqual(keyTriggered, true, "Enter キーでハンドラが実行される");

  // onMetaClick なし（通常利用時）にクラスと属性が消えるか
  sandbox.openText("チャンネル設定", "ヒント", "本文", null, null);
  assert.strictEqual(metaEl.classList.contains("is-clickable"), false, "is-clickable が消去される");
  assert.strictEqual(metaEl.getAttribute("role"), undefined, "role 属性が消去される");
  assert.strictEqual(metaEl.onclick, null, "onclick がクリアされる");

  console.log("  PASS: openText は onMetaClick の有無に応じてボタン風属性を制御する");
}

console.log("--- 3. buildRagHitCardNode テスト ---");
{
  // 正常カード (重要・呼称・スコアあり・経過時間オブジェクト)
  const item1 = {
    id: "mem1",
    category: "calling",
    status: "active",
    elapsed_time: { day: 2, period: "夜" },
    content: "お兄ちゃん",
    score: 0.9523,
    missing: false
  };
  const card1 = sandbox.buildRagHitCardNode(item1);
  assert.strictEqual(card1.classList.contains("rag-entry"), true);
  assert.strictEqual(card1.classList.contains("rag-entry-calling"), true);
  assert.strictEqual(card1.classList.contains("rag-entry-resolved"), false);

  const score1 = card1.querySelector(".rag-hits-score");
  assert.strictEqual(score1.textContent, "0.95", "スコアが小数第2位でフォーマットされる");
  assert.strictEqual(score1.title, "スコア: 0.9523", "詳細スコアがツールチップに設定される");

  const meta1 = card1.querySelector(".rag-mem-meta");
  assert.strictEqual(meta1.textContent, "2日目・夜", "日・時間帯が表示される");

  const catBadge1 = card1.querySelector(".badge-cat-calling");
  assert.strictEqual(catBadge1.textContent, "🏷️ 呼称", "CAT_LABELS が適用される");

  const content1 = card1.querySelector(".rag-mem-content");
  assert.strictEqual(content1.textContent, "お兄ちゃん");

  // スコア未記録カード (score: null)
  const item2 = {
    id: "mem2",
    category: "event",
    status: "active",
    elapsed_time: "1日目・朝",
    content: "昔の記憶",
    score: null,
    missing: false
  };
  const card2 = sandbox.buildRagHitCardNode(item2);
  const score2 = card2.querySelector(".rag-hits-score");
  assert.strictEqual(score2.textContent, "--", "スコアなしは -- になる");

  // 参照不可カード (missing: true)
  const item3 = {
    id: "mem_deleted",
    category: "event",
    status: "active",
    elapsed_time: null,
    content: "（参照不可: mem_deleted）",
    score: 0.81,
    missing: true
  };
  const card3 = sandbox.buildRagHitCardNode(item3);
  assert.strictEqual(card3.classList.contains("rag-entry-resolved"), true, "missing カードは resolved スタイル");
  const meta3 = card3.querySelector(".rag-mem-meta");
  assert.strictEqual(meta3.textContent, "参照不可");
  const score3 = card3.querySelector(".rag-hits-score");
  assert.strictEqual(score3.textContent, "0.81");

  // 空オブジェクトの日時 (elapsed_time: {}) のカード
  const item4 = {
    id: "mem4",
    category: "rule",
    status: "active",
    elapsed_time: {},
    content: "空の日時",
    score: -0.5,
    missing: false
  };
  const card4 = sandbox.buildRagHitCardNode(item4);
  const meta4 = card4.querySelector(".rag-mem-meta");
  assert.strictEqual(meta4, null, "空の日時オブジェクトでは .rag-mem-meta が追加されない");
  const score4 = card4.querySelector(".rag-hits-score");
  assert.strictEqual(score4.textContent, "-0.50", "負のスコアも正しくフォーマットされる");

  console.log("  PASS: buildRagHitCardNode は4大要素（カテゴリ、日時、スコア、本文）および参照不可を正しく生成する");
}

console.log("--- 4. openRagHitsModal データ読み込みとグループ描画テスト ---");
(async () => {
  const mockApiResponse = {
    ok: true,
    tier1_count: 1,
    tier2_count: 1,
    tier1: [
      { id: "m1", category: "calling", status: "active", content: "テスト重要", score: 0.99, missing: false }
    ],
    tier2: [
      { id: "m2", category: "event", status: "active", content: "テスト関連", score: 0.55, missing: false }
    ]
  };

  sandbox.fetch = async (url) => {
    return {
      json: async () => mockApiResponse
    };
  };

  await sandbox.openRagHitsModal("test-sid", "msg1");

  const modal = sandbox.$("ragHitsModal");
  assert.strictEqual(modal.classList.contains("hidden"), false, "モーダルが表示される");

  const body = sandbox.$("ragHitsBody");
  const groups = body.querySelectorAll(".rag-hits-group");
  assert.strictEqual(groups.length, 2, "重要と関連の2グループが生成される");

  const labels = body.querySelectorAll(".rag-hits-group-label");
  assert.strictEqual(labels[0].textContent, "重要 1件");
  assert.strictEqual(labels[1].textContent, "関連 1件");

  console.log("  PASS: openRagHitsModal は重要・関連の2セクションを件数付きで描画する");

  console.log("--- 5. モーダルのクリック・✕ボタン・Escキー・エラーハンドリング テスト ---");

  // 1) ✕ ボタンクリック
  modal.classList.remove("hidden");
  sandbox.$("ragHitsClose").onclick({ stopPropagation: () => {} });
  assert.strictEqual(modal.classList.contains("hidden"), true, "✕ボタンでモーダルが閉じる");

  // 2) 外枠（バックドロップ）クリック
  modal.classList.remove("hidden");
  modal.dispatchEvent({ type: "click", target: modal, stopPropagation: () => {} });
  assert.strictEqual(modal.classList.contains("hidden"), true, "外枠クリックでモーダルが閉じる");

  // 3) 内側クリック（閉じないこと）
  modal.classList.remove("hidden");
  modal.dispatchEvent({ type: "click", target: body, stopPropagation: () => {} });
  assert.strictEqual(modal.classList.contains("hidden"), false, "内側クリックでは閉じない");

  // 4) Esc キー押下
  let stopImmediateCalled = false;
  let preventDefaultCalled = false;
  const escEvt = {
    key: "Escape",
    stopImmediatePropagation: () => { stopImmediateCalled = true; },
    stopPropagation: () => {},
    preventDefault: () => { preventDefaultCalled = true; }
  };
  const keydownListeners = windowStub.listeners["keydown"] || [];
  for (const l of keydownListeners) {
    l.fn(escEvt);
  }
  assert.strictEqual(modal.classList.contains("hidden"), true, "Escキーでモーダルが閉じる");
  assert.strictEqual(stopImmediateCalled, true, "Escキーで他のモーダルへ伝播しない（stopImmediatePropagation）");

  // 5) APIエラー時
  sandbox.fetch = async () => {
    return {
      json: async () => ({ ok: false, error: "サーバーエラー" })
    };
  };
  await sandbox.openRagHitsModal("test-sid", "msg1");
  assert.strictEqual(body.innerHTML.includes("記憶の取得に失敗しました"), true, "APIエラー時に失敗メッセージを表示");

  // 6) 空リスト時
  sandbox.fetch = async () => {
    return {
      json: async () => ({ ok: true, tier1_count: 0, tier2_count: 0, tier1: [], tier2: [] })
    };
  };
  await sandbox.openRagHitsModal("test-sid", "msg1");
  const empties = body.querySelectorAll(".muted");
  const emptyTexts = empties.map(e => e.textContent);
  assert.strictEqual(emptyTexts.includes("なし"), true, "0件時は「なし」が表示される");

  console.log("  PASS: 閉じる操作とエラー時・空リスト表示が仕様通り動作する");

  console.log("ALL UI tests PASSED!");
})();
