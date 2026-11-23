
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import struct
import tarfile

import ufs_android_sparse
import original_8704_vm as vm
import ufs_virtual_disk as disk
from storage_layout import inventory

PROFILE = 'virtual-8704-archive-lun0-android-v1'
SUPER = 'android/super.img'
                                                                             
                                                                                
SUPER_RAW_SHA256 = 'b36d6ecb91aca84b5335ef2b7c2a37f025fdfbe69a6883a2e11b32c44b6abdba'
SLOTS = {41:('boot_a','android/boot.img'),42:('boot_b','android/boot.img'),
         43:('vendorboot_a','android/vendor_boot.img'),44:('vendorboot_b','android/vendor_boot.img'),
         46:('vbmeta_a','android/vbmeta.img'),47:('vbmeta_b','android/vbmeta.img'),48:('super',SUPER)}
SOURCE = 'var/inputs/packages/ICAS3CN_CPU.CPU_2025121-0317/31/update.tgz'
CATALOG = 'var/inputs/analysis/cpu-extraction.jsonl'


def archive_members():
    names = {member for _,member in SLOTS.values()}
    rows = (vm.ROOT/CATALOG).read_text(encoding='utf8').splitlines()
    result = {}
    for line in rows:
        row = json.loads(line)
        name = row['name'].removeprefix('./')
        if name in names:
            if name in result:
                raise ValueError('Ambiguous original archive inventory')
            result[name] = (row['size'],row['sha256'])
    if result.keys() != names:
        raise ValueError('Missing original Android archive identities')
    return result



def vbmeta_measurement(raw):

    if len(raw) != 65536 or raw[:4] != b'AVB0':
        raise ValueError('Require the original 64 KiB AVB partition image')
    major, minor, auth, aux, alg = struct.unpack_from('>IIQQI',raw,4)
    fields = struct.unpack_from('>10Q',raw,32)
    (hash_off, hash_len, sig_off, sig_len, key_off, key_len,
     key_meta_off, key_meta_len, desc_off, desc_len) = fields
    flags = struct.unpack_from('>I',raw,120)[0]
    total = 256 + auth + aux
    if ((major,minor,alg,flags)!=(1,0,2,0) or total>len(raw) or
        auth%64 or aux%64 or (hash_len,sig_len,key_len)!=(32,512,1032) or
        hash_off+hash_len>auth or sig_off+sig_len>auth or key_off+key_len>aux or
        key_meta_off+key_meta_len>aux or desc_off+desc_len>aux or not desc_len):
        raise ValueError('Unexpected OEM AVB header or disabled verification')
    aux_start = 256+auth
    aux_data = raw[aux_start:total]
    off = desc_off
    end = off+desc_len
    while off < end:
        if end-off<16:
            raise ValueError('Short AVB descriptor header')
        tag, size = struct.unpack_from('>QQ',aux_data,off)
        if tag not in (0,1,2) or size%8 or size>end-off-16:
            raise ValueError('Unsupported/chained or truncated AVB descriptor')
        off += 16+size

                                                                           
                                                                    
    key = aux_data[key_off:key_off+key_len]
    modulus = int.from_bytes(key[8:520],'big')
    signature = int.from_bytes(raw[256+sig_off:256+sig_off+sig_len],'big')
    digest = hashlib.sha256(raw[:256]+aux_data).digest()
    sha256_der_prefix = bytes.fromhex('3031300d060960864801650304020105000420')
    encoded = b'\0\1' + b'\xff'*(sig_len-3-len(sha256_der_prefix)-len(digest)) + b'\0'+sha256_der_prefix+digest
    if (struct.unpack_from('>I',key)[0]!=4096 or modulus.bit_length()!=4096 or
        not 0<signature<modulus or not modulus&1 or
        raw[256+hash_off:256+hash_off+hash_len]!=digest or
        pow(signature,65537,modulus).to_bytes(sig_len,'big')!=encoded):
        raise ValueError('OEM vbmeta authentication hash or RSA signature mismatch')
    return {'size':total,'hash_alg':'sha256','digest':hashlib.sha256(raw[:total]).hexdigest(),
            'public_key_sha256':hashlib.sha256(key).hexdigest(),'rsa_signature_verified':True,
            'hardware_root_of_trust_or_rollback_verified':False}


