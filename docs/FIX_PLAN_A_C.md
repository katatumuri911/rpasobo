# FIX PLAN A〜C（即効修正・CSS・テスト）

**作成日**: 2026-11-21
**対象**: `docs/AUDIT_REPORT.md` の指摘のうち、A（1行中心の即効修正）／B（CSSのみ）／C（テストのみ）に分類した14件
**関連**: `docs/AUDIT_REPORT.md`

---

## 0. 前提と注意

- `server.py` は別作業（turn_index 移行）の未コミット差分がある。**行番号は執筆時点のものであり、実装時に必ず `grep -n` で再確認すること。**
- 行番号の代わりに「関数名・検索アンカー」も併記している。アンカーで検索するのが安全。
- 本ドキュメントは計画のみ。コード変更は含まない。
- `git checkout` / `git restore` で作業ツリー全体を戻さないこと（他作業の差分を壊す）。

### 検索アンカー一覧

| ID | アンカー |
|:--|:--|
| L-05 | `build_prompt` 内 `raw_old.get("inflight_mid") == m.get("id")` |
| L-03 | outbox choices 最終フォールバック `m.get("choices_status") in ("pending", "processing") or m.get("status") in ("pending", "processing")` |
| F-07 | `run_google_task` 冒頭 `task_type not in ("user", "choices", ...)` |
| L-07 | `clean_import` / `session_needs_migration` / `migrate_session` / `summary_of` の image 参照 |
| L-09 | `_decensor_validate` 内 `(w != frm_word or w != to)` |
| F-11 | `favBtn.addEventListener("mousedown"` |
| F-06 | `_google_prepare_attachments` 内 `raw = open(path, "rb").read()` |
| F-09 | `$("messageInput").addEventListener("blur"` 内 `hideAgQuota()` |
| P-01 | `isWaiting ? 100 : 1500` / `setInterval(refreshSessions, 3000)` |
| P-08 | `outbox_worker` 内 `time.sleep(0.3)` |
| U-07 | `style.css` の `.sess-sub` / `.meta` / `.rag-mem-meta` / `.folder-row .st` |
| U-06 | `style.css` の `.sess-rename {` |
| F-10 | `test/test_mobile_layout.cjs` の `function lastMediaBlock` |
| F-12 | `test/test_rag_hits_modal_ui.cjs` の `this.style = {};` |

---

## 1. 判定サマリ

凡例: ✅ 妥当（そのまま実装可）／⚠️ 不完全（追加変更が必要）／❌ 対象外（簡単修正ではない）

| 分類 | ID | 判定 | 工数 | 理由 |
|:--:|:--|:--:|:--:|:--|
| A | L-05 | ✅ | 1分 | `bool(m.get("id"))` 追加のみ。既存テストに影響なし |
| A | L-03 | ✅ | 1分 | 最終フォールバックの条件削除のみ。ただし再現経路は送信ガードでほぼ塞がれている |
| A | F-07 | ✅ | 1分 | 呼び出し元は `_google_pick_tasks` 経由の1箇所のみ |
| A | L-07 | ⚠️ | 10分 | 1行の `isinstance` では `summary_of()` の500が残る。4箇所の正規化が必要 |
| A | L-09 | ✅ | 1分 | 条件は恒真で `w in to` と同値。連鎖警告テストも通る |
| A | F-11 | ✅ | 1分 | テストは `pointerdown` の文字列一致のみ要求。開閉は `touchend`/`click` 側 |
| A | F-06 | ✅ | 2分 | `with open` 化のみ |
| A | F-09 | ✅ | 1分 | ガードしても `hideAgQuota()` の文字列は残るため `test_composer_ui.py` も通る |
| A | P-08 | ✅ | 15分 | 起床イベント付き段階バックオフ。**操作後10分（600秒）は通常速度**、10分超で5秒 |
| A | P-01(client) | ✅ | 5分 | 数値2箇所＋重複テスト式の更新。ただしサーバー側は別タスク |
| B | U-07 | ✅ | 5分 | 背景 `#1a1a1e` でコントラスト不足を確認。色変更のみ |
| B | U-06 | ⚠️ | 10分 | 押し判定拡大だけでは不可。タッチ端末で `opacity` を戻す追記が必須 |
| C | F-10 | ✅ | 10分 | 全 `@media` ブロック走査案で **PASS 80 / FAIL 0** を検証済み |
| C | F-12 | ✅ | 5分 | 2行追加で **ALL UI tests PASSED** を検証済み |

