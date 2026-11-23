
import hashlib
import re

import clock_rpc_platform as clock
from vlm_platform import Elf

                                                                             
                                                                
BIASES = (0x78000000, 0x78182000)
SITES = {'name-open-call':0x7f60, 'name_open':0x7f68, 'MsgSend':0x7f90,
         'io-connect':0x7084, 'cal-enable':0x70b8, 'lookup':0x7038}
CAL_INIT = clock.LIBC.parents[4]/'qnx-cal-init/members/bin/cal_init'
CAL_INIT_SHA = 'db82429f4298a8b9ca17bc606e880ae2f07b3a2fe944d1fe0319563639e24a17'
RESULT_MASK = (1 << 32)-1
                                                                         
                                                              
SERVICE_SITES = {
    'cal-clock-thread-entry': 0x7fe8,
    'pthread-create-return': 0x82f8,
    'name-attach-return': 0x6e68,
    'receive-entry': 0x6ea8,
    'receive-failure': 0x6f00,
    'name-detach-call': 0x6fbc,
}
MAX_SITE_COLLISIONS = 8
                                                                  
                                                                 
PROC_REPORT = 0xffffff80600e4238
PROC_REPORT_BYTES = bytes.fromhex('ffc308d1fd7bbaa9fd030091f35301a9')
MAX_OTHER_PROC_REPORTS = 24
                                                                           
PROC_ABORT = 0xffffff806011c148
PROC_ABORT_BYTES = bytes.fromhex('fd7bb7a9fd030091f35301a9f55b02a9')
MAX_PROC_ABORT_HITS = 32


def mem(rsp,address,size):

    if not 0 < address < (1<<39) or not 1<=size<=32 or address+size > (1<<39):
        return None
    reply=rsp.command(f'm{address:x},{size:x}')
    if len(reply)!=size*2 or not re.fullmatch(rb'[0-9a-fA-F]+',reply):
        return None
    return bytes.fromhex(reply.decode('ascii'))


def checked_elf(path,digest):
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError(f'Original QNX executable identity changed: {path.name}')
    return Elf(data)


def original_library():
    return checked_elf(clock.LIBRARY,clock.LIBRARY_SHA)


def ordered(state,event):
    state['event_sequence']=state.get('event_sequence',0)+1
    event['sequence']=state['event_sequence']
    return event


def arm_live_site(rsp,armed,pc,kind,original,ttbr0):
    if str(pc) in armed or mem(rsp,pc,len(original))!=original:
        return False
    clock.command_ok(rsp,f'Z1,{pc:x},4')
    armed[str(pc)]={'kind':kind,'original':original.hex(),'ttbr0':ttbr0,'collisions':0}
    return True


def client_connect_site(rsp,armed,bias,ttbr0):
                                                                            
    got=mem(rsp,bias+0x1cba0,8)
    if not got:return False
    entry=int.from_bytes(got,'little')
    libc=checked_elf(clock.LIBC,clock.LIBC_SHA)
    if mem(rsp,entry,8)!=libc.at(0x5e200,8):return False
    return arm_live_site(rsp,armed,entry+0x150,'libc-connect-return',
                         libc.at(0x5e350,8),ttbr0)


def activate(rsp,state):
    elf=original_library()
    spec={}
    for bias in BIASES:
        for kind,offset in SITES.items():
            pc=bias+offset
            clock.command_ok(rsp,f'Z1,{pc:x},4')
            spec[str(pc)]={'kind':kind,'original':elf.at(offset,8).hex()}
    state['qnx_clock_bracket']={'armed':spec,'events':[],'verified_original_library':clock.LIBRARY_SHA,
        'scope':'Only between original VM3 ABOX cpu prepare entry/return; no guest writes'}


def deactivate(rsp,state):
    session=state.get('qnx_clock_bracket')
    if not session:return
    for pc in session['armed']:
        clock.command_ok(rsp,f'z1,{int(pc):x},4')
    session['armed']={}
    session['withdrawn']=True


def step(rsp,thread):
    reply=rsp.command('vCont;s:'+thread)
    if reply[:3] not in (b'S05',b'T05'):
        raise ValueError('Original cross-domain step failed: '+repr(reply))


