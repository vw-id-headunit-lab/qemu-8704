
import argparse
import os
from pathlib import Path
import struct
import subprocess
import time

import original_8704_vm as vm
import ufs_platform as u
import ufs_vh_platform as vh


def run(output, qemu):
    output.mkdir(parents=True, exist_ok=False)
    disk = output / 'read-only.raw'
    pattern = bytes((i * 19 + 3) % 256 for i in range(u.BLOCK_SIZE))
    with disk.open('xb') as f:
        f.truncate(16 * 1024 * 1024)
        f.seek(7 * u.BLOCK_SIZE)
        f.write(pattern)
    digest = vm.sha(disk)
    mp, tp = vm.free_port(), vm.free_port()
    command = [str(qemu), '-machine', 'virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-ufs=on,x-exynos-ufs-host0=1,x-exynos-ufs-mailbox=1,x-exynos-ufs-vh=1',
               '-cpu', 'cortex-a57', '-accel', 'qtest', '-smp', '8', '-m', '128', '-display', 'none',
               '-serial', 'null', '-serial', 'null', '-monitor', 'none', '-nic', 'none', '-S',
               '-qmp', f'tcp:127.0.0.1:{mp},server=on,wait=off', '-qtest',
               f'tcp:127.0.0.1:{tp},server=on,wait=off', '-qtest-log', str(output/'qtest.log'),
               '-global', 'ufs.x-samsung-byte-granularity=on',
               '-blockdev', f'driver=file,filename={disk.as_posix()},node-name=ufs-base-file,read-only=on',
               '-blockdev', 'driver=raw,file=ufs-base-file,node-name=ufs-base,read-only=on'] + vh.devices()
    vm.save(output/'launch.json', {'command': command, 'qemu_sha256': vm.sha(qemu)})
    env = dict(os.environ)
    env['PATH'] = str(vm.ROOT/'.local/deps/bin') + os.pathsep + env.get('PATH', '')
    rows = []

    def check(name, condition):
        rows.append({'name': name, 'passed': bool(condition)})
        if not condition:
            raise ValueError(name)

    qmp = qt = None
    with (output/'qemu.log').open('wb') as log:
        proc = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    qmp = vm.QMP(mp)
                    break
                except OSError:
                    time.sleep(.1)
            if qmp is None:
                raise RuntimeError('QTest QEMU failed to start; inspect qemu.log')
            qt = u.QTest(tp)
            gic = next(r['name'] for r in qmp.execute('qom-list', {'path':'/machine/unattached'})
                       if r['type']=='child<arm_gic>')
            qt.call('irq_intercept_in /machine/unattached/' + gic)
            check('Both distinct media nodes are read-only', len(vh.enable(qmp)) == 2)
            qmp.execute('cont')
            for i, vid in enumerate((2, 3, 4)):
                qt.write(u.VGEN + 4*i, vid)
            qt.write(u.S2MPU, 3)
            qt.write(u.S2MPU+0x20, 0x18)
            qt.write(u.S2MPU+0x2c, 0x18)
                                                                                  
            for i in (1, 2):
                vid = i+2
                table = u.TABLE + (i-1)*0x1000
                qt.put(table, b'\xff'*1024)
                qt.write(u.S2MPU+0x4008+vid*0x200, table >> 4)
                qt.write(u.S2MPU+0x400c+vid*0x200, 1)

                                                                             
                                                                               
            qt.write(u.HCI+0xd8, 0x601)
            check('Broadcast alone leaves both VH links down',
                  all(not (qt.read(u.HCI+i*0x10000+0x30)&7) for i in (1,2)))
            qt.write(u.HCI+0x34, 1)
            qt.write(u.HCI+0x90, 0x16)
            check('Physical link startup changes real PH state', qt.read(u.HCI+0x30)&7 == 7)

            def exercise(i):
                base = u.HCI+0x10000*i
                other = u.HCI+0x10000*(3-i)
                trl, cmd, data = (a+(i-1)*0x20000 for a in (u.UTRL, u.COMMAND, u.DATA))
                vid = i+2
                label = f'VH{i}'
                state = lambda: vh.counters(qmp, i)
                cap = qt.read(base)
                check(f'{label} separate CAP and host index', cap & 31 == 31 and state()['host-index'] == i)
                check(f'{label} remains unlinked before real requests', qt.read(base+0x34) == 0)
                qt.write(base+0x34, 1)
                check(f'{label} HCE remains local and does not imply shared readiness',
                      qt.read(base+0x34) == 1 and qt.read(base+0x30)&7 == 0)
                qt.write(base+0xdc, 2)
                check(f'{label} reads its real PH COMPLETE', qt.read(base+0xd8) == 1)
                qt.write(base+0xd0, 0x10000)
                qt.write(base+0xdc, 5)
                check(f'{label} full exchange exposes all link readiness from PH', qt.read(base+0x30)&15 == 15)
                qt.write(base+0xd4, 0x10200 if i==1 else 0x10000)
                check(f'{label} owner-specific interrupt config stays local',
                      qt.read(base+0xd4)==(0x10200 if i==1 else 0x10000)
                      and qt.read(u.HCI+0xd4)==0)
                qt.write(base+0x24, 0xffffffff)
                qt.write(base+0x20, 0xffffffff)
                qt.write(base+0x50, trl)
                qt.write(base+0x60, 1)
                check(f'{label} UTRLBA not aliased', qt.read(other+0x50) != trl and qt.read(u.HCI+0x50) == 0)

                def submit(op=0x28, request=None):
                    qt.put(cmd, bytes(4096))
                    qt.put(cmd, u.scsi_request(op, 7) if request is None else request)
                    qt.put(data, bytes(u.BLOCK_SIZE) if op == 0x28 else b'\x35'*u.BLOCK_SIZE)
                    qt.put(cmd+2048, struct.pack('<QII', data, 0, u.BLOCK_SIZE-1) + bytes(112))
                    descriptor = bytearray(u.utrd(cmd, 0x04000000 if op == 0x28 else 0x02000000, 1, True))
                    struct.pack_into('<H',descriptor,28,128)
                    qt.put(trl, descriptor)
                    qt.write(base+0x58, 1)

                def until(fn):
                    limit = time.monotonic() + 4
                    while time.monotonic() < limit:
                        if fn():
                            return True
                        qt.call('clock_step 10000')
                        time.sleep(.002)
                    return False

                submit()
                check(f'{label} backend read completed', until(lambda: qt.read(base+0x20) & 1))
                check(f'{label} request completes on its own SPI', f'IRQ raise {227+i}' in qt.events)
                if qt.get(cmd+1024, 8)[7] == 2:
                                                                                      
                    qt.write(base+0x20, 0xffffffff)
                    sense = bytearray(288)
                    sense[0:2], sense[16], sense[20] = b'\x01\x40', 3, 18
                    struct.pack_into('>I', sense, 12, 18)
                    submit(request=sense)
                    check(f'{label} initial REQUEST SENSE completes',
                          until(lambda: qt.read(base+0x20) & 1) and qt.read(trl+8)==0)
                    qt.write(base+0x20, 0xffffffff)
                    submit()
                    check(f'{label} READ10 after initial sense completes',
                          until(lambda: qt.read(base+0x20) & 1))
                check(f'{label} SCSI GOOD and actual RAM matches block 7',
                      qt.read(trl+8) == 0 and qt.get(cmd+1024, 8)[7] == 0
                      and qt.get(data, u.BLOCK_SIZE) == pattern and qt.read(base+0x58) == 0)
                check(f'{label} uses only its own VGEN route', state()['host0-last-vid'] == vid)
                qt.write(base+0x20, 0xffffffff)
                submit(0x2a)
                check(f'{label} write returns a status', until(lambda: qt.read(base+0x20) & 1))
                check(f'{label} read-only write is not SCSI GOOD',
                      qt.read(trl+8) != 0 or qt.get(cmd+1024, 8)[7] != 0)
                qt.write(base+0x20, 0xffffffff)

                table = u.TABLE + (i-1)*0x1000
                page = (data & 0x3fffffff) >> 12
                shift = (page % 16)*2
                qt.write(table + (page//16)*4, 0xffffffff & ~(2 << shift))
                qt.write(u.S2MPU+0x1000, vid << 16)
                submit()
                check(f'{label} active S2MPU blocks its DMA', until(lambda: state()['host0-s2-denied'] > 0))
                check(f'{label} denied page unmodified and no successful completion',
                      qt.get(data,u.BLOCK_SIZE) == bytes(u.BLOCK_SIZE) and not qt.read(base+0x20))
                check(f'{label} S2MPU fault is attributed to VID{vid}', bool(qt.read(u.S2MPU+0x2000) & (1<<vid)))
                qt.write(table + (page//16)*4, 0xffffffff)
                qt.write(u.S2MPU+0x1000, vid << 16)
                qt.write(u.S2MPU+0x2c, 1 << vid)
                check(f'{label} repairing the denied mapping resumes real DMA',
                      until(lambda: qt.read(base+0x20) & 1) and qt.get(data,u.BLOCK_SIZE) == pattern)
                qt.write(base+0x20, 0xffffffff)
                qt.write(u.VGEN+4*i, 7-vid)                               
                submit()
                check(f'{label} wrong VGEN VID rejected', until(lambda: state()['host0-invalid'] > 0))
                check(f'{label} wrong VID cannot complete the descriptor',
                      qt.read(trl+8) == 15 and not qt.read(base+0x20))
                qt.write(u.VGEN+4*i, vid)

            for i in (1, 2):
                exercise(i)
            state = vh.runtime(qmp, output)
            vm.save(output/'runtime.json', state)
            for i in (1, 2):
                stats = state[f'vh{i}']['blockstats']['stats']
                check(f'VH{i} actual read bytes counted separately', stats['rd_bytes'] == 2*u.BLOCK_SIZE)
                check(f'VH{i} no successful media writes', stats['wr_bytes'] == 0)
            qt.write(u.HCI+0x34,0)
            check('Loss of PH link removes VH presence without resetting their HCE',
                  all(qt.read(u.HCI+i*0x10000+0x30)&7 == 0 and qt.read(u.HCI+i*0x10000+0x34)==1
                      for i in (1,2)))
            check('Read-only backing file SHA256 unchanged', vm.sha(disk) == digest)
        finally:
            vm.save(output/'checks.json', rows)
            if qt:
                qt.close()
            if qmp:
                try:
                    qmp.execute('quit')
                finally:
                    qmp.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    print(f'{len(rows)} VH data-plane checks passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve(), args.qemu.resolve())

