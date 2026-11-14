# -*- coding: utf-8 -*-
"""
tools/cleanup_test_data.py
Python テスト等で生成されたゴミチャンネル（セッション）および
それに付随する関連データ（RAG記憶, sent, choices, states, images, ctx, trash等）を
安全に検出・削除・掃除するスクリプト。

使い方:
    python tools/cleanup_test_data.py --dry-run     # 削除対象の確認（ファイルは削除されません）
    python tools/cleanup_test_data.py               # 確認プロンプト後に実行（自動バックアップ付き）
    python tools/cleanup_test_data.py --force       # 確認なしで即座に実行
    python tools/cleanup_test_data.py --include-trash # data/trash 内のテスト残骸も一緒に削除
"""
import argparse
import copy
import glob
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

DATA_DIR = os.path.join(ROOT_DIR, "data")
SESS_DIR = os.path.join(DATA_DIR, "sessions")
RAG_DIR = os.path.join(DATA_DIR, "rag")
SENT_DIR = os.path.join(DATA_DIR, "sent")
CHOICES_DIR = os.path.join(DATA_DIR, "choices")
STATES_DIR = os.path.join(DATA_DIR, "states")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
CTX_DIR = os.path.join(DATA_DIR, "ctx")
TRASH_DIR = os.path.join(DATA_DIR, "trash")
SESSION_FOLDER = os.path.join(ROOT_DIR, "session")
OUTBOX_DIR = os.path.join(ROOT_DIR, "outbox")

# テストで使われる典型的なタイトル
TEST_TITLE_EXACT = {
    "CardNlLimit", "CardCharLimit", "TestBuildContextTrim", "FullModeTest",
    "API Test", "Updated Title", "Keep Test", "Endpoint Test",
    "M8テスト", "M9テスト", "M11テスト", "D14テスト", "D15ラピテスト", "D16超重量テスト",
    "D17メトリクステスト", "ErrorSchemaテスト", "Lifecycleテスト",
    "Google Test Session", "Google Choices Session",
    "RAGテスト", "RAGエラーテスト", "サブタスクテスト", "タイムアウトテスト",
    "空本文テスト", "3回空テスト", "選択肢ステータステスト", "bad再ポークテスト",
    "成功リセットテスト", "perfログテスト", "spillテスト", "compactテスト", "時間テスト"
}

TEST_TITLE_PATTERNS = [
    re.compile(r"テスト", re.IGNORECASE),
    re.compile(r"^test_", re.IGNORECASE),
    re.compile(r"_test$", re.IGNORECASE),
    re.compile(r"limit$", re.IGNORECASE),
]


def is_test_session(sid, data):
    """セッションがテスト用のゴミデータかどうかを判定する"""
    if not isinstance(data, dict):
        return True, "corrupted_json"

    # ID判定
    if sid.startswith("test_") or sid.startswith("sid_test") or sid.startswith("sid-"):
        return True, f"sid_prefix({sid})"

    setting = data.get("setting") if isinstance(data.get("setting"), dict) else {}
    title = (setting.get("title") or data.get("title") or "").strip()
    char = setting.get("character") or data.get("character") or {}
    char_name = (char.get("name") if isinstance(char, dict) else "") or ""
    msgs = data.get("messages", [])
    if not isinstance(msgs, list):
        msgs = []
    mcount = len(msgs)

    # 1. 既知のテストタイトル完全一致
    if title in TEST_TITLE_EXACT:
        return True, f"known_test_title({title})"

    # 2. テスト用タイトルパターンかつメッセージ数が少数（3件以下）
    if mcount <= 3 and any(pat.search(title) for pat in TEST_TITLE_PATTERNS):
        return True, f"pattern_test_title({title})"

    # 3. メッセージが「初期化」のみ等のテスト特有のダミー
    if msgs and mcount <= 3:
        first_txt = msgs[0].get("text", "") if isinstance(msgs[0], dict) else ""
        if first_txt in ("初期化", "テスト", "こんにちは！") and title in TEST_TITLE_EXACT:
            return True, f"dummy_msg_pattern({first_txt})"

    return False, "real_session"


