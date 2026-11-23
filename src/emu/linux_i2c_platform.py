
import argparse
import hashlib
from pathlib import Path
import struct
import json
import os
import subprocess
import time
import re
import original_8704_vm as vm
import vlm_platform as platform
import sysreg_platform as spi
WATCH_PC=0xffffffc010686d44


def install_clock_watch(rsp):

    contract=original_contract()
    if rsp.command(f'Z1,{WATCH_PC:x},4')!=b'OK':raise ValueError('Cannot arm clock preparation observer')
    return {'pc':hex(WATCH_PC),'original_kernel_sha256':contract['kernel_sha256'],'memory_modified':False,'hit':False}


def collect_clock_watch(rsp,qmp,output,kernel):
    output=Path(output);output.mkdir()
    cpus=qmp.execute('human-monitor-command',{'command-line':'info registers -a'})
    result={'pc':hex(WATCH_PC),'cpu_registers':cpus,'memory_modified':False,
        'hit':bool(re.search(r'PC=0*'+format(WATCH_PC,'x'),cpus,re.I))}
    vm.save(output/'cpu.json',result)
    if result['hit']:capture_clock_rpc(qmp,output/'rpc',kernel)
                                                                            
    reply=rsp.command(f'z1,{WATCH_PC:x},4')
    if reply[:1] in (b'S',b'T'):reply=rsp.receive()
    if reply!=b'OK':raise ValueError('Cannot withdraw clock preparation observer')
    return result


def inventory():
    raw=spi.DT.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=spi.DT_SHA:raise ValueError('Original Linux I2C DT identity mismatch')
    nodes=platform.parse_dtb(raw);ports=[]
    for name,props in nodes.items():
        compatible=props.get('compatible',b'')
        if b'hsi2c' not in compatible or props.get('status')!=b'okay\0':continue
        aliases=[key for key,value in nodes['/aliases'].items() if value.rstrip(b'\0').decode('ascii')==name]
        reg=struct.unpack('>3I',props['reg']);interrupt=struct.unpack('>3I',props['interrupts'])
        handle=props['samsung,usi-phandle'];parent=next(p for p in nodes.values() if p.get('phandle')==handle)
        base=struct.unpack('>3I',parent['reg'])[1];offset=struct.unpack('>I',props['samsung,usi-offset'])[0]
        children={n:{k:platform.render(v) for k,v in p.items()} for n,p in nodes.items() if n.startswith(name+'/')}
        ports.append({'node':name,'aliases':aliases,'base':reg[1],'bytes':reg[2],'selector':base+offset,
            'spi':interrupt[1],'properties':{k:platform.render(v) for k,v in props.items()},'children':children})
    return {'source':str(spi.DT),'sha256':spi.DT_SHA,'kernel_sha256':spi.KERNEL_SHA,'ports':ports,
        'factory_peer_implemented':False,'factory_images_modified':False,'silicon_timing_verified':False}


def native_layout():
    rows=inventory()['ports']
    for row in rows:
        aliases=[a for a in row['aliases'] if a.startswith('i2c') and a[3:].isdigit()]
        if len(aliases)!=1:raise ValueError('Require one original I2C alias')
        row['index']=int(aliases[0][3:])
    actual=[(r['index'],r['base'],r['selector'],r['spi']) for r in rows]
    if actual!=[(1,0x10310000,0x10221004,344),(5,0x10350000,0x10221014,348),
                (9,0x10390000,0x10221024,352),(19,0x10970000,0x1082101c,363)]:
        raise ValueError('Original Linux I2C topology changed')
    return rows


def profile_options(profile,board_adapter=True):
    if profile is None:return []
    if profile!='virtual-linux-i2c-v1' or not board_adapter:raise ValueError('Linux I2C needs explicit profile and native board')
    return ['-global','driver=exynos-auto9.i2c,property=virtual-linux-ports,value=1']


