
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import original_8704_vm as vm
import vlm_platform as platform


def om_input_policy(value, native_qnx):

    if value is None:
        return None
    if not native_qnx or type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError('PMU OM input needs native QNX/PMU/HCB and a 32-bit board value')
    raw=platform.original('hcb/hcb.img').read_bytes()
    node='/vplatform/vl,pmu_system_controller@mem'
    for vmid in (2,3,4):
        off=raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        size=struct.unpack_from('>I',raw,off+4)[0]
        props=platform.parse_dtb(raw[off:off+size])[node]
        if (props['reg'][:12]!=struct.pack('>QI',0x10460000,0x700)
                or props['vl,alignment']!=struct.pack('>I',4)
                or ('vl,disabled' in props)!=(vmid!=2)):
            raise ValueError(f'Unexpected OEM VM{vmid} PMU grant')
    source=platform.factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
    with source.open('rb') as f:
        f.seek(16412672)
        consumer=f.read(12695)
    digest=hashlib.sha256(consumer).hexdigest()
    if (digest!='c880cd4a25d5134ca7cc9ba21bb19cb3f579c042c91070ef76e0e85c5d20a711'
            or platform.Elf(consumer).at(0xf80,0x28).hex()!=
            '82e647f9430040f9a31700f9030080d2e8feff97e00100b4130040b9010480d2731244d303ffff97'
            or not all(n in consumer for n in (b'/dev/hd0.ms.system_qnx_rbhv-qia',
                                                b'/dev/hd0.ms.system_qnx_bak_rbhv-qia'))):
        raise ValueError('Original QNX root mount strap consumer changed')
    return dict(profile='virtual-qnx-pmu-om-input-v1',vmid=2,physical='0x10460000',
                width=4,read_only=True,value=value,physical_value_verified=False,
                original_consumer='/sbin/mount_rootfs_qtd',consumer_sha256=digest,
                read_pc='0xf98',slot_select_bit=4,selected_slot='b' if value&16 else 'a',
                hcb_sha256=hashlib.sha256(raw).hexdigest())


def scan_elf(raw,name):

    header=struct.unpack_from('<HHIQQQIHHHHHH',raw,16)
    if not header[11] and not header[10]:
                                                                          
                                                                             
        segments=[];symbols=[]
        if header[9] and header[8]<56:raise ValueError('Invalid stripped program stride')
        for index in range(header[9]):
            row=struct.unpack_from('<IIQQQQQQ',raw,header[4]+index*header[8])
            if row[2]+row[5]>len(raw):raise ValueError('Stripped segment outside file')
            segments.append(dict(zip(('type','flags','offset','virtual','physical','file_size','memory_size','alignment'),row)))
    else:
        elf=platform.Elf(raw);segments=elf.segments;symbols=elf.symbols
    decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM);decoder.detail=True;decoder.skipdata=True
    rows=[]
    for seg in segments:
        if seg['type']!=1 or not seg['flags']&1:continue
        for ins in decoder.disasm(raw[seg['offset']:seg['offset']+seg['file_size']],seg['virtual']):
            if not ins.id:continue
            if not any((op.type==2 and (op.imm in (0x73c,0x840,0x1046073c,0x10460840,0x10460000,0x1046,0xa5000000))) or
                       (op.type==3 and op.mem.disp in (0x73c,0x840)) for op in ins.operands):continue
            funcs=[s['name'] for s in symbols if s['type']==2 and s['address']<=ins.address<s['address']+s['size']]
            rows.append({'address':hex(ins.address),'bytes':ins.bytes.hex(),'mnemonic':ins.mnemonic,'operands':ins.op_str,'symbols':funcs})
                                                                             
    literals=[]
    for value in (0x1046073c,0x10460840,0x10460000):
        needle=struct.pack('<I',value);start=0
        while (start:=raw.find(needle,start))>=0:
            literals.append({'file_offset':hex(start),'u32':hex(value)});start+=4
    related=[s for s in symbols if s['name'].startswith(('pmucal_pd','pmucal_p2v','pmucal_rae'))]
    return {'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'instructions':rows,'literals':literals,'related_symbols':related,
            'scope':'Exact decoded constants/offsets; not proven accesses to this physical base until code/data binding is checked'}


