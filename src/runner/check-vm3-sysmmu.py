
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm
from sysmmu_platform import DISPLAY_G0_PORT, DISPLAY_PORT, runtime_state
from vlm_platform import LINUX_FACTORY_HIGH_BANK


def check_display(command,qmp):
    def put(base,offset,value):
        command(f'writel {base+offset:#x} {value:#x}')
    def get(base,offset):
        return int(command(f'readl {base+offset:#x}')[0],16)

    expected={
        DISPLAY_G0_PORT:(0x40,(0x0c070004,0x0c000400,0x0c000800)),
        DISPLAY_PORT:(0x50,(0x70001,0x70002,0x70003,0x70004)),
    }
    def readback(base,offset,value):
        actual=get(base,offset)
        if actual!=value:
            raise ValueError(f'{base+offset:#x}: expected {value:#x}, got {actual:#x}')

    for base,(capa,ids) in expected.items():
        readback(base,0x874,capa)
        put(base,0x4,0xf80)
        put(base,0x2004,0x0a)
        readback(base,0x4,0xf80)
        readback(base,0x2004,0x0a)
        for i,sid in enumerate(ids,1):
            for off,value in ((4,0x0a),(8,0x10100),(0x14,sid)):
                reg=0x2000+0x20*i+off
                put(base,reg,value)
                readback(base,reg,value)
        put(base,0,5)
        readback(base,0,5)
        put(base,0,7)
        readback(base,8,1)
        put(base,0,5)
        readback(base,8,0)
        for off,value in ((0x3020,0xff000000),(0x3024,0xff42afff),(0x3018,1)):
            put(base,off,value)
                                                                                   
    put(DISPLAY_G0_PORT,0x2034,0x70001)
    put(DISPLAY_G0_PORT,0x2084,0x0a)
    put(DISPLAY_G0_PORT,0x4,0xf00)
    readback(DISPLAY_G0_PORT,0x2034,0x0c070004)
    readback(DISPLAY_G0_PORT,0x4,0xf80)
    put(DISPLAY_G0_PORT,0x4,0)
    put(DISPLAY_G0_PORT,0,0)
    readback(DISPLAY_G0_PORT,0x4,0)
    readback(DISPLAY_PORT,0x4,0xf80)
    rows=runtime_state(qmp,tuple(expected))
    keys=('display-qos-writes','display-index-write-mask','display-ctrl-writes',
          'display-flush-range','rejected','pending')
    for base,want in ((DISPLAY_G0_PORT,(2,0x1ff,4,1,3,0)),
                      (DISPLAY_PORT,(1,0xfff,3,1,0,0))):
        got=tuple(rows[hex(base)].get(k) for k in keys)
        if got!=want:
            raise ValueError(f'{base:#x}: expected {want}, got {got}')
    return rows


def check(qemu,out,mapped,display_only=False):
    out.mkdir()
    qt_port,qmp_port=vm.free_port(),vm.free_port()
    profile='virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-linux-sysmmu=1,nvdimm=on'
    cmd=[str(qemu),'-machine',profile,'-cpu','cortex-a57','-smp','8','-m','128M,slots=1,maxmem=64G',
         '-display','none','-monitor','none','-nic','none','-S','-accel','qtest',
         '-L',str(ROOT/'.local/build/build-aarch64/pc-bios'),
         '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
         '-qtest',f'tcp:127.0.0.1:{qt_port},server=on,wait=off','-qtest-log',str(out/'wire.log')]
    if mapped:
        address,size=LINUX_FACTORY_HIGH_BANK
        cmd+=['-object',f'memory-backend-ram,id=vm3-test-ram,size={size}',
              '-device',f'nvdimm,memdev=vm3-test-ram,addr={address:#x}']
    env=os.environ.copy()
    env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
    transcript=[]
    status={}
    with (out/'qemu.log').open('xb') as log, subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,
            env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as proc:
        qmp=None
        try:
            deadline=time.monotonic()+8
            while not qmp and time.monotonic()<deadline and proc.poll() is None:
                try:
                    qmp=vm.QMP(qmp_port)
                except OSError:
                    time.sleep(.1)
            if not qmp:
                raise RuntimeError('QEMU management endpoint did not start')
            with socket.create_connection(('127.0.0.1',qt_port),timeout=5) as conn, conn.makefile('rb') as stream:
                def command(line):
                    conn.sendall(line.encode('ascii')+b'\n')
                    reply=stream.readline(4096).decode('ascii').strip()
                    transcript.append({'command':line,'reply':reply})
                    if not reply.startswith('OK'):
                        raise RuntimeError(f'{line}: {reply}')
                    return reply.split()[1:]
                if display_only:
                    status=check_display(command,qmp)
                    qmp.execute('quit')
                    proc.wait(timeout=5)
                    return len(transcript)
                base=0x18c80000
                reg=base+0x300c
                if mapped:
                    command('writeq 0xaf9f38000 0xb39480521150768f')
                    if int(command('readq 0xaf9f38000')[0],16)!=0xb39480521150768f:
                        raise ValueError('QEMU did not back the exact OEM root address')
                def write(page,expected):
                    command(f'writel {reg:#x} {page:#x}')
                    if int(command(f'readl {reg:#x}')[0],16)!=expected:
                        raise ValueError(f'Root {page:#x} did not leave {expected:#x} latched')
                write(0xb8000,0xb8000)
                high=0xaf9f38 if mapped else 0xb8000
                write(0xaf9f38,high)
                write(0xaf9f39,high)                    
                write(0xb80000,high)                                    
                command(f'writel {base+0x3010:#x} 1')
                status=runtime_state(qmp,(base,))[hex(base)]
                expected=(2,2,1,0) if mapped else (1,3,1,0)
                if tuple(status[k] for k in ('ptbase-writes','rejected','flush-all','pending'))!=expected:
                    raise ValueError('Unexpected root/flush/reject/IRQ state: '+repr(status))
            qmp.execute('quit')
            proc.wait(timeout=5)
            return len(transcript)
        finally:
            if qmp:
                qmp.close()
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            vm.save(out/'result.json',dict(mapped=mapped,display_only=display_only,transcript=transcript,status=status,
                                           qemu_sha256=vm.sha(qemu),command=cmd))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--display-only',action='store_true')
    args=parser.parse_args()
    args.output.mkdir()
    cases=[('display-config',False)] if args.display_only else [('unbacked',False),('backed',True)]
    print({label:check(args.qemu.resolve(strict=True),args.output/label,mapped,args.display_only)
           for label,mapped in cases})
