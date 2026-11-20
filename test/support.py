# -*- coding: utf-8 -*-
"""test/support.py: 本番環境・設定ファイル・グローバル状態を完全隔離するテスト基盤"""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import server
import memory_store
import ide_poke

# テンポラリ配下にディレクトリとして作成・隔離する対象（17件）
PATHS = (
    "DATA", "SESS_DIR", "CHOICES_DIR", "STATES_DIR", "IMAGES_DIR", "RAG_DIR",
    "SENT", "CTX", "ARCHIVE_DIR", "TRASH", "OUTBOX", "DONE", "UPLOADS",
    "NEW_IDE_DIR", "WIZARD_DIR", "WIZARD_IDE_DIR", "REVERT_BACKUP"
)

# テンポラリ data 配下にファイルとして配置・隔離する対象（12件）
FILES = (
    "APP_SETTINGS", "PRESETS", "CHAR_PRESETS", "WRITING_STYLE", "LEGACY",
    "RUNNING", "AGENTS_LOG", "IDE_MOVES", "VAPID_FILE", "PUSH_SUBS",
    "IDE_KNOWN", "TAG_DICT_PATH"
)

# clear() のみ行う派生インメモリキャッシュ群（11件）
CACHES = (
    "session_cache", "summary_cache", "choices_cache", "states_cache",
    "images_cache", "sent_cache", "session_ide_cache", "msg_to_session",
    "session_revs", "google_status", "wizard_model_cache"
)

# 退避 → clear → 復元 を行う状態変数群（7件）
STATEFUL = (
    "deleted_sessions", "repoke_tasks", "google_inflight", "rotation_queue",
    "ide_waiting", "_new_ide_in_progress", "reverting"
)

# clear() のみ行うキュー（deque）群（3件）
QUEUES = ("queued_images", "queued_memories", "_choice_notice_q")

# clear() のみ行う同期イベント群（7件）
EVENTS = (
    "poke_event", "google_event", "image_event", "memory_event",
    "rotation_event", "ide_auto_event", "_choice_notice_event"
)


def add_user_msg(s, text, status="pending"):
    """テスト用: セッションにユーザー発言を追加してその dict を返す"""
    m = {
        "id": server.new_id(),
        "role": "user",
        "text": text,
        "ts": server.now(),
        "status": status,
    }
    s.setdefault("messages", []).append(m)
    return m


def create_ide_poke_mock():
    """実測アリティ・演算子・戻り値型を 100% 整合させた ide_poke Mock を生成する"""
    stub = mock.Mock(spec=ide_poke)

    # 実体定数の保存（|= 演算や正規表現呼び出しでの TypeError / Mock汚染を防止）
    stub.FORK_TAG = ide_poke.FORK_TAG
    stub.AVOID_BORROW = set()

    # 正しい戻り値アリティと型
    stub.poke.return_value = (True, "")
    stub.locate.return_value = (None, None, None)  # 3要素タプル。第1要素が None でないと alive 判定される
    stub.new_conversation.return_value = ("mock_cid", "")
    stub.send_to_conversation.return_value = (True, "", False)
    stub.idle.return_value = (True, "CASCADE_RUN_STATUS_IDLE")
    stub.last_reply.return_value = ("mock reply", None, "ok")
    stub.summary.return_value = None
    stub.progress.return_value = ({"items": []}, None)  # 2値タプル
    stub.turns.return_value = ({}, [])                  # 2値タプル
    stub.fork.return_value = ("mock_fork_cid", "")
    stub.tool_usage_since.return_value = {"total": 0, "read_file": False, "names": [], "ok": False}
    stub.errors_since.return_value = []
    stub.models.return_value = ({"models": [], "default": ""}, None)
    stub.rename.return_value = (True, "")
    stub.revert.return_value = (True, "")
    stub.revert_when_idle.return_value = (True, "")
    # ★重要: move_workspaces は辞書を返す必要がある（server.py 内で res.items() が走るため）
    stub.move_workspaces.return_value = ({}, "")
    stub.busy_conversations.return_value = []
    return stub


