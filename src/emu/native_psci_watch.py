import hashlib,os,re,struct,time
from pathlib import Path
import original_8704_vm as vm

HYP_SHA='49250f278f354865146c05355575312caa8dac24274f7d46e680aa48f1c889ff'
BASE=0x80080000
POINTS={'lookup':0x800d7964,'return':0x800d7990}
PROFILE='cpu-on-once-v1'

def command(rsp,text):
    reply=rsp.command(text)
    for _ in range(2):
        if reply[:1]not in(b'S',b'T'):return reply
        reply=rsp.receive()
    raise ValueError('Unconsumed PSCI debug notification')

def physical(rsp,address,count):
    mode=command(rsp,'qqemu.PhyMemMode')
    if mode not in(b'0',b'1'):raise ValueError('Unknown debugger physical memory mode')
    if command(rsp,'Qqemu.PhyMemMode:1')!=b'OK':raise ValueError('Physical original HYP observation unavailable')
    try:return command(rsp,f'm{address:x},{count:x}')
    finally:
        if command(rsp,'Qqemu.PhyMemMode:'+mode.decode())!=b'OK':raise ValueError('Cannot restore debugger virtual memory mode')

def withdraw(rsp,state):
    for name in list(state['armed']):
        if command(rsp,f'z1,{POINTS[name]:x},4')!=b'OK':raise ValueError('Cannot withdraw PSCI observer '+name)
        state['armed'].remove(name)

def arm_if_selected(rsp,*,native=False,exclusive=False):
    value=os.environ.get('MEB_PSCI_CPU_ON_WATCH','')
    if not value:return None
    if value!=PROFILE or not native or exclusive:raise ValueError('PSCI observation requires an exclusive original native profile')
    from vlm_platform import Elf
    source=vm.ROOT/'var/inputs/support/display-chain-8704/original-platform/hyp-binary'
    raw=(source/'hv.img').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=HYP_SHA:raise ValueError('Original PSCI Hypervisor differs')
    elf=Elf((source/'hypervisor').read_bytes());code={};application_thread=rsp.select_application_cpu()
    for name,pc in POINTS.items():
        actual=raw[pc-BASE:pc-BASE+16]
        if len(actual)!=16 or elf.at(pc,16)!=actual:raise ValueError('PSCI ELF and original image differ')
        loaded=physical(rsp,pc,16)
        if loaded.decode()!=actual.hex():raise ValueError('Loaded PSCI instruction bytes differ '+hex(pc)+' '+repr(loaded))
        code[name]=actual.hex()
    state=dict(profile=PROFILE,application_thread=application_thread,original_HYP_sha256=HYP_SHA,armed=[],events=[],code=code,phase='lookup',deadline_monotonic=time.monotonic()+120,memory_modified=False,registers_modified=False,complete=False)
    if command(rsp,f'Z1,{POINTS["lookup"]:x},4')!=b'OK':raise ValueError('Cannot arm original PSCI lookup observer')
    state['armed'].append('lookup');return state

def mem(rsp,address,count):
    if not 0x80000000<=address<address+count<=0x90000000 or not 0<count<=128:raise ValueError('PSCI observation pointer outside original HYP RAM')
    raw=physical(rsp,address,count).decode()
    if not re.fullmatch('[0-9a-fA-F]{'+str(count*2)+'}',raw):raise ValueError('PSCI original RAM observation unavailable')
    return bytes.fromhex(raw)

def cpu(rsp,pc):
    command(rsp,'qSupported:multiprocess+;qXfer:features:read+')
    reply=command(rsp,'qfThreadInfo').decode();threads=[]
    while reply.startswith('m')and len(threads)<64:
        threads+=reply[1:].split(',');reply=command(rsp,'qsThreadInfo').decode()
    for thread in threads:
        if command(rsp,'Hg'+thread)!=b'OK':raise ValueError('PSCI CPU selection failed')
        reply=command(rsp,'g').decode()
        if not re.fullmatch('[0-9a-fA-F]+',reply)or len(reply)<536:continue
        data=bytes.fromhex(reply);words=struct.unpack_from('<33Q',data)
        if words[32]!=pc:continue
        inventory,_=rsp.xml_registers();system={}
        for name in ['TPIDR_EL2','HCR_EL2','VTTBR_EL2','VMPIDR_EL2','MPIDR_EL1']:
            if name not in inventory:continue
            reg=inventory[name];wire=command(rsp,'p'+format(reg['number'],'x')).decode()
            if re.fullmatch('[0-9a-fA-F]{16}',wire):system[name]=int.from_bytes(bytes.fromhex(wire),'little')
        return dict(thread=thread,core={f'x{i}':words[i]for i in [0,1,19,20,21]},pc=pc,system=system)
    raise ValueError('Stopped PSCI application CPU was not found')

def observe(rsp,qmp,state,output):
    name=state['phase'];pc=POINTS[name]
    info=qmp.execute('human-monitor-command',{'command-line':'info registers -a'})
    if not re.search(r'PC=0*'+format(pc,'x'),info,re.I):return False
    event=cpu(rsp,pc)
    if mem(rsp,pc,16).hex()!=state['code'][name]:raise ValueError('Stopped original PSCI instruction changed')
    current=event['system']['TPIDR_EL2'];owner=struct.unpack('<Q',mem(rsp,current+0x828,8))[0];vmid=struct.unpack('<I',mem(rsp,owner,4))[0]
    if vmid not in(1,2,3,4):raise ValueError('Original PSCI current VM differs')
    event.update(point=name,vmid=vmid,current_vcpu=current,vm=owner)
    if name=='lookup':
        table,count=struct.unpack('<QI',mem(rsp,owner+0x18,12))
        if not 0<count<=8:raise ValueError('Original PSCI vCPU count differs')
        rows=[]
        for i in range(count):
            ptr=struct.unpack('<Q',mem(rsp,table+i*8,8))[0];hid,identifier,index=struct.unpack('<QII',mem(rsp,ptr+0x838,16));rows.append(dict(vcpu=ptr,hid=hid,vmid=identifier,index=index))
        event.update(requested_hid=event['core']['x21'],lookup_vcpu=event['core']['x0'],vcpus=rows)
        state['active_thread']=event['thread'];state['active_vmid']=vmid
    else:
        value=event['core']['x1'];event['return_value']=value-(1<<64)if value>>63 else value
        event['same_call']=event['thread']==state.get('active_thread')and vmid==state.get('active_vmid')
    state['events'].append(event);withdraw(rsp,state)
    if(name=='return'and vmid==4 and event['same_call'])or len(state['events'])>=32:
        state['complete']=True;state['phase']='complete'
    else:
        next_name='return'if name=='lookup'else'lookup'
        if command(rsp,f'Z1,{POINTS[next_name]:x},4')!=b'OK':raise ValueError('Cannot arm next PSCI observation point')
        state['phase']=next_name;state['armed'].append(next_name)
    vm.save(Path(output)/'native-psci-watch.json',state);return True
