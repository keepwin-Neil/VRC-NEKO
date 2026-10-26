"""Opt-in remote-player speech settings; no capture during import/startup."""
from dataclasses import dataclass
import math
import re


@dataclass(frozen=True, slots=True)
class SpeechInputConfig:
    enabled: bool = False
    culture: str = 'zh-CN'
    max_capture_s: int = 12
    minimum_confidence: float = .45

    @classmethod
    def from_mapping(cls, source):
        data = dict(source or {})
        enabled = data.get('enabled', False)
        culture = data.get('culture', 'zh-CN')
        maximum = data.get('max_capture_s', 12)
        confidence = data.get('minimum_confidence', .45)
        if type(enabled) is not bool:
            raise ValueError('speech_input.enabled must be boolean')
        if not isinstance(culture, str) or not re.fullmatch(r'[a-z]{2,3}-[A-Z]{2}', culture):
            raise ValueError('speech_input.culture must be a locale such as zh-CN')
        if type(maximum) is not int or not 3 <= maximum <= 12:
            raise ValueError('speech_input.max_capture_s must be 3..12')
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not .2 <= confidence <= 1:
            raise ValueError('speech_input.minimum_confidence must be .2..1')
        return cls(enabled, culture, maximum, float(confidence))
