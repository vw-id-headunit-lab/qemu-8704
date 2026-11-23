
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import original_8704_vm as vm
import vlm_platform as platform

SOURCE_SHA='c652be6e5211f8c5ccfa12dc660a22289c0383f93bfd8313ec7fcb4e772f8567'
QOM='/machine/peripheral/ti941-iid'
STATE_KEYS=('pdb-level','powered','cable-present','register-reads','register-writes','nacks','rejected-accesses',
            'powerups','powerdowns','pdb-edges','starts','stops','probe-pending','probe-result','probe-completions',
            'untrained-probes','indirect-writes','indirect-reads','history-dropped','write-history-first256')


def policy(profile,board_adapter,powerup_ns=100000,probe_ns=100000):
    if profile is None and powerup_ns==probe_ns==100000:return None
    if profile!='virtual-ti941-iid-v1' or not board_adapter:raise ValueError('Serializer requires explicit profile and original board')
    if not all(isinstance(x,int) and 1<=x<=1000000000 for x in (powerup_ns,probe_ns)):
        raise ValueError('Virtual serializer deadlines require1..1e9ns')
    return {'profile':profile,'original_program_sha256':SOURCE_SHA,'address':'0x1a','bus':'i2c17',
            'pdb':{'gpio_path':'/machine/exynos-gpio4','output':34,'register':'0x10830084','bit':2},
            'powerup_ns':powerup_ns,'probe_ns':probe_ns,'virtual_cable_connected':False,
            'factory_images_modified':False,'exact_ti_suffix_verified':False,'physical_probe_semantics_verified':False,
            'dsi_fpd_training_hdcp_video_implemented':False,
            'assumptions':['Finite original TI941 control endpoint, not a complete DS90Ux941AS silicon implementation',
                           'PDB is driven by the actual original GPIO output; a real virtual timer gates I2C ACK',
                           'Original undocumented23/24 pair probes an explicitly disconnected virtual cable after a deadline',
                           'Connected input alone never returns1B training success; no video backend is attached',
                           'Original forced-clock/link request bits5C are retained but not physical readiness',
                           'Unspecified register/strap/analog defaults are declared virtual zeros; reserved accesses NACK']}


def device_option(profile,board_adapter,powerup_ns=100000,probe_ns=100000):
    cfg=policy(profile,board_adapter,powerup_ns,probe_ns)
    if cfg is None:return None
    return f'exynos-auto9.ti941-iid,id=ti941-iid,bus=i2c17,address=0x1a,virtual-profile=1,powerup-ns={powerup_ns},probe-ns={probe_ns}'


def runtime_state(qmp):return {key:qmp.execute('qom-get',{'path':QOM,'property':key}) for key in STATE_KEYS}


def original_contract(source):
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SOURCE_SHA:raise ValueError('Original TI941 program identity mismatch')
    elf=platform.Elf(raw)
    return {'source':str(source.resolve()),'sha256':SOURCE_SHA,
            'ranges':[{'start':hex(a),'bytes':n,'sha256':hashlib.sha256(elf.at(a,n)).hexdigest()}
                      for a,n in ((0xe30,0xc08),(0x1bf8,0x100),(0x1cf8,0x130),(0x1e98,0xb8),(0x22d0,0x2b0))],
            'software_interface':{'bus':'/dev/i2c17','address':26,'pointer_bytes':1,'read_delay_us':20000,
                'pdb_bank_base':'0x10830080','pdb_pin':2,'probe_no_cable_value':4,'probe_other_exit_mask':'0x1b'},
            'scope':'Original REA-bound software transaction contract, not recovered physical chip revision or undocumented register specification'}


def fetch_public(output):
    url='https://www.ti.com/lit/ds/symlink/ds90uh941as-q1.pdf'
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'MEB-local-source-verification'}),timeout=30) as response:raw=response.read(16*1024*1024+1)
    if len(raw)>16*1024*1024 or not raw.startswith(b'%PDF-'):raise ValueError('TI source is not a bounded PDF')
    target=output/'ds90uh941as-q1.pdf';target.write_bytes(raw)
    return {'url':url,'path':str(target),'bytes':len(raw),'sha256':vm.sha(target),
            'scope':'TI941AS family control interface and matching clock setup; exact OEM suffix and registers23/24 are not established'}


