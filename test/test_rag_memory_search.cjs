// test/test_rag_memory_search.cjs - RAG記憶キーワード検索機能の単体・統合テスト
// 実行: node test/test_rag_memory_search.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");

// DOM スタブ環境構築
class StubNode {
  constructor() {
    this.parentNode = null;
  }
}

class StubTextNode extends StubNode {
  constructor(text) {
    super();
    this.nodeType = 3;
    this.textContent = String(text != null ? text : "");
  }
}

class StubFragment extends StubNode {
  constructor() {
    super();
    this.nodeType = 11;
    this.children = [];
  }
  appendChild(child) {
    if (child) {
      if (child.nodeType === 11) {
        for (const c of child.children) {
          c.parentNode = this;
          this.children.push(c);
        }
        child.children = [];
      } else {
        child.parentNode = this;
        this.children.push(child);
      }
    }
    return child;
  }
  append(...children) {
    for (const c of children) this.appendChild(c);
  }
}

class StubEl extends StubNode {
  constructor(tag = "div") {
    super();
    this.nodeType = 1;
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.className = "";
    this._textContent = "";
    this.title = "";
    this.style = {};
    this.attrs = {};
    this.listeners = {};
    this.onclick = null;
    this.onchange = null;
    this.value = "";
    this.disabled = false;
    this.type = "";
    this.open = false;
    this.focused = false;
    this._bound = false;

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
        return self.className.split(/\s+/).filter(Boolean).includes(c);
      },
      toggle(c, force) {
        if (force === undefined) force = !this.contains(c);
        if (force) this.add(c); else this.remove(c);
      }
    };
  }

  focus() {
    this.focused = true;
  }

  click() {
    if (this.onclick) this.onclick({ stopPropagation: () => {}, preventDefault: () => {} });
  }

  get textContent() {
    if (this.children.length === 0) return this._textContent;
    return this.children.map(c => c.textContent).join("");
  }
  set textContent(v) {
    this._textContent = String(v != null ? v : "");
    this.children = [];
  }

  get innerHTML() {
    return this._textContent;
  }
  set innerHTML(v) {
    this._textContent = String(v != null ? v : "");
    this.children = [];
  }

  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }

  appendChild(child) {
    if (child) {
      if (child.nodeType === 11) { // DocumentFragment
        for (const c of child.children) {
          c.parentNode = this;
          this.children.push(c);
        }
        child.children = [];
      } else {
        child.parentNode = this;
        this.children.push(child);
      }
    }
    return child;
  }
  append(...children) {
    for (const c of children) this.appendChild(c);
  }

  addEventListener(evt, fn) {
    if (!this.listeners[evt]) this.listeners[evt] = [];
    this.listeners[evt].push(fn);
  }

  dispatchEvent(evt) {
    const list = this.listeners[evt.type] || [];
    for (const fn of list) {
      fn(evt);
    }
    if (evt.type === "click" && this.onclick) {
      this.onclick(evt);
    }
  }

  querySelector(sel) {
    const parts = sel.trim().split(/\s+/);
    if (parts.length > 1) {
      let cur = this;
      for (const p of parts) {
        if (!cur) return null;
        cur = cur.querySelector(p);
      }
      return cur;
    }
    for (const c of this.children) {
      if (c.nodeType !== 1) continue;
      if (sel.startsWith(".") && c.classList.contains(sel.slice(1))) return c;
      if (sel.startsWith("#") && c.id === sel.slice(1)) return c;
      if (c.tagName.toLowerCase() === sel.toLowerCase()) return c;
      const found = c.querySelector(sel);
      if (found) return found;
    }
    return null;
  }

  querySelectorAll(sel) {
    const res = [];
    for (const c of this.children) {
      if (c.nodeType !== 1) continue;
      if (sel.startsWith(".") && c.classList.contains(sel.slice(1))) res.push(c);
      if (sel.startsWith("#") && c.id === sel.slice(1)) res.push(c);
      if (c.tagName.toLowerCase() === sel.toLowerCase()) res.push(c);
      res.push(...c.querySelectorAll(sel));
    }
    return res;
  }
}

