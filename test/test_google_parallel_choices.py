import unittest
import copy
import server

class TestGoogleParallelChoices(unittest.TestCase):
    def test_has_parallel_choices_ide(self):
        # Antigravity で choices_ide_session 未設定
        st_ag_single = {"ai_provider": "antigravity", "suggest_choices": True, "ide_session": "ide-1", "choices_ide_session": ""}
        self.assertFalse(server.has_parallel_choices_ide(st_ag_single))
        self.assertFalse(server.has_choices_ide_session(st_ag_single))

        # Antigravity で choices_ide_session 設定済み
        st_ag_par = {"ai_provider": "antigravity", "suggest_choices": True, "ide_session": "ide-1", "choices_ide_session": "ide-2"}
        self.assertTrue(server.has_parallel_choices_ide(st_ag_par))
        self.assertTrue(server.has_choices_ide_session(st_ag_par))

        # Google API 利用時: has_parallel_choices_ide は常に True、has_choices_ide_session は False
        st_google = {"ai_provider": "google", "suggest_choices": True, "ide_session": "ide-1", "choices_ide_session": ""}
        self.assertTrue(server.has_parallel_choices_ide(st_google))
        self.assertFalse(server.has_choices_ide_session(st_google))

        # suggest_choices が False のときは両方 False
        st_no_sug = {"ai_provider": "google", "suggest_choices": False}
        self.assertFalse(server.has_parallel_choices_ide(st_no_sug))
        self.assertFalse(server.has_choices_ide_session(st_no_sug))

    def test_choices_ide_of_google_guard(self):
        # Google 利用中は choices_ide_of が空文字を返す
        st_google = {"ai_provider": "google", "suggest_choices": True, "ide_session": "ide-1", "choices_ide_session": "ide-2"}
        self.assertEqual(server.choices_ide_of(st_google), "")

        # Antigravity 利用時は正しく返す
        st_ag = {"ai_provider": "antigravity", "suggest_choices": True, "ide_session": "ide-1", "choices_ide_session": "ide-2"}
        self.assertEqual(server.choices_ide_of(st_ag), "ide-2")

    def test_set_google_status_user_priority(self):
        sid = "test_status_prio_sid"
        # 1. user タスクを開始
        server._set_google_status(sid, "user", "requesting", model="model-a")
        st = server.google_status[sid]
        self.assertEqual(st["task_type"], "user")
        self.assertEqual(st["state"], "requesting")

        # 2. choices タスクを更新 -> user が requesting なので top は user のまま維持
        server._set_google_status(sid, "choices", "waiting_import", model="model-b")
        st = server.google_status[sid]
        self.assertEqual(st["task_type"], "user")
        self.assertEqual(st["state"], "requesting")
        self.assertIn("choices", st["tasks"])
        self.assertEqual(st["tasks"]["choices"]["state"], "waiting_import")

        # 3. choices をクリア -> user は残る
        server._google_clear_status(sid, "choices")
        st = server.google_status[sid]
        self.assertEqual(st["task_type"], "user")
        self.assertNotIn("choices", st.get("tasks", {}))

        # 4. 全クリア
        server._google_clear_status(sid)
        self.assertNotIn(sid, server.google_status)

    def test_google_pick_tasks_parallel_and_orig_id(self):
        sid = "test_pick_sid"
        s = {
            "id": sid,
            "setting": {"ai_provider": "google", "suggest_choices": True},
            "messages": [
                {
                    "id": "u1",
                    "role": "user",
                    "status": "pending",
                    "choices_status": "pending",
                    "choices_type": "normal",
                }
            ]
        }
        # mock load
        orig_load = server.load
        orig_list_ids = server.list_ids
        try:
            server.list_ids = lambda: [sid]
            server.load = lambda _sid, readonly=True: s
            server.google_inflight.clear()
            server.google_retry_at.clear()

            tasks = server._google_pick_tasks()
            # user と choices の2タスクが同時にピックアップされる
            task_types = [t[2] for t in tasks]
            self.assertIn("user", task_types)
            self.assertIn("choices", task_types)

            # choices の tkey が orig_mid (u1) で作られていること
            cho_task = next(t for t in tasks if t[2] == "choices")
            self.assertEqual(cho_task[3], f"{sid}:choices:normal:u1")

            # 本文が完了して assistant に引き継がれたケース
            s["messages"] = [
                {"id": "u1", "role": "user", "status": "answered"},
                {
                    "id": "a1",
                    "role": "assistant",
                    "choices_status": "pending",
                    "choices_type": "normal",
                    "choices_orig_id": "u1",
                }
            ]
            tasks2 = server._google_pick_tasks()
            task_types2 = [t[2] for t in tasks2]
            self.assertEqual(task_types2, ["choices"])
            # assistant メッセージになっても tkey は choices_orig_id (u1) で作られるため二重発火しない
            self.assertEqual(tasks2[0][3], f"{sid}:choices:normal:u1")
        finally:
            server.load = orig_load
            server.list_ids = orig_list_ids

if __name__ == "__main__":
    unittest.main()
