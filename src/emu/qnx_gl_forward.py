import hashlib,json,os,struct,sys
from pathlib import Path

PROFILE='original-loader-v1'
IFS_SHA='35306f08c1b9def32271ddb5233522cfe6abe79949dbe5b7c069724c88f92c96'
IMAGEFS=266424
STARTUP=4020
SCRIPT='etc/early_init_qtd.sh'
LIBRARY='proc/boot/libmeb-qnx-gl-abi.so'

def digest(raw):return hashlib.sha256(raw).hexdigest()

def checksum(raw):
 if sys.byteorder!='little':raise ValueError('QNX checksum requires explicit little-endian host')
 return sum(memoryview(raw).cast('I'))&0xffffffff

def directory(raw):
 base=IMAGEFS;size,end,start=struct.unpack_from('<III',raw,base+8)
 if raw[base:base+7]!=b'imagefs'or not 88<=start<end<size or base+size>len(raw):raise ValueError('QNX imagefs bounds changed')
 pos=base+start;result=[]
 while pos<base+end:
  n,ext=struct.unpack_from('<HH',raw,pos)
  if not n:break
  if n<24 or n%4 or pos+n>base+end:raise ValueError('QNX directory record changed')
  ino,mode=struct.unpack_from('<II',raw,pos+4);kind=mode&0o170000
  off=32 if kind in [0o100000,0o020000,0o060000]else 28 if kind==0o120000 else 24
  name=raw[pos+off:pos+n].split(b'\0')[0].decode()
  result.append(dict(name=name,pos=pos,bytes=n,ino=ino,kind=kind))
  pos+=n
 if pos+4>base+end or raw[pos:pos+4]!=b'\0'*4:raise ValueError('QNX directory terminator changed')
 return result,base+start,pos

def private_image(raw,library):
 if digest(raw)!=IFS_SHA:raise ValueError('Original QNX IFS identity changed')
 entries,start,end=directory(raw);script=next(x for x in entries if x['name']==SCRIPT)
 off,length=struct.unpack_from('<II',raw,script['pos']+24);old=raw[IMAGEFS+off:IMAGEFS+off+length]
 if digest(old)!='002113efe9acb972198c7bd55072ace971cb0e3c11743693c5d2b6c491263f6b':raise ValueError('Original QNX early script changed')
 if library[:7]!=b'\x7fELF\x02\x01\x01'or struct.unpack_from('<H',library,18)[0]!=183:raise ValueError('Require AArch64 little-endian ELF shared library')
 if any(x['name']==LIBRARY for x in entries):raise ValueError('Loader probe path already exists')
 insert=b"\nLD_PRELOAD=/proc/boot/libmeb-qnx-gl-abi.so /proc/boot/ksh -c ':' >/tmp/meb-qnx-gl-abi.log 2>&1 &\n"
 first=old.index(b'\n')+1;updated=old[:first]+insert+old[first:]
 out=bytearray(raw)
 def append(data,align):
  out.extend(b'\0'*(-len(out)%align));p=len(out);out.extend(data);return p-IMAGEFS
 liboff=append(library,4096);scriptoff=append(updated,4)
 records=bytearray(raw[start:end]);rel=script['pos']-start;struct.pack_into('<II',records,rel+24,scriptoff,len(updated))
 name=LIBRARY.encode()+b'\0';size=(32+len(name)+3)&~3;ino=max(x['ino']&0x0fffffff for x in entries)+1
 records.extend(struct.pack('<HH7I',size,0,ino,0o100555,0,0,1737438697,liboff,len(library))+name+b'\0'*(size-32-len(name)));records.extend(b'\0'*4)
 diroff=append(records,4);hdr_end=len(out)-IMAGEFS;out.extend(b'\0'*4);extent=len(out)-IMAGEFS
 struct.pack_into('<III',out,IMAGEFS+8,extent,hdr_end,diroff)
 struct.pack_into('<III',out,STARTUP+28,len(out)-STARTUP,262404,len(out)-STARTUP)
 struct.pack_into('<I',out,STARTUP+44,extent)
 struct.pack_into('<I',out,IMAGEFS-4,0);struct.pack_into('<I',out,IMAGEFS-4,(-checksum(out[STARTUP:IMAGEFS]))&0xffffffff)
 struct.pack_into('<I',out,len(out)-4,(-checksum(out[IMAGEFS:]))&0xffffffff)
 assert not checksum(out[STARTUP:IMAGEFS])and not checksum(out[IMAGEFS:])
 check,_,_=directory(out)
 for row,new in zip(entries,check):
  assert row['name']==new['name']
  if row['name']==SCRIPT:continue
  assert out[new['pos']:new['pos']+new['bytes']]==raw[row['pos']:row['pos']+row['bytes']], ('IFS entry changed',row,new)
 assert out[IMAGEFS+liboff:IMAGEFS+liboff+len(library)]==library
 assert out[IMAGEFS+scriptoff:IMAGEFS+scriptoff+len(updated)]==updated
 return bytes(out),dict(original_ifs_sha256=digest(raw),private_ifs_sha256=digest(out),library_sha256=digest(library),library_bytes=len(library),library_offset=IMAGEFS+liboff,script_offset=IMAGEFS+scriptoff,script_bytes=len(updated),directory_offset=IMAGEFS+diroff,directory_entries=len(check),original_entries_preserved=len(entries),original_payload_addresses_preserved=True,startup_checksum=0,imagefs_checksum=0)

