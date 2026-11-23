"""Check exactly the tracked/staged publication set, not ignored private inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TOP = {'.gitignore', '.gitattributes', '.editorconfig', 'LICENSE', 'README.md',
       'THIRD_PARTY_NOTICES.md', 'SECURITY.md', 'CONTRIBUTING.md', 'pyproject.toml',
       'requirements.txt', 'config.example.json', 'run.py', 'Run-HMI.cmd', 'Setup-Dependencies.cmd'}
DIRECTORIES = {'src', 'guest', 'platform', 'native', 'scripts', 'tests', 'docs', '.github'}
SUFFIXES = {'.py', '.java', '.c', '.sh', '.md', '.json', '.toml', '.txt', '.yml', '.yaml', '.cmd'}
# User-selected documentation image only. Other binary files remain excluded.
DOCUMENTATION_IMAGES = {
    'docs/images/hmi-menu-zh-cn.png': '519e9e4cf7fc164f685be2859375a48195ce8e0fb430cf2892f005c9d6f5db80',
}
PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'aws_access_key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'personal_workspace_path': re.compile(r'[A-Za-z]:[\\/](?:Users|baidunetdiskdownload)[\\/][^\s\x22]+', re.I),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', action='store_true', help='Check the public source tree before staging')
    args = parser.parse_args()
    if args.tree:
        names = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
                 and '.git' not in p.relative_to(ROOT).parts
                 and (p.relative_to(ROOT).parts[0] in DIRECTORIES
                      or len(p.relative_to(ROOT).parts) == 1 and p.name in TOP)
                 and '__pycache__' not in p.parts]
    else:
        data = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
        names = [n for n in data.decode('utf-8').split('\0') if n]
    if not names:
        parser.error('The publication set is empty; stage the source files first')
    findings, total = [], 0
    for name in sorted(names):
        path = ROOT / name
        reason = None
        if Path(name).parts[0] not in DIRECTORIES and name not in TOP:
            reason = 'unexpected top-level path'
        elif path.is_symlink():
            reason = 'symlink is not part of this source release'
        elif (name not in TOP and name not in DOCUMENTATION_IMAGES and path.suffix not in SUFFIXES
              and not (path.suffix == '.go' and name.startswith('native/guest-tcp-relay/'))):
            reason = 'unexpected extension'
        if args.tree:
            data = path.read_bytes()
        else:
            mode = subprocess.check_output(['git', 'ls-files', '--stage', '--', name], cwd=ROOT).decode().split()[0]
            if mode not in ('100644', '100755'):
                reason = 'non-regular file in Git index'
            data = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        total += len(data)
        if len(data) > 512 * 1024:
            reason = 'source file exceeds 512 KiB'
        if name in DOCUMENTATION_IMAGES:
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                reason = 'documentation image is not a PNG'
            elif hashlib.sha256(data).hexdigest() != DOCUMENTATION_IMAGES[name]:
                reason = 'documentation image differs from the reviewed screenshot'
            if reason:
                findings.append({'path': name, 'reason': reason})
            continue
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            reason, text = 'non-text content', ''
        if b'\0' in data:
            reason = 'binary NUL in a public source file'
        if reason:
            findings.append({'path': name, 'reason': reason})
        for label, expression in PATTERNS.items():
            if expression.search(text):
                findings.append({'path': name, 'reason': label})
    report = {'files_checked': len(names), 'source_bytes': total, 'findings': findings,
              'content_source': 'working tree' if args.tree else 'Git index blobs',
              'passed': not findings, 'limitations': 'Pattern and allowlist audit, not an exhaustive credential or rights review.'}
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports/public-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if not findings else 1


if __name__ == '__main__':
    raise SystemExit(main())
