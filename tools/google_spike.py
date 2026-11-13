"""
Google AI Studio (Gemini API) 最小スパイク.

目的:
  1. APIキーをローカルで保存する（チャットには出さない）
  2. models.list で利用可能モデルを確認する
  3. 既存チャンネルの最新ユーザー発言（または入力した本文）を1回だけ送る
  4. 返答JSONを outbox に書き、動いている server.py に取り込ませる

起動:
  python tools/google_spike.py
  → http://127.0.0.1:8765/ を開く

このツールは本番の server.py を変更しません。テスト用に元チャンネルを複製し、
複製先（[Google Spike] ...）へ応答を入れます。元チャンネルは変更しません。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import google_ai

DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
SESS_DIR = os.path.join(DATA, "sessions")
OUTBOX = os.path.join(ROOT, "outbox")
RESULT_LOG = os.path.join(DATA, "google_spike_results.jsonl")
BASE = "https://generativelanguage.googleapis.com/v1beta"
SAFETY_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_JAILBREAK",
]
EXCLUDE_MODEL_PARTS = (
    "-image", "embedding", "tts", "-live", "transcribe", "robotics", "lyria",
    "omni", "deep-research", "computer-use", "nano-banana",
)

HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Google AI Studio Spike</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background:#15171c; color:#e7e9ee; margin:0; padding:24px; }
  .wrap { max-width: 980px; margin: 0 auto; }
  h1 { font-size:20px; margin:0 0 6px; }
  .muted { color:#9aa3b2; font-size:13px; }
  .box { background:#1e222a; border:1px solid #303643; border-radius:12px; padding:16px; margin:14px 0; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  label { display:block; font-size:13px; color:#b9c1cf; margin:8px 0 4px; }
  input, select, textarea, button { font:inherit; }
  input[type=password], input[type=text], select, textarea { width:100%; box-sizing:border-box; background:#11141a; color:#e7e9ee; border:1px solid #3a4252; border-radius:8px; padding:10px; }
  textarea { min-height:70px; resize:vertical; }
  button { background:#3b6cf6; color:#fff; border:0; border-radius:8px; padding:10px 14px; cursor:pointer; }
  button.secondary { background:#343b48; }
  button.danger { background:#8b2e3a; }
  button:disabled { opacity:.5; cursor:default; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { border-bottom:1px solid #303643; padding:7px 6px; text-align:left; vertical-align:top; }
  th { color:#9aa3b2; font-weight:500; }
  code { background:#11141a; padding:2px 5px; border-radius:4px; }
  .ok { color:#67d391; } .warn { color:#f0c36a; } .err { color:#ff7d8a; }
  .pre { white-space:pre-wrap; word-break:break-word; background:#11141a; border:1px solid #303643; border-radius:8px; padding:10px; font-family:ui-monospace,monospace; font-size:12px; max-height:320px; overflow:auto; }
  a { color:#8aa8ff; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Google AI Studio Spike</h1>
  <div class="muted">APIキーはサーバー側の <code>.env</code> に保存されます。画面には末尾4文字しか出しません。元チャンネルは変更せず、テスト用の複製チャンネルに応答を入れます。</div>

  <div class="box">
    <div class="row" style="justify-content:space-between">
      <strong>1. APIキー</strong>
      <span id="keyStatus" class="muted">確認中…</span>
    </div>
    <label for="key">Google AI Studio の APIキー</label>
    <div class="row">
      <input id="key" type="password" autocomplete="off" placeholder="AIza... / AQ... を貼り付け" style="flex:1; min-width:260px">
      <button id="saveKey">保存してモデル一覧を取得</button>
      <button id="delKey" class="danger secondary">削除</button>
    </div>
    <div id="keyMsg" class="muted" style="margin-top:8px"></div>
  </div>

  <div class="box">
    <div class="row" style="justify-content:space-between">
      <strong>2. モデル</strong>
      <button id="reloadModels" class="secondary">一覧を再取得</button>
    </div>
    <label for="model">モデル</label>
    <select id="model"><option value="">APIキーを保存してください</option></select>
    <label for="manualModel">モデルIDを直接入力（一覧にない場合）</label>
    <input id="manualModel" type="text" placeholder="例: gemini-3.6-flash">
    <div id="modelMsg" class="muted" style="margin-top:8px"></div>
  </div>

  <div class="box">
    <strong>3. テスト送信</strong>
    <div class="row" style="margin-top:8px">
      <label style="margin:0"><input type="checkbox" id="safeContext"> 合成SFWコンテキストを使う（元チャンネル・履歴を送らない）</label>
    </div>
    <label for="sid">元チャンネル（この内容を複製してテストします）</label>
    <select id="sid"></select>
    <label for="mode">送る本文</label>
    <select id="mode">
      <option value="latest">そのチャンネルの最新ユーザー発言を使う</option>
      <option value="custom">下の欄の本文を使う</option>
    </select>
    <textarea id="customText" placeholder="「下の欄の本文を使う」を選んだときだけ送信します。ここに入力した内容は結果には表示しません。" disabled></textarea>
    <label for="context">コンテキスト</label>
    <select id="context">
      <option value="minimal">最小（概要・指示・キャラ・メモ＋直近16件）</option>
      <option value="full">設定込み（執筆スタイルも含む）</option>
    </select>
    <div class="row" style="margin-top:10px">
      <label style="margin:0"><input type="checkbox" id="fetchMode"> プロンプト取り寄せ（function calling）を試す</label>
    </div>
    <div class="row" style="margin-top:10px">
      <label style="margin:0"><input type="checkbox" id="safetyOff" checked> 安全設定を OFF にする（規約上の禁止内容は解除できません）</label>
    </div>
    <div class="row" style="margin-top:10px">
      <label style="margin:0">連続回数
        <select id="runs" style="width:auto"><option>1</option><option>2</option><option>3</option></select>
      </label>
      <button id="run">テスト送信</button>
      <span id="runMsg" class="muted"></span>
    </div>
    <div id="result" class="pre" style="margin-top:12px; display:none"></div>
    <div class="row" style="margin-top:10px">
      <button id="showText" class="secondary" style="display:none">取り込まれた本文を表示</button>
      <span id="textMsg" class="muted"></span>
    </div>
    <div id="textView" class="pre" style="margin-top:8px; display:none"></div>
  </div>
</div>
<script>
const $ = (id) => document.getElementById(id);
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
  return d;
}
function status(text, cls) { $("keyStatus").className = cls || "muted"; $("keyStatus").textContent = text; }
let appPort = 8200;
let lastSpikeSid = "";
async function loadStatus() {
  try {
    const d = await api("/api/status");
    if (d.app_port) appPort = d.app_port;
    if (d.configured) status("設定済み（…" + (d.hint || "") + " / " + (d.source || "") + "）", "ok");
    else status("未設定", "warn");
    await loadSessions();
    if (d.configured) await loadModels();
  } catch (e) { status(e.message, "err"); }
}
async function loadSessions() {
  const d = await api("/api/sessions");
  const sel = $("sid");
  sel.innerHTML = (d.sessions || []).map(s => `<option value="${esc(s.id)}">${esc(s.title)}（${esc(s.updated || "")} / 最新ユーザー${s.latest_user_len || 0}字）</option>`).join("");
}
function renderModels(d) {
  const sel = $("model");
  const rows = d.models || [];
  sel.innerHTML = rows.map(m => `<option value="${esc(m.id)}">${esc(m.label || m.id)}${m.outputTokenLimit ? "（出力上限 " + m.outputTokenLimit + "）" : ""}</option>`).join("");
  const prefer = rows.find(m => /gemini-4-hydrogen/i.test(m.id)) || rows.find(m => /gemini-3\.8-flash/i.test(m.id)) || rows.find(m => /gemini-flash-latest/i.test(m.id)) || rows.find(m => /gemini-3\.6-flash/i.test(m.id)) || rows.find(m => /gemini-3\.5-flash/i.test(m.id)) || rows[0];
  if (prefer) sel.value = prefer.id;
  $("modelMsg").innerHTML = d.error ? `<span class="err">${esc(d.error)}</span>` : `${rows.length} 件。残量％はGoogle APIから取得できません。`;
}
async function loadModels() {
  try { const d = await api("/api/models"); renderModels(d); } catch (e) { $("modelMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
$("saveKey").onclick = async () => {
  const key = $("key").value.trim();
  if (!key) return alert("APIキーを入れてください");
  $("saveKey").disabled = true;
  try {
    const d = await api("/api/key", {key});
    $("key").value = "";
    status("設定済み（…" + (d.hint || "") + " / " + (d.source || "") + "）", "ok");
    $("keyMsg").textContent = d.error ? ("キーは保存しましたが、モデル一覧の取得に失敗しました: " + d.error) : "キーを保存しました。";
    if (d.models) renderModels(d); else await loadModels();
  } catch (e) { $("keyMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  $("saveKey").disabled = false;
};
$("delKey").onclick = async () => {
  if (!confirm("保存したAPIキーを削除しますか？")) return;
  try { await api("/api/key/delete", {}); status("未設定", "warn"); $("model").innerHTML = '<option value="">APIキーを保存してください</option>'; } catch (e) { alert(e.message); }
};
$("reloadModels").onclick = loadModels;
$("mode").onchange = () => { $("customText").disabled = $("mode").value !== "custom"; };
$("safeContext").onchange = () => {
  $("sid").disabled = $("safeContext").checked;
  if ($("safeContext").checked) {
    $("mode").value = "custom";
    $("customText").disabled = false;
    if (!$("customText").value.trim()) $("customText").value = "こんにちは。短く返事をして。";
  }
};
$("run").onclick = async () => {
  const model = $("manualModel").value.trim() || $("model").value;
  if (!model) return alert("モデルを選んでください");
  if ($("mode").value === "custom" && !$("customText").value.trim()) return alert("本文を入れてください");
  $("run").disabled = true;
  $("runMsg").textContent = "送信中…";
  $("result").style.display = "none";
  try {
    const d = await api("/api/run", {
      sid: $("sid").value, model,
      mode: $("mode").value, text: $("customText").value,
      safety_off: $("safetyOff").checked, runs: Number($("runs").value || 1),
      context: $("context").value,
      safe_context: $("safeContext").checked,
      fetch: $("fetchMode").checked,
    });
    $("result").style.display = "block";
    $("result").textContent = JSON.stringify(d, null, 2);
    lastSpikeSid = d.spike_sid || "";
    $("showText").style.display = lastSpikeSid ? "inline-block" : "none";
    $("textView").style.display = "none";
    $("textView").textContent = "";
    const last = (d.results || [])[d.results.length - 1] || {};
    if (d.spike_sid) $("runMsg").innerHTML = `<a href="http://127.0.0.1:${appPort}/?sid=${encodeURIComponent(d.spike_sid)}" target="_blank">テスト用チャンネルを開く</a>`;
    if (last.fetch_mode) $("runMsg").innerHTML += ` <span class="${last.fetch_called ? "ok" : "err"}">fetch ${last.fetch_called ? "呼び出し成功" : "未呼び出し"}</span>`;
    if (last.ok) $("runMsg").innerHTML += ` <span class="ok">本文 ${last.text_len}字 / 取り込み ${last.imported ? "成功" : "未確認"}</span>`;
    else $("runMsg").innerHTML += ` <span class="err">${esc(last.error || "失敗")}</span>`;
  } catch (e) { $("runMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  $("run").disabled = false;
};
$("showText").onclick = async () => {
  if (!lastSpikeSid) return;
  $("textMsg").textContent = "読み込み中…";
  try {
    const d = await api("/api/session_text?sid=" + encodeURIComponent(lastSpikeSid));
    $("textView").style.display = "block";
    $("textView").textContent = d.text || "（本文がありません）";
    $("textMsg").textContent = d.text ? `${d.text.length}文字` : "";
  } catch (e) { $("textMsg").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
};
loadStatus();
</script>
</body>
</html>
"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_key():
    return google_ai.load_key()


def key_hint(key=None):
    return google_ai.key_hint(key)


def gemini_request(model, payload, key, timeout=180):
    url = f"{BASE}/models/{urllib.parse.quote(model, safe='')}:generateContent"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw[:2000]}
        return e.code, data


def list_models(key, timeout=30):
    url = f"{BASE}/models?pageSize=1000"
    req = urllib.request.Request(url, headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    models = []
    for m in data.get("models") or []:
        name = str(m.get("name") or "")
        mid = name.split("/", 1)[-1] if name.startswith("models/") else name
        if not mid:
            continue
        methods = [str(x) for x in (m.get("supportedGenerationMethods") or [])]
        if "generateContent" not in methods:
            continue
        low = mid.lower()
        if any(p in low for p in EXCLUDE_MODEL_PARTS):
            continue
        models.append({
            "id": mid,
            "label": m.get("displayName") or mid,
            "inputTokenLimit": m.get("inputTokenLimit"),
            "outputTokenLimit": m.get("outputTokenLimit"),
            "thinking": bool(m.get("thinking")),
        })
    models.sort(key=lambda x: x["id"])
    return models


def session_title(st):
    return (st.get("title") or "無題").strip()


def session_list():
    rows = []
    try:
        names = [n for n in os.listdir(SESS_DIR) if n.endswith(".json")]
    except OSError:
        return rows
    for name in names:
        sid = name[:-5]
        s = read_json(os.path.join(SESS_DIR, name), {})
        if not isinstance(s, dict):
            continue
        st = s.get("setting") or {}
        msgs = s.get("messages") or []
        latest = next((m for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "user"), None)
        rows.append({
            "id": sid,
            "title": session_title(st),
            "updated": s.get("updated") or "",
            "latest_user_len": len((latest or {}).get("text") or ""),
        })
    rows.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return rows[:200]


def latest_user_text(s):
    msgs = s.get("messages") or []
    m = next((x for x in reversed(msgs) if isinstance(x, dict) and x.get("role") == "user" and (x.get("text") or "").strip()), None)
    if not m:
        return ""
    return (m.get("text") or "").strip()


def clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def build_prompt(s, current_text, context_mode="minimal"):
    st = s.get("setting") or {}
    ch = st.get("character") or {}
    msgs = s.get("messages") or []
    lines = []
    lines.append("あなたは日本語ロールプレイの本文生成器です。")
    lines.append("与えられた設定・会話・ユーザーの最後の発言に沿って、AIキャラクターとして次の本文を書いてください。")
    lines.append("出力は必ず次のJSONだけにしてください。説明、前置き、コードフェンスは禁止です。")
    lines.append('{"text":"本文"}')
    lines.append("")

    def add(label, value):
        value = str(value or "").strip()
        if value:
            lines.append(f"--- {label} ---")
            lines.append(value)
            lines.append("")

    add("チャンネル概要", st.get("summary"))
    add("指示", st.get("instructions"))
    chara = []
    for k, label in (("name", "名前"), ("age", "年齢"), ("speech", "口調"), ("profile", "設定")):
        if (ch.get(k) or "").strip():
            chara.append(f"{label}: {ch[k]}")
    add("キャラクター", "\n".join(chara))
    add("舞台・状況", st.get("scenario"))
    add("AIメモ", st.get("memo"))

    if context_mode == "full":
        try:
            style_path = os.path.join(DATA, "writing_style.md")
            if os.path.isfile(style_path):
                with open(style_path, encoding="utf-8-sig") as f:
                    add("執筆スタイル", clip(f.read(), 12000))
        except OSError:
            pass

    history = []
    for m in msgs[-16:]:
        if not isinstance(m, dict):
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        who = "ユーザー" if m.get("role") == "user" else "AI"
        history.append(f"[{who}]\n{clip(text, 6000)}")
    if history:
        hist_text = "\n\n".join(history)
        add("直近の会話", clip(hist_text, 24000 if context_mode == "minimal" else 60000))

    add("今回のユーザー発言（これに応答する）", current_text)
    return "\n".join(lines).strip()


def extract_text_from_response(data):
    """GeminiのGenerateContentResponseから本文テキストとメタ情報を取る。thoughtは無視。"""
    meta = {
        "finish_reason": None,
        "block_reason": None,
        "safety_ratings": [],
        "usage": {},
        "prompt_feedback": {},
        "candidates": 0,
    }
    if not isinstance(data, dict):
        return "", meta
    pf = data.get("promptFeedback")
    if isinstance(pf, dict):
        meta["prompt_feedback"] = pf
        meta["block_reason"] = pf.get("blockReason")
        if isinstance(pf.get("safetyRatings"), list):
            meta["safety_ratings"] = pf["safetyRatings"]
    um = data.get("usageMetadata")
    if isinstance(um, dict):
        meta["usage"] = um
    cands = data.get("candidates")
    if isinstance(cands, list):
        meta["candidates"] = len(cands)
        if cands and isinstance(cands[0], dict):
            c0 = cands[0]
            meta["finish_reason"] = c0.get("finishReason")
            if isinstance(c0.get("safetyRatings"), list):
                meta["safety_ratings"] = c0["safetyRatings"]
            content = c0.get("content") or {}
            parts = content.get("parts") or []
            texts = []
            for p in parts:
                if not isinstance(p, dict) or p.get("thought"):
                    continue
                t = p.get("text")
                if isinstance(t, str) and t:
                    texts.append(t)
            if texts:
                return "".join(texts), meta
    return "", meta


def extract_function_call(data, name="fetch_prompt"):
    """最初の候補から functionCall と、そのまま返送する model content を取り出す。"""
    if not isinstance(data, dict):
        return None, None
    cands = data.get("candidates")
    if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict):
        return None, None
    content = cands[0].get("content") or {}
    for p in content.get("parts") or []:
        if not isinstance(p, dict):
            continue
        fc = p.get("functionCall")
        if isinstance(fc, dict) and fc.get("name") == name:
            return content, fc
    return content, None


def parse_json_text(raw):
    raw = (raw or "").strip()
    if not raw:
        return None, False
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw, strict=False)
        if isinstance(obj, dict):
            return obj, True
    except Exception:
        pass
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(raw[i:j + 1], strict=False)
            if isinstance(obj, dict):
                return obj, True
        except Exception:
            pass
    return None, False


def outbox_write(sid, reply_to, text):
    os.makedirs(OUTBOX, exist_ok=True)
    name = f"{sid}_spike_{reply_to}_{int(time.time() * 1000)}.json"
    path = os.path.join(OUTBOX, name)
    tmp = path + ".tmp"
    rep = {
        "session": sid,
        "reply_to": reply_to,
        "text": text,
        "memories": [],
        "type": "reply",
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def safe_source_session():
    """外部チャンネルの内容を一切送らない合成SFWテスト用セッション。"""
    return {
        "id": "safe_source",
        "setting": {
            "title": "合成SFWテスト",
            "summary": "日常会話の短いロールプレイ。健全な内容だけを扱う。",
            "instructions": "短く、自然に返事をする。",
            "character": {"name": "アシスタント", "age": "", "speech": "丁寧で簡潔", "profile": "日常会話を手伝うアシスタント"},
            "scenario": "静かな部屋で軽い雑談をしている。",
            "memo": "",
            "image_mode": "off",
            "suggest_choices": False,
            "rp_state": False,
        },
        "messages": [],
    }


def make_spike_session(src, current_text, title_prefix="[Google Spike] "):
    sid = "gs" + uuid.uuid4().hex[:10]
    st = dict(src.get("setting") or {})
    st.update({
        "title": title_prefix + session_title(st),
        "ide_session": "spike-no-ag",   # --ide なしの wait_input.py に拾わせない
        "ide_title": "Google Spike (AG disabled)",
        "ide_auto": False,
        "choices_ide_session": "",
        "choices_ide_title": "",
        "choices_ide_auto": False,
        "choices_ide_manual_off": True,
        "rotation_skip_until": 10 ** 9,
        "image_mode": "off",
        "suggest_choices": False,
        "rp_state": False,
        "agents_md": False,
        "rules_every": False,
        "rag_collections": [],
        "input_mode": "diff",
    })
    mid = "spk" + uuid.uuid4().hex[:10]
    msg = {
        "id": mid,
        "role": "user",
        "text": current_text,
        "ts": now(),
        "status": "pending",
        "spike": True,
    }
    out = {
        "id": sid,
        "rev": 0,
        "updated": now(),
        "setting": st,
        "messages": list(src.get("messages") or []) + [msg],
    }
    write_json_atomic(os.path.join(SESS_DIR, sid + ".json"), out)
    return sid, mid


def clone_session_for_spike(src_sid, current_text):
    src_path = os.path.join(SESS_DIR, src_sid + ".json")
    src = read_json(src_path, None)
    if not isinstance(src, dict):
        raise ValueError("元チャンネルを読めません")
    return make_spike_session(src, current_text)


def mark_spike_error(sid, reply_to, error):
    """失敗したテスト発言を pending のまま残さない（チャンネルが応答待ちに見え続けるのを防ぐ）"""
    path = os.path.join(SESS_DIR, sid + ".json")
    s = read_json(path, None)
    if not isinstance(s, dict):
        return
    for m in s.get("messages") or []:
        if isinstance(m, dict) and m.get("id") == reply_to:
            m["status"] = "error"
            m["error"] = str(error or "Google API test failed")
            break
    s["updated"] = now()
    write_json_atomic(path, s)


def wait_for_import(sid, reply_to, timeout=20):
    path = os.path.join(SESS_DIR, sid + ".json")
    start = time.time()
    while time.time() - start < timeout:
        s = read_json(path, {})
        msgs = s.get("messages") if isinstance(s, dict) else []
        if isinstance(msgs, list):
            idx = next((i for i, m in enumerate(msgs) if isinstance(m, dict) and m.get("id") == reply_to), -1)
            for m in msgs[idx + 1:]:
                if isinstance(m, dict) and m.get("role") == "assistant" and (m.get("text") or "").strip():
                    return True, len(m.get("text") or "")
        time.sleep(0.3)
    return False, 0


def run_one(src_sid, model, mode, custom_text, safety_off, context_mode, fetch_mode=False, safe_context=False):
    key, _ = load_key()
    if not key:
        return {"ok": False, "error": "APIキーが未設定です", "fetch_mode": bool(fetch_mode), "safe_context": bool(safe_context)}

    if safe_context:
        src = safe_source_session()
        text = (custom_text or "").strip() or "こんにちは。短く返事をして。"
        spike_sid, reply_to = make_spike_session(src, text, "[Google Spike SFW] ")
    else:
        src = read_json(os.path.join(SESS_DIR, src_sid + ".json"), None)
        if not isinstance(src, dict):
            return {"ok": False, "error": "元チャンネルを読めません", "fetch_mode": bool(fetch_mode), "safe_context": False}
        text = (custom_text or "").strip() if mode == "custom" else latest_user_text(src)
        if not text:
            return {"ok": False, "error": "送る本文が空です", "fetch_mode": bool(fetch_mode), "safe_context": False}
        spike_sid, reply_to = clone_session_for_spike(src_sid, text)

    prompt = build_prompt(src, text, context_mode)
    system_text = "あなたは日本語ロールプレイの本文生成器です。必ずJSONのみを返します。"
    safety = [{"category": c, "threshold": "OFF"} for c in SAFETY_CATEGORIES] if safety_off else None

    first_status = None
    second_status = None
    first_elapsed = 0
    second_elapsed = 0
    first_usage = {}
    fetch_called = False
    status = None
    raw_text = ""
    meta = {}
    error = None

    if fetch_mode:
        # 1回目: 本文は書かせず、fetch_prompt の呼び出しだけを強制する。
        first_payload = {
            "systemInstruction": {"parts": [{"text": (
                "あなたは日本語ロールプレイの本文生成器です。"
                "今回の応答を書く前に、必ず fetch_prompt を呼び出してコンテキストを取得してください。"
                "取得するまで本文を書いてはいけません。"
            )}]},
            "contents": [{"role": "user", "parts": [{"text": "fetch_prompt を呼び出してコンテキストを取得してください。"}]}],
            "tools": [{"functionDeclarations": [{
                "name": "fetch_prompt",
                "description": "このチャンネルの設定・会話・今回のユーザー発言を取得する。引数はない。",
            }]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["fetch_prompt"]}},
            "generationConfig": {"maxOutputTokens": 256},
        }
        if safety:
            first_payload["safetySettings"] = safety
        t0 = time.time()
        first_status, first_data = gemini_request(model, first_payload, key)
        first_elapsed = int((time.time() - t0) * 1000)
        _, first_meta = extract_text_from_response(first_data if first_status == 200 else {})
        first_usage = first_meta.get("usage") or {}
        model_content, fc = extract_function_call(first_data if first_status == 200 else {}, "fetch_prompt")
        fetch_called = bool(fc)

        if first_status != 200:
            status = first_status
            meta = first_meta
            err = first_data.get("error") if isinstance(first_data, dict) else None
            if isinstance(err, dict):
                error = f"HTTP {first_status} (fetch step): {err.get('status') or ''} {err.get('message') or ''}".strip()
            else:
                error = f"HTTP {first_status} (fetch step): {str(first_data)[:500]}"
        elif first_meta.get("block_reason"):
            status = first_status
            meta = first_meta
            error = f"prompt blocked (fetch step): {first_meta['block_reason']}"
        elif not fc:
            status = first_status
            meta = first_meta
            error = "fetch step: model did not call fetch_prompt"
        else:
            # 2回目: モデルが要求した functionCall に対して、実際のコンテキストを返す。
            second_payload = {
                "systemInstruction": {"parts": [{"text": system_text}]},
                "contents": [
                    {"role": "user", "parts": [{"text": "fetch_prompt を呼び出してコンテキストを取得してください。"}]},
                    model_content,
                    {"role": "user", "parts": [{"functionResponse": {
                        "name": "fetch_prompt",
                        "response": {"context": prompt},
                    }}]},
                ],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 2048},
            }
            if safety:
                second_payload["safetySettings"] = safety
            t1 = time.time()
            second_status, second_data = gemini_request(model, second_payload, key)
            second_elapsed = int((time.time() - t1) * 1000)
            status = second_status
            raw_text, meta = extract_text_from_response(second_data if second_status == 200 else {})
            if second_status != 200:
                err = second_data.get("error") if isinstance(second_data, dict) else None
                if isinstance(err, dict):
                    error = f"HTTP {second_status} (generate step): {err.get('status') or ''} {err.get('message') or ''}".strip()
                else:
                    error = f"HTTP {second_status} (generate step): {str(second_data)[:500]}"
            elif meta.get("block_reason"):
                error = f"prompt blocked (generate step): {meta['block_reason']}"
            elif not raw_text:
                error = f"empty response (finishReason={meta.get('finish_reason')})"
    else:
        payload = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 2048},
        }
        if safety:
            payload["safetySettings"] = safety
        t0 = time.time()
        status, data = gemini_request(model, payload, key)
        first_elapsed = int((time.time() - t0) * 1000)
        raw_text, meta = extract_text_from_response(data if status == 200 else {})
        if status != 200:
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                error = f"HTTP {status}: {err.get('status') or ''} {err.get('message') or ''}".strip()
            else:
                error = f"HTTP {status}: {str(data)[:500]}"
        elif meta.get("block_reason"):
            error = f"prompt blocked: {meta['block_reason']}"
        elif not raw_text:
            error = f"empty response (finishReason={meta.get('finish_reason')})"

    obj, json_ok = parse_json_text(raw_text) if raw_text else (None, False)
    reply_text = ""
    if isinstance(obj, dict):
        reply_text = str(obj.get("text") or "").strip()
    elif raw_text and not error:
        reply_text = raw_text.strip()
    if not reply_text and not error:
        error = "応答JSONに text がありません"

    imported = False
    imported_len = 0
    outbox_name = ""
    if reply_text and not error:
        path = outbox_write(spike_sid, reply_to, reply_text)
        outbox_name = os.path.basename(path)
        imported, imported_len = wait_for_import(spike_sid, reply_to, timeout=20)
        if not imported:
            mark_spike_error(spike_sid, reply_to, "outbox は書いたが、サーバーが取り込まなかった")
    elif error:
        mark_spike_error(spike_sid, reply_to, error)

    result = {
        "ok": bool(reply_text and not error),
        "http_status": status,
        "model": model,
        "elapsed_ms": first_elapsed + second_elapsed,
        "first_elapsed_ms": first_elapsed,
        "second_elapsed_ms": second_elapsed,
        "first_http_status": first_status,
        "second_http_status": second_status,
        "fetch_mode": bool(fetch_mode),
        "fetch_called": bool(fetch_called),
        "safe_context": bool(safe_context),
        "context": context_mode,
        "safety_off": bool(safety_off),
        "finish_reason": meta.get("finish_reason"),
        "block_reason": meta.get("block_reason"),
        "safety_ratings": meta.get("safety_ratings") or [],
        "usage": meta.get("usage") or {},
        "first_usage": first_usage,
        "json_ok": json_ok,
        "text_len": len(reply_text),
        "reply_to": reply_to,
        "outbox": outbox_name,
        "imported": imported,
        "imported_text_len": imported_len,
        "spike_sid": spike_sid,
        "error": error,
    }
    try:
        with open(RESULT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in result.items() if k != "safety_ratings"}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_html(self, text):
        b = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        d = json.loads(self.rfile.read(n).decode("utf-8"))
        return d if isinstance(d, dict) else {}

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/":
            return self.send_html(HTML)
        if p == "/api/status":
            key, source = load_key()
            app_port = 8200
            try:
                srv = read_json(os.path.join(DATA, "server.json"), {})
                if isinstance(srv, dict) and srv.get("port"):
                    app_port = int(srv["port"])
            except Exception:
                pass
            return self.send_json({"configured": bool(key), "hint": key_hint(key), "source": source, "app_port": app_port})
        if p == "/api/sessions":
            return self.send_json({"sessions": session_list()})
        if p == "/api/session_text":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sid = (qs.get("sid") or [""])[0].strip()
            if not sid or not all(c.isalnum() or c in "-_" for c in sid):
                return self.send_json({"error": "チャンネルが不正です"}, 400)
            s = read_json(os.path.join(SESS_DIR, sid + ".json"), None)
            if not isinstance(s, dict):
                return self.send_json({"error": "チャンネルが見つかりません"}, 404)
            text = ""
            for m in reversed(s.get("messages") or []):
                if isinstance(m, dict) and m.get("role") == "assistant" and (m.get("text") or "").strip():
                    text = m.get("text") or ""
                    break
            return self.send_json({"ok": True, "sid": sid, "text": text, "title": session_title(s.get("setting") or {})})
        if p == "/api/models":
            key, _ = load_key()
            if not key:
                return self.send_json({"error": "APIキーが未設定です", "models": []}, 400)
            try:
                return self.send_json({"models": list_models(key), "error": None})
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                try:
                    msg = (json.loads(raw).get("error") or {}).get("message") or raw
                except Exception:
                    msg = raw
                return self.send_json({"error": f"HTTP {e.code}: {msg}", "models": []}, 200)
            except Exception as e:
                return self.send_json({"error": str(e), "models": []}, 200)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            d = self.body()
        except Exception as e:
            return self.send_json({"error": f"bad request: {e}"}, 400)
        if p == "/api/key":
            key = str(d.get("key") or "").strip()
            try:
                google_ai.save_key(key)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            _, source = google_ai.load_key()
            try:
                models = list_models(key)
                return self.send_json({"ok": True, "hint": key_hint(key), "source": source or ".env", "models": models, "error": None})
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace")
                try:
                    msg = (json.loads(raw).get("error") or {}).get("message") or raw
                except Exception:
                    msg = raw
                return self.send_json({"ok": True, "hint": key_hint(key), "source": source or ".env", "models": [], "error": f"HTTP {e.code}: {msg}"})
            except Exception as e:
                return self.send_json({"ok": True, "hint": key_hint(key), "source": source or ".env", "models": [], "error": str(e)})
        if p == "/api/key/delete":
            google_ai.delete_key()
            return self.send_json({"ok": True})
        if p == "/api/run":
            sid = str(d.get("sid") or "").strip()
            model = str(d.get("model") or "").strip()
            mode = d.get("mode") or "latest"
            text = str(d.get("text") or "")
            safety_off = bool(d.get("safety_off", True))
            context_mode = str(d.get("context") or "minimal")
            fetch_mode = bool(d.get("fetch"))
            safe_context = bool(d.get("safe_context"))
            try:
                runs = max(1, min(5, int(d.get("runs") or 1)))
            except Exception:
                runs = 1
            if not safe_context and (not sid or not all(c.isalnum() or c in "-_" for c in sid)):
                return self.send_json({"error": "チャンネルが不正です"}, 400)
            if not model or not re.fullmatch(r"[A-Za-z0-9._-]+", model):
                return self.send_json({"error": "モデルIDが不正です"}, 400)
            if mode not in ("latest", "custom"):
                mode = "latest"
            if context_mode not in ("minimal", "full"):
                context_mode = "minimal"
            if safe_context:
                mode = "custom"
                if not text.strip():
                    text = "こんにちは。短く返事をして。"
            results = []
            spike_sid = ""
            for i in range(runs):
                r = run_one(sid, model, mode, text, safety_off, context_mode, fetch_mode, safe_context)
                results.append(r)
                spike_sid = r.get("spike_sid") or spike_sid
                if i + 1 < runs and r.get("ok"):
                    time.sleep(3)  # レート制限の様子を見るため少し空ける
            return self.send_json({"ok": all(x.get("ok") for x in results), "spike_sid": spike_sid, "results": results})
        self.send_json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    os.makedirs(SESS_DIR, exist_ok=True)
    os.makedirs(OUTBOX, exist_ok=True)
    print(f"Google Spike: http://{args.host}:{args.port}/")
    print("APIキーは .env に保存されます。")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
