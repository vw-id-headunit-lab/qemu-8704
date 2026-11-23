
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess

from dissect.extfs import ExtFS

import linux_data_overlay as data_overlay
from linux_hmi_overlay import NBD, QEMU, ROOT, UNIT, connect, seed_backing
from linux_pid1_context import LINUX_SHA
from original_8704_vm import free_port, sha

NAME = 'ufs-vh1-data-session.qcow2'
IME = {
    'ChineseMainlandUser.dic': (84280, '9947671fb39f7bcf4f6fdde3b501233b6a73feb8e5d008ca34fc714494acc505'),
    'EnglishUser.dic': (257196, 'b0205c3762284ddf7c68a44fa0524e16a5921b600a208b0fb6bd2f7625a0c57f'),
}


def _load(path):
    return json.loads(path.read_bytes())


def _image(op, path):
    return json.loads(subprocess.check_output([str(QEMU/'qemu-img.exe'), op, '--output=json', str(path)], timeout=45))


@contextmanager
def _export(path, log, *, readonly, disk_size):
    port = free_port()
    cmd = [str(QEMU/'qemu-nbd.exe'), '--bind=127.0.0.1', f'--port={port}', '--format=qcow2']
    if readonly:
        cmd.append('--read-only')
    proc = subprocess.Popen(cmd+[str(path)], stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        with connect(proc,port) as sock:
            nbd = NBD(sock)
            if nbd.size != disk_size or bool(nbd.flags & 2) != readonly or (not readonly and not nbd.flags & 4):
                raise ValueError('Unexpected native VM3 NBD export contract')
            yield nbd
            nbd.command(2)
    finally:
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=4)


class _Slot(io.RawIOBase):
    def __init__(self, nbd, first, length):
        self.nbd, self.first, self.length = nbd, first, length
        self.pos = self.total = 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        bases = {0:0, 1:self.pos, 2:self.length}
        if whence not in bases or not 0 <= bases[whence]+offset <= self.length:
            raise ValueError('Read outside original VM3 /data slot')
        self.pos = bases[whence]+offset
        return self.pos

    def read(self, count=-1):
        if not 0 <= count <= UNIT or self.pos+count > self.length or self.total+count > 8*UNIT:
            raise ValueError('Out-of-scope OEM /data proof read')
        block = self.nbd.command(0,self.first+self.pos,count) if count else b''
        self.pos += count
        self.total += count
        return block


def require_no_stale_vw_verity_marker(fs):
                                                                            
                                                                         
    try:
        fs.get('/fsverity_vw.flag')
    except FileNotFoundError:
        return
    raise ValueError('Old /data marks a different VW volume as already enrolled')


def validate_contents(nbd, first, length, *, flight_recording=False, vm_log=False, vw_transition=False):

    fs = ExtFS(_Slot(nbd, first, length))
    if vw_transition:
        require_no_stale_vw_verity_marker(fs)
    if flight_recording or vm_log:
        from linux_early_uart import HMI_RECORDING_PATH, HMI_VM_LOG_PATH
        path=HMI_VM_LOG_PATH if vm_log else HMI_RECORDING_PATH
        try:
            fs.get(path.removeprefix('/data'))
        except FileNotFoundError:
            pass
        else:
            raise ValueError('HMI recording path already exists in the source data slot')
    for path in ('/post_init_done.flag','/post_init_variant.id'):
        try:
            fs.get(path)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('HMI seed unexpectedly already marks OEM post-init complete')
    dirs = ('/lge/hmi','/lge/hmi/cache','/lge/hmi/persist')
    for path in dirs:
        if fs.get(path).filetype != stat.S_IFDIR:
            raise ValueError('Not an original HMI directory: '+path)
    checked = {}
    for name,(size,digest) in IME.items():
        node = fs.get('/lge/ime_rw/'+name)
        if node.filetype != stat.S_IFREG or node.size != size:
            raise ValueError('Original China IME file geometry changed: '+name)
        with node.open() as stream:
            raw = stream.read(size+1)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Original China IME file bytes changed: '+name)
        checked[name] = dict(bytes=size,sha256=digest)
    return dict(directories=list(dirs),dictionary_files=checked,original_post_init_markers_absent=True)


def copy_slot(source, target, first, length):
    if (source.size != target.size or first % UNIT or length % UNIT or length != 1<<30
            or not 0 < first < first+length <= target.size or not source.flags&2 or target.flags&2 or not target.flags&4):
        raise ValueError('Copy requires one bounded, aligned /data slot on a read-only source')
    before, after = hashlib.sha256(), hashlib.sha256()
    changed = 0
    for at in range(first,first+length,UNIT):
        block = source.command(0,at,UNIT)
        if len(block) != UNIT:
            raise ValueError('Short original /data extent')
        before.update(block)
        if block != target.command(0,at,UNIT):
            target.command(1,at,UNIT,block)
            changed += UNIT
    target.command(3)
    for at in range(first,first+length,UNIT):
        after.update(target.command(0,at,UNIT))
    if before.digest()!=after.digest():
        raise ValueError('Native VM3 /data full read-back failed')
    return dict(slot_sha256=after.hexdigest(),verified_bytes=length,written_bytes=changed)


