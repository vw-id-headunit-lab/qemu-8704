
import argparse
import hashlib
import json
from pathlib import Path
import struct
import base64
import os
import socket
import subprocess
import time
import urllib.request
import original_8704_vm as vm
import vlm_platform as platform

DT=vm.ROOT/'var/inputs/analysis/cpu/ivi/icas3cn-gp_b0_zero.dtb'
DT_SHA='e46987bd74fbf64595358089e054aa11a8e6bcf21d5ecdf5932b69b386b7721e'
SYMBOLS=vm.ROOT/'var/inputs/symbols/linux-symbols.json'
KERNEL_SHA='c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109'
QOM='/machine/exynos-spi1'
KEYS=('virtual-profile','fifo-bytes','word-period-ns','selector','selector-reads','selector-writes','active',
    'register-reads','register-writes','rejected-accesses','control','mode','tx-count','rx-count',
    'pending','software-resets','words-shifted','loopback-words','wire-words','irq-level',
    'dma-tx-stop','dma-rx-stop','last-offset','last-value')
FIFO_ERRORS=('tx-overruns','rx-overruns','rx-underruns')
MCU_SPI_PATH='/machine/exynos-mcu-spi6'
MCU_SPI_KEYS=('valid-reset-frames','completed-responses','confirmed-start-frames',
              'rejected-startup-frames','request-low','selected-version')


def mcu_spi6_contract():

    mcu=vm.ROOT/'var/inputs/packages/ICAS3CN_MCU.MCU_2025121-0317/31/mcu_image.bin'
    digest='9ded0fc2ca4d187add68ea21a1a02f7290c31729d40a83ea57d4d03844f4bc7a'
    if vm.sha(mcu)!=digest:
        raise ValueError('SPI6 MCU source identity changed')
    spans=(
        (0x103b0ca,0xa4,'057d555a11ff6d3d7b1ec9d616b62fa5f791e1a5b375dc4c00bfc90e87caec65'),
        (0x103b56e,0xdc,'d2a50eb63059b4b8011a2cc86eb077884fa90fd6c57538c47d5ce86af1ef6968'),
        (0x103b64a,0x76,'1e8345c2feb37d5ead0568e4f16d03b9c362252059044bf8b4ab535a8ab5ba7f'),
        (0x103b6c0,0xf6,'0291c4759ffedb7e3e4e9b03362e8b14c60be0e30b0584d8f20d6774f3f899bb'),
        (0x103b7b6,0x212,'56a88fe8d37525e8fa7c1d631bc85cd0a454813f53ac35199e7b66d2181c6789'),
        (0x103dcb8,0x6c,'0c5e4c6183e83c97cc2e7f990c213d6a89bd0ee30caddf91b4a8eb59d11dc982'),
    )
    with mcu.open('rb') as stream:
        for offset,length,expected in spans:
            stream.seek(offset)
            if hashlib.sha256(stream.read(length)).hexdigest()!=expected:
                raise ValueError(f'Original SPI6 MCU startup path changed at {offset:#x}')
    ap_spans=(
        (0xffffffc010587c90,0x64,'c2368a918dc39334a5361186e3a0d0db41c7627f0f575cfffe6a2540c6c0a22a'),
        (0xffffffc010588638,0x54,'d2543d7a6b1388203e03b806da8b47821d7731af98d728d01bc4c312d0cdea46'),
        (0xffffffc0105887ec,0xd4,'2550f8b70738501a1da2596bd1db285bd622e679073a4c5d67ab8073def6ae6f'),
        (0xffffffc0105cf1e8,0x290,'23ff53df3e81ea06f6c52f3cbf7c69b961b1f2e9e6640eb1db65c0cd18890ff5'),
    )
    with platform.factory_file('ivi/Image').open('rb') as stream:
        for address,length,expected in ap_spans:
            stream.seek(address-0xffffffc010080000)
            if hashlib.sha256(stream.read(length)).hexdigest()!=expected:
                raise ValueError(f'Original AP SPI6 receive path changed at {address:#x}')
    dt=DT.read_bytes()
    if hashlib.sha256(dt).hexdigest()!=DT_SHA:
        raise ValueError('SPI6 MCU Linux device tree changed')
    nodes=platform.parse_dtb(dt)
    spi=nodes['/spi@10900000']; slave=nodes['/spi@10900000/spi_mcu@0']
    gpio=nodes['/pinctrl@10450000/gpa0']; cpu=nodes['/pinctrl@17060000/gpf8']
    if (spi['dmas']!=struct.pack('>4I',229,13,229,12) or slave['reg']!=bytes(4)
            or slave['compatible']!=b'lge,mcu-spi-driver\0' or 'spi-cpha' not in slave
            or slave['lge,mcu-req-gpio']!=gpio['phandle']+bytes(8)
            or slave['lge,cpu-req-gpio']!=cpu['phandle']+struct.pack('>II',3,0)
            or struct.unpack('>3I',gpio['interrupts'][:12])!=(0,13,4)
            or platform.native_alive_gpio_layout()['pin_irqs'][0]!=13):
        raise ValueError('SPI6 MCU original GPIO/CS/DMA routing changed')
    hcb=platform.original('hcb/hcb.img').read_bytes()
    start=hcb.index(b'VDTVM3\0\0')+8
    grants=platform.parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
    alive=grants['/vplatform/vl,vgpio@alive0']
    if (alive['reg']!=struct.pack('>QI',0x10450000,0xb00) or
            (int.from_bytes(grants['/vpinctrl/gpa0']['phandle'],'big'),0,0)
            not in tuple(struct.iter_unpack('>III',alive['write-enable-range']))):
        raise ValueError('Original VM3 lacks GPA0[0] grant')
    return dict(mcu_sha256=digest,vle_source_spans=[(hex(a),b,c) for a,b,c in spans],
                ap_source_spans=[(hex(a),b,c) for a,b,c in ap_spans],
                linux_dtb_sha256=DT_SHA,spi_port=6,chip_select=0,mcu_request='GPA0[0] active-low',
                mcu_request_spi=13,cpu_request='GPF8[3]',frame_bytes=512,virtual_turnaround_ns=50000,
                a0_response_rx_only_packet_count=512,
                b1_distinct_request_edge=True,virtual_b1_turnaround_ns=5000000,
                scope='Source-derived startup peer only; no original MCU CPU, HSM or application KIPC execution')


