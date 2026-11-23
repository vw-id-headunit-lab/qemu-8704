





import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import struct
import original_8704_vm as vm
import vlm_platform as platform


class MClassSystemRegisters:





    def __init__(self,reference_divider=0,external_lines=32):
        if not 0<=reference_divider<=1024:raise ValueError('Invalid conditional SysTick reference divider')
        if external_lines%32 or not 32<=external_lines<=224:raise ValueError('Require a bounded whole NVIC word count')
        self.reference_divider=reference_divider
        self.external_lines=external_lines
        self.enable=0;self.pending=0;self.priority=bytearray(external_lines)
        self.control=0;self.reload=0;self.current=0;self.flag=0;self.tick=0
    def advance(self,tick):
        previous=self.tick;delta=tick-previous;self.tick=tick
        if not self.control&1 or not delta:return
        if not self.control&4:
            if not self.reference_divider:raise ValueError('SysTick external reference clock is not specified')
            delta=tick//self.reference_divider-previous//self.reference_divider
            if not delta:return
        if not self.reload:self.current=0;return
        period=self.reload+1
        until_zero=self.current if self.current else period
        if delta>=until_zero:
            self.flag=1
            if self.control&2:raise ValueError('SysTick expiration requires interrupt delivery')
        self.current=(self.current-delta)%period if self.current else self.reload-((delta-1)%period)
    def read(self,offset,size,tick):
        self.advance(tick)
        if 0x400<=offset<0x400+self.external_lines and size in (1,2,4) and offset+size<=0x400+self.external_lines:
            return int.from_bytes(self.priority[offset-0x400:offset-0x400+size],'little')
        if size!=4 or offset%4:raise ValueError('Unsupported core register width/alignment')
        if offset==0x10:
            result=self.control|(self.flag<<16);self.flag=0;return result
        if offset==0x14:return self.reload
        if offset==0x18:return self.current
        if offset==0x1c:raise ValueError('SysTick calibration value is not known for this SoC')
        for base,value in ((0x100,self.enable),(0x180,self.enable),(0x200,self.pending),(0x280,self.pending),(0x300,0)):
            if base<=offset<base+32:
                shift=(offset-base)*8
                return (value>>shift)&0xffffffff if shift<self.external_lines else 0
        if offset==0xd00:return 0x410fc231                                                           
        if offset==0xd04:return self.pending and (1<<22) or 0
        raise ValueError('Unsupported architectural core register read '+hex(offset))
    def write(self,offset,size,value,tick):
        self.advance(tick)
        if 0x400<=offset<0x400+self.external_lines and size in (1,2,4) and offset+size<=0x400+self.external_lines:
            self.priority[offset-0x400:offset-0x400+size]=bytes(b&0xe0 for b in value.to_bytes(size,'little'));return
        if size!=4 or offset%4:raise ValueError('Unsupported core register width/alignment')
        if offset==0x10:
            if value&1 and not value&4 and not self.reference_divider:raise ValueError('SysTick external reference clock is not specified')
            self.control=value&7;return
        if offset==0x14:self.reload=value&0xffffff;self.current=min(self.current,self.reload);return
        if offset==0x18:self.current=0;self.flag=0;return
        for base,attribute,clear in ((0x100,'enable',False),(0x180,'enable',True),(0x200,'pending',False),(0x280,'pending',True)):
            if base<=offset<base+32:
                shift=(offset-base)*8
                if shift<self.external_lines:
                    bits=value<<shift
                    old=getattr(self,attribute);setattr(self,attribute,old&~bits if clear else old|bits)
                    if self.enable&self.pending:raise ValueError('External interrupt delivery is required')
                return
        raise ValueError('Unsupported architectural core register write '+hex(offset))