---

## 2. A: 即効修正の詳細

### A-1. L-05: `None == None` による `prev_sent` 誤復元

- **ファイル**: `server.py`
- **関数**: `build_prompt`
- **アンカー**: `raw_old.get("inflight_mid") == m.get("id")`
- **現状**:

```python
if isinstance(m, dict) and raw_old.get("inflight_mid") == m.get("id") and isinstance(raw_old.get("prev_sent"), dict) and raw_old.get("prev_sent"):
```

- **変更後**:

```python
if isinstance(m, dict) and bool(m.get("id")) and raw_old.get("inflight_mid") == m.get("id") and isinstance(raw_old.get("prev_sent"), dict) and raw_old.get("prev_sent"):
```

- **理由**: `m.get("id")` と `raw_old.get("inflight_mid")` が両方 `None` のとき `None == None` が成立し、前回の `prev_sent` を誤って復元する。
- **リスク**: 実メッセージは必ず `id` を持つため、正常系への影響なし。
- **確認**: `python -m unittest test.test_lapi_speedup_plan`

---

### A-2. L-03: 選択肢が別ターンのユーザー発言に紐付く

- **ファイル**: `server.py`
- **場所**: outbox の choices 取り込み、**最終フォールバック**（`if not target_asst and not target_user:` の2つ目のループ）
- **アンカー**:

```python
if m["role"] == "user" and (m.get("choices_status") in ("pending", "processing") or m.get("status") in ("pending", "processing")):
```

- **変更後**:

```python
if m["role"] == "user" and m.get("choices_status") in ("pending", "processing"):
```

- **重要**:
  - 直前に `if m["id"] == reply_to ...` のループ内にある `else:` 側（`reply_to` が最後のユーザー発言のとき）の `status` 判定は**残す**。ここは並列選択肢の正当な宛先判定。
  - 削除対象は上記「逆順フォールバック」の1箇所のみ。
  - `status` 単独の判定は `server.py` 内に複数ある（送信ガード、`has_pending`、`p_user` 探索など）。**文字列の一括置換は禁止**。必ず `if m["role"] == "user" and (m.get("choices_status")` で始まる行を個別に確認する。
- **リスク**: `reply_to` が欠落した choices ファイルは、選択肢待ちのユーザー発言が無い限り取り込まれなくなる（誤結合よりは安全側）。
- **副作用（要対応）**: 宛先が見つからない choices ファイルは現状 `DONE` へ無言で移動する（L-08と同じ無通知）。L-03単独では「間違って付く」を「黙って消える」に変えるだけなので、**L-08（空choices／宛先不明のエラー通知）とセットで入れる**のが望ましい。
- **確認**: `python -m unittest test.test_choices_slots test.test_parallel_choices`
- **補足**: `/api/send` の二重送信ガードにより、報告された同時送信シナリオは再現しにくくなっている。退行防止としての修正。

---

### A-3. F-07: Google AI の受付タスクに動かない機能名

- **ファイル**: `server.py`
- **関数**: `run_google_task`
- **現状**:

```python
if task_type not in ("user", "choices", "image_refine", "memory_extract", "consolidation") \
        and mid in ("user", "choices", "image_refine", "memory_extract", "consolidation"):
```

- **変更後**:

```python
if task_type not in ("user", "choices", "image_refine") \
        and mid in ("user", "choices", "image_refine"):
```

- **根拠**: `run_google_task` の呼び出しは `process_outbox` から `_google_pick_tasks` の結果を渡す1箇所のみ。`_google_pick_tasks` は `user` / `image_refine` / `choices` しか返さない。`_google_build_prompt` は `memory_extract` / `consolidation` で `ValueError("unknown task")` を投げる。
- **リスク**: なし（未使用分岐の削除）。
- **確認**: `python -m unittest test.test_google_ai test.test_google_parallel_choices`

