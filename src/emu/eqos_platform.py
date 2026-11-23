
import argparse
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import time

import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

BASE=0x17d80000
CLOCK=0x17d60200
PTP=BASE+0x1000
PTP_HZ=26000000
PRESENTATION=BASE+0x240
PRESENTATION_UPDATE=BASE+0x244
PRESENTATION_PC=0xffffffc0105ec0a4
                                                                                
PRESENTATION_STORE=bytes.fromhex('bf3203d561120991200000b9e303152a0200805201008052e00314aa80ffff97')
PRESENTATION_REFERENCE='https://doc-en.rvspace.org/JH7110/TRM/JH7110_TRM/eqos_mac_registers.html'
PTP_PROPERTIES=('ptp-control','ptp-writes','ptp-commands','ptp-increment','ptp-active-addend','ptp-mcgr','ptp-mcgr-writes',
                'ptp-pps0-writes','ptp-presentation-reads','ptp-presentation-update',
                'ptp-presentation-writes','ptp-presentation-commits')
HOT_READ_PROPERTIES=('mmc-overflow-reads','ptp-control-reads','ptp-time-reads','dma-debug-reads')
MTL_PROPERTIES=('mtl-txq-flushes','mtl-txq-flush-mask')
MTL_FLUSH_SITES={0xe8e8:'437d409362ce46f9200080526100028b646862b8606822b8200040b9e0ff0737646822b8',
                 0xee58:'437f409382ce46f9200080526100028b646862b8606822b8200040b9e0ff0737646822b8'}
FSYS2=0x17c20008
FSYS2_MASK=0x11
FSYS2_PROPERTIES=('fsys2-config','fsys2-reads','fsys2-writes')
CHANNEL_PROPERTIES=('channel-read-mask','channel-write-mask','rx-drop-reads','rx-drop-read-mask')
RX_DROP=0x64
MMC_RX_FIFO_OVERFLOW=0x7d4
QNX_MMC_CODE_SHA='8227113220d7c19ee8e36f63cc11ff20933789f80399491aaa1f832ec8e92024'
MMC_RX_RUNT_ERROR=0x79c
MMC_RX_WATCHDOG_ERROR=0x7dc
MMC_RX_RECEIVE_ERROR=0x7e0
MMC_IPC_COUNTERS=(*range(0x810,0x848,4),*range(0x850,0x888,4))
MMC_IPC_STATUS=0x808
MMC_IPC_PADDING=(0x80c,0x848,0x84c)
MMC_IPC_SNAPSHOT=tuple(range(0x808,0x888,4))
MMC_IPC_CODE_PC=0x1b750
MMC_IPC_CODE_SHA='cbe1cc8d031c2c82574993c2d6d6b88e118e6542634676aa392bfab912703b32'
MMC_IPC_LOAD_SITES=((0x1b790,0x1b79c),(0x1b794,0x1b7d0),(0x1b798,0x1b7e0),(0x1b788,0x1b7f0),
                    (0x1b7d8,0x1b800),(0x1b7e8,0x1b808),(0x1b7f8,0x1b820),(0x1b814,0x1b824))
QNX_MMC_OFFSETS=(0x768,0x764,0x788,0x720,0x790,0x71c,0x78c,0x798,0x74c,0x750,0x794,
                 0x754,0x758,0x75c,0x748,0x760,0x7d4,0x76c,0x7cc,0x7c8,0x7a8,0x7a0,0x7a4)
RX_DROP_PC=0xffffffc0105e5398
RX_DROP_CODE=bytes.fromhex('022c40f942900191420040b9bf3103d5e003022a000000ca000000b563064c2942280012')
QNX_RX_DROP_CALL=bytes.fromhex('8100805255001091e00315aa424c487942900111e8e7ff9781008052e00315aa39e8ff97003c0053')
CHANNEL_OFFSETS=(0,4,8,0x10,0x14,0x18,0x1c,0x20,0x28,0x2c,0x30,0x34,0x38,0x60)
LINUX_DTB='ivi/icas3cn-gp_b0_zero.dtb'
LINUX_KERNEL=vm.ROOT/'var/inputs/original/ivi/Image'
LINUX_KERNEL_SHA='c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109'
FSYS2_PC=0xffffffc0105f11d4
FSYS2_BYTES=bytes.fromhex('000040b9bf3103d5e103002a210001ca010000b5bf3203d5210280520000012ae1d740f9200000b9')
DMA_MODE=BASE+0x11000
DMA_SYSBUS=DMA_MODE+4
RESET_NS=50000
MDIO_NS=30000
CHANNEL_BASE=BASE+0x18000
PATH='/machine/exynos-eqos0'
FACTORY_DRIVER_SHA='7d2ca75ebc2c4e7548bb8e1dffd73e3acc966439fde0bf291b3db8bfce8bca15'
FACTORY_DRIVER=vm.ROOT/'var/inputs/support/归档/虚拟机平台对照/ufs-pma-native--devnp-dwceqos.so'
PROPERTIES=('reads','writes','mac-reads','clock-config','clock-reads','clock-writes',
            'reset-requests','reset-completions','rejected','last-rejected','mac-config','hash-lo','hash-hi','hash-2','hash-7',
            'channel-reads','channel-writes','armed-tx','armed-rx',
            'mdio-reads','mdio-writes','mdio-completions','mdio-busy-writes','mdio-due-on-access')
PHY_PROPERTIES=('q2112-reads','q2112-writes','q2112-id-reads','q2112-link-reads',
                'q2112-reset-requests','q2112-unsupported')
PHY_SITES=(
    (0x13870,0xa0,'e0d6c7c0c7d957d393801b56cabd6cf5025adcc107d32ca3169dcd4ec9dae13f'),
    (0x1eb90,0x28,'538787c60f98e4465093931740ff5024d9934f0025127eab8921fbb024cfc3ac'),
    (0x1ed0c,0x28,'ca4dacface079230f16ccd56500663d821dabbc8aaed25ebaf3b309f1686c75b'),
    (0x1f134,0x20,'d1c7b0965d04dd24740929f16c11b0ed84be4301c2995f980430fd538f9809c5'),
    (0x1ff98,0x24,'c0376d4e2d92f4ed54152d908a3df514eabed67dede9917dbe7dc2cbd1381059'),
    (0x14e8c,0x34,'706e7d3956c7d1d52622ea535fa80f15a5428726a50931c9846866cdff807ced'),
)


def qnx_phy_contract():
    from vlm_platform import Elf

    raw=FACTORY_DRIVER.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=FACTORY_DRIVER_SHA:
        raise ValueError('Original QNX PHY driver identity changed')
    elf=Elf(raw)
    if any(hashlib.sha256(elf.at(a,n)).hexdigest()!=sha for a,n,sha in PHY_SITES):
        raise ValueError('Original QNX MDIO endpoint layout changed')
    return dict(family='88Q211x',phy_address=7,mmd_window=[13,14],virtual_model=24,
                virtual_revision=1,id_registers=[0x002b,0x0981],link_register=[3,0x901,2],
                link_partner=False,driver_sites=PHY_SITES,opaque_vendor_latches=True,
                scope='Disconnected management plane; no MDI, packet DMA, or PHY analog behavior')


def linux_fsys2_contract():
    dtb=factory_file(LINUX_DTB)
    node=parse_dtb(dtb.read_bytes())['/ethernet@17D80000']
    ranges=list(struct.iter_unpack('>QI',node['reg']))
    if node['compatible']!=b'samsung,ethernet-qos\0' or ranges[-1]!=(FSYS2,4):
        raise ValueError('Original Linux EQoS FSYS2 resource changed')
    if vm.sha(LINUX_KERNEL)!=LINUX_KERNEL_SHA:
        raise ValueError('Original Linux EQoS kernel identity changed')
    with LINUX_KERNEL.open('rb') as f:
        f.seek(FSYS2_PC-0xffffffc010080000)
        if f.read(len(FSYS2_BYTES))!=FSYS2_BYTES:
            raise ValueError('Original Linux EQoS FSYS2 read/modify/write changed')
        f.seek(0xffffffc0105ed840-0xffffffc010080000)
        if f.read(16)!=bytes.fromhex('bf3203d500e00091010000b9c0035fd6'):
            raise ValueError('Original Linux EQoS RX watchdog write changed')
        f.seek(RX_DROP_PC-8-0xffffffc010080000)
        if f.read(len(RX_DROP_CODE))!=RX_DROP_CODE:
            raise ValueError('Original Linux EQoS missed-frame counter read changed')
    return {'address':hex(FSYS2),'size':4,'mask':hex(FSYS2_MASK),'virtual_reset':0,
            'node':'/ethernet@17D80000','linux_dtb_sha256':vm.sha(dtb),
            'linux_image_sha256':LINUX_KERNEL_SHA,'original_read_pc':hex(FSYS2_PC),
            'original_write_pc':hex(FSYS2_PC+36),'configuration_only':True}


