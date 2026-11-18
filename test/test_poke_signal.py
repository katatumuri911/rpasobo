# -*- coding: utf-8 -*-
"""合図（poke）の内容の回帰テスト。

守りたい仕様:
  1. wait_input.py をまだ実行したことのない会話（ide_known でない）には、コマンド全文を送る。
  2. 一度でも実行した会話には半角スペース1文字で足りる。**選択肢専用会話も同じ基準**
     （以前は「選択肢タスクは毎回フルコマンド」という例外があり、選択肢を出すたびに
      同じコマンド全文が会話に積まれていた。server.py の dispatch_poke を参照）。
  3. フルコマンドを送ったタスクは unclaimed（POKE_AGAIN 秒後の再送）の対象にしない。
     再送は「スペースの合図が効かなかった」ときのための1回だけの保険。
"""
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server


class _StopLoop(Exception):
    """poke_worker の無限ループをテストから抜けるための番兵"""


class _StepEvent:
    """poke_worker が使う poke_event の代用。steps 回だけループを回してから止める。"""

    def __init__(self, steps=1, gap=0.0):
        self.steps = steps
        self.gap = gap
        self.calls = 0

    def wait(self, timeout=None):
        self.calls += 1
        if self.calls > self.steps:
            raise _StopLoop()
        if self.calls > 1 and self.gap:
            time.sleep(self.gap)  # quiet（合図直後の1.5秒）を明けて次の周回に入る
        return True

    def clear(self):
        pass


