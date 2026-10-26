"""日志尾读线程不能被玩家聊天处理阻塞（处理中会等世界 ACK，而 ACK 只能由尾读线程送达）。"""
import threading
import time
import unittest

import _bootstrap  # noqa: F401
from test_plugin_config import _plugin_class


class ChatSubmitWorkerTests(unittest.TestCase):
    def test_log_thread_returns_immediately_and_submissions_stay_ordered(self):
        plugin = _plugin_class()(None)
        plugin._event_loop = None
        handled = []
        release = threading.Event()

        def slow_handler(event):
            release.wait(2.0)  # 模拟 cancel_origin / 清注视等待 ACK
            handled.append(event["submit_seq"])

        plugin._handle_player_chat_submit = slow_handler
        started = time.monotonic()
        for seq in (1, 2, 3):
            plugin._on_session_event({"type": "player.chat_submit", "session": 1, "slot": 0,
                                      "pid": 1, "submit_seq": seq, "text": "hi"})
        self.assertLess(time.monotonic() - started, 0.2)  # 尾读线程没有被卡住
        self.assertEqual(handled, [])
        release.set()
        plugin._chat_submit_queue.join()
        self.assertEqual(handled, [1, 2, 3])

    def test_handler_exception_does_not_kill_worker(self):
        plugin = _plugin_class()(None)
        plugin._event_loop = None
        calls = []

        def flaky(event):
            calls.append(event["submit_seq"])
            if event["submit_seq"] == 1:
                raise RuntimeError("boom")

        plugin._handle_player_chat_submit = flaky
        for seq in (1, 2):
            plugin._on_session_event({"type": "player.chat_submit", "session": 1, "slot": 0,
                                      "pid": 1, "submit_seq": seq, "text": "hi"})
        plugin._chat_submit_queue.join()
        self.assertEqual(calls, [1, 2])
        self.assertIn("chat.rejected", [e["event"] for e in plugin._diagnostics.snapshot()])
