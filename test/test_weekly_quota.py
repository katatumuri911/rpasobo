# -*- coding: utf-8 -*-
"""Antigravity の週の利用枠（RetrieveUserQuotaSummary）まわりのテスト。

入力欄の「残り x%」は 5 時間の枠ではなく週の枠を出す。
モデル → 利用枠グループの対応が崩れると、別モデルの残りを表示してしまうので固定する。
"""
import unittest
from unittest.mock import patch

import ide_poke

GROUPS = [
    {"name": "Gemini Models", "models": ["Gemini 3.8 Flash"],
     "weekly": {"left": 0.45, "reset": "2026-11-21T01:35:23Z"}},
    {"name": "Claude and GPT models", "models": ["Claude Sonnet 4.6", "Claude Opus 4.6", "GPT-OSS 120B"],
     "weekly": {"left": 1.0, "reset": "2026-11-17T00:40:13Z"}},
]


class TestQuotaGroupMapping(unittest.TestCase):
    def tearDown(self):
        ide_poke._quota_cache = (0.0, None)

    def test_base_label_strips_thinking_suffix(self):
        self.assertEqual("Gemini 3.8 Flash", ide_poke._base_label("Gemini 3.8 Flash (High)"))
        self.assertEqual("Claude Sonnet 4.6", ide_poke._base_label("Claude Sonnet 4.6 (Thinking)"))
        self.assertEqual("Gemini 3.8 Flash", ide_poke._base_label("Gemini 3.8 Flash（中）"))
        self.assertEqual("", ide_poke._base_label(""))

    def test_group_for_matches_model_names(self):
        self.assertEqual("Gemini Models", ide_poke._group_for("Gemini 3.8 Flash (Medium)", GROUPS)["name"])
        self.assertEqual("Claude and GPT models", ide_poke._group_for("Claude Opus 4.6 (Thinking)", GROUPS)["name"])
        # 一覧にない新しい Gemini も、グループ名で寄せる
        self.assertEqual("Gemini Models", ide_poke._group_for("Gemini 4 Carbon", GROUPS)["name"])
        self.assertEqual("Claude and GPT models", ide_poke._group_for("GPT-OSS 120B (Medium)", GROUPS)["name"])

    def test_quota_groups_parses_buckets_and_only_keeps_weekly(self):
        body = {"response": {"groups": [{
            "displayName": "Gemini Models",
            "description": "Models within this group: Gemini 3.8 Flash",
            "buckets": [
                {"window": "weekly", "remainingFraction": 0.45, "resetTime": "2026-11-21T01:35:23Z"},
                {"window": "5h", "remainingFraction": 0.9, "resetTime": "2026-11-16T07:34:58Z"},
            ],
        }]}}
        with patch("ide_poke._call", return_value=(200, body)) as call:
            groups = ide_poke._quota_groups(1, "tok")
            again = ide_poke._quota_groups(1, "tok")   # 30秒キャッシュ
        self.assertEqual([{"name": "Gemini Models", "models": ["Gemini 3.8 Flash"],
                           "weekly": {"left": 0.45, "reset": "2026-11-21T01:35:23Z"}}], groups)
        self.assertEqual(1, call.call_count, "2回目はキャッシュから返す")
        self.assertEqual(groups, again)

    def test_models_attaches_weekly_and_keeps_five_hour_left(self):
        rows = [
            {"model": "M318", "label": "Gemini 3.8 Flash (High)", "left": 0.99, "reset": "2026-11-16T07:34:58Z"},
            {"model": "M35", "label": "Claude Sonnet 4.6 (Thinking)", "left": 1.0, "reset": "2026-11-16T08:18:26Z"},
        ]
        with patch("ide_poke.hub_server", return_value=(1, "tok")), \
             patch("ide_poke._model_data", return_value=(rows, "M318")), \
             patch("ide_poke._quota_groups", return_value=GROUPS):
            res, why = ide_poke.models()
        self.assertIsNone(why)
        self.assertEqual({"left": 0.45, "reset": "2026-11-21T01:35:23Z"}, res["models"][0]["weekly"])
        self.assertEqual("Gemini Models", res["models"][0]["quotaGroup"])
        self.assertEqual(1.0, res["models"][1]["weekly"]["left"])
        # 送信前の「使い切り」注意に使う 5 時間の値はそのまま残す
        self.assertEqual(0.99, res["models"][0]["left"])
        self.assertEqual(2, len(res["quota"]))

    def test_models_without_quota_summary_still_work(self):
        rows = [{"model": "M318", "label": "Gemini 3.8 Flash (High)", "left": 0.99, "reset": ""}]
        with patch("ide_poke.hub_server", return_value=(1, "tok")), \
             patch("ide_poke._model_data", return_value=(rows, "M318")), \
             patch("ide_poke._quota_groups", return_value=None):
            res, why = ide_poke.models()
        self.assertIsNone(why)
        self.assertNotIn("weekly", res["models"][0])
        self.assertEqual([], res["quota"])


if __name__ == "__main__":
    unittest.main()
