# -*- coding: utf-8 -*-
"""応答遅延・コンテキスト汚染修正計画の包括的検証テストスイート"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import memory_store as ms
import server
import ide_poke
from test.support import NoWaitTime


def make_msg(role, text, mid=None, **kwargs):
    m = {"id": mid or server.now(), "role": role, "text": text, "ts": server.now()}
    m.update(kwargs)
    return m


class TestMemoryStoreZeroBudgetAndTruthy(unittest.TestCase):
    """1. memory_store.py の Truthy バグ解消とゼロ予算・保護層死守テスト"""

    def setUp(self):
        self.orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)

    def test_soft_budget_zero_returns_empty_and_floor_hit(self):
        """soft_budget=0 で記憶が空文字列となり floor_hit=True となること"""
        mems = [
            {"id": "r1", "category": "rule", "content": "催眠暗示のルール", "score": 0.95},
            {"id": "c1", "category": "calling", "content": "ラピの呼称", "score": 0.90},
            {"id": "e1", "category": "event", "content": "過去の思い出", "score": 0.85},
        ]
        stats = {}
        res = ms.format_prompt_memories(mems, current_turn=1, stats=stats, soft_budget=0)
        self.assertEqual("", res)
        self.assertEqual(0, stats["bytes"])
        self.assertEqual(0, stats["rules"])
        self.assertEqual(0, stats["calling"])
        self.assertEqual(0, stats["recall"])
        self.assertTrue(stats["floor_hit"])

    def test_env_soft_budget_zero_returns_empty(self):
        """環境変数 RPCHAT_MEM_SOFT_BUDGET=0 でも空文字列が返ること"""
        os.environ["RPCHAT_MEM_SOFT_BUDGET"] = "0"
        mems = [
            {"id": "e1", "category": "event", "content": "イベント記憶", "score": 0.9},
        ]
        stats = {}
        res = ms.format_prompt_memories(mems, current_turn=1, stats=stats)
        self.assertEqual("", res)
        self.assertEqual(0, stats["recall"])
        self.assertTrue(stats["floor_hit"])

    def test_recall_budget_zero_keeps_rules_and_pinned_calling_drops_unpinned_calling_and_pool(self):
        """recall_budget=0 で想起プールと非ピン留め呼称が0件となり、ルールおよびピン留め呼称のみが出力されること"""
        mems = [
            {"id": "r1", "category": "rule", "content": "絶対遵守ルール", "score": 0.95},
            {"id": "c1", "category": "calling", "content": "指揮官", "score": 0.90},
            {"id": "c_pin", "category": "calling", "content": "マスター", "score": 0.92, "priority": "pinned"},
            {"id": "e1", "category": "event", "content": "昨日の出来事", "score": 0.85},
            {"id": "e2", "category": "event", "content": "過去の約束", "score": 0.80},
        ]
        stats = {}
        res = ms.format_prompt_memories(mems, current_turn=1, stats=stats, recall_budget=0, soft_budget=1500)
        self.assertIn("絶対遵守ルール", res)
        self.assertIn("マスター", res)
        self.assertNotIn("指揮官", res)
        self.assertNotIn("昨日の出来事", res)
        self.assertNotIn("過去の約束", res)
        self.assertEqual(1, stats["rules"])
        self.assertEqual(1, stats["calling"])
        self.assertEqual(2, stats["tier1"])
        self.assertEqual(0, stats["recall"])

    def test_trim_down_to_zero_pool_when_rules_exceed_soft(self):
        """ルールだけで soft_budget を超過する場合、可変層 pool は 0 件まで削られること"""
        mems = [
            {"id": "r1", "category": "rule", "content": "非常に長いルール内容その1" * 8, "score": 0.95},
            {"id": "r2", "category": "rule", "content": "非常に長いルール内容その2" * 8, "score": 0.90},
            {"id": "e1", "category": "event", "content": "出来事", "score": 0.85},
        ]
        stats = {}
        # soft_budget=250: r1+r2 で 300B を超える
        res = ms.format_prompt_memories(mems, current_turn=1, stats=stats, soft_budget=250)
        self.assertEqual(2, stats["rules"])
        self.assertEqual(0, stats["recall"])
        self.assertTrue(stats["floor_hit"])
        self.assertIn("その1", res)
        self.assertIn("その2", res)
        self.assertNotIn("出来事", res)


class TestServerSentTransactionAndStop(unittest.TestCase):
    """2. server.py の sent トランザクション管理、未完了再実行、Stop時のロールバック検証"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_lapi_srv_")
        self.orig_dirs = {}
        for name in ("DATA", "SESS_DIR", "STATES_DIR", "IMAGES_DIR", "CHOICES_DIR", "RAG_DIR", "SENT", "CTX", "TRASH", "OUTBOX", "DONE"):
            self.orig_dirs[name] = getattr(server, name, None)
            path = os.path.join(self.tmp, name.lower())
            os.makedirs(path, exist_ok=True)
            setattr(server, name, path)
        self.orig_ms_dirs = (ms.DATA, ms.RAG_DIR, ms.TRASH)
        ms.DATA = self.tmp
        ms.RAG_DIR = os.path.join(self.tmp, "rag")
        ms.TRASH = server.TRASH
        os.makedirs(ms.RAG_DIR, exist_ok=True)

    def tearDown(self):
        for k, v in self.orig_dirs.items():
            if v is not None:
                setattr(server, k, v)
        ms.DATA, ms.RAG_DIR, ms.TRASH = self.orig_ms_dirs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inflight_and_prev_sent_saved_in_build_context(self):
        """build_context 実行時に inflight_mid と prev_sent が正しく保存されること"""
        s = server.create_session({"title": "Txテスト", "ide_session": "ide-1"})
        m0 = make_msg("user", "1ターン目", mid="u0")
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)

        sent0 = server.read_sent(s["id"])
        self.assertEqual("u0", sent0.get("inflight_mid"))
        self.assertIsInstance(sent0.get("prev_sent"), dict)

        # note_reply で確定
        a0 = make_msg("assistant", "応答0", mid="a0")
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)

        sent0_committed = server.read_sent(s["id"])
        self.assertIsNone(sent0_committed.get("inflight_mid"))
        self.assertIsNone(sent0_committed.get("prev_sent"))
        self.assertFalse(sent0_committed.get("reset"))

    def test_uncompleted_turn_retry_recovers_from_prev_sent(self):
        """Turn 2 が未完了のまま再実行された場合、prev_sent から安全に復元され差分モードが維持されること"""
        s = server.create_session({"title": "Retryテスト", "ide_session": "ide-1"})
        m0 = make_msg("user", "1ターン目", mid="u0")
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = make_msg("assistant", "応答0", mid="a0")
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)

        # Turn 2 実行
        m1 = make_msg("user", "2ターン目", mid="u1")
        s["messages"].append(m1)
        server.save(s)
        ctx1 = server.build_context(s, m1)
        self.assertEqual("diff", ctx1["mode"])

        sent1 = server.read_sent(s["id"])
        self.assertEqual("u1", sent1.get("inflight_mid"))
        # u1 のメッセージシグネチャが含まれている
        self.assertEqual(len(sent1["msgs"]), 3)  # u0, a0, u1

        # ここで note_reply を呼ばずに再度 build_context(s, m1) が呼ばれたとする（リトライ）
        ctx1_retry = server.build_context(s, m1)
        # 差分モードが維持され、破壊されないこと
        self.assertEqual("diff", ctx1_retry["mode"])

    def test_idless_message_does_not_restore_stale_prev_sent(self):
        """idなしメッセージでは inflight_mid=None でも prev_sent を誤復元しない（L-05）"""
        s = server.create_session({"title": "L05", "ide_session": "ide-1"})
        s["messages"].append(make_msg("user", "1ターン目", mid="u0"))
        server.save(s)
        stale = {"reset": True, "why": "STALE_PREV_SENT", "msgs": []}
        fake_sent = {"inflight_mid": None, "prev_sent": stale}
        with patch.object(server, "read_sent", return_value=fake_sent):
            ctx = server.build_context(s, {"role": "user", "text": "idなし"})
        self.assertNotIn("STALE_PREV_SENT", ctx.get("reason", ""))

    def test_rollback_sent_on_abort_restores_prev_sent(self):
        """Stop実行時に rollback_sent_on_abort が sent を前ターンの確定状態へ戻し reset: False を維持すること"""
        s = server.create_session({"title": "StopRollbackテスト", "ide_session": "ide-1"})
        m0 = make_msg("user", "1ターン目", mid="u0")
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = make_msg("assistant", "応答0", mid="a0")
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)

        committed_sent = copy.deepcopy(server.read_sent(s["id"]))

        # Turn 2 実行開始
        m1 = make_msg("user", "2ターン目", mid="u1", status="processing")
        s["messages"].append(m1)
        server.save(s)
        server.build_context(s, m1)

        # ユーザーが送信停止 (rollback_sent_on_abort)
        server.rollback_sent_on_abort(s["id"], "u1")

        restored_sent = server.read_sent(s["id"])
        # 前ターンの確定状態に戻っていること
        self.assertEqual(committed_sent["msgs"], restored_sent["msgs"])
        self.assertFalse(restored_sent.get("reset"))
        self.assertIsNone(restored_sent.get("inflight_mid"))

    def test_card_skipped_when_headroom_tight_or_memo_due(self):
        """余白不足時または memo_due 時に定期カード再送がスキップされること"""
        s = server.create_session({"title": "CardSkipテスト", "ide_session": "ide-1"})
        m0 = make_msg("user", "初期化", mid="u0")
        s["messages"].append(m0)
        server.save(s)
        server.build_context(s, m0)
        a0 = make_msg("assistant", "初期化応答", mid="a0")
        s["messages"].append(a0)
        server.note_reply(s, "u0", a0)

        # other_bytes が大きくなるように長い発言
        m1 = make_msg("user", "あ" * 2500, mid="u1")
        s["messages"].append(m1)
        server.save(s)

        ctx = server.build_context(s, m1)
        # 余白不足のため CARD_KEYS (概要・指示など) が post_rag_sections に追加されていないこと
        card_labels = [sec["label"] for sec in ctx["sections"] if "概要" in sec["label"] or "指示" in sec["label"]]
        self.assertEqual(0, len(card_labels))


