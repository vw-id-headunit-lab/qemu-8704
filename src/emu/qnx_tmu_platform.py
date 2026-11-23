
import argparse
import hashlib
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import original_8704_vm as vm
import vlm_platform as platform
from cmu_sequence import DynamicCalImage
import tmu_platform

ORIGINAL_SHA='00af06c7ccf4399d8668c6fcc1c81807e98f175be7f01c8f3bb7566638323248'
DEFAULT_PROGRAM=vm.ROOT/'var/inputs/support/归档/QNX显示与时钟对照/qnx-pmu-native--tmu'


def original_contract(program=DEFAULT_PROGRAM):
    raw=program.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ORIGINAL_SHA:raise ValueError('Original QNX tmu identity mismatch')
    image=DynamicCalImage(raw);base=image.pointer(0x16ff8);domains=[]
    expected=(('CPUCL0',0x10090000,462,2,4),('CPUCL1',0x10090000,462,6,4),
              ('G3D00',0x10094000,461,10,1),('G3D01',0x10094000,461,9,1),('G3D1',0x10094000,461,5,4))
    for n,want in enumerate(expected):
        address=base+32*n
        name=image.string(image.pointer(address));physical=image.pointer(address+8)
        spi,start,count,reserved=struct.unpack('<4I',image.elf.at(address+16,16))
        if (name,physical,spi,start,count)!=want or reserved:raise ValueError('QNX domain descriptor changed')
        domains.append({'name':name,'physical':hex(physical),'spi':spi,'qnx_interrupt':spi+32,
                        'start_sensor':start,'sensor_count':count,'descriptor':hex(address)})
    dt=[]
    for guest,relative in (('qnx','qnx/qtd/icas3gp-qla-vm.dtb'),('linux','ivi/icas3cn-gp_b0_zero.dtb')):
        path=platform.factory_file(relative)
        for name,props in platform.parse_dtb(path.read_bytes()).items():
            if 'tmu' in name.lower() or any(b'exynos-tmu' in v for v in props.values()):
                dt.append({'guest':guest,'path':name,'source_sha256':vm.sha(path),
                           'properties':{k:platform.render(v) for k,v in props.items()}})
    return {'program':str(program.resolve()),'sha256':ORIGINAL_SHA,'domains':domains,'dt_matches':dt,
            'functions':{'init':'0x4d40','enable':'0x44b8','disable':'0x44d0','valid_and_read':'0x5440',
                         'thresholds':'0x4978','irq_handler':'0x1d60','irq_enable':'0x1f88',
                         'status_read':'0x48c0','status_w1c':'0x4908','clear_all':'0x44e8'},
            'control':{'offset':32,'enable_bit':0,'trim_fields':'bits24..28,8..11'},
            'valid':{'offset':40,'sensor0_7_bit':'sensor+12','sensor8_11_bit':'sensor+16'},
            'scope':'Pinned ELF relocation/descriptor bytes and REA pseudocode, not physical timing or actual IRQ delivery'}


def profile_policy(temperature_c=25):
    data=tmu_platform.profile_policy(temperature_c)
    data.update(profile='virtual-qnx-controller-v1',
        sampling={**data['sampling'],'banks_independently_enabled':True,'cold_enable_is_assumption':True},
        controller={'ctrl20':'enable cancels/restarts actual ADC deadline, no ready-bit injection',
                    'valid28':'per-bank sample validity after deadline only',
                    'thresholds':'8 rise/8 fall nine-bit threshold pairs, latched crossings',
                    'irq':'12 top/11 sub finite sensor slots, masks and W1C pending; real GIC SPIs462/461',
                    'config24_2c_38':'finite retention only; units and analog filtering unknown'},
        limits='Uniform declared virtual environment; no physical OTP, analog thermal/filtering, overtemperature reset or historical-sample FIFO; migration absent')
    return data


def runtime_state(qmp):
    names=['control-reads','control-writes','valid-reads','threshold-writes','irq-raises',
           'control-history-first256','history-dropped','virtual-time-ns','bank0-deadline-ns','bank1-deadline-ns']
    names += [f'bank{i}-{key}' for i in range(2) for key in ('control','valid','completions','irq-level')]
    return {n:qmp.execute('qom-get',{'path':'/machine/exynos-tmu','property':n}) for n in names}


