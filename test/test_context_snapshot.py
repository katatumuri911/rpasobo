# -*- coding: utf-8 -*-
"""test/test_context_snapshot.py: コンテキスト生成・スナップショット契約テスト (P4)"""
import hashlib
import json
import os
import re

import server
from test.support import IsolatedTestCase

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden", "prompt")


def normalize_prompt_text(text, test_dir=None):
    """テンポラリパス、ISO日時、UUIDをセクション単位で正規化する"""
    if not text:
        return ""
    # 1. テンポラリパス正規化
    if test_dir:
        text = text.replace(test_dir.replace("/", "\\"), "<TMP>")
        text = text.replace(test_dir.replace("\\", "/"), "<TMP>")
        text = text.replace(test_dir, "<TMP>")
    # 2. ISO日時正規化 (例: 2026-11-20T04:54:52, 2026-11-20 04:54:52)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b", "<TS>", text)
    # 3. UUID 正規化
    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<UUID>", text)
    return text


def load_or_update_golden(rel_name, current_data):
    """スナップショット JSON の読み込みまたは RPCHAT_UPDATE_GOLDEN=1 時の更新"""
    path = os.path.join(GOLDEN_DIR, rel_name)
    if os.environ.get("RPCHAT_UPDATE_GOLDEN") == "1":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current_data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Golden snapshot not found: {path}. Run with RPCHAT_UPDATE_GOLDEN=1 to create it.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestContextSnapshot(IsolatedTestCase):
    """コンテキスト生成と静的プロンプトの不変性を検証するスナップショットテスト"""

    def test_output_text_default(self):
        """Scenario 1: デフォルト設定での静的プロンプトハッシュ・バイト数照合"""
        ot = server.output_text(server.merge_defaults({}))
        ot_norm = normalize_prompt_text(ot, self.test_dir)
        ot_b = ot_norm.encode("utf-8")
        current_data = {
            "byte_length": len(ot_b),
            "sha256": hashlib.sha256(ot_b).hexdigest()
        }
        golden = load_or_update_golden("output_text_default.json", current_data)
        self.assertEqual(current_data["byte_length"], golden["byte_length"], "output_text byte length mismatch")
        self.assertEqual(current_data["sha256"], golden["sha256"], "output_text sha256 mismatch")

    def test_build_full_first(self):
        """Scenario 2: 初回ターンでの full モードコンテキスト生成"""
        s = server.create_session({"ide_session": "mock_ide_conv"})
        u1 = server.add_user_msg(s, "こんにちは")
        server.save(s)

        got = server.build_context(s, u1)
        self.assertEqual(got["mode"], "full")
        self.assertIn("初めて", got["reason"])

    def test_build_diff_second(self):
        """Scenario 3: 2ターン目での diff モードとセクションハッシュのスナップショット照合"""
        s = server.create_session({"ide_session": "mock_ide_conv"})
        u1 = server.add_user_msg(s, "こんにちは")
        server.save(s)
        server.build_context(s, u1)

        a1 = {"role": "assistant", "id": "a1", "text": "どうも", "ts": server.now()}
        s["messages"].append(a1)
        server.save(s)
        server.note_reply(s, u1["id"], a1)

        u2 = server.add_user_msg(s, "続きです")
        server.save(s)

        got = server.build_context(s, u2)
        self.assertEqual(got["mode"], "diff")
        self.assertEqual(got["reason"], "")

        sec_hashes = []
        for sec in got.get("sections", []):
            norm_t = normalize_prompt_text(sec.get("text", ""), self.test_dir)
            sec_h = hashlib.sha256(norm_t.encode("utf-8")).hexdigest()
            sec_hashes.append({"label": sec.get("label"), "sha256": sec_h})

        current_data = {
            "mode": got["mode"],
            "reason": got["reason"],
            "sections": sec_hashes
        }
        golden = load_or_update_golden("build_diff_second.json", current_data)
        self.assertEqual(current_data, golden)

    def test_build_full_reset_fallback(self):
        """Scenario 4: リセットフラグによる full モードへのフォールバック"""
        s = server.create_session({"ide_session": "mock_ide_conv"})
        u1 = server.add_user_msg(s, "こんにちは")
        server.save(s)
        server.build_context(s, u1)

        a1 = {"role": "assistant", "id": "a1", "text": "どうも", "ts": server.now()}
        s["messages"].append(a1)
        server.save(s)
        server.note_reply(s, u1["id"], a1)

        # 4a: sent["why"] = "リセット" の場合（仕様書ケース: "リセット" が reason に含まれる）
        sent = server.read_sent(s["id"])
        sent["reset"] = True
        sent["why"] = "リセット"
        server.write_sent(s["id"], sent)

        u3 = server.add_user_msg(s, "リセット後")
        server.save(s)

        got = server.build_context(s, u3)
        self.assertEqual(got["mode"], "full")
        self.assertIn("リセット", got["reason"])

        # 4b: sent["why"] が省略された場合（/api/context/reset 既定動作: "UIで「次は全部送る」が押された"）
        sent2 = server.read_sent(s["id"])
        sent2["reset"] = True
        sent2.pop("why", None)
        server.write_sent(s["id"], sent2)

        u4 = server.add_user_msg(s, "2回目リセット後")
        server.save(s)

        got2 = server.build_context(s, u4)
        self.assertEqual(got2["mode"], "full")
        self.assertEqual(got2["reason"], "UIで「次は全部送る」が押された")
