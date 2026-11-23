




import argparse
import os
from pathlib import Path
import socket
import subprocess
import time
import original_8704_vm as vm


def machine_options(profile, cycles=64, acpm_clock_hz=0):
    if profile is None:
        if cycles != 64:
            raise ValueError('PSCDC latency requires an explicit behavioral profile')
        return ''
    if profile != 'virtual-oem-v1' or not 1000000 <= acpm_clock_hz <= 1000000000:
        raise ValueError('PSCDC behavior requires virtual-oem-v1 and the native management core')
    if not 1 <= cycles <= 1000000:
        raise ValueError('PSCDC virtual latency must be 1..1000000 reference cycles')
    return f',x-exynos-pscdc-profile=1,x-exynos-pscdc-cycles={cycles}'


def profile_policy(profile, cycles):
    return {'profile':profile, 'virtual_latency_reference_cycles':cycles,
            'silicon_instruction_semantics_verified':False,
            'physical_transition_timing_verified':False,
            'factory_images_modified':False, 'runtime_ready_value_injected':False,
            'assumptions':[
                'Zero queue word is an empty slot in this virtual profile',
                '10000 and 80020000 open a logical memory-clock transaction envelope',
                '80040000 commits the armed replica; 80030000 closes the envelope',
                'Known 12xx TOP operations update source-bound config fields',
                'Replica600 selects the four declared MIF DDRPHY2X parents',
                'ReplicaA08 latches a virtual auto-gating policy, not an electrical PHY gate',
                'Logical pause does not implement analog DRAM state or physical bus stalling',
                'Completion follows actual virtual time and available selected clock sources',
            ],
            'bounds':'Only original observed 211/241/271 requests and bounded words; all inputs validate before work starts; unknown words reject'}


