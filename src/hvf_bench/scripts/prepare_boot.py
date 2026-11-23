"""Fetch a matching ARM64 Alpine kernel/initramfs/modloop from one official directory."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/aarch64/netboot/")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/alpine")
    args = parser.parse_args()
    base = args.base_url.rstrip('/') + '/'
    if urlparse(base).scheme != 'https' or urlparse(base).hostname != 'dl-cdn.alpinelinux.org':
        parser.error("Use an official Alpine HTTPS netboot directory")
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ('vmlinuz-virt', 'initramfs-virt', 'modloop-virt'):
        destination = args.output / name
        temporary = destination.with_suffix('.partial')
        with urllib.request.urlopen(base + name, timeout=90) as response, temporary.open('wb') as stream:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 512 * 1024 * 1024:
                    raise ValueError("Unexpected boot asset size")
                stream.write(block)
        temporary.replace(destination)
        rows.append({'name': name, 'url': base + name, 'bytes': total,
                     'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
        print('Downloaded', name, total)
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports/boot-download.json').write_text(json.dumps({'files': rows,
        'verification': 'Official HTTPS transport and local byte digests; upstream signatures are not checked by this helper.'}, indent=2) + '\n')


if __name__ == '__main__':
    main()
