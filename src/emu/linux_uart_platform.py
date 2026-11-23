
import hashlib
import struct
import re
import time

import original_8704_vm as vm
from vlm_platform import Elf, factory_file, original, parse_dtb

PROFILE='virtual-linux-usi-uart-v1'
PORTS=((3,0x10360000,0x10221018,351),(4,0x10380000,0x10221020,353),(8,0x10940000,0x10821010,362))
PROPS=('selector','control','option','selector-reads','selector-writes',
       'config-reads','config-writes','software-resets','rejected-accesses')
UINTM_WIDE=('uintm-wide-reads','uintm-wide-writes','uintm-wide-rejected')
UINTM_WIDE_PC=0xffffffc01049da1c
UINTM_CONSUMER_SHA='d5ed1abfeb2dc106bfa3d5e4e1d81acaf61cb214b470fd515e2d97a945238150'
VLM_FOCUS=0x80196038
VLM_RAM=0x80000000
VLM_RX_CAPTURE=0x800000
VLM_RX_TABLE=0x80196040
VLM_VCONS=0x801960f8
VLM_VSUART_RX=0x800c0fa0
VLM_RX_ABI=(
    ('_cons_in_ring_putchar',0x800aa934,0x80,'4edd79ecb22f70bf749127e0a847de6091b8cf8bb758fdcd938fbee811769b19'),
    ('_cons_in_vmid.part.2',0x800aabe4,0x2c,'6184b3e33a5908f0425bb3e1281ad6dafaafc91318ddf7fa2bc11f68bd48fba5'),
    ('nk_cons_poll',0x800ab4f8,0x1cc,'ff8289c446929ad6b6f4f7da905f5e61a65518fd1f5a1cafae0d2a6c276927ad'),
    ('_vcons_tx_handler',0x800ae208,0x68,'0acba4fb931884d6b208916768ee1215d7b1263712a0a1b47c60f54b9d8695bb'),
    ('vcons_create',0x800ae3a0,0x190,'22f99014a8036f399c0bed6dfb3fc8620a9fa7826cb748569f400a9aae13d340'),
    ('vfifo_data_put',0x800ade90,0xf4,'8e667110bf2aa8cc7ea5bd2e50e590ee16b14378753681af32e5e320aec5cb16'),
    ('vfifo_data_get',0x800adf84,0xf8,'7337e06815af2fb746285136b39a223adfb74d8be223cf0914e1b932ede5e1be'),
    ('vfifo_irq_connect',0x800adcf0,0xc,'79131688174590e14f3e92081449486c4a758cd0608cb0620b82b3810659daed'),
    ('_vsuart_rx_handler',VLM_VSUART_RX,0xf0,'28cd12737f7d3ef0ea729d54d1636f97690ea040e9c787a00ac53d66cbe41c56'),
    ('_vsuart_load_urxh',0x800c1dc0,0xac,'8bfa57de6964b8fb7787bc265ecb50a6a76b2fff65a3b430163f321a111f21d8'),
)

def policy(profile,native_linux):
    if profile is None:
        return None
    if profile!=PROFILE or not native_linux:
        raise ValueError('Linux USI UART requires original native VM3 DT and HCB')
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    linux=parse_dtb(dt.read_bytes())
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    with factory_file('ivi/Image').open('rb') as image:
        image.seek(0xffffffc01049d9d0-0xffffffc010080000)
        if hashlib.sha256(image.read(0xc4)).hexdigest()!=UINTM_CONSUMER_SHA:
            raise ValueError('Original VM3 64-bit UART UINTM RMW consumer changed')
    rows=[]
    for index,base,selector,spi in PORTS:
        sysbase=selector & ~0xff
        syscon=linux[f'/syscon@{sysbase:08x}']
        if syscon['reg']!=struct.pack('>QI',sysbase,0x100):
            raise ValueError('Original UART selector aperture changed')
        name=f'/uart@{base:08X}0'
        node=linux[name]
        if (node['compatible']!=b'samsung,exynos-uart\0' or node['status']!=b'okay\0' or
            node['reg']!=struct.pack('>QI',base,0x100) or node['interrupts']!=struct.pack('>III',0,spi,4) or
            node['samsung,usi-phandle']!=syscon['phandle'] or
            node['samsung,usi-offset']!=struct.pack('>I',selector-sysbase) or
            node['samsung,fifo-size']!=struct.pack('>I',64) or 'samsung,usi-serial-v2' not in node):
            raise ValueError(f'Original Linux USI{index:02} UART changed')
        prefix=f'/vplatform/vl,usi{index:02}/vl,usi{index:02}'
        for vmid in (2,3,4):
            pos=raw.index(f'VDTVM{vmid}\0\0'.encode())+8
            tree=parse_dtb(raw[pos:pos+struct.unpack_from('>I',raw,pos+4)[0]])
            mem,irq=(tree[prefix+suffix] for suffix in ('@mem','@irq'))
            if (mem['reg']!=struct.pack('>QIQI',selector,4,base,0x1000) or
                irq['interrupts']!=struct.pack('>III',0,spi,0) or
                ('vl,disabled' in mem)!=(vmid!=3) or ('vl,disabled' in irq)!=(vmid!=3)):
                raise ValueError(f'Original VM{vmid} USI{index:02} UART grant changed')
        rows.append(dict(index=index,base=base,selector=selector,spi=spi,fifo=64,
                         uart_config=base+0xc4,linux_node=name))
    return dict(profile=profile,ports=rows,linux_dt_sha256=vm.sha(dt),
                uintm_usi08=dict(address=0x10940038, read_pc=UINTM_WIDE_PC,
                                 write_pc=UINTM_WIDE_PC+8, bytes=8, adjacent_lane='reserved zero'),
                uintm_wide=[{'address':base+0x38,'read_pc':UINTM_WIDE_PC,
                             'write_pc':UINTM_WIDE_PC+8,'bytes':8,'adjacent_lane':'reserved zero'}
                            for _,base,_,_ in PORTS],
                hcb_sha256=vm.sha(hcb),scope='Native VM3 real virtual UART FIFO and GIC, no shell acceptance')

