from pathlib import Path
import sys,json,gzip,subprocess,socket,time,os,signal
from contextlib import ExitStack
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src/hvf_bench/src'))
from hmi_bench.archive import cpio_entry
from hmi_bench.launcher import free_port
from media import LogicalNBDServer
HERE=Path(__file__).resolve().parent
def alpine_members(path):
 data=gzip.decompress(Path(path).read_bytes());out={};o=0
 while o+110<=len(data):
  if data[o:o+6]not in(b'070701',b'070702'):
   o=data.find(b'070701',o+1)
   if o<0:break
  f=[int(data[o+6+i*8:o+14+i*8],16)for i in range(13)];n=data[o+110:o+110+f[11]-1].decode();start=(o+110+f[11]+3)&~3
  if n in('usr/bin/busybox','usr/lib/ld-musl-aarch64.so.1'):out[n]=data[start:start+f[6]]
  o=(start+f[6]+3)&~3
 assert len(out)==2;return out
def main():
 provenance=json.loads((HERE/'runtime/modules/provenance.json').read_text())
 import hashlib
 assert len(provenance)==4
 for row in provenance:assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
 c=json.loads((ROOT/'config/hvf-8704.json').read_text());out=ROOT/'var/hvf'/time.strftime('android-%Y%m%dT%H%M%SZ',time.gmtime());out.mkdir(exist_ok=False);a=alpine_members(c['initramfs']);members={name:(b'',0o40755)for name in ['bin','sbin','lib','dev','proc','sys','modules']};members.update({'bin/busybox':(a['usr/bin/busybox'],0o100755),'lib/ld-musl-aarch64.so.1':(a['usr/lib/ld-musl-aarch64.so.1'],0o100755),'bin/sh':(b'busybox',0o120777),'init':((HERE/'guest/probe-init.sh').read_bytes(),0o100755)})
 for p in(HERE/'runtime/modules').glob('*.ko'):members['modules/'+p.name]=(p.read_bytes(),0o100644)
 for image in(HERE/'runtime/renderer').glob('*.so'):members['bench-'+image.name]=(image.read_bytes(),0o100755)
 members['bench-keystore-software']=((HERE/'build/keystore-software').read_bytes(),0o100755)
 members['bench-gralloc.bench.so']=((HERE/'build/gralloc.bench.so').read_bytes(),0o100755)
 members['bench-hwcomposer.bench.so']=((HERE/'build/hwcomposer.bench.so').read_bytes(),0o100755)
 members['framework-setup']=((HERE/'guest/framework-setup.sh').read_bytes(),0o100755);members['framework-control']=((HERE/'guest/control.sh').read_bytes(),0o100755)
 initrd=out/'initramfs.gz';initrd.write_bytes(gzip.compress(b''.join(cpio_entry(n,b,m,i+1)for i,(n,(b,m))in enumerate(members.items()))+cpio_entry('TRAILER!!!',b'',0,999),mtime=0));port=free_port();qmp=free_port();frame_port=free_port()
 with ExitStack()as stack:
  servers=[LogicalNBDServer(ROOT/'var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw',n)for n in ['system_a','vendor_a']]
  for server in servers:stack.callback(server.close)
  argv=[c['qemu'],'-machine','virt,gic-version=3','-cpu','host','-accel','hvf','-smp','4','-m','2048','-display','none','-serial','stdio','-monitor','none','-nic','none','-no-reboot','-kernel',str(HERE/'runtime/kernel/Image'),'-initrd',str(initrd),'-append','console=ttyAMA0 earlycon=pl011,0x9000000 rdinit=/init panic=-1 androidboot.hardware=bench androidboot.selinux=permissive androidboot.console=ttyAMA0 androidboot.slot_suffix=_a','-qmp',f'tcp:127.0.0.1:{qmp},server=on,wait=off','-device','virtio-serial-device','-chardev',f'socket,id=control,host=127.0.0.1,port={port},server=on,wait=off','-device','virtserialport,chardev=control,name=bench.control']
  data=ROOT/'var/hvf/android-data.ext4'
  if not data.exists():
   with data.open('xb')as file:file.truncate(4*1024**3)
  argv+=['-object','rng-random,id=rng0,filename=/dev/urandom','-device','virtio-rng-device,rng=rng0','-drive',f'file={data},format=raw,if=none,id=benchdata','-device','virtio-blk-device,drive=benchdata,serial=bench-data']
  misc=ROOT/'var/hvf/android-misc.raw'
  assert misc.stat().st_size==1048576
  argv+=['-drive',f'file={misc},format=raw,if=none,id=benchmisc','-device','virtio-blk-device,drive=benchmisc,serial=bench-misc']
  argv+=['-chardev',f'socket,id=display,host=127.0.0.1,port={frame_port},server=on,wait=off','-device','virtserialport,chardev=display,name=bench.display']
  for i,server in enumerate(servers):argv+=['-drive',f'file=nbd://127.0.0.1:{server.port},format=raw,if=none,id=d{i},readonly=on','-device',f'virtio-blk-device,drive=d{i}']
  with(out/'stdout.txt').open('xb')as stdout,(out/'stderr.txt').open('xb')as stderr:
   process=subprocess.Popen(argv,stdout=stdout,stderr=stderr)
   from frame import FrameSink
   frame_sink=FrameSink(frame_port,out);stack.callback(frame_sink.close)
   record=dict(state='running',worker_pid=os.getpid(),qemu_pid=process.pid,session=str(out),control_port=port,qmp_port=qmp,argv=argv,firmware='8704 Android11',framework_boot_completed=False,frame_port=frame_port,android_root='/',boot_mode='original-init-second-stage')
   (out/'session.json').write_text(json.dumps(record,indent=2)+'\n');(ROOT/'var/hvf/android-current.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2),flush=True)
   try:rc=process.wait()
   finally:
    if process.poll()is None:process.terminate();process.wait(timeout=10)
    record.update(state='ended',qemu_exit_status=process.returncode,nbd=[s.stats for s in servers]);(out/'session.json').write_text(json.dumps(record,indent=2)+'\n')
  return rc
if __name__=='__main__':raise SystemExit(main())
