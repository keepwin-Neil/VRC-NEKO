"""姿态发送器切换时心跳不得整段缺失（世界看门狗 3 s）。"""
import threading
import time
from types import SimpleNamespace

import _bootstrap  # noqa: F401
from yui_npc_controller.runtime.yui_transport import YuiReliableTransport


def _transport(interval=0.05):
    session = SimpleNamespace(session=7, capabilities=[])
    transport = YuiReliableTransport(lambda event: None, session, heartbeat_interval_s=interval)
    beats = []
    transport.send_heartbeat = lambda: beats.append(time.monotonic())
    transport.set_heartbeat_enabled(True)
    return transport, beats


def test_restarted_heartbeat_sends_immediately_when_overdue():
    transport, beats = _transport(interval=0.5)
    transport._last_heartbeat_at = time.monotonic() - 5.0
    started = time.monotonic()
    transport.start_heartbeat()
    try:
        deadline = started + 0.3
        while not beats and time.monotonic() < deadline:
            time.sleep(0.005)
        assert beats and beats[0] - started < 0.2
    finally:
        transport.stop_heartbeat()


def test_heartbeat_keeps_running_while_detach_waits_for_inflight_command():
    transport, beats = _transport(interval=0.05)
    transport.start_heartbeat()
    holder_ready = threading.Event()
    release = threading.Event()

    def inflight_command():
        # 模拟 STOP/SNAPSHOT 在普通命令锁里等 ACK。
        with transport._normal_lock:
            holder_ready.set()
            release.wait(1.0)

    holder = threading.Thread(target=inflight_command)
    holder.start()
    holder_ready.wait(1.0)
    detacher = threading.Thread(target=transport.detach_pose_sender)
    detacher.start()
    time.sleep(0.05)
    before = len(beats)
    time.sleep(0.4)  # detach 正在排队等锁
    during = len(beats) - before
    release.set()
    holder.join(1.0)
    detacher.join(1.0)
    try:
        assert during >= 4, f"等锁期间心跳只有 {during} 次"
        assert transport._heartbeat_thread is not None and transport._heartbeat_thread.is_alive()
    finally:
        transport.stop_heartbeat()


def test_detach_restarts_heartbeat_even_if_sender_close_raises():
    transport, beats = _transport(interval=0.05)

    class Broken:
        def close(self, **kwargs):
            raise RuntimeError("底层 MIDI 写入仍阻塞")

    transport._shared_sender = Broken()
    transport.start_heartbeat()
    try:
        try:
            transport.detach_pose_sender()
        except RuntimeError:
            pass
        assert transport._shared_sender is None
        assert transport._heartbeat_thread is not None and transport._heartbeat_thread.is_alive()
    finally:
        transport.stop_heartbeat()
