




import argparse
import hashlib
import json
from pathlib import Path
import struct
import os
import subprocess
import time
import original_8704_vm as vm
import vlm_platform as platform
from cmu_sequence import DynamicCalImage

CAL=vm.ROOT/'var/inputs/support/归档/固件与启动研究/pscdc-loader--libcal_if.so.1'
CAL_SHA='1936013a8c897dcae7b11503634c445b68d9c63dbbaf9aa1b4d5b404bef69ceb'
BL2_SHA='a83ca762143fab69a0724843bad958f39916ec261f7a65e4de615d82345a5a44'
ECT_OFFSET=0x40c00
ECT_BASE=0xe9a00000
ECT_RESERVATION=0x3a000
ECT_LIB=vm.ROOT/'var/inputs/support/归档/虚拟机平台对照/cal-init-native--libect.so.1'
ECT_LIB_SHA='a4d390d461abbb0ca9c6f5486cdc3cab1fb9b214b0800e9c6ad5654e6c451902'
QNX_RUNTIME_BASE=0xe0000000
QNX_RUNTIME_BYTES=0x3c00000
QNX_LOG_BASE=0xe0010000
QNX_LOG_BYTES=0x200000
QNX_LOG_PREFIX=b'MMFLAGS=19\r\n'


def load_window(raw,address,size):

    if address<0 or not 1<=size<=1048576:raise ValueError('Invalid ELF window bound')
    if len(raw)<64 or raw[:6]!=b'\x7fELF\x02\x01':raise ValueError('Require little-endian ELF64')
    header=struct.unpack_from('<HHIQQQIHHHHHH',raw,16)
    if header[1]!=183 or header[8]!=56 or not 1<=header[9]<=128:raise ValueError('Invalid ARM64 program table')
    if header[4]+header[8]*header[9]>len(raw):raise ValueError('Truncated program table')
    matches=[]
    for n in range(header[9]):
        row=struct.unpack_from('<IIQQQQQQ',raw,header[4]+n*header[8])
        kind,flags,offset,virtual,physical,file_size,memory_size,align=row
        if offset+file_size>len(raw):raise ValueError('Truncated ELF segment')
        if kind==1 and virtual<=address and address+size<=virtual+file_size:
            start=offset+address-virtual;matches.append(raw[start:start+size])
    if len(matches)!=1:raise ValueError('ELF address not in one initialized LOAD')
    return matches[0]


def contract():
    raw=CAL.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=CAL_SHA:raise ValueError('Original CAL library identity mismatch')
    image=DynamicCalImage(raw);elf=image.elf
    u32=lambda a:struct.unpack('<I',elf.at(a,4))[0]
    count_pointer=image.pointer(0x8efd0);count=u32(count_pointer);table=image.pointer(0x8ee80)
    if not 1<=count<=32:raise ValueError('Original DVFS table count outside bound')
    nodes=[]
    for index in range(count):
        address=table+96*index;ident=u32(address)
        if ident!=0xb040000+index:raise ValueError('Original QNX DVFS node id changed')
        name=image.string(image.pointer(address+8));members=image.pointer(address+32);member_count=u32(address+52)
        if not 0<=member_count<=1024:raise ValueError('Original DVFS member count outside bound')
        nodes.append({'index':index,'address':hex(address),'id':hex(ident),'name':name,
            'cold_members_pointer':hex(members),'cold_member_count':member_count,'voltage_index':u32(address+72),
            'original_members':[hex(v[0]) for v in struct.iter_unpack('<I',elf.at(members,4*member_count))] if member_count else []})
    seq_count=u32(image.pointer(0x8ee40));seq_base=image.pointer(0x8eed0);sequences=[]
    if seq_count>1024:raise ValueError('Unbounded original PMUCAL init sequence')
    for i in range(seq_count):
        a=seq_base+i*80
        sequences.append({'index':i,'address':hex(a),'op':u32(a),'name':image.string(image.pointer(a+8)),
            'base':hex(u32(a+16)),'offset':hex(u32(a+32)),'mask':hex(u32(a+36)),'value':hex(u32(a+40))})
    hook=image.pointer(image.pointer(0x8efe8))
    return {'source':str(CAL),'sha256':CAL_SHA,'dvfs_count_pointer':hex(count_pointer),'dvfs_count':count,
        'table':hex(table),'stride':96,'nodes':nodes,'board_init_hook':hex(hook),'lpm_init':sequences,
        'fvmap_copy_entry':'0x4bf40','memory_modified':False}


