



import argparse,hashlib,io,json,stat,sys
from pathlib import Path
import original_8704_vm as vm
import ufs_android_media

class RawView(io.RawIOBase):
    def __init__(self,stream,offset,length):self.stream,self.offset,self.size,self.pos=stream,offset,length,0
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.pos
    def seek(self,off,whence=0):
        pos=off if whence==0 else self.pos+off if whence==1 else self.size+off
        if not 0<=pos<=self.size:raise ValueError('Raw view seek outside extent')
        self.pos=pos;return pos
    def read(self,size=-1):
        size=self.size-self.pos if size<0 else min(size,self.size-self.pos)
        if size>64*1024**2:raise ValueError('Read exceeds bounded analysis limit')
        self.stream.seek(self.offset+self.pos);b=self.stream.read(size)
        if len(b)!=size:raise EOFError('Short raw view read')
        self.pos+=size;return b

def inspect(output):
    from dissect.extfs import ExtFS
    sys.path.insert(0,str(vm.ROOT/'src/firmware'))
    from android_sparse import PartitionView,partitions
    base=vm.ROOT/'var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw'
    manifest=base.with_suffix('.json');layout=json.loads(manifest.read_bytes())
    if layout['profile']!='virtual-8704-archive-lun0-android-dsp-v1' or layout['sector_bytes']!=4096:raise ValueError('Unexpected original media profile')
    row=next(p for p in layout['partitions']if p['slot']==48)
    if row['payload']['sha256']!=ufs_android_media.SUPER_RAW_SHA256:raise ValueError('Unbound original super')
    before=base.stat();start=row['first']*4096;length=row['payload']['bytes']
    wanted={
      'system_a':['/system/lib64/libsoftkeymasterdevice.so','/system/lib64/libpuresoftkeymasterdevice.so','/system/lib64/libkeymaster_portable.so','/system/lib64/libkeymaster4support.so','/system/lib64/libkeymaster4_1support.so','/system/etc/init/keystore.rc','/system/etc/init/vold.rc','/system/etc/init/wait_for_keymaster.rc','/system/bin/keystore'],
      'vendor_a':['/bin/hw/android.hardware.keymaster@4.0_tee-service','/etc/init/android.hardware.keymaster@4.0_tee-service.rc','/etc/fstab.exynosauto9','/etc/vintf/manifest.xml','/etc/selinux/vendor_file_contexts','/etc/selinux/vendor_sepolicy.cil']}
    wanted['system_a'].append('/system/etc/init/hw/init.rc')
    wanted['system_a'].append('/system/etc/init/apexd.rc')
    wanted['system_a']+=['/odm/'+n for n in ('app','bin','etc','firmware','framework','lib','lib64','overlay','priv-app','usr')]
    wanted['system_a'].append('/odm')
    wanted['vendor_a']+=['/odm/etc/'+n for n in ('NOTICE.xml.gz','build.prop','group','passwd')]
    wanted['vendor_a'].append('/odm/etc')
    wanted['vendor_a'].append('/etc/init/init.exynosauto9.rc')
    report={'base':str(base),'manifest_sha256':vm.sha(manifest),'base_open_mode':'rb','whole_super_rehashed_this_run':False,'files':[],'service_candidates':[],'images_modified':False,'services_executed':False,'offline_boot_accepted':False}
    with base.open('rb')as f:
        report['lp_verified']=ufs_android_media.super_metadata(f,start,length)
        source=RawView(f,start,length);parts=partitions(source)
        inv=json.loads((vm.ROOT/'var/inputs/analysis/android-files.json').read_bytes())
        for part,names in wanted.items():
            fs=ExtFS(PartitionView(source,next(p for p in parts if p['name']==part)))
            report['service_candidates'] += [dict(partition=part,**e)for e in inv[part]['inventory']if any(t in e['path'].lower()for t in ('keymaster','keystore'))and('/bin/'in e['path']or'/lib64/hw/'in e['path'])]
            for name in names:
                try:node=fs.get(name)
                except FileNotFoundError:
                    report['files'].append(dict(partition=part,path=name,present=False));continue
                contexts=[x.value for x in node.xattr if x.name=='security.selinux']
                context=contexts[0].decode('ascii').rstrip('\0')if len(contexts)==1 else None
                if stat.S_ISDIR(node.filetype):
                    report['files'].append(dict(partition=part,path=name,filetype=node.filetype,mode=stat.S_IMODE(node.inode.i_mode),metadata_only=True,selinux_context=context));continue
                if not(stat.S_ISREG(node.filetype)or stat.S_ISLNK(node.filetype))or node.size>8*1024**2:raise ValueError('Unexpected selected file type/size')
                b=node.open().read();assert len(b)==node.size
                target=output/part/name.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if target.is_symlink() or target.read_bytes()!=b:raise ValueError('Existing selected copy differs')
                else:target.write_bytes(b)
                item=dict(partition=part,path=name,local_path=str(target),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),filetype=node.filetype,mode=stat.S_IMODE(node.inode.i_mode),selinux_context=context)
                if name.endswith(('.rc','.xml'))or'fstab'in name:item['text']=b.decode('utf-8')
                report['files'].append(item)
    after=base.stat();report['base_stat_unchanged']=(before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns)
    assert report['base_stat_unchanged'];return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--resume',action='store_true');a=p.parse_args();out=a.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var')or(out.exists()and not a.resume):raise ValueError('Require fresh task scratch directory or explicit checked resume')
    if a.resume and not(out/'result.json').is_file():raise ValueError('Require existing selected inventory')
    out.mkdir(parents=True,exist_ok=a.resume);z=inspect(out);vm.save(out/'result.json',z)
    print(json.dumps({'output':str(out),'selected_files':len(z['files']),'service_candidates':z['service_candidates'],'images_modified':False,'offline_boot_accepted':False}))
if __name__=='__main__':main()
