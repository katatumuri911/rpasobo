# 長期記憶のターン番号・作中時刻の出所整合（Tier A＋B）仕様書

## 1. 目的

長期記憶を登録するとき、その記憶を作った会話メッセージの**通算ユーザーターン番号**と**そのメッセージ時点の作中時刻**を保存する。

現状は、後追い抽出と記憶再生成の一部で「抽出を実行した現在の番号・時刻」を使っているため、記憶のTTL、直近記憶の除外、優先度補正、表示、巻き戻しの判断が狂う。これを、`turn_idx`（ターン番号）`elapsed_time`（作中時刻）の**記憶書き込み経路**に限定して修正する。

本仕様は `AUDIT_REPORT.md` L-01 の局所修正に加え、同じ根本原因を持つ記憶再生成とメタデータ併合を対象とする。既存データの一括修復と F-04 は対象外である。

---

## 2. 背景と用語

### 2.1 ターンの定義

- `turn_idx` は、対象メッセージに**最も近い先行する `role == "user"` の発言を含めた、その時点までの通算ユーザー発言数**とする。
- 対象がユーザー発言なら、それをターンの終端として数える。
- 対象がアシスタント発言なら、通常は直前のユーザー発言と同じ番号になる。
- どのユーザー発言より前にあるメッセージ（例：初回の単独アシスタント発言）は `turn_idx = 0` を返す。
- 以降、アーカイブ・直近100件・チャンクといった部分的なリストに対しても、**ファイル内の相対番号ではなく、この通算番号を優先する**。

### 2.2 作中時刻の定義

- `elapsed_time` は `{"day": 整数, "period": "午前"}` 等の作中時刻であり、現実時刻ではない。
- 作中時刻の優先順は次の1系統に固定する（現行コードの読み取り順と一致させる）。
  1. 対象メッセージの `elapsed_time`
  2. 対象メッセージの `snapshot_elapsed_time`（旧セッション・アーカイブ用）
  3. 対象メッセージの `elapsed_snapshot`
  4. それより前のメッセージの同上のいずれか（直近の有効値）
  5. どれも無ければ `None`
- 時刻不明を**現在の作中時刻や `1日目・午前` で補って「正確な出所」と偽らない**。時刻不明は `None` とし、表示は従来のターンベース保険（`約Nターン前`）に退避する。
- この「不明」は**保存された後も不明のまま保持されなければならない**。`elapsed_time: null` を読み込み時に補完・再保存してはならない（§3.2-6）。補完してよいのは「キー自体が無い旧データ」だけとする。
- `format_prompt_memories` は `elapsed_time` が偽値のとき `turn_idx` から `（約Nターン前）` を作る既存分岐を持っている。この分岐が不明データの正式な表示先であり、新しい表示モードを追加しない。
- `elapsed_time` が存在する記憶では `format_prompt_memories` が日付ベースの表示を優先する。`turn_idx` は、この表示だけでなくTTL・直近除外・優先度・巻き戻しに使われる。

### 2.3 「出来事のターン」と「最新の観測」

- **新規登録する記憶**：その出所メッセージの `turn_idx` / `elapsed_time` を保存する。
- **既存記憶への併合**：出所情報は、通常経路の追記（新しいターン）だけが進められる。古い出所から後追い処理された記憶が、より新しい記憶の番号・時刻・本文を巻き戻してはならない（§7）。
- 本修正では新フィールド `last_observed_turn` は作らない。既存の `turn_idx` を「併合後の最新の観測ターン」として維持する。

---

## 3. 対象範囲

### 3.1 Tier A（必須）：L-01 の局所修正

1. `memory_extract.py::extract_memories_fallback` の `turn_idx` 計算を、対象メッセージの通算ターンを返す共通関数へ置き換える。
2. `total_turns=0` かつ `rotation_count>0` の旧セッションを、`total_turns_base` と同じ式で補正してから位置計算する。
3. 対象メッセージが `s["messages"]` に見つからない場合、末尾へ偽装して登録するフォールバックを止める。
4. 作中時刻も「対象メッセージのスナップショット優先、無ければ `None`」に揃え、`st["elapsed_time"]`（＝現在の作中時刻）で補わない。保存して再読込しても `null` のままであることまで確認する（§3.2-6）。
5. 1〜4の値を、§7.1 の項目別出所メタ（`_source_*`）として `save_extracted_memories` へ渡す。Tier A も Tier B と同じ受け渡し機構を使う（保存関数側の分岐を増やさない）。

### 3.2 Tier B（必須）：同じ根本原因の書き込み経路

1. `server.py::run_memory_regeneration` の `cumulative_turns` をやめ、チャンク内の各抽出項目に対象メッセージの通算ターン・作中時刻を付ける。
2. `/api/rag/memories/regenerate` の `d["memories"]` 直接提供経路（`server.py:11682–11686`）でも、メッセージIDから出所を特定する。`source_mid` を必須とし、出所不明は HTTP 400。現在ターン・現在時刻を黙って付けない（§8）。
3. `memory_store.py::save_extracted_memories` が、抽出項目ごとの出所メタデータを扱えるようにする。1チャンクの件数上限・優先度・重複除外・保存ロックは従来どおり維持する。
4. 併合を新しいターンの方向にだけ進め、古い遅延抽出による巻き戻しを防ぐ。この拒否は **`touch` の真偽に関係なく** 適用する（§7.3）。
5. 新規本文メッセージの記憶登録（`server.py:8428`）は、`turn_index_base(st, len(user_msgs), ROTATION_PERIOD)` を使う。**`total_after`（`:8257`）で置き換えない**。`total_after` は回転補正を含まないため、`total_turns=0` の旧セッションで 50→10 の後退を起こす。
6. 「明示された時刻不明（`elapsed_time: null`）」が読み込み・表示時に上書きされないようにする。対象は次の2か所であり、**両方**を直さないと片方から破壊される。
   - `memory_store.load_memories`（`memory_store.py:782–799`）: キー欠落の旧データだけを補完対象に限定し、キーが存在して値が `None` の記憶はそのまま返し、再保存もしない。
   - `/api/rag/memories` の補完保存（`server.py:10927–10941`）: 同じ `if not mem.get("elapsed_time")` で補完し `save_memories` している。ここもキー存在判定に変更する。

