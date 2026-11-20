# -*- coding: utf-8 -*-
"""Comprehensive unit tests for atomic file writes, retry backoff, and race condition prevention.

Covers:
- atomic_write_text (uniqueness, retry on PermissionError, cleanup on failure)
- write_json (signature, backward compatibility, indent, serialization failure cleanup)
- concurrent writes to same file
- concurrent readers and writers without transient empty dict / fallback
- save_choices_data / save_states_data / save_images_data (lock, zombie prevention, cache invalidation on error)
- save(s) bare raise fix (raises PermissionError on retry exhaustion, cleans up tmp, pops cache)
- memory_store.save_memories cache poisoning fix and tmp cleanup
- _log_ph_misses concurrency protection
- wait_input and ide_poke atomic write
- google_ai._sig_save integration
"""
import concurrent.futures
import copy
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import server
import memory_store
import google_ai
import wait_input
import ide_poke


# 負荷テスト（test_concurrent_readers_and_writers_under_load_no_empty_dict）の契約。
# 保持時間だけを決めると「一度も書けていないのに緑」を見逃すので、
# 「実際に何回動いたか」の下限をテストの契約にする。
# 実測（このリポジトリの開発機、4 writers + 4 readers / 各ループ 1ms sleep）:
#   1.0 秒: write 約 550 回 / read 約 1730 回
#   0.4 秒: write 約 220 回 / read 約 710 回
# → 下限は実測の 2 割弱に置いてマシン差の余裕をとる。
# 保持時間を変えるときは、実測して下限も同じ比率で見直すこと。
LOAD_HOLD_SECONDS = 0.4
LOAD_MIN_WRITES = 40
LOAD_MIN_READS = 130