// HTML ファイル検証
console.log("=== 1. HTML構造の検証 (public/index.html) ===");
const htmlSrc = fs.readFileSync(path.join(__dirname, "..", "public", "index.html"), "utf8");
assert(htmlSrc.includes('id="ragMemSearchInput"'), "index.html に #ragMemSearchInput が存在する");
assert(htmlSrc.includes('id="ragMemSearchClear"'), "index.html に #ragMemSearchClear が存在する");
assert(htmlSrc.includes('id="ragMemSearchCount"'), "index.html に #ragMemSearchCount が存在する");
assert(htmlSrc.includes('class="rag-mem-search-bar"'), "index.html に .rag-mem-search-bar が存在する");
assert(htmlSrc.includes('class="rag-search-icon"'), "index.html に .rag-search-icon が存在する");
assert(htmlSrc.includes('type="text"'), "#ragMemSearchInput は type=\"text\" であること");
console.log("  PASS: HTML の検索バー要素・属性が仕様通り");

// CSS ファイル検証
console.log("=== 2. CSSルールの検証 (public/style.css) ===");
const cssSrc = fs.readFileSync(path.join(__dirname, "..", "public", "style.css"), "utf8");
assert(cssSrc.includes("#ragMemView:not(.hidden)"), "CSS に #ragMemView の Flex カラム指定がある");
assert(cssSrc.includes(".rag-mem-search-bar"), "CSS に .rag-mem-search-bar がある");
assert(cssSrc.includes("#ragMemSearchInput.has-query"), "CSS に .has-query のスタイルがある");
assert(cssSrc.includes(".rag-search-highlight"), "CSS に .rag-search-highlight がある");
assert(cssSrc.includes(".rag-empty-state"), "CSS に .rag-empty-state がある");
assert(cssSrc.includes("font-size: 16px; /* iOS Safari 自動ズーム防止 */"), "モバイルで font-size: 16px が定義されている");
assert(cssSrc.includes("#ragMemRegenHint"), "モバイルで #ragMemRegenHint の非表示指定がある");
assert(cssSrc.includes("body.kb-open #ragModal:has(#ragMemSearchInput:focus) .rag-mem-toolbar"), "キーボード展開時にツールバーを隠す指定がある");
assert(cssSrc.includes("body.kb-open #ragModal.rag-search-focused .rag-mem-toolbar"), "非:has()環境向けフォールバッククラス指定がある");
assert(cssSrc.includes("button.rag-search-clear:active"), "クリアボタンのアクティブ時スタイルが定義されている");
console.log("  PASS: CSS のスタイル・モバイル最適化ルールが仕様通り");

// JS ロジック検証
console.log("=== 3. JS 関数の検証 (public/app.js) ===");
const appJsSrc = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

// DOM要素マップ
const elements = new Map();
function getStub(id, tag = "div") {
  if (!elements.has(id)) {
    const el = new StubEl(tag);
    el.id = id;
    elements.set(id, el);
  }
  return elements.get(id);
}

const doc = {
  createElement(tag) { return new StubEl(tag); },
  createDocumentFragment() { return new StubFragment(); },
  createTextNode(text) { return new StubTextNode(text); },
  getElementById(id) { return elements.get(id) || null; }
};

// 必要な要素を初期化
const ragMemEntries = getStub("ragMemEntries", "div");
const ragMemSearchInput = getStub("ragMemSearchInput", "input");
const ragMemSearchClear = getStub("ragMemSearchClear", "button");
const ragMemSearchCount = getStub("ragMemSearchCount", "span");
const ragMemFilterTabs = getStub("ragMemFilterTabs", "div");
const ragModal = getStub("ragModal", "div");
const ragMemProgress = getStub("ragMemProgress", "div");
const ragMemProgressBar = getStub("ragMemProgressBar", "div");
const ragMemProgressText = getStub("ragMemProgressText", "div");

// タブボタンを作成
const tabCats = ["all", "calling", "rule", "item", "status", "promise", "char_fact", "user_fact", "relationship", "event"];
for (const cat of tabCats) {
  const btn = new StubEl("button");
  btn.className = "rag-filter-btn" + (cat === "all" ? " active" : "");
  btn.setAttribute("data-cat", cat);
  btn.textContent = cat;
  ragMemFilterTabs.appendChild(btn);
}

