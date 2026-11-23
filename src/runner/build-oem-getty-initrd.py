
import gzip
import hashlib
import json
from pathlib import Path
import sys

from dissect.extfs import ExtFS

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
from vlm_platform import factory_file

ARCHIVE='56e70bbf1f7d06d6bc645c6ea9d93921ec1e6c0c63214f1a1c63b0ab5e0a97bf'
UNIT='d065ed7b4114df103c99040d7024028328011b9a33b48376d6c9ca7c87a22e3e'
FILES={
    'usr/bin/busybox':('bin/busybox','0359fe9d5e51c88cc4dd084767abd4ddb500e77612b724857c3777719500de59',0o100755),
    'usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1':('lib/ld-linux-aarch64.so.1','d81cbf4d0923aa3833a9dceb631cde32b8dadd0935e73ab16cc7f57e3f7cf45b',0o100755),
    'usr/lib/aarch64-linux-gnu/libc.so.6':('lib/aarch64-linux-gnu/libc.so.6','f5e19064c639ad150a3cd2d0f2b663534326f93e5f9048d4e0d50004fd6ddbc4',0o100644),
}

INIT=b'''#!/bin/busybox sh
bb=/bin/busybox
fail() {
    echo "OEM run overlay: failed at $1" >&2
    if [ -c /dev/kmsg ]; then echo "<3>OEM run overlay: failed at $1" >/dev/kmsg; fi
    exec "$bb" sleep 2147483647
}
"$bb" mount -t devtmpfs devtmpfs /dev || fail devtmpfs
i=0
while [ ! -b /dev/dm-0 ] && [ "$i" -lt 30 ]; do
    "$bb" sleep 1
    i=$((i + 1))
done
[ -b /dev/dm-0 ] || fail verity-device
"$bb" mount -t ext4 -o ro /dev/dm-0 /newroot || fail verity-root
[ -x /newroot/sbin/init ] || fail oem-init
"$bb" mount -t tmpfs -o mode=0755,nosuid,nodev,strictatime tmpfs /newroot/run || fail run
"$bb" mkdir -p /newroot/run/systemd/system || fail unit-dir
"$bb" cp /oem-getty /newroot/run/systemd/system/serial-getty@ttySAC0.service || fail unit
"$bb" chmod 0644 /newroot/run/systemd/system/serial-getty@ttySAC0.service || fail unit-mode
echo '<5>OEM run overlay: exact getty staged on /run; handing off verity root to original init' >/dev/kmsg
"$bb" mount --move /dev /newroot/dev || fail move-dev
exec "$bb" switch_root /newroot /sbin/init "$@"
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unpack(data):
    pos=0
    while data[pos:pos+6] in (b'070701',b'070702'):
        row=[int(data[i:i+8],16) for i in range(pos+6,pos+110,8)]
        size,namesize=row[6],row[11]
        if not namesize or data[pos+109+namesize]!=0:
            raise ValueError('Invalid source newc member name')
        name=data[pos+110:pos+109+namesize].decode('utf-8',errors='surrogateescape')
        begin=(pos+110+namesize+3)&~3
        if name=='TRAILER!!!':
            return
        if size>len(data)-begin:
            raise ValueError('Truncated source newc member')
        yield name,data[begin:begin+size]
        pos=(begin+size+3)&~3
    raise ValueError('Missing newc trailer')


def newc(entries):
    result=bytearray()
    for index,(name,mode,data,rmajor,rminor) in enumerate([*entries,('TRAILER!!!',0,b'',0,0)],1):
        name=name.encode('ascii')+b'\0'
        fields=(index,mode,0,0,2 if mode==0o40755 else 1,0,len(data),0,0,rmajor,rminor,len(name),0)
        result+=b'070701'+''.join(f'{n:08x}' for n in fields).encode('ascii')+name
        result+=b'\0'*(-len(result)%4)
        result+=data
        result+=b'\0'*(-len(result)%4)
    return gzip.compress(result,compresslevel=6,mtime=0)


def build(output):
    base=ROOT/'vendor/ivi-lab/boot/ivi-initrd.gz'
    archive=base.read_bytes()
    if sha(archive)!=ARCHIVE:
        raise ValueError('Unexpected local IVI lab archive')
    binaries={name:data for name,data in unpack(gzip.decompress(archive)) if name in FILES}
    if set(binaries)!=set(FILES):
        raise ValueError('Missing ARM64 BusyBox or runtime library')
    entries=[(name,0o40755,b'',0,0) for name in ('bin','lib','lib/aarch64-linux-gnu','dev','newroot')]
    entries.append(('dev/console',0o20600,b'',5,1))
    for name,(target,digest,mode) in FILES.items():
        data=binaries[name]
        if sha(data)!=digest or data[:6]!=b'\x7fELF\x02\x01' or data[18:20]!=b'\xb7\x00':
            raise ValueError('Unexpected ELF bytes: '+name)
        entries.append((target,mode,data,0,0))
    with factory_file('ivi/icas3cn-gp-image-euto-v9.ext4').open('rb') as image:
        with ExtFS(image).get('/lib/systemd/system/serial-getty@.service').open() as stream:
            unit=stream.read(4096)
    if sha(unit)!=UNIT:
        raise ValueError('Original login unit differs from the factory hash')
    entries.extend([('oem-getty',0o100644,unit,0,0),('init',0o100755,INIT,0,0)])
    data=newc(entries)
    if dict(unpack(gzip.decompress(data)))!={name:body for name,_,body,_,_ in entries}:
        raise ValueError('New initramfs archive did not round-trip')
    with output.open('xb') as f:
        f.write(data)
    print(json.dumps({'path':str(output.resolve()),'bytes':len(data),'sha256':sha(data),
          'oem_unit_sha256':UNIT,'early_init_only':True,'root':'/dev/dm-0 read-only',
          'handoff':'OEM /sbin/init (PID 1)','authentication_modified':False}))


if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: build-oem-getty-initrd.py OUTPUT.gz')
    build(Path(sys.argv[1]))
