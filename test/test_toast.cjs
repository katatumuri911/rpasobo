// トースト通知 / 婉曲表現の自動復元通知 単体テスト
// 実行: node test/test_toast.cjs
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const appJs = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

// public/app.js からトーストおよび婉曲差分モジュールを抽出
const s0 = appJs.indexOf("const TOAST_MS =");
const s1 = appJs.indexOf("// 発言を書き換えたら、担当の Antigravity 会話も巻き戻す");
if (s0 < 0 || s1 < 0 || s1 <= s0) {
  throw new Error("app.js からトーストモジュールを抽出できませんでした");
}
const toastModuleSrc = appJs.slice(s0, s1);

class FakeTimerSystem {
  constructor() {
    this.currentTime = 1000000;
    this.nextId = 1;
    this.timers = new Map();
  }
  now() {
    return this.currentTime;
  }
  setTimeout(fn, ms) {
    const id = this.nextId++;
    const delay = Math.max(0, Number(ms) || 0);
    this.timers.set(id, { fn, triggerAt: this.currentTime + delay });
    return id;
  }
  clearTimeout(id) {
    this.timers.delete(id);
  }
  getActiveTimerCount() {
    return this.timers.size;
  }
  advance(ms) {
    const targetTime = this.currentTime + ms;
    while (true) {
      let earliest = null;
      let earliestId = null;
      for (const [id, t] of this.timers.entries()) {
        if (t.triggerAt <= targetTime) {
          if (!earliest || t.triggerAt < earliest.triggerAt) {
            earliest = t;
            earliestId = id;
          }
        }
      }
      if (!earliest) break;
      this.currentTime = earliest.triggerAt;
      this.timers.delete(earliestId);
      earliest.fn();
    }
    this.currentTime = targetTime;
  }
}

function createTestEnvironment() {
  const fakeTimers = new FakeTimerSystem();
  const operationsLog = [];

  class StubClassList {
    constructor(owner) {
      this.owner = owner;
      this.classes = new Set();
    }
    add(...names) {
      for (const n of names) {
        this.classes.add(n);
        operationsLog.push({ type: "classList.add", target: this.owner.id, name: n });
      }
    }
    remove(...names) {
      for (const n of names) {
        this.classes.delete(n);
        operationsLog.push({ type: "classList.remove", target: this.owner.id, name: n });
      }
    }
    contains(name) {
      return this.classes.has(name);
    }
  }

  class StubElement {
    constructor(id) {
      this.id = id;
      this.classList = new StubClassList(this);
      this.dataset = {};
      this._textContent = "";
      this.onclick = null;
    }
    get textContent() {
      return this._textContent;
    }
    set textContent(val) {
      this._textContent = val;
      operationsLog.push({ type: "textContent", target: this.id, value: val });
    }
  }

  const elements = {
    toast: new StubElement("toast"),
    toastIcon: new StubElement("toastIcon"),
    toastMsg: new StubElement("toastMsg"),
  };
  elements.toast.classList.add("hidden");

  const sandbox = {
    console,
    Date: {
      now: () => fakeTimers.now(),
    },
    setTimeout: (fn, ms) => fakeTimers.setTimeout(fn, ms),
    clearTimeout: (id) => fakeTimers.clearTimeout(id),
    $: (id) => elements[id] || null,
    decToastCount: 0,
    decToastTimer: 0,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(toastModuleSrc, sandbox, { filename: "app.js#toast" });

  return { sandbox, elements, fakeTimers, operationsLog };
}

console.log("=== 1. toast() の基本表示とタイマー検証 ===");
{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();
  // 1-1: 既存形式 toast(msg) -> kind: "info", 6000ms
  sandbox.toast("標準メッセージ");
  assert.strictEqual(elements.toast.classList.contains("hidden"), false, "hidden が解除されること");
  assert.strictEqual(elements.toast.dataset.kind, "info", "kind が info であること");
  assert.strictEqual(elements.toast.dataset.long, "0", "48文字以下は long=0");
  assert.strictEqual(elements.toastIcon.textContent, "", "info のアイコンは空文字");
  assert.strictEqual(elements.toastMsg.textContent, "標準メッセージ");
  assert.strictEqual(fakeTimers.getActiveTimerCount(), 1, "6000ms のタイマーが1本登録されること");

  fakeTimers.advance(5999);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "5999ms では fade-out していない");
  fakeTimers.advance(1); // 6000ms 経過
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true, "6000ms で fade-out が付与されること");
  fakeTimers.advance(279);
  assert.strictEqual(elements.toast.classList.contains("hidden"), false, "279ms ではまだ hidden にならない");
  fakeTimers.advance(1); // 280ms 経過
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "280ms 後に hidden が付与されること");
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "hidden 付与と同時に fade-out が外れること");
  console.log("  PASS: toast(msg) 6000ms 自動消去");
}

