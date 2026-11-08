const $ = (id) => {
  if (id === "setting-suggest-choices") return document.getElementById("f_suggest_choices");
  if (id === "message-input") return document.getElementById("messageInput");
  return document.getElementById(id);
};
const getStorage = (key, fallback = "") => {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : fallback;
  } catch {
    return fallback;
  }
};
const setStorage = (key, val) => {
  try {
    localStorage.setItem(key, val);
  } catch {}
};
let pollTimer = null;
let pollLoopFn = null;
function wakePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  if (typeof pollLoopFn === "function") {
    pollTimer = setTimeout(pollLoopFn, 50);
  }
}
window.wakePoll = wakePoll;
const log = $("chatLog");
let touchStartX = 0;
let touchStartY = 0;
let touchMoved = false;
let isPointerDown = false;
let lastScrollTime = 0;
const _initParams = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
let sid = (_initParams && _initParams.get("sid")) || getStorage("sid") || "";
let state = null;
let lastRev = -1;
let lastRotationNotice = "";
let formSid = "";  // 設定フォームに今入っているのはどのチャンネルの設定か
const openChoicesSet = new Set();       // 開いているアコーディオンのメッセージIDを保持
const userClosedChoicesSet = new Set(); // ユーザーが手動で閉じたアコーディオンのメッセージIDを保持
const selectedChoiceMap = new Map();    // メッセージID -> 選択中のchoiceインデックス
let lastSelectedChoiceText = "";        // 選択された候補のテキスト（手入力で変わったら選択解除）
let sessions = [];
let rpMode = getStorage("rpMode") === "1";
let currentPromptMsg = null;
let formDirty = false;
let sending = false;   // /api/send を送信中（サーバーから応答が返るまで）
let stopping = false;  // /api/send/stop を送信中
const dirtyFields = new Set();
const PERIODS = ["早朝", "午前", "昼", "夕方", "夜", "深夜"];
const PERIOD_EMOJIS = { "早朝": "🌅", "午前": "☀️", "昼": "🍱", "夕方": "🌇", "夜": "🌙", "深夜": "🌌" };
const PERIOD_DESCS = {
  "早朝": "一晩明けて翌朝へ！",
  "午前": "登校・授業・午前の業務へ",
  "昼": "昼休み・ランチへ",
  "夕方": "放課後・夕暮れの下校へ",
  "夜": "帰宅・夜の時間へ",
  "深夜": "就寝前・ベッドの中へ"
};
let editTarget = null;
let ragCollections = [];
let ragSelected = null;
let ragEditingEntry = null;
let ragActiveTab = "memory";
let ragMemEditingId = null;
let ragMemRegenTimer = null;
let meta = { app: {} };
let ideSessions = [];
let ideNewDir = "";  // 「新規会話」で作るフォルダ
let sysFiles = [];
let folderOpts = [];  // 登録フォルダごとの {folder, name, ok, files: [{label, path}], more}
let presets = [];
let textSaveHandler = null;
const collapsed = JSON.parse(getStorage("collapsedProjects", "{}"));
const seen = JSON.parse(getStorage("seenRev", "{}"));

// --- モバイル判定・短縮文言（CSS と同じ境界を matchMedia で持つ） ---
const mqMobile = (typeof window !== "undefined" && window.matchMedia) ? window.matchMedia("(max-width: 768px)") : null;
const mqTiny   = (typeof window !== "undefined" && window.matchMedia) ? window.matchMedia("(max-width: 360px)") : null;
function isMobileLayout() {
  return mqMobile ? mqMobile.matches : (typeof window !== "undefined" && window.innerWidth <= 768);
}
const SHORT_STATUS = {
  "サーバー未接続": "未接続", "AI応答待ち": "応答待ち", "AI応答＆候補生成中": "応答＋候補",
  "候補生成中": "候補", "タグ推敲中": "推敲", "記憶整理中": "記憶"
};

// 入力欄のプレースホルダ。スマホは Enter が改行（送信は送信ボタン）なので文言を分ける
const PLACEHOLDER_DESKTOP = "入力をEnterで送信";
const PLACEHOLDER_MOBILE = "セリフや行動を入力…";
function syncInputPlaceholder() {
  const ta = $("messageInput");
  if (ta) ta.placeholder = isMobileLayout() ? PLACEHOLDER_MOBILE : PLACEHOLDER_DESKTOP;
}

// ---------------------------------------------------------------- 「操作を表示」と reveal（モバイル）
// ドロワー / 設定 / モーダル / ⋯ シート / ライトボックス以外のオーバーレイを「開いている」とみなす
function isOverlayOpen() {
  const b = document.body;
  const sideOpen = !!(b && b.classList && b.classList.contains("side-open"));
  const settingsOpen = !!($("settingsPanel") && !$("settingsPanel").classList.contains("hidden"));
  const lbEl = $("lightbox");
  const lightboxOpen = !!lbEl && !lbEl.classList.contains("hidden");
  if (typeof document.querySelectorAll !== "function") return sideOpen || settingsOpen || lightboxOpen;
  const modalOpen = document.querySelectorAll(".modal:not(.hidden)").length > 0;
  const moreSheetOpen = document.querySelectorAll(".more-sheet").length > 0;   // ★z66/67 も覆う
  return sideOpen || settingsOpen || modalOpen || moreSheetOpen || lightboxOpen;
}
function isFooterFocused() {
  const footer = document.querySelector("footer");
  return !!(footer && document.activeElement && footer.contains(document.activeElement));
}
function hideReveal() {
  const el = $("mobileReveal");
  if (el) el.classList.add("hidden");
  const bd = $("revealBackdrop");
  if (bd) bd.classList.add("hidden");
}
function showReveal() {
  if (!isMobileLayout() || isOverlayOpen() || isFooterFocused()) return;
  const el = $("mobileReveal");
  if (!el) return;
  el.classList.remove("hidden");   // 時間経過では消さない（操作を選ぶか、オーバーレイ/入力で隠れるまで出す）
  // 画面のどこかをタップしたら閉じられるように、透明な受け皿を敷く
  const bd = $("revealBackdrop");
  if (bd) bd.classList.remove("hidden");
}
// 何度呼んでも安全。開いている間は必ず隠す（z64 が modal より上なので描画させない）
function syncActionControlsVisibility() {
  // ここでは rpMode を見ない。reveal の 🖼️ で背景表示を切ると「自分の土台」ごと消えて
  // 二度と押せなくなる（しかも localStorage に残って再読込でも戻らない）ため。
  const base = isMobileLayout() && !isOverlayOpen();
  const typing = isFooterFocused();
  // メニュー（reveal）は入力中だけ畳む。中央の「操作を表示」は常に押せる
  if (!base || typing) hideReveal();
  const reveal = $("mobileReveal");
  const revealVisible = !!(reveal && !reveal.classList.contains("hidden"));
  // 前/次はFABと同じ条件（モバイル・入力中でない・オーバーレイなし）で出す。
  // 入力中も中央の「操作を表示」は残すので、ナビだけを .nav-off で抜く
  const cluster = $("mobileTurnCluster");
  if (cluster) {
    cluster.classList.toggle("hidden", !base);
    cluster.classList.toggle("nav-off", !(base && !typing));
  }
  const toggle = $("mobileActionToggle");
  if (toggle) {
    toggle.classList.toggle("hidden", !base);
    toggle.textContent = (base && revealVisible) ? "✕ 閉じる" : "操作を表示";
  }
  // 担当IDE会話が無ければ 🤖 を出さない（[hidden] は CSS に負けるので .hidden を使う）
  const model = $("revealModel");
  if (model) model.classList.toggle("hidden", !(state && state.setting && state.setting.ide_session));
  // 🖼️ は背景表示のオン/オフを切り替えるトグル。今の状態を文言と色に映す
  const rpBtn = $("revealRpToggle");
  if (rpBtn) {
    rpBtn.classList.toggle("on", !!rpMode);
    const label = rpMode ? "背景表示をオフにする" : "背景表示をオンにする";
    rpBtn.title = label;
    rpBtn.setAttribute("aria-label", label);
  }
}

// スマホ向けレスポンシブ・UIヘルパー
function updateFooterHeight() {
  const footer = document.querySelector("footer");
  if (!footer) return;
  const h = footer.offsetHeight;
  if (h > 0 && typeof document !== "undefined" && document.documentElement && document.documentElement.style) {
    document.documentElement.style.setProperty("--footer-h", h + "px");
  }
}

function autoResizeInput() {
  const ta = $("messageInput");
  if (!ta || !ta.style) return;
  ta.style.height = "auto";
  // 最小/最大は CSS から読む（モバイルではフッターを細くするため 28/96 にしている）
  let minH = 38, maxH = 120;
  try {
    const cs = (typeof getComputedStyle === "function") ? getComputedStyle(ta) : null;
    const mn = cs ? parseFloat(cs.minHeight) : NaN;
    const mx = cs ? parseFloat(cs.maxHeight) : NaN;
    if (Number.isFinite(mn) && mn > 0) minH = mn;
    if (Number.isFinite(mx) && mx > 0) maxH = mx;
  } catch {}
  const newH = Math.min(Math.max(ta.scrollHeight || 0, minH), maxH);
  ta.style.height = newH + "px";
  ta.style.overflowY = (ta.scrollHeight || 0) > maxH ? "auto" : "hidden";
  updateFooterHeight();
}

// FAB の表示と役割を「今開いているレイヤー」から毎回1箇所で導出する。
// ここだけが fab-hidden / is-close を書き換える（散在する add/remove は禁止）。
// - visible: モバイル かつ 入力中でなく、何らかのオーバーレイ（モーダル/⋯シート/lightbox）に覆われていない
// - closeMode: FAB が上に乗るレイヤー（設定/ドロワー）が開いている
function updateFabState() {
  const fab = $("mobileFab");
  const mobile = isMobileLayout();
  const moreSheet = typeof document.querySelectorAll === "function" && document.querySelectorAll(".more-sheet").length > 0;
  const lbEl = $("lightbox");
  const lightbox = !!lbEl && !lbEl.classList.contains("hidden");
  const hasOpenModal = typeof document.querySelectorAll === "function" && document.querySelectorAll(".modal:not(.hidden)").length > 0;
  const isSideOpen = !!(document.body && document.body.classList && document.body.classList.contains("side-open"));
  const isSettingsOpen = !!($("settingsPanel") && !$("settingsPanel").classList.contains("hidden"));
  const typing = isFooterFocused();

  // モーダル・シート・lightbox は自前の閉じる導線があるので、FAB は完全に消す
  const visible = mobile && !typing && !moreSheet && !lightbox && !hasOpenModal;
  const closeMode = isSettingsOpen || isSideOpen;
  if (fab) {
    fab.classList.toggle("fab-hidden", !visible);   // 未知クラス（RP色など）は保持
    fab.classList.toggle("is-close", closeMode);
    fab.textContent = closeMode ? "✕" : "☰";
    const label = closeMode ? "閉じる" : "メニュー";
    fab.setAttribute("aria-label", label);
    fab.title = label;
  }
  syncFootBarPlacement();            // シート/ドロワー/フッターのどこに居るべきかを毎回導出
  syncActionControlsVisibility();
}

// FAB/Esc 共通: 最前面の開いているレイヤーを1つ閉じる。閉じたら true
function closeTopmostLayer() {
  const openModals = typeof document.querySelectorAll === "function"
    ? Array.from(document.querySelectorAll(".modal:not(.hidden)")) : [];
  if (openModals.length > 0) {
    openModals.forEach((m) => m.classList.add("hidden"));
    return true;
  }
  if ($("settingsPanel") && !$("settingsPanel").classList.contains("hidden")) {
    $("settingsPanel").classList.add("hidden");
    return true;
  }
  if (document.body && document.body.classList && document.body.classList.contains("side-open")) {
    document.body.classList.remove("side-open");
    return true;
  }
  return false;
}

function updateHeaderAndSideStatus() {
  const title = $("title");
  const sideTitle = $("sideTitle");
  if (sideTitle && title) sideTitle.textContent = title.textContent;

  const ragCount = $("ragCount");
  const sideRagCount = $("sideRagCount");
  if (sideRagCount && ragCount) sideRagCount.textContent = ragCount.textContent;

  const statusPill = $("statusPill");
  const sideStatusPill = $("sideStatusPill");
  if (sideStatusPill && statusPill) {
    sideStatusPill.className = statusPill.className;
    sideStatusPill.textContent = statusPill.textContent;
  }

  const sideRpToggle = $("sideRpToggle");
  if (sideRpToggle) sideRpToggle.classList.toggle("on", rpMode);

  // モバイルの状態チップ（表示専用）。状態クラスだけを取り出して写す
  const chip = $("mobileStatusChip");
  if (chip && statusPill) {
    const st = (statusPill.className || "").split(/\s+/).filter((c) => c && c !== "pill").join(" ");
    chip.className = ("pill " + st).trim();
    const text = statusPill.textContent || "";
    chip.textContent = (mqTiny && mqTiny.matches) ? (SHORT_STATUS[text] || text) : text;
    chip.classList.toggle("dim", !!rpMode);
  }
  syncActionControlsVisibility();
}

function syncFootBarPlacement() {
  const footBar = $("footBar");
  const footer = document.querySelector("footer");
  if (!footBar || !footer) return;                 // スロット欠品でもデスクトップ復帰は必ず通す
  if (isMobileLayout()) {
    const sheetOpen = !!($("modelSheet") && !$("modelSheet").classList.contains("hidden"));
    const target = (sheetOpen && $("modelSheetSlot")) || $("sideModelContainer") || footer;
    if (footBar.parentElement !== target) target.appendChild(footBar);
  } else if (footBar.parentElement !== footer) {
    if (typeof footer.insertBefore === "function") footer.insertBefore(footBar, $("attachList") || footer.firstChild);
    else footer.appendChild(footBar);
  }
}

async function api(path, body, method = null) {
  const m = method || (body ? "POST" : "GET");
  const opt = { method: m };
  if (body) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  return r.json();
}
const post = (path, body = {}) => api(path, { sid, ...body });
async function checked(path, body, method = null) {
  const result = await api(path, body, method);
  if (result.error) throw new Error(result.error);
  return result;
}
const imgUrl = (f) => "/img?f=" + encodeURIComponent(f);

function openLightbox(src) {
  if (!src) return;
  const lb = $("lightbox");
  const img = $("lightboxImg");
  if (!lb || !img) return;
  img.src = src;
  lb.classList.remove("hidden");
  updateFabState();
}

function closeLightbox() {
  const lb = $("lightbox");
  if (!lb) return;
  lb.classList.add("hidden");
  const img = $("lightboxImg");
  if (img) img.src = "";
  updateFabState();
}

