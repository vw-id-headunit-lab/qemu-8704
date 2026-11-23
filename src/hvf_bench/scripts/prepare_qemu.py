"""Assemble pinned portable MSYS2 QEMU (and optional LLVM) without an installer."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import platform
import shutil
import subprocess

from dependency_utils import Downloader, destination, extract_tar_files, save_report, sha256, zstd_tar

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'scripts/dependencies/windows-x64.lock.json'
EXES = {'qemu-system-aarch64.exe', 'clang.exe', 'clang++.exe', 'clang-23.exe', 'ld.lld.exe',
        'lld.exe', 'llvm-readobj.exe', 'llvm-objdump.exe'}


def selected(name):
    if name.startswith('ucrt64/bin/'):
        base = Path(name).name.lower()
        return base in EXES or base.endswith('.dll')
    return name.startswith(('ucrt64/share/qemu/', 'ucrt64/share/licenses/', 'ucrt64/lib/qemu/'))


def prepare(root=ROOT, *, llvm=False, downloader=None):
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Portable QEMU preparation currently targets Windows x64')
    root = Path(root).resolve()
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    downloader = downloader or Downloader(root / 'runtime/cache')
    names = set(lock['qemu_packages'])
    if llvm:
        names.update(lock['llvm_packages'])
    specs = [(name, lock['msys2_packages'][name]) for name in sorted(names)]
    def get(item):
        name, spec = item
        return name, downloader.obtain(spec['filename'], spec)
    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(pool.map(get, specs))
    tree = root / 'runtime/msys2'
    files = []
    for name, package in archives:
        aliases = []
        with zstd_tar(package) as archive:
            # Tar hardlinks cannot be followed through a streaming reader. Only
            # reviewed executable aliases are copied from extracted regular files.
            # Inspect the headers once, then stream regular selected files.
            # Package hashes are checked before either pass.
            for member in archive:
                if member.islnk() and selected(member.name):
                    aliases.append((member.name, member.linkname))
        with zstd_tar(package) as archive:
            rows = extract_tar_files(archive, tree, selected)
        for alias, link in aliases:
            if not selected(link) or not link.startswith('ucrt64/bin/'):
                raise ValueError('Unexpected selected MSYS2 hardlink target')
            source, target = destination(tree, link), destination(tree, alias)
            if not source.is_file() or source.is_symlink():
                raise ValueError('MSYS2 executable alias target is missing')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            rows.append({'path': alias, 'sha256': sha256(target), 'copied_hardlink': link})
        files.extend(rows)
        print('Prepared:', name, len(rows), 'portable files', flush=True)
    probes = []
    binaries = ['qemu-system-aarch64.exe'] + (['clang.exe', 'ld.lld.exe', 'llvm-readobj.exe', 'llvm-objdump.exe'] if llvm else [])
    for binary in binaries:
        path = tree / 'ucrt64/bin' / binary
        result = subprocess.run([str(path), '--version'], capture_output=True, timeout=30)
        row = {'binary': binary, 'exit_status': result.returncode, 'stdout': result.stdout.decode(errors='replace'),
               'stderr': result.stderr.decode(errors='replace')}
        probes.append(row)
        if result.returncode:
            save_report(root / 'reports/msys2-setup.json', {'files': files, 'probes': probes, 'passed': False})
            raise RuntimeError('Portable binary failed its version probe: ' + binary + ' ' + row['stderr'])
    if not (tree / 'ucrt64/share/qemu/keymaps/en-us').is_file():
        raise RuntimeError('QEMU keymap en-us is missing')
    report = {'provider': 'Official MSYS2 UCRT64 packages listed in a checked-in SHA-256 lock',
              'version': lock['qemu_version'], 'system_installation_performed': False,
              'files': files, 'probes': probes, 'passed': True}
    save_report(root / 'reports/msys2-setup.json', report)
    return tree / 'ucrt64'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-llvm', action='store_true')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--seed-cache', type=Path)
    args = parser.parse_args()
    prepare(llvm=args.with_llvm, downloader=Downloader(ROOT / 'runtime/cache', args.offline, args.seed_cache))


if __name__ == '__main__':
    main()
