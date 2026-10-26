"""Microphone attention leases reuse chat rules with an independent sequence."""
import unittest
from unittest.mock import Mock

import _bootstrap  # noqa: F401
import test_autonomy as fixtures


class SpeechEngagementTests(unittest.TestCase):
    def setUp(self):
        fixtures.AutonomyTests.setUp(self)
        self.session.players[2].update(pid=22)
        self.host_opening = Mock()
        self.director._opening_callback = self.host_opening

    tearDown = fixtures.AutonomyTests.tearDown

    def activity(self, sequence, phase='open', *, source='microphone', **extra):
        return self.director.note_chat_input(dict(session=9, slot=2, pid=22,
            input_seq=sequence, state=phase, idle_ms=0, **extra), source=source)

    def test_microphone_open_immediately_cancels_roaming_without_host_speech(self):
        self.clock.advance(1)
        self.director._tick()
        self.assertIsNotNone(self.director._active)
        roaming = self.director._active.plan_id
        self.assertTrue(self.activity(1))
        self.assertIn(('autonomy', 'chat_engagement', True), self.adapter.plan_manager.cancelled)
        self.assertEqual(self.adapter.plan_manager.statuses[roaming], 'cancelled')
        self.assertEqual(self.director._chat_engagement.phase, 'waiting_input')
        self.clock.advance(5)
        self.director._tick()
        self.assertEqual(self.director._chat_engagement.player_slot, 2)
        self.assertEqual(self.director._chat_engagement.phase, 'waiting_input')
        self.host_opening.assert_not_called()

    def test_text_and_microphone_sequences_are_independent(self):
        self.assertTrue(self.activity(1, source='microphone'))
        self.assertTrue(self.activity(1, 'active', source='world_chat'))
        self.assertFalse(self.activity(1, 'active', source='microphone'))
        self.assertFalse(self.activity(1, 'active', source='world_chat'))
        self.assertTrue(self.activity(2, 'active', source='microphone'))
        self.assertTrue(self.activity(2, 'active', source='world_chat'))
        self.assertEqual(self.director._input_sequences[(9, 2, 22, 'microphone')], 2)
        self.assertEqual(self.director._input_sequences[(9, 2, 22, 'world_chat')], 2)

    def test_microphone_closed_uses_existing_ten_second_release_grace(self):
        self.assertTrue(self.activity(1))
        self.clock.advance(2)
        self.assertTrue(self.activity(3, 'closed'))
        self.assertEqual(self.director._chat_engagement.phase, 'input_closed')
        self.clock.advance(9.9)
        self.director._tick()
        self.assertIsNotNone(self.director._chat_engagement)
        self.clock.advance(.2)
        self.director._tick()
        self.assertIsNone(self.director._chat_engagement)
        self.host_opening.assert_not_called()

    def test_invalid_source_rejected_and_event_json_does_not_choose_namespace(self):
        for source in ('unknown', '', None, False, {}, []):
            with self.subTest(source=repr(source)):
                self.assertFalse(self.activity(1, source=source))
        self.assertEqual(self.director._input_sequences, {})
        self.assertTrue(self.activity(1, source='microphone'))
        event = dict(session=9, slot=2, pid=22, input_seq=1, state='active', idle_ms=0,
                     source='microphone')
        self.assertTrue(self.director.note_chat_input(event))  # Default remains world text.
        self.assertFalse(self.director.note_chat_input(event | {'source':'world_chat'}))
        self.assertEqual(set(key[-1] for key in self.director._input_sequences), {'world_chat','microphone'})


if __name__ == '__main__':
    unittest.main()
