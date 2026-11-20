<!-- verify-names: armed archiving settle spawn_main spawn_choices bootstrap_main bootstrap_choices verify_ready verify_test cutover new_ide new_choices_ide rotation_reserve rotation_skip_until rotation_notice consolidation_ts -->

# 世代交代（ローテーション）ステートマシン仕様書

本ドキュメントは `server.py`（行 6050 付近の `ROTATION_STEPS`）に実装されている、IDE セッションの世代交代（ローテーション）ステートマシンの全10フェーズおよび関連状態変数の仕様を定義する。

## 概要

会話セッションが長大化した際、コンテキスト肥大化によるレイテンシ増大や忘却を防ぐため、バックグラウンドで新しい IDE 会話セッションを作成・初期化・疎通確認した上で、アトミックに切り替える。この遷移は以下の 10 フェーズによって管理される。

## 状態遷移フロー

```
[armed] (準備完了・トリガー待機)
   │
   ▼
[archiving] (現在会話のアーカイブ・退避)
   │
   ▼
[settle] (インフライトタスクの完了・沈静化待ち)
   │
   ▼
[spawn_main] (新メインIDE会話の生成: new_ide)
   │
   ▼
[spawn_choices] (新選択肢IDE会話の生成: new_choices_ide)
   │
   ▼
[bootstrap_main] (新メインIDE会話の初期化プロンプト送信)
   │
   ▼
[bootstrap_choices] (新選択肢IDE会話の初期化)
   │
   ▼
[verify_ready] (新IDEの起動・アイドル確認)
   │
   ▼
[verify_test] (新IDEへの疎通テスト確認)
   │
   ▼
[cutover] (新IDEへのアトミック切替・クリーンアップ)
```

## 全10フェーズ詳細仕様

### 1. `armed`
- **目的**: 世代交代待機フェーズ。
- **動作**: チャンネルが世代交代可能（`rotation_count`、`ROTATION_PERIOD`）に到達するまで待機。ユーザー発言やターン終了時に条件を満たすと次フェーズへ移行する。

### 2. `archiving`
- **目的**: 現在の会話データの永続化と退避。
- **動作**: メッセージ履歴をアーカイブファイルへ書き出し、差分コンテキスト計算用の整合性を担保する。

### 3. `settle`
- **目的**: 送信中・処理中タスクの完了待ち（安定化）。
- **動作**: 未処理の outbox や pending / processing 状態のタスクが落ち着くまで待機し、会話状態の不整合を防ぐ。

### 4. `spawn_main`
- **目的**: 新しいメイン会話用 IDE プロセスの確保。
- **動作**: `ide_poke.new_conversation` を呼び出して `new_ide` を払い出し、ローテーション状態に記録する。

### 5. `spawn_choices`
- **目的**: 選択肢（choices）並列生成用の新 IDE プロセス確保。
- **動作**: 並列選択肢設定が有効な場合、選択肢専用の `new_choices_ide` を作成・確保する。

### 6. `bootstrap_main`
- **目的**: 新メイン IDE への初期プロンプト（キャラクター設定・要約・System命令）の投入。
- **動作**: `new_ide` に対して立ち上げ用コンテキストを送信し、応答可能な状態へ導く。

### 7. `bootstrap_choices`
- **目的**: 新選択肢 IDE への初期プロンプト投入。
- **動作**: `new_choices_ide` が存在する場合、選択肢生成に必要な指示文・制約プロンプトを注入する。

### 8. `verify_ready`
- **目的**: 新 IDE の準備完了確認。
- **動作**: 新会話がアイドル状態（`idle`）になり、次のメッセージを受け付けられるか確認する。

### 9. `verify_test`
- **目的**: 疎通テスト（Ping/Test）の検証。
- **動作**: 実際に短い入力に対する応答が得られるかテストし、ハングやエラーがないか確認する。失敗した場合は再試行またはフォールバックを行う。

### 10. `cutover`
- **目的**: アトミックな切り替えと後処理。
- **動作**:
  - セッション設定の `ide_session` を `new_ide` に、`choices_ide_session` を `new_choices_ide` に切り替える。
  - メッセージ履歴をプルーニングし、直近ターンのみ残す。
  - `rotation_notice`、`rotation_reserve`、`rotation_skip_until`、`consolidation_ts` 等の関連メタデータを更新・クリアする。
  - セッションを保存し、ステートマシンを完了させる。

## 関連フィールド・変数一覧

- `new_ide`: 新しく生成されたメイン IDE 会話 ID。
- `new_choices_ide`: 新しく生成された選択肢専用 IDE 会話 ID。
- `rotation_reserve`: 交代予備スロット情報。
- `rotation_skip_until`: エラー発生時等に一時的に交代を保留するターン数。
- `rotation_notice`: 交代状態の UI 通知オブジェクト。
- `consolidation_ts`: 長期記憶整理（consolidation）タスクのタイムスタンプ。