def check_model(output, qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qmp_port, test_port=vm.free_port(),vm.free_port()
    cycles=26000                                                                      
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000'+machine_options('virtual-oem-v1',cycles,96000000),
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128',
             '-display','none','-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),
                                'scope':'Actual QEMU peripheral checks; no guest code or HMI acceptance'})
    vm.save(output/'virtual-policy.json',profile_policy('virtual-oem-v1',cycles))
    environment=os.environ.copy()
    environment['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+environment.get('PATH','')
    transcript=[];checks=[];qmp=connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=environment,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('PSCDC QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('PSCDC QMP startup timeout')
                                                                         
                                                                             
            qmp.execute('cont')
            connection=socket.create_connection(('127.0.0.1',test_port),timeout=5)
            stream=connection.makefile('rb')
            def test(text):
                connection.sendall(text.encode()+b'\n')
                line=stream.readline(4096).decode().strip()
                transcript.append({'command':text,'response':line})
                if not line.startswith('OK'):raise ValueError('QTest response: '+line)
                return line
            def read(address):return int(test(f'readl {address:#x}').split()[1],16)
            def write(address,value):test(f'writel {address:#x} {value:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(name):return qmp.execute('qom-get',{'path':'/machine/exynos-cmu','property':name})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('PSCDC check failed: '+name)
            def replica(select=3,automatic=0):
                write(0x1b243e10,0x12000600|(select<<20))
                write(0x1b243e14,0x10000a08|(automatic<<16))
            words=[0,0x10000,0,0x12001020,0x1200181c,0x80040000,0x1207109c,0x80030000]
            def stage(values):
                write(0x1b243c00,0)
                for i,word in enumerate(values):write(0x1b243c10+4*i,word)
                write(0x1b243e00,0x10)
            check('Explicit profile enabled',get('pscdc-profile'),1)
            check('SYSREG configuration virtual reset',read(0x1b221004),0)
            write(0x1b221004,0xaa00000f)
            check('SYSREG actual configuration retention',read(0x1b221004),0xaa00000f)
            for address,value in ((0x1b241880,0),(0x1b241088,2),(0x1b241814,1),
                                  (0x1b241824,2),(0x1b2418ac,1),(0x1b2418b0,1),(0x1bf01000,1),
                                  (0x17701004,1),(0x17041800,19),(0x17c01008,1),
                                  (0x10201818,1),(0x10801018,1)):
                write(address,value)
                check('Original remaining CMU config '+hex(address),read(address),value)
                                                                                     
            for index,m,p,s in ((0,328,4,0),(3,500,13,0)):
                write(0x1b240000+4*index,150*p)
                write(0x1b24010c+0x40*index,0x80000000|(m<<16)|(p<<8)|s)
                write(0x1b240100+0x40*index,0x10)
            for base in (0x1b400000,0x1b600000,0x1c400000,0x1c600000):
                write(base+4,750);write(base+0x14c,0x80000000|(316<<16)|(5<<8)|5);write(base+0x140,0x10)
            write(0x1b2418b8,1);step(100000)
            check('Actual switch parent before request',get('mif-switch-mux-hz'),2132000000)
            replica();stage(words);write(0x1b243c00,0x271)
            for i in range(3):check('Read does not complete request '+str(i),read(0x1b243c00)&0x100,0x100)
            check('Config busy derives from queued work',read(0x1b24109c)&0x10000,0x10000)
            check('No clock effect before deadline',get('mif-switch-mux-hz'),2132000000)
            step(500000);check('Half deadline still busy',read(0x1b243c00)&0x100,0x100)
            step(500001);check('Deadline completed request',read(0x1b243c00)&0x100,0)
            check('Queue completion counted',get('queue-completions'),1)
            check('Replica completion counted',get('mirror-completions'),1)
            check('Actual TOP mux changed',get('mif-switch-mux-hz'),1000000000)
            for i,base in enumerate((0x1b400000,0x1b600000,0x1c400000,0x1c600000)):
                check('MIF selected field '+str(i),read(base+0x600),0x30)
                check('MIF actual Clock frequency '+str(i),get(f'mif-ddrphy2x{i}-ungated-hz'),51350000)
                check('MIF actual Clock period '+str(i),get(f'mif-ddrphy2x{i}-ungated-period'),(1000000000<<32)//51350000)
            bad=words.copy();bad[1]=0xdeadbeef;stage(bad);write(0x1b243c00,0x271)
            check('Unknown word rejected',get('queue-rejected-starts'),1)
            check('Rejected request has no work',read(0x1b243c00)&0x100,0)
            check('Rejected request no partial clock effect',get('mif-switch-mux-hz'),1000000000)
            changed=words.copy();changed[6]=0x1200109c;stage(changed);write(0x1b243c00,0x271)
            step(100000);write(0x1b243c00,0);step(1000000)
            check('Cancellation removes scheduled work',get('queue-cancellations'),1)
            check('Cancelled request cannot complete',get('queue-completions'),1)
            check('Cancelled request did not reparent',get('mif-switch-mux-hz'),1000000000)
            replica(2,1);write(0x1b243e00,0x11)
            check('Standalone replica reports busy',read(0x1b243e30),1)
            step(1000001);check('Standalone replica completes',read(0x1b243e30),0)
            check('Virtual auto-gating policy latched',get('phy-auto-gate-mask'),15)
            for i in range(4):check('Main PLL bypass parent '+str(i),get(f'mif-ddrphy2x{i}-ungated-hz'),26000000)
            for base in (0x1b400000,0x1b600000,0x1c400000,0x1c600000):write(base+0x14c,0)
            replica(3);write(0x1b243e00,0x11);step(1000001)
            check('Missing real clock keeps request busy',read(0x1b243e30),1)
            check('Clock wait does not fabricate completion',get('mirror-completions'),2)
            check('Clock wait tracked',get('queue-clock-waits')>0,True)
            for base in (0x1b400000,0x1b600000,0x1c400000,0x1c600000):write(base+0x14c,0x80000000|(316<<16)|(5<<8)|5)
            step(2000001);check('Actual PLL repair permits work',read(0x1b243e30),0)
            check('Repaired source completion counted',get('mirror-completions'),3)
            replica();write(0x1b243e00,0x11);qmp.execute('system_reset');step(2000001)
            check('Reset cancels pending timer',get('mirror-completions'),0)
            check('Reset clears queue work',get('queue-completions'),0)
            check('Reset retains explicit profile',get('pscdc-profile'),1)
            check('Reset clears virtual SYSREG configuration',read(0x1b221004),0)
            check('Declared PLL engine count',get('pll-count'),22)
            index=next(i for i in range(get('pll-count')) if get(f'pll{i}-base')==0x1d020000)
            check('CPU cluster PLL virtual reset is disabled',read(0x1d02010c),0)
            write(0x1d020000,600);write(0x1d020100,0x10);write(0x1d02010c,0x81480400)
            check('CPU PLL does not lock on write',read(0x1d02010c)&0x20000000,0)
            step(23077)
            check('CPU PLL actual programmed-cycle lock',read(0x1d02010c)&0x20000000,0x20000000)
            check('CPU PLL actual QEMU clock output',get(f'pll{index}-hz'),2132000000)
            for address,value in ((0x1d0230b4,6),(0x1d020804,0xf0000000),
                                  (0x1d020108,0x8000000f),(0x1d020820,0xffffffff)):
                write(address,value)
                check('Original CPU initialization configuration '+hex(address),read(address),value)
            audio=next(i for i in range(get('pll-count')) if get(f'pll{i}-base')==0x19c00000)
            write(0x19c00000,3000);write(0x19c00114,0xcf29);write(0x19c00100,0x10);write(0x19c0010c,0x80260100)
            step(115385)
            expected=26000000*((38<<16)+(0xcf29-65536))//65536
            check('Audio negative signed K is calculated',get(f'pll{audio}-hz'),expected)
            check('Audio actual fractional Clock period',qmp.execute('qom-get',{'path':f'/machine/exynos-cmu/pll{audio}','property':'qtest-clock-period'}),(1000000000<<32)//expected)
            write(0x19c00114,0x1000)
            check('K change withdraws old clock before relock',get(f'pll{audio}-hz'),0)
            step(115385)
            check('K change really retunes output',get(f'pll{audio}-hz'),989625000)
            for address,value in ((0x19200800,0xf0000000),(0x19400800,0xf0000000),
                                  (0x19600800,0xf0000000),(0x19600804,0xf0000000),
                                  (0x19c00850,1),(0x19c00a00,1)):
                write(address,value)
                check('Original auxiliary initialization configuration '+hex(address),read(address),value)
            for control,packet in ((0x211,[0x10000,0x80030000]),
                                   (0x241,[0,0x10000,0x12021020,0x1201181c,0x80030000])):
                stage(packet);write(0x1b243c00,control)
                check('Bounded count request actually starts '+hex(control),read(0x1b243c00)&0x100,0x100)
                step(1000001)
                check('Bounded count request completes '+hex(control),read(0x1b243c00)&0x101,0)
                check('Count request applies actual field '+hex(control),read(0x1b241020),0 if control==0x211 else 2)
            write(0x1b243c00,0x261)
            check('Unobserved control count remains rejected',get('queue-rejected-starts'),1)
            write(0x1b400600,0)
            check('Direct MIF configuration changes actual clock',get('mif-ddrphy2x0-ungated-hz'),26000000)
            write(0x1b400a08,1)
            check('Direct gate policy derives actual bank mask',get('phy-auto-gate-mask'),1)
            return {'checks':checks,'passed':True,'guest_boot_verified':False,
                    'scope':'Real QEMU virtual-profile transaction, timing, clock, rejection, cancellation and reset checks only'}
        finally:
            vm.save(output/'qtest-transcript.json',transcript)
            vm.save(output/'checks.json',checks)
            if connection:connection.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate();process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--qemu',type=Path)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():
        raise ValueError('Require a new scratch output directory')
    output.mkdir(parents=True)
    result=check_model(output,args.qemu);vm.save(output/'result.json',result)
    print(str(output/'result.json'))


if __name__=='__main__':main()
