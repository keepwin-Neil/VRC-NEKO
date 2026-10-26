import json
from pathlib import Path
import queue
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from yui_npc_controller.runtime.voice_gateway import (
    FRAME_BYTES, LiveSubscribers, PcmQueue, VoiceGateway, VoiceGatewayConfig,
)


class VoiceQueueTests(unittest.TestCase):
    sender = 'a' * 32

    def test_retry_is_idempotent_and_conflicting_retry_rejected(self):
        pcm = PcmQueue()
        self.assertEqual(pcm.append(self.sender,0,0,b'\1\0'*20),(200,'accepted'))
        self.assertEqual(pcm.append(self.sender,0,0,b'\1\0'*20),(200,'duplicate'))
        self.assertEqual(pcm.append(self.sender,0,0,b'\2\0'*20),(409,'sequence_conflict'))
        self.assertEqual(pcm.snapshot()['received_samples'],20)

    def test_reset_cannot_replay_old_audio(self):
        pcm = PcmQueue()
        pcm.append(self.sender,0,0,b'\1\0'*20)
        self.assertTrue(pcm.reset(self.sender,1))
        self.assertEqual(pcm.append(self.sender,0,1,b'\1\0'*20),(409,'stale_epoch'))
        self.assertEqual(pcm.take(),bytes(FRAME_BYTES))
        self.assertEqual(pcm.append(self.sender,1,0,b'\2\0'*20),(200,'accepted'))
        self.assertFalse(pcm.reset(self.sender,1))

    def test_active_sender_lease_blocks_duplicate_windows(self):
        clock=[0]
        pcm=PcmQueue(clock=lambda:clock[0])
        pcm.append(self.sender,0,0,b'\1\0')
        self.assertEqual(pcm.append('b'*32,0,0,b'\2\0'),(409,'sender_busy'))
        pcm.take(); clock[0]=4
        self.assertEqual(pcm.append('b'*32,0,0,b'\2\0'),(200,'accepted'))

    def test_bounded_backpressure_does_not_consume_sequence(self):
        pcm=PcmQueue(seconds=.5)
        for seq in range(5): self.assertEqual(pcm.append(self.sender,0,seq,bytes(FRAME_BYTES))[0],200)
        self.assertEqual(pcm.append(self.sender,0,5,bytes(FRAME_BYTES)),(429,'backpressure'))
        pcm.take()
        self.assertEqual(pcm.append(self.sender,0,5,bytes(FRAME_BYTES))[0],200)

    def test_underflow_pads_silence_without_repeating_old_samples(self):
        pcm=PcmQueue()
        pcm.append(self.sender,0,0,b'\1\0'*12)
        block=pcm.take()
        self.assertEqual(block[:24],b'\1\0'*12)
        self.assertEqual(block[24:],bytes(FRAME_BYTES-24))
        self.assertEqual(pcm.take(),bytes(FRAME_BYTES))

    def test_validation_rejects_oversized_odd_and_unordered_pcm(self):
        pcm=PcmQueue()
        for body in (b'',b'x',bytes(FRAME_BYTES*2+2)):
            self.assertEqual(pcm.append(self.sender,0,0,body)[0],400)
        self.assertEqual(pcm.append(self.sender,0,2,b'\0\0'),(409,'out_of_order'))

    def test_slow_receiver_is_disconnected_without_blocking_others(self):
        listeners=LiveSubscribers(2,queue_size=2)
        slow=listeners.join(); fast=listeners.join()
        self.assertIsNone(listeners.join())
        for _ in range(3):
            listeners.broadcast(b'packet')
            self.assertEqual(fast.get_nowait(),b'packet')
        self.assertIsNone(slow.get_nowait())
        self.assertEqual(listeners.slow_disconnects,1)
        self.assertEqual(len(listeners.clients),1)

    def test_config_rejects_port_aliases_and_invalid_limits(self):
        for data in ({'enabled':'yes'},{'ingest_port':17864},{'max_queue_seconds':float('nan')},{'max_listeners':100}):
            with self.assertRaises(ValueError): VoiceGatewayConfig.from_mapping(data)


class VoiceHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        ports=[]
        for _ in range(2):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0)); ports.append(sock.getsockname()[1])
        binary=Path(__file__).resolve().parents[1]/'vendor/voice/ffmpeg.exe'
        if not binary.exists(): self.skipTest('Windows FFmpeg integration dependency not installed')
        self.gateway=VoiceGateway(VoiceGatewayConfig(enabled=True,ingest_port=ports[0],stream_port=ports[1],
            ffmpeg_path=str(binary),state_path=str(Path(self.directory.name)/'private.json')), token='f'*64)
        self.gateway.start()
        self.private='http://127.0.0.1:'+str(ports[0]); self.public='http://127.0.0.1:'+str(ports[1])
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        if hasattr(self,'gateway'): self.gateway.close()
        self.directory.cleanup()

    def request(self,path,body=None,**headers):
        request=urllib.request.Request(path,data=body,headers=headers)
        try: return self.opener.open(request,timeout=3)
        except urllib.error.HTTPError as error: return error

    def headers(self):
        return {'Content-Type':'application/octet-stream','X-Yui-Token':'f'*64,
            'X-Yui-Sender':'a'*32,'X-Yui-Epoch':'0','X-Yui-Sequence':'0'}

    def test_public_listener_cannot_ingest_or_read_private_health(self):
        for path in ('/audio','/health','/gateway.json','/../private.json'):
            with self.request(self.public+path,b'\0\0') as response: self.assertEqual(response.status,404)
        with self.request(self.public+'/health') as response: self.assertEqual(response.status,404)

    def test_auth_origin_and_world_gate_are_enforced(self):
        with self.request(self.private+'/audio',b'\0\0') as response: self.assertEqual(response.status,403)
        with self.request(self.private+'/audio',b'\0\0',**self.headers(),Origin='https://attacker.example') as response:
            self.assertEqual(response.status,403)
        with self.request(self.private+'/audio',b'\0\0',**self.headers()) as response: self.assertEqual(response.status,409)
        self.gateway.set_armed(True)
        with self.request(self.private+'/audio',b'\1\0'*10,**self.headers()) as response:
            self.assertEqual(response.status,200)
        self.gateway.set_armed(False)
        self.assertEqual(self.gateway.pcm.snapshot()['queued_ms'],0)

    def test_two_live_receivers_and_late_joiner_receive_valid_transport_packets(self):
        with self.request(self.public+'/yui/live.ts') as first, self.request(self.public+'/yui/live.ts') as second:
            for response in (first,second):
                data=response.read(188*3)
                self.assertEqual(response.headers['Content-Type'],'video/mp2t')
                self.assertEqual([data[i] for i in range(0,len(data),188)],[0x47]*3)
            time.sleep(.15)
            with self.request(self.public+'/yui/live.ts') as late:
                self.assertEqual(late.read(188)[0],0x47)
        self.assertGreater(self.gateway.encoded_bytes,0)

    def test_health_never_exposes_ingest_token_or_pcm(self):
        with self.request(self.private+'/health',Origin='http://127.0.0.1:48911') as response:
            body=response.read().decode()
            self.assertNotIn('f'*64,body)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'],'http://127.0.0.1:48911')
            self.assertEqual(json.loads(body)['state'],'streaming')

    def test_hls_has_positive_target_duration_and_valid_segments(self):
        deadline=time.monotonic()+6
        while True:
            with self.request(self.public+'/yui/live.m3u8') as response:
                playlist=response.read().decode()
                if response.status==200:break
            if time.monotonic()>=deadline:self.fail('HLS playlist did not become ready')
            time.sleep(.1)
        lines=playlist.splitlines()
        target=int(next(line.split(':',1)[1] for line in lines if line.startswith('#EXT-X-TARGETDURATION:')))
        self.assertGreaterEqual(target,1,'A zero target duration can busy-loop native live players')
        durations=[float(line.split(':',1)[1].rstrip(',')) for line in lines if line.startswith('#EXTINF:')]
        self.assertTrue(durations)
        self.assertTrue(all(0<duration<=target+.5 for duration in durations))
        segment=next(line for line in lines if line.endswith('.ts'))
        with self.request(self.public+'/yui/'+segment) as response:
            data=response.read();self.assertEqual(response.status,200)
            self.assertGreaterEqual(len(data),188)
            self.assertEqual(len(data)%188,0)
            self.assertTrue(all(data[i]==0x47 for i in range(0,len(data),188)))


if __name__=='__main__': unittest.main()
