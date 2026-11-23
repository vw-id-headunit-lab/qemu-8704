




from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import uuid
import zlib

import original_8704_vm as vm

SECTOR = 4096                                                   
PIT_SECTOR = 512                                                
ALIGN = (1 << 20) // SECTOR
ENTRIES, ENTRY_BYTES = 128, 128
TABLE_BLOCKS = ENTRIES * ENTRY_BYTES // SECTOR
CHUNK = 8 << 20
TYPE = uuid.UUID('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7')
NS = uuid.UUID('bea2bc31-49f0-4ec4-aa2a-be8797e3b87a')
MANIFEST = 'ufs-lun0.json'
IMAGE = 'ufs-lun0.raw'
PAYLOAD = {
    14: 'ivi/icas3cn-gp-image-euto-v9.ext4',
    27: 'ivi/ivi_hash.img',
    31: 'ivi/icas3cn-gp-image-euto-v9.ext4',
    32: 'qnx/qtd/system-qla.part_qtd.qnx6.vw_cn',
    33: 'qnx/qtd/system-qla.part_qtd.qnx6.vw_cn',
    55: 'ivi/ivi_hash.img',
}
EXPECTED = {
    14: 'a_agl_system_ivi_rbhv-qia', 27: 'a_hash_ivi_rbhv-qia',
    31: 'b_agl_system_ivi_rbhv-qia', 32: 'system_qnx_rbhv-qia',
    33: 'system_qnx_bak_rbhv-qia', 34: 'reserved1',
    36: 'resource_qnx_rbhv-qia', 55: 'b_hash_ivi_rbhv-qia',
    56: 'ramdump',
}


def make_plan(inv, archive):
    if inv['xml_matched_entries'] != 107 or len(inv['entries']) != 107:
        raise ValueError('Require original 107 PIT/XML partition records')
    lun = [r for r in inv['entries'] if r.get('xml_lun') == 0]
    rows = [r for r in lun if r['filesystem'] in (1, 5)]
    if len(lun) != 89 or len(rows) != 56 or any(r['block_offset'] for r in lun):
        raise ValueError('Unexpected LUN0 factory template')
    disk_id = uuid.uuid5(NS, inv['pit_sha256'] + inv['xml_sha256'])
    total, left, parts, omitted = sum(r['block_count'] for r in lun), ALIGN, [], []
    if total * PIT_SECTOR % SECTOR:
        raise ValueError('PIT LUN0 size is not QEMU-sector aligned')
    nblocks = total * PIT_SECTOR // SECTOR
    for number, row in enumerate(rows, 1):
        name, size = row['name'], row['block_count'] * PIT_SECTOR
        if number in EXPECTED and name != EXPECTED[number]:
            raise ValueError(f'Factory slot {number} no longer matches')
        if number == 56 and size == 0 and row['option'] == 'remained':
            omitted.append({'slot': number, 'label': name, 'reason': 'no extent in factory template'})
            continue
        if not size or size % SECTOR or len(name.encode('utf-16-le')) > 72 or not name or '\0' in name:
            raise ValueError(f'Invalid partition extent or label {number}')
        first = (left + ALIGN - 1) // ALIGN * ALIGN
        left = first + size // SECTOR
        part = dict(slot=number, label=name, first=first, last=left-1,
                    type=str(TYPE), guid=str(uuid.uuid5(disk_id, f'{number}:{name}')))
        if number in PAYLOAD:
            member = PAYLOAD[number]
            item = archive[member]
            path = row['xml']['Path']
            if (member not in (path, path+'.vw_cn') or not 0 < item['size'] <= size
                    or (number not in (27, 55) and item['size'] != size)):
                raise ValueError(f'Factory member does not fit or bind slot {number}')
            part['payload'] = dict(member=member, bytes=item['size'], sha256=item['sha256'])
        parts.append(part)
    if left > nblocks - TABLE_BLOCKS - 1 or len(parts) != 55 or len(omitted) != 1:
        raise ValueError('LUN0 GPT would overlap backup or lose factory ordinals')
    return dict(profile='virtual-8704-archive-lun0-v1', sector_bytes=SECTOR,
                pit_sector_bytes=PIT_SECTOR, alignment_sectors=ALIGN, blocks=nblocks,
                disk_guid=str(disk_id), pit_sha256=inv['pit_sha256'], xml_sha256=inv['xml_sha256'],
                partitions=parts, omitted=omitted, other_luns_present=False,
                factory_runtime_gpt=False, partition_type_policy='Microsoft Basic Data for original QNX ms names',
                missing_payload_policy='no filesystem populated; unwritten sparse bytes read as zero')


