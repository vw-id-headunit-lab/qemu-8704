





import argparse
from collections import deque
import hashlib
from pathlib import Path
import struct

import original_8704_vm as vm
from pmic_bootstrap import BL2_SHA256

BASE = 0x02100000
SRAM = 0x02039000
SRAM_SIZE = 0x38000
FVMAP = 0x02066000
TABLE_LIMIT = 0x2fe0                                                    


def validate(candidate):

    if len(candidate) != 0x3000:
        raise ValueError('FVMap candidate size mismatch')
    rows = []
    occupied = []
    for index in range(11):
        header = candidate[index*24:(index+1)*24]
        domain, levels, members, pll, mux, div = header[:6]
        gate, initial = header[9], header[8]
        if domain != index or not levels or not members or initial >= levels:
            raise ValueError('FVMap domain/count/initial OPP mismatch at '+str(index))
        clock_members=pll+mux+div+gate
                                                                          
                                                                       
        if members != clock_members and not (index==10 and members==1 and clock_members==0):
            raise ValueError('FVMap member classification mismatch at '+str(index))
        member_offset, opp_offset, matrix_offset = struct.unpack_from('<3H',header,18)
        sections = [('members',member_offset,2*clock_members),('rate_voltage',opp_offset,8*levels),
                    ('matrix',matrix_offset,levels*members)]
        for name, start, length in sections:
            if start < 11*24 or start+length > TABLE_LIMIT:
                raise ValueError('FVMap section outside original BL2 table allocation: '+name)
            if length: occupied.append((start,start+length,index,name))
        opps = [struct.unpack_from('<II',candidate,opp_offset+8*i) for i in range(levels)]
        if any(rate in (0,0xffffffff) or voltage in (0,0xffffffff) for rate,voltage in opps):
            raise ValueError('FVMap contains absent rate/voltage')
        member_values = struct.unpack_from('<'+'H'*clock_members,candidate,member_offset)
        if any(offset < 264 or offset+8 > TABLE_LIMIT for offset in member_values[:pll]):
            raise ValueError('FVMap PLL descriptor outside original table')
        for offset in member_values[:pll]:
            address,lock,words=struct.unpack_from('<IHH',candidate,offset)
            if not words or offset+8+4*words>TABLE_LIMIT:
                raise ValueError('FVMap PLL parameter array outside original table')
            occupied.append((offset,offset+8+4*words,index,'pll'))
        rows.append({'domain':domain,'levels':levels,'members':members,'pll':pll,
                     'mux':mux,'div':div,'gate':gate,'initial_opp':initial,
                     'offsets':{'members':member_offset,'rate_voltage':opp_offset,'matrix':matrix_offset},
                     'opps':[{'rate_khz':rate,'voltage_uv':voltage} for rate,voltage in opps]})
    ordered=sorted(occupied)
    if any(left[1] > right[0] for left,right in zip(ordered,ordered[1:])):
        raise ValueError('FVMap sections overlap')
    return {'abi_valid':True,'physical_calibration_verified':False,'domains':rows,
            'scope':'Original BL2 11x24-byte layout, bounds/counts/OPPs; not physical power/clock validation'}


