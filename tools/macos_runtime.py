"""Darwin build and firmware-free carrier checks; never rewrites OEM inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.local/build/darwin'
SOURCE = BUILD / 'qemu-11.1.0'
NATIVE = BUILD / 'native'
RUNTIME = ROOT / 'runtime/darwin'
LOGS = ROOT / '.local/mac-port/logs'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def command(args, cwd=ROOT):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def build(jobs):
    if sys.platform != 'darwin':
        raise ValueError('Darwin build requires macOS')
    if not SOURCE.exists():
        command([sys.executable, ROOT/'tools/native_sources.py', '--build-root', BUILD])
    NATIVE.mkdir(parents=True, exist_ok=True)
    configure = [str(SOURCE/'configure'), '--target-list=aarch64-softmmu',
                 '--disable-werror', '--disable-docs', '--enable-cocoa',
                 '--disable-gtk', '--disable-sdl', '--disable-opengl',
                 '--disable-guest-agent', '--disable-slirp', '--disable-capstone',
                 '--disable-pvg',
                 '--enable-fdt=system', '--extra-cflags=-I/opt/homebrew/include',
                 '--extra-ldflags=-L/opt/homebrew/lib',
                 '--python='+sys.executable, '--prefix='+str(RUNTIME)]
    signature = NATIVE/'meb-configure.json'
    if (not (NATIVE/'build.ninja').exists() or not signature.exists()
            or json.loads(signature.read_text()) != configure):
        command(configure, cwd=NATIVE)
        signature.write_text(json.dumps(configure, indent=2)+'\n')
    command(['ninja', '-j', str(jobs), 'qemu-system-aarch64', 'qemu-img'], cwd=NATIVE)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name in ('qemu-system-aarch64', 'qemu-img'):
        shutil.copy2(NATIVE/name, RUNTIME/name)
        outputs.append({'path': str(RUNTIME/name), 'sha256': sha(RUNTIME/name)})
    report = {'host': sys.platform, 'upstream_sha256': sha(ROOT/'native/upstream/qemu-11.1.0.tar.xz'),
              'overrides': {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT/'native/overrides').rglob('*')) if p.is_file()},
              'binaries': outputs, 'firmware_booted': False}
    (RUNTIME/'build.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def smoke(display):
    binary = RUNTIME/'qemu-system-aarch64'
    with tempfile.TemporaryDirectory(prefix='meb-mac-smoke-') as folder:
        qmp_path = Path(folder)/'qmp.sock'
        args = [str(binary), '-machine', 'virt', '-cpu', 'cortex-a57', '-m', '256M',
                '-accel', 'tcg', '-nodefaults', '-display', display, '-S',
                '-monitor', 'none', '-serial', 'none', '-qmp', 'unix:'+str(qmp_path)+',server=on,wait=off']
        import socket
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        transcript = []
        try:
            deadline = time.monotonic()+20
            with socket.socket(socket.AF_UNIX) as sock:
                while True:
                    try:
                        sock.connect(str(qmp_path)); break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if process.poll() is not None or time.monotonic()>deadline:
                            raise RuntimeError('QEMU did not open QMP: '+process.communicate(timeout=2)[1].decode(errors='replace'))
                        time.sleep(.05)
                sock.settimeout(15)
                stream = sock.makefile('rwb', buffering=0)
                greeting = json.loads(stream.readline()); transcript.append({'greeting': greeting})
                for ident, execute in enumerate(('qmp_capabilities', 'query-status', 'query-machines', 'cont', 'query-status', 'stop', 'query-status', 'quit')):
                    request = {'execute': execute, 'id': ident}
                    stream.write((json.dumps(request)+'\n').encode())
                    while True:
                        reply = json.loads(stream.readline()); transcript.append(reply)
                        if reply.get('id')==ident: break
                    if 'error' in reply: raise RuntimeError(str(reply['error']))
                    if execute=='query-status':
                        expected = {1: 'prelaunch', 4: 'running', 6: 'paused'}[ident]
                        if reply['return']['status'] != expected:
                            raise RuntimeError('Unexpected state: '+str(reply))
                stream.close()
            stdout, stderr = process.communicate(timeout=15)
            if process.returncode: raise RuntimeError(stderr.decode(errors='replace'))
        finally:
            if process.poll() is None: process.kill(); process.wait()
        image = Path(folder)/'smoke.qcow2'
        image_checks = []
        for arguments in (['create', '-f', 'qcow2', str(image), '16M'], ['info', '--output=json', str(image)], ['check', '--output=json', str(image)]):
            proc = subprocess.run([str(RUNTIME/'qemu-img'), *arguments], capture_output=True, text=True, timeout=15)
            image_checks.append({'argv': proc.args, 'stdout': proc.stdout, 'stderr': proc.stderr, 'exit': proc.returncode})
            if proc.returncode: raise RuntimeError(str(image_checks[-1]))
        result = {'argv': args, 'qmp': transcript, 'stdout': stdout.decode(errors='replace'),
                  'stderr': stderr.decode(errors='replace'), 'exit': process.returncode,
                  'images': image_checks, 'display': display, 'firmware_booted': False}
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS/('smoke-'+display+'.json')).write_text(json.dumps(result, indent=2)+'\n')
        return {'passed': True, 'display': display, 'transitions': ['prelaunch', 'running', 'paused', 'quit'],
                'image_checks': len(image_checks), 'firmware_booted': False, 'report': str(LOGS/('smoke-'+display+'.json'))}


def verify_sources():
    manifest = json.loads((ROOT/'config/release-manifest.json').read_text())
    errors = [row['path'] for row in manifest['files']
              if not (ROOT/row['path']).is_file() or sha(ROOT/row['path']) != row['sha256']]
    result = {'original_release_files': len(manifest['files']), 'hash_errors': errors,
              'passed': not errors, 'mac_port_additions_not_frozen_into_windows_release': True}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 2


def board_check():
    output = ROOT/'var/sessions'/('mac-board-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    command([sys.executable, '-B', ROOT/'tools/rekit/rekit.py', '_execute',
             'vlm_live', '--', 'board-check', '--qemu', RUNTIME/'qemu-system-aarch64',
             '--output', output])
    result = json.loads((output/'result.json').read_text())
    return {'output': str(output), 'result': result, 'firmware_booted': False}


def probe():
    output = ROOT/'var/sessions'/('mac-vlm-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    command([sys.executable, '-B', ROOT/'tools/rekit/rekit.py', '_execute',
             'vlm_live', '--', 'probe', '--qemu', RUNTIME/'qemu-system-aarch64',
             '--output', output, '--seconds', '5', '--steps', '0', '--board-adapter',
             '--hcb', '--hcb-ram', '--map-pram', '--payload-sized-qnx',
             '--guest-dtbs', '--load-qnx', '--load-linux', '--load-android',
             '--linux-factory-bank', '--qnx-factory-bank', '--rtc-bootstrap',
             '--cpus', '8', '--loader-ram-mib', '1024', '--trace-mode', 'boot'])
    uart = (output/'exynos-uart.bin').read_bytes().decode('utf-8', 'backslashreplace')
    report = {'output': str(output),
              'scope': 'Original vLM plus three verified guest payloads; storage and HMI orchestration are not supplied',
              'uart_bytes': len((output/'exynos-uart.bin').read_bytes()),
              'configuration_error': 'CONFIGURATION ERROR' in uart,
              'el2_panic': 'EL2 PANIC' in uart,
              'guest_boot_verified': False,
              'integrity': json.loads((output/'integrity.json').read_text())}
    report['guest_entry_messages'] = [vmid for vmid in (2, 3, 4)
                                      if f'VM{vmid} jumping in the Guest' in uart]
    report['kernel_records'] = {}
    for guest in ('linux', 'android'):
        records = json.loads((output/(guest+'-boot-records.json')).read_text()).get('records', [])
        report['kernel_records'][guest] = {
            'count': len(records),
            'version_lines': [r['text'] for r in records if r['text'].startswith('Linux version ')],
            'last_record': records[-1]['text'] if records else None}
    (LOGS/'probe-summary.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['build', 'smoke', 'verify', 'board-check', 'probe'])
    parser.add_argument('--jobs', type=int, default=min(8, os.cpu_count() or 2))
    parser.add_argument('--display', choices=['none', 'cocoa'], default='none')
    args = parser.parse_args()
    try:
        if args.action == 'verify': return verify_sources()
        if args.action == 'build': result = build(args.jobs)
        elif args.action == 'board-check': result = board_check()
        elif args.action == 'probe': result = probe()
        else: result = smoke(args.display)
        print(json.dumps(result, indent=2))
        if args.action == 'probe' and (result['configuration_error'] or result['el2_panic']):
            return 2
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
