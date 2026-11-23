





import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import time
import original_8704_vm as vm

PUBLIC_SOURCE=vm.ROOT/'var/inputs/support/归档/虚拟机平台对照/pmu-signal-native--upstream-s3c2410-wdt.c'
PUBLIC_SHA='3c90a67205064b29735602672c48e2a487d0c0245077dd1ae09a7f004f11f814'


def policy(profile, clock_hz=0, acpm_clock_hz=0):
    if profile is None and clock_hz==0:return None
    if profile!='virtual-samsung32-v1' or not 1000000<=clock_hz<=1000000000 or not 1000000<=acpm_clock_hz<=1000000000:
        raise ValueError('APM watchdog requires explicit profile,1MHz..1GHz counter clock and native CPU')
    return {'profile':profile,'clock_hz':clock_hz,'physical_clock_verified':False,
            'factory_images_modified':False,'ready_value_injected':False,
            'physical_irq_route_verified':False,'physical_reset_route_verified':False,
            'cold_reset':'Declared virtual disabled control/data/count0, NOT measured silicon/BL2 state',
            'counter_bits':32,'source_functions':['4BAC','4BE8'],
            'irq_reset_requests':'Actual named watchdog-event outputs0/1; no fabricated delivery',
            'unrouted_request_policy':'Default expiry-stop1 pauses VM; QTest explicitly uses0 to observe outputs without CPUs',
            'assumptions':['Divider16/32/64/128 and prescale+1 use Samsung public register family',
                           'WTCNT writes feed; WTDAT writes affect next IRQ-only reload, not current deadline',
                           'Zero count schedules a minimum one source-divider tick',
                           'Changing control/clock divider rephases the retained remaining count',
                           'Reset request latches and waits for an external reset sink; no pretend reset'],
            'complexity':'O(1) read/program/expiry, O(number of expiries) for IRQ-only periodic mode'}


def original_contract():
    import acpm_firmware
    raw,meta=acpm_firmware.original_image()
    if meta['source_sha256']!='a83ca762143fab69a0724843bad958f39916ec261f7a65e4de615d82345a5a44' or hashlib.sha256(raw).hexdigest()!='ae498979f262d28018aee012d869fd6d95fe5686d838e83e3fb9c876e84caa9a':
        raise ValueError('Original watchdog firmware identity changed')
    if vm.sha(PUBLIC_SOURCE)!=PUBLIC_SHA:raise ValueError('Public watchdog source identity changed')
    return {'source':meta,'public_source':str(PUBLIC_SOURCE),'public_sha256':PUBLIC_SHA,
            'public_url':'https://raw.githubusercontent.com/torvalds/linux/master/drivers/watchdog/s3c2410_wdt.c',
            'code_ranges':[{'local_start':hex(start),'bytes':size,'sha256':hashlib.sha256(raw[start:start+size]).hexdigest()}
                           for start,size in ((0x4bac,0x3c),(0x4be8,0x1c))],
            'registers':{'WTCON':'A0440000','WTDAT':'A0440004','WTCNT':'A0440008','WTCLRINT':'A044000C'},
            'original_programmed_count':100000,'original_control_after_enable':'0xE25',
            'public_scope':'Samsung family coding and AutoV9 AP watchdog variants, not private MCU clock/NVIC/reset specification',
            'full_private_irq_or_reset_route_recovered':False}


