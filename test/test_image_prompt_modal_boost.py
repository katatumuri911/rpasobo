# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import shutil
import urllib.request
import tempfile
import threading
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server

# serve_forever の待ち時間。既定の 0.5 秒だと tearDown の shutdown() が毎回 0.5 秒ブロックする
_POLL = 0.02


class TestImagePromptModalBoost(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_prompt_boost_")
        self.orig_data_dir = server.DATA
        self.orig_sess_dir = server.SESS_DIR
        self.orig_images_dir = server.IMAGES_DIR
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
        server.queued_images.clear()

        # Spin up test HTTP server
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.server_port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=_POLL), daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.DATA = self.orig_data_dir
        server.SESS_DIR = self.orig_sess_dir
        server.IMAGES_DIR = self.orig_images_dir
        server.OUTBOX = self.orig_outbox
        server.DONE = self.orig_done
        server.queued_images.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def api_post(self, path, data):
        url = f"http://127.0.0.1:{self.server_port}{path}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_save_purges_none_keys_and_prevents_zombie_revival(self):
        """Setting refined_prompt=None and manual=None purges them from images json and memory"""
        s = server.create_session({"title": "画像メタテスト"})
        sid = s["id"]
        mid = "asst_msg_1"
        m = {
            "id": mid,
            "role": "assistant",
            "text": "こんにちは",
            "ts": server.now(),
            "image": {
                "file": "test.png",
                "status": "done",
                "prompt": "initial prompt",
                "refined_prompt": "some refined prompt",
                "manual": True
            }
        }
        s["messages"].append(m)
        server.save(s)

        # Confirm initial save has refined_prompt and manual in images json
        idata = server.load_images_data(sid, readonly=True)
        self.assertIn(mid, idata)
        self.assertEqual(idata[mid].get("refined_prompt"), "some refined prompt")
        self.assertTrue(idata[mid].get("manual"))

        # Now simulate setting refined_prompt=None, manual=None (e.g. from regen or refine-image)
        m["image"]["refined_prompt"] = None
        m["image"]["manual"] = None
        server.save(s)

        # Check images data on disk: refined_prompt and manual must be purged!
        idata_after = server.load_images_data(sid, readonly=True)
        self.assertNotIn("refined_prompt", idata_after.get(mid, {}))
        self.assertNotIn("manual", idata_after.get(mid, {}))

        # Check in-memory message: None keys purged, not resurrected from pruned_idata
        self.assertNotIn("refined_prompt", m["image"])
        self.assertNotIn("manual", m["image"])

    def test_build_prompt_manual_preserves_weights_ordering_and_raw_scene(self):
        """When img.manual is True, sanitize_prompt and rigid sorting are bypassed"""
        setting = {
            "image": {
                "base_positive": "masterpiece, best quality, ultra-detailed",
                "base_negative": "lowres, bad anatomy",
                "character_tags": "default_char_tag"
            },
            "chars": [
                {"name": "錦木千束", "tags": "nishikigi chisato, blonde hair, short hair, red ribbon"}
            ]
        }
        # User entered prompt with custom weights, ordering, and an escape tag that normal sanitizer would prune/alter
        raw_user_prompt = "(masterpiece:1.3), 1girl, (blonde hair:1.1), (smile:1.2), holding hands"
        img = {
            "prompt": raw_user_prompt,
            "manual": True
        }
        pos, neg = server.build_prompt(setting, img)

        # 1. User's exact weighted tags must be retained without being stripped or duplicated
        self.assertIn("(masterpiece:1.3)", pos)
        self.assertIn("(blonde hair:1.1)", pos)
        self.assertIn("(smile:1.2)", pos)
        self.assertIn("holding hands", pos)

        # 2. masterpiece was in user's prompt (normalized), so base_positive's masterpiece must NOT be duplicated
        self.assertEqual(pos.count("masterpiece"), 1)

        # 3. best quality and ultra-detailed were NOT in user's prompt, so they should be prepended
        self.assertTrue(pos.startswith("best quality, ultra-detailed") or "best quality" in pos.split("(masterpiece:1.3)")[0])

        # 4. blonde hair was in user prompt, so blonde hair from character appearance should NOT duplicate
        self.assertEqual(pos.count("blonde hair"), 1)

        # 5. short hair and red ribbon (from chisato) were NOT in user prompt, so they should be in prefix
        self.assertIn("short hair", pos)
        self.assertIn("red ribbon", pos)

        # 6. Verify negative prompt
        self.assertIn("lowres", neg)
        self.assertIn("bad anatomy", neg)

    def test_build_prompt_manual_character_name_switch(self):
        """If registered character name or alias appears in manual prompt, switch target_chars"""
        setting = {
            "image": {
                "base_positive": "best quality",
                "base_negative": "lowres"
            },
            "chars": [
                {"name": "錦木千束", "tags": "nishikigi chisato, blonde hair, short hair"},
                {"name": "井ノ上たきな", "tags": "inoue takina, black hair, long hair"}
            ]
        }
        # Originally image had chars: ['錦木千束'], but user writes prompt for Takina
        img = {
            "chars": ["錦木千束"],
            "prompt": "1girl, inoue takina, black hair, smiling",
            "manual": True
        }
        pos, neg = server.build_prompt(setting, img)

        # Should switch to Takina
        self.assertEqual(img.get("chars"), ["井ノ上たきな"])
        self.assertIn("long hair", pos)  # Takina's appearance tag
        self.assertNotIn("short hair", pos)  # Chisato's appearance tag should not be present
        self.assertNotIn("nishikigi", pos)

    def test_build_prompt_manual_no_characters_skips_character_tags(self):
        """If 'no characters', 'scenery', or 'nobody' appears in manual prompt, skip character tags"""
        setting = {
            "image": {
                "base_positive": "best quality",
                "base_negative": "lowres"
            },
            "chars": [
                {"name": "錦木千束", "tags": "nishikigi chisato, blonde hair, short hair, red ribbon"}
            ]
        }

        # Case 1: "scenery"
        img1 = {
            "chars": ["錦木千束"],
            "prompt": "scenery, sunset, ocean, beautiful sky",
            "manual": True
        }
        pos1, _ = server.build_prompt(setting, img1)
        self.assertNotIn("blonde hair", pos1)
        self.assertNotIn("short hair", pos1)
        self.assertNotIn("red ribbon", pos1)
        self.assertNotIn("chisato", pos1)

        # Case 2: "no characters"
        img2 = {
            "chars": ["錦木千束"],
            "prompt": "no characters, empty classroom, sunlight filtering through trees",
            "manual": True
        }
        pos2, _ = server.build_prompt(setting, img2)
        self.assertNotIn("blonde hair", pos2)
        self.assertNotIn("chisato", pos2)

        # Case 3: "nobody"
        img3 = {
            "chars": ["錦木千束"],
            "prompt": "nobody, mountain range, cloudy sky",
            "manual": True
        }
        pos3, _ = server.build_prompt(setting, img3)
        self.assertNotIn("blonde hair", pos3)
        self.assertNotIn("chisato", pos3)

    def test_process_outbox_refine_mode_card_resets_manual_and_refined_prompt(self):
        """process_outbox resets manual and refined_prompt to None when refine_mode == 'card'"""
        s = server.create_session({"title": "推敲カードテスト"})
        sid = s["id"]
        mid = "asst_img_card"
        m = {
            "id": mid,
            "role": "assistant",
            "text": "画像生成テスト",
            "ts": server.now(),
            "image_refine_status": "pending",
            "image_refine_mode": "card",
            "image": {
                "file": "old.png",
                "status": "done",
                "prompt": "old prompt",
                "refined_prompt": "old refined",
                "manual": True
            }
        }
        s["messages"].append(m)
        server.save(s)

        outbox_file = os.path.join(server.OUTBOX, f"{sid}_refine_1.json")
        with open(outbox_file, "w", encoding="utf-8") as f:
            json.dump({
                "session": sid,
                "reply_to": mid,
                "type": "image_refine",
                "prompt": "ai newly refined prompt"
            }, f, ensure_ascii=False)

        imported = server.process_outbox()
        self.assertTrue(imported)

        # Reload session and images metadata
        updated_s = server.load(sid, readonly=True)
        img_msg = updated_s["messages"][0]
        self.assertEqual(img_msg["image"]["prompt"], "ai newly refined prompt")
        self.assertEqual(img_msg["image"]["status"], "queued")
        self.assertNotIn("refined_prompt", img_msg["image"])
        self.assertNotIn("manual", img_msg["image"])

        # Check images json on disk
        idata = server.load_images_data(sid, readonly=True)
        self.assertNotIn("refined_prompt", idata.get(mid, {}))
        self.assertNotIn("manual", idata.get(mid, {}))

    def test_process_images_records_final_positive_negative_and_persists(self):
        """process_images records final_positive and final_negative and does NOT pop them on success"""
        s = server.create_session({"title": "生成プロンプト永続化テスト"})
        sid = s["id"]
        mid = "asst_img_gen"
        m = {
            "id": mid,
            "role": "assistant",
            "text": "生成テスト",
            "ts": server.now(),
            "image": {
                "status": "queued",
                "prompt": "1girl, smile"
            }
        }
        s["messages"].append(m)
        server.save(s)
        server.queued_images.append((sid, mid))

        # Mock comfy_run to succeed immediately
        orig_comfy_run = server.comfy_run
        try:
            server.comfy_run = lambda wf, timeout=900: "rpchat/test/rp_output.png"
            server.process_images()

            # Message on disk should be done and retain final_positive, final_negative
            idata = server.load_images_data(sid, readonly=True)
            self.assertIn(mid, idata)
            self.assertTrue(bool(idata[mid].get("final_positive")))
            self.assertTrue(bool(idata[mid].get("final_negative")))
            self.assertIn("1girl, smile", idata[mid]["final_positive"])
        finally:
            server.comfy_run = orig_comfy_run

    def test_api_regen_restores_metadata_and_sets_manual_prompt(self):
        """/api/regen restores older message metadata, sets prompt without sanitization, and manual=True"""
        s = server.create_session({"title": "再生成APIテスト"})
        sid = s["id"]

        # Older message with detailed metadata saved on disk
        mid_old = "asst_older_msg"
        m_old = {
            "id": mid_old,
            "role": "assistant",
            "text": "古いアシスタント",
            "ts": "2026-01-01T00:00:00Z",
            "image": {
                "file": "old.png",
                "status": "done",
                "prompt": "original prompt",
                "chars": ["錦木千束"],
                "width": 896,
                "height": 1152,
            }
        }
        s["messages"].append(m_old)

        # Add 3 newer assistant messages to push m_old out of recent_2_assts
        for i in range(3):
            s["messages"].append({
                "id": f"asst_new_{i}",
                "role": "assistant",
                "text": f"新しいアシスタント {i}",
                "ts": f"2026-01-01T00:0{i+1}:00Z"
            })
        server.save(s)

        # Verify m_old in memory has had detailed metadata trimmed (only file, status, error remain)
        loaded_s = server.load(sid)
        old_mem_m = server.find_msg(loaded_s, mid_old)
        self.assertNotIn("chars", old_mem_m["image"])
        self.assertNotIn("prompt", old_mem_m["image"])

        # Call /api/regen with custom new prompt containing escape / weighted syntax
        custom_p = "(1girl:1.2), (smile:1.1), (holding hands:1.3)"
        res = self.api_post("/api/regen", {"sid": sid, "id": mid_old, "prompt": custom_p, "seed": 12345})
        self.assertTrue(res.get("ok"))

        # Check session after regen
        updated_s = server.load(sid, readonly=True)
        regen_m = server.find_msg(updated_s, mid_old)
        im = regen_m["image"]

        # Metadata restored from load_images_data
        self.assertEqual(im.get("chars"), ["錦木千束"])
        self.assertEqual(im.get("width"), 896)
        # Prompt directly set to custom_p without sanitize_prompt
        self.assertEqual(im.get("prompt"), custom_p)
        self.assertTrue(im.get("manual"))
        # refined_prompt is None / purged
        self.assertNotIn("refined_prompt", im)
        self.assertEqual(im.get("status"), "queued")

        # Check images data on disk
        idata = server.load_images_data(sid, readonly=True)
        self.assertNotIn("refined_prompt", idata.get(mid_old, {}))
        self.assertTrue(idata[mid_old].get("manual"))
        self.assertEqual(idata[mid_old].get("chars"), ["錦木千束"])

    def test_older_message_with_refined_prompt_retains_full_metadata(self):
        """Messages with refined_prompt retain full metadata in memory even beyond recent_2_assts"""
        s = server.create_session({"title": "推敲メタデータ保持テスト"})
        sid = s["id"]
        mid = "asst_refined_older"
        m = {
            "id": mid,
            "role": "assistant",
            "text": "推敲済みアシスタント",
            "ts": "2026-01-01T00:00:00Z",
            "image": {
                "file": "test.png",
                "status": "done",
                "prompt": "original base prompt",
                "refined_prompt": "1girl, refined tags, masterpiece",
                "chars": ["錦木千束"],
                "width": 896,
                "height": 1152,
                "refined": True
            }
        }
        s["messages"].append(m)

        for i in range(3):
            s["messages"].append({
                "id": f"asst_later_{i}",
                "role": "assistant",
                "text": f"最新アシスタント {i}",
                "ts": f"2026-01-01T00:0{i+1}:00Z"
            })
        server.save(s)

        # Loaded session must keep full metadata for this refined older message
        loaded_s = server.load(sid)
        loaded_m = server.find_msg(loaded_s, mid)
        self.assertEqual(loaded_m["image"].get("refined_prompt"), "1girl, refined tags, masterpiece")
        self.assertEqual(loaded_m["image"].get("prompt"), "original base prompt")
        self.assertEqual(loaded_m["image"].get("chars"), ["錦木千束"])
        self.assertEqual(loaded_m["image"].get("width"), 896)

        # Regenerate via /api/regen purges refined_prompt and resets refined to None
        res = self.api_post("/api/regen", {"sid": sid, "id": mid, "prompt": "new prompt"})
        self.assertTrue(res.get("ok"))
        s_after = server.load(sid, readonly=True)
        m_after = server.find_msg(s_after, mid)
        self.assertNotIn("refined_prompt", m_after["image"])
        self.assertIsNone(m_after["image"].get("refined"))
        idata = server.load_images_data(sid, readonly=True)
        self.assertNotIn("refined_prompt", idata.get(mid, {}))

    def test_api_refine_endpoints_set_refined_prompt_to_none(self):
        """/api/message/refine-image and /api/prompt/refine reset refined_prompt to None and purge it"""
        s = server.create_session({"title": "推敲APIテスト"})
        sid = s["id"]
        mid = "asst_refine_api"
        m = {
            "id": mid,
            "role": "assistant",
            "text": "推敲テスト",
            "ts": server.now(),
            "image": {
                "file": "test.png",
                "status": "done",
                "prompt": "base prompt",
                "refined_prompt": "existing refined prompt"
            }
        }
        s["messages"].append(m)
        server.save(s)

        # 1. /api/prompt/refine (modal refine request)
        res_modal = self.api_post("/api/prompt/refine", {"sid": sid, "id": mid, "prompt": "custom modal input"})
        self.assertTrue(res_modal.get("ok"))
        s_after_modal = server.load(sid, readonly=True)
        m_modal = server.find_msg(s_after_modal, mid)
        self.assertEqual(m_modal.get("image_refine_mode"), "modal")
        self.assertEqual(m_modal.get("image_refine_status"), "pending")
        self.assertNotIn("refined_prompt", m_modal["image"])
        # Disk image metadata must not have zombie refined_prompt
        idata_modal = server.load_images_data(sid, readonly=True)
        self.assertNotIn("refined_prompt", idata_modal.get(mid, {}))

        # Re-inject refined_prompt for testing card refine
        s_work = server.load(sid)
        m_work = server.find_msg(s_work, mid)
        m_work["image"]["refined_prompt"] = "another refined prompt"
        server.save(s_work)
        self.assertEqual(server.load_images_data(sid, readonly=True)[mid].get("refined_prompt"), "another refined prompt")

        # 2. /api/message/refine-image (card refine request)
        res_card = self.api_post("/api/message/refine-image", {"sid": sid, "id": mid})
        self.assertTrue(res_card.get("ok"))
        s_after_card = server.load(sid, readonly=True)
        m_card = server.find_msg(s_after_card, mid)
        self.assertEqual(m_card.get("image_refine_mode"), "card")
        self.assertEqual(m_card.get("image_refine_status"), "pending")
        self.assertNotIn("refined_prompt", m_card["image"])
        # Disk image metadata must not have zombie refined_prompt
        idata_card = server.load_images_data(sid, readonly=True)
        self.assertNotIn("refined_prompt", idata_card.get(mid, {}))

    def test_get_char_match_terms_with_count_tags_and_danbooru_parens(self):
        """get_char_match_terms properly extracts character tags even when preceded by count or appearance tags"""
        # Case 1: Count tag at index 0 followed by character tag
        c1 = {"name": "アスカ", "tags": "1girl, asuka langley soryu, plugsuit, red hair"}
        terms1 = server.get_char_match_terms(c1)
        self.assertIn("asuka langley soryu", terms1)
        self.assertIn("asuka", terms1)
        self.assertIn("langley", terms1)
        self.assertIn("soryu", terms1)
        self.assertNotIn("plugsuit", terms1)
        self.assertNotIn("red hair", terms1)
        self.assertNotIn("hair", terms1)

        # Case 2: Appearance tag preceding Danbooru character tag with parens
        c2 = {"name": "レム", "tags": "1girl, maid, rem (re:zero), blue hair"}
        terms2 = server.get_char_match_terms(c2)
        self.assertIn("rem (re:zero)", terms2)
        self.assertIn("rem", terms2)
        self.assertNotIn("maid", terms2)
        self.assertNotIn("blue hair", terms2)
        self.assertNotIn("blue", terms2)

        # Case 3: Pure appearance tags should not produce appearance terms
        c3 = {"name": "モブ", "tags": "1girl, blue hair, blue eyes, smiling"}
        terms3 = server.get_char_match_terms(c3)
        self.assertNotIn("blue hair", terms3)
        self.assertNotIn("blue", terms3)
        self.assertNotIn("smiling", terms3)
        self.assertIn("モブ", terms3)

    def test_build_prompt_manual_preserves_danbooru_paren_tags_without_duplication(self):
        """User weighted Danbooru paren tag like (rem (re:zero):1.2) must not be duplicated by character tags"""
        setting = {
            "image": {
                "base_positive": "best quality",
                "base_negative": "lowres"
            },
            "chars": [
                {"name": "レム", "tags": "1girl, rem (re:zero), blue hair, maid"}
            ]
        }
        img = {
            "chars": ["レム"],
            "prompt": "1girl, (rem (re:zero):1.2), blue hair, smiling",
            "manual": True
        }
        pos, _ = server.build_prompt(setting, img)
        self.assertIn("(rem (re:zero):1.2)", pos)
        self.assertEqual(pos.count("rem (re:zero)"), 1)
        self.assertIn("maid", pos)

    def test_build_prompt_manual_character_name_switch_with_count_tags(self):
        """Character auto-detection works even when tags start with 1girl"""
        setting = {
            "image": {
                "base_positive": "best quality",
                "base_negative": "lowres"
            },
            "chars": [
                {"name": "錦木千束", "tags": "1girl, nishikigi chisato, blonde hair, short hair"},
                {"name": "井ノ上たきな", "tags": "1girl, inoue takina, black hair, long hair"}
            ]
        }
        img = {
            "chars": ["錦木千束"],
            "prompt": "1girl, inoue takina, black hair, smiling",
            "manual": True
        }
        pos, _ = server.build_prompt(setting, img)
        self.assertEqual(img.get("chars"), ["井ノ上たきな"])
        self.assertIn("long hair", pos)
        self.assertNotIn("short hair", pos)
        self.assertNotIn("nishikigi", pos)

    def test_api_regen_empty_prompt_sets_manual(self):
        """Calling /api/regen with empty string prompt should still set manual=True and clear prompt"""
        s = server.create_session({"title": "空プロンプトテスト"})
        sid = s["id"]
        mid = "asst_empty_regen"
        m = {
            "id": mid,
            "role": "assistant",
            "text": "テスト",
            "ts": server.now(),
            "image": {
                "status": "done",
                "prompt": "original prompt",
                "chars": ["錦木千束"]
            }
        }
        s["messages"].append(m)
        server.save(s)

        res = self.api_post("/api/regen", {"sid": sid, "id": mid, "prompt": ""})
        self.assertTrue(res.get("ok"))
        loaded_s = server.load(sid, readonly=True)
        updated_m = server.find_msg(loaded_s, mid)
        self.assertEqual(updated_m["image"]["prompt"], "")
        self.assertTrue(updated_m["image"]["manual"])
        self.assertNotIn("refined_prompt", updated_m["image"])

    def test_build_prompt_clean_raw_scene_strips_leading_trailing_commas(self):
        """Commas and whitespace at boundaries of raw_scene should be cleaned without double commas"""
        setting = {
            "image": {
                "base_positive": "best quality",
                "base_negative": "lowres"
            },
            "chars": []
        }
        img = {
            "prompt": " , 1girl, smiling, ",
            "manual": True
        }
        pos, _ = server.build_prompt(setting, img)
        self.assertEqual(pos, "best quality, 1girl, smiling")


if __name__ == "__main__":
    unittest.main()
