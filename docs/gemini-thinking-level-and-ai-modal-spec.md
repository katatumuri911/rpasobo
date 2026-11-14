# 実装仕様書（改訂版）：Gemini API / Vertex AI「思考の深さ (thinking_level)」選択機能 ＆ AI設定のツールモーダル集約

## 1. 概要と目的
1. **思考の深さ（Thinking Level / Budget）の選択機能追加**
   Google AI Studio (`google`) および Google Cloud Vertex AI (`vertex`) プロバイダ利用時に、「本文・状況整理（メイン生成）」と「補助タスク（選択肢8択・画像推敲・長期記憶抽出）」の思考の深さを選択可能にする。
   モデル世代ごとのパラメータ仕様差（`thinkingLevel` vs `thinkingBudget`、およびモデルごとの対応段の違い）はバックエンドで自動正規化し、HTTP 400 エラーや思考トークン消費による JSON 途切れ（`MAX_TOKENS`）を未然に防止する。
2. **AI設定・認証・モデル選択の「ツールモーダル」への1か所集約**
   これまで「チャンネル設定画面」に置かれていた AIプロバイダ選択・APIキー設定・Vertex認証/リージョン設定・モデル選択・API詳細設定を**「ツールモーダル（`#aiToolModal`）」へ移設・集約**し、日常操作用の**「サイドバー（コンパクト表示）」**と合わせた2か所構成に整理する。チャンネル設定画面からはAI関連フォームを撤去し、現在値の1行サマリとツールモーダルを開くショートカットボタンのみを配置する。

---

## 2. バックエンド仕様（`google_ai.py`）

### 2.1 既存関数の無改造維持（既存テスト互換性の担保）
既存の全704件の単体テストを破壊しないため、以下の既存関数・定数のシグネチャおよび既存ロジックは変更しないこと。
* `_is_thinking_model(model_id)`（65行目付近）：無改造で維持する。
* `_step1_generation_config(model)`（1624行目付近）：無改造で維持する（1段目の `fetch_prompt` 呼び出しにはユーザーの `thinking_level` を適用せず、常に既存の軽量設定で実行させる）。
* `list_models()`（1243行目付近）：キャッシュ構造 `_models_cache` や返却辞書のフィールド計算は無改造で維持する。
* `extract_text(data)`（1515行目付近）：返却タプル `(text, meta)` の構造を維持しつつ、`meta["usage"]` から思考トークン数を参照できるようにする。

### 2.2 モデル世代判定と `resolve_thinking_config` の新設
`google_ai.py` に以下の定数・関数を追加する。
※`gemma-3` 系や `gemini-1.5-flash-latest` 等の非思考モデルが誤判定されて 400 エラーとなる重大バグを防止するため、除外フィルタを厳格化する。