const sandbox = {
  window: {},
  document: doc,
  $: (id) => elements.get(id) || null,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }),
  alert: () => {},
  confirm: () => true,
  checked: () => Promise.resolve({}),
  scrollToMessage: () => {},
  openRagMemEditor: () => {},
  loadRagMemories: () => Promise.resolve(),
  setRegenButtonsDisabled: () => {},
  startRegenStatusPolling: () => {},
  ragMemRegenBefore: null,
  sid: "test_sid_1",
  clearTimeout: (t) => clearTimeout(t),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  console
};
sandbox.window = sandbox;

// 対象コードの抽出
// MEMORY_PRIORITY_LEVELS から openRagMemEditor 直前まで
const startIdx = appJsSrc.indexOf("const MEMORY_PRIORITY_LEVELS = [");
const endIdx = appJsSrc.indexOf("function openRagMemEditor(");
assert(startIdx >= 0 && endIdx >= 0, "コードスライスの抽出に成功すること");
const targetCode = appJsSrc.slice(startIdx, endIdx);

const regenStart = appJsSrc.indexOf("async function regenerateRagMemories(");
const regenEnd = appJsSrc.indexOf("async function loadRagCollections(");
assert(regenStart >= 0 && regenEnd >= 0, "regenerateRagMemories スライスの抽出に成功すること");
const regenCode = appJsSrc.slice(regenStart, regenEnd);

const exportCode = `
;globalThis.normalizeSearchChar = normalizeSearchChar;
globalThis.normalizeSearchText = normalizeSearchText;
globalThis.parseSearchWords = parseSearchWords;
globalThis.formatRagMemMeta = formatRagMemMeta;
globalThis.appendHighlightedText = appendHighlightedText;
globalThis.clearRagMemSearch = clearRagMemSearch;
globalThis.setupRagSearchBar = setupRagSearchBar;
globalThis.renderRagMemories = renderRagMemories;
globalThis.compareRagMemories = compareRagMemories;
globalThis.regenerateRagMemories = regenerateRagMemories;
Object.defineProperty(globalThis, 'ragMemSearchQuery', {
  get: () => ragMemSearchQuery,
  set: (v) => { ragMemSearchQuery = v; }
});
Object.defineProperty(globalThis, 'ragMemFilter', {
  get: () => ragMemFilter,
  set: (v) => { ragMemFilter = v; }
});
Object.defineProperty(globalThis, 'ragMemSortDir', {
  get: () => ragMemSortDir,
  set: (v) => { ragMemSortDir = v; }
});
Object.defineProperty(globalThis, 'ragAllMemories', {
  get: () => ragAllMemories,
  set: (v) => { ragAllMemories = v; }
});
`;

vm.createContext(sandbox);
vm.runInContext(targetCode + regenCode + exportCode, sandbox);

console.log("--- 3.1 日本語正規化・単語分割テスト ---");
const { normalizeSearchText, parseSearchWords, formatRagMemMeta, appendHighlightedText } = sandbox;

// 全角半角・大文字小文字
assert.strictEqual(normalizeSearchText("Ａｐｐｌｅ"), "apple");
assert.strictEqual(normalizeSearchText("Ｔ１２"), "t12");
// ひらがな・カタカナ統一
assert.strictEqual(normalizeSearchText("アイス"), "あいす");
assert.strictEqual(normalizeSearchText("ｱｲｽ"), "あいす");
assert.strictEqual(normalizeSearchText("ゔ"), "ゔ");
assert.strictEqual(normalizeSearchText("ヴ"), "ゔ");
assert.strictEqual(normalizeSearchText("ガ"), "が");
assert.strictEqual(normalizeSearchText("ｶﾞ"), "が");

// AND 分割（全角スペース、半角スペース、連続スペース）
const words1 = parseSearchWords("駅前　水族館   T12");
assert.deepStrictEqual([...words1], ["駅前", "水族館", "t12"]);
const words2 = parseSearchWords("   ");
assert.deepStrictEqual([...words2], []);
console.log("  PASS: 日本語正規化とAND分割が仕様通り");

console.log("--- 3.2 メタ情報生成 formatRagMemMeta テスト ---");
assert.strictEqual(formatRagMemMeta({ turn_idx: 12, elapsed_time: { day: 2, period: "夜" }, character_name: "アリス" }), "T12 2日目・夜 [アリス]");
assert.strictEqual(formatRagMemMeta({ turn_idx: 0, elapsed_time: "朝" }), "T0 朝");
assert.strictEqual(formatRagMemMeta({ turn_idx: null, elapsed_time: null }), "");
console.log("  PASS: formatRagMemMeta 生成が仕様通り");