---

### A-4. L-07: 旧形式（文字列）image の取込・読み込み

- **ファイル**: `server.py`
- **判定**: 1行の `isinstance` ガードでは**不完全**。以下4箇所をセットで修正する。

#### (1) `clean_import`（アンカー: `if m.get("image") and m["image"].get("status") in ("queued", "generating"):`）

- **変更後**:

```python
im = m.get("image")
if isinstance(im, str) and im.strip():
    # 旧形式: 画像パスの文字列だけが入っている
    m["image"] = {"file": im.strip(), "status": "done"}
elif isinstance(im, dict) and im.get("status") in ("queued", "generating"):
    m["image"]["status"] = "error"
    m["image"]["error"] = "取り込み時に未完成"
```

#### (2) `session_needs_migration`（アンカー: `im = m.get("image")`）

- **追加**:

```python
im = m.get("image")
if isinstance(im, str) and im.strip():
    return True   # 旧形式。migrate_session で正規化する
```

#### (3) `migrate_session`（アンカー: `im = m.get("image")`）

- **変更後**:

```python
im = m.get("image")
if isinstance(im, str) and im.strip():
    m["image"] = {"file": im.strip(), "status": "done"}
elif isinstance(im, dict):
    details = {k: v for k, v in im.items() if k not in ("file", "status", "error")}
    if details:
        cur_im = idata.get(mid, {})
        cur_im.update(details)
        idata[mid] = cur_im
        images_count += 1
    m["image"] = {k: v for k, v in im.items() if k in ("file", "status", "error")}
```

#### (4) `summary_of`（アンカー: `"generating": any((m.get("image") or {}).get(...)`）

- **変更後**:

```python
"generating": any(
    isinstance(m.get("image"), dict)
    and m["image"].get("status") in ("queued", "generating")
    for m in msgs
),
```

- **理由**: 文字列imageをそのまま残すと、`(m.get("image") or {}).get(...)` が `AttributeError` になり `/api/sessions` が500になる。旧セッションは `session_needs_migration` が文字列を検知しないため、マイグレーションも走らない。
- **読み込み経路の補足**: `load()` は `readonly=True` でも `session_needs_migration()` が True なら `migrate_session()` を実行する（既存挙動）。したがって (2) の検知を追加すれば、`summary_of()` 呼び出し前に正規化される。ただし**プロセス起動時点で `session_cache` に残っているセッションは再読込されない**ため、(4) の防御は残す。
- **リスク**: 旧画像ファイルが実在しない場合も `status: "done"` になる。必要なら `os.path.isfile` で `"error"` に振り分けるが、初回は表示優先で `"done"` を推奨。
- **確認**: 文字列 `image` を含む旧JSONを `/api/session/import` → `/api/sessions` が500にならないこと。追加テスト参照。

---

### A-5. L-09: 伏せ字解除の恒真条件

- **ファイル**: `decensor_engine.py`
- **関数**: `_decensor_validate` 内の警告ループ
- **現状**:

```python
for frm, to in words.items():
    frm_word, _ = _split_context_key(frm)
    inner = [w for w in target_words if (w != frm_word or w != to) and w in to]
```

- **変更後**:

```python
for frm, to in words.items():
    inner = [w for w in target_words if w in to]
```

- **理由**: `frm == to` は直前で除外済みのため `(w != frm_word or w != to)` は常に真。`w in to` と同値。
- **注意**: `or` を `and` に変えてはいけない（自己連鎖・推移連鎖の警告が消える）。
- **確認**: `python -m unittest test.test_decensor`
  - 胸→突起: 警告あり
  - 突起→乳首: 警告なし
  - 突起|股→クリトリス: 警告なし

---

### A-6. F-11: お気に入りモデルボタンのタッチ反応

- **ファイル**: `public/app.js`
- **アンカー**: `favBtn.addEventListener("mousedown"`
- **変更**:

```js
favBtn.addEventListener("mousedown", (e) => {
```
→
```js
favBtn.addEventListener("pointerdown", (e) => {
```

