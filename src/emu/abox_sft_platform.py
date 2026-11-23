
import hashlib
import struct

from vlm_platform import factory_file, original, parse_dtb


SFT_DAPM_ENUMS = (
    (14, 0x159a278, 0x8040, 0x19db8000),
    (15, 0x159a2f0, 0x9040, 0x19db9000),
)


LINUX_RDMA = (0, 1, 3, 4, 12, 13)
LINUX_WDMA = (1, 3, 4, 12, 13)
LINUX_UAIF = (1, 3)
PCM_NEW_CODE = (
    (0x795b60, 416, '354296e4e60f8d22f6223780ea0e214aff22681eae6bb5db3c378d1d0362f0b2'),
    (0x7981b0, 416, '7b253b04ea5c727a1cf0ede1712d03d1da0e2fa0e8d59a7fd835d84d32ac6a16'),
)
SHARED_ROUTE_OFFSETS = (0x40, 0x44, 0x48, 0x4c, 0x60, 0x64, 0x68)
LINUX_ASRC = (('rdma', 0x19d30000, LINUX_RDMA), ('wdma', 0x19d70000, LINUX_WDMA))
UAIF_FORMAT_WORDS = ((0, 0x00108090, 0x00000004), (4, 0x04000000, 0x03800000))


def linux_asrc_policy(kernel_file):

    with kernel_file.open('rb') as f:
        for offset,size,digest in PCM_NEW_CODE:
            f.seek(offset)
            if hashlib.sha256(f.read(size)).hexdigest()!=digest:
                raise ValueError('Original Linux PCM DMA status consumer changed')
        for kind, e, d in (('RDMA',0xade8c8,0xade158), ('WDMA',0xadf530,0xadf448)):
            f.seek(e)
            enum=struct.unpack('<IBB2xII',f.read(16))
            f.seek(d)
            default=struct.unpack('<II',f.read(8))
            if enum!=(0x40,8,8,14,15) or default!=(0x40,0):
                raise ValueError(f'Original VM3 {kind} ASRC enumeration changed')
        f.seek(0xadf878)
        defaults=dict(struct.iter_unpack('<II',f.read(16)))
        f.seek(0x79a0c8)
        fmt=f.read(568)
        f.seek(0xadc510)
        main_defaults=dict(struct.iter_unpack('<II',f.read(144)))
    if any(main_defaults.get(off)!=0 for off in SHARED_ROUTE_OFFSETS):
        raise ValueError('Original shared ABOX route reset values changed')
    if (any(defaults.get(off)!=cold for off,cold,_ in UAIF_FORMAT_WORDS) or
            hashlib.sha256(fmt).hexdigest()!='5a539a5ab5e56f86ba3050210088855f5dd26a28f611d05e459c1f3e9a4ff5e8'):
        raise ValueError('Original VM3 UAIF format or defaults changed')
    nodes=parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())
    raw=original('hcb/hcb.img').read_bytes()
    grants={}
    for vmid in (2,3,4):
        start=raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        size=struct.unpack_from('>I',raw,start+4)[0]
        grants[vmid]=parse_dtb(raw[start:start+size])
    main=nodes['/abox@0x19D40000']
    if (main.get('reg',b'')[:12]!=struct.pack('>QI',0x19d40000,0x9000)
            or main.get('compatible')!=b'samsung,abox'+bytes(1)):
        raise ValueError('Original Linux ABOX SFR resource changed')
    for vmid,tree in grants.items():
        node=tree['/vplatform/vl,audio/vl,abox@mem']
        if (node.get('reg',b'')[:12]!=struct.pack('>QI',0x19d40000,0x10000)
                or 'vl,allow-sharing' not in node or 'vl,disabled' in node):
            raise ValueError(f'Original VM{vmid} shared ABOX SFR grant changed')
    controls={}
    for kind,first,indices in LINUX_ASRC+(('uaif',0x19d90000,LINUX_UAIF),):
        for index in indices:
            base=first+0x1000*index
            reg=struct.pack('>QI',base,0x1000)
            dt=nodes[f'/abox@0x19D40000/abox_{kind}@0x{base:X}']
            if (dt.get('compatible')!=f'samsung,abox-{kind}'.encode()+bytes(1)
                    or dt.get('status')!=b'okay'+bytes(1) or dt.get('reg')!=reg
                    or dt.get('samsung,id')!=struct.pack('>I',index)):
                raise ValueError(f'Original Linux {kind}{index} DT resource changed')
            for vmid,tree in grants.items():
                hcb=tree[f'/vplatform/vl,audio/vl,abox_{kind}{index}@mem']
                if hcb.get('reg')!=reg or ('vl,disabled' in hcb)!=(vmid!=3):
                    raise ValueError(f'Original VM{vmid} {kind}{index} grant changed')
            words=UAIF_FORMAT_WORDS if kind=='uaif' else ((0x40,0,0xf00),)
            controls[f'{kind}{index}']=[dict(address=base+off,cold_value=cold,mask=mask)
                                       for off,cold,mask in words]
    shared=[dict(address=0x19d40000+off,cold_value=0,read_only=True) for off in SHARED_ROUTE_OFFSETS]
    idle_status={f'{kind}{i}':dict(address=base+0x1000*i+0x30, value=0, running_bit=31, read_only=True)
                 for kind,base,ids in LINUX_ASRC for i in ids}
    return dict(vm=3,controls=controls,shared_cold_route=shared,idle_status=idle_status,
                pcm_new_code=PCM_NEW_CODE,
                dma=False,completion_irq=False,bit_clock=False)


