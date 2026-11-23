
import hashlib
import struct
import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE = 'virtual-abox-dma-sysmmu-v1'
BASE = 0x19e00000
PATH = '/machine/exynos-sysmmu-19e00000'
RESUME = (0x42fff0,1024,'d22592c0af1232e5a818fb97e0abcc0b330dda9adfd913c71cc7116f984ae5a5')
TLBS = ((1,0,0x10300,0x70004),(2,0,0x10300,0x70005),
        (3,0,0x10300,0x70006),(4,0,0x10300,0x70000))
PRIMARY = ('https://android.googlesource.com/kernel/google-modules/soc/gs/'
           '+/204078758737776959ecc85c527a00fd2cabb62f/drivers/iommu/samsung-iommu.c')


def policy(profile, linux_sysmmu, abox):
    if profile is None:
        return None
    if profile!=PROFILE or not linux_sysmmu or not abox:
        raise ValueError('ABOX DMA SysMMU needs the original Linux SysMMU and ABOX GIC')
    path=f'/sysmmu@{BASE:X}'
    raw=original('hcb/hcb.img').read_bytes()
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    tree=parse_dtb(dt.read_bytes())
    node=tree[path]
    off=raw.index(b'VDTVM3\0\0')+8
    hcb=parse_dtb(raw[off:off+struct.unpack_from('>I',raw,off+4)[0]])
    if (vm.sha(dt)!=linux_sysmmu['factory_linux_dtb_sha256'] or
            hashlib.sha256(raw).hexdigest()!=linux_sysmmu['factory_hcb_sha256'] or
            hcb['/vplatform'+path]['reg']!=struct.pack('>QI',BASE,0x10000) or
            node['port-name']!=b'CA32, ABOX_DMA\0' or node['vid']!=struct.pack('>I',1) or
            node['sysmmu,default_tlb']!=bytes(4) or node['qos']!=struct.pack('>I',15) or
            tuple(struct.iter_unpack('>4I',node['sysmmu,tlb_property']))!=TLBS or
            tree['/abox@0x19D40000']['iommus']!=node['phandle']):
        raise ValueError('Original ABOX DMA SysMMU resource contract changed')
    begin,size,digest=RESUME
    with factory_file('ivi/Image').open('rb') as f:
        f.seek(begin)
        if hashlib.sha256(f.read(size)).hexdigest()!=digest:
            raise ValueError('Original ABOX DMA runtime resume body changed')
    return dict(profile=profile,base=BASE,vid=1,virtual_capa1=0x50,original_qos=15,
                original_default_tlb=0,original_indexed_tlbs=TLBS,resume=RESUME,
                linux_dtb_sha256=vm.sha(dt),reference=PRIMARY,translation=False,irq=False)


def runtime(qmp):
    keys=['abox-ctrl','abox-cfg','abox-default','abox-writes','abox-index-write-mask',
          'ptbase-page','ptbase-writes','flush-all','rejected','last-rejected']
    keys += [f'abox-tlb-{i}-{n}' for i in range(1,5) for n in ('config','match','id')]
    return {k:qmp.execute('qom-get',dict(path=PATH,property=k)) for k in keys}

