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
// 前/次ボタンで移動した直後は、下端付近でも自動で最下段へ戻さない。
// ユーザーが自分で最下段まで戻るか、送信したら解除する。
let navHoldBottom = false;
let lastAutoScrolledAsstId = "";            // スマホで本文表示時に上端合わせした最新AI応答のID
const _initParams = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
let sid = (_initParams && _initParams.get("sid")) || getStorage("sid") || "";
let state = null;
let lastRev = -1;
let lastRotationNotice = "";
let formSid = "";  // 設定フォームに今入っているのはどのチャンネルの設定か
const openChoicesSet = new Set();       // 開いているアコーディオンのメッセージIDを保持
const userClosedChoicesSet = new Set(); // ユーザーが手動で閉じたアコーディオンのメッセージIDを保持
const selectedChoiceMap = new Map();    // メッセージID -> 選択中のchoiceインデックス
const displayChoicesMode = new Map();   // msgKey -> "normal" | "erotic"
let renderLastAsstId = "";              // 画面表示時点の「最後のAI応答」のID（候補はここだけ出す）
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
let ragMemRegenBefore = null;   // 一括再生成の開始時件数（進捗・完了表示の差分用）
let meta = { app: {} };
let decensorInfo = null;  // 婉曲→直接の辞書（GET /api/decensor の結果）
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
// 入力操作におけるスマホ判定。
// スマホ（幅768px以下かつタッチ等のモバイル端末）は Enter 改行（送信は中央/右の送信ボタン）。
// PC（マウス・物理キーボード環境、またはデスクトップ幅）は常に Enter 送信 / Shift+Enter 改行。
function isMobileInputDevice() {
  if (!isMobileLayout()) return false;
  if (typeof window === "undefined") return true;
  const hasTouch = (typeof navigator !== "undefined" && navigator.maxTouchPoints > 0) || ("ontouchstart" in window);
  const hasCoarse = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  const isPureMouse = !!(window.matchMedia && window.matchMedia("(pointer: fine) and (hover: hover)").matches && !hasCoarse);
  if (isPureMouse) return false;
  return hasTouch || hasCoarse;
}
const SHORT_STATUS = {
  "サーバー未接続": "未接続", "AI応答待ち": "応答待ち", "AI応答＆候補生成中": "応答＋候補",
  "候補生成中": "候補", "タグ推敲中": "推敲", "記憶整理中": "記憶"
};

// 入力欄のプレースホルダ。PC は Enter 送信（Shift+Enter で改行）、スマホは Enter 改行。
const PLACEHOLDER_DESKTOP = "ユーザーのセリフや行動を入力";
const PLACEHOLDER_MOBILE = "ユーザーのセリフや行動を入力";
function syncInputPlaceholder() {
  const ta = $("messageInput");
  if (ta) ta.placeholder = isMobileLayout() ? PLACEHOLDER_MOBILE : PLACEHOLDER_DESKTOP;
}

