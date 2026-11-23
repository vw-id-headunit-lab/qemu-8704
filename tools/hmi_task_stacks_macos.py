from pathlib import Path
import hashlib,json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/emu'))
from gl_center_live_darwin import GLCenterReader
from linux_pid1_context import capture,capture_task_stack,LINUX_SHA
from vlm_platform import factory_file

def observe(session):
 from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
 image=factory_file('ivi/Image')
 with image.open('rb')as f:
  if hashlib.file_digest(f,'sha256').hexdigest()!=LINUX_SHA:raise ValueError('Original kernel identity differs')
  for addr,expected in [(0xffffffc0102919a4,('ldr','x19, [x19, #0x5b8]')),(0xffffffc01020d40c,('add','x1, x20, #0x6c0'))]:
   f.seek(addr-0xffffffc010080000);i=next(Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(f.read(4),addr))
   if(i.mnemonic,i.op_str)!=expected:raise ValueError('Original thread ABI differs')
 reader=GLCenterReader(session);rows=[];start=time.monotonic()
 try:
  read=reader.read;first=int(capture(reader.base.symbols,read)['task'],16);pending=[first];seen=set();leaders=[]
  while pending:
   task=pending.pop()
   if task in seen:continue
   if len(seen)>=4096:raise ValueError('Process tree exceeded bound')
   seen.add(task);pidptr=read(task+0x570)
   if pidptr and read(pidptr+0x58)==reader.base.addr['init_pid_ns']:
    comm=(read(task+0x6c0).to_bytes(8,'little')+read(task+0x6c8).to_bytes(8,'little')).split(b'\0',1)[0].decode('ascii',errors='replace')
    if comm.lower()in('tee','vtee','java'):leaders.append((task,comm))
   head=task+0x528;link=read(head);siblings=set()
   while link!=head:
    if link in siblings or len(siblings)>=1024:raise ValueError('Children list changed')
    siblings.add(link);pending.append(link-0x538);link=read(link)
  for leader,name in leaders:
   tasks=[leader]
   if name.lower()=='java':
    head=leader+0x5b8;link=read(head);threads=set()
    while link!=head:
     if link in threads or len(threads)>=512:raise ValueError('Thread group changed')
     threads.add(link);tasks.append(link-0x5b8);link=read(link)
   for task in tasks:
    pidptr=read(task+0x570)
    if not pidptr or read(pidptr+0x58)!=reader.base.addr['init_pid_ns']:continue
    tid=read(pidptr+0x50)&0xffffffff;comm=(read(task+0x6c0).to_bytes(8,'little')+read(task+0x6c8).to_bytes(8,'little')).split(b'\0',1)[0].decode('ascii',errors='replace')
    fields=(0x20,0xa60,0xa68,0xa70);before=tuple(read(task+x)for x in fields)
    try:row=capture_task_stack(reader.base.symbols,task,read)
    except ValueError as e:row={'status':'changing_or_unavailable','error':str(e),'frames':[]}
    after=tuple(read(task+x)for x in fields);row.update(tid=tid,comm=comm,leader=name,stable_saved_context=before==after,scope='Live owned RAM read only; per-task stable saved sleeping context, not current vCPU backtrace')
    if before!=after:row['frames']=[]
    rows.append(row)
  reader.check_live()
  result=dict(qemu_pid=reader.base.pid,original_kernel_sha256=LINUX_SHA,observed_unix=time.time(),read_seconds=time.monotonic()-start,tasks=rows);out=Path(session)/'hmi-live-task-stacks.json';out.write_text(json.dumps(result,indent=2)+'\n');return result
 finally:reader.close()

if __name__=='__main__':
 s=Path(json.loads((ROOT/'var/current-center.json').read_bytes())['session']);j=observe(s)
 for row in j['tasks']:
  print(row['tid'],row['comm'],row['status'],'stable='+str(row['stable_saved_context']),' -> '.join(x['symbol']for x in row['frames'][:8]))
 print('RESULT',s/'hmi-live-task-stacks.json','TASKS',len(j['tasks']))
