




from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile

import original_8704_vm as vm
import vlm_platform as platform
import pmic_bootstrap as bootstrap

MEMBERS = ('boot-binary/el3_mon.bin', 'boot-binary/epbl.bin')
PUBLIC_CLOCK_SHA256 = 'f4e3c1654230b942677e30f0593a5da500af14af2580b166c0cc8ae7da579870'
QNX_CAL_SHA256 = '1936013a8c897dcae7b11503634c445b68d9c63dbbaf9aa1b4d5b404bef69ceb'
PHY_REFERENCE_SHA256 = '8a16671d228ee206012d9a3b8a976aec113f7ea2ea45fcabe8565b2d85e4f6cf'
PUBLIC_PSCDC_SHA256 = 'd06e1bb58419cd298bdf1232d2c4cee3aa3656795262e5707eca9ccbe934c01e'
PUBLIC_PSCDC_HEADER_SHA256 = '20e8d775c9264581ed8ab8dbb82737fef357d80f603c1f094d3402303642c77e'
NATIVE_QUEUE_SHA256 = '4aaeb4ecd4c6358bd1066fdeab6e96726acb3c3f2e606118eb4e0086e22d8dd0'
NATIVE_MIRROR_SHA256 = 'e0269f6a2bdb90b36f9502afd4c610fca509f0045fee67046836fc0785b761fd'


