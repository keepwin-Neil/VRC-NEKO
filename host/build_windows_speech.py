"""Build reproducible .NET Framework helpers using the project's Roslyn compiler."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--dotnet', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    framework = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319'
    references = [framework / name for name in ('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Web.Extensions.dll')]
    references.append(framework / 'WPF/System.Speech.dll')
    assert args.compiler.is_file() and all(path.is_file() for path in references), 'Roslyn/.NET Framework dependencies are missing'
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix='yui-speech-build-') as repeat:
        for name in ('windows_process_capture', 'windows_speech_recognizer'):
            code = source / (name + '.cs')
            target = output / (name + '.exe')
            def build(path):
                subprocess.run([str(args.dotnet), str(args.compiler), '/nologo', '/noconfig', '/nostdlib+', '/target:exe',
                    '/platform:x64', '/optimize+', '/deterministic+', '/langversion:7.3',
                    '/pathmap:' + str(source) + '=/_yui_source/host',
                    *['/reference:' + str(path) for path in references], '/out:' + str(path), str(code)],
                    check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            build(target)
            other = Path(repeat) / target.name
            build(other)
            assert target.read_bytes() == other.read_bytes(), 'Non-reproducible native helper: ' + name
            records.append(dict(path=target.name, bytes=target.stat().st_size, sha256=sha(target),
                source=code.name, source_sha256=sha(code), deterministic=True))
    report = dict(compiler=str(args.compiler), compiler_sha256=sha(args.compiler),
        references=[dict(path=str(path), sha256=sha(path)) for path in references], helpers=records)
    (output / 'build-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
