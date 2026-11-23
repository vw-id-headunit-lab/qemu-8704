
import argparse
from contextlib import contextmanager
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import tarfile
import time

import original_8704_vm as vm
from ufs_virtual_disk import verify

ROOT=Path(__file__).resolve().parents[2]
QEMU=ROOT/'.local/deps/bin'
UNIT=2<<20
SLOT=23
NAVI_SLOT=18
NAVI_MEMBER='ivi/agl-navi_core.ext4.sparse.vw_cn'
CPU_ARCHIVE=ROOT/'var/inputs/packages/ICAS3CN_CPU.CPU_2025121-0317/31/update.tgz'
NAVI_ID=(507420808,'b19a8e48a6fbad45a4b392a21ffa43491e1747db47a0895a49105dfa0df040e5')
VW_INSTALL_BASE=ROOT/'var/inputs/support/vw-verity-native/overlay-verity/result.json'
VW_INSTALL_SCRIPTS={
    '/etc/smack/conf/vw_rem_up.sh':'563bfa1f29a25a2b0999074789fd4448d0173e2540b66b6b76921d1205653da1',
    '/etc/smack/conf/smack_label.sh':'119004d4c876543bd62f9202d3ed06ad3317e5cf5132e6be538db37bf3f90209',
}


def recv(sock, count):
    out=bytearray()
    while len(out)<count:
        piece=sock.recv(count-len(out))
        if not piece:
            raise EOFError('NBD reply ended early')
        out.extend(piece)
    return bytes(out)


class NBD:
    def __init__(self,sock):
        self.sock=sock
        self.handle=0
        magic,opt,flags=struct.unpack('>8s8sH',recv(sock,18))
        if (magic,opt)!=(b'NBDMAGIC',b'IHAVEOPT') or flags&3!=3:
            raise ValueError('QEMU NBD lacks fixed newstyle/no-zeroes')
        sock.sendall(struct.pack('>IQII',3,0x49484156454f5054,1,0))
        self.size,self.flags=struct.unpack('>QH',recv(sock,10))

    def command(self,code,offset=0,length=0,data=None):
        if not 0<=offset<=offset+length<=self.size or length>UNIT:
            raise ValueError('NBD extent outside virtual disk or transfer limit')
        self.handle+=1
        self.sock.sendall(struct.pack('>IHHQQI',0x25609513,0,code,self.handle,offset,length))
        if data is not None:
            self.sock.sendall(data)
        if code==2:
            return None
        magic,error,handle=struct.unpack('>IIQ',recv(self.sock,16))
        if magic!=0x67446698 or error or handle!=self.handle:
            raise OSError(f'Unexpected NBD reply {magic:#x}/{error}/{handle}')
        return recv(self.sock,length) if code==0 else None


def connect(proc,port):
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError(f'Local QEMU NBD exited: {proc.returncode}')
        try:
            return socket.create_connection(('127.0.0.1',port),timeout=5)
        except OSError:
            time.sleep(.05)
    raise TimeoutError('Local QEMU NBD did not listen')


def part_geometry(base_manifest, slot=SLOT):
    labels={NAVI_SLOT:'navi_core', SLOT:'vw_hmi_ivi_rbhv-qia', 30:'lg_custom_ivi_rbhv-qia'}
    data=json.loads(base_manifest.read_bytes())
    rows=[r for r in data['partitions'] if r['slot']==slot]
    if (slot not in labels or data['sector_bytes']!=4096 or len(rows)!=1
            or rows[0]['label']!=labels[slot] or rows[0].get('payload')):
        raise ValueError(f'Verified derived base does not have unpopulated OEM Linux slot {slot}')
    p=rows[0]
    return p['first']*4096,(p['last']-p['first']+1)*4096