def original_contract():
    data=inventory();meta=json.loads(spi.SYMBOLS.read_text(encoding='utf-8'))
    image=Path(meta['analysis_elf']);original=vm.ROOT/'var/inputs/original/ivi/Image'
    if vm.sha(image)!='dac5ea52de7614757e608e28d8c08d77f8cdafd68986eb208c091a3169695189' or vm.sha(original)!=spi.KERNEL_SHA:
        raise ValueError('Original Linux I2C kernel identity mismatch')
    elf=platform.Elf(image.read_bytes())
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    code=elf.at(0xffffffc010686b20,12)
    ins=[(i.mnemonic,i.op_str) for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(code,0xffffffc010686b20)]
    if ins!=[('mov','w3, #4'),('mov','w2, #7'),('bl','#0xffffffc010575320')]:raise ValueError('Original I2C selector contract changed')
    data.update({'profile':'virtual-linux-i2c-v1','layout':native_layout(),'kernel':str(original),'analysis':str(image),
        'analysis_sha256':vm.sha(image),'selector_mask':7,'selector_value':4,'probe_code_hex':code.hex(),
        'linux_consumer':{'probe':'0xffffffc010686a88','init':'0xffffffc010685c28','reset':'0xffffffc010686908',
            'irq':'0xffffffc010685cc0','xfer':'0xffffffc0106870d8'},
        'assumptions':['Virtual22500ns/byte and cold zero fields, not physical SCL calibration',
            'Linux-only low4 idle1 inferred from original SDA wait, no silicon FSM claim',
            'RX final trailing bit6 at actual last-byte completion; physical trailing latency unknown',
            'FIFO readiness persists until actual RX bytes consumed, not tied to RUN',
            'No OEM IMU/audio endpoint attached; real bus address absence NACKs; QNX17 keeps legacy status semantics']})
    return data


def runtime_states(qmp):
    keys=('linux-profile','linux-port','selector','selector-reads','selector-writes','register-reads','register-writes',
        'rejected-accesses','transfers-started','transfers-completed','nacks','cancelled','software-resets','tx-bytes','rx-bytes',
        'last-target','last-length','last-read','targets-low64','targets-high64')
    return {str(r['index']):{k:qmp.execute('qom-get',{'path':f"/machine/exynos-i2c{r['index']}",'property':k}) for k in keys}
            for r in native_layout()}


def capture_clock_rpc(qmp,output,kernel):

    output=Path(output);output.mkdir()
    meta=json.loads(spi.SYMBOLS.read_text(encoding='utf-8'));lookup={s['name']:int(s['address'],16) for s in meta['symbols']}
    if kernel['kernel_sha256']!=spi.KERNEL_SHA:raise ValueError('Clock capture belongs to another kernel')
    base=kernel['physical'];text=lookup['_text'];reads=[]
    result={'source_kernel_sha256':spi.KERNEL_SHA,'memory_modified':False,'reads':reads,'verified':False,
        'scope':'Paused native guest RAM and real kernel PTEs only; no reply/return-value injection or peripheral readiness claim'}
    def physical(address):
        reply=qmp.execute('human-monitor-command',{'command-line':f'xp /1gx {address:#x}'})
        match=re.search(r':\s*(0x[0-9a-fA-F]+)',reply)
        if not match:raise ValueError('Cannot read actual physical clock state '+hex(address))
        value=int(match[1],16);reads.append({'physical':hex(address),'value':hex(value),'reply':reply});return value
    try:
                                                                             
                                                                           
                                                                           
        pgd=base+lookup['swapper_pg_dir']-text;tcr=int(meta['observed_tcr_el1'],16)
        def translate(address):return platform.walk_guest_4k(address,tcr,pgd,physical)
        def virtual(address):
            walk=translate(address);value=physical(int(walk['physical_target'],16));return value,walk
        links=0xffffffc0114093c0
        client=physical(base+links-text);result['client_global']={'linked_va':hex(links),'physical':hex(base+links-text),'pointer':hex(client)}
        result['original_swapper_pgd']=hex(pgd);result['original_observed_tcr']=hex(tcr)
        if not client:raise ValueError('Original clock RPC client pointer is zero')
        shared,walk=virtual(client+24);result['client_to_transport']={'field_va':hex(client+24),'shared_va':hex(shared),'walk':walk}
        if not shared:raise ValueError('Original clock RPC shared pointer is zero')
        data=shared+12;record=bytearray();walks=[]
        for offset in range(0,88,8):
            value,walk=virtual(data+offset);record+=struct.pack('<Q',value);walks.append(walk)
        p=output/'clock-rpc-data.bin';p.write_bytes(record)
        result.update({'verified':True,'data_va':hex(data),'data':str(p),'sha256':vm.sha(p),
            'original_layout':'do_vrpc_call writes id@12/op@16/value@20, request88 response52; vlx_clk_ctrl_call reads signed result@12',
            'word12_signed':struct.unpack_from('<i',record,12)[0],'operation_word16':struct.unpack_from('<I',record,16)[0],
            'value_word20':struct.unpack_from('<I',record,20)[0],'raw_hex':record.hex(),'data_walks':walks,
            'last_transaction_identity_verified':False})
    except (ValueError,RuntimeError,KeyError) as error:result['unverified']=str(error)
    vm.save(output/'result.json',result);return result


