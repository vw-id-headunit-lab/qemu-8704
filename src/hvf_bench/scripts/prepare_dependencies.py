"""One-command portable open-source dependency preparation for Windows x64."""
import argparse
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import venv

from dependency_utils import (Downloader, extract_tar_files, extract_zip_files,
                              save_report, sha256)

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / 'scripts/dependencies/windows-x64.lock.json'


def config_paths(root):
    return {
        'qemu': 'runtime/msys2/ucrt64/bin/qemu-system-aarch64.exe',
        'qemu_data': 'runtime/msys2/ucrt64/share/qemu',
        'kernel': 'runtime/alpine/vmlinuz-virt', 'initramfs': 'runtime/alpine/initramfs-virt',
        'modules': 'runtime/alpine/modloop-virt',
        'firmware_root': 'local/firmware/root.ext4', 'firmware_hmi': 'local/firmware/hmi.ext4.sparse',
        'hmi_jar': 'local/compile/hmi.jar', 'osgi_jar': 'local/compile/osgi.jar',
        'adapter_jar': 'build/bench-platform.jar', 'mesa_archive': 'runtime/mesa/mesa-runtime.tar.gz',
        'egl_bridge': 'build/libegl-forward-bridge.so', 'novnc': 'runtime/novnc', 'output': 'output',
    }


def prepare_config(root, config_file):
    root = Path(root).resolve()
    config_file = Path(config_file).resolve()
    if config_file.exists():
        return {'path': str(config_file), 'preserved': True, 'sha256': sha256(config_file)}
    if config_file.parent != root:
        raise ValueError('A new local configuration must be created in the project directory')
    save_report(config_file, config_paths(root))
    return {'path': str(config_file), 'preserved': False, 'sha256': sha256(config_file)}


def prepare_boot(root, lock, downloader):
    rows = []
    target = root / 'runtime/alpine'
    target.mkdir(parents=True, exist_ok=True)
    for name, spec in lock['alpine']['assets'].items():
        path = downloader.obtain(name, spec)
        shutil.copyfile(path, target / name)
        rows.append({'name': name, 'sha256': sha256(target / name)})
    save_report(root / 'reports/boot-download.json', {'files': rows, 'pinned_branch': lock['alpine']['branch'],
        'verification': 'Checked-in SHA-256 pins; fail closed if the netboot URL changes. No signature verification claim.'})


def prepare_novnc(root, spec, downloader):
    package = downloader.obtain('novnc-' + spec['version'] + '.tgz', spec)
    with tarfile.open(package, mode='r:gz') as archive:
        rows = extract_tar_files(archive, root / 'runtime/novnc', prefix='package/')
    if not (root / 'runtime/novnc/core/rfb.js').is_file():
        raise ValueError('noVNC rfb.js missing after extraction')
    save_report(root / 'reports/novnc-download.json', {'version': spec['version'], 'files': rows,
        'verification': 'Pinned official npm tarball SHA-256', 'install_scripts_executed': False})


def prepare_jdk(root, spec, downloader):
    archive = downloader.obtain(spec['filename'], spec)
    target = root / 'runtime/jdk' / spec['filename'].removesuffix('.zip')
    extract_zip_files(archive, target)
    candidates = list(target.glob('*/bin/javac.exe'))
    if len(candidates) != 1:
        raise RuntimeError('Expected one portable JDK directory')
    return candidates[0].parent