def avb_handoff(manifest):

    path = manifest.resolve(strict=True)
    if path.name != disk.MANIFEST:
        raise ValueError('Require the original virtual LUN0 manifest')
    image = disk._safe_dir(path.parent)/disk.IMAGE
    if image.resolve(strict=True)!=image:
        raise ValueError('Do not measure a redirected virtual base')
    data = json.loads(path.read_text(encoding='utf-8'))
    source = 'android/vbmeta.img'
    size, digest = archive_members()[source]
    rows = [row for row in data['partitions'] if row['slot']==46 or row['label']=='vbmeta_a']
    if len(rows)!=1 or data['sector_bytes']!=disk.SECTOR:
        raise ValueError('Virtual LUN0 has no unique A-slot vbmeta extent')
    row = rows[0]
    if (row['slot'],row['label'],row['payload'])!=(46,'vbmeta_a',dict(member=source,bytes=size,sha256=digest)):
        raise ValueError('Virtual A-slot vbmeta is not bound to original archive bytes')
    with image.open('rb') as stream:
        stream.seek(row['first']*disk.SECTOR)
        raw = stream.read(size)
    if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('Virtual A-slot vbmeta fails original archive hash')
    result = vbmeta_measurement(raw)
    result.update(partition='vbmeta_a',lba=row['first'],partition_sha256=digest)
    return result



def _checked_digest(data, start):
    field = data[start:start+32]
    return hashlib.sha256(data[:start]+bytes(32)+data[start+32:]).digest() == field


def super_metadata(stream, offset, capacity):

    def read(start, size):
        if start < 0 or size < 0 or start+size > capacity:
            raise ValueError('LP read outside physical partition')
        stream.seek(offset+start)
        data = stream.read(size)
        if len(data) != size:
            raise EOFError('Short LP metadata read')
        return data

    geo = read(4096,52)
    if (geo != read(8192,52) or struct.unpack_from('<2I',geo)!=(0x616c4467,52)
            or not _checked_digest(geo,8)):
        raise ValueError('LP geometry or backup checksum mismatch')
    limit, slots, block = struct.unpack_from('<3I',geo,40)
    if limit < 512 or limit > 1<<20 or limit % 512 or not 1 <= slots <= 4 or block != 4096:
        raise ValueError('LP geometry bounds invalid')

    def header(start):
        magic,major,minor,hs = struct.unpack('<IHHI',read(start,12))
        if (magic,major)!=(0x414c5030,10) or minor>2 or hs != (256 if minor==2 else 128):
            raise ValueError('Unexpected LP metadata header')
        raw = read(start,hs)
        length = struct.unpack_from('<I',raw,44)[0]
        if not _checked_digest(raw,12) or length > limit-hs:
            raise ValueError('LP metadata header hash or size mismatch')
        tables = read(start+hs,length)
        if hashlib.sha256(tables).digest()!=raw[48:80]:
            raise ValueError('LP metadata tables hash mismatch')

        def rows(anchor, size):
            pos,n,stride = struct.unpack_from('<3I',raw,anchor)
            if stride!=size or pos+stride*n > length:
                raise ValueError('LP metadata table bounds mismatch')
            return [tables[pos+i*stride:pos+(i+1)*stride] for i in range(n)]

        blocks = rows(116,64)
        if len(blocks)!=1:
            raise ValueError('Expected one original LP physical block device')
        first,_,_,block_bytes = struct.unpack_from('<QIIQ',blocks[0])
        if block_bytes!=capacity or blocks[0][24:60].split(b'\0')[0]!=b'super':
            raise ValueError('LP device does not bind to virtual super')
        extents = [struct.unpack('<QIQI',x) for x in rows(92,24)]
        groups = rows(104,48)
        result = {}
        for row in rows(80,52):
            name = row[:36].split(b'\0')[0].decode('ascii')
            flags,begin,count,group = struct.unpack_from('<4I',row,36)
            if not name or name in result or begin+count>len(extents) or group>=len(groups):
                raise ValueError('LP partition extents, name or group invalid')
            total = 0
            for sectors,kind,target,src in extents[begin:begin+count]:
                if not sectors or kind not in (0,1) or (kind==0 and
                        (src!=0 or target<first or (target+sectors)*512>capacity)):
                    raise ValueError('LP extent leaves original super')
                total += sectors*512
            result[name] = dict(bytes=total,extents=count,attributes=flags)
        return dict(version=[major,minor],sha256=hashlib.sha256(raw+tables).hexdigest(),partitions=result)

    meta = []
    for i in range(slots):
        a = header(12288+i*limit)
        b = header(12288+(slots+i)*limit)
        if a!=b:
            raise ValueError('LP primary and backup metadata differ')
        meta.append(a)
    return dict(slots=meta,logical_block_bytes=block,metadata_max_size=limit)


