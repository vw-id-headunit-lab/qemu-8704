from pathlib import Path
import io,json,subprocess,sys,tarfile,tempfile
r=Path(__file__).resolve().parents[1];p=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'src/hvf_bench/guest/hmi-init.sh';report={'source':str(p),'input':'Non-virgl cold startup: empty runtime tree, staged Mesa archive, original Mesa and navigation launch clauses'}
try:
 s=p.read_text();a=s.index('    if [ -f /mesa-runtime.tar.gz ] && [ ! -d /mnt/agl/tmp/mesa ]; then\n');b=s.index('    fi\n',a)+7;nav='    if [ -f /navigation-setup ]; then $BB sh /navigation-setup; fi\n';n=s.index(nav);parts=sorted([(a,s[a:b]),(n,nav)])
 with tempfile.TemporaryDirectory(prefix='navigation-mesa-')as tmp:
  d=Path(tmp);(d/'root/tmp').mkdir(parents=True);archive=d/'mesa-runtime.tar.gz'
  with tarfile.open(archive,'w:gz')as t:
   data=b'{}';info=tarfile.TarInfo('usr/share/glvnd/egl_vendor.d/50_mesa.json');info.size=len(data);t.addfile(info,io.BytesIO(data))
  setup=d/'navigation-setup';setup.write_text('if [ -f "'+str(d/'root/tmp/mesa/usr/share/glvnd/egl_vendor.d/50_mesa.json')+'" ]; then echo NAVI_GLES=mesa; else echo NAVI_GLES=vendor; fi\n')
  script='BB=bb\nbb() { "$@"; }\n'+''.join(x[1]for x in parts)
  script=script.replace('/mnt/agl',str(d/'root')).replace('/mesa-runtime.tar.gz',str(archive)).replace('/navigation-setup',str(setup))
  a=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True);report.update(stdout=a.stdout,stderr=a.stderr,shell_exit=a.returncode);assert a.returncode==0 and 'NAVI_GLES=mesa'in a.stdout
 report['exit']=0
except Exception as e:report.update(exit=2,error=repr(e))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
