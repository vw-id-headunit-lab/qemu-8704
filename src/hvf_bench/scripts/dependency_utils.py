"""Verified, cacheable downloads and bounded data-only archive extraction."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import time
from urllib.parse import urlparse
import urllib.request
import zipfile

HOSTS = {'repo.msys2.org', 'mirror.msys2.org', 'dl-cdn.alpinelinux.org',
         'archive.debian.org', 'registry.npmjs.org', 'github.com',
         'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
HOSTS.add('cdn.azul.com')
HOSTS.update({'mirrors.tuna.tsinghua.edu.cn', 'mirrors.ustc.edu.cn'})


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        raise ValueError('Expected an approved official HTTPS source: ' + url)


class Downloader:
    def __init__(self, cache, offline=False, seed_cache=None):
        self.cache = Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.seed_cache = Path(seed_cache).resolve() if seed_cache else None
        self.records = []

    def obtain(self, name, spec):
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Cache filename must be a basename')
        expected = spec['sha256']
        if len(expected) != 64:
            raise ValueError('A pinned SHA-256 is required')
        target = self.cache / name
        size = spec.get('size')
        def valid(path):
            return path.is_file() and (size is None or path.stat().st_size == size) and sha256(path) == expected
        if valid(target):
            self.records.append({'name': name, 'sha256': expected, 'source': 'verified-cache'})
            return target
        if target.exists():
            raise ValueError('Cached file differs from the lock; remove or quarantine it first: ' + name)
        if self.seed_cache:
            candidate = self.seed_cache / name
            if candidate.is_file():
                if not valid(candidate):
                    raise ValueError('Seed cache differs from the pinned digest: ' + name)
                shutil.copyfile(candidate, target)
                self.records.append({'name': name, 'sha256': expected, 'source': 'verified-seed-cache'})
                return target
        if self.offline:
            raise FileNotFoundError('Offline cache is missing verified dependency: ' + name)
        urls = spec.get('urls', [spec.get('url')])
        partial = target.with_name(target.name + '.partial')
        failures = []
        for url in urls:
            checked_url(url)
            for attempt in range(2):
                try:
                    if valid(target):
                        self.records.append({'name': name, 'sha256': expected, 'source': 'verified-cache'})
                        return target
                    print('Downloading:', name, 'from', urlparse(url).hostname, flush=True)
                    curl = shutil.which('curl.exe') or shutil.which('curl')
                    if curl:
                        command = [curl, '--fail', '--location', '--silent', '--show-error',
                            '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15',
                            '--max-time', '180', '--max-filesize', str(size or 1024 * 1024 * 1024),
                            '--output', str(partial), '--write-out', '%{url_effective}', url]
                        result = subprocess.run(command, capture_output=True, timeout=195)
                        if result.returncode:
                            raise OSError(result.stderr.decode(errors='replace'))
                        checked_url(result.stdout.decode().strip())
                        total = partial.stat().st_size
                    else:
                        request = urllib.request.Request(url, headers={'User-Agent': 'mib3-hmi-bench/0.1 dependency setup'})
                        started = time.monotonic()
                        with urllib.request.urlopen(request, timeout=30) as response, partial.open('wb') as output:
                            checked_url(response.geturl())
                            total = 0
                            while block := response.read(1024 * 1024):
                                total += len(block)
                                if total > (size if size is not None else 1024 * 1024 * 1024):
                                    raise ValueError('Download exceeds expected size')
                                if time.monotonic() - started > 180:
                                    raise TimeoutError('Download exceeded its wall-clock limit')
                                output.write(block)
                    if not valid(partial):
                        raise ValueError('Downloaded bytes differ from pinned size/SHA-256: ' + name)
                    partial.replace(target)
                    self.records.append({'name': name, 'sha256': expected, 'source': url, 'bytes': total})
                    print('Downloaded and verified:', name, total, flush=True)
                    return target
                except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                    failures.append(str(error))
                    if attempt == 0:
                        time.sleep(0.5)
        raise RuntimeError('Dependency download failed: ' + name + '\n' + '\n'.join(failures))


def destination(root, member):
    if '\\' in member:
        raise ValueError('Unexpected backslash in archive path')
    path = PurePosixPath(member)
    if path.is_absolute() or not path.parts or '..' in path.parts or any(':' in p for p in path.parts):
        raise ValueError('Archive path leaves extraction directory')
    root = Path(root).resolve()
    target = root.joinpath(*path.parts).resolve()
    if root not in target.parents:
        raise ValueError('Archive member must be inside extraction directory')
    return target


@contextmanager
def zstd_tar(path):
    import zstandard
    with Path(path).open('rb') as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as decoded:
            with tarfile.open(fileobj=decoded, mode='r|') as archive:
                yield archive


def extract_tar_files(archive, root, select=lambda name: True, prefix=''):
    """Regular files only; package scripts/symlinks/devices are never executed."""
    rows, total = [], 0
    for member in archive:
        name = member.name.removeprefix('./')
        # Validate even members not selected for this portable subset.
        destination(root, name)
        if not member.isfile() or not select(name):
            continue
        if prefix and not name.startswith(prefix):
            raise ValueError('Archive member prefix mismatch')
        relative = name[len(prefix):] if prefix else name
        target = destination(root, relative)
        total += member.size
        if total > 1536 * 1024 * 1024:
            raise ValueError('Selected archive data exceeds extraction budget')
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(member) as source, target.open('wb') as output:
            shutil.copyfileobj(source, output)
        if target.stat().st_size != member.size:
            raise ValueError('Archive member was truncated')
        rows.append({'path': relative, 'sha256': sha256(target), 'bytes': member.size})
    return rows


def extract_zip_files(path, root):
    rows, total = [], 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = member.filename
            if name.endswith('/'):
                destination(root, name.rstrip('/'))
                continue
            target = destination(root, name)
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ZIP symlinks are not accepted')
            total += member.file_size
            if total > 1024 * 1024 * 1024:
                raise ValueError('ZIP exceeds extraction budget')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            rows.append({'path': name, 'sha256': sha256(target)})
    return rows


def save_report(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
