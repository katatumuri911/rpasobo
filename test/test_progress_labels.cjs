// 進捗ラベル言い換え純粋関数の単体テスト
// 実行: node test/test_progress_labels.cjs
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "public", "progress_labels.js"));

assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "read", text: "view file" }), "📖 ファイルを読み込み");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "write", text: "write" }), "✍️ 応答を書き込み");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "search", text: "grep" }), "🔍 中身を検索");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "list", text: "list_dir" }), "📁 フォルダを確認");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "plan", text: "update_plan" }), "🧭 段取りを整理");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "command", text: "run_command" }), "⚙️ コマンドを実行");
assert.strictEqual(L.stepLabel({ kind: "reply", tool_kind: "", text: "返事を書いた" }), "✅ 応答を書き上げた");
assert.strictEqual(L.stepLabel({ kind: "think", tool_kind: "", text: "考えている" }), "… 考えている");
assert.strictEqual(
  L.stepLabel({ kind: "error", tool_kind: "", text: "エラー", filter_name: "コンテンツ安全フィルタ" }),
  "⛔ コンテンツ安全フィルタで停止"
);
assert.strictEqual(
  L.stepLabel({ kind: "error", tool_kind: "", text: "エラー", filter_name: "" }),
  "エラー"
);
// ide_poke 由来でない行（API 側）は文言を変えない
assert.strictEqual(L.stepLabel({ kind: "tool", text: "Gemini が生成中" }), "Gemini が生成中");
// 内部コマンドと返信本文は詳細に出さない
assert.strictEqual(L.stepDetail({ internal: true, detail: "python wait_input.py" }), "");
assert.strictEqual(L.stepDetail({ kind: "reply", detail: "本文の冒頭" }), "");
assert.strictEqual(L.stepDetail({ kind: "tool", tool_kind: "read", detail: "abc.md" }), "abc.md");
assert.strictEqual(L.stepRaw({ internal: true, detail: "python wait_input.py" }), "");
assert.strictEqual(L.stepRaw({ kind: "error", detail: "content safety filters" }), "content safety filters");

// app.js の pollProgress / notifyProgressStep 結合テスト (vm 実行)
const fs = require("fs");
const vm = require("vm");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");
const s0 = appSrc.indexOf("let progressHtml = \"\", progressSid = \"\", progressBusy = false;");
const s1 = appSrc.indexOf("\nfunction hasWaitingInput()");
if (s0 < 0 || s1 < 0 || s1 < s0) throw new Error("pollProgress ブロックを抽出できません");
const blockSrc = appSrc.slice(s0, s1);

async function runPollProgressTests() {
  const toasts = [];
  const progressBox = { innerHTML: "" };
  const logBox = { scrollHeight: 500, scrollTop: 400, clientHeight: 100 };
  let apiResponse = { items: [], running: false };
  const ctx = {
    ProgressLabels: L,
    document: { hidden: false },
    state: { id: "s1", setting: { ide_session: "ide1" }, messages: [{ role: "assistant", id: "a1" }] },
    sid: "s1",
    navHoldBottom: false,
    topAlignHold: false,
    pinnedBottom: false,
    log: logBox,
    $: (id) => (id === "progress" ? progressBox : null),
    esc: (v) => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
    api: async () => apiResponse,
    toast: (msg, opts) => toasts.push({ msg, opts }),
    Set,
  };
  vm.createContext(ctx);
  vm.runInContext(blockSrc, ctx);

  // 1. wait_input.py のみのときは「⏳ 応答を待っています」が出て、トーストは出ない
  apiResponse = {
    running: true,
    items: [{ kind: "tool", tool: "run_command", tool_kind: "command", internal: true, text: "run", detail: "", done: false, step: 1, call: 0 }],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("⏳ 応答を待っています"));
  assert.ok(!progressBox.innerHTML.includes("wait_input.py"));
  assert.strictEqual(toasts.length, 0);

  // 2. read → 同じステップ再ポーリングでトースト連打しない
  apiResponse = {
    running: true,
    items: [
      { kind: "tool", tool: "run_command", tool_kind: "command", internal: true, text: "run", detail: "", done: true, step: 1, call: 0 },
      { kind: "tool", tool: "view_file", tool_kind: "read", internal: false, text: "view_file", detail: "abc.md", done: false, step: 2, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("📖 ファイルを読み込み"));
  assert.ok(progressBox.innerHTML.includes("abc.md"));
  assert.ok(!progressBox.innerHTML.includes("view_file"));
  assert.strictEqual(toasts.length, 1);
  assert.strictEqual(toasts[0].msg, "📖 abc.mdを読み込んでいます");
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, 1, "0.8秒ポーリングで同じステップのトーストが連打されないこと");

  // 3. write (outbox) → detail 隠蔽、トーストは「✍️ 応答を書き込んでいます」
  apiResponse = {
    running: true,
    items: [
      { kind: "tool", tool: "write_to_file", tool_kind: "write", internal: false, text: "write", detail: "", done: false, step: 3, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("✍️ 応答を書き込み"));
  assert.strictEqual(toasts.length, 2);
  assert.strictEqual(toasts[1].msg, "✍️ 応答を書き込んでいます");

  // 4. フィルタ停止 → filter クラス・フィルタ名・title に生文・トースト
  apiResponse = {
    running: false,
    items: [
      { kind: "error", tool: "", tool_kind: "", internal: false, filter_name: "コンテンツ安全フィルタ", text: "エラー", detail: "content safety filters", done: true, step: 4, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("p-row error filter"));
  assert.ok(progressBox.innerHTML.includes("⛔ コンテンツ安全フィルタで停止"));
  assert.ok(progressBox.innerHTML.includes('title="content safety filters"'));
  assert.strictEqual(toasts.length, 3);
  assert.strictEqual(toasts[2].msg, "⛔ コンテンツ安全フィルタで止まりました");

  // 5. Google provider 経路 → note がそのまま出て、🔧 作業に化けない
  ctx.state = { id: "s2", setting: { ai_provider: "google" }, messages: [] };
  ctx.sid = "s2";
  apiResponse = { running: true, items: [], note: "Google APIで生成中" };
  await ctx.pollProgress();
  assert.strictEqual(progressBox.innerHTML, '<div class="p-note">Google APIで生成中</div>');
}

runPollProgressTests().then(() => {
  console.log("progress_labels: ok");
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
