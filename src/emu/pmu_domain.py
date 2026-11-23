
import argparse
import hashlib
from pathlib import Path
import struct
import os
import re
import socket
import subprocess
import time
import cmu_sequence
import original_8704_vm as vm


CAL_SHA256='1936013a8c897dcae7b11503634c445b68d9c63dbbaf9aa1b4d5b404bef69ceb'
QOM_PATH='/machine/exynos-pmu/domains'
CONFIGS=(0x1980,0x1d00,0x1d80,0x1e00,0x1e80,0x1f00,0x1f80,0x2000,0x2080,0x2100,
         0x2880,0x2780,0x2800,0x2680,0x2700,0x2480,0x2900,0x2500,0x2580,0x2280)
STATE_KEYS=('profile','transition-ns','supply-good-mask','request-mask','active-mask','pending-mask',
            'waiting-supply-mask','register-reads','register-writes','rejected-accesses',
            'completions','cancellations','supply-losses')


def policy(profile,pmu_profile,clock_hz,latency_ns=1000,supply_mask=0):
    if profile is None and latency_ns==1000 and supply_mask==0:return None
    if profile!='virtual-sequencer-v1' or pmu_profile!='virtual-cold-v1' or not 1000000<=clock_hz<=1000000000:
        raise ValueError('Domain model requires explicit sequencer profile, cold PMU and native ACPM clock')
    if not 1<=latency_ns<=1000000000 or not 0<=supply_mask<=0xfffff:raise ValueError('Domain environment outside finite bounds')
    return {'profile':profile,'source_cal_sha256':CAL_SHA256,'domain_config_offsets':[hex(x) for x in CONFIGS],
            'transition_ns':latency_ns,'declared_virtual_supply_good_mask':hex(supply_mask),
            'factory_images_modified':False,'physical_timing_or_power_spec_verified':False,
            'physical_clock_gpu_dp_or_reset_effects_implemented':False,
            'assumptions':['All domains start logically off, independently of the real-car boot state',
                           'Configuration requests change logical state only after a nonzero virtual deadline and supply-good',
                           'Named virtual supply input and logical active output are not analog rails or fabricated physical completion',
                           'PMU OUT bit2 retains original software requests only, with no invented GPU/reset effect',
                           'Original CAL save/restore clocks and full hardware power microsequences are not executed by this model']}


def runtime_state(qmp):
    return {key:qmp.execute('qom-get',{'path':QOM_PATH,'property':key}) for key in STATE_KEYS}


def original_display_dump_contract(source):
    raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!='54b40286c32a225bbbab83f218978847170257cc9645cfe509f3f268886821f4':
        raise ValueError('Original QNX display library identity mismatch')
    image=cmu_sequence.DynamicCalImage(raw);base=image.pointer(0x58f58)
    rows=[]
    for index in range(1,9):
        address=base+24*index
        rows.append({'index':index,'entry_address':hex(address),'name':image.string(image.pointer(address+8)),
                     'physical_base':hex(image.u32(address+16)),'bytes':image.u32(address+20)})
    return {'source':str(source.resolve()),'sha256':digest,'table_base':hex(base),'dump_regions':rows,
            'source_functions':{'dump_by_type':'0x27228','dptx_pmu_dump':'0x274c8'},
            'scope':'Static original debug-dump mapping only; does not establish hardware fields or actual runtime caller identity'}