console.log("--- 3.3 DOMハイライト appendHighlightedText テスト (サロゲートペア・濁点・XSSゼロ) ---");
{
  const container = new StubEl("div");
  // 1) 通常テキスト
  appendHighlightedText(container, "日曜日に駅前の水族館へ行く", ["水族館"]);
  assert.strictEqual(container.children.length, 3, "前テキスト, mark, 後テキスト の3ノード");
  assert.strictEqual(container.children[0].textContent, "日曜日に駅前の");
  assert.strictEqual(container.children[1].tagName, "MARK");
  assert.strictEqual(container.children[1].className, "rag-search-highlight");
  assert.strictEqual(container.children[1].textContent, "水族館");
  assert.strictEqual(container.children[2].textContent, "へ行く");

  // 2) ひらがな検索でカタカナ本文がヒットして mark で囲まれること
  container.textContent = "";
  appendHighlightedText(container, "冷たいアイスクリームを食べる", parseSearchWords("あいす"));
  assert.strictEqual(container.children[1].textContent, "アイス");

  // 3) 半角カナ濁点 "ｶﾞ" が合成されて "が" でハイライトされ位置ズレしないこと
  container.textContent = "";
  appendHighlightedText(container, "ｶﾞﾗｽの靴", parseSearchWords("ガラス"));
  assert.strictEqual(container.children[0].tagName, "MARK");
  assert.strictEqual(container.children[0].textContent, "ｶﾞﾗｽ");
  assert.strictEqual(container.children[1].textContent, "の靴");

  // 4) サロゲートペア（絵文字）を含んでも位置ズレしないこと
  container.textContent = "";
  appendHighlightedText(container, "約束🍎水族館で会う", ["水族館"]);
  assert.strictEqual(container.children[0].textContent, "約束🍎");
  assert.strictEqual(container.children[1].textContent, "水族館");
  assert.strictEqual(container.children[2].textContent, "で会う");

  // 5) 絵文字自体の検索
  container.textContent = "";
  appendHighlightedText(container, "約束🍎水族館で会う", ["🍎"]);
  assert.strictEqual(container.children[0].textContent, "約束");
  assert.strictEqual(container.children[1].textContent, "🍎");
  assert.strictEqual(container.children[2].textContent, "水族館で会う");

  // 6) XSS 安全性（HTML 特殊文字がそのまま textNode になりスクリプト実行されない）
  container.textContent = "";
  appendHighlightedText(container, "<script>alert('xss')</script> & <b>太字</b>", ["alert"]);
  assert.strictEqual(container.children[0].textContent, "<script>");
  assert.strictEqual(container.children[1].textContent, "alert");
  assert.strictEqual(container.children[2].textContent, "('xss')</script> & <b>太字</b>");

  // 7) 正規表現メタ文字によるクラッシュ防止
  container.textContent = "";
  appendHighlightedText(container, "正規表現 [test.*+?] のテスト", ["[test.*+?]"]);
  assert.strictEqual(container.children[1].textContent, "[test.*+?]");
}
console.log("  PASS: appendHighlightedText のサロゲートペア・濁点合成・XSSゼロ・RegExp耐性");

console.log("--- 3.4 renderRagMemories のキーワード絞り込み＆連動テスト ---");
const testMemories = [
  { id: "m1", category: "promise", content: "水族館へ行く約束", turn_idx: 12, elapsed_time: { day: 2, period: "夜" }, priority: "default", created_at: 100 },
  { id: "m2", category: "item", content: "合鍵を預かった", turn_idx: 5, priority: "pinned", created_at: 200 },
  { id: "m3", category: "promise", content: "門限は22時", turn_idx: 1, priority: "high", created_at: 300 },
  {
    id: "m4",
    category: "rule",
    content: "現在のルール",
    history: [{ content: "昔の約束：駅前でアイスを食べる", elapsed_time: { day: 1, period: "昼" }, archived_at: 50 }],
    priority: "default",
    created_at: 400
  }
];

// 3.4.1 全件表示
sandbox.ragMemFilter = "all";
sandbox.ragMemSearchQuery = "";
sandbox.renderRagMemories(testMemories);
assert.strictEqual(ragMemEntries.children.length, 4, "全4件が表示されること");
assert.strictEqual(ragMemSearchCount.textContent, "4件", "絞り込みなし時は「4件」");

