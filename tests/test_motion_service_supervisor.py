"""推理子进程因一次超时被关闭后，服务应自动重载而不是永久停在 loading。"""
import threading

import _bootstrap  # noqa: F401
from integrations.motion_service.__main__ import supervise_generator


class Inner:
    frames, fps = 40, 20

    def __init__(self):
        self.ready = True
        self.closed = False

    def close(self):
        self.ready = False
        self.closed = True


class Holder:
    frames, fps, inner = 40, 20, None


class Service:
    def __init__(self):
        self.lock = threading.Lock()
        self.error = None


def test_supervisor_reloads_after_child_dies_and_retries_failed_loads():
    generator, service, stop = Holder(), Service(), threading.Event()
    loads = []
    plan = ["ok", "fail", "ok"]

    def load():
        step = plan[len(loads)] if len(loads) < len(plan) else "ok"
        loads.append(step)
        if step == "fail":
            raise RuntimeError("boom")
        return Inner()

    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        if len(waits) == 1:
            generator.inner.ready = False          # 推理超时 → 子进程被关闭
        if len(loads) >= 3 and generator.inner is not None and generator.inner.ready:
            stop.set()
        return stop.is_set()

    stop.wait = fake_wait
    first = []
    orig_load = load

    def tracking_load():
        inner = orig_load()
        first.append(inner)
        return inner

    supervise_generator(generator, service, tracking_load, stop, poll_s=0.01)
    assert loads == ["ok", "fail", "ok"]
    assert first[0].closed and generator.inner is first[1] and generator.inner.ready
    assert service.error is None
