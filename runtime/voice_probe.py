"""Bounded public HLS checks; a registered tunnel alone is not playable audio."""
from __future__ import annotations

import math
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_public_hls(url, *, opener=None, timeout=2, stop_event=None):
    """Return None only for a playable-shaped playlist and one complete TS file.

    This does not assert native decoder compatibility. Requests have socket
    timeouts, bounded bodies, no redirects, and cancellation between reads.
    No audio is ingested and no decoder/native process is started.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return 'invalid_public_url'
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + timeout * 2

    def read(path, maximum):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        request = urllib.request.Request(path, headers={'Cache-Control': 'no-cache', 'User-Agent': 'YUI-Voice-Health/1'})
        with opener.open(request, timeout=min(timeout, remaining)) as response:
            if response.status != 200:
                raise urllib.error.HTTPError(path, response.status, '', response.headers, None)
            chunks = bytearray()
            reader = getattr(response, 'read1', response.read)
            while len(chunks) <= maximum:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError()
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                data = reader(min(4096, maximum + 1 - len(chunks)))
                if not data:
                    break
                chunks.extend(data)
            return bytes(chunks)

    try:
        data = read(url, 16384)
        if len(data) > 16384:
            return 'playlist_too_large'
        try:
            lines = data.decode('utf-8-sig').splitlines()
        except UnicodeError:
            return 'playlist_invalid'
        if not lines or lines[0].strip() != '#EXTM3U':
            return 'playlist_invalid'
        targets = [line.partition(':')[2] for line in lines if line.startswith('#EXT-X-TARGETDURATION:')]
        if len(targets) != 1 or not targets[0].isdigit() or int(targets[0]) < 1:
            return 'playlist_invalid'
        target = int(targets[0])
        segments = []
        duration = None
        for line in lines[1:]:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                try:
                    duration = float(line.partition(':')[2].partition(',')[0])
                except ValueError:
                    return 'playlist_invalid'
                if not math.isfinite(duration) or not 0 < duration <= target + .5:
                    return 'playlist_invalid'
            elif line and not line.startswith('#'):
                if duration is None or not re.fullmatch(r'segment_[0-9]{9}\.ts', line):
                    return 'playlist_invalid'
                segments.append(line)
                duration = None
        if not segments or duration is not None:
            return 'playlist_invalid'
        # The newest complete segment is less likely to expire while probing.
        segment = read(urllib.parse.urljoin(url, segments[-1]), 262144)
        if not segment or len(segment) > 262144 or len(segment) % 188 or any(segment[i] != 0x47 for i in range(0, len(segment), 188)):
            return 'segment_invalid'
        return None
    except urllib.error.HTTPError as error:
        return 'http_' + str(error.code)
    except InterruptedError:
        return 'cancelled'
    except (TimeoutError, socket.timeout):
        return 'timeout'
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return 'timeout'
        if isinstance(error.reason, socket.gaierror):
            return 'dns_error'
        if isinstance(error.reason, ssl.SSLError):
            return 'tls_error'
        return 'network_error'
    except (OSError, ValueError):
        return 'network_error'