def channel_grants(hcb):
    grants={}
    for vmid,wanted in ((2,list(range(5))),(3,[5,6,7]),(4,[])):
        start=hcb.index(f'VDTVM{vmid}'.encode()+b'\0\0')+8
        nodes=parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
        active=[]
        for index in range(8):
            node=nodes[f'/vplatform/vl,eth0_ch{index}/vl,eth0_ch{index}@mem']
            if node.get('reg')!=struct.pack('>QI',CHANNEL_BASE+0x1000*index,0x80):
                raise ValueError(f'Original EQoS channel window changed for VM{vmid}')
            if 'vl,disabled' not in node:active.append(index)
        if active!=wanted:
            raise ValueError(f'Original EQoS channel grants changed for VM{vmid}')
        grants[f'vm{vmid}']=active
    return grants


def ptp_contract(raw):
    dtb=factory_file(LINUX_DTB)
    tree=parse_dtb(dtb.read_bytes())
    node=tree['/ethernet@17D80000']
    dma=tree['/amba/pdma0@1B2E0000']
    if (node['dma-names']!=b'mcgr\0' or node['dmas']!=dma['phandle']+struct.pack('>I',22)
            or dma['reg']!=struct.pack('>QI',0x1b2e0000,0x1000)):
        raise ValueError('Original MCGR to PDMA0 request 22 binding changed')
    if (PTP,0x100) not in struct.iter_unpack('>QI',node['reg']):
        raise ValueError('Original Linux EQoS timestamp window changed')
    names=node['clock-names'].split(b'\0')
    clocks=list(struct.iter_unpack('>II',node['clocks']))
    if clocks[names.index(b'ptp_ref')]!=(0x17,1):
        raise ValueError('Original Linux EQoS PTP clock binding changed')
    path='/vplatform/vl,eth0_ts/vl,eth0_ts@mem'
    for i in (2,3,4):
        start=raw.index(f'VDTVM{i}\0\0'.encode())+8
        dev=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])[path]
        if dev['reg']!=struct.pack('>QI',PTP,0x100) or ('vl,disabled' in dev)!=(i!=3):
            raise ValueError('Original EQoS timestamp grant changed')
        mac=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])[
            '/vplatform/vl,eth0_mac/vl,eth0_mac_ts_prsnt@mem']
        if (mac['reg']!=struct.pack('>QI',PRESENTATION,8)
                or mac['vl,alignment']!=struct.pack('>I',4)
                or ('vl,disabled' in mac)!=(i!=3)):
            raise ValueError('Original EQoS presentation grant changed')
    if vm.sha(LINUX_KERNEL)!=LINUX_KERNEL_SHA:
        raise ValueError('Original EQoS presentation writer identity changed')
    with LINUX_KERNEL.open('rb') as f:
        f.seek(PRESENTATION_PC-8-0xffffffc010080000)
        if f.read(len(PRESENTATION_STORE))!=PRESENTATION_STORE:
            raise ValueError('Original EQoS presentation write/commit instruction changed')
    return {'base':hex(PTP),'vmid':3,'reference_hz':PTP_HZ,
            'reference_basis':'virtual board fin_pll rate, original provider index 1',
            'packet_timestamps':False,'pps_signal':False,'mcgr_config_offset':0x70,'pps0_seconds_offset':0x80,
            'mcgr_request':{'controller':'pdma0','request':22,'idle':'inactive','emits_requests':False},
            'presentation_nanoseconds':hex(PRESENTATION),
            'presentation_update':{'address':hex(PRESENTATION_UPDATE),'width':4,'vmid':3,
                                   'original_store_pc':hex(PRESENTATION_PC),
                                   'commit':['TSINIT','TSUPDT'],'direction':'PTP ADDSUB',
                                   'reference':PRESENTATION_REFERENCE,'emits_packet_or_dma':False},
            'oem_entry_points':['0xffffffc0105eb468','0xffffffc0105eb830',
                                '0xffffffc0105eb960','0xffffffc0105ebeb8']}


def source_contract():
    from vlm_platform import Elf

    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    start=raw.index(b'VDTVM2\0\0')+8
    nodes=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    ranges=[]
    for path,props in nodes.items():
        if (path.startswith(('/vplatform/vl,eth0_mac/','/vplatform/vl,eth0_base/','/vplatform/vl,eth0_ch'))
            and 'reg' in props and 'vl,disabled' not in props):
            ranges.extend(struct.iter_unpack('>QI',props['reg']))
                                                                          
    offsets={0,4,8,0xa4,0xa8,0xac,0x90,0x98,0x9c,0xa0,0xb4,0x11c,0x120,0x700,0x70c,0x710,0x800,
             0x2000,0x2030,0x2034,0x11000,0x11004,0x1100c,0x11010,0x11014}
    offsets.update(range(0x10,0x30,4))
    offsets.update(range(0x300,0x400,4))
    offsets.update(range(0x70,0x90,4))
    offsets.update(0x8000+queue*0x1000+off for queue in range(8)
                   for off in (0,0x10,0x18,0x1c,0x20,0x24,0x2c,0x30))
    offsets.update((0x200,0x204))
    offsets.update(QNX_MMC_OFFSETS)
    offsets.update((MMC_RX_RUNT_ERROR,MMC_RX_WATCHDOG_ERROR,MMC_RX_RECEIVE_ERROR,*MMC_IPC_SNAPSHOT))
    offsets.update(0x18000+index*0x1000+reg for index in range(5)
                   for reg in (*CHANNEL_OFFSETS,RX_DROP))
    required={BASE+off for off in offsets}|{CLOCK}
    missing=sorted(a for a in required if not any(b<=a and a+4<=b+n for b,n in ranges))
    if missing:raise ValueError('Unbound EQoS words: '+', '.join(map(hex,missing)))
    if vm.sha(FACTORY_DRIVER)!=FACTORY_DRIVER_SHA:raise ValueError('Factory EQoS driver identity changed')
    driver=Elf(FACTORY_DRIVER.read_bytes())
    stats=driver.at(0xb3d0,0x178)
    loads=tuple(((ins>>10)&0xfff)*4 for ins, in struct.iter_unpack('<I',stats)
                if ins&0xffc003e0==0xb9400000 and 0x700<=((ins>>10)&0xfff)*4<0x800)
    if hashlib.sha256(stats).hexdigest()!=QNX_MMC_CODE_SHA or loads!=QNX_MMC_OFFSETS:
        raise ValueError('Original QNX MMC statistics loads changed')
                                                                                               
    ipc=driver.at(MMC_IPC_CODE_PC,0x1b848-MMC_IPC_CODE_PC)
    if hashlib.sha256(ipc).hexdigest()!=MMC_IPC_CODE_SHA:
        raise ValueError('Original QNX MMC IPC snapshot loads changed')
    ipc_words=[]
    for address_pc, load_pc in MMC_IPC_LOAD_SITES:
        addr, = struct.unpack_from('<I',ipc,address_pc-MMC_IPC_CODE_PC)
        pair, = struct.unpack_from('<I',ipc,load_pc-MMC_IPC_CODE_PC)
                                                                             
        if ((addr&0xffc003e0)!=0x91000260 or (pair&0xffff8000)!=0xa9400000
                or ((pair>>5)&31)!=(addr&31)):
            raise ValueError('Original QNX MMC IPC load shape changed')
        start=(addr>>10)&0xfff
        ipc_words.extend(range(start,start+16,4))
    modeled=tuple(sorted((MMC_IPC_STATUS,*MMC_IPC_PADDING,*MMC_IPC_COUNTERS)))
    if tuple(ipc_words)!=MMC_IPC_SNAPSHOT or modeled!=MMC_IPC_SNAPSHOT:
        raise ValueError('MMC IPC model does not cover the original grouped loads')
    if driver.at(0x183dc,len(QNX_RX_DROP_CALL))!=QNX_RX_DROP_CALL:
        raise ValueError('Original QNX EQoS missed-frame counter read changed')
    for pc,code in MTL_FLUSH_SITES.items():
        expected=bytes.fromhex(code)
        if driver.at(pc,len(expected))!=expected:
            raise ValueError('Original QNX MTL flush loop changed')
    return {'hcb_sha256':vm.sha(hcb),'driver_sha256':FACTORY_DRIVER_SHA,'words':sorted(required),
            'linux_fsys2':linux_fsys2_contract(),
            'channel_grants':channel_grants(raw),'ptp':ptp_contract(raw),
            'rx_watchdog':{'offset':0x38,'interval_mask':0xff,'original_write_pc':'0xffffffc0105ed848',
                           'packet_event_backend':False},
            'rx_missed_frames':{'offset':RX_DROP,'original_load_pc':hex(RX_DROP_PC),
                                'qnx_call_pc':'0x183fc','channels':list(range(8)),
                                'read_only':True,'receives_frames':False},
            'mmc_rx_fifo_overflow':{'offset':MMC_RX_FIFO_OVERFLOW,'width':4,'read_only':True,
                                    'disconnected_counter':0},
            'mmc_rx_runt_error':{'offset':MMC_RX_RUNT_ERROR,'width':4,'read_only':True,
                                 'disconnected_counter':0},
            'mmc_rx_watchdog_error':{'offset':MMC_RX_WATCHDOG_ERROR,'width':4,'read_only':True,
                                     'disconnected_counter':0},
            'mmc_rx_receive_error':{'offset':MMC_RX_RECEIVE_ERROR,'width':4,'read_only':True,
                                    'disconnected_counter':0},
            'mmc_ipc_snapshot':{'counter_offsets':MMC_IPC_COUNTERS,'irq_status':MMC_IPC_STATUS,
                                'snapshot_padding':MMC_IPC_PADDING,'width':4,'read_only':True,
                                'original_ldp_sites':[hex(pc) for _,pc in MMC_IPC_LOAD_SITES],
                                'original_code_pc':hex(MMC_IPC_CODE_PC),'code_sha256':MMC_IPC_CODE_SHA,
                                'disconnected_value':0,'register_reference':PRESENTATION_REFERENCE},
            'qnx_mmc_statistics':{'offsets':loads,'original_code_pc':'0xb3d0','code_sha256':QNX_MMC_CODE_SHA,
                                  'read_only':True,'disconnected_counters':0},
            'mtl_txq_flush':{'first':0x8000,'stride':0x1000,'queues':8,'command_bit':0,
                             'original_poll_sites':[hex(pc+0x18) for pc in MTL_FLUSH_SITES],
                             'disconnected_empty_queues_only':True},
            'vm3_channel_words':[CHANNEL_BASE+index*0x1000+off
                                     for index in (5,6,7) for off in (*CHANNEL_OFFSETS,RX_DROP)],
            'original_sites':{'get_mac_hi':'0xd83c','get_mac_lo':'0xd84c','disable_hwacg':'0xdb4c',
                              'mac_speed_config':'0x133d8','dma_reset_request':'0xcb50','dma_sysbus':'0xda20','mtl_init':'0xdf38'}}