QNX_UAIF2_BASE = 0x19d92000
QNX_UAIF2_MEMBER = (
    15687680, 158368, '3874770b1f7150ddffe3b9d160fdb670cb95495efac4ba93c998396823c01931')
QNX_UAIF2_CODE = (
    0x116c8, 0x2dc, '09a1c2774a25a06655ee3b543b3c6991f5215ad358d09ca82236ff931385634a')


def qnx_uaif2_policy():

    raw=original('hcb/hcb.img').read_bytes()
    for vmid in (2,3,4):
        start=raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        size=struct.unpack_from('>I',raw,start+4)[0]
        node=parse_dtb(raw[start:start+size])['/vplatform/vl,audio/vl,abox_uaif2@mem']
        if (node.get('reg')!=struct.pack('>QI',QNX_UAIF2_BASE,0x1000)
                or ('vl,disabled' in node)!=(vmid!=2)):
            raise ValueError(f'Original VM{vmid} UAIF2 ownership changed')
    source=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
    offset,size,digest=QNX_UAIF2_MEMBER
    with source.open('rb') as f:
        f.seek(offset)
        member=f.read(size)
    start,size,code_digest=QNX_UAIF2_CODE
    if (hashlib.sha256(member).hexdigest()!=digest
            or hashlib.sha256(member[start:start+size]).hexdigest()!=code_digest):
        raise ValueError('Original QNX UAIF2 member or format consumer changed')
    return dict(vm=2,base=QNX_UAIF2_BASE,member=QNX_UAIF2_MEMBER,code=QNX_UAIF2_CODE,
                format_words=UAIF_FORMAT_WORDS,
                cold_defaults='same Auto9 ABOX format-block defaults as OEM Linux/Android',
                physical_reset_readback=False,dma=False,bit_clock=False)


UAIF_BASES = ((4, 0x19dbc000), (5, 0x19dbd000))


