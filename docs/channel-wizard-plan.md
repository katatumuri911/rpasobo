# AIチャンネル作成ウィザード 完全版実装プラン（コード付き）

- 対象: `C:/Users/nukon/RPasobou`
- 版: 2.0（wizard_tags.json の defaults 移行）
- 実装範囲: Phase 0 + Phase 1
- この文書は、そのまま実装に移せるように、目的・最終ゴール・データモデル・API・サーバーコード・フロントコード・CSS・HTML/JS差分・テスト・受け入れ基準までを含む。

---

## 1. 目的

「＋（新規チャンネル）」を押したとき、**AIとの短い質疑応答だけで、キャラクター・世界観・関係性・第一声まで揃った新規チャンネルを作れるようにする**。開幕画像は、チャンネル完成を待たせず、最初のAI発言に非同期で生成する。

現状の「空のチャンネルを作ってから、設定画面で全部入力する」導線は残しつつ、以下の課題を解決する。

1. 設定項目が多く、最初の会話までが遠い。
2. 何を決めればよいか分からず、空のまま放置されやすい。
3. キャラクターの画像タグや口調、関係性がバラバラで、後から直すのが大変。
4. 作成のたびに本文AIの設定を選び直すのが面倒。
5. AIが使えない・失敗したときに、チャンネル作成自体が止まってしまう。

このウィザードは、**自由チャット形式ではなく「スロット埋め＋5択補完＋一括compose」**で実現する。質問は最大6問・2ラウンド（1ラウンド最大3問）に制限し、すべてスキップ・おまかせ可能にする。

また、作成の最初に「引き継ぎ元」を確認し、現在のチャンネルから画像設定・参照ファイル・プロジェクト・ユーザー名・本文AI設定を引き継ぐか、初期設定で作るかを選べるようにする。ストーリー・進捗・IDE会話は常にリセットする。

---

## 2. 最終ゴール（受け入れ条件）

### 2.1 必須ゴール

| # | ゴール | 合格条件 |
|---|---|---|
| G1 | 初回に作成AIを選べる | プロバイダー・モデル・思考の深さを選び、接続テストができる。設定はアプリ全体に保存される |
| G2 | 1行のアイデアから始められる | 空でも「おまかせ」で進める |
| G3 | 相手とあなたを決められる | オリジナル／版権／おまかせ、作品名、名前、開始地点、原作準拠度を設定できる |
| G4 | 関係性・世界観・雰囲気・特殊要素を選べる | タグの上限を守り、NGと衝突したらNGを優先する |
| G5 | AIが最大6問・2ラウンド質問する | 1ラウンド最大3問。すべてスキップ／おまかせ可能 |
| G6 | プレビューで確認・編集できる | タイトル、あらすじ、キャラ名、年齢、口調、開始状況、第一声、あなたの名前、プロジェクトを編集できる |
| G7 | ワンタップで作成できる | 作成後に新チャンネルへ切り替わり、第一声が入っている。開幕画像は非同期で生成中/完成 |
| G8 | 二重作成しない | 連打・再送・リロードでも同じ `sid` を返す |
| G9 | AIなしでも必ず作れる | AI未設定・失敗・タイムアウト時はテンプレートcomposeで作成できる |
| G10 | リロードで続きから再開できる | `draft.step` と `status` から画面を復元する |
| G11 | 既存導線を壊さない | 「空のチャンネル」と既存 `/api/session/new` のテストが通る |
| G12 | モバイルで使える | 375px幅で全画面表示、FABと重ならない、Esc/戻るで閉じられる |
| G13 | 最初に引き継ぎ元を確認できる | 「＋」直後に、現在のチャンネル名と「このチャンネルから引き継ぐ／引き継がない（初期設定）」を表示する。引き継がない場合は初期設定で作れる |
| G14 | 開幕画像を非同期生成できる | 作成直後に最初のAI発言へ画像が `queued` で付き、チャンネルはすぐ使える。画像OFFなら生成しない。失敗・タイムアウトでもチャンネルは壊れない |

### 2.2 品質ゴール

- 全員18歳以上の成人として扱う。18歳未満の年齢・高校生以下を示す表現は警告し、年齢を「なし」に校正する。
- 版権キャラは原作の設定を尊重し、版権タグはAI生成も許可する。未成年キャラは年齢を「なし」に。校正プリセットがあれば優先する。
- NGと特殊要素が衝突したらNGを優先する。
- チャンネル作成AIと本文AIを分離する。本文AIの設定は変更しない。
- 最初に「引き継ぎ元」を確認する。引き継ぐ場合のみ、画像設定・参照ファイル・プロジェクト・ユーザー名・本文AI設定をコピーし、ストーリー・進捗・IDE会話は必ずリセットする。
- プロンプトインジェクション対策として、AI出力はプレーンテキスト化し、許可キー以外は設定に入れない。
- 開幕画像はチャンネル作成をブロックしない。画像OFFなら生成せず、失敗・タイムアウトでも第一声とチャンネルは使える。

### 2.3 非ゴール（今回やらない）

- 本文生成AI・画像生成AIの変更。
- 既存チャンネルの一括移行。
- 版権キャラのデータベース化。
- 複数キャラクターの同時作成（Phase 1は相手1人＋あなた）。
- 開幕画像の同期的な完成保証（画像の完成を待ってから「作成完了」にはしない）。
- 別チャンネルを一覧から選ぶUI（Phase 1は「現在のチャンネル／引き継がない」の2択。Phase 2で追加）。
- 音声、共有、クラウド同期。
- 自由チャット形式の長いインタビュー。
- ウィザード専用会話の下書きごと管理（Phase 1は単一のビルダー会話）。

---

## 3. 前提・確定事項

1. 質問は通算6問、2ラウンド、1ラウンド最大3問。
2. アプリ全体の「チャンネル作成用モデル」を初回に選ばせる。「賢いモデル推奨」の注意書きを出す。
3. 版権キャラのタグはAI生成も許可。プリセット優先。
4. 成人前提。NG優先。
5. `/api/session/new` は `RESET_STORY` を必ず適用するため、ウィザードは `/api/wizard/create` から `create_session(final_setting)` を直接呼ぶ。
6. `DELETE` は既存の `do_DELETE` → `do_POST` で利用可能。ただしウィザードは `POST /api/wizard/delete` を使う。
7. `send_json(obj, code=200)` のシグネチャは既存どおり。
8. `chars` の本体スキーマは `{"id","name","tags"(カンマ区切り文字列),"locked","source"}`。ウィザード内部では配列で扱い、最終段で正規化する。
9. `ide_poke.models()` は `(dict, None)` または `(None, 理由)` のタプルを返す。
10. Vertexのモデル一覧は `google_ai.list_models(provider="vertex", probe=False)` を使う。
11. `server.py` は `from urllib.parse import urlparse, parse_qs, quote` を既に読み込んでいる。`parse_qs` を直接使う。
12. `/api/app-settings` の保存は既存の `for k in DEFAULT_APP` ループが行う。`DEFAULT_APP` にキーを追加し、`cur` の検証だけを足す。
13. `public/app.js` の `switchTo` はトップレベル関数。`wizard.js` の IIFE 内で同名関数を作らない。
14. `#wizardRoot` には `.modal` クラスを付けない。付けると `closeTopmostLayer()` が `RPWizard.close()` を通らずに隠してしまう。
15. `user_name`（AIの呼び名）と `project` は元チャンネルから引き継ぐ。AIの質問effects・composeはこれらを変更しない。変更はユーザーの明示入力（「あなたの名前」/プレビュー）だけ。
16. 「＋」直後に「引き継ぎ元」ステップを置く。現在のチャンネルを既定にし、「このチャンネルから引き継ぐ」「引き継がない（初期設定）」を選べる。引き継ぎ元は**保存済みの設定**を使い、未保存の変更は引き継がない（画面に注意書きを出す）。
17. 引き継ぎ元を変えられるのは、スロット・質問・composeがまだ無い最初の段階だけ。入力後は `base_locked` で拒否する。
18. Phase 1では別チャンネル一覧を出さない。別のチャンネルから引き継ぎたい場合は、そのチャンネルを開いてから「＋」を押す。Phase 2で選択UIを追加する。
19. 「校正プリセット」は既存の `char_presets()`（`data/char-presets.json`）を使う。キャラ名または選択IDが一致したら、プリセットのタグをAIタグより優先し、`locked=true` / `source="preset"` にする。年齢はプリセットに無いため、年齢ルール（18歳未満は「なし」）で別途扱う。
20. 開幕画像は案A（非同期）。`create_session()` の後、最初のassistantメッセージに `image.status="queued"` を付けて既存の `queued_images` / `image_event` / `image_worker` に流す。ウィザードは画像の完成を待たない。`image_mode="off"` なら積まない。

### 3.1 校正プリセット（既存の `char_presets`）

「校正プリセット」は、新しく作る機能ではなく、画像機能で既に使っている**キャラプリセット**（`data/char-presets.json` / `char_presets()`）をそのまま使う。

- 保存内容: `{id, name, tags}`。`tags` は「そのキャラの固定画像タグ」（例: `1girl, black hair, long hair, red eyes`）。
- 用途: AIが生成したキャラの見た目タグを、プリセットのタグで**上書きして揃える**。同じキャラを別チャンネルで作っても見た目がブレない。
- 優先順位: プリセットがある → プリセットのタグ（`locked=true`, `source="preset"`）。無い → AIのタグ。
- AIタグと違い、プリセットのタグは服装・品質タグも削らない（ユーザーが保存した見た目をそのまま使う）。
- 年齢: プリセットは年齢を持たない。年齢は2.2のルール（18歳未満は「なし」）で別に処理する。
- 既存UI: 相手画面の「キャラプリセット（任意）」で選択できる。名前が一致する場合は自動で使う。

例: AIが「美咲: 1girl, blonde hair」と返しても、プリセット「篠原 美咲: 1girl, black hair, long hair, red eyes」があれば、後者を採用して固定する。

---

## 4. 用語

| 用語 | 意味 |
|---|---|
| 下書き（draft） | ウィザードの途中状態。`data/wizards/wiz_*.json` |
| 引き継ぎ元（base） | 新チャンネルに設定をコピーする元のチャンネル。`base_sid` / `base_setting` / `inherit_base` |
| スロット | キャラ・世界観・関係性などの設定項目。`source` を持つ |
| source | `locked > user > answer > auto > derived > unset` の優先度 |
| derived | タグから導出された値 |
| auto | ユーザーが「おまかせ」を選んだ状態 |
| compose | スロットとタグから最終設定を生成する処理 |
| ジョブ | AI質問・composeを非同期実行する単位。`wj_*` |
| プリセット | `char_presets()` が返す固定キャラ。`locked` になる |
| 校正プリセット | 既存の `char_presets()`。キャラ名＋固定画像タグ。AIタグより優先して見た目を揃える |
| 開幕画像 | 新チャンネルの最初のassistantメッセージに付ける画像。作成後に非同期生成する |
| ビルダー会話 | Antigravityのウィザード専用会話。`data/wizard_ide/` |

---

## 5. 全体アーキテクチャ

```
[ブラウザ]
  index.html
    ├─ app.js（既存。newSession を RPWizard に委譲）
    └─ wizard.js（IIFE。window.RPWizard）
         │  fetch /api/wizard/*
         ▼
[server.py]
  wizard_route()
    ├─ 下書きCRUD（data/wizards/*.json）
    ├─ スロット・タグ・NG
    ├─ 質問生成ジョブ ──> google_ai.generate / ide_poke
    ├─ composeジョブ ──> google_ai.generate / ide_poke
    │                     └─ 失敗時は wizard_template_compose()
    └─ create ──> create_session(final_setting)
                    ├─ 第一声を messages に保存
                    ├─ 開幕画像を image.status="queued" で最初のassistantに付与
                    └─ queued_images / image_event ──> image_worker ──> ComfyUI

[Antigravity]
  data/wizard_ide/ の専用会話（単一）
```

### 5.1 採用方式

- **最初に引き継ぎ元を確認**。現在のチャンネルを既定にし、「引き継ぐ／引き継がない」を選ばせる。ストーリー・進捗・IDE会話は常にリセットする。
- **スロット埋め＋5択補完＋一括compose**。自由チャットではない。
- **source 6状態**で、おまかせの再質問とタグ由来の衝突を防ぐ。
- **ジョブ＋ポーリング**。Antigravityに同期一発生成APIが無いため。
- **テンプレートcompose fallback**。AI失敗でも必ず作成。
- **冪等作成**。`created_sid` と `rev` で二重作成を防ぐ。
- **楽観ロック**。`rev` 不一致は409で返し、クライアントが再読み込みする。
- **開幕画像は非同期**。`create_session` 後に最初のAI発言へ `image.status="queued"` を付け、既存の `queued_images` / `image_event` / `image_worker` に流す。`image_mode="off"` なら積まない。画像の失敗・タイムアウトでチャンネル作成は失敗させない。

### 5.2 データ配置

```
data/
  wizards/
    wiz_<10hex>.json      下書き
  wizard_ide/             Antigravity専用会話の作業フォルダ
  sessions/
    <sid>.json            既存チャンネル
```

---

## 6. ユーザーフロー

```
＋
 ↓
[引き継ぎ元]
  チャンネル「○○」の設定を引き継ぎますか？
  引き継ぐ: 画像設定 / 参照ファイル / プロジェクト / ユーザー名 / 本文AI設定
  リセット: ストーリー / 進捗（メモ・ターン・日数） / IDE会話
  「このチャンネルから引き継ぐ」「引き継がない（初期設定）」
  ※ 保存済みの設定のみ。未保存の変更は引き継がない
  ※ チャンネルが0件のときは「引き継ぎ元なし」で初期設定のみ
 ↓
[モデル選択]（初回のみ）
  provider / model / thinking / 接続テスト
  「AIを使わずに作る」も選べる
 ↓
[アイデア]
  1行入力 or 例から選択 or 空のまま
  「確認しない」チェック
  「AIと作る」「おまかせで作る」「空のチャンネル」
 ↓
[相手とあなた]
  相手: オリジナル / 版権 / おまかせ
  作品・世界観、名前、プリセット
  あなた: 種類、作品、名前、設定
  開始地点、原作準拠度、目的・ゴール、結末
 ↓
[関係性・世界観・雰囲気・特殊要素・NG]
  タグを選択（軸ごとに上限）
  NGプリセット＋自由入力
  「次へ」「ここまでで作る」
 ↓
[質問]（最大6問・2ラウンド）
  1問ずつ、5択＋自由入力＋おまかせ
  ラウンド1終了時に「追加で聞く / このまま作る」
 ↓
[プレビュー]
  タイトル、あらすじ、キャラ、口調、開始状況、第一声、
  あなたの名前、プロジェクト、本文AIへの指示
  「この内容で作成」「作り直す」
 ↓
[作成中]
  ジョブ進捗。キャンセル可。閉じても継続。
 ↓
[完成]
  新チャンネルへ切り替え。第一声が入っている。
  開幕画像は裏で生成中（画像OFFなら生成しない）。
  画像の完成は待たない。失敗してもチャンネルは使える。
```

---

## 7. データモデル

### 7.1 下書き（draft）

```json
{
  "schema_version": 1,
  "id": "wiz_0123456789",
  "rev": 3,
  "created": "2026-01-01T12:00:00",
  "updated": "2026-01-01T12:05:00",
  "status": "interview",
  "step": "tags",
  "base_sid": "s_xxx",
  "base_title": "学園もの",
  "inherit_base": true,
  "base_setting": { "...": "既存チャンネルのsetting。AIには渡さない" },
  "model": {
    "provider": "google",
    "model": "gemini-2.5-pro",
    "thinking_level": "high",
    "ai_enabled": true
  },
  "slots": {
    "rough_idea": { "value": "放課後の教室で幼馴染と", "source": "user" },
    "partner_type": { "value": "original", "source": "user" },
    "relationship": { "value": ["幼馴染"], "source": "derived" },
    "world": { "value": ["現代の学園"], "source": "derived" },
    "tone": { "value": ["日常"], "source": "derived" }
  },
  "selected_tags": ["rel_childhood", "world_school", "tone_gentle"],
  "selected_ng": ["ng_minor", "ng_ntr"],
  "custom_ng": ["流血"],
  "partner_preset_id": "",
  "partner_preset_off": false,
  "questions": [],
  "asked_slots": ["relationship"],
  "asked_question_ids": ["q_relationship"],
  "round": 1,
  "done_questions": false,
  "compose": null,
  "compose_stale": true,
  "last_job": "wj_0123456789",
  "created_sid": "",
  "warnings": [],
  "ai_calls": 2,
  "confirm": true
}
```

`step` は `base` / `idea` / `partner` / `tags` / `questions` / `preview` / `creating`。
`base` は最初の画面で、`base_setting` を引き継ぐかどうかを決める。`inherit_base=false` のときも `base_sid` は再開判定用に残し、`base_setting` は空にする。

### 7.2 スロット定義

| key | ラベル | 型 | 値 |
|---|---|---|---|
| rough_idea | 作りたい話 | text 300 | 自由入力 |
| partner_type | 相手の種類 | enum | original / copyright |
| work | 作品・世界観 | text 60 | 自由入力 |
| partner_name | 相手の名前 | text 40 | 自由入力 |
| player_type | あなた | enum | original / copyright / existing |
| player_work | あなたの作品 | text 60 | 自由入力 |
| player_name | あなたの名前 | text 40 | 自由入力 |
| player_profile | あなたの設定 | text 300 | 自由入力 |
| start_point | 開始地点 | text 80 | 自由入力 |
| canon_fidelity | 原作準拠度 | enum | canon / mixed / au |
| relationship | 関係性 | multi 3 | タグ由来のラベル |
| world | 世界観 | multi 2 | タグ由来のラベル |
| tone | 雰囲気 | multi 2 | タグ由来のラベル |
| kinks | 特殊要素 | multi 4 | タグ由来のラベル |
| intensity | 性的濃度 | enum | none / soft / medium / hard |
| player_role | あなたの立場 | multi 2 | タグ由来のラベル |
| ending | 結末 | enum | happy / bittersweet / dark / open |
| goal | 目的・ゴール | text 100 | 自由入力 |
| pov | 視点 | enum | first / second / third |
| style | 文体 | enum | novel / light / script / chat |
| length | 長さ | enum | short / medium / long |
| project | プロジェクト | text 40 | 自由入力 |

### 7.3 source の優先度

```
locked   6  プリセット・固定。AIもユーザーも変更不可
user     5  ユーザーが明示入力
answer   4  AI質問への回答
auto     3  「おまかせ」（タグより優先）
derived  2  タグから導出
unset    0  未設定
```

`wslot_set` は、既存の source より低い優先度の書き込みを無視する。`wslot_set_auto` だけは明示的な「おまかせ」なので、既存値をクリアする。

### 7.4 タグカタログ

`defaults/wizard_tags.json` は以下を持つ。

- `axes`: `relation` / `world` / `tone` / `kink` / `role` の上限。
- `tags`: `{id,label,axis,description,effects,image_tags?}`。`effects` は `{slot: value}` の dict。`image_tags` は開幕画像の場面タグ（英語、任意）。
- `ng_presets`: `{id,label,locked?}`。`ng_minor` は常に有効。
- `examples`: アイデア例。

#### 7.4.1 `wizard_tags.json` の完全な初期値

`axes` は **dict**（`{軸ID: {label,max}}`）。`tags[].description` を使い、古い `desc` も読み替える。`conflicts_ng` は NG の ID または表示名（正規化して比較）。

```json
{
  "version": 1,
  "axes": {
    "relation": { "label": "関係性", "max": 3 },
    "world": { "label": "世界観", "max": 2 },
    "tone": { "label": "雰囲気", "max": 2 },
    "kink": { "label": "特殊要素", "max": 4 },
    "role": { "label": "あなたの立場", "max": 2 }
  },
  "tags": [
    { "id": "rel_pure", "label": "純愛", "axis": "relation", "description": "相手を大切にする、まっすぐな恋愛。", "effects": { "relationship": ["純愛"], "tone": ["穏やか"] }, "image_tags": ["romantic", "gentle lighting"] },
    { "id": "rel_childhood", "label": "幼馴染", "axis": "relation", "description": "子どもの頃から一緒の、遠慮のない関係。", "effects": { "relationship": ["幼馴染"] }, "image_tags": ["childhood friend", "warm light"] },
    { "id": "rel_lover", "label": "恋人", "axis": "relation", "description": "すでに付き合っている二人。", "effects": { "relationship": ["恋人"] }, "image_tags": ["couple", "close distance"] },
    { "id": "rel_rival", "label": "ライバル", "axis": "relation", "description": "負けたくない相手。反発と惹かれ合い。", "effects": { "relationship": ["ライバル"] }, "image_tags": ["rival", "tension"] },
    { "id": "rel_family", "label": "家族", "axis": "relation", "description": "家族として暮らす二人。", "effects": { "relationship": ["家族"] }, "image_tags": ["family", "home"] },

    { "id": "world_school", "label": "学園", "axis": "world", "description": "学校を舞台にした日常。", "effects": { "world": ["学園"] }, "image_tags": ["classroom", "after school"] },
    { "id": "world_office", "label": "オフィス", "axis": "world", "description": "仕事場での関係。", "effects": { "world": ["オフィス"] }, "image_tags": ["office", "city night"] },
    { "id": "world_fantasy", "label": "ファンタジー", "axis": "world", "description": "魔法や剣のある世界。", "effects": { "world": ["ファンタジー"] }, "image_tags": ["fantasy castle", "magic"] },

    { "id": "tone_gentle", "label": "穏やか", "axis": "tone", "description": "静かでやさしい空気。", "effects": { "tone": ["穏やか"] }, "image_tags": ["soft lighting"] },
    { "id": "tone_dark", "label": "ダーク", "axis": "tone", "description": "重く、影のある雰囲気。", "effects": { "tone": ["ダーク"] }, "image_tags": ["dark lighting", "shadow"] },
    { "id": "tone_comedy", "label": "コメディ", "axis": "tone", "description": "軽快で笑いのある会話。", "effects": { "tone": ["コメディ"] }, "image_tags": ["bright lighting"] },

    { "id": "kink_ntr", "label": "NTR", "axis": "kink", "description": "第三者が絡む背徳的な関係。", "effects": { "kinks": ["NTR"], "tone": ["背徳"] }, "conflicts_ng": ["ng_ntr"] },
    { "id": "kink_gore", "label": "グロ", "axis": "kink", "description": "流血や残酷な描写を含む。", "effects": { "kinks": ["グロ"], "tone": ["凄惨"] }, "conflicts_ng": ["ng_gore"] },
    { "id": "kink_bondage", "label": "拘束", "axis": "kink", "description": "拘束や主従の要素。", "effects": { "kinks": ["拘束"], "intensity": ["hard"] }, "image_tags": ["restrained"] },
    { "id": "kink_public", "label": "公開", "axis": "kink", "description": "人目につく場所での緊張感。", "effects": { "kinks": ["公開"], "intensity": ["medium"] }, "image_tags": ["public place"] },

    { "id": "role_hero", "label": "主人公", "axis": "role", "description": "物語の中心になる立場。", "effects": { "player_role": ["主人公"] } },
    { "id": "role_teacher", "label": "教師", "axis": "role", "description": "教える側の立場。", "effects": { "player_role": ["教師"] } },
    { "id": "role_senior", "label": "先輩", "axis": "role", "description": "一歩先を行く立場。", "effects": { "player_role": ["先輩"] } }
  ],
  "ng_presets": [
    { "id": "ng_minor", "label": "未成年", "locked": true },
    { "id": "ng_ntr", "label": "NTR" },
    { "id": "ng_gore", "label": "グロ・流血" },
    { "id": "ng_scat", "label": "排泄" },
    { "id": "ng_ryona", "label": "リョナ" }
  ],
  "examples": [
    "幼馴染と、放課後の教室で秘密の関係を始める",
    "事故で同居することになった年上の上司との、ぎこちない日常",
    "魔法学院のライバル同士が、共同任務で距離を縮める"
  ]
}
```

### 7.5 ジョブ

```json
{
  "job_id": "wj_0123456789",
  "draft_id": "wiz_0123456789",
  "kind": "questions",
  "status": "queued",
  "progress": "質問を考えています",
  "result": null,
  "error": null,
  "error_code": "",
  "retryable": false,
  "cancel": false,
  "created": "2026-01-01T12:00:00",
  "ts_epoch": 1760000000.0,
  "base_rev": 3
}
```

`status`: `queued` / `running` / `done` / `error`。

### 7.6 chars（本体スキーマ）

```json
{
  "id": "0123456789",
  "name": "篠原 美咲",
  "tags": "1girl, black hair, long hair, school uniform",
  "locked": false,
  "source": "ai"
}
```

