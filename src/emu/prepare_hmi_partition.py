



import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
import struct
import subprocess
import tarfile
from zipfile import ZipFile

from dissect.extfs import ExtFS
from ufs_virtual_disk import _mark_sparse

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'var/inputs/packages/ICAS3CN_HMI.ICAS_CN_2025121-0317/31'
BLOCK = 4096
HMI_BYTES = 13631488 * 512
EXT4_RO_COMPAT_VERITY = 0x8000
INIT = b"""#!/bin/sh
set -eu
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
exec </dev/null >/dev/ttyAMA0 2>&1
trap 'echo HMI_MEDIA_BUILDER_FAILED; poweroff -f' EXIT
. /lab/load-modules.sh
for i in 1 2 3 4 5; do
    [ -b /dev/vda ] && [ -b /dev/vdb ] && break
    sleep 1
done
[ -b /dev/vda ] && [ -b /dev/vdb ]
mkdir -p /populate
mount -t ext4 -o rw /dev/vdb /populate
tar -xpf /dev/vda -C /populate
# OEM_LINK_OWNERS
test -x /populate/hmi/runHMI.sh
test -s /populate/hmi/hmi_version
sync
umount /populate
echo HMI_MEDIA_BUILDER_UNMOUNTED
trap - EXIT
poweroff -f
"""


