
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import urllib.request
import urllib.error
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
import original_8704_vm as vm
import vlm_platform as platform

LK_SHA='55e83063c6125a1100e15905d1596df60130df9ec1a62141a3e1f27b56fa40c3'
PMU_HANDOFF=0x10460840


def original_contract():
    source=platform.original('lk-boot/lk.bin');raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=LK_SHA:raise ValueError('Original LK identity mismatch')
    window=raw[0x3b5ac:0x3b5c4]
    expected=bytes.fromhex('010881d202a0b452c108a2f2200040b90000022a200000b9')
    if window!=expected:raise ValueError('Original handoff producer instructions changed')
                                                                          
                                                                         
    first,second=struct.unpack_from('<QQ',raw,0x79bb0)
    if first!=second or first-0x79bb0!=0xe5e00000:raise ValueError('Original LK linked pointer contract mismatch')
    return {'source':str(source),'sha256':LK_SHA,'producer':'0x3b59c','write_instruction':'0x3b5c0',
            'producer_bytes':window.hex(),'expression':'PMU0840 = PMU0840 | 0xa5000000',
            'predecessors':['0x3b548 environment save attempt','0x535f0 opens scsissu, sends START_STOP through50378'],
            'ssu_callback':'Original UFS registration52974->506D8 installs52554 at device+288',
            'scsi_cdb':'1b000000300000000000000000000000','device_lun':208,
            'guarded_caller':'5CB50 checks27FC50 before calling producer, clears it afterward',
            'other_caller':'472F0 thunk','linked_address_from_initialized_list':hex(first-0x79bb0),
            'runtime_physical_link_address_verified':False,'producer_executed':False,
            'prior_scan_correction':'MOV W2,#-0x5b000000 represents unsignedA5000000; positive-only constant matching missed it',
            'limits':'Original producer unconditionally ORs marker after returned calls; marker alone does not prove healthy storage or complete LK boot'}


def fetch_public(output):
    url='https://raw.githubusercontent.com/torvalds/linux/master/drivers/ufs/core/ufshcd.c'
    transport=url;blob=None
    try:
        with urllib.request.urlopen(url,timeout=30) as response:raw=response.read(2000000)
    except urllib.error.HTTPError as e:
        if e.code!=429:raise
        transport='https://api.github.com/repos/torvalds/linux/contents/drivers/ufs/core/ufshcd.c?ref=master'
        with urllib.request.urlopen(transport,timeout=30) as response:body=response.read(2000000)
        if len(body)>=2000000:raise ValueError('Primary API source exceeds bound')
        data=json.loads(body)
        if data.get('path')!='drivers/ufs/core/ufshcd.c' or data.get('encoding')!='base64':raise ValueError('Unexpected primary source response')
        raw=base64.b64decode(data['content']);blob=data['sha']
        if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=blob:raise ValueError('Primary Git blob identity mismatch')
    if len(raw)>=2000000:raise ValueError('Primary source exceeds download bound')
    text=raw.decode('utf-8')
    if 'UFSHCD_QUIRK_PRDT_BYTE_GRAN' not in text or 'cpu_to_le16(sg_entries * ufshcd_sg_entry_size(hba))' not in text:
        raise ValueError('Primary UFS byte-unit source contract changed')
    source=output/'ufshcd.c';source.write_bytes(raw)
    return {'url':url,'transport':transport,'git_blob_sha1':blob,'path':str(source),'sha256':vm.sha(source),'bytes':len(raw),
            'scope':'Retrieved Linux software unit contract; no claim about undocumented physical register timing'}


def entropy(raw):
    if not raw:return 0
    return -sum((n/len(raw))*math.log2(n/len(raw)) for n in [raw.count(i) for i in range(256)] if n)


