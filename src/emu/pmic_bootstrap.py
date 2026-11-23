





import argparse
import hashlib
import json
import struct
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64_const import ARM64_OP_IMM, ARM64_OP_REG, ARM64_OP_MEM
import original_8704_vm as vm

BL2_SHA256 = 'a83ca762143fab69a0724843bad958f39916ec261f7a65e4de615d82345a5a44'


def constant_calls(raw, start, end):

    decoder = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    decoder.detail = True
    known = {}
    calls = []
    for instruction in decoder.disasm(raw[start:end], start):
        operands = instruction.operands
        if instruction.mnemonic == 'bl':
            arguments = [known.get('w'+str(i)) for i in range(4)]
            calls.append({'offset': instruction.address, 'target': operands[0].imm,
                          'arguments': arguments})
                                                                            
            for index in range(19):
                known.pop('w'+str(index), None)
            continue
        value = None
        destination = None
        if instruction.mnemonic == 'mov' and len(operands) == 2:
            if operands[0].type == ARM64_OP_REG and operands[1].type == ARM64_OP_IMM:
                name = instruction.reg_name(operands[0].reg)
                if name.startswith(('w', 'x')) and name[1:].isdigit():
                    destination = 'w'+name[1:]
                    value = operands[1].imm & 0xffffffff
        _, writes = instruction.regs_access()
        for register in writes:
            name = instruction.reg_name(register)
            if name.startswith(('w', 'x')) and name[1:].isdigit():
                known.pop('w'+name[1:], None)
        if destination is not None:
            known[destination] = value
    return calls


def contract_from_bytes(raw):
    if hashlib.sha256(raw).hexdigest() != BL2_SHA256:
        raise ValueError('Original BL2 identity mismatch; do not synthesize a PMIC policy')
    recovered = constant_calls(raw, 0x5030, 0x5060)
    recovered += constant_calls(raw, 0x10e34, 0x10e94)
    calls = {row['offset']: row for row in recovered if row['target'] == 0x49dc}
    expected = ((0x5044, 0x10560000, 15, 0xbd, 0x16, True),
                (0x505c, 0x10570000, 15, 0xbd, 0x16, True),
                (0x10e68, 0x10560000, 1, 0x55, 0x7c, False),
                (0x10e7c, 0x10560000, 1, 0x47, 0xfc, False),
                (0x10e90, 0x10570000, 1, 0x47, 0x7c, False))
    if set(calls) != {row[0] for row in expected}:
        raise ValueError('BL2 PMIC write-call inventory changed')
    writes = []
    for offset, base, device, register, value, conditional in expected:
        arguments = calls[offset]['arguments']
        if any(item is None for item in arguments):
            raise ValueError('A BL2 write argument was not proven constant')
                                                                            
                                                                    
        observed = (arguments[0], arguments[1] & 15,
                    arguments[2] & 255, arguments[3] & 255)
        if observed != (base, device, register, value):
            raise ValueError('BL2 constant PMIC arguments changed at '+hex(offset))
        writes.append({'file_offset': hex(offset), 'controller': (base-0x10560000)//0x10000,
                       'base': hex(base), 'device': device, 'register': register, 'value': value,
                       'condition': 'Selected virtual PMIC revision low3 == 0' if conditional else
                                    'Direct constant write in original BL2 sub_10E34'})
    handoff=mailbox_accesses(raw,0x2150,start=0x20b4,horizon=256)
    matches=[r for r in handoff if r['offset']=='0x2140']
    if len(matches)!=1 or matches[0]['address']!='0x10500088' or matches[0]['value']!=0x2d000:
        raise ValueError('Original BL2 FVMap handoff contract changed')
    selectors=[]
    for index,reg in enumerate((0x4a,0x4c,0x4e,0x50,0x52,0x48,0x48,0x4a,0x4c,0x4e,0x50,0x56)):
        offset=0x1c400+(0x2487c-0xb000)+44*index
        descriptor,initial,unused,minimum,step=struct.unpack_from('<5I',raw,offset)
        expected=((0x68 if index in (1,4) else 0x98)<<24)|0xff0100|reg
        if descriptor!=expected or initial!=0 or minimum!=450000 or step!=6250:
            raise ValueError('Original ACPM voltage selector descriptor changed')
        selectors.append({'index':index,'controller':int(index>5),'device':1,'register':reg,
            'mask':255,'metadata_high_byte':descriptor>>24,'minimum_uv':minimum,'step_uv':step,'initial_voltage_record':initial,
            'file_offset':hex(offset),'management_address':hex(0x2487c+44*index),
            'virtual_reset_byte':None,'reads_before_first_write':'unanswered',
            'analog_voltage_model_connected':False})
    return {'profile': 'virtual-bl2-rev0', 'bl2_sha256': BL2_SHA256,'voltage_selectors':selectors,
            'fvmap_handoff':{'address':'0x10500088','value':'0x2d000','write_file_offset':'0x2140',
                'table_builder':'BL2 sub_1EA0, called at0x2134 BEFORE handing off the pointer',
                'performed':False,'warning':'Do not hand off a zero/handmade table; original builder output is still required'},
            'virtual_revision_byte': 0, 'physical_pmic_identity_verified': False,
            'physical_reset_snapshot_verified': False, 'full_bl2_executed': False,
            'writes': writes,
            'scope': 'Five proven BL2 constant writes plus explicitly selected virtual revision byte; '
                     'twelve original ACPM selector registers retain actual writes only, analog model absent; '
                     'no arbitrary register defaults, OTP, FVMap or complete PMIC emulation'}