def stage_loader_probe(command,output,display_profile):
 if os.environ.get('MEB_QNX_GL_ABI_PROBE')!=PROFILE:raise ValueError('Require exact QNX loader probe profile')
 import offline_bench_hmi
 if display_profile!='iid-arhud'or not offline_bench_hmi.selected()or os.environ.get('OEM_8704_QNX_HUD_STARTUP'):raise ValueError('Require private original IID/ARHUD bench without another IFS edit')
 from original_8704_vm import ROOT
 from vlm_platform import factory_file
 original=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn');library=ROOT/'runtime/guest/libmeb-qnx-gl-abi.so'
 exact=f'loader,file={original},addr=0x91080000,force-raw=on'
 if command.count(exact)!=1:raise ValueError('Require exact original QNX IFS loader')
 output=Path(output).resolve(strict=True);p=output/'qnx-gl-loader-private.bin'
 if not output.is_relative_to(ROOT/'var')or p.exists()or p.is_symlink():raise ValueError('Require fresh private loader output')
 raw=original.read_bytes();updated,record=private_image(raw,library.read_bytes())
 with p.open('xb')as f:f.write(updated)
 assert digest(p.read_bytes())==record['private_ifs_sha256']and digest(original.read_bytes())==IFS_SHA
 command[command.index(exact)]=f'loader,file={p},addr=0x91080000,force-raw=on'
 record.update(profile=PROFILE,original_ifs=str(original),private_ifs=str(p),library=str(library),original_ifs_unchanged=True,scope='Actual original QNX dynamic loader/libc ABI only, not graphics success')
 (output/'qnx-gl-loader-private.json').write_text(json.dumps(record,indent=2)+'\n')
 return record

FORWARD_PROFILE='original-gtf-v1'

def guard_null_sampler(module):
 if digest(module)!='5f66c452010d86d4c72c170c3def9fe9177571dd10f7d195ad03ef5a7147b425':raise ValueError('Original GLES3 module identity changed')
 out=bytearray(module)
 for offset,before,after in [(114492, '60130054', '40130054'), (115108, '1f2003d5', 'b5f4ffb4')]:
  before=bytes.fromhex(before);after=bytes.fromhex(after)
  if out[offset:offset+4]!=before:raise ValueError('Original null-sampler branch changed')
  out[offset:offset+4]=after
 return bytes(out)