### 3.3 Tier C（今回行わない）

| 対象外 | 理由 |
|---|---|
| 保存済み記憶の `turn_idx` / `history` / `elapsed_time` の一括修復 | マージ済み記憶は正しい出所を一意に復元できない。誤った一括補正を避ける |
| F-04（truncate/delete/regenerate/edit の記憶ベクトル、task locks の残留） | 別バグ。同じ削除経路に手を入れると原因の切り分けができなくなる |
| `server.py:3708`（`build_context`側）、`server.py:8414`（装着物の解決）などの広範なターン計算 | L-01に不要。時間管理・RAG・ローテーション全体へ波及する |
| `total_turns_base` や `prune_session_messages` の仕様変更 | I12（剪定後も `total_turns` を保持）は変更しない |
| `ROTATION_PERIOD = 20` による旧データ補填の再設計 | 現行の後方互換式を維持する |
| `0.98 ** n` によるdecayの新規実装 | 現行コードには実装がない。現行の補正式は `turn_idx/current_turn` による最大0.05の加算 |
| `resolved_ids` の解消状態の立て方、自動マージの類似度閾値、カテゴリ仕様 | 出所の時間情報とは別の問題である |
| UI変更・記憶モデルの大幅刷新 | 不要。既存の操作・進捗・件数制御を維持する |

---

## 4. 現行の問題と実際の到達経路

| 経路 | 現行の問題 | 到達条件・実害 |
|---|---|---|
| `memory_extract.py:248–255` | `st.get("total_turns")` が真なら、対象位置を無視して全て現在ターンになる | 非API＋`RPCHAT_MEMORY_BACKFILL != 0` 場合の後追いタイムアウト処理。主に `load` 内のスイープ（`server.py:1854`）から来る。通常抽出の全件バグではない |
| 同上 | ID未命中／配列不在のメッセージを `len(msgs)-1` に合わせて登録する | 削除・古いスナップショット等で、無関係な末尾会話の記憶を作る |
| `server.py:4828`（保存は `:4929–4933`）記憶再生成 | `cumulative_turns`（`:4867`, `:4886`）が再生成のたびに1から始まる | ローテーション後・直近100件の開始位置が失われる |
| 同上 | 10ターン/チャンク全て、チャンク末尾のターン・日付・midになる | ユーザーがUIの「再構築」「全消し再構築」から実行できる。件数・時刻が実際の出来事と一致しない |
| `server.py:11682–11686` | 直接提供記憶を `total_turns or len(user_indices)` に一括登録 | 「直近100件の件数」を通算ターンとして使う単位混在 |
| `memory_store.py:1324–1335` | `elapsed_time=None` をセッションの現在時刻または `1日目・午前` に補う | 歴史的に不明な記憶が、事実と異なる時刻を持つ |
| `memory_store.py:782–799`（`load_memories`） | `if not mem.get("elapsed_time")` で、キー欠落と明示 `None` を区別せず補完し `needs_resave` で上書き保存する | 保存した `null` が**次の読み込みで消える**。テストA6/M6/R5が成立しない |
| `server.py:10927–10941`（`/api/rag/memories`） | 同じ補完を、しかも `save_memories` 付きで行う | UIで記憶一覧を開くだけで `null` が破壊され、表示が「1日目・午前」になる |
| `memory_store.py:1183`（`filter_new_items`）/ `:1298–1302`（`valid_items`） | 同一バッチ内の同内容重複を先着順で1件に縮退する | 10ターン内で同文が抽出されると、古いターンの出所が採用され、新しい出所が捨てられる |
| `server.py:8257`（`total_after`） | `rotation_count * ROTATION_PERIOD` の回転補正を含まない | これを `:8428` の代替に使うと、旧セッションで 50→10 の後退 |
| `memory_store.py:1251`（併合は `:1394` / `:1446` / `:1502`） | 古い遅延抽出でも `turn_idx`・本文を上書きできる | 正しい番号計算に直すだけでは、逆方向の時間逆行が残る |
| `queued_memories` の`fallback`分岐 | ワーカー内部にはあるが、登録元が無い | 実経路の検証では未使用のキュー経路を要件化しない |

---

## 5. 共通ターン計算

### 5.1 配置と互換性

- 標準ライブラリだけの `rpchat/turn_index.py` を追加する。`memory_store`／ONNX／Windows固有処理へ依存させない。
- `rpchat/__init__.py` は現状ドックストリングのみで、package 経由の import に重い副作用は無い（確認済み）。
- 少なくとも次を提供する。

