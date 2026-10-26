"""TTS-only private ingest and public MPEG-TS playback, with owned process cleanup.

The playback listener exposes no ingest/configuration routes. No desktop or
microphone capture is performed. Configuration and bounded transport state are
kept in voice_config and voice_pcm; their names are re-exported for callers.
"""
from __future__ import annotations
from collections import deque
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import re
import secrets
import socket
import subprocess
import threading
import tempfile
import time
from .voice_config import VoiceGatewayConfig
from .voice_pcm import SAMPLE_RATE, FRAME_SAMPLES, FRAME_BYTES, PcmQueue, LiveSubscribers
from .voice_process import OwnedProcesses
from .voice_probe import probe_public_hls

DEFAULT_ORIGINS = ('http://127.0.0.1:48911', 'http://localhost:48911')

class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class VoiceGateway:
    def __init__(self, config: VoiceGatewayConfig, *, token=None):
        self.config = config
        self.pcm = PcmQueue(config.max_queue_seconds)
        self.listeners = LiveSubscribers(config.max_listeners)
        self.token = token or ''
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.servers = []
        self.threads = []
        self.encoder = None
        self.owned = OwnedProcesses()
        self.state = 'stopped'
        self.error = None
        self.encoded_bytes = 0
        self.stderr_tail = deque(maxlen=12)
        self.armed = False
        self.tunnel = None
        self.public_url = None
        self.tunnel_state = 'disabled'
        self.public_probe_error = None
        self.public_probe_checked_at = None
        self._tunnel_url = None
        self._tunnel_lock = threading.Lock()
        self._tunnel_probe_wakeup = threading.Event()
        self.hls_directory = None
        self._hls_temp = None
        self._hls_requests = threading.BoundedSemaphore(config.max_listeners)

    def set_armed(self, armed):
        self.armed = bool(armed)
        if not self.armed: self.pcm.clear()

    def _load_token(self):
        if self.token: return
        path = self.config.state_file()
        if path.exists():
            state = json.loads(path.read_text(encoding='utf-8'))
            token = state.get('ingest_token', '')
            if not re.fullmatch(r'[a-f0-9]{64}', token): raise ValueError('invalid private voice state')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            token = secrets.token_hex(32)
            with path.open('x', encoding='utf-8') as stream:
                json.dump({'ingest_token':token}, stream)
        self.token = token

    def _handler(self, private):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def _origin_ok(self):
                origin = self.headers.get('Origin')
                return not origin or origin in DEFAULT_ORIGINS
            def _cors(self):
                origin = self.headers.get('Origin')
                if origin in DEFAULT_ORIGINS:
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                    self.send_header('Access-Control-Allow-Private-Network', 'true')
            def _json(self, code, value):
                data = json.dumps(value,separators=(',',':')).encode()
                self.send_response(code); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store')
                self._cors(); self.end_headers(); self.wfile.write(data)
            def do_OPTIONS(self):
                if not private or not self._origin_ok(): return self._json(403, {'error':'origin'})
                self.send_response(204); self._cors()
                self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
                self.send_header('Access-Control-Allow-Headers','Content-Type,X-Yui-Token,X-Yui-Sender,X-Yui-Epoch,X-Yui-Sequence')
                self.send_header('Content-Length','0'); self.end_headers()
            def do_GET(self):
                if private:
                    if not self._origin_ok(): return self._json(403, {'error':'origin'})
                    if self.path == '/health': return self._json(200, owner.snapshot())
                    return self._json(404, {'error':'not_found'})
                if self.path == '/yui/ping':
                    self.send_response(204); self.send_header('Content-Length','0')
                    self.send_header('Cache-Control','no-store'); self.end_headers(); return
                if self.path == '/yui/live.m3u8' or re.fullmatch(r'/yui/segment_[0-9]{9}\.ts',self.path):
                    if owner.hls_directory is None or not owner.ready.is_set():
                        return self._json(503, {'error':'encoder_not_ready'})
                    if not owner._hls_requests.acquire(blocking=False): return self._json(503, {'error':'listener_limit'})
                    try:
                        name=self.path.rsplit('/',1)[1]
                        path=owner.hls_directory/name
                        if not path.is_file(): return self._json(404, {'error':'segment_not_ready_or_expired'})
                        data=path.read_bytes()
                        if len(data)>262144: return self._json(503, {'error':'segment_size'})
                        self.send_response(200)
                        self.send_header('Content-Type','application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t')
                        self.send_header('Content-Length',str(len(data)))
                        self.send_header('Cache-Control','no-store, no-cache, must-revalidate')
                        self.end_headers();self.wfile.write(data)
                    except (FileNotFoundError,ConnectionError,OSError):pass
                    finally:owner._hls_requests.release()
                    return
                if self.path != '/yui/live.ts': return self._json(404, {'error':'not_found'})
                if not owner.ready.is_set() or owner.state != 'streaming':
                    return self._json(503, {'error':'encoder_not_ready'})
                client = owner.listeners.join()
                if client is None: return self._json(503, {'error':'listener_limit'})
                try:
                    self.connection.settimeout(2)
                    self.send_response(200)
                    self.send_header('Content-Type','video/mp2t')
                    self.send_header('Cache-Control','no-store, no-cache, must-revalidate')
                    self.send_header('X-Accel-Buffering','no')
                    self.send_header('Transfer-Encoding','chunked')
                    self.send_header('Connection','close'); self.end_headers()
                    while not owner.stop_event.is_set():
                        try: data = client.get(timeout=.5)
                        except queue.Empty: continue
                        if data is None: break
                        self.wfile.write(('%x\r\n'%len(data)).encode('ascii'))
                        self.wfile.write(data); self.wfile.write(b'\r\n'); self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
                except (ConnectionError, OSError, socket.timeout): pass
                finally:
                    owner.listeners.leave(client); self.close_connection = True
            def do_POST(self):
                if not private: return self._json(404, {'error':'not_found'})
                if not self._origin_ok(): return self._json(403, {'error':'origin'})
                if not hmac.compare_digest(self.headers.get('X-Yui-Token',''), owner.token):
                    return self._json(403, {'error':'authorization'})
                try:
                    size = int(self.headers.get('Content-Length','-1'))
                    epoch = int(self.headers.get('X-Yui-Epoch','-1'))
                    sequence = int(self.headers.get('X-Yui-Sequence','-1'))
                except ValueError: return self._json(400, {'error':'headers'})
                sender = self.headers.get('X-Yui-Sender','')
                if not 0 <= size <= FRAME_BYTES * 2 or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',sender) or epoch < 0:
                    return self._json(400, {'error':'request'})
                if owner.state != 'streaming': return self._json(503, {'error':'encoder_not_ready'})
                if not owner.armed: return self._json(409, {'error':'world_not_playing'})
                if self.path == '/reset':
                    if size: return self._json(400, {'error':'reset_body'})
                    ok = owner.pcm.reset(sender,epoch)
                    return self._json(200 if ok else 409, {'status':'reset' if ok else 'stale_or_busy'})
                if self.path != '/audio': return self._json(404, {'error':'not_found'})
                if self.headers.get('Content-Type','').split(';')[0] != 'application/octet-stream':
                    return self._json(415, {'error':'pcm_content_type'})
                self.connection.settimeout(2)
                try: data = self.rfile.read(size)
                except (OSError,socket.timeout): return self._json(408, {'error':'body_timeout'})
                if len(data) != size: return self._json(400, {'error':'incomplete_body'})
                code, status = owner.pcm.append(sender,epoch,sequence,data)
                return self._json(code, {'status':status, **owner.pcm.snapshot()})
        return Handler

    def encoder_command(self):
        return [str(self.config.binary()), '-hide_banner','-loglevel','warning','-nostdin',
            '-f','lavfi','-i','color=c=black:s=160x96:r=5',
            '-f','s16le','-ar',str(SAMPLE_RATE),'-ac','1','-probesize','32','-analyzeduration','0','-i','pipe:0',
            '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast','-tune','zerolatency',
            '-profile:v','baseline','-pix_fmt','yuv420p','-g','5','-keyint_min','5','-sc_threshold','0',
            '-c:a','aac','-b:a','64k','-ar',str(SAMPLE_RATE),'-ac','1',
            '-flags','+global_header','-f','tee',
            '[f=mpegts:muxrate=128k:mpegts_flags=+resend_headers:pat_period=0.1]pipe:1|'
            '[f=hls:hls_time=1:hls_list_size=8:hls_delete_threshold=4:hls_flags=delete_segments+independent_segments+omit_endlist+temp_file:hls_segment_filename=segment_%09d.ts]live.m3u8']

    def _thread(self, function, name):
        thread = threading.Thread(target=function,name=name,daemon=True)
        self.threads.append(thread); thread.start()

    def start(self):
        if self.state != 'stopped': return
        if self.owned.closed:self.owned=OwnedProcesses()
        self.state = 'starting'; self.stop_event.clear()
        try:
            self._load_token()
            parent=self.config.state_file().resolve().parent
            parent.mkdir(parents=True,exist_ok=True)
            self._hls_temp=tempfile.TemporaryDirectory(prefix='hls-',dir=parent)
            self.hls_directory=Path(self._hls_temp.name).resolve()
            if self.hls_directory.parent!=parent:raise ValueError('invalid audio temporary directory')
            if not self.config.binary().is_file(): raise FileNotFoundError('voice FFmpeg is not installed')
            for port, private in [(self.config.ingest_port,True),(self.config.stream_port,False)]:
                server = GatewayServer(('127.0.0.1',port),self._handler(private))
                self.servers.append(server)
            flags = getattr(subprocess,'CREATE_NO_WINDOW',0)
            self.encoder = self.owned.start(self.encoder_command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,bufsize=0,creationflags=flags,cwd=self.hls_directory)
            self.state = 'streaming'
            self._thread(self._write_pcm,'yui-voice-pcm')
            self._thread(self._read_stream,'yui-voice-ts')
            self._thread(self._read_errors,'yui-voice-errors')
            for server in self.servers:
                self._thread(lambda server=server: server.serve_forever(poll_interval=.1),'yui-voice-http')
            if not self.ready.wait(5): raise RuntimeError('voice encoder startup timeout')
            if self.config.public_tunnel:
                self._thread(self._probe_tunnel,'yui-voice-public-health')
                self._thread(self._start_tunnel,'yui-voice-tunnel')
        except Exception as exc:
            self.error = type(exc).__name__ + ': ' + str(exc)
            self.close(); self.state = 'failed'
            raise

    def _write_pcm(self):
        deadline = time.monotonic()
        try:
            while not self.stop_event.is_set():
                data = self.pcm.take()
                view = memoryview(data)
                while view:
                    count = self.encoder.stdin.write(view)
                    if not count: raise BrokenPipeError('encoder input closed')
                    view = view[count:]
                deadline += .1
                delay = deadline-time.monotonic()
                if delay < -.25: deadline = time.monotonic()
                elif delay > 0: self.stop_event.wait(delay)
        except (OSError,ValueError) as exc:
            if not self.stop_event.is_set(): self.error='encoder_write_failed'; self.state='failed'; self.pcm.clear()

    def _read_stream(self):
        pending = bytearray()
        try:
            while not self.stop_event.is_set():
                data = self.encoder.stdout.read(1316)
                if not data: break
                pending.extend(data)
                count = len(pending)//188*188
                if count:
                    chunk = bytes(pending[:count]); del pending[:count]
                    self.encoded_bytes += len(chunk); self.ready.set(); self.listeners.broadcast(chunk)
        finally:
            if not self.stop_event.is_set():
                self.error='encoder_exited'; self.state='failed'; self.pcm.clear()

    def _read_errors(self):
        while not self.stop_event.is_set():
            line = self.encoder.stderr.readline()
            if not line: break
            self.stderr_tail.append(line.decode('utf-8','replace').strip()[:300])

    def _start_tunnel(self):
        with self._tunnel_lock:
            self.tunnel_state = 'starting'
            self.public_url = None
            self._tunnel_url = None
            self.public_probe_error = None
            self.public_probe_checked_at = None
        try:
            binary = self.config.tunnel_binary()
            if not binary.is_file(): raise FileNotFoundError('cloudflared missing')
            if self.stop_event.is_set(): return
            tunnel_config=self.hls_directory/'tunnel-empty.yml'
            tunnel_config.write_text('{}\n',encoding='utf-8')
            self.tunnel = self.owned.start([str(binary),'--no-autoupdate','tunnel','--config',str(tunnel_config),'--protocol','http2',
                '--url','http://127.0.0.1:'+str(self.config.stream_port),'--metrics','127.0.0.1:0'],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if self.stop_event.is_set(): self.tunnel.terminate(); return
            while not self.stop_event.is_set():
                line = self.tunnel.stdout.readline(2048)
                if not line: break
                match = re.search(rb'https://[a-z0-9-]+\.trycloudflare\.com',line)
                if match:
                    candidate = match.group().decode('ascii')+'/yui/live.m3u8'
                    with self._tunnel_lock:
                        changed = candidate != self._tunnel_url
                        if changed:
                            self._tunnel_url = candidate
                            self.public_url = None
                            self.tunnel_state = 'probing'
                    if changed:
                        self._tunnel_probe_wakeup.set()
                # Connection logs are useful signals, never proof of playback.
                if re.search(rb'\bunregistered tunnel connection\b|\bconnection terminated\b',line.lower()):
                    with self._tunnel_lock:
                        self.public_url = None
                        self.tunnel_state = 'probing'
                    self._tunnel_probe_wakeup.set()
        except Exception:
            pass
        finally:
            with self._tunnel_lock:
                self._tunnel_url = None
                self.public_url = None
                if not self.stop_event.is_set():
                    self.tunnel_state = 'failed'
            self._tunnel_probe_wakeup.set()

    def _probe_tunnel(self):
        while not self.stop_event.is_set():
            self._tunnel_probe_wakeup.wait(5)
            self._tunnel_probe_wakeup.clear()
            if self.stop_event.is_set():
                break
            with self._tunnel_lock:
                candidate = self._tunnel_url
            if candidate is None:
                continue
            error = probe_public_hls(candidate, stop_event=self.stop_event)
            with self._tunnel_lock:
                # A late result must not revive an exited/replaced tunnel.
                if self.stop_event.is_set() or candidate != self._tunnel_url:
                    continue
                self.public_probe_error = error
                self.public_probe_checked_at = time.time()
                self.public_url = candidate if error is None else None
                self.tunnel_state = 'ready' if error is None else 'unavailable'

    def snapshot(self):
        return {'enabled':self.config.enabled,'state':self.state,'error':self.error,
                'armed':self.armed,
                'tunnel_state':self.tunnel_state,'public_url':self.public_url,
                'public_probe_error':self.public_probe_error,'public_probe_checked_at':self.public_probe_checked_at,
                'sample_rate':SAMPLE_RATE,'listeners':len(self.listeners.clients),
                'slow_disconnects':self.listeners.slow_disconnects,'encoded_bytes':self.encoded_bytes,
                **self.pcm.snapshot()}

    def close(self):
        self.stop_event.set(); self._tunnel_probe_wakeup.set(); self.ready.clear(); self.pcm.clear()
        if self.owned.close():self.error='native_process_cleanup_failed'
        for server in self.servers:
            if any(t.name == 'yui-voice-http' and t.is_alive() for t in self.threads): server.shutdown()
            server.server_close()
        self.servers.clear()
        for thread in self.threads:
            if thread is not threading.current_thread(): thread.join(timeout=2)
        self.threads.clear(); self.state='stopped'; self.public_url=None; self.tunnel_state='disabled'
        self._tunnel_url = None
        if self._hls_temp is not None:
            self._hls_temp.cleanup(); self._hls_temp=None; self.hls_directory=None