def inspect_session(session):
    session=Path(session);result=contract();raw=(session/'acpm-shared-sram.bin').read_bytes()
    if len(raw)!=0x38000:raise ValueError('Native SRAM capture length changed')
    registers=json.loads((session/'apm-shared-registers.json').read_text(encoding='utf-8'))['registers']
    pointer=int(registers['0x10500088'],16)
    if pointer!=0x2d000:raise ValueError('Unexpected original FVMap source pointer')
    data=raw[pointer:pointer+0x2000];rows=[]
    for node in result['nodes']:
        offset=24*node['index'];header=data[offset:offset+24]
        ident,levels,members,plls,muxes,divs=header[:6]
        member_offset,rates_offset=struct.unpack_from('<HH',header,18)
        rows.append({'node':node,'header_hex':header.hex(),'sram_id':ident,'levels':levels,'members':members,
            'plls':plls,'muxes':muxes,'dividers':divs,'member_offset':member_offset,'rates_offset':rates_offset,
            'cold_array_requires_ect_allocation':members>node['cold_member_count'],
            'member_table_in_shmem':member_offset+members*2<=len(data),
            'rates_table_in_shmem':rates_offset+levels*8<=len(data)})
    result.update({'session':str(session),'sram_sha256':vm.sha(session/'acpm-shared-sram.bin'),
        'rows':rows,'fvmap_pointer':hex(pointer),'fvmap_hex_prefix':data[:24*len(rows)].hex(),
        'cold_arrays_require_ect_initialization':any(r['cold_array_requires_ect_allocation'] for r in rows),
        'cal_init_runtime_position_verified':False})
    return result


def scan_ect():

    index=[json.loads(s) for s in (vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines()]
    matches=[];examined=[]
    for row in index:
        name=row['name'].removeprefix('./')
        if not 0<row['size']<=16*1024*1024 or not name.startswith(('boot-binary/','lk-boot/','hcb/')):continue
        try:path=platform.factory_file(name)
        except (FileNotFoundError,StopIteration):continue
        raw=path.read_bytes();examined.append({'path':str(path),'sha256':vm.sha(path)})
        start=0
        while (start:=raw.find(b'PARA',start))>=0:
            if start+16<=len(raw):
                a,b=struct.unpack_from('<II',raw,start+8)
                matches.append({'path':str(path),'sha256':vm.sha(path),'offset':hex(start),
                    'header_hex':raw[start:start+64].hex(),'word8':a,'block_count':b})
            start+=4
    return {'examined':examined,'candidates':matches,'originals_modified':False}


def original_ect():

    path=platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin');raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=BL2_SHA:raise ValueError('Original BL2 ECT source changed')
    magic,version,size,count=struct.unpack_from('<4s4sII',raw,ECT_OFFSET)
    if (magic,version,size,count)!=(b'PARA',b'000a',35076,6):raise ValueError('Original embedded ECT header changed')
    data=raw[ECT_OFFSET:ECT_OFFSET+size]
    if len(data)!=size or size>ECT_RESERVATION:raise ValueError('Original ECT outside source/reservation')
    offset=16;blocks=[]
    for _ in range(count):
        length=struct.unpack_from('<I',data,offset)[0];offset+=4
        if not 1<=length<=64:raise ValueError('Invalid ECT block name length')
        name=data[offset:offset+length].decode('ascii')
        if data[offset+length]!=0:raise ValueError('Unterminated original ECT block name')
        offset+=(length+4)&~3
        target=struct.unpack_from('<I',data,offset)[0];offset+=4
        if target%4 or not 0<target<size:raise ValueError('Original ECT block offset outside image')
        blocks.append({'name':name,'offset':target})
    if len({b['name'] for b in blocks})!=count or min(b['offset'] for b in blocks)!=offset:
        raise ValueError('Original ECT directory overlap/duplicate')
                                                                               
    hcb=platform.original('hcb/hcb.img').read_bytes();start=hcb.index(b'VDTHYP\0\0')+8
    nodes=platform.parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
    banks=list(struct.iter_unpack('>QQ',nodes['/vlm/vm@2/vm-memory']['reg']))
    if (ECT_BASE,ECT_RESERVATION) not in banks:raise ValueError('Original VM2 ECT reservation changed')
    lib=ECT_LIB.read_bytes()
    if hashlib.sha256(lib).hexdigest()!=ECT_LIB_SHA:raise ValueError('Original QNX ECT consumer changed')
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    from capstone.arm64 import ARM64_OP_IMM
    decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM);decoder.detail=True
    instructions=list(decoder.disasm(load_window(lib,0x21c8,0x58),0x21c8))
    assignments={i.reg_name(i.operands[0].reg):i.operands[1].imm&0xffffffff for i in instructions
        if i.mnemonic=='mov' and len(i.operands)==2 and i.operands[1].type==ARM64_OP_IMM}
    if assignments.get('w4',assignments.get('x4'))!=ECT_BASE:raise ValueError('Original libect mmap physical address changed')
    return data,{'profile':'original-bl2-ect-v1','source':str(path),'source_sha256':BL2_SHA,
        'source_offset':hex(ECT_OFFSET),'bytes':size,'sha256':hashlib.sha256(data).hexdigest(),'blocks':blocks,
        'consumer':str(ECT_LIB),'consumer_sha256':ECT_LIB_SHA,
        'physical':hex(ECT_BASE),'reserved_bytes':ECT_RESERVATION,'original_bytes_unchanged':True,
        'generated_tables':False,'cal_init_success_verified':False}


