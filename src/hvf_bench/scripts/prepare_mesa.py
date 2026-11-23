"""Download and unpack a bounded Debian Buster ARM64 software-GL runtime."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import io
import json
import lzma
import re
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'runtime/mesa'
ANALYSIS = ROOT / 'reports/mesa'
BASE.mkdir(parents=True, exist_ok=True)
ANALYSIS.mkdir(parents=True, exist_ok=True)
ARCHIVE = 'https://archive.debian.org/debian/'
ROOT_PACKAGES = ['libegl1', 'libegl-mesa0', 'libgles2', 'libglapi-mesa', 'libgl1-mesa-dri', 'libllvm7', 'libwayland-egl1']
FIRMWARE_CORE = {'libc6', 'libgcc1', 'libstdc++6'}
LIMIT = 700 * 1024 * 1024


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, destination, expected=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or expected and sha(destination) != expected:
        temporary = destination.with_suffix(destination.suffix + '.partial')
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        temporary.replace(destination)
    actual = sha(destination)
    if expected and actual != expected:
        raise ValueError('Package digest mismatch: ' + destination.name)
    return {'url': url, 'path': str(destination), 'size': destination.stat().st_size, 'sha256': actual}


def package_index(path):
    packages = {}
    with lzma.open(path, 'rt', encoding='utf-8') as stream:
        fields = {}
        key = None
        for line in stream:
            if line == '\n':
                if fields.get('Architecture') in ('arm64', 'all'):
                    packages[fields['Package']] = fields
                fields, key = {}, None
            elif line.startswith(' '):
                if key:
                    fields[key] += '\n' + line.rstrip('\n')
            else:
                key, _, value = line.rstrip('\n').partition(': ')
                fields[key] = value
    return packages


def dependencies(value):
    for group in value.split(','):
        options = []
        for choice in group.split('|'):
            match = re.match(r'\s*([a-z0-9][a-z0-9+.-]*)', choice)
            if match:
                options.append(match.group(1))
        if options:
            yield options


def data_tar(path):
    data = path.read_bytes()
    if data[:8] != b'!<arch>\n':
        raise ValueError('Invalid Debian ar archive')
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset:offset + 60]
        name = header[:16].decode('ascii').strip().rstrip('/')
        size = int(header[48:58])
        payload = data[offset + 60:offset + 60 + size]
        if name.startswith('data.tar'):
            return tarfile.open(fileobj=io.BytesIO(payload), mode='r:*')
        offset += 60 + size + size % 2
    raise ValueError('Debian data archive missing')


def wanted(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Unexpected archive path: ' + name)
    if '/dri/' in name:
        return path.name in ('swrast_dri.so', 'kms_swrast_dri.so')
    return '.so' in path.name and name.startswith(('lib/', 'usr/lib/')) or name.startswith('usr/share/glvnd/egl_vendor.d/') or name.endswith('/copyright')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata-only', action='store_true')
    parser.add_argument('--locked', action='store_true', help='Use checked-in exact Debian package versions/SHA-256 pins')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--seed-cache', type=Path)
    args = parser.parse_args()
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    locked_downloader = None
    if args.locked:
        from dependency_utils import Downloader
        locked = json.loads((ROOT / 'scripts/dependencies/mesa-buster-arm64.lock.json').read_text(encoding='utf-8'))
        selected = locked['packages']
        skipped = set(FIRMWARE_CORE)
        source = release = {'size': 0, 'verification': 'Checked-in package lock'}
        locked_downloader = Downloader(ROOT / 'runtime/cache', args.offline, args.seed_cache)
    else:
        if args.offline:
            parser.error('--offline requires --locked')
        index = BASE / 'metadata/Packages.xz'
        source = download(ARCHIVE + 'dists/buster/main/binary-arm64/Packages.xz', index)
        release = download(ARCHIVE + 'dists/buster/Release', BASE / 'metadata/Release')
        entries = package_index(index)
        release_text = Path(release['path']).read_text(encoding='utf-8')
        checksum_line = next(line.strip().split() for line in release_text.splitlines()
                             if line.strip().endswith('main/binary-arm64/Packages.xz') and len(line.strip().split()[0]) == 64)
        if source['sha256'] != checksum_line[0] or source['size'] != int(checksum_line[1]):
            raise ValueError('Packages index differs from Release SHA256')
        selected, queue, skipped = {}, list(ROOT_PACKAGES), set()
        while queue:
            name = queue.pop(0)
            if name in FIRMWARE_CORE:
                skipped.add(name)
                continue
            if name in selected:
                continue
            if name not in entries:
                raise ValueError('Package unavailable: ' + name)
            entry = entries[name]
            selected[name] = entry
            for options in dependencies(entry.get('Pre-Depends', '') + ',' + entry.get('Depends', '')):
                candidate = next((option for option in options if option in entries), None)
                if candidate is None:
                    raise ValueError('Unresolved dependency choices: ' + str(options))
                queue.append(candidate)
    compressed = sum(int(p['Size']) for p in selected.values())
    installed = sum(int(p.get('Installed-Size', '0')) * 1024 for p in selected.values())
    budget = {'download_bytes': compressed, 'full_packages_installed_upper_bytes': installed,
              'metadata_bytes': source['size'] + release['size'],
              'conservative_without_runtime_archive_bytes': compressed + installed + source['size'] + release['size'],
              'limit_bytes': LIMIT}
    if budget['conservative_without_runtime_archive_bytes'] > LIMIT:
        raise ValueError('Metadata budget exceeds requested limit: ' + str(budget))
    plan = {'suite': 'buster', 'architecture': 'arm64', 'archive': ARCHIVE,
            'sources': [source, release], 'release_index_sha256_match': not args.locked,
            'pinned_package_lock': args.locked,
            'roots': ROOT_PACKAGES, 'firmware_core_dependencies': sorted(skipped),
            'budget': budget, 'packages': selected,
            'scope': 'runtime shared libraries plus software DRI drivers and EGL vendor configuration; no package installation'}
    (ANALYSIS / 'package-plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'package_count': len(selected), 'packages': sorted(selected), 'budget': budget}), flush=True)
    if args.metadata_only:
        return
    members, links, downloads = [], [], []
    runtime_root = BASE / 'rootfs'
    for name, entry in sorted(selected.items()):
        if locked_downloader:
            package = locked_downloader.obtain(Path(entry['Filename']).name,
                {'url': ARCHIVE + entry['Filename'], 'sha256': entry['SHA256'], 'size': int(entry['Size'])})
            record = {'url': ARCHIVE + entry['Filename'], 'path': str(package),
                      'sha256': sha(package), 'size': package.stat().st_size}
        else:
            package = BASE / 'packages' / Path(entry['Filename']).name
            record = download(ARCHIVE + entry['Filename'], package, entry['SHA256'])
        record.update({'package': name, 'version': entry['Version'], 'architecture': entry['Architecture']})
        downloads.append(record)
        with data_tar(package) as archive:
            for member in archive.getmembers():
                path = member.name.removeprefix('./')
                if not wanted(path):
                    continue
                destination = (runtime_root / path).resolve()
                if runtime_root.resolve() not in destination.parents:
                    raise ValueError('Extraction path leaves runtime root')
                if member.issym():
                    links.append({'path': path, 'target': member.linkname, 'package': name, 'type': 'symlink'})
                elif member.isfile() or member.islnk():
                    content = archive.extractfile(member).read()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                    members.append({'path': path, 'package': name, 'size': len(content),
                                    'sha256': hashlib.sha256(content).hexdigest(), 'original_tar_type': member.type.decode('ascii', errors='replace')})
        print(name, entry['Version'], record['size'], flush=True)
    report = {'plan': str(ANALYSIS / 'package-plan.json'), 'downloads': downloads, 'members': members, 'links': links}
    (ANALYSIS / 'runtime-inventory.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    # Recreate Unix symlink metadata in an ordinary tar for the guest; host files stay regular.
    tar_path = BASE / 'mesa-runtime.tar.gz'
    with tarfile.open(tar_path, 'w:gz', compresslevel=3) as archive:
        seen = set()
        for member in members:
            if member['path'] in seen:
                continue
            archive.add(runtime_root / member['path'], arcname=member['path'], recursive=False)
            seen.add(member['path'])
        for link in links:
            if link['path'] in seen:
                continue
            info = tarfile.TarInfo(link['path'])
            info.type, info.linkname, info.mode = tarfile.SYMTYPE, link['target'], 0o777
            archive.addfile(info)
            seen.add(link['path'])
    actual = sum(p.stat().st_size for p in BASE.rglob('*') if p.is_file())
    if actual > LIMIT:
        raise ValueError('Actual disk budget exceeded: ' + str(actual))
    report['runtime_archive'] = {'path': str(tar_path), 'size': tar_path.stat().st_size, 'sha256': sha(tar_path)}
    report['actual_tree_bytes'] = actual
    (ANALYSIS / 'runtime-inventory.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'runtime_archive': report['runtime_archive'], 'actual_tree_bytes': actual}), flush=True)


if __name__ == '__main__':
    main()