@contextmanager
def factory_tar_member(archive, relative, size):
                                                                                   
                                                                        
    with gzip.open(archive,'rb') as inflated, tarfile.open(fileobj=inflated,mode='r|',bufsize=1<<20) as tar:
        for member in tar:
            if member.name.removeprefix('./') == relative:
                if not member.isfile() or member.size != size:
                    raise ValueError('Unexpected factory CPU archive member')
                with tar.extractfile(member) as src:
                    yield src
                return
    raise FileNotFoundError(relative)


def restore_sparse(out, geometry, src, provenance, slot, name, identity=None):
    from ufs_android_sparse import expand
    from ufs_virtual_disk import _mark_sparse

    offset,length=geometry
    volume=out/f'{name}.ext4'
    with volume.open('x+b') as target:
        _mark_sparse(target)
        target.truncate(length)
        digest=expand(src,target,0,length)
        if identity is not None and (digest.input_size,digest.input_sha256)!=identity:
            raise ValueError(f'Encoded factory {name} differs from pinned member')
        target.seek(1024+56)
        if target.read(2)!=bytes((0x53,0xef)):
            raise ValueError(f'Decoded factory {name} has no ext4 signature')
    if vm.sha(volume)!=digest.output_sha256:
        raise ValueError(f'Decoded factory {name} ext4 bytes differ on disk')
    vm.save(out/f'{name}-source.json',dict(provenance,gpt_slot=slot,**asdict(digest)))
    return dict(slot=slot,offset=offset,length=length,volume=str(volume),volume_sha256=digest.output_sha256)


def restore_lg_custom(out, geometry):
    from vlm_platform import factory_file

    source=factory_file('ivi/agl-lge_custom.ext4.sparse')
    with source.open('rb') as src:
        return restore_sparse(out,geometry,src,dict(source=str(source)),30,'lg-custom')


def restore_navi_core(out, geometry):
    with factory_tar_member(CPU_ARCHIVE,NAVI_MEMBER,NAVI_ID[0]) as src:
        return restore_sparse(out,geometry,src,dict(source=str(CPU_ARCHIVE),member=NAVI_MEMBER),
                              NAVI_SLOT,'navi-core',NAVI_ID)


def create(media,base_manifest,out,lg_custom=False,navi_core=False):
    out=out.resolve()
    if not out.is_relative_to(ROOT/'var') or out==ROOT/'var':
        raise ValueError('Use a new project scratch output')
    out.mkdir(exist_ok=False,parents=True)
    info=json.loads(media.read_bytes())
    volume=Path(info['ext4']).resolve(strict=True)
    if not info['all_zip_bytes_and_unix_modes_owners_links_verified'] or info['gpt_slot']!=SLOT:
        raise ValueError('HMI media was not verified')
    verify(base_manifest)
    base=(base_manifest.parent/'ufs-lun0.raw').resolve(strict=True)
    offset,length=part_geometry(base_manifest)
    if length!=info['bytes'] or volume.stat().st_size!=length:
        raise ValueError('HMI ext4 does not match slot geometry')
    extents=[dict(slot=SLOT,offset=offset,length=length,volume=str(volume),volume_sha256=info['ext4_sha256'])]
    if navi_core:
        extents.append(restore_navi_core(out,part_geometry(base_manifest,NAVI_SLOT)))
    if lg_custom:
        extents.append(restore_lg_custom(out,part_geometry(base_manifest,30)))
    overlay=out/'vm3-vw-lun0.qcow2'
    qimg=QEMU/'qemu-img.exe'
    with (out/'qemu-img.log').open('wb') as log:
        subprocess.run([str(qimg),'create','-f','qcow2','-F','raw','-b',str(base),str(overlay)],
                       stdout=log,stderr=log,check=True,timeout=40)
    port=vm.free_port()
    with (out/'nbd.log').open('wb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
                               '--format=qcow2','--persistent',str(overlay)],stdout=log,stderr=log,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock,base.open('rb') as bf:
                endpoint=NBD(sock)
                if endpoint.size!=base.stat().st_size or not endpoint.flags&4:
                    raise ValueError('Unexpected QEMU NBD virtual size or no flush')
                for row in extents:
                    offset,length=row['offset'],row['length']
                    bf.seek(offset)
                    sent=0
                    src_hash=hashlib.sha256()
                    with Path(row['volume']).open('rb') as vf:
                        for p in range(0,length,UNIT):
                            n=min(UNIT,length-p)
                            data,prior=vf.read(n),bf.read(n)
                            if len(data)!=n or len(prior)!=n or prior.count(0)!=n:
                                raise ValueError('OEM partition source or empty backing extent changed')
                            src_hash.update(data)
                            if data.count(0)!=n:
                                endpoint.command(1,offset+p,n,data)
                                sent+=n
                    if src_hash.hexdigest()!=row['volume_sha256']:
                        raise ValueError(f"OEM ext4 digest changed in slot {row['slot']}")
                    endpoint.command(3)
                    read_hash=hashlib.sha256()
                    for p in range(0,length,UNIT):
                        read_hash.update(endpoint.command(0,offset+p,min(UNIT,length-p)))
                    if src_hash.digest()!=read_hash.digest():
                        raise ValueError(f"QCOW2 slot {row['slot']} read-back mismatch")
                    row.update(bytes_written=sent,complete_readback=True)
                endpoint.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    mapping=json.loads(subprocess.check_output([str(qimg),'map','--output=json',str(overlay)],timeout=40))
    current=[r for r in mapping if r['depth']==0 and r.get('present',r['data'])]
    if not current or any(not any(e['offset']<=r['start']<r['start']+r['length']<=e['offset']+e['length']
                                 for e in extents) for r in current):
        raise ValueError('QCOW2 allocation exceeds verified OEM Linux slots')
    vm.save(out/'allocation.json',mapping)
    detail=dict(extents[0],base=str(base),overlay=str(overlay),overlay_sha256=vm.sha(overlay),
                virtual_bytes=base.stat().st_size,read_only_for_vm3=True,other_vms_unchanged=True)
    if len(extents)>1:
        detail['additional_partitions']=extents[1:]
    vm.save(out/'result.json',detail)
    return detail


