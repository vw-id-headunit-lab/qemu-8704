




import hashlib
import json
import argparse
from pathlib import Path
import re
import struct
import original_8704_vm as vm
import vlm_platform as platform

LIBRARY=vm.ROOT/'var/inputs/support/归档/固件与启动研究/pscdc-loader--libexynos_clock.so.1'
LIBRARY_SHA='226354575762de4e8de315e0eca10c3fa2bbae6776efeb01aea281dd073e93cf'
BACKEND=LIBRARY.parents[2]/'bin/clk_ctrl_be'
BACKEND_SHA='d1a9963d3fe853889f0cedca19112f26924d93ea549f7ae23d9c0f954caa6f01'
MEMBER_OFFSET=0x3cc000
IFS_LOAD=0x91080000
ENTRY=0x7f28
RETURNS={'name_open':0x7f68,'MsgSend':0x7f90}
MASK=0xfffffffff000
LIBC=vm.ROOT/'var/inputs/support/归档/QNX显示与时钟对照/clock-rpc-native--libc.so.4'
LIBC_SHA='630db6147c3f4481a627efa2332bf6b74b1a6073a1c4bb0aaefc128a3931f6b3'


def contract():
    raw=LIBRARY.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=LIBRARY_SHA:raise ValueError('Original clock library identity mismatch')
    index=json.loads((vm.ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_text(encoding='utf-8'))
    member=next(r for r in index['entries'] if r['name']=='proc/boot/libexynos_clock.so.1')
    if member['absolute_offset']!=MEMBER_OFFSET or member['sha256']!=LIBRARY_SHA:raise ValueError('Original clock IFS member changed')
    elf=platform.Elf(raw)
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    instructions=[{'address':hex(i.address),'mnemonic':i.mnemonic,'operands':i.op_str}
                  for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(elf.at(ENTRY,0xb0),ENTRY)]
    actual={int(i['address'],16):(i['mnemonic'],i['operands']) for i in instructions}
    for address,expected in {0x7f60:('bl','#0x5de0'),0x7f68:('b.eq','#0x7fa8'),
            0x7f88:('bl','#0x5e40'),0x7f90:('b.eq','#0x7fa8'),0x7fa8:('mov','w0, #1')}.items():
        if actual.get(address)!=expected:raise ValueError('Original clock failure-branch contract changed')
    libc=LIBC.read_bytes()
    if hashlib.sha256(libc).hexdigest()!=LIBC_SHA:raise ValueError('Original libc identity mismatch')
    tls=platform.Elf(libc).at(0x81668,12)
    if tls.hex()!='60d03bd5000840f9c0035fd6':raise ValueError('Original QNX errno accessor changed')
    from cmu_sequence import DynamicCalImage
    backend=BACKEND.read_bytes()
    if hashlib.sha256(backend).hexdigest()!=BACKEND_SHA:raise ValueError('Original clock backend identity mismatch')
    backend_image=DynamicCalImage(backend)
    callbacks=[backend_image.pointer(0x1da40+8*i) for i in range(11)]
    if callbacks[:4]!=[0x4668,0x4408,0x45b8,0x4370]:raise ValueError('Original prepare/unprepare/enable/disable dispatch changed')
    return {'library':str(LIBRARY),'sha256':LIBRARY_SHA,'entry':hex(ENTRY),'instructions':instructions,
        'return_breakpoints':{k:hex(v) for k,v in RETURNS.items()},'memory_modified':False,
        'libc':{'path':str(LIBC),'sha256':LIBC_SHA,'errno_accessor':'0x81668','code_hex':tls.hex(),
            'semantics':'MRS TPIDRRO_EL0; LDR pointer [TLS+16]; RET'},
        'backend':{'path':str(BACKEND),'sha256':BACKEND_SHA,'callback_table':[hex(n) for n in callbacks],
            'prepare_callback':'0x4668','request_bytes':88,'response_bytes':52},
        'ifs_member_offset':hex(MEMBER_OFFSET),
        'failure_semantics':'Original exynos_vclk_enable returns +1 on name_open or MsgSend -1; not a hardware status code',
        'scope':'Read-only active QNX stage-1 maps; guest IPA equals board physical address checked by original code bytes, no claim about all processes'}


def enumerate_user_maps(tcr,ttbr,read_page,budget=128):

    if tcr&63!=25 or (tcr>>14)&3:raise ValueError('Need native 39-bit 4KiB QNX TTBR0')
    seen=set();leaves=[]
    def visit(table,shift,prefix):
        if (table,shift) in seen:raise ValueError('Cyclic/aliased QNX page-table tree')
        if len(seen)>=budget:raise ValueError('QNX table observation budget exhausted')
        seen.add((table,shift));raw=read_page(table)
        if len(raw)!=4096:raise ValueError('Truncated QNX page-table page')
        for index,(word,) in enumerate(struct.iter_unpack('<Q',raw)):
            if not word&1:continue
            virtual=prefix|(index<<shift)
            if shift==12 or not word&2:
                if shift==12 and not word&2:raise ValueError('Reserved last-level QNX descriptor')
                size=1<<shift;physical=word&MASK&~(size-1)
                leaves.append({'virtual':virtual,'physical':physical,'bytes':size,'descriptor':hex(word)})
            else:visit(word&MASK,shift-9,virtual)
    visit(ttbr&MASK,30,0)
    return leaves


def discover(rsp,qmp,output):

    output=Path(output);output.mkdir();source=contract();system=rsp.system_snapshot()
    vm.save(output/'cpu-system-registers.json',system)
    pages={};reads=[]
    def physical(address,size):
        if not any(base<=address and address+size<=base+length for base,length in banks):
            raise ValueError('Clock observer refuses physical reads outside original VM2 RAM')
        p=output/f'physical-{address:x}-{size:x}.bin'
        if not p.exists():
            qmp.execute('pmemsave',{'val':address,'size':size,'filename':str(p)})
            if p.stat().st_size!=size:raise ValueError('Incomplete physical clock observation')
            reads.append({'physical':hex(address),'bytes':size,'path':str(p),'sha256':vm.sha(p)})
        return p.read_bytes()
    def page(address):
        if address not in pages:pages[address]=physical(address,4096)
        return pages[address]
                                                                            
                                                                        
    hcb=platform.original('hcb/hcb.img').read_bytes();start=hcb.index(b'VDTHYP\0\0')+8
    nodes=platform.parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
    banks=list(struct.iter_unpack('>QQ',nodes['/vlm/vm@2/vm-memory']['reg']))
    if banks[1]!=(0xe0000000,0x3c00000):raise ValueError('Original QNX runtime bank changed')
    runtime=physical(*banks[1]);messages=[]
    for match in re.finditer(rb'[\x20-\x7e\r\n\t]{12,4096}',runtime):
        value=match.group()
        if re.search(rb'exynos_clock|cal_if|fvmap|cal-if|CAL Interface',value,re.I):
            messages.append({'physical':hex(banks[1][0]+match.start()),'text':value.decode('ascii')})
    vm.save(output/'runtime-string-candidates.json',{'matches':messages,'scope':'Actual paused QNX RAM strings; candidates may include copied formats, only formatted records establish log content'})
    elf=platform.Elf(LIBRARY.read_bytes());segment=next(s for s in elf.segments if s['type']==1 and s['virtual']<=ENTRY<s['virtual']+s['file_size'])
    expected_physical=IFS_LOAD+MEMBER_OFFSET+segment['offset']+ENTRY-segment['virtual']
    expected=elf.at(ENTRY,64);spaces=[];candidates=[];roots=set()
    for cpu in system['cpus']:
        values=cpu.get('system',{})
        if not isinstance(values.get('VTTBR_EL2'),str) or int(values['VTTBR_EL2'],16)>>48!=2:continue
        root=int(values['TTBR0_EL1'],16)&MASK
        if not root or root in roots:continue
        roots.add(root);row={'thread':cpu['thread'],'ttbr0':values['TTBR0_EL1'],'tcr':values['TCR_EL1']};spaces.append(row)
        try:
            leaves=enumerate_user_maps(int(values['TCR_EL1'],16),root,page);row['leaves']=leaves
            for leaf in leaves:
                if leaf['physical']<=expected_physical<leaf['physical']+leaf['bytes']:
                    actual=physical(expected_physical,64)
                    if actual!=expected:raise ValueError('Mapped IFS clock code does not equal original library')
                    address=leaf['virtual']+expected_physical-leaf['physical']
                    candidates.append({'thread':cpu['thread'],'entry_va':hex(address),'bias':address-ENTRY,
                        'physical':hex(expected_physical),'original_64_byte_match':True})
        except (ValueError,KeyError) as error:row['unverified']=str(error)
    biases=sorted(set(c['bias'] for c in candidates));targets=[]
    for bias in biases:
        for kind,offset in RETURNS.items():targets.append({'kind':kind,'pc':bias+offset})
    result={'contract':source,'spaces':spaces,'reads':reads,'candidates':candidates,'targets':targets,
        'memory_modified':False,'source_bound_active_maps_verified':bool(candidates),
        'all_qnx_processes_enumerated':False,'factory_system_boot_verified':False}
    vm.save(output/'result.json',result)
    return result


def command_ok(rsp,command):
    reply=rsp.command(command)
    if reply[:1] in (b'S',b'T'):reply=rsp.receive()
    if reply!=b'OK':raise ValueError('Clock observer GDB command failed: '+command+' '+repr(reply))


def arm(rsp,targets):
    for row in targets:command_ok(rsp,f"Z1,{row['pc']:x},4")


def withdraw(rsp,targets):
    for row in targets:command_ok(rsp,f"z1,{row['pc']:x},4")


def observe(rsp,qmp,output,targets):

    output=Path(output);output.mkdir()
    registers=qmp.execute('human-monitor-command',{'command-line':'info registers -a'})
    hits=[]
    for block in re.split(r'CPU#',registers)[1:]:
        match=re.search(r'PC=([0-9a-f]+)\s+X00=([0-9a-f]+)',block,re.I)
        if not match:continue
        pc,value=map(lambda x:int(x,16),match.groups())
        for row in targets:
            if pc!=row['pc']:continue
            fields={k:int(v,16) for k,v in re.findall(r'(X\d\d)=([0-9a-f]+)',block,re.I)}
            width=32 if row['kind']=='name_open' else 64
            hits.append({'kind':row['kind'],'cpu':block.splitlines()[0],'pc':hex(pc),'return_raw':hex(value),
                'failed':value&((1<<width)-1)==(1<<width)-1,'clock_id':fields.get('X21'),
                'request_kind':fields.get('X22'),'callsite_lr':hex(fields.get('X30',0))})
                                                                             
                                                                            
    if hits:
        command_ok(rsp,'Hg0')
        snapshot=rsp.system_snapshot()
        for hit in hits:
            candidates=[c for c in snapshot['cpus'] if int(c['core'].get('pc','0'),16)==int(hit['pc'],16)]
            if len(candidates)!=1:hit['errno_unverified']='Ambiguous stopped QNX thread';continue
            cpu=candidates[0];command_ok(rsp,'Hg'+cpu['thread'])
            inventory,_=rsp.xml_registers();reg=inventory.get('TPIDRRO_EL0')
            if not reg or reg['bits']!=64:hit['errno_unverified']='QNX TLS register unavailable';continue
            def read_virtual(address,size):
                reply=rsp.command(f'm{address:x},{size:x}')
                if not re.fullmatch(b'[0-9a-fA-F]{'+str(size*2).encode()+b'}',reply):raise ValueError('Cannot read original QNX TLS')
                return bytes.fromhex(reply.decode())
            reply=rsp.command('p'+format(reg['number'],'x'))
            if not re.fullmatch(b'[0-9a-fA-F]{16}',reply):raise ValueError('Cannot read original QNX TLS register')
            tls=int.from_bytes(bytes.fromhex(reply.decode()),'little')
            pointer=int.from_bytes(read_virtual(tls+16,8),'little')
            errno=int.from_bytes(read_virtual(pointer,4),'little',signed=True)
            code=read_virtual(int(hit['pc'],16),4)
            expected=platform.Elf(LIBRARY.read_bytes()).at(RETURNS[hit['kind']],4)
            if code!=expected:raise ValueError('Stopped QNX branch bytes differ from original library')
            hit.update({'thread':cpu['thread'],'tls':hex(tls),'errno_pointer':hex(pointer),'errno':errno,
                'branch_bytes_match':True,'errno_accessor_sha256':LIBC_SHA})
        vm.save(output/'cpu-system-registers.json',snapshot)
    result={'hits':hits,'cpu_registers':registers,'memory_modified':False}
    vm.save(output/'result.json',result);return result


def inspect_runtime(session,output):

    session=Path(session);output=Path(output)
    discovery=json.loads((session/'qnx-clock-discovery/result.json').read_text(encoding='utf-8'))
    bank=next(r for r in discovery['reads'] if r['physical']=='0xe0000000' and r['bytes']==0x3c00000)
    p=Path(bank['path'])
    if vm.sha(p)!=bank['sha256']:raise ValueError('Retained QNX runtime bank changed')
    raw=p.read_bytes();log=raw[0x10000:0x20000].split(b'\0',1)[0]
    if not log.startswith(b'MMFLAGS=19\r\n'):raise ValueError('Observed QNX boot log shape changed')
    text=log.decode('utf-8',errors='backslashreplace');target=output/'qnx-boot-live.txt';target.write_text(text,encoding='utf-8',newline='')
    index=json.loads((vm.ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_text(encoding='utf-8'))
    mains=[]
    for space in discovery['spaces']:
        leaf=next((r for r in space.get('leaves',[]) if r['virtual']<=0x1000000<r['virtual']+r['bytes']),None)
        if not leaf:continue
        physical=leaf['physical']+0x1000000-leaf['virtual'];offset=physical-IFS_LOAD
        members=[r['name'] for r in index['entries'] if r.get('kind')=='reg' and 'absolute_offset' in r and r['absolute_offset']<=offset<r['absolute_offset']+r['file_size']]
        mains.append({'thread':space['thread'],'ttbr0':space['ttbr0'],'virtual_probe':'0x1000000',
            'mapped_page_physical':hex(physical),'ifs_members':members,'main_executable_identified':False})
    return {'source':bank,'formatted_boot_log':str(target),'formatted_log_bytes':len(log),'fixed_address_mapping_candidates':mains,
        'records':[line for line in text.splitlines() if re.search(r'cal-if|cal_if|fvmap|exynos_clock|vclk_initialize',line,re.I)],
        'scope':'Retained actual native formatted log and active mappings, not a complete QNX process inventory'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=('contract','inspect-runtime'))
    parser.add_argument('--session',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    if output.exists() or not output.is_relative_to(vm.ROOT/'var'):raise ValueError('Require new scratch output')
    if args.operation=='inspect-runtime' and not args.session:raise ValueError('Require retained native session')
    output.mkdir(parents=True)
    result=contract() if args.operation=='contract' else inspect_runtime(args.session.resolve(),output)
    vm.save(output/'result.json',result);print(output/'result.json')


if __name__=='__main__':main()
