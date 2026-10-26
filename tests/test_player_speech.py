import threading
import time
import unittest

from yui_npc_controller.runtime.player_speech import SpeechAudio, SpeechTurnCoordinator


TURN = (17, 2, 9, 1)
AUDIO = SpeechAudio(b'\x00\x20' * 1600)


class Capture:
    def __init__(self, audio=AUDIO):
        self.audio = audio
        self.started = threading.Event()
        self.closed = threading.Event()
        self.close_count = 0

    def start(self):
        self.started.set()

    def finish(self):
        return self.audio

    def close(self):
        self.close_count += 1
        self.closed.set()


class PlayerSpeechTests(unittest.TestCase):
    def create(self, *, capture=None, recognize=None, submit=None, enabled=True, timeout_s=2):
        self.capture = capture or Capture()
        self.created = []
        self.submissions = []
        self.submitted = threading.Event()
        self.valid = True
        def factory(turn, cancelled):
            self.created.append(turn)
            return self.capture
        def enqueue(turn, text):
            self.submissions.append((turn, text))
            self.submitted.set()
        self.coordinator = SpeechTurnCoordinator(factory,
            recognize or (lambda audio, turn, cancelled: '过来'),
            submit or enqueue,
            lambda turn: self.valid and turn.slot == 2 and turn.pid == 9,
            enabled=enabled, timeout_s=timeout_s)
        self.coordinator.update_session(17)
        self.addCleanup(self.coordinator.close)
        return self.coordinator

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Speech worker did not reach expected state')
            time.sleep(.005)

    def test_default_disabled_and_invalid_identity_never_record(self):
        coordinator = SpeechTurnCoordinator(lambda *args: self.fail('Unexpected recording'),
            lambda *args: '', lambda *args: None, lambda turn: True)
        coordinator.update_session(17)
        self.assertFalse(coordinator.start(*TURN))
        coordinator.close()
        coordinator = self.create()
        for turn in ((18, 2, 9, 1), (17, 3, 9, 1), (17, 2, 10, 1), (17, 2, 9, 0), (True, 2, 9, 1)):
            self.assertFalse(coordinator.start(*turn))
        self.assertEqual(self.created, [])

    def test_valid_turn_is_single_flight_idempotent_and_snapshot_has_no_content(self):
        coordinator = self.create()
        self.assertTrue(coordinator.start(*TURN))
        self.assertTrue(self.capture.started.wait(1))
        self.assertTrue(coordinator.start(*TURN))
        self.assertFalse(coordinator.start(17, 2, 9, 2))
        self.assertFalse(coordinator.finish(17, 2, 9, 2))
        self.assertTrue(coordinator.finish(*TURN))
        self.wait_for(lambda: coordinator.snapshot()['state'] == 'submitted')
        self.assertEqual(self.submissions[0][1], '过来')
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.capture.close_count, 1)
        self.assertTrue(coordinator.start(*TURN))
        self.assertEqual(len(self.created), 1)
        snapshot = coordinator.snapshot()
        self.assertEqual(set(snapshot), {'enabled', 'state', 'error', 'identity'})
        self.assertNotIn('过来', str(snapshot))
        self.assertNotIn('pcm', str(snapshot))

    def test_other_valid_player_cannot_steal_active_turn(self):
        coordinator = self.create()
        coordinator.validate_identity = lambda turn: True
        self.assertTrue(coordinator.start(*TURN))
        self.assertFalse(coordinator.start(17, 3, 10, 1))
        self.assertEqual(coordinator.snapshot()['identity']['pid'], 9)

    def test_start_finish_and_cancel_do_not_block_log_reader_on_device_work(self):
        release_start, release_close = threading.Event(), threading.Event()
        capture = Capture()
        def start():
            capture.started.set()
            release_start.wait(2)
        def close():
            capture.close_count += 1
            capture.closed.set()
            release_close.wait(2)
        capture.start, capture.close = start, close
        coordinator = self.create(capture=capture)
        try:
            began = time.monotonic()
            self.assertTrue(coordinator.start(*TURN))
            self.assertTrue(capture.started.wait(1))
            self.assertTrue(coordinator.finish(*TURN))
            self.assertTrue(coordinator.cancel(*TURN))
            self.assertLess(time.monotonic() - began, .2)
            self.assertTrue(capture.closed.wait(1))
            self.assertEqual(self.submissions, [])
        finally:
            release_start.set()
            release_close.set()

    def test_cancellation_session_change_departure_disable_and_stop_reject_late_asr(self):
        actions = (lambda c: c.cancel(*TURN), lambda c: c.update_session(18),
                   lambda c: c.player_left(9), lambda c: c.set_enabled(False), lambda c: c.close())
        for action in actions:
            with self.subTest(action=action):
                entered, release, returned = threading.Event(), threading.Event(), threading.Event()
                def recognize(audio, turn, cancelled):
                    entered.set()
                    release.wait(2)
                    returned.set()
                    return '挥手'
                coordinator = self.create(recognize=recognize)
                try:
                    coordinator.start(*TURN)
                    coordinator.finish(*TURN)
                    self.assertTrue(entered.wait(1))
                    action(coordinator)
                    release.set()
                    self.assertTrue(returned.wait(1))
                    self.assertFalse(self.submitted.wait(.05))
                    self.assertEqual(self.submissions, [])
                    self.assertEqual(self.capture.close_count, 1)
                finally:
                    release.set()
                    coordinator.close()

    def test_timeout_closes_capture_and_late_finish_does_nothing(self):
        coordinator = self.create(timeout_s=.08)
        coordinator.start(*TURN)
        self.assertTrue(self.capture.closed.wait(1))
        self.assertEqual(coordinator.snapshot()['error'], 'timeout')
        self.assertFalse(coordinator.finish(*TURN))
        self.assertEqual(self.submissions, [])

    def test_timeout_during_recognition_rejects_late_result(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        def recognize(*args):
            entered.set()
            release.wait(2)
            returned.set()
            return '过来'
        coordinator = self.create(recognize=recognize, timeout_s=.1)
        try:
            coordinator.start(*TURN)
            coordinator.finish(*TURN)
            self.assertTrue(entered.wait(1))
            self.wait_for(lambda: coordinator.snapshot()['error'] == 'timeout')
            release.set()
            self.assertTrue(returned.wait(1))
            self.assertFalse(self.submitted.wait(.05))
            self.assertEqual(self.submissions, [])
        finally:
            release.set()

    def test_late_capture_factory_result_is_closed_without_starting(self):
        entered, release = threading.Event(), threading.Event()
        coordinator = self.create()
        def factory(turn, cancelled):
            entered.set()
            release.wait(2)
            return self.capture
        coordinator.capture_factory = factory
        try:
            coordinator.start(*TURN)
            self.assertTrue(entered.wait(1))
            coordinator.cancel(*TURN)
            release.set()
            self.assertTrue(self.capture.closed.wait(1))
            self.assertFalse(self.capture.started.is_set())
            self.assertEqual(self.capture.close_count, 1)
            self.assertEqual(self.submissions, [])
        finally:
            release.set()

    def test_identity_is_rechecked_after_recognition(self):
        def recognize(*args):
            self.valid = False
            return '过来'
        coordinator = self.create(recognize=recognize)
        coordinator.start(*TURN)
        coordinator.finish(*TURN)
        self.wait_for(lambda: coordinator.snapshot()['state'] == 'rejected')
        self.assertEqual(coordinator.snapshot()['error'], 'identity_invalid')
        self.assertEqual(self.submissions, [])

    def test_empty_silent_invalid_or_excessive_audio_never_reaches_asr(self):
        for audio in (None, SpeechAudio(b''), SpeechAudio(bytes(3200)), SpeechAudio(b'x'),
                      SpeechAudio(b'\x00\x20'*40000, sample_rate=8000), b'RIFF-not-PCM'):
            with self.subTest(audio=type(audio).__name__):
                coordinator = self.create(capture=Capture(audio), recognize=lambda *args: self.fail('Invalid audio recognized'))
                coordinator.start(*TURN)
                coordinator.finish(*TURN)
                self.wait_for(lambda: coordinator.snapshot()['state'] == 'rejected')
                self.assertIn(coordinator.snapshot()['error'], ('audio_invalid', 'no_speech'))
                self.assertEqual(self.submissions, [])
                coordinator.close()

    def test_text_controls_empty_and_oversize_are_rejected(self):
        for text in ('', '  ', '过来\n', '\t挥手', 'a\x00b', 'a\u202eb', 'a'*145):
            with self.subTest(text=repr(text)):
                coordinator = self.create(recognize=lambda *args: text)
                coordinator.start(*TURN)
                coordinator.finish(*TURN)
                self.wait_for(lambda: coordinator.snapshot()['state'] == 'rejected')
                self.assertEqual(self.submissions, [])
                coordinator.close()

    def test_submit_rejection_and_adapter_errors_are_sanitized(self):
        coordinator = self.create(submit=lambda *args: False)
        coordinator.start(*TURN)
        coordinator.finish(*TURN)
        self.wait_for(lambda: coordinator.snapshot()['state'] == 'rejected')
        self.assertEqual(coordinator.snapshot()['error'], 'submit_failed')
        def failure(*args):
            raise RuntimeError('private audio/transcript detail')
        coordinator = self.create(recognize=failure)
        coordinator.start(*TURN)
        coordinator.finish(*TURN)
        self.wait_for(lambda: coordinator.snapshot()['state'] == 'rejected')
        self.assertEqual(coordinator.snapshot()['error'], 'recognition_failed')
        self.assertNotIn('private', str(coordinator.snapshot()))


if __name__ == '__main__':
    unittest.main()
