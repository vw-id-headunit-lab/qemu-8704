
import argparse
import os
from pathlib import Path
import subprocess
import time

import original_8704_vm as vm
from ufs_platform import QTest
import scaler_reset_platform as sc
import abox_gic_platform as ab
import abox_sysmmu_platform as mmu
import abox_dsp_platform as dsp


def run(output, exe, with_sysmmu=False, with_axiuser=False, with_dsp_off=False, poly_probe=False):
    output.mkdir(parents=True,exist_ok=False)
    mp,tp=vm.free_port(),vm.free_port()
    mode=2 if poly_probe else 1
    command=[str(exe),'-machine',f'virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-scaler-reset={mode},x-exynos-abox-gic=1',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none','-serial','null','-serial','null',
             '-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{mp},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    if with_dsp_off:
        if not with_axiuser:
            raise ValueError('DSP cold status test needs AXIUSER')
        command[2]=command[2].replace('virt,','virt-8704-apm,',1)+',x-exynos-abox-dsp-off=1,x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1'
    if with_axiuser:
        command[2]+=',x-exynos-abox-axiuser=1'
    if with_sysmmu:
        command[2]+=',x-exynos-linux-sysmmu=1,x-exynos-abox-sysmmu=1'
    vm.save(output/'launch.json',dict(command=command,qemu_sha256=vm.sha(exe)))
    env=dict(os.environ)
    env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    qt=qmp=None
    checks=[]
    def check(name,actual,expected):
        checks.append(dict(name=name,actual=actual,expected=expected,passed=actual==expected))
        if actual!=expected:
            raise ValueError(f'{name}: {actual} != {expected}')

    with (output/'qemu.log').open('wb') as log:
        proc=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            until=time.monotonic()+8
            while time.monotonic()<until and proc.poll() is None:
                try:
                    qmp=vm.QMP(mp)
                    break
                except OSError:
                    time.sleep(.1)
            if qmp is None:
                raise RuntimeError('QTest failed to start')
            qt=QTest(tp)
            qmp.execute('cont')
            d,c=(r[0] for r in ab.REG)
            ap_pmr=qt.read(0x10102004)
            check('Scaler cold CFG',qt.read(sc.BASE+4),0)
            qt.write(sc.BASE+4,2)
            check('Scaler reset bit asserted by real request',qt.read(sc.BASE+4),2)
            qt.call('clock_step 127')
            check('Scaler reset remains asserted before deadline',qt.read(sc.BASE+4),2)
            qt.call('clock_step 1')
            check('Scaler reset bit self-clears at deadline',qt.read(sc.BASE+4),0)
            check('Scaler completion count',sc.runtime(qmp)['reset-completions'],1)
            if poly_probe:
                check('POLY cold probe can reset before its first version read',
                      (sc.runtime(qmp)['rejected'],sc.runtime(qmp)['version-reads']),(0,0))
                check('Original POLY version',qt.read(sc.BASE+0x260),sc.VERSION)
                qt.write(sc.BASE+4,8)
                check('POLY stop request is not an immediate ACK',qt.read(sc.BASE+4),8)
                qt.write(sc.BASE+4,2)
                check('POLY reset during outstanding stop is rejected',sc.runtime(qmp)['rejected'],1)
                qt.call('clock_step 127')
                check('POLY stop ACK remains clear before deadline',qt.read(sc.BASE+4),8)
                qt.call('clock_step 1')
                check('POLY stop ACK asserts at deadline',qt.read(sc.BASE+4),12)
                qt.write(sc.BASE+4,2)
                check('POLY stop ACK is consumed by reset',qt.read(sc.BASE+4),2)
                qt.call('clock_step 128')
                check('POLY stop-and-reset also completes',
                      (qt.read(sc.BASE+4),sc.runtime(qmp)['reset-completions']),(0,2))
            qt.write(sc.BASE+4,1)
            qt.write(sc.BASE+0x100,0x1234)
            check('Job and unknown register explicitly rejected',sc.runtime(qmp)['rejected'],3 if poly_probe else 2)

                                                                                      
            check('Unwired ABOX starts with spurious IAR',qt.read(c+0xc),1023)
            qt.write(c+0x10,230)
            check('Original-style EOI does not create an interrupt',qt.read(c+0xc),1023)
            check('EOI write passed to real isolated GICC',ab.runtime(qmp)['last-eoi'],230)
            check('ABOX distributor virtual line count',qt.read(d+4)&31,7)
            qt.write(d,1)
            qt.write(c,1)
            qt.write(c+4,0xff)
            intid=230
            target = d+0x800+(intid & ~3)
            qt.write(target,1<<((intid%4)*8))
            qt.write(d+0x100+(intid//32)*4,1<<(intid%32))
            qt.write(d+0x200+(intid//32)*4,1<<(intid%32))
            check('Software pending INTID acknowledged by isolated GICC',qt.read(c+0xc),intid)
            qt.write(c+0x10,intid)
            check('Actual EOI clears active software-pended INTID',qt.read(c+0xc),1023)
            check('AP GICC PMR untouched by isolated ABOX writes',qt.read(0x10102004),ap_pmr)
            if with_sysmmu:
                base=mmu.BASE
                check('ABOX non-VCR capability exposes the original table depth',qt.read(base+0x874),0x50)
                check('Other Linux SysMMU capability unchanged',qt.read(0x19b90000+0x874),0)
                qt.write(base+0x2004,0)
                for index,cfg,match,id_ in mmu.TLBS:
                    offset=base+0x2000+index*0x20
                    for reg,val in ((4,cfg),(8,match),(0x14,id_)):
                        qt.write(offset+reg,val)
                        check(f'Original TLB{index} +{reg:#x} readback',qt.read(offset+reg),val)
                qt.write(base+4,0xf80)
                qt.write(base+0x100c,0xb8000)
                qt.write(base+0x1010,1)
                qt.write(base,5)
                check('Only original four indexed configurations retained',mmu.runtime(qmp)['abox-index-write-mask'],0xfff)
                check('Owner-only VID1 root and actual invalidate latch',
                      (qt.read(base+0x100c),mmu.runtime(qmp)['flush-all']),(0xb8000,1))
                qt.write(base+0x200c,0xb8000)
                qt.write(base+0x2034,0x70000)
                qt.write(base+0x100c,0xa0000)
                check('Foreign VID, match ID and cross-domain root rejected',
                      (mmu.runtime(qmp)['rejected'],qt.read(base+0x2034),qt.read(base+0x100c)),
                      (3,0x70004,0xb8000))
                qt.write(base,7)
                check('Disconnected ABOX SysMMU reaches blocked, not a DMA completion',
                      (qt.read(base+8),qt.read(base+0x60)),(1,0))
            if with_axiuser:
                x=ab.AXI_ADDRESS
                check('Cold AXIUSER lanes start explicitly zero',qt.read(x),0)
                qt.write(x,0xaaaa)
                check('Configuration lanes latch original 16-bit input',qt.read(x),0xaaaa)
                qt.write(x,0x10000)
                check('Reserved AXIUSER bits fail without changing retained value',qt.read(x),0xaaaa)
                check('AXIUSER rejects reserved write and has no IRQ side effect',
                      (ab.axi_runtime(qmp)['axi-rejected'],qt.read(c+0xc)),(1,1023))
            if with_dsp_off:
                addresses=[dsp.PMU_BASE+off+4 for off in dsp.CONFIG]
                for i, address in enumerate(addresses):
                    check(f'Unpowered ABOX core{i} reads off',qt.read(address),0)
                    qt.write(address,1)
                    check(f'ABOX core{i} cannot be marked ready by a status write',qt.read(address),0)
                counters=dsp.runtime(qmp)
                check('Only four ABOX status words accepted; writes rejected',
                      (list(counters.values())[:-1],counters['abox-dsp-status-rejected']),([2]*4,4))
            qmp.execute('system_reset')
            if with_dsp_off:
                check('System reset clears ABOX status telemetry',sum(dsp.runtime(qmp).values()),0)
            if with_axiuser:
                check('System reset clears separate AXIUSER config',qt.read(ab.AXI_ADDRESS),0)
            if with_sysmmu:
                check('Reset clears ABOX SysMMU writes and selected root',
                      (mmu.runtime(qmp)['abox-writes'],qt.read(mmu.BASE+0x100c)),(0,0))
            check('ABOX counters reset',ab.runtime(qmp)['eoi-writes'],0)
            check('Scaler counters reset',sc.runtime(qmp)['reset-completions'],0)
        finally:
            vm.save(output/'checks.json',checks)
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
    print(f'{len(checks)} hardware checks passed')


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--qemu',required=True,type=Path)
    ap.add_argument('--with-sysmmu',action='store_true')
    ap.add_argument('--with-axiuser',action='store_true')
    ap.add_argument('--with-dsp-off',action='store_true')
    ap.add_argument('--poly-probe',action='store_true')
    a=ap.parse_args()
    run(a.output.resolve(),a.qemu.resolve(),a.with_sysmmu,a.with_axiuser,a.with_dsp_off,a.poly_probe)

