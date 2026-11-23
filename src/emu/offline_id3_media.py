




import hashlib,json,math
from pathlib import Path
from dissect.extfs import ExtFS
from linux_hmi_userdata import _Slot,_export
from original_8704_vm import sha

PATH='/hmi/config/diagApi.properties'
ORIGINAL_SHA='55be7269c0e27872b76e7c92d4537ca6ef6b8ddfad9bc80544811ffa90c297b0'

def masked_file(raw,mask,value):
    if hashlib.sha256(raw).hexdigest()!=ORIGINAL_SHA:
        raise ValueError('Only the hash-pinned original 25-byte override file is admitted')
    result=raw
    for name,head in [('coding.mask',mask),('coding.value',value)]:
        old=name.encode()+b'='+b'0'*50+b'\n'
        new=name.encode()+b'='+head.encode()+b'0'*36+b'\n'
        if len(head)!=14 or result.count(old)!=1:raise ValueError('Original coding layout differs')
        result=result.replace(old,new,1)
    if len(result)!=len(raw):raise ValueError('Known bits must not change file geometry')
    return result

def apply_leaf(nbd,first,length,original,changed):
    if len(original)!=7909 or len(changed)!=len(original):raise ValueError('Unexpected HMI property size')
    fs=ExtFS(_Slot(nbd,first,length));node=fs.get(PATH)
    current=node.open().read()
    if fs.block_size!=4096 or node.filetype!=0x8000 or current not in (original,changed):
        raise ValueError('Require the original regular file in the private view')
    inode=node.inode.dumps();attrs=repr(node.xattr);runs=node.dataruns()
    blocks=[block+i for block,count in runs if block is not None for i in range(count)]
    if any(block is None for block,count in runs) or len(blocks)!=math.ceil(len(original)/4096):
        raise ValueError('Only exact initialized original file extents are admitted')
    rows=[];pos=0
    for block in blocks:
        start=first+block*4096
        if not first<=start<start+4096<=first+length:raise ValueError('File block outside original /vw partition')
        before=nbd.command(0,start,4096);count=min(4096,len(original)-pos)
        if before[:count]!=current[pos:pos+count]:raise ValueError('Extent/file readback mismatch')
        after=changed[pos:pos+count]+before[count:]
        wrote=before!=after
        if wrote:nbd.command(1,start,4096,after)
        if nbd.command(0,start,4096)!=after:raise ValueError('Private block write readback mismatch')
        rows.append({'offset':start,'bytes':4096,'write_executed':wrote,'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest()})
        pos+=count
    nbd.command(3)
    check=ExtFS(_Slot(nbd,first,length)).get(PATH)
    if check.open().read()!=changed or check.inode.dumps()!=inode or repr(check.xattr)!=attrs:
        raise ValueError('Private known-bit file or unchanged metadata verification failed')
    return {'guest_path':'/vw'+PATH,'original_file_sha256':hashlib.sha256(original).hexdigest(),
            'private_file_sha256':hashlib.sha256(changed).hexdigest(),'bytes':len(changed),
            'blocks':rows,'inode_and_xattrs_unchanged':True,'xattrs':attrs,
            'unknown_bits_not_overridden':True,'resolution_changed':False,
            'resource_key_forced':False,'factory_security_claimed':False,
            'scope':'Private pre-boot configuration input only; NOT runtime selection/GUI acceptance'}

def provision(result,media,out,original,changed):
    out=Path(out).resolve(strict=True);target=Path(result['path']).resolve(strict=True)
    if target!=out/'ufs-vh1-data-session.qcow2' or result['owner']!='VM3 / UFS VH1' or not result['original_base_read_only']:
        raise ValueError('Only this fresh session-private VM3 leaf is admitted')
    backing=Path(media['overlay']).resolve(strict=True)
    if backing==target or sha(backing)!=media['overlay_sha256']:raise ValueError('Original factory backing differs')
    with (out/'linux-id3-media-nbd.log').open('xb')as log:
        with _export(target,log,readonly=False,disk_size=media['virtual_bytes'])as nbd:
            row=apply_leaf(nbd,media['offset'],media['length'],original,changed)
    if sha(backing)!=media['overlay_sha256']:raise ValueError('Original factory backing changed')
    row.update(path=str(target),backing=str(backing),backing_unchanged=True)
    (out/'linux-id3-media.json').write_text(json.dumps(row,indent=2)+'\n',encoding='utf8')
    return row
