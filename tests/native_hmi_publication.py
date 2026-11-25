from pathlib import Path
import sys,json,importlib.util,hashlib,io
from PIL import Image
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r;sys.path[:0]=[str(r/'tools'),str(source/'src/emu'),str(r/'src/emu')];report=dict(scope='Current original native HMI task/mm/process and pinned graphics producer; real frame, not fixture',source=str(source))
reader=None
try:
 def load(name):
  p=source/'src/emu'/(name+'.py');spec=importlib.util.spec_from_file_location(name,p);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
 base=load('native_center_live_darwin');base.ROOT=r;base.BASE=r/'var/sessions';gl=load('gl_center_live_darwin');gl.ROOT=r
 from hvf_qnx import owner
 s,pid=owner();reader=gl.GLCenterReader(s);png,proof=reader.sample();im=Image.open(io.BytesIO(png));im.load();assert im.size==(1920,1080)and proof['qemu_pid']==pid and proof['guest_uid']==724 and proof['frame_source']=='original_hmi_authenticated_ANGLE'and proof['nonzero'];assert hashlib.sha256(png).hexdigest()==proof['png_sha256']
 import native_domain_state,hvf_qnx
 require=native_domain_state.require_running;owner_fn=hvf_qnx.owner
 def stopped(*args):raise RuntimeError('TEST_ONLY_VM3_NOT_RUN')
 try:
  native_domain_state.require_running=stopped
  try:reader.sample()
  except RuntimeError as error:assert str(error)=='TEST_ONLY_VM3_NOT_RUN';report['cached_frame_rejects_stopped_domain']=True
  else:raise AssertionError('Cached frame ignored domain guard')
 finally:native_domain_state.require_running=require
 try:
  hvf_qnx.owner=lambda:(s.parent/'TEST_ONLY_WRONG_EPOCH',pid)
  try:reader.sample()
  except ValueError as error:assert 'epoch differs'in str(error);report['cached_frame_rejects_wrong_epoch']=True
  else:raise AssertionError('Cached frame ignored epoch guard')
 finally:hvf_qnx.owner=owner_fn
 report.update(exit=0,session=str(s),qemu_pid=pid,proof=proof,provenance=reader.provenance)
except Exception as error:report.update(exit=2,error=repr(error))
finally:
 if reader:reader.close()
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
