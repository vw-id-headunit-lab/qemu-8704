from pathlib import Path
import ast,asyncio,importlib.util,json,sys
r=Path(__file__).resolve().parents[1];p=Path(sys.argv[1])if len(sys.argv)>1 else r/'src/hvf_bench/guest/hmi-init.sh'
try:
 assert 'de.vw.mib.asl.system.input.rsi.disable=true'not in p.read_text()
 viewer=(r/'src/hvf_bench/src/hmi_bench/viewer.py').read_text();assert 'input-target'not in viewer and 'def _emit_map_input'not in viewer
 tree=ast.parse(viewer);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='VNCViewerServer');fn=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef)and n.name=='_pointer');calls=[n for n in ast.walk(fn)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='_emit_input'];assert len(calls)==1
 launcher=(r/'src/hvf_bench/src/hmi_bench/launcher.py').read_text();assert 'virtio-multitouch-pci,id=map-touch'not in launcher
 out=dict(scope='Original8704 RSI input is not disabled; one canonicalviewer pointer path, no manualmaptarget orseconddefaulttouchscreen; live navigationacceptance separate',source=str(p),exit=0)
except Exception as e:out=dict(error=repr(e),source=str(p),exit=2)
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
