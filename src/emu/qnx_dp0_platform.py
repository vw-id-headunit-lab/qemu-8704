
import hashlib
import struct

from vlm_platform import Elf, factory_file, original, parse_dtb

BASES = (0x18A80000, 0x18AA0000)
WINDOW_SIZE = 0xA000
RESET_OFFSET = 4
RESET_NS = 500
PLL_NS = 4000
PHY_TABLE_SHA256 = '89795c7381c1b4ee82a1473c68ecf925d767bac07f35d5d7b4aae23bb4d29127'
SOURCE = 'qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn'
MEMBER_OFFSET = 134209536
MEMBER_BYTES = 304261
MEMBER_SHA256 = '54b40286c32a225bbbab83f218978847170257cc9645cfe509f3f268886821f4'
RESET_CODE = bytes.fromhex(
    '200440b9e203202a420000124000004a200400b902000014a10640f9007d80d2'
    '340440b973060051dec7ff977f021f6be0079f1a9f02006a01ffff5473060035'
)
PATH = '/machine/exynos-dp0-reset'


def phy_defaults(elf):
                                                                              
    raw = struct.pack('<II', 0x830, 7) + elf.at(0x3E090, 0x1A8)
    if hashlib.sha256(raw).hexdigest() != PHY_TABLE_SHA256:
        raise ValueError('Original QNX DP0 PHY default table changed')
    return list(struct.iter_unpack('<II', raw))


LINK_CONFIG = {'software-functions': (0x14, 1), 'common-functions': (0x18, 3),
               'lane-map': (0x3004, 0x3333)}


PLUG_REGISTERS = {'plug-status': (0x108, 0xf00), 'plug-mask': (0x10c, 0xf00),
                  'hpd-config': (0x28, 0x3fff0000)}

AUX_CODE = (
    (0x16fc8, 0x26c, '45562fab50cd414fa8df5d9d5bb39be1be2a91172a332edd6b1fd1ae15e10d11'),
    (0x17580, 0x80, '99b20f758117d4ec13ecaeeaa4460886f5622800c466d06be0029ebd8035c6b4'),
    (0x17600, 0x68, '28b50bc1f9d100b75903a583818f67b8c6783ea8b5190f6bb047d9e0b1094243'),
    (0x17a38, 0x15c, '639235ff1d80a9d0a04e582dca7b52dbb02424b2001304fdcf825c2ce4fd485e'),
    (0x17c80, 0x58, 'a6237905faf5a4ce1d1fd8eeba1b03f6df6197ab4361a9e1d1713cd7883da94d'),
    (0x17d78, 0x78, '4e830066d25b3b17d24a560b6811b633f878ab6a23693be699a6fc7fe212a5f8'),
    (0x17df0, 0x274, 'e3d66a6279961e406b940ebc91269c20c62e03f5c11634a55701e5b667757bc9'),
    (0x183c8, 0xbc, '55d15c3ef4394746718c2232e8eb81af91aa4ba0fd9cdf1020764f6f54bd113e'),
    (0x18488, 0x284, '020c83878baf4d6c7139a0963baf618086bd954f281e615d3a7470dcfea8e23c'),
    (0x18968, 0x2e0, 'd771ea481797f30e04f85994ef005fb2cc935f53662dfe2ba5e640ba0cefefc6'),
    (0x18c48, 0x348, 'dfe76238da2bb46e01b3eb02e92946b5ec90f82ed92dc29bf4055d3e523de412'),
    (0x1a160, 0x1c4, '04e1d380474909af9550455f82fc6f8e12980f98759a98cffc3f8137209290cb'),
    (0x1a4c0, 0xbc, '851b6f818f7efdc68560cc97527cb894f2a5d5321e7f0d205876233bfbfe9eed'),
    (0x1bb38, 0x110, '82cc427aa6f8cc05c157b3e4c99974298fb3b3cb871db022157b3ef619c90494'),
    (0x3df88, 0x100, '93fc2df6850dd884c15ff4ed33b474d445b0a5e6a673e7727889c1fc38323e19'),
    (0x3e2c8, 0xc, '3f91b76ff423e2cbb6e30618fe0f496121cc2456e9101cd5f771f299f2079eeb'),
)
AUX_RUNTIME_KEYS = ('hpd-level', 'hpd-events', 'hpd-irq-level', 'aux-requests', 'aux-replies',
                    'aux-nacks', 'aux-mmio-rejected', 'aux-native-reads', 'aux-native-writes',
                    'aux-i2c-reads', 'aux-i2c-writes', 'aux-last-command', 'aux-last-address',
                    'link-phase', 'link-trained', 'source-lane-count-value', 'source-lane-count-writes',
                    'source-pcs-control-value', 'source-pcs-control-writes', 'phy-tune-writes')
SST_RUNTIME_KEYS = tuple(prefix.format(i) for i in range(4) for prefix in (
    'sst{}-timing-writes', 'sst{}-timing-420', 'sst{}-timing-430', 'sst{}-video-enable',
    'source-enable-sst{}-value', 'source-functions-sst{}-value'))