```python
def calc_turn_idx(msgs, i, total_turns=None): ...
def turn_index_base(setting, file_user_turns, rotation_period): ...
```

- `calc_turn_idx` の現行の計算と互換を保つ。

```python
idx_in_file = msgs[:i+1] 内の user 数
if idx_in_file == 0:
    return 0
if total_turns > 0 and file_total_user > 0 and total_turns >= file_total_user:
    return total_turns - (file_total_user - idx_in_file)
return idx_in_file
```

- `turn_index_base` は次を返す。

```python
total = setting.total_turns を整数化した値
if total > 0:
    return total
return rotation_count * rotation_period + file_user_turns
```

数値変換は `rpchat.util._as_int` に準ずる安全な変換（`None`/不正値→0）とする。`bool`を許容するかまで実装時に再設計せず、`server.total_turns_base` と同じ既存の扱いを優先する。

- `server.calc_turn_idx` は共通関数の別名またはごく薄いラッパーとして残し、既存API・テストを壊さない。
- `server.total_turns_base(st, file_turns)` の呼び出しシグネチャは維持し、内部で共通関数へ `ROTATION_PERIOD` を渡す。
- `read_past_messages.py` も `calc_turn_idx` の実装重複をやめて共通関数を使う。これにより標準ライブラリ単体の過去メッセージツールとしての独立性は保たれる。

### 5.2 異常メタデータの扱い

- `total_turns < file_total_user` の不整合時の `calc_turn_idx` は、当面の保護策として現行どおり**ファイル内相対番号にフォールバック**する。将来の正確な修復を行うと無断で番号が飛ぶため、本修正で新しいアルゴリズムに置き換えない。
- この条件は、実装中に誤った基準値を渡していないかをログで検出できるようにする。ただしログは同一セッションで高頻度に出し続けない（セッション単位の抑制、または照査テスト時の検証）。
  - 実装はセッションIDを持たない共通関数から呼ばれるため、`(total_turns, file_total_user)` の値ペア単位でプロセス内1回だけ `logger.warning` を出す（T5で初回検知と抑制を検証）。
- 0番への到達は仕様内の値とする（§2.1）。

### 5.3 Tier A での呼び出し

```python
base_total = turn_index_base(st, len(all_user_msgs), ROTATION_PERIOD)
turn_idx = calc_turn_idx(msgs, idx, base_total)
```

`ROTATION_PERIOD` は server側が起動時に `_memory_extract.ROTATION_PERIOD` を代入する既存受け渡しを維持する。ただし相互importは追加しない。

同じ補正を、新規本文メッセージの記憶登録（`server.py:8428`）でも使う。

```python
# server.py:8428 周辺（現行の `st.get("total_turns") or (...)` を置換）
user_msgs = [x for x in s["messages"] if x.get("role") == "user"]
turn_idx = turn_index_base(st, len(user_msgs), ROTATION_PERIOD)
```

**`server.py:8257` の `total_after` で代用しない。** あれは時間進行用に作られた値で `max(1, ...)` を含み、回転補正もない。ここで使うと `total_turns=0` の旧セッションで 50 が 10 になる。

turn_index_base 適用後に calc_turn_idx を通さないのは、ここが「今追記したばかりの末尾メッセージ」であり、基準値そのものが通算ターンになるためである。末尾位置の計算を経ると、ファイル内の user 数だけに劣化する。

### 5.4 対象が存在しないとき

- `m["id"]` があり、そのIDが `msgs` に無ければ **保存せず終了**する。
- IDを持たないメッセージについては、従来の配列内一致が成功した場合だけ受理する。配列にも無ければ終了する。
- `m.get("role") != "assistant"`、または正常な `idx` が得られないときも、本関数からの自動抽出は行わない。
- 現行の呼び出し元（`server.py:1854` のタイムアウトスイープ、`:9793` のワーカー分岐、テスト）はすべて assistant を渡しているため、role 検査は既存挙動を変えない。userメッセージを渡された場合の「無関係な記憶作成」だけを防ぐ。
- `len(msgs)-1` や負の `idx` を使って末尾に紐付けない。
- 例外を外部へ広げる現行の設計は変更しないが、単に抽出しないケースは `error` ではなく理由付きのdebugログ等にして、エラーログ乱発を避ける。
- fallbackのタイムアウト時に `memory_status` / `memory_ts` / `memory_claimed_ts` をクリアする既存処理は変更しない。

---

## 6. 記憶再生成の出所保持

### 6.1 基本設計：メッセージ単位メタ、保存はチャンク単位

次の両立を必須にする。

- 抽出対象は**各メッセージ**である。
- 保存APIへの投入は**従来のチャンク単位**である。

メッセージ単位で直ちに `save_extracted_memories` を呼んではいけない。それは、

- `reset=False` の1チャンク最大5件の制限
- 件数超過時の `MEMORY_PRIORITY` による選抜
- 同一チャンク内の重複除外と併合順

を変更し、記憶の数と品質まで変えてしまうためである。

したがって、各抽出項目に次の内部メタを付ける。

```json
{
  "category": "promise",
  "content": "ラピは指揮官と水族館へ行く約束をした",
  "_source_turn_idx": 43,
  "_source_mid": "msg_assistant_43",
  "_source_elapsed_time": {"day": 2, "period": "夜"}
}
```

- `_source_*` は保存関数へ渡すためだけの一時メタであり、記憶ファイル・APIレスポンスへ漏らさない。
- `_source_elapsed_time` は**キーの存在自体が意味を持つ**。値 `null` は「この項目の作中時刻は不明であり、現在時刻で補完してはいけない」の指示であり、キー欠落（従来呼び出し）とは区別する。