def initialization_writes():
                                                                           
    return [(1,8),(0x1e,1),(0x40,0x10),(0x41,0x86),(0x42,0xa),
            (0x1e,2),(0x40,0x10),(0x41,0x94),(0x42,0xa),
            (0x1e,1),(0x5b,5),(0x4f,0x8c),(0x40,4),(0x41,5),(0x42,2),
            (0x41,0x20),(0x42,0x6f),(0x41,0x30),(0x42,0),(0x41,0x31),(0x42,0x80),
            (0x41,0x32),(0x42,0),(0x41,0x33),(0x42,0xb),(0x5c,0x67),
            (0x1e,2),(0x4f,0x8c),(0x40,8),(0x41,5),(0x42,0x10),
            (0x41,0x20),(0x42,0x6f),(0x41,0x30),(0x42,0),(0x41,0x31),(0x42,4),
            (0x41,0x32),(0x42,0),(0x41,0x33),(0x42,2),(0x5c,0x67)]


def check_model(output,qemu=None):
    exe=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve();qp,tp=vm.free_port(),vm.free_port()
    command=[str(exe),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none','-serial','null','-serial','null',
             '-monitor','none','-nic','none','-S','-device',device_option('virtual-ti941-iid-v1',True),
             '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(exe),'original_cpu_executed':False})
    vm.save(output/'virtual-policy.json',policy('virtual-ti941-iid-v1',True))
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];transcript=[];levels={};qmp=conn=None
    with (output/'qemu.log').open('wb') as log:
        proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            end=time.monotonic()+10
            while time.monotonic()<end:
                if proc.poll() is not None:raise RuntimeError('Serializer QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('Serializer QMP timeout')
            conn=socket.create_connection(('127.0.0.1',tp),timeout=5);stream=conn.makefile('rwb',buffering=0)
            def test(cmd):
                stream.write((cmd+'\n').encode());reply=stream.readline().decode().strip()
                while reply.startswith('IRQ '):
                    parts=reply.split();levels[int(parts[2])]=int(parts[1]=='raise');transcript.append({'gpio':reply});reply=stream.readline().decode().strip()
                transcript.append({'command':cmd,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError(reply)
                return reply
            def rd(a):return int(test(f'readl {a:#x}').split()[1],16)
            def wr(a,v):test(f'writel {a:#x} {v:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(key):return qmp.execute('qom-get',{'path':QOM,'property':key})
            def check(n,a,e):
                checks.append({'name':n,'actual':a,'expected':e,'passed':a==e})
                if a!=e:raise ValueError('Serializer check: '+n)
            b=0x10950000
            def transfer(payload=None,length=1,address=0x1a):
                wr(b,0x80000000);wr(b,8|(0x40 if payload is None else 0x80));wr(b+4,0x80083)
                wr(b+8,0xffffff);wr(b+0x40,0);wr(b+0x48,0)
                wr(b+0x24,0xffffffff);wr(b+0x20,0)
                wr(b+0x70,address<<10)
                if payload is not None:
                    for value in payload:wr(b+0x34,value)
                    length=len(payload)
                wr(b+0x44,0x80000000|0x20000|length|(0x10000 if payload is None else 0))
                step((length+2)*22500)
                status=rd(b+0x24)
                values=[rd(b+0x38)&255 for i in range(length)] if payload is None and status==0x80 else []
                return status,values
            def write(reg,value):
                status,_=transfer([reg,value]);check('Accepted real bus write '+hex(reg),status,0x80)
            def read(reg):
                status,_=transfer([reg]);check('Accepted pointer SEND '+hex(reg),status,0x80)
                status,values=transfer(None,1);check('Accepted separate RECV '+hex(reg),status,0x80)
                return values[0]
            qmp.execute('cont');wr(0x10821014,4)
            test('irq_intercept_out '+QOM+' i2c-active')
            check('Cold PDB low',get('pdb-level'),0);check('Cold control interface off',get('powered'),0)
            transfer([0]);check('PDB-off bus really NACKs',get('nacks'),1)
            wr(0x10830080,0x100);wr(0x10830084,4)
            check('Original GPIO drives actual PDB input',get('pdb-level'),1)
            step(99999);check('Powerup cannot finish early',get('powered'),0)
            wr(0x10830084,4);step(1)
            check('Powerup actual deadline',get('powered'),1);check('Actual I2C-active output',levels.get(0,0),1)
            check('Address follows configured virtual strap',read(0),0x34)
            transfer([0],address=0x19);check('Wrong address cannot reach endpoint',get('register-reads'),1)
            for reg,value in initialization_writes():write(reg,value)
            check('Accepted original script count',get('register-writes'),len(initialization_writes()))
            check('Original GPIO revision input is not forged30',read(0xd),0)
            write(1,0);write(0x23,0x80);write(0x24,0x80)
            check('Probe pending requires virtual time',get('probe-pending'),1)
                                                                            
                                                                    
            step(77499);check('Cable probe not early',get('probe-completions'),0)
            step(1);check('Actual absent-cable probe completes',get('probe-completions'),1)
            check('Absent cable is not fake trained link',read(0x24),4)
            write(0x24,0);write(0x23,0)
                                                                              
                                                                       
            for ctl in (0x10,0x14):
                for reg,value in ((0x40,ctl),(0x41,0x4a),(0x42,0x3f),(0x41,0x4b),(0x42,0x8a),(0x41,0x49),(0x42,0x10),(0x42,0)):write(reg,value)
            for ctl,value in ((5,2),(9,0x10)):
                write(0x40,ctl);write(0x41,5);check('Independent digital bank '+hex(ctl),read(0x42),value)
            write(0x40,7);write(0x41,0x30);write(0x42,0xab);write(0x42,0xcd)
            write(0x41,0x30);check('Indirect read auto increment first',read(0x42),0xab)
            check('Indirect read auto increment second',read(0x42),0xcd)
            rejected=get('rejected-accesses');transfer([0xfe]);check('Reserved register pointer NACKs',get('rejected-accesses'),rejected+1)
            transfer([1,0xff]);check('Unknown reset effects NACK',read(1),0)
            test('set_irq_in '+QOM+' environment 1 1')
            write(0x23,0x80);write(0x24,0x80);step(100000)
            check('Connected cable without video backend never trains',read(0x24),0)
            check('Untrained probe tracked separately',get('untrained-probes'),1)
            write(0x24,0);write(0x23,0);write(0x23,0x80);write(0x24,0x80)
            wr(0x10830084,0);step(1000000)
            check('PDB loss cancels probe',get('probe-pending'),0);check('PDB loss turns interface off',get('powered'),0)
            check('Actual output drops',levels.get(0,0),0)
            wr(0x10830084,4);step(100000);check('Real GPIO power cycle clears config',read(0x5b),0)
            vm.save(output/'runtime-state.json',runtime_state(qmp))
            return {'passed':True,'checks':checks,'guest_boot_verified':False,'video_training_or_frames_verified':False}
        finally:
            vm.save(output/'checks.json',checks);vm.save(output/'qtest-transcript.json',transcript)
            if conn:conn.close()
            if qmp:
                try:qmp.execute('quit')
                except (OSError,RuntimeError,ValueError):pass
                qmp.close()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=5)


def original_consumers():
    inventory_path=vm.ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json'
    inventory=json.loads(inventory_path.read_text(encoding='utf-8'))
    source=platform.factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
    if vm.sha(source)!=inventory['sha256']:raise ValueError('Factory IFS identity mismatch')
    needles=(b'ser_i2c_read',b'Ser-E',b'ser_i2c_write')
    matches=[];skipped=[];examined=0
    with source.open('rb') as stream:
        for row in inventory['entries']:
            if row['kind']!='reg' or not row.get('file_size'):continue
            if row['file_size']>64*1024*1024:
                skipped.append({'name':row['name'],'bytes':row['file_size'],'reason':'Member exceeds64MiB scanner bound'});continue
            stream.seek(row['absolute_offset']);raw=stream.read(row['file_size']);examined+=1
            if len(raw)!=row['file_size']:raise ValueError('Short factory member read')
            found=[]
            for needle in needles:
                start=0
                while True:
                    offset=raw.find(needle,start)
                    if offset<0:break
                    found.append({'needle':needle.decode('ascii'),'file_offset':hex(offset),
                                  'context_hex':raw[max(0,offset-16):offset+len(needle)+48].hex()})
                    if len(found)>1024:raise ValueError('Excessive ambiguous consumer strings')
                    start=offset+len(needle)
            if found:
                digest=hashlib.sha256(raw).hexdigest()
                if digest!=row['sha256']:raise ValueError('Original serializer member identity mismatch')
                matches.append({'name':row['name'],'bytes':len(raw),'sha256':digest,'matches':found})
    return {'source':str(source.resolve()),'sha256':inventory['sha256'],'examined_members':examined,
            'skipped':skipped,'consumers':matches,'scope':'Exact byte-string matches in pinned original regular IFS members; not proof of runtime call or chip identity'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--program',type=Path);parser.add_argument('--fetch-public',action='store_true')
    parser.add_argument('--check-model',action='store_true');parser.add_argument('--qemu',type=Path)
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new scratch output')
    out.mkdir(parents=True)
    if args.program:vm.save(out/'original-contract.json',original_contract(args.program))
    if args.fetch_public:vm.save(out/'public-source.json',fetch_public(out))
    vm.save(out/'result.json',check_model(out,args.qemu) if args.check_model else original_consumers());print(str(out/'result.json'))


if __name__=='__main__':main()