def find_garbage(include_trash=False):
    """
    削除対象のゴミセッションおよび関連孤立ファイルを全走査して収集する。
    戻り値:
        real_sessions: 保存すべき正規セッションのリスト
        garbage_sessions: 削除対象セッションのリスト (dict)
        related_files: 削除対象の関連ファイル一覧 (path)
        orphan_files: 正規セッションに紐づかない孤立ファイル一覧 (path)
    """
    real_sessions = []
    garbage_sessions = []
    real_sids = set()
    garbage_sids = set()

    # 1. sessions スキャン
    sess_files = glob.glob(os.path.join(SESS_DIR, "*.json"))
    for f in sess_files:
        sid = os.path.splitext(os.path.basename(f))[0]
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception as e:
            garbage_sessions.append({
                "sid": sid,
                "file": f,
                "title": "(破損ファイル)",
                "msgs": 0,
                "reason": f"read_error: {e}"
            })
            garbage_sids.add(sid)
            continue

        is_garbage, reason = is_test_session(sid, data)
        setting = data.get("setting") if isinstance(data.get("setting"), dict) else {}
        title = setting.get("title") or data.get("title") or "(無題)"
        msgs = data.get("messages", [])
        mcount = len(msgs) if isinstance(msgs, list) else 0

        info = {
            "sid": sid,
            "file": f,
            "title": title,
            "msgs": mcount,
            "mtime": datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason
        }

        if is_garbage:
            garbage_sessions.append(info)
            garbage_sids.add(sid)
        else:
            real_sessions.append(info)
            real_sids.add(sid)

    # 2. ゴミセッションに紐づく関連ファイル収集
    related_files = []
    # sessions そのもの
    for gs in garbage_sessions:
        related_files.append(gs["file"])

    # 他のデータディレクトリ走査
    scan_dirs = [
        (RAG_DIR, "*_memories.json"),
        (SENT_DIR, "*.json"),
        (CHOICES_DIR, "*.json"),
        (STATES_DIR, "*.json"),
        (IMAGES_DIR, "*.json"),
        (CTX_DIR, "*"),
        (OUTBOX_DIR, "*"),
    ]

    orphan_files = []

    for dpath, pat in scan_dirs:
        if not os.path.exists(dpath):
            continue
        for f in glob.glob(os.path.join(dpath, pat)):
            if not os.path.isfile(f):
                continue
            base = os.path.basename(f)
            # sid を推定: [sid]_... or [sid].json
            token = base.split("_")[0].split(".")[0]
            if token in garbage_sids:
                related_files.append(f)
            elif token not in real_sids:
                # どの正規セッションにも紐づかない孤児ファイル
                # （ただし設定ファイルなどの特定固定名は除く）
                if base not in ("agents_md.json", "ide_moves.json", "vapid_keys.json", "push_subscriptions.json", "settings.json", "server.json", "decensor.json", "tag_dictionary.json"):
                    orphan_files.append(f)

    # session フォルダ下のゴミチャンネルフォルダ・ファイル
    if os.path.exists(SESSION_FOLDER):
        for item in os.listdir(SESSION_FOLDER):
            item_path = os.path.join(SESSION_FOLDER, item)
            token = item.split("_")[0].split(".")[0]
            if token in garbage_sids:
                related_files.append(item_path)
            elif token not in real_sids and token != "choices" and not item.startswith("."):
                orphan_files.append(item_path)

    # trash ディレクトリ内のテスト残骸
    trash_files = []
    if include_trash and os.path.exists(TRASH_DIR):
        for f in glob.glob(os.path.join(TRASH_DIR, "*")):
            if os.path.isfile(f):
                base = os.path.basename(f)
                token = base.split("_")[0]
                if token in garbage_sids or token.startswith("test_") or token.startswith("sid_") or token.startswith("sid-"):
                    trash_files.append(f)
                elif token not in real_sids:
                    trash_files.append(f)

    return real_sessions, garbage_sessions, related_files, orphan_files, trash_files