def check_model(output,qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qp,tp=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000'+tmu_platform.machine_options('virtual-qnx-controller-v1',25,96000000),
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none','-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'scope':'Real device and GIC QTest, no guest boot'})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];transcript=[];events=[];qmp=connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('TMU QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('TMU QMP timeout')
            qmp.execute('cont');connection=socket.create_connection(('127.0.0.1',tp),timeout=5);stream=connection.makefile('rwb',buffering=0)
            def test(command):
                stream.write((command+'\n').encode())
                while True:
                    reply=stream.readline().decode().strip()
                    if reply.startswith('IRQ '):events.append(reply);continue
                    transcript.append({'command':command,'reply':reply})
                    if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                    return reply
            def read(a):return int(test(f'readl {a:#x}').split()[1],16)
            def write(a,v):test(f'writel {a:#x} {v:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(n):return qmp.execute('qom-get',{'path':'/machine/exynos-tmu','property':n})
            def check(n,a,e):
                checks.append({'name':n,'actual':a,'expected':e,'passed':a==e})
                if a!=e:raise ValueError('TMU check failed: '+n)
            def temperature(c):qmp.execute('qom-set',{'path':'/machine/exynos-tmu','property':'temperature-c','value':c})
            children=qmp.execute('qom-list',{'path':'/machine/unattached'})
            gic=next(row['name'] for row in children if row['type']=='child<arm_gic>')
            test('irq_intercept_in /machine/unattached/'+gic)
            for bank,base in enumerate((0x10090000,0x10094000)):
                check('Explicit cold enabled bank'+str(bank),read(base+0x20),1)
                check('No instant valid bank'+str(bank),read(base+0x28),0)
            step(49);check('Not ready before real ADC deadline',read(0x10090028),0)
            step(1)
            check('Top valid bitmap',read(0x10090028),0x0f0ff000)
            check('Sub valid bitmap',read(0x10094028),0x070ff000)
            for base in (0x10090000,0x10094000):
                check('QNX sensor1 real packed sample',read(base+0x40)>>9,150)
                check('QNX sensor10 current sample',read(base+0x444)&511,150)
            for bank,base in enumerate((0x10090000,0x10094000)):
                write(base+0x20,0);check('Disable withdraws sample'+str(bank),read(base+0x28),0)
            check('Both banks disabled withdraw global valid',get('sample-valid'),0)
            before=get('sample-completions');temperature(70);step(3000000)
            check('Disabled banks do not convert',get('sample-completions'),before)
            for bank,base in enumerate((0x10090000,0x10094000)):
                write(base+0x20,1)
                check('Enable does not invent ready'+str(bank),read(base+0x28),0)
            step(50);check('Reenabled real new sample',read(0x10090040)&511,195)
                                                                                              
            for base in (0x10090000,0x10094000):
                write(base+0x24,0xb0000);write(base+0x2c,24);write(base+0x38,0x13)
                for off,value in ((0x24,0xb0000),(0x2c,24),(0x38,0x13)):
                    check('Original config roundtrip '+hex(base+off),read(base+off),value)
                for sensor in range(12 if base==0x10090000 else 11):
                    threshold=0x50 if sensor==0 else 32*sensor+(0x150 if sensor<=7 else 0x350)
                    irq=16*sensor+(0x110 if sensor<=4 else 0x2c0 if sensor<=7 else 0x5d0)
                    for pair in range(8):
                        value=(180+pair)|((200+pair)<<16)
                        write(base+threshold+4*pair,value)
                        check('Finite threshold pair '+hex(base+threshold+4*pair),read(base+threshold+4*pair),value)
                    write(base+irq,0);write(base+irq+8,0xffff01ff)
                    check('Original W1C clear '+hex(base+irq+8),read(base+irq+8),0)
                                                                                        
            temperature(25);step(1000000)
            for base,spi in ((0x10090000,462),(0x10094000,461)):
                write(base+0x118,0xffffffff);write(base+0x5c,170);write(base+0x6c,160)
                temperature(70);mark=len(events);step(1000000)
                check('Crossing latches pending with mask off '+str(spi),read(base+0x118)&1,1)
                check('Masked crossing not delivered '+str(spi),'IRQ raise '+str(spi) in events[mark:],False)
                mark=len(events);write(base+0x110,1)
                check('Unmask real GIC input '+str(spi),'IRQ raise '+str(spi) in events[mark:],True)
                mark=len(events);write(base+0x118,1)
                check('W1C lowers actual GIC input '+str(spi),'IRQ lower '+str(spi) in events[mark:],True)
                step(1000000);check('No relatch without crossing '+str(spi),read(base+0x118)&1,0)
                write(base+0x110,0x10000);temperature(25);mark=len(events);step(1000000)
                check('Falling crossing is actual GIC input '+str(spi),'IRQ raise '+str(spi) in events[mark:],True)
                check('Fall bit16 actual pending '+str(spi),read(base+0x118)&0x10000,0x10000)
                mark=len(events);write(base+0x20,0)
                check('Disable drops actual IRQ '+str(spi),'IRQ lower '+str(spi) in events[mark:],True)
                write(base+0x118,0xffffffff);write(base+0x110,0);write(base+0x20,1);step(50)
            rejected=get('rejected-accesses')
            write(0x10090000,0);read(0x10090030);write(0x10090020,0x80000001)
            check('ROM, unsupported hole and control bits reject',get('rejected-accesses'),rejected+3)
            check('Rejected control preserves old state',read(0x10090020),1)
            qmp.execute('system_reset')
            reset_status=qmp.execute('query-status');vm.save(output/'reset-status.json',reset_status)
            if not reset_status.get('running'):qmp.execute('cont')
            check('Reset withdraws validity',get('sample-valid'),0)
            check('Reset clears IRQ accounting',get('irq-raises'),0)
            vm.save(output/'reset-timing-before.json',{n:get(n) for n in ('virtual-time-ns','bank0-deadline-ns','bank1-deadline-ns')})
            step(50)
            vm.save(output/'reset-timing-after.json',{n:get(n) for n in ('virtual-time-ns','bank0-deadline-ns','bank1-deadline-ns')})
            check('Reset actual two-bank conversions',get('sample-completions'),2)
            return {'checks':checks,'passed':True,'runtime':runtime_state(qmp),'boot_verified':False}
        finally:
            vm.save(output/'qtest-transcript.json',transcript);vm.save(output/'checks.json',checks);vm.save(output/'gic-events.json',events)
            if connection:connection.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--program',type=Path,default=DEFAULT_PROGRAM);p.add_argument('--qemu',type=Path);p.add_argument('--check-model',action='store_true')
    a=p.parse_args();out=a.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new scratch output')
    out.mkdir(parents=True);vm.save(out/'original-contract.json',original_contract(a.program));vm.save(out/'virtual-policy.json',profile_policy())
    if a.check_model:vm.save(out/'result.json',check_model(out,a.qemu))
    print(str(out))


if __name__=='__main__':main()
