
import hashlib
import json
from pathlib import Path
import subprocess

from dissect.extfs import ExtFS
import linux_data_overlay as data_overlay
from linux_hmi_overlay import NBD, QEMU, UNIT, connect
import original_8704_vm as vm
from ufs_virtual_disk import _mark_sparse
from vlm_platform import factory_file

UNIT_DIR='systemd-native-units'
UNIT_NAME='serial-getty@ttySAC0.service'
OEM_PATH='/lib/systemd/system/serial-getty@.service'
OEM_SHA='d065ed7b4114df103c99040d7024028328011b9a33b48376d6c9ca7c87a22e3e'
UNIT_ENV='SYSTEMD_UNIT_PATH=/data/'+UNIT_DIR+':'
PORTABLE=Path(__file__).resolve().parent/'e2fsprogs-portable'


def edit_volume(volume, out, unit):

    manifest=json.loads((PORTABLE/'manifest.json').read_bytes())
    if manifest.get('signer')!='56405CF6FCC81574682A5D561A698DE9E2E56300':
        raise ValueError('Expected the staged signed e2fsprogs manifest')
    bin_dir=PORTABLE/'bin'
    for name,digest in manifest['binaries'].items():
        if Path(name).name!=name or vm.sha(bin_dir/name)!=digest:
            raise ValueError('Staged e2fsprogs binary differs from the verified package')
    fsck=str(bin_dir/'e2fsck.exe')
    debugfs=str(bin_dir/'debugfs.exe')
    (out/'native-getty-unit').write_bytes(unit)
    (out/'native-getty-debugfs.txt').write_text(
        f'mkdir /{UNIT_DIR}\ncd /{UNIT_DIR}\nwrite native-getty-unit {UNIT_NAME}\n'
        f'set_inode_field {UNIT_NAME} mode 0100644\n',encoding='ascii')
    with (out/'linux-getty-filesystem.log').open('xb') as log:
        for command,allowed in (([fsck,'-p',volume.name],(0,1)),
                                ([debugfs,'-w','-f','native-getty-debugfs.txt',volume.name],(0,)),
                                ([fsck,'-fn',volume.name],(0,))):
            result=subprocess.run(command,cwd=out,stdout=log,stderr=subprocess.STDOUT,timeout=60)
            if result.returncode not in allowed:
                raise RuntimeError(f'{Path(command[0]).name} exited {result.returncode}; inspect '+log.name)
    with volume.open('rb') as raw:
        fs=ExtFS(raw)
        with fs.get('/'+UNIT_DIR+'/'+UNIT_NAME).open() as unit_file:
            if unit_file.read()!=unit:
                raise ValueError('Derived /data does not contain the exact OEM unit')


def install(base, media, out, overlay):
    if not overlay.get('prior_session') or Path(overlay['path']).parent!=out:
        raise ValueError('Getty instance needs a fresh copy of an exited VM3 session')
    with factory_file('ivi/icas3cn-gp-image-euto-v9.ext4').open('rb') as raw:
        entry=ExtFS(raw).get(OEM_PATH)
        with entry.open() as stream:
            unit=stream.read(4096)
    if hashlib.sha256(unit).hexdigest()!=OEM_SHA or b'ExecStart=-/sbin/agetty ' not in unit:
        raise ValueError('Original getty unit contents changed')
    offset,length=data_overlay.geometry(Path(base).with_suffix('.json'))
    volume=out/'linux-getty-data.ext4'
    port=vm.free_port()
    target=Path(overlay['path'])
    expected=hashlib.sha256()
    actual=hashlib.sha256()
    changed=0
    with (out/'linux-getty-nbd.log').open('xb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
            '--format=qcow2','--persistent',str(target)],stdout=log,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock:
                nbd=NBD(sock)
                if nbd.size!=media['virtual_bytes'] or not nbd.flags&4:
                    raise ValueError('Getty overlay NBD geometry changed')
                with volume.open('x+b') as source:
                    _mark_sparse(source)
                    source.truncate(length)
                    for pos in range(0,length,UNIT):
                        block=nbd.command(0,offset+pos,min(UNIT,length-pos))
                        if block.count(0)!=len(block):
                            source.seek(pos)
                            source.write(block)
                edit_volume(volume,out,unit)
                with volume.open('rb') as source:
                    for pos in range(0,length,UNIT):
                        count=min(UNIT,length-pos)
                        content=source.read(count)
                        if len(content)!=count:
                            raise ValueError('Short derived /data read')
                        expected.update(content)
                        before=nbd.command(0,offset+pos,count)
                        if before==content:
                            continue
                        for at in range(0,count,4096):
                            if before[at:at+4096]!=content[at:at+4096]:
                                nbd.command(1,offset+pos+at,4096,content[at:at+4096])
                                changed+=4096
                nbd.command(3)
                for pos in range(0,length,UNIT):
                    actual.update(nbd.command(0,offset+pos,min(UNIT,length-pos)))
                nbd.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    if expected.digest()!=actual.digest():
        raise ValueError('Private /data getty image failed full block read-back')
    report=dict(original_unit=OEM_PATH,original_sha256=OEM_SHA,target_unit='/data/'+UNIT_DIR+'/'+UNIT_NAME,
                launch_env=UNIT_ENV,data_sha256=actual.hexdigest(),changed_bytes=changed,
                signed_root_unchanged=True,authentication_modified=False)
    vm.save(out/'linux-getty-material.json',report)
    return dict(overlay,original_getty_instance=report)