// ---------------------------------------------------------------- 「ツール」（旧「操作を表示」）と reveal（モバイル）
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
// 中央の「送信」を押している間は true。押した瞬間に入力欄のフォーカスが外れても
// 役割（送信/ツール）と見た目を変えないため、isFooterFocused でも入力中とみなす。
let actionTogglePressed = false;
function isFooterFocused() {
  if (actionTogglePressed) return true;
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
// 中央ボタン（#mobileActionToggle）は「入力中だけ」送信になる。
//   input 中 → send（送信） / それ以外 → tools（ツール） / メニューを開いていれば close
// 下中央は左右どちらの親指でも届く位置なので、左手持ちでも送信できる。
// 入力中でもツールを使いたいときは、入力を止めれば（キーボードを閉じれば）ツールに戻る。
let actionToggleMode = "tools";

// 送れる中身があるか（送信ボタンの disabled と同じ条件）。
// attaching はこの関数より後で宣言されるので、初期化前でも落ちないようにする
function hasSendableContent() {
  const ta = $("messageInput");
  if (ta && ta.value.trim()) return true;
  try { return Boolean(attaching && attaching.length); } catch { return false; }
}

// 今は送れない状態か（送信中/停止中/応答待ち）
function isSendBlocked() {
  const waiting = typeof hasWaitingInput === "function" ? hasWaitingInput() : false;
  return Boolean(sending || stopping || waiting);
}

// 何度呼んでも安全。開いている間は必ず隠す（z64 が modal より上なので描画させない）
function syncActionControlsVisibility() {
  // ここでは rpMode を見ない。reveal の 🖼️ で背景表示を切ると「自分の土台」ごと消えて
  // 二度と押せなくなる（しかも localStorage に残って再読込でも戻らない）ため。
  const base = isMobileLayout() && !isOverlayOpen();
  const typing = isFooterFocused();
  // メニュー（reveal）は入力中だけ畳む。中央の「ツール」は常に押せる
  if (!base || typing) hideReveal();
  const reveal = $("mobileReveal");
  const revealVisible = !!(reveal && !reveal.classList.contains("hidden"));
  // 前/次はFABと同じ条件（モバイル・入力中でない・オーバーレイなし）で出す。
  // 入力中も中央の「ツール」は残すので、ナビだけを .nav-off で抜く
  const cluster = $("mobileTurnCluster");
  if (cluster) {
    cluster.classList.toggle("hidden", !base);
    cluster.classList.toggle("nav-off", !(base && !typing));
  }
  const toggle = $("mobileActionToggle");
  if (toggle) {
    toggle.classList.toggle("hidden", !base);
    // 入力中（入力欄にフォーカスがあり、文字か添付がある）だけ「送信」に入れ替わる。
    // 応答待ちの間は送れないので「ツール」に戻す（押しても無反応のボタンを出さない）
    const sendMode = base && typing && !revealVisible && !isSendBlocked() && hasSendableContent();
    actionToggleMode = (base && revealVisible) ? "close" : (sendMode ? "send" : "tools");
    const label = actionToggleMode === "close" ? "✕ 閉じる"
                : actionToggleMode === "send" ? "送信" : "ツール";
    if (toggle.textContent !== label) toggle.textContent = label;
    // 4つの操作メニューを開いている間は「閉じる」を青く点灯させる。
    toggle.classList.toggle("on", base && revealVisible);
    toggle.classList.toggle("send-mode", actionToggleMode === "send");
    const hint = actionToggleMode === "send" ? "送信"
               : actionToggleMode === "close" ? "ツールを閉じる" : "ツールを開く";
    if (toggle.title !== hint) toggle.title = hint;
    toggle.setAttribute("aria-label", hint);
  }
  // チャンネルが開いていればいつでもモデル選択（AG / Google）を出す
  const model = $("revealModel");
  if (model) model.classList.toggle("hidden", !(state && state.setting));
  // 🧑 キャラ（状態）は「直近の応答」用の近道。過去の応答の状態は ⋯ から直す
  const stateBtn = $("revealState");
  if (stateBtn) stateBtn.classList.toggle("hidden", !latestStateTarget());
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
// スマホの仮想キーボードは、iOS では「見えている範囲（visual viewport）」だけを縮める。
// position:fixed のモーダルはレイアウト基準のままなので、モーダルの下半分（保存ボタンや
// 入力欄）がキーボードの裏に丸ごと隠れてしまう。実寸を CSS 変数で渡して、CSS 側で
// モーダルを「今見えている範囲」にぴったり合わせる（style.css の .modal / body.kb-open）。
let kbWasOpen = false;
const rafOnce = (fn) => {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(fn);
  else setTimeout(fn, 16);
};
function syncViewportVars() {
  const root = typeof document !== "undefined" ? document.documentElement : null;
  if (!root || !root.style) return;
  const vv = typeof window !== "undefined" ? window.visualViewport : null;
  const layoutH = root.clientHeight || (typeof window !== "undefined" ? window.innerHeight : 0) || 0;
  const vh = vv && vv.height ? Math.round(vv.height) : Math.round(layoutH);
  const vtop = vv && typeof vv.offsetTop === "number" ? Math.round(vv.offsetTop) : 0;
  root.style.setProperty("--app-vvh", vh + "px");
  root.style.setProperty("--app-vvtop", Math.max(0, vtop) + "px");
  // レイアウトがキーボード分だけ縮まない（＝キーボードが重なる）端末だけ kb-open にする。
  // resizes-content が効く端末では 100dvh 自体が縮むので、何もする必要がない。
  const kbOpen = isMobileLayout() && layoutH > 0 && (layoutH - vh) > 120;
  if (document.body && document.body.classList) document.body.classList.toggle("kb-open", kbOpen);
  // キーボードが出た瞬間に、フォーカス中の入力欄が画面の外に残らないように寄せる
  if (kbOpen && !kbWasOpen) rafOnce(keepFocusedVisible);
  kbWasOpen = kbOpen;
}
// キーボードで縮んだあと、フォーカス中の入力欄（記憶の内容など）を必ず見える位置へ
function keepFocusedVisible() {
  const ae = typeof document !== "undefined" ? document.activeElement : null;
  if (!ae || !isMobileLayout() || typeof ae.scrollIntoView !== "function") return;
  if (typeof document.querySelector !== "function" || !document.querySelector(".modal:not(.hidden)")) return;
  try { ae.scrollIntoView({ block: "nearest", inline: "nearest" }); }
  catch { try { ae.scrollIntoView(); } catch { /* 対応していない環境では何もしない */ } }
}
function bindViewportWatchers() {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") return;
  const vv = window.visualViewport;
  if (vv && typeof vv.addEventListener === "function") {
    let queued = false;
    const onViewportChange = () => {
      if (queued) return;
      queued = true;
      rafOnce(() => { queued = false; syncViewportVars(); });
    };
    vv.addEventListener("resize", onViewportChange);
    vv.addEventListener("scroll", onViewportChange);
  }
  syncViewportVars();
}

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
  const hitsModal = $("ragHitsModal");
  if (hitsModal && !hitsModal.classList.contains("hidden")) {
    hitsModal.classList.add("hidden");
    return true;
  }
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
  if (result && result.error && !(Array.isArray(result.models) && result.models.length) && !(result.status && typeof result.status === "object")) {
    const err = new Error(result.error);
    if (result.code) err.code = result.code;
    throw err;
  }
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
  navHoldBottom = false;
  lastAutoScrolledAsstId = "";
  state = null;
  rpBgFile = undefined;
  formDirty = false;
  dirtyFields.clear();
  formSid = "";
  newCharRow = false;
  lastSelectedChoiceText = "";
  selectedChoiceMap.clear();
  openChoicesSet.clear();
  displayChoicesMode.clear();
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
  if (!all.length) list.innerHTML = `<span class="muted">ファイルは未設定です（使うときだけ、${esc(meta.app?.system_root || "systemfiles")} の下に「フォルダ名/ファイル名.md」を置くか、上の「変更…」でフォルダを選んでください）</span>`;
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
    : "フォルダは未登録です（使うときだけ、上の「＋ フォルダを追加」で .md / .txt の入ったフォルダを選んでください）"}</span>`;
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
  if ($("f_agents_md") && canSet("f_agents_md")) $("f_agents_md").checked = !!st.agents_md;
  if ($("f_ide_revert") && canSet("f_ide_revert")) $("f_ide_revert").checked = st.ide_revert !== false;
  if ($("f_rules_every") && canSet("f_rules_every")) $("f_rules_every").checked = !!st.rules_every;
  if ($("f_rp_state") && canSet("f_rp_state")) $("f_rp_state").checked = !!st.rp_state;
  const suggestEl = $("f_suggest_choices") || $("setting-suggest-choices");
  if (suggestEl && canSet(suggestEl.id)) suggestEl.checked = st.suggest_choices !== false;
  if ($("f_choices_erotic_auto") && canSet("f_choices_erotic_auto")) $("f_choices_erotic_auto").checked = !!st.choices_erotic_auto;
  if ($("f_decensor_choices") && canSet("f_decensor_choices")) $("f_decensor_choices").checked = st.decensor_choices !== false;
  if ($("f_decensor_reply") && canSet("f_decensor_reply")) $("f_decensor_reply").checked = st.decensor_reply !== false;
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
  if ($("f_ide_revert")) st.ide_revert = $("f_ide_revert").checked;
  if ($("f_rules_every")) st.rules_every = $("f_rules_every").checked;
  if ($("f_rp_state")) st.rp_state = $("f_rp_state").checked;
  st.suggest_choices = ($("f_suggest_choices") || $("setting-suggest-choices"))?.checked ?? true;
  st.choices_erotic_auto = $("f_choices_erotic_auto")?.checked ?? false;
  st.decensor_choices = $("f_decensor_choices")?.checked ?? true;
  st.decensor_reply = $("f_decensor_reply")?.checked ?? true;
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
  await loadDecensor();
  showPhone(meta.phone || {});
  fillSelect($("presetSelect"), presets.map((x) => ({ value: x.id, label: x.name })), $("presetSelect").value, "プリセット…");
  if (state && !formDirty) fillFileOptions(state.setting);
  await loadCharPresets();
}

// スマホで開くアドレスと QR コード（start_mobile.bat で起動したときだけサーバーが返す）
// 婉曲→直接の辞書（全チャンネル共通。data/decensor.json）
async function loadDecensor() {
  try {
    decensorInfo = await api("/api/decensor");
    if ($("app_decensor")) $("app_decensor").value = decensorInfo.text || "";
  } catch (e) { /* 辞書が読めなくてもアプリは動かす */ }
}

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

// いま画面中央（または上端）にある返信を基準に、前/次のロールプレイ返信の本文先頭を
// 画面上端へ即座に寄せる。smooth にせず scrollTop を直接代入するので、押した瞬間に「ピタッ」と止まる。
// 上端まで持っていけない場合だけ、中央寄せにフォールバックする。
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
  const atBottom = scrollable && (log.scrollHeight - log.scrollTop - log.clientHeight <= 120);
  const atTop = scrollable && (log.scrollTop <= 60);
  const wraps = Array.from(log.querySelectorAll(".msg-wrap"));
  const latestBubble = bubbles[bubbles.length - 1];
  const latestRect = latestBubble.getBoundingClientRect();
  // 下端補正は「最新の返信がまだ中央以上にある」ときだけ使う。
  // 前へ移動した結果、最新の返信が中央より下に来たのに下端補正を続けると、
  // 同じ返信を選び直してしまい、そこで前へ進めなくなる。
  const latestAtOrAboveCenter = latestRect.top <= centerY;
  let target = -1;

  // 直前の移動で本文先頭を上端に合わせた返信があれば、それを「今」として前/次へ進む。
  // 中央判定だけで探すと、上端に合わせた短い返信が中央より上にあり、同じ返信を
  // 選び直してしまうため。
  // スマホでは #chatLog の padding-top が上のチップ避けの余白になっているので、
  // その余白の下（本文が実際に見え始める位置）を上端の基準にする。
  let padTop = 0;
  try { padTop = parseFloat(getComputedStyle(log).paddingTop) || 0; } catch {}
  const topOffset = (padTop > 0 ? padTop : 6) + 6;
  const navTop = logRect.top + topOffset;

  let anchorIndex = -1;
  for (let i = 0; i < bubbles.length; i++) {
    const r = bubbles[i].getBoundingClientRect();
    if (Math.abs(r.top - navTop) <= 8) { anchorIndex = i; break; }
  }

  // 返信の先頭合わせで移動したあと、最新/最古の返信でさらに同じ方向を押したら、
  // 返信の外側（画像・タグ・開いた選択肢など）を含む本当の端まで移動する。
  const jumpToEdge = (toBottom) => {
    navHoldBottom = false;
    log.scrollTop = toBottom ? maxScroll : 0;
    updateRpBg();
  };

  if (anchorIndex >= 0) {
    if (dir > 0 && anchorIndex === bubbles.length - 1) { jumpToEdge(true); return; }
    if (dir < 0 && anchorIndex === 0) { jumpToEdge(false); return; }
    target = Math.max(0, Math.min(bubbles.length - 1, anchorIndex + dir));
  } else if (dir < 0 && atBottom && latestAtOrAboveCenter) {
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
    // 最新の返信まで来ている（または最下段付近にいる）なら、
    // これ以上下に返信はないので、画像・タグ・開いた選択肢を含む最下段へ。
    jumpToEdge(true);
    return;
  } else if (dir < 0 && atTop) {
    // 最古の返信まで来ている（または最上段付近にいる）なら、本当の最上段へ。
    jumpToEdge(false);
    return;
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

  // 選んだ返信がすでに中央にあるなら、同じ場所を選び直さず、さらに1つ進む。
  // （上端まで持っていけず、中央寄せにフォールバックしたときの保険）
  if (target >= 0) {
    const picked = bubbles[target];
    const pr = picked.getBoundingClientRect();
    if (Math.abs((pr.top + pr.bottom) / 2 - centerY) <= 4) {
      target = Math.max(0, Math.min(bubbles.length - 1, target + dir));
    }
  }

  const el = bubbles[target];
  scrollAssistantToTop(el);
}

// アシスタント返信の本文上端を、画面上端（スマホではチップ直下）へピタッと寄せる
function scrollAssistantToTop(el) {
  if (!el || !log) return;
  const logRect = log.getBoundingClientRect();
  const maxScroll = Math.max(0, log.scrollHeight - log.clientHeight);
  let padTop = 0;
  try { padTop = parseFloat(getComputedStyle(log).paddingTop) || 0; } catch {}
  const topOffset = (padTop > 0 ? padTop : 6) + 6;
  const r = el.getBoundingClientRect();
  const topAligned = log.scrollTop + (r.top - logRect.top) - topOffset;
  const centerAligned = log.scrollTop + (r.top - logRect.top) - (log.clientHeight - r.height) / 2;
  // 本文先頭を上端へ。それが無理なときだけ中央寄せにする。
  const top = topAligned <= maxScroll ? topAligned : Math.min(centerAligned, maxScroll);
  // 移動した直後に render が「下端付近だから」と最下段へ引き戻すのを防ぐ。
  navHoldBottom = true;
  log.scrollTop = Math.min(Math.max(0, top), maxScroll);
  // 移動先が最下段そのものなら、自動追従は解除したままにする。
  if (log.scrollHeight - log.scrollTop - log.clientHeight <= 2) navHoldBottom = false;
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
let googleModels = [];
let googleStatusLoaded = false;
let googleConfigured = false;
let googleKeyHint = "";
let vertexModels = [];
let vertexStatusLoaded = false;
let vertexConfigured = false;
let vertexStatus = {};

async function loadModels() {
  try {
    ideModels = await checked("/api/ide/models");
  } catch (e) {
    ideModels = { models: [], error: e.message };
  }
  try {
    const d = await checked("/api/google/status");
    googleConfigured = !!d.configured;
    googleKeyHint = d.hint || "";
    googleStatusLoaded = true;
    if ($("googleKeyState")) {
      $("googleKeyState").className = d.configured ? "ok" : "warn";
      $("googleKeyState").textContent = d.configured ? `設定済み（…${d.hint || ""} / ${d.source || ""}）` : "未設定";
    }
    if (d.configured) {
      const gm = await checked("/api/google/models");
      googleModels = gm.models || [];
    } else {
      googleModels = [];
    }
  } catch (e) {
    googleConfigured = false;
    googleModels = [];
  }
  try {
    const vd = await checked("/api/vertex/status");
    vertexStatus = vd || {};
    vertexConfigured = !!vd.configured;
    vertexStatusLoaded = true;
    updateVertexStateBadge(vertexStatus);
    if (vd.configured) {
      const vm = await checked("/api/vertex/models");
      vertexModels = vm.models || [];
    } else {
      vertexModels = [];
    }
  } catch (e) {
    vertexStatus = {};
    vertexConfigured = false;
    vertexModels = [];
  }
  renderModels();
  syncGoogleSettingsUI();
}

function renderModels() {
  const sel = $("ideModel");
  const selChoices = $("choicesIdeModel");
  const prov = state?.setting?.ai_provider || "antigravity";
  const isAg = !prov || prov === "antigravity";
  const isGoogle = prov === "google";
  const isVertex = prov === "vertex";
  const rows = ideModels?.models || [];
  const reset = modelResetText;
  const left = (r) => (typeof r.left !== "number" ? ""
    : r.left > 0 ? `（残り${Math.max(1, Math.round(r.left * 100))}%）` : `（使い切り${reset(r)}）`);

  const splitModelLabel = (label) => {
    const m = String(label || "").match(/^(.*?)\s*(\(.*?\))\s*$/);
    return { name: m ? m[1] : (label || ""), sub: m ? m[2] : "" };
  };

  const agOpts = [`<option value="ag:">会話の最後の入力と同じ${ideModels?.error ? "（一覧を読めない）" : ""}</option>`]
    .concat(rows.map((r) => {
      const parts = splitModelLabel(r.label);
      return `<option value="ag:${esc(r.model)}">${esc(parts.name || r.label)}${r.model === ideModels?.default ? " ・既定" : ""}</option>`;
    }));
  const currentAg = state?.setting?.ide_model || "";
  if (isAg && currentAg && !rows.some((r) => r.model === currentAg)) {
    const curParts = splitModelLabel(currentAg);
    agOpts.push(`<option value="ag:${esc(currentAg)}">${esc(curParts.name || currentAg)}（いまは選べない）</option>`);
  }

  const googleOpts = [];
  if (googleConfigured && googleModels.length > 0) {
    for (const m of googleModels) {
      googleOpts.push(`<option value="google:${esc(m.id)}">${esc(m.label || m.id)}${m.outputTokenLimit ? "（出力上限 " + m.outputTokenLimit + "）" : ""}</option>`);
    }
    const currentGoogle = state?.setting?.google_model || "";
    if (isGoogle && currentGoogle && !googleModels.some((m) => m.id === currentGoogle)) {
      googleOpts.push(`<option value="google:${esc(currentGoogle)}">${esc(currentGoogle)}（一覧にない）</option>`);
    }
    googleOpts.push(`<option value="__google_key_modal__">⚙ Google APIキー変更 / 削除…</option>`);
  } else {
    googleOpts.push(`<option value="__google_key_modal__">🔑 Google APIキーを設定して利用…</option>`);
  }

  const vertexOpts = [];
  const vertexDefaultId = vertexStatus.suggest_model
    || (vertexModels.find(vertexModelAvailable) || {}).id
    || "gemini-3.8-flash";
  if (vertexConfigured && vertexModels.length > 0) {
    for (const m of vertexModels) {
      const dis = vertexModelAvailable(m) ? "" : " disabled";
      const tag = vertexModelAvailable(m) ? "" : "【このリージョンでは404】";
      vertexOpts.push(`<option value="vertex:${esc(m.id)}"${dis}>${esc(m.label || m.id)}${m.outputTokenLimit ? "（出力上限 " + m.outputTokenLimit + "）" : ""}${tag}</option>`);
    }
    const currentVertex = state?.setting?.vertex_model || "";
    if (isVertex && currentVertex && !vertexModels.some((m) => m.id === currentVertex)) {
      vertexOpts.push(`<option value="vertex:${esc(currentVertex)}">${esc(currentVertex)}（一覧にない）</option>`);
    } else if (isVertex && currentVertex) {
      const cur = vertexModels.find((m) => m.id === currentVertex);
      if (cur && !vertexModelAvailable(cur)) {
        vertexOpts.push(`<option value="vertex:${esc(currentVertex)}">${esc(currentVertex)}【このリージョンでは404】</option>`);
      }
    }
    vertexOpts.push(`<option value="__vertex_config_modal__">⚙ Vertex AI 認証・設定…</option>`);
  } else {
    if (isVertex) {
      const vModel = state?.setting?.vertex_model || vertexDefaultId;
      vertexOpts.push(`<option value="vertex:${esc(vModel)}">${esc(vModel)}</option>`);
    }
    vertexOpts.push(`<option value="__vertex_config_modal__">🔑 Vertex AI 認証を設定して利用…</option>`);
  }

  const optsMain = [
    `<optgroup label="Antigravity（IDE連携）">${agOpts.join("")}</optgroup>`,
    `<optgroup label="Google Gemini API">${googleOpts.join("")}</optgroup>`,
    `<optgroup label="Vertex AI（Google Cloud）">${vertexOpts.join("")}</optgroup>`
  ];

  if (sel && state && document.activeElement !== sel) {
    const want = isVertex
      ? ("vertex:" + (state.setting?.vertex_model || vertexDefaultId))
      : isGoogle
        ? (state.setting?.google_model ? "google:" + state.setting.google_model : "google:")
        : (state.setting?.ide_model ? "ag:" + state.setting.ide_model : "ag:");
    const html = optsMain.join("");
    if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
    sel.value = want;
    if (sel.value !== want && isGoogle && googleModels.length > 0) {
      sel.value = `google:${googleModels[0].id}`;
    } else if (sel.value !== want && isVertex && vertexModels.length > 0) {
      const firstOk = vertexModels.find(vertexModelAvailable) || vertexModels[0];
      sel.value = `vertex:${firstOk.id}`;
    } else if (sel.value !== want && isAg) {
      sel.value = "ag:";
    }
    $("footBar")?.classList.toggle("hidden", !state?.setting);
  }

  if (selChoices && state && document.activeElement !== selChoices) {
    let wantChoices = "";
    let rowsChoices = [];
    let noList = "";
    if (isVertex) {
      wantChoices = state.setting?.vertex_choices_model || "";
      rowsChoices = (vertexModels || []).map((m) => ({ model: m.id, label: m.label || m.id }));
      noList = rowsChoices.length ? "" : "（Vertexモデルを読めない）";
    } else if (isGoogle) {
      wantChoices = state.setting?.google_choices_model || "";
      rowsChoices = (googleModels || []).map((m) => ({ model: m.id, label: m.label || m.id }));
      noList = rowsChoices.length ? "" : "（Googleモデルを読めない）";
    } else {
      wantChoices = state.setting?.choices_ide_model || "";
      rowsChoices = rows;
      noList = ideModels?.error ? "（一覧を読めない）" : "";
    }
    const optsChoices = [`<option value="">メインのモデルと同じ${noList}</option>`]
      .concat(rowsChoices.map((r) => (isGoogle || isVertex)
        ? `<option value="${esc(r.model)}">${esc(r.label)}</option>`
        : `<option value="${esc(r.model)}">${esc(r.label)}${left(r)}${r.model === ideModels?.default ? " ・既定" : ""}</option>`));
    if (wantChoices && !rowsChoices.some((r) => r.model === wantChoices)) {
      optsChoices.push(`<option value="${esc(wantChoices)}">${esc(wantChoices)}（いまは選べない）</option>`);
    }
    const htmlChoices = optsChoices.join("");
    if (selChoices.dataset.html !== htmlChoices) { selChoices.innerHTML = htmlChoices; selChoices.dataset.html = htmlChoices; }
    selChoices.value = wantChoices;
  }
  updateModelSheetLeft();
  updateFootModelSubInfo();
  ThinkingUI.render(state?.setting);
}

// ---------------------------------------------------------------- Thinking Level UI & Resolvers
const ThinkingUI = {
  VALID_THINKING_LEVELS: ["auto", "minimal", "low", "medium", "high", "xhigh"],
  VALID_CHOICES_THINKING_LEVELS: ["low", "minimal", "medium", "high", "auto", "same"],

  LEVEL_LABELS: {
    auto: "自動",
    minimal: "💨最小",
    low: "⚡低",
    medium: "⚖️中",
    high: "🔍高",
    xhigh: "🔥最大"
  },

  CHOICES_LEVEL_LABELS: {
    low: "⚡低 (速・推奨)",
    minimal: "💨最小",
    medium: "⚖️中",
    high: "🔍高",
    same: "🔗本文と同じ",
    auto: "自動"
  },

  GUIDES: {
    auto: "モデル既定の深さで思考します（Gemini 3.x/4.x: 中、Hydrogen: 高、2.5: 自動）",
    minimal: "思考を最小限に抑え、最も高速に応答します（対応: Gemini 3.0〜3.7 Flash、2.5 Flash は budget:0。非対応モデルは「低」に丸められます）",
    low: "思考を短くして応答速度を高めます（Gemini 2.5: 1024トークン）",
    medium: "思考の深さと応答速度のバランスを取ります（Gemini 2.5: 4096トークン）",
    high: "じっくり思考して複雑なプロットやキャラの心理・設定を精密に整理します（Gemini 2.5: 8192トークン）",
    xhigh: "最大深度で思考します（Gemini 4 Hydrogen のみネイティブ対応。他モデルは「高」に丸められます。Gemini 2.5: 16384トークン）"
  },

  CHOICES_GUIDES: {
    low: "【推奨】思考を短く抑え、選択肢を素早く提示します",
    minimal: "思考を最小限にし、最速で選択肢を生成します",
    medium: "選択肢の文脈整合性を高めて丁寧に生成します",
    high: "深い思考で選択肢を生成します（記憶抽出タスクは安全のため「中」に制限されます）",
    same: "本文と同じ思考設定を使用します（最大「高」まで）",
    auto: "モデル既定で生成します"
  },

  thinkingFamily(modelId) {
    const low = String(modelId || "").toLowerCase();
    if (["2.0", "2-0", "1.5", "1-5", "1.0", "1-0", "gemma", "nano", "embedding"].some(k => low.includes(k))) {
      return "none";
    }
    const isLatestThinking = ["flash-latest", "flash-lite-latest", "pro-latest", "thinking-latest"].some(k => low.includes(k));
    const isThinkingModel = ["2.5", "2-5", "3.", "3-", "4.", "4-", "thinking"].some(k => low.includes(k));
    if (!isThinkingModel && !isLatestThinking) {
      return "none";
    }
    if (low.includes("2.5") || low.includes("2-5")) {
      return low.includes("flash") ? "budget_25_flash" : "budget_25_pro";
    }
    if (low.includes("hydrogen")) {
      return "level_hydrogen";
    }
    if (["3.8", "3-8", "3.9", "3-9", "4.", "4-", "pro-latest"].some(k => low.includes(k)) || (low.includes("flash-latest") && !low.includes("lite"))) {
      return "level_38_4";
    }
    if (low.includes("flash")) {
      return "level_3_flash";
    }
    return "level_38_4";
  },

  resolve(modelId, level = "auto") {
    const fam = this.thinkingFamily(modelId);
    let lv = String(level || "auto").trim().toLowerCase();
    if (!this.VALID_THINKING_LEVELS.includes(lv)) lv = "auto";
    if (fam === "none") {
      return { config: null, eff: "none", minOut: 0, isNone: true, rounded: false };
    }
    if (fam === "budget_25_flash" || fam === "budget_25_pro") {
      if (lv === "auto") {
        return { config: null, eff: "auto(budget)", minOut: 0, isAuto: true, rounded: false };
      }
      const minB = fam === "budget_25_flash" ? 0 : 128;
      const budgetMap = { minimal: minB, low: 1024, medium: 4096, high: 8192, xhigh: 16384 };
      const b = budgetMap[lv] ?? 4096;
      const minOut = b > 0 ? Math.max(8192, b + 4096) : 0;
      return { config: { thinkingBudget: b }, eff: `budget:${b}`, minOut, rounded: false };
    }
    const defaultLv = fam === "level_hydrogen" ? "HIGH" : "MEDIUM";
    if (lv === "auto") {
      return { config: null, eff: `auto(${defaultLv})`, minOut: 0, isAuto: true, rounded: false };
    }
    let eff = lv.toUpperCase();
    let rounded = false;
    if (eff === "MINIMAL" && fam !== "level_3_flash") {
      eff = "LOW";
      rounded = true;
    }
    if (eff === "XHIGH" && fam !== "level_hydrogen") {
      eff = "HIGH";
      rounded = true;
    }
    let minOut = 0;
    if (eff === "XHIGH") minOut = 32768;
    else if (eff === "HIGH") minOut = 16384;
    return { config: { thinkingLevel: eff }, eff, minOut, rounded };
  },

  resolveChoices(mainLevel, choicesLevel, modelId) {
    let m = String(mainLevel || "auto").trim().toLowerCase();
    if (!this.VALID_THINKING_LEVELS.includes(m)) m = "auto";
    let c = String(choicesLevel || "low").trim().toLowerCase();
    if (!this.VALID_CHOICES_THINKING_LEVELS.includes(c)) c = "low";
    let resolved = (c === "same") ? m : c;
    if (resolved === "xhigh") resolved = "high";
    return this.resolve(modelId, resolved);
  },

  async setThinking(mainLevel, choicesLevel) {
    if (!state || !state.setting) return;
    const body = { sid };
    if (mainLevel !== null && mainLevel !== undefined) {
      const lv = String(mainLevel).toLowerCase();
      if (this.VALID_THINKING_LEVELS.includes(lv)) {
        state.setting.thinking_level = lv;
        body.thinking_level = lv;
      }
    }
    if (choicesLevel !== null && choicesLevel !== undefined) {
      const clv = String(choicesLevel).toLowerCase();
      if (this.VALID_CHOICES_THINKING_LEVELS.includes(clv)) {
        state.setting.choices_thinking_level = clv;
        body.choices_thinking_level = clv;
      }
    }
    this.render(state.setting);
    try {
      const res = await api("/api/ai/thinking", body);
      if (res && res.ok && state && state.setting) {
        if (res.thinking_level) state.setting.thinking_level = res.thinking_level;
        if (res.choices_thinking_level) state.setting.choices_thinking_level = res.choices_thinking_level;
        this.render(state.setting);
      }
    } catch (e) {
      console.warn("failed to save thinking level:", e);
    }
  },

  render(st) {
    st = st || state?.setting || {};
    const prov = ["google", "vertex"].includes(st.ai_provider) ? st.ai_provider : "antigravity";
    const isGoogle = prov === "google";
    const isVertex = prov === "vertex";
    const isAg = prov === "antigravity";
    const mainLevel = String(st.thinking_level || "auto").toLowerCase();
    const choicesLevel = String(st.choices_thinking_level || "low").toLowerCase();

    // 1. Resolve current active model for preview
    let currentModelId = "";
    if (isGoogle) currentModelId = $("googleModel")?.value || st.google_model || "";
    else if (isVertex) currentModelId = ($("vertexCustomModel")?.value || "").trim() || $("vertexModel")?.value || st.vertex_model || "";
    else currentModelId = st.ide_model || "";

    // Choices (auxiliary) model
    let choicesModelId = "";
    if (isVertex) choicesModelId = ($("choicesIdeModel")?.value !== undefined ? $("choicesIdeModel").value : st.vertex_choices_model) || "";
    else if (isGoogle) choicesModelId = ($("choicesIdeModel")?.value !== undefined ? $("choicesIdeModel").value : st.google_choices_model) || "";
    else choicesModelId = ($("choicesIdeModel")?.value !== undefined ? $("choicesIdeModel").value : st.choices_ide_model) || "";
    if (!choicesModelId) choicesModelId = currentModelId;

    const mainResolved = this.resolve(currentModelId, mainLevel);
    const choicesResolved = this.resolveChoices(mainLevel, choicesLevel, choicesModelId);

    // 2. SettingsPanel AI Summary row
    const summaryEl = $("aiSummaryText");
    if (summaryEl) {
      if (isGoogle) {
        const mLabel = currentModelId || "モデル未設定";
        summaryEl.textContent = `Google: ${mLabel} (思考: ${this.LEVEL_LABELS[mainLevel] || mainLevel})`;
      } else if (isVertex) {
        const mLabel = currentModelId || "モデル未設定";
        summaryEl.textContent = `Vertex AI: ${mLabel} (思考: ${this.LEVEL_LABELS[mainLevel] || mainLevel})`;
      } else {
        const mLabel = st.ide_model || "会話と同じ";
        summaryEl.textContent = `Antigravity (${mLabel})`;
      }
    }

    // 3. Sidebar AI Summary text
    const sideSummaryEl = $("sideAiSummaryText");
    if (sideSummaryEl) {
      if (isGoogle || isVertex) {
        const pName = isGoogle ? "Google" : "Vertex";
        sideSummaryEl.textContent = `${pName}: ${currentModelId || "-"} (${this.LEVEL_LABELS[mainLevel] || mainLevel})`;
      } else {
        sideSummaryEl.textContent = "Antigravity";
      }
    }

    // 4. FootBar Compact Thinking Section
    const footSec = $("footThinkingSection");
    if (footSec) {
      footSec.classList.toggle("hidden", isAg);
      footSec.querySelectorAll(".segment-btn").forEach(btn => {
        const lv = btn.dataset.level;
        btn.classList.toggle("active", lv === mainLevel);
        const r = this.resolve(currentModelId, lv);
        btn.classList.toggle("is-rounded", !!r.rounded);
      });
      const footEff = $("footThinkingEff");
      if (footEff) {
        footEff.textContent = mainResolved.isNone ? "実効: なし"
          : mainResolved.rounded ? `実効: ${mainResolved.eff} (丸め)`
          : `実効: ${mainResolved.eff}`;
      }
      const footChoicesSel = $("footChoicesThinkingSelect");
      if (footChoicesSel && document.activeElement !== footChoicesSel) {
        footChoicesSel.value = choicesLevel;
      }
    }

    // 5. Tool Modal Cards & Pills
    if (isGoogle || isVertex) {
      const prefix = isGoogle ? "google" : "vertex";
      const isAuth = isGoogle ? googleConfigured : vertexConfigured;

      // Lock sections if unauthenticated
      const cardsSec = $(`${prefix}CardsSection`);
      if (cardsSec) cardsSec.classList.toggle("is-unauthenticated", !isAuth);
      const auxCard = $("choicesIdeCard");
      if (auxCard) auxCard.classList.toggle("is-unauthenticated", !isAuth);

      // Main Thinking Pills
      const pillsContainer = $(`${prefix}ThinkingPills`);
      if (pillsContainer) {
        pillsContainer.querySelectorAll(".pill-btn").forEach(btn => {
          const lv = btn.dataset.level;
          btn.classList.toggle("active", lv === mainLevel);
          const r = this.resolve(currentModelId, lv);
          btn.classList.toggle("is-rounded", !!r.rounded);
        });
      }

      // Main Badge & Guide
      const badgeEl = $(`${prefix}ThinkingBadge`);
      if (badgeEl) {
        if (mainResolved.isNone) badgeEl.textContent = "実効: 思考非対応";
        else if (mainResolved.rounded) badgeEl.textContent = `実効: ${mainResolved.eff} (最小/最大は非対応のため丸め)`;
        else badgeEl.textContent = `実効: ${mainResolved.eff}`;
      }
      const guideEl = $(`${prefix}ThinkingGuide`);
      if (guideEl) guideEl.textContent = this.GUIDES[mainLevel] || "";

      // Choices Thinking Pills (shared choicesIdeCard)
      const choicesPillsContainer = $("choicesThinkingPills");
      if (choicesPillsContainer) {
        choicesPillsContainer.querySelectorAll(".pill-btn").forEach(btn => {
          const clv = btn.dataset.choicesLevel;
          btn.classList.toggle("active", clv === choicesLevel);
          const cr = this.resolveChoices(mainLevel, clv, choicesModelId);
          btn.classList.toggle("is-rounded", !!cr.rounded);
        });
      }

      // Choices Badge & Guide
      const choicesBadgeEl = $("choicesThinkingBadge");
      if (choicesBadgeEl) {
        if (choicesResolved.isNone) choicesBadgeEl.textContent = "実効: 思考非対応";
        else if (choicesResolved.rounded) choicesBadgeEl.textContent = `実効: ${choicesResolved.eff} (丸め)`;
        else choicesBadgeEl.textContent = `実効: ${choicesResolved.eff}`;
      }
      const choicesGuideEl = $("choicesThinkingGuide");
      if (choicesGuideEl) choicesGuideEl.textContent = this.CHOICES_GUIDES[choicesLevel] || "";

      // MaxTokens ceiling badge
      const tokInput = $(`${prefix}MaxTokens`);
      const tokBadge = $(`${prefix}MaxTokensBadge`);
      if (tokBadge && tokInput) {
        const curVal = Number(tokInput.value || 8192);
        if (mainResolved.minOut > 0) {
          tokBadge.textContent = curVal < mainResolved.minOut
            ? `下限 ${mainResolved.minOut} に自動底上げ`
            : `推奨下限: ${mainResolved.minOut}`;
        } else {
          tokBadge.textContent = "";
        }
      }
    }
  }
};

// ---------------------------------------------------------------- Google Gemini / Vertex AI

function googleModelOptions(selected) {
  const rows = googleModels || [];
  const opts = [`<option value="">モデルを選択</option>`]
    .concat(rows.map((m) => `<option value="${esc(m.id)}">${esc(m.label || m.id)}${m.outputTokenLimit ? "（出力上限 " + m.outputTokenLimit + "）" : ""}</option>`));
  if (selected && !rows.some((m) => m.id === selected)) opts.push(`<option value="${esc(selected)}">${esc(selected)}（一覧にない）</option>`);
  return opts.join("");
}

function vertexModelAvailable(m) {
  return m && m.available !== false;
}

function vertexModelOptions(selected) {
  const rows = vertexModels || [];
  const opt = (m, extra) => `<option value="${esc(m.id)}"`
    + `${vertexModelAvailable(m) ? "" : " disabled"}>`
    + `${esc(m.label || m.id)}${extra || ""}`
    + `${vertexModelAvailable(m) ? "" : "【このリージョンでは404】"}</option>`;
  const opts = [`<option value="">モデルを選択</option>`]
    .concat(rows.map((m) => opt(m, m.outputTokenLimit ? `（出力上限 ${m.outputTokenLimit}）` : "")));
  if (selected && !rows.some((m) => m.id === selected)) {
    opts.push(`<option value="${esc(selected)}">${esc(selected)}（一覧にない）</option>`);
  }
  return opts.join("");
}

async function loadVertexStatus() {
  try {
    const d = await checked("/api/vertex/status");
    vertexStatus = d || {};
    vertexConfigured = !!d.configured;
    updateVertexStateBadge(vertexStatus);
  } catch (e) {
    vertexStatus = {};
    vertexConfigured = false;
    if ($("vertexKeyState")) {
      $("vertexKeyState").className = "err";
      $("vertexKeyState").textContent = e.message;
    }
  } finally {
    vertexStatusLoaded = true;
  }
}

async function loadVertexModels(force = false) {
  try {
    // probe=1 で「そのリージョンで実際に generateContent できるか」を実測させる。
    // 一覧APIはリージョン非依存に全モデルを返すため、これが無いと 404 になる
    // モデル（Gemini 2.x やリージョン未提供のもの）を選べてしまう。
    const d = await checked("/api/vertex/models" + (force ? "?refresh=1&probe=1" : "?probe=1"));
    vertexModels = d.models || [];
    vertexStatus.effective_location = d.effective_location || vertexStatus.location;
    vertexStatus.location_note = d.location_note || "";
    vertexStatus.suggest_model = d.suggest_model || vertexStatus.suggest_model || "";
    if ($("vertexApplyMsg")) {
      const note = d.location_note ? ("（" + d.location_note + "）") : "";
      if (d.error) {
        $("vertexApplyMsg").className = "err";
        $("vertexApplyMsg").textContent = d.error;
      } else if (vertexStatus.location_note) {
        $("vertexApplyMsg").className = "warn";
        $("vertexApplyMsg").textContent = "使用リージョン: " + (d.effective_location || "") + note;
      } else if ($("vertexApplyMsg").className === "err") {
        $("vertexApplyMsg").className = "muted";
        $("vertexApplyMsg").textContent = "";
      }
    }
  } catch (e) {
    vertexModels = [];
    if ($("vertexApplyMsg")) {
      $("vertexApplyMsg").className = "err";
      $("vertexApplyMsg").textContent = e.message;
    }
  }
  syncGoogleSettingsUI();
  renderModels();
}

let vertexAuthManualOpen = false;

function toggleVertexAuth() {
  vertexAuthManualOpen = !vertexAuthManualOpen;
  $("vertexAuthForm")?.classList.toggle("hidden", !vertexAuthManualOpen);
  if ($("vertexAuthToggle")) {
    $("vertexAuthToggle").textContent = vertexAuthManualOpen ? "認証設定を閉じる ▴" : "認証設定を変更 ▾";
  }
}

function updateVertexStateBadge(vd) {
  const el = $("vertexKeyState");
  if (!el) return;
  if (vd && vd.configured) {
    el.className = "ok";
    const who = vd.auth_type === "adc" ? "ADC" : (vd.client_email_hint || "SA");
    el.textContent = `🟢 設定済み（${vd.project_id} / ${vd.location} / ${who}）`;
  } else {
    el.className = "warn";
    el.textContent = "⚠️ 未設定";
  }
}

function openVertexConfigModal() {
  openAiToolModal();
  if ($("aiProvider")) $("aiProvider").value = "vertex";
  $("googleBox")?.classList.add("hidden");
  $("vertexBox")?.classList.remove("hidden");
  $("agAiBox")?.classList.add("hidden");
  vertexAuthManualOpen = true;
  $("vertexAuthForm")?.classList.remove("hidden");
  if ($("vertexAuthToggle")) $("vertexAuthToggle").textContent = "認証設定を閉じる ▴";
  if ($("vertexConfigMsg")) {
    $("vertexConfigMsg").className = "muted";
    $("vertexConfigMsg").textContent = "";
  }
  if ($("vertexProjectInput")) {
    $("vertexProjectInput").value = vertexStatus.project_id || vertexStatus.adc_project_hint || "";
  }
  if ($("vertexLocationInput")) {
    $("vertexLocationInput").value = vertexStatus.location || "global";
  }
  if ($("vertexSaInput")) $("vertexSaInput").value = "";
  if ($("vertexSaFile")) $("vertexSaFile").value = "";
  setTimeout(() => $("vertexProjectInput")?.focus(), 50);
}

function closeVertexConfigModal() {
  if (vertexConfigured) {
    vertexAuthManualOpen = false;
    $("vertexAuthForm")?.classList.add("hidden");
    if ($("vertexAuthToggle")) $("vertexAuthToggle").textContent = "認証設定を変更 ▾";
  }
}

async function saveVertexConfig() {
  const btn = $("vertexConfigSave");
  if (btn) btn.disabled = true;
  if ($("vertexConfigMsg")) {
    $("vertexConfigMsg").className = "muted";
    $("vertexConfigMsg").textContent = "確認中…";
  }
  try {
    const body = {
      sa_json: ($("vertexSaInput")?.value || "").trim(),
      project_id: ($("vertexProjectInput")?.value || "").trim(),
      location: ($("vertexLocationInput")?.value || "").trim() || "global",
    };
    const r = await checked("/api/vertex/config", body);
    if (Array.isArray(r.models) && r.models.length) vertexModels = r.models;
    await loadVertexStatus();
    if (!vertexModels.length) await loadVertexModels(true);
    let note = "";
    if (r.location_corrected) {
      note = `このリージョンでは全モデルが404だったため、'${r.location_corrected}' に自動修正しました。`;
      if ($("vertexLocationInput")) $("vertexLocationInput").value = r.location_corrected;
    }
    if (r.error) {
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = "err";
        $("vertexConfigMsg").textContent = (note ? note + "\n" : "") + "保存しましたが疎通確認で警告: " + r.error;
      }
    } else {
      // 疎通したモデルをその場で選択して保存する（未指定のままだと 404 を踏むため）
      const pick = r.suggest_model
        || (r.available_models || [])[0]
        || (vertexModels.filter(vertexModelAvailable)[0] || {}).id
        || "";
      if (pick && state?.setting) {
        try {
          await checked("/api/ide/model", { sid, model: pick, target: "main", provider: "vertex" });
          state.setting.vertex_model = pick;
        } catch (e) { /* 保存に失敗しても切替は続行 */ }
      }
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = "ok";
        $("vertexConfigMsg").textContent = (note ? note + "\n" : "")
          + "保存して認証を確認しました" + (pick ? `（モデル: ${pick}）` : "");
      }
      closeVertexConfigModal();
      if (state?.setting) {
        await applyProviderSettings("vertex");
        await poll();
        renderModels();
      }
    }
  } catch (e) {
    if ($("vertexConfigMsg")) {
      $("vertexConfigMsg").className = "err";
      $("vertexConfigMsg").textContent = e.message;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function applyProviderSettings(provider) {
  const p = ["google", "vertex"].includes(provider) ? provider : "antigravity";
  const body = { sid, provider: p };
  if (p === "google") {
    body.model = $("googleModel")?.value || "";
    body.fetch = !!$("googleFetch")?.checked;
    body.safety_off = !!$("googleSafetyOff")?.checked;
    body.max_output_tokens = Number($("googleMaxTokens")?.value || 8192);
  } else if (p === "vertex") {
    const custom = ($("vertexCustomModel")?.value || "").trim();
    body.model = custom || ($("vertexModel")?.value || "");
    body.fetch = !!$("vertexFetch")?.checked;
    body.safety_off = !!$("vertexSafetyOff")?.checked;
    body.max_output_tokens = Number($("vertexMaxTokens")?.value || 8192);
  }
  try {
    const d = await checked("/api/ai/provider", body);
    if (state?.setting && d) {
      state.setting.ai_provider = d.provider || p;
      if (p === "vertex" && d.model) state.setting.vertex_model = d.model;
      if (p === "google" && d.model) state.setting.google_model = d.model;
      if (typeof d.fetch === "boolean") state.setting.google_fetch = d.fetch;
      if (typeof d.safety_off === "boolean") state.setting.google_safety_off = d.safety_off;
      if (d.max_output_tokens) state.setting.google_max_output_tokens = d.max_output_tokens;
      if (d.thinking_level) state.setting.thinking_level = d.thinking_level;
      if (d.choices_thinking_level) state.setting.choices_thinking_level = d.choices_thinking_level;
    }
    await poll();
    await loadModels();
    renderModels();
    syncGoogleSettingsUI();
    flash(p === "vertex" ? "Vertex AI に切り替えました"
      : p === "google" ? "Google Gemini に切り替えました" : "Antigravity に切り替えました");
  } catch (e) {
    if (e.code === "vertex_not_configured") openVertexConfigModal();
    else if (e.code === "google_no_key") openGoogleKeyModal();
    else alert(e.message);
    renderModels();
    syncGoogleSettingsUI();
  }
}

const applyGoogleSettings = () => applyProviderSettings("google");

let googleAuthManualOpen = false;

function toggleGoogleAuth() {
  googleAuthManualOpen = !googleAuthManualOpen;
  $("googleAuthForm")?.classList.toggle("hidden", !googleAuthManualOpen);
  if ($("googleAuthToggle")) {
    $("googleAuthToggle").textContent = googleAuthManualOpen ? "認証設定を閉じる ▴" : "認証設定を変更 ▾";
  }
}

function syncGoogleSettingsUI() {
  const st = state?.setting || {};
  const prov = ["google", "vertex"].includes(st.ai_provider) ? st.ai_provider : "antigravity";
  if ($("aiProvider") && document.activeElement !== $("aiProvider")) $("aiProvider").value = prov;
  if ($("googleBox")) $("googleBox").classList.toggle("hidden", prov !== "google");
  if ($("vertexBox")) $("vertexBox").classList.toggle("hidden", prov !== "vertex");
  if ($("agAiBox")) $("agAiBox").classList.toggle("hidden", prov !== "antigravity");
  if ($("auxThinkingSection")) $("auxThinkingSection").classList.toggle("hidden", prov === "antigravity");

  // Accordion auto-unfold if unauthenticated
  if ($("googleAuthForm")) {
    const show = !googleConfigured || googleAuthManualOpen;
    $("googleAuthForm").classList.toggle("hidden", !show);
    if ($("googleAuthToggle")) {
      $("googleAuthToggle").textContent = show ? "認証設定を閉じる ▴" : "認証設定を変更 ▾";
    }
  }
  if ($("vertexAuthForm")) {
    const show = !vertexConfigured || vertexAuthManualOpen;
    $("vertexAuthForm").classList.toggle("hidden", !show);
    if ($("vertexAuthToggle")) {
      $("vertexAuthToggle").textContent = show ? "認証設定を閉じる ▴" : "認証設定を変更 ▾";
    }
  }

  // Location warning note for Vertex
  if ($("vertexLocationWarning")) {
    const note = vertexStatus.location_note || "";
    $("vertexLocationWarning").classList.toggle("hidden", !note);
    if (note) $("vertexLocationWarning").textContent = `⚠️ 使用リージョン: ${vertexStatus.effective_location || ""} (${note})`;
  }

  // Google AI Studio
  if ($("googleModel") && document.activeElement !== $("googleModel")) {
    const html = googleModelOptions(st.google_model || "");
    if ($("googleModel").dataset.html !== html) {
      $("googleModel").innerHTML = html;
      $("googleModel").dataset.html = html;
    }
    $("googleModel").value = st.google_model || "";
  }
  if ($("googleFetch")) $("googleFetch").checked = st.google_fetch !== false;
  if ($("googleSafetyOff")) $("googleSafetyOff").checked = st.google_safety_off !== false;
  if ($("googleMaxTokens")) $("googleMaxTokens").value = st.google_max_output_tokens || 8192;

  // Vertex AI
  if ($("vertexModel") && document.activeElement !== $("vertexModel")) {
    const html = vertexModelOptions(st.vertex_model || "");
    if ($("vertexModel").dataset.html !== html) {
      $("vertexModel").innerHTML = html;
      $("vertexModel").dataset.html = html;
    }
    $("vertexModel").value = st.vertex_model || "";
  }
  if ($("vertexCustomModel") && document.activeElement !== $("vertexCustomModel")) {
    const inList = (vertexModels || []).some((m) => m.id === st.vertex_model);
    $("vertexCustomModel").value = (st.vertex_model && !inList) ? st.vertex_model : "";
  }
  if ($("vertexFetch")) $("vertexFetch").checked = st.google_fetch !== false;
  if ($("vertexSafetyOff")) $("vertexSafetyOff").checked = st.google_safety_off !== false;
  if ($("vertexMaxTokens")) $("vertexMaxTokens").value = st.google_max_output_tokens || 8192;
  updateVertexStateBadge(vertexStatus);

  ThinkingUI.render(st);
}

async function loadGoogleStatus() {
  try {
    const d = await checked("/api/google/status");
    googleStatusLoaded = true;
    googleConfigured = !!d.configured;
    googleKeyHint = d.hint || "";
    if ($("googleKeyState")) {
      $("googleKeyState").className = d.configured ? "ok" : "warn";
      $("googleKeyState").textContent = d.configured ? `🟢 設定済み（…${d.hint || ""}）` : "⚠️ 未設定";
    }
    if (d.configured) await loadGoogleModels(false);
  } catch (e) {
    googleStatusLoaded = true;
    if ($("googleKeyState")) { $("googleKeyState").className = "err"; $("googleKeyState").textContent = e.message; }
  }
}

async function loadGoogleModels(force = false) {
  try {
    const d = await checked("/api/google/models" + (force ? "?refresh=1" : ""));
    googleModels = d.models || [];
    if (d.error && $("googleApplyMsg")) { $("googleApplyMsg").className = "err"; $("googleApplyMsg").textContent = d.error; }
    syncGoogleSettingsUI();
  } catch (e) {
    googleModels = [];
    if ($("googleApplyMsg")) { $("googleApplyMsg").className = "err"; $("googleApplyMsg").textContent = e.message; }
    syncGoogleSettingsUI();
  }
}

function openGoogleKeyModal() {
  openAiToolModal();
  if ($("aiProvider")) $("aiProvider").value = "google";
  $("googleBox")?.classList.remove("hidden");
  $("vertexBox")?.classList.add("hidden");
  $("agAiBox")?.classList.add("hidden");
  googleAuthManualOpen = true;
  $("googleAuthForm")?.classList.remove("hidden");
  if ($("googleAuthToggle")) $("googleAuthToggle").textContent = "認証設定を閉じる ▴";
  if ($("googleKeyMsg")) $("googleKeyMsg").textContent = "";
  setTimeout(() => $("googleKeyInput")?.focus(), 50);
}

function closeGoogleKeyModal() {
  if (googleConfigured) {
    googleAuthManualOpen = false;
    $("googleAuthForm")?.classList.add("hidden");
    if ($("googleAuthToggle")) $("googleAuthToggle").textContent = "認証設定を変更 ▾";
  }
}

if ($("vertexSaFile")) {
  $("vertexSaFile").onchange = async () => {
    const f = $("vertexSaFile").files?.[0];
    if (!f) return;
    try {
      const text = await f.text();
      if ($("vertexSaInput")) $("vertexSaInput").value = text;
      let pid = "";
      try { pid = (JSON.parse(text) || {}).project_id || ""; } catch (_) { pid = ""; }
      if (pid && $("vertexProjectInput") && !$("vertexProjectInput").value.trim()) {
        $("vertexProjectInput").value = pid;
      }
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = pid ? "ok" : "warn";
        $("vertexConfigMsg").textContent = pid
          ? `読み込みました（project_id: ${pid}）`
          : "JSONを読みましたが project_id が見つかりません";
      }
    } catch (e) {
      if ($("vertexConfigMsg")) {
        $("vertexConfigMsg").className = "err";
        $("vertexConfigMsg").textContent = e.message;
      }
    }
  };
}
if ($("aiProvider")) {
  $("aiProvider").onchange = async () => {
    const sel = $("aiProvider").value;
    const provider = ["google", "vertex"].includes(sel) ? sel : "antigravity";
    $("googleBox")?.classList.toggle("hidden", provider !== "google");
    $("vertexBox")?.classList.toggle("hidden", provider !== "vertex");
    if (provider === "google") {
      if (!googleStatusLoaded) await loadGoogleStatus();
      if (!googleConfigured) { openGoogleKeyModal(); return; }
      if (!googleModels.length) await loadGoogleModels();
    } else if (provider === "vertex") {
      if (!vertexStatusLoaded) await loadVertexStatus();
      if (!vertexConfigured) { openVertexConfigModal(); return; }
      if (!vertexModels.length) await loadVertexModels();
    }
    await applyProviderSettings(provider);
  };
}
if ($("googleApply")) {
  $("googleApply").onclick = async () => {
    try {
      await applyGoogleSettings();
      if ($("googleApplyMsg")) {
        $("googleApplyMsg").className = "ok";
        $("googleApplyMsg").textContent = "保存しました";
      }
    } catch (e) {
      if ($("googleApplyMsg")) {
        $("googleApplyMsg").className = "err";
        $("googleApplyMsg").textContent = e.message;
      }
    }
  };
}
if ($("googleKeySet")) $("googleKeySet").onclick = openGoogleKeyModal;
if ($("googleKeyCancel")) $("googleKeyCancel").onclick = closeGoogleKeyModal;
if ($("googleKeyModal")) $("googleKeyModal").addEventListener("click", (e) => { if (e.target === $("googleKeyModal")) closeGoogleKeyModal(); });
if ($("googleKeySave")) {
  $("googleKeySave").onclick = async () => {
    const key = $("googleKeyInput").value.trim();
    if (!key) return alert("APIキーを入れてください");
    $("googleKeySave").disabled = true;
    try {
      const d = await checked("/api/google/key", { key });
      $("googleKeyInput").value = "";
      $("googleKeyMsg").className = d.error ? "err" : "ok";
      $("googleKeyMsg").textContent = d.error ? ("保存しましたが、モデル一覧の取得に失敗: " + d.error) : "保存しました";
      googleStatusLoaded = true;
      if ($("googleKeyState")) { $("googleKeyState").className = "ok"; $("googleKeyState").textContent = `設定済み（…${d.hint || ""}）`; }
      if (d.models) googleModels = d.models;
      googleConfigured = true;
      googleKeyHint = d.hint || "";
      syncGoogleSettingsUI();
      if (state && sid) {
        try {
          const defaultModel = (d.models && d.models[0]?.id) || "";
          await checked("/api/ai/provider", { sid, provider: "google", model: defaultModel });
          flash("Google Gemini に切り替えました");
          await poll();
        } catch (_) {}
      }
      await loadModels();
      closeGoogleKeyModal();
    } catch (e) {
      $("googleKeyMsg").className = "err";
      $("googleKeyMsg").textContent = e.message;
    } finally { $("googleKeySave").disabled = false; }
  };
}
if ($("googleKeyDelete")) {
  $("googleKeyDelete").onclick = async () => {
    if (!confirm("保存したGoogle APIキーを削除しますか？")) return;
    try {
      await checked("/api/google/key/delete", {});
      googleModels = [];
      googleConfigured = false;
      googleKeyHint = "";
      googleStatusLoaded = true;
      if ($("googleKeyState")) { $("googleKeyState").className = "warn"; $("googleKeyState").textContent = "未設定"; }
      syncGoogleSettingsUI();
      if (state?.setting?.ai_provider === "google" && sid) {
        try {
          await checked("/api/ai/provider", { sid, provider: "antigravity" });
          flash("Antigravity に切り替えました");
          await poll();
        } catch (_) {}
      }
      await loadModels();
      flash("Google APIキーを削除しました");
    } catch (e) { alert(e.message); }
  };
}
if ($("googleModelsReload")) $("googleModelsReload").onclick = () => loadGoogleModels(true);
if ($("vertexConfigSet")) $("vertexConfigSet").onclick = openVertexConfigModal;
if ($("vertexConfigCancel")) $("vertexConfigCancel").onclick = closeVertexConfigModal;
if ($("vertexConfigSave")) $("vertexConfigSave").onclick = saveVertexConfig;
if ($("vertexModelsReload")) $("vertexModelsReload").onclick = () => loadVertexModels(true);
if ($("vertexApply")) {
  $("vertexApply").onclick = async () => {
    const custom = ($("vertexCustomModel")?.value || "").trim();
    const model = custom || ($("vertexModel")?.value || "");
    try {
      if (model) await checked("/api/ide/model", { sid, model, target: "main", provider: "vertex" });
      await applyProviderSettings("vertex");
      if ($("vertexApplyMsg")) {
        $("vertexApplyMsg").className = "ok";
        $("vertexApplyMsg").textContent = "保存しました";
      }
    } catch (e) {
      if ($("vertexApplyMsg")) {
        $("vertexApplyMsg").className = "err";
        $("vertexApplyMsg").textContent = e.message;
      }
    }
  };
}
if ($("vertexModel")) {
  $("vertexModel").onchange = () => {
    if ($("vertexCustomModel")) $("vertexCustomModel").value = "";
    ThinkingUI.render(state?.setting);
  };
}
if ($("vertexConfigDelete")) {
  $("vertexConfigDelete").onclick = async () => {
    if (!confirm("Vertex AI の設定（サービスアカウントJSON・プロジェクト・リージョン）を削除します。\nVertex を使っているチャンネルは Antigravity に戻ります。よろしいですか？")) return;
    try {
      await checked("/api/vertex/config/delete", {});
      await loadVertexStatus();
      vertexModels = [];
      await poll();
      renderModels();
      syncGoogleSettingsUI();
      flash("Vertex AI の設定を削除しました");
    } catch (e) { alert(e.message); }
  };
}
if ($("vertexConfigModal")) {
  $("vertexConfigModal").addEventListener("click", (e) => {
    if (e.target === $("vertexConfigModal")) closeVertexConfigModal();
  });
}

$("ideModel").onchange = async () => {
  const val = $("ideModel").value;
  if (val === "__google_key_modal__") {
    renderModels();
    openGoogleKeyModal();
    return;
  }
  if (val === "__vertex_config_modal__") {
    renderModels();
    openVertexConfigModal();
    return;
  }
  if (val.startsWith("vertex:")) {
    const model = val.slice("vertex:".length);
    if (!model) { renderModels(); return; }
    if (!vertexStatusLoaded) await loadVertexStatus();
    if (!vertexConfigured) { openVertexConfigModal(); renderModels(); return; }
    try {
      await checked("/api/ai/provider", { sid, provider: "vertex", model });
      toast("Vertex AI: " + model);
      await poll();
      renderModels();
      syncGoogleSettingsUI();
    } catch (e) {
      if (e.code === "vertex_not_configured") openVertexConfigModal();
      else alert(e.message);
      renderModels();
    }
    $("ideModel").blur();
    return;
  }
  if (val.startsWith("google:")) {
    const model = val.slice("google:".length);
    if (!googleConfigured) {
      renderModels();
      openGoogleKeyModal();
      return;
    }
    try {
      await checked("/api/ai/provider", { sid, provider: "google", model });
      const label = $("ideModel").selectedOptions[0]?.textContent || model;
      toast("Google Gemini: " + label);
      await poll();
      renderModels();
    } catch (e) {
      if (e.message?.includes("APIキー") || e.code === "google_no_key") {
        openGoogleKeyModal();
      } else {
        alert(e.message);
      }
      renderModels();
    }
  } else if (val.startsWith("ag:")) {
    const model = val.slice("ag:".length);
    try {
      await checked("/api/ai/provider", { sid, provider: "antigravity", model });
      const label = $("ideModel").selectedOptions[0]?.textContent || (model || "会話と同じ");
      toast("Antigravity: " + label);
      await poll();
      renderModels();
    } catch (e) {
      alert(e.message);
      renderModels();
    }
  }
  $("ideModel").blur();
};
if ($("choicesIdeModel")) {
  $("choicesIdeModel").onchange = async () => {
    const p = state?.setting?.ai_provider;
    const provider = ["google", "vertex"].includes(p) ? p : "antigravity";
    try {
      await checked("/api/ide/model", { sid, model: $("choicesIdeModel").value, target: "choices", provider });
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

$("appDecensorSave").onclick = async () => {
  try {
    const r = await checked("/api/decensor", { text: $("app_decensor").value });
    $("appDecensorMsg").textContent = r.warnings?.length
      ? `${r.count}語を保存（注意: ${r.warnings[0]}）`
      : `${r.count}語を保存しました`;
    setTimeout(() => ($("appDecensorMsg").textContent = ""), 6000);
  } catch (e) { alert(e.message); }
};

// 初期セット（62語）を欄に戻すだけ。ファイルへの反映は「辞書を保存」を押したとき
$("appDecensorReset").onclick = async () => {
  if (!confirm("辞書の欄を初期セット（62語）に戻しますか？（保存を押すまで反映されません）")) return;
  $("app_decensor").value = decensorInfo?.seed_text || "";
};

$("previewBtn").onclick = async () => {
  if (formDirty && !confirm("未保存の変更があります。保存済みの設定で表示します。")) return;
  const r = await api("/api/prompt-preview?sid=" + encodeURIComponent(sid));
  openText("AIに送る設定（" + (r.chars || 0) + "文字）",
    (r.missing?.length ? "⚠ 読めないファイル: " + r.missing.join(", ") + "\n" : "") + "wait_input.py が入力を受け取ったとき、この設定とAIメモ・会話・発言をそのまま表示します。担当IDEが前回と同じなら、変わった設定と増えた発言だけを出します。",
    r.text || "", null);
};

// ---------------------------------------------------------------- 汎用テキストモーダル
function openText(title, hint, body, onSave, meta, onMetaClick = null) {
  $("textTitle").textContent = title;
  $("textHint").textContent = hint;
  const metaEl = $("textMeta");
  metaEl.textContent = meta || "";
  if (onMetaClick && meta) {
    metaEl.classList.add("is-clickable");
    metaEl.setAttribute("role", "button");
    metaEl.tabIndex = 0;
    metaEl.title = "参照した記憶の内訳を見る";
    metaEl.onclick = (e) => {
      e.stopPropagation();
      onMetaClick();
    };
    metaEl.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        onMetaClick();
      }
    };
  } else {
    metaEl.classList.remove("is-clickable");
    metaEl.removeAttribute("role");
    metaEl.removeAttribute("tabindex");
    metaEl.removeAttribute("title");
    metaEl.onclick = null;
    metaEl.onkeydown = null;
  }
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
    displayChoicesMode.get(m.id || ("msg-" + (m.ts || ""))) || "",
    m.choices ? m.choices.map((c) => [c.label, c.text]) : null,
    m.choices_updated_ts || null,
    m.choices_erotic ? m.choices_erotic.map((c) => [c.label, c.text]) : null,
    m.choices_erotic_updated_ts || null,
    m.choices_notice || null,
    m.choices_timeout || null,
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
    isLast,
    m.id === renderLastAsstId      // 候補を出す対象か（位置が変わったら作り直す）
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

// ⋯ メニューのインラインSVGアイコン。
// 分岐は絵文字だと 🔀（シャッフル）にしか見えないので、
// Google Fonts Icons（Material Symbols）の "arrow_split" を使う（Apache License 2.0）。
// https://fonts.google.com/icons?selected=Material+Symbols+Outlined:arrow_split
const MORE_ICON_BRANCH =
  '<svg viewBox="0 -960 960 960" aria-hidden="true" focusable="false">' +
  '<path d="M160-440v-80h304l200-200H560v-80h240v240h-80v-104L496-440H160Zm400 280v-80h104L536-366l58-58 126 128v-104h80v240H560Z"/>' +
  '</svg>';

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
    b.textContent = label;                 // ラベルは必ずテキストで入れる（HTML は使わない）
    if (opts.title) b.title = opts.title;
    if (opts.icon) {                       // アイコンは文字の前に差し込む
      const ic = document.createElement("span");
      ic.className = "more-ico";
      ic.innerHTML = opts.icon;            // 渡すのは固定のSVG文字列だけ
      if (b.firstChild && typeof b.insertBefore === "function") b.insertBefore(ic, b.firstChild);
      else b.appendChild(ic);
    }
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
  item(m.role === "user" ? "✏️ 編集して再送" : "📝 本文を編集", () => openEdit(m),
    { title: m.role === "user" ? "この入力を編集して、この発言以降をやり直す" : "この発言の本文を編集する" });
  if (m.role !== "user" && (m.state || s.setting?.rp_state)) {
    item("🧑 キャラの状態", () => openState(m, s.messages), { title: "この応答のときのキャラの状態と内心を直す（次の入力で AI が読む）" });
  }
  sep();

  // 会話系
  if (m.role !== "user" && s.messages.slice(0, s.messages.indexOf(m)).some((x) => x.role === "user")) {
    item("🔄 再生成", async () => {
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
  }, { title: "ここまでの会話で新しいチャンネルを作る（担当の Antigravity 会話も同じところで分岐して、新しいチャンネルの担当にする）", icon: MORE_ICON_BRANCH });
  item("📋 コピー", () => navigator.clipboard.writeText(m.text || ""));
  sep();

  // 取り消しにくい操作はまとめて一番下に置く
  if (!isLast) {
    item("⏪ 巻き戻し", async () => {
      if (!confirm("このメッセージより後の会話を消して、ここまで戻しますか？（控えは data/trash に残ります）")) return;
      const ch = sid;
      let r;
      try { r = await checked("/api/message/truncate", { sid, id: m.id }); await poll(); }
      catch (e) { alert(e.message); return; }
      await revertIde(r, ch);
    });
  }
  item("🗑️ 削除", async () => {
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

function textDecensorLine(m, s) {
  const dec = m.text_decensor || {};
  const applied = dec.applied || [];
  const total = applied.reduce((n, a) => n + (a.count || 0), 0);
  const el = document.createElement("div");
  el.className = "msg-notice";
  el.textContent = `婉曲表現を${total}か所 自動で戻しました`;
  el.title = applied.map((a) => `${a.from} → ${a.to}（${a.count}）`).join("\n")
    + "\n\n「戻す」でAIが書いた元の文面に戻せます";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "choices-undo-btn";
  btn.textContent = "戻す";
  btn.title = "自動置換を取り消して、AIが書いた元の文面に戻します";
  btn.onclick = async (e) => {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    btn.disabled = true;
    try {
      const r = await api("/api/message/undo-decensor", {
        sid: s?.id || (typeof sid !== "undefined" ? sid : ""),
        id: m.id,
        kind: "text",
      });
      if (r && r.error) {
        // 再起動・別枝分岐・20件超過などで記録が無い（404）場合はボタンを静かに消す（ゾンビ防止）
        btn.remove();
        const is404 = r.error === "戻せる置換がありません" || r.error === "no such assistant message" || r.error === "no such message" || r.error === "no such session";
        if (!is404) {
          alert("戻せませんでした: " + r.error);
        }
        return;
      }
      m.text_decensor = null;
      el.remove();
      if (typeof poll === "function") await poll();
    } catch (err) {
      btn.remove();
    }
  };
  el.appendChild(btn);
  return el;
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
  if (m.role === "assistant" && m.text_decensor?.applied?.length) {
    wrap.appendChild(textDecensorLine(m, s));
  }

  const hasImage = !!m.image;
  const hasChoices = m.role !== "user" && (
    (Array.isArray(m.choices) && m.choices.length > 0) ||
    (Array.isArray(m.choices_erotic) && m.choices_erotic.length > 0) ||
    m.choices_status || m.choices_notice || m.choices_timeout ||
    // 🔞を既定にしているチャンネル: 先読みが失敗して候補ゼロでも ↻ で作り直せるようにする
    (m.id === renderLastAsstId && !!s?.setting?.choices_erotic_auto));

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
    // 「🔁」ボタン1つのラベルを入れ替えて表す（押してもボタンは増やさない）
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
    // アイドル時はアイコンだけ（文字は状態のときだけ出す）
    const RG_IDLE_LABEL = "🔁";
    const RG_IDLE_TITLE = "タグはそのままで、新しいシードで画像を作り直す（タグを変えるときは 🎨）";
    const RG_GEN_LABEL = "⏳ 生成中…";
    const RG_GEN_TITLE = "画像を生成しています（終わると 🔁 に戻ります）";

    let rgLabel = RG_IDLE_LABEL;
    let rgTitle = RG_IDLE_TITLE;
    if (refineBusy) {
      // プロンプト画面の「✨ タグ推敲」からの推敲中
      rgLabel = "✨ 推敲中…";
      rgTitle = "AIがプロンプトを推敲しています";
    } else if (genBusy) {
      rgLabel = RG_GEN_LABEL;
      rgTitle = RG_GEN_TITLE;
    } else if (waitingGen) {
      rgLabel = "⏳ 生成待ち…";
      rgTitle = "画像の生成待ちです（終わると 🔁 に戻ります。タップで作り直す）";
    }

    const rg = document.createElement("button");
    rg.type = "button";
    rg.className = "btn-img-refine" + ((refineBusy || genBusy || waitingGen) ? " busy" : "");
    rg.textContent = rgLabel;
    rg.title = rgTitle;
    rg.setAttribute("aria-label", rgTitle);
    if (refineBusy || genBusy) rg.disabled = true;
    rg.onclick = async () => {
      if (rg.disabled) return;
      // 押した瞬間にその場で「生成中」に入れ替える（別のボタンは出さない）
      rg.disabled = true;
      rg.className = "btn-img-refine busy";
      rg.textContent = RG_GEN_LABEL;
      rg.title = RG_GEN_TITLE;
      rg.setAttribute("aria-label", RG_GEN_TITLE);
      try {
        const res = await post("/api/regen", { id: m.id });
        if (res && res.error) throw new Error(res.error);
      } catch (e) {
        rg.className = "btn-img-refine";
        rg.textContent = RG_IDLE_LABEL;
        rg.title = RG_IDLE_TITLE;
        rg.setAttribute("aria-label", RG_IDLE_TITLE);
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
    pe.textContent = "🎨";
    pe.title = "画像プロンプト（タグ）を開いて編集する";
    pe.setAttribute("aria-label", "画像プロンプト（タグ）を編集");
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

  // エラー状態のユーザー発言にはエラーバッジと再送ボタンを表示
  if (m.role === "user" && m.status === "error") {
    const errBadge = document.createElement("span");
    errBadge.className = "img-status-badge err";
    errBadge.textContent = "送信エラー: " + (m.error || "応答を受信できませんでした");
    bar.appendChild(errBadge);

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn-stop-wait";
    retryBtn.textContent = "✏ 再送";
    retryBtn.title = "本文を開いて送り直す";
    retryBtn.onclick = () => openEdit(m);
    bar.appendChild(retryBtn);
  }

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

  // 送信状態のテキストは表示しない。
  // 送信中かどうかは ■ 停止 ボタン自身で分かる。

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
  if (!sheet || sheet.classList.contains("hidden")) return;
  const prov = state?.setting?.ai_provider || "antigravity";
  if (prov === "google") {
    el.textContent = googleConfigured ? `Google Gemini API（…${googleKeyHint}）` : "Google Gemini API（APIキー未設定）";
    el.classList.remove("hidden");
    return;
  }
  if (prov === "vertex") {
    const who = vertexStatus.auth_type === "adc" ? "ADC" : (vertexStatus.client_email_hint || "SA");
    el.textContent = vertexConfigured ? `Vertex AI（${vertexStatus.project_id} / ${who}）` : "Vertex AI（認証未設定）";
    el.classList.remove("hidden");
    return;
  }
  const val = $("ideModel") ? $("ideModel").value : "";
  const model = val.startsWith("ag:") ? val.slice(3) : val;
  const r = (ideModels?.models || []).find((x) => x.model === model);
  const txt = r ? modelLeftText(r) : "";
  el.textContent = txt ? `残り利用可能: ${txt}` : "";
  el.classList.toggle("hidden", !txt);
}
function updateFootModelSubInfo() {
  const el = $("footModelSubInfo");
  if (!el) return;
  const prov = state?.setting?.ai_provider || "antigravity";
  if (prov === "google") {
    const curId = state?.setting?.google_model || "";
    const cur = googleModels.find((m) => m.id === curId);
    el.textContent = cur?.outputTokenLimit ? `（出力上限 ${cur.outputTokenLimit}）` : "";
    return;
  }
  if (prov === "vertex") {
    const curId = state?.setting?.vertex_model || "";
    const cur = vertexModels.find((m) => m.id === curId);
    el.textContent = cur?.outputTokenLimit ? `（出力上限 ${cur.outputTokenLimit}）` : "";
    return;
  }
  const val = $("ideModel") ? $("ideModel").value : "";
  const model = val.startsWith("ag:") ? val.slice(3) : val;
  const r = (ideModels?.models || []).find((x) => x.model === model);
  if (!r) {
    el.textContent = "";
    return;
  }
  const m = String(r.label || "").match(/^(.*?)\s*(\(.*?\))\s*$/);
  const sub = m ? m[2] : "";
  const reset = modelResetText;
  const left = (typeof r.left !== "number" ? ""
    : r.left > 0 ? `（残り${Math.max(1, Math.round(r.left * 100))}%）` : `（使い切り${reset(r)}）`);
  el.textContent = (sub ? " " + sub : "") + left;
}
function openModelSheet() {
  if (!state || !state.setting) {
    toast("チャンネルを開いてください", 4000); syncActionControlsVisibility(); return;
  }
  hideReveal();
  $("modelSheet").classList.remove("hidden");
  syncFootBarPlacement();     // #ideModel をシートへ移す（DOM は1つだけ）
  updateModelSheetLeft();
  updateFabState();
  loadModels();               // 開いた時点で残量％を最新化
}
function closeModelSheet() {
  const el = $("modelSheet");
  if (!el || el.classList.contains("hidden")) return;
  el.classList.add("hidden");
  syncFootBarPlacement();     // ドロワー下段へ戻す
  updateFabState();
}

async function openAiToolModal() {
  if (!state || !state.setting) {
    toast("チャンネルを開いてください", 4000);
    syncActionControlsVisibility();
    return;
  }
  hideReveal();
  $("aiToolModal")?.classList.remove("hidden");
  updateFabState();
  syncActionControlsVisibility();

  const prov = state.setting.ai_provider || "antigravity";
  if (prov === "google") {
    if (!googleStatusLoaded) await loadGoogleStatus();
    if (googleConfigured && !googleModels.length) await loadGoogleModels();
  } else if (prov === "vertex") {
    if (!vertexStatusLoaded) await loadVertexStatus();
    if (vertexConfigured && !vertexModels.length) await loadVertexModels();
  }
  renderModels();
  syncGoogleSettingsUI();
  ThinkingUI.render(state.setting);
}

function closeAiToolModal() {
  const el = $("aiToolModal");
  if (!el || el.classList.contains("hidden")) return;
  el.classList.add("hidden");
  updateFabState();
  syncActionControlsVisibility();
}

function render() {
  const s = state;
  syncGoogleSettingsUI();
  if (s?.setting?.ai_provider === "google" && !googleStatusLoaded) loadGoogleStatus();
  if (s?.setting?.ai_provider === "vertex" && !vertexStatusLoaded) loadVertexStatus();
  // 候補（8択）を出すのは「最新のAI応答」だけ。過去の応答では開閉ボタンも出さない
  renderLastAsstId = "";
  for (let i = s.messages.length - 1; i >= 0; i--) {
    if (s.messages[i].role === "assistant" && s.messages[i].id) { renderLastAsstId = s.messages[i].id; break; }
  }
  // poll のたびにメッセージを作り直すので、開いている「⋯」は閉じる
  closeMoreMenu();
  $("title").textContent = s.setting?.title || "RP Image Chat";
  $("ragCount").textContent = (s.setting?.rag_collections || []).length;
  updateElapsedTimeUI(s.setting?.elapsed_time);
  document.title = (s.setting?.title || "") + " - RP Image Chat";
  const nearBottom = !navHoldBottom && (log.scrollHeight - log.scrollTop - log.clientHeight < 120);

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
    if (s.setting?.ide_session || s.setting?.choices_ide_session || ["google", "vertex"].includes(s.setting?.ai_provider)) {
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

  // スマホ限定：AIから新しい返信が届いた瞬間に、本文をすぐ読めるよう上部チップ直下にカード上端を寄せる
  let didAutoScroll = false;
  if (isMobileLayout() && nearBottom && renderLastAsstId && renderLastAsstId !== lastAutoScrolledAsstId) {
    const latestAsstMsg = s.messages.find((m) => m.id === renderLastAsstId && m.role === "assistant" && (m.text || "").trim());
    if (latestAsstMsg) {
      const wrap = document.getElementById("msg-" + renderLastAsstId);
      const bubble = wrap?.querySelector(".msg.assistant") || wrap;
      if (bubble) {
        lastAutoScrolledAsstId = renderLastAsstId;
        scrollAssistantToTop(bubble);
        didAutoScroll = true;
        const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : setTimeout;
        raf(() => {
          scrollAssistantToTop(bubble);
        }, 0);
      }
    }
  }

  if (!didAutoScroll && nearBottom) log.scrollTop = log.scrollHeight;
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
  // ナビ移動で止めた位置から、ユーザー自身が最下段まで戻ったら自動追従を再開する。
  if (navHoldBottom) {
    const gap = log.scrollHeight - log.scrollTop - log.clientHeight;
    if (gap <= 2) navHoldBottom = false;
  }
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
      window.currentSession = s;
      if (typeof PromptToolbar !== "undefined") {
        PromptToolbar.syncWithSession(s);
      }
      if (s.setting?.rotation_notice && s.setting.rotation_notice !== lastRotationNotice) {
        lastRotationNotice = s.setting.rotation_notice;
        if (!first) {
          flash(s.setting.rotation_notice);
        }
      }
      render();
      if (first) {
        navHoldBottom = false;
        log.scrollTop = log.scrollHeight;
        lastAutoScrolledAsstId = renderLastAsstId || "";
      }
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
  if ((typeof document !== "undefined" && document.hidden) || progressBusy || !s || s.id !== sid || !(s.setting?.ide_session || s.setting?.choices_ide_session || ["google", "vertex"].includes(s.setting?.ai_provider)) || !$("progress")) return;
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
      const nearBottom = !navHoldBottom && (log.scrollHeight - log.scrollTop - log.clientHeight < 120);
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
    // 表示は丸い上矢印（CSS）。文字は読み上げ用なので、差し替えても見た目は崩れない
    const sendLabel = sending ? "送信中…" : "送信";
    sendBtn.classList.toggle("sending", Boolean(sending));
    sendBtn.setAttribute("aria-label", sendLabel);
    sendBtn.title = sendLabel;
    const sendText = sendBtn.querySelector(".send-text");
    if (sendText) sendText.textContent = sendLabel;
    else sendBtn.textContent = sendLabel;
  }
  const stopBtn = $("stopBtn");
  if (stopBtn) {
    stopBtn.classList.toggle("hidden", !waiting);
    stopBtn.disabled = stopping || sending;
    // 見た目は ■ だけ（文字は読み上げ/ツールチップで伝える）
    const stopLabel = stopping ? "停止中…" : "送信を停止";
    stopBtn.textContent = "■";
    stopBtn.title = stopLabel;
    stopBtn.setAttribute("aria-label", stopLabel);
  }
  // 応答待ちになると中央の「送信」は「ツール」に戻る（render からも毎回ここを通る）
  syncActionControlsVisibility();
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
    navHoldBottom = false;
    lastAutoScrolledAsstId = "";
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
  syncActionControlsVisibility();   // 添付だけでも中央ボタンが「送信」になる
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
      if (typeof btn.setAttribute === "function") btn.setAttribute("aria-expanded", "false");
    });
    document.querySelectorAll(".choices-panel").forEach((pl) => {
      pl.classList.remove("is-open");
    });
    document.querySelectorAll(".choice-item-btn").forEach((btn) => {
      btn.classList.remove("is-selected");
    });
  }
}

function choicesNoticeText(n) {
  const ic = (n && n.slot === "normal") ? "💬" : "🔞";
  switch (n && n.kind) {
    case "filter": return `${ic} フィルタで1回停止 → 自動で回避して出力`;
    case "suspect": return `${ic} の候補: 健全な内容に差し替わった可能性あり`;
    case "placeholder": return `${ic} の候補: プレースホルダーを一部自動置換しました`;
    case "timeout": return `${ic} の候補: 生成がタイムアウト（応答が届いていません）`;
    case "rescued": return `${ic} の候補: 生成結果を回収しました（.tmp から復元）`;
    default: return "";
  }
}

function choicesDecensorLine(dec, mode) {
  if (!dec || dec.slot !== mode || !(dec.applied || []).length) return null;
  const total = dec.applied.reduce((n, a) => n + (a.count || 0), 0);
  const el = document.createElement("div");
  el.className = "choices-notice is-ok";
  el.textContent = `${mode === "normal" ? "💬" : "🔞"} の候補: 婉曲表現を${total}か所 自動で戻しました`;
  el.title = dec.applied.map((a) => `${a.from} → ${a.to}（${a.count}）`).join("\n") + "\n\n「戻す」でAIが書いた元の文面に戻せます";
  return el;
}

function choicesBox(m, targetContainer) {
  const norm = (arr) => (arr || []).filter((c) => c && typeof c === "object" && typeof c.text === "string" && c.text.trim());
  const normalChoices = norm(m.choices);
  const eroticChoices = norm(m.choices_erotic);
  const hasErotic = eroticChoices.length > 0;
  const autoErotic = !!(typeof state !== "undefined" && state && state.setting && state.setting.choices_erotic_auto);
  if (!normalChoices.length && !hasErotic && !m.choices_status && !m.choices_notice && !m.choices_decensor && !m.choices_timeout && !autoErotic) {
    return targetContainer || document.createDocumentFragment();
  }

  const msgKey = m.id || ("msg-" + (m.ts || Math.random()));
  // 最新のAI応答以外は候補を出さない（古い候補は返信に使えないので、触れないほうが事故が少ない）
  if (typeof renderLastAsstId !== "undefined" && renderLastAsstId && m.id && m.id !== renderLastAsstId) return targetContainer || document.createDocumentFragment();
  const isPending = m.choices_status === "pending" || m.choices_status === "processing";
  const isEroticRegen = isPending && m.choices_type === "erotic";

  // 表示モード: 手で切り替えた記憶 > 🔞生成中 > 「🔞が通常より新しければ🔞」
  let mode = displayChoicesMode.get(msgKey);
  if (!mode) {
    const eTs = m.choices_erotic_updated_ts || "";
    const nTs = m.choices_updated_ts || "";
    mode = (hasErotic && eTs && eTs > nTs) ? "erotic" : "normal";
    if (autoErotic && !normalChoices.length) mode = "erotic";   // 🔞既定のチャンネル
  }
  if (mode === "erotic" && !hasErotic && !isEroticRegen && !autoErotic) mode = "normal";
  if (isEroticRegen) mode = "erotic";
  const choices = mode === "erotic" ? eroticChoices : normalChoices;

  if (isPending && !userClosedChoicesSet.has(msgKey)) openChoicesSet.add(msgKey);
  const isOpen = openChoicesSet.has(msgKey);

  const wrap = targetContainer || document.createElement("div");
  if (!targetContainer) wrap.className = "choices-container";

  // 状態をまとめて更新してから render する（旧DOMを触らない）
  const commit = (patch) => {
    if (patch.mode) displayChoicesMode.set(msgKey, patch.mode);
    if (patch.open) { openChoicesSet.add(msgKey); userClosedChoicesSet.delete(msgKey); }
    render();
  };

  // 通知（折りたたみ時も見える）
  const notice = m.choices_notice || (m.choices_timeout ? { kind: "timeout", slot: m.choices_slot || m.choices_type || "normal" } : null);
  if (notice) {
    const txt = choicesNoticeText(notice);
    if (txt) {
      const el = document.createElement("div");
      el.className = "choices-notice" + (notice.kind === "suspect" ? " is-warn" : "");
      el.textContent = txt;
      el.title = notice.kind === "filter"
        ? "生成中に自動フィルタで1回止まりましたが、回避して出力しました"
        : "生成結果についての注意です（候補はそのまま使えます）";
      wrap.appendChild(el);
    }
  }
  // 婉曲表現を戻した記録（通知とは別枠。ここだけ「戻す」できる）
  const decEl = choicesDecensorLine(m.choices_decensor, mode);
  if (decEl) {
    const undoBtn = document.createElement("button");
    undoBtn.type = "button";
    undoBtn.className = "choices-undo-btn";
    undoBtn.textContent = "戻す";
    undoBtn.title = "自動置換を取り消して、AIが書いた元の文面に戻します";
    undoBtn.addEventListener("click", async (e) => {
      if (e && typeof e.stopPropagation === "function") e.stopPropagation();
      try {
        const r = await checked("/api/message/undo-decensor", { sid: (typeof sid !== "undefined" ? sid : ""), id: m.id });
        if (r && r.error) return alert(r.error);
        m.choices_decensor = null;
        if (typeof poll === "function") await poll();
      } catch (err) {
        alert("戻せませんでした: " + err.message);
      }
    });
    decEl.appendChild(undoBtn);
    wrap.appendChild(decEl);
  }

  // 一覧パネル（左上に ↻、右上にモードスイッチ）
  const panel = document.createElement("div");
  panel.className = "choices-panel" + (isOpen ? " is-open" : "");

  const headRow = document.createElement("div");
  headRow.className = "choices-head-row";

  // ↻ 表示中の候補を作り直す
  const rerollBtn = document.createElement("button");
  rerollBtn.type = "button";
  rerollBtn.className = "choices-reroll-btn";
  rerollBtn.textContent = isPending ? "⌛ 生成中" : "↻ 作り直す";
  rerollBtn.title = mode === "erotic" ? "🔞の候補を作り直す" : "💬の候補を作り直す";
  rerollBtn.setAttribute("aria-label", rerollBtn.title);
  rerollBtn.disabled = isPending;   // 候補が空でも「↻ 作り直す」で生成できる
  rerollBtn.addEventListener("click", async (e) => {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    if (!rerollBtn.disabled) await startChoicesRegen(mode);
  });

  // iOS風スイッチ: ノブ（白い円）が今のモードのアイコンになる
  const sw = document.createElement("button");
  sw.type = "button";
  sw.className = "choices-switch" + (mode === "erotic" ? " is-erotic" : "")
    + (hasErotic ? "" : " has-no-erotic") + (isPending ? " is-busy" : "");
  sw.setAttribute("role", "switch");
  sw.setAttribute("aria-checked", mode === "erotic" ? "true" : "false");
  sw.setAttribute("aria-label", "🔞の候補を表示する");
  sw.title = isPending ? "生成中です"
    : mode === "erotic" ? "通常の候補に戻す"
    : hasErotic ? "🔞の候補を表示する" : "🔞の候補を生成する";
  sw.disabled = isPending;

  const icNormal = document.createElement("span");
  icNormal.className = "choices-ic ic-normal";
  icNormal.textContent = "💬";
  icNormal.setAttribute("aria-hidden", "true");
  const icErotic = document.createElement("span");
  icErotic.className = "choices-ic ic-erotic";
  icErotic.textContent = isEroticRegen ? "⌛" : "🔞";
  icErotic.setAttribute("aria-hidden", "true");
  const knob = document.createElement("span");
  knob.className = "choices-knob";
  knob.setAttribute("aria-hidden", "true");

  sw.appendChild(icNormal);
  sw.appendChild(icErotic);
  sw.appendChild(knob);
  sw.addEventListener("click", async (e) => {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    if (sw.disabled || m.choices_status) return;
    if (mode === "erotic") { commit({ mode: "normal" }); return; }
    if (hasErotic) { commit({ mode: "erotic", open: true }); return; }
    await startChoicesRegen("erotic");
  });

  const spacer = document.createElement("span");
  spacer.className = "choices-head-sp";
  headRow.appendChild(rerollBtn);
  headRow.appendChild(spacer);
  // このチャンネルが「🔞を最初から作る」設定かどうかを、その場で分かるように出す
  if (autoErotic) {
    const badge = document.createElement("span");
    badge.className = "choices-auto-badge";
    badge.textContent = "🔞既定";
    badge.title = "このチャンネルは新しいターンでも🔞の候補を作ります（設定で変更）";
    headRow.appendChild(badge);
  }
  headRow.appendChild(sw);

  async function startChoicesRegen(kind) {
    const type = kind === "erotic" ? "erotic" : "normal";
    // 1) 先に状態を全部決める
    m.choices_status = "pending";
    m.choices_type = type;
    m.choices_notice = null;
    m.choices_decensor = null;
    delete m.choices_timeout;
    sw.disabled = true;
    rerollBtn.disabled = true;
    selectedChoiceMap.delete(`${msgKey}:normal`);
    selectedChoiceMap.delete(`${msgKey}:erotic`);
    displayChoicesMode.set(msgKey, type);
    openChoicesSet.add(msgKey);
    userClosedChoicesSet.delete(msgKey);
    render();                       // 2) ここで1回だけ（⌛・開いたまま・スイッチ無効）
    try {
      const curSid = (typeof state !== "undefined" && state && state.id) ? state.id : (typeof sid !== "undefined" ? sid : "");
      const res = await api("/api/message/regenerate-choices", { sid: curSid, id: m.id, type });
      if (res && res.error) alert("再生成エラー: " + res.error);
      if (typeof poll === "function") await poll();
      if (typeof wakePoll === "function") wakePoll();
    } catch (err) {
      alert("通信エラー: " + (err.message || err));
    }
  }

  // ツールバーの開閉ボタン（🔁 🎨 の隣、⋯ の手前）
  const toggleBtn = document.createElement("button");
  toggleBtn.type = "button";
  toggleBtn.className = "choices-toggle-btn" + (isOpen ? " is-open" : "") + (mode === "erotic" ? " is-erotic" : "");
  toggleBtn.title = "返答候補の一覧を開閉";
  toggleBtn.setAttribute("aria-label", "返答候補の一覧を開閉");
  toggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
  const tIc = document.createElement("span");
  tIc.className = "choices-mode-ic";
  tIc.textContent = (isPending && !isOpen) ? "⌛" : (mode === "erotic" ? "🔞" : "💬");
  const tCv = document.createElement("span");
  tCv.className = "cv";
  tCv.textContent = isOpen ? "▴" : "▾";
  toggleBtn.appendChild(tIc);
  toggleBtn.appendChild(tCv);
  toggleBtn.addEventListener("click", () => {
    const nowOpen = !panel.classList.contains("is-open");
    panel.classList.toggle("is-open", nowOpen);
    toggleBtn.classList.toggle("is-open", nowOpen);
    toggleBtn.setAttribute("aria-expanded", nowOpen ? "true" : "false");
    tCv.textContent = nowOpen ? "▴" : "▾";
    if (nowOpen) { openChoicesSet.add(msgKey); userClosedChoicesSet.delete(msgKey); }
    else { openChoicesSet.delete(msgKey); userClosedChoicesSet.add(msgKey); }
  });

  const listEl = document.createElement("div");
  listEl.className = "choices-grid";

  if (!choices.length && isPending) {
    const hint = document.createElement("div");
    hint.className = "choices-loading-hint";
    hint.textContent = isEroticRegen ? "🔞 ドスケベな返答候補を生成中…" : "💬 返答候補を生成中…";
    listEl.appendChild(hint);
  } else if (!choices.length) {
    // 🔞を既定にしているチャンネルでは通常の候補が無いので、作り方を案内する
    const hint = document.createElement("div");
    hint.className = "choices-loading-hint";
    hint.textContent = mode === "erotic"
      ? "🔞の候補はまだありません（↻ 作り直すで生成）"
      : "通常の候補はありません（↻ 作り直すで生成）";
    listEl.appendChild(hint);
  }

  choices.forEach((choice, cIdx) => {
    const key = `${msgKey}:${mode}`;
    const itemBtn = document.createElement("button");
    itemBtn.type = "button";
    itemBtn.className = "choice-item-btn" + (selectedChoiceMap.get(key) === cIdx ? " is-selected" : "");

    const badge = document.createElement("span");
    badge.className = "choice-label-badge";
    badge.textContent = choice.label || "候補";
    const textSpan = document.createElement("span");
    textSpan.className = "choice-text-body";
    textSpan.textContent = choice.text || "";
    itemBtn.appendChild(badge);
    itemBtn.appendChild(textSpan);

    itemBtn.addEventListener("click", () => {
      const inputEl = $("messageInput") || document.getElementById("message-input");
      if (!inputEl) return;
      lastSelectedChoiceText = choice.text || "";
      inputEl.value = choice.text || "";
      inputEl.dispatchEvent(new Event("input", { bubbles: true }));
      autoResizeInput();
      inputEl.focus();
      const len = inputEl.value.length;
      inputEl.setSelectionRange(len, len);
      selectedChoiceMap.clear();
      selectedChoiceMap.set(key, cIdx);
      if (typeof document.querySelectorAll === "function") {
        document.querySelectorAll(".choice-item-btn.is-selected").forEach((b) => b.classList.remove("is-selected"));
      }
      listEl.querySelectorAll(".choice-item-btn").forEach((b) => b.classList.toggle("is-selected", b === itemBtn));
    });
    listEl.appendChild(itemBtn);
  });

  panel.appendChild(headRow);
  panel.appendChild(listEl);
  wrap.appendChild(toggleBtn);
  wrap.appendChild(panel);
  return wrap;
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
// 「ツール」の 🧑 キャラ（状態）が開く対象。直近の「状態を持つAI応答」、無ければ最後のAI応答。
// （状態は応答ごとのデータなので、過去の応答を直すときは ⋯ の「キャラの状態」を使う）
function latestStateTarget() {
  const msgs = (state && state.messages) || [];
  if (!msgs.length) return null;
  const isAi = (x) => x && x.role !== "user";
  const withState = msgs.slice().reverse().find((x) => isAi(x) && Array.isArray(x.state) && x.state.length > 0);
  if (withState) return withState;
  if (!(state.setting && state.setting.rp_state)) return null;
  return msgs.slice().reverse().find(isAi) || null;
}

async function openState(m, msgs) {
  let curState = m.state;
  let ragHits = null;
  if (sid && m.id) {
    try {
      const res = await (await fetch(`/api/message/state?sid=${encodeURIComponent(sid)}&mid=${encodeURIComponent(m.id)}&fallback=1`)).json();
      if (res && res.ok) {
        ragHits = res.rag_hits || null;
        if (!curState && Array.isArray(res.state) && res.state.length > 0) {
          curState = res.state;
          m.state = curState;
        }
      }
    } catch (e) {
      console.warn("fetch state error:", e);
    }
  }
  // この応答に無ければ、それより前に記録した状態から始める（サーバーの state_before と同じ）
  const prev = curState || msgs.slice(0, msgs.indexOf(m)).reverse().find((x) => x.role === "assistant" && Array.isArray(x.state))?.state || [];
  // 重要＝類似度に関係なく毎回渡す枠（ルール・ピン留め呼称等。呼称はピン留め時のみ重要枠、通常は関連枠）／関連＝今回の会話に近い記憶と未達の約束
  // 件数はモーダル左下に小さく添える（タイトルには入れない）
  const hitsMeta = ragHits
    ? `🧠 （重要${ragHits.tier1 || 0}・関連${ragHits.tier2 || 0}）`
    : "";
  openText("キャラの状態（この応答のとき）",
    "「■ 名前」の下に「衣装＆露出状態:」「上着＆露出状態:」「下着＆露出状態:」「装具＆大人のおもちゃ:」「挿入・結合状態:」「身体痕跡・付着物:」「体勢:」「表情:」「場所:」「構図:」と、今の状態を「- 」で1行ずつ、「内心:」の後に内心を書く。キャラを消すときはその塊ごと消す。保存すると、次の入力で AI がこの状態を読む。",
    stateText(prev) || "■ キャラ名\n衣装＆露出状態: \n上着＆露出状態: \n下着＆露出状態: \n装具＆大人のおもちゃ: なし\n挿入・結合状態: なし\n身体痕跡・付着物: なし\n体勢: \n表情: \n場所: \n構図: \n- 今の状態\n内心: ",
    async (text) => {
      const parsed = parseState(text);
      await checked("/api/message/state", { sid, id: m.id, state: parsed });
      m.state = parsed;
      await poll();
    },
    hitsMeta,
    ragHits ? () => openRagHitsModal(sid, m.id) : null
  );
}

let ragHitsReqSeq = 0;

function buildRagHitCardNode(item) {
  const normCat = (c) => (c === "taboo" || c === "rule_taboo" ? "rule" : (c === "item_status" ? "item" : (c || "event")));
  const cat = normCat(item.category);

  const card = document.createElement("div");
  card.className = `rag-entry rag-entry-${cat}`;
  if (item.missing || (item.status && item.status !== "active")) {
    card.classList.add("rag-entry-resolved");
  }

  const header = document.createElement("div");
  header.className = "rag-entry-head";

  const badges = document.createElement("div");
  badges.className = "rag-entry-badges";

  if (!item.missing) {
    const catBadge = document.createElement("span");
    catBadge.className = `badge-cat badge-cat-${cat}`;
    catBadge.textContent = (typeof CAT_LABELS !== "undefined" && CAT_LABELS[cat]) ? CAT_LABELS[cat] : (cat || "記憶");
    badges.appendChild(catBadge);

    if (item.elapsed_time) {
      const meta = document.createElement("span");
      meta.className = "rag-mem-meta";
      if (typeof item.elapsed_time === "object") {
        const d = item.elapsed_time.day != null ? `${item.elapsed_time.day}日目` : "";
        const p = item.elapsed_time.period || "";
        meta.textContent = d && p ? `${d}・${p}` : (d || p);
      } else {
        meta.textContent = String(item.elapsed_time || "").trim();
      }
      if (meta.textContent) {
        badges.appendChild(meta);
      }
    }
  } else {
    const meta = document.createElement("span");
    meta.className = "rag-mem-meta";
    meta.textContent = "参照不可";
    badges.appendChild(meta);
  }

  const scoreEl = document.createElement("span");
  scoreEl.className = "rag-hits-score";
  const hasScore = item.score !== null && item.score !== undefined && !Number.isNaN(Number(item.score));
  scoreEl.textContent = hasScore ? Number(item.score).toFixed(2) : "--";
  scoreEl.title = hasScore ? `スコア: ${Number(item.score).toFixed(4)}` : "スコア未記録";

  header.append(badges, scoreEl);

  const body = document.createElement("div");
  body.className = "rag-mem-content";
  body.style.whiteSpace = "pre-wrap";
  body.textContent = item.content || "";

  card.append(header, body);
  return card;
}

async function openRagHitsModal(targetSid, targetMid) {
  const modal = $("ragHitsModal");
  const body = $("ragHitsBody");
  if (!modal || !body || !targetSid) return;

  const seq = ++ragHitsReqSeq;
  modal.classList.remove("hidden");
  updateFabState();
  body.scrollTop = 0;
  body.innerHTML = `<div class="muted" style="padding:12px;">記憶を読み込み中...</div>`;

  try {
    const url = `/api/rag/hits?sid=${encodeURIComponent(targetSid)}&mid=${encodeURIComponent(targetMid || "")}`;
    const res = await (await fetch(url)).json();
    if (seq !== ragHitsReqSeq) return;
    if (!res || !res.ok) throw new Error(res?.error || "取得失敗");

    const t1 = Array.isArray(res.tier1) ? res.tier1 : [];
    const t2 = Array.isArray(res.tier2) ? res.tier2 : [];
    const t1Count = res.tier1_count ?? t1.length;
    const t2Count = res.tier2_count ?? t2.length;

    body.innerHTML = "";

    const renderGroup = (labelTitle, count, list) => {
      const group = document.createElement("div");
      group.className = "rag-hits-group";
      const label = document.createElement("div");
      label.className = "rag-hits-group-label";
      label.textContent = `${labelTitle} ${count}件`;
      group.appendChild(label);

      if (!list.length) {
        const empty = document.createElement("div");
        empty.className = "muted";
        empty.style.padding = "4px 2px";
        empty.textContent = "なし";
        group.appendChild(empty);
      } else {
        for (const item of list) {
          group.appendChild(buildRagHitCardNode(item));
        }
      }
      return group;
    };

    body.appendChild(renderGroup("重要", t1Count, t1));
    body.appendChild(renderGroup("関連", t2Count, t2));
    body.scrollTop = 0;
  } catch (e) {
    if (seq !== ragHitsReqSeq) return;
    body.innerHTML = `<div class="danger" style="padding:12px;">記憶の取得に失敗しました</div>`;
  }
}

function closeRagHitsModal() {
  const modal = $("ragHitsModal");
  if (!modal || modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  updateFabState();
}

if ($("ragHitsClose")) {
  $("ragHitsClose").onclick = (e) => {
    e.stopPropagation();
    closeRagHitsModal();
  };
}
if ($("ragHitsModal")) {
  $("ragHitsModal").addEventListener("click", (e) => {
    if (e.target === $("ragHitsModal")) {
      e.stopPropagation();
      closeRagHitsModal();
    }
  });
}
if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "Esc") {
      const m = $("ragHitsModal");
      if (m && !m.classList.contains("hidden")) {
        e.preventDefault();
        e.stopImmediatePropagation();
        closeRagHitsModal();
      }
    }
  }, true);
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
        if (typeof PromptToolbar !== "undefined") PromptToolbar.syncTextareaUI($("promptEdit"));
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
            if (typeof PromptToolbar !== "undefined") PromptToolbar.syncTextareaUI($("promptEdit"));
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
  if (typeof PromptToolbar !== "undefined") {
    PromptToolbar.syncTextareaUI($("promptEdit"));
  }
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

// =============================================================================
// 長期記憶（RAG）5段階優先度定義＆ヘルパー関数群
// =============================================================================
const MEMORY_PRIORITY_LEVELS = [
  {
    key: "pinned",
    label: "📌 ピン留め",
    short: "📌 ピン留め (常時)",
    actionLabel: "📌固定▼",
    desc: "スコアに関係なく常時前提枠（最大4枠）に最優先で固定します"
  },
  {
    key: "highest",
    label: "▲▲ 最優先 (×1.25)",
    short: "▲▲ 最優先 (×1.25)",
    actionLabel: "📍最優先▼",
    desc: "なかなか出ない記憶用。単語の直接一致がなくても文脈が近ければ優先想起されます"
  },
  {
    key: "high",
    label: "▲ 優先 (×1.12)",
    short: "▲ 優先 (×1.12)",
    actionLabel: "📍優先▼",
    desc: "惜しくも選ばれにくい記憶を出やすくし、標準の記憶より一歩優先します"
  },
  {
    key: "default",
    label: "― 標準 (×1.00)",
    short: "― 標準 (×1.00)",
    actionLabel: "📍標準▼",
    desc: "通常のハイブリッド検索スコアで評価します"
  },
  {
    key: "low",
    label: "▼ 抑制 (×0.82)",
    short: "▼ 抑制 (×0.82)",
    actionLabel: "📍抑制▼",
    desc: "普段は控えめに沈め、明確に関連する話題が出た時だけ想起させます"
  },
];

function resolveMemoryPriorityKey(mem) {
  const p = mem && mem.priority;
  if (p === "slightly_high") return "high";
  if (p && MEMORY_PRIORITY_LEVELS.some(item => item.key === p)) return p;
  const b = Number((mem && mem.boost) || 0);
  if (b >= 0.08) return "pinned";
  if (b >= 0.03) return "highest";
  if (b > 1e-6) return "high";
  if (b < -1e-6) return "low";
  return "default";
}

function ensurePriorityFieldMounted() {
  if (document.getElementById("memPriorityPills")) return;
  const contentEl = document.getElementById("ragMemContent");
  if (!contentEl) return;
  const parentGroup = contentEl.closest(".form-group") || contentEl.parentElement;
  if (!parentGroup || !parentGroup.parentElement) return;

  const wrap = document.createElement("div");
  wrap.className = "form-group";
  wrap.innerHTML = `
    <label>想起の優先度</label>
    <div id="memPriorityPills" class="mem-priority-pills"></div>
    <div id="memPriorityHint" class="mem-prio-hint"></div>
    <input type="hidden" id="memPriorityInput" value="default">
  `;
  parentGroup.parentElement.insertBefore(wrap, parentGroup);
}

function renderPriorityPills(selectedKey = "default") {
  ensurePriorityFieldMounted();
  const container = document.getElementById("memPriorityPills");
  const hint = document.getElementById("memPriorityHint");
  const hiddenInput = document.getElementById("memPriorityInput");
  if (!container || !hiddenInput) return;

  const normKey = resolveMemoryPriorityKey({ priority: selectedKey });
  hiddenInput.value = normKey;
  container.innerHTML = "";

  const currentObj = MEMORY_PRIORITY_LEVELS.find(p => p.key === normKey) || MEMORY_PRIORITY_LEVELS[3];
  if (hint) hint.textContent = currentObj.desc;

  for (const p of MEMORY_PRIORITY_LEVELS) {
    const btn = document.createElement("button");
    btn.type = "button";
    const isActive = p.key === normKey;
    btn.className = `mem-prio-pill mem-prio-${p.key}` + (isActive ? " active" : "");
    btn.textContent = p.label;
    btn.onclick = () => renderPriorityPills(p.key);
    container.appendChild(btn);
  }
}

function createInlinePrioritySelect(sessionId, mem) {
  const select = document.createElement("select");
  const currentKey = resolveMemoryPriorityKey(mem);
  select.className = `rag-entry-priority-select mem-priority-select mem-prio-${currentKey}` + (currentKey === "pinned" ? " is-pinned" : "");
  const currentObj = MEMORY_PRIORITY_LEVELS.find(p => p.key === currentKey) || MEMORY_PRIORITY_LEVELS[3];
  select.title = `想起の優先度を変更（現在: ${currentObj.label}）`;
  select.setAttribute("aria-label", `想起の優先度: ${currentObj.label}`);
  select.addEventListener("click", (e) => e.stopPropagation());

  for (const p of MEMORY_PRIORITY_LEVELS) {
    const opt = document.createElement("option");
    opt.value = p.key;
    opt.textContent = p.actionLabel || p.short;
    if (p.key === currentKey) opt.selected = true;
    select.appendChild(opt);
  }

  select.addEventListener("change", async (e) => {
    e.stopPropagation();
    const newKey = e.target.value;
    const prevKey = resolveMemoryPriorityKey(mem);
    select.className = `rag-entry-priority-select mem-priority-select mem-prio-${newKey}` + (newKey === "pinned" ? " is-pinned" : "");
    select.disabled = true;
    try {
      const res = await fetch(`/api/rag/memories/${encodeURIComponent(mem.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          priority: newKey
        })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "更新失敗");
      if (data.memory && data.memory.priority) {
        mem.priority = data.memory.priority;
      } else {
        delete mem.priority;
      }
      delete mem.boost;
      await loadRagMemories();
    } catch (err) {
      alert(`優先度の変更に失敗しました: ${err.message}`);
      select.value = prevKey;
      select.className = `rag-entry-priority-select mem-priority-select mem-prio-${prevKey}` + (prevKey === "pinned" ? " is-pinned" : "");
    } finally {
      select.disabled = false;
    }
  });

  return select;
}