class TestPokeSignal(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_poke_signal_")
        self.orig = {
            "DATA": server.DATA, "SENT": server.SENT, "SESS_DIR": server.SESS_DIR,
            "CHOICES_DIR": server.CHOICES_DIR, "IMAGES_DIR": server.IMAGES_DIR,
            "STATES_DIR": server.STATES_DIR, "CTX": server.CTX, "TRASH": server.TRASH,
            "OUTBOX": server.OUTBOX, "DONE": server.DONE, "AGENTS_LOG": server.AGENTS_LOG,
            "NEW_IDE_DIR": server.NEW_IDE_DIR, "IDE_KNOWN": server.IDE_KNOWN,
            "known_ides": server.known_ides, "poke_event": server.poke_event,
            "ide_poke": server.ide_poke, "POKE_MODE": server.POKE_MODE,
            "POKE_AGAIN": server.POKE_AGAIN, "POKE_DELAY": server.POKE_DELAY,
        }
        server.DATA = os.path.join(self.test_dir, "data")
        server.SENT = os.path.join(server.DATA, "sent")
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        server.STATES_DIR = os.path.join(server.DATA, "states")
        server.CTX = os.path.join(server.DATA, "ctx")
        server.TRASH = os.path.join(server.DATA, "trash")
        for d in (server.SENT, server.SESS_DIR, server.CHOICES_DIR, server.IMAGES_DIR,
                  server.STATES_DIR, server.CTX, server.TRASH):
            os.makedirs(d, exist_ok=True)
        server.AGENTS_LOG = os.path.join(server.DATA, "agents_md.json")
        server.NEW_IDE_DIR = os.path.join(self.test_dir, "session")
        os.makedirs(server.NEW_IDE_DIR, exist_ok=True)
        server.IDE_KNOWN = os.path.join(server.DATA, "ide_known.json")
        server.known_ides = None
        server.agents_state.clear()
        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(server.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.summary_cache.clear()

        # 合図は実際には送らない（送信結果を記録するだけ）。ide_poke も設定借用の集合だけの代物にする
        server.ide_poke = types.SimpleNamespace()
        server.POKE_MODE = "rpc"
        server.POKE_DELAY = 0

    def tearDown(self):
        for k, v in self.orig.items():
            setattr(server, k, v)
        server.agents_state.clear()
        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.summary_cache.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def _channel_with_choices_task(self):
        """選択肢タスク（choices_status=pending）を持つチャンネルを作る。戻り値は (sid, メインID, 選択肢ID)"""
        main_ide, choices_ide = "conv-main-test", "conv-choices-test"
        s = server.create_session({"title": "poke signal"})
        sid = s["id"]
        s = server.load(sid)
        s["setting"].update({
            "suggest_choices": True,
            "ide_session": main_ide,
            "choices_ide_session": choices_ide,
        })
        s["messages"].append({
            "id": server.new_id(), "role": "assistant", "text": "返事", "ts": server.now(),
            "choices_status": "pending", "choices_type": "normal", "choices_slot": "normal",
            "choices_ts": server.now(),
        })
        server.save(s)
        return sid, main_ide, choices_ide

    def _run_worker(self, steps, gap=0.0):
        """poke_worker を steps 周だけ回し、(ide, full, media) の送信記録を返す"""
        calls = []

        def fake_send_poke(ide, full=False, media=None, model=None, wait=None):
            calls.append((ide, full, media))
            return True, "test", False

        server.poke_event = _StepEvent(steps=steps, gap=gap)
        with mock.patch.object(server, "send_poke", side_effect=fake_send_poke):
            with self.assertRaises(_StopLoop):
                server.poke_worker()
        return calls

    # ------------------------------------------------------------ tests
    def test_choices_task_to_known_conversation_sends_space(self):
        """選択肢専用会話でも、wait_input.py を実行済みなら半角スペース1文字（フルコマンドを送らない）"""
        _sid, main_ide, choices_ide = self._channel_with_choices_task()
        server.mark_known(choices_ide)

        calls = self._run_worker(steps=1)

        pokes = [c for c in calls if c[0] == choices_ide]
        self.assertEqual(len(pokes), 1, f"選択肢会話への合図は1回だけのはず: {calls}")
        self.assertFalse(pokes[0][1], "実行済みの選択肢会話にフルコマンドを送ってはいけない")
        self.assertEqual(server.poke_text(choices_ide, full=pokes[0][1]), server.POKE_TEXT)
        self.assertEqual([c for c in calls if c[0] == main_ide], [])

    def test_choices_task_to_unknown_conversation_sends_full_command(self):
        """新規作成直後の選択肢会話（wait_input.py 未実行）にはコマンド全文を送る"""
        _sid, _main_ide, choices_ide = self._channel_with_choices_task()
        self.assertFalse(server.ide_is_known(choices_ide))

        calls = self._run_worker(steps=1)

        pokes = [c for c in calls if c[0] == choices_ide]
        self.assertEqual(len(pokes), 1, f"選択肢会話への合図は1回だけのはず: {calls}")
        self.assertTrue(pokes[0][1], "未実行の会話にはフルコマンドが必要")
        text = server.poke_text(choices_ide, full=pokes[0][1])
        self.assertIn("wait_input.py", text)
        self.assertIn(f"--ide {choices_ide}", text)

    def test_full_command_is_not_resent_while_pending(self):
        """フルコマンドを送ったタスクは、受け取られるまで再送しない（同じコマンドを積み重ねない）"""
        _sid, _main_ide, choices_ide = self._channel_with_choices_task()
        # 「未実行の会話にフルコマンドを送った」状態を作る
        server.POKE_AGAIN = 0  # 再送の待ち時間を無効化（unclaimed の判定を通す）

        calls = self._run_worker(steps=2, gap=1.7)  # 2周目は quiet(1.5秒) を明けてから

        pokes = [c for c in calls if c[0] == choices_ide]
        self.assertEqual(len(pokes), 1, f"フルコマンドを2回送ってはいけない: {calls}")

    def test_space_signal_is_resent_as_full_command_once(self):
        """スペースの合図が POKE_AGAIN 秒たっても受け取られなければ、フルコマンドを1回だけ送り直す"""
        _sid, _main_ide, choices_ide = self._channel_with_choices_task()
        server.mark_known(choices_ide)
        server.POKE_AGAIN = 0

        calls = self._run_worker(steps=2, gap=1.7)

        pokes = [c for c in calls if c[0] == choices_ide]
        self.assertEqual(len(pokes), 2, f"スペース→フルコマンドの1回だけ再送するはず: {calls}")
        self.assertFalse(pokes[0][1], "1回目はスペース")
        self.assertTrue(pokes[1][1], "2回目はフルコマンド（保険の再送）")

    def test_known_state_is_per_conversation(self):
        """判定は会話ごと（選択肢かどうかではなく、その会話が wait_input.py を実行したか）"""
        _sid, main_ide, choices_ide = self._channel_with_choices_task()
        server.mark_known(choices_ide)
        self.assertFalse(server.poke_text(choices_ide, full=not server.ide_is_known(choices_ide))
                         .startswith("（ロールプレイチャットアプリからの合図）"))
        self.assertTrue(server.poke_text(main_ide, full=not server.ide_is_known(main_ide))
                        .startswith("（ロールプレイチャットアプリからの合図）"))


if __name__ == "__main__":
    unittest.main()
