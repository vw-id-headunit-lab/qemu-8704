from pathlib import Path
import asyncio,json,os,shlex,signal,socket,struct,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'var/hvf/network-current.json'
sys.path.insert(0,str(ROOT/'src/emu'))

def read(path):
 return json.loads(Path(path).read_bytes())

def qmp(port,operation,arguments):
 with socket.create_connection(('127.0.0.1',port),timeout=3)as sock:
  sock.settimeout(5);stream=sock.makefile('rwb');json.loads(stream.readline())
  for name,args in [('qmp_capabilities',{}),(operation,arguments)]:
   stream.write(json.dumps(dict(execute=name,arguments=args)).encode()+b'\r\n');stream.flush()
   while True:
    line=stream.readline()
    if not line:raise ConnectionError('Emulator monitor closed')
    result=json.loads(line)
    if 'error'in result:raise RuntimeError(result['error'])
    if 'return'in result:break
  return result['return']

def android_backend(port):
 return dict(id='domain-network',backend=dict(type='socket',data=dict(addr=dict(type='inet',data=dict(host='127.0.0.1',port=str(port))),server=False,nodelay=True,**{'reconnect-ms':1000})))

def linux_endpoint():
 j=read(ROOT/'var/hvf/current.json');s=Path(j['session']);j=read(s/'session.json')
 from hvf_guest_command import session
 session('linux');return j['ports']['domain_port'],str(s)

def ensure():
 if STATE.exists():
  j=read(STATE)
  try:
   os.kill(j['pid'],0);args=shlex.split(subprocess.run(['ps','-p',str(j['pid']),'-o','command='],capture_output=True,text=True,check=True).stdout)
   if str(Path(__file__).resolve())in args:return j
  except(ProcessLookupError,subprocess.CalledProcessError):pass
 root=ROOT/'var/hvf'
 with(root/'network.stdout').open('ab')as out,(root/'network.stderr').open('ab')as err:
  child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker'],cwd=ROOT,stdout=out,stderr=err,start_new_session=True)
 for _ in range(100):
  if STATE.exists():
   j=read(STATE)
   if j.get('pid')==child.pid:return j
  if child.poll()is not None:raise RuntimeError('Private switch exited; inspect network.stderr')
  time.sleep(.05)
 raise TimeoutError('Same private switch is preparing; worker retained')

def stop():
 if not STATE.exists():return
 j=read(STATE)
 try:
  os.kill(j['pid'],0);args=shlex.split(subprocess.run(['ps','-p',str(j['pid']),'-o','command='],capture_output=True,text=True,check=True).stdout)
  if str(Path(__file__).resolve())in args:os.kill(j['pid'],signal.SIGTERM)
 except(ProcessLookupError,subprocess.CalledProcessError):pass

