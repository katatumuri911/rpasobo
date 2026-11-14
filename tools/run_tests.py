# -*- coding: utf-8 -*-
"""テスト実行スクリプト（唯一の入口）。

使い方:
    python tools/run_tests.py                        # 通常（低速テストはスキップ）
    python tools/run_tests.py -v                     # 各テスト名を表示
    RPCHAT_SLOW_TESTS=1 python tools/run_tests.py    # 実データを走査する低速テストも実行

- test/test_*.py を discover して実行する
- 収集0件のテストモジュールを検出して失敗扱いにする
  （TestCase を継承していない・メソッド名が test_ で始まらない等で、
   テストが静かに実行されないまま増える事故を防ぐ）
"""
import os
import sys
import time
import unittest
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.join(ROOT, "test")


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main(argv):
    # 日本語のテスト出力で cp932 端末が落ちないようにする
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)

    stems = sorted(
        f[:-3] for f in os.listdir(TEST_DIR)
        if f.startswith("test_") and f.endswith(".py")
    )
    suite = unittest.TestLoader().discover(start_dir=TEST_DIR, pattern="test_*.py")
    counts = Counter(t.__class__.__module__ for t in flatten(suite))
    total = sum(counts.values())
    slow = os.environ.get("RPCHAT_SLOW_TESTS") == "1"

    print(f"テスト収集: {len(stems)} ファイル / {total} テスト"
          f"（低速テスト: {'実行' if slow else 'スキップ'}）")

    empty = [s for s in stems if counts.get(s, 0) == 0]
    for s in empty:
        print(f"[NG] 収集0件: test/{s}.py — TestCase を継承しているか、メソッド名が test_ で始まるか確認")

    runner = unittest.TextTestRunner(verbosity=2 if "-v" in argv else 1)
    started = time.time()
    result = runner.run(suite)
    elapsed = time.time() - started

    # テスト後に本番 data/ にテスト用ゴミファイルが残留していないか検知・自動クリーンアップ
    cleanup_msg = ""
    try:
        from tools import cleanup_test_data
        real, garbage, related, orphans, trash = cleanup_test_data.find_garbage(include_trash=False)
        targets = sorted(list(set(related + orphans)))
        if targets:
            del_files, _ = cleanup_test_data.perform_cleanup(targets)
            sample_names = ", ".join(os.path.basename(f) for f in del_files[:3])
            more = f" 他 {len(del_files) - 3} 件" if len(del_files) > 3 else ""
            cleanup_msg = f"\n[自動掃除] テストで本番環境に生成されたゴミファイル {len(del_files)} 件 ({sample_names}{more}) を自動削除しました。"
    except Exception as e:
        cleanup_msg = f"\n[自動掃除エラー] {e}"

    ok = result.wasSuccessful() and not empty
    print(f"\n結果: {'OK' if ok else 'NG'} / {elapsed:.1f} 秒 / "
          f"{result.testsRun - len(result.skipped)} 実行 / スキップ {len(result.skipped)} 件{cleanup_msg}")
    if not slow:
        print("ヒント: 実データを走査する低速テストは RPCHAT_SLOW_TESTS=1 で実行します")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
