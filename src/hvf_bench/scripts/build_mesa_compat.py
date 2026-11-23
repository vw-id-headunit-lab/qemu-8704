"""Build a target AArch64 loader bridge and package real Unix aliases."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'runtime/mesa'
ANALYSIS = ROOT / 'reports/mesa'
BASE.mkdir(parents=True, exist_ok=True)
ANALYSIS.mkdir(parents=True, exist_ok=True)
RUNTIME = BASE / 'rootfs'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    inventory_path = ANALYSIS / 'runtime-inventory.json'
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    libs = RUNTIME / 'usr/lib/aarch64-linux-gnu'
    output = libs / 'libmali.so.0'
    commands = []

    def run(argv):
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace')
        commands.append({'argv': argv, 'exit_status': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result

    run(['clang', '--target=aarch64-linux-gnu', '-fPIC', '-fvisibility=hidden', '-c', str(ROOT / 'native/mali-compat.c'), '-o', str(BASE / 'mali-compat.o')])
    linker = shutil.which('ld.lld') or str(Path(shutil.which('clang')).parent / ('ld.lld.exe' if os.name == 'nt' else 'ld.lld'))
    run([str(linker), '-m', 'aarch64elf', '-shared', '--hash-style=both', '--soname=libmali.so.0',
         '--no-as-needed', '--allow-shlib-undefined', '-rpath', '$ORIGIN',
         str(BASE / 'mali-compat.o'), str(libs / 'libEGL.so.1.1.0'),
         str(libs / 'libGLESv2.so.2.1.0'), str(libs / 'libwayland-egl.so.1.0.0'), '-o', str(output)])
    result = run(['llvm-objdump', '-p', str(output)])
    (ANALYSIS / 'mali-compat.elf.txt').write_text(result.stdout, encoding='utf-8')
    expected = ['libEGL.so.1', 'libGLESv2.so.2', 'libwayland-egl.so.1']
    needed = [line.strip().split(None, 1)[1] for line in result.stdout.splitlines() if line.strip().startswith('NEEDED')]
    for name in expected:
        if name not in needed:
            raise ValueError('Required real implementation dependency missing: ' + name)
    run(['llvm-readobj', '--file-headers', str(output)])
    # Regular libraries are preserved once; POSIX aliases live in the guest tar metadata.
    bridge_path = 'usr/lib/aarch64-linux-gnu/libmali.so.0'
    inventory['members'] = [x for x in inventory['members'] if x['path'] != bridge_path]
    inventory['members'].append({'path': bridge_path, 'package': 'local-loader-bridge',
                                 'size': output.stat().st_size, 'sha256': sha(output), 'original_tar_type': '0'})
    for name in ['libmali.so', 'libEGL_adreno.so', 'libGLESv2_adreno.so', 'libwaylandegl.so.1']:
        path = 'usr/lib/aarch64-linux-gnu/' + name
        inventory['links'] = [x for x in inventory['links'] if x['path'] != path]
        inventory['links'].append({'path': path, 'target': 'libmali.so.0', 'package': 'local-loader-bridge', 'type': 'symlink'})
    archive_path = BASE / 'mesa-runtime.tar.gz'
    with tarfile.open(archive_path, 'w:gz', compresslevel=3) as archive:
        seen = set()
        for member in inventory['members']:
            if member['path'] in seen:
                continue
            archive.add(RUNTIME / member['path'], arcname=member['path'], recursive=False)
            seen.add(member['path'])
        for link in inventory['links']:
            if link['path'] in seen:
                continue
            item = tarfile.TarInfo(link['path'])
            item.type, item.linkname, item.mode = tarfile.SYMTYPE, link['target'], 0o777
            archive.addfile(item)
            seen.add(link['path'])
    inventory['runtime_archive'] = {'path': str(archive_path), 'size': archive_path.stat().st_size, 'sha256': sha(archive_path)}
    inventory['actual_tree_bytes'] = sum(p.stat().st_size for p in BASE.rglob('*') if p.is_file())
    inventory['compatibility_bridge'] = {'path': str(output), 'sha256': sha(output), 'real_needed': expected,
                                        'implemented_graphics_functions': [], 'tested_in_guest': False}
    inventory_path.write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    (ANALYSIS / 'compat-build.json').write_text(json.dumps({'commands': commands,
                   'bridge': inventory['compatibility_bridge'], 'runtime_archive': inventory['runtime_archive']}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'bridge': inventory['compatibility_bridge'], 'runtime_archive': inventory['runtime_archive'],
                      'actual_tree_bytes': inventory['actual_tree_bytes']}, indent=2))


if __name__ == '__main__':
    main()