const CAT_LABELS = {
  calling: "🏷️ 呼称",
  rule: "📜 ルール・禁忌",
  item: "🎁 アイテム",
  status: "💫 状態",
  promise: "🤙 約束",
  char_fact: "🗝️ キャラ",
  user_fact: "👤 好み",
  relationship: "💖 関係",
  event: "⚡ 事件",
};

let ragMemFilter = "all";
let ragAllMemories = [];
// 並び順：desc = 追加日時の新しい順（デフォルト）、asc = 古い順
let ragMemSortDir = "desc";

// 記憶の追加日時（UNIX秒）。無い古いデータは更新日時で代用する
function ragMemTimestamp(mem) {
  if (!mem) return null;
  const raw = mem.created_at != null ? mem.created_at : (mem.updated_at != null ? mem.updated_at : null);
  if (raw == null) return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  const num = Number(raw);
  if (Number.isFinite(num) && String(raw).trim() !== "") return num;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? null : parsed / 1000;
}

function compareRagMemories(a, b) {
  // ピン留め（pinned）は常に最上位に表示
  const pa = (a && resolveMemoryPriorityKey(a) === "pinned") ? 1 : 0;
  const pb = (b && resolveMemoryPriorityKey(b) === "pinned") ? 1 : 0;
  if (pa !== pb) return pb - pa;

  const ta = ragMemTimestamp(a);
  const tb = ragMemTimestamp(b);
  let diff;
  if (ta != null && tb != null) {
    diff = ta - tb;
  } else if (ta != null) {
    diff = 1;   // 日時ありを新しい側に寄せる
  } else if (tb != null) {
    diff = -1;
  } else {
    diff = 0;
  }
  // 日時が同じ（同時刻）ならターン番号で安定させる
  if (diff === 0) {
    const sa = Number(a && a.turn_idx);
    const sb = Number(b && b.turn_idx);
    if (Number.isFinite(sa) && Number.isFinite(sb) && sa !== sb) diff = sa - sb;
  }
  return ragMemSortDir === "asc" ? diff : -diff;
}