def public_pscdc_contract(source, header, session):






    inputs = []
    for path, expected in ((source, PUBLIC_PSCDC_SHA256),
                           (header, PUBLIC_PSCDC_HEADER_SHA256)):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Pinned public PSCDC source identity mismatch')
        inputs.append(raw.decode('utf-8'))
    text, declarations = inputs
    begin = text.index('static void pscdc_trans(')
    end = text.index('/* parent rate table', begin)
    body = text[begin:end]
    writes = []
    for match in re.finditer(r'pwrcal_writel\(\s*(\w+)\s*,\s*([^;]+)\);', body):
        writes.append({'register':match[1], 'expression':match[2].strip(),
                       'source_line':text.count('\n', 0, begin+match.start())+1})
                                                                        
                                                                     
    bindings = {}
    for row in writes:
        fifo = re.fullmatch(r'PSCDC_(SMC|SCI)_FIFO_CLK_CON\d+', row['register'])
        expression = re.fullmatch(r'(0x800[0-4]0000)\s*\|\s*\((\w+)(?:\s*<<\s*(12))?\)', row['expression'])
        if fifo and expression:
            base = int(expression[1],16)
            key = (fifo[1], (base >> 16) & 15)
            binding = {'queue':fifo[1], 'selector_19_16':key[1],
                       'constant':hex(base), 'parameter':expression[2],
                       'payload_shift':int(expression[3] or 0),
                       'source_lines':[row['source_line']]}
            if key in bindings:
                if any(bindings[key][name] != binding[name]
                       for name in ('constant','parameter','payload_shift')):
                    raise ValueError('Public queue selector has conflicting bindings')
                bindings[key]['source_lines'].append(row['source_line'])
            else:
                bindings[key] = binding
    expected_bindings = {
        ('SMC',0):('top_mux_bus_pll',12),
        ('SMC',2):('mif_mux_mif_pll',12),
        ('SMC',3):('mif_mux_bus_pll',12),
        ('SMC',4):('mif_mux_aclk_mif_pll',12),
        ('SCI',0):('top_mux_ccore',12),
        ('SCI',1):('top_div_ccore',0),
        ('SCI',2):('ccore_mux_user',12),
    }
    if {key:(row['parameter'],row['payload_shift']) for key,row in bindings.items()} != expected_bindings:
        raise ValueError('Public producer binding assertions failed')
    start = next(row for row in writes if row['register']=='PSCDC_CTRL0')
    if re.sub(r'\s+','',start['expression']) != '0x40000000|(mif_pause<<31)':
        raise ValueError('Public producer start expression changed')
    if not re.search(r'while\s*\(pwrcal_readl\(PSCDC_CTRL0\)\s*&\s*0xC0000000\)\s*cpu_relax\(\)',body):
        raise ValueError('Public producer wait expression changed')
    gate_declarations = []
    for match in re.finditer(r'^#define\s+(CG_CTRL_VAL_DDRPHY[0-3])\s+\(\(void \*\)\(CMU_MIF([0-3])_BASE\s*\+\s*0x0A08\)\)', declarations,re.M):
        gate_declarations.append({'name':match[1], 'domain':'CMU_MIF'+match[2],
                                  'offset':0xa08,
                                  'source_line':declarations.count('\n',0,match.start())+1})
    if len(gate_declarations) != 4:
        raise ValueError('Public DDRPHY gate declarations changed')
    session = session.resolve()
    if not session.is_relative_to(vm.ROOT/'var'):
        raise ValueError('Require retained native session inside scratch')
    state_path = session/'cmu-queue-runtime-state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    queues = {}
    for name, expected, mask in (('queue',NATIVE_QUEUE_SHA256,255),
                                  ('mirror',NATIVE_MIRROR_SHA256,3)):
        path = Path(state[name]['source']).resolve()
        if not path.is_relative_to(session):
            raise ValueError('Native queue source outside selected session')
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != 32 or digest != expected or state[name]['sha256'] != digest:
            raise ValueError('Native queue readback identity mismatch')
        if state[name+'-valid-mask'] != mask or state[name]['qom_matches_actual_ap_readback'] is not True:
            raise ValueError('Native queue staging/readback mismatch')
        rows = []
        for index, word in enumerate(struct.unpack('<8I',raw)):
            if not mask & (1 << index):
                continue
            selector = (word >> 16) & 15
            candidates = [binding for binding in bindings.values()
                          if word & 0xffff0000 == int(binding['constant'],16)]
            rows.append({'index':index, 'raw':hex(word), 'bit31':word >> 31,
                         'selector_19_16':selector, 'low16':word & 65535,
                         'public_numeric_family_candidates':candidates,
                         'auto9_target_verified':False,
                         'execution_approved':False})
        queues[name] = {'source':str(path), 'sha256':digest, 'words':rows}
    request = state['last-queue-request']
    if request != 0x271:
        raise ValueError('Require selected original native start271 capture')
    return {
        'source':str(source.resolve()), 'sha256':PUBLIC_PSCDC_SHA256,
        'source_url':'https://github.com/krzk/linux-vendor-backup/blob/mokee/android-3.18-samsung-galaxy-s7-sm-g930f-exynos8890/drivers/soc/samsung/pwrcal/S5E8890/S5E8890-dfs.c',
        'header':str(header.resolve()), 'header_sha256':PUBLIC_PSCDC_HEADER_SHA256,
        'producer':'pscdc_trans', 'writes':writes,
        'queue_specific_bindings':list(bindings.values()),
        'public_control':{'write':start, 'start_mask':0x40000000,
                          'wait_mask':0xc0000000, 'pause_parameter_shift':31},
        'public_ddrphy_gate_declarations':gate_declarations,
        'native_state':str(state_path), 'native_state_sha256':vm.sha(state_path),
        'native_queues':queues,
        'corrections':{
            'word_10000_selector_19_16':(0x10000 >> 16) & 15,
            'word_10000_bit31':0,
            'selector2_is_queue_specific':bindings[('SMC',2)]['parameter'] != bindings[('SCI',2)]['parameter'],
            'auto9_a08_target_verified':False,
            'native_request_has_public_start_bit':bool(request & 0x40000000),
            'public_wait_mask_on_native_request':request & 0xc0000000,
        },
        'native_numeric_family_match_count':sum(bool(row['public_numeric_family_candidates'])
            for queue in queues.values() for row in queue['words']),
        'native_staged_word_count':sum(len(queue['words']) for queue in queues.values()),
        'auto9_execution_approved':False,
        'complete_native_transaction_explained':False,
        'scope':'Source-bound public producer and captured numeric comparison only; no Auto9 selector routing, register effects, start/completion handshake or DDRPHY gate identity is inferred',
    }


