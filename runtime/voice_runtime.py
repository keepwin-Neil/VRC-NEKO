"""Voice lifecycle and world authorization, independent of the host/UI framework."""
from .voice_gateway import VoiceGateway


class VoiceRuntime:
    def __init__(self, factory=VoiceGateway):
        self.factory=factory
        self.gateway=None
        self.enabled=False
        self.error=None
        self.phase='disabled'

    def configure(self,config):
        self.close()
        self.enabled=config.enabled; self.error=None; self.phase='starting' if config.enabled else 'disabled'
        if not self.enabled:return
        gateway=None
        try:
            gateway=self.factory(config)
            gateway.start(); self.gateway=gateway; self.phase='streaming'
        except Exception as exc:
            if gateway is not None:
                try:gateway.close()
                except Exception:pass
            self.error=type(exc).__name__+': '+str(exc)
            self.phase='failed'

    def refresh(self,session,*,fresh,manual_disconnect):
        if self.gateway is None:return
        self.gateway.set_armed(bool(session is not None and fresh and not manual_disconnect
            and session.discovery_ready and not session.estop
            and 'voice_stream' in session.capabilities
            and session.control_state in {'external','moving','action'}
            and session.voice_state.get('state')=='playing'))

    def snapshot(self):
        return self.gateway.snapshot() if self.gateway is not None else {
            'enabled':self.enabled,'state':self.phase,'error':self.error}

    def close(self):
        gateway=self.gateway; self.gateway=None
        if gateway is not None:
            try:gateway.close()
            except Exception as exc:
                self.error=type(exc).__name__+': voice cleanup failed';self.phase='failed';return
        self.phase='stopped' if self.enabled else 'disabled'