def consumption_evidence(candidate, shared, controllers, selector_contract):

    layout=validate(candidate)
    if len(shared)!=SRAM_SIZE:raise ValueError('Native shared SRAM size mismatch')
    rows=[]
    for domain in layout['domains']:
        index=domain['domain'];offset=0x2454c+36*index
        pointer,selector,rate=(struct.unpack_from('<I',shared,offset+field)[0] for field in (0,8,12))
        selector &= 255
        if selector>=len(selector_contract):raise ValueError('Native selector index outside original table')
        desc=selector_contract[selector]
        initial=domain['opps'][domain['initial_opp']]
        code=max(0,(initial['voltage_uv']-desc['minimum_uv']+desc['step_uv']-1)//desc['step_uv'])
        actual=controllers[desc['controller']]['voltage_selectors'][selector%6]['value']
        row={'domain':index,'header_pointer':hex(pointer),'expected_header_pointer':hex(0x2d000+24*index),
            'current_rate_khz':rate,'expected_initial_rate_khz':initial['rate_khz'],
            'selector':selector,'expected_selector_code':code,'actual_transmitted_selector_code':actual}
        row['passed']=(pointer==0x2d000+24*index and rate==initial['rate_khz'] and actual==code)
        rows.append(row)
    base=struct.unpack_from('<I',shared,0x24ef8)[0]
    return {'fvmap_pointer':hex(base),'domains':rows,
            'consumption_observed':base==0x2d000 and all(row['passed'] for row in rows),
            'full_acpm_boot_verified':False,'physical_voltage_verified':False,
            'scope':'Actual native consumer pointers/rates and actual latched PMIC selector writes; not full ACPM readiness or analog voltage'}


def execute(raw, instruction_limit, initialize_parameters=False, virtual_chipid=False,
            virtual_asv_group_zero=False, inspect_ddr_keys=False, ddr_timing_key=None):
    from unicorn import (Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                         UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE)
    from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0,
                                    UC_ARM64_REG_X1, UC_ARM64_REG_X30)
    if hashlib.sha256(raw).hexdigest() != BL2_SHA256:
        raise ValueError('Original BL2 identity mismatch')
    if not 1 <= instruction_limit <= 500000:
        raise ValueError('Instruction bound exceeded')
    if ddr_timing_key is not None and not 0 <= ddr_timing_key < 1 << 64:
        raise ValueError('DDR timing key outside uint64')
    if (inspect_ddr_keys or ddr_timing_key is not None) and not initialize_parameters:
        raise ValueError('DDR timing requires original parameter parser')
    engine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    engine.mem_map(BASE, len(raw))
    engine.mem_write(BASE, raw)
    engine.mem_map(SRAM, SRAM_SIZE)
                                                                          
    stack = 0x40000000
    engine.mem_map(stack, 0x10000)
    engine.reg_write(UC_ARM64_REG_SP, stack+0xff00)
    state = {'instructions': 0, 'invalid_access': None}
    trace = deque(maxlen=96)
    chipid_reads = []
    asv_reads = []
                                                                             
                                                                    
    chipid_values = {0: 0xaaa80000, 4: 0, 8: 0, 0x10: 1 << 20, 0x24: 0}
    if virtual_chipid:
        engine.mem_map(0x10000000, 0x1000)
        for offset, value in chipid_values.items():
            engine.mem_write(0x10000000+offset, struct.pack('<I', value))
        def on_chipid(cpu, access, address, size, value, unused):
            if access == 16 and size == 4 and address-0x10000000 in chipid_values:
                chipid_reads.append({'address': hex(address), 'value': hex(chipid_values[address-0x10000000]),
                                     'pc': hex(cpu.reg_read(UC_ARM64_REG_PC))})
            else:
                state['invalid_access'] = {'access':access,'address':hex(address),'size':size,
                    'value':hex(value & 0xffffffffffffffff),'pc':hex(cpu.reg_read(UC_ARM64_REG_PC))}
                cpu.emu_stop()
        engine.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, on_chipid,
                        begin=0x10000000, end=0x10000fff)
    if virtual_asv_group_zero:
                                                                           
                                                                           
                                                                           
        engine.mem_map(0x10009000, 0x1000)
        def on_asv(cpu, access, address, size, value, unused):
            if access == 16 and size in (1, 2, 4) and 0x10009000 <= address and address+size <= 0x10009010:
                asv_reads.append({'address':hex(address),'size':size,'value':0,
                                  'pc':hex(cpu.reg_read(UC_ARM64_REG_PC))})
            else:
                state['invalid_access']={'access':access,'address':hex(address),'size':size,
                    'value':hex(value & 0xffffffffffffffff),'pc':hex(cpu.reg_read(UC_ARM64_REG_PC))}
                cpu.emu_stop()
        engine.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE,on_asv,
                        begin=0x10009000,end=0x10009fff)
    def on_code(cpu, address, size, unused):
        state['instructions'] += 1
        trace.append(hex(address))
    def on_invalid(cpu, access, address, size, value, unused):
        state['invalid_access'] = {'access': access, 'address': hex(address), 'size': size,
                                   'value': hex(value & 0xffffffffffffffff),
                                   'pc': hex(cpu.reg_read(UC_ARM64_REG_PC))}
        return False
    engine.hook_add(UC_HOOK_CODE, on_code)
    engine.hook_add(UC_HOOK_MEM_INVALID, on_invalid)
    error = None
    stop = BASE+0x2138
    parser_status = None
    try:
        if initialize_parameters:
            if raw[0x40c00:0x40c04] != b'PARA':
                raise ValueError('Original parameter input signature changed')
                                                                           
                                                                             
                                                                    
            engine.emu_start(BASE+0x3f90, BASE+0x3fa8, count=instruction_limit)
            if engine.reg_read(UC_ARM64_REG_PC)!=BASE+0x3fa8 or state['instructions']>=instruction_limit:
                raise ValueError('Instruction budget exhausted during parameter pointer setup')
            sentinel=stack+0xfff0
            engine.reg_write(UC_ARM64_REG_X30,sentinel)
            engine.emu_start(BASE+0x3db4,sentinel,count=instruction_limit-state['instructions'])
            parser_status={'pc':hex(engine.reg_read(UC_ARM64_REG_PC)),
                           'result':hex(engine.reg_read(UC_ARM64_REG_X0)),
                           'instructions':state['instructions']}
            if engine.reg_read(UC_ARM64_REG_PC)!=sentinel or engine.reg_read(UC_ARM64_REG_X0)!=0:
                raise ValueError('Original parameter parser did not return success')
                                                                          
                                                                        
        remaining=instruction_limit-state['instructions']
        if remaining<=0:raise ValueError('Instruction budget exhausted during parameter parsing')
        engine.emu_start(BASE+0x2110, stop, count=remaining)
    except (UcError, ValueError) as failure:
        error = str(failure)
    pc = engine.reg_read(UC_ARM64_REG_PC)
    candidate = bytes(engine.mem_read(FVMAP, 0x3000))
    returned=pc == stop and error is None and state['invalid_access'] is None
    validation=None
    if returned:
        try: validation=validate(candidate)
        except ValueError as failure: validation={'abi_valid':False,'error':str(failure)}
    fvmap_instructions=state['instructions']
    ddr_timing = None
    if returned and (inspect_ddr_keys or ddr_timing_key is not None):
                                                                            
                                                                           
        before=state['instructions']
        sentinel=stack+0xfff0
        def call(offset, arg0, arg1=0):
            engine.reg_write(UC_ARM64_REG_X0,arg0)
            engine.reg_write(UC_ARM64_REG_X1,arg1)
            engine.reg_write(UC_ARM64_REG_X30,sentinel)
            remaining=instruction_limit-state['instructions']
            if remaining <= 0:
                raise ValueError('DDR instruction budget exhausted')
            engine.emu_start(BASE+offset,sentinel,count=remaining)
            if state['invalid_access'] or engine.reg_read(UC_ARM64_REG_PC) != sentinel:
                raise ValueError('Original DDR data function did not return')
            return engine.reg_read(UC_ARM64_REG_X0)
        def local(address,length):
            if not BASE <= address <= BASE+len(raw)-length:
                raise ValueError('Parsed NEWTIME source outside BL2 memory')
            return bytes(engine.mem_read(address,length))
        try:
            if raw[0x12560:0x12568] != b'NEWTIME\0':
                raise ValueError('Original DDR parameter name changed')
            descriptor=call(0x3a68,BASE+0x12560)
            selector=call(0x3d74,descriptor,0x11111111)
            if not descriptor or not selector:
                raise ValueError('Original NEWTIME selector table absent')
            columns,rows,table=struct.unpack_from('<IIQ',local(selector,32),16)
            ddr_timing={'descriptor':hex(descriptor),'selector_address':hex(selector),
                        'selector_header_hex':local(selector,32).hex(),
                        'columns':columns,'rows':rows,'table_address':hex(table)}
                                                                            
                                                                              
            if not 2 <= columns <= 64 or not 1 <= rows <= 32:
                raise ValueError('Original NEWTIME selector dimensions outside bound')
            local(table,8*columns*rows)
            keys=[call(0x3a4c,table+8*columns*index) for index in range(rows)]
            ddr_timing={'selector':'0x11111111','columns':columns,'rows':rows,
                'keys':[hex(key) for key in keys],'selected_key':None,
                'scope':'Original later-stage NEWTIME data lookup/copy in isolation; ACPM-ready barrier, DRAM identity/training and full BL2 NOT executed',
                'native_boot_approved':False,'hardware_training_performed':False,
                'table_catalog_only':True,'fixed_header_offsets_populated':False,
                'pre_ddr_fvmap_sha256':hashlib.sha256(candidate).hexdigest()}
            if ddr_timing_key is not None:
                if ddr_timing_key not in keys:
                    raise ValueError('Requested DDR key absent from original NEWTIME selector')
                                                                               
                                                                            
                for start,end in ((0x223c,0x2248),(0x224c,0x2274)):
                    remaining=instruction_limit-state['instructions']
                    if remaining <= 0:raise ValueError('DDR instruction budget exhausted')
                    engine.emu_start(BASE+start,BASE+end,count=remaining)
                    if engine.reg_read(UC_ARM64_REG_PC)!=BASE+end:
                        raise ValueError('Original DDR buffer setup did not complete')
                size=call(0x1754,ddr_timing_key)
                if not 400 <= size <= 0x1c00:
                    raise ValueError('Original DDR output outside allocated window')
                image=bytes(engine.mem_read(0x02066c00,size))
                                                                              
                                                                             
                if struct.unpack_from('<Q',image)[0] != ddr_timing_key or struct.unpack_from('<I',image,16)[0]!=size:
                    raise ValueError('Original DDR output header mismatch')
                directory=struct.unpack_from('<Q',image,32)[0]-0x02066c00
                if directory != 400 or directory+16*(columns-1) > size:
                    raise ValueError('Original DDR directory outside image')
                sections=[]
                for index in range(columns-1):
                    tag,offset,width,height=struct.unpack_from('<4I',image,directory+16*index)
                    length=4*width*height
                    if not width or not height or offset%4 or offset < directory+16*(columns-1) or offset+length > size:
                        raise ValueError('Original DDR section outside image')
                    source_record=call(0x3d74,descriptor,tag)
                    if not source_record:raise ValueError('Original DDR section source absent')
                    source_width,source_height,data=struct.unpack_from('<IIQ',local(source_record,32),16)
                    source_data=local(data,length)
                    if (source_width,source_height)!=(width,height) or image[offset:offset+length]!=source_data:
                        raise ValueError('Original DDR copy/source mismatch')
                    file_offset=data-BASE
                    if source_data != raw[file_offset:file_offset+length]:
                        raise ValueError('NEWTIME source data is not unmodified original file bytes')
                    sections.append({'tag':hex(tag),'offset':offset,'columns':width,'rows':height,
                        'bytes':length,'original_file_offset':file_offset,
                        'sha256':hashlib.sha256(source_data).hexdigest()})
                ddr_timing.update(selected_key=hex(ddr_timing_key),bytes=size,
                    ap_address='0x2066c00',mcu_address='0x2dc00',
                    sha256=hashlib.sha256(image).hexdigest(),image_hex=image.hex(),sections=sections)
            ddr_timing['instructions']=state['instructions']-before
        except (UcError,ValueError) as failure:
            error=str(failure)
            ddr_timing={**(ddr_timing or {}),'error':error,'native_boot_approved':False,
                        'instructions':state['instructions']-before}
    rows = [{'index': index, 'raw_hex': candidate[index*24:(index+1)*24].hex(),
             'id': candidate[index*24], 'bytes_1_9': list(candidate[index*24+1:index*24+10]),
             'offsets': list(struct.unpack_from('<3H', candidate, index*24+18))}
            for index in range(11)]
    return candidate, {'instructions': state['instructions'],'fvmap_instructions':fvmap_instructions,'pc': hex(pc),
        'fragment_returned': returned, 'parameter_parser':parser_status,'validation':validation,
        'ddr_timing':ddr_timing,
        'stop_reason': 'ddr_data_function_failed' if ddr_timing and 'error' in ddr_timing else
                       'unknown_memory' if state['invalid_access'] else
                       'original_fragment_returned' if pc == stop else 'instruction_limit_or_exception',
        'invalid_access': state['invalid_access'], 'error': error, 'last_instruction_addresses': list(trace),
        'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
        'candidate_nonzero_bytes': sum(value != 0 for value in candidate), 'header_candidates': rows,
        'initial_context': 'Original BL2 ROM, cold-zero virtual SRAM, explicit analysis stack; prior cal_init NOT executed',
        'original_parameters_initialized':initialize_parameters,
        'virtual_chipid': {'enabled':virtual_chipid,'physical_identity_verified':False,
            'registers':{hex(0x10000000+k):hex(v) for k,v in chipid_values.items()},
            'reads':chipid_reads,'source':'exynos-auto9-chipid.inc.c default QOM properties',
            'source_sha256':vm.sha(Path(__file__).with_name('exynos-auto9-chipid.inc.c'))},
        'virtual_asv':{'enabled':virtual_asv_group_zero,'profile':'four-zero-virtual-fuse-words',
            'registers':{hex(0x10009000+4*i):0 for i in range(4)},'reads':asv_reads,
            'physical_calibration_verified':False,'native_board_asv_model_connected':False},
        'native_boot_approved': False, 'full_bl2_executed': False, 'physical_calibration_inputs_verified': False,
        'scope': 'Actual bounded original ARM64 instructions, explicit optional virtual silicon configuration; no table/return-value synthesis'}


