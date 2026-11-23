
import hashlib
from pathlib import Path
import re
import struct

import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE='virtual-pcie2-elbi-cold-v1'
PHY_PROFILE='virtual-pcie2-phy-cold-v1'
PCS_PROFILE='virtual-pcie2-pcs-no-endpoint-v1'
DBI_PROFILE='virtual-pcie2-dbi-root-latches-v1'
ATU_PROFILE='virtual-pcie2-unrolled-empty-v1'
PHY_PMU=0x10460000
PHY_OFFSET=0x71c
PATH='/machine/exynos-pcie2-elbi'
BASE=0x17a80000
SIZE=0x1be8
WORDS=(0x10,0x14,0x18)

def policy(profile, native_linux):
    if profile is None:
        return None
    if profile!=PROFILE or not native_linux:
        raise ValueError('PCIe2 cold ELBI needs the original native Linux/HCB')
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    nodes=parse_dtb(dt.read_bytes())
    path='/pcie@17A80000'
    root_ports=[p for p,row in nodes.items() if p.startswith('/pcie@') and row.get('status')==b'okay\0']
    if root_ports!=[path]:
        raise ValueError('Unexpected enabled original PCIe RC set')
    node=nodes[path]
    if node['reg-names'].split(b'\0')[0]!=b'elbi' or next(struct.iter_unpack('>QI',node['reg']))!=(BASE,SIZE):
        raise ValueError('PCIe2 ELBI DT binding changed')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    start=raw.index(b'VDTVM3\0\0')+8
    vm3=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    grant='/vplatform/vl,pcie2/vl,pcie2_01@mem'
    if 'vl,disabled' in vm3[grant] or next(struct.iter_unpack('>QI',vm3[grant]['reg']))!=(BASE,SIZE):
        raise ValueError('PCIe2 ELBI not allowed by original VM3 HCB')
    return dict(profile=profile,linux_node=path,hcb_node=grant,base=BASE,size=SIZE,offsets=WORDS,
                linux_dtb_sha256=vm.sha(dt),hcb_sha256=vm.sha(hcb),link=False,endpoint=False,irq=False,dma=False)

def phy_policy(profile, elbi, cold_pmu):
    if profile is None:
        return None
    if profile!=PHY_PROFILE or not (elbi and cold_pmu):
        raise ValueError('PCIe2 PHY control requires the original ELBI and cold PMU')
    dt=parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())
    rc=dt[elbi['linux_node']]
    syscon=dt['/system-controller@10460000']
    if (rc['pmu-phy-offset']!=struct.pack('>I',PHY_OFFSET) or
        syscon['phandle']!=rc['samsung,syscon-phandle'] or
        next(struct.iter_unpack('>QI',syscon['reg']))!=(PHY_PMU,0x10000)):
        raise ValueError('Original Linux PCIe PMU resource changed')
    raw=original('hcb/hcb.img').read_bytes()
    start=raw.index(b'VDTVM3\0\0')+8
    tree=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    path='/vplatform/vl,pcie_common/vl,pcie_2_3_common@mem'
    if 'vl,disabled' in tree[path] or (PHY_PMU+PHY_OFFSET,4) not in struct.iter_unpack('>QI',tree[path]['reg']):
        raise ValueError('Original VM3 PCIe PMU resource grant changed')
    return dict(profile=profile,address=PHY_PMU+PHY_OFFSET,hcb_node=path,bit_mask=1,
                cold_reset=0,link=False,pll=False,irq=False)

def phy_runtime_state(qmp):
    return {k:qmp.execute('qom-get',dict(path='/machine/exynos-pmu',property=k))
            for k in ('pcie23-phy-control','pcie23-phy-reads','pcie23-phy-writes')}

PCS_AREAS={
    'elbi':(0x17a80000,0x1be8),'elbi_cmn':(0x17a81404,8),
    'phy':(0x17b10000,0x1544),'sysreg':(0x17730804,4),'pcs':(0x17ad0000,0xcb0),
}
PCS_STATE=('cold-reads','cold-writes','cold-rejected','cold-last-address','cold-last-value',
           'cold-last-write','cold-last-rejected-address')
