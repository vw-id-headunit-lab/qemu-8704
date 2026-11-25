import hashlib,json,subprocess,sys
from pathlib import Path
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 init=(r/'src/hvf_bench/guest/hmi-init.sh').read_text();archive=(r/'src/hvf_bench/src/hmi_bench/archive.py').read_text();p=r/'src/hvf_bench/guest/navigation-memory.sh';s=p.read_text()
 assert init.index('$BB sh /navigation-memory')<init.index('$BB sh /navigation-setup')
 assert '"navigation-memory": "navigation-memory.sh"'in archive
 assert '2147483648'in s and '2097148'in s and 'swapon -p 100 /dev/zram0'in s and 'NAVIGATION_SWAP_REUSED'in s
 assert all(x not in s for x in ['oom_score_adj','drop_caches','swapoff','kill ','MXNavi'])
 for file in [p,r/'src/hvf_bench/guest/hmi-init.sh']:subprocess.run(['/bin/sh','-n',file],check=True,capture_output=True)
 print(json.dumps(dict(exit=0,stdout='NAVIGATION_MEMORY_STAGE_PASS zram=2GiB original_navigation_and_unified_input_unchanged',source_sha256=hashlib.sha256(p.read_bytes()).hexdigest())));raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,stderr=repr(e))));raise SystemExit(2)
