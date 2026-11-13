# -*- coding: utf-8 -*-
"""RAG「会話ログから記憶を一括再生成」の非破壊化テスト

観点:
- save_extracted_memories(touch=False) が既存記憶の turn_idx / mid / elapsed_time / updated_at を保持する
- filter_new_items() が既存 content_hash と重複する項目を除外する
- reset=False（既定）は既存記憶を消さず追加のみ、同じ内容の再実行で増えない
- reset=True は自動記憶だけを消し（TRASH にバックアップ）、手動記憶の fulfilled を active に戻す
- items=[] では何も変わらない
- server.run_memory_regeneration(reset=False) が非破壊で動く
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import memory_store as ms
import server

SID = "s1"


class _TempStore(unittest.TestCase):
    """memory_store の保存先を一時ディレクトリへ差し替える共通の足場"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_rag_regen_")
        # TRASH は import 時に確定しているので DATA / RAG_DIR / TRASH を全部差し替える
        self.orig = (ms.DATA, ms.RAG_DIR, ms.TRASH)
        ms.DATA = self.tmp
        ms.RAG_DIR = os.path.join(self.tmp, "rag")
        ms.TRASH = os.path.join(self.tmp, "trash")
        os.makedirs(ms.RAG_DIR, exist_ok=True)
        ms._memory_cache.clear()
        # 埋め込みモデルの有無でテスト結果が変わらないよう、決定的なダミーベクトルに差し替える
        # （同一カテゴリ同士は類似度 1.0 になるため、併合経路も決定的に検証できる）
        self._orig_embed = (ms.get_embeddings_batch, ms.get_embedding)
        ms.get_embeddings_batch = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]  # 正規化済み（内積=類似度）
        ms.get_embedding = lambda text: [1.0, 0.0, 0.0]

    def tearDown(self):
        ms.get_embeddings_batch, ms.get_embedding = self._orig_embed
        ms.DATA, ms.RAG_DIR, ms.TRASH = self.orig
        ms._memory_cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _freeze_updated_at(self, value=1000.0):
        """updated_at の変化を検証できるよう、既存記憶の値を既知の値に固定する"""
        data = ms.load_memories(SID)
        for m in data.get("memories", []):
            m["updated_at"] = value
        ms.save_memories(SID, data)
        return value

    # -------------------------------------------------------------
    # helpers
    # -------------------------------------------------------------
    def _seed_auto(self, cat, content, turn, mid, elapsed=None, session_id=SID):
        ms.save_extracted_memories(
            session_id, "ラピ", [{"category": cat, "content": content}],
            turn_idx=turn, mid=mid,
            elapsed_time=elapsed or {"day": 1, "period": "午前"}, max_new=5
        )

    def _seed_manual(self, cat, content, status="active", session_id=SID):
        return ms.add_manual_memory(session_id, "ラピ", cat, content, status=status)

    def _memories(self, session_id=SID):
        return ms.load_memories(session_id).get("memories", [])

    def _snapshot(self, session_id=SID):
        return {
            m["id"]: {
                "turn_idx": m.get("turn_idx"),
                "mid": m.get("mid"),
                "elapsed_time": m.get("elapsed_time"),
                "updated_at": m.get("updated_at"),
                "source": m.get("source"),
                "content": m.get("content"),
            }
            for m in self._memories(session_id)
        }

    def _trash_files(self):
        if not os.path.isdir(ms.TRASH):
            return []
        return os.listdir(ms.TRASH)

    def _seed_three_auto_and_one_manual(self):
        self._seed_auto("rule", "ラピは夜10時以降に部屋から出てはならない", 1, "m1",
                        {"day": 1, "period": "午前"})
        self._seed_auto("item", "ラピは指揮官から合鍵をもらった", 2, "m2",
                        {"day": 1, "period": "午後"})
        self._seed_auto("relationship", "ラピは指揮官との距離を縮めている", 3, "m3",
                        {"day": 2, "period": "夜"})
        self._seed_manual("user_fact", "指揮官は甘いものが好き")
        return 4