def inventory(inputs,scan_ifs):
    result={'dt_matches':[],'elf_candidates':[]}
    for guest,relative in (('linux','ivi/icas3cn-gp_b0_zero.dtb'),('qnx','qnx/qtd/icas3gp-qla-vm.dtb')):
        path=platform.factory_file(relative);tree=platform.parse_dtb(path.read_bytes())
        for name,props in tree.items():
            if any(any(struct.pack('>I',n) in value or f'{n:x}'.encode() in value.lower() for n in (0x73c,0x840,0x1046073c,0x10460840)) for value in props.values()):
                result['dt_matches'].append({'guest':guest,'path':name,'source':str(path),'sha256':vm.sha(path),
                    'properties':{k:platform.render(v) for k,v in props.items()}})
    for path in inputs:result['elf_candidates'].append(scan_elf(path.read_bytes(),str(path.resolve())))
    if scan_ifs:
        manifest=json.loads((vm.ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_text(encoding='utf-8'))
        source=platform.factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
        if vm.sha(source)!=manifest['sha256']:raise ValueError('IFS inventory mismatch')
        with source.open('rb') as stream:
            for row in manifest['entries']:
                if row['kind']!='reg' or row['file_size']<64:continue
                stream.seek(row['absolute_offset']);head=stream.read(20)
                if head[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',head,18)[0]!=183:continue
                stream.seek(row['absolute_offset']);raw=stream.read(row['file_size'])
                if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('IFS member mismatch '+row['name'])
                record=scan_elf(raw,row['name'])
                if record['instructions'] or record['literals']:result['elf_candidates'].append(record)
    result['scope']='Static candidates across verified source bytes; no native register or CPU state writes'
    return result


def check_model(output,qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qp,tp=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128M','-display','none',
             '-serial','null','-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),
        'scope':'Real PMU configuration/handoff word tests, not PHY training, LK storage completion or original OS boot'})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];transcript=[];qmp=connection=None
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('QNX PMU QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('QNX PMU QMP timeout')
            connection=socket.create_connection(('127.0.0.1',tp),timeout=5);stream=connection.makefile('rwb',buffering=0)
            def test(command):
                stream.write((command+'\n').encode());reply=stream.readline().decode().strip()
                transcript.append({'command':command,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                return reply
            def read(off):return int(test(f'readl {0x10460000+off:#x}').split()[1],16)
            def write(off,value):test(f'writel {0x10460000+off:#x} {value:#x}')
            def get(name):return qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':name})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('QNX PMU check failed: '+name)
            qmp.execute('cont')
            for off in (0x730,0x738,0x73c,0x740):
                check('Explicit virtual cold isolation '+hex(off),read(off),0)
                                                                            
                                                                             
                write(off,read(off)|1);check('Original bit0 enable '+hex(off),read(off),1)
                check('Actual QOM value matches bus '+hex(off),get('display-phy-'+f'{off:x}'),1)
                rejected=get('rejected-accesses');write(off,2)
                check('Unknown isolation bits reject '+hex(off),get('rejected-accesses'),rejected+1)
                check('Rejected write preserves state '+hex(off),read(off),1)
                write(off,read(off)&~1);check('Original bit0 disable '+hex(off),read(off),0)
            check('No software LK completion initially',read(0x840),0)
            for _ in range(20):read(0x840)
            test('clock_step 1000000000')
            check('Polling and elapsed time never forge A5',read(0x840),0)
            check('No hidden bootflag producer writes',get('ufs-bootflag-writes'),0)
            write(0x840,0x12345678);check('Actual explicit software scratch write is retained',read(0x840),0x12345678)
            check('Nonmagic word does not satisfy original UFS mask',read(0x840)&0xff000000,0x12000000)
                                                                            
            write(0x840,0xa5123456);check('Original UFS predicate after explicit fixture producer',read(0x840)&0xff000000,0xa5000000)
            write(0x840,0);check('Original LK clear withdraws handoff',read(0x840),0)
            check('Every handoff change counts actual bus writes',get('ufs-bootflag-writes'),3)
            rejected=get('rejected-accesses')
            for off in (0x734,0x83c,0x844,0xc73c,0x8840):
                write(off,1);read(off)
            check('Neighbor holes and unproven atomic aliases reject',get('rejected-accesses'),rejected+10)
            write(0x73c,1);write(0x840,0x1234);qmp.execute('system_reset')
            check('System reset clears retained isolation',read(0x73c),0)
            check('Explicit cold reset clears handoff without marking done',read(0x840),0)
            check('Reset counters have no invented writer',get('ufs-bootflag-writes'),0)
            return {'passed':True,'checks':checks,'factory_boot_verified':False,'storage_initialized':False,
                    'scope':'Finite isolation config and genuine software handoff register only; no native A5 injection, physical link or storage completion claim'}
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
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--binary',type=Path,action='append',default=[])
    parser.add_argument('--scan-ifs',action='store_true')
    parser.add_argument('--check',action='store_true');parser.add_argument('--qemu',type=Path)
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new scratch output')
    out.mkdir(parents=True);vm.save(out/'inventory.json' if args.check else out/'result.json',inventory(args.binary,args.scan_ifs))
    if args.check:vm.save(out/'result.json',check_model(out,args.qemu))
    print(str(out/'result.json'))


if __name__=='__main__':main()