class TestIdePokeRevertAndCache(unittest.TestCase):
    """3. ide_poke.py のキャッシュ最適化と revert_when_idle テスト"""

    def test_cid_cfg_cache_hits_without_step_idx_mismatch(self):
        """cid 単位でキャッシュされ、step_idx が進んでもローカルRPCを行わずにヒットすること"""
        ide_poke._cid_cfg.clear()
        mock_cfg = {"plannerConfig": {"planModel": "models/gemini-2.0-flash"}}
        ide_poke._cid_cfg["test-cid-1"] = copy.deepcopy(mock_cfg)

        with patch("ide_poke.locate", return_value=(1234, "tok", {"summary": "s", "status": "IDLE", "lastUserInputStepIndex": 5})), \
             patch("ide_poke._fix_model", return_value=""), \
             patch("ide_poke._model_data", return_value=([], None)), \
             patch("ide_poke._summary", return_value={"status": "IDLE", "lastUserInputTime": "new"}), \
             patch("ide_poke._call", return_value=(200, {})):
            ok, msg, retry = ide_poke.send_to_conversation("test-cid-1", text="test")
            self.assertTrue(ok)
            # キャッシュが維持されていること
            self.assertIn("test-cid-1", ide_poke._cid_cfg)

    def test_cid_cfg_cache_cleared_on_call_error(self):
        """SendUserCascadeMessage が 200 以外を返した場合はキャッシュが破棄されること"""
        ide_poke._cid_cfg["test-cid-err"] = {"plannerConfig": {"planModel": "models/gemini-2.0-flash"}}
        with patch("ide_poke.locate", return_value=(1234, "tok", {"summary": "s", "status": "IDLE", "lastUserInputStepIndex": 5})), \
             patch("ide_poke._fix_model", return_value=""), \
             patch("ide_poke._model_data", return_value=([], None)), \
             patch("ide_poke._call", return_value=(500, "Internal Server Error")):
            ok, msg, retry = ide_poke.send_to_conversation("test-cid-err", text="test")
            self.assertFalse(ok)
            self.assertNotIn("test-cid-err", ide_poke._cid_cfg)

    def test_revert_when_idle_waits_for_idle_status(self):
        """revert_when_idle が会話が IDLE になるまで待機してから revert を呼ぶこと"""
        statuses = ["RUNNING", "RUNNING", "IDLE"]
        def mock_locate(cid):
            st = statuses.pop(0) if statuses else "IDLE"
            return 1234, "tok", {"status": st, "stepCount": 10}

        with patch("ide_poke.locate", side_effect=mock_locate), \
             patch("ide_poke.revert", return_value=(True, "reverted")) as mock_revert, \
             patch.object(ide_poke, "time", NoWaitTime()):  # IDLE 待ちの 0.4 秒ポーリングは実時間を使わない
            ok, msg = ide_poke.revert_when_idle("test-cid-idle", at=4, timeout=5)
            self.assertTrue(ok)
            self.assertEqual("reverted", msg)
            mock_revert.assert_called_once()


