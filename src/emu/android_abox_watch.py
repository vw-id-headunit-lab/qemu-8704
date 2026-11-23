
import hashlib

from linux_panic_watch import cstring,mem,fault_walk

IMAGE_BASE=0xffffffc010080000
PROBE=0xffffffc010bcc4d0
PROBE_SHA='46580af3b7d5a45309b1ca951b90d0e239939e9ece8fff3730809c37fff3deff'
CHECKPOINTS={
    'after-dsp-mappings':0xffffffc010bcc938,
    'clock-0-request':0xffffffc010bcc98c,
    'clock-0-return':0xffffffc010bcc990,
    'clock-1-request':0xffffffc010bcc9b4,
    'clock-1-return':0xffffffc010bcc9b8,
    'runtime-resume-request':0xffffffc010bccc48,
    'runtime-resume-return':0xffffffc010bccc4c,
    'axiusers-property-request':0xffffffc010bc981c,
    'axiusers-property-return':0xffffffc010bc9820,
    'axiusers-register-read':0xffffffc010bc9860,
    'axiusers-register-return':0xffffffc010bc9864,
    'axiusers-return':0xffffffc010bccc54,
    'dsp-query-request':0xffffffc010bccccc,
    'dsp-query-return':0xffffffc010bcccd0,
}


LINUX_CHECKPOINTS={
    'linux-abox-clock-prepare-return':0xffffffc010813228,
    'linux-general-probe':0xffffffc010581b98,
    'linux-lk-vmap-request':0xffffffc010581b24,
    'linux-lk-vmap-return':0xffffffc010581b28,
    'linux-lk-log-copy':0xffffffc010581bf0,
    'linux-lk-log-return':0xffffffc010581bf4,
    'linux-lk-mcu-map-request':0xffffffc010581c10,
    'linux-lk-mcu-map-return':0xffffffc010581c14,
    'linux-lk-mcu-copy-end':0xffffffc010581c48,
    'linux-general-probe-return':0xffffffc0105820ac,
}
EXTRA_PROBE=(0xffffffc010bc97a8,476,'0713f3ad55cc9508bd2146e77ec307488302a9a968d9b6afcd7f249072f34d62')
LINUX_PROBES=(
    (0xffffffc0108131f8,120,'863d250061aa3e0b50d3cd83369c61597db0f3b98168346b5d3dacc9219129f8'),
    (0xffffffc010581b98,1544,'407b5586b543d41f5d4d359fd4a147ea2a64ba01458df57c504172453b44d753'),
    (0xffffffc010581a78,288,'6e7da3469b5f15cf0f5cfc2d830cc38eb0f0c45524ad807f616fbd93003a1529'),
)
CLOCK_REQUEST=0xffffffc010a99178
ALL=CHECKPOINTS|LINUX_CHECKPOINTS|{'linux-abox-clock-prepare-call':CLOCK_REQUEST}


def arm(rsp,kernel,linux=None,qnx_clock=False):
    def verify(image,rows):
        for pc,size,digest in rows:
            off=pc-IMAGE_BASE
            if hashlib.sha256(image[off:off+size]).hexdigest()!=digest:
                raise ValueError('Original checkpoint function identity changed')
    verify(kernel,((PROBE,2864,PROBE_SHA),EXTRA_PROBE))
    if linux is not None:
        verify(linux,LINUX_PROBES)
    selected=dict(CHECKPOINTS)
    if linux is not None:selected.update(LINUX_CHECKPOINTS)
    if qnx_clock:
        if linux is None:raise ValueError('QNX clock bracket requires Linux source')
        verify(linux,((0xffffffc010a99150,80,'f74a798ebd3c9937c288a4fd31e9f68987da72f85bf94135b1eabda37c4b2faf'),))
        selected['linux-abox-clock-prepare-call']=CLOCK_REQUEST
    code={}
    for name,pc in selected.items():
        if rsp.command(f'Z1,{pc:x},4')!=b'OK':
            raise ValueError('ABOX checkpoint could not be armed: '+name)
        img=linux if name.startswith("linux-") else kernel
        code[name]=img[pc-IMAGE_BASE:pc-IMAGE_BASE+8].hex()
    state=dict(code=code,armed=list(code),events=[],memory_modified=False,qnx_audio_clock=qnx_clock)
    if qnx_clock:
        from qnx_audio_clock_watch import arm_service
        arm_service(rsp,state)
    return state


def pending(state):
    return bool(state and (state['armed'] or state.get('qnx_clock_service',{}).get('armed') or
                          state.get('qnx_clock_bracket',{}).get('armed')))


def withdraw(rsp,name):
    reply=rsp.command(f'z1,{ALL[name]:x},4')
    if reply[:1] in (b'S',b'T'):
        reply=rsp.receive()
    if reply!=b'OK':
        raise ValueError('ABOX checkpoint could not be removed: '+name)