{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();
  // 1-2: 既存形式 toast(msg, 4000)
  sandbox.toast("4秒メッセージ", 4000);
  assert.strictEqual(elements.toast.dataset.kind, "info");
  fakeTimers.advance(3999);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false);
  fakeTimers.advance(1);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
  fakeTimers.advance(280);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true);
  console.log("  PASS: toast(msg, 4000) 4000ms 自動消去");
}

{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();
  // 1-3: 種別オプション
  // ok: 2000ms, アイコン ✓
  sandbox.toast("コピーしました", { kind: "ok" });
  assert.strictEqual(elements.toast.dataset.kind, "ok");
  assert.strictEqual(elements.toastIcon.textContent, "✓");
  assert.strictEqual(elements.toastMsg.textContent, "コピーしました");
  fakeTimers.advance(2000);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
  fakeTimers.advance(280);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true);

  // warn: 3600ms, アイコン !
  sandbox.toast("警告です", { kind: "warn" });
  assert.strictEqual(elements.toast.dataset.kind, "warn");
  assert.strictEqual(elements.toastIcon.textContent, "!");
  fakeTimers.advance(3600);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
  fakeTimers.advance(280);

  // err: 6000ms, アイコン ×
  sandbox.toast("失敗しました", { kind: "err" });
  assert.strictEqual(elements.toast.dataset.kind, "err");
  assert.strictEqual(elements.toastIcon.textContent, "×");
  fakeTimers.advance(6000);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
  fakeTimers.advance(280);

  // progress: 永続（タイマーなし）
  sandbox.toast("処理中…", { kind: "progress" });
  assert.strictEqual(elements.toast.dataset.kind, "progress");
  assert.strictEqual(fakeTimers.getActiveTimerCount(), 0, "progress はタイマーなし");
  fakeTimers.advance(60000);
  assert.strictEqual(elements.toast.classList.contains("hidden"), false, "progress は消えない");

  // ms: 0 (明示指定でタイマーなし)
  sandbox.toast("常時表示", 0);
  assert.strictEqual(fakeTimers.getActiveTimerCount(), 0, "ms: 0 はタイマーなし");

  // kind 指定かつ ms 明示指定 (ms が優先)
  sandbox.toast("長めの成功", { kind: "ok", ms: 5000 });
  assert.strictEqual(elements.toast.dataset.kind, "ok");
  fakeTimers.advance(2000);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "2000ms では消えない");
  fakeTimers.advance(3000);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true, "5000ms で消える");
  console.log("  PASS: 種別ごとの秒数・アイコンと ms 明示指定");
}

console.log("=== 2. 長文フォールバック判定 ===");
{
  const { sandbox, elements } = createTestEnvironment();
  // 48文字以下
  const text48 = "あ".repeat(48);
  sandbox.toast(text48);
  assert.strictEqual(elements.toast.dataset.long, "0");

  // 49文字
  const text49 = "あ".repeat(49);
  sandbox.toast(text49);
  assert.strictEqual(elements.toast.dataset.long, "1");

  // 改行を含む短い文
  sandbox.toast("1行目\n2行目");
  assert.strictEqual(elements.toast.dataset.long, "1");
  console.log("  PASS: 長文フォールバック (48文字境界 / 改行判定)");
}