function updateRagMemSortBtn() {
  const btn = $("ragMemSortBtn");
  if (!btn) return;
  const desc = ragMemSortDir === "desc";
  btn.textContent = desc ? "⬇ 新しい順" : "⬆ 古い順";
  btn.title = desc
    ? "追加日時の新しい順（クリックで古い順に切替）"
    : "追加日時の古い順（クリックで新しい順に切替）";
  btn.classList.toggle("asc", !desc);
  btn.setAttribute("aria-pressed", desc ? "true" : "false");
}

function setupRagFilterTabs() {
  const container = $("ragMemFilterTabs");
  if (container && !container._bound) {
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
  const sortBtn = $("ragMemSortBtn");
  if (sortBtn && !sortBtn._bound) {
    sortBtn._bound = true;
    sortBtn.onclick = () => {
      ragMemSortDir = ragMemSortDir === "desc" ? "asc" : "desc";
      updateRagMemSortBtn();
      renderRagMemories();
    };
  }
  updateRagMemSortBtn();
}

async function openRag() {
  if (!sid) return;
  $("ragModal").classList.remove("hidden");
  ragEditingEntry = null;
  ragMemEditingId = null;
  $("ragEditor").classList.add("hidden");
  $("ragMemEditor").classList.add("hidden");
  syncRagMemEditingClass();
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
  syncRagMemEditingClass();
  syncRagModeBtn(tab);
}

// 上の2つのタブ（記憶 / 知識）のうち、今見ている面を点灯させる
function syncRagModeBtn(tab) {
  const custom = tab !== "memory";
  const memBtn = $("ragTabMemoryTop");
  const knBtn = $("ragTabKnowledge");
  if (memBtn) {
    memBtn.classList.toggle("active", !custom);
    memBtn.setAttribute("aria-selected", String(!custom));
  }
  if (knBtn) {
    knBtn.classList.toggle("active", custom);
    knBtn.setAttribute("aria-selected", String(custom));
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
  // 旧データ（taboo / rule_taboo）は rule に寄せて表示する
  const normCat = (c) => (c === "taboo" || c === "rule_taboo" ? "rule" : (c === "item_status" ? "item" : (c || "promise")));
  const filtered = ragMemFilter === "all" ? ragAllMemories : ragAllMemories.filter(m => {
    const c = normCat(m.category);
    if (c === ragMemFilter) return true;
    return false;
  });
  // 追加日時で並び替え（デフォルトは新しい順）
  const displayList = (filtered || []).slice().sort(compareRagMemories);
  if (!displayList || !displayList.length) {
    list.innerHTML = `<p class="muted" style="padding:12px;">${ragMemFilter === "all" ? "記憶はまだありません。会話が進むと自動で蓄積されます（手動追加も可能）。" : "このカテゴリの記憶はありません。"}</p>`;
    return;
  }
  for (const mem of displayList) {
    const cat = normCat(mem.category);
    const pKey = resolveMemoryPriorityKey(mem);
    const card = document.createElement("div");
    card.className = `rag-entry rag-entry-${cat}`;
    if (pKey === "pinned") card.classList.add("rag-entry-pinned");
    // 達成済（解決済み）の記憶は薄く見せる（バッジは下で付ける）
    if ((mem.status || "active") !== "active") card.classList.add("rag-entry-resolved");

    const header = document.createElement("div");
    header.className = "rag-entry-head";

    const badges = document.createElement("div");
    badges.className = "rag-entry-badges";

    const catBadge = document.createElement("span");
    catBadge.className = `badge-cat badge-cat-${cat}`;
    catBadge.textContent = CAT_LABELS[cat] || cat || "記憶";

    badges.append(catBadge);

    const srcBadge = document.createElement("span");
    srcBadge.className = `badge-source badge-source-${mem.source || "auto"}`;
    // 自動 = ロボット / 手動 = パー（文字だと折り返しの原因になる）
    const manual = mem.source === "manual";
    srcBadge.textContent = manual ? "✋" : "🤖";
    srcBadge.title = manual ? "手動で追加" : "会話から自動で抽出";
    srcBadge.setAttribute("aria-label", manual ? "手動" : "自動");

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

    // 自動/手動のアイコンと日時は 1段下の行に置く
    const sub = document.createElement("div");
    sub.className = "rag-entry-sub";
    sub.append(srcBadge, meta);

    const actions = document.createElement("div");
    actions.className = "rag-entry-actions";

    // 優先度ドロップダウン（固定/優先度を統合したボタン）
    actions.appendChild(createInlinePrioritySelect(sid, mem));

    const jumpBtn = document.createElement("button");
    jumpBtn.type = "button";
    jumpBtn.className = "sub rag-jump-btn";
    jumpBtn.textContent = "💬";
    jumpBtn.setAttribute("aria-label", "会話へ");
    if (mem.mid) {
      jumpBtn.title = "この記憶が抽出された会話へジャンプ";
      jumpBtn.onclick = () => {
        $("ragModal").classList.add("hidden");
        scrollToMessage(mem.mid);
      };
    } else {
      jumpBtn.title = "会話画面へ戻る";
      jumpBtn.onclick = () => {
        $("ragModal").classList.add("hidden");
      };
    }
    actions.appendChild(jumpBtn);

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "rag-entry-edit";
    editBtn.textContent = "✏️";
    editBtn.title = "編集";
    editBtn.setAttribute("aria-label", "編集");
    editBtn.onclick = () => openRagMemEditor(mem);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "rag-entry-del";
    delBtn.textContent = "🗑";
    delBtn.title = "削除";
    delBtn.setAttribute("aria-label", "削除");
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
    header.append(badges, actions, sub);

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
  $("ragMemContent").value = mem ? (mem.content || "") : "";
  renderPriorityPills(mem ? resolveMemoryPriorityKey(mem) : "default");
  $("ragMemView").classList.add("hidden");
  $("ragMemEditor").classList.remove("hidden");
  syncRagMemEditingClass();
  $("ragMemContent").focus();
  if (isMobileLayout()) rafOnce(keepFocusedVisible);
}

// 編集中かどうかを body クラスに映す（スマホは編集中だけシートを中身の高さに縮める）
function syncRagMemEditingClass() {
  const el = $("ragMemEditor");
  const on = !!(el && !el.classList.contains("hidden"));
  if (document.body && document.body.classList) document.body.classList.toggle("rag-mem-editing", on);
}

function closeRagMemEditor() {
  ragMemEditingId = null;
  // キーボードを閉じてから一覧へ戻す（開いたままだと一覧が半分しか見えない）
  if ($("ragMemContent") && typeof $("ragMemContent").blur === "function") $("ragMemContent").blur();
  $("ragMemEditor").classList.add("hidden");
  $("ragMemView").classList.remove("hidden");
  syncRagMemEditingClass();
}

async function saveRagMem() {
  const category = $("ragMemCategory").value;
  const content = $("ragMemContent").value.trim();
  const priority = $("memPriorityInput") ? $("memPriorityInput").value : ($("ragMemPriority") ? $("ragMemPriority").value : "default");
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
        status,
        priority
      }, "PUT");
    } else {
      await checked("/api/rag/memories", {
        session_id: sid,
        category,
        content,
        status,
        priority
      }, "POST");
    }
    closeRagMemEditor();
    await loadRagMemories();
  } catch (e) {
    alert(e.message);
  }
}

