
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import urllib.request
import original_8704_vm as vm
import vlm_platform as platform


def original_contract():
    path=platform.factory_file('qnx/qtd/icas3gp-qla-vm.dtb')
    nodes=platform.parse_dtb(path.read_bytes());props=nodes['/i2c_17']
    def text(name):return props[name].rstrip(b'\0').decode('ascii')
    if int(text('reg'),16)!=0x10950000 or int(text('usi_reg'),16)!=0x10821014:
        raise ValueError('Original QNX I2C17 resource mismatch')
    hcb=platform.original('hcb/hcb.img');raw=hcb.read_bytes();links=[]
                                                                           
                                                                           
    for offset in range(0,len(raw)-40,8):
        if raw[offset:offset+4]!=b'\xd0\x0d\xfe\xed':continue
        size=struct.unpack_from('>I',raw,offset+4)[0]
        if not 40<=size<=len(raw)-offset:continue
        tree=platform.parse_dtb(raw[offset:offset+size])
        for name,values in tree.items():
            if name.endswith(('/vl,usi08_sub_i2c@mem','/vl,usi09@mem')):
                irq=tree[name.removesuffix('@mem')+'@irq']
                links.append({'dt_offset':hex(offset),'path':name,'properties':{k:platform.render(v) for k,v in values.items()},
                              'irq_properties':{k:platform.render(v) for k,v in irq.items()}})
    return {'qnx_dtb':{'path':str(path),'sha256':vm.sha(path),'node':'/i2c_17',
            'properties':{k:platform.render(v) for k,v in props.items()}},
            'qnx_i2c_nodes':{n:{k:platform.render(v) for k,v in p.items()} for n,p in nodes.items() if n.startswith('/i2c_')},
            'hcb':{'path':str(hcb),'sha256':vm.sha(hcb),'matching_views':links},
            'behavior':'Original init writes USI selector0 then4 and controller C4 reset1/0; I2C_CTL reset then master8; real bus needed, no generic ACK endpoint',
            'silicon_timing_verified':False,'physical_endpoint_verified':False}


def fetch_public(output):
    records=[]
    for name,url in (
        ('i2c-exynos5.c','https://raw.githubusercontent.com/torvalds/linux/master/drivers/i2c/busses/i2c-exynos5.c'),
        ('exynos-usi.c','https://raw.githubusercontent.com/torvalds/linux/master/drivers/soc/samsung/exynos-usi.c')):
        request=urllib.request.Request(url,headers={'User-Agent':'MEB-local-source-verification'})
        with urllib.request.urlopen(request,timeout=30) as response:data=response.read()
        path=output/name;path.write_bytes(data)
        records.append({'url':url,'path':str(path),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                        'scope':'Public same-family coding evidence, not identical Auto9 hardware specification'})
    return records