def run_setup(args, lock):
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('This setup entry currently targets Windows x64')
    root = ROOT
    lock_file = root / 'runtime/dependency-setup.lock'
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_handle = lock_file.open('x', encoding='utf-8')
    except FileExistsError as error:
        raise RuntimeError('Dependency setup is already locked; confirm no setup is running before removing a stale runtime/dependency-setup.lock') from error
    lock_handle.write(str(os.getpid()))
    lock_handle.close()
    downloader = Downloader(root / 'runtime/cache', args.offline, args.seed_cache)
    commands = []
    report = {'status': 'running', 'lock_sha256': sha256(LOCK_PATH),
              'firmware_downloaded': False, 'system_installation_performed': False, 'commands': commands}
    config = Path(args.config).resolve()
    prepared = None
    def execute(argv, env=None):
        print('Running:', ' '.join(map(str, argv)), flush=True)
        result = subprocess.run(list(map(str, argv)), cwd=root, env=env, capture_output=True)
        row = {'argv': list(map(str, argv)), 'exit_status': result.returncode,
               'stdout': result.stdout.decode(errors='replace'), 'stderr': result.stderr.decode(errors='replace')}
        commands.append(row)
        if result.returncode:
            raise RuntimeError(row['stdout'] + row['stderr'])
        if row['stdout']:
            print(row['stdout'][-2500:], flush=True)
    try:
        print('[1/7] Preparing portable QEMU and its pinned runtime libraries', flush=True)
        from prepare_qemu import prepare
        need_llvm = not all(shutil.which(tool) for tool in ('clang', 'ld.lld', 'llvm-objdump', 'llvm-readobj'))
        tools = prepare(root, llvm=need_llvm or args.portable_tools, downloader=downloader)
        print('[2/7] Preparing matching pinned Alpine ARM64 boot assets', flush=True)
        prepare_boot(root, lock, downloader)
        print('[3/7] Preparing noVNC (no npm scripts)', flush=True)
        prepare_novnc(root, lock['novnc'], downloader)
        print('[4/7] Preparing Java build tools', flush=True)
        need_jdk = not all(shutil.which(tool) for tool in ('java', 'javac', 'jar'))
        if not need_jdk:
            probe = subprocess.run(['javac', '-version'], capture_output=True, timeout=20)
            version_text = (probe.stdout + probe.stderr).decode(errors='replace')
            major = re.search(r'javac\s+(\d+)', version_text)
            need_jdk = probe.returncode != 0 or major is None or int(major[1]) < 17
        jdk = None
        if need_jdk or args.portable_tools:
            try:
                jdk = prepare_jdk(root, lock['jdk'], downloader)
                report['jdk_distribution'] = 'Eclipse Temurin'
            except (OSError, ValueError, RuntimeError) as primary_error:
                fallback = lock.get('jdk_fallback')
                if fallback is None:
                    raise
                print('Primary JDK download unavailable; trying the separately pinned official Azul build.', flush=True)
                jdk = prepare_jdk(root, fallback, downloader)
                report['jdk_distribution'] = 'Azul Zulu (pinned fallback)'
                report['primary_jdk_error'] = str(primary_error)
        env = os.environ.copy()
        search = [str(tools / 'bin')]
        if jdk:
            search.append(str(jdk))
        env['PATH'] = os.pathsep.join(search + [env.get('PATH', '')])
        print('[5/7] Preparing pinned Mesa ARM64 packages and the native bridges', flush=True)
        # The helper's lock mode uses the same verified cache and never installs .deb packages.
        mesa_args = [sys.executable, root / 'scripts/prepare_mesa.py', '--locked']
        if args.offline:
            mesa_args.append('--offline')
        if args.seed_cache:
            mesa_args.extend(['--seed-cache', args.seed_cache])
        execute(mesa_args, env)
        execute([sys.executable, root / 'scripts/build_mesa_compat.py'], env)
        execute([sys.executable, root / 'scripts/build_native.py'], env)
        print('[6/7] Preparing local configuration (existing files are preserved)', flush=True)
        prepared = prepare_config(root, config)
        report['configuration'] = prepared
        values = json.loads(config.read_text(encoding='utf-8-sig'))
        def local_path(key):
            path = Path(values[key])
            return path if path.is_absolute() else config.parent / path
        if local_path('hmi_jar').is_file() and local_path('osgi_jar').is_file():
            execute([sys.executable, root / 'scripts/build_platform.py', '--config', config], env)
            report['adapter_built'] = True
        else:
            report['adapter_built'] = False
            print('Public dependencies are ready. Supply the two local compile JARs, then rerun setup to build the adapter.', flush=True)
        execute([str(tools / 'bin/qemu-system-aarch64.exe'), '--version'], env)
        print('[7/7] Checking the configured ARM64 baseline prerequisites', flush=True)
        execute([sys.executable, root / 'run.py', 'doctor', '--config', config, '--mode', 'baseline'], env)
        if report['adapter_built'] and local_path('firmware_root').is_file() and local_path('firmware_hmi').is_file():
            execute([sys.executable, root / 'run.py', 'doctor', '--config', config, '--mode', 'hmi'], env)
            report['hmi_prerequisites_ready'] = True
        else:
            report['hmi_prerequisites_ready'] = False
        report['status'] = 'complete'
        print('Dependency setup complete. Firmware is not included. Start with Run-HMI.cmd after supplying the documented local inputs.', flush=True)
    except BaseException as error:
        report.update(status='error', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        report['downloads'] = downloader.records
        save_report(root / 'reports/dependency-setup.json', report)
        lock_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.local.json')
    parser.add_argument('--offline', action='store_true', help='Require already cached verified public packages')
    parser.add_argument('--seed-cache', type=Path, help='Optional existing public-package cache; every byte is rehashed')
    parser.add_argument('--portable-tools', action='store_true', help='Fetch the pinned LLVM and JDK even if build tools already exist on PATH')
    parser.add_argument('--plan', action='store_true', help='Show pinned sources without downloading or running tools')
    parser.add_argument('--in-venv', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    lock = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
    if args.plan:
        print(json.dumps({'platform': lock['platform'], 'qemu': lock['qemu_version'], 'alpine': lock['alpine']['branch'],
            'novnc': lock['novnc']['version'], 'jdk': lock['jdk']['version'],
            'python_requirements': lock['python_requirements'], 'firmware_downloaded': False,
            'destinations': ['.venv/', 'runtime/', 'build/', 'reports/'], 'system_installation_performed': False}, indent=2))
        return 0
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required to bootstrap this project')
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64', 'x86_64'):
        parser.error('One-command setup currently targets Windows x64; --plan is available on other hosts')
    if not args.in_venv:
        python = ROOT / '.venv/Scripts/python.exe'
        if not python.exists():
            print('Creating project-local Python environment', flush=True)
            venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
        install = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:',
                   '--index-url', 'https://pypi.org/simple', *lock['python_requirements']]
        if args.offline:
            # Offline mode reuses the existing prepared venv; it never invokes pip's network resolver.
            probe = subprocess.run([str(python), '-c', 'import aiohttp,zstandard'], capture_output=True)
            if probe.returncode:
                parser.error('Offline setup requires an existing .venv with aiohttp and zstandard')
        else:
            subprocess.run(install, check=True, cwd=ROOT)
        command = [str(python), str(Path(__file__).resolve()), *sys.argv[1:], '--in-venv']
        return subprocess.call(command, cwd=ROOT)
    if importlib.util.find_spec('zstandard') is None:
        parser.error('zstandard is missing from the setup environment')
    for requirement in lock['python_requirements']:
        name, expected = requirement.split('==', 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            parser.error('The setup environment is missing the pinned dependency: ' + requirement)
        if actual != expected:
            parser.error('The setup environment differs from the dependency lock: ' + requirement + ' (installed ' + actual + ')')
    try:
        run_setup(args, lock)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Setup stopped:', error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