def check_model(output, qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qmp_port,test_port=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none',
             '-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'scope':'Actual virtual watchdog, no firmware/OS executed'})
    vm.save(output/'virtual-policy.json',policy('virtual-samsung32-v1',96000000,96000000))
    vm.save(output/'original-contract.json',original_contract())
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    transcript=[];checks=[];levels={};qmp=connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            end=time.monotonic()+10
            while time.monotonic()<end:
                if process.poll() is not None:raise RuntimeError('Watchdog QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('Watchdog QMP timeout')
            qmp.execute('cont');connection=socket.create_connection(('127.0.0.1',test_port),timeout=5)
            stream=connection.makefile('rwb',buffering=0)
            def test(cmd):
                stream.write((cmd+'\n').encode());reply=stream.readline().decode().strip()
                while reply.startswith('IRQ '):
                    parts=reply.split();levels[int(parts[2])]=int(parts[1]=='raise')
                    transcript.append({'asynchronous_gpio':reply});reply=stream.readline().decode().strip()
                transcript.append({'command':cmd,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                return reply
            def read(off):return int(test(f'readl {0x10440000+off:#x}').split()[1],16)
            def write(off,value):test(f'writel {0x10440000+off:#x} {value:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(name):return qmp.execute('qom-get',{'path':'/machine/exynos-apm-wdt','property':name})
            def set_input(name,value):return qmp.execute('qom-set',{'path':'/machine/exynos-apm-wdt','property':name,'value':value})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('Watchdog check failed: '+name)
            def denied(name,value):
                failed=False
                try:set_input(name,value)
                except RuntimeError:failed=True
                check('Rejected invalid/active QOM '+name+'='+str(value),failed,True)
            check('Profile disabled by default',get('profile'),0)
            check('Unhandled request stop enabled by default',get('expiry-stop'),1)
            read(0);check('Disabled profile remains MMIO fault',get('rejected-accesses'),1)
            denied('profile',1);set_input('clock-hz',96000000);set_input('profile',1)
            denied('clock-hz',999999);denied('clock-hz',1000000001);denied('profile',2);denied('expiry-stop',2)
            check('Clock input preserved',get('clock-hz'),96000000)
            set_input('expiry-stop',0)                                                    
            test('irq_intercept_out /machine/exynos-apm-wdt watchdog-event')
            check('Declared cold control',read(0),0)
            write(4,0);write(8,100000);step(1000000)
            check('Disabled32bit count does not run',read(8),100000)
            check('No expiry caused by reads',get('expirations'),0)
            rejected=get('rejected-accesses');write(0,2)
            check('Reserved control bit rejected',get('rejected-accesses'),rejected+1)
            rejected=get('rejected-accesses');read(12)
            check('CLRINT is write-only',get('rejected-accesses'),rejected+1)
            rejected=get('rejected-accesses');write(16,1)
            check('Unknown register hole rejected',get('rejected-accesses'),rejected+1)
            write(4,6000);write(8,6000);write(0,0x24)
            denied('clock-hz',26000000);denied('profile',0)
            step(500000);check('Counter actually decrements',read(8),3000)
            write(4,12000);check('WTDAT does not feed',read(8),3000)
            write(0,0x24);step(499999)
            check('Same control write cannot feed',read(8),1)
            check('No early expiry',get('expirations'),0)
            step(1);check('Exact deadline expiry',get('expirations'),1)
            check('IRQ-only mode reloads actual WTDAT',read(8),12000)
            check('Real IRQ output raised',levels.get(0,0),1)
            check('No invented reset output',levels.get(1,0),0)
            write(12,0xffffffff);check('CLRINT lowers actual IRQ output',levels.get(0,0),0)
            write(8,6000);step(750000);check('Feed counter elapsed',read(8),1500)
            write(8,6000);step(999999);check('Feed restarts real deadline',get('expirations'),1)
            step(1);check('Fed deadline expires once',get('expirations'),2)
            write(12,1);write(0,4);held=read(8);step(5000000)
            check('Disable preserves remaining count',read(8),held)
            check('Disable cancels old deadline',get('expirations'),2)
            write(4,3);write(8,3);write(0,0xf3c);step(63999)
            check('Divider/prescaler deadline not early',get('expirations'),2)
            step(1);check('Divider/prescaler actual deadline',get('expirations'),3)
            qmp.execute('system_reset');step(1)
            check('Reset cancels timer and clears outputs',get('expirations'),0)
            check('Reset lowers real GPIO',sum(levels.values()),0)
            write(4,0xffffffff);write(8,0xffffffff);write(0,0xff3c);step(1000000)
            check('Maximum32bit count and prescaler do not overflow',read(8),0xffffffff-2)
            check('Long deadline cannot overflow into immediate expiry',get('expirations'),0)
            qmp.execute('system_reset');step(1)
            write(4,0);write(8,100000);write(0,0xe25);step(249999999)
            check('Original100000 count retained above16bits',read(8),1)
            check('Original programmed deadline not early',get('expirations'),0)
            step(1);check('Original programmed deadline expires',get('expirations'),1)
            check('Actual IRQ request line',levels.get(0,0),1)
            check('Actual reset request line',levels.get(1,0),1)
            check('Reset request is latched, not fake delivery',get('reset-pending'),1)
            write(12,1);check('CLRINT cannot cancel reset request',levels.get(1,0),1)
            write(8,200000);step(500000000)
            check('Pending reset is not ignored by feeding',get('expirations'),1)
            qmp.execute('system_reset');step(1)
            check('External reset clears pending reset line',levels.get(1,0),0)
            set_input('expiry-stop',1);write(4,6000);write(8,6000);write(0,0x24);step(1000000)
            end=time.monotonic()+2;status=qmp.execute('query-status')
            while status.get('running') and time.monotonic()<end:
                time.sleep(.02);status=qmp.execute('query-status')
            vm.save(output/'unrouted-expiry-stop.json',status)
            check('Default native policy actually stops on unrouted request',status.get('status'),'debug')
            return {'checks':checks,'passed':True,'guest_boot_verified':False,'private_irq_or_reset_delivery_verified':False,
                    'scope':'Actual virtual clock/count/output/stop checks; no original CPU executed'}
        finally:
            vm.save(output/'qtest-transcript.json',transcript);vm.save(output/'checks.json',checks)
            if connection:connection.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qemu',type=Path);parser.add_argument('--contract-only',action='store_true')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True);vm.save(output/'result.json',original_contract() if args.contract_only else check_model(output,args.qemu))
    print(str(output/'result.json'))


if __name__=='__main__':main()