```python
VALID_THINKING_LEVELS = ("auto", "minimal", "low", "medium", "high", "xhigh")
VALID_CHOICES_THINKING_LEVELS = ("low", "minimal", "medium", "high", "auto", "same")


def _thinking_family(model_id):
    """モデル名から思考パラメータの世代・対応範囲を判定する
    戻り値:
      'none'            : 思考非対応、または 2.0-thinking 等の level/budget 非対応モデル、Gemma等
      'budget_25_flash' : Gemini 2.5 Flash 系 (thinkingBudget: 0〜24576)
      'budget_25_pro'   : Gemini 2.5 Pro 系 (thinkingBudget: 128〜32768)
      'level_3_flash'   : Gemini 3.0〜3.7 Flash 系 (MINIMAL / LOW / MEDIUM / HIGH)
      'level_38_4'      : Gemini 3.x Pro 系、3.8+ Flash、4 Carbon、-latest 等 (LOW / MEDIUM / HIGH)
      'level_hydrogen'  : Gemini 4 Hydrogen 系 (LOW / MEDIUM / HIGH / XHIGH)
    """
    low = str(model_id or "").lower()

    # 1. 思考パラメータ非対応モデル（2.0系、1.x系、Gemma系）の完全除外
    # ※ _is_thinking_model("gemma-3-27b") は "3-" により True になるため、ここで確実に none に落とす
    if any(k in low for k in ("2.0", "1.5", "1.0", "gemma", "nano", "embedding")):
        return "none"

    # 思考モデル判定（_is_thinking_model または 思考世代の最新エイリアス）
    is_latest_thinking = any(k in low for k in ("flash-latest", "pro-latest", "thinking-latest"))
    if not (_is_thinking_model(low) or is_latest_thinking):
        return "none"

    # 2. Gemini 2.5 系 (thinkingBudget)
    if "2.5" in low:
        return "budget_25_flash" if "flash" in low else "budget_25_pro"

    # 3. Gemini 4 Hydrogen 系 (XHIGH 対応)
    if "hydrogen" in low:
        return "level_hydrogen"

    # 4. Gemini 3.8+ / 4.x / -latest 系 (MINIMAL非対応、LOW / MEDIUM / HIGH)
    # ※ 3.8, 3.9, 4.x, および最新エイリアス
    if any(k in low for k in ("3.8", "3-8", "3.9", "3-9", "4.", "4-", "flash-latest", "pro-latest")):
        return "level_38_4"

    # 5. Gemini 3.0〜3.7 Flash 系 (MINIMAL 対応)
    if "flash" in low:
        return "level_3_flash"

    # 6. その他の 3.x Pro 等
    return "level_38_4"


def resolve_thinking_config(model_id, level="auto"):
    """モデルと指定レベルから (thinkingConfig辞書またはNone, 実効レベル文字列, 推奨最小maxOutputTokens) を返す。
    非対応の段は HTTP 400 を防ぐため自動で有効な隣接段へ丸める。
    """
    fam = _thinking_family(model_id)
    lv = str(level or "auto").strip().lower()
    if lv not in VALID_THINKING_LEVELS:
        lv = "auto"

    if fam == "none":
        return None, "none", 0

    # 1. Gemini 2.5 系: thinkingBudget (トークン数) に変換
    if fam in ("budget_25_flash", "budget_25_pro"):
        if lv == "auto":
            return None, "auto(budget)", 0
        min_b = 0 if fam == "budget_25_flash" else 128
        budget_map = {
            "minimal": min_b,
            "low": 1024,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
        }
        b = budget_map.get(lv, 4096)
        min_out = 16384 if b >= 8192 else 0
        return {"thinkingBudget": b}, f"budget:{b}", min_out

    # 2. Gemini 3.x / 4.x 系: thinkingLevel に変換
    default_lv = "HIGH" if fam == "level_hydrogen" else "MEDIUM"
    if lv == "auto":
        return None, f"auto({default_lv})", 0

    eff = lv.upper()
    # MINIMAL は 3.0〜3.7 Flash 系のみ対応。Pro 系や 3.8 / 4 系では LOW に丸める
    if eff == "MINIMAL" and fam != "level_3_flash":
        eff = "LOW"
    # XHIGH は Hydrogen のみ対応。他モデルでは HIGH に丸める
    if eff == "XHIGH" and fam != "level_hydrogen":
        eff = "HIGH"

    if eff == "XHIGH":
        min_out = 32768
    elif eff == "HIGH":
        min_out = 16384
    else:
        min_out = 0

    return {"thinkingLevel": eff}, eff, min_out
```

### 2.3 SDK (`google-genai`) 変換とサイレント無視の防止（`_sdk_payload_to_kwargs`）
`_sdk_payload_to_kwargs`（1112行目付近）の `thinkingConfig` 変換処理（1163行目付近）を以下のように更新する。古い SDK（Pydantic `extra="ignore"`）で `thinking_level` が黙って無視されるのを防ぐため、フィールド存在チェックを行い、未対応なら即座に `_SdkFallbackToRest` を投げて REST 経路へフォールバックさせる。

```python
    if gc.get("thinkingConfig"):
        tc = gc["thinkingConfig"]
        if "thinkingLevel" in tc and tc["thinkingLevel"]:
            fields = (getattr(t.ThinkingConfig, "model_fields", None)
                      or getattr(t.ThinkingConfig, "__fields__", None) or {})
            if fields and "thinking_level" not in fields:
                raise _SdkFallbackToRest("SDK ThinkingConfig does not support thinking_level")
            try:
                tc_obj = t.ThinkingConfig(thinking_level=str(tc["thinkingLevel"]))
            except Exception:
                raise _SdkFallbackToRest("SDK ThinkingConfig raised error on thinking_level")
            if getattr(tc_obj, "thinking_level", None) is None:
                raise _SdkFallbackToRest("SDK ThinkingConfig ignored thinking_level")
            cfg["thinking_config"] = tc_obj
        elif "thinkingBudget" in tc and tc["thinkingBudget"] is not None:
            cfg["thinking_config"] = t.ThinkingConfig(thinking_budget=int(tc.get("thinkingBudget", 0)))
```

