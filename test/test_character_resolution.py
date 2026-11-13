import unittest
import sys
import os

# Add parent directory to path so we can import server
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import server


class TestFindChar(unittest.TestCase):
    def setUp(self):
        self.chars = [
            {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato, blonde hair, short hair"},
            {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina, black hair, long hair"},
            {"id": "c3", "name": "中原・ミズキ", "tags": "nakahara mizuki, brown hair, glasses"},
        ]

    def test_exact_match(self):
        # Exact match
        c = server.find_char(self.chars, "錦木千束")
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c1")

        # Exact match with different spacing
        c = server.find_char(self.chars, "錦木 千束")
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c1")

        # Exact match without fuzzy
        c = server.find_char(self.chars, "井ノ上たきな", fuzzy=False)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c2")

    def test_fuzzy_partial_match(self):
        # Substring in character name
        c = server.find_char(self.chars, "千束", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c1")

        # Fuzzy=False must NOT match partial
        c_off = server.find_char(self.chars, "千束", fuzzy=False)
        self.assertIsNone(c_off)

        # Longer input containing character name
        c = server.find_char(self.chars, "錦木千束ちゃん", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c1")

    def test_fuzzy_name_parts(self):
        # Middle dot delimiter
        c = server.find_char(self.chars, "ミズキ", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c3")

        c = server.find_char(self.chars, "中原", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c3")

    def test_fuzzy_english_tag_match(self):
        # English romaji matching tags
        c = server.find_char(self.chars, "chisato", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c1")

        c = server.find_char(self.chars, "nishikigi", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c1")

        c = server.find_char(self.chars, "takina", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "c2")

    def test_fuzzy_ambiguity_rejection(self):
        # Ambiguous query matching multiple characters
        ambiguous_chars = [
            {"id": "a1", "name": "美樹さやか", "tags": "miki sayaka"},
            {"id": "a2", "name": "美樹マドカ", "tags": "miki madoka"},
        ]
        # "美樹" matches both
        c = server.find_char(ambiguous_chars, "美樹", fuzzy=True)
        self.assertIsNone(c)

        # But specific match succeeds
        c = server.find_char(ambiguous_chars, "さやか", fuzzy=True)
        self.assertIsNotNone(c)
        self.assertEqual(c["id"], "a1")

    def test_fuzzy_short_input_rejection(self):
        # Input < 2 chars must be rejected in fuzzy mode
        c = server.find_char(self.chars, "千", fuzzy=True)
        self.assertIsNone(c)
        c = server.find_char(self.chars, "a", fuzzy=True)
        self.assertIsNone(c)
        c = server.find_char(self.chars, "", fuzzy=True)
        self.assertIsNone(c)


class TestMergeChars(unittest.TestCase):
    def test_merge_exact_and_fuzzy(self):
        chars = [
            {"id": "c1", "name": "錦木千束", "tags": "blonde hair, red eyes", "locked": False, "source": "ai"},
            {"id": "c2", "name": "井ノ上たきな", "tags": "black hair, purple eyes", "locked": True, "source": "user"},
        ]

        # 1. Exact match updates tags if unlocked
        server.merge_chars(chars, [{"name": "錦木千束", "tags": "blonde hair, ribbon, red eyes"}])
        c1 = server.find_char(chars, "錦木千束")
        self.assertIn("ribbon", c1["tags"])

        # 2. Exact match does NOT update if locked
        server.merge_chars(chars, [{"name": "井ノ上たきな", "tags": "short hair, blue eyes"}])
        c2 = server.find_char(chars, "井ノ上たきな")
        self.assertEqual(c2["tags"], "black hair, purple eyes")

        # 3. Fuzzy match (e.g. AI passed "千束" instead of "錦木千束")
        # Must NOT overwrite original tags and must NOT add a duplicate "千束"
        server.merge_chars(chars, [{"name": "千束", "tags": "corrupted tags"}])
        self.assertEqual(len(chars), 2)
        c1_after = server.find_char(chars, "錦木千束")
        self.assertNotIn("corrupted", c1_after["tags"])

        # 4. New character added
        presets = [{"id": "p1", "name": "クルミ", "tags": "kurumi, blonde hair, twin tails"}]
        server.merge_chars(chars, [{"name": "クルミ", "tags": "ai kurumi tags"}], presets=presets)
        self.assertEqual(len(chars), 3)
        c3 = server.find_char(chars, "クルミ")
        self.assertIsNotNone(c3)
        self.assertEqual(c3["tags"], "kurumi, blonde hair, twin tails")
        self.assertTrue(c3["locked"])
        self.assertEqual(c3["source"], "preset")


class TestResolveImageChars(unittest.TestCase):
    def setUp(self):
        self.setting = {
            "character": {"name": "錦木千束"},
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato, blonde hair"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina, black hair"},
            ]
        }

    def test_step1_raw_names_string_list_dict(self):
        # AI specified single name as str
        res = server.resolve_image_chars(self.setting, raw_names="千束")
        self.assertEqual([c["name"] for c in res], ["錦木千束"])

        # AI specified comma-separated str
        res = server.resolve_image_chars(self.setting, raw_names="千束, たきな")
        self.assertEqual([c["name"] for c in res], ["錦木千束", "井ノ上たきな"])

        # AI specified list of str
        res = server.resolve_image_chars(self.setting, raw_names=["たきな"])
        self.assertEqual([c["name"] for c in res], ["井ノ上たきな"])

        # AI specified list of dict
        res = server.resolve_image_chars(self.setting, raw_names=[{"name": "千束"}])
        self.assertEqual([c["name"] for c in res], ["錦木千束"])

    def test_step2_active_state_filtering(self):
        # When active_state contains only Takina, candidates should be Takina
        active_state = [
            {"name": "井ノ上たきな", "situation": ["カフェで作業中"]},
            {"name": "通行人", "remove": True}
        ]
        # Without msg_text or raw_names, fallback within candidates chooses Takina
        res = server.resolve_image_chars(self.setting, active_state=active_state)
        self.assertEqual([c["name"] for c in res], ["井ノ上たきな"])

    def test_step3_multi_girls_composition(self):
        # When prompt_text has 2girls and both appear in msg_text
        res = server.resolve_image_chars(
            self.setting,
            prompt_text="2girls, masterpiece, smile",
            msg_text="千束とたきなは一緒に歩いていた。"
        )
        self.assertEqual(set(c["name"] for c in res), {"錦木千束", "井ノ上たきな"})

        # When prompt_text is 1girl, even if both appear, only 1 is returned
        res_solo = server.resolve_image_chars(
            self.setting,
            prompt_text="1girl, solo, smile",
            msg_text="千束とたきなは一緒に歩いていた。"
        )
        self.assertEqual(len(res_solo), 1)
        # Chisato wins because is_main is True
        self.assertEqual(res_solo[0]["name"], "錦木千束")

    def test_step4_no_kina_false_positive(self):
        # CRITICAL TEST: 「きな」誤爆の完全防止
        # "大きな木の下で" contains "きな", but clean_n[-2:] is NOT performed.
        # Takina must have score 0, and fallback to main character (Chisato)
        setting_no_space = {
            "character": {"name": "錦木千束"},
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina"},
            ]
        }
        res = server.resolve_image_chars(
            setting_no_space,
            msg_text="大きな木の下で風を感じていた。"
        )
        # Since "きな" does not match, Takina count is 0. Chisato is chosen by fallback.
        self.assertEqual([c["name"] for c in res], ["錦木千束"])

        # When Takina is explicitly mentioned by safe alias "たきな"
        res_takina = server.resolve_image_chars(
            setting_no_space,
            msg_text="たきなが静かに本を読んでいる。"
        )
        self.assertEqual([c["name"] for c in res_takina], ["井ノ上たきな"])

    def test_step5_fallback_main_char_and_single(self):
        # Empty text and empty raw_names -> fallback to main character
        res = server.resolve_image_chars(self.setting, msg_text="")
        self.assertEqual([c["name"] for c in res], ["錦木千束"])

        # Single registered character fallback
        single_setting = {
            "chars": [{"id": "s1", "name": "千束", "tags": "blonde hair"}]
        }
        res_single = server.resolve_image_chars(single_setting, msg_text="")
        self.assertEqual([c["name"] for c in res_single], ["千束"])


class TestBuildPrompt(unittest.TestCase):
    def test_build_prompt_empty_chars_auto_resolution(self):
        setting = {
            "image": {"checkpoint": "test.safetensors"},
            "character": {"name": "錦木千束"},
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato, blonde hair"}
            ]
        }
        # Image with empty chars
        img = {"prompt": "1girl, solo, smile", "chars": []}
        pos, neg = server.build_prompt(setting, img)

        # img["chars"] must be auto-populated
        self.assertEqual(img["chars"], ["錦木千束"])
        # Appearance tags must be present in pos prompt
        self.assertIn("nishikigi chisato", pos)
        self.assertIn("blonde hair", pos)
        self.assertIn("smile", pos)

    def test_build_prompt_intimacy_removes_solo(self):
        setting = {
            "image": {},
            "character": {"name": "千束"},
            "chars": [
                {"id": "c1", "name": "千束", "tags": "chisato, solo, blonde hair"}
            ]
        }
        # Scene has intimacy tag (e.g. penetration)
        img = {"prompt": "penis, vaginal penetration, missionary", "chars": ["千束"]}
        pos, neg = server.build_prompt(setting, img)

        # "solo" from character tags must be excluded
        self.assertNotIn("solo", pos.split(", "))
        self.assertIn("chisato", pos)
        self.assertIn("vaginal penetration", pos)

    def test_build_prompt_preserves_existing_valid_chars(self):
        setting = {
            "image": {},
            "character": {"name": "錦木千束"},
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina"},
            ]
        }
        img = {"prompt": "1girl, smile", "chars": ["井ノ上たきな"]}
        pos, neg = server.build_prompt(setting, img)
        self.assertEqual(img["chars"], ["井ノ上たきな"])
        self.assertIn("inoue takina", pos)
        self.assertNotIn("nishikigi chisato", pos)

    def test_build_prompt_legacy_fallback(self):
        # When chars is empty and no chars match, fall back to character_tags if present
        setting = {
            "image": {"character_tags": "legacy_girl, legacy_hair"},
            "chars": []
        }
        img = {"prompt": "1girl, smile", "chars": []}
        pos, neg = server.build_prompt(setting, img)
        self.assertIn("legacy_girl", pos)


class TestEdgeCasesAndSteps(unittest.TestCase):
    def test_step1_always_show_keys(self):
        self.assertIn("chars", server.ALWAYS_SHOW_KEYS)
        self.assertIn("elapsed_time", server.ALWAYS_SHOW_KEYS)
        self.assertNotIn("output.md", server.ALWAYS_SHOW_KEYS)

    def test_step1_output_text_diff_mode(self):
        st = {"image_mode": "always", "image": {"width": 896, "height": 1152}}
        out = server.output_text(st, is_diff=True)
        self.assertIn("image.characters", out)
        self.assertIn("固定キャラ一覧と同じ表記", out)

    def test_step2_empty_or_none_setting(self):
        res = server.resolve_image_chars(None)
        self.assertEqual(res, [])
        res2 = server.resolve_image_chars({})
        self.assertEqual(res2, [])
        res3 = server.resolve_image_chars({"chars": []})
        self.assertEqual(res3, [])

    def test_step2_active_state_all_removed_falls_back_to_reg_chars(self):
        setting = {
            "character": {"name": "錦木千束"},
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina"},
            ]
        }
        active_state = [
            {"name": "錦木千束", "remove": True},
            {"name": "井ノ上たきな", "remove": True},
        ]
        # Since all items in active_state have remove=True, state_chars is empty,
        # so candidates falls back to reg_chars, and Chisato is chosen by main_char fallback
        res = server.resolve_image_chars(setting, active_state=active_state)
        self.assertEqual([c["name"] for c in res], ["錦木千束"])

    def test_step3_multi_girls_tag_variations(self):
        setting = {
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina"},
                {"id": "c3", "name": "中原ミズキ", "tags": "nakahara mizuki"},
            ]
        }
        for tag in ["3girls", "multiple girls", "multiple_girls", "4girls"]:
            res = server.resolve_image_chars(
                setting,
                prompt_text=f"{tag}, masterpiece",
                msg_text="千束とたきなとミズキが話す。"
            )
            self.assertEqual(len(res), 3, f"Failed for tag: {tag}")

    def test_step3_build_context_prev_chars_supplement(self):
        setting = {
            "image_mode": "always",
            "character": {"name": "錦木千束"},
            "chars": [{"id": "c1", "name": "錦木千束", "tags": "blonde hair"}]
        }
        # Simulate last_image_prompt returning (prompt, [])
        # resolve_image_chars should be called to supplement prev_chars
        res = server.resolve_image_chars(setting, prompt_text="1girl, blonde hair, smile")
        self.assertEqual([c["name"] for c in res], ["錦木千束"])

    def test_step4_no_appearance_tags_in_aliases_red_ribbon(self):
        # BUG FIX TEST: Danbooru appearance tags (like 'red ribbon') must NOT be counted as character aliases
        setting = {
            "character": {"name": "錦木千束"},
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato, blonde hair, red ribbon"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina, black hair"}
            ]
        }
        # In this message, Takina is explicitly mentioned. The text mentions 'red ribbon', which is in Chisato's tags,
        # but NOT a character name. Takina must win!
        res = server.resolve_image_chars(setting, msg_text="たきなは赤いリボン(red ribbon)を見つめていた。")
        self.assertEqual([c["name"] for c in res], ["井ノ上たきな"])

    def test_find_char_rejects_count_tag(self):
        # Composition tags must NEVER match characters even if present in tags
        chars = [{"id": "c1", "name": "錦木千束", "tags": "1girl, solo, blonde hair"}]
        self.assertIsNone(server.find_char(chars, "1girl", fuzzy=True))
        self.assertIsNone(server.find_char(chars, "solo", fuzzy=True))
        self.assertIsNone(server.find_char(chars, "2girls", fuzzy=True))

    def test_char_key_and_find_char_non_string_safety(self):
        self.assertEqual(server.char_key(None), "")
        self.assertEqual(server.char_key(123), "")
        self.assertEqual(server.char_key({"name": "foo"}), "")
        chars = [{"id": "c1", "name": "錦木千束", "tags": "blonde hair"}, {"id": "c2", "name": 123}]
        self.assertIsNone(server.find_char(chars, None, fuzzy=True))
        self.assertIsNone(server.find_char(chars, 123, fuzzy=True))
        self.assertIsNone(server.find_char(None, "千束", fuzzy=True))

    def test_split_tags_list_input(self):
        self.assertEqual(server.split_tags(["tag1, tag2", "tag3"]), ["tag1", "tag2", "tag3"])
        self.assertEqual(server.split_tags(None), [])
        self.assertEqual(server.split_tags(123), [])

    def test_merge_chars_preset_canonical_name_and_list_tags(self):
        chars = []
        presets = [{"id": "p1", "name": "錦木千束", "tags": "nishikigi chisato, blonde hair"}]
        # AI introduces character as "千束" with tags as list
        server.merge_chars(chars, [{"name": "千束", "tags": ["chisato", "blonde hair"]}], presets=presets)
        self.assertEqual(len(chars), 1)
        # Canonical preset name should be used
        self.assertEqual(chars[0]["name"], "錦木千束")
        self.assertIn("blonde hair", chars[0]["tags"])

    def test_resolve_image_chars_raw_names_variations(self):
        setting = {
            "chars": [
                {"id": "c1", "name": "錦木千束", "tags": "nishikigi chisato"},
                {"id": "c2", "name": "井ノ上たきな", "tags": "inoue takina"}
            ]
        }
        # Dict with comma separated names
        res1 = server.resolve_image_chars(setting, raw_names={"name": "千束, たきな"})
        self.assertEqual(len(res1), 2)
        # Tuple
        res2 = server.resolve_image_chars(setting, raw_names=("千束",))
        self.assertEqual([c["name"] for c in res2], ["錦木千束"])


if __name__ == "__main__":
    unittest.main()