- **根拠**: このハンドラは `preventDefault()` 専用（開く処理は `touchend` と `click`）。`pointerdown` はマウス・タッチ・ペンすべてで発火する。
- **リスク**: `pointerdown` の `preventDefault` で互換マウスイベント（`mousedown`）は抑止されるが、`click` は発火するため開閉は維持される。念のため実機でPCクリック＋スマホタップを確認。
- **確認**: `node test/test_model_favorites_ui.cjs` → 87 PASSED / 0 FAILED

---

### A-7. F-06: 添付読み込みのファイルディスクリプタ未クローズ

- **ファイル**: `server.py`
- **関数**: `_google_prepare_attachments`
- **現状**:

```python
raw = open(path, "rb").read()
```

- **変更後**:

```python
with open(path, "rb") as fp:
    raw = fp.read()
```

- **リスク**: なし。
- **確認**: `python -m unittest test.test_google_ai`

---

### A-8. F-09: テスト環境で `hideAgQuota` 未定義

- **ファイル**: `public/app.js`
- **アンカー**: textarea の `blur` ハンドラ内 `hideAgQuota();`
- **変更**:

```js
hideAgQuota();
```
→
```js
if (typeof hideAgQuota === "function") hideAgQuota();
```

- **補足**: 同様の呼び出しが他に2箇所ある（`fadeAgQuota()` のタイマー内と `send()` 冒頭）。どちらもフォーカス処理ではない。テストが踏むのは blur の1箇所だが、`send()` 側は将来テストから呼ばれる可能性があるため、余裕があれば3箇所すべてガードする。
- **リスク**: なし（未定義時は何もしない）。
- **確認**:
  - `node test/test_enter_send.cjs` → 12 PASS / 0 FAIL
  - `python -m unittest test.test_composer_ui`（`hideAgQuota()` の文字列assertを維持）

---

### A-9. P-08: outbox ワーカーのアイドル時バックオフ

- **ファイル**: `server.py`
- **関数**: `outbox_worker` / `do_POST`
- **判定**: 実装可。ただし「寝る時間を伸ばすだけ」は不可。**起床イベントとセット**で入れる。
- **前提（コード確認済み）**:
  - `process_outbox()` の戻り値は `imported`。OUTBOX が空、またはファイルが未完成でまだ取り込めない間は `False`。
  - outbox へ書くのは外部プロセス（LocalAI/IDE連携）なので、外部からの書き込みではイベントを立てられない。
  - ただし**ユーザー操作はすべて POST**（送信・再生成・選択肢・編集）なので、`do_POST` でイベントを立てれば「操作直後の返答」は即座に拾える。
  - Google/Vertex の結果は同一プロセスの `google_worker` が `process_outbox()` を直接呼ぶため、元々遅延しない。
- **設計（確定仕様）**:
  - **操作後10分（600秒）は現状同等（0.05→0.3秒）を維持する。**
  - 10分を超えて操作が無い場合のみ 5秒間隔に落とす（60〜600秒は1秒）。
  - `threading.Event` で起床したら即座に fast に戻す。
- **変更後コード**:

```python
# 定数定義の近く（poke_event / google_event の並び）
OUTBOX_ACTIVE_WINDOW = 600   # 操作後10分は通常速度（確定仕様）
OUTBOX_DEEP_IDLE = 5.0       # 10分以上アイドル時の間隔
outbox_event = threading.Event()
_outbox_active_until = 0.0


def touch_outbox_activity():
    """ユーザー操作があったことを記録し、outbox ワーカーを即時起床させる"""
    global _outbox_active_until
    _outbox_active_until = time.time() + OUTBOX_ACTIVE_WINDOW
    outbox_event.set()


def _outbox_delay(empty_sec, active):
    """空 outbox を次に走査するまでの待ち時間（秒）"""
    if active:
        return 0.05 if empty_sec < 0.3 else 0.3   # 従来と同じ
    if empty_sec < 60:
        return 0.3
    if empty_sec < OUTBOX_ACTIVE_WINDOW:
        return 1.0
    return OUTBOX_DEEP_IDLE                        # 10分以上: 5秒


def outbox_worker():
    empty_since = time.time()
    while True:
        try:
            had_files = process_outbox()
        except Exception as e:
            print(f"[outbox worker] {e}")
            had_files = False
        if had_files:
            empty_since = time.time()
            continue                               # 連続ファイルは即処理
        active = time.time() < _outbox_active_until
        delay = _outbox_delay(time.time() - empty_since, active)
        if outbox_event.wait(delay):
            empty_since = time.time()              # 起床したら fast に戻す
            outbox_event.clear()
```