def qimg_json(command, image):
    return json.loads(subprocess.check_output(
        [str(QEMU/'qemu-img.exe'),command,'--output=json',str(image)],timeout=60))


def private_ranges(mapping):
    return [(r['start'],r['length']) for r in mapping if r['depth']==0 and r.get('present',r['data'])]


def require_no_slot23_shadow(mapping, media):
    lo, hi = media['offset'], media['offset']+media['length']
    if any(start<hi and lo<start+length for start,length in private_ranges(mapping)):
        raise ValueError('The prior VM3 leaf shadows the replaced slot 23')


def seed_backing(media, prior):

    selected = Path(media['overlay']).resolve(strict=True)
    if Path(prior['parent']).resolve() == selected and prior['parent_sha256']==media['overlay_sha256']:
        return selected
    previous = media.get('install_metadata_transition')
    if previous and Path(prior['parent']).resolve() == Path(previous['predecessor']).resolve() and (
            prior['parent_sha256']==previous['predecessor_sha256']):
        return Path(previous['predecessor']).resolve(strict=True)
    raise ValueError('Prior VM3 backing is neither the selected media nor its verified VW predecessor')


def validate_install_source(proof_file, old):
    proof_file=Path(proof_file).resolve(strict=True)
    p=json.loads(proof_file.read_bytes())
    volume=Path(p['ext4']).resolve(strict=True)
    label=dict(uid=724,gid=44,labels={'SMACK64':'hmi::sub','SMACK64EXEC':'hmi::sub'})
    if (not proof_file.is_relative_to(ROOT/'var') or volume!=proof_file.parent/'vw-hmi.ext4'
            or p['label_script']!=VW_INSTALL_SCRIPTS or p['gpt_slot']!=SLOT or p['path']!='/vw'
            or p['base_sha256']!=old['volume_sha256'] or p['bytes']!=old['length']
            or volume.stat().st_size!=old['length'] or p['entries']!=1726
            or p['original_unchanged'] is not True or p['guest_hmi_executed'] is not False
            or p['all_payload_bytes_modes_symlinks_verified'] is not True or p['ext4_verity_capable'] is not True
            or p['selected_metadata']!={'/hmiroot.sh':label,'/hmi/runHMI.sh':label}):
        raise ValueError('Unverified OEM install metadata volume')
    return p,volume