ウィザード内部では `tags` を配列で扱うが、`wizard_build_final_setting` の最終段で `wizard_normalize_chars()` を通し、必ず文字列に変換する。

### 7.7 開幕画像

チャンネル作成後、最初のassistantメッセージ（第一声）に画像を付ける。画像は既存の画像生成キューに積み、ウィザードは完成を待たない。

```json
{
  "prompt": "classroom, morning, looking at viewer",
  "negative": "lowres, bad anatomy",
  "raw": false,
  "seed": 123456789,
  "status": "queued",
  "chars": ["篠原 美咲"],
  "opening": true
}
```

- `prompt` は**場面タグだけ**。人物の髪色・服装などの外見は `chars` の固定タグから付く。
- `chars` は最終設定の `chars[].name`。プリセットのタグもここから反映される。`chars` が空でも `character.name` を1件入れる。
- `status` は既存の画像と同じ `queued` / `generating` / `done` / `error`。
- `opening: true` で、通常の画像との区別と二重生成防止に使う。
- `image_mode="off"` のときは画像を付けない。
- `compose.setting.opening_image` は場面タグの配列。最終設定には保存せず、作成時のキュー投入にだけ使う。

作成APIは `opening_image`（積んだか）と `opening_image_mid`（最初のassistantメッセージID）を返す。画像が失敗しても `sid` は返り、チャンネルは使える。

---

## 8. 状態遷移

```
                 ┌──────────────┐
                 │  interview   │
                 └──────┬───────┘
      質問/作成開始      │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
  ┌───────────┐   ┌───────────┐    ┌───────────┐
  │ composing │   │   ready   │    │  created  │
  └─────┬─────┘   └─────┬─────┘    └───────────┘
        │               │
  成功  │               │ 作り直す
        └───────────────┘
        │
  失敗/キャンセル
        ▼
  interview（composeが残っていれば ready）
```

- `composing`: composeジョブ実行中。画面を閉じても継続。
- `ready`: compose結果あり。プレビュー可能。
- `created`: チャンネル作成済み。以降は編集不可。
- `step`: `base` / `idea` / `partner` / `tags` / `questions` / `preview` / `creating`。リロード復元に使う。
- `base`: 最初の「引き継ぎ元」画面。`status` は `interview` のまま。`inherit_base=false` でも `base_sid` は再開判定用に残し、`base_setting` を空にする。
- `model`: 下書きの `step` ではなく、`base` の後ろに置くUI画面。作成AIが未設定のときだけ表示し、設定済みなら `idea` へ進む。
- スロット・質問・composeが入った後は `base` に戻れない（`base_locked`）。

---

## 9. API一覧

| Method | Path | 用途 | 主な入力 | 主な出力 |
|---|---|---|---|---|
| GET | `/api/wizard/meta` | 設定・タグ・上限 | - | `enabled,current,max_questions,tags` |
| GET | `/api/wizard/models?provider=` | モデル一覧 | provider | `models,configured,error` |
| POST | `/api/wizard/test` | 接続テスト | provider,model | `ok,response` |
| GET | `/api/wizard/list` | 下書き一覧 | - | `drafts` |
| GET | `/api/wizard/<id>` | 下書き取得 | - | `draft` |
| POST | `/api/wizard/start` | 再開/新規 | base_sid,force_new | `resumed,draft`（新規は `step=base`） |
| POST | `/api/wizard/base` | 引き継ぎ元の確定 | base_sid,inherit | `draft` |
| POST | `/api/wizard/answer` | 回答 | question_id,option_ids,custom_text,auto,step | `draft` |
| POST | `/api/wizard/slots` | 複数スロット保存 | slots | `draft` |
| POST | `/api/wizard/tags` | タグ/NG保存 | selected_tags,selected_ng,custom_ng | `draft` |
| POST | `/api/wizard/model` | 下書きのAI設定 | provider,model,thinking_level,ai_enabled | `draft` |
| POST | `/api/wizard/step` | 画面/確認フラグ | step,confirm | `draft` |
| POST | `/api/wizard/preview` | プレビュー編集保存 | setting | `draft` |
| POST | `/api/wizard/questions` | 質問ジョブ開始 | draft_id,rev | `job_id,draft` |
| POST | `/api/wizard/compose` | composeジョブ開始 | draft_id,rev | `job_id,draft` |
| GET | `/api/wizard/job/<job_id>` | ジョブ状態 | - | `status,result,error` |
| POST | `/api/wizard/job_cancel` | キャンセル | job_id | `ok` |
| POST | `/api/wizard/create` | チャンネル作成 | draft_id,rev,overrides,use_template | `sid,title,draft,opening_image,opening_image_mid` |
| POST | `/api/wizard/delete` | 下書き削除 | draft_id | `ok` |

### 9.1 エラーコード

| code | HTTP | 意味 |
|---|---|---|
| `bad_request` | 400 | 入力不正 |
| `not_found` | 404 | 下書き/ジョブなし |
| `rev` | 409 | rev不一致。`draft` を返す |
| `wizard_busy` | 409 | 同一下書きでジョブ実行中 |
| `stale` | 409 | 生成中に下書きが更新された |
| `wizard_not_composed` | 400 | compose未生成。`use_template` で回避可 |
| `ai_limit` | 429 | AI呼び出し上限 |
| `no_key` / `model_not_found` | 400 | AI設定不備 |
| `bad_base` | 400 | 引き継ぎ元がない/読み込めない |
| `base_locked` | 409 | 入力開始後に引き継ぎ元を変更しようとした |
| `timeout` / `quota` / `ai_error` | 502 | AI側エラー。再試行可 |
| `ai_disabled` | 400 | AIなし設定 |
| `created` | 409 | 作成済み |
| `canceled` | 400 | キャンセル |

※ 開幕画像のキュー投入に失敗しても作成自体は成功し、`opening_image=false` と警告を返す（エラーコードにはしない）。

## 10. バックエンド実装（`server.py`）

`create_session()` の直後、`find_msg()` の前に挿入する。

### 10.1 定数・スロット・状態

```python
# ============================================================
# AIチャンネル作成ウィザード
# ============================================================
WIZARD_DIR = os.path.join(DATA, "wizards")
WIZARD_IDE_DIR = os.path.join(DATA, "wizard_ide")
WIZARD_TAGS_PATH = defaults_path("wizard_tags.json")
WIZARD_ID_RE = re.compile(r"^wiz_[0-9a-f]{10}$")
WIZARD_JOB_RE = re.compile(r"^wj_[0-9a-f]{10}$")

WIZARD_MAX_QUESTIONS = 6
WIZARD_MAX_ROUNDS = 2
WIZARD_QUESTIONS_PER_ROUND = 3
WIZARD_AI_CALL_LIMIT = 8
WIZARD_DRAFT_LIMIT = 20
WIZARD_DRAFT_TTL = 7 * 24 * 60 * 60
WIZARD_CREATED_TTL = 24 * 60 * 60
WIZARD_JOB_STALE = 600
WIZARD_JOB_KEEP = 3600

REQUIRED_SLOTS = ["partner_type", "world", "tone", "relationship", "player_role"]
ASKABLE_PRIORITY = [
    "relationship", "start_point", "player_role",
    "world", "tone", "ending", "intensity", "goal",
]

WIZARD_SLOTS = {
    "rough_idea":      {"label": "作りたい話", "type": "text", "max": 300},
    "partner_type":    {"label": "相手の種類", "type": "enum",
                        "values": ["original", "copyright"]},
    "work":            {"label": "作品・世界観", "type": "text", "max": 60},
    "partner_name":    {"label": "相手の名前", "type": "text", "max": 40},
    "player_type":     {"label": "あなた", "type": "enum",
                        "values": ["original", "copyright", "existing"]},
    "player_work":     {"label": "あなたの作品", "type": "text", "max": 60},
    "player_name":     {"label": "あなたの名前", "type": "text", "max": 40},
    "player_profile":  {"label": "あなたの設定", "type": "text", "max": 300},
    "start_point":     {"label": "物語の開始地点", "type": "text", "max": 80},
    "canon_fidelity":  {"label": "原作準拠度", "type": "enum",
                        "values": ["canon", "mixed", "au"]},
    "relationship":    {"label": "関係性", "type": "multi", "max": 3},
    "world":           {"label": "世界観", "type": "multi", "max": 2},
    "tone":            {"label": "雰囲気", "type": "multi", "max": 2},
    "kinks":           {"label": "特殊要素", "type": "multi", "max": 4},
    "intensity":       {"label": "性的濃度", "type": "enum",
                        "values": ["none", "soft", "medium", "hard"]},
    "player_role":     {"label": "あなたの立場", "type": "multi", "max": 2},
    "ending":          {"label": "結末", "type": "enum",
                        "values": ["happy", "bittersweet", "dark", "open"]},
    "goal":            {"label": "目的・ゴール", "type": "text", "max": 100},
    "pov":             {"label": "視点", "type": "enum",
                        "values": ["first", "second", "third"]},
    "style":           {"label": "文体", "type": "enum",
                        "values": ["novel", "light", "script", "chat"]},
    "length":          {"label": "長さ", "type": "enum",
                        "values": ["short", "medium", "long"]},
    "project":         {"label": "プロジェクト", "type": "text", "max": 40},
}

SLOT_LABEL = {k: v["label"] for k, v in WIZARD_SLOTS.items()}

WIZARD_SLOT_DEFAULTS = {
    "player_type": "original",
    "player_name": "あなた",
    "intensity": "soft",
    "pov": "second",
    "style": "novel",
    "length": "medium",
    "ending": "open",
    "start_point": "出会った直後",
    "canon_fidelity": "mixed",
}

# 親スロットが変わったら、子スロットを消す
INVALIDATES = {
    "partner_type": ["work", "partner_name", "start_point", "canon_fidelity"],
    "work":         ["partner_name", "start_point", "canon_fidelity"],
    "player_type":  ["player_work", "player_name", "player_profile"],
    "player_work":  ["player_name", "player_profile"],
}

# AIのeffectsを適用する順序（親→子）
APPLY_ORDER = [
    "partner_type", "work", "player_type", "player_work",
    "player_name", "partner_name", "player_profile", "start_point",
    "canon_fidelity", "relationship", "world", "tone", "kinks",
    "intensity", "player_role", "ending", "goal", "pov", "style",
    "length", "rough_idea", "project",
]

SOURCE_ORDER = {
    # 「おまかせ」はユーザーの明示選択なので、タグ由来の derived より強い。
    "unset": 0, "derived": 2, "auto": 3, "answer": 4, "user": 5, "locked": 6,
}

COMPOSE_SETTING_KEYS = [
    "title", "summary", "instructions", "scenario", "first_message",
    "character", "chars", "elapsed_time", "user_name", "project",
]

# AIの質問effectsから変更させない（元チャンネルからの引き継ぎを守る）
PROTECTED_AI_SLOTS = {"player_name", "project"}

wizard_lock = threading.RLock()
wizard_create_lock = threading.Lock()  # 同じ下書きの二重作成を防ぐ
wizard_jobs = {}
wizard_tags_cache = {"ts": 0.0, "data": None, "index": {}}
wizard_model_cache = {}


class WizardError(Exception):
    def __init__(self, message, code="wizard_error", retryable=False, status=400):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status
```

### 10.2 保存・読み込み・TTL

```python
def _wiz_uid(prefix):
    return prefix + uuid.uuid4().hex[:10]


def wizard_path(wid):
    if not WIZARD_ID_RE.match(str(wid or "")):
        raise ValueError("bad wizard id")
    return os.path.join(WIZARD_DIR, wid + ".json")


def wizard_load(wid):
    try:
        d = read_json(wizard_path(wid), None)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if _as_int(d.get("schema_version"), 0) != 1:
        return None
    if not isinstance(d.get("slots"), dict):
        d["slots"] = {}
    if not isinstance(d.get("selected_tags"), list):
        d["selected_tags"] = []
    if not isinstance(d.get("questions"), list):
        d["questions"] = []
    return d


def wizard_save(d, bump=True):
    """bump=False: last_job / ai_calls / status など、クライアントの rev を動かしたくない内部更新用"""
    with wizard_lock:
        if bump:
            d["rev"] = _as_int(d.get("rev"), 0) + 1
        d["updated"] = now()
        os.makedirs(WIZARD_DIR, exist_ok=True)
        write_json(wizard_path(d["id"]), d)


def wizard_ids():
    if not os.path.isdir(WIZARD_DIR):
        return []
    out = []
    for f in os.listdir(WIZARD_DIR):
        if f.endswith(".json") and WIZARD_ID_RE.match(f[:-5]):
            out.append(f[:-5])
    return out


def wizard_ids_sorted():
    out = []
    for wid in wizard_ids():
        d = wizard_load(wid)
        if d:
            out.append((wid, d))
    out.sort(key=lambda x: x[1].get("updated") or "", reverse=True)
    return out


def wizard_sweep():
    with wizard_lock:
        now_t = time.time()
        # 終了したジョブをいつまでもメモリに残さない
        for jid, j in list(wizard_jobs.items()):
            age = now_t - _as_int((j or {}).get("ts_epoch"), 0)
            if (j or {}).get("status") in ("done", "error") and age > WIZARD_JOB_KEEP:
                wizard_jobs.pop(jid, None)
    if not os.path.isdir(WIZARD_DIR):
        return 0
    drafts = []
    for wid in wizard_ids():
        d = wizard_load(wid)
        if d:
            drafts.append((wid, d))
    drafts.sort(key=lambda x: x[1].get("updated") or "")
    removed = 0
    for wid, d in drafts:
        age = time.time() - (_epoch(d.get("updated")) or 0)
        if d.get("status") == "created":
            should = age > WIZARD_CREATED_TTL
        else:
            should = age > WIZARD_DRAFT_TTL or len(drafts) - removed > WIZARD_DRAFT_LIMIT
        if should:
            try:
                os.remove(wizard_path(wid))
                removed += 1
            except OSError:
                pass
    return removed
```

### 10.3 タグ・NG

```python
def wizard_tags():
    with wizard_lock:
        t = time.time()
        if wizard_tags_cache["data"] is not None and t - wizard_tags_cache["ts"] < 300:
            return wizard_tags_cache["data"]
        raw = read_json(WIZARD_TAGS_PATH, {})
        if not isinstance(raw, dict):
            raw = {}
        tags = []
        for x in (raw.get("tags") or []):
            if not isinstance(x, dict) or not x.get("id"):
                continue
            tag = dict(x)
            # 仕様書の desc / 実装の description の両方を受ける
            tag["description"] = str(tag.get("description") or tag.get("desc") or "")
            c = tag.get("conflicts_ng") or []
            tag["conflicts_ng"] = (split_tags(c) if isinstance(c, str) else list(c))[:20]
            tags.append(tag)
        ng = [x for x in (raw.get("ng_presets") or []) if isinstance(x, dict) and x.get("id")]
        axes_raw = raw.get("axes")
        if isinstance(axes_raw, dict):
            axes = axes_raw
        elif isinstance(axes_raw, list):
            # 仕様書の [{id,label,max}] 形式も受ける
            axes = {str(a.get("id")): {"label": a.get("label") or a.get("id"),
                                       "max": a.get("max")}
                    for a in axes_raw if isinstance(a, dict) and a.get("id")}
        else:
            axes = {}
        data = {
            "version": _as_int(raw.get("version"), 1),
            "axes": axes,
            "tags": tags,
            "ng_presets": ng,
            "examples": [str(x) for x in (raw.get("examples") or []) if str(x).strip()],
        }
        index = {x["id"]: x for x in tags}
        wizard_tags_cache.update({"ts": t, "data": data, "index": index})
        return data


def wizard_tag_by_id(tid):
    wizard_tags()
    return wizard_tags_cache["index"].get(str(tid or ""))


def wizard_ng_labels(d):
    labels = []
    presets = {x["id"]: x for x in wizard_tags().get("ng_presets", [])}
    for nid in (d.get("selected_ng") or []):
        if nid in presets:
            labels.append(presets[nid].get("label") or nid)
    labels.extend(str(x) for x in (d.get("custom_ng") or []) if str(x).strip())
    return list(dict.fromkeys(labels))


def wizard_norm_label(x):
    return re.sub(r"[\s_\-・/]+", "", str(x or "").casefold())


def wizard_ng_conflicts(d):
    """NG の ID と表示名の両方。表記ゆれは正規化して比較する。"""
    out = set()
    presets = {x.get("id"): x for x in wizard_tags().get("ng_presets", [])}
    for nid in (d.get("selected_ng") or []):
        out.add(wizard_norm_label(nid))
        if nid in presets:
            out.add(wizard_norm_label(presets[nid].get("label")))
    for x in (d.get("custom_ng") or []):
        out.add(wizard_norm_label(x))
    out.discard("")
    return out


def wizard_tag_hits_ng(tag, ng_set):
    if not isinstance(tag, dict):
        return False
    cand = [tag.get("label")]
    for key in ("conflicts_ng", "conflicts"):
        v = tag.get(key) or []
        if isinstance(v, str):
            v = split_tags(v)
        cand.extend(v)
    return any(wizard_norm_label(x) in ng_set for x in cand if str(x or "").strip())


def wizard_clean_tags(tags):
    try:
        raw = split_tags(tags) if isinstance(tags, str) else (tags or [])
    except Exception:
        raw = []
    drop = (
        "masterpiece", "best quality", "high quality", "highres", "absurdres",
        "official art", "1girl", "1boy", "solo", "looking at viewer",
        "full body", "upper body", "close-up", "from behind", "from side",
        "school uniform", "serafuku", "uniform", "dress", "outfit",
        "standing", "sitting", "lying", "walking", "running",
    )
    drop_set = set(drop)
    out = []
    for x in raw:
        s = str(x or "").strip()
        low = s.lower()
        # 部分一致だと headdress / flying / han solo を誤爆するため完全一致で落とす
        if not s or low in drop_set:
            continue
        if s not in out:
            out.append(s)
    return out[:40]


def wizard_tag_lines(d):
    out = []
    for tid in (d.get("selected_tags") or []):
        tag = wizard_tag_by_id(tid)
        if tag:
            out.append(f"- {tag.get('label')}: {tag.get('description') or ''}")
    return "\n".join(out) or "- なし"
```

### 10.4 スロット操作

```python
def wslot(d, key):
    return (d.get("slots") or {}).get(key) or {}


def wslot_source(d, key):
    return wslot(d, key).get("source") or "unset"


def wslot_value(d, key):
    return wslot(d, key).get("value")


def wizard_effective_value(d, key):
    v = wslot_value(d, key)
    if v not in (None, "", []):
        return v
    if key == "player_name":
        base = d.get("base_setting") or {}
        return str(base.get("user_name") or "").strip() or WIZARD_SLOT_DEFAULTS["player_name"]
    if key == "project":
        base = d.get("base_setting") or {}
        return str(base.get("project") or "").strip()
    return copy.deepcopy(WIZARD_SLOT_DEFAULTS.get(key))


def wiz_list(v):
    if isinstance(v, list):
        return v
    if v in (None, ""):
        return []
    return [v]


def wizard_plain_text(v, limit=2000):
    s = str(v or "").replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch in "\n\t" or ord(ch) >= 32)
    s = re.sub(r"[|<>]", "", s)
    s = re.sub(r"\n{4,}", "\n\n\n", s)
    return s.strip()[:limit]


def wizard_norm_value(spec, v):
    typ = spec.get("type")
    if typ == "text":
        return wizard_plain_text(v, _as_int(spec.get("max"), 120))
    if typ == "enum":
        s = str(v or "").strip()
        return s if s in (spec.get("values") or []) else ""
    if typ == "multi":
        vals = []
        for x in wiz_list(v):
            s = wizard_plain_text(x, 40)
            if s and s not in vals:
                vals.append(s)
        return vals[: _as_int(spec.get("max"), 4)]
    return ""


def wslot_set(d, key, value, source):
    if key not in WIZARD_SLOTS:
        return False
    if source != "locked" and wslot_source(d, key) == "locked":
        return False
    if SOURCE_ORDER.get(source, 0) < SOURCE_ORDER.get(wslot_source(d, key), 0):
        return False
    spec = WIZARD_SLOTS[key]
    val = wizard_norm_value(spec, value)
    if val in (None, "", []) and source != "auto":
        return False
    prev = wslot_value(d, key)
    d.setdefault("slots", {})[key] = {"value": val, "source": source}
    if prev != val:
        for dep in INVALIDATES.get(key, []):
            if wslot_source(d, dep) != "locked":
                d["slots"].pop(dep, None)
        d["compose_stale"] = True
    return True


def wslot_set_auto(d, key):
    if key not in WIZARD_SLOTS:
        return
    d.setdefault("slots", {})[key] = {"value": "", "source": "auto"}
    for dep in INVALIDATES.get(key, []):
        if wslot_source(d, dep) != "locked":
            d["slots"].pop(dep, None)
    d["compose_stale"] = True


def wizard_filled(d, key):
    s = wslot(d, key)
    src = s.get("source") or "unset"
    if src == "auto":
        return True
    return src in ("locked", "user", "answer", "derived") and s.get("value") not in (None, "", [])


def wizard_missing_required(d):
    return [k for k in REQUIRED_SLOTS if not wizard_filled(d, k)]


def wizard_ready(d):
    return not wizard_missing_required(d)


def wizard_askable_slots(d):
    return [k for k in ASKABLE_PRIORITY
            if k in WIZARD_SLOTS and wslot_source(d, k) == "unset"]


def wizard_recompute_derived(d):
    """derived だけ再計算する。user/answer/locked/auto は保持する。"""
    overflow = []
    for k, s in list((d.get("slots") or {}).items()):
        if (s or {}).get("source") == "derived":
            d["slots"].pop(k, None)
    for tid in (d.get("selected_tags") or []):
        tag = wizard_tag_by_id(tid)
        if not tag:
            continue
        for key, val in (tag.get("effects") or {}).items():
            if key not in WIZARD_SLOTS:
                continue
            # auto はユーザーの「おまかせ」なので derived で上書きしない。
            # 既に derived の項目は複数タグの効果をマージする。
            if wslot_source(d, key) not in ("unset", "derived"):
                continue
            spec = WIZARD_SLOTS[key]
            if spec.get("type") == "multi":
                cur = wslot_value(d, key)
                merged = list(dict.fromkeys(wiz_list(cur) + wiz_list(val)))
                mx = _as_int(spec.get("max"), 4)
                if len(merged) > mx:
                    overflow.append(key)
                merged = merged[:mx]
                if merged:
                    wslot_set(d, key, merged, "derived")
            else:
                nv = wizard_norm_value(spec, val)
                if nv:
                    wslot_set(d, key, nv, "derived")
    d["compose_stale"] = True
    return list(dict.fromkeys(overflow))


def wizard_slots_lines(d, only_decided=False):
    lines = []
    for key in APPLY_ORDER:
        if key not in WIZARD_SLOTS:
            continue
        src = wslot_source(d, key)
        if only_decided and src == "unset":
            continue
        val = wslot_value(d, key)
        if isinstance(val, list):
            shown = "、".join(str(x) for x in val)
        else:
            shown = str(val or "")
        if src == "auto":
            shown = "（おまかせ）"
        elif not shown:
            shown = "（未設定）"
        lines.append(f"- {SLOT_LABEL[key]}: {shown} [{src}]")
    return "\n".join(lines)
```

### 10.5 質問生成・サニタイズ

