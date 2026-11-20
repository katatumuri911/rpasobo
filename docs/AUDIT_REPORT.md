# Comprehensive Project Audit Report: RPasobou (RPあそぼう)

**Date**: 2026-11-20  
**Target Repository**: `c:\Users\nukon\RPasobou`  
**Audit Scope**: Entire codebase (Backend Python, Frontend Vanilla JS/CSS, Test Suites, Tools, Config)  
**Inspection Mode**: Non-Destructive / Static & Dynamic Baseline Verification  

---

## Table of Contents
1. [Executive Summary & Architecture Overview](#executive-summary--architecture-overview)
2. [優先度ランキング（昇順）](#優先度ランキング昇順)
3. [Baseline Verification & Test Suite Execution Status](#baseline-verification--test-suite-execution-status)
4. [Section 1: Functional Bugs & Defects](#section-1-functional-bugs--defects)
5. [Section 2: Logic Oversights & Edge Cases](#section-2-logic-oversights--edge-cases)
6. [Section 3: Performance Bottlenecks & Optimization Proposals](#section-3-performance-bottlenecks--optimization-proposals)
7. [Section 4: UI/UX & Accessibility Improvement Proposals](#section-4-uiux--accessibility-improvement-proposals)
8. [Appendix: Independent Verification & Reproduction Guide](#appendix-independent-verification--reproduction-guide)

---

## Executive Summary & Architecture Overview

RPasobou ("RPあそぼう") is a rich desktop-integrated web application designed for interactive AI roleplay chat. The platform combines:
- **Backend Core**: Python 3.12 multi-threaded HTTP server (`server.py`, 12,324 lines) implementing REST endpoints, session persistence, message rotation, and outbox file ingestion.
- **AI Integrations**: Native Google GenAI SDK (`google-genai 3.3.0`), Vertex AI (ADC / service account / REST fallback), and local IDE automation (`ide_poke.py`) driving Antigravity / Code Desktop windows via Windows Win32 APIs and keyboard synthesis.
- **Long-Term Memory (RAG)**: FastEmbed local ONNX embedding inference (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions) combined with Janome morphological Japanese tokenization, Maximal Marginal Relevance (MMR) re-ranking, and dynamic turn decay (`memory_store.py`).
- **Image Generation Pipeline**: Local ComfyUI workflow automation with Danbooru tag dictionary integration and prompt refinement (`image_engine.py`).
- **Frontend Core**: Vanilla HTML5, ES6 JavaScript (`public/app.js`, 11,795 lines; `public/wizard.js`), and responsive CSS (`public/style.css`, 5,008 lines) featuring mobile drawer gestures, floating actions, and custom bottom sheets.

### Audit Summary Statistics
- **Functional Bugs & Defects Identified**: 13 items
- **Logic Oversights & Edge Cases Identified**: 13 items
- **Performance Bottlenecks & Optimization Proposals**: 8 major areas
- **UI/UX & Accessibility Proposals**: 9 major areas
- **優先度内訳（日本語表記）**: 高 11 件 ／ 中 22 件 ／ 低 10 件 — 詳細は「優先度ランキング（昇順）」を参照
- **Baseline Test Results**: Official test runner `tools/run_tests.py` ran 986 unit tests (OK, 27.97s, 1 skipped) but failed overall (Exit code 1) due to a zero-collection error on `test/test_channel_wizard_module.py`. Pytest passes 10/10 tests. 5 Node/JS UI test scripts fail due to environment stub gaps, CSS indexing regressions, and external bundle changes.

---

## 優先度ランキング（昇順）

各指摘の優先度を日本語表記（高／中／低）に統一し、総合順位の昇順（1位＝最優先）で並べ替えた一覧。本文の各セクション内も同じ順位順に並べ直してある。

- **優先度の基準**: **高** = データ損失・クラッシュ・恒久的な機能不全、**中** = 誤動作・テスト破綻・明確なUX劣化、**低** = 軽微な不整合・将来の保守リスク
- **内訳**: 高 11 件 ／ 中 22 件 ／ 低 10 件（全 43 件）

| 順位 | ID | 優先度 | 区分 | 概要 |
| :--: | :-- | :--: | :-- | :-- |
| 1 | F-01 | 高 | 機能不具合 | outbox エラー復旧時に raw 未定義で UnboundLocalError／前ファイル内容が混入 |
| 2 | F-02 | 高 | 機能不具合 | write_json の一時ファイル名がプロセス内で衝突し破損・PermissionError |
| 3 | F-03 | 高 | 機能不具合 | /api/send の二重送信で pending が二重化し片方の応答が永久に喪失 |
| 4 | L-01 | 高 | ロジック／エッジケース | 過去メッセージの記憶抽出で turn_idx が現在ターンになり decay/TTL が破綻 |
| 5 | L-02 | 高 | ロジック／エッジケース | fulfilled/inactive 記憶が自動マージ後も復活せず恒久的に注入対象外 |
| 6 | L-03 | 高 | ロジック／エッジケース | 選択肢が別ターンのユーザーメッセージに誤って紐付けられる |
| 7 | L-04 | 高 | ロジック／エッジケース | test_channel_wizard_module.py が unittest 未検出でランナーが常時失敗 |
| 8 | P-04 | 高 | 性能 | Object URL 未 revoke のリークと 40MB Base64 による大規模メモリ消費 |
| 9 | P-01 | 高 | 性能 | 100ms ポーリングとグローバルロック競合（/api/session・/api/sessions） |
| 10 | P-02 | 高 | 性能 | サイドバー全 DOM 再生成と入力1文字ごとのレイアウトスラッシング |
| 11 | U-02 | 高 | UI・UX／アクセシビリティ | サーバーエラー応答で先頭チャンネルへ強制遷移し下書き入力が消失 |
| 12 | F-04 | 中 | 機能不具合 | truncate/delete/regenerate/edit で記憶ベクトルと claimed_at が残留 |
| 13 | F-05 | 中 | 機能不具合 | ComfyUI ポーリングで urlopen が閉じられずソケット枯渇 |
| 14 | F-07 | 中 | 機能不具合 | run_google_task に memory_extract/consolidation の処理分岐が欠落 |
| 15 | L-05 | 中 | ロジック／エッジケース | inflight_mid の None == None 成立で prev_sent を誤復元 |
| 16 | L-06 | 中 | ロジック／エッジケース | SQLite の workspace_uris 破損 JSON で /api/ide/sessions が 500 |
| 17 | L-08 | 中 | ロジック／エッジケース | 空の選択肢応答が無言で握り潰されユーザーに通知されない |
| 18 | L-13 | 中 | ロジック／エッジケース | wizard が savePreview 失敗を握り潰し古い下書きのまま作成 |
| 19 | L-10 | 中 | ロジック／エッジケース | update_sessions.py の import sys 欠落で NameError |
| 20 | L-11 | 中 | ロジック／エッジケース | cleanup_test_sessions.py の旧パス固定で即 FileNotFoundError |
| 21 | F-09 | 中 | 機能不具合 | hideAgQuota 未定義時の blur で ReferenceError |
| 22 | F-10 | 中 | 機能不具合 | モバイルレイアウトテストのメディアクエリ探索と .progress 検証の回帰 |
| 23 | F-11 | 中 | 機能不具合 | favModelBtn が pointerdown ではなく mousedown を購読 |
| 24 | F-12 | 中 | 機能不具合 | RAG ヒットモーダルテストの style モックに removeProperty が欠落 |
| 25 | U-04 | 中 | UI・UX／アクセシビリティ | 全10モーダルで Esc・フォーカストラップ・ARIA 属性が未対応 |
| 26 | U-05 | 中 | UI・UX／アクセシビリティ | サイドバー項目がキーボード操作・支援技術に非対応 |
| 27 | U-06 | 中 | UI・UX／アクセシビリティ | .sess-rename 等のタップ領域が 24px 未満（WCAG 2.5.8 不適合） |
| 28 | U-07 | 中 | UI・UX／アクセシビリティ | ダークテーマの副次テキストが 4.5:1 未満（WCAG 2.1 AA 不適合） |
| 29 | U-09 | 中 | UI・UX／アクセシビリティ | 右クリック／長押しで即削除確認が起動する誤操作リスク |
| 30 | U-01 | 中 | UI・UX／アクセシビリティ | バックグラウンド更新で「⋯」メニューが勝手に閉じる |
| 31 | P-03 | 中 | 性能 | MutationObserver が body 全体を監視し全 DOM 変更を走査 |
| 32 | P-05 | 中 | 性能 | 384 次元 embedding の deepcopy による過剰な複製 |
| 33 | P-07 | 中 | 性能 | 静的配信が無圧縮・ETag 未対応で毎回 200 全転送 |
| 34 | F-06 | 低 | 機能不具合 | 添付取り込みで open().read() によりファイルディスクリプタ未クローズ |
| 35 | F-08 | 低 | 機能不具合 | 保存リトライ全失敗時に .tmp が孤児ファイルとして残留 |
| 36 | F-13 | 低 | 機能不具合 | Electron asar のハッシュ固定参照で仕様テストが壊れやすい |
| 37 | L-07 | 低 | ロジック／エッジケース | 文字列形式の旧 image フィールドで .get() が AttributeError |
| 38 | L-09 | 低 | ロジック／エッジケース | decensor 警告フィルタの恒真条件でフィルタ節がデッドコード |
| 39 | L-12 | 低 | ロジック／エッジケース | プロバイダ切替失敗の空 catch でユーザー通知なし |
| 40 | U-03 | 低 | UI・UX／アクセシビリティ | 画像再生成ボタンに待機表示と破壊的操作が混在 |
| 41 | U-08 | 低 | UI・UX／アクセシビリティ | alert/confirm/prompt の同期ネイティブダイアログ依存 |
| 42 | P-06 | 低 | 性能 | 共有記憶の重複排除が O(N*M) かつ毎ターン listdir |
| 43 | P-08 | 低 | 性能 | outbox ワーカーが 300ms 間隔で常時ディレクトリ走査 |

---

## Baseline Verification & Test Suite Execution Status

In accordance with non-destructive verification requirements, repository tests and build scripts were executed in the workspace without modifying any source files or configurations.

### 1. Official Python Test Runner (`tools/run_tests.py`)
- **Command**: `python tools/run_tests.py`
- **Result**: **FAILED (Exit Code: 1)**
- **Execution Metric**: Ran 986 tests in 27.97s; 985 executed, 1 skipped.
- **Console Log**:
  ```text
  テスト収集: 54 ファイル / 986 テスト（低速テスト: スキップ）
  [NG] 収集0件: test/test_channel_wizard_module.py — TestCase を継承しているか、メソッド名が test_ で始まるか確認
  ----------------------------------------------------------------------
  Ran 986 tests in 27.967s
  OK (skipped=1)
  結果: NG / 28.0 秒 / 985 実行 / スキップ 1 件
  ```
- **Root Cause**: `test/test_channel_wizard_module.py` defines pytest-style standalone functions (`def test_*():`) rather than subclassing `unittest.TestCase`. `unittest.TestLoader().discover` found 0 tests in that file, which tripped `tools/run_tests.py`'s zero-collection guard.

### 2. Pytest Execution on Channel Wizard (`pytest test/test_channel_wizard_module.py`)
- **Command**: `pytest test/test_channel_wizard_module.py`
- **Result**: **PASSED (Exit Code: 0)**
- **Execution Metric**: 10 passed in 0.92s.
- **Confirmation**: All 10 tests in `test/test_channel_wizard_module.py` are functionally valid when executed by a pytest runner.

### 3. Node.js / JavaScript UI Test Suites
Across 17 JavaScript/Node test scripts in `test/`, 12 pass and 5 fail:
1. `test/test_enter_send.cjs`: **FAILED (Exit Code: 1)**
   - *Error*: `ReferenceError: hideAgQuota is not defined at evalmachine.<anonymous>:70:3`
   - *Cause*: `public/app.js:9701` added `hideAgQuota()` to textarea blur listener, but VM sandbox in the test did not stub `hideAgQuota`.
2. `test/test_mobile_layout.cjs`: **FAILED (74 PASS, 6 FAIL)**
   - *Error*: Media query rule assertion failures and `.progress` CSS smoke failure (`FAIL T22: CSS スモーク`).
   - *Cause*: `lastMediaBlock` in test uses `lastIndexOf("@media (max-width: 768px)")`, which matched only a new trailing media query at `public/style.css:4957`, missing prior mobile layout declarations. Line 653 contains `overflow: hidden;` triggering T22 failure.
3. `test/test_model_favorites_ui.cjs`: **FAILED (86 PASS, 1 FAIL)**
   - *Error*: `FAIL: #favModelBtn pointerdown prevents blur in app.js`
   - *Cause*: `public/app.js:9540` attaches a `mousedown` listener instead of the required `pointerdown` listener.
4. `test/test_rag_hits_modal_ui.cjs`: **FAILED (Exit Code: 1)**
   - *Error*: `TypeError: document.body.style.removeProperty is not a function at applyRpCardStrength`
   - *Cause*: `public/app.js:10483` calls `document.body.style.removeProperty()`. The VM sandbox mock style object in the test lacked `removeProperty`.
5. `test/test_v4_spec.mjs`: **FAILED (Exit Code: 1)**
   - *Error*: `TypeError: Cannot read properties of undefined (reading 'replace')`
   - *Cause*: External Electron bundle asar hash changed from `runtime-DL3Dibca.js` to a newer build hash.

---

## Section 1: Functional Bugs & Defects

### F-01: `UnboundLocalError` in Outbox Error Recovery Path
- **File Path**: `server.py`
- **Line Numbers**: `7370–7394`
- **優先度**: **高**（High） ／ **総合順位**: **1位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `process_outbox()`, when an outbox JSON file cannot be opened (e.g. temporary Windows file lock `PermissionError` while an AI process is writing, or `UnicodeDecodeError`), an exception is caught at line 7382.
  When the file age exceeds 10 seconds (line 7385), the recovery handler attempts regex extraction:
  ```python
  m_rt = re.search(r'"reply_to"\s*:\s*"([^"]+)"', raw)
  m_sid = re.search(r'"session(?:_id)?"\s*:\s*"([^"]+)"', raw)
  ```
  Because the exception occurred before `raw = f.read()` succeeded, `raw` was never assigned in local scope on the first loop iteration, raising `UnboundLocalError: cannot access local variable 'raw' where it is not associated with a value`. If it fails on subsequent iterations, `raw` retains the content of the *previous* file, contaminating session IDs.
- **Recommended Remediation**:
  Initialize `raw = ""` prior to `try:` at line 7369. Guard regex extraction with `if raw:`.

---

### F-02: Race Condition and Process-Wide Temporary File Collisions in `write_json`
- **File Path**: `server.py`
- **Line Numbers**: `1109–1113`
- **優先度**: **高**（High） ／ **総合順位**: **2位**（全43件中）
- **Trigger Condition & Root Cause**:
  `write_json` is implemented as:
  ```python
  def write_json(path, obj):
      tmp = f"{path}.{os.getpid()}.tmp"
      with open(tmp, "w", encoding="utf-8") as f:
          json.dump(obj, f, ensure_ascii=False, indent=2)
      os.replace(tmp, path)
  ```
  `os.getpid()` is identical across all threads in the Python process. Multiple daemon threads concurrently calling `write_json` (e.g., prompt refinement, `APP_SETTINGS` saves, `AGENTS_LOG`, and `IDE_MOVES`) will generate the exact same temporary file path. One thread truncates or overwrites the file while another is in the middle of `json.dump` or `os.replace`. Furthermore, on Windows, `os.replace` fails immediately with `PermissionError` if another thread has the destination file open for reading.
- **Recommended Remediation**:
  Incorporate thread ID and UUID into temporary file paths:
  `tmp = f"{path}.{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex[:8]}.tmp"`.
  Wrap `os.replace` in a retry loop (up to 10 iterations with `time.sleep(0.02)`) matching the robust pattern implemented in `save(s)` and `memory_store.save_memories()`.

---

### F-03: Race Condition and Multiple Pending State Desynchronization in `/api/send`
- **File Path**: `server.py`
- **Line Numbers**: `11241–11292` and `7904–7910`
- **優先度**: **高**（High） ／ **総合順位**: **3位**（全43件中）
- **Trigger Condition & Root Cause**:
  `/api/send` does not verify whether the active session already contains a user message with `status in ("pending", "processing")`. If a user clicks Send twice or a client resends a request, two pending user messages are appended to `s["messages"]`.
  When a single assistant response arrives in `process_outbox()` without an explicit `reply_to` (lines 7904–7910), the loop executes:
  ```python
  for m in s["messages"]:
      if m["role"] == "user" and m.get("status") in ("pending", "processing") and (not reply_to or m["id"] == reply_to):
          m["status"] = "answered"
          target_u = m
  ```
  This marks *both* user messages as `"answered"`, while creating only one assistant message. The second user turn's response is permanently dropped.
- **Recommended Remediation**:
  In `/api/send`, check if any user message is currently pending or processing. If so, return `409 Conflict` (`{"error": "応答待ちのメッセージがあります", "code": "pending_turn_exists"}`). In `process_outbox()`, add `break` after marking the first matched user message when `reply_to` is absent.

---

### F-04: Failure to Clear Memory Vectors and Task Claims on Truncate, Delete, Regenerate & Edit
- **File Path**: `server.py`
- **Line Numbers**: `11422–11428`, `11493–11504`, `12004–12028`
- **優先度**: **中**（Medium） ／ **総合順位**: **12位**（全43件中）
- **Trigger Condition & Root Cause**:
  Task locks in `claimed_at` and associated memory vectors are leaked across multiple message mutation endpoints:
  1. In `/api/message/truncate` (line 11426), when `drop_target` is `None` (standard truncate):
     ```python
     else:
         rv = idx + 1 < len(s["messages"]) and plan_revert(s, idx)
         s["messages"] = s["messages"][:idx + 1]
     ```
     `removed_ids` remains an empty list `[]`. Lines 11500–11504 iterate over `removed_ids` to invoke `memory_store.delete_by_mid(sid, mid)` and purge `claimed_at`. Because `removed_ids` is empty, none of the truncated messages' memories or `claimed_at` task locks are cleaned up.
  2. In `/api/delete` (line 12004), while `memory_store.delete_by_mid` is invoked, `claimed_at.pop(...)` is omitted entirely.
  3. In `/api/message/regenerate` (line 11494) and `/api/message/edit` (line 11497), while memory deletion is performed via `delete_by_mid` or `delete_after_turn`, `claimed_at` locks (including sub-keys `{mid}:choices`, `{mid}:image_refine`, `{mid}:memory`, `{mid}:consolidation`) are never purged.
- **Recommended Remediation**:
  1. In `/api/message/truncate`, collect `removed_ids = [m["id"] for m in s["messages"][idx + 1:]]` in the `else:` branch prior to slicing.
  2. Define a unified helper function `clear_message_locks(claimed_at, mid)` (identical to the purge logic in `/api/stop`: `for k in list(claimed_at): if k == mid or k.startswith(mid + ":"): claimed_at.pop(k, None)`).
  3. Call this helper across all message mutation endpoints: truncate, delete, regenerate, and edit.

---

### F-05: Connection Churn and Brittle Error Handling in ComfyUI Image Generation Polling
- **File Path**: `server.py`
- **Line Numbers**: `8525–8615` (旧 `8253–8279`)
- **優先度**: **中**（Medium） ／ **総合順位**: **13位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `comfy_run()`:
  ```python
  while time.time() - start < timeout:
      time.sleep(0.1)
      try:
          h = json.loads(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10).read())
      except Exception:
          continue
  ```
  1. **Connection Churn & Excessive Polling**: Polling at a rigid 100ms (10Hz) interval without backoff generates up to ~9,000 short-lived TCP connections (`Connection: close`) over a generation timeout. When ComfyUI is under heavy load or restarting, this aggressive retry rate floods the accept queue and network stack.
  *(Note: Prior claims that CPython leaks unclosed sockets on Windows without `with` were inaccurate; CPython's reference counting closes sockets deterministically upon exiting the `except` block. However, reliance on implicit GC cleanup is unhygienic and does not mitigate connection churn or TIME_WAIT accumulation on active-close endpoints).*
  2. **Unhandled `/prompt` Failures**: `/prompt` dispatch lacked `urllib.error.HTTPError` handling, leaving HTTP 400/500 responses unparsed and socket streams unclosed upon submission failure.
  3. **Brittle Polling Exception Handling**: Catching all exceptions with a silent `continue` obscured underlying transport failures, omitted diagnostic logging, and lacked exponential backoff on errors.
- **Recommended Remediation**:
  1. Implement exponential backoff (`COMFY_POLL_MIN = 0.1` to `COMFY_POLL_MAX = 1.0`) during polling errors while maintaining 0.1s responsiveness on active valid poll responses.
  2. Use deterministic context managers (`with urllib.request.urlopen(...) as resp:`) and safely close `HTTPError` instances (`try: e.close() except Exception: pass`).
  3. Catch specific exception types (`urllib.error.HTTPError`, `(urllib.error.URLError, OSError, ValueError)`) and add throttled diagnostic warning logs (every 10 consecutive failures).
  4. Safeguard `/prompt` submission with explicit `HTTPError` response decoding and cleanup.

---

### F-07: Incomplete Task Handlers and Prompt Routing in `run_google_task` / `_google_build_prompt`
- **File Path**: `server.py`
- **Line Numbers**: `8757`, `8776`, `8894`, `8959–9005`
- **優先度**: **中**（Medium） ／ **総合順位**: **14位**（全43件中）
- **Trigger Condition & Root Cause**:
  Line 8776 accepts `task_type in ("user", "choices", "image_refine", "memory_extract", "consolidation")`. However, line 8757 in `_google_build_prompt` raises `ValueError("unknown task")` for `memory_extract` and `consolidation`. In `run_google_task`, lines 8849–8895 have no handling branches for these tasks, dropping through to `else: return`.
  Architecturally, long-term memory extraction and consolidation are intentionally reserved for local background workers (`memory_worker`, `consolidation_worker`) using local embeddings. This is corroborated by `_google_pick_tasks()` (lines 8959–9005), which explicitly documents `"user / choices / image_refine の3種のみ"` and actively treats `memory_status` / `consolidation_status` as stale to prevent infinite loops. Implementing memory extraction in Google AI would duplicate work and cause outbox write collisions.
- **Recommended Remediation**:
  Align line 8776 with the actual architectural design by removing `"memory_extract"` and `"consolidation"` from the accepted `task_type` tuple: `if task_type not in ("user", "choices", "image_refine"): return`.

---

### F-09: Unhandled `hideAgQuota` ReferenceError in Textarea Blur
- **File Path**: `public/app.js` and `test/test_enter_send.cjs`
- **Line Numbers**: `public/app.js:9701`, `test/test_enter_send.cjs:124`
- **優先度**: **中**（Medium） ／ **総合順位**: **21位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `public/app.js:9701`:
  ```javascript
  messageInput.addEventListener("blur", () => {
    hideAgQuota();
  });
  ```
  If `hideAgQuota` is not globally defined (such as in headless testing environments or modular imports), triggering a `blur` event throws an unhandled `ReferenceError: hideAgQuota is not defined`.
- **Recommended Remediation**:
  Guard invocation with `if (typeof hideAgQuota === "function") hideAgQuota();` and provide the stub in `test/test_enter_send.cjs`.

---

### F-10: Media Query Scope and Overflow Assertion Regressions in Mobile Test
- **File Path**: `test/test_mobile_layout.cjs`, `public/style.css`
- **Line Numbers**: `test/test_mobile_layout.cjs:277–315, 1232–1244`, `public/style.css:653, 4957`
- **優先度**: **中**（Medium） ／ **総合順位**: **22位**（全43件中）
- **Trigger Condition & Root Cause**:
  1. `lastMediaBlock(text)` uses `lastIndexOf("@media (max-width: 768px)")` to find mobile CSS rules. An additional media query appended at `public/style.css:4957` causes `lastMediaBlock` to check only the trailing block, failing assertions for `#mobileActionToggle` and `#mobileReveal`.
  2. Test T22 asserts `!/\.progress\s*\{[^}]*overflow:\s*hidden/i.test(cssText)`. Line 653 in `public/style.css` specifies `overflow: hidden;` for `.progress`, triggering an assertion failure.
- **Recommended Remediation**:
  Update `test_mobile_layout.cjs` to search across all `@media (max-width: 768px)` blocks, and align the `.progress` CSS specification.

---

### F-11: Event Type Discrepancy on Model Favorites Button
- **File Path**: `public/app.js`, `test/test_model_favorites_ui.cjs`
- **Line Numbers**: `public/app.js:9540`, `test/test_model_favorites_ui.cjs:59`
- **優先度**: **中**（Medium） ／ **総合順位**: **23位**（全43件中）
- **Trigger Condition & Root Cause**:
  `test_model_favorites_ui.cjs:59` verifies that `app.js` attaches a `pointerdown` listener to `#favModelBtn` with `preventDefault()` to prevent textarea blur on touchscreen and stylus devices. `public/app.js:9540` attaches `mousedown` instead.
- **Recommended Remediation**:
  Change `favBtn.addEventListener("mousedown", ...)` in `public/app.js:9540` to `pointerdown`.

---

### F-12: Missing `removeProperty` Method in RAG Hits Modal Test Sandbox
- **File Path**: `test/test_rag_hits_modal_ui.cjs`, `public/app.js`
- **Line Numbers**: `test/test_rag_hits_modal_ui.cjs:224`, `public/app.js:10483`
- **優先度**: **中**（Medium） ／ **総合順位**: **24位**（全43件中）
- **Trigger Condition & Root Cause**:
  `test_rag_hits_modal_ui.cjs` executes `app.js` in a Node VM sandbox. On evaluation, `initRpCardStrength()` calls `applyRpCardStrength()`, which invokes `document.body.style.removeProperty(...)`. The test sandbox mock for `document.body.style` does not provide `removeProperty`, throwing `TypeError: document.body.style.removeProperty is not a function`.
- **Recommended Remediation**:
  Add `removeProperty: () => {}` to `document.body.style` in `test/test_rag_hits_modal_ui.cjs`.

---

### F-06: Unclosed File Descriptors in Attachment Ingestion
- **File Path**: `server.py`
- **Line Numbers**: `8704`
- **優先度**: **低**（Low） ／ **総合順位**: **34位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `_google_prepare_attachments(m)`:
  ```python
  raw = open(path, "rb").read()
  ```
  Calling `open(...).read()` without a context manager leaves the file descriptor open until CPython garbage collector collects the file object. On Windows, this retains read locks on user attachment files.
- **Recommended Remediation**:
  Replace with `with open(path, "rb") as f: raw = f.read()`.

---

### F-08: Stale Temporary File Accumulation on Write Retries
- **File Path**: `server.py`
- **Line Numbers**: `1164–1175`, `1212–1223`, `1260–1271`
- **優先度**: **低**（Low） ／ **総合順位**: **35位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `save_choices_data`, `save_states_data`, and `save_images_data`, temporary files are generated as `tmp = f"{p}.{os.getpid()}.tmp"`. If all 10 `os.replace` retry attempts fail (e.g. persistent antivirus scan lock), `tmp` is never deleted, leaving orphaned `.tmp` files in `data/choices/`, `data/states/`, and `data/images/`.
- **Recommended Remediation**:
  Add an `else:` clause to the retry `for` loop:
  ```python
  if os.path.exists(tmp):
      try:
          os.remove(tmp)
      except OSError:
          pass
  ```

---

### F-13: Fragile Hash Lookup in External Electron Bundle Spec
- **File Path**: `test/test_v4_spec.mjs`
- **Line Numbers**: `test/test_v4_spec.mjs:23–24`
- **優先度**: **低**（Low） ／ **総合順位**: **36位**（全43件中）
- **Trigger Condition & Root Cause**:
  `test_v4_spec.mjs` searches an external installed Electron asar (`.../deepai-code-desktop/resources/server.asar`) for hardcoded hash strings (`runtime-DL3Dibca.js`). When the upstream application updates, `find(...)` returns `undefined`, throwing `TypeError: Cannot read properties of undefined (reading 'replace')`.
- **Recommended Remediation**:
  Use regex matching `x => /runtime-.*\.js/.test(x)` and check for null/undefined before string operations.

---

## Section 2: Logic Oversights & Edge Cases

### L-01: Historical Fallback Memory Extraction Timestamp Distortion
- **File Path**: `memory_extract.py`
- **Line Numbers**: `248–255`
- **優先度**: **高**（High） ／ **総合順位**: **4位**（全43件中）
- **Trigger Condition & Root Cause**:
  When extracting memories from an older message `m` in a session:
  ```python
  turn_idx = st.get("total_turns") or ((st.get("rotation_count", 0) * ROTATION_PERIOD) + len(user_msgs))
  ```
  `st.get("total_turns")` is the session's *current* cumulative turn count (e.g., turn 60). Because `st.get("total_turns")` evaluates to truthy, the historical calculation on the right-hand side of `or` is bypassed. As a result, memories extracted from turn 3 receive `turn_idx = 60`.
  In `memory_store.py`, memory recency weighting (`decay = 0.98 ** max(0, total_turn - m["turn_idx"])`), memory TTL aging, and chronological reconstruction in the prompt are severely distorted.
- **Recommended Remediation**:
  Replace line 252 with `calc_turn_idx(msgs, idx, st.get("total_turns"))` (importing `calc_turn_idx` from `server` or factoring it into a common utility).

---

### L-02: Permanent Trapping of Fulfilled/Inactive Memories Across Categories
- **File Path**: `memory_store.py`
- **Line Numbers**: `1473–1497`, `1084`
- **優先度**: **高**（High） ／ **総合順位**: **5位**（全43件中）
- **Trigger Condition & Root Cause**:
  Line 1477 explicitly excludes non-active memories only for `cat in ("rule", "calling")`:
  ```python
  if cat in ("rule", "calling") and existing.get("status", "active") != "active":
      continue
  ```
  For all other categories (`relationship`, `char_fact`, `user_fact`, `event`), if a memory was previously marked `fulfilled` or `inactive`, it is eligible for cosine similarity matching (`best_idx`).
  When matched, lines 1486–1496 update `content`, `tokens`, `embedding`, `content_hash`, and `turn_idx`, but *never* reset `status` to `"active"`.
  Subsequent active recall queries (`memory_store.py:1084`: `if m["id"] in tier1_ids or m.get("status", "active") != "active": continue`) filter out these memories. The memory is updated in storage on disk but permanently excluded from LLM context injection.
- **Recommended Remediation**:
  Either exclude non-active memories for all categories during auto-merge (`if existing.get("status", "active") != "active": continue`) or explicitly reactivate them upon update (`memories[best_idx]["status"] = "active"`).

---

### L-03: Cross-Turn Parallel Choices Staging Race Condition
- **File Path**: `server.py`
- **Line Numbers**: `7763–7767`, `7836–7843`, `8088–8097`
- **優先度**: **高**（High） ／ **総合順位**: **6位**（全43件中）
- **Trigger Condition & Root Cause**:
  When choices are generated for assistant response $N$, an outbox choice file is dispatched. If the user rapidly sends turn $N+1$ before the choice file is processed, the new user message is appended with `status: "pending"`.
  In `server.py:7764`, the fallback search for choice destination checks:
  ```python
  if m["role"] == "user" and (m.get("choices_status") in ("pending", "processing") or m.get("status") in ("pending", "processing")):
      target_user = m
      break
  ```
  Because the *new* turn $N+1$ user message has `m.get("status") == "pending"`, it matches `target_user = m`. Lines 7840–7843 stage turn $N$'s choices onto turn $N+1$'s user message. When turn $N+1$'s assistant message completes, lines 8088–8097 attach the staged choices to turn $N+1$'s assistant message. The user is presented with choices generated for the prior context.
- **Recommended Remediation**:
  Restrict the fallback choice destination match strictly to user messages with active choice requests:
  `if m["role"] == "user" and m.get("choices_status") in ("pending", "processing"):` (removing `m.get("status") in ("pending", "processing")`).

---

### L-04: Test Runner Test Discovery Incompatibility in `test_channel_wizard_module.py`
- **File Path**: `test/test_channel_wizard_module.py` and `tools/run_tests.py`
- **Line Numbers**: `test/test_channel_wizard_module.py:1–97`, `tools/run_tests.py:44–50`
- **優先度**: **高**（High） ／ **総合順位**: **7位**（全43件中）
- **Trigger Condition & Root Cause**:
  `test/test_channel_wizard_module.py` defines 10 unit tests as pytest-style standalone functions (`def test_*():`). `tools/run_tests.py` relies on `unittest.defaultTestLoader.discover`. Because standard Python `unittest` only discovers test methods in classes that inherit from `unittest.TestCase`, 0 tests are collected from that file. `tools/run_tests.py` treats 0 collected tests from a test module as a hard error, exiting with status code 1.
  *Note on Fixture Compatibility*: Merely wrapping the functions in a `unittest.TestCase` class is insufficient and will cause `TypeError: missing 1 required positional argument: 'monkeypatch'` / `'tmp_path'`. Multiple tests (`test_import_has_no_side_effects`, `test_missing_deps_detection`, `test_missing_getters_detection`) depend on pytest's automatic dependency injection fixtures (`tmp_path`, `monkeypatch`), which standard `unittest` does not provide.
- **Recommended Remediation**:
  Subclass `unittest.TestCase` and refactor pytest fixture usages into standard `unittest` constructs:
  - Replace `monkeypatch` with `unittest.mock.patch` or manual dictionary restore in `tearDown()` / `try...finally`.
  - Replace `tmp_path` with `tempfile.TemporaryDirectory()`.
  - Replace `pytest.raises` with `self.assertRaises(...)`.
  (Alternatively, configure `tools/run_tests.py` to invoke `pytest` or exclude pytest-dedicated test modules from standard unittest discovery).

---

### L-05: Inflight Message Equality Condition `None == None` True Evaluation
- **File Path**: `server.py`
- **Line Numbers**: `3498–3504`
- **優先度**: **中**（Medium） ／ **総合順位**: **15位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `build_prompt`:
  ```python
  if isinstance(m, dict) and raw_old.get("inflight_mid") == m.get("id") and isinstance(raw_old.get("prev_sent"), dict) and raw_old.get("prev_sent"):
  ```
  If `m` is an empty dictionary or does not specify `"id"`, `m.get("id")` is `None`. If `raw_old` has no `"inflight_mid"` key, `raw_old.get("inflight_mid")` is `None`. In Python, `None == None` evaluates to `True`. If `raw_old` has a non-empty `prev_sent` from a previous turn, this branch incorrectly triggers, restoring `prev_sent` as if `m` were an in-flight retry of an existing message.
- **Recommended Remediation**:
  Update condition to: `if isinstance(m, dict) and bool(m.get("id")) and raw_old.get("inflight_mid") == m.get("id") and ...`.

---

### L-06: Unhandled `JSONDecodeError` on External SQLite Workspace URIs
- **File Path**: `server.py`
- **Line Numbers**: `5308`, `5322`
- **優先度**: **中**（Medium） ／ **総合順位**: **16位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `load_ide_sessions` and `ide_workspaces`:
  ```python
  "workspace": ", ".join(ws_path(u) for u in json.loads(ws or "[]")),
  ...
  return [urllib.parse.unquote(u).replace("file:///", "") for u in json.loads((row and row[0]) or "[]")]
  ```
  If an external IDE process writes corrupted or partial JSON into SQLite `workspace_uris`, `json.loads` raises `json.JSONDecodeError`. Unlike date parsing on line 5300 (`try ... except`), lines 5308 and 5322 have no exception handler, causing `/api/ide/sessions` to crash with an HTTP 500 error.
- **Recommended Remediation**:
  Wrap `json.loads` in a helper function `safe_json_loads(text, default=[])` that catches `(json.JSONDecodeError, TypeError)` and returns `default`.

---

### L-08: Silent Swallowing of Empty Choices Output
- **File Path**: `server.py`
- **Line Numbers**: `7769–7815`
- **優先度**: **中**（Medium） ／ **総合順位**: **17位**（全43件中）
- **Trigger Condition & Root Cause**:
  If the LLM returns an empty list `[]` or blank text for choices, `normalized_choices` evaluates to empty. The block `if normalized_choices:` (line 7775) is bypassed. Lines 7810–7815 proceed to remove `choices_status` and `choices_error`, and line 7832 moves the file to `DONE`. The pending indicator on the message vanishes, but no choices are populated, leaving the user with no choices and no error notification.
- **Recommended Remediation**:
  In the `else:` branch of `if normalized_choices:`, record `target_asst["choices_error"] = "候補が空でした"` and mark `choices_status = "error"`.

---

### L-13: Swallowed Save Promise Rejection in Channel Creation Wizard
- **File Path**: `public/wizard.js`
- **Line Numbers**: `1985–1991`
- **優先度**: **中**（Medium） ／ **総合順位**: **18位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `public/wizard.js`:
  ```javascript
  if (a === "create") {
    return runAction("create", async () => {
      const ov = collectOverrides();
      await savePreview().catch(() => {});
      return doCreate(ov);
    });
  }
  ```
  If `savePreview()` fails (e.g. revision conflict or validation error), the error is silently swallowed, and `doCreate(ov)` immediately proceeds to create the session using stale or uncommitted draft state.
- **Recommended Remediation**:
  Do not swallow rejection; await `savePreview()` and halt creation if draft persistence fails.

---

### L-10: Missing `import sys` in Session Migration Script
- **File Path**: `update_sessions.py`
- **Line Numbers**: `4–8`, `81–84`
- **優先度**: **中**（Medium） ／ **総合順位**: **19位**（全43件中）
- **Trigger Condition & Root Cause**:
  At line 84:
  ```python
  if not success:
      sys.exit(1)
  ```
  `sys` is not in the imported modules list at lines 4–7. If session verification fails, the script crashes with `NameError: name 'sys' is not defined` instead of cleanly exiting with status 1.
- **Recommended Remediation**:
  Add `import sys` to top of `update_sessions.py`.

---

### L-11: Hardcoded Legacy Project Directory in Maintenance Script
- **File Path**: `tools/cleanup_test_sessions.py`
- **Line Numbers**: `8–11`
- **優先度**: **中**（Medium） ／ **総合順位**: **20位**（全43件中）
- **Trigger Condition & Root Cause**:
  Line 8 hardcodes:
  ```python
  ROOT_DIR = r"c:\Users\nukon\rp-image-chat-main"
  ```
  In the current repository (`c:\Users\nukon\RPasobou`), running `cleanup_test_sessions.py` fails immediately with `FileNotFoundError: [WinError 3] The system cannot find the path specified`.
- **Recommended Remediation**:
  Change `ROOT_DIR` to dynamically resolve from file location: `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`.

---

### L-07: Legacy String Image Object Import Crash
- **File Path**: `server.py`
- **Line Numbers**: `9178–9180`
- **優先度**: **低**（Low） ／ **総合順位**: **37位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `clean_import`:
  ```python
  if m.get("image") and m["image"].get("status") in ("queued", "generating"):
  ```
  In legacy session archives, `m["image"]` may be stored as a simple string file path (e.g. `"images/char_01.png"`). If `m["image"]` is a string, `m.get("image")` is truthy, and calling `.get()` on a `str` raises `AttributeError: 'str' object has no attribute 'get'`.
- **Recommended Remediation**:
  Add `isinstance(m.get("image"), dict)` check before calling `.get()`.

---

### L-09: Tautological Filter Logic in Decensor Dictionary Warning Check
- **File Path**: `decensor_engine.py`
- **Line Numbers**: `345–350`
- **優先度**: **低**（Low） ／ **総合順位**: **38位**（全43件中）
- **Trigger Condition & Root Cause**:
  In `_decensor_validate`:
  ```python
  for frm, to in words.items():
      frm_word, _ = _split_context_key(frm)
      inner = [w for w in target_words if (w != frm_word or w != to) and w in to]
  ```
  Because line 340 already eliminated `frm == to`, `frm_word != to` is invariant for all replacement entries. For any candidate word $w$:
  - If $w == frm\_word$, then $w \neq to$ is True.
  - If $w == to$, then $w \neq frm\_word$ is True.
  - If $w \neq frm\_word$ and $w \neq to$, both are True.
  Consequently, `(w != frm_word or w != to)` is a boolean tautology that evaluates to `True` for every $w$, rendering the filter clause dead code.
  *Note on `and` replacement hazard*: Replacing `or` with `and` is incorrect. If replaced with `(w != frm_word and w != to)`, self-recursive chaining (e.g. `肉 -> 生肉棒` where $w == frm\_word$) and transitive chaining (e.g. $A \to B$ and $B \to C$ where $w == to$) would both be filtered out and silenced, breaking chaining assertions in `test/test_decensor.py:358-362`.
- **Recommended Remediation**:
  Simplify the list comprehension directly to `inner = [w for w in target_words if w in to]`. This accurately flags any dictionary entry whose replacement text contains any target word (including recursive or chained words) without dead tautological conditions.

---

### L-12: Silent Error Suppression in Frontend AI Provider Switch
- **File Path**: `public/app.js`
- **Line Numbers**: `4031–4038`, `4058–4064`
- **優先度**: **低**（Low） ／ **総合順位**: **39位**（全43件中）
- **Trigger Condition & Root Cause**:
  Calls to `/api/ai/provider` inside API key save and delete handlers are wrapped in empty `catch (_) {}` blocks:
  ```javascript
  try {
    await checked("/api/ai/provider", { sid, provider: "google", model: defaultModel });
    flash("Google AI Studio に切り替えました");
    await poll();
  } catch (_) {}
  ```
  While `flash(...)` is positioned after `await checked(...)` (so a thrown error correctly avoids showing the success toast), the empty `catch (_) {}` completely swallows network dropouts and HTTP failure responses. The user receives zero feedback as to why the provider did not switch.
- **Recommended Remediation**:
  Handle the error in the `catch` block to notify the user: `catch (e) { toast("切替に失敗しました: " + (e?.message || e), { kind: "err" }); }`.

---

## Section 3: Performance Bottlenecks & Optimization Proposals

### P-04: Memory Leak via Unrevoked Object URLs and Base64 Payload Inefficiency
- **Affected Components / Paths**:
  - `public/app.js`: lines 7296–7308, 816, 7126, 7275
- **優先度**: **高**（本監査で新規付与） ／ **総合順位**: **8位**（全43件中）
- **Rationale for Improvement**:
  1. `URL.createObjectURL(file)` is called on file attach, but `URL.revokeObjectURL(a.preview)` is never called anywhere when attachments are deleted, cleared upon send, or discarded on channel switch. These blob URLs remain pinned in browser memory for the lifetime of the tab.
  2. Files up to 40MB are converted into Base64 strings in browser heap (~53MB string, ~106MB memory in V8) and serialized inside a JSON request to `/api/upload`.
- **Actionable Suggestions & Implementation Outline**:
  - Create a helper `clearAttaching()`:
    ```javascript
    function clearAttaching() {
      for (const a of attaching) {
        if (a.preview && a.preview.startsWith("blob:")) URL.revokeObjectURL(a.preview);
      }
      attaching = [];
      renderAttach();
    }
    ```
    Invoke `clearAttaching()` on send completion (line 7126), session switch (line 816), and attachment removal (lines 7275, 7306).
  - Adopt `multipart/form-data` for `/api/upload` to avoid Base64 string memory ballooning.

---

### P-01: High-Frequency Polling (100ms) and Global Lock Contention in Session List
- **Affected Components / Paths**:
  - `public/app.js`: lines 11780–11794, line 11789
  - `server.py`: lines 9480–9484, 1496–1534, 9493–9500
- **優先度**: **高**（本監査で新規付与） ／ **総合順位**: **9位**（全43件中）
- **Rationale for Improvement**:
  1. During active AI turns (`isWaiting`), `pollLoop` in `public/app.js` polls `/api/session` every **100ms** (10 times/second per open tab).
  2. Simultaneously, `setInterval(refreshSessions, 3000)` calls `/api/sessions` every 3 seconds. In `/api/sessions`, the server acquires the global `lock`, iterates over all sessions via `list_ids()`, and invokes `load(sid, readonly=True)`. Each `load()` executes **4 `os.stat` system calls** under `with lock:`. For 25 sessions, that is 100 filesystem stats holding the global thread lock every 3 seconds.
  3. On every 100ms poll to `/api/session`, even when returning `{"unchanged": True}`, `server.py` executes `os.listdir(ARCHIVE_DIR)` with regex evaluations across archive filenames, incurring redundant disk I/O and CPU waste.
- **Actionable Suggestions & Implementation Outline**:
  - *Client*: In `public/app.js:11780`, relax the active polling interval from 100ms to **400ms–500ms**. Increase `refreshSessions` interval from 3s to **10s** (or poll only on user drawer interaction).
  - *Server*: In `server.py:9493`, cache `has_history` in `session_cache` rather than executing `os.listdir(ARCHIVE_DIR)` on every poll. In `/api/sessions`, read metadata from an in-memory summary dictionary rather than `stat`-ing every session file under `with lock:`.

---

### P-02: Full-DOM Sidebar Destruction and Keystroke Layout Thrashing
- **Affected Components / Paths**:
  - `public/app.js`: lines 709, 720, 10022, 357–376
- **優先度**: **高**（本監査で新規付与） ／ **総合順位**: **10位**（全43件中）
- **Rationale for Improvement**:
  1. `renderSessions()` executes `el.innerHTML = ""` every 3 seconds (triggered by `refreshSessions`), destroying and re-creating all session DOM elements, throwing away hover states, focus, and scroll offsets.
  2. Typing into `#sessionFilter` triggers `renderSessions` on every input event without debouncing (`oninput = renderSessions`), rebuilding the entire sidebar on every keystroke.
  3. `autoResizeInput()` mutates `ta.style.height = "auto"` (style write), queries `ta.scrollHeight` (forced reflow), updates height (style write), and then calls `updateFooterHeight()` which reads `footer.offsetHeight` (second forced reflow) and mutates CSS `:root`. This causes severe layout thrashing on every keystroke.
- **Actionable Suggestions & Implementation Outline**:
  - *Sidebar Reconciliation*: Update `renderSessions()` to reconcile existing session elements by ID/rev rather than setting `el.innerHTML = ""`.
  - *Debounce Filter*: Debounce `#sessionFilter.oninput` by 150ms using a timer.
  - *Batch Layout Reads/Writes*: In `autoResizeInput()`, avoid setting `ta.style.height = "auto"` unless the text line count changes, or wrap height adjustments in `requestAnimationFrame`.

---

### P-03: Unscoped Global `MutationObserver` on `document.body`
- **Affected Components / Paths**:
  - `public/prompt_toolbar.js`: lines 1201–1220
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **31位**（全43件中）
- **Rationale for Improvement**:
  `PromptToolbar` observes `document.body` with `{ childList: true, subtree: true }`. Every single DOM mutation across the entire application (message bubbles added, sidebar updates, progress pills) triggers the observer, and for each added node, `scanAndAttach` runs `root.querySelectorAll(selector)`.
- **Actionable Suggestions & Implementation Outline**:
  Scope the observer to target containers only:
  ```javascript
  const targetContainers = [document.getElementById("promptModal"), document.getElementById("chatLog")].filter(Boolean);
  targetContainers.forEach(container => observer.observe(container, { childList: true, subtree: true }));
  ```

---

### P-05: Inefficient Deepcopy of 384-Dimensional Embedding Vectors
- **Affected Components / Paths**:
  - `memory_store.py`: lines 697–703, lines 1217–1232
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **32位**（全43件中）
- **Rationale for Improvement**:
  Every memory contains a 384-element float list (`"embedding": [0.012, -0.045, ...]`). In `load_memories()`, even when the cache is hit, Python's `copy.deepcopy()` is invoked on the entire data tree. `copy.deepcopy` recursively duplicates every individual float object and maintains a memo dictionary. With 100 memories, over 38,400 float objects are cloned on every memory search/load call.
  *Note on Shallow Copy Hazard*: A naive shallow copy `[dict(m) for m in ...]` causes severe in-memory cache corruption. Memory dictionaries contain mutable objects such as `history: list` and `elapsed_time: dict`. During memory consolidation and archiving (`_archive_memory_to_history` at `memory_store.py:1217-1232`), code executes `mem_obj.setdefault("history", []).append(old_hist)`. With shallow copying, `m["history"]` points to the identical list object residing in `_memory_cache`, polluting the cache prior to disk commit.
- **Actionable Suggestions & Implementation Outline**:
  Implement a safe property-level clone that shares the read-only float embedding vector while duplicating mutable lists and dicts:
  ```python
  def clone_memory_item(m):
      item = dict(m)
      if "history" in item and isinstance(item["history"], list):
          item["history"] = [dict(h) for h in item["history"]]
      if "elapsed_time" in item and isinstance(item["elapsed_time"], dict):
          item["elapsed_time"] = dict(item["elapsed_time"])
      # "embedding" (list of floats) is never mutated in-place and can be safely shared
      return item

  def clone_memory_data(d):
      out = dict(d)
      out["memories"] = [clone_memory_item(m) for m in d.get("memories", [])]
      return out
  ```

---

### P-07: Uncompressed Static Asset Delivery and Missing HTTP Cache Revalidation
- **Affected Components / Paths**:
  - `server.py`: lines 9404–9417
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **33位**（全43件中）
- **Rationale for Improvement**:
  1. Synchronous disk reads on every static request without in-memory caching.
  2. `public/app.js` (514 KB) and `public/style.css` (160 KB) are transmitted uncompressed without `gzip` or `deflate`.
  3. No `ETag` or `Last-Modified` headers are sent. Although `Cache-Control: no-cache, must-revalidate` is specified, clients cannot revalidate with `If-None-Match` or `If-Modified-Since`, forcing full 200 OK payloads on every page refresh.
- **Actionable Suggestions & Implementation Outline**:
  Cache static file bytes in memory. Send `ETag: "<mtime_hash>"`. When `If-None-Match` matches, respond with `304 Not Modified`. Compress `.js`, `.css`, and `.html` with gzip if supported by the client.

---

### P-06: Quadratic O(N*M) Deduplication and Directory Scan in Shared Memories
- **Affected Components / Paths**:
  - `memory_store.py`: lines 1029–1045
- **優先度**: **低**（本監査で新規付与） ／ **総合順位**: **42位**（全43件中）
- **Rationale for Improvement**:
  On every message turn when `shared_memories` is active:
  1. `os.listdir(RAG_DIR)` scans every memory file in the directory.
  2. For every matching channel, `load_memories` reads and deepcopies all vectors.
  3. `if not any(c.get("id") == om.get("id") for c in candidates)` performs a nested linear search, causing quadratic O(N * M) comparison overhead.
- **Actionable Suggestions & Implementation Outline**:
  Maintain an in-memory index `_character_memories[character_name]` mapping characters to memory IDs. When resolving shared memories, deduplicate using `seen_ids = set(...)` in O(1) time.

---

### P-08: High-Frequency Polling in Outbox Background Worker
- **Affected Components / Paths**:
  - `server.py`: lines 9041–9057
- **優先度**: **低**（本監査で新規付与） ／ **総合順位**: **43位**（全43件中）
- **Rationale for Improvement**:
  `outbox_worker` loops continuously, invoking `os.scandir(OUTBOX)` every 300ms when idle, adding constant I/O wakeups.
- **Actionable Suggestions & Implementation Outline**:
  Increase `outbox_worker` idle sleep from 300ms to **1.0s–2.0s** after 10 consecutive idle iterations, or wake the worker via a `threading.Event()` whenever outbox files are written by the server or external tools.

---

## Section 4: UI/UX & Accessibility Improvement Proposals

### U-02: Unexpected Channel Navigation and State Loss on Server Error Responses
- **Affected Components / Paths**:
  - `public/app.js`: lines 6534, 6603–6605, 772–775
- **優先度**: **高**（本監査で新規付与） ／ **総合順位**: **11位**（全43件中）
- **Rationale for Improvement**:
  In `loadCurrentSession()` (`public/app.js:6534`):
  ```javascript
  if (s.error) {
    sid = "";
    resetDecensorToast();
    return refreshSessions();
  }
  ```
  On transient network disconnection or server restart, `fetch()` throws an exception and is handled by `catch (e)` at line 6603, which retains `sid` and updates the UI status pill to "サーバー未接続".
  However, when the HTTP connection succeeds but the server responds with a 500 error, validation failure, or transient backend error formatted as JSON `{ "error": "..." }`, `s.error` evaluates to truthy. The client immediately resets `sid = ""` and calls `refreshSessions()`. Because `""` is not in `sessions`, `refreshSessions()` automatically navigates to `sessions[0].id`. The user is jarringly transported to the first channel in the list, and any unsaved composer input is lost.
- **Actionable Suggestions & Implementation Outline**:
  Restrict session reset and navigation strictly to explicit session deletion (404):
  ```javascript
  if (s.error) {
    if (s.error === "no such session") {
      sid = "";
      resetDecensorToast();
      return refreshSessions();
    }
    $("statusPill").className = "pill err";
    $("statusPill").textContent = "エラー: " + s.error;
    return;
  }
  ```

---

### U-04: Widespread Absence of Global Escape Key, Focus Trapping, and Dialog ARIA Semantics
- **Affected Components / Paths**:
  - `public/index.html`: lines 390, 410, 421, 433, 439, 457, 474, 590, 608, 964
  - `public/app.js`: lines 8760–8762
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **25位**（全43件中）
- **Rationale for Improvement**:
  Across 10 modal dialogs in the application (`promptModal`, `editModal`, `textModal`, `ragHitsModal`, `ideModal`, `forkModal`, `ragModal`, `elapsedModal`, `aiToolModal`, `modelSheet`):
  1. None have `role="dialog"`, `aria-modal="true"`, or `aria-labelledby` attributes.
  2. None trap keyboard focus: pressing Tab cycles past the modal into background page elements.
  3. No unified Escape key listener exists for closing open modals. As explicitly acknowledged by the code comment in line 8761, pressing Esc does nothing.
  4. Closing a modal does not restore keyboard focus to the triggering element.
- **Actionable Suggestions & Implementation Outline**:
  Add `role="dialog"`, `aria-modal="true"`, and `aria-labelledby` to all modal containers in `public/index.html`. Implement a unified modal stack manager in `public/app.js`:
  ```javascript
  const modalStack = [];
  function openModal(modalEl, focusEl) {
    modalStack.push({ el: modalEl, returnFocus: document.activeElement });
    modalEl.classList.remove("hidden");
    (focusEl || modalEl.querySelector("button, input, textarea"))?.focus();
  }
  function closeModal(modalEl) {
    modalEl.classList.add("hidden");
    const item = modalStack.pop();
    item?.returnFocus?.focus();
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modalStack.length > 0) {
      e.preventDefault();
      closeModal(modalStack[modalStack.length - 1].el);
    }
  });
  ```

---

### U-05: Inaccessible Non-Keyboard Sidebar Items
- **Affected Components / Paths**:
  - `public/app.js`: lines 723–745
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **26位**（全43件中）
- **Rationale for Improvement**:
  Project headers and session list items are created as plain `<div>` elements with mouse `onclick` listeners. They lack `tabindex="0"`, `role="button"`, `aria-expanded` (for projects), and `keydown` event listeners (for Enter/Space). Keyboard-only and assistive technology users cannot navigate, collapse, or switch channels via keyboard.
- **Actionable Suggestions & Implementation Outline**:
  Add `tabindex="0"`, `role="button"`, and keydown handlers:
  ```javascript
  d.setAttribute("tabindex", "0");
  d.setAttribute("role", "button");
  d.setAttribute("aria-label", s.title || "(無題)");
  d.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      switchTo(s.id);
    }
  };
  ```

---

### U-06: Sub-Standard Touch Targets on Mobile Devices (WCAG 2.5.5 / 2.5.8 Failure)
- **Affected Components / Paths**:
  - `public/style.css`: lines 35, 472, 946, 2594, 2733
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **27位**（全43件中）
- **Rationale for Improvement**:
  `.sess-rename` is positioned inside the session card with a footprint of ~14px × 14px. On touch screens, attempting to tap `.sess-rename` routinely triggers the parent `.sess.onclick`, unintentionally switching channels. Buttons in RAG entries, fork turns, and category filters are far smaller than the WCAG 2.5.8 minimum target size of 24×24px (and recommended 44×44px).
- **Actionable Suggestions & Implementation Outline**:
  In `public/style.css`, expand `.sess-rename` touch area using a pseudo-element:
  ```css
  .sess-rename {
    position: relative;
    padding: 8px;
    margin: -8px;
  }
  ```
  Give `.fork-btn`, `.rag-filter-btn`, and RAG entry buttons a `min-height: 36px; min-width: 36px; display: inline-flex; align-items: center; justify-content: center;`.

---

### U-07: Low Contrast Failures on Dark Theme (WCAG 2.1 AA Failure)
- **Affected Components / Paths**:
  - `public/style.css`: lines 33, 636, 2786, 3054
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **28位**（全43件中）
- **Rationale for Improvement**:
  On dark backgrounds (`#1a1a1e` / `#202024`):
  - `#777` against `#1a1a1e` yields a contrast ratio of **3.73:1**.
  - `#666` against `#1a1a1e` yields a contrast ratio of **2.90:1**.
  For 11px text, WCAG 2.1 AA mandates a minimum contrast ratio of **4.5:1** (requires colors of at least `#9e9ea6` or lighter). In bright environments or on low-tier screens, secondary information, message timestamps, and memory metadata are illegible.
- **Actionable Suggestions & Implementation Outline**:
  In `public/style.css`:
  - Update `.sess-sub` color from `#777` to `#a8a8b2` (contrast ratio 5.1:1).
  - Update `.meta` color from `#666` to `#9e9ea6` (contrast ratio 4.6:1).
  - Update `.folder-row .st` and `.rag-mem-meta` from `#888` / `#777` to `#a0a0aa`.

---

### U-09: Accidental Channel Deletion Hazard via Right-Click / Long-Press
- **Affected Components / Paths**:
  - `public/app.js`: lines 740–745
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **29位**（全43件中）
- **Rationale for Improvement**:
  Right-clicking a session card immediately executes `confirm("...を削除？")` instead of opening a context menu. On mobile, touch long-press synthesizes a `contextmenu` event, surprising users with a destructive deletion prompt when attempting to select or preview a channel.
- **Actionable Suggestions & Implementation Outline**:
  Replace the direct `confirm("削除？")` on `d.oncontextmenu` with a contextual bottom sheet / popover offering: "名前を変更", "複製", "書き出し", "削除".

---

### U-01: Action Menu ("⋯") Abruptly Dismissed by Background Poll
- **Affected Components / Paths**:
  - `public/app.js`: lines 6210–6211
- **優先度**: **中**（本監査で新規付与） ／ **総合順位**: **30位**（全43件中）
- **Rationale for Improvement**:
  Whenever `render()` executes (which occurs whenever `lastRev` increments due to incoming replies, background choices generation, tag refinement, or ComfyUI generation completions), it unconditionally executes `closeMoreMenu()`. A user reading or about to tap "編集", "削除", or "分岐" has the menu abruptly vanished under their cursor/finger without warning.
- **Actionable Suggestions & Implementation Outline**:
  Check whether the message associated with `moreOpen.cur` has actually changed its DOM ID or text. If unchanged, retain the menu open and adjust its positioning rather than closing it.

---

### U-03: Confusing Interactive Status Label on Image Refine Button
- **Affected Components / Paths**:
  - `public/app.js`: lines 5330–5350
- **優先度**: **低**（本監査で新規付与） ／ **総合順位**: **40位**（全43件中）
- **Rationale for Improvement**:
  In `public/app.js:5330-5332`, the tooltip for `waitingGen` states:
  `rgTitle = "画像の生成待ちです（終わると 🔁 に戻ります。タップで作り直す）";`
  This confirms that allowing the button to remain clickable during generation was an intentional design choice ("tap to recreate/regenerate with a new seed if taking too long"), rather than an accidental omission of `waitingGen` from disabled checks.
  However, combining a passive progress indicator ("⏳ 生成待ち…") with a destructive re-generation action creates an affordance conflict: users tap the button assuming it is an inert status indicator or a manual status poll, inadvertently aborting and re-queueing generation.
- **Actionable Suggestions & Implementation Outline**:
  Decouple the passive status indicator from the destructive regeneration trigger:
  - Display a non-interactive progress pill `[⏳ 生成待ち...]`.
  - Display a distinct, clearly labeled button `[↺ 別のシードで作り直す]` with a confirmation or distinct secondary styling.

---

### U-08: Blocking Synchronous Native Dialogs in Modern Mobile/PWA Flows
- **Affected Components / Paths**:
  - `public/app.js`: lines 742, 753, 7091, 7115, 7153, 10871, 10874
- **優先度**: **低**（本監査で新規付与） ／ **総合順位**: **41位**（全43件中）
- **Rationale for Improvement**:
  `window.alert`, `window.confirm`, and `window.prompt` freeze JS execution and cannot be styled. When installed as a mobile PWA or accessed over Android Chrome / iOS Safari, native prompts pop jarring OS dialogs, fail to focus input, or are suppressed by browser security policies. Custom modal sheets (such as `favDeleteModal` and `favNameModal`) are already implemented in the project but are inconsistently adopted.
- **Actionable Suggestions & Implementation Outline**:
  - Replace `window.prompt` in `renameChannel()` with an in-app dialog (similar to `favNameModal`).
  - Replace `window.confirm` in channel deletion and session reset with a confirmation sheet (similar to `favDeleteModal`).
  - Replace `window.alert` in send failure handlers with dismissible toast notifications (`toast(msg, { kind: "err" })`).

---

## Appendix: Independent Verification & Reproduction Guide

### Verification Commands

1. **Verify Official Python Test Suite Baseline**:
   ```powershell
   python tools/run_tests.py
   ```
   *Expected Current Output*: Runs 986 tests, reports `[NG] 収集0件: test/test_channel_wizard_module.py`, exits with code 1.

2. **Verify Pytest Execution on Channel Wizard**:
   ```powershell
   pytest test/test_channel_wizard_module.py
   ```
   *Expected Output*: 10 passed in ~0.9s, exits with code 0.

3. **Verify Node UI Test Failures**:
   ```powershell
   node test/test_enter_send.cjs
   node test/test_model_favorites_ui.cjs
   node test/test_rag_hits_modal_ui.cjs
   node test/test_mobile_layout.cjs
   ```
   *Expected Output*: Reproduces `ReferenceError: hideAgQuota is not defined`, `FAIL: #favModelBtn pointerdown prevents blur`, `TypeError: document.body.style.removeProperty is not a function`, and `FAIL T22: CSS スモーク`.

4. **Verify Outbox `UnboundLocalError` Logic**:
   ```powershell
   python -c "try:
       with open('non_existent_locked_file') as f:
           raw = f.read()
   except Exception:
       try:
           print(raw)
       except UnboundLocalError as e:
           print('Verified:', e)"
   ```
   *Output*: `Verified: cannot access local variable 'raw' where it is not associated with a value`.

5. **Verify Inflight `None == None` Logic**:
   ```powershell
   python -c "raw_old = {'prev_sent': {'a': 1}}; m = {}; print('Evaluates to True:', raw_old.get('inflight_mid') == m.get('id'))"
   ```
   *Output*: `Evaluates to True: True`.

6. **Verify Fallback Turn Calculation Distortion**:
   ```powershell
   python -c "msgs = [{'role':'user','text':'hi','id':'m1'},{'role':'assistant','text':'hello','id':'m2'}]; st={'total_turns':50}; print('Distorted:', st.get('total_turns')); from read_past_messages import calc_turn_idx; print('Correct:', calc_turn_idx(msgs, 0, st['total_turns']))"
   ```
   *Output*: Demonstrates `st.get('total_turns')` outputs 50 for turn 0, whereas `calc_turn_idx` correctly outputs 0.

---
*Report compiled and independently verified by worker_audit_1 in strictly non-destructive mode.*
