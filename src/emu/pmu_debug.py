
import argparse
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import cmu_sequence
import original_8704_vm as vm

DISPLAY_SHA='54b40286c32a225bbbab83f218978847170257cc9645cfe509f3f268886821f4'
QOM='/machine/exynos-pmu'
ADDRESSES=(0x1046210c,0x18a00604,0x18a00614)
KEYS=('dptx-debug-profile','dptx-debug-word0','dptx-debug-word1','dptx-debug-word2',
      'dptx-debug-reads0','dptx-debug-reads1','dptx-debug-reads2')


def policy(profile,pmu_profile,clock_hz,word0=None,word1=None,word2=None):
    if profile is None and word0 is None and word1 is None and word2 is None:return None
    if profile!='virtual-input-bank-v1' or pmu_profile!='virtual-cold-v1' or not 1000000<=clock_hz<=1000000000:
        raise ValueError('Debug inputs require explicit profile, cold PMU and native management CPU')
    if any(not isinstance(x,int) or not 0<=x<=0xffffffff for x in (word0,word1,word2)):
        raise ValueError('All three opaque debug words must be explicit32-bit unsigned inputs')
    return {'profile':profile,'original_display_sha256':DISPLAY_SHA,'input_words':[
        {'address':hex(address),'value':hex(word)} for address,word in zip(ADDRESSES,(word0,word1,word2))],
        'hardware_field_meanings_verified':False,'physical_reset_snapshot_verified':False,
        'factory_images_modified':False,'power_clock_ready_or_gpu_effects':False,
        'assumptions':['Exactly three opaque external input words used by the original raw register dump',
                       'Input values are declared virtual environment, not observed physical reset values',
                       '96 actual named virtual GPIO inputs can change these words; software writes and aliases reject',
                       'Reads only expose input bits; never complete an operation or change any domain state']}


def runtime_state(qmp):return {key:qmp.execute('qom-get',{'path':QOM,'property':key}) for key in KEYS}


def original_consumer(source,session):
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=DISPLAY_SHA:raise ValueError('Original display identity mismatch')
    image=cmu_sequence.DynamicCalImage(raw)
    if image.elf.at(0x25b44,4).hex()!='440140b9':raise ValueError('Original volatile word-load instruction mismatch')
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    instructions=list(Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(image.elf.at(0x2739c,4),0x2739c))
    if len(instructions)!=1 or instructions[0].mnemonic!='sub' or instructions[0].op_str!='w5, w5, #4':
        raise ValueError('Original debug dump length-minus4 instruction mismatch')
    prefix=image.string(0x3b5a8)
    if prefix!=' ':raise ValueError('Original dump item-prefix mismatch: '+repr(prefix))
    text=(session/'tcg.log').read_text(encoding='utf-8')
    pattern=r'Auto9 PMU unsupported read offset=0x210c\nAuto9 PMU caller cpu=(\d+) el=(\d+) pc=(0x[0-9a-f]+) lr=(0x[0-9a-f]+) fp=(0x[0-9a-f]+) sp=(0x[0-9a-f]+) x0=(0x[0-9a-f]+) x1=(0x[0-9a-f]+) x2=(0x[0-9a-f]+) x3=(0x[0-9a-f]+)'
    matches=list(re.finditer(pattern,text));rows=[]
    for m in matches:
        cpu,el,pc,lr,fp,sp,x0,x1,x2,x3=(int(s,0) for s in m.groups())
        base=pc-0x25b44
        if base<0 or base%4096 or lr!=base+0x25b5c or x1!=base+0x3b5a8 or x3!=x1 or el!=0:
            raise ValueError('Runtime PC/LR/item-prefix pointers do not bind to original dump')
        rows.append({'cpu':cpu,'el':el,'pc':hex(pc),'lr':hex(lr),'fp':hex(fp),'sp':hex(sp),
                     'derived_library_base':hex(base),'original_load_offset':'0x25b44',
                     'original_load':'ldr w4, [x10]','item_prefix_offset':'0x3b5a8','item_prefix':prefix})
    if not rows:raise ValueError('No actual PMU210C caller observation')
    return {'source':str(source.resolve()),'sha256':DISPLAY_SHA,'session':str(session.resolve()),
            'trace_sha256':vm.sha(session/'tcg.log'),'observations':rows,
            'binding':'Derived from aligned relocation plus three matching original pointers/instruction, not independently captured loader metadata',
            'meaning':'Original raw hex-dump load, not a source-proven power-ready poll',
            'dump_length_rule':'Original2739C subtracts4 from mapped region size before dumping;2110/608/618/804 are NOT read by these calls',
            'opaque_input_addresses':[hex(a) for a in ADDRESSES],
            'physical_field_semantics_recovered':False}