def phy_reference(source, output):

    if vm.sha(source) != PHY_REFERENCE_SHA256:
        raise ValueError('Cross-IP PHY reference identity mismatch')
    import pymupdf as fitz
    pages = []
    candidates = []
    with fitz.open(source) as document:
        page_count = len(document)
        for index,page in enumerate(document):
            text = page.get_text()
            if 'DDRPHY' in text or 'DVFS' in text or '0x0A08' in text or '0x0a08' in text:
                candidates.append({'pdf_page':index+1,'preview':text[:1200]})
            if 'DDRPHY' in text and re.search(r'0x0A08|0x0a08|DVFS[01]_CON6|DVFS_CON6',text):
                if len(pages) >= 12:
                    raise ValueError('Unexpected cross-IP reference coverage')
                target = output/f'phy-reference-page-{index+1}.png'
                page.get_pixmap(matrix=fitz.Matrix(1.3,1.3)).save(target)
                pages.append({'pdf_page':index+1,'text':text,'image':str(target)})
    return {'source':str(source),'sha256':PHY_REFERENCE_SHA256,'pages':pages,
            'page_count':page_count,'candidate_pages':candidates,
            'scope':'Rockchip RK3576 manufacturer TRM; matching offsets are cross-IP candidates only, not Auto9 identity, routing or PSCDC behavior'}


def runtime_dmc_contract(session):






    from acpm_firmware import original_image
    session = session.resolve()
    if not session.is_relative_to(vm.ROOT/'var'):
        raise ValueError('Require retained native session inside scratch')
    identity = json.loads((session/'acpm-runtime-state.json').read_text(encoding='utf-8'))
    source = Path(identity['source']).resolve()
    if not source.is_relative_to(session):
        raise ValueError('Runtime SRAM source outside selected session')
    raw = source.read_bytes()
    if len(raw) != 0x38000 or vm.sha(source) != identity['sha256']:
        raise ValueError('Native SRAM identity/size mismatch')
    original,meta = original_image()
    if raw[0x1e000:0x243cc] != original[0x1e000:0x243cc]:
        raise ValueError('Original DDR function/literal bytes changed')
    def u32(offset):
        if offset % 4 or not 0 <= offset <= len(raw)-4:
            raise ValueError('DDR runtime pointer outside SRAM')
        return struct.unpack_from('<I',raw,offset)[0]
                                                                  
    got = (u32(0x213e8)+0x210ee) & 0xffffffff
    result = []
    for literal in (0x213ec,0x213f0,0x213f4,0x213f8,0x213fc):
        offset=u32(literal)
        slot=(got+offset)&0xffffffff
        pointer=u32(slot)
        row={'literal':hex(literal),'got_offset':hex(offset),'slot':hex(slot),'value':hex(pointer)}
        if pointer % 4 == 0 and 0 <= pointer <= len(raw)-192:
            row['words']=[hex(u32(pointer+index*4)) for index in range(48)]
        result.append(row)
    dispatch = []
    for table_slot, count in ((0x4,26),(0,43)):
        pointer=u32(got+table_slot)
        functions=[]
        for index in range(count):
            entry=u32(pointer+index*4)
            if entry and (not entry&1 or not 0x1e000 <= entry-1 < 0x243cc):
                raise ValueError('DDR dispatch entry is not original Thumb code')
            functions.append({'offset':hex(index*4),'pointer':hex(entry),
                              'entry':hex(entry-1) if entry else None})
        dispatch.append({'slot':hex(got+table_slot),'table':hex(pointer),
                         'functions':functions})
    mmio_tables = []
                                                                           
                                                                         
    for routine, literal, pc, slot_literal in ((0x21d20,0x21edc,0x21d2c,0x21ee0),
                                               (0x2327c,0x23340,0x2328a,0x23344)):
        selected_got = (u32(literal)+pc)&0xffffffff
        if selected_got != got:
            raise ValueError('DDR MMIO reference does not resolve shared GOT')
        slot = (got+u32(slot_literal))&0xffffffff
        pointer = u32(slot)
        banks = [u32(pointer+4*i) for i in range(9)]
        if any(not 0xa0000000 <= value <= 0xbffffffc or value % 4 for value in banks):
            raise ValueError('DDR MMIO base outside original AP bridge')
        mmio_tables.append({'routine':hex(routine),'got_literal':hex(literal),
            'add_pc':hex(pc),'offset_literal':hex(slot_literal),'slot':hex(slot),
            'table':hex(pointer),'mcu_bases':[hex(value) for value in banks],
            'ap_bases':[hex(value-0x90000000) for value in banks]})
    training_refs = []
    for literal,pc in ((0x22a2c,0x22814),(0x22a30,0x22844),(0x22a34,0x22904),
                       (0x22c30,0x22a8e),(0x22c34,0x22b1e),(0x22c38,0x22b32)):
        pointer=(u32(literal)+pc)&0xffffffff
        training_refs.append({'literal':hex(literal),'add_pc':hex(pc),'table':hex(pointer)})
    if len({row['table'] for row in training_refs}) != 1:
        raise ValueError('Original PHY training references disagree')
    training_base = int(training_refs[0]['table'],16)
                                                                           
                                                                        
                                                                          
    training_rows = []
    for timing_set in range(2):
        locations = [(2*(20*timing_set+i+12)+6,2,'rw-a') for i in range(9)]
        locations += [(2*(20*timing_set+i+4)+4,2,'rw-b') for i in range(9)]
        locations += [(4*(10*timing_set+12)+i,2,'rw-tail') for i in (0,2)]
        locations += [(44*timing_set+92+2*i,2,'gate-a') for i in range(9)]
        locations += [(44*timing_set+110+2*i,2,'gate-b') for i in range(9)]
        locations += [(44*timing_set+i,4,'gate-tail') for i in (128,132)]
        for offset,size,role in locations:
            address=training_base+offset
            if address % size or not 0 <= address <= len(raw)-size:
                raise ValueError('PHY training offset outside SRAM')
            if raw[address:address+size] != original[address:address+size]:
                raise ValueError('Original PHY training offset changed')
            training_rows.append({'timing_set':timing_set,'role':role,'table_offset':hex(offset),
                'source':hex(address),'load_bytes':size,
                'register_offset':hex(int.from_bytes(raw[address:address+size],'little'))})
    return {'source':str(source),'sha256':identity['sha256'],'original_source':meta['source'],
            'original_sha256':meta['source_sha256'],'original_function_bytes_match':True,
            'got':hex(got),'slots':result,'dispatch_tables':dispatch,'mmio_tables':mmio_tables,
            'training_references':training_refs,'training_write_targets':training_rows,
            'scope':'Actual captured native DDR dispatch data, not a PSCDC opcode/routing/completion specification'}