def check_model(output,qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qp,tp,serial_port=vm.free_port(),vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128M','-display','none',
             '-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-chardev',f'socket,id=usi09-test,host=127.0.0.1,port={serial_port},server=on,wait=off',
             '-serial','chardev:usi09-test',
             '-device','at24c-eeprom,bus=i2c17,address=0x50,rom-size=256',
             '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),
        'scope':'Actual virtual controller and upstream EEPROM fixture only; no factory CPU execution or native display chip emulation'})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];transcript=[];events=[];qmp=connection=serial_connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('I2C QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('I2C QMP timeout')
            connection=socket.create_connection(('127.0.0.1',tp),timeout=5);stream=connection.makefile('rwb',buffering=0)
            def test(command):
                stream.write((command+'\n').encode())
                while True:
                    reply=stream.readline().decode().strip();transcript.append({'command':command,'reply':reply})
                    if reply.startswith('IRQ '):events.append(reply);continue
                    if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                    return reply
            def read(a):return int(test(f'readl {a:#x}').split()[1],16)
            def write(a,v):test(f'writel {a:#x} {v:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(n):return qmp.execute('qom-get',{'path':'/machine/exynos-i2c17','property':n})
            def set_timing(n):qmp.execute('qom-set',{'path':'/machine/exynos-i2c17','property':'virtual-byte-ns','value':n})
            def check(n,a,e):
                checks.append({'name':n,'actual':a,'expected':e,'passed':a==e})
                if a!=e:raise ValueError('I2C check failed: '+n)
            b,u=0x10950000,0x10821014;period=22500
            children=qmp.execute('qom-list',{'path':'/machine/unattached'})
            gic=next(row['name'] for row in children if row['type']=='child<arm_gic>')
            test('irq_intercept_in /machine/unattached/'+gic)
            serial_connection=socket.create_connection(('127.0.0.1',serial_port),timeout=3)
            qmp.execute('cont')
            def init():
                write(u,0);write(b+0xc4,1);write(b+0xc4,0);write(u,4)
                write(b+0xc4,1);write(b+0xc4,0)
                write(b,0x80000000);write(b,0x7fffffff);write(b,8)
                write(b+8,0xffffff);write(b+0x40,0);write(b+0x48,0);write(b+4,0x80083)
            def run(address,length,reading=False,stop=True):
                write(b,(read(b)&~0xc0)|(0x40 if reading else 0x80))
                write(b+0x70,address<<10);write(b+0x44,0x80000000|(0x10000 if reading else 0)|(0x20000 if stop else 0)|length)
            def tx(data,stop=True):
                for value in data:write(b+0x34,value)
                run(0x50,len(data),stop=stop);step(period*(len(data)+1))
            check('Declared virtual cold selector',read(u),0)
            check('Real empty FIFO packing',read(b+0x30),0x1000100)
            run(0x50,1);check('Disabled selector does not run',get('transfers-started'),0)
            init();check('Original selector sequence retains I2C4',read(u),4)
            for off in (8,0x40,0x48,0x54,0x58,0x5c,0x60,0x64,0x68,0x6c,0x70):
                write(b+off,0x123400);check('Known config readback '+hex(off),read(b+off),0x123400)
            init();run(0x51,1);check('No NACK before address deadline',read(b+0x24),0)
            for _ in range(5):read(b+0x24)
            step(period-1);check('Polling does not complete bus address',get('nacks'),0)
            step(1);check('Missing peer NACK comes from actual I2C bus',read(b+0x24),0x400)
            check('NACK is not success',get('transfers-completed'),0)
            check('NACK returns bus idle',read(b+0x50),0)
            mark=len(events);write(b+0x20,0x400)
            check('Unmask latched NACK reaches actual GIC SPI361','IRQ raise 361' in events[mark:],True)
            mark=len(events);write(b+0x24,0x400)
            check('W1C lowers actual GIC SPI361','IRQ lower 361' in events[mark:],True)
            init();write(b+0x20,0x80);write(b+0x34,0x20);write(b+0x34,0xa5);write(b+0x34,0x5a)
            run(0x50,3);step(period*3)
            check('Completion waits for all data bytes',get('transfers-completed'),0)
            step(period);check('Real EEPROM write completes',get('transfers-completed'),1)
            check('Write completion is an actual GIC SPI361','IRQ raise 361' in events,True);write(b+0x24,0x80)
            tx([0x20],stop=False);check('No STOP holds bus for repeated START',read(b+0x50),0x20000)
            run(0x50,2,reading=True);step(period*3)
            check('Read FIFO contains actual two EEPROM bytes',(read(b+0x30)>>16)&127,2)
            check('First data byte round-trips actual slave',read(b+0x38),0xa5)
            check('Second data byte round-trips actual slave',read(b+0x38),0x5a)
            check('STOP releases owned bus',read(b+0x50),0)
            tx([0x20],stop=False)
            write(b+0x44,0x80030001);step(period)
            check('Address phase completes before RX enable',(read(b+0x30)>>16)&127,0)
            write(b,0x48);step(period)
            check('Original QNX RUN-then-RXCHON sequence transfers actual byte',read(b+0x38),0xa5)
            second=0x10960000;second_u=0x10821018
            write(second_u,1);check('Observed USI09 UART mode selected',read(second_u),1)
            check('Real upstream UART TX empty state',read(second+0x10)&6,6)
            write(second+4,5);write(second+8,1);write(second+0x38,0xe)
            test(f'writeb {second+0x20:#x} 0x55')
            check('Actual UART transmits to real host socket',list(serial_connection.recv(1)),[85])
            mark=len(events);serial_connection.sendall(b'R')
            deadline=time.monotonic()+3
            while not read(second+0x10)&1 and time.monotonic()<deadline:time.sleep(.01)
            check('Host RX reaches actual UART FIFO',read(second+0x10)&1,1)
            check('UART RX reaches actual GIC SPI364','IRQ raise 364' in events[mark:],True)
            check('Actual upstream UART RX byte',int(test(f'readb {second+0x24:#x}').split()[1],16),ord('R'))
            write(second+0xc4,1);write(second+0xc4,0)
            check('USI reset actually clears upstream RX FIFO',read(second+0x10)&1,0)
            check('I2C17 selector is independent of USI09 UART',read(u),4)
            before=qmp.execute('qom-get',{'path':'/machine/exynos-usi09','property':'rejected-accesses'})
            write(second_u,4)
            check('Unsupported I2C personality does not silently become UART',qmp.execute('qom-get',{'path':'/machine/exynos-usi09','property':'rejected-accesses'}),before+1)
            check('Invalid selection retains UART personality',read(second_u),1)
            read(b+0x38);check('Empty FIFO raises underrun, not invented data',read(b+0x24)&0x10,0x10)
                                                                                    
            init();payload=[(i*7+3)&255 for i in range(100)];write(b+0x34,0x40)
            for value in payload[:63]:write(b+0x34,value)
            check('FIFO full bit at64',read(b+0x30)&0xff,0xc0)
            rejected=get('rejected-accesses');write(b+0x34,0xaa)
            check('FIFO overrun rejects extra write',get('rejected-accesses'),rejected+1)
            run(0x50,101);step(period*65)
            check('Empty TX stalls rather than invent bytes',read(b+0x50)&0x20000,0x20000)
            for value in payload[63:]:write(b+0x34,value)
            step(period*37);check('Stream write really drains all bytes',read(b+0x30)&0x1ff,0x100)
            tx([0x40],stop=False);run(0x50,100,reading=True);step(period*70)
            check('RX full stalls real transfer',(read(b+0x30)>>16)&0xff,0xc0)
            check('RX full is not premature completion',read(b+0x50)&0x20000,0x20000)
            result=[read(b+0x38) for _ in range(64)];step(period*36)
            result += [read(b+0x38) for _ in range(36)]
            check('100 bytes cross both real FIFOs and EEPROM',result,payload)
            check('FIFO backpressure resumes to actual STOP',read(b+0x50),0)
            init();run(0x50,1);step(period);check('Empty writer really holds active bus',read(b+0x50)&0x20000,0x20000)
            rejected=False
            try:set_timing(1)
            except (RuntimeError,ValueError):rejected=True
            check('Active transfer cannot change clock under deadline',rejected,True)
            old=get('cancelled');write(b+0xc4,1);step(period*10)
            check('USI reset cancels actual pending transfer',get('cancelled'),old+1)
            check('Cancelled timer never creates completion',read(b+0x24),0)
            init();write(b+0x48,0x80000001);run(0x50,1);step(period*2)
            check('Enabled virtual timeout ends stalled TX',read(b+0x24),0x800)
            check('Timeout releases actual bus',read(b+0x50),0)
            for value in (0,1000000001):
                rejected=False
                try:set_timing(value)
                except (RuntimeError,ValueError):rejected=True
                check('Timing bound '+str(value),rejected,True)
            set_timing(1000);check('Idle timing is explicit external parameter',get('virtual-byte-ns'),1000)
            before=get('rejected-accesses');write(u,1);read(b+0x14);write(b+0x4c,1)
            check('Unknown selector/hole/manual commands reject',get('rejected-accesses'),before+3)
            init();run(0x50,1);qmp.execute('system_reset');step(1000000)
            check('System reset clears selector',read(u),0)
            check('System reset cancels engine',read(b+0x50),0)
            check('System reset cancels pending IRQ',read(b+0x24),0)
            check('Reset leaves actual FIFOs empty',read(b+0x30),0x1000100)
            return {'passed':True,'checks':checks,'irq_events':events,'factory_boot_verified':False,
                    'scope':'Real QEMU I2C/EEPROM/FIFOs/timers/GIC and USI09 upstream UART/socket TX/RX only; no native endpoint or shell/display acceptance'}
        finally:
            vm.save(output/'checks.json',checks);vm.save(output/'qtest-transcript.json',transcript);vm.save(output/'irq-events.json',events)
            if connection:connection.close()
            if serial_connection:serial_connection.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--fetch-public',action='store_true')
    parser.add_argument('--check',action='store_true');parser.add_argument('--qemu',type=Path)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True);vm.save(output/'original-contract.json',original_contract())
    if args.fetch_public:vm.save(output/'public-sources.json',fetch_public(output))
    if args.check:vm.save(output/'result.json',check_model(output,args.qemu))
    print(str(output/'original-contract.json'))


if __name__=='__main__':main()
