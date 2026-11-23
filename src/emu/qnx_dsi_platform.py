
import hashlib
import json
import struct

from qnx_dp0_platform import SOURCE, MEMBER_OFFSET, MEMBER_BYTES, MEMBER_SHA256
from vlm_platform import Elf, factory_file, original, parse_dtb

SYSREG = 0x18c11000
BIAS = 0x18e20000
DSIM = (0x18e40000, 0x18e50000)
PHY = (0x18e20100, 0x18e21100)
PATH = '/machine/exynos-qnx-dsi'
DECON_MAIN = (0x18c38000, 0x18c39000)
DECON_SHADOW = (0x18c3a000, 0x18c3b000)
DECON_GLOBAL = (0x18c4a000, 0x18c4b000)
DECON_CONNECTION = 0x18c3c000
DECON_WINDOWS = (0,2,3,4)
DECON_PATH = '/machine/exynos-qnx-decon'
FRAME_START_SPI = (117, 122)
FRAME_END_SPI = (116, 121)
FRAME_PERIOD_NS = (16666616, 16666773)
DPUS_MAIN = 0x1aa38000
DPUS_SHADOW = 0x1aa3a000
DPUS_GLOBAL = 0x1aa4a000
DPUS_CONNECTION = 0x1aa3c000
DPUS_DP0_ROUTE = DPUS_CONNECTION + 0x2000
DPUS_FRAME_START_SPI = 131
DPUS_FRAME_END_SPI = 130
DPUS_ENABLE_SHA = '25105da21372278d3a915cd0c900d7e92d685bd96550fed650457100c93653a0'
DPUS_WINDOWS = (0,7)
DPUS_WINDOW_SHA = 'e2a940995eb38d6562fc859d32d62446dab2190077be64f38e14c5d562a0c8f7'
DECON_CRC_SHA = '63b8a662336130396f24b48e7fff777f5fe7b19a9c02cee300e3d6c4e8448cd9'
DECON_CRC_WORDS = (0x280,0x284,0x288,0x28c)