### 6.2 チャンクと直近100件の互換性

- 現行の `user_indices` による直近100件制限、`chunk_size = 10`、進捗件数・管理状態のキーを変更しない。
- `chunks[i]` は `list[message]` の形を維持する。既存テストと `run_memory_regeneration(sid, chunks, ...)` の互換性を保ち、新しい必須のチャンク形を導入しない。
- `run_memory_regeneration` のシグネチャは後方互換で拡張する：`run_memory_regeneration(sid, chunks, char_name, reset=False, source_index=None)`。`source_index` は `{message_id: {"index": int, "turn_idx": int, "mid": str|None, "elapsed_time": dict|None}}` の任意引数で、既存呼び出し（テスト含む）はそのままで動く。
- `turn_idx` は **チャンクリストの局所インデックスではなく、会話全体における該当IDのindexで算出**する。
- 1チャンクに複数のターンが混ざることを仕様上許容する。チャンク単位の一括時刻・一括midを使用しない。
- 出所マップ（後述の `source_index`）は、**直近100件への切り出しとチャンク分割より前**に、セッションの全 `messages` を先頭から走査して構築する。
  - 理由：チャンク先頭のユーザー発言が、前チャンク末尾のアシスタント発言の作中時刻を失わないため。チャンク内だけで時刻を補完してはならない。
  - 時刻の前方伝播（直近の有効なスナップショットを遡る）も、この全走査の中で一度だけ行い、結果をマップに焼き込む。
- 再生成後の `before` / `after` / `running` / `total` など進捗APIとUIは変更しない。
- 出所の特定は次の優先順とする。
  1. 呼び出し元が渡した `source_index`（同一スナップショットから作成。エンドポイント経路。ライブ読み直しをしない）
  2. 呼び出し元がマップを渡さない場合（テスト・直接呼び出し）は、ワーカー冒頭の `load(sid, readonly=True)` の `messages` から同じ規則で `source_index` を構築する
  3. どちらにもidが無ければ、その項目は保存対象から外し、理由をログに残す（位置の推測・cumulative番号での代用はしない）。削除・キャンセル処理は既存のゲートを継続する。
- エンドポイントのライブな `chunks` と、後から別バージョンを読んだマップを混ぜてはならない。

### 6.3 抽出ルールを変えない

- 現在 `run_memory_regeneration` が使っている `extract_memories_from_text` の対象文・カテゴリ補正を、時刻修正とは別に変更しない。
- 抽出された全項目に対して、その**文字列が書かれているメッセージ**を出所とする。ユーザーとアシスタントを別に抽出している現行方式に対して、出所をチャンク末尾へ移し替えない。
- 付与は抽出ループの中で行う。`extract_memories_from_text` は辞書を返すだけで出所を知らないため、次の形を明記する（現行 `server.py:4888–4895`）。

```python
for u in user_msgs:
    src = resolve_source(source_index, u.get("id"))   # 見つからなければ None
    for item in extract_memories_from_text(user_text=u.get("text", ""), ...):
        if src is None:
            skipped_unknown_source += 1
            continue
        item["_source_turn_idx"] = src["turn_idx"]
        item["_source_mid"] = src["mid"]
        item["_source_elapsed_time"] = src["elapsed_time"]   # None でもキーを作る
        extracted.append(item)
```

- `asst_msgs` 側も同じ。メッセージIDを持たないメッセージは、配列上の同一性で `source_index` を引く。それもできなければ出所不明として除外する。
  - 明示的な帰結：`source_index` を省略して `run_memory_regeneration` を直接呼び、かつ渡した `chunks` が別スナップショットのコピーで、メッセージIDも持たない場合、その項目は出所を確定できないため保存しない。IDがある場合、または同一オブジェクトを渡した場合は従来どおり解決される。
- `filter_new_items` は `dict(item)` を保持するため、内部メタを通せる。意図しないキーの削除をここで行わない。

### 6.4 作中時刻の採取

- 出所メッセージのスナップショットを優先し、欠けていれば**セッション全体**を前方向に遡って直近の有効なスナップショットを採用する（チャンク境界で切らない。§6.2）。
- 有効な辞書が得られない場合は `None` とする。`_source_elapsed_time` キーが存在することを「セッション現在時刻を補わない」指定とみなす。
- その `None` は、保存後・再読込後・一覧API経由でも `None` のままでなければならない（§3.2-6）。ここが崩れると本節の設計全体が無意味になる。
- 新規記憶に `None` が保存された場合は、既存のプロンプト表示が `turn_idx` を使う。これは正常な不明データの表現であり、欠陥ではない。

---

## 7. `save_extracted_memories` の仕様

### 7.1 引数互換性

既存シグネチャは維持する。

```python
save_extracted_memories(
    session_id, character_name, new_memories,
    resolved_ids=None, turn_idx=None, mid=None,
    elapsed_time=None, max_new=None, touch=True
)
```

