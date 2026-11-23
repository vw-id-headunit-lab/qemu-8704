
import json
from pathlib import Path
import re
import struct

import original_8704_vm as vm
from vlm_platform import Elf, walk_guest_4k

TARGETS={'die':0xffffffc0100912c0,'panic':0xffffffc010a78904}

def arm(rsp, symbols, kernel):
    meta=json.loads((Path(symbols)/'linux-symbols.json').read_text(encoding='utf-8'))
    native=Path(kernel['kernel'])
    elf=Elf(Path(meta['analysis_elf']).read_bytes())
    if vm.sha(native)!=kernel['kernel_sha256']:
        raise ValueError('Linux crash observer needs the verified original Linux Image')
    code={}
    with native.open('rb') as f:
        for name,pc in TARGETS.items():
            f.seek(pc-0xffffffc010080000)
            raw=f.read(16)
            if len(raw)!=16 or raw!=elf.at(pc,16):
                raise ValueError(f'Linux breakpoint instruction bytes mismatch: {name}')
            if rsp.command(f'Z1,{pc:x},4')!=b'OK':
                raise ValueError(f'Cannot arm original Linux {name}')
            code[name]=raw.hex()
    return dict(original_kernel_sha256=kernel['kernel_sha256'], memory_modified=False, armed=list(TARGETS), code=code, events=[])

def withdraw(rsp, name):
    reply=rsp.command(f'z1,{TARGETS[name]:x},4')
    if reply[:1] in (b'S',b'T'):
        reply=rsp.receive()
    if reply!=b'OK':
        raise ValueError('Linux crash observer cleanup failed: '+name)

def mem(rsp, address, size):
    if not (0xffffff8000000000 <= address < 1<<64):
        return None
    reply=rsp.command(f'm{address:x},{size:x}').decode('ascii')
    if len(reply)!=size*2 or not re.fullmatch('[0-9a-fA-F]+',reply):
        return None
    return bytes.fromhex(reply)

def cstring(rsp, address):
    out=bytearray()
    for i in range(0,192,16):
        part=mem(rsp,address+i,16)
        if part is None:
            break
        word, sep, _=part.partition(b'\0')
        out.extend(word)
        if sep:
            break
    return bytes(out).decode('utf-8','backslashreplace') if out else None

def fault_walk(qmp, values):

    def phys(address):
        reply=qmp.execute('human-monitor-command',{'command-line':f'xp /1gx {address:#x}'})
        match=re.search(r':\s+0x([0-9a-fA-F]+)',reply)
        if not match:
            raise ValueError('Unreadable page-table entry at '+hex(address))
        return int(match[1],16)
    try:
        far=int(values['FAR_EL1'],16)
        if far < 1<<63:
            raise ValueError('Not a kernel TTBR1 virtual address')
        return dict(virtual=hex(far),**walk_guest_4k(far,int(values['TCR_EL1'],16),
            int(values['TTBR1_EL1'],16),phys))
    except (ValueError,KeyError,TypeError) as exc:
        return dict(unverified=str(exc))

def observe(rsp, qmp, state, output):
    info=qmp.execute('human-monitor-command',{'command-line':'info registers -a'})
    candidates=[n for n in state['armed'] if re.search(r'PC=0*'+format(TARGETS[n],'x'),info,re.I)]
    if not candidates:
        return False
    cpus=rsp.system_snapshot()['cpus']
    for cpu in cpus:
        pc=cpu.get('core',{}).get('pc')
        name=next((n for n in candidates if pc==hex(TARGETS[n])),None)
        if not name:
            continue
        if rsp.command('Hg'+cpu['thread'])!=b'OK':
            raise ValueError('Cannot select stopped Linux thread')
        actual=mem(rsp,TARGETS[name],16)
        if actual is None or actual.hex()!=state['code'][name]:
            continue
        regs={k:int(v,16) for k,v in cpu['core'].items()}
        event=dict(name=name,thread=cpu['thread'],registers=cpu['core'],system=cpu['system'],
                   verified_original_instructions=True,message=cstring(rsp,regs['x0']))
        if name=='die':
            event['fault_walk']=fault_walk(qmp,cpu['system'])
            saved=mem(rsp,regs['x1'],34*8)
            if saved is not None:
                words=struct.unpack('<34Q',saved)
                event['fault_frame']=dict(x=[hex(x) for x in words[:31]],sp=hex(words[31]),pc=hex(words[32]),pstate=hex(words[33]))
        elif event['message'] and '%s' in event['message']:
            event['first_string_argument']=cstring(rsp,regs['x1'])
        state['events'].append(event)
        withdraw(rsp,name)
        state['armed'].remove(name)
        vm.save(Path(output)/'linux-panic-watch.json',state)
        return True
    return False