class TestNonDestructiveSave(_TempStore):
    def test_existing_kept_and_only_new_added(self):
        """reset=False: 既存 auto3 + manual1 + 新規2 → 4件残る + 2件増える + 既存メタは不変"""
        base = self._seed_three_auto_and_one_manual()
        self.assertEqual(len(self._memories()), base)

        before = self._snapshot()
        items = [
            {"category": "event", "content": "ラピは pi 記念日に海へ行った"},
            {"category": "char_fact", "content": "ラピの生い立ちには秘密のトラウマがある"},
            # 既存と同じ内容（除外されるべき）
            {"category": "rule", "content": "ラピは夜10時以降に部屋から出てはならない"},
        ]
        new_items = ms.filter_new_items(SID, items)
        self.assertEqual(len(new_items), 2)

        ms.save_extracted_memories(
            SID, "ラピ", new_items,
            turn_idx=99, mid=None, max_new=5, touch=False
        )

        after = self._memories()
        self.assertEqual(len(after), base + 2)

        after_map = {m["id"]: m for m in after}
        for mem_id, snap in before.items():
            self.assertIn(mem_id, after_map, "既存記憶が消えている")
            cur = after_map[mem_id]
            self.assertEqual(cur.get("turn_idx"), snap["turn_idx"])
            self.assertEqual(cur.get("mid"), snap["mid"])
            self.assertEqual(cur.get("elapsed_time"), snap["elapsed_time"])
            self.assertEqual(cur.get("updated_at"), snap["updated_at"])
            self.assertEqual(cur.get("source"), snap["source"])

    def test_rerun_same_content_does_not_grow(self):
        """同内容の再実行で件数が増えない"""
        self._seed_three_auto_and_one_manual()
        items = [
            {"category": "event", "content": "ラピは pi 記念日に海へ行った"},
            {"category": "char_fact", "content": "ラピの生い立ちには秘密のトラウマがある"},
        ]
        for _ in range(2):
            new_items = ms.filter_new_items(SID, items)
            if new_items:
                ms.save_extracted_memories(
                    SID, "ラピ", new_items,
                    turn_idx=50, mid=None, max_new=5, touch=False
                )
        # 2回目は filter で全件除外される
        self.assertEqual(ms.filter_new_items(SID, items), [])
        self.assertEqual(len(self._memories()), 6)

    def test_empty_items_change_nothing(self):
        """items=[] で何も変わらない"""
        self._seed_three_auto_and_one_manual()
        before = self._snapshot()
        self.assertEqual(ms.filter_new_items(SID, []), [])
        ms.save_extracted_memories(SID, "ラピ", [], turn_idx=99, mid=None, max_new=5, touch=False)
        self.assertEqual(self._snapshot(), before)

    def test_touch_true_still_updates_metadata(self):
        """既定（touch=True）は従来どおり既存記憶のメタを更新する（併合経路）"""
        self._seed_auto("rule", "ラピは夜10時以降に部屋から出てはならない", 1, "m1",
                        {"day": 1, "period": "午前"})
        self._freeze_updated_at()
        ms.save_extracted_memories(
            SID, "ラピ", [{"category": "rule", "content": "ラピは夜10時以降に部屋から出てはならない。"}],
            turn_idx=7, mid="m7", elapsed_time={"day": 3, "period": "夜"}, max_new=5
        )
        mems = [m for m in self._memories() if m.get("source") == "auto"]
        self.assertEqual(len(mems), 1)   # 類似度 1.0 なので併合（新規追加されない）
        self.assertEqual(mems[0].get("turn_idx"), 7)
        self.assertEqual(mems[0].get("mid"), "m7")
        self.assertEqual(mems[0].get("elapsed_time"), {"day": 3, "period": "夜"})
        self.assertNotEqual(mems[0].get("updated_at"), 1000.0)
        self.assertTrue(mems[0].get("history"), "併合履歴が残る")

    def test_merge_without_touch_keeps_metadata(self):
        """touch=False の併合では既存の turn_idx / mid / elapsed_time / updated_at を上書きしない"""
        self._seed_auto("rule", "ラピは夜10時以降に部屋から出てはならない", 3, "m3",
                        {"day": 2, "period": "夜"})
        self._freeze_updated_at()
        before = self._snapshot()
        mem_id = list(before)[0]

        # 末尾の句点だけ違う文（content_hash は不一致 → filter_new_items は除外しない）
        newer = "ラピは夜10時以降に部屋から出てはならない。"
        items = ms.filter_new_items(SID, [{"category": "rule", "content": newer}])
        self.assertEqual(len(items), 1)

        ms.save_extracted_memories(
            SID, "ラピ", items, turn_idx=99, mid="m99",
            elapsed_time={"day": 9, "period": "朝"}, max_new=5, touch=False
        )

        mems = self._memories()
        self.assertEqual(len(mems), 1, "併合なので件数は増えない")
        self.assertEqual(mems[0].get("content"), newer, "本文は併合で更新される（既存仕様）")
        self.assertEqual(mems[0].get("turn_idx"), before[mem_id]["turn_idx"])
        self.assertEqual(mems[0].get("mid"), before[mem_id]["mid"])
        self.assertEqual(mems[0].get("elapsed_time"), before[mem_id]["elapsed_time"])
        self.assertEqual(mems[0].get("updated_at"), before[mem_id]["updated_at"])