def recover_ect(output):
    data,metadata=original_ect();path=Path(output)/'ect-original.bin';path.write_bytes(data)
    metadata['path']=str(path);return metadata


def config_groups():
    original=contract();cmu_bases={r['base'] for r in platform.native_cmu_layout()}
    groups={'pmu':{},'cmu':{},'sysreg':{}}
    for row in original['lpm_init']:
        base,offset,mask=map(lambda k:int(row[k],16),('base','offset','mask'))
        if row['op'] not in (1,3) or offset%4:raise ValueError('CAL init configuration requires original word RMW/conditional RMW')
        kind='pmu' if base==0x10460000 else 'cmu' if base in cmu_bases else 'sysreg'
        if kind=='sysreg' and (not row['name'].startswith('SYSREG_') or offset not in (0x104,0x10c,0x110) or mask!=0xffffffff):
            raise ValueError('Unknown CAL init hardware class: '+str(row))
        address=base+offset
        if address not in groups[kind]:groups[kind][address]={'address':address,'mask':0,'names':[]}
        item=groups[kind][address];item['mask']|=mask;item['names'].append(row['name'])
    return {key:sorted(rows.values(),key=lambda r:r['address']) for key,rows in groups.items()}


def emit_configs(output):
    groups=config_groups();sysregs=groups['sysreg']
    if len(sysregs)!=42:raise ValueError('Original SYSREG init count changed')
    prefix='''/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original QNX CAL lpm_init op1/op3 configuration retention only.
 * Source: 1936013a8c897dcae7b11503634c445b68d9c63dbbaf9aa1b4d5b404bef69ceb
 * Explicit profile, cold zero assumed. No clock/power/IRQ/ready side effects.
 */
static bool exynos_auto9_cal_init_enabled;
'''
    prefix+='static const uint64_t exynos_auto9_cal_cmu_config[] = {\n'+''.join(f'    0x{r["address"]:x}, /* {r["names"][0]} */\n' for r in groups['cmu'])+'};\n'
    prefix+='static const uint32_t exynos_auto9_cal_pmu_offsets[] = {\n'+''.join(f'    0x{r["address"]-0x10460000:x}, /* {r["names"][0]} */\n' for r in groups['pmu'])+'};\n'
    prefix+='static const uint64_t exynos_auto9_cal_sysreg_addresses[] = {\n'+''.join(f'    0x{r["address"]:x}, /* {r["names"][0]} */\n' for r in sysregs)+'};\n'
    body='''
static bool exynos_auto9_cal_pmu_config(hwaddr off)
{
    if (!exynos_auto9_cal_init_enabled) { return false; }
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_cal_pmu_offsets); i++) {
        if (exynos_auto9_cal_pmu_offsets[i] == off) { return true; }
    }
    return false;
}
static bool exynos_auto9_cal_cmu_declared(hwaddr address)
{
    if (!exynos_auto9_cal_init_enabled) { return false; }
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_cal_cmu_config); i++) {
        if (exynos_auto9_cal_cmu_config[i] == address) { return true; }
    }
    return false;
}
#define TYPE_EXYNOS_AUTO9_CAL_INIT "exynos-auto9.cal-init"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9CalInit, EXYNOS_AUTO9_CAL_INIT)
typedef struct {
    MemoryRegion mr;
    ExynosAuto9CalInit *owner;
    unsigned index;
} ExynosAuto9CalReg;
struct ExynosAuto9CalInit {
    SysBusDevice parent_obj;
    ExynosAuto9CalReg regions[G_N_ELEMENTS(exynos_auto9_cal_sysreg_addresses)];
    uint32_t words[G_N_ELEMENTS(exynos_auto9_cal_sysreg_addresses)];
    uint64_t reads, writes;
};
static uint64_t exynos_auto9_cal_reg_read(void *opaque, hwaddr off, unsigned size)
{
    ExynosAuto9CalReg *r = opaque;
    r->owner->reads++;
    return r->owner->words[r->index];
}
static void exynos_auto9_cal_reg_write(void *opaque, hwaddr off, uint64_t value, unsigned size)
{
    ExynosAuto9CalReg *r = opaque;
    r->owner->writes++;
    r->owner->words[r->index] = value;
}
static const MemoryRegionOps exynos_auto9_cal_reg_ops = {
    .read = exynos_auto9_cal_reg_read, .write = exynos_auto9_cal_reg_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_cal_reg_reset(DeviceState *dev)
{
    ExynosAuto9CalInit *s = EXYNOS_AUTO9_CAL_INIT(dev);
    memset(s->words, 0, sizeof(s->words)); s->reads = s->writes = 0;
}
static void exynos_auto9_cal_reg_init(Object *obj)
{
    ExynosAuto9CalInit *s = EXYNOS_AUTO9_CAL_INIT(obj);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->regions); i++) {
        ExynosAuto9CalReg *r = &s->regions[i]; r->owner = s; r->index = i;
        memory_region_init_io(&r->mr, obj, &exynos_auto9_cal_reg_ops, r, "auto9-cal-sysreg", 4);
    }
    object_property_add_uint64_ptr(obj, "config-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "config-writes", &s->writes, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_cal_reg_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_cal_reg_reset);
}
static const TypeInfo exynos_auto9_cal_reg_type = {
    .name = TYPE_EXYNOS_AUTO9_CAL_INIT, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9CalInit),
    .instance_init = exynos_auto9_cal_reg_init, .class_init = exynos_auto9_cal_reg_class,
};
static void exynos_auto9_cal_reg_register(void) { type_register_static(&exynos_auto9_cal_reg_type); }
type_init(exynos_auto9_cal_reg_register)
static void exynos_auto9_cal_init_create(VirtMachineState *vms, bool enabled)
{
    exynos_auto9_cal_init_enabled = enabled;
    if (!enabled) { return; }
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_CAL_INIT);
    object_property_add_child(OBJECT(vms), "exynos-cal-init", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_cal_sysreg_addresses); i++) {
        /* More than32 sparse words: direct MemoryRegions, not SysBus MMIO slots. */
        ExynosAuto9CalInit *s = EXYNOS_AUTO9_CAL_INIT(dev);
        memory_region_add_subregion(get_system_memory(), exynos_auto9_cal_sysreg_addresses[i], &s->regions[i].mr);
    }
}
'''
    path=Path(output)/'exynos-auto9-cal-init.inc.c';path.write_text(prefix+body,encoding='utf-8',newline='')
    return {'source_sha256':CAL_SHA,'groups':groups,'path':str(path),'sha256':vm.sha(path),
        'configuration_retention_only':True,'physical_clock_power_effects':False}