def check_model(output,qemu=None):
    exe=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qp,tp=vm.free_port(),vm.free_port()
    command=[str(exe),'-machine','virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1',
             '-cpu','cortex-a57','-accel','qtest','-smp','8','-m','128','-display','none','-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(exe),'original_cpu_executed':False})
    vm.save(output/'virtual-policy.json',policy('virtual-input-bank-v1','virtual-cold-v1',96000000,0,0,0))
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    qmp=connection=None;checks=[];transcript=[]
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            end=time.monotonic()+10
            while time.monotonic()<end:
                if process.poll() is not None:raise RuntimeError('Debug bank QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('Debug bank QMP timeout')
            qmp.execute('cont');connection=socket.create_connection(('127.0.0.1',tp),timeout=5);stream=connection.makefile('rwb',buffering=0)
            def test(cmd):
                stream.write((cmd+'\n').encode());reply=stream.readline().decode().strip()
                transcript.append({'command':cmd,'reply':reply})
                if not reply.startswith('OK'):raise RuntimeError(reply)
                return reply
            def read(off):return int(test(f'readl {0x10460000+off:#x}').split()[1],16)
            def write(off,val):test(f'writel {0x10460000+off:#x} {val:#x}')
            def read_word(i):return int(test(f'readl {ADDRESSES[i]:#x}').split()[1],16)
            def get(key):return qmp.execute('qom-get',{'path':QOM,'property':key})
            def set_input(key,value):return qmp.execute('qom-set',{'path':QOM,'property':key,'value':value})
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('Debug bank: '+name)
            check('Default disabled',get('dptx-debug-profile'),0)
            option=0x18a00800
            def read_option():return int(test(f'readl {option:#x}').split()[1],16)
            check('Original DPTX controller option explicit cold config',read_option(),0)
            test(f'writel {option:#x} 0x30000000');check('Only original save mask retained',read_option(),0x30000000)
            test(f'writel {option:#x} 0x30000001');check('Unmodelled option field rejects without mutation',read_option(),0x30000000)
            test(f'writel {option:#x} 0')
            read(0x210c);read_word(1);read_word(2);check('Default PMU read faults',get('rejected-accesses'),1)
            for key,value in (('dptx-debug-profile',2),('dptx-debug-word0',0x100000000),('dptx-debug-word1',-1),('dptx-debug-word2',0x100000000)):
                failed=False
                try:set_input(key,value)
                except RuntimeError:failed=True
                check('Invalid QOM rejects '+key,failed,True)
            set_input('dptx-debug-profile',1)
            for bit in range(96):
                test(f'set_irq_in {QOM} dptx-debug-input {bit} 1')
                word=bit//32;expected=(1<<((bit%32)+1))-1
                check('Actual GPIO bit '+str(bit),read_word(word),expected)
            before=get('rejected-accesses')
            for alias in (0,0x4000,0x8000,0xc000):write(0x210c|alias,1)
            check('All PMU software write forms reject',get('rejected-accesses'),before+4)
            for i,address in enumerate(ADDRESSES):
                test(f'writel {address:#x} 0');check('Software cannot mutate external word '+hex(address),read_word(i),0xffffffff)
            before=get('rejected-accesses');read(0x2110);read(0x2114);read(0x210c|0x8000)
            check('No excluded/neighboring/alias PMU read aperture',get('rejected-accesses'),before+3)
            set_input('dptx-debug-word0',0x12345678);set_input('dptx-debug-word1',0x9abcdef0)
            set_input('dptx-debug-word2',0x87654321)
            for i,value in enumerate((0x12345678,0x9abcdef0,0x87654321)):check('Explicit input word'+str(i),read_word(i),value)
            for i in range(10):
                for word in range(3):read_word(word)
            test('clock_step 1000000000')
            for key in ('active-mask','request-mask','pending-mask','completions'):
                value=qmp.execute('qom-get',{'path':QOM+'/domains','property':key})
                check('Reads/time cannot create domain '+key,value,0)
            check('Reads cannot inject UFS done',read(0x840),0)
            qmp.execute('system_reset');test('clock_step 1')
            for i,value in enumerate((0x12345678,0x9abcdef0,0x87654321)):check('External word survives reset'+str(i),read_word(i),value)
            check('Reset clears read counter0',get('dptx-debug-reads0'),1)
            set_input('dptx-debug-profile',0);before=get('rejected-accesses');read(0x210c)
            check('Disable restores strict fault',get('rejected-accesses'),before+1)
            vm.save(output/'runtime-state.json',runtime_state(qmp))
            return {'passed':True,'checks':checks,'guest_boot_verified':False,'physical_debug_fields_verified':False}
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
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--display-library',type=Path);p.add_argument('--session',type=Path)
    p.add_argument('--check-model',action='store_true');p.add_argument('--qemu',type=Path)
    args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new scratch output')
    if not args.check_model and not (args.display_library and args.session):raise ValueError('Require actual consumer inputs or --check-model')
    out.mkdir(parents=True)
    if args.display_library and args.session:vm.save(out/'original-consumer.json',original_consumer(args.display_library,args.session))
    result=check_model(out,args.qemu) if args.check_model else {'consumer_bound':True,'guest_boot_verified':False}
    vm.save(out/'result.json',result);print(str(out/'result.json'))


if __name__=='__main__':main()
