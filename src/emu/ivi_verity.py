
import argparse
import hashlib
from pathlib import Path
import re

import original_8704_vm as vm
from vlm_platform import factory_file, parse_dtb

BLOCK=4096
DIGEST=32
FANOUT=BLOCK//DIGEST
DT='ivi/icas3cn-gp_b0_zero.dtb'
DATA='ivi/icas3cn-gp-image-euto-v9.ext4'
HASH='ivi/ivi_hash.img'


def contract(dtb):
    args=parse_dtb(dtb)['/chosen']['bootargs'].rstrip(b'\0').decode('ascii')
    m=re.search(r'dm-mod\.create="dm-verity,,,ro, 0 (\d+) verity 1 (/dev/sda\d+) (/dev/sda\d+) 4096 4096 (\d+) (\d+) sha256 ([0-9a-f]{64}) ([0-9a-f]{64}) 1 panic_on_corruption"',args)
    if not m:raise ValueError('Missing exact original dm-verity v1 contract')
    sectors,ddev,hdev,count,start,root,salt=m.groups()
    if int(count)<2 or int(sectors)!=int(count)*8:
        raise ValueError('Original dm target extent does not match protected blocks')
    return dict(data_device=ddev,hash_device=hdev,data_blocks=int(count),
                hash_start_block=int(start),root_hash=root,salt=salt,
                block_bytes=BLOCK,algorithm='sha256',version=1,panic_on_corruption=True)


def verify(data, hashes, count, start, root, salt):
    if count<2 or start<0 or len(root)!=DIGEST or len(salt)!=DIGEST:
        raise ValueError('Unsupported dm-verity tree dimensions')
    levels=[]
    n=count
    while n>1:
        n=(n+FANOUT-1)//FANOUT
        levels.append(n)
    offsets=[]
    cursor=start
    for n in reversed(levels):
        offsets.insert(0,cursor)
        cursor+=n
    if data.stat().st_size!=count*BLOCK or hashes.stat().st_size<cursor*BLOCK:
        raise ValueError('Original partition payload extent does not cover the hash tree')
    ctx=hashlib.sha256(salt)
    def digest(block):
        h=ctx.copy()
        h.update(block)
        return h.digest()

    with hashes.open('rb') as hf:
        hf.seek(start*BLOCK)
        packed=hf.read((cursor-start)*BLOCK)
    if len(packed)!=(cursor-start)*BLOCK:
        raise ValueError('Truncated hash partition read')
    tree=memoryview(packed)
    def block(level,index):
        off=(offsets[level]-start+index)*BLOCK
        return tree[off:off+BLOCK]
    if digest(block(len(levels)-1,0))!=root:
        raise ValueError('Root hash from original bootargs does not match hash partition')
    for level in reversed(range(1,len(levels))):
        for i in range(levels[level-1]):
            idx=i%FANOUT*DIGEST
            if digest(block(level-1,i))!=block(level,i//FANOUT)[idx:idx+DIGEST]:
                raise ValueError(f'Hash-tree mismatch at level {level-1} block {i}')
                                                                         
                                                                    
    verified=0
    with data.open('rb') as f:
        for leaf in range(levels[0]):
            n=min(count-verified,FANOUT)
            chunk=memoryview(f.read(n*BLOCK))
            if len(chunk)!=n*BLOCK:raise ValueError('Truncated protected data read')
            expected=block(0,leaf)
            for i in range(n):
                if digest(chunk[i*BLOCK:(i+1)*BLOCK])!=expected[i*DIGEST:(i+1)*DIGEST]:
                    raise ValueError(f'Verity data mismatch at original block {verified+i}')
            verified+=n
        if f.read(1):raise ValueError('Extra data outside original partition extent')
    if verified!=count:raise ValueError('Incomplete verification')
    return {'verified_data_blocks':verified,'verified_hash_blocks':sum(levels),
            'hash_levels_from_leaves':levels,'hash_offsets_from_leaves':offsets,
            'unverified_trailing_hash_partition_bytes':hashes.stat().st_size-cursor*BLOCK}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    out=a.output.resolve()
    if out.exists() or not out.is_relative_to(vm.ROOT/'var'):
        raise ValueError('Require a new project scratch output directory')
    out.mkdir(parents=True)
    dt,data,hashes=(factory_file(rel) for rel in (DT,DATA,HASH))
    spec=contract(dt.read_bytes())
    vm.save(out/'contract.json',dict(spec,dt=str(dt),data=str(data),hashes=str(hashes)))
    try:
        verified=verify(data,hashes,spec['data_blocks'],spec['hash_start_block'],
                        bytes.fromhex(spec['root_hash']),bytes.fromhex(spec['salt']))
    except ValueError as exc:
        vm.save(out/'result.json',{'verified':False,'error':str(exc)})
        raise
    vm.save(out/'result.json',dict(verified,verified=True,original_archive_identities_checked=True,
                                  original_payloads_written=False,disk_boot_verified=False))
    print(str(out/'result.json'))


if __name__=='__main__':main()