def policy(native_qnx, dp_aux_sink=False):
    if not native_qnx:
        raise ValueError('DSIM bootstrap requires original QNX VM2 and HCB')
    hcb = original('hcb/hcb.img').read_bytes()
    for i in (2, 3, 4):
        off = hcb.index(f'VDTVM{i}'.encode() + bytes(2)) + 8
        size = struct.unpack_from('>I', hcb, off+4)[0]
        nodes = parse_dtb(hcb[off:off+size])
        sysreg = nodes['/vplatform/vl,disp_phy/vl,io-memory@SYSREG']
        if (sysreg.get('reg') != struct.pack('>QI', SYSREG, 0x10)
                or ('vl,disabled' in sysreg) != (i != 2)):
            raise ValueError(f'Original VM{i} DPHY SYSREG grant changed')
        for pipe, main in enumerate(DECON_MAIN):
            for kind, spis in (('START', FRAME_START_SPI), ('END', FRAME_END_SPI)):
                irq = nodes[f'/vplatform/vl,dpum/vl,irq@DPUM_DECON{pipe}_FRM_{kind}_IRQ']
                if (irq['interrupts'] != struct.pack('>III', 0, spis[pipe], 4)
                        or ('vl,disabled' in irq) != (i != 2)):
                    raise ValueError(f'Original VM{i} DECON{pipe} frame-{kind.lower()} IRQ grant changed')
            node = nodes[f'/vplatform/vl,dpum/vl,io-memory@0x{main:08X}']
            expected = (main, DECON_SHADOW[pipe], DECON_GLOBAL[pipe])
            grants = tuple(struct.iter_unpack('>QI',node['reg']))
            if (grants[:3] != tuple((base,0x1000) for base in expected)
                    or ('vl,disabled' in node) != (i != 2)):
                raise ValueError(f'Original VM{i} DECON{pipe} grant changed')
        for win in DECON_WINDOWS:
            window=0x18c30000+0x1000*win
            node=nodes[f'/vplatform/vl,dpum/vl,io-memory@0x{window:08X}']
            grants=tuple(struct.iter_unpack('>QI',node['reg']))
            if (grants != ((window,0x1000),(window+0x20000,0x1000),(window+0x28000,0x1000))
                    or ('vl,disabled' in node) != (i != 2)):
                raise ValueError(f'Original VM{i} DECON window{win} grant changed')
        con = nodes['/vplatform/vl,dpum/vl,io-memory@0x18C3C000']
        if (con.get('reg') != struct.pack('>QI',DECON_CONNECTION,0x4000)
                or ('vl,disabled' in con) != (i != 2)):
            raise ValueError(f'Original VM{i} DECON connection grant changed')
        if dp_aux_sink:
            prefix = '/vplatform/vl,dpus0/'
            main = nodes[prefix + 'vl,io-memory@0x1AA38000']
            route = nodes[prefix + 'vl,io-memory@0x1AA3C000']
            spi = nodes[prefix + 'vl,irq@DPUS0_DECON0_FRM_START_IRQ']
            end_spi = nodes[prefix + 'vl,irq@DPUS0_DECON0_FRM_END_IRQ']
            regs = (DPUS_MAIN, DPUS_SHADOW, DPUS_GLOBAL, 0x1aa40000, 0x1aa42000, 0x1aa43000)
            if (tuple(struct.iter_unpack('>QI',main['reg'])) != tuple((x,0x1000) for x in regs)
                or route['reg'] != struct.pack('>QI',DPUS_CONNECTION,0x4000)
                or spi['interrupts'] != struct.pack('>III',0,DPUS_FRAME_START_SPI,4)
                or end_spi['interrupts'] != struct.pack('>III',0,DPUS_FRAME_END_SPI,4)
                or any(('vl,disabled' in x) != (i != 2) for x in (main,route,spi,end_spi))):
                raise ValueError(f'Original VM{i} DPUS0 main/route/IRQ grant changed')
            for win in DPUS_WINDOWS:
                address=0x1aa30000+0x1000*win
                entry=nodes[prefix+f'vl,io-memory@0x{address:08X}']
                expected=tuple((address+off,0x1000) for off in (0,0x20000,0x28000))
                if (tuple(struct.iter_unpack('>QI',entry['reg']))!=expected
                    or ('vl,disabled' in entry)!=(i!=2)):
                    raise ValueError(f'Original VM{i} DPUS0 window {win} grant changed')
        bias = nodes['/vplatform/vl,disp_phy/vl,io-memory@DPHY_BIAS']
        if (bias.get('reg') != struct.pack('>QI', BIAS, 0x100)
                or ('vl,disabled' in bias) != (i != 2)):
            raise ValueError(f'Original VM{i} DPHY bias grant changed')
        for link, base in enumerate(DSIM):
            name = f'/vplatform/vl,dpum/vl,io-memory@0x{base:08X}'
            if name not in nodes and i != 2:
                continue
            node = nodes[name]
            if (node.get('reg') != struct.pack('>QIQI', base, 0x1000, PHY[link], 0x700)
                    or ('vl,disabled' in node) != (i != 2)):
                raise ValueError(f'Original VM{i} DSIM grant changed: {base:#x}')
    with factory_file(SOURCE).open('rb') as f:
        f.seek(MEMBER_OFFSET)
        raw = f.read(MEMBER_BYTES)
    if hashlib.sha256(raw).hexdigest() != MEMBER_SHA256:
        raise ValueError('Original DSIM consumer changed')
    elf = Elf(raw)
    if hashlib.sha256(elf.at(0x114d0,0x1a0)).hexdigest()!=DECON_CRC_SHA:
        raise ValueError('Original QNX shared DECON CRC consumer changed')
    if dp_aux_sink:
        for address,size,digest in ((0x10458,0x9b0,DPUS_ENABLE_SHA),
                                    (0xf858,0x130,DPUS_WINDOW_SHA)):
            if hashlib.sha256(elf.at(address,size)).hexdigest()!=digest:
                raise ValueError('Original QNX DPUS0 DECON consumer changed')
    evidence = {hex(a):elf.at(a,n).hex() for a,n in (
        (0x29ea4,16), (0x29f1c,20), (0x290c0,36), (0x2be94,12),
        (0x2bf04,8), (0x2c3b8,44), (0x2a2e4,52), (0x29280,12), (0x2bc30,96))}
    return dict(profile='auto9-qnx-dsim-peripheral-v3', vm=2, sysreg=SYSREG,
                sysreg_offset=8, sysreg_mask=0x33, link_bases=DSIM,
                dphy_bases=PHY, dphy_bias=BIAS, swreset_ns=500, pll_lock_ns=20000,
                hcb_sha256=hashlib.sha256(hcb).hexdigest(),
                original_member_sha256=MEMBER_SHA256, original_instruction_groups=evidence,
                pll='conditional-behavioral-handshake', electrical_phy=False,
                packet_transfer='peripheral-on-off-short-headers-only',
                packet_delay_ns=2000, irq=False, scanout=False,
                decon=dict(main=DECON_MAIN, shadow=DECON_SHADOW, global_register=DECON_GLOBAL,
                           connection=DECON_CONNECTION, reset_ns=2000, update_ns=10000,
                           windows=DECON_WINDOWS, window_shadow_offset=0x800,
                           crc_control=0x290, crc_results=False, frame_count=0x2a0,
                           crc_empty_latches=DECON_CRC_WORDS,crc_consumer_pcs=(0x11564,0x11634),
                           frame_count_without_scanout=0, scanout='direct-single-linear-plane',
                           dpus0=dict(main=DPUS_MAIN,shadow=DPUS_SHADOW,global_register=DPUS_GLOBAL,
                                      route=DPUS_DP0_ROUTE,route_value=0,output_path=8,sst_index=2,
                                      frame_start_spi=DPUS_FRAME_START_SPI,frame_end_spi=DPUS_FRAME_END_SPI,mode=[1560,700],
                                      windows=DPUS_WINDOWS,window_ctl_offset=0x20000,
                                      crc_empty_latches=DECON_CRC_WORDS,crc_results=False,
                                      timing_without_source=True) if dp_aux_sink else None,
                           frame_timing=dict(spis=FRAME_START_SPI, periods_ns=FRAME_PERIOD_NS,
                                             modes=((1120,780,1),(800,400,0)),
                                             enable=0x40,pending_w1c=0x4c,event_bit=12,
                                             frame_end=dict(spis=FRAME_END_SPI,event_bit=13,requires_source=True),
                                             source='OEM WFD modes and HCB IRQ grants'),
                           original_instruction_groups={hex(a):elf.at(a,n).hex() for a,n in
                               ((0x1051c,40),(0x10670,108),(0x105c4,48),(0xfb10,36),
                                (0xfb50,168),(0xfbf8,156))}))