def _other_allocations(rows, first, length):
                                                                                
    kept=[]
    for r in rows:
        if r['depth']!=0 or not r['present']:
            continue
        a,b=r['start'],r['start']+r['length']
        for lo,hi in ((a,min(b,first)),(max(a,first+length),b)):
            if lo>=hi:
                continue
            if kept and kept[-1][1]==lo:
                kept[-1]=(kept[-1][0],hi)
            else:
                kept.append((lo,hi))
    return kept


def _candidate(source_dir, target, media):
    source = source_dir/NAME
    backing = Path(media['overlay']).resolve(strict=True)
    disk_size = media['virtual_bytes']
    if (not source_dir.is_relative_to(ROOT/'var') or source.resolve(strict=True)!=source or
            source==target or target.resolve(strict=True)!=target or not (source_dir/'result.json').is_file()):
        raise ValueError('Need independent, closed OEM VM3 source and new target')
    prior = _load(source_dir/'linux-ivi-data.json')
    prior_backing = seed_backing(media,prior)
    integrity = _load(source_dir/'integrity.json')
    snap = _load(source_dir/'linux-original-userspace.json')
    if (Path(prior['path']).resolve()!=source or prior['owner']!='VM3 / UFS VH1' or
            prior['profile']!=data_overlay.PROFILE or integrity['exit_code']!=0 or
            integrity['original_unchanged'] is not True or snap['status']!='verified_process_snapshot' or
            snap['kernel_sha256']!=LINUX_SHA or snap['mcu_identity']!={'platform':4,'market':4,'variant':0x65} or
            snap['mounts_verified'] is not True or not any(
                m['mountpoint']=='/data' and m['source']=='/dev/sda15' and m['fstype']=='ext4' and
                m['read_only'] is False for m in snap['mounts'])):
        raise ValueError('Original VM3 HMI seed provenance or /data mount is not verified')
                                                                             
    for p,parent in ((source,prior_backing),(target,backing)):
        info = _image('info',p)
        fmt = info.get('format-specific',{}).get('data',{})
        if (info['format']!='qcow2' or info.get('backing-filename-format')!='qcow2' or info.get('dirty-flag') or
                fmt.get('corrupt') or fmt.get('data-file') or info['virtual-size']!=disk_size or
                Path(info['full-backing-filename']).resolve()!=parent):
            raise ValueError('Closed HMI source/target qcow2 differs from the verified factory chain')
                                                                          
                                                                              
    return source, disk_size, prior_backing!=backing


def merge(source_dir, base, media, output, overlay, *, flight_recording=False, vm_log=False):
    output=Path(output).resolve(strict=True)
    target=Path(overlay['path'])
    if (target!=output/NAME or not output.is_relative_to(ROOT/'var') or
            overlay.get('owner')!='VM3 / UFS VH1' or overlay.get('original_base_read_only') is not True):
        raise ValueError('HMI reuse is restricted to the new native VM3 write layer')
    source_dir=Path(source_dir).resolve(strict=True)
    source,disk_size,vw_transition=_candidate(source_dir,target,media)
    first,length=data_overlay.geometry(Path(base).with_suffix('.json'))
    before=sha(source)
    other_before=_other_allocations(_image('map',target),first,length)
    with (output/'linux-hmi-userdata-nbd.log').open('xb') as log:
        with _export(source,log,readonly=True,disk_size=disk_size) as src:
            options=dict(flight_recording=flight_recording,vm_log=vm_log,vw_transition=vw_transition)
            evidence=validate_contents(src,first,length,**options)
            with _export(target,log,readonly=False,disk_size=disk_size) as dst:
                copied=copy_slot(src,dst,first,length)
                if validate_contents(dst,first,length,**options)!=evidence:
                    raise ValueError('Copied HMI files differ from the original guest output')
    if (sha(source)!=before or _other_allocations(_image('map',target),first,length)!=other_before):
        raise ValueError('HMI source changed or write escaped the /data slot')
    result=dict(prior_session=str(source_dir),prior_overlay_sha256=before,slot=15,offset=first,
                no_other_slot_changes=True,**evidence,**copied)
    if flight_recording:
        result['flight_recording_path_absent_before_boot']=True
    if vm_log:
        result['vm_log_path_absent_before_boot']=True
    if vw_transition:
        result['no_old_vw_verity_marker']=True
    return result
