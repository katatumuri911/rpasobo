# (L-02) 解決済み・無効化記憶の自動再有効化（Reactivation）詳細仕様書（最終監査版）

## 1. 目的と概要

### 1.1 背景と課題
キャラクター対話システムにおいて、一度「解決済み（`fulfilled`）」（達成された約束、外された装備品、解消された一時状態など）となった長期記憶に対して、後の会話で新しい情報が追記・更新されたり、再度同じ事象が発生したりした場合に、記憶が「無効（`fulfilled`）」のラベルを保持したまま更新される現象が報告された。
その結果：
1. 想起（RAG）の検索対象（Tier 1 常時枠、および Tier 2 想起枠の一部）から永久に除外されたままになる。
2. 装備品の再装着や状態の再発時に、古い解決済み記憶が更新されず新規複製（ゴースト記憶）が重複作成される。
3. ユーザーが UI 上で解決済み記憶の内容を編集しても、ステータスが `fulfilled` のまま保存され、依然として想起されない。

### 1.2 直し方の基本原則
**「新しい情報で更新された記憶は、自動的に有効（`active`）に戻す」**
ただし、単純に `status: "active"` を代入するだけでは、同一ターン内の解決処理との競合、ロールバック時の状態破壊、過去ターンからの遅延併合（stale）による解決打ち消し等の深刻な副作用を引き起こす。
本仕様書は、事前提案プランおよび先行調査レポートを徹底的に批判的検証し、すべての考慮漏れ・潜在バグ・エッジケースを解消した確定仕様を定義する。

---

## 2. 先行提案および先行調査の批判的検証（発見された全欠陥と修正）

### 2.1 先行調査で指摘された 8 つの欠陥の検証

| # | 先行指摘項目 | 先行調査の主張 | コード実態と検証結果 | 確定方針 |
|---|---|---|---|---|
| **D1** | Stale 解決打ち消し | `_is_stale_merge` が `turn_idx` のみ比較しており、過去ターン情報で解決済み記憶が誤って active 化する | **検証確認**: `turn_idx=1, resolved_turn=5` に対し `item_turn=3` が届くと `3 < 1` が False となりマージされる。 | `resolved_turn` を比較対象に加え、`item_turn <= resolved_turn` を stale 拒否する。 |
| **D2** | 同ターン解決ゴースト複製 | 現バッチ解決分を単に `continue` スキップすると新規追加ブロックへフォールスルーし同ターンにアクティブな複製が作られる | **検証確認**: candidate ループでヒットしないため `else: memories.append(...)` に到達しゴーストが生成される。 | 現ターン/現バッチ解決と類似する抽出項目は、新規追加も抑止（drop）する。 |
| **D3** | カテゴリ別スキップの残存 | `memory_store.py:1521, 1569, 1619` に `status != "active"` スキップが残り、item 等が再有効化されない | **検証確認**: item/status/rule/calling は解決済みだとマージ対象から除外され、再有効化マージが不可能。 | 現ターン解決分のみ除外とし、過去ターンの解決済み記憶へのマージ・再有効化を全カテゴリで許可。 |
| **D4** | ロールバック時の解決メタデータ欠損 | `delete_after_turn` が `status` のみ戻し `resolved_*` を復元しないと多段階巻き戻しでスタックする | **検証確認**: `_archive_memory_to_history` が `resolved_*` を退避せず、復元時もメタデータが消失する。 | `history` に `status`, `resolved_*` を完全退避・対称復元する。 |
| **D5** | 中間削除時の履歴 desync | `shift_turn_indices` が `history` 内の `resolved_turn` をシフトしない | **検証確認**: `h["turn_idx"]` のみシフトされ `h["resolved_turn"]` は放置される。 | `h.get("resolved_turn")` も `_shift()` 対象に含める。 |
| **D6** | UI 編集時の status 上書き罠 | `public/app.js` が常に `status: $("ragMemStatus").value` を送るためバックエンドの None 判定が機能しない | **検証確認**: `saveRagMem` は編集時常に現在の status（`fulfilled`）を PUT 送信する。 | フロントで本文変更時に `active` へ自動切替、バックエンドでも `status='active'` 時にメタデータ全削除。 |
| **D7** | 装備解除（`resolve_items`）との同期ズレ | 非同期抽出ワーカーの `resolved_ids` に `resolve_items` の解決 ID が入らない | **検証確認**: `server.py:8402` は同期で `resolved_turn=current_turn` を設定するが `m_res` には含まれない。 | 除外判定に `existing.get("status") == "fulfilled" and existing.get("resolved_turn") == item_turn` も含める。 |
| **D8** | `touch=False` 時のステータス処遇 | 一括再生成（`touch=False`）で新情報が来た場合の挙動が未定義 | **検証確認**: 非破壊モードでも新ターン情報であれば active 化と解決メタデータクリアを行うのが正しい。 | `touch=False` でも stale でなければ `status: "active"` とし解決メタデータをクリア。 |

---

### 2.2 先行調査が見落としていた【5 つの重大な欠陥・考慮漏れ】（二次監査による発見）

先行調査レポートおよびその仕様案には、実運用で致命的となる以下の 5 つの見落とし・論理的誤りが存在していた。

#### 【新規欠陥 1】Branch 1 における `promise` の 1 ターン隣接制限トラップ（The One-Turn Trap）
- **先行調査の記述**:
  仕様書 255 行目: `過去ターンで解決済みの記憶は、abs(item_turn - ex_turn) <= 1（または新ターン）の条件を満たせばマージ候補として評価される。`