### 2.4 `_is_thinking_400`・`_step2_generation_config`・`generate` の更新
* `_is_thinking_400(msg)`（1618行目付近）：
  判定タプルに `"thinking_level"`, `"thinkinglevel"` を追加する。`_request_with_fallbacks` 本体は既存通り（400時に `thinkingConfig` を `pop`）を維持する。
* `_step2_generation_config(model, max_output_tokens, provider="google", thinking_level="auto")`（1634行目付近）：
  ```python
  def _step2_generation_config(model, max_output_tokens, provider="google", thinking_level="auto"):
      """2段目・直接生成。Vertex の思考モデルや高思考設定時は思考で JSON が切れないよう下限を上げる"""
      tok = int(max_output_tokens or 8192)
      low = str(model or "").lower()
      tc, _, min_out = resolve_thinking_config(model, thinking_level)
      if min_out > 0:
          tok = max(tok, min_out)
      if provider == "vertex" and _is_thinking_model(low):
          tok = max(tok, 16384)
      cfg = {"maxOutputTokens": tok, "responseMimeType": "application/json"}
      if tc:
          cfg["thinkingConfig"] = tc
      return cfg
  ```
* `generate(...)`（1698行目付近）：
  キーワード引数に `thinking_level="auto"` を追加する。
  関数の冒頭で `resolve_thinking_config(model, thinking_level)` を呼び、第2戻り値（実効値）が `"XHIGH"` または `"budget:16384"` のときは `timeout = max(int(timeout or 180), 240)` に延長する。
  関数内の4か所の `_step2_generation_config(...)` 呼び出しすべてに `thinking_level=thinking_level` を渡す。
  1. 1745行目付近（fetch step 1 で fc が無かった場合の直接生成フォールバック）
  2. 1785行目付近（fetch step 2 生成）
  3. 1797行目付近（Vertex tool 400 時の直接生成フォールバック）
  4. 1834行目付近（else 節の通常直接生成）

---

## 3. バックエンド仕様（`server.py`）

### 3.1 `DEFAULT_SETTING` と `merge_defaults`
* `DEFAULT_SETTING`（493行目付近）に以下を追加する（`NO_IDE` には追加せず、チャンネル複製時も設定を引き継ぐ）：
  ```python
  "thinking_level": "auto",           # 本文・睡眠プロセス用: auto / minimal / low / medium / high / xhigh
  "choices_thinking_level": "low",    # 補助タスク用: low / minimal / medium / high / auto / same
  ```
* `merge_defaults(setting)`（888行目付近）の末尾に正規化処理を追加する：
  ```python
  tl = str(out.get("thinking_level") or "auto").strip().lower()
  out["thinking_level"] = tl if tl in google_ai.VALID_THINKING_LEVELS else "auto"
  ctl = str(out.get("choices_thinking_level") or "low").strip().lower()
  out["choices_thinking_level"] = ctl if ctl in google_ai.VALID_CHOICES_THINKING_LEVELS else "low"
  ```

### 3.2 タスク種別ごとの思考レベル解決関数 `active_thinking_level`
`server.py` の `active_api_model`（960行目付近）の直後に追加する。

```python
def active_thinking_level(st, task_type="user"):
    """タスク種別に応じて google_ai.generate へ渡す thinking_level を解決する"""
    st = st or {}
    main_lv = str(st.get("thinking_level") or "auto").strip().lower()
    if main_lv not in google_ai.VALID_THINKING_LEVELS:
        main_lv = "auto"

    if task_type == "user":
        return main_lv

    if task_type == "consolidation":
        # 世代交代の睡眠プロセス（30ターン要約＋時間確定）は浅すぎると精度が落ちるため最低 medium に底上げ
        return "medium" if main_lv in ("minimal", "low") else main_lv

    # 補助タスク: choices / image_refine / memory_extract / memory_regen
    sub_lv = str(st.get("choices_thinking_level") or "low").strip().lower()
    if sub_lv not in google_ai.VALID_CHOICES_THINKING_LEVELS:
        sub_lv = "low"
    resolved = main_lv if sub_lv == "same" else sub_lv

    # 「本文と同じ(same)」で本文が xhigh の場合でも、選択肢や画像推敲が超長考で詰まらないよう high に抑える
    if resolved == "xhigh":
        resolved = "high"

    # 記憶抽出・一括記憶再生成はバックグラウンドで走るため、長考設定でも最大 medium に制限して保護
    if task_type in ("memory_extract", "memory_regen") and resolved in ("high", "xhigh"):
        return "medium"
    return resolved
```

