// 進捗ラベル言い換え純粋関数の単体テスト
// 実行: node test/test_progress_labels.cjs
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "public", "progress_labels.js"));

assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "read", text: "view file" }), "📖 ファイルを読み込み");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "write", text: "write" }), "✍️ プロンプトを用意");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "search", text: "grep" }), "🔍 中身を検索");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "list", text: "list_dir" }), "📁 フォルダを確認");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "plan", text: "update_plan" }), "🧭 段取りを整理");
assert.strictEqual(L.stepLabel({ kind: "tool", tool_kind: "command", text: "run_command" }), "⚙️ コマンドを実行");
assert.strictEqual(L.stepLabel({ kind: "reply", tool_kind: "", text: "返事を書いた" }), "返答完了");
assert.strictEqual(L.stepLabel({ kind: "reply", detail: "（待機）" }), "プロンプトを渡した");
assert.strictEqual(L.stepLabel({ kind: "think", tool_kind: "", text: "考えている" }), "考えている");
assert.strictEqual(
  L.stepLabel({ kind: "error", tool_kind: "", text: "エラー", filter_name: "コンテンツ安全フィルタ" }),
  "コンテンツ安全フィルタで停止"
);
assert.strictEqual(
  L.stepLabel({ kind: "error", tool_kind: "", text: "エラー", filter_name: "" }),
  "エラー"
);
assert.strictEqual(
  L.stepLabel({ kind: "error", tool_kind: "", text: "エラー",
                filter_name: "400エラー：送信内容がAIに受け付けられませんでした" }),
  "400エラー：送信内容がAIに受け付けられませんでした"
);
assert.strictEqual(
  L.stepLabel({ kind: "error", tool_kind: "", text: "エラー", error_kind: "capacity",
                filter_name: "503エラー：モデルが混雑（容量不足）" }),
  "503エラー：モデルが混雑（容量不足）"
);
// ide_poke 由来でない行（API 側）は文言を変えない
assert.strictEqual(L.stepLabel({ kind: "tool", text: "Gemini が生成中" }), "Gemini が生成中");
// 内部コマンドと返信本文は詳細に出さない
assert.strictEqual(L.stepDetail({ internal: true, detail: "python wait_input.py" }), "");
assert.strictEqual(L.stepDetail({ kind: "reply", detail: "本文の冒頭" }), "");
assert.strictEqual(L.stepDetail({ kind: "tool", tool_kind: "read", detail: "abc.md" }), "abc.md");
assert.strictEqual(L.stepRaw({ internal: true, detail: "python wait_input.py" }), "");
assert.strictEqual(L.stepRaw({ kind: "error", detail: "content safety filters" }), "content safety filters");
assert.strictEqual(L.stepRaw({ kind: "error", detail: "日本語の説明", raw_detail: "raw API error" }), "raw API error");
assert.strictEqual(
  L.stepLabel({ kind: "error", error_kind: "unknown", filter_name: "通信エラーが起きました", text: "エラー" }),
  "通信エラーが起きました"
);