- **`do_POST` への1行**（`p = u.path` の直後）:

```python
touch_outbox_activity()
```

- **効果**: 空 outbox の走査が 3.3回/秒 → 0.2回/秒（10分以上アイドル時、約94%減）。
- **正直な注記**: `poke_worker` も 0.3秒で回っているため、アプリ全体の起床回数がゼロになるわけではない。CPU効果は小さいが、電池・低スペック端末での無駄な起床は減る。
- **トレードオフ**: 10分以上応答待ちが続く外部生成では、完了検知が最大5秒遅れる。
  - ただし ComfyUI の画像/動画生成は `comfy_run(timeout=900)` を**同一プロセス内でポーリング**しており、outbox ワーカーを通らない。P-08 の影響を受けない。
  - outbox 経由（LocalAI/IDE連携）はチャット返答と選択肢が中心で、10分超は稀。通常の返答（10分以内）は従来通り0.3秒。
- **テスト**: `_outbox_delay()` を純関数として切り出したので、境界値をユニットテストできる。
  - `(0, True) → 0.05` / `(10, True) → 0.3`
  - `(30, False) → 0.3` / `(60, False) → 1.0` / `(600, False) → 5.0`
- **統合確認**: 10分アイドル相当にした後、送信→返答が1秒以内に表示されること。
- **確認**: `python -m unittest test.test_outbox_backoff`（新規）

---

### A-10. P-01（クライアント側のみ）: 待機中 100ms ポーリング

- **ファイル**: `public/app.js`
- **変更**:

```js
const delay = isErr ? (hidden ? 5000 : 1500) : (hidden ? (isWaiting ? 1000 : 5000) : (isWaiting ? 100 : 1500));
```
→
```js
const delay = isErr ? (hidden ? 5000 : 1500) : (hidden ? (isWaiting ? 1000 : 5000) : (isWaiting ? 450 : 1500));
```

```js
setInterval(refreshSessions, 3000);
```
→
```js
setInterval(refreshSessions, 10000);
```

- **テスト側の同時更新**（整合のため推奨）: `test/test_poll_error_ui.cjs` は遅延式を**複製**しているため、app.js を変えてもこのテストは落ちないが、値を合わせないと仕様の二重管理になる。

```js
return isErr ? (hidden ? 5000 : 1500) : (hidden ? (isWaiting ? 1000 : 5000) : (isWaiting ? 450 : 1500));
```
```js
assert.strictEqual(computeDelay(false, true, false), 450, "waiting in foreground = 450ms");
```

- **根拠**: `wakePoll()` が送信後・操作後に即時ポーリングするため、450ms でも操作直後の反映は遅れない。`pollProgress` は別途 800ms。
- **リスク**: 画像生成完了などの検知が最大450ms遅れる（許容範囲）。
- **UX副作用**: `refreshSessions` を10秒にすると、サイドバーの未読ドット・⏳/🎨バッジの更新も最大10秒遅れる。操作後は個別に `refreshSessions()` が呼ばれるため実害は小さいが、必要なら `document.hidden` 時のみ間隔を延ばす。
- **注意**: これは**クライアント側のみ**。サーバーの `/api/sessions` 全件 `load()` と `/api/session` の `listdir` は別タスク（P-01本体）。
- **確認**: 送信→返答表示の体感、`test_poll_error_ui.cjs`。

---

## 3. B: CSS修正の詳細

### B-1. U-07: ダークテーマの低コントラスト

- **ファイル**: `public/style.css`
- **背景**: `body { background: #1a1a1e; }`、サイドバー `#1f1f24`
- **変更一覧**:

