from pathlib import Path
import base64,copy,hashlib,importlib.util,io,json,sys,tempfile
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'));p=r/'tools/native_android_frame.py';spec=importlib.util.spec_from_file_location('android_frame_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 from PIL import Image
 image=io.BytesIO();Image.new('RGB',(32,18),(20,40,60)).save(image,format='PNG');png=image.getvalue();sha=hashlib.sha256(png).hexdigest()
 with tempfile.TemporaryDirectory()as temp:
  s=Path(temp);stdout=sha+'  /data/local/tmp/meb-nav-android.png\n'+base64.b64encode(png).decode()+'\n';nonce='0123456789abcdef';raw=('MEBB_'+nonce+'\n'+stdout+'\nMEBE_'+nonce+':0\n').encode();(s/'exynos-uart.bin').write_bytes(raw)
  result=dict(guest='android',uid=2000,session=str(s),qemu_pid=42,command=m.COMMAND,exit=0,transport='original-native-vlm-vm4',kernel='5.4.131-android11-2-ab151',capture=str(s/'exynos-uart.bin'),raw_start=0,raw_end=len(raw),raw_sha256=hashlib.sha256(raw).hexdigest(),stdout=stdout)
  data,proof=m.validate(result,s,42);assert data==png and proof['vmid']==4 and proof['frame_source']==m.SOURCE
  cases=[dict(guest='linux'),dict(uid=724),dict(kernel='5.4.131'),dict(session=str(s/'old')),dict(qemu_pid=43),dict(capture=str(s/'native-hmi.png')),dict(command='cat linux-frame.png'),dict(stdout=stdout+'wrong'),dict(raw_sha256='0'*64),dict(exit=1)]
  for fields in cases:
   bad=copy.deepcopy(result);bad.update(fields)
   try:m.validate(bad,s,42)
   except (ValueError,KeyError):pass
   else:raise AssertionError('Cross-domain or changed capture accepted '+str(fields))
 source=(r/'tools/hvf_dashboard.py').read_text();assert 'render_session(\'android\')'not in source;assert 'native_android_sample'in source and 'X-Android-VMID'in source and 'clearAndroidFrame'in source
 assert 'Linux 中控 · 当前导航实例'in source and 'Linux 中控 · 原厂内核实例（同属 Linux）'not in source and 'Android 独立画面'in source
 print('ANDROID_FRAME_IDENTITY_PASS native_VM4_only=1 original_screencap_receipt=1 UART_hash_and_PNG_hash=1 reject_Linux_and_old_epoch=1 no_legacy_fallback=1 distinct_display_labels=1 clear_wrong_or_unavailable_image=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
