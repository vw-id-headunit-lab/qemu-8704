from pathlib import Path
import asyncio,json,os,time

def packet(command,payload,sequence=0,channel=0x200):
 if not 0<=sequence<=255:raise ValueError('UART sequence out of range')
 if channel not in (0x100,0x200):raise ValueError('UART channel must be power or transport')
 body=bytes([sequence,0])+(10+len(payload)).to_bytes(2,'big')+channel.to_bytes(3,'big')+(channel|0x800000).to_bytes(3,'big')+command.to_bytes(2,'big')+len(payload).to_bytes(2,'big')+payload
 return b'\xff\xfe'+body+(sum(body)&65535).to_bytes(2,'big')

def power_frame(state,sequence=1):
 if state not in (2,9):raise ValueError('Stationary bench power state must be IOC2 or ON9')
 return packet(0x91,bytes([1,state,state,0,0,0,0]),sequence,channel=0x100)

def vehicle_frame(ignition,sequence=1):
 if type(ignition) is not bool:raise ValueError('Stationary bench ignition must be boolean')
 active=int(ignition)
 return packet(0x91,bytes([5,0,0,0,0,active,active,active]),sequence,channel=0x100)

def transport_reply(frame):
 if len(frame)<18 or frame[:4]!=b'\xfe\xff\x00\x00' or frame[6:12]!=bytes.fromhex('800200000200'):return None
 if len(frame)!=int.from_bytes(frame[4:6],'big')+8 or sum(frame[2:-2])&65535!=int.from_bytes(frame[-2:],'big'):return None
 command=int.from_bytes(frame[12:14],'big');payload=frame[16:-2]
 if command==0xa0 and payload==bytes.fromhex('01aa55aa55aa550b'):return packet(command,b'\x81'+payload[1:])
 if command==0xa2 and payload==b'\x01SAFE START':return packet(command,b'\x81'+payload[1:])
 return None

def serial_arguments(output,selected,environ=None):
 env=os.environ if environ is None else environ
 port=env.get('MEB_QNX_MCU_UART_PORT')
 if port is None:return ['-serial','file:'+str(Path(output)/'exynos-secondary-uart.bin')]
 if not selected or not port.isdecimal() or not 1024<=int(port)<=65535:raise ValueError('Native QNX MCU UART requires a loopback port and original QNX')
 return ['-chardev',f'socket,id=oem-qnx-mcu,host=127.0.0.1,port={port},server=on,wait=off,nodelay=on,logfile={Path(output)/"exynos-secondary-uart.bin"}','-serial','chardev:oem-qnx-mcu']

def desired_power(directory):
 path=Path(directory)/'mcu-uart-bench-input.json'
 if not path.exists():return 9
 value=json.loads(path.read_bytes())
 if set(value)!={'state'} or type(value['state']) is not int or value['state'] not in (2,9):raise ValueError('Invalid local bench power input')
 return value['state']

def desired_vehicle(directory):
 path=Path(directory)/'mcu-uart-vehicle-input.json'
 if not path.exists():return True
 value=json.loads(path.read_bytes())
 if set(value)!={'ignition'} or type(value['ignition']) is not bool:raise ValueError('Invalid local stationary ignition input')
 return value['ignition']

def cfa_input(directory):
 path=Path(directory)/'mcu-uart-cfa-input.json'
 if not path.exists():return None
 value=json.loads(path.read_bytes())
 if set(value)!={'id','frame'}or not isinstance(value['id'],str)or len(value['id'])!=16 or any(c not in '0123456789abcdef'for c in value['id']):raise ValueError('Invalid CFA input identity')
 if not isinstance(value['frame'],str):raise ValueError('Invalid CFA frame type')
 frame=bytes.fromhex(value['frame'])
 if not 22<=len(frame)<=530 or frame[:2]!=b'\xff\xfe'or frame[3]!=0 or frame[6:12]!=bytes.fromhex('000008800002')or frame[12:14]!=b'\x00\x98':raise ValueError('Invalid MCU CFA route')
 if len(frame)!=int.from_bytes(frame[4:6],'big')+8 or int.from_bytes(frame[14:16],'big')!=len(frame)-18 or sum(frame[2:-2])&65535!=int.from_bytes(frame[-2:],'big'):raise ValueError('Invalid MCU CFA length or checksum')
 receipt=Path(directory)/'mcu-uart-cfa-delivery.json'
 if receipt.exists():
  previous=json.loads(receipt.read_bytes())
  if previous.get('id')==value['id']:
   if previous.get('frame')!=frame.hex():raise ValueError('Delivered CFA identity reused with different bytes')
   return None
 return value['id'],frame