class DynamicCalImage:





    def __init__(self, raw):
        self.elf = platform.Elf(raw)
        if struct.unpack_from('<H',raw,16)[0] != 3:
            raise ValueError('Require original shared-object ELF')
        dynamic = [row for row in self.elf.segments if row['type'] == 2]
        if len(dynamic) != 1 or dynamic[0]['file_size'] % 16:
            raise ValueError('Require one bounded PT_DYNAMIC')
        self.tags = {}
        terminated = False
        for tag,value in struct.iter_unpack('<qQ',self.elf.at(dynamic[0]['virtual'],dynamic[0]['file_size'])):
            if tag == 0:
                terminated = True
                break
            if tag != 1:
                if tag in self.tags:
                    raise ValueError('Ambiguous dynamic tag')
                self.tags[tag] = value
        if not terminated or any(tag not in self.tags for tag in (5,6,7,8,9,10,11)):
            raise ValueError('Missing bounded dynamic metadata')
        if self.tags.get(11) != 24 or self.tags.get(9) != 24 or self.tags.get(8,1) % 24:
            raise ValueError('Unsupported ELF dynamic record sizes')
        self.relocations = {}
        for address,info,addend in struct.iter_unpack('<QQq',self.elf.at(self.tags[7],self.tags[8])):
            if address in self.relocations:
                raise ValueError('Ambiguous dynamic relocation')
            self.relocations[address] = (info,addend)
        self.strings = self.elf.at(self.tags[5],self.tags[10])

    def u32(self, address):
        return struct.unpack('<I',self.elf.at(address,4))[0]

    def symbol(self, index):
        if not 0 <= index < 65536:
            raise ValueError('Dynamic symbol index outside analysis bound')
        name,info,other,section,value,size = struct.unpack('<IBBHQQ',self.elf.at(self.tags[6]+index*24,24))
        if name >= len(self.strings):
            raise ValueError('Dynamic symbol name outside string table')
        end = self.strings.find(b'\0',name)
        if end < 0:
            raise ValueError('Unterminated dynamic symbol')
        return {'name':self.strings[name:end].decode('ascii'),'value':value,'section':section,'size':size}

    def pointer(self, address):
        if address not in self.relocations:
            return struct.unpack('<Q',self.elf.at(address,8))[0]
        info,addend = self.relocations[address]
        kind,index = info & 0xffffffff,info >> 32
        if kind == 1027 and index == 0:                                             
            return addend
        if kind in (257,1025):                                                       
            symbol = self.symbol(index)
            if not symbol['section']:
                raise ValueError('Unresolved external pointer: '+symbol['name'])
            return symbol['value'] + addend
        raise ValueError('Unsupported pointer relocation type '+str(kind))

    def string(self, address):
        if address == 0:
            return None                                                  
        for row in self.elf.segments:
            delta = address-row['virtual']
            if row['type'] == 1 and 0 <= delta < row['file_size']:
                raw = self.elf.at(address,min(512,row['file_size']-delta))
                end = raw.find(b'\0')
                if end < 0:
                    raise ValueError('Unterminated CAL table string')
                return raw[:end].decode('ascii')
        raise ValueError('CAL string pointer not file-backed')