if (typeof window !== "undefined") {
  window.openLightbox = openLightbox;
  window.closeLightbox = closeLightbox;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
// 発言の表示。Markdown（見出し・太字・斜体・取り消し線・コード・引用・リスト・表・区切り線・リンク）を HTML にする。
// 「」と（）の色分けはそのまま。〜 は fmtAltered が付ける「書き換えた所」の印
const ED = /[]/g;
const fmt = (text) => fmtMarked(String(text).replace(ED, ""));
function fmtMarked(text) {
  return md(text).replace(//g, '<span class="edited-text">').replace(//g, "</span>");
}
function mdInline(s) {
  const stash = [];
  const put = (h) => `\u0000${stash.push(h) - 1}\u0000`;
  s = s.replace(/`([^`\n]+)`/g, (_, c) => put(`<code>${esc(c)}</code>`))
    .replace(/\\([\\`*_{}\[\]()#+\-.!|~>])/g, (_, c) => put(esc(c)));
  const h = esc(s)
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => put(`<a href="${u}" target="_blank" rel="noopener">${t}</a>`))
    .replace(/\*\*(?!\s)([^*\n]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(?!\s)([^_\n]+?)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?![\s*])([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/~~(?!\s)([^~\n]+?)~~/g, "<del>$1</del>")
    .replace(/「[^」]*」/g, (m) => `<span class="speech">${m}</span>`)
    .replace(/（[^）]*）/g, (m) => `<span class="thought">${m}</span>`);
  const back = (x) => x.replace(/\u0000(\d+)\u0000/g, (_, n) => back(stash[n]));  // リンクの文字の中のコードも戻す
  return back(h);
}
const mdBr = (s) => mdInline(s).replace(/\n/g, "<br>");
function md(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [];
  const flush = () => { if (para.length) { out.push(`<p>${mdBr(para.join("\n"))}</p>`); para = []; } };
  const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  // 書き換えた所の印が行頭にあっても、見出しやリストの記号として読めるように
  const split = (l) => (l.startsWith("") ? ["", l.slice(1)] : ["", l]);
  for (let i = 0; i < lines.length; i++) {
    const [lead, l] = split(lines[i]);
    const bare = l.replace(ED, "");
    let m;
    if ((m = bare.match(/^\s*(```|~~~)/))) {
      flush();
      const code = [];
      while (++i < lines.length && !lines[i].replace(ED, "").trim().startsWith(m[1])) code.push(lines[i].replace(ED, ""));
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (!bare.trim()) { flush(); continue; }
    if ((m = l.match(/^(#{1,6})\s+(.*?)(\s+#+)?\s*$/))) {
      flush();
      out.push(`<h${m[1].length}>${mdInline(lead + m[2])}</h${m[1].length}>`);
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(bare)) { flush(); out.push("<hr>"); continue; }
    if (/^\s*>/.test(bare)) {
      flush();
      const q = [];
      for (; i < lines.length && /^\s*>/.test(lines[i].replace(ED, "")); i++) {
        const [ld, x] = split(lines[i]);
        q.push(ld + x.replace(/^\s*> ?/, ""));
      }
      i--;
      out.push(`<blockquote>${md(q.join("\n"))}</blockquote>`);
      continue;
    }
    if (LIST.test(l)) {
      flush();
      const items = [];
      for (; i < lines.length; i++) {
        const [ld, x] = split(lines[i]);
        const lm = x.match(LIST);
        if (lm) items.push({ ind: lm[1].replace(/\t/g, "    ").length, tag: /\d/.test(lm[2]) ? "ol" : "ul", text: ld + lm[3] });
        else if (x.replace(ED, "").trim() && /^\s+/.test(x) && items.length) items[items.length - 1].text += "\n" + ld + x.trim();
        else break;
      }
      i--;
      let h = "";
      const stack = [];
      for (const it of items) {
        while (stack.length > 1 && it.ind < stack[stack.length - 1].ind) h += `</li></${stack.pop().tag}>`;
        const top = stack[stack.length - 1];
        if (!top || it.ind > top.ind + 1) { stack.push({ ind: it.ind, tag: it.tag }); h += `<${it.tag}><li>`; }
        else h += "</li><li>";
        h += mdBr(it.text.replace(/^(?)\[([ xX])\]\s+/, (_, e, c) => e + (c === " " ? "☐ " : "☑ ")));
      }
      while (stack.length) h += `</li></${stack.pop().tag}>`;
      out.push(h);
      continue;
    }
    if (bare.includes("|") && i + 1 < lines.length && SEP.test(lines[i + 1].replace(ED, "")) && lines[i + 1].includes("-")) {
      flush();
      const align = cells(lines[i + 1].replace(ED, "")).map((c) => (c.endsWith(":") ? (c.startsWith(":") ? "center" : "right") : ""));
      const row = (l2, tag) => "<tr>" + cells(l2.replace(ED, "")).map((c, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${mdInline(c)}</${tag}>`).join("") + "</tr>";
      let h = "<table><thead>" + row(lines[i], "th") + "</thead><tbody>";
      for (i += 2; i < lines.length && lines[i].replace(ED, "").includes("|") && lines[i].replace(ED, "").trim(); i++) h += row(lines[i], "td");
      i--;
      out.push(h + "</tbody></table>");
      continue;
    }
    para.push(lead + l);
  }
  flush();
  return out.join("");
}
function fmtAltered(original, modified) {
  const a = Array.from(original || "");
  const b = Array.from(modified || "");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let end = 0;
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
  if (start === b.length && end === 0) return fmt(modified);
  const prefix = b.slice(0, start).join("");
  const changed = b.slice(start, b.length - end).join("");
  const suffix = b.slice(b.length - end).join("");
  // 書き換えた所に行ごとの印を付けて、まとめて Markdown にする（行をまたぐ span で HTML が崩れないように）
  const mark = changed.replace(ED, "").split("\n").map((x) => (x.trim() ? "" + x + "" : x)).join("\n");
  return fmtMarked(prefix.replace(ED, "") + mark + suffix.replace(ED, ""));
}
function markSeen() {
  if (state) { seen[sid] = state.rev; setStorage("seenRev", JSON.stringify(seen)); }
}

// ---------------------------------------------------------------- sidebar
function renderSessions() {
  const el = $("sessionList");
  el.innerHTML = "";
  const q = $("sessionFilter").value.trim().toLowerCase();
  const shown = sessions.filter((s) => !q || [s.title, s.summary, s.project, s.last].some((x) => (x || "").toLowerCase().includes(q)));
  // プロジェクトごとにまとめる（プロジェクトなしは最後）
  const groups = new Map();
  for (const s of shown) {
    const p = s.project || "";
    if (!groups.has(p)) groups.set(p, []);
    groups.get(p).push(s);
  }
  const names = [...groups.keys()].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b, "ja"));
  $("projectList").innerHTML = names.filter(Boolean).map((n) => `<option value="${esc(n)}">`).join("");
  for (const name of names) {
    if (names.length > 1 || name) {
      const h = document.createElement("div");
      h.className = "proj-head";
      const isCol = collapsed[name] && !q;
      h.textContent = (isCol ? "▸ " : "▾ ") + (name || "（プロジェクトなし）") + ` (${groups.get(name).length})`;
      h.onclick = () => { collapsed[name] = !collapsed[name]; setStorage("collapsedProjects", JSON.stringify(collapsed)); renderSessions(); };
      el.appendChild(h);
      if (isCol && !groups.get(name).some((s) => s.id === sid)) continue;
    }
    for (const s of groups.get(name)) {
      const d = document.createElement("div");
      d.className = "sess" + (s.id === sid ? " active" : "");
      const unread = s.id !== sid && seen[s.id] !== undefined && s.rev > seen[s.id];
      const badges = (s.ide_title ? `<span title="担当IDE: ${esc(s.ide_title)}">🖥</span>` : "") + (s.waiting ? "⏳" : "") + (s.generating ? "🎨" : "") + (unread ? '<span class="dot"></span>' : "");
      d.innerHTML = `<div class="sess-title">${esc(s.title || "(無題)")}<span class="badges">${badges}<button type="button" class="sess-rename" title="チャンネルの名前を変える">✎</button></span></div>
        <div class="sess-sub">${esc(s.summary || s.last || "")}</div>`;
      d.onclick = () => switchTo(s.id);
      d.querySelector(".sess-rename").onclick = (e) => { e.stopPropagation(); renameChannel(s.id, s.title); };
      d.oncontextmenu = (e) => {
        e.preventDefault();
        if (confirm(`「${s.title || "(無題)"}」を削除？（data/trash に移動）`)) {
          api("/api/session/delete", { sid: s.id }).then(refreshSessions);
        }
      };
      el.appendChild(d);
    }
  }
}

// チャンネルの名前を変える（担当の会話があるなら作業フォルダはいまのまま）
async function renameChannel(id, cur) {
  const title = prompt("チャンネルの新しい名前", cur || "");
  if (title === null || !title.trim() || title.trim() === cur) return;
  try {
    const r = await checked("/api/session/rename", { sid: id, title: title.trim() });
    if (id === sid) {
      $("f_title").value = r.title;  // 設定欄を開いていても、ほかの書きかけはそのまま
      await poll();
    }
    refreshSessions();
    flash("チャンネルの名前を変えました: " + r.title);
  } catch (e) { alert(e.message); }
}
$("title").onclick = () => { if (state) renameChannel(sid, state.setting?.title || ""); };

async function refreshSessions() {
  if (typeof document !== "undefined" && document.hidden) return;
  try {
    const r = await api("/api/sessions");
    sessions = r?.sessions || [];
    if (!sessions.find((s) => s.id === sid) && sessions.length) {
      await switchTo(sessions[0].id);
      return;
    }
    for (const s of sessions) if (seen[s.id] === undefined) seen[s.id] = s.rev;
    renderSessions();
  } catch (e) {
    console.warn("refreshSessions error:", e);
  }
}

async function switchTo(id) {
  sid = id;
  setStorage("sid", sid);
  lastRev = -1;
  state = null;
  rpBgFile = undefined;
  formDirty = false;
  dirtyFields.clear();
  formSid = "";
  newCharRow = false;
  lastSelectedChoiceText = "";
  selectedChoiceMap.clear();
  openChoicesSet.clear();
  $("charList").innerHTML = "";  // 前のチャンネルで編集中だった行は捨てる
  attaching = [];  // 添付は上げたチャンネルの発言にしか付けられない
  renderAttach();
  log.innerHTML = "";
  hideReveal();                                   // 前のチャンネルの操作を残さない（★19）
  if ($("modelSheet")) $("modelSheet").classList.add("hidden");
  if ($("elapsedModal")) $("elapsedModal").classList.add("hidden");
  await poll();
  renderSessions();
  document.body.classList.remove("side-open");
  updateFabState();
  autoResizeInput();
  updateHeaderAndSideStatus();
}

// ---------------------------------------------------------------- settings form
const FIELDS = ["title", "project", "summary", "instructions", "memo", "user_name", "scenario", "first_message", "image_mode", "input_mode", "system_file",
  "ide_folder", "agents_extra"];
const CHAR = { char_name: "name", char_age: "age", char_speech: "speech", char_profile: "profile" };

// 選択肢にない値（ファイルが消えた・古いパス）も消さずに残して見せる
function fillSelect(el, items, value, empty = "（なし）") {
  el.innerHTML = "";
  const add = (v, label) => { const o = document.createElement("option"); o.value = v; o.textContent = label; el.appendChild(o); };
  if (empty !== null) add("", empty);
  const groups = {};
  for (const it of items) {
    if (!it.group) { add(it.value, it.label); continue; }
    if (!groups[it.group]) { groups[it.group] = document.createElement("optgroup"); groups[it.group].label = it.group; el.appendChild(groups[it.group]); }
    const o = document.createElement("option");
    o.value = it.value;
    o.textContent = it.label;
    groups[it.group].appendChild(o);
  }
  if (value && !items.some((it) => it.value === value)) add(value, "⚠ " + value.split(/[\\/]/).pop() + "（一覧にない）");
  el.value = value || "";
}

function fillFileOptions(st) {
  fillSelect($("f_system_file"), sysFiles.map((f) => ({ value: f, label: f })), st.system_file);
  const refs = st.ref_files || [];
  const list = $("refList");
  list.innerHTML = "";
  const all = [...sysFiles, ...refs.filter((r) => !sysFiles.includes(r))];
  if (!all.length) list.innerHTML = '<span class="muted">ファイルがありません（上の「変更…」でフォルダを確認）</span>';
  for (const f of all) {
    const l = document.createElement("label");
    l.className = "check";
    l.innerHTML = `<input type="checkbox" value="${esc(f)}"${refs.includes(f) ? " checked" : ""}> ${esc(f)}${sysFiles.includes(f) ? "" : " ⚠一覧にない"}`;
    l.querySelector("input").onchange = () => (formDirty = true);
    list.appendChild(l);
  }
  fillFolderFiles(strList(st.folder_files));
  $("f_files_extra").value = st.files_extra || "";
}

// 「読み込むファイル」は登録フォルダの中から複数選べる。選んだものを先頭に、選んだ順で並べる
const strList = (v) => (Array.isArray(v) ? v : []).filter((x) => typeof x === "string" && x);
function fillFolderFiles(selected) {
  const list = $("folderFileList");
  const opts = folderOpts.flatMap((f) => f.files.map((x) => ({ path: x.path, label: `${f.name}/${x.label}` })));
  const items = [...selected.map((p) => opts.find((o) => o.path === p) || { path: p, label: p.split(/[\\/]/).pop() + " ⚠一覧にない", gone: true }),
                 ...opts.filter((o) => !selected.includes(o.path))];
  list.innerHTML = items.length ? "" : `<span class="muted">${folderOpts.length
    ? "ファイルがありません（登録したフォルダに .md .txt がない）"
    : "フォルダがまだありません（上の「＋ フォルダを追加」で選ぶ）"}</span>`;
  for (const o of items) {
    const l = document.createElement("label");
    l.className = "check";
    l.title = o.path;
    l.innerHTML = `<input type="checkbox" value="${esc(o.path)}"${selected.includes(o.path) ? " checked" : ""}> ${esc(o.label)}`;
    l.querySelector("input").onchange = () => (formDirty = true);
    list.appendChild(l);
  }
  filterFolderFiles();
}
function readFolderFiles() {
  return [...$("folderFileList").querySelectorAll("input:checked")].map((x) => x.value);
}
function filterFolderFiles() {
  const q = $("folderFileFilter").value.trim().toLowerCase();
  for (const l of $("folderFileList").querySelectorAll("label.check")) {
    l.hidden = !!q && !l.querySelector("input").checked && !l.textContent.toLowerCase().includes(q);
  }
}
$("folderFileFilter").addEventListener("input", filterFolderFiles);

// 読み込むファイルのフォルダ（全チャンネル共通。＋でいくつでも。足す・外すとすぐ保存）
function fillFolderRows(list) {
  $("folderRows").innerHTML = "";
  for (const path of list) {
    const info = folderOpts.find((f) => f.folder.toLowerCase() === path.replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase());
    const row = document.createElement("div");
    row.className = "folder-row";
    const st = !info ? "" : info.ok ? `${info.files.length}${info.more ? "件以上" : "件"}` : "見つからない";
    row.innerHTML = `<code class="folder-path" title="${esc(path)}">${esc(path)}</code>
      <span class="st${info && !info.ok ? " ng" : ""}">${esc(st)}</span><button type="button" class="sub">外す</button>`;
    row.querySelector("button").onclick = () => saveFolders(list.filter((x) => x !== path));
    $("folderRows").appendChild(row);
  }
}
$("folderAdd").onclick = async () => {
  const path = await pickFolder();
  if (!path) return;
  const list = strList(meta.app?.file_folders);
  if (list.some((x) => x.toLowerCase() === path.toLowerCase())) return flash("そのフォルダはもう入っています");
  await saveFolders([...list, path]);
};
async function saveFolders(list) {
  try { await checked("/api/app-settings", { file_folders: list }); await reloadFileLists(); }
  catch (e) { alert(e.message); }
}
$("sysRootPick").onclick = async () => {
  const path = await pickFolder(meta.app?.system_root || "");
  if (!path) return;
  try { await checked("/api/app-settings", { system_root: path }); await reloadFileLists(); }
  catch (e) { alert(e.message); }
};

// PC の画面に「フォルダーの選択」ウィンドウを出す（やめたら ""）。出せないとき（スマホから・tkinter がない）は場所を打ってもらう
async function pickFolder(initial = "") {
  flash("PC の画面にフォルダを選ぶウィンドウが出ます");
  const r = await api("/api/pick-folder", { initial }).catch((e) => ({ error: e.message }));
  if (!r.error) return r.path || "";
  if (r.busy) { alert(r.error); return ""; }
  return (prompt(r.error + "\nフォルダの場所を入力してください（例: D:\\notes\\characters）", initial) || "").trim();
}

// フォルダを変えたあと一覧を読み直す。チェック中でまだ保存していない選択はそのまま
async function reloadFileLists() {
  const keep = state && { ...state.setting, system_file: $("f_system_file").value, files_extra: $("f_files_extra").value,
    ref_files: [...$("refList").querySelectorAll("input:checked")].map((x) => x.value), folder_files: readFolderFiles() };
  await loadOptions();
  if (keep) fillFileOptions(keep);
}

function fillForm(st, force = false) {
  const canSet = (id) => force || !dirtyFields.has(id);
  for (const k of FIELDS) {
    const el = $("f_" + k);
    if (el && el.tagName !== "SELECT" && canSet("f_" + k)) el.value = st[k] || "";
  }
  if ($("f_image_mode") && canSet("f_image_mode")) $("f_image_mode").value = st.image_mode || "always";
  if ($("f_input_mode") && canSet("f_input_mode")) $("f_input_mode").value = st.input_mode || "diff";
  if ($("f_direct_send") && canSet("f_direct_send")) $("f_direct_send").checked = !!st.direct_send;
  if ($("f_agents_md") && canSet("f_agents_md")) $("f_agents_md").checked = !!st.agents_md;
  if ($("f_ide_revert") && canSet("f_ide_revert")) $("f_ide_revert").checked = st.ide_revert !== false;
  if ($("f_rules_every") && canSet("f_rules_every")) $("f_rules_every").checked = !!st.rules_every;
  if ($("f_rp_state") && canSet("f_rp_state")) $("f_rp_state").checked = !!st.rp_state;
  const suggestEl = $("f_suggest_choices") || $("setting-suggest-choices");
  if (suggestEl && canSet(suggestEl.id)) suggestEl.checked = st.suggest_choices !== false;
  const sharedEl = $("f_shared_memories");
  if (sharedEl && canSet("f_shared_memories")) sharedEl.checked = !!st.shared_memories;
  for (const [id, k] of Object.entries(CHAR)) {
    const el = $("f_" + id);
    if (el && canSet("f_" + id)) el.value = (st.character || {})[k] || "";
  }
  if ($("f_image") && canSet("f_image")) $("f_image").value = JSON.stringify(st.image || {}, null, 2);

  const et = st.elapsed_time || {};
  if ($("f_elapsed_day") && canSet("f_elapsed_day")) $("f_elapsed_day").value = et.day || 1;
  if ($("f_elapsed_period") && canSet("f_elapsed_period")) $("f_elapsed_period").value = et.period || "午前";

  fillFileOptions(st);
}
function readForm() {
  const st = JSON.parse(JSON.stringify(state.setting));
  for (const k of FIELDS) {
    if ($("f_" + k)) st[k] = $("f_" + k).value;
  }
  if ($("f_agents_md")) st.agents_md = $("f_agents_md").checked;
  if ($("f_direct_send")) st.direct_send = $("f_direct_send").checked;
  if ($("f_ide_revert")) st.ide_revert = $("f_ide_revert").checked;
  if ($("f_rules_every")) st.rules_every = $("f_rules_every").checked;
  if ($("f_rp_state")) st.rp_state = $("f_rp_state").checked;
  st.suggest_choices = ($("f_suggest_choices") || $("setting-suggest-choices"))?.checked ?? true;
  st.shared_memories = $("f_shared_memories")?.checked ?? false;
  st.character = st.character || {};
  for (const [id, k] of Object.entries(CHAR)) {
    if ($("f_" + id)) st.character[k] = $("f_" + id).value;
  }
  if ($("f_image")) st.image = JSON.parse($("f_image").value);
  if ($("refList")) st.ref_files = [...$("refList").querySelectorAll("input:checked")].map((x) => x.value);
  st.folder_files = readFolderFiles();
  if ($("f_files_extra")) st.files_extra = $("f_files_extra").value;

  st.elapsed_time = st.elapsed_time || {};
  if ($("f_elapsed_day")) st.elapsed_time.day = parseInt($("f_elapsed_day").value, 10) || 1;
  if ($("f_elapsed_period")) st.elapsed_time.period = $("f_elapsed_period").value || "午前";

  return st;
}
document.querySelectorAll("#settingsPanel input, #settingsPanel textarea, #settingsPanel select").forEach((el) =>
  el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
    if (!el.closest("#appSettings") && !el.closest("#charBox") && el.id !== "presetSelect" && el.id !== "folderFileFilter" && el.id !== "ideModel") {
      formDirty = true;
      if (el.id) dirtyFields.add(el.id);
    }
  }));

// ---------------------------------------------------------------- 設定ファイル・読み込むファイル
async function loadOptions() {
  try {
    const [m, f, o, p] = await Promise.all([api("/api/meta"), api("/api/files"), api("/api/folder-files"), api("/api/file-presets")]);
    meta = m;
    sysFiles = f.files || [];
    folderOpts = o.folders || [];
    presets = p.presets || [];
  } catch (e) { flash("一覧の読み込みに失敗: " + e.message); }
  $("sysRoot").textContent = $("sysRoot").title = meta.app?.system_root || "";
  fillFolderRows(strList(meta.app?.file_folders));
  $("app_new_channel_ide").checked = meta.app?.new_channel_ide !== false;
  showPhone(meta.phone || {});
  fillSelect($("presetSelect"), presets.map((x) => ({ value: x.id, label: x.name })), $("presetSelect").value, "プリセット…");
  if (state && !formDirty) fillFileOptions(state.setting);
  await loadCharPresets();
}

// スマホで開くアドレスと QR コード（start_mobile.bat で起動したときだけサーバーが返す）
function showPhone(ph) {
  $("phoneOn").hidden = !ph.url;
  $("phoneOff").hidden = !!ph.url;
  $("phoneUrl").textContent = ph.url || "";
  $("phoneQr").hidden = !ph.qr;
  if (ph.qr) $("phoneQr").src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(ph.qr);
  updatePushOrigin(ph.url);
}
$("phoneCopy").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("phoneUrl").textContent);
    flash("アドレスをコピーしました");
  } catch {  // http のスマホからはコピーできないので、選ぶだけにする
    getSelection().selectAllChildren($("phoneUrl"));
    flash("コピーできないので、選んだアドレスを長押しでコピーしてください");
  }
};

// ---------------------------------------------------------------- Web Push 通知
let pushSubscription = null;
const pushSupported = typeof window !== "undefined" && ("serviceWorker" in navigator) && ("PushManager" in window) && ("Notification" in window);

function urlB64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

// いま画面中央にある返信を基準に、前/次のロールプレイ返信の本文を画面中央へ即座に寄せる。
// smooth にせず scrollTop を直接代入するので、押した瞬間に「ピタッ」と止まる。
function jumpToAssistantReply(dir) {
  const bubbles = Array.from(log.querySelectorAll(".msg-wrap.assistant .msg.assistant"))
    .filter((b) => (b.textContent || "").trim());
  if (!bubbles.length) return;

  const logRect = log.getBoundingClientRect();
  const centerY = logRect.top + log.clientHeight / 2;
  const maxScroll = Math.max(0, log.scrollHeight - log.clientHeight);
  const scrollable = log.scrollHeight > log.clientHeight + 1;
  // 最上段/最下段では、中央判定ではなく「今見ている端」を基準にする。
  // 端では最後の返信が中央より下に来ることがあり、中央判定だと1つ飛ばすため。
  const atBottom = scrollable && (log.scrollHeight - log.scrollTop - log.clientHeight <= 100);
  const atTop = scrollable && (log.scrollTop <= 60);
  const wraps = Array.from(log.querySelectorAll(".msg-wrap"));
  let target = -1;

  if (dir < 0 && atBottom) {
    // 最下段の「今」は、最後のメッセージ（返信ならその返信、ユーザー発言ならその直前の返信）。
    const lastWrap = wraps[wraps.length - 1];
    const lastBubble = lastWrap ? lastWrap.querySelector(".msg.assistant") : null;
    const lastIsAssistantText = !!(lastWrap && lastWrap.classList.contains("assistant") && lastBubble && (lastBubble.textContent || "").trim());
    target = lastIsAssistantText ? bubbles.length - 2 : bubbles.length - 1;
    if (target < 0) target = 0;
  } else if (dir > 0 && atTop) {
    // 最上段の「今」は最初のメッセージ。返信ならその次、ユーザー発言なら最初の返信。
    const firstWrap = wraps[0];
    const firstBubble = firstWrap ? firstWrap.querySelector(".msg.assistant") : null;
    const firstIsAssistantText = !!(firstWrap && firstWrap.classList.contains("assistant") && firstBubble && (firstBubble.textContent || "").trim());
    target = firstIsAssistantText ? 1 : 0;
    target = Math.min(target, bubbles.length - 1);
  } else if (dir > 0 && atBottom) {
    // 最下段の「次」は最新の返信。既に最下段なので、中央判定で手前の返信に
    // 戻ってしまわないよう、最後の本文へ固定する。
    target = bubbles.length - 1;
  } else if (dir < 0) {
    // 前へ: 中央より上にある「直前の返信」。中央に返信があれば、その1つ前
    for (let i = 0; i < bubbles.length; i++) {
      const r = bubbles[i].getBoundingClientRect();
      if (r.top <= centerY && r.bottom >= centerY) { target = i - 1; break; }
      if (r.bottom < centerY) target = i;
      else break;
    }
    if (target < 0) target = 0;   // 最初の返信より上にいるときは、最初の返信へ
  } else {
    // 次へ: 中央に返信があればその1つ後ろ。無ければ中央より下で最初の返信
    for (let i = 0; i < bubbles.length; i++) {
      const r = bubbles[i].getBoundingClientRect();
      if (r.top <= centerY && r.bottom >= centerY) { target = i + 1; break; }
      if (r.top > centerY) { target = i; break; }
    }
    if (target < 0) target = bubbles.length - 1;   // 最後の返信より下なら最後へ
    target = Math.min(target, bubbles.length - 1);
  }

  const el = bubbles[target];
  const r = el.getBoundingClientRect();
  const top = log.scrollTop + (r.top - logRect.top) - (log.clientHeight - r.height) / 2;
  log.scrollTop = Math.min(Math.max(0, top), maxScroll);
  updateRpBg();   // 背景表示も、移動先の返信に合わせて即座に切り替える
}
function jumpToPrevAssistantReply() { jumpToAssistantReply(-1); }
function jumpToNextAssistantReply() { jumpToAssistantReply(1); }

function scrollToMessage(messageId, maxRetries = 10) {
  if (!messageId) return;
  const tryScroll = (retriesLeft) => {
    const el = document.getElementById("msg-" + messageId);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("highlight-msg");
      setTimeout(() => el.classList.remove("highlight-msg"), 3000);
      const img = el.querySelector("img");
      if (img && !img.complete) {
        img.addEventListener("load", () => {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }, { once: true });
      }
    } else if (retriesLeft > 0) {
      setTimeout(() => tryScroll(retriesLeft - 1), 200);
    }
  };
  tryScroll(maxRetries);
}

function updatePushOrigin(url) {
  const el = $("pushOriginUrl");
  if (!el) return;
  const origin = url ? url.replace(/\/$/, "") : window.location.origin;
  el.textContent = origin;
}

async function updatePushStatus() {
  if (!$("pushStatusText")) return;
  updatePushOrigin(meta.phone?.url);
  if (!pushSupported) {
    if (typeof window !== "undefined" && !window.isSecureContext) {
      $("pushStatusText").textContent = "通知: Chrome の例外設定（下記手順）が必要です";
    } else {
      $("pushStatusText").textContent = "通知: この環境では非対応です";
    }
    if ($("pushToggleBtn")) $("pushToggleBtn").disabled = true;
    if ($("pushTestBtn")) $("pushTestBtn").disabled = true;
    return;
  }
  if (Notification.permission === "denied") {
    $("pushStatusText").textContent = "通知: ブラウザ設定でブロックされています";
    if ($("pushToggleBtn")) $("pushToggleBtn").textContent = "通知を有効化";
    if ($("pushTestBtn")) $("pushTestBtn").disabled = true;
    return;
  }
  try {
    const reg = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise((_, reject) => setTimeout(() => reject(new Error("SW ready timeout")), 3000))
    ]);
    const sub = await reg.pushManager.getSubscription();
    pushSubscription = sub;
    if (sub) {
      $("pushStatusText").textContent = "通知: 有効 (購読中)";
      if ($("pushToggleBtn")) $("pushToggleBtn").textContent = "通知を停止";
      if ($("pushTestBtn")) $("pushTestBtn").disabled = false;
    } else {
      $("pushStatusText").textContent = "通知: 未設定（オフ）";
      if ($("pushToggleBtn")) $("pushToggleBtn").textContent = "通知を有効化";
      if ($("pushTestBtn")) $("pushTestBtn").disabled = true;
    }
  } catch (err) {
    $("pushStatusText").textContent = "通知: 未設定";
    if ($("pushToggleBtn")) $("pushToggleBtn").textContent = "通知を有効化";
    if ($("pushTestBtn")) $("pushTestBtn").disabled = true;
  }
}

function initPushNotifications() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((err) => {
      console.warn("[SW] register error:", err);
    });
    navigator.serviceWorker.addEventListener("message", async (event) => {
      if (event.data && event.data.type === "NAVIGATE_TO") {
        const { sessionId, messageId } = event.data;
        if (typeof closeLightbox === "function") closeLightbox();
        document.body.classList.remove("side-open");
        if ($("promptModal") && !$("promptModal").classList.contains("hidden")) {
          $("promptModal").classList.add("hidden");
        }
        if ($("settingsPanel") && !$("settingsPanel").classList.contains("hidden")) {
          $("settingsPanel").classList.add("hidden");
        }
        updateFabState();
        if (sessionId && sessionId !== sid) {
          await switchTo(sessionId);
        } else {
          await poll();
        }
        if (messageId) {
          scrollToMessage(messageId);
        }
      }
    });
  }

  if ($("pushToggleBtn")) {
    $("pushToggleBtn").onclick = async () => {
      const btn = $("pushToggleBtn");
      btn.disabled = true;
      try {
        if (pushSubscription) {
          const ep = pushSubscription.endpoint;
          await api("/api/push/subscribe", { unsubscribe: true, endpoint: ep });
          await pushSubscription.unsubscribe();
          pushSubscription = null;
          flash("通知を無効化しました");
        } else {
          if (typeof window !== "undefined" && !window.isSecureContext) {
            alert("LAN接続では、下記手順の Chrome 設定（flags）を先に有効化してください。");
            return;
          }
          if (Notification.permission === "denied") {
            alert("通知の権限が拒否されています。ブラウザの設定（サイト設定）で通知を許可してください。");
            return;
          }
          const perm = await Notification.requestPermission();
          if (perm !== "granted") {
            flash("通知の権限が得られませんでした");
            return;
          }
          const keyRes = await api("/api/push/vapid_key");
          if (!keyRes.ok || !keyRes.publicKey) {
            throw new Error(keyRes.error || "VAPIDキーの取得に失敗しました");
          }
          const reg = await Promise.race([
            navigator.serviceWorker.ready,
            new Promise((_, reject) => setTimeout(() => reject(new Error("Service Worker の準備がタイムアウトしました。ページを再読み込みしてください。")), 5000))
          ]);
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlB64ToUint8Array(keyRes.publicKey)
          });
          const saveRes = await api("/api/push/subscribe", { subscription: sub.toJSON() });
          if (!saveRes || !saveRes.ok) {
            throw new Error(saveRes?.error || "サーバーへの購読登録に失敗しました");
          }
          pushSubscription = sub;
          flash("通知を有効化しました");
        }
      } catch (e) {
        alert("通知設定エラー: " + e.message);
      } finally {
        btn.disabled = false;
        await updatePushStatus();
      }
    };
  }

  if ($("pushTestBtn")) {
    $("pushTestBtn").onclick = async () => {
      const btn = $("pushTestBtn");
      btn.disabled = true;
      try {
        const res = await api("/api/push/test", {});
        if (res.ok) {
          flash(`テスト通知を送信しました（${res.sent}件）`);
        } else {
          flash("テスト通知の送信に失敗: " + (res.error || ""));
        }
      } catch (e) {
        flash("送信エラー: " + e.message);
      } finally {
        btn.disabled = false;
      }
    };
  }

  if ($("pushFlagsCopy")) {
    $("pushFlagsCopy").onclick = async () => {
      const text = "chrome://flags/#unsafely-treat-insecure-origin-as-secure";
      try {
        await navigator.clipboard.writeText(text);
        flash("設定URLをコピーしました");
      } catch {
        flash("コピーに失敗しました。手動で入力してください");
      }
    };
  }

  if ($("pushOriginCopy")) {
    $("pushOriginCopy").onclick = async () => {
      const text = $("pushOriginUrl").textContent;
      try {
        await navigator.clipboard.writeText(text);
        flash("サーバーアドレスをコピーしました");
      } catch {
        getSelection().selectAllChildren($("pushOriginUrl"));
        flash("長押ししてコピーしてください");
      }
    };
  }
}


// ---------------------------------------------------------------- 担当IDE（Antigravity の会話）
async function loadIdeSessions() {
  try {
    const r = await checked("/api/ide/sessions");
    ideSessions = r.sessions || [];
    ideNewDir = r.new_dir || "";
    $("ideNew").title = `Antigravity に新しい会話を作って担当にする（フォルダ: ${ideNewDir}。何も送らない。最初の入力で送られる）`;
  } catch (e) {
    ideSessions = [];
    $("ideState").textContent = "IDEの会話一覧を読めません: " + e.message;
  }
  renderIde();
  loadAgents();
}

