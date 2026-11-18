# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import server


TAGS_FIXTURE = {
    "version": 1,
    "axes": {"relation": {"label": "関係性", "max": 3}},
    "tags": [
        {"id": "rel_pure", "label": "純愛", "axis": "relation",
         "description": "まっすぐな恋愛", "effects": {"relationship": ["純愛"]}},
        {"id": "rel_childhood", "label": "幼馴染", "axis": "relation",
         "description": "幼馴染", "effects": {"relationship": ["幼馴染"]}},
        {"id": "kink_ntr", "label": "NTR", "axis": "kink",
         "description": "背徳", "effects": {"kinks": ["NTR"]}, "conflicts_ng": ["ng_ntr"]},
        {"id": "world_school", "label": "学園", "axis": "world",
         "description": "学校", "effects": {"world": ["学園"]},
         "image_tags": ["classroom"]},
    ],
    "ng_presets": [
        {"id": "ng_minor", "label": "未成年", "locked": True},
        {"id": "ng_ntr", "label": "NTR"},
    ],
    "examples": ["例"],
}


class WizardTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wizard_test_")
        self._old = {}
        for name in ("DATA", "APP_SETTINGS", "SESS_DIR", "ARCHIVE_DIR",
                     "CHOICES_DIR", "STATES_DIR", "IMAGES_DIR", "TRASH",
                     "WIZARD_DIR", "WIZARD_IDE_DIR", "WIZARD_TAGS_PATH",
                     "PUSH_SUBS", "VAPID_FILE"):
            self._old[name] = getattr(server, name, None)

        data = os.path.join(self.tmp, "data")
        os.makedirs(data, exist_ok=True)
        server.DATA = data
        server.APP_SETTINGS = os.path.join(data, "app_settings.json")
        server.SESS_DIR = os.path.join(data, "sessions")
        server.ARCHIVE_DIR = os.path.join(data, "archive")
        server.CHOICES_DIR = os.path.join(data, "choices")
        server.STATES_DIR = os.path.join(data, "states")
        server.IMAGES_DIR = os.path.join(data, "images")
        server.TRASH = os.path.join(data, "trash")
        server.WIZARD_DIR = os.path.join(data, "wizards")
        server.WIZARD_IDE_DIR = os.path.join(data, "wizard_ide")
        server.PUSH_SUBS = os.path.join(data, "push_subs.json")
        server.VAPID_FILE = os.path.join(data, "vapid_keys.json")
        server.WIZARD_TAGS_PATH = os.path.join(data, "wizard_tags.json")
        with open(server.WIZARD_TAGS_PATH, "w", encoding="utf-8") as fp:
            json.dump(TAGS_FIXTURE, fp, ensure_ascii=False)

        for p in (server.SESS_DIR, server.ARCHIVE_DIR, server.CHOICES_DIR,
                  server.STATES_DIR, server.IMAGES_DIR, server.TRASH,
                  server.WIZARD_DIR, server.WIZARD_IDE_DIR):
            os.makedirs(p, exist_ok=True)

        # 本物のキャラプリセットを読まない（テストを決定的にする）
        self._old_char_presets = getattr(server, "char_presets", None)
        server.char_presets = lambda: []

        server.wizard_jobs.clear()
        server.wizard_model_cache.clear()
        server.wizard_tags_cache.update({"ts": 0.0, "data": None, "index": {}})
        server.queued_images.clear()
        server.image_event.clear()

    def tearDown(self):
        for k, v in self._old.items():
            if v is not None:
                setattr(server, k, v)
        if getattr(self, "_old_char_presets", None) is not None:
            server.char_presets = self._old_char_presets
        server.queued_images.clear()
        server.image_event.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_draft(self):
        d = server.wizard_new_draft(
            {}, {"provider": "google", "model": "gemini-2.5-pro",
                 "thinking_level": "high", "ai_enabled": True}, True)
        d["base_sid"] = ""
        server.wizard_save(d)
        return d

    def with_compose(self, d):
        setting, warnings = server.wizard_template_compose(d)
        d["compose"] = {"setting": setting, "source": "template", "warnings": warnings,
                        "auto_slots": [], "title_alternatives": []}
        d["compose_stale"] = False
        d["status"] = "ready"
        server.wizard_save(d)
        return d