def policy(native_qnx, phy=False, link_config=False, irq=False, aux_sink=False):
    if aux_sink and not irq:
        raise ValueError('Virtual DP0 AUX sink requires the native plug interrupt register path')
    if irq and not link_config:
        raise ValueError('DP0 plug registers require DP0 LINK configuration')
    if link_config and not phy:
        raise ValueError('DP0 LINK configuration requires DP0 PHY')
    if not native_qnx:
        raise ValueError('DP0 reset requires original QNX VM2 with native board and HCB')
    raw = original('hcb/hcb.img').read_bytes()
    prefix = '/vplatform/vl,dpus0/'
    regs = struct.pack('>QIQI', BASES[0], WINDOW_SIZE, BASES[1], WINDOW_SIZE)
    for i in (2, 3, 4):
        start = raw.index(f'VDTVM{i}'.encode() + bytes(2)) + 8
        size = struct.unpack_from('>I', raw, start + 4)[0]
        tree = parse_dtb(raw[start:start + size])
        mem = tree[prefix + 'vl,io-memory@EXYNOS_DP0']
        irq = tree[prefix + 'vl,irq@EXYNOS_DP0_IRQ']
        if (mem['reg'] != regs or mem['vl,alignment'] != struct.pack('>I', 4)
                or irq['interrupts'] != struct.pack('>III', 0, 111, 4)
                or any(('vl,disabled' in node) != (i != 2) for node in (mem, irq))):
            raise ValueError(f'Original VM{i} DP0 grant changed')
    with factory_file(SOURCE).open('rb') as source:
        source.seek(MEMBER_OFFSET)
        driver = source.read(MEMBER_BYTES)
    if (hashlib.sha256(driver).hexdigest() != MEMBER_SHA256
            or Elf(driver).at(0x16940, len(RESET_CODE)) != RESET_CODE):
        raise ValueError('Original QNX DP0 reset instructions changed')
    if aux_sink:
        elf = Elf(driver)
        if any(hashlib.sha256(elf.at(off,n)).hexdigest()!=digest for off,n,digest in AUX_CODE):
            raise ValueError('Original QNX DP0 register consumers changed')
    if phy:
        elf = Elf(driver)
        defaults = phy_defaults(elf)
        if struct.unpack('<4I', elf.at(0x3DEE0, 16)) != (6, 10, 20, 30):
            raise ValueError('Original QNX DP0 PHY rate selector changed')
        if len(defaults) != 54 or dict(defaults).get(0x38c) != 2:
            raise ValueError('Unknown original DP0 PHY initialization')
    return dict(profile='virtual-qnx-dp0-phy-v1' if phy else 'virtual-qnx-dp0-swreset-v1', vm=2, hcb_bases=BASES,
                window_size=WINDOW_SIZE, reset_word=BASES[0] + RESET_OFFSET,
                mask=1, virtual_reset_ns=RESET_NS, hcb_irq_spi=111,
                hcb_sha256=hashlib.sha256(raw).hexdigest(), member_sha256=MEMBER_SHA256,
                original_reset_rmw_va=0x16940, original_reset_poll_va=0x16960,
                physical_timing_verified=False, phy=phy,
                link_config=LINK_CONFIG if link_config else None,
                plug_registers=PLUG_REGISTERS if irq else None,
                hpd_sink_connected=False,
                virtual_aux_sink=dict(mode=[1560,700],virtual_edid=True,dpcd_version='1.2',
                    rate='HBR',lanes=4,guest_abi=AUX_CODE) if aux_sink else None,
                phy_default_table_sha256=PHY_TABLE_SHA256 if phy else None,
                phy_link_rate_mask=0x60 if phy else None,
                pll_status_mask=0x10 if phy else None,
                pll_lock_polarity="active-high" if phy else None,
                virtual_pll_ns=PLL_NS if phy else None, link=False, irq=False,
                scanout=False)


def runtime(qmp, phy=False, link_config=False, irq=False, aux_sink=False):
    keys = ('busy', 'reads', 'writes', 'requests', 'completions')
    if phy:
        keys += ('phy-control', 'phy-rate', 'phy-reads', 'phy-writes', 'pll-reads',
                 'pll-locked', 'pll-requests', 'pll-completions')
    if link_config:
        keys += ('phy-release-seen', 'phy-release-gap-ns', 'phy-release-config-count',
                 'phy-mode', 'phy-extra-seen')
        keys += tuple(f'{word}-{field}' for word in LINK_CONFIG
                      for field in ('value', 'reads', 'writes'))
    if irq:
        keys += tuple(f'{word}-{field}' for word in PLUG_REGISTERS
                      for field in ('value', 'reads', 'writes'))
    if aux_sink:
        keys += AUX_RUNTIME_KEYS + SST_RUNTIME_KEYS
    return {k: qmp.execute('qom-get', {'path': PATH, 'property': k}) for k in keys}
