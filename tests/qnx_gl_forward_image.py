from pathlib import Path
import sys,json,hashlib,struct,importlib.util,io
from elftools.elf.elffile import ELFFile
r=Path.cwd();target=Path(sys.argv[1]).resolve();label=sys.argv[2];record={'target':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'label':label,'input':'Original8704 IFS, same private EGL/GLES assets and fixed port41451/token; verify original processed GTF PT_LOAD physical code points to the modified backend names','checks':[]}
try:
 spec=importlib.util.spec_from_file_location('qnx_forward_subject',target);q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
 raw=(r/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn').read_bytes();assets={n:(r/'runtime/guest/qnx-gl'/n).read_bytes()for n in ['libmeb-qnx-gl-forward.so','libMEG.so','libMEBGL2.so']};out,meta=q.forward_image(raw,assets,b'41451\n0123456789abcdef0123456789abcdef\n');old,_,_=q.directory(raw);new,_,_=q.directory(out)
 for row in old:
  if not row['name'].endswith(('GTF_6.11.0/bin/libGtfGuiOpenGLES20.so','GTF_6.11.0/bin/libGtfGuiOpenGLES3.so')):continue
  at=next(x for x in new if x['name']==row['name']);off,n=struct.unpack_from('<II',raw,row['pos']+24);new_off,new_n=struct.unpack_from('<II',out,at['pos']+24);e=ELFFile(io.BytesIO(raw[q.IMAGEFS+off:q.IMAGEFS+off+n]));seg=next(s for s in e.iter_segments()if s['p_type']=='PT_LOAD'and s['p_offset']==0);physical=seg['p_paddr'];assert physical==0x91080000+q.IMAGEFS+off
  mapped=out[physical-0x91080000:physical-0x91080000+seg['p_filesz']];checks={'member':row['name'],'original_inode':hex(row['ino']),'candidate_inode':hex(at['ino']),'original_physical_load':hex(physical),'original_payload_offset':q.IMAGEFS+off,'candidate_payload_offset':q.IMAGEFS+new_off,'modified_backend_at_original_physical_load':b'\0libMEG\0'in mapped and b'\0libMEBGL2\0'in mapped,'metadata_preserved':out[at['pos']:at['pos']+at['bytes']]==raw[row['pos']:row['pos']+row['bytes']]};record['checks'].append(checks)
  assert checks['modified_backend_at_original_physical_load'],'original processed PT_LOAD still maps unmodified backend names'
  assert checks['metadata_preserved']and off==new_off and n==new_n,'original processed ELF metadata/address moved'
 assert len(record['checks'])==2
 pf_old=next(x for x in old if x['name']=='etc/pf/pf_all.conf');pf_new=next(x for x in new if x['name']==pf_old['name']);off,n=struct.unpack_from('<II',raw,pf_old['pos']+24);at,length=struct.unpack_from('<II',out,pf_new['pos']+24);original_pf=raw[q.IMAGEFS+off:q.IMAGEFS+off+n];new_pf=out[q.IMAGEFS+at:q.IMAGEFS+at+length];rule=b'pass out quick on vlan0 inet6 proto 6 from fd53:7cb8:383:3::73 to fd53:7cb8:383:3::108 port 41451 keep state\n';record['network_policy']={'exact_rule_present':new_pf.count(rule)==1,'all_original_rules_preserved':new_pf.replace(rule,b'')==original_pf};assert all(record['network_policy'].values()),'isolated graphics TCP destination is blocked by original default-drop PF policy'
 assert hashlib.sha256((r/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn').read_bytes()).hexdigest()==q.IFS_SHA;record.update(exit=0,original_ifs_unchanged=True,startup_checksum=meta['startup_checksum'],imagefs_checksum=meta['imagefs_checksum'])
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-forward-xip-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