def runtime_state(qmp):
    result={}
    for i,_,_,_ in PORTS:
        path=f'/machine/exynos-usi{i:02}'
        props=PROPS
        available={row['name'] for row in qmp.execute('qom-list',dict(path=path))}
        props+=tuple(key for key in UINTM_WIDE if key in available)
        result[str(i)]={k:qmp.execute('qom-get',dict(path=path,property=k)) for k in props}
    return result



def native_console_arguments(port, output, lk_slot):

    if not 1024<=port<=65535:
        raise ValueError('Native Linux console TCP port outside 1024..65535')
                                                                       
                                                                
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    linux=parse_dtb(dt.read_bytes())
    node=linux['/uart@103000000']
    bootargs=linux['/chosen']['bootargs'].rstrip(bytes(1)).split()
    if (node.get('reg')!=struct.pack('>QI',0x10300000,0x100)
            or node.get('status')!=b'okay'+bytes(1)
            or b'console=ttySAC0,115200n8' not in bootargs):
        raise ValueError('Original VM3 UART0 console binding changed')
    raw=original('hcb/hcb.img').read_bytes()
    pos=raw.index(b'VDTHYP\0\0')+8
    hyp=parse_dtb(raw[pos:pos+struct.unpack_from('>I',raw,pos+4)[0]])
    focus={}
    for name,vmid in (('qnx',2),('linux',3),('android',4)):
        config=hyp[f'/vlm/vm@{vmid}/console']
        keys=config.get('focus',b'').split(b'\0')
        if config.get('prefix')!=b'enable\0' or keys[-1]!=b'' or not all(keys[:-1]):
            raise ValueError(f'Original VM{vmid} vLM console contract changed')
        sequences=[key.hex() for key in keys[:-1]]
        if not sequences or set(sequences).intersection(k for row in focus.values() for k in row['keys_hex']):
            raise ValueError('Missing or ambiguous original vLM focus sequence')
        focus[name]=dict(vmid=vmid,keys_hex=sequences)
    hyp_image=original('hv.img').read_bytes()
    elf=Elf(original('hypervisor').read_bytes())
    names={row['name']:row['address'] for row in elf.symbols}
    writer=names['_cons_in_focus_set']
    offset=writer-0x80080000
    if names['_cons_in_focus_vprop_value']!=VLM_FOCUS or elf.at(writer,0x4c)!=hyp_image[offset:offset+0x4c]:
        raise ValueError('Original console focus producer differs from the loaded vLM')
    path=output/'exynos-uart.bin'
    escaped=str(path).replace(',',',,')
    args=['-chardev',f'socket,id=oem-vlm-console,host=127.0.0.1,port={port},server=on,wait=off,logfile={escaped}',
          '-serial','chardev:oem-vlm-console']
    return args,dict(source='vlm-console-mux',serial_index=1,terminal='ttySAC0',physical=0x10300000,
                     host='127.0.0.1',port=port,lk_slot=lk_slot,raw_tx_log=str(path),
                     hcb_sha256=vm.sha(original('hcb/hcb.img')),focus=focus,focus_address=VLM_FOCUS,
                     interactive_shell_verified=False)


def publish_native_focus(qmp, output, running):
    data=qmp.execute('human-monitor-command',{'command-line':f'xp /2bx {VLM_FOCUS:#x}'}) if running else ''
    match=re.search(r':\s+0x(3[0-4])\s+0x(00|72)\b',data)
                                                              
    focus=(int(match[1],16)-ord('0')) if match else None
    vm.save(output/'vlm-console-focus.json',dict(source='original-vlm-focus',running=running,
        vmid=focus,read_only=not match or match[2]!='00',host_monotonic=time.monotonic()))