def scan_a64(raw,base=0,smc_wrapper=None):






    decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM);decoder.detail=True;decoder.skipdata=True
    known={};accesses=[];calls=[];magic=[];instructions=0
    def regname(number):
        name=decoder.reg_name(number)
        if re.fullmatch('[xw][0-9]+',name):return int(name[1:])
        return name
    def value(op):
        if op.type==2:return op.imm
        if op.type==1:
            name=decoder.reg_name(op.reg)
            if name in ('xzr','wzr'):return 0
            v=known.get(regname(op.reg))
            if v is not None:return v&((1<<(32 if name.startswith('w') else 64))-1)
        return None
    for ins in decoder.disasm(raw,base):
        if not ins.id:known.clear();continue
        instructions+=1;ops=ins.operands;result=None;destination=None
        if ops and ops[0].type==1:destination=regname(ops[0].reg)
        if ins.mnemonic in ('mov','movz','movn') and len(ops)==2:
            result=value(ops[1])
            if result is not None:
                if ins.mnemonic!='mov':result<<=ops[1].shift.value
                if ins.mnemonic=='movn':result=~result
        elif ins.mnemonic=='movk' and len(ops)==2 and destination in known:
            shift=ops[1].shift.value
            result=(known[destination]&~(0xffff<<shift))|((ops[1].imm&0xffff)<<shift)
        elif ins.mnemonic in ('adr','adrp') and len(ops)==2:result=ops[1].imm
        elif ins.mnemonic in ('add','sub','orr','and','lsl') and len(ops)==3:
            a,b=value(ops[1]),value(ops[2])
            if a is not None and b is not None:
                if ins.mnemonic!='lsl':b<<=ops[2].shift.value
                if ins.mnemonic=='lsl':
                    bits=32 if decoder.reg_name(ops[0].reg).startswith('w') else 64
                    result=a<<(b&(bits-1)) if ops[2].type==1 else a<<b if 0<=b<bits else None
                else:result={'add':lambda:a+b,'sub':lambda:a-b,'orr':lambda:a|b,'and':lambda:a&b}[ins.mnemonic]()
        elif ins.mnemonic=='ldr' and len(ops)==2 and ops[1].type==2:
            address=ops[1].imm-base;size=4 if decoder.reg_name(ops[0].reg).startswith('w') else 8
            if 0<=address<=len(raw)-size:result=int.from_bytes(raw[address:address+size],'little')
        for op in ops:
            if op.type!=3:continue
            address=known.get(regname(op.mem.base))
            if op.mem.index:continue
            if address is None:continue
            address+=op.mem.disp
            if address==PMU_HANDOFF:
                accesses.append({'address':hex(ins.address),'bytes':ins.bytes.hex(),'mnemonic':ins.mnemonic,'operands':ins.op_str,
                                 'target':hex(address),'value':hex(value(ops[0])) if value(ops[0]) is not None else None})
        if ins.mnemonic=='smc' or (ins.mnemonic=='bl' and smc_wrapper is not None and ops[0].imm==smc_wrapper):
            calls.append({'address':hex(ins.address),'bytes':ins.bytes.hex(),'mnemonic':ins.mnemonic,
                          'arguments':{f'x{i}':hex(known[i]&((1<<64)-1)) if i in known else None for i in range(4)}})
        _,written=ins.regs_access()
        for r in written:known.pop(regname(r),None)
        if result is not None and isinstance(destination,int):
            result&=(1<<(32 if decoder.reg_name(ops[0].reg).startswith('w') else 64))-1
            known[destination]=result
            if result&0xff000000==0xa5000000:
                magic.append({'address':hex(ins.address),'mnemonic':ins.mnemonic,'operands':ins.op_str,'value':hex(result)})
        if ins.mnemonic in ('bl','blr'):
            known={r:v for r,v in known.items() if isinstance(r,int) and 19<=r<=28}
        elif ins.mnemonic.startswith(('b','cb','tb')) or ins.mnemonic in ('ret','eret','smc'):
            known.clear()
    return {'decoded_instructions':instructions,'handoff_access_candidates':accesses,'smc_candidates':calls,
            'a5_value_candidates':magic,'scope':'Local Capstone straight-line candidates, NOT REA or executed firmware; not exhaustive control/dataflow'}


def inventory():
    inputs=[('LK',platform.original('lk-boot/lk.bin')),
            ('SBL1',platform.factory_file('boot-binary/S5AHR80.evt1.sbl1.bin')),
            ('BL2',platform.factory_file('boot-binary/SADK_EVT1_EXYNOS_AUTOV9_bl2.bin')),
            ('TBASE',platform.factory_file('boot-binary/t-base.img.raw')),
            ('LDFW',platform.original('boot-binary/ldfw.bin'))]
    rows=[]
    for kind,path in inputs:
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if kind=='LK' and digest!=LK_SHA:raise ValueError('Original LK identity mismatch')
        strings=[{'offset':hex(m.start()),'text':m.group().decode('ascii')} for m in re.finditer(rb'[\x20-\x7e]{6,160}',raw)
                 if re.search(rb'ufs|smc|secure monitor|boot.*(done|complete)|el3|sfr|swp',m.group(),re.I)]
        row={'kind':kind,'path':str(path),'sha256':digest,'bytes':len(raw),'header64':raw[:64].hex(),
             'entropy':entropy(raw),'strings':strings}
        row['linear_a64_candidates']=scan_a64(raw,smc_wrapper=0x410a8 if kind=='LK' else None)
        rows.append(row)
    return {'inputs':rows,'factory_images_modified':False,'boot_executed':False,
            'scope':'Only these five hash-verified package inputs; encrypted/mixed data can be decoded as false A64 candidates'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fetch-public',action='store_true')
    a=p.parse_args();out=a.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new scratch output')
    out.mkdir(parents=True);vm.save(out/'original-contract.json',original_contract())
    if a.fetch_public:vm.save(out/'public-source.json',fetch_public(out))
    data=inventory();vm.save(out/'result.json',data)
    print(str(out/'result.json'))


if __name__=='__main__':main()
