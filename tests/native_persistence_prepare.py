from pathlib import Path
import ast,configparser,json,subprocess,sys,tempfile
root=Path(__file__).resolve().parents[1];source=Path(sys.argv[1])if len(sys.argv)>1 else root/'src/emu/offline_bench_hmi_darwin.py';result=dict(scope='Execute original staged unit and log generation with inert captured-input fixture; actual OEM database readiness checked separately')
try:
 tree=ast.parse(source.read_bytes());fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='stage');env=dict(STAGE='/sys/firmware/devicetree/base/chosen/oem-stage',BENCH='/sys/firmware/devicetree/base/chosen/oem-bench',rsi_queue_stage=lambda n:'',input_ready_stage=lambda:'',panel_stage=lambda:'',power_stage=lambda n:'',native_touch_stage=lambda n:'',bench_script=lambda:"r=/run/oem-bench-resolution\nmkdir -p \"$r\"\nprintf 'CAPTURED_INPUT_FIXTURE\\n'\n");exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),env)
 nodes={'/chosen':{}};env['stage'](nodes,'exec /sbin/init');code=nodes['/chosen']['oem-stage'].decode()
 with tempfile.TemporaryDirectory(prefix='original-persistence-stage-')as d:
  d=Path(d);units=d/'units';units.mkdir();run=code.replace('/run/systemd/system',str(units)).replace('exec /sbin/init',"printf 'ORIGINAL_INIT_HANDOFF\\n'");p=subprocess.run(['sh','-c',run],capture_output=True,text=True);assert p.returncode==0,(p.returncode,p.stderr)
  def read(path):
   c=configparser.ConfigParser(strict=False);c.optionxform=str;c.read_string(path.read_text());return c
  prepare=read(units/'oem-bench-persistence-input.service');app=read(units/'persistence-app.service.d/10-bench.conf')
  assert prepare['Service']['Type']=='oneshot'and prepare['Service']['TimeoutStartSec']=='600'and prepare['Service']['RemainAfterExit']=='yes'
  assert set(prepare['Unit']['Requires'].split())==set(prepare['Unit']['After'].split())=={'data.mount','post-init.service'}
  assert prepare['Unit']['Before']=='persistence-app.service'and prepare['Service']['ExecStart']=='/bin/sh '+env['BENCH']
  assert app['Unit']['Requires']==app['Unit']['After']=='oem-bench-persistence-input.service'
  assert app['Service']['ExecStartPre']==''and app['Service']['TimeoutStartSec']=='60'
  assert not set(app['Service']).intersection({'ExecStart','User','Type','FailureAction','WatchdogSec','TimeoutStopSec','Restart'})
  timing=ast.parse((root/'src/runner/native-persistence-priority.py').read_text());budget=next(ast.literal_eval(x.value)for x in timing.body if isinstance(x,ast.Assign)and any(isinstance(n,ast.Name)and n.id=='PERSISTENCE_START_GRACE_SECONDS'for n in x.targets));assert int(app['Service']['TimeoutStartSec'])==budget
  body=nodes['/chosen']['oem-bench'].decode().replace('/run/oem-bench-persistence-input',str(d/'logs')).replace('/run/oem-bench-resolution',str(d/'resolution'));script=d/'input.sh';script.write_text(body);check=subprocess.run(['sh','-n',str(script)],capture_output=True,text=True);assert check.returncode==0,check.stderr
  p=subprocess.run(['sh',str(script)],capture_output=True,text=True);assert p.returncode==0,p.stderr
  stdout=(d/'logs/stdout').read_text();stderr=(d/'logs/stderr').read_text();exitstatus=(d/'logs/exit').read_text();assert stdout=='CAPTURED_INPUT_FIXTURE\nCAPTURED_PERSISTENCE_INPUT_PREPARED\n'and not stderr and exitstatus=='0\n'
  result.update(exit=0,preparation_stdout=stdout,preparation_stderr=stderr,preparation_exit=exitstatus,prepare_unit=(units/'oem-bench-persistence-input.service').read_text(),original_app_dropin=(units/'persistence-app.service.d/10-bench.conf').read_text())
except Exception as error:result.update(exit=2,error=repr(error))
print(json.dumps(result,indent=2));raise SystemExit(result['exit'])
