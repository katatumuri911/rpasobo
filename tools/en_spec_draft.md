# Test A：spec 英語化ドラフトとオフライン計測（ラピ）

対象: `server.output_text(st, is_diff=True)` の compact 分岐（server.py 2392–2445 付近）
計測: `tools/measure_spec_en.py` / ドラフト: `tools/en_spec_text.py`（どちらも課金ゼロ・実サーバー非接続）
測定日: 本セッション / channel: ラピ(催眠) `95a409720c`

## 1. 計測環境

- `tiktoken` / `transformers` / `sentencepiece` は**いずれも未インストール**（`ModuleNotFoundError`）。
  そのためトークン数は測らず、**bytes / chars の実測**で評価する。
- 判断基準は既存の較正 **約 1,700B ≒ 1秒** と、`wait_input.py:30` の **9,400B**（spill 閾値）。
- `server.py` 側の `LONG_BYTES` は 9000 のまま（R-3 の既知ズレ）。本計測は 9,400B を基準にした。
- 計測は `RPCHAT_DATA` を一時ディレクトリへコピーし、`build_context()` を実際に通して
  `sections + same 行 + rules + boiler(1000B)` を再計算（`wait_input.py` と同じ式）。

### パターン

| パターン | 作り方 |
|---|---|
| 通常 | ラピ ch に未送信の通常会話を 1 件追加。直前 state の `action` / `situation` を非エロに差し替え |
| 濡れ場 | 同じ ch に行為中の発言を 1 件追加。`situation` / `action` に行為タグが入った実 state を使用 |

※ 現行 `server.py` に `is_in_action_hold` は存在しない（handoff 記載の関数はこの版には未実装）。
そのため「濡れ場」は state の `situation` / `action` タグで再現した。spec 文面自体は両パターンで同一。

## 2. 結果（ラピ）

### spec 単体

| | bytes | chars |
|---|---:|---:|
| 日本語 spec（現行 diff×compact） | 2,521 | 1,289 |
| 英語 spec（ドラフト） | **1,994** | 1,766 |
| 差 | **−527B（−20.9%）** | |

JSON サンプルブロック（440B）は原形維持のため共通。削減は説明文だけ。

### payload 再計算

| パターン | 日本語 spec | 英語 spec | spec 英語化の差 |
|---|---:|---:|---:|
| 通常 | 12,030B | 11,503B | −527B（−4.4%） |
| 濡れ場 | 12,048B | 11,521B | −527B（−4.4%） |

**英語化のみでは 9,400B を切らない**（通常 +2,103B / 濡れ場 +2,121B 超過）。

### 間引き案

| 案 | 通常 | 濡れ場 | 9,400B 判定 |
|---|---:|---:|---|
| 英語 spec + キャラ間引き（`chara.md` を毎回送らない） | **9,004B** | **9,022B** | ぎりぎり可（−396 / −378B） |
| 英語 spec + キャラ・指示間引き（3層設計の定常ターン） | **7,844B** | **7,862B** | 可（−1,556 / −1,538B） |
| 日本語 spec + キャラ・指示間引きのみ | 8,371B | 8,389B | 可（−1,029 / −1,011B） |

- 英語化の寄与は全体で −4.4%。**主効果は間引き**（キャラ 2,499B + 指示 1,160B）。
- 「英語 spec + キャラ間引き」は合格ラインまで **400B 弱**しか余裕がない。
  記憶（現在 2,066B）や状態（877B）が少し増えると再 spill する。
- 日本語のままキャラ・指示を間引いても 8,371B で通る。**spill 回避だけなら英語化は必須ではない。**

### 内訳（通常 / 英語 spec）

| 要素 | bytes |
|---|---:|
| 指示 | 1,160 |
| キャラクター | 2,499 |
| 作中経過時間 | 30 |
| spec | 1,994 |
| 固定キャラ | 201 |
| キャラの状態 | 877 |
| 過去の記憶 | 2,066 |
| ユーザーの発言 | 72 |
| セクション見出し枠 ×8 + same 行 + rules + boiler | 約 2,600 |

## 3. 判定（handoff §4 のルールに照らす）

| ルール | 本計測 |
|---|---|
| −40% 以上・品質合格 → 本実装 | 最大でも 3 層定常で **−34.8%**（7,844B）。−40% には届かない |
| 英語が混ざる → 縮小案 | 未検証（Test B の品質検査待ち） |
| −20% 未満 → 英語化中止 | 全体では −4.4%。**spec 単体では −20.9%** |
| JSON 崩れ / `.bad` 増加 → 中止 | 未検証（Test B） |