| セレクタ | 現在 | 変更後 | 根拠 |
|:--|:--|:--|:--|
| `.sess-sub` | `color: #777` | `color: #a8a8b2` | 3.7:1 → 約5.1:1 |
| `.meta` | `color: #666` | `color: #9e9ea6` | 2.9:1 → 約4.6:1 |
| `.rag-mem-meta` | `color: #777` | `color: #a0a0aa` | 4.5:1以上 |
| `.folder-row .st` | `color: #888` | `color: #a0a0aa` | 4.5:1以上 |

- **任意**: `.rag-mem-history summary` の `#888` も同様に `#a0a0aa` へ。監査対象外だが `.muted`（`#888`）も同じ背景で使われるため、まとめて `#a0a0aa` にすると統一感が出る。
- **注意（RP背景）**: `#chatLog.rp` はユーザー設定の背景画像を重ねるため、`.meta` の実効コントラストは背景画像に依存する。今回の変更は「既定のダーク背景で4.5:1以上」を保証するもので、背景画像使用時はカード側のオーバーレイ濃度で担保する（既存仕様）。
- **リスク**: 見た目のみ。テストに色依存なし。
- **確認**: ダーク背景で日付・補足テキストが読めること。DevToolsのコントラストチェッカーで4.5:1以上。

---

### B-2. U-06: 名前変更ボタンのタップ領域

- **ファイル**: `public/style.css`
- **現状**:

```css
.sess-rename { background: none; border: none; padding: 0 2px; font-size: 12px; line-height: 1; cursor: pointer; color: inherit; opacity: 0; }
```

- **変更後**:

```css
.sess-rename {
  background: none; border: none; font-size: 12px; line-height: 1; cursor: pointer; color: inherit;
  opacity: 0;
  padding: 8px; margin: -8px;                       /* 見た目を変えず押し判定だけ拡大 */
  min-width: 36px; min-height: 36px;
  display: inline-flex; align-items: center; justify-content: center;
}
```

- **必須の追加**（これが無いとタッチ端末ではボタンが消えたまま）:

```css
@media (hover: none), (pointer: coarse) {
  .sess-rename { opacity: .6; }
}
```

- **リスク**: `min-width: 36px` は `.badges`（`flex-shrink: 0`）を広げるため、長いタイトルを圧迫する可能性がある。レイアウトを動かしたくない場合は、`min-width` を使わず疑似要素で当たり判定だけを広げる方式が安全:

```css
.sess-rename { position: relative; }
.sess-rename::before {
  content: "";
  position: absolute;
  inset: -12px;      /* 見た目は変えず 24px 以上を確保 */
}
```

  疑似要素はボタンの一部としてクリック判定される。`opacity: 0` でもヒットテストは有効。どちらを採用するかは実機の見た目で決める。
- **確認**: スマホ幅で鉛筆が常時見え、タップで会話切り替えが起きないこと。`min-width` 方式なら行の高さ・タイトル折返しも確認。

---

## 4. C: テスト修正の詳細

### C-1. F-10: モバイルCSSテストの対象ブロック誤り

- **ファイル**: `test/test_mobile_layout.cjs`
- **問題**: `lastMediaBlock` が**最後の** `@media (max-width: 768px)` だけを見るため、`#mobileActionToggle` 等を宣言している大きなブロックを見落とす。
- **変更**: `lastMediaBlock` を以下の全ブロック走査版に置換（検証済み）。

```js
function lastMediaBlock(text) {
  // すべての @media (max-width: 768px) ブロックを集め、対象セレクタを含むものを返す
  const blocks = [];
  let from = 0;
  for (;;) {
    const at = text.indexOf("@media (max-width: 768px)", from);
    if (at < 0) break;
    const open = text.indexOf("{", at);
    let depth = 0;
    let end = -1;
    for (let i = open; i < text.length; i++) {
      if (text[i] === "{") depth++;
      else if (text[i] === "}" && --depth === 0) { end = i; break; }
    }
    if (end < 0) throw new Error("@media ブロックの閉じ括弧が無い");
    blocks.push(text.slice(open + 1, end));
    from = end + 1;
  }
  if (!blocks.length) throw new Error("モバイル用 @media が無い");
  return blocks.find((b) => b.includes("#mobileActionToggle")) || blocks[blocks.length - 1];
}
```