### 3.3 `google_ai.generate` 呼び出し時のモック互換ガード
既存テスト（`test_google_parallel_choices.py` 等）で `google_ai.generate` が固定引数関数にモンキーパッチされている場合に `TypeError` を起こさないよう、`server.py` から `google_ai.generate` を呼ぶ箇所（`run_google_task`: 11897行目付近）では以下の互換呼び出しを行う。

```python
import inspect

def _call_google_generate(model, prompt, **kwargs):
    """既存テストのモック関数が thinking_level 引数を持たない場合でも安全に呼び出す"""
    fn = google_ai.generate
    try:
        sig = inspect.signature(fn)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_var_kw and "thinking_level" not in sig.parameters:
            kwargs.pop("thinking_level", None)
    except (ValueError, TypeError):
        pass
    return fn(model, prompt, **kwargs)
```

`run_google_task`（11897行目付近）：
```python
        thinking_level = active_thinking_level(st, task_type)
        res = _call_google_generate(
            model, prompt, system,
            safety_off=bool(st.get("google_safety_off", True)),
            max_output_tokens=int(st.get("google_max_output_tokens") or 8192),
            fetch=use_fetch, images=images, provider=provider,
            thinking_level=thinking_level,
        )
```

### 3.4 チャンネル設定保存時のロストアップデート保護（`server.py:14798`）
チャンネル設定保存処理（`POST /api/setting`）において、フォームに含まれないキーをサーバー側の現在値で保護する `keep` リスト（14799行目付近）に、`thinking_level` と `choices_thinking_level` を追加する。
これを怠ると、チャンネル設定画面でキャラや概要を保存した際にクライアント側の古い値によって思考設定が消去・初期化される重大バグとなる。

```python
                keep = {k: s["setting"].get(k, v) for k, v in NO_IDE.items()}
                for _pk in (
                    "ai_provider", "google_model", "google_fetch", "google_safety_off",
                    "google_max_output_tokens", "vertex_model", "vertex_choices_model",
                    "thinking_level", "choices_thinking_level",
                ):
                    keep[_pk] = s["setting"].get(_pk)
```

### 3.5 思考レベル即時更新用 API（`POST /api/ai/thinking`）の新設
サイドバーのコンパクトセグメントバーやツールモーダルのピルボタンを操作した際、チャンネル設定全体を保存することなく、即座に思考設定をセッションへ永続化する専用エンドポイントを追加する（`POST /api/ai/provider` でも任意引数として受け付け可能にする）。

```python
            if p == "/api/ai/thinking":
                sid = d.get("sid") or sid or ""
                with lock:
                    s = load(sid)
                    st = s["setting"]
                    if "thinking_level" in d:
                        tl = str(d.get("thinking_level") or "auto").strip().lower()
                        if tl in google_ai.VALID_THINKING_LEVELS:
                            st["thinking_level"] = tl
                    if "choices_thinking_level" in d:
                        ctl = str(d.get("choices_thinking_level") or "low").strip().lower()
                        if ctl in google_ai.VALID_CHOICES_THINKING_LEVELS:
                            st["choices_thinking_level"] = ctl
                    save(s)
                return self.send_json({
                    "ok": True,
                    "thinking_level": s["setting"]["thinking_level"],
                    "choices_thinking_level": s["setting"]["choices_thinking_level"],
                })
```

※インフライトタスクのキャンセル方針：
思考レベルの変更操作のみでは、生成中のリクエストを自動キャンセルしない（誤操作による破棄を防止）。変更は次回の生成リクエストから即時適用される。「再生成」ボタンが押された場合のみ既存タスクがキャンセルされ、新しい思考レベルで再生成される。

