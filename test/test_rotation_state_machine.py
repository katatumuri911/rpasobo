# -*- coding: utf-8 -*-
"""test/test_rotation_state_machine.py: 世代交代仕様書の自動整合性検証"""
import os
import re
import unittest
import server

class TestRotationDocVerification(unittest.TestCase):
    def test_verify_names_exist_in_server(self):
        doc_path = os.path.join(server.ROOT, "docs", "rotation_state_machine.md")
        self.assertTrue(os.path.exists(doc_path), f"Document not found: {doc_path}")

        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.search(r'<!--\s*verify-names:\s*(.*?)\s*-->', content)
        self.assertIsNotNone(match, "verify-names tag not found in document")

        names = match.group(1).split()
        server_code = open(os.path.join(server.ROOT, "server.py"), "r", encoding="utf-8").read()

        missing = [n for n in names if n not in server_code]
        self.assertEqual(missing, [], f"Names defined in doc are MISSING in server.py: {missing}")

    def test_all_rotation_steps_documented(self):
        doc_path = os.path.join(server.ROOT, "docs", "rotation_state_machine.md")
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()

        # server.py の ROTATION_STEPS に定義された全フェーズが網羅されていること
        for step in server.ROTATION_STEPS:
            self.assertIn(step, content, f"Rotation step '{step}' in server.ROTATION_STEPS is not documented")

        # 初期武装状態 'armed' も文書内に定義されていること
        self.assertIn("armed", content, "'armed' phase is not documented in rotation_state_machine.md")