def factory_plan():
    from storage_layout import inventory
    src = vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl'
    archive = {row['name'].removeprefix('./'): row for row in map(json.loads, src.read_text(encoding='utf-8').splitlines())}
    return make_plan(inventory(), archive)


def gpt(plan):
    last = plan['blocks'] - 1
    table = bytearray(ENTRIES * ENTRY_BYTES)
    for row in plan['partitions']:
        pos = (row['slot'] - 1) * ENTRY_BYTES
        struct.pack_into('<16s16sQQQ72s', table, pos, uuid.UUID(row['type']).bytes_le,
                         uuid.UUID(row['guid']).bytes_le, row['first'], row['last'], 0,
                         row['label'].encode('utf-16-le'))

    def header(current, peer, entry_lba):
        raw = bytearray(SECTOR)
        struct.pack_into('<8sIIIIQQQQ16sQIII', raw, 0, b'EFI PART', 0x10000, 92, 0, 0,
                         current, peer, 2 + TABLE_BLOCKS, last - TABLE_BLOCKS - 1,
                         uuid.UUID(plan['disk_guid']).bytes_le, entry_lba, ENTRIES, ENTRY_BYTES,
                         zlib.crc32(table))
        struct.pack_into('<I', raw, 16, zlib.crc32(raw[:92]))
        return bytes(raw)

    mbr = bytearray(SECTOR)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, bytes.fromhex('000200'), 0xee, b'\xff'*3,
                     1, min(last, 0xffffffff))
    mbr[510:512] = b'\x55\xaa'
    return {0: bytes(mbr), 1: header(1, last, 2), 2: bytes(table),
            last-TABLE_BLOCKS: bytes(table), last: header(last, 1, last-TABLE_BLOCKS)}


def _safe_dir(path):
    path = path.resolve()
    if path == vm.ROOT/'var' or not path.is_relative_to(vm.ROOT/'var'):
        raise ValueError('Require output under a workspace scratch subdirectory')
    return path


def _mark_sparse(stream):
    if os.name != 'nt':
        return                                                                       
    import msvcrt
    from ctypes import wintypes
    ctl = ctypes.WinDLL('kernel32', use_last_error=True).DeviceIoControl
    ctl.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    ctl.restype = wintypes.BOOL
    count = wintypes.DWORD()
    if not ctl(msvcrt.get_osfhandle(stream.fileno()), 0x900c4, None, 0, None, 0, ctypes.byref(count), None):
        raise ctypes.WinError(ctypes.get_last_error())


def _transfer(stream, length, dest=None):
    sha = hashlib.sha256()
    while length:
        block = stream.read(min(CHUNK, length))
        if not block:
            raise EOFError('Short archive member or virtual image')
        sha.update(block)
        if dest is not None:
            dest.write(block)
        length -= len(block)
    return sha.hexdigest()


def create(output):
    from vlm_platform import factory_file
    output = _safe_dir(output)
    plan = factory_plan()
    output.mkdir(parents=True, exist_ok=False)
    image = output/IMAGE
    members = {row['payload']['member'] for row in plan['partitions'] if 'payload' in row}
    sources = {member: factory_file(member) for member in sorted(members)}
    with image.open('xb') as target:
        _mark_sparse(target)
                                                                      
                                                                            
        for lba, data in gpt(plan).items():
            target.seek(lba * SECTOR)
            target.write(data)
        for row in plan['partitions']:
            payload = row.get('payload')
            if payload:
                target.seek(row['first'] * SECTOR)
                with sources[payload['member']].open('rb') as original:
                    if _transfer(original, payload['bytes'], target) != payload['sha256'] or original.read(1):
                        raise ValueError(f'Archive member changed: {payload["member"]}')
    result = verify_image(image, plan)
    temporary = output/(MANIFEST+'.new')
    vm.save(temporary, plan)
    os.replace(temporary, output/MANIFEST)
    return result


