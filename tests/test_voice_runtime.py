from types import SimpleNamespace
import unittest
from yui_npc_controller.runtime.voice_config import VoiceGatewayConfig
from yui_npc_controller.runtime.voice_runtime import VoiceRuntime

class FakeGateway:
    def __init__(self,config):self.armed=False;self.closed=0
    def start(self):pass
    def close(self):self.closed+=1
    def set_armed(self,value):self.armed=value
    def snapshot(self):return {'state':'streaming','armed':self.armed}

class VoiceRuntimeTests(unittest.TestCase):
    def session(self):
        return SimpleNamespace(discovery_ready=True,estop=False,capabilities={'voice_stream'},
            control_state='external',voice_state={'state':'playing'})
    def test_only_fresh_authorized_playing_world_can_receive_tts(self):
        runtime=VoiceRuntime(FakeGateway);runtime.configure(VoiceGatewayConfig(enabled=True))
        session=self.session();runtime.refresh(session,fresh=True,manual_disconnect=False)
        self.assertTrue(runtime.gateway.armed)
        for field,value in [('estop',True),('discovery_ready',False),('capabilities',set()),('control_state','safe_idle'),('voice_state',{'state':'paused'})]:
            previous=getattr(session,field);setattr(session,field,value)
            runtime.refresh(session,fresh=True,manual_disconnect=False)
            self.assertFalse(runtime.gateway.armed,field);setattr(session,field,previous)
        runtime.refresh(session,fresh=False,manual_disconnect=False);self.assertFalse(runtime.gateway.armed)
        runtime.refresh(session,fresh=True,manual_disconnect=True);self.assertFalse(runtime.gateway.armed)
        runtime.refresh(None,fresh=False,manual_disconnect=False);self.assertFalse(runtime.gateway.armed)
    def test_reconfiguration_and_shutdown_close_owned_gateway_once(self):
        runtime=VoiceRuntime(FakeGateway);runtime.configure(VoiceGatewayConfig(enabled=True))
        first=runtime.gateway;runtime.configure(VoiceGatewayConfig(enabled=True))
        self.assertEqual(first.closed,1)
        second=runtime.gateway;runtime.close();runtime.close();self.assertEqual(second.closed,1)
    def test_audio_failure_is_reported_without_throwing_into_body_control(self):
        class Failure(FakeGateway):
            def start(self):raise OSError('port occupied')
        runtime=VoiceRuntime(Failure);runtime.configure(VoiceGatewayConfig(enabled=True))
        self.assertIsNone(runtime.gateway);self.assertEqual(runtime.snapshot()['state'],'failed')
        self.assertIn('port occupied',runtime.snapshot()['error'])
    def test_disabled_default_does_not_construct_a_service(self):
        def forbidden(config):raise AssertionError('unexpected audio service')
        runtime=VoiceRuntime(forbidden);runtime.configure(VoiceGatewayConfig())
        self.assertEqual(runtime.snapshot()['state'],'disabled')

if __name__=='__main__':unittest.main()