```python
def wizard_question_prompt(d):
    ng = "、".join(wizard_ng_labels(d)) or "なし"
    return f"""あなたは日本語のインタラクティブ小説の編集者です。
以下の「決定済み」を変えない範囲で、物語を良くする質問を最大{WIZARD_QUESTIONS_PER_ROUND}問作ってください。
質問は必ず未設定のスロットだけにしてください。すでに決まっていることを聞き直さないでください。

# 決定済み
{wizard_slots_lines(d, only_decided=True)}

# 選択済みタグ
{wizard_tag_lines(d)}

# NG（絶対に避ける）
{ng}

# 質問にできるスロット（優先順）
{', '.join(wizard_askable_slots(d)) or 'なし'}

# 出力（JSONのみ）
{{
  "questions": [
    {{
      "slot": "relationship",
      "text": "二人の関係は？",
      "why": "物語の距離感が決まるため",
      "options": [
        {{
          "label": "幼馴染",
          "description": "長年の距離感が近い",
          "effects": {{"relationship": ["幼馴染"], "tone": ["日常"]}},
          "invalidates": []
        }}
      ]
    }}
  ]
}}

# ルール
- 各質問のoptionsは4〜6個。
- effectsのキーはスロット名のみ。relationship/world/tone/kinks/player_roleは配列。
- effectsには必ず質問のslot自身を含める。
- NGに触れる選択肢は作らない。
- 説明は日本語。JSON以外を出力しない。"""


def wizard_sanitize_options(d, slot, options):
    out = []
    ng_labels = wizard_ng_conflicts(d)
    spec = WIZARD_SLOTS[slot]
    for i, o in enumerate(options or []):
        if not isinstance(o, dict):
            continue
        label = wizard_plain_text(o.get("label"), 20)
        if not label:
            continue
        if any(x and x in label.lower() for x in ng_labels):
            continue
        effects = {}
        for k, v in (o.get("effects") or {}).items():
            if k in PROTECTED_AI_SLOTS or k == "ng" or k not in WIZARD_SLOTS:
                continue
            nv = wizard_norm_value(WIZARD_SLOTS[k], v)
            if nv not in (None, "", []):
                effects[k] = nv
        # 質問スロット自身を必ず含める
        if slot not in effects:
            effects[slot] = label if spec.get("type") != "multi" else [label]
        inv = [x for x in (o.get("invalidates") or []) if x in WIZARD_SLOTS]
        out.append({
            "id": f"o{i + 1}",
            "label": label,
            "description": wizard_plain_text(o.get("description"), 80),
            "effects": effects,
            "invalidates": inv,
        })
        if len(out) >= 6:
            break
    return out


def wizard_sanitize_questions(d, raw_questions):
    askable = set(wizard_askable_slots(d))
    out, seen_slots, seen_ids = [], set(), set()
    for q in (raw_questions or []):
        if not isinstance(q, dict):
            continue
        slot = str(q.get("slot") or "")
        if slot not in askable or slot in seen_slots:
            continue
        if wslot_source(d, slot) != "unset":
            continue
        text = wizard_plain_text(q.get("text"), 120)
        if not text:
            continue
        opts = wizard_sanitize_options(d, slot, q.get("options"))
        if len(opts) < 3:
            continue
        qid = f"q_{slot}"
        n = 2
        while qid in seen_ids or qid in (d.get("asked_question_ids") or []):
            qid = f"q_{slot}_{n}"
            n += 1
        seen_ids.add(qid)
        seen_slots.add(slot)
        out.append({
            "id": qid,
            "slot": slot,
            "text": text,
            "why": wizard_plain_text(q.get("why"), 60),
            "options": opts,
        })
        if len(out) >= WIZARD_QUESTIONS_PER_ROUND:
            break
    return out
```

### 10.6 compose・設定生成

```python
def wizard_compose_prompt(d):
    ng = "、".join(wizard_ng_labels(d)) or "なし"
    skeleton = wizard_template_compose(d)[0]
    return f"""あなたは日本語のインタラクティブ小説の設定ライターです。
以下の材料から、新しいチャンネルの設定をJSONで作ってください。

# 材料
{wizard_slots_lines(d, only_decided=False)}

# 選択済みタグ
{wizard_tag_lines(d)}

# NG（絶対に避ける）
{ng}

# 参考の骨子（これをそのまま使わず、文章を豊かにする）
{json.dumps(skeleton, ensure_ascii=False, indent=2)}

# 出力（JSONのみ）
{{
  "title": "40文字以内",
  "summary": "300文字以内",
  "instructions": "400〜1200文字。本文AIへの指示。",
  "scenario": "800文字以内。物語の開始状況。",
  "first_message": "相手の第一声。500文字以内。",
  "character": {{
    "name": "相手の名前",
    "age": "24",
    "profile": "外見・性格・背景",
    "speech": "口調のサンプル"
  }},
  "chars": [
    {{"name": "相手の名前", "tags": ["1girl", "long hair"]}}
  ],
  "opening_image": ["classroom", "morning", "looking at viewer"],
  "elapsed_time": {{"day": 1, "period": "午前"}},
  "warnings": [],
  "title_alternatives": ["案1", "案2", "案3"]
}}

# ルール
- 全員18歳以上の成人として扱う。18歳未満の年齢は「なし」にする。
- 版権キャラは原作の設定を尊重する。未成年キャラは年齢を「なし」にする。
- NGと特殊要素が衝突したらNGを優先する。
- charsのtagsは画像生成用。quality/服装/ポーズ/構図タグは入れない。
- opening_imageは開幕画像の場面タグだけ（英語、最大12個）。人物の髪色・服装などの外見は入れない。
- JSON以外を出力しない。"""


OPENING_SCENE_DROP = (
    "masterpiece", "best quality", "high quality", "highres", "absurdres",
    "official art", "school uniform", "serafuku", "uniform", "dress", "outfit",
)


def wizard_clean_scene_tags(tags):
    """開幕画像の場面タグ。人物の外見は chars 側に任せる。"""
    try:
        raw = split_tags(tags) if isinstance(tags, str) else (tags or [])
    except Exception:
        raw = []
    out = []
    for x in raw:
        s = wizard_plain_text(x, 60)
        low = s.lower()
        if not s or not re.search(r"[a-zA-Z0-9]", s):
            continue
        if any(w in low for w in OPENING_SCENE_DROP):
            continue
        if re.search(r"\b(hair|eyes?|eyebrows?|skin|breasts?|nipples?|penis|pussy|uniform|dress|shirt|skirt|panties|bra|lingerie|outfit)\b", low):
            continue
        if s not in out:
            out.append(s)
    return out[:12]


def wizard_opening_tags(d):
    """タグカタログの image_tags から、開幕画像の場面タグを作る。"""
    out = []
    for tid in (d.get("selected_tags") or []):
        tag = wizard_tag_by_id(tid) or {}
        raw = tag.get("image_tags") or []
        if isinstance(raw, str):
            raw = split_tags(raw)
        for x in raw:
            out.append(x)
    return wizard_clean_scene_tags(out)


WIZARD_COUNT_RE = re.compile(r"(\d+\+?\s*(girl|boy|other)s?|multiple (girls|boys|others)|solo( focus)?)", re.I)


def wizard_count_tag(tags, chars):
    """開幕画像に必ず1つ入れる人数タグ。AI/プリセット優先、無ければ1girl。"""
    if isinstance(tags, str):
        tags = split_tags(tags)
    for x in (tags or []):
        s = str(x).strip()
        if WIZARD_COUNT_RE.fullmatch(s):
            return s
    for c in chars or []:
        for t in split_tags(c.get("tags")):
            s = str(t).strip()
            if WIZARD_COUNT_RE.fullmatch(s):
                return s
    return "1girl"


def wizard_opening_message_id(s):
    for m in (s.get("messages") or []):
        im = m.get("image") or {}
        if m.get("role") == "assistant" and im.get("opening"):
            return str(m.get("id") or "")
    return ""


def wizard_enqueue_opening_image(s, setting, tags=None):
    """作成をブロックせず、最初のassistant発言に開幕画像を積む。戻り値は message id。"""
    if (setting.get("image_mode") or "always") == "off":
        return ""
    first = next((m for m in (s.get("messages") or [])
                  if m.get("role") == "assistant"), None)
    if not first or not first.get("id"):
        return ""
    chars = [c for c in (setting.get("chars") or [])
             if isinstance(c, dict) and str(c.get("name") or "").strip()][:3]
    names = [str(c.get("name"))[:40] for c in chars]
    if not names:
        # 画像タグが空でも、第一声のキャラ名は画像メタに残す
        ch = setting.get("character") if isinstance(setting.get("character"), dict) else {}
        lead = str(ch.get("name") or "").strip()
        if lead:
            names = [lead[:40]]
    blocked = set()
    for c in chars:
        for t in split_tags(c.get("tags")):
            ts = str(t).strip()
            if WIZARD_COUNT_RE.fullmatch(ts):
                continue
            blocked.add(ts.lower())
    scene = wizard_clean_scene_tags(tags)
    scene = [t for t in scene if t.strip().lower() not in blocked]
    if not scene:
        scene = ["indoors", "morning", "looking at viewer"]
    if not any(WIZARD_COUNT_RE.fullmatch(t.strip()) for t in scene):
        scene.insert(0, wizard_count_tag(tags, chars))
    image_cfg = setting.get("image") if isinstance(setting.get("image"), dict) else {}
    img = {
        "prompt": ", ".join(scene),
        "negative": str(image_cfg.get("base_negative") or ""),
        "raw": False,
        "seed": random.randint(0, 2**32 - 1),
        "status": "queued",
        "chars": names,
        "opening": True,
    }
    with lock:
        try:
            cur = load(s["id"])
        except Exception:
            return ""
        cm = find_msg(cur, first["id"])
        if not cm or cm.get("image"):
            return ""
        cm["image"] = img
        save(cur)
        queued_images.append((s["id"], first["id"]))
        image_event.set()
    return str(first["id"])


def wizard_template_compose(d):
    """AIが使えなくても必ず非空の設定を作る。"""
    name = str(wslot_value(d, "partner_name") or "ヒロイン")[:40]
    work = str(wslot_value(d, "work") or "")[:60]
    rel = "、".join(wiz_list(wslot_value(d, "relationship"))) or "特別な関係"
    world = "、".join(wiz_list(wslot_value(d, "world"))) or "現代日本"
    tone = "、".join(wiz_list(wslot_value(d, "tone"))) or "穏やか"
    role = "、".join(wiz_list(wslot_value(d, "player_role"))) or "主人公"
    kinks = "、".join(wiz_list(wslot_value(d, "kinks"))) or "なし"
    start = str(wizard_effective_value(d, "start_point") or "出会った直後")
    pov = str(wizard_effective_value(d, "pov") or "second")
    style = str(wizard_effective_value(d, "style") or "novel")
    length = str(wizard_effective_value(d, "length") or "medium")
    player = str(wizard_effective_value(d, "player_name") or "あなた")
    player_profile = str(wizard_effective_value(d, "player_profile") or "")
    goal = str(wizard_effective_value(d, "goal") or "おまかせ")
    ending_label = {
        "happy": "ハッピーエンド",
        "bittersweet": "ビターエンド",
        "dark": "ダークエンド",
        "open": "オープン",
    }.get(str(wizard_effective_value(d, "ending") or ""), "おまかせ")
    ng = "、".join(wizard_ng_labels(d)) or "なし"

    title = (f"{work} — " if work else "") + rel
    title = title[:40] or "新しい物語"
    summary = (
        f"{world}を舞台に、{name}との{rel}を描く物語。"
        f"あなたは{role}として、{start}の状況から関わっていく。"
        f"目的は{goal}。雰囲気は{tone}。"
    )[:300]
    scenario = (
        f"{world}。{name}はあなたの目の前にいる。"
        f"二人の関係は{rel}。あなたの立場は{role}。"
        f"物語は{start}から始まり、{goal}に向かう。"
        f"結末は{ending_label}。{tone}空気の中で、{name}との距離が変わり始める。"
    )[:2000]
    first = f"「……{name}です。よろしくね」\n{name}は少しだけ視線を逸らして、そう言った。"
    instructions = (
        f"あなたは日本語のインタラクティブ小説の語り手です。"
        f"視点は{pov}、文体は{style}、長さは{length}。"
        f"{name}の性格と口調を一貫させ、{tone}雰囲気を保つ。"
        f"物語の目的・ゴール: {goal}。結末: {ending_label}。"
        f"あなたの設定: {player_profile or 'おまかせ'}。"
        f"特殊要素: {kinks}。NG: {ng}。"
        f"全員18歳以上の成人として描写する。"
    )[:2000]
    chars = wizard_build_chars(d, [], name)
    opening_image = wizard_opening_tags(d) or ["indoors", "morning", "looking at viewer"]
    warnings = ["AIを使わずにテンプレートで設定を生成しました。プレビューで調整してください。"]
    if not chars[0].get("tags"):
        warnings.append("画像タグが空です。必要ならキャラプリセットを選んでください。")
    period = "午前"
    try:
        period = infer_initial_period(scenario + " " + first) or "午前"
    except Exception:
        pass
    setting = {
        "title": title,
        "summary": summary,
        "instructions": instructions,
        "scenario": scenario,
        "first_message": first,
        "character": {
            "name": name,
            "age": "24",
            "profile": f"{name}。{rel}としてあなたの前に現れる。{tone}雰囲気を持つ。",
            "speech": f"「……{player}、どうする？」",
        },
        "chars": chars,
        "opening_image": opening_image,
        "elapsed_time": {"day": 1, "period": normalize_period(period)},
        "user_name": player,
        "project": str(wizard_effective_value(d, "project") or "")[:40],
    }
    return setting, warnings


def wizard_partner_preset(d):
    if d.get("partner_preset_off"):
        return None
    pid = str(d.get("partner_preset_id") or "").strip()
    name = str(wslot_value(d, "partner_name") or "").strip()
    presets = []
    try:
        presets = char_presets() or []
    except Exception:
        presets = []
    if pid:
        for p in presets:
            if pid in (str(p.get("id") or ""), str(p.get("name") or "")):
                return p
    if name:
        for p in presets:
            if str(p.get("name") or "") == name:
                return p
        try:
            return find_char(presets, name, fuzzy=True)
        except TypeError:
            try:
                return find_char(name)
            except Exception:
                return None
        except Exception:
            return None
    return None


def wizard_preset_tags(preset):
    """既存の char_presets のタグを、そのまま見た目の正として使う。"""
    out = []
    for t in split_tags((preset or {}).get("tags")):
        s = wizard_plain_text(t, 60)
        if s and s not in out:
            out.append(s)
    return out[:40]


def wizard_build_chars(d, raw_chars, name):
    """内部では list[dict] を返す。最終段で wizard_normalize_chars() が文字列化する。"""
    item = {}
    if isinstance(raw_chars, list):
        for x in raw_chars:
            if isinstance(x, dict) and str(x.get("name") or "").strip() == name:
                item = x
                break
        if not item:
            for x in raw_chars:
                if isinstance(x, dict):
                    item = x
                    break
    elif isinstance(raw_chars, dict):
        item = raw_chars

    preset = wizard_partner_preset(d)
    preset_tags = wizard_preset_tags(preset)
    ai_tags = wizard_clean_tags(item.get("tags"))
    if preset_tags:
        tags, src = preset_tags, "preset"
    else:
        tags = ai_tags
        src = "ai" if ai_tags else "template"
    return [{
        "id": new_id(),
        "name": wizard_plain_text(name or (preset or {}).get("name") or "ヒロイン", 40),
        "tags": tags,
        "source": src,
        "locked": src == "preset",
    }]


def wizard_normalize_chars(chars):
    """本体の chars は tags がカンマ区切り文字列、id/source 必須。"""
    out = []
    for c in chars or []:
        if not isinstance(c, dict):
            continue
        name = wizard_plain_text(c.get("name"), 40)
        if not name:
            continue
        locked = bool(c.get("locked"))
        src = str(c.get("source") or ("preset" if locked else "ai"))
        if src == "preset" or locked:
            # プリセットはユーザーが固定した「正」。服装・人数・品質タグも削らない。
            tags = ", ".join(wizard_preset_tags(c))
        else:
            tags = ", ".join(wizard_clean_tags(c.get("tags")))
        if not tags:
            continue
        out.append({
            "id": str(c.get("id") or new_id()),
            "name": name,
            "tags": tags,
            "locked": locked,
            "source": src,
        })
    return out


def wizard_age_is_minor(age):
    s = str(age or "")
    # 「17歳」のような明示年齢を最優先
    m = re.search(r"(\d+)\s*(歳|才)", s)
    if m:
        return int(m.group(1)) < 18
    # 「大学1年」の1を年齢と誤認しない
    if re.search(r"(大学|短大|専門学校|社会人|成人|大人)", s):
        return False
    # 数字だけでなく「高校生」「高2」などの表現も未成年として扱う
    # 「小生」「中生」のような一般語を誤爆しないよう、学校段階の語だけで判定する
    if re.search(r"(小学生|中学生|高校生|女子高生|男子高生|女子中学生|男子中学生|小学|中学|高校|中等部|高等部|幼女|幼児|JK|JC|JS|小[1-6]|中[1-3]|高[1-3])", s, re.I):
        return True
    m = re.search(r"\d+", s)
    return bool(m and int(m.group()) < 18)


def wizard_age_warning(age):
    s = str(age or "").strip()
    if s in ("なし", "未設定"):
        return ""
    if wizard_age_is_minor(s):
        return "18歳未満の年齢が含まれています。年齢を「なし」にします。"
    if not re.search(r"\d+", s):
        return "年齢が不明です。全員18歳以上の成人として扱います。"
    return ""


def wizard_sanitize_compose(d, raw):
    if not isinstance(raw, dict):
        raise WizardError("設定のJSONが不正です", code="bad_json")
    warnings = []
    ch = raw.get("character")
    if not isinstance(ch, dict):
        ch = {}
    name = wizard_plain_text(ch.get("name"), 40) or str(wslot_value(d, "partner_name") or "ヒロイン")
    age = wizard_plain_text(ch.get("age"), 10) or "24"
    aw = wizard_age_warning(age)
    if aw:
        warnings.append(aw)
        if wizard_age_is_minor(age):
            age = "なし"
        elif not re.search(r"\d+", str(age or "")):
            age = "24"

    setting = {
        "title": wizard_plain_text(raw.get("title"), 40),
        "summary": wizard_plain_text(raw.get("summary"), 300),
        "instructions": wizard_plain_text(raw.get("instructions"), 2000),
        "scenario": wizard_plain_text(raw.get("scenario"), 2000),
        "first_message": wizard_plain_text(raw.get("first_message"), 1200),
        "character": {
            "name": name,
            "age": age,
            "profile": wizard_plain_text(ch.get("profile"), 600),
            "speech": wizard_plain_text(ch.get("speech"), 400),
        },
    }
    setting["chars"] = wizard_build_chars(d, raw.get("chars"), name)
    opening_image = wizard_clean_scene_tags(raw.get("opening_image"))
    if opening_image:
        setting["opening_image"] = opening_image

    et = raw.get("elapsed_time")
    day = _safe_day(et.get("day") if isinstance(et, dict) else 1, 1)
    period = normalize_period((et.get("period") if isinstance(et, dict) else "") or "午前")
    setting["elapsed_time"] = {"day": day, "period": period}
    setting["user_name"] = str(wizard_effective_value(d, "player_name") or "あなた")[:40]
    setting["project"] = str(wizard_effective_value(d, "project") or "")[:40]

    text = " ".join([setting["summary"], setting["scenario"], setting["first_message"]])
    for ng in wizard_ng_labels(d):
        if ng and ng.lower() in text.lower():
            warnings.append(f"NG「{ng}」に触れる可能性があります。プレビューで確認してください。")

    for w in (raw.get("warnings") or []):
        s = wizard_plain_text(w, 120)
        if s:
            warnings.append(s)
    alts = [wizard_plain_text(x, 40) for x in (raw.get("title_alternatives") or [])]
    warnings = list(dict.fromkeys(warnings))
    meta = {"source": "ai", "warnings": warnings, "title_alternatives": [x for x in alts if x][:3]}
    return setting, meta


def wizard_validate_compose(setting):
    missing = []
    for k in ("title", "summary", "instructions", "scenario", "first_message"):
        if not str((setting or {}).get(k) or "").strip():
            missing.append(k)
    ch = (setting or {}).get("character") or {}
    if not isinstance(ch, dict):
        missing.append("character")
    else:
        for k in ("name", "profile", "speech"):
            if not str(ch.get(k) or "").strip():
                missing.append(f"character.{k}")
    return missing


def wizard_build_final_setting(d, overrides=None):
    comp = d.get("compose") or {}
    setting = comp.get("setting")
    if not isinstance(setting, dict):
        raise WizardError("設定がまだ生成されていません", code="wizard_not_composed")
    if not isinstance(overrides, dict):
        overrides = {}

    base = merge_defaults(copy.deepcopy(d.get("base_setting") or {}))
    base.update(copy.deepcopy(RESET_PROGRESS))
    base.update(copy.deepcopy(RESET_STORY))
    base.update(copy.deepcopy(NO_IDE))
    base.update(copy.deepcopy(NO_FOLDER))
    for k in ("initial_elapsed_time", "rotation", "rotation_notice",
              "rotation_skip_until", "rotation_reserve"):
        base.pop(k, None)

    final = copy.deepcopy(setting)
    old_name = str((final.get("character") or {}).get("name") or "").strip()
    for k in ("title", "summary", "scenario", "first_message", "instructions"):
        if k in overrides:
            final[k] = wizard_plain_text(overrides.get(k), {"title": 40, "summary": 300}.get(k, 2000))
    if isinstance(overrides.get("character"), dict):
        ch = dict(final.get("character") or {})
        for k, lim in (("name", 40), ("age", 10), ("profile", 600), ("speech", 400)):
            if k in overrides["character"]:
                ch[k] = wizard_plain_text(overrides["character"].get(k), lim)
        final["character"] = ch
    if isinstance(overrides.get("chars"), list):
        chars = []
        for x in overrides["chars"][:3]:
            if not isinstance(x, dict):
                continue
            chars.append({
                "id": new_id(),
                "name": wizard_plain_text(x.get("name"), 40),
                "tags": wizard_clean_tags(x.get("tags")),
                "source": "user",
                "locked": bool(x.get("locked")),
            })
        if chars:
            final["chars"] = chars

    name = str((final.get("character") or {}).get("name") or "")
    user_name = ((wizard_plain_text(overrides.get("user_name"), 40) or "あなた")
                 if "user_name" in overrides else
                 str(wizard_effective_value(d, "player_name") or "あなた")[:40])
    final["user_name"] = user_name

    # プレビューで名前を変えたら、chars と第一声などにも追従させる
    chars_list = final.get("chars")
    if name and isinstance(chars_list, list) and chars_list:
        first_char = chars_list[0]
        if isinstance(first_char, dict):
            first_char["name"] = name
    if old_name and name and old_name != name:
        for k in ("first_message", "scenario", "summary", "instructions"):
            if isinstance(final.get(k), str):
                final[k] = final[k].replace(old_name, name)

    # instructions はプレビューで編集されても、確定事項を必ず末尾に入れる
    base_instr = str(final.get("instructions") or "").strip()
    base_instr = re.sub(r"\n\n# 確定事項\n(?:- .*\n?)*", "", base_instr).strip()
    facts = (
        "\n\n# 確定事項\n"
        f"- 相手の名前: {name}\n"
        f"- あなたの名前: {user_name}\n"
        f"- あらすじ: {final.get('summary', '')}\n"
        f"- 目的・ゴール: {wizard_effective_value(d, 'goal') or 'おまかせ'}\n"
        f"- NG: {'、'.join(wizard_ng_labels(d)) or 'なし'}"
    )
    # 確定事項が長い instructions の末尾切れで消えないよう、facts の分を先に確保する
    final["instructions"] = (base_instr[:max(0, 2000 - len(facts))] + facts)[:2000]

    # プレビューで年齢を書き換えた場合も成人チェックする
    age_warn = wizard_age_warning((final.get("character") or {}).get("age"))
    if age_warn:
        age_val = (final.get("character") or {}).get("age")
        if wizard_age_is_minor(age_val):
            final.setdefault("character", {})["age"] = "なし"
        elif not re.search(r"\d+", str(age_val or "")):
            final.setdefault("character", {})["age"] = "24"

    # プレビューで必須項目を空にした場合は作成しない
    miss = wizard_validate_compose(final)
    if miss:
        raise WizardError("必須項目が空です: " + ", ".join(miss), code="bad_compose")

    for k in COMPOSE_SETTING_KEYS:
        if k in final and final[k] not in (None, "", []):
            base[k] = final[k]
    base["chars"] = wizard_normalize_chars(final.get("chars"))
    if "project" in overrides:
        base["project"] = wizard_plain_text(overrides.get("project"), 40)
    else:
        base["project"] = str(wizard_effective_value(d, "project")
                              or base.get("project") or "")[:40]
    base["user_name"] = user_name
    if not base.get("title"):
        base["title"] = "新しいチャンネル"

    # 新規環境（ベースチャンネルなし）は作成AIを本文AIにも引き継ぐ
    if not (d.get("base_setting") or {}) and not d.get("base_sid"):
        mc = d.get("model") or {}
        if mc.get("provider") in ("google", "vertex") and mc.get("model"):
            base["ai_provider"] = mc["provider"]
            if mc["provider"] == "google":
                base["google_model"] = mc["model"]
            else:
                base["vertex_model"] = mc["model"]
            base["thinking_level"] = mc.get("thinking_level") or "high"

    warnings = list(comp.get("warnings") or [])
    if age_warn:
        warnings.append(age_warn)
    warnings += [f"{SLOT_LABEL.get(k, k)}はおまかせで埋めました" for k in (comp.get("auto_slots") or [])]
    return base, list(dict.fromkeys(warnings))
```

