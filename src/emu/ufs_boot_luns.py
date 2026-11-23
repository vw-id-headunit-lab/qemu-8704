




import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile

import original_8704_vm as vm
from storage_layout import inventory
from ufs_virtual_disk import _safe_dir

PROFILE='virtual-8704-archive-boot-luns-v1'
MANIFEST='boot-luns.json'
PIT_SECTOR=512
SECTOR=4096
LABELS=('fwbl1','epbl','bl2','dramtrain','ect_test','bootloader','el3_mon','tzsw','ldfw')
COUNTS=(24,152,776,64,160,4096,512,2048,16384)

def archive_index():
    f=vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl'
    return {x['name'].removeprefix('./'):x for x in map(json.loads,f.read_text(encoding='utf-8').splitlines())}

def make_plan(inv,index):
    if len(inv['entries'])!=107 or inv['xml_matched_entries']!=107:
        raise ValueError('Original PIT/XML no longer matches')
    luns=[]
    for lun,prefix in ((1,'a'),(2,'b')):
        src=[e for e in inv['entries'] if e['xml_lun']==lun]
        if len(src)!=len(LABELS):
            raise ValueError('Original boot LUN extent count changed')
        offset=0
        rows=[]
        for e,label,count in zip(src,LABELS,COUNTS):
            if (e['name']!=f'{prefix}_{label}' or e['filesystem']!=0 or
                    e['block_offset']!=0 or e['block_count']!=count):
                raise ValueError('Original boot extent identity changed')
            path=e['xml']['Path']
            extent=count*PIT_SECTOR
            row=dict(label=e['name'],offset=offset,extent=extent)
            if path:
                item=index[path]
                if label=='ect_test' or not 0<item['size']<=extent:
                    raise ValueError('Unexpected boot payload extent')
                row['payload']=dict(member=path,bytes=item['size'],sha256=item['sha256'])
            elif label!='ect_test':
                raise ValueError('Missing original boot payload')
            rows.append(row)
            offset+=extent
        if offset%SECTOR:
            raise ValueError('Virtual boot LUN geometry not aligned')
        luns.append(dict(lun=lun,image=f'boot-lun{lun}.raw',bytes=offset,extents=rows))
    return dict(profile=PROFILE,pit_sha256=inv['pit_sha256'],xml_sha256=inv['xml_sha256'],
                sector_bytes=SECTOR,pit_sector_bytes=PIT_SECTOR,luns=luns,
                layout='contiguous virtual PIT order, not measured physical offsets',
                read_only=True,rpmb=False,boot_wlun=False)

def plan():
    return make_plan(inventory(),archive_index())

def _payloads(doc):
    from vlm_platform import factory_file
    wanted={x['payload']['member']:x['payload'] for l in doc['luns'] for x in l['extents'] if 'payload' in x}
    out={}
    for member in wanted:
        try:
            out[member]=factory_file(member).read_bytes()
        except FileNotFoundError:
            pass
    missing=set(wanted)-set(out)
    if missing:
        archives=[p for p in (vm.ROOT/'var/inputs/packages').rglob('update.tgz') if p.stat().st_size>1024*1024]
        if len(archives)!=1:
            raise ValueError('Require one original CPU update archive')
        with tarfile.open(archives[0],'r|gz',bufsize=1024*1024) as tf:
            for entry in tf:
                member=entry.name.removeprefix('./')
                if member not in missing:
                    continue
                if not entry.isfile() or entry.size!=wanted[member]['bytes']:
                    raise ValueError('Unexpected original tar member type or size')
                with tf.extractfile(entry) as f:
                    out[member]=f.read()
                missing.remove(member)
                if not missing:
                    break
    if missing:
        raise ValueError('Missing original boot payloads: '+', '.join(sorted(missing)))
    for k,data in out.items():
        if len(data)!=wanted[k]['bytes'] or hashlib.sha256(data).hexdigest()!=wanted[k]['sha256']:
            raise ValueError('Original boot payload changed: '+k)
    return out

def _verify(directory,doc):
    result={}
    for lun in doc['luns']:
        p=directory/lun['image']
        if p.resolve().parent!=directory or p.stat().st_size!=lun['bytes']:
            raise ValueError('Boot LUN path or geometry changed')
        with p.open('rb') as f:
            for row in lun['extents']:
                if f.tell()!=row['offset']:
                    raise ValueError('Non-contiguous virtual boot layout')
                raw=f.read(row['extent'])
                item=row.get('payload')
                n=item['bytes'] if item else 0
                if (len(raw)!=row['extent'] or any(raw[n:]) or
                    (item and hashlib.sha256(raw[:n]).hexdigest()!=item['sha256'])):
                    raise ValueError('Boot LUN content mismatch: '+row['label'])
        result[str(lun['lun'])]=dict(image=str(p),sha256=vm.sha(p),bytes=lun['bytes'])
    return result

def create(output):
    output=_safe_dir(output)
    doc=plan()
    output.mkdir(parents=True,exist_ok=False)
    data=_payloads(doc)
    for lun in doc['luns']:
        tmp=output/(lun['image']+'.new')
        with tmp.open('xb') as f:
            for row in lun['extents']:
                item=row.get('payload')
                raw=data[item['member']] if item else b''
                f.write(raw)
                f.write(bytes(row['extent']-len(raw)))
        os.replace(tmp,output/lun['image'])
    result=_verify(output,doc)
    tmp=output/(MANIFEST+'.new')
    vm.save(tmp,doc)
    os.replace(tmp,output/MANIFEST)
    return result

def verify(manifest):
    manifest=Path(manifest).resolve()
    parent=_safe_dir(manifest.parent)
    doc=plan()
    if manifest.name!=MANIFEST or json.loads(manifest.read_text(encoding='utf-8'))!=doc:
        raise ValueError('Original boot LUN manifest mismatch')
    return _verify(parent,doc)

def launch_options(manifest):
    spec=verify(manifest)
    args=[]
    for lun,row in spec.items():
        name=f'ufs-oem-boot-lun{lun}'
        file=dict(driver='file',filename=row['image'],**{'node-name':name+'-file','read-only':True})
        raw=dict(driver='raw',file=name+'-file',**{'node-name':name,'read-only':True})
        for node in (file,raw):
            args.extend(('-blockdev',json.dumps(node,separators=(',',':'))))
        args.extend(('-device',f'ufs-lu,drive={name},bus=auto9-ufs0-luns,lun={lun}'))
    return args,spec

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    subs=ap.add_subparsers(dest='op',required=True)
    subs.add_parser('create').add_argument('--output',type=Path,required=True)
    subs.add_parser('verify').add_argument('--manifest',type=Path,required=True)
    a=ap.parse_args()
    print(json.dumps(create(a.output) if a.op=='create' else verify(a.manifest),indent=2))

if __name__=='__main__':
    main()