def qnx_log_source():
    dtb=platform.factory_file('qnx/qtd/icas3gp-qla-vm.dtb')
    nodes=platform.parse_dtb(dtb.read_bytes())
    if nodes['/reserved-memory/debug_snapshot/log_kernel']['reg']!=struct.pack('>QI',QNX_LOG_BASE,QNX_LOG_BYTES):
        raise ValueError('Original QNX debug-snapshot log reservation changed')
    return dtb


def decode_qnx_log_area(area):
    if len(area)!=QNX_LOG_BYTES:
        raise ValueError('Incomplete original QNX debug-snapshot log reservation')
    if not area.startswith(QNX_LOG_PREFIX):
        return b'',False
    payload,separator,_=area.partition(b'\0')
    return payload,bool(separator)


def formatted_qnx_boot_log(raw):

    offset=QNX_LOG_BASE-QNX_RUNTIME_BASE
    payload,terminated=decode_qnx_log_area(raw[offset:offset+QNX_LOG_BYTES])
    return offset if payload else None,payload,terminated


def capture_runtime(qmp,output):

    output=Path(output);output.mkdir();ect,metadata=original_ect()                                    
    target=output/'qnx-runtime-e0000000.bin'
    qmp.execute('pmemsave',{'val':QNX_RUNTIME_BASE,'size':QNX_RUNTIME_BYTES,'filename':str(target)})
    raw=target.read_bytes()
    if len(raw)!=QNX_RUNTIME_BYTES:raise ValueError('Incomplete original QNX runtime bank capture')
    dtb=qnx_log_source()
    start,log,terminated=formatted_qnx_boot_log(raw)
    text=log.decode('utf-8',errors='backslashreplace');log_path=output/'qnx-boot-live.txt';log_path.write_text(text,encoding='utf-8',newline='')
    ect_path=output/'ect-runtime.bin'
    qmp.execute('pmemsave',{'val':ECT_BASE,'size':ECT_RESERVATION,'filename':str(ect_path)})
    ect_runtime=ect_path.read_bytes()
    result={'physical':'0xe0000000','bytes':len(raw),'path':str(target),'sha256':vm.sha(target),
        'log_offset':hex(start) if start is not None else None,
        'formatted_log':str(log_path),'formatted_log_bytes':len(log),
        'formatted_log_reservation':{'base':hex(QNX_LOG_BASE),'bytes':QNX_LOG_BYTES,
                                     'nul_terminated':terminated,'dtb_sha256':vm.sha(dtb)},
        'ect_runtime':{'path':str(ect_path),'sha256':vm.sha(ect_path),'header_hex':ect_runtime[:16].hex(),
            'all_zero':not any(ect_runtime),'original_prefix_matches':ect_runtime[:len(ect)]==ect},
        'memory_modified':False,'full_qnx_boot_verified':False}
    vm.save(output/'result.json',result);return result


