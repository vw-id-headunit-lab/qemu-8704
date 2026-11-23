




import argparse
from collections import deque
import hashlib
from pathlib import Path
import struct
import original_8704_vm as vm
import vlm_platform as platform
from bl2_mcu import MailboxRegisters, MClassSystemRegisters

SRAM_BASE=0x02039000
SRAM_SIZE=0x38000
BL2_BASE=0x02100000


def scatter_load(bl2):






    if len(bl2)!=0x61000:raise ValueError('Unexpected original BL2 size')
    result=bytearray(SRAM_SIZE);segments=[]
    for routine,slots,expected in (
        (0x22c,(0x3d0,0x408,0x3e0),(0x02117400,0x0211c400,0x0203c000)),
        (0x254,(0x408,0x410,0x418),(0x0211c400,0x0213e400,0x02044000))):
                                                                               
        instructions=struct.unpack_from('<10I',bl2,routine)
        for index,slot in enumerate(slots):
            pc=routine+4*index
            expected_word=0x58000000|(((slot-pc)//4&0x7ffff)<<5)|index
            if instructions[index]!=expected_word:raise ValueError('BL2 literal-load contract changed')
        if instructions[3:]!=(0xa8c11003,0xa8811043,0xeb01001f,0x54ffffab,0xd5033f9f,0xd5033fdf,0xd65f03c0):
            raise ValueError('BL2 copy-loop contract changed')
        source,end,destination=(struct.unpack_from('<Q',bl2,slot)[0] for slot in slots)
        if (source,end,destination)!=expected:raise ValueError('BL2 scatter literals changed')
        offset=source-BL2_BASE;local=destination-SRAM_BASE;size=end-source
        if size<=0 or size%16 or not 0<=offset<=len(bl2)-size or not 0<=local<=SRAM_SIZE-size:
            raise ValueError('BL2 scatter range outside original input/SRAM')
        result[local:local+size]=bl2[offset:offset+size]
        segments.append({'routine':hex(routine),'literal_slots':[hex(s) for s in slots],
            'source_file_offset':hex(offset),'source_linked_address':hex(source),
            'ap_destination':hex(destination),'local_destination':hex(local),'bytes':size,
            'sha256':hashlib.sha256(bl2[offset:offset+size]).hexdigest()})
    if struct.unpack_from('<4I',result,0x3000)!=(0xb000,0x3259,0x3279,0x327f):
        raise ValueError('ACPM relocated vectors changed')
    return bytes(result),{'sram_base':hex(SRAM_BASE),'sram_bytes':SRAM_SIZE,'bl2_linked_base':hex(BL2_BASE),
        'segments':segments,'entry_vector_local':'0x3000','reset_entry':'0x3259','initial_sp':'0xb000',
        'scope':'Verified two BL2 SRAM copies; reset ROM, DRAM tables and full platform state not reconstructed',
        'cold_zero_backing_is_virtual_initial_condition':True}


def original_image():
    source=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')
    raw,meta=scatter_load(source.read_bytes())
    meta.update(source=str(source),source_sha256=vm.sha(source),loaded_sram_sha256=hashlib.sha256(raw).hexdigest())
    return raw,meta


def public_metadata(memory):

    if len(memory)!=SRAM_SIZE:raise ValueError('Require complete local SRAM')
    words=struct.unpack_from('<48I',memory,0x7000)
    plugins,channels=words[0],words[2]
    def table(pointer,count,stride):
        if not count:return []
        if count>32 or pointer%4 or not 0<=pointer<=len(memory)-count*stride:return None
        return [{'index':i,'local_address':hex(pointer+i*stride),
            'words':[hex(n) for n in struct.unpack_from('<'+'I'*(stride//4),memory,pointer+i*stride)]}
            for i in range(count)]
    return {'header_local':'0x7000','header_ap':'0x2040000','header_words':[hex(n) for n in words],
        'plugin_count':words[5],'channel_count':words[6],
        'plugins':table(plugins,words[5],48),'channels':table(channels,words[6],72),
        'all_runtime_initializers_completed':False,'fvmap_verified':False,
        'usable_for_native_boot':False}


def execute(memory,instructions=100000,mailbox=False,system_registers=False,counter_config=False,reference_divider=0):
    from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_WRITE
    from unicorn.arm_const import UC_CPU_ARM_CORTEX_M3, UC_ARM_REG_MSP, UC_ARM_REG_SP, UC_ARM_REG_XPSR, UC_ARM_REG_PC, UC_ARM_REG_LR
    if len(memory)!=SRAM_SIZE or not 1<=instructions<=1000000:raise ValueError('Invalid isolated ACPM execution bounds')
    sp,entry=struct.unpack_from('<2I',memory,0x3000)
    if not entry&1 or not 0<=entry<SRAM_SIZE or not 0<sp<=SRAM_SIZE:raise ValueError('Invalid ACPM reset vectors')
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS);uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M3)
    uc.mem_map(0,SRAM_SIZE);uc.mem_write(0,memory)
    uc.reg_write(UC_ARM_REG_MSP,sp);uc.reg_write(UC_ARM_REG_SP,sp);uc.reg_write(UC_ARM_REG_XPSR,0x01000000)
    trace=deque(maxlen=64);writes=deque(maxlen=192);events=deque(maxlen=256);visits=[];executed=0;stopped=None
    def stop(reason,**details):
        nonlocal stopped
        if stopped is None:stopped={'reason':reason,'pc':hex(uc.reg_read(UC_ARM_REG_PC)),**details}
        uc.emu_stop()
    def code(engine,address,size,data):
        nonlocal executed
        executed+=1;trace.append({'pc':hex(address),'bytes':bytes(engine.mem_read(address,size)).hex()})
        if address in (0x3258,0x3c08,0x4528,0x4488,0x43dc,0xb000,0xb800,0xcc00,0xe000,0x13000,0x1e000):
            visits.append(hex(address))
    def write(engine,access,address,size,value,data):
        if 0x7000<=address<0x70c0:writes.append({'pc':hex(engine.reg_read(UC_ARM_REG_PC)),'address':hex(address),'size':size,'value':hex(value)})
    def invalid(engine,access,address,size,value,data):
        stop('unsupported_memory_or_mmio',access=access,address=hex(address),size=size,value=hex(value));return False
    models={base:MailboxRegisters() for base in (0xa0500000,0xa0510000,0xa0580000)}
    def mread(engine,offset,size,base):
        try:value=models[base].read(offset,size)
        except ValueError as exc:stop('unsupported_mailbox_read',address=hex(base+offset),detail=str(exc));return 0
        events.append({'operation':'mailbox_read','address':hex(base+offset),'value':hex(value)});return value
    def mwrite(engine,offset,size,value,base):
        events.append({'operation':'mailbox_write','address':hex(base+offset),'value':hex(value)})
        try:models[base].write(offset,size,value)
        except ValueError as exc:stop('unsupported_mailbox_write',address=hex(base+offset),detail=str(exc));return
        if models[base].pending[1]&~models[base].mask[1]:stop('mailbox_interrupt_delivery_required',base=hex(base))
    core=MClassSystemRegisters(reference_divider,external_lines=64)                                                                           
    def cread(engine,offset,size,data):
        try:value=core.read(offset,size,executed)
        except ValueError as exc:stop('unsupported_core_read',address=hex(0xe000e000+offset),detail=str(exc));return 0
        events.append({'operation':'core_read','address':hex(0xe000e000+offset),'value':hex(value)});return value
    def cwrite(engine,offset,size,value,data):
        events.append({'operation':'core_write','address':hex(0xe000e000+offset),'value':hex(value)})
        try:core.write(offset,size,value,executed)
        except ValueError as exc:stop('unsupported_core_write',address=hex(0xe000e000+offset),detail=str(exc))
    counter={0:0,4:0,8:0}
    def wread(engine,offset,size,data):
                                                                                  
        stop('apm_counter_countdown_or_unsupported_read',address=hex(0xa0430000+offset),size=size);return 0
    def wwrite(engine,offset,size,value,data):
        if size!=4 or offset not in (0,4,8):
            stop('unsupported_counter_configuration_write',address=hex(0xa0430000+offset),size=size);return
        counter[offset]=value
        events.append({'operation':'counter_configuration_write','address':hex(0xa0430000+offset),'value':hex(value)})
    uc.hook_add(UC_HOOK_CODE,code);uc.hook_add(UC_HOOK_MEM_WRITE,write);uc.hook_add(UC_HOOK_MEM_INVALID,invalid)
    if mailbox:
        for base in models:uc.mmio_map(base,0x1000,mread,base,mwrite,base)
    if system_registers:uc.mmio_map(0xe000e000,0x1000,cread,None,cwrite,None)
    if counter_config:uc.mmio_map(0xa0430000,0x1000,wread,None,wwrite,None)
    error=None
    try:uc.emu_start(entry,SRAM_SIZE,timeout=2000000,count=instructions)
    except UcError as exc:error={'code':exc.errno,'message':str(exc)}
    after=bytes(uc.mem_read(0,SRAM_SIZE))
    result={'instructions':executed,'stopped':stopped,'engine_error':error,'last_instructions':list(trace),
        'header_write_events':list(writes),'mmio_events':list(events),'entry_visits':visits,
        'registers':{'pc':hex(uc.reg_read(UC_ARM_REG_PC)),'sp':hex(uc.reg_read(UC_ARM_REG_SP)),'lr':hex(uc.reg_read(UC_ARM_REG_LR))},
        'metadata':public_metadata(after),'mailbox_models':mailbox,'system_register_model':system_registers,
        'nvic_external_lines_modelled':64 if system_registers else 0,
        'counter_configuration_only':counter_config,'apm_counter_countdown_modelled':False,
        'conditional_systick_reference_divider':reference_divider,
        'clock_provenance':'Explicit instruction-tick scenario, not measured hardware timing' if reference_divider else 'External reference unknown; execution stops when required',
        'cpu_model':'Cortex-M3 ISA-compatible analysis hypothesis; exact physical core unverified',
        'initial_register_condition':'Engine zeroed GPRs except supplied SP/PC/XPSR; no reset-ROM handoff emulated',
        'reset_rom_executed':False,'ap_connected':False,'hardware_state_verified':False,
        'full_acpm_boot_verified':False,'three_domain_boot_completed':False,'native_boot_usable':False,
        'termination':'dependency_stop' if stopped else 'engine_error' if error else 'bounded_return_or_budget'}
    return result,after


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--instructions',type=int,default=100000)
    parser.add_argument('--mailbox',action='store_true',help='Stateful original mailbox layout, no automatic ACK or AP connection')
    parser.add_argument('--system-registers',action='store_true',help='Architectural register subset only, not IRQ delivery or verified timing')
    parser.add_argument('--counter-config',action='store_true',help='Capture original APM counter/timer configuration (device identity not independently confirmed); counter reads stop, no invented timing')
    parser.add_argument('--systick-reference-divider',type=int,default=0,help='Conditional instruction-tick analysis only; 0 stops on unknown external reference')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require a new scratch directory')
    memory,identity=original_image();output.mkdir(parents=True)
    result,after=execute(memory,args.instructions,args.mailbox,args.system_registers,args.counter_config,args.systick_reference_divider);result['input']=identity
    (output/'loaded-sram-before.bin').write_bytes(memory);(output/'executed-sram-partial.bin').write_bytes(after)
    analysis=output/'loaded-sram-analysis-only.elf';analysis.write_bytes(platform.arm32_analysis_elf(memory,0x3259))
    result['analysis_elf']={'path':str(analysis),'sha256':vm.sha(analysis),'not_a_boot_image':True}
    result['partial_sram_sha256']=hashlib.sha256(after).hexdigest();vm.save(output/'result.json',result)
    print(__import__('json').dumps({k:result[k] for k in ('instructions','stopped','engine_error','full_acpm_boot_verified')}))


if __name__=='__main__':main()