def policy(mac, enabled, qnx_phy=False):
    if mac is None:
        if qnx_phy:raise ValueError('QNX PHY requires the original VM2 EQoS controller')
        return None
    if not enabled or not re.fullmatch(r'(?i)[0-9a-f]{2}(?::[0-9a-f]{2}){5}',mac):
        raise ValueError('EQoS requires original QNX, the native board and an explicit MAC')
    mac_raw=bytes.fromhex(mac.replace(':',''))
    if mac_raw[0]&3!=2:raise ValueError('EQoS requires a locally administered unicast MAC')
    return {'profile':'virtual-auto9-eqos-control-v1','default_enabled':False,
            'mac':mac.lower(),'mac_le':int.from_bytes(mac_raw,'little'),
            'virtual_reset_ns':RESET_NS,'virtual_mdio_ns':MDIO_NS,
            'virtual_fifo_bytes_per_direction':16384,'isolated_channels':8,
            'network_backend':False,'phy':qnx_phy_contract() if qnx_phy else False,
            'descriptor_dma':False,'irq':False,**source_contract()}


def runtime_state(qmp):
    props={row['name'] for row in qmp.execute('qom-list',{'path':PATH})}
    fsys2=all(key in props for key in FSYS2_PROPERTIES)
    state={k:qmp.execute('qom-get',{'path':PATH,'property':k})
           for k in PROPERTIES+tuple(k for k in FSYS2_PROPERTIES+CHANNEL_PROPERTIES+PTP_PROPERTIES+PHY_PROPERTIES+HOT_READ_PROPERTIES+MTL_PROPERTIES if k in props)}
    if 'read-histogram' in props:
        raw=qmp.execute('qom-get',{'path':PATH,'property':'read-histogram'})
        histogram={}
        for row in raw.splitlines():
            offset,count=row.split(':')
            address=int(offset,16)
            key=hex(address)
            if address % 4 or not 0<=address<0x20000 or key in histogram or int(count)<=0:
                raise ValueError('Invalid EQoS read histogram')
            histogram[key]=int(count)
        state['read-histogram']=histogram
    return {'fsys2-model-present':fsys2,**state}


