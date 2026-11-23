
import hashlib
import struct

from vlm_platform import factory_file, original, parse_dtb

BASE = 0x17200000
SIZE = 0x10000
SPI = 199
STATUS_PATH = '/machine/exynos-linux-dwc3/dwc3-xhci'
USB_HOST = ((1, 201, 0x708, 0x710), (2, 191, 0x714, 0x714))


def _host_grants(dt, hcb):
    nodes = parse_dtb(dt)
    result = []
    for port, spi, pmu0, pmu1 in USB_HOST:
        base, phy = BASE + port*0x100000, 0x171c0000 + port*0x10000
        usb, phynode = nodes[f'/usb@{base:08X}'], nodes[f'/phy@{phy:08X}']
        reg, phyreg, irq = struct.pack('>QI',base,SIZE), struct.pack('>QI',phy,SIZE), struct.pack('>III',0,spi,4)
        handle = phynode.get('phandle')
        if (usb.get('reg') != reg or usb.get('interrupts') != irq
                or usb.get('status') != b'okay\0' or usb.get('dr_mode') != b'host\0'
                or usb.get('ip_type') != b'usb2\0' or usb.get('maximum-speed') != b'high-speed\0'
                or usb.get('compatible') != b'samsung,exynos-dwusb\0'
                or not handle or usb.get('phys') != handle+bytes(4)+(handle+struct.pack('>I',1) if port==1 else b'')
                or phynode.get('reg') != phyreg or phynode.get('status') != b'okay\0'
                or phynode.get('phy_version') != struct.pack('>I',0x300)
                or phynode.get('pmu_offset') != struct.pack('>I',pmu0)
                or phynode.get('pmu_offset_2nd') != struct.pack('>I',pmu1)
                or phynode.get('pmu_mask') != bytes(4)):
            raise ValueError(f'Original VM3 USB host{port} DT contract changed')
        for vmid in (2,3,4):
            start = hcb.index(f'VDTVM{vmid}'.encode() + bytes(2)) + 8
            size = struct.unpack_from('>I', hcb, start + 4)[0]
            layout = parse_dtb(hcb[start:start+size])
            prefix = f'/vplatform/vl,udc{port}/vl,udc{port}@'
            mmio, interrupt = layout[prefix+'mem'], layout[prefix+'irq']
            phygrant = layout[f'/vplatform/vl,usbphy{port}/vl,usbphy{port}@mem']
            ranges = tuple(struct.iter_unpack('>QI',phygrant['reg']))
            if (mmio.get('reg') != reg or interrupt.get('interrupts') != irq
                    or not ranges or ranges[0] != (phy,SIZE)
                    or not {(0x10460000+off,4) for off in (pmu0,pmu1)}.issubset(ranges)
                    or any(('vl,disabled' in p) != (vmid!=3) for p in (mmio,interrupt,phygrant))):
                raise ValueError(f'Original VM{vmid} USB host{port} HCB grant changed')
        result.append(dict(port=port,base=base,phy=phy,spi=spi,pmu=[0x10460000+pmu0,0x10460000+pmu1]))
    if nodes['/usb@17500000'].get('status') != b'disabled\0':
        raise ValueError('Original USB3 enablement changed')
    return result

PREFIX = '/vplatform/vl,udc0/vl,udc0@'


def policy(native_linux):
    if not native_linux:
        raise ValueError('DWC3 requires original VM3 Linux and native HCB')
    reg = struct.pack('>QI', BASE, SIZE)
    irq = struct.pack('>III', 0, SPI, 4)
    dt = factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
    node = parse_dtb(dt)['/usb@17200000']
    if (node.get('reg') != reg or node.get('interrupts') != irq
            or node.get('compatible') != b'samsung,exynos-dwusb\0'
            or node.get('dr_mode') != b'otg\0' or node.get('status') != b'okay\0'):
        raise ValueError('Original Linux USB node changed')
    hcb = original('hcb/hcb.img').read_bytes()
    for i in (2, 3, 4):
        start = hcb.index(f'VDTVM{i}'.encode() + bytes(2)) + 8
        size = struct.unpack_from('>I', hcb, start + 4)[0]
        nodes = parse_dtb(hcb[start:start+size])
        mmio, interrupt = nodes[PREFIX+'mem'], nodes[PREFIX+'irq']
        if (mmio.get('reg') != reg or interrupt.get('interrupts') != irq
                or any(('vl,disabled' in p) != (i != 3) for p in (mmio, interrupt))):
            raise ValueError(f'Original VM{i} DWC3 grant changed')
    return dict(profile='upstream-dwc3-auto9-device-config-v3', vm=3, base=BASE, size=SIZE, spi=SPI,
                dctl_offset=0xc704, dctl_reset_mask=1<<30, dctl_reset_latency_ns=50000,
                dcfg_offset=0xc700, dcfg_speed_mask=7,
                hcb_sha256=hashlib.sha256(hcb).hexdigest(), dt_sha256=hashlib.sha256(dt).hexdigest(),
                original_dt_role='otg', model_role='host', downstream_usb=False,
                host_ports=_host_grants(dt,hcb),
                dma='empty-fail-closed', oem_role_switch_and_gadget=False)


