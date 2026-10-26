"""Restore pinned native tools after checking exact SHA256, without running them."""
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile


def verified_download(url, expected):
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Voice dependency SHA256 mismatch')
    return data


def main():
    source = Path(__file__).resolve().parents[1]
    lock = json.loads((source / 'host/voice-dependencies.lock.json').read_text(encoding='utf-8'))
    target = source / 'vendor/voice'
    target.mkdir(parents=True, exist_ok=True)
    ffmpeg = lock['ffmpeg']
    wheel = verified_download(ffmpeg['url'], ffmpeg['wheel_sha256'])
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        name = next(n for n in archive.namelist() if n.endswith('.exe') and 'ffmpeg-' in n)
        binary = archive.read(name)
    if hashlib.sha256(binary).hexdigest() != ffmpeg['binary_sha256']:
        raise ValueError('FFmpeg binary SHA256 mismatch')
    cloudflared = lock['cloudflared']
    tunnel = verified_download(cloudflared['url'], cloudflared['sha256'])
    for name, data in [('ffmpeg.exe', binary), ('cloudflared.exe', tunnel)]:
        destination = target / name
        if destination.exists() and destination.read_bytes() != data:
            raise FileExistsError('Different existing dependency: ' + name)
        destination.write_bytes(data)
    licenses = source / 'host/licenses'
    for item in licenses.iterdir():
        if item.is_file():
            (target / item.name).write_bytes(item.read_bytes())
    print('Pinned FFmpeg and cloudflared restored and verified.')


if __name__ == '__main__':
    main()
