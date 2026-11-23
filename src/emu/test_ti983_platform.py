
import argparse
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest

import original_8704_vm as vm
import ti983_platform as ti


class Contract(unittest.TestCase):
    def test_factory_vm3_vlink_and_vm2_gpio(self):
        self.assertIsNone(ti.policy(False))
        cfg = ti.policy(True)
        self.assertEqual((cfg['source_vmid'], cfg['physical_owner'], cfg['address']), (3, 2, 12))
        self.assertEqual(cfg['gpio4_outputs'], {'pdb': 33, '1v8': 38, '1v1': 42})


def check_qemu(output, exe=None):
    build = vm.ROOT/'.local/build'
    exe = exe or build/'build-aarch64/qemu-system-aarch64.exe'
    qp, tp = vm.free_port(), vm.free_port()
    env = dict(os.environ, PATH=str(build/'msys64/mingw64/bin')+os.pathsep+os.environ.get('PATH', ''))
    args = [str(exe), '-machine', 'virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on',
            '-cpu', 'cortex-a57', '-accel', 'qtest', '-smp', '8', '-m', '128', '-display', 'none',
            '-serial', 'null', '-serial', 'null', '-monitor', 'none', '-nic', 'none', '-S',
            '-device', ti.OPTION, '-qmp', f'tcp:127.0.0.1:{qp},server=on,wait=off',
            '-qtest', f'tcp:127.0.0.1:{tp},server=on,wait=off', '-qtest-log', str(output/'qtest.log')]
    checks = []
    qmp = None
    with (output/'qemu.log').open('wb') as log:
        proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError('QEMU fixture exited; inspect qemu.log')
                try:
                    qmp = vm.QMP(qp)
                    break
                except OSError:
                    time.sleep(.1)
            if qmp is None:
                raise RuntimeError('QMP did not respond')
            with socket.create_connection(('127.0.0.1', tp), timeout=5) as sock, sock.makefile('rwb', buffering=0) as wire:
                def test(cmd):
                    wire.write((cmd+'\n').encode())
                    reply = wire.readline().decode().strip()
                    while reply.startswith('IRQ '):
                        reply = wire.readline().decode().strip()
                    if not reply.startswith('OK'):
                        raise ValueError(reply)
                    return reply

                def wr(a,v): test(f'writel {a:#x} {v:#x}')
                def rd(a): return int(test(f'readl {a:#x}').split()[1], 16)
                def step(n): test(f'clock_step {n}')
                def get(key): return qmp.execute('qom-get', {'path':ti.QOM,'property':key})
                def eq(name, actual, expected):
                    checks.append(dict(name=name, actual=actual, expected=expected, passed=actual==expected))
                    if actual != expected:
                        raise ValueError(name)

                b = 0x10950000
                def transfer(payload=None, *, stop=True, reset=True, address=12):
                    receive = payload is None
                    if reset: wr(b, 0x80000000)
                    wr(b, 8 | (0x40 if receive else 0x80))
                    wr(b+4, 0x80083); wr(b+8, 0xffffff); wr(b+0x40, 0); wr(b+0x48, 0)
                    wr(b+0x24, 0xffffffff); wr(b+0x20, 0); wr(b+0x70, address<<10)
                    if not receive:
                        for value in payload: wr(b+0x34, value)
                    length = 1 if receive else len(payload)
                    wr(b+0x44, 0x80000000 | (0x20000 if stop else 0) | (0x10000 if receive else 0) | length)
                    step((length+2)*22500)
                    status = rd(b+0x24)
                    return status, rd(b+0x38)&255 if receive and status == 0x80 else None

                def write(reg,val): eq(f'write {reg:#x}/{val:#x}',transfer([reg,val])[0],0x80)
                def read(reg, *, repeated=False):
                    eq(f'pointer {reg:#x}', transfer([reg], stop=not repeated)[0], 0x80)
                    status,value = transfer(reset=not repeated)
                    eq(f'receive {reg:#x}',status,0x80)
                    return value

                qmp.execute('cont'); wr(0x10821014,4)
                eq('unpowered address NACK',transfer([0xc])[0]==0x80,False)
                wr(0x10830080,0x01000010); wr(0x10830084,0x42)
                eq('two original rails are present',get('rail-mask'),3)
                wr(0x108300a0,0x100); wr(0x108300a4,4)
                eq('three GPIO-backed rails',get('rail-mask'),7)
                step(99999); eq('settling timer not early',get('powered'),0)
                step(1); eq('powered after actual timer',get('powered'),1)
                eq('no connected/trained link',read(0xc,repeated=True),0)
                write(0x40,0x31); write(0x41,0x31)
                eq('original indirect diagnostics',read(0x42),0)
                write(0x49,0); write(0x4a,5); write(0x48,3)
                for reg in range(0x4b,0x4f): eq(f'APB quiescent byte {reg:#x}',read(reg,repeated=True),0)
                eq('one issued APB read',get('apb-requests'),1)
                write(0x49,0xff)
                eq('unknown APB command rejected',transfer([0x48,3])[0]==0x80,False)
                transfer([0x4b]); eq('stale APB data rejected',transfer()[0]==0x80,False)
                eq('other slave address isolated',transfer([0xc],address=0x1a)[0]==0x80,False)
                wr(0x10830084,0x40)
                eq('PDB loss disables endpoint',get('powered'),0)
                eq('PDB loss NACK',transfer([0xc])[0]==0x80,False)
                wr(0x10830084,0x42); step(100000)
                eq('PDB cycle resets APB address',read(0x49),0)
                return dict(checks=checks, runtime=ti.runtime_state(qmp), original_guest_executed=False,
                            qemu_sha256=vm.sha(exe))
        finally:
            vm.save(output/'checks.json', checks)
            if qmp:
                try: qmp.execute('quit')
                except (OSError, RuntimeError, ValueError): pass
                qmp.close()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate(); proc.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qtest-output', type=Path)
    parser.add_argument('--qemu', type=Path, help='Optional test-only executable inside project scratch')
    args = parser.parse_args()
    if args.qtest_output is None:
        if args.qemu: parser.error('--qemu requires --qtest-output')
        unittest.main(argv=[__file__])
    else:
        out = args.qtest_output.resolve()
        if not out.is_relative_to(vm.ROOT/'var') or out.exists():
            parser.error('Use a new scratch output directory')
        executable = args.qemu.resolve(strict=True) if args.qemu else None
        if executable and not executable.is_relative_to(vm.ROOT/'var'):
            parser.error('The override executable must be inside project scratch')
        out.mkdir(parents=True)
        result = check_qemu(out, executable)
        vm.save(out/'result.json', result)
        print(f"TI983: {len(result['checks'])} QTest checks passed")
