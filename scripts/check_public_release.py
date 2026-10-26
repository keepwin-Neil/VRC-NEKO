"""Check the exact Git index or reachable history. Never print matched values."""
from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '.gitignore', '.gitattributes', 'README.md', 'README.zh-CN.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md',
    'pyproject.toml', 'plugin.toml', 'profiles.toml', 'config.example.toml',
    'pytest.ini', 'run_tests.py', 'run_tests.bat', '__init__.py', 'mcp_server.py',
}
PATTERNS = (
    'runtime/*.py', 'ui/*.tsx', 'tests/*.py', 'host/*.py', 'host/*.cs',
    'host/*.js', 'host/*.cjs', 'host/voice-dependencies.lock.json',
    'host/licenses/*', 'integrations/motion_service/*.py',
    'integrations/motion_service/README.md', 'integrations/verify_continuous_generator.py',
    'Docs/Protocols/*.md', 'Docs/Protocols/*.json', 'Docs/VALIDATION.md', 'scripts/*.py',
    'unity/README.md', 'unity/Assets/NEKO/*.cs', 'unity/Assets/NEKO/*.cs.meta',
    'unity/Assets/NEKO/**/*.cs', 'unity/Assets/NEKO/**/*.cs.meta',
    '.github/workflows/*.yml',
)
DENIED_PARTS = {
    '.git', '.private', 'vendor', 'third_party', 'third-party', 'backups', 'backup',
    'before', 'memory', 'logs', 'release', 'profiles', 'ardy-patches', '__pycache__',
    'models', 'checkpoints', 'weights', 'fonts', 'scenes', '.secrets', 'node_modules',
}
RULES = {
    'provider-token': re.compile(r'(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'jwt': re.compile(r'eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}'),
    'vrc-identifier': re.compile(r'(?:usr_|wrld_|avtr_|authcookie_)[0-9A-Za-z-]{20,}'),
    'personal-home': re.compile(r'(?i)(?:[A-Z]:[\\/]+Users[\\/]+[^\s/\\"\'<>]+|/mnt/[a-z]/[Uu]sers/[^\s/"\'<>]+|/(?:home|Users)/[^\s/"\'<>]+)'),
    'private-network': re.compile(r'\b(?:192\.168\.(?:\d{1,3}\.)\d{1,3}|10\.(?:\d{1,3}\.){2}\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b'),
    'credential-url': re.compile(r'https?://[^\s/]+:[^\s/@]+@'),
}
LITERAL_SECRET = re.compile(r'''(?i)["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|authorization)["']?\s*[:=]\s*["']([A-Za-z0-9_+/=. :{}$-]{12,})["']''')
PLACEHOLDER = re.compile(r'(?i)^(?:test[-_ ]|fake[-_ ]|dummy|example|placeholder|invalid|redacted|your[-_ ]|bearer\s+(?:test|fake)|\{|\$|[xX]{12,})')


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', '-c', f'safe.directory={ROOT.as_posix()}', *args], cwd=ROOT, stderr=subprocess.PIPE)


def allowed(name: str) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or any(part.lower() in DENIED_PARTS for part in p.parts):
        return False
    if name in ROOT_FILES:
        return True
    return any(
        ('**' in pattern or name.count('/') == pattern.count('/'))
        and fnmatch.fnmatchcase(name, pattern) for pattern in PATTERNS
    )


def inspect(name: str, data: bytes) -> list[tuple[str, int]]:
    issues = []
    if not allowed(name):
        issues.append(('file-outside-source-policy', 0))
    if len(data) > 1024 * 1024:
        issues.append(('file-over-1MiB', 0))
    if b'\0' in data:
        return issues + [('binary-content', 0)]
    try:
        content = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return issues + [('non-UTF8-content', 0)]
    for line_no, line in enumerate(content.splitlines(), 1):
        for label, rule in RULES.items():
            if rule.search(line):
                issues.append((label, line_no))
        match = LITERAL_SECRET.search(line)
        if match and not PLACEHOLDER.search(match.group(1)):
            issues.append(('literal-credential-review', line_no))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--working-tree', action='store_true', help='Scan public-policy files without writing Git metadata')
    parser.add_argument('--staged', action='store_true', help='Scan index bytes, including forced additions')
    parser.add_argument('--all-history', action='store_true', help='Scan every tree reachable from local refs')
    args = parser.parse_args()
    if sum((args.staged, args.all_history, args.working_tree)) > 1:
        parser.error('Choose one scan mode')
    if args.working_tree:
        files = [p for p in ROOT.rglob('*') if p.is_file()
                 and not any(part in ('.git', '.private', '__pycache__', '.pytest_cache')
                             for part in p.relative_to(ROOT).parts)]
        candidates = [p for p in files if allowed(p.relative_to(ROOT).as_posix())]
        findings = []
        for path in candidates:
            name = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                findings.append((name, 'symlink', 0))
            findings.extend((name, label, line) for label, line in inspect(name, path.read_bytes()))
        for name, label, line in findings:
            print(f'FAIL {name}:{line} [{label}]')
        print(f'Scanned {len(candidates)} public working-tree files; {len(findings)} finding(s).')
        print(f'Excluded {len(files) - len(candidates)} local-only files by source policy.')
        return int(bool(findings) or not candidates)
    refs = git('rev-list', '--all').decode().splitlines() if args.all_history else [None]
    seen = set()
    failures = []
    total = 0
    for ref in refs:
        if ref:
            raw = git('ls-tree', '-r', '-z', ref)
            entries = [entry.split(b'\t', 1) for entry in raw.split(b'\0') if entry]
        else:
            entries = [entry.split(b'\t', 1) for entry in git('ls-files', '--stage', '-z').split(b'\0') if entry]
        for metadata, raw_name in entries:
            name = raw_name.decode('utf-8')
            fields = metadata.decode().split()
            mode = fields[0]
            blob = fields[2] if ref else fields[1]
            identity = (name, blob) if ref or args.staged else (name, 'working')
            if identity in seen:
                continue
            seen.add(identity)
            total += 1
            if mode not in ('100644', '100755'):
                failures.append((name, 'symlink-or-gitlink', 0))
                continue
            data = git('cat-file', 'blob', blob) if ref or args.staged else (ROOT / name).read_bytes()
            failures.extend((name, label, line) for label, line in inspect(name, data))
    if total == 0:
        print('FAIL: no files scanned; stage the intended release first.')
        return 1
    for name, label, line in failures:
        print(f'FAIL {name}:{line} [{label}]')
    print(f'Scanned {total} unique file versions across {len(refs)} tree(s); {len(failures)} finding(s).')
    return int(bool(failures))


if __name__ == '__main__':
    sys.exit(main())
