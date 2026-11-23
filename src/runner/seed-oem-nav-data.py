
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import uuid

from dissect.extfs import ExtFS
from linux_data_overlay import IVI_WRITABLE, MKFS
from linux_hmi_overlay import NBD, QEMU, UNIT, connect
import original_8704_vm as vm
from ufs_virtual_disk import _mark_sparse
from vlm_platform import factory_file

SLOT=26
LABEL='nav_rw_data_ivi_rbhv-qia'
FS_LABEL='ivi-navi-data'
OEM_FSTAB='ac06a5fdd55a136bb5dfc8bca1f17eeed2b7eacf001823ed743473977f695c52'


def allocations(target,start,end,disk_size):


    data=subprocess.check_output([str(QEMU/'qemu-img.exe'),'map','--output=json',str(target)],timeout=45)
    inside=0
    outside=[]
    last=0
    for row in json.loads(data):
        lo,hi=row['start'],row['start']+row['length']
        if not last<=lo<hi<=disk_size:
            raise ValueError('Invalid or overlapping private VM3 allocation map')
        last=hi
        if row['depth'] or not row.get('present',row['data']):
            continue
        inside+=max(0,min(hi,end)-max(lo,start))
        for a,b in ((lo,min(hi,start)),(max(lo,end),hi)):
            if a>=b:
                continue
            if outside and outside[-1][1]==a:
                outside[-1]=(outside[-1][0],b)
            else:
                outside.append((a,b))
    return inside,outside


def prior_nav_data(overlay,offset,length):

    from linux_pid1_context import LINUX_SHA
    if not overlay.get('prior_session'):
        raise ValueError('Unproven pre-existing VM3 /navi_data volume; no writes made')
    prior=Path(overlay['prior_session']).resolve(strict=True)
    integrity=json.loads((prior/'integrity.json').read_bytes())
    if (not (prior/'result.json').is_file() or integrity.get('original_unchanged') is not True
            or type(integrity.get('exit_code')) is not int or integrity['exit_code']!=0):
        raise ValueError('The inherited VM3 navigation data session is not cleanly closed')
    def read_optional(name):
        path=prior/name
        return json.loads(path.read_bytes()) if path.is_file() else {}
    snapshot=read_optional('linux-original-userspace.json')
    mounts=[r for r in snapshot.get('mounts',[]) if r.get('mountpoint')=='/navi_data']
    native=(snapshot.get('kernel_sha256')==LINUX_SHA and snapshot.get('status')=='verified_process_snapshot'
            and snapshot.get('mounts_verified') is True and len(mounts)==1 and all(
                mounts[0].get(k)==v for k,v in dict(source='/dev/sda26',fstype='ext4',dev_major=259,dev_minor=9).items()))
    old=read_optional('linux-nav-data-material.json')
    digest=old.get('full_readback_sha256')
    derived=(old.get('slot')==SLOT and old.get('partlabel')==LABEL and old.get('offset')==offset
             and old.get('length')==length and old.get('fs_label')==FS_LABEL
             and old.get('parent_unchanged') is True and old.get('only_slot26') is True
             and old.get('original_fstab_sha256')==OEM_FSTAB and
             ((isinstance(digest,str) and re.fullmatch(r'[a-f0-9]{64}',digest) is not None)
              or old.get('disposition')=='inherited-read-only'))
    if not native and not derived:
        raise ValueError('No prior OEM mount or verified provision evidence for existing /navi_data')
    return prior,native,derived


def ext4_header(raw,length):

    if len(raw)!=4096:
        raise ValueError('Short /navi_data superblock read')
    sb=raw[1024:2048]
    u32=lambda at:struct.unpack_from('<I',sb,at)[0]
    features=u32(0x60)
    blocks=u32(4)|(u32(0x150)<<32 if features&0x80 else 0)
    if (sb[0x38:0x3a]!=b'\x53\xef' or u32(0x18)!=2 or blocks*4096!=length
            or not any(sb[0x68:0x78])):
        raise ValueError('Inherited /navi_data is not the expected full-size ext4 volume')
    return sb[0x78:0x88].split(b'\0',1)[0].decode('ascii'),str(uuid.UUID(bytes=sb[0x68:0x78]))


def reuse_nav_data(out,target,overlay,offset,length,disk_size,allocated):
    prior,native,derived=prior_nav_data(overlay,offset,length)
    port=vm.free_port()
    with (out/'linux-nav-data-nbd.log').open('xb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--read-only','--bind=127.0.0.1',f'--port={port}',
            '--format=qcow2',str(target)],stdout=log,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock:
                nbd=NBD(sock)
                if nbd.size!=disk_size or not nbd.flags&2:
                    raise ValueError('Inherited /navi_data requires read-only NBD')
                label,fs_uuid=ext4_header(nbd.command(0,offset,4096),length)
                if derived and not native and label!=FS_LABEL:
                    raise ValueError('Inherited provisioned /navi_data label changed')
                nbd.command(2)
        finally:
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired: proc.terminate(); proc.wait(timeout=4)
    proof=dict(slot=SLOT,partlabel=LABEL,offset=offset,length=length,fs_label=label,fs_uuid=fs_uuid,
               allocation_bytes=allocated,original_fstab_sha256=OEM_FSTAB,parent_unchanged=True,
               only_slot26=True,disposition='inherited-read-only',prior_session=str(prior),
               original_mount_in_prior=native,prior_provision_evidence=derived,
               scope='Existing ext4 header and cloned bytes; no write, format, or fsck claim')
    vm.save(out/'linux-nav-data-material.json',proof)
    return dict(overlay,navigation_data=proof)