class TestSourceModel(WizardTestBase):
    def test_defaults_are_not_stored(self):
        d = self.new_draft()
        self.assertNotIn("player_type", d["slots"])
        self.assertEqual(server.wizard_effective_value(d, "player_type"), "original")
        self.assertEqual(server.wizard_effective_value(d, "pov"), "second")

    def test_user_beats_derived(self):
        d = self.new_draft()
        server.wslot_set(d, "relationship", ["恋人"], "user")
        d["selected_tags"] = ["rel_childhood"]
        server.wizard_recompute_derived(d)
        self.assertEqual(server.wslot_value(d, "relationship"), ["恋人"])

    def test_auto_is_filled(self):
        d = self.new_draft()
        server.wslot_set_auto(d, "partner_type")
        self.assertTrue(server.wizard_filled(d, "partner_type"))
        self.assertEqual(server.wslot_source(d, "partner_type"), "auto")

    def test_invalidation(self):
        d = self.new_draft()
        server.wslot_set(d, "partner_type", "copyright", "user")
        server.wslot_set(d, "work", "作品A", "user")
        server.wslot_set(d, "partner_type", "original", "user")
        self.assertIsNone(server.wslot_value(d, "work"))

    def test_canon_fidelity_default(self):
        d = self.new_draft()
        self.assertEqual(server.wizard_effective_value(d, "canon_fidelity"), "mixed")