// AGENTS.md: 作業フォルダの場所、書き出せたか、担当の会話が読んでいるか
let agentsFolder = "";
async function loadAgents() {
  if (!sid) return;
  const want = sid;
  let a;
  try { a = await checked("/api/agents?sid=" + encodeURIComponent(sid)); }
  catch (e) { $("agentsState").innerHTML = `<span class="ide-off">${esc(e.message)}</span>`; return; }
  if (want !== sid) return;
  agentsFolder = a.folder;
  $("f_ide_folder").placeholder = a.fixed ? "" : a.folder.split(/[\\/]/).pop() + "（未保存の既定。保存か新規会話で決まる）";
  $("agentsPreview").textContent = a.text || a.error || "";
  const ide = state?.setting?.ide_session;
  const lines = [];
  if (!a.enabled) {
    lines.push(`<span class="muted">書き出していない（オンにして保存すると ${esc(a.path)} に書く）</span>`);
  } else {
    const s = a.state;
    if (s && !s.ok) lines.push(`<span class="ide-off">✖ ${esc(s.msg)}</span>`);
    else if (s) lines.push(`<span class="ide-on">✔ ${esc(s.path)}（${esc(s.msg)} ${esc(s.ts.slice(11))}）</span>`);
    else lines.push(`<span class="muted">${esc(a.path)}（次の保存・入力の受け取りで書く）</span>`);
    if (a.error) lines.push(`<span class="ide-off">${esc(a.error)}</span>`);
    if (ide && a.in_use) lines.push('<span class="ide-on">担当の会話はこのフォルダなので毎ターン読み込まれる（System は wait_input の表示から外す）</span>');
    else if (ide && a.workspace) lines.push(`<span class="ide-off">⚠ 担当の会話の作業フォルダは ${esc(a.workspace)} なので、この AGENTS.md は読まれない。下の「移す」で会話ごとこのフォルダへ引っ越すか、「新規会話」でこのフォルダの会話を作ると効く（それまで System は今までどおり wait_input で見せる）</span>`);
  }
  $("agentsState").innerHTML = lines.join("<br>");
  let mv;
  try { mv = await checked("/api/ide/move"); } catch (e) { $("moveBox").innerHTML = ""; return; }
  if (want !== sid) return;
  renderMoves(mv, a.enabled && ide && a.workspace && !a.in_use);
}

// 会話の引っ越し: 担当の会話の作業フォルダをチャンネルのフォルダへ（Antigravity の再起動が要るので予約してまとめて移す）
function renderMoves(mv, canMove) {
  const ide = state?.setting?.ide_session;
  const pend = mv.pending || [];
  const mine = pend.find((x) => x.ide === ide);
  const st = mv.state || {};
  const box = $("moveBox");
  const parts = [];
  if (st.running) parts.push(`<span class="ide-on">⏳ ${esc(st.msg)}</span>`);
  else if (st.msg) {
    const res = (st.results || []).map((r) => `${r.ok ? "✔" : "✖"} ${esc(r.title || r.ide.slice(0, 8))}: ${esc(r.msg)}`).join("<br>");
    parts.push(`<span class="muted">前回の引っ越し ${esc((st.ts || "").slice(11))}: ${esc(st.msg)}${res ? "<br>" + res : ""}</span>`);
  }
  if (mine) parts.push(`<span class="ide-off">この会話は ${esc(mine.folder)} へ移す予約中</span><button type="button" class="sub" id="moveCancel">予約を取り消す</button>`);
  else if (canMove && !st.running) parts.push('<button type="button" class="sub" id="moveReserve" title="履歴はそのまま、会話の作業フォルダだけ変える。Antigravity の再起動が要るので、ここでは予約だけ">この会話をチャンネルのフォルダへ移す（予約）</button>');
  if (pend.length && !st.running) {
    parts.push(`<button type="button" id="moveRun" title="${esc(pend.map((x) => `${x.channel}: ${x.title} → ${x.folder}`).join("\n"))}">Antigravity を再起動して移す（${pend.length}件）</button>`);
  }
  box.innerHTML = parts.join(" ");
  if ($("moveCancel")) $("moveCancel").onclick = async () => {
    try { renderMoves(await checked("/api/ide/move", { sid, cancel: true }), canMove); } catch (e) { alert(e.message); }
  };
  if ($("moveReserve")) $("moveReserve").onclick = async () => {
    if (!confirm(`担当の会話の作業フォルダを\n${agentsFolder}\nへ移す予約をする？\n\n・履歴はそのまま。移したあとはこのフォルダの AGENTS.md を毎ターン読む\n・会話からは元のフォルダのファイルが「作業フォルダ」として見えなくなる\n・実際に移すのは「Antigravity を再起動して移す」を押したとき`)) return;
    try {
      const r = await checked("/api/ide/move", { sid });
      if (r.others?.length) alert(`この会話はほかのチャンネルでも担当になっています:\n${r.others.join("\n")}\n移すと、その会話はこのチャンネルの AGENTS.md を読むようになります`);
      renderMoves(r, canMove);
    } catch (e) { alert(e.message); }
  };
  if ($("moveRun")) $("moveRun").onclick = () => runMoves(pend, false);
}

async function runMoves(pend, force) {
  if (!force && !confirm(`Antigravity を一度閉じて、次の会話の作業フォルダを書き換え、起動し直す:\n\n${pend.map((x) => `・${x.title}（${x.channel}）\n   → ${x.folder}`).join("\n")}\n\nAntigravity で開いているほかの会話も一度閉じられる。続ける？`)) return;
  let r;
  try { r = await checked("/api/ide/move-run", { force }); } catch (e) { alert(e.message); return; }
  if (r.busy) {
    if (confirm(`応答中の会話がある:\n${r.busy.join("\n")}\n\n止めて（Antigravity を閉じて）移す？`)) return runMoves(pend, true);
    return;
  }
  flash("Antigravity を閉じて会話を移しています…（数十秒）");
  for (let i = 0; i < 90; i++) {
    await new Promise((ok) => setTimeout(ok, 2000));
    let mv;
    try { mv = await checked("/api/ide/move"); } catch { continue; }
    if (!mv.state?.running) {
      flash("引っ越し: " + (mv.state?.msg || "終わった"));
      loadIdeSessions();
      return;
    }
  }
}

let idePickTarget = "main";  // "main" | "choices"

function renderIde() {
  if (!state) return;
  const ide = state.setting?.ide_session || "";
  const cur = ideSessions.find((x) => x.id === ide);
  $("ideCurrent").innerHTML = ide
    ? `${esc(cur?.title && cur.title !== "(無題)" ? cur.title : state.setting.ide_title || "(無題)")} <span class="muted">${esc(ide)}${cur?.workspace ? " ・ " + esc(cur.workspace) : ""}</span>`
    : state.setting.ide_auto
      ? "まだなし（Antigravity が起動していれば、数秒で新規会話が付きます。起動していなければ、起動したら付きます）"
      : "指定なし（--ide なしの wait_input.py が受け取る）";
  $("ideClear").disabled = $("ideFork").disabled = $("ideRename").disabled = !ide;
  $("idePokeNow").disabled = !ide;

  const choicesIde = state.setting?.choices_ide_session || "";
  const curChoices = ideSessions.find((x) => x.id === choicesIde);
  const choicesEl = $("choicesIdeCurrent");
  if (choicesEl) {
    choicesEl.innerHTML = choicesIde
      ? `${esc(curChoices?.title && curChoices.title !== "(無題)" ? curChoices.title : state.setting.choices_ide_title || "(無題)")} <span class="muted">${esc(choicesIde)}${curChoices?.workspace ? " ・ " + esc(curChoices.workspace) : ""}</span>`
      : "指定なし（メインの会話で順番に生成）";
  }
  if ($("choicesIdeClear")) $("choicesIdeClear").disabled = !choicesIde;
  if ($("choicesIdeRename")) $("choicesIdeRename").disabled = !choicesIde;
  if ($("choicesIdePokeNow")) $("choicesIdePokeNow").disabled = !choicesIde;
  const choicesState = $("choicesIdeState");
  if (choicesState) {
    if (choicesIde) {
      choicesState.innerHTML = curChoices?.waiting
        ? '<span class="ide-on">● この会話で wait_input.py が待機中です</span>'
        : `<span class="ide-off">○ 未待機（python wait_input.py --ide ${esc(choicesIde)}）</span>`;
    } else {
      choicesState.textContent = "";
    }
  }
  const cpk = curChoices?.poke;
  const choicesPokeEl = $("choicesPokeState");
  if (choicesPokeEl) {
    choicesPokeEl.innerHTML = choicesIde && cpk
      ? `<span class="${cpk.ok ? "ide-on" : "ide-off"}">最後の送信 ${esc(cpk.ts.slice(11))}: ${esc(cpk.msg)}</span>` : "";
  }

  renderModels();
  const pk = cur?.poke;
  $("pokeState").innerHTML = ide && pk
    ? `<span class="${pk.ok ? "ide-on" : "ide-off"}">最後の送信 ${esc(pk.ts.slice(11))}: ${esc(pk.msg)}</span>` : "";
  if (!ide) {
    if (ideSessions.length) $("ideState").textContent = "";
    return;
  }
  $("ideState").innerHTML = cur?.waiting
    ? '<span class="ide-on">● この会話で wait_input.py が待機中です</span>'
    : `<span class="ide-off">○ 未待機（python wait_input.py --ide ${esc(ide)}）</span>`;
}

async function assignIde(ide, title, target = idePickTarget) {
  if (ide) {
    const mainIde = state?.setting?.ide_session;
    const choicesIde = state?.setting?.choices_ide_session;
    if (target === "choices" && mainIde && ide === mainIde) {
      alert("メイン担当と同じ会話が選ばれています。並列で高速化するには別の会話を選ぶか「新規会話」を押してください。");
      return false;
    } else if (target === "main" && choicesIde && ide === choicesIde) {
      alert("選択肢担当と同じ会話が選ばれています。並列で高速化するには別の会話を選んでください。");
      return false;
    }
  }
  try {
    let r = await checked("/api/ide/assign", { sid, ide, title, target });
    if (r.conflict) {  // ほかのチャンネルがもうこの会話を担当にしている
      if (!confirm(`この会話は、すでに次のチャンネルにつながっています:\n\n${r.conflict.map((t) => "・" + t).join("\n")}\n\n` +
        "このチャンネルもつなぐと、どのチャンネルの入力もこの会話に届き、同じ会話の中で混ざります（応答も遅くなる）。\nそれでもつなぐ？")) return false;
      r = await checked("/api/ide/assign", { sid, ide, title, target, force: true });
    }
    await poll(); refreshSessions(); return true;
  }
  catch (e) { alert(e.message); renderIde(); return false; }
}

// 会話を選ぶ: フォルダ（ワークスペース）ごとにまとめて全部見せる。検索は全フォルダから
let ideFolder = null;  // 選んでいるフォルダ（小文字）。"" は全部
const folderKey = (x) => (x.workspace || "").toLowerCase();
function renderIdePicker() {
  const q = $("ideQuery").value.trim().toLowerCase();
  const ide = (idePickTarget === "choices" ? state?.setting?.choices_ide_session : state?.setting?.ide_session) || "";
  const hit = ideSessions.filter((x) => !q || [x.title, x.workspace, x.id].some((v) => (v || "").toLowerCase().includes(q)));
  const folders = new Map();  // 新しい会話があるフォルダが上（ideSessions は新しい順）
  for (const x of hit) {
    const k = folderKey(x);
    if (!folders.has(k)) folders.set(k, { label: x.workspace || "（フォルダなし）", n: 0 });
    folders.get(k).n++;
  }
  if (ideFolder === null) {  // 開いたときは担当の会話のフォルダ（なければ全部）
    const c = ideSessions.find((x) => x.id === ide);
    ideFolder = c ? folderKey(c) : "";
  }
  if (ideFolder && !folders.has(ideFolder)) ideFolder = "";
  const newKey = (ideNewDir || "").replace(/\\/g, "/").toLowerCase();
  const fl = $("ideFolders");
  fl.innerHTML = "";
  const addFolder = (k, label, n) => {
    const d = document.createElement("div");
    d.className = "ide-folder" + (k === ideFolder ? " selected" : "");
    d.innerHTML = `${k && k === newKey ? "★ " : ""}${esc(label)} <span class="n">(${n})</span>`;
    d.title = k && k === newKey ? "「新規会話」で作るフォルダ" : label;
    d.onclick = () => { ideFolder = k; renderIdePicker(); };
    fl.appendChild(d);
  };
  addFolder("", q ? "検索結果すべて" : "すべて（新しい順）", hit.length);
  for (const [k, f] of folders) addFolder(k, f.label, f.n);
  const list = hit.filter((x) => !ideFolder || folderKey(x) === ideFolder);
  const cl = $("ideConvs");
  cl.innerHTML = list.length ? "" : `<span class="muted">${ideSessions.length ? "会話がありません" : esc($("ideState").textContent || "会話がありません")}</span>`;
  for (const x of list.slice(0, 300)) {
    const d = document.createElement("div");
    d.className = "ide-conv" + (x.id === ide ? " current" : "");
    d.innerHTML = `${x.steps ? '<button type="button" class="sub fork-btn" title="この会話を途中の発言から分岐する">分岐…</button>' : ""}
      <button type="button" class="sub fork-btn rename-btn" title="この会話の名前を変える">名前…</button>
      <div class="t">${x.waiting ? "● " : ""}${x.id === ide ? "✔ " : ""}${esc(x.title)}</div>
      <div class="s">${esc((x.updated || "").slice(0, 16))} ・ ${x.steps ?? "?"}ステップ ・ ${esc(x.id.slice(0, 8))}${ideFolder ? "" : " ・ " + esc(x.workspace || "")}</div>`;
    d.onclick = async () => { if (await assignIde(x.id, x.title, idePickTarget)) { $("ideModal").classList.add("hidden"); updateFabState(); } };
    const fb = d.querySelector(".fork-btn:not(.rename-btn)");
    if (fb) fb.onclick = (e) => { e.stopPropagation(); openFork(x.id, x.title); };
    d.querySelector(".rename-btn").onclick = async (e) => {
      e.stopPropagation();
      if (await renameIde(x.id, x.title)) renderIdePicker();
    };
    cl.appendChild(d);
  }
  if (list.length > 300) cl.insertAdjacentHTML("beforeend", `<span class="muted">ほか ${list.length - 300} 件（検索で絞り込む）</span>`);
}
async function openIdeModal(target = "main") {
  idePickTarget = target;
  ideFolder = null;
  $("ideQuery").value = "";
  $("ideIdInput").value = "";
  $("ideFolders").innerHTML = "";
  $("ideConvs").innerHTML = '<span class="muted">読み込み中…</span>';
  $("ideModal").classList.remove("hidden");
  updateFabState();
  await loadIdeSessions();
  renderIdePicker();
}
$("idePick").onclick = () => openIdeModal("main");
if ($("choicesIdePick")) $("choicesIdePick").onclick = () => openIdeModal("choices");
$("ideQuery").addEventListener("input", () => { ideFolder = ""; renderIdePicker(); });
$("ideModalClose").onclick = () => { $("ideModal").classList.add("hidden"); updateFabState(); };
$("ideModal").addEventListener("click", (e) => { if (e.target === $("ideModal")) { $("ideModal").classList.add("hidden"); updateFabState(); } });
$("ideRefresh").onclick = async () => { await loadIdeSessions(); renderIdePicker(); };
$("ideIdSet").onclick = async () => {
  const id = $("ideIdInput").value.trim();
  if (!/^[0-9a-zA-Z-]+$/.test(id)) return alert("会話IDは英数字とハイフンだけ");
  const x = ideSessions.find((s) => s.id === id || (id.length >= 8 && s.id.startsWith(id)));
  if (!x && !confirm(`一覧にない会話IDです。このまま担当にする？\n${id}`)) return;
  if (await assignIde(x ? x.id : id, x ? x.title : "", idePickTarget)) { $("ideModal").classList.add("hidden"); updateFabState(); }
};
$("ideClear").onclick = () => assignIde("", "", "main");
if ($("choicesIdeClear")) $("choicesIdeClear").onclick = () => assignIde("", "", "choices");

// 会話の名前を変える（Antigravity の一覧の名前。担当にしているチャンネルの控えも変わる）
async function renameIde(id, cur) {
  const title = prompt("会話の新しい名前", cur && cur !== "(無題)" ? cur : "");
  if (title === null || !title.trim() || title.trim() === cur) return false;
  try {
    const r = await checked("/api/ide/rename", { ide: id, title: title.trim() });
    const x = ideSessions.find((s) => s.id === id);
    if (x) x.title = r.title;  // 一覧の元（Antigravity の会話一覧）に反映されるまで少しかかることがあるので先に直す
    flash("名前を変えました: " + r.title);
    if (state?.setting?.ide_session === id || state?.setting?.choices_ide_session === id) { await poll(); refreshSessions(); }
    renderIde();
    return true;
  } catch (e) { alert(e.message); return false; }
}
$("ideRename").onclick = () => {
  const ide = state?.setting?.ide_session;
  if (!ide) return;
  const cur = ideSessions.find((x) => x.id === ide);
  renameIde(ide, cur?.title || state.setting.ide_title || "");
};
if ($("choicesIdeRename")) {
  $("choicesIdeRename").onclick = () => {
    const ide = state?.setting?.choices_ide_session;
    if (!ide) return;
    const cur = ideSessions.find((x) => x.id === ide);
    renameIde(ide, cur?.title || state.setting.choices_ide_title || "");
  };
}
$("ideNew").onclick = async () => {
  if (formDirty && ($("f_ide_folder").value.trim() !== (state?.setting?.ide_folder || ""))) {
    alert("作業フォルダを変えたら、先に設定を保存してください"); return;
  }
  const dir = agentsFolder || "チャンネルの作業フォルダ";
  const cur = state?.setting?.ide_session;
  if (!confirm(`Antigravity に新しい会話を作って、このチャンネルの担当にする？\nフォルダ: ${dir}` +
    (cur ? "\n（いまの担当の会話は消えない。担当から外れるだけ）" : ""))) return;
  $("ideNew").disabled = true;
  try {
    const needBoth = state?.setting?.suggest_choices !== false && !state?.setting?.choices_ide_session;
    const r = await checked("/api/ide/new", { sid, target: needBoth ? "both" : "main" });
    flash("新しい会話を作って担当にしました: " + r.ide.slice(0, 8) + (r.msg !== "作った" ? " " + r.msg : ""));
    await poll(); refreshSessions(); loadIdeSessions();
  } catch (e) { alert(e.message); }
  $("ideNew").disabled = false;
};
if ($("choicesIdeNew")) {
  $("choicesIdeNew").onclick = async () => {
    if (formDirty && ($("f_ide_folder").value.trim() !== (state?.setting?.ide_folder || ""))) {
      alert("作業フォルダを変えたら、先に設定を保存してください"); return;
    }
    const dir = agentsFolder || "チャンネルの作業フォルダ";
    const cur = state?.setting?.choices_ide_session;
    if (!confirm(`Antigravity に新しい会話を作って、このチャンネルの選択肢担当にする？\nフォルダ: ${dir}` +
      (cur ? "\n（いまの選択肢担当の会話は消えない。担当から外れるだけ）" : ""))) return;
    $("choicesIdeNew").disabled = true;
    try {
      const r = await checked("/api/ide/new", { sid, target: "choices" });
      flash("新しい会話を作って選択肢担当にしました: " + r.ide.slice(0, 8) + (r.msg !== "作った" ? " " + r.msg : ""));
      await poll(); refreshSessions(); loadIdeSessions();
    } catch (e) { alert(e.message); }
    $("choicesIdeNew").disabled = false;
  };
}

// 分岐: IDE会話のユーザー発言を並べ、選んだ発言の直前までをコピーした新しい会話を作る（元の会話はそのまま）
let forkSrc = null;  // { id, title, turns }
async function openFork(id, title) {
  forkSrc = { id, title: title || "", turns: null };
  $("forkSrc").textContent = `元: ${title || "(無題)"}（${id.slice(0, 8)}）`;
  $("forkMove").checked = false;
  $("forkMoveTo").disabled = true;
  $("forkMoveTo").value = agentsFolder || "";  // 既定はこのチャンネルの作業フォルダ
  $("forkTurns").innerHTML = '<span class="muted">発言を読み込み中…</span>';
  $("forkModal").classList.remove("hidden");
  updateFabState();
  try {
    const r = await checked("/api/ide/turns?ide=" + encodeURIComponent(id));
    if (forkSrc?.id !== id) return;
    forkSrc.turns = r.turns;
    if (r.title) forkSrc.title = r.title;
    $("forkSrc").textContent = `元: ${forkSrc.title || "(無題)"}（${id.slice(0, 8)}・${r.steps ?? "?"}ステップ・発言 ${r.turns.length} 件）`;
    renderForkTurns();
  } catch (e) { $("forkTurns").innerHTML = `<span class="ide-off">${esc(e.message)}</span>`; }
}
function renderForkTurns() {
  const box = $("forkTurns");
  if (!forkSrc?.turns) return;
  const hide = $("forkHideSpace").checked;
  const list = forkSrc.turns.filter((t) => !hide || t.text.trim()).reverse();  // 新しい発言が上
  box.innerHTML = list.length ? "" : '<span class="muted">発言がありません</span>';
  for (const t of list) {
    const d = document.createElement("div");
    d.className = "ide-conv fork-turn";
    const when = t.time ? new Date(t.time).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    d.innerHTML = `<div class="body"><div class="t">${t.text.trim() ? esc(t.text) : '<span class="muted">（スペースだけの送信）</span>'}</div>
      <div class="s">#${t.n} ・ ${esc(when)} ・ ステップ ${t.step}</div></div>`;
    if (t.at !== null) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "sub"; b.textContent = "この発言からやり直す";
      b.title = `ステップ ${t.at} まで（この発言の直前）をコピーして分岐する`;
      b.onclick = () => doFork(t.at, t.n, `#${t.n}「${t.text.trim().slice(0, 30) || "（スペース）"}」の直前`);
      d.appendChild(b);
    }
    box.appendChild(d);
  }
}
async function doFork(at, n, label) {
  const assign = $("forkAssign").checked;
  const moveTo = $("forkMove").checked ? $("forkMoveTo").value.trim() : "";
  if ($("forkMove").checked && !moveTo) return alert("移す先のフォルダを入れてください");
  if (!confirm(`${label}で分岐する？\n元の会話はそのまま残ります。` + (assign ? "\n分岐した会話をこのチャンネルの担当にします。" : "") +
    (moveTo ? `\n分岐したあと、その会話を\n${moveTo}\nへ移します（Antigravity を再起動。もう一度確かめます）。` : ""))) return;
  $("forkModal").querySelectorAll("button").forEach((b) => (b.disabled = true));
  let r = null;
  try {
    r = await checked("/api/ide/fork", { sid, ide: forkSrc.id, title: forkSrc.title, at, n, assign, move_to: moveTo });
    flash(`分岐しました: ${r.title}（${r.ide.slice(0, 8)}）` + (assign ? "。担当にしました" : "") + (r.msg && r.msg.includes("移さない") ? "。移す先が元と同じフォルダなので移しません" : ""));
    $("forkModal").classList.add("hidden");
    $("ideModal").classList.add("hidden");
    updateFabState();
    if (assign) { await poll(); refreshSessions(); }
    loadIdeSessions();
  } catch (e) { alert(e.message); }
  $("forkModal").querySelectorAll("button").forEach((b) => (b.disabled = false));
  if (r?.move) {
    // 予約しただけなので、ここで移す（やめても予約は残り、設定の「Antigravity を再起動して移す」からできる）
    let pend = [r.move];
    try { pend = (await checked("/api/ide/move")).pending || pend; } catch { }
    await runMoves(pend, false);
    loadAgents();
  }
}
$("forkMove").onchange = () => { $("forkMoveTo").disabled = !$("forkMove").checked; if ($("forkMove").checked) $("forkMoveTo").focus(); };
$("ideFork").onclick = () => {
  const ide = state?.setting?.ide_session;
  if (!ide) return;
  const cur = ideSessions.find((x) => x.id === ide);
  openFork(ide, cur?.title || state.setting.ide_title || "");
};
$("forkLatest").onclick = () => doFork(-1, 0, "最新の状態");
$("forkHideSpace").onchange = renderForkTurns;
$("forkClose").onclick = () => { $("forkModal").classList.add("hidden"); updateFabState(); };
$("forkModal").addEventListener("click", (e) => { if (e.target === $("forkModal")) { $("forkModal").classList.add("hidden"); updateFabState(); } });
// 送信に使うモデル。一覧は Antigravity から読む（読めなくても、選んであるモデルは出す）
let ideModels = null;
async function loadModels() {
  try {
    ideModels = await checked("/api/ide/models");
  } catch (e) {
    ideModels = { models: [], error: e.message };
  }
  renderModels();
}
function renderModels() {
  const sel = $("ideModel");
  const selChoices = $("choicesIdeModel");
  const rows = ideModels?.models || [];
  const reset = modelResetText;      // 元のローカル関数と同一文字列（重複排除）
  const left = (r) => (typeof r.left !== "number" ? ""
    : r.left > 0 ? `（残り${Math.max(1, Math.round(r.left * 100))}%）` : `（使い切り${reset(r)}）`);
  const opts = [`<option value="">会話の最後の入力と同じ${ideModels?.error ? "（一覧を読めない）" : ""}</option>`]
    .concat(rows.map((r) => `<option value="${esc(r.model)}">${esc(r.label)}${left(r)}${r.model === ideModels.default ? " ・既定" : ""}</option>`));

  if (sel && state && document.activeElement !== sel) {
    const want = state.setting?.ide_model || "";
    const optsMain = opts.slice();
    if (want && !rows.some((r) => r.model === want)) optsMain.push(`<option value="${esc(want)}">${esc(want)}（いまは選べない）</option>`);
    const html = optsMain.join("");
    if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
    sel.value = want;
    $("footBar").classList.toggle("hidden", !state.setting?.ide_session);  // 担当の会話がなければ送信もしない
  }

  if (selChoices && state && document.activeElement !== selChoices) {
    const wantChoices = state.setting?.choices_ide_model || "";
    const optsChoices = [`<option value="">メインのモデルと同じ${ideModels?.error ? "（一覧を読めない）" : ""}</option>`]
      .concat(rows.map((r) => `<option value="${esc(r.model)}">${esc(r.label)}${left(r)}${r.model === ideModels.default ? " ・既定" : ""}</option>`));
    if (wantChoices && !rows.some((r) => r.model === wantChoices)) optsChoices.push(`<option value="${esc(wantChoices)}">${esc(wantChoices)}（いまは選べない）</option>`);
    const htmlChoices = optsChoices.join("");
    if (selChoices.dataset.html !== htmlChoices) { selChoices.innerHTML = htmlChoices; selChoices.dataset.html = htmlChoices; }
    selChoices.value = wantChoices;
  }
  updateModelSheetLeft();            // 選択変更・↻・poll すべてで追従
}
$("ideModel").onchange = async () => {
  try {
    await checked("/api/ide/model", { sid, model: $("ideModel").value, target: "main" });
    toast("送信のモデル: " + $("ideModel").selectedOptions[0].textContent);
    await poll();
  } catch (e) { alert(e.message); }
  $("ideModel").blur();
  renderModels();
};
if ($("choicesIdeModel")) {
  $("choicesIdeModel").onchange = async () => {
    try {
      await checked("/api/ide/model", { sid, model: $("choicesIdeModel").value, target: "choices" });
      flash("選択肢のモデル: " + $("choicesIdeModel").selectedOptions[0].textContent);
      await poll();
    } catch (e) { alert(e.message); }
    $("choicesIdeModel").blur();
    renderModels();
  };
}
$("ideModelReload").onclick = loadModels;
$("idePokeNow").onclick = async () => {
  $("idePokeNow").disabled = true;
  try {
    const r = await checked("/api/ide/poke", { sid, now: true, target: "main" });
    flash((r.ok ? "送信しました: " : "送信できませんでした: ") + r.msg);
  } catch (e) { alert(e.message); }
  $("idePokeNow").disabled = false;
  loadIdeSessions();
};
if ($("choicesIdePokeNow")) {
  $("choicesIdePokeNow").onclick = async () => {
    $("choicesIdePokeNow").disabled = true;
    try {
      const r = await checked("/api/ide/poke", { sid, now: true, target: "choices" });
      flash((r.ok ? "選択肢会話に送信しました: " : "送信できませんでした: ") + r.msg);
    } catch (e) { alert(e.message); }
    $("choicesIdePokeNow").disabled = false;
    loadIdeSessions();
  };
};
$("ctxReset").onclick = async () => {
  try {
    await checked("/api/context/reset", { sid });
    $("ideState").textContent = "次の入力では設定と会話を全部送ります";
    setTimeout(renderIde, 3000);
  } catch (e) { alert(e.message); }
};