def forward_image(raw,assets,config,*,observe=False,fault_trace=False,control_only=False):
 if digest(raw)!=IFS_SHA:raise ValueError('Original QNX IFS identity changed')
 entries,start,end=directory(raw);out=bytearray(raw);records=bytearray(raw[start:end]);changed=[];appended=[]
 def append(data,align=4096):
  out.extend(b'\0'*(-len(out)%align));pos=len(out);out.extend(data);return pos-IMAGEFS
 blobs={name:(append(data),len(data))for name,data in assets.items()};blobs['meb-qnx-gl.conf']=(append(config,4),len(config))
 for row in entries:
  if row['kind']!=0o100000 or not row['name'].endswith(('GTF_6.11.0/bin/libGtfGuiOpenGLES20.so','GTF_6.11.0/bin/libGtfGuiOpenGLES3.so','GTF_6.11.0/bin/libGtfRuntime.so')):continue
  offset,n=struct.unpack_from('<II',raw,row['pos']+24);original=raw[IMAGEFS+offset:IMAGEFS+offset+n];candidate=original
  if row['name'].endswith('/libGtfGuiOpenGLES3.so'):candidate=guard_null_sampler(original)
  names=[(b'\0libGLESv2\0',b'\0libMEBGL2\0'),(b'\0libEGL\0',b'\0libMEG\0')]
  if row['name'].endswith('/libGtfRuntime.so'):
   if digest(original)!='1bfd372ce71a879ddfa0da185674aedc53edd16c5fe3acb3224299045409963e':raise ValueError('Original GTF Screen stream runtime identity changed')
   names=[(b'\0libGLESv2.so\0',b'\0libMEBGL2.so\0')]
  for before,after in names:
   if len(before)!=len(after)or candidate.count(before)!=1:raise ValueError('Original GTF dynamic module name differs')
   candidate=candidate.replace(before,after)
  out[IMAGEFS+offset:IMAGEFS+offset+n]=candidate;changed.append(dict(member=row['name'],original_sha256=digest(original),modified_sha256=digest(candidate),original_payload_offset=IMAGEFS+offset,working_payload_offset=IMAGEFS+offset,processed_elf_metadata_preserved=True,changes='Same-length private graphics module names, including original Screen stream glReadPixels backend; GLES3 null-sampler guard retained; original PT_LOAD addresses and processed ELF metadata retained'))
 if len(changed)!=3:raise ValueError('Expected both original GTF renderers and Screen stream runtime')
 from qnx_vci_startup import modified as prepare_vci
 vci=next(x for x in entries if x['name']=='middleware/MgrVCI')
 vci_off,vci_n=struct.unpack_from('<II',raw,vci['pos']+24);vci_original=raw[IMAGEFS+vci_off:IMAGEFS+vci_off+vci_n];vci_candidate=prepare_vci(vci_original)
 out[IMAGEFS+vci_off:IMAGEFS+vci_off+vci_n]=vci_candidate
 changed.append(dict(member=vci['name'],original_sha256=digest(vci_original),modified_sha256=digest(vci_candidate),original_payload_offset=IMAGEFS+vci_off,working_payload_offset=IMAGEFS+vci_off,processed_elf_metadata_preserved=True,initial_mgrtsk_wait_seconds=600,original_initial_wait_seconds=15,normal_running_watchdog_unchanged=True,real_monitor_start_still_arms_watchdog=True,changes='Finite initial MgrTsk startup wait only; original monitor start, nine-second ALIVE timeout and TX failure handling retained'))
 from qnx_hud_startup import MEMBER as hud_member,modified as initialize_hud
 hud=next(x for x in entries if x['name']==hud_member)
 hud_off,hud_n=struct.unpack_from('<II',raw,hud['pos']+24);hud_original=raw[IMAGEFS+hud_off:IMAGEFS+hud_off+hud_n];hud_candidate=initialize_hud(hud_original)
 out[IMAGEFS+hud_off:IMAGEFS+hud_off+hud_n]=hud_candidate
 changed.append(dict(member=hud_member,original_sha256=digest(hud_original),modified_sha256=digest(hud_candidate),original_payload_offset=IMAGEFS+hud_off,working_payload_offset=IMAGEFS+hud_off,processed_elf_metadata_preserved=True,changes='Compose existing declared virtual MCU ARHUD=1 initializer with private GTF backend; asynchronous original persistence callback unchanged'))
 observer=next(x for x in entries if x['name']==SCRIPT);observer_off,observer_n=struct.unpack_from('<II',raw,observer['pos']+24);observer_original=raw[IMAGEFS+observer_off:IMAGEFS+observer_off+observer_n]
 observer_line=b"\nMEB_QNX_OBSERVE=original-process-v1 LD_PRELOAD=/proc/boot/libmeb-qnx-gl-forward.so qon -d /proc/boot/ksh -c ':' >/tmp/meb-qnx-observer.log 2>&1\n"
 if control_only:observer_line=observer_line.replace(b"original-process-v1",b"original-control-v1")
 observer_first=observer_original.index(b'\n')+1;observer_candidate=observer_original[:observer_first]+observer_line+observer_original[observer_first:] if observe else observer_original;observer_at=append(observer_candidate,4);struct.pack_into('<II',records,observer['pos']-start+24,observer_at,len(observer_candidate))
 observer_receipt=dict(member=SCRIPT,original_sha256=digest(observer_original),modified_sha256=digest(observer_candidate),mode='original-control-v1'if control_only else'original-process-v1',enabled=observe,commands='native original pidin/slog2info/ls from independent QNX ksh process',graphics_source=False)

 port=int(config.splitlines()[0]);assert 1<=port<=65535
 pf=next(x for x in entries if x['name']=='etc/pf/pf_all.conf');pf_off,pf_n=struct.unpack_from('<II',raw,pf['pos']+24);pf_original=raw[IMAGEFS+pf_off:IMAGEFS+pf_off+pf_n];rule=('pass out quick on vlan0 inet6 proto 6 from fd53:7cb8:383:3::73 to fd53:7cb8:383:3::108 port '+str(port)+' keep state\n').encode();anchor=b'pass quick $log_pass on vlan0 inet6 proto 58 icmp6-type { 1, 2 }';assert pf_original.count(anchor)==1
 pf_candidate=pf_original.replace(anchor,rule+anchor);pf_at=append(pf_candidate,4);struct.pack_into('<II',records,pf['pos']-start+24,pf_at,len(pf_candidate));network_policy=dict(member=pf['name'],original_sha256=digest(pf_original),modified_sha256=digest(pf_candidate),exact_rule=rule.decode().strip(),all_original_rules_preserved=True)
 boot=next(x for x in entries if x['name']=='proc/boot/.script')
 boot_off,boot_n=struct.unpack_from('<II',raw,boot['pos']+24);boot_original=raw[IMAGEFS+boot_off:IMAGEFS+boot_off+boot_n]
 boot_old=b'/scripts/startup.sh\0';boot_new=b'/proc/boot/run-oem1\0'
 if len(boot_old)!=len(boot_new)or boot_original.count(boot_old)!=1:raise ValueError('Original OEM startup executable field changed')
 boot_candidate=boot_original.replace(boot_old,boot_new);boot_at=append(boot_candidate,4);struct.pack_into('<II',records,boot['pos']-start+24,boot_at,len(boot_candidate))
 startup_wrapper=b'#!/bin/sh\nexec /scripts/startup.sh "$@" >/tmp/meb-oem-startup-stdout.txt 2>&1\n'
 blobs['run-oem1']=(append(startup_wrapper,4),len(startup_wrapper))
 startup_log_receipt=dict(member=boot['name'],original_sha256=digest(boot_original),modified_sha256=digest(boot_candidate),wrapper='/proc/boot/run-oem1',log='/tmp/meb-oem-startup-stdout.txt',original_executable='/scripts/startup.sh',original_arguments_unchanged=True,original_command_flags_unchanged=True,original_watchdog_unchanged=True,stdout_stderr_retained=True)
 launch_records=[]
 launch_targets={'hmi/vwHMI/ICAS3-VW/HMIstart.sh','hmi/vwHMI/ICAS3-VW/HMIstartIID.sh'}
 launch_old=b'qon -T default_policy_t -u 818:847 -d -p 11 ./GtfStartup ${MODEL_DIR}/model.json ${MODEL_DIR}/platform.json'
 hud_file_gate=b'for meb_hud_file in model.json model.bin resources.gtf scenes.gtf; do while [ ! -e "/hmi/vwHMI/ICAS3-VW/HUD_MODEL/$meb_hud_file" ]; do qwaitfor "/hmi/vwHMI/ICAS3-VW/HUD_MODEL/$meb_hud_file" 1 || :; done; [ -r "/hmi/vwHMI/ICAS3-VW/HUD_MODEL/$meb_hud_file" ] || exit 13; done\n'
 launch_new=hud_file_gate+(b'MEB_QNX_FAULT_TRACE=native-fault-v1 'if fault_trace else b'')+launch_old+b' 2>/tmp/meb-gtf-stderr.txt'
 for launch in entries:
  if launch['name']not in launch_targets:continue
  launch_off,launch_n=struct.unpack_from('<II',raw,launch['pos']+24);launch_original=raw[IMAGEFS+launch_off:IMAGEFS+launch_off+launch_n]
  if launch_original.count(launch_old)!=1:raise ValueError('Exact original GTF launch changed')
  launch_candidate=launch_original.replace(launch_old,launch_new);launch_at=append(launch_candidate,4);struct.pack_into('<II',records,launch['pos']-start+24,launch_at,len(launch_candidate));launch_records.append(dict(member=launch['name'],original_sha256=digest(launch_original),modified_sha256=digest(launch_candidate),policy_uid_priority_unchanged=True,original_detach_preserved=True,original_arguments_unchanged=True,stderr_captured=True,native_fault_trace=fault_trace,original_hud_files_ready_before_launch=True))
 if len(launch_records)!=2:raise ValueError('Original IID launch sites missing')
 ino=max(x['ino']&0x0fffffff for x in entries)+1
 for name,(offset,n)in blobs.items():
  paths=['proc/boot/'+name]
  if name in ['libMEG.so','libMEBGL2.so']:paths+=['hmi/vwHMI/ICAS3-VW/GTF_6.11.0/bin/'+name]
  for path in paths:
   encoded=path.encode()+b'\0';size=(32+len(encoded)+3)&~3;records.extend(struct.pack('<HH7I',size,0,ino,0o100555 if name.endswith('.so')or name=='run-oem1'else 0o100444,0,0,1737438697,offset,n)+encoded+b'\0'*(size-32-len(encoded)));ino+=1;appended.append(path)
 records.extend(b'\0'*4);diroff=append(records,4);hdr_end=len(out)-IMAGEFS;out.extend(b'\0'*4);extent=len(out)-IMAGEFS
 struct.pack_into('<III',out,IMAGEFS+8,extent,hdr_end,diroff);struct.pack_into('<III',out,STARTUP+28,len(out)-STARTUP,262404,len(out)-STARTUP);struct.pack_into('<I',out,STARTUP+44,extent)
 struct.pack_into('<I',out,IMAGEFS-4,0);struct.pack_into('<I',out,IMAGEFS-4,(-checksum(out[STARTUP:IMAGEFS]))&0xffffffff);struct.pack_into('<I',out,len(out)-4,(-checksum(out[IMAGEFS:]))&0xffffffff)
 assert not checksum(out[STARTUP:IMAGEFS])and not checksum(out[IMAGEFS:]);check,_,_=directory(out)
 for old,new in zip(entries,check):
  assert old['name']==new['name']
  if old['name']in {c['member']for c in changed}|{pf['name'],SCRIPT,boot['name']}|launch_targets:continue
  assert out[new['pos']:new['pos']+new['bytes']]==raw[old['pos']:old['pos']+old['bytes']]
 return bytes(out),dict(original_ifs_sha256=digest(raw),private_ifs_sha256=digest(out),changed=changed,appended=appended,network_policy=network_policy,native_observer=observer_receipt,original_startup_log=startup_log_receipt,original_launch_receipts=launch_records,original_other_payload_bytes_unchanged=True,original_other_metadata_unchanged_except_declared_script_offsets=True,original_processed_elf_metadata_preserved=True,original_global_EGL_GLES_unchanged=True,directory_offset=IMAGEFS+diroff,startup_checksum=0,imagefs_checksum=0,assets={k:digest(v)for k,v in assets.items()})

