"""Build a separately supplied existing Go gateway and authored restricted relay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Existing ivi-go-gateway Go source directory')
    p.add_argument('--go', default='go')
    args = p.parse_args()
    source = args.source.resolve()
    if not (source / 'go.mod').is_file() or not (source / 'main.go').is_file():
        p.error('Expected separately supplied Go gateway source')
    go = shutil.which(args.go)
    if not go:
        p.error('Go toolchain missing; pass --go PATH')
    original = {x.name: hashlib.sha256(x.read_bytes()).hexdigest() for x in source.glob('*.go')}
    output = ROOT / 'build';output.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(CGO_ENABLED='0', GOOS='linux', GOARCH='arm64', GOARM64='v8.0')
    records = []
    for name, cwd, target in [('ivi-go-gateway-linux-arm64', source, '.'),
        ('bench-tcp-relay-linux-arm64', ROOT, str(ROOT / 'native/guest-tcp-relay/main.go'))]:
        argv = [go, 'build', '-trimpath', '-ldflags=-s -w', '-o', str(output / name), target]
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
        records.append(dict(argv=argv, cwd=str(cwd), exit_status=result.returncode, stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError(result.stderr)
        data = (output / name).read_bytes()
        if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
            raise ValueError('Build did not produce an AArch64 ELF')
    if original != {x.name: hashlib.sha256(x.read_bytes()).hexdigest() for x in source.glob('*.go')}:
        raise ValueError('Existing gateway sources changed during build')
    reports = ROOT / 'reports';reports.mkdir(exist_ok=True)
    (reports / 'guest-gateway-build.json').write_text(json.dumps(dict(commands=records,
        original_gateway_source_hashes=original, outputs={name: hashlib.sha256((output/name).read_bytes()).hexdigest()
          for name in ['ivi-go-gateway-linux-arm64', 'bench-tcp-relay-linux-arm64']}), indent=2), encoding='utf-8')
    print('Built static ARM64 gateway and restricted relay; reference source unchanged.')

if __name__ == '__main__':
    main()
