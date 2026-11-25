





from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import original_8704_vm as vm
sys.path.insert(0, str(vm.ROOT/'src/firmware'))
from inspect_debug_device_tree import parse_dtb, render
from capstone import Cs, CS_ARCH_ARM64, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_MCLASS

ORIGINAL = vm.ROOT/'var/inputs/support/display-chain-8704/original-platform/hyp-binary'
BOOT = vm.ROOT/'var/inputs/support/vlm-exynos-bringup/original-boot'
ROUTINE_OEM_SECONDS = 240


def accelerated_seed_window(seconds, *, prepare=False, accelerated=False):

    return prepare is True and accelerated is True and seconds in (ROUTINE_OEM_SECONDS,600)


def native_ppi27_path(qmp):
    prop = 'x-auto9-ppi27-state'
    matches = []
    for parent in ('/machine', '/machine/unattached'):
        for row in qmp.execute('qom-list', {'path': parent}):
            if row['type'] != 'child<arm_gic>':
                continue
            path = parent + '/' + row['name']
            if (qmp.execute('qom-get', {'path': path, 'property': 'num-irq'}) > 260 and
                    prop in {field['name'] for field in qmp.execute('qom-list', {'path': path})}):
                matches.append(path)
    if len(matches) != 1:
        raise ValueError('No unique native GICv2 PPI27 observer')
    return matches[0]


def tcg_execution_policy(native_parallel,board_adapter,management_clock,cpus,steps,icount,host_platform):

    if native_parallel:
        if (not board_adapter or not management_clock or cpus<2 or steps or icount is not None
                or host_platform not in ('win32','darwin')):
            raise ValueError('Native parallel TCG requires a Windows multi-CPU board with no instruction clock or boot steps')
        return 'tcg,thread=multi,x-auxiliary-vcpu=on',dict(
            thread_model='multi',clock='qemu-virtual-wall',auxiliary_vcpu_contexts=1,physical_cycle_accuracy_claimed=False)
    return ('tcg,thread=single' if management_clock else 'tcg',
            dict(thread_model='single' if management_clock else 'default',
                 clock='instruction' if icount is not None else 'qemu-virtual-wall',physical_cycle_accuracy_claimed=False))


def native_parallel_threads(rows, application_cpus):

    threads=[]
    for row in rows:
        path=row.get('qom-path','')
        cpu,thread=row.get('cpu-index'),row.get('thread-id')
        if (type(cpu) is not int or cpu<0 or type(thread) is not int or thread<=0
                or not isinstance(path,str) or not path.startswith('/machine/')):
            raise ValueError('Invalid native parallel TCG CPU readback')
        threads.append(dict(cpu=cpu,host_thread=thread,qom_path=path,
                            role='acpm' if path.startswith('/machine/exynos-apm-core/') else 'ap'))
    if (len(threads)!=application_cpus+1 or len({r['host_thread'] for r in threads})!=len(threads)
            or sorted(r['cpu'] for r in threads if r['role']=='ap')!=list(range(application_cpus))
            or [r['cpu'] for r in threads if r['role']=='acpm']!=[application_cpus]):
        raise ValueError('Native parallel TCG requires distinct host threads for all AP CPUs and ACPM')
    return threads


def ufs_runtime_trace_events(mode, has_disk):
    if mode == 'boot':
                                                                      
                                                                         
                                                                        
        return ('ufs_process_uiccmd','ufs_native_read_result','ufs_err_scsi_cmd_invalid_lun',
                'ufs_err_dma_read_utrd','ufs_err_dma_read_req_upiu','ufs_err_dma_read_prdt',
                'ufs_err_dma_write_utrd','ufs_err_dma_write_rsp_upiu',
                'ufs_err_utrl_slot_error','ufs_err_utrl_slot_busy')
    events=('ufs_process_uiccmd','ufs_mmio_write','ufs_irq_raise','ufs_irq_lower','ufs_native_read_result',
            'ufs_process_req','ufs_exec_nop_cmd','ufs_exec_query_cmd','ufs_exec_scsi_cmd',
            'ufs_complete_req','ufs_sendback_req','ufs_err_scsi_cmd_invalid_lun')
    if has_disk:
        events+=('scsi_disk_new_request','scsi_disk_read_complete','scsi_disk_read_data_count','blk_co_preadv')
    return events


def rebuild_fdt(raw,nodes):

    h=struct.unpack_from('>10I',raw)
    if h[0]!=0xd00dfeed or h[5]!=17:raise ValueError('Require verified FDT v17')
    reservations=bytearray();position=h[4]
    while True:
        if position>h[1]-16:raise ValueError('FDT reservations outside image')
        pair=raw[position:position+16];position+=16;reservations+=pair
        if pair==b'\0'*16:break
    strings=bytearray();names={};children={name:[] for name in nodes}
    for name in nodes:
        if name!='/':
            parent=name.rsplit('/',1)[0] or '/'
            if parent not in children:raise ValueError('FDT node has no parent')
            children[parent].append(name)
    structure=bytearray()
    def word(value):structure.extend(struct.pack('>I',value))
    def padded(value):structure.extend(value);structure.extend(b'\0'*(-len(structure)%4))
    def emit(path):
        word(1);padded(('' if path=='/' else path.rsplit('/',1)[1]).encode()+b'\0')
        for name,value in nodes[path].items():
            if name not in names:names[name]=len(strings);strings.extend(name.encode()+b'\0')
            word(3);word(len(value));word(names[name]);padded(value)
        for child in children[path]:emit(child)
        word(2)
    emit('/');word(9)
    off_reserve=40;off_struct=off_reserve+len(reservations);off_strings=off_struct+len(structure)
    total=off_strings+len(strings)
    result=struct.pack('>10I',h[0],total,off_struct,off_strings,off_reserve,17,h[6],h[7],len(strings),len(structure))+reservations+structure+strings
    if parse_dtb(result)!=nodes:raise ValueError('FDT rebuild changed properties')
    return bytes(result)


LINUX_FACTORY_HIGH_BANK=(0xa80000000,0x80000000)
LINUX_FACTORY_SECOND_HIGH_BANK=(0xb00000000,0x80000000)
QNX_FACTORY_HIGH_BANK=(0x880000000,0x4e000000)
ANDROID_FACTORY_HIGH_BANKS=((0xa00000000,0x80000000),(0xb80000000,0x80000000))


def native_low_or_high_ram(address,length,high_bank=None,second_bank=None):
    banks=((0x80000000,0x80000000),)+tuple(b for b in (high_bank,second_bank) if b)
    return length>0 and any(base<=address and address+length<=base+size for base,size in banks)


def linux_factory_high_bank(raw):

    return factory_high_bank(raw,3,LINUX_FACTORY_HIGH_BANK)


def qnx_factory_high_bank(raw):

    return factory_high_bank(raw,2,QNX_FACTORY_HIGH_BANK)


def factory_high_bank(raw,vmid,bank):
    start=raw.index(b'VDTHYP\0\0\xd0\x0d\xfe\xed')+8
    nodes=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    base,size=bank
    owners=[]
    for path,props in nodes.items():
        if not (path.endswith('/vm-memory') or path in ('/vlm/memory/invalid-region','/vlm/hyp/hyp-memory')):
            continue
        for address,length in struct.iter_unpack('>QQ',props['reg']):
            if base<address+length and address<base+size:
                owners.append((path,address,length))
    if owners!=[(f'/vlm/vm@{vmid}/vm-memory',base,size)]:
        raise ValueError(f'Expected the whole factory VM{vmid} high RAM bank without overlapping guests')
    return base,size


def hardware_hcb_copy(raw,extra_ram_bytes=0,linux_factory_bank=False,qnx_factory_bank=False,android_factory_banks=False,*,linux_second_bank=False):





    original_size=struct.unpack_from('>I',raw,4)[0]
    nodes=parse_dtb(raw[:original_size])
    if any(props.get('device_type')==b'memory\0' for props in nodes.values()):
        raise ValueError('Preserve pre-existing hardware RAM description')
    banks=[{'base':hex(0x80000000),'bytes':0x80000000}]
    regs=struct.pack('>QI',0x80000000,0x80000000)
    if extra_ram_bytes:
        if extra_ram_bytes not in (0x10000000,0x20000000,0x40000000,0x80000000):
            raise ValueError('Unsupported local loader RAM size')
                                                                             
                                                                             
        regs+=struct.pack('>QI',0xc00000000,extra_ram_bytes)
        banks.append({'base':hex(0xc00000000),'bytes':extra_ram_bytes})
    if linux_factory_bank:
        base,length=linux_factory_high_bank(raw)
        regs+=struct.pack('>QI',base,length)
        banks.append({'base':hex(base),'bytes':length,'owner':'factory VM3'})
    if linux_second_bank:
        if not linux_factory_bank:
            raise ValueError('The second VM3 bank requires the first bank')
        base,length=factory_high_bank(raw,3,LINUX_FACTORY_SECOND_HIGH_BANK)
        regs+=struct.pack('>QI',base,length)
        banks.append({'base':hex(base),'bytes':length,'owner':'factory VM3'})
    if qnx_factory_bank:
        base,length=qnx_factory_high_bank(raw)
        regs+=struct.pack('>QI',base,length)
        banks.append({'base':hex(base),'bytes':length,'owner':'factory VM2'})
    if android_factory_banks:
        for bank in ANDROID_FACTORY_HIGH_BANKS:
            base,length=factory_high_bank(raw,4,bank)
            regs+=struct.pack('>QI',base,length)
            banks.append({'base':hex(base),'bytes':length,'owner':'factory VM4'})
    nodes['/memory@80000000']={'device_type':b'memory\0','reg':regs}
                                                                               
                                                                               
                                                                         
    hyp_wdt=b'samsung,exynos9-v1-wdt\0'
    cl0=nodes['/watchdog_cl0@10050000']
    oem_wdt=cl0.get('compatible',b'')
    if b'samsung,exynosauto9-v1-wdt\0' not in oem_wdt:
        raise ValueError('Factory cluster0 watchdog compatible is missing')
    if hyp_wdt not in oem_wdt:
        cl0['compatible']=hyp_wdt+oem_wdt
    root=rebuild_fdt(raw[:original_size],nodes)
    tail=raw.find(b'VDTHYP\0\0',original_size)
    if tail<0 or tail%4096:raise ValueError('Original CBI continuation is not page aligned')
    result=root+b'\0'*(-len(root)%4096)+raw[tail:]
    return result,{'change':'Bootloader physical RAM node only','banks':banks,
                   'original_hcb_sha256':hashlib.sha256(raw).hexdigest(),'derived_hcb_sha256':hashlib.sha256(result).hexdigest(),
                   'original_hwp_size':original_size,'derived_hwp_size':len(root),'original_cbi_tail':tail,
                   'hyp_watchdog_compatible':'samsung,exynos9-v1-wdt prepended on watchdog_cl0@10050000',
                   'signature_verified':False,'scope':'Derived local virtual-board configuration; not a signed factory HCB'}


