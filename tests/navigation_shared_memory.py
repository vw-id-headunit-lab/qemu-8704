from pathlib import Path
import json,os,subprocess,sys,tempfile
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 p=r/'src/hvf_bench/guest/hmi-init.sh';s=p.read_text();assert s.count('prepare_shared_memory() {')==1
 start=s.index('    prepare_shared_memory() {');end=s.index('\n    }',start)+len('\n    }');function=s[start:end]
 assert s.index('prepare_shared_memory /mnt/agl')<s.index('. /platform-setup')
 assert s.index('prepare_shared_memory /mnt/agl')>s.index('for directory in tmp run var data; do')
 q=subprocess.run(['/bin/sh','-n',p],capture_output=True,text=True);assert q.returncode==0,(q.stdout,q.stderr)
 with tempfile.TemporaryDirectory()as temp:
  d=Path(temp);root=d/'root';(root/'dev/shm').mkdir(parents=True);(root/'data/sharedmemory').mkdir(parents=True)
  sentinel=root/'data/sharedmemory/existing';sentinel.write_bytes(b'existing original state');sentinel.chmod(0o640)
  other=root/'data/other';other.mkdir();other.chmod(0o750)
  bb=d/'busybox';bb.write_text('#!/bin/sh\nif [ "${FAIL_CHMOD-}" = 1 ] && [ "$1" = chmod ]; then exit 13; fi\nexec "$@"\n');bb.chmod(0o755)
  def run(where,fail=False):
   return subprocess.run(['/bin/sh','-c','BB="$1"; '+function+'; prepare_shared_memory "$2"','test',str(bb),str(where)],env=dict(os.environ,FAIL_CHMOD='1'if fail else'0'),capture_output=True,text=True)
  q=run(root);assert q.returncode==0,(q.stdout,q.stderr)
  assert all((root/n).stat().st_mode&0o7777==0o777 for n in ['dev/shm','data/sharedmemory'])
  assert sentinel.read_bytes()==b'existing original state'and sentinel.stat().st_mode&0o7777==0o640
  assert other.stat().st_mode&0o7777==0o750
  new=d/'new';q=run(new);assert q.returncode==0;assert all((new/n).is_dir()for n in ['dev/shm','data/sharedmemory'])
  outside=d/'outside';outside.mkdir();outside.chmod(0o700);bad=d/'bad';(bad/'dev').mkdir(parents=True);(bad/'dev/shm').symlink_to(outside,target_is_directory=True)
  q=run(bad);assert q.returncode==1 and outside.stat().st_mode&0o7777==0o700
  q=run(root,True);assert q.returncode==1
 print('ORIGINAL_SHARED_MEMORY_STAGE_PASS native_mode0777=1 before_services=1 existing_content_preserved=1 nonrecursive=1 create_missing=1 symlink_rejected=1 chmod_failure_propagated=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