def mcu_spi6_runtime(qmp):
    return {key:qmp.execute('qom-get',{'path':MCU_SPI_PATH,'property':key}) for key in MCU_SPI_KEYS}


def inventory():
    raw=DT.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=DT_SHA:raise ValueError('Original syscon DT identity mismatch')
    nodes=platform.parse_dtb(raw);path='/syscon@10221000';props=nodes[path]
    handle=struct.unpack('>I',props['phandle'])[0]
    if props['reg']!=bytes.fromhex('000000001022100000000100') or handle!=0xf1:
        raise ValueError('Original syscon address/phandle contract changed')
    refs=[]
    for name,p in nodes.items():
        for key,value in p.items():
                                                                              
                                                        
            if name!=path and value[:4]==props['phandle']:
                refs.append({'node':name,'property':key,'raw_hex':value.hex(),
                    'arguments':platform.render(value),'properties':{k:platform.render(v) for k,v in p.items()}})
    symbols=json.loads(SYMBOLS.read_text(encoding='utf-8'))
    if symbols['sha256']!=KERNEL_SHA:raise ValueError('Original Linux symbol identity mismatch')
    selected=[s for s in symbols['symbols'] if s['name'] in (
        'exynos_usi_init','exynosauto_spi_probe','exynosauto_spi_hwinit','exynosauto_spi_pin_ctrl',
        'exynosauto_spi_config','arbif_front_link_on','arbif_front_probe','regmap_update_bits_base')]
    enabled=[]
    for name,p in nodes.items():
        if p.get('compatible')!=b'samsung,exynosauto-spi\0' or p.get('status')!=b'okay\0':continue
        selector_handle=p['samsung,usi-phandle']
        syscon=next(q for q in nodes.values() if q.get('phandle')==selector_handle)
        alias=next(k for k,v in nodes['/aliases'].items() if k.startswith('spi') and v.rstrip(b'\0').decode()==name)
        base=struct.unpack('>3I',p['reg'])[1];selector=struct.unpack('>3I',syscon['reg'])[1]+struct.unpack('>I',p['samsung,usi-offset'])[0]
        dmas=struct.unpack('>4I',p['dmas']) if 'dmas' in p else (229,-1,229,-1)
        if dmas[0]!=229 or dmas[2]!=229:raise ValueError('Enabled SPI uses a different PL330')
        enabled.append({'port':int(alias[3:]),'node':name,'base':base,'selector':selector,'fifo_bytes':struct.unpack('>I',p['samsung,spi-fifosize'])[0],
            'spi':struct.unpack('>3I',p['interrupts'])[1],'tx_request':dmas[1],'rx_request':dmas[3]})
    return {'source':str(DT),'sha256':DT_SHA,'kernel_sha256':KERNEL_SHA,'syscon':path,'enabled_spi_ports':enabled,
        'phandle':handle,'properties':{k:platform.render(v) for k,v in props.items()},
        'references':refs,'symbols':selected,'runtime_fields_verified':False,
        'scope':'Original DT references and symbol metadata, not recovered reset values or hardware effects'}