class TestBaseStep(WizardTestBase):
    def make_base(self, title="学園もの", user_name="先生", project="企画X"):
        s = server.create_session({"title": title, "user_name": user_name, "project": project})
        return s["id"]

    def test_start_creates_base_step(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        self.assertFalse(r["resumed"])
        self.assertEqual(r["draft"]["step"], "base")
        self.assertTrue(r["draft"]["inherit_base"])
        self.assertEqual(r["draft"]["base_title"], "学園もの")

    def test_base_inherit_keeps_defaults(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        d = server.wizard_load(r["draft"]["id"])
        server.wizard_set_base(d, sid, True)
        pub = server.wizard_public(d, True)
        self.assertEqual(pub["defaults"]["user_name"], "先生")
        self.assertEqual(pub["defaults"]["project"], "企画X")
        self.assertEqual(pub["step"], "idea")

    def test_base_no_inherit_uses_defaults(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        d = server.wizard_load(r["draft"]["id"])
        server.wizard_set_base(d, sid, False)
        pub = server.wizard_public(d, True)
        self.assertFalse(pub["inherit_base"])
        self.assertEqual(pub["defaults"]["user_name"], "あなた")
        self.assertEqual(pub["defaults"]["project"], "")
        self.assertEqual(pub["base_title"], "学園もの")

    def test_base_locked_after_input(self):
        sid = self.make_base()
        r = server.wizard_start({"base_sid": sid})
        d = server.wizard_load(r["draft"]["id"])
        server.wizard_set_base(d, sid, True)
        server.wslot_set(d, "rough_idea", "放課後", "user")
        with self.assertRaises(server.WizardError) as cm:
            server.wizard_set_base(d, sid, False)
        self.assertEqual(cm.exception.code, "base_locked")

    def test_start_without_base_has_no_inherit(self):
        r = server.wizard_start({})
        self.assertEqual(r["draft"]["step"], "base")
        self.assertFalse(r["draft"]["inherit_base"])
        self.assertEqual(r["draft"]["base_title"], "")


class TestQuestionsAndTags(WizardTestBase):
    def test_sanitize_keeps_question_slot_effect(self):
        d = self.new_draft()
        qs = server.wizard_sanitize_questions(d, [{
            "slot": "relationship",
            "text": "関係は？",
            "options": [
                {"label": "幼馴染", "effects": {"relationship": ["幼馴染"]}},
                {"label": "恋人", "effects": {"relationship": ["恋人"]}},
                {"label": "ライバル", "effects": {"relationship": ["ライバル"]}},
                {"label": "家族", "effects": {"relationship": ["家族"]}},
            ],
        }])
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["options"][0]["effects"]["relationship"], ["幼馴染"])

    def test_ai_effects_cannot_change_inherited_name_or_project(self):
        d = self.new_draft()
        opts = server.wizard_sanitize_options(d, "relationship", [{
            "label": "上司",
            "effects": {"relationship": ["上司"], "player_name": "部長", "project": "別企画"},
        }])
        self.assertEqual(opts[0]["effects"]["relationship"], ["上司"])
        self.assertNotIn("player_name", opts[0]["effects"])
        self.assertNotIn("project", opts[0]["effects"])

    def test_tags_can_be_read_twice(self):
        # 2回目の wizard_tags() がキャッシュタイムスタンプの型破壊で落ちないこと
        self.assertTrue(server.wizard_tags()["tags"])
        self.assertTrue(server.wizard_tags()["tags"])

    def test_ng_removes_conflicting_tag(self):
        d = self.new_draft()
        server.wizard_apply_tags(d, {
            "selected_tags": ["kink_ntr", "rel_pure"],
            "selected_ng": ["ng_ntr"],
            "custom_ng": "",
        })
        self.assertNotIn("kink_ntr", d["selected_tags"])
        self.assertIn("rel_pure", d["selected_tags"])

    def test_ng_minor_always_present(self):
        d = self.new_draft()
        server.wizard_apply_tags(d, {"selected_tags": [], "selected_ng": [], "custom_ng": ""})
        self.assertIn("ng_minor", d["selected_ng"])

    def test_derived_does_not_overwrite_auto(self):
        d = self.new_draft()
        server.wslot_set_auto(d, "relationship")
        d["selected_tags"] = ["rel_childhood"]
        server.wizard_recompute_derived(d)
        self.assertEqual(server.wslot_source(d, "relationship"), "auto")

    def test_clean_tags_does_not_substring_drop(self):
        self.assertEqual(
            server.wizard_clean_tags(["headdress", "flying", "han solo"]),
            ["headdress", "flying", "han solo"],
        )

    def test_ng_matches_normalized_custom_text(self):
        d = self.new_draft()
        server.wizard_apply_tags(d, {
            "selected_tags": ["kink_ntr", "rel_pure"],
            "selected_ng": [],
            "custom_ng": "N T R",
        })
        self.assertNotIn("kink_ntr", d["selected_tags"])
        self.assertIn("rel_pure", d["selected_tags"])


class TestCompose(WizardTestBase):
    def test_template_is_valid(self):
        d = self.new_draft()
        setting, warnings = server.wizard_template_compose(d)
        self.assertEqual(server.wizard_validate_compose(setting), [])
        self.assertTrue(setting["first_message"])
        self.assertTrue(warnings)

    def test_age_17_warns(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "17歳", "profile": "P", "speech": "SP"},
        }
        setting, meta = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["character"]["age"], "なし")
        self.assertTrue(any("18歳未満" in w for w in meta["warnings"]))

    def test_age_text_detection(self):
        self.assertTrue(server.wizard_age_is_minor("高校2年生"))
        self.assertTrue(server.wizard_age_is_minor("17歳"))
        self.assertFalse(server.wizard_age_is_minor("大学1年"))
        self.assertFalse(server.wizard_age_is_minor("なし"))

    def test_compose_sanitizes_opening_image(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "24", "profile": "P", "speech": "SP"},
            "opening_image": ["classroom", "masterpiece", "1girl"],
        }
        setting, _ = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["opening_image"], ["classroom", "1girl"])

    def test_age_highschool_becomes_none(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "高校2年生", "profile": "P", "speech": "SP"},
        }
        setting, meta = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["character"]["age"], "なし")
        self.assertTrue(any("18歳未満" in w for w in meta["warnings"]))

    def test_age_none_stays_none(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "A", "age": "なし", "profile": "P", "speech": "SP"},
        }
        setting, meta = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["character"]["age"], "なし")
        self.assertFalse(any("18歳未満" in w for w in meta["warnings"]))

    def test_preview_age_17_becomes_none(self):
        d = self.new_draft()
        self.with_compose(d)
        final, _ = server.wizard_build_final_setting(d, {"character": {"age": "17"}})
        self.assertEqual(final["character"]["age"], "なし")

    def test_preset_tags_override_ai_tags(self):
        d = self.new_draft()
        server.wslot_set(d, "partner_name", "美咲", "user")
        preset = [{"id": "p1", "name": "美咲", "tags": "1girl, black hair, red eyes"}]
        with mock.patch.object(server, "char_presets", return_value=preset):
            chars = server.wizard_build_chars(
                d, [{"name": "美咲", "tags": ["1girl", "blonde hair"]}], "美咲")
        self.assertEqual(chars[0]["tags"], ["1girl", "black hair", "red eyes"])
        self.assertEqual(chars[0]["source"], "preset")
        self.assertTrue(chars[0]["locked"])

    def test_chars_picks_matching_name(self):
        d = self.new_draft()
        raw = {
            "title": "T", "summary": "S", "instructions": "I" * 400,
            "scenario": "SC", "first_message": "F",
            "character": {"name": "美咲", "age": "24", "profile": "P", "speech": "SP"},
            "chars": [
                {"name": "別名", "tags": ["red hair"]},
                {"name": "美咲", "tags": ["black hair", "long hair"]},
            ],
        }
        setting, _ = server.wizard_sanitize_compose(d, raw)
        self.assertEqual(setting["chars"][0]["name"], "美咲")
        self.assertIn("black hair", setting["chars"][0]["tags"])

    def test_chars_are_normalized_for_app(self):
        d = self.new_draft()
        self.with_compose(d)
        d["compose"]["setting"]["chars"] = [
            {"name": "美咲", "tags": ["black hair", "long hair"], "source": "ai"}
        ]
        final, _ = server.wizard_build_final_setting(d, {})
        c = final["chars"][0]
        self.assertIsInstance(c["tags"], str)
        self.assertEqual(c["tags"], "black hair, long hair")
        self.assertTrue(c["id"])
        self.assertEqual(c["source"], "ai")

    def test_final_resets_initial_elapsed(self):
        d = self.new_draft()
        d["base_setting"] = {"initial_elapsed_time": {"day": 5, "period": "夜"},
                             "elapsed_time": {"day": 5, "period": "夜"}}
        self.with_compose(d)
        final, _ = server.wizard_build_final_setting(d, {})
        self.assertNotIn("initial_elapsed_time", final)

    def test_preview_edits_are_reflected_in_instructions(self):
        d = self.new_draft()
        self.with_compose(d)
        final, _ = server.wizard_build_final_setting(d, {
            "user_name": "太郎",
            "character": {"name": "美咲"},
            "summary": "新しいあらすじ",
        })
        self.assertEqual(final["user_name"], "太郎")
        self.assertIn("美咲", final["instructions"])
        self.assertIn("太郎", final["instructions"])
        self.assertIn("新しいあらすじ", final["instructions"])

    def test_compose_persists_auto_slots_on_disk_reload(self):
        d = self.new_draft()
        # 必須スロットを未設定のまま compose ジョブを実行
        server.wizard_save(d)
        job = {"job_id": "wj_test_persist", "draft_id": d["id"],
               "kind": "compose", "base_rev": d["rev"], "cancel": False}
        server.wizard_jobs[job["job_id"]] = job
        d["last_job"] = job["job_id"]
        server.wizard_save(d, bump=False)
        server.wizard_do_compose(job)
        fresh = server.wizard_load(d["id"])
        # missing_before のスロットが auto で保存され、reload 後も保持されていること
        self.assertEqual(server.wslot_source(fresh, "relationship"), "auto")
        self.assertEqual(server.wslot_source(fresh, "world"), "auto")