console.log("=== 3. 重複制御 (同一文言の連打対策) と dedupe: false ===");
{
  const { sandbox, elements, fakeTimers, operationsLog } = createTestEnvironment();

  // 1回目の呼び出し
  sandbox.toast("コピーしました", { kind: "ok" });
  const firstAssigns = operationsLog.filter((op) => op.type === "textContent" && op.target === "toastMsg").length;
  assert.strictEqual(firstAssigns, 1);

  // 500ms 後に同じ文言・同じ種別で呼び出し（1200ms 以内）
  fakeTimers.advance(500);
  sandbox.toast("コピーしました", { kind: "ok" });
  const secondAssigns = operationsLog.filter((op) => op.type === "textContent" && op.target === "toastMsg").length;
  assert.strictEqual(secondAssigns, 1, "同一文言の再代入はスキップされること");

  // タイマーが延長されていることを確認（500ms + 2000ms = 2500ms 後に消去）
  fakeTimers.advance(1999);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "タイマーが延長され、まだ消えていないこと");
  fakeTimers.advance(1);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true, "延長された2000ms後に fade-out");
  fakeTimers.advance(280);

  // dedupe: false の場合は同一文言でも再代入されること
  sandbox.toast("通知A", { kind: "ok", dedupe: false });
  const a1 = operationsLog.filter((op) => op.type === "textContent" && op.value === "通知A").length;
  assert.strictEqual(a1, 1);
  fakeTimers.advance(200);
  sandbox.toast("通知A", { kind: "ok", dedupe: false });
  const a2 = operationsLog.filter((op) => op.type === "textContent" && op.value === "通知A").length;
  assert.strictEqual(a2, 2, "dedupe:false なら本文が再代入されること");

  console.log("  PASS: 同一文言のタイマー延長と dedupe: false");
}

console.log("=== 4. dismissToast (タップ消去) とライフサイクル ===");
{
  const { sandbox, elements, fakeTimers, operationsLog } = createTestEnvironment();

  sandbox.toast("消せるトースト", { kind: "ok" });
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);

  // タップ実行
  assert.strictEqual(typeof elements.toast.onclick, "function", "onclick に dismissToast が登録されていること");
  elements.toast.onclick();
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true, "タップ直後に fade-out");

  // fade-out 退場中に同じ文言が呼ばれた場合 -> 新規表示として本文が再代入されること
  fakeTimers.advance(100);
  sandbox.toast("消せるトースト", { kind: "ok" });
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "fade-out が解除されて再表示");

  // 再びタップして最後まで消す
  elements.toast.onclick();
  fakeTimers.advance(280);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true);

  // 消去直後（toastLastAt = 0 にリセットされているため）同一文言を新規表示できる
  sandbox.toast("消せるトースト", { kind: "ok" });
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);

  console.log("  PASS: dismissToast と退場中・消去直後の再要求");
}

console.log("=== 5. アクセシビリティ順序 (hidden 解除が本文代入より先行) ===");
{
  const { sandbox, elements, operationsLog } = createTestEnvironment();
  operationsLog.length = 0;

  sandbox.toast("アクセシビリティ確認");
  const hiddenRemoveIndex = operationsLog.findIndex((op) => op.type === "classList.remove" && op.name === "hidden");
  const textAssignIndex = operationsLog.findIndex((op) => op.type === "textContent" && op.target === "toastMsg");

  assert(hiddenRemoveIndex >= 0, "hidden の削除が記録されていること");
  assert(textAssignIndex >= 0, "本文の代入が記録されていること");
  assert(hiddenRemoveIndex < textAssignIndex, "hidden 削除が本文代入より前であること（live region 読み上げ保証）");
  console.log("  PASS: aria-live のための DOM 更新順序");
}

console.log("=== 6. 新規通知による旧タイマー破棄 ===");
{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();
  sandbox.toast("1本目", 6000);
  fakeTimers.advance(1000);
  // 1秒後に2本目（2000ms）
  sandbox.toast("2本目", 2000);
  assert.strictEqual(elements.toastMsg.textContent, "2本目");

  fakeTimers.advance(1999);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false);
  fakeTimers.advance(1); // 2本目の2000ms 到達
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
  console.log("  PASS: 新規通知によるタイマー差し替え");
}