class TestAtomicWriteText(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_atomic_write_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_atomic_write_creates_file_and_dirs(self):
        target = os.path.join(self.tmp_dir, "sub", "dir", "test.txt")
        server.atomic_write_text(target, "hello world", encoding="utf-8")
        self.assertTrue(os.path.exists(target))
        with open(target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello world")

    def test_temp_filename_uniqueness(self):
        target = os.path.join(self.tmp_dir, "unique.txt")
        seen_tmps = []
        orig_open = open

        def tracking_open(path, mode="r", *args, **kwargs):
            if isinstance(path, str) and path.endswith(".tmp"):
                seen_tmps.append(path)
            return orig_open(path, mode, *args, **kwargs)

        with patch("server.open", side_effect=tracking_open):
            for _ in range(5):
                server.atomic_write_text(target, "data", encoding="utf-8")

        self.assertEqual(len(seen_tmps), 5)
        # All temp filenames must be unique
        self.assertEqual(len(set(seen_tmps)), 5)
        pid_str = str(os.getpid())
        for tmp_name in seen_tmps:
            self.assertIn(pid_str, tmp_name)
            self.assertTrue(tmp_name.endswith(".tmp"))

    def test_retry_on_permission_error_success(self):
        target = os.path.join(self.tmp_dir, "retry_ok.txt")
        orig_replace = os.replace
        calls = [0]

        def flaky_replace(src, dst):
            calls[0] += 1
            if calls[0] < 3:
                raise PermissionError(13, "Permission denied")
            return orig_replace(src, dst)

        with patch("server.os.replace", side_effect=flaky_replace), \
             patch("server.time.sleep", return_value=None):
            server.atomic_write_text(target, "success after retry", encoding="utf-8")

        self.assertEqual(calls[0], 3)
        with open(target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "success after retry")

    def test_retry_exhaustion_cleans_up_orphaned_tmp(self):
        target = os.path.join(self.tmp_dir, "exhaust.txt")

        def always_fail(src, dst):
            raise PermissionError(13, "Permanent permission denied")

        with patch("server.os.replace", side_effect=always_fail), \
             patch("server.time.sleep", return_value=None):
            with self.assertRaises(PermissionError):
                server.atomic_write_text(target, "should fail", encoding="utf-8")

        # Verify target was not created and no orphaned .tmp files remain in dir
        self.assertFalse(os.path.exists(target))
        files = os.listdir(self.tmp_dir)
        self.assertEqual(files, [])

    def test_concurrent_writes_same_file(self):
        target = os.path.join(self.tmp_dir, "concurrent.json")
        num_threads = 8
        writes_per_thread = 15
        errors = []

        def worker(tid):
            try:
                for i in range(writes_per_thread):
                    payload = {"thread": tid, "iter": i, "time": time.time()}
                    server.write_json(target, payload)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(os.path.exists(target))
        # Ensure file contains valid JSON
        data = server.read_json(target, default=None)
        self.assertIsInstance(data, dict)
        self.assertIn("thread", data)
        # Check no .tmp files orphaned
        tmp_files = [f for f in os.listdir(self.tmp_dir) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])


    def test_concurrent_readers_and_writers_under_load_no_empty_dict(self):
        target = os.path.join(self.tmp_dir, "shared.json")
        server.write_json(target, {"count": 0, "status": "init"})

        stop_event = threading.Event()
        read_errors = []
        empty_fallbacks = []
        # スレッド内の例外を握りつぶさない（書けていないのに緑、を防ぐ）
        thread_errors = []
        writes_done = []
        reads_done = []
        counts_seen = []  # reader が実際に見た count（0 のままなら書込みが届いていない）

        def writer():
            cnt = 0
            try:
                while not stop_event.is_set():
                    cnt += 1
                    server.write_json(target, {"count": cnt, "status": "active"})
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001 - 集めてテスト側で失敗させる
                thread_errors.append(("writer", repr(e)))
            finally:
                writes_done.append(cnt)

        def reader():
            cnt = 0
            try:
                while not stop_event.is_set():
                    val = server.read_json(target, default={"fallback": True})
                    cnt += 1
                    if val == {"fallback": True} or val == {}:
                        empty_fallbacks.append(val)
                    elif not isinstance(val, dict) or "count" not in val:
                        read_errors.append(val)
                    else:
                        counts_seen.append(val["count"])
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001 - 集めてテスト側で失敗させる
                thread_errors.append(("reader", repr(e)))
            finally:
                reads_done.append(cnt)

        writers = [threading.Thread(target=writer) for _ in range(4)]
        readers = [threading.Thread(target=reader) for _ in range(4)]

        for t in writers + readers:
            t.start()

        time.sleep(LOAD_HOLD_SECONDS)
        stop_event.set()

        for t in writers + readers:
            t.join()

        self.assertEqual(thread_errors, [], f"負荷中にスレッドが例外で止まった: {thread_errors}")
        self.assertEqual(read_errors, [])
        self.assertEqual(empty_fallbacks, [], "Reader got empty dict or fallback default due to transient PermissionError!")

        # 「負荷が実際にかかった」ことの契約（時間ではなく回数で保証する）
        self.assertEqual(len(writes_done), 4, "writer スレッドが起動していない")
        self.assertEqual(len(reads_done), 4, "reader スレッドが起動していない")
        self.assertTrue(all(n > 0 for n in writes_done), f"書けていない writer がある: {writes_done}")
        self.assertTrue(all(n > 0 for n in reads_done), f"読めていない reader がある: {reads_done}")
        self.assertGreaterEqual(
            sum(writes_done), LOAD_MIN_WRITES,
            f"書込み回数が下限未満（{sum(writes_done)} < {LOAD_MIN_WRITES}）: 負荷がかかっていない")
        self.assertGreaterEqual(
            sum(reads_done), LOAD_MIN_READS,
            f"読込み回数が下限未満（{sum(reads_done)} < {LOAD_MIN_READS}）: 負荷がかかっていない")
        # 書込みが「読者から見える」ことまで確かめる（静かに何も書かない退行を検出）
        self.assertTrue(counts_seen, "reader が書込み後の値を 1 度も見ていない（書込みが届いていない）")
        self.assertGreater(max(counts_seen), 0, "count が 0 のまま: 書込みが届いていない")


class TestWriteJson(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_write_json_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_write_json_indent_options(self):
        p1 = os.path.join(self.tmp_dir, "indented.json")
        p2 = os.path.join(self.tmp_dir, "compact.json")
        data = {"a": 1, "b": [2, 3]}

        server.write_json(p1, data)  # default indent=2
        server.write_json(p2, data, indent=None)

        with open(p1, "r", encoding="utf-8") as f:
            content1 = f.read()
        with open(p2, "r", encoding="utf-8") as f:
            content2 = f.read()

        self.assertIn("\n", content1)
        self.assertNotIn("\n", content2)
        self.assertEqual(json.loads(content1), data)
        self.assertEqual(json.loads(content2), data)

    def test_write_json_serialization_failure_leaves_no_tmp(self):
        target = os.path.join(self.tmp_dir, "bad.json")
        bad_data = {"func": lambda: None}  # not JSON serializable

        with self.assertRaises(TypeError):
            server.write_json(target, bad_data)

        self.assertFalse(os.path.exists(target))
        files = os.listdir(self.tmp_dir)
        self.assertEqual(files, [], "Serialization failure should never create or orphan a .tmp file (F-08)")


class TestChoicesStatesImagesAtomic(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_csi_")
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_images_dir = server.IMAGES_DIR
        server.CHOICES_DIR = os.path.join(self.tmp_dir, "choices")
        server.STATES_DIR = os.path.join(self.tmp_dir, "states")
        server.IMAGES_DIR = os.path.join(self.tmp_dir, "images")
        server.deleted_sessions.clear()
        server.choices_cache.clear()
        server.states_cache.clear()
        server.images_cache.clear()

    def tearDown(self):
        server.CHOICES_DIR = self.orig_choices_dir
        server.STATES_DIR = self.orig_states_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.deleted_sessions.clear()
        server.choices_cache.clear()
        server.states_cache.clear()
        server.images_cache.clear()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_load_choices_normal(self):
        sid = "sess_c1"
        data = {"mid1": {"choices": ["A", "B"], "updated_ts": "2026-01-01"}}
        server.save_choices_data(sid, data)
        self.assertTrue(os.path.exists(server.choices_path(sid)))
        loaded = server.load_choices_data(sid)
        self.assertEqual(loaded, data)

    def test_save_choices_zombie_resurrection_prevention(self):
        sid = "sess_deleted_choices"
        server.deleted_sessions.add(sid)
        server.choices_cache[sid] = {"mtime": 0, "size": 0, "data": {"old": 1}}

        server.save_choices_data(sid, {"mid1": {"choices": ["X"]}})

        self.assertFalse(os.path.exists(server.choices_path(sid)))
        self.assertNotIn(sid, server.choices_cache)

    def test_save_states_zombie_resurrection_prevention(self):
        sid = "sess_deleted_states"
        server.deleted_sessions.add(sid)
        server.states_cache[sid] = {"mtime": 0, "size": 0, "data": {"old": 1}}

        server.save_states_data(sid, {"mid1": {"mood": "happy"}})

        self.assertFalse(os.path.exists(server.states_path(sid)))
        self.assertNotIn(sid, server.states_cache)

    def test_save_images_zombie_resurrection_prevention(self):
        sid = "sess_deleted_images"
        server.deleted_sessions.add(sid)
        server.images_cache[sid] = {"mtime": 0, "size": 0, "data": {"old": 1}}

        server.save_images_data(sid, {"mid1": {"file": "a.png"}})

        self.assertFalse(os.path.exists(server.images_path(sid)))
        self.assertNotIn(sid, server.images_cache)

    def test_save_choices_zombie_prevention_mid_retry(self):
        """If session is marked deleted during retry loop before os.replace, it should abort and remove tmp."""
        sid = "sess_c_mid_del"
        orig_replace = os.replace
        attempt = [0]

        def replace_with_deletion(src, dst):
            attempt[0] += 1
            if attempt[0] == 1:
                # Mark deleted on first attempt, raise PermissionError to force retry
                server.deleted_sessions.add(sid)
                raise PermissionError(13, "locked")
            return orig_replace(src, dst)

        with patch("server.os.replace", side_effect=replace_with_deletion), \
             patch("server.time.sleep", return_value=None):
            server.save_choices_data(sid, {"m": 1})

        # File must not be created
        self.assertFalse(os.path.exists(server.choices_path(sid)))
        self.assertNotIn(sid, server.choices_cache)
        # Temp file must be cleaned up
        tmp_files = [f for f in os.listdir(server.CHOICES_DIR) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_save_choices_exhaustion_raises_and_invalidates_cache(self):
        sid = "sess_c_fail"
        server.choices_cache[sid] = {"mtime": 100, "size": 50, "data": {"cached": True}}

        with patch("server.os.replace", side_effect=PermissionError(13, "denied")), \
             patch("server.time.sleep", return_value=None):
            with self.assertRaises(PermissionError):
                server.save_choices_data(sid, {"new": True})

        self.assertNotIn(sid, server.choices_cache)
        tmp_files = [f for f in os.listdir(server.CHOICES_DIR) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_save_states_exhaustion_raises_and_invalidates_cache(self):
        sid = "sess_s_fail"
        server.states_cache[sid] = {"mtime": 100, "size": 50, "data": {"cached": True}}

        with patch("server.os.replace", side_effect=PermissionError(13, "denied")), \
             patch("server.time.sleep", return_value=None):
            with self.assertRaises(PermissionError):
                server.save_states_data(sid, {"new": True})

        self.assertNotIn(sid, server.states_cache)
        tmp_files = [f for f in os.listdir(server.STATES_DIR) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_save_images_exhaustion_raises_and_invalidates_cache(self):
        sid = "sess_i_fail"
        server.images_cache[sid] = {"mtime": 100, "size": 50, "data": {"cached": True}}

        with patch("server.os.replace", side_effect=PermissionError(13, "denied")), \
             patch("server.time.sleep", return_value=None):
            with self.assertRaises(PermissionError):
                server.save_images_data(sid, {"new": True})

        self.assertNotIn(sid, server.images_cache)
        tmp_files = [f for f in os.listdir(server.IMAGES_DIR) if f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_save_choices_open_failure_invalidates_cache(self):
        sid = "sess_c_open_fail"
        server.choices_cache[sid] = {"mtime": 100, "size": 50, "data": {"cached": True}}

        orig_open = open
        def failing_open(p, *args, **kwargs):
            if isinstance(p, str) and p.endswith(".tmp"):
                raise OSError("Simulated disk error on open")
            return orig_open(p, *args, **kwargs)

        with patch("server.open", side_effect=failing_open):
            with self.assertRaises(OSError):
                server.save_choices_data(sid, {"new": True})

        self.assertNotIn(sid, server.choices_cache)

    def test_load_choices_transient_permission_error_retry(self):
        sid = "sess_c_retry_load"
        data = {"mid1": {"choices": ["A", "B"]}}
        server.save_choices_data(sid, data)
        # Clear cache to force disk read
        server.choices_cache.clear()

        attempts = [0]
        orig_stat = os.stat
        def flaky_stat(p):
            if p == server.choices_path(sid):
                attempts[0] += 1
                if attempts[0] < 3:
                    raise PermissionError(13, "Sharing violation during replace")
            return orig_stat(p)

        with patch("server.os.stat", side_effect=flaky_stat), \
             patch("server.time.sleep", return_value=None):
            loaded = server.load_choices_data(sid)

        self.assertEqual(loaded, data)
        self.assertGreaterEqual(attempts[0], 3)

    def test_load_and_save_choices_under_load_no_empty_dict(self):
        sid = "sess_c_concurrent"
        server.save_choices_data(sid, {"mid": {"choices": [0]}})

        stop_event = threading.Event()
        errors = []
        empty_returns = []

        def writer():
            cnt = 0
            while not stop_event.is_set():
                cnt += 1
                server.save_choices_data(sid, {"mid": {"choices": [cnt]}})
                time.sleep(0.001)

        def reader():
            while not stop_event.is_set():
                # Clear cache occasionally to force disk load
                server.choices_cache.pop(sid, None)
                val = server.load_choices_data(sid)
                if val == {} or not isinstance(val, dict):
                    empty_returns.append(val)
                time.sleep(0.001)

        writers = [threading.Thread(target=writer) for _ in range(3)]
        readers = [threading.Thread(target=reader) for _ in range(3)]

        for t in writers + readers:
            t.start()

        time.sleep(1.0)
        stop_event.set()

        for t in writers + readers:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(empty_returns, [], "load_choices_data returned empty dict under concurrent write load!")


class TestSaveSessionAndBareRaiseFix(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_save_sess_")
        self.orig_sess_dir = server.SESS_DIR
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_images_dir = server.IMAGES_DIR
        server.SESS_DIR = os.path.join(self.tmp_dir, "sessions")
        server.CHOICES_DIR = os.path.join(self.tmp_dir, "choices")
        server.STATES_DIR = os.path.join(self.tmp_dir, "states")
        server.IMAGES_DIR = os.path.join(self.tmp_dir, "images")
        server.deleted_sessions.clear()
        server.session_cache.clear()

    def tearDown(self):
        server.SESS_DIR = self.orig_sess_dir
        server.CHOICES_DIR = self.orig_choices_dir
        server.STATES_DIR = self.orig_states_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.deleted_sessions.clear()
        server.session_cache.clear()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_session_zombie_prevention(self):
        sid = "sess_zombie"
        server.deleted_sessions.add(sid)
        s = {"id": sid, "rev": 1, "messages": []}
        server.session_cache[sid] = {"mtime": 100, "size": 50, "s": s}

        server.save(s)

        self.assertFalse(os.path.exists(server.spath(sid)))
        self.assertNotIn(sid, server.session_cache)

    def test_save_session_open_failure_invalidates_cache(self):
        sid = "sess_open_fail"
        s = {"id": sid, "rev": 1, "messages": []}
        server.session_cache[sid] = {"mtime": 100, "size": 50, "s": s}

        orig_open = open
        def failing_open(p, *args, **kwargs):
            if isinstance(p, str) and p.endswith(".tmp"):
                raise OSError("Simulated disk error on open")
            return orig_open(p, *args, **kwargs)

        with patch("server.open", side_effect=failing_open):
            with self.assertRaises(OSError):
                server.save(s)

        self.assertNotIn(sid, server.session_cache)

    def test_save_session_bare_raise_fix(self):
        """Verify that on retry exhaustion, save(s) properly raises PermissionError (not bare raise RuntimeError)."""
        sid = "sess_perm_err"
        s = {"id": sid, "rev": 1, "messages": []}
        server.session_cache[sid] = {"mtime": 0, "size": 0, "s": s}

        with patch("server.os.replace", side_effect=PermissionError(13, "Sharing violation")), \
             patch("server.time.sleep", return_value=None):
            with self.assertRaises(PermissionError) as ctx:
                server.save(s)
            self.assertEqual(ctx.exception.errno, 13)

        # Cache must be invalidated
        self.assertNotIn(sid, server.session_cache)
        # Temp file must be cleaned up
        if os.path.exists(server.SESS_DIR):
            tmp_files = [f for f in os.listdir(server.SESS_DIR) if f.endswith(".tmp")]
            self.assertEqual(tmp_files, [])


class TestMemoryStoreAtomic(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_mem_store_")
        self.orig_data = memory_store.DATA
        self.orig_rag_dir = memory_store.RAG_DIR
        memory_store.DATA = self.tmp_dir
        memory_store.RAG_DIR = os.path.join(self.tmp_dir, "rag")
        memory_store._deleted_sessions.clear()
        memory_store._memory_cache.clear()

    def tearDown(self):
        memory_store.DATA = self.orig_data
        memory_store.RAG_DIR = self.orig_rag_dir
        memory_store._deleted_sessions.clear()
        memory_store._memory_cache.clear()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_memory_store_save_and_load(self):
        sid = "sess_mem_ok"
        data = {"session_id": sid, "memories": [{"id": "m1", "content": "約束"}]}
        memory_store.save_memories(sid, data)
        self.assertTrue(os.path.exists(memory_store.memory_path(sid)))
        loaded = memory_store.load_memories(sid)
        self.assertEqual(len(loaded.get("memories", [])), 1)
        self.assertEqual(loaded["memories"][0]["content"], "約束")

    def test_memory_store_cache_poisoning_fix(self):
        """When save_memories fails to replace, _memory_cache must NOT contain new data with old stat."""
        sid = "sess_poison_test"
        old_data = {"session_id": sid, "memories": [{"id": "m_old", "content": "old memory"}]}
        memory_store.save_memories(sid, old_data)

        # Verify old data is on disk and in cache
        old_loaded = memory_store.load_memories(sid)
        self.assertEqual(old_loaded["memories"][0]["id"], "m_old")

        # Now attempt to save new data, but fail during replace
        new_data = {"session_id": sid, "memories": [{"id": "m_new", "content": "poison attempt"}]}

        with patch("memory_store.os.replace", side_effect=OSError("Disk locked")), \
             patch("memory_store.time.sleep", return_value=None):
            with self.assertRaises(OSError):
                memory_store.save_memories(sid, new_data)

        # _memory_cache must have been evicted, NOT poisoned with new_data
        self.assertNotIn(sid, memory_store._memory_cache)

        # When reloaded from disk, it should return the actual disk content (old_data)
        fresh_loaded = memory_store.load_memories(sid)
        self.assertEqual(fresh_loaded["memories"][0]["id"], "m_old")

        # Orphaned .tmp files must be deleted
        tmp_files = [f for f in os.listdir(memory_store.RAG_DIR) if ".tmp" in f]
        self.assertEqual(tmp_files, [])

    def test_memory_store_zombie_prevention(self):
        sid = "sess_deleted_mem"
        memory_store.mark_session_deleted(sid)
        data = {"session_id": sid, "memories": [{"id": "m1"}]}

        memory_store.save_memories(sid, data)

        self.assertFalse(os.path.exists(memory_store.memory_path(sid)))
        self.assertNotIn(sid, memory_store._memory_cache)

    def test_memory_store_open_failure_invalidates_cache(self):
        sid = "sess_mem_open_fail"
        memory_store._memory_cache[sid] = {"mtime": 100, "size": 50, "data": {"old": 1}}

        orig_open = open
        def failing_open(p, *args, **kwargs):
            if isinstance(p, str) and ".tmp" in p:
                raise OSError("Simulated disk error on open")
            return orig_open(p, *args, **kwargs)

        with patch("memory_store.open", side_effect=failing_open):
            with self.assertRaises(OSError):
                memory_store.save_memories(sid, {"memories": []})

        self.assertNotIn(sid, memory_store._memory_cache)


class TestOtherModulesAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_other_atomic_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_wait_input_atomic_write(self):
        target = os.path.join(self.tmp_dir, "ctx", "test.md")
        wait_input.atomic_write_text(target, "# Title\nbody text", encoding="utf-8")
        self.assertTrue(os.path.exists(target))
        with open(target, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Title\nbody text")

    def test_ide_poke_atomic_write_json(self):
        target = os.path.join(self.tmp_dir, "cfg.json")
        cfg = {"model": "gemini-test", "temperature": 0.7}
        ide_poke._atomic_write_json(target, cfg)
        self.assertTrue(os.path.exists(target))
        with open(target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), cfg)

    def test_google_write_outbox_atomic(self):
        orig_outbox = server.OUTBOX
        server.OUTBOX = os.path.join(self.tmp_dir, "outbox")
        try:
            name = server._google_write_outbox("sess_g", "mid1", "user", {"text": "reply"})
            path = os.path.join(server.OUTBOX, name)
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"text": "reply"})
            tmp_files = [f for f in os.listdir(server.OUTBOX) if f.endswith(".tmp")]
            self.assertEqual(tmp_files, [])
        finally:
            server.OUTBOX = orig_outbox

    def test_google_ai_sig_save_uses_write_json(self):
        orig_sig_file = google_ai.SIG_CACHE_FILE
        target = os.path.join(self.tmp_dir, "sig_cache.json")
        google_ai.SIG_CACHE_FILE = target
        try:
            google_ai._SIG_MEM = {"version": google_ai.SIG_CACHE_VERSION, "entries": {"k": "v"}}
            google_ai._sig_save()
            self.assertTrue(os.path.exists(target))
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
                # indent=None means single line
                self.assertNotIn("\n", content)
                self.assertEqual(json.loads(content)["entries"], {"k": "v"})
        finally:
            google_ai.SIG_CACHE_FILE = orig_sig_file
            google_ai._SIG_MEM = None

    def test_write_text_if_changed(self):
        p = os.path.join(self.tmp_dir, "changed.txt")
        self.assertTrue(server._write_text_if_changed(p, "line 1\n"))
        self.assertFalse(server._write_text_if_changed(p, "line 1\n"))
        self.assertTrue(server._write_text_if_changed(p, "line 2\n"))
        with open(p, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "line 2\n")

    def test_log_ph_misses_concurrent_safety(self):
        orig_data = server.DATA
        server.DATA = self.tmp_dir
        try:
            num_threads = 6
            tokens_per_thread = 10
            errors = []

            def worker(tid):
                try:
                    for i in range(tokens_per_thread):
                        server._log_ph_misses(misses=[f"__TEST_{tid}_{i}__"])
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            log_path = os.path.join(self.tmp_dir, "ph_misses.log")
            self.assertTrue(os.path.exists(log_path))
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), num_threads * tokens_per_thread)
            for line in lines:
                self.assertTrue(line.endswith("\n"))
                self.assertIn("__TEST_", line)
        finally:
            server.DATA = orig_data


if __name__ == "__main__":
    unittest.main()