class TestCreate(WizardTestBase):
    def test_create_is_idempotent(self):
        d = self.new_draft()
        self.with_compose(d)
        r1 = server.wizard_create(d, {})
        r2 = server.wizard_create(server.wizard_load(d["id"]), {})
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["sid"], r2["sid"])
        self.assertTrue(os.path.exists(server.spath(r1["sid"])))

    def test_create_requires_compose(self):
        d = self.new_draft()
        with self.assertRaises(server.WizardError) as cm:
            server.wizard_build_final_setting(d, {})
        self.assertEqual(cm.exception.code, "wizard_not_composed")

    def test_create_with_use_template_without_compose(self):
        d = self.new_draft()
        r = server.wizard_create(d, {}, use_template=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["sid"])

    def test_created_channel_has_first_message(self):
        d = self.new_draft()
        self.with_compose(d)
        r = server.wizard_create(d, {})
        s = server.load(r["sid"])
        self.assertTrue(s["messages"])
        self.assertEqual(s["messages"][0]["role"], "assistant")

    def test_create_queues_opening_image(self):
        d = self.new_draft()
        self.with_compose(d)
        r = server.wizard_create(d, {})
        self.assertTrue(r["opening_image"])
        self.assertTrue(r["opening_image_mid"])
        s = server.load(r["sid"])
        m = s["messages"][0]
        self.assertEqual(m["id"], r["opening_image_mid"])
        self.assertEqual(m["image"]["status"], "queued")
        self.assertTrue(m["image"]["opening"])
        self.assertEqual(m["image"]["chars"], ["ヒロイン"])
        self.assertIn((r["sid"], m["id"]), list(server.queued_images))

    def test_create_skips_opening_image_when_off(self):
        d = self.new_draft()
        d["base_setting"] = {"image_mode": "off"}
        self.with_compose(d)
        r = server.wizard_create(d, {})
        self.assertFalse(r["opening_image"])
        s = server.load(r["sid"])
        self.assertNotIn("image", s["messages"][0])
        self.assertEqual(len(server.queued_images), 0)

    def test_create_does_not_duplicate_opening_image(self):
        d = self.new_draft()
        self.with_compose(d)
        r1 = server.wizard_create(d, {})
        r2 = server.wizard_create(server.wizard_load(d["id"]), {})
        self.assertEqual(r1["sid"], r2["sid"])
        self.assertEqual(len(server.queued_images), 1)
        s = server.load(r1["sid"])
        self.assertEqual(sum(1 for m in s["messages"] if (m.get("image") or {}).get("opening")), 1)

    def test_opening_image_uses_scene_tags_and_filters_appearance(self):
        d = self.new_draft()
        self.with_compose(d)
        d["compose"]["setting"]["opening_image"] = ["classroom", "masterpiece", "1girl", "black hair"]
        d["compose"]["setting"]["chars"][0]["tags"] = ["1girl", "black hair"]
        server.wizard_save(d)
        r = server.wizard_create(d, {})
        s = server.load(r["sid"])
        im = s["messages"][0]["image"]
        self.assertIn("classroom", im["prompt"])
        self.assertNotIn("masterpiece", im["prompt"])
        # 人数タグは画像生成に必要なので残す（AIが付け忘れたら1girlを補う）
        self.assertIn("1girl", im["prompt"])
        self.assertNotIn("black hair", im["prompt"])

    def test_opening_image_adds_count_when_missing(self):
        d = self.new_draft()
        self.with_compose(d)
        d["compose"]["setting"]["opening_image"] = ["classroom"]
        d["compose"]["setting"]["chars"][0]["tags"] = ["black hair"]
        server.wizard_save(d)
        r = server.wizard_create(d, {})
        s = server.load(r["sid"])
        im = s["messages"][0]["image"]
        self.assertIn("1girl", im["prompt"])
        self.assertNotIn("black hair", im["prompt"])

    def test_opening_image_failure_does_not_fail_create(self):
        d = self.new_draft()
        self.with_compose(d)
        with mock.patch.object(server, "wizard_enqueue_opening_image", side_effect=RuntimeError("boom")):
            r = server.wizard_create(d, {})
        self.assertTrue(r["ok"])
        self.assertFalse(r["opening_image"])
        self.assertTrue(any("開幕画像" in w for w in r["warnings"]))
        self.assertTrue(os.path.exists(server.spath(r["sid"])))