async def peer(port,directory,stop,state):
 directory=Path(directory);state.update(port=port,source='declared stationary local MCU power and ignition input',cmac_replies=0,connected=False)
 while not stop.is_set():
  writer=None
  try:
   reader,writer=await asyncio.open_connection('127.0.0.1',port)
   state.update(connected=True,error=None);buffer=bytearray();sent_state=None;last_send=0;sent_ignition=None;last_vehicle=0;ready=False
   log=directory/'exynos-secondary-uart.bin'
   prior=directory/'mcu-uart-input.jsonl'
   if prior.exists():
    records=[json.loads(line) for line in prior.read_text().splitlines()]
    ready=any(bytes.fromhex(row['source_frame'])[12:14]==b'\x00\xa2' and transport_reply(bytes.fromhex(row['source_frame']))==bytes.fromhex(row['transport_reply']) for row in records)
   previous=directory/'mcu-uart-last-input.bin'
   if not previous.exists():previous=directory/'mcu-uart-bench-power.bin'
   if previous.exists():
    prior_frame=previous.read_bytes()
    if len(prior_frame) in (25,26) and prior_frame[:2]==b'\xff\xfe' and sum(prior_frame[2:-2])&65535==int.from_bytes(prior_frame[-2:],'big'):state['sequence']=prior_frame[2]
   if not ready and log.exists():buffer.extend(log.read_bytes()[:4096])
   while not stop.is_set():
    while True:
     start=buffer.find(b'\xfe\xff')
     if start<0:buffer[:]=buffer[-1:];break
     if start:del buffer[:start]
     if len(buffer)<6:break
     size=int.from_bytes(buffer[4:6],'big')+8
     if not 18<=size<=4096:del buffer[0];continue
     if len(buffer)<size:break
     frame=bytes(buffer[:size]);del buffer[:size]
     if sum(frame[2:-2])&65535!=int.from_bytes(frame[-2:],'big'):continue
     state['ap_valid_frames']=state.get('ap_valid_frames',0)+1
     reply=transport_reply(frame)
     if reply:
      writer.write(reply);await writer.drain()
      state['transport_replies']=state.get('transport_replies',0)+1
      with (directory/'mcu-uart-input.jsonl').open('a')as file:file.write(json.dumps(dict(source_frame=frame.hex(),transport_reply=reply.hex(),monotonic=time.monotonic()))+'\n')
      if frame[13]==0xa0:
       ready=False;sent_state=None;sent_ignition=None;state['original_reset_observed']=frame.hex()
      if frame[13]==0xa2:
       ready=True;sent_state=None;sent_ignition=None;state['original_start_observed']=frame.hex()
    desired=desired_power(directory)
    if ready and desired!=sent_state:
     state['sequence']=(state.get('sequence',0)+1)&255
     wire=power_frame(desired,state['sequence']);writer.write(wire);await writer.drain();sent_state=desired;last_send=time.monotonic()
     (directory/'mcu-uart-bench-power.bin').write_bytes(wire)
     (directory/'mcu-uart-last-input.bin').write_bytes(wire)
     state.update(power_inputs=state.get('power_inputs',0)+1,power_state=desired,last_input_hex=wire.hex(),last_input_monotonic=last_send)
    ignition=desired_vehicle(directory)
    if ready and ignition!=sent_ignition:
     state['sequence']=(state.get('sequence',0)+1)&255
     wire=vehicle_frame(ignition,state['sequence']);writer.write(wire);await writer.drain();sent_ignition=ignition;last_vehicle=time.monotonic()
     (directory/'mcu-uart-bench-vehicle.bin').write_bytes(wire)
     (directory/'mcu-uart-last-input.bin').write_bytes(wire)
     state.update(vehicle_inputs=state.get('vehicle_inputs',0)+1,ignition=ignition,last_vehicle_hex=wire.hex(),last_vehicle_monotonic=last_vehicle)
    cfa=cfa_input(directory)if ready else None
    if cfa:
     ident,wire=cfa;writer.write(wire);await writer.drain()
     receipt=dict(id=ident,frame=wire.hex(),source='declared virtual MCU CFA UART input',monotonic=time.monotonic(),delivery='host UART bytes written; native interpretation recorded separately')
     target=directory/'mcu-uart-cfa-delivery.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(receipt,indent=2)+'\n');temp.replace(target)
     state.update(cfa_inputs=state.get('cfa_inputs',0)+1,last_cfa_input=receipt)
    try:data=await asyncio.wait_for(reader.read(4096),1)
    except TimeoutError:continue
    if not data:raise ConnectionError('Native MCU UART disconnected')
    state['ap_tx_bytes']=state.get('ap_tx_bytes',0)+len(data);buffer.extend(data)
  except (ConnectionError,OSError,ValueError) as error:state['error']=str(error)
  finally:
   state['connected']=False
   if writer:
    writer.close()
    try:await asyncio.wait_for(writer.wait_closed(),2)
    except (ConnectionError,OSError,TimeoutError):writer.transport.abort()
  try:await asyncio.wait_for(stop.wait(),1)
  except TimeoutError:pass