def factory_file(relative):

    candidates=[vm.ROOT/'var/inputs/analysis/cpu'/relative,vm.load_config()['original']/relative]
    path=next((path for path in candidates if path.is_file()),candidates[0])
    rows=(vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines()
    item=next(json.loads(row) for row in rows if json.loads(row)['name'].removeprefix('./')==relative)
    if path.stat().st_size!=item['size'] or vm.sha(path)!=item['sha256']:
        raise ValueError('Factory input identity mismatch: '+relative)
    return path


def replace_cbi_record(raw,tag,transform):

    if len(tag)!=8 or raw.count(tag+b'\xd0\x0d\xfe\xed')!=1:
        raise ValueError('Require one CBI record')
    start=raw.index(tag+b'\xd0\x0d\xfe\xed')+8
    size=struct.unpack_from('>I',raw,start+4)[0]
    data=raw[start:start+size]
    nodes=parse_dtb(data)
    transform(nodes)
    rebuilt=rebuild_fdt(data,nodes)
    end=(start+size+7)&~7
    return raw[:start]+rebuilt+b'\0'*(-len(rebuilt)%8)+raw[end:]


def payload_sized_hcb(raw,payload_bytes):







    size=(payload_bytes+4095)&~4095
    change={}
    def edit(nodes):
        bank=nodes['/vlm/vm@2/vm-memory']['reg']
        base,bank_size=struct.unpack_from('>QQ',bank)
        props=nodes['/vlm/vm@2/vm-memory/kernel-region']
        offset,previous=struct.unpack('>QQ',props['reg'])
        if (base,bank_size,offset,previous)!=(0x90000000,0x10000000,0x1080000,0x13000000):
            raise ValueError('Unexpected VM2 factory reservation')
        if not 0<payload_bytes<=size<=bank_size-offset:
            raise ValueError('QNX payload does not fit VM2 first bank')
        props['reg']=struct.pack('>QQ',offset,size)
        change.update(node='/vlm/vm@2/vm-memory/kernel-region',original_bytes=previous,
                      derived_bytes=size,payload_bytes=payload_bytes,physical_base=hex(base+offset),
                      reason='Explicit local payload-sized reservation; factory loader policy unverified')
    result=replace_cbi_record(raw,b'VDTHYP\0\0',edit)
    return result,change


def loaded_kernels_hcb(raw,images):
    changes=[]
    def edit(nodes):
        for item in images:
            vmid=item['vmid'];prefix=f'/vlm/vm@{vmid}/vm-memory'
            bank,bank_size=struct.unpack_from('>QQ',nodes[prefix]['reg'])
            for kind,size in (('kernel',item['kernel_bytes']),('initrd',item.get('ramdisk_bytes',0))):
                props=nodes[prefix+'/'+kind+'-region']
                offset,old=struct.unpack('>QQ',props['reg'])
                if offset+size>bank_size:raise ValueError('Native guest payload exceeds first RAM bank')
                props['reg']=struct.pack('>QQ',offset,size)
                changes.append({'vmid':vmid,'kind':kind,'physical':hex(bank+offset),'before_bytes':old,'after_bytes':size})
    result=replace_cbi_record(raw,b'VDTHYP\0\0',edit)
    return result,changes


def original(name):
    path = BOOT/name if '/' in name else ORIGINAL/name
    inventory = [json.loads(line) for line in (vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines()]
    entry = next(row for row in inventory if row['name'].removeprefix('./') == (name if '/' in name else 'hyp-binary/'+name))
    if path.stat().st_size != entry['size'] or vm.sha(path) != entry['sha256']:
        raise ValueError('Original hypervisor identity mismatch: '+name)
    return path


def native_vgen_layout():

    raw=original('hcb/hcb.img').read_bytes()
    nodes=parse_dtb(raw[:struct.unpack_from('>I',raw,4)[0]])
    kinds={b'samsung,vgen\0':0,b'samsung,vgen-lite\0':1,b'samsung,vgen-ufs\0':2,
           b'samsung,vgen-dpu\0':3,b'samsung,vgen-g2d\0':4}
    result=[]
    for path,props in nodes.items():
        compatible=props.get('compatible')
        if compatible not in kinds or props.get('status')==b'disabled\0':continue
        base,size=struct.unpack('>QQ',props['reg'])
        kind=kinds[compatible]
        if size!=(0x9000 if kind==3 else 0x1000):raise ValueError('Unknown VGEN aperture')
        result.append({'path':path,'base':base,'size':size,'layout':kind,
                       'factory_vid':list(struct.unpack('>'+'I'*(len(props['vid'])//4),props['vid']))})
    if len(result)!=20:raise ValueError('Unexpected factory VGEN inventory')
    return result


def native_ufs_inventory():

    source=original('hcb/hcb.img');raw=source.read_bytes()
    start=raw.index(b'VDTVM2\0\0')+8
    trees={'hardware':parse_dtb(raw[:struct.unpack_from('>I',raw,4)[0]]),
           'qnx_virtual':parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])}
    rows=[]
    for tree,nodes in trees.items():
        for path,props in nodes.items():
            if 'ufs' not in path.lower() and 's2mpu_fsys1' not in path.lower():continue
            rows.append({'tree':tree,'path':path,'properties':{k:{'hex':v.hex(),
                'u32':list(struct.unpack('>'+'I'*(len(v)//4),v)) if len(v)%4==0 else None}
                for k,v in props.items()}})
    return {'source':str(source),'sha256':vm.sha(source),'nodes':rows,
            'scope':'Original property inventory; no inferred DMA/IRQ/partition route'}


def native_cmu_layout():

    raw=original('hcb/hcb.img').read_bytes();start=raw.index(b'VDTVM2\0\0')+8
    nodes=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    props=nodes['/vplatform/vl,cmu@mem'];data=props['reg']
    if len(data)!=39*12:raise ValueError('Unexpected original CMU port count')
    ports=[{'base':base,'size':size} for base,size in struct.iter_unpack('>QI',data)]
    if len({r['base'] for r in ports})!=39 or any(r['size']!=0x10000 or r['base']%0x10000 for r in ports):
        raise ValueError('Unexpected original CMU register aperture')
    if not {0x1bf00000,0x1b240000,0x10400000} <= {r['base'] for r in ports}:
        raise ValueError('Missing observed original CMU ports')
    return ports


def native_s2mpu_layout():
    raw=original('hcb/hcb.img').read_bytes()
    nodes=parse_dtb(raw[:struct.unpack_from('>I',raw,4)[0]])
    result=[]
    for path,props in nodes.items():
        if props.get('compatible')!=b'samsung,s2mpu,node\0' or props.get('status')==b'disabled\0':continue
        base,size=struct.unpack('>QQ',props['reg'])
        if size!=0x5000:raise ValueError('Unknown S2MPU aperture')
        irqs=list(struct.unpack('>'+'I'*(len(props['interrupts'])//4),props['interrupts']))
        parent=props.get('interrupt-parent')
        if len(irqs)==1 and parent==struct.pack('>I',0x16):
            controller='exynos-dintc2';irq=irqs[0]
        elif len(irqs)==3 and irqs[0]==0:
            controller='gic';irq=irqs[1]
        else:raise ValueError('Unresolved native S2MPU interrupt route')
        result.append({'path':path,'base':base,'size':size,'controller':controller,'irq':irq})
    if not result:raise ValueError('No native S2MPUs')
    return result


def native_gpio_layout():
    raw=original('hcb/hcb.img').read_bytes()
    nodes=parse_dtb(raw[:struct.unpack_from('>I',raw,4)[0]])
    result=[]
    for path,props in nodes.items():
        if props.get('compatible')!=b'samsung,exynosauto9-pinctrl\0' or 'vl,pins-per-banks' not in props:continue
        base,size=struct.unpack('>QI',props['reg'])
        widths=list(struct.unpack('>'+'I'*(len(props['vl,pins-per-banks'])//4),props['vl,pins-per-banks']))
        irqtype,irq,trigger=struct.unpack('>III',props['interrupts'])
        if size!=0x1000 or irqtype!=0 or not 1<=len(widths)<=8 or any(not 1<=n<=8 for n in widths):raise ValueError('Unknown native GPIO layout')
        result.append({'path':path,'base':base,'size':size,'pins':widths,'encoded_pins':sum(n<<(i*4) for i,n in enumerate(widths)),'irq':irq})
    if len(result)!=5:raise ValueError('Unexpected native normal GPIO controller count')
    return result


def native_alive_gpio_layout():
    raw=original('hcb/hcb.img').read_bytes()
    start=raw.index(b'VDTVM2\0\0')+8
    nodes=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    widths=[struct.unpack('>I',nodes['/vpinctrl/'+name]['#count'])[0] for name in ('gpa0','gpa1','gpq0')]
    base,size=struct.unpack('>QI',nodes['/pinctrl@10450000']['reg'])
    irqs=[struct.unpack('>III',nodes[f'/vplatform/vl,alive_eint{pin}@irq']['interrupts'])[1] for pin in range(10)]
    if widths!=[8,2,2] or irqs!=list(range(13,23)) or size!=4096:
        raise ValueError('Unknown native ALIVE GPIO layout')
    return {'base':base,'size':size,'pins':widths,'encoded_pins':sum(n<<(i*4) for i,n in enumerate(widths)),
            'pin_irqs':irqs,'source':'Original VDTVM2 pin-bank and interrupt-mapper records'}


def native_aud_gpio_layout():
    raw=original('hcb/hcb.img').read_bytes();start=raw.index(b'VDTVM2\0\0')+8
    nodes=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    widths=[struct.unpack('>I',nodes['/vpinctrl/gpb'+str(n)]['#count'])[0] for n in range(4)]
    base,size=struct.unpack('>QI',nodes['/pinctrl@19C60000']['reg'])
    if widths!=[5,8,8,8] or size!=4096:raise ValueError('Unknown native AUD GPIO layout')
    return {'base':base,'size':size,'pins':widths,'encoded_pins':sum(n<<(i*4) for i,n in enumerate(widths)),
            'irq_route_verified':False,'source':'Original VDTVM2 gpb0..3 pin-bank records'}


def native_primecells():
    path=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    nodes=parse_dtb(path.read_bytes());result=[]
    for name,props in nodes.items():
        if b'arm,primecell\0' not in props.get('compatible',b''):continue
        result.append({'path':name,'properties':{key:render(value) for key,value in props.items()}})
    return result


def extract_ifs_members(members,output):
    inventory=json.loads((vm.ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_text(encoding='utf-8'))
    source=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
    if vm.sha(source)!=inventory['sha256']:raise ValueError('IFS inventory identity mismatch')
    records=[]
    with source.open('rb') as stream:
        for member in members:
            if '..' in Path(member).parts or ':' in member or '\\' in member or member.startswith('/'):
                raise ValueError('Unsafe IFS member path')
            rows=[r for r in inventory['entries'] if r['name']==member and r['kind']=='reg']
            if len(rows)!=1:raise ValueError('Require exactly one regular IFS member: '+member)
            row=rows[0];stream.seek(row['absolute_offset']);raw=stream.read(row['file_size'])
            if len(raw)!=row['file_size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ValueError('IFS member identity mismatch')
            target=output/'members'/member;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            records.append({'name':member,'path':str(target),'source':str(source),'bytes':len(raw),'sha256':vm.sha(target)})
    return records


def mclass_vector_candidates(raw):






    found=[]
    for offset in range(0,len(raw)-63,4):
        words=struct.unpack_from('<16I',raw,offset)
        if not 0<words[0]<0x40000000 or words[0]&7 or not words[1]&1:continue
        handlers=[words[i] for i in (1,2,3,4,5,6,11,12,14,15) if words[i]]
        if (len(handlers)<4 or len(set(handlers))<2 or any(not n&1 for n in handlers)
                or max(handlers)-min(handlers)>=0x100000):continue
        found.append({'file_offset':hex(offset),'words':[hex(n) for n in words],
            'reserved_vectors_all_zero':not any(words[i] for i in (7,8,9,10,13)),
            'scope':'Clustered Cortex-M-shaped vectors only; identity, runtime and load map unverified'})
        if len(found)>256:raise ValueError('Too many ambiguous vector candidates; narrow the input')
    return found


def coprocessor_inputs(output):
    result=[]
    inputs=[(factory_file(name),True) for name in ('boot-binary/S5AHR80.evt1.sbl1.bin','boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin','boot-binary/t-base.img.raw')]
    inputs.append((original('boot-binary/ldfw.bin'),False))
    inputs.append((original('lk-boot/lk.bin'),True))
    for path,arm64_analysis in inputs:
        raw=path.read_bytes()
        strings=[{'file_offset':hex(m.start()),'text':m.group().decode('ascii')} for m in re.finditer(rb'[\x20-\x7e]{5,180}',raw)
            if re.search(rb'acpm|apm|sram|firmware|cortex|crypto|plugin|eld',m.group(),re.I)]
        vectors=mclass_vector_candidates(raw)
        analysis=None
        if arm64_analysis:
            analysis=output/(path.name+'-analysis-only.elf');analysis.write_bytes(analysis_elf(raw))
        result.append({'path':str(path),'sha256':vm.sha(path),'bytes':len(raw),'header_128_hex':raw[:128].hex(),
            'selected_strings':strings,'vector_candidates':vectors,'analysis_elf':str(analysis) if analysis else None,
            'embedded_elf_offsets':[hex(m.start()) for m in re.finditer(b'\x7fELF',raw)],
            'analysis_mapping':'Original file offset equals analysis VA; byte-preserving container, not boot image' if arm64_analysis else 'Container architecture/encoding unknown; no fabricated executable header'})
    return result


def arm32_analysis_elf(raw,entry,base=0):

    if not 0<=base<=entry<base+len(raw) or base%4096:raise ValueError('Analysis entry/base outside candidate')
    ident=b'\x7fELF\x01\x01\x01'+b'\0'*9
    header=ident+struct.pack('<HHIIIIIHHHHHH',2,40,1,entry,52,0,0x5000000,52,32,1,40,0,0)
    program=struct.pack('<8I',1,4096,base,base,len(raw),len(raw),7,4096)
    return (header+program).ljust(4096,b'\0')+raw


def mcu_candidate(output):

    path=factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin');source=path.read_bytes()
    offset=0x54c00;raw=source[offset:];vectors=struct.unpack_from('<16I',raw)
    if vectors[:4]!=(0x10000,0x81,0x1429,0xb5):raise ValueError('BL2 vector candidate changed')
    binary=output/'bl2-mcu-candidate.bin';binary.write_bytes(raw)
    elf=output/'bl2-mcu-analysis-only.elf';elf.write_bytes(arm32_analysis_elf(raw,vectors[1]))
    strings=[{'file_offset':hex(m.start()),'text':m.group().decode('ascii')}
        for m in re.finditer(rb'[\x20-\x7e]{5,180}',raw)]
    constants=[]
    for number in (0x02039000,0x02040000,0x10430000,0x10470000,0x10500000,0x10540000,
                   0xa0500000,0xa0540000,0xa0460000,0x00400000):
        constants.append({'value':hex(number),'offsets':[hex(m.start()) for m in re.finditer(re.escape(struct.pack('<I',number)),raw)]})
    return {'source':str(path),'source_sha256':vm.sha(path),'source_offset':hex(offset),'bytes':len(raw),
        'candidate':str(binary),'candidate_sha256':vm.sha(binary),'vectors':[hex(n) for n in vectors],
        'analysis_elf':str(elf),'analysis_sha256':vm.sha(elf),'analysis_mapping':'Candidate offset equals analysis VA0, Thumb entry0x81; physical load map unverified',
        'strings':strings,'selected_constants':constants,'acpm_identity_verified':False,'firmware_executed':False}


def elf_window(path,address,length):
    original=path.read_bytes()
    if len(original)<52 or address<0 or length<=0:raise ValueError('Invalid ELF window input')
    machine=struct.unpack_from('<H',original,18)[0]
    if original[:6]==b'\x7fELF\x01\x01' and machine==40:
        header=struct.unpack_from('<HHIIIIIHHHHHH',original,16)
        if (header[8]<32 or not 0<header[9]<=1024 or header[4]<52
                or header[4]+header[8]*header[9]>len(original)):
            raise ValueError('Invalid ARM32 program table')
        raw=None
        for index in range(header[9]):
            segment=struct.unpack_from('<8I',original,header[4]+index*header[8])
            kind,offset,virtual,physical,size,memory,flags,align=segment
            delta=address-virtual
            if kind==1 and 0<=delta<=size-length and offset+delta+length<=len(original):
                raw=original[offset+delta:offset+delta+length];break
        if raw is None:raise ValueError('ARM32 address is not file-backed')
        decoder=Cs(CS_ARCH_ARM,(CS_MODE_THUMB|CS_MODE_MCLASS) if header[3]&1 else CS_MODE_ARM)
    else:
        raw=Elf(original).at(address,length);decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
    return {'path':str(path.resolve()),'sha256':vm.sha(path),'address':hex(address),
        'bytes_hex':raw.hex(),'u32':[hex(n[0]) for n in struct.iter_unpack('<I',raw[:len(raw)//4*4])],
        'instructions':[{'address':hex(i.address),'mnemonic':i.mnemonic,'operands':i.op_str} for i in decoder.disasm(raw,address)]}


def arm64_materialized_refs(path,target):







    elf=Elf(path.read_bytes());decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
    decoder.detail=True;decoder.skipdata=True;matches=[]
    for segment in elf.segments:
        if segment['type']!=1 or not segment['flags']&1:continue
        known={}
        for i in decoder.disasm(elf.read(segment['offset'],segment['file_size']),segment['virtual']):
            if i.id==0:known.clear();continue
            known={r:v for r,v in known.items() if i.address-v[1]<=32}
            candidate=None;destination=None
            ops=i.operands
            if i.mnemonic in ('adr','adrp'):
                destination=ops[0].reg;candidate=ops[1].imm
            elif i.mnemonic=='add' and len(ops)==3 and ops[2].type==2 and ops[1].reg in known:
                destination=ops[0].reg
                candidate=known[ops[1].reg][0]+(ops[2].imm<<ops[2].shift.value)
            _,writes=i.regs_access()
            for reg in writes:known.pop(reg,None)
            if candidate is not None:
                known[destination]=(candidate,i.address)
                if candidate==target:matches.append({'address':hex(i.address),'mnemonic':i.mnemonic,'operands':i.op_str})
            if i.mnemonic.startswith(('b','cb','tb')) or i.mnemonic in ('ret','eret'):known.clear()
    return {'path':str(path.resolve()),'sha256':vm.sha(path),'target':hex(target),'candidates':matches,
        'scope':'Static linear ADR/ADRP+ADD candidates, not exhaustive references or observed execution'}


def lk_linux_boot_dtb(raw,slot):






    if slot not in ('a','b'):raise ValueError('Require explicit original Linux slot a/b')
    nodes=parse_dtb(raw);chosen=nodes.get('/chosen',{})
    data=chosen.get('bootargs',b'')
    if not data.endswith(b'\0') or b'\0' in data[:-1]:raise ValueError('Require one terminated bootargs string')
    before=data[:-1].decode('ascii')
    roots=list(re.finditer(r'(?<!\S)root=\S+',before))
    verity=list(re.finditer(r'(?<!\S)dm-mod\.create="[^"]*"',before))
    if len(roots)!=1 or len(verity)!=1:raise ValueError('Require one root and quoted factory dm-verity table')
    table=verity[0].group()
    if len(re.findall(r'(?<!\S)/dev/sda14(?!\S)',table))!=1 or len(re.findall(r'(?<!\S)/dev/sda27(?!\S)',table))!=1:
        raise ValueError('Factory A-slot verity device contract changed')
    if slot=='b':table=re.sub(r'(?<!\S)/dev/sda14(?!\S)','/dev/sda31',table);table=re.sub(r'(?<!\S)/dev/sda27(?!\S)','/dev/sda55',table)
    after=before[:verity[0].start()]+table+before[verity[0].end():]
    after=re.sub(r'(?<!\S)root=\S+','root=/dev/dm-0',after)
    slot_pattern=r'(?<!\S)bootloader\.boot\.om=\S+'
    if len(re.findall(slot_pattern,after))>1:raise ValueError('Duplicate slot argument')
    if re.search(slot_pattern,after):after=re.sub(slot_pattern,'bootloader.boot.om='+slot,after)
    else:after=after.rstrip()+' bootloader.boot.om='+slot+' '
    chosen['bootargs']=after.encode('ascii')+b'\0'
    result=rebuild_fdt(raw,nodes)
    return result,{'slot':slot,'original_sha256':hashlib.sha256(raw).hexdigest(),
        'derived_sha256':hashlib.sha256(result).hexdigest(),'bootargs_before':before,'bootargs_after':after,
        'changed_properties':['/chosen/bootargs'],'dm_verity_preserved':True,
        'contract':'Partial original LK root/slot mutation only, not full bootloader execution',
        'lk_analysis_file_offsets':['0x39740','0x36cf8','0x3e1ac']}



def lk_android_normal_dtb(raw,lk,vendor_boot,avb):

                                                                                
    records=((0x6414c,'root','/dev/ram0'),(0x64a54,'androidboot.slot_suffix','_a'),
             (0x64ed8,'androidboot.force_normal_boot','1'))
    args={}
    for offset,key,value in records:
        record=lk[offset-4:offset+0x480]
        if (record[:4]!=b'\x01\x00\x00\x00' or
            record[4:0x84].split(b'\0',1)[0]!=key.encode('ascii') or
            record[0x84:].split(b'\0',1)[0]!=value.encode('ascii')):
            raise ValueError('Original LK Android A-slot record changed: '+key)
        args[key]=value
    if vendor_boot[:12]!=b'VNDRBOOT\x03\0\0\0' or len(vendor_boot)<2112:
        raise ValueError('Require original vendor_boot v3 command line')
    cmdline,terminator,_=vendor_boot[28:2076].partition(b'\0')
    if not terminator:
        raise ValueError('Unterminated original vendor_boot command line')
    vendor_args=cmdline.decode('ascii').split()
    if vendor_args!=['androidboot.boot_devices=17e20000.ufs','androidboot.hardware=exynosauto9',
                     'firmware_class.path=/vendor/firmware','buildvariant=user']:
        raise ValueError('Original 8704 vendor_boot command line changed')
    avb_args=['androidboot.vbmeta.'+k+'='+str(avb[k]) for k in ('hash_alg','size','digest')]
    nodes=parse_dtb(raw);chosen=nodes['/chosen'];value=chosen.get('bootargs',b'')
    if not value.endswith(b'\0') or b'\0' in value[:-1]:
        raise ValueError('Require one terminated factory Android bootargs string')
    before=value[:-1].decode('ascii')
    tokens=before.split()
    if (tokens.count('root=/dev/sda1')!=1 or
        any(t.partition('=')[0] in set(args).union(x.partition('=')[0] for x in vendor_args+avb_args)
            and t!='root=/dev/sda1' for t in tokens) or 'skip_initramfs' in tokens):
        raise ValueError('Android factory DT has conflicting boot parameters')
    after=re.sub(r'(?<!\\S)root=/dev/sda1(?!\\S)','root='+args.pop('root'),before)
    after+=' '+' '.join([*(k+'='+v for k,v in args.items()),*vendor_args,*avb_args])
    chosen['bootargs']=after.encode('ascii')+b'\0'
    result=rebuild_fdt(raw,nodes)
    return result,{'slot':'a','original_dtb_sha256':hashlib.sha256(raw).hexdigest(),
        'derived_dtb_sha256':hashlib.sha256(result).hexdigest(),'lk_sha256':hashlib.sha256(lk).hexdigest(),
        'vendor_boot_sha256':hashlib.sha256(vendor_boot).hexdigest(),'lk_table_offsets':[hex(r[0]) for r in records],
        'bootargs_before':before,'bootargs_after':after,'changed_properties':['/chosen/bootargs'],
        'scope':'Partial original LK normal-boot handoff; not a full LK/secure-boot replay',
        'avb_measurement':avb,'avb_or_verity_disabled':False,'verified_boot_state_injected':False}


def walk_guest_4k(far,tcr,ttbr,read_u64):

    if ((tcr>>16)&63)!=25 or ((tcr>>30)&3)!=2 or not far>>63:
        raise ValueError('Unsupported stage-1 address/granule configuration')
    table=ttbr&0xfffffffff000;walk=[]
    for shift in (30,21,12):
        address=table+((far>>shift)&511)*8;entry=read_u64(address)
        walk.append({'pte_address':hex(address),'entry':hex(entry),'shift':shift})
        if not entry&1:raise ValueError('Invalid guest descriptor')
        if shift==12 or not entry&2:
            physical=(entry&0xfffffffff000&~((1<<shift)-1))|(far&((1<<shift)-1))
            return {'physical_target':hex(physical),'walk':walk}
        table=entry&0xfffffffff000
    raise ValueError('No terminal guest descriptor')


def walk_user_4k(far,tcr,root,read_u64):

    if tcr&63!=25 or (tcr>>14)&3 or not 0x1000<=far<1<<39 or not root or root&0xfff:
        raise ValueError('Unsupported original 39-bit userspace table')
    table=root
    for shift in (30,21,12):
        entry=read_u64(table+((far>>shift)&511)*8)
        if not entry&1 or (shift==12 and not entry&2):
            raise ValueError('Unmapped original compositor page')
        if shift==12 or not entry&2:
            if not entry&(1<<6):
                raise ValueError('Compositor page is not accessible to original EL0')
            return (entry&0xfffffffff000&~((1<<shift)-1))|(far&((1<<shift)-1))
        table=entry&0xfffffffff000
    raise ValueError('Missing original compositor page descriptor')


def kernel_symbols(session,output):
    from vmlinux_to_elf.core.kallsyms import KallsymsFinder
    class ArtifactSymbols(KallsymsFinder):
        def extract_db_information(self,extra_info=True):
                                                                              
                                                                                
                                                                            
            return None
    results=[]
    for row in json.loads((session/'kernel-loads.json').read_text(encoding='utf-8')):
        path=Path(row['kernel']);raw=path.read_bytes()
        finder=ArtifactSymbols(raw,bit_size=64)
        num=finder.kallsyms_num_syms__offset
        table=num-8-((len(finder.symbols)*4+7)&~7)
                                                                             
                                                                                
                                                                             
        raw_count=struct.unpack_from('<I',raw,num)[0]
        offsets=struct.unpack_from('<'+str(raw_count)+'I',raw,table) if raw_count==len(finder.symbols) else ()
        vm.save(output/(row['guest']+'-symbol-layout.json'),{'num_file_offset':hex(num),'names_file_offset':hex(finder.kallsyms_names__offset),
            'inferred_offsets_file_offset':hex(table),'raw_count':raw_count,'finder_count':len(finder.symbols),
            'relative_base_bytes':raw[num-8:num].hex(),'first_offsets':[hex(n) for n in offsets[:8]],
            'last_offsets':[hex(n) for n in offsets[-8:]]})
        if not (offsets and offsets[0]==0 and list(offsets)==sorted(offsets) and offsets[-1]<len(raw)+16*1024*1024):
            raise ValueError('Native kallsyms offset/count layout failed validation')
        for symbol,offset in zip(finder.symbols,offsets):symbol.virtual_address=0xffffffc010080000+offset
        parser_text=finder.name_to_symbol['_text'].virtual_address
        runtime_text=0xffffffc010080000
        result={'engine':'vmlinux-to-elf1.2.2 KallsymsFinder','count':len(finder.symbols),
            'original_file_unchanged':vm.sha(path)==hashlib.sha256(raw).hexdigest(),
            'analysis_memory_relocations':finder.kernel_img!=raw,
            'parser_text':hex(parser_text),'runtime_text':hex(runtime_text),
            'mapping':'Offsets relative to recovered _text; VA anchored to observed running kernel _text',
            'symbols':[{'name':symbol.name,'type':symbol.symbol_type.value,'address':hex(symbol.virtual_address-parser_text+runtime_text)} for symbol in finder.symbols]}
        result.update(guest=row['guest'],source=str(path),sha256=vm.sha(path))
        snapshot=json.loads((session/'cpu-system-registers.json').read_text(encoding='utf-8'))
        native_cpu=next(c for c in snapshot['cpus'] if int(c['system'].get('VTTBR_EL2','0'),16)>>48==row['vmid'] and int(c['system'].get('TCR_EL1','0'),16))
        result['observed_tcr_el1']=native_cpu['system']['TCR_EL1']
        result['observed_context_source']=str(session/'cpu-system-registers.json')
        analysis=output/(row['guest']+'-analysis-only.elf')
        analysis.write_bytes(symbolized_analysis_elf(raw,result['symbols']))
        result['analysis_elf']=str(analysis);result['analysis_sha256']=vm.sha(analysis)
        vm.save(output/(row['guest']+'-symbols.json'),result)
        results.append({key:value for key,value in result.items() if key!='symbols'})
    return results


def symbolized_analysis_elf(raw,symbols):

    base=next(int(s['address'],16) for s in symbols if s['name']=='_text')
    names=bytearray(b'\0');table=bytearray(b'\0'*24)
    for symbol in symbols:
        address=int(symbol['address'],16)
        if not base<=address<base+len(raw):continue
        offset=len(names);names+=symbol['name'].encode('utf-8')+b'\0'
        kind=2 if symbol['type'] in ('T','W') else 1
        table+=struct.pack('<IBBHQQ',offset,16|kind,0,1,address,0)
    shnames=b'\0.image\0.symtab\0.strtab\0.shstrtab\0'
    result=bytearray(4096);result+=raw;result+=b'\0'*(-len(result)%8)
    symoff=len(result);result+=table;stroff=len(result);result+=names
    shstroff=len(result);result+=shnames;result+=b'\0'*(-len(result)%8)
    shoff=len(result)
    sections=[(0,0,0,0,0,0,0,0,0,0),(1,1,7,base,4096,len(raw),0,0,4096,0),
              (8,2,0,0,symoff,len(table),3,1,8,24),(16,3,0,0,stroff,len(names),0,0,1,0),
              (24,3,0,0,shstroff,len(shnames),0,0,1,0)]
    for section in sections:result+=struct.pack('<IIQQQQIIQQ',*section)
    header=b'\x7fELF\x02\x01\x01'+b'\0'*9+struct.pack('<HHIQQQIHHHHHH',2,183,1,base,64,shoff,0,64,56,1,64,5,4)
    segment=struct.pack('<IIQQQQQQ',1,7,4096,base,base,len(raw),len(raw),4096)
    result[:len(header+segment)]=header+segment
    if Elf(result).at(base,len(raw))!=raw:raise ValueError('Analysis ELF changed original image')
    return bytes(result)


def analysis_elf(raw):






    identity=b'\x7fELF\x02\x01\x01'+b'\0'*9
    header=identity+struct.pack('<HHIQQQIHHHHHH',2,183,1,0,64,0,0,64,56,1,64,0,0)
    segment=struct.pack('<IIQQQQQQ',1,7,4096,0,0,len(raw),len(raw),4096)
    result=(header+segment).ljust(4096,b'\0')+raw
    parsed=Elf(result)
    if parsed.at(0,len(raw))!=raw:raise ValueError('Analysis container changed source bytes')
    return result


def acpm_candidate(output):

    source=factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin');full=source.read_bytes()
    start,end=0x17400,0x54c00;raw=full[start:end]
    if struct.unpack_from('<4I',raw)!=(0xb000,0x3259,0x3279,0x327f):raise ValueError('Second BL2 vector identity changed')
    binary=output/'bl2-second-mcu-candidate.bin';binary.write_bytes(raw)
    elf=output/'bl2-second-mcu-analysis-only.elf';elf.write_bytes(arm32_analysis_elf(raw,0x3259,0x3000))
    return {'source':str(source),'source_sha256':vm.sha(source),'source_offset':hex(start),'end_bound':hex(end),
        'bytes':len(raw),'candidate':str(binary),'candidate_sha256':vm.sha(binary),
        'analysis_elf':str(elf),'analysis_sha256':vm.sha(elf),'analysis_private_base':'0x3000',
        'base_evidence':'Reset/NMI/HardFault vectors match code at file offsets 0x258/0x278/0x27e',
        'acpm_identity_verified':False,'firmware_executed':False,
        'scope':'Second M-class candidate to next known image boundary; not a verified complete image or physical load plan',
        'strings':[{'file_offset':hex(m.start()),'text':m.group().decode('ascii')}
            for m in re.finditer(rb'[\x20-\x7e]{5,180}',raw) if re.search(rb'acpm|pmic|dvfs|fvmap|version',m.group(),re.I)]}


def prepare_lk_analysis(output):
    path=original('lk-boot/lk.bin')
    raw=path.read_bytes();derived=output/'lk-analysis-only.elf'
    derived.write_bytes(analysis_elf(raw))
    result={'original':str(path),'original_sha256':vm.sha(path),'original_bytes':len(raw),
            'analysis_container':str(derived),'analysis_sha256':vm.sha(derived),
            'mapping':'Original file offset equals analysis virtual address; PT_LOAD file offset4096',
            'physical_link_address_verified':False,'byte_preserving_roundtrip':True,'not_a_boot_image':True}
    vm.save(output/'transformation.json',result)
    return result


class Elf:
    def __init__(self, raw):
        if raw[:6] != b'\x7fELF\x02\x01' or len(raw) < 64:
            raise ValueError('Require little-endian ELF64')
        self.raw = raw
        h = struct.unpack_from('<HHIQQQIHHHHHH', raw, 16)
        if h[1] != 183 or h[8] < 56 or h[10] < 64:
            raise ValueError('Require bounded ARM64 ELF tables')
        self.entry = h[3]
        self.segments = []
        for index in range(h[9]):
            row = struct.unpack('<IIQQQQQQ', self.read(h[4]+index*h[8], 56))
            self.read(row[2], row[5])
            self.segments.append(dict(zip(('type','flags','offset','virtual','physical','file_size','memory_size','alignment'),row)))
        self.sections = []
        for index in range(h[11]):
            row = struct.unpack('<IIQQQQIIQQ', self.read(h[5]+index*h[10], 64))
            self.sections.append(dict(zip(('name','type','flags','address','offset','size','link','info','alignment','entry_size'),row)))
        self.symbols = []
        for section in self.sections:
            if section['type'] not in (2,11):
                continue
            strings = self.sections[section['link']]
            table = self.read(strings['offset'], strings['size'])
            stride = section['entry_size']
            if stride < 24 or section['size'] % stride:
                raise ValueError('Invalid ELF symbol stride')
            for index in range(section['size']//stride):
                name, info, other, shndx, address, size = struct.unpack('<IBBHQQ',self.read(section['offset']+index*stride,24))
                if name >= len(table):
                    raise ValueError('Invalid symbol name')
                end = table.find(b'\0',name)
                if end < 0:
                    raise ValueError('Unterminated symbol')
                text = table[name:end].decode('utf-8',errors='replace')
                if text and shndx:
                    self.symbols.append({'name':text,'address':address,'size':size,'type':info&15})

    def read(self, offset, length):
        if not 0 <= offset <= len(self.raw)-length or length < 0:
            raise ValueError('ELF range outside file')
        return self.raw[offset:offset+length]

    def at(self, address, length):
        for segment in self.segments:
            delta = address-segment['virtual']
            if segment['type']==1 and 0 <= delta <= segment['file_size']-length:
                return self.read(segment['offset']+delta,length)
        raise ValueError('Address is not file-backed')


def inventory(output):
    images = []
    for name in ('hv.img','hypervisor','hypervisor-debug','hcb/hcb.img','lk-boot/lk.bin','boot-binary/ldfw.bin'):
        path = original(name)
        raw = path.read_bytes()
        item = {'source':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'first64_hex':raw[:64].hex(),'last64_hex':raw[-64:].hex(),'fdts':[]}
        for match in re.finditer(b'\xd0\x0d\xfe\xed',raw):
            position = match.start()
            try:
                size = struct.unpack_from('>I',raw,position+4)[0]
                if not 40 <= size <= len(raw)-position:
                    continue
                data = raw[position:position+size]
                nodes = parse_dtb(data)
            except (ValueError,AssertionError,struct.error,UnicodeError):
                continue
            stem = name.replace('.','-').replace('/','-')+'-'+hex(position)
            binary = output/(stem+'.dtb')
            binary.write_bytes(data)
            properties = {node:{key:{'hex':value.hex(),'derived':render(value)} for key,value in props.items()}
                          for node,props in nodes.items()}
            vm.save(output/(stem+'.json'),properties)
            item['fdts'].append({'offset':position,'framing_before_hex':raw[max(0,position-16):position].hex(),'framing_after_hex':raw[position+size:position+size+32].hex(),'size':size,'nodes':len(nodes),'path':str(binary),'sha256':hashlib.sha256(data).hexdigest(),
                                'selected_paths':[node for node in nodes if re.search('vm|vlm|memory|uart|gic|display|dpu|gpu',node,re.I)]})
        if raw[:4] == b'\x7fELF':
            elf = Elf(raw)
            vm.save(output/(name+'-symbols.json'),elf.symbols)
            vm.save(output/(name+'-segments.json'),elf.segments)
            decoder = Cs(CS_ARCH_ARM64,CS_MODE_ARM)
            item['entry'] = hex(elf.entry)
            item['entry_instructions'] = [{'address':hex(ins.address),'bytes':ins.bytes.hex(),'mnemonic':ins.mnemonic,'operands':ins.op_str}
                                           for ins in decoder.disasm(elf.at(elf.entry,0x200),elf.entry)]
            item['selected_symbols']=[row for row in elf.symbols if re.search('boot_start|bsp_dtb|bsp_config$|nk_hyp_calls|hvc|uart|console|nk_version|bsp_start|sadk9_default',row['name'],re.I)]
            symbols={row['name']:row for row in elf.symbols}
            item['dintc_bank_tables']=[{'address':hex(row['address']),'words':list(struct.unpack('<2I',elf.at(row['address'],8)))}
                                       for row in elf.symbols if row['name']=='_dintc_bank_base' and row['size']==8]
            handlers={row['address']:row['name'] for row in elf.symbols if row['type']==2}
            if 'nk_hyp_calls' in symbols:
                table=symbols['nk_hyp_calls']
                pointers=struct.unpack('<'+str(table['size']//8)+'Q',elf.at(table['address'],table['size']))
                vm.save(output/(name+'-hvc-table.json'),[{'selector':number,'address':hex(address),'handler':handlers.get(address)} for number,address in enumerate(pointers)])
            if 'hyp_limit' in symbols:item['hyp_limit']=hex(struct.unpack('<Q',elf.at(symbols['hyp_limit']['address'],8))[0])
        images.append(item)
        if name=='boot-binary/ldfw.bin':
            item['header_512_hex']=raw[:512].hex()
            item['firmware_strings']=[{'file_offset':hex(m.start()),'text':m.group().decode('ascii')}
                for m in re.finditer(rb'[\x20-\x7e]{5,200}',raw) if re.search(rb'acpm|apm|sram|firmware|cortex',m.group(),re.I)]
            vectors=[]
            for offset in range(0,len(raw)-64,4):
                sp,entry=struct.unpack_from('<II',raw,offset)
                if 0x02039000<=sp<=0x02071000 and entry&1 and 0x02039000<=entry<0x02071000:
                    vectors.append({'file_offset':hex(offset),'stack':hex(sp),'entry':hex(entry),'vector_words':list(struct.unpack_from('<16I',raw,offset))})
            item['candidate_acpm_vectors']=vectors
        if name=='lk-boot/lk.bin':
            item['bootloader_strings']=[{'file_offset':hex(match.start()),'text':match.group().decode('ascii')}
                for match in re.finditer(rb'[\x20-\x7e]{5,1024}',raw)
                if re.search(rb'hcb|kernel-region|vm-memory|VDTHYP|QNX|qnx|vplatform|bootparams',match.group(),re.I)]
            decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM);decoder.skipdata=True
            item['rtc_address_literal_candidates']=[{'file_offset':hex(ins.address),'mnemonic':ins.mnemonic,'operands':ins.op_str}
                for ins in decoder.disasm(raw[:0x60000],0) if '0x1054' in ins.op_str]
    guests=[]
    for kind,relative in (('linux','ivi/icas3cn-gp_b0_zero.dtb'),('android','android/vendor_boot.dtb'),('qnx','qnx/qtd/icas3gp-qla-vm.dtb')):
        path=vm.ROOT/'var/inputs/analysis/cpu'/relative
        raw=path.read_bytes()
        nodes=parse_dtb(raw)
        selected={name:{key:{'hex':value.hex(),'derived':render(value)} for key,value in props.items()}
                  for name,props in nodes.items() if name in ('/','/chosen') or re.search('^/memory|vlx|vlm|hypervisor',name)}
        guests.append({'guest':kind,'source':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'nodes':selected})
    vm.save(output/'guest-platforms.json',guests)
    qnx=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn').read_bytes()
    decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
    vm.save(output/'qnx-preboot.json',{'sha256':hashlib.sha256(qnx).hexdigest(),
        'instruction_addresses_are_file_offsets':True,
        'ranges':{hex(offset):[{'offset':hex(ins.address),'mnemonic':ins.mnemonic,'operands':ins.op_str}
            for ins in decoder.disasm(qnx[offset:offset+64],offset)] for offset in (0,0x800,0x1000,0x1800)},
        'header_magic_offsets':[hex(m.start()) for m in re.finditer(b'\xeb\x7e\xff\x00',qnx[:65536])]})
    vm.save(output/'inventory.json',images)
    return images


def inspect_session(session, output):
    data=(session/'hypervisor-ram.bin').read_bytes()
    launch=json.loads((session/'launch.json').read_text(encoding='utf-8'))
    base=launch.get('ram_capture_base',0x80080000)
    source=original('hv.img').read_bytes()
    elf=Elf(original('hypervisor').read_bytes())
    ram_regions=[(base,data)]
    pram=session/'exynos-pram.bin'
    if pram.exists():
        symbols={row['name']:row for row in elf.symbols}
        pointer=struct.unpack('<Q',elf.at(symbols['bsp_config']['address'],8))[0]
        physical=struct.unpack('<Q',elf.at(pointer+136,8))[0]
        ram_regions.append((physical,pram.read_bytes()))
    cpu=json.loads((session/'cpu.json').read_text(encoding='utf-8'))['output']
    pc=int(re.search(r'PC=([0-9a-fA-F]+)',cpu)[1],16)
    functions=[row for row in elf.symbols if row['type']==2 and row['address']<=pc<row['address']+row['size']]
    strings=[]
    for match in re.finditer(rb'[\x09\x0a\x0d\x20-\x7e]{24,4096}',data):
        pos=match.start();raw=match.group()
        source_pos=base+pos-0x80080000
        if source_pos>=0 and source[source_pos:source_pos+len(raw)]==raw:
            continue
        strings.append({'physical':hex(base+pos),'bytes':len(raw),'text':raw.decode('ascii')})
    def read(address,count):
        for physical,region in ram_regions:
            offset=address-physical
            if 0<=offset<=len(region)-count:return region[offset:offset+count]
        raise ValueError('Outside captured RAM: '+hex(address)+'+'+hex(count))
    symbols={row['name']:row for row in elf.symbols}
    bsp=struct.unpack('<Q',read(symbols['bsp_config']['address'],8))[0]
    fields={'configuration_address':hex(bsp),'persistent_base':hex(struct.unpack('<Q',read(bsp+136,8))[0]),
            'persistent_size':struct.unpack('<I',read(bsp+144,4))[0]}
    histories=[]
    for name in ('cons_hist_ram','cons_hist_uart'):
        try:
            opaque=struct.unpack('<Q',read(symbols['_cons_hist_'+('ram' if name.endswith('ram') else 'uart')+'_default_ops']['address']+48,8))[0]
            values=struct.unpack('<6Q',read(opaque,48))
            start,limit,current,total=values[1],values[2],values[3],values[5]
            if not start<=current<limit or not 0<limit-start<=2*1024*1024:raise ValueError('History pointers out of bounds')
            buffer=read(start,limit-start)
            value=buffer[:total] if total<=len(buffer) else buffer[current-start:]+buffer[:current-start]
            histories.append({'name':name,'opaque':hex(opaque),'start':hex(start),'limit':hex(limit),'current':hex(current),
                              'ordinal':total,'text':value.decode('utf-8',errors='replace')})
        except (ValueError,KeyError,struct.error) as error:
            histories.append({'name':name,'error':str(error)})
    machines=[]
    try:
        count=struct.unpack('<I',read(symbols['core__max_os']['address'],4))[0]
        if not 1<=count<=32:raise ValueError('Runtime VM limit outside known ABI')
        for index in range(count):
            address=struct.unpack('<Q',read(symbols['_vms']['address']+index*8,8))[0]
            if not address:continue
            u32=lambda offset:struct.unpack('<I',read(address+offset,4))[0]
            u64=lambda offset:struct.unpack('<Q',read(address+offset,8))[0]
            image=u64(88)
            row={'vmid':u32(0),'address':hex(address),'enabled':u32(84),'state_flags':u32(80),
                 'paused':u32(140),'stopped':u32(144),'load_in_progress':u32(180),'boot_count':u32(164),'image':hex(image)}
            if image:
                row['entry']=hex(struct.unpack('<Q',read(image+8,8))[0])
                row['image_wait_callback']=hex(struct.unpack('<Q',read(image+56,8))[0])
                kernel=struct.unpack('<Q',read(image+32,8))[0]
                if kernel:
                    physical,size,flags=struct.unpack('<QII',read(kernel+8,16))
                    row['kernel']={'physical':hex(physical),'bytes':size,'flags':flags}
            machines.append(row)
    except (ValueError,KeyError,struct.error) as error:machines.append({'error':str(error)})
    result={'session':str(session),'pc':hex(pc),'pc_symbols':functions,'bsp':fields,'console_histories':histories,'changed_printable_regions':strings,
            'runtime_vms':machines,
            'limits':'Changed printable RAM regions include formatting buffers and copied configuration; not every region is an emitted log record.'}
    vm.save(output/'memory-analysis.json',result)
    candidates=[]
    for filename,key in (('kernel-loads.json','kernel'),('guest-image-loads.json','path')):
        path=session/filename
        if path.exists():
            for row in json.loads(path.read_text(encoding='utf-8')):
                candidates.append({'guest':row['guest'],'path':row[key],'raw':Path(row[key]).read_bytes()})
    trace=(session/'tcg.log').read_text(encoding='utf-8',errors='replace')
    blocks=[]
    for match in re.finditer(r'IN:\s*\n(0x[0-9a-f]+):[^\n]*\n((?:OBJD-T: [0-9a-f]+\s*\n)+)',trace):
        code=bytes.fromhex(''.join(re.findall(r'OBJD-T: ([0-9a-f]+)',match[2])))
        blocks.append((int(match[1],16),code))
    pcs=[];decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
    for item in re.finditer(r'CPU#(\d+)[\s\S]*?PC=([0-9a-f]+)([\s\S]*?)(?=CPU#|\Z)',cpu):
        if 'EL1h' not in item[3]:continue
        address=int(item[2],16);matches=[]
        for start,code in blocks:
            if not start<=address<start+len(code):continue
            for candidate in candidates:
                raw=candidate['raw'];position=raw.find(code)
                if position>=0 and raw.find(code,position+1)<0:
                    offset=position+address-start;window=max(0,offset-32)
                    matches.append({'guest':candidate['guest'],'path':candidate['path'],'pc_file_offset':hex(offset),
                                    'unique_trace_signature_bytes':len(code),'instructions':[
                                        {'file_offset':hex(ins.address),'mnemonic':ins.mnemonic,'operands':ins.op_str}
                                        for ins in decoder.disasm(raw[window:offset+48],window)]})
        pcs.append({'cpu':int(item[1]),'pc':hex(address),'unique_original_matches':matches})
    vm.save(output/'guest-pc-analysis.json',pcs)
                                                                               
                                                                             
    system_path=session/'cpu-system-registers.json'
    faults=[]
    if system_path.exists():
        snapshots=json.loads(system_path.read_text(encoding='utf-8'))['cpus']
        for cpu in snapshots:
            regs=cpu['system'];far=int(regs.get('FAR_EL1','0'),16)
            if not far:continue
            ttbr=int(regs.get('TTBR1_EL1' if far>>63 else 'TTBR0_EL1','0'),16)
            bank=0xb8000000 if 0xb8000000<=ttbr<0xbc000000 else 0xa0000000
            capture=session/('linux-boot-ram.bin' if bank==0xb8000000 else 'android-boot-ram.bin')
            if not capture.exists():continue
            memory=capture.read_bytes();table=ttbr&0xffffffffff000;walk=[]
                                                                          
            tcr=int(regs.get('TCR_EL1','0'),16)
            row={'thread':cpu['thread'],'far':hex(far),'tcr':hex(tcr),'walk':walk}
            try:
                if ((tcr>>16)&63)!=25 or ((tcr>>30)&3)!=2:
                    raise ValueError('Unsupported stage-1 address/granule configuration')
                for shift in (30,21,12):
                    address=table+((far>>shift)&511)*8;offset=address-bank
                    if not 0<=offset<=len(memory)-8:raise ValueError('Table outside captured RAM')
                    entry=struct.unpack_from('<Q',memory,offset)[0]
                    walk.append({'pte_address':hex(address),'entry':hex(entry),'shift':shift})
                    if not entry&1:raise ValueError('Invalid guest descriptor')
                    if shift==12 or not entry&2:
                        row['physical_target']=hex((entry&0xfffffffff000&~((1<<shift)-1))|(far&((1<<shift)-1)));break
                    table=entry&0xfffffffff000
            except ValueError as error:row['unverified']=str(error)
            faults.append(row)
    vm.save(output/'guest-fault-translations.json',faults)
    sites=[]
    if system_path.exists():
        for cpu in snapshots:
            elr=int(cpu['system'].get('ELR_EL1','0'),16)
            for candidate in candidates:
                if candidate['guest'] not in ('linux','android'):continue
                                                                               
                                                                               
                ttbr=int(cpu['system'].get('TTBR1_EL1','0'),16)
                if candidate['guest']!=('linux' if 0xb8000000<=ttbr<0xbc000000 else 'android'):continue
                offset=elr-0xffffffc010080000
                if not 0<=offset<len(candidate['raw']):continue
                start=max(0,offset-48)
                sites.append({'thread':cpu['thread'],'guest':candidate['guest'],'elr':hex(elr),'file_offset':hex(offset),
                    'instructions':[{'file_offset':hex(ins.address),'mnemonic':ins.mnemonic,'operands':ins.op_str}
                        for ins in decoder.disasm(candidate['raw'][start:offset+160],start)]})
    vm.save(output/'guest-fault-sites.json',sites)
    return result


class RSP:

    def __init__(self, port):
        self.socket = socket.create_connection(('127.0.0.1',port),timeout=3)
        self.socket.settimeout(3)

    def read_exact(self, count):
        result=b''
        while len(result)<count:
            data=self.socket.recv(count-len(result))
            if not data:
                raise OSError('GDB remote disconnected')
            result+=data
        return result

    def receive(self):
        for _ in range(256):
            if self.read_exact(1)==b'$':break
        else:raise ValueError('GDB packet start not found')
        raw=bytearray()
        while len(raw)<1024*1024:
            char=self.read_exact(1)
            if char==b'#':
                break
            raw+=char
        else:
            raise ValueError('GDB packet too long')
        checksum=int(self.read_exact(2),16)
        if sum(raw)%256!=checksum:
            self.socket.sendall(b'-')
            raise ValueError('GDB checksum mismatch')
        self.socket.sendall(b'+')
        result=bytearray();index=0
        while index<len(raw):
            value=raw[index];index+=1
            if value==ord('}'):
                if index==len(raw):raise ValueError('Truncated GDB escape')
                value=raw[index]^32;index+=1
            elif value==ord('*'):
                if not result or index==len(raw):raise ValueError('Invalid GDB run length')
                if raw[index]<29:raise ValueError('Invalid GDB repeat count')
                result.extend([result[-1]]*(raw[index]-29));index+=1;continue
            result.append(value)
            if len(result)>1024*1024:raise ValueError('Expanded GDB packet too long')
        return bytes(result)

    def command(self, text):
        raw=text.encode('ascii')
        self.socket.sendall(b'$'+raw+b'#'+f'{sum(raw)%256:02x}'.encode())
        return self.receive()

    def set_x0(self, value):
        response=self.command('P0='+struct.pack('<Q',value).hex())
        if response!=b'OK' or int(self.registers()['x0'],16)!=value:
            raise ValueError('Bootloader register write/readback failed: '+repr(response))

    def write_boot_memory(self, address, raw):

        if not raw or len(raw)>0x38000:raise ValueError('Boot memory write bound exceeded')
        for offset in range(0,len(raw),1024):
            chunk=raw[offset:offset+1024];current=address+offset
            if self.command(f'M{current:x},{len(chunk):x}:'+chunk.hex())!=b'OK':
                raise ValueError('Boot memory write failed at '+hex(current))
            if self.command(f'm{current:x},{len(chunk):x}').decode()!=chunk.hex():
                raise ValueError('Boot memory readback mismatch at '+hex(current))

    def attach_inferiors(self,process_ids):
        self.command('qSupported:multiprocess+;qXfer:features:read+')
        replies=[]
        for pid in process_ids:
            if not 1<=pid<=256:raise ValueError('Invalid local QEMU inferior ID')
            reply=self.command('vAttach;'+format(pid,'x')).decode()
            replies.append({'pid':pid,'reply':reply})
                                                                              
                                                                             
        return replies

    def select_application_cpu(self,expected_pc=0x80080330):
        self.command('qSupported:multiprocess+;qXfer:features:read+')
        reply=self.command('qfThreadInfo').decode();threads=[]
        while reply.startswith('m') and len(threads)<64:
            threads+=reply[1:].split(',');reply=self.command('qsThreadInfo').decode()
        observations=[]
                                                                            
                                                                              
        for thread in threads:
            if self.command('Hg'+thread)!=b'OK':raise ValueError('GDB boot CPU selection failed')
            registers,_=self.xml_registers()
            if 'X0' not in registers:
                observations.append({'thread':thread,'arm64':False});continue
            core=self.registers();observations.append({'thread':thread,'arm64':True,'pc':core['pc']})
            if int(core['pc'],16)==expected_pc:return thread
        raise ValueError('No ARM64 boot entry in actual GDB threads: '+json.dumps(observations))

    def registers(self):
        raw=bytes.fromhex(self.command('g').decode())
        if len(raw)<33*8+4:
            raise ValueError('Missing ARM64 core registers')
        return {**{f'x{index}':hex(struct.unpack_from('<Q',raw,index*8)[0]) for index in range(31)},
                'sp':hex(struct.unpack_from('<Q',raw,31*8)[0]),'pc':hex(struct.unpack_from('<Q',raw,32*8)[0]),
                'cpsr':hex(struct.unpack_from('<I',raw,33*8)[0])}

    def xml_registers(self):
        self.command('qSupported:multiprocess+;qXfer:features:read+')
        documents={};registers={};number=0
        def visit(name,depth=0):
            nonlocal number
            if depth>8 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',name) or name in documents:
                raise ValueError('Invalid/cyclic GDB register XML include')
            data=bytearray()
            while len(data)<256*1024:
                reply=self.command(f'qXfer:features:read:{name}:{len(data):x},fff')
                if reply[:1] not in (b'm',b'l'):raise ValueError('GDB register XML unavailable')
                data+=reply[1:]
                if reply[:1]==b'l':break
            else:raise ValueError('GDB register XML too large')
            documents[name]=data.decode('utf-8')
            xml=data.decode('utf-8')
            if 'xi:' in xml and 'xmlns:xi=' not in xml:
                                                                            
                                                                             
                xml=re.sub(r'<(target|feature)(?=[\s>])',r'<\1 xmlns:xi="http://www.w3.org/2001/XInclude"',xml,count=1)
            for node in ET.fromstring(xml).iter():
                if node.tag.endswith('include'):
                    visit(node.attrib['href'],depth+1)
                elif node.tag=='reg':
                    number=int(node.attrib.get('regnum',number))
                    registers[node.attrib['name'].upper()]={'number':number,'bits':int(node.attrib['bitsize'])}
                    number+=1
        visit('target.xml')
        return registers,documents

    def system_snapshot(self):
        registers,documents=self.xml_registers()
        reply=self.command('qfThreadInfo').decode();threads=[]
        while reply.startswith('m') and len(threads)<64:
            threads+=reply[1:].split(',');reply=self.command('qsThreadInfo').decode()
        desired=('ELR_EL1','ESR_EL1','FAR_EL1','SPSR_EL1','VBAR_EL1','SCTLR_EL1','TCR_EL1','SP_EL0',
                 'TTBR0_EL1','TTBR1_EL1','VTTBR_EL2','HCR_EL2','CNTFRQ_EL0','CNTPCT_EL0',
                 'CNTVCT_EL0','MPIDR_EL1','TPIDR_EL2')
        cpus=[];inventories={}
        for thread in threads:
            if self.command('Hg'+thread)!=b'OK':raise ValueError('GDB CPU selection failed')
                                                                               
                                                                             
            inferior=thread.split('.')[0] if thread.startswith('p') else 'single'
            if inferior not in inventories:inventories[inferior]=self.xml_registers()
            current,current_docs=inventories[inferior]
            if 'R0' in current and 'X0' not in current:
                core={}
                for name in ('R0','R1','R2','R3','R4','R5','R6','R7','R8','R9','R10','R11','R12','SP','LR','PC','XPSR'):
                    reg=current.get(name)
                    if reg:
                        raw=self.command('p'+format(reg['number'],'x')).decode()
                        core[name.lower()]=hex(int.from_bytes(bytes.fromhex(raw),'little')) if re.fullmatch('[0-9a-fA-F]+',raw) and len(raw)*4==reg['bits'] else {'unavailable':raw}
                cpus.append({'thread':thread,'architecture':'arm-m','core':core,'system':{},'register_inventory':current,'xml':current_docs})
                                                                          
                                                                               
                for name in ('PRIMASK','BASEPRI','FAULTMASK','CONTROL'):
                    reg=current.get(name)
                    if reg:
                        raw=self.command('p'+format(reg['number'],'x')).decode()
                        cpus[-1]['system'][name]=(hex(int.from_bytes(bytes.fromhex(raw),'little'))
                            if re.fullmatch('[0-9a-fA-F]+',raw) and len(raw)*4==reg['bits'] else {'unavailable':raw})
                cpus[-1]['nvic_readonly']={}
                for name,address in (('ISER',0xe000e100),('ISPR',0xe000e200),('IABR',0xe000e300)):
                    raw=self.command(f'm{address:x},8').decode()
                    cpus[-1]['nvic_readonly'][name]=([hex(x) for x in struct.unpack('<2I',bytes.fromhex(raw))]
                        if re.fullmatch('[0-9a-fA-F]{16}',raw) else {'unavailable':raw})
                fault_raw=self.command('me000ed28,18').decode()
                if re.fullmatch('[0-9a-fA-F]{48}',fault_raw):
                    cpus[-1]['m_profile_fault_registers']={name:hex(value) for name,value in zip(
                        ('CFSR','HFSR','DFSR','MMFAR','BFAR','AFSR'),struct.unpack('<6I',bytes.fromhex(fault_raw)))}
                else:cpus[-1]['m_profile_fault_registers']={'unavailable':fault_raw}
                continue
            values={}
            for name in desired:
                reg=current.get(name)
                if reg:
                    raw=self.command('p'+format(reg['number'],'x')).decode()
                    if re.fullmatch('[0-9a-fA-F]+',raw) and len(raw)*4==reg['bits']:
                        values[name]=hex(int.from_bytes(bytes.fromhex(raw),'little'))
                    else:values[name]={'unavailable':raw}
            cpus.append({'thread':thread,'core':self.registers(),'system':values})
        return {'cpus':cpus,'register_inventory':registers,'xml':documents}

    def close(self):
        self.socket.close()


def cmu_queue_operands(words,valid_mask):

    rows=[]
    for index,value in enumerate(words):
        row={'index':index,'written':bool(valid_mask & (1<<index)),'raw':hex(value)}
        if value==0:row['kind']='zero-word'
        elif value>>24 in (0x10,0x12):
            row.update(kind='register-operand-candidate',encoding_class=hex(value>>24),
                       offset=hex(value&0xffff),data_byte=(value>>16)&255,
                       execution_semantics_verified=False)
        elif (value & 0xff00ffff)==0x80000000:
            row.update(kind='special-word',number=(value>>16)&255,execution_semantics_verified=False)
        else:row.update(kind='unresolved-word',execution_semantics_verified=False)
        rows.append(row)
    return rows


def probe(output, seconds, steps, map_pram=False, hcb=False, exynos_uart=False, board_adapter=False, qemu=None, cpus=1,hcb_ram=False,payload_sized_qnx=False,guest_dtbs=False,loader_ram_mib=0,load_qnx=False,load_linux=False,load_android=False,trace_mode='blocks',rtc_bootstrap=False,symbol_directory=None,translate_address=(),acpm_sram=None,lk_linux_slot=None,acpm_clock_hz=0,pmic_interface_reset_byte=None,pmic_bootstrap_profile=None,bl2_fvmap_profile=None,native_ufs=False,pscdc_profile=None,pscdc_latency_cycles=64,pmic_pmu_input_byte=None,pmu_profile=None,icount_shift=None,tmu_profile=None,tmu_temperature_c=25,pmu_link_profile=None,pmu_signal_profile=None,pmu_status1204=0,pmu_core_status_mask=0,apm_wdt_profile=None,apm_wdt_clock_hz=0,pmu_domain_profile=None,pmu_domain_latency_ns=1000,pmu_domain_supply_mask=0,pmu_dptx_debug_profile=None,pmu_dptx_debug_word0=None,pmu_dptx_debug_word1=None,pmu_dptx_debug_word2=None,serializer_profile=None,serializer_powerup_ns=100000,serializer_probe_ns=100000,ufs_descriptor_profile=None,lk_handoff_profile=None,usi_spi_profile=None,usi_i2c_profile=None,clock_rpc_watch=False,clock_backend_watch=False,lk_pwm_profile=None,ect_handoff_profile=None,cal_init_config_profile=None,ufs_shareability_profile=None,ufs_phy_isolation_profile=None,eqos_mac=None,sysmmu_profile=None,dpp_profile=None,decon_window_profile=None,linux_panic_watch=False,pcie2_elbi_profile=None,pcie2_phy_profile=None,ufs_uic_trace=False,mali_g3d1_id=None,ufs_prelink_profile=None,ufs_vgen_profile=None,ufs_lun0_image=None,ufs_power_profile=None,ufs_prdt_profile=None,pcie2_pcs_profile=None,pcie2_dbi_profile=None,pcie2_atu_profile=None,mali_g3d1_discovery=None,mali_g3d1_reset_irq=None,mali_g3d1_config=None,mali_g3d1_empty_power=None,linux_usi_uart=None,g2d_reset=None,ufs_mailbox=None,linux_sysmmu=None,ufs_boot_luns=None,ufs_vh=None,linux_dpp=None,linux_decon=None,scaler_reset=None,abox_gic=None,abox_sysmmu=None,android_initcall_trace=False,linux_initcall_trace=False,android_abox_watch=False,abox_axiuser=None,abox_dsp_off=None,lk_dsp_load=None,abox_dsp_xtensa=None,abox_dsp_mailbox=False,lk_android_normal=False,linux_pid1_context=False,android_ufs_overlay=False,android_task_context=False,android_ufs_parent=None,qnx_audio_clock_watch=False,qnx_dp0_reset=False,qnx_dp0_phy=False,qnx_dp0_link_config=False,qnx_dp0_irq=False,qnx_dpp=False,linux_dwc3=False,linux_usb_phy=False,qnx_dsi=False,linux_mfc=False,qnx_g3d00=False,linux_native_console_port=None,linux_direct_console=False,linux_smack_observe=False,linux_hmi_overlay=None,linux_ivi_data=None,linux_ivi_parent=None,mali_g3d1_jm_topology=None,linux_factory_bank=False,qnx_g3d00_jm_topology=False,pmu_om_input=None,qnx_dp0_aux_sink=False,qnx_factory_bank=False,qnx_bts=False,host_file_ram=False,qnx_display_profile=None,qnx_phy88q2112=False,qnx_g3d00_control_jobs=False,native_performance_cores=False,native_parallel_tcg=False,qnx_ufs_parent=None,native_tee_observe=False,native_hmi_signal_observe=False):
    from native_live_policy import profile_seconds, virtual_acpm_rx_policy, native_lifetime_policy, native_observation_active, service_native_trace
    virtual_rx=virtual_acpm_rx_policy(acpm_clock_hz=acpm_clock_hz, native_three_guests=bool(board_adapter and hcb and load_qnx and load_linux and load_android))
    from offline_clock import policy as instruction_clock_policy
    instruction_policy=instruction_clock_policy(native=bool(board_adapter and hcb and load_qnx and load_linux and load_android and linux_factory_bank and qnx_factory_bank),parallel=native_parallel_tcg,shift=icount_shift)
    if instruction_policy:vm.save(output/'offline-clock-policy.json',instruction_policy)
    stock_profile_seconds=profile_seconds(seconds, extended='--extended-observation' in sys.argv, stock=board_adapter and hcb and load_qnx and load_linux and load_android and (native_parallel_tcg or bool(instruction_policy)) and linux_factory_bank and qnx_factory_bank, prepare=os.environ.get('OEM_8704_SEED_NO_DISPLAY')=='1')
    lifetime=native_lifetime_policy(stock=bool(board_adapter and hcb and load_qnx and load_linux and load_android and (native_parallel_tcg or instruction_policy) and linux_factory_bank and qnx_factory_bank), prepare=os.environ.get('OEM_8704_SEED_NO_DISPLAY')=='1')
    from pscdc_platform import machine_options,profile_policy
    pscdc_options=machine_options(pscdc_profile,pscdc_latency_cycles,acpm_clock_hz)
    import pmu_platform
    pmu_options=pmu_platform.machine_options(pmu_profile,acpm_clock_hz)
    if pmu_om_input is not None:
        from qnx_pmu_platform import om_input_policy
        om_input=om_input_policy(pmu_om_input, bool(load_qnx and hcb and board_adapter and pmu_profile))
    else:
        om_input=None
    link_contract=pmu_platform.link_policy(pmu_link_profile,pmu_profile,acpm_clock_hz)
    signal_contract=pmu_platform.signal_policy(pmu_signal_profile,pmu_profile,acpm_clock_hz,pmu_status1204,pmu_core_status_mask)
    import pmu_domain
    domain_contract=pmu_domain.policy(pmu_domain_profile,pmu_profile,acpm_clock_hz,pmu_domain_latency_ns,pmu_domain_supply_mask)
    import pmu_debug
    debug_contract=pmu_debug.policy(pmu_dptx_debug_profile,pmu_profile,acpm_clock_hz,pmu_dptx_debug_word0,pmu_dptx_debug_word1,pmu_dptx_debug_word2)
    import serializer_platform
    serializer_contract=serializer_platform.policy(serializer_profile,board_adapter,serializer_powerup_ns,serializer_probe_ns)
    import ti983_platform
    ti983_requested=bool(serializer_contract and hcb and guest_dtbs and load_linux and load_qnx and
                         linux_factory_bank and qnx_factory_bank)
    import apm_watchdog
    wdt_contract=apm_watchdog.policy(apm_wdt_profile,apm_wdt_clock_hz,acpm_clock_hz)
    if lk_handoff_profile:
        if lk_handoff_profile!='virtual-original-lk-ssu-v1' or not (native_ufs and board_adapter and hcb_ram and acpm_clock_hz and pmu_profile=='virtual-cold-v1' and ufs_descriptor_profile=='virtual-samsung-byte-v1' and steps==0):
            raise ValueError('Original LK stage requires explicit native byte UFS, cold PMU, management CPU, native3GiB RAM and zero prior steps')
    import eqos_platform
    eqos=eqos_platform.policy(eqos_mac,board_adapter and load_qnx,qnx_phy88q2112)
    import sysmmu_platform
    vm3_sysmmu=sysmmu_platform.linux_policy(linux_sysmmu,board_adapter and load_linux and guest_dtbs and hcb)
    sysmmu=sysmmu_platform.policy(sysmmu_profile,board_adapter and load_android and guest_dtbs and hcb)
    import dpp_platform
    dpp=dpp_platform.policy(dpp_profile,sysmmu is not None)
    vm3_dpp=dpp_platform.linux_policy(linux_dpp,vm3_sysmmu)
    vm2_dpp=dpp_platform.qnx_policy(board_adapter and load_qnx and hcb and guest_dtbs) if qnx_dpp else None
    if qnx_dsi:
        import qnx_dsi_platform
        dsi_boot=qnx_dsi_platform.policy(board_adapter and load_qnx and hcb and guest_dtbs,
                                        dp_aux_sink=qnx_dp0_aux_sink)
    if linux_mfc:
        import linux_mfc_platform
        mfc=linux_mfc_platform.policy(board_adapter and load_linux and hcb and guest_dtbs)
    if linux_usb_phy and not linux_dwc3:
        raise ValueError('Linux USB PHY requires --linux-dwc3')
    if linux_dwc3:
        import linux_dwc3_platform
        usb3=linux_dwc3_platform.policy(board_adapter and load_linux and hcb and guest_dtbs)
        usb_phy=linux_dwc3_platform.phy_policy(usb3) if linux_usb_phy else None
    if dpp and dpp['factory_android_dtb_sha256']!=sysmmu['factory_android_dtb_sha256']:
        raise ValueError('DPP and SysMMU require the same original Android DT')
    import mali_g3d1_platform
    import abox_gic_platform
    abox=abox_gic_platform.policy(abox_gic,board_adapter and load_linux and load_android and guest_dtbs and hcb)
    abox_axi=abox_gic_platform.axi_policy(abox_axiuser,abox)
    import abox_dsp_platform
    abox_dsp=abox_dsp_platform.policy(abox_dsp_off,abox_axi,pmu_profile,acpm_clock_hz)
    import abox_sysmmu_platform
    abox_dma=abox_sysmmu_platform.policy(abox_sysmmu,vm3_sysmmu,abox)
    import android_init_trace
    if android_initcall_trace and not (board_adapter and load_android and guest_dtbs):
        raise ValueError('Android initcall trace needs original Android and factory DT')
    if linux_initcall_trace and not (board_adapter and load_linux and guest_dtbs):
        raise ValueError('Linux initcall trace needs original Linux and factory DT')
    if qnx_dp0_aux_sink and not qnx_dp0_irq:
        raise ValueError('DP0 AUX sink requires the plug IRQ path')
    if qnx_dp0_irq and not qnx_dp0_link_config:
        raise ValueError('DP0 plug registers require --qnx-dp0-link-config')
    if qnx_dp0_link_config and not qnx_dp0_phy:
        raise ValueError('DP0 LINK configuration requires --qnx-dp0-phy')
    if qnx_dp0_phy and not qnx_dp0_reset:
        raise ValueError('DP0 PHY requires --qnx-dp0-reset')
    if qnx_dp0_reset:
        import qnx_dp0_platform
        dp0=qnx_dp0_platform.policy(board_adapter and load_qnx and hcb and guest_dtbs,qnx_dp0_phy,qnx_dp0_link_config,qnx_dp0_irq,qnx_dp0_aux_sink)
    if qnx_audio_clock_watch and not (android_abox_watch and linux_initcall_trace and load_qnx and load_linux):
        raise ValueError('QNX audio clock bracket requires native QNX, Linux and ABOX watch')
    if android_abox_watch and not (android_initcall_trace and not clock_rpc_watch):
        raise ValueError('ABOX watch requires original Android initcall trace and exclusive clock observer')
    if qnx_g3d00_control_jobs and not (qnx_g3d00 and qnx_g3d00_jm_topology):
        raise ValueError('QNX control jobs require the original G3D00 JM configuration')
    if qnx_g3d00_jm_topology and not qnx_g3d00:
        raise ValueError('QNX Mali topology requires original G3D00')
    if qnx_g3d00:
        import qnx_g3d00_platform
        g3d00=qnx_g3d00_platform.policy(board_adapter and load_qnx and hcb and guest_dtbs,
                                        qnx_g3d00_jm_topology,qnx_g3d00_control_jobs)
    else:
        g3d00=None
    import scaler_reset_platform
    scaler=scaler_reset_platform.policy(scaler_reset,board_adapter and load_linux and guest_dtbs and hcb)
    import g2d_reset_platform
    g2d=g2d_reset_platform.policy(g2d_reset,board_adapter and load_android and guest_dtbs and hcb)
    import linux_uart_platform
    linux_uart=linux_uart_platform.policy(linux_usi_uart,board_adapter and load_linux and guest_dtbs and hcb)
    jm_topology=mali_g3d1_platform.jm_topology_policy(mali_g3d1_jm_topology,
        factory_file('ivi/Image') if mali_g3d1_jm_topology and load_linux else None,mali_g3d1_empty_power,
        vm3_native=bool(board_adapter and hcb and hcb_ram and guest_dtbs and load_linux and load_android
                        and load_qnx and linux_factory_bank and seconds<=stock_profile_seconds))
    g3d1=mali_g3d1_platform.policy(mali_g3d1_id,board_adapter and load_android and guest_dtbs and hcb,mali_g3d1_discovery,mali_g3d1_reset_irq,mali_g3d1_config,mali_g3d1_empty_power)
    if jm_topology and jm_topology['vm3_control']:
        if not g3d1:
            raise ValueError('VM3 G3D1 AS0 requires the verified original G3D1 aperture')
        g3d1.update(gpu_jobs=True,gpu_mmu=True,job_scope='VM3 control, exact viewport vertex program and bounded color clear; no general shaders or scanout')
    import pcie2_platform
    pcie2=pcie2_platform.policy(pcie2_elbi_profile,board_adapter and hcb and guest_dtbs and load_linux)
    pcie2_phy=pcie2_platform.phy_policy(pcie2_phy_profile,pcie2,acpm_clock_hz and pmu_profile=='virtual-cold-v1')
    pcie2_pcs=pcie2_platform.pcs_policy(pcie2_pcs_profile,pcie2,pcie2_phy)
    pcie2_dbi=pcie2_platform.dbi_policy(pcie2_dbi_profile,pcie2_pcs)
    pcie2_atu=pcie2_platform.atu_policy(pcie2_atu_profile,pcie2_dbi)
    if pcie2_atu and not lk_handoff_profile:
        raise ValueError('Native iATU aperture requires original LK to finish before switching decode')
    import decon_platform
    decon=decon_platform.policy(decon_window_profile,dpp)
    vm3_decon=decon_platform.linux_policy(linux_decon,vm3_dpp)
    if linux_panic_watch and not (board_adapter and load_linux and symbol_directory and not clock_rpc_watch):
        raise ValueError('Linux panic observer requires original Linux symbols and exclusive observer use')
    if ufs_uic_trace and not (native_ufs and load_qnx and lk_handoff_profile):
        raise ValueError('Runtime UIC trace requires native QNX UFS after original LK')
    import ufs_platform
    ufs_host0=ufs_platform.host0_policy(ufs_vgen_profile,native_ufs and load_qnx and hcb and board_adapter)
    import ufs_mailbox_platform
    mailbox_policy=ufs_mailbox_platform.policy(ufs_mailbox,ufs_host0,load_linux and load_android and guest_dtbs)
    import ufs_vh_platform
    vh_policy=ufs_vh_platform.policy(ufs_vh,mailbox_policy,ufs_lun0_image,android_ufs_overlay)
    if vh_policy and not ufs_descriptor_profile:
        raise ValueError('VH transport needs the Samsung byte-unit descriptor profile')
    if ufs_boot_luns is not None and ufs_lun0_image is None:
        raise ValueError('Derived read-only boot LUNs require the verified native LUN0 fixture')
    if ufs_lun0_image is not None and not (ufs_host0 and lk_handoff_profile and ufs_uic_trace):
        raise ValueError('Archive LUN0 requires original LK, UFS host0 route and runtime evidence')
    ufs_prelink=ufs_platform.prelink_policy(ufs_prelink_profile,native_ufs and load_qnx and lk_handoff_profile)
    ufs_power=ufs_platform.power_policy(ufs_power_profile,ufs_prelink)
    ufs_prdt=ufs_platform.plain128_policy(ufs_prdt_profile,ufs_host0 and ufs_power and ufs_descriptor_profile)
    ufs_descriptor_options=ufs_platform.descriptor_options(ufs_descriptor_profile,native_ufs)
    ufs_shareability=ufs_platform.shareability_policy(ufs_shareability_profile,native_ufs and board_adapter)
    ufs_phy_isolation=ufs_platform.phy_isolation_policy(ufs_phy_isolation_profile,
        bool(native_ufs and board_adapter and acpm_clock_hz and pmu_profile=='virtual-cold-v1'))
    import sysreg_platform
    usi_spi_options=sysreg_platform.profile_options(usi_spi_profile,board_adapter)
    mcu_spi6=(sysreg_platform.mcu_spi6_contract() if load_linux and hcb and
              usi_spi_profile=='virtual-linux-spi-v1' else None)
    import linux_i2c_platform
    usi_i2c_options=linux_i2c_platform.profile_options(usi_i2c_profile,board_adapter)
    if cal_init_config_profile is not None and (cal_init_config_profile!='virtual-qnx-cal-config-v1' or not (board_adapter and acpm_clock_hz and pmu_profile=='virtual-cold-v1')):raise ValueError('CAL init config needs explicit native board, management clock and cold PMU profile')
    if ect_handoff_profile is not None and (ect_handoff_profile!='original-bl2-ect-v1' or not (board_adapter and hcb and load_qnx)):raise ValueError('Original ECT handoff needs original QNX, HCB and native board')
    if lk_pwm_profile is not None and (lk_pwm_profile!='virtual-lk-pwm24-v1' or not board_adapter):raise ValueError('LK PWM needs explicit virtual board/profile')
    if clock_backend_watch and not clock_rpc_watch:raise ValueError('Backend clock observer needs --clock-rpc-watch')
    if clock_rpc_watch and not (usi_i2c_profile and load_linux and symbol_directory):raise ValueError('Clock observer needs original Linux, symbols and explicit Linux I2C profile')
    import tmu_platform
    tmu_options=tmu_platform.machine_options(tmu_profile,tmu_temperature_c,acpm_clock_hz)
    if icount_shift is not None and (not acpm_clock_hz or not isinstance(icount_shift,int) or not 0<=icount_shift<=10 or steps):
        raise ValueError('Instruction clock requires native management CPU, zero boot steps and shift0..10')
    if native_performance_cores and not (board_adapter and acpm_clock_hz and not steps and sys.platform=='win32'):
        raise ValueError('Host performance CPU Sets require a Windows native AP probe')
    accelerated_seed=accelerated_seed_window(seconds,
        prepare=getattr(probe,'oem_seed_preparation',False),
        accelerated=getattr(probe,'oem_accelerated_seed',False))
    if native_parallel_tcg and (native_performance_cores or not 0<seconds<=stock_profile_seconds and not (
            getattr(probe,'oem_seed_preparation',False) and (600<seconds<=1200 or accelerated_seed)
            and linux_factory_bank and linux_ivi_parent)):
        raise ValueError('Parallel TCG beyond the routine window requires bounded OEM seed preparation')
    accelerator,tcg_policy=tcg_execution_policy(native_parallel_tcg,board_adapter,acpm_clock_hz,cpus,steps,icount_shift,sys.platform)
    if pmic_pmu_input_byte is not None and (not acpm_clock_hz or
        pmic_bootstrap_profile != 'virtual-bl2-rev0' or not 0 <= pmic_pmu_input_byte <= 255):
        raise ValueError('PMIC PMU input requires native management CPU, explicit BL2 profile and 0..255 byte')
    cfg = vm.load_config()
    if native_ufs and not board_adapter:raise ValueError('Native UFS requires original board adapter')
    if bl2_fvmap_profile is not None and (bl2_fvmap_profile!='virtual-asv0' or not acpm_clock_hz or pmic_bootstrap_profile!='virtual-bl2-rev0' or steps):
        raise ValueError('BL2 FVMap profile requires native ACPM, explicit PMIC profile and zero boot steps')
    if pmic_bootstrap_profile is not None and (pmic_bootstrap_profile != 'virtual-bl2-rev0' or pmic_interface_reset_byte is None):
        raise ValueError('Virtual BL2 profile requires explicit communication byte and known profile')
    if pmic_interface_reset_byte is not None and (not acpm_clock_hz or not 0<=pmic_interface_reset_byte<=255):
        raise ValueError('Explicit virtual PMIC-interface byte requires native management CPU and 0..255')
    if acpm_clock_hz and (not board_adapter or acpm_sram or not 1000000<=acpm_clock_hz<=1000000000):
        raise ValueError('Native ACPM requires board adapter, 1MHz..1GHz explicit virtual clock and no runtime snapshot')
    if linux_hmi_overlay is not None and not (vh_policy and load_linux):
        raise ValueError('Linux /vw overlay requires original VM3 and native UFS VH')
    if linux_ivi_parent is not None and linux_ivi_data is None:
        raise ValueError('IVI cold boot requires --linux-ivi-data')
    if linux_ivi_data is not None and linux_hmi_overlay is None:
        raise ValueError('IVI userdata requires the verified Linux media')
    from offline_development import policy as offline_boot_policy
    offline_policy=offline_boot_policy(native=bool(board_adapter and hcb and load_qnx and load_linux and load_android),writable=android_ufs_overlay)
    if offline_policy:
        from offline_android_seed import select as select_private_android_seed
        android_ufs_parent=select_private_android_seed(offline_policy)
    if android_ufs_parent is not None and not android_ufs_overlay:
        raise ValueError('Prior Android data requires an independent writable VM4 overlay')
    if android_ufs_overlay and not (lk_android_normal and vh_policy):
        raise ValueError('Writable Android UFS requires original Android normal boot and native VH')
    if android_task_context and not (load_android and symbol_directory):
        raise ValueError('Android task capture requires the original kernel and symbols')
    if linux_pid1_context and not (load_linux and symbol_directory):
        raise ValueError('Linux PID 1 capture requires the original Linux Image and symbols')
    if lk_android_normal and not (load_android and guest_dtbs and native_ufs and ufs_vh and ufs_lun0_image):
        raise ValueError('LK Android normal boot requires OEM VM4, guest DTB and a virtual UFS LUN0')
    if lk_linux_slot and not (guest_dtbs and load_linux):raise ValueError('LK Linux slot requires original Linux and guest DTBs')
    if linux_native_console_port is not None and not (board_adapter and load_linux and lk_linux_slot
                                                     and 1024<=linux_native_console_port<=65535):
        raise ValueError('Native Linux console requires OEM board/LK/Linux and a local port 1024..65535')
    if linux_direct_console and not (linux_native_console_port and linux_uart and
                                     linux_factory_bank and linux_initcall_trace):
        raise ValueError('Dedicated Linux console requires native VM3 UART, bank, and boot policy')
    if linux_smack_observe and not (load_linux and symbol_directory and linux_pid1_context):
        raise ValueError('SMACK observation requires original Linux and --linux-pid1-context')
    if (hcb_ram or payload_sized_qnx or guest_dtbs) and not hcb:
        raise ValueError('RAM/configuration adaptation and guest DTBs require --hcb')
    if loader_ram_mib and not (hcb_ram and map_pram):raise ValueError('Loader RAM requires --hcb-ram --map-pram')
    if linux_factory_bank and not (board_adapter and load_linux and hcb_ram and map_pram):
        raise ValueError('Factory VM3 RAM backing requires the board adapter, loaded Linux, HCB RAM and mapped PRAM')
    high_bank=linux_factory_high_bank(original('hcb/hcb.img').read_bytes()) if linux_factory_bank else None
    second_policy=os.environ.get('OEM_8704_VM3_SECOND_BANK','')
    if second_policy and (second_policy!='original-b0-v1' or not (offline_policy and host_file_ram and high_bank)):
        raise ValueError('VM3 second bank requires the original offline three-domain file-RAM profile')
    second_bank=factory_high_bank(original('hcb/hcb.img').read_bytes(),3,LINUX_FACTORY_SECOND_HIGH_BANK) if second_policy else None
    gl_runtime=None
    if os.environ.get('OEM_8704_GL_FORWARD_BOOT',''):
        if not (second_bank and offline_policy and load_linux and guest_dtbs and linux_initcall_trace):
            raise ValueError('GL boot requires the original private VM3 bank and staged Linux boot')
        import importlib.util
        spec=importlib.util.spec_from_file_location('_oem8704_gl_runtime',vm.ROOT/'src/emu/gl-forward/runtime-darwin.py')
        gl_runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(gl_runtime)
        gl_runtime.selected()
    if second_bank and jm_topology and jm_topology['vm3_control']:
        jm_topology['dma_private_banks']+= (second_bank,)
    if offline_policy and not(host_file_ram and hcb_ram and map_pram and guest_dtbs):
        raise ValueError('Offline Android requires real file-backed original VM4 banks, HCB and guest DTs')
    android_high_banks=tuple(factory_high_bank(original('hcb/hcb.img').read_bytes(),4,b)for b in ANDROID_FACTORY_HIGH_BANKS)if offline_policy else()
    if native_hmi_signal_observe and not (linux_factory_bank and seconds<=stock_profile_seconds):
        raise ValueError(f'HMI signal observation requires the original Linux bank and at most {stock_profile_seconds} seconds')
    if native_tee_observe and not (qnx_factory_bank and seconds<=stock_profile_seconds):
        raise ValueError(f'TEE observation requires the original QNX bank and at most {stock_profile_seconds} seconds')
    if qnx_ufs_parent is not None and not (qnx_factory_bank and ufs_lun0_image and native_ufs and seconds<=stock_profile_seconds):
        raise ValueError(f'QNX first-boot reuse requires the original native PH disk and at most {stock_profile_seconds} seconds')
    if qnx_factory_bank and not (board_adapter and load_qnx and hcb_ram and map_pram):
        raise ValueError('QNX factory RAM requires the board adapter, VM2 payload and populated HCB')
    qnx_high_bank=qnx_factory_high_bank(original('hcb/hcb.img').read_bytes()) if qnx_factory_bank else None
    display_identity = None
    linux_display_identity = None
    if qnx_display_profile is not None:
        if not (board_adapter and load_qnx and guest_dtbs and hcb_ram and steps == 0):
            raise ValueError('Virtual QNX display configuration requires the original VM2 board')
        import qnx_display_identity
        display_identity = qnx_display_identity.contract(qnx_display_profile)
        if load_linux:
            import linux_mcu_identity
            linux_display_identity=linux_mcu_identity.contract()
    if board_adapter and qemu is None:qemu=vm.ROOT/'runtime/qemu-system-aarch64.exe'
    if qemu is not None:cfg['qemu']=qemu.resolve()
    if gl_runtime:
        parent_qemu=cfg['qemu'];cfg['qemu']=gl_runtime.carrier()
        vm.save(output/'gl-forward-carrier.json',dict(profile=gl_runtime.PROFILE,parent=str(parent_qemu),parent_sha256=vm.sha(parent_qemu),selected=str(cfg['qemu']),selected_sha256=vm.sha(cfg['qemu'])))
    ti983_contract=ti983_platform.policy(ti983_requested and ti983_platform.carrier_has_device(
        cfg['qemu'], vm.ROOT/'.local/deps/bin'))
    image, symbols = original('hv.img'),original('hypervisor')
    elf = Elf(symbols.read_bytes())
    symbol_map={row['name']:row for row in elf.symbols}
    bsp=struct.unpack('<Q',elf.at(symbol_map['bsp_config']['address'],8))[0]
    pram_base=struct.unpack('<Q',elf.at(bsp+136,8))[0]
    pram_size=struct.unpack('<I',elf.at(bsp+144,4))[0]
    if not pram_base%0x200000==0 or not pram_size%0x200000==0 or pram_size>64*1024*1024:
        raise ValueError('Unexpected original persistent RAM layout')
    qmp_port,gdb_port = vm.free_port(),vm.free_port()
    machine='virt,virtualization=on,gic-version='+('2,x-exynos-vlm=on' if board_adapter else '3')+(',nvdimm=on' if map_pram else '')
    if qnx_dp0_reset:
        machine+=',x-exynos-dp0-reset=1'
        if qnx_dp0_phy:machine+=',x-exynos-dp0-phy=1'
        if qnx_dp0_link_config:machine+=',x-exynos-dp0-link-config=1'
        if qnx_dp0_irq:machine+=',x-exynos-dp0-irq=1'
        if qnx_dp0_aux_sink:machine+=',x-exynos-dp0-aux-sink=1'
        vm.save(output/'qnx-dp0-profile.json',dp0)
    if linux_dwc3:
        machine+=',x-exynos-linux-dwc3=1'
        vm.save(output/'linux-dwc3-profile.json',usb3)
        if usb_phy:
            machine+=',x-exynos-linux-usb-phy=1'
            vm.save(output/'linux-usb-phy-profile.json',usb_phy)
    if qnx_dsi:
        machine+=',x-exynos-qnx-dsi=1'
        vm.save(output/'qnx-dsi-profile.json',dsi_boot)
    if qnx_bts:
        import qnx_bts_platform
        bts_contract=qnx_bts_platform.policy(board_adapter and load_qnx and hcb and guest_dtbs)
        machine+=',x-exynos-qnx-bts=1'
        vm.save(output/'qnx-bts-profile.json',bts_contract)
    if linux_mfc:
        machine+=',x-exynos-linux-mfc=1'
        vm.save(output/'linux-mfc-profile.json',mfc)
    if native_ufs:machine+=',x-exynos-ufs=on'
    if ufs_host0:
        machine+=',x-exynos-ufs-host0=1'
        vm.save(output/'ufs-vgen-profile.json',ufs_host0)
    if mailbox_policy:
        machine+=',x-exynos-ufs-mailbox=1'
        vm.save(output/'ufs-mailbox-profile.json',mailbox_policy)
    if vh_policy:
        machine+=',x-exynos-ufs-vh=1'
        vm.save(output/'ufs-vh-profile.json',vh_policy)
    if ufs_prelink:
        machine+=',x-exynos-ufs-prelink=1'
        vm.save(output/'ufs-prelink-profile.json',ufs_prelink)
    if ufs_shareability:
        machine+=',x-exynos-ufs-shareability=on'
        vm.save(output/'ufs-shareability-profile.json',ufs_shareability)
    if abox_dsp:
        machine+=',x-exynos-abox-dsp-off=1'
        vm.save(output/'abox-dsp-off-profile.json',abox_dsp)
    if abox_axi:
        machine+=',x-exynos-abox-axiuser=1'
        vm.save(output/'abox-axiuser-profile.json',abox_axi)
    if abox_dma:
        machine+=',x-exynos-abox-sysmmu=1'
        vm.save(output/'abox-sysmmu-profile.json',abox_dma)
    if abox:
        machine+=',x-exynos-abox-gic=1'
        vm.save(output/'abox-gic-profile.json',abox)
    if scaler:
        machine+=f',x-exynos-scaler-reset={scaler["mode"]}'
        vm.save(output/'scaler-reset-profile.json',scaler)
    if vm3_decon:
        machine+=',x-exynos-linux-decon-window=1'
        vm.save(output/'linux-decon-profile.json',vm3_decon)
    if vm2_dpp:
        machine+=',x-exynos-qnx-dpp-irq=1'
        vm.save(output/'qnx-dpp-profile.json',vm2_dpp)
    if vm3_dpp:
        machine+=',x-exynos-linux-dpp-irq=1'
        vm.save(output/'linux-dpp-profile.json',vm3_dpp)
    if dpp:
        machine+=',x-exynos-dpp-irq=1'
        vm.save(output/'dpp-profile.json',dpp)
    if g2d:
        machine+=",x-exynos-g2d-reset=1"
        vm.save(output/'g2d-reset-profile.json',g2d)
    if linux_uart:
        machine+=",x-exynos-linux-usi-uart=1"
        vm.save(output/'linux-usi-uart-profile.json',linux_uart)
    if g3d00:
        machine+=',x-exynos-qnx-g3d00=1'
        if qnx_g3d00_jm_topology:
            machine+=f',x-exynos-qnx-g3d00-jm-topology={2 if qnx_g3d00_control_jobs else 1}'
        vm.save(output/'qnx-g3d00-profile.json',g3d00)
    if g3d1:
        machine+=f",x-exynos-mali-g3d1-id={g3d1['gpu_id']}"
        if mali_g3d1_discovery:
            machine+=",x-exynos-mali-g3d1-discovery=1"
        if mali_g3d1_reset_irq:
            machine+=",x-exynos-mali-g3d1-reset-irq=1"
        if mali_g3d1_config:
            machine+=",x-exynos-mali-g3d1-config=1"
        if mali_g3d1_empty_power:
            machine+=",x-exynos-mali-g3d1-empty-power=1"
        if jm_topology:
            machine+=f",x-exynos-mali-g3d1-jm-topology={2 if jm_topology['vm3_control'] else 1}"
            g3d1['jm_topology']=jm_topology
        vm.save(output/'mali-g3d1-profile.json',g3d1)
    if pcie2:
        machine+=',x-exynos-pcie2-elbi=1'
        vm.save(output/'pcie2-elbi-profile.json',pcie2)
    if pcie2_phy:
        machine+=',x-exynos-pcie2-phy=1'
        vm.save(output/'pcie2-phy-profile.json',pcie2_phy)
    if pcie2_pcs:
        machine+=',x-exynos-pcie2-pcs=1'
        vm.save(output/'pcie2-pcs-profile.json',pcie2_pcs)
    if pcie2_dbi:
        machine+=',x-exynos-pcie2-dbi=1'
        vm.save(output/'pcie2-dbi-profile.json',pcie2_dbi)
    if pcie2_atu:
        machine+=',x-exynos-pcie2-atu=1'
        vm.save(output/'pcie2-atu-profile.json',pcie2_atu)
    if decon:
        machine+=',x-exynos-decon-window=1'
        vm.save(output/'decon-window-profile.json',decon)
    if sysmmu:
        machine+=',x-exynos-sysmmu-discovery=1'
        vm.save(output/'sysmmu-profile.json',sysmmu)
    if vm3_sysmmu:
        machine+=",x-exynos-linux-sysmmu=1"
        vm.save(output/'linux-sysmmu-profile.json',dict(vm3_sysmmu,vm3_optional_high_ram=high_bank))
    if eqos:
        machine+=',x-exynos-eqos-mac='+str(eqos['mac_le'])
        vm.save(output/'eqos-profile.json',eqos)
    if ufs_phy_isolation:
        machine+=',x-exynos-ufs-phy-isolation=on'
        vm.save(output/'ufs-phy-isolation-profile.json',ufs_phy_isolation)
    if acpm_clock_hz:
        firmware=str(output/'acpm-bl2-loaded-sram.bin').replace(chr(92),chr(47))
        if ',' in firmware:raise ValueError('QEMU machine firmware path contains an unsupported comma')
        machine=machine.replace('virt,','virt-8704-apm,',1)+f',x-exynos-acpm=on,x-exynos-acpm-hz={acpm_clock_hz},x-exynos-acpm-firmware={firmware}'
                                                                           
                                                                               
                                                                          
                                                                      
        if os.environ.get('OEM_8704_ACPM_DOORBELL_DIAGNOSTIC','1')=='1':
            machine+=',x-exynos-acpm-doorbells=on'
    machine+=pscdc_options
    machine+=pmu_options
    machine+=tmu_options
    if tmu_profile:
        _,tmu_contract=tmu_platform.original_contract()
        vm.save(output/'tmu-original-contract.json',tmu_contract)
        if tmu_profile=='virtual-qnx-controller-v1':
            import qnx_tmu_platform
            vm.save(output/'qnx-tmu-original-contract.json',qnx_tmu_platform.original_contract())
            vm.save(output/'tmu-virtual-profile.json',qnx_tmu_platform.profile_policy(tmu_temperature_c))
        else:vm.save(output/'tmu-virtual-profile.json',tmu_platform.profile_policy(tmu_temperature_c))
    if pmu_profile:vm.save(output/'pmu-virtual-profile.json',pmu_platform.profile_policy())
    if usi_spi_profile:vm.save(output/'spi1-original-contract.json',sysreg_platform.original_contract(usi_spi_profile))
    if mcu_spi6:
        machine+=',x-exynos-mcu-spi6-startup=1'
        vm.save(output/'mcu-spi6-original-contract.json',mcu_spi6)
    if usi_i2c_profile:vm.save(output/'linux-i2c-original-contract.json',linux_i2c_platform.original_contract())
    if link_contract:
        vm.save(output/'pmu-link-virtual-profile.json',link_contract)
        vm.save(output/'pmu-link-original-contract.json',pmu_platform.original_link_contract())
    if signal_contract:
        vm.save(output/'pmu-signal-virtual-profile.json',signal_contract)
        vm.save(output/'pmu-signal-original-contract.json',pmu_platform.original_signal_contract())
    if domain_contract:vm.save(output/'pmu-domain-virtual-profile.json',domain_contract)
    if debug_contract:vm.save(output/'pmu-debug-virtual-profile.json',debug_contract)
    if serializer_contract:vm.save(output/'serializer-virtual-profile.json',serializer_contract)
    if ti983_requested:
        vm.save(output/'ti983-virtual-profile.json',ti983_contract or {'status':'carrier_missing_ti983',
                'scope':'Existing carrier remains bootable; rebuild it before testing the TI983 dependency'})
    if wdt_contract:
        vm.save(output/'apm-watchdog-virtual-profile.json',wdt_contract)
        vm.save(output/'apm-watchdog-original-contract.json',apm_watchdog.original_contract())
    if pscdc_profile:
        vm.save(output/'pscdc-virtual-profile.json',profile_policy(pscdc_profile,pscdc_latency_cycles))
    memory='3072' if hcb_ram else '2048'
    base_file_ram=os.environ.get('OEM_8704_OFFLINE_BASE_RAM','')
    if base_file_ram and not (base_file_ram=='file-v1' and host_file_ram and offline_policy
                              and second_bank and load_linux and load_android and load_qnx):
        raise ValueError('Base file RAM requires the original three-domain offline file-RAM layout')
    if base_file_ram:
        machine+=',memory-backend=mach-virt.ram'
    if host_file_ram:
        if (os.name not in ('nt','posix') or not map_pram or not hcb_ram or loader_ram_mib!=1024
                or not high_bank or not qnx_high_bank):
            raise ValueError('Windows file RAM requires the full native Auto9 memory layout')
        accelerator+=',tb-size=256'
        base_bytes=int(memory)<<20
        vm.save(output/'host-ram-backing.json',dict(profile='windows-ephemeral-file-all-ram' if base_file_ram else 'windows-ephemeral-file-aux-ram',
            file_backed_bytes=loader_ram_mib*1024*1024+high_bank[1]+qnx_high_bank[1]+sum(n for _,n in android_high_banks)+(second_bank[1] if second_bank else 0)+(base_bytes if base_file_ram else 0),
            translator_cache_mib=256, base_ram_unchanged=not bool(base_file_ram), base_ram_bytes=base_bytes,
            base_ram_backing='file' if base_file_ram else 'anonymous', guest_addresses_unchanged=True))

    def memory_backend(name,size):
        if not host_file_ram:
            return f'memory-backend-ram,id={name},size={size}'
        return json.dumps({'qom-type':'memory-backend-file','id':name,'size':size,
                           'share':True,'mem-path':str(output/(name+'.temporary-ram'))})

    command=[str(cfg['qemu']),'-machine',machine,'-cpu','cortex-a72','-accel',accelerator,
             '-smp',str(cpus),'-m',memory+'M,slots='+str(1+bool(loader_ram_mib)+bool(high_bank)+bool(second_bank)+bool(qnx_high_bank)+len(android_high_banks))+',maxmem=64G' if map_pram else memory,'-display','none','-serial','file:'+str(output/'uart.bin'),'-monitor','none','-nic','none','-S',
             '-device',f'loader,file={image},addr=0x80080000,force-raw=on',
             '-device',f'loader,addr={hex(elf.entry)},cpu-num=0',
             '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off','-gdb',f'tcp:127.0.0.1:{gdb_port}',
             '-d',('in_asm,guest_errors,unimp' if trace_mode=='blocks' else 'guest_errors,unimp'),'-D',str(output/'tcg.log'),'-no-reboot',
             '-name','8704-original-vlm-el2']
    lifecycle_trace=os.environ.get('OEM_8704_VM_LIFECYCLE_TRACE','')
    if lifecycle_trace:
        if lifecycle_trace!='stop-writer-v1' or lifetime is None or not board_adapter or not native_parallel_tcg:
            raise ValueError('Original VM lifecycle trace requires the persistent native three-domain board')
        targets=[]
        for name in ('vm_stop','vm_restart','vm_halt','_vm_state_event_set_running'):
            row=next(x for x in elf.symbols if x['name']==name)
            address,size=row['address'],row['size']
            body=elf.at(address,size)
            if not size or image.read_bytes()[address-0x80080000:address-0x80080000+size]!=body:
                raise ValueError('Original vLM lifecycle producer differs: '+name)
            targets.append(dict(symbol=name,address=hex(address),bytes=size,sha256=hashlib.sha256(body).hexdigest()))
        command[command.index('-d')+1]+=',exec,cpu'
        command+=['-dfilter',','.join(x['address']+'+'+hex(x['bytes'])for x in targets)]
        vm.save(output/'native-vm-lifecycle-trace.json',dict(profile=lifecycle_trace,targets=targets,
            original_hypervisor_sha256=vm.sha(image),register_writes=False,guest_code_changed=False,
            purpose='Observe original stop/halt/restart callers and arguments; no forced running state'))
    command+=eqos_platform.packet_options(eqos)
    if base_file_ram:
        command+=['-object',memory_backend('mach-virt.ram',base_bytes)]
    if om_input:
        command+=['-global',f"driver=exynos-auto9.pmu,property=virtual-om-input,value={om_input['value']}"]
        vm.save(output/'pmu-om-input-profile.json',om_input)
    if display_identity:
        command+=qnx_display_identity.loader_options()
        vm.save(output/'qnx-display-identity.json',display_identity)
    if linux_display_identity:
        command+=linux_mcu_identity.loader_options(linux_display_identity)
        vm.save(output/'linux-mcu-identity.json',linux_display_identity)
    if qnx_phy88q2112:
        command+=['-global','driver=exynos-auto9.eqos,property=phy-88q2112,value=on']
    command+=ufs_descriptor_options
    command+=usi_spi_options
    command+=usi_i2c_options
    if ufs_descriptor_profile:vm.save(output/'ufs-descriptor-profile.json',ufs_platform.descriptor_policy())
    if serializer_contract:
        command+=['-device',serializer_platform.device_option(serializer_profile,board_adapter,serializer_powerup_ns,serializer_probe_ns)]
    if ti983_contract:
        command+=['-device',ti983_platform.OPTION]
    if icount_shift is not None:
        command+=['-icount',f'shift={icount_shift},align=off,sleep=off']
    if map_pram:
        command+=['-object',f'memory-backend-ram,id=exynos-pram,size={pram_size}',
                  '-device',f'nvdimm,memdev=exynos-pram,addr={hex(pram_base)}']
    if loader_ram_mib:
        command+=['-object',memory_backend('exynos-loader-ram',loader_ram_mib*1024*1024),
                  '-device','nvdimm,memdev=exynos-loader-ram,addr=0xc00000000']
    if high_bank:
        base,length=high_bank
        command+=['-object',memory_backend('exynos-vm3-factory-ram',length),
                  '-device',f'nvdimm,memdev=exynos-vm3-factory-ram,addr={base:#x}']
    if second_bank:
        base,length=second_bank
        name='exynos-vm3-factory-ram-b0'
        command+=['-object',memory_backend(name,length),'-device',f'nvdimm,memdev={name},addr={base:#x}']
    if qnx_high_bank:
        base,length=qnx_high_bank
        command+=['-object',memory_backend('exynos-vm2-factory-ram',length),
                  '-device',f'nvdimm,memdev=exynos-vm2-factory-ram,addr={base:#x}']
    if android_high_banks:
        for i,(base,length)in enumerate(android_high_banks):
            name=f'exynos-vm4-factory-ram-{i}'
            command+=['-object',memory_backend(name,length),'-device',f'nvdimm,memdev={name},addr={base:#x}']
        vm.save(output/'android-factory-memory.json',{'vmid':4,'banks':[{'base':hex(b),'bytes':n}for b,n in android_high_banks],
            'original_hcb_sha256':vm.sha(original('hcb/hcb.img')),'owner_verified':True,'actual_qemu_backing':True,
            'scope':'Original VM4-only banks, real file RAM; no overlapping guest/secure ranges, no OOM detector change'})
    if rtc_bootstrap:
        if not board_adapter:raise ValueError('RTC bootstrap requires native board adapter')
        writes=[{'address':0x10540038,'value':8},{'address':0x10540044,'value':0xffffffff},{'address':0x10540040,'value':0x101}]
        for item in writes:
            command+=['-device',f"loader,addr={hex(item['address'])},data={hex(item['value'])},data-len=4"]
        vm.save(output/'bootloader-device-initialization.json',{'rtc':writes,'meaning':'Start real QEMU Auto9 RTC ascending counter at2048Hz; native LK frequency/ascending semantics, local initialization not LK execution'})
    if acpm_clock_hz:
        from acpm_firmware import original_image
        memory,meta=original_image();target=output/'acpm-bl2-loaded-sram.bin';target.write_bytes(memory)
                                                                           
                                                                           
        vm.save(output/'acpm-original-code-input.json',{'input':meta,'derived_load_memory':str(target),
            'sha256':vm.sha(target),'virtual_clock_hz':acpm_clock_hz,'physical_clock_verified':False,
            'meaning':'Verified original BL2 scatter copies; actual management CPU/SysTick, not a preinitialized runtime table',
            'reset_rom_executed':False,'full_acpm_boot_verified':False})
    fvmap=None
    if bl2_fvmap_profile:
        from bl2_fvmap import execute, validate
        source=factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')
        fvmap,producer=execute(source.read_bytes(),100000,True,True,True)
        if not producer['fragment_returned'] or not producer['validation']['abi_valid']:
            raise ValueError('Original BL2 FVMap construction/ABI did not complete')
        validate(fvmap)
        (output/'bl2-generated-fvmap.bin').write_bytes(fvmap)
        vm.save(output/'bl2-fvmap-producer.json',{'source':str(source),'source_sha256':vm.sha(source),**producer,
            'native_probe_input_authorized':True,'meaning':'Bounded original BL2 component execution for an explicit virtual silicon profile; not full boot or physical calibration'})
    if acpm_sram:
        if not board_adapter or acpm_sram.stat().st_size!=0x38000:
            raise ValueError('Require native APM model and exact 0x38000-byte ACPM SRAM snapshot')
        command+=['-device',f'loader,file={str(acpm_sram.resolve()).replace(chr(92),chr(47))},addr=0x02039000,force-raw=on']
        vm.save(output/'acpm-sram-input.json',{'path':str(acpm_sram.resolve()),'sha256':vm.sha(acpm_sram),'bytes':0x38000,
            'meaning':'Explicit external runtime SRAM snapshot; not ACPM processor execution or an invented firmware response'})
    if load_qnx:
        if not (hcb_ram and guest_dtbs and payload_sized_qnx):raise ValueError('QNX loading requires populated HCB, payload reservation and guest DTBs')
        payload=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
        raw=payload.read_bytes();header=raw.find(b'\xeb\x7e\xff\x00',0,65536)
        if header!=0xfb4 or struct.unpack_from('<H',raw,header+10)[0]!=183:
            raise ValueError('Unexpected native QNX startup header')
        startup,bias,physical=struct.unpack_from('<III',raw,header+12)
        load=physical-header
        if load!=0x91080000 or startup-load!=0x1800 or bias:
            raise ValueError('Unverified native QNX image placement')
        command+=['-device',f'loader,file={payload},addr={hex(load)},force-raw=on']
        vm.save(output/'guest-image-loads.json',[{'guest':'qnx','path':str(payload),'sha256':vm.sha(payload),
                'physical':hex(load),'bytes':len(raw),'startup_header_offset':header,'startup_entry':hex(startup),
                'first_instruction_entry':hex(load),'startup_file_offset':startup-load,'original_bytes_unchanged':True}])
    loaded=[]
    if (load_linux or load_android) and not (hcb_ram and guest_dtbs):
        raise ValueError('Original kernels require populated HCB and native guest DTBs')
    if load_linux:
        kernel=factory_file('ivi/Image')
        if abox_dsp_mailbox:
            from abox_sft_platform import linux_asrc_policy
            vm.save(output/'linux-abox-asrc-profile.json',linux_asrc_policy(kernel))
        if vh_policy:
            ufs_vh_platform.verify_linux(kernel)
        header=kernel.read_bytes()[:64]
        if header[56:60]!=b'ARMd':raise ValueError('Original IVI ARM64 Image header missing')
        loaded.append({'guest':'linux','vmid':3,'kernel':str(kernel),'kernel_bytes':kernel.stat().st_size,
                       'kernel_sha256':vm.sha(kernel),'physical':0xb8080000,'ramdisk_bytes':0})
    if load_qnx and abox_dsp_mailbox:
        from abox_sft_platform import qnx_uaif2_policy
        vm.save(output/'qnx-abox-uaif2-profile.json',qnx_uaif2_policy())
    if load_android:
        boot=factory_file('android/boot.img');vendor_boot=factory_file('android/vendor_boot.img')
        kernel,generic,vendor=vm.android_parts(boot.read_bytes(),vendor_boot.read_bytes())
        if mali_g3d1_discovery:
            mali_g3d1_platform.verify_discovery(mali_g3d1_discovery,kernel,mali_g3d1_reset_irq,mali_g3d1_config,mali_g3d1_empty_power)
        if g2d:
            g2d_reset_platform.verify_kernel(kernel)
        if mailbox_policy:
            ufs_mailbox_platform.verify_android(kernel)
        if vh_policy:
            ufs_vh_platform.verify_android(kernel)
        if abox:
            abox_gic_platform.verify_android(kernel)
        if abox_axi:
            abox_gic_platform.verify_axi_kernel(kernel)
        if abox_dsp:
            abox_dsp_platform.verify_kernel(kernel,abox_dsp_platform.ANDROID)
        if android_initcall_trace:
            android_init_trace.verify_kernel(kernel)
        if kernel[56:60]!=b'ARMd':raise ValueError('Original Android ARM64 Image header missing')
        kernel_file=output/'android-kernel-original.bin';kernel_file.write_bytes(kernel)
                                                                             
        ramdisk=vendor+generic if lk_android_normal else generic+vendor
        if offline_policy:
            from offline_development import adapt_ramdisk
            ramdisk,derived=adapt_ramdisk(ramdisk)
            vm.save(output/'offline-development.json',dict(offline_policy,ramdisk=derived))
        ramdisk_file=output/('android-ramdisk-offline.gz' if offline_policy else 'android-ramdisk-original.gz');ramdisk_file.write_bytes(ramdisk)
        loaded.append({'guest':'android','vmid':4,'kernel':str(kernel_file),'kernel_bytes':len(kernel),
                       'kernel_sha256':vm.sha(kernel_file),'physical':0xa0080000,'ramdisk':str(ramdisk_file),
                       'ramdisk_bytes':len(ramdisk),'ramdisk_order':'vendor+generic' if lk_android_normal else 'generic+vendor',
                       'ramdisk_sha256':vm.sha(ramdisk_file),'ramdisk_physical':0xa5000000,
                       'boot_parent_sha256':vm.sha(boot),'vendor_parent_sha256':vm.sha(vendor_boot)})
    for item in loaded:
        command+=['-device',f"loader,file={item['kernel']},addr={hex(item['physical'])},force-raw=on"]
        if item.get('ramdisk'):
            command+=['-device',f"loader,file={item['ramdisk']},addr={hex(item['ramdisk_physical'])},force-raw=on"]
    if loaded:vm.save(output/'kernel-loads.json',loaded)
    if hcb:
        hcb_image=original('hcb/hcb.img')
        data=hcb_image.read_bytes();metadata={'changes':[],'original_hcb_sha256':vm.sha(hcb_image)}
        if hcb_ram:
            data,ram_change=hardware_hcb_copy(data,loader_ram_mib*1024*1024,
                                             linux_factory_bank,qnx_factory_bank,android_factory_banks=bool(offline_policy),
                                             linux_second_bank=bool(second_bank))
            metadata['changes'].append(ram_change)
        if payload_sized_qnx:
            payload=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
            data,change=payload_sized_hcb(data,payload.stat().st_size)
            change.update(payload=str(payload),payload_sha256=vm.sha(payload));metadata['changes'].append(change)
        if loaded:
            data,changes=loaded_kernels_hcb(data,loaded);metadata['changes']+=changes
        if hcb_ram or payload_sized_qnx or loaded:
            hcb_image=output/'hcb-virtual-board.img';hcb_image.write_bytes(data)
            metadata['derived_hcb_sha256']=vm.sha(hcb_image)
            vm.save(output/'hcb-adaptation.json',metadata)
        command+=['-device',f'loader,file={hcb_image},addr=0x82000000,force-raw=on']
    elif payload_sized_qnx or guest_dtbs:
        raise ValueError('Derived HCB/guest boot parameters require --hcb')
    if guest_dtbs:
        placements=[]
        for kind,relative,base in (
                ('qnx','qnx/qtd/icas3gp-qla-vm.dtb',0x90200000),
                ('linux','ivi/icas3cn-gp_b0_zero.dtb',0xbc000000)):
            path=factory_file(relative)
            if kind=='linux' and lk_linux_slot:
                parent=path;raw,metadata=lk_linux_boot_dtb(parent.read_bytes(),lk_linux_slot)
                lk=original('lk-boot/lk.bin')                                                
                metadata.update(lk_path=str(lk),lk_sha256=vm.sha(lk),original_path=str(parent))
                path=output/'linux-lk-bootargs.dtb';path.write_bytes(raw)
                vm.save(output/'linux-lk-bootargs.json',metadata)
            if kind=='linux' and linux_initcall_trace:
                native=path.read_bytes()
                diagnostic,meta=android_init_trace.boot_dtb(native)
                vm.save(output/'linux-initcall-dtb.json',meta)
                path=output/'linux-initcall.dtb';path.write_bytes(diagnostic)
            if kind=='linux' and offline_policy:
                from offline_development import adapt_linux_dtb
                data,dev_linux=adapt_linux_dtb(path.read_bytes())
                path=output/'linux-offline.dtb';path.write_bytes(data)
                vm.save(output/'linux-offline-development.json',dev_linux)
            if kind=='linux' and gl_runtime:
                data=gl_runtime.stage(path.read_bytes(),output)
                path=output/'linux-gl-forward.dtb';path.write_bytes(data)
            command+=['-device',f'loader,file={path},addr={hex(base)},force-raw=on']
            placements.append({'guest':kind,'path':str(path),'base':hex(base),'sha256':vm.sha(path)})
        vendor=factory_file('android/vendor_boot.img').read_bytes()
        dtb_size=struct.unpack_from('<I',vendor,2100)[0]
        header_size,page,ramdisk=struct.unpack_from('<I',vendor,2096)[0],struct.unpack_from('<I',vendor,12)[0],struct.unpack_from('<I',vendor,24)[0]
        offset=((header_size+page-1)//page+(ramdisk+page-1)//page)*page
        data=vendor[offset:offset+dtb_size];parse_dtb(data)
        if sysmmu and hashlib.sha256(data).hexdigest()!=sysmmu['factory_android_dtb_sha256']:
            raise ValueError('Loaded original Android DT does not match SysMMU contract')
        if load_android and abox_dsp_mailbox:
            from abox_sft_platform import sft_mux_policy
            vm.save(output/'android-sft-mux-profile.json',sft_mux_policy(kernel,data))
        path=output/'android-factory.dtb';path.write_bytes(data)
        if lk_android_normal:
            lk=original('lk-boot/lk.bin')
            from ufs_android_media import avb_handoff
            data,handoff=lk_android_normal_dtb(data,lk.read_bytes(),vendor,avb_handoff(ufs_lun0_image))
            vm.save(output/'android-lk-bootargs.json',handoff)
            path=output/'android-lk-bootargs.dtb';path.write_bytes(data)
        if android_initcall_trace:
            diagnostic,meta=android_init_trace.boot_dtb(data)
            vm.save(output/'android-initcall-dtb.json',meta)
            path=output/'android-initcall.dtb';path.write_bytes(diagnostic)
        if offline_policy:
            from offline_development import adapt_dtb
            data,dev_dtb=adapt_dtb(path.read_bytes())
            path=output/'android-offline.dtb';path.write_bytes(data)
            detail=json.loads((output/'offline-development.json').read_bytes())
            detail['dtb']=dev_dtb;vm.save(output/'offline-development.json',detail)
        command+=['-device',f'loader,file={path},addr=0xa4000000,force-raw=on']
        placements.append({'guest':'android','path':str(path),'base':'0xa4000000','sha256':vm.sha(path),'parent_sha256':hashlib.sha256(vendor).hexdigest()})
        vm.save(output/'guest-dtb-loads.json',placements)
    if exynos_uart:
        command+=['-chardev','file,id=exynos-log,path='+str(output/'exynos-uart.bin'),
                  '-device','exynos4210.uart,chardev=exynos-log,rx-size=256,tx-size=256']
    if board_adapter:
                                                                 
                                                                       
        if linux_native_console_port is not None:
            from linux_uart_platform import native_console_arguments
            serial, detail = native_console_arguments(linux_native_console_port, output, lk_linux_slot)
            command+=serial
            vm.save(output/'linux-native-console.json',detail)
        else:
            command+=['-serial','file:'+str(output/'exynos-uart.bin')]
        from qnx_mcu_uart import serial_arguments
        command+=serial_arguments(output,load_qnx)
    if linux_uart:
        from linux_early_uart import DIRECT_INDEX, DIRECT_TTY
        for row in linux_uart['ports']:
            if linux_direct_console and row['index']==DIRECT_INDEX:
                from linux_early_uart import DIRECT_META, direct_serial
                boot=json.loads((output/'linux-initcall-dtb.json').read_bytes())
                if boot.get('local_linux_shell') is not True or boot.get('local_linux_shell_tty')!=DIRECT_TTY:
                    raise ValueError('Dedicated Linux console requires the selected VM3 UART shell staging')
                for _ in range(8):
                    port=vm.free_port()
                    if port not in (qmp_port,gdb_port,linux_native_console_port):
                        break
                else:
                    raise ValueError('No distinct Linux UART loopback port')
                serial,detail=direct_serial(output,port,linux_uart)
                command+=serial
                vm.save(output/DIRECT_META,detail)
            else:
                command+=['-serial','file:'+str(output/f"linux-usi{row['index']:02}-uart.bin")]
    if cal_init_config_profile:
        import cal_init_platform
        command[2]+=',x-exynos-cal-init=1'
    ect_handoff=None
    if ect_handoff_profile:
        import cal_init_platform
        ect_handoff=cal_init_platform.recover_ect(output)
        vm.save(output/'ect-original-handoff.json',ect_handoff)
        command+=['-device',f"loader,file={ect_handoff['path']},addr={ect_handoff['physical']},force-raw=on"]
    if lk_pwm_profile:
        import lk_pwm_platform
        vm.save(output/'lk-pwm-original-contract.json',lk_pwm_platform.contract())
        command[2]+=',x-exynos-lk-pwm=1'
    ufs_disk = None
    if ufs_lun0_image is not None:
        from ufs_virtual_disk import launch_options
        disk_args, ufs_disk = launch_options(ufs_lun0_image, output, qnx_parent=qnx_ufs_parent)
        command += disk_args
        vm.save(output/'ufs-lun0-input.json', ufs_disk)
    from dsp_bootstrap import policy as dsp_load_policy
    dsp_load=dsp_load_policy(lk_dsp_load,lk_handoff_profile,ufs_host0,ufs_disk,lk_linux_slot)
    if dsp_load:
        vm.save(output/'dsp-preboot-profile.json',dsp_load)
    vh2_overlay=None
    if android_ufs_overlay:
        from ufs_virtual_disk import create_overlay
        vh2_overlay=create_overlay(ufs_disk,output,'ufs-vh2-lun0-overlay.qcow2',android_parent=android_ufs_parent)
        vm.save(output/'android-ufs-overlay.json',{'path':str(vh2_overlay),'owner':'VM4 / UFS VH2',
            'base':ufs_disk['base'],'base_read_only':True,'virtual_policy_not_factory_lun_acl':True,
            'prefilled_filesystem':False,'prior_guest_written_data':str(android_ufs_parent.resolve()) if android_ufs_parent else None,
            'cold_boot':True,'prior_overlay_sha256':vm.sha(vh2_overlay) if android_ufs_parent else None})
    hmi_disk=None
    hmi_predecessor=None
    if linux_hmi_overlay is not None:
        from linux_hmi_overlay import launch_policy
        hmi_disk=launch_policy(linux_hmi_overlay,ufs_disk['base'])
        hmi_predecessor=hmi_disk.get('install_metadata_transition',{}).get('predecessor')
        vm.save(output/'linux-vw-overlay.json',hmi_disk)
    linux_data=None
    if linux_ivi_data is not None:
        from linux_data_overlay import create as create_ivi_data
        linux_data=create_ivi_data(linux_ivi_data,ufs_disk['base'],hmi_disk,output,previous=linux_ivi_parent)
        vm.save(output/'linux-ivi-data.json',linux_data)
        vh_policy.update(linux_private_overlay=True,backend=(
            'VM3 session-private /data layer over read-only HMI/lg_custom; VM4 separate '+
            ('writable qcow2' if android_ufs_overlay else 'read-only base')))
        vm.save(output/'ufs-vh-profile.json',vh_policy)
    if vh_policy:
        command+=ufs_vh_platform.devices(vh2_overlay,hmi_disk['overlay'] if hmi_disk else None,
                                         linux_data['path'] if linux_data else None,hmi_predecessor)
    boot_luns={}
    if ufs_boot_luns is not None:
        from ufs_boot_luns import launch_options as launch_boot_luns
        options,boot_luns=launch_boot_luns(ufs_boot_luns)
        command+=options
        vm.save(output/'ufs-boot-luns-input.json',boot_luns)
    if abox_dsp_xtensa is not None and not (abox_dsp and dsp_load and board_adapter):
        raise ValueError('Live DSP peer requires original LK, UFS DSP DMA and guarded AP PMU status')
    if abox_dsp_mailbox and abox_dsp_xtensa is None:
        raise ValueError('Native ABOX mailbox requires a live original DSP peer')
    from contextlib import ExitStack
    from abox_dsp_peer import DspPeer, snapshot as dsp_peer_snapshot
    from abox_mailbox_wire import MailboxWire
    environment=os.environ.copy()
    if board_adapter:
        environment['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+environment.get('PATH','')
    before=vm.sha(image)
    with ExitStack() as stack:
        peer = wire = None
        if abox_dsp_mailbox:
            import abox_dsp_platform
            banks=abox_dsp_platform.mailbox_banks(cfg['qemu'],abox_dsp_xtensa)
            wire = stack.enter_context(MailboxWire(output/'abox-mailbox-wire',banks=banks))
            command += wire.arguments("ap")
        if abox_dsp_xtensa is not None:
            peer = stack.enter_context(DspPeer(abox_dsp_xtensa,
                output/'original-lk-stage/dsp-ufs-load/core0-ap-dma.bin',
                output/'live-dsp-core0', environment,
                mailbox_arguments=wire.arguments("dsp") if wire else ()))
            command += peer.arguments
        from qnx_hud_startup import stage as stage_qnx_hud_startup
        stage_qnx_hud_startup(command,output,qnx_display_profile)
        if gl_runtime:
            gl_host=stack.enter_context(gl_runtime.Host(output));command+=gl_host.arguments
        vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(cfg['qemu']),'input_sha256':before,'hypervisor_entry':hex(elf.entry),'qmp_port':qmp_port,'gdb_port':gdb_port,
                                  'ram_capture_base':0x80000000,'hcb_bootloader_x0':hex(0x82000000) if hcb else None,
                                  'tcg_execution':tcg_policy,
                                  'instruction_clock':{'shift':icount_shift,'ns_per_instruction':1<<icount_shift,'physical_cycle_accuracy_claimed':False} if icount_shift is not None else None,
                                  'scope':('Original production Hypervisor on the opt-in Exynos board extension; not full guest/UI acceptance'
                                           if board_adapter else 'Original production Hypervisor execution on generic virt with EL2; not Exynos guest/UI acceptance')})
        log = stack.enter_context((output/'qemu.log').open('wb'))
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        qmp=rsp=None
        try:
            if native_performance_cores:
                from native_cpu_policy import apply_to_child
                vm.save(output/'native-host-cpu-policy.json',apply_to_child(process))
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('QEMU exited; see qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('QMP did not become ready')
            if native_parallel_tcg:
                threads=native_parallel_threads(qmp.execute('query-cpus-fast'),cpus)
                vm.save(output/'native-tcg-thread-readback.json',dict(tcg_policy,threads=threads,
                    status='distinct_host_vcpu_threads_before_boot'))
                if os.environ.get('OEM_8704_FAST_AP_THREADS')=='1' or accelerated_seed:
                    if not (board_adapter and acpm_clock_hz and cpus==8 and (seconds==stock_profile_seconds or accelerated_seed) and
                            load_linux and load_android and load_qnx and not steps and
                            qmp.execute('query-status')['status']=='prelaunch'):
                        raise ValueError('Fast AP workers require a paused native three-domain board in a validated profile')
                    from native_cpu_policy import prefer_ap_threads
                    split = os.environ.get('OEM_8704_LINUX_SEED_SPLIT_CPU')=='1'
                    if split and not (accelerated_seed and seconds == stock_profile_seconds):
                        raise ValueError('Split CPU placement requires the 240-second seed rehearsal')
                    qnx_workers=()
                    if os.environ.get('OEM_8704_QNX_DISPLAY_CPU_SPLIT')=='1':
                        if accelerated_seed or split or seconds!=stock_profile_seconds or not hcb:
                            raise ValueError('QNX display CPU placement requires the routine native board')
                        raw=original('hcb/hcb.img').read_bytes()
                        off=raw.index(b'VDTHYP\0\0')+8
                        tree=parse_dtb(raw[off:off+struct.unpack_from('>I',raw,off+4)[0]])
                        if tree['/vlm/vm@2/vcpus']['affinity']!=b'0,4\0':
                            raise ValueError('Original QNX AP affinity changed')
                        qnx_workers=(0,4)
                    vm.save(output/'native-host-ap-thread-policy.json',
                            prefer_ap_threads(process,threads,linux_seed_split=split,qnx_workers=qnx_workers))
            if display_identity:
                check=qnx_display_identity.readback(qmp)
                vm.save(output/'qnx-display-identity.json',dict(display_identity,before_boot=check))
                if not check['all_match']:
                    raise ValueError('Virtual QNX display configuration was not loaded')
            if ect_handoff:
                target=output/'ect-before-original-boot.bin'
                qmp.execute('pmemsave',{'val':int(ect_handoff['physical'],16),'size':ect_handoff['bytes'],'filename':str(target)})
                if vm.sha(target)!=ect_handoff['sha256']:raise ValueError('Original ECT loader RAM readback mismatch')
                vm.save(output/'ect-original-readback.json',{'path':str(target),'sha256':vm.sha(target),'all_original_bytes_match':True,'before_guest_execution':True})
            if pmu_link_profile:
                for key,value in (('virtual-pmlink-v1',1),('link-peer-connected',1)):
                    qmp.execute('qom-set',{'path':'/machine/exynos-pmu','property':key,'value':value})
            if pmu_signal_profile:
                for key,value in (('virtual-latched-signals-v1',1),('signal-peer-connected',1),('virtual-status1204',pmu_status1204),('virtual-core-status-mask',pmu_core_status_mask)):
                    qmp.execute('qom-set',{'path':'/machine/exynos-pmu','property':key,'value':value})
            if domain_contract:
                for key,value in (('transition-ns',pmu_domain_latency_ns),('supply-good-mask',pmu_domain_supply_mask),('profile',1)):
                    qmp.execute('qom-set',{'path':pmu_domain.QOM_PATH,'property':key,'value':value})
            if debug_contract:
                for key,value in (('dptx-debug-word0',pmu_dptx_debug_word0),('dptx-debug-word1',pmu_dptx_debug_word1),('dptx-debug-word2',pmu_dptx_debug_word2),('dptx-debug-profile',1)):
                    qmp.execute('qom-set',{'path':pmu_debug.QOM,'property':key,'value':value})
            if apm_wdt_profile:
                for key,value in (('clock-hz',apm_wdt_clock_hz),('profile',1)):
                    qmp.execute('qom-set',{'path':'/machine/exynos-apm-wdt','property':key,'value':value})
            if native_ufs:
                from ufs_platform import route_contract
                qmp.execute('qom-set',{'path':'/machine/exynos-ufs0','property':'transport-enabled','value':True})
                vm.save(output/'ufs-native-contract.json',{'route':route_contract(),
                    'transport_enabled_after_reset':True,'standard_hci_only':True,
                    'attached_luns':int(ufs_disk is not None)+len(boot_luns),'factory_partition_layout_verified':False,
                    'oem_ufs_driver_started_verified':False,
                    'scope':'Native adapter with optional derived GPT; not factory disk layout or Samsung PHY/FMP completion'})
                if ufs_disk is not None:
                    wanted = {'ufs-base-file','ufs-base','ufs-overlay-file','ufs-oem-lun0'}
                    nodes = {r['node-name']:r for r in qmp.execute('query-named-block-nodes') if r.get('node-name') in wanted}
                    if (set(nodes) != wanted or not nodes['ufs-base']['ro'] or not nodes['ufs-base-file']['ro']
                            or nodes['ufs-oem-lun0']['ro']):
                        raise ValueError('UFS disk backing permissions differ from launch policy')
                    vm.save(output/'ufs-lun0-block-nodes.json',nodes)
                if vh_policy:
                    vm.save(output/'ufs-vh-block-nodes.json',ufs_vh_platform.enable(
                        qmp,android_ufs_overlay,bool(hmi_disk),bool(linux_data),bool(hmi_predecessor)))
                if boot_luns:
                    wanted={f'ufs-oem-boot-lun{i}{tail}' for i in boot_luns for tail in ('','-file')}
                    nodes={r['node-name']:r for r in qmp.execute('query-named-block-nodes')
                           if r.get('node-name') in wanted}
                    if set(nodes)!=wanted or not all(node['ro'] for node in nodes.values()):
                        raise ValueError('Boot LUN block node policy must remain read-only')
                    vm.save(output/'ufs-boot-luns-block-nodes.json',nodes)
            vm.save(output/'memory-tree.json',{'output':qmp.execute('human-monitor-command',{'command-line':'info mtree'})})
            if tmu_profile:
                qmp.execute('qom-set',{'path':'/machine/exynos-tmu','property':'temperature-c','value':tmu_temperature_c})
            if pmic_bootstrap_profile:
                from pmic_bootstrap import contract
                profile=contract()
                vm.save(output/'pmic-bootstrap-contract.json',profile)
                for controller in range(2):
                    qmp.execute('qom-set',{'path':f'/machine/exynos-speedy{controller}','property':'virtual-bl2-rev0','value':1})
            if pmic_pmu_input_byte is not None:
                qmp.execute('qom-set',{'path':'/machine/exynos-speedy0','property':'virtual-pmu-input-byte','value':pmic_pmu_input_byte})
                vm.save(output/'pmic-pmu-input-policy.json',{'byte':pmic_pmu_input_byte,
                    'controller':0,'device':1,'register':'0x0c','access':'read-only',
                    'source':'Explicit virtual input; original PMU init13188 consumes bit0',
                    'physical_register_meaning_verified':False,'physical_reset_value_verified':False,
                    'guest_images_modified':False,'scope':'Not a full PMIC or power-on event emulation'})
            if pmic_interface_reset_byte is not None:
                for controller in range(2):
                    qmp.execute('qom-set',{'path':f'/machine/exynos-speedy{controller}','property':'virtual-interface-byte','value':pmic_interface_reset_byte})
                vm.save(output/'pmic-virtual-policy.json',{'interface_reset_byte':pmic_interface_reset_byte,
                    'source':'Explicit virtual-board policy, NOT original PMIC reset values or SRAM snapshot',
                    'physical_reset_byte_verified':False,'pmic_identity_verified':False,
                    'bootstrap_profile':pmic_bootstrap_profile,'full_bl2_executed':False,
                    'supported_endpoint':'Common interface plus optional explicit sparse BL2 revision-zero policy; see contract, no arbitrary unknown-register values'})
            rsp=RSP(gdb_port)
            if acpm_clock_hz:
                                                                            
                                                                        
                replies=rsp.attach_inferiors((3,))
                selected=rsp.select_application_cpu()
                vm.save(output/'gdb-boot-cpu.json',{'thread':selected,'attach_replies':replies,'meaning':'Explicit original ARM64 CPU0, not management core'})
            if virtual_rx is not None:
                if qmp.execute('query-status').get('running'):
                    raise ValueError('Virtual predecessor may be applied only before any CPU runs')
                keys=('bank0-mask','bank0-pending','bank0-generated','bank0-cleared','bank0-irq-rises','bank1-generated')
                virtual_before={k:qmp.execute('qom-get',{'path':'/machine/exynos-apm-mailbox','property':k}) for k in keys}
                if virtual_before['bank0-pending'] or virtual_before['bank0-generated'] or virtual_before['bank1-generated']:
                    raise ValueError('Virtual predecessor cannot discard or acknowledge a queued native transaction')
                rsp.write_boot_memory(0x10500010,struct.pack('<I',virtual_rx['value']))
                virtual_after={k:qmp.execute('qom-get',{'path':'/machine/exynos-apm-mailbox','property':k}) for k in keys}
                if virtual_after['bank0-mask']!=0 or any(virtual_after[k]!=virtual_before[k] for k in keys if k!='bank0-mask'):
                    raise ValueError('Virtual receiver mask changed an unrelated state')
                for controller in range(2):
                    qmp.execute('qom-set',{'path':f'/machine/exynos-speedy{controller}','property':'virtual-debug-snapshot','value':1})
                vm.save(output/'virtual-acpm-rx-predecessor.json',dict(virtual_rx,before=virtual_before,after=virtual_after,
                    applied_by='Host debugger MMIO input before original CPUs run; not an OEM writer',
                    firmware_queue_consumption_verified=False,original_boot_chain_completed=False))
            if hcb:rsp.set_x0(0x82000000)
            registers=[rsp.registers()]
            for _ in range(steps):
                stop=rsp.command('s').decode()
                if not stop.startswith(('S05','T05')):raise RuntimeError('Unexpected single-step result: '+stop)
                registers.append(rsp.registers())
            vm.save(output/'steps.json',registers)
            if fvmap is not None:
                                                                           
                                                                            
                rsp.write_boot_memory(0x02066000,fvmap)
                rsp.write_boot_memory(0x10500088,struct.pack('<I',0x2d000))
                vm.save(output/'bl2-fvmap-handoff.json',{'profile':bl2_fvmap_profile,
                    'physical_base':'0x02066000','management_base':'0x2d000','bytes':len(fvmap),
                    'sha256':hashlib.sha256(fvmap).hexdigest(),'all_bytes_readback_verified':True,
                    'mailbox_address':'0x10500088','mailbox_value':'0x2d000',
                    'physical_calibration_verified':False,'full_bl2_executed':False,
                    'meaning':'Actual local bootloader memory/MMIO writes of original-code output; no MCU completion injected'})
            lk_stage_result=None
            if lk_handoff_profile:
                import lk_runtime
                lk_stage_result=lk_runtime.native_stage(rsp,qmp,selected,output/'original-lk-stage',
                                                        pwm_delay=bool(lk_pwm_profile),dsp_load=dsp_load,dsp_peer=peer is not None)
            if wire and 0 in wire.banks:
                contract=abox_dsp_platform.mailbox0_contract(output/'original-lk-stage/dsp-ufs-load/core0-ap-dma.bin')
                vm.save(output/'qnx-abox-mailbox0-contract.json',contract)
            if pcie2_atu:
                vm.save(output/'pcie2-atu-handoff.json',pcie2_platform.activate_atu(qmp))
                vm.save(output/'pcie2-atu-memory-tree.json',{'output':qmp.execute(
                    'human-monitor-command',{'command-line':'info mtree'})})
            if ufs_power:
                vm.save(output/'ufs-power-profile.json',ufs_power)
                ufs_platform.power_configure(qmp)
            if ufs_prdt:
                ufs_platform.configure_plain128(qmp)
                vm.save(output/'ufs-plain128-profile.json',ufs_prdt)
            if acpm_clock_hz:
                initial_snapshot=rsp.system_snapshot()
                vm.save(output/'management-cpu-before-run.json',{'cpus':[c for c in initial_snapshot['cpus'] if c.get('architecture')=='arm-m']})
                qmp.execute('pmemsave',{'val':0x02040000,'size':0xc0,'filename':str(output/'management-header-before-run.bin')})
                qmp.execute('pmemsave',{'val':0x1050008c,'size':4,'filename':str(output/'management-ready-before-run.bin')})
            if ufs_uic_trace:
                events=ufs_runtime_trace_events(trace_mode,ufs_disk is not None)
                for event in events:
                    qmp.execute('trace-event-set-state',{'name':event,'enable':True})
                vm.save(output/'ufs-runtime-uic-trace.json',{'phase':'after-original-lk-before-original-vlm',
                    'trace_mode':trace_mode,
                    'events':[qmp.execute('trace-event-get-state',{'name':event})[0] for event in events],
                    'trace_file':str(output/'tcg.log'),'guest_state_modified':False})
            if native_hmi_signal_observe:
                event='auto9_vm3_hmi_signal'
                linux=next(item for item in loaded if item['guest']=='linux')
                if linux['kernel_sha256']!='c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109':
                    raise ValueError('Unexpected Linux kernel for the HMI signal observation ABI')
                qmp.execute('trace-event-set-state',{'name':event,'enable':True})
                state=qmp.execute('trace-event-get-state',{'name':event})
                if state!=[{'name':event,'state':'enabled'}]:
                    raise ValueError('QEMU did not enable the original Linux HMI signal observation')
                vm.save(output/'native-linux-hmi-signal-observation.json',dict(event=state,trace_file=str(output/'tcg.log'),
                    kernel_sha256=linux['kernel_sha256'],guest_state_modified=False,
                    scope='VM3 AArch64 setup_rt_frame input, selected signals to JAVA process only, at most 256 rows per AP vCPU'))
                if os.environ.get('OEM_8704_KIPC_TRACE')=='1':
                    event='auto9_vm3_kipc_watch'
                    qmp.execute('trace-event-set-state',{'name':event,'enable':True})
                    state=qmp.execute('trace-event-get-state',{'name':event})
                    if state!=[{'name':event,'state':'enabled'}]:
                        raise ValueError('QEMU did not enable the original Linux KIPC observation')
                    vm.save(output/'native-linux-kipc-observation.json',dict(event=state,
                        trace_file=str(output/'tcg.log'),kernel_sha256=linux['kernel_sha256'],
                        guest_state_modified=False,scope='Original VM3 persistence KIPC routing/dequeue; classified envelope only'))
            if native_tee_observe:
                event='auto9_qnx_tee_edge'
                qmp.execute('trace-event-set-state',{'name':event,'enable':True})
                state=qmp.execute('trace-event-get-state',{'name':event})
                if state!=[{'name':event,'state':'enabled'}]:
                    raise ValueError('QEMU did not enable the original QNX TEE observation event')
                vm.save(output/'native-qnx-tee-observation.json',dict(event=state,trace_file=str(output/'tcg.log'),
                    guest_state_modified=False,scope='Only two known VM2 SMCs and paired exception returns; no argument or payload capture'))
            watch=linux_i2c_platform.install_clock_watch(rsp) if clock_rpc_watch else None
            panic_watch=None
            if linux_panic_watch:
                import linux_panic_watch as panic_observer
                linux=next(item for item in loaded if item['guest']=='linux')
                panic_watch=panic_observer.arm(rsp,symbol_directory,linux)
            abox_watch=None
            if android_abox_watch:
                import android_abox_watch as abox_observer
                a=next(item for item in loaded if item['guest']=='android')
                linux_image=next((item for item in loaded if item['guest']=='linux'),None)
                abox_watch=abox_observer.arm(rsp,Path(a['kernel']).read_bytes(),
                    Path(linux_image['kernel']).read_bytes() if linux_initcall_trace and linux_image else None,qnx_audio_clock_watch)
            import native_psci_watch as psci_observer
            psci_watch=psci_observer.arm_if_selected(rsp,native=bool(board_adapter and hcb and load_qnx and load_linux and load_android),exclusive=bool(watch or panic_watch or abox_watch))
            if psci_watch is not None:vm.save(output/'native-psci-watch.json',psci_watch)
            if not watch and panic_watch is None and abox_watch is None and psci_watch is None:rsp.close();rsp=None
                                                                       
                                                                             
            ppi27 = None
            if board_adapter and native_parallel_tcg and os.environ.get('OEM_8704_PPI27_SAMPLE') == '1':
                ppi27_path = native_ppi27_path(qmp)
                ppi27 = stack.enter_context((output/'native-ppi27.jsonl').open('x', encoding='ascii', buffering=1))
                print(json.dumps(dict(gic=ppi27_path, carrier_sha256=vm.sha(cfg['qemu']))), file=ppi27)
            if lifetime is not None:
                vm.save(output/'native-runtime-lifetime.json',dict(lifetime,session=str(output.resolve()),reference_window_seconds=seconds))
            qmp.execute('cont')
            run_started=time.monotonic();deadline=run_started+seconds
            next_runtime_state=run_started
            trace_window=None
            next_ppi27 = run_started
            if linux_native_console_port is not None:
                from linux_uart_platform import publish_native_focus
            next_focus=run_started
            stop_reason='wall-observation-budget';status_before_stop=None
            if offline_policy:
                from native_offline_console import native_vlm_terminal
            while native_observation_active(time.monotonic(),deadline,lifetime=lifetime) and process.poll() is None:
                if offline_policy:
                    if (output/'offline-stop.request').is_file():
                        stop_reason='offline-user-request';break
                    uart=output/'exynos-uart.bin'
                    if uart.is_file():
                        with uart.open('rb')as capture:
                            capture.seek(max(0,uart.stat().st_size-16384));tail=capture.read(16384)
                        if b'System-wide panic initiated' in tail or b'Kernel panic - not syncing:' in tail:
                            stop_reason='offline-observed-guest-panic';break
                        if native_vlm_terminal(tail):
                            stop_reason='offline-observed-el2-recursive-panic';break
                        if b'bootloader,bootstrap-apexd-failed' in tail:
                            stop_reason='offline-observed-apexd-bootstrap-reboot';break
                if lifetime is not None and (output/lifetime['stop_file']).is_file():
                    stop_reason='user-request';break
                time.sleep(.05)
                status_before_stop=qmp.execute('query-status')
                now=time.monotonic()
                if lifetime is not None and now>=next_runtime_state:
                    try:
                        vm.save(output/'native-runtime-state.json',{'profile':lifetime['profile'],
                            'running':bool(status_before_stop.get('running',False)),'vm_status':status_before_stop.get('status'),
                            'elapsed_wall_seconds':now-run_started,'observed_at_unix':time.time(),
                            'qemu_pid':process.pid,'scope':'Owner QMP state only; NOT boot or GUI interaction acceptance'})
                    except PermissionError:pass
                    next_runtime_state=now+2
                if psci_watch and psci_watch['armed'] and now>=psci_watch['deadline_monotonic'] and status_before_stop.get('running'):
                    qmp.execute('stop');psci_observer.withdraw(rsp,psci_watch)
                    psci_watch.update(complete=True,phase='expired',error='No complete VM4 CPU_ON pair within the original observation window')
                    vm.save(output/'native-psci-watch.json',psci_watch);rsp.close();rsp=None;qmp.execute('cont');continue
                if ppi27 is not None and now >= next_ppi27 and status_before_stop.get('running'):
                    rows = json.loads(qmp.execute('qom-get', {'path': ppi27_path, 'property': 'x-auto9-ppi27-state'}))
                    print(json.dumps(dict(elapsed_s=round(now-run_started, 3), cpus=rows)), file=ppi27)
                    next_ppi27 = now + 2
                if linux_native_console_port is not None and now>=next_focus:
                    try:
                        publish_native_focus(qmp,output,status_before_stop.get('running',False))
                    except PermissionError:
                                                                                           
                                                                                           
                        pass
                    next_focus=now+0.75
                if not status_before_stop.get('running',False):
                    if psci_watch and psci_watch['armed'] and status_before_stop.get('status')=='debug':
                        try:handled=psci_observer.observe(rsp,qmp,psci_watch,output)
                        except (OSError,RuntimeError,ValueError,KeyError,struct.error) as error:
                            psci_watch['error']=repr(error);psci_observer.withdraw(rsp,psci_watch)
                            psci_watch.update(complete=True,phase='observation-error');vm.save(output/'native-psci-watch.json',psci_watch);handled=True
                        if handled:
                            if not psci_watch['armed']:rsp.close();rsp=None
                            qmp.execute('cont');continue
                    if watch is not None and not watch.get('completed',False) and status_before_stop.get('status')=='debug':
                        if not watch.get('hit',False):
                            item=next(i for i in loaded if i['guest']=='linux')
                            observation=linux_i2c_platform.collect_clock_watch(rsp,qmp,output/'linux-clock-probe-watch',item)
                            watch.update(observation)
                            if clock_backend_watch:
                                import clock_rpc_platform
                                discovery=clock_rpc_platform.discover(rsp,qmp,output/'qnx-clock-discovery')
                                watch['backend_targets']=discovery['targets']
                                clock_rpc_platform.arm(rsp,watch['backend_targets'])
                            watch['completed']=not watch.get('backend_targets')
                        else:
                            targets=watch['backend_targets']
                            event=clock_rpc_platform.observe(rsp,qmp,output/f"qnx-clock-event-{watch.get('backend_events',0)}",targets)
                            watch['backend_events']=watch.get('backend_events',0)+1
                                                                                 
                                                                              
                            remove=[r for r in targets if any(h['pc']==hex(r['pc']) for h in event['hits'])]
                            if not event['hits'] or any(h['failed'] for h in event['hits']) or watch['backend_events']>=2:remove=list(targets)
                            clock_rpc_platform.withdraw(rsp,remove)
                            watch['backend_targets']=[r for r in targets if r not in remove]
                            watch['completed']=not watch['backend_targets']
                        if watch['completed']:rsp.close();rsp=None
                        qmp.execute('cont');continue
                    if panic_watch and panic_watch['armed'] and status_before_stop.get('status')=='debug':
                        if panic_observer.observe(rsp,qmp,panic_watch,output):
                            if not panic_watch['armed'] and not (abox_watch and abox_observer.pending(abox_watch)):
                                rsp.close();rsp=None
                            qmp.execute('cont');continue
                    if abox_watch and abox_observer.pending(abox_watch) and status_before_stop.get('status')=='debug':
                        if abox_observer.observe(rsp,qmp,abox_watch,output):
                            vm.save(output/'android-abox-watch.json',abox_watch)
                            if not abox_observer.pending(abox_watch) and not (panic_watch and panic_watch['armed']):
                                rsp.close();rsp=None
                            qmp.execute('cont');continue
                    if lifetime is not None and status_before_stop.get('status') in ('paused','io-error'):continue
                    stop_reason='observed-vm-stop';break
                if (output/'tcg.log').stat().st_size>16*1024*1024:
                    try:
                        trace_stop,trace_window=service_native_trace(qmp,output,lifetime,trace_window)
                        if trace_window is not None:vm.save(output/'native-trace-window.json',trace_window)
                    except (OSError,RuntimeError,ValueError) as exc:
                        vm.save(output/'native-trace-window-error.json',{'error':str(exc),'type':type(exc).__name__})
                        stop_reason='trace-rotation-error';break
                    if trace_stop is not None:
                        stop_reason=trace_stop;break
            vm.save(output/'run-observation.json',{'requested_wall_budget_seconds':seconds if lifetime is None else None,
                'lifetime_profile':None if lifetime is None else lifetime['profile'],'reference_window_seconds':seconds,
                'wall_observation_seconds':time.monotonic()-run_started,'stop_reason':stop_reason,
                'status_before_manual_stop':status_before_stop,'cpu_execution_duration_verified':False,
                'scope':'Wall observation and actual QMP state, NOT continuous guest CPU runtime or boot acceptance'})
            qmp.execute('stop')
            if lifetime is not None:
                final_runtime=qmp.execute('query-status')
                vm.save(output/'native-runtime-state.json',{'profile':lifetime['profile'],
                    'running':bool(final_runtime.get('running',False)),'vm_status':final_runtime.get('status'),
                    'elapsed_wall_seconds':time.monotonic()-run_started,'observed_at_unix':time.time(),
                    'qemu_pid':process.pid,'stop_reason':stop_reason,'scope':'Owner QMP state only; NOT boot acceptance'})
            if gl_runtime and stop_reason in ('user-request','offline-user-request'):
                return dict(status='explicit-private-GL-session-stopped',session=str(output),
                    wall_observation_seconds=time.monotonic()-run_started,
                    stop_reason=stop_reason,original_HMI_execution_verified=False,
                    finalization='owned-stop-without-unrequested-deep-postmortem')
            if linux_native_console_port is not None:
                try:
                    publish_native_focus(qmp,output,False)
                except PermissionError:
                    pass
            if watch is not None:
                if rsp is not None:
                    if watch.get('backend_targets'):clock_rpc_platform.withdraw(rsp,watch['backend_targets'])
                    reply=rsp.command(f'z1,{linux_i2c_platform.WATCH_PC:x},4')
                    if reply[:1] in (b'S',b'T'):reply=rsp.receive()
                    if reply!=b'OK':raise ValueError('Clock observer cleanup failed')
                    rsp.close();rsp=None
                vm.save(output/'clock-watch-state.json',watch)
            if panic_watch is not None:
                if rsp is not None:
                    for name in panic_watch['armed']:panic_observer.withdraw(rsp,name)
                panic_watch['armed']=[]
                vm.save(output/'linux-panic-watch.json',panic_watch)
            if abox_watch is not None:
                if rsp is not None:
                    abox_observer.close_observer(rsp,abox_watch)
                abox_watch['not_hit']=list(abox_watch['armed'])
                abox_watch['armed']=[]
                vm.save(output/'android-abox-watch.json',abox_watch)
            if psci_watch is not None:
                if rsp is not None:psci_observer.withdraw(rsp,psci_watch)
                vm.save(output/'native-psci-watch.json',psci_watch)
            if rsp is not None:
                rsp.close();rsp=None
            if load_qnx and (ect_handoff or cal_init_config_profile):
                cal_init_platform.capture_runtime(qmp,output/'qnx-native-runtime')
            if cal_init_config_profile:
                vm.save(output/'cal-init-config-runtime.json',{k:qmp.execute('qom-get',{'path':'/machine/exynos-cal-init','property':k}) for k in ('config-reads','config-writes')})
            info=qmp.execute('human-monitor-command',{'command-line':'info registers -a'})
            vm.save(output/'cpu.json',{'output':info,'status':qmp.execute('query-status'),'guests_booted_verified':False})
            if usi_i2c_profile:
                vm.save(output/'linux-i2c-runtime-state.json',linux_i2c_platform.runtime_states(qmp))
                linux_image=next((item for item in loaded if item['guest']=='linux'),None)
                if linux_image:linux_i2c_platform.capture_clock_rpc(qmp,output/'linux-clock-rpc-state',linux_image)
            if usi_spi_profile:vm.save(output/'spi1-runtime-state.json',sysreg_platform.runtime_state(qmp))
            if usi_spi_profile:vm.save(output/'spi-runtime-state.json',{str(r['port']):sysreg_platform.runtime_state(qmp,r['port']) for r in sysreg_platform.native_layout()})
            if mcu_spi6:vm.save(output/'mcu-spi6-runtime.json',sysreg_platform.mcu_spi6_runtime(qmp))
            if g3d1:
                vm.save(output/'mali-g3d1-runtime.json',mali_g3d1_platform.runtime_state(qmp,bool(jm_topology),
                    vm3_control=bool(jm_topology and jm_topology['vm3_control'])))
            if g3d00:
                vm.save(output/'qnx-g3d00-runtime.json',qnx_g3d00_platform.runtime(qmp,qnx_g3d00_jm_topology))
            if g2d:
                vm.save(output/'g2d-reset-runtime.json',g2d_reset_platform.runtime_state(qmp))
            if linux_uart:
                vm.save(output/'linux-usi-uart-runtime.json',linux_uart_platform.runtime_state(qmp))
            if pcie2_phy:
                vm.save(output/'pcie2-phy-runtime.json',pcie2_platform.phy_runtime_state(qmp))
            if pcie2_pcs:
                vm.save(output/'pcie2-pcs-runtime.json',pcie2_platform.pcs_runtime_state(qmp))
            if pcie2_dbi:
                vm.save(output/'pcie2-dbi-runtime.json',pcie2_platform.dbi_runtime_state(qmp))
            if pcie2_atu:
                vm.save(output/'pcie2-atu-runtime.json',pcie2_platform.atu_runtime_state(qmp))
            if pcie2:
                vm.save(output/'pcie2-elbi-runtime.json',pcie2_platform.runtime_state(qmp))
            if decon:
                vm.save(output/'decon-window-runtime.json',decon_platform.runtime_state(qmp))
            if qnx_dp0_reset:
                vm.save(output/'qnx-dp0-runtime.json',qnx_dp0_platform.runtime(qmp,qnx_dp0_phy,qnx_dp0_link_config,qnx_dp0_irq,qnx_dp0_aux_sink))
            if dpp:
                vm.save(output/'dpp-runtime.json',dpp_platform.runtime_state(qmp))
            if linux_dwc3:
                vm.save(output/'linux-dwc3-runtime.json',linux_dwc3_platform.runtime(qmp))
                if usb_phy:
                    vm.save(output/'linux-usb-phy-runtime.json',linux_dwc3_platform.phy_runtime(qmp))
                    pmu='/machine/exynos-pmu'
                    keys=('usb0-phy-isolation','usb0-phy-reads','usb0-phy-writes')
                    if any(row['name']=='exynos-pmu' for row in qmp.execute('qom-list',{'path':'/machine'})):
                        fields={row['name'] for row in qmp.execute('qom-list',{'path':pmu})}
                        if all(key in fields for key in keys):
                            secondary=('usb0-phy2-isolation','usb0-phy2-reads','usb0-phy2-writes')
                            keys += tuple(key for key in secondary if key in fields)
                            keys += tuple(sorted(key for key in fields if key.startswith('usb-host-')))
                            vm.save(output/'linux-usb-pmu-runtime.json',{
                                key:qmp.execute('qom-get',{'path':pmu,'property':key}) for key in keys})
            if linux_mfc:
                vm.save(output/'linux-mfc-runtime.json',linux_mfc_platform.runtime(qmp))
            if qnx_dsi:
                vm.save(output/'qnx-dsi-runtime.json',qnx_dsi_platform.runtime(qmp))
                qnx_decon=qnx_dsi_platform.decon_runtime(qmp)
                if qnx_decon is not None:
                    vm.save(output/'qnx-decon-runtime.json',qnx_decon)
            if qnx_bts:
                vm.save(output/'qnx-bts-runtime.json',qnx_bts_platform.runtime(qmp))
            if vm2_dpp:
                vm.save(output/'qnx-dpp-runtime.json',dpp_platform.qnx_runtime(qmp))
                vm.save(output/'qnx-dpp-source-runtime.json',dpp_platform.qnx_source_runtime(qmp))
            if vm3_dpp:
                vm.save(output/'linux-dpp-runtime.json',dpp_platform.linux_runtime(qmp))
                vm.save(output/'linux-dpp-config-runtime.json',dpp_platform.linux_config_runtime(qmp))
            if vm3_decon:
                vm.save(output/'linux-decon-runtime.json',decon_platform.linux_runtime(qmp))
            if scaler:
                vm.save(output/'scaler-reset-runtime.json',scaler_reset_platform.runtime(qmp))
            if abox:
                vm.save(output/'abox-gic-runtime.json',abox_gic_platform.runtime(qmp))
            if abox_axi:
                vm.save(output/'abox-axiuser-runtime.json',abox_gic_platform.axi_runtime(qmp))
            if abox_dsp:
                vm.save(output/'abox-dsp-off-runtime.json',abox_dsp_platform.runtime(qmp))
            if peer is not None:
                vm.save(output/'abox-dsp-peer-runtime.json',dsp_peer_snapshot(qmp))
            if wire is not None:
                vm.save(output/'abox-mailbox-runtime.json',{
                    name:qmp.execute('qom-get', {'path':'/machine/abox-mailbox-link','property':name})
                    for name in ('sends','receives','acknowledgments','rejects')})
                sft_path='/machine/abox-sft'
                props={row['name'] for row in qmp.execute('qom-list',{'path':sft_path})}
                sft_keys=('control','reads','writes','rejects')+tuple(
                    f'rdma{rdma}-mux{suffix}' for rdma in (14,15)
                    for suffix in ('','-reads','-writes','-rejects'))
                sft_keys += tuple('speaker-route'+s for s in ('','-reads','-writes','-rejects'))
                sft_keys += tuple(f'rdma{rdma}-status{suffix}' for rdma in (14,15)
                                  for suffix in ('','-reads','-rejects'))
                sft_keys += tuple(f'uaif{uaif}-ctrl{word}{suffix}' for uaif in (4,5)
                                  for word in (0,1) for suffix in ('','-reads','-writes','-rejects'))
                from abox_sft_platform import LINUX_ASRC, LINUX_UAIF, SHARED_ROUTE_OFFSETS
                sft_keys += tuple(f'linux-{kind}{i}-asrc{suffix}' for kind,_,indices in LINUX_ASRC
                                  for i in indices for suffix in ('','-reads','-writes','-rejects'))
                sft_keys += tuple(f'linux-uaif{i}-ctrl{j}{suffix}' for i in LINUX_UAIF
                                  for j in (0,1) for suffix in ('','-reads','-writes','-rejects'))
                sft_keys += tuple(f'shared-route-{off:x}{suffix}' for off in SHARED_ROUTE_OFFSETS
                                  for suffix in ('','-reads','-rejects'))
                sft_keys += tuple(f'linux-{kind}{i}-status{suffix}' for kind,_,indices in LINUX_ASRC
                                  for i in indices for suffix in ('','-reads','-rejects'))
                sft_keys += tuple(f'qnx-uaif2-ctrl{j}{suffix}' for j in (0,1)
                                  for suffix in ('','-reads','-writes','-rejects'))
                vm.save(output/'abox-sft-runtime.json',{
                    name:qmp.execute('qom-get',{'path':sft_path,'property':name})
                    for name in sft_keys if name in props})
            if abox_dma:
                vm.save(output/'abox-sysmmu-runtime.json',abox_sysmmu_platform.runtime(qmp))
                for index, row in enumerate(native_s2mpu_layout()):
                    if row['base'] == 0x1abc0000:
                        path=f'/machine/exynos-s2mpu{index}'
                        vm.save(output/'display-s2mpu-runtime.json',{'hcb':row,'path':path,**{
                            key:qmp.execute('qom-get',{'path':path,'property':key})
                            for key in ('irq-enable','irq-status','irq-clear-writes','irq-clear-last-mask')}})
                        break
                else:
                    raise ValueError('Missing original DPUS0 S2MPU')
            if sysmmu:
                vm.save(output/'sysmmu-runtime.json',sysmmu_platform.runtime_state(qmp))
            if vm3_sysmmu:
                vm.save(output/'linux-sysmmu-runtime.json',
                        sysmmu_platform.runtime_state(qmp,sysmmu_platform.LINUX_VIDS))
            if eqos:
                vm.save(output/'eqos-runtime.json',eqos_platform.runtime_state(qmp))
            if native_ufs:
                if ufs_phy_isolation:
                    vm.save(output/'ufs-phy-isolation-runtime.json',{
                        key:qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':key})
                        for key in ('ufs0-phy-isolation','ufs0-phy-reads','ufs0-phy-writes')})
                if ufs_shareability:
                    vm.save(output/'ufs-shareability-runtime.json',{
                        key:qmp.execute('qom-get',{'path':'/machine/exynos-ufs0','property':key})
                        for key in ('shareability','shareability-reads','shareability-writes')})
                if lk_stage_result is not None:
                    final=lk_runtime.device_snapshot(qmp)
                    vm.save(output/'ufs-since-lk-stage.json',{
                        'after_preboot':lk_stage_result['devices_after'],'after_system_observation':final,
                        'runtime_ufs_delta':lk_runtime.counter_delta(lk_stage_result['devices_after'],final),
                        'scope':'Actual device counters exclude all preboot loader/original LK DMA; no OEM disk mount or boot claim'})
                if ufs_descriptor_profile:
                    vm.save(output/'ufs-descriptor-state.json',{'samsung-byte-granularity':qmp.execute('qom-get',
                        {'path':'/machine/exynos-ufs0/controller','property':'x-samsung-byte-granularity'}),
                        'scope':'Actual optional engine mode; not LK execution or native storage DMA proof'})
                if ufs_uic_trace:
                    vgen=output/'ufs-runtime-vgen.bin'
                    qmp.execute('pmemsave',{'val':0x17dd0000,'size':0x20,'filename':str(vgen)})
                    vm.save(output/'ufs-runtime-vgen.json',{'physical':'0x17dd0000',
                        'words':list(struct.unpack('<8I',vgen.read_bytes())), 'guest_state_modified':False})
                if ufs_prelink:
                    vm.save(output/'ufs-prelink-runtime.json',ufs_platform.prelink_runtime(qmp))
                if ufs_power:
                    vm.save(output/'ufs-power-runtime.json',ufs_platform.power_runtime(qmp))
                capture=output/'ufs-standard-hci.bin'
                qmp.execute('pmemsave',{'val':0x17e00000,'size':0x100,'filename':str(capture)})
                if ufs_host0:
                    vm.save(output/'ufs-host0-runtime.json',ufs_platform.host0_runtime(qmp))
                if mailbox_policy:
                    vm.save(output/'ufs-mailbox-runtime.json',ufs_mailbox_platform.runtime(qmp))
                if vh_policy:
                    vm.save(output/'ufs-vh-runtime.json',ufs_vh_platform.runtime(qmp,output,ufs_disk["base"],android_ufs_overlay,hmi_disk,bool(linux_data)))
                if ufs_uic_trace:
                    slot=ufs_platform.observe_slot0(qmp,output,capture.read_bytes(),bool(ufs_descriptor_profile),
                                                    128 if ufs_prdt else 16)
                    vm.save(output/'ufs-runtime-slot0.json',slot)
                    if ufs_disk is not None:
                        vm.save(output/'ufs-s2mpu-runtime.json',ufs_platform.observe_s2mpu(qmp,output,slot))
                        blockstats=[r for r in qmp.execute('query-blockstats')
                                    if r.get('node-name')=='ufs-oem-lun0' or 'auto9-ufs0' in (r.get('qdev') or '')]
                        vm.save(output/'ufs-lun0-blockstats.json',blockstats)
                        if boot_luns:
                            names={f'ufs-oem-boot-lun{i}' for i in boot_luns}
                            vm.save(output/'ufs-boot-luns-blockstats.json',[
                                row for row in qmp.execute('query-blockstats') if row.get('node-name') in names])
                        if ufs_prdt:
                            other=ufs_platform.observe_slot0(qmp,output,capture.read_bytes(),True,128,1)
                            vm.save(output/'ufs-runtime-slot1.json',other)
                            vm.save(output/'ufs-runtime-read-dma-checks.json',[
                                ufs_platform.verify_completed_read(qmp,output,ufs_disk['base'],r)
                                for r in (slot,other)])
                vm.save(output/'ufs-native-state.json',{'standard_hci_source':str(capture),'sha256':vm.sha(capture),
                    'qom':{name:qmp.execute('qom-get',{'path':'/machine/exynos-ufs0','property':name})
                           for name in ('transport-enabled','irq-level','dma-reads','dma-writes','dma-rejected',
                               'vendor-rejected','software-resets','device-reset-assertions','device-reset-releases','reset-pulse-ns')},
                    'oem_ufs_driver_started_verified':False})
            qmp.execute('pmemsave',{'val':0x80000000,'size':0x480000,'filename':str(output/'hypervisor-ram.bin')})
            if map_pram:qmp.execute('pmemsave',{'val':pram_base,'size':pram_size,'filename':str(output/'exynos-pram.bin')})
            if board_adapter:
                capture=output/'acpm-shared-sram.bin'
                qmp.execute('pmemsave',{'val':0x02039000,'size':0x38000,'filename':str(capture)})
                content=capture.read_bytes()
                mailbox=output/'apm-shared-registers.bin'
                qmp.execute('pmemsave',{'val':0x10500080,'size':16,'filename':str(mailbox)})
                vm.save(output/'apm-shared-registers.json',{'registers':{hex(0x10500080+4*i):hex(value)
                    for i,value in enumerate(struct.unpack('<4I',mailbox.read_bytes()))},
                    'source':str(mailbox),'sha256':vm.sha(mailbox)})
                mailbox_path='/machine/exynos-apm-mailbox'
                mailbox_properties={row['name'] for row in qmp.execute('qom-list',{'path':mailbox_path})}
                mailbox_keys=tuple(f'bank{bank}-{name}' for bank in range(2) for name in (
                    'pending','mask','generated','cleared','irq-rises','mask-writes',
                    'last-generate','last-clear','irq-level'))
                mailbox_keys+=tuple(name for name in ('acpm-pulses','acpm-pulse-bits') if name in mailbox_properties)
                if set(mailbox_keys)<=mailbox_properties:
                    vm.save(output/'apm-mailbox-runtime.json',{key:qmp.execute('qom-get',{
                        'path':mailbox_path,'property':key}) for key in mailbox_keys})
                vm.save(output/'acpm-runtime-state.json',{'source':str(capture),'sha256':vm.sha(capture),
                    'physical_base':'0x02039000','bytes':len(content),'header_at_0x7000_hex':content[0x7000:0x7040].hex(),
                    'channel_count':struct.unpack_from('<I',content,0x7018)[0],
                    'acpm_firmware_running_verified':False})
                if acpm_clock_hz:
                    from acpm_firmware import public_metadata
                    vm.save(output/'acpm-runtime-metadata.json',public_metadata(content))
                    pdma={key:qmp.execute('qom-get',{'path':'/machine/exynos-pdma','property':key})
                          for key in ('wrapper-reads','wrapper-writes','rejected-accesses')}
                    pdma['scope']='Counters before host readback; actual original CPU accesses to behavior-level 36-bit wrappers, not proof of native high DMA or full boot'
                    pdma['wrappers']=[]
                    for label,address,size in [('instruction',0x1b2e4500,4)]+[(f'channel{i}',0x1b2e4400+32*i,8) for i in range(8)]:
                        path=output/f'pdma-{label}-ap-readback.bin'
                        qmp.execute('pmemsave',{'val':address,'size':size,'filename':str(path)})
                        pdma['wrappers'].append({'name':label,'address':hex(address),'source':str(path),'sha256':vm.sha(path),
                            'values':[v[0] for v in struct.iter_unpack('<I',path.read_bytes())]})
                    vm.save(output/'pdma-runtime-state.json',pdma)
                    i2c={key:qmp.execute('qom-get',{'path':'/machine/exynos-i2c17','property':key}) for key in
                         ('selector','virtual-byte-ns','selector-reads','selector-writes','register-reads','register-writes',
                          'rejected-accesses','transfers-started','transfers-completed','nacks','cancelled','software-resets','tx-bytes','rx-bytes',
                          'last-target','last-length','last-read','targets-low64','targets-high64')}
                    i2c.update(native_endpoint_attached=serializer_contract is not None,
                               scope='Source-bound virtual TI941 and TI983 control endpoints; no video/training backend' if ti983_contract else
                                     'Source-bound virtual TI941 control endpoint attached; no video/training backend' if serializer_contract else
                                     'Real virtual I2C17 state; no native screen-chip or sensor endpoint, no injected ACK/data')
                    vm.save(output/'i2c17-runtime-state.json',i2c)
                    if serializer_contract:
                        vm.save(output/'serializer-runtime-state.json',serializer_platform.runtime_state(qmp))
                    if ti983_contract:
                        vm.save(output/'ti983-runtime-state.json',ti983_platform.runtime_state(qmp))
                    usi09={key:qmp.execute('qom-get',{'path':'/machine/exynos-usi09','property':key}) for key in
                           ('selector','control','option','selector-reads','selector-writes','config-reads','config-writes','software-resets','rejected-accesses')}
                    usi09.update(scope='Observed UART personality, upstream real FIFO/chardev/IRQ364; secondary serial bytes separate from a Linux/Android shell or display acceptance')
                    vm.save(output/'usi09-runtime-state.json',usi09)
                    sci_path='/machine/exynos-sci'
                    fabric_reads=[]
                    for label,base,length in (('sci-control',0x1b000488,4),('vch0',0x1c251000,16),('vch1',0x1ce11000,8),('vch2',0x1b901000,4)):
                        path=output/(label+'-ap-readback.bin')
                        qmp.execute('pmemsave',{'val':base,'size':length,'filename':str(path)})
                        fabric_reads.append({'label':label,'physical_base':hex(base),'source':str(path),'sha256':vm.sha(path),
                            'words':[hex(v[0]) for v in struct.iter_unpack('<I',path.read_bytes())]})
                    speedy=[]
                    for controller in range(2):
                        path=f'/machine/exynos-speedy{controller}'
                        speedy.append({'controller':controller,'physical_base':hex(0x10560000+controller*0x10000),
                            **{key:qmp.execute('qom-get',{'path':path,'property':key}) for key in
                                ('commands-issued','unanswered-commands','last-command','last-tx-byte','interrupt-status','unanswered-command-latency-ns','virtual-interface-byte','interface-reads','interface-writes','virtual-bl2-rev0','virtual-revision-byte','virtual-bd-byte','virtual-reg47-byte','virtual-reg55-byte','profile-reads','profile-writes','virtual-pmu-input-byte','pmu-input-reads')}})
                        if native_parallel_tcg:
                                                                                       
                            speedy[-1]['status-poll-completions']=qmp.execute('qom-get',
                                {'path':path,'property':'status-poll-completions'})
                        speedy[-1]['controller_readback']=[]
                        for off in (0,0x10,0x30,0x48):
                            capture=output/f'speedy{controller}-{off:02x}-readback.bin'
                            qmp.execute('pmemsave',{'val':0x10560000+controller*0x10000+off,'size':4,'filename':str(capture)})
                            speedy[-1]['controller_readback'].append({'offset':hex(off),'value':hex(struct.unpack('<I',capture.read_bytes())[0]),'source':str(capture),'sha256':vm.sha(capture)})
                    for controller in speedy:
                        path=f"/machine/exynos-speedy{controller['controller']}"
                        controller['voltage_selectors']=[{'index':i,'value':qmp.execute('qom-get',{'path':path,'property':f'selector{i}-byte'})} for i in range(6)]
                    if fvmap is not None:
                        from bl2_fvmap import consumption_evidence
                        vm.save(output/'fvmap-consumption-evidence.json',consumption_evidence(
                            fvmap,content,speedy,profile['voltage_selectors']))
                    if apm_wdt_profile:
                        wdt={key:qmp.execute('qom-get',{'path':'/machine/exynos-apm-wdt','property':key}) for key in
                             ('profile','clock-hz','expiry-stop','current-count','control','reload','register-reads',
                              'register-writes','feeds','interrupt-clears','expirations','irq-pending','reset-pending',
                              'irq-assertions','reset-requests','rejected-accesses')}
                        wdt['scope']='Actual timed virtual watchdog; IRQ/reset request outputs do not prove private route/delivery'
                        vm.save(output/'apm-watchdog-runtime-state.json',wdt)
                    if debug_contract:
                        vm.save(output/'pmu-debug-runtime-state.json',pmu_debug.runtime_state(qmp))
                    if domain_contract:
                        vm.save(output/'pmu-domain-runtime-state.json',pmu_domain.runtime_state(qmp))
                    if pmu_profile:
                        pmu_state={key:qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':key})
                                   for key in ('register-writes','atomic-writes','rejected-accesses',
                                               'display-phy-reads','display-phy-writes','ufs-bootflag-reads','ufs-bootflag-writes',
                                               'ufs-cpu-reads','ufs-cpu-writes','ufs-access-dropped','ufs-access-first64',
                                               'display-phy-730','display-phy-738','display-phy-73c','display-phy-740','ufs-lk-bootflag',
                                               'timer-completions','timer-status','last-write-offset','last-write-value',
                                               'virtual-pmlink-v1','link-peer-connected','link-state','link-deliveries',
                                               'link-releases','link-cancelled','link-last-payload','link-last-route',
                                               'link-received0','link-received1','link-received-valid','link-history-first32','link-last-delivery-ns',
                                               'virtual-latched-signals-v1','signal-peer-connected','virtual-status1204','virtual-core-status-mask','signal-latched0','signal-latched1',
                                               'signal-state0','signal-state1','signal-commits','signal-releases',
                                               'signal-cancelled','signal-failed','signal-valid')}
                        if om_input:
                            pmu_state['om_input']={key:qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':key})
                                for key in ('om-input-active','om-input-value','om-input-reads','om-input-cpu-reads')}
                        pmu_state['registers']=[]
                        for off in (0x404,0x90c,0xb00,0xb20,0x1508,0x1520,0x1524,0x3a40,0xe34,0xe38,0xe3c):
                            path=output/f'pmu-{off:04x}-readback.bin'
                            qmp.execute('pmemsave',{'val':0x10460000+off,'size':4,'filename':str(path)})
                            pmu_state['registers'].append({'offset':hex(off),'value':hex(struct.unpack('<I',path.read_bytes())[0]),'source':str(path),'sha256':vm.sha(path)})
                        pmu_state['scope']='Explicit virtual cold profile; actual configuration and timer work only, not full power/IRQ/CPU sequencing'
                        vm.save(output/'pmu-runtime-state.json',pmu_state)
                    if tmu_profile:
                        state={key:qmp.execute('qom-get',{'path':'/machine/exynos-tmu','property':key}) for key in
                               ('temperature-c','sampled-temperature-c','sample-period-ns','initial-conversion-latency-ns',
                                'onepoint-reference-code','sampled-code','sample-valid','sample-completions','trim-reads','sample-reads','rejected-accesses')}
                        state['scope']='Declared virtual onepoint calibration/environment with actual periodic samples; not measured physical trim or temperature'
                        if tmu_profile=='virtual-qnx-controller-v1':
                            import qnx_tmu_platform
                            state.update(qnx_tmu_platform.runtime_state(qmp))
                        vm.save(output/'tmu-runtime-state.json',state)
                    cmu_path='/machine/exynos-cmu'
                    clock_state={key:qmp.execute('qom-get',{'path':cmu_path,'property':key}) for key in
                        ('reference-hz','locked-mask','lock-completions','register-writes')}
                    if load_qnx:
                        fields=('fsys1-gate-reads','fsys1-gate-writes','fsys1-gate-enabled-mask',
                                'fsys1-gate-read-mask','fsys1-gate-write-mask')
                        props={row['name'] for row in qmp.execute('qom-list',{'path':cmu_path})}
                        if all(key in props for key in fields):
                            vm.save(output/'fsys1-gate-runtime.json',{key:qmp.execute(
                                'qom-get',{'path':cmu_path,'property':key}) for key in fields})
                    pll_count=qmp.execute('qom-get',{'path':cmu_path,'property':'pll-count'}) if pscdc_profile else 13
                    clock_state['plls']=[{'index':i,'pll_hz':qmp.execute('qom-get',{'path':cmu_path,'property':f'pll{i}-hz'}),
                        'selected_hz':qmp.execute('qom-get',{'path':cmu_path,'property':f'shared{i}-hz'}),
                        'base':hex(qmp.execute('qom-get',{'path':cmu_path,'property':f'pll{i}-base'})),
                        'slot':qmp.execute('qom-get',{'path':cmu_path,'property':f'pll{i}-slot'}) if pscdc_profile else (i if i<5 else (i-5)%2)} for i in range(pll_count)]
                    clock_state['scope']='TOP/MIF and declared auxiliary PMS/signed-16K PLL outputs; no full clock tree, analog or physical reset claim'
                    topology=[]
                    for clock in ('core-bus-mux','core-bus-div-ungated','mif-switch-mux'):
                        topology.append({'clock':clock,
                            'hz':qmp.execute('qom-get',{'path':cmu_path,'property':clock+'-hz'}),
                            'qemu_period':qmp.execute('qom-get',{'path':cmu_path,'property':clock+'-period'})})
                    clock_state['pscdc_ungated_topology']=topology
                    clock_state['pscdc_ungated_registers']=[]
                    for offset in (0x1020,0x181c,0x109c,0x18b8,0x18bc,0x18c0,0x18c4,0x18cc,0x18d0,0x18d8):
                        path=output/f'cmu-topology-{offset:04x}-ap-readback.bin'
                        qmp.execute('pmemsave',{'val':0x1b240000+offset,'size':4,'filename':str(path)})
                        clock_state['pscdc_ungated_registers'].append({'offset':hex(offset),
                            'value':struct.unpack('<I',path.read_bytes())[0],'source':str(path),'sha256':vm.sha(path)})
                    clock_state['pscdc_topology_scope']='Actual parent Clock outputs; explicit behavioral PSCDC when selected, not silicon queue/gate/analog validation'
                    for i in range(pll_count):
                        path=output/f'cmu-pll{i}-ap-readback.bin'
                        slot=clock_state['plls'][i]['slot']
                        qmp.execute('pmemsave',{'val':int(clock_state['plls'][i]['base'],16)+0x10c+0x40*slot,'size':4,'filename':str(path)})
                        clock_state['plls'][i].update({'source':str(path),'sha256':vm.sha(path),'con3':hex(struct.unpack('<I',path.read_bytes())[0])})
                    queue_state={key:qmp.execute('qom-get',{'path':cmu_path,'property':key}) for key in
                        ('queue-control','mirror-control','queue-valid-mask','mirror-valid-mask',
                         'queue-rejected-starts','mirror-rejected-starts','last-queue-request','last-mirror-request')}
                    for label,base in (('queue',0x1b243c10),('mirror',0x1b243e10)):
                        path=output/(label+'-staged-words.bin')
                        qmp.execute('pmemsave',{'val':base,'size':32,'filename':str(path)})
                        words=struct.unpack('<8I',path.read_bytes())
                        properties=[qmp.execute('qom-get',{'path':cmu_path,'property':f'{label}-entry{i}'}) for i in range(8)]
                        queue_state[label]={'source':str(path),'sha256':vm.sha(path),
                            'qom_matches_actual_ap_readback':list(words)==properties,
                            'operands':cmu_queue_operands(words,queue_state[label+'-valid-mask'])}
                    queue_state.update(execution_supported=bool(pscdc_profile),completion_injected=False,
                        behavioral_profile=pscdc_profile,silicon_isa_verified=False,
                        scope='Actual virtual-profile execution when explicitly enabled; otherwise staging/rejected start. No recovered full Auto9 ISA claim.')
                    if pscdc_profile:
                        queue_state['virtual_execution']={key:qmp.execute('qom-get',{'path':cmu_path,'property':key}) for key in
                            ('pscdc-profile','pscdc-latency-cycles','queue-completions','mirror-completions',
                             'queue-cancellations','queue-clock-waits','memory-clock-pause','phase-barriers','phy-auto-gate-mask')}
                        queue_state['mif_clocks']=[{'bank':i,
                            'hz':qmp.execute('qom-get',{'path':cmu_path,'property':f'mif-ddrphy2x{i}-ungated-hz'}),
                            'qemu_period':qmp.execute('qom-get',{'path':cmu_path,'property':f'mif-ddrphy2x{i}-ungated-period'})} for i in range(4)]
                    vm.save(output/'cmu-queue-runtime-state.json',queue_state)
                    vm.save(output/'cmu-runtime-state.json',clock_state)
                    vm.save(output/'speedy-runtime-state.json',{'controllers':speedy,'pmic_slave_attached':False,'virtual_common_interface_attached':pmic_interface_reset_byte is not None,'virtual_bootstrap_profile':pmic_bootstrap_profile,
                        'scope':'Original code on bounded virtual controllers; optional explicit common-interface policy, NOT a full PMIC/regulator/physical IRQ model'})
                    vm.save(output/'sci-runtime-state.json',{
                        'sysreg_lane_config':qmp.execute('qom-get',{'path':sci_path,'property':'lane-config'}) if pscdc_profile else None,
                        'ap_readbacks':fabric_reads,
                        'invalidation_completions':qmp.execute('qom-get',{'path':sci_path,'property':'invalidation-completions'}),
                        'virtual_latency_ns':qmp.execute('qom-get',{'path':sci_path,'property':'invalidation-latency-ns'}),
                        'physical_base':'0x1b000000','mcu_alias':'0xab000000',
                        'scope':'Observed virtual SCI operation; bit semantics inferred from original code, no modeled LLC data lines'})
            for item in loaded:
                physical=0xb8000000 if item['guest']=='linux' else 0xa0000000
                capture=output/(item['guest']+'-boot-ram.bin')
                qmp.execute('pmemsave',{'val':physical,'size':64*1024*1024,'filename':str(capture)})
                records=vm.printk_records(capture.read_bytes())
                for record in records:
                    record['physical_address']=hex(physical+int(record['physical_address'],16)-0x40000000)
                vm.save(output/(item['guest']+'-boot-records.json'),{'source':str(capture),'physical_base':hex(physical),
                        'records':records,'record_count':len(records),'panic_observed':any('Kernel panic - not syncing:' in row['text'] for row in records)})
            if load_qnx or loaded:
                rsp=RSP(gdb_port)
                if acpm_clock_hz:rsp.attach_inferiors((3,))
                snapshot=rsp.system_snapshot()
                vm.save(output/'cpu-system-registers.json',snapshot)
                if acpm_clock_hz:
                    before_header=(output/'management-header-before-run.bin').read_bytes()
                    shared=(output/'acpm-shared-sram.bin').read_bytes()
                    management=[c for c in snapshot['cpus'] if c.get('architecture')=='arm-m']
                    initial=json.loads((output/'management-cpu-before-run.json').read_text(encoding='utf-8'))['cpus']
                    header_before=struct.unpack_from('<I',before_header,0x18)[0]
                    first_fault={name:qmp.execute('qom-get',{'path':'/machine/exynos-apm-core','property':name})
                                 for name in ('first-fault-address','first-fault-size','first-fault-write',
                                              'first-fault-pc','first-fault-sp','first-fault-lr','first-fault-r4','first-fault-r5')}
                    first_fault['scope']='Captured at first actual failing MMIO transaction before asynchronous debug stop/guest exception handler; no guest state modification'
                    vm.save(output/'acpm-first-mmio-fault.json',first_fault)
                    vm.save(output/'acpm-runtime-state.json',{
                        'management_halted':qmp.execute('qom-get',{'path':'/machine/exynos-apm-core','property':'management-halted'}),
                        'scope':'Actual architectural halt flag after observation; separate from global VM pause, not full ACPM boot acceptance'})
                    header_after=struct.unpack_from('<I',shared,0x7018)[0]
                    observed=(len(initial)==len(management)==1 and
                        initial[0]['core']['pc']=='0x3258' and initial[0]['core']['sp']=='0xb000' and
                        management[0]['core']['pc']!='0x3258' and header_before==0 and header_after==10)
                    ready_before=struct.unpack('<I',(output/'management-ready-before-run.bin').read_bytes())[0]
                    ready_after=struct.unpack_from('<I',(output/'apm-shared-registers.bin').read_bytes(),12)[0]
                    vm.save(output/'acpm-execution-evidence.json',{'original_code_execution_observed':observed,
                        'management_cpu':management,'initial_cpu':initial,'channel_count_before':header_before,
                        'channel_count_after':header_after,'runtime_header_prefilled':False,
                        'ready_marker_before':hex(ready_before),'ready_marker_after':hex(ready_after),
                        'original_ready_marker_observed':observed and ready_before==0 and ready_after==0x1234abcd,
                        'ready_value_injected':False,
                        'shared_sram_sha256':vm.sha(output/'acpm-shared-sram.bin'),
                        'qemu_cpu_count':len(snapshot['cpus']),'virtual_clock_hz':acpm_clock_hz,
                        'virtual_common_interface_policy_used':pmic_interface_reset_byte is not None,
                        'interface_reset_byte':pmic_interface_reset_byte,
                        'physical_pmic_reset_state_verified':False,'virtual_pmic_bootstrap_profile':pmic_bootstrap_profile,
                        'original_bl2_fvmap_virtual_profile':bl2_fvmap_profile,
                        'virtual_serializer_profile':serializer_profile,'virtual_pmu_debug_profile':pmu_dptx_debug_profile,'virtual_pmu_domain_profile':pmu_domain_profile,'virtual_pmu_profile':pmu_profile,'virtual_pmu_link_profile':pmu_link_profile,'virtual_pmu_signal_profile':pmu_signal_profile,'virtual_pmu_status1204':pmu_status1204,'virtual_pmu_core_status_mask':pmu_core_status_mask,'virtual_apm_watchdog_profile':apm_wdt_profile,'virtual_apm_watchdog_clock_hz':apm_wdt_clock_hz,'virtual_pmic_pmu_input_byte':pmic_pmu_input_byte,
                        'virtual_tmu_profile':tmu_profile,'virtual_tmu_temperature_c':tmu_temperature_c if tmu_profile else None,
                        'physical_clock_verified':False,'full_acpm_boot_verified':False,
                        'scope':'Actual management reset/vector/code and shared-memory mutation; not completed ACPM/three guests/UI'})
                rsp.close();rsp=None
                fault_targets=[]
                def read_physical_u64(address):
                    reply=qmp.execute('human-monitor-command',{'command-line':f'xp /1gx {address:#x}'})
                    match=re.search(r':\s+0x([0-9a-fA-F]+)',reply)
                    if not match:raise ValueError('Physical table read failed: '+reply)
                    return int(match[1],16)
                for cpu in snapshot['cpus']:
                    values=cpu['system'];far=int(values.get('FAR_EL1','0'),16)
                    if not far>>63:continue
                    row={'thread':cpu['thread'],'far':hex(far)}
                    try:row.update(walk_guest_4k(far,int(values['TCR_EL1'],16),int(values['TTBR1_EL1'],16),read_physical_u64))
                    except ValueError as error:row['unverified']=str(error)
                    fault_targets.append(row)
                vm.save(output/'live-guest-fault-translations.json',fault_targets)
                if symbol_directory:
                    captures=[]
                    for item in loaded:
                        symbol_file=symbol_directory/(item['guest']+'-symbols.json')
                        symbols=json.loads(symbol_file.read_text(encoding='utf-8'))
                        if symbols['sha256']!=item['kernel_sha256']:raise ValueError('Symbols belong to different original kernel')
                        lookup={s['name']:int(s['address'],16) for s in symbols['symbols']}
                        pointer_address=item['physical']+lookup['log_buf']-lookup['_text']
                        length_address=item['physical']+lookup['log_buf_len']-lookup['_text']
                        pointer=read_physical_u64(pointer_address)
                        length=read_physical_u64(length_address)&0xffffffff
                        if not 4096<=length<=16*1024*1024 or length&(length-1):raise ValueError('Invalid native printk length')
                                                                              
                                                                                
                        matching=[c for c in snapshot['cpus']
                                  if int(c['system'].get('VTTBR_EL2','0'),16)>>48==item['vmid']]
                                                                              
                                                                                 
                        cpu=next((c for c in matching
                                  if int(c['system'].get('TCR_EL1','0'),16)
                                  and int(c['system'].get('TTBR1_EL1','0'),16)),
                                 matching[0] if matching else None)
                        if cpu is None:
                                                                               
                                                                               
                                                                                
                            captures.append({'guest':item['guest'],'status':'not_active_at_capture',
                                'reason':'No actual EL2 VMID context after board stopped','vmid':item['vmid']})
                            continue
                        values=cpu['system']
                        if int(values['TCR_EL1'],16)==0 and 0xffffff8000000000<=pointer<0xffffffc000000000:
                                                                              
                                                                                
                                                                               
                            memstart=read_physical_u64(item['physical']+lookup['memstart_addr']-lookup['_text'])
                            physical=pointer-0xffffff8000000000+memstart
                            bank=high_bank if item['guest']=='linux' else None
                            backed=native_low_or_high_ram(physical,length,bank,second_bank if item['guest']=='linux' else None)
                            if item['guest']=='android':backed=backed or any(b<=physical and physical+length<=b+n for b,n in android_high_banks)
                            if not backed:
                                raise ValueError('Cleared-context ring outside backed native RAM')
                            translation={'physical_target':hex(physical),'method':'Native 39-bit linear map with captured memstart_addr after vCPU context cleared',
                                'memstart_addr':hex(memstart),'page_offset':'0xffffff8000000000'}
                        else:
                            translation=walk_guest_4k(pointer,int(values['TCR_EL1'],16),int(values['TTBR1_EL1'],16),read_physical_u64)
                        physical=int(translation['physical_target'],16)
                        capture=output/(item['guest']+'-printk-ram.bin')
                        qmp.execute('pmemsave',{'val':physical,'size':length,'filename':str(capture)})
                        raw_ring=capture.read_bytes()
                        def scalar(name,size):
                            addr=item['physical']+lookup[name]-lookup['_text']
                            return (read_physical_u64(addr&~7)>>((addr&7)*8))&((1<<(size*8))-1)
                        ring={}
                        try:
                            ring=dict(first=scalar('log_first_idx',4),next_index=scalar('log_next_idx',4),
                                      first_seq=scalar('log_first_seq',8),next_seq=scalar('log_next_seq',8))
                            layout=vm.original_printk_layout(item['kernel_sha256'])
                            records=vm.printk_live_ring(raw_ring,**ring,physical=physical,**layout)
                            ring.update(status='verified_live_ring',layout=layout)
                        except (ValueError,KeyError) as error:
                            ring.update(status='heuristic_fallback',reason=str(error))
                            records=vm.printk_records(raw_ring,max_records=65536)
                            for record in records:record['physical_address']=hex(physical+int(record['physical_address'],16)-0x40000000)
                        detail={'guest':item['guest'],'kernel_sha256':item['kernel_sha256'],'pointer_variable_physical':hex(pointer_address),
                            'buffer_virtual':hex(pointer),'buffer_physical':hex(physical),'bytes':length,'translation':translation,
                            'source_cpu_thread':cpu['thread'],
                            'records':records,'record_count':len(records),'ring':ring,
                            'panic_observed':any('Kernel panic - not syncing:' in r['text'] for r in records)}
                        extra=[]
                        for request in translate_address:
                            guest,address=request.split(':',1)
                            if guest!=item['guest']:continue
                            address=int(address,0)
                            root=item['physical']+lookup['swapper_pg_dir']-lookup['_text']
                            try:
                                requested=walk_guest_4k(address,int(symbols['observed_tcr_el1'],16),root,read_physical_u64)
                            except ValueError as exc:
                                                                              
                                                                               
                                requested={'translation_error':str(exc)}
                            extra.append({'requested_virtual':hex(address),'pgd_physical':hex(root),**requested})
                        detail['requested_translations']=extra
                        vm.save(output/(item['guest']+'-printk-records.json'),detail)
                        if android_initcall_trace and item['guest']=='android':
                            vm.save(output/'android-initcall-runtime.json',android_init_trace.summarize(records))
                        if linux_initcall_trace and item['guest']=='linux':
                            vm.save(output/'linux-initcall-runtime.json',android_init_trace.summarize(records))
                        if linux_pid1_context and item['guest']=='linux':
                            import linux_pid1_context as pid1
                            def read_ram(address):
                                if address%8 or not native_low_or_high_ram(address,8,high_bank,second_bank):
                                    raise ValueError(f'PID 1 read outside backed VM3 RAM: {address:#x}')
                                return read_physical_u64(address)
                            tcr=int(values['TCR_EL1'],16)
                            ttbr=int(values['TTBR1_EL1'],16)
                            mapping='live VM3 TTBR1'
                            if not (tcr and ttbr) and symbols['sha256']==pid1.LINUX_SHA:
                                                                               
                                                                                 
                                tcr=int(symbols['observed_tcr_el1'],16)
                                ttbr=item['physical']+lookup['swapper_pg_dir']-lookup['_text']
                                mapping='pinned OEM swapper_pg_dir after context clear'
                            def read_linux(address):
                                walk=walk_guest_4k(address,tcr,ttbr,read_ram)
                                return read_ram(int(walk['physical_target'],16))
                            from functools import lru_cache
                            @lru_cache(maxsize=8)
                            def user_root(pgd):
                                if not 0xffffff8000000000<=pgd<1<<64 or pgd&0xfff:
                                    raise ValueError('Unaligned original user pgd')
                                return int(walk_guest_4k(pgd,tcr,ttbr,read_ram)['physical_target'],16)
                            @lru_cache(maxsize=512)
                            def user_page(pgd,page):
                                return walk_user_4k(page,tcr,user_root(pgd),read_ram)
                            def read_linux_user(pgd,address):
                                if address&7:
                                    raise ValueError('Unaligned original user word')
                                return read_ram(user_page(pgd,address&~0xfff)+(address&0xfff))
                            try:
                                pid1_detail=pid1.capture(symbols,read_linux,read_user_u64=read_linux_user)
                                pid1_detail.update(mapping=mapping,ttbr1=hex(ttbr))
                            except ValueError as error:
                                pid1_detail={'status':'not_verified','reason':str(error)}
                            vm.save(output/'linux-pid1-context.json',pid1_detail)
                            if any(row['symbol'] in ('rtnetlink_rcv_msg','rtnl_lock') for row in pid1_detail.get('frames',[])):
                                from linux_rtnl_observer import capture as capture_rtnl
                                try:
                                    rtnl=capture_rtnl(symbols,read_linux,snapshot['cpus'])
                                except ValueError as error:
                                    rtnl={'status':'not_verified','reason':str(error)}
                                vm.save(output/'linux-rtnl-context.json',rtnl)
                            if linux_smack_observe:
                                from linux_smack_observer import capture
                                try:
                                    policy=capture(symbols,read_linux)
                                except ValueError as error:
                                    policy={'status':'not_verified','reason':str(error)}
                                vm.save(output/'linux-smack-runtime.json',policy)
                        if android_task_context and item['guest']=='android':
                            import android_task_context as android_tasks
                            from functools import lru_cache
                            @lru_cache(maxsize=8192)
                            def read_android_ram(address):
                                                                                           
                                if address%8 or not any(b<=address<=b+n-8 for b,n in ((0xa0000000,0x18000000),(0xe4c00000,0x1000000))+android_high_banks):
                                    raise ValueError(f'VM4 read outside source-verified backed banks: {address:#x}')
                                return read_physical_u64(address)
                            tcr=int(values['TCR_EL1'],16)
                            ttbr=int(values['TTBR1_EL1'],16)
                            def read_android(address):
                                walk=walk_guest_4k(address,tcr,ttbr,read_android_ram)
                                return read_android_ram(int(walk['physical_target'],16))
                            try:
                                if symbols['sha256']!=android_tasks.ANDROID_SHA:
                                    raise ValueError('Cannot recover context from a different Android Image')
                                mapping='live VM4 TTBR1'
                                try:
                                    read_android(lookup['init_task']+0x5a8)
                                except ValueError:
                                                                               
                                                                                      
                                    tcr=int(symbols['observed_tcr_el1'],16)
                                    ttbr=item['physical']+lookup['swapper_pg_dir']-lookup['_text']
                                    mapping='pinned OEM swapper_pg_dir; stopped VM4 RAM'
                                if offline_policy:
                                    from offline_android_waits import capture as capture_development_waits
                                    tasks=capture_development_waits(symbols,read_android,output)
                                else:
                                    tasks=android_tasks.capture(symbols,read_android)
                                tasks.update(mapping=mapping,ttbr1=hex(ttbr))
                            except (ValueError,KeyError) as error:
                                tasks={'status':'not_verified','reason':str(error)}
                            vm.save(output/'android-task-context.json',tasks)
                        captures.append({k:v for k,v in detail.items() if k!='records'})
                    vm.save(output/'dynamic-printk-captures.json',captures)
            if peer is not None:
                peer.shutdown()
                deadline=time.monotonic()+1
                while True:
                    final_peer=dsp_peer_snapshot(qmp)
                    if not final_peer['abox-core0-executing'] or time.monotonic()>=deadline:
                        break
                    time.sleep(.01)
                vm.save(output/'abox-dsp-peer-disconnect.json',final_peer)
                if final_peer['abox-core0-executing']:
                    raise RuntimeError('AP still reports DSP power after execution peer disconnected')
            if qnx_dsi:
                                                                                        
                try:
                    decon_irq=qnx_dsi_platform.decon_gic_runtime(qmp)
                except (OSError,RuntimeError,ValueError,KeyError,TypeError) as exc:
                    decon_irq=dict(status='not_verified',reason=str(exc))
                vm.save(output/'qnx-decon-gic.json',decon_irq)
            return {'initial':registers[0],'last_step':registers[-1],'cpu':info,'trace_bytes':(output/'tcg.log').stat().st_size}
        finally:
            if gl_runtime:gl_host.expected_disconnect=True
            if peer is not None:
                peer.closed.set()
            if rsp:rsp.close()
            if process.poll() is None:
                try:
                    if qmp:qmp.execute('quit')
                    process.wait(timeout=3)
                except (OSError,RuntimeError,subprocess.TimeoutExpired):
                    process.terminate();process.wait(timeout=3)
            if qmp:qmp.close()
            if ufs_disk is not None:
                st = Path(ufs_disk['base']).stat()
                same = [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns] == ufs_disk['stat']
                vm.save(output/'ufs-lun0-base-integrity.json',{'unchanged_stat':same,'base':ufs_disk['base']})
                if not same:
                    raise ValueError('Read-only UFS base changed')
            after=vm.sha(image)
            vm.save(output/'integrity.json',{'before':before,'after':after,'original_unchanged':before==after,'exit_code':process.returncode})


def board_check(output,qemu,acpm_clock_hz=0):

    executable=(qemu or vm.ROOT/'runtime/qemu-system-aarch64.exe').resolve()
    qmp_port,test_port=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt,virtualization=on,gic-version=2,x-exynos-vlm=on',
             '-cpu','cortex-a72','-accel','tcg','-smp','8','-m','128','-display','none',
             '-serial','null','-serial','null','-monitor','none','-nic','none','-S',
             '-device','loader,addr=0x40000000,data=0x14000000,data-len=4',
             '-device','loader,addr=0x40000000,cpu-num=0',
             '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
             '-qtest',f'tcp:127.0.0.1:{test_port},server=on,wait=off','-qtest-log',str(output/'qtest.log')]
    if acpm_clock_hz:
        if not 1000000<=acpm_clock_hz<=1000000000:raise ValueError('ACPM virtual clock outside model bounds')
        command[2]=command[2].replace('virt,','virt-8704-apm,',1)+f',x-exynos-acpm=on,x-exynos-acpm-hz={acpm_clock_hz}'
                                                                         
                                                                          
                                                                           
        command[6]='qtest'
        command[4]='cortex-a57'                                                                                 
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'scope':'Board model checks, no guest/UI acceptance'})
    environment=os.environ.copy()
    environment['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+environment.get('PATH','')
    qmp=connection=None
    transcript=[];checks=[]
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Board test QEMU exited; inspect qemu.log')
                try:qmp=vm.QMP(qmp_port);break
                except OSError:time.sleep(.1)
            if qmp is None:raise RuntimeError('Board check QMP did not become ready')
            connection=socket.create_connection(('127.0.0.1',test_port),timeout=5)
            stream=connection.makefile('rb')
            events=[]
            def test(command):
                connection.sendall(command.encode()+b'\n')
                while True:
                    line=stream.readline(4096).decode().strip()
                    if not line:raise OSError('QTest disconnected')
                    transcript.append({'command':command,'response':line})
                    if line.startswith('IRQ '):events.append(line);continue
                    if not line.startswith('OK'):raise ValueError('QTest: '+line)
                    return line
            def read(address):return int(test('readl '+hex(address)).split()[1],16)
            def write(address,value):test('writel '+hex(address)+' '+hex(value))
            def check(name,actual,expected):
                checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
                if actual!=expected:raise ValueError('Board check failed: '+name)
            cpus=qmp.execute('query-cpus-fast')
            management=[cpu for cpu in cpus if '/exynos-apm-core/' in cpu['qom-path']]
            affinities=[qmp.execute('qom-get',{'path':cpu['qom-path'],'property':'mp-affinity'}) for cpu in cpus if cpu not in management]
            check('Native eight-core MPIDRs',affinities,[0,0x100,0x200,0x300,0x10000,0x10100,0x10200,0x10300])
            if acpm_clock_hz:check('Separate management CPU count',len(management),1)
            children=qmp.execute('qom-list',{'path':'/machine/unattached'})
            gic=next(item['name'] for item in children if item['type']=='child<arm_gic>')
            test('irq_intercept_in /machine/unattached/'+gic)
            for number,base,banks,spi in ((0,0x10030000,2,459),(1,0x100a0000,1,457),(2,0x100b0000,2,455)):
                path=f'/machine/exynos-dintc{number}'
                for bank in range(banks):
                    regs=base+(0x280 if bank==0 else 0x2b0)
                    check(path+f' bank{bank} reset',read(regs+8),0)
                    test(f'set_irq_in {path} unnamed-gpio-in {bank*32+3} 1')
                    check(path+' pending before enable',read(regs+20),8)
                    check(path+' masked before enable',read(regs+16),0)
                    start=len(events);write(regs+8,8)
                    check(path+' enabled pending',read(regs+16),8)
                    check(path+' raises output','IRQ raise '+str(spi) in events[start:],True)
                    start=len(events);write(regs+12,8)
                    check(path+' masks output','IRQ lower '+str(spi) in events[start:],True)
                    test(f'set_irq_in {path} unnamed-gpio-in {bank*32+3} 0')
                    write(regs+20,8)
                    check(path+' clear after source deassertion',read(regs+20),0)
            for index,row in enumerate(native_vgen_layout()):
                base=row['base'];kind=row['layout'];label=f'VGEN{index} layout{kind}'
                if kind==0:
                    write(base,2);write(base+4,0x123);write(base,3);write(base+4,0x456)
                    write(base,2);check(label+' indexed context isolated',read(base+4),0x123)
                    write(base,3);check(label+' second indexed context',read(base+4),0x456)
                else:
                    offset=0x1004 if kind==3 else 0x50 if kind==4 else 0
                    write(base+offset,0x123);check(label+' context readback',read(base+offset),0x123)
            for index,row in enumerate(native_s2mpu_layout()):
                base=row['base'];label=f'S2MPU{index}'
                write(base,3);check(label+' enable control',read(base),3)
                write(base+0x20,8);check(label+' IRQ enable SET',read(base+0x28),8)
                write(base+0x24,8);check(label+' IRQ enable CLR',read(base+0x28),0)
                write(base+0x4200,0x123400);write(base+0x4204,6)
                check(label+' VMID1 L1 pointer',read(base+0x4200),0x123400)
                check(label+' VMID1 L1 permissions',read(base+0x4204),6)
            gpios=native_gpio_layout()+[native_alive_gpio_layout(),native_aud_gpio_layout()]
            for index,row in enumerate(gpios):
                base=row['base'];path=f'/machine/exynos-gpio{index}';label=f'GPIO{index}'
                write(base,1);write(base+4,1);check(label+' output latch',read(base+4)&1,1)
                write(base,0);test(f'set_irq_in {path} unnamed-gpio-in 0 0')
                check(label+' external low',read(base+4)&1,0)
                write(base+0x700,3);write(base,15);write(base+0xa00,1);write(base+0x900,0xfe)
                test(f'set_irq_in {path} unnamed-gpio-in 0 1')
                check(label+' rising edge pending',read(base+0xa00)&1,1)
                check(label+' service arbitration',read(base+0xb08),8)
                write(base+0xa00,1);check(label+' edge W1C',read(base+0xa00)&1,0)
            rtc=0x10540000
            write(rtc+0x38,8);write(rtc+0x44,0xffffffff);write(rtc+0x40,0x101)
            check('RTC selector readback',read(rtc+0x38),8)
            before=read(rtc+0x90);start=time.monotonic()
            if acpm_clock_hz:
                                                                           
                                                                        
                                                                            
                qmp.execute('cont')
                test('clock_step 250000000');elapsed=.25
            else:
                qmp.execute('cont');time.sleep(.25);qmp.execute('stop');elapsed=time.monotonic()-start
            after=read(rtc+0x90);delta=(after-before)&0xffffffff
            check('RTC QTest clock ascends' if acpm_clock_hz else 'RTC actual TCG clock ascends',0<delta<100000,True)
            check('RTC approximately 2048Hz (host timing tolerance)',0.5<delta/(2048*elapsed)<1.5,True)
            vm.save(output/'rtc-timing.json',{'before':before,'after':after,'delta':delta,'elapsed_seconds':elapsed,
                'scope':'Deterministic QTest virtual-clock step, not CPU execution' if acpm_clock_hz else 'Live guestless TCG clock check with broad host-scheduling tolerance; not an exact oscillator calibration'})
            check('ChipID product identifier',read(0x10000000),0xaaa80000)
            check('ChipID native product subfield',read(0x10000024),0)
                                                                             
                                                                            
            vgen=0x1b250000;s2mpu=0x1b2f0000;pdma=0x1b2e0000
            for slot in range(16):write(vgen,slot);write(vgen+4,3)
            write(s2mpu,3);write(s2mpu+0x4608,0);write(s2mpu+0x460c,6)
            source,destination,program=0x40001000,0x40002000,0x40003000
            code=b'\xbc\x00'+struct.pack('<I',source)+b'\xbc\x02'+struct.pack('<I',destination)+b'\xbc\x01'+struct.pack('<I',0x14005)+b'\x04\x08\x00'
            test(f'write {program:#x} {len(code):#x} 0x'+code.hex())
            write(source,0x89abcdef);write(destination,0)
            write(pdma+0xd08,0xa0<<16);write(pdma+0xd0c,program);write(pdma+0xd04,0)
            time.sleep(.02)
            check('Real PL330 transfers through VGEN/S2MPU',read(destination),0x89abcdef)
            write(destination,0);write(s2mpu+0x460c,2)
            write(s2mpu+0x1000,1)
            write(pdma+0xd04,0);time.sleep(.02)
            check('S2MPU denies actual PDMA destination write',read(destination),0)
            check('S2MPU records actual VID3 write fault',read(s2mpu+0x2000)&8,8)
            mailbox=0x10500000
            check('APM receive reset mask',read(mailbox+0x24),0xffff)
            test('set_irq_in /machine/exynos-apm-mailbox unnamed-gpio-in 16 1')
            check('APM real producer latches RX status',read(mailbox+0x28),1)
            check('APM masked RX status',read(mailbox+0x2c),0)
            start=len(events);write(mailbox+0x24,0xfffe)
            check('APM IRQ23 after unmask','IRQ raise 23' in events[start:],True)
            start=len(events);write(mailbox+0x20,1)
            check('APM receive W1C',read(mailbox+0x28),0)
            check('APM IRQ23 cleared','IRQ lower 23' in events[start:],True)
            write(mailbox+8,0x10000)
            check('APM transmitted message remains unacknowledged',read(mailbox+0x14),0x10000)
            check('APM does not invent remote response',read(mailbox+0x28),0)
            check('ACPM SRAM starts empty without firmware snapshot',read(0x02040018),0)
            write(mailbox+0x80,0x12345678)
            check('Mailbox shared register retains actual write',read(mailbox+0x80),0x12345678)
            if acpm_clock_hz:
                cmu=0x1b240000;cmu_path='/machine/exynos-cmu'
                def clock_property(name):return qmp.execute('qom-get',{'path':cmu_path,'property':name})
                check('CMU PLL reset mask',clock_property('locked-mask'),0)
                check('CMU actual reference bypass',clock_property('shared0-hz'),26000000)
                check('CORE_BUS reset follows real bypass, no assumed fixed /2',clock_property('core-bus-div-ungated-hz'),26000000)
                check('MIF_SWITCH reset raw PLL parent is stopped',clock_property('mif-switch-mux-hz'),0)
                for base,offset in ((0x1bf00000,0x600),(0x1b200000,0x600),(0x1d020000,0x610)):
                    write(base+offset,0x10)
                    check('CMU port selection retains '+hex(base),read(base+offset),0x10)
                before_count=clock_property('register-writes')
                write(0x1bf01900,0x12345678)
                check('CMU unsupported domain register cannot create state',clock_property('register-writes'),before_count)
                write(cmu+0x18e0,0x12345678)
                check('CMU undeclared TOP divider offset is not RAM',clock_property('register-writes'),before_count)
                write(cmu+0x10c,0x20000000)
                check('CMU lock bit is not guest-writable',read(cmu+0x10c)&0x20000000,0)
                write(cmu,600);write(cmu+0x100,0x10);write(cmu+0x10c,0x81480400)
                check('CMU no clock output before lock',clock_property('pll0-hz'),0)
                check('CMU selected unlocked PLL is stopped',clock_property('shared0-hz'),0)
                check('CMU read cannot create lock',read(cmu+0x10c)&0x20000000,0)
                test('clock_step 23076')
                check('CMU lock not before programmed reference cycles',clock_property('locked-mask'),0)
                test('clock_step 1')
                check('CMU PLL lock at virtual deadline',clock_property('locked-mask'),1)
                check('CMU rate is calculated from programmed M/P/S',clock_property('pll0-hz'),2132000000)
                check('CMU selected output follows PLL',clock_property('shared0-hz'),2132000000)
                check('CORE_BUS follows actual PLL lock event',clock_property('core-bus-div-ungated-hz'),2132000000)
                period=qmp.execute('qom-get',{'path':cmu_path+'/pll0','property':'qtest-clock-period'})
                check('CMU actual QEMU clock period, not only a shadow rate',period,(1000000000<<32)//2132000000)
                write(cmu+0x100,0)
                check('CMU bypass switches to real reference',clock_property('shared0-hz'),26000000)
                check('CORE_BUS follows actual bypass event',clock_property('core-bus-div-ungated-hz'),26000000)
                check('MIF_SWITCH direct fout parent does not incorrectly follow bypass',clock_property('mif-switch-mux-hz'),2132000000)
                write(cmu+0x100,0x10);write(cmu+0x10c,0x812c0400)
                check('CMU retune drops previous lock',clock_property('locked-mask'),0)
                check('CMU retune withdraws old output',clock_property('pll0-hz'),0)
                check('CORE_BUS withdraws retuned source',clock_property('core-bus-div-ungated-hz'),0)
                test('clock_step 23077')
                check('CMU retune has genuinely different rate',clock_property('pll0-hz'),1950000000)
                write(cmu+0x10c,0x012c0400)
                check('CMU disable cancels lock',clock_property('locked-mask'),0)
                check('CMU disable stops output',clock_property('pll0-hz'),0)
                before_count=clock_property('lock-completions')
                write(cmu+0x10c,0x812c0400);write(cmu+0x10c,0x012c0400)
                test('clock_step 23077')
                check('CMU canceled request never completes',clock_property('lock-completions'),before_count)
                write(cmu+0x10c,0x81480000)                                               
                test('clock_step 23077')
                check('CMU invalid P cannot manufacture lock',clock_property('locked-mask'),0)
                configs=(0x81480400,0x811f0400,0x80f60400,0x81f40d00,0x80cd0400)
                rates=(2132000000,1865500000,1599000000,1000000000,1332500000)
                for i,config in enumerate(configs):
                    write(cmu+4*i,1950 if i==3 else 600)
                    write(cmu+0x100+0x40*i,0x10)
                    write(cmu+0x10c+0x40*i,config)
                test('clock_step 23077')
                check('CMU four short-deadline PLLs complete independently',clock_property('locked-mask'),0x17)
                test('clock_step 51923')
                check('CMU longer PLL3 deadline completes independently',clock_property('locked-mask'),0x1f)
                for i,rate in enumerate(rates):
                    check(f'CMU PLL{i} programmed output',clock_property(f'pll{i}-hz'),rate)
                    expected_period=(1000000000<<32)//rate
                    for clock in (f'pll{i}',f'shared{i}'):
                        period=qmp.execute('qom-get',{'path':cmu_path+'/'+clock,'property':'qtest-clock-period'})
                        check('CMU actual QEMU clock '+clock,period,expected_period)
                topology_offsets=(0x1020,0x181c,0x109c,0x18b8,0x18bc,0x18c0,0x18c4,0x18cc,0x18d0,0x18d8)
                saved_topology={offset:read(cmu+offset) for offset in topology_offsets}
                for offset,value in ((0x18b8,1),(0x18bc,2),(0x18c0,1),(0x18c4,2),(0x18cc,1),(0x18d0,2),(0x18d8,1)):
                    write(cmu+offset,value)
                core_parents=(rates[0]//2,rates[1]//2,rates[2]//2,rates[0]//3,
                              rates[4]//2,rates[1]//3,rates[2]//3,rates[3])
                mif_parents=(rates[0],rates[1],rates[2],rates[4],rates[0]//2,rates[1]//2,rates[2]//2,rates[3])
                write(cmu+0x181c,3)
                for selection in range(8):
                    write(cmu+0x1020,selection);write(cmu+0x109c,selection)
                    check(f'CORE_BUS exact Auto9 parent{selection}',clock_property('core-bus-mux-hz'),core_parents[selection])
                    check(f'CORE_BUS parent{selection} real DIV ratio+1',clock_property('core-bus-div-ungated-hz'),core_parents[selection]//4)
                    check(f'MIF_SWITCH exact Auto9 parent{selection}',clock_property('mif-switch-mux-hz'),mif_parents[selection])
                    for clock,hz in (('core-bus-mux',core_parents[selection]),
                                     ('core-bus-div-ungated',core_parents[selection]//4),
                                     ('mif-switch-mux',mif_parents[selection])):
                        period=qmp.execute('qom-get',{'path':cmu_path+'/'+clock,'property':'qtest-clock-period'})
                        check(f'Actual {clock} parent{selection} QEMU Clock',period,(1000000000<<32)//hz)
                        check(f'Native getter reads actual {clock} parent{selection} Clock',clock_property(clock+'-period'),period)
                write(cmu+0x1020,0);write(cmu+0x109c,4);write(cmu+0x18b8,0)
                check('Named shared0_div2 is programmable, not hard-coded /2',clock_property('core-bus-mux-hz'),rates[0])
                check('Shared divider change propagates into MIF_SWITCH',clock_property('mif-switch-mux-hz'),rates[0])
                write(cmu+0x181c,15)
                check('CORE_BUS divider uses low four bits',clock_property('core-bus-div-ungated-hz'),rates[0]//16)
                for offset,value in saved_topology.items():write(cmu+offset,value)
                                                                          
                                                                             
                                                                      
                mif_bases=(0x1b400000,0x1b600000,0x1c400000,0x1c600000)
                for bank,base in enumerate(mif_bases):
                    for slot,(cycles,config) in enumerate(((600,0x81480400),(750,0x813c0505))):
                        index=5+2*bank+slot
                        check(f'MIF PLL{index} physical bank',clock_property(f'pll{index}-base'),base)
                        check(f'MIF PLL{index} starts unlocked',read(base+0x10c+0x40*slot),0)
                        write(base+4*slot,cycles);write(base+0x100+0x40*slot,0x10)
                        write(base+0x10c+0x40*slot,config|0x20000000)
                        check(f'MIF PLL{index} lock cannot be written',read(base+0x10c+0x40*slot)&0x20000000,0)
                        check(f'MIF PLL{index} no output before deadline',clock_property(f'pll{index}-hz'),0)
                    before_writes=clock_property('register-writes')
                    write(base+0x18c,0x81480400)
                    check(f'MIF bank{bank} absent third PLL rejects write',clock_property('register-writes'),before_writes)
                    write(base+0x610,0x10);write(base+0x850,1)
                    check(f'MIF bank{bank} observed mux config',read(base+0x610),0x10)
                    check(f'MIF bank{bank} observed gating config',read(base+0x850),1)
                test('clock_step 23076')
                check('MIF no lock before shortest real deadline',clock_property('locked-mask'),31)
                test('clock_step 1')
                check('MIF four primary PLLs lock independently',clock_property('locked-mask'),2751)
                test('clock_step 5769')
                check('MIF secondary PLLs remain pending before deadline',clock_property('locked-mask'),2751)
                test('clock_step 1')
                check('MIF all eight plus five TOP PLLs lock',clock_property('locked-mask'),8191)
                for bank,base in enumerate(mif_bases):
                    for slot,rate in enumerate((2132000000,51350000)):
                        index=5+2*bank+slot
                        check(f'MIF PLL{index} calculated output rate',clock_property(f'pll{index}-hz'),rate)
                        check(f'MIF PLL{index} selected output rate',clock_property(f'shared{index}-hz'),rate)
                        for clock in (f'pll{index}',f'shared{index}'):
                            period=qmp.execute('qom-get',{'path':cmu_path+'/'+clock,'property':'qtest-clock-period'})
                            check('MIF actual QEMU clock '+clock,period,(1000000000<<32)//rate)
                base=mif_bases[0]
                write(base+0x100,0)
                check('MIF PLL5 actual bypass',clock_property('shared5-hz'),26000000)
                check('MIF bank0 PLL1 unaffected by neighbor bypass',clock_property('shared6-hz'),51350000)
                write(base+0x100,0x10);write(base+0x10c,0x812c0400)
                check('MIF retune withdraws only its old lock',clock_property('locked-mask'),8159)
                check('MIF retune stops only its output',clock_property('pll5-hz'),0)
                check('MIF retune leaves TOP PLL0 running',clock_property('pll0-hz'),2132000000)
                test('clock_step 23077')
                check('MIF retune applies new rate',clock_property('pll5-hz'),1950000000)
                write(base+0x10c,0)
                check('MIF disable stops actual output',clock_property('pll5-hz'),0)
                before_count=clock_property('lock-completions')
                write(base+0x10c,0x812c0400);write(base+0x10c,0)
                test('clock_step 23077')
                check('MIF cancel cannot complete old deadline',clock_property('lock-completions'),before_count)
                write(base+0x10c,0x81480000)
                test('clock_step 23077')
                check('MIF invalid P cannot lock',clock_property('locked-mask'),8159)
                check('MIF invalid P cannot start output',clock_property('pll5-hz'),0)
                staged=(0,0x10000,0,0x12001020,0x1200181c,0x80040000,0x1207109c,0x80030000)
                write(cmu+0x3c00,0)
                for i,value in enumerate(staged):
                    write(cmu+0x3c10+4*i,value)
                    check(f'CMU queue stages actual entry{i}',read(cmu+0x3c10+4*i),value)
                    check(f'CMU queue entry{i} matches device state',clock_property(f'queue-entry{i}'),value)
                write(cmu+0x3e10,0x12300600);write(cmu+0x3e14,0x10000a08)
                write(cmu+0x3e00,0x10)
                check('CMU queue records explicit zero entries as written',clock_property('queue-valid-mask'),255)
                check('CMU mirror has only two actual programmed entries',clock_property('mirror-valid-mask'),3)
                check('CMU mirror arm configuration retained',read(cmu+0x3e00),16)
                check('CMU mirror arm alone creates no work',read(cmu+0x3e30),0)
                before_writes=clock_property('register-writes')
                write(cmu+0x3e30,0xffffffff);write(cmu+0x3c30,0x12345678)
                write(0x1b403c10,0x12345678)
                check('CMU queue status/hole/nonTOP writes reject',clock_property('register-writes'),before_writes)
                write(cmu+0x1020,0x12345678)
                before_locks=clock_property('lock-completions')
                write(cmu+0x3c00,0x271)
                check('CMU unsupported queue start counted as rejected',clock_property('queue-rejected-starts'),1)
                check('CMU actual queue start operand retained as observation',clock_property('last-queue-request'),0x271)
                check('CMU rejected start cannot create busy or completion',read(cmu+0x3c00),0)
                test('clock_step 1000000')
                check('CMU no timer can falsely apply rejected queue',read(cmu+0x1020),0x12345678)
                check('CMU rejected queue changes no PLL lock state',clock_property('lock-completions'),before_locks)
                write(cmu+0x3e00,0x11)
                check('CMU explicit unsupported mirror start rejected',clock_property('mirror-rejected-starts'),1)
                check('CMU mirror rejection retains prior arm configuration',read(cmu+0x3e00),16)
                check('CMU mirror rejected work is not falsely queued',read(cmu+0x3e30),0)
                sci=0x1b000000
                check('SCI idle reset',read(sci+0x488),0)
                write(sci+0x488,4)
                check('SCI busy state is not directly guest-writable',read(sci+0x488),0)
                write(0x40003000,0x1234abcd)
                for latency in (0,1000000001):
                    qmp.execute('qom-set',{'path':'/machine/exynos-sci','property':'invalidation-latency-ns','value':latency})
                    write(sci+0x488,1)
                    check('SCI invalid latency rejects operation '+str(latency),read(sci+0x488)&4,0)
                qmp.execute('qom-set',{'path':'/machine/exynos-sci','property':'invalidation-latency-ns','value':1000})
                write(sci+0x488,1)
                check('SCI command queues actual busy state',read(sci+0x488)&4,4)
                check('SCI repeated read does not complete work',read(sci+0x488)&4,4)
                test('clock_step 999')
                check('SCI does not complete before its virtual deadline',read(sci+0x488)&4,4)
                write(sci+0x488,0)
                check('SCI clearing control cannot clear in-flight work',read(sci+0x488)&4,4)
                test('clock_step 1')
                check('SCI virtual invalidation finishes',read(sci+0x488)&4,0)
                check('SCI completion count is driven by timer',qmp.execute('qom-get',{'path':'/machine/exynos-sci','property':'invalidation-completions'}),1)
                check('SCI invalidation does not erase backing RAM',read(0x40003000),0x1234abcd)
                for base,values in ((0x1c251000,(0x22222212,0x222222,0x11133000,0x331)),(0x1ce11000,(0x21,0x100000)),(0x1b901000,(0,))):
                    for i,value in enumerate(values):
                        write(base+4*i,value);check('SCI VCH configuration retains '+hex(base+4*i),read(base+4*i),value)
                for number in range(2):
                    speedy=0x10560000+number*0x10000
                    path=f'/machine/exynos-speedy{number}'
                    label=f'SPEEDY{number} '
                    check(label+'empty FIFO',read(speedy+0x30),0x40)
                    write(speedy,1);write(speedy+4,0x101);write(speedy+0xc,0xffffffff)
                    write(speedy+0x10,0xffffffff)
                    check(label+'W1C cannot create status',read(speedy+0x10),0)
                    write(speedy+8,0x80800)                                    
                    check(label+'read does not auto-ACK',read(speedy+0x10),0)
                    test('clock_step 999')
                    check(label+'no early timeout',read(speedy+0x10),0)
                    test('clock_step 1')
                    check(label+'unanswered read produces timeout, not done',read(speedy+0x10),2)
                    write(speedy+0x10,2)
                    check(label+'timeout W1C',read(speedy+0x10),0)
                    write(speedy+8,0x180800)
                    test('clock_step 2000')
                    check(label+'write waits for actual TX byte',qmp.execute('qom-get',{'path':path,'property':'commands-issued'}),1)
                    write(speedy+0x34,0xa5)
                    test('clock_step 1000')
                    check(label+'write has no invented slave ACK',read(speedy+0x10),2)
                    check(label+'counts issued read and write',qmp.execute('qom-get',{'path':path,'property':'commands-issued'}),2)
                    check(label+'counts real virtual deadlines',qmp.execute('qom-get',{'path':path,'property':'unanswered-commands'}),2)
                    check(label+'captures actual transmitted data',qmp.execute('qom-get',{'path':path,'property':'last-tx-byte'}),0xa5)
                    write(speedy+0x10,0xffffffff)
                    write(speedy+8,0x80800)
                    write(speedy,0x80000001)
                    test('clock_step 1000')
                    check(label+'software reset cancels queued deadline',read(speedy+0x10),0)
                    check(label+'reset empties FIFO',read(speedy+0x30),0x40)
                for number in range(2):
                    speedy=0x10560000+number*0x10000;path=f'/machine/exynos-speedy{number}';label=f'Virtual interface{number} '
                    qmp.execute('qom-set',{'path':path,'property':'virtual-interface-byte','value':0})
                    write(speedy,1);write(speedy+4,0x101)
                    write(speedy+8,0x80800)
                    check(label+'not complete before endpoint operation',read(speedy+0x10),0)
                    test('clock_step 999')
                    check(label+'status poll one ns before deadline does not retire',read(speedy+0x10),0)
                    test('clock_step 1')
                    check(label+'known read completes',read(speedy+0x10),1)
                    check(label+'one actual RX byte',read(speedy+0x30),1)
                    count=qmp.execute('qom-get',{'path':path,'property':'commands-issued'})
                    write(speedy+8,0x80800)
                    check(label+'cannot overwrite unread result',qmp.execute('qom-get',{'path':path,'property':'commands-issued'}),count)
                    check(label+'explicit initial byte',read(speedy+0x38),0)
                    check(label+'FIFO pops actual byte',read(speedy+0x30),0x40)
                    write(speedy+0x10,1);write(speedy+8,0x180800);write(speedy+0x34,0x20)
                    check(label+'writes not applied before deadline',qmp.execute('qom-get',{'path':path,'property':'virtual-interface-byte'}),0)
                    test('clock_step 1000')
                    check(label+'actual endpoint write persists',qmp.execute('qom-get',{'path':path,'property':'virtual-interface-byte'}),0x20)
                    write(speedy+0x10,1);write(speedy,0x61);write(speedy+8,0x80800)
                    test('clock_step 1000')
                    check(label+'trailer and data queued separately',read(speedy+0x30),2)
                    check(label+'valid-frame trailer first',read(speedy+0x38),0)
                    check(label+'then actual current state, not constant',read(speedy+0x38),0x20)
                    write(speedy+0x10,1);write(speedy+8,0x180800)
                    qmp.execute('qom-set',{'path':path,'property':'response-timeout','value':1})
                    write(speedy+0x34,0xa0);test('clock_step 1000')
                    check(label+'missing response is not success',read(speedy+0x10),2)
                    check(label+'failed write does not mutate endpoint',qmp.execute('qom-get',{'path':path,'property':'virtual-interface-byte'}),0x20)
                    qmp.execute('qom-set',{'path':path,'property':'response-timeout','value':0})
                    write(speedy+0x10,2);write(speedy+8,0x80000);test('clock_step 1000')
                    check(label+'identity register remains unsupported',read(speedy+0x10),2)
                    check(label+'unsupported address has no RX bytes',read(speedy+0x30),0x40)
                    check(label+'counts actual supported reads',qmp.execute('qom-get',{'path':path,'property':'interface-reads'}),2)
                    check(label+'counts only successful writes',qmp.execute('qom-get',{'path':path,'property':'interface-writes'}),1)
                for number in range(2):
                    speedy=0x10560000+number*0x10000;path=f'/machine/exynos-speedy{number}'
                    label=f'BL2 sparse profile{number} '
                    qmp.execute('qom-set',{'path':path,'property':'virtual-bl2-rev0','value':1})
                    def profile_read(device,reg):
                        write(speedy+0x10,0xffffffff);write(speedy+4,0x80000101)
                        write(speedy+8,0x80000|(device<<15)|(reg<<7));test('clock_step 1000')
                        check(label+'read commits '+hex(device*256+reg),read(speedy+0x10),1)
                        check(label+'valid trailer',read(speedy+0x38),0)
                        return read(speedy+0x38)
                    def profile_write(device,reg,value,success=True):
                        write(speedy+0x10,0xffffffff);write(speedy+4,0x80000101)
                        write(speedy+8,0x180000|(device<<15)|(reg<<7));write(speedy+0x34,value)
                        test('clock_step 1000')
                        check(label+'write result '+hex(device*256+reg),read(speedy+0x10),1 if success else 2)
                        if success:check(label+'write trailer',read(speedy+0x38),0)
                        else:check(label+'failed write has no RX',read(speedy+0x30),0x40)
                    check(label+'selected virtual revision',profile_read(0,0),0)
                    profile_write(0,0,7,False)
                    check(label+'revision is read-only',profile_read(0,0),0)
                    check(label+'original BL2 BD write',profile_read(15,0xbd),0x16)
                    profile_write(15,0xbd,0x36)
                    check(label+'actual later write persists',profile_read(15,0xbd),0x36)
                    qmp.execute('qom-set',{'path':path,'property':'response-timeout','value':1})
                    profile_write(15,0xbd,0xff,False)
                    qmp.execute('qom-set',{'path':path,'property':'response-timeout','value':0})
                    check(label+'failed write cannot mutate seeded state',profile_read(15,0xbd),0x36)
                    check(label+'per-controller BL2 reg47',profile_read(1,0x47),0xfc if number==0 else 0x7c)
                    if number==0:check(label+'known original BL2 reg55',profile_read(1,0x55),0x7c)
                    else:
                        write(speedy+0x10,0xffffffff);write(speedy+4,0x80000101)
                        write(speedy+8,0x80000|(1<<15)|(0x55<<7));test('clock_step 1000')
                        check(label+'unproven controller1 reg55 remains unknown',read(speedy+0x10),2)
                    selector_regs=((0x4a,0x4c,0x4e,0x50,0x52,0x48),(0x48,0x4a,0x4c,0x4e,0x50,0x56))[number]
                    for i,reg in enumerate(selector_regs):
                        write(speedy+0x10,0xffffffff);write(speedy+4,0x80000101)
                        write(speedy+8,0x88000|(reg<<7));test('clock_step 1000')
                        check(label+'selector has no invented reset value '+hex(reg),read(speedy+0x10),2)
                        check(label+'unwritten selector marker '+hex(reg),qmp.execute('qom-get',{'path':path,'property':f'selector{i}-byte'}),256)
                        value=0x30+i+number
                        profile_write(1,reg,value)
                        check(label+'selector actual written code '+hex(reg),profile_read(1,reg),value)
                        qmp.execute('qom-set',{'path':path,'property':'response-timeout','value':1})
                        profile_write(1,reg,0xff,False)
                        qmp.execute('qom-set',{'path':path,'property':'response-timeout','value':0})
                        check(label+'failed selector write retains code '+hex(reg),profile_read(1,reg),value)
                    write(speedy+0x10,0xffffffff);write(speedy+4,0x80000101)
                    write(speedy+8,0x80000|0x80);test('clock_step 1000')
                    check(label+'unseeded address is not silently zero',read(speedy+0x10),2)
                counter=0x10430000
                write(counter+4,0xffff);write(counter+8,0xffff);write(counter,0x61)
                check('APM counter initial count',read(counter+8),0xffff)
                test('clock_step 100000')
                ticks=acpm_clock_hz//10000
                expected=(0xffff-ticks)%0x10000
                check('APM counter follows actual QEMU virtual clock',read(counter+8),expected)
                check('APM counter read does not advance time',read(counter+8),expected)
                write(counter,0x70)
                test('clock_step 100000')
                check('APM disabled counter stays stopped',read(counter+8),expected)
            return {'checks':checks,'passed':all(row['passed'] for row in checks),'guests_booted_verified':False}
        finally:
            vm.save(output/'qtest-transcript.json',transcript);vm.save(output/'checks.json',checks)
            if connection:connection.close()
            if process.poll() is None:
                if qmp:
                    try:qmp.execute('quit')
                    except OSError:pass
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
            if qmp:qmp.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('inventory','probe','inspect-session','board-check','prepare-lk-analysis','kernel-symbols','elf-window','arm64-refs','primecells','extract-ifs','coprocessors','mcu-candidate','acpm-candidate','ufs-layout'))
    parser.add_argument('--session',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=.5,help='Wall observation budget; captures earlier on actual VM stop, not verified guest CPU execution time')
    parser.add_argument('--extended-observation',action='store_true',
                        help=f'Explicitly allow a full OEM boot observation longer than {ROUTINE_OEM_SECONDS} wall seconds')
    parser.add_argument('--steps',type=int,default=32)
    parser.add_argument('--map-pram',action='store_true',help='Map the original ELF-configured Exynos persistent RAM as real cold-plugged NVDIMM-backed RAM')
    parser.add_argument('--hcb',action='store_true',help='Pass the hash-pinned original HCB at x0 as a bootloader-provided hardware configuration')
    parser.add_argument('--exynos-uart',action='store_true',help='Instantiate QEMU real Exynos UART model on the platform bus')
    parser.add_argument('--board-adapter',action='store_true',help='Use the separately built opt-in UART/GICv2 board layout')
    parser.add_argument('--qemu',type=Path)
    parser.add_argument('--cpus',type=int,default=1)
    parser.add_argument('--hcb-ram',action='store_true',help='Populate a separate HCB copy with the actual supplied 2GiB Exynos RAM bank; allocates 3GiB QEMU RAM')
    parser.add_argument('--payload-sized-qnx',action='store_true',help='Explicit derived HCB reservation sized from the verified QNX IFS; factory loader policy remains unverified')
    parser.add_argument('--guest-dtbs',action='store_true',help='Preload factory guest DTBs at the default original HCB boot-parameter addresses')
    parser.add_argument('--linux-factory-bank',action='store_true',
                        help='Back the original VM3 2 GiB high bank with real local RAM')
    parser.add_argument('--qnx-factory-bank',action='store_true',
                        help='Back the original VM2 0x4e000000-byte high bank with real local RAM')
    parser.add_argument('--loader-ram-mib',type=int,choices=(0,256,512,1024,2048),default=0,help='Extra real local loader-workspace RAM beyond factory guest banks; derived board configuration')
    parser.add_argument('--load-qnx',action='store_true',help='Load the verified original QNX IFS according to its own physical startup header')
    parser.add_argument('--load-linux',action='store_true',help='Load the verified original IVI Image at the native HCB kernel address')
    parser.add_argument('--load-android',action='store_true',help='Load unmodified kernel/ramdisks extracted from verified original Android boot images')
    parser.add_argument('--trace-mode',choices=('blocks','errors','boot'),default='blocks',
                        help='boot keeps CPU errors, UFS sector results and failures without per-request trace chatter')
    parser.add_argument('--rtc-bootstrap',action='store_true',help='Initialize the real Auto9 RTC ascending counter as a local virtual bootloader')
    parser.add_argument('--symbol-directory',type=Path,help='Hash-verified native kernel symbol reports for capturing the actual relocated printk ring')
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--address',type=lambda s:int(s,0),default=0)
    parser.add_argument('--length',type=int,default=128)
    parser.add_argument('--translate-address',action='append',default=[],help='Additional native virtual address to translate, guest:0xVA; uses saved initial kernel PGD after fatal context clearing')
    parser.add_argument('--member',action='append',default=[])
    parser.add_argument('--acpm-sram',type=Path,help='Exact external 0x38000-byte ACPM runtime SRAM snapshot; no synthetic channel tables')
    parser.add_argument('--acpm-clock-hz',type=int,default=0,help='Opt-in real QEMU management CPU with explicit virtual clock; 0 disables it, no prefilled runtime snapshot')
    parser.add_argument('--pscdc-profile',choices=('virtual-oem-v1',),help='Explicit behavior-level PSCDC model; original images unchanged, not recovered silicon ISA')
    parser.add_argument('--pscdc-latency-cycles',type=int,default=64,help='Virtual transaction latency in 26MHz reference cycles; not measured silicon timing')
    parser.add_argument('--pmic-bootstrap-profile',choices=('virtual-bl2-rev0',),help='Explicit virtual revision-zero policy with five hash-verified BL2 writes; not physical PMIC identity/full BL2 execution')
    parser.add_argument('--bl2-fvmap-profile',choices=('virtual-asv0',),help='Run pinned original BL2 parser/builder with explicit virtual group-zero fuses; validated output only, no physical calibration/full BL2 claim')
    parser.add_argument('--pmic-interface-reset-byte',type=lambda value:int(value,0),help='Explicit virtual device0/reg0x10 initial byte, not factory PMIC data; default absent slave')
    parser.add_argument('--pmic-pmu-input-byte',type=lambda value:int(value,0),help='Explicit virtual controller0/device1/reg0x0c read input; physical meaning/reset unknown, absent by default')
    parser.add_argument('--apm-wdt-profile',choices=('virtual-samsung32-v1',),help='Explicit virtual cold-disabled32bit watchdog; unknown private IRQ/reset routes stop on expiry')
    parser.add_argument('--apm-wdt-clock-hz',type=int,default=0,help='Explicit watchdog source clock1MHz..1GHz, not a measured silicon clock')
    parser.add_argument('--pmu-core-status-mask',type=int,default=0,help='Explicit8-bit input mask for original eight saved status literals; not physical CPU-power state')
    parser.add_argument('--pmu-status1204',type=int,choices=(0,1),default=0,help='Declared bit input saved by original FlexPMU, not physical reset or forced ACK')
    parser.add_argument('--pmu-signal-profile',choices=('virtual-latched-signals-v1',),help='Explicit virtual shadow/output-latch hypothesis; actual timed GPIO outputs, no physical CPU/rail mapping')
    parser.add_argument('--pmu-link-profile',choices=('virtual-pmlink-v1',),help='Explicit timed virtual PMU message endpoint; unknown physical command effects, no HCU microcode claim')
    parser.add_argument('--pmu-om-input',type=lambda x:int(x,0),help='Explicit virtual read-only PMU word at 0x10460000 for original VM2, not a measured strap')
    parser.add_argument('--pmu-profile',choices=('virtual-cold-v1',),help='Explicit virtual cold inputs, bounded original PMU config/atomic ports and real timers; not physical reset/power sequencing')
    parser.add_argument('--icount-shift',type=int,help='Optional deterministic TCG virtual clock: 2**shift ns per instruction, shift0..10; not physical cycle accuracy')
    parser.add_argument('--lk-dsp-load',choices=('virtual-lk-dsp-ufs-read-v1',),
                        help='Use real native HCI READ DMA to verified HiFi4 AP windows; no DSP completion')
    parser.add_argument('--lk-handoff-profile',choices=('virtual-original-lk-ssu-v1',),help='Run original LK handoff/callees on native CPU with explicit initialized runtime, not full LK boot')
    parser.add_argument('--usi-spi-profile',choices=('virtual-spi1-v1','virtual-linux-spi-v1'),help='Source-bound SPI selector, timed FIFOs and IRQ/PL330; original VM3 HCB runs add its bounded SPI6 startup peer')
    parser.add_argument('--usi-i2c-profile',choices=('virtual-linux-i2c-v1',),help='Original Linux I2C1/5/9/19, real bus/FIFOs/IRQ; no OEM peer identities')
    parser.add_argument('--clock-rpc-watch',action='store_true',help='Read-only one-shot original I2C clock preparation breakpoint, resumes all guests automatically')
    parser.add_argument('--clock-backend-watch',action='store_true',help='Read-only QNX clock-library failure-branch observation; requires --clock-rpc-watch')
    parser.add_argument('--lk-pwm-profile',choices=('virtual-lk-pwm24-v1',),help='Opt-in unmodified upstream 24MHz PWM at original LK timer address; polling only')
    parser.add_argument('--linux-sysmmu',choices=('virtual-linux-sysmmu-v7-v1',),
                        help='Enable original VM3 SysMMU discovery and owner-bound root latches')
    parser.add_argument('--ufs-boot-luns',type=Path,help='Verified read-only archive-derived LUN1 and LUN2')
    parser.add_argument('--linux-initcall-trace',action='store_true',
                        help='Original Linux printk initcall trace via a diagnostic DT copy')
    parser.add_argument('--qnx-g3d00-jm-topology',action='store_true',
                        help='Virtual G3D00 job slot discovery and AS0 configuration, without job execution')
    parser.add_argument('--qnx-g3d00-control-jobs',action='store_true',
                        help='Execute independent QNX G76 control jobs via AS0; no shader or rendering')
    parser.add_argument('--qnx-g3d00',action='store_true',help='Enable distinct original QNX G76 bootstrap')
    parser.add_argument('--qnx-dsi',action='store_true',help='Enable original VM2 DSIM SYSREG and local link reset/config only')
    parser.add_argument('--qnx-bts',action='store_true',help='Enable original VM2 BTS DMC request-limit words')
    parser.add_argument('--linux-usb-phy',action='store_true',help='Enable original VM3 USB v3.1 HS register bank and guest-driven link reset')
    parser.add_argument('--linux-dwc3',action='store_true',help='Use upstream host DWC3 registers at original VM3 address, with DMA blocked')
    parser.add_argument('--qnx-phy88q2112',action='store_true',help='Attach a virtual 88Q211x management plane to original QNX EQoS; MDI disconnected')
    parser.add_argument('--linux-mfc',action='store_true',help='Enable original VM3 MFC cold reset without firmware or DMA')
    parser.add_argument('--qnx-dpp',action='store_true',help='Enable original VM2 DPP/IDMA interrupt words without DMA or scanout')
    parser.add_argument('--qnx-dp0-aux-sink',action='store_true',help='Connect an explicit virtual ABT sink to original VM2 DP0 HPD/AUX; does not generate frames')
    parser.add_argument('--qnx-dp0-irq',action='store_true',help='Enable DP0 plug status/mask and HPD configuration with a disconnected sink')
    parser.add_argument('--qnx-dp0-link-config',action='store_true',help='Retain the original VM2 DP0 local function and lane-map words; no link or sink')
    parser.add_argument('--qnx-dp0-phy',action='store_true',help='Enable the original DP0 PHY configuration and modeled local PLL handshake')
    parser.add_argument('--qnx-dp0-reset',action='store_true',help='Enable only the original VM2 DP0 LINK software-reset handshake')
    parser.add_argument('--qnx-audio-clock-watch',action='store_true',help='Bracket the original VM3 ABOX prepare with source-verified QNX clock observations')
    parser.add_argument('--android-abox-watch',action='store_true',
                        help='One-shot read-only original Android ABOX probe checkpoints')
    parser.add_argument('--android-initcall-trace',action='store_true',
                        help='Original printk initcall trace via a diagnostic DT copy')
    parser.add_argument('--abox-dsp-mailbox', action='store_true',
                        help='Connect native AP and Xtensa ABOX mailbox endpoints over local sockets')
    parser.add_argument('--abox-dsp-xtensa', type=Path,
                        help='Optional local QEMU Xtensa binary for the original DSP reset/lifecycle peer')
    parser.add_argument('--abox-dsp-off',choices=('virtual-abox-dsp-off-v1',),
                        help='Opt-in four read-only ABOX DSP status words; no DSP executes')
    parser.add_argument('--abox-axiuser',choices=('virtual-abox-axiuser-v1',),
                        help='Opt-in original ABOX shared AXIUSER config; no DSP or DMA')
    parser.add_argument('--abox-sysmmu',choices=('virtual-abox-dma-sysmmu-v1',),
                        help='Opt-in VM3 ABOX DMA SysMMU config latches; no DMA or IRQ')
    parser.add_argument('--abox-gic',choices=('virtual-abox-gic-v1',),
                        help='Opt-in original ABOX GICv2 register file, no DSP or audio IRQ wiring')
    parser.add_argument('--scaler-reset',choices=('virtual-linux-scaler-reset-v1','virtual-linux-scaler-poly-probe-v2'),
                        help='Opt-in original Linux Scaler reset, no jobs or scanout')
    parser.add_argument('--linux-decon',choices=('virtual-linux-wod-window-v1',),
                        help='Opt-in VM3 WOD window registers; no scanout')
    parser.add_argument('--linux-dpp',choices=('virtual-linux-dpp-irq-v1',),
                        help='Opt-in VM3 DPP and IDMA interrupt/configuration words; no scanout')
    parser.add_argument('--ufs-vh',choices=('virtual-ufs-vh-ro-v1',),
                        help='Opt-in isolated behavior-level VH HCI with read-only derived media')
    parser.add_argument('--ufs-mailbox',choices=('virtual-ufs-ph-vh-mailbox-v1',),
                        help='Route genuine PH writes to the two polled original UFS virtual host mailboxes')
    parser.add_argument('--g2d-reset',choices=('virtual-fimg2d-reset-v1',),
                        help='Enable FIMG2D reset/status only; no graphics execution')
    parser.add_argument('--linux-usi-uart',choices=('virtual-linux-usi-uart-v1',),
                        help='Enable original VM3 USI03/04/08 physical UART resources')
    parser.add_argument('--mali-g3d1-jm-topology',choices=('virtual-g76-jm-topology-v1','virtual-g76-vm3-control-as0-v1'),
                        help='Expose OEM scheduler slots; optional VM3 AS0 with bounded control, viewport and clear jobs')
    parser.add_argument('--mali-g3d1-empty-power',choices=('virtual-g76-empty-power-v1',),
                        help='Model idle power masks for the absent G76 execution resources')
    parser.add_argument('--mali-g3d1-config',choices=('virtual-g76-config-v1',),
                        help='Enable original Mali control-plane configuration latches, no GPU engines')
    parser.add_argument('--mali-g3d1-reset-irq',choices=('virtual-g76-reset-irq-v1',),
                        help='Enable bounded soft/hard reset and level IRQ banks, no jobs')
    parser.add_argument('--mali-g3d1-discovery',choices=('virtual-g76-feature-discovery-v1',),
                        help='Expose read-only feature discovery, no GPU engines or jobs')
    parser.add_argument('--mali-g3d1-id',type=lambda n:int(n,0),help='Explicit virtual G76 ID; no physical GPU revision or engine')
    parser.add_argument('--ufs-vgen-profile',choices=('virtual-ufs0-host0-v1',),help='Use physical HCI VGEN entry0, with S2MPU enforced')
    parser.add_argument('--ufs-prelink-profile',choices=('virtual-ufs0-prelink-config-v1',),help='Original QNX UniPro period and single-lane PMA config')
    parser.add_argument('--ufs-prdt-profile',help='Native QNX legacy PRDT128 clear descriptors, no FMP crypto')
    parser.add_argument('--ufs-power-profile',help='Optional exact original-driver UniPro timer/PMA latch profile')
    parser.add_argument('--native-hmi-signal-observe',action='store_true',help='Observe original Linux AArch64 signal frame input for the HMI JVM')
    parser.add_argument('--native-tee-observe',action='store_true',help='Observe the original QNX TEE init SMCs without changing guest state')
    parser.add_argument('--qnx-ufs-parent',type=Path,help='Closed original QNX ordinary first-boot overlay')
    parser.add_argument('--ufs-lun0-image',type=Path,help='Verified derived archive LUN0 manifest; writes only to new qcow2')
    parser.add_argument('--ufs-uic-trace',action='store_true',help='Observe upstream HCI writes, UIC commands and IRQ after original LK')
    parser.add_argument('--pcie2-atu-profile',choices=('virtual-pcie2-unrolled-empty-v1',),
                        help='Native unrolled PCIe2 iATU, type1 root, empty secondary bus')
    parser.add_argument('--pcie2-dbi-profile',choices=('virtual-pcie2-dbi-root-latches-v1',),help='Exact original RC DBI latches, no endpoints')
    parser.add_argument('--pcie2-pcs-profile',choices=('virtual-pcie2-pcs-no-endpoint-v1',),help='Exact original two-lane cold PCIe2 configuration, no endpoint')
    parser.add_argument('--pcie2-phy-profile',choices=('virtual-pcie2-phy-cold-v1',),help='Original PCIe2/3 PMU bit0 only')
    parser.add_argument('--pcie2-elbi-profile',choices=('virtual-pcie2-elbi-cold-v1',),help='Original Linux PCIe2 ELBI cold probe words, no link')
    parser.add_argument('--decon-window-profile',choices=('virtual-dpus0-window-v1',),help='VM4 WOD window state without scanout')
    parser.add_argument('--linux-panic-watch',action='store_true',help='One-shot read-only Linux die/panic hardware breakpoints')
    parser.add_argument('--dpp-profile',choices=('virtual-dpus0-dpp-irq-v1',),help='Native four DPP/IDMA IRQ config words; no generated frames')
    parser.add_argument('--sysmmu-profile',choices=('virtual-sysmmu-v7-discovery-v1',),help='Original Android SysMMU ID, CAPA1, VID4 table base; no DMA translation')
    parser.add_argument('--eqos-mac',help='Enable isolated Auto9 EQoS control device with explicit locally administered unicast MAC')
    parser.add_argument('--ufs-phy-isolation-profile',choices=('virtual-ufs0-phy-isolation-v1',),help='Retain Auto9 UFS0 PMU isolation bit, no PHY or link results')
    parser.add_argument('--ufs-shareability-profile',choices=('virtual-ufs0-shareability-v1',),help='Retain original UFS0 I/O shareability bits, without DMA or readiness effects')
    parser.add_argument('--ect-handoff-profile',choices=('original-bl2-ect-v1',),help='Load unmodified embedded BL2 ECT into original HCB/libect RAM reservation, no generated tables')
    parser.add_argument('--cal-init-config-profile',choices=('virtual-qnx-cal-config-v1',),help='Source-bound CAL boot config retention, not physical clock/power/ready effects')
    parser.add_argument('--ufs-descriptor-profile',choices=('virtual-samsung-byte-v1',),help='Explicit original Samsung legacy UTRD/PRDT byte units; no LK done injection')
    parser.add_argument('--tmu-profile',choices=('virtual-onepoint-v1','virtual-qnx-controller-v1'),help='Explicit synthetic onepoint ADC, optional QNX finite controller/threshold/GIC behavior; not physical trim/timing')
    parser.add_argument('--tmu-temperature-c',type=int,default=25,help='Uniform virtual TMU environment -40..150C, sampled periodically; requires explicit profile')
    parser.add_argument('--android-task-context',action='store_true',help='Read original VM4 init and selected child task stacks on stop')
    parser.add_argument('--linux-hmi-overlay',type=Path,help='Verified OEM Linux media layer for VM3 only, read-only')
    from linux_data_overlay import PROFILE as IVI_DATA_PROFILE
    parser.add_argument('--linux-ivi-parent',type=Path,help='Cold boot using a private copy of an exited OEM VM3 session')
    parser.add_argument('--linux-ivi-data',choices=(IVI_DATA_PROFILE,),help='Empty ext4 /data in a session-private VM3 layer')
    parser.add_argument('--linux-native-console-port',type=int,help='Bind the original vLM multiplexed console (includes VM3 ttySAC0) to loopback only')
    parser.add_argument('--linux-direct-console',action='store_true',help='Bind the explicitly staged VM3 ttySAC3 to a separate loopback console')
    parser.add_argument('--linux-smack-observe',action='store_true',help='Read OEM kernel SMACK rule state after stop; no guest writes')
    parser.add_argument('--linux-pid1-context',action='store_true',help='Capture a sleeping OEM Linux PID 1 kernel stack; requires native symbols')
    from qnx_display_identity import PROFILE as QNX_DISPLAY_PROFILE
    parser.add_argument('--qnx-display-profile',choices=(QNX_DISPLAY_PROFILE,),
                        help='Supply original VM2 MCU display selection from an explicit virtual configuration')
    parser.add_argument('--host-file-ram',action='store_true',help='Use ephemeral Windows backing for the three native auxiliary RAM banks')
    parser.add_argument('--android-ufs-parent',type=Path,help='Cold boot from a private copy of the previous completed VM4 run directory')
    parser.add_argument('--android-ufs-overlay',action='store_true',help='Give only VM4 a private writable LUN0 overlay over the immutable original virtual base')
    parser.add_argument('--lk-android-normal',action='store_true',help='Reproduce the original LK A-slot normal Android initramfs order and DT bootargs subset; retains AVB')
    parser.add_argument('--lk-linux-slot',choices=('a','b'),help='Apply verified original LK root/slot mutations to a separate Linux DTB; retains dm-verity')
    parser.add_argument('--native-ufs',action='store_true',help='Native UFS HCI and bounded vendor reset/config; no guessed OEM disk, PHY calibration or FMP')
    parser.add_argument('--pmu-domain-profile',choices=('virtual-sequencer-v1',),help='Explicit timed logical domain model, no physical rail/clock/GPU effects')
    parser.add_argument('--pmu-domain-latency-ns',type=int,default=1000)
    parser.add_argument('--pmu-domain-supply-mask',type=lambda x:int(x,0),default=0,help='Declared20-bit virtual supply-good environment')
    parser.add_argument('--pmu-dptx-debug-profile',choices=('virtual-input-bank-v1',),help='Explicit three-word opaque external inputs for the original raw DPTX dump, unknown physical fields')
    parser.add_argument('--pmu-dptx-debug-word0',type=lambda x:int(x,0))
    parser.add_argument('--pmu-dptx-debug-word1',type=lambda x:int(x,0))
    parser.add_argument('--pmu-dptx-debug-word2',type=lambda x:int(x,0))
    parser.add_argument('--serializer-profile',choices=('virtual-ti941-iid-v1',),help='Optional source-bound I2C17/1A control endpoint; no video/training backend')
    parser.add_argument('--serializer-powerup-ns',type=int,default=100000)
    parser.add_argument('--serializer-probe-ns',type=int,default=100000)
    parser.add_argument('--native-performance-cores',action='store_true',help='Prefer faster Windows CPU Sets for only the paused native AP')
    parser.add_argument('--native-parallel-tcg',action='store_true',help='Run the native AP with separate vCPU TCG threads and no icount')
    args=parser.parse_args()
    if (args.native_performance_cores or args.native_parallel_tcg) and args.operation!='probe':
        parser.error('Native host scheduling options apply only to a native probe')
    if (args.pmic_interface_reset_byte is not None or args.pmic_pmu_input_byte is not None or args.pmic_bootstrap_profile or args.bl2_fvmap_profile) and args.operation != 'probe':
        parser.error('--pmic-interface-reset-byte applies only to probe')
    if (args.native_hmi_signal_observe or args.native_tee_observe or args.qnx_ufs_parent is not None or args.qnx_phy88q2112 or args.qnx_display_profile or args.host_file_ram or args.qnx_bts or args.qnx_factory_bank or args.pmu_om_input is not None or args.mali_g3d1_jm_topology is not None or args.linux_ivi_parent is not None or args.linux_ivi_data is not None or args.linux_hmi_overlay is not None or args.linux_native_console_port is not None or args.linux_direct_console or args.linux_smack_observe or args.qnx_g3d00 or args.qnx_g3d00_jm_topology or args.qnx_g3d00_control_jobs or args.linux_mfc or args.qnx_dsi or args.linux_usb_phy or args.linux_dwc3 or args.qnx_dpp or args.qnx_dp0_aux_sink or args.qnx_dp0_irq or args.qnx_dp0_link_config or args.qnx_dp0_phy or args.qnx_dp0_reset or args.qnx_audio_clock_watch or args.android_ufs_parent or args.android_ufs_overlay or args.lk_android_normal or args.android_task_context or args.linux_pid1_context or args.abox_dsp_mailbox or args.abox_dsp_xtensa or args.lk_dsp_load or args.abox_dsp_off or args.abox_axiuser or args.linux_initcall_trace or args.android_abox_watch or args.android_initcall_trace or args.abox_sysmmu or args.abox_gic or args.scaler_reset or args.linux_decon or args.linux_dpp or args.ufs_vh or args.ufs_boot_luns is not None or args.linux_sysmmu or args.ufs_mailbox or args.g2d_reset or args.linux_usi_uart or args.mali_g3d1_empty_power or args.mali_g3d1_config or args.mali_g3d1_reset_irq or args.mali_g3d1_discovery or args.pcie2_atu_profile or args.pcie2_dbi_profile or args.pcie2_pcs_profile or args.ufs_prdt_profile or args.ufs_power_profile or args.ufs_lun0_image is not None or args.ufs_vgen_profile or args.ufs_prelink_profile or args.mali_g3d1_id is not None or args.ufs_uic_trace or args.pcie2_phy_profile or args.pcie2_elbi_profile or args.linux_panic_watch or args.decon_window_profile or args.dpp_profile or args.sysmmu_profile or args.eqos_mac or args.ufs_phy_isolation_profile or args.ufs_shareability_profile or args.cal_init_config_profile or args.ect_handoff_profile or args.lk_pwm_profile or args.clock_backend_watch or args.clock_rpc_watch or args.usi_i2c_profile or args.usi_spi_profile or args.lk_handoff_profile or args.ufs_descriptor_profile or args.serializer_profile or args.serializer_powerup_ns!=100000 or args.serializer_probe_ns!=100000 or args.pmu_dptx_debug_profile or args.pmu_dptx_debug_word0 is not None or args.pmu_dptx_debug_word1 is not None or args.pmu_dptx_debug_word2 is not None or args.pmu_domain_profile or args.pmu_domain_latency_ns!=1000 or args.pmu_domain_supply_mask or args.apm_wdt_profile or args.apm_wdt_clock_hz or args.pmu_core_status_mask or args.pmu_status1204 or args.pmu_signal_profile or args.pmu_link_profile or args.pmu_profile or args.pscdc_profile or args.pscdc_latency_cycles!=64 or args.icount_shift is not None or args.tmu_profile or args.tmu_temperature_c!=25) and args.operation!='probe':
        parser.error('Virtual hardware and instruction clock options apply only to probe')
    output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():
        raise ValueError('Require a new output directory under workspace scratch')
                                                                         
                                                          
    full_oem_boot=(args.operation=='probe' and args.board_adapter and args.load_qnx
                   and args.load_linux and args.load_android and args.abox_dsp_off
                   and args.abox_dsp_mailbox and args.linux_hmi_overlay is not None
                   and args.android_ufs_overlay and args.linux_ivi_data is not None)
    if full_oem_boot and args.seconds > ROUTINE_OEM_SECONDS and not args.extended_observation:
        parser.error(f'Routine full OEM boot verification is limited to {ROUTINE_OEM_SECONDS} wall seconds; '
                     'use --extended-observation for a specific later boot milestone')
                                                                           
                                                                              
    if full_oem_boot and args.linux_factory_bank:
                                                                                
        max_seconds=7200 if args.qnx_factory_bank else 1200
    else:
        max_seconds=600 if full_oem_boot else (300 if args.abox_dsp_off else 60)
    if not .05<=args.seconds<=max_seconds or not 0<=args.steps<=256 or not 1<=args.cpus<=8:
        raise ValueError('Probe bounds exceeded')
    output.mkdir(parents=True)
    if args.operation=='inventory':result=inventory(output)
    elif args.operation=='ufs-layout':result=native_ufs_inventory()
    elif args.operation=='primecells':result=native_primecells()
    elif args.operation=='coprocessors':result=coprocessor_inputs(output)
    elif args.operation=='mcu-candidate':result=mcu_candidate(output)
    elif args.operation=='acpm-candidate':result=acpm_candidate(output)
    elif args.operation=='extract-ifs':
        if not args.member:raise ValueError('Require at least one exact IFS member')
        result=extract_ifs_members(args.member,output)
    elif args.operation=='prepare-lk-analysis':result=prepare_lk_analysis(output)
    elif args.operation=='board-check':result=board_check(output,args.qemu,args.acpm_clock_hz)
    elif args.operation=='kernel-symbols':
        if not args.session:raise ValueError('Require --session')
        result=kernel_symbols(args.session,output)
    elif args.operation=='elf-window':
        if not args.binary or not 4<=args.length<=4096:raise ValueError('Require ELF path and bounded length')
        result=elf_window(args.binary,args.address,args.length)
    elif args.operation=='arm64-refs':
        if not args.binary:raise ValueError('Require ELF path')
        result=arm64_materialized_refs(args.binary,args.address)
    elif args.operation=='inspect-session':
        if not args.session:raise ValueError('Require --session')
        result=inspect_session(args.session,output)
    else:
        parameters=('seconds','steps','map_pram','hcb','exynos_uart','board_adapter','qemu','cpus','hcb_ram',
                    'payload_sized_qnx','guest_dtbs','loader_ram_mib','linux_factory_bank','qnx_factory_bank','qnx_bts','host_file_ram','qnx_display_profile','qnx_phy88q2112','load_qnx','load_linux','load_android','trace_mode','rtc_bootstrap','symbol_directory','translate_address','acpm_sram','lk_linux_slot','acpm_clock_hz','pmic_interface_reset_byte','pmic_bootstrap_profile','bl2_fvmap_profile','native_ufs','pmu_om_input','pscdc_profile','pscdc_latency_cycles','pmic_pmu_input_byte','pmu_profile','icount_shift','tmu_profile','tmu_temperature_c','pmu_link_profile','pmu_signal_profile','pmu_status1204','pmu_core_status_mask','apm_wdt_profile','apm_wdt_clock_hz','pmu_domain_profile','pmu_domain_latency_ns','pmu_domain_supply_mask','pmu_dptx_debug_profile','pmu_dptx_debug_word0','pmu_dptx_debug_word1','pmu_dptx_debug_word2','serializer_profile','serializer_powerup_ns','serializer_probe_ns','ufs_descriptor_profile','lk_handoff_profile','usi_spi_profile','usi_i2c_profile','clock_rpc_watch','clock_backend_watch','lk_pwm_profile','ect_handoff_profile','cal_init_config_profile','ufs_shareability_profile','ufs_phy_isolation_profile','eqos_mac','sysmmu_profile','dpp_profile','decon_window_profile','linux_panic_watch','pcie2_elbi_profile','pcie2_phy_profile','ufs_uic_trace','mali_g3d1_id','ufs_prelink_profile','ufs_vgen_profile','ufs_lun0_image','ufs_power_profile','ufs_prdt_profile','pcie2_pcs_profile','pcie2_dbi_profile','pcie2_atu_profile','mali_g3d1_discovery','mali_g3d1_reset_irq','mali_g3d1_config','mali_g3d1_empty_power','mali_g3d1_jm_topology','linux_usi_uart','g2d_reset','ufs_mailbox','linux_sysmmu','ufs_boot_luns','ufs_vh','linux_dpp','linux_decon','scaler_reset','abox_gic','abox_sysmmu','android_initcall_trace','linux_initcall_trace','android_abox_watch','abox_axiuser','abox_dsp_off','lk_dsp_load','abox_dsp_xtensa','abox_dsp_mailbox','lk_android_normal','linux_pid1_context','android_ufs_overlay','android_task_context','android_ufs_parent','qnx_audio_clock_watch','qnx_dp0_reset','qnx_dp0_phy','qnx_dp0_link_config','qnx_dp0_irq','qnx_dp0_aux_sink','qnx_dpp','linux_dwc3','linux_usb_phy','qnx_dsi','linux_mfc','qnx_g3d00','qnx_g3d00_jm_topology','qnx_g3d00_control_jobs','linux_native_console_port','linux_direct_console','linux_smack_observe','linux_hmi_overlay','linux_ivi_data','linux_ivi_parent')
        result=probe(output,native_performance_cores=args.native_performance_cores,
                     native_parallel_tcg=args.native_parallel_tcg,qnx_ufs_parent=args.qnx_ufs_parent,native_tee_observe=args.native_tee_observe,
                     native_hmi_signal_observe=args.native_hmi_signal_observe,
                     **{name:getattr(args,name) for name in parameters})
    vm.save(output/'result.json',result)
    print(json.dumps({'output':str(output),'operation':args.operation,'completed':True,'guests_booted_verified':False}))


if __name__=='__main__':
    main()