def create_backup(files_to_backup):
    """削除対象ファイルを zip アーカイブとしてバックアップする"""
    if not files_to_backup:
        return None
    os.makedirs(DATA_DIR, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(DATA_DIR, f"_cleanup_backup_{ts_str}.zip")

    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files_to_backup:
            if os.path.isfile(f):
                rel = os.path.relpath(f, ROOT_DIR)
                zf.write(f, rel)
            elif os.path.isdir(f):
                for root, _, sfiles in os.walk(f):
                    for sf in sfiles:
                        sp = os.path.join(root, sf)
                        rel = os.path.relpath(sp, ROOT_DIR)
                        zf.write(sp, rel)
    return backup_path


def perform_cleanup(target_files):
    """ファイルを実際に安全に削除する"""
    deleted_files = []
    errors = []
    for f in target_files:
        try:
            if os.path.isfile(f):
                os.remove(f)
                deleted_files.append(f)
            elif os.path.isdir(f):
                shutil.rmtree(f, ignore_errors=True)
                deleted_files.append(f)
        except Exception as e:
            errors.append((f, str(e)))
    return deleted_files, errors


def main():
    parser = argparse.ArgumentParser(description="Clean up garbage test sessions and related files.")
    parser.add_argument("--dry-run", action="store_true", help="Preview files to be deleted without removing them.")
    parser.add_argument("--force", action="store_true", help="Execute cleanup without interactive confirmation prompt.")
    parser.add_argument("--include-trash", action="store_true", help="Also clean up test remnants in data/trash.")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating backup zip before deletion.")
    args = parser.parse_args()

    print("==================================================")
    print("   rp-image-chat テスト用ゴミデータお掃除ツール")
    print("==================================================")

    real, garbage, related, orphans, trash = find_garbage(include_trash=args.include_trash)

    print(f"\n[スキャン結果]")
    print(f"  正規チャンネル（保護対象）: {len(real)} 件")
    print(f"  ゴミチャンネル（削除対象）: {len(garbage)} 件")
    print(f"  紐づく関連ファイル:         {len(related)} 件")
    print(f"  孤立した残骸ファイル:       {len(orphans)} 件")
    if args.include_trash:
        print(f"  trash 内の残骸ファイル:     {len(trash)} 件")

    print("\n--- 保護される正規チャンネル ---")
    for r in sorted(real, key=lambda x: x["title"]):
        print(f"  [保護] ID: {r['sid']} | タイトル: {r['title']} ({r['msgs']} 件の発言)")

    all_targets = sorted(list(set(related + orphans + trash)))

    if not all_targets:
        print("\n掃除対象のゴミファイルは見つかりませんでした。データは綺麗です！")
        return 0

    print(f"\n--- 削除対象のゴミセッション (代表例) ---")
    for g in garbage[:15]:
        print(f"  [削除] ID: {g['sid']} | タイトル: {g['title']} | 理由: {g['reason']}")
    if len(garbage) > 15:
        print(f"  ... 他 {len(garbage) - 15} 件のゴミセッション")

    print(f"\n合計 {len(all_targets)} 件のファイルを削除対象として検出しました。")

    if args.dry_run:
        print("\n[DRY RUN モード] 削除は実行されませんでした。")
        print("実際に削除を実行するには、引数なしで実行するか --force を指定してください。")
        return 0

    if not args.force:
        confirm = input(f"\nこれら {len(all_targets)} 件のゴミファイルを削除しますか？ (y/N): ").strip()
        if confirm.lower() not in ("y", "yes"):
            print("キャンセルしました。")
            return 0

    # バックアップ作成
    if not args.no_backup:
        print("\n削除前にバックアップを作成しています...")
        bk_path = create_backup(all_targets)
        print(f"バックアップ作成完了: {bk_path}")

    # 削除実行
    print("ゴミファイルを削除中...")
    del_files, errs = perform_cleanup(all_targets)
    print(f"完了: {len(del_files)} 件のファイルを削除しました。")

    if errs:
        print(f"\n警告: {len(errs)} 件の削除中にエラーが発生しました:")
        for ep, err in errs[:5]:
            print(f"  {ep}: {err}")

    print("\n掃除が正常に完了しました！")
    return 0


if __name__ == "__main__":
    sys.exit(main())

