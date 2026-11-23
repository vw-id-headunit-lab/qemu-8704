
import hashlib
import struct

import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE='virtual-sysmmu-v7-discovery-v1'
PORTS=(0x19a60000,0x19a90000,0x1aa80000,0x1aa90000)
VERSION=0x70000000
CAPA1=0
INT_STATUS=0x60
INT_CLEAR=0x64
PT_BASE_VID4=0x400c
FLUSH_ALL_VID4=0x4010
VM4_BANK=(0xa0000000,0x18000000)


def policy(profile, native_android):
    if profile is None:return None
    if profile!=PROFILE or not native_android:
        raise ValueError('SysMMU discovery needs original Android on the native board')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    start=raw.index(b'VDTVM4\0\0')+8
    table=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    start=raw.index(b'VDTHYP\0\0')+8
    allocation=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    bank=struct.unpack_from('>QQ',allocation['/vlm/vm@4/vm-memory']['reg'])
    if bank!=VM4_BANK:
        raise ValueError('Original VM4 low RAM bank changed')
    vendor=factory_file('android/vendor_boot.img').read_bytes()
    dt_size=struct.unpack_from('<I',vendor,2100)[0]
    hdr_size,page,rd_size=(struct.unpack_from('<I',vendor,i)[0] for i in (2096,12,24))
    dt_start=((hdr_size+page-1)//page+(rd_size+page-1)//page)*page
    dt=vendor[dt_start:dt_start+dt_size]
    nodes=parse_dtb(dt)
    layout={}
    for name,props in nodes.items():
        if props.get('compatible')!=b'samsung,exynos-sysmmu\0' or props.get('status',b'okay\0') not in (b'okay\0',b'ok\0'):
            continue
        base,size=next(struct.iter_unpack('>QI',props['reg']))
        hcb_node=f'/vplatform/sysmmu@{base:X}'
        row=table.get(hcb_node,{})
        if size!=0x10000 or row.get('reg')!=struct.pack('>QI',base,size) or 'vl,disabled' in row:
            raise ValueError(f'Unbound original Android SysMMU: {name}')
        if props.get('vid')!=struct.pack('>I',4):
            raise ValueError(f'Original guest VID changed: {name}')
        layout[base]={'android_path':name,'hcb_path':hcb_node,'vid':4}
    if tuple(sorted(layout))!=PORTS:
        raise ValueError('Original active SysMMU address set changed')
    return {'profile':profile,'version':VERSION,'capa1':CAPA1,'addresses':layout,
            'factory_hcb_sha256':vm.sha(hcb),'factory_android_dtb_sha256':hashlib.sha256(dt).hexdigest(),
            'virtual_not_measured':True,'translation':False,'irq':False,
            'registers':[0x34,0x874,PT_BASE_VID4,FLUSH_ALL_VID4,INT_STATUS,INT_CLEAR],'vm4_root_ram':bank}


LINUX_PROFILE='virtual-linux-sysmmu-v7-v1'
LINUX_PRIVATE=((0xb8000000,0x18000000),(0xe3c00000,0x1000000))
LINUX_VIDS={
    0x18050000:0,0x18250000:0,0x18c80000:3,0x19880000:3,0x198b0000:3,0x19b90000:3,
    0x19e00000:1,0x1a080000:0,0x1a280000:0,0x1a8d0000:0,0x1a9d0000:0,0x1aaa0000:3,
    0x1d800000:0,0x1d830000:0,0x1d860000:0,0x1d890000:0,0x1d8c0000:0,0x1d900000:0,0x1d930000:0,
}
MFC_PORTS=(0x19880000,0x198b0000)
MFC_RUNTIME_PROPERTIES=('mfc-ctrl','mfc-qos','mfc-default-tlb','mfc-default-writes',
                        'mfc-index-write-mask','mfc-ctrl-writes','mfc-flush-range')
DISPLAY_PORT=0x1aaa0000
DISPLAY_DPP=('/dpp@0x1AA27000','/dpp@0x1AA25000')
DISPLAY_TLBS=tuple((i,0x0a,0x10100,0x70000+i) for i in range(1,5))
DISPLAY_G0_PORT=0x18c80000
DISPLAY_G0_DPP=('/dpp@0x18C21000','/dpp@0x18C22000')
DISPLAY_G0_TLBS=tuple((i,0x0a,0x10100,sid) for i,sid in
                      enumerate((0x0c070004,0x0c000400,0x0c000800),1))
DISPLAY_PORTS={
    DISPLAY_PORT:(DISPLAY_DPP,(b'okay\0',b'okay\0'),b'VG0, G2\0',DISPLAY_TLBS,17,16),
    DISPLAY_G0_PORT:(DISPLAY_G0_DPP,(b'disable\0',b'okay\0'),b'GF0, G0\0',DISPLAY_G0_TLBS,37,36),
}
DISPLAY_RUNTIME_PROPERTIES=('display-ctrl','display-qos','display-qos-writes',
                            'display-default-tlb','display-default-writes',
                            'display-index-write-mask','display-ctrl-writes','display-flush-range')
DISPLAY_RESUME_CODE={
    0xffffffc0104b00f4: bytes.fromhex('1f040031080c19530801153208119f1a'),
    0xffffffc0104b0140: bytes.fromhex('400040b9010004111f0400310002005421681b53400440b9231000110000070a8340238b600000b9400840b9232000110000060a8340238b600000b921500011400c40b98140218b200000b942400091'),
    0xffffffc0104b17dc: bytes.fromhex('00d02191816e00b9010040b9212c44d3817200b9000040b9183200d018630e91800b7037'),
    0xffffffc0104b1a48: bytes.fromhex('620240b9837240b95f00036ba22d0054734200915f0313eb41ffff549b9a00b9'),
    0xffffffc0104b14b0: bytes.fromhex('021000915f0000b91f0000b9'),
}
MFC_RESUME_CODE={
    0xffffffc0104b0114: bytes.fromhex('c31d8052860084d22100030a8000068b010000b9'),
    0xffffffc0104b03d0: bytes.fromhex('080000b97bffff17'),
    0xffffffc0104b0264: bytes.fromhex('bf3203d5a0008052612640f9200000b960a64239'),
}
LINUX_PROBE=(0x431678,2704,'ceb6ad04c2eaa5f4cd889e1b4b4311a489b5ceefe4333bf61036024428d5c401')

def linux_policy(profile,native_linux):
    if profile is None:
        return None
    if profile!=LINUX_PROFILE or not native_linux:
        raise ValueError('Linux SysMMU discovery requires the original native VM3 DT and HCB')
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    def tree(tag):
        start=raw.index(tag+bytes(2))+8
        return parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    memory=tuple(struct.iter_unpack('>QQ',tree(b'VDTHYP')['/vlm/vm@3/vm-memory']['reg']))
    if memory[:2]!=LINUX_PRIVATE:
        raise ValueError('Original VM3 private SysMMU root banks changed')
    grant=tree(b'VDTVM3')
    rows={}
    tree_linux=parse_dtb(dt.read_bytes())
    for name,node in tree_linux.items():
        if node.get('compatible')!=b'samsung,exynos-sysmmu\0' or node.get('status',b'okay\0') not in (b'okay\0',b'ok\0'):
            continue
        base,size=struct.unpack_from('>QI',node['reg'])
        hcb_name=f'/vplatform/sysmmu@{base:X}'
        hcb_node=grant[hcb_name]
        vid=struct.unpack('>I',node.get('vid',bytes(4)))[0]
        if size!=0x10000 or hcb_node['reg']!=node['reg'][:12] or 'vl,disabled' in hcb_node:
            raise ValueError('Original Linux SysMMU aperture not granted: '+name)
        early='sysmmu,early-enable' in node
        if early!=(base==0x19e00000):
            raise ValueError('Original VM3 SysMMU early-enable port changed')
        rows[base]=dict(linux_path=name,hcb_path=hcb_name,vid=vid,vid_explicit='vid' in node,early_enable=early)
    if {k:v['vid'] for k,v in rows.items()}!=LINUX_VIDS:
        raise ValueError('Original Linux active SysMMU context set changed')
    mfc=tree_linux['/mfc@198E0000']
    nodes=[tree_linux[rows[base]['linux_path']] for base in MFC_PORTS]
    if (mfc.get('iommus')!=b''.join(n['phandle'] for n in nodes)
            or any(n.get('qos')!=struct.pack('>I',15) or
                   n.get('sysmmu,default_tlb')!=struct.pack('>I',0x60) for n in nodes)
            or 'sysmmu,tlb_property' in nodes[0]
            or nodes[1].get('sysmmu,tlb_property')!=struct.pack('>4I',1,0x20,0x10300,0x10001)):
        raise ValueError('Original MFC SysMMU DT contract changed')
    display_resumes={}
    for base,(clients,statuses,name,tlbs,dintc,dintc_s) in DISPLAY_PORTS.items():
        dpu=tree_linux[rows[base]['linux_path']]
        dpu_clients={path for path,node in tree_linux.items() if node.get('iommus')==dpu['phandle']}
        if (dpu_clients!=set(clients) or tuple(tree_linux[c].get('status') for c in clients)!=statuses
                or dpu.get('port-name')!=name or rows[base]['vid']!=3
                or dpu.get('qos')!=struct.pack('>I',15)
                or dpu.get('sysmmu,default_tlb')!=struct.pack('>I',0x0a)
                or dpu.get('sysmmu,tlb_property')!=b''.join(struct.pack('>4I',*row) for row in tlbs)
                or dpu.get('reg')!=struct.pack('>QIQI',base,0x10000,0x10030000,0x1000)
                or dpu.get('sysmmu,dintc_port')!=struct.pack('>I',dintc)
                or dpu.get('sysmmu,dintc_port_s')!=struct.pack('>I',dintc_s)):
            raise ValueError(f'Original display SysMMU DT contract changed at {base:#x}')
        display_resumes[base]=dict(port=base,vid=3,dpp=clients,
                                   qos_store_pc='0xffffffc0104b03d0',qos_word=0xf80,
                                   default_tlb=0x0a,indexed_tlbs=tlbs,
                                   virtual_non_vcr_capa1=(1+len(tlbs))<<4,
                                   range_invalidate_order=[0x20,0x24,0x18],dma=False,irq_source=False)
    with factory_file('ivi/Image').open('rb') as f:
        for pc, expected in MFC_RESUME_CODE.items():
            f.seek(pc-0xffffffc010080000)
            if f.read(len(expected))!=expected:
                raise ValueError('Original MFC SysMMU runtime-resume instruction changed')
        for pc, expected in DISPLAY_RESUME_CODE.items():
            f.seek(pc-0xffffffc010080000)
            if f.read(len(expected))!=expected:
                raise ValueError('Original VG0/G2 SysMMU register consumer changed')
        off,size,digest=LINUX_PROBE
        f.seek(off)
        if hashlib.sha256(f.read(size)).hexdigest()!=digest:
            raise ValueError('Original Linux SysMMU consumer bytes changed')
        f.seek(0xa73448)                                                       
        if f.read(24)!=struct.pack('<6I',0xc,0x10,0x14,0x18,0x20,0x24):
            raise ValueError('Original Linux SysMMU root and invalidate offsets changed')
        f.seek(0x42fee4)                                                   
        if hashlib.sha256(f.read(100)).hexdigest()!='bd4ef76f026a18b2ea358f79115ad744daf0f6130b975695e1c2b246a8adb68d':
            raise ValueError('Original Linux SysMMU range invalidate consumer changed')
        f.seek(0x4313b0)                                                           
        if hashlib.sha256(f.read(0x2a0)).hexdigest()!='60e404bc59d32ad8ef48aca8b6d44e3fb83b82b9a1d2b3bb75dc2bdc59ab2b84':
            raise ValueError('Original Linux SysMMU runtime-suspend consumer changed')
    return dict(profile=profile,addresses=rows,factory_hcb_sha256=vm.sha(hcb),
                factory_linux_dtb_sha256=vm.sha(dt),original_probe=LINUX_PROBE,
                vm3_private_ram=LINUX_PRIVATE,version=VERSION,capa1=CAPA1,translation=False,irq=False,
                mfc_resume={'ports':MFC_PORTS,'default_tlb_offset':0x2004,'default_tlb':0x60,
                            'qos_word':0xf80,'indexed_port':MFC_PORTS[1],
                            'index_words':{0x2024:0x20,0x2028:0x10300,0x2034:0x10001},
                            'range_invalidate_order':[0x20,0x24,0x18],
                            'suspend_qos_word':0,
                            'default_tlb_store_pc':'0xffffffc0104b0124','dma':False},
                display_resume=display_resumes[DISPLAY_PORT],
                display_g0_resume=display_resumes[DISPLAY_G0_PORT])

def runtime_state(qmp,ports=PORTS):
    common=('version-reads','capa1-reads','ptbase-reads','ptbase-writes','ptbase-page',
            'flush-all','status-reads','clear-writes','pending','rejected','last-rejected')
    result={}
    for base in ports:
        path=f'/machine/exynos-sysmmu-{base:x}'
        extra=()
        if base in MFC_PORTS or base in DISPLAY_PORTS:
            present={p['name'] for p in qmp.execute('qom-list',{'path':path})}
            keys=DISPLAY_RUNTIME_PROPERTIES if base in DISPLAY_PORTS else MFC_RUNTIME_PROPERTIES
            extra=tuple(key for key in keys if key in present)
        result[hex(base)]={k:qmp.execute('qom-get',{'path':path,'property':k}) for k in common+extra}
    return result

