
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


def check(qemu,out,enabled,qnx_enabled=False,cache=False):
    out.mkdir()
    test_port,qmp_port=vm.free_port(),vm.free_port()
    profile=('virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-qnx-g3d00=1,'
             'x-exynos-mali-g3d1-id=0x72110000,x-exynos-mali-g3d1-discovery=1,'
             'x-exynos-mali-g3d1-reset-irq=1,x-exynos-mali-g3d1-config=1,x-exynos-mali-g3d1-empty-power=1')
    if enabled:
        profile+=',x-exynos-mali-g3d1-jm-topology=1'
    if qnx_enabled:
        profile+=',x-exynos-qnx-g3d00-jm-topology=1'
    cmd=[str(qemu),'-machine',profile,'-cpu','cortex-a57' if cache else 'cortex-a72','-smp','8','-m','128',
         '-display','none','-monitor','none','-nic','none','-S',
         '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
         '-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(out/'wire.log')]
    if cache:
        cmd+=['-accel','qtest']
    env=os.environ.copy()
    env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
    transcript=[]
    with (out/'qemu.log').open('xb') as log, subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,
            env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as proc:
        try:
            for _ in range(80):
                if proc.poll() is not None:
                    raise RuntimeError('QEMU exited before QTest connection')
                try:
                    qmp=vm.QMP(qmp_port)
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise TimeoutError('QEMU management endpoint not ready')
            try:
                with socket.create_connection(('127.0.0.1',test_port),timeout=5) as conn, conn.makefile('rb') as stream:
                    def request(line):
                        conn.sendall(line.encode('ascii')+b'\n')
                        reply=stream.readline(4096).decode('ascii').strip()
                        if not reply.startswith('OK'):
                            raise RuntimeError(f'{line}: {reply}')
                        transcript.append(dict(command=line,response=reply))
                        return reply.split()[1:]
                    base=0x19700000
                    probes={base+0x18:0,base+0x1c:7 if enabled else 0,
                            base+0xc0:0x200 if enabled else 0,base+0xc4:0x9c if enabled else 0,
                            base+0xc8:0x18 if enabled else 0,base+0x100:0,
                            0x1930001c:7 if qnx_enabled else 0}
                    if enabled:
                        probes.update({base+0x1010:0,base+0x1924:0,base+0x1960:0})
                    for address,expected in probes.items():
                        result=int(request(f'readl {address:#x}')[0],16)
                        if result!=expected:
                            raise ValueError(f'{address:#x}: got {result:#x}, expected {expected:#x}')
                    qpath='/machine/exynos-mali-g3d1'
                    count=qmp.execute('qom-get',{'path':qpath,'property':'jm-probe-reads'})
                    if count!=(7 if enabled else 0):
                        raise ValueError('Unexpected count of real G3D1 topology reads')
                    if enabled:
                        request(f'writel {base+0x1960:#x} 1')
                        if qmp.execute('qom-get',{'path':qpath,'property':'rejected'})!=1:
                            raise ValueError('An execution command was unexpectedly accepted')
                        if int(request(f'readl {base+0x1000:#x}')[0],16):
                            raise ValueError('The topology-only model generated a job IRQ')
                    if qnx_enabled:
                        for offset,expected in ((0x18,0),(0xc0,0x200),(0xc4,0x9c),(0xc8,0x18),(0x100,0),(0x1010,0)):
                            if int(request(f'readl {0x19300000+offset:#x}')[0],16)!=expected:
                                raise ValueError('QNX JM register mismatched its independent topology')
                        request('writel 0x19301960 1')
                        qnx_path='/machine/exynos-mali-g3d00'
                        if (qmp.execute('qom-get',{'path':qnx_path,'property':'rejected'})!=1 or
                                qmp.execute('qom-get',{'path':qnx_path,'property':'jm-probe-reads'})!=5 or
                                int(request('readl 0x19301000')[0],16)):
                            raise ValueError('QNX topology-only model failed isolation or job-rejection check')
                    if cache:
                        qmp.execute('cont')
                        get=lambda key:qmp.execute('qom-get',{'path':qpath,'property':key})
                        gpu=lambda offset:int(request(f'readl {base+offset:#x}')[0],16)
                        rejected=get('rejected')
                        request('writel 0x19700028 0x20000')
                        request('writel 0x19700030 8')
                        assert gpu(0x2c)==0
                        request('clock_step 1000')
                        if enabled:
                            assert get('rejected')==rejected and get('cache-commands')==1
                            assert get('cache-completions')==1 and gpu(0x2c)==0x20000
                            assert get('gpu-irq-level')==1 and gpu(0x1000)==0 and gpu(0x2000)==0
                            request('writel 0x19700024 0x20000')
                            assert gpu(0x2c)==0 and get('gpu-irq-level')==0
                            request('writel 0x19700030 8')
                            request('writel 0x19700030 1')
                            request('clock_step 1000')
                            assert gpu(0x20)==0x100 and get('cache-completions')==1
                        else:
                            assert get('rejected')==rejected+1 and get('cache-commands')==0
                            assert gpu(0x2c)==0
                qmp.execute('quit')
                proc.wait(timeout=5)
            finally:
                qmp.close()
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            vm.save(out/'result.json',dict(enabled=enabled,qnx_enabled=qnx_enabled,transcript=transcript,
                                          qemu_sha256=vm.sha(qemu),command=cmd))
    return len(transcript)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qnx',action='store_true')
    parser.add_argument('--cache',action='store_true')
    args=parser.parse_args()
    args.output.mkdir()
    profiles=[('off',False,False),('on',True,False)]
    if args.qnx:
        profiles.append(('qnx-only',False,True))
    print({label:check(args.qemu.resolve(strict=True),args.output/label,enabled,qnx_enabled,args.cache)
           for label,enabled,qnx_enabled in profiles})