def runtime(qmp):
    dma = qmp.execute('qom-get', {'path':STATUS_PATH, 'property':'dma'})
    if not isinstance(dma,str) or not dma.startswith('/machine/exynos-dwc3-dma-empty/'):
        raise ValueError('Upstream DWC3 DMA escaped its empty address space')
    out = {'xhci_path':STATUS_PATH,'dma_address_space':dma}
    path = '/machine/exynos-dwc3-dma-empty'
    names = {row['name'] for row in qmp.execute('qom-list', {'path':path})}
    out.update({key:qmp.execute('qom-get', {'path':path,'property':key})
                for key in ('dctl-reads','dctl-requests','dctl-completions','dcfg','dcfg-reads','dcfg-writes') if key in names})
    machine = {row['name'] for row in qmp.execute('qom-list',{'path':'/machine'})}
    for port,_,_,_ in USB_HOST:
        child=f'exynos-linux-dwc3-{port}'
        if child in machine:
            xhci=f'/machine/{child}/dwc3-xhci'
            addr=qmp.execute('qom-get',{'path':xhci,'property':'dma'})
            if not isinstance(addr,str) or not addr.startswith(f'/machine/exynos-dwc3-dma-empty-{port}/'):
                raise ValueError(f'USB host{port} DMA escaped its empty address space')
            out[f'host-{port}']={'xhci_path':xhci,'dma_address_space':addr}
    return out


PHY_BASE = 0x171c0000
PHY_NODE = '/phy@171C0000'
PHY_HCB = '/vplatform/vl,usbphy0/vl,usbphy0@mem'
PHY_PATH = '/machine/exynos-linux-usb-phy'
PHY_CODE = (
    (0x36b828, 0x68, '2757dcf0bf4499b00442be2eca226c3ac7837c5a72514289dc069e01079d8458'),
    (0x36b690, 104, '303982bded4f7098bec299383b827433acfa0068c84133d286c076d38ebda8d6'),
    (0x36ab90, 72, 'b80d874f06c945eb5587d3f9d584c6661b19084f843f2ee9074e8bf6f76e3149'),
    (0x36da78, 1064, 'a36109ac38dcb2e8fdc0fcddf0dec278256384a418b2a60401ce8c1c8909edf4'),
    (0x36d9e0, 152, '192964fdf9b87117d2fa7c6c89753e7dd0e5466fea5495598c594b069cc8e976'),
)
PHY_OFFSETS = (4, 8, 0x20, 0x50, 0x54, 0x58, 0x5c, 0x100, 0x108, 0x10c, 0x110, 0x11c, 0x48)


def phy_policy(dwc3):
    if not dwc3:
        raise ValueError('USB PHY needs the native Linux DWC3 configuration')
    dt = factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
    nodes = parse_dtb(dt)
    phy, core = nodes[PHY_NODE], nodes['/usb@17200000']
    phandle = phy.get('phandle')
    reg = struct.pack('>QI', PHY_BASE, SIZE)
    if (not phandle or phy.get('compatible') != b'samsung,exynos-usbdrd-phy\0'
            or phy.get('reg') != reg or phy.get('status') != b'okay\0'
            or phy.get('phy_version') != struct.pack('>I', 0x300)
            or phy.get('pmu_offset') != struct.pack('>I', 0x704)
            or phy.get('pmu_offset_2nd') != struct.pack('>I', 0x70c)
            or phy.get('pmu_mask') != bytes(4)
            or core.get('phys') != phandle + bytes(4) + phandle + struct.pack('>I', 1)):
        raise ValueError('Original VM3 DWC3 PHY dependency changed')
    hcb = original('hcb/hcb.img').read_bytes()
    for vmid in (2, 3, 4):
        start = hcb.index(f'VDTVM{vmid}'.encode() + bytes(2)) + 8
        size = struct.unpack_from('>I', hcb, start+4)[0]
        node = parse_dtb(hcb[start:start+size])[PHY_HCB]
        grants = tuple(struct.iter_unpack('>QI',node['reg']))
        if (not grants or grants[0] != (PHY_BASE,SIZE) or not {(0x10460704, 4), (0x1046070c, 4)}.issubset(grants)
                or ('vl,disabled' in node) != (vmid != 3)):
            raise ValueError(f'Original VM{vmid} USB PHY grant changed')
    with factory_file('ivi/Image').open('rb') as image:
        for offset, size, expected in PHY_CODE:
            image.seek(offset)
            if hashlib.sha256(image.read(size)).hexdigest() != expected:
                raise ValueError('Original Linux USB PHY code changed')
    return dict(profile='auto9-v3p1-hs-config-v1', vm=3, base=PHY_BASE, size=SIZE,
                hcb_sha256=dwc3['hcb_sha256'], dt_sha256=dwc3['dt_sha256'],
                reset_offset=0x20, reset_mask=1, original_reset_and_enable=PHY_CODE,
                usb_pmu_isolation_register=0x10460704, usb_pmu_isolation_register_2nd=0x1046070c,
                usb_pmu_isolation_mask=1,
                offsets=PHY_OFFSETS, software_assert_and_release=True,
                electrical_phy=False, external_vbus=False, endpoint=False, rewa_engine=False)


def phy_runtime(qmp):
    fields = ('reads', 'writes', 'read-mask', 'write-mask', 'rejected',
              'reset-assertions', 'reset-releases', 'reset-pulse-ns')
    def capture(path):
        return {key:qmp.execute('qom-get',{'path':path,'property':key}) for key in fields}
    out=capture(PHY_PATH)
    names={row['name'] for row in qmp.execute('qom-list',{'path':'/machine'})}
    for port,_,_,_ in USB_HOST:
        if f'exynos-linux-usb-phy-{port}' in names:
            out[f'host-{port}']=capture(f'{PHY_PATH}-{port}')
    return out
