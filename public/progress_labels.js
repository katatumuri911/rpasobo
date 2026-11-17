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
    write: "✍️ プロンプトを用意",
    search: "🔍 中身を検索",
    list: "📁 フォルダを確認",
    plan: "🧭 段取りを整理",
    tool: "🔧 作業",
  };
  function stepLabel(it) {
    if (!it) return "";
    const name = it.filter_name || "";
    if (it.kind === "error") {
      // 容量不足は「停止」ではなく、IDE が自動で再試行している状態
      if (it.error_kind === "capacity") return name || "モデルが混雑";
      if (it.error_kind === "unknown") return name || "通信エラーが起きました";
      if (/^\d{3}エラー：/.test(name)) return name;
      return name ? `${name}で停止` : (it.text || "エラー");
    }
    if (it.kind === "reply") {
      // このエージェントは「（待機）」を返してプロンプトを渡し、次の生成を待つことがある。
      const d = String(it.detail || "").trim();
      if (/^[（(]?\s*待機\s*[）)]?$/i.test(d)) return "プロンプトを渡した";
      return "返答完了";
    }
    if (it.kind === "think") return "考えている";
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
    return it.raw_detail || it.detail || it.text || "";
  }
  return { STEP_LABELS, stepLabel, stepDetail, stepRaw };
});