def check_model(output,executable,enabled=True):
    from ufs_platform import QTest
    qp,tp=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt,virtualization=on,gic-version=2,x-exynos-vlm=on',
        '-cpu','cortex-a57','-accel','qtest','-m','128','-display','none','-monitor','none','-nic','none',
        '-serial','null','-serial','null','-serial','null','-S',
        '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off',
        '-qtest-log',str(output/'qtest.log')]
    if enabled:command+=profile_options('virtual-linux-i2c-v1')+['-device','at24c-eeprom,bus=i2c1,address=0x50,rom-size=256']
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable),'fixture_only':True})
    env=os.environ.copy();env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];error=None;qmp=qt=None
    def check(name,actual,expected):
        checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
        if actual!=expected:raise ValueError('Linux I2C check failed: '+name)
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            until=time.monotonic()+10
            while time.monotonic()<until:
                if process.poll() is not None:raise RuntimeError('Linux I2C QEMU exited')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.05)
            if qmp is None:raise RuntimeError('Linux I2C QMP timeout')
            qmp.execute('cont');qt=QTest(tp);rd,wr=qt.read,qt.write
            def step(ns):qt.call(f'clock_step {ns}')
            def get(index,key):return qmp.execute('qom-get',{'path':f'/machine/exynos-i2c{index}','property':key})
            gic=next(r['name'] for r in qmp.execute('qom-list',{'path':'/machine/unattached'}) if r['type']=='child<arm_gic>')
            qt.call('irq_intercept_in /machine/unattached/'+gic)
            for r in native_layout():
                i,b,u=r['index'],r['base'],r['selector']
                check('Explicit profile '+str(i),get(i,'linux-profile'),int(enabled))
                check('Separate Linux variant '+str(i),get(i,'linux-port'),True)
                if not enabled:continue
                check('Cold selector '+str(i),rd(u),0)
                wr(u,4);wr(b+0xc4,0);wr(b,0x80000000);wr(b,0)
                wr(b,8);wr(b+8,0xffffff);wr(b+0x40,0x80000000);wr(b+4,0x80083)
                check('Declared Linux virtual idle1 '+str(i),rd(b+0x50),1)
                                                                              
                wr(b+0x20,0xf81);wr(b+0x70,0x51<<10);wr(b+0x44,0x80020001);wr(b,0x88)
                step(22499);check('No early or polling NACK '+str(i),get(i,'nacks'),0)
                start=len(qt.events);step(1)
                check('Absent peer actual NACK '+str(i),rd(b+0x24)&0xf00,0x400)
                check('Absent peer is not success '+str(i),get(i,'transfers-completed'),0)
                check('Actual native GIC route '+str(i),'IRQ raise '+str(r['spi']) in qt.events[start:],True)
                wr(b+0x24,0xffffffff);check('Actual W1C clears IRQ '+str(i),'IRQ lower '+str(r['spi']) in qt.events,True)
                check('NACK releases real bus to virtual idle '+str(i),rd(b+0x50),1)
            check('Existing QNX17 stays legacy status',rd(0x10950050),0)
            check('Existing QNX17 stays legacy variant',get(17,'linux-port'),False)
            if enabled:
                b,u=0x10310000,0x10221004;p=22500
                def init():
                    wr(b+0xc4,1);wr(b+0xc4,0);wr(u,4);wr(b,8)
                    wr(b+0x40,0x80000000);wr(b+8,0xffffff);wr(b+4,0x80083);wr(b+0x48,0)
                def run(n,reading=False,stop=True):
                    wr(b+0x70,0x50<<10);wr(b+0x44,0x80000000|(0x10000 if reading else 0)|(0x20000 if stop else 0)|n)
                    wr(b,0x48 if reading else 0x88)
                init();wr(b+0x20,0xf81)
                for byte in (0x20,0xa5,0x5a,0x99):wr(b+0x34,byte)
                run(4);step(p*5)
                check('Actual Linux EEPROM write',get(1,'transfers-completed'),1)
                check('Actual write bytes, not host RAM fill',get(1,'tx-bytes'),4)
                wr(b+0x24,0xffffffff);wr(b+0x34,0x20);run(1,stop=False);step(p*2)
                check('Repeated START retains actual bus',rd(b+0x50)&0x20000,0x20000)
                wr(b+0x24,0xffffffff);wr(b+0x20,0xfc2);run(3,reading=True);step(p*4)
                check('Linux final partial RX triggers real trailing',rd(b+0x24)&0x40,0x40)
                check('Final buffered RX remains ready after RUN clears',rd(b+0x24)&2,0)
                                                                          
                                                                            
                check('Actual final EEPROM bytes',[rd(b+0x38) for _ in range(3)],[0xa5,0x5a,0x99])
                wr(b+0x24,0xffffffff);check('Final W1C clears latched completion',rd(b+0x24),0)
                wr(b+4,0x10013);wr(b+0x34,0x20);run(1,stop=False);step(p*2);wr(b+0x24,0xffffffff)
                run(1,reading=True);step(p*2)
                check('One-byte threshold persists after true completion',rd(b+0x24)&2,2)
                wr(b+0x24,0xffffffff);check('W1C cannot invent FIFO consumption',rd(b+0x24)&2,2)
                check('Actual final byte',rd(b+0x38),0xa5)
                check('Consuming RX actually withdraws readiness',rd(b+0x24)&2,0)
                init();old=get(1,'transfers-started');wr(b+0x40,0);run(1)
                check('Linux AUTO mode required, not silent manual',get(1,'transfers-started'),old)
                init();wr(b+0x34,0x20);run(1);old=get(1,'cancelled');wr(b+0xc4,1);step(p*10)
                check('Actual reset cancels armed transaction',get(1,'cancelled'),old+1)
                check('Cancelled clock cannot synthesize completion',rd(b+0x24),0)
                vm.save(output/'runtime-state.json',runtime_states(qmp))
            else:
                tree=qmp.execute('human-monitor-command',{'command-line':'info mtree -f'})
                vm.save(output/'memory-tree.json',{'output':tree})
                for r in native_layout():check('Default new aperture absent '+str(r['index']),f"{r['base']:016x}-" in tree,False)
            qmp.execute('quit');process.wait(timeout=5)
        except Exception as exc:error=repr(exc)
        finally:
            if qt:vm.save(output/'qtest-transcript.json',{'calls':qt.transcript,'events':qt.events});qt.stream.close();qt.socket.close()
            if qmp:qmp.close()
            if process.poll() is None:process.terminate();process.wait(timeout=5)
    result={'passed':error is None and all(c['passed'] for c in checks),'error':error,'checks':checks,'qemu_exit_code':process.returncode,
        'factory_peer_implemented':False,'factory_system_boot_verified':False,'fixture_eeprom':enabled}
    vm.save(output/'result.json',result)
    if not result['passed']:raise RuntimeError('Linux I2C model failed; inspect '+str(output/'result.json'))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=('inventory','contract','check','strict-check'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--qemu',type=Path,default=vm.ROOT/'runtime/qemu-system-aarch64.exe')
    args=parser.parse_args();output=args.output.resolve()
    if output.exists() or not output.is_relative_to(vm.ROOT/'var'):raise ValueError('Require new scratch output')
    output.mkdir(parents=True)
    if args.operation=='inventory':result=inventory()
    elif args.operation=='contract':result=original_contract()
    else:result=check_model(output,args.qemu.resolve(),enabled=args.operation=='check')
    vm.save(output/'result.json',result);print(str(output/'result.json'))


if __name__=='__main__':main()
