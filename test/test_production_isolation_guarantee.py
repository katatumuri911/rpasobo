# -*- coding: utf-8 -*-
"""test/test_production_isolation_guarantee.py: 本番ディレクトリ無汚染の自動検証"""
import os
import unittest
import server

class TestProductionIsolationGuarantee(unittest.TestCase):
    def test_production_directories_untouched(self):
        # 1. agents_md.json が初期状態（2バイト "{}" または空）のままであること
        agents_log = os.path.join(server.ROOT, "data", "agents_md.json")
        if os.path.exists(agents_log):
            with open(agents_log, "r", encoding="utf-8") as f:
                content = f.read().strip()
            self.assertIn(content, ("", "{}"), f"Production agents_md.json was modified: {content}")

        # 2. 本番 session ディレクトリ配下に AGENTS.md が生成されていないこと
        real_agents_md = os.path.join(server.ROOT, "session", "AGENTS.md")
        self.assertFalse(os.path.exists(real_agents_md), "Production session/AGENTS.md was created!")

        # 3. 本番 outbox ディレクトリ内に残骸ファイルが存在しないこと
        real_outbox = os.path.join(server.ROOT, "outbox")
        if os.path.exists(real_outbox):
            leftovers = [f for f in os.listdir(real_outbox) if f.endswith(".json")]
            self.assertEqual(leftovers, [], f"Leftover files found in production outbox: {leftovers}")

        # 4. 本番 data/sessions 配下にテスト用セッションが漏洩していないこと
        real_sess_dir = os.path.join(server.ROOT, "data", "sessions")
        if os.path.exists(real_sess_dir):
            test_files = [f for f in os.listdir(real_sess_dir) if f.startswith("rp_isolated_") or f.startswith("test_")]
            self.assertEqual(test_files, [], f"Test sessions leaked into production: {test_files}")