async def worker():
 peers={};learned={};counts={name:dict(received=0,sent=0)for name in ['linux','android','qnx','navigation-uplink']};attached_android=None;targets={};tasks={};mcu_state={};mcu_target=None;mcu_task=None;stop_event=asyncio.Event();errors={};port=0
 def save():
  data=dict(pid=os.getpid(),android_port=port,transport='isolated Ethernet length-prefixed stream switch',peers=sorted(peers),counts=counts,targets=targets,errors=errors,mcu=mcu_state,observed_unix=time.time())
  tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(STATE)
 async def pump(name,reader,writer):
  previous=peers.get(name)
  if previous:previous[0].close()
  peers[name]=(writer,asyncio.Lock());save()
  try:
   while True:
    size=struct.unpack('>I',await reader.readexactly(4))[0]
    if not 14<=size<=65535:raise ValueError('Ethernet frame length outside transport limit')
    frame=await reader.readexactly(size);counts[name]['received']+=1;learned[frame[6:12]]=name
    if name=='qnx':
     capture=Path(targets['qnx']['session'])/'network-qnx.pcap'
     if not capture.exists():capture.write_bytes(struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65535,1))
     if capture.stat().st_size<2*1024*1024:
      now=time.time()
      with capture.open('ab')as file:file.write(struct.pack('<IIII',int(now),int(now%1*1e6),len(frame),len(frame))+frame)
    if name=='qnx':
     duplicate_macs={bytes.fromhex('027dfa010800'),bytes.fromhex('027dfa009900')}
     if frame[6:12]in duplicate_macs:
      counts[name]['duplicate_native_domain_dropped']=counts[name].get('duplicate_native_domain_dropped',0)+1
      continue
     if len(frame)>=18 and frame[12:14]==b'\x81\x00'and int.from_bytes(frame[14:16],'big')&4095==3:
      frame=frame[:12]+frame[16:];size=len(frame)
      counts[name]['vlan3_decapsulated']=counts[name].get('vlan3_decapsulated',0)+1
    destination=learned.get(frame[:6])if not(frame[0]&1)else None
    selected=[destination]if destination in peers else list(peers)
    for other in selected:
     if other==name:continue
     peer=peers.get(other)
     if not peer:continue
     async with peer[1]:
      outgoing=frame
      if other=='qnx' and frame[12:14]==b'\x86\xdd':
       outgoing=frame[:12]+b'\x81\x00\x60\x03'+frame[12:]
       counts[other]['vlan3_encapsulated']=counts[other].get('vlan3_encapsulated',0)+1
      peer[0].write(struct.pack('>I',len(outgoing))+outgoing)
      await asyncio.wait_for(peer[0].drain(),2)
     counts[other]['sent']+=1
  except(asyncio.IncompleteReadError,ConnectionError,OSError,TimeoutError,ValueError)as error:
   errors[name]=str(error)
  finally:
   if peers.get(name,(None,))[0]is writer:peers.pop(name,None)
   for mac,owner in list(learned.items()):
    if owner==name:learned.pop(mac,None)
   writer.close()
   try:await asyncio.wait_for(writer.wait_closed(),2)
   except(ConnectionError,OSError,TimeoutError):writer.transport.abort()
   save()
 async def android_connection(reader,writer):
  await pump('android',reader,writer)
 async def connect(name,target_port):
  while not stop_event.is_set():
   try:
    reader,writer=await asyncio.open_connection('127.0.0.1',target_port)
    errors.pop(name,None);await pump(name,reader,writer)
   except(ConnectionError,OSError)as error:errors[name]=str(error)
   await asyncio.sleep(.5)
 async def navigation_uplink():
  while not stop_event.is_set():
   process=None
   try:
    process=await asyncio.create_subprocess_exec(sys.executable,'-B',str(ROOT/'tools/navigation_slirp.py'),str(ROOT/'config/navigation-uplink.json'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
    targets['navigation-uplink']=dict(pid=process.pid,profile=str(ROOT/'config/navigation-uplink.json'))
    errors.pop('navigation-uplink',None)
    await pump('navigation-uplink',process.stdout,process.stdin)
   except(ConnectionError,OSError,ValueError)as error:errors['navigation-uplink']=str(error)
   finally:
    if process and process.returncode is None:process.terminate()
    if process:await process.wait()
   await asyncio.sleep(1)
 if (ROOT/'config/navigation-uplink.json').exists():tasks['navigation-uplink']=asyncio.create_task(navigation_uplink())
 server=await asyncio.start_server(android_connection,'127.0.0.1',0);port=server.sockets[0].getsockname()[1];save()
 loop=asyncio.get_running_loop()
 for sig in [signal.SIGTERM,signal.SIGINT]:loop.add_signal_handler(sig,stop_event.set)
 try:
  while not stop_event.is_set():
   try:
    from hvf_guest_command import session
    _,android,_=session('android')
    if attached_android!=android['qemu_pid']:
     await asyncio.to_thread(qmp,android['qmp_port'],'chardev-change',android_backend(port));attached_android=android['qemu_pid'];errors.pop('android-control',None)
   except Exception as error:errors['android-control']=str(error)
   wanted={}
   try:lp,ls=linux_endpoint();wanted['linux']=dict(port=lp,session=ls);errors.pop('linux-control',None)
   except Exception as error:errors['linux-control']=str(error)
   try:
    import hvf_qnx
    qs,qj=hvf_qnx.descriptor();wanted['qnx']=dict(port=qj['network_port'],session=str(qs));errors.pop('qnx-control',None)
   except Exception as error:errors['qnx-control']=str(error)
   try:
    qs,qj=hvf_qnx.descriptor();mt=(qj.get('mcu_uart_port'),str(qs));errors.pop('mcu-control',None)
    if mt[0] and mt!=mcu_target:
     if mcu_task:
      mcu_task.cancel();await asyncio.gather(mcu_task,return_exceptions=True)
     from qnx_mcu_uart import peer as mcu_peer
     mcu_target=mt;mcu_state.clear();mcu_task=asyncio.create_task(mcu_peer(mt[0],mt[1],stop_event,mcu_state))
   except Exception as error:errors['mcu-control']=str(error)
   for name,target in wanted.items():
    if targets.get(name)!=target or name not in tasks or tasks[name].done():
     if name in tasks:
      tasks[name].cancel();await asyncio.gather(tasks[name],return_exceptions=True)
     targets[name]=target;tasks[name]=asyncio.create_task(connect(name,target['port']))
   save()
   try:await asyncio.wait_for(stop_event.wait(),1)
   except TimeoutError:pass
 finally:
  server.close()
  if mcu_task:
   mcu_task.cancel();await asyncio.gather(mcu_task,return_exceptions=True)
  for task in tasks.values():task.cancel()
  await asyncio.gather(*tasks.values(),return_exceptions=True)
  for writer,_ in list(peers.values()):writer.close()
  peers.clear()
  await asyncio.wait_for(server.wait_closed(),3)
  try:
   from hvf_guest_command import session
   _,android,_=session('android');lp,_=linux_endpoint()
   await asyncio.to_thread(qmp,android['qmp_port'],'chardev-change',android_backend(lp))
  except Exception as error:errors['restore-direct']=str(error)
  save()

if __name__=='__main__':
 if '--worker'in sys.argv:asyncio.run(worker())
 elif len(sys.argv)>1 and sys.argv[1]=='stop':stop()
 else:print(json.dumps(ensure(),indent=2))