def verify_image(image, plan):
    before = image.stat()
    if before.st_size != plan['blocks'] * SECTOR:
        raise ValueError('Incorrect virtual capacity')
    writes = []
    with image.open('rb') as stream:
        for lba, data in gpt(plan).items():
            stream.seek(lba * SECTOR)
            if stream.read(len(data)) != data:
                raise ValueError(f'GPT byte mismatch at LBA {lba}')
        for row in plan['partitions']:
            payload = row.get('payload')
            if payload:
                stream.seek(row['first'] * SECTOR)
                digest = _transfer(stream, payload['bytes'])
                if digest != payload['sha256']:
                    raise ValueError(f'Archive payload hash mismatch at slot {row["slot"]}')
                writes.append(dict(slot=row['slot'], bytes=payload['bytes'], sha256=digest))
    after = image.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after):
        raise ValueError('Virtual image changed during verification')
    return dict(base=str(image), stat=list(identity(after)), gpt_regions_verified=5,
                archive_payloads_verified=writes, physical_gpt_verified=False)


def verify(manifest):
    manifest = manifest.resolve(strict=True)
    if manifest.name != MANIFEST:
        raise ValueError('Require exact virtual LUN0 manifest name')
    directory = _safe_dir(manifest.parent)
    image = directory/IMAGE
    if image.resolve(strict=True) != image:
        raise ValueError('Virtual image cannot redirect through a symlink')
    actual = json.loads(manifest.read_text(encoding='utf-8'))
    profile = actual.get('profile')
    if profile == 'virtual-8704-archive-lun0-android-dsp-v1':
        from ufs_dsp_media import expected_plan, zero_extents
        plan = expected_plan()
    elif profile == 'virtual-8704-archive-lun0-android-v1':
        from ufs_android_media import expected_plan
        plan = expected_plan()
    else:
        plan = factory_plan()
    if actual != plan:
        raise ValueError('Manifest no longer matches exact archive layout/inputs')
    result = verify_image(image, plan)
    if profile == 'virtual-8704-archive-lun0-android-dsp-v1':
        result['dsp_slot_zero_padding'] = zero_extents(image,plan)
    if plan.get('android_userdata_populated') is False:
        from ufs_android_media import super_metadata
        part = next(row for row in plan['partitions'] if row['label']=='super')
        with image.open('rb') as f:
            result['android_super_liblp'] = super_metadata(f,part['first']*SECTOR,part['payload']['bytes'])
    result['profile'] = plan['profile']
    return result


def create_overlay(verified, output, name, *, android_parent=None):
    base = Path(verified['base'])
    if name not in ('ufs-lun0-overlay.qcow2','ufs-vh2-lun0-overlay.qcow2'):
        raise ValueError('Require a declared local UFS overlay filename')
    overlay = _safe_dir(output)/name
    if overlay.exists():
        raise ValueError('Require a new private overlay')
    qemu_img = vm.ROOT/'.local/deps/bin/qemu-img.exe'
    def invoke(*args):
        return subprocess.run([str(qemu_img), *args], check=True, capture_output=True,
                              text=True, encoding='utf-8')
    def check(path):
        info=json.loads(invoke('info','--output=json',str(path)).stdout)
        data=info.get('format-specific',{}).get('data',{})
        if (info.get('backing-filename-format')!='raw' or info.get('format')!='qcow2'
                or info['virtual-size']!=verified['stat'][2]
                or Path(info['full-backing-filename']).resolve()!=base
                or data.get('data-file') or data.get('corrupt') or info.get('encrypted')):
            raise ValueError('Unexpected qcow2 backing chain')
    if android_parent is None:
        invoke('create','-f','qcow2','-F','raw','-b',str(base),str(overlay))
        check(overlay)
    else:
        if name!='ufs-vh2-lun0-overlay.qcow2':
            raise ValueError('Only Android VM4 can resume a previous private run')
        source=_safe_dir(android_parent)
        parent=source/name
        if parent.resolve(strict=True)!=parent:
            raise ValueError('Prior VM4 image cannot redirect through a link')
        def load(file):return json.loads((source/file).read_text(encoding='utf-8'))
        origin=load('android-ufs-overlay.json')
        integrity=load('ufs-lun0-base-integrity.json')
        if (Path(origin['path']).resolve()!=parent or Path(origin['base']).resolve()!=base or
                origin.get('owner')!='VM4 / UFS VH2' or not integrity.get('unchanged_stat')
                or not load('integrity.json').get('original_unchanged')
                or load('integrity.json').get('exit_code')!=0):
            raise ValueError('Prior VM4 image is not a completed run over the same immutable base')
        check(parent)
        digest=vm.sha(parent)
        temp=overlay.with_suffix('.qcow2.tmp')
        if temp.exists():raise ValueError('Private VM4 copy temporary file already exists')
        created=False
        try:
            with parent.open('rb') as stream, temp.open('xb') as copy:
                created=True
                shutil.copyfileobj(stream,copy,length=CHUNK)
            if vm.sha(temp)!=digest or vm.sha(parent)!=digest:
                raise ValueError('Prior VM4 image changed during private copy')
            check(temp)
            os.replace(temp,overlay)
        finally:
            if created:temp.unlink(missing_ok=True)
    return overlay


