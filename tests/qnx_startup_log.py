from pathlib import Path
import sys,importlib.util,json,struct,tempfile,subprocess,hashlib
ROOT=Path(__file__).resolve().parents[1];binary=Path(sys.argv[1]).resolve();root=binary.parents[2];label=sys.argv[2]
sys.path.insert(0,str(ROOT/'src/emu'))
p=root/'src/emu/qnx_gl_forward.py';spec=importlib.util.spec_from_file_location('selected_forward',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
record=dict(label=label,input='Original IFS boot command qtd ZERO ICAS3CN-GP; same stdout/stderr and exact original command flags, real shell redirection fixture',source_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
try:
 source=ROOT/'var/inputs/analysis/cpu/qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn';raw=source.read_bytes();assets={n:(ROOT/'runtime/guest/qnx-gl'/n).read_bytes()for n in ['libmeb-qnx-gl-forward.so','libMEG.so','libMEBGL2.so']};out,receipt=m.forward_image(raw,assets,b'30001\n0123456789abcdef0123456789abcdef\n');rows=m.directory(out)[0]
 def member(data,rows,name):
  x=next(x for x in rows if x['name']==name);o,n=struct.unpack_from('<II',data,x['pos']+24);return data[m.IMAGEFS+o:m.IMAGEFS+o+n]
 boot=member(out,rows,'proc/boot/.script');oldboot=member(raw,m.directory(raw)[0],'proc/boot/.script');wrapped=b'/proc/boot/run-oem1\0'in boot
 assert boot.replace(b'/proc/boot/run-oem1\0',b'/scripts/startup.sh\0')==oldboot
 assert b'startup.sh\0qtd\0ZERO\0ICAS3CN-GP\0'in boot
 record['wrapped']=wrapped;record['boot_bytecode_flags_arguments_unchanged']=True
 with tempfile.TemporaryDirectory(prefix='meb-startup-log-')as td:
  temp=Path(td);fixture=temp/'startup.sh';log=temp/'meb-oem-startup-stdout.txt';fixture.write_text('#!/bin/sh\nprintf "OEM_STARTUP_ARGS=%s,%s,%s\\n" "$1" "$2" "$3"\nprintf "OEM_STDERR_PRESERVED\\n" >&2\n');fixture.chmod(0o755)
  if wrapped:
   wrapper=member(out,rows,'proc/boot/run-oem1');x=next(x for x in rows if x['name']=='proc/boot/run-oem1');mode=struct.unpack_from('<I',out,x['pos']+8)[0];assert mode&0o111
   expected=b'#!/bin/sh\nexec /scripts/startup.sh "$@" >/tmp/meb-oem-startup-stdout.txt 2>&1\n';assert wrapper==expected
   selected=temp/'run-oem1';selected.write_text(wrapper.decode().replace('/scripts/startup.sh',str(fixture)).replace('/tmp/meb-oem-startup-stdout.txt',str(log)));cmd=['/bin/sh',str(selected),'qtd','ZERO','ICAS3CN-GP']
  else:cmd=['/bin/sh',str(fixture),'qtd','ZERO','ICAS3CN-GP']
  a=subprocess.run(cmd,capture_output=True,text=True);actual=log.read_text()if log.exists()else'';record.update(stdout=a.stdout,stderr=a.stderr,process_exit=a.returncode,captured_log=actual)
  assert a.returncode==0
  assert a.stdout=='' and a.stderr=='' and actual=='OEM_STARTUP_ARGS=qtd,ZERO,ICAS3CN-GP\nOEM_STDERR_PRESERVED\n','Native startup standard I/O still inherited serial console'
 record.update(exit=0,original_ifs_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==m.IFS_SHA,scope='Actual generated IFS command field and real shell I/O; live original manager queues/pixels required separately')
except Exception as e:record.update(exit=1,error=repr(e))
(ROOT/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
