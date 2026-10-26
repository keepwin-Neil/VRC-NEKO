"""Prepare minimal, reversible hooks in a separately backed-up N.E.K.O frontend."""
from pathlib import Path


def patch_playback(original: str):
    replacements = [
        ('        S.audioBufferQueue.push(bufferObj);',
         '        // YUI world voice: accepted TTS buffers only; never microphone/system audio.\n'
         '        try { if (window.nekoYuiVoice) bufferObj.yuiWorldOnly = window.nekoYuiVoice.enqueue(bufferObj) && !window.nekoYuiVoice.monitorLocal; } catch (_) {}\n'
         '        S.audioBufferQueue.push(bufferObj);'),
        ('                    if (hasAnalyser) {\n                        playbackTailNode.connect(S.globalAnalyser);',
         '                    if (item.yuiWorldOnly) {\n'
         '                        var worldOnlyMute = S.audioPlayerContext.createGain();\n'
         '                        worldOnlyMute.gain.value = 0;\n'
         '                        source._nekoYuiWorldMute = worldOnlyMute;\n'
         '                        playbackTailNode.connect(worldOnlyMute);\n'
         '                        worldOnlyMute.connect(S.audioPlayerContext.destination);\n'
         '                    } else if (hasAnalyser) {\n                        playbackTailNode.connect(S.globalAnalyser);'),
        ('        source._nekoPlaybackLimiterNode = null;',
         '        source._nekoPlaybackLimiterNode = null;\n'
         '        try { source._nekoYuiWorldMute?.disconnect(); } catch (_) {}\n'
         '        source._nekoYuiWorldMute = null;'),
        ('    async function clearAudioQueue() {',
         '    async function clearAudioQueue() {\n        if (window.nekoYuiVoice) window.nekoYuiVoice.reset();'),
        ('    function clearAudioQueueWithoutDecoderReset() {',
         '    function clearAudioQueueWithoutDecoderReset() {\n        if (window.nekoYuiVoice) window.nekoYuiVoice.reset();'),
    ]
    for before, after in replacements:
        if original.count(before) != 1:
            raise ValueError('Unsupported/previously patched frontend anchor: ' + before[:80])
        original = original.replace(before,after)
    return original


def patch_template(original: str):
    lines = original.splitlines(True)
    hits = [i for i, line in enumerate(lines) if '<script' in line and '/static/app/app-audio-playback.js' in line]
    if len(hits) != 1 or '/static/yui-voice-relay.js' in original:
        raise ValueError('Unsupported/previously patched frontend template')
    lines.insert(hits[0], '    <script src="/static/yui-voice-config.js"></script>\n'
        '    <script src="/static/yui-voice-relay.js?v=20261024"></script>\n')
    return ''.join(lines)
