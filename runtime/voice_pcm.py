"""Bounded PCM ordering, reset fencing and independent listener queues."""
from __future__ import annotations
from collections import deque
import hashlib
import queue
import re
import threading
import time

SAMPLE_RATE = 24000
FRAME_SAMPLES = 2400
FRAME_BYTES = FRAME_SAMPLES * 2

class PcmQueue:
    """A single active sender lease, ordered retries, bounded audio, and reset fencing."""
    def __init__(self, seconds=3.0, clock=time.monotonic):
        self.lock = threading.Lock()
        self.blocks = deque()
        self.bytes = 0
        self.limit = int(seconds * SAMPLE_RATE) * 2
        self.clock = clock
        self.sender = ''
        self.epoch = -1
        self.sequence = -1
        self.last_seen = -100.0
        self.packet_hash = ''
        self.received_samples = 0
        self.resets = 0

    def append(self, sender, epoch, sequence, data):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}', sender or ''):
            return 400, 'sender'
        if type(epoch) is not int or epoch < 0 or type(sequence) is not int or sequence < 0:
            return 400, 'sequence'
        if not data or len(data) % 2 or len(data) > FRAME_BYTES * 2:
            return 400, 'pcm_size'
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            now = self.clock()
            if sender != self.sender:
                if self.bytes or now - self.last_seen < 3:
                    return 409, 'sender_busy'
                self.sender, self.epoch, self.sequence = sender, epoch, -1
            if epoch < self.epoch:
                return 409, 'stale_epoch'
            if epoch > self.epoch:
                self.blocks.clear(); self.bytes = 0
                self.epoch, self.sequence = epoch, -1
            if sequence == self.sequence:
                return (200, 'duplicate') if digest == self.packet_hash else (409, 'sequence_conflict')
            if sequence != self.sequence + 1:
                return 409, 'out_of_order'
            if self.bytes + len(data) > self.limit:
                return 429, 'backpressure'
            self.blocks.append(bytes(data)); self.bytes += len(data)
            self.sequence, self.packet_hash, self.last_seen = sequence, digest, now
            self.received_samples += len(data) // 2
        return 200, 'accepted'

    def reset(self, sender, epoch):
        with self.lock:
            if sender != self.sender and (self.bytes or self.clock() - self.last_seen < 3):
                return False
            if sender == self.sender and epoch <= self.epoch:
                return False
            self.sender, self.epoch, self.sequence = sender, epoch, -1
            self.blocks.clear(); self.bytes = 0
            self.packet_hash = ''; self.last_seen = self.clock(); self.resets += 1
            return True

    def take(self):
        with self.lock:
            output = bytearray()
            while self.blocks and len(output) < FRAME_BYTES:
                block = self.blocks.popleft()
                size = min(FRAME_BYTES-len(output), len(block))
                output.extend(block[:size]); self.bytes -= size
                if size < len(block): self.blocks.appendleft(block[size:])
            output.extend(b'\0' * (FRAME_BYTES-len(output)))
            return bytes(output)

    def clear(self):
        with self.lock:
            self.blocks.clear(); self.bytes = 0

    def snapshot(self):
        with self.lock:
            return {'queued_ms': round(self.bytes / (SAMPLE_RATE * 2) * 1000),
                    'received_samples': self.received_samples, 'resets': self.resets}


class LiveSubscribers:
    def __init__(self, maximum=8, queue_size=48):
        self.maximum, self.queue_size = maximum, queue_size
        self.lock = threading.Lock()
        self.clients = set()
        self.slow_disconnects = 0

    def join(self):
        with self.lock:
            if len(self.clients) >= self.maximum: return None
            client = queue.Queue(self.queue_size); self.clients.add(client)
            return client

    def leave(self, client):
        with self.lock: self.clients.discard(client)

    def broadcast(self, chunk):
        with self.lock:
            for client in list(self.clients):
                try: client.put_nowait(chunk)
                except queue.Full:
                    self.clients.discard(client); self.slow_disconnects += 1
                    while not client.empty():
                        try: client.get_nowait()
                        except queue.Empty: break
                    client.put_nowait(None)