### 10.7 AIアダプタ（Google / Vertex / Antigravity）

```python
def wizard_ai_enabled(d):
    mc = d.get("model") or {}
    if not mc.get("ai_enabled", True):
        return False
    return str(mc.get("provider") or "") != "none"


def wizard_error_code(msg, http=0):
    m = str(msg or "").lower()
    if http in (401, 403) or "api key" in m or "permission" in m or "unauthenticated" in m:
        return "no_key"
    if http == 404 or "not found" in m or "not_found" in m:
        return "model_not_found"
    try:
        if _is_filter_error_msg(msg):
            return "safety"
    except Exception:
        pass
    if "timeout" in m or "timed out" in m:
        return "timeout"
    if "quota" in m or "rate" in m or http == 429:
        return "quota"
    return "ai_error"


def wizard_generate(model, prompt, system="", max_tokens=4096, kind="compose", job=None):
    mc = model or {}
    if not mc.get("ai_enabled", True) or str(mc.get("provider") or "") == "none":
        raise WizardError("AIは無効です", code="ai_disabled")
    provider = str(mc.get("provider") or "").strip().lower()
    mid = str(mc.get("model") or "").strip()
    thinking = str(mc.get("thinking_level") or "high").strip().lower()
    if kind == "questions":
        thinking = "low"
    if provider == "antigravity":
        return wizard_ide_generate(mc, prompt, kind, job)
    if provider not in ("google", "vertex"):
        raise WizardError("作成AIのプロバイダーが未設定です", code="no_model")
    try:
        if provider == "google" and not mid:
            mid = google_ai.default_model()
        if provider == "vertex" and not mid:
            mid = google_ai.default_vertex_model()
    except Exception as e:
        raise WizardError(f"モデルを取得できません: {e}", code="model_not_found")
    try:
        res = google_ai.generate(
            mid, prompt, system=system, safety_off=True,
            max_output_tokens=max_tokens, fetch=False,
            timeout=180, provider=provider, thinking_level=thinking,
        )
    except Exception as e:
        code = wizard_error_code(str(e))
        raise WizardError(str(e), code=code, retryable=code in ("timeout", "quota", "ai_error"), status=502)
    if isinstance(res, str):
        text = res
        meta = {"provider": provider, "model": mid, "thinking_level": thinking}
    elif isinstance(res, dict):
        if res.get("error"):
            msg = str(res.get("error"))
            code = wizard_error_code(msg, _as_int(res.get("http_status"), 0))
            raise WizardError(msg, code=code, retryable=code in ("timeout", "quota", "ai_error"), status=502)
        text = str(res.get("text") or res.get("output") or "")
        meta = {"provider": provider, "model": mid, "thinking_level": thinking}
    else:
        raise WizardError("AIの応答形式が不正です", code="ai_error", retryable=True, status=502)
    if not text.strip():
        raise WizardError("AIが空の応答を返しました", code="ai_error", retryable=True, status=502)
    return text, meta


def wizard_ensure_builder():
    if ide_poke is None:
        raise WizardError("Antigravityが利用できません", code="antigravity_down")
    with wizard_lock:
        app = app_settings()
        cid = str(app.get("wizard_builder_ide") or "").strip()
        if cid:
            try:
                if ide_poke.summary(cid):
                    return cid
            except Exception:
                pass
        os.makedirs(WIZARD_IDE_DIR, exist_ok=True)
        res = ide_poke.new_conversation(WIZARD_IDE_DIR)
        # new_conversation は (会話ID, 説明) か (None, 失敗理由) を返す。
        if isinstance(res, str):
            nid, why = res, ""
        elif isinstance(res, (tuple, list)) and len(res) >= 2:
            nid, why = str(res[0] or ""), str(res[1] or "")
        else:
            nid, why = "", ""
        if not nid:
            raise WizardError(why or "作成AIの会話を作成できませんでした",
                              code="antigravity_down", retryable=True, status=502)
    # デッドロック防止: wizard_lock を解放してから lock で設定を保存する（ロック逆順ネスト防止）
    with lock:
        cur = read_json(APP_SETTINGS, {})
        cur["wizard_builder_ide"] = nid
        write_json(APP_SETTINGS, cur)
    return nid


def wizard_ide_generate(mc, prompt, kind="compose", job=None):
    cid = wizard_ensure_builder()
    mid = str(mc.get("model") or "").strip()
    try:
        summ = ide_poke.summary(cid) or {}
        step = _as_int(summ.get("stepCount") or summ.get("step_count"), 0)
    except Exception:
        step = 0

    last_msg = ""
    # 同じ会話に複数の生成を同時に送らない（send_poke と同じ会話ロックを使う）
    with get_poke_lock(cid):
        if job and job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        sent = False
        for attempt in range(3):
            if job and job.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            try:
                res = ide_poke.send_to_conversation(cid, prompt, model=mid or None)
            except Exception as e:
                raise WizardError(str(e), code="antigravity_down", retryable=True, status=502)
            ok = bool(res[0]) if isinstance(res, (tuple, list)) else bool(res)
            msg = str(res[1]) if isinstance(res, (tuple, list)) and len(res) > 1 else ""
            retry = bool(res[2]) if isinstance(res, (tuple, list)) and len(res) > 2 else False
            last_msg = msg or last_msg
            if ok:
                sent = True
                break
            if retry and attempt < 2:
                time.sleep(5)
                continue
            raise WizardError(msg or "送信できませんでした", code="antigravity_down", retryable=retry, status=502)
        if not sent:
            raise WizardError(last_msg or "送信できませんでした", code="antigravity_down", retryable=True, status=502)

        deadline = time.time() + 180
        while time.time() < deadline:
            if job and job.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            time.sleep(1)
            try:
                idle_ok, _st = ide_poke.idle(cid)
            except Exception:
                idle_ok = False
            if not idle_ok:
                continue
            try:
                r = ide_poke.last_reply(cid, after_step=step)
            except Exception:
                r = (None, None, "")
            text = str(r[0] or "") if isinstance(r, (tuple, list)) else str(r or "")
            err = str(r[1] or "") if isinstance(r, (tuple, list)) and len(r) > 1 else ""
            if err:
                last_msg = err
            if text.strip():
                return text, {"provider": "antigravity", "model": mid, "thinking_level": ""}
        raise WizardError(last_msg or "Antigravityの応答がタイムアウトしました", code="timeout", retryable=True, status=502)


def wizard_extract_json(text):
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise WizardError("JSONを読み取れませんでした", code="bad_json")
    try:
        return json.loads(s[a:b + 1])
    except ValueError as e:
        raise WizardError(f"JSONの形式が不正です: {e}", code="bad_json")
```

### 10.8 ジョブ

```python
def wizard_job_running(jid):
    j = wizard_jobs.get(str(jid or ""))
    return bool(j and j.get("status") in ("queued", "running")
                and time.time() - j.get("ts_epoch", 0) < WIZARD_JOB_STALE)


def wizard_start_job(draft, kind, expected_rev=None):
    if kind not in ("questions", "compose"):
        raise WizardError("ジョブ種別が不正です", code="bad_request")
    with wizard_lock:
        d = wizard_load(draft.get("id"))
        if not d:
            raise WizardError("下書きがありません", code="not_found", status=404)
        if expected_rev is not None and _as_int(expected_rev, -1) != _as_int(d.get("rev"), 0):
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(d, True)
            raise e
        if d.get("status") == "created":
            raise WizardError("作成済みです", code="created", status=409)
        if wizard_job_running(d.get("last_job")):
            raise WizardError("この下書きで別の処理を実行中です", code="wizard_busy", status=409)

        jid = _wiz_uid("wj_")
        job = {
            "job_id": jid,
            "draft_id": d["id"],
            "kind": kind,
            "status": "queued",
            "progress": "質問を考えています" if kind == "questions" else "設定を書いています",
            "result": None,
            "error": None,
            "error_code": "",
            "retryable": False,
            "cancel": False,
            "created": now(),
            "ts_epoch": time.time(),
            "base_rev": d.get("rev", 0),
        }
        wizard_jobs[jid] = job
        d["last_job"] = jid
        if kind == "compose":
            d["status"] = "composing"
            d["step"] = "creating"
        else:
            d["step"] = "questions"
        wizard_save(d, bump=False)
        public = wizard_public(d, True)

    def run():
        j = wizard_jobs.get(jid)
        if not j:
            return
        j["status"] = "running"
        j["ts_epoch"] = time.time()
        try:
            if j.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            if kind == "questions":
                result = wizard_do_questions(j)
            else:
                result = wizard_do_compose(j)
            j["result"] = result
            j["status"] = "done"
        except WizardError as e:
            j["status"] = "error"
            j["error"] = str(e)
            j["error_code"] = e.code
            j["retryable"] = e.retryable
        except Exception as e:
            j["status"] = "error"
            j["error"] = str(e)
            j["error_code"] = "wizard_error"
            j["retryable"] = True
        finally:
            j["ts_epoch"] = time.time()
            if j.get("kind") == "compose" and j.get("status") == "error":
                with wizard_lock:
                    dd = wizard_load(j["draft_id"])
                    if dd and dd.get("status") == "composing":
                        has = bool((dd.get("compose") or {}).get("setting"))
                        dd["status"] = "ready" if has else "interview"
                        dd["step"] = "preview" if has else "tags"
                        wizard_save(dd, bump=False)

    threading.Thread(target=run, daemon=True).start()
    return job, public


def wizard_do_questions(job):
    with wizard_lock:
        d = wizard_load(job["draft_id"])
        if not d or d.get("last_job") != job["job_id"]:
            raise WizardError("古いジョブです", code="stale", status=409)
        if job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        if d.get("rev") != job.get("base_rev"):
            raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)

    pending = [q for q in (d.get("questions") or []) if q.get("id") not in (d.get("asked_question_ids") or [])]
    if pending:
        return {"draft": wizard_public(d, True), "questions": pending, "done": False}
    if not wizard_ai_enabled(d):
        with wizard_lock:
            d["done_questions"] = True
            d["status"] = "ready" if wizard_ready(d) else "interview"
            wizard_save(d)
        return {"draft": wizard_public(d, True), "questions": [], "done": True}
    if d.get("done_questions") or d.get("round", 0) >= WIZARD_MAX_ROUNDS or not wizard_askable_slots(d):
        with wizard_lock:
            d["done_questions"] = True
            d["status"] = "ready" if wizard_ready(d) else "interview"
            wizard_save(d)
        return {"draft": wizard_public(d, True), "questions": [], "done": True}

    with wizard_lock:
        d = wizard_load(d["id"])
        if d.get("ai_calls", 0) >= WIZARD_AI_CALL_LIMIT:
            raise WizardError("AI呼び出しの上限に達しました", code="ai_limit", status=429)
        d["ai_calls"] = _as_int(d.get("ai_calls"), 0) + 1
        wizard_save(d, bump=False)

    try:
        text, _meta = wizard_generate(d.get("model") or {}, wizard_question_prompt(d),
                                      system="あなたは質問設計の専門家です。JSONのみを返します。",
                                      max_tokens=2048, kind="questions", job=job)
        raw = wizard_extract_json(text)
        qs = wizard_sanitize_questions(d, raw.get("questions") if isinstance(raw, dict) else [])
    except WizardError as e:
        if job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        if e.code in ("stale", "canceled"):
            raise
        qs = []
        with wizard_lock:
            d = wizard_load(d["id"])
            d.setdefault("warnings", []).append(f"AI質問を取得できませんでした: {e}")
            d["done_questions"] = True
            d["status"] = "ready" if wizard_ready(d) else "interview"
            wizard_save(d)
        return {"draft": wizard_public(d, True), "questions": [], "done": True}

    with wizard_lock:
        d = wizard_load(d["id"])
        if job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        if d.get("rev") != job.get("base_rev"):
            raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)
        d["questions"] = qs
        d["round"] = min(_as_int(d.get("round"), 0) + 1, WIZARD_MAX_ROUNDS)
        d["done_questions"] = not qs
        d["status"] = "interview" if qs else ("ready" if wizard_ready(d) else "interview")
        if qs:
            d["step"] = "questions"
        wizard_save(d)
        pub = wizard_public(d, True)
    return {"draft": pub, "questions": qs, "done": not qs}


def wizard_do_compose(job):
    with wizard_lock:
        d = wizard_load(job["draft_id"])
        if not d or d.get("last_job") != job["job_id"]:
            raise WizardError("古いジョブです", code="stale", status=409)
        if job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        if d.get("rev") != job.get("base_rev"):
            raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)
        if (d.get("compose") or {}).get("setting") and not d.get("compose_stale"):
            return {"draft": wizard_public(d, True)}

        auto_slots = [k for k, s in (d.get("slots") or {}).items()
                      if (s or {}).get("source") == "auto"]
        missing_before = wizard_missing_required(d)
        if missing_before:
            for k in missing_before:
                if k not in auto_slots:
                    auto_slots.append(k)
                wslot_set_auto(d, k)
            # 自動補完したスロットを即座に永続化（以後の wizard_load で破棄されないよう bump=False で保存）
            wizard_save(d, bump=False)

    if not wizard_ai_enabled(d):
        with wizard_lock:
            d = wizard_load(d["id"])
            if job.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            if d.get("rev") != job.get("base_rev"):
                raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)
        setting, warnings = wizard_template_compose(d)
        cmeta = {"source": "template", "warnings": warnings,
                 "title_alternatives": [], "auto_slots": auto_slots, "model": copy.deepcopy(d.get("model") or {})}
        with wizard_lock:
            d = wizard_load(d["id"])
            d["compose"] = dict(cmeta, setting=setting)
            d["compose_stale"] = False
            d["status"] = "ready"
            d["step"] = "preview"
            d["done_questions"] = True
            d["warnings"] = list(warnings)
            wizard_save(d)
            pub = wizard_public(d, True)
        return {"draft": pub}

    setting = None
    cmeta = None
    model = copy.deepcopy(d.get("model") or {})
    for attempt in (1, 2):
        with wizard_lock:
            d = wizard_load(d["id"])
            if job.get("cancel"):
                raise WizardError("キャンセルしました", code="canceled")
            if d.get("ai_calls", 0) >= WIZARD_AI_CALL_LIMIT:
                raise WizardError("AI呼び出しの上限に達しました", code="ai_limit", status=429)
            d["ai_calls"] = _as_int(d.get("ai_calls"), 0) + 1
            wizard_save(d, bump=False)

        try:
            prompt = wizard_compose_prompt(d)
            if attempt == 2:
                prompt += "\n\n前回の出力は不正でした。JSONのみを返し、必須項目をすべて埋めてください。"
            text, _meta = wizard_generate(model, prompt,
                                          system="あなたは設定ライターです。JSONのみを返します。",
                                          max_tokens=8192, kind="compose", job=job)
            raw = wizard_extract_json(text)
            candidate, cmeta = wizard_sanitize_compose(d, raw)
            miss = wizard_validate_compose(candidate)
            if miss:
                raise WizardError("必須項目が不足しています: " + ", ".join(miss), code="bad_compose")
            setting = candidate
            break
        except WizardError as e:
            if e.code in ("stale", "canceled"):
                raise
            if attempt == 2 or e.code not in ("bad_json", "bad_compose", "timeout", "ai_error", "quota", "no_key", "safety", "ai_disabled"):
                break
            if e.code == "timeout" and model.get("thinking_level") == "high":
                model["thinking_level"] = "medium"

    if setting is None:
        setting, warnings = wizard_template_compose(d)
        cmeta = {"source": "template", "warnings": warnings, "title_alternatives": []}
    cmeta = dict(cmeta or {})
    cmeta["auto_slots"] = auto_slots
    cmeta["model"] = copy.deepcopy(model)

    with wizard_lock:
        d = wizard_load(d["id"])
        if job.get("cancel"):
            raise WizardError("キャンセルしました", code="canceled")
        if d.get("rev") != job.get("base_rev"):
            raise WizardError("下書きが更新されました", code="stale", retryable=True, status=409)
        d["compose"] = dict(cmeta, setting=setting)
        d["compose_stale"] = False
        d["status"] = "ready"
        d["step"] = "preview"
        d["done_questions"] = True
        d["warnings"] = list(cmeta.get("warnings") or [])
        wizard_save(d)
        pub = wizard_public(d, True)
    return {"draft": pub}
```

### 10.9 下書き・公開・回答

```python
def wizard_new_draft(base, model, confirm):
    t = now()
    return {
        "schema_version": 1,
        "id": _wiz_uid("wiz_"),
        "rev": 0,
        "created": t,
        "updated": t,
        "status": "interview",
        "step": "base",
        "base_sid": "",
        "base_title": "",
        "inherit_base": False,
        "base_setting": copy.deepcopy(base or {}),
        "model": copy.deepcopy(model or {}),
        "slots": {},
        "selected_tags": [],
        "selected_ng": ["ng_minor"],
        "custom_ng": [],
        "partner_preset_id": "",
        "partner_preset_off": False,
        "questions": [],
        "asked_slots": [],
        "asked_question_ids": [],
        "round": 0,
        "done_questions": False,
        "compose": None,
        "compose_stale": True,
        "last_job": "",
        "created_sid": "",
        "warnings": [],
        "ai_calls": 0,
        "confirm": bool(confirm),
    }


def wizard_public(d, full=False):
    missing = wizard_missing_required(d)
    out = {
        "id": d.get("id"),
        "rev": d.get("rev", 0),
        "status": d.get("status", "interview"),
        "step": d.get("step", "idea"),
        "created": d.get("created"),
        "updated": d.get("updated"),
        "base_sid": d.get("base_sid", ""),
        "base_title": str(d.get("base_title") or
                          ((d.get("base_setting") or {}).get("title") or "")),
        "inherit_base": bool(d.get("inherit_base", bool(d.get("base_setting")))),
        "model": copy.deepcopy(d.get("model") or {}),
        "selected_tags": list(d.get("selected_tags") or []),
        "partner_preset_id": str(d.get("partner_preset_id") or ""),
        "partner_preset_off": bool(d.get("partner_preset_off")),
        "selected_ng": list(d.get("selected_ng") or []),
        "custom_ng": list(d.get("custom_ng") or []),
        "round": d.get("round", 0),
        "done_questions": bool(d.get("done_questions")),
        "ready": not missing,
        "missing": missing,
        "confirm": bool(d.get("confirm", True)),
        "last_job": d.get("last_job", ""),
        "created_sid": d.get("created_sid", ""),
        "warnings": list(d.get("warnings") or []),
        "compose_stale": bool(d.get("compose_stale", True)),
        "title_hint": ((d.get("compose") or {}).get("setting") or {}).get("title")
                      or str(wslot_value(d, "work") or "")[:24]
                      or str(wslot_value(d, "rough_idea") or "")[:24]
                      or "新しいチャンネル",
        "defaults": {
            "project": str((d.get("base_setting") or {}).get("project") or ""),
            "user_name": str((d.get("base_setting") or {}).get("user_name") or "あなた"),
        },
    }
    if full:
        out["slots"] = {
            k: {
                "label": WIZARD_SLOTS[k]["label"],
                "value": copy.deepcopy(wslot_value(d, k)),
                "source": wslot_source(d, k),
            }
            for k in WIZARD_SLOTS
        }
        out["questions"] = copy.deepcopy(d.get("questions") or [])
        out["asked_slots"] = list(d.get("asked_slots") or [])
        out["asked_question_ids"] = list(d.get("asked_question_ids") or [])
        out["compose"] = copy.deepcopy(d.get("compose"))
        base = d.get("base_setting") or {}
        out["base_info"] = {
            "ai_provider": base.get("ai_provider"),
            "google_model": base.get("google_model"),
            "vertex_model": base.get("vertex_model"),
            "image_mode": base.get("image_mode") or "always",
            "suggest_choices": base.get("suggest_choices"),
            "project": base.get("project"),
        }
    return out


def wizard_set_base(d, base_sid, inherit):
    """最初の「引き継ぎ元」画面の確定。base_sid は再開判定用に残す。"""
    if d.get("status") == "created":
        raise WizardError("作成済みの下書きは変更できません", code="created", status=409)
    if d.get("slots") or d.get("questions") or d.get("compose"):
        raise WizardError("設定を入力した後は引き継ぎ元を変更できません",
                          code="base_locked", status=409)

    base_sid = str(base_sid or "")
    src = {}
    if base_sid:
        try:
            src = (load(base_sid, readonly=True) or {}).get("setting") or {}
        except Exception:
            src = {}

    if inherit:
        if not base_sid:
            raise WizardError("引き継ぎ元を選んでください", code="bad_base")
        if not src:
            raise WizardError("引き継ぎ元のチャンネルが見つかりません", code="bad_base")
        d["base_setting"] = copy.deepcopy(src)
        d["inherit_base"] = True
    else:
        d["base_setting"] = {}
        d["inherit_base"] = False

    if base_sid:
        d["base_sid"] = base_sid
    d["base_title"] = str(src.get("title") or d.get("base_title") or "")
    d["step"] = "idea"
    d["status"] = "interview"
    d["done_questions"] = False
    d["compose_stale"] = True
    return d


def wizard_model_from_app():
    app = app_settings()
    return {
        "provider": str(app.get("wizard_provider") or "google").strip().lower(),
        "model": str(app.get("wizard_model") or "").strip(),
        "thinking_level": str(app.get("wizard_thinking_level") or "high").strip().lower(),
        "ai_enabled": True,
    }


def wizard_apply_slots(d, slots):
    if not isinstance(slots, dict):
        raise WizardError("slotsが不正です", code="bad_request")
    for key in APPLY_ORDER:
        if key not in slots:
            continue
        item = slots[key]
        if isinstance(item, dict):
            val = item.get("value")
            src = str(item.get("source") or "user")
        else:
            val = item
            src = "user"
        if src not in ("user", "auto", "derived"):
            src = "user"
        if src == "auto":
            wslot_set_auto(d, key)
        else:
            wslot_set(d, key, val, src)
    for key, item in slots.items():
        if key in APPLY_ORDER or key not in WIZARD_SLOTS:
            continue
        val = item.get("value") if isinstance(item, dict) else item
        wslot_set(d, key, val, "user")
    if any(k in slots for k in ("partner_type", "partner_name", "work")):
        d["partner_preset_id"] = ""
        d["partner_preset_off"] = False
    d["compose_stale"] = True


def wizard_apply_tags(d, body):
    ids = [str(x) for x in (body.get("selected_tags") or [])]
    d["selected_tags"] = [x for x in dict.fromkeys(ids) if wizard_tag_by_id(x)]
    presets = {x["id"]: x for x in wizard_tags().get("ng_presets", [])}
    ngs = [str(x) for x in (body.get("selected_ng") or [])]
    ngs = [x for x in dict.fromkeys(ngs) if x in presets]
    if "ng_minor" not in ngs:
        ngs.insert(0, "ng_minor")
    d["selected_ng"] = ngs
    custom = body.get("custom_ng") or []
    if isinstance(custom, str):
        custom = re.split(r"[,、\n]", custom)
    d["custom_ng"] = [wizard_plain_text(x, 30) for x in (custom or []) if wizard_plain_text(x, 30)][:10]
    ng_set = wizard_ng_conflicts(d)
    d["selected_tags"] = [
        x for x in d["selected_tags"]
        if not wizard_tag_hits_ng(wizard_tag_by_id(x), ng_set)
    ]
    wizard_recompute_derived(d)
    d["compose_stale"] = True


def wizard_apply_answer(d, body):
    if d.get("status") == "created":
        raise WizardError("作成済みの下書きは変更できません", code="created", status=409)
    if wizard_job_running(d.get("last_job")):
        raise WizardError("生成中です。完了してから回答してください", code="wizard_busy", status=409)

    qid = str(body.get("question_id") or "")
    auto = bool(body.get("auto"))
    custom = wizard_plain_text(body.get("custom_text"), 300)
    step = str(body.get("step") or d.get("step") or "idea")
    if step in ("base", "idea", "partner", "tags", "questions", "preview", "creating"):
        d["step"] = step

    fixed_slot = {
        "fixed_idea": "rough_idea",
        "fixed_partner_type": "partner_type",
        "fixed_work": "work",
        "fixed_partner_name": "partner_name",
        "fixed_player": "player_type",
        "fixed_player_work": "player_work",
        "fixed_player_name": "player_name",
        "fixed_player_profile": "player_profile",
        "fixed_start_point": "start_point",
        "fixed_canon_fidelity": "canon_fidelity",
        "fixed_goal": "goal",
        "fixed_ending": "ending",
        "fixed_project": "project",
    }
    if qid in fixed_slot:
        key = fixed_slot[qid]
        if auto:
            wslot_set_auto(d, key)
        elif key in ("rough_idea", "project", "work", "partner_name",
                     "player_work", "player_name", "player_profile",
                     "start_point", "goal"):
            wslot_set(d, key, custom, "user")
        else:
            opts = body.get("option_ids") or []
            val = str(opts[0]) if opts else custom
            spec = WIZARD_SLOTS[key]
            if spec.get("type") == "enum" and val not in (spec.get("values") or []):
                raise WizardError("選択肢が不正です", code="bad_answer")
            wslot_set(d, key, val, "user")
        if key in ("partner_type", "partner_name", "work"):
            d["partner_preset_id"] = ""
            d["partner_preset_off"] = False
        return

    if qid == "fixed_tags":
        wizard_apply_tags(d, body)
        return
    if qid == "fixed_ng":
        wizard_apply_tags(d, {
            "selected_tags": d.get("selected_tags") or [],
            "selected_ng": body.get("option_ids") or [],
            "custom_ng": body.get("custom_text") or "",
        })
        return
    if qid == "fixed_preset":
        pid = str((body.get("option_ids") or [""])[0] or "")
        d["partner_preset_id"] = pid
        d["partner_preset_off"] = not bool(pid)
        preset = wizard_partner_preset({**d, "partner_preset_id": pid, "partner_preset_off": d["partner_preset_off"]})
        if preset and not wslot_value(d, "partner_name"):
            wslot_set(d, "partner_name", preset.get("name") or "", "user")
        d["compose_stale"] = True
        return

    q = next((x for x in (d.get("questions") or []) if x.get("id") == qid), None)
    if not q:
        raise WizardError("質問が見つかりません。再読み込みしてください", code="bad_question")
    slot = q.get("slot")
    if slot not in WIZARD_SLOTS:
        raise WizardError("質問のスロットが不正です", code="bad_question")

    if auto:
        wslot_set_auto(d, slot)
    else:
        # 自由入力が非空なら、選択肢より優先する
        if custom:
            opt = {"id": "free", "label": custom[:20],
                   "effects": {slot: custom[:120]}, "invalidates": []}
        else:
            opt = next((o for o in (q.get("options") or [])
                        if o.get("id") in (body.get("option_ids") or [])), None)
        if not opt:
            raise WizardError("選択肢を選ぶか、自由入力してください", code="bad_answer")
        effects = dict(opt.get("effects") or {})
        if slot not in effects:
            effects[slot] = [opt.get("label")] if WIZARD_SLOTS[slot].get("type") == "multi" else opt.get("label")
        for key in APPLY_ORDER:
            if key in effects:
                wslot_set(d, key, effects[key], "answer")
        for key in (opt.get("invalidates") or []):
            if key in WIZARD_SLOTS and wslot_source(d, key) != "locked":
                d["slots"].pop(key, None)

    if slot not in (d.get("asked_slots") or []):
        d.setdefault("asked_slots", []).append(slot)
    if qid not in (d.get("asked_question_ids") or []):
        d.setdefault("asked_question_ids", []).append(qid)
    d["questions"] = [x for x in (d.get("questions") or []) if x.get("id") != qid]
    if not d["questions"] and _as_int(d.get("round"), 0) >= WIZARD_MAX_ROUNDS:
        d["done_questions"] = True
    d["compose_stale"] = True


def wizard_apply_preview(d, setting):
    if not isinstance(setting, dict):
        raise WizardError("プレビュー内容が不正です", code="bad_request")
    comp = d.get("compose") or {}
    cur = comp.get("setting")
    if not isinstance(cur, dict):
        raise WizardError("設定がまだ生成されていません", code="wizard_not_composed")
    for k in ("title", "summary", "scenario", "first_message", "instructions"):
        if k in setting:
            cur[k] = wizard_plain_text(setting.get(k), {"title": 40, "summary": 300}.get(k, 2000))
    if isinstance(setting.get("character"), dict):
        ch = dict(cur.get("character") or {})
        for k, lim in (("name", 40), ("age", 10), ("profile", 600), ("speech", 400)):
            if k in setting["character"]:
                ch[k] = wizard_plain_text(setting["character"].get(k), lim)
        cur["character"] = ch
    if isinstance(setting.get("chars"), list):
        chars = []
        for x in setting["chars"][:3]:
            if not isinstance(x, dict):
                continue
            chars.append({
                "id": new_id(),
                "name": wizard_plain_text(x.get("name"), 40),
                "tags": wizard_clean_tags(x.get("tags")),
                "source": "user",
                "locked": bool(x.get("locked")),
            })
        if chars:
            cur["chars"] = chars
    if "project" in setting:
        cur["project"] = wizard_plain_text(setting.get("project"), 40)
    if "user_name" in setting:
        cur["user_name"] = wizard_plain_text(setting.get("user_name"), 40)
    miss = wizard_validate_compose(cur)
    if miss:
        raise WizardError("必須項目が不足しています: " + ", ".join(miss), code="bad_compose")
    comp["setting"] = cur
    d["compose"] = comp
    d["compose_stale"] = False
```

