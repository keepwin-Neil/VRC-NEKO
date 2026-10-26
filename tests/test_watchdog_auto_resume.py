"""看门狗暂停后链路恢复即自动恢复自主；其他暂停原因不受影响。"""
from types import SimpleNamespace
import unittest

import _bootstrap  # noqa: F401
import test_autonomy as fixtures


class WatchdogAutoResumeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutonomyTests()
        self.fixture.setUp()
        self.director = self.fixture.director
        self.clock = self.fixture.clock
        self.session = self.fixture.session
        # 不启动真实后台线程；start() 看到“线程存活”就只改状态。
        self.director._thread = SimpleNamespace(is_alive=lambda: True)

    def tearDown(self):
        self.fixture.tearDown()

    def trip_watchdog(self):
        self.session.emit({"type": "sys.watchdog"})
        self.assertEqual(self.director.status()["pause_reason"], "watchdog")
        self.assertFalse(self.director.status()["running"])

    def test_resumes_after_world_is_stably_back_under_external_control(self):
        self.trip_watchdog()
        self.session.control_state = "safe_idle"
        self.clock.advance(5)
        self.director._tick()
        self.assertFalse(self.director.status()["running"])
        self.session.control_state = "external"
        self.director._tick()                       # 开始计稳定时间
        self.clock.advance(1.0)
        self.director._tick()
        self.assertFalse(self.director.status()["running"])
        self.clock.advance(1.1)
        self.director._tick()
        self.assertTrue(self.director.status()["running"])
        self.assertFalse(self.director.status()["pause_reason"])

    def test_flapping_back_to_safe_idle_restarts_the_stability_window(self):
        self.trip_watchdog()
        self.session.control_state = "external"
        self.director._tick()
        self.clock.advance(1.5)
        self.session.control_state = "safe_idle"
        self.director._tick()
        self.session.control_state = "action"
        self.director._tick()
        self.clock.advance(1.5)
        self.director._tick()
        self.assertFalse(self.director.status()["running"])
        self.clock.advance(0.6)
        self.director._tick()
        self.assertTrue(self.director.status()["running"])

    def test_manual_pause_stop_and_estop_are_never_auto_resumed(self):
        for reason in ("manual_pause", "explicit_stop", "estop"):
            self.director.pause(reason)
            self.session.control_state = "external"
            for _ in range(4):
                self.director._tick()
                self.clock.advance(1.0)
            self.assertFalse(self.director.status()["running"], reason)
            self.assertEqual(self.director.status()["pause_reason"], reason)

    def test_estop_world_blocks_resume(self):
        self.trip_watchdog()
        self.session.control_state = "external"
        self.session.estop = True
        for _ in range(4):
            self.director._tick()
            self.clock.advance(1.0)
        self.assertFalse(self.director.status()["running"])


class WatchdogDoesNotBlockLogThreadTests(unittest.TestCase):
    def test_watchdog_event_returns_immediately_even_if_cancel_blocks(self):
        import threading
        import time
        fixture = fixtures.AutonomyTests()
        fixture.setUp()
        try:
            director = fixture.director
            release = threading.Event()
            cancelled = []

            def slow_cancel(origin, reason):
                release.wait(2.0)  # 模拟等待世界 ACK
                cancelled.append(reason)

            director.adapter.plan_manager.cancel_origin = slow_cancel
            started = time.monotonic()
            # FakeSession.emit 默认等待状态机完成；此例直接模拟真实尾读回调。
            director._on_session_event({"type": "sys.watchdog"})
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertFalse(director.status()["running"])
            self.assertEqual(director.status()["pause_reason"], "watchdog")
            release.set()
            deadline = time.monotonic() + 2
            while not cancelled and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(cancelled, ["watchdog"])
        finally:
            fixture.tearDown()