function regenDiffLabel(before, after) {
  const a = Number(after) || 0;
  if (before === null || before === undefined || !Number.isFinite(Number(before))) return `記憶 ${a}件`;
  const b = Number(before);
  const d = a - b;
  return `記憶 ${a}件（開始時 ${b}件 / ${d >= 0 ? "+" : ""}${d}件）`;
}

function setRegenButtonsDisabled(disabled) {
  const btn = $("ragMemRegenBtn");
  const resetBtn = $("ragMemRegenReset");
  if (btn) btn.disabled = disabled;
  if (resetBtn) resetBtn.disabled = disabled;
}

function startRegenStatusPolling() {
  setRegenButtonsDisabled(true);
  const progressBox = $("ragMemProgress");
  const progressBar = $("ragMemProgressBar");
  const progressText = $("ragMemProgressText");

  if (progressBox) progressBox.classList.remove("hidden");

  if (ragMemRegenTimer) clearInterval(ragMemRegenTimer);
  ragMemRegenTimer = setInterval(async () => {
    try {
      const st = await checked("/api/rag/memories/regenerate/status?session_id=" + encodeURIComponent(sid));
      if (!st) return;
      if (st.before !== null && st.before !== undefined) ragMemRegenBefore = Number(st.before);
      const pct = Math.min(100, Math.max(0, st.progress || 0));
      if (progressBar) progressBar.style.width = pct + "%";
      if (progressText) progressText.textContent = `記憶を再構築中... (${st.processed_chunks || 0}/${st.total_chunks || 0} チャンク) - ${regenDiffLabel(ragMemRegenBefore, st.memories_count)}`;

      if (st.status === "completed" || st.status === "done" || st.status === "idle") {
        clearInterval(ragMemRegenTimer);
        ragMemRegenTimer = null;
        setRegenButtonsDisabled(false);
        if (progressBox) progressBox.classList.add("hidden");
        const hasAfter = st.after !== null && st.after !== undefined;
        const after = Number(hasAfter ? st.after : st.memories_count) || 0;
        const before = ragMemRegenBefore;
        const diff = (before === null || !hasAfter) ? null : after - Number(before);
        flash(diff === null
          ? (hasAfter ? `記憶の再生成が完了しました（記憶 ${after}件）` : "記憶の再生成が完了しました")
          : `記憶の再生成が完了しました（${diff >= 0 ? "+" : ""}${diff}件 / 記憶 ${after}件）`);
        await loadRagMemories();
      } else if (st.status === "error") {
        clearInterval(ragMemRegenTimer);
        ragMemRegenTimer = null;
        setRegenButtonsDisabled(false);
        if (progressBox) progressBox.classList.add("hidden");
        alert("記憶の再生成でエラーが発生しました: " + (st.error || ""));
      }
    } catch (e) {
      // Continue polling
    }
  }, 1500);
}