---

## 4. フロントエンド仕様（`public/`）

### 4.1 UI配置の再編（3か所→2か所へ集約）
1. **ツールモーダル（統合AIコントロールパネル: `#aiToolModal`）**:
   * **モーダルID**: `<div id="aiToolModal" class="modal hidden">`
   * **開閉トリガー**:
     - チャンネル設定画面内の `[⚡ AI・モデル・認証設定を開く]` ボタン
     - 画面下部アクションバーの「🤖モデル」ボタン（`#revealModel`）（※従来の `#modelSheet` の機能を包含・統合）
     - サイドバーの AI サマリ行の歯車/設定リンク
   * **プロバイダ選択タブ**: `Antigravity IDE` / `Google AI Studio` / `Google Cloud Vertex`
   * **認証ステータス＆折りたたみフォーム（Google / Vertex）**:
     - 設定済みの場合は `🟢 APIキー設定済 (key_hint) [認証設定を変更 ▾]` または `🟢 Vertex AI 設定済 (project / location) [変更 ▾]` と1行に折りたたむ。
     - 未設定の場合はフォームを自動展開し、下のモデル・思考カードを `.is-unauthenticated`（半透明・操作無効）にする。
     - Vertex AI の `location_note`（404 による `global` 自動切替通知）がある場合は警告行を表示する。
     - 従来の `googleKeyModal` や `vertexConfigModal` の中身をアコーディオンとしてインライン展開し、モーダル多重起動を排除する。
   * **本文・状況整理カード**:
     - 本文モデル選択 `<select id="googleModel">` / `<select id="vertexModel">` と「モデル一覧を更新」ボタン
     - 思考の深さ 2段ピルボタン（`自動` / `💨最小` / `⚡低` / `⚖️中` / `🔍高` / `🔥最大`）
     - 実効値バッジ（例: `実効: LOW (最小は非対応のため丸め)`、`実効: budget:4096 (中)`）
     - 1行詳細ガイド文
   * **補助タスク（選択肢8択・画像推敲・記憶抽出）カード**:
     - 選択肢モデル選択 `<select id="googleChoicesModel">` / `<select id="vertexChoicesModel">`
     - 補助タスクの思考ピルボタン（`⚡低(速・推奨)` / `💨最小` / `⚖️中` / `🔍高` / `🔗本文と同じ` / `自動`）＋ 実効値バッジ ＋ 1行ガイド
   * **API詳細パラメータカード**:
     - `google_max_output_tokens`（横に自動底上げ後の実効上限バッジを表示）、`google_fetch`、`google_safety_off`
2. **サイドバー（日常操作用コンパクト枠）**:
   * 本文モデル `<select>` の直下に、横幅 100% のコンパクトセグメントバー（`自動|最小|低|中|高|最大`）を配置。
   * 補助タスク思考設定のミニ `<select>` を配置。
   * 選択変更時は即座に `/api/ai/thinking` を叩き、ツールモーダル側へもリアルタイム双方向同期する。
3. **チャンネル設定画面（AI設定の撤去と1行ショートカット化）**:
   * チャンネル設定画面内にあった AIプロバイダ・認証・モデル選択UIをツールモーダルへDOM移動する。
   * 跡地には以下の1行サマリ・ショートカットのみを配置する：
     ```html
     <div class="box">
       <div class="box-title">AI・生成エンジン</div>
       <div class="ai-summary-row">
         <span id="aiSummaryText">Google Gemini 3.8 Flash (思考: 低)</span>
         <button id="openAiToolBtn" type="button" class="sub">⚡ AI・モデル・認証設定を開く</button>
       </div>
     </div>
     ```

### 4.2 フロントエンド実装時の必須ガード条件（バグ防止）
1. **既存DOM要素の `id` 保持**:
   チャンネル設定画面からツールモーダルへ要素を移設する際、既存JSが参照している `id` 属性（`aiProvider`, `googleBox`, `googleModel`, `vertexModel` 等）は一切変更せず、DOM構造のみをツールモーダルへ移動させること。
2. **チャンネル設定保存時のロストアップデート防止**:
   フロントエンドの `readForm()` においても、`state.setting` が持つ AI 関連キーが漏れなく保持されていることを確認すること（バックエンド側の 3.4 の保護と二重化）。