def hash_stream(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def ext4_verity_capable(image):
    with image.open('rb') as f:
        f.seek(1024)
        superblock=f.read(1024)
    if len(superblock)!=1024 or struct.unpack_from('<H',superblock,0x38)[0]!=0xef53:
        raise ValueError('Derived HMI image lacks an ext4 superblock')
    return bool(struct.unpack_from('<I',superblock,0x64)[0]&EXT4_RO_COMPAT_VERITY)


def verify_archive(zip_path, manifest):
    size, block, want = (manifest[k] for k in ('FileSize','CheckSumSize','CheckSum'))
    if (zip_path.name!=manifest['Source'] or manifest['MountPoint']!='/vw'
            or zip_path.stat().st_size!=size or not 1<=block<=1<<22
            or len(want)!=(size+block-1)//block):
        raise ValueError('OEM HMI installer source or geometry mismatch')
    h=hashlib.sha256()
    with zip_path.open('rb') as f:
        for i,digest in enumerate(want):
            data=f.read(block)
            if hashlib.sha256(data).hexdigest()!=digest.lower():
                raise ValueError(f'OEM HMI ZIP installer block {i} mismatch')
            h.update(data)
    return h.hexdigest()


def zip_metadata(entry):
    extra=entry.extra
    fields={}
    while extra:
        if len(extra)<4:
            raise ValueError('Truncated ZIP extra')
        code,n=struct.unpack_from('<HH',extra)
        if n+4>len(extra):
            raise ValueError('Truncated ZIP extra value')
        fields[code]=extra[4:4+n]
        extra=extra[n+4:]
    owner=fields.get(0x7875)
    if not owner or owner[0]!=1:
        raise ValueError(f'OEM Unix UID/GID absent: {entry.filename}')
    ids=[]
    left=owner[1:]
    for _ in range(2):
        if not left or not 1<=left[0]<=4 or len(left)<=left[0]:
            raise ValueError(f'Invalid ZIP UID/GID: {entry.filename}')
        n=left[0]
        ids.append(int.from_bytes(left[1:1+n],'little'))
        left=left[1+n:]
    if left:
        raise ValueError(f'Trailing UID/GID bytes: {entry.filename}')
    ts=fields.get(0x5455,b'')
    if len(ts)<5 or not ts[0]&1:
        raise ValueError(f'OEM mtime absent: {entry.filename}')
    return *ids,struct.unpack_from('<I',ts,1)[0]


class HashingReader:
    def __init__(self, stream):
        self.stream=stream
        self.digest=hashlib.sha256()
    def read(self, count):
        data=self.stream.read(count)
        self.digest.update(data)
        return data


def build_tar(zip_path, tar_path):
    records=[]
    with ZipFile(zip_path) as z, tarfile.open(tar_path,'x',format=tarfile.PAX_FORMAT) as tar:
        info={}
        for entry in z.infolist():
            name=entry.filename.rstrip('/')
            path=PurePosixPath(name)
            mode=entry.external_attr>>16
            kind=stat.S_IFMT(mode)
            if (entry.create_system!=3 or path.as_posix()!=name or path.is_absolute()
                    or not path.parts or '..' in path.parts or '\\' in name or ':' in name
                    or name in info or kind not in (stat.S_IFREG,stat.S_IFDIR,stat.S_IFLNK)):
                raise ValueError(f'Unsafe or unsupported OEM ZIP member: {name!r}')
            info[name]=(entry,mode,kind)
        for name,(entry,mode,kind) in info.items():
            parents=PurePosixPath(name).parents
            if any(p.as_posix() in info and info[p.as_posix()][2]!=stat.S_IFDIR for p in parents):
                raise ValueError(f'Member traverses a non-directory: {name}')
            t=tarfile.TarInfo(name)
            t.mode=stat.S_IMODE(mode)
            t.uid,t.gid,t.mtime=zip_metadata(entry)
            row=dict(name=name,mode=mode,uid=t.uid,gid=t.gid,mtime=t.mtime,size=entry.file_size)
            if kind==stat.S_IFDIR:
                t.type=tarfile.DIRTYPE
                tar.addfile(t)
            elif kind==stat.S_IFLNK:
                if not 1<=entry.file_size<=4096:
                    raise ValueError(f'Unexpected symlink length: {name}')
                t.type=tarfile.SYMTYPE
                t.linkname=z.read(entry).decode('utf-8')
                if '\x00' in t.linkname:
                    raise ValueError(f'Invalid link target: {name}')
                row['link']=t.linkname
                tar.addfile(t)
            else:
                t.size=entry.file_size
                with z.open(entry) as source:
                    reader=HashingReader(source)
                    tar.addfile(t,reader)
                    row['sha256']=reader.digest.hexdigest()
            records.append(row)
    return records


def entry(name,data,mode,ino):
    name=name.encode()+bytes(1)
    fields=(ino,mode,0,0,1,0,len(data),0,0,0,0,len(name),0)
    start=b'070701'+b''.join(f'{v:08x}'.encode() for v in fields)+name
    return start+bytes(-len(start)%4)+data+bytes(-len(data)%4)


def verify_ext4(path,records):
    with path.open('rb') as f:
        fs=ExtFS(f)
        for row in records:
            inode=fs.get(row['name'])
            v=inode.inode
            uid=v.i_uid | v.i_uid_high<<16
            gid=v.i_gid | v.i_gid_high<<16
            if (v.i_mode,uid,gid)!=(row['mode'],row['uid'],row['gid']):
                raise ValueError(f"OEM mode/owner mismatch: {row['name']}")
            if 'link' in row and inode.link!=row['link']:
                raise ValueError(f"OEM symlink mismatch: {row['name']}")
            if 'sha256' in row:
                with inode.open() as data:
                    if inode.size!=row['size'] or hash_stream(data)!=row['sha256']:
                        raise ValueError(f"OEM payload mismatch: {row['name']}")


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--resume-link-owners',action='store_true',
                    help='Finish owner restoration on a previously cleanly unmounted, unverified output')
    args=ap.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'var') or out==ROOT/'var':
        raise ValueError('Use a new directory within project scratch')
    if not args.resume_link_owners:
        out.mkdir(exist_ok=False,parents=True)
    archive=SOURCE/'ICAS_CN.zip'
    installer=json.loads((SOURCE/'ICAS_CN_application1_installer.txt').read_bytes())
    archive_hash=verify_archive(archive,installer)
    tar=out/'oem-tree.tar'
    image=out/'vw-hmi.ext4'
    resume=args.resume_link_owners
    if resume:
        for file in (out/'builder-serial.log', out/'zip-content.json', image):
            if file.resolve()!=file or not file.is_file():
                raise ValueError('Prepared builder files must be regular files in the selected output')
        if ((out/'result.json').exists() or image.stat().st_size!=HMI_BYTES
                or b'HMI_MEDIA_BUILDER_UNMOUNTED' not in (out/'builder-serial.log').read_bytes()):
            raise ValueError('Resume needs an unverified, cleanly unmounted builder image')
        records=json.loads((out/'zip-content.json').read_bytes())
                                                                                               
        with ZipFile(archive) as z:
            expected={i.filename.rstrip('/'):i for i in z.infolist()}
            if len(expected)!=len(records) or set(expected)!={r['name'] for r in records}:
                raise ValueError('Resume manifest differs from OEM ZIP directory')
            for row in records:
                original=expected[row['name']]
                if (row['mode']!=original.external_attr>>16
                        or (row['uid'],row['gid'],row['mtime'])!=zip_metadata(original)
                        or ('link' in row)!=(stat.S_IFMT(row['mode'])==stat.S_IFLNK)):
                    raise ValueError('Resume owner/type differs from OEM ZIP')
    else:
        records=build_tar(archive,tar)
        (out/'zip-content.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        with image.open('x+b') as f:
            _mark_sparse(f)
            f.truncate(HMI_BYTES)
        mkfs=ROOT/'.local/deps/bin/mke2fs.exe'
        with (out/'mke2fs.log').open('wb') as log:
            subprocess.run([str(mkfs),'-F','-t','ext4','-b',str(BLOCK),'-O','verity,^orphan_file,^metadata_csum_seed',
                            '-L','vw',str(image),str(HMI_BYTES//BLOCK)],stdout=log,stderr=log,check=True,timeout=60)
        if not ext4_verity_capable(image):
            raise ValueError('New /vw filesystem does not support original fs-verity provisioning')
    boot=ROOT/'vendor/ivi-lab/boot'
    tag='builder-owner' if resume else 'builder'
    initrd=out/(tag+'-initrd.gz')
    original=(boot/'ivi-initrd.gz').read_bytes()
    links='\n'.join(f"chown -h {r['uid']}:{r['gid']} {shlex.quote('/populate/'+r['name'])}"
                    for r in records if 'link' in r)
    init=INIT.replace(b'# OEM_LINK_OWNERS',links.encode())
    if resume:
                                                                                    
        init=init.replace(b'tar -xpf /dev/vda -C /populate',b':')
    extra=gzip.compress(entry('init-hmi-media',init,0o100755,7000000)+entry('TRAILER!!!',b'',0,0),mtime=0)
    initrd.write_bytes(original+extra)
    exe=ROOT/'runtime/qemu-system-aarch64.exe'
    argv=[str(exe),'-machine','virt','-cpu','cortex-a72','-accel','tcg,thread=multi',
          '-smp','2','-m','512','-kernel',str(boot/'linux'),'-initrd',str(initrd),
          '-append','console=ttyAMA0,115200 rdinit=/init-hmi-media panic=-1',
          '-nic','none','-display','none','-serial','file:'+str(out/(tag+'-serial.log')),
          '-monitor','none','-no-reboot']
    for name,path,readonly in (('src',tar,True),('dest',image,False)):
        escaped=str(path).replace(',',',,')
        argv+=['-drive',f'file={escaped},if=none,id={name},format=raw,readonly={"on" if readonly else "off"}',
               '-device',f'virtio-blk-pci,drive={name}']
    (out/(tag+'-launch.json')).write_text(json.dumps(argv,indent=2)+'\n',encoding='utf-8')
    with (out/(tag+'-host.log')).open('wb') as log:
        subprocess.run(argv,stdout=log,stderr=log,check=True,timeout=300)
    if b'HMI_MEDIA_BUILDER_UNMOUNTED' not in (out/(tag+'-serial.log')).read_bytes():
        raise RuntimeError('Builder did not confirm complete extraction and clean unmount')
    verify_ext4(image,records)
    with image.open('rb') as f:
        ext4_hash=hash_stream(f)
    result=dict(zip_sha256=archive_hash,archive_blocks_verified=len(installer['CheckSum']),
                ext4=str(image),ext4_sha256=ext4_hash,bytes=HMI_BYTES,entries=len(records),
                all_zip_bytes_and_unix_modes_owners_links_verified=True,
                path='/vw',gpt_slot=23,original_factory_partition_dump=False,
                smack_xattrs_fabricated=False,guest_hmi_executed=False,
                ext4_verity_capable=ext4_verity_capable(image),file_verity_enabled_by_builder=False)
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    main()