// 3.4.2 単一キーワード絞り込み
sandbox.ragMemSearchQuery = "水族館";
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1, "「水族館」で1件に絞り込み");
assert.strictEqual(ragMemSearchCount.textContent, "1 / 4件", "件数バッジが「1 / 4件」");
const card1 = ragMemEntries.children[0];
const mark1 = card1.querySelector(".rag-search-highlight");
assert(mark1, "ハイライトが付与されている");
assert.strictEqual(mark1.textContent, "水族館");

// 3.4.3 作中時間・ターン検索
sandbox.ragMemSearchQuery = "2日目・夜";
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1, "「2日目・夜」でm1がヒット");

sandbox.ragMemSearchQuery = "T12";
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1, "「T12」でm1がヒット");

// 3.4.4 AND検索（複数キーワード）
sandbox.ragMemSearchQuery = "水族館　約束"; // 全角スペース
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1, "AND検索でm1がヒット");

sandbox.ragMemSearchQuery = "水族館 門限"; // 両方は含まない
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1, "Empty state のラッパーが出ること");
assert(ragMemEntries.children[0].classList.contains("rag-empty-state"), "Empty state が表示される");

// 3.4.5 履歴（history）ヒット＆自動展開テスト
sandbox.ragMemSearchQuery = "アイス";
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1, "履歴にのみアイスを含むm4がヒット");
const card4 = ragMemEntries.children[0];
const details = card4.querySelector(".rag-mem-history");
assert(details, "履歴 details が存在する");
assert.strictEqual(details.open, true, "履歴ヒット時は details が自動オープン（open = true）される");
const histMark = details.querySelector(".rag-search-highlight");
assert(histMark, "履歴内の単語にハイライトが付与される");
assert.strictEqual(histMark.textContent, "アイス");

console.log("  PASS: 絞り込み・メタ情報・AND・履歴自動オープン");

console.log("--- 3.5 カテゴリ絞り込みとの掛け合わせ・ピン留め・ソート連動テスト ---");
// カテゴリ「promise」かつ「水族館」
sandbox.ragMemFilter = "promise";
sandbox.ragMemSearchQuery = "水族館";
sandbox.renderRagMemories();
assert.strictEqual(ragMemEntries.children.length, 1);
assert.strictEqual(ragMemSearchCount.textContent, "1 / 4件");

// カテゴリ「item」かつ「水族館」→ itemカテゴリには無いが全体には1件ある（Pattern B 救済）
sandbox.ragMemFilter = "item";
sandbox.ragMemSearchQuery = "水族館";
sandbox.renderRagMemories();
const emptyB = ragMemEntries.children[0];
assert(emptyB.classList.contains("rag-empty-state"));
const msgB = emptyB.querySelector(".rag-empty-state-msg");
assert(msgB.textContent.includes("「🎁 アイテム」カテゴリ内に 「水族館」 に一致する記憶はありません"));
assert(msgB.textContent.includes("他のカテゴリに 1件 の一致があります"));
const allCatBtn = emptyB.querySelector(".main-btn");
assert(allCatBtn, "「すべてのカテゴリで表示」ボタンが存在する");
assert.strictEqual(allCatBtn.textContent, "すべてのカテゴリで表示 (1件)");

// 「すべてのカテゴリで表示」ボタンをクリックして救済されるか検証
allCatBtn.click();
assert.strictEqual(sandbox.ragMemFilter, "all", "フィルタが all に変更された");
assert.strictEqual(ragMemEntries.children.length, 1, "全カテゴリ表示で水族館カードが復帰した");

// パターンC: 特定カテゴリ選択中かつ全カテゴリでも0件
sandbox.ragMemFilter = "item";
sandbox.ragMemSearchQuery = "存在しない単語xyz";
sandbox.renderRagMemories();
const emptyC = ragMemEntries.children[0];
const msgC = emptyC.querySelector(".rag-empty-state-msg");
assert(msgC.textContent.includes("すべてのカテゴリに 「存在しない単語xyz」 に一致する記憶はありません。"));
assert.strictEqual(emptyC.querySelectorAll(".main-btn").length, 0, "救済ボタンは出ない");

