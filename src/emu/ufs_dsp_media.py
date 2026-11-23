
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import original_8704_vm as vm
import ufs_android_media as android
import ufs_virtual_disk as disk
from storage_layout import inventory

PROFILE = 'virtual-8704-archive-lun0-android-dsp-v1'
CORES = {5:('a_audio0fw',0), 6:('b_audio0fw',0), 7:('a_audio1fw',1),
         8:('b_audio1fw',1), 9:('a_audio2fw',2), 10:('b_audio2fw',2)}
ABSENT = {11:'a_audio3fw', 12:'b_audio3fw'}


def source_name(core):
    return f'audio-dsp/evt1/abox_firmware_evt1_core{core}.bin'


def identities():
    wanted = {source_name(core) for core in range(3)}
    rows = {}
    for line in (vm.ROOT/android.CATALOG).read_text(encoding='utf8').splitlines():
        row = json.loads(line)
        name = row['name'].removeprefix('./')
        if name in wanted:
            if name in rows or row['type'] != '0':
                raise ValueError('Ambiguous original DSP archive inventory')
            rows[name] = (row['size'], row['sha256'])
    if rows.keys() != wanted:
        raise ValueError('Original DSP archive inventory incomplete')
    return rows


def expected_plan():
    plan = android.expected_plan()
    template = {r['name']:r for r in inventory()['entries'] if r.get('xml_lun')==0}
    original = identities()
    seen = set()
    for row in plan['partitions']:
        slot = row['slot']
        if slot not in CORES and slot not in ABSENT:
            continue
        label, core = CORES.get(slot, (ABSENT.get(slot),None))
        xml = template[label]['xml']
        if row['label'] != label or 'payload' in row:
            raise ValueError('Original DSP slot binding changed')
        seen.add(slot)
        if core is None:
            if xml['Path'] or xml['Flash']:
                raise ValueError('Unpopulated DSP slot no longer matches OEM metadata')
            continue
        name = source_name(core)
        size, digest = original[name]
        capacity = (row['last']-row['first']+1)*disk.SECTOR
        if xml['Path'] != name or xml['Flash'] != 'yes' or not 0 < size <= capacity:
            raise ValueError('Original DSP member/slot capacity changed')
        row['payload'] = dict(member=name,bytes=size,sha256=digest)
    if seen != CORES.keys() | ABSENT.keys():
        raise ValueError('Original DSP slots incomplete')
    plan['profile'] = PROFILE
    return plan


def zero_extents(image, plan):

    checked = []
    with image.open('rb') as stream:
        for row in plan['partitions']:
            if row['slot'] not in CORES and row['slot'] not in ABSENT:
                continue
            payload = row.get('payload',{}).get('bytes',0)
            stream.seek(row['first']*disk.SECTOR+payload)
            remaining = (row['last']-row['first']+1)*disk.SECTOR-payload
            pad = stream.read(remaining)
            if len(pad) != remaining or pad != bytes(remaining):
                raise ValueError(f'Unexpected bytes in DSP slot padding: {row["label"]}')
            checked.append(dict(slot=row['slot'],zero_bytes=remaining))
    return checked


def transfer_sparse(source, target, length):
    digest = hashlib.sha256()
    zero = bytes(disk.CHUNK)
    while length:
        expected = min(disk.CHUNK,length)
        data = source.read(expected)
        if len(data) != expected:
            raise EOFError('Short parent archive extent')
        digest.update(data)
        if data == zero[:expected]:
            target.seek(expected,os.SEEK_CUR)
        else:
            target.write(data)
        length -= expected
    return digest.hexdigest()


def create(output, parent, extracted):
    output = disk._safe_dir(output)
    source = extracted.resolve(strict=True)
    original = {}
    for name,(size,digest) in identities().items():
        path = (source/name).resolve(strict=True)
        if not path.is_relative_to(source) or path.stat().st_size != size:
            raise ValueError(f'Original DSP source identity changed: {name}')
        data = path.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f'Original DSP source digest changed: {name}')
        original[name] = data
    verified = disk.verify(parent)
    if verified['profile'] != android.PROFILE:
        raise ValueError('Parent must be the verified original Android virtual LUN0')
    plan = expected_plan()
    base = Path(verified['base'])
    output.mkdir(parents=True,exist_ok=False)
    staging = output/(disk.IMAGE+'.new')
    with staging.open('xb') as target,base.open('rb') as old:
        disk._mark_sparse(target)
        for lba, data in disk.gpt(plan).items():
            target.seek(lba*disk.SECTOR)
            target.write(data)
        for row in plan['partitions']:
            if 'payload' not in row:
                continue
            payload = row['payload']
            start = row['first']*disk.SECTOR
            target.seek(start)
            if row['slot'] in CORES:
                target.write(original[payload['member']])
                continue
            old.seek(start)
            if transfer_sparse(old,target,payload['bytes']) != payload['sha256']:
                raise ValueError(f'Parent partition changed during copy: {row["label"]}')
    stat = base.stat()
    if [stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns] != verified['stat']:
        raise ValueError('Parent image changed during copy')
    checked = disk.verify_image(staging,plan)
    checked['dsp_slot_zero_padding'] = zero_extents(staging,plan)
    super_part = next(row for row in plan['partitions'] if row['label']=='super')
    with staging.open('rb') as stream:
        lp = android.super_metadata(stream,super_part['first']*disk.SECTOR,super_part['payload']['bytes'])
    image = output/disk.IMAGE
    os.replace(staging,image)
    checked.update(base=str(image),profile=PROFILE,parent=str(base),android_super_liblp=lp,
                   dsp_executed=False,source_archive=android.SOURCE)
    vm.save(output/'dsp-media-evidence.json',checked)
    vm.save(output/disk.MANIFEST,plan)
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--parent',type=Path,required=True)
    parser.add_argument('--extracted',type=Path,required=True,help='Root containing verified audio-dsp archive members')
    args = parser.parse_args()
    print(json.dumps(create(args.output,args.parent,args.extracted),indent=2))


if __name__ == '__main__':
    main()