def install(base,media,out,overlay):
    out=Path(out).resolve(strict=True)
    target=Path(overlay['path']).resolve(strict=True)
    parent=Path(media['overlay']).resolve(strict=True)
    if (not (overlay.get('prior_session') or overlay.get('init_only_empty_ext4') is True)
            or target.parent!=out or target.name!='ufs-vh1-data-session.qcow2'
            or target==parent or overlay.get('owner')!='VM3 / UFS VH1'):
        raise ValueError('Require a new VM3 private layer with verified /data provenance')
    with factory_file('ivi/icas3cn-gp-image-euto-v9.ext4').open('rb') as root:
        with ExtFS(root).get('/etc/fstab').open() as stream:
            fstab=stream.read(8192)
    if hashlib.sha256(fstab).hexdigest()!=OEM_FSTAB or not any(
            row.split()[:3]==[f'PARTLABEL={LABEL}'.encode(),b'/navi_data',b'ext4'] for row in fstab.splitlines()):
        raise ValueError('Original OEM /navi_data mount contract changed')
    layout=json.loads(Path(base).with_suffix('.json').read_bytes())
    if layout['sector_bytes']!=4096:
        raise ValueError('Unexpected virtual UFS sector size')
    ranges={r['slot']:(r['first']*4096,(r['last']+1)*4096) for r in layout['partitions']
            if IVI_WRITABLE.get(r['slot'])==r['label'] and not r.get('payload')}
    offset,end=ranges[SLOT]
    length=end-offset
    if set(ranges)!=set(IVI_WRITABLE) or length!=1<<30:
        raise ValueError('Original writable GPT layout or /navi_data geometry changed')
    before_inside,before_outside=allocations(target,offset,end,media['virtual_bytes'])
    if before_inside:
        return reuse_nav_data(out,target,overlay,offset,length,media['virtual_bytes'],before_inside)
    volume=out/'linux-nav-data-empty.ext4'
    with volume.open('x+b') as f:
        _mark_sparse(f)
        f.truncate(length)
    command=[str(MKFS),'-F','-t','ext4','-b','4096','-L',FS_LABEL,
             '-O','^metadata_csum_seed,^orphan_file','-E','nodiscard,lazy_itable_init=0,lazy_journal_init=0',
             str(volume),str(length//4096)]
    with (out/'linux-nav-data-mkfs.log').open('xb') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=45)
    with volume.open('rb') as f:
        fs=ExtFS(f)
        if fs.volume_name!=FS_LABEL or set(fs.root.listdir())-{'lost+found','.','..'}:
            raise ValueError('Private navigation data volume is not the expected blank ext4')
    expected=hashlib.sha256()
    actual=hashlib.sha256()
    port=vm.free_port()
    with (out/'linux-nav-data-nbd.log').open('xb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
            '--format=qcow2','--persistent',str(target)],stdout=log,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock, volume.open('rb') as source:
                nbd=NBD(sock)
                if nbd.size!=media['virtual_bytes'] or not nbd.flags&4:
                    raise ValueError('Private navigation data NBD geometry or flush mismatch')
                for at in range(0,length,UNIT):
                    block=nbd.command(0,offset+at,min(UNIT,length-at))
                    if block.count(0)!=len(block):
                        raise ValueError('Slot 26 is not blank; no writes made')
                for at in range(0,length,UNIT):
                    size=min(UNIT,length-at)
                    block=source.read(size)
                    if len(block)!=size:
                        raise ValueError('Truncated derived navigation data volume')
                    expected.update(block)
                    if block.count(0)!=size:
                        nbd.command(1,offset+at,size,block)
                nbd.command(3)
                for at in range(0,length,UNIT):
                    actual.update(nbd.command(0,offset+at,min(UNIT,length-at)))
                nbd.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    after_inside,after_outside=allocations(target,offset,end,media['virtual_bytes'])
    if (actual.digest()!=expected.digest() or not after_inside or
            after_outside!=before_outside or
            vm.sha(parent)!=overlay['parent_sha256']):
        raise ValueError('Slot 26 readback, isolation, or immutable backing check failed')
    proof=dict(slot=SLOT,partlabel=LABEL,offset=offset,length=length,fs_label=FS_LABEL,
               full_readback_sha256=actual.hexdigest(),allocation_bytes=after_inside,
               original_fstab_sha256=OEM_FSTAB,parent_unchanged=True,only_slot26=True,
               disposition='provisioned-blank')
    volume.unlink()                                                                              
    vm.save(out/'linux-nav-data-material.json',proof)
    return dict(overlay,navigation_data=proof)
