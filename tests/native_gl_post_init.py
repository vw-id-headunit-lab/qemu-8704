from pathlib import Path
import ast,configparser,json,sys,tempfile
root=Path(__file__).resolve().parents[1];source=Path(sys.argv[1])if len(sys.argv)>1 else root/'src/emu/gl-forward/runtime-darwin.py';report={'scope':'Executed original staged HMI unit dependency check, not native startup acceptance','input':'HMI graphics provider must wait for real OEM oneshot post-init; retain notify readiness, 150s timeout, mount checks and native agent'}
try:
 tree=ast.parse(source.read_bytes());fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='hmi_payload');env={};exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
 with tempfile.TemporaryDirectory(prefix='original-unit-fixture-')as tmp:
  p=Path(tmp)/'asset';p.write_bytes(b'UNIT_FIXTURE_NOT_FIRMWARE');inputs={k:p for k in('agent','library','jni','font','java','java_rollback')};payload=env['hmi_payload'](inputs)
 unit=payload['unit'].decode();c=configparser.ConfigParser();c.optionxform=str;c.read_string(unit);report['literal_unit']=unit
 assert c['Unit']['DefaultDependencies']=='no' and 'post-init.service'in c['Unit'].get('Requires','').split() and 'post-init.service'in c['Unit'].get('After','').split()
 assert c['Service']['Type']=='notify' and c['Service']['TimeoutStartSec']=='150' and c['Service']['ExecStart']=='/bin/sh /run/oem-gl-forward/run' and 'chsmack'in c['Service']['ExecStartPost']
 assert b"until grep -q ' /vw ext4 rw[, ]'"in payload.get('prepare',payload['run']) and (b'exec "$d/agent"'in payload['run'] or b'exec /vw/oem-gl-forward/agent'in payload['run']);report['exit']=0
except Exception as e:report.update(error=repr(e),exit=2)
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(report['exit'])
