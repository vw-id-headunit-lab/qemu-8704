
import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / x) for x in (
    'tools/pythondeps', 'var/inputs/analysis', 'src/emu')]
from android_sparse import PartitionView, partitions
from dissect.extfs import ExtFS
from ufs_android_media import SUPER_RAW_SHA256, super_metadata

SERVICE = 'android.hardware.keymaster@4.0_tee-service'


class Extent(io.RawIOBase):
    def __init__(self, source, start, size):
        self.source, self.start, self.size, self.pos = source, start, size, 0

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        target = {0: 0, 1: self.pos, 2: self.size}[whence] + off
        if not 0 <= target <= self.size:
            raise ValueError('seek outside super')
        self.pos = target
        return target

    def read(self, n=-1):
        n = self.size - self.pos if n < 0 else min(n, self.size - self.pos)
        if n > 64 * 1024 * 1024:
            raise ValueError('bounded reads only')
        self.source.seek(self.start + self.pos)
        data = self.source.read(n)
        if len(data) != n:
            raise EOFError('short super read')
        self.pos += n
        return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('measured_lun', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('extra', nargs='*')
    a = p.parse_args()
    observed = json.loads(a.measured_lun.read_text(encoding='utf-8'))
    base = Path(observed['base']).resolve(strict=True)
    st = base.stat()
    if not base.is_relative_to(ROOT / 'var') or [
            st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns] != observed['stat']:
        raise ValueError('different LUN0 from measurement')
    manifest = json.loads(base.with_suffix('.json').read_text(encoding='utf-8'))
    match, = [r for r in observed['archive_payloads_verified'] if r['slot'] == 48]
    slot, = [r for r in manifest['partitions'] if r['slot'] == 48]
    if slot['label'] != 'super' or match['sha256'] != SUPER_RAW_SHA256:
        raise ValueError('no measured OEM super')
    paths = ['/bin/hw/' + SERVICE, '/etc/init/' + SERVICE + '.rc', *a.extra]
    report = {}
    a.output.mkdir(exist_ok=True)
    with base.open('rb') as stream:
        start = slot['first'] * manifest['sector_bytes']
        metadata = super_metadata(stream, start, match['bytes'])
        view = Extent(stream, start, match['bytes'])
        vendor, = [row for row in partitions(view) if row['name'] == 'vendor_a']
        fs = ExtFS(PartitionView(view, vendor))
        for name in paths:
            node = fs.get(name)
            if not stat.S_ISREG(node.filetype) or not 0 < node.size <= 8 * 1024 * 1024:
                raise ValueError('not a bounded regular vendor file')
            data = node.open().read()
            if len(data) != node.size:
                raise EOFError('short vendor file')
            output = a.output / Path(name).name
            if output.exists() and output.read_bytes() != data:
                raise ValueError('preserve different previous extraction')
            if not output.exists():
                with output.open('xb') as dst:
                    dst.write(data)
            report[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    print(json.dumps(dict(base=str(base), super=match, liblp=metadata, files=report), indent=2))


if __name__ == '__main__':
    main()