def errno(rsp):
    regs,_=rsp.xml_registers()
    reg=regs.get('TPIDRRO_EL0')
    if not reg or reg['bits']!=64:return None
    value=rsp.command('p'+format(reg['number'],'x'))
    if not re.fullmatch(rb'[0-9a-fA-F]{16}',value):return None
    tls=int.from_bytes(bytes.fromhex(value.decode('ascii')),'little')
    pointer=mem(rsp,tls+16,8)
    if not pointer:return None
    got=mem(rsp,int.from_bytes(pointer,'little'),4)
    return int.from_bytes(got,'little',signed=True) if got else None


                                                                          
                                                                              
QNX_PAGE_TABLE_BANKS = ((0x90000000,0xa0000000), (0xe0000000,0xe3c00000))


def snapshot_server(qmp,identity):
    if not identity:return None
    mask=clock.MASK
    cache={}
    def read(address):
        if not any(a<=address and address+8<=b for a,b in QNX_PAGE_TABLE_BANKS):
            raise ValueError(f'Address outside observed QNX RAM banks: {address:#x}')
        if address not in cache:
            reply=qmp.execute('human-monitor-command',{'command-line':f'xp /1gx {address:#x}'})
            value=re.search(r':\s+0x([0-9a-fA-F]+)',reply)
            if not value:raise ValueError('Physical RAM read unavailable')
            cache[address]=int(value[1],16)
        return cache[address]
    results={}
    for label,va in identity['addresses'].items():
        walk=[]
        results[label]={'virtual':hex(va),'walk':walk}
        try:
            table=int(identity['ttbr0'],16)&mask
            for shift in (30,21,12):
                pa=table+8*((va>>shift)&511)
                pte=read(pa)
                walk.append([hex(pa),hex(pte)])
                if pte&1==0 or (shift==12 and pte&3!=3) or (shift==30 and pte&3==1):
                    raise ValueError('Invalid QNX stage-1 descriptor')
                if shift==12 or pte&3==1:
                    offset=(1<<shift)-1
                    physical=(pte&mask&~offset)|(va&offset)
                    results[label].update(physical=hex(physical),aligned_word=hex(read(physical&~7)))
                    break
                table=pte&mask
        except (ValueError,TypeError) as exc:
            results[label]['unverified']=str(exc)
    return results


def observe(rsp,state,cpus,qmp=None):
    session=state.get('qnx_clock_bracket')
    if not session or not session['armed']:return False
    sites=session['armed']
    for cpu in cpus:
        core=cpu.get('core',{})
        pc=int(core.get('pc','0'),16)
        row=sites.get(str(pc))
        if row is None:continue
        vmid=int(cpu['system'].get('VTTBR_EL2','0'),16)>>48
        clock.command_ok(rsp,'Hg'+cpu['thread'])
        ttbr0=cpu['system'].get('TTBR0_EL1')
        raw=mem(rsp,pc,8) if vmid==2 and (not row.get('ttbr0') or row['ttbr0']==ttbr0) else None
        event={'kind':row['kind'],'pc':hex(pc),'vmid':vmid,'thread':cpu['thread'],
               'original_bytes_verified':raw is not None and raw.hex()==row['original'],
               'ttbr0':ttbr0}
        if event['original_bytes_verified']:
            values={k:int(core[k],16) for k in ('x0','x1','x19','x20','x21','x22','x23','sp','x30')}
            result=values['x0']
            event.update(x0=hex(result), caller_lr=hex(values['x30']))
            if row['kind'] in ('name_open','MsgSend','libc-connect-return'):
                event['raw_return']=hex(result)
            kind=row['kind']
            if kind in ('name-open-call','name_open','MsgSend'):
                event.update(clock_id=values['x21']&RESULT_MASK,op=values['x22']&0xffff,
                             error=errno(rsp) if kind!='name-open-call' and result&RESULT_MASK==RESULT_MASK else None)
                if kind=='name-open-call':
                    event['connect_site_armed']=client_connect_site(rsp,sites,pc-SITES[kind],ttbr0)
            elif kind=='libc-connect-return':
                                                                           
                pathname=mem(rsp,values['x19'],32)
                event['pathname']=pathname.split(b'\0',1)[0].decode('ascii',errors='replace') if pathname else None
                event['error']=errno(rsp) if result&RESULT_MASK==RESULT_MASK else None
                if qmp is not None and event['error']==2:
                    event['server_mapping']=snapshot_server(qmp,state.get('qnx_clock_server_identity'))
                if state.get('qnx_clock_service',{}).get('armed',{}).pop(str(PROC_ABORT),None):
                    clock.command_ok(rsp,f'z1,{PROC_ABORT:x},4')
                    state['qnx_clock_service']['abort_watch_withdrawn_at_connect']=True
            elif kind=='io-connect':
                event.update(receive_id=values['x19']&RESULT_MASK,attachment=hex(values['x20']))
            else:
                                                                            
                op=mem(rsp,values['sp']+0xb8,2)
                clock_id=mem(rsp,values['sp']+0xd0,4)
                event.update(clock_id=int.from_bytes(clock_id,'little') if clock_id else None,
                             op=int.from_bytes(op,'little') if op else None)
        session['events'].append(ordered(state,event))
        if len(session['events'])>=48:
            deactivate(rsp,state)
            session['bounded']=True
        else:
            clock.command_ok(rsp,f'z1,{pc:x},4')
            step(rsp,cpu['thread'])
            clock.command_ok(rsp,f'Z1,{pc:x},4')
        return True
    return False


