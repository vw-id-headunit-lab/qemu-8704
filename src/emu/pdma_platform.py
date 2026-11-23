
import argparse
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import original_8704_vm as vm


def check_model(output, qemu=None, mcgr_idle=False):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qp,tp=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,nvdimm=on',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128M,slots=3,maxmem=64G',
             '-display','none','-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    if mcgr_idle:
        command[2]+=',x-exynos-eqos-mac=1099511924482'
    for bank in (1,2,3):
        command+=['-object',f'memory-backend-ram,id=high{bank},size=2M',
                  '-device',f'nvdimm,memdev=high{bank},addr={hex((bank<<32)|0x40000000)}']
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),
        'scope':'Real virtual device transactions, declared wrapper prefixes; no factory CPUs executed'})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];transcript=[];qmp=connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('PDMA QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('PDMA QMP timeout')
            qmp.execute('cont');connection=socket.create_connection(('127.0.0.1',tp),timeout=5)
            stream=connection.makefile('rwb',buffering=0)
            def test(command):
                stream.write((command+'\n').encode());reply=stream.readline().decode().strip()
                transcript.append({'command':command,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                return reply
            def read(a):return int(test(f'readl {a:#x}').split()[1],16)
            def write(a,v):test(f'writel {a:#x} {v:#x}')
            def put(a,data):test(f'write {a:#x} {len(data):#x} 0x'+data.hex())
            def get(n):return qmp.execute('qom-get',{'path':'/machine/exynos-pdma','property':n})
            def check(n,a,e):
                checks.append({'name':n,'actual':a,'expected':e,'passed':a==e})
                if a!=e:raise ValueError('PDMA check failed: '+n)
            p=0x1b2e0000;v=0x1b250000;s=0x1b2f0000
            src,dst,pc=0x40001000,0x40002000,0x40003000
            def permission(bank,value):
                off=0x4600+((bank<<2)|1)*8
                write(s+off,0);write(s+off+4,value);write(s+0x1000,1)
            def configure():
                for slot in range(16):write(v,slot);write(v+4,3)
                for bank in (0,1,2,3):permission(bank,6)
                write(s,3)
            def start(ch,program=pc):
                write(p+0xd08,(0xa0<<16)|(ch<<24));write(p+0xd0c,program);write(p+0xd04,0)
                test('clock_step 1000')
            def kill(ch):
                write(p+0xd08,(1<<16)|(ch<<8)|1);write(p+0xd04,0);test('clock_step 1000')
                check('KILL clears channel fault '+str(ch),read(p+0x40+4*ch),0)
            def program(zero=False, event=False):
                code=b'\xbc\x00'+struct.pack('<I',src)+b'\xbc\x02'+struct.pack('<I',dst)
                code+=b'\xbc\x01'+struct.pack('<I',0x14005)+ (b'\x0c' if zero else b'\x04\x08')
                                                                                  
                return code+b'\x13'+(b'\x34\x00' if event else b'')+b'\x00'
            configure()
            if mcgr_idle:
                engine='/machine/exynos-pdma/engine'
                check('Original MCGR is the only idle input',
                      qmp.execute('qom-get',{'path':engine,'property':'x-auto9-idle-peripheral-mask'}),1<<22)
                write(src,0x5a1100ff);write(dst,0x11223344)
                put(pc,bytes((0x31,22<<3))+program(event=True))
                write(p+0x20,1);start(0)
                check('Real DMAWFP waits on inactive MCGR input',read(p+0x100)&15,7)
                check('Inactive request does not read or complete',read(dst),0x11223344)
                check('Inactive request does not raise success event',read(p+0x24)&1,0)
                test(f'set_irq_in {engine} unnamed-gpio-in 22 0')
                test('clock_step 1000')
                check('Test request resumes real PL330 instruction stream',read(dst),0x5a1100ff)
                check('Only actual transfer generates completion',read(p+0x24)&1,1)
                write(p+0x2c,1);write(p+0x20,0)
                qmp.execute('system_reset')
                configure()
                put(pc,bytes((0x31,22<<3,0)));start(0)
                check('Reset restores inactive input',read(p+0x100)&15,7)
                kill(0)
                put(pc,bytes((0x31,21<<3,0)));start(0)
                check('Other peripheral reset behavior unchanged',read(p+0x100)&15,0)
            check('Native engine explicitly opts into class/channel metadata',
                  qmp.execute('qom-get',{'path':'/machine/exynos-pdma/engine','property':'x-auto9-sideband'}),True)
            for ch in range(8):
                write(p+0x4400+ch*32,ch%4);write(p+0x4404+ch*32,(ch+1)%4)
                check('AR independent register '+str(ch),read(p+0x4400+ch*32),ch%4)
                check('AW independent register '+str(ch),read(p+0x4404+ch*32),(ch+1)%4)
            write(p+0x4500,3);check('Instruction wrapper at factory DT address',read(p+0x4500),3)
            for ch in range(8):
                ib,rb,wb=(ch+2)%4,ch%4,(ch+1)%4
                write(p+0x4500,ib);write(p+0x4400+ch*32,rb);write(p+0x4404+ch*32,wb)
                                                                               
                for bank in range(4):
                    put((bank<<32)|pc,b'\xff'*32);write((bank<<32)|src,0xdeadbeef);write((bank<<32)|dst,0xaaaaaaaa)
                value=0x12345670+ch
                put((ib<<32)|pc,program());write((rb<<32)|src,value)
                start(ch)
                check('Channel copies across distinct 36-bit AR/AW banks '+str(ch),read((wb<<32)|dst),value)
                check('Channel terminates without fault '+str(ch),read(p+0x40+4*ch),0)
                check('DMAEND reaches stopped state '+str(ch),read(p+0x100+ch*8)&15,0)
                for bank in range(4):
                    if bank!=wb:check('No destination alias write ch'+str(ch)+' bank'+str(bank),read((bank<<32)|dst),0xaaaaaaaa)
                                                                               
            write(p+0x4500,1);write(p+0x4400,2);write(p+0x4404,3)
            put((1<<32)|pc,program(zero=True));write((3<<32)|dst,0xffffffff)
            start(0);check('DMASTZ uses 36-bit AW prefix',read((3<<32)|dst),0)
            put((1<<32)|pc,program(event=True));write(p+0x20,1)
                                                                                      
            for name,bank,perm,flag in [('fetch',1,0,1<<16),('read',2,0,1<<18),('write',3,2,1<<17)]:
                permission(bank,perm);write((3<<32)|dst,0x55555555);start(0)
                check(name+' error is reported by real PL330 fault register',read(p+0x40),flag)
                check(name+' denial prevents destination completion',read((3<<32)|dst),0x55555555)
                check(name+' denial does not generate success event',read(p+0x24)&1,0)
                check(name+' denied transaction faults actual VID3',read(s+0x2000)&8,8)
                check(name+' fault retains actual high address',read(s+0x2068),bank)
                kill(0);permission(bank,6);write(s+0x2c,8)
                check('Original VID3 W1C clears the first fault latch',read(s+0x2000)&8,0)
            start(0);check('KILL and permission repair allow new real transfer',read((3<<32)|dst),0xdeadbeef)
            check('Successful repaired transfer emits event',read(p+0x24)&1,1);write(p+0x2c,1)
                                                                             
                                                                               
            write(p+0x44e0,15);write(p+0x44e4,3);permission(15,6)
            check('Full 4-bit AR prefix is retained',read(p+0x44e0),15)
            write((3<<32)|dst,0x77777777);start(7)
            check('Unmapped 36-bit data read faults channel7',read(p+0x40+7*4),1<<18)
            check('Unmapped high address never aliases to low RAM',read((3<<32)|dst),0x77777777)
            kill(7)
                                                                            
                                                                                
            edge=0x401fffff;put((1<<32)|edge,b'\xbc');start(7,edge)
            check('Partial instruction transaction causes FETCH, not UNDEF',read(p+0x40+7*4),1<<16)
            check('Failed instruction operands do not advance PC',read(p+0x104+7*8),edge)
            kill(7)
            write(v,15);write(v+4,2);write((3<<32)|dst,0xcccccccc);start(0)
            check('Mixed VGEN contexts cause actual fetch fault',read(p+0x40),1<<16)
            check('Mixed VID never silently copies using a guessed context',read((3<<32)|dst),0xcccccccc)
            kill(0);write(v,15);write(v+4,3)
            rejected=get('rejected-accesses');write(p+0x4500,16)
            check('Invalid prefix does not truncate into valid address',read(p+0x4500),1)
            write(p+0x4408,1);read(p+0x4504)
            check('Reserved wrappers and high nibble overflow explicitly reject',get('rejected-accesses'),rejected+3)
                                                                           
                                                                             
            put((1<<32)|pc,b'\x36\xf8\x00');start(0)
            check('Actual DMAWFE is pending before reset',read(p+0x100)&15,4)
            qmp.execute('system_reset')
            check('Reset clears instruction prefix',read(p+0x4500),0)
            for ch in range(8):
                check('Reset clears AR '+str(ch),read(p+0x4400+ch*32),0)
                check('Reset clears AW '+str(ch),read(p+0x4404+ch*32),0)
                check('Reset clears channel state '+str(ch),read(p+0x100+8*ch)&15,0)
            test('clock_step 100000');check('Reset leaves engine fault-free',read(p+0x34),0)
            return {'passed':True,'checks':checks,'factory_boot_verified':False,
                    'scope':'Behavior-level 36-bit wrappers, actual upstream instruction/data transfers and propagated permission faults; not silicon AxID, migration or factory system boot'}
        finally:
            vm.save(output/'checks.json',checks);vm.save(output/'qtest-transcript.json',transcript)
            if connection:connection.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--qemu',type=Path)
    parser.add_argument('--mcgr-idle',action='store_true',help='Verify the inactive factory EQoS request and GPIO release fixture')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True);vm.save(output/'result.json',check_model(output,args.qemu,args.mcgr_idle));print(str(output/'result.json'))


if __name__=='__main__':main()