def native_rx_contract():

    image=original('hv.img').read_bytes()
    elf=Elf(original('hypervisor').read_bytes())
    symbols={r['name']:(r['address'],r['size']) for r in elf.symbols}
    if symbols['_cons_in_vms']!=(VLM_RX_TABLE,8) or symbols['_vcons']!=(VLM_VCONS,8):
        raise ValueError('Original vLM RX globals changed')
    for name,address,size,sha in VLM_RX_ABI:
        offset=address-0x80080000
        body=elf.at(address,size)
        if (symbols[name]!=(address,size) or image[offset:offset+size]!=body or
                hashlib.sha256(body).hexdigest()!=sha):
            raise ValueError('Original vLM RX consumer changed: '+name)
    raw=original('hcb/hcb.img').read_bytes()
    def tree(tag):
        off=raw.index(tag+b'\0\0')+8
        return parse_dtb(raw[off:off+struct.unpack_from('>I',raw,off+4)[0]])
    hyp=tree(b'VDTHYP')
    if hyp['/vlm/hyp/hyp-memory']['reg']!=struct.pack('>QQ',VLM_RAM,0x8000000):
        raise ValueError('Original vLM hypervisor-only aperture changed')
    for tag in (b'VDTVM3',b'VDTVM4'):
        uart=tree(tag)['/uart@103000000']
        if uart['vl,vfifo']!=b'console\0' or uart['interrupts']!=struct.pack('>III',0,345,0):
            raise ValueError('Original vLM guest VFIFO changed')


def native_rx_snapshot(raw):

    if not 0x200000<=len(raw)<=VLM_RX_CAPTURE:
        raise ValueError('Original vLM RAM snapshot outside bounded RX capture')

    def get(fmt, ptr):
        offset=ptr-VLM_RAM
        if not 0<=offset<=len(raw)-struct.calcsize(fmt):
            raise ValueError('vLM RX pointer outside captured RAM')
        return struct.unpack_from(fmt,raw,offset)[0]

    def recent_cr(count,read):
        last=min(count,8)
        return sum(read(i)==13 for i in range(count-last,count)),last

    result={'timestamp_monotonic':time.monotonic(),'vmids':{},'scope':'Original HYP ring and virtual FIFO counters, not guest tty or shell proof'}
    table=get('<Q',VLM_RX_TABLE)
    if not table:
        return result
    rows=result['vmids']
    for vmid in (2,3,4):
        try:
            ring=get('<Q',table+8*vmid)
            head,tail=(get('<I',ring+i) for i in (8,12))
            pending=(tail-head)&0xffffffff
            if pending>1024:
                raise ValueError('Original RX ring counter span changed')
            cr,n=recent_cr(tail,lambda seq:get('<B',ring+0x10+(seq&0x3ff)))
            rows[str(vmid)]=dict(ring=dict(produced=tail,consumed=head,pending=pending,
                                          recent_samples=n,recent_cr=cr),vfifos=[])
        except ValueError as exc:
            rows[str(vmid)]=dict(error=str(exc),vfifos=[])

    current=get('<Q',VLM_VCONS)
    visited=set()
    try:
        while current:
            vmid=get('<I',current)
            other=get('<Q',current+0x48)
            link=current
            while link:
                if link in visited or len(visited)>=24 or get('<I',link)!=vmid:
                    raise ValueError('Unexpected original vcons list')
                visited.add(link)
                vf=get('<Q',link+0x10)
                if vf and str(vmid) in rows:
                    buf,peer=(get('<Q',vf+i) for i in (0,8))
                    tx,rx=(get('<I',ptr+0x14) for ptr in (vf,peer))
                    cap,mask=(get('<I',buf+i) for i in (0x20,0x24))
                    pending=(tx-rx)&0xffffffff
                    if not 0<cap<=4096 or cap&(cap-1) or mask!=cap-1 or pending>cap:
                        raise ValueError('Unexpected original console VFIFO capacity or counters')
                    cr,n=recent_cr(tx,lambda seq:get('<Q',buf+0xd0+(seq&mask)*8))
                    summary=dict(active=bool(get('<I',link+4)),
                        produced=tx,consumed=rx,pending=pending,capacity=cap,
                        recent_samples=n,recent_cr=cr,rx_threshold=get('<I',peer+0x18))
                    if get('<Q',peer+0x28)==VLM_VSUART_RX:
                        uart=get('<Q',peer+0x30)
                        if get('<Q',uart+0x100)!=peer:
                            raise ValueError('Original virtual UART RX channel backpointer changed')
                        summary['virtual_uart']=dict(irq_id=get('<I',uart+0x10),
                            rx_mode=get('<I',uart+0x18)&3,
                            rx_pending=bool(get('<I',uart+0x48)&1),
                            rx_masked=bool(get('<I',uart+0x4c)&1),
                            deferred_events=get('<I',uart+0x134)&6)
                    rows[str(vmid)]['vfifos'].append(summary)
                link=get('<Q',link+0x50)
            current=other
    except ValueError as exc:
        result['vfifo_partial']=str(exc)
    return result