### 10.10 作成

```python
def wizard_create(d, overrides, use_template=False):
    """同じ下書きを二重に作成しないよう直列化し、最新の下書きで判定する。"""
    with wizard_create_lock:
        fresh = wizard_load(d.get("id")) or d
        if _as_int(fresh.get("rev"), 0) != _as_int(d.get("rev"), -1):
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(fresh, True)
            raise e
        return _wizard_create_locked(fresh, overrides, use_template)


def _wizard_create_locked(d, overrides, use_template=False):
    if d.get("created_sid"):
        try:
            if os.path.exists(spath(d["created_sid"])):
                s0 = load(d["created_sid"])
                mid0 = wizard_opening_message_id(s0)
                return {"ok": True, "sid": d["created_sid"],
                        "title": (d.get("compose") or {}).get("setting", {}).get("title", ""),
                        "draft": wizard_public(d, True), "created": True,
                        "opening_image": bool(mid0), "opening_image_mid": mid0}
        except Exception:
            pass
        d["created_sid"] = ""

    comp = d.get("compose") or {}
    if not isinstance(comp.get("setting"), dict):
        if not use_template:
            raise WizardError("設定がまだ生成されていません", code="wizard_not_composed")
        setting, warnings = wizard_template_compose(d)
        d["compose"] = {
            "setting": setting,
            "source": "template",
            "warnings": warnings,
            "auto_slots": [],
            "title_alternatives": [],
        }
        d["compose_stale"] = False
        d["status"] = "ready"
        d["warnings"] = list(warnings)
        wizard_save(d)

    comp = d.get("compose") or {}
    comp_setting = comp.get("setting") if isinstance(comp.get("setting"), dict) else {}
    opening_tags = wizard_clean_scene_tags(comp_setting.get("opening_image"))
    if not opening_tags:
        opening_tags = wizard_opening_tags(d)

    final, warnings = wizard_build_final_setting(d, overrides)
    s = create_session(final)
    sid = s.get("id")
    if not sid:
        raise WizardError("チャンネル作成に失敗しました", code="create_failed", status=500)
    with wizard_lock:
        d = wizard_load(d["id"]) or d
        d["status"] = "created"
        d["created_sid"] = sid
        d["step"] = "preview"
        d["warnings"] = list(warnings)
        wizard_save(d)
        pub = wizard_public(d, True)

    # 案A: 画像の完成は待たない。最初のassistant発言にqueuedで積むだけ。
    opening_mid = ""
    try:
        opening_mid = wizard_enqueue_opening_image(s, final, opening_tags)
    except Exception:
        warnings.append("開幕画像の生成を開始できませんでした（チャンネルは作成済みです）。")

    return {"ok": True, "sid": sid, "title": (s.get("setting") or {}).get("title", ""),
            "draft": pub, "warnings": warnings,
            "opening_image": bool(opening_mid), "opening_image_mid": opening_mid}
```

### 10.11 モデル一覧・接続テスト

```python
def wizard_model_tier(label):
    base = re.sub(r"\s*[\(（].*?[\)）]\s*$", "", str(label or "")).lower()
    for k in ("flash", "mini", "haiku", "lite", "fast", "nano"):
        if k in base:
            return "fast"
    for k in ("pro", "ultra", "opus", "sonnet", "gpt-5", "gpt-4.1", "gemini-3", "gemini-2.5-pro"):
        if k in base:
            return "high"
    return "standard"


def wizard_models_payload(provider):
    provider = str(provider or "google").strip().lower()
    if provider not in ("google", "vertex", "antigravity"):
        raise WizardError("プロバイダーが不正です", code="bad_request")

    now_t = time.time()
    with wizard_lock:
        c = wizard_model_cache.get(provider)
        if c and now_t - c[0] < 300 and not (c[1] or {}).get("error"):
            return c[1]

    app = app_settings()
    current = str(app.get("wizard_model") or "").strip()
    rows, err, configured = [], "", True

    try:
        if provider == "antigravity":
            if ide_poke is None:
                raise RuntimeError("Antigravityが利用できません")
            res, why = ide_poke.models()          # ← タプル
            if not res:
                configured = False
                err = why or "Antigravityのモデル一覧を読めませんでした"
            else:
                default = str(res.get("default") or "")
                for m in (res.get("models") or []):
                    if not isinstance(m, dict):
                        continue
                    mid = str(m.get("model") or m.get("id") or "")
                    if not mid:
                        continue
                    label = str(m.get("label") or mid)
                    left = m.get("left")
                    rows.append({
                        "id": mid, "label": label,
                        "tier": wizard_model_tier(label),
                        "configured": bool(current and current == mid),
                        "available": left is None or _as_int(left, 1) > 0,
                        "left": left,
                    })
                if default and not any(x["id"] == default for x in rows):
                    rows.insert(0, {
                        "id": default, "label": default,
                        "tier": wizard_model_tier(default),
                        "configured": current == default,
                        "available": True, "left": None,
                    })
        else:
            if provider == "google":
                try:
                    # load_key() は (key, source) を返すため、bool(tuple) では常に True になる。
                    configured = bool(google_ai.load_key()[0])
                except Exception:
                    configured = False
            else:
                try:
                    configured = bool(google_ai.load_vertex_config().get("configured"))
                except Exception:
                    configured = False

            if not configured:
                err = "認証情報が未設定です"
            else:
                raw = google_ai.list_models(provider=provider, probe=False) or []
                for m in raw:
                    if isinstance(m, str):
                        mid, label = m, m
                    elif isinstance(m, dict):
                        mid = str(m.get("id") or m.get("name") or m.get("model") or "")
                        label = str(m.get("label") or m.get("display_name") or m.get("displayName") or mid)
                    else:
                        continue
                    mid = mid.replace("models/", "")
                    if not mid:
                        continue
                    rows.append({
                        "id": mid, "label": label,
                        "tier": wizard_model_tier(label),
                        "configured": bool(current and current == mid),
                        "available": bool(m.get("available", True)) if isinstance(m, dict) else True,
                        "left": None,
                    })
    except Exception as e:
        err = str(e)

    rows.sort(key=lambda x: ({"high": 0, "standard": 1, "fast": 2}.get(x.get("tier"), 1),
                             x.get("label", "")))
    payload = {
        "ok": not bool(err) or bool(rows),
        "provider": provider,
        "models": rows,
        "current": current,
        "configured": configured,
        "recommended": "high" if any(x["tier"] == "high" for x in rows) else "standard",
        "error": err,
    }
    with wizard_lock:
        if err:
            wizard_model_cache.pop(provider, None)
        else:
            wizard_model_cache[provider] = (now_t, payload)
    return payload


def wizard_test_model(body):
    provider = str((body or {}).get("provider") or "").strip().lower()
    model = str((body or {}).get("model") or "").strip()
    if provider not in ("google", "vertex", "antigravity"):
        raise WizardError("プロバイダーが不正です", code="bad_request")
    if provider == "antigravity":
        cid = wizard_ensure_builder()
        try:
            ok_idle, _st = ide_poke.idle(cid)
        except Exception:
            ok_idle = False
        if not ok_idle:
            raise WizardError("Antigravityの会話が応答中です。少し待ってから試してください。",
                              code="antigravity_down", retryable=True, status=502)
        return {"ok": True, "note": "Antigravityの会話を確認しました。"}
    text, meta = wizard_generate(
        {"provider": provider, "model": model, "thinking_level": "minimal"},
        'JSONで {"ok": true, "message": "接続OK"} とだけ返してください。',
        system="JSONのみを返します。", max_tokens=128, kind="test",
    )
    raw = wizard_extract_json(text)
    if not isinstance(raw, dict):
        raise WizardError("テスト応答が不正です", code="bad_json", status=502)
    return {"ok": True, "model": meta, "response": raw}
```

### 10.12 開始・ルーティング

```python
def wizard_draft_from_request(body):
    wid = str((body or {}).get("draft_id") or "")
    if not WIZARD_ID_RE.match(wid):
        raise WizardError("下書きIDが不正です", code="bad_request")
    d = wizard_load(wid)
    if not d:
        raise WizardError("下書きが見つかりません", code="not_found", status=404)
    return d


def wizard_start(body):
    force = bool((body or {}).get("force_new"))
    base_sid = str((body or {}).get("base_sid") or "")
    base = None
    if base_sid:
        try:
            base = (load(base_sid, readonly=True) or {}).get("setting")
        except Exception:
            base = None

    if not force:
        for wid, d in wizard_ids_sorted():
            if d.get("base_sid", "") != base_sid:
                continue
            if d.get("status") not in ("interview", "composing", "ready"):
                continue
            if d.get("status") == "composing" and not wizard_job_running(d.get("last_job")):
                has = bool((d.get("compose") or {}).get("setting"))
                d["status"] = "ready" if has else "interview"
                d["step"] = "preview" if has else "tags"
                wizard_save(d, bump=False)
            return {"ok": True, "resumed": True, "draft": wizard_public(d, True)}

    d = wizard_new_draft(base, wizard_model_from_app(), bool(app_settings().get("wizard_confirm", True)))
    d["base_sid"] = base_sid
    d["base_title"] = str((base or {}).get("title") or "")
    d["inherit_base"] = bool(base)
    wizard_save(d)
    wizard_sweep()
    return {"ok": True, "resumed": False, "draft": wizard_public(d, True)}
```

`wizard_route` は `H` クラスより前に置く。

```python
from contextlib import contextmanager


@contextmanager
def wizard_write(body, bump=True, allow_running=False):
    """rev 確認・状態確認・保存を同じロック内で行う（read-modify-write 競合対策）。"""
    with wizard_lock:
        d = wizard_draft_from_request(body)
        if _as_int(body.get("rev"), -1) != _as_int(d.get("rev"), 0):
            e = WizardError("下書きが更新されました", code="rev", status=409)
            e.draft = wizard_public(d, True)
            raise e
        if d.get("status") == "created":
            raise WizardError("作成済みの下書きは変更できません", code="created", status=409)
        if not allow_running and wizard_job_running(d.get("last_job")):
            raise WizardError("生成中です。完了してから操作してください", code="wizard_busy", status=409)
        yield d
        wizard_save(d, bump=bump)


def wizard_route(h, path, body, method):
    try:
        path_only, _, qs = path.partition("?")
        if method == "GET":
            if path_only == "/api/wizard/meta":
                app = app_settings()
                return h.send_json({
                    "ok": True,
                    "enabled": bool(app.get("wizard_enabled", True)),
                    "confirm": bool(app.get("wizard_confirm", True)),
                    "configured": bool(str(app.get("wizard_model") or "").strip()) or str(app.get("wizard_provider") or "") == "antigravity",
                    "current": wizard_model_from_app(),
                    "max_questions": WIZARD_MAX_QUESTIONS,
                    "max_rounds": WIZARD_MAX_ROUNDS,
                    "tags": wizard_tags(),
                    "defaults": {
                        "project": str(app.get("project") or ""),
                        "user_name": "あなた",
                    },
                })
            if path_only == "/api/wizard/models":
                q = parse_qs(qs or "")
                provider = (q.get("provider") or ["google"])[0]
                return h.send_json(wizard_models_payload(provider))
            if path_only == "/api/wizard/list":
                rows = []
                for wid, d in wizard_ids_sorted():
                    rows.append({
                        "id": wid,
                        "title": wizard_public(d).get("title_hint"),
                        "status": d.get("status"),
                        "updated": d.get("updated"),
                        "created_sid": d.get("created_sid") or "",
                    })
                return h.send_json({"ok": True, "drafts": rows})
            m = re.match(r"^/api/wizard/job/(wj_[0-9a-f]{10})$", path_only)
            if m:
                job = wizard_jobs.get(m.group(1))
                if not job:
                    return h.send_json({"ok": False, "error": "ジョブが見つかりません", "code": "not_found"}, 404)
                out = {k: v for k, v in job.items() if k != "cancel"}
                return h.send_json(out)
            m = re.match(r"^/api/wizard/(wiz_[0-9a-f]{10})$", path_only)
            if m:
                d = wizard_load(m.group(1))
                if not d:
                    return h.send_json({"ok": False, "error": "下書きが見つかりません", "code": "not_found"}, 404)
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            return h.send_json({"ok": False, "error": "not found"}, 404)

        if method == "POST":
            if path_only == "/api/wizard/start":
                return h.send_json(wizard_start(body))
            if path_only == "/api/wizard/base":
                with wizard_write(body) as d:
                    wizard_set_base(d, body.get("base_sid"), bool(body.get("inherit")))
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/answer":
                with wizard_write(body) as d:
                    wizard_apply_answer(d, body)
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/slots":
                with wizard_write(body) as d:
                    wizard_apply_slots(d, body.get("slots") or {})
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/tags":
                with wizard_write(body) as d:
                    wizard_apply_tags(d, body)
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/model":
                with wizard_write(body) as d:
                    provider = str(body.get("provider") or "").strip().lower()
                    if provider not in ("google", "vertex", "antigravity", "none"):
                        raise WizardError("プロバイダーが不正です", code="bad_request")
                    d["model"] = {
                        "provider": provider,
                        "model": str(body.get("model") or "").strip(),
                        "thinking_level": str(body.get("thinking_level") or "").strip().lower(),
                        "ai_enabled": bool(body.get("ai_enabled", provider != "none")),
                    }
                    if "confirm" in body:
                        d["confirm"] = bool(body.get("confirm"))
                    d["compose_stale"] = True
                    # 選択したモデルの configured 表示を次の一覧取得で更新する
                    wizard_model_cache.clear()
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/step":
                with wizard_write(body, bump=False, allow_running=True) as d:
                    step = str(body.get("step") or "").strip()
                    if step in ("base", "idea", "partner", "tags", "questions", "preview", "creating"):
                        d["step"] = step
                    if "confirm" in body:
                        d["confirm"] = bool(body.get("confirm"))
                    # step / confirm はUI状態。ジョブ実行中の画面遷移でも 409 wizard_busy にせず、rev も上げない。
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/preview":
                with wizard_write(body) as d:
                    wizard_apply_preview(d, body.get("setting") or {})
                return h.send_json({"ok": True, "draft": wizard_public(d, True)})
            if path_only == "/api/wizard/questions":
                d = wizard_draft_from_request(body)
                job, pub = wizard_start_job(d, "questions", body.get("rev"))
                return h.send_json({"ok": True, "job_id": job["job_id"], "draft": pub})
            if path_only == "/api/wizard/compose":
                d = wizard_draft_from_request(body)
                job, pub = wizard_start_job(d, "compose", body.get("rev"))
                return h.send_json({"ok": True, "job_id": job["job_id"], "draft": pub})
            if path_only == "/api/wizard/job_cancel":
                jid = str(body.get("job_id") or "")
                job = wizard_jobs.get(jid)
                if job:
                    job["cancel"] = True
                return h.send_json({"ok": bool(job)})
            if path_only == "/api/wizard/create":
                d = wizard_draft_from_request(body)
                if _as_int(body.get("rev"), -1) != _as_int(d.get("rev"), 0):
                    return h.send_json({"ok": False, "error": "下書きが更新されました", "code": "rev", "draft": wizard_public(d, True)}, 409)
                return h.send_json(wizard_create(d, body.get("overrides") or {},
                                                 bool(body.get("use_template"))))
            if path_only == "/api/wizard/delete":
                d = wizard_draft_from_request(body)
                if d.get("last_job"):
                    job = wizard_jobs.get(d["last_job"])
                    if job:
                        job["cancel"] = True
                try:
                    os.remove(wizard_path(d["id"]))
                except OSError:
                    pass
                return h.send_json({"ok": True})
            if path_only == "/api/wizard/test":
                return h.send_json(wizard_test_model(body))
            return h.send_json({"ok": False, "error": "not found"}, 404)
        return h.send_json({"ok": False, "error": "method not allowed"}, 405)
    except WizardError as e:
        payload = {"ok": False, "error": str(e), "code": e.code, "retryable": e.retryable}
        if getattr(e, "draft", None):
            payload["draft"] = e.draft
        return h.send_json(payload, e.status)
    except ValueError as e:
        return h.send_json({"ok": False, "error": str(e), "code": "bad_request"}, 400)
    except Exception as e:
        return h.send_json({"ok": False, "error": str(e), "code": "wizard_error", "retryable": True}, 500)
```

### 10.13 既存コードへの差分

#### `DEFAULT_APP`

```python
DEFAULT_APP = {
    # ... 既存 ...
    "wizard_enabled": True,
    "wizard_provider": "google",
    "wizard_model": "",
    "wizard_thinking_level": "high",
    "wizard_confirm": True,
    "wizard_builder_ide": "",
}
```

#### `/api/app-settings`

既存の `server.py`（10876-10887行付近）では、`with lock:` が `/api/app-settings` だけでなく `/api/file-presets` などの複数のエンドポイントをまとめて囲っている。
デッドロック防止（`wizard_ensure_builder` での `wizard_lock` 解放後 `lock` 取得との逆順ネスト完全排除）のため、`/api/app-settings` を外側の `with lock:` の手前に独立して配置し、`with lock:` のスコープを `APP_SETTINGS` の読み書きのみに限定する。
そして `lock` を完全に解放した直後に `with wizard_lock:` で `wizard_model_cache.clear()` を呼ぶ。
**移動後、元の外側 `with lock:` 内にある `/api/app-settings` 分岐は必ず削除する**（残すと到達しない重複分岐になる）。

```python
        # /api/app-settings の処理（server.py 10876行の with lock: の直前に配置）:
        if p == "/api/app-settings":
            with lock:
                cur = read_json(APP_SETTINGS, {})
                for k in DEFAULT_APP:
                    if isinstance(d.get(k), str) and isinstance(DEFAULT_APP[k], str):
                        cur[k] = d[k].strip()
                    elif isinstance(d.get(k), bool) and isinstance(DEFAULT_APP[k], bool):
                        cur[k] = d[k]
                    elif isinstance(d.get(k), list) and isinstance(DEFAULT_APP[k], list):
                        cur[k] = list(dict.fromkeys(x.strip() for x in str_list(d[k])))
                cur["wizard_enabled"] = bool(cur.get("wizard_enabled", True))
                cur["wizard_confirm"] = bool(cur.get("wizard_confirm", True))
                cur["wizard_model"] = str(cur.get("wizard_model") or "").strip()
                if cur.get("wizard_provider") not in ("google", "vertex", "antigravity"):
                    cur["wizard_provider"] = "google"
                if cur.get("wizard_thinking_level") not in ("minimal", "low", "medium", "high", "xhigh"):
                    cur["wizard_thinking_level"] = "high"
                write_json(APP_SETTINGS, cur)

            # lock 解放後に実行（デッドロック防止: 逆順ネストの完全排除）
            with wizard_lock:
                wizard_model_cache.clear()
            return self.send_json({"ok": True, "app": app_settings()})
```

#### `do_GET` / `do_POST`

`server.py` では `do_GET` / `do_POST` 冒頭で `u = urlparse(self.path)` により `p = u.path`（クエリ文字列無しのパス）が既に取得されている。
また、`do_POST` では `try: d = self.body()` が既にパースされているため、`self.body()` を二重に呼ぶとソケット読み込みが失敗する。パース済みの `p` と `d` をそのまま利用し、ルーティングを行う。

```python
# do_GET: self.blocked(u) による拒否判定（9577-9578行付近）の直後
if p.startswith("/api/wizard/"):
    return wizard_route(self, p, None, "GET")

# do_POST: self.body() のパースブロック（10434-10437行付近）の直後
# （※d = self.body() を再実行してはならない。すでにパースされた d を渡す）
if p.startswith("/api/wizard/"):
    return wizard_route(self, p, d, "POST")
```

## 11. フロントエンド実装

### 11.1 `public/wizard.js`（新規）

`app.js` の後に読み込む。`api` / `esc` / `sid` / `meta` / `checked` は `app.js` のグローバルを使う。**`let meta` は宣言しない**（アプリの `meta` を隠さないため）。

