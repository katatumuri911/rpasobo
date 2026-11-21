# -*- coding: utf-8 -*-
"""P-08: outbox ワーカーのアイドル時バックオフ（操作後10分は通常速度）"""
import time
import unittest

import server
from test.support import IsolatedTestCase


class TestOutboxBackoff(IsolatedTestCase):
    def tearDown(self):
        server._outbox_active_until = 0.0
        server.outbox_event.clear()
        super().tearDown()

    def test_active_uses_fast_polling(self):
        self.assertEqual(server._outbox_delay(0, True), 0.05)
        self.assertEqual(server._outbox_delay(0.1, True), 0.05)
        self.assertEqual(server._outbox_delay(10, True), 0.3)

    def test_inactive_tiers(self):
        self.assertEqual(server._outbox_delay(30, False), 0.3)
        self.assertEqual(server._outbox_delay(59.9, False), 0.3)
        self.assertEqual(server._outbox_delay(60, False), 1.0)
        self.assertEqual(server._outbox_delay(599, False), 1.0)
        self.assertEqual(server._outbox_delay(600, False), 5.0)
        self.assertEqual(server._outbox_delay(3600, False), 5.0)

    def test_touch_marks_active_and_wakes_worker(self):
        server._outbox_active_until = 0.0
        server.outbox_event.clear()
        before = time.monotonic()
        server.touch_outbox_activity()
        self.assertGreaterEqual(server._outbox_active_until, before + server.OUTBOX_ACTIVE_WINDOW)
        self.assertGreater(server._outbox_active_until, time.monotonic())
        self.assertTrue(server.outbox_event.is_set())

    def test_active_window_is_ten_minutes(self):
        self.assertEqual(server.OUTBOX_ACTIVE_WINDOW, 600)
        self.assertEqual(server.OUTBOX_DEEP_IDLE, 5.0)


if __name__ == "__main__":
    unittest.main()