- **コード実態**:
  [memory_store.py:1531](file:///c:/Users/nukon/RPasobou/memory_store.py#L1531):
  ```python
  if item_turn is not None and ex_turn is not None and abs(item_turn - ex_turn) <= 1:
  ```
  コードには「または新ターン」などという分岐は存在しない。
  Branch 1 では `event` と `promise` が同居しており、`abs(item_turn - ex_turn) <= 1` が両方に強制されている。
- **実害**:
  ターン 1 で作成（`turn_idx=1`）、ターン 5 で解決（`resolved_turn=5`）された約束に対し、ターン 10 で「また買い物に行こう」と新情報が来ても、`abs(10 - 1) = 9 > 1` となり **絶対にマージ候補にヒットしない**。
  その結果、先行調査の案を適用しても、**1 ターン以上過去に解決された約束は二度と再有効化できず、永久に重複複製（ゴースト記憶）が新規追加され続ける**。
- **是正策**:
  `event` はエピソード記憶であるため直近隣接ターン（`abs <= 1`）のみマージするのが正しいが、`promise` は持続的な状態・約束である。Branch 1 の候補ループにおいて、`promise` は `abs <= 1` の制約から外し、意味的類似度（$\ge 0.85$）のみでマッチさせる（または Branch 2 の永続属性と同様に扱う）。

#### 【新規欠陥 2】ロールバック時に未来の解決状態が過去へ漏洩する不整合（Future Resolution Leak on Direct Rollback）
- **先行調査の主張（§5 E5 および §6 R6）**:
  ターン 2 作成 $\to$ ターン 5 解決 $\to$ ターン 10 再有効化された記憶において、ターン 3 へ直接巻き戻した場合、Step 2 で「ターン 1 の状態」から復元されて active に戻ると主張。R6 テストも 10 $\to$ 7 $\to$ 3 という「2段階巻き戻し」しかテストしていなかった。
- **コード実態**:
  記憶の解決（`save_extracted_memories` の `resolved_ids` や `resolve_items`）は、**`_archive_memory_to_history` を呼ばない**（直接 `m["status"] = "fulfilled"` を書き換える）。
  したがって、ターン 10 で `_merge_into_memory` が呼ばれた時点で退避される `history[0]` の内容は「ターン 5 解決時点の状態（`turn_idx: 2, status: "fulfilled", resolved_turn: 5`）」である。
  ターン 10 からターン 3 へ一気に巻き戻す（`delete_after_turn(sid, 3)`）と：
  1. Step 1: 現在の記憶は `status: "active"` なので Step 1 は **スキップ** される。
  2. Step 2: `turn_idx (10) > 3` なので `history` を探索。`h["turn_idx"] == 2 <= 3` より `history[0]` が `restored` に選ばれる。
  3. `m["status"]` に `restored["status"]`（`"fulfilled"`）が代入され、`m["resolved_turn"]` に 5 が代入される。
  4. Step 1 は既に終了しているため、復元されたオブジェクトは再検査されない。
- **実害**:
  **ターン 3（まだ解決されていない時点）の記憶に、未来である「ターン 5 で解決済み」というステータスが復元されてしまう**。因果律の完全な破壊である。
- **是正策**:
  Step 2 で `history` から復元した直後に、対称的に未来解決ガードを実施する：
  ```python
  if m.get("status") == "fulfilled":
      r_turn = m.get("resolved_turn")
      if r_turn is not None and int(r_turn) > turn_idx:
          m["status"] = "active"
          m.pop("resolved_turn", None)
          m.pop("resolved_mid", None)
          m.pop("resolved_elapsed_time", None)
          m.pop("resolved_at", None)
  ```
  `delete_by_mid` においても同様に `m.get("resolved_mid") == mid` を復元直後に検査・除去する。

#### 【新規欠陥 3】ゴースト複製抑止フラグのループ汚染と過剰破棄（Loop Variable Leakage）
- **先行調査の記述（§4.3 [B] & [C]）**:
  候補ループ内で `conflict_resolved = True` を立て、新規追加ブロックで `if conflict_resolved: continue` としていた。
- **コード実態と実害**:
  `prepared_items` に複数の抽出項目が含まれる場合（例: アイテム 1 が同ターン解決のゴースト、アイテム 2 が全く無関係な新規事実）、アイテムごとのループ先頭で `conflict_resolved = False` を明示的に初期化しないと、アイテム 1 のフラグがアイテム 2 に残留し、**正当な新規記憶が永久に消失（サイレントドロップ）** する。
  さらに、Branch 3 で `thresh`（`calling` や `relationship` は 0.70 という非常に緩い閾値）を使って `conflict_resolved` を判定すると、関係性の記述が少し似ているだけで無関係な新規事実がゴーストと誤判定されて破棄される。
- **是正策**:
  1. `for cat, content, ... in prepared_items:` のループ先頭で必ず `conflict_resolved = False` を初期化。
  2. ゴースト抑止の類似度判定はカテゴリの想起閾値（`thresh`）に関わらず、全カテゴリで **高信頼閾値 0.85** に固定する。

#### 【新規欠陥 4】UI のステータス単独手動解決における `resolved_turn` 欠損
- **先行調査の記述**:
  UI 編集で本文変更時の自動 active 化のみを議論し、ステータスのみを手動変更した場合を無視していた。
- **コード実態**:
  [memory_store.py:2010-2022](file:///c:/Users/nukon/RPasobou/memory_store.py#L2010-L2022) では、ステータスのみ変更時は `source` を `"auto"` のまま維持する設計になっている（`# 優先度やステータスのみの変更時は auto の寿命・巻き戻し連動を維持`）。
  しかし、`update_memory` で `status="fulfilled"` にした際、`resolved_at = time.time()` しか記録されず、`resolved_turn` が記録されない。
- **実害**:
  1. `delete_after_turn` の Step 1 は `res_turn = m.get("resolved_turn")` が None の場合スキップするため、**UI で手動解決した記憶はロールバックしても active に戻らない**（巻き戻し連動が破綻）。
  2. `_is_stale_merge` も `resolved_turn` が None だと `item_turn <= resolved_turn` を検査できず、同ターンや過去ターンの自動抽出によって即座に active に上書き復活してしまう。
- **是正策**:
  1. `update_memory` に `turn_idx=None` 引数を追加し、`status="fulfilled"` 指定時に `resolved_turn` を記録可能にする（`server.py` の PUT ハンドラから現在のセッションターンを渡す）。
  2. `_is_stale_merge` において、`status == "fulfilled"` かつ `resolved_turn is None` の場合、最低でも `item_turn <= existing_turn` を stale 扱いとして即時誤復帰を防御する。

#### 【新規欠陥 5】`clear_auto_memories` における Invariant 1 違反の放置
- **コード実態**:
  [memory_store.py:2060-2065](file:///c:/Users/nukon/RPasobou/memory_store.py#L2060-L2065) の `clear_auto_memories` は手動・優先記憶を active 化する際、以下のように処理している：
  ```python
  if m.get("status") == "fulfilled":
      m["status"] = "active"
      m.pop("resolved_turn", None)
      m.pop("resolved_mid", None)
      m.pop("resolved_elapsed_time", None)
      m["updated_at"] = time.time()
  ```
  **`resolved_at` を pop することを忘れている**。
- **実害**:
  手動解決（`resolved_at` を持つ）された記憶が active 化された後も `resolved_at` が残り続け、「Active 記憶は解決メタデータを持たない」という不変条件を破壊する。
- **是正策**:
  `clear_auto_memories` に `m.pop("resolved_at", None)` を追加し、さらに `load_memories` に自己修復処理（active 記憶からの `resolved_*` 全削除）を配備する。

---

## 3. 全体アーキテクチャと 6 大不変条件（Invariants）

システム全体で常に維持されるべき不変条件を以下に定める。

1. **Active 整合性の原則**:
   `status == "active"` である記憶は、`resolved_turn`, `resolved_mid`, `resolved_elapsed_time`, `resolved_at` の各キーを一切持たない。
2. **Fulfilled 整合性の原則**:
   `status == "fulfilled"` である記憶は、`resolved_turn` または `resolved_at` の少なくとも一方を必ず保持する。
3. **因果律の原則（Anti-causal Reversion Guard）**:
   解決ターン $T_{res}$ 以前（$T_{new} \le T_{res}$）の情報はマージできず（stale）、解決状態を打ち消してはならない。$T_{new} > T_{res}$ である新しい情報がマージされた場合に限り、記憶は `status: "active"` に昇格し、過去の解決状態は `history` に保存される。
4. **ロールバックの完全可逆性と未来遮断（Future Leak Guard）**:
   ターン $T$ への巻き戻しを行った場合、その記憶のターン $T$ 終了時点における状態が完全に復元される。ターン $T$ より未来で発生した解決や再有効化は、直接巻き戻し・多段階巻き戻しを問わず一切残留してはならない。
5. **手動意思の絶対尊重**:
   ユーザーが UI で明示的に `fulfilled` を選択して保存した場合は、本文変更があっても `fulfilled` を維持する。また、優先度（ピン留め・最優先等）が設定された記憶は自動抽出マージの対象外とする。
6. **自己修復性（Self-healing Load）**:
   `load_memories` 読み込み時に `status` が欠落していれば `"active"` に補完し、`status == "active"` に残存する `resolved_*` メタデータは自動的に除去・保存する。

---

## 4. コンポーネント別詳細実装仕様

### 4.1 `memory_store.py::_is_stale_merge` & `_merge_into_memory`

#### [A] `_is_stale_merge`
解決済み記憶の解決ターン、および解決ターン欠損時の安全弁を実装する。

```python
def _is_stale_merge(new_turn, existing_turn, resolved_turn=None, is_fulfilled=False):
    """new_turn が既存の観測ターンまたは解決ターン以前なら True (stale / 巻き戻し)。

    - active 記憶: new_turn < existing_turn で stale
    - fulfilled 記憶: new_turn <= resolved_turn（または existing_turn）で stale（同ターンも解決優先）
    """
    if new_turn is None:
        return False
    try:
        n_turn = int(new_turn)
    except (ValueError, TypeError):
        return False

    # 解決済み記憶の場合の検査
    if is_fulfilled or resolved_turn is not None:
        cutoff = None
        if resolved_turn is not None:
            try:
                cutoff = int(resolved_turn)
            except (ValueError, TypeError):
                pass
        if cutoff is None and existing_turn is not None:
            try:
                cutoff = int(existing_turn)
            except (ValueError, TypeError):
                pass
        if cutoff is not None and n_turn <= cutoff:
            return True

    if existing_turn is None:
        return False
    try:
        return n_turn < int(existing_turn)
    except (ValueError, TypeError):
        return False
```

#### [B] `_merge_into_memory`
マージ実行時にステータスを `active` へ復帰し、解決メタデータを完全クリーンアップする。

```python
def _merge_into_memory(ex_mem, content, new_tokens, new_vec, new_hash,
                       item_turn, item_mid, item_elapsed_time,
                       now_ts, touch=True):
    ex_turn = ex_mem.get("turn_idx")
    res_turn = ex_mem.get("resolved_turn")
    is_fulfilled = (ex_mem.get("status") == "fulfilled")

    # 解決ターンおよび既存ターンに対する stale 判定
    if _is_stale_merge(item_turn, ex_turn, resolved_turn=res_turn, is_fulfilled=is_fulfilled):
        return False

    # 既存状態（status および resolved_* を含む）を history へ退避
    _archive_memory_to_history(ex_mem, now_ts)

    ex_mem["content"] = content
    ex_mem["tokens"] = new_tokens
    ex_mem["embedding"] = new_vec
    ex_mem["content_hash"] = new_hash

    # 【再有効化】新情報による併合成功時は自動的に active へ復帰
    ex_mem["status"] = "active"
    ex_mem.pop("resolved_turn", None)
    ex_mem.pop("resolved_mid", None)
    ex_mem.pop("resolved_elapsed_time", None)
    ex_mem.pop("resolved_at", None)

    if touch:
        ex_mem["updated_at"] = now_ts
        if item_turn is not None:
            ex_mem["turn_idx"] = item_turn
        if item_mid:
            ex_mem["mid"] = item_mid
        if item_elapsed_time is not None:
            ex_mem["elapsed_time"] = copy.deepcopy(item_elapsed_time)
    else:
        # touch=False（非破壊モード）：turn_idx が None の場合のみ補填
        if ex_turn is None and item_turn is not None:
            try:
                ex_mem["turn_idx"] = int(item_turn)
            except (ValueError, TypeError):
                pass
    return True
```

---

### 4.2 `memory_store.py::_archive_memory_to_history`

```python
def _archive_memory_to_history(mem_obj, now_ts):
    """既存記憶の更新前に現在の状態を history へ退避する（status, priority, resolved_* も保持）"""
    old_hist = {
        "turn_idx": mem_obj.get("turn_idx"),
        "elapsed_time": copy.deepcopy(mem_obj.get("elapsed_time")),
        "mid": mem_obj.get("mid"),
        "content": mem_obj.get("content"),
        "tokens": copy.deepcopy(mem_obj.get("tokens") or []),
        "embedding": copy.deepcopy(mem_obj.get("embedding") or []),
        "content_hash": mem_obj.get("content_hash") or "",
        "status": mem_obj.get("status", "active"),
        "archived_at": now_ts
    }
    if mem_obj.get("priority"):
        old_hist["priority"] = mem_obj["priority"]
    if mem_obj.get("resolved_turn") is not None:
        old_hist["resolved_turn"] = mem_obj["resolved_turn"]
    if mem_obj.get("resolved_mid"):
        old_hist["resolved_mid"] = mem_obj["resolved_mid"]
    if mem_obj.get("resolved_elapsed_time"):
        old_hist["resolved_elapsed_time"] = copy.deepcopy(mem_obj["resolved_elapsed_time"])
    if mem_obj.get("resolved_at"):
        old_hist["resolved_at"] = mem_obj["resolved_at"]

    mem_obj.setdefault("history", []).append(old_hist)
```

---

### 4.3 `memory_store.py::save_extracted_memories`

#### [A] 同ターン解決競合の判定ヘルパー
```python
def _is_resolved_in_current_turn_or_batch(mem, cur_turn, res_ids_set):
    """現バッチまたは現ターンで解決された記憶かを判定する（同ターン競合のガード）"""
    if mem.get("id") in res_ids_set:
        return True
    if mem.get("status") == "fulfilled":
        r_turn = mem.get("resolved_turn")
        if cur_turn is not None and r_turn is not None:
            try:
                if int(r_turn) == int(cur_turn):
                    return True
            except (ValueError, TypeError):
                pass
    return False
```

#### [B] 抽出アイテムループとゴースト抑止
`for cat, content, ... in prepared_items:` ループ内で：

```python
    appended_new = 0
    current_batch_ids = set()
    for cat, content, new_vec, new_tokens, new_hash, item_turn, item_mid, item_el in prepared_items:
        conflict_resolved = False  # 【重要】アイテム毎に必ず初期化

        if cat in ("event", "promise"):
            best_recent_sim = -1.0
            best_recent_idx = -1
            for idx, existing in enumerate(memories):
                if existing.get("id") in current_batch_ids:
                    continue
                if is_pinned_memory(existing) or memory_priority_key(existing) != "default":
                    continue
                if existing.get("category") == cat and existing.get("source") == "auto":
                    # 同ターン解決記憶との衝突検査（高類似 0.85 でゴースト抑止フラグを立てる）
                    if _is_resolved_in_current_turn_or_batch(existing, item_turn, res_set):
                        sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                        if sim >= 0.85:
                            conflict_resolved = True
                        continue

                    # 【重要: 1ターン隣接制限の分離】
                    # event のみ隣接ターン制約を適用。promise は持続的な状態のためターン差不問で評価する
                    if cat == "event":
                        ex_turn = existing.get("turn_idx")
                        if not (item_turn is not None and ex_turn is not None and abs(item_turn - ex_turn) <= 1):
                            continue

                    sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                    if sim > best_recent_sim:
                        best_recent_sim = sim
                        best_recent_idx = idx

            if best_recent_sim >= 0.85 and best_recent_idx >= 0:
                if _merge_into_memory(
                    memories[best_recent_idx], content, new_tokens, new_vec, new_hash,
                    item_turn, item_mid, item_el,
                    now_ts, touch=touch
                ):
                    current_batch_ids.add(memories[best_recent_idx].get("id"))
            else:
                if conflict_resolved:
                    continue  # 同ターン解決済み内容のオウム返し抽出なので新規追加を破棄
                if appended_new >= limit_new:
                    continue
                # 新規追加処理（memories.append）...

        elif cat in ("item", "status"):
            best_sim = -1.0
            best_idx = -1
            for idx, existing in enumerate(memories):
                if existing.get("id") in current_batch_ids:
                    continue
                if is_pinned_memory(existing) or memory_priority_key(existing) != "default":
                    continue
                if existing.get("category") in ("item", "status") and existing.get("source") == "auto":
                    if _is_resolved_in_current_turn_or_batch(existing, item_turn, res_set):
                        sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                        if sim >= 0.85:
                            conflict_resolved = True
                        continue
                    sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                    if sim > best_sim:
                        best_sim = sim
                        best_idx = idx

            if best_idx >= 0 and best_sim >= 0.85:
                if _merge_into_memory(
                    memories[best_idx], content, new_tokens, new_vec, new_hash,
                    item_turn, item_mid, item_el,
                    now_ts, touch=touch
                ):
                    current_batch_ids.add(memories[best_idx].get("id"))
            else:
                if conflict_resolved:
                    continue
                if appended_new >= limit_new:
                    continue
                # 新規追加処理...

        else:  # calling, relationship, rule, char_fact, user_fact
            best_sim = -1.0
            best_idx = -1
            thresh = SEMANTIC_THRESHOLDS.get(cat, 0.70)
            for idx, existing in enumerate(memories):
                if existing.get("id") in current_batch_ids:
                    continue
                if is_pinned_memory(existing) or memory_priority_key(existing) != "default":
                    continue
                ex_cat = existing.get("category")
                if ex_cat == cat and existing.get("source") == "auto":
                    if _is_resolved_in_current_turn_or_batch(existing, item_turn, res_set):
                        sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                        # ゴースト判定は想起閾値によらず 0.85 で厳格判定
                        if sim >= 0.85:
                            conflict_resolved = True
                        continue
                    sim = cosine_similarity(new_vec, existing.get("embedding") or [])
                    if sim > best_sim:
                        best_sim = sim
                        best_idx = idx

            if best_idx >= 0 and best_sim >= thresh:
                if _merge_into_memory(
                    memories[best_idx], content, new_tokens, new_vec, new_hash,
                    item_turn, item_mid, item_el,
                    now_ts, touch=touch
                ):
                    current_batch_ids.add(memories[best_idx].get("id"))
            else:
                if conflict_resolved:
                    continue
                if appended_new >= limit_new:
                    continue
                # 新規追加処理...
```

---

### 4.4 `memory_store.py::update_memory`

手動更新 API において `turn_idx` を受け取れるようにし、active 化・fulfilled 化の両方でメタデータの完全整合性を維持する。

```python
def update_memory(session_id, mem_id, category=None, content=None, status=None, boost=None, priority=None, turn_idx=None):
    if not session_id or not mem_id:
        raise ValueError("session_id and mem_id are required")
    # tokens, vec, hash の計算...

    with _store_lock:
        data = load_memories(session_id)
        mem = next((m for m in data.get("memories", []) if m.get("id") == mem_id), None)
        if not mem:
            raise KeyError("no such memory")

        # 1. content の更新処理
        if content is not None and content != mem.get("content"):
            mem["content"] = content
            mem["content_hash"] = new_hash
            mem["tokens"] = new_tokens
            mem["embedding"] = new_vec
            content_or_cat_changed = True
            # status 引数が明示されていない場合、fulfilled な記憶は自動的に active へ昇格
            if status is None and mem.get("status") == "fulfilled":
                mem["status"] = "active"
                mem.pop("resolved_turn", None)
                mem.pop("resolved_mid", None)
                mem.pop("resolved_elapsed_time", None)
                mem.pop("resolved_at", None)

        if has_priority_update:
            apply_priority_field(mem, priority=p_key)

        # 2. status の明示的更新処理
        if status is not None:
            status = status.strip().lower()
            if status in ("active", "fulfilled"):
                mem["status"] = status
                if status == "fulfilled":
                    mem["resolved_at"] = time.time()
                    if turn_idx is not None:
                        try:
                            mem["resolved_turn"] = int(turn_idx)
                        except (ValueError, TypeError):
                            pass
                elif status == "active":
                    mem.pop("resolved_turn", None)
                    mem.pop("resolved_mid", None)
                    mem.pop("resolved_elapsed_time", None)
                    mem.pop("resolved_at", None)

        if (content is not None) or (category is not None):
            mem["source"] = "manual"

        mem["updated_at"] = time.time()
        save_memories(session_id, data)
        return mem
```

また、`server.py:11615` の PUT `/api/rag/memories/<mem_id>` ハンドラでは、`turn_idx=calc_turn_idx(s.get("messages", []), len(s.get("messages", [])) - 1, s.get("setting", {}).get("total_turns"))` を渡すよう更新する。

---

### 4.5 `memory_store.py::delete_after_turn` & `delete_by_mid`

#### [A] `delete_after_turn`（ターン巻き戻し）
履歴復元直後に **未来解決ガード（Post-restore Future Resolution Guard）** を追加。

```python
        for m in original_memories:
            # Step 1: 解決ターンが turn_idx より新しい fulfilled 記憶を active に戻す
            if m.get("status") == "fulfilled":
                res_turn = m.get("resolved_turn")
                try:
                    res_turn_val = int(res_turn) if res_turn is not None else None
                except (ValueError, TypeError):
                    res_turn_val = None
                if res_turn_val is not None and res_turn_val > turn_idx:
                    m["status"] = "active"
                    m.pop("resolved_turn", None)
                    m.pop("resolved_mid", None)
                    m.pop("resolved_elapsed_time", None)
                    m.pop("resolved_at", None)
                    m["updated_at"] = time.time()
                    changed = True

            # 手動記憶およびピン留め記憶の保護
            if m.get("source") != "auto" or is_pinned_memory(m):
                new_memories.append(m)
                continue

            # Step 2: 記憶自体のターンが turn_idx より新しい場合、history から復元
            m_turn = m.get("turn_idx")
            try:
                m_turn_val = int(m_turn) if m_turn is not None else None
            except (ValueError, TypeError):
                m_turn_val = None

            if m_turn_val is not None and m_turn_val > turn_idx:
                history = m.get("history") or []
                valid_hist = []
                for h in history:
                    try:
                        h_turn = int(h.get("turn_idx")) if h.get("turn_idx") is not None else None
                    except (ValueError, TypeError):
                        h_turn = None
                    if h_turn is not None and h_turn <= turn_idx:
                        valid_hist.append(h)

                if valid_hist:
                    restored = valid_hist[-1]
                    kept_prio = m.get("priority") or restored.get("priority")
                    m["content"] = restored["content"]
                    m["turn_idx"] = restored.get("turn_idx")
                    m["mid"] = restored.get("mid")
                    m["elapsed_time"] = copy.deepcopy(restored.get("elapsed_time"))
                    m["content_hash"] = restored.get("content_hash") or compute_hash(restored["content"])
                    m["tokens"] = copy.deepcopy(restored.get("tokens")) if restored.get("tokens") else extract_tokens(restored["content"])
                    m["embedding"] = copy.deepcopy(restored.get("embedding")) if restored.get("embedding") else get_embedding(restored["content"])
                    m["history"] = valid_hist[:-1]
                    apply_priority_field(m, priority=kept_prio)

                    # 履歴からの status および解決メタデータの復元
                    m["status"] = restored.get("status", "active")
                    for r_key in ("resolved_turn", "resolved_mid", "resolved_elapsed_time", "resolved_at"):
                        if r_key in restored:
                            m[r_key] = copy.deepcopy(restored[r_key])
                        else:
                            m.pop(r_key, None)

                    # 【新規修正: 未来解決ガード】
                    # 直接巻き戻し時、復元された解決状態が巻き戻し先端（turn_idx）より未来であれば active に戻す
                    if m.get("status") == "fulfilled":
                        r_turn = m.get("resolved_turn")
                        try:
                            r_turn_val = int(r_turn) if r_turn is not None else None
                        except (ValueError, TypeError):
                            r_turn_val = None
                        if r_turn_val is not None and r_turn_val > turn_idx:
                            m["status"] = "active"
                            m.pop("resolved_turn", None)
                            m.pop("resolved_mid", None)
                            m.pop("resolved_elapsed_time", None)
                            m.pop("resolved_at", None)

                    m["updated_at"] = time.time()
                    new_memories.append(m)
                    changed = True
                else:
                    changed = True
                    continue
            else:
                new_memories.append(m)
```

#### [B] `delete_by_mid`
同様に Step 2 の復元直後に未来解決ガードを追加：
```python
                    m["status"] = restored.get("status", "active")
                    for r_key in ("resolved_turn", "resolved_mid", "resolved_elapsed_time", "resolved_at"):
                        if r_key in restored:
                            m[r_key] = copy.deepcopy(restored[r_key])
                        else:
                            m.pop(r_key, None)

                    # 復元された解決 mid が今回削除された mid と一致する場合は active 化
                    if m.get("status") == "fulfilled" and m.get("resolved_mid") == mid:
                        m["status"] = "active"
                        m.pop("resolved_turn", None)
                        m.pop("resolved_mid", None)
                        m.pop("resolved_elapsed_time", None)
                        m.pop("resolved_at", None)
```

---

### 4.6 `memory_store.py::shift_turn_indices`

```python
            if m.get("resolved_turn") is not None:
                nv = _shift(m.get("resolved_turn"))
                if nv != m.get("resolved_turn"):
                    m["resolved_turn"] = nv
                    changed = True

            for h in m.get("history") or []:
                if isinstance(h, dict):
                    if h.get("turn_idx") is not None:
                        nv = _shift(h.get("turn_idx"))
                        if nv != h.get("turn_idx"):
                            h["turn_idx"] = nv
                            changed = True
                    if h.get("resolved_turn") is not None:
                        nv = _shift(h.get("resolved_turn"))
                        if nv != h.get("resolved_turn"):
                            h["resolved_turn"] = nv
                            changed = True
```

---

### 4.7 `clear_auto_memories` と `load_memories` の不変条件防御

#### [A] `clear_auto_memories`（全解決メタデータのクリーンアップ）
```python
        for m in data.get("memories", []):
            if m.get("source") == "manual" or memory_priority_key(m) != "default":
                if m.get("status") == "fulfilled":
                    m["status"] = "active"
                    m.pop("resolved_turn", None)
                    m.pop("resolved_mid", None)
                    m.pop("resolved_elapsed_time", None)
                    m.pop("resolved_at", None)  # 【追加】resolved_at も確実にクリア
                    m["updated_at"] = time.time()
                kept_memories.append(m)
```

#### [B] `load_memories`（自己修復・サニタイズ）
```python
            if not mem.get("status"):
                mem["status"] = "active"
                needs_resave = True
            # active 記憶が解決メタデータを抱えていた場合の自己修復
            if mem.get("status") == "active":
                for r_key in ("resolved_turn", "resolved_mid", "resolved_elapsed_time", "resolved_at"):
                    if r_key in mem:
                        mem.pop(r_key, None)
                        needs_resave = True
```

---

### 4.8 `public/app.js`（UI 自動連動）

```javascript
let ragMemInitialContent = "";
let ragMemInitialStatus = "active";
let ragMemStatusManuallyChanged = false;

function openRagMemEditor(mem = null) {
  ragMemEditingId = mem ? mem.id : null;
  $("ragMemEditorTitle").textContent = mem ? "記憶の編集" : "手動で記憶を追加";
  const defaultCat = ragMemFilter !== "all" ? ragMemFilter : "promise";
  $("ragMemCategory").value = mem ? (mem.category || "promise") : defaultCat;
  
  ragMemInitialStatus = mem ? (mem.status || "active") : "active";
  if ($("ragMemStatus")) {
    $("ragMemStatus").value = ragMemInitialStatus;
  }
  
  ragMemInitialContent = mem ? (mem.content || "") : "";
  $("ragMemContent").value = ragMemInitialContent;
  ragMemStatusManuallyChanged = false;

  renderPriorityPills(mem ? resolveMemoryPriorityKey(mem) : "default");
  $("ragMemView").classList.add("hidden");
  $("ragMemEditor").classList.remove("hidden");
  syncRagMemEditingClass();
  $("ragMemContent").focus();
  if (isMobileLayout()) rafOnce(keepFocusedVisible);
}

// イベントリスナーの初期化設定（initRagMemModal 付近）
if ($("ragMemStatus") && !$("ragMemStatus").__boundChange) {
  $("ragMemStatus").__boundChange = true;
  $("ragMemStatus").addEventListener("change", () => {
    ragMemStatusManuallyChanged = true;
  });
}

if ($("ragMemContent") && !$("ragMemContent").__boundInputReactivate) {
  $("ragMemContent").__boundInputReactivate = true;
  $("ragMemContent").addEventListener("input", () => {
    if (!ragMemStatusManuallyChanged && ragMemInitialStatus === "fulfilled" && $("ragMemStatus")) {
      const cur = $("ragMemContent").value.trim();
      if (cur !== ragMemInitialContent.trim() && $("ragMemStatus").value === "fulfilled") {
        $("ragMemStatus").value = "active";
      } else if (cur === ragMemInitialContent.trim() && $("ragMemStatus").value === "active") {
        $("ragMemStatus").value = "fulfilled";
      }
    }
  });
}
```

---

## 5. エッジケース分析と振る舞いマトリクス（E1 〜 E13）

| 状況 | 入力/イベント | 判定・動作 | 結果ステータス | 解決メタデータ |
|---|---|---|---|---|
| **E1: 後続ターンでの新情報マージ** | ターン 5 解決（`res_turn=5`）の記憶に、ターン 10（`item_turn=10`）で新情報 | $10 > 5$ で正当マージ。旧状態を `history` 退避 | `active` | 全て削除（`turn_idx=10`） |
| **E2: 過去の約束の再有効化** | ターン 1 作成・ターン 5 解決の `promise` にターン 12 で新約束文 | `abs <= 1` 解除により正当マッチ。$12 > 5$ でマージ | `active` | 全て削除（`turn_idx=12`） |
| **E3: 同一ターン内の解決＆抽出** | ターン 5 で解決された記憶に対し、同一ターン 5 で類似記憶が抽出 | 同ターン競合検知。マージ拒否＆新規 append も破棄 | `fulfilled` 維持 | `resolved_turn=5` 維持 |
| **E4: 複数項目同時抽出時のゴースト抑止** | アイテム 1 がゴースト、アイテム 2 が正当新規事実 | アイテム 1 のみ drop。アイテム 2 は独立評価で正常追加 | アイテム 2 は `active` | アイテム 2 正常保存 |
| **E5: 遅延抽出による過去情報の到着** | ターン 10 解決済みの記憶に、遅延ワーカーからターン 6 の抽出が届く | $6 \le 10$ により `_is_stale_merge` が True を返し拒否 | `fulfilled` 維持 | 変更なし |
| **E6: ターン巻き戻し（解決後〜再有効化前）** | ターン 5 解決、ターン 10 再有効化された記憶で、ターン 7 へ巻き戻し | Step 2 で `history`（ターン 5 状態）から復元 | `fulfilled` 復帰 | `resolved_turn=5` 完全復元 |
| **E7: ターン巻き戻し（解決前への直接後退）** | ターン 5 解決、ターン 10 再有効化された記憶で、ターン 3 へ一気に巻き戻し | Step 2 で復元後、未来解決ガード（$5 > 3$）により active 化 | `active` 復帰 | メタデータなし |
| **E8: 段階的ターン巻き戻し** | E6 の状態（ターン 7 巻き戻し後）から、さらにターン 3 へ巻き戻し | Step 1 で `res_turn (5) > 3` により active 化 | `active` 復帰 | メタデータなし |
| **E9: UI で本文を修正して保存** | `fulfilled` 記憶の編集画面で本文を変更し「保存」クリック | `app.js` が自動で `status: active` を選択して PUT 送信 | `active` | メタデータ全削除 |
| **E10: UI で本文修正後に手動で達成済を選択** | 本文修正後に、ユーザーが意識してセレクトを `fulfilled` に戻して保存 | ユーザー意思尊重で `status: fulfilled` を維持 | `fulfilled` | `resolved_at` 更新 |
| **E11: 中間メッセージ削除** | ターン 3 のメッセージを削除（`delta=-1`） | トップレベルおよび `history` 内の `resolved_turn` を 4 へ減算 | 不変 | 整合性を維持して減算 |
| **E12: 非破壊一括再生成（`touch=False`）** | 解決済み記憶に新しいターンの本文がマージ | stale でなければ本文・ベクトル更新し、active 化 | `active` | メタデータ削除（出所保持） |
| **E13: 手動ピン留めされた解決済み記憶** | `priority: "pinned"` の解決済み記憶に類似情報が抽出 | 優先度 $\ne$ default によりマージ除外（新規追加） | `fulfilled` 維持 | ピン留め効果は active 化で復活 |

---

## 6. テスト計画と検証ケース定義（R1 〜 R13）

`test/test_memory_reactivate.py` として実装すべき検証テストケース：

- **R1: 正常系・後続ターンマージによる再有効化**
  - ターン 3 作成、ターン 5 `fulfilled`（`resolved_turn=5`）の記憶にターン 8 の抽出を投入。
  - `status == "active"`、`resolved_*` 不在、`turn_idx == 8`、`len(history) == 1` を確認。
- **R2: 約束（promise）のターン差制限解除**
  - ターン 1 作成、ターン 5 解決の `promise` に対し、ターン 15 の高類似約束を投入。
  - `abs(15 - 1) > 1` であってもフォールスルーせず、正しく再有効化マージされることを確認。
- **R3: 同ターン解決＆抽出のゴースト複製防止**
  - `save_extracted_memories` に `resolved_ids=["mem1"]` と同時に `mem1` と高類似（0.95）の新規アイテムを渡す。
  - `mem1` の `fulfilled` が維持され、新規アクティブ記憶が作成されない（記憶総数 1 件維持）ことを確認。
- **R4: バッチ内複数項目のゴースト抑止分離**
  - アイテム 1（同ターン解決ゴースト）とアイテム 2（無関係な新規事実）を同一バッチで投入。
  - アイテム 1 のみが drop され、アイテム 2 は正常に新規追加されることを確認。
- **R5: 装備解除（`resolve_items`）直後の同ターン抽出ガード**
  - `resolve_items` でターン 5 に解除された item 記憶に、同ターン 5 の抽出文を投入。
  - マージも新規追加もされず `fulfilled` が維持されることを確認。
- **R6: Stale 解決マージ拒否（過去情報による解決打ち消し防止）**
  - ターン 2 作成、ターン 10 解決済みの記憶に、ターン 6 のアイテムが届いた場合、マージが拒否され `fulfilled` を維持することを確認。
- **R7: ターン巻き戻し（解決状態への復元）**
  - ターン 2 作成 $\to$ ターン 5 解決 $\to$ ターン 10 再有効化。
  - `delete_after_turn(sid, turn_idx=7)` で `status == "fulfilled"`, `resolved_turn == 5` に正しく復元されることを確認。
- **R8: 直接巻き戻し時の未来解決ガード（因果律テスト）**
  - ターン 2 作成 $\to$ ターン 5 解決 $\to$ ターン 10 再有効化。
  - `delete_after_turn(sid, turn_idx=3)` を一気に直接実行。
  - 記憶が `status == "active"` に戻り、未来の `resolved_turn: 5` が除去されていることを確認。
- **R9: メッセージID指定削除（`delete_by_mid`）による復元と未来解決ガード**
  - `mid_10` で再有効化された記憶で `delete_by_mid(sid, "mid_10")` を実行し、解決状態に復元されることを確認。
- **R10: `shift_turn_indices` による履歴内解決ターンの整合**
  - 履歴に `resolved_turn: 5` を持つ記憶に対し、`shift_turn_indices(sid, after_turn=3, delta=-1)` を実行。
  - `history[0]["resolved_turn"]` が 4 に減算されていることを確認。
- **R11: `clear_auto_memories` における `resolved_at` クリア**
  - 手動解決された記憶（`resolved_at` あり）に対し `clear_auto_memories` を実行。
  - `resolved_at` が確実に pop されていることを確認。
- **R12: `update_memory` 手動編集の再有効化と turn_idx 記録**
  - `status == "fulfilled"` の記憶に対し本文のみ変更で active 化すること、および `status="fulfilled", turn_idx=15` 指定時に `resolved_turn=15` が記録されることを確認。
- **R13: UI 自動切り替えと手動維持の分離（DOM テスト）**
  - `public/app.js` のスタブ環境で `openRagMemEditor` を呼び出し、本文変更での自動 active 化と手動 fulfilled の維持を確認。

---

## 7. 実装手順と推奨フェーズ

1. **第 1 段階: `memory_store.py` コア改修**
   - `_is_stale_merge` のシグネチャ拡張（`is_fulfilled`, `resolved_turn`）
   - `_archive_memory_to_history` の `status` / `resolved_*` 完全退避
   - `_merge_into_memory` の再有効化とメタデータ除去
   - `save_extracted_memories` の `promise` 1ターン制限解除、アイテム別 `conflict_resolved` 初期化、0.85 閾値固定
   - `delete_after_turn` / `delete_by_mid` の復元後未来解決ガード追加
   - `shift_turn_indices` の履歴内 `resolved_turn` シフト追加
   - `clear_auto_memories` の `resolved_at` クリアおよび `load_memories` の自己修復追加
   - `update_memory` の `turn_idx` 引数対応
2. **第 2 段階: `server.py` & `public/app.js` 連携**
   - `server.py` の PUT `/api/rag/memories/<mem_id>` で `turn_idx` を渡す
   - `public/app.js` のモーダル入力検知リスナー登録
3. **第 3 段階: テストスイート検証**
   - 既存の 1,181 件のユニットテストの全件合格維持
   - 新規 R1 〜 R13 テストの実装と Green 確認
