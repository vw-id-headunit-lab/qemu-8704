import ast,json,sys
from pathlib import Path
p=Path(sys.argv[1])if len(sys.argv)>1 else Path(__file__).resolve().parents[1]/'tools/macos_hvf.py'
try:
 t=ast.parse(p.read_text());calls=[n for n in ast.walk(t)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='run'];assert len(calls)==1
 selected={k.arg:ast.literal_eval(k.value)for k in calls[0].keywords if isinstance(k.value,ast.Constant)}
 assert selected.get('disable_animations')is True
 out=dict(source=str(p),run_keywords=selected,scope='Canonical worker selects original supported widget property; runtime log and visual latency acceptance separate',exit=0)
except Exception as e:out=dict(error=repr(e),exit=2)
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
