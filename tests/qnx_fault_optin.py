from pathlib import Path
import hashlib,importlib.util,json,sys
r=Path.cwd();sys.path.insert(0,str(r/'src/emu'));root=Path(sys.argv[1]).resolve();label=sys.argv[2];p=root/'src/emu/qnx_gl_forward.py';s=importlib.util.spec_from_file_location('fault_candidate_'+label,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
record=dict(label=label,input='Same original8704 IFS, current private GL assets/config; default versus explicit native-fault-v1 only')
try:
 raw=(r/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn').read_bytes();assets={n:(r/'runtime/guest/qnx-gl'/n).read_bytes()for n in ['libmeb-qnx-gl-forward.so','libMEG.so','libMEBGL2.so']};config=b'12345\n'+b'0'*32+b'\n'
 default,dr=m.forward_image(raw,assets,config);active,ar=m.forward_image(raw,assets,config,fault_trace=True)
 assert default!=active
 for result,enabled in [(default,False),(active,True)]:
  rows,_,_=m.directory(result)
  for name in ['hmi/vwHMI/ICAS3-VW/HMIstart.sh','hmi/vwHMI/ICAS3-VW/HMIstartIID.sh']:
   row=next(x for x in rows if x['name']==name);import struct;off,n=struct.unpack_from('<II',result,row['pos']+24);body=result[m.IMAGEFS+off:m.IMAGEFS+off+n]
   assert (b'MEB_QNX_FAULT_TRACE=native-fault-v1 'in body)==enabled
   assert b'qon -T default_policy_t -u 818:847 -d -p 11 ./GtfStartup' in body
 assert all(not v['native_fault_trace']for v in dr['original_launch_receipts'])
 assert all(v['native_fault_trace']for v in ar['original_launch_receipts'])
 record.update(exit=0,default_trace=False,explicit_trace=True,original_ifs_sha256=hashlib.sha256(raw).hexdigest(),default_sha256=hashlib.sha256(default).hexdigest(),active_sha256=hashlib.sha256(active).hexdigest(),scope='Exact private startup composition only, not proof of runtime fault or GUI success')
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-fault-optin-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