def check_configs(output,exe,enabled=True):
    from ufs_platform import QTest
    groups=config_groups();qp,tp=vm.free_port(),vm.free_port()
    machine='virt-8704-apm,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1'+(',x-exynos-cal-init=1' if enabled else '')
    command=[str(exe),'-machine',machine,'-cpu','cortex-a57','-accel','qtest','-m','128','-display','none','-monitor','none',
        '-nic','none','-serial','null','-serial','null','-serial','null','-S',
        '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off',
        '-d','unimp,guest_errors','-D',str(output/'hardware-rejects.log'),
        '-qtest-log',str(output/'qtest.log')]
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(exe),'fixture_only':True})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];error=None;qt=qmp=None
    def check(name,actual,expected):
        checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
        if actual!=expected:raise ValueError('CAL configuration check: '+name)
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            until=time.monotonic()+10
            while time.monotonic()<until:
                if process.poll() is not None:raise ValueError('CAL QEMU exited')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.05)
            if qmp is None:raise ValueError('CAL QMP unavailable')
            qmp.execute('cont');qt=QTest(tp)
            children=qmp.execute('qom-list',{'path':'/machine'})
            check('Sparse SYSREG device is opt-in',any(r['name']=='exynos-cal-init' for r in children),enabled)
            if enabled:
                for kind,rows in groups.items():
                    for row in rows:
                        address,mask=row['address'],row['mask'];before=qt.read(address)
                        for value in (0xa5a5a5a5,0x5a5a5a5a):
                            after=(before&~mask)|(value&mask);qt.write(address,after)
                            check(kind+' source word '+hex(address)+' '+hex(value),qt.read(address),after)
                        qt.write(address,before)
                addresses={r['address'] for r in groups['sysreg']}
                for address in sorted(addresses):
                    if address+4 in addresses:continue
                    before=qmp.execute('qom-get',{'path':'/machine/exynos-cal-init','property':'config-reads'})
                    qt.read(address+4)
                    check('Adjacent SYSREG hole unmapped '+hex(address+4),qmp.execute('qom-get',{'path':'/machine/exynos-cal-init','property':'config-reads'}),before)
                                                                         
                qt.write(0x1046830c,31);check('PS_HOLD atomic clear shares state',qt.read(0x1046030c)&0x80000000,0)
                qt.write(0x1046c30c,31);check('PS_HOLD atomic set shares state',qt.read(0x1046030c)&0x80000000,0x80000000)
                check('No LK handoff fabricated',qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':'ufs-lk-bootflag'}),0)
                check('No PMU timer completion fabricated',qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':'timer-completions'}),0)
                vm.save(output/'config-runtime.json',{k:qmp.execute('qom-get',{'path':'/machine/exynos-cal-init','property':k}) for k in ('config-reads','config-writes')})
            else:
                for address in (0x1046030c,0x10460284,0x10460a08):
                    before=qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':'rejected-accesses'})
                    qt.read(address)
                    check('Default PMU extra word still rejects '+hex(address),qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':'rejected-accesses'}),before+1)
                qt.read(0x1bf00850)
                tree=qmp.execute('human-monitor-command',{'command-line':'info mtree -f'})
                for row in groups['sysreg']:check('Default SYSREG aperture absent '+hex(row['address']),f"{row['address']:016x}-" in tree,False)
            qmp.execute('quit');process.wait(timeout=5)
        except Exception as exc:error=repr(exc)
        finally:
            if qt:vm.save(output/'qtest-transcript.json',{'calls':qt.transcript,'events':qt.events});qt.stream.close();qt.socket.close()
            if qmp:qmp.close()
            if process.poll() is None:process.terminate();process.wait(timeout=5)
    if not enabled and error is None:
        check('Default CMU extra word still rejects','Auto9 CMU unsupported read base=0x1bf00000 offset=0x850' in (output/'hardware-rejects.log').read_text(encoding='utf-8'),True)
    result={'passed':error is None and all(c['passed'] for c in checks),'error':error,'checks':checks,
        'qemu_exit_code':process.returncode,'configuration_retention_only':True,'factory_boot_verified':False}
    vm.save(output/'result.json',result)
    if not result['passed']:raise ValueError('CAL config checks failed: '+str(output/'result.json'))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=('contract','inspect','scan-ect','recover-ect','emit-configs','check-configs','strict-configs'))
    parser.add_argument('--session',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qemu',type=Path,default=vm.ROOT/'runtime/qemu-system-aarch64.exe')
    args=parser.parse_args();output=args.output.resolve()
    if output.exists() or not output.is_relative_to(vm.ROOT/'var'):raise ValueError('Require new scratch output')
    if args.operation=='inspect' and not args.session:raise ValueError('Require original native session')
    output.mkdir(parents=True)
    if args.operation=='contract':result=contract()
    elif args.operation=='scan-ect':result=scan_ect()
    elif args.operation=='recover-ect':result=recover_ect(output)
    elif args.operation=='emit-configs':result=emit_configs(output)
    elif args.operation in ('check-configs','strict-configs'):result=check_configs(output,args.qemu.resolve(),args.operation=='check-configs')
    else:result=inspect_session(args.session.resolve())
    vm.save(output/'result.json',result);print(output/'result.json')


if __name__=='__main__':main()