- 内部の `prepared_items` は現在 `(cat, content, new_vec, tokens, h)` のタプルで持ち回っている。項目別出所を載せるため、この内部タプルへ出所情報を追加する（内部構造のみの変更で、外部シグネチャは変えない）。
- 既存呼び出しは `turn_idx` / `mid` / `elapsed_time` をそのまま使う。
- 新規追加の、各項目に `_source_turn_idx` / `_source_mid` / `_source_elapsed_time` があれば、それを「項目固有の出所」とする。既定の一括値より優先する。
- `_source_elapsed_time` というキー自体が存在する場合、値が `None` でもセッション時刻・初期時刻へのフォールバックを行わない。キーが無い従来呼び出しだけ、現行のデフォルト時刻補完を使う。
- `resolved_ids` による `resolved_turn` / `resolved_mid` / `resolved_elapsed_time` は既存の「呼び出し共通値」を維持する。`_source_*` で個別の解消対象を変更する機能は作らない。
- `_source_*` は保存ファイルとレスポンスの記憶オブジェクトへ漏らさない（取り除く）。
- 注意：現在は `valid_items` を作る段階（`memory_store.py:1278–1302`）で `(category, content)` のペアへ作り直しており、それ以外のキーはここで落ちる。`_source_*` はこのループ内で同時に取り出し、後の `prepared_items` タプルへ持ち回ること。ここを外すと「渡したのに無視される」静かな不具合になる。
- 同じバッチ内で同内容が重複した場合（同一 `content_hash`、または同一 `(cat, content)`）、**出所ターンが大きい方（新しい方）を残す**。同ターンなら先着を残す。現行の `seen_items` による先着順縮退は、10ターン/チャンク内で同じ文が2回抽出されたときに**古いターンの出所を採用してしまう**ため変更する。
- 同じ「新しい出所を優先」規則を `filter_new_items`（`memory_store.py:1183`）にも適用する。非破壊再生成は `filter_new_items` → `save_extracted_memories` の順で2回縮退するため、片方だけ直すと新しい出所がまた捨てられる。既存記憶と同ハッシュの項目を除外する挙動（既存の重複防止）は変えない。
- 実装は「1パスで `seen` を打って先着を残す」方式をやめ、同キーの候補を集めてから `_source_turn_idx` が最大の1件を採用する（2パス）。原リスト上の順序は、同じターン内の候補については先着優先（安定ソート）とする。`_source_turn_idx` を持たない従来項目は「不明」として先着を残す。

### 7.2 新規追加

出所が得られた項目は、新規append時にその値を保持する。

- `_source_turn_idx` → `turn_idx`
- `_source_mid` → `mid`
- `_source_elapsed_time` → `elapsed_time`（明示された `None` を含む）

再生成の `reset=False` だけ `mid=None` にしていた現行制限は、出所を確定できた新規項目には適用しない。出所情報があるにもかかわらず消す必要はない。これにより将来の `delete_by_mid` の正当性も向上するが、F-04そのものは修正しない。

### 7.3 既存記憶への併合：古い処理の巻き戻し禁止

- stale判定は、出所ターンを整数化して比較し、`new_turn < existing_turn` の項目を**併合不成立（stale）**とする。この判定は **`touch` の値に関係なく**、併合対象となった全項目に適用する。
  - 本文・embedding・tokens・content_hash・`turn_idx`・`mid`・`elapsed_time`・`updated_at`・historyを更新しない。
  - この結果、その項目を新規にも追加しない（同じ内容の新旧2件を作るため）。
  - 新規件数カウンタは増やさない。処理上は「採用せず破棄」となる。
- これは、現行の「`touch=False` でも本文は上書きする」挙動を、**古い出所のときだけ**意図的に変更するものである。非破壊再生成はチャンクを古い順に処理するが、**再生成開始前から最新ターンの記憶が存在し得る**ため、走査順だけでは巻き戻しを防げない。
- `touch=False` で `new_turn >= existing_turn` の場合は、現行どおり本文・embedding・tokens・hashを併合し、既存の `turn_idx` / `mid` / `elapsed_time` / `updated_at` は上書きしない。これは `test_rag_regen.py::TestNonDestructiveSave` の既存契約であり維持する。
- `touch=True` で `new_turn >= existing_turn` なら従来の併合を維持し、最新の出所情報を記録する。
- 既存ターンが `None`／不正値で、新しいターンが有効なら、既存値を新しい値で補正する。
- 新しい出所ターンが `None` で、既存ターンが有効なら既存ターンを消さない。後続のメタ比較も、None同士や日付文字列同士で例外を出さない。
- 同じターンで、新しい `mid`／`elapsed_time` が不明なら、既存の有効なmid・時刻を消さない。新規の有効な値のみ上書きする。これは現行の `if mid: ... update` と同等の方針である。
- このガードは3つの併合経路（`memory_store.py:1394`付近 event/promise、`:1446`付近 item/status、`:1502`付近 その他）で共通化する。ピン留め・ユーザー優先記憶を現行どおり併合対象から外す仕様も維持する。
- stale判定により暫定的な優先マージを拒否した後、別の類似記憶へ再付け替えする機能は今回作らない。対象項目は破棄する（歴史的項目不整合を増やさないため）。

※stale拒否により、`reset=True` の全件作り直しでも「古いチャンクの項目が新しい記憶より後に来る」場合は併合されない。これは意図した挙動である。`reset=True` は事前に自動記憶を消去するが、**手動・優先記憶は残る**ため、それらへの巻き戻しを防ぐ必要がある。併合先が消えている場合（自動記憶のみの再生成）は、単純な新規追加となり従来どおり件数が増える。

---

## 8. API `d["memories"]` の出所必須化

### 8.1 入出力

直接提供する記憶には `source_mid` を要求する。

