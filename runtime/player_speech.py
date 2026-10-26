"""Explicit, identity-bound speech turns; importing this module never records.

Adapters implement start(), finish() -> SpeechAudio | None, and close(). close()
must interrupt capture, be safe alongside start/finish, and prevent later start.
capture_factory(turn, cancelled) and recognize(audio, turn, cancelled) run on a
worker. validate_identity(turn) and submit(turn, text) must be fast, nonblocking
callbacks; submit should enqueue work, not wait for model/world acknowledgments.
The identity check and submit admission share the cancellation lock: cancellation
before admission prevents submission; after admission it cannot retract a message.
No world log event is fabricated here.
"""
from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass, field
import math
import threading
import unicodedata


@dataclass(frozen=True, slots=True)
class SpeechTurn:
    session: int
    slot: int
    pid: int
    request_seq: int


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    """Little-endian signed PCM16, not a WAV/container/encoded payload."""
    pcm: bytes
    sample_rate: int = 16000
    channels: int = 1


@dataclass(slots=True)
class _Pending:
    turn: SpeechTurn
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    close_lock: threading.Lock = field(default_factory=threading.Lock)
    capture: object = None
    capture_closed: bool = False
    timer: threading.Timer | None = None


class SpeechTurnCoordinator:
    def __init__(self, capture_factory, recognize, submit, validate_identity, *,
                 enabled=False, timeout_s=15.0, max_chars=144, minimum_rms=.005):
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not .05 <= timeout_s <= 120:
            raise ValueError('timeout_s must be .05..120')
        if type(max_chars) is not int or not 1 <= max_chars <= 144:
            raise ValueError('max_chars must be 1..144')
        if type(minimum_rms) not in (int, float) or not math.isfinite(minimum_rms) or not 0 <= minimum_rms <= 1:
            raise ValueError('minimum_rms must be 0..1')
        self.capture_factory, self.recognize = capture_factory, recognize
        self.submit, self.validate_identity = submit, validate_identity
        self.enabled, self.timeout_s = enabled, timeout_s
        self.max_chars, self.minimum_rms = max_chars, minimum_rms
        self._lock = threading.RLock()
        self._active = None
        self._session = None
        self._seen = {}
        self._closed = False
        self._state = 'idle' if enabled else 'disabled'
        self._identity = None
        self._error = None

    def _valid_identity(self, turn):
        return turn.session == self._session and bool(self.validate_identity(turn))

    def start(self, session, slot, pid, request_seq):
        if any(type(value) is not int for value in (session, slot, pid, request_seq)):
            return False
        if session <= 0 or not 0 <= slot < 64 or pid <= 0 or request_seq <= 0:
            return False
        turn = SpeechTurn(session, slot, pid, request_seq)
        with self._lock:
            if self._closed or not self.enabled:
                return False
            try:
                if not self._valid_identity(turn):
                    return False
            except Exception:
                return False
            if self._active is not None:
                return self._active.turn == turn  # Identical retransmission only.
            previous_pid, previous = self._seen.get(slot, (pid, 0))
            if previous_pid != pid:
                previous = 0
            if request_seq <= previous:
                return request_seq == previous  # Completed/cancelled replay is a no-op.
            self._seen[slot] = (pid, request_seq)
            pending = _Pending(turn)
            self._active = pending
            self._identity = turn
            self._state, self._error = 'starting', None
            pending.timer = threading.Timer(self.timeout_s, self._expire, args=(pending,))
            pending.timer.daemon = True
            pending.timer.start()
            threading.Thread(target=self._run, args=(pending,), name='yui-player-speech', daemon=True).start()
            return True

    def finish(self, session, slot, pid, request_seq):
        with self._lock:
            pending = self._active
            if pending is None or pending.turn != SpeechTurn(session, slot, pid, request_seq):
                return False
            pending.finished.set()
            return True

    def cancel(self, session, slot, pid, request_seq, *, reason='cancelled'):
        with self._lock:
            pending = self._active
            if pending is None or pending.turn != SpeechTurn(session, slot, pid, request_seq):
                return False
            self._cancel_locked(pending, reason)
            return True

    def _cancel_locked(self, pending, reason):
        if self._active is not pending:
            return
        self._active = None
        self._state, self._error = 'cancelled', reason
        pending.cancelled.set()
        pending.finished.set()
        pending.timer.cancel()
        # Device teardown may block; it must never run on the log reader thread.
        threading.Thread(target=self._release, args=(pending,), name='yui-speech-close', daemon=True).start()

    @staticmethod
    def _release(pending):
        with pending.close_lock:
            if pending.capture is None or pending.capture_closed:
                return
            pending.capture_closed = True
            try:
                pending.capture.close()
            except Exception:
                pass

    def _expire(self, pending):
        with self._lock:
            self._cancel_locked(pending, 'timeout')

    def update_session(self, session):
        if session is not None and (type(session) is not int or session <= 0):
            raise ValueError('session must be positive or None')
        with self._lock:
            if session == self._session:
                return
            if self._active is not None:
                self._cancel_locked(self._active, 'session_changed')
            self._session = session
            self._seen.clear()

    def player_left(self, pid):
        with self._lock:
            if self._active is not None and self._active.turn.pid == pid:
                self._cancel_locked(self._active, 'player_left')

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        with self._lock:
            self.enabled = enabled and not self._closed
            if not self.enabled:
                if self._active is not None:
                    self._cancel_locked(self._active, 'disabled')
                self._state = 'disabled' if not self._closed else 'stopped'
            elif self._active is None:
                self._state, self._error = 'idle', None

    def close(self):
        with self._lock:
            self._closed = True
            self.set_enabled(False)

    def snapshot(self):
        with self._lock:
            return dict(enabled=self.enabled, state=self._state, error=self._error,
                        identity=asdict(self._identity) if self._identity is not None else None)

    def _audio_error(self, audio):
        if audio is None or (isinstance(audio, SpeechAudio) and not audio.pcm):
            return 'no_speech'
        if not isinstance(audio, SpeechAudio) or type(audio.pcm) is not bytes:
            return 'audio_invalid'
        if (type(audio.sample_rate) is not int or not 8000 <= audio.sample_rate <= 96000
                or type(audio.channels) is not int or not 1 <= audio.channels <= 2
                or len(audio.pcm) % (2 * audio.channels)
                or len(audio.pcm) > audio.sample_rate * audio.channels * 2 * self.timeout_s):
            return 'audio_invalid'
        samples = array('h', audio.pcm)
        if not samples or math.sqrt(sum(value*value for value in samples) / len(samples)) / 32768 <= self.minimum_rms:
            return 'no_speech'
        return None

    def _run(self, pending):
        error = None
        try:
            capture = self.capture_factory(pending.turn, pending.cancelled)
            with pending.close_lock:
                pending.capture = capture
            if pending.cancelled.is_set():
                return
            capture.start()
            with self._lock:
                if self._active is pending:
                    self._state = 'recording'
            pending.finished.wait()
            if pending.cancelled.is_set():
                return
            audio = capture.finish()
            self._release(pending)
            if pending.cancelled.is_set():
                return
            error = self._audio_error(audio)
            if error:
                return
            with self._lock:
                if self._active is not pending or not self._valid_identity(pending.turn):
                    error = 'identity_invalid'
                    return
                self._state = 'recognizing'
            error = 'recognition_failed'
            text = self.recognize(audio, pending.turn, pending.cancelled)
            if not isinstance(text, str) or any(unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in text):
                error = 'text_invalid'
                return
            text = text.strip()
            if not text:
                error = 'no_speech'
                return
            if len(text) > self.max_chars:
                error = 'text_too_long'
                return
            with self._lock:
                if self._active is not pending or pending.cancelled.is_set():
                    return
                if not self._valid_identity(pending.turn):
                    error = 'identity_invalid'
                    return
                # Nonblocking enqueue only. This is the atomic admission gate.
                error = 'submit_failed'
                if self.submit(pending.turn, text) is False:
                    return
                self._active = None
                pending.timer.cancel()
                self._state, self._error = 'submitted', None
        except Exception:
            error = error or 'capture_failed'
        finally:
            self._release(pending)
            with self._lock:
                if self._active is pending:
                    self._active = None
                    pending.timer.cancel()
                    self._state, self._error = 'rejected', error or 'capture_failed'