def qnx_register_contract(source):






    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != QNX_CAL_SHA256:
        raise ValueError('Original QNX CAL library identity mismatch')
    image = DynamicCalImage(raw)
                                                             
                                                                                  
    layouts = {'blocks':(0x8ef98,0x8efb0,40,256),
               'registers':(0x8ef78,0x8ef00,24,65536),
               'fields':(0x8eef0,0x8ef90,24,65536)}
    result = {}
    for name,(base_slot,count_slot,stride,maximum) in layouts.items():
        base = image.pointer(base_slot)
        count = image.u32(image.pointer(count_slot))
        if not 0 < count <= maximum:
            raise ValueError('CAL table count outside bound: '+name)
        image.elf.at(base,count*stride)
        rows = []
        for index in range(count):
            address = base+index*stride
            identifier = image.u32(address)
            if identifier & 0xffff != index:
                raise ValueError('CAL table index mismatch: '+name)
            row = {'index':index,'id':hex(identifier),'name':image.string(image.pointer(address+8)),
                   'table_address':hex(address)}
            if name == 'blocks':
                row.update(physical=struct.unpack('<Q',image.elf.at(address+16,8))[0],
                           size=image.u32(address+32))
            elif name == 'registers':
                offset,block_id = struct.unpack('<II',image.elf.at(address+16,8))
                block = block_id & 0xffff                                               
                if block >= len(result['blocks']):
                    raise ValueError('CAL register block index outside table')
                parent = result['blocks'][block]
                if offset >= parent['size']:
                    raise ValueError('CAL register offset outside block')
                row.update(offset=offset,block=block,block_id=hex(block_id),block_name=parent['name'],
                           physical=parent['physical']+offset)
            else:
                shift,width,_,register_id = struct.unpack('<BBHI',image.elf.at(address+16,8))
                register = register_id & 0xffff
                if not 1 <= width <= 32 or shift+width > 32 or register >= len(result['registers']):
                    raise ValueError('Invalid CAL field encoding')
                parent = result['registers'][register]
                row.update(shift=shift,width=width,register=register,register_id=hex(register_id),physical=parent['physical'])
            rows.append(row)
        result[name] = rows
                                                                      
                                                                          
    clocks = []
    node_layouts = (
        ('fixed_rate',0x1000000,0x8eec0,0x8efd8,64),
        ('fixed_factor',0x2000000,0x8ef88,0x8ee38,64),
        ('pll',0x3000000,0x8eee0,0x8eec8,112),
        ('mux',0x4000000,0x8ee88,0x8efa8,72),
        ('divider',0x5000000,0x8ef60,0x8ee58,56),
        ('gate',0x6000000,0x8eff0,0x8ee70,56),
        ('qch',0x7000000,0x8eef8,0x8ee78,72),
    )
    for kind,type_id,base_slot,count_slot,stride in node_layouts:
        base = image.pointer(base_slot)
        count = image.u32(image.pointer(count_slot))
        if not 0 < count <= 65536:
            raise ValueError('CAL clock count outside bound: '+kind)
        image.elf.at(base,count*stride)
        for index in range(count):
            address = base+index*stride
            identifier = image.u32(address)
            if identifier & 0xffff != index or identifier & 0xf000000 != type_id:
                raise ValueError('CAL clock node identity mismatch: '+kind)
            node = {'kind':kind,'id':hex(identifier),'table_address':hex(address),
                    'name':image.string(image.pointer(address+16)),
                    'parent_id':hex(image.u32(address+8)),'fields':{}}
                                                                        
                                                                           
                                                               
            members = [('member24',24),('member32',32),('member40',40)]
            if kind in ('fixed_rate','fixed_factor'):
                members = [('member32',32)]
                node['fixed_value'] = (image.u32(address+56) if kind == 'fixed_rate'
                                       else struct.unpack('<H',image.elf.at(address+56,2))[0])
            if kind == 'pll':
                members = [('locktime',24),('enable',32),('lock',40),
                           ('pdiv',84),('mdiv',88),('sdiv',92),('kdiv',96)]
            for member,offset in members:
                                                                        
                field_id = image.u32(address+offset) & 0xffff
                if field_id == 0xffff:
                    node['fields'][member] = None
                elif field_id >= len(result['fields']):
                    raise ValueError('CAL node field outside table: '+node['name'])
                else:
                    field = result['fields'][field_id]
                    node['fields'][member] = {key:field[key] for key in
                                              ('id','physical','shift','width','register')}
            if kind == 'mux':
                parent_pointer = image.pointer(address+56)
                parent_count = image.elf.at(address+64,1)[0]
                if not 0 < parent_count <= 32:
                    raise ValueError('CAL mux parent count outside bound')
                node['parent_ids'] = [hex(value) for value in struct.unpack(
                    '<'+'I'*parent_count,image.elf.at(parent_pointer,parent_count*4))]
            if kind in ('mux','divider'):
                                                                             
                                                                            
                                                                              
                                                                             
                config = node['fields']['member24']
                poll = node['fields']['member40']
                node['software_access'] = {
                    'setter':'0x50858', 'initializer':'0x53440',
                    'config_member':'member24', 'config_field':config,
                    'write_enabled':config is not None,
                    'clear_mask':((1 << config['width'])-1) << config['shift'] if config else None,
                    'input_value_masked':False,
                    'poll_member':'member40', 'poll_field':poll,
                    'poll_enabled':config is not None and poll is not None,
                    'poll_mask':1 << poll['shift'] if poll else None,
                    'poll_condition':'while nonzero' if config and poll else None,
                    'sleep_api':'usleep', 'sleep_argument_us':1,
                    'maximum_sleep_calls':100, 'timeout_return_i32':-110,
                    'member32_used_by_this_setter':False,
                    'physical_transition_latency_verified':False,
                    'pscdc_execution_semantics_verified':False,
                }
            clocks.append(node)
    names = {node['id']:node['name'] for node in clocks}
    for node in clocks:
        node['parent_name'] = names.get(node['parent_id'])
        if 'parent_ids' in node:
            node['parent_names'] = [names.get(parent) for parent in node['parent_ids']]
    return {'source':str(source),'sha256':QNX_CAL_SHA256,'tables':result,'clocks':clocks,
            'counts':{name:len(rows) for name,rows in result.items()},
            'clock_counts':{kind:sum(node['kind']==kind for node in clocks)
                            for kind,*_ in node_layouts},
            'limitations':['NULL SFR names are retained as null, not recovered identifiers',
                           'Clock member24/member32/member40 are raw struct coordinates, not interchangeable status/enable roles',
                           'MIF base in this CAL table is 0x1b400000; replica-port routing is not declared here',
                           'No PSCDC queue opcode, 0xa08 target, mirror routing or completion contract is established'],
            'scope':'Original QNX SFR/field declarations and mux/div write-poll software ABI, not PSCDC hardware execution, physical transition timing or runtime mapping proof'}