def profile_options(profile,board_adapter=True):
    if profile is None:return []
    if profile not in ('virtual-spi1-v1','virtual-linux-spi-v1') or not board_adapter:raise ValueError('SPI requires explicit known profile and native board')
                                                                          
                                                                     
    return ['-global','driver=exynos-auto9.usi-spi,property=virtual-profile,value='+('2' if profile=='virtual-linux-spi-v1' else '1')]


def runtime_state(qmp,port=1):
    path=f'/machine/exynos-spi{port}'
    properties={row['name'] for row in qmp.execute('qom-list',{'path':path})}
    return {key:qmp.execute('qom-get',{'path':path,'property':key})
            for key in (*KEYS,*(key for key in FIFO_ERRORS if key in properties))}


def native_layout():
    rows=inventory()['enabled_spi_ports']
    actual=[(r['port'],r['base'],r['selector'],r['spi'],r['tx_request'],r['rx_request']) for r in rows]
    expected=[(1,0x10320000,0x10221008,347,3,2),(6,0x10900000,0x10821000,358,13,12),
        (7,0x10920000,0x10821008,360,15,14),(10,0x10980000,0x10821020,366,-1,-1)]
    if actual!=expected or [r['fifo_bytes'] for r in rows]!=[256,256,64,64]:raise ValueError('Original enabled SPI topology/capacity changed')
    return rows


