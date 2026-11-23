
import hashlib
import struct

import original_8704_vm as vm
from ufs_mailbox_platform import android_dtb
from vlm_platform import factory_file, original, parse_dtb

PROFILE = 'virtual-abox-gic-v1'
AXIUSER = 'virtual-abox-axiuser-v1'
AXI_ADDRESS = 0x19c1040c
AXI_CODE = (0xb497a8,476,'0713f3ad55cc9508bd2146e77ec307488302a9a968d9b6afcd7f249072f34d62')
PATH = '/machine/exynos-abox-gic'
REG = ((0x19cf1000,0x1000),(0x19cf2000,0x1004))
LINUX = (0xa19748,1000,'96745073f61d7c341f556e71c138a7990da6a7bccdf1b921eed43d0ca321f015')
ANDROID = (0xfe078,816,'34de6f2cc7b6092a40c606904ea7114d3d7d36708e2280a738d31a3e93c8fcc8')


def verify_android(kernel):
    offset,count,digest = ANDROID
    if hashlib.sha256(kernel[offset:offset+count]).hexdigest()!=digest:
        raise ValueError('Original Android ABOX GIC driver changed')


def policy(profile, native_both):
    if profile is None:
        return None
    if profile!=PROFILE or not native_both:
        raise ValueError('ABOX GIC requires original Linux and Android DT/HCB')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    linux=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    hashes={}
    for i,dt in ((3,linux.read_bytes()),(4,android_dtb())):
        start=raw.index(f'VDTVM{i}'.encode()+bytes(2))+8
        h=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
        d=parse_dtb(dt)['/abox_gic@0x19CF0000']
        prefix='/vplatform/vl,audio/vl,abox_gic'
        shared=h[prefix+'_shared@mem']
        dist=h[prefix+'@mem']
        if (tuple(struct.iter_unpack('>QI',d['reg']))!=REG or
            d['reg-names']!=b'gicd\0gicc\0' or d['compatible']!=b'samsung,abox_gic\0'
            or d['status']!=b'okay\0' or REG[1] not in struct.iter_unpack('>QI',shared['reg'])
            or 'vl,allow-sharing' not in shared or 'vl,disabled' in shared or 'vl,disabled' in dist
            or (REG[0][0],4) not in struct.iter_unpack('>QI',dist['reg'])):
            raise ValueError(f'Original ABOX GIC binding changed for VM{i}')
        hashes[i]=hashlib.sha256(dt).hexdigest()
    offset,count,digest=LINUX
    with factory_file('ivi/Image').open('rb') as f:
        f.seek(offset)
        if hashlib.sha256(f.read(count)).hexdigest()!=digest:
            raise ValueError('Original Linux ABOX GIC driver changed')
    return dict(profile=profile,original_hcb_sha256=vm.sha(hcb),original_dtbs=hashes,
                linux_code=LINUX,android_code=ANDROID,reg=REG,virtual_num_irq=256,
                isolated_qemu_gicv2=True,dsp_running=False,audio_irq_wired=False)


def axi_policy(profile,abox):
    if profile is None:
        return None
    if profile!=AXIUSER or not abox:
        raise ValueError('ABOX AXIUSER needs original ABOX GIC resource grants')
    raw=original('hcb/hcb.img').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=abox['original_hcb_sha256']:
        raise ValueError('ABOX AXIUSER grant changed')
    for vmid in (3,4):
        start=raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        h=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
        g=h['/vplatform/vl,audio/vl,abox_gic_shared@mem']
        if (AXI_ADDRESS,4) not in struct.iter_unpack('>QI',g['reg']) or 'vl,disabled' in g:
            raise ValueError('ABOX AXIUSER is not granted to original VM')
    node=parse_dtb(android_dtb())['/abox@0x19D40000']
    ranges=tuple(struct.iter_unpack('>QI',node['reg']))
    names=node['reg-names'].rstrip(bytes(1)).split(bytes(1))
    if len(ranges)!=len(names) or not any(base==AXI_ADDRESS-0x40c and size>=0x410 for base,size in ranges):
        raise ValueError('Original ABOX register resource does not cover AXIUSER')
    return dict(profile=profile,address=AXI_ADDRESS,model_mask=0xffff,cold_value=0,
                original_code=AXI_CODE,original_hcb_sha256=abox['original_hcb_sha256'],
                dma=False,physical_reset_value_verified=False)


def verify_axi_kernel(kernel):
    off,size,digest=AXI_CODE
    if hashlib.sha256(kernel[off:off+size]).hexdigest()!=digest:
        raise ValueError('Original Android ABOX AXIUSER code changed')


def axi_runtime(qmp):
    return {p:qmp.execute('qom-get',dict(path=PATH,property=p))
            for p in ('axiuser','axi-reads','axi-writes','axi-rejected')}


def runtime(qmp):
    return {p:qmp.execute('qom-get',dict(path=PATH,property=p))
            for p in ('eoi-writes','last-eoi','iar-reads','last-iar')}