async function regenerateRagMemories(reset = false) {
  if (!sid) return;
  const isReset = reset === true;
  if (isReset) {
    if (!confirm(
      "🗑 自動記憶を全消しして、会話ログから作り直しますか？\n\n" +
      "・自動記憶を削除します（手動で追加した記憶は残ります）\n" +
      "・達成済みの手動記憶は「有効」に戻ります\n" +
      "・削除前の内容は data/trash にバックアップされます"
    )) return;
  } else {
    if (!confirm("会話ログを再分析して記憶を追加します。\n既存の記憶は消しません（同じ内容は自動で除外）。")) return;
  }

  setRegenButtonsDisabled(true);
  const progressBox = $("ragMemProgress");
  const progressBar = $("ragMemProgressBar");
  const progressText = $("ragMemProgressText");

  progressBox.classList.remove("hidden");
  progressBar.style.width = "0%";
  progressText.textContent = "記憶の再構築を開始中...";

  try {
    const res = await checked("/api/rag/memories/regenerate", { session_id: sid, reset: isReset });
    ragMemRegenBefore = (res && res.before !== null && res.before !== undefined) ? Number(res.before) : null;
    startRegenStatusPolling();
  } catch (e) {
    alert("再生成の開始に失敗しました: " + e.message);
    setRegenButtonsDisabled(false);
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
  if (!ragSelected) { list.textContent = "コレクションを選択してください。"; return; }
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
      edit.type = "button";
      edit.textContent = "✏️";
      edit.title = "編集";
      edit.setAttribute("aria-label", "編集");
      edit.onclick = () => openRagEditor(entry);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "rag-entry-del";
      del.textContent = "🗑";
      del.title = "削除";
      del.setAttribute("aria-label", "削除");
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
let inputIsComposing = false;
let inputComposingTimer = null;
$("messageInput").addEventListener("compositionstart", () => {
  inputIsComposing = true;
  if (inputComposingTimer) { clearTimeout(inputComposingTimer); inputComposingTimer = null; }
});
$("messageInput").addEventListener("compositionend", () => {
  if (inputComposingTimer) clearTimeout(inputComposingTimer);
  inputComposingTimer = setTimeout(() => {
    inputIsComposing = false;
    inputComposingTimer = null;
  }, 20);
});
$("messageInput").addEventListener("keydown", (e) => {
  // PC は Enter 送信（Shift+Enter は改行）、スマホは Enter 改行。
  // 日本語IMEの変換確定 Enter では送らない（isComposing / keyCode 229 は変換中）
  if (e.key !== "Enter") return;
  if (e.isComposing || inputIsComposing || e.keyCode === 229) return;
  if (e.shiftKey) return;                 // Shift+Enter は改行
  // スマホ（幅768px以下かつモバイル端末）では Enter は改行（送信は送信ボタン）。PC は Enter 送信。
  if (isMobileInputDevice()) return;
  e.preventDefault();
  send();
});
$("messageInput").addEventListener("input", () => {
  autoResizeInput();
  syncActionControlsVisibility();   // 中央ボタンを「ツール」↔「送信」に切り替える
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
  inputIsComposing = false;
  if (inputComposingTimer) { clearTimeout(inputComposingTimer); inputComposingTimer = null; }
  setTypingInput(false);
  updateFabState();
});
// 添付ボタン下フリック送信 & タップでファイル選択
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
  const FLICK_THRESHOLD_Y = 35; // 下方向に35px以上でフリックとみなす
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

    const downDist = deltaY;
    const absDeltaX = Math.abs(deltaX);
    const isSwayed = absDeltaX >= MAX_SWAY_X;

    // 下方向へのリアルタイムスライド（transform: translateY）
    let visualY = 0;
    if (deltaY > 0) {
      visualY = Math.min(75, deltaY);
    }
    btn.style.transform = visualY !== 0 ? `translateY(${visualY}px)` : "";

    // 送信ブルー（#3b5bdb: rgb(59, 91, 219)）への滑らかな変化
    let progress = 0;
    if (downDist > 0 && !isSwayed) {
      progress = Math.min(1, downDist / FLICK_THRESHOLD_Y);
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
    if (downDist >= TAP_THRESHOLD && e.cancelable) {
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
    const downDist = deltaY;
    const absDeltaX = Math.abs(deltaX);
    const endDist = Math.hypot(deltaX, deltaY);
    if (endDist > touchMaxDist) {
      touchMaxDist = endDist;
    }

    const isFlick = (downDist >= FLICK_THRESHOLD_Y) && (absDeltaX < MAX_SWAY_X);
    const isTap = (touchMaxDist < TAP_THRESHOLD) && (endDist < TAP_THRESHOLD);

    if (isFlick) {
      // 分岐A: フリック成功
      suppressClickUntil = Date.now() + 600;
      if (e.cancelable) e.preventDefault();

      const messageInput = $("messageInput");
      const hasContent = Boolean((messageInput && messageInput.value.trim()) || attaching.length);
      const isSendDisabled = Boolean($("sendBtn")?.disabled);

      if (hasContent && !isSendDisabled) {
        // [あり] 下方へシュッと飛び去りながらフェードアウト
        clearTimers();
        isLaunching = true;
        btn.style.pointerEvents = "none";
        btn.style.transition = "transform 0.22s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.22s ease-out";
        btn.style.transform = "translateY(95px)";
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
$("ragClose").onclick = () => {
  $("ragModal").classList.add("hidden");
  // 編集中のまま閉じても body クラスが残らないようにする（次のモーダルを縮めない）
  if ($("ragMemEditor") && !$("ragMemEditor").classList.contains("hidden")) closeRagMemEditor();
  updateFabState();
};
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
if ($("ragTabMemoryTop")) $("ragTabMemoryTop").onclick = () => switchRagTab("memory");
if ($("ragTabKnowledge")) $("ragTabKnowledge").onclick = () => switchRagTab("custom");
if ($("ragMemAddBtn")) $("ragMemAddBtn").onclick = () => openRagMemEditor();
if ($("ragMemRegenBtn")) $("ragMemRegenBtn").onclick = () => regenerateRagMemories(false);
if ($("ragMemRegenReset")) $("ragMemRegenReset").onclick = () => regenerateRagMemories(true);
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
// タップが速すぎても青い反応が見えるように、押下時に一瞬だけ青いクラスを付ける。
// CSS の :active だけだと、指を離すのが速い端末では色がほぼ見えない。
function flashTapButton(btn) {
  if (!btn) return;
  btn.classList.remove("tap-flash");
  void btn.offsetWidth;                 // 連続タップでもクラスを付け直す
  btn.classList.add("tap-flash");
  clearTimeout(btn._tapFlashTimer);
  btn._tapFlashTimer = setTimeout(() => {
    btn.classList.remove("tap-flash");
    btn._tapFlashTimer = 0;
  }, 220);
}
function bindTurnTapFlash(id) {
  const btn = $(id);
  if (!btn) return;
  btn.addEventListener("pointerdown", (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    flashTapButton(btn);
  }, { passive: true });
  // キーボード（Enter/Space）でも反応が見えるように。pointerdown 直後の click では二重にしない
  btn.addEventListener("click", () => {
    if (!btn.classList.contains("tap-flash")) flashTapButton(btn);
  });
}
bindTurnTapFlash("mobileTurnPrev");
bindTurnTapFlash("mobileTurnNext");

// 「ツール / 閉じる」「選択肢」は動的に作られることもあるので、
// document への委譲で拾って同じ青フラッシュを付ける。
const TAP_FLASH_SELECTOR = "#mobileActionToggle, .choices-toggle-btn, .choices-switch, .choices-reroll-btn, .choice-item-btn, .btn-img-refine, .btn-prompt, .btn-more";
function tapFlashTarget(e) {
  if (!e.target || typeof e.target.closest !== "function") return null;
  const btn = e.target.closest(TAP_FLASH_SELECTOR);
  if (!btn || btn.disabled) return null;
  return btn;
}
document.addEventListener("pointerdown", (e) => {
  if (e.button !== undefined && e.button !== 0) return;
  const btn = tapFlashTarget(e);
  if (btn) flashTapButton(btn);
}, { passive: true });
document.addEventListener("click", (e) => {
  const btn = tapFlashTarget(e);
  if (btn && !btn.classList.contains("tap-flash")) flashTapButton(btn);
});
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
// 中央ボタン: 入力中は「送信」、それ以外は「ツール」
if ($("mobileActionToggle")) {
  const actionToggle = $("mobileActionToggle");
  // 表示は「送信」なのに押しても送信されないバグの対策。
  //  1) 押した瞬間の役割を覚えておく（ボタンはフッターの外なので、触れた時点で
  //     入力欄のフォーカスが外れ、actionToggleMode が「ツール」に戻ってしまう）
  //  2) 押下で preventDefault（フォーカスを手放さない＝キーボードが閉じて
  //     ボタンが指の下から動くのを防ぐ）
  //  3) 送信は pointerup で行う（click が別要素に落ちても送れる）
  let pressedMode = "";
  let pressedClearTimer = null;
  let pressStartX = 0;
  let pressStartY = 0;
  let pressMoved = false;
  let suppressClickUntil = 0;   // pointerup で送ったあとに来る click で二重送信しない
  const TAP_SLOP = 10;          // これ以上動いたらタップではなくスクロール扱い
  const clearPressed = () => {
    pressedMode = "";
    actionTogglePressed = false;
    if (pressedClearTimer) { clearTimeout(pressedClearTimer); pressedClearTimer = null; }
  };
  // click が来なかったときのために、押下状態を遅れて戻す（押しっぱなしでも送信のまま）
  const scheduleRelease = (delay) => {
    if (pressedClearTimer) clearTimeout(pressedClearTimer);
    pressedClearTimer = setTimeout(() => {
      pressedClearTimer = null;
      if (!actionTogglePressed) return;
      pressedMode = "";
      actionTogglePressed = false;
      syncActionControlsVisibility();
    }, delay);
  };
  actionToggle.addEventListener("pointerdown", (e) => {
    if (e && e.button !== undefined && e.button !== 0) return;   // 右クリック等は無視
    pressedMode = actionToggleMode;
    actionTogglePressed = true;
    pressStartX = e.clientX || 0;
    pressStartY = e.clientY || 0;
    pressMoved = false;
    // 「送信」のときは入力欄のフォーカスを保つ。フォーカスが外れるとキーボードが閉じ、
    // 画面下に固定したこのボタンが指の下から動いてしまい、タップが成立しなくなる（送信されない）。
    if (pressedMode === "send" && typeof e.preventDefault === "function") e.preventDefault();
    scheduleRelease(4000);
  }, { passive: false });
  actionToggle.addEventListener("pointermove", (e) => {
    if (!actionTogglePressed) return;
    const dx = (e.clientX || 0) - pressStartX;
    const dy = (e.clientY || 0) - pressStartY;
    if (Math.hypot(dx, dy) > TAP_SLOP) pressMoved = true;
  }, { passive: true });
  // 「送信」は pointerup で送る。ボタン自体が動いて click が別要素に落ちても送れるように。
  // （ツール/閉じるは今まで通り click で開閉する）
  actionToggle.addEventListener("pointerup", () => {
    if (!actionTogglePressed) { scheduleRelease(600); return; }
    const mode = pressedMode;
    const sendNow = mode === "send" && !pressMoved;
    pressedMode = "";
    // 送ったあと／スクロールしたあとに同じジェスチャの click が来ても何もしない
    if (sendNow || pressMoved) suppressClickUntil = Date.now() + 600;
    if (sendNow) {
      clearPressed();
      send();
      syncActionControlsVisibility();
      return;
    }
    scheduleRelease(600);
  }, { passive: true });
  actionToggle.addEventListener("pointercancel", () => {
    clearPressed();
    syncActionControlsVisibility();
  }, { passive: true });
  actionToggle.onclick = () => {
    if (Date.now() < suppressClickUntil) return;   // pointerup で送信済み
    const mode = pressedMode || actionToggleMode;   // 押した瞬間の役割で動く
    clearPressed();
    if (mode === "send") {
      // 中央の送信。キーボードは閉じない（続けて打てるように blur しない）
      send();
      syncActionControlsVisibility();
      return;
    }
    const ae = document.activeElement;                       // ★15: 入力の focus が残っていると押しても無反応になる
    const footer = document.querySelector("footer");
    if (ae && footer && footer.contains(ae) && typeof ae.blur === "function") ae.blur();
    const el = $("mobileReveal");
    // もう一度押したら閉じる（トグル）。開いたままだと「ツール」に戻れなくなるため
    if (el && !el.classList.contains("hidden")) hideReveal();
    else showReveal();
    syncActionControlsVisibility();
  };
}
if ($("revealRpToggle")) $("revealRpToggle").onclick = () => { hideReveal(); $("rpToggle").click(); syncActionControlsVisibility(); };
if ($("revealRag"))      $("revealRag").onclick      = () => { hideReveal(); openRag(); syncActionControlsVisibility(); };
if ($("revealState"))    $("revealState").onclick    = () => {
  hideReveal();
  const target = latestStateTarget();
  if (!target) { toast("まだキャラの状態がありません"); syncActionControlsVisibility(); return; }
  openState(target, (state && state.messages) || []);
  syncActionControlsVisibility();
};
if ($("revealSettings")) $("revealSettings").onclick = () => { hideReveal(); if ($("settingsToggle")) $("settingsToggle").click(); syncActionControlsVisibility(); };
if ($("revealModel"))    $("revealModel").onclick    = () => { hideReveal(); openAiToolModal(); syncActionControlsVisibility(); };
if ($("openAiToolBtn"))  $("openAiToolBtn").onclick  = () => openAiToolModal();
if ($("sideAiToolBtn"))  $("sideAiToolBtn").onclick  = () => {
  if (document.body && document.body.classList) document.body.classList.remove("side-open");
  updateFabState();
  openAiToolModal();
};
if ($("aiToolModalClose")) $("aiToolModalClose").onclick = closeAiToolModal;
if ($("aiToolModal")) {
  $("aiToolModal").addEventListener("click", (e) => {
    if (e.target === $("aiToolModal")) closeAiToolModal();
  });
}
if ($("googleAuthToggle")) $("googleAuthToggle").onclick = toggleGoogleAuth;
if ($("vertexAuthToggle")) $("vertexAuthToggle").onclick = toggleVertexAuth;

const bindThinkingPills = (containerId) => {
  const container = $(containerId);
  if (!container) return;
  container.addEventListener("click", (e) => {
    const btn = e.target.closest(".pill-btn");
    if (!btn || !btn.dataset.level) return;
    ThinkingUI.setThinking(btn.dataset.level);
  });
};
bindThinkingPills("googleThinkingPills");
bindThinkingPills("vertexThinkingPills");

if ($("choicesThinkingPills")) {
  $("choicesThinkingPills").addEventListener("click", (e) => {
    const btn = e.target.closest(".pill-btn");
    if (!btn || !btn.dataset.choicesLevel) return;
    ThinkingUI.setThinking(undefined, btn.dataset.choicesLevel);
  });
}

if ($("footThinkingBar")) {
  $("footThinkingBar").addEventListener("click", (e) => {
    const btn = e.target.closest(".segment-btn");
    if (!btn || !btn.dataset.level) return;
    ThinkingUI.setThinking(btn.dataset.level);
  });
}

if ($("footChoicesThinkingSelect")) {
  $("footChoicesThinkingSelect").addEventListener("change", (e) => {
    ThinkingUI.setThinking(undefined, e.target.value);
  });
}

if ($("googleMaxTokens")) {
  $("googleMaxTokens").addEventListener("input", () => {
    ThinkingUI.render(state?.setting);
  });
}
if ($("vertexMaxTokens")) {
  $("vertexMaxTokens").addEventListener("input", () => {
    ThinkingUI.render(state?.setting);
  });
}

if ($("googleModel")) {
  $("googleModel").addEventListener("change", () => {
    ThinkingUI.render(state?.setting);
  });
}
if ($("vertexModel")) {
  $("vertexModel").addEventListener("change", () => {
    ThinkingUI.render(state?.setting);
  });
}
if ($("vertexCustomModel")) {
  $("vertexCustomModel").addEventListener("input", () => {
    ThinkingUI.render(state?.setting);
  });
}
if ($("choicesIdeModel")) {
  $("choicesIdeModel").addEventListener("change", () => {
    ThinkingUI.render(state?.setting);
  });
}

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
    syncViewportVars();
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
  syncViewportVars();
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
const memoAsInitialEl = $("memoAsInitial");
if (memoAsInitialEl) {
  memoAsInitialEl.onclick = async () => {
    if (!state || formSid !== sid || state.id !== sid) {
      flash("設定の読み込みが追いついていません。少し待ってからもう一度保存してください");
      poll();
      return;
    }
    try {
      const r = await post("/api/setting", {
        setting: readForm(),
        memo_as_initial: true,
      });
      if (r.error) {
        flash("初期memoの保存に失敗: " + r.error);
        return;
      }
      if (r.ok) {
        flash("現在のmemoを初期値にも保存しました");
        formDirty = false;
        dirtyFields.clear();
        await poll();
      }
    } catch (e) {
      flash("初期memoの保存に失敗: " + e.message);
    }
  };
}
// 🔞を既定にするフラグは「切り替えたらすぐ効く」ように、その場で保存する
// （保存ボタンを押し忘れて「通常の候補が出る」と混乱しやすいため）
const eroticAutoEl = $("f_choices_erotic_auto");
if (eroticAutoEl) {
  eroticAutoEl.addEventListener("change", async () => {
    if (!state || state.id !== sid) return;
    if (eroticAutoEl.checked && state.setting && state.setting.suggest_choices === false) {
      eroticAutoEl.checked = false;
      flash("「返答候補（8択）を生成する」がオフなので、先にオンにしてください");
      return;
    }
    const next = JSON.parse(JSON.stringify(state.setting || {}));
    next.choices_erotic_auto = !!eroticAutoEl.checked;
    // 先読みは「選択肢担当の会話」で走るので、未設定なら効かないことを伝える
    const par = !!(next.choices_ide_session && next.choices_ide_session !== next.ide_session);
    try {
      await post("/api/setting", { setting: next });
      state.setting = next;
      dirtyFields.delete("f_choices_erotic_auto");
      if (next.choices_erotic_auto && !par) {
        flash("オンにしました。ただし「選択肢担当の会話」が未設定のため、いまは効きません");
      } else {
        flash(next.choices_erotic_auto
          ? "🔞の候補を最初から作る：オン（通常の候補は作りません）"
          : "🔞の候補を最初から作る：オフ");
      }
      await poll();
    } catch (e) {
      eroticAutoEl.checked = !eroticAutoEl.checked;
      flash("保存できませんでした: " + e.message);
    }
  });
}

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
  if (typeof PromptToolbar !== "undefined") {
    PromptToolbar.observe({
      selector: "#promptEdit, textarea.image-prompt-input, textarea[data-role='image-prompt']",
      getSessionContext: () => ({
        sessionId: typeof sid !== "undefined" ? sid : window.currentSession?.id,
        channelSetting: typeof state !== "undefined" && state?.setting ? state.setting : window.currentSession?.setting,
      }),
    });
  }
  bindViewportWatchers();
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