def sft_mux_policy(kernel, android_dtb):

    uaif_defaults = dict(struct.iter_unpack('<II', kernel[0x15987d8:0x15987d8+32]))
    if any(uaif_defaults.get(off) != cold for off, cold, _ in UAIF_FORMAT_WORDS):
        raise ValueError('Original Android UAIF defaults changed')
    fmt_code = kernel[0xb53ed0:0xb53ed0+552]
    if hashlib.sha256(fmt_code).hexdigest() != 'add48bc4e1b9d29d560db940d6027a4a9445cd80289a57bdbece4676342a096a':
        raise ValueError('Original Android UAIF format consumer changed')
    android_nodes = parse_dtb(android_dtb)
    for index, base in UAIF_BASES:
        node = android_nodes[f'/abox@0x19D40000/abox_uaif@0x{base:X}']
        if (node.get('compatible') != b'samsung,abox-uaif\0'
                or node.get('status') != b'okay\0'
                or node.get('reg') != struct.pack('>QI',base,0x1000)):
            raise ValueError(f'Original Android UAIF{index} DT resource changed')
    defaults = dict(struct.iter_unpack('<II', kernel[0x1599170:0x1599170+88]))
    for channel, off, reg, _ in SFT_DAPM_ENUMS:
        data = kernel[off:off+16]
        if (struct.unpack('<IBB2xII',data) != (reg,0,0,2,1)
                or defaults.get(reg) != 0):
            raise ValueError(f'Original Android SFT RDMA{channel} mux data changed')
    for offset, shift in ((0x159a188,0), (0x159a358,8)):
        enum = struct.unpack('<IBB2xII',kernel[offset:offset+16])
        if enum != (0x30,shift,shift,17,31):
            raise ValueError('Original Android SFT UAIF speaker-route enum changed')
    if defaults.get(0x30) != 0:
        raise ValueError('Original Android SFT speaker-route cold value changed')
    status_code=kernel[0xb4af48:0xb4af48+128]
    if hashlib.sha256(status_code).hexdigest() != 'fa628021f24018943c211015984cef755d57ddbd6ff2ee6c729a294a96ea9cbf':
        raise ValueError('Original Android SFT RDMA status consumer changed')
    raw = original('hcb/hcb.img').read_bytes()
    for vmid in (2,3,4):
        start = raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        length = struct.unpack_from('>I',raw,start+4)[0]
        nodes = parse_dtb(raw[start:start+length])
        sft = nodes['/vplatform/vl,audio/vl,abox_sft@mem']
        if (sft['reg'] != struct.pack('>QI',0x19db0000,0x1000)
                or ('vl,disabled' in sft) != (vmid!=4)):
            raise ValueError(f'Original VM{vmid} speaker-route grant changed')
        for index, base in UAIF_BASES:
            node = nodes[f'/vplatform/vl,audio/vl,abox_uaif{index}@mem']
            if (node['reg'] != struct.pack('>QI',base,0x1000)
                    or ('vl,disabled' in node) != (vmid!=4)):
                raise ValueError(f'Original VM{vmid} UAIF{index} ownership changed')
        for channel, _, _, base in SFT_DAPM_ENUMS:
            node = nodes[f'/vplatform/vl,audio/vl,abox_rdma{channel}@mem']
            if (node['reg'] != struct.pack('>QI',base,0x1000)
                    or ('vl,disabled' in node) != (vmid!=4)):
                raise ValueError(f'Original VM{vmid} RDMA{channel} ownership changed')
    return {'vm':4, 'words':[base+0x40 for _,_,_,base in SFT_DAPM_ENUMS],
            'bitmask':1,'cold_value':0,
            'speaker_route':{'address':0x19db0030,'mask':0x1f1f,'cold_value':0,
                             'fields':{'uaif4':0,'uaif5':8}},
            'rdma_status':{'addresses':[base+0x30 for _,_,_,base in SFT_DAPM_ENUMS],
                           'running_mask':1<<31,'cold_value':0,'read_only':True},
            'uaif_format':{str(index):[{'address':base+off,'cold_value':cold,'mask':mask}
                                      for off,cold,mask in UAIF_FORMAT_WORDS]
                           for index,base in UAIF_BASES},
            'uaif_bit_clock':False,'dma':False,'completion_irq':False}
