
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
from dissect.extfs import ExtFS
from linux_hmi_overlay import NBD, QEMU, connect, part_geometry
import original_8704_vm as vm

PAGE=65536
EXT4_VERITY_FL=0x00100000
FILES=('/prework_hmi.sh','/hmiroot.sh','/hmi/runHMI.sh','/hmi/hmi_version',
       '/ssoa_manifests/alias/static_alias_collection.mose')


class Slice:
    def __init__(self, peer, base, length):
        self.peer,self.base,self.length,self.pos=peer,base,length,0

    @lru_cache(maxsize=64)
    def block(self, offset):
        return self.peer.command(0,self.base+offset,min(PAGE,self.length-offset))

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        pos=(0,self.pos,self.length)[whence]+offset
        if not 0<=pos<=self.length:
            raise ValueError('Read moved outside OEM slot 23')
        self.pos=pos
        return pos

    def read(self, size):
        if not 0<=size<=1<<20:
            raise ValueError('Bounded slot 23 reads only')
        remaining=min(size,self.length-self.pos)
        result=bytearray()
        while remaining:
            start=self.pos//PAGE*PAGE
            data=self.block(start)
            within=self.pos-start
            n=min(remaining,len(data)-within)
            result.extend(data[within:within+n])
            self.pos+=n
            remaining-=n
        return bytes(result)


def inspect(fs):
    files={}
    for path in FILES:
        node=fs.get(path)
        if not 0<node.size<=1<<20 or node.inode.i_mode&0xf000!=0x8000:
            raise ValueError('Unexpected OEM verification member: '+path)
        with node.open() as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        flags=node.inode.i_flags
        files[path]=dict(inode=node.inum,bytes=node.size,sha256=digest,flags=hex(flags),
                         verity=bool(flags&EXT4_VERITY_FL))
    return files


def verify(session, media, output):
    def load(path):
        return json.loads(path.read_bytes())

    run=load(session/'run-observation.json')
    storage=load(session/'linux-ivi-data.json')
    target=Path(storage['path']).resolve(strict=True)
    if (not run.get('stop_reason') or target.parent!=session or target.name!='ufs-vh1-data-session.qcow2'
            or storage['owner']!='VM3 / UFS VH1' or not load(session/'integrity.json')['original_unchanged']):
        raise ValueError('Require completed original VM3 session and scoped writable layer')
    manifest=load(media)
    base=Path(load(session/'ufs-lun0-input.json')['base']).with_suffix('.json')
    offset,length=part_geometry(base)
    if (Path(storage['parent']).resolve()!=Path(manifest['overlay']).resolve()
            or storage['parent_sha256']!=manifest['overlay_sha256']
            or (manifest['offset'],manifest['length'])!=(offset,length)):
        raise ValueError('Session /vw backing does not match the verified media')
    with Path(manifest['volume']).open('rb') as raw:
        fs=ExtFS(raw)
        before=inspect(fs)
    port=vm.free_port()
    with output.with_suffix('.nbd.log').open('xb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
            '--format=qcow2','--read-only','--persistent',str(target)],stdout=log,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock:
                peer=NBD(sock)
                if peer.size!=manifest['virtual_bytes'] or not peer.flags&2:
                    raise ValueError('Expected matching read-only NBD export')
                after=inspect(ExtFS(Slice(peer,offset,length)))
                peer.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    for path in FILES:
        if before[path]['sha256']!=after[path]['sha256'] or before[path]['verity']:
            raise ValueError('Factory bytes or unprovisioned baseline mismatch: '+path)
    return dict(slot=23,source=str(target),immutable_media=str(media),read_only=True,
                verified_files=sum(x['verity'] for x in after.values()),before=before,after=after)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ('session','media','output'):
        p.add_argument(arg,type=Path)
    a=p.parse_args()
    result=verify(a.session.resolve(strict=True),a.media.resolve(strict=True),a.output.resolve())
    vm.save(a.output,result)
    print(json.dumps({'verified_files':result['verified_files'],'selected_files':len(FILES)}))