**推奨**: 英語化単独では spill を解消できないため、本実装の主軸は**間引き（3層化）**に置く。
英語 spec は「キャラ・指示を間引いてもなお余裕が欲しい」場合の追加策として扱う。
まず Test B で英語 spec の品質 6 項目を確認し、英語混入が無ければ
「英語 spec + キャラ・指示間引き」で進めるのが安全。

## 4. 対訳表（人が文言確認する用）

`原形維持` = 翻訳禁止・そのまま残す要素。

| # | 日本語（現行） | 英語（ドラフト） | 備考 |
|---|---|---|---|
| 1 | 応答は wait_input.py が表示した outbox に UTF-8 JSON で書く（session と reply_to はそのまま）。1回だけ書き、同じ内容を再出力しない。 | Write UTF-8 JSON to the wait_input.py outbox (session/reply_to as-is). Once; no duplicate. | 「session」「reply_to」「wait_input.py」「outbox」は原形 |
| 2 | ```json …（サンプル）… ``` | 同左（変更なし） | 原形維持。サンプル値も日本語のまま |
| 3 | キー: 必須 text / state / image。任意 memories / memo / elapsed_time / resolved_ids / memory_query。 | Required: text/state/image. Optional: memories/memo/elapsed_time/resolved_ids/memory_query. | キー名は原形 |
| 4 | - text: 一人称視点（視点キャラは1人に固定、他キャラとユーザーは三人称、ユーザーの内心と知覚外は書かない）。地の文は客観描写にせず、そのキャラの脳内の肉声（心の声・ツッコミ）で書く。メタ禁止。長さは通常300字以内・最大600字（チャンネルの「指示」に指定があれば優先）。セリフの個数と並べ方は末尾の「応答スタイル」に従う。 | - text: one fixed 1st-person POV; others+user 3rd person; never the user's inner thoughts or unseen events. Inner voice (thoughts, tsukkomi), not objective narration. No meta. <=300 JP chars, max 600 in action/climax (channel「指示」 wins). Dialogue count/order: follow「応答スタイル」. | 「指示」「応答スタイル」は原形 |
| 5 | - memories: 催眠・暗示・刷り込み・「〜するようになる」「〜は常識」など今後も効き続ける決まりをユーザーが追加したら、必ず rule で出す（キャラ名を主語にした三人称1文）。呼称・約束・所持品・痕跡・関係の変化も同様。該当なしは省略。 | - memories: lasting rule added by user (hypnosis/suggestion/imprint,「〜するようになる」「〜は常識」) → always category rule, one 3rd-person sentence, char as subject. Also names/promises/items/traces/relationships. Else omit. | `rule` と「」内は原形 |
| 6 | - 任意: memo（10ターンごと／大きな場面転換時のみ全文）、elapsed_time（時間帯が進んだ時だけ。移行はセリフで口に出させず地の文で描写）、resolved_ids（達成・解除した記憶ID）、memory_query（想起用キーワード）。 | - Optional: memo (full; every 10 turns/major scene change), elapsed_time (only when period advances; narrate, not dialogue), resolved_ids (done/cleared mem IDs), memory_query (recall keywords). | キー名は原形 |
| 7 | - state: pose と expression は毎回必ず。background と camera は変わった時だけ3個ずつ。outfit/outerwear/underwear/equipped_items/action/body_fluids は変化した項目のみキーを追加（省略は維持）。【重要】終了・解除した状態は必ず「なし」（equipped_items は []）で上書き。すべて Danbooru 英語タグ。penis_present は画面内に男性器が写る時だけ true。 | - state: pose+expression every turn; background+camera only when changed, 3 each; outfit/outerwear/underwear/equipped_items/action/body_fluids only changed keys (omit=keep). IMPORTANT: overwrite ended/removed with「なし」(equipped_items: []). Danbooru English tags. penis_present only if male genitalia visible. | `なし` / `[]` / キー名 / `penis_present` は原形 |
| 8 | - image: image.characters（この画像に写る固定キャラ名の配列。固定キャラ一覧と同じ表記）を毎回書く。image.prompt はサーバーが state から作るので書かない。characters は初登場時・不変の外見が変わった時だけ。 | - image: image.characters every turn (visible fixed names, same spelling as 固定キャラ一覧). Never image.prompt (server builds it from state). characters only on first appearance/immutable change. | `image.characters` / `image.prompt` / `characters` / 固定キャラ一覧は原形 |
| 9 | （追加必須）出力言語は日本語（text / inner / situation / memo / memories.content） | - Language: Japanese for text/state.inner/state.situation/memo/memories.content; keep「」and「なし」as-is. | 3行分を1行に統合。内容は同じ |
| 10 | （追加必須）「」はそのまま／`なし` は日本語のまま | （#9 に統合） | |

