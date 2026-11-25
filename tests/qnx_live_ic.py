from pathlib import Path
import sys,json,hashlib,importlib.util,io
from PIL import Image
sys.dont_write_bytecode=True
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];root=binary.parents[2];sys.path.insert(0,str(root/'tools'));p=root/'tools/hvf_qnx.py';record={'label':label,'input':'Same current original QNX IC producer through canonical frame_info(ic); current owner/epoch/host hash/freshness required, actual PNG pixels','source_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
try:
 spec=importlib.util.spec_from_file_location('qnx_live_'+label,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);current=json.loads((r/'var/hvf/qnx-current.json').read_text());native=json.loads((Path(current['session'])/'native-runtime-state.json').read_text());info=m.frame_info('ic',owned=(Path(current['session']),native['qemu_pid']));data=Path(info['path']).read_bytes();im=Image.open(io.BytesIO(data));assert info['frame_source']=='original_qnx_gles_forward' and info['swap_count']>15 and im.size==(800,400);assert hashlib.sha256(data).hexdigest()==info['sha256'];assert info['channel_id']>35;assert len(im.getcolors(800*400)or[])>500
 import hvf_qnx_gl_frame as forward
 bad=[]
 for meta in [dict(source='qnx_gl_fixture_test',pid=1,width=800,height=400,window_id=3,native_window=1,tid=7,swap_count=20),dict(source='original_qnx_gles_forward',pid=2,width=800,height=400,window_id=3,native_window=1,tid=7,swap_count=20)]:
  try:forward.validate_metadata(meta,{'qnx_pid':1},0)
  except ValueError:bad.append('rejected')
 assert len(bad)==2
 record.update(exit=0,info=info,colors=len(im.getcolors(800*400)or[]),negative_fixture_and_wrong_pid_rejected=bad)
except Exception as ex:record.update(exit=1,error=repr(ex))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
