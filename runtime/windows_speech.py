"""Owned process-only capture and file-only offline ASR; no ambient recording."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import wave

from .player_speech import SpeechAudio
from .voice_process import OwnedProcesses


def vrchat_process_id():
    """Select an unambiguous VRChat process, never the desktop audio device."""
    if not hasattr(ctypes, 'WinDLL'):
        raise RuntimeError('windows_required')
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError('process_list_unavailable')
    matches = []
    try:
        entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
        valid = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while valid:
            if entry.szExeFile.casefold() == 'vrchat.exe':
                matches.append(entry.th32ProcessID)
            valid = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    if len(matches) != 1:
        raise RuntimeError('vrchat_not_running' if not matches else 'vrchat_process_ambiguous')
    return matches[0]


class WindowsProcessCapture:
    def __init__(self, turn, cancelled, *, binary=None, seconds=12, pid_provider=vrchat_process_id,
                 require_name='VRChat'):
        self.cancelled = cancelled
        self.binary = Path(binary) if binary else Path(__file__).parents[1] / 'vendor/speech/windows_process_capture.exe'
        self.seconds, self.pid_provider, self.require_name = seconds, pid_provider, require_name
        self.owned = OwnedProcesses()
        self.lock = threading.RLock()
        self.process = None
        self.directory = None
        self.path = None
        self.closed = False

    def start(self):
        with self.lock:
            if self.closed or self.cancelled.is_set():
                raise RuntimeError('capture_cancelled')
            if not self.binary.is_file():
                raise RuntimeError('capture_helper_missing')
            pid = self.pid_provider()
            self.directory = tempfile.TemporaryDirectory(prefix='yui-speech-')
            self.path = Path(self.directory.name) / 'capture.wav'
            command = [str(self.binary), '--pid', str(pid), '--seconds', str(self.seconds), '--output', str(self.path)]
            if self.require_name:
                command.extend(['--require-name', self.require_name])
            self.process = self.owned.start(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            process = self.process
        ready = queue.Queue(maxsize=1)
        def first_line():
            try: ready.put(process.stdout.readline(2048))
            except (OSError, ValueError): ready.put(b'')
        threading.Thread(target=first_line, name='yui-speech-ready', daemon=True).start()
        deadline = time.monotonic() + 5
        while not self.cancelled.is_set():
            try:
                line = ready.get(timeout=.05)
                if line.strip() != b'ready':
                    raise RuntimeError('capture_start_failed')
                return
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise RuntimeError('capture_start_timeout')
        raise RuntimeError('capture_cancelled')

    def finish(self):
        with self.lock:
            if self.closed or self.cancelled.is_set() or self.process is None:
                return None
            process = self.process
        try:
            process.communicate(input=b'stop\n', timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise RuntimeError('capture_stop_failed') from None
        if self.cancelled.is_set() or process.returncode != 0:
            return None
        with self.lock:
            if self.closed or self.path is None or self.path.stat().st_size > self.seconds*32000+44:
                return None
            with wave.open(str(self.path), 'rb') as stream:
                if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
                    raise RuntimeError('capture_format_invalid')
                pcm = stream.readframes(stream.getnframes())
        return SpeechAudio(pcm)

    def close(self):
        with self.lock:
            self.closed = True
        self.owned.close()
        with self.lock:
            if self.directory is not None:
                self.directory.cleanup()
                self.directory = None
                self.path = None


class WindowsOfflineRecognizer:
    def __init__(self, *, binary=None, culture='zh-CN', minimum_confidence=.45, timeout_s=40):
        self.binary = Path(binary) if binary else Path(__file__).parents[1] / 'vendor/speech/windows_speech_recognizer.exe'
        self.culture, self.minimum_confidence, self.timeout_s = culture, minimum_confidence, timeout_s

    def __call__(self, audio, turn, cancelled):
        if cancelled.is_set():
            return ''
        if not self.binary.is_file():
            raise RuntimeError('recognizer_helper_missing')
        owner = OwnedProcesses()
        try:
            with tempfile.TemporaryDirectory(prefix='yui-asr-') as directory:
                path = Path(directory) / 'input.wav'
                with wave.open(str(path), 'wb') as stream:
                    stream.setnchannels(audio.channels); stream.setsampwidth(2)
                    stream.setframerate(audio.sample_rate); stream.writeframes(audio.pcm)
                process = owner.start([str(self.binary), '--input', str(path), '--culture', self.culture],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + self.timeout_s
                while True:
                    try:
                        output, _ = process.communicate(timeout=.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if cancelled.is_set():
                        owner.close()
                        return ''
                    if time.monotonic() >= deadline:
                        owner.close()
                        raise RuntimeError('recognition_timeout')
                if process.returncode != 0 or len(output) > 8192:
                    raise RuntimeError('recognizer_failed')
                value = json.loads(output.decode('utf-8-sig'))
                confidence = value.get('confidence')
                if (value.get('error') or not isinstance(value.get('text'), str)
                        or type(confidence) not in (float, int) or not self.minimum_confidence <= confidence <= 1):
                    return ''
                return value['text']
        finally:
            owner.close()
