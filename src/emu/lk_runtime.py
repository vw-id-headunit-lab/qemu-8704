




from collections import deque
import hashlib
import struct
import time
from pathlib import Path
import lk_handoff
import vlm_platform as platform

LINK=0xe5e00000
UTRL,CMD=0x40100000,0x40101000
BIO,CONTEXT,THREAD=0x40004000,0x40008000,0x4000c000
SENTINEL=0x400ff000
INITIALIZER=0x400fe000


def initializer_code():

    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    code=struct.pack('<3I',0xd518d080,0xd51a0002,0xd61f0020)
    decoded=[(i.mnemonic,i.op_str) for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(code,INITIALIZER)]
    if decoded!=[('msr','tpidr_el1, x0'),('msr','csselr_el1, x2'),('br','x1')]:
        raise ValueError('Native initializer instruction contract mismatch')
    return code


def fixture_writes():

    rows=[]
    def u32(a,v):rows.append((a,struct.pack('<I',v)))
    def u64(a,v):rows.append((a,struct.pack('<Q',v)))
    head=LINK+0x79bb0
    for a,v in ((head,BIO),(head+8,BIO),(BIO,head),(BIO+8,head),(BIO+24,LINK+0x70d80),
                (BIO+64,BIO),(BIO+288,LINK+0x52554),(BIO+296,LINK+0x50a6c),(LINK+0x27d520,CONTEXT)):
        u64(a,v)
    u32(BIO+16,1);u32(BIO+200,80);u32(LINK+0x27d530,0)
    for off,v in ((24,0x17e00000),(32,0x17e01100),(152,CMD),(160,UTRL),(168,0x40104000)):
        u64(CONTEXT+off,v)
    u32(CONTEXT+212,100000)
    descriptor=bytearray(32);struct.pack_into('<Q',descriptor,16,CMD)
    descriptor[24:28]=bytes.fromhex('00040004');rows.append((UTRL,bytes(descriptor)))
    return rows


class RspMemory:

    def __init__(self,rsp):self.rsp=rsp;self.events=[]
    def get(self,address,size):
        result=bytearray()
        for off in range(0,size,1024):
            n=min(1024,size-off);reply=self.rsp.command(f'm{address+off:x},{n:x}')
            if reply.startswith(b'E'):raise ValueError('Boot memory read failed '+hex(address+off))
            data=bytes.fromhex(reply.decode())
            if len(data)!=n:raise ValueError('Short boot memory read')
            result+=data
        return bytes(result)
    def put(self,address,data):
        for off in range(0,len(data),1024):
            part=data[off:off+1024]
            if self.rsp.command(f'M{address+off:x},{len(part):x}:'+part.hex())!=b'OK':
                raise ValueError('Boot memory write failed '+hex(address+off))
    def read(self,a):return struct.unpack('<I',self.get(a,4))[0]
    def write(self,a,v):self.events.append({'address':hex(a),'value':v});self.put(a,struct.pack('<I',v))


def device_snapshot(qmp):

    groups={'ufs':('/machine/exynos-ufs0',('dma-reads','dma-writes','dma-rejected','vendor-rejected',
        'software-resets','device-reset-assertions','device-reset-releases')),
        'pmu':('/machine/exynos-pmu',('ufs-bootflag-reads','ufs-bootflag-writes','ufs-lk-bootflag',
            'ufs-cpu-reads','ufs-cpu-writes','ufs-access-dropped','ufs-access-first64'))}
    return {group:{name:qmp.execute('qom-get',{'path':path,'property':name}) for name in names}
            for group,(path,names) in groups.items()}


def counter_delta(before,after):

    keys=('dma-reads','dma-writes','dma-rejected','vendor-rejected','software-resets',
          'device-reset-assertions','device-reset-releases')
    result={k:after['ufs'][k]-before['ufs'][k] for k in keys}
    if any(v<0 for v in result.values()):raise ValueError('UFS counter reset across LK stage')
    return result


