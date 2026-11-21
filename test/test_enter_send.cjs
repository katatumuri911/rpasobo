// Enter 送信のキーボード操作テスト
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");

// keydown リスナー部分を抽出
const startMarker = "let inputIsComposing = false;";
const endMarker = 'function setupAttachFlickSend()';
const a = src.indexOf(startMarker);
const b = src.indexOf(endMarker, a);
if (a < 0 || b < 0) throw new Error("抽出失敗");
const keydownCode = src.slice(a, b);

class El {
  constructor(id) {
    this.id = id;
    this.listeners = {};
    this.value = "";
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  fire(type, ev = {}) {
    let prevented = false;
    const list = this.listeners[type] || [];
    for (const fn of list) {
      fn({
        preventDefault: () => { prevented = true; },
        ...ev
      });
    }
    return prevented;
  }
}

const messageInput = new El("messageInput");
let sendCount = 0;
let hideAgQuotaCalls = 0;
const sandbox = {
  console,
  setTimeout: (fn, delay) => { fn(); },
  clearTimeout: (id) => {},
  $: (id) => (id === "messageInput" ? messageInput : null),
  send: () => { sendCount++; },
  hideAgQuota: () => { hideAgQuotaCalls++; },
  setTypingInput: () => {},
  updateFabState: () => {},
  syncActionControlsVisibility: () => {},
  autoResizeInput: () => {},
  selectedChoiceMap: new Set(),
  lastSelectedChoiceText: "",
  isMobileLayout: () => false,
  isMobileInputDevice: () => false,
};

vm.createContext(sandbox);
vm.runInContext(keydownCode, sandbox);

let pass = 0, fail = 0;
function test(name, cond) {
  if (cond) {
    pass++;
    console.log("  PASS " + name);
  } else {
    fail++;
    console.log("  FAIL " + name);
  }
}

console.log("[Test Enter Send Behavior]");

// 1. 通常の Enter 単体で送信される
sendCount = 0;
let prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 });
test("Enter単体でsend()が呼ばれる", sendCount === 1);
test("Enter単体でpreventDefaultされる", prevented === true);

// 2. Shift+Enter では改行（送信されない）
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: true, isComposing: false, keyCode: 13 });
test("Shift+Enterではsend()が呼ばれない", sendCount === 0);
test("Shift+EnterではpreventDefaultされない（改行可能）", prevented === false);

// 3. IME変換中の Enter (isComposing: true) では送信されない
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: true, keyCode: 13 });
test("isComposing=true ではsend()が呼ばれない", sendCount === 0);

// 4. keyCode 229 (IME変換中) では送信されない
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 229 });
test("keyCode=229 ではsend()が呼ばれない", sendCount === 0);

// 5. compositionstart/end 中
messageInput.fire("compositionstart");
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 });
test("compositionstart中はsend()が呼ばれない", sendCount === 0);

messageInput.fire("compositionend");
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 });
test("compositionend後はEnterでsend()が呼ばれる", sendCount === 1);

// 6. 他のキー（文字入力など）ではsend()が呼ばれない
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "a", shiftKey: false, isComposing: false, keyCode: 65 });
test("通常キー入力ではsend()が呼ばれない", sendCount === 0);

// 7. スマホ判定時（幅768px以下・モバイル端末）は Enter 改行（送信は送信ボタン）
sandbox.isMobileInputDevice = () => true;
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 });
test("スマホ判定時（幅768px以下・モバイル端末）はEnterでsend()が呼ばれず改行になる", sendCount === 0 && prevented === false);

// 8. PC判定時（デスクトップまたはウィンドウ幅が狭いマウスPC）は Enter 送信
sandbox.isMobileInputDevice = () => false;
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 });
test("PC判定時はEnterでsend()が呼ばれpreventDefaultされる", sendCount === 1 && prevented === true);

// 9. blur 時に inputIsComposing がリセットされる
messageInput.fire("compositionstart");
hideAgQuotaCalls = 0;
messageInput.fire("blur");
test("blurでhideAgQuotaが呼ばれる", hideAgQuotaCalls === 1);
sendCount = 0;
prevented = messageInput.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 });
test("blur後はinputIsComposingがリセットされてEnter送信できる", sendCount === 1 && prevented === true);

console.log(`\n===== PASS: ${pass} / FAIL: ${fail} =====`);
if (fail > 0) process.exit(1);