$("presetLoad").onclick = () => {
  const p = presets.find((x) => x.id === $("presetSelect").value);
  if (!p) return;
  const st = readForm();
  st.folder_files = strList(p.files);
  st.files_extra = p.extra || "";
  fillFileOptions(st);
  formDirty = true;
  flash(`プリセット「${p.name}」を読み込みました（保存で確定）`);
};
$("presetSave").onclick = async () => {
  const name = prompt("プリセット名");
  if (!name) return;
  const body = { name, files: readFolderFiles(), extra: $("f_files_extra").value };
  try { await checked("/api/file-presets", body); await loadOptions(); flash("プリセットを保存しました"); }
  catch (e) { alert(e.message); }
};
$("presetDelete").onclick = async () => {
  const p = presets.find((x) => x.id === $("presetSelect").value);
  if (!p || !confirm(`プリセット「${p.name}」を削除しますか？`)) return;
  try { await checked("/api/file-presets/delete", { id: p.id }); $("presetSelect").value = ""; await loadOptions(); }
  catch (e) { alert(e.message); }
};
// ---------------------------------------------------------------- 画像のキャラ固定
let charPresets = [];
let newCharRow = false;  // 手動追加の空の行を出しているか
const SRC_LABEL = { ai: "AI作成", user: "手動", preset: "プリセット" };
const charKey = (n) => (n || "").replace(/\s+/g, "").toLowerCase();

// 編集中の行があるあいだは、AIの応答などで一覧を描き直さない（打ちかけが消えないように）
function charsDirty() {
  return newCharRow || !!$("charList").querySelector(".char-row.dirty");
}
function renderChars() {
  if (!state || charsDirty()) return;
  const list = $("charList");
  list.innerHTML = "";
  const chars = state.setting.chars || [];
  if (!chars.length) list.innerHTML = '<span class="muted">まだいません（AIがキャラを出すと自動で入ります）</span>';
  for (const c of chars) list.appendChild(charRow(c));
}
function charRow(c) {
  const row = document.createElement("div");
  row.className = "char-row" + (c.id ? "" : " dirty");
  row.innerHTML = `<div class="row tight">
      <input class="char-name" placeholder="キャラ名">
      <span class="char-src"></span>
      <label class="check" title="AIに外見タグを書き換えさせない"><input type="checkbox" class="char-lock"> 🔒固定</label>
    </div>
    <textarea class="char-tags mono" rows="2" spellcheck="false" placeholder="black hair, bob cut, blunt bangs, blue eyes, ..."></textarea>
    <div class="row tight">
      <button type="button" class="sub char-save">保存</button>
      <button type="button" class="sub char-preset">プリセットに保存</button>
      <button type="button" class="sub char-del">削除</button>
    </div>`;
  const q = (s) => row.querySelector(s);
  q(".char-name").value = c.name || "";
  q(".char-tags").value = c.tags || "";
  q(".char-lock").checked = !!c.locked;
  q(".char-src").textContent = c.id ? SRC_LABEL[c.source] || "" : "新規";
  const dirty = () => row.classList.add("dirty");
  q(".char-name").oninput = dirty;
  q(".char-lock").onchange = dirty;
  q(".char-tags").oninput = () => { dirty(); q(".char-lock").checked = true; };  // 手で直したタグはAIに戻されないように
  q(".char-save").onclick = async () => {
    const name = q(".char-name").value.trim(), tags = q(".char-tags").value.trim();
    const source = c.source === "preset" && tags === c.tags ? "preset" : "user";
    try {
      await checked("/api/chars/upsert", { sid, char: { id: c.id, name, tags, locked: q(".char-lock").checked, source } });
      row.classList.remove("dirty");
      if (!c.id) newCharRow = false;
      await poll();
      renderChars();
    } catch (e) { alert(e.message); }
  };
  q(".char-preset").onclick = async () => {
    const name = q(".char-name").value.trim(), tags = q(".char-tags").value.trim();
    if (!name || !tags) return alert("名前とタグを入れてください");
    if (charPresets.some((p) => charKey(p.name) === charKey(name)) && !confirm(`プリセット「${name}」を上書きしますか？`)) return;
    try { await checked("/api/char-presets", { name, tags }); await loadCharPresets(); flash(`キャラプリセット「${name}」を保存しました`); }
    catch (e) { alert(e.message); }
  };
  q(".char-del").onclick = async () => {
    if (c.id) {
      if (!confirm(`「${c.name}」の固定を外しますか？（控えは data/trash に残ります）`)) return;
      try { await checked("/api/chars/delete", { sid, id: c.id }); } catch (e) { return alert(e.message); }
    } else newCharRow = false;
    row.remove();
    await poll();
    renderChars();
  };
  return row;
}
async function loadCharPresets() {
  try { charPresets = (await checked("/api/char-presets")).presets || []; } catch (e) { flash(e.message); }
  fillSelect($("charPresetSelect"), charPresets.map((p) => ({ value: p.id, label: `${p.name}（${p.tags.slice(0, 40)}${p.tags.length > 40 ? "…" : ""}）` })),
    $("charPresetSelect").value, "キャラプリセット…");
}
$("charNew").onclick = () => {
  if (newCharRow) return;
  newCharRow = true;
  $("charList").querySelector(":scope > .muted")?.remove();
  const row = charRow({ id: "", name: "", tags: "", locked: true, source: "user" });
  $("charList").appendChild(row);
  row.querySelector(".char-name").focus();
};
$("charPresetAdd").onclick = async () => {
  const p = charPresets.find((x) => x.id === $("charPresetSelect").value);
  if (!p || !state) return;
  const cur = (state.setting.chars || []).find((c) => charKey(c.name) === charKey(p.name));
  if (cur && !confirm(`このチャンネルの「${cur.name}」をプリセットのタグで上書きしますか？`)) return;
  try {
    await checked("/api/chars/upsert", { sid, char: { id: cur?.id, name: p.name, tags: p.tags, locked: true, source: "preset" } });
    await poll();
    renderChars();
    flash(`「${p.name}」をこのチャンネルに固定しました`);
  } catch (e) { alert(e.message); }
};
$("charPresetDelete").onclick = async () => {
  const p = charPresets.find((x) => x.id === $("charPresetSelect").value);
  if (!p || !confirm(`キャラプリセット「${p.name}」を削除しますか？（各チャンネルに追加済みのものは残ります）`)) return;
  try { await checked("/api/char-presets/delete", { id: p.id }); $("charPresetSelect").value = ""; await loadCharPresets(); }
  catch (e) { alert(e.message); }
};

$("appSave").onclick = async () => {
  try {
    await checked("/api/app-settings", { new_channel_ide: $("app_new_channel_ide").checked });
    await loadOptions();
    $("appMsg").textContent = "保存しました";
    setTimeout(() => ($("appMsg").textContent = ""), 3000);
  } catch (e) { alert(e.message); }
};
$("previewBtn").onclick = async () => {
  if (formDirty && !confirm("未保存の変更があります。保存済みの設定で表示します。")) return;
  const r = await api("/api/prompt-preview?sid=" + encodeURIComponent(sid));
  openText("AIに送る設定（" + (r.chars || 0) + "文字）",
    (r.missing?.length ? "⚠ 読めないファイル: " + r.missing.join(", ") + "\n" : "") + "wait_input.py が入力を受け取ったとき、この設定とAIメモ・会話・発言をそのまま表示します。担当IDEが前回と同じなら、変わった設定と増えた発言だけを出します。",
    r.text || "", null);
};

// ---------------------------------------------------------------- 汎用テキストモーダル
function openText(title, hint, body, onSave) {
  $("textTitle").textContent = title;
  $("textHint").textContent = hint;
  $("textBody").value = body;
  $("textBody").readOnly = !onSave;
  $("textSave").classList.toggle("hidden", !onSave);
  textSaveHandler = onSave;
  $("textModal").classList.remove("hidden");
  updateFabState();
}
$("textCancel").onclick = () => { $("textModal").classList.add("hidden"); updateFabState(); };
$("textSave").onclick = async () => {
  if (!textSaveHandler) return;
  try {
    await textSaveHandler($("textBody").value);
    $("textModal").classList.add("hidden");
    updateFabState();
  } catch (e) { alert(e.message); }
};

// ---------------------------------------------------------------- 会話管理
$("exportBtn").onclick = () => { location.href = "/api/session/export?sid=" + encodeURIComponent(sid); };
$("importBtn").onclick = () => $("importFile").click();
$("importFile").onchange = async () => {
  const f = $("importFile").files[0];
  $("importFile").value = "";
  if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    const r = await checked("/api/session/import", { data });
    await refreshSessions();
    await switchTo(r.sid);
  } catch (e) { alert("取り込めませんでした: " + e.message); }
};
$("rawBtn").onclick = () => {
  if (!state) return;
  const { setting, messages } = state;
  openText("チャンネルの JSON を編集", "保存すると今の内容は data/trash に控えを取ってから置き換えます。",
    JSON.stringify({ setting, messages }, null, 2), async (text) => {
      const obj = JSON.parse(text);
      await checked("/api/session/raw", { sid, setting: obj.setting, messages: obj.messages });
      lastRev = -1;
      formDirty = false;
      await poll();
    });
};

// ---------------------------------------------------------------- chat
function messageSignature(m, isLast) {
  return JSON.stringify([
    m.id,
    m.role,
    m.text,
    m.ts,
    m.edited,
    m.original_text,
    m.status,
    m.choices_status,
    m.choices_type,
    m.choices ? m.choices.map((c) => [c.label, c.text]) : null,
    m.memory_status,
    m.image_refine_status,
    m.image_refine_mode,
    m.image_refine_error,
    m.image?.status,
    m.image?.file,
    m.image?.error,
    m.image?.prompt,
    m.image?.refined_prompt,
    m.image?.prev_prompt,
    m.image?.history?.map((h) => [h.file, h.seed]) || null,
    m.image?.chars || null,
    m.state || null,
    m.files ? m.files.map((f) => [f.name, f.size]) : null,
    isLast
  ]);
}

// ---------------------------------------------------------------- その他の操作（⋯）
// メッセージごとの操作は「⋯」にまとめる。
// PC はその場にポップオーバー、狭い画面は画面下のシート。
// 開いている間は外側クリック / Esc で閉じ、スクロールやリサイズでは閉じずに位置を追う。
const moreOpen = { cur: null };

function isNarrowScreen() {
  return typeof window !== "undefined" && window.innerWidth <= 768;
}

// アンカーの位置に合わせて置き直す（下に入らなければ上へフリップ）
function placeMorePopup(el, anchor) {
  if (!el || !anchor || anchor.isConnected === false) return;
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth || 176;
  const h = el.offsetHeight || 220;
  let top = r.bottom + 6;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
  let left = r.right - w;
  if (left + w > window.innerWidth - 8) left = Math.max(8, window.innerWidth - w - 8);
  if (left < 8) left = 8;
  el.style.top = top + "px";
  el.style.left = left + "px";
}

function moreOnDocClick(e) {
  const cur = moreOpen.cur;
  if (!cur) return;
  const t = e.target;
  if (cur.el && cur.el.contains && cur.el.contains(t)) return;
  if (cur.backdrop && cur.backdrop === t) return;
  // 開いたボタン自身はトグルに任せる（押し直しで閉じる）
  if (cur.anchor && cur.anchor.contains && cur.anchor.contains(t)) return;
  closeMoreMenu();
}

function moreOnKey(e) {
  if (e.key !== "Escape") return;
  e.stopPropagation();
  closeMoreMenu();
}

function moreOnReflow() {
  const cur = moreOpen.cur;
  if (!cur || cur.kind !== "popup") return;
  placeMorePopup(cur.el, cur.anchor);
}

function unbindMoreGlobal() {
  document.removeEventListener("click", moreOnDocClick, true);
  document.removeEventListener("keydown", moreOnKey, true);
  document.removeEventListener("scroll", moreOnReflow, true);
  window.removeEventListener("resize", moreOnReflow);
}

function closeMoreMenu() {
  const cur = moreOpen.cur;
  if (!cur) return;
  moreOpen.cur = null;
  unbindMoreGlobal();
  if (cur.anchor && typeof cur.anchor.setAttribute === "function") {
    cur.anchor.setAttribute("aria-expanded", "false");
  }
  if (cur.el && cur.el.parentNode) cur.el.parentNode.removeChild(cur.el);
  if (cur.backdrop && cur.backdrop.parentNode) cur.backdrop.parentNode.removeChild(cur.backdrop);
  // FAB の表示は「今のレイヤー」から再導出する（直接フラグを触らない）
  updateFabState();
}

// メニューの中身。出す項目はメッセージの状態で変わる
function buildMoreMenu(m, isLast, s) {
  const menu = document.createElement("div");
  menu.className = "more-menu";

  const sep = () => {
    const hr = document.createElement("hr");
    hr.className = "more-sep";
    menu.appendChild(hr);
  };
  const item = (label, onClick, opts = {}) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "more-item" + (opts.danger ? " danger" : "");
    b.textContent = label;
    if (opts.title) b.title = opts.title;
    b.onclick = () => {
      closeMoreMenu();
      onClick();
    };
    menu.appendChild(b);
  };

  // 画像系（完成している画像だけ）
  const im = m.image;
  if (im && im.status === "done" && im.file && !m.image_refine_status) {
    item("🔍 拡大", () => openLightbox(imgUrl(im.file)));
    sep();
  }

  // 編集系
  item(m.role === "user" ? "✏️ 編集して再送" : "本文を編集", () => openEdit(m),
    { title: m.role === "user" ? "この入力を編集して、この発言以降をやり直す" : "この発言の本文を編集する" });
  if (m.role !== "user" && (m.state || s.setting?.rp_state)) {
    item("キャラの状態", () => openState(m, s.messages), { title: "この応答のときのキャラの状態と内心を直す（次の入力で AI が読む）" });
  }
  sep();

  // 会話系
  if (m.role !== "user" && s.messages.slice(0, s.messages.indexOf(m)).some((x) => x.role === "user")) {
    item("再生成", async () => {
      if (!confirm("この応答以降の会話を削除して、AI応答を作り直しますか？")) return;
      const ch = sid;
      let r;
      try { r = await checked("/api/message/regenerate", { sid, id: m.id }); await poll(); }
      catch (e) { alert(e.message); return; }
      await revertIde(r, ch);
    });
  }
  item("分岐", async () => {
    const src = sid, ide = state?.setting?.ide_session;
    let r;
    try { r = await checked("/api/session/branch", { sid, id: m.id }); await refreshSessions(); await switchTo(r.sid); }
    catch (e) { alert(e.message); return; }
    if (!ide) return;
    flash("Antigravity の会話も分岐しています…（大きい会話は数十秒）");
    try {
      const f = await checked("/api/ide/branch-fork", { sid: r.sid, src, id: m.id });
      if (f.skipped) alert("チャンネルは分岐しました。\n" + f.skipped);
      else {
        flash(`Antigravity の会話も分岐して担当にしました: ${f.title}（${f.ide.slice(0, 8)}）`);
        if (sid === r.sid) await poll();
        refreshSessions(); loadIdeSessions();
      }
    } catch (e) { alert("チャンネルは分岐しましたが、Antigravity の会話は分岐できませんでした:\n" + e.message); }
  }, { title: "ここまでの会話で新しいチャンネルを作る（担当の Antigravity 会話も同じところで分岐して、新しいチャンネルの担当にする）" });
  item("コピー", () => navigator.clipboard.writeText(m.text || ""));
  sep();

  // 取り消しにくい操作はまとめて一番下に置く
  if (!isLast) {
    item("巻き戻し", async () => {
      if (!confirm("このメッセージより後の会話を消して、ここまで戻しますか？（控えは data/trash に残ります）")) return;
      const ch = sid;
      let r;
      try { r = await checked("/api/message/truncate", { sid, id: m.id }); await poll(); }
      catch (e) { alert(e.message); return; }
      await revertIde(r, ch);
    });
  }
  item("削除", async () => {
    if (!confirm("このメッセージを削除？")) return;
    const ch = sid, r = await post("/api/delete", { id: m.id });
    await poll();
    await revertIde(r, ch);
  }, { danger: true });

  return menu;
}

function toggleMoreMenu(anchorBtn, m, isLast, s) {
  const already = !!moreOpen.cur && moreOpen.cur.anchor === anchorBtn;
  closeMoreMenu();
  if (already) return;

  const menu = buildMoreMenu(m, isLast, s);
  if (isNarrowScreen()) {
    const backdrop = document.createElement("div");
    backdrop.className = "more-backdrop";
    menu.classList.add("more-sheet");
    document.body.appendChild(backdrop);
    document.body.appendChild(menu);
    backdrop.addEventListener("click", closeMoreMenu);
    // 画面下のメニューボタン（☰）はシートに重なる。表示は updateFabState() が導出する
    moreOpen.cur = { kind: "sheet", el: menu, backdrop, anchor: anchorBtn };
  } else {
    menu.classList.add("more-popup");
    menu.style.visibility = "hidden";  // 位置を決める前に見えないように
    document.body.appendChild(menu);
    placeMorePopup(menu, anchorBtn);
    menu.style.visibility = "";
    moreOpen.cur = { kind: "popup", el: menu, backdrop: null, anchor: anchorBtn };
  }

  if (typeof anchorBtn.setAttribute === "function") anchorBtn.setAttribute("aria-expanded", "true");
  document.addEventListener("keydown", moreOnKey, true);
  document.addEventListener("click", moreOnDocClick, true);
  document.addEventListener("scroll", moreOnReflow, true);
  window.addEventListener("resize", moreOnReflow);
  updateFabState();   // ⋯ シート（z66/67）は FAB を覆うので、ここで表示を導出し直す
}

function createMessageWrap(m, isLast, s) {
  const wrap = document.createElement("div");
  wrap.className = "msg-wrap " + m.role;
  if (m.id) wrap.id = "msg-" + m.id;
  const bubble = document.createElement("div");
  bubble.className = "msg md " + m.role;
  bubble.innerHTML = m.edited ? fmtAltered(m.original_text, m.text || "") : fmt(m.text || "");
  bubble.addEventListener("click", (e) => {
    if (touchMoved || (lastScrollTime && Date.now() - lastScrollTime < 300)) return;
    if (e.target && typeof e.target.closest === "function" && e.target.closest("a, button, input, select, textarea")) return;
    if (rpMode && m.role === "assistant") {
      const targetImg = m.image?.file || wrap.dataset.img || rpBgFile;
      if (targetImg) {
        openLightbox(imgUrl(targetImg));
      }
      return;
    }
    // 背景表示以外では、バブルをタップしても何もしない（操作は「⋯」に集約した）
  });
  if (m.edited) {
    bubble.classList.add("edited");
    bubble.title = "改変前: " + (m.original_text || "");
  }
  if (m.text || !m.files?.length) wrap.appendChild(bubble);
  if (m.files?.length) wrap.appendChild(filesBox(m.files));
  if (m.edited) {
    const tag = document.createElement("span");
    tag.className = "edited-label";
    tag.textContent = "改変済";
    wrap.appendChild(tag);
  }

  const hasImage = !!m.image;
  const hasChoices = m.role !== "user" && ((Array.isArray(m.choices) && m.choices.length > 0) || m.choices_status);

  // 画像本体（背景表示ではCSSで非表示にして背景で使用）
  if (hasImage && m.image.status === "done" && m.image.file && !m.image_refine_status) {
    const box = document.createElement("div");
    box.className = "gen-image";
    const img = document.createElement("img");
    img.src = imgUrl(m.image.file);
    img.onclick = () => openLightbox(img.src);
    box.appendChild(img);
    wrap.dataset.img = m.image.file;  // 背景表示で、この発言を読んでいる間の背景にする
    wrap.appendChild(box);
  } else if (m.image?.file) {
    wrap.dataset.img = m.image.file;
  }

  // 1段に揃えるツールバー（画像操作・返答選択肢・⋯）
  // ⋯ があるので、操作が何も無いメッセージでもこの段を出す
  const bar = document.createElement("div");
  bar.className = "choices-container";

  // 画像ツールボタン群
  if (hasImage) {
    const im = m.image;
    // 画像まわりの状態（生成中・生成待ち・タグ推敲中）は、
    // 「🔁画像」ボタン1つのラベルを入れ替えて表す（押してもボタンは増やさない）
    const refineBusy = (m.image_refine_status === "pending" || m.image_refine_status === "processing");
    const genBusy = (im.status === "queued" || im.status === "generating");
    const isErr = (im.status === "error" || m.image_refine_status === "error");
    const hasDoneImage = (im.status === "done" && im.file && !m.image_refine_status);
    const waitingGen = !hasDoneImage && !genBusy && !isErr;

    // エラーだけは文言が長いので、ボタンとは別のバッジで出す
    if (isErr && !refineBusy && !genBusy) {
      const ph = document.createElement("span");
      ph.className = "img-status-badge err";
      ph.textContent = im.status === "error"
        ? "生成エラー: " + (im.error || "")
        : "推敲エラー: " + (m.image_refine_error || "");
      bar.appendChild(ph);
    }
    // 完成した画像の「🔍 拡大」は ⋯ の中に入れた（画像本体のクリックでも拡大できる）

    // タグはそのままで、シードだけランダムに変えて作り直す
    const RG_IDLE_LABEL = "🔁画像";
    const RG_IDLE_TITLE = "タグはそのままで、新しいシードで作り直します（タグを変えるときは 🎨タグ）";
    const RG_GEN_LABEL = "🎨 生成中…";
    const RG_GEN_TITLE = "画像を生成しています（終わると「🔁画像」に戻ります）";

    let rgLabel = RG_IDLE_LABEL;
    let rgTitle = RG_IDLE_TITLE;
    if (refineBusy) {
      // プロンプト画面の「✨ タグ推敲」からの推敲中
      rgLabel = "✨ タグ推敲中…";
      rgTitle = "AIがプロンプトを推敲しています";
    } else if (genBusy) {
      rgLabel = RG_GEN_LABEL;
      rgTitle = RG_GEN_TITLE;
    } else if (waitingGen) {
      rgLabel = "⏳ 生成待ち…";
      rgTitle = "画像の生成待ちです（終わると「🔁画像」に戻ります。タップで作り直す）";
    }

    const rg = document.createElement("button");
    rg.type = "button";
    rg.className = "btn-img-refine" + ((refineBusy || genBusy || waitingGen) ? " busy" : "");
    rg.textContent = rgLabel;
    rg.title = rgTitle;
    if (refineBusy || genBusy) rg.disabled = true;
    rg.onclick = async () => {
      if (rg.disabled) return;
      // 押した瞬間にその場で「生成中」に入れ替える（別のボタンは出さない）
      rg.disabled = true;
      rg.className = "btn-img-refine busy";
      rg.textContent = RG_GEN_LABEL;
      rg.title = RG_GEN_TITLE;
      try {
        const res = await post("/api/regen", { id: m.id });
        if (res && res.error) throw new Error(res.error);
      } catch (e) {
        rg.className = "btn-img-refine";
        rg.textContent = RG_IDLE_LABEL;
        rg.title = RG_IDLE_TITLE;
        rg.disabled = false;
        toast("再生成リクエストに失敗しました: " + e.message);
      }
      poll();
      wakePoll();
    };
    bar.appendChild(rg);

    const pe = document.createElement("button");
    pe.type = "button";
    pe.className = "btn-prompt";
    pe.textContent = "🎨タグ";
    pe.onclick = () => openPrompt(m);
    bar.appendChild(pe);
  }

  // 返答選択肢（同じコンテナに配置し、横1列で揃える）
  if (hasChoices) {
    choicesBox(m, bar);
  }

  // 画像履歴サムネイル（複数ある場合、ボタン列の下に展開）
  if (hasImage && m.image.history && m.image.history.length > 1) {
    const th = document.createElement("div");
    th.className = "thumbs";
    for (const h of m.image.history.slice(-8)) {
      const t = document.createElement("img");
      t.src = imgUrl(h.file);
      t.title = "seed " + h.seed;
      if (h.file === m.image.file) t.className = "active";
      t.onclick = () => post("/api/pick", { id: m.id, file: h.file }).then(poll);
      th.appendChild(t);
    }
    bar.appendChild(th);
  }

  // ユーザー発言の「✏️ 編集して再送」は ⋯ の中に集約した（ツールバーには出さない）

  // 送信中の発言には、その場からも止められるボタンを出す
  if (m.role === "user" && (m.status === "pending" || m.status === "processing")) {
    const stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.className = "btn-stop-wait";
    stopBtn.textContent = "■ 停止";
    stopBtn.title = "この送信を停止して、入力欄に戻す";
    stopBtn.onclick = () => stopTurn(m.id);
    bar.appendChild(stopBtn);
  }

  // 「送信済」は出さない（送信中は ■ 停止 があるので分かる）
  const st = m.role === "user" && m.status === "processing" ? "読込済" : "";
  if (st) {
    const info = document.createElement("div");
    info.className = "meta";
    info.textContent = st;
    bar.appendChild(info);
  }

  // その他の操作（⋯）。常に右端。狭い画面で2段目に落ちても右端に寄せる
  const moreBtn = document.createElement("button");
  moreBtn.type = "button";
  moreBtn.className = "btn-more";
  moreBtn.textContent = "⋯";
  moreBtn.title = "その他の操作";
  moreBtn.setAttribute("aria-haspopup", "true");
  moreBtn.setAttribute("aria-expanded", "false");
  moreBtn.onclick = () => toggleMoreMenu(moreBtn, m, isLast, s);
  bar.appendChild(moreBtn);

  wrap.appendChild(bar);
  return wrap;
}