3. **モデル `<select>` 変更時のプレビュー再計算**:
   サイドバーまたはツールモーダルでモデル `<select>` が変更された際、`ThinkingUI.render(currentSession.setting)` を呼び出し、変更後のモデルに合わせて実効値バッジと非対応段の半透明表示（`.is-rounded`）を即時更新すること。
4. **ツールモーダル開窓時の初期化**:
   ツールモーダルを開く関数の中で、必ず認証ステータスの確認・モデル一覧の反映・`ThinkingUI.render(currentSession.setting)` を実行すること。

---

## 5. 受け入れ要件（Acceptance Criteria）とテスト計画

### A. バックエンド単体テスト（`google_ai.py` / `server.py`）
- [ ] **AC-1（既存テスト100%通過）**: 既存の全704件のテストスイートがエラーなくパスすること。
- [ ] **AC-2（モデル別の正規化・丸め検証）**:
  - `gemini-4-hydrogen`: `minimal` → `{"thinkingLevel": "LOW"}` に丸め / `xhigh` → `{"thinkingLevel": "XHIGH"}` かつ `maxOutputTokens >= 32768`, `timeout >= 240`
  - `gemini-3.8-flash` / `gemini-3-pro` / `gemini-flash-latest`: `minimal` → `"LOW"` / `xhigh` → `"HIGH"` に丸め
  - `gemini-3.5-flash` / `gemini-3.1-flash-lite`: `minimal` → `"MINIMAL"` / `xhigh` → `"HIGH"` に丸め
  - `gemini-2.5-flash`: `minimal` → `{"thinkingBudget": 0}` / `low` → `1024` / `medium` → `4096` / `high` → `8192`（`maxOutputTokens >= 16384`）
  - `gemini-2.5-pro`: `minimal` → `{"thinkingBudget": 128}`
  - `gemini-2.0-flash` / `gemini-2.0-flash-thinking-exp` / `gemma-3-27b` / `gemini-1.5-flash-latest`: いずれのレベルでも `thinkingConfig` が付与されない（`None`）こと
- [ ] **AC-3（`auto` 時の完全後方互換）**: `thinking_level="auto"` のとき、`_step2_generation_config` が `thinkingConfig` を付与せず、既存の `maxOutputTokens` ロジックと完全一致すること。
- [ ] **AC-4（SDK非対応時のREST透過フォールバック）**: `ThinkingConfig` が `thinking_level` を受け付けない（または無視する）環境で `_SdkFallbackToRest` が送出され、REST API 経由で正常にリクエストが送信されること。
- [ ] **AC-5（タスク別の思考レベル解決）**:
  - 本文が `minimal` または `low` のとき、`consolidation`（睡眠プロセス）では `medium` に底上げされること。
  - 本文が `xhigh` かつ補助タスクが `same` のとき、`choices` / `image_refine` は `high`、`memory_extract` / `memory_regen` は `medium` にキャップされること。
- [ ] **AC-9（チャンネル設定保存 API での思考レベル保護）**:
  `/api/setting` 呼び出し時に、サーバー側の `keep` リストに `thinking_level` と `choices_thinking_level` が含まれており、クライアントからのフォーム保存によって思考設定が消去・上書きされないこと。
- [ ] **AC-10（思考レベル即時保存 API の動作確認）**:
  `/api/ai/thinking` に POST することで、セッション設定に思考レベルが即座に永続化されること。

### B. フロントエンド・UIテスト
- [ ] **AC-6（サイドバーとツールモーダルの双方向リアルタイム同期）**:
  サイドバーとツールモーダルのどちらでモデルや思考レベルを変更しても、両方のUIの選択状態・実効値バッジ・ガイド文・出力トークン実効上限バッジが即座に同期し、サーバーへ保存されること。
- [ ] **AC-7（認証アコーディオンとロック連動）**:
  APIキーまたはVertex認証が設定済みのときは認証欄が1行に折りたたまれ、未設定時は自動展開されてモデル・思考カードが操作ロックされること。
- [ ] **AC-8（チャンネル設定保存時の非破壊確認）**:
  ツールモーダルでプロバイダ・モデル・思考レベルを変更したあと、チャンネル設定画面を開いてキャラ設定等を保存しても、ツールモーダル側の設定が一切リセットされないこと。

