"""用同一尾读线程递送输入和 ACK，复现并防止回调自锁。"""
import threading

import pytest
from test_autonomy import FakeAdapter, FakeSession
from yui_npc_controller.runtime.autonomy import AutonomyDirector
from yui_npc_controller.runtime.config import YuiAutonomyConfig


def make_director():
    session = FakeSession()
    session.players[2]['pid'] = 12
    adapter = FakeAdapter()
    director = AutonomyDirector(adapter, session, YuiAutonomyConfig(enabled=True))
    director._desired_running = True
    return director, adapter, session


def drained(director):
    with director._event_condition:
        assert director._event_condition.wait_for(
            lambda: not director._session_events and not director._event_busy, timeout=2)


@pytest.mark.parametrize('kind', ['player.chat_activity', 'player.leave'])
def test_log_reader_delivers_ack_while_event_control_waits(kind):
    director, adapter, session = make_director()
    ack = threading.Event()
    command_started = threading.Event()
    completed = threading.Event()
    reader_done = threading.Event()

    def command(*args):
        command_started.set()
        if ack.wait(1):
            completed.set()

    if kind == 'player.leave':
        director.begin_chat_engagement(2)
        director._chat_engagement.look_owned = True
        adapter.clear_look_wire = command
        event = dict(type=kind, slot=2, session=9)
    else:
        adapter.plan_manager.cancel_origin = command
        event = dict(type=kind, session=9, slot=2, pid=12, input_seq=1, state='open')

    def log_reader():
        # 下一条日志就是该命令的回执；回调若同步等 ACK，会消耗整个超时预算。
        director._on_session_event(event)
        reader_done.set()
        if command_started.wait(1):
            ack.set()

    reader = threading.Thread(target=log_reader)
    try:
        reader.start()
        assert reader_done.wait(.5)
        assert completed.wait(1)
        drained(director)
    finally:
        ack.set()
        reader.join(2)
        director.close()


def test_estop_callback_does_not_wait_for_worker_state_lock():
    director, _, session = make_director()
    held, release, returned = threading.Event(), threading.Event(), threading.Event()

    def hold():
        with director._condition:
            held.set()
            release.wait(2)

    owner = threading.Thread(target=hold)
    owner.start()
    assert held.wait(1)
    reader = threading.Thread(target=lambda: (
        director._on_session_event(dict(type='npc.state', estop=True)), returned.set()))
    try:
        reader.start()
        assert returned.wait(.5)
        assert not director._desired_running and director._pause_reason == 'estop'
    finally:
        release.set()
        owner.join(2)
        reader.join(2)
        drained(director)
        director.close()


def test_queued_events_do_not_cross_world_session_or_survive_close():
    director, _, session = make_director()
    entered, release = threading.Event(), threading.Event()
    handled = []

    def handle(event):
        handled.append(event['slot'])
        entered.set()
        release.wait(2)

    director._handle_session_event = handle
    try:
        director._on_session_event(dict(type='player.leave', slot=1))
        assert entered.wait(1)
        director._on_session_event(dict(type='player.leave', slot=2))
        session.session = 10
        release.set()
        drained(director)
        assert handled == [1]
    finally:
        release.set()
        director.close()
    director._on_session_event(dict(type='player.leave', slot=3))
    assert handled == [1]


def test_event_overflow_is_bounded_and_pauses_instead_of_dropping_silently():
    director, _, _ = make_director()
    entered, release = threading.Event(), threading.Event()
    director._handle_session_event = lambda event: (entered.set(), release.wait(2))
    try:
        director._on_session_event(dict(type='player.leave', slot=1))
        assert entered.wait(1)
        for _ in range(140):
            director._on_session_event(dict(type='player.leave', slot=2))
        assert len(director._session_events) <= 128
        assert not director._desired_running
        release.set()
        drained(director)
        assert director.status()['pause_reason'] == 'session_event_overflow'
    finally:
        release.set()
        director.close()


def test_delayed_departure_cannot_end_chat_with_new_player_in_same_slot():
    director, _, session = make_director()
    try:
        director.begin_chat_engagement(2)
        director._on_session_event(dict(type='player.leave', slot=2, pid=99))
        drained(director)
        assert director._chat_engagement.player_pid == 12
    finally:
        director.close()