def decon_runtime(qmp):
    if not any(row['name']=='exynos-qnx-decon' for row in qmp.execute('qom-list',{'path':'/machine'})):
        return None
    fields = ('route-writes','dsi0-route','dsi1-route')+tuple(
        f'{field}-{pipe}' for pipe in range(2)
        for field in ('reads','writes','rejected','updates','rejected-updates','running',
                      'active-size','active-dsi'))
    possible={row['name'] for row in qmp.execute('qom-list',{'path':DECON_PATH})}
    fields += tuple(name for name in ('dp0-route','dp0-route-writes','reads-2','writes-2',
        'rejected-2','updates-2','rejected-updates-2','running-2','active-size-2','active-dp-2',
        'timing-ns-2','frame-ticks-2','frame-start-irqs-2','frame-start-acks-2','crc-empty-reads-2')
        if name in possible)
    fields += tuple(k for i in range(2) for kind in ('control','writes','empty-reads')
                    if (k:=f'crc-{kind}-{i}') in possible)
    fields += tuple(name for i in range(2)
                    for key in ('frame-count-reads','timing-ns','frame-pending','frame-ticks',
                                'frame-start-irqs','frame-start-acks','frame-irq-level')
                    if (name:=f'{key}-{i}') in possible)
    fields += tuple(name for i in range(3)
                    for key in ('scanned-frames','scanned-source','scanned-nonzero',
                                'direct-scan-frames','copied-scan-frames',
                                'direct-scan-host-ns','copied-scan-host-ns',
                                'frame-end-irqs','frame-end-acks','frame-end-irq-level')
                    if (name:=f'{key}-{i}') in possible)
    window_fields=('commits','owner','rejected-updates','active-control','active-start','active-end',
                   'source-address','source-control','source-frame-size','source-crop-offset',
                   'source-crop-size','source-output-size','source-writes')
    fields += tuple(name for w in DECON_WINDOWS for suffix in window_fields
                    if (name:=f'window-{w}-{suffix}') in possible)
    fields += tuple(name for w in DPUS_WINDOWS for suffix in window_fields
                    if (name:=f'dpus0-window-{w}-{suffix}') in possible)
    return {key:qmp.execute('qom-get',{'path':DECON_PATH,'property':key}) for key in fields}


def decon_gic_runtime(qmp):

    prop='x-auto9-decon-irq-state'
    matches=[]
    for parent in ('/machine','/machine/unattached'):
        for row in qmp.execute('qom-list', {'path':parent}):
            if row['type']=='child<arm_gic>':
                path=parent+'/'+row['name']
                keys={k['name'] for k in qmp.execute('qom-list', {'path':path})}
                if prop in keys and qmp.execute('qom-get', {'path':path,'property':'num-irq'})>260:
                    matches.append(path)
    if len(matches)!=1:
        raise ValueError('No unique native DECON GICv2 observer')
    path=matches[0]
    rows=json.loads(qmp.execute('qom-get',{'path':path,'property':prop}))
    pairs=(*zip(FRAME_START_SPI,FRAME_END_SPI),(DPUS_FRAME_START_SPI,DPUS_FRAME_END_SPI))
    by_id={row['id']:row for row in rows}
    if len(rows)!=6 or set(by_id)!={spi+32 for pair in pairs for spi in pair}:
        raise ValueError('Unexpected DECON GICv2 observer interrupt IDs')
    return dict(path=path,routes=[dict(pipe=i,start=by_id[start+32],end=by_id[end+32])
                                 for i,(start,end) in enumerate(pairs)],
                scope='Host GICv2 IRQ and virtual LR state; no MMIO, IAR or guest state writes')


def runtime(qmp):
    fields = ('dphy-control','route-reads','route-writes')+tuple(
        f'{field}-{link}' for link in range(2)
        for field in ('reset-requests','reset-completions','clock-writes','lane-writes'))
    optional = ('bias-writes',)+tuple(
        f'{field}-{link}' for link in range(2)
        for field in ('pll-locks','pll-locked','pll-status-reads','phy-writes',
                      'peripheral-on','headers-sent','headers-rejected','headers-cancelled',
                      'last-header'))
    available = {item['name'] for item in qmp.execute('qom-list',{'path':PATH})}
    fields += tuple(key for key in optional if key in available)
    return {key:qmp.execute('qom-get',{'path':PATH,'property':key}) for key in fields}