- **検証結果**: `PASS 80 / FAIL 0`（変更前は 75 / 5）
- **注意（ヒューリスティック）**: 返すブロックを「`#mobileActionToggle` を含むもの」で選んでいるため、将来このIDが改名・移動されると最後のブロックにフォールバックし、同じ誤判定が再発する。関数名も `lastMediaBlock` から `mobileBlockFor` 等に改名し、コメントで意図を残すのが望ましい。
- **確認**: `node test/test_mobile_layout.cjs`

---

### C-2. F-12: RAGヒットモーダルテストのスタブ不足

- **ファイル**: `test/test_rag_hits_modal_ui.cjs`
- **変更1**: `StubEl` の style モック

```js
this.style = {};
```
→
```js
this.style = { removeProperty() {} };
```

- **変更2**: 既存の `getOrCreate("lightbox").classList.add("hidden");` の直後に追加

```js
getOrCreate("wizardRoot").classList.add("hidden");
```

- **理由**: `closeTopmostLayer()` が最初に `wizardRoot` を見るため、スタブが無いと「ウィザードが開いている」と誤判定し、`ragHitsModal` を閉じない。`removeProperty` だけでは次のassertで落ちる。
- **検証結果**: `ALL UI tests PASSED!`
- **確認**: `node test/test_rag_hits_modal_ui.cjs`

---

## 5. 実装順チェックリスト

| Phase | 内容 | 目的 |
|:--:|:--|:--|
| 1 | 1行修正 7件（L-05 / L-03 / F-07 / L-09 / F-11 / F-06 / F-09） | 挙動を変えない安全な修正 |
| 2 | CSS 2件（U-07 / U-06） | 見た目・タッチ操作 |
| 3 | テスト 2件（F-10 / F-12） | テストの誤判定修正 |
| 4 | L-07 / P-01（クライアント） | 挙動が変わる修正＋回帰テスト |
| 5 | P-08 | アイドル時の省電力（最後） |

### Phase 1: 1行修正（Aの7件）
- [ ] L-05: `bool(m.get("id"))`
- [ ] L-03: 最終フォールバックの `status` 条件削除
- [ ] F-07: 受付タプルを3種に
- [ ] L-09: `w in to` に簡約
- [ ] F-11: `pointerdown`
- [ ] F-06: `with open`
- [ ] F-09: `typeof` ガード

完了条件: 対象ユニットテスト＋JSテストが全て成功。

### Phase 2: CSS（Bの2件）
- [ ] U-07: 4セレクタの色変更
- [ ] U-06: 押し判定拡大＋`@media (hover: none)` で表示
完了条件: スマホ幅で表示・タップを実機確認。

### Phase 3: テスト（Cの2件）
- [ ] F-10: `lastMediaBlock` 置換 → 80/0
- [ ] F-12: 2行追加 → ALL PASS
完了条件: 両テストが0 FAIL。

### Phase 4: 追加変更（Aの残り）
- [ ] L-07: 4箇所の正規化＋回帰テスト
- [ ] P-01: クライアント数値＋`test_poll_error_ui.cjs` 更新
完了条件: `/api/sessions` が500にならない。ポーリングテスト成功。

### Phase 5: P-08（性能・最後）
- [ ] `outbox_event` / `touch_outbox_activity()` / `_outbox_delay()` を追加
- [ ] `do_POST` の先頭で `touch_outbox_activity()`
- [ ] `test/test_outbox_backoff.py` を追加
完了条件: **操作後10分（600秒）は0.05→0.3秒を維持**、10分超で5秒間隔、送信直後は0.05秒に復帰すること。

> F-13 は外部バンドル依存のため本プランから削除（§8参照）。

---

## 6. 検証コマンド