def original_contract(source):
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=CAL_SHA256:raise ValueError('Original QNX CAL identity mismatch')
    image=cmu_sequence.DynamicCalImage(raw)
    count=image.u32(image.pointer(0x8eeb8));base=image.pointer(0x8eea8)
    if not 1<=count<=64:raise ValueError('Original domain count outside bound')
    image.elf.at(base,count*72);domains=[]
    for index in range(count):
        address=base+72*index
        row={'index':index,'address':hex(address),'identifier':image.u32(address),
             'name':image.string(image.pointer(address+8)),'sequences':{}}
        for name,po,co in (('on',16,48),('save',24,52),('off',32,56),('status',40,60)):
            pointer=image.pointer(address+po);length=image.u32(address+co)
            if length>8192 or bool(pointer)!=bool(length):raise ValueError('Unbounded/ambiguous original sequence')
            rows=[]
            if length:image.elf.at(pointer,length*80)
            for n in range(length):
                a=pointer+n*80
                rows.append({'index':n,'address':hex(a),'op':image.u32(a),
                    'name':image.string(image.pointer(a+8)),'base':hex(image.u32(a+16)),
                    'offset':hex(image.u32(a+32)),'mask':hex(image.u32(a+36)),'value':hex(image.u32(a+40)),
                    'condition_base':hex(image.u32(a+44)),'condition_offset':hex(image.u32(a+56)),
                    'condition_mask':hex(image.u32(a+60)),'condition_value':hex(image.u32(a+64)),
                    'trailing_bytes_hex':image.elf.at(a+68,12).hex()})
            row['sequences'][name]={'pointer':hex(pointer),'count':length,'rows':rows}
        domains.append(row)
    populated=[row for row in domains if row['name']]
    if len(populated)!=20:raise ValueError('Original populated domain count changed')
    for index,row in enumerate(populated):
        if row['index']!=index:raise ValueError('Domain index order changed')
        for seq,value in (('on',1),('off',0)):
            literal=[r for r in row['sequences'][seq]['rows'] if r['op']==1 and r['base']=='0x10460000' and r['offset']==hex(CONFIGS[index])]
            wait=[r for r in row['sequences'][seq]['rows'] if r['op']==6 and r['base']=='0x10460000' and r['offset']==hex(CONFIGS[index]+4)]
            if len(literal)!=1 or len(wait)!=1 or any(r['mask']!='0x1' or int(r['value'],16)!=value for r in literal+wait):
                raise ValueError('Original on/off masked-write and wait contract mismatch')
        status=row['sequences']['status']['rows']
        if len(status)!=1 or status[0]['op']!=0 or status[0]['offset']!=hex(CONFIGS[index]+4) or status[0]['mask']!='0x1':
            raise ValueError('Original status-read contract mismatch')
    c=Path(__file__).with_name('exynos-auto9-pmu-domains.inc.c').read_text(encoding='utf-8')
    declared=c.split('exynos_auto9_domain_configs[20] = {',1)[1].split('};',1)[0]
    if tuple(int(x,16) for x in re.findall(r'0x[0-9a-f]+',declared))!=CONFIGS:raise ValueError('C model table differs from original CAL')
    return {'source':str(source.resolve()),'sha256':CAL_SHA256,'list_pointer':hex(base),'count':count,'domains':domains,
            'original_on_off_status_and_c_allowlist_verified':True,
            'scope':'Original72-byte descriptors/80-byte software sequences bound to original CAL functions; no silicon timing or physical power claim'}