console.log("=== 7. decensorSig / checkDecensorDiff / announceDecensor 単体検証 ===");
{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();

  // 7-1: decensorSig
  assert.strictEqual(sandbox.decensorSig(null), "");
  assert.strictEqual(sandbox.decensorSig({}), "");
  assert.strictEqual(sandbox.decensorSig({ applied: [] }), "");
  const sig1 = sandbox.decensorSig({ slot: "normal", ts: "2026-11-15T12:00:00", target_ts: "t1", applied: [{ from: "a", to: "b", count: 1 }] });
  const sig2 = sandbox.decensorSig({ slot: "normal", ts: "2026-11-15T12:00:00", target_ts: "t1", applied: [{ from: "a", to: "b", count: 2 }] });
  const sigSlot = sandbox.decensorSig({ slot: "erotic", ts: "2026-11-15T12:00:00", target_ts: "t1", applied: [{ from: "a", to: "b", count: 1 }] });
  const sigTargetTs = sandbox.decensorSig({ slot: "normal", ts: "2026-11-15T12:00:00", target_ts: "t2", applied: [{ from: "a", to: "b", count: 1 }] });
  assert.notStrictEqual(sig1, sig2, "同一秒でも count や applied が変わればシグネチャが異なること");
  assert.notStrictEqual(sig1, sigSlot, "同一秒でも slot が変わればシグネチャが異なること");
  assert.notStrictEqual(sig1, sigTargetTs, "同一秒でも target_ts が変わればシグネチャが異なること");

  // 7-2: checkDecensorDiff (初回 isFirst = true は発火しない)
  const state1 = {
    messages: [
      { id: "m1", role: "assistant", text_decensor: { applied: [{ from: "a", to: "b", count: 1 }] } }
    ]
  };
  sandbox.checkDecensorDiff(null, state1, true);
  fakeTimers.advance(1000);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "初回読み込みではトーストが出ないこと");

  // 7-3: 本文復元 1件（単数形文言）
  const state2 = {
    messages: [
      { id: "m1", role: "assistant", text_decensor: { applied: [{ from: "a", to: "b", count: 1 }] } }
    ]
  };
  sandbox.checkDecensorDiff({ messages: [] }, state2, false);
  fakeTimers.advance(799);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "集約ウィンドウ (800ms) 内はまだ鳴らない");
  fakeTimers.advance(1); // 800ms 経過
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toastMsg.textContent, "婉曲表現を戻しました");
  assert.strictEqual(elements.toast.dataset.kind, "ok");

  fakeTimers.advance(2200 + 280); // 消えるのを待つ
  assert.strictEqual(elements.toast.classList.contains("hidden"), true);

  // 7-4: 本文＋候補の集約（複数件）
  // ほぼ同時に本文(2件)と候補(1件)が到着
  const decBody = { applied: [{ from: "a", to: "b", count: 2 }] };
  const decChoices = { applied: [{ from: "c", to: "d", count: 1 }] };
  sandbox.announceDecensor(decBody);
  fakeTimers.advance(300);
  sandbox.announceDecensor(decChoices);
  fakeTimers.advance(799);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "2回目の到着から800msまでは待機");
  fakeTimers.advance(1); // 800ms経過
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toastMsg.textContent, "婉曲表現を3か所 戻しました", "800ms 以内に届いた件数が合算されること");

  fakeTimers.advance(2200 + 280); // 消去待機

  // 7-4b: checkDecensorDiff で choices_decensor の単独検知
  const stateChoicesOnly = {
    messages: [
      { id: "m_c1", role: "assistant", choices_decensor: { slot: "normal", applied: [{ from: "c", to: "d", count: 2 }] } }
    ]
  };
  sandbox.checkDecensorDiff({ messages: [] }, stateChoicesOnly, false);
  fakeTimers.advance(800);
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toastMsg.textContent, "婉曲表現を2か所 戻しました");
  assert.strictEqual(elements.toast.dataset.kind, "ok");

  fakeTimers.advance(2200 + 280);

  // 7-4c: checkDecensorDiff で 1つのメッセージに text_decensor と choices_decensor が同時に到着
  const stateBoth = {
    messages: [
      {
        id: "m_both",
        role: "assistant",
        text_decensor: { applied: [{ from: "a", to: "b", count: 1 }] },
        choices_decensor: { slot: "erotic", applied: [{ from: "c", to: "d", count: 3 }] },
      }
    ]
  };
  sandbox.checkDecensorDiff({ messages: [] }, stateBoth, false);
  fakeTimers.advance(800);
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toastMsg.textContent, "婉曲表現を4か所 戻しました", "同一メッセージ内の本文・候補復元が合算されること");

  fakeTimers.advance(2200 + 280);

  // 7-4d: 複数アシスタント発言の復元合算
  const stateMulti = {
    messages: [
      { id: "m_asst_1", role: "assistant", text_decensor: { applied: [{ from: "a", to: "b", count: 1 }] } },
      { id: "m_asst_2", role: "assistant", text_decensor: { applied: [{ from: "x", to: "y", count: 1 }] } },
    ]
  };
  sandbox.checkDecensorDiff({ messages: [] }, stateMulti, false);
  fakeTimers.advance(800);
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toastMsg.textContent, "婉曲表現を2か所 戻しました");

  fakeTimers.advance(2200 + 280);

  // 7-5: resetDecensorToast によるキャンセル
  sandbox.announceDecensor({ applied: [{ from: "x", to: "y", count: 1 }] });
  sandbox.resetDecensorToast();
  fakeTimers.advance(1000);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "resetDecensorToast で保留中の通知がキャンセルされること");

  // 7-5b: 表示中のトーストがある状態での resetDecensorToast（チャンネル切替時の即座退場）
  sandbox.toast("切替前通知", { kind: "ok" });
  assert.strictEqual(elements.toast.classList.contains("hidden"), false);
  assert.strictEqual(elements.toast.classList.contains("fade-out"), false);
  sandbox.resetDecensorToast();
  assert.strictEqual(elements.toast.classList.contains("fade-out"), true, "resetDecensorToast で表示中トーストが即座に fade-out すること");
  fakeTimers.advance(280);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "280ms 後に完全に hidden になること");

  console.log("  PASS: decensorSig / checkDecensorDiff / 800ms 集約 / resetDecensorToast");
}