```bat
:: Python ユニット
python -m unittest test.test_decensor test.test_choices_slots test.test_google_ai test.test_lapi_speedup_plan
python -m unittest test.test_composer_ui

:: JS / UI
node test\test_mobile_layout.cjs        :: PASS 80 / FAIL 0
node test\test_rag_hits_modal_ui.cjs    :: ALL UI tests PASSED
node test\test_model_favorites_ui.cjs   :: 87 PASSED, 0 FAILED
node test\test_enter_send.cjs           :: 12 PASS / 0 FAIL
node test\test_poll_error_ui.cjs

:: 全体
python tools\run_tests.py               :: 収集0件NGが無いこと
```

---

## 7. 追加すべき回帰テスト

| 対象 | テスト内容 | 置き場所（案） |
|:--|:--|:--|
| L-05 | `raw_old = {"prev_sent": {...}}`、`m = {}` で `prev_sent` が復元されない | `test/test_lapi_speedup_plan.py` |
| L-07 | 文字列 `image` を含むJSONを `clean_import` → `summary_of()` が例外を出さない | `test/test_choices_slots.py` or 新規 |
| L-07 | `session_needs_migration` が文字列imageで True を返す | 同上 |
| L-03 | 旧ユーザー宛choices + 新ユーザーpending で、新ユーザーに `staged_choices` が付かない | `test/test_choices_slots.py` |
| L-08（任意） | 空choicesのoutboxで `choices_status="error"` と `choices_error` が立つ | `test/test_outbox_contract.py` |

---

## 8. 対象外・別タスク

以下はA〜Cに含めない。P-01サーバー側、U-04（Esc/フォーカストラップ）、U-05（キーボード操作）、U-08（ネイティブダイアログ置換）、U-09（長押しメニュー）、U-01（⋯メニュー維持）、U-03（画像ボタン分離）、P-04（ObjectURL/FormData）、P-07（gzip/ETag）、F-04（claims掃除）、L-06、L-08、L-12、L-13は別プランで扱う。

**F-13（`test_v4_spec.mjs` の外部Electronバンドル依存assert）は、ファイル名修正だけでは解決せず「簡単修正」ではないため、本プランから削除した。** 対応するなら、asar import パスと `liveAsar` の存在チェック＋版数ゲートを入れる別タスクにする。

---

## 9. 本レビューで追記・修正した考慮事項

初版のチェックで以下を修正・追記した。

| 箇所 | 追記・修正内容 |
|:--|:--|
| A-2 (L-03) | `status` 判定が複数箇所にあるため一括置換禁止を明記。宛先不明時に無言で消える副作用と、L-08 とのセット実施を推奨 |
| A-4 (L-07) | `load()` は readonly でもマイグレーションする既存挙動と、`session_cache` 残留時のため防御ガードを残す理由を追記 |
| A-8 (F-09) | 他の2呼び出しは「フォーカス系」ではなく `fadeAgQuota()` タイマー内と `send()` 冒頭、と訂正 |
| A-10 (P-01) | `test_poll_error_ui` の更新は「必須」ではなく整合推奨に訂正。未読ドット/バッジ更新が最大10秒遅れる副作用を追記 |
| A-9 (P-08) | 見送り推奨から「起床イベント付き段階バックオフ」の実装案に変更。**操作後10分（600秒）は通常速度**を確定仕様として明記 |
| B-1 (U-07) | RP背景画像使用時はコントラストが背景依存になる旨と、`.muted` も任意対象に追加 |
| B-2 (U-06) | `min-width` 方式は `.badges` を広げてタイトルを圧迫するため、疑似要素 `::before` で当たり判定だけ広げる代替案を追加 |
| C-1 (F-10) | 「`#mobileActionToggle` を含むブロック」はヒューリスティックである点と関数名変更の提案を追記 |
| C-3 (F-13) | 外部バンドル依存で簡単修正ではないため、プランから削除（§8へ移動） |
| 期待値 | `test_enter_send.cjs` の期待値を 11 → **12 PASS** に修正 |

---

## 10. 実装時の注意（再掲）

1. `server.py` は他作業の未コミット差分がある。編集前に `git diff server.py` で自分の変更だけが乗ることを確認。
2. 行番号は変動する。必ずアンカーで検索する。
3. `git checkout -- .` / `git restore .` は使わない。
4. 変更後は `git diff --stat` で想定ファイルだけが変わっていることを確認。