class IsolatedTestCase(unittest.TestCase):
    """全パス・ファイル・キャッシュ・グローバル状態をテンポラリに完全隔離する基底テストクラス"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="rp_isolated_")
        self._orig_paths = {}
        self._orig_files = {}
        self._orig_stateful = {}
        self._orig_known_ides = server.known_ides
        self._orig_ms_data = memory_store.DATA
        self._orig_ms_rag = memory_store.RAG_DIR
        self._orig_ms_trash = memory_store.TRASH
        self._orig_ms_deleted_sessions = set(memory_store._deleted_sessions)
        self._env = os.environ.copy()

        temp_data = os.path.join(self.test_dir, "data")
        os.makedirs(temp_data, exist_ok=True)

        # 1. ディレクトリパスの隔離設定
        for n in PATHS:
            orig = getattr(server, n, None)
            self._orig_paths[n] = orig
            if not orig:
                continue
            if n == "DATA":
                p = temp_data
            elif n in ("OUTBOX", "NEW_IDE_DIR"):
                p = os.path.join(self.test_dir, os.path.basename(orig))
            elif n == "DONE":
                p = os.path.join(self.test_dir, "outbox", "done")
            elif n == "REVERT_BACKUP":
                p = os.path.join(temp_data, "trash", "ide_revert")
            else:
                p = os.path.join(temp_data, os.path.basename(orig))
            os.makedirs(p, exist_ok=True)
            setattr(server, n, p)

        # 2. ファイルパスの隔離設定（basename を維持してテンポラリ data 配下へ）
        for n in FILES:
            orig = getattr(server, n, None)
            self._orig_files[n] = orig
            if orig:
                p = os.path.join(temp_data, os.path.basename(orig))
                setattr(server, n, p)

        # 3. memory_store のパス隔離とキャッシュクリア
        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = server.RAG_DIR
        memory_store.TRASH = server.TRASH
        memory_store._memory_cache.clear()
        memory_store._query_cache.clear()
        memory_store._deleted_sessions.clear()

        # 4. キャッシュのクリア（退避しない）
        for c in CACHES:
            getattr(server, c).clear()

        # 5. 状態変数の退避とクリア
        for s_name in STATEFUL:
            obj = getattr(server, s_name)
            self._orig_stateful[s_name] = (obj, set(obj) if isinstance(obj, set) else dict(obj))
            obj.clear()

        server.known_ides = None  # モジュールキャッシュを None に初期化（テスト用 IDE_KNOWN を参照させる）

        # 6. キューとイベントのクリア
        for q_name in QUEUES:
            getattr(server, q_name).clear()
        for ev_name in EVENTS:
            getattr(server, ev_name).clear()

        # 7. 環境変数汚染防止
        os.environ["RPCHAT_PERF_LOG"] = "0"
        os.environ["RPCHAT_COMPACT"] = "1"

        # 8. ide_poke モックの注入
        self.mock_poke = create_ide_poke_mock()
        self._orig_ide_poke = server.ide_poke
        server.ide_poke = self.mock_poke

        self.add_user_msg = add_user_msg
        server.add_user_msg = add_user_msg

        self.assertTmp()

    def tearDown(self):
        if hasattr(server, "add_user_msg"):
            delattr(server, "add_user_msg")

        # 1. ide_poke 復元
        server.ide_poke = self._orig_ide_poke

        # 2. パス・ファイルの復元
        for n, p in self._orig_paths.items():
            if p is not None:
                setattr(server, n, p)
        for n, p in self._orig_files.items():
            if p is not None:
                setattr(server, n, p)

        # 3. memory_store の復元
        memory_store.DATA = self._orig_ms_data
        memory_store.RAG_DIR = self._orig_ms_rag
        memory_store.TRASH = self._orig_ms_trash
        memory_store._memory_cache.clear()
        memory_store._query_cache.clear()
        memory_store._deleted_sessions.clear()
        memory_store._deleted_sessions.update(self._orig_ms_deleted_sessions)

        # 4. キャッシュのクリア
        for c in CACHES:
            getattr(server, c).clear()

        # 5. 状態変数の復元
        for s_name, (obj, saved) in self._orig_stateful.items():
            obj.clear()
            obj.update(saved)

        server.known_ides = self._orig_known_ides

        # 6. キューとイベントのクリア
        for q_name in QUEUES:
            getattr(server, q_name).clear()
        for ev_name in EVENTS:
            getattr(server, ev_name).clear()

        # 7. 環境変数の復元
        os.environ.clear()
        os.environ.update(self._env)

        # 8. テンポラリディレクトリの削除
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def assertTmp(self):
        """全定数が現在テンポラリディレクトリ内を指していることを厳格検証"""
        root = os.path.realpath(self.test_dir)
        for n in PATHS:
            val = getattr(server, n, None)
            if val:
                self.assertTrue(os.path.realpath(val).startswith(root), f"{n} が tmp 外: {val}")
        for n in FILES:
            val = getattr(server, n, None)
            if val:
                self.assertTrue(os.path.realpath(val).startswith(root), f"{n} が tmp 外: {val}")
        self.assertTrue(os.path.realpath(memory_store.DATA).startswith(root))
        self.assertTrue(os.path.realpath(memory_store.RAG_DIR).startswith(root))
        self.assertTrue(os.path.realpath(memory_store.TRASH).startswith(root))


class NoWaitTime:
    """`time` モジュール互換のスタブ。sleep だけ無効化し、他（time()/strftime() 等）は本物へ委譲する。

    実装側の「少し待ってから再確認する」ループ（IDE の IDLE 待ち、outbox の取り込み待ちなど）を、
    待ち時間そのものを検証しないテストで実時間を消費せずに通すためのもの。
    使い方: `with mock.patch.object(ide_poke, "time", NoWaitTime()):`
    """

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, *_args, **_kwargs):
        return None