PCS_CODE_SHA='f912b46265747d820d4bfefa9e713b6a5346cbe73c76e3da704ca2905503df26'
PCS_LATE_ELBI=(0xffffffc010a7f9e4,0x148,'353dd4928cd0c93d20adf92ffdb7cc8fcc0d8ce04287ec41296198311ef9ab63')
PCS_ELBI_READL=(0xffffffc0104296e8,8,'9bb31e50c9e4b1e6d3afbc308de8dce185c4ece3c405e5fc1e3b97b52d26c6a0')
PCS_ELBI_3BC=(0xffffffc010a7fbb0,0x74,'57ece1ff13db9bca88bbe5d8cc8795ec0a6d004c08478f1e2a5165fabce41fd5')
PCS_ELBI_2E8=(0xffffffc010a7fc34,0x94,'67d72cf504c40f6432b020126207db331aa1209521c2dff76dce093484dcdc0b')
PCS_ELBI_54_CLR=(0xffffffc010a7fea0,0x34,'5d40c1ce2089e4ee46d9e7b540c03d4a491a8c6119785f0dd6f8f9b3079aa09e')
PCS_ELBI_C88_DUMP=(0xffffffc01042b8f8,0x60,'1b6b221b37f156b279b79408d4d363a81fbeced0acafcf26ffed0190db9b710a')

def pcs_policy(profile, elbi, phy):
    if profile is None:
        return None
    if profile!=PCS_PROFILE or not (elbi and phy):
        raise ValueError('PCIe2 PCS configuration requires native ELBI/PMU profiles')
    dt=parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())[elbi['linux_node']]
    regs=dict(zip(dt['reg-names'].rstrip(bytes(1)).decode().split(chr(0)),struct.iter_unpack('>QI',dt['reg'])))
    if (any(regs[k]!=v for k,v in PCS_AREAS.items()) or dt['ch-num']!=struct.pack('>I',2)
            or dt['num-lanes']!=struct.pack('>I',2)
            or any(dt[name]!=b'false'+bytes(1) for name in ('use-bifurcation','use-sris','use-ia'))):
        raise ValueError('Original PCIe2 resource/variant changed')
    hcb=original('hcb/hcb.img').read_bytes()
    offset=hcb.index(b'VDTVM3'+bytes(2))+8
    tree=parse_dtb(hcb[offset:offset+struct.unpack_from('>I',hcb,offset+4)[0]])
    grant='/vplatform/vl,pcie_common/vl,pcie_2_3_common@mem'
    if 'vl,disabled' in tree[grant] or not (set(PCS_AREAS.values())-{PCS_AREAS['elbi']})<=set(struct.iter_unpack('>QI',tree[grant]['reg'])):
        raise ValueError('PCIe2 shared aperture no longer granted by original VM3 HCB')
    kernel=factory_file('ivi/Image')
    original_code=((0xffffffc010428e10,2264,PCS_CODE_SHA),)+((18446743799111086044, 128, '048e9a9385efeb7cc45eaeb5924b2d903b75779537dc3dbf011437ba66215a4e'), (18446743799104441392, 368, 'f568e9ade1f9bef0ce9065cb6e1246f6950f3d8edeef6573deba957d76e20b4c'))
    original_code+=(PCS_LATE_ELBI,PCS_ELBI_READL,PCS_ELBI_3BC,PCS_ELBI_2E8,PCS_ELBI_54_CLR,PCS_ELBI_C88_DUMP)
    with kernel.open('rb') as stream:
        for address,size,digest in original_code:
            stream.seek(address-0xffffffc010080000)
            if hashlib.sha256(stream.read(size)).hexdigest()!=digest:
                raise ValueError(f'Original PCIe2 config/reset bytes changed at {address:#x}')
    impl=Path(__file__).with_name('exynos-auto9-pcie2-elbi.inc.c').read_text(encoding='utf-8')
    words=[]
    for address,rd,n,a,b in re.findall(r'^    \{(0x[0-9a-f]{8}), ([01]), ([012]), (0x[0-9a-f]+), (0x[0-9a-f]+)\},$',impl,re.M):
        words.append(dict(address=int(address,16),read=bool(int(rd)),writes=[int(a,16),int(b,16)][:int(n)]))
    if len(words)!=121 or [r['address'] for r in words]!=sorted({r['address'] for r in words}):
        raise ValueError('Unexpected exact cold configuration table')
    return dict(profile=profile,words=words,areas=PCS_AREAS,original_code_address='0xffffffc010428e10',
                original_code_sha256=PCS_CODE_SHA,original_config_and_reset_code=original_code,
                kernel_sha256=vm.sha(kernel),hcb_shared_node=grant,
                virtual_cold_status=0,link=False,pll=False,irq=False,endpoint=False,dma=False)