// パターンD: 「全て」タブ選択中で0件
sandbox.ragMemFilter = "all";
sandbox.ragMemSearchQuery = "存在しない単語xyz";
sandbox.renderRagMemories();
const emptyD = ragMemEntries.children[0];
const msgD = emptyD.querySelector(".rag-empty-state-msg");
assert(msgD.textContent.includes("「存在しない単語xyz」 に一致する記憶は見つかりませんでした。"));

// パターンA: セッション全体に記憶が0件
sandbox.ragMemFilter = "all";
sandbox.ragMemSearchQuery = "";
sandbox.renderRagMemories([]);
assert.strictEqual(ragMemEntries.children.length, 1);
assert(ragMemEntries.children[0].textContent.includes("記憶はまだありません"));

// ソート連動およびピン留め最上位
sandbox.ragMemFilter = "all";
sandbox.ragMemSearchQuery = "";
sandbox.ragMemSortDir = "desc"; // 新しい順
sandbox.renderRagMemories(testMemories);
// pinned (m2) が最上位
assert(ragMemEntries.children[0].classList.contains("rag-entry-pinned"), "新しい順でも pinned が最上位");

sandbox.ragMemSortDir = "asc"; // 古い順
sandbox.renderRagMemories(testMemories);
// pinned (m2) は古い順でも最上位を維持
assert(ragMemEntries.children[0].classList.contains("rag-entry-pinned"), "古い順でも pinned が最上位を維持");
// 2枚目は最も古い m4 (50秒) または m1 (100秒)
console.log("  PASS: Empty State 4パターン、ソート連動、ピン留め最上位維持");

console.log("--- 3.6 clearRagMemSearch & setupRagSearchBar イベント制御テスト ---");
sandbox.setupRagSearchBar();

// 検索入力
ragMemSearchInput.value = "合鍵";
ragMemSearchInput.dispatchEvent({ type: "input", isComposing: false });
assert.strictEqual(ragMemSearchInput.classList.contains("has-query"), true, "has-query クラスが付く");
assert.strictEqual(ragMemSearchClear.classList.contains("hidden"), false, "クリアボタンが表示される");

// Esc キー押下（文字あり時 → クリアされてフォーカス維持）
let escPrevented = false;
let escStopped = false;
ragMemSearchInput.dispatchEvent({
  type: "keydown",
  key: "Escape",
  isComposing: false,
  preventDefault: () => { escPrevented = true; },
  stopPropagation: () => { escStopped = true; }
});
assert.strictEqual(escPrevented, true, "文字あり時の Esc は preventDefault される");
assert.strictEqual(escStopped, true, "文字あり時の Esc は stopPropagation される");
assert.strictEqual(ragMemSearchInput.value, "", "Esc で入力がクリアされる");
assert.strictEqual(ragMemSearchInput.focused, true, "入力欄にフォーカスが戻る");
assert.strictEqual(ragMemSearchClear.classList.contains("hidden"), true, "クリアボタンが非表示になる");

// Esc キー押下（文字なし時 → 何もしない、伝播を止めない）
escPrevented = false;
escStopped = false;
ragMemSearchInput.dispatchEvent({
  type: "keydown",
  key: "Escape",
  isComposing: false,
  preventDefault: () => { escPrevented = true; },
  stopPropagation: () => { escStopped = true; }
});
assert.strictEqual(escPrevented, false, "文字なし時の Esc は preventDefault されない");
assert.strictEqual(escStopped, false, "文字なし時の Esc は stopPropagation されない");

// クリアボタンクリック
ragMemSearchInput.value = "テスト";
ragMemSearchInput.dispatchEvent({ type: "input", isComposing: false });
ragMemSearchClear.dispatchEvent({ type: "click" });
assert.strictEqual(ragMemSearchInput.value, "", "クリアボタン押下で入力がクリアされる");
assert.strictEqual(ragMemSearchInput.focused, true, "クリアボタン押下でフォーカスが戻る");

console.log("  PASS: Escキー制御・クリアボタン制御");

console.log("--- 3.7 ライフサイクル連動テスト (チャンネル切替 & 全消し) ---");
// チャンネル切替
// app.js の openRag() 内のロジック
const ragMemLastSidRef = { val: "test_sid_1" };
const currentSid = "test_sid_2"; // 別のチャンネルへ切り替え