def clock_contract(source):
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PUBLIC_CLOCK_SHA256:
        raise ValueError('Pinned published Auto9 clock source identity mismatch')
    text = raw.decode('utf-8')
    definitions = {name:int(value,16) for name,value in
                   re.findall(r'^#define\s+(\w+)\s+(0x[0-9a-fA-F]+)\s*$',text,re.M)}
    divs = {name:{'parent':parent,'register':register,'offset':definitions[register],
                  'shift':int(shift),'width':int(width),'divisor_encoding':'field+1'}
            for name,parent,register,shift,width in re.findall(
                r'DIV\([^,]+,\s*"([^"]+)",\s*"([^"]+)",\s*(\w+),\s*(\d+),\s*(\d+)\)',text)}
    result = {}
    for name in ('mout_clkcmu_core_bus','mout_clkcmu_mif_switch'):
        match = re.search(r'PNAME\('+name+r'_p\)\s*=\s*\{([^}]+)\}',text)
        if not match:
            raise ValueError('Missing published parent array: '+name)
        parents = re.findall(r'"([^"]+)"',match[1])
        if len(parents) != 8:
            raise ValueError('Unexpected published mux parent count')
        result[name] = parents
    needed = set(parent for parents in result.values() for parent in parents if parent.startswith('dout_'))
    return {'source':str(source),'sha256':PUBLIC_CLOCK_SHA256,
            'url':'https://github.com/torvalds/linux/blob/master/drivers/clk/samsung/clk-exynosautov9.c',
            'git_blob_sha1':'507c92e09ccfe7feaee4a4915cd60ebe18179d56',
            'parents':result,'shared_dividers':{name:divs[name] for name in sorted(needed)},
            'core_divider':divs['dout_clkcmu_core_bus'],
            'unimplemented_gates':{'core_bus':{'offset':0x204c,'enable_bit':21},
                                   'mif_switch':{'offset':0x2028,'enable_bit':21}},
            'scope':'Primary published Auto9 topology/fields, not PSCDC FIFO ISA or physical reset/transition timing'}