def fixture(output, exe, mac, sysmmu=False, dpp=False, decon_window=False, pcie2_elbi=False, pcie2_phy=False, mali_g3d1_id=None, pcie2_pcs=False, pcie2_dbi=False, pcie2_atu=False,mali_g3d1_discovery=False,mali_g3d1_reset_irq=False,mali_g3d1_config=False,mali_g3d1_empty_power=False,linux_usi_uart=False,g2d_reset=False,linux_sysmmu=False,qnx_phy88q2112=False):
    from ufs_platform import QTest
    import sysmmu_platform as mmu
    import dpp_platform as display
    import decon_platform as windows
    import pcie2_platform as pcie
    import mali_g3d1_platform as mali
    import linux_uart_platform as uart
    import g2d_reset_platform as g2d
    conf=policy(mac,True,qnx_phy88q2112)
    qport,tport=vm.free_port(),vm.free_port()
    machine='virt,virtualization=on,gic-version=2,x-exynos-vlm=on'
    if conf:machine+=f",x-exynos-eqos-mac={conf['mac_le']}"
    if sysmmu:
        mmu.policy(mmu.PROFILE,True)
        machine+=",x-exynos-sysmmu-discovery=1"
    if linux_sysmmu:
        mmu.linux_policy(mmu.LINUX_PROFILE,True)
        machine+=",x-exynos-linux-sysmmu=1"
    if dpp:
        dpp_contract=display.policy(display.PROFILE,sysmmu)
        machine+=",x-exynos-dpp-irq=1"
    if decon_window:
        windows.policy(windows.PROFILE,dpp_contract if dpp else None)
        machine+=",x-exynos-decon-window=1"
    if pcie2_elbi:
        pcie_contract=pcie.policy(pcie.PROFILE,True)
        machine+=',x-exynos-pcie2-elbi=1'
    if pcie2_phy:
        phy_contract=pcie.phy_policy(pcie.PHY_PROFILE,pcie_contract if pcie2_elbi else None,True)
        machine=machine.replace('virt,','virt-8704-apm,',1)+',x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1,x-exynos-pcie2-phy=1'
    if pcie2_pcs:
        pcs_contract=pcie.pcs_policy(pcie.PCS_PROFILE,pcie_contract if pcie2_elbi else None,
                                    phy_contract if pcie2_phy else None)
        machine+=',x-exynos-pcie2-pcs=1'
        vm.save(output/'pcie2-pcs-profile.json',pcs_contract)
    if pcie2_dbi:
        dbi_contract=pcie.dbi_policy(pcie.DBI_PROFILE,pcs_contract if pcie2_pcs else None)
        machine+=',x-exynos-pcie2-dbi=1'
    if pcie2_atu:
        atu=pcie.atu_policy(pcie.ATU_PROFILE,dbi_contract if pcie2_dbi else None)
        machine+=',x-exynos-pcie2-atu=1'
        vm.save(output/'pcie2-atu-profile.json',atu)
    if mali_g3d1_id is not None:
        mali.policy(mali_g3d1_id,True)
        machine+=f',x-exynos-mali-g3d1-id={mali_g3d1_id}'
    if mali_g3d1_discovery:
        mali.policy(mali_g3d1_id,True,mali.DISCOVERY_PROFILE)
        machine+=',x-exynos-mali-g3d1-discovery=1'
    if mali_g3d1_reset_irq:
        mali.policy(mali_g3d1_id,True,mali.DISCOVERY_PROFILE if mali_g3d1_discovery else None,
                    mali.RESET_IRQ_PROFILE)
        machine+=',x-exynos-mali-g3d1-reset-irq=1'
    if mali_g3d1_config:
        mali.policy(mali_g3d1_id,True,mali.DISCOVERY_PROFILE if mali_g3d1_discovery else None,
                    mali.RESET_IRQ_PROFILE if mali_g3d1_reset_irq else None,mali.CONFIG_PROFILE)
        machine+=',x-exynos-mali-g3d1-config=1'
    if mali_g3d1_empty_power:
        mali.policy(mali_g3d1_id,True,mali.DISCOVERY_PROFILE if mali_g3d1_discovery else None,
                    mali.RESET_IRQ_PROFILE if mali_g3d1_reset_irq else None,
                    mali.CONFIG_PROFILE if mali_g3d1_config else None,mali.EMPTY_POWER_PROFILE)
        machine+=',x-exynos-mali-g3d1-empty-power=1'
    if linux_usi_uart:
        uart.policy(uart.PROFILE,True)
        machine+=',x-exynos-linux-usi-uart=1'
    if g2d_reset:
        g2d.policy(g2d.PROFILE,True)
        machine+=',x-exynos-g2d-reset=1'
    cmd=[str(exe),'-machine',machine,'-cpu','cortex-a57','-accel','qtest',
         '-m','128','-display','none','-monitor','none','-nic','none','-serial','null','-serial','null',
         '-S','-qmp',f'tcp:127.0.0.1:{qport},server=on,wait=off',
         '-qtest',f'tcp:127.0.0.1:{tport},server=on,wait=off',
         '-qtest-log',str(output/'qtest.log')]
    serial_logs=[]
    if linux_usi_uart:
        cmd+=['-serial','null']
        for index,_,_,_ in uart.PORTS:
            serial_logs.append(output/f'usi{index:02}-qtest.bin')
            cmd+=['-serial','file:'+str(serial_logs[-1])]
    if qnx_phy88q2112:
        cmd+=['-global','driver=exynos-auto9.eqos,property=phy-88q2112,value=on']
    vm.save(output/'launch.json',{'command':cmd,'qemu_sha256':vm.sha(exe),'profile':conf})
    env=os.environ.copy()
    env['PATH']=str(vm.ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    checks=[]
    error=qmp=qt=None
    def check(name, value, expected):
        checks.append(dict(name=name,value=value,expected=expected,passed=value==expected))
        if value!=expected:raise AssertionError(name)
    with (output/'qemu.log').open('wb') as log:
        proc=subprocess.Popen(cmd,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            end=time.monotonic()+10
            while time.monotonic()<end:
                if proc.poll() is not None:raise RuntimeError('QEMU exited before QMP')
                try:qmp=vm.QMP(qport);break
                except OSError:time.sleep(.05)
            if qmp is None:raise TimeoutError('QMP not ready')
            qmp.execute('cont')
            qt=QTest(tport)
            nodes=qmp.execute('qom-list',{'path':'/machine'})
            check('Controller is opt-in',any(n['name']=='exynos-eqos0' for n in nodes),conf is not None)
            if conf:
                hi=(1<<31)|(conf['mac_le']>>32)
                lo=conf['mac_le']&0xffffffff
                check('Original MAC high word',qt.read(BASE+0x300),hi)
                check('Original MAC low word',qt.read(BASE+0x304),lo)
                check('FIFO capability',qt.read(BASE+0x120),(7<<6)|7)
                for index in range(8):
                    qt.write(BASE+0x10+4*index,0xa5a55a00|index)
                check('Eight independent original hash config words',
                      [qt.read(BASE+0x10+4*i) for i in range(8)],
                      [0xa5a55a00|i for i in range(8)])
                qt.write(BASE,0x2000)
                check('MAC speed/duplex config retained',qt.read(BASE),0x2000)
                qt.write(CLOCK,0xffffffff)
                check('Only original clock bit',qt.read(CLOCK),2)
                check('Separate FSYS2 cold config',qt.read(FSYS2),0)
                qt.write(FSYS2,0xffffffff)
                check('Only observed Linux FSYS2 bits latch',qt.read(FSYS2),FSYS2_MASK)
                qt.write(FSYS2,0x10)
                check('FSYS2 config bits independently writable',qt.read(FSYS2),0x10)
                qt.write(BASE+0x8030,0x20)
                check('First queue holds configuration',qt.read(BASE+0x8030),0x20)
                check('Separate second queue remains clear',qt.read(BASE+0x9030),0)
                for index in (5,6,7):
                    chan=CHANNEL_BASE+index*0x1000
                    check(f'Guest channel {index} starts stopped',qt.read(chan+8),0)
                    qt.write(chan+0x34,0x100+index)
                check('Separate guest channel ring config',
                      [qt.read(CHANNEL_BASE+index*0x1000+0x34) for index in (5,6,7)],
                      [0x105,0x106,0x107])
                check('All three guest channels were visited',runtime_state(qmp)['channel-write-mask']&0xe0,0xe0)
                qt.write(CHANNEL_BASE+5*0x1000+0x38,0xffffffff)
                check('RX watchdog interval latches the OEM 8-bit field',qt.read(CHANNEL_BASE+5*0x1000+0x38),0xff)
                check('RX watchdog is per-channel',qt.read(CHANNEL_BASE+6*0x1000+0x38),0)
                check('RX watchdog config alone does not assert DMA status',qt.read(CHANNEL_BASE+5*0x1000+0x60),0)
                qt.write(CHANNEL_BASE+5*0x1000+0x64,1)
                qt.write(BASE+0x120,0)
                qt.read(BASE+0x2fc)
                check('Reserved channel word and arbitrary/RO accesses reject',runtime_state(qmp)['rejected'],3)
                desc=0x40010000
                raw=bytes.fromhex('001001400000000020000000000000b0')
                qt.put(desc,raw)
                qt.write(CHANNEL_BASE+0x14,desc)
                qt.write(CHANNEL_BASE+0x20,desc+16)
                qt.write(CHANNEL_BASE+4,1)
                check('TX configuration arms disconnected channel',runtime_state(qmp)['armed-tx'],1)
                qt.call('clock_step 1000')
                check('No packet backend means no descriptor writeback',qt.get(desc,len(raw)).hex(),raw.hex())
                qt.write(CHANNEL_BASE+0x60,0xffff)
                check('W1C does not assert status',qt.read(CHANNEL_BASE+0x60),0)
                check('Armed disconnected DMA remains stopped',
                      [qt.read(BASE+o) for o in (0x1100c,0x11010,0x11014)],[0,0,0])
                mdio_before=runtime_state(qmp)
                qt.write(BASE+0x204,0x1234)
                qt.write(BASE+0x200,0x10d)
                check('MDIO read initially busy',qt.read(BASE+0x200)&1,1)
                qt.write(BASE+0x204,0x4003)                                             
                qt.write(BASE+0x200,0x105)
                check('Busy MDIO writes leave the original command/data latched',
                      [qt.read(BASE+0x200),qt.read(BASE+0x204)],[0x10d,0x1234])
                mdio_busy=runtime_state(qmp)
                check('Busy writes are counted without a decode error',
                      [mdio_busy[k]-mdio_before[k] for k in ('mdio-busy-writes','rejected','mdio-writes','mdio-completions')],
                      [2,0,2,0])
                qt.call(f'clock_step {MDIO_NS-1}')
                check('Busy writes cannot complete or postpone original MDIO deadline early',qt.read(BASE+0x200)&1,1)
                qt.call('clock_step 1')
                check('MDIO host clock completes on empty bus',qt.read(BASE+0x200)&1,0)
                check('Undriven MDIO reads all ones',qt.read(BASE+0x204),0xffff)
                check('Exactly one original MDIO transfer completed',
                      runtime_state(qmp)['mdio-completions']-mdio_before['mdio-completions'],1)
                if qnx_phy88q2112:
                    def smi(reg, value=None):
                        if value is not None:
                            qt.write(BASE+0x204,value)
                        qt.write(BASE+0x200,(7<<21)|(reg<<16)|(0x10d if value is None else 0x105))
                        qt.call(f'clock_step {MDIO_NS}')
                        return qt.read(BASE+0x204)

                    def mmd(dev, reg, value=None):
                        smi(13,dev)
                        smi(14,reg)
                        smi(13,dev|0x4000)
                        return smi(14,value)

                    check('OEM indirect PMA ID1',mmd(1,2),0x002b)
                    check('OEM indirect PMA ID2',mmd(1,3),0x0981)
                    check('PCS reports disconnected MDI',mmd(3,0x0901),0)
                    mmd(3,0x0901,0xffff)
                    check('Cannot set read-only PHY link by writing status',mmd(3,0x0901),0)
                    mmd(31,0x8001,0xc000)
                    check('OEM interface timing controls retained',mmd(31,0x8001),0xc000)
                    check('Original VM2 PHY identity count',runtime_state(qmp)['q2112-id-reads'],2)
                qt.write(DMA_MODE,1)
                check('Original DMA reset initially busy',qt.read(DMA_MODE),1)
                qt.call(f'clock_step {RESET_NS-1}')
                check('Reset still busy before deadline',qt.read(DMA_MODE),1)
                qt.call('clock_step 1')
                check('Reset clears by virtual timer',qt.read(DMA_MODE),0)
                check('MAC reset preserves declared identity',qt.read(BASE+0x304),lo)
                check('Reset clears queue config',qt.read(BASE+0x8030),0)
                check('Reset disarms channel',runtime_state(qmp)['armed-tx'],0)
                check('Reset clears ring configuration',qt.read(CHANNEL_BASE+0x14),0)
                check('Reset did not clear separate SYSREG',qt.read(CLOCK),2)
                check('MAC DMA reset did not clear Linux FSYS2 config',qt.read(FSYS2),0x10)
                                                                                                
                qt.write(PTP+4,50<<16)
                qt.write(PTP+0x18,1<<31)
                qt.write(PTP,0x223)
                check('PTP addend command commits',qt.read(PTP),0x203)
                qt.write(PTP+0x10,12)
                qt.write(PTP+0x14,400)
                qt.write(PTP,0x207)
                check('PTP initialized to guest seconds',qt.read(PTP+8),12)
                qt.call('clock_step 2000')
                check('PTP counter advances from reference and addend',qt.read(PTP+0xc),1700)
                qt.write(PTP+0x10,0)
                qt.write(PTP+0x14,0x80000000|999999900)
                qt.write(PTP,0x20b)
                check('PTP subtracts fractional seconds',qt.read(PTP+0xc),1600)
                qt.write(PTP,0x202)
                qt.call('clock_step 2000')
                check('Disabled PTP time remains frozen',qt.read(PTP+0xc),1600)
                check('Three applied PTP commands',runtime_state(qmp)['ptp-commands'],3)
                qt.write(PTP+0x70,0x89)
                check('MCGR retains original output-zero configuration',qt.read(PTP+0x70),0x89)
                qt.write(PTP+0x70,0)
                check('MCGR disables output on the original zero write',qt.read(PTP+0x70),0)
                qt.write(PTP+0x80,0x12345678)
                check('PPS target is retained separately',qt.read(PTP+0x80),0x12345678)
                check('Presentation time is low 32 bits of PTP ns',qt.read(BASE+0x240),
                      (12_000_001_600)&0xffffffff)
                before=qt.read(PRESENTATION)
                qt.write(PRESENTATION_UPDATE,250)
                check('MCGR stages magnitude without changing presentation',qt.read(PRESENTATION),before)
                check('MCGR staged magnitude is readable',qt.read(PRESENTATION_UPDATE),250)
                qt.write(PTP+0x14,0x80000000)
                qt.write(PTP,0x20a)
                check('ADDSUB subtracts media magnitude on TSUPDT',qt.read(PRESENTATION),(before-250)&0xffffffff)
                check('Zero system delta does not affect PTP clock',qt.read(PTP+0xc),1600)
                qt.write(PTP,0x20a)
                check('Following PTP command does not replay stale correction',
                      qt.read(PRESENTATION),(before-250)&0xffffffff)
                qt.write(PRESENTATION_UPDATE,100)
                qt.write(PTP+0x14,0)
                qt.write(PTP,0x20a)
                check('Positive MCGR correction uses the same latch',qt.read(PRESENTATION),(before-150)&0xffffffff)
                qt.write(PRESENTATION_UPDATE,0xfffffffe)
                qt.write(PTP,0x206)
                check('TSINIT loads separately staged media time',qt.read(PRESENTATION),0xfffffffe)
                qt.write(PRESENTATION_UPDATE,5)
                qt.write(PTP,0x20a)
                check('Presentation accumulator wraps at 32 bits',qt.read(PRESENTATION),3)
                state=runtime_state(qmp)
                check('Exactly four genuine media commits',
                      [state['ptp-presentation-writes'],state['ptp-presentation-commits']],[4,4])
                for i in range(8):
                    check(f'Disconnected channel {i} has no missed RX frames',
                          qt.read(CHANNEL_BASE+i*0x1000+RX_DROP),0)
                qt.write(CHANNEL_BASE+0x7000+RX_DROP,1)
                qt.write(CHANNEL_BASE+RX_DROP,1)
                drop=runtime_state(qmp)
                check('Both guest channel groups have read-only missed-frame counters',
                      [drop['rx-drop-reads'],drop['rx-drop-read-mask'],drop['rejected']-state['rejected']],
                      [8,0xff,2])
                qt.write(DMA_SYSBUS,0x80e)
                check('Original DMA SYSBUS retention',qt.read(DMA_SYSBUS),0x80e)
                state=runtime_state(qmp)
                check('Exactly one reset', [state['reset-requests'],state['reset-completions']],[1,1])
                check('Disconnected MMC RX FIFO overflow reads zero',qt.read(BASE+MMC_RX_FIFO_OVERFLOW),0)
                mmc_read=runtime_state(qmp)
                check('MMC read is accepted without side effects',
                      [mmc_read[k]-state[k] for k in ('reads','writes','rejected')],[1,0,0])
                qt.write(BASE+MMC_RX_FIFO_OVERFLOW,1)
                check('The overflow count is not writable',qt.read(BASE+MMC_RX_FIFO_OVERFLOW),0)
                mmc_after=runtime_state(qmp)
                check('MMC write remains rejected',mmc_after['rejected']-mmc_read['rejected'],1)
                check('MMC counter distinguishes reads from writes',
                      [mmc_read['mmc-overflow-reads']-state['mmc-overflow-reads'],
                       mmc_after['mmc-overflow-reads']-state['mmc-overflow-reads']],[1,2])
                qt.read(PTP)
                qt.read(PTP+8)
                qt.read(PTP+0xc)
                debug_words=[qt.read(DMA_MODE+off) for off in (0xc,0x10,0x14)]
                check('DMA debug remains stopped',debug_words,[0,0,0])
                hot=runtime_state(qmp)
                check('Hot reads classified without extra traffic or rejection',
                      [hot[k]-mmc_after[k] for k in (*HOT_READ_PROPERTIES,'reads','writes','rejected')],
                      [0,1,2,3,6,0,0])
                def histogram_delta(first, second):
                    before=first['read-histogram']
                    return {k:v-before.get(k,0) for k,v in second['read-histogram'].items()
                            if v!=before.get(k,0)}
                check('Exact word histogram excludes the rejected MMC write',
                      histogram_delta(state,mmc_after),{'0x7d4':2})
                check('Exact word histogram resolves adjacent PTP and DMA addresses',
                      histogram_delta(mmc_after,hot),
                      {hex(n):1 for n in (0x1000,0x1008,0x100c,0x1100c,0x11010,0x11014)})
                check('Exact word histogram accounts for every accepted EQoS read',
                      sum(hot['read-histogram'].values()),hot['reads'])
                qt.write(BASE+0xd000,0x100a)
                old_mode=qt.read(BASE+0xd000)
                qt.write(BASE+0xd000,1)
                check('Original QNX polling read sees empty queue flush complete',qt.read(BASE+0xd000),0)
                qt.write(BASE+0xd000,old_mode)
                qt.write(BASE+0xc000,0x101)
                qt.write(BASE+0xd010,1)
                check('FTQ self-clears only in Tx queue operation mode',
                      [qt.read(BASE+off) for off in (0xd000,0xc000,0xd010)],[0x100a,0x100,1])
                flushed=runtime_state(qmp)
                check('Both empty TX flushes complete without changing DMA/IRQ state',
                      [flushed['mtl-txq-flushes']-hot['mtl-txq-flushes'],flushed['mtl-txq-flush-mask'],
                       flushed['armed-tx'],flushed['armed-rx'],flushed['rejected']],
                      [2,0x30,hot['armed-tx'],hot['armed-rx'],hot['rejected']])
                check('Original QNX MMC statistics read sequence returns empty counters',
                      [qt.read(BASE+off) for off in QNX_MMC_OFFSETS],[0]*len(QNX_MMC_OFFSETS))
                check('Original RX runt-error count is zero with no received frames',
                      qt.read(BASE+MMC_RX_RUNT_ERROR),0)
                check('Original RX watchdog-error count is zero with no received frames',
                      qt.read(BASE+MMC_RX_WATCHDOG_ERROR),0)
                check('Original RX receive-error count is zero with no received frames',
                      qt.read(BASE+MMC_RX_RECEIVE_ERROR),0)
                check('Original grouped MMC IPC read sees empty counters, status and padding',
                      [qt.read(BASE+off) for off in MMC_IPC_SNAPSHOT],[0]*len(MMC_IPC_SNAPSHOT))
                readonly=(*QNX_MMC_OFFSETS,MMC_RX_RUNT_ERROR,MMC_RX_WATCHDOG_ERROR,
                          MMC_RX_RECEIVE_ERROR,*MMC_IPC_SNAPSHOT)
                for off in readonly:
                    qt.write(BASE+off,1)
                unmodeled=(0x7ac,0x804,0x888,0x88c)
                for off in unmodeled:
                    qt.read(BASE+off)
                stats=runtime_state(qmp)
                check('MMC writes and words outside the modeled bank rejected; read histogram exact',
                      [stats['rejected']-flushed['rejected'],histogram_delta(flushed,stats)],
                      [len(readonly)+len(unmodeled),{hex(off):1 for off in readonly}])
                vm.save(output/'runtime.json',stats)
            for base in mmu.PORTS:
                check('SysMMU default-closed topology '+hex(base),
                      any(n['name']==f'exynos-sysmmu-{base:x}' for n in nodes),sysmmu)
                if not sysmmu:continue
                check('Read-only v7 virtual version '+hex(base),qt.read(base+0x34),mmu.VERSION)
                check('Optional SysMMU features not advertised '+hex(base),qt.read(base+0x874),0)
                addr=base+mmu.PT_BASE_VID4
                qt.write(addr,0xae150)
                check('Original VID4 PFN retained '+hex(base),qt.read(addr),0xae150)
                qt.write(addr,0xae151)
                qt.write(addr,0x90000)
                qt.write(base+0x500c,0xae150)
                qt.write(base+0x34,0)
                state=mmu.runtime_state(qmp)[hex(base)]
                check('Reject unaligned/non-VM4/other VID/RO write '+hex(base),state['rejected'],4)
                check('No rejected write alters VID4 root '+hex(base),state['ptbase-page'],0xae150)
                qt.write(base+mmu.FLUSH_ALL_VID4,1)
                qt.write(base+mmu.FLUSH_ALL_VID4,2)
                state=mmu.runtime_state(qmp)[hex(base)]
                check('Only original invalidate command accepted '+hex(base),[state['flush-all'],state['rejected']],[1,5])
                check('Reset pending state on disconnected SysMMU '+hex(base),qt.read(base+mmu.INT_STATUS),0)
                qt.write(base+mmu.INT_CLEAR,0xffffffff)
                check('W1C cannot raise a fault '+hex(base),qt.read(base+mmu.INT_STATUS),0)
                qt.write(base+mmu.INT_STATUS,1)
                state=mmu.runtime_state(qmp)[hex(base)]
                check('Status is read-only and clear counted '+hex(base),
                      [state['status-reads'],state['clear-writes'],state['pending'],state['rejected']],[2,1,0,6])
                qt.write(addr,0)
                check('Root can be cleared without a completion '+hex(base),qt.read(addr),0)
            check('Original DPUS0 IRQ profile default closed',
                  any(n['name']=='exynos-dpp-irqs' for n in nodes),dpp)
            if dpp:
                for pair in display.PORTS:
                    for kind,base in enumerate(pair):
                        check('DPP/IDMA IRQ word reset '+hex(base),qt.read(base+4),0)
                        qt.write(base+4,0xffffffff)
                        check('DPP/IDMA configuration and W1C separation '+hex(base),
                              qt.read(base+4),display.MASKS[kind])
                        qt.write(base+4,display.PENDING[kind])
                        check('DPP/IDMA clear cannot raise an event '+hex(base),qt.read(base+4),0)
                state=display.runtime_state(qmp)
                check('Exactly the eight declared words responded',len(state),8)
                check('No DPP/IDMA synthetic pending events',
                      [row['pending'] for row in state.values()],[0]*8)
            check('DECON WOD profile default closed',
                  any(n['name']=='exynos-decon-window' for n in nodes),decon_window)
            if decon_window:
                for i,base in enumerate(windows.WINDOWS):
                    check('WOD live window starts off '+hex(base),qt.read(base+windows.STATUS),0)
                    for offset,mask in windows.MASKS.items():
                        value=0xffffffff ^ (i<<4 if offset==0 else i)
                        qt.write(base+offset,value)
                        check('WOD staged control mask '+hex(base+offset),qt.read(base+offset),value&mask)
                    qt.write(base+windows.STATUS,1)
                    qt.write(base+0x814,1)
                    check('WOD staged configuration cannot fabricate live output '+hex(base),
                          qt.read(base+windows.STATUS),0)
                state=windows.runtime_state(qmp)
                check('Four isolated WOD staged controls',
                      [row['control'] for row in state.values()],[0xf3,0xe3,0xd3,0xc3])
                check('Read-only status and unknown offsets refused',
                      [row['rejected'] for row in state.values()],[2]*4)
                check('All four WOD status samples were read',
                      [row['status-reads'] for row in state.values()],[2]*4)
            check('PCIe2 ELBI profile default closed',
                  any(n['name']=='exynos-pcie2-elbi' for n in nodes),pcie2_elbi)
            if pcie2_elbi:
                for offset,value in zip(pcie.WORDS,(0x154000,0x33,0x55)):
                    check('PCIe2 cold reset '+hex(offset),qt.read(pcie.BASE+offset),0)
                    qt.write(pcie.BASE+offset,value)
                    check('PCIe2 independent config word '+hex(offset),qt.read(pcie.BASE+offset),value)
                qt.write(pcie.BASE+0x2c,1)
                actual=pcie.runtime_state(qmp)
                if pcie2_pcs:
                    state=pcie.pcs_runtime_state(qmp)
                    check('Unknown LTSSM write does not create a link',
                          [state['cold-rejected'],state['cold-last-rejected-address']], [1,pcie.BASE+0x2c])
                else:
                    check('Unknown LTSSM write does not create a link',
                          [actual['rejected'],actual['last-rejected'],actual['last-rejected-write']], [1,0x2c,1])
                check('LTSSM write cannot alter probe words',[actual[f'config-{k:x}'] for k in pcie.WORDS],
                      [0x154000,0x33,0x55])
            if pcie2_phy:
                address=pcie.PHY_PMU+pcie.PHY_OFFSET
                check('Original PCIe2 PHY PMU cold state',qt.read(address),0)
                qt.write(address,1)
                check('PCIe2 PMU bit0 is retained',qt.read(address),1)
                qt.write(address,3)
                check('PCIe2 reserved write cannot alter the latch',qt.read(address),1)
                qt.write(address,0)
                check('PCIe2 PMU bit0 may be cleared',qt.read(address),0)
                readback=pcie.phy_runtime_state(qmp)
                check('PCIe2 PHY PMU has only actual valid writes',readback['pcie23-phy-writes'],2)
            if pcie2_pcs:
                tree=qmp.execute('human-monitor-command',{'command-line':'info mtree'})
                check('Original PCS and PHY apertures mapped', all(name in tree for name in
                      ('auto9-pcie2-pcs','auto9-pcie2-pma','auto9-pcie2-sysreg')),True)
                a=pcie.pcs_runtime_state(qmp)
                reads=writes=0
                for row in pcs_contract['words']:
                    addr=row['address']
                    if row['read']:
                        check(f'Cold word {addr:#x}',qt.read(addr),0)
                        reads+=1
                    for value in row['writes']:
                        qt.write(addr,value)
                        writes+=1
                        if row['read']:
                            check(f'Latched {addr:#x}={value:#x}',qt.read(addr),value)
                            reads+=1
                b=pcie.pcs_runtime_state(qmp)
                check('All exact table operations accepted',
                      [b['cold-reads']-a['cold-reads'],b['cold-writes']-a['cold-writes'],
                       b['cold-rejected']-a['cold-rejected']],[reads,writes,0])
                qt.write(0x17ad0988,2)
                qt.write(0x17b10400,0xff)
                check('PCS link cannot be fabricated by a register write',qt.read(0x17ad0988),0)
                check('Bad PMA value cannot alter real latch',qt.read(0x17b10400),3)
                c=pcie.pcs_runtime_state(qmp)
                check('Unknown values are refused',[c['cold-rejected']-b['cold-rejected'],
                                                     c['cold-last-rejected-address']],[2,0x17b10400])
            if pcie2_dbi:
                qt.call('writew 0x13000000 0x144d')
                check('Cold DBI IDs are not preseeded and RO protected',qt.read(0x13000000),0)
                qt.write(0x130008bc,1)
                qt.call('writew 0x13000000 0x144d')
                qt.call('writew 0x13000002 0xecee')
                check('Independent guest halfword identity writes',qt.read(0x13000000),0xecee144d)
                qt.write(0x13200010,1)
                check('DBI shadow enable latch',qt.read(0x13200010),1)
                qt.write(0x13000080,0x20)
                check('Retrain request does not invent link',qt.read(0x13000080),0)
                qt.write(0x13000820,0x91abc000)
                qt.write(0x13000824,1)
                check('MSI target addresses are latches only',[qt.read(0x13000820),qt.read(0x13000824)],
                      [0x91abc000,1])
                qt.write(0x130008bc,0)
                qt.call('writew 0x13000002 0x1111')
                check('RO protection returns on gate clear',qt.read(0x13000000),0xecee144d)
                check('Two prohibited identity writes rejected',pcie.dbi_runtime_state(qmp)['dbi-rejected'],2)
            if pcie2_atu:
                check('DBI discovers the separate unrolled ATU',qt.read(0x13000900),0xffffffff)
                check('Standard type1 header byte',int(qt.call('readb 0x1300000e').split()[-1],16),1)
                check('Root link is not reported connected',qt.read(0x1300072c),0)
                qt.write(0x40004000,0xa55a5aa5)
                check('Original LK loader aperture initially remains RAM',qt.read(0x40004000),0xa55a5aa5)
                handoff=pcie.activate_atu(qmp)
                check('Root PCI aperture activated only at handoff',handoff['active_after'],True)
                check('Active PCI aperture no longer aliases QEMU RAM',qt.read(0x40004000),0)
                base,cfg=atu['windows']['atu'][0],atu['windows']['config'][0]
                before=pcie.atu_runtime_state(qmp)
                qt.write(base+4,0x80000000)
                check('Invalid/unconfigured ATU cannot enable',qt.read(base+4),0)
                check('A disabled translation cannot synthesize config response',qt.read(cfg),0)
                for bank,bus in ((0,1),(1,2)):
                    off=base+bank*0x200
                    target=cfg+bank*0x1000
                    for address,value in ((0,4+bank),(8,target),(12,0),(16,target+0xfff),(20,bus<<24),(24,0),(4,0x80000000)):
                        qt.write(off+address,value)
                    check(f'Active ATU bank {bank}',qt.read(off+4),0x80000000)
                    check(f'Empty bus {bus} returns absent PCI vendor',qt.read(target),0xffffffff)
                qt.write(cfg,0xecee144d)
                check('Empty bus cannot acquire a fabricated device',qt.read(cfg),0xffffffff)
                check('Two independent active config windows',pcie.atu_runtime_state(qmp)['atu-active'],2)
                qt.write(base+8,0x90000000)
                qt.write(base+4,0x80000000)
                check('Reprogramming cannot leave a stale active mapping',qt.read(base+4),0)
                qt.write(base+0x104,0x80000000)
                check('Inbound translation never authorizes DMA',qt.read(base+0x104),0)
                check('Other ATU bank remains enabled',qt.read(base+0x204),0x80000000)
                after=pcie.atu_runtime_state(qmp)
                check('ATU rejects invalid and inbound enables',after['atu-rejected']-before['atu-rejected'],3)
                check('Only valid routed accesses are absent-device responses',
                      [after['cfg-absent-reads'],after['cfg-absent-writes'],after['cfg-rejected']],
                      [3,1,1])
            check('G3D1 ID profile default closed',any(n['name']=='exynos-mali-g3d1' for n in nodes),
                  mali_g3d1_id is not None)
            if mali_g3d1_id is not None:
                check('Declared G76 ID read',qt.read(mali.BASE),mali_g3d1_id)
                qt.write(mali.BASE,0xffffffff)
                check('ID register is read only',qt.read(mali.BASE),mali_g3d1_id)
                qt.write(mali.BASE+0x30,3 if mali_g3d1_reset_irq else 1)
                actual=mali.runtime_state(qmp)
                check('G3D1 soft reset not fabricated',
                      [actual['id-reads'],actual['rejected'],actual['last-rejected'],actual['last-rejected-write']],
                      [2,2,0x30,1])
                if mali_g3d1_discovery:
                    check('G3D1 read-only MMU widths',qt.read(mali.BASE+0x14),0x2830)
                    check('G3D1 reports no usable engine slots',
                          [qt.read(mali.BASE+o) for o in (0x18,0x1c,0x100,0x110,0x120)],
                          [0,0,0,0,0])
                    qt.write(mali.BASE+0x04,0xffffffff)
                    check('G3D1 discovery is immutable',qt.read(mali.BASE+0x04),0)
                    qt.read(mali.BASE+0x308)
                    actual=mali.runtime_state(qmp)
                    check('G3D1 implements only exact read-only feature offsets',
                          [actual['feature-reads'],actual['feature-last-offset'],
                           actual['rejected'],actual['last-rejected'],actual['last-rejected-write']],
                          [7,4,4,0x308,0])
                if mali_g3d1_reset_irq:
                    gic = next(row['name'] for row in qmp.execute('qom-list',{'path':'/machine/unattached'})
                               if row['type']=='child<arm_gic>')
                    qt.call('irq_intercept_in /machine/unattached/'+gic)
                    b=mali.BASE
                    qt.write(b+0x30,2)
                    qt.call('clock_step 999')
                    check('No reset completion before timer',[qt.read(b+0x20),qt.read(b+0x2c)],[0,0])
                    qt.call('clock_step 1')
                    check('Completed hard reset latches raw while masked',[qt.read(b+0x20),qt.read(b+0x2c)],[0x100,0])
                    start=len(qt.events)
                    qt.write(b+0x28,0x100)
                    check('Reset completion routes to GPU SPI 255',qt.events[start:],['IRQ raise 255'])
                    check('GPU masked status follows raw and mask',qt.read(b+0x2c),0x100)
                    start=len(qt.events)
                    qt.write(b+0x24,0x100)
                    check('GPU W1C deasserts SPI 255',qt.events[start:],['IRQ lower 255'])
                    check('GPU W1C leaves other bank IRQs empty',
                          [qt.read(b+o) for o in (0x20,0x2c,0x100c,0x200c)],[0,0,0,0])
                    qt.write(b+0x30,1)
                    qt.call('clock_step 1000')
                    state=mali.runtime_state(qmp)
                    check('Soft reset completes through same timer and line',
                          [state['reset-commands'],state['reset-completions'],state['gpu-irq-level']],[2,2,1])
                if mali_g3d1_config:
                    b=mali.BASE
                    regs=(0xf00,0xf04,0xf08,0xf0c)
                    check('Mali config reset values',[qt.read(b+o) for o in regs+(0x304,)],[0,0,0,0,31])
                    vals=[0xf0000,0x20000,1,0x800000]
                    for o,v in zip(regs,vals):
                        qt.write(b+o,v)
                    check('Four configuration registers preserve independent writes',[qt.read(b+o) for o in regs],vals)
                    qt.write(b+0x304,31)
                    qt.write(b+0x304,1)
                    check('Unadvertised coherency mode is rejected',qt.read(b+0x304),31)
                    qt.write(b+0x30,2)
                    qt.call('clock_step 1000')
                    check('Hard reset returns config latches to defaults',[qt.read(b+o) for o in regs],[0,0,0,0])
                    s=mali.runtime_state(qmp)
                    check('Config profile does not silently accept rejected coherency write',
                          [s['config-reads'],s['config-writes'],s['last-rejected'],s['last-rejected-write']],[14,5,0x304,1])
                if mali_g3d1_empty_power:
                    b=mali.BASE
                    check('Absent Mali cores are neither active nor transitioning',
                          [qt.read(b+o) for o in (0x140,0x150,0x160,0x200,0x210,0x220,0x260)],[0]*7)
                    qt.write(b+0x1a0,0)
                    qt.write(b+0x1a0,1)
                    state=mali.runtime_state(qmp)
                    check('Mali core power request cannot create missing resources',
                          [state['power-reads'],state['power-writes'],state['last-rejected'],state['last-rejected-write']],
                          [7,1,0x1a0,1])
            check('FIMG2D reset is opt-in',any(n['name']=='exynos-g2d-reset' for n in nodes),g2d_reset)
            if g2d_reset:
                b=g2d.BASE
                qt.write(b,3)
                qt.call(f'clock_step {g2d.RESET_NS-1}')
                check('FIMG2D remains busy during reset',qt.read(b+0x10),0)
                qt.call('clock_step 1')
                check('FIMG2D completes reset and exposes unspecified version',
                      [qt.read(b+0x10),qt.read(b+0x14)],[1,0])
                qt.write(b+0x100,1)
                state=g2d.runtime_state(qmp)
                check('FIMG2D job start does not synthesize completion',
                      [state['resets'],state['completions'],state['last-rejected'],state['last-rejected-write']],
                      [1,1,0x100,1])
            if linux_usi_uart:
                for i,base,selector,spi in uart.PORTS:
                    check(f'USI{i:02} cold selector',qt.read(selector),0)
                    qt.write(selector,1)
                    qt.write(base+0xc4,1)
                    qt.write(base+0xc4,0)
                    qt.write(base+0xc8,2)
                    qt.write(base+0x20,0x40+i)
                states=uart.runtime_state(qmp)
                check('USI UART routing and software reset are independent',
                      [[v[k] for k in ('selector','control','option','software-resets','rejected-accesses')]
                       for v in states.values()],[[1,0,2,1,0]]*len(uart.PORTS))
            if linux_sysmmu:
                check('Every original VM3 SysMMU has virtual version 7',
                      [qt.read(base+0x34) for base in mmu.LINUX_VIDS],
                      [mmu.VERSION]*len(mmu.LINUX_VIDS))
                a,b,c=0x18c80000,0x19e00000,0x18050000
                qt.write(b+0x1010,1)
                qt.write(a+0x3010,1)
                qt.write(a+0x300c,0xa0000)
                qt.write(a+0x400c,0xb8000)
                qt.write(a+0x300c,0xb8000)
                qt.write(a+0x3010,1)
                qt.write(c+0xc,0xe3c00)
                check('Separate VID3 and default root registers retain valid VM3 pages',
                      [qt.read(a+0x300c),qt.read(c+0xc)],[0xb8000,0xe3c00])
                st=mmu.runtime_state(qmp,(a,b,c))
                check('Wrong VID, VM4 page and non-early pre-root flush are rejected',
                      [st[hex(a)]['rejected'],st[hex(a)]['flush-all'],st[hex(b)]['flush-all']],[3,1,1])
                m0,m1=mmu.MFC_PORTS
                for base in mmu.MFC_PORTS:
                    qt.write(base+0x2004,0x60)
                    qt.write(base+4,0xf80)
                    qt.write(base+0x300c,0xb8000)
                    qt.write(base+0x3010,1)
                    qt.write(base,5)
                    check('MFC non-VCR resume '+hex(base),
                          [qt.read(base+0x2004),qt.read(base+4),qt.read(base),qt.read(base+8)],
                          [0x60,0xf80,5,0])
                    qt.write(base,7)
                    check('MFC block quiesces with no requester '+hex(base),qt.read(base+8),1)
                    qt.write(base,0)
                for offset,value in ((0x2024,0x20),(0x2028,0x10300),(0x2034,0x10001)):
                    qt.write(m1+offset,value)
                    qt.write(m0+offset,value)
                    check('Original MFC port1 indexed TLB '+hex(offset),qt.read(m1+offset),value)
                qt.write(m1+0x2004,0x62)
                qt.write(a+0x2004,0x60)
                m=mmu.runtime_state(qmp,(m0,m1,a))
                check('MFC port-local register scope and rejected values',
                      [[m[hex(v)][k] for k in ('mfc-default-writes','mfc-index-write-mask',
                                                'mfc-ctrl-writes','rejected','flush-all')]
                       for v in (m0,m1)],[[1,0,3,3,1],[1,7,3,1,1]])
                check('Non-MFC port did not gain the MFC latch',
                      [m[hex(a)]['rejected'],'mfc-default-writes' in m[hex(a)]],[4,False])
            qmp.execute('quit')
            proc.wait(timeout=5)
            if linux_usi_uart:
                check('Distinct UART backends receive each guest FIFO byte',
                      [f.read_bytes().hex() for f in serial_logs],[f'{0x40+i:02x}' for i,_,_,_ in uart.PORTS])
            check('QEMU exits cleanly',proc.returncode,0)
        except Exception as exc:error=repr(exc)
        finally:
            if qt:vm.save(output/'transcript.json',qt.transcript);qt.close()
            if qmp:qmp.close()
            if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
    result={'checks':checks,'passed':error is None,'error':error,'guest_executed':False}
    vm.save(output/'result.json',result)
    if error:raise RuntimeError(error)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=('contract','check'))
    p.add_argument('--mac')
    p.add_argument('--qnx-phy88q2112',action='store_true')
    p.add_argument('--sysmmu',action='store_true')
    p.add_argument('--dpp',action='store_true')
    p.add_argument('--decon-window',action='store_true')
    p.add_argument('--pcie2-elbi',action='store_true')
    p.add_argument('--pcie2-phy',action='store_true')
    p.add_argument('--pcie2-pcs',action='store_true')
    p.add_argument('--pcie2-dbi',action='store_true')
    p.add_argument('--pcie2-atu',action='store_true')
    p.add_argument('--mali-g3d1-empty-power',action='store_true')
    p.add_argument('--linux-sysmmu',action='store_true')
    p.add_argument('--linux-usi-uart',action='store_true')
    p.add_argument('--g2d-reset',action='store_true')
    p.add_argument('--mali-g3d1-config',action='store_true')
    p.add_argument('--mali-g3d1-reset-irq',action='store_true')
    p.add_argument('--mali-g3d1-discovery',action='store_true')
    p.add_argument('--mali-g3d1-id',type=lambda n:int(n,0))
    p.add_argument('--qemu',type=Path,default=vm.ROOT/'runtime/qemu-system-aarch64.exe')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    out=a.output.resolve()
    if not out.is_relative_to(vm.ROOT/'var') or out.exists():raise ValueError('Require new project scratch output')
    out.mkdir(parents=True)
    if a.operation=='contract':
        report=source_contract()
        if a.qnx_phy88q2112:report['phy']=qnx_phy_contract()
        vm.save(out/'result.json',report)
    else:fixture(out,a.qemu,a.mac,a.sysmmu,a.dpp,a.decon_window,a.pcie2_elbi,a.pcie2_phy,a.mali_g3d1_id,a.pcie2_pcs,a.pcie2_dbi,a.pcie2_atu,a.mali_g3d1_discovery,a.mali_g3d1_reset_irq,a.mali_g3d1_config,a.mali_g3d1_empty_power,a.linux_usi_uart,a.g2d_reset,a.linux_sysmmu,a.qnx_phy88q2112)
    print(str(out/'result.json'))


if __name__=='__main__':
    main()

