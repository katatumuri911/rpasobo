(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const E = (v) => (typeof esc === "function" ? esc(v == null ? "" : String(v))
    : String(v == null ? "" : v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const say = (m) => { if (typeof toast === "function") toast(m); else console.log(m); };
  const escSel = (s) => (window.CSS && CSS.escape) ? CSS.escape(String(s))
    : String(s).replace(/["\\]/g, "\\$&");

  let wmeta = null;
  let draft = null;
  let jobId = "";
  let jobKind = "";
  let autoCreateOnce = false;  // 「おまかせで開始」の1回だけ自動作成（下書きの confirm は変えない）
  let jobTimer = null;
  let busy = false;
  let screen = "idea";
  let focusEl = null;
  let pendingTags = null;
  let tagTimer = null;

  // 戻るナビゲーション・入力退避用状態
  let navStack = [];         // 画面名の履歴（末尾が現在地）
  let returnTo = "tags";     // compose ジョブ完了後に戻す親画面
  let pending = "";          // 実行中のユーザー操作名（"back" / "next" / "create" / "close" ...）
  let tagSavePromise = null; // タグ保存の直列化
  let tagDirty = false;      // 前回保存後にタグ/自由入力が変わった
  let snap = {};             // 退避失敗時のみ使う画面スナップショット
  let renderSeq = 0;         // 非同期rendererの世代（古いawait後の書き込みを捨てる）
  let rendering = false;     // 現在画面の初回描画が終わるまで true
  let formSeq = 0;           // 入力の版（保存中の追記検出）
  let resumePromise = null;  // ジョブ再開の二重実行防止
  let touchedKeys = new Set(); // partner 画面のタッチ履歴
  let modelMode = "step";     // "step"=流れの中のAI選択 / "edit"=AIチップから設定変更
  let dialogOpen = false;     // 独自の確認ダイアログ表示中（背面のキー操作を止める）
  let dialogDone = null;      // 表示中のダイアログを閉じる関数（強制クローズ用）

  const root = () => $("#wizardRoot");
  const body = () => $("#wizardBody");
  const foot = () => $("#wizardFoot");

  async function apiCall(path, data) {
    if (typeof api !== "function") throw new Error("api() がありません");
    const r = await api(path, data);
    if (r && r.code === "rev") {
      // サーバーが最新の下書きを返すので、draft のみ追従させる（自動再描画はしない）
      if (r.draft) draft = r.draft;
      initPendingTags();
      if (typeof say === "function") say("他の操作で更新されました。最新の内容を読み込みました");
      const e = new Error(r.error || "下書きが更新されました");
      e.code = "rev";
      e.draft = r.draft;
      throw e;
    }
    return r;
  }

  function appMeta() {
    return (typeof meta !== "undefined" && meta) ? meta : {};
  }

  function currentSid() {
    return (typeof sid !== "undefined" && sid) ? sid : "";
  }

  // app.js の switchTo / refreshSessions を使う。ローカルで switchTo を定義しない。
  async function activateCreatedSession(sidValue) {
    if (!sidValue) return;
    try {
      const rf = window.refreshSessions || (typeof refreshSessions === "function" ? refreshSessions : null);
      if (typeof rf === "function") {
        await rf();
      }
    } catch (_) {}
    const sw = window.switchTo || (typeof switchTo === "function" ? switchTo : null);
    if (typeof sw === "function") {
      await sw(sidValue);
      return;
    }
    try { localStorage.setItem("sid", sidValue); } catch (_) {}
    location.reload();
  }

  function stopPoll() {
    if (jobTimer) clearTimeout(jobTimer);
    jobTimer = null;
  }

  function trapFocus() {
    focusEl = document.activeElement;
    const tEl = $("#wizardTitle");
    if (tEl && typeof tEl.focus === "function") {
      setTimeout(() => {
        try { tEl.focus({ preventScroll: true }); } catch (_) {}
      }, 0);
    }
  }

  function releaseFocus() {
    if (focusEl && focusEl.focus) try { focusEl.focus(); } catch (_) {}
    focusEl = null;
  }

  function titleFor(name) {
    return {
      model: "使用するAIを選ぶ",
      base: "チャンネルの引継ぎ",
      idea: "どんな話にしますか？",
      partner: "相手とあなた",
      tags: "関係性と雰囲気",
      questions: "いくつか質問します",
      preview: "できあがり",
      creating: "チャンネルを作成中",
      done: "作成しました",
    }[name] || "新しいチャンネル";
  }

  function progressFor(name) {
    const order = ["base", "idea", "model", "partner", "tags", "questions", "preview"];
    const i = order.indexOf(name);
    if (i < 0) return "";
    return `${i + 1} / ${order.length}`;
  }

  function canBack() {
    return !busy && !pending && !rendering && navStack.length > 1 && screen !== "done";
  }

  function updateFootDisabled() {
    const dis = !!(busy || pending);
    $$("#wizardFoot button").forEach(b => {
      b.disabled = dis;
    });
  }

  function updateChrome() {
    const tEl = $("#wizardTitle");
    if (tEl) tEl.textContent = titleFor(screen);
    const pEl = $("#wizardProgress");
    if (pEl) pEl.textContent = progressFor(screen);
    const bEl = $("#wizardBack");
    if (bEl) {
      const shouldHide = navStack.length <= 1 || screen === "done";
      bEl.classList.toggle("hidden", shouldHide);
      if (!shouldHide) {
        const enabled = canBack();
        bEl.disabled = !enabled;
        bEl.setAttribute("aria-disabled", enabled ? "false" : "true");
      }
    }
    const mc = (draft && draft.model && draft.model.model) ? draft.model : ((wmeta && wmeta.current) || {});
    const chip = $("#wizardModelChip");
    if (chip) {
      chip.textContent = mc.model ? `AI: ${mc.model.split("/").pop()}` : "AI: 未設定";
      chip.classList.toggle("warn", !mc.model);
      const locked = !!jobId || screen === "creating" || screen === "done" || busy || !!pending;
      chip.disabled = locked;
      chip.setAttribute("aria-disabled", locked ? "true" : "false");
    }
  }

  async function runAction(name, fn) {
    if (busy || pending) return;
    pending = name;
    updateChrome();
    updateFootDisabled();
    const bodyEl = $("#wizardBody");
    if (bodyEl) {
      bodyEl.inert = true;
      bodyEl.style.pointerEvents = "none";
    }
    try {
      return await fn();
    } finally {
      if (bodyEl) {
        bodyEl.inert = false;
        bodyEl.style.pointerEvents = "";
      }
      pending = "";
      updateChrome();
      updateFootDisabled();
    }
  }

  function errBox(msg, actions = "") {
    return `<div class="wizard-err"><p>${E(msg)}</p><div class="wizard-actions">${actions}</div></div>`;
  }

  function busyHtml(text) {
    return `<div class="wizard-busy"><div class="wizard-spinner"></div><p>${E(text || "処理中…")}</p><p class="muted">画面を閉じても処理は続きます。</p></div>`;
  }

  function chip(text, on, attrs = "") {
    return `<button type="button" class="wizard-chip ${on ? "on" : ""}" ${attrs}>${E(text)}</button>`;
  }

  function setBody(html) { const b = body(); if (b) b.innerHTML = html; }
  function setFoot(html) {
    const f = foot();
    if (f) f.innerHTML = html || "";
    updateFootDisabled();
  }

  function val(id) {
    const el = $(id);
    return el ? String(el.value || "").trim() : "";
  }
  function isChecked(id) {
    const el = $(id);
    return !!(el && el.checked);
  }
  function tagsText(c) {
    const t = c && c.tags;
    return Array.isArray(t) ? t.join(", ") : String(t || "");
  }

  // 下書きに何か入力・保存済みのものがあるか。
  // 空の下書き（開いて「保存せずに閉じる」しただけ）で再開確認を出さないための判定。
  function draftHasInput(d) {
    if (!d) return false;
    const slots = d.slots || {};
    if (Object.values(slots).some(s => s && s.source && s.source !== "unset")) return true;
    if ((d.questions || []).length) return true;
    if ((d.asked_slots || []).length) return true;
    if (d.compose) return true;
    if (String(d.partner_preset_id || "")) return true;
    if ((d.custom_ng || []).length) return true;
    if (d.step && d.step !== "base") return true;
    return false;
  }

  async function ensureDraft(baseSid) {
    const sidToUse = (baseSid !== undefined && baseSid !== null) ? baseSid : currentSid();
    const r = await apiCall("/api/wizard/start", { base_sid: sidToUse });
    if (!r || r.ok === false || r.error) throw new Error((r && r.error) || "下書きを作成できません");
    if (r.resumed && r.draft && !draftHasInput(r.draft)) {
      // 入力が何もない下書きは、確認せずにそのまま使い回す
      draft = r.draft;
      return;
    }
    if (r.resumed && r.draft) {
      const choice = await askChoice({
        title: "前回の下書き",
        message: "前回の続きから再開しますか？",
        buttons: [
          { value: "resume", label: "再開", primary: true },
          { value: "new", label: "新規作成" },
        ],
      });
      if (choice === "new") {
        const n = await apiCall("/api/wizard/start", { base_sid: sidToUse, force_new: true });
        if (!n || n.error) throw new Error(n.error || "下書きを作成できません");
        draft = n.draft;
      } else {
        draft = r.draft;
      }
    } else {
      draft = r.draft;
    }
  }

  function entryScreen() {
    if (!draft) return "idea";
    if (draft.status === "created") return "done";
    if (draft.status === "composing") return "creating";
    const step = draft.step || "base";
    if (step === "base") return "base";
    if (step === "preview") return draft.compose ? "preview" : "tags";
    if (step === "tags") return "tags";
    if (step === "partner") return "partner";
    if (step === "questions") return "questions";
    if (step === "creating") return "creating";
    return "idea";
  }

  function render(name) {
    stopPoll();
    const seq = ++renderSeq;
    rendering = true;
    const prevScreen = screen;
    screen = name;
    formSeq = 0;
    updateChrome();
    persistStep(name);
    if (prevScreen !== name) {
      const tEl = $("#wizardTitle");
      if (tEl && typeof tEl.focus === "function") {
        try { tEl.focus({ preventScroll: true }); } catch (_) {}
      }
    }
    const finish = () => {
      if (seq === renderSeq) {
        rendering = false;
        updateChrome();
        updateFootDisabled();
      }
    };
    const fn = ({
      base: renderBase, model: renderModel, idea: renderIdea, partner: renderPartner,
      tags: renderTags, questions: renderQuestions, preview: renderPreview,
      creating: renderCreating, done: renderDone,
    })[name];
    const p = fn ? fn() : null;
    if (p && typeof p.then === "function") p.then(finish, finish);
    else finish();
  }

  function navigate(name) {
    if (navStack[navStack.length - 1] !== name) {
      const i = navStack.lastIndexOf(name);
      if (i >= 0) navStack = navStack.slice(0, i + 1);
      else navStack.push(name);
    }
    render(name);
  }

  function backRender() {
    render(navStack[navStack.length - 1]);
  }

  function settleTo(name, parent) {
    const i = navStack.lastIndexOf(parent);
    navStack = i >= 0 ? navStack.slice(0, i + 1) : [parent];
    navigate(name);
  }

  function resetTo(name) {
    navStack = [name];
    render(name);
  }

  const ENTRY_STACK = {
    base: ["base"], model: ["model"], idea: ["idea"],
    partner: ["idea", "partner"], tags: ["partner", "tags"],
    questions: ["tags", "questions"], preview: ["tags", "preview"],
    creating: ["tags", "creating"], done: ["done"],
  };

  function enterScreen(name) {
    navStack = (ENTRY_STACK[name] || [name]).slice();
    render(name);
  }

  function chromeFor(name) {
    renderSeq += 1;
    rendering = false;
    if (navStack[navStack.length - 1] !== name) {
      const i = navStack.lastIndexOf(name);
      if (i >= 0) navStack = navStack.slice(0, i + 1); else navStack.push(name);
    }
    screen = name;
    updateChrome();
    persistStep(name);
  }

  function closeModel() {
    const parent = navStack[navStack.length - 2];
    if (!parent || parent === "base") return resetTo("idea");
    return navigate(parent);
  }

  // 流れの中のAI選択（ベース→話→AI→相手…）として開く
  function openModelStep() {
    modelMode = "step";
    navigate("model");
  }

  // AIチップからの設定変更として開く（保存したら元の画面に戻る）
  function openModelEdit() {
    modelMode = "edit";
    navigate("model");
  }

  async function open(baseSid) {
    if (busy) return;
    clearDialog();
    autoCreateOnce = false;
    jobId = "";
    jobKind = "";
    pendingTags = null;
    if (tagTimer) { clearTimeout(tagTimer); tagTimer = null; }
    stopPoll();
    busy = true;
    try {
      wmeta = await apiCall("/api/wizard/meta");
      if (!wmeta || wmeta.ok === false || wmeta.error) throw new Error((wmeta && wmeta.error) || "ウィザードを開けません");
      if (!wmeta.enabled) throw new Error("ウィザードは無効です");
      const r = root();
      if (r) {
        r.classList.remove("hidden");
        r.setAttribute("aria-hidden", "false");
      }
      document.body.classList.add("wizard-open");
      if (typeof updateFabState === "function") updateFabState();
      trapFocus();
      try {
        await ensureDraft(baseSid);
      } catch (e) {
        stopPoll();
        clearDialog();
        if (r) {
          r.classList.add("hidden");
          r.setAttribute("aria-hidden", "true");
        }
        document.body.classList.remove("wizard-open");
        releaseFocus();
        if (typeof updateFabState === "function") updateFabState();
        throw e;
      }
      busy = false;
      // ベース → 話（idea）→ AI（model）→ 相手 … の順で進む。
      // モデル未設定でも先に話を入力してもらい、idea画面の「AIと作る」からmodelへ進む。
      const start = entryScreen();
      enterScreen(start);
      if (start !== "base" && draft.last_job && !jobId) resumeJob(draft.last_job);
    } finally {
      busy = false;
    }
  }

  // ------------------------------------------------------------ confirm
  // ウィザード内で使う確認ダイアログ。ボタン構成を呼び出し側で指定できる。
  // 戻り値は選んだボタンの value（Esc / 背景クリックは "cancel"）。
  function askChoice(opts) {
    const o = opts || {};
    const btns = Array.isArray(o.buttons) ? o.buttons : [];
    return new Promise((resolve) => {
      const r = root();
      if (!r || !btns.length) { resolve("cancel"); return; }
      if (typeof document.createElement !== "function" || typeof r.appendChild !== "function") {
        // DOM を組み立てられない環境（自動テストなど）では confirm で代替する
        const ok = typeof window.confirm === "function" ? window.confirm(o.message || o.title || "") : true;
        resolve(ok ? ((btns[0] && btns[0].value) || "cancel") : "cancel");
        return;
      }
      const wrap = document.createElement("div");
      wrap.className = "wizard-confirm";
      wrap.innerHTML = `
        <div class="wizard-confirm-backdrop"></div>
        <div class="wizard-confirm-box" role="dialog" aria-modal="true" aria-labelledby="wizConfirmTitle" tabindex="-1">
          <h3 id="wizConfirmTitle">${E(o.title || "確認")}</h3>
          <p class="wizard-confirm-msg">${E(o.message || "")}</p>
          <div class="wizard-confirm-btns">
            ${btns.map(b => `<button type="button" class="wizard-btn${b.primary ? " primary" : " ghost"}${b.danger ? " danger" : ""}" data-choice="${E(b.value)}">${E(b.label)}</button>`).join("")}
          </div>
        </div>`;
      const box = $(".wizard-confirm-box", wrap);
      const prevFocus = document.activeElement;
      const modalEl = $(".wizard-modal", r);
      let settled = false;
      dialogOpen = true;
      // 背面のウィザードは Tab / クリックの対象から外す
      if (modalEl) modalEl.inert = true;
      const done = (val) => {
        if (settled) return;
        settled = true;
        dialogOpen = false;
        dialogDone = null;
        wrap.removeEventListener("keydown", onKey, true);
        if (wrap.isConnected) wrap.remove();
        if (modalEl) modalEl.inert = false;
        try { prevFocus && prevFocus.focus && prevFocus.focus({ preventScroll: true }); } catch (_) {}
        resolve(val);
      };
      dialogDone = done;
      const onKey = (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          done("cancel");
        } else if (e.key === "Tab") {
          const f = Array.from(box.querySelectorAll("button:not([disabled])"));
          if (!f.length) return;
          const first = f[0], last = f[f.length - 1];
          const cur = document.activeElement;
          if (e.shiftKey && (cur === first || cur === box)) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && cur === last) { e.preventDefault(); first.focus(); }
        }
      };
      wrap.addEventListener("click", (e) => {
        const t = e.target && typeof e.target.closest === "function" ? e.target : null;
        const b = t && t.closest("[data-choice]");
        if (b) { e.preventDefault(); done(b.dataset.choice); return; }
        if (t && t.closest(".wizard-confirm-backdrop")) done("cancel");
      });
      wrap.addEventListener("keydown", onKey, true);
      r.appendChild(wrap);
      const primary = box.querySelector(".wizard-btn.primary") || box.querySelector("button");
      if (primary) setTimeout(() => {
        try { primary.focus({ preventScroll: true }); } catch (_) {}
      }, 0);
    });
  }

  const CLOSE_BUTTONS = [
    { value: "save", label: "保存", primary: true },
    { value: "discard", label: "保存せずに閉じる", danger: true },
    { value: "cancel", label: "キャンセル" },
  ];

  // 表示中の確認ダイアログを強制的に閉じる（キャンセル扱いで await を解く）
  function clearDialog() {
    const d = dialogDone;
    dialogDone = null;
    if (d) d("cancel");
    $$(".wizard-confirm").forEach(el => el.remove());
    dialogOpen = false;
    const modalEl = $(".wizard-modal", root());
    if (modalEl) modalEl.inert = false;
  }

  async function close(force) {
    if (force) return closeNow();
    if (busy || pending || rendering || dialogOpen) return;
    await runAction("close", async () => {
      const running = !!jobId;
      const msg = running
        ? "生成は続きます。チャンネルの作成は次に開いたときに行ってください。"
        : (screen === "questions" || screen === "model")
          ? "この画面の入力は下書きに残りません。"
          : "入力中の内容を下書きに保存して閉じます。";
      const choice = await askChoice({ title: "チャンネル作成を閉じる", message: msg, buttons: CLOSE_BUTTONS });
      if (choice !== "save" && choice !== "discard") return;
      if (choice === "discard") { closeNow(); return; }
      for (;;) {
        let r = await flushStable(screen);
        if (r.ok) delete snap[screen];
        for (const name of ["idea", "partner", "tags", "preview"]) {
          if (name === screen || !snap[name]) continue;
          const rr = await flushScreen(name, snap[name]);
          if (rr.ok) delete snap[name]; else r = rr;
        }
        if (r.ok) { closeNow(); return; }
        const again = await askChoice({
          title: "保存できませんでした",
          message: `${r.error || "通信を確認してください"}。入力中の内容はまだ保存されていません。`,
          buttons: CLOSE_BUTTONS,
        });
        if (again === "cancel") return;
        if (again === "discard") { closeNow(); return; }
      }
    });
  }

  function closeNow() {
    autoCreateOnce = false;
    stopPoll();
    clearDialog();
    if (tagTimer) { clearTimeout(tagTimer); tagTimer = null; }
    if (draft && draft.status !== "created" && !jobId && (draft.step || "") !== screen) {
      apiCall("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step: screen }).catch(() => {});
    }
    const r = root();
    if (r) {
      r.classList.add("hidden");
      r.setAttribute("aria-hidden", "true");
    }
    document.body.classList.remove("wizard-open");
    releaseFocus();
    if (typeof updateFabState === "function") updateFabState();
    navStack = [];
    snap = {};
    pendingTags = null;
    tagDirty = false;
    rendering = false;
    jobId = "";
    jobKind = "";
  }

  async function goBack() {
    if (!canBack()) return;
    await runAction("back", async () => {
      if (screen === "model") autoCreateOnce = false; // AI選択から戻ったら「おまかせ即作成」は解除
      const running = !!jobId;
      if (running && (screen === "creating" || screen === "questions")) {
        if (!window.confirm("生成を中止して戻りますか？")) return;
        const c = await cancelJob();
        if (!c.ok) { say(c.error || "中止できません。通信を確認してください"); return; }
        if (c.draft && c.draft.status === "created") return enterScreen("done");
        if (c.draft && c.draft.compose && !c.draft.compose_stale) return settleTo("preview", returnTo);
        return settleTo(returnTo, returnTo);
      }
      if (screen === "questions"
          && (val("#wizFree") || (foot() && foot().dataset.picked))
          && !window.confirm("入力中の回答は破棄されます。戻りますか？")) return;
      const r = await flushStable(screen);
      if (r.ok) {
        delete snap[screen];
      } else if (r.error === "入力が確定しません。もう一度お試しください") {
        say(r.error);
        return;
      } else {
        if (r.error) say(r.error);
      }
      navStack.pop();
      backRender();
    });
  }

  async function saveStep(step, extra = {}) {
    const r = await apiCall("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step, ...extra });
    if (!r || r.error) throw new Error(r.error || "保存できません");
    draft = r.draft;
  }

  // 画面移動を draft.step に残す（リロード復元用）。rev を変えず、失敗しても画面は進める。
  function persistStep(name) {
    if (!draft || typeof api !== "function") return;
    if (!["idea", "partner", "tags", "questions", "preview"].includes(name)) return;
    if (jobId || draft.status === "composing" || draft.status === "created") return;
    if ((draft.step || "") === name) return;
    draft.step = name;
    api("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step: name }).catch(() => {});
  }

  async function saveSlots(slots, step) {
    const r = await apiCall("/api/wizard/slots", { draft_id: draft.id, rev: draft.rev, slots, step });
    if (!r || r.error) throw new Error(r.error || "保存できません");
    draft = r.draft;
  }

  function initPendingTags() {
    const selTags = (draft && draft.selected_tags) || [];
    const selNg = (draft && draft.selected_ng) || [];
    const customVal = Array.isArray(draft && draft.custom_ng)
      ? draft.custom_ng.join("、")
      : String((draft && draft.custom_ng) || "");
    pendingTags = {
      tags: selTags.slice(),
      ng: selNg.slice(),
      custom: customVal,
    };
  }

  function syncTagChips() {
    if (!pendingTags) return;
    $$("#wizardBody [data-tag]").forEach(el => el.classList.toggle("on", pendingTags.tags.includes(el.dataset.tag)));
    $$("#wizardBody [data-ng]").forEach(el => el.classList.toggle("on", pendingTags.ng.includes(el.dataset.ng)));
  }

  function markTagsDirty() { tagDirty = true; }

  function scheduleTagSave() {
    if (tagTimer) clearTimeout(tagTimer);
    tagDirty = true;
    tagTimer = setTimeout(() => {
      tagTimer = null;
      saveTagsToServer().catch(e => say(e.message));
    }, 500);
  }

  function saveTagsToServer() {
    if (!pendingTags) return Promise.resolve();
    if (tagSavePromise) return tagSavePromise;
    const sent = { tags: pendingTags.tags.slice(), ng: pendingTags.ng.slice(), custom: pendingTags.custom };
    tagSavePromise = (async () => {
      const r = await apiCall("/api/wizard/tags", {
        draft_id: draft.id, rev: draft.rev,
        selected_tags: sent.tags, selected_ng: sent.ng, custom_ng: sent.custom,
      });
      if (!r || r.error) throw new Error(r.error || "タグを保存できません");
      draft = r.draft;
      delete snap.tags;
      if (!pendingTags) return;
      const curSelected = (draft && draft.selected_tags) || [];
      const dropped = sent.tags.filter(t => !curSelected.includes(t));
      pendingTags.tags = pendingTags.tags.filter(t => !dropped.includes(t));
      syncTagChips();
      tagDirty = pendingTags.tags.join() !== sent.tags.join()
              || pendingTags.ng.join()   !== sent.ng.join()
              || pendingTags.custom      !== sent.custom;
    })().finally(() => { tagSavePromise = null; });
    return tagSavePromise;
  }

  async function drainTagSaves() {
    if (!pendingTags) return true;
    for (let i = 0; i < 3; i++) {
      if (tagSavePromise) await tagSavePromise;
      if (!tagDirty) return true;
      await saveTagsToServer();
    }
    return !tagDirty;
  }

  // ---- idea 退避 ----
  function readIdeaForm() {
    return { idea: val("#wizIdea"), confirm: isChecked("#wizConfirm") };
  }

  async function flushIdea(v) {
    v = v || readIdeaForm();
    const cur = String(((draft && draft.slots || {}).rough_idea || {}).value || "");
    let changed = false;
    if (v.idea !== cur) {
      await saveSlots({ rough_idea: v.idea ? { value: v.idea, source: "user" }
                                         : { value: "", source: "auto" } }, "idea");
      changed = true;
    }
    if (v.confirm !== !!(draft && draft.confirm)) {
      const r = await apiCall("/api/wizard/step", {
        draft_id: draft.id, rev: draft.rev, step: "idea", confirm: v.confirm,
      });
      if (r && r.draft) draft = r.draft;
      changed = true;
    }
    return { ok: true, changed };
  }

  // ---- partner 退避 ----
  function readPartnerForm() {
    const segBox = $("#wizPartnerType");
    const slots = (draft && draft.slots) || {};
    const s = (k) => String((slots[k] && slots[k].value) || "");
    const values = {
      partner_type: segBox
        ? (($(".wizard-seg-btn.on", segBox) || {}).dataset?.pt || "")
        : s("partner_type"),
      work: segBox ? val("#wizWork") : s("work"),
      partner_name: segBox ? val("#wizPartnerName") : s("partner_name"),
      player_type: segBox ? (val("#wizPlayerType") || "original") : (s("player_type") || "original"),
      player_work: segBox ? val("#wizPlayerWork") : s("player_work"),
      player_name: segBox ? val("#wizPlayerName") : s("player_name"),
      player_profile: segBox ? val("#wizPlayerProfile") : s("player_profile"),
      start_point: segBox ? val("#wizStart") : s("start_point"),
      canon_fidelity: segBox ? (val("#wizCanon") || "mixed") : (s("canon_fidelity") || "mixed"),
      goal: segBox ? val("#wizGoal") : s("goal"),
      ending: segBox ? val("#wizEnding") : s("ending"),
      project: segBox ? val("#wizProject") : (s("project") || (draft && draft.defaults && draft.defaults.project) || ""),
    };
    const preset = segBox ? val("#wizPreset") : String((draft && draft.partner_preset_id) || "");
    return { values, touched: new Set(touchedKeys), preset };
  }

  const PARTNER_INVALIDATES = {
    partner_type: ["work", "partner_name", "start_point", "canon_fidelity"],
    work: ["partner_name", "start_point", "canon_fidelity"],
    player_type: ["player_work", "player_name", "player_profile"],
    player_work: ["player_name", "player_profile"],
  };

  function buildPartnerPatch(v) {
    const patch = {};
    const prevOf = (k) => k === "project"
      ? String(((draft && draft.slots || {}).project || {}).value || (draft && draft.defaults || {}).project || "")
      : String(((draft && draft.slots || {})[k] || {}).value || "");
    const put = (k, next) => {
      if (next === prevOf(k)) return;
      patch[k] = next ? { value: next, source: "user" } : { value: "", source: "auto" };
    };
    for (const k of ["work", "partner_name", "player_work", "player_name",
                     "player_profile", "start_point", "goal", "project", "ending"]) {
      put(k, v.values[k]);
    }
    for (const k of ["player_type", "canon_fidelity"]) {
      if (v.touched && v.touched.has(k)) put(k, v.values[k]);
    }
    if (v.touched && v.touched.has("partner_type") && v.values.partner_type
        && v.values.partner_type !== "auto") {
      put("partner_type", v.values.partner_type);
    }
    const preserve = new Set();
    for (const k of Object.keys(patch)) {
      for (const dep of (PARTNER_INVALIDATES[k] || [])) {
        if (!(dep in patch)) preserve.add(dep);
      }
    }
    for (const k of preserve) {
      const next = v.values[k];
      patch[k] = next ? { value: next, source: "user" } : { value: "", source: "auto" };
    }
    return patch;
  }

  async function flushPartner(snapValues) {
    const v = snapValues || readPartnerForm();
    const patch = buildPartnerPatch(v);
    let changed = false;
    if (Object.keys(patch).length) {
      await saveSlots(patch, "partner");
      changed = true;
    }
    const curPreset = String((draft && draft.partner_preset_id) || "");
    if (v.preset !== curPreset) {
      const r = await apiCall("/api/wizard/answer", {
        draft_id: draft.id, rev: draft.rev, question_id: "fixed_preset",
        option_ids: v.preset ? [v.preset] : [], step: "partner",
      });
      if (r && r.draft) draft = r.draft;
      changed = true;
    }
    return { ok: true, changed };
  }

  // ---- tags 退避 ----
  async function flushTags(snapValues) {
    if (!snapValues && !tagDirty && !tagTimer) return { ok: true, changed: false };
    if (tagTimer) { clearTimeout(tagTimer); tagTimer = null; }
    if (snapValues) {
      pendingTags = { tags: snapValues.tags.slice(), ng: snapValues.ng.slice(), custom: snapValues.custom };
      tagDirty = true;
    } else if (pendingTags) {
      const el = $("#wizCustomNg");
      if (el) {
        if (pendingTags.custom !== el.value) {
          pendingTags.custom = el.value;
          tagDirty = true;
        }
      }
    }
    if (!pendingTags) return { ok: false, changed: false, error: "タグが見つかりません" };
    await drainTagSaves();
    return { ok: true, changed: true };
  }

  // ---- preview 退避 ----
  function buildPreviewPatch(v) {
    const c = (draft && draft.compose && draft.compose.setting) || {};
    const base = (k) => k === "user_name" ? (c.user_name || (draft && draft.defaults || {}).user_name || "あなた")
                      : k === "project"   ? (c.project   || (draft && draft.defaults || {}).project   || "")
                      : (c[k] || "");
    const patch = {};
    for (const k of ["title", "summary", "scenario", "first_message",
                     "instructions", "user_name", "project"]) {
      if (v[k] !== base(k)) patch[k] = v[k];
    }
    const ch = v.character || {}, cch = c.character || {}, dch = {};
    for (const k of ["name", "age", "profile", "speech"]) {
      if (ch[k] !== (cch[k] || "")) dch[k] = ch[k];
    }
    if (Object.keys(dch).length) patch.character = dch;
    return patch;
  }

  async function flushPreview(v) {
    const patch = v || buildPreviewPatch(collectOverrides());
    if (!Object.keys(patch).length) return { ok: true, changed: false };
    const r = await apiCall("/api/wizard/preview", {
      draft_id: draft.id, rev: draft.rev, setting: patch,
    });
    if (!r || r.error) throw new Error(r.error || "プレビューを保存できません");
    draft = r.draft;
    return { ok: true, changed: true };
  }

  function readScreenValues(name) {
    if (name === "idea")    return readIdeaForm();
    if (name === "partner") return readPartnerForm();
    if (name === "tags")    return pendingTags
      ? { tags: pendingTags.tags.slice(), ng: pendingTags.ng.slice(), custom: pendingTags.custom }
      : null;
    if (name === "preview") return buildPreviewPatch(collectOverrides());
    return null;
  }

  async function flushOne(name, values) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        if (name === "idea")    return await flushIdea(values);
        if (name === "partner") return await flushPartner(values);
        if (name === "tags")    return await flushTags(values);
        if (name === "preview") return await flushPreview(values);
        return { ok: true, changed: false };
      } catch (e) {
        if (e.code === "rev" && attempt === 0) continue;
        throw e;
      }
    }
    return { ok: true, changed: false };
  }

  async function flushScreen(name, values) {
    const seq = formSeq;
    try {
      const r = await flushOne(name, values);
      if (!values && seq !== formSeq) {
        snap[name] = readScreenValues(name);
        return { ok: false, changed: true, error: "入力が更新されました" };
      }
      return r;
    } catch (e) {
      snap[name] = values || readScreenValues(name);
      return { ok: false, changed: true, error: e.message };
    }
  }

  async function flushStable(name, tries = 2) {
    for (let i = 0; i < tries; i++) {
      const r = await flushScreen(name);
      if (r.ok) return r;
      if (r.error !== "入力が更新されました") return r;
    }
    return { ok: false, changed: true, error: "入力が確定しません。もう一度お試しください" };
  }

  // ------------------------------------------------------------ base
  async function renderBase() {
    const locked = !!(Object.values(draft.slots || {}).some(s => s && s.source && s.source !== "unset") ||
                      (draft.questions || []).length || draft.compose);
    const hasBase = !!draft.base_sid;
    const title = draft.base_title || "現在のチャンネル";
    if (locked) {
      setBody(`
        <div class="wizard-card">
          <p class="wizard-lead">設定の入力を開始した後は、ベース設定を変更できません。</p>
          <p class="wizard-note">現在の設定: ${draft.inherit_base ? `「${E(title)}」の設定を引き継ぐ` : "初期設定で新しく作る"}</p>
        </div>`);
      setFoot(`<button type="button" class="wizard-btn primary" data-act="base-continue">この設定で続ける</button>`);
      return;
    }
    const current = draft.inherit_base ? "このチャンネルの設定を引き継ぐ" : "初期設定で新しく作る";
    setBody(`
      <div class="wizard-card">
        ${hasBase ? `<p class="wizard-lead">直前まで開いていた「${E(title)}」のチャンネル設定を引き継いで作りますか？</p>`
                  : `<p class="wizard-lead">ベースにできるチャンネルがありません。初期設定から新しく作成します。</p>`}
        <table class="wizard-inherit">
          <tr><th>引き継ぐ</th><td>画像設定 / 参照ファイル / プロジェクト / ユーザー名 / 本文AI設定</td></tr>
          <tr><th>新規作成</th><td>ストーリー / 進捗（メモ・ターン・日数） / IDE会話</td></tr>
        </table>
        <p class="wizard-note">※ 引き継ぐのは元のチャンネルで保存済みの設定です。未保存の編集内容は反映されません。</p>
        ${hasBase ? `<p class="wizard-note">「初期設定で新しく作る」を選ぶと、ユーザー名は「あなた」、プロジェクトは空、画像設定・参照ファイルは標準の初期状態になります。（現在の選択: ${current}）</p>` : ""}
        <div id="wizBaseMsg" class="wizard-msg"></div>
      </div>`);
    setFoot(hasBase
      ? `<button type="button" class="wizard-btn primary" data-act="base-inherit">このチャンネルの設定を引き継ぐ</button>
         <button type="button" class="wizard-btn ghost" data-act="base-empty">初期設定で新しく作る</button>`
      : `<button type="button" class="wizard-btn primary" data-act="base-empty">初期設定で新しく作る</button>`);
  }

  async function saveBase(inherit) {
    if (inherit && !draft.base_sid) { say("引き継ぎ対象のチャンネルがありません"); return; }
    const r = await apiCall("/api/wizard/base", {
      draft_id: draft.id, rev: draft.rev,
      base_sid: draft.base_sid, inherit,
    });
    if (!r || r.error) { say(r.error || "保存できません"); return; }
    draft = r.draft;
    navigate("idea");
  }

  // ------------------------------------------------------------ model
  async function renderModel() {
    const mySeq = renderSeq;
    const cur = (draft && draft.model && draft.model.model) ? draft.model : ((wmeta && wmeta.current) || {});
    const provider = cur.provider || "google";
    const thinking = cur.thinking_level || "high";
    setBody(`
      <div class="wizard-card">
        <h3>チャンネル作成に使うAI</h3>
        <p class="muted">このモデルはチャンネル作成のみで使用します。</p>
        <label class="wizard-label">プロバイダー
          <select id="wizProvider" class="wizard-input">
            <option value="google" ${provider === "google" ? "selected" : ""}>Google AI Studio</option>
            <option value="vertex" ${provider === "vertex" ? "selected" : ""}>Vertex AI</option>
            <option value="antigravity" ${provider === "antigravity" ? "selected" : ""}>Antigravity</option>
          </select>
        </label>
        <label class="wizard-label">モデル
          <select id="wizModel" class="wizard-input"><option value="">読み込み中…</option></select>
        </label>
        <label class="wizard-label" id="wizThinkWrap">思考の深さ
          <select id="wizThink" class="wizard-input">
            <option value="high" ${thinking === "high" ? "selected" : ""}>high（推奨）</option>
            <option value="xhigh" ${thinking === "xhigh" ? "selected" : ""}>xhigh（Gemini 4 Hydrogen 系のみ）</option>
            <option value="medium" ${thinking === "medium" ? "selected" : ""}>medium</option>
            <option value="low" ${thinking === "low" ? "selected" : ""}>low</option>
            <option value="minimal" ${thinking === "minimal" ? "selected" : ""}>minimal</option>
          </select>
        </label>
        <p class="wizard-note">※ できるだけ賢いモデルを推奨。(思考レベルが低いとチャンネルが壊れる可能性があります)</p>
        <div id="wizModelMsg" class="wizard-msg"></div>
      </div>`);

    const load = async () => {
      const p = val("#wizProvider") || "google";
      const r = await apiCall(`/api/wizard/models?provider=${encodeURIComponent(p)}`);
      if (mySeq !== renderSeq) return;
      const sel = $("#wizModel");
      if (!sel) return;
      const rows = (r && r.models) || [];
      sel.innerHTML = rows.length
        ? rows.map(m => `<option value="${E(m.id)}" ${m.id === cur.model ? "selected" : ""}>${E(m.label)}${m.tier === "high" ? " ★" : ""}</option>`).join("")
        : `<option value="">（モデルが見つかりません）</option>`;
      const tw = $("#wizThinkWrap");
      if (tw) tw.classList.toggle("hidden", p === "antigravity");
      const msg = $("#wizModelMsg");
      if (msg) msg.textContent = (r && r.error) ? r.error : "";
    };
    const provSel = $("#wizProvider");
    if (provSel) provSel.onchange = load;
    await load();
    if (mySeq !== renderSeq) return;

    setFoot(`
      <button type="button" class="wizard-btn" data-act="model-test">接続テスト</button>
      <button type="button" class="wizard-btn primary" data-act="model-save">${modelMode === "edit" ? "保存" : "保存して次へ"}</button>
      <button type="button" class="wizard-btn ghost" data-act="model-skip">AIを使わずに作る</button>
    `);
  }

  async function saveModel(skipAi) {
    const provider = val("#wizProvider") || "google";
    const model = val("#wizModel");
    const thinking = val("#wizThink") || "high";
    if (!skipAi && provider !== "antigravity" && !model) {
      say("モデルを選んでください"); return;
    }
    if (!skipAi) {
      const a = await apiCall("/api/app-settings", {
        wizard_enabled: true,
        wizard_provider: provider,
        wizard_model: model,
        wizard_thinking_level: thinking,
        wizard_confirm: draft.confirm,
      });
      if (a && a.error) throw new Error(a.error);
    }
    const r = await apiCall("/api/wizard/model", {
      draft_id: draft.id, rev: draft.rev,
      provider: skipAi ? "none" : provider,
      model: skipAi ? "" : model,
      thinking_level: skipAi ? "" : thinking,
      ai_enabled: !skipAi,
    });
    if (!r || r.error) throw new Error(r.error || "保存できません");
    draft = r.draft;
    if (wmeta) {
      wmeta.current = skipAi
        ? { provider: "none", model: "", thinking_level: "" }
        : { provider, model, thinking_level: thinking };
      wmeta.configured = !skipAi && (provider === "antigravity" || !!model);
    }
    if (modelMode === "step") {
      // 流れの中のステップ：話（idea）→ AI → 相手（partner）。
      // 「おまかせで作る」から来ていたら、ここでそのまま作成まで進める。
      if (autoCreateOnce) {
        autoCreateOnce = false;
        return startCreateChain(false);
      }
      return navigate("partner");
    }
    closeModel();
  }

  async function testModel() {
    const provider = val("#wizProvider") || "google";
    const model = val("#wizModel");
    const msg = $("#wizModelMsg");
    if (msg) msg.textContent = "テスト中…";
    try {
      const r = await apiCall("/api/wizard/test", { provider, model, thinking_level: "minimal" });
      if (msg) msg.textContent = r.error ? r.error : (r.note || "接続OK");
    } catch (e) {
      if (msg) msg.textContent = e.message;
    }
  }

  // ------------------------------------------------------------ idea
  function renderIdea() {
    const snapIdea = snap.idea;
    const idea = snapIdea ? snapIdea.idea : ((draft && draft.slots && draft.slots.rough_idea && draft.slots.rough_idea.value) || "");
    const confirm = snapIdea ? snapIdea.confirm : (draft && draft.confirm);
    const examples = (wmeta && wmeta.tags && wmeta.tags.examples) || [];
    setBody(`
      <div class="wizard-card">
        <h3>やりたいことを一言で</h3>
        <textarea id="wizIdea" class="wizard-textarea" rows="4" maxlength="300"
          placeholder="例: 放課後の教室で、幼馴染と二人きり。">${E(idea)}</textarea>
        <div class="wizard-examples">${examples.map(x => `<button type="button" class="wizard-example" data-example="${E(x)}">${E(x)}</button>`).join("")}</div>
        <label class="wizard-check"><input type="checkbox" id="wizConfirm" ${confirm ? "checked" : ""}> 作成前にプレビューを確認する</label>
        <p class="wizard-note">空でも作れます。わからないことはAIが「おまかせ」で補います。</p>
      </div>`);
    setFoot(`
      <button type="button" class="wizard-btn primary" data-act="idea-next">AIと作る</button>
      <button type="button" class="wizard-btn" data-act="idea-auto">おまかせで作る</button>
      <button type="button" class="wizard-btn ghost" data-act="empty">空のチャンネル</button>
    `);
  }

  async function saveIdea(auto) {
    const text = auto ? "" : val("#wizIdea");
    const confirm = isChecked("#wizConfirm");
    const r = await apiCall("/api/wizard/answer", {
      draft_id: draft.id, rev: draft.rev, question_id: "fixed_idea",
      custom_text: text, auto, step: "idea",
    });
    if (!r || r.error) throw new Error(r.error || "保存できません");
    draft = r.draft;
    delete snap.idea;
    if (confirm !== draft.confirm) {
      const s = await apiCall("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step: "idea", confirm });
      if (s && s.draft) draft = s.draft;
    }
  }

  // ------------------------------------------------------------ partner
  function partnerValues() {
    const ptBtn = $(".wizard-seg-btn.on", $("#wizPartnerType"));
    const pt = ptBtn ? ptBtn.dataset.pt : "original";
    const textSlot = (sel) => {
      const v = val(sel);
      return { value: v, source: v ? "user" : "auto" };
    };
    return {
      partner_type: { value: pt, source: pt === "auto" ? "auto" : "user" },
      work: textSlot("#wizWork"),
      partner_name: textSlot("#wizPartnerName"),
      player_type: { value: val("#wizPlayerType") || "original", source: "user" },
      player_work: textSlot("#wizPlayerWork"),
      player_name: textSlot("#wizPlayerName"),
      player_profile: textSlot("#wizPlayerProfile"),
      start_point: textSlot("#wizStart"),
      canon_fidelity: textSlot("#wizCanon"),
      goal: textSlot("#wizGoal"),
      ending: textSlot("#wizEnding"),
      project: textSlot("#wizProject"),
    };
  }

  async function renderPartner() {
    const mySeq = renderSeq;
    touchedKeys = (snap.partner && snap.partner.touched) ? new Set(snap.partner.touched) : new Set();
    const snapVals = snap.partner && snap.partner.values;
    const s = (k) => (snapVals && snapVals[k] !== undefined)
      ? snapVals[k]
      : ((draft && draft.slots && draft.slots[k] && draft.slots[k].value) || "");
    const pt = s("partner_type") || "original";
    let presets = [];
    try {
      const r = await apiCall("/api/char-presets");
      presets = Array.isArray(r) ? r : (r && r.presets) || [];
    } catch (_) {}
    if (mySeq !== renderSeq) return;

    const presetVal = (snap.partner && snap.partner.preset !== undefined)
      ? snap.partner.preset
      : String((draft && draft.partner_preset_id) || "");

    setBody(`
      <div class="wizard-card">
        <h3>相手は誰？</h3>
        <div class="wizard-seg" id="wizPartnerType">
          ${["original", "copyright", "auto"].map(v => `<button type="button" class="wizard-seg-btn ${pt === v ? "on" : ""}" data-pt="${v}">${v === "original" ? "オリジナル" : v === "copyright" ? "版権キャラ" : "おまかせ"}</button>`).join("")}
        </div>
        <label class="wizard-label">作品・世界観
          <input id="wizWork" class="wizard-input" maxlength="60" value="${E(s("work"))}" placeholder="例: 現代日本 / 架空の王国">
        </label>
        <label class="wizard-label">相手の名前
          <input id="wizPartnerName" class="wizard-input" maxlength="40" value="${E(s("partner_name"))}" placeholder="例: 篠原 美咲">
        </label>
        <label class="wizard-label">キャラプリセット（任意）
          <select id="wizPreset" class="wizard-input">
            <option value="">使わない</option>
            ${presets.map(p => `<option value="${E(p.id || p.name || "")}" ${presetVal === String(p.id || p.name || "") ? "selected" : ""}>${E(p.name || p.id || "")}</option>`).join("")}
          </select>
        </label>
        <h3>あなたは？</h3>
        <label class="wizard-label">立場
          <select id="wizPlayerType" class="wizard-input">
            ${["original", "copyright", "existing"].map(v => `<option value="${v}" ${(s("player_type") || "original") === v ? "selected" : ""}>${v === "original" ? "オリジナル" : v === "copyright" ? "版権キャラ" : "既存の自分"}</option>`).join("")}
          </select>
        </label>
        <label class="wizard-label">あなたの作品（版権の場合）
          <input id="wizPlayerWork" class="wizard-input" maxlength="60" value="${E(s("player_work"))}">
        </label>
        <label class="wizard-label">あなたの名前
          <input id="wizPlayerName" class="wizard-input" maxlength="40" value="${E(s("player_name"))}" placeholder="${E(draft?.defaults?.user_name || "例: あなた")}">
          <small class="wizard-note">空欄なら元のチャンネルの呼び名「${E(draft?.defaults?.user_name || "あなた")}」を引き継ぎます。</small>
        </label>
        <label class="wizard-label">あなたの設定（任意）
          <textarea id="wizPlayerProfile" class="wizard-textarea" rows="2" maxlength="300">${E(s("player_profile"))}</textarea>
        </label>
        <label class="wizard-label">開始地点
          <input id="wizStart" class="wizard-input" maxlength="80" value="${E(s("start_point"))}" placeholder="例: 出会った直後">
        </label>
        <label class="wizard-label">原作準拠度
          <select id="wizCanon" class="wizard-input">
            ${[["mixed", "混合（推奨）"], ["canon", "原作準拠"], ["au", "AU（パラレル）"]].map(([v, l]) =>
              `<option value="${v}" ${(s("canon_fidelity") || "mixed") === v ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </label>
        <label class="wizard-label">物語の目的・ゴール
          <input id="wizGoal" class="wizard-input" maxlength="100" value="${E(s("goal"))}" placeholder="例: 最後に告白して結ばれる">
        </label>
        <label class="wizard-label">結末
          <select id="wizEnding" class="wizard-input">
            ${[["", "おまかせ"], ["happy", "ハッピーエンド"], ["bittersweet", "ビターエンド"],
               ["dark", "ダークエンド"], ["open", "オープン"]].map(([v, l]) =>
              `<option value="${v}" ${s("ending") === v ? "selected" : ""}>${l}</option>`).join("")}
          </select>
        </label>
        <label class="wizard-label">プロジェクト（任意）
          <input id="wizProject" class="wizard-input" maxlength="40" value="${E(s("project") || draft?.defaults?.project || "")}">
        </label>
      </div>`);
    setFoot(`
      <button type="button" class="wizard-btn primary" data-act="partner-next">次へ</button>
      <button type="button" class="wizard-btn ghost" data-act="partner-skip">おまかせ</button>
    `);
    const ptBox = $("#wizPartnerType");
    if (ptBox) {
      $$(".wizard-seg-btn", ptBox).forEach(b => {
        b.onclick = () => {
          $$(".wizard-seg-btn", ptBox).forEach(x => x.classList.toggle("on", x === b));
          touchedKeys.add("partner_type");
        };
      });
    }
    const plSel = $("#wizPlayerType");
    if (plSel) {
      plSel.onchange = () => touchedKeys.add("player_type");
    }
    const cfSel = $("#wizCanon");
    if (cfSel) {
      cfSel.onchange = () => touchedKeys.add("canon_fidelity");
    }
  }

  async function savePartner(auto) {
    const ptBtn = $(".wizard-seg-btn.on", $("#wizPartnerType"));
    const pt = ptBtn ? ptBtn.dataset.pt : "";
    if (auto || pt === "auto") {
      await saveSlots({
        partner_type: { value: "", source: "auto" },
        work: { value: "", source: "auto" },
        partner_name: { value: "", source: "auto" },
        player_type: { value: "", source: "auto" },
        player_name: { value: "", source: "auto" },
        start_point: { value: "", source: "auto" },
      }, "partner");
      await apiCall("/api/wizard/answer", {
        draft_id: draft.id, rev: draft.rev, question_id: "fixed_preset",
        option_ids: [], step: "partner",
      });
      delete snap.partner;
      return;
    }
    await saveSlots(partnerValues(), "partner");
    const preset = val("#wizPreset");
    const r = await apiCall("/api/wizard/answer", {
      draft_id: draft.id, rev: draft.rev, question_id: "fixed_preset",
      option_ids: preset ? [preset] : [], step: "partner",
    });
    if (r && r.draft) draft = r.draft;
    delete snap.partner;
  }

  // ------------------------------------------------------------ tags
  function renderTags() {
    const tags = (wmeta && wmeta.tags && wmeta.tags.tags) || [];
    const axes = (wmeta && wmeta.tags && wmeta.tags.axes) || {};
    if (snap.tags) {
      pendingTags = {
        tags: snap.tags.tags.slice(),
        ng: snap.tags.ng.slice(),
        custom: snap.tags.custom,
      };
      tagDirty = true;
    } else if (!pendingTags) {
      initPendingTags();
    }
    const groups = [
      ["relation", "関係性"], ["world", "世界観"], ["tone", "雰囲気"],
      ["kink", "特殊要素"], ["role", "あなたの立場"],
    ];
    setBody(`
      <div class="wizard-card">
        <h3>関係性・世界観・雰囲気</h3>
        ${groups.map(([axis, label]) => {
          const list = tags.filter(t => t.axis === axis);
          if (!list.length) return "";
          const max = (axes[axis] && axes[axis].max) || 4;
          return `<div class="wizard-group"><div class="wizard-group-head"><span>${label}</span><span class="muted">最大${max}</span></div>
            <div class="wizard-chips">${list.map(t => chip(t.label, pendingTags.tags.includes(t.id), `data-tag="${E(t.id)}" data-axis="${axis}"`)).join("")}</div></div>`;
        }).join("")}
        <h3>NG（避けたい要素）</h3>
        <div class="wizard-chips">${((wmeta && wmeta.tags && wmeta.tags.ng_presets) || []).map(n =>
          chip(n.label, pendingTags.ng.includes(n.id), `data-ng="${E(n.id)}" ${n.locked ? "disabled" : ""}`)).join("")}</div>
        <label class="wizard-label">その他のNG（カンマ区切り）
          <input id="wizCustomNg" class="wizard-input" maxlength="200" value="${E(pendingTags.custom)}">
        </label>
        <p id="wizTagWarn" class="wizard-warn"></p>
      </div>`);
    setFoot(`
      <button type="button" class="wizard-btn primary" data-act="tags-next">次へ</button>
      <button type="button" class="wizard-btn" data-act="tags-finish">ここまでで作る</button>
    `);
    const cEl = $("#wizCustomNg");
    if (cEl) {
      cEl.addEventListener("input", (e) => {
        if (pendingTags) pendingTags.custom = e.target.value;
        scheduleTagSave();
      });
    }
  }

  function toggleTag(id) {
    const tag = ((wmeta && wmeta.tags && wmeta.tags.tags) || []).find(x => x.id === id);
    if (!tag) return;
    const max = (((wmeta && wmeta.tags && wmeta.tags.axes) || {})[tag.axis] || {}).max || 4;
    const same = pendingTags.tags.filter(x => {
      const t = (((wmeta && wmeta.tags && wmeta.tags.tags)) || []).find(y => y.id === x);
      return t && t.axis === tag.axis;
    });
    if (!pendingTags.tags.includes(id) && same.length >= max) {
      const axisLabel = ((((wmeta && wmeta.tags && wmeta.tags.axes) || {})[tag.axis] || {}).label) || tag.axis;
      say(`${axisLabel}は最大${max}個です`); return;
    }
    pendingTags.tags = pendingTags.tags.includes(id)
      ? pendingTags.tags.filter(x => x !== id)
      : pendingTags.tags.concat([id]);
    const el = $(`[data-tag="${escSel(id)}"]`);
    if (el) el.classList.toggle("on", pendingTags.tags.includes(id));
    scheduleTagSave();
  }

  function toggleNg(id) {
    pendingTags.ng = pendingTags.ng.includes(id)
      ? pendingTags.ng.filter(x => x !== id)
      : pendingTags.ng.concat([id]);
    const el = $(`[data-ng="${escSel(id)}"]`);
    if (el) el.classList.toggle("on", pendingTags.ng.includes(id));
    scheduleTagSave();
  }

  async function saveTagsAndGo(next) {
    if (tagTimer) { clearTimeout(tagTimer); tagTimer = null; }
    const custom = val("#wizCustomNg");
    if (pendingTags) {
      if (pendingTags.custom !== custom) {
        pendingTags.custom = custom;
        tagDirty = true;
      }
    } else {
      initPendingTags();
      pendingTags.custom = custom;
      tagDirty = true;
    }
    await drainTagSaves();
    delete snap.tags;
    if (next === "finish") return startCreateChain(false);
    if (draft && draft.ready && !draft.done_questions) {
      const ask = window.confirm("情報が揃いました。追加の質問をしますか？\n（キャンセルでこのまま作成）");
      if (!ask) return startCreateChain(false);
    }
    startCreateChain(true);
  }

  // ------------------------------------------------------------ questions
  function renderQuestions() {
    const qs = ((draft && draft.questions) || []).filter(q => !((draft && draft.asked_question_ids) || []).includes(q.id));
    if (!qs.length) {
      if (draft && draft.done_questions) return renderQuestionsDone();
      if (!jobId && draft && draft.last_job) {
        renderQuestionsLoading();
        resumeJob(draft.last_job);
        return;
      }
      if (!jobId) startJob("questions");
      return renderQuestionsLoading();
    }
    const q = qs[0];
    const ft = foot();
    if (ft) ft.dataset.picked = "";
    setBody(`
      <div class="wizard-card">
        <p class="wizard-why">${E(q.why || "")}</p>
        <h3>${E(q.text)}</h3>
        <div class="wizard-options">
          ${(q.options || []).map(o => `<button type="button" class="wizard-option" data-opt="${E(o.id)}">
            <strong>${E(o.label)}</strong><span>${E(o.description || "")}</span>
          </button>`).join("")}
        </div>
        <label class="wizard-label">自由入力（任意）
          <input id="wizFree" class="wizard-input" maxlength="120" placeholder="選択肢にない答え">
        </label>
      </div>`);
    setFoot(`
      <button type="button" class="wizard-btn primary" data-act="q-answer">答える</button>
      <button type="button" class="wizard-btn" data-act="q-auto">おまかせ</button>
      <button type="button" class="wizard-btn ghost" data-act="q-finish">このまま作る</button>
    `);
  }

  function renderQuestionsLoading() {
    setBody(`
      <div class="wizard-card">
        <h3>質問を考えています</h3>
        <p class="muted">回答済み: ${((draft && draft.asked_question_ids) || []).length} / ${(wmeta && wmeta.max_questions) || 6}</p>
        ${busyHtml("AIが追加の質問を考えています…")}
      </div>`);
    setFoot(`<button type="button" class="wizard-btn ghost" data-act="q-finish">質問を飛ばして作る</button>`);
  }

  function renderQuestionsDone() {
    setBody(`
      <div class="wizard-card">
        <h3>質問はここまで</h3>
        <p>この内容で設定を作ります。</p>
        <p class="muted">未設定の項目は「おまかせ」で補います。</p>
      </div>`);
    setFoot(`<button type="button" class="wizard-btn primary" data-act="q-finish">設定を作る</button>`);
  }

  async function answerQuestion(auto) {
    const qs = ((draft && draft.questions) || []).filter(q => !((draft && draft.asked_question_ids) || []).includes(q.id));
    const q = qs[0];
    if (!q) return;
    const free = val("#wizFree");
    const ft = foot();
    const picked = free ? "" : ((ft && ft.dataset.picked) || "");
    const r = await apiCall("/api/wizard/answer", {
      draft_id: draft.id, rev: draft.rev, question_id: q.id,
      option_ids: picked ? [picked] : [], custom_text: free,
      auto, step: "questions",
    });
    if (!r || r.error) throw new Error(r.error || "回答を保存できません");
    draft = r.draft;
    if (((draft && draft.questions) || []).filter(x => !((draft && draft.asked_question_ids) || []).includes(x.id)).length) {
      renderQuestions();
    } else if (draft && (draft.done_questions || draft.round >= ((wmeta && wmeta.max_rounds) || 2))) {
      renderQuestionsDone();
    } else {
      const more = window.confirm("追加で2問ほど聞きますか？\n（キャンセルでこのまま作成）");
      if (more) startJob("questions");
      else startCreateChain(false);
    }
  }

  // ------------------------------------------------------------ preview
  function renderPreview() {
    const rawC = (draft && draft.compose && draft.compose.setting) || {};
    const snapPrev = snap.preview || {};
    const c = { ...rawC, ...snapPrev };
    if (rawC.character || snapPrev.character) {
      c.character = { ...(rawC.character || {}), ...(snapPrev.character || {}) };
    }
    const ch = c.character || {};
    const warn = Array.from(new Set(
      ((draft && draft.warnings) || []).concat((draft && draft.compose && draft.compose.warnings) || [])
    ));
    const imgMode = (draft && draft.base_info && draft.base_info.image_mode) || "always";
    setBody(`
      <div class="wizard-card">
        ${warn.length ? `<div class="wizard-warn">${warn.map(x => `<p>${E(x)}</p>`).join("")}</div>` : ""}
        <label class="wizard-label">タイトル
          <input id="pvTitle" class="wizard-input" maxlength="40" value="${E(c.title)}">
        </label>
        <label class="wizard-label">あらすじ
          <textarea id="pvSummary" class="wizard-textarea" rows="3" maxlength="300">${E(c.summary)}</textarea>
        </label>
        <div class="wizard-grid2">
          <label class="wizard-label">相手の名前
            <input id="pvName" class="wizard-input" maxlength="40" value="${E(ch.name)}">
          </label>
          <label class="wizard-label">年齢
            <input id="pvAge" class="wizard-input" maxlength="10" value="${E(ch.age)}">
          </label>
        </div>
        <label class="wizard-label">あなたの名前
          <input id="pvUser" class="wizard-input" maxlength="40" value="${E(c.user_name || draft?.defaults?.user_name || "あなた")}">
        </label>
        <label class="wizard-label">キャラ説明
          <textarea id="pvProfile" class="wizard-textarea" rows="3" maxlength="600">${E(ch.profile)}</textarea>
        </label>
        <label class="wizard-label">口調
          <textarea id="pvSpeech" class="wizard-textarea" rows="2" maxlength="400">${E(ch.speech)}</textarea>
        </label>
        <label class="wizard-label">開始状況
          <textarea id="pvScenario" class="wizard-textarea" rows="4" maxlength="2000">${E(c.scenario)}</textarea>
        </label>
        <label class="wizard-label">第一声
          <textarea id="pvFirst" class="wizard-textarea" rows="4" maxlength="1200">${E(c.first_message)}</textarea>
        </label>
        <label class="wizard-label">プロジェクト
          <input id="pvProject" class="wizard-input" maxlength="40" value="${E(c.project || draft?.defaults?.project || "")}">
        </label>
        <details class="wizard-details">
          <summary>本文AIへの指示</summary>
          <textarea id="pvInstructions" class="wizard-textarea" rows="5" maxlength="2000">${E(c.instructions)}</textarea>
        </details>
        <div class="wizard-preview-meta">
          <span>画像タグ: ${E(tagsText(((c.chars || [])[0]) || {})) || "なし"}</span>
          <span>開幕画像: ${imgMode === "off" ? "生成しない" : "作成後に非同期生成"}</span>
          <span>自動補完: ${E(((draft && draft.compose && draft.compose.auto_slots) || []).map(k => draft?.slots?.[k]?.label || k).join("、") || "なし")}</span>
        </div>
      </div>`);
    setFoot(`
      <button type="button" class="wizard-btn primary" data-act="create">この内容で作成</button>
      <button type="button" class="wizard-btn" data-act="recompose">作り直す</button>
    `);
  }

  function collectOverrides() {
    return {
      title: val("#pvTitle"), summary: val("#pvSummary"),
      scenario: val("#pvScenario"), first_message: val("#pvFirst"),
      instructions: val("#pvInstructions"),
      user_name: val("#pvUser"),
      project: val("#pvProject"),
      character: {
        name: val("#pvName"), age: val("#pvAge"),
        profile: val("#pvProfile"), speech: val("#pvSpeech"),
      },
    };
  }

  async function savePreview() {
    if (screen !== "preview") return;
    const r = await apiCall("/api/wizard/preview", {
      draft_id: draft.id, rev: draft.rev, setting: collectOverrides(),
    });
    if (!r || r.error) throw new Error(r.error || "プレビューを保存できません");
    draft = r.draft;
    delete snap.preview;
  }

  // ------------------------------------------------------------ creating
  function renderCreating() {
    setBody(`
      <div class="wizard-card">
        ${busyHtml("設定を書いています。少しお待ちください…")}
        <div id="wizJobMsg" class="wizard-msg"></div>
      </div>`);
    setFoot(`<button type="button" class="wizard-btn ghost" data-act="cancel-job">キャンセル</button>`);
    if (!jobId && draft && draft.last_job) {
      resumeJob(draft.last_job);
      return;
    }
    if (!jobId) startJob("compose");
  }

  function renderDone() {
    setBody(`
      <div class="wizard-card center">
        <h3>チャンネルを作成しました</h3>
        <p>${E((draft && draft.title_hint) || "")}</p>
      </div>`);
    setFoot(`<button type="button" class="wizard-btn primary" data-act="open-created">開く</button>`);
  }

  async function startJob(kind) {
    if (busy) return;
    busy = true;
    try {
      if (jobTimer) clearTimeout(jobTimer);
      if (kind === "compose") {
        const parent = navStack[navStack.length - 2] || "tags";
        returnTo = ["questions", "preview", "creating"].includes(screen) ? parent : (screen === "model" ? "idea" : screen);
      }
      const r = await apiCall(`/api/wizard/${kind}`, { draft_id: draft.id, rev: draft.rev });
      if (!r || r.error) throw new Error(r.error || "開始できません");
      if (r.draft) draft = r.draft;
      jobId = r.job_id;
      jobKind = kind;
      if (kind === "compose") navigate("creating");
      else navigate("questions");
      pollJob();
    } catch (e) {
      chromeFor(kind === "compose" ? "creating" : "questions");
      setBody(errBox(e.message, `<button type="button" class="wizard-btn" data-act="retry-${kind}">再試行</button>
        <button type="button" class="wizard-btn ghost" data-act="use-template">AIなしで作る</button>`));
    } finally {
      busy = false;
    }
  }

  async function pollJob() {
    const myJob = jobId;
    if (!myJob) return;
    try {
      const j = await apiCall(`/api/wizard/job/${myJob}`);
      if (jobId !== myJob) return;
      if (!j || (j.ok === false && !j.status)) throw new Error((j && j.error) || "ジョブが見つかりません");
      const msg = $("#wizJobMsg");
      if (msg) msg.textContent = j.progress || "";
      if (j.status === "canceled") {
        jobId = "";
        stopPoll();
        return;
      }
      if (j.status === "done") {
        jobId = "";
        stopPoll();
        if (j.result && j.result.draft) draft = j.result.draft;
        if (jobKind === "questions") {
          navigate("questions");
        } else {
          delete snap.preview;
          settleTo("preview", returnTo);
          if (autoCreateOnce) { autoCreateOnce = false; doCreate(); }
          else if (draft && draft.confirm) { /* stay at preview */ }
          else doCreate();
        }
        return;
      }
      if (j.status === "error") {
        jobId = "";
        stopPoll();
        chromeFor(jobKind === "compose" ? "creating" : "questions");
        const canTemplate = jobKind === "compose";
        setBody(errBox(j.error || "生成に失敗しました",
          `<button type="button" class="wizard-btn" data-act="retry-${jobKind}">再試行</button>
           ${canTemplate ? `<button type="button" class="wizard-btn ghost" data-act="use-template">AIなしで作る</button>` : ""}`));
        return;
      }
      jobTimer = setTimeout(pollJob, 1500);
    } catch (e) {
      if (jobId !== myJob) return;
      jobTimer = setTimeout(pollJob, 3000);
    }
  }

  async function resumeJob(jid) {
    if (!jid) return;
    if (resumePromise) return resumePromise;
    resumePromise = (async () => {
      try {
        const j = await apiCall(`/api/wizard/job/${jid}`).catch(() => null);
        if (!j || !j.status) return;
        const kind = j.kind || ((draft && draft.compose && draft.compose.setting) ? "compose" : "questions");
        jobKind = kind;
        if (j.status === "canceled") return;
        if (j.status === "done") {
          if (j.result && j.result.draft) draft = j.result.draft;
          if (kind === "compose") {
            delete snap.preview;
            if (autoCreateOnce || (draft && !draft.confirm)) {
              autoCreateOnce = false;
              return doCreate();
            }
          }
          enterScreen(entryScreen());
          return;
        }
        if (j.status === "error") {
          chromeFor(kind === "compose" ? "creating" : "questions");
          const canTemplate = kind === "compose";
          setBody(errBox(j.error || "生成に失敗しました",
            `<button type="button" class="wizard-btn" data-act="retry-${kind}">再試行</button>
             ${canTemplate ? `<button type="button" class="wizard-btn ghost" data-act="use-template">AIなしで作る</button>` : ""}`));
          return;
        }
        if (j.status === "queued" || j.status === "running") {
          jobId = jid;
          const parent = navStack[navStack.length - 2] || "tags";
          returnTo = ["questions", "preview", "creating"].includes(screen) ? parent : (screen === "model" ? "idea" : screen);
          navigate(kind === "compose" ? "creating" : "questions");
          pollJob();
        }
      } finally {
        resumePromise = null;
      }
    })();
    return resumePromise;
  }

  async function cancelJob() {
    const jid = jobId;
    if (!jid) return { ok: true, draft };
    try {
      const r = await apiCall("/api/wizard/job_cancel", { job_id: jid, draft_id: draft ? draft.id : "" });
      if (!r || r.ok === false || !r.draft) {
        return { ok: false, error: (r && r.error) || "生成を中止できませんでした" };
      }
      jobId = ""; autoCreateOnce = false; stopPoll();
      draft = r.draft;
      return { ok: true, draft: r.draft };
    } catch (e) {
      return { ok: false, error: e.message || "生成を中止できませんでした" };
    }
  }

  function startCreateChain(withQuestions) {
    if (!draft || !draft.model || draft.model.provider === "none" || draft.model.ai_enabled === false) {
      return doCreate({}, true);
    }
    if (draft.compose && !draft.compose_stale) {
      if (autoCreateOnce) { autoCreateOnce = false; return doCreate(); }
      return draft.confirm ? navigate("preview") : doCreate();
    }
    if (withQuestions && !draft.done_questions && draft.round < ((wmeta && wmeta.max_rounds) || 2)) {
      return startJob("questions");
    }
    return startJob("compose");
  }

  async function doCreate(overrides, useTemplate) {
    if (busy) return;
    autoCreateOnce = false;
    busy = true;
    try {
      const r = await apiCall("/api/wizard/create", {
        draft_id: draft.id, rev: draft.rev,
        overrides: overrides || null,
        use_template: !!useTemplate,
      });
      if (!r || r.error) {
        if (r && r.code === "wizard_not_composed" && !useTemplate) {
          setBody(errBox(r.error,
            `<button type="button" class="wizard-btn" data-act="use-template">AIなしで作る</button>`));
          return;
        }
        throw new Error((r && r.error) || "作成できません");
      }
      draft = r.draft || draft;
      if (r.sid) {
        if (appMeta()?.app?.new_channel_ide !== false && typeof checked === "function") {
          checked("/api/ide/new", { sid: r.sid, target: "both" })
            .then(async () => {
              if (typeof loadIdeSessions === "function") await loadIdeSessions();
              if (typeof refreshSessions === "function") await refreshSessions();
            })
            .catch(() => {});
        }
        close(true);
        await activateCreatedSession(r.sid);
        const imgMode = (r.draft && r.draft.base_info && r.draft.base_info.image_mode) || "always";
        if (r.opening_image && !r.created) say("開幕画像を裏で生成しています");
        else if (!r.created && imgMode !== "off") say("開幕画像を開始できませんでした");
      } else {
        enterScreen("done");
      }
    } catch (e) {
      say(e.message);
      if (draft && draft.compose) {
        settleTo("preview", returnTo);
        setFoot(`<button type="button" class="wizard-btn" data-act="create">再試行</button>`);
      } else {
        settleTo("tags", returnTo);
      }
    } finally {
      busy = false;
    }
  }

  async function useTemplate() {
    return doCreate({}, true);
  }

  // ------------------------------------------------------------ events
  function onBodyClick(e) {
    const ex = e.target.closest("[data-example]");
    if (ex) {
      const t = $("#wizIdea");
      if (t) {
        t.value = ex.dataset.example;
        formSeq += 1;
      }
      return;
    }
    const tag = e.target.closest("[data-tag]");
    if (tag) return toggleTag(tag.dataset.tag);
    const ng = e.target.closest("[data-ng]");
    if (ng && !ng.disabled) return toggleNg(ng.dataset.ng);
    const opt = e.target.closest("[data-opt]");
    if (opt) {
      $$(".wizard-option").forEach(x => x.classList.toggle("on", x === opt));
      const ft = foot();
      if (ft) ft.dataset.picked = opt.dataset.opt;
      const f = $("#wizFree");
      if (f) f.value = "";
      return;
    }
    const act = e.target.closest("[data-act]");
    if (!act) return;
    const a = act.dataset.act;
    (async () => {
      try {
        if (a === "base-inherit") return runAction("next", () => saveBase(true));
        if (a === "base-empty") return runAction("next", () => saveBase(false));
        if (a === "base-continue") return runAction("next", async () => navigate("idea"));
        if (a === "model-test") return testModel();
        if (a === "model-save") return runAction("next", () => saveModel(false));
        if (a === "model-skip") return runAction("next", () => saveModel(true));
        if (a === "idea-next") return runAction("next", async () => { await saveIdea(false); autoCreateOnce = false; openModelStep(); });
        if (a === "idea-auto") return runAction("next", async () => {
          await saveIdea(true);
          autoCreateOnce = true;
          openModelStep();
        });
        if (a === "empty") { close(true); if (window.callLegacyNewSession) window.callLegacyNewSession(); return; }
        if (a === "partner-next") return runAction("next", async () => { await savePartner(false); navigate("tags"); });
        if (a === "partner-skip") return runAction("next", async () => { await savePartner(true); navigate("tags"); });
        if (a === "tags-next") return runAction("next", () => saveTagsAndGo("next"));
        if (a === "tags-finish") return runAction("create", () => saveTagsAndGo("finish"));
        if (a === "q-answer") return runAction("next", () => answerQuestion(false));
        if (a === "q-auto") return runAction("next", () => answerQuestion(true));
        if (a === "q-finish") return runAction("create", () => startCreateChain(false));
        if (a === "create") {
          return runAction("create", async () => {
            const ov = collectOverrides();
            await savePreview().catch(() => {});
            return doCreate(ov);
          });
        }
        if (a === "recompose") {
          return runAction("recompose", async () => {
            delete snap.preview;
            const r = await apiCall("/api/wizard/model", { draft_id: draft.id, rev: draft.rev, ...(draft.model || {}) });
            if (r && r.draft) draft = r.draft;
            return startJob("compose");
          });
        }
        if (a === "cancel-job") {
          return runAction("cancel", async () => {
            await cancelJob();
            return settleTo((draft && draft.compose) ? "preview" : "tags", returnTo);
          });
        }
        if (a === "open-created") { close(true); return activateCreatedSession(draft.created_sid); }
        if (a === "retry-questions") return startJob("questions");
        if (a === "retry-compose") return startJob("compose");
        if (a === "use-template") return runAction("create", () => useTemplate());
      } catch (err) {
        say(err.message);
      }
    })();
  }

  // ------------------------------------------------------------ settings
  async function loadWizardSettings() {
    const box = $("#f_wizard_provider");
    if (!box) return;
    try {
      const m = await apiCall("/api/wizard/meta");
      if (!m) return;
      if ($("#f_wizard_enabled")) $("#f_wizard_enabled").checked = !!m.enabled;
      if ($("#f_wizard_provider")) $("#f_wizard_provider").value = m.current?.provider || "google";
      if ($("#f_wizard_model")) $("#f_wizard_model").value = m.current?.model || "";
      if ($("#f_wizard_thinking_level")) $("#f_wizard_thinking_level").value = m.current?.thinking_level || "high";
      if ($("#f_wizard_confirm")) $("#f_wizard_confirm").checked = !!m.confirm;
    } catch (_) {}
  }

  async function saveWizardSettings() {
    if (!$("#f_wizard_provider")) return;
    try {
      await apiCall("/api/app-settings", {
        wizard_enabled: isChecked("#f_wizard_enabled"),
        wizard_provider: val("#f_wizard_provider") || "google",
        wizard_model: val("#f_wizard_model"),
        wizard_thinking_level: val("#f_wizard_thinking_level") || "high",
        wizard_confirm: isChecked("#f_wizard_confirm"),
      });
      const m = await apiCall("/api/wizard/meta");
      if (m && !m.error) {
        wmeta = m;
        const sp = $("#f_wizard_provider");
        if (sp) sp.value = m.current?.provider || "google";
        const sm = $("#f_wizard_model");
        if (sm) sm.value = m.current?.model || "";
        const st = $("#f_wizard_thinking_level");
        if (st) st.value = m.current?.thinking_level || "high";
        const sc = $("#f_wizard_confirm");
        if (sc) sc.checked = !!m.confirm;
        const se = $("#f_wizard_enabled");
        if (se) se.checked = !!m.enabled;
      }
    } catch (e) {
      say(e.message);
    }
  }

  function init() {
    if (!$("#wizardRoot")) return;
    const clBtn = $("#wizardClose");
    if (clBtn) clBtn.onclick = () => close(false);
    const bkBtn = $("#wizardBack");
    if (bkBtn) bkBtn.onclick = goBack;
    const mcChip = $("#wizardModelChip");
    if (mcChip) mcChip.onclick = () => {
      if (busy || jobId || screen === "creating" || screen === "done" || pending) return;
      openModelEdit();
    };

    const modalEl = $(".wizard-modal", root());
    if (modalEl) modalEl.addEventListener("click", onBodyClick);

    const bEl = $("#wizardBody");
    if (bEl) {
      bEl.addEventListener("input", (e) => {
        formSeq += 1;
        if (e.target && e.target.id === "wizFree") {
          const ft = foot();
          if (ft) ft.dataset.picked = "";
          $$(".wizard-option").forEach(x => x.classList.remove("on"));
        }
      });
      bEl.addEventListener("change", () => {
        formSeq += 1;
      });
    }

    const rEl = $("#wizardRoot");
    if (rEl) {
      rEl.addEventListener("click", (e) => {
        if (e.target.closest(".wizard-backdrop")) close(false);
      });
    }

    document.addEventListener("keydown", (e) => {
      const r = root();
      if (!r || r.classList.contains("hidden")) return;
      if (dialogOpen) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close(false);
      } else if (e.key === "Tab") {
        const m = $(".wizard-modal", r);
        if (!m) return;
        const focusables = Array.from(m.querySelectorAll(
          'button:not([disabled]):not(.hidden), input:not([disabled]):not(.hidden), textarea:not([disabled]):not(.hidden), select:not([disabled]):not(.hidden), [tabindex]:not([tabindex="-1"]):not([disabled]):not(.hidden)'
        )).filter(el => el.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const tEl = $("#wizardTitle");
        if (document.activeElement === tEl) {
          e.preventDefault();
          if (e.shiftKey) last.focus();
          else first.focus();
          return;
        }
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }, true);

    const appSetEl = $("#appSettings");
    if (appSetEl) {
      if (appSetEl.open) loadWizardSettings();
      appSetEl.addEventListener("toggle", () => {
        if (appSetEl.open) loadWizardSettings();
      });
    }
    const appSaveBtn = $("#appSave");
    if (appSaveBtn) {
      appSaveBtn.addEventListener("click", () => setTimeout(saveWizardSettings, 0));
    }
    ["#f_wizard_enabled", "#f_wizard_provider", "#f_wizard_model",
     "#f_wizard_thinking_level", "#f_wizard_confirm"].forEach((sel) => {
      const el = $(sel);
      if (el) el.addEventListener("change", saveWizardSettings);
    });
  }

  function showModel() {
    const r = root();
    if (r && r.classList.contains("hidden")) {
      open().then(() => openModelEdit()).catch(() => {});
    } else {
      openModelEdit();
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.RPWizard = { open, close, showModel };

  if (typeof window !== "undefined" && window.__RP_WIZARD_TEST__) {
    window.RPWizard.__test = {
      get navStack() { return navStack; },
      set navStack(v) { navStack = v; },
      get screen() { return screen; },
      set screen(v) { screen = v; },
      get draft() { return draft; },
      set draft(v) { draft = v; },
      get wmeta() { return wmeta; },
      set wmeta(v) { wmeta = v; },
      get snap() { return snap; },
      set snap(v) { snap = v; },
      get busy() { return busy; },
      set busy(v) { busy = v; },
      get pending() { return pending; },
      set pending(v) { pending = v; },
      get jobId() { return jobId; },
      set jobId(v) { jobId = v; },
      get jobKind() { return jobKind; },
      set jobKind(v) { jobKind = v; },
      get rendering() { return rendering; },
      set rendering(v) { rendering = v; },
      get renderSeq() { return renderSeq; },
      set renderSeq(v) { renderSeq = v; },
      get formSeq() { return formSeq; },
      set formSeq(v) { formSeq = v; },
      get returnTo() { return returnTo; },
      set returnTo(v) { returnTo = v; },
      get pendingTags() { return pendingTags; },
      set pendingTags(v) { pendingTags = v; },
      get tagDirty() { return tagDirty; },
      set tagDirty(v) { tagDirty = v; },
      get jobTimer() { return jobTimer; },
      set jobTimer(v) { jobTimer = v; },
      get touchedKeys() { return touchedKeys; },
      set touchedKeys(v) { touchedKeys = v; },
      ENTRY_STACK,
      PARTNER_INVALIDATES,
      navigate,
      render,
      backRender,
      settleTo,
      resetTo,
      enterScreen,
      canBack,
      closeModel,
      openModelStep,
      openModelEdit,
      progressFor,
      chromeFor,
      runAction,
      flushOne,
      flushScreen,
      flushStable,
      flushIdea,
      flushPartner,
      flushTags,
      flushPreview,
      readIdeaForm,
      readPartnerForm,
      buildPartnerPatch,
      buildPreviewPatch,
      readScreenValues,
      saveTagsToServer,
      drainTagSaves,
      scheduleTagSave,
      cancelJob,
      goBack,
      closeNow,
      pollJob,
      resumeJob,
      initPendingTags,
    };
  }
})();
