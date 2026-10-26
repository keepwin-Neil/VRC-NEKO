"""两个 VRChat 客户端同时写日志时，跟随器不能在文件间切换时重放旧事件。"""
import json
import os
from pathlib import Path
import tempfile
import time

import _bootstrap  # noqa: F401
from yui_npc_controller.runtime.yui_log import YuiOutputLogTailer


class _Recorder:
    def __init__(self):
        self.lines = []

    def ingest_line(self, line):
        self.lines.append(json.loads(line.split(b"[NEKO]", 1)[1])["log_seq"])
        return {"ok": True}


def _neko(seq):
    return ("2026.09.27 02:00:00 Debug - [NEKO]" + json.dumps({"log_seq": seq}) + "\n").encode()


def _touch_newer(path, bump):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, time.time_ns() + bump))


def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_switching_between_live_logs_resumes_instead_of_replaying():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        neko = root / "output_log_2026-09-27_01-52-03.txt"
        other = root / "output_log_2026-09-27_01-48-19.txt"
        neko.write_bytes(_neko(1) + _neko(2))          # 启动前的旧会话：不应读到
        other.write_bytes(b"2026 Debug - Enabled Mouse Cursor\n")
        _touch_newer(neko, 0)
        recorder = _Recorder()
        tailer = YuiOutputLogTailer(recorder, log_directory=root, from_end=True, poll_interval_s=0.01)
        tailer.start()
        try:
            time.sleep(0.05)
            with neko.open("ab") as handle:
                handle.write(_neko(3))
            _touch_newer(neko, 10_000_000)
            _wait(lambda: 3 in recorder.lines)
            for round_ in range(3):
                # 另一个客户端写了一行，它变成“最新”；随后 NEKO 客户端再写。
                with other.open("ab") as handle:
                    handle.write(b"2026 Debug - Disabled Mouse Cursor\n")
                _touch_newer(other, 20_000_000 * (2 * round_ + 2))
                time.sleep(0.05)
                with neko.open("ab") as handle:
                    handle.write(_neko(4 + round_))
                _touch_newer(neko, 20_000_000 * (2 * round_ + 3))
                _wait(lambda: 4 + round_ in recorder.lines)
        finally:
            tailer.stop()
        assert recorder.lines == [3, 4, 5, 6]


def test_log_created_after_start_is_read_from_beginning():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        old = root / "output_log_2026-09-27_00-50-50.txt"
        old.write_bytes(_neko(99))
        recorder = _Recorder()
        tailer = YuiOutputLogTailer(recorder, log_directory=root, from_end=True, poll_interval_s=0.01)
        tailer.start()
        try:
            time.sleep(0.05)
            new = root / "output_log_2026-09-27_01-52-03.txt"
            new.write_bytes(_neko(1) + _neko(2))
            _touch_newer(new, 50_000_000)
            _wait(lambda: 2 in recorder.lines)
        finally:
            tailer.stop()
        assert recorder.lines == [1, 2]
