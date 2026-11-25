from pathlib import Path
import asyncio,importlib.util,json,sys,tempfile,types
r=Path.cwd();root=Path(sys.argv[1]).resolve().parents[2];label=sys.argv[2]
spec=importlib.util.spec_from_file_location('mcu_transition_'+label,root/'src/emu/qnx_mcu_uart.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
a0=bytes.fromhex('feff0000001280020000020000a0000801aa55aa55aa550b0447');a2=bytes.fromhex('feff0000001580020000020000a2000b01534146452053544152540414')
async def run():
 clock=[1000000.0];m.time=types.SimpleNamespace(monotonic=lambda:clock[0]);incoming=asyncio.Queue();frames=[];readers=[];stop=asyncio.Event();state={}
 async def accept(reader,writer):
  await incoming.put(writer)
  async def collect():
   data=bytearray()
   while chunk:=await reader.read(4096):
    data.extend(chunk)
    while len(data)>=6:
     assert data[:2]==b'\xff\xfe';n=int.from_bytes(data[4:6],'big')+8
     if len(data)<n:break
     wire=bytes(data[:n]);del data[:n];assert sum(wire[2:-2])&65535==int.from_bytes(wire[-2:],'big');frames.append(wire)
  readers.append(asyncio.create_task(collect()))
 async def until(check):
  async def wait():
   while not check():await asyncio.sleep(.01)
  await asyncio.wait_for(wait(),3)
 def inputs():return [w for w in frames if w[12:14]==b'\x00\x91']
 async def wake(writer):
  writer.write(b'?');await writer.drain();await asyncio.sleep(.08)
 with tempfile.TemporaryDirectory(prefix='mcu-transition-')as tmp:
  path=Path(tmp);server=await asyncio.start_server(accept,'127.0.0.1',0);port=server.sockets[0].getsockname()[1];task=asyncio.create_task(m.peer(port,path,stop,state));writer=await asyncio.wait_for(incoming.get(),3)
  try:
   writer.write(a0+a2);await writer.drain();await until(lambda:len(inputs())>=2);initial=len(inputs())
   for _ in range(3):clock[0]+=6;await wake(writer)
   repeated=len(inputs())-initial
   (path/'mcu-uart-vehicle-input.json').write_text('{"ignition":false}');before=len(inputs());await wake(writer);vehicle_delta=len(inputs())-before
   (path/'mcu-uart-bench-input.json').write_text('{"state":2}');before=len(inputs());await wake(writer);power_delta=len(inputs())-before
   before=len(inputs());writer.write(a2);await writer.drain();await asyncio.sleep(.12);restart_delta=len(inputs())-before
   before=len(inputs());writer.close();await writer.wait_closed();writer=await asyncio.wait_for(incoming.get(),3);await until(lambda:len(inputs())>=before+2);reconnect_delta=len(inputs())-before
   result=dict(initial_inputs=initial,unchanged_poll_inputs=repeated,ignition_transition_inputs=vehicle_delta,power_transition_inputs=power_delta,original_start_replay_inputs=restart_delta,reconnect_replay_inputs=reconnect_delta,transport_replies=state.get('transport_replies'),packets=[w.hex()for w in frames],scope='Real loopback async MCU peer packet behavior, original captured A0/A2 frames; not QNX GUI proof')
   result['exit']=0 if(initial,repeated,vehicle_delta,power_delta,restart_delta,reconnect_delta)==(2,0,1,1,2,2)else 1
   return result
  finally:
   stop.set();writer.close();await writer.wait_closed();task.cancel();await asyncio.gather(task,*readers,return_exceptions=True);server.close();await server.wait_closed()
try:record=dict(label=label,input='Same captured original A0/A2; three unchanged6s polls, ignitionOFF, powerIOC2, original-start and reconnect replay',**asyncio.run(run()))
except Exception as e:record=dict(label=label,exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