```json
{
  "category": "event",
  "content": "ラピは海へ行った",
  "source_mid": "msg_assistant_12"
}
```

- `source_mid` は対象セッションに現存するメッセージIDでなければならない。
- サーバーはメッセージ内容から `turn_idx`・`mid`・`elapsed_time` を再計算する。外部から指定された `turn_idx` / `elapsed_time` を信用しない。
- `source_mid` 欠落、ID不明、対象が不正な場合は **HTTP 400** とする。現行のような現在ターンへの一括登録は行わない。
- 現行のUIは `memories` を送らず、ログ解析経路のみを使っていることを確認済み（`public/app.js:9482` は `session_id` と `reset` のみ送信）。直接提供は互換APIで、今回から出所情報を明示する。
- `source_mid` 方式に**一本化**する。`source_turn_idx` などクライアント供給の代替経路は設けない（仕様の曖昧さを残さない）。リポジトリ内で `d["memories"]` を送っているのは `test/test_rag_regen.py` の1テストだけで、UI・外部ツールの利用は確認できていない。
- `source_mid` を付与できない呼び出し元は、このAPIを使わず通常の会話ログ経路を使う。
- 既存テスト `test_memories_branch_is_non_destructive_by_default` は `source_mid` を追加して更新する。
- 直接提供の各項目において、不正な `category`（未定義カテゴリ）や空の `content` が含まれる場合、または `memories` が空リストの場合は事前バリデーションにより **HTTP 400** で即時拒否する（§8.2 の自動記憶消去防止を徹底するため）。正常項目の `filter_new_items` による重複除去、`max_new=200`、非破壊既定は従来どおり。

### 8.2 無効入力による消去を防ぐ

`reset=True` の `clear_auto_memories(sid)` は、**全入力と全 `source_mid` の検証完了まで実行しない**。無効な入力だけで自動記憶が失われてはいけない。バックアップと進捗メタも、従来の実際の実行時点から作る。

---

## 9. 変わらないもの・変わるもの

### 9.1 維持

- ユーザー操作、UI文言、再生成進捗ポーリング。
- `reset=False` 既定、`reset=True` での自動記憶の退避と手動・優先記憶保護。
- 直近100ターン対象、10ターン/チャンク、最大新規件数とカテゴリ優先選抜（`max_new`, `MEMORY_PRIORITY`）。
- `filter_new_items` の同内容除外（既存記憶との重複防止）と、ピン留め等の併合対象除外。ただし**同一バッチ内の重複は、より新しい出所を優先**する（§7.1）。
- `_store_lock`、非同期保存、セッション削除・リセット時の作業中止ゲート。
- 想定される現実時刻と作中時間の意味、ローテーションとI12。

### 9.2 意図的に変更

- 後追い抽出は対象位置の過去番号を持つため、autoの `status` / `item` が、正しく20/60ターンTTLで除外され得る。
- 再生成で抽出された新規記憶が、チャンク末尾ではなく正しい出所ターンと時刻を持つ。
- 時刻不明を現行時刻や初期時刻に偽装しない。読み込み・一覧APIでも `null` を保持する（旧データの自動補完はキー欠落時のみ）。
- 古い遅延抽出による新しい記憶への上書きを止める。`touch=False` の非破壊再生成でも、出所が古い項目は本文を上書きしない。
- 出所不明な直接提供は400で拒否する（`source_mid` 一本化）。

---

## 10. テスト仕様

すべて実データを分離し、embeddingは決定的なダミーに差し替える。外部モデル・ネットワークに依存させない。同じ成功でも「保存項目が1件ある」ではなく、出所の値まで検証する。

### 10.1 共通関数／異常系

| # | 条件 | 期待値 |
|---|---|---|
| T1 | 60ユーザー分の会話で3ターン目assistant | 現行fallbackは60、修正後は **3** |
| T2 | `total=60`, ファイルには過去41～60の20ユーザー分、2番目user | **42** |
| T3 | `total=0`, `rotation_count=2`, ファイル10user、3番目のuser（またはその直後のassistant） | **43**（3へ後退させない） |
| T4 | 先行userが無い単独assistant | **0** |
| T5 | `total < file_user_count` の不整合 | 例外なく、現行の相対番号へ戻る。ログ抑制方針も確認 |
| T6 | prune済みでexchange先頭・中間・末尾、連続user複数、user/assistant混在 | それぞれ通算数式に一致し、assistantは直前userと同番号 |
| T7 | `read_past_messages.py` をヘビー依存の無い環境でimport | 標準ライブラリだけで利用可能 |

### 10.2 Tier A／実経路

| # | 観点 | 手順と期待 |
|---|---|---|
| A1 | fallbackの保存引数 | 3ターン目assistantに`memory_status`と古いタイムスタンプを設定し、実際のタイムアウトスイープを通す。保存の`turn_idx=3`、`mid=対象assistant ID`、`elapsed_time=対象の時刻`を確認 |
| A2 | ステータス後始末 | A1後に`memory_status`/`memory_ts`/`memory_claimed_ts`が消える（L-01対象外のタイムアウト仕様が壊れていない） |
| A3 | 未登録/削除済み | IDが存在しないメッセージをfallbackへ渡す。保存関数未呼び出し、別の末尾user textを使わない |
| A4 | IDのない既存メッセージ | 配列内で参照できれば0/正しい番号で従来どおり処理でき、配列外なら保存しない |
| A5 | 旧セッションの新規登録 | `total_turns=0`, `rotation_count=2`, ファイル10user の状態で新規assistantを追記。保存される記憶の `turn_idx` が **50**（`total_after`=10 へ後退しない）。`server.py:8257` の値は変更されない |
| A6 | 作中時刻の不明（保存後も維持） | 時刻スナップショットの無いメッセージでfallbackを実行。`turn_idx`は正しい位置、`elapsed_time`は現在の作中時刻ではなく`None`。プロンプト表示は「約Nターン前」に退避する |
| A7 | 一覧APIの往復 | A6の後、`/api/rag/memories` を呼んでも `elapsed_time` は `None` のままで、`1日目・午前` に化けず、再保存による変化もない |

