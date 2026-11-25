from pathlib import Path
import ast,configparser,json,sys,tempfile,subprocess
root=Path(__file__).resolve().parents[1];source=Path(sys.argv[1])if len(sys.argv)>1 else root/'src/emu/gl-forward/runtime-darwin.py';out=dict(scope='Execute staged payload generation and parse real units/shell; live original boot acceptance separate')
try:
 tree=ast.parse(source.read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='hmi_payload');env={};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
 with tempfile.TemporaryDirectory(prefix='original-gl-prepare-')as d:
  p=Path(d)/'asset';p.write_bytes(b'UNIT_FIXTURE_NOT_FIRMWARE');payload=env['hmi_payload']({k:p for k in('agent','library','jni','font','java','java_rollback')})
  def unit(name):
   c=configparser.ConfigParser(strict=False);c.optionxform=str;c.read_string(payload[name].decode());return c
  prepare=unit('prepare_unit');forward=unit('unit');hmi=unit('dropin')
  assert prepare['Service']['Type']=='oneshot'and prepare['Service']['TimeoutStartSec']=='600'and prepare['Service']['RemainAfterExit']=='yes'
  assert prepare['Unit']['Requires']==prepare['Unit']['After']=='post-init.service'and prepare['Unit']['Before']=='oem-gl-forward.service'
  assert 'oem-gl-prepare.service'in forward['Unit']['Requires'].split()and 'oem-gl-prepare.service'in forward['Unit']['After'].split()
  assert forward['Service']['Type']=='notify'and forward['Service']['TimeoutStartSec']=='150'and forward['Service']['NotifyAccess']=='all'
  assert 'chsmack'in forward['Service']['ExecStartPost']and hmi['Unit']['Requires']=='oem-gl-forward.service'
  assert b'chsmack -a hmi::sub -e hmi::sub'in payload['prepare']and b'grep -Fq' in payload['prepare']and b'PREPARATION_COMPLETE'in payload['prepare']
  assert b'exec /vw/oem-gl-forward/agent'in payload['run']and b'chsmack -a'not in payload['run']
  for name in ['prepare','run','entry']:
   p=Path(d)/name;p.write_bytes(payload[name]);s=subprocess.run(['sh','-n',str(p)],capture_output=True,text=True);assert s.returncode==0,(name,s.stderr)
 out.update(exit=0,prepare_unit=payload['prepare_unit'].decode(),graphics_unit=payload['unit'].decode())
except Exception as e:out.update(exit=2,error=repr(e))
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