console.log("=== 8. エッジケース検証 (null/undefined/未知kind/非assistant無視/復元消失) ===");
{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();

  // 8-1: toast(null) / toast(undefined)
  sandbox.toast(null);
  assert.strictEqual(elements.toastMsg.textContent, "");
  sandbox.toast(undefined);
  assert.strictEqual(elements.toastMsg.textContent, "");

  // 8-2: 未知の kind
  sandbox.toast("未知のkind", { kind: "something_weird" });
  assert.strictEqual(elements.toast.dataset.kind, "info", "未知kindはinfoにフォールバック");
  assert.strictEqual(fakeTimers.getActiveTimerCount(), 1, "6000ms のタイマーが登録されること");

  // 8-3: すでに hidden の状態で dismissToast を呼んでもエラーにならない
  elements.toast.classList.add("hidden");
  assert.doesNotThrow(() => sandbox.dismissToast());

  // 8-4: checkDecensorDiff で user メッセージは無視される
  const stateUser = {
    messages: [
      { id: "u1", role: "user", text_decensor: { applied: [{ from: "a", to: "b", count: 1 }] } }
    ]
  };
  sandbox.checkDecensorDiff({ messages: [] }, stateUser, false);
  fakeTimers.advance(1000);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "user ロールのメッセージは検知されないこと");

  // 8-5: 復元が削除（null化）された時は通知を発火しない
  const statePrev = {
    messages: [
      { id: "m1", role: "assistant", text_decensor: { applied: [{ from: "a", to: "b", count: 1 }] } }
    ]
  };
  const stateNext = {
    messages: [
      { id: "m1", role: "assistant", text_decensor: null }
    ]
  };
  sandbox.checkDecensorDiff(statePrev, stateNext, false);
  fakeTimers.advance(1000);
  assert.strictEqual(elements.toast.classList.contains("hidden"), true, "text_decensor 削除時は鳴らないこと");

  // 8-6: toastMsg 要素が存在しない場合のフォールバック（#toast textContent 直接代入）
  const { sandbox: bareSandbox, elements: bareElements } = (() => {
    const ft = new FakeTimerSystem();
    const bToast = new (elements.toast.constructor)("toast");
    bToast.classList.add("hidden");
    const bEls = { toast: bToast };
    const sb = {
      console,
      Date: { now: () => ft.now() },
      setTimeout: (fn, ms) => ft.setTimeout(fn, ms),
      clearTimeout: (id) => ft.clearTimeout(id),
      $: (id) => bEls[id] || null,
      decToastCount: 0,
      decToastTimer: 0,
    };
    sb.window = sb;
    sb.globalThis = sb;
    vm.createContext(sb);
    vm.runInContext(toastModuleSrc, sb);
    return { sandbox: sb, elements: bEls };
  })();
  bareSandbox.toast("裸要素フォールバック");
  assert.strictEqual(bareElements.toast.textContent, "裸要素フォールバック", "toastMsg が無くても toast 要素に直接代入されること");

  console.log("  PASS: null/undefined / 未知kind / ユーザー発言無視 / 復元削除時ガード / DOMフォールバック");
}

