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
import ide_poke
import memory_store


class TestBoostStep1To6(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_boost_steps_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_images_dir = server.IMAGES_DIR
        self.orig_states_dir = server.STATES_DIR
        self.orig_choices_dir = server.CHOICES_DIR
        self.orig_outbox = server.OUTBOX
        self.orig_done = server.DONE

        server.DATA = os.path.join(self.test_dir, "data")
        os.makedirs(server.DATA, exist_ok=True)
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

        server.OUTBOX = os.path.join(self.test_dir, "outbox")
        os.makedirs(server.OUTBOX, exist_ok=True)
        server.DONE = os.path.join(server.OUTBOX, "done")
        os.makedirs(server.DONE, exist_ok=True)

        server.session_cache.clear()
        server.session_ide_cache.clear()
        server.session_revs.clear()
        server.queued_images.clear()

        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.STATES_DIR = self.orig_states_dir
        server.CHOICES_DIR = self.orig_choices_dir
        server.OUTBOX = self.orig_outbox
        server.DONE = self.orig_done
        server.session_cache.clear()
        server.session_revs.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def api_get(self, path):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))

    def api_post(self, path, payload):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode("utf-8"))

    # --- Step 1: sanitize_prompt false positive elimination ---
    def test_sanitize_prompt_everyday_poses_and_subtle_expressions_preserved(self):
        """Everyday poses and subtle expressions are not stripped as prohibited escape tags"""
        raw = "1girl, hands on thighs, head grab, faint smile, breathless, dazed, satisfied, school uniform"
        res = server.sanitize_prompt(raw)
        self.assertIn("hands on thighs", res)
        self.assertIn("head grab", res)
        self.assertIn("faint smile", res)
        self.assertIn("breathless", res)
        self.assertIn("dazed", res)
        self.assertIn("satisfied", res)
        # Should not inject penis/fellatio in everyday scene
        self.assertNotIn("penis", res)
        self.assertNotIn("fellatio", res)

    def test_sanitize_prompt_ambiguous_oral_keywords_do_not_inject(self):
        """Ambiguous keywords (くわえ, しゃぶ, 喉奥, etc.) do NOT trigger oral injection"""
        raw = "1girl, hands on user thighs, school uniform"
        for kw in ["くわえ", "咥え", "しゃぶ", "喉奥", "舐めさせ", "足元で跪"]:
            res = server.sanitize_prompt(raw, context_text=f"飴を{kw}ている")
            self.assertNotIn("fellatio", res, f"Failed for keyword: {kw}")
            self.assertNotIn("penis", res, f"Failed for keyword: {kw}")

    def test_sanitize_prompt_explicit_oral_keywords_trigger_oral_scene(self):
        """Explicit keywords with escape tags trigger oral scene when allow_inject=True"""
        raw = "1girl, hands on user thighs, blush"
        res = server.sanitize_prompt(raw, context_text="フェラ奉仕をしている", allow_inject=True)
        self.assertIn("fellatio", res)
        self.assertIn("penis", res)
        self.assertIn("pov", res)

    def test_sanitize_prompt_allow_inject_false_blocks_injection(self):
        """allow_inject=False strictly prevents oral tag injection even in oral scenes"""
        raw = "1girl, hands on user thighs, blush"
        res = server.sanitize_prompt(raw, context_text="フェラ奉仕をしている", allow_inject=False)
        self.assertNotIn("fellatio", res)
        self.assertNotIn("penis", res)

    def test_sanitize_prompt_normalizes_invented_pose_tags(self):
        """造語ポーズタグ（i-balance 等）は実在の Danbooru タグに置換される"""
        res = server.sanitize_prompt("1girl, standing, i-balance, i shaped balance, completely naked", allow_inject=True)
        self.assertIn("standing split", res)
        self.assertNotIn("i-balance", res)
        self.assertNotIn("i shaped balance", res)

    def test_sanitize_prompt_converts_japanese_pose_keywords(self):
        """日本語・全角で指定されたポーズも実在タグに変換される"""
        res = server.sanitize_prompt("1girl, completely naked", context_text="全裸でＩ字バランスをしてオシッコを出して", allow_inject=True)
        self.assertIn("standing split", res)
        self.assertIn("leg up", res)

        res2 = server.sanitize_prompt("1girl, I字バランス, 四つん這い", allow_inject=True)
        self.assertIn("standing split", res2)
        self.assertIn("all fours", res2)

    def test_image_tag_context_includes_replied_user_message(self):
        """画像タグ補完の文脈に、返答元のユーザー発言も含まれる"""
        s = {"messages": [
            {"id": "u1", "role": "user", "text": "透明ピンクのアナルゼリーを浣腸する"},
            {"id": "a1", "role": "assistant", "text": "ひゃぅっ……！"},
        ]}
        ctx = server.image_tag_context(s, s["messages"][1], "ひゃぅっ……！")
        self.assertIn("アナルゼリー", ctx)
        res = server.sanitize_prompt("1girl, all fours", context_text=ctx, allow_inject=True)
        self.assertIn("lubricant", res)
        self.assertIn("enema", res)
        self.assertIn("anal object insertion", res)

    def test_sanitize_prompt_pose_tags_ordered_before_outfit(self):
        """ポーズタグはコア行為タグの直後・衣装タグより前に並ぶ"""
        res = server.sanitize_prompt(
            "1girl, vaginal penetration, penis, completely naked, i-balance",
            allow_inject=True,
        )
        tags = [t.strip() for t in res.split(",")]
        self.assertLess(tags.index("standing split"), tags.index("completely naked"))
        self.assertGreater(tags.index("standing split"), tags.index("penis"))

    def test_sanitize_prompt_normalizes_invented_prop_tags(self):
        """造語の小道具タグ（anal jelly / lube 等）は実在タグに置換される"""
        res = server.sanitize_prompt("1girl, all fours, anal jelly, lube, from behind", allow_inject=True)
        self.assertIn("anal object insertion", res)
        self.assertIn("lubricant", res)
        self.assertNotIn("anal jelly", res)
        self.assertNotIn("lube", res)

    def test_sanitize_prompt_converts_japanese_prop_and_excretion(self):
        """日本語の装具・排泄表現も実在タグに変換される"""
        res = server.sanitize_prompt("1girl, all fours", context_text="透明ピンクのアナルゼリーを浣腸して排泄させた", allow_inject=True)
        self.assertIn("lubricant", res)
        self.assertIn("enema", res)
        self.assertIn("anal", res)
        # ゼリーは液体タグだけでは描かれないため、物体としての anal object insertion も必要
        self.assertIn("anal object insertion", res)

        res2 = server.sanitize_prompt("1girl, standing", context_text="我慢できず放尿してしまった", allow_inject=True)
        self.assertIn("peeing", res2)
        self.assertIn("urine", res2)

        res3 = server.sanitize_prompt("1girl, squatting", context_text="我慢できずにうんちをしてしまった", allow_inject=True)
        self.assertIn("feces", res3)
        self.assertIn("pooping", res3)

    def test_sanitize_prompt_injects_equipped_items(self):
        """state の equipped_items は、プロンプトに無くても実在タグで補完される"""
        st = [{"name": "A", "equipped_items": ["ピンクのゼリー"]}]
        res = server.sanitize_prompt("1girl, all fours, completely naked", state=st, allow_inject=True)
        self.assertIn("lubricant", res)
        self.assertIn("anal object insertion", res)

        st2 = [{"name": "A", "equipped_items": ["大型アナルプラグ"]}]
        res2 = server.sanitize_prompt("1girl, all fours", state=st2, allow_inject=True)
        self.assertIn("butt plug", res2)

    def test_sanitize_prompt_object_insertion_does_not_inject_penis(self):
        """プラグ挿入を性交と誤判定して penis / anal penetration を補完しない"""
        st = [{"name": "A", "equipped_items": ["大型アナルプラグ"], "penetration": "アナル挿入（プラグ装着）"}]
        res = server.sanitize_prompt("1girl, all fours, from behind, ass focus", state=st, allow_inject=True)
        self.assertNotIn("penis", res)
        self.assertNotIn("anal penetration", res)
        self.assertIn("anal object insertion", res)

    def test_sanitize_prompt_kissing_blocks_oral_injection(self):
        """Kissing tags prevent oral scene injection"""
        raw = "1girl, hands on user thighs, kissing, blush"
        res = server.sanitize_prompt(raw, context_text="ディープフェラ", allow_inject=True)
        self.assertNotIn("fellatio", res)
        self.assertNotIn("penis", res)

    def test_sanitize_prompt_non_oral_sex_acts_in_tags_blocks_oral_injection(self):
        """Candidate tags indicating other sex acts block oral injection"""
        for tag in ["vaginal penetration", "anal penetration", "paizuri", "handjob", "cunnilingus", "fingering"]:
            raw = f"1girl, hands on user thighs, {tag}"
            res = server.sanitize_prompt(raw, context_text="フェラ", allow_inject=True)
            self.assertNotIn("fellatio", res, f"Failed for tag: {tag}")

    def test_sanitize_prompt_state_penetration_dict_and_list_blocks_oral_injection(self):
        """Non-oral penetration keywords in state block oral injection for both dict and list state"""
        raw = "1girl, hands on user thighs, blush"
        # ※結合中は「penetration と penis を補完する」のが仕様なので、口淫タグが入らないことだけを見る
        # 1. Nested dict: {"char1": {"penetration": "正常位"}}
        s1 = {"char1": {"penetration": "正常位"}}
        res1 = server.sanitize_prompt(raw, context_text="フェラ", state=s1, allow_inject=True)
        self.assertNotIn("fellatio", res1)
        self.assertIn("vaginal penetration", res1)

        # 2. Single dict: {"penetration": "正常位"}
        s2 = {"penetration": "正常位"}
        res2 = server.sanitize_prompt(raw, context_text="フェラ", state=s2, allow_inject=True)
        self.assertNotIn("fellatio", res2)

        # 3. List of dicts: [{"name": "千束", "penetration": "後背位"}]
        s3 = [{"name": "千束", "penetration": "後背位"}]
        res3 = server.sanitize_prompt(raw, context_text="フェラ", state=s3, allow_inject=True)
        self.assertNotIn("fellatio", res3)

        # 4. List of strings: ["結合中"]
        s4 = ["結合中"]
        res4 = server.sanitize_prompt(raw, context_text="フェラ", state=s4, allow_inject=True)
        self.assertNotIn("fellatio", res4)

    # --- Step 2: Cache early-return preventing timeout handling ---
    def test_has_expired_subtasks_detects_timeouts(self):
        """_has_expired_subtasks detects timeouts for both pending and processing subtasks"""
        expired_proc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 150))
        fresh_proc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10))
        expired_pending = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 350))
        fresh_pending = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 50))

        # image_refine in processing: limit is 120s
        s_refine_expired = {"messages": [{"id": "m1", "image_refine_status": "processing", "image_refine_claimed_ts": expired_proc}]}
        s_refine_fresh = {"messages": [{"id": "m1", "image_refine_status": "processing", "image_refine_claimed_ts": fresh_proc}]}
        self.assertTrue(server._has_expired_subtasks(s_refine_expired))
        self.assertFalse(server._has_expired_subtasks(s_refine_fresh))

        # image_refine in pending: limit is 300s
        s_refine_pend_exp = {"messages": [{"id": "m1", "image_refine_status": "pending", "image_refine_ts": expired_pending}]}
        s_refine_pend_fresh = {"messages": [{"id": "m1", "image_refine_status": "pending", "image_refine_ts": fresh_pending}]}
        self.assertTrue(server._has_expired_subtasks(s_refine_pend_exp))
        self.assertFalse(server._has_expired_subtasks(s_refine_pend_fresh))

        # choices in processing: limit is 90s
        s_choices_expired = {"messages": [{"id": "m1", "choices_status": "processing", "choices_claimed_ts": expired_proc}]}
        self.assertTrue(server._has_expired_subtasks(s_choices_expired))

        # memory in processing: limit is 90s
        s_mem_expired = {"messages": [{"id": "m1", "role": "assistant", "memory_status": "processing", "memory_claimed_ts": expired_proc}]}
        self.assertTrue(server._has_expired_subtasks(s_mem_expired))

        # consolidation in processing: limit is 90s
        s_con_expired = {"messages": [{"id": "m1", "role": "assistant", "consolidation_status": "processing", "consolidation_claimed_ts": expired_proc}]}
        self.assertTrue(server._has_expired_subtasks(s_con_expired))

    def test_load_handles_subtask_timeouts_and_updates_error(self):
        """load() marks expired image_refine as error and clears expired consolidation"""
        s = server.create_session({"title": "タイムアウトテスト"})
        sid = s["id"]
        expired_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 150))
        m = {
            "id": "asst_timeout_msg",
            "role": "assistant",
            "text": "タイムアウト検証",
            "ts": expired_ts,
            "image_refine_status": "processing",
            "image_refine_mode": "modal",
            "image_refine_ts": expired_ts,
            "image_refine_claimed_ts": expired_ts,
            "consolidation_status": "processing",
            "consolidation_ts": expired_ts,
            "consolidation_claimed_ts": expired_ts
        }
        s["messages"].append(m)
        server.save(s)

        # Session is now cached
        self.assertIn(sid, server.session_cache)

        # When loading, cache is bypassed because _has_expired_subtasks is True, and timeout is handled
        loaded = server.load(sid)
        loaded_m = server.find_msg(loaded, "asst_timeout_msg")
        self.assertEqual(loaded_m.get("image_refine_status"), "error")
        self.assertEqual(loaded_m.get("image_refine_error"), "推敲がタイムアウトしました")
        self.assertNotIn("consolidation_status", loaded_m)

    def test_api_session_bypasses_unchanged_when_subtask_expired(self):
        """/api/session does NOT return unchanged: true when subtasks have expired"""
        s = server.create_session({"title": "APIタイムアウトテスト"})
        sid = s["id"]
        expired_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 150))
        m = {
            "id": "asst_api_timeout",
            "role": "assistant",
            "text": "APIタイムアウト検証",
            "ts": expired_ts,
            "image_refine_status": "processing",
            "image_refine_ts": expired_ts,
            "image_refine_claimed_ts": expired_ts
        }
        s["messages"].append(m)
        server.save(s)

        rev = server.session_revs[sid]
        client_rev = f"{sid}:{rev}"

        res = self.api_get(f"/api/session?sid={sid}&rev={client_rev}")
        # Should NOT return unchanged: True; should return full session with updated error
        self.assertFalse(res.get("unchanged", False))
        m_res = next((x for x in res.get("messages", []) if x["id"] == "asst_api_timeout"), None)
        self.assertIsNotNone(m_res)
        self.assertEqual(m_res.get("image_refine_status"), "error")

    # --- 途中メッセージ削除で以降の長期記憶を巻き込まない ---
    def test_delete_middle_user_message_keeps_later_memories(self):
        """途中のユーザー発言を削除しても、以降のターンの記憶が消えない（番号だけ詰める）"""
        orig_ms_data = memory_store.DATA
        orig_rag = memory_store.RAG_DIR
        memory_store.DATA = server.DATA
        memory_store.RAG_DIR = os.path.join(server.DATA, "rag")
        os.makedirs(memory_store.RAG_DIR, exist_ok=True)
        try:
            s = server.create_session({"title": "削除テスト"})
            sid = s["id"]
            msgs = []
            for i in range(3):
                msgs.append({"id": f"u{i}", "role": "user", "text": f"発言{i}", "ts": server.now(), "status": "answered"})
                msgs.append({"id": f"a{i}", "role": "assistant", "text": f"返答{i}", "ts": server.now()})
            s["messages"] = msgs
            s["setting"]["total_turns"] = 3
            server.save(s)
            memory_store.save_memories(sid, {
                "session_id": sid, "character_name": "A", "memories": [
                    {"id": "m1", "category": "event", "content": "一件目", "source": "auto", "turn_idx": 1, "mid": "a0", "history": []},
                    {"id": "m2", "category": "event", "content": "二件目", "source": "auto", "turn_idx": 2, "mid": "a1", "history": []},
                    {"id": "m3", "category": "event", "content": "三件目", "source": "auto", "turn_idx": 3, "mid": "a2", "history": []},
                ]})

            res = self.api_post("/api/delete", {"sid": sid, "id": "u1"})
            self.assertTrue(res.get("ok"))

            mems = {m["id"]: m for m in memory_store.load_memories(sid).get("memories", [])}
            self.assertIn("m3", mems, "後ろのターンの記憶が削除されてはいけない")
            self.assertEqual(mems["m3"]["turn_idx"], 2)
            self.assertEqual(mems["m1"]["turn_idx"], 1)

            cur = server.load(sid)
            self.assertEqual(cur["setting"]["total_turns"], 2)
        finally:
            memory_store.DATA = orig_ms_data
            memory_store.RAG_DIR = orig_rag

    # --- Step 5: clean_state normalizing "なし" ---
    def test_clean_state_normalizes_clean_ignore_values_to_empty_string(self):
        """clean_state normalizes all variants of なし, none, null, [] to empty string"""
        test_data = {
            "char1": {
                "outfit": "なし",
                "outerwear": "無し",
                "underwear": "特になし",
                "penetration": "none",
                "body_fluids": "None",
                "situation": ["正常位", "なし", "特になし", "none"],
                "equipped_items": ["item1", "なし"]
            },
            "char2": {
                "outfit": "null",
                "outerwear": "Null",
                "underwear": "NULL",
                "penetration": "[]",
                "body_fluids": [],
                "situation": "なし\n立ち話\n特になし"
            }
        }
        res = server.clean_state(test_data)
        self.assertEqual(len(res), 2)
        c1 = next(c for c in res if c["name"] == "char1")
        self.assertEqual(c1["outfit"], "")
        self.assertEqual(c1["outerwear"], "")
        self.assertEqual(c1["underwear"], "")
        self.assertEqual(c1["penetration"], "")
        self.assertEqual(c1["body_fluids"], "")
        self.assertEqual(c1["situation"], ["正常位"])
        self.assertEqual(c1["equipped_items"], ["item1"])

        c2 = next(c for c in res if c["name"] == "char2")
        self.assertEqual(c2["outfit"], "")
        self.assertEqual(c2["outerwear"], "")
        self.assertEqual(c2["underwear"], "")
        self.assertEqual(c2["penetration"], "")
        self.assertEqual(c2["body_fluids"], "")
        self.assertEqual(c2["situation"], ["立ち話"])

    # --- Step 6: ide_poke model config freshness ---
    def test_ide_poke_send_to_conversation_invalidates_cached_config_on_step_change(self):
        """ide_poke re-fetches config when lastUserInputStepIndex changes"""
        cid = "conv-freshness-test"
        step_idx = 1
        fetched_count = [0]

        def mock_user_config(p, t, c, s):
            fetched_count[0] += 1
            return {"plannerConfig": {"planModel": f"model_v{fetched_count[0]}"}}

        orig_user_config = ide_poke._user_config
        orig_cached_config = ide_poke._cached_config
        orig_locate = ide_poke.locate
        orig_call = ide_poke._call
        orig_fix = ide_poke._fix_model
        orig_summary = ide_poke._summary

        try:
            ide_poke._user_config = mock_user_config
            ide_poke._cached_config = lambda cfg=None: cfg or {}
            ide_poke._fix_model = lambda p, t, c: ""
            ide_poke._call = lambda p, t, m, b, timeout=15: (200, {})
            ide_poke._summary = lambda c, p, t: {"lastUserInputTime": "ts_updated", "status": "CASCADE_RUN_STATUS_IDLE"}

            # Step 1
            ide_poke.locate = lambda c: (9999, "tok", {"summary": "test", "status": "CASCADE_RUN_STATUS_IDLE", "lastUserInputStepIndex": 1})
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertTrue(ok)
            self.assertEqual(fetched_count[0], 1)

            # Same Step 1 -> must use cached config
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertTrue(ok)
            self.assertEqual(fetched_count[0], 1)

            # Step 2 -> cid単位キャッシュのため再取得せずキャッシュヒットを維持（毎ターンのローカルRPC削減）
            ide_poke.locate = lambda c: (9999, "tok", {"summary": "test", "status": "CASCADE_RUN_STATUS_IDLE", "lastUserInputStepIndex": 2})
            ok, msg, retry = ide_poke.send_to_conversation(cid)
            self.assertTrue(ok)
            self.assertEqual(fetched_count[0], 1)
        finally:
            ide_poke._user_config = orig_user_config
            ide_poke._cached_config = orig_cached_config
            ide_poke._fix_model = orig_fix
            ide_poke._call = orig_call
            ide_poke._summary = orig_summary
            ide_poke.locate = orig_locate
            ide_poke._cid_cfg.pop(cid, None)

    # --- Step 1-2: Image Tag Spec v2 (Tests 1-41) ---
    def test_v2_01_pose_i_balance(self):
        """1: state.pose='I字バランス' -> standing split, leg up"""
        st = [{"name": "A", "pose": "I字バランス"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertIn("standing split", res)
        self.assertIn("leg up", res)

    def test_v2_02_pose_all_fours(self):
        """2: state.pose='四つん這い' -> all fours"""
        st = [{"name": "A", "pose": "四つん這い"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertIn("all fours", res)

    def test_v2_03_pose_space_swimming_miss_logged(self):
        """3: state.pose='宇宙遊泳' -> タグ化されない・state_tag_misses.log に記録"""
        st = [{"name": "A", "pose": "宇宙遊泳"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertNotIn("宇宙遊泳", res)
        log_path = os.path.join(server.DATA, "state_tag_misses.log")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[pose] 宇宙遊泳", content)

    def test_v2_04_expression_list(self):
        """4: state.expression=['羞恥','涙目'] -> blush, teary eyes"""
        st = [{"name": "A", "expression": ["羞恥", "涙目"]}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertIn("blush", res)
        self.assertIn("teary eyes", res)

    def test_v2_05_expression_string_split(self):
        """5: state.expression='羞恥、涙目'（文字列） -> 2タグに分割される"""
        st = [{"name": "A", "expression": "羞恥、涙目"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertIn("blush", res)
        self.assertIn("teary eyes", res)

    def test_v2_06_place_bathroom(self):
        """6: state.place='バスルーム' -> bathroom"""
        st = [{"name": "A", "place": "バスルーム"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("bathroom", res)

    def test_v2_07_place_spaceship_miss_logged(self):
        """7: state.place='宇宙船' -> タグ化されない・ログ"""
        st = [{"name": "A", "place": "宇宙船"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertNotIn("宇宙船", res)
        log_path = os.path.join(server.DATA, "state_tag_misses.log")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[place] 宇宙船", content)

    def test_v2_08_camera_ascii_passthrough(self):
        """8: state.camera='from behind' -> そのまま入る"""
        st = [{"name": "A", "camera": "from behind"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("from behind", res)

    def test_v2_09_camera_japanese_from_above(self):
        """9: state.camera='俯瞰' -> from above"""
        st = [{"name": "A", "camera": "俯瞰"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("from above", res)

    def test_v2_10_pose_conflict_strips_standing(self):
        """10: state.pose='四つん這い' ＋ raw に standing -> all fours が入り standing が消える"""
        st = [{"name": "A", "pose": "四つん這い"}]
        res = server.sanitize_prompt("1girl, standing", state=st, state_turn=st, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertNotIn("standing", res)

    def test_v2_11_pose_preserves_spread_legs(self):
        """11: state.pose='四つん這い' ＋ raw に spread legs -> spread legs は残る（付加タグは保持）"""
        st = [{"name": "A", "pose": "四つん這い"}]
        res = server.sanitize_prompt("1girl, spread legs", state=st, state_turn=st, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertIn("spread legs", res)

    def test_v2_12_place_conflict_strips_classroom_and_outdoors(self):
        """12: state.place='バスルーム' ＋ raw に classroom, outdoors -> bathroom が入り両方消える"""
        st = [{"name": "A", "place": "バスルーム"}]
        res = server.sanitize_prompt("1girl, classroom, outdoors", state=st, allow_inject=True)
        self.assertIn("bathroom", res)
        self.assertNotIn("classroom", res)
        self.assertNotIn("outdoors", res)

    def test_v2_13_camera_conflict_strips_angle_preserves_framing(self):
        """13: state.camera='from behind' ＋ raw に from above, close-up -> from behind が入り from above が消え、close-up は残る"""
        st = [{"name": "A", "camera": "from behind"}]
        res = server.sanitize_prompt("1girl, from above, close-up", state=st, allow_inject=True)
        self.assertIn("from behind", res)
        self.assertNotIn("from above", res)
        self.assertIn("close-up", res)

    def test_v2_14_expression_group_suppression(self):
        """14: raw に heavy blush ＋ state.expression=['羞恥'] -> blush を追加しない（グループ抑止）"""
        st = [{"name": "A", "expression": ["羞恥"]}]
        res = server.sanitize_prompt("1girl, heavy blush", state=st, state_turn=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("heavy blush", tags)
        self.assertNotIn("blush", tags)

    def test_v2_15_pose_deduplication(self):
        """15: 既に all fours がある状態で state.pose='四つん這い' -> 重複しない"""
        st = [{"name": "A", "pose": "四つん這い"}]
        res = server.sanitize_prompt("1girl, all fours", state=st, state_turn=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertEqual(tags.count("all fours"), 1)

    def test_v2_16_merge_state_preserves_omitted_pose_clears_on_empty(self):
        """16: merge_state: pose を省略した更新 -> 前ターンの pose が維持される。pose='' を明示すると消える"""
        prev = [{"name": "A", "pose": "standing"}]
        upd = [{"name": "A", "outfit": "dress"}]
        m1 = server.merge_state(prev, upd)
        self.assertEqual(m1[0].get("pose"), "standing")
        upd_clear = [{"name": "A", "pose": ""}]
        m2 = server.merge_state(prev, upd_clear)
        self.assertEqual(m2[0].get("pose", ""), "")

    def test_v2_17_allow_inject_false_no_injection(self):
        """17: allow_inject=False ＋ state 4項目 -> 何も注入されない"""
        st = [{"name": "A", "pose": "四つん這い", "expression": ["羞恥"], "place": "バスルーム", "camera": "from behind"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=False)
        self.assertNotIn("all fours", res)
        self.assertNotIn("blush", res)
        self.assertNotIn("bathroom", res)
        self.assertNotIn("from behind", res)

    def test_v2_18_state_turn_overrides_stale_state_pose(self):
        """18: 取込相当: state_turn={'pose':'四つん這い'}, state に古い pose='立ち上が' -> 新しい all fours が入り、standing は消える"""
        st = [{"name": "A", "pose": "立ち上が"}]
        st_turn = [{"name": "A", "pose": "四つん這い"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st_turn, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertNotIn("standing", res)

    def test_v2_19_empty_state_turn_does_not_inject_or_strip(self):
        """19: state_turn={}（pose 未記入）, state に pose='立ち上が', raw に all fours -> standing を注入せず、all fours も消さない（何もしない）"""
        st = [{"name": "A", "pose": "立ち上が"}]
        st_turn = {}
        res = server.sanitize_prompt("1girl, all fours", state=st, state_turn=st_turn, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertNotIn("standing", res)

    def test_v2_20_sanitize_prompt_idempotent(self):
        """20: 1回目と2回目で state / state_turn が同じ -> 出力が完全に一致する（冪等）"""
        st = [{"name": "A", "pose": "四つん這い", "expression": ["羞恥"], "place": "バスルーム", "camera": "from behind"}]
        st_turn = [{"name": "A", "pose": "四つん這い", "expression": ["羞恥"]}]
        res1 = server.sanitize_prompt("1girl, standing, classroom", state=st, state_turn=st_turn, allow_inject=True)
        res2 = server.sanitize_prompt(res1, state=st, state_turn=st_turn, allow_inject=True)
        self.assertEqual(res1, res2)

    def test_v2_21_scope_single_girl_viewpoint_only(self):
        """21: raw に 1girl。state A='四つん這い', B='立ち'、state_turn に両方 -> all fours のみ入り standing は入らない（視点キャラのみ）"""
        st = [{"name": "A", "pose": "四つん這い"}, {"name": "B", "pose": "立ち"}]
        st_turn = [{"name": "A", "pose": "四つん這い"}, {"name": "B", "pose": "立ち"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st_turn, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertNotIn("standing", res)

    def test_v2_22_scope_multi_girl_all_expanded(self):
        """22: raw に 2girls。同じ state -> all fours と standing の両方が入る（全キャラ展開）"""
        st = [{"name": "A", "pose": "四つん這い"}, {"name": "B", "pose": "立ち"}]
        st_turn = [{"name": "A", "pose": "四つん這い"}, {"name": "B", "pose": "立ち"}]
        res = server.sanitize_prompt("2girls", state=st, state_turn=st_turn, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertIn("standing", res)

    def test_v2_23_scope_no_person_count_defaults_to_single(self):
        """23: 人数タグなし。同じ state -> 1人扱いで standing は入らない（安全側）"""
        st = [{"name": "A", "pose": "四つん這い"}, {"name": "B", "pose": "立ち"}]
        st_turn = [{"name": "A", "pose": "四つん這い"}, {"name": "B", "pose": "立ち"}]
        res = server.sanitize_prompt("holding hands", state=st, state_turn=st_turn, allow_inject=True)
        self.assertIn("all fours", res)
        self.assertNotIn("standing", res)

    def test_v2_24_scope_multi_girl_no_pose_conflict_stripping(self):
        """24: raw に 2girls と kneeling。state A='四つん這い' -> kneeling が消されずに残る（複数人では競合除去しない）"""
        st = [{"name": "A", "pose": "四つん這い"}]
        st_turn = [{"name": "A", "pose": "四つん這い"}]
        res = server.sanitize_prompt("2girls, kneeling", state=st, state_turn=st_turn, allow_inject=True)
        self.assertIn("kneeling", res)
        self.assertIn("all fours", res)

    def test_v2_25_place_longest_match_classroom(self):
        """25: state.place='薄暗い教室' -> classroom（修飾語付きでも最長キーで一致）"""
        st = [{"name": "A", "place": "薄暗い教室"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("classroom", res)

    def test_v2_26_place_anise_bedroom_no_bed(self):
        """26: state.place='アニスの寝室' -> bedroom。bed は入らない"""
        st = [{"name": "A", "place": "アニスの寝室"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("bedroom", tags)
        self.assertNotIn("bed", tags)

    def test_v2_27_place_bedroom_no_bed_collision(self):
        """27: state.place='ベッドルーム' -> bedroom。bed は入らない（誤爆しない）"""
        st = [{"name": "A", "place": "ベッドルーム"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("bedroom", tags)
        self.assertNotIn("bed", tags)

    def test_v2_28_camera_ascii_normalization(self):
        """28: state.camera='From_Behind, close-up' -> from behind, close-up（大文字・_・カンマ対応）"""
        st = [{"name": "A", "camera": "From_Behind, close-up"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("from behind", res)
        self.assertIn("close-up", res)

    def test_v2_29_pose_ascii_passthrough(self):
        """29: state.pose='Standing Split, leg up' -> standing split, leg up"""
        st = [{"name": "A", "pose": "Standing Split, leg up"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        self.assertIn("standing split", res)
        self.assertIn("leg up", res)

    def test_v2_30_pose_ascii_multiple_conflicts_takes_first_only(self):
        """30: state.pose='Standing, all fours'（ASCII で複数） -> standing のみ（矛盾する姿勢は最初の1つ）"""
        st = [{"name": "A", "pose": "Standing, all fours"}]
        res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("standing", tags)
        self.assertNotIn("all fours", tags)

    def test_v2_31_camera_placed_before_core_act(self):
        """31: 1girl, vaginal penetration, from above（構図は state 由来でなくても） -> from above がコア行為より前に入る"""
        res = server.sanitize_prompt("1girl, vaginal penetration, from above")
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("from above", tags)
        self.assertIn("vaginal penetration", tags)
        self.assertLess(tags.index("from above"), tags.index("vaginal penetration"))

    def test_v2_32_tag_ordering_count_pov_camera_penis(self):
        """32: 1girl, from behind, pov, penis -> 順序が 1girl -> pov -> from behind -> penis になる"""
        res = server.sanitize_prompt("1girl, from behind, pov, penis")
        tags = [t.strip() for t in res.split(",")]
        self.assertEqual(tags[:4], ["1girl", "pov", "from behind", "penis"])

    def test_v2_33_camera_placed_before_pose(self):
        """33: 1girl, all fours, from behind -> from behind が all fours（ポーズ）より前になる"""
        res = server.sanitize_prompt("1girl, all fours, from behind")
        tags = [t.strip() for t in res.split(",")]
        self.assertLess(tags.index("from behind"), tags.index("all fours"))

    def test_v2_34_build_prompt_camera_before_core_act(self):
        """34: build_prompt（img.manual なし）に 1girl, vaginal penetration, from behind -> 生成プロンプトでも from behind が vaginal penetration より前になる"""
        setting = {"image": {"base_positive": ""}}
        img = {"prompt": "1girl, vaginal penetration, from behind"}
        pos, _ = server.build_prompt(setting, img)
        tags = [t.strip() for t in pos.split(",")]
        self.assertIn("from behind", tags)
        self.assertIn("vaginal penetration", tags)
        self.assertLess(tags.index("from behind"), tags.index("vaginal penetration"))

    def test_v2_35_place_bed_preserved_with_bedroom(self):
        """35: state.place='寝室' ＋ raw に bedroom, bed -> bed が落ちない（両方残る）"""
        st = [{"name": "A", "place": "寝室"}]
        res = server.sanitize_prompt("1girl, bedroom, bed", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("bedroom", tags)
        self.assertIn("bed", tags)

    def test_v2_36_place_school_classroom_compatible_coexists(self):
        """36: state.place='教室' ＋ raw に school, classroom -> 両方残る（包含関係は共存）"""
        st = [{"name": "A", "place": "教室"}]
        res = server.sanitize_prompt("1girl, school, classroom", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("school", tags)
        self.assertIn("classroom", tags)

    def test_v2_37_place_bathroom_strips_classroom_preserves_bed(self):
        """37: state.place='バスルーム' ＋ raw に classroom, bed -> classroom は落ち、bed は残る"""
        st = [{"name": "A", "place": "バスルーム"}]
        res = server.sanitize_prompt("1girl, classroom, bed", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("bathroom", tags)
        self.assertNotIn("classroom", tags)
        self.assertIn("bed", tags)

    def test_v2_38_place_pool_outdoors_preserved(self):
        """38: state.place='プール' ＋ raw に outdoors -> outdoors が落ちない（区分不定）"""
        st = [{"name": "A", "place": "プール"}]
        res = server.sanitize_prompt("1girl, outdoors", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("pool", tags)
        self.assertIn("outdoors", tags)

    def test_v2_39_place_onsen_indoors_preserved(self):
        """39: state.place='温泉' ＋ raw に indoors -> indoors が落ちない（区分不定）"""
        st = [{"name": "A", "place": "温泉"}]
        res = server.sanitize_prompt("1girl, indoors", state=st, allow_inject=True)
        tags = [t.strip() for t in res.split(",")]
        self.assertIn("onsen", tags)
        self.assertIn("indoors", tags)

    def test_v2_40_clean_state_omitted_keys_preserved_in_merge_state(self):
        """40: clean_state([{'name':'A'}])（4キーなし）-> merge_state -> 出力に4キーが作られず、前ターンの値が維持される"""
        prev = [{"name": "A", "pose": "standing", "expression": ["smile"], "place": "school", "camera": "close-up"}]
        curr = server.clean_state([{"name": "A"}])
        self.assertNotIn("pose", curr[0])
        self.assertNotIn("expression", curr[0])
        self.assertNotIn("place", curr[0])
        self.assertNotIn("camera", curr[0])
        merged = server.merge_state(prev, curr)
        self.assertEqual(merged[0].get("pose"), "standing")
        self.assertEqual(merged[0].get("expression"), ["smile"])
        self.assertEqual(merged[0].get("place"), "school")
        self.assertEqual(merged[0].get("camera"), "close-up")

    def test_v2_41_clean_state_pose_explicit_clear(self):
        """41: clean_state([{'name':'A','pose':''}]) -> pose が明示的にクリアされる"""
        prev = [{"name": "A", "pose": "standing"}]
        curr = server.clean_state([{"name": "A", "pose": ""}])
        self.assertIn("pose", curr[0])
        self.assertEqual(curr[0]["pose"], "")
        merged = server.merge_state(prev, curr)
        self.assertEqual(merged[0].get("pose", ""), "")

    def test_v2_42_clean_state_single_dict(self):
        """42: clean_state with single character dict retains state"""
        res = server.clean_state({"name": "A", "pose": "四つん這い"})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "A")
        self.assertEqual(res[0]["pose"], "四つん這い")

    def test_v2_43_turn_by_name_nested_dict_keys(self):
        """43: turn_by_name and iter_turn_chars preserve character names when dict keys are names"""
        turns = server.turn_by_name({"A": {"pose": "四つん這い"}, "B": {"pose": "立ち"}})
        self.assertIn("a", turns)
        self.assertIn("b", turns)
        self.assertEqual(turns["a"]["pose"], "四つん這い")
        self.assertEqual(turns["b"]["pose"], "立ち")

    def test_v2_44_ascii_mixed_miss_logged(self):
        """44: ASCII passthrough with mixed valid and invalid tags injects valid and logs invalid"""
        log_path = os.path.join(server.DATA, "state_tag_misses.log")
        if os.path.exists(log_path):
            os.remove(log_path)
        st = [{"name": "A", "camera": "from behind, invalid@camera"}]
        res = server.sanitize_prompt("1girl", state=st, allow_inject=True)
        self.assertIn("from behind", res)
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[camera] invalid@camera", content)

    def test_v2_45_build_prompt_uses_img_state_turn(self):
        """45: build_prompt uses img['state_turn'] even when msg is None"""
        setting = {"image": {"base_positive": ""}}
        img = {
            "prompt": "1girl, standing",
            "state_turn": [{"name": "A", "pose": "四つん這い"}]
        }
        pos, _ = server.build_prompt(setting, img)
        tags = [t.strip() for t in pos.split(",")]
        self.assertIn("all fours", tags)
        self.assertNotIn("standing", tags)

    def test_v2_46_pose_standing_conjugations(self):
        """46: POSE_TAG_MAP handles 立っている and 立って"""
        for kw in ["立っている", "立って"]:
            st = [{"name": "A", "pose": kw}]
            res = server.sanitize_prompt("1girl", state=st, state_turn=st, allow_inject=True)
            self.assertIn("standing", res, f"Failed for pose: {kw}")

    def test_v2_47_additive_pose_does_not_strip_posture(self):
        """47: 姿勢を指定しない付加ポーズ（開脚・手を繋ぐ）で lying 等を消さない"""
        for kw in ["開脚", "手を繋ぐ"]:
            st = [{"name": "A", "pose": kw}]
            res = server.sanitize_prompt("1girl, lying, on back, completely naked", state=st, state_turn=st, allow_inject=True)
            tags = [t.strip() for t in res.split(",")]
            self.assertIn("lying", tags, f"lying removed by pose={kw}")
            self.assertIn("on back", tags, f"on back removed by pose={kw}")


    def test_v2_48_penetration_location_selects_anal_over_vaginal(self):
        """48: penetration の場所＋行為で anal / vaginal を判定する（結合中だけでは決め打ちしない）"""
        raw = "1girl, lying, on back"
        anal = server.sanitize_prompt(
            raw,
            state=[{"name": "A", "penetration": "結合中", "situation": ["アナルにペニス挿入中"]}],
            allow_inject=True,
        )
        self.assertIn("anal penetration", anal)
        self.assertNotIn("vaginal penetration", anal)

        vag = server.sanitize_prompt(
            raw,
            state=[{"name": "A", "penetration": "結合中", "situation": ["膣にペニス挿入中"]}],
            allow_inject=True,
        )
        self.assertIn("vaginal penetration", vag)
        self.assertNotIn("anal penetration", vag)

        ambiguous = server.sanitize_prompt(
            raw,
            state=[{"name": "A", "penetration": "結合中"}],
            allow_inject=True,
        )
        self.assertNotIn("vaginal penetration", ambiguous)
        self.assertNotIn("anal penetration", ambiguous)

        oral = server.sanitize_prompt(
            raw,
            state=[{"name": "A", "penetration": "口にペニス挿入中"}],
            allow_inject=True,
        )
        self.assertIn("fellatio", oral)
        self.assertNotIn("vaginal penetration", oral)
        self.assertNotIn("anal penetration", oral)


if __name__ == "__main__":
    unittest.main()