def expected_plan():
    plan = disk.factory_plan()
    template = {r['name']:r for r in inventory()['entries'] if r.get('xml_lun')==0}
    catalog = archive_members()
    for row in plan['partitions']:
        if row['slot'] not in SLOTS:
            continue
        label, member = SLOTS[row['slot']]
        if label!=row['label'] or template[label]['xml']['Path']!=member or row.get('payload'):
            raise ValueError('Android virtual partition binding changed')
        stored,digest = catalog[member]
        extent = (row['last']-row['first']+1)*disk.SECTOR
        if member!=SUPER and extent!=stored:
            raise ValueError('Original Android member no longer fits exactly')
        row['payload'] = dict(member=member,bytes=extent,sha256=SUPER_RAW_SHA256 if member==SUPER else digest)
        if member==SUPER:
            row['payload'].update(encoding='android-sparse-v1',encoded_bytes=stored,encoded_sha256=digest)
    plan.update(profile=PROFILE,android_userdata_populated=False)
    return plan


def create(output, parent):
    output = disk._safe_dir(output)
    parent_data = disk.verify(parent)
    base = Path(parent_data['base'])
    old = disk.factory_plan()
    expected = archive_members()
    template = expected_plan()
    partitions = [row for row in template['partitions'] if row['slot'] in SLOTS]
    super_row = next(row for row in partitions if row['slot']==48)
    output.mkdir(parents=True,exist_ok=False)
    staging = output/(disk.IMAGE+'.new')
    decoded = None
    with staging.open('xb+') as target, base.open('rb') as parent_file:
        disk._mark_sparse(target)
        for lba,data in disk.gpt(template).items():
            target.seek(lba*disk.SECTOR)
            target.write(data)
        for row in old['partitions']:
            payload = row.get('payload')
            if payload:
                start = row['first']*disk.SECTOR
                parent_file.seek(start)
                target.seek(start)
                if disk._transfer(parent_file,payload['bytes'],target)!=payload['sha256']:
                    raise ValueError('Previously verified LUN0 changed during copy')
        seen = set()
        with tarfile.open(vm.ROOT/SOURCE,'r|gz') as archive:
            for item in archive:
                member = item.name.removeprefix('./')
                if member not in expected:
                    continue
                size,digest = expected[member]
                if not item.isfile() or item.size!=size or member in seen:
                    raise ValueError('Unexpected original Android tar member')
                rows = [r for r in partitions if r['payload']['member']==member]
                with archive.extractfile(item) as stream:
                    if member==SUPER:
                        decoded = ufs_android_sparse.expand(stream,target,super_row['first']*disk.SECTOR,
                                                        super_row['payload']['bytes'])
                        if (decoded.input_size,decoded.input_sha256)!=(size,digest):
                            raise ValueError('Original Android sparse identity mismatch')
                    else:
                                                                                                
                        data = stream.read(size)
                        if len(data)!=size or hashlib.sha256(data).hexdigest()!=digest or stream.read(1):
                            raise ValueError('Original Android tar member hash mismatch')
                        for row in rows:
                            target.seek(row['first']*disk.SECTOR)
                            target.write(data)
                seen.add(member)
                print(f'Verified and populated {member}',flush=True)
        if seen!=expected.keys() or decoded is None:
            raise ValueError('Original Android media incomplete')
        target.flush()
        lp = super_metadata(target,super_row['first']*disk.SECTOR,super_row['payload']['bytes'])
    if decoded.output_sha256!=SUPER_RAW_SHA256:
        raise ValueError('Expanded original super differs from pinned readback')
    plan = template
    checked = disk.verify_image(staging,plan)
    image = output/disk.IMAGE
    staging.replace(image)
    checked.update(base=str(image),android_sparse=asdict(decoded),liblp=lp,source_archive=SOURCE)
    vm.save(output/'android-media-evidence.json',checked)
    manifest = output/(disk.MANIFEST+'.new')
    vm.save(manifest,plan)
    os.replace(manifest,output/disk.MANIFEST)
    return checked


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--parent',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(create(args.output,args.parent),indent=2))