def pcs_runtime_state(qmp):
    return {key:qmp.execute('qom-get',dict(path=PATH,property=key)) for key in PCS_STATE}

DBI_AREAS={'dbi':(0x13000000,0x5000),'dbi_shadow':(0x13200000,0x1000)}
DBI_CODE_SHA='5eb81fbdd4cde8db56d7788e6548739cf99149b0eafd8394f51021a5e0516f9f'
DBI_STATE=('dbi-reads','dbi-writes','dbi-rejected','dbi-last-address','dbi-last-value',
           'dbi-last-write','dbi-rejected-address','dbi-guest-vendor-device')

def dbi_policy(profile, pcs):
    if profile is None:
        return None
    if profile!=DBI_PROFILE or not pcs:
        raise ValueError('PCIe2 DBI root latches require the native cold PCS profile')
    node=parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())['/pcie@17A80000']
    regs=dict(zip(node['reg-names'].rstrip(bytes(1)).decode().split(chr(0)),struct.iter_unpack('>QI',node['reg'])))
    if any(regs[k]!=r for k,r in DBI_AREAS.items()):
        raise ValueError('Original DBI windows changed')
    raw=original('hcb/hcb.img').read_bytes()
    offset=raw.index(b'VDTVM3'+bytes(2))+8
    tree=parse_dtb(raw[offset:offset+struct.unpack_from('>I',raw,offset+4)[0]])
    grant='/vplatform/vl,pcie2/vl,pcie2_02@mem'
    if 'vl,disabled' in tree[grant] or not set(DBI_AREAS.values())<=set(struct.iter_unpack('>QI',tree[grant]['reg'])):
        raise ValueError('Original VM3 DBI grants changed')
    with factory_file('ivi/Image').open('rb') as f:
        f.seek(0xffffffc010a7f0d8-0xffffffc010080000)
        if hashlib.sha256(f.read(664)).hexdigest()!=DBI_CODE_SHA:
            raise ValueError('Original exynos_pcie_setup_rc bytes changed')
    return dict(profile=profile,areas=DBI_AREAS,grant=grant,ro_write_enable=0x8bc,
                original_setup_rc='0xffffffc010a7f0d8',original_setup_rc_sha256=DBI_CODE_SHA,
                expected_guest_programmed_id='ecee:144d (device:vendor)', host_preseeded_id=False,
                link=False,downstream_enumeration=False,msi_delivery=False,dma=False)

def dbi_runtime_state(qmp):
    return {key:qmp.execute('qom-get',dict(path=PATH,property=key)) for key in DBI_STATE}

