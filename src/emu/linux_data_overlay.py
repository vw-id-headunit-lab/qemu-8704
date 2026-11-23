




import hashlib
import json
from pathlib import Path
import subprocess

from linux_hmi_overlay import NBD, QEMU, ROOT, UNIT, connect, private_ranges, require_no_slot23_shadow, seed_backing
import original_8704_vm as vm
from ufs_virtual_disk import _mark_sparse


PROFILE = 'virtual-empty-ivi-data-ext4-v1'
SLOT = 15
MKFS = ROOT/'.local/deps/bin/mke2fs.exe'
IVI_WRITABLE = {3:'swup', 15:'userdata_ivi_rbhv-qia', 19:'bt_ivi_rbhv-qia',
                20:'navi_ivi_rbhv-qia', 21:'radio_ivi_rbhv-qia',
                22:'speech_ivi_rbhv-qia', 26:'nav_rw_data_ivi_rbhv-qia', 29:'agl_log'}
FACTORY_MEDIA = {18:'navi_core', 23:'vw_hmi_ivi_rbhv-qia', 30:'lg_custom_ivi_rbhv-qia'}


def geometry(base_manifest):
    layout = json.loads(Path(base_manifest).read_bytes())
    rows = [p for p in layout['partitions'] if p['slot'] == SLOT]
    if (layout['sector_bytes'] != 4096 or len(rows) != 1 or
            rows[0]['label'] != 'userdata_ivi_rbhv-qia' or rows[0].get('payload')):
        raise ValueError('Expected the unpopulated factory IVI /data slot')
    offset = rows[0]['first']*4096
    length = (rows[0]['last']+1-rows[0]['first'])*4096
    if length != 1 << 30:
        raise ValueError('IVI /data capacity differs from the verified OEM PIT')
    return offset, length


def private_allocations(path, offset, length):
    extents = json.loads(subprocess.check_output(
        [str(QEMU/'qemu-img.exe'), 'map', '--output=json', str(path)], timeout=45))
    rows = [r for r in extents if r['depth'] == 0 and r.get('present', r['data'])]
    return dict(allocated_bytes=sum(r['length'] for r in rows), extents=rows,
                only_data_slot=bool(rows) and all(
                    offset <= r['start'] < r['start']+r['length'] <= offset+length for r in rows))


def classify_allocations(rows, writable, factory, virtual_bytes, readonly_verified=frozenset()):

    slots, factory_cow = {}, {}
    previous_end = 0
    for row in sorted(rows, key=lambda r:r['start']):
        if row['depth'] or not row.get('present',row['data']):
            continue
        start, length = row['start'], row['length']
        end = start + length
        if start % 4096 or length % 4096 or not previous_end <= start < end <= virtual_bytes:
            raise ValueError('Prior VM3 allocation has invalid sector boundaries')
        previous_end = end
        if (start,length) in readonly_verified:
            continue
        matched = [slot for slot,(lo,hi) in writable.items() if lo<=start<end<=hi]
        if len(matched)==1:
            slot=matched[0]
            slots[slot]=slots.get(slot,0)+length
            continue
        shadow = [slot for slot,(lo,hi) in factory.items() if lo<=start<end<=hi]
        if len(shadow)!=1:
            raise ValueError('Prior VM3 allocation reaches an immutable or unknown slot')
        slot=shadow[0]
        factory_cow[slot]=factory_cow.get(slot,0)+length
    return slots, factory_cow