class TestClearAutoMemories(_TempStore):
    def test_reset_removes_auto_keeps_manual_and_backs_up(self):
        """reset=True: auto が消える / manual は残る / TRASH にバックアップが増える"""
        self._seed_three_auto_and_one_manual()
        trash_before = len(self._trash_files())

        ms.clear_auto_memories(SID)

        mems = self._memories()
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0].get("source"), "manual")
        self.assertEqual(len(self._trash_files()), trash_before + 1)

    def test_reset_restores_fulfilled_manual_to_active(self):
        """reset=True で manual の fulfilled が active に戻る"""
        self._seed_auto("rule", "ラピは夜10時以降に部屋から出てはならない", 1, "m1")
        self._seed_manual("promise", "指揮官はラピと港に行く約束をした", status="fulfilled")
        self.assertEqual([m for m in self._memories() if m.get("source") == "manual"][0].get("status"),
                         "fulfilled")

        ms.clear_auto_memories(SID)

        manual = [m for m in self._memories() if m.get("source") == "manual"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0].get("status"), "active")


class _ServerTempStore(_TempStore):
    """server 側のパスも一時ディレクトリへ差し替え、HTTP ハンドラを起動する足場"""

    def setUp(self):
        super().setUp()
        self.orig_server = (
            server.DATA, server.SESS_DIR, server.RAG_DIR, server.TRASH,
            server.CHOICES_DIR, server.STATES_DIR, server.IMAGES_DIR,
        )
        server.DATA = self.tmp
        server.SESS_DIR = os.path.join(self.tmp, "sessions")
        server.RAG_DIR = ms.RAG_DIR
        server.TRASH = ms.TRASH
        server.CHOICES_DIR = os.path.join(self.tmp, "choices")
        server.STATES_DIR = os.path.join(self.tmp, "states")
        server.IMAGES_DIR = os.path.join(self.tmp, "images")
        for p in (server.SESS_DIR, server.CHOICES_DIR, server.STATES_DIR, server.IMAGES_DIR):
            os.makedirs(p, exist_ok=True)
        server.session_cache.clear()
        server.session_revs.clear()
        server.session_ide_cache.clear()
        server.memory_regen_state.clear()
        server.memory_regen_meta.clear()

        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.port = self.httpd.server_address[1]
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.http_thread.join(timeout=2)
        except Exception:
            pass
        (server.DATA, server.SESS_DIR, server.RAG_DIR, server.TRASH,
         server.CHOICES_DIR, server.STATES_DIR, server.IMAGES_DIR) = self.orig_server
        server.session_cache.clear()
        server.session_revs.clear()
        server.session_ide_cache.clear()
        server.memory_regen_state.clear()
        server.memory_regen_meta.clear()
        super().tearDown()

    def _write_session(self, sid, messages, setting=None):
        session = {
            "id": sid,
            "title": "regen test",
            "rev": 0,
            "setting": setting or {"user_name": "指揮官", "chars": [{"name": "ラピ"}],
                                   "elapsed_time": {"day": 2, "period": "夜"}},
            "messages": messages,
        }
        with open(os.path.join(server.SESS_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False)

    def _post(self, path, data):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _wait_regen(self, sid, timeout=30.0):
        """再生成スレッドが終わる（または待ち時間切れ）まで状態をポーリングする"""
        deadline = time.time() + timeout
        st = {}
        while time.time() < deadline:
            st = self._get("/api/rag/memories/regenerate/status?session_id=" + sid)
            if not st.get("running"):
                return st
            time.sleep(0.05)
        return st


class TestRunMemoryRegeneration(_ServerTempStore):
    """server.run_memory_regeneration の配線（非破壊 / 破壊）を確かめる"""

    def setUp(self):
        super().setUp()
        self.messages = []
        self._add(1, "user", "明日デートに行く約束をした")
        self._add(1, "assistant", "「覚えておくね」")
        self._add(2, "user", "門限は夜9時です")
        self._add(2, "assistant", "「わかった」")
        self._write_session(SID, self.messages)

    def _add(self, turn, role, text):
        mid = f"m{turn}_{role}"
        self.messages.append({"id": mid, "role": role, "text": text,
                              "ts": 1700000000 + len(self.messages),
                              "turn": turn})

    def _chunks(self):
        return [self.messages]

    def test_non_destructive_regeneration(self):
        existing = server.extract_memories_from_text(
            user_text="明日デートに行く約束をした", char_name="ラピ", user_name="指揮官")
        self.assertTrue(existing, "テスト前提: キーワード抽出で記憶が取れること")

        self._seed_three_auto_and_one_manual()
        before = self._snapshot()

        server.memory_regen_meta[SID] = {"before": len(before), "reset": False, "after": len(before)}
        server.run_memory_regeneration(SID, self._chunks(), "ラピ", reset=False)

        after = self._memories()
        after_map = {m["id"]: m for m in after}
        # 既存は消えない
        for mem_id, snap in before.items():
            self.assertIn(mem_id, after_map)
            self.assertEqual(after_map[mem_id].get("turn_idx"), snap["turn_idx"])
            self.assertEqual(after_map[mem_id].get("mid"), snap["mid"])
            self.assertEqual(after_map[mem_id].get("elapsed_time"), snap["elapsed_time"])
        self.assertGreaterEqual(len(after), len(before))
        self.assertEqual(server.memory_regen_meta[SID]["after"], len(after))
        self.assertFalse(server.memory_regen_state[SID]["running"])

        # 同じ会話ログでもう一度 → 件数が増えない
        count_once = len(after)
        server.run_memory_regeneration(SID, self._chunks(), "ラピ", reset=False)
        self.assertEqual(len(self._memories()), count_once)

    def test_reset_regeneration_keeps_manual(self):
        self._seed_three_auto_and_one_manual()
        manual_ids = [m["id"] for m in self._memories() if m.get("source") == "manual"]
        self.assertEqual(len(manual_ids), 1)
        trash_before = len(self._trash_files())

        # エンドポイントと同じ順序（先に全消し → reset=True で作り直す）
        ms.clear_auto_memories(SID)
        server.run_memory_regeneration(SID, self._chunks(), "ラピ", reset=True)

        mems = self._memories()
        self.assertIn(manual_ids[0], [m["id"] for m in mems])
        self.assertEqual(len(self._trash_files()), trash_before + 1)
        autos = [m for m in mems if m.get("source") == "auto"]
        for m in autos:
            self.assertIsNotNone(m.get("turn_idx"))
        self.assertFalse(server.memory_regen_state[SID]["running"])


class TestRegenerateEndpoint(_ServerTempStore):
    """エンドポイントの reset フラグ（既定=非破壊 / True=全消し）を HTTP 経由で確かめる"""

    def setUp(self):
        super().setUp()
        self.sid = "ep_regen"
        msgs = [
            {"id": "e1", "role": "user", "text": "明日デートに行く約束をした", "ts": 1700000000, "turn": 1},
            {"id": "e2", "role": "assistant", "text": "「覚えておくね」", "ts": 1700000001, "turn": 1},
        ]
        self._write_session(self.sid, msgs)

    def _seed(self):
        self._seed_auto("rule", "ラピは夜10時以降に部屋から出てはならない", 1, "m1",
                        {"day": 1, "period": "午前"}, session_id=self.sid)
        self._seed_auto("item", "ラピは指揮官から合鍵をもらった", 2, "m2",
                        {"day": 1, "period": "午後"}, session_id=self.sid)
        self._seed_manual("promise", "指揮官はラピと港に行く約束をした", status="fulfilled",
                          session_id=self.sid)

    def test_default_is_non_destructive(self):
        """reset を送らない（既定 False）と既存の自動記憶が消えない"""
        self._seed()
        before_ids = {m["id"] for m in self._memories(self.sid)}
        self.assertEqual(len(before_ids), 3)
        trash_before = len(self._trash_files())

        res = self._post("/api/rag/memories/regenerate", {"session_id": self.sid})
        self.assertFalse(res.get("reset"))
        self.assertEqual(res.get("before"), 3)
        st = self._wait_regen(self.sid)
        self.assertEqual(st.get("status"), "completed")

        after_ids = {m["id"] for m in self._memories(self.sid)}
        self.assertTrue(before_ids <= after_ids, "既存記憶が消えている（非破壊のはず）")
        self.assertEqual(len(self._trash_files()), trash_before, "全消しバックアップは作られない")
        self.assertEqual(st.get("before"), 3)
        self.assertEqual(st.get("after"), len(after_ids))

        # 同じログでもう一度実行しても件数が増えない
        count_once = len(after_ids)
        self._post("/api/rag/memories/regenerate", {"session_id": self.sid})
        self._wait_regen(self.sid)
        self.assertEqual(len(self._memories(self.sid)), count_once)

    def test_reset_true_clears_auto_and_keeps_manual(self):
        """reset=True のときだけ自動記憶が消え、手動記憶は残る（TRASH にバックアップ）"""
        self._seed()
        old_auto_ids = {m["id"] for m in self._memories(self.sid) if m.get("source") == "auto"}
        manual_ids = {m["id"] for m in self._memories(self.sid) if m.get("source") == "manual"}
        self.assertEqual(len(old_auto_ids), 2)
        self.assertEqual(len(manual_ids), 1)
        trash_before = len(self._trash_files())

        res = self._post("/api/rag/memories/regenerate", {"session_id": self.sid, "reset": True})
        self.assertTrue(res.get("reset"))
        st = self._wait_regen(self.sid)
        self.assertEqual(st.get("status"), "completed")

        mems = self._memories(self.sid)
        ids = {m["id"] for m in mems}
        self.assertFalse(old_auto_ids & ids, "reset=True なのに自動記憶が残っている")
        self.assertTrue(manual_ids <= ids, "手動記憶が消えている")
        manual = [m for m in mems if m.get("source") == "manual"][0]
        self.assertEqual(manual.get("status"), "active", "達成済みの手動記憶が有効に戻る")
        self.assertEqual(len(self._trash_files()), trash_before + 1, "TRASH バックアップが増える")

    def test_memories_branch_is_non_destructive_by_default(self):
        """d.get("memories") 直接保存の分岐も、既定では既存を消さず重複も追加しない"""
        self._seed()
        before_ids = {m["id"] for m in self._memories(self.sid)}
        payload = {"session_id": self.sid,
                   "memories": [{"category": "event", "content": "ラピは pi 記念日に海へ行った"}]}

        res = self._post("/api/rag/memories/regenerate", payload)
        self.assertFalse(res.get("reset"))
        ids = {m["id"] for m in self._memories(self.sid)}
        self.assertTrue(before_ids <= ids)
        self.assertEqual(len(ids), len(before_ids) + 1)

        # 同じ内容をもう一度送っても増えない（filter_new_items が除外）
        self._post("/api/rag/memories/regenerate", payload)
        self.assertEqual({m["id"] for m in self._memories(self.sid)}, ids)


if __name__ == "__main__":
    unittest.main()