def stage_forward(command,output,display_profile):
 if os.environ.get('MEB_QNX_GL_FORWARD')!=FORWARD_PROFILE:raise ValueError('Require exact QNX forward profile')
 import offline_bench_hmi,subprocess
 from original_8704_vm import ROOT
 from vlm_platform import factory_file
 if display_profile!='iid-arhud'or not offline_bench_hmi.selected()or os.environ.get('OEM_8704_QNX_HUD_STARTUP')or os.environ.get('MEB_QNX_GL_ABI_PROBE'):raise ValueError('Require one private original GTF forward profile')
 state=json.loads((ROOT/'var/hvf/qnx-gl-forward.json').read_text());p=subprocess.run(['ps','-p',str(state['pid']),'-o','command='],capture_output=True,text=True,check=True)
 if str(ROOT/'tools/hvf_qnx_gl_forward.py')not in p.stdout:raise ValueError('Forward owner changed')
 port=int(state['relay_port']);token=state['token']
 if not 1<=port<=65535 or len(token)!=32 or any(c not in '0123456789abcdef'for c in token):raise ValueError('Forward config differs')
 original=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn');exact=f'loader,file={original},addr=0x91080000,force-raw=on'
 if command.count(exact)!=1:raise ValueError('Require exact original QNX IFS loader')
 output=Path(output).resolve(strict=True);p=output/'qnx-gl-forward-private.bin'
 if not output.is_relative_to(ROOT/'var')or p.exists()or p.is_symlink():raise ValueError('Require fresh private forward output')
 assets={name:(ROOT/'runtime/guest/qnx-gl'/name).read_bytes()for name in ['libmeb-qnx-gl-forward.so','libMEG.so','libMEBGL2.so']}
 candidate,record=forward_image(original.read_bytes(),assets,(str(port)+'\n'+token+'\n').encode(),observe=os.environ.get('MEB_QNX_NATIVE_OBSERVER')in('original-process-v1','original-control-v1'),fault_trace=os.environ.get('MEB_QNX_FAULT_TRACE')=='native-fault-v1',control_only=os.environ.get('MEB_QNX_NATIVE_OBSERVER')=='original-control-v1')
 with p.open('xb')as f:f.write(candidate)
 assert digest(p.read_bytes())==record['private_ifs_sha256']and digest(original.read_bytes())==IFS_SHA
 command[command.index(exact)]=f'loader,file={p},addr=0x91080000,force-raw=on';record.update(profile=FORWARD_PROFILE,private_ifs=str(p),original_ifs=str(original),linux_qemu_pid=state['linux_qemu_pid'],relay_owner=state['pid'],relay_port=port,channel_floor=max(map(int,state['channels']),default=0),host_sha256=digest((ROOT/'runtime/darwin/gl-host').read_bytes()))
 (output/'qnx-gl-forward-private.json').write_text(json.dumps(record,indent=2)+'\n');return record