### 原形維持の確認

- 「」内のセリフ例・みさくら語: 今回の compact 分岐には含まれない（full 分岐のみ）。**full 分岐は英語化しない**前提。
- `なし` / `equipped_items: []` / `penis_present` / enum（`rule` 等）: すべて残置。
- JSON サンプルの値（`キャラ名` `今の体勢` `今の表情` `状況・体調` `内心` `ロールプレイ本文…`）: すべて日本語のまま。
- ラベル `応答の書き方（outbox の JSON）`: ラベルは `output_text()` の外（`settings_parts`）なので**変更なし**。perf.log の `output_chars` 検索（G-19）も維持。

## 5. 英語 spec 全文（ドラフト）

````text
Write UTF-8 JSON to the wait_input.py outbox (session/reply_to as-is). Once; no duplicate.
```json
{"session": "<session>","reply_to": "<reply_to>","text": "ロールプレイ本文（末尾の「応答スタイル」に従う）","image": {"characters": ["キャラ名"]},"state": [{"name": "キャラ名","pose": "今の体勢","expression": ["今の表情"],"situation": ["状況・体調"],"inner": "内心"}],"memories": [{"category": "rule","content": "キャラ名は…という決まりを守る（三人称1文）"}]}
```
Required: text/state/image. Optional: memories/memo/elapsed_time/resolved_ids/memory_query.
- text: one fixed 1st-person POV; others+user 3rd person; never the user's inner thoughts or unseen events. Inner voice (thoughts, tsukkomi), not objective narration. No meta. <=300 JP chars, max 600 in action/climax (channel「指示」 wins). Dialogue count/order: follow「応答スタイル」.
- memories: lasting rule added by user (hypnosis/suggestion/imprint,「〜するようになる」「〜は常識」) → always category rule, one 3rd-person sentence, char as subject. Also names/promises/items/traces/relationships. Else omit.
- Optional: memo (full; every 10 turns/major scene change), elapsed_time (only when period advances; narrate, not dialogue), resolved_ids (done/cleared mem IDs), memory_query (recall keywords).
- state: pose+expression every turn; background+camera only when changed, 3 each; outfit/outerwear/underwear/equipped_items/action/body_fluids only changed keys (omit=keep). IMPORTANT: overwrite ended/removed with「なし」(equipped_items: []). Danbooru English tags. penis_present only if male genitalia visible.
- image: image.characters every turn (visible fixed names, same spelling as 固定キャラ一覧). Never image.prompt (server builds it from state). characters only on first appearance/immutable change.
- Language: Japanese for text/state.inner/state.situation/memo/memories.content; keep「」and「なし」as-is.
````

（`suggest_choices` が有効なら `- choices: 8 natural player replies (label <=10 chars, text:「短いセリフ」＋具体的な行動).` が `- text` の前に挿入される。`image_mode=off` なら image 行が `- No image or characters.` に変わる。）

## 6. 実装時の注意（Test B 以降）

1. **`tools/check_context.py` の `spec_has_required_words` が英語 spec で落ちる**。必須語が
   `催眠 / 一人称視点 / pose と expression は毎回必ず / image.characters / memories / reply_to`。
   英語化するなら判定語を英語側（`hypnosis` / `1st-person POV` / `pose+expression` 等）へ更新するか、
   `RPCHAT_SPEC_EN=1` のときは別判定にする。**本番前に必ず対応**。
2. `tools/baseline.json` は文言変更後に `--save` し直す（`--check` は古い基準で失敗）。
3. Test B は `RPCHAT_SPEC_EN`（既定 `0`）で compact 分岐のみ差し替え。既存 ch を触らず、
   スクラッチ 2ch（`ide_session` 空）で A/B。終了後フラグを `0` に戻す。
4. `sigs` トラップ: spec を切り替えると `output.md` の sig が変わる。フラグ切替後に
   「前回と同じ」で消えないことを確認する（`sent["sigs"]` を再生成）。
5. `writing_style.md` は英語化禁止（日本語表記規則そのもの）。
6. 本文の出力言語は英語 spec の `- Language:` 行だけに依存する。プロンプト全体が英語になるため、
   **英語の地の文が漏れるリスク**は Test B の最重要検査項目。

## 7. 参考：現行の欠落

- handoff §8 が参照する `docs/prompt-size-plan.md` / `docs/speedup-remaining-plan.md` は
  このチェックアウトに存在しない（`docs/` は `decensor-body-plan.md` と本 handoff のみ）。
  git 管理にも入っていないため、別環境の文書と思われる。
- `is_in_action_hold` は現行 `server.py` に存在しない（§1 参照）。