def contract():
    from vlm_platform import factory_file
    source = factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')
    return {'source': str(source), **contract_from_bytes(source.read_bytes())}



def mailbox_accesses(raw, limit=None, start=0, horizon=96,
                     address_begin=0x10500080,address_end=0x105000c0):

    decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM);decoder.detail=True;decoder.skipdata=True
    known={};matches=[]
    def register(instruction,operand):
        name=instruction.reg_name(operand)
        if name in ('wzr','xzr'):return -1
        if name.startswith(('w','x')) and name[1:].isdigit():return int(name[1:])
        return None
    for ins in decoder.disasm(raw[start:limit],start):
        if not ins.id:known.clear();continue
        known={k:v for k,v in known.items() if ins.address-v[1]<=horizon}
        ops=ins.operands;destination=None;value=None
        def constant(reg):
            n=register(ins,reg)
            return 0 if n==-1 else known[n][0] if n in known else None
        for operand in ops:
            if operand.type==ARM64_OP_MEM and not operand.mem.index:
                base=constant(operand.mem.base)
                address=base+operand.mem.disp if base is not None else None
                if address is not None and address_begin<=address<address_end:
                    matches.append({'offset':hex(ins.address),'mnemonic':ins.mnemonic,'operands':ins.op_str,
                        'address':hex(address),'value':constant(ops[0].reg) if ins.mnemonic.startswith(('str','stur')) else None})
        if len(ops)>=2 and ops[0].type==ARM64_OP_REG:
            destination=register(ins,ops[0].reg)
            if ins.mnemonic=='mov' and ops[1].type==ARM64_OP_IMM:value=ops[1].imm
            elif ins.mnemonic=='mov' and ops[1].type==ARM64_OP_REG:value=constant(ops[1].reg)
            elif ins.mnemonic=='movk' and ops[1].type==ARM64_OP_IMM:
                previous=constant(ops[0].reg);shift=ops[1].shift.value
                if previous is not None:value=(previous&~(0xffff<<shift))|(ops[1].imm<<shift)
            elif ins.mnemonic in ('adr','adrp') and ops[1].type==ARM64_OP_IMM:value=ops[1].imm
            elif ins.mnemonic in ('add','sub') and len(ops)==3 and ops[1].type==ARM64_OP_REG and ops[2].type==ARM64_OP_IMM:
                previous=constant(ops[1].reg)
                if previous is not None:value=previous+(1 if ins.mnemonic=='add' else -1)*(ops[2].imm<<ops[2].shift.value)
            elif ins.mnemonic=='ldr' and ops[1].type==ARM64_OP_IMM:
                width=4 if ins.reg_name(ops[0].reg).startswith('w') else 8;where=ops[1].imm
                if 0<=where<=len(raw)-width:value=int.from_bytes(raw[where:where+width],'little')
            if value is not None:value&=0xffffffff if ins.reg_name(ops[0].reg).startswith('w') else 0xffffffffffffffff
        _,writes=ins.regs_access()
        for reg in writes:known.pop(register(ins,reg),None)
        if destination is not None and destination>=0 and value is not None:known[destination]=(value,ins.address)
        if ins.mnemonic=='bl':
            for i in range(19):known.pop(i,None)
        elif ins.mnemonic.startswith(('b','cb','tb')) or ins.mnemonic in ('ret','eret'):known.clear()
    return matches


def mailbox_inventory():
    from vlm_platform import factory_file,original
    result=[]
    for source,limit in ((factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin'),0x17400),
                         (original('lk-boot/lk.bin'),None)):
        raw=source.read_bytes()
        result.append({'source':str(source),'sha256':hashlib.sha256(raw).hexdigest(),
            'bytes_scanned':len(raw) if limit is None else limit,'candidates':mailbox_accesses(raw,limit),
            'scope':'Short-horizon static constant candidates; verify in original function before making a handoff contract'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mailbox-inventory',action='store_true')
    parser.add_argument('--cmu-queue-inventory',action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():
        raise ValueError('Require new scratch output directory')
    data = contract()
    output.mkdir(parents=True)
    vm.save(output/'contract.json', data)
    if args.mailbox_inventory:vm.save(output/'mailbox-inventory.json',mailbox_inventory())
    if args.cmu_queue_inventory:
        source=Path(data['source']);raw=source.read_bytes()
        vm.save(output/'cmu-queue-inventory.json',{'source':str(source),'sha256':vm.sha(source),
            'candidates':mailbox_accesses(raw,0x17400,horizon=256,
                address_begin=0x1b243c00,address_end=0x1b243f00),
            'scope':'Static MOV/MOVK/ADR/ADD candidates, not exhaustive xrefs or observed execution'})
    print(json.dumps({'profile': data['profile'], 'writes': len(data['writes']),
                      'physical_pmic_identity_verified': False, 'full_bl2_executed': False}))


if __name__ == '__main__':
    main()