```javascript
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
    const m = $(".wizard-modal", root());
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
    $("#wizardTitle").textContent = titleFor(screen);
    $("#wizardProgress").textContent = progressFor(screen);
    $("#wizardBack").classList.toggle("hidden", screen === "base" || screen === "model" || screen === "creating");
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

  function setBody(html) { body().innerHTML = html; }
  function setFoot(html) { foot().innerHTML = html || ""; }

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

  async function ensureDraft() {
    const r = await apiCall("/api/wizard/start", { base_sid: currentSid() });
    if (!r || r.ok === false || r.error) throw new Error((r && r.error) || "下書きを作成できません");
    if (r.resumed && r.draft) {
      const resume = window.confirm("前回の続きから再開しますか？\n（キャンセルで新しく始めます）");
      if (!resume) {
        const n = await apiCall("/api/wizard/start", { base_sid: currentSid(), force_new: true });
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
    if (step === "questions" && !draft.done_questions) return "questions";
    if (step === "creating") return "creating";
    return "idea";
  }

  function show(name) {
    stopPoll();
    screen = name;
    updateChrome();
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

  async function open() {
    if (busy) return;
    autoCreateOnce = false;
    busy = true;
    try {
      wmeta = await apiCall("/api/wizard/meta");
      if (!wmeta || wmeta.ok === false || wmeta.error) throw new Error((wmeta && wmeta.error) || "ウィザードを開けません");
      if (!wmeta.enabled) throw new Error("ウィザードは無効です");
      root().classList.remove("hidden");
      root().setAttribute("aria-hidden", "false");
      document.body.classList.add("wizard-open");
      if (typeof updateFabState === "function") updateFabState();
      trapFocus();
      try {
        await ensureDraft();
      } catch (e) {
        // trapFocus() の後に失敗した場合、フォーカスを戻さないと背面が操作できなくなる
        stopPoll();
        root().classList.add("hidden");
        root().setAttribute("aria-hidden", "true");
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
    root().classList.add("hidden");
    root().setAttribute("aria-hidden", "true");
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
      show(draft.compose ? "preview" : "tags");
    }
  }

  async function saveStep(step, extra = {}) {
    const r = await apiCall("/api/wizard/step", { draft_id: draft.id, rev: draft.rev, step, ...extra });
    if (!r || r.error) throw new Error(r.error || "保存できません");
    draft = r.draft;
  }

  async function saveSlots(slots, step) {
    const r = await apiCall("/api/wizard/slots", { draft_id: draft.id, rev: draft.rev, slots, step });
    if (!r || r.error) throw new Error(r.error || "保存できません");
    draft = r.draft;
  }

  async function saveTagsToServer() {
    if (!pendingTags) return;
    const r = await apiCall("/api/wizard/tags", {
      draft_id: draft.id, rev: draft.rev,
      selected_tags: pendingTags.tags,
      selected_ng: pendingTags.ng,
      custom_ng: pendingTags.custom,
    });
    if (!r || r.error) throw new Error(r.error || "タグを保存できません");
    draft = r.draft;
    pendingTags = null;
  }

  function scheduleTagSave() {
    if (tagTimer) clearTimeout(tagTimer);
    tagTimer = setTimeout(() => saveTagsToServer().catch(e => say(e.message)), 500);
  }

  // ------------------------------------------------------------ base
  async function renderBase() {
    const locked = !!(Object.keys(draft.slots || {}).length ||
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
    show(((draft.model && draft.model.model) || wmeta.configured) ? "idea" : "model");
  }

  // ------------------------------------------------------------ model
  async function renderModel() {
    const cur = (draft && draft.model && draft.model.model) ? draft.model : (wmeta.current || {});
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
      $("#wizThinkWrap").classList.toggle("hidden", p === "antigravity");
      const msg = $("#wizModelMsg");
      if (msg) msg.textContent = (r && r.error) ? r.error : "";
    };
    $("#wizProvider").onchange = load;
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
    wmeta.current = skipAi
      ? { provider: "none", model: "", thinking_level: "" }
      : { provider, model, thinking_level: thinking };
    wmeta.configured = !skipAi && (provider === "antigravity" || !!model);
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
    const idea = (draft.slots && draft.slots.rough_idea && draft.slots.rough_idea.value) || "";
    const examples = (wmeta.tags && wmeta.tags.examples) || [];
    setBody(`
      <div class="wizard-card">
        <h3>やりたいことを一言で</h3>
        <textarea id="wizIdea" class="wizard-textarea" rows="4" maxlength="300"
          placeholder="例: 放課後の教室で、幼馴染と二人きり。">${E(idea)}</textarea>
        <div class="wizard-examples">${examples.map(x => `<button type="button" class="wizard-example" data-example="${E(x)}">${E(x)}</button>`).join("")}</div>
        <label class="wizard-check"><input type="checkbox" id="wizConfirm" ${draft.confirm ? "checked" : ""}> 作成前にプレビューを確認する</label>
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
    const s = (k) => (draft.slots && draft.slots[k] && draft.slots[k].value) || "";
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
            ${presets.map(p => `<option value="${E(p.id || p.name || "")}" ${String(draft.partner_preset_id || "") === String(p.id || "") ? "selected" : ""}>${E(p.name || p.id || "")}</option>`).join("")}
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
          <input id="wizPlayerName" class="wizard-input" maxlength="40" value="${E(s("player_name"))}" placeholder="${E(draft.defaults?.user_name || "例: あなた")}">
          <small class="wizard-note">空欄なら元のチャンネルの呼び名「${E(draft.defaults?.user_name || "あなた")}」を引き継ぎます。</small>
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
          <input id="wizProject" class="wizard-input" maxlength="40" value="${E(s("project") || draft.defaults?.project || "")}">
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
    const tags = (wmeta.tags && wmeta.tags.tags) || [];
    const axes = (wmeta.tags && wmeta.tags.axes) || {};
    const selected = new Set(draft.selected_tags || []);
    const ng = new Set(draft.selected_ng || []);
    pendingTags = pendingTags || {
      tags: Array.from(selected),
      ng: Array.from(ng),
      custom: (draft.custom_ng || []).join("、"),
    };
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
        <div class="wizard-chips">${(wmeta.tags.ng_presets || []).map(n =>
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
    const tag = ((wmeta.tags && wmeta.tags.tags) || []).find(x => x.id === id);
    if (!tag) return;
    const max = ((wmeta.tags.axes || {})[tag.axis] || {}).max || 4;
    const same = pendingTags.tags.filter(x => {
      const t = ((wmeta.tags.tags) || []).find(y => y.id === x);
      return t && t.axis === tag.axis;
    });
    if (!pendingTags.tags.includes(id) && same.length >= max) {
      say(`${tag.axis}は最大${max}個です`); return;
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
      tags: draft.selected_tags || [],
      ng: draft.selected_ng || [],
      custom,
    };
    pendingTags.custom = custom;
    await saveTagsToServer();
    if (next === "finish") return startCreateChain(false);
    if (draft.ready && !draft.done_questions) {
      const ask = window.confirm("情報が揃いました。追加の質問をしますか？\n（キャンセルでこのまま作成）");
      if (!ask) return startCreateChain(false);
    }
    startCreateChain(true);
  }

  // ------------------------------------------------------------ questions
  function renderQuestions() {
    const qs = (draft.questions || []).filter(q => !(draft.asked_question_ids || []).includes(q.id));
    if (!qs.length) {
      if (draft.done_questions) return renderQuestionsDone();
      if (!jobId && draft.last_job) {
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
    foot().dataset.picked = "";
    setBody(`
      <div class="wizard-card">
        <p class="wizard-why">${E(q.why || "")}</p>
        <h3>${E(q.text)}</h3>
        <div class="wizard-options">
          ${q.options.map(o => `<button type="button" class="wizard-option" data-opt="${E(o.id)}">
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
        <p class="muted">回答済み: ${(draft.asked_question_ids || []).length} / ${wmeta.max_questions}</p>
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
    const qs = (draft.questions || []).filter(q => !(draft.asked_question_ids || []).includes(q.id));
    const q = qs[0];
    if (!q) return;
    const free = val("#wizFree");
    const picked = free ? "" : (foot().dataset.picked || "");
    const r = await apiCall("/api/wizard/answer", {
      draft_id: draft.id, rev: draft.rev, question_id: q.id,
      option_ids: picked ? [picked] : [], custom_text: free,
      auto, step: "questions",
    });
    if (!r || r.error) throw new Error(r.error || "回答を保存できません");
    draft = r.draft;
    if ((draft.questions || []).filter(x => !(draft.asked_question_ids || []).includes(x.id)).length) {
      renderQuestions();
    } else if (draft.done_questions || draft.round >= wmeta.max_rounds) {
      renderQuestionsDone();
    } else {
      const more = window.confirm("追加で2問ほど聞きますか？\n（キャンセルでこのまま作成）");
      if (more) startJob("questions");
      else startCreateChain(false);
    }
  }

  // ------------------------------------------------------------ preview
  function renderPreview() {
    const c = (draft.compose && draft.compose.setting) || {};
    const ch = c.character || {};
    const warn = (draft.warnings || []).concat((draft.compose && draft.compose.warnings) || []);
    const imgMode = (draft.base_info && draft.base_info.image_mode) || "always";
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
          <input id="pvUser" class="wizard-input" maxlength="40" value="${E(c.user_name || draft.defaults?.user_name || "あなた")}">
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
          <input id="pvProject" class="wizard-input" maxlength="40" value="${E(c.project || draft.defaults?.project || "")}">
        </label>
        <details class="wizard-details">
          <summary>本文AIへの指示</summary>
          <textarea id="pvInstructions" class="wizard-textarea" rows="5" maxlength="2000">${E(c.instructions)}</textarea>
        </details>
        <div class="wizard-preview-meta">
          <span>画像タグ: ${E(tagsText(((c.chars || [])[0]) || {})) || "なし"}</span>
          <span>開幕画像: ${imgMode === "off" ? "生成しない" : "作成後に非同期生成"}</span>
          <span>自動補完: ${E(((draft.compose && draft.compose.auto_slots) || []).map(k => draft.slots?.[k]?.label || k).join("、") || "なし")}</span>
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
    if (!jobId && draft.last_job) {
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
        <p>${E(draft.title_hint || "")}</p>
      </div>`);
    setFoot(`<button type="button" class="wizard-btn primary" data-act="open-created">開く</button>`);
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
      else renderQuestionsLoading();
      pollJob();
    } catch (e) {
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
          if ((draft.questions || []).length) renderQuestions();
          else renderQuestionsDone();
        } else {
          if (autoCreateOnce) { autoCreateOnce = false; doCreate(); }
          else if (draft.confirm) show("preview");
          else doCreate();
        }
        return;
      }
      if (j.status === "error") {
        jobId = "";
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
    jobKind = (draft.compose && draft.compose.setting) ? "compose" : "questions";
    const j = await apiCall(`/api/wizard/job/${jid}`).catch(() => null);
    if (!j || !j.status) {
      jobId = "";
      return;
    }
    jobKind = j.kind || jobKind;
    if (j.status === "done") {
      jobId = "";
      if (j.result && j.result.draft) draft = j.result.draft;
      if ((j.kind || jobKind) === "compose" && (autoCreateOnce || !draft.confirm)) {
        autoCreateOnce = false;
        return doCreate();
      }
      show(entryScreen());
      return;
    }
    if (j.status === "error") {
      jobId = "";
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
    if (!draft.model || draft.model.provider === "none" || draft.model.ai_enabled === false) {
      return doCreate({}, true);
    }
    if (draft.compose && !draft.compose_stale) {
      if (autoCreateOnce) { autoCreateOnce = false; return doCreate(); }
      return draft.confirm ? show("preview") : doCreate();
    }
    if (withQuestions && !draft.done_questions && draft.round < wmeta.max_rounds) {
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
      foot().dataset.picked = opt.dataset.opt;
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
          return show(draft.compose ? "preview" : "tags");
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
    $("#wizardClose").onclick = () => close(false);
    $("#wizardBack").onclick = goBack;
    $("#wizardModelChip").onclick = () => {
      if (busy || jobId || screen === "creating" || screen === "done") return;
      show("model");
    };

    // body と foot の両方を覆う
    $(".wizard-modal", root()).addEventListener("click", onBodyClick);

    $("#wizardBody").addEventListener("input", (e) => {
      if (e.target && e.target.id === "wizFree") {
        foot().dataset.picked = "";
        $$(".wizard-option").forEach(x => x.classList.remove("on"));
      }
    });

    $("#wizardRoot").addEventListener("click", (e) => {
      if (e.target.closest(".wizard-backdrop")) close(false);
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !root().classList.contains("hidden")) {
        e.preventDefault();
        e.stopPropagation();
        close(false);
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

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.RPWizard = { open, close, showModel: () => show("model") };
})();
```

### 11.2 `public/wizard.css`（新規）

```css
.wizard-root { position: fixed; inset: 0; z-index: 1200; display: flex; align-items: center; justify-content: center; }
.wizard-root.hidden { display: none; }
body.wizard-open { overflow: hidden; }
body.wizard-open #toast { z-index: 1300; }  /* ウィザード表示中もトーストを前面に */
.wizard-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.55); backdrop-filter: blur(2px); }
.wizard-modal {
  position: relative; width: min(680px, calc(100vw - 32px)); max-height: min(86vh, 820px);
  display: flex; flex-direction: column; background: var(--bg, #1e1f22); color: var(--fg, #e8eaed);
  border: 1px solid var(--line, #3a3d42); border-radius: 18px; box-shadow: 0 20px 60px rgba(0,0,0,.45);
  overflow: hidden;
}
.wizard-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line, #3a3d42); }
.wizard-head h2 { font-size: 16px; margin: 0; }
.wizard-head .wizard-progress { font-size: 11px; color: var(--muted, #9aa0a6); }
.wizard-head button { background: transparent; color: inherit; border: 0; font-size: 18px; cursor: pointer; padding: 6px 8px; border-radius: 8px; }
.wizard-model-chip:disabled { opacity: .45; cursor: default; }
.wizard-head button:hover { background: rgba(255,255,255,.08); }
.wizard-model-chip { margin-left: auto; font-size: 11px; border: 1px solid var(--line, #3a3d42); padding: 4px 8px; border-radius: 999px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wizard-model-chip.warn { color: #f5c26b; border-color: #7a5a1e; }
.wizard-body { padding: 16px; overflow: auto; flex: 1; }
.wizard-foot { display: flex; gap: 8px; justify-content: flex-end; padding: 12px 14px; border-top: 1px solid var(--line, #3a3d42); flex-wrap: wrap; }
.wizard-card { display: flex; flex-direction: column; gap: 10px; }
.wizard-card h3 { margin: 4px 0; font-size: 15px; }
.wizard-card.center { text-align: center; align-items: center; padding: 24px 0; }
.wizard-label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted, #9aa0a6); }
.wizard-input, .wizard-textarea {
  width: 100%; box-sizing: border-box; background: var(--input-bg, #2a2c30); color: inherit;
  border: 1px solid var(--line, #3a3d42); border-radius: 10px; padding: 9px 10px; font: inherit;
}
.wizard-textarea { resize: vertical; line-height: 1.55; }
.wizard-input:focus, .wizard-textarea:focus { outline: 2px solid var(--accent, #8ab4f8); outline-offset: 1px; }
.wizard-btn {
  background: var(--btn, #2a2c30); color: inherit; border: 1px solid var(--line, #3a3d42);
  border-radius: 10px; padding: 9px 14px; font: inherit; cursor: pointer;
}
.wizard-btn.primary { background: var(--accent, #8ab4f8); color: #101114; border-color: transparent; font-weight: 700; }
.wizard-btn.ghost { background: transparent; }
.wizard-btn:disabled { opacity: .5; cursor: default; }
.wizard-note { font-size: 11px; color: var(--muted, #9aa0a6); line-height: 1.5; }
.wizard-lead { font-size: 14px; line-height: 1.6; margin: 0 0 10px; }
.wizard-inherit { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0 10px; }
.wizard-inherit th, .wizard-inherit td { border-top: 1px solid var(--line, #333); padding: 7px 4px; text-align: left; vertical-align: top; }
.wizard-inherit th { width: 64px; color: var(--muted, #9aa0a6); font-weight: 600; white-space: nowrap; }
.wizard-check { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted, #9aa0a6); }
.wizard-examples { display: flex; flex-wrap: wrap; gap: 6px; }
.wizard-example { font-size: 11px; background: transparent; color: inherit; border: 1px dashed var(--line, #3a3d42); border-radius: 999px; padding: 4px 8px; cursor: pointer; }
.wizard-seg { display: flex; gap: 6px; }
.wizard-seg-btn { flex: 1; padding: 8px; border-radius: 10px; border: 1px solid var(--line, #3a3d42); background: transparent; color: inherit; cursor: pointer; }
.wizard-seg-btn.on { background: var(--accent, #8ab4f8); color: #101114; border-color: transparent; font-weight: 700; }
.wizard-group { display: flex; flex-direction: column; gap: 6px; }
.wizard-group-head { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted, #9aa0a6); }
.wizard-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.wizard-chip { font-size: 12px; padding: 6px 10px; border-radius: 999px; border: 1px solid var(--line, #3a3d42); background: transparent; color: inherit; cursor: pointer; }
.wizard-chip.on { background: var(--accent, #8ab4f8); color: #101114; border-color: transparent; font-weight: 700; }
.wizard-chip:disabled { opacity: .45; cursor: default; }
.wizard-options { display: flex; flex-direction: column; gap: 8px; }
.wizard-option { text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 12px; border: 1px solid var(--line, #3a3d42); background: transparent; color: inherit; cursor: pointer; }
.wizard-option.on { border-color: var(--accent, #8ab4f8); box-shadow: 0 0 0 1px var(--accent, #8ab4f8) inset; }
.wizard-option span { font-size: 11px; color: var(--muted, #9aa0a6); }
.wizard-why { font-size: 11px; color: var(--muted, #9aa0a6); margin: 0; }
.wizard-grid2 { display: grid; grid-template-columns: 1fr 100px; gap: 8px; }
.wizard-warn { border: 1px solid #7a5a1e; background: rgba(245,194,107,.08); color: #f5c26b; border-radius: 10px; padding: 8px 10px; font-size: 11px; line-height: 1.5; }
.wizard-err { border: 1px solid #7a2e2e; background: rgba(255,120,120,.08); color: #ff9d9d; border-radius: 10px; padding: 10px; font-size: 12px; }
.wizard-msg { font-size: 11px; color: var(--muted, #9aa0a6); min-height: 1em; }
.wizard-details { border: 1px solid var(--line, #3a3d42); border-radius: 10px; padding: 8px 10px; }
.wizard-details summary { cursor: pointer; font-size: 12px; color: var(--muted, #9aa0a6); }
.wizard-preview-meta { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: var(--muted, #9aa0a6); }
.wizard-busy { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 28px 0; }
.wizard-spinner { width: 28px; height: 28px; border-radius: 50%; border: 3px solid rgba(255,255,255,.15); border-top-color: var(--accent, #8ab4f8); animation: wiz-spin .9s linear infinite; }
@keyframes wiz-spin { to { transform: rotate(360deg); } }
.wizard-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.hidden { display: none !important; }

@media (max-width: 640px) {
  .wizard-root { align-items: stretch; justify-content: stretch; }
  .wizard-modal { width: 100vw; max-height: 100dvh; height: 100dvh; border-radius: 0; border: 0; }
  .wizard-body { padding: 14px 14px calc(14px + env(safe-area-inset-bottom)); }
  .wizard-foot { padding-bottom: calc(12px + env(safe-area-inset-bottom)); }
  .wizard-grid2 { grid-template-columns: 1fr 80px; }
}
```

### 11.3 `public/index.html` 修正

`<head>`:

```html
<link rel="stylesheet" href="style.css?v=161">
<link rel="stylesheet" href="wizard.css?v=1">
```

`<body>` の最後、スクリプトの前にウィザード本体を置く。

```html
<div id="wizardRoot" class="wizard-root hidden" aria-hidden="true">
  <div class="wizard-backdrop"></div>
  <div class="wizard-modal" role="dialog" aria-modal="true" aria-labelledby="wizardTitle">
    <header class="wizard-head">
      <button type="button" id="wizardBack" aria-label="戻る">‹</button>
      <div>
        <h2 id="wizardTitle">新しいチャンネル</h2>
        <div id="wizardProgress" class="wizard-progress"></div>
      </div>
      <button type="button" id="wizardModelChip" class="wizard-model-chip">AI: 未設定</button>
      <button type="button" id="wizardClose" aria-label="閉じる">✕</button>
    </header>
    <main id="wizardBody" class="wizard-body"></main>
    <footer id="wizardFoot" class="wizard-foot"></footer>
  </div>
</div>
```

スクリプト（`public/index.html` 957-960行付近の `<script>` 群の末尾に `wizard.js` を追加し、`app.js` のキャッシュバージョンを更新）:

```html
<script src="prompt_toolbar.js"></script>
<script src="progress_labels.js?v=1"></script>
<script src="app.js?v=138"></script>
<script src="wizard.js?v=1"></script>
```

`<details id="appSettings">` 内、`<div class="row tight"><button id="appSave"...></div>` 行の直後、既存の `<div class="box">`（候補の婉曲表現ボックス、244行付近）の手前に作成AIの設定を追加する（既存の `.box` クラスに合わせる）。

```html
<div class="box">
  <div class="box-title">チャンネル作成AI</div>
  <div class="muted">ウィザードの質問と設定生成に使います。本文AIとは別です。賢いモデルを推奨します。</div>
  <label class="wizard-check">
    <input type="checkbox" id="f_wizard_enabled" checked> ウィザードを使う
  </label>
  <label class="wizard-label">プロバイダー
    <select id="f_wizard_provider" class="wizard-input">
      <option value="google">Google AI Studio</option>
      <option value="vertex">Vertex AI</option>
      <option value="antigravity">Antigravity</option>
    </select>
  </label>
  <label class="wizard-label">モデル
    <input id="f_wizard_model" class="wizard-input" placeholder="例: gemini-2.5-pro">
  </label>
  <label class="wizard-label">思考の深さ
    <select id="f_wizard_thinking_level" class="wizard-input">
      <option value="high">high（推奨）</option>
      <option value="xhigh">xhigh（Gemini 4 Hydrogen 系のみ）</option>
      <option value="medium">medium</option>
      <option value="low">low</option>
      <option value="minimal">minimal</option>
    </select>
  </label>
  <label class="wizard-check">
    <input type="checkbox" id="f_wizard_confirm" checked> 作成前にプレビューを確認する
  </label>
</div>
```

### 11.4 `public/app.js` 修正

#### 既存の `newSession` を委譲

```javascript
async function legacyNewSession() {
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
  $("settingsPanel").classList.remove("hidden");
  updateFabState();
  flash("新しいチャンネルを作成しました");
  if (meta.app?.new_channel_ide === false) return;
  toast("Antigravity の新規会話を作っています…", { kind: "progress" });
  try {
    const n = await checked("/api/ide/new", { sid: r.sid, target: "both" });
    toast(`Antigravity の新規会話を作って担当にしました: ${n.title}（${String(n.ide).slice(0, 8)}）`, { kind: "ok", ms: 6000 });
    if (sid === r.sid) await poll();
    refreshSessions(); loadIdeSessions();
  } catch (e) {
    toast("チャンネルは作りましたが、Antigravity の新規会話は作れませんでした:\n" + e.message, { kind: "err", ms: 15000 });
  }
}

window.callLegacyNewSession = legacyNewSession;

$("newSession").onclick = async () => {
  if (window.RPWizard && meta?.app?.wizard_enabled !== false) {
    try {
      await window.RPWizard.open();
      return;
    } catch (e) {
      console.warn("[wizard]", e);
    }
  }
  legacyNewSession();
};
```

#### モバイルFAB・Esc・オーバーレイ

`isOverlayOpen()`:

```javascript
function isOverlayOpen() {
  const b = document.body;
  const sideOpen = !!(b && b.classList && b.classList.contains("side-open"));
  const settingsOpen = !!($("settingsPanel") && !$("settingsPanel").classList.contains("hidden"));
  const lbEl = $("lightbox");
  const lightboxOpen = !!lbEl && !lbEl.classList.contains("hidden");
  const wr = $("wizardRoot");
  const wizardOpen = !!wr && !wr.classList.contains("hidden");
  if (typeof document.querySelectorAll !== "function") {
    return sideOpen || settingsOpen || lightboxOpen || wizardOpen;
  }
  const modalOpen = document.querySelectorAll(".modal:not(.hidden)").length > 0;
  const moreSheetOpen = document.querySelectorAll(".more-sheet").length > 0;
  return sideOpen || settingsOpen || modalOpen || moreSheetOpen || lightboxOpen || wizardOpen;
}
```

`updateFabState()` は、ウィザードが開いている間も FAB を隠す（`isOverlayOpen()` を通す）。

```javascript
// app.js の updateFabState() 内、visible を決める直前
const wizardOpen = !!($("wizardRoot") && !$("wizardRoot").classList.contains("hidden"));
const visible = mobile && !moreSheet && !lightbox && !hasOpenModal && !wizardOpen && !scrolling;
```

`closeTopmostLayer()` の先頭:

```javascript
const wr = $("wizardRoot");
if (wr && !wr.classList.contains("hidden")) {
  if (window.RPWizard && typeof window.RPWizard.close === "function") {
    window.RPWizard.close();
  } else {
    wr.classList.add("hidden");
  }
  return true;
}
```

`#wizardRoot` には `.modal` を付けない。付けると `closeTopmostLayer` が `RPWizard.close()` を通らずに隠してしまう。

#### 作成後の切替

`app.js` のトップレベル `switchTo` / `refreshSessions` を `window` に明示公開する。
`wizard.js` 側はローカル `switchTo` を作らず、`activateCreatedSession` から `window.switchTo` を呼ぶ。

```javascript
// app.js の switchTo / refreshSessions 定義の末尾（またはスクリプト末尾）で明示公開
window.switchTo = switchTo;
window.refreshSessions = refreshSessions;
```

---

## 12. テスト

### 12.1 `test/test_wizard_server.py`（新規）

```python
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import server


TAGS_FIXTURE = {
    "version": 1,
    "axes": {"relation": {"label": "関係性", "max": 3}},
    "tags": [
        {"id": "rel_pure", "label": "純愛", "axis": "relation",
         "description": "まっすぐな恋愛", "effects": {"relationship": ["純愛"]}},
        {"id": "rel_childhood", "label": "幼馴染", "axis": "relation",
         "description": "幼馴染", "effects": {"relationship": ["幼馴染"]}},
        {"id": "kink_ntr", "label": "NTR", "axis": "kink",
         "description": "背徳", "effects": {"kinks": ["NTR"]}, "conflicts_ng": ["ng_ntr"]},
        {"id": "world_school", "label": "学園", "axis": "world",
         "description": "学校", "effects": {"world": ["学園"]},
         "image_tags": ["classroom"]},
    ],
    "ng_presets": [
        {"id": "ng_minor", "label": "未成年", "locked": True},
        {"id": "ng_ntr", "label": "NTR"},
    ],
    "examples": ["例"],
}


class WizardTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wizard_test_")
        self._old = {}
        for name in ("DATA", "APP_SETTINGS", "SESS_DIR", "ARCHIVE_DIR",
                     "CHOICES_DIR", "STATES_DIR", "IMAGES_DIR", "TRASH",
                     "WIZARD_DIR", "WIZARD_IDE_DIR", "WIZARD_TAGS_PATH",
                     "PUSH_SUBS", "VAPID_FILE"):
            self._old[name] = getattr(server, name, None)

        data = os.path.join(self.tmp, "data")
        os.makedirs(data, exist_ok=True)
        server.DATA = data
        server.APP_SETTINGS = os.path.join(data, "app_settings.json")
        server.SESS_DIR = os.path.join(data, "sessions")
        server.ARCHIVE_DIR = os.path.join(data, "archive")
        server.CHOICES_DIR = os.path.join(data, "choices")
        server.STATES_DIR = os.path.join(data, "states")
        server.IMAGES_DIR = os.path.join(data, "images")
        server.TRASH = os.path.join(data, "trash")
        server.WIZARD_DIR = os.path.join(data, "wizards")
        server.WIZARD_IDE_DIR = os.path.join(data, "wizard_ide")
        server.PUSH_SUBS = os.path.join(data, "push_subs.json")
        server.VAPID_FILE = os.path.join(data, "vapid_keys.json")
        server.WIZARD_TAGS_PATH = os.path.join(data, "wizard_tags.json")
        with open(server.WIZARD_TAGS_PATH, "w", encoding="utf-8") as fp:
            json.dump(TAGS_FIXTURE, fp, ensure_ascii=False)

        for p in (server.SESS_DIR, server.ARCHIVE_DIR, server.CHOICES_DIR,
                  server.STATES_DIR, server.IMAGES_DIR, server.TRASH,
                  server.WIZARD_DIR, server.WIZARD_IDE_DIR):
            os.makedirs(p, exist_ok=True)

        # 本物のキャラプリセットを読まない（テストを決定的にする）
        self._old_char_presets = getattr(server, "char_presets", None)
        server.char_presets = lambda: []

        server.wizard_jobs.clear()
        server.wizard_model_cache.clear()
        server.wizard_tags_cache.update({"ts": 0.0, "data": None, "index": {}})
        server.queued_images.clear()
        server.image_event.clear()

    def tearDown(self):
        for k, v in self._old.items():
            if v is not None:
                setattr(server, k, v)
        if getattr(self, "_old_char_presets", None) is not None:
            server.char_presets = self._old_char_presets
        server.queued_images.clear()
        server.image_event.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_draft(self):
        d = server.wizard_new_draft(
            {}, {"provider": "google", "model": "gemini-2.5-pro",
                 "thinking_level": "high", "ai_enabled": True}, True)
        d["base_sid"] = ""
        server.wizard_save(d)
        return d

    def with_compose(self, d):
        setting, warnings = server.wizard_template_compose(d)
        d["compose"] = {"setting": setting, "source": "template", "warnings": warnings,
                        "auto_slots": [], "title_alternatives": []}
        d["compose_stale"] = False
        d["status"] = "ready"
        server.wizard_save(d)
        return d


class TestSourceModel(WizardTestBase):
    def test_defaults_are_not_stored(self):
        d = self.new_draft()
        self.assertNotIn("player_type", d["slots"])
        self.assertEqual(server.wizard_effective_value(d, "player_type"), "original")
        self.assertEqual(server.wizard_effective_value(d, "pov"), "second")

    def test_user_beats_derived(self):
        d = self.new_draft()
        server.wslot_set(d, "relationship", ["恋人"], "user")
        d["selected_tags"] = ["rel_childhood"]
        server.wizard_recompute_derived(d)
        self.assertEqual(server.wslot_value(d, "relationship"), ["恋人"])

    def test_auto_is_filled(self):
        d = self.new_draft()
        server.wslot_set_auto(d, "partner_type")
        self.assertTrue(server.wizard_filled(d, "partner_type"))
        self.assertEqual(server.wslot_source(d, "partner_type"), "auto")

    def test_invalidation(self):
        d = self.new_draft()
        server.wslot_set(d, "partner_type", "copyright", "user")
        server.wslot_set(d, "work", "作品A", "user")
        server.wslot_set(d, "partner_type", "original", "user")
        self.assertIsNone(server.wslot_value(d, "work"))

    def test_canon_fidelity_default(self):
        d = self.new_draft()
        self.assertEqual(server.wizard_effective_value(d, "canon_fidelity"), "mixed")


class TestBaseStep(WizardTestBase):
    def make_base(self, title="学園もの", user_name="先生", project="企画X"):
        s = server.create_session({"title": title, "user_name": user_name, "project": project})
        return s["id"]

    def test_start_creates_base_step(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        self.assertFalse(r["resumed"])
        self.assertEqual(r["draft"]["step"], "base")
        self.assertTrue(r["draft"]["inherit_base"])
        self.assertEqual(r["draft"]["base_title"], "学園もの")

    def test_base_inherit_keeps_defaults(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        d = server.wizard_load(r["draft"]["id"])
        server.wizard_set_base(d, sid, True)
        pub = server.wizard_public(d, True)
        self.assertEqual(pub["defaults"]["user_name"], "先生")
        self.assertEqual(pub["defaults"]["project"], "企画X")
        self.assertEqual(pub["step"], "idea")

    def test_base_no_inherit_uses_defaults(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        d = server.wizard_load(r["draft"]["id"])
        server.wizard_set_base(d, sid, False)
        pub = server.wizard_public(d, True)
        self.assertFalse(pub["inherit_base"])
        self.assertEqual(pub["defaults"]["user_name"], "あなた")
        self.assertEqual(pub["defaults"]["project"], "")
        self.assertEqual(pub["base_title"], "学園もの")

    def test_base_locked_after_input(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        d = server.wizard_load(r["draft"]["id"])
        server.wizard_set_base(d, sid, True)
        server.wslot_set(d, "rough_idea", "放課後", "user")
        with self.assertRaises(server.WizardError) as cm:
            server.wizard_set_base(d, sid, False)
        self.assertEqual(cm.exception.code, "base_locked")

    def test_start_without_base_has_no_inherit(self):
        r = server.wizard_start({})
        self.assertEqual(r["draft"]["step"], "base")
        self.assertFalse(r["draft"]["inherit_base"])
        self.assertEqual(r["draft"]["base_title"], "")


class TestQuestionsAndTags(WizardTestBase):
    def test_sanitize_keeps_question_slot_effect(self):
        d = self.new_draft()
        qs = server.wizard_sanitize_questions(d, [{
            "slot": "relationship",
            "text": "関係は？",
            "options": [
                {"label": "幼馴染", "effects": {"relationship": ["幼馴染"]}},
                {"label": "恋人", "effects": {"relationship": ["恋人"]}},
                {"label": "ライバル", "effects": {"relationship": ["ライバル"]}},
                {"label": "家族", "effects": {"relationship": ["家族"]}},
            ],
        }])
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["options"][0]["effects"]["relationship"], ["幼馴染"])

    def test_ai_effects_cannot_change_inherited_name_or_project(self):
        d = self.new_draft()
        opts = server.wizard_sanitize_options(d, "relationship", [{
            "label": "上司",
            "effects": {"relationship": ["上司"], "player_name": "部長", "project": "別企画"},
        }])
        self.assertEqual(opts[0]["effects"]["relationship"], ["上司"])
        self.assertNotIn("player_name", opts[0]["effects"])
        self.assertNotIn("project", opts[0]["effects"])

    def test_tags_can_be_read_twice(self):
        # 2回目の wizard_tags() がキャッシュタイムスタンプの型破壊で落ちないこと
        self.assertTrue(server.wizard_tags()["tags"])
        self.assertTrue(server.wizard_tags()["tags"])

    def test_ng_removes_conflicting_tag(self):
        d = self.new_draft()
        server.wizard_apply_tags(d, {
            "selected_tags": ["kink_ntr", "rel_pure"],
            "selected_ng": ["ng_ntr"],
            "custom_ng": "",
        })
        self.assertNotIn("kink_ntr", d["selected_tags"])
        self.assertIn("rel_pure", d["selected_tags"])

    def test_ng_minor_always_present(self):
        d = self.new_draft()
        server.wizard_apply_tags(d, {"selected_tags": [], "selected_ng": [], "custom_ng": ""})
        self.assertIn("ng_minor", d["selected_ng"])

    def test_derived_does_not_overwrite_auto(self):
        d = self.new_draft()
        server.wslot_set_auto(d, "relationship")
        d["selected_tags"] = ["rel_childhood"]
        server.wizard_recompute_derived(d)
        self.assertEqual(server.wslot_source(d, "relationship"), "auto")

    def test_clean_tags_does_not_substring_drop(self):
        self.assertEqual(
            server.wizard_clean_tags(["headdress", "flying", "han solo"]),
            ["headdress", "flying", "han solo"],
        )

    def test_ng_matches_normalized_custom_text(self):
        d = self.new_draft()
        server.wizard_apply_tags(d, {
            "selected_tags": ["kink_ntr", "rel_pure"],
            "selected_ng": [],
            "custom_ng": "N T R",
        })
        self.assertNotIn("kink_ntr", d["selected_tags"])
        self.assertIn("rel_pure", d["selected_tags"])


class TestCompose(WizardTestBase):
    def test_template_is_valid(self):
        d = self.new_draft()
        setting, warnings = server.wizard_template_compose(d)
        self.assertEqual(server.wizard_validate_compose(setting), [])
        self.assertTrue(setting["first_message"])
        self.assertTrue(warnings)

    def test_age_17_warns(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "17歳", "profile": "P", "speech": "SP"},
        }
        setting, meta = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["character"]["age"], "なし")
        self.assertTrue(any("18歳未満" in w for w in meta["warnings"]))

    def test_age_text_detection(self):
        self.assertTrue(server.wizard_age_is_minor("高校2年生"))
        self.assertTrue(server.wizard_age_is_minor("17歳"))
        self.assertFalse(server.wizard_age_is_minor("大学1年"))
        self.assertFalse(server.wizard_age_is_minor("なし"))

    def test_compose_sanitizes_opening_image(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "24", "profile": "P", "speech": "SP"},
            "opening_image": ["classroom", "masterpiece", "1girl"],
        }
        setting, _ = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["opening_image"], ["classroom", "1girl"])

    def test_age_highschool_becomes_none(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "高校2年生", "profile": "P", "speech": "SP"},
        }
        setting, meta = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["character"]["age"], "なし")
        self.assertTrue(any("18歳未満" in w for w in meta["warnings"]))

    def test_age_none_stays_none(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "なし", "profile": "P", "speech": "SP"},
        }
        setting, meta = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["character"]["age"], "なし")
        self.assertFalse(any("18歳未満" in w for w in meta["warnings"]))

    def test_preview_age_17_becomes_none(self):
        d = self.new_draft()
        self.with_compose(d)
        final, _ = server.wizard_build_final_setting(d, {"character": {"age": "17"}})
        self.assertEqual(final["character"]["age"], "なし")

    def test_preset_tags_override_ai_tags(self):
        d = self.new_draft()
        server.wslot_set(d, "partner_name", "美咲", "user")
        preset = [{"id": "p1", "name": "美咲", "tags": "1girl, black hair, red eyes"}]
        with mock.patch.object(server, "char_presets", return_value=preset):
            chars = server.wizard_build_chars(
                d, [{"name": "美咲", "tags": ["1girl", "blonde hair"]}], "美咲")
        self.assertEqual(chars[0]["tags"], ["1girl", "black hair", "red eyes"])
        self.assertEqual(chars[0]["source"], "preset")
        self.assertTrue(chars[0]["locked"])

    def test_chars_picks_matching_name(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "美咲", "age": "24", "profile": "P", "speech": "SP"},
            "chars": [
                {"name": "別名", "tags": ["red hair"]},
                {"name": "美咲", "tags": ["black hair", "long hair"]},
            ],
        }
        setting, _ = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["chars"][0]["name"], "美咲")
        self.assertIn("black hair", setting["chars"][0]["tags"])

    def test_chars_are_normalized_for_app(self):
        d = self.new_draft()
        self.with_compose(d)
        d["compose"]["setting"]["chars"] = [
            {"name": "美咲", "tags": ["black hair", "long hair"], "source": "ai"}
        ]
        final, _ = server.wizard_build_final_setting(d, {})
        c = final["chars"][0]
        self.assertIsInstance(c["tags"], str)
        self.assertEqual(c["tags"], "black hair, long hair")
        self.assertTrue(c["id"])
        self.assertEqual(c["source"], "ai")

    def test_final_resets_initial_elapsed(self):
        d = self.new_draft()
        d["base_setting"] = {"initial_elapsed_time": {"day": 5, "period": "夜"},
                             "elapsed_time": {"day": 5, "period": "夜"}}
        self.with_compose(d)
        final, _ = server.wizard_build_final_setting(d, {})
        self.assertNotIn("initial_elapsed_time", final)

    def test_preview_edits_are_reflected_in_instructions(self):
        d = self.new_draft()
        self.with_compose(d)
        final, _ = server.wizard_build_final_setting(d, {
            "user_name": "太郎",
            "character": {"name": "美咲"},
            "summary": "新しいあらすじ",
        })
        self.assertEqual(final["user_name"], "太郎")
        self.assertIn("美咲", final["instructions"])
        self.assertIn("太郎", final["instructions"])
        self.assertIn("新しいあらすじ", final["instructions"])

    def test_compose_persists_auto_slots_on_disk_reload(self):
        d = self.new_draft()
        # 必須スロットを未設定のまま compose ジョブを実行
        server.wizard_save(d)
        job = {"job_id": "wj_test_persist", "draft_id": d["id"],
               "kind": "compose", "base_rev": d["rev"], "cancel": False}
        server.wizard_jobs[job["job_id"]] = job
        d["last_job"] = job["job_id"]
        server.wizard_save(d, bump=False)
        server.wizard_do_compose(job)
        fresh = server.wizard_load(d["id"])
        # missing_before のスロットが auto で保存され、reload 後も保持されていること
        self.assertEqual(server.wslot_source(fresh, "relationship"), "auto")
        self.assertEqual(server.wslot_source(fresh, "world"), "auto")


class TestCreate(WizardTestBase):
    def test_create_is_idempotent(self):
        d = self.new_draft()
        self.with_compose(d)
        r1 = server.wizard_create(d, {})
        r2 = server.wizard_create(server.wizard_load(d["id"]), {})
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["sid"], r2["sid"])
        self.assertTrue(os.path.exists(server.spath(r1["sid"])))

    def test_create_requires_compose(self):
        d = self.new_draft()
        with self.assertRaises(server.WizardError) as cm:
            server.wizard_build_final_setting(d, {})
        self.assertEqual(cm.exception.code, "wizard_not_composed")

    def test_create_with_use_template_without_compose(self):
        d = self.new_draft()
        r = server.wizard_create(d, {}, use_template=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["sid"])

    def test_created_channel_has_first_message(self):
        d = self.new_draft()
        self.with_compose(d)
        r = server.wizard_create(d, {})
        s = server.load(r["sid"])
        self.assertTrue(s["messages"])
        self.assertEqual(s["messages"][0]["role"], "assistant")

    def test_create_queues_opening_image(self):
        d = self.new_draft()
        self.with_compose(d)
        r = server.wizard_create(d, {})
        self.assertTrue(r["opening_image"])
        self.assertTrue(r["opening_image_mid"])
        s = server.load(r["sid"])
        m = s["messages"][0]
        self.assertEqual(m["id"], r["opening_image_mid"])
        self.assertEqual(m["image"]["status"], "queued")
        self.assertTrue(m["image"]["opening"])
        self.assertEqual(m["image"]["chars"], ["ヒロイン"])
        self.assertIn((r["sid"], m["id"]), list(server.queued_images))

    def test_create_skips_opening_image_when_off(self):
        d = self.new_draft()
        d["base_setting"] = {"image_mode": "off"}
        self.with_compose(d)
        r = server.wizard_create(d, {})
        self.assertFalse(r["opening_image"])
        s = server.load(r["sid"])
        self.assertNotIn("image", s["messages"][0])
        self.assertEqual(len(server.queued_images), 0)

    def test_create_does_not_duplicate_opening_image(self):
        d = self.new_draft()
        self.with_compose(d)
        r1 = server.wizard_create(d, {})
        r2 = server.wizard_create(server.wizard_load(d["id"]), {})
        self.assertEqual(r1["sid"], r2["sid"])
        self.assertEqual(len(server.queued_images), 1)
        s = server.load(r1["sid"])
        self.assertEqual(sum(1 for m in s["messages"] if (m.get("image") or {}).get("opening")), 1)

    def test_opening_image_uses_scene_tags_and_filters_appearance(self):
        d = self.new_draft()
        self.with_compose(d)
        d["compose"]["setting"]["opening_image"] = ["classroom", "masterpiece", "1girl", "black hair"]
        d["compose"]["setting"]["chars"][0]["tags"] = ["1girl", "black hair"]
        server.wizard_save(d)
        r = server.wizard_create(d, {})
        s = server.load(r["sid"])
        im = s["messages"][0]["image"]
        self.assertIn("classroom", im["prompt"])
        self.assertNotIn("masterpiece", im["prompt"])
        # 人数タグは画像生成に必要なので残す（AIが付け忘れたら1girlを補う）
        self.assertIn("1girl", im["prompt"])
        self.assertNotIn("black hair", im["prompt"])

    def test_opening_image_adds_count_when_missing(self):
        d = self.new_draft()
        self.with_compose(d)
        d["compose"]["setting"]["opening_image"] = ["classroom"]
        d["compose"]["setting"]["chars"][0]["tags"] = ["black hair"]
        server.wizard_save(d)
        r = server.wizard_create(d, {})
        s = server.load(r["sid"])
        im = s["messages"][0]["image"]
        self.assertIn("1girl", im["prompt"])
        self.assertNotIn("black hair", im["prompt"])

    def test_opening_image_failure_does_not_fail_create(self):
        d = self.new_draft()
        self.with_compose(d)
        with mock.patch.object(server, "wizard_enqueue_opening_image", side_effect=RuntimeError("boom")):
            r = server.wizard_create(d, {})
        self.assertTrue(r["ok"])
        self.assertFalse(r["opening_image"])
        self.assertTrue(any("開幕画像" in w for w in r["warnings"]))
        self.assertTrue(os.path.exists(server.spath(r["sid"])))


class TestJobsAndSweep(WizardTestBase):
    def test_stale_job_is_rejected(self):
        d = self.new_draft()
        job = {"job_id": "wj_0123456789", "draft_id": d["id"],
               "kind": "compose", "base_rev": d["rev"], "cancel": False}
        server.wizard_jobs[job["job_id"]] = job
        # wizard_do_compose が「last_job 不一致」で先に落ちないように合わせる
        d["last_job"] = job["job_id"]
        server.wizard_save(d, bump=False)
        d["rev"] += 1
        server.wizard_save(d, bump=False)
        with self.assertRaises(server.WizardError) as cm:
            server.wizard_do_compose(job)
        self.assertEqual(cm.exception.code, "stale")

    def test_compose_job_sets_composing(self):
        d = self.new_draft()
        # スレッドが即座に完了して status を戻さないようにブロックする
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.3)):
            job, _ = server.wizard_start_job(d, "compose")
            dd = server.wizard_load(d["id"])
            self.assertEqual(dd["status"], "composing")
            self.assertEqual(dd["step"], "creating")

    def test_sweep_removes_oldest(self):
        ids = []
        base_time = time.time() - 3600
        for i in range(server.WIZARD_DRAFT_LIMIT + 2):
            d = self.new_draft()
            # 7日間のTTLには引っかからず、上限（20件）超過で古いものから消えることを確認する
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base_time + i * 10))
            server.write_json(server.wizard_path(d["id"]), d)
            ids.append(d["id"])
            time.sleep(0.001)
        server.wizard_sweep()
        self.assertFalse(os.path.exists(server.wizard_path(ids[0])))
        self.assertTrue(os.path.exists(server.wizard_path(ids[-1])))

    def test_start_resumes_ready(self):
        d = self.new_draft()
        self.with_compose(d)
        r = server.wizard_start({"base_sid": ""})
        self.assertTrue(r["resumed"])
        self.assertEqual(r["draft"]["id"], d["id"])

    def test_ai_disabled_uses_template(self):
        d = self.new_draft()
        d["model"] = {"provider": "none", "model": "", "ai_enabled": False}
        self.assertFalse(server.wizard_ai_enabled(d))
        setting, warnings = server.wizard_template_compose(d)
        self.assertEqual(server.wizard_validate_compose(setting), [])

    def test_ide_poke_models_is_tuple(self):
        with mock.patch.object(server, "ide_poke") as ip:
            ip.models.return_value = ({"models": [{"model": "m1", "label": "M1"}], "default": "m1"}, None)
            payload = server.wizard_models_payload("antigravity")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["models"][0]["id"], "m1")


if __name__ == "__main__":
    unittest.main()
```

### 12.2 フロントの手動テスト

- `#wizardFoot` の「次へ」「作成」が押せる。
- 「＋」直後に「引き継ぎ元」が表示され、現在のチャンネル名が出る。
- 「このチャンネルから引き継ぐ」で、元のユーザー名・プロジェクト・画像設定が使われる。
- 「引き継がない」で、ユーザー名が「あなた」、プロジェクトが空、画像設定が初期値になる。
- チャンネル0件では「引き継ぎ元なし」と表示され、「初期設定で作る」だけが出る。
- `base` で閉じて再度開くと、`base` から再開する。
- 設定を入力した後に `base` へ戻ると、変更できない旨が表示される。
- 「AIを使わずに作る」でAI呼び出しが発生せず、即作成できる。
- AIエラー後に「AIなしで作る」で無限ループしない。
- 作成後に `window.switchTo` で新チャンネルが開く。
- モバイルでウィザード表示中に `☰` / `⌃⌄` が重ならない。
- Esc でウィザードだけが閉じる。
- 質問画面で自由入力すると選択肢の選択が解除される。
- 「あなたの名前」を空欄のまま保存すると、元チャンネルの呼び名が使われる。
- 一度名前を入れて保存→空欄で保存すると、元の呼び名に戻る。
- AIの質問に答えても呼び名が変わらない。
- 年齢「17歳」で警告が出て、プレビューの年齢が「なし」になる。
- 年齢「高校2年生」でも「なし」になる。
- 年齢「なし」はそのまま維持される。
- キャラプリセット「美咲」がある状態でAIが別の髪色を返しても、プリセットのタグが優先され、`locked` / `source=preset` になる。
- 画像ONで作成すると、チャンネルがすぐ開き、第一声の画像が「生成中」になる。ウィザードは画像の完成を待たない。
- 開幕画像の生成中にチャンネルを操作できる。
- 画像OFFで作成すると、第一声に画像が付かず、画像生成キューも空のまま。
- ComfyUI停止中でもチャンネルは作成され、画像はエラー表示になり、後から再生成できる。
- 作成連打・再送で開幕画像が二重に積まれない。
- プレビューに「開幕画像: 作成後に非同期生成 / 生成しない」が出る。
- プリセットの外見タグが開幕画像の場面タグに混ざらない。
- リロードで `draft.step` から復元される。

---

## 13. 実装順

| マイルストーン | 内容 | 受け入れゲート |
|---|---|---|
| M0 | `wizard_tags.json` 作成（§7.4.1 カタログ）・定数・スロット・source・既定値・`wizard_save(bump)`・chars正規化 | カタログ読み込み、source優先度、既定値非保存、chars文字列化のテスト |
| M1 | 下書きCRUD・引き継ぎ元ステップ・`apply_answer`・`apply_tags`・`apply_slots`・`wizard_build_chars`（プリセット優先）・テンプレートcompose・`create`・`wizard_enqueue_opening_image` | 「引き継ぐ／引き継がない」が通り、AIなしで「引き継ぎ元→アイデア→相手→タグ→作成」が通る。プリセットのタグがAIタグより優先され、開幕画像が `queued` で積まれ、二重作成なし |
| M2 | モデル一覧・AIアダプタ・質問ジョブ・composeジョブ・年齢補正・`opening_image`タグ・stale/rev・キャンセル | モックAIで6問/2ラウンド、空出力→テンプレート、18歳未満→「なし」、開幕場面タグが空でもfallback、busy再送 |
| M3 | `wizard.js` 全画面・引き継ぎ元画面・モデル設定UI・プレビュー保存・開幕画像の予告/トースト・エラー/再試行・legacy公開 | リロード復元、連打、おまかせ即作成、AIなし作成、画像OFF、375px |
| M4 | テスト拡充・手動チェック・PWA/既存導線の回帰 | 下記チェックリスト全通し |

---

## 14. 受け入れチェックリスト

| # | 確認 | 期待 |
|---|---|---|
| A0 | 「＋」直後 | 引き継ぎ元画面が出る。現在のチャンネル名と「引き継ぐ／引き継がない」がある |
| A1 | 「＋」→引き継ぎ元→次へ | モデル選択画面が出る |
| A2 | 接続テスト | Google/VertexはJSON応答、Antigravityはモデル一覧 |
| A3 | モデル未選択で次へ | エラーになる |
| A4 | 「AIを使わずに作る」 | 質問なしで作成できる |
| A5 | アイデア空で「おまかせ」 | 作成できる |
| A6 | 版権キャラ＋作品名 | プリセット優先、タグ生成 |
| A7 | NG「NTR」＋NTRタグ | タグが外れる |
| A8 | 年齢「17歳」 | 警告、「なし」に補正 |
| A9 | 質問6問 | 2ラウンドで終了 |
| A10 | 質問を全部おまかせ | 作成できる |
| A11 | 自由入力＋選択肢 | 自由入力が優先 |
| A12 | プレビューで名前変更 | 第一声・指示に反映 |
| A13 | 作成連打 | 同じ `sid` |
| A14 | 作成中に閉じて再開 | ジョブが続き、完了後にプレビュー |
| A15 | 生成中に回答 | 409 `stale`、上書きされない |
| A16 | AIエラー→「AIなしで作る」 | テンプレートで作成 |
| A17 | 空のチャンネル | 既存導線が動く |
| A18 | 既存 `/api/session/new` | 既存テストが通る |
| A19 | モバイル375px | 全画面、FABなし、Esc/戻る |
| A20 | 下書き20件 | 最古が削除される |
| A21 | 「引き継がない」で作成 | `user_name` が「あなた」、`project` が空、画像設定が初期値 |
| A22 | 「このチャンネルから引き継ぐ」で作成 | 元の `user_name` / `project` / 画像設定 / 参照ファイル / 本文AI設定が使われる |
| A23 | チャンネル0件で「＋」 | 「引き継ぎ元なし」と「初期設定で作る」だけが出る |
| A24 | アイデア入力後に `base` へ戻る | 「変更できません」と表示され、`base_locked` になる |
| A25 | 年齢「高校2年生」 | 警告が出て、年齢が「なし」になる |
| A26 | キャラプリセット「美咲」あり | プリセットのタグがAIタグより優先され、`locked` / `source=preset` になる |
| A27 | 画像ONで作成 | 新チャンネルがすぐ開き、第一声の画像が `queued` / `generating`。ウィザードは待たない |
| A28 | 画像OFFで作成 | 第一声に画像が付かず、`queued_images` にも積まれない |
| A29 | ComfyUI停止中に作成 | チャンネルは作成され、画像は `error`。再生成できる |
| A30 | 作成連打・再送 | 開幕画像は1つだけ。`opening_image_mid` が同じメッセージを指す |

---

## 15. リスクと対策

| リスク | 対策 |
|---|---|
| AIがJSON以外を返す | `wizard_extract_json`＋1回修復＋テンプレートfallback |
| Antigravityが応答中 | `send_to_conversation` を最大3回再送 |
| 生成中の下書き変更 | `base_rev` チェックで `stale` |
| 二重作成 | `created_sid` と `rev` |
| 既定値がタグ再計算で消える | 既定値は保存せず `wizard_effective_value` で補う |
| charsの型不一致 | `wizard_normalize_chars` で文字列化 |
| app-settingsのKeyError | 既存 `DEFAULT_APP` ループ＋`cur` 検証 |
| フッターが押せない | `.wizard-modal` にイベント登録 |
| `switchTo` の衝突 | `activateCreatedSession` から `window.switchTo` |
| モバイルFABの重なり | `isOverlayOpen` に `#wizardRoot` を追加 |
| モデル一覧のプロバイダ混同 | `google_ai.list_models(provider=...)` を明示 |
| テストが本番データを汚す | `DATA` 配下の全パスを一時領域へ |
| 引き継ぎ元の確認が邪魔 | 既定を「引き継ぐ」にし、1画面だけ。前回の下書きは再開時にスキップ |
| 未保存の設定が引き継がれない | 画面に「保存済みの設定のみ」と明記。必要ならPhase 2でスナップショット |
| 別チャンネルから選べない | Phase 1は現在／なしの2択。別チャンネルは、そのチャンネルを開いてから「＋」 |
| 年齢の文章表現を誤判定する | 「17歳」「高校2年生」などは未成年、「大学1年」「成人」は成人として扱う。判定できない場合は成人既定「なし」 |
| プリセットのタグが服装を含む | プリセットはユーザーが固定した「正」なので、AIタグと違って服装もそのまま使う |
| 開幕画像が遅い/失敗する | 非同期キューに積むだけにし、チャンネル作成は待たせない。失敗は画像の `error` 表示と再生成で回復する |
| 開幕画像が二重に積まれる | `created_sid` の冪等作成と、最初のassistantの `image` 有無チェック。`opening: true` で識別する |
| 画像OFFなのに画像が付く | `image_mode="off"` を最初に判定してキューに積まない |
| 開幕画像に人物の外見が混ざる | プロンプトで場面タグのみ指示。`chars` と同名のタグは `wizard_enqueue_opening_image` で除去する |
| 画像キューが混む | 開幕画像は既存キューに並ぶだけ。ウィザードの応答は待たない。画像完成は非ゴール |
| 画像生成中にチャンネルを削除 | 既存の `image_worker` がセッションの存在を確認してスキップする |
| 画面移動で実ジョブが stale になる | `/api/wizard/step` は `bump=False`。生成中は step を送らない |
| 二重クリックで二重作成 | `wizard_create_lock` と `created_sid`／rev 再確認 |
| プリセットを「使わない」に戻せない | `partner_preset_off` を保存し、空IDも送る |
| プリセットの服装タグが消える | `source=="preset"` は `wizard_clean_tags` を通さない |
| 人数タグが消えて画像が崩れる | 開幕画像の場面タグは人数タグを残し、無ければ `1girl` を補う |
| rev競合で画面が古いまま | 409 に最新 `draft` を付け、`apiCall` が再描画する |

---

## 16. 付録: 実装時の確認コマンド

```bash
python -m py_compile server.py
python -m unittest test.test_wizard_server -v
node --check public/wizard.js
```

`wizard_tags.json` は起動時に読み込む。編集後はサーバーを再起動するか、`wizard_tags_cache` をクリアする。

## 17. v1.5 レビュー指摘の修正一覧

| 深刻度 | 指摘 | 対応 |
|---|---|---|
| 致命的 | `$("#newSession")` が `getElementById` と不一致 | `$("newSession")` に修正 |
| 致命的 | 閉じる操作の step 保存が rev を上げ、生成ジョブが stale | `/api/wizard/step` を `bump=False`。生成中は閉じても step を送らない |
| 致命的 | プリセットのタグが `wizard_normalize_chars` で削られる | `source=="preset"`／`locked` は `wizard_preset_tags` をそのまま使う |
| 致命的 | タグカタログの axes/desc/ID が仕様と不一致 | §7.4.1 に完全な JSON を追加。`desc`・axes配列も読み替える |
| 重大 | プレビューの instructions 編集で確定事項が消える | 常に末尾へ再付与。名前変更は chars／第一声へ反映 |
| 重大 | `project`／`user_name` を空に戻せない | `or` 連鎖をやめ、明示的な空文字を保存 |
| 重大 | プリセットの「使わない」が保存されない | `partner_preset_off` と空ID送信 |
| 重大 | 作成失敗でフォームが消える | プレビューを再描画し、再試行ボタンを残す |
| 重大 | Antigravity の configured／排他／キャンセル／エラー | configured を provider 考慮、`get_poke_lock`、`job.cancel`、`http_status` |
| 重大 | rev 確認と保存が別ロック | `wizard_write` で同一ロック内に CAS。`/create` は専用ロック |
| 中 | トーストがウィザードの下 | `body.wizard-open #toast` を前面に |
| 中 | base のタイトル／進捗／モデルチップ | `titleFor`・`progressFor`、生成中はチップ無効 |
| 中 | `wizard_thinking_level` が xhigh を拒否 | 検証と設定UIに xhigh を追加 |
| 中 | `res.get("http")` で status 判定が死ぬ | `http_status` に修正 |
| 中 | derived が auto を上書き／NG の表記ゆれ | `SOURCE_ORDER` と recompute を修正、NG を正規化 |
| 軽微 | 終了ジョブがメモリに残る | `wizard_sweep` で TTL 回収 |
| 軽微 | モバイルFABがウィザードに残る | `updateFabState` に `wizardOpen` を追加 |
| 軽微 | confirm=false の再開で自動作成されない | `resumeJob`／`pollJob`／`startCreateChain` で統一。`idea-auto` は下書きを書き換えず1回だけ |

## 18. v1.6 レビュー指摘（Hydrogen）の修正一覧

| 深刻度 | 指摘 | 対応 |
|---|---|---|
| 致命的 | `wizard_ensure_builder` と `/api/app-settings` のデッドロック | `wizard_ensure_builder` では `wizard_lock` 解放後に `lock` を取得し、`/api/app-settings` では `with lock:` を抜けた直後に `wizard_lock` でキャッシュ破棄。ロック逆順ネストを完全に解消 |
| 致命的 | compose ジョブで自動補完したスロットがリロードで消滅 | `wizard_do_compose` 内で `missing_before` を `wslot_set_auto` した直後に `wizard_save(d, bump=False)` を呼ぶように修正（以後の `wizard_load` で破棄されるのを防止） |
| 致命的 | `#appSettings` 内のクリックで入力中のモデル名がリセット | `document.addEventListener("click")` で `#appSettings` 内の全クリック時に `loadWizardSettings()` を呼んでいた不具合を、`<details id="appSettings">` の `toggle` イベント（展開時のみ）と `#appSave` クリック時の同期に是正 |
| 重大 | `app.js` の `legacyNewSession()` 実装がプレースホルダのみ | 既存 `app.js`（L9980-10003）の完全な実装コードをプラン内に記述 |
| 重大 | `window.switchTo` / `window.refreshSessions` が未公開で `location.reload()` にフォールバック | `app.js` 側で `window.switchTo` / `window.refreshSessions` を明示公開し、`wizard.js` 側でもグローバル `switchTo` / `refreshSessions` をフォールバック参照するよう二重化 |
| 重大 | ジョブ実行中にモーダルを閉じると 409 `wizard_busy` で失敗 | `/api/wizard/step` に `wizard_write(body, bump=False, allow_running=True)` を指定し、UI状態更新をジョブ実行中でも安全に受容 |
| 中 | 原作準拠度（`canon_fidelity`）が未選択時に `canon` に強制反転 | HTMLの `<select>` 既定順を `mixed`（推奨・既定値）優先に変更し、未設定時の意図しない反転を解消 |
| 中 | Antigravity / AI無効モデル設定時にモデル画面へ誤リダイレクト | `open()` のモデル判定に `provider === "antigravity"`（モデル空許容）および `provider === "none"`（AI無効）の条件を追加 |
| 中 | `#appSettings` の設定枠が `.settings-box` でスタイル未定義 | `public/index.html` および `style.css` 既存の `.box` / `.box-title` クラスに統一 |
| 軽微 | M0マイルストーンに `wizard_tags.json` の作成が未明記 | §13 の M0 に §7.4.1 カタログに基づく `wizard_tags.json` の配置を明記 |

## 19. v1.7 レビュー指摘（Hydrogen再精査）の修正一覧

| 深刻度 | 指摘 | 対応 |
|---|---|---|
| 致命的 | `wizard_tags()` 内でループ変数 `t` がタイムスタンプ `t` をシャドーイングし、全タグ参照・セッション作成・17件の単体テストが `TypeError` でクラッシュ | `t = dict(x)` を `tag = dict(x)` に修正し、`wizard_tags_cache["ts"]` にタグ辞書ではなく正規のタイムスタンプ `time.time()` が保存されるように是正 |
| 致命的 | `/api/app-settings` の差分が `server.py` の外側 `with lock:` 内に配置されデッドロックリスクが残留 | `/api/app-settings` を外側 `with lock:` の手前に独立配置し、`with lock:` を `APP_SETTINGS` 保存のスコープのみに限定。元の分岐を削除し、`lock` 解放後に `wizard_lock` でキャッシュ破棄する構成を明記 |
| 致命的 | `do_POST` の差分で `d = self.body()` を再実行しリクエストストリーム枯渇・例外発生 | 既に `server.py` 冒頭でパース済みの `d` および `p = u.path` をそのまま利用し、二重読み込みと再分割を排除 |
| 重大 | `test_sweep_removes_oldest` で 2020 年の日時を使用し TTL で全削除されテストが失敗 | `time.time() - 3600` を基準とする直近の日時を使用し、7日間のTTLにはかからず件数上限（20件）による古い下書きの間引きのみを正確に検証するよう修正 |
| 重大 | `open()` 実行中に `ensureDraft()` が例外で失敗すると空のモーダルが閉じずに残留 | `ensureDraft()` を try-catch で保護し、失敗時は `.hidden` 付加、`wizard-open` 除去、`releaseFocus()`、FAB同期を行う |
| 重大 | モーダル表示・非表示時に `updateFabState()` が呼ばれずモバイルFABが非表示のまま残る | `open()` および `close()` 時に `updateFabState()` を明示的に呼び出し、モバイルFABの開閉表示を即時同期 |
| 中 | ウィザード内モデル選択モーダル（`renderModel`）で `xhigh` 思考深度が選択不可 | `#wizThink` の `<select>` オプションに `xhigh（Gemini 4 Hydrogen 系のみ）` を追加 |
| 中 | 新規チャンネル作成時の Antigravity 会話作成完了後に会話一覧が即時反映されない | `checked("/api/ide/new")` 完了時に `loadIdeSessions()` および `refreshSessions()` を呼び出すように改善 |
| 中 | `#appSettings` が最初から展開されている場合にウィザード設定値が空欄になる | `init()` 時に `appSetEl.open` であれば即座に `loadWizardSettings()` を実行するガードを追加 |
| 軽微 | `public/index.html` 内の作成AIボックスの具体的な挿入位置が曖昧 | `#appSettings` 内の `#appSave` 行の直後、候補の婉曲表現ボックスの手前であることを明記。`app.js` のキャッシュバージョン（`v=137` → `v=138`）も整合 |

**仕様書との関係**: `docs/channel-wizard-spec.md` の §6.3 は旧形式（axes配列・`desc`・`ntr` 等のID）を含むため、実装は本プラン §7.4.1 のカタログを正とし、旧形式も読み替える。

## 20. v1.8 再精査での追加修正

v1.7 の指摘はすべて有効だったが、実装に落とすと不足する点が3つあったため補正した。

1. **`open()` 失敗時のフォーカス復帰**
   `trapFocus()` の後に `ensureDraft()` が失敗すると、モーダルを隠してもフォーカスが背面に戻らなかった。`releaseFocus()` と `stopPoll()` を catch に追加。

2. **`/api/app-settings` 移設後の旧分岐削除**
   外側 `with lock:` の手前に移すだけでは、元の内側分岐が到達不能な重複として残る。移設時に元の `if p == "/api/app-settings":` を必ず削除する旨を明記。

3. **`do_POST` はパース済みの `p` を使う**
   `self.path` の再分割ではなく、既に取得済みの `p = u.path` を `wizard_route` に渡すよう統一。

あわせて、Antigravity 会話作成後の一覧同期を `await loadIdeSessions()` / `await refreshSessions()` にし、`wizard_tags()` を2回読む回帰テストと、`custom_ng` の重複プロパティ削除を追加した。

---

## 21. defaults/ 基盤（v1.9）

### 21.1 目的

`data/` を「初回起動に不要・私有・ユーザー変更データ」専用のまま維持し、配布する初期設定を置くための `defaults/` 基盤を追加した。

今回は基盤のみで、`wizard_tags.json` の移動は次フェーズ。

### 21.2 追加ファイル

- `defaults/README.md`
  - `defaults/` を Git 管理するための説明ファイル
  - 読み取り専用、秘密情報禁止、`data/` 優先を明記
- `test/test_defaults.py`
  - defaults/data の解決順とフォールバックの単体テスト

### 21.3 `server.py` の変更

`DATA` の直後に定数を追加。

```python
DATA = os.environ.get("RPCHAT_DATA", os.path.join(ROOT, "data"))
DEFAULTS = os.path.join(ROOT, "defaults")
```

`read_text()` の直後に解決ヘルパーを追加。

```python
def _seed_rel(rel):
    """defaults/data 配下の相対パスを安全な形に正規化する。"""
    s = str(rel or "").replace("\\", "/").strip()
    if not s:
        raise ValueError("empty defaults path")
    if os.path.isabs(s) or s.startswith("/") or ":" in s.split("/", 1)[0]:
        raise ValueError("absolute defaults path")
    return s


def _under(root, rel):
    """root の外に出るパスを拒否して絶対パスを返す。"""
    root_abs = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root_abs, _seed_rel(rel)))
    try:
        common = os.path.commonpath([root_abs, path])
    except ValueError:
        raise ValueError("invalid defaults path")
    if os.path.normcase(common) != os.path.normcase(root_abs):
        raise ValueError("invalid defaults path")
    return path


def defaults_path(rel):
    """defaults/ 配下の絶対パスを返す（存在確認はしない）。"""
    return _under(DEFAULTS, rel)


def seed_path(rel):
    """
    ユーザー版 data/ があればそれを、無ければ defaults/ を返す。
    どちらも無ければ空文字列。
    """
    user = _under(DATA, rel)
    if os.path.isfile(user):
        return user
    default = defaults_path(rel)
    if os.path.isfile(default):
        return default
    return ""


def read_seed_json(rel, fallback):
    """data/ 優先、無ければ defaults/、無ければ fallback。"""
    path = seed_path(rel)
    if not path:
        return fallback
    try:
        return read_json(path, fallback)
    except Exception:
        return fallback


def read_seed_text(rel, fallback=""):
    """テキスト版。data/ 優先、無ければ defaults/、無ければ fallback。"""
    path = seed_path(rel)
    if not path:
        return fallback
    try:
        value = read_text(path)
        return fallback if value is None else value
    except Exception:
        return fallback
```

### 21.4 読み込み契約

| 用途 | 使う関数 | 書き込み先 |
|---|---|---|
| ユーザー設定の読み込み | `seed_path()` / `read_seed_json()` / `read_seed_text()` | `data/` |
| 配布カタログの読み込み | `defaults_path()` | なし |
| ユーザー設定の保存 | 既存の `write_json()` | `data/` |
| defaults への保存 | 禁止 | なし |

- `data/` と `defaults/` の両方がある場合、`data/` を優先する。
- `defaults/` は読み取り専用。
- `wizard_tags.json` は配布カタログなので、将来は `defaults_path("wizard_tags.json")` を使う。
- `wizard_tags.json` にユーザー上書きを許す場合は、別名（例: `wizard_tags.local.json`）にする。
- v1.9 のフェーズでは `WIZARD_TAGS_PATH` を変更していない（§22 で変更済み）。

### 21.5 テスト

`test/test_defaults.py` を追加し、以下を検証する。

- defaults のみ存在する場合
- `data/` が `defaults/` を上書きする場合
- 両方無い場合の fallback
- テキストファイルの同じ解決
- `../` による defaults 外参照の拒否
- 絶対パスの拒否
- Windows の `\` 区切りの正規化
- 壊れた JSON の fallback
- `defaults/README.md` の存在

実行結果:

```text
python -m unittest test.test_defaults -v
Ran 12 tests ... OK

python -m unittest test.test_wizard_server -v
Ran 47 tests ... OK

python -m py_compile server.py
OK
```

### 21.6 受け入れ

- `git check-ignore -v defaults/README.md` が出力なし
- `git status --short` で `data/` と `.gitignore` が変更されていない
- 既存のウィザードテスト47件が成功
- サーバー起動時に `defaults/` への書き込みが発生しない

### 21.7 次フェーズ（実施済み）

`wizard_tags.json` を `defaults/` へ移動し、`WIZARD_TAGS_PATH = defaults_path("wizard_tags.json")` に変更した。詳細は §22。

`decensor.json`、`writing_style.md`、`char-presets.json` は `read_seed_json()` / `read_seed_text()` へ段階的に接続する。

---

## 22. `wizard_tags.json` の defaults 移行（v2.0）

### 22.1 変更内容

- `data/wizard_tags.json` を `defaults/wizard_tags.json` へ移動した。
- `WIZARD_TAGS_PATH` を `defaults_path("wizard_tags.json")` に変更した。
- `DATA` や `ROOT` 直下へのフォールバックは削除した。
- `defaults_path()` を `WIZARD_TAGS_PATH` の初期化より前に定義した。
  - 起動時に `wizard_tags.json` を読むため、`NameError` を避ける。

```python
WIZARD_TAGS_PATH = defaults_path("wizard_tags.json")
```

### 22.2 読み込み仕様

- 配布カタログは `defaults/wizard_tags.json` を直接読む。
- `data/wizard_tags.json` へのユーザー上書きは行わない。
  - 将来ユーザー編集を許す場合は `wizard_tags.local.json` のような明示的な別名を追加する。
- テストでは `WizardTestBase` が `WIZARD_TAGS_PATH` を一時ファイルへ差し替えるため、既存のテストは決定的なまま。

### 22.3 回帰テスト

`test/test_defaults.py` に以下を追加した。

- `server.WIZARD_TAGS_PATH` が `defaults/wizard_tags.json` を指す
- ファイルが存在する
- `tags` が空でないリストである

実行結果:

```text
python -m unittest test.test_defaults -v
Ran 13 tests ... OK

python -m unittest test.test_wizard_server -v
Ran 47 tests ... OK

python -m py_compile server.py
OK
```

### 22.4 受け入れ

- `data/wizard_tags.json` が存在しない
- `defaults/wizard_tags.json` が存在し、JSON として読める
- 新規 `RPCHAT_DATA` でもタグカタログが読める
- `WIZARD_TAGS_PATH` が `defaults_path()` の結果と一致する
- 既存のウィザードテスト47件が成功

---

以上。

