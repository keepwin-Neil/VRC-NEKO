"""Remote-player speech admission uses existing host dispatch without fake logs."""
import importlib
import json
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import _bootstrap  # noqa: F401
from test_plugin_config import _plugin_class


class PlayerSpeechPluginTests(unittest.TestCase):
    identity = dict(session=31, slot=2, pid=90210, request_seq=1)

    def make_plugin(self, *, recognize=None, capture_start=None):
        plugin = _plugin_class()(None)
        module = sys.modules[type(plugin).__module__]
        speech_module = importlib.import_module(module.__name__ + '.runtime.player_speech')
        plugin._config = module.YuiPluginConfig.from_mapping({'speech_input': {'enabled': True}})
        plugin._session = SimpleNamespace(session=31, players={2: {'pid':90210}, 0: {'pid':1}},
            driver_pid=1, discovery_ready=True, estop=False, control_state='external')
        plugin._transport = SimpleNamespace()
        plugin._last_world_event_at = time.monotonic()
        plugin._current_host_character = lambda: 'YUI'
        plugin._model_context_payload = lambda: {'instructions':['test-only context']}
        pushes, captures, queued = [], [], []
        queued_signal = threading.Event()
        speech_submission_signal = threading.Event()
        note = Mock()
        engage = Mock()
        plugin._autonomy = SimpleNamespace(note_player_message=note, begin_chat_engagement=engage)
        plugin.push_message = lambda **kwargs: pushes.append(kwargs) or {'submitted':True}
        original_enqueue = plugin._enqueue_player_chat_submit
        def enqueue(value):
            queued.append(value)
            queued_signal.set()
            if isinstance(value, module._PlayerSpeechSubmission):
                speech_submission_signal.set()
            return original_enqueue(value)
        plugin._enqueue_player_chat_submit = enqueue

        class Capture:
            def __init__(self, turn, cancelled, **kwargs):
                self.turn, self.cancelled, self.closed = turn, cancelled, threading.Event()
                self.close_count = 0
                captures.append(self)
            def start(self):
                if capture_start:
                    capture_start()
            def finish(self):
                return speech_module.SpeechAudio(b'\x00\x20'*1600)
            def close(self):
                self.close_count += 1
                self.closed.set()

        recognizer = Mock(side_effect=recognize or (lambda *args: '请走过来'))
        with patch.object(module, 'WindowsProcessCapture', Capture), patch.object(module, 'WindowsOfflineRecognizer', return_value=recognizer):
            # Keep the factory patch active for deferred construction as well.
            plugin._configure_player_speech()
        capture_patch = patch.object(module, 'WindowsProcessCapture', Capture)
        capture_patch.start()
        self.addCleanup(capture_patch.stop)
        def close():
            plugin._speech.close()
            worker = plugin._chat_submit_worker
            if worker is not None and worker.is_alive():
                plugin._chat_submit_queue.put(None)
                worker.join(2)
        self.addCleanup(close)
        return SimpleNamespace(plugin=plugin, module=module, speech_module=speech_module,
            pushes=pushes, captures=captures, recognizer=recognizer, note=note, engage=engage,
            queued=queued, queued_signal=queued_signal, speech_submission_signal=speech_submission_signal)

    def event(self, state, **overrides):
        return dict(type='player.speech_input', state=state, **(self.identity | overrides))

    def wait_for(self, predicate):
        deadline = time.perf_counter() + 2
        while not predicate():
            if time.perf_counter() >= deadline:
                self.fail('Speech integration worker did not reach expected state')
            time.sleep(.005)

    def finish_and_drain(self, fixture):
        fixture.plugin._on_session_event(self.event('finish'))
        self.wait_for(lambda: fixture.plugin._speech.snapshot()['state'] == 'submitted')
        fixture.plugin._chat_submit_queue.join()

    def test_configuration_does_not_record_until_an_explicit_remote_request(self):
        fixture = self.make_plugin()
        self.assertEqual(fixture.captures, [])
        self.assertEqual(fixture.recognizer.call_count, 0)
        self.assertEqual(fixture.pushes, [])
        self.assertEqual(fixture.plugin._speech.snapshot()['state'], 'idle')

    def test_remote_speech_dispatches_once_with_identity_source_and_no_world_log_forgery(self):
        fixture = self.make_plugin()
        plugin = fixture.plugin
        plugin._handle_player_chat_submit = Mock(side_effect=AssertionError('Speech must not masquerade as a world chat event'))
        freshness = plugin._last_world_event_at
        plugin._on_session_event(self.event('start'))
        plugin._on_session_event(self.event('start'))
        self.finish_and_drain(fixture)
        plugin._on_session_event(self.event('finish'))
        plugin._on_session_event(self.event('start'))
        self.assertEqual(len(fixture.captures), 1)
        self.assertEqual(fixture.recognizer.call_count, 1)
        self.assertEqual(len(fixture.pushes), 1)
        fixture.note.assert_called_once_with(2, '请走过来')
        fixture.engage.assert_called_once_with(2, reply_baseline_serial=0)
        pushed = fixture.pushes[0]
        self.assertEqual(pushed['ai_behavior'], 'respond')
        self.assertEqual(pushed['metadata']['input_source'], 'microphone')
        self.assertEqual(pushed['metadata']['event_type'], 'player.speech_submit')
        request = json.loads(pushed['parts'][0]['text'].removeprefix('YUI_WORLD_CHAT_REQUEST '))
        self.assertEqual((request['request_type'], request['input_source'], request['player_slot']),
                         ('player.speech_submit', 'microphone', 2))
        self.assertTrue(all(isinstance(value, (fixture.module._PlayerSpeechSubmission, fixture.module._PlayerSpeechActivity)) for value in fixture.queued))
        self.assertEqual(sum(isinstance(value, fixture.module._PlayerSpeechSubmission) for value in fixture.queued), 1)
        plugin._handle_player_chat_submit.assert_not_called()
        self.assertEqual(plugin._last_world_event_at, freshness)
        self.assertNotIn('请走过来', json.dumps(plugin._diagnostics.snapshot(), ensure_ascii=False))

    def test_wrong_session_slot_pid_driver_disconnect_and_expired_world_fail_closed(self):
        cases = (
            ('session', {'session':32}, lambda p:None),
            ('slot', {'slot':3}, lambda p:None),
            ('pid', {'pid':7}, lambda p:None),
            ('driver', {'slot':0, 'pid':1}, lambda p:None),
            ('disconnect', {}, lambda p:setattr(p, '_manual_disconnect', True)),
            ('transport', {}, lambda p:setattr(p, '_transport', None)),
            ('expired', {}, lambda p:setattr(p, '_last_world_event_at', time.monotonic()-11)),
            ('unknown_freshness', {}, lambda p:setattr(p, '_last_world_event_at', None)),
            ('estop', {}, lambda p:setattr(p._session, 'estop', True)),
        )
        for label, fields, change in cases:
            with self.subTest(case=label):
                fixture = self.make_plugin()
                change(fixture.plugin)
                fixture.plugin._on_session_event(self.event('start', **fields))
                self.assertEqual(fixture.captures, [])
                turn = fixture.speech_module.SpeechTurn(**(self.identity | fields))
                fixture.plugin._enqueue_player_speech_submit(turn, '不会提交')
                fixture.plugin._chat_submit_queue.join()
                self.assertEqual(fixture.pushes, [])
                fixture.note.assert_not_called()
                fixture.plugin._speech.close()

    def test_cancelled_recognition_cannot_enqueue_a_late_request(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        def recognize(audio, turn, cancelled):
            entered.set()
            release.wait(2)
            returned.set()
            return '迟到的话不能发送'
        fixture = self.make_plugin(recognize=recognize)
        try:
            fixture.plugin._on_session_event(self.event('start'))
            fixture.plugin._on_session_event(self.event('finish'))
            self.assertTrue(entered.wait(1))
            fixture.plugin._on_session_event(self.event('cancel'))
            release.set()
            self.assertTrue(returned.wait(1))
            self.assertFalse(fixture.speech_submission_signal.wait(.1))
            self.assertFalse(any(isinstance(value, fixture.module._PlayerSpeechSubmission) for value in fixture.queued))
            self.assertEqual(fixture.pushes, [])
            fixture.note.assert_not_called()
        finally:
            release.set()

    def test_speech_open_and_cancel_hold_attention_without_requesting_a_reply(self):
        fixture = self.make_plugin()
        activity = Mock()
        fixture.plugin._autonomy.note_chat_input = activity
        fixture.plugin._on_session_event(self.event('start', request_seq=5))
        fixture.plugin._chat_submit_queue.join()
        activity.assert_called_once_with(dict(session=31, slot=2, pid=90210,
            input_seq=13, state='open', idle_ms=0), source='microphone')
        self.assertEqual(fixture.pushes, [])
        self.assertEqual(fixture.recognizer.call_count, 0)
        fixture.note.assert_not_called()
        fixture.plugin._on_session_event(self.event('cancel', request_seq=5))
        fixture.plugin._chat_submit_queue.join()
        self.assertEqual(activity.call_count, 2)
        self.assertEqual(activity.call_args.kwargs, {'source':'microphone'})
        self.assertEqual(activity.call_args.args[0]['state'], 'closed')
        self.assertEqual(activity.call_args.args[0]['input_seq'], 15)
        self.assertEqual(fixture.pushes, [])

    def test_queued_speech_is_revalidated_after_session_changes(self):
        fixture = self.make_plugin()
        entered, release = threading.Event(), threading.Event()
        fixture.plugin._handle_player_chat_submit = lambda event:(entered.set(), release.wait(2))
        try:
            fixture.plugin._enqueue_player_chat_submit({'type':'test_blocker'})
            self.assertTrue(entered.wait(1))
            turn = fixture.speech_module.SpeechTurn(**self.identity)
            fixture.plugin._enqueue_player_speech_submit(turn, '旧会话的话')
            fixture.plugin._session.session = 32
            release.set()
            fixture.plugin._chat_submit_queue.join()
            self.assertEqual(fixture.pushes, [])
            fixture.note.assert_not_called()
        finally:
            release.set()

    def test_world_text_and_microphone_have_separate_sequences_but_share_cooldown(self):
        fixture = self.make_plugin()
        plugin = fixture.plugin
        event = dict(session=31, slot=2, pid=90210, submit_seq=1, text='文字请求')
        with patch.object(fixture.module.time, 'monotonic', return_value=100) as clock:
            plugin._last_world_event_at = 100
            plugin._handle_player_chat_submit(event)
            self.assertEqual(len(fixture.pushes), 1)
            clock.return_value = 100.1
            turn = fixture.speech_module.SpeechTurn(**(self.identity | {'request_seq':2}))
            plugin._enqueue_player_speech_submit(turn, '冷却中的语音')
            plugin._chat_submit_queue.join()
            self.assertEqual(plugin._player_chat_status['last_error'], 'rate_limited')
            self.assertEqual(len(fixture.pushes), 1)
            clock.return_value = 102.1
            turn = fixture.speech_module.SpeechTurn(**self.identity)
            plugin._enqueue_player_speech_submit(turn, '语音请求')
            plugin._chat_submit_queue.join()
            self.assertEqual(len(fixture.pushes), 2)
            self.assertEqual(fixture.pushes[-1]['metadata']['input_source'], 'microphone')
            clock.return_value = 104.2
            plugin._enqueue_player_speech_submit(turn, '同序号重放')
            plugin._chat_submit_queue.join()
            self.assertEqual(len(fixture.pushes), 2)
            self.assertEqual(plugin._player_chat_status['last_error'], 'duplicate')
            plugin._handle_player_chat_submit(event | {'submit_seq':2, 'text':'新文字请求'})
            self.assertEqual(len(fixture.pushes), 3)
            self.assertEqual(fixture.pushes[-1]['metadata']['input_source'], 'world_chat')

    def test_audio_and_dispatch_workers_do_not_block_log_event_thread(self):
        capture_entered, capture_release = threading.Event(), threading.Event()
        def slow_capture():
            capture_entered.set()
            capture_release.wait(2)
        fixture = self.make_plugin(capture_start=slow_capture)
        try:
            began = time.perf_counter()
            fixture.plugin._on_session_event(self.event('start'))
            self.assertTrue(capture_entered.wait(1))
            fixture.plugin._on_session_event(self.event('finish'))
            self.assertLess(time.perf_counter()-began, .2)
            self.assertEqual(fixture.pushes, [])
            capture_release.set()
            self.wait_for(lambda: fixture.plugin._speech.snapshot()['state'] == 'submitted')
            fixture.plugin._chat_submit_queue.join()
        finally:
            capture_release.set()
        entered, release = threading.Event(), threading.Event()
        fixture.plugin._handle_player_request = lambda event, source:(entered.set(), release.wait(2))
        try:
            turn = fixture.speech_module.SpeechTurn(**(self.identity | {'request_seq':2}))
            began = time.perf_counter()
            self.assertTrue(fixture.plugin._enqueue_player_speech_submit(turn, '异步提交'))
            self.assertLess(time.perf_counter()-began, .2)
            self.assertTrue(entered.wait(1))
        finally:
            release.set()
            fixture.plugin._chat_submit_queue.join()


if __name__ == '__main__':
    unittest.main()