class TestJobsAndSweep(WizardTestBase):
    def test_stale_job_is_rejected(self):
        d = self.new_draft()
        job = {"job_id": "wj_0123456789", "draft_id": d["id"],
               "kind": "compose", "base_rev": d["rev"], "cancel": False}
        server.wizard_jobs[job["job_id"]] = job
        # wizard_do_compose が「last_job 不一致」で先に落ちないように合わせる
        d["last_job"] = job["job_id"]
        server.wizard_save(d, bump=False)
        d["rev"] += 1
        server.wizard_save(d, bump=False)
        with self.assertRaises(server.WizardError) as cm:
            server.wizard_do_compose(job)
        self.assertEqual(cm.exception.code, "stale")

    def test_compose_job_sets_composing(self):
        d = self.new_draft()
        # スレッドが即座に完了して status を戻さないようにブロックする
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.3)):
            job, _ = server.wizard_start_job(d, "compose")
            dd = server.wizard_load(d["id"])
            self.assertEqual(dd["status"], "composing")
            self.assertEqual(dd["step"], "creating")

    def test_sweep_removes_oldest(self):
        ids = []
        base_time = time.time() - 3600
        for i in range(server.WIZARD_DRAFT_LIMIT + 2):
            d = self.new_draft()
            # 7日間のTTLには引っかからず、上限（20件）超過で古いものから消えることを確認する
            d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base_time + i * 10))
            server.write_json(server.wizard_path(d["id"]), d)
            ids.append(d["id"])
            time.sleep(0.001)
        server.wizard_sweep()
        self.assertFalse(os.path.exists(server.wizard_path(ids[0])))
        self.assertTrue(os.path.exists(server.wizard_path(ids[-1])))

    def test_start_resumes_ready(self):
        d = self.new_draft()
        self.with_compose(d)
        r = server.wizard_start({"base_sid": ""})
        self.assertTrue(r["resumed"])
        self.assertEqual(r["draft"]["id"], d["id"])

    def test_start_reuses_empty_draft_without_prompt(self):
        # 何も入力していない下書きは「前回の続きから再開しますか？」を出さず、黙って使い回す
        r1 = server.wizard_start({"base_sid": ""})
        self.assertFalse(r1["resumed"])
        r2 = server.wizard_start({"base_sid": ""})
        self.assertFalse(r2["resumed"])
        self.assertEqual(r2["draft"]["id"], r1["draft"]["id"])

    def test_start_resumes_after_any_input(self):
        d = self.new_draft()
        server.wslot_set(d, "rough_idea", "海辺の町", "user")
        server.wizard_save(d)
        r = server.wizard_start({"base_sid": ""})
        self.assertTrue(r["resumed"])
        self.assertEqual(r["draft"]["id"], d["id"])

    def test_ai_disabled_uses_template(self):
        d = self.new_draft()
        d["model"] = {"provider": "none", "model": "", "ai_enabled": False}
        self.assertFalse(server.wizard_ai_enabled(d))
        setting, warnings = server.wizard_template_compose(d)
        self.assertEqual(server.wizard_validate_compose(setting), [])

    def test_ide_poke_models_is_tuple(self):
        with mock.patch.object(server, "ide_poke") as ip:
            ip.models.return_value = ({"models": [{"model": "m1", "label": "M1"}], "default": "m1"}, None)
            payload = server.wizard_models_payload("antigravity")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["models"][0]["id"], "m1")