def arm_service(rsp,state):
    elf=original_library()
    armed={}
    for bias in BIASES:
        for kind,offset in SERVICE_SITES.items():
            pc=bias+offset
            clock.command_ok(rsp,f'Z1,{pc:x},4')
            armed[str(pc)]={'kind':kind,'original':elf.at(offset,8).hex(),'collisions':0}
    state['qnx_clock_service']={
        'armed':armed,'events':[],'collisions':[],
        'verified_original_library':clock.LIBRARY_SHA,
        'candidate_biases':[hex(bias) for bias in BIASES],
        'scope':'Bounded VM2 user and original process-report sites; an unhit site is not evidence of absence',
    }


def close_service(rsp,state):
    session=state.get('qnx_clock_service')
    if not session:return
    for pc in session['armed']:
        clock.command_ok(rsp,f'z1,{int(pc):x},4')
    session['unobserved_sites']=sorted(hex(int(pc)) for pc in session['armed'])
    session['armed']={}
    session['withdrawn']=True


def read_proc_report(rsp,core):
    from linux_panic_watch import cstring,mem as kernel_mem
    proc=int(core['x0'],16)
    siginfo=int(core['x1'],16)
    pointer=kernel_mem(rsp,proc+0x1a0,8)
    if not pointer:return None
    path=cstring(rsp,int.from_bytes(pointer,'little'))
    if not path or path.rsplit('/',1)[-1]!='cal_init':return None
    pid=kernel_mem(rsp,proc+8,4)
    raw=kernel_mem(rsp,siginfo,32)
    if not raw:return None
    return {'process_path':path,'pid':int.from_bytes(pid,'little') if pid else None,
            'signo':int.from_bytes(raw[:4],'little',signed=True),
            'signal_code':int.from_bytes(raw[4:8],'little',signed=True),
            'signal_info_hex':raw.hex(),'siginfo_at_0x18':hex(int.from_bytes(raw[24:32],'little'))}