def extraction_index():
    return {row['name'].removeprefix('./'): row for row in
            (json.loads(line) for line in (vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines())}


def restore_members(archive, output, expected):





    wanted = set(MEMBERS)
    found = []
    with tarfile.open(archive, 'r|gz', bufsize=1024*1024) as stream:
        for member in stream:
            name = member.name.removeprefix('./')
            if name not in wanted:
                continue
            parts = PurePosixPath(name)
            target = (output/Path(*parts.parts)).resolve()
            if parts.is_absolute() or '..' in parts.parts or not target.is_relative_to(output.resolve()):
                raise ValueError('Unsafe selected member path')
            row = expected[name]
            if not member.isfile() or member.size != row['size'] or member.size > 1024*1024:
                raise ValueError('Selected member type/size mismatch')
            if target.exists():
                raise ValueError('Preserve existing restored input')
            raw = stream.extractfile(member).read()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row['sha256']:
                raise ValueError('Original member SHA256 mismatch: '+name)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name+'.tmp')
            temporary.write_bytes(raw)
            os.replace(temporary, target)
            found.append({'name':name,'path':str(target),'bytes':len(raw),'sha256':digest})
            wanted.remove(name)
            print(json.dumps({'restored':name,'bytes':len(raw)}), flush=True)
            if not wanted:
                break
    if wanted:
        raise ValueError('Missing factory members: '+','.join(sorted(wanted)))
    return found