QNX_FIRSTBOOT_SLOTS = {2: 'persist_qnx_rbhv-qia', 4: 'ucm',
                       36: 'resource_qnx_rbhv-qia', 54: 'persist2_qnx_rbhv-qia'}
QNX_COMPLETE_VAR_SLOTS = {35: 'userdata_qnx_rbhv-qia', 37: 'coredump_qnx_rbhv-qia'}
QNX_COMPLETE_VAR_SHA256 = '3d51fa91ede9b9720bc866eda6484253b2e136edcb85413a09a5413928f045db'
QNX_COMPLETE_VAR_MARKERS = '95a75ab51d43f8fbd70c77827db9d0c5ac3d73386ca7111a527ed3d4e4ae634a'
QNX_RETAINED_VAR_MARKERS = {
    'b2619e66eaee102b4d4045288b6636651fede085e1806b58211dcdd920436e62':
        'b2dc8eae4d4b929272bcf21963513eb219deabb4c03e6662c16f16c42cd577ac',
}


def qnx_initial_allocations(plan, mapping, *, completed_var=False):

    allowed = QNX_FIRSTBOOT_SLOTS | (QNX_COMPLETE_VAR_SLOTS if completed_var else {})
    slots = {p['slot']: p for p in plan['partitions'] if p['slot'] in allowed}
    if (plan['sector_bytes'] != SECTOR or set(slots) != set(allowed)
            or any(p['label'] != allowed[n] or p.get('payload') for n, p in slots.items())):
        raise ValueError('QNX ordinary first-boot slot layout differs')
    counts = {n: 0 for n in slots}
    end = 0
    for r in mapping:
        start, length, depth = r['start'], r['length'], r['depth']
        if (type(start) is not int or type(length) is not int or type(depth) is not int
                or start != end or length <= 0 or depth not in (0, 1) or type(r['present']) is not bool):
            raise ValueError('Invalid map for the exact two-layer QNX disk')
        end = start + length
        if depth == 0 and r['present']:
            matched = [n for n, p in slots.items() if p['first'] * SECTOR <= start
                       and end <= (p['last'] + 1) * SECTOR]
            if len(matched) != 1:
                count = 'six' if completed_var else 'four'
                raise ValueError(f'QNX private extent outside the {count} ordinary first-boot slots')
            counts[matched[0]] += length
    if end != plan['blocks'] * SECTOR or not all(counts.values()):
        raise ValueError('QNX source lacks a complete map or a required first-boot extent')
    return {str(n): {'label': allowed[n], 'bytes': counts[n]} for n in sorted(slots)}


def qnx_completed_var_source(source, digest):

    expected = (QNX_COMPLETE_VAR_MARKERS if digest == QNX_COMPLETE_VAR_SHA256
                else QNX_RETAINED_VAR_MARKERS.get(digest))
    if expected is None:
        return False
    path = source/'qnx-native-runtime/qnx-bootmarkers.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Original completed QNX /var bootmarkers differ')
    observed = json.loads(raw)
    required = ('qtd_root_wait_returned', 'qnx6_startup_userdata_end',
                'qnx6_startup_coredump_begin', 'qnx6_startup_coredump_end')
    rows = [row for row in observed['records'] if row['marker'] in required]
    if (observed.get('status') != 'observed_original_marker_records'
            or tuple(row['marker'] for row in rows) != required
            or any(a['sequence'] >= b['sequence'] for a, b in zip(rows, rows[1:]))):
        raise ValueError('Original completed QNX /var milestones are not ordered')
    return True