def close_observer(rsp,state):
    if state.get('qnx_audio_clock'):
        from qnx_audio_clock_watch import close_service,deactivate
        deactivate(rsp,state)
        close_service(rsp,state)
    for name in state['armed']:withdraw(rsp,name)


def observe(rsp,qmp,state,output=None):
    snap=rsp.system_snapshot()['cpus']
    if state.get('qnx_audio_clock'):
        import qnx_audio_clock_watch as qnx_clock
        if qnx_clock.observe(rsp,state,snap,qmp) or qnx_clock.observe_service(rsp,state,snap,qmp):return True
    for cpu in snap:
        sys=cpu['system']
        vmid=int(sys.get('VTTBR_EL2','0'),16)>>48
        if vmid not in (3,4):
            continue
        core=cpu['core']
        addresses=(LINUX_CHECKPOINTS|{'linux-abox-clock-prepare-call':CLOCK_REQUEST}) if vmid==3 else CHECKPOINTS
        name=next((n for n in state['armed'] if n in addresses and core['pc']==hex(addresses[n])),None)
        if not name:
            continue
        if rsp.command('Hg'+cpu['thread'])!=b'OK':
            raise ValueError('Cannot select stopped Android ABOX vCPU')
        code=mem(rsp,ALL[name],8)
        if code is None or code.hex()!=state['code'][name]:
            raise ValueError('Live ABOX checkpoint instruction mismatch')
        event=dict(name=name,thread=cpu['thread'],vmid=vmid,pc=core['pc'],x0=core['x0'],x1=core['x1'],
                   verified_original_instructions=True)
        if state.get('qnx_audio_clock'):qnx_clock.ordered(state,event)
        if name=='axiusers-property-request':
            event['property']=cstring(rsp,int(core['x0'],16))
        if name=='axiusers-register-read':
            event['register']=fault_walk(qmp,dict(sys,FAR_EL1=core['x8']))
        if name=='linux-abox-clock-prepare-call':
            event['clock']=cstring(rsp,int(core['x1'],16))
            if event['clock']!='cpu':raise ValueError('Unexpected original ABOX first clock')
            qnx_clock.activate(rsp,state)
        if name=='linux-abox-clock-prepare-return':
            if state.get('qnx_audio_clock'):qnx_clock.deactivate(rsp,state)
            event['clock']=cstring(rsp,int(core['x21'],16))
            handle=int(core['x19'],16)
            h=mem(rsp,handle,8)
            if h:
                pointer=int.from_bytes(h,'little')
                count=mem(rsp,pointer+0x80,4)
                event['clock_core']=hex(pointer)
                event['prepare_count']=int.from_bytes(count,'little') if count else None
            caller=mem(rsp,int(core['sp'],16)+8,8)
            if caller:
                event['caller_return']=hex(int.from_bytes(caller,'little'))
            if output is not None:
                from linux_i2c_platform import capture_clock_rpc
                from linux_pid1_context import LINUX_SHA
                report=capture_clock_rpc(qmp,output/'linux-abox-clock-rpc',
                    {'kernel_sha256':LINUX_SHA,'physical':0xb8080000})
                event['clock_rpc_capture']=report.get('data')
        if name=='linux-lk-vmap-request':
            raw=mem(rsp,int(core['x0'],16),8)
            if raw is not None:
                page=int.from_bytes(raw,'little')
                event['struct_page']=hex(page)
                event['struct_page_readable']=mem(rsp,page,8) is not None
                event['struct_page_walk']=fault_walk(qmp,dict(sys,FAR_EL1=hex(page)))
        if name.startswith('clock-') and name.endswith('-request'):
            event['clock']=cstring(rsp,int(core['x1'],16))
        state['events'].append(event)
        withdraw(rsp,name)
        state['armed'].remove(name)
        return True
                                                                         
                                                                              
                                                                                 
    for clock_name in ('linux-abox-clock-prepare-return','linux-abox-clock-prepare-call'):
        if clock_name not in state['armed']:continue
        pc=ALL[clock_name]
        hit=next((cpu for cpu in snap if cpu['core'].get('pc')==hex(pc) and
                  int(cpu['system'].get('VTTBR_EL2','0'),16)>>48==4),None)
        if hit is not None:
            withdraw(rsp,clock_name)
            collisions=state.setdefault('foreign_vmid_breakpoints',[])
            row={'thread':hit['thread'],'vmid':4,'pc':hex(pc)}
            collisions.append(row)
            if len(collisions)>=8:
                state['armed'].remove(clock_name)
                row['action']='withdrew_optional_watch_after_collision_bound'
            else:
                reply=rsp.command('vCont;s:'+hit['thread'])
                if reply[:3] not in (b'S05',b'T05'):
                    raise ValueError('Unexpected original foreign-VMID step result: '+repr(reply))
                if rsp.command(f'Z1,{pc:x},4')!=b'OK':
                    raise ValueError('Could not rearm original Linux clock watch')
                row['action']='single_original_instruction_then_rearmed'
            return True
    return False

