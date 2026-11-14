// スマホで「返信が届いたら本文を上端にそろえる」自動スクロールの回帰テスト。
//
// キーボードを閉じる／URL バーが引っ込むと #chatLog の見える高さだけが増え、
// scrollTop はそのままだと最下段から離れる（gap が 100〜300px になる）。
// その状態で返信が届くと「下端付近か（120px以内）」の判定が外れ、
// 上端合わせも最下段への追従も起きず、新しい本文が画面の下に隠れてしまう。
// 対策として、見える高さが変わったら「最下段を読んでいた間だけ」最下段へ戻す。
//
// 実行: node test/test_mobile_bottom_follow.cjs
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8").replace(/\r\n/g, "\n");

let passed = 0;
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  PASS: ${name}`); passed++; }
  else { console.error(`  FAIL: ${name}`); failed++; }
}

// --- 1. 配線（関数が存在し、見える高さが変わったときに必ず呼ばれる） -----------------
const start = src.indexOf("function keepBottomAfterResize() {");
check("keepBottomAfterResize がある", start >= 0);
if (start < 0) { console.error(`\n結果: FAIL ${failed} / PASS ${passed}`); process.exit(1); }
let depth = 0, end = -1;
for (let i = src.indexOf("{", start); i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnSrc = src.slice(start, end);

const syncStart = src.indexOf("function syncViewportVars() {");
const syncEnd = src.indexOf("\nfunction keepBottomAfterResize", syncStart);
const syncSrc = src.slice(syncStart, syncEnd);
check("syncViewportVars（キーボード/URL バーの伸縮で呼ばれる）から呼んでいる", syncSrc.includes("keepBottomAfterResize()"));
check("見える高さが変わっても最下段を読んでいれば追従する", src.includes("pinnedBottom = true"));

// --- 2. 振る舞い（最下段を読んでいれば戻す / 読み返していれば触らない） --------------
function makeLog({ scrollHeight = 2000, clientHeight = 500, scrollTop = 0 }) {
  const box = { top: scrollTop };
  const log = {
    clientHeight,
    scrollHeight,
    getBoundingClientRect: () => ({ top: 0, bottom: clientHeight, height: clientHeight }),
  };
  Object.defineProperty(log, "scrollTop", {
    get: () => box.top,
    set: (v) => { box.top = Math.max(0, Math.min(v, scrollHeight - clientHeight)); },
  });
  return { log, box };
}

function run(state, scene, rafNow = true) {
  const rafs = [];
  const sandbox = {
    log: scene.log,
    pinnedBottom: state.pinnedBottom,
    navHoldBottom: state.navHoldBottom,
    rafOnce: (fn) => { rafs.push(fn); },
  };
  vm.createContext(sandbox);
  vm.runInContext(fnSrc, sandbox, { filename: "app.js#keepBottomAfterResize" });
  sandbox.keepBottomAfterResize();
  if (rafNow) rafs.forEach((fn) => fn());
  return { sandbox, rafs, scrolled: scene.box.top };
}

// キーボードが閉じて見える高さが増えた直後：scrollTop は取り残されている
let scene = makeLog({ scrollHeight: 2000, clientHeight: 700, scrollTop: 1300 });  // gap 300
let res = run({ pinnedBottom: true, navHoldBottom: false }, scene);
check("最下段を読んでいた → 新しい最下段へ戻す", res.scrolled === 1300 && scene.log.scrollTop === 1300);
check("判定は rAF 後（レイアウト確定後）にもう一度行う", res.rafs.length === 1);

// 過去ログを読み返している（ユーザーが上へスクロールした）
scene = makeLog({ scrollHeight: 2000, clientHeight: 700, scrollTop: 400 });
res = run({ pinnedBottom: false, navHoldBottom: false }, scene);
check("読み返している → 触らない", res.scrolled === 400 && res.rafs.length === 0);

// 「前/次」で本文先頭へ移動した直後（navHoldBottom）
scene = makeLog({ scrollHeight: 2000, clientHeight: 700, scrollTop: 300 });
res = run({ pinnedBottom: true, navHoldBottom: true }, scene);
check("前/次で移動した直後 → 触らない", res.scrolled === 300 && res.rafs.length === 0);

// すでに最下段（キーボードが出ただけ等）→ 何もしない
scene = makeLog({ scrollHeight: 2000, clientHeight: 500, scrollTop: 1500 });
res = run({ pinnedBottom: true, navHoldBottom: false }, scene);
check("すでに最下段なら何もしない", res.scrolled === 1500);

// 画面に収まっていてスクロールできない → 何もしない
scene = makeLog({ scrollHeight: 400, clientHeight: 500, scrollTop: 0 });
res = run({ pinnedBottom: true, navHoldBottom: false }, scene);
check("スクロールできないときは何もしない", res.scrolled === 0 && res.rafs.length === 0);

console.log(`\n===== PASS ${passed} / FAIL ${failed} =====`);
process.exit(failed ? 1 : 0);
