
import hashlib
import struct

import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE='virtual-dpus0-dpp-irq-v1'
PATH='/machine/exynos-dpp-irqs'
PORTS=((0x1aa21000,0x1aa65000),(0x1aa22000,0x1aa61000),
       (0x1aa23000,0x1aa62000),(0x1aa24000,0x1aa66000))
MASKS=(0x43,0x7f7)
PENDING=(0x00210000,0x03fb0000)


def policy(profile, native_android):
    if profile is None:return None
    if profile!=PROFILE or not native_android:
        raise ValueError('DPUS0 DPP IRQ needs original Android with the native SysMMU profile')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    start=raw.index(b'VDTVM4\0\0')+8
    table=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    ranges=[]
    for k,row in table.items():
        if k.startswith('/vplatform/vl,dpus0/') and 'reg' in row and 'vl,disabled' not in row:
            ranges.extend((base,size,k) for base,size in struct.iter_unpack('>QI',row['reg']))
    vendor=factory_file('android/vendor_boot.img').read_bytes()
    dt_size=struct.unpack_from('<I',vendor,2100)[0]
    hdr_size,page,rd_size=(struct.unpack_from('<I',vendor,i)[0] for i in (2096,12,24))
    dt_start=((hdr_size+page-1)//page+(rd_size+page-1)//page)*page
    dt=vendor[dt_start:dt_start+dt_size]
    paths={}
    for name,props in parse_dtb(dt).items():
        if not props.get('compatible',b'').startswith(b'samsung,exynos9-dpp-') or props.get('status',b'okay\0') not in (b'okay\0',b'ok\0'):
            continue
        idma,dpp,common=tuple(struct.iter_unpack('>QI',props['reg']))
        if {idma[1],dpp[1],common[1]}!={0x1000} or common[0]!=0x1aa60000:
            raise ValueError(f'Unexpected original DPP mapping: {name}')
        paths[(dpp[0],idma[0])]=name
    if tuple(sorted(paths))!=PORTS:
        raise ValueError('Original active DPP/IDMA address set changed')
    words=[]
    for pair in PORTS:
        for kind,base in enumerate(pair):
            address=base+4
            matches=[k for b,n,k in ranges if b<=address and address+4<=b+n]
            if len(matches)!=1:raise ValueError(f'Unbound or ambiguous DPP register: {address:#x}')
            words.append({'address':address,'kind':('dpp','idma')[kind],'android_node':paths[pair],
                          'hcb_node':matches[0],'config_mask':MASKS[kind],'w1c_mask':PENDING[kind]})
    return {'profile':profile,'factory_hcb_sha256':vm.sha(hcb),'factory_android_dtb_sha256':hashlib.sha256(dt).hexdigest(),
            'words':words,'scanout':False,'dma':False,'irq_assertion':False}


def runtime_state(qmp):
    return {hex(base+4):{key:qmp.execute('qom-get',{'path':PATH,'property':f'{key}-{i}'})
                        for key in ('reads','writes','config','pending')}
            for i,base in enumerate(base for pair in PORTS for base in pair)}


LINUX_PROFILE='virtual-linux-dpp-irq-v1'
LINUX_PATH='/machine/exynos-linux-dpp-irqs'
LINUX_PORTS=((0x18c22000,0x18c61000,0x18c60000,232),
             (0x1aa25000,0x1aa63000,0x1aa60000,263),
             (0x1aa27000,0x1aa67000,0x1aa60000,263))
LINUX_CODE=((0x47bd20,240,'0b1511026dd4f4cc4b945af50815b7613ede4570e1f8077a2f92772ac0660d52'),
            (0x486a88,728,'305fc9f51fcfd996ba0872bb02b4ed6751fc0d6f84ad4dc03d1a5ce0e7004efd'),
            (0x47ba18,0xe8,'fa1e0b010056f2a13474c2c6fb9aa817ceaa577a522f0915919f55c379c46a75'),
            (0x47bb00,0x220,'ec04658c52d26b11be13a5c2ada8b4540a40b8daddd6747cfcdcf04c94e39f9f'),
            (0x47b5e0,0x438,'3815aa4627cb9aa1ef8b3a864043eceba8c5cfa03033bce9cb07f3ed09fc5226'),
            (0x47bfa0,0x1c8,'5e3d2063ccf34c8018670dcee394fd721042a91806bc50a773a29c21db5feec7'))
LINUX_CONFIG_OFFSETS={
    'dpp':(0x00,0x08,0x18,0xa54,0xd00),
    'idma':(0x00,0x08,0x0c,0x10,0x14,0x18,0x24,0x28,0x30,
            0x40,0x44,0x48,0x4c,0x58,0x5c,0x68),
    'common':(0x20,0x24,0x28,0x2c,0x70,0x74,0x80),
}


def linux_policy(profile, linux_sysmmu):
    if profile is None:
        return None
    if profile!=LINUX_PROFILE or not linux_sysmmu:
        raise ValueError('VM3 DPP words require the original Linux/SysMMU profile')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    start=raw.index(b'VDTVM3\0\0')+8
    table=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    ranges=[(base,size,k) for k,row in table.items()
            if k.startswith(('/vplatform/vl,dpum/','/vplatform/vl,dpus0/'))
            and 'reg' in row and 'vl,disabled' not in row
            for base,size in struct.iter_unpack('>QI',row['reg'])]
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    paths={}
    for k,row in parse_dtb(dt.read_bytes()).items():
        if (not row.get('compatible',b'').startswith(b'samsung,exynos9-dpp-') or
                row.get('status',b'okay\0')!=b'okay\0'):
            continue
        idma,dpp,common=tuple(struct.iter_unpack('>QI',row['reg']))
        typ,spi,flags=struct.unpack('>III',row['interrupts'])
        if {idma[1],dpp[1],common[1]}!={0x1000} or (typ,flags)!=(0,4):
            raise ValueError('Unexpected original VM3 DPP mapping')
        paths[(dpp[0],idma[0],common[0],spi)]=k
    if tuple(sorted(paths))!=LINUX_PORTS:
        raise ValueError('Original Linux DPP aperture set changed')
    kernel=factory_file('ivi/Image')
    with kernel.open('rb') as f:
        for offset,size,digest in LINUX_CODE:
            f.seek(offset)
            if hashlib.sha256(f.read(size)).hexdigest()!=digest:
                raise ValueError('Original Linux DPP consumer changed')
    words=[]
    config={}
    for port in LINUX_PORTS:
        for kind,base in enumerate(port[:3]):
            label=('dpp','idma','common')[kind]
            window=[k for b,n,k in ranges if b<=base and base+0x1000<=b+n]
            if not window or (kind!=2 and len(window)!=1):
                raise ValueError(f'Unbound VM3 DPP configuration page {base:#x}')
            config[base]=dict(kind=label,offsets=LINUX_CONFIG_OFFSETS[label],hcb_nodes=window)
            if kind==2:
                continue
            addr=base+4
            matches=[k for b,n,k in ranges if b<=addr and addr+4<=b+n]
            if len(matches)!=1:
                raise ValueError(f'Unbound VM3 DPP register {addr:#x}')
            words.append(dict(address=addr,kind=('dpp','idma')[kind],guest_node=paths[port],
                              hcb_node=matches[0],config_mask=MASKS[kind],w1c_mask=PENDING[kind]))
    return dict(profile=profile,original_hcb_sha256=vm.sha(hcb),original_linux_dtb_sha256=vm.sha(dt),
                original_code=LINUX_CODE,words=words,config=config,
                scanout=False,dma=False,irq_assertion=False)


def linux_runtime(qmp):
    return {hex(base+4): {key:qmp.execute('qom-get',{'path':LINUX_PATH,'property':f'{key}-{i}'})
                          for key in ('reads','writes','config','pending')}
            for i,base in enumerate(base for port in LINUX_PORTS for base in port[:2])}


def linux_config_runtime(qmp):
    present={p['name'] for p in qmp.execute('qom-list',{'path':LINUX_PATH})}
    result={}
    for port in LINUX_PORTS:
        for i,base in enumerate(port[:3]):
            prefix=f'linux-page-{base:x}-'
            keys=('writes','rejected','resets')+tuple(f'{off:03x}' for off in
                        LINUX_CONFIG_OFFSETS[('dpp','idma','common')[i]])
            if hex(base) not in result and all(prefix+key in present for key in keys):
                result[hex(base)]={key:qmp.execute('qom-get',{'path':LINUX_PATH,'property':prefix+key})
                                   for key in keys}
    return result


QNX_PATH = '/machine/exynos-qnx-dpp-irqs'
QNX_PORTS = (
    (0x18c27000, 0x18c67000, 'dpum', '0x18C27000'),
    (0x18c25000, 0x18c63000, 'dpum', 'DPUM_DPP_G2'),
    (0x18c28000, 0x18c68000, 'dpum', '0x18C28000'),
    (0x18c26000, 0x18c64000, 'dpum', 'DPUM_DPP_G3'),
    (0x1aa28000, 0x1aa68000, 'dpus0', '0x1AA28000'),
    (0x1aa26000, 0x1aa64000, 'dpus0', 'DPUS0_DPP_G3'),
)
QNX_CODE = (
    (0x23bb8, 160, '179962b9d735d15ba80a06da55cd5847c641840c67183ae47d1831d163c4c1de'),
    (0x23c58, 340, '9530175d1163216d6e15c6dbabf6c34c1abbecfed370faa58b5fcb5d9dda2a31'),
)
QNX_ENABLE_CODE=(
    (0x23ab0,0x108,'06c4ac7ea0ceca835a992887720afcc026a9f5cefd9cc7c5096b301f95b93f8a'),
    (0x23920,0x18c,'0af28cd8abcf15e715a9cd51cccf71f4a09a7871f26b38dcd7e777c30b83effc'),
    (0x23fd8,0x264,'42a3d7a524b450d49cc38fee9f530009cb980081d56e44bd7bbe18bfa5e89477'),
    (0x23db0,0x224,'0a2ceba3ab3fadee61dc843f69fa47c6f1550e48ea88451a5d952c88262a909f'),
)
QNX_COMMON=((0x18c60000,'dpum','DPU_DMA_COMMON'),(0x1aa60000,'dpus0','0x1AA60000'))
QNX_CONFIG_CODE=(0x23668,0x2b4,'d2cf721625d0c0e0032dac7230fd1994a8209614607a66cd8b4edd47b91746ed')
QNX_CONFIG_OFFSETS=((0x00,0x08,0x18,0xa54,0xd00),
                    (0x00,0x08,0x0c,0x10,0x14,0x18,0x24,0x28,0x30,0x40,0x44,0x48,0x4c,0x58,0x5c,0x68))
QNX_IC_SOURCE=(1,2,0x18c27000,0x18c67000)


def qnx_policy(native_qnx):
    from qnx_dp0_platform import SOURCE, MEMBER_OFFSET, MEMBER_BYTES, MEMBER_SHA256
    from vlm_platform import Elf
    if not native_qnx:
        raise ValueError('VM2 DPP requires the original QNX IFS and HCB')
    with factory_file(SOURCE).open('rb') as f:
        f.seek(MEMBER_OFFSET)
        raw = f.read(MEMBER_BYTES)
    if hashlib.sha256(raw).hexdigest() != MEMBER_SHA256:
        raise ValueError('Original QNX DPP consumer changed')
    elf = Elf(raw)
    for address, size, digest in (*QNX_CODE,QNX_CONFIG_CODE,*QNX_ENABLE_CODE):
        if hashlib.sha256(elf.at(address, size)).hexdigest() != digest:
            raise ValueError('Original QNX DPP interrupt sequence changed')
    hcb = original('hcb/hcb.img').read_bytes()
    trees = {}
    for vmid in (2, 3, 4):
        start = hcb.index(f'VDTVM{vmid}'.encode() + bytes(2)) + 8
        size = struct.unpack_from('>I', hcb, start + 4)[0]
        trees[vmid] = parse_dtb(hcb[start:start + size])
    foreign = [
        (base, size) for i in (3, 4) for path, node in trees[i].items()
        if path.startswith(('/vplatform/vl,dpum/', '/vplatform/vl,dpus0/'))
        and 'reg' in node and 'vl,disabled' not in node
        for base, size in struct.iter_unpack('>QI', node['reg'])
    ]
    words = []
    for dpp, idma, fabric, name in QNX_PORTS:
        path = f'/vplatform/vl,{fabric}/vl,io-memory@{name}'
        node = trees[2][path]
        if 'vl,disabled' in node or node['reg'] != struct.pack('>QIQI', idma, 0x1000, dpp, 0x1000):
            raise ValueError(f'Original VM2 DPP grant changed: {path}')
        for kind, base in enumerate((dpp, idma)):
            for offset in (4,*QNX_CONFIG_OFFSETS[kind]):
                if any(start <= base+offset < start+size for start,size in foreign):
                    raise ValueError(f'VM2 DPP word overlaps another domain: {base+offset:#x}')
            words.append(dict(address=base+4, kind=('dpp','idma')[kind], hcb_node=path,
                              config_mask=MASKS[kind], w1c_mask=PENDING[kind]))
    for base,fabric,name in QNX_COMMON:
        node=trees[2][f'/vplatform/vl,{fabric}/vl,io-memory@{name}']
        if 'vl,disabled' in node or node['reg']!=struct.pack('>QI',base,0x1000):
            raise ValueError('Original shared QNX DPP common bank changed')
    return dict(profile='virtual-qnx-dpp-irq-control-v3', original_hcb_sha256=hashlib.sha256(hcb).hexdigest(),
                original_member_sha256=MEMBER_SHA256, original_code=(*QNX_CODE,QNX_CONFIG_CODE,*QNX_ENABLE_CODE),
                shared_common=QNX_COMMON,common_offsets=(0x70,0x74), ic_source=QNX_IC_SOURCE,
                words=words, config_offsets=QNX_CONFIG_OFFSETS, scanout=False, dma=False, irq_assertion=False)


def qnx_runtime(qmp):
    return {hex(base+4): {key:qmp.execute('qom-get', {'path':QNX_PATH,'property':f'{key}-{i}'})
                        for key in ('reads','writes','config','pending')}
            for i,base in enumerate(base for port in QNX_PORTS for base in port[:2])}


def qnx_source_runtime(qmp):

    regs=(('idma',0x08),('idma',0x10),('idma',0x14),('idma',0x18),('idma',0x40),('dpp',0x18))
    available={row['name'] for row in qmp.execute('qom-list', {'path':QNX_PATH})}
    out={}
    for i,(dpp,idma,_,name) in enumerate(QNX_PORTS):
        values={}
        for bank,off in regs:
            key=f'{bank}-{i}-{off:03x}'
            if {key,key+'-writes'}<=available:
                base=idma if bank=='idma' else dpp
                values[hex(base+off)]={k:qmp.execute('qom-get', {'path':QNX_PATH,'property':p})
                                       for k,p in (('value',key),('writes',key+'-writes'))}
        if values:
            out[hex(idma)]=dict(name=name,registers=values)
    return out
