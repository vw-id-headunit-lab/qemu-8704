from pathlib import Path
import sys,importlib.util,json
r=Path(__file__).resolve().parents[1];sys.path.insert(0,str(r/'tools'));p=Path(sys.argv[1]).resolve();spec=importlib.util.spec_from_file_location('recorded_source',p);m=importlib.util.module_from_spec(spec);record={'source':str(p),'input':'Current original QNX epoch saved IC/HUD native PNG receipts; exact source, shape, hash, channel and real age, never live-readback acceptance'}
try:
 spec.loader.exec_module(m);frames={role:m.recorded_frame_info(role)for role in ['ic','hud']};assert all(x['recorded']and x['frame_age_seconds']>=0 for x in frames.values());record.update(frames=frames,exit=0)
except Exception as error:record.update(error=repr(error),exit=2)
print(json.dumps(record,ensure_ascii=False,indent=2));raise SystemExit(record['exit'])