def clone_qnx_firstboot(manifest, verified, output, qnx_parent):

    source = _safe_dir(qnx_parent)
    name = 'ufs-lun0-overlay.qcow2'
    prior, new = source/name, _safe_dir(output)/name
    if (prior.resolve(strict=True)!=prior or prior==new or new.exists()
            or not (source/'result.json').is_file()):
        raise ValueError('Require a completed source and a fresh QNX overlay')
    def load(filename):
        return json.loads((source/filename).read_bytes())
    base = Path(verified['base'])
    origin, original, disk = (load(x) for x in ('ufs-lun0-input.json', 'integrity.json',
                                                'ufs-lun0-base-integrity.json'))
    if (original.get('original_unchanged') is not True or original.get('exit_code') != 0
            or disk.get('unchanged_stat') is not True or Path(disk['base']).resolve()!=base
            or origin.get('host') != 0 or origin.get('lun') != 0 or origin['stat']!=verified['stat']
            or origin.get('read_only_backing') is not True or origin.get('guest_writes_to_overlay_only') is not True
            or Path(origin['base']).resolve()!=base or Path(origin['overlay']).resolve()!=prior
            or Path(origin['manifest']).resolve()!=manifest.resolve()):
        raise ValueError('Prior QNX LUN0 is not a stopped original run over this immutable base')
    exe = vm.ROOT/'.local/deps/bin/qemu-img.exe'
    def image(*args):
        return subprocess.run([str(exe), *args, str(prior)], check=True, capture_output=True,
                              text=True, encoding='utf-8').stdout
    digest=vm.sha(prior)
    completed_var=qnx_completed_var_source(source,digest)
    info=json.loads(image('info','--output=json'))
    fmt=info.get('format-specific',{}).get('data',{})
    if (info.get('format')!='qcow2' or info.get('backing-filename-format')!='raw'
            or info.get('virtual-size')!=verified['stat'][2]
            or Path(info['full-backing-filename']).resolve()!=base or not Path(info['backing-filename']).is_absolute()
            or info.get('dirty-flag') is not False or info.get('snapshots') or info.get('encrypted')
            or fmt.get('data-file') or fmt.get('corrupt')):
        raise ValueError('QNX requires a clean qcow2 container with a single absolute raw backing file')
                                                                            
    allocations=qnx_initial_allocations(json.loads(manifest.read_bytes()),json.loads(image('map','--output=json')),
                                       completed_var=completed_var)
    image('check','-q')
    temp=new.with_suffix('.qcow2.tmp')
    created=False
    try:
        with prior.open('rb') as src, temp.open('xb') as dest:
            created=True
            shutil.copyfileobj(src,dest,CHUNK)
        if vm.sha(temp)!=digest or vm.sha(prior)!=digest:
            raise ValueError('QNX seed changed while checking or copying it')
        os.replace(temp,new)
    finally:
        if created: temp.unlink(missing_ok=True)
    return new, dict(prior_session=str(source), prior_overlay_sha256=digest,
                     private_allocations_by_slot=allocations, copied_closed_firstboot_overlay=True,
                     verified_original_completed_var_profile=completed_var,
                     clean_boot_state_claimed=False, rpmb_copied=False)


def launch_options(manifest, output, *, qnx_parent=None):
    verified = verify(manifest)
    base = Path(verified['base'])
    seed = None
    if qnx_parent is None:
        overlay = create_overlay(verified,output,'ufs-lun0-overlay.qcow2')
    else:
        overlay, seed = clone_qnx_firstboot(manifest,verified,output,qnx_parent)
                                                                             
    file_node = dict(driver='file', filename=str(base), **{'node-name':'ufs-base-file','read-only':True})
    base_node = dict(driver='raw', file='ufs-base-file', **{'node-name':'ufs-base','read-only':True})
    overlay_file = dict(driver='file', filename=str(overlay), **{'node-name':'ufs-overlay-file'})
    overlay_node = dict(driver='qcow2', file='ufs-overlay-file', backing='ufs-base', **{'node-name':'ufs-oem-lun0'})
    command = []
    for node in (file_node, base_node, overlay_file, overlay_node):
        command.extend(('-blockdev', json.dumps(node, separators=(',', ':'))))
    command += ['-device', 'ufs-lu,drive=ufs-oem-lun0,bus=auto9-ufs0-luns,lun=0']
    record = dict(verified, manifest=str(manifest.resolve()), overlay=str(overlay), read_only_backing=True,
                  host=0, lun=0, guest_writes_to_overlay_only=True)
    if seed:
        record['qnx_firstboot_seed']=seed
    return command, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmd = parser.add_subparsers(dest='operation', required=True)
    cmd.add_parser('create').add_argument('--output', type=Path, required=True)
    cmd.add_parser('verify').add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    result = create(args.output) if args.operation == 'create' else verify(args.manifest)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

