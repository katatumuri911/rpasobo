// 進捗行の言い換え（DOM に触らない純粋関数だけ。Node でテストできる）
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ProgressLabels = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  // tool_kind がある行だけ言い換える。無い行（API 側の進捗など）は元の文言を尊重する
  const STEP_LABELS = {
    command: "⚙️ コマンドを実行",
    read: "📖 ファイルを読み込み",
    write: "✍️ 応答を書き込み",
    search: "🔍 中身を検索",
    list: "📁 フォルダを確認",
    plan: "🧭 段取りを整理",
    tool: "🔧 作業",
  };
  function stepLabel(it) {
    if (!it) return "";
    const name = it.filter_name || "";
    if (it.kind === "error") return name ? `⛔ ${name}で停止` : (it.text || "⚠️ エラー");
    if (it.kind === "reply") return "✅ 応答を書き上げた";
    if (it.kind === "think") return "… 考えている";
    if (!it.tool_kind) return it.text || "";
    return STEP_LABELS[it.tool_kind] || it.text || "🔧 作業";
  }
  // 内部（wait_input.py の待機）は詳細も出さない。返事の本文冒頭も進捗には出さない
  function stepDetail(it) {
    if (!it || it.internal || it.kind === "reply") return "";
    return it.detail || "";
  }
  // title（ホバー）用。情報は消さずに残す
  function stepRaw(it) {
    if (!it || it.internal) return "";
    return it.detail || it.text || "";
  }
  return { STEP_LABELS, stepLabel, stepDetail, stepRaw };
});
