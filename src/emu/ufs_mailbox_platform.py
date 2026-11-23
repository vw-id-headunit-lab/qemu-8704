
import hashlib
import struct

import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE='virtual-ufs-ph-vh-mailbox-v1'
HCI=0x17e00000
ANDROID_CODE=(0x893a3c,396,'4694b26243b92fc73ef8831ff6d5973d2f04a48571b1dd158068145ee9429bda')
PRIMARY='https://android.googlesource.com/kernel/common/+/8164851725c867f74fc0e157f13a7f257da4a7db/drivers/ufs/host/ufs-exynos.c'
PH_KEYS=('config','tx','writes','rejected')
VH_KEYS=('rx','pending','ready-writes','ready-reads','clear-writes','tx5-writes','ack','rejected','last-rejected')

def android_dtb():
    p=factory_file('android/vendor_boot.img')
    with p.open('rb') as f:
        h=f.read(2112)
        page,rd,hs,ds=(struct.unpack_from('<I',h,i)[0] for i in (12,24,2096,2100))
        f.seek(((hs+page-1)//page+(rd+page-1)//page)*page)
        return f.read(ds)

def verify_android(kernel):
    off,size,digest=ANDROID_CODE
    if hashlib.sha256(kernel[off:off+size]).hexdigest()!=digest:
        raise ValueError('Original Android UFS IOV consumer changed')

def policy(profile, host0, native_both):
    if profile is None:
        return None
    if profile!=PROFILE or not host0 or not native_both:
        raise ValueError('UFS mailbox requires original physical HCI and both original VH resource trees')
    raw=original('hcb/hcb.img').read_bytes()
    linux=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    trees=[parse_dtb(linux.read_bytes()),parse_dtb(android_dtb())]
    views={}
    for vmid in (2,3,4):
        start=raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        views[vmid]=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    for i in (1,2):
        base=HCI+i*0x10000
        reg=struct.pack('>QIQI',base,0x1000,base+0x1100,0x400)
        irq=struct.pack('>III',0,227+i,4)
        path=f'/ufs@0x{base:X}'
        for j,t in enumerate(trees,1):
            node=t[path]
            if (node['compatible']!=b'samsung,exynos-ufs\0' or node['reg']!=reg or
                node['interrupts']!=irq or node['status']!=(b'okay\0' if i==j else b'disabled\0')):
                raise ValueError(f'Original VH{i} ownership or aperture changed in guest {j}')
        path=f'/vplatform/vl,ufs0_{i}/vl,ufs0_{i}'
        for vmid,t in views.items():
            mem,ints=(t[path+suffix] for suffix in ('@mem','@irq'))
            if (mem['reg']!=reg or ints['interrupts']!=irq or
                (('vl,disabled' not in mem)!=(vmid in (2,i+2))) or
                (('vl,disabled' not in ints)!=(vmid==i+2)) or
                (('vl,allow-sharing' in mem)!=(vmid==2 or (i==2 and vmid==4)))):
                raise ValueError(f'Original VH{i} HCB route changed in VM{vmid}')
    return dict(profile=profile,hosts=host0['hosts'],primary_reference=PRIMARY,
                producer_sha256=host0['route']['driver_sha256'],android_consumer=ANDROID_CODE,
                original_hcb_sha256=hashlib.sha256(raw).hexdigest(),original_linux_dtb_sha256=vm.sha(linux),
                protocol={'ph_config':0xd4,'ph_tx_vh_rx':0xd8,'vh_tx':0xdc,'vh_status_w1c':0xd0,
                          'ph_ready':1,'vh_ready':2,'vh_tx5':5,'dst_mask':6,'pending_bit':16},
                behavior='Original bidirectional PH/VH latches, W1C and shared IRQ; no host-seeded COMPLETE',
                polled=True,vh_dma=False,vh_irq=True,ph_irq=True,full_hci=False)

def runtime(qmp):
    path='/machine/exynos-ufs0'
    def get(prefix,keys):
        return {k:qmp.execute('qom-get',dict(path=path,property=f'mailbox-{prefix}-{k}')) for k in keys}
    return dict(ph=get('ph',PH_KEYS),vh1=get('vh1',VH_KEYS),vh2=get('vh2',VH_KEYS))