function updateElapsedTimeUI(et) {
  const day = et?.day || 1;
  const period = et?.period || "午前";
  const emoji = PERIOD_EMOJIS[period] || "☀️";
  const text = `${day}日目・${period} ${emoji}`;
  if ($("elapsedTimeLabel")) $("elapsedTimeLabel").textContent = text;
  if ($("sideElapsedTimeLabel")) $("sideElapsedTimeLabel").textContent = text;
  if ($("mobileElapsedTimeLabel")) $("mobileElapsedTimeLabel").textContent = text;
}

function renderElapsedTimeDropdown(dropdown) {
  dropdown.innerHTML = "";
  const et = state?.setting?.elapsed_time || { day: 1, period: "午前" };
  const day = et.day || 1;
  const curIdx = Math.max(0, PERIODS.indexOf(et.period || "午前"));

  for (let step = 1; step <= 5; step++) {
    const newTotal = curIdx + step;
    const addDays = Math.floor(newTotal / PERIODS.length);
    const targetDay = day + addDays;
    const targetPeriod = PERIODS[newTotal % PERIODS.length];
    const targetEmoji = PERIOD_EMOJIS[targetPeriod] || "";
    const desc = PERIOD_DESCS[targetPeriod] || "";

    const item = document.createElement("button");
    item.type = "button";
    item.className = "elapsed-time-item";
    const dayLabel = `${targetDay}日目・`;
    const icon = step === 5 && addDays > 0 ? "🌅" : "⏩";
    item.innerHTML = `<span style="font-weight:600">${icon} +${step}段階先 : <strong>${dayLabel}${targetPeriod} ${targetEmoji}</strong></span> <span class="muted" style="font-size:11px;margin-left:8px;">（${desc}）</span>`;
    item.onclick = async (e) => {
      e.stopPropagation();
      dropdown.classList.add("hidden");
      await advanceElapsedTime(step);
    };
    dropdown.appendChild(item);
  }
}

function setupElapsedTimeDropdown(btnId, dropdownId) {
  const btn = $(btnId);
  const dropdown = $(dropdownId);
  if (!btn || !dropdown) return;

  btn.onclick = (e) => {
    e.stopPropagation();
    const isHidden = dropdown.classList.toggle("hidden");
    if (!isHidden) {
      renderElapsedTimeDropdown(dropdown);
    }
  };
}

async function advanceElapsedTime(step) {
  if (!state || !state.setting) return;
  const et = state.setting.elapsed_time || { day: 1, period: "午前" };
  const day = et.day || 1;
  const curIdx = Math.max(0, PERIODS.indexOf(et.period || "午前"));
  const newTotal = curIdx + step;
  const addDays = Math.floor(newTotal / PERIODS.length);
  const targetDay = day + addDays;
  const targetPeriod = PERIODS[newTotal % PERIODS.length];

  const newSetting = JSON.parse(JSON.stringify(state.setting));
  newSetting.elapsed_time = { day: targetDay, period: targetPeriod };
  newSetting.turns_in_period = 0;

  try {
    const r = await post("/api/setting", { setting: newSetting });
    state.setting = newSetting;
    updateElapsedTimeUI(newSetting.elapsed_time);
    fillForm(newSetting, true);
    flash(`時間を「${targetDay}日目・${targetPeriod}」に進めました`);
  } catch (e) {
    flash("時間の更新に失敗: " + e.message);
  }
}

// ---------------------------------------------------------------- 作中の時間モーダル（モバイルの日時チップから）
let elapsedDraft = { day: 1, period: "午前" };
function clampDay(n) { return Math.max(1, Math.min(99999, Number.isFinite(n) ? Math.trunc(n) : 1)); }

function renderElapsedPeriodPicker() {
  const box = $("elapsedPeriodPicker");
  if (!box) return;
  box.innerHTML = "";
  for (const p of PERIODS) {
    const b = document.createElement("button");
    b.type = "button";
    const on = p === elapsedDraft.period;
    b.className = on ? "selected" : "";
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.textContent = `${p} ${PERIOD_EMOJIS[p] || ""}`.trim();
    b.onclick = () => { elapsedDraft.period = p; renderElapsedPeriodPicker(); };
    box.appendChild(b);
  }
}
function setElapsedDay(n) {
  elapsedDraft.day = clampDay(n);
  if ($("elapsedDayInput")) $("elapsedDayInput").value = String(elapsedDraft.day);
}
function openElapsedModal() {
  if (!state || !state.setting) { toast("チャンネルを読み込んでからもう一度", 6000); return; }
  const et = state.setting.elapsed_time || {};
  elapsedDraft = { day: clampDay(parseInt(et.day, 10)), period: PERIODS.includes(et.period) ? et.period : "午前" };
  setElapsedDay(elapsedDraft.day);
  renderElapsedPeriodPicker();
  $("elapsedModal").classList.remove("hidden");
  hideReveal();                       // reveal(z64) は modal(z60) より上なので必ず隠す
  updateFabState();
}
function closeElapsedModal() {
  if ($("elapsedModal")) $("elapsedModal").classList.add("hidden");
  updateFabState();
}
async function saveElapsedModal() {
  if (!state || !state.setting) return closeElapsedModal();
  const raw = $("elapsedDayInput") ? parseInt($("elapsedDayInput").value, 10) : elapsedDraft.day;
  const day = clampDay(raw);
  const period = PERIODS.includes(elapsedDraft.period) ? elapsedDraft.period : "午前";
  const before = state.setting.elapsed_time || {};
  if ((before.day || 1) === day && (before.period || "午前") === period) return closeElapsedModal();
  const newSetting = JSON.parse(JSON.stringify(state.setting));
  newSetting.elapsed_time = { day, period };
  newSetting.turns_in_period = 0;      // advanceElapsedTime と同じ経路
  const btn = $("elapsedSave");
  if (btn) btn.disabled = true;
  try {
    await checked("/api/setting", { setting: newSetting });   // post() は error を投げないので checked を使う
  } catch (e) {
    toast("時間を変更できませんでした: " + e.message, 8000);   // 失敗時はモーダルを開いたまま
    return;
  } finally { if (btn) btn.disabled = false; }
  state.setting = newSetting;
  updateElapsedTimeUI(newSetting.elapsed_time);
  // 設定パネルの経過時間欄だけ合わせる（fillForm(...,true) は書きかけの編集を消すので使わない）
  if ($("f_elapsed_day")) { $("f_elapsed_day").value = day; dirtyFields.delete("f_elapsed_day"); }
  if ($("f_elapsed_period")) { $("f_elapsed_period").value = period; dirtyFields.delete("f_elapsed_period"); }
  closeElapsedModal();
  await poll();
  toast(`時間を「${day}日目・${period}」に変更しました`);   // flash() は設定パネル内で見えない
}