def original_contract(profile='virtual-spi1-v1'):
    profile_options(profile)
    result=inventory();symbols=json.loads(SYMBOLS.read_text(encoding='utf-8'))
    image=Path(symbols['analysis_elf'])
    if vm.sha(image)!=symbols['analysis_sha256'] or symbols['analysis_sha256']!='dac5ea52de7614757e608e28d8c08d77f8cdafd68986eb208c091a3169695189':
        raise ValueError('Original Linux analysis carrier identity mismatch')
    original=vm.ROOT/'var/inputs/original/ivi/Image'
    if vm.sha(original)!=KERNEL_SHA:raise ValueError('Original Linux kernel identity mismatch')
    elf=platform.Elf(image.read_bytes())
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    code=elf.at(0xffffffc0105ce860,12)
    decoded=[(i.mnemonic,i.op_str) for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(code,0xffffffc0105ce860)]
    if decoded!=[('mov','w3, #2'),('mov','w2, #7'),('bl','#0xffffffc010575320')]:
        raise ValueError('Original selector mask/value/call contract mismatch')
    rx,done=struct.unpack('<2I',elf.at(0xffffffc0111da188,8))
    if (rx,done)!=(15,25):raise ValueError('Original FIFO shift/TXDONE contract mismatch')
    burst_sites=(0xffffffc0105cf0a4,0xffffffc0105cf1b8)
    for a in burst_sites:
        ins=[(i.mnemonic,i.op_str) for i in Cs(CS_ARCH_ARM64,CS_MODE_ARM).disasm(elf.at(a,12),a)]
        if ins!=[('tst','x0, #1'),('mov','w0, #4'),('csinc','w0, w0, wzr, ne')]:
            raise ValueError('Original TX/RX maxburst selection changed')
    spi=next(r['properties'] for r in result['references'] if r['node']=='/spi@10320000')
    if spi['samsung,usi-offset']['cells']!=[8] or spi['dmas']['cells']!=[229,3,229,2] or spi['interrupts']['cells']!=[0,347,4]:
        raise ValueError('Original SPI1 selector/DMA/IRQ route mismatch')
    nodes=platform.parse_dtb(DT.read_bytes())
    dma=next((n,p) for n,p in nodes.items() if p.get('phandle')==struct.pack('>I',229))
    if bytes.fromhex('1b2e0000') not in dma[1]['reg']:raise ValueError('SPI1 DMA not the native PL330')
    result.update({'profile':profile,'original_kernel':str(original),'analysis_carrier':str(image),
        'analysis_sha256':vm.sha(image),'probe_selector_instructions':code.hex(),'selector_mask':7,'spi_value':2,
        'fifo_bytes':256,'tx_fifo_shift':6,'rx_fifo_shift':rx,'tx_done_bit':done,
        'dma_burst_words':{'mode_bit_0_clear':1,'mode_bit_0_set':4,'original_sites':[hex(a) for a in burst_sites]},
        'dma_controller':dma[0],'dma_properties':{k:platform.render(v) for k,v in dma[1].items()},
        'factory_peer_implemented':False,'factory_images_modified':False,'physical_reset_and_timing_verified':False,
        'assumptions':['Explicit cold selector0, USI reset asserted and empty FIFOs',
            'Virtual1000ns per shifted byte; no physical serial clock/waveform claim',
            'One-word PIO ready thresholds, original single/four-word DMA watermarks; no slave mode, trailing interrupt or extended CS-delay fields',
            'CLK_CFG and USI_OPTION are bounded retained config; timer is independent, not a recovered clock/prescaler model',
            'Actual upstream SSI wire bus with no factory peripheral or application response synthesized',
            'SPI347 and PL330 STOP inputs TX3/RX2 from original DT; this is not physical wiring validation']})
    return result


def public_source(output):
    url='https://android.googlesource.com/kernel/google-modules/soc/gs/+/204078758737776959ecc85c527a00fd2cabb62f/drivers/spi/spi-s3c64xx.c?format=TEXT'
    request=urllib.request.Request(url,headers={'User-Agent':'MEB-local-source-research/1'})
    with urllib.request.urlopen(request,timeout=25) as response:encoded=response.read(1024*1024)
    raw=base64.b64decode(encoded,validate=False)
    blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    if blob!='250ac015f85cd43bc46d3df0c74850f4666a75ff':raise ValueError('Pinned primary SPI source blob mismatch')
    p=output/'spi-s3c64xx.c';p.write_bytes(raw)
    return {'source_url':url,'git_blob_sha1':blob,'path':str(p),'sha256':vm.sha(p),
        'scope':'Related Samsung family source corroboration, not Auto9 silicon specification or substitute for original instructions'}