def install_transition(old, proof_file, overlay, ranges):
    p, volume = validate_install_source(proof_file, old)
    info=qimg_json('info',overlay)
    expected=Path(old['overlay']).resolve(strict=True)
    if (info['format']!='qcow2' or info.get('dirty-flag')
            or info.get('format-specific',{}).get('data',{}).get('corrupt')
            or info['virtual-size']!=old['virtual_bytes'] or info.get('backing-filename-format')!='qcow2'
            or Path(info['full-backing-filename']).resolve()!=expected
            or not ranges or any(not old['offset']<=a<a+n<=old['offset']+old['length'] for a,n in ranges)):
        raise ValueError('VW install delta is not a private slot-23-only overlay')
    return dict(proof=str(Path(proof_file).resolve()),predecessor=str(expected),
                predecessor_sha256=old['overlay_sha256'],private_slot=23,
                slot_sha256=p['ext4_sha256'],private_allocated_bytes=sum(n for _,n in ranges))


def build_install_overlay(proof_file, base_manifest, out):

    out=Path(out).resolve()
    if not out.is_relative_to(ROOT/'var') or out==ROOT/'var':
        raise ValueError('Use a new scratch output for OEM install media')
    base_manifest=Path(base_manifest).resolve(strict=True)
    base=base_manifest.with_suffix('.raw').resolve(strict=True)
    old=launch_policy(VW_INSTALL_BASE,base)
    p,volume=validate_install_source(proof_file,old)
    out.mkdir(parents=False,exist_ok=False)
    overlay=out/'vm3-vw-install-lun0.qcow2'
    with (out/'qemu-img.log').open('xb') as log:
        subprocess.run([str(QEMU/'qemu-img.exe'),'create','-f','qcow2','-F','qcow2',
                        '-b',old['overlay'],str(overlay)],stdout=log,stderr=log,timeout=45,check=True)
    port=vm.free_port()
    write_hash,old_hash,read_hash=hashlib.sha256(),hashlib.sha256(),hashlib.sha256()
    sent=0
    with (out/'nbd.log').open('xb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
                              '--format=qcow2','--persistent',str(overlay)],stdout=log,stderr=log,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock,Path(old['volume']).open('rb') as before,volume.open('rb') as after:
                endpoint=NBD(sock)
                if endpoint.size!=old['virtual_bytes'] or not endpoint.flags&4:
                    raise ValueError('Unexpected VM3 install NBD geometry')
                for pos in range(0,old['length'],UNIT):
                    count=min(UNIT,old['length']-pos)
                    orig,new=before.read(count),after.read(count)
                    if len(orig)!=count or len(new)!=count:
                        raise ValueError('Short OEM VW volume read')
                    write_hash.update(new)
                    old_hash.update(orig)
                    if orig!=new:
                        endpoint.command(1,old['offset']+pos,count,new)
                        sent+=count
                endpoint.command(3)
                for pos in range(0,old['length'],UNIT):
                    read_hash.update(endpoint.command(0,old['offset']+pos,min(UNIT,old['length']-pos)))
                endpoint.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    if (not sent or old_hash.hexdigest()!=old['volume_sha256'] or
            read_hash.hexdigest()!=p['ext4_sha256'] or write_hash.digest()!=read_hash.digest()
            or vm.sha(old['overlay'])!=old['overlay_sha256']):
        raise ValueError('OEM metadata slot readback or immutable predecessor integrity failed')
    ranges=private_ranges(qimg_json('map',overlay))
    detail=dict(old,volume=str(volume),volume_sha256=p['ext4_sha256'],bytes_written=sent,
                overlay=str(overlay),overlay_sha256=vm.sha(overlay),
                install_metadata_transition=install_transition(old,proof_file,overlay,ranges))
    vm.save(out/'result.json',detail)
    return detail