class FakeHandler:
    def __init__(self, path="/", body=None):
        self.path = path
        self._body = body or {}
        self.status = 200
        self.result = None

    def blocked(self, u, post=False):
        return False

    def body(self):
        return self._body

    def send_json(self, data, code=200):
        self.status = code
        self.result = data
        return data


class TestJobCancelAndWorkerRecheck(WizardTestBase):
    def post(self, path, body):
        h = FakeHandler(path, body)
        server.wizard_route(h, path, body, "POST")
        return h.status, h.result

    def test_job_cancel_with_draft_id_clears_last_job_and_bumps_rev(self):
        d = self.new_draft()
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.5)):
            job, _ = server.wizard_start_job(d, "compose")
            orig_d = server.wizard_load(d["id"])
            self.assertEqual(orig_d["last_job"], job["job_id"])
            self.assertEqual(orig_d["status"], "composing")
            old_rev = orig_d["rev"]

            status, res = self.post("/api/wizard/job_cancel", {"job_id": job["job_id"], "draft_id": d["id"]})
            self.assertEqual(status, 200)
            self.assertTrue(res["ok"])
            self.assertTrue(res["canceled"])
            self.assertEqual(res["draft"]["last_job"], "")
            self.assertEqual(res["draft"]["status"], "interview")
            self.assertEqual(res["draft"]["step"], "tags")
            self.assertEqual(res["draft"]["rev"], old_rev + 1)

            saved = server.wizard_load(d["id"])
            self.assertEqual(saved["last_job"], "")
            self.assertEqual(saved["status"], "interview")
            self.assertEqual(saved["step"], "tags")
            self.assertEqual(saved["rev"], old_rev + 1)

    def test_job_cancel_with_existing_setting_restores_ready_and_preview(self):
        d = self.new_draft()
        self.with_compose(d)
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.5)):
            job, _ = server.wizard_start_job(d, "compose")
            status, res = self.post("/api/wizard/job_cancel", {"job_id": job["job_id"], "draft_id": d["id"]})
            self.assertEqual(status, 200)
            self.assertEqual(res["draft"]["status"], "ready")
            self.assertEqual(res["draft"]["step"], "preview")
            saved = server.wizard_load(d["id"])
            self.assertEqual(saved["status"], "ready")
            self.assertEqual(saved["step"], "preview")

    def test_cancel_causes_old_rev_slots_to_return_409(self):
        d = self.new_draft()
        base_rev = d["rev"]
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.5)):
            job, _ = server.wizard_start_job(d, "compose")

        status, _ = self.post("/api/wizard/job_cancel", {"job_id": job["job_id"], "draft_id": d["id"]})
        self.assertEqual(status, 200)

        status, res = self.post("/api/wizard/slots", {
            "draft_id": d["id"],
            "rev": base_rev,
            "slots": {"work": {"value": "作品名", "source": "user"}}
        })
        self.assertEqual(status, 409)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "rev")

    def test_wizard_job_running_is_false_after_cancel(self):
        d = self.new_draft()
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.5)):
            job, _ = server.wizard_start_job(d, "compose")
            self.assertTrue(server.wizard_job_running(job["job_id"]))
            self.assertTrue(server.wizard_job_running(server.wizard_load(d["id"])["last_job"]))

            status, _ = self.post("/api/wizard/job_cancel", {"job_id": job["job_id"], "draft_id": d["id"]})
            self.assertEqual(status, 200)
            self.assertFalse(server.wizard_job_running(job["job_id"]))
            saved = server.wizard_load(d["id"])
            self.assertFalse(server.wizard_job_running(saved["last_job"]))

    def test_cleaned_up_job_idempotent_cancel(self):
        d = self.new_draft()
        jid = "wj_cleanedup01"
        d["last_job"] = jid
        d["status"] = "composing"
        d["step"] = "creating"
        server.wizard_save(d, bump=False)
        base_rev = d["rev"]

        server.wizard_jobs.pop(jid, None)

        status, res = self.post("/api/wizard/job_cancel", {"job_id": jid, "draft_id": d["id"]})
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        self.assertFalse(res["canceled"])
        self.assertEqual(res["draft"]["last_job"], "")
        self.assertEqual(res["draft"]["status"], "interview")
        self.assertEqual(res["draft"]["step"], "tags")
        self.assertEqual(res["draft"]["rev"], base_rev + 1)

        saved = server.wizard_load(d["id"])
        self.assertEqual(saved["last_job"], "")
        self.assertEqual(saved["rev"], base_rev + 1)

        # Idempotent: call cancel again
        status2, res2 = self.post("/api/wizard/job_cancel", {"job_id": jid, "draft_id": d["id"]})
        self.assertEqual(status2, 200)
        self.assertTrue(res2["ok"])
        self.assertFalse(res2["canceled"])
        self.assertEqual(res2["draft"]["last_job"], "")
        self.assertEqual(res2["draft"]["rev"], base_rev + 1)

    def test_legacy_job_cancel_without_draft_id(self):
        d = self.new_draft()
        base_rev = d["rev"]
        with mock.patch.object(server, "wizard_do_compose", side_effect=lambda job: time.sleep(0.5)):
            job, _ = server.wizard_start_job(d, "compose")

        status, res = self.post("/api/wizard/job_cancel", {"job_id": job["job_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(res, {"ok": True})
        self.assertNotIn("draft", res)
        self.assertNotIn("canceled", res)

        saved = server.wizard_load(d["id"])
        self.assertEqual(saved["rev"], base_rev)
        self.assertTrue(job["cancel"])
        self.assertEqual(job["status"], "canceled")

    def test_non_ai_compose_canceled_before_final_save_does_not_resurrect_job(self):
        d = self.new_draft()
        d["model"] = {"provider": "none", "model": "", "ai_enabled": False}
        server.wizard_save(d)
        job = {
            "job_id": "wj_comp_cancel",
            "draft_id": d["id"],
            "kind": "compose",
            "status": "running",
            "cancel": False,
            "base_rev": d["rev"],
        }
        server.wizard_jobs[job["job_id"]] = job
        d["last_job"] = job["job_id"]
        d["status"] = "composing"
        server.wizard_save(d, bump=False)

        orig_template_compose = server.wizard_template_compose
        def hook(draft):
            # Cancel job right before final save in wizard_do_compose
            server.wizard_route(FakeHandler("/api/wizard/job_cancel"),
                                "/api/wizard/job_cancel",
                                {"job_id": job["job_id"], "draft_id": d["id"]}, "POST")
            return orig_template_compose(draft)

        with mock.patch.object(server, "wizard_template_compose", side_effect=hook):
            with self.assertRaises(server.WizardError) as cm:
                server.wizard_do_compose(job)
            self.assertIn(cm.exception.code, ("canceled", "stale"))

        saved = server.wizard_load(d["id"])
        self.assertEqual(saved["last_job"], "")
        self.assertIsNone(saved.get("compose"))
        self.assertEqual(saved["status"], "interview")

    def test_non_ai_questions_done_branch_canceled_does_not_save(self):
        d = self.new_draft()
        d["model"] = {"provider": "none", "model": "", "ai_enabled": False}
        server.wizard_save(d)
        job = {
            "job_id": "wj_ques_cancel",
            "draft_id": d["id"],
            "kind": "questions",
            "status": "running",
            "cancel": False,
            "base_rev": d["rev"],
        }
        server.wizard_jobs[job["job_id"]] = job
        d["last_job"] = job["job_id"]
        server.wizard_save(d, bump=False)

        orig_ai_enabled = server.wizard_ai_enabled
        def hook(draft):
            server.wizard_route(FakeHandler("/api/wizard/job_cancel"),
                                "/api/wizard/job_cancel",
                                {"job_id": job["job_id"], "draft_id": d["id"]}, "POST")
            return orig_ai_enabled(draft)

        with mock.patch.object(server, "wizard_ai_enabled", side_effect=hook):
            with self.assertRaises(server.WizardError) as cm:
                server.wizard_do_questions(job)
            self.assertIn(cm.exception.code, ("canceled", "stale"))

        saved = server.wizard_load(d["id"])
        self.assertEqual(saved["last_job"], "")
        self.assertFalse(saved.get("done_questions"))

    def test_run_canceled_error_handling(self):
        d = self.new_draft()
        # When wizard_do_compose raises WizardError(code="canceled"), run() sets status to "canceled", not "error"
        with mock.patch.object(server, "wizard_do_compose", side_effect=server.WizardError("キャンセルしました", code="canceled")):
            job, _ = server.wizard_start_job(d, "compose")
            time.sleep(0.1)
            self.assertEqual(job["status"], "canceled")
            self.assertIsNone(job["error"])

        # When an unexpected Exception occurs after cancel is set, run() sets status to "canceled", not "error"
        d2 = self.new_draft()
        def fail_with_cancel(j):
            j["cancel"] = True
            j["status"] = "canceled"
            raise RuntimeError("unexpected boom after cancel")

        with mock.patch.object(server, "wizard_do_compose", side_effect=fail_with_cancel):
            job2, _ = server.wizard_start_job(d2, "compose")
            time.sleep(0.1)
            self.assertEqual(job2["status"], "canceled")
            self.assertIsNone(job2["error"])

    def test_run_pre_canceled_does_not_run(self):
        d = self.new_draft()
        run_func = []
        def intercept_thread(target, daemon=True):
            run_func.append(target)
            m = mock.MagicMock()
            m.start = lambda: None
            return m

        with mock.patch("threading.Thread", side_effect=intercept_thread):
            job, _ = server.wizard_start_job(d, "compose")
            self.assertTrue(run_func)
            job["status"] = "canceled"
            job["cancel"] = True
            run_func[0]()
            self.assertEqual(job["status"], "canceled")
            self.assertIsNone(job["error"])

    def test_invalid_draft_id_returns_400(self):
        status, res = self.post("/api/wizard/job_cancel", {"job_id": "wj_test", "draft_id": "invalid/id"})
        self.assertEqual(status, 400)
        self.assertEqual(res["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()