def check_model(output,executable,enabled=True,all_ports=False):
    from ufs_platform import QTest
    qp,tp=vm.free_port(),vm.free_port()
    command=[str(executable),'-machine','virt,virtualization=on,gic-version=2,x-exynos-vlm=on',
        '-cpu','cortex-a57','-accel','qtest','-m','256','-display','none','-monitor','none','-nic','none',
        '-serial','null','-serial','null','-serial','null','-S',
        '-qmp',f'tcp:127.0.0.1:{qp},server=on,wait=off','-qtest',f'tcp:127.0.0.1:{tp},server=on,wait=off',
        '-qtest-log',str(output/'qtest.log')]
    if enabled:command+=profile_options('virtual-linux-spi-v1' if all_ports else 'virtual-spi1-v1')
    vm.save(output/'launch.json',{'command':command,'qemu_sha256':vm.sha(executable)})
    env=os.environ.copy()
    env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[];qmp=None;qt=None;error=None
    def check(name,actual,expected):
        checks.append({'name':name,'actual':actual,'expected':expected,'passed':actual==expected})
        if actual!=expected:raise ValueError('SPI check failed: '+name)
    with (output/'qemu.log').open('wb') as log:
        process=subprocess.Popen(command,cwd=output,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('SPI QEMU exited')
                try:qmp=vm.QMP(qp);break
                except OSError:time.sleep(.05)
            if qmp is None:raise RuntimeError('SPI QMP connection timeout')
            qmp.execute('cont');qt=QTest(tp);rd,wr=qt.read,qt.write
            def get(n):return qmp.execute('qom-get',{'path':QOM,'property':n})
            def step(n):qt.call(f'clock_step {n}')
            check('Profile explicit or strict',get('virtual-profile'),2 if all_ports else int(enabled))
            check('Cold selector, no SPI access manufactured',get('selector'),0)
            check('Cold SPI engine gated',get('active'),0)
            if not enabled:
                for port in native_layout()[1:]:
                    check('Other default port gated '+str(port['port']),runtime_state(qmp,port['port'])['active'],0)
                tree=qmp.execute('human-monitor-command',{'command-line':'info mtree -f'})
                vm.save(output/'memory-tree.json',{'output':tree})
                check('Strict mode maps no new SPI MMIO','exynos-auto9.usi-spi-selector' in tree or 'exynos-auto9.spi' in tree,False)
            if enabled:
                vm.save(output/'original-contract.json',original_contract('virtual-linux-spi-v1' if all_ports else 'virtual-spi1-v1'))
                gic=next(x['name'] for x in qmp.execute('qom-list',{'path':'/machine/unattached'}) if x['type']=='child<arm_gic>')
                qt.call('irq_intercept_in /machine/unattached/'+gic)
                wr(0x10221008,2);base=0x10320000
                check('Source selector value2 retained',rd(0x10221008),2)
                check('Actual SPI aperture enabled',get('active'),1)
                check('Declared cold USI reset asserted',rd(base+0xc4),1)
                wr(base+0xc4,0);wr(base+0x0c,1);wr(base+8,8)
                check('Empty FIFO real TXDONE',rd(base+0x14)&(1<<25),1<<25)
                wr(base+0x18,0x89abcdef)
                check('Queued bytes not instantly transmitted',get('tx-count'),4)
                check('Disabled channels do not shift',get('words-shifted'),0)
                wr(base,3);step(999)
                check('Actual virtual deadline not early',get('rx-count'),0)
                step(1);check('First deadline transfers one byte',get('rx-count'),1)
                for i in range(3):step(1000)
                check('Loopback real FIFO bytes',rd(base+0x1c),0x89abcdef)
                check('Actual loopback count',get('loopback-words'),4)
                check('No SSI/OEM response invented by loopback',get('wire-words'),0)
                wr(base+0x10,0x10);start=len(qt.events);rd(base+0x1c)
                check('Empty RX latches original underrun flag',rd(base+0x14)&0x10,0x10)
                check('Actual controller IRQ level',get('irq-level'),1)
                check('Actual native GIC347 route',any(e=='IRQ raise 347' for e in qt.events[start:]),True)
                wr(base+0x24,4);check('Original W1C bit2 clears RX underrun',get('pending'),0)
                check('Actual IRQ withdrawn',get('irq-level'),0)
                wr(base+0x10,0);wr(base,0)
                for i in range(64):wr(base+0x18,i)
                check('Bounded256 byte TX FIFO',get('tx-count'),256)
                wr(base+0x18,0x55555555);check('Overflow never exceeds FIFO',get('tx-count'),256)
                check('Real TX overrun flag',get('pending')&8,8)
                wr(base+0x24,8);check('Original W1C TX overrun',get('pending'),0)
                wr(base,0x20);wr(base,0);check('Software reset actually flushes',get('tx-count'),0)
                for width,mode in ((2,0x20020008),(4,0x40040008)):
                    wr(base+8,mode);wr(base+0x18,0x12345678);wr(base,3)
                    for i in range(4//width):step(1000*width)
                    check('Actual packed loopback width'+str(width),rd(base+0x1c),0x12345678)
                    wr(base,0)
                wr(base+8,8);wr(base+0x18,1);wr(base,3)
                before=get('words-shifted');wr(base+0xc4,1);step(10000)
                check('USI reset cancels real pending transfer',get('words-shifted'),before)
                check('USI reset withdraws FIFO data',get('tx-count')|get('rx-count'),0)
                wr(base+0xc4,0);wr(base,0)
                before=get('rejected-accesses');wr(base+8,0x60000000);rd(base+0x30);wr(0x10221008,3)
                check('Reserved mode/address/selector reject',get('rejected-accesses'),before+3)
                check('Rejected selector does not change mode',get('selector'),2)
                check('Rejected word width does not change mode',get('mode'),8)
                                                                          
                                                                              
                vgen,mpu,pdma=0x1b250000,0x1b2f0000,0x1b2e0000
                for slot in range(16):wr(vgen,slot);wr(vgen+4,3)
                wr(mpu,3);wr(mpu+0x4608,0);wr(mpu+0x460c,6)
                                                                             
                                                                             
                wr(mpu+0x4600,0);wr(mpu+0x4604,2)
                dest,program=0x40006000,0x40007000
                code=b'\xbc\x00'+struct.pack('<I',base+0x1c)+b'\xbc\x02'+struct.pack('<I',dest)+b'\xbc\x01'+struct.pack('<I',0x4000)+b'\x30\x10\x25\x10\x08\x00'
                qt.call(f'write {program:#x} {len(code):#x} 0x'+code.hex());wr(dest,0xeeeeeeee)
                wr(base+8,14);wr(base,3)
                check('Empty RX asserts actual PL330 STOP',get('dma-rx-stop'),1)
                wr(pdma+0xd08,0xa0<<16);wr(pdma+0xd0c,program);wr(pdma+0xd04,0);step(1)
                check('Real PL330 waits without fake RX data',rd(dest),0xeeeeeeee)
                qt.call(f'writeb {base+0x18:#x} 0x5a');step(1000);step(1)
                vm.save(output/'pdma-observation.json',{'registers':{hex(o):rd(pdma+o) for o in (0,4,0x30,0x34,0x38,0x40,0x100,0x104,0x400,0x404,0x408)},
                    'mpu':{hex(o):rd(mpu+o) for o in (0x2000,0x2060,0x2064,0x2068,0x206c)},'spi':runtime_state(qmp)})
                check('Real PL330 RX writes destination after transfer',rd(dest),0xeeeeee5a)
                check('DMA actually consumed RX FIFO',get('rx-count'),0)
                check('Empty RX reasserts STOP',get('dma-rx-stop'),1)
                wr(base,0);wr(base+8,8);wr(base+0x20,0x10001);wr(base+0x18,0x44332211)
                count=get('words-shifted');wr(base,3);step(1000);step(10000)
                check('Packet count stops at one actual word',get('words-shifted'),count+1)
                check('Packet-limited queued bytes retained',get('tx-count'),3)
                wr(base+0x20,0)
                for i in range(3):step(1000)
                check('Packet restart shifts remaining actual bytes',rd(base+0x1c),0x44332211)
                wr(base+0x18,0x12345678);count=get('words-shifted');wr(base,0);step(10000)
                check('Channel disable cancels scheduled shifting',get('words-shifted'),count)
                wr(base,0x20);wr(base,0);wr(base+8,0x40040008);wr(base+0x28,13);wr(base+0x18,0x12345678);wr(base,3);step(4000)
                check('Actual TX byte/halfword swap',rd(base+0x1c),0x78563412)
                wr(base,0);wr(base+8,0);wr(base+0x28,0);wr(base+0xc,0)
                qt.call(f'writeb {base+0x18:#x} 0xa5');wr(base,3);step(1000)
                check('Empty real SSI bus has no synthesized OEM response',int(qt.call(f'readb {base+0x1c:#x}').split()[1],16),0)
                check('Actual SSI wire path used',get('wire-words'),1)
                for port in native_layout()[1:]:
                    path=f"/machine/exynos-spi{port['port']}"
                    def other(n):return qmp.execute('qom-get',{'path':path,'property':n})
                    check('Other port starts gated '+str(port['port']),other('active'),0)
                    if all_ports:
                        wr(port['selector'],2);b=port['base'];wr(b+0xc4,0);wr(b+8,8);wr(b,3)
                        qt.call(f'writeb {b+0x18:#x} 0xa5');step(1000)
                        check('Other port real loopback '+str(port['port']),int(qt.call(f'readb {b+0x1c:#x}').split()[1],16),0xa5)
                        wr(b+0x10,16);start=len(qt.events);rd(b+0x1c)
                        check('Other port actual native IRQ '+str(port['port']),any(e==f"IRQ raise {port['spi']}" for e in qt.events[start:]),True)
                        wr(b+0x24,4);wr(b+0x10,0);wr(b,0)
                        if port['port']==6:
                            wr(b,0x20);wr(b,0);wr(b+8,0xf)
                            for i in range(63):wr(b+0x18,0x41414141)
                            qt.call(f'writeb {b+0x18:#x} 0x41')
                            wr(b,3)
                            check('SPI6 4BURST does not request TX with three bytes free',other('dma-tx-stop'),1)
                            step(1000)
                            check('SPI6 4BURST requests TX when four bytes are free',other('dma-tx-stop'),0)
                            for i in range(2):step(1000)
                            check('SPI6 4BURST does not request RX with three bytes queued',other('dma-rx-stop'),1)
                            step(1000)
                            check('SPI6 4BURST requests RX when four bytes are queued',other('dma-rx-stop'),0)
                            check('Four actual shifted bytes',rd(b+0x1c),0x41414141)
                            check('Reading four bytes asserts RX STOP again',other('dma-rx-stop'),1)
                            check('No fixture overruns in either direction',other('tx-overruns')|other('rx-overruns'),0)
                            wr(b,0x20);wr(b,0);wr(b+8,8)
                        for i in range(port['fifo_bytes']//4):wr(b+0x18,i)
                        wr(b+0x18,0xffffffff)
                        check('Exact source DT FIFO capacity '+str(port['port']),other('tx-count'),port['fifo_bytes'])
                        check('Bounded other port overflow '+str(port['port']),other('pending')&8,8)
                        wr(b+0xc4,1);wr(port['selector'],0)
                vm.save(output/'runtime-state.json',runtime_state(qmp))
            qmp.execute('quit');process.wait(timeout=5)
        except Exception as e:error=repr(e)
        finally:
            if qt:vm.save(output/'qtest-transcript.json',{'calls':qt.transcript,'events':qt.events});qt.stream.close();qt.socket.close()
            if qmp:qmp.close()
            if process.poll() is None:process.terminate();process.wait(timeout=5)
    result={'passed':error is None and all(c['passed'] for c in checks),'error':error,'checks':checks,
        'qemu_exit_code':process.returncode,'factory_system_boot_verified':False,'factory_wire_peer_implemented':False}
    vm.save(output/'result.json',result)
    if not result['passed']:raise RuntimeError('SPI model failed; inspect '+str(output/'result.json'))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('inventory','contract','public-source','check','check-linux','strict-check'))
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--qemu',type=Path,default=vm.ROOT/'runtime/qemu-system-aarch64.exe')
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True)
    if args.operation=='inventory':result=inventory()
    elif args.operation=='contract':result=original_contract()
    elif args.operation=='public-source':result=public_source(output)
    else:result=check_model(output,args.qemu.resolve(),enabled=args.operation in ('check','check-linux'),all_ports=args.operation=='check-linux')
    vm.save(output/'result.json',result);print(str(output/'result.json'))


if __name__=='__main__':main()
