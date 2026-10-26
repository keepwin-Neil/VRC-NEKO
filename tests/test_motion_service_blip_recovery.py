"""动作服务短暂不可用（推理子进程重载）导致的 stream_lease_lost 必须自动重新握手。"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import _bootstrap  # noqa: F401
from yui_npc_controller.runtime.motion_backend import MotionBackend, MotionBackendConfig


def _backend(error):
    now = [1.0]
    backend = MotionBackend(MotionBackendConfig(enabled=True), clock=lambda: now[0])
    backend._seen = 1
    backend._health = dict(ready=True, protocol='yui-motion/1', continuous_protocol='yui-motion-stream/2', instance='same')
    session = SimpleNamespace(world_id='world', session=7, estop=False, control_state='external',
                              capabilities=['pose_stream_v2', 'pose_link_recovery_v1'])
    transport = SimpleNamespace(motion_recovery_blocked=False, recover_motion_link=Mock(return_value=True))
    backend._continuous_environment = (transport, session, lambda: False)
    old = SimpleNamespace(status='failed', error=error, closed=SimpleNamespace(is_set=lambda: True),
                          sender=SimpleNamespace(core=SimpleNamespace(reason=None)), close=Mock())
    backend._continuous = old
    backend._continuous_instance = ('same', 'world', 7)
    return backend, transport, old


def test_lease_lost_and_prewarm_timeout_are_retried():
    for error in ('stream_lease_lost', 'stream_prewarm_timeout', 'authority_lost'):
        backend, transport, old = _backend(error)
        with patch('yui_npc_controller.runtime.continuous_execution.ContinuousExecution') as fresh:
            backend.refresh_continuous()
        assert transport.recover_motion_link.call_count == 1, error
        fresh.assert_called_once()
        assert backend._continuous is not old


def test_unrelated_failure_still_stays_terminal():
    backend, transport, old = _backend('invalid_stream_block')
    with patch('yui_npc_controller.runtime.continuous_execution.ContinuousExecution') as fresh:
        backend.refresh_continuous()
    transport.recover_motion_link.assert_not_called()
    fresh.assert_not_called()
    assert backend._continuous is old


def test_authority_loss_still_requires_fresh_probe_and_respects_stop_and_expiry():
    for blocked in ('probe_failed', 'estop', 'manual_stop', 'expired'):
        backend, transport, old = _backend('authority_lost')
        session = backend._continuous_environment[1]
        if blocked == 'probe_failed':
            transport.recover_motion_link.return_value = False
        elif blocked == 'estop':
            session.estop = True
        elif blocked == 'manual_stop':
            transport.motion_recovery_blocked = True
        else:
            backend._continuous_environment = (transport, session, lambda: True)
        with patch('yui_npc_controller.runtime.continuous_execution.ContinuousExecution') as fresh:
            backend.refresh_continuous()
            fresh.assert_not_called()
        assert backend._continuous is old