console.log("=== 9. P1 通知モジュール連携検証 (copyMessageText / ThinkingUI.setThinking) ===");
{
  const { sandbox, elements, fakeTimers } = createTestEnvironment();

  // 9-1: copyMessageText の連携検証
  const sCopy0 = appJs.indexOf("async function copyText(text) {");
  const sCopy1 = appJs.indexOf("function makeMoreButton(");
  assert(sCopy0 > 0 && sCopy1 > sCopy0, "copyText / copyMessageText を抽出できること");
  const copySrc = appJs.slice(sCopy0, sCopy1);
  vm.runInContext(copySrc, sandbox);

  // navigator.clipboard スタブ
  let copiedText = "";
  sandbox.window.isSecureContext = true;
  sandbox.navigator = {
    clipboard: {
      writeText: async (t) => { copiedText = t; },
    },
  };

  // コピー成功
  (async () => {
    await sandbox.copyMessageText({ text: "テスト本文" });
    assert.strictEqual(copiedText, "テスト本文");
    assert.strictEqual(elements.toastMsg.textContent, "コピーしました");
    assert.strictEqual(elements.toast.dataset.kind, "ok");
    assert.strictEqual(elements.toastIcon.textContent, "✓");
    fakeTimers.advance(2000);
    assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
    fakeTimers.advance(280);
    assert.strictEqual(elements.toast.classList.contains("hidden"), true);

    // コピー失敗
    sandbox.navigator.clipboard.writeText = async () => { throw new Error("denied"); };
    sandbox.document = { execCommand: () => false };
    await sandbox.copyMessageText({ text: "失敗本文" });
    assert.strictEqual(elements.toastMsg.textContent, "コピーできませんでした");
    assert.strictEqual(elements.toast.dataset.kind, "err");
    assert.strictEqual(elements.toastIcon.textContent, "×");
    fakeTimers.advance(3999);
    assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "4000ms 指定のため3999msでは消えない");
    fakeTimers.advance(1);
    assert.strictEqual(elements.toast.classList.contains("fade-out"), true);
    fakeTimers.advance(280);

    // 9-2: ThinkingUI.setThinking の連携検証
    const normalizedAppJs = appJs.replace(/\r\n/g, "\n");
    const thinkingMatch = normalizedAppJs.match(/const ThinkingUI = \{([\s\S]*?)\n\};\n\n\/\/ -/);
    assert(thinkingMatch, "ThinkingUI を抽出できること");
    const thinkSrc = "ThinkingUI = {" + thinkingMatch[1] + "\n};";
    vm.runInContext(thinkSrc, sandbox);

    sandbox.state = {
      setting: {
        thinking_level: "auto",
        choices_thinking_level: "same",
      }
    };
    sandbox.sid = "s_test";
    sandbox.api = async (path, body) => ({
      ok: true,
      thinking_level: body.thinking_level,
      choices_thinking_level: body.choices_thinking_level,
    });

    // 両方変更: 思考: 高 / 候補: 低
    await sandbox.ThinkingUI.setThinking("high", "low");
    assert.strictEqual(elements.toastMsg.textContent, "思考: 高 / 候補: 低");
    assert.strictEqual(elements.toast.dataset.kind, "ok");

    // メインのみ変更: 思考: 最小
    fakeTimers.advance(2280);
    await sandbox.ThinkingUI.setThinking("minimal", null);
    assert.strictEqual(elements.toastMsg.textContent, "思考: 最小");
    assert.strictEqual(elements.toast.dataset.kind, "ok");

    // 候補のみ変更: 候補の思考: 中
    fakeTimers.advance(2280);
    await sandbox.ThinkingUI.setThinking(null, "medium");
    assert.strictEqual(elements.toastMsg.textContent, "候補の思考: 中");
    assert.strictEqual(elements.toast.dataset.kind, "ok");

    // 保存失敗時: { kind: "err", ms: 3500 }
    fakeTimers.advance(2280);
    sandbox.api = async () => ({ error: "APIエラーが発生しました" });
    await sandbox.ThinkingUI.setThinking("xhigh", null);
    assert.strictEqual(elements.toastMsg.textContent, "APIエラーが発生しました");
    assert.strictEqual(elements.toast.dataset.kind, "err");
    fakeTimers.advance(3499);
    assert.strictEqual(elements.toast.classList.contains("fade-out"), false, "3500ms 指定のため3499msでは消えない");
    fakeTimers.advance(1);
    assert.strictEqual(elements.toast.classList.contains("fade-out"), true);

    console.log("  PASS: copyMessageText / ThinkingUI.setThinking (P1 通知連携)");
    console.log("\nALL toast notification tests PASSED!");
  })().catch((e) => {
    console.error("FAIL:", e);
    process.exit(1);
  });
}
