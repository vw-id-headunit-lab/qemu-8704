





import argparse
import ast
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import original_8704_vm as vm
import vlm_platform as platform


def parse_ldfw(raw):
    if not raw:raise ValueError('Empty firmware container')
    rows=[];offset=0
    while offset<len(raw):
        if len(rows)>=64:raise ValueError('Excessive firmware count')
        if len(raw)-offset<64:raise ValueError('Truncated firmware header')
        words=struct.unpack_from('<12I',raw,offset)
        if words[0]!=0x10adab1e:raise ValueError('Unexpected firmware magic at '+hex(offset))
        size=words[1]
        if size<4096 or size%4096 or size>len(raw)-offset:raise ValueError('Invalid bounded firmware size')
        chunk=raw[offset:offset+size];name=chunk[48:64].split(b'\0',1)[0].decode('ascii')
        row=dict(zip(('magic','size','init_entry','entry_point','suspend_entry','resume_entry',
            'start_smc_id','version','set_runtime_entry','reserved0','encoding_flag','reserved2'),words))
        if not name:raise ValueError('Empty firmware name')
        row['entries_within_container']=all(row[k]<size for k in ('init_entry','entry_point','suspend_entry','resume_entry','set_runtime_entry'))
        start,end=struct.unpack_from('<2I',chunk,0x44)
        row.update(index=len(rows),offset=offset,name=name,sha256=hashlib.sha256(chunk).hexdigest(),
            candidate_crypto_start=start,candidate_crypto_end=end,
            crypto_region_bounds_valid=0x100<=start<end<=size and (end-start)%16==0)
        rows.append(row);offset+=size
        if name=='tail_fw':
            if any(b not in (0,255) for b in raw[offset:]):raise ValueError('Nonpadding bytes after tail firmware')
            return {'entries':rows,'consumed_bytes':offset,'trailing_padding_bytes':len(raw)-offset}
    return {'entries':rows,'consumed_bytes':offset,'trailing_padding_bytes':0}


def public_source_parameters(path):

    found={}
    for statement in ast.parse(path.read_text(encoding='utf-8')).body:
        if not isinstance(statement,ast.Assign) or len(statement.targets)!=1:continue
        target=statement.targets[0]
        if not isinstance(target,ast.Name) or target.id not in ('MASTER_KEY','MASTER_IV'):continue
        value=statement.value
        if not (isinstance(value,ast.Call) and isinstance(value.func,ast.Attribute)
            and isinstance(value.func.value,ast.Name) and value.func.value.id=='bytes'
            and value.func.attr=='fromhex' and len(value.args)==1 and not value.keywords
            and isinstance(value.args[0],ast.Constant) and isinstance(value.args[0].value,str)):
            raise ValueError('Require literal bytes.fromhex parameters')
        if target.id in found:raise ValueError('Duplicate public source parameter')
        found[target.id]=bytes.fromhex(value.args[0].value)
    if len(found.get('MASTER_KEY',b''))!=32 or len(found.get('MASTER_IV',b''))!=16:
        raise ValueError('Missing bounded public source parameters')
    return found['MASTER_KEY'],found['MASTER_IV']


def entropy(raw):
    return -sum((n/len(raw))*math.log2(n/len(raw)) for n in Counter(raw).values()) if raw else 0


def candidate_decode(chunk,row,key_seed,iv_seed):
    from Crypto.Cipher import AES
    if not row['crypto_region_bounds_valid']:raise ValueError('Unverified crypto bounds do not fit this firmware')
    if not 0<=row['init_entry']<=len(chunk)-4:raise ValueError('Candidate init entry is not file-backed')
    key=AES.new(key_seed,AES.MODE_CBC,iv_seed).decrypt(chunk[0x4c:0x6c])
    iv=bytes(a^b for a,b in zip(iv_seed,chunk[0xec:0xfc]))
    start,end=row['candidate_crypto_start'],row['candidate_crypto_end']
    decoded=AES.new(key,AES.MODE_CBC,iv).decrypt(chunk[start:end])
    result=chunk[:start]+decoded+chunk[end:]
    entry=row['init_entry'];decoder=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
    observations={'body_entropy_before':entropy(chunk[start:end]),'body_entropy_after':entropy(decoded),
        'printable_runs_12_before':len(re.findall(rb'[\x20-\x7e]{12,}',chunk[start:end])),
        'printable_runs_12_after':len(re.findall(rb'[\x20-\x7e]{12,}',decoded)),
        'candidate_init_instructions':[{'address':hex(i.address),'mnemonic':i.mnemonic,'operands':i.op_str}
            for i in decoder.disasm(result[entry:entry+96],entry)],
        'decryption_verified':False,'firmware_executed':False,'candidate_usable_for_boot':False,
        'scope':'One public Exynos9830 parameter candidate only; Auto9 applicability unverified'}
    return result,observations


def inventory(output,public_key_source=None):
    source=platform.original('boot-binary/ldfw.bin');raw=source.read_bytes();report=parse_ldfw(raw)
    report.update(source=str(source),sha256=vm.sha(source),decryption_verified=False,firmware_executed=False,
        header_source='https://github.com/u-boot/u-boot/blob/master/board/samsung/e850-96/fw.c')
    parameters=public_source_parameters(public_key_source) if public_key_source else None
    if parameters:report['public_source']={'path':str(public_key_source.resolve()),'sha256':vm.sha(public_key_source),
        'declared_soc':'Exynos9830, not verified ExynosAuto9'}
    for row in report['entries']:
        chunk=raw[row['offset']:row['offset']+row['size']]
        name=f"{row['index']:02d}-{re.sub('[^A-Za-z0-9_-]','_',row['name'])}"
        path=output/(name+'.original-container.bin');path.write_bytes(chunk);row['path']=str(path)
        if parameters and row['encoding_flag']:
            candidate,observations=candidate_decode(chunk,row,*parameters)
            path=output/(name+'.unverified-candidate.bin');path.write_bytes(candidate)
            row['candidate']={**observations,'path':str(path),'sha256':vm.sha(path)}
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--public-key-source',type=Path)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True);result=inventory(output,args.public_key_source);vm.save(output/'result.json',result)
    print(json.dumps({'firmware_count':len(result['entries']),'decryption_verified':False,'firmware_executed':False}))


if __name__=='__main__':main()