def native_stage(rsp,qmp,thread,output,wall_budget=5,pwm_delay=False,dsp_load=None,dsp_peer=False):






    import original_8704_vm as vm
    import ufs_platform as ufs
    output=Path(output);output.mkdir()
    contract=lk_handoff.original_contract();raw=Path(contract['source']).read_bytes()
    if rsp.command('Hg'+thread)!=b'OK':raise ValueError('Select native LK boot CPU')
    regs,_=rsp.xml_registers();initial=rsp.command('g');core=rsp.registers();io=RspMemory(rsp)
    def readreg(name):
        r=regs[name];data=bytes.fromhex(rsp.command('p'+format(r['number'],'x')).decode())
        return int.from_bytes(data,'little')
    def writereg(name,value):
        r=regs[name];data=value.to_bytes(r['bits']//8,'little')
        if rsp.command(f'P{r["number"]:x}='+data.hex())!=b'OK':raise ValueError('Native register write '+name)
        if readreg(name)!=value:raise ValueError('Native register readback '+name)
    if readreg('SCTLR_EL2')&1:raise ValueError('Native LK stage requires paused MMU-off EL2')
    saved={n:readreg(n) for n in ('TPIDR_EL1','CSSELR')}
    if io.read(0x10460840):raise ValueError('Native LK stage requires a genuine cold zero handoff')
    result={'profile':'virtual-original-lk-ssu-v1','source':contract,'fixture_objects':True,
            'full_lk_boot_verified':False,'oem_environment_saved':False,'original_calls_replaced':False,
            'native_cpu':thread,'initial_core':core,'initial_system':saved,'stops':[],'loader_requests':[],'passed':False,
            'generated_initializer':{'address':hex(INITIALIZER),'bytes':initializer_code().hex(),
                'scope':'Separate loader MSR/BR trampoline because QEMU GDB system-register writes are read-only; original firmware untouched'}}
    breakpoints=[LINK+0x3b5a8,LINK+0x3b5ac,LINK+0x3b5c0,SENTINEL,LINK+0x5db00,LINK+0x5f788]
    installed=[];oldtimeout=rsp.socket.gettimeout();systems_modified=False
    result['devices_before']=device_snapshot(qmp)
    result['phase']='initializing-native-runtime';vm.save(output/'result.json',result)
    try:
                                                                             
                                                                             
        io.put(LINK,raw)
        if hashlib.sha256(io.get(LINK,len(raw))).hexdigest()!=contract['sha256']:
            raise ValueError('Original LK load readback mismatch')
        io.put(LINK+0x270000,bytes(0x10000));io.put(0x40004000,bytes(0x10000));io.put(CMD,bytes(8192))
        for a,data in fixture_writes():io.put(a,data)
        route=ufs.route_contract();vgen=route['vgen']['base'];mpu=route['s2mpu']['base']
        for i in range(8):io.write(vgen+4*i,2)
        io.put(ufs.TABLE,b'\xff'*128);io.write(mpu,3);io.write(mpu+0x20,4)
        io.write(mpu+0x4408,ufs.TABLE>>4);io.write(mpu+0x440c,1);io.write(mpu+0x1000,2<<16)
        io.write(ufs.HCI+0x34,1)
        for off in (0x94,0x98,0x9c):io.write(ufs.HCI+off,0)
        io.write(ufs.HCI+0x90,0x16)
        if io.read(ufs.HCI+0x30)&11!=11:raise ValueError('Real UFS HCI link/list not ready')
        io.write(ufs.HCI+0x20,0xffffffff);io.write(ufs.HCI+0x24,0xffffffff)
        io.write(ufs.HCI+0x50,UTRL);io.write(ufs.HCI+0x54,0);io.write(ufs.HCI+0x60,1)
        def submit(request):
            io.put(CMD,bytes(8192));io.put(CMD,request)
            io.put(UTRL,ufs.utrd(byte_granularity=True));io.write(ufs.HCI+0x58,1)
            deadline=time.monotonic()+wall_budget
            while not io.read(ufs.HCI+0x20)&1 and time.monotonic()<deadline:time.sleep(.001)
            ocs=io.read(UTRL+8)&15;response=io.get(CMD+1024,48)
            row={'ocs':ocs,'doorbell':io.read(ufs.HCI+0x58),'request_hex':request[:32].hex(),'response_hex':response.hex()}
            result['loader_requests'].append(row)
            if ocs or row['doorbell']:raise ValueError('Real bootloader UFS protocol request failed')
            io.write(ufs.HCI+0x20,io.read(ufs.HCI+0x20));return response
        if submit(bytes(288))[0]!=0x20:raise ValueError('Real bootloader NOP response invalid')
        request=bytearray(288);request[0],request[5],request[12],request[13]=0x16,0x81,6,1
        if submit(bytes(request))[6]:raise ValueError('Real bootloader device-init rejected')
        if dsp_load:
            from dsp_bootstrap import execute
            result['dsp_preboot']=execute(io,qmp,output/'dsp-ufs-load',dsp_load,mpu,UTRL,CMD,wall_budget)
                                                                              
        io.put(CMD,bytes(8192))
        for a,data in fixture_writes():io.put(a,data)
        io.put(INITIALIZER,initializer_code())
        writereg('X0',THREAD);writereg('X1',LINK+0x3b59c);writereg('X2',saved['CSSELR'])
        writereg('SP',0x400eeff0);writereg('X30',SENTINEL);writereg('PC',INITIALIZER)
        for a in breakpoints:
            if rsp.command(f'Z1,{a:x},4')!=b'OK':raise ValueError('Native LK breakpoint install')
            installed.append(a)
        rsp.socket.settimeout(wall_budget)
        def resume(expected):
            result['phase']='continuing-to-'+hex(expected);vm.save(output/'result.json',result)
            started=time.monotonic()
            stop=rsp.command('vCont;c:'+thread).decode();state=rsp.registers()
            result['stops'].append({'reply':stop,'core':state,'wall_seconds':time.monotonic()-started})
            vm.save(output/'result.json',result)
            if int(state['pc'],16)!=expected:raise ValueError('Original LK unexpected stop '+state['pc'])
            if rsp.command(f'z1,{expected:x},4')!=b'OK':raise ValueError('Native LK breakpoint removal')
            installed.remove(expected);return state
        systems_modified=True
        env=resume(LINK+0x3b5a8);result['environment_return']=env['x0']
        if readreg('TPIDR_EL1')!=THREAD:raise ValueError('Native initializer did not set actual thread register')
        if pwm_delay:
                                                                                 
                                                                              
                                                                               
            caller=rsp.command('g')
            writereg('X0',0);writereg('X1',15);writereg('X30',SENTINEL);writereg('PC',LINK+0x3bca4)
            delay=resume(SENTINEL)
            if not 15<int(delay['x0'],16)<1000:raise ValueError('Original native PWM delay did not count elapsed ticks')
            result['original_pwm_delay']={'entry':hex(LINK+0x3bca4),'channel':0,'threshold_ticks':15,
                'returned_elapsed_ticks':int(delay['x0'],16),'virtual_input_hz':24000000,
                'physical_clock_verified':False,'original_calls_replaced':False,
                'source_window_sha256':hashlib.sha256(raw[0x3badc:0x3bd10]).hexdigest()}
            if rsp.command('G'+caller.decode())!=b'OK' or rsp.command('g')!=caller:
                raise ValueError('Original timer caller context restoration failed')
            result['original_pwm_delay']['caller_context_restored']=True
            if rsp.command(f'Z1,{SENTINEL:x},4')!=b'OK':raise ValueError('Reinstall original LK exit observer')
            installed.append(SENTINEL)
        scsi=resume(LINK+0x3b5ac);result['scsi_return']=scsi['x0']
        result['original_request_hex']=io.get(CMD,32).hex();result['original_response_hex']=io.get(CMD+1024,48).hex()
        result['original_utrd_hex']=io.get(UTRL,32).hex()
        if int(scsi['x0'],16)!=0 or io.read(UTRL+8)&15:raise ValueError('Original SSU returned failure')
        if bytes.fromhex(result['original_request_hex'])[2]!=0xd0 or bytes.fromhex(result['original_request_hex'])[16:32]!=bytes.fromhex(contract['scsi_cdb']):
            raise ValueError('Original SSU did not actually reach UFS W-LUN')
        before=resume(LINK+0x3b5c0);result['before_store']=before
        if io.read(0x10460840)!=0:raise ValueError('Handoff appeared before original store')
        rsp.command('s');result['after_store_core']=rsp.registers();result['after_store_handoff']=io.read(0x10460840)
        if result['after_store_handoff']&0xff000000!=0xa5000000:raise ValueError('Original store did not create handoff')
        resume(SENTINEL)
        if dsp_peer:
            from abox_dsp_peer import snapshot
            release = (0x39df4, 0x39e00, 0x39e34)
            config = (0x10463400, 0x10463480, 0x10463500, 0x10463580)
            if not result.get('dsp_preboot', {}).get('passed'):
                raise ValueError('Original LK DSP release requires actual same-run UFS DMA')
            literals = struct.unpack_from('<5Q',raw,0x39f60)
            if literals != (*config[:3], 0x19c9c000, config[3]):
                raise ValueError('Original LK DSP PMU and core3 select literals changed')
            cold = snapshot(qmp)
            if any(cold[k] for k in ('abox-core0-executing','abox-core0-link-requests',
                                     'abox-core0-execution-reports')):
                raise ValueError('DSP lifecycle was active before original LK PMU release')
            for address in (LINK+release[1], LINK+release[2], SENTINEL):
                if rsp.command(f'Z1,{address:x},4') != b'OK':
                    raise ValueError('Original LK DSP release observer install failed')
                installed.append(address)
                                                                              
                                                                          
            writereg('X30',SENTINEL);writereg('PC',LINK+release[0])
            first = resume(LINK+release[1])
            if int(first['x0'],16) != config[0] or int(first['x1'],16) != 1:
                raise ValueError('Original LK core0 release store arguments differ')
            resume(LINK+release[2]);resume(SENTINEL)
            values=[io.read(address) for address in config]
            if values != [1,1,1,0]:
                raise ValueError('Original LK did not produce the three-core release configuration')
            deadline = time.monotonic() + 16
            while True:
                active = snapshot(qmp)
                if active['abox-core0-executing'] or time.monotonic() >= deadline:
                    break
                time.sleep(.05)
            result['dsp_release'] = dict(entry=hex(LINK+release[0]),
                original_first_store=hex(LINK+release[1]), config_before_core0=False,
                config_addresses=[hex(a) for a in config], config_after=values,
                same_run_ufs_dma=True, cold=cold, after=active, full_lk_boot=False)
            if (active['abox-core0-executing'] != 1 or active['abox-core0-link-requests'] != 1
                    or active['abox-core0-execution-reports'] != 1 or active['abox-core0-link-rejected']):
                raise RuntimeError('Live Xtensa core0 did not complete PMU execution handshake')
        after=io.get(LINK,0x65000)
        if after!=raw[:0x65000]:raise ValueError('Original LK executable prefix changed')
        result['code_prefix_bytes_unchanged']=len(after)
        result['devices_after']=device_snapshot(qmp)
        result['preboot_ufs_delta']=counter_delta(result['devices_before'],result['devices_after'])
        result['passed']=True
    except Exception as e:
        result['error']=str(e)
        try:qmp.execute('stop')
        except (OSError,RuntimeError):pass
        try:
                                                                            
                                                                              
            vm.save(output/'failure-native-cpu.json',{'registers':qmp.execute('human-monitor-command',
                {'command-line':'info registers -a'}),'devices':device_snapshot(qmp)})
        except (OSError,RuntimeError,ValueError):pass
        if isinstance(e,TimeoutError):
                                                                           
                                                                            
            try:
                rsp.socket.settimeout(.5)
                result['timeout_stop_reply']=rsp.receive().decode()
            except (OSError,RuntimeError,ValueError) as recovery_error:
                result['stop_drain_error']=str(recovery_error)
            finally:rsp.socket.settimeout(oldtimeout)
        try:
            result['failure_core']=rsp.registers()
            result['failure_system']={name:hex(readreg(name)) for name in ('ELR_EL2','ESR_EL2','FAR_EL2','VBAR_EL2') if name in regs}
        except (OSError,RuntimeError,ValueError):pass
        vm.save(output/'result.json',result)
    finally:
        rsp.socket.settimeout(oldtimeout)
        for a in installed:
            try:rsp.command(f'z1,{a:x},4')
            except (OSError,RuntimeError,ValueError):pass
        try:
            if systems_modified:
                                                                                  
                                                                                 
                writereg('X0',saved['TPIDR_EL1']);writereg('X2',saved['CSSELR']);writereg('X1',SENTINEL);writereg('PC',INITIALIZER)
                if rsp.command(f'Z1,{SENTINEL:x},4')!=b'OK':raise ValueError('Native restore breakpoint')
                rsp.socket.settimeout(wall_budget)
                rsp.command('vCont;c:'+thread)
                if int(rsp.registers()['pc'],16)!=SENTINEL:raise ValueError('Native system-state restore stop')
                rsp.command(f'z1,{SENTINEL:x},4');rsp.socket.settimeout(oldtimeout)
                for name,value in saved.items():
                    if readreg(name)!=value:raise ValueError('Native system-state restore '+name)
            if rsp.command('G'+initial.decode())!=b'OK' or rsp.command('g')!=initial:
                raise ValueError('Native LK original boot CPU register restoration failed')
            result['initial_cpu_registers_restored']=True;result['loader_mmio']=io.events
        except Exception as restore_error:
            result['passed']=False;result['restore_error']=str(restore_error)
            result['initial_cpu_registers_restored']=False
        result['loader_mmio']=io.events
        result['scope']='Actual original LK subroutine/callees on native QEMU CPU, explicit bootloader runtime/hardware setup, no full LK/environment/OEM disk claim'
        vm.save(output/'result.json',result)
    if not result['passed']:raise RuntimeError('Original native LK stage failed; inspect '+str(output/'result.json'))
    return result


def replay(qt,instruction_budget=500000):
    from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE,UC_HOOK_MEM_INVALID
    from unicorn.arm64_const import UC_ARM64_REG_PC,UC_ARM64_REG_X0,UC_ARM64_REG_SP,UC_ARM64_REG_LR,UC_ARM64_REG_TPIDR_EL1
    if not 1000<=instruction_budget<=1000000:raise ValueError('LK instruction budget must be1000..1000000')
    contract=lk_handoff.original_contract();raw=platform.original('lk-boot/lk.bin').read_bytes()
    uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM);uc.mem_map(LINK,0x280000);uc.mem_write(LINK,raw)
    uc.mem_map(0x40000000,0x200000)
    cache=[]
    for level in range(2):
        uc.cpr_write(3,2,0,0,0,2*level);value=uc.cpr_read(3,1,0,0,0)
        cache.append({'level':level,'ccsidr':hex(value),'setway_operations':((value>>13&32767)+1)*((value>>3&1023)+1)})
    uc.cpr_write(3,2,0,0,0,0)
    def put(address,data):uc.mem_write(address,data)
    def u32(address,value):put(address,struct.pack('<I',value))
    def u64(address,value):put(address,struct.pack('<Q',value))
                                                                              
                                                                             
    head=LINK+0x79bb0
    u64(head,BIO);u64(head+8,BIO);u64(BIO,head);u64(BIO+8,head)
    u32(BIO+16,1);u64(BIO+24,LINK+0x70d80);u64(BIO+64,BIO)
    u32(BIO+200,80);u64(BIO+288,LINK+0x52554);u64(BIO+296,LINK+0x50a6c)
    u64(LINK+0x27d520,CONTEXT);u32(LINK+0x27d530,0)
    for offset,value in ((24,0x17e00000),(32,0x17e01100),(152,CMD),(160,UTRL),(168,0x40104000)):
        u64(CONTEXT+offset,value)
    u32(CONTEXT+212,100000)
                                                                                  
    descriptor=bytearray(32);struct.pack_into('<Q',descriptor,16,CMD)
    descriptor[24:28]=bytes.fromhex('00040004')
    put(UTRL,bytes(descriptor));qt.put(UTRL,bytes(descriptor));qt.put(CMD,bytes(8192))
    uc.reg_write(UC_ARM64_REG_SP,0x400eeff0);uc.reg_write(UC_ARM64_REG_LR,SENTINEL)
    uc.reg_write(UC_ARM64_REG_TPIDR_EL1,THREAD)
    dirty=set();last=deque(maxlen=96);events=[];calls=[];faults=[];failure=None;instructions=0;cache_operations=0
    def stop(message):
        nonlocal failure
        failure=message;uc.emu_stop()
    def flush():
                                                                              
                                                                              
                                                                          
        for address in sorted(dirty):qt.put(address,bytes(uc.mem_read(address,64)))
        dirty.clear()
    def data_write(engine,access,address,size,value,data):
        for a in range(address&~63,(address+size+63)&~63,64):dirty.add(a)
    def data_read(engine,access,address,size,value,data):
        flush();engine.mem_write(address,qt.get(address,size))
    uc.hook_add(UC_HOOK_MEM_WRITE,data_write,begin=UTRL,end=CMD+8192-1)
    uc.hook_add(UC_HOOK_MEM_READ,data_read,begin=UTRL,end=CMD+8192-1)
    def mmio_read(engine,offset,size,base):
        flush();address=base+offset
        if size!=4:stop('Unsupported original MMIO read width '+str(size));return 0
        value=qt.read(address)
        if len(events)<512:events.append({'operation':'read','pc':hex(engine.reg_read(UC_ARM64_REG_PC)),'address':hex(address),'value':value})
        return value
    def mmio_write(engine,offset,size,value,base):
        flush();address=base+offset
        if size!=4:stop('Unsupported original MMIO write width '+str(size));return
        if len(events)<512:events.append({'operation':'write','pc':hex(engine.reg_read(UC_ARM64_REG_PC)),'address':hex(address),'value':value})
        qt.write(address,value)
    for address,length in ((0x10460000,0x10000),(0x17e00000,0x2000)):
        uc.mmio_map(address,length,mmio_read,address,mmio_write,address)
    labels={0x3b59c:'handoff',0x3b548:'environment-save-attempt',0x535f0:'scsi-stop',0x50378:'build-original-ssu',
            0x52554:'original-ufs-scsi-exec',0x50b8c:'original-upiu-build',0x51448:'original-hci-submit',
            0x3b5a8:'environment-return',0x3b5ac:'scsi-return'}
    def code(engine,address,size,data):
        nonlocal instructions,cache_operations
        instructions+=1;last.append(hex(address))
        if address==SENTINEL:engine.emu_stop();return
        if not LINK<=address<LINK+len(raw):stop('Unexpected original execution target '+hex(address));return
        offset=address-LINK
        if offset==0x3a4ac:cache_operations+=1
        if bytes(engine.mem_read(address,4))!=raw[offset:offset+4]:stop('Original executed instruction changed');return
        if offset in labels:calls.append({'name':labels[offset],'pc':hex(address),'x0':hex(engine.reg_read(UC_ARM64_REG_X0))})
        if offset in (0x5db00,0x5f788):stop('Original assertion/diagnostic path '+hex(offset));return
        if instructions%512==0:qt.call('clock_step 512')
    uc.hook_add(UC_HOOK_CODE,code)
    def invalid(engine,access,address,size,value,data):
        faults.append({'access':access,'address':hex(address),'size':size,'pc':hex(engine.reg_read(UC_ARM64_REG_PC))})
        return False
    uc.hook_add(UC_HOOK_MEM_INVALID,invalid)
    try:uc.emu_start(LINK+0x3b59c,SENTINEL,count=instruction_budget)
    except Exception as e:failure=str(e)
    flush();pc=uc.reg_read(UC_ARM64_REG_PC)
    if pc!=SENTINEL and failure is None:failure='Original execution exhausted bounded instruction budget'
    return {'source':contract['source'],'sha256':hashlib.sha256(raw).hexdigest(),'linked_fixture':hex(LINK),
            'instructions':instructions,'pc':hex(pc),'returned':pc==SENTINEL and failure is None,
            'cache_geometry':cache,'cache_setway_instructions':cache_operations,'instruction_budget':instruction_budget,
            'return_value':uc.reg_read(UC_ARM64_REG_X0),'error':failure,'memory_faults':faults,'last_pcs':list(last),'calls':calls,'mmio':events,
            'upiu_request_hex':qt.get(CMD,32).hex(),'upiu_response_hex':qt.get(CMD+1024,48).hex(),
            'utrd_hex':qt.get(UTRL,32).hex(),'pmu_handoff':qt.read(0x10460840),
            'original_code_modified':False,'original_calls_replaced':False,'full_lk_boot_verified':False,
            'scope':'Original whole3B59C and callee instructions, explicit initialized runtime fixture, real QEMU MMIO/DMA; not full LK or guest boot'}