class TestStopApiBehavior(unittest.TestCase):
    """4. /api/send/stop の target_seen ガードと非同期 Revert テスト"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_stop_api_")
        self.orig_data = server.DATA
        self.orig_sess = server.SESS_DIR
        server.DATA = self.tmp
        server.SESS_DIR = os.path.join(self.tmp, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "sent"), exist_ok=True)

    def tearDown(self):
        server.DATA = self.orig_data
        server.SESS_DIR = self.orig_sess
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_trigger_async_revert_with_lock(self):
        """trigger_async_revert が reverting セットを適切に操作し非同期スレッドを呼ぶこと"""
        called = []
        def mock_revert_when_idle(cid, at, backup_dir=None, timeout=45):
            self.assertIn(cid, server.reverting)
            called.append((cid, at))
            return True, "ok"

        with patch("ide_poke.revert_when_idle", side_effect=mock_revert_when_idle):
            server.trigger_async_revert("ide-test-rev", target_step=3)
            # バックグラウンドスレッド完了を待機
            for _ in range(50):
                if called:
                    break
                time.sleep(0.05)
            self.assertEqual([("ide-test-rev", 3)], called)
            self.assertNotIn("ide-test-rev", server.reverting)


class TestWaitInputInstructions(unittest.TestCase):
    """5. wait_input.py のツール使用指示文テスト"""

    def setUp(self):
        import wait_input
        self.tmp = tempfile.mkdtemp(prefix="test_wait_ctx_")
        self.orig_ctx = wait_input.CTX_DIR
        wait_input.CTX_DIR = self.tmp

    def tearDown(self):
        import wait_input
        wait_input.CTX_DIR = self.orig_ctx
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wait_input_inline_instruction(self):
        """通常インライン時（must_spill=False, files=[]）の厳格なツール禁止指示"""
        import io
        import wait_input
        ctx = {
            "title": "テスト",
            "mode": "reply",
            "sections": [{"label": "ユーザーの発言", "text": "こんにちは"}],
            "compact": True,
            "rules": None,
            "same": [],
            "files": [],
        }
        m = {"id": "m1", "text": "こんにちは"}
        out = io.StringIO()
        with patch("sys.stdout", out):
            try:
                wait_input.show("sid_test", m, ctx=ctx)
            except SystemExit:
                pass
        printed = out.getvalue()
        self.assertIn("追加の run_command や read_file は一切使わず", printed)
        self.assertIn("outbox/done や server.py 等の検索・閲覧は厳禁", printed)
        self.assertIn("過去にエラーや応答ブロックがあっても原因調査は一切行わないこと", printed)

    def test_wait_input_spill_instruction(self):
        """退避時（must_spill=True）の限定ファイル閲覧指示"""
        import io
        import wait_input
        # 10,000バイト以上の巨大テキストで must_spill を誘発
        ctx = {
            "title": "テスト",
            "mode": "reply",
            "sections": [{"label": "巨大発言", "text": "あ" * 4000}],
            "compact": True,
            "rules": None,
            "same": [],
            "files": [],
        }
        m = {"id": "m2", "text": "あ" * 4000}
        out = io.StringIO()
        with patch("sys.stdout", out):
            try:
                wait_input.show("sid_test2", m, ctx=ctx)
            except SystemExit:
                pass
        printed = out.getvalue()
        self.assertIn("この表示で明示されたファイルパスを読む以外のファイル閲覧・検索", printed)
        self.assertIn("Get-ChildItem / dir 等）は絶対に行わず", printed)


if __name__ == "__main__":
    unittest.main()
