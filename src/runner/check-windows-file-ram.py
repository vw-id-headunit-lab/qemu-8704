
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'src/emu'))
import original_8704_vm as vm
from ufs_platform import QTest


BANKS = (
    ('loader', 0xc00000000, 0x40000000),
    ('vm3', 0xa80000000, 0x80000000),
    ('vm2', 0x880000000, 0x4e000000),
)


def test(qemu, directory):
    if not directory.resolve().is_relative_to(vm.ROOT/'var'):
        raise ValueError('Temporary RAM must stay inside project scratch')
    directory.mkdir()
    qmp_port, test_port = vm.free_port(), vm.free_port()
    args = [str(qemu), '-machine', 'virt,nvdimm=on,gic-version=2,virtualization=on',
            '-accel', 'tcg,thread=single,tb-size=256', '-cpu', 'cortex-a72',
            '-smp', '8', '-m', '3072M,slots=4,maxmem=64G', '-display', 'none',
            '-monitor', 'none', '-serial', 'none', '-nic', 'none', '-S',
            '-qmp', f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
            '-qtest', f'tcp:127.0.0.1:{test_port},server=on,wait=off']
    files = []
    for label, address, length in BANKS:
        file = directory/f'{label}.temporary-ram'
        files.append(file)
        args += ['-object', json.dumps({'qom-type':'memory-backend-file','id':label,
            'size':length,'share':True,'mem-path':str(file)}),
            '-device', f'nvdimm,memdev={label},addr={address:#x}']
    env = os.environ.copy()
    env['PATH'] = str(vm.ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
    with (directory/'qemu.log').open('xb') as log:
        process = subprocess.Popen(args, env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        qmp = qtest = None
        try:
            for _ in range(200):
                if process.poll() is not None:
                    raise RuntimeError(f'QEMU exited {process.returncode}')
                try:
                    qmp = vm.QMP(qmp_port)
                    break
                except OSError:
                    time.sleep(.05)
            if qmp is None:
                raise TimeoutError('QMP did not start')
            qtest = QTest(test_port)
            if qmp.execute('query-status')['running']:
                raise ValueError('Expected an inert board')
            checked = []
            for index, (label, start, length) in enumerate(BANKS):
                for address in (start, start+length-4):
                    pattern = 0x70370400+index
                    if qtest.read(address) != 0:
                        raise ValueError(f'Initial RAM is not zero: {address:#x}')
                    qtest.write(address, pattern)
                    if qtest.read(address) != pattern:
                        raise ValueError(f'RAM roundtrip failed: {address:#x}')
                checked.append(label)
            memory = qmp.execute('query-memory-size-summary')
            used = int(subprocess.check_output(
                ['pwsh','-NoProfile','-Command',f'(Get-Process -Id {process.pid}).PrivateMemorySize64'],
                creationflags=subprocess.CREATE_NO_WINDOW,timeout=10))
            if memory['base-memory']!=3<<30 or memory['plugged-memory']!=sum(n for _,_,n in BANKS):
                raise ValueError('The original RAM sizes were not retained')
            if used >= 5 << 30:
                raise ValueError('The auxiliary banks are consuming anonymous private commit')
            qtest.close()
            qtest = None
            try:
                qmp.execute('quit')
            except OSError:
                pass
            if process.wait(timeout=12) != 0 or any(file.exists() for file in files):
                raise ValueError('QEMU exit or automatic RAM file cleanup failed')
                                                                
            files[0].write_bytes(b'owned-test-sentinel')
            try:
                blocked = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
                if blocked.returncode == 0 or b'Cannot create ephemeral Windows RAM' not in blocked.stderr:
                    raise ValueError('Existing RAM backing was not rejected at allocation')
                if files[0].read_bytes() != b'owned-test-sentinel':
                    raise ValueError('Existing file content was changed')
            finally:
                files[0].unlink()
            proof = dict(qemu_sha256=vm.sha(qemu), memory=memory, private_commit_bytes=used,
                         checked_ranges=checked, files_deleted_on_exit=True, existing_path_preserved=True,
                         guest_executed=False)
            vm.save(directory/'result.json',proof)
            print(json.dumps(proof))
        finally:
            if qtest:
                qtest.close()
            if qmp:
                qmp.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu',type=Path)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    test(args.qemu.resolve(strict=True),args.directory.resolve())