def main():
    import vlm_platform as platform
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instructions', default=100000, type=int)
    parser.add_argument('--initialize-original-parameters',action='store_true')
    parser.add_argument('--virtual-chipid',action='store_true')
    parser.add_argument('--virtual-asv-group-zero',action='store_true')
    parser.add_argument('--inspect-ddr-keys',action='store_true')
    parser.add_argument('--ddr-timing-key',type=lambda value:int(value,0))
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():
        raise ValueError('Require a new scratch output directory')
    source = platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')
    candidate, result = execute(source.read_bytes(), args.instructions,args.initialize_original_parameters,
                                args.virtual_chipid,args.virtual_asv_group_zero,
                                args.inspect_ddr_keys,args.ddr_timing_key)
    output.mkdir(parents=True)
    (output/'fvmap-unverified-partial.bin').write_bytes(candidate)
    timing=result.get('ddr_timing')
    if timing and 'image_hex' in timing:
        (output/'ddr-original-newtime-untrained.bin').write_bytes(bytes.fromhex(timing.pop('image_hex')))
    vm.save(output/'result.json', {'source': str(source), 'source_sha256': vm.sha(source), **result})
    summary={key:result[key] for key in ('instructions','pc','stop_reason','invalid_access',
                                       'fragment_returned','native_boot_approved','error')}
    if timing is not None:
        summary['ddr_timing']={key:timing.get(key) for key in
                              ('selected_key','bytes','instructions','error','native_boot_approved')}
    print(__import__('json').dumps(summary))


if __name__ == '__main__':
    main()