// app.js の pollProgress / notifyProgressStep 結合テスト (vm 実行)
const fs = require("fs");
const vm = require("vm");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");
const s0 = appSrc.indexOf("let progressHtml = ");
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
    state: { id: "s1", setting: { ide_session: "ide1" }, messages: [{ role: "assistant", id: "a1" }, { role: "user", status: "pending", id: "u1" }] },
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

  // 1. wait_input.py のみのときは「⏳ 本文の返答を待っています」が出て、トーストは出ない
  apiResponse = {
    running: true,
    items: [{ kind: "tool", tool: "run_command", tool_kind: "command", internal: true, text: "run", detail: "", done: false, step: 1, call: 0 }],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("Antigravityの処理開始を待っています"));
  assert.ok(!progressBox.innerHTML.includes("本文の返答を待っています"), "注記と待機行を2行並べない");
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 1, "待機の情報は1行だけ");
  assert.ok(!progressBox.innerHTML.includes("返答完了"), "送信直後に返答完了を出さない");
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
  assert.strictEqual(toasts.length, 0, "通常作業（read）は窓に出るので短文トーストは出さない");
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, 0);

  // 3. write (outbox) → detail 隠蔽。毎ターン出るので短文トーストは出さない
  apiResponse = {
    running: true,
    items: [
      { kind: "tool", tool: "write_to_file", tool_kind: "write", internal: false, text: "write", detail: "", done: false, step: 3, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("✍️ プロンプトを用意"));
  assert.strictEqual(toasts.length, 0, "write では短文トーストを出さないこと");

  // 3b. write が完了しても「次にすることを考えている」を足さない
  apiResponse = {
    running: true,
    items: [
      { kind: "tool", tool: "write_to_file", tool_kind: "write", internal: false, text: "write", detail: "", done: true, step: 3, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("次にすることを考えている"));

  // 4. フィルタ停止 → filter クラス・フィルタ名・title に生文・トースト
  apiResponse = {
    running: false,
    items: [
      { kind: "error", tool: "", tool_kind: "", internal: false, filter_name: "コンテンツ安全フィルタ", text: "エラー", detail: "content safety filters", done: true, step: 4, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("p-row error filter"));
  assert.ok(progressBox.innerHTML.includes("コンテンツ安全フィルタで停止"));
  assert.ok(progressBox.innerHTML.includes('title="content safety filters"'));
  assert.strictEqual(toasts.length, 1);
  assert.strictEqual(toasts[0].msg, "コンテンツ安全フィルタで止まりました");

  // 4b. 容量不足 → 「停止」ではなく再試行中として1行に出し、警告トースト
  apiResponse = {
    running: true,
    items: [
      { kind: "error", tool: "", tool_kind: "", internal: false,
        filter_name: "503エラー：モデルが混雑（容量不足）", error_kind: "capacity", text: "エラー",
        detail: "選択したモデルは、いまサーバーが混雑していて使えません。自動で再試行しています（5/9回目）。",
        attempt: 5, done: true, step: 5, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("503エラー：モデルが混雑（容量不足）"));
  assert.ok(progressBox.innerHTML.includes(">⏳<"));
  assert.ok(progressBox.innerHTML.includes("サーバーが混雑"));
  const capRow = progressBox.innerHTML.slice(progressBox.innerHTML.lastIndexOf('<div class="p-row'));
  assert.ok(capRow.includes('class="p-row error filter'), "容量不足も error 行の折り返しレイアウトになる");
  assert.ok(!capRow.includes("で停止"));
  assert.strictEqual(toasts.length, 2);
  assert.ok(toasts[1].msg.startsWith("503エラー：モデルが混雑（容量不足）"));
  assert.strictEqual(toasts[1].opts.kind, "warn");

  // 4c. 400エラー → 番号付きの見出しと説明を2行で出し、「で止まりました」を付けない
  apiResponse = {
    running: false,
    items: [
      { kind: "error", tool: "", tool_kind: "", internal: false,
        filter_name: "400エラー：送信内容がAIに受け付けられませんでした", error_kind: "", text: "エラー",
        detail: "AIへの送信内容が受け付けられませんでした。プロンプトが長すぎるか、モデル設定に無効な値が含まれている可能性があります。",
        done: true, step: 6, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("400エラー：送信内容がAIに受け付けられませんでした"));
  assert.strictEqual(toasts.length, 3);
  assert.ok(toasts[2].msg.startsWith("400エラー："));
  assert.ok(!toasts[2].msg.includes("で止まりました"));

  // 4d. 未知のエラー → 日本語の見出しと対策を2行で出し、英語の生文を出さない
  apiResponse = {
    running: false,
    items: [
      { kind: "error", tool: "", tool_kind: "", internal: false,
        filter_name: "通信エラーが起きました", error_kind: "unknown", text: "エラー",
        detail: "AIとの通信中に問題が起きました。少し待ってからもう一度送信してください。直らない場合は、別のモデルに変えるか、別のプロバイダに切り替えてください。",
        raw_detail: "Something went wrong while contacting the model",
        done: true, step: 7, call: 0 },
    ],
  };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("通信エラーが起きました"));
  assert.ok(progressBox.innerHTML.includes("もう一度送信してください"));
  assert.ok(progressBox.innerHTML.includes('title="Something went wrong while contacting the model"'));
  assert.strictEqual(toasts.length, 4);
  assert.ok(toasts[3].msg.startsWith("通信エラーが起きました"));
  assert.ok(toasts[3].msg.includes("もう一度送信してください"));
  assert.ok(!toasts[3].msg.includes("Something went wrong"));
  assert.ok(!toasts[3].msg.includes("⚠️ エラー"));

  // 5. Google provider 経路 → note がそのまま出て、🔧 作業に化けない
  ctx.state = { id: "s2", setting: { ai_provider: "google" }, messages: [{ role: "user", status: "pending", id: "u" }] };
  ctx.sid = "s2";
  apiResponse = { running: true, items: [], note: "Google APIで生成中" };
  await ctx.pollProgress();
  assert.strictEqual(progressBox.innerHTML,
    '<div class="p-row now wait p-enter"><span class="p-mark">●</span><span class="p-text">Google APIで生成中</span></div>');

  // 5b. 旧サーバーの「会話は止まっている（合図を待っている）」も、
  // 待機行に統合して1行で出す（注記と待機行を2行並べない）
  ctx.state = { id: "s2b", setting: { ide_session: "ide2b" }, messages: [{ role: "user", status: "pending", id: "u2b" }] };
  ctx.sid = "s2b";
  apiResponse = { running: false, items: [], note: "会話は止まっている（合図を待っている）" };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("Antigravityの処理開始を待っています"));
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 1, "注記と待機行は1行に統合");
  assert.ok(!progressBox.innerHTML.includes("合図を待っている"));
  assert.ok(!progressBox.innerHTML.includes("次にすることを考えている"));

  // 5c. 会話を読めない等のエラー注記は、オレンジの待機行にせずエラー行にする
  ctx.state = { id: "s2c", setting: { ide_session: "ide2c" }, messages: [{ role: "user", status: "pending", id: "u2c" }] };
  ctx.sid = "s2c";
  apiResponse = { running: false, items: [], note: "会話を読めなかった: timeout" };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes('class="p-row error', "エラー行として表示"));
  assert.ok(progressBox.innerHTML.includes("p-enter"));
  assert.ok(progressBox.innerHTML.includes("会話を読めなかった"));
  assert.ok(!progressBox.innerHTML.includes("p-row now wait"));

  // 6. 容量不足の再試行は1〜4回目は静かにし、5回目からは毎回長文で知らせる
  ctx.state = { id: "s3", setting: { ide_session: "ide3" }, messages: [{ role: "user", status: "pending", id: "u" }] };
  ctx.sid = "s3";
  const cap = {
    kind: "error", tool: "", tool_kind: "", internal: false,
    filter_name: "503エラー：モデルが混雑（容量不足）", error_kind: "capacity", text: "エラー",
    detail: "選択したモデルは、いまサーバーが混雑していて使えません。自動で再試行しています（5/9回目）。",
    done: true, step: 10, call: 0,
  };
  const capAttempt = (n, step) => ({
    ...cap, attempt: n, step: step || (10 + n),
    detail: `選択したモデルは、いまサーバーが混雑していて使えません。自動で再試行しています（${n}/9回目）。`,
  });
  const beforeRetry = toasts.length;
  apiResponse = { running: true, user_time: "t1", items: [capAttempt(3)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeRetry, "3回目は通知しない");

  // 停止時でも1〜4回目は通知しない
  apiResponse = { running: false, user_time: "t2", items: [capAttempt(2)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeRetry, "停止時の2回目も通知しない");

  apiResponse = { running: true, user_time: "t1", items: [capAttempt(5)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeRetry + 1);
  assert.strictEqual(toasts[toasts.length - 1].opts.kind, "warn");
  assert.ok(toasts[toasts.length - 1].msg.includes("5/9回目"));

  // 同じ回数の再ポーリングでは増えない
  apiResponse = { running: true, user_time: "t1", items: [capAttempt(5, 15)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeRetry + 1);

  // 6,7,8,9回目は毎回通知する
  for (const n of [6, 7, 8, 9]) {
    apiResponse = { running: true, user_time: "t1", items: [capAttempt(n)] };
    await ctx.pollProgress();
    assert.strictEqual(toasts.length, beforeRetry + 1 + (n - 5));
    assert.ok(toasts[toasts.length - 1].msg.includes(`${n}/9回目`));
  }

  // 自動再試行が終わったら、最後に1回だけ失敗を知らせる
  apiResponse = { running: false, user_time: "t1", items: [capAttempt(9)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeRetry + 6);
  assert.strictEqual(toasts[toasts.length - 1].opts.kind, "err");
  assert.ok(toasts[toasts.length - 1].msg.includes("自動再試行が終了しました"));

  // 同じ停止状態を再描画しても増えない
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeRetry + 6);

  // 7. 進捗は直近3行だけ表示し、新しい行が来たら1行ずつ進める
  ctx.state = { id: "s4", setting: { ide_session: "ide4" }, messages: [{ role: "user", status: "pending", id: "u" }] };
  ctx.sid = "s4";
  const readItem = (n) => ({ kind: "tool", tool: "view_file", tool_kind: "read", internal: false,
    text: "view_file", detail: `file${n}.md`, done: false, step: n, call: 0 });
  const lastRow = () => progressBox.innerHTML.slice(progressBox.innerHTML.lastIndexOf('<div class="p-row'));
  apiResponse = { running: true, user_time: "t4", items: [1, 2, 3, 4, 5].map(readItem) };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("file4.md"));
  assert.ok(progressBox.innerHTML.includes("file5.md"));
  assert.ok(!progressBox.innerHTML.includes("file1.md"));
  assert.ok(!progressBox.innerHTML.includes("file2.md"));
  assert.ok(!progressBox.innerHTML.includes("file3.md"));
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.strictEqual((progressBox.innerHTML.match(/p-enter/g) || []).length, 1);
  assert.ok(lastRow().includes("本文の返答を待っています"));
  assert.ok(lastRow().includes("p-enter"));

  apiResponse = { running: true, user_time: "t4", items: [1, 2, 3, 4, 5, 6].map(readItem) };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("file5.md"));
  assert.ok(progressBox.innerHTML.includes("file6.md"));
  assert.ok(!progressBox.innerHTML.includes("file3.md"));
  assert.ok(!progressBox.innerHTML.includes("file4.md"));
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.ok(lastRow().includes("本文の返答を待っています"));

  // 同じ内容の再ポーリングでは増えない／再アニメしない
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.ok(!progressBox.innerHTML.includes("p-enter"));

  // 8. エラーの直後に wait_input.py が来ても、エラー表示を消さない
  ctx.state = { id: "s5", setting: { ide_session: "ide5" }, messages: [{ role: "user", status: "pending", id: "u" }] };
  ctx.sid = "s5";
  const err = {
    kind: "error", tool: "", tool_kind: "", internal: false,
    filter_name: "503エラー：モデルが混雑（容量不足）", error_kind: "capacity", text: "エラー",
    detail: "選択したモデルは、いまサーバーが混雑していて使えません。自動で再試行しています（5/9回目）。",
    attempt: 5, done: true, step: 20, call: 0,
  };
  const wait = {
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "Run wait_input.py", detail: "", done: true, step: 21, call: 0,
  };
  apiResponse = { running: true, user_time: "t5", items: [err, wait] };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("503エラー：モデルが混雑（容量不足）"));
  assert.ok(progressBox.innerHTML.includes("5/9回目"));
  assert.ok(!progressBox.innerHTML.includes("本文の返答を待っています"));

  // 9. 容量不足の初回通知は5回目から（1〜4回目は静かにする）
  ctx.state = { id: "s6", setting: { ide_session: "ide6" }, messages: [{ role: "user", status: "pending", id: "u" }] };
  ctx.sid = "s6";
  const capAt = (n) => ({ ...cap, attempt: n, step: 30 + n,
    detail: `選択したモデルは、いまサーバーが混雑していて使えません。自動で再試行しています（${n}/9回目）。` });
  const before = toasts.length;
  apiResponse = { running: true, user_time: "t6", items: [capAt(1)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, before);
  assert.ok(!progressBox.innerHTML.includes("503エラー"), "1回目の503は進捗にも出さない");
  apiResponse = { running: true, user_time: "t6", items: [capAt(4)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, before);
  assert.ok(!progressBox.innerHTML.includes("503エラー"), "4回目まで進捗にも出さない");
  apiResponse = { running: true, user_time: "t6", items: [capAt(5)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, before + 1);
  assert.ok(toasts[before].msg.includes("5/9回目"));
  assert.ok(progressBox.innerHTML.includes("503エラー"));
  apiResponse = { running: true, user_time: "t6", items: [capAt(6)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, before + 2);
  assert.ok(toasts[before + 1].msg.includes("6/9回目"));

  // 5回目以降のあとに2回目へ戻ったら、古い503行も消す
  apiResponse = { running: true, user_time: "t6", items: [capAt(2)] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, before + 2);
  assert.ok(!progressBox.innerHTML.includes("503エラー"), "2回目では進捗の503行も出さない");

  // attempt フィールドが欠けていても、詳細の「2/9回目」から2回目と判定する
  apiResponse = { running: true, user_time: "t6", items: [{ ...capAt(2), attempt: 0 }] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, before + 2);
  assert.ok(!progressBox.innerHTML.includes("503エラー"), "詳細から2回目と判定して出さない");

  // 10. 容量不足のあとにAIの返事が届いたら、エラー行は消して成功表示にする
  ctx.state = { id: "s7", setting: { ide_session: "ide7" },
    messages: [{ role: "user", status: "processing", id: "u7" }, { role: "assistant", id: "a7" }] };
  ctx.sid = "s7";
  apiResponse = { running: false, user_time: "t7", items: [
    { ...cap, attempt: 9, step: 40,
      detail: "選択したモデルは、いまサーバーが混雑していて使えません。自動で再試行しています（9/9回目）。" },
    { kind: "reply", tool: "", tool_kind: "", internal: false, text: "返事を書いた",
      detail: "（待機）", done: true, step: 41, call: 0 },
  ] };
  const beforeSuccess = toasts.length;
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("503エラー"));
  assert.ok(progressBox.innerHTML.includes("プロンプトを渡した"));
  assert.strictEqual(toasts.length, beforeSuccess, "本文の完了通知は poll() 側で出す");

  // 11. 応答が届いた後に古いポーリング結果が返っても、エラー通知を出さない
  ctx.state = { id: "s8", setting: { ide_session: "ide8" },
    messages: [{ role: "user", status: "answered", id: "u8" }, { role: "assistant", id: "a8" }] };
  ctx.sid = "s8";
  const beforeAnswered = toasts.length;
  apiResponse = { running: false, user_time: "t8", items: [{ ...cap, attempt: 9, step: 50 }] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeAnswered);

  // 12. 返事が届いているのに status 更新が遅れている場合も、容量不足の終了通知を出さない
  ctx.state = { id: "s9", setting: { ide_session: "ide9" },
    messages: [{ role: "user", status: "processing", id: "u9" }, { role: "assistant", id: "a9" }] };
  ctx.sid = "s9";
  const beforeAnsweredStatus = toasts.length;
  apiResponse = { running: false, user_time: "t9", items: [{ ...cap, attempt: 9, step: 60 }] };
  await ctx.pollProgress();
  assert.strictEqual(toasts.length, beforeAnsweredStatus);

  // 13. 返事が届いているのに status が processing のままでも、進捗のエラー行を消す
  ctx.state = { id: "s10", setting: { ide_session: "ide10" },
    messages: [{ role: "user", status: "processing", id: "u10" }, { role: "assistant", id: "a10" }] };
  ctx.sid = "s10";
  apiResponse = { running: true, user_time: "t10", items: [
    { ...cap, attempt: 9, step: 70 },
    { kind: "tool", tool: "write_to_file", tool_kind: "write", internal: false,
      text: "write", detail: "", done: true, step: 71, call: 0 },
  ] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("503エラー"));
  assert.ok(progressBox.innerHTML.includes("✍️ プロンプトを用意"));

  // 14. 新しいターンで進捗が1件（待機/思考）しかなくても、直前の3件窓から埋めて3件にする
  ctx.state = { id: "s11", setting: { ide_session: "ide11" }, messages: [{ role: "user", status: "pending", id: "u11" }] };
  ctx.sid = "s11";
  apiResponse = { running: true, user_time: "t11a", items: [1, 2, 3].map(readItem) };
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.ok(progressBox.innerHTML.includes("file3.md"));

  // 次のターンは待機だけ。直前の file2/file3 を残して3件にする
  apiResponse = { running: true, user_time: "t11b", items: [{
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 20, call: 0,
  }] };
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.ok(progressBox.innerHTML.includes("file2.md"));
  assert.ok(progressBox.innerHTML.includes("file3.md"));
  assert.ok(progressBox.innerHTML.includes("Antigravityの処理開始を待っています"));
  assert.strictEqual((progressBox.innerHTML.match(/p-enter/g) || []).length, 1);

  // 思考だけが来ても、待機行（いま何を待っているか）は消さずに3件にする
  apiResponse = { running: true, user_time: "t11b", items: [{
    kind: "think", tool: "", tool_kind: "", internal: false,
    text: "考えている", detail: "", done: false, step: 21, call: 0,
  }] };
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.ok(progressBox.innerHTML.includes("考えている"));
  assert.ok(progressBox.innerHTML.includes("本文の返答を待っています"),
    "作業行が出たら『処理開始待ち』ではなく本文待ちにする");
  assert.ok(progressBox.innerHTML.includes("file3.md"));
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);

  // 14b. 本文が届いて選択肢だけ待っているときは「選択肢を生成中」＋「返答完了」
  ctx.state = { id: "s15", setting: { ide_session: "ide15" }, messages: [
    { role: "user", status: "answered", id: "u15" },
    { role: "assistant", id: "a15", text: "本文", choices_status: "pending" },
  ] };
  ctx.sid = "s15";
  apiResponse = { running: true, user_time: "t15", items: [{
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 25, call: 0,
  }] };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("💬 選択肢を生成しています"));
  assert.ok(progressBox.innerHTML.includes("返答完了"));
  assert.ok(!progressBox.innerHTML.includes("本文の返答を待っています"));

  // 選択肢待ちのときの Antigravity 側の注記も、待機行に統合して1行で出す
  apiResponse = { running: false, user_time: "t15", items: [], note: "Antigravityの処理開始待ち" };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("💬 選択肢を生成しています"));
  assert.ok(progressBox.innerHTML.includes('<span class="p-text">返答完了</span>'));
  assert.ok(!progressBox.innerHTML.includes("Antigravityの処理開始待ち"));
  assert.ok(!progressBox.innerHTML.includes("選択肢生成待ち"), "注記を別行で出さない");

  // 14b2. 本文が届いていれば、status が processing のままでも
  // 「本文と選択肢を生成」と「返答完了」を同時に出さない
  ctx.state = { id: "s15b", setting: { ide_session: "ide15b" }, messages: [
    { role: "user", status: "processing", id: "u15b" },
    { role: "assistant", id: "a15b", text: "本文", choices_status: "pending" },
  ] };
  ctx.sid = "s15b";
  apiResponse = { running: true, user_time: "t15b", items: [{
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 26, call: 0,
  }] };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("💬 選択肢を生成しています"));
  assert.ok(progressBox.innerHTML.includes("返答完了"));
  assert.ok(!progressBox.innerHTML.includes("本文と選択肢を生成しています"),
    "本文到着後に『本文と選択肢』と『返答完了』を併記しない");

  // 14b3. スクショの再現: 選択肢待ち＋思考＋返答完了＋履歴でも最大3行。
  // 注記を別行で足して4行にしない。
  ctx.state = { id: "s15c", setting: { ide_session: "ide15c" }, messages: [
    { role: "user", status: "processing", id: "u15c" },
    { role: "assistant", id: "a15c", text: "本文", choices_status: "pending" },
  ] };
  ctx.sid = "s15c";
  apiResponse = { running: true, user_time: "t15c", items: [{
    kind: "think", tool: "", tool_kind: "", internal: false,
    text: "考えている", detail: "", done: false, step: 27, call: 0,
  }], history: [readItem(1), readItem(2)] };
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3,
    "注記・待機・思考・完了が重なっても最大3行");
  assert.ok(progressBox.innerHTML.includes("返答完了"));
  assert.ok(progressBox.innerHTML.includes("考えている"));
  assert.ok(!progressBox.innerHTML.includes("Antigravityの選択肢生成待ち"));
  assert.ok(!progressBox.innerHTML.includes("本文と選択肢を生成しています"));

  // 14b4. 本文が届いたあと（user=answered, choices待ち）は、
  // まだ items に 503 が残っていてもエラーを出さない
  ctx.state = { id: "s15d", setting: { ide_session: "ide15d" }, messages: [
    { role: "user", status: "answered", id: "u15d" },
    { role: "assistant", id: "a15d", text: "本文", choices_status: "pending" },
  ] };
  ctx.sid = "s15d";
  apiResponse = { running: false, user_time: "t15d", items: [
    { kind: "error", tool: "", tool_kind: "", internal: false,
      filter_name: "503エラー：モデルが混雑（容量不足）", error_kind: "capacity",
      text: "エラー", detail: "混雑しています（9/9回目）。", attempt: 9, done: true, step: 28, call: 0 },
  ] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("503エラー"), "成功後に503を残さない");
  assert.ok(progressBox.innerHTML.includes("返答完了"));
  assert.ok(progressBox.innerHTML.includes("選択肢を生成しています"));

  // 14c. 新しいターンでは、前のターンの「返答完了」を持ち越さない
  ctx.state = { id: "s16", setting: { ide_session: "ide16" }, messages: [
    { role: "user", status: "answered", id: "u16" },
    { role: "assistant", id: "a16", text: "本文", choices_status: "pending" },
  ] };
  ctx.sid = "s16";
  apiResponse = { running: true, user_time: "t16a", items: [{
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 30, call: 0,
  }] };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("返答完了"));
  // 次のユーザー入力（user_time が変わる）では、返答完了を引き継がない
  ctx.state = { id: "s16", setting: { ide_session: "ide16" }, messages: [
    { role: "user", status: "pending", id: "u16b" },
  ] };
  apiResponse = { running: true, user_time: "t16b", items: [{
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 31, call: 0,
  }] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("返答完了"), "送信直後に前ターンの返答完了を出さない");
  assert.ok(progressBox.innerHTML.includes("Antigravityの処理開始を待っています"));

  // 14d. 本文待ちの新ターンでは、サーバー履歴の前ターンの reply も出さない
  ctx.state = { id: "s17", setting: { ide_session: "ide17" },
    messages: [{ role: "user", status: "pending", id: "u17" }] };
  ctx.sid = "s17";
  apiResponse = { running: true, user_time: "t17", items: [{
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 40, call: 0,
  }], history: [
    { kind: "reply", tool: "", tool_kind: "", internal: false,
      text: "前の返事", detail: "", done: true, step: 39, call: 0 },
    { kind: "error", tool: "", tool_kind: "", internal: false,
      filter_name: "503エラー：モデルが混雑（容量不足）", error_kind: "capacity",
      text: "エラー", detail: "混雑しています（5/9回目）。", attempt: 5, done: true, step: 38, call: 0 },
  ] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("返答完了"), "前ターンの reply を履歴から出さない");
  assert.ok(!progressBox.innerHTML.includes("503エラー"), "前ターンのエラーを履歴から出さない");
  assert.ok(progressBox.innerHTML.includes("Antigravityの処理開始を待っています"));
  // 現在のステップとして前の reply が返ってきても、本文待ちのあいだは出さない
  apiResponse = { running: true, user_time: "t17", items: [{
    kind: "reply", tool: "", tool_kind: "", internal: false,
    text: "前の返事", detail: "", done: true, step: 41, call: 0,
  }] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("返答完了"), "待機中に reply を現在行として出さない");

  // 15. 返事が届いたら、履歴に残ったエラー行も消す（成功後に503が残らない）
  ctx.state = { id: "s12", setting: { ide_session: "ide12" },
    messages: [{ role: "user", status: "pending", id: "u12" }] };
  ctx.sid = "s12";
  apiResponse = { running: true, user_time: "t12", items: [{ ...cap, attempt: 5, step: 80 }] };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("503エラー"));
  ctx.state.messages = [{ role: "user", status: "processing", id: "u12" }, { role: "assistant", id: "a12" }];
  apiResponse = { running: false, user_time: "t12", items: [
    { ...cap, attempt: 5, step: 80 },
    { kind: "reply", tool: "", tool_kind: "", internal: false, text: "返事を書いた",
      detail: "（待機）", done: true, step: 81, call: 0 },
  ] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("503エラー"));
  assert.ok(progressBox.innerHTML.includes("プロンプトを渡した"));

  // 16. ページを開いた直後（フロント履歴が空）でも、サーバーの直前ターン履歴で3件にする
  ctx.state = { id: "s13", setting: { ide_session: "ide13" },
    messages: [{ role: "user", status: "pending", id: "u13" }] };
  ctx.sid = "s13";
  const waitOnly = {
    kind: "tool", tool: "run_command", tool_kind: "command", internal: true,
    text: "run", detail: "", done: false, step: 30, call: 0,
  };
  apiResponse = { running: true, user_time: "t13", items: [waitOnly],
    history: [readItem(1), readItem(2)] };
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.ok(progressBox.innerHTML.includes("file1.md"));
  assert.ok(progressBox.innerHTML.includes("file2.md"));
  assert.ok(progressBox.innerHTML.includes("Antigravityの処理開始を待っています"));
  assert.strictEqual((progressBox.innerHTML.match(/p-enter/g) || []).length, 1);

  // 2回目以降はフロント側の履歴を使い、サーバー履歴と二重にならない
  await ctx.pollProgress();
  assert.strictEqual((progressBox.innerHTML.match(/class="p-row/g) || []).length, 3);
  assert.strictEqual((progressBox.innerHTML.match(/<div class="p-row" title="file1\.md"/g) || []).length, 1);

  // 17. 容量不足以外のエラーも、返事が届いたら履歴から消す
  ctx.state = { id: "s14", setting: { ide_session: "ide14" },
    messages: [{ role: "user", status: "pending", id: "u14" }] };
  ctx.sid = "s14";
  const bad400 = { kind: "error", tool: "", tool_kind: "", internal: false,
    filter_name: "400エラー：送信内容がAIに受け付けられませんでした", error_kind: "",
    text: "エラー", detail: "送信内容が受け付けられませんでした。", done: true, step: 90, call: 0 };
  apiResponse = { running: false, user_time: "t14", items: [bad400] };
  await ctx.pollProgress();
  assert.ok(progressBox.innerHTML.includes("400エラー"));
  ctx.state.messages = [{ role: "user", status: "processing", id: "u14" }, { role: "assistant", id: "a14" }];
  apiResponse = { running: false, user_time: "t14", items: [
    bad400,
    { kind: "reply", tool: "", tool_kind: "", internal: false, text: "返事を書いた",
      detail: "（待機）", done: true, step: 91, call: 0 },
  ] };
  await ctx.pollProgress();
  assert.ok(!progressBox.innerHTML.includes("400エラー"));
  assert.ok(progressBox.innerHTML.includes("プロンプトを渡した"));

  // 18. 返答も選択肢も完了したら、最後の待機行を残さない
  ctx.state = { id: "s14", setting: { ide_session: "ide14" },
    messages: [{ role: "user", status: "answered", id: "u14" },
      { role: "assistant", id: "a14", text: "本文" }] };
  apiResponse = { running: false, user_time: "t14", items: [] };
  await ctx.pollProgress();
  assert.strictEqual(progressBox.innerHTML, "", "完了後に待機行が残らない");
}

runPollProgressTests().then(() => {
  console.log("progress_labels: ok");
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
