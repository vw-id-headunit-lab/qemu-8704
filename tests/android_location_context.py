from pathlib import Path
import importlib.util,sys,json,hashlib,base64,io,tempfile
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'));spec=importlib.util.spec_from_file_location('location_capture',r/'tools/native_android_frame.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 assert m.LOCATION_INTENT=='android.intent.action.MANAGE_PERMISSION_APPS'and m.LOCATION_GROUP=='android.permission-group.LOCATION'
 assert m.LOCATION_READINESS_COMMAND=="dumpsys activity -a processes | grep -q 'mSystemReady=true'"
 assert m.LOCATION_CAPTURE_COMMAND.startswith(m.LOCATION_READINESS_COMMAND+' && cmd -w window size && cmd -w activity start-activity -W -a '+m.LOCATION_INTENT)
 assert 'android.intent.extra.PERMISSION_NAME '+m.LOCATION_GROUP in m.LOCATION_CAPTURE_COMMAND and '&& '+m.COMMAND in m.LOCATION_CAPTURE_COMMAND
 from PIL import Image
 buf=io.BytesIO();Image.new('RGB',(20,10),(100,120,140)).save(buf,format='PNG');png=buf.getvalue();sha=hashlib.sha256(png).hexdigest()
 with tempfile.TemporaryDirectory()as temp:
  p=Path(temp)
  def result(prefix):
   out=prefix+sha+'  /data/local/tmp/meb-nav-android.png\n'+base64.b64encode(png).decode()+'\n';raw=('MEBB_0123456789abcdef\n'+out+'\nMEBE_0123456789abcdef:0\n').encode();(p/'exynos-uart.bin').write_bytes(raw)
   return dict(guest='android',uid=2000,session=str(p),qemu_pid=42,command=m.LOCATION_CAPTURE_COMMAND,exit=0,transport='original-native-vlm-vm4',kernel='5.4.131-android11-2-ab151',capture=str(p/'exynos-uart.bin'),raw_start=0,raw_end=len(raw),raw_sha256=hashlib.sha256(raw).hexdigest(),stdout=out)
  for prefix in ['', 'Error: Activity not started\n','Status: ok\nActivity: com.android.car.settings/.Other\n']:
   try:m.validate(result(prefix),p,42)
   except ValueError:pass
   else:raise AssertionError('Unconfirmed or wrong application accepted')
  data,proof=m.validate(result('Starting: Intent { act=android.intent.action.MANAGE_PERMISSION_APPS (has extras) }\nStatus: ok\nActivity: com.android.permissioncontroller/.permission.ui.ManagePermissionsActivity\n'),p,42);assert data==png and proof['vmid']==4
 print('ORIGINAL_ANDROID_LOCATION_CONTEXT_PASS original_intent_and_group=1 wait_real_activity_registration=1 require_original_PermissionController_launch=1 capture_same_native_command=1 no_permission_or_ready_writes=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