def check_model(output,qemu=None):
    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qmp_port,test_port=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none','-serial','null','-serial','null',
             '-monitor','none','-nic','none','-S','-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'original_cpu_executed':False})
    vm.save(output/'virtual-policy.json',policy('virtual-sequencer-v1','virtual-cold-v1',96000000,1000,0xfffff))
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    qmp=connection=None;checks=[];transcript=[];levels={}
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            end=time.monotonic()+10
            while time.monotonic()<end:
                if process.poll() is not None:raise RuntimeError('Domain QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('Domain QMP timeout')
            qmp.execute('cont');connection=socket.create_connection(('127.0.0.1',test_port),timeout=5)
            stream=connection.makefile('rwb',buffering=0)
            def test(cmd):
                stream.write((cmd+'\n').encode());reply=stream.readline().decode().strip()
                while reply.startswith('IRQ '):
                    p=reply.split();levels[int(p[2])]=int(p[1]=='raise')
                    transcript.append({'gpio':reply});reply=stream.readline().decode().strip()
                transcript.append({'command':cmd,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError('QTest: '+reply)
                return reply
            def read(off):return int(test(f'readl {0x10460000+off:#x}').split()[1],16)
            def write(off,value):test(f'writel {0x10460000+off:#x} {value:#x}')
            def step(ns):test(f'clock_step {ns}')
            def get(name):return qmp.execute('qom-get',{'path':QOM_PATH,'property':name})
            def set_input(name,value):qmp.execute('qom-set',{'path':QOM_PATH,'property':name,'value':value})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('Domain check failed: '+name)
            def denied(name,value):
                failed=False
                try:set_input(name,value)
                except RuntimeError:failed=True
                check('QOM rejects '+name+'='+str(value),failed,True)
            check('Disabled default',get('profile'),0);read(0x2100)
            check('Disabled original DPTX access faults',get('rejected-accesses'),1)
            denied('profile',2);denied('transition-ns',0);denied('transition-ns',1000000001);denied('supply-good-mask',0x100000)
            denied('active-mask',1);set_input('profile',1)
            test('irq_intercept_out '+QOM_PATH+' domain-active')
            write(0x2100,1);step(100000)
            check('No supply cannot turn on',read(0x2104),0)
            check('Missing supply wait tracked',get('waiting-supply-mask'),1<<9)
            denied('profile',0);denied('transition-ns',2)
            test(f'set_irq_in {QOM_PATH} domain-supply-good 9 1')
            step(999);check('Actual GPIO supply does not complete early',read(0x2104),0)
            write(0x2100,1);step(1)
            check('Idempotent write does not postpone deadline',read(0x2104),1)
            check('Actual logical domain output rises',levels.get(9,0),1)
            test(f'set_irq_in {QOM_PATH} domain-supply-good 9 0');step(1)
            check('Supply loss lowers status',read(0x2104),0)
            check('Supply loss lowers output',levels.get(9,0),0)
            check('Loss independently counted',get('supply-losses'),1)
            test(f'set_irq_in {QOM_PATH} domain-supply-good 9 1');step(999)
            check('Supply restoration has a fresh deadline',read(0x2104),0);step(1)
            check('Restored supply completes',read(0x2104),1)
            write(0x2100,0);step(999);check('Off is not an immediate reflection',read(0x2104),1)
            step(1);check('Off deadline completes',read(0x2104),0)
            write(0x2100,1);step(500);write(0x2100,0);step(500)
            check('Reversed request cancels pending activation',read(0x2104),0)
            check('Cancellation actually tracked',get('cancellations'),1)
            set_input('supply-good-mask',0xfffff)
            for i,off in enumerate(CONFIGS):write(off,1)
            check('All20 requests are independent',get('request-mask'),0xfffff)
            for off in CONFIGS:check('Read does not synthesize completion '+hex(off),read(off+4),0)
            step(999);check('All deadlines still pending',get('pending-mask'),0xfffff)
            step(1)
            for i,off in enumerate(CONFIGS):
                check('Original CAL status after actual deadline '+hex(off),read(off+4),1)
                check('Independent GPIO output '+str(i),levels.get(i,0),1)
            before=get('rejected-accesses')
            for off,value in ((0x2104,1),(0x2100,2),(0xe100,0),(0x6100,1),(0xa100,0),(0xe104,1),
                              (0xe520,3),(0x2520,8),(0xa520,32)):
                write(off,value);before+=1;check('Unsupported domain write faults '+hex(off),get('rejected-accesses'),before)
            check('Failed writes cannot alter20 statuses',get('active-mask'),0xfffff)
            for off in (0x2520,0x25a0,0x22a0):
                write(off|0xc000,2);check('OUT original op14 bit index '+hex(off),read(off),4)
                write(off|0x8000,2);check('OUT original op15 clears only declared bit '+hex(off),read(off),0)
            before=get('rejected-accesses');read(0xc000|0x2520)
            check('Atomic OUT alias is write-only',get('rejected-accesses'),before+1)
            write(0x2100,0);step(500);write(0x2100,1);step(500)
            check('Reversed off request preserves active domain',read(0x2104),1)
            qmp.execute('system_reset');step(1)
            for name in ('request-mask','active-mask','pending-mask','completions','cancellations','supply-losses'):
                check('Reset clears '+name,get(name),0)
            for i in range(20):check('Reset clears real output '+str(i),levels.get(i,0),0)
            check('Reset preserves explicit supply environment',get('supply-good-mask'),0xfffff)
            write(0x2100,1);step(500);qmp.execute('system_reset');step(1000)
            check('Reset cancels outstanding virtual timer',get('active-mask'),0)
                                                                                   
            step(1000000000);check('No spontaneous domain completion',get('completions'),0)
            vm.save(output/'runtime-state.json',runtime_state(qmp))
            return {'checks':checks,'passed':True,'original_cpu_executed':False,'guest_boot_verified':False,
                    'scope':'Actual finite PMU MMIO, QEMU virtual timers and GPIO; no claim of physical rails or GPU/DP functionality'}
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
    parser.add_argument('--cal-library',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qemu',type=Path);parser.add_argument('--check-model',action='store_true')
    parser.add_argument('--display-library',type=Path)
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new scratch output')
    if not args.check_model and not args.cal_library:raise ValueError('Require --cal-library or --check-model')
    out.mkdir(parents=True)
    if args.cal_library:vm.save(out/'original-contract.json',original_contract(args.cal_library))
    if args.display_library:vm.save(out/'display-dump-contract.json',original_display_dump_contract(args.display_library))
    vm.save(out/'result.json',check_model(out,args.qemu) if args.check_model else original_contract(args.cal_library));print(str(out/'result.json'))


if __name__=='__main__':main()