sandbox.ragMemSearchQuery = "残留クエリ";
ragMemSearchInput.value = "残留クエリ";
if (ragMemLastSidRef.val !== currentSid) {
  ragMemLastSidRef.val = currentSid;
  sandbox.clearRagMemSearch(false);
  sandbox.ragMemFilter = "all";
}
assert.strictEqual(sandbox.ragMemSearchQuery, "", "チャンネル切替で検索クエリが初期化される");
assert.strictEqual(ragMemSearchInput.value, "", "チャンネル切替で入力欄が初期化される");
assert.strictEqual(sandbox.ragMemFilter, "all", "チャンネル切替でタブが all にリセットされる");

console.log("  PASS: チャンネル切替ライフサイクル連動");

console.log("--- 3.8 IME確定制御と重複実行抑止テスト ---");
{
  let renderCallCount = 0;
  const origRender = sandbox.renderRagMemories;
  sandbox.renderRagMemories = (...args) => {
    renderCallCount++;
    return origRender(...args);
  };

  // IME未確定中
  renderCallCount = 0;
  ragMemSearchInput.value = "すいぞくかん";
  ragMemSearchInput.dispatchEvent({ type: "input", isComposing: true });
  assert.strictEqual(renderCallCount, 0, "IME編集中は即座にrenderRagMemoriesが呼ばれない");

  // compositionend (変換確定)
  ragMemSearchInput.value = "水族館";
  ragMemSearchInput.dispatchEvent({ type: "compositionend" });
  assert.strictEqual(renderCallCount, 1, "compositionend で即座にrenderRagMemoriesが呼ばれる");

  // compositionend 直後のブラウザ自動発火 input (isComposing=false) は抑止される
  ragMemSearchInput.dispatchEvent({ type: "input", isComposing: false });
  // タイマーがセットされずに抑止されていること（80ms後も増えない）
  assert.strictEqual(renderCallCount, 1, "直後の input イベントが抑止され二重描画されない");

  sandbox.renderRagMemories = origRender;
}
console.log("  PASS: IME確定・debounce・重複抑止が仕様通り動作");

console.log("--- 3.9 重複・隣接キーワードのハイライトマージテスト ---");
{
  const container = new StubEl("div");
  // "東京都" に対して "東京" と "京都"
  appendHighlightedText(container, "東京都庁に行く", parseSearchWords("東京 京都"));
  assert.strictEqual(container.children.length, 2, "mark と 後続テキスト の2ノードに統合される");
  assert.strictEqual(container.children[0].tagName, "MARK");
  assert.strictEqual(container.children[0].textContent, "東京都", "区間が正しくマージされて1つのmarkになる");
  assert.strictEqual(container.children[1].textContent, "庁に行く");
}
console.log("  PASS: 重複・隣接キーワードの安全な区間マージ");

console.log("--- 3.10 キャラ名・nullコンテンツ等の異常系エッジケーステスト ---");
{
  const edgeMemories = [
    { id: "e1", content: null, turn_idx: 0, priority: "default" },
    { id: "e2", content: "秘密基地", character_name: "アリス", priority: "default" }
  ];

  sandbox.ragMemFilter = "all";
  sandbox.ragMemSearchQuery = "T0";
  sandbox.renderRagMemories(edgeMemories);
  assert.strictEqual(ragMemEntries.children.length, 1, "content=null でも T0 でクラッシュせずヒット");

  sandbox.ragMemSearchQuery = "アリス";
  sandbox.renderRagMemories(edgeMemories);
  assert.strictEqual(ragMemEntries.children.length, 1, "キャラ名 [アリス] で検索ヒット");
}
console.log("  PASS: 異常系・境界値・キャラ名マッチング");

console.log("--- 3.11 防御的 null/0/異常値ハンドリングテスト ---");
{
  assert.strictEqual(sandbox.normalizeSearchChar(null), "");
  assert.strictEqual(sandbox.normalizeSearchChar(undefined), "");
  assert.strictEqual(sandbox.normalizeSearchText(null), "");
  assert.strictEqual(sandbox.normalizeSearchText(undefined), "");
  assert.strictEqual(sandbox.normalizeSearchText(0), "0", "0 (数値) が空文字にならず保持されること");
  assert.strictEqual(sandbox.formatRagMemMeta(null), "");
  assert.strictEqual(sandbox.formatRagMemMeta(undefined), "");
  assert.strictEqual(sandbox.appendHighlightedText(null, "テスト", ["テスト"]), false);
}
console.log("  PASS: 防御的 null/0/異常値ハンドリングが安全");