def observe_service(rsp,state,cpus,qmp=None):
    session=state.get('qnx_clock_service')
    if not session:return False
    armed=session['armed']
    for cpu in cpus:
        core=cpu.get('core',{})
        pc=int(core.get('pc','0'),16)
        key=str(pc)
        row=armed.get(key)
        if row is None:continue
        system=cpu['system']
        vmid=int(system.get('VTTBR_EL2','0'),16)>>48
        clock.command_ok(rsp,'Hg'+cpu['thread'])
        ttbr0=system.get('TTBR0_EL1')
        if row['kind'] in ('proc-report','proc-abort'):
            from linux_panic_watch import mem as kernel_mem
            raw=kernel_mem(rsp,pc,len(row['original'])//2) if vmid==2 else None
        else:
            raw=mem(rsp,pc,8) if vmid==2 and (not row.get('ttbr0') or row['ttbr0']==ttbr0) else None
        clock.command_ok(rsp,f'z1,{pc:x},4')
        if raw is None or raw.hex()!=row['original']:
            row['collisions']+=1
            exhausted=row['collisions']>=MAX_SITE_COLLISIONS
            session['collisions'].append({'pc':hex(pc),'vmid':vmid,'thread':cpu['thread'],
                                          'site_withdrawn':exhausted})
            if exhausted:
                del armed[key]
            else:
                step(rsp,cpu['thread'])
                clock.command_ok(rsp,f'Z1,{pc:x},4')
            return True
        kind=row['kind']
        x0=int(core['x0'],16)
        event={'kind':kind,'pc':hex(pc),'thread':cpu['thread'],'vmid':vmid,
               'ttbr0':ttbr0,'original_bytes_verified':True,'x0':hex(x0),'lr':core['x30']}
        if kind=='proc-abort':
            hits=session.get('abort_hits',0)+1
            session['abort_hits']=hits
            if ttbr0==row['server_ttbr0']:
                event.update({k:system.get(k) for k in ('ELR_EL1','ESR_EL1','FAR_EL1','SPSR_EL1')})
                session['events'].append(ordered(state,event))
            if hits>=MAX_PROC_ABORT_HITS:
                del armed[key]
                session['abort_watch_bounded']=True
            else:
                step(rsp,cpu['thread'])
                clock.command_ok(rsp,f'Z1,{pc:x},4')
            return True
        if kind=='proc-report':
            details=read_proc_report(rsp,core)
            if not details:
                misses=session.get('other_proc_reports',0)+1
                session['other_proc_reports']=misses
                if misses>=MAX_OTHER_PROC_REPORTS:
                    del armed[key]
                    session['proc_report_observation_bounded']=True
                else:
                    step(rsp,cpu['thread'])
                    clock.command_ok(rsp,f'Z1,{pc:x},4')
                return True
            event.update(details)
        if kind=='cal-clock-thread-entry':
                                                                             
                                                                                
            lr=int(core['x30'],16)
            image=checked_elf(CAL_INIT,CAL_INIT_SHA)
            event['cal_init_caller_verified']=mem(rsp,lr-4,16)==image.at(0xc54,16)
            if event['cal_init_caller_verified']:
                event['cal_if_return_armed']=arm_live_site(rsp,armed,lr+8,'cal-if-return',
                                                           image.at(0xc60,8),ttbr0)
        if kind=='cal-if-return':
            event['cal_if_result']=x0 & RESULT_MASK
        if kind=='pthread-create-return':
            event['pthread_error']=x0 & RESULT_MASK
        if kind in ('name-attach-return','receive-failure'):
            failed=x0==0 if kind=='name-attach-return' else x0 & RESULT_MASK==RESULT_MASK
            event.update(failed=failed,error=errno(rsp) if failed else None)
        if kind=='name-attach-return' and x0:
                                                                            
            channel=mem(rsp,x0+8,4)
            event['channel_id']=int.from_bytes(channel,'little',signed=True) if channel else None
            from linux_panic_watch import mem as kernel_mem
            if str(PROC_REPORT) not in armed and kernel_mem(rsp,PROC_REPORT,len(PROC_REPORT_BYTES))==PROC_REPORT_BYTES:
                clock.command_ok(rsp,f'Z1,{PROC_REPORT:x},4')
                armed[str(PROC_REPORT)]={'kind':'proc-report','original':PROC_REPORT_BYTES.hex(),'collisions':0}
                event['proc_report_armed']=True
            if (ttbr0 and str(PROC_ABORT) not in armed and
                    kernel_mem(rsp,PROC_ABORT,len(PROC_ABORT_BYTES))==PROC_ABORT_BYTES):
                clock.command_ok(rsp,f'Z1,{PROC_ABORT:x},4')
                armed[str(PROC_ABORT)]={'kind':'proc-abort','original':PROC_ABORT_BYTES.hex(),
                                        'collisions':0,'server_ttbr0':ttbr0}
                event['proc_abort_armed']=True
                                                                  
            got=mem(rsp,pc-SERVICE_SITES['name-attach-return']+0x1cbe0,8)
            if got and ttbr0:
                entry=int.from_bytes(got,'little')
                libc=checked_elf(clock.LIBC,clock.LIBC_SHA)
                if mem(rsp,entry,8)==libc.at(0x467b8,8):
                    bias=entry-0x467b8
                    for off in (0x40140,0x3f8c0,0x3f8d0):
                        arm_live_site(rsp,armed,bias+off,'proc-libc-exit',libc.at(off,8),ttbr0)
            tcr=int(system.get('TCR_EL1','0'),16)
            if qmp is not None and ttbr0 and tcr&63==25 and (tcr>>14)&3==0:
                identity={'ttbr0':ttbr0,'addresses':{'worker':pc,'attachment':x0,'channel':x0+8}}
                state['qnx_clock_server_identity']=identity
                event['server_mapping']=snapshot_server(qmp,identity)
        if kind in ('receive-entry','receive-failure','name-detach-call'):
            event['attachment']=core.get('x20')
        session['events'].append(ordered(state,event))
        del armed[key]
        return True
    return False