def inventory_file(path, expected, analysis_directory=None):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) != expected['size'] or digest != expected['sha256']:
        raise ValueError('Factory input identity mismatch: '+str(path))
    strings = [{'offset':hex(match.start()),'text':match.group().decode('ascii')}
               for match in re.finditer(rb'[ -~]{4,180}',raw)
               if re.search(rb'pscdc|cmu|mif|sequence|fifo|ddr|clk',match.group(),re.I)]
    refs = bootstrap.mailbox_accesses(raw, len(raw), horizon=256,
                                     address_begin=0x1b243c00,address_end=0x1b244000)
    analysis = None
    if analysis_directory is not None:
                                                                                       
        entry = 0x20 if path.name == 'el3_mon.bin' else 0x10
        wrapped = bytearray(platform.analysis_elf(raw))
        struct.pack_into('<Q',wrapped,24,entry)
        if wrapped[4096:] != raw:
            raise ValueError('Analysis wrapper changed original payload')
        analysis = analysis_directory/(path.name+'-analysis-only.elf')
        analysis.write_bytes(wrapped)
    return {'source':str(path),'sha256':digest,'bytes':len(raw),'first64_hex':raw[:64].hex(),
            'selected_strings':strings,'arm64_linear_candidates':refs,
            'analysis_elf':str(analysis) if analysis else None,
            'analysis_mapping':'Original file offset equals analysis VA; explicit A64 prefix, remaining region architectures unverified; never boot this wrapper',
            'scope':'Linear A64 candidate references, not proof of execution, architecture of all regions, or complete xrefs'}


def native_log_evidence():
    from acpm_firmware import scatter_load
    source = platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')
    raw, _ = scatter_load(source.read_bytes())
    result = []
    for producer, address, expected in ((0x1f5bc,0x243e0,b'mif0'),
                                         (0x1f668,0x243e8,b'pscdci'),
                                         (0x1f6a6,0x243f0,b'pscdcio')):
        data = raw[address:raw.index(b'\0',address)]
        if data != expected:
            raise ValueError('Native PSCDC log binding changed')
        result.append({'producer_offset':hex(producer),'mcu_string_address':hex(address),
                       'text':data.decode('ascii'),'text_hex':data.hex()})
    return {'source':str(source),'sha256':vm.sha(source),'logs':result,
            'meaning':'Original PSCDC label; special command meanings and mirror target port remain unverified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('restore','inventory','native-logs','clock-contract','qnx-contract','native-dmc','phy-reference','public-pscdc'))
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--inputs',type=Path)
    parser.add_argument('--public-source',type=Path)
    parser.add_argument('--public-header',type=Path)
    parser.add_argument('--qnx-library',type=Path)
    parser.add_argument('--session',type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():
        raise ValueError('Require new output directory within scratch')
    output.mkdir(parents=True)
    if args.operation == 'restore':
        archives = [path for path in (vm.ROOT/'var/inputs/packages').rglob('update.tgz') if path.stat().st_size > 1024*1024]
        if len(archives) != 1:
            raise ValueError('Require one full original CPU update archive')
        result = {'archive':str(archives[0]),'members':restore_members(archives[0],output,extraction_index()),
                  'whole_archive_digest_recomputed':False,'selected_member_digests_verified':True}
    elif args.operation == 'inventory':
        if args.inputs is None:
            raise ValueError('Require restored input directory')
        inputs = args.inputs.resolve()
        if not inputs.is_relative_to(vm.ROOT/'var'):
            raise ValueError('Require workspace retained inputs')
        index = extraction_index()
        result = {'files':[inventory_file(inputs/name,index[name],output) for name in MEMBERS],
                  'native_pscdc_labels':native_log_evidence()}
    elif args.operation == 'clock-contract':
        if args.public_source is None:
            raise ValueError('Require hash-pinned published Auto9 source')
        result = clock_contract(args.public_source.resolve())
    elif args.operation == 'qnx-contract':
        if args.qnx_library is None:
            raise ValueError('Require original QNX CAL library')
        result = qnx_register_contract(args.qnx_library.resolve())
    elif args.operation == 'native-dmc':
        if args.session is None:
            raise ValueError('Require retained native SRAM session')
        result = runtime_dmc_contract(args.session)
    elif args.operation == 'phy-reference':
        if args.public_source is None:
            raise ValueError('Require hash-pinned manufacturer PDF')
        result = phy_reference(args.public_source.resolve(),output)
    elif args.operation == 'public-pscdc':
        if args.public_source is None or args.public_header is None or args.session is None:
            raise ValueError('Require pinned producer/header and retained native session')
        result = public_pscdc_contract(args.public_source,args.public_header,args.session)
    else:
        result = native_log_evidence()
    vm.save(output/'result.json',result)
    print(str(output/'result.json'))


if __name__ == '__main__':
    main()