ATU_AREAS={'atu':(0x13600000,0x1000),'config':(0x5fffd000,0x2000)}
ATU_MEMORY=(0x40000000,0x1fffd000)
ATU_GRANT=(0x40000000,0x20000000)
ATU_SOURCE=(
    (0xffffffc0104262f0,496,'1bf945b515fc857b9fbc76fe43ef58fce86630182d6d275dc9a26eb57ccd949f'),
    (0xffffffc010426830,648,'6417282c2427a90919d90d12625b470ab0aa25ba18a10451a16bfd4fd573f3c9'),
    (0xffffffc0104296f0,160,'3139eca93e4f7fc41dc8d0311007e88d6adc0b56d7d2cb23f0f2a3f9d0125a42'),
    (0xffffffc010429790,152,'ca9471dc7633e4a0b59cea6f9a48b9bbc8bda649154d220d8fd241ab76962daf'),
    (0xffffffc010427260,1520,'42a6fb4406e7043a1796b12654617e24a7021f64663c4533fa3ce64238e6f20f'),
)
ATU_STATE=('atu-discovery-reads','root-header-reads','atu-reads','atu-writes','atu-rejected','atu-active',
           'atu-last-offset','atu-last-value','cfg-absent-reads','cfg-absent-writes','cfg-rejected')

def atu_policy(profile, dbi):
    if profile is None:
        return None
    if profile!=ATU_PROFILE or not dbi:
        raise ValueError('PCIe2 iATU requires the original native DBI profile')
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    node=parse_dtb(dt.read_bytes())['/pcie@17A80000']
    regs=dict(zip(node['reg-names'].rstrip(bytes(1)).decode().split(chr(0)),struct.iter_unpack('>QI',node['reg'])))
    ranges=tuple(struct.iter_unpack('>I',node['ranges']))
    if (any(regs[key]!=area for key,area in ATU_AREAS.items()) or
            ranges!=tuple((w,) for w in (0x82000000,0,ATU_MEMORY[0],0,ATU_MEMORY[0],0,ATU_MEMORY[1]))):
        raise ValueError('Original PCIe2 config/ATU/MEM ranges changed')
    hcb=original('hcb/hcb.img').read_bytes()
    offset=hcb.index(b'VDTVM3'+bytes(2))+8
    tree=parse_dtb(hcb[offset:offset+struct.unpack_from('>I',hcb,offset+4)[0]])
    grant=dbi['grant']
    ranges=set(struct.iter_unpack('>QI',tree[grant]['reg']))
    if 'vl,disabled' in tree[grant] or not {ATU_AREAS['atu'],ATU_GRANT}<=ranges:
        raise ValueError('Original VM3 PCIe iATU grant changed')
    with factory_file('ivi/Image').open('rb') as stream:
        for address,length,digest in ATU_SOURCE:
            stream.seek(address-0xffffffc010080000)
            if hashlib.sha256(stream.read(length)).hexdigest()!=digest:
                raise ValueError(f'Original PCIe iATU/host code changed at {address:#x}')
    return dict(profile=profile,windows=ATU_AREAS,outbound_memory=ATU_MEMORY,
                pci_aperture_hcb=ATU_GRANT,original_sources=ATU_SOURCE,dt_sha256=vm.sha(dt),
                discovery=0xffffffff,root_header_type=1,banks=8,secondary_devices=[],
                host_preseeded_id=False,link=False,inbound_dma=False,msi_delivery=False)

def activate_atu(qmp):
    query=dict(path=PATH,property='pci-aperture-active')
    before=qmp.execute('qom-get',query)
    if before:
        raise ValueError('PCI aperture was already active during original LK')
    qmp.execute('qom-set',dict(query,value=True))
    if not qmp.execute('qom-get',query):
        raise ValueError('PCI aperture activation failed')
    return dict(active_before=False,active_after=True,phase='after original LK, before original vLM')

def atu_runtime_state(qmp):
    return {key:qmp.execute('qom-get',dict(path=PATH,property=key)) for key in ATU_STATE}

def runtime_state(qmp):
    props=tuple(f'{kind}-{off:x}' for off in WORDS for kind in ('config','reads','writes'))+(
        'rejected','last-rejected','last-rejected-value','last-rejected-write')
    return {key:qmp.execute('qom-get',dict(path=PATH,property=key)) for key in props}