def launch_policy(result_file,base):
    if result_file is None:
        return None
    row=json.loads(result_file.read_bytes())
    overlay=Path(row['overlay']).resolve(strict=True)
    raw=Path(base).resolve(strict=True)
    extra=row.get('additional_partitions',[])
    if (row['slot']!=SLOT or [r['slot'] for r in extra] not in ([],[NAVI_SLOT],[30],[NAVI_SLOT,30])
            or Path(row['base']).resolve()!=raw or row['virtual_bytes']!=raw.stat().st_size
            or row.get('read_only_for_vm3') is not True or row.get('other_vms_unchanged') is not True
            or not overlay.is_relative_to(ROOT/'var') or vm.sha(overlay)!=row['overlay_sha256']):
        raise ValueError('VM3 OEM Linux overlay identity mismatch')
    for part in [row,*extra]:
        volume=Path(part['volume']).resolve(strict=True)
        p=part_geometry(raw.with_suffix('.json'),part['slot'])
        if (p!=(part['offset'],part['length']) or not part['complete_readback']
                or volume.stat().st_size!=part['length'] or vm.sha(volume)!=part['volume_sha256']):
            raise ValueError(f"VM3 OEM volume slot {part['slot']} layout or digest mismatch")
    if 'install_metadata_transition' in row:
        if Path(result_file).resolve()==VW_INSTALL_BASE.resolve():
            raise ValueError('The pinned predecessor must not be an install delta')
        prior=launch_policy(VW_INSTALL_BASE,raw)
        expected=install_transition(prior,row['install_metadata_transition']['proof'],overlay,
                                    private_ranges(qimg_json('map',overlay)))
        if (row['install_metadata_transition']!=expected or row['volume_sha256']!=expected['slot_sha256']
                or Path(row['volume']).resolve()!=Path(expected['proof']).with_name('vw-hmi.ext4')
                or extra!=prior.get('additional_partitions',[])):
            raise ValueError('Install delta may change only the verified VW media')
    return row


def expected_reader(base, row):

    segments=[]
    previous=0
    for part in sorted([row,*row.get('additional_partitions',[])],key=lambda part:part['offset']):
        start,end=part['offset'],part['offset']+part['length']
        if not previous<=start<end<=row['virtual_bytes']:
            raise ValueError('Invalid or overlapping Linux media extents')
        segments.extend(((previous,start,Path(base),0),(start,end,Path(part['volume']),start)))
        previous=end
    segments.append((previous,row['virtual_bytes'],Path(base),0))

    def read(offset,length):
        if not 0<=offset<=offset+length<=row['virtual_bytes']:
            raise ValueError('Native READ outside OEM disk')
        chunks=[]
        for low,high,path,origin in segments:
            lo,hi=max(offset,low),min(offset+length,high)
            if lo<hi:
                with path.open('rb') as f:
                    f.seek(lo-origin)
                    data=f.read(hi-lo)
                if len(data)!=hi-lo:
                    raise EOFError('Native READ media changed or truncated')
                chunks.append(data)
        return b''.join(chunks)
    return read


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--media',type=Path)
    source.add_argument('--install-metadata',type=Path,help='Create a verified slot-23-only OEM install delta')
    p.add_argument('--base',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--lg-custom',action='store_true',help='Restore verified OEM lg_custom slot 30 alongside HMI')
    p.add_argument('--navi-core',action='store_true',help='Stream OEM navi_core from CPU update into slot 18')
    a=p.parse_args()
    if a.install_metadata:
        if a.lg_custom or a.navi_core:
            p.error('OEM install delta preserves every other verified predecessor slot')
        print(json.dumps(build_install_overlay(a.install_metadata,a.base,a.output)))
    else:
        print(json.dumps(create(a.media,a.base,a.output,a.lg_custom,a.navi_core)))
