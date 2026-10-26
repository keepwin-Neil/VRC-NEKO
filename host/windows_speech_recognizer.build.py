"""Compatibility entry point for the deterministic Windows helper build."""
import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--compiler', type=Path, required=True)
parser.add_argument('--dotnet', type=Path, required=True)
arguments = parser.parse_args()
output = arguments.output.resolve()
if output.name != 'windows_speech_recognizer.exe':
    raise SystemExit('Use the canonical windows_speech_recognizer.exe output name')
subprocess.run([sys.executable, str(Path(__file__).with_name('build_windows_speech.py')),
    '--output', str(output.parent), '--compiler', str(arguments.compiler), '--dotnet', str(arguments.dotnet)], check=True)