### 10.3 併合（3分岐すべて）

| # | 条件 | 期待 |
|---|---|---|
| M1 | `touch=True`, 既存turn=60, 新規turn=3の類似 `rule`（または `char_fact`） | 内容・時間情報とも60側を保持。新規重複も作らない。履歴・更新時刻を無闇に増やさない |
| M1b | `touch=True`, 既存turn=4, 新規turn=3の類似 **`event`**（ターン差1以内） | 同上。拒否後に新規追加へフォールスルーしない（`event` はターン差1以内でしか類似判定しないため、この条件でしか検証できない） |
| M2 | M1をitem/statusとcalling/char_factなど別分岐でも実施 | 同じ退避を共通化して通過 |
| M3 | `touch=True`, 既存turn=3, 新規turn=60 | 従来どおり本文・出所情報が進み、historyが構築される |
| M4 | `touch=False`, 既存turn=3, 新規turn=99 | 既存の時刻等が不変で、従来の本文併合は維持される |
| M4b | `touch=False`, 既存turn=60, 新規turn=3 | **本文も巻き戻さない**。history・updated_atも増えない。新規重複も作らない（非破壊再生成の経路でもstale拒否が効く） |
| M5 | 既存turn=None、新規turn=3 | 有効値で補完・例外なし |
| M6 | 新しい出所時刻=None、既存に有効な時刻 | 併合で既存値を消さない。新規記憶なら時刻Noneを保存し、**再読込・一覧APIを経てもNoneのまま**、現時刻に補完しない |
| M7 | ピン留め／手動優先記憶 | 既存の併合からの除外条件が変わらない |
| M8 | 同一バッチ内に同じ本文が古いturn→新しいturnの順で2回含まれる | 保存される出所は**新しいturn**。`filter_new_items` 経由（reset=False）でも同じ結果になる |

### 10.4 記憶再生成／直接提供

| # | 条件 | 期待 |
|---|---|---|
| R1 | `total=60`, 41～60の10user, 2 chunks, 同じassistantにも複数のカテゴリ抽出 | `cumulative_turns` を廃止しても各項目が出所の41～60を持つ。midと時刻はチャンク末尾でなくメッセージの値 |
| R2 | 101+ユーザーターンで直近100件が選ばれる | 1～100ではなく、絶対ターンN-99～Nになる |
| R3 | `reset=False`, 1チャンク6件以上＋低優先カテゴリ混入＋同一本文の重複 | 従来どおり最大5件、優先度順、重複は1件で**新しい出所**を保持。残った全項目の正しい出所が保持される。同じ再実行で増えない |
| R4 | `reset=True`の古い順のチャンク | 破壊的修復・backupと時刻更新方針を壊さず、出所が正しい |
| R5 | 時刻欠落メッセージ | 現時刻ではなくNoneまたは直前スナップショットの採用。**再読込・一覧API後もNoneが維持**される。プロンプトではターンベース表示に逃げる |
| R6 | 未登録source_midの直接提供 | 400。`reset=True` でも消去・backup増加・進捗開始が起きない |
| R7 | 有効なsource_midの直接提供 | 出所ターン/時刻/midが正しく、クライアント供給の時刻を鵜呑みにしない。重複再送で件数不増 |
| R8 | 削除・キャンセル | 従来のゲートが働く。出所不明を推定せず後段でも保存しない |

### 10.5 既存テストとの互換性（変えてはいけない振る舞い）

| 既存テスト | 期待される扱い |
|---|---|
| `test_boost_changes.py::test_fallback_extraction_matching_by_id` | 合格を維持する（IDが存在するので `turn_idx` 修正の影響を受けない） |
| `test_new_session_fix.py::test_4_extract_memories_fallback` | 合格を維持する（`total_turns=5`・2userなので `calc_turn_idx` で同じ値） |
| `test_new_session_fix.py::test_4_run_memory_regeneration` | 合格を維持する（chunkは `list[message]` のまま） |
| `test_session_cleanup_zombie_prevention.py` の `run_memory_regeneration` 3件 | 合格を維持する（供給したchunkがセッションに存在しない場合は出所不明として保存しないだけ。中止ゲートの期待値は不変） |
| `test_rag_regen.py::TestNonDestructiveSave` 4件 | 合格を維持する（`test_merge_without_touch_keeps_metadata` は 99 >= 3 なので従来どおり本文併合される）。stale拒否は新規テストM4bで検証する |
| `test_rag_regen.py::TestRunMemoryRegeneration` / `TestRegenerateEndpoint` | 件数と非破壊性の期待は不変。`turn_idx` の具体値は未検証なので、新規に `turn_idx` / `elapsed_time` の検証を追加する |
| `test_rotation_pipeline.py::T6` | 合格を維持する（末尾35のまま） |
| `test_rag_regen.py::test_memories_branch_is_non_destructive_by_default` | **更新必須**。`source_mid` を付与しないと400になるため、ペイロードに `source_mid` を追加する |