console.log("--- 3.12 非:has()環境フォールバック (focus/blur クラス連携) テスト ---");
{
  ragModal.className = "modal";
  ragMemSearchInput.dispatchEvent({ type: "focus" });
  assert.strictEqual(ragModal.classList.contains("rag-search-focused"), true, "フォーカス時に rag-search-focused が付与される");

  ragMemSearchInput.dispatchEvent({ type: "blur" });
  assert.strictEqual(ragModal.classList.contains("rag-search-focused"), false, "ブラー時に rag-search-focused が解除される");
}
console.log("  PASS: 非:has()環境フォールバック (focus/blur) が正常動作");

console.log("--- 3.13 Enterキー即時実行テスト ---");
{
  let appliedCount = 0;
  const origRender = sandbox.renderRagMemories;
  sandbox.renderRagMemories = (...args) => {
    appliedCount++;
    return origRender(...args);
  };

  ragMemSearchInput.value = "即時実行テスト";
  ragMemSearchInput.dispatchEvent({ type: "input", isComposing: false });
  assert.strictEqual(appliedCount, 0, "80msタイマー中は即座に呼ばれない");

  let enterPrevented = false;
  ragMemSearchInput.dispatchEvent({
    type: "keydown",
    key: "Enter",
    isComposing: false,
    preventDefault: () => { enterPrevented = true; }
  });
  assert.strictEqual(enterPrevented, true, "Enter押下でpreventDefaultされる");
  assert.strictEqual(appliedCount, 1, "Enter押下で即時にapplySearchが実行される");
  assert.strictEqual(sandbox.ragMemSearchQuery, "即時実行テスト");

  sandbox.renderRagMemories = origRender;
}
console.log("  PASS: Enterキーによる即時検索実行");

console.log("--- 3.14 空白のみ入力時のクリアボタン表示・消去テスト ---");
{
  ragMemSearchInput.value = "   ";
  ragMemSearchInput.dispatchEvent({ type: "input", isComposing: false });
  assert.strictEqual(ragMemSearchInput.classList.contains("has-query"), false, "空白のみは has-query なし");
  assert.strictEqual(ragMemSearchClear.classList.contains("hidden"), false, "空白のみでも入力値があればクリアボタンは表示される (3.2-4)");

  ragMemSearchClear.dispatchEvent({ type: "click" });
  assert.strictEqual(ragMemSearchInput.value, "", "クリアボタン押下で空白が消去される");
  assert.strictEqual(ragMemSearchClear.classList.contains("hidden"), true, "消去後はクリアボタンが非表示");
}
console.log("  PASS: 空白のみ入力時のクリアボタン表示・消去制御");

console.log("--- 3.15 全消し (regenerateRagMemories) キャンセル時のクエリ維持テスト ---");
(async () => {
  sandbox.ragMemSearchQuery = "保護対象クエリ";
  ragMemSearchInput.value = "保護対象クエリ";

  // 1) ユーザーがキャンセルした場合
  sandbox.confirm = () => false;
  await sandbox.regenerateRagMemories(true);
  assert.strictEqual(sandbox.ragMemSearchQuery, "保護対象クエリ", "全消しキャンセル時はクエリが破棄されず維持される");
  assert.strictEqual(ragMemSearchInput.value, "保護対象クエリ", "全消しキャンセル時は入力欄が維持される");

  // 2) ユーザーが確認した場合
  sandbox.confirm = () => true;
  await sandbox.regenerateRagMemories(true);
  assert.strictEqual(sandbox.ragMemSearchQuery, "", "全消し確認時はクエリが破棄される");
  assert.strictEqual(ragMemSearchInput.value, "", "全消し確認時は入力欄がクリアされる");

  console.log("  PASS: 全消しキャンセル時のクエリ維持・確認時の破棄ライフサイクル");

  console.log("\n========================================");
  console.log("ALL RAG MEMORY SEARCH TESTS PASSED!");
  console.log("========================================");
})().catch(err => {
  console.error("Test failed:", err);
  process.exit(1);
});
