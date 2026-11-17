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

  const root = () => $("#wizardRoot");
  const body = () => $("#wizardBody");
  const foot = () => $("#wizardFoot");

  async function apiCall(path, data) {
    if (typeof api !== "function") throw new Error("api() がありません");
    const r = await api(path, data);
    if (r && r.code === "rev") {
      // サーバーが最新の下書きを返すので、画面だけ追従させる（古い rev で上書きしない）
      if (r.draft) draft = r.draft;
      pendingTags = null;
      if (typeof say === "function") say("他の操作で更新されました。最新の内容を読み込みました");
      if (screen === "base" && typeof renderBase === "function") renderBase();
      else if (screen === "partner") renderPartner();
      else if (screen === "tags") renderTags();
      else if (screen === "preview") renderPreview();
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
    const r = root();
    const m = r ? $(".wizard-modal", r) : null;
    if (!m) return;
    const first = m.querySelector("button, input, textarea, select, [tabindex]");
    if (first) setTimeout(() => first.focus(), 0);
  }

  function releaseFocus() {
    if (focusEl && focusEl.focus) try { focusEl.focus(); } catch (_) {}
    focusEl = null;
  }

  function titleFor(name) {
    return {
      model: "作成AIを選ぶ",
      base: "引き継ぎ元を選ぶ",
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
    const order = ["base", "model", "idea", "partner", "tags", "questions", "preview"];
    const i = order.indexOf(name);
    if (i < 0) return "";
    return `${i + 1} / ${order.length}`;
  }

  function updateChrome() {
    const tEl = $("#wizardTitle");
    if (tEl) tEl.textContent = titleFor(screen);
    const pEl = $("#wizardProgress");
    if (pEl) pEl.textContent = progressFor(screen);
    const bEl = $("#wizardBack");
    if (bEl) bEl.classList.toggle("hidden", screen === "base" || screen === "model" || screen === "creating");
    const mc = (draft && draft.model && draft.model.model) ? draft.model : ((wmeta && wmeta.current) || {});
    const chip = $("#wizardModelChip");
    if (chip) {
      chip.textContent = mc.model ? `AI: ${mc.model.split("/").pop()}` : "AI: 未設定";
      chip.classList.toggle("warn", !mc.model);
      // 生成中にモデル画面へ飛ぶとジョブと下書きが壊れるので触れなくする
      const locked = !!jobId || screen === "creating" || screen === "done" || busy;
      chip.disabled = locked;
      chip.setAttribute("aria-disabled", locked ? "true" : "false");
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
  function setFoot(html) { const f = foot(); if (f) f.innerHTML = html || ""; }

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

  async function ensureDraft(baseSid) {
    const sidToUse = (baseSid !== undefined && baseSid !== null) ? baseSid : currentSid();
    const r = await apiCall("/api/wizard/start", { base_sid: sidToUse });
    if (!r || r.ok === false || r.error) throw new Error((r && r.error) || "下書きを作成できません");
    if (r.resumed && r.draft) {
      const resume = window.confirm("前回の続きから再開しますか？\n（キャンセルで新しく始めます）");
      if (!resume) {
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

  function show(name) {
    stopPoll();
    screen = name;
    updateChrome();
    persistStep(name);
    if (name === "base") return renderBase();
    if (name === "model") return renderModel();
    if (name === "idea") return renderIdea();
    if (name === "partner") return renderPartner();
    if (name === "tags") return renderTags();
    if (name === "questions") return renderQuestions();
    if (name === "preview") return renderPreview();
    if (name === "creating") return renderCreating();
    if (name === "done") return renderDone();
  }

  async function open(baseSid) {
    if (busy) return;
    autoCreateOnce = false;
    // 前の下書きのジョブ・タグ選択・タイマーを引きずらない（別チャンネルへの漏れ防止）
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
        // trapFocus() の後に失敗した場合、フォーカスを戻さないと背面が操作できなくなる
        stopPoll();
        if (r) {
          r.classList.add("hidden");
          r.setAttribute("aria-hidden", "true");
        }
        document.body.classList.remove("wizard-open");
        releaseFocus();
        if (typeof updateFabState === "function") updateFabState();
        throw e;
      }
      // show() の中で startJob() を呼べるように、描画前に busy を下ろす
      busy = false;
      let start = entryScreen();
      if (start !== "base" && start !== "done" && start !== "creating") {
        const hasModel = draft.model && (draft.model.model || draft.model.provider === "antigravity" || draft.model.provider === "none");
        if (!(hasModel || wmeta.configured)) start = "model";
      }
      show(start);
      if (start !== "base" && draft.last_job && !jobId) resumeJob(draft.last_job);
    } finally {
      busy = false;
    }
  }

  function close(force) {
    if (!force && draft && draft.status !== "created") {
      if (!window.confirm("作成中の内容は下書きとして保存されます。閉じますか？")) return;
      // 生成中に step を保存すると rev が上がり、裏の質問/compose が stale になる。
      // ジョブ中は wizard_start_job が step を保存済みなので、ここでは送らない。
      const running = !!(jobId || (draft.last_job &&
        (draft.status === "composing" || draft.step === "questions" || draft.step === "creating")));
      if (!running) {
        apiCall("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step: screen }).catch(() => {});
      }
    }
    autoCreateOnce = false;
    stopPoll();
    const r = root();
    if (r) {
      r.classList.add("hidden");
      r.setAttribute("aria-hidden", "true");
    }
    document.body.classList.remove("wizard-open");
    releaseFocus();
    if (typeof updateFabState === "function") updateFabState();
  }

  function goBack() {
    if (screen === "base") return close();
    if (screen === "model") return show("base");
    if (screen === "idea") return show("model");
    if (screen === "partner") return show("idea");
    if (screen === "tags") return show("partner");
    if (screen === "questions") return show("tags");
    if (screen === "preview") return show("tags");
    if (screen === "creating") {
      if (jobId && !window.confirm("生成をキャンセルして戻りますか？")) return;
      cancelJob();
      show((draft && draft.compose) ? "preview" : "tags");
    }
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

  // pendingTags はタグ画面の作業中の値。保存後も破棄せず、サーバーの結果へ同期する。
  // （null にすると、500ms の自動保存後にタグを押した瞬間に TypeError で落ちる）
  function initPendingTags() {
    pendingTags = {
      tags: ((draft && draft.selected_tags) || []).slice(),
      ng: ((draft && draft.selected_ng) || []).slice(),
      custom: ((draft && draft.custom_ng) || []).join("、"),
    };
  }

  function syncTagChips() {
    if (!pendingTags) return;
    $$("#wizardBody [data-tag]").forEach(el => el.classList.toggle("on", pendingTags.tags.includes(el.dataset.tag)));
    $$("#wizardBody [data-ng]").forEach(el => el.classList.toggle("on", pendingTags.ng.includes(el.dataset.ng)));
  }

  async function saveTagsToServer() {
    if (!pendingTags) return;
    const requested = pendingTags.tags.slice();
    const customEl = $("#wizCustomNg");
    if (customEl) pendingTags.custom = customEl.value;
    const r = await apiCall("/api/wizard/tags", {
      draft_id: draft.id, rev: draft.rev,
      selected_tags: pendingTags.tags,
      selected_ng: pendingTags.ng,
      custom_ng: pendingTags.custom,
    });
    if (!r || r.error) throw new Error(r.error || "タグを保存できません");
    draft = r.draft;
    // サーバー側で NG と衝突して外れたタグを画面にも反映する
    pendingTags = {
      tags: (draft.selected_tags || []).slice(),
      ng: (draft.selected_ng || []).slice(),
      custom: pendingTags.custom,
    };
    syncTagChips();
    const dropped = requested.filter(t => !pendingTags.tags.includes(t));
    if (dropped.length) say("NGと衝突するタグを外しました");
  }

  function scheduleTagSave() {
    if (tagTimer) clearTimeout(tagTimer);
    tagTimer = setTimeout(() => saveTagsToServer().catch(e => say(e.message)), 500);
  }

  // ------------------------------------------------------------ base
  async function renderBase() {
    // wizard_public(full) は未設定スロットも source="unset" で返すため、
    // キーの有無ではなく「実際に入力されたか」で判定する。
    const locked = !!(Object.values(draft.slots || {}).some(s => s && s.source && s.source !== "unset") ||
                      (draft.questions || []).length || draft.compose);
    const hasBase = !!draft.base_sid;
    const title = draft.base_title || "現在のチャンネル";
    if (locked) {
      setBody(`
        <div class="wizard-card">
          <h3>引き継ぎ元</h3>
          <p class="wizard-lead">設定を入力した後は、引き継ぎ元を変更できません。</p>
          <p class="wizard-note">現在: ${draft.inherit_base ? `「${E(title)}」の設定を引き継ぐ` : "引き継がない（初期設定）"}</p>
        </div>`);
      setFoot(`<button type="button" class="wizard-btn primary" data-act="base-continue">この設定で続ける</button>`);
      return;
    }
    const current = draft.inherit_base ? "引き継ぐ" : "引き継がない";
    setBody(`
      <div class="wizard-card">
        <h3>引き継ぎ元</h3>
        ${hasBase ? `<p class="wizard-lead">チャンネル「${E(title)}」の設定を引き継ぎますか？</p>`
                  : `<p class="wizard-lead">引き継ぎ元のチャンネルがありません。初期設定で作ります。</p>`}
        <table class="wizard-inherit">
          <tr><th>引き継ぐ</th><td>画像設定 / 参照ファイル / プロジェクト / ユーザー名 / 本文AI設定</td></tr>
          <tr><th>リセット</th><td>ストーリー / 進捗（メモ・ターン・日数） / IDE会話</td></tr>
        </table>
        <p class="wizard-note">引き継ぐのは保存済みの設定です。未保存の変更は反映されません。</p>
        ${hasBase ? `<p class="wizard-note">「引き継がない」を選ぶと、ユーザー名は「あなた」、プロジェクトは空、画像設定・参照ファイルは初期値になります。現在: ${current}</p>` : ""}
        <div id="wizBaseMsg" class="wizard-msg"></div>
      </div>`);
    setFoot(hasBase
      ? `<button type="button" class="wizard-btn primary" data-act="base-inherit">このチャンネルから引き継ぐ</button>
         <button type="button" class="wizard-btn ghost" data-act="base-empty">引き継がない（初期設定）</button>`
      : `<button type="button" class="wizard-btn primary" data-act="base-empty">初期設定で作る</button>`);
  }

  async function saveBase(inherit) {
    if (inherit && !draft.base_sid) { say("引き継ぎ元のチャンネルがありません"); return; }
    const r = await apiCall("/api/wizard/base", {
      draft_id: draft.id, rev: draft.rev,
      base_sid: draft.base_sid, inherit,
    });
    if (!r || r.error) { say(r.error || "保存できません"); return; }
    draft = r.draft;
    show(((draft.model && draft.model.model) || (wmeta && wmeta.configured)) ? "idea" : "model");
  }

  // ------------------------------------------------------------ model
  async function renderModel() {
    const cur = (draft && draft.model && draft.model.model) ? draft.model : ((wmeta && wmeta.current) || {});
    const provider = cur.provider || "google";
    const thinking = cur.thinking_level || "high";
    setBody(`
      <div class="wizard-card">
        <h3>チャンネル作成に使うAI</h3>
        <p class="muted">この設定はアプリ全体で使います。本文を書くAIとは別です。</p>
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
        <p class="wizard-note">賢いモデルを推奨します。作成AIは質問と設定生成にだけ使われ、本文AIには影響しません。</p>
        <div id="wizModelMsg" class="wizard-msg"></div>
      </div>`);

    const load = async () => {
      const p = val("#wizProvider") || "google";
      const r = await apiCall(`/api/wizard/models?provider=${encodeURIComponent(p)}`);
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

    setFoot(`
      <button type="button" class="wizard-btn" data-act="model-test">接続テスト</button>
      <button type="button" class="wizard-btn primary" data-act="model-save">保存して次へ</button>
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
    show("idea");
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
    const idea = (draft && draft.slots && draft.slots.rough_idea && draft.slots.rough_idea.value) || "";
    const examples = (wmeta && wmeta.tags && wmeta.tags.examples) || [];
    setBody(`
      <div class="wizard-card">
        <h3>やりたいことを一言で</h3>
        <textarea id="wizIdea" class="wizard-textarea" rows="4" maxlength="300"
          placeholder="例: 放課後の教室で、幼馴染と二人きり。">${E(idea)}</textarea>
        <div class="wizard-examples">${examples.map(x => `<button type="button" class="wizard-example" data-example="${E(x)}">${E(x)}</button>`).join("")}</div>
        <label class="wizard-check"><input type="checkbox" id="wizConfirm" ${(draft && draft.confirm) ? "checked" : ""}> 作成前にプレビューを確認する</label>
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
    if (confirm !== draft.confirm) {
      const s = await apiCall("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step: "idea", confirm });
      if (s && s.draft) draft = s.draft;
    }
  }

  // ------------------------------------------------------------ partner
  function partnerValues() {
    const ptBtn = $(".wizard-seg-btn.on", $("#wizPartnerType"));
    const pt = ptBtn ? ptBtn.dataset.pt : "original";
    // 空欄は「おまかせ」。player_name/project は元チャンネルの値を引き継ぐ。
    const textSlot = (sel) => {
      const v = val(sel);
      return { value: v, source: v ? "user" : "auto" };
    };
    return {
      partner_type: { value: pt, source: pt === "auto" ? "auto" : "user" },
      work: textSlot("#wizWork"),
      partner_name: textSlot("#wizPartnerName"),
      player_type: { value: val("#wizPlayerType"), source: "user" },
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
    const s = (k) => (draft && draft.slots && draft.slots[k] && draft.slots[k].value) || "";
    const pt = s("partner_type") || "original";
    let presets = [];
    try {
      const r = await apiCall("/api/char-presets");
      presets = Array.isArray(r) ? r : (r && r.presets) || [];
    } catch (_) {}
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
            ${presets.map(p => `<option value="${E(p.id || p.name || "")}" ${String((draft && draft.partner_preset_id) || "") === String(p.id || "") ? "selected" : ""}>${E(p.name || p.id || "")}</option>`).join("")}
          </select>
        </label>
        <h3>あなたは？</h3>
        <label class="wizard-label">立場
          <select id="wizPlayerType" class="wizard-input">
            ${["original", "copyright", "existing"].map(v => `<option value="${v}" ${s("player_type") === v ? "selected" : ""}>${v === "original" ? "オリジナル" : v === "copyright" ? "版権キャラ" : "既存の自分"}</option>`).join("")}
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
    $$("#wizPartnerType .wizard-seg-btn").forEach(b => b.onclick = () => {
      $$("#wizPartnerType .wizard-seg-btn").forEach(x => x.classList.toggle("on", x === b));
    });
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
      return;
    }
    await saveSlots(partnerValues(), "partner");
    const preset = val("#wizPreset");
    // 空（使わない）も送る。送らないと一度選んだプリセットを解除できない。
    const r = await apiCall("/api/wizard/answer", {
      draft_id: draft.id, rev: draft.rev, question_id: "fixed_preset",
      option_ids: preset ? [preset] : [], step: "partner",
    });
    if (r && r.draft) draft = r.draft;
  }

  // ------------------------------------------------------------ tags
  function renderTags() {
    const tags = (wmeta && wmeta.tags && wmeta.tags.tags) || [];
    const axes = (wmeta && wmeta.tags && wmeta.tags.axes) || {};
    const selected = new Set((draft && draft.selected_tags) || []);
    const ng = new Set((draft && draft.selected_ng) || []);
    // 常に今の下書きから作り直す。前のセッションの選択が残ると「引き継がない」でもタグが漏れる。
    pendingTags = {
      tags: Array.from(selected),
      ng: Array.from(ng),
      custom: ((draft && draft.custom_ng) || []).join("、"),
    };    const groups = [
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
    if (tagTimer) clearTimeout(tagTimer);
    const custom = val("#wizCustomNg");
    pendingTags = pendingTags || {
      tags: (draft && draft.selected_tags) || [],
      ng: (draft && draft.selected_ng) || [],
      custom,
    };
    pendingTags.custom = custom;
    await saveTagsToServer();
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
        jobId = draft.last_job;
        jobKind = "questions";
        renderQuestionsLoading();
        pollJob();
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
    const c = (draft && draft.compose && draft.compose.setting) || {};
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
      jobId = draft.last_job;
      jobKind = "compose";
      pollJob();
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

  function chromeFor(name) {
    screen = name;
    updateChrome();
    persistStep(name);
  }

  async function startJob(kind) {
    if (busy) return;
    busy = true;
    try {
      if (jobTimer) clearTimeout(jobTimer);
      const r = await apiCall(`/api/wizard/${kind}`, { draft_id: draft.id, rev: draft.rev });
      if (!r || r.error) throw new Error(r.error || "開始できません");
      if (r.draft) draft = r.draft;
      jobId = r.job_id;
      jobKind = kind;
      if (kind === "compose") show("creating");
      else show("questions");
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
    if (!jobId) return;
    try {
      const j = await apiCall(`/api/wizard/job/${jobId}`);
      if (!j || (j.ok === false && !j.status)) throw new Error((j && j.error) || "ジョブが見つかりません");
      const msg = $("#wizJobMsg");
      if (msg) msg.textContent = j.progress || "";
      if (j.status === "done") {
        jobId = "";
        if (j.result && j.result.draft) draft = j.result.draft;
        if (jobKind === "questions") {
          show("questions");
        } else {
          if (autoCreateOnce) { autoCreateOnce = false; doCreate(); }
          else if (draft && draft.confirm) show("preview");
          else doCreate();
        }
        return;
      }
      if (j.status === "error") {
        jobId = "";
        chromeFor(jobKind === "compose" ? "creating" : "questions");
        const canTemplate = jobKind === "compose";
        setBody(errBox(j.error || "生成に失敗しました",
          `<button type="button" class="wizard-btn" data-act="retry-${jobKind}">再試行</button>
           ${canTemplate ? `<button type="button" class="wizard-btn ghost" data-act="use-template">AIなしで作る</button>` : ""}`));
        return;
      }
      jobTimer = setTimeout(pollJob, 1500);
    } catch (e) {
      jobTimer = setTimeout(pollJob, 3000);
    }
  }

  async function resumeJob(jid) {
    jobId = jid;
    jobKind = (draft && draft.compose && draft.compose.setting) ? "compose" : "questions";
    const j = await apiCall(`/api/wizard/job/${jid}`).catch(() => null);
    if (!j || !j.status) {
      jobId = "";
      return;
    }
    jobKind = j.kind || jobKind;
    if (j.status === "done") {
      jobId = "";
      if (j.result && j.result.draft) draft = j.result.draft;
      if ((j.kind || jobKind) === "compose" && (autoCreateOnce || (draft && !draft.confirm))) {
        autoCreateOnce = false;
        return doCreate();
      }
      show(entryScreen());
      return;
    }
    if (j.status === "error") {
      jobId = "";
      chromeFor((j.kind || jobKind) === "compose" ? "creating" : "questions");
      const canTemplate = (j.kind || jobKind) === "compose";
      setBody(errBox(j.error || "生成に失敗しました",
        `<button type="button" class="wizard-btn" data-act="retry-${j.kind || jobKind}">再試行</button>
         ${canTemplate ? `<button type="button" class="wizard-btn ghost" data-act="use-template">AIなしで作る</button>` : ""}`));
      return;
    }
    show(j.kind === "compose" ? "creating" : "questions");
    pollJob();
  }

  async function cancelJob() {
    if (!jobId) return;
    await apiCall("/api/wizard/job_cancel", { job_id: jobId }).catch(() => {});
    jobId = "";
    autoCreateOnce = false;
    stopPoll();
  }

  function startCreateChain(withQuestions) {
    if (!draft || !draft.model || draft.model.provider === "none" || draft.model.ai_enabled === false) {
      return doCreate({}, true);
    }
    if (draft.compose && !draft.compose_stale) {
      if (autoCreateOnce) { autoCreateOnce = false; return doCreate(); }
      return draft.confirm ? show("preview") : doCreate();
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
              // 会話一覧とセッション一覧の両方を、取得完了まで待って同期する
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
        show("done");
      }
    } catch (e) {
      // 入力フォームを壊さず、プレビューを残したまま再試行できるようにする
      say(e.message);
      if (draft && draft.compose) {
        show("preview");
        setFoot(`<button type="button" class="wizard-btn" data-act="create">再試行</button>`);
      } else {
        show("tags");
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
    if (ex) { const t = $("#wizIdea"); if (t) t.value = ex.dataset.example; return; }
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
        if (a === "base-inherit") return saveBase(true);
        if (a === "base-empty") return saveBase(false);
        if (a === "base-continue") return show("idea");
        if (a === "model-test") return testModel();
        if (a === "model-save") return saveModel(false);
        if (a === "model-skip") return saveModel(true);
        if (a === "idea-next") { await saveIdea(false); return show("partner"); }
        if (a === "idea-auto") {
          await saveIdea(true);
          // confirm を下書きに書き込むと、キャンセル後も自動作成のままになる。1回だけのローカル印にする。
          autoCreateOnce = true;
          return startCreateChain(false);
        }
        if (a === "empty") { close(true); if (window.callLegacyNewSession) window.callLegacyNewSession(); return; }
        if (a === "partner-next") { await savePartner(false); return show("tags"); }
        if (a === "partner-skip") { await savePartner(true); return show("tags"); }
        if (a === "tags-next") return saveTagsAndGo("next");
        if (a === "tags-finish") return saveTagsAndGo("finish");
        if (a === "q-answer") return answerQuestion(false);
        if (a === "q-auto") return answerQuestion(true);
        if (a === "q-finish") return startCreateChain(false);
        if (a === "create") {
          const ov = collectOverrides();
          await savePreview().catch(() => {});
          return doCreate(ov);
        }
        if (a === "recompose") {
          const r = await apiCall("/api/wizard/model", { draft_id: draft.id, rev: draft.rev, ...(draft.model || {}) });
          if (r && r.draft) draft = r.draft;
          return startJob("compose");
        }
        if (a === "cancel-job") {
          await cancelJob();
          return show((draft && draft.compose) ? "preview" : "tags");
        }
        if (a === "open-created") { close(true); return activateCreatedSession(draft.created_sid); }
        if (a === "retry-questions") return startJob("questions");
        if (a === "retry-compose") return startJob("compose");
        if (a === "use-template") return useTemplate();
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
      // 画面内の wmeta も更新する（次に開いたとき古い configured を掴まない）
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
      if (busy || jobId || screen === "creating" || screen === "done") return;
      show("model");
    };

    // body と foot の両方を覆う
    const modalEl = $(".wizard-modal", root());
    if (modalEl) modalEl.addEventListener("click", onBodyClick);

    const bEl = $("#wizardBody");
    if (bEl) {
      bEl.addEventListener("input", (e) => {
        if (e.target && e.target.id === "wizFree") {
          const ft = foot();
          if (ft) ft.dataset.picked = "";
          $$(".wizard-option").forEach(x => x.classList.remove("on"));
        }
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
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close(false);
      } else if (e.key === "Tab") {
        const m = $(".wizard-modal", r);
        if (!m) return;
        const focusables = Array.from(m.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter(el => el.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
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
      open().then(() => show("model")).catch(() => {});
    } else {
      show("model");
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.RPWizard = { open, close, showModel };
})();
