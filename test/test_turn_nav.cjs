// スマホの「前/次の返信へ」ボタンの回帰テスト。
// 最下段から前へ移動したあと、下端付近の自動スクロールに引き戻されて
// 同じ返信を選び直し、それ以上戻れなくなる不具合を防ぐ。
// 実行: node test/test_turn_nav.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8").replace(/\r\n/g, "\n");
const start = src.indexOf("function jumpToAssistantReply(dir) {");
const end = src.indexOf("\nfunction jumpToPrevAssistantReply", start);
if (start < 0 || end < 0) throw new Error("jumpToAssistantReply を抽出できません");
const fnSrc = src.slice(start, end);

// 自動最下段戻しの抑止が render / pollProgress の両方に残っていること。
if ((src.match(/const nearBottom = !navHoldBottom/g) || []).length < 2) {
  throw new Error("render / pollProgress の自動スクロール抑止がありません");
}
if (!src.includes("if (gap <= 2) navHoldBottom = false;")) throw new Error("最下段で抑止を解除する処理がありません");
if (!src.includes("if (log.scrollHeight - log.scrollTop - log.clientHeight <= 2) navHoldBottom = false;")) {
  throw new Error("移動先が最下段のときの抑止解除がありません");
}

function makeScene({ items, scrollTop, scrollHeight, clientHeight = 500 }) {
  const scroll = { top: scrollTop };
  const wraps = [];
  const bubbles = [];
  for (const item of items) {
    const wrap = { role: item.role, classList: { contains: (c) => c === item.role } };
    if (item.role === "assistant") {
      const bubble = {
        textContent: item.text === undefined ? "返信" : item.text,
        getBoundingClientRect: () => ({
          top: item.top - scroll.top,
          bottom: item.top + item.height - scroll.top,
          height: item.height,
        }),
      };
      wrap.querySelector = (sel) => sel === ".msg.assistant" ? bubble : null;
      bubbles.push(bubble);
    } else {
      wrap.querySelector = () => null;
    }
    wraps.push(wrap);
  }
  const log = {
    clientHeight,
    scrollHeight,
    getBoundingClientRect: () => ({ top: 0, bottom: clientHeight, height: clientHeight }),
    querySelectorAll: (sel) => {
      if (sel === ".msg-wrap.assistant .msg.assistant") return bubbles;
      if (sel === ".msg-wrap") return wraps;
      return [];
    },
  };
  Object.defineProperty(log, "scrollTop", {
    get: () => scroll.top,
    set: (v) => { scroll.top = v; },
  });
  return { log, scroll };
}

function jump(scene, dir, padTop) {
  const sandbox = {
    log: scene.log,
    updateRpBg: () => {},
    Math,
    Array,
    Number,
    getComputedStyle: padTop === undefined ? undefined : () => ({ paddingTop: padTop + "px" }),
  };
  vm.createContext(sandbox);
  vm.runInContext(fnSrc, sandbox, { filename: "app.js#jumpToAssistantReply" });
  sandbox.jumpToAssistantReply(dir);
  return scene.scroll.top;
}

const A = (top, height) => ({ role: "assistant", top, height });
let failures = 0;
function check(name, got, want) {
  const ok = Math.abs(got - want) < 0.01;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}: ${Math.round(got)} (expected ${want})`);
  if (!ok) failures++;
}

// 下端で、最新の返信が中央より上（画像などで下に余白がある）→ 直前の返信の本文先頭を上端へ。
let s = makeScene({ items: [A(0, 100), A(200, 100), A(400, 60)], scrollTop: 500, scrollHeight: 1000 });
check("最下段から前へ（本文先頭を上端）", jump(s, -1), 188);
// 上端に合わせた返信を基準に、さらに前へ進める。
check("上端合わせからさらに前へ", jump(s, -1), 0);
// スマホの padding-top（チップ避けの56px）の下に本文先頭が来ること。
s = makeScene({ items: [A(0, 100), A(200, 100), A(400, 60)], scrollTop: 500, scrollHeight: 1000 });
check("スマホの本文先頭（padding 56px）", jump(s, -1, 56), 138);

// 前へ移動したあとも下端から120px以内。そこで同じ返信を選び直さず、さらに前へ。
s = makeScene({ items: [A(0, 100), A(500, 100), A(700, 60)], scrollTop: 415, scrollHeight: 915 });
check("下端付近で前へ 1回目", jump(s, -1), 300);
check("下端付近で前へ 2回目", jump(s, -1), 0);

// 中央判定の基本。
s = makeScene({ items: [A(0, 200), A(250, 200), A(500, 200), A(800, 40), A(850, 40)], scrollTop: 350, scrollHeight: 1000 });
check("中央から前へ", jump(s, -1), 238);

// 最上段の次へ。
s = makeScene({ items: [A(0, 200), A(250, 200), A(500, 200)], scrollTop: 0, scrollHeight: 1000 });
check("最上段から次へ", jump(s, 1), 238);

// 最下段の次へ（最新の返信）。
s = makeScene({ items: [A(0, 200), A(250, 200), A(500, 200)], scrollTop: 500, scrollHeight: 1000 });
check("最下段から次へ（限界）", jump(s, 1), 500);

// 最新の返信を上端に合わせた状態から次へ → 画像・タグ・選択肢を含む最下段まで。
s = makeScene({ items: [A(0, 100), A(200, 100), A(400, 100)], scrollTop: 388, scrollHeight: 1200 });
check("最新の返信から次へ（最下段）", jump(s, 1), 700);

// 最古の返信を上端に合わせた状態から前へ → 本当の最上段まで。
s = makeScene({ items: [{ role: "user" }, A(50, 100), A(200, 100)], scrollTop: 38, scrollHeight: 1000 });
check("最古の返信から前へ（最上段）", jump(s, -1), 0);

// 最上段付近（返信の先頭合わせではない）から前へ → 最上段まで。
s = makeScene({ items: [A(100, 100), A(300, 100)], scrollTop: 30, scrollHeight: 1000 });
check("最上段付近から前へ（最上段）", jump(s, -1), 0);

if (failures) {
  console.error(`${failures} 件失敗`);
  process.exit(1);
}
console.log("ALL OK");
