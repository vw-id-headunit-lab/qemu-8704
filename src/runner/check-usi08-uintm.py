
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
import linux_uart_platform as uart
import original_8704_vm as vm
from ufs_platform import QTest


def run(qemu, out):
    out.mkdir()
    qport, tport, sport = vm.free_port(), vm.free_port(), vm.free_port()
    argv=[str(qemu),'-machine','virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-linux-usi-uart=1',
          '-cpu','cortex-a57','-accel','qtest','-m','128','-display','none','-monitor','none','-nic','none','-S',
          '-qmp',f'tcp:127.0.0.1:{qport},server=on,wait=off',
          '-qtest',f'tcp:127.0.0.1:{tport},server=on,wait=off',
          '-chardev',f'socket,id=usi08,host=127.0.0.1,port={sport},server=on,wait=off',
          *(['-serial','null']*5),'-serial','chardev:usi08']
    env=os.environ.copy()
    env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
    with (out/'qemu.log').open('xb') as log:
        proc=subprocess.Popen(argv,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        qmp=qt=serial=None
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError(f'QEMU exited with {proc.returncode}')
                try:
                    qmp=vm.QMP(qport)
                    break
                except OSError:
                    time.sleep(.05)
            if not qmp:
                raise TimeoutError('QMP unavailable')
            qt=QTest(tport)
            serial=socket.create_connection(('127.0.0.1',sport),timeout=3)
            qmp.execute('cont')
            gic=next(r['name'] for r in qmp.execute('qom-list',{'path':'/machine/unattached'})
                     if r['type']=='child<arm_gic>')
            qt.call('irq_intercept_in /machine/unattached/'+gic)
            b=0x10940000
            prop=lambda k:qmp.execute('qom-get',{'path':'/machine/exynos-usi08','property':k})
            load=lambda:int(qt.call(f'readq {b+0x38:#x}').split()[1],16)
            store=lambda v:qt.call(f'writeq {b+0x38:#x} {v:#x}')
            qt.write(0x10821010,1)
            qt.write(b+4,5)                             
            qt.write(b+8,1)                 
            qt.write(b+0x38,0xf)
            assert load()==0xf
            mark=len(qt.events)
            serial.sendall(b'U')
            deadline=time.monotonic()+3
            while not qt.read(b+0x10)&1:
                if time.monotonic()>deadline:
                    raise TimeoutError('USI08 did not receive the host byte')
                time.sleep(.01)
            assert 'IRQ raise 362' not in qt.events[mark:]
            store(load()&~1)                                        
            assert qt.read(b+0x38)==0xe and 'IRQ raise 362' in qt.events[mark:]
            assert int(qt.call(f'readb {b+0x24:#x}').split()[1],16)==ord('U')
            store(1<<32)
            assert qt.read(b+0x38)==0xe and prop('uintm-wide-rejected')==1
            assert prop('uintm-wide-reads')==2 and prop('uintm-wide-writes')==1
            qt.write(b+0xc4,1)
            store(0xb)                                                                
            assert prop('uintm-wide-writes')==1
            qt.write(b+0xc4,0)
            assert load()==0 and prop('uintm-wide-reads')==3
            state=uart.runtime_state(qmp)
            vm.save(out/'result.json',dict(passed=True, firmware_started=False,
                qemu_sha256=vm.sha(qemu), actual_rx_irq='IRQ raise 362', runtime=state,
                transcript=qt.transcript, policy=uart.policy(uart.PROFILE,True)))
            return state['8']
        finally:
            if serial:
                serial.close()
            if qt:
                qt.close()
            if qmp:
                qmp.close()
            proc.terminate()
            proc.wait(timeout=8)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('qemu',type=Path)
    p.add_argument('output',type=Path)
    a=p.parse_args()
    print(run(a.qemu.resolve(strict=True),a.output.resolve()))
