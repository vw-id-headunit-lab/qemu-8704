




import argparse
import os
from pathlib import Path
import subprocess
import time
import original_8704_vm as vm
import vlm_platform as platform
from ufs_platform import QTest

BASE=0x103f0000
PWM=vm.ROOT/'.local/build/qemu-11.1.0/hw/timer/exynos4210_pwm.c'
PWM_SHA='ffb440b4cc76f1f60a3854e623e4ac44f75ee53ad410205d54563850196e32c7'


def contract():
    if vm.sha(PWM)!=PWM_SHA:raise ValueError('Original upstream PWM model changed')
    raw=platform.original('lk-boot/lk.bin').read_bytes()
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    instructions=[{'address':hex(i.address),'mnemonic':i.mnemonic,'operands':i.op_str}
        for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(raw[0x3badc:0x3bd20],0x3badc)]
    by_address={int(i['address'],16):(i['mnemonic'],i['operands']) for i in instructions}
    for address,value in {0x3bae4:('movk','x2, #0x103f, lsl #16'),0x3bb34:('mov','w5, #0x41'),
            0x3bb64:('str','w3, [x0]'),0x3bb80:('str','w3, [x1]'),0x3bb8c:('str','w0, [x1]')}.items():
        if by_address.get(address)!=value:raise ValueError('Original LK timer sequence changed')
    return {'profile':'virtual-lk-pwm24-v1','upstream':str(PWM),'upstream_sha256':PWM_SHA,
        'original_lk_sha256':vm.sha(platform.original('lk-boot/lk.bin')),'base':hex(BASE),'virtual_input_hz':24000000,
        'physical_clock_verified':False,'irqs_connected':False,'external_pwm_implemented':False,
        'instructions':instructions,'factory_images_modified':False}


def check_model(output,exe,enabled=True):
    source=contract();qp,tp=vm.free_port(),vm.free_port()
    machine='virt,virtualization=on,gic-version=2,x-exynos-vlm=on'+(',x-exynos-lk-pwm=1' if enabled else '')
    command=[str(exe),'-machine',machine,'-cpu','cortex-a57','-accel','qtest','-m','128','-display','none',
        '-monitor','none','-nic','none','-serial','null','-serial','null','-serial','null','-S',
        '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off',
        '-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(exe),'contract':source})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];error=None;qmp=qt=None
    def check(name,actual,expected):
        checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
        if actual!=expected:raise ValueError('PWM model check: '+name)
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise ValueError('PWM QEMU exited')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.05)
            if qmp is None:raise ValueError('PWM QMP unavailable')
            tree=qmp.execute('human-monitor-command',{'command-line':'info mtree -f'})
            check('Native polling aperture opt-in',f'{BASE:016x}-' in tree,enabled)
            if enabled:
                qmp.execute('cont');qt=QTest(tp)
                rd=lambda off:qt.read(BASE+off)
                wr=lambda off,val:qt.write(BASE+off,val)
                check('Cold timer stopped',rd(8),0)
                for channel,shift,count_offset in [(0,0,0x14),(1,8,0x20)]:
                    wr(4,rd(4)&~(15<<(channel*4)));wr(0x44,rd(0x44)&~(1<<channel))
                    wr(8,rd(8)&~(1<<shift));wr(0,(rd(0)&0xff00ff00)|0x41)
                    wr(0xc+channel*12,0xffffffff);wr(0x10+channel*12,0x7fffffff)
                    cleared=rd(8)&~(31<<shift);wr(8,cleared|(0xa<<shift))
                    before=rd(count_offset);qt.call('clock_step 1000000')
                    check(f'Channel{channel} manual-update does not run',rd(count_offset),before)
                    wr(8,cleared|(9<<shift));before=rd(count_offset)
                    qt.call('clock_step 6600000');after=rd(count_offset)
                                                                            
                    check(f'Channel{channel} elapsed real virtual ticks',2398<before-after<2402,True)
                    wr(8,rd(8)&~(1<<shift));stopped=rd(count_offset);qt.call('clock_step 1000000')
                    check(f'Channel{channel} stop freezes count',rd(count_offset),stopped)
                wr(8,0);wr(0,0);wr(4,0);wr(0xc,99);wr(8,0xa);wr(8,9)
                qt.call('clock_step 10000')
                check('Auto reload preserves bounded count',0<=rd(0x14)<=99,True)
                check('Actual underflow status latched',bool(rd(0x44)&32),True)
                wr(0x44,32);check('Underflow W1C clears status',rd(0x44)&32,0)
            qmp.execute('quit');process.wait(timeout=5)
        except Exception as exc:error=repr(exc)
        finally:
            if qt:vm.save(output/'qtest-transcript.json',{'calls':qt.transcript,'events':qt.events});qt.stream.close();qt.socket.close()
            if qmp:qmp.close()
            if process.poll() is None:process.terminate();process.wait(timeout=5)
    result={'passed':error is None and all(c['passed'] for c in checks),'error':error,'checks':checks,
        'qemu_exit_code':process.returncode,'full_original_boot_verified':False,'physical_frequency_verified':False}
    vm.save(output/'result.json',result)
    if not result['passed']:raise ValueError('PWM checks failed: '+str(output/'result.json'))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=('contract','check','strict-check'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qemu',type=Path,default=vm.ROOT/'runtime/qemu-system-aarch64.exe')
    args=parser.parse_args();output=args.output.resolve()
    if output.exists() or not output.is_relative_to(vm.ROOT/'var'):raise ValueError('Require new scratch output')
    output.mkdir(parents=True)
    result=contract() if args.operation=='contract' else check_model(output,args.qemu.resolve(),args.operation=='check')
    vm.save(output/'result.json',result);print(output/'result.json')


if __name__=='__main__':main()