// ---------------------------------------------------------------- モデルシート（背景表示バーの 🤖 から）
function modelResetText(r) {
  const d = new Date(r.reset);
  return isNaN(d) ? "" : `・${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}に回復`;
}
function modelLeftText(r) {
  if (typeof r.left !== "number") return "";
  if (r.left > 0) return `残り${Math.max(1, Math.round(r.left * 100))}%`;   // 既存 renderModels と同じ丸め
  const d = new Date(r.reset);
  const when = isNaN(d) ? "" : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}に回復`;
  return when ? `使い切り（${when}）` : "使い切り";
}
function updateModelSheetLeft() {
  const el = $("modelSheetLeft");
  if (!el) return;
  const sheet = $("modelSheet");
  if (!sheet || sheet.classList.contains("hidden")) return;      // 閉じているときは書かない
  const model = $("ideModel") ? $("ideModel").value : "";
  const r = (ideModels?.models || []).find((x) => x.model === model);
  const txt = r ? modelLeftText(r) : "";
  el.textContent = txt ? `このモデルの残量: ${txt}` : "";
  el.classList.toggle("hidden", !txt);
}
function openModelSheet() {
  if (!state || !state.setting || !state.setting.ide_session) { toast("担当の IDE 会話がありません", 6000); syncActionControlsVisibility(); return; }
  hideReveal();
  $("modelSheet").classList.remove("hidden");
  syncFootBarPlacement();     // #ideModel をシートへ移す（DOM は1つだけ）
  updateModelSheetLeft();
  updateFabState();
  loadModels();               // 開いた時点で残量％を最新化（失敗しても既存表示のまま）
}
function closeModelSheet() {
  const el = $("modelSheet");
  if (!el || el.classList.contains("hidden")) return;
  el.classList.add("hidden");
  syncFootBarPlacement();     // ドロワー下段へ戻す
  updateFabState();
}

function render() {
  const s = state;
  // poll のたびにメッセージを作り直すので、開いている「⋯」は閉じる
  closeMoreMenu();
  $("title").textContent = s.setting?.title || "RP Image Chat";
  $("ragCount").textContent = (s.setting?.rag_collections || []).length;
  updateElapsedTimeUI(s.setting?.elapsed_time);
  document.title = (s.setting?.title || "") + " - RP Image Chat";
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 120;

  const existingMap = new Map();
  for (const child of log.children) {
    if (child.id && child.classList.contains("msg-wrap")) {
      existingMap.set(child.id, child);
    }
  }

  const desiredChildren = [];

  if (s.setting?.summary) {
    let sm = log.querySelector(".summary-card");
    if (!sm || sm.textContent !== s.setting.summary) {
      sm = document.createElement("div");
      sm.className = "summary-card";
      sm.textContent = s.setting.summary;
    }
    desiredChildren.push(sm);
  }

  for (let i = 0; i < s.messages.length; i++) {
    const m = s.messages[i];
    const isLast = i === s.messages.length - 1;
    const sig = messageSignature(m, isLast);
    const mid = m.id ? "msg-" + m.id : null;
    let wrap = mid ? existingMap.get(mid) : null;
    if (wrap && wrap.dataset.sig === sig) {
      desiredChildren.push(wrap);
    } else {
      wrap = createMessageWrap(m, isLast, s);
      wrap.dataset.sig = sig;
      desiredChildren.push(wrap);
    }
  }

  const waitingInput = s.messages.some((m) => m.role === "user" && (m.status === "pending" || m.status === "processing"));
  const waitingMemory = s.messages.some((m) => m.role === "assistant" && (m.memory_status === "pending" || m.memory_status === "processing"));
  const waitingChoices = s.messages.some((m) => m.choices_status === "pending" || m.choices_status === "processing");
  const waitingRefine = s.messages.some((m) => m.image_refine_status === "pending" || m.image_refine_status === "processing");
  if (waitingInput || waitingChoices || waitingRefine) {
    let t = log.querySelector(".typing");
    if (!t) {
      t = document.createElement("div");
      t.className = "typing";
    }
    if (waitingRefine && !waitingInput && !waitingChoices) {
      t.innerHTML = "画像プロンプトを推敲しています<span>.</span><span>.</span><span>.</span>";
    } else if (waitingInput && waitingChoices) {
      t.innerHTML = "応答と選択肢を生成しています<span>.</span><span>.</span><span>.</span>";
    } else if (waitingInput) {
      t.innerHTML = "応答を書いています<span>.</span><span>.</span><span>.</span>";
    } else {
      t.innerHTML = "選択肢を生成しています<span>.</span><span>.</span><span>.</span>";
    }
    desiredChildren.push(t);
    if (s.setting?.ide_session || s.setting?.choices_ide_session) {
      let p = $("progress");
      if (!p) {
        p = document.createElement("div");
        p.id = "progress";
        p.className = "progress";
      }
      p.innerHTML = progressSid === s.id ? progressHtml : "";
      desiredChildren.push(p);
      if (!p.innerHTML) setTimeout(pollProgress, 0);  // 送った直後は2秒待たずに読む
    }
  } else if (progressSid === s.id) progressHtml = "";  // 次の入力で前のターンの表示が一瞬出ないように

  // Sync desired children with log DOM
  const desiredSet = new Set(desiredChildren);
  for (let i = log.childNodes.length - 1; i >= 0; i--) {
    const child = log.childNodes[i];
    if (!desiredSet.has(child)) {
      log.removeChild(child);
    }
  }
  let current = log.firstChild;
  for (const desired of desiredChildren) {
    if (current === desired) {
      current = current.nextSibling;
    } else {
      log.insertBefore(desired, current);
    }
  }

  const pill = $("statusPill");
  pill.className = "pill " + (waitingInput ? "wait" : (waitingChoices || waitingRefine || waitingMemory ? "wait" : "ok"));
  pill.textContent = (waitingInput && waitingChoices)
    ? "AI応答＆候補生成中"
    : (waitingInput ? "AI応答待ち" : (waitingChoices ? "候補生成中" : (waitingRefine ? "タグ推敲中" : (waitingMemory ? "記憶整理中" : "入力待機"))));
  const rotPhase = state?.setting?.rotation?.phase;
  if (!waitingInput && rotPhase && rotPhase !== "armed") {
    pill.className = "pill wait";
    pill.textContent = rotPhase === "cutover" ? "新しいセッションへ切替中…" : "世代交代を準備中…";
  }
  syncSendButton(waitingInput);

  log.classList.toggle("rp", rpMode);
  $("rpToggle").classList.toggle("on", rpMode);
  updateHeaderAndSideStatus();

  if (nearBottom) log.scrollTop = log.scrollHeight;
  updateRpBg();
  renderIde();
}

// 背景表示の画像：いちばん下にいるときは最新の画像、過去ログを読んでいるときは
// 画面の 6 割の高さにかかっている発言のときに描いた画像（その発言に無ければ直前の画像）
let rpBgFile;
let rpTick = 0;
function updateRpBg() {
  if (!rpMode) return;
  const withImg = [...log.querySelectorAll(".msg-wrap[data-img]")];
  let file = null;
  if (withImg.length) {
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    if (atBottom) file = withImg[withImg.length - 1].dataset.img;
    else {
      const line = log.getBoundingClientRect().top + log.clientHeight * 0.6;
      file = withImg[0].dataset.img;  // 最初の画像より前を読んでいるときは最初の画像
      for (const w of withImg) {
        if (w.getBoundingClientRect().top > line) break;
        file = w.dataset.img;
      }
    }
  }
  const curBg = typeof log.style.getPropertyValue === "function" ? log.style.getPropertyValue("--rp-bg") : log.style["--rp-bg"];
  if (file === rpBgFile && curBg) return;
  rpBgFile = file;
  log.style.setProperty("--rp-bg", file ? `url("${imgUrl(file)}")` : "none");
}
log.addEventListener("scroll", () => {
  lastScrollTime = Date.now();
  touchMoved = true;
  if (!rpMode || rpTick) return;
  const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : setTimeout;
  rpTick = raf(() => { rpTick = 0; updateRpBg(); }, 0);
}, { passive: true });

async function poll() {
  if (!sid) return refreshSessions();
  const want = sid;
  try {
    const revParam = (lastRev !== -1 && state !== null) ? "&rev=" + encodeURIComponent(lastRev) : "";
    const s = await api("/api/session?sid=" + encodeURIComponent(want) + revParam);
    // 待っている間にチャンネルを切り替えていたら、前のチャンネルの結果は捨てる
    // （捨てないと前のチャンネルの設定が今のチャンネルの画面に出て、保存すると上書きしてしまう）
    if (want !== sid) return;
    if (s.error) { sid = ""; return refreshSessions(); }
    if (s.unchanged) {
      if (!state) {
        lastRev = -1;
        return poll();
      }
      if ($("statusPill").classList.contains("err")) {
        $("statusPill").className = "pill ok";
        $("statusPill").textContent = "入力待機";
        updateHeaderAndSideStatus();
      }
      markSeen();
      return;
    }
    const key = s.id + ":" + s.rev;  // rev はチャンネルごとの番号なので id と組にして比べる
    if (key !== lastRev) {
      const first = lastRev === -1;
      lastRev = key;
      state = s;
      if (s.setting?.rotation_notice && s.setting.rotation_notice !== lastRotationNotice) {
        lastRotationNotice = s.setting.rotation_notice;
        if (!first) {
          flash(s.setting.rotation_notice);
        }
      }
      render();
      if (first) log.scrollTop = log.scrollHeight;
      fillForm(s.setting, first || !formDirty); formSid = s.id;
      renderChars();
      checkPromptModalRefine(s);
    }
    markSeen();
  } catch (e) {
    $("statusPill").className = "pill err";
    $("statusPill").textContent = "サーバー未接続";
    updateHeaderAndSideStatus();
  }
}

// 応答待ちのあいだ、担当の Antigravity 会話がしていること（ツール呼び出し）を入力中の表示の下に映す
let progressHtml = "", progressSid = "", progressBusy = false;
async function pollProgress() {
  const s = state;
  if ((typeof document !== "undefined" && document.hidden) || progressBusy || !s || s.id !== sid || !(s.setting?.ide_session || s.setting?.choices_ide_session) || !$("progress")) return;
  progressBusy = true;
  try {
    const r = await api("/api/ide/progress?sid=" + encodeURIComponent(s.id));
    if (s.id !== sid) return;
    const items = r.items || [];
    const rows = items.map((it, i) => {
      const last = i === items.length - 1;
      const now = !it.done && last;
      const mark = it.kind === "error" ? "✕" : it.kind === "think" ? "…" : it.done ? "✓" : now ? "●" : "・";
      const cls = "p-row" + (now ? " now" : "") + (it.kind === "error" ? " error" : "");
      return `<div class="${cls}"><span class="p-mark">${mark}</span><span class="p-text">${esc(it.text)}</span>`
        + (it.detail ? `<span class="p-detail" title="${esc(it.detail)}">${esc(it.detail)}</span>` : "") + "</div>";
    });
    if (r.running && (!items.length || items[items.length - 1].done))
      rows.push(`<div class="p-row now"><span class="p-mark">●</span><span class="p-text">次にすることを考えている</span></div>`);
    if (!rows.length && r.note) rows.push(`<div class="p-note">${esc(r.note)}</div>`);
    progressHtml = rows.join("");
    progressSid = s.id;
    const box = $("progress");
    if (box && box.innerHTML !== progressHtml) {
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
      box.innerHTML = progressHtml;
      if (nearBottom) log.scrollTop = log.scrollHeight;
    }
  } catch { /* 読めないときは前の表示のまま */ }
  finally { progressBusy = false; }
}

function hasWaitingInput() {
  return !!state?.messages?.some((m) => m.role === "user" && (m.status === "pending" || m.status === "processing"));
}

// 送信中は「送信」を押せなくし、代わりに「停止」を出す
function syncSendButton(waiting = hasWaitingInput()) {
  const sendBtn = $("sendBtn");
  if (sendBtn) {
    // 応答待ちの間は「送信」と「停止」を入れ替える（スマホでも入力欄が狭くならない）
    sendBtn.classList.toggle("hidden", waiting);
    sendBtn.disabled = waiting || sending || stopping;
    sendBtn.textContent = sending ? "送信中…" : "送信";
  }
  const stopBtn = $("stopBtn");
  if (stopBtn) {
    stopBtn.classList.toggle("hidden", !waiting);
    stopBtn.disabled = stopping || sending;
    stopBtn.textContent = stopping ? "停止中…" : "■ 停止";
  }
}

async function send() {
  closeChoicesAccordions();
  if (sending || stopping || hasWaitingInput()) return;
  const ta = $("messageInput");
  const text = ta.value.trim();
  if (attaching.some((a) => !a.file)) return alert("添付をアップロード中です。終わってから送ってください");
  if ((!text && !attaching.length) || !sid) return;
  sending = true;
  syncSendButton();
  try {
    const r = await post("/api/send", { text, files: attaching.map((a) => a.file) });
    if (r.error) return alert("送れませんでした: " + r.error);
    ta.value = "";
    autoResizeInput();
    lastSelectedChoiceText = "";
    selectedChoiceMap.clear();
    attaching = [];
    renderAttach();
    if (r.session && r.session.id === sid) {
      lastRev = r.session.id + ":" + r.session.rev;
      state = r.session;
      render();
      markSeen();
    } else {
      await poll();
    }
    wakePoll();
    log.scrollTop = log.scrollHeight;
  } catch (e) {
    // 通信が切れたときもボタンが「送信中」のまま固まらないようにする
    alert("送信に失敗しました: " + (e.message || e) + "\n画面を更新して、送れたか確認してください。");
    wakePoll();
  } finally {
    sending = false;
    syncSendButton();
  }
}

// 送信中の入力を取り消し、本文と添付を入力欄に戻す
// id を渡すと、その発言だけを止める（フッターの停止は最新の送信を止める）
async function stopTurn(id = "") {
  if (stopping || sending || !sid) return;
  const targetId = typeof id === "string" ? id : "";
  if (targetId) {
    const target = state?.messages?.find((m) => m.id === targetId);
    if (!target || !(target.status === "pending" || target.status === "processing")) {
      toast("この送信はもう応答待ちではありません");
      return;
    }
  } else if (!hasWaitingInput()) {
    toast("停止できる送信がありません");
    return;
  }
  if (!confirm("送信を停止して、この入力を取り消しますか？\n（AIが生成中でも、届いた応答は取り込みません。本文は入力欄に戻ります）")) return;
  stopping = true;
  syncSendButton();
  try {
    const r = await checked("/api/send/stop", targetId ? { sid, id: targetId } : { sid });
    const ta = $("messageInput");
    let restoredText = false, restoredFiles = false;
    if (r.text && !ta.value.trim()) {
      ta.value = r.text;
      autoResizeInput();
      restoredText = true;
    }
    if (Array.isArray(r.files) && r.files.length && !attaching.length) {
      attaching = r.files.map((f) => ({
        name: f.name,
        preview: /^image\//.test(f.type || "") ? uploadUrl(f.path) : "",
        file: f
      }));
      renderAttach();
      restoredFiles = true;
    }
    // 通信が不安定でも止めた直後に画面から消えるよう、まず返ってきた状態を反映する
    if (r.session && r.session.id === sid) {
      lastRev = r.session.id + ":" + r.session.rev;
      state = r.session;
      render();
      markSeen();
    }
    await poll();
    wakePoll();
    if (restoredText || restoredFiles) {
      const what = [restoredText ? "本文" : "", restoredFiles ? "添付" : ""].filter(Boolean).join("と");
      toast(`送信を停止しました。${what}を入力欄に戻しました。`);
    } else if (r.text || (r.files && r.files.length)) {
      toast("送信を停止しました（入力欄に下書きがあったため、本文・添付は戻していません）");
    } else {
      toast("送信を停止しました。");
    }
  } catch (e) {
    alert("停止できませんでした: " + e.message);
    wakePoll();
  } finally {
    stopping = false;
    syncSendButton();
  }
}

// ---------------------------------------------------------------- 添付
// 選んだ・貼り付けた・ドロップしたファイルはすぐ上げておき、送信でその発言に付ける
const uploadUrl = (path) => "/upload?f=" + encodeURIComponent(path);
const fileSize = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + "MB" : Math.max(1, Math.round(n / 1024)) + "KB";
let attaching = [];  // { name, preview, file(上げ終わったら) }

function filesBox(files) {
  const box = document.createElement("div");
  box.className = "msg-files";
  for (const f of files) {
    if (/^image\/(png|jpeg|gif|webp|bmp)$/.test(f.type)) {
      const img = document.createElement("img");
      img.src = uploadUrl(f.path);
      img.alt = img.title = f.name;
      img.onclick = () => openLightbox(img.src);
      box.appendChild(img);
    } else {
      const a = document.createElement("a");
      a.className = "file";
      a.href = uploadUrl(f.path);
      a.download = f.name;
      a.innerHTML = `📄 ${esc(f.name)} <span class="muted">${fileSize(f.size || 0)}</span>`;
      box.appendChild(a);
    }
  }
  return box;
}

function renderAttach() {
  const list = $("attachList");
  list.classList.toggle("hidden", !attaching.length);
  list.innerHTML = "";
  for (const a of attaching) {
    const chip = document.createElement("div");
    chip.className = "attach-chip" + (a.file ? "" : " busy");
    chip.title = a.file ? a.name : a.name + "（アップロード中）";
    if (a.preview) {
      const img = document.createElement("img");
      img.src = a.preview;
      chip.appendChild(img);
    }
    const nm = document.createElement("span");
    nm.className = "a-name";
    nm.textContent = (a.preview ? "" : "📄 ") + a.name;
    const del = document.createElement("button");
    del.className = "a-del";
    del.textContent = "×";
    del.title = "外す";
    del.onclick = () => { attaching = attaching.filter((x) => x !== a); renderAttach(); };
    chip.append(nm, del);
    list.appendChild(chip);
  }
  updateFooterHeight();
}

function readDataUrl(file) {
  return new Promise((ok, ng) => {
    const fr = new FileReader();
    fr.onload = () => ok(fr.result);
    fr.onerror = () => ng(fr.error);
    fr.readAsDataURL(file);
  });
}

async function addFiles(list) {
  if (!sid) return;
  const ch = sid;
  for (const file of list) {
    if (file.size > 40 * 1048576) { alert(`${file.name} は大きすぎます（40MB まで）`); continue; }
    const name = file.name || (file.type.startsWith("image/") ? "image." + (file.type.split("/")[1] || "png") : "file");
    const a = { name, preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : "", file: null };
    attaching.push(a);
    renderAttach();
    try {
      const r = await checked("/api/upload", { sid: ch, name, data: await readDataUrl(file) });
      a.file = r.file;
    } catch (e) {
      alert(`${name} を添付できませんでした: ${e.message}`);
      attaching = attaching.filter((x) => x !== a);
    }
    renderAttach();
  }
}

// ---------------------------------------------------------------- 返答候補（8択）
function closeChoicesAccordions() {
  openChoicesSet.clear();
  userClosedChoicesSet.clear();
  selectedChoiceMap.clear();
  if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
    document.querySelectorAll(".choices-toggle-btn").forEach((btn) => {
      btn.classList.remove("is-open");
      btn.textContent = (btn.textContent || "").replace(/▲/g, "▼");
      if (typeof btn.setAttribute === "function") btn.setAttribute("aria-expanded", "false");
    });
    document.querySelectorAll(".choices-grid").forEach((grid) => {
      grid.classList.remove("is-open");
    });
    document.querySelectorAll(".choice-item-btn").forEach((btn) => {
      btn.classList.remove("is-selected");
    });
  }
}

function choicesBox(m, targetContainer) {
  const validChoices = (m.choices || []).filter((c) => c && typeof c === "object" && typeof c.text === "string" && c.text.trim());
  if (validChoices.length === 0 && !m.choices_status) return targetContainer || document.createDocumentFragment();

  const msgKey = m.id || ("msg-" + (m.ts || Math.random()));
  const isPending = m.choices_status === "pending" || m.choices_status === "processing";
  const isEroticRegen = isPending && m.choices_type === "erotic";
  const isNormalLoading = isPending && m.choices_type !== "erotic";

  if (isPending && !userClosedChoicesSet.has(msgKey)) {
    openChoicesSet.add(msgKey);
  }
  const isOpen = openChoicesSet.has(msgKey);

  const choicesWrap = targetContainer || document.createElement("div");
  if (!targetContainer) choicesWrap.className = "choices-container";

  // 開閉トグルボタン
  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "choices-toggle-btn" + (isOpen ? " is-open" : "");
  const countLabel = isNormalLoading && validChoices.length === 0 ? " (生成中…)" : "";
  toggleBtn.textContent = `💡選択肢${countLabel} ${isOpen ? "▲" : "▼"}`;
  if (typeof toggleBtn.setAttribute === "function") toggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");

  // 選択肢リスト（グリッド）
  const listEl = document.createElement("div");
  listEl.className = "choices-grid" + (isOpen ? " is-open" : "");

  // ドスケベ再生成ボタン
  const regenBtn = document.createElement("button");
  regenBtn.type = "button";
  regenBtn.className = "choices-regen-btn" + (isEroticRegen ? " is-loading" : "");
  regenBtn.textContent = isEroticRegen ? "⌛ 生成中…" : "🔞";
  regenBtn.title = "ドスケベ再生成";
  regenBtn.disabled = isPending;
  if (typeof regenBtn.setAttribute === "function") regenBtn.setAttribute("aria-busy", isEroticRegen ? "true" : "false");

  toggleBtn.addEventListener("click", () => {
    const nowOpen = !listEl.classList.contains("is-open");
    listEl.classList.toggle("is-open", nowOpen);
    toggleBtn.classList.toggle("is-open", nowOpen);
    const countText = isNormalLoading && validChoices.length === 0 ? " (生成中…)" : "";
    toggleBtn.textContent = `💡選択肢${countText} ${nowOpen ? "▲" : "▼"}`;
    if (typeof toggleBtn.setAttribute === "function") toggleBtn.setAttribute("aria-expanded", nowOpen ? "true" : "false");
    if (nowOpen) {
      openChoicesSet.add(msgKey);
      userClosedChoicesSet.delete(msgKey);
    } else {
      openChoicesSet.delete(msgKey);
      userClosedChoicesSet.add(msgKey);
    }
  });

  regenBtn.addEventListener("click", async (e) => {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    if (regenBtn.disabled || m.choices_status === "pending" || m.choices_status === "processing") return;
    regenBtn.disabled = true;
    regenBtn.textContent = "⌛ 生成中…";
    regenBtn.classList.add("is-loading");
    if (typeof regenBtn.setAttribute === "function") regenBtn.setAttribute("aria-busy", "true");
    m.choices_status = "pending";
    m.choices_type = "erotic";

    // 以前の選択ハイライトをクリア
    if (selectedChoiceMap.has(msgKey)) {
      selectedChoiceMap.delete(msgKey);
      lastSelectedChoiceText = "";
    }
    listEl.querySelectorAll(".choice-item-btn.is-selected").forEach((btn) => btn.classList.remove("is-selected"));

    // アコーディオンを自動で展開
    userClosedChoicesSet.delete(msgKey);
    openChoicesSet.add(msgKey);
    listEl.classList.add("is-open");
    toggleBtn.classList.add("is-open");
    toggleBtn.textContent = `💡選択肢 ▲`;
    if (typeof toggleBtn.setAttribute === "function") toggleBtn.setAttribute("aria-expanded", "true");

    try {
      const curSid = (typeof state !== "undefined" && state && state.id) ? state.id : (typeof sid !== "undefined" ? sid : "");
      const res = await api("/api/message/regenerate-choices", { sid: curSid, id: m.id, type: "erotic" });
      if (res && res.error) {
        alert("再生成エラー: " + res.error);
        delete m.choices_status;
        delete m.choices_type;
        regenBtn.disabled = false;
        regenBtn.textContent = "🔞";
        regenBtn.classList.remove("is-loading");
        if (typeof regenBtn.setAttribute === "function") regenBtn.setAttribute("aria-busy", "false");
      } else {
        if (typeof poll === "function") await poll();
        if (typeof wakePoll === "function") wakePoll();
      }
    } catch (err) {
      alert("通信エラー: " + (err.message || err));
      delete m.choices_status;
      delete m.choices_type;
      regenBtn.disabled = false;
      regenBtn.textContent = "🔞";
      regenBtn.classList.remove("is-loading");
      if (typeof regenBtn.setAttribute === "function") regenBtn.setAttribute("aria-busy", "false");
    }
  });

  if (validChoices.length === 0 && isPending) {
    const loadingNotice = document.createElement("div");
    loadingNotice.className = "choices-loading-hint";
    loadingNotice.textContent = isEroticRegen ? "🔞 ドスケベな返答候補を生成中…" : "💡 返答候補を生成中…";
    listEl.appendChild(loadingNotice);
  }

  validChoices.forEach((choice, cIdx) => {
    const itemBtn = document.createElement("button");
    itemBtn.type = "button";
    const isSelected = selectedChoiceMap.get(msgKey) === cIdx;
    itemBtn.className = "choice-item-btn" + (isSelected ? " is-selected" : "");

    const badge = document.createElement("span");
    badge.className = "choice-label-badge";
    badge.textContent = choice.label || "候補";

    const textSpan = document.createElement("span");
    textSpan.className = "choice-text-body";
    textSpan.textContent = choice.text || "";

    itemBtn.appendChild(badge);
    itemBtn.appendChild(textSpan);

    itemBtn.addEventListener("click", () => {
      // 入力欄に上書きセット＆フォーカス
      const inputEl = $("messageInput") || document.getElementById("message-input");
      if (inputEl) {
        lastSelectedChoiceText = choice.text || "";
        inputEl.value = choice.text || "";
        inputEl.dispatchEvent(new Event("input", { bubbles: true }));
        autoResizeInput();
        inputEl.focus();
        // カーソルを末尾へ移動
        const len = inputEl.value.length;
        inputEl.setSelectionRange(len, len);
      }

      // 選択中ハイライトの更新（単一選択化 & 全体で排他）
      selectedChoiceMap.clear();
      selectedChoiceMap.set(msgKey, cIdx);
      if (typeof document.querySelectorAll === "function") {
        document.querySelectorAll(".choice-item-btn.is-selected").forEach((btn) => {
          btn.classList.remove("is-selected");
        });
      }
      listEl.querySelectorAll(".choice-item-btn").forEach((btn) => {
        btn.classList.toggle("is-selected", btn === itemBtn);
      });
    });

    listEl.appendChild(itemBtn);
  });

  choicesWrap.appendChild(toggleBtn);
  choicesWrap.appendChild(listEl);
  choicesWrap.appendChild(regenBtn);
  return choicesWrap;
}

// ---------------------------------------------------------------- キャラの状態
const asList = (v) => Array.isArray(v) ? v : (v === undefined || v === null || v === "" ? [] : [String(v)]);
const stateText = (list) => list.map((c) => {
  const lines = [`■ ${c.name}`];
  lines.push(`衣装＆露出状態: ${c.outfit || ""}`);
  lines.push(`上着＆露出状態: ${c.outerwear || ""}`);
  lines.push(`下着＆露出状態: ${c.underwear || ""}`);
  const eq = asList(c.equipped_items);
  lines.push(`装具＆大人のおもちゃ: ${eq.length ? eq.join("、") : "なし"}`);
  lines.push(`挿入・接触状態: ${asList(c.action).join("、") || c.penetration || "なし"}`);
  lines.push(`身体痕跡・付着物: ${c.body_fluids || "なし"}`);
  lines.push(`体勢: ${asList(c.pose).join("、") || c.pose || ""}`);
  lines.push(`表情: ${asList(c.expression).join("、")}`);
  lines.push(`背景: ${asList(c.background).join("、") || c.place || ""}`);
  lines.push(`構図: ${asList(c.camera).join("、") || c.camera || ""}`);
  lines.push(`男性器: ${c.penis_present === true ? "あり" : c.penis_present === false ? "なし" : ""}`);
  lines.push(...asList(c.situation).map((x) => `- ${x}`));
  lines.push(`内心: ${c.inner || ""}`);
  return lines.join("\n");
}).join("\n\n");
function parseState(text) {
  const out = [];
  let cur = null;
  let field = null;

  const splitCsv = (str) => {
    if (!str) return [];
    return str.split(/[,、]/).map((x) => x.trim()).filter((x) => x && !["なし", "無し", "特になし", "none", "None", "null"].includes(x));
  };

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const name = line.match(/^[■#]+\s*(.+)$/);
    if (name) {
      cur = {
        name: name[1].trim(),
        outfit: "",
        outerwear: "",
        underwear: "",
        equipped_items: [],
        action: [],
        penetration: "",
        body_fluids: "",
        pose: [],
        expression: [],
        background: [],
        place: "",
        camera: [],
        penis_present: null,
        situation: [],
        inner: ""
      };
      out.push(cur);
      field = null;
      continue;
    }
    if (!cur || !line) continue;
    const outfit = line.match(/^(?:衣装(?:＆露出状態)?|服)\s*[:：]\s*(.*)$/);
    const outerwear = line.match(/^(?:上着(?:＆露出状態)?|アウター)\s*[:：]\s*(.*)$/);
    const underwear = line.match(/^(?:下着(?:＆露出状態)?|インナー)\s*[:：]\s*(.*)$/);
    const equipped = line.match(/^(?:装具＆大人のおもちゃ|装具|大人のおもちゃ|おもちゃ|器具|装備)\s*[:：]\s*(.*)$/);
    const penetration = line.match(/^(?:挿入・結合状態|挿入・結合|挿入|結合)\s*[:：]\s*(.*)$/);
    const action = line.match(/^(?:挿入・接触状態|行為・接触|接触|行為)\s*[:：]\s*(.*)$/);
    const background = line.match(/^(?:背景)\s*[:：]\s*(.*)$/);
    const penis = line.match(/^(?:男性器|penis_present)\s*[:：]\s*(.*)$/);
    const fluids = line.match(/^(?:身体痕跡・付着物|身体痕跡|痕跡・付着物|痕跡|付着物)\s*[:：]\s*(.*)$/);
    const pose = line.match(/^(?:体勢|ポーズ|姿勢)\s*[:：]\s*(.*)$/);
    const expr = line.match(/^(?:表情)\s*[:：]\s*(.*)$/);
    const place = line.match(/^(?:場所|居場所)\s*[:：]\s*(.*)$/);
    const camera = line.match(/^(?:構図|カメラ|アングル)\s*[:：]\s*(.*)$/);
    const inner = line.match(/^内心\s*[:：]\s*(.*)$/);

    if (outfit) { cur.outfit = outfit[1].trim(); field = "outfit"; }
    else if (outerwear) { cur.outerwear = outerwear[1].trim(); field = "outerwear"; }
    else if (underwear) { cur.underwear = underwear[1].trim(); field = "underwear"; }
    else if (equipped) {
      cur.equipped_items = splitCsv(equipped[1]);
      field = "equipped_items";
    }
    else if (action) { cur.action = splitCsv(action[1]); field = "action"; }
    else if (penetration) { cur.penetration = penetration[1].trim(); field = "penetration"; }
    else if (fluids) { cur.body_fluids = fluids[1].trim(); field = "body_fluids"; }
    else if (pose) {
      cur.pose = splitCsv(pose[1]);
      field = "pose";
    }
    else if (background) {
      cur.background = splitCsv(background[1]);
      field = "background";
    }
    else if (penis) {
      const pv = penis[1].trim();
      cur.penis_present = ["あり", "true", "yes", "はい"].includes(pv) ? true
        : ["なし", "false", "no", "いいえ"].includes(pv) ? false : null;
      field = null;
    }
    else if (expr) {
      cur.expression = splitCsv(expr[1]);
      field = "expression";
    }
    else if (place) {
      const pl = place[1].trim();
      cur.place = ["なし", "無し", "特になし", "none", "None", "null"].includes(pl) ? "" : pl;
      field = "place";
    }
    else if (camera) {
      cur.camera = splitCsv(camera[1]);
      field = "camera";
    }
    else if (inner) { cur.inner = inner[1]; field = "inner"; }

    // 箇条書き行（ラベルの下に書かれた行）
    else if (/^[-・*•]\s+/.test(line)) {
      const v = line.replace(/^[-・*•]\s*/, "");
      if (field === "expression") {
        if (!["なし", "無し", "特になし", "none", "None", "null"].includes(v)) {
          cur.expression.push(v);
        }
      }
      else if (field === "inner") cur.inner += (cur.inner ? "\n" : "") + v;
      else cur.situation.push(v);
    }
    // ラベルなしの継続行
    else if (field === "inner") cur.inner += (cur.inner ? "\n" : "") + line;
    else if (field === "expression") {
      if (!["なし", "無し", "特になし", "none", "None", "null"].includes(line)) {
        cur.expression.push(line);
      }
    }
    else cur.situation.push(line);
  }
  return out;
}
async function openState(m, msgs) {
  let curState = m.state;
  if (!curState && sid && m.id) {
    try {
      const res = await (await fetch(`/api/message/state?sid=${encodeURIComponent(sid)}&mid=${encodeURIComponent(m.id)}&fallback=1`)).json();
      if (res && res.ok && Array.isArray(res.state) && res.state.length > 0) {
        curState = res.state;
        m.state = curState;
      }
    } catch (e) {
      console.warn("fetch state error:", e);
    }
  }
  // この応答に無ければ、それより前に記録した状態から始める（サーバーの state_before と同じ）
  const prev = curState || msgs.slice(0, msgs.indexOf(m)).reverse().find((x) => x.role === "assistant" && Array.isArray(x.state))?.state || [];
  openText("キャラの状態（この応答のとき）",
    "「■ 名前」の下に「衣装＆露出状態:」「上着＆露出状態:」「下着＆露出状態:」「装具＆大人のおもちゃ:」「挿入・結合状態:」「身体痕跡・付着物:」「体勢:」「表情:」「場所:」「構図:」と、今の状態を「- 」で1行ずつ、「内心:」の後に内心を書く。キャラを消すときはその塊ごと消す。保存すると、次の入力で AI がこの状態を読む。",
    stateText(prev) || "■ キャラ名\n衣装＆露出状態: \n上着＆露出状態: \n下着＆露出状態: \n装具＆大人のおもちゃ: なし\n挿入・結合状態: なし\n身体痕跡・付着物: なし\n体勢: \n表情: \n場所: \n構図: \n- 今の状態\n内心: ",
    async (text) => {
      const parsed = parseState(text);
      await checked("/api/message/state", { sid, id: m.id, state: parsed });
      m.state = parsed;
      await poll();
    });
}

let promptUndoValue = null;
let isPromptRefining = false;

async function checkPromptModalRefine(s) {
  if (!currentPromptMsg || $("promptModal").classList.contains("hidden")) return;
  const liveM = (s.messages || []).find((m) => m.id === currentPromptMsg.id);
  if (!liveM) return;
  currentPromptMsg = liveM;
  if (isPromptRefining) {
    if (liveM.image_refine_status !== "pending" && liveM.image_refine_status !== "processing") {
      isPromptRefining = false;
      if ($("promptRefine")) {
        $("promptRefine").disabled = false;
        $("promptRefine").textContent = "✨ タグ推敲";
      }
      if (liveM.image_refine_error) {
        toast("推敲エラー: " + liveM.image_refine_error);
      } else if (liveM.image?.refined_prompt) {
        $("promptEdit").value = liveM.image.refined_prompt;
        updatePromptQuickTagsActive();
        if ($("promptUndo")) $("promptUndo").classList.remove("hidden");
        toast("✨ プロンプトの推敲が完了しました");
      } else if (sid && liveM.id) {
        try {
          const res = await (await fetch(`/api/message/image_meta?sid=${encodeURIComponent(sid)}&mid=${encodeURIComponent(liveM.id)}`)).json();
          if (res && res.ok && res.image?.refined_prompt) {
            if (!liveM.image) liveM.image = {};
            Object.assign(liveM.image, res.image);
            $("promptEdit").value = res.image.refined_prompt;
            updatePromptQuickTagsActive();
            if ($("promptUndo")) $("promptUndo").classList.remove("hidden");
            toast("✨ プロンプトの推敲が完了しました");
            return;
          }
        } catch (e) {
          console.warn("fetch image_meta fallback error:", e);
        }
        toast("推敲エラー: 有効なプロンプトタグが得られませんでした");
      } else {
        toast("推敲エラー: 推敲に失敗しました");
      }
    }
  }
}

async function openPrompt(m) {
  currentPromptMsg = m;
  promptUndoValue = null;
  if (m.image && sid && m.id) {
    try {
      const res = await (await fetch(`/api/message/image_meta?sid=${encodeURIComponent(sid)}&mid=${encodeURIComponent(m.id)}`)).json();
      if (res && res.ok && res.image) {
        if (!("refined_prompt" in res.image)) {
          delete m.image.refined_prompt;
        }
        Object.assign(m.image, res.image);
      }
    } catch (e) {
      console.warn("fetch image_meta error:", e);
    }
  }
  const isBusy = (m.image_refine_status === "pending" || m.image_refine_status === "processing");
  isPromptRefining = (m.image_refine_mode === "modal" && isBusy);
  $("promptEdit").value = m.image?.prompt || "";
  updatePromptQuickTagsActive();
  const who = m.image?.chars?.length ? `固定キャラ: ${m.image.chars.join(", ")}（外見タグは再生成時も今の設定で付く）\n\n` : "";
  const prevP = m.image?.prev_prompt ? `推敲前 prompt:\n${m.image.prev_prompt}\n\n` : "";
  $("promptFinal").textContent = prevP + who + (m.image?.final_positive
    ? `最終 positive:\n${m.image.final_positive}\n\nnegative:\n${m.image.final_negative}\n\nseed: ${m.image.seed}`
    : (m.image?.seed ? `seed: ${m.image.seed}` : ""));
  if ($("promptUndo")) {
    if (m.image?.prev_prompt && m.image?.prev_prompt !== m.image?.prompt) {
      $("promptUndo").classList.remove("hidden");
    } else {
      $("promptUndo").classList.add("hidden");
    }
  }
  if ($("promptRefine")) {
    $("promptRefine").disabled = isBusy;
    $("promptRefine").textContent = isPromptRefining ? "✨ 推敲中…" : "✨ タグ推敲";
  }
  $("promptModal").classList.remove("hidden");
  updateFabState();
}
function flash(msg) {
  $("settingMsg").textContent = msg;
  setTimeout(() => ($("settingMsg").textContent = ""), 3000);
}
// 設定パネルを閉じていても見える知らせ（タップで閉じる。ms = 0 なら次の知らせまで出したまま）
let toastTimer = null;
function toast(msg, ms = 6000) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  if (ms) toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
}
$("toast").onclick = () => $("toast").classList.add("hidden");

// 発言を書き換えたら、担当の Antigravity 会話も巻き戻す（チャンネルの設定がオンなら、サーバーが revert: true を返す）
async function revertIde(r, chSid) {
  if (!r?.revert) return;
  toast("Antigravity の会話も巻き戻しています…", 0);
  try {
    const v = await checked("/api/ide/revert", { sid: chSid });
    toast(v.skipped ? "Antigravity の会話は巻き戻していません: " + v.skipped : v.msg + "（次の入力では設定と会話を全部送り直します）",
      v.skipped ? 10000 : 6000);
  } catch (e) { toast("アプリの発言は変えましたが、Antigravity の会話は巻き戻せませんでした:\n" + e.message, 15000); }
}

function openEdit(m) {
  editTarget = m;
  $("editTitle").textContent = m.role === "user" ? "過去の入力を編集" : "AI応答を改変";
  $("editHint").textContent = m.role === "user"
    ? "保存すると、この入力以降の会話を削除し、編集後の入力をAIへ送ります。"
    : "保存すると、この応答がAIに渡す過去の文脈でも書き換わります。後続の会話は残ります。";
  $("editText").value = m.text || "";
  $("editSave").textContent = m.role === "user" ? "編集して再送" : "改変を適用";
  $("editModal").classList.remove("hidden");
  updateFabState();
  $("editText").focus();
}

async function saveEdit() {
  const m = editTarget;
  const text = $("editText").value.trim();
  if (!m || !text) return;
  if (m.role === "user" && !confirm("この入力以降の会話を削除して再応答を待ちますか？")) return;
  const button = $("editSave");
  button.disabled = true;
  const ch = sid;
  let r;
  try {
    r = await checked(m.role === "user" ? "/api/message/edit" : "/api/message/alter", { sid, id: m.id, text });
    $("editModal").classList.add("hidden");
    updateFabState();
    editTarget = null;
    await poll();
  } catch (e) { alert(e.message); return; }
  finally { button.disabled = false; }
  await revertIde(r, ch);
}

const CAT_LABELS = {
  calling: "🏷️ 呼称",
  rule: "📜 ルール",
  taboo: "🚫 タブー",
  rule_taboo: "⚖️ 規則・禁忌",
  item: "🎁 アイテム",
  status: "💫 状態",
  item_status: "💍 アイテム",
  promise: "🤙 約束",
  char_fact: "🗝️ キャラ",
  user_fact: "👤 好み",
  relationship: "💖 関係",
  event: "⚡ 事件",
};

let ragMemFilter = "all";
let ragAllMemories = [];

function setupRagFilterTabs() {
  const container = $("ragMemFilterTabs");
  if (!container || container._bound) return;
  container._bound = true;
  container.querySelectorAll(".rag-filter-btn").forEach(btn => {
    btn.onclick = () => {
      container.querySelectorAll(".rag-filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      ragMemFilter = btn.getAttribute("data-cat") || "all";
      renderRagMemories();
    };
  });
}

async function openRag() {
  if (!sid) return;
  $("ragModal").classList.remove("hidden");
  ragEditingEntry = null;
  ragMemEditingId = null;
  $("ragEditor").classList.add("hidden");
  $("ragMemEditor").classList.add("hidden");
  updateFabState();
  setupRagFilterTabs();
  await loadRagCollections();
  switchRagTab(ragActiveTab);
}

function switchRagTab(tab) {
  ragActiveTab = tab;
  const tabMem = $("ragTabMemory");
  const memView = $("ragMemView");
  const memEditor = $("ragMemEditor");
  const customView = $("ragCustomView");
  const editor = $("ragEditor");

  if (tab === "memory") {
    if (tabMem) tabMem.classList.add("selected");
    document.querySelectorAll("#ragCollections .rag-collection").forEach(el => el.classList.remove("selected"));
    if (memView) memView.classList.remove("hidden");
    if (memEditor) memEditor.classList.add("hidden");
    if (customView) customView.classList.add("hidden");
    if (editor) editor.classList.add("hidden");
    loadRagMemories();
  } else {
    if (tabMem) tabMem.classList.remove("selected");
    if (memView) memView.classList.add("hidden");
    if (memEditor) memEditor.classList.add("hidden");
    if (customView) customView.classList.remove("hidden");
    renderRagCollections();
    renderRagEntries();
  }
}

async function loadRagMemories() {
  if (!sid) return;
  const list = $("ragMemEntries");
  if (!list) return;
  list.innerHTML = `<div class="muted" style="padding:12px;">記憶を読み込み中...</div>`;
  try {
    const res = await checked("/api/rag/memories?session_id=" + encodeURIComponent(sid));
    renderRagMemories(res.memories || []);
  } catch (e) {
    list.innerHTML = `<div class="danger" style="padding:12px;">記憶の取得に失敗しました: ${esc(e.message)}</div>`;
  }

  // Check if regeneration is actively running in background
  try {
    const st = await checked("/api/rag/memories/regenerate/status?session_id=" + encodeURIComponent(sid));
    if (st && (st.status === "running" || st.status === "processing")) {
      startRegenStatusPolling();
    }
  } catch (_) {}
}

function renderRagMemories(memories = null) {
  if (memories !== null) {
    ragAllMemories = memories;
  }
  const list = $("ragMemEntries");
  if (!list) return;
  list.innerHTML = "";
  const displayList = ragMemFilter === "all" ? ragAllMemories : ragAllMemories.filter(m => {
    const c = m.category || "promise";
    if (c === ragMemFilter) return true;
    if (ragMemFilter === "rule" && (c === "rule" || c === "rule_taboo")) return true;
    if (ragMemFilter === "taboo" && (c === "taboo" || c === "rule_taboo")) return true;
    if (ragMemFilter === "rule_taboo" && (c === "rule" || c === "taboo" || c === "rule_taboo")) return true;
    if (ragMemFilter === "item" && (c === "item" || c === "item_status")) return true;
    if (ragMemFilter === "status" && (c === "status" || c === "item_status")) return true;
    if (ragMemFilter === "item_status" && (c === "item" || c === "status" || c === "item_status")) return true;
    return false;
  });
  if (!displayList || !displayList.length) {
    list.innerHTML = `<p class="muted" style="padding:12px;">${ragMemFilter === "all" ? "記憶はまだありません。会話が進むと自動で蓄積されます（手動追加も可能）。" : "このカテゴリの記憶はありません。"}</p>`;
    return;
  }
  for (const mem of displayList) {
    const cat = mem.category || "promise";
    const card = document.createElement("div");
    card.className = `rag-entry rag-entry-${cat}`;

    const header = document.createElement("div");
    header.style.display = "flex";
    header.style.alignItems = "center";
    header.style.justifyContent = "space-between";
    header.style.marginBottom = "6px";

    const badges = document.createElement("div");
    badges.style.display = "flex";
    badges.style.alignItems = "center";
    badges.style.gap = "6px";

    const catBadge = document.createElement("span");
    catBadge.className = `badge-cat badge-cat-${cat}`;
    catBadge.textContent = CAT_LABELS[cat] || cat || "記憶";

    const srcBadge = document.createElement("span");
    srcBadge.className = `badge-source badge-source-${mem.source || "auto"}`;
    srcBadge.textContent = mem.source === "manual" ? "手動" : "自動";

    badges.append(catBadge, srcBadge);

    if (mem.status === "fulfilled") {
      const fBadge = document.createElement("span");
      fBadge.className = "badge-status-fulfilled";
      fBadge.textContent = "達成済";
      badges.appendChild(fBadge);
    }

    const meta = document.createElement("span");
    meta.className = "rag-mem-meta";
    const metaParts = [];
    if (mem.turn_idx != null) metaParts.push(`T${mem.turn_idx}`);
    if (mem.elapsed_time) {
      if (typeof mem.elapsed_time === "object") {
        const d = mem.elapsed_time.day != null ? `${mem.elapsed_time.day}日目` : "";
        const p = mem.elapsed_time.period || "";
        metaParts.push(d && p ? `${d}・${p}` : (d || p));
      } else {
        metaParts.push(mem.elapsed_time);
      }
    }
    if (mem.character_name) metaParts.push(`[${mem.character_name}]`);
    meta.textContent = metaParts.join(" ");

    badges.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "rag-entry-actions";

    if (mem.mid) {
      const jumpBtn = document.createElement("button");
      jumpBtn.type = "button";
      jumpBtn.className = "sub rag-jump-btn";
      jumpBtn.title = "この記憶が抽出された会話へジャンプ";
      jumpBtn.textContent = "💬 会話へ";
      jumpBtn.onclick = () => {
        $("ragModal").classList.add("hidden");
        scrollToMessage(mem.mid);
      };
      actions.appendChild(jumpBtn);
    }

    const editBtn = document.createElement("button");
    editBtn.textContent = "編集";
    editBtn.onclick = () => openRagMemEditor(mem);

    const delBtn = document.createElement("button");
    delBtn.textContent = "削除";
    delBtn.onclick = async () => {
      if (!confirm(`この記憶を削除しますか？\n「${mem.content}」`)) return;
      try {
        await checked(`/api/rag/memories/${encodeURIComponent(mem.id)}?session_id=${encodeURIComponent(sid)}`, null, "DELETE");
        await loadRagMemories();
      } catch (e) {
        alert(e.message);
      }
    };

    actions.append(editBtn, delBtn);
    header.append(badges, actions);

    const body = document.createElement("div");
    body.className = "rag-mem-content";
    body.style.whiteSpace = "pre-wrap";
    body.textContent = mem.content;

    card.append(header, body);

    if (mem.history && mem.history.length > 0) {
      const histDetails = document.createElement("details");
      histDetails.className = "rag-mem-history";
      const histSummary = document.createElement("summary");
      histSummary.textContent = `変更履歴 (${mem.history.length}件)`;
      histDetails.appendChild(histSummary);
      for (const h of mem.history) {
        const hDiv = document.createElement("div");
        hDiv.className = "rag-mem-history-item";
        let hEt = "";
        if (h.elapsed_time) {
          if (typeof h.elapsed_time === "object") {
            const hd = h.elapsed_time.day != null ? `${h.elapsed_time.day}日目` : "";
            const hp = h.elapsed_time.period || "";
            hEt = ` [${hd && hp ? `${hd}・${hp}` : (hd || hp)}]`;
          } else {
            hEt = ` [${h.elapsed_time}]`;
          }
        }
        const hStatus = h.status === "fulfilled" ? " (達成済)" : "";
        const ts = h.archived_at || h.replaced_at;
        const hTime = ts ? new Date(ts * 1000).toLocaleString() : "";
        const hMeta = document.createElement("span");
        hMeta.className = "muted";
        hMeta.textContent = `${hTime}${hEt}${hStatus}: `;
        const hBody = document.createElement("div");
        hBody.textContent = h.content || "";
        hDiv.append(hMeta, hBody);
        histDetails.appendChild(hDiv);
      }
      card.appendChild(histDetails);
    }

    list.appendChild(card);
  }
}

function openRagMemEditor(mem = null) {
  ragMemEditingId = mem ? mem.id : null;
  $("ragMemEditorTitle").textContent = mem ? "記憶の編集" : "手動で記憶を追加";
  const defaultCat = ragMemFilter !== "all" ? ragMemFilter : "promise";
  $("ragMemCategory").value = mem ? (mem.category || "promise") : defaultCat;
  if ($("ragMemStatus")) {
    $("ragMemStatus").value = mem ? (mem.status || "active") : "active";
  }
  $("ragMemContent").value = mem ? mem.content : "";
  $("ragMemView").classList.add("hidden");
  $("ragMemEditor").classList.remove("hidden");
  $("ragMemContent").focus();
}

function closeRagMemEditor() {
  ragMemEditingId = null;
  $("ragMemEditor").classList.add("hidden");
  $("ragMemView").classList.remove("hidden");
}

async function saveRagMem() {
  const category = $("ragMemCategory").value;
  const content = $("ragMemContent").value.trim();
  const status = $("ragMemStatus") ? $("ragMemStatus").value : "active";
  if (!content) {
    alert("記憶の内容を入力してください。");
    return;
  }
  try {
    if (ragMemEditingId) {
      await checked(`/api/rag/memories/${encodeURIComponent(ragMemEditingId)}`, {
        session_id: sid,
        category,
        content,
        status
      }, "PUT");
    } else {
      await checked("/api/rag/memories", {
        session_id: sid,
        category,
        content,
        status
      }, "POST");
    }
    closeRagMemEditor();
    await loadRagMemories();
  } catch (e) {
    alert(e.message);
  }
}

function startRegenStatusPolling() {
  const btn = $("ragMemRegenBtn");
  if (btn) btn.disabled = true;
  const progressBox = $("ragMemProgress");
  const progressBar = $("ragMemProgressBar");
  const progressText = $("ragMemProgressText");

  if (progressBox) progressBox.classList.remove("hidden");

  if (ragMemRegenTimer) clearInterval(ragMemRegenTimer);
  ragMemRegenTimer = setInterval(async () => {
    try {
      const st = await checked("/api/rag/memories/regenerate/status?session_id=" + encodeURIComponent(sid));
      if (!st) return;
      const pct = Math.min(100, Math.max(0, st.progress || 0));
      if (progressBar) progressBar.style.width = pct + "%";
      if (progressText) progressText.textContent = `記憶を再構築中... (${st.processed_chunks || 0}/${st.total_chunks || 0} チャンク) - 記憶 ${st.memories_count || 0}件`;

      if (st.status === "completed" || st.status === "done" || st.status === "idle") {
        clearInterval(ragMemRegenTimer);
        ragMemRegenTimer = null;
        if (btn) btn.disabled = false;
        if (progressBox) progressBox.classList.add("hidden");
        flash("記憶の再生成が完了しました");
        await loadRagMemories();
      } else if (st.status === "error") {
        clearInterval(ragMemRegenTimer);
        ragMemRegenTimer = null;
        if (btn) btn.disabled = false;
        if (progressBox) progressBox.classList.add("hidden");
        alert("記憶の再生成でエラーが発生しました: " + (st.error || ""));
      }
    } catch (e) {
      // Continue polling
    }
  }, 1500);
}

async function regenerateRagMemories() {
  if (!sid) return;
  if (!confirm("現在の会話ログを再分析して記憶を一括再生成しますか？\n（手動で追加した記憶は保持されます）")) return;

  const btn = $("ragMemRegenBtn");
  btn.disabled = true;
  const progressBox = $("ragMemProgress");
  const progressBar = $("ragMemProgressBar");
  const progressText = $("ragMemProgressText");

  progressBox.classList.remove("hidden");
  progressBar.style.width = "0%";
  progressText.textContent = "記憶の再構築を開始中...";

  try {
    await checked("/api/rag/memories/regenerate", { session_id: sid });
    startRegenStatusPolling();
  } catch (e) {
    alert("再生成の開始に失敗しました: " + e.message);
    btn.disabled = false;
    progressBox.classList.add("hidden");
  }
}

async function loadRagCollections() {
  try {
    ragCollections = (await checked("/api/rag")).collections || [];
    if (ragSelected && !ragCollections.some((c) => c.id === ragSelected)) ragSelected = null;
    renderRagCollections();
    if (ragActiveTab === "custom") {
      await renderRagEntries();
    }
  } catch (e) { alert(e.message); }
}

function renderRagCollections() {
  const list = $("ragCollections");
  list.innerHTML = "";
  for (const col of ragCollections) {
    const row = document.createElement("div");
    row.className = "rag-collection" + (ragActiveTab === "custom" && col.id === ragSelected ? " selected" : "");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = (state?.setting?.rag_collections || []).includes(col.id);
    check.title = "このチャンネルで参照";
    check.onchange = async () => {
      const ids = new Set(state?.setting?.rag_collections || []);
      if (check.checked) ids.add(col.id); else ids.delete(col.id);
      try { await checked("/api/rag/select", { sid, ids: [...ids] }); await poll(); }
      catch (e) { check.checked = !check.checked; alert(e.message); }
    };
    const label = document.createElement("span");
    label.textContent = `${col.name} (${col.entryCount})`;
    label.onclick = () => {
      ragActiveTab = "custom";
      ragSelected = col.id;
      ragEditingEntry = null;
      $("ragEditor").classList.add("hidden");
      switchRagTab("custom");
    };
    const del = document.createElement("button");
    del.textContent = "×";
    del.title = "コレクションを削除";
    del.onclick = async () => {
      if (!confirm(`「${col.name}」を削除しますか？`)) return;
      try { await checked("/api/rag/delete", { id: col.id }); await poll(); await loadRagCollections(); }
      catch (e) { alert(e.message); }
    };
    row.append(check, label, del);
    list.appendChild(row);
  }
}

function openRagEditor(entry = null) {
  ragEditingEntry = entry?.id || null;
  $("ragEntryTitle").value = entry?.title || "";
  $("ragEntryContent").value = entry?.content || "";
  $("ragEditor").classList.remove("hidden");
  $("ragEntryTitle").focus();
}

async function renderRagEntries() {
  const list = $("ragEntries");
  list.innerHTML = "";
  if (!ragSelected) { list.textContent = "左のコレクションを選択してください。"; return; }
  try {
    const col = (await checked("/api/rag/collection?id=" + encodeURIComponent(ragSelected))).collection;
    const add = document.createElement("button");
    add.textContent = "＋ エントリ追加";
    add.onclick = () => openRagEditor();
    list.appendChild(add);
    for (const entry of col.entries || []) {
      const card = document.createElement("div");
      card.className = "rag-entry";
      const title = document.createElement("strong");
      title.textContent = entry.title;
      const body = document.createElement("div");
      body.textContent = entry.content;
      const edit = document.createElement("button");
      edit.textContent = "編集";
      edit.onclick = () => openRagEditor(entry);
      const del = document.createElement("button");
      del.textContent = "削除";
      del.onclick = async () => {
        if (!confirm(`「${entry.title}」を削除しますか？`)) return;
        try { await checked("/api/rag/entry/delete", { id: ragSelected, entry_id: entry.id }); await loadRagCollections(); }
        catch (e) { alert(e.message); }
      };
      const actions = document.createElement("div");
      actions.className = "rag-entry-actions";
      actions.append(edit, del);
      card.append(title, actions, body);
      list.appendChild(card);
    }
  } catch (e) { list.textContent = e.message; }
}

async function saveRagEntry() {
  const title = $("ragEntryTitle").value.trim();
  const content = $("ragEntryContent").value.trim();
  if (!title || !content || !ragSelected) return;
  try {
    await checked("/api/rag/entry/save", { id: ragSelected, entry_id: ragEditingEntry, title, content });
    ragEditingEntry = null;
    $("ragEditor").classList.add("hidden");
    await loadRagCollections();
  } catch (e) { alert(e.message); }
}

$("sendBtn").onclick = send;
if ($("stopBtn")) $("stopBtn").onclick = () => stopTurn();
$("sendBtn").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
    closeChoicesAccordions();
  }
});
$("messageInput").addEventListener("keydown", (e) => {
  // Enter で送信するのは PC だけ。スマホは仮想キーボードなので Enter は改行のままにして、
  // 送信は「送信」ボタンにまかせる。
  // 日本語IMEの変換確定 Enter では送らない（isComposing / keyCode 229 は変換中）
  if (e.key !== "Enter") return;
  if (e.isComposing || e.keyCode === 229) return;
  if (e.shiftKey) return;                 // Shift+Enter は改行（PC）
  if (isMobileLayout()) return;           // スマホは改行
  e.preventDefault();
  send();
});
$("messageInput").addEventListener("input", () => {
  autoResizeInput();
  if (selectedChoiceMap.size > 0 && $("messageInput").value !== lastSelectedChoiceText) {
    selectedChoiceMap.clear();
    lastSelectedChoiceText = "";
    if (typeof document.querySelectorAll === "function") {
      document.querySelectorAll(".choice-item-btn.is-selected").forEach((btn) => {
        btn.classList.remove("is-selected");
      });
    }
  }
});
$("messageInput").addEventListener("focus", () => {
  setTypingInput(true);
  updateFabState();
});
$("messageInput").addEventListener("blur", () => {
  setTypingInput(false);
  updateFabState();
});
// 添付ボタン上フリック送信 & タップでファイル選択
function setupAttachFlickSend() {
  const btn = $("attachBtn");
  const input = $("attachInput");
  if (!btn || !input) return;

  let touchStartX = 0;
  let touchStartY = 0;
  let touchMaxDist = 0;
  let activeTouchId = null;
  let isTracking = false;
  let isLaunching = false;
  let suppressClickUntil = 0;

  let resetTimer = null;
  let launchTimer1 = null;
  let launchTimer2 = null;

  function clearTimers() {
    if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
    if (launchTimer1) { clearTimeout(launchTimer1); launchTimer1 = null; }
    if (launchTimer2) { clearTimeout(launchTimer2); launchTimer2 = null; }
  }

  // 誤判定防止の数値基準
  const FLICK_THRESHOLD_Y = 35; // 上方向に35px以上でフリックとみなす
  const MAX_SWAY_X = 40;        // 横ブレ40px以上はキャンセル
  const TAP_THRESHOLD = 10;     // 移動量10px未満は通常タップ

  function resetBtnStyle(animated = true) {
    clearTimers();
    if (animated) {
      btn.style.transition = "transform 0.2s cubic-bezier(0.2, 0.9, 0.3, 1), background-color 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease, color 0.2s ease, opacity 0.2s ease";
    } else {
      btn.style.transition = "none";
    }
    btn.style.transform = "";
    btn.style.backgroundColor = "";
    btn.style.borderColor = "";
    btn.style.color = "";
    btn.style.boxShadow = "";
    btn.style.opacity = "1";
    btn.style.pointerEvents = "";
    const hint = btn.querySelector(".attach-flick-hint");
    if (hint) hint.style.opacity = "";
    if (animated) {
      resetTimer = setTimeout(() => {
        resetTimer = null;
        if (!isTracking && !isLaunching) {
          btn.style.transition = "";
        }
      }, 210);
    } else {
      btn.style.transition = "";
    }
  }

  function getActiveTouch(touchList) {
    if (!touchList || !touchList.length) return null;
    if (activeTouchId !== null) {
      for (let i = 0; i < touchList.length; i++) {
        if (touchList[i].identifier === activeTouchId) {
          return touchList[i];
        }
      }
    }
    return touchList[0];
  }

  btn.addEventListener("touchstart", (e) => {
    const btnTouches = (e.targetTouches && e.targetTouches.length > 0) ? e.targetTouches : e.touches;
    if (btnTouches && btnTouches.length > 1) {
      isTracking = false;
      activeTouchId = null;
      resetBtnStyle(false);
      return;
    }
    clearTimers();
    if (isLaunching) {
      isLaunching = false;
      resetBtnStyle(false);
    }

    const touch = (e.targetTouches && e.targetTouches[0]) || (e.changedTouches && e.changedTouches[0]) || (e.touches && e.touches[0]);
    if (!touch) return;
    activeTouchId = (touch.identifier !== undefined) ? touch.identifier : null;
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
    touchMaxDist = 0;
    isTracking = true;
    btn.style.transition = "none";
  }, { passive: true });

  btn.addEventListener("touchmove", (e) => {
    if (!isTracking || isLaunching) return;
    const btnTouches = (e.targetTouches && e.targetTouches.length > 0) ? e.targetTouches : e.touches;
    if (btnTouches && btnTouches.length > 1) {
      isTracking = false;
      activeTouchId = null;
      resetBtnStyle(true);
      return;
    }

    const touch = getActiveTouch(e.touches) || getActiveTouch(e.targetTouches) || getActiveTouch(e.changedTouches);
    if (!touch) return;

    const deltaX = touch.clientX - touchStartX;
    const deltaY = touch.clientY - touchStartY;
    const currentDist = Math.hypot(deltaX, deltaY);
    if (currentDist > touchMaxDist) {
      touchMaxDist = currentDist;
    }

    const upDist = -deltaY;
    const absDeltaX = Math.abs(deltaX);
    const isSwayed = absDeltaX >= MAX_SWAY_X;

    // 上方向へのリアルタイムスライド（transform: translateY）
    let visualY = 0;
    if (deltaY < 0) {
      visualY = Math.max(-75, deltaY);
    }
    btn.style.transform = visualY !== 0 ? `translateY(${visualY}px)` : "";

    // 送信ブルー（#3b5bdb: rgb(59, 91, 219)）への滑らかな変化
    let progress = 0;
    if (upDist > 0 && !isSwayed) {
      progress = Math.min(1, upDist / FLICK_THRESHOLD_Y);
    }

    if (progress > 0) {
      const r = Math.round(44 + (59 - 44) * progress);
      const g = Math.round(44 + (91 - 44) * progress);
      const b = Math.round(50 + (219 - 50) * progress);
      btn.style.backgroundColor = `rgb(${r}, ${g}, ${b})`;
      btn.style.borderColor = progress > 0.6 ? "#5c7cfa" : "";
      btn.style.color = progress > 0.4 ? "#ffffff" : "";
      if (progress >= 0.7) {
        btn.style.boxShadow = `0 2px 12px rgba(59, 91, 219, ${(0.45 * progress).toFixed(2)})`;
      } else {
        btn.style.boxShadow = "";
      }
      const hint = btn.querySelector(".attach-flick-hint");
      if (hint) hint.style.opacity = (0.55 + 0.45 * progress).toFixed(2);
    } else {
      btn.style.backgroundColor = "";
      btn.style.borderColor = "";
      btn.style.color = "";
      btn.style.boxShadow = "";
      const hint = btn.querySelector(".attach-flick-hint");
      if (hint) hint.style.opacity = "";
    }

    // タップ判定閾値（10px）以上動いた場合のみスクロール防止
    if (upDist >= TAP_THRESHOLD && e.cancelable) {
      e.preventDefault();
    }
  }, { passive: false });

  btn.addEventListener("touchend", (e) => {
    if (!isTracking) return;
    isTracking = false;
    if (isLaunching) return;

    const touch = getActiveTouch(e.changedTouches) || getActiveTouch(e.touches) || getActiveTouch(e.targetTouches);
    activeTouchId = null;
    if (!touch) {
      resetBtnStyle(true);
      return;
    }

    const deltaX = touch.clientX - touchStartX;
    const deltaY = touch.clientY - touchStartY;
    const upDist = -deltaY;
    const absDeltaX = Math.abs(deltaX);
    const endDist = Math.hypot(deltaX, deltaY);
    if (endDist > touchMaxDist) {
      touchMaxDist = endDist;
    }

    const isFlick = (upDist >= FLICK_THRESHOLD_Y) && (absDeltaX < MAX_SWAY_X);
    const isTap = (touchMaxDist < TAP_THRESHOLD) && (endDist < TAP_THRESHOLD);

    if (isFlick) {
      // 分岐A: フリック成功
      suppressClickUntil = Date.now() + 600;
      if (e.cancelable) e.preventDefault();

      const messageInput = $("messageInput");
      const hasContent = Boolean((messageInput && messageInput.value.trim()) || attaching.length);
      const isSendDisabled = Boolean($("sendBtn")?.disabled);

      if (hasContent && !isSendDisabled) {
        // [あり] 上空へシュッと飛び立ちながらフェードアウト
        clearTimers();
        isLaunching = true;
        btn.style.pointerEvents = "none";
        btn.style.transition = "transform 0.22s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.22s ease-out";
        btn.style.transform = "translateY(-95px)";
        btn.style.opacity = "0";

        // メッセージ送信
        send();

        // 元の位置にスッと戻る
        launchTimer1 = setTimeout(() => {
          launchTimer1 = null;
          btn.style.transition = "none";
          btn.style.transform = "";
          btn.style.backgroundColor = "";
          btn.style.borderColor = "";
          btn.style.color = "";
          btn.style.boxShadow = "";
          const hint = btn.querySelector(".attach-flick-hint");
          if (hint) hint.style.opacity = "";
          void btn.offsetHeight;
          btn.style.transition = "opacity 0.15s ease-in";
          btn.style.opacity = "1";
          launchTimer2 = setTimeout(() => {
            launchTimer2 = null;
            btn.style.transition = "";
            btn.style.pointerEvents = "";
            isLaunching = false;
          }, 160);
        }, 220);
      } else {
        // [なし（空）] 送信せず元の位置・色へリセット
        resetBtnStyle(true);
      }
    } else if (isTap) {
      // 分岐B: 通常タップ（移動量がほぼ 0px）
      suppressClickUntil = 0; // 直前のフリックやキャンセルの抑制期間を解除し、このタップを通す
      resetBtnStyle(false);
      // 通常の click イベントが発火して input.click() を呼ぶ
    } else {
      // 分岐B: キャンセル（少し動かして戻した、または横ブレ・中途半端なドラッグ）
      suppressClickUntil = Date.now() + 600;
      if (e.cancelable) e.preventDefault();
      resetBtnStyle(true);
    }
  });

  btn.addEventListener("touchcancel", () => {
    isTracking = false;
    isLaunching = false;
    activeTouchId = null;
    suppressClickUntil = Date.now() + 600;
    resetBtnStyle(true);
  });

  // PC（マウス操作）: 従来の「クリックしてファイル添付」のみを維持
  btn.onclick = (e) => {
    if (Date.now() < suppressClickUntil) {
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
    input.click();
  };

  btn.addEventListener("click", (e) => {
    if (Date.now() < suppressClickUntil) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);
}
setupAttachFlickSend();
$("attachInput").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
$("messageInput").addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); addFiles(files); }
});
for (const el of [document.querySelector("footer"), log]) {
  if (!el || typeof el.addEventListener !== "function") continue;
  el.addEventListener("dragover", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    document.querySelector("footer").classList.add("drag");
  });
  el.addEventListener("dragleave", () => document.querySelector("footer").classList.remove("drag"));
  el.addEventListener("drop", (e) => {
    document.querySelector("footer").classList.remove("drag");
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    addFiles([...e.dataTransfer.files]);
  });
}
setupElapsedTimeDropdown("elapsedTimeBtn", "elapsedTimeDropdown");
setupElapsedTimeDropdown("sideElapsedTimeBtn", "sideElapsedTimeDropdown");
document.addEventListener("click", () => {
  if ($("elapsedTimeDropdown")) $("elapsedTimeDropdown").classList.add("hidden");
  if ($("sideElapsedTimeDropdown")) $("sideElapsedTimeDropdown").classList.add("hidden");
});
$("settingsToggle").onclick = () => {
  if (!$("settingsPanel").classList.toggle("hidden")) { loadOptions(); loadIdeSessions(); updatePushStatus(); }
  updateFabState();
};
if ($("settingsCloseBtn")) {
  $("settingsCloseBtn").onclick = () => {
    $("settingsPanel").classList.add("hidden");
    updateFabState();
  };
}
$("sessionFilter").oninput = renderSessions;
$("ragToggle").onclick = openRag;
$("ragClose").onclick = () => { $("ragModal").classList.add("hidden"); updateFabState(); };
$("ragCreate").onclick = async () => {
  const name = $("ragName").value.trim();
  if (!name) return;
  try {
    const r = await checked("/api/rag/create", { name });
    $("ragName").value = "";
    ragSelected = r.collection.id;
    await loadRagCollections();
  } catch (e) { alert(e.message); }
};
$("ragName").onkeydown = (e) => { if (e.key === "Enter") $("ragCreate").click(); };
$("ragEntrySave").onclick = saveRagEntry;
$("ragEditCancel").onclick = () => $("ragEditor").classList.add("hidden");
if ($("ragTabMemory")) $("ragTabMemory").onclick = () => switchRagTab("memory");
if ($("ragMemAddBtn")) $("ragMemAddBtn").onclick = () => openRagMemEditor();
if ($("ragMemRegenBtn")) $("ragMemRegenBtn").onclick = regenerateRagMemories;
if ($("ragMemCancel")) $("ragMemCancel").onclick = closeRagMemEditor;
if ($("ragMemSave")) $("ragMemSave").onclick = saveRagMem;
$("editSave").onclick = saveEdit;
$("editCancel").onclick = () => { editTarget = null; $("editModal").classList.add("hidden"); updateFabState(); };
$("sideToggle").onclick = () => {
  document.body.classList.toggle("side-open");
  updateFabState();
};
$("sideBackdrop").onclick = () => {
  document.body.classList.remove("side-open");
  updateFabState();
};
$("rpToggle").onclick = () => {
  rpMode = !rpMode;
  setStorage("rpMode", rpMode ? "1" : "0");
  render();
  updateHeaderAndSideStatus();
};

if ($("sideTitle")) {
  $("sideTitle").onclick = () => {
    if (state) renameChannel(sid, state.setting?.title || "");
  };
}

function setTypingInput(on) {
  if (document.body && document.body.classList) document.body.classList.toggle("typing-input", !!on);
  if (on) hideReveal();
  syncActionControlsVisibility();
}

if ($("mobileFab")) {
  $("mobileFab").onclick = () => {
    // 開いている最前面のレイヤーを1つ閉じる。何も無ければドロワーを開く
    if (closeTopmostLayer()) { updateFabState(); return; }
    if (document.body && document.body.classList) document.body.classList.add("side-open");
    updateFabState();
  };
}
if ($("mobileTurnPrev")) {
  $("mobileTurnPrev").onclick = () => {
    hideReveal();
    syncActionControlsVisibility();
    jumpToPrevAssistantReply();
  };
}
if ($("mobileTurnNext")) {
  $("mobileTurnNext").onclick = () => {
    hideReveal();
    syncActionControlsVisibility();
    jumpToNextAssistantReply();
  };
}
if ($("mobileActionToggle")) {
  $("mobileActionToggle").onclick = () => {
    const ae = document.activeElement;                       // ★15: 入力の focus が残っていると押しても無反応になる
    const footer = document.querySelector("footer");
    if (ae && footer && footer.contains(ae) && typeof ae.blur === "function") ae.blur();
    const el = $("mobileReveal");
    // もう一度押したら閉じる（トグル）。開いたままだと「操作を表示」に戻れなくなるため
    if (el && !el.classList.contains("hidden")) hideReveal();
    else showReveal();
    syncActionControlsVisibility();
  };
}
if ($("revealRpToggle")) $("revealRpToggle").onclick = () => { hideReveal(); $("rpToggle").click(); syncActionControlsVisibility(); };
if ($("revealRag"))      $("revealRag").onclick      = () => { hideReveal(); openRag(); syncActionControlsVisibility(); };
if ($("revealSettings")) $("revealSettings").onclick = () => { hideReveal(); if ($("settingsToggle")) $("settingsToggle").click(); syncActionControlsVisibility(); };
if ($("revealModel"))    $("revealModel").onclick    = () => { hideReveal(); openModelSheet(); syncActionControlsVisibility(); };

if ($("mobileElapsedChip")) $("mobileElapsedChip").onclick = openElapsedModal;
if ($("elapsedCancel")) $("elapsedCancel").onclick = closeElapsedModal;
if ($("elapsedSave"))   $("elapsedSave").onclick   = saveElapsedModal;
if ($("elapsedDayMinus")) $("elapsedDayMinus").onclick = () => setElapsedDay(elapsedDraft.day - 1);
if ($("elapsedDayPlus"))  $("elapsedDayPlus").onclick  = () => setElapsedDay(elapsedDraft.day + 1);
if ($("elapsedDayInput")) $("elapsedDayInput").addEventListener("change", () => setElapsedDay(parseInt($("elapsedDayInput").value, 10)));
if ($("modelSheetClose")) $("modelSheetClose").onclick = closeModelSheet;
// バーの外側（画面のどこか）をタップしたら閉じる
if ($("revealBackdrop")) $("revealBackdrop").onclick = () => { hideReveal(); syncActionControlsVisibility(); };
if ($("sideRpToggle")) {
  $("sideRpToggle").onclick = () => $("rpToggle").click();
}
if ($("sideRagToggle")) {
  $("sideRagToggle").onclick = () => {
    if (document.body && document.body.classList) document.body.classList.remove("side-open");
    updateFabState();
    openRag();
  };
}
if ($("sideSettingsToggle")) {
  $("sideSettingsToggle").onclick = () => {
    if (document.body && document.body.classList) document.body.classList.remove("side-open");
    if ($("settingsPanel")) {
      const isHidden = $("settingsPanel").classList.toggle("hidden");
      if (!isHidden) { loadOptions(); loadIdeSessions(); updatePushStatus(); }
    }
    updateFabState();
  };
}

if (typeof document.querySelectorAll === "function") {
  document.querySelectorAll(".modal").forEach((m) => {
    m.addEventListener("click", (e) => {
      if (e.target === m) {
        m.classList.add("hidden");
        updateFabState();
      }
    });
  });
}
if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("resize", () => {
    syncFootBarPlacement();
    autoResizeInput();
    updateFooterHeight();
    updateHeaderAndSideStatus();   // 360px 境界などの保険（★20）
  });
}
function onLayoutChange() {
  if (!isMobileLayout()) {
    hideReveal();                                  // 幅が戻ったらモバイル専用の小物は畳む
    if ($("modelSheet")) $("modelSheet").classList.add("hidden");
    if ($("elapsedModal")) $("elapsedModal").classList.add("hidden");
  }
  syncFootBarPlacement();
  updateFabState();
  updateHeaderAndSideStatus();
  syncInputPlaceholder();
}
if (mqMobile) {
  if (mqMobile.addEventListener) mqMobile.addEventListener("change", onLayoutChange);
  else if (mqMobile.addListener) mqMobile.addListener(onLayoutChange);
}
if (mqTiny) {
  if (mqTiny.addEventListener) mqTiny.addEventListener("change", () => updateHeaderAndSideStatus());
  else if (mqTiny.addListener) mqTiny.addListener(() => updateHeaderAndSideStatus());
}
if (log && typeof log.addEventListener === "function") {
  const onTouchStart = (x, y) => {
    isPointerDown = true;
    touchStartX = x;
    touchStartY = y;
    touchMoved = false;
  };

  log.addEventListener("pointerdown", (e) => {
    onTouchStart(e.clientX, e.clientY);
  });

  log.addEventListener("touchstart", (e) => {
    if (e.touches && e.touches.length > 0) {
      onTouchStart(e.touches[0].clientX, e.touches[0].clientY);
    }
  }, { passive: true });

  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("pointermove", (e) => {
      if (!isPointerDown) return;
      if (Math.hypot(e.clientX - touchStartX, e.clientY - touchStartY) > 8) {
        touchMoved = true;
      }
    });

    window.addEventListener("touchmove", (e) => {
      if (e.touches && e.touches.length > 0) {
        const t = e.touches[0];
        if (Math.hypot(t.clientX - touchStartX, t.clientY - touchStartY) > 8) {
          touchMoved = true;
        }
      }
    }, { passive: true });

    const onPointerEnd = () => {
      isPointerDown = false;
    };
    const onPointerCancel = () => {
      isPointerDown = false;
      touchMoved = true;
    };
    window.addEventListener("pointerup", onPointerEnd);
    window.addEventListener("pointercancel", onPointerCancel);
    window.addEventListener("touchend", onPointerEnd);
    window.addEventListener("touchcancel", onPointerCancel);
  }

  log.addEventListener("click", (e) => {
    // If user was swiping or dragging to scroll, or if chatLog scrolled within 300ms, ignore
    if (touchMoved || (lastScrollTime && Date.now() - lastScrollTime < 300)) {
      touchMoved = false;
      return;
    }

    // Check if click was on scrollbar (desktop)
    if (e.clientX && log.clientWidth && typeof log.getBoundingClientRect === "function") {
      const rect = log.getBoundingClientRect();
      if (e.clientX >= rect.left + log.clientWidth) return;
    }

    // Clicks inside a message wrap, summary card, typing indicator, progress, choices, or interactive controls are not background
    if (e.target && typeof e.target.closest === "function") {
      if (e.target.closest(".msg-wrap, .summary-card, .typing, .progress, #progress, .choices-box, button, a, input, textarea, select")) {
        return;
      }
    }

    // 背景表示モードでは、チャット面タップで背景画像を最大化する
    if (!rpMode) return;
    if (!rpBgFile) return;

    openLightbox(imgUrl(rpBgFile));
  });
}
const footerEl = document.querySelector("footer");
if (footerEl && typeof footerEl.addEventListener === "function") {
  footerEl.addEventListener("focusin", () => {
    setTypingInput(true);
    updateFabState();
  });
  footerEl.addEventListener("focusout", (e) => {
    if (!footerEl.contains(e.relatedTarget)) {
      setTypingInput(false);
      updateFabState();
    }
  });
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => updateFooterHeight()).observe(footerEl);
  }
}
$("settingSave").onclick = async () => {
  // 念のため：フォームの中身が別チャンネルのものなら保存しない
  if (!state || formSid !== sid || state.id !== sid) { flash("設定の読み込みが追いついていません。少し待ってからもう一度保存してください"); poll(); return; }
  try {
    const r = await post("/api/setting", { setting: readForm() });
    formDirty = false;
    dirtyFields.clear();
    flash(r.agents && !r.agents.ok ? "保存しました。ただし AGENTS.md: " + r.agents.msg : "保存しました");
    loadAgents();
    await poll();
    refreshSessions();
  } catch (e) { flash("画像設定のJSONエラー: " + e.message); }
};
// 新規: 動作・画像設定のみを引き継いで、真っ白な新しいチャンネルを始める
$("newSession").onclick = async () => {
  let base = null;
  if (state && state.id === sid) {
    try { base = formSid === sid ? readForm() : JSON.parse(JSON.stringify(state.setting)); }
    catch { base = JSON.parse(JSON.stringify(state.setting)); }
  }
  const title = prompt("新しいチャンネル名", "新しいチャンネル");
  if (title === null) return;
  const r = await api("/api/session/new", base ? { title, setting: base } : { title });
  await refreshSessions();
  await switchTo(r.sid);
  $("settingsPanel").classList.remove("hidden");  // ✅ Q2 確定：常にパネルを開く
  updateFabState();
  flash("新しいチャンネルを作成しました");
  if (meta.app?.new_channel_ide === false) return;
  // アプリ全体の設定でオンなら、Antigravity の新規会話も作って担当にする
  toast("Antigravity の新規会話を作っています…", 0);
  try {
    const n = await checked("/api/ide/new", { sid: r.sid, target: "both" });
    toast(`Antigravity の新規会話を作って担当にしました: ${n.title}（${String(n.ide).slice(0, 8)}）`);
    if (sid === r.sid) await poll();
    refreshSessions(); loadIdeSessions();
  } catch (e) { toast("チャンネルは作りましたが、Antigravity の新規会話は作れませんでした:\n" + e.message, 15000); }
};
$("dupBtn").onclick = async () => {
  const r = await api("/api/session/new", { copy_from: sid });
  await refreshSessions();
  await switchTo(r.sid);
};
$("resetBtn").onclick = () => {
  if (confirm("このチャンネルの会話とAIメモを消去して、最初のメッセージから始めます。よろしい？")) post("/api/reset").then(poll);
};
$("deleteSession").onclick = async () => {
  if (!confirm(`チャンネル「${state?.setting?.title}」を削除？（data/trash に移動）`)) return;
  await post("/api/session/delete");
  sid = "";
  await refreshSessions();
};
$("lightbox").onclick = () => closeLightbox();
if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "Esc") {
      const lb = $("lightbox");
      if (lb && !lb.classList.contains("hidden")) {
        if (typeof e.preventDefault === "function") e.preventDefault();
        e.stopPropagation();
        closeLightbox();
      }
    }
  });
}
function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildPromptTagPattern(tag) {
  const words = tag.trim().split(/\s+/).map(escapeRegExp);
  const tagWords = words.join("[\\s_-]+");
  const weight = "(?:\\s*:\\s*[-+]?\\d+(?:\\.\\d+)?)*";
  const bracketed = `(?:\\(+|\\[+|\\{+)+\\s*\\b${tagWords}\\b${weight}\\s*(?:\\)+|\\]+|\\}+)+`;
  const bare = `\\b${tagWords}\\b${weight}`;
  return `(?:${bracketed}|${bare})`;
}

function hasPromptTag(prompt, tag) {
  if (!prompt || !tag) return false;
  const pattern = buildPromptTagPattern(tag);
  return new RegExp(pattern, "i").test(prompt);
}

function removePromptTag(prompt, tag) {
  if (!prompt || !tag) return prompt || "";
  const tagPattern = buildPromptTagPattern(tag);
  const tagRegex = new RegExp(tagPattern, "gi");

  const rawSegments = prompt.split(",");
  const newSegments = [];

  for (let i = 0; i < rawSegments.length; i++) {
    const seg = rawSegments[i];
    if (!hasPromptTag(seg, tag)) {
      newSegments.push(seg);
      continue;
    }
    const cleanedSeg = seg.replace(tagRegex, "").replace(/[^\S\r\n]+/g, " ");
    const trimmed = cleanedSeg.trim();
    if (trimmed.length > 0 && !/^[\s()[\]{}:.0-9+-,;!]*$/.test(trimmed)) {
      newSegments.push(cleanedSeg);
    }
  }

  if (newSegments.length === 0) return "";

  let result = newSegments.join(",");
  result = result.replace(/^[ \t]*,[ \t]*/, "").replace(/[ \t]*,[ \t]*$/, "");
  if (!prompt.startsWith(" ") && result.startsWith(" ")) {
    result = result.trimStart();
  }
  if (!result.trim() || result.trim() === ",") return "";
  return result;
}

function addPromptTag(prompt, tag) {
  const p = (prompt || "").trim();
  if (!p) return tag;
  const base = p.replace(/[\s,]+$/, "");
  if (!base) return tag;
  return `${base}, ${tag}`;
}

function updatePromptQuickTagsActive() {
  const textarea = $("promptEdit");
  if (!textarea) return;
  const currentText = textarea.value || "";
  const tagButtons = document.querySelectorAll("#promptQuickTags .prompt-tag-btn");
  tagButtons.forEach((btn) => {
    const tag = btn.dataset.tag;
    if (tag && hasPromptTag(currentText, tag)) {
      btn.classList.add("is-active");
    } else {
      btn.classList.remove("is-active");
    }
  });
}

function initPromptQuickTags() {
  const container = $("promptQuickTags");
  const textarea = $("promptEdit");
  if (textarea) {
    textarea.addEventListener("input", () => {
      updatePromptQuickTagsActive();
    });
  }
  if (container) {
    container.addEventListener("click", (e) => {
      const btn = e.target.closest(".prompt-tag-btn");
      if (!btn) return;
      const tag = btn.dataset.tag;
      if (!tag) return;
      if (!textarea) return;

      const currentVal = textarea.value || "";
      if (hasPromptTag(currentVal, tag)) {
        textarea.value = removePromptTag(currentVal, tag);
      } else {
        textarea.value = addPromptTag(currentVal, tag);
        textarea.scrollTop = textarea.scrollHeight;
      }
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      updateFabState();
    });
  }
}

$("promptCancel").onclick = () => {
  $("promptModal").classList.add("hidden");
  isPromptRefining = false;
  promptUndoValue = null;
  updateFabState();
};
if ($("promptUndo")) {
  $("promptUndo").onclick = () => {
    const fallback = currentPromptMsg?.image?.prev_prompt;
    if (promptUndoValue !== null) {
      $("promptEdit").value = promptUndoValue;
      promptUndoValue = null;
      $("promptUndo").classList.add("hidden");
      updatePromptQuickTagsActive();
    } else if (fallback) {
      $("promptEdit").value = fallback;
      $("promptUndo").classList.add("hidden");
      updatePromptQuickTagsActive();
    }
  };
}
if ($("promptRefine")) {
  $("promptRefine").onclick = async () => {
    if (!currentPromptMsg) return;
    promptUndoValue = $("promptEdit").value;
    isPromptRefining = true;
    $("promptRefine").disabled = true;
    $("promptRefine").textContent = "✨ 推敲中…";
    try {
      await post("/api/prompt/refine", { id: currentPromptMsg.id, prompt: promptUndoValue.trim() });
      await poll();
    } catch (e) {
      toast("推敲リクエストに失敗しました: " + e.message);
      isPromptRefining = false;
      $("promptRefine").disabled = false;
      $("promptRefine").textContent = "✨ タグ推敲";
    }
  };
}
$("promptRegen").onclick = async () => {
  if (!currentPromptMsg) return;
  const newPrompt = $("promptEdit").value.trim();
  if (!currentPromptMsg.image) currentPromptMsg.image = {};
  currentPromptMsg.image.prompt = newPrompt;
  currentPromptMsg.image.manual = true;
  delete currentPromptMsg.image.refined_prompt;
  await post("/api/regen", { id: currentPromptMsg.id, prompt: newPrompt });
  $("promptModal").classList.add("hidden");
  isPromptRefining = false;
  promptUndoValue = null;
  updateFabState();
  poll();
  wakePoll();
};

(async () => {
  initPromptQuickTags();
  syncFootBarPlacement();
  autoResizeInput();
  syncInputPlaceholder();
  updateFooterHeight();
  updateFabState();
  updateHeaderAndSideStatus();
  initPushNotifications();
  await loadOptions();
  updatePushStatus().catch(console.error);
  loadModels();
  await refreshSessions();

  const params = new URLSearchParams(window.location.search);
  const targetSid = params.get("sid");
  const targetMid = params.get("mid");
  if (targetSid) {
    if (targetSid !== sid) {
      await switchTo(targetSid);
    } else {
      await poll();
    }
    if (targetMid) {
      scrollToMessage(targetMid);
    }
    try {
      window.history.replaceState({}, document.title, window.location.pathname);
    } catch {}
  } else {
    await poll();
  }

  let isPolling = false;

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        wakePoll();
        refreshSessions();
      }
    });
  }

  async function pollLoop() {
    if (isPolling) return;
    isPolling = true;
    try {
      await poll();
    } finally {
      isPolling = false;
    }
    if (pollTimer) clearTimeout(pollTimer);
    const isWaiting = state?.messages?.some((m) =>
      (m.role === "user" && (m.status === "pending" || m.status === "processing")) ||
      (m.role === "assistant" && (m.memory_status === "pending" || m.memory_status === "processing")) ||
      (m.choices_status === "pending" || m.choices_status === "processing") ||
      (m.image_refine_status === "pending" || m.image_refine_status === "processing") ||
      (m.image && (m.image.status === "queued" || m.image.status === "generating"))
    );
    const hidden = typeof document !== "undefined" && document.hidden;
    const delay = hidden ? (isWaiting ? 1000 : 5000) : (isWaiting ? 100 : 1500);
    pollTimer = setTimeout(pollLoop, delay);
  }

  pollLoopFn = pollLoop;

  pollLoop();
  setInterval(pollProgress, 800);
  setInterval(refreshSessions, 3000);
  setInterval(() => {
    if (typeof document !== "undefined" && !document.hidden && !$("settingsPanel").classList.contains("hidden")) {
      loadIdeSessions();
    }
  }, 5000);
})();
