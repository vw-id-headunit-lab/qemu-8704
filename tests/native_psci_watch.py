from pathlib import Path
import importlib.util,json,os,struct,sys,tempfile
repo=Path(__file__).resolve().parents[1];root=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else repo
try:
 sys.path[:0]=[str(repo/'tools'),str(repo/'src/emu'),str(repo/'src/firmware')]
 spec=importlib.util.spec_from_file_location('psci_stage',root/'src/emu/native_psci_watch.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 original=os.environ.pop('MEB_PSCI_CPU_ON_WATCH',None)
 try:
  assert m.arm_if_selected(None)is None
  for value,native,exclusive in [('unknown',True,False),(m.PROFILE,False,False),(m.PROFILE,True,True)]:
   os.environ['MEB_PSCI_CPU_ON_WATCH']=value
   try:m.arm_if_selected(None,native=native,exclusive=exclusive)
   except ValueError:pass
   else:raise AssertionError('Unexpected observer profile accepted')
  os.environ['MEB_PSCI_CPU_ON_WATCH']=m.PROFILE
  class RSP:
   def __init__(self):
    self.mode=b'0';self.calls=[];self.pc=m.POINTS['lookup'];self.queued=None;self.pending=True;self.memory={};self.vcpu=0x803a25c0;self.owner=0x80347840;self.table=0x80347000
    def put(a,b):
     for i,v in enumerate(b):self.memory[a+i]=v
    self.put=put;raw=(m.vm.ROOT/'var/inputs/support/display-chain-8704/original-platform/hyp-binary/hv.img').read_bytes()
    for pc in m.POINTS.values():put(pc,raw[pc-m.BASE:pc-m.BASE+16])
    put(self.vcpu+0x828,struct.pack('<Q',self.owner));put(self.owner,struct.pack('<I',4));put(self.owner+0x18,struct.pack('<QI',self.table,2));put(self.table,struct.pack('<QQ',self.vcpu,0x803a7040));put(self.vcpu+0x838,struct.pack('<QII',0,4,0));put(0x803a7040+0x838,struct.pack('<QII',1,4,1))
   def command(self,text):
    self.calls.append(text)
    if text.startswith('Qqemu.PhyMemMode:'):self.mode=text[-1:].encode();out=b'OK'
    elif text=='qqemu.PhyMemMode':out=self.mode
    elif text.startswith(('Z1,','z1,','Hg')):out=b'OK'
    elif text.startswith('m'):
     a,n=(int(x,16)for x in text[1:].split(','));out=bytes(self.memory[a+i]for i in range(n)).hex().encode()
    elif text.startswith('qSupported'):out=b'multiprocess+;qXfer:features:read+'
    elif text=='qfThreadInfo':out=b'mp1.4'
    elif text=='qsThreadInfo':out=b'l'
    elif text=='g':
     words=[0]*33;words[1]=(1<<64)-2 if self.pc==m.POINTS['return']else 1;words[19]=self.vcpu;words[21]=1;words[32]=self.pc;out=(struct.pack('<33Q',*words)+struct.pack('<I',0x3c9)).hex().encode()
    elif text.startswith('p'):
     value={100:self.vcpu,101:0x80080001,102:4<<48}[int(text[1:],16)];out=struct.pack('<Q',value).hex().encode()
    else:raise AssertionError(text)
    if self.pending:self.pending=False;self.queued=out;return b'T05thread:p1.4;'
    return out
   def select_application_cpu(self):return 'p1.1'
   def receive(self):out=self.queued;self.queued=None;return out
   def xml_registers(self):return {n:dict(number=100+i,bits=64)for i,n in enumerate(['TPIDR_EL2','HCR_EL2','VTTBR_EL2'])},{}
  class QMP:
   def __init__(self,rsp):self.rsp=rsp
   def execute(self,cmd,args):assert cmd=='human-monitor-command';return 'CPU#3\n PC='+format(self.rsp.pc,'016x')+'\n'
  rsp=RSP();q=QMP(rsp);state=m.arm_if_selected(rsp,native=True);assert state['armed']==['lookup']
  with tempfile.TemporaryDirectory()as tmp:
   assert m.observe(rsp,q,state,Path(tmp));assert state['phase']=='return'and state['armed']==['return'];event=state['events'][-1];assert event['vmid']==4 and event['requested_hid']==1 and event['lookup_vcpu']==0 and [x['hid']for x in event['vcpus']]==[0,1]
   rsp.pc=m.POINTS['return'];rsp.pending=True
   assert m.observe(rsp,q,state,Path(tmp));assert state['complete']and not state['armed']and state['events'][-1]['return_value']==-2 and state['events'][-1]['same_call']
  assert rsp.mode==b'0';assert not any(x.startswith(('M','P'))for x in rsp.calls);assert state['memory_modified']is False and state['registers_modified']is False
  source=(root/'src/emu/vlm_offline_live_darwin.py').read_text();assert 'psci_observer.arm_if_selected'in source and "phase='expired'"in source and "qmp.execute('cont');continue"in source
 finally:
  if original is None:os.environ.pop('MEB_PSCI_CPU_ON_WATCH',None)
  else:os.environ['MEB_PSCI_CPU_ON_WATCH']=original
 print('NATIVE_PSCI_WATCH_PASS optional_original_HYP_only=1 finite_hardware_breakpoints=1 pending_stop_packet_consumed=1 read_original_CPU_ON_HID_lookup_and_return=1 no_memory_or_register_writes=1 normal_execution_resumed_by_owner=1');raise SystemExit(0)
except Exception as error:print(json.dumps(dict(exit=2,error=repr(error))));raise SystemExit(2)
