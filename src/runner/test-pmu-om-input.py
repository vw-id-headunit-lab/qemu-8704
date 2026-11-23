
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm
from qnx_pmu_platform import om_input_policy


def run(qemu, out, value):
    qp,tp=vm.free_port(),vm.free_port()
    path='/machine/exynos-pmu'
    command=[str(qemu),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1',
        '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128M','-display','none','-nic','none',
        '-monitor','none','-serial','null','-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off',
        '-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-S']
    if value is not None:
        command+=['-global',f'driver=exynos-auto9.pmu,property=virtual-om-input,value={value}']
    env=os.environ.copy()
    env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
    transcript=[]
    with (out/('enabled.log' if value is not None else 'off.log')).open('wb') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        qmp=None
        try:
            deadline=time.monotonic()+8
            while qmp is None and time.monotonic()<deadline:
                if proc.poll() is not None:
                    raise RuntimeError('QEMU exited; see this variant log')
                try:
                    qmp=vm.QMP(qp)
                except OSError:
                    time.sleep(.1)
            if not qmp:
                raise RuntimeError('QMP unavailable')
            with socket.create_connection(('127.0.0.1',tp),timeout=3) as peer, peer.makefile('rwb',buffering=0) as stream:
                def test(query):
                    stream.write((query+'\n').encode())
                    reply=stream.readline().decode().strip()
                    transcript.append((query,reply))
                    assert reply.startswith('OK'),reply
                    return reply

                def get(prop):
                    return qmp.execute('qom-get',{'path':path,'property':prop})

                qmp.execute('cont')
                assert get('om-input-active')==int(value is not None)
                before=get('rejected-accesses')
                data=int(test('readl 0x10460000').split()[1],16)
                if value is None:
                    assert get('rejected-accesses')==before+1
                    assert get('om-input-reads')==0
                else:
                    assert data==get('om-input-value')==value
                    assert get('rejected-accesses')==before
                    assert get('om-input-reads')==1
                    test('writel 0x10460000 0xffffffff')
                    test('readl 0x10460004')
                    assert get('rejected-accesses')==before+2
                    assert int(test('readl 0x10460000').split()[1],16)==value
                    assert get('om-input-reads')==2 and get('om-input-cpu-reads')==0
                    try:
                        qmp.execute('qom-set',{'path':path,'property':'virtual-om-input','value':1})
                    except RuntimeError as e:
                        assert 'fixed at device realization' in str(e),e
                    else:
                        raise AssertionError('Unexpected runtime strap write')
        finally:
            if qmp:
                qmp.close()
            proc.terminate()
            proc.wait(8)
    return dict(value=value,command=command,accesses=transcript)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('qemu',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    out=args.output.resolve(); out.mkdir()
    qemu=args.qemu.resolve(strict=True)
    result=dict(qemu_sha256=vm.sha(qemu),native_policy=om_input_policy(0,True),
                default=run(qemu,out,None),enabled=run(qemu,out,0x524a9),firmware_started=False)
    vm.save(out/'result.json',result)
    print(json.dumps({'qtest_accesses':sum(len(result[k]['accesses']) for k in ('default','enabled'))}))