### 10.6 実行コマンド（Windows、プロジェクトルート）

```bat
python -m unittest test.test_memory_turn_index test.test_memory_merge_stale test.test_rag_regen test.test_memory_backfill test.test_boost_changes test.test_rotation_pipeline test.test_new_session_fix
python tools/run_tests.py
```

- 追加・拡張するテストは次のとおり。
  - `test/test_memory_turn_index.py` … §10.1 のT1～T7、§10.2 のA1～A6（A6は `memory_store` 直接呼び出しで保存→再読込まで確認。HTTPは使わない）
  - `test/test_memory_merge_stale.py` … §10.3 のM1～M8（3分岐とtouch両方のstale拒否、同一バッチ重複）
- §10.4 のR1～R8と §10.2 のA7（一覧APIの往復）は、既存の `test/test_rag_regen.py` を拡張する（HTTP足場と `_ServerTempStore` が既にある）。
- 全量テストについて、既存の既知失敗と新規失敗を区別する。修正前のベースラインを保存し、「0件になって検出されていないテスト」ではないことを確認する。

---

## 11. 実装手順・影響管理

### コミット1：ユーティリティ＋Tier A

1. `rpchat/turn_index.py` 追加、三方向の互換参照、legacy補正含む単体テスト（`test/test_memory_turn_index.py`）。
2. fallbackの位置特定・時刻・保存だけを修正。
3. `server.py:8428` を `turn_index_base` に置換（`total_after` は使わない）。
4. A1～A6／T1～T7を通す。

### コミット2：保存側の項目別出所と巻き戻し防止

1. `_source_*` を `prepared_items` に保持。
2. `elapsed_time: null` の存続を、`load_memories` と `/api/rag/memories` の2か所で保証する（§3.2-6）。**先にこれを入れないとA6/A7/M6/R5が検証不能になる。**
3. 同一バッチ内の重複は「新しい出所優先」に変更（`valid_items` と `filter_new_items` の両方）。
4. 併合の時間逆行ガードを3分岐へ共通化し、**`touch` の真偽に関係なく**適用。
5. A7（一覧APIの往復）とM1～M8（`test/test_memory_merge_stale.py`）と §10.5 の既存テストを通す。

### コミット3：再生成経路

1. セッション全メッセージを先頭走査する `source_index`（時刻の前方向伝播込み、チャンク分割前）を作り、抽出項目へ出所を供給。
2. 直接提供の `source_mid` 事前検証を導入。
3. R1～R8を通す（`test/test_rag_regen.py` を拡張）。
4. 全量スイートを実行し、UIの進捗・件数表示を変えていないことを確認する。

ロールバックは各コミットを独立して戻せる構造とするが、コミット3のデータ校正はコミット2の項目別メタに依存するため、3だけ先に戻す順序を許容する。既存データの過去番号の無条件な書き換えは、いずれのフェーズでも行わない。

---

## 12. 完了条件

- §3.1／§3.2 の全項目が実装されている。
- Tier Cのファイル・挙動は意図的に変更されていない。
- 本仕様の時間番号（通算位置・legacy補正）を実際のタイムアウト／再生成で確認し、ユーティリティ単体だけでは完了としない。
- `elapsed_time: null` が、保存→再読込→一覧API→プロンプト生成の全経路で `null` のままであり、`1日目・午前` や現在時刻に補完されない。
- 過去遅延抽出の併合が、`touch=True` でも `touch=False` でも時間・本文を巻き戻さない。
- 同一バッチ内の重複が、古い出所ではなく新しい出所を残す（2層の縮退の両方）。
- 新規記憶登録（`server.py:8428`）が `rotation_count` を失わない（50→10 の後退がない）。
- メッセージごとの保存時刻と件数抑制が同時に成立し、落ちた項目数があるだけの埋まった結果に見せかけない。
- legacyセッション、prune後、100件制限、時刻不明、無効な出所IDの各ケースがテスト済みである。
- 全量テストに新規失敗がない。

---

## 13. 実装時に再検討しない確定事項（後戻り防止）

1. **素朴な `calc_turn_idx(..., st.get("total_turns"))` へ置換しない**（43→3など後退の防止）。
2. **再生成でメッセージ単位ファイル保存しない**（チャンク上限と優先度を保つ）。
3. **出所不明に現在ターン・現在時刻を付けて救済しない**。
4. **古い遅延項目で新しい併合を成立させない**。`touch=True` / `touch=False` の両方で拒否する。
5. **無効な `source_mid` で自動記憶を消去しない**。
6. **過去データの一括再番号付け・F-04を同じ変更に混ぜない**。
7. **`memory_store` への重たい依存をstandaloneツールへ持ち込まない**。
8. **本書に無い、新しい減衰式や新しい記憶カテゴリを追加しない**。
9. **`elapsed_time: null` を読み込み時に補完しない**。補完対象は「キー自体が無い旧データ」だけとし、`load_memories` と `/api/rag/memories` の両方で守る。
10. **`server.py:8257` の `total_after` を記憶のターン番号に使わない**。回転補正が無く、旧セッションで 50→10 の後退を起こす。
11. **直接提供APIに `source_turn_idx` などの代替経路を設けない**。`source_mid` 必須の一本化を維持する。
