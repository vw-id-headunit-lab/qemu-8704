
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm
import qnx_dp0_platform as dp


def verify(session):
    stages=runpy.run_path(str(Path(__file__).with_name('qnx-screen-stage2.py')))
    launch=json.loads((session/'launch.json').read_bytes())
    if launch['input_sha256']!=stages['ORIGINAL_HV']:
        raise ValueError('Unverified native vLM')
    logs=(session/'tcg.log').read_bytes()
    pattern=(rb'Auto9 VM2 external abort: pc=(0x[\da-f]+) va=(0x[\da-f]+) '
             rb'pa=0x18a80010 ttbr0=(0x[\da-f]+) access=1 size=4 result=2')
    found=list(re.finditer(pattern,logs))
    if len(found)!=1:
        raise ValueError('Expected exactly one original VM2 LINK lane-count write fault')
    match=found[0]
    pc,mmio,ttbr0=(int(x,16) for x in match.groups())
    states=json.loads((session/'cpu-system-registers.json').read_bytes())['cpus']
    vm2=[c['system'] for c in states if int(c['system'].get('VTTBR_EL2','0'),16)>>48==2 and
         int(c['system'].get('HCR_EL2','0'),16)&1]
    tcrs={int(c['TCR_EL1'],16) for c in vm2}
    roots={int(c['VTTBR_EL2'],16) for c in vm2}
    if len(tcrs)!=1 or len(roots)!=1:
        raise ValueError('No consistent original VM2 context after stop')
    tcr=tcrs.pop()
    if tcr&63!=25 or (tcr>>14)&3 or pc>>39:
        raise ValueError('Original QNX TTBR0 is not 39-bit, 4KiB')
    vttbr=roots.pop()
    hyp=(session/'hypervisor-ram.bin').read_bytes()
    if len(hyp)!=stages['HYP_BYTES']:
        raise ValueError('Unexpected original monitor RAM range')
    qnx=(session/'qnx-native-runtime/qnx-runtime-e0000000.bin').read_bytes()
    hb,qb=stages['HYP_BASE'],0xe0000000
    vtcr=struct.unpack_from('<I',hyp,stages['VTCR_WORD']-hb)[0]

    def read(data,base,pa):
        if pa%8 or not base<=pa<=base+len(data)-8:
            raise ValueError(f'Uncaptured translation table word at {pa:#x}')
        return struct.unpack_from('<Q',data,pa-base)[0]

    def stage2(ipa):
        return stages['map_ipa'](vttbr,vtcr,ipa,lambda pa:read(hyp,hb,pa))

    def stage1(address):
        table=ttbr0&0xfffffffff000
        walk=[]
        for shift in (30,21,12):
            pte=table+((address>>shift)&0x1ff)*8
            pa,_,s2=stage2(pte)
            word=read(qnx,qb,pa)
            walk.append(dict(pte_ipa=hex(pte),pte_pa=hex(pa),word=hex(word),stage2=s2))
            kind=word&3
            if (shift==12 and kind!=3) or (shift!=12 and kind not in (1,3)):
                raise ValueError('Absent original VM2 stage-1 PTE')
            table=word&0xfffffffff000
            if shift==12 or kind==1:
                return ((table&~((1<<shift)-1))|(address&((1<<shift)-1))),walk
        raise ValueError('No stage-1 leaf')

    code_ipa,walk=stage1(pc)
    code_pa,_,walk2=stage2(code_ipa)
    source=dp.factory_file(dp.SOURCE).resolve()
    load=[arg for arg in launch['command'] if arg.startswith(f'loader,file={source},addr=0x91080000,')]
    if len(load)!=1:
        raise ValueError('Cannot bind mapped instructions to the original IFS loader')
    off=code_pa-0x91080000-dp.MEMBER_OFFSET
    if off!=0x16ffc:
        raise ValueError(f'Unexpected source instruction offset: {off:#x}')
    with source.open('rb') as src:
        src.seek(dp.MEMBER_OFFSET)
        raw=src.read(dp.MEMBER_BYTES)
    insn=dp.Elf(raw).at(off,4)
    if hashlib.sha256(raw).hexdigest()!=dp.MEMBER_SHA256 or insn!=bytes.fromhex('011000b9'):
        raise ValueError('Original lane-count store does not match reviewed bytes')
    return dict(vmid=2,original_fault=match[0].decode('ascii'),pc=hex(pc),fault_va=hex(mmio),
                original_ttbr0=hex(ttbr0),original_vttbr=hex(vttbr),original_tcr=hex(tcr),
                pc_ipa=hex(code_ipa),pc_pa=hex(code_pa),pc_stage1=walk,pc_stage2=walk2,
                ifs_member_sha256=dp.MEMBER_SHA256,member_offset=hex(off),source_bytes=insn.hex(),
                instruction='str w1, [x0, #0x10]',
                scope='Actual stopped page tables and original IFS-backed source bytes; not a direct instruction-byte RAM dump')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('session',type=Path)
    p.add_argument('output',type=Path)
    a=p.parse_args()
    proof=verify(a.session.resolve(strict=True))
    vm.save(a.output,proof)
    print(json.dumps({k:proof[k] for k in ('pc','pc_pa','member_offset','instruction')}))