def reuse(base, media, out, previous, *, weston_plan=None):

    previous = Path(previous).resolve(strict=True)
    if not previous.is_relative_to(ROOT/'var') or not (previous/'result.json').is_file():
        raise ValueError('Require an exited native VM3 session in project scratch')
    source = previous/'ufs-vh1-data-session.qcow2'
    prior = json.loads((previous/'linux-ivi-data.json').read_bytes())
    backing = Path(media['overlay']).resolve(strict=True)
    prior_backing = seed_backing(media,prior)
    migrated = prior_backing!=backing
    if migrated:
        terminal=json.loads((previous/'integrity.json').read_bytes())
        if terminal['original_unchanged'] is not True or terminal['exit_code']!=0:
            raise ValueError('VW backing transition requires a cleanly closed original seed')
    if (source.resolve(strict=True) != source or Path(prior['path']).resolve() != source
            or prior.get('profile') != PROFILE or prior.get('owner') != 'VM3 / UFS VH1'
            or vm.sha(prior_backing)!=prior['parent_sha256'] or vm.sha(backing)!=media['overlay_sha256']):
        raise ValueError('Prior VM3 provenance or immutable backing differs')

    def inspect(command):
        return json.loads(subprocess.check_output(
            [str(QEMU/'qemu-img.exe'),command,'--output=json',str(source)],timeout=45))

                                                                    
    info = inspect('info')
    if (info['format']!='qcow2' or info['virtual-size']!=media['virtual_bytes']
            or info.get('backing-filename-format')!='qcow2'
            or info.get('dirty-flag') or info.get('format-specific',{}).get('data',{}).get('corrupt')
            or Path(info['full-backing-filename']).resolve()!=prior_backing):
        raise ValueError('Prior VM3 image geometry or qcow2 backing differs')
    layout = json.loads(Path(base).with_suffix('.json').read_bytes())
    if layout['sector_bytes']!=4096:
        raise ValueError('Unexpected IVI sector size')
    ranges = {p['slot']:(p['first']*4096,(p['last']+1)*4096) for p in layout['partitions']
              if IVI_WRITABLE.get(p['slot'])==p['label'] and not p.get('payload')}
    if set(ranges)!=set(IVI_WRITABLE):
        raise ValueError('Original IVI writable partition geometry changed')
                                                                                
                                                                              
    factory_ranges = {}
    items=([media] if 'slot' in media else [])+(media.get('additional_partitions') or [])
    for item in items:
        slot=item['slot']
        if slot not in FACTORY_MEDIA:
            raise ValueError('Unrecognized VM3 factory media slot')
        matching=[p for p in layout['partitions'] if p['slot']==slot and
                  p['label']==FACTORY_MEDIA[slot] and not p.get('payload')]
        if len(matching)!=1 or slot in factory_ranges:
            raise ValueError('Unexpected original VM3 media slot geometry')
        row=matching[0]
        start,end=row['first']*4096,(row['last']+1)*4096
        if not item.get('complete_readback') or (item.get('offset'),item.get('length'))!=(start,end-start):
            raise ValueError('Cannot reuse an unverified factory-media backing extent')
        factory_ranges[slot]=(start,end)
    allocation_rows=inspect('map')
    if migrated:
        require_no_slot23_shadow(allocation_rows,media)
        from dissect.extfs import ExtFS
        from linux_hmi_userdata import _export, _Slot, require_no_stale_vw_verity_marker
        first,length=geometry(Path(base).with_suffix('.json'))
        with (out/'linux-vw-verity-marker-nbd.log').open('xb') as log:
            with _export(source,log,readonly=True,disk_size=media['virtual_bytes']) as nbd:
                require_no_stale_vw_verity_marker(ExtFS(_Slot(nbd,first,length)))
    readonly_verified=frozenset()
    if prior.get('early_weston_verity') is not None:
        if (weston_plan is None or weston_plan.base!=Path(base).resolve() or
                weston_plan.virtual_bytes!=media['virtual_bytes']):
            raise ValueError('Prior VM3 state requires its exact Weston boot policy')
        from linux_weston_platform import verify_inherited
        readonly_verified=verify_inherited(weston_plan,prior,info,source,out,allocation_rows)
    slots,factory_cow=classify_allocations(allocation_rows,ranges,factory_ranges,
                                          media['virtual_bytes'],readonly_verified)
    if SLOT not in slots:
        raise ValueError('Prior VM3 image lacks its /data allocation')
    target = out/'ufs-vh1-data-session.qcow2'
    temporary = target.with_suffix('.qcow2.copying')
    if target.exists() or temporary.exists():
        raise ValueError('Require a new IVI writable target')
    owns_temporary = False
    try:
        with source.open('rb') as src, temporary.open('xb') as dst:
            owns_temporary = True
                                                                         
                                                                         
                                       
            copied = hashlib.sha256()
            while block := src.read(1 << 20):
                copied.update(block)
                if dst.write(block) != len(block):
                    raise OSError('Short VM3 seed write')
        digest = copied.hexdigest()
        if vm.sha(temporary)!=digest:
            raise ValueError('Prior VM3 byte-for-byte copy failed verification')
        if migrated:
            qimg=str(QEMU/'qemu-img.exe')
                                                                               
                                                                            
                                                                       
            with (out/'linux-vw-seed-rebase.log').open('xb') as log:
                subprocess.run([qimg,'rebase','-u','-f','qcow2','-F','qcow2',
                                '-b',str(backing),str(temporary)],stdout=log,stderr=log,timeout=45,check=True)
            target_info=json.loads(subprocess.check_output(
                [qimg,'info','--output=json',str(temporary)],timeout=45))
            target_map=json.loads(subprocess.check_output(
                [qimg,'map','--output=json',str(temporary)],timeout=45))
            if (target_info['format']!='qcow2' or target_info['virtual-size']!=info['virtual-size']
                    or target_info.get('dirty-flag') or target_info.get('backing-filename-format')!='qcow2'
                    or Path(target_info['full-backing-filename']).resolve()!=backing
                    or private_ranges(target_map)!=private_ranges(allocation_rows)):
                raise ValueError('VM3 seed changed beyond its verified backing reference')
        if (vm.sha(source)!=digest or vm.sha(backing)!=media['overlay_sha256']
                or migrated and vm.sha(prior_backing)!=prior['parent_sha256']):
            raise ValueError('Prior VM3 image or factory backing changed during copy')
        temporary.rename(target)
    finally:
        if owns_temporary:
            temporary.unlink(missing_ok=True)
    result=dict(profile=PROFILE,owner='VM3 / UFS VH1',path=str(target),parent=str(backing),
                parent_sha256=media['overlay_sha256'],prior_session=str(previous),
                prior_overlay_sha256=digest,prior_allocated_bytes_by_slot=slots|factory_cow,
                prior_factory_media_cow_bytes_by_slot=factory_cow,
                prior_weston_clusters_verified=sum(length//info['cluster-size'] for _,length in readonly_verified),
                original_base_read_only=True,factory_lun_acl=False)
    if migrated:
        result['vw_install_seed_transition']=dict(old_parent=str(prior_backing), replaced_slot=23,
                                                 original_seed_unchanged=True,private_allocations_identical=True,
                                                 no_old_vw_verity_marker=True)
    return result


def create(profile, base, media, out, previous=None, *, weston_plan=None):

    if profile != PROFILE or [p['slot'] for p in media.get('additional_partitions', [])] not in ([30], [18,30]):
        raise ValueError('IVI userdata requires verified factory HMI and lg_custom media')
    out = Path(out).resolve(strict=True)
    if not out.is_relative_to(ROOT/'var') or out == ROOT/'var':
        raise ValueError('IVI userdata requires project scratch')
    if previous is not None:
        return reuse(base, media, out, previous,weston_plan=weston_plan)
    parent = Path(media['overlay']).resolve(strict=True)
    offset, length = geometry(Path(base).with_suffix('.json'))
    volume = out/'linux-data-empty.ext4'
    target = out/'ufs-vh1-data-session.qcow2'
    if target.exists():
        raise ValueError('Require a fresh IVI userdata qcow2')
    with volume.open('x+b') as f:
        _mark_sparse(f)
        f.truncate(length)
    cmd = [str(MKFS), '-F', '-t', 'ext4', '-b', '4096', '-L', 'ivi-data',
           '-O', '^metadata_csum_seed,^orphan_file', '-E',
           'nodiscard,lazy_itable_init=0,lazy_journal_init=0', str(volume), str(length//4096)]
    with (out/'linux-data-mkfs.log').open('xb') as log:
        subprocess.run(cmd, stdout=log, stderr=log, check=True, timeout=45)
    from dissect.extfs import ExtFS
    with volume.open('rb') as f:
        fs = ExtFS(f)
        if set(fs.root.listdir())-{'lost+found', '.', '..'} or fs.volume_name != 'ivi-data':
            raise ValueError('IVI blank ext4 unexpectedly contains files')
    with (out/'linux-data-qemu-img.log').open('xb') as log:
        subprocess.run([str(QEMU/'qemu-img.exe'), 'create', '-f', 'qcow2', '-F', 'qcow2',
                        '-b', str(parent), str(target)], stdout=log, stderr=log, check=True, timeout=45)
    port = vm.free_port()
    before = hashlib.sha256()
    actual = hashlib.sha256()
    with (out/'linux-data-nbd.log').open('xb') as log:
        proc = subprocess.Popen([str(QEMU/'qemu-nbd.exe'), '--bind=127.0.0.1', f'--port={port}',
                                 '--format=qcow2', '--persistent', str(target)], stdout=log, stderr=log,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            with connect(proc, port) as sock, volume.open('rb') as source:
                nbd = NBD(sock)
                if nbd.size != media['virtual_bytes'] or not nbd.flags&4:
                    raise ValueError('IVI userdata NBD size or flush mismatch')
                for pos in range(0, length, UNIT):
                    count = min(UNIT, length-pos)
                    block = source.read(count)
                    if len(block) != count or any(nbd.command(0, offset+pos, count).strip(bytes(1))):
                        raise ValueError('Short IVI ext4 or nonempty factory /data backing')
                    before.update(block)
                    if block.count(0) != count:
                        nbd.command(1, offset+pos, count, block)
                nbd.command(3)
                for pos in range(0, length, UNIT):
                    actual.update(nbd.command(0, offset+pos, min(UNIT, length-pos)))
                nbd.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    if before.digest() != actual.digest():
        raise ValueError('IVI userdata ext4 full read-back failed')
    allocation = private_allocations(target, offset, length)
    if not allocation['only_data_slot'] or vm.sha(parent) != media['overlay_sha256']:
        raise ValueError('Unexpected IVI userdata allocation or altered read-only parent')
    return dict(profile=profile, owner='VM3 / UFS VH1', path=str(target), parent=str(parent),
                parent_sha256=media['overlay_sha256'], offset=offset, length=length, slot=SLOT,
                init_sha256=actual.hexdigest(), init_volume=str(volume), init_only_empty_ext4=True,
                full_readback=True, original_base_read_only=True, factory_lun_acl=False,
                init_allocation=allocation, mkfs_command=cmd)