class MailboxRegisters:

    def __init__(self):self.pending=[0,0];self.mask=[0xffff0000,0xffff];self.shared=[0]*16
    def read(self,offset,size):
        if size!=4 or offset%4:raise ValueError('Unsupported mailbox access width')
        if 0x80<=offset<0xc0:return self.shared[(offset-0x80)//4]
        bank=int(offset>=0x1c);reg=offset-20*bank
        if reg in (8,12):return 0
        if reg==16:return self.mask[bank]
        if reg==20:return self.pending[bank]
        if reg==24:return self.pending[bank]&~self.mask[bank]
        raise ValueError('Unsupported mailbox read '+hex(offset))
    def write(self,offset,size,value):
        if size!=4 or offset%4:raise ValueError('Unsupported mailbox access width')
        if 0x80<=offset<0xc0:self.shared[(offset-0x80)//4]=value;return
        bank=int(offset>=0x1c);reg=offset-20*bank;value&=0xffff if bank else 0xffff0000
        if reg==8:self.pending[bank]|=value
        elif reg==12:self.pending[bank]&=~value
        elif reg==16:self.mask[bank]=value
        else:raise ValueError('Unsupported mailbox write '+hex(offset))


def original_candidate():
    source=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')
    full=source.read_bytes();raw=full[0x54c00:0x54c00+0xc000]
    if len(raw)!=0xc000 or struct.unpack_from('<4I',raw)!=(0x10000,0x81,0x1429,0xb5):
        raise ValueError('Original M-class vector/size contract changed')
    literals=struct.unpack_from('<12I',raw,0xbc4)
    if literals!=(0,0x1900,0x3000,0x2400,0x1900,0x1700,0x9800,0x2800,0x5400,0x1000,0x6400,0x3400):
        raise ValueError('Original sub_B44 initializer literals changed')
    context,bss_size,bss=struct.unpack_from('<3I',raw,0x82c)
    if (context,bss_size,bss)!=(0xe000,0x4000,0xc000):raise ValueError('M-class private RAM contract changed')
    return raw,{'source':str(source),'source_sha256':vm.sha(source),'source_offset':0x54c00,'image_bytes':len(raw),
        'image_sha256':hashlib.sha256(raw).hexdigest(),'local_context':hex(context),
        'bss_base':hex(bss),'bss_bytes':bss_size,'static_initializer':'sub_B44',
        'plugin_spans':[{'entry':hex(a),'bytes':b} for a,b in ((0,0x1900),(0x1900,0x1700),(0x3000,0x2400),(0x5400,0x1000),(0x6400,0x3400),(0x9800,0x2800))],
        'plugin_names':['FRM','ARR','S2D','ELD','ECD','BCM'],
        'candidate_role':'Debug/advanced-tracer role inferred from plugin names; not identified as ACPM',
        'scope':'Local MCU coordinates, not yet mapped to AP-visible ACPM SRAM'}


def execute(raw,instructions=100000,watchdog=False,read_inputs=None,system_registers=False,reference_divider=0,mailbox=False,chipid=False,boot_marker=False,debug_dram=False):
    from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
    from unicorn.arm_const import (UC_CPU_ARM_CORTEX_M3, UC_ARM_REG_SP, UC_ARM_REG_MSP,
        UC_ARM_REG_XPSR, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1)
    if len(raw)>0xc000 or len(raw)<8 or not 1<=instructions<=1000000:raise ValueError('MCU analysis bounds exceeded')
    sp,entry=struct.unpack_from('<2I',raw)
    if not 0<sp<=0x10000 or not entry&1 or entry>=len(raw):raise ValueError('Invalid M-class reset vector')
    read_inputs=dict(read_inputs or {})
    if len(read_inputs)>16 or any(a%4 or not 0xa0000000<=a<0xb0000000 or not 0<=v<2**32 for a,v in read_inputs.items()):
        raise ValueError('Conditional read inputs require at most 16 aligned local MMIO u32 values')
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS);uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M3)
    uc.mem_map(0,0x10000);uc.mem_write(0,bytes(raw))
    if debug_dram:uc.mem_map(0x80003000,0x100000)                                                                  
    uc.reg_write(UC_ARM_REG_MSP,sp);uc.reg_write(UC_ARM_REG_SP,sp);uc.reg_write(UC_ARM_REG_XPSR,0x01000000)
    trace=deque(maxlen=64);events=[];executed=0;stopped=None;plugin_visits=[]
    registers={base:{0:0,4:0,8:0} for base in (0xa0780000,0xa0790000,0xa07a0000)}
    def stop(reason,**values):
        nonlocal stopped
        if stopped is None:stopped={'reason':reason,'pc':hex(uc.reg_read(UC_ARM_REG_PC)),**values}
        uc.emu_stop()
    def code(engine,address,size,data):
        nonlocal executed
        executed+=1;trace.append({'pc':hex(address),'bytes':bytes(engine.mem_read(address,size)).hex()})
        if address in (0x1900,0x3000,0x5400,0x6400,0x9800):plugin_visits.append(hex(address))
    def invalid(engine,access,address,size,value,data):
        stop('unsupported_memory_or_mmio',access=access,address=hex(address),size=size,value=hex(value))
        return False
    def wdt_read(engine,offset,size,base):
        if size!=4 or offset not in (0,4):
            stop('unsupported_watchdog_read_or_countdown',offset=hex(offset),size=size);return 0
        events.append({'operation':'read','address':hex(base+offset),'value':hex(registers[base][offset])})
        return registers[base][offset]
    def wdt_write(engine,offset,size,value,base):
        if size!=4 or offset not in (0,4,8,12):
            stop('unsupported_watchdog_write',offset=hex(offset),size=size);return
        events.append({'operation':'write','address':hex(base+offset),'value':hex(value)})
        if offset!=12:registers[base][offset]=value                                                 
    def conditional_read(engine,offset,size,base):
        address=base+offset
        if size!=4 or address not in read_inputs:
            stop('unsupported_conditional_page_read',address=hex(address),size=size);return 0
        value=read_inputs[address]
        events.append({'operation':'conditional_read','address':hex(address),'value':hex(value),
            'provenance':'Explicit scenario condition, not observed or verified hardware state'})
        return value
    def conditional_write(engine,offset,size,value,base):
        stop('unsupported_conditional_page_write',address=hex(base+offset),size=size,value=hex(value))
    core=MClassSystemRegisters(reference_divider)
    def core_read(engine,offset,size,data):
        try:value=core.read(offset,size,executed)
        except ValueError as exc:stop('unsupported_core_read',address=hex(0xe000e000+offset),detail=str(exc));return 0
        events.append({'operation':'core_read','address':hex(0xe000e000+offset),'value':hex(value)})
        return value
    def core_write(engine,offset,size,value,data):
        events.append({'operation':'core_write','address':hex(0xe000e000+offset),'value':hex(value)})
        try:core.write(offset,size,value,executed)
        except ValueError as exc:stop('unsupported_core_write',address=hex(0xe000e000+offset),detail=str(exc))
    mbox=MailboxRegisters()
    def mailbox_read(engine,offset,size,data):
        try:value=mbox.read(offset,size)
        except ValueError as exc:stop('unsupported_mailbox_read',address=hex(0xa04c0000+offset),detail=str(exc));return 0
        events.append({'operation':'mailbox_read','address':hex(0xa04c0000+offset),'value':hex(value)});return value
    def mailbox_write(engine,offset,size,value,data):
        events.append({'operation':'mailbox_write','address':hex(0xa04c0000+offset),'value':hex(value)})
        try:mbox.write(offset,size,value)
        except ValueError as exc:stop('unsupported_mailbox_write',address=hex(0xa04c0000+offset),detail=str(exc))
        if mbox.pending[1]&~mbox.mask[1]:stop('mailbox_receive_interrupt_delivery_required')
    def chip_read(engine,offset,size,data):
        values={0:0xaaa80000,4:0,8:0,0x10:1<<20,0x24:0}                                                          
        if size!=4 or offset not in values:stop('unsupported_chipid_read',offset=hex(offset),size=size);return 0
        events.append({'operation':'chipid_read','address':hex(0xa0000000+offset),'value':hex(values[offset])})
        return values[offset]
    def chip_write(engine,offset,size,value,data):stop('write_to_readonly_chipid',offset=hex(offset))
    def marker_read(engine,offset,size,data):
        stop('unsupported_boot_marker_read',offset=hex(offset),size=size);return 0
    def marker_write(engine,offset,size,value,data):
        if offset!=0x488 or size!=4:stop('unsupported_boot_marker_write',offset=hex(offset),size=size);return
        events.append({'operation':'outgoing_boot_marker_write','address':'0xa0420488','value':hex(value),
            'scope':'Captured firmware write only; no inferred AP interrupt, reset or reply'})
    uc.hook_add(UC_HOOK_CODE,code);uc.hook_add(UC_HOOK_MEM_INVALID,invalid)
    if watchdog:
        for base in registers:uc.mmio_map(base,0x1000,wdt_read,base,wdt_write,base)
    if system_registers:uc.mmio_map(0xe000e000,0x1000,core_read,None,core_write,None)
    if mailbox:uc.mmio_map(0xa04c0000,0x1000,mailbox_read,None,mailbox_write,None)
    if chipid:uc.mmio_map(0xa0000000,0x1000,chip_read,None,chip_write,None)
    if boot_marker:uc.mmio_map(0xa0420000,0x1000,marker_read,None,marker_write,None)
    for base in sorted({address&~4095 for address in read_inputs}):
        if (watchdog and base in registers or mailbox and base==0xa04c0000 or chipid and base==0xa0000000
                or boot_marker and base==0xa0420000):raise ValueError('Conditional input overlaps a hardware model')
        uc.mmio_map(base,0x1000,conditional_read,base,conditional_write,base)
    error=None
    try:uc.emu_start(entry,0x10000,timeout=2000000,count=instructions)
    except UcError as exc:error={'code':exc.errno,'message':str(exc)}
    memory=bytes(uc.mem_read(0,0x10000))
    result={'cpu_model':'cortex-m3 ISA-compatible candidate hypothesis; exact physical core unverified','instructions':executed,'stopped':stopped,'engine_error':error,
        'registers':{n:hex(uc.reg_read(r)) for n,r in (('pc',UC_ARM_REG_PC),('sp',UC_ARM_REG_SP),('lr',UC_ARM_REG_LR),('r0',UC_ARM_REG_R0),('r1',UC_ARM_REG_R1))},
        'last_instructions':list(trace),'mmio_events':events,'watchdog_configuration_model':watchdog,
        'watchdog_countdown_modelled':False,'nvic_registers_modelled':system_registers,
        'interrupt_delivery_modelled':False,'nvic_external_lines_modelled':32,'nvic_priority_bits_modelled':3,
        'systick_clock':'Instruction ticks, not verified physical timing' if system_registers else None,'acpm_identity_verified':False,
        'conditional_systick_reference_divider':reference_divider,
        'local_mailbox_modelled':mailbox,'local_mailbox_pending':mbox.pending,'local_mailbox_masks':mbox.mask,
        'local_chipid_modelled':chipid,'chipid_identity':'Same virtual native-QEMU identity, not physical ECU identity' if chipid else None,
        'outgoing_boot_marker_captured':boot_marker,
        'public_acpm_sram_generated_verified':False,'three_domain_boot_completed':False,
        'loaded_image_bytes_unchanged':memory[:len(raw)]==raw,
        'plugin_entry_visits':plugin_visits,
        'private_context_words':[hex(n) for n in struct.unpack_from('<52I',memory,0xe000)],
        'private_plugin_descriptors':[{'index':i,'init':hex(struct.unpack_from('<I',memory,0x1640+40*i+4)[0]),
            'callback_table':hex(struct.unpack_from('<I',memory,0x1640+40*i+12)[0]),
            'enabled':bool(memory[0x1640+40*i+24]),'image_bytes':struct.unpack_from('<I',memory,0x1640+40*i+28)[0]} for i in range(6)],
        'conditional_read_inputs':{hex(a):hex(v) for a,v in read_inputs.items()},
        'hardware_state_verified':False,'public_sram_usable_for_boot':False}
    dram=bytes(uc.mem_read(0x80003000,0x100000)) if debug_dram else None
    result['debug_dram_supplied']=debug_dram
    result['debug_dram_ap_physical_mapping_verified']=False
    return result,memory,dram


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--instructions',type=int,default=100000)
    parser.add_argument('--watchdog',action='store_true',help='Model observed four watchdog configuration registers, not countdown')
    parser.add_argument('--read-input',action='append',default=[],help='Explicit conditional MMIO read address=value; not hardware truth or an automatic ready response')
    parser.add_argument('--system-registers',action='store_true',help='M-class NVIC register subset and instruction-tick SysTick; stops if IRQ delivery/reset is required')
    parser.add_argument('--systick-reference-divider',type=int,default=0,help='Explicit conditional reference-clock divisor in instruction ticks; 0 means unknown and stops')
    parser.add_argument('--mailbox',action='store_true',help='Model observed local mailbox registers at A04C0000; no AP connection or auto reply')
    parser.add_argument('--chipid',action='store_true',help='Same virtual ChipID values as native QEMU, at the observed MCU alias')
    parser.add_argument('--boot-marker',action='store_true',help='Capture observed outgoing write at A0420488 only; no inferred AP effects or read response')
    parser.add_argument('--debug-dram',action='store_true',help='Supply zeroed RAM at the exact local debug area 80003000/1MiB; AP translation unverified')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require a new scratch directory')
    raw,identity=original_candidate();output.mkdir(parents=True)
    inputs={}
    for text in args.read_input:
        address,value=(int(s,0) for s in text.split('=',1))
        if address in inputs:raise ValueError('Duplicate conditional read address')
        inputs[address]=value
    result,memory,dram=execute(raw,args.instructions,args.watchdog,inputs,args.system_registers,args.systick_reference_divider,args.mailbox,args.chipid,args.boot_marker,args.debug_dram)
    (output/'local-tcm.bin').write_bytes(memory);result['input']=identity
    if dram is not None:(output/'local-debug-dram.bin').write_bytes(dram)
    vm.save(output/'result.json',result)
    print(json.dumps({k:result[k] for k in ('instructions','stopped','engine_error','acpm_identity_verified')}))


if __name__=='__main__':main()
