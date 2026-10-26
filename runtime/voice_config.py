"""Strict voice settings; importing configuration never starts networking."""
from __future__ import annotations
from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Any, Mapping

@dataclass(frozen=True, slots=True)
class VoiceGatewayConfig:
    enabled: bool = False
    ingest_port: int = 17863
    stream_port: int = 17864
    ffmpeg_path: str = ''
    state_path: str = ''
    character: str = 'YUI'
    max_queue_seconds: float = 3.0
    max_listeners: int = 8
    public_tunnel: bool = False
    cloudflared_path: str = ''

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any] | None):
        data = dict(source or {})
        defaults = cls()
        values = {}
        for key in ('ingest_port', 'stream_port', 'max_listeners'):
            value = data.get(key, getattr(defaults, key))
            minimum, maximum = (1, 32) if key == 'max_listeners' else (1024, 65535)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError('voice.' + key + ' is outside its allowed range')
            values[key] = value
        if values['ingest_port'] == values['stream_port']:
            raise ValueError('voice ingest and stream ports must differ')
        enabled = data.get('enabled', False)
        if type(enabled) is not bool:
            raise ValueError('voice.enabled must be boolean')
        duration = data.get('max_queue_seconds', defaults.max_queue_seconds)
        if type(duration) not in (int, float) or not math.isfinite(duration) or not .5 <= duration <= 8:
            raise ValueError('voice.max_queue_seconds must be .5..8')
        public_tunnel = data.get('public_tunnel', False)
        if type(public_tunnel) is not bool: raise ValueError('voice.public_tunnel must be boolean')
        for key in ('ffmpeg_path', 'state_path', 'character', 'cloudflared_path'):
            value = data.get(key, getattr(defaults, key))
            if not isinstance(value, str) or len(value) > 2048 or (key == 'character' and not value.strip()):
                raise ValueError('invalid voice.' + key)
            values[key] = value.strip()
        return cls(enabled=enabled, public_tunnel=public_tunnel, max_queue_seconds=float(duration), **values)

    def binary(self):
        return Path(self.ffmpeg_path) if self.ffmpeg_path else Path(__file__).parents[1] / 'vendor/voice/ffmpeg.exe'

    def state_file(self):
        root = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share')))
        return Path(self.state_path) if self.state_path else root / 'N.E.K.O/plugins/yui_npc_controller/voice/gateway.json'

    def tunnel_binary(self):
        return Path(self.cloudflared_path) if self.cloudflared_path else Path(__file__).parents[1] / 'vendor/voice/cloudflared.exe'

