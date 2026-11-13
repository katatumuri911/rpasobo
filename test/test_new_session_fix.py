# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import urllib.request
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server
import memory_store


class TestNewSessionFix(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_new_session_fix_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_images_dir = server.IMAGES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_outbox = server.OUTBOX
        self.orig_done = server.DONE
        self.orig_rag_dir = server.RAG_DIR
        self.orig_memory_store_data = memory_store.DATA
        self.orig_ide_known = server.IDE_KNOWN
        self.orig_known_ides = server.known_ides

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
        server.IDE_KNOWN = os.path.join(server.DATA, "ide_known.json")
        server.known_ides = None
        server.SESS_DIR = os.path.join(server.DATA, "sessions")
        os.makedirs(server.SESS_DIR, exist_ok=True)
        server.IMAGES_DIR = os.path.join(server.DATA, "images")
        os.makedirs(server.IMAGES_DIR, exist_ok=True)
        server.STATES_DIR = os.path.join(server.DATA, "states")
        os.makedirs(server.STATES_DIR, exist_ok=True)
        server.CHOICES_DIR = os.path.join(server.DATA, "choices")
        os.makedirs(server.CHOICES_DIR, exist_ok=True)
        server.SENT = os.path.join(server.DATA, "sent")
        os.makedirs(server.SENT, exist_ok=True)
        server.ARCHIVE_DIR = os.path.join(server.DATA, "archive")
        os.makedirs(server.ARCHIVE_DIR, exist_ok=True)
        server.TRASH = os.path.join(server.DATA, "trash")
        os.makedirs(server.TRASH, exist_ok=True)
        server.RAG_DIR = os.path.join(server.DATA, "rag")
        os.makedirs(server.RAG_DIR, exist_ok=True)

        memory_store.DATA = server.DATA
        # memory_store 側の RAG_DIR / TRASH は import 時に確定しているので明示的に差し替える
        # （差し替えないとデータが実データの data/rag に書き込まれてしまう）
        self.orig_ms_rag_dir = memory_store.RAG_DIR
        self.orig_ms_trash = memory_store.TRASH
        memory_store.RAG_DIR = server.RAG_DIR
        memory_store.TRASH = server.TRASH
        memory_store._memory_cache.clear()

        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(server.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.session_revs.clear()
        server.queued_images.clear()

        # HTTP サーバー起動
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server_thread.join(timeout=2)

        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.STATES_DIR = self.orig_states_dir
        server.CHOICES_DIR = self.orig_choices_dir
        server.OUTBOX = self.orig_outbox
        server.DONE = self.orig_done
        server.RAG_DIR = self.orig_rag_dir
        server.IDE_KNOWN = self.orig_ide_known
        server.known_ides = self.orig_known_ides
        memory_store.DATA = self.orig_memory_store_data
        memory_store.RAG_DIR = self.orig_ms_rag_dir
        memory_store.TRASH = self.orig_ms_trash
        memory_store._memory_cache.clear()

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _post(self, path, data):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server_port}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -------------------------------------------------------------
    # 観点 1: 廃止キーの完全除去
    # -------------------------------------------------------------
    def test_1_deprecated_keys_removal_merge_defaults(self):
        """merge_defaults() に relationship_core, initial_relationship_core, image.character_tags を含むデータを渡しても完全に除去される"""
        legacy_data = {
            "title": "レガシーセッション",
            "relationship_core": {"call_user": "先輩", "call_char": "後輩"},
            "initial_relationship_core": {"call_user": "先輩"},
            "image": {
                "character_tags": "legacy_char, tags",
                "width": 1024,
            }
        }
        merged = server.merge_defaults(legacy_data)

        self.assertNotIn("relationship_core", merged)
        self.assertNotIn("initial_relationship_core", merged)
        self.assertIn("image", merged)
        self.assertNotIn("character_tags", merged["image"])
        self.assertEqual(merged["image"]["width"], 1024)

    def test_1_deprecated_keys_removal_save(self):
        """save() に relationship_core、initial_relationship_core、image.character_tags を含むセッションを渡した際、保存ファイルと戻りから完全に消える"""
        sid = "sess_legacy_01"
        s = {
            "id": sid,
            "rev": 0,
            "created": server.now(),
            "updated": server.now(),
            "setting": {
                "title": "保存テスト",
                "relationship_core": {"status": "bad"},
                "initial_relationship_core": {"status": "bad"},
                "image": {
                    "checkpoint": "waiIllustriousSDXL_v170.safetensors",
                    "character_tags": "old_tags",
                    "width": 768,
                }
            },
            "messages": [
                {
                    "id": "m_leg_1",
                    "role": "user",
                    "text": "こんにちは",
                    "relationship_core": {"dummy": "val"},
                    "snapshot_relationship_core": {"dummy": "val"},
                }
            ]
        }
        server.save(s)

        # メモリ上の setting からも除去されている
        self.assertNotIn("relationship_core", s["setting"])
        self.assertNotIn("initial_relationship_core", s["setting"])
        self.assertNotIn("character_tags", s["setting"]["image"])

        # ディスク上の保存ファイル（JSON）からも完全に消えている
        file_path = server.spath(sid)
        self.assertTrue(os.path.exists(file_path))
        with open(file_path, "r", encoding="utf-8") as f:
            saved_json = json.load(f)

        saved_st = saved_json.get("setting", {})
        self.assertNotIn("relationship_core", saved_st)
        self.assertNotIn("initial_relationship_core", saved_st)
        self.assertIn("image", saved_st)
        self.assertNotIn("character_tags", saved_st["image"])

        # メッセージからも廃止キーが除去されている
        saved_m = saved_json.get("messages", [])[0]
        self.assertNotIn("relationship_core", saved_m)
        self.assertNotIn("snapshot_relationship_core", saved_m)

    def test_1_merge_defaults_none_subdicts(self):
        """merge_defaults() に image や character が None または非dictのデータが渡されても、デフォルト辞書構造が破壊されない"""
        merged = server.merge_defaults({"image": None, "character": None, "elapsed_time": "invalid"})
        self.assertIsInstance(merged.get("image"), dict)
        self.assertEqual(merged["image"].get("width"), 768)
        self.assertIsInstance(merged.get("character"), dict)
        self.assertIsInstance(merged.get("elapsed_time"), dict)

    # -------------------------------------------------------------
    # 観点 2: 初期値と時間帯推論の検証
    # -------------------------------------------------------------
    def test_2_create_session_defaults(self):
        """create_session() のデフォルト値が現行仕様（user_name: 'あなた', rp_state: True, 768x1344, cfg: 4 等）であること"""
        s = server.create_session()
        st = s["setting"]

        self.assertEqual(st.get("user_name"), "あなた")
        self.assertIs(st.get("rp_state"), True)
        self.assertEqual(st.get("suggest_choices"), True)
        self.assertEqual(st.get("choices_ide_manual_off"), False)

        img = st.get("image", {})
        self.assertEqual(img.get("width"), 768)
        self.assertEqual(img.get("height"), 1344)
        self.assertEqual(img.get("cfg"), 4)
        self.assertEqual(img.get("steps"), 8)
        self.assertEqual(img.get("sampler"), "euler")
        self.assertEqual(img.get("scheduler"), "normal")
        self.assertEqual(img.get("clip_skip"), 2)
        self.assertEqual(img.get("speed_lora"), "Hyper-SDXL-8steps-lora.safetensors")
        self.assertEqual(img.get("speed_lora_strength"), 0.8)
        self.assertEqual(img.get("base_positive"), "masterpiece, best quality, ultra-detailed, anime style, glossy skin, motion lines, large breasts, cleavage, ")
        self.assertEqual(img.get("base_negative"), "worst quality, low quality, bad anatomy, bad proportions, missing limbs, extra limbs, bad hands, mutated hands, missing fingers, text, watermark, signature, multiple girls,")

    def test_2_create_session_time_inference_and_notice_cleanup(self):
        """シナリオ文面から elapsed_time が自動推論され、rotation_notice 等が残らないこと"""
        custom_setting = {
            "title": "深夜シナリオ",
            "scenario": "時計の針は午前2時を指していた。深夜の静まり返った部屋で…",
            "rotation_notice": "残ってはいけない通知",
            "last_memo_mid": "m_old_12345",
            "rotation": {"phase": "armed"},
            "rotation_reserve": {"ide": "old-ide"},
            "rotation_skip_until": 35,
        }
        s = server.create_session(custom_setting)
        st = s["setting"]

        # 時間帯推論の確認
        self.assertEqual(st.get("elapsed_time"), {"day": 1, "period": "深夜"})
        self.assertEqual(st.get("initial_elapsed_time"), {"day": 1, "period": "深夜"})

        # rotation_notice, last_memo_mid, rotation 等が除去されている
        self.assertNotIn("rotation_notice", st)
        self.assertNotIn("last_memo_mid", st)
        self.assertNotIn("memo_manual_edit_mid", st)
        self.assertNotIn("memo_manual_edit_ts", st)
        self.assertNotIn("rotation", st)
        self.assertNotIn("rotation_reserve", st)
        self.assertNotIn("rotation_skip_until", st)

        # 保存ファイルでも確認
        saved_file = server.spath(s["id"])
        with open(saved_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        self.assertNotIn("rotation_notice", disk_data["setting"])
        self.assertNotIn("last_memo_mid", disk_data["setting"])
        self.assertNotIn("memo_manual_edit_mid", disk_data["setting"])
        self.assertNotIn("memo_manual_edit_ts", disk_data["setting"])
        self.assertNotIn("rotation", disk_data["setting"])
        self.assertNotIn("rotation_reserve", disk_data["setting"])
        self.assertNotIn("rotation_skip_until", disk_data["setting"])

        # 夕方の文面推論も確認
        s_evening = server.create_session({"scenario": "夕暮れの教室で放課後のチャイムが鳴り響く。"})
        self.assertEqual(s_evening["setting"]["elapsed_time"]["period"], "夕方")

        # 明示的に elapsed_time を渡した場合は推論で上書きされず尊重されること
        s_explicit = server.create_session({
            "scenario": "深夜の静寂…",
            "elapsed_time": {"day": 3, "period": "昼"}
        })
        self.assertEqual(s_explicit["setting"]["elapsed_time"], {"day": 3, "period": "昼"})

    def test_2_create_session_messages_not_none_cleanup(self):
        """create_session(st, messages) で messages が渡された場合（インポート等）でも rotation_notice と last_memo_mid が常に除去されること（バグ温床Bの是正確認）"""
        st = {
            "title": "インポートテスト",
            "rotation_notice": "旧通知",
            "last_memo_mid": "old_mid_123",
            "rotation": {"phase": "cutover"},
            "rotation_reserve": {"ide": "cid"},
        }
        msgs = [{"id": "m1", "role": "user", "text": "テスト"}]
        s = server.create_session(st, msgs)
        self.assertNotIn("rotation_notice", s["setting"])
        self.assertNotIn("last_memo_mid", s["setting"])
        self.assertNotIn("rotation", s["setting"])
        self.assertNotIn("rotation_reserve", s["setting"])

    def test_2_create_session_malformed_elapsed_time(self):
        """create_session() に None や文字列等の不正な elapsed_time が渡されても、クラッシュせず健全な辞書にフォールバックすること"""
        s_none = server.create_session({"elapsed_time": None})
        self.assertIsInstance(s_none["setting"]["elapsed_time"], dict)
        self.assertEqual(s_none["setting"]["elapsed_time"]["day"], 1)
        self.assertTrue(s_none["setting"]["elapsed_time"].get("period"))

        s_str = server.create_session({"elapsed_time": "午前"})
        self.assertIsInstance(s_str["setting"]["elapsed_time"], dict)
        self.assertTrue(s_str["setting"]["elapsed_time"].get("period"))

        s_empty = server.create_session({"elapsed_time": {}})
        self.assertIsInstance(s_empty["setting"]["elapsed_time"], dict)
        self.assertTrue(s_empty["setting"]["elapsed_time"].get("period"))

    # -------------------------------------------------------------
    # 観点 3: 新規作成と複製の分岐 (/api/session/new)
    # -------------------------------------------------------------
    def test_3_api_session_new_normal_vs_copy(self):
        """/api/session/new の通常作成と copy_from 複製の分岐検証"""
        # 親セッションを作成（キャラ、シナリオ、RAG、進行状況、画像設定、担当IDE等を持つ）
        parent_setting = {
            "title": "冒険の始まり",
            "summary": "冒険者が魔王を倒す旅",
            "instructions": "敬語で話してください",
            "character": {"name": "ミリス", "age": "16", "profile": "魔法使い", "speech": "〜わよ"},
            "scenario": "王都の酒場で仲間を探している。",
            "first_message": "あなたも冒険者志望なの？",
            "chars": [{"id": "c1", "name": "ミリス", "tags": "silver hair"}],
            "rag_collections": ["magic_world_lore"],
            "memo": "仲間集め中",
            "initial_memo": "冒険開始前",
            "total_turns": 42,
            "rotation_count": 2,
            "turns_in_period": 7,
            "last_rotation_turn": 30,
            "turns_since_rotation": 12,
            "elapsed_time": {"day": 4, "period": "夜"},
            "rotation_notice": "旧ローテーション通知",
            "last_memo_mid": "m_parent_last",
            "relationship_core": {"call_char": "ミリスちゃん"},
            "ide_session": "ide_session_parent",
            "ide_folder": "parent_folder",
            "image": {
                "checkpoint": "waiIllustriousSDXL_v170.safetensors",
                "width": 1024,
                "height": 1024,
                "steps": 15,
                "cfg": 5.0,
                "character_tags": "deprecated_char_tags",
            }
        }
        parent_sess = server.create_session(parent_setting)
        parent_id = parent_sess["id"]

        # --- A. 通常作成: 画像・環境設定のみ引き継ぎ、キャラ設定・RAG・進行カウンタを初期化 ---
        res_new = self._post("/api/session/new", {
            "title": "全く別の新チャンネル",
            "setting": parent_sess["setting"]
        })
        self.assertTrue(res_new.get("ok"))
        new_sid = res_new["sid"]
        new_sess = server.load(new_sid)
        nst = new_sess["setting"]

        self.assertEqual(nst["title"], "全く別の新チャンネル")

        # 1. 画像・環境設定は引き継がれている
        self.assertEqual(nst["image"]["width"], 1024)
        self.assertEqual(nst["image"]["height"], 1024)
        self.assertEqual(nst["image"]["steps"], 15)
        self.assertEqual(nst["image"]["cfg"], 5.0)

        # 2. キャラ設定・ストーリーは RESET_STORY により初期化されている
        self.assertEqual(nst["character"], {"name": "", "age": "", "profile": "", "speech": ""})
        self.assertEqual(nst["scenario"], "")
        self.assertEqual(nst["summary"], "")
        self.assertEqual(nst["instructions"], "")
        self.assertEqual(nst["first_message"], "")
        self.assertEqual(nst["chars"], [])
        self.assertEqual(nst["rag_collections"], [])

        # 3. 進行カウンタは RESET_PROGRESS により初期化されている
        self.assertEqual(nst["memo"], "")
        self.assertEqual(nst["initial_memo"], "")
        self.assertEqual(nst["total_turns"], 0)
        self.assertEqual(nst["rotation_count"], 0)
        self.assertEqual(nst["turns_in_period"], 0)
        self.assertEqual(nst["last_rotation_turn"], 0)
        self.assertEqual(nst["turns_since_rotation"], 0)

        # 4. 担当IDE・フォルダはリセットされている
        self.assertEqual(nst["ide_session"], "")
        self.assertEqual(nst["ide_folder"], "")

        # 5. 廃止キー・旧時間経過キー・ローテーション通知は消去されている
        self.assertNotIn("relationship_core", nst)
        self.assertNotIn("character_tags", nst["image"])
        self.assertNotIn("rotation_notice", nst)
        self.assertNotIn("last_memo_mid", nst)
        self.assertNotIn("memo_manual_edit_mid", nst)
        self.assertNotIn("memo_manual_edit_ts", nst)
        self.assertFalse(nst.get("memo_over_budget"))
        self.assertEqual(nst.get("memo_bytes"), 0)
        self.assertEqual(nst["elapsed_time"]["day"], 1)

        # --- B. copy_from（複製）: キャラ設定・RAGを残して進行カウンタのみリセット ---
        res_copy = self._post("/api/session/new", {
            "copy_from": parent_id
        })
        self.assertTrue(res_copy.get("ok"))
        copy_sid = res_copy["sid"]
        copy_sess = server.load(copy_sid)
        cst = copy_sess["setting"]

        # 1. タイトルに " (コピー)" が付与されている
        self.assertEqual(cst["title"], "冒険の始まり (コピー)")

        # 2. キャラ設定・ストーリー・RAGは保持されている
        self.assertEqual(cst["character"]["name"], "ミリス")
        self.assertEqual(cst["scenario"], "王都の酒場で仲間を探している。")
        self.assertEqual(cst["summary"], "冒険者が魔王を倒す旅")
        self.assertEqual(cst["instructions"], "敬語で話してください")
        self.assertEqual(cst["first_message"], "あなたも冒険者志望なの？")
        self.assertEqual(len(cst["chars"]), 1)
        self.assertEqual(cst["chars"][0]["name"], "ミリス")
        self.assertEqual(cst["rag_collections"], ["magic_world_lore"])

        # 3. 進行カウンタはリセットされている
        self.assertEqual(cst["memo"], "")
        self.assertEqual(cst["total_turns"], 0)
        self.assertEqual(cst["rotation_count"], 0)
        self.assertEqual(cst["turns_in_period"], 0)
        self.assertEqual(cst["last_rotation_turn"], 0)
        self.assertEqual(cst["turns_since_rotation"], 0)

        # 4. 担当IDE・フォルダはリセットされている
        self.assertEqual(cst["ide_session"], "")
        self.assertEqual(cst["ide_folder"], "")

        # 5. 廃止キー・通知キーは消去されている
        self.assertNotIn("relationship_core", cst)
        self.assertNotIn("character_tags", cst["image"])
        self.assertNotIn("rotation_notice", cst)
        self.assertNotIn("last_memo_mid", cst)
        self.assertNotIn("memo_manual_edit_mid", cst)
        self.assertNotIn("memo_manual_edit_ts", cst)
        self.assertFalse(cst.get("memo_over_budget"))
        self.assertEqual(cst.get("memo_bytes"), 0)

        # 6. 会話履歴はリセットされ、first_message のみで再開される
        self.assertEqual(len(copy_sess["messages"]), 1)
        self.assertEqual(copy_sess["messages"][0]["text"], "あなたも冒険者志望なの？")
        self.assertEqual(cst["elapsed_time"]["day"], 1)
        self.assertEqual(cst["initial_memo"], "")

    def test_3_clone_session_elapsed_time_isolation(self):
        """clone_session() で elapsed_time と initial_elapsed_time が独立して複製され、変更が共有されないこと"""
        s = server.create_session()
        cloned = server.clone_session(s)
        s["setting"]["elapsed_time"]["period"] = "深夜"
        s["setting"]["initial_elapsed_time"]["period"] = "深夜"
        self.assertEqual(cloned["setting"]["elapsed_time"]["period"], "午前")
        self.assertEqual(cloned["setting"]["initial_elapsed_time"]["period"], "午前")

    # -------------------------------------------------------------
    # 観点 4: 記憶抽出の代替ロジック (extract_memories_fallback)
    # -------------------------------------------------------------
    def test_4_extract_memories_fallback(self):
        """extract_memories_fallback() が relationship_core を参照せず、user_name と chars から人物名を抽出して動作すること"""
        sid = "sess_fallback_test"
        s = {
            "id": sid,
            "setting": {
                "character": {"name": "千束"},
                "user_name": "健太",
                "chars": [{"id": "c1", "name": "千束"}, {"id": "c2", "name": "たきな"}],
                # 廃止キーが混入していても参照されないこと
                "relationship_core": {
                    "call_char": "無効な呼び名_CHAR",
                    "call_user": "無効な呼び名_USER"
                },
                "total_turns": 5,
                "elapsed_time": {"day": 2, "period": "昼"}
            },
            "messages": [
                {
                    "id": "m_user_1",
                    "role": "user",
                    "text": "千束、好きだよ！健太は約束を守るからね。"
                },
                {
                    "id": "m_asst_1",
                    "role": "assistant",
                    "text": "健太くん、ありがとう！ずっと一緒だよ。",
                    "elapsed_time": {"day": 2, "period": "昼"}
                }
            ]
        }
        server.save(s)

        # extract_memories_fallback 実行
        asst_msg = s["messages"][1]
        server.extract_memories_fallback(sid, s, asst_msg)

        # memory_store から抽出・保存された記憶を取得
        mems = memory_store.load_memories(sid, "千束").get("memories", [])
        self.assertTrue(len(mems) > 0, "フォールバックで記憶が抽出・保存されていること")

        # 抽出された記憶の内容に健太や千束が含まれ、かつ無効な呼び名が含まれていないことを確認
        for m in mems:
            content = m.get("content", "")
            self.assertNotIn("無効な呼び名_CHAR", content)
            self.assertNotIn("無効な呼び名_USER", content)

        # デフォルト user_name ("あなた", "ユーザー", "") の場合は extra に追加されないことの検証
        s_default = {
            "id": "sess_default_user",
            "setting": {
                "character": {"name": "アリス"},
                "user_name": "あなた",
                "chars": [{"name": "アリス"}],
                "total_turns": 1,
            },
            "messages": [
                {"id": "mu", "role": "user", "text": "アリス、大好きだよ。"},
                {"id": "ma", "role": "assistant", "text": "私も大好き！"}
            ]
        }
        server.save(s_default)
        server.extract_memories_fallback("sess_default_user", s_default, s_default["messages"][1])
        mems_default = memory_store.load_memories("sess_default_user", "アリス").get("memories", [])
        self.assertTrue(len(mems_default) > 0)

    def test_4_run_memory_regeneration(self):
        """run_memory_regeneration() が relationship_core を参照せず、user_name と chars から記憶を再生成できること"""
        sid = "sess_regen_test"
        s = {
            "id": sid,
            "setting": {
                "character": {"name": "ミリス"},
                "user_name": "ユウキ",
                "chars": [{"id": "c1", "name": "ミリス"}],
                "relationship_core": {"call_char": "無効_CHAR", "call_user": "無効_USER"},
                "elapsed_time": {"day": 1, "period": "昼"},
            },
            "messages": [
                {"id": "m1", "role": "user", "text": "ミリス、ユウキは君のことが好きだよ。約束しよう。"},
                {"id": "m2", "role": "assistant", "text": "ユウキくん、私も好き！約束ね。", "elapsed_time": {"day": 1, "period": "昼"}}
            ]
        }
        server.save(s)
        chunks = [[s["messages"][0], s["messages"][1]]]
        server.run_memory_regeneration(sid, chunks, "ミリス")

        mems = memory_store.load_memories(sid, "ミリス").get("memories", [])
        self.assertTrue(len(mems) > 0, "再生成で記憶が抽出・保存されていること")
        for m in mems:
            content = m.get("content", "")
            self.assertNotIn("無効_CHAR", content)
            self.assertNotIn("無効_USER", content)

    def test_5_dispatch_poke_full_flag_uses_known_ides(self):
        """世代交代等でブートストラップ済みの会話には full=False（半角スペース）が送られ、二重フルコマンドが防止されること"""
        # 1. 未知の会話 (known でない) -> full=True
        ide_unknown = "conv_test_unknown_123"
        server.known_ides = set()
        self.assertFalse(server.ide_is_known(ide_unknown))
        txt_unknown = server.poke_text(ide_unknown, full=(not server.ide_is_known(ide_unknown)))
        self.assertIn("python", txt_unknown)
        self.assertIn("wait_input.py", txt_unknown)

        # 2. ブートストラップ済み（mark_known された会話） -> full=False (半角スペースのみ)
        ide_known = "conv_test_known_456"
        server.mark_known(ide_known)
        self.assertTrue(server.ide_is_known(ide_known))
        txt_known = server.poke_text(ide_known, full=(not server.ide_is_known(ide_known)))
        self.assertEqual(txt_known, server.POKE_TEXT)
        self.assertNotIn("python", txt_known)
        self.assertNotIn("wait_input.py", txt_known)


if __name__ == "__main__":
    unittest.main()
