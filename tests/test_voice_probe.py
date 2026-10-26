import io
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yui_npc_controller.runtime.voice_config import VoiceGatewayConfig
from yui_npc_controller.runtime.voice_gateway import VoiceGateway
from yui_npc_controller.runtime.voice_probe import probe_public_hls


URL = 'https://example.trycloudflare.com/yui/live.m3u8'
PLAYLIST = b'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\nsegment_000000001.ts\n'
TS = (b'\x47' + bytes(187)) * 3


class Response(io.BytesIO):
    status = 200
    headers = {}


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return Response(item)


class PublicHlsProbeTests(unittest.TestCase):
    def test_requires_both_valid_playlist_and_complete_segment(self):
        opener = Opener(PLAYLIST, TS)
        self.assertIsNone(probe_public_hls(URL, opener=opener))
        self.assertEqual([x[0] for x in opener.requests], [URL, 'https://example.trycloudflare.com/yui/segment_000000001.ts'])
        self.assertTrue(all(0 < x[1] <= 2 for x in opener.requests))

    def test_530_is_reported_without_leaking_body_or_inventing_subcode(self):
        error = urllib.error.HTTPError(URL, 530, 'private body', {}, None)
        self.assertEqual(probe_public_hls(URL, opener=Opener(error)), 'http_530')
        self.assertEqual(probe_public_hls(URL, opener=Opener(PLAYLIST, error)), 'http_530')

    def test_invalid_html_empty_live_playlist_and_external_segment_are_rejected(self):
        for data in (b'<html>error</html>', b'#EXTM3U\n#EXT-X-TARGETDURATION:1\n',
                     PLAYLIST.replace(b':1\n', b':0\n'),
                     PLAYLIST.replace(b'segment_000000001.ts', b'https://attacker.example/seg.ts'),
                     PLAYLIST.replace(b':1.0,', b':nan,')):
            with self.subTest(data=data):
                opener = Opener(data)
                self.assertEqual(probe_public_hls(URL, opener=opener), 'playlist_invalid')
                self.assertEqual(len(opener.requests), 1)

    def test_body_limits_and_transport_packet_validation(self):
        self.assertEqual(probe_public_hls(URL, opener=Opener(b'x' * 16385)), 'playlist_too_large')
        for body in (b'', TS[:-1], b'x' * 188, TS * 500):
            with self.subTest(size=len(body)):
                self.assertEqual(probe_public_hls(URL, opener=Opener(PLAYLIST, body)), 'segment_invalid')

    def test_timeout_dns_and_cancel_have_bounded_diagnostic_codes(self):
        self.assertEqual(probe_public_hls(URL, opener=Opener(TimeoutError())), 'timeout')
        self.assertEqual(probe_public_hls(URL, opener=Opener(urllib.error.URLError(socket.gaierror()))), 'dns_error')
        stop = threading.Event(); stop.set()
        opener = Opener()
        self.assertEqual(probe_public_hls(URL, opener=opener, stop_event=stop), 'cancelled')
        self.assertFalse(opener.requests)


class PublicReadinessTests(unittest.TestCase):
    def wait_for(self, condition):
        deadline = time.monotonic() + 2
        while not condition():
            if time.monotonic() >= deadline:
                self.fail('public probe worker did not reach the expected state')
            time.sleep(.005)

    def test_registration_log_does_not_publish_an_unchecked_address(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = VoiceGateway(VoiceGatewayConfig(cloudflared_path=__file__), token='f' * 64)
            gateway.hls_directory = Path(directory)
            drained = threading.Event(); finish = threading.Event()
            lines = iter([b'Visit https://example.trycloudflare.com\n', b'INF Registered tunnel connection connIndex=0\n'])
            class Pipe:
                def readline(self, maximum):
                    try:
                        return next(lines)
                    except StopIteration:
                        drained.set(); finish.wait(2); return b''
            child = SimpleNamespace(stdout=Pipe())
            gateway.owned = SimpleNamespace(start=lambda *a, **k: child)
            worker = threading.Thread(target=gateway._start_tunnel)
            worker.start()
            try:
                self.assertTrue(drained.wait(2))
                self.assertEqual(gateway.tunnel_state, 'probing')
                self.assertEqual(gateway._tunnel_url, URL)
                self.assertIsNone(gateway.public_url)
                self.assertIsNone(gateway.public_probe_checked_at)
            finally:
                finish.set(); worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(gateway.tunnel_state, 'failed')
            self.assertIsNone(gateway._tunnel_url)

    def test_success_loss_and_recovery_publish_only_checked_addresses(self):
        gateway = VoiceGateway(VoiceGatewayConfig(), token='f' * 64)
        gateway._tunnel_url = URL
        gateway.tunnel_state = 'probing'
        outcomes = iter([None, 'http_530', None])
        with patch('yui_npc_controller.runtime.voice_gateway.probe_public_hls', side_effect=lambda *a, **k: next(outcomes)):
            worker = threading.Thread(target=gateway._probe_tunnel)
            worker.start()
            try:
                self.assertIsNone(gateway.public_url)
                gateway._tunnel_probe_wakeup.set()
                self.wait_for(lambda: gateway.tunnel_state == 'ready')
                self.assertEqual(gateway.public_url, URL)
                self.assertIsNotNone(gateway.public_probe_checked_at)
                gateway._tunnel_probe_wakeup.set()
                self.wait_for(lambda: gateway.tunnel_state == 'unavailable')
                self.assertIsNone(gateway.public_url)
                self.assertEqual(gateway.public_probe_error, 'http_530')
                gateway._tunnel_probe_wakeup.set()
                self.wait_for(lambda: gateway.tunnel_state == 'ready')
                self.assertEqual(gateway.public_url, URL)
                self.assertIsNone(gateway.public_probe_error)
            finally:
                gateway.stop_event.set(); gateway._tunnel_probe_wakeup.set(); worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_late_success_cannot_republish_exited_tunnel(self):
        gateway = VoiceGateway(VoiceGatewayConfig(), token='f' * 64)
        gateway._tunnel_url = URL
        entered = threading.Event(); complete = threading.Event()
        def probe(*args, **kwargs):
            entered.set(); complete.wait(2); return None
        with patch('yui_npc_controller.runtime.voice_gateway.probe_public_hls', side_effect=probe):
            worker = threading.Thread(target=gateway._probe_tunnel)
            worker.start(); gateway._tunnel_probe_wakeup.set()
            try:
                self.assertTrue(entered.wait(2))
                with gateway._tunnel_lock:
                    gateway._tunnel_url = None; gateway.tunnel_state = 'failed'
                complete.set()
                gateway.stop_event.set(); gateway._tunnel_probe_wakeup.set(); worker.join(2)
                self.assertIsNone(gateway.public_url)
                self.assertEqual(gateway.tunnel_state, 'failed')
            finally:
                complete.set(); gateway.stop_event.set(); gateway._tunnel_probe_wakeup.set(); worker.join(2)
            self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
