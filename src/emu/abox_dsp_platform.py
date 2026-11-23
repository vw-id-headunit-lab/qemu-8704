
import hashlib
import mmap
from pathlib import Path
import struct

from vlm_platform import factory_file, original, parse_dtb

PROFILE = 'virtual-abox-dsp-off-v1'
PMU_BASE = 0x10460000
CONFIG = (0x3400, 0x3480, 0x3500, 0x3580)
LINUX = (0x793f60, 176, '3449b65cff0989748c8a706fd669d2a10ffe642ae7722ec4b9c7e9b6c9e456c1', 0xadc260)
ANDROID = (0xb4c278, 384, 'f4b0f5ca35eefbf5d57c1bb951e3682f212bf6c994db5b902fcce3e104b22e89', 0x1595340)
MAILBOX0_CORE0='cdc63fb8ac191c40cd4d50915bff11b8ed3829570509551adf3d5af1f42d8714'
MAILBOX0_SPANS=(
    (0xfe18,0x34,'c59d0fe84db14274de98ac5513df421217587d468c0c60e18af213a85ff2f2d2'),
    (0x25570,0x6c,'1ff89f39af071ad52237b5dce0eff0a6f4d303ed891aaf2580916cce8b7f76c0'),
    (0x25b38,0xf0,'911cd8f2d406116c2ce6490d3436bb199ed326fe9e62c965e2f6473882e1105f'),
    (0x259a0,0x44,'cc291df2acef694864661be358b5683e092027a97bc98a5273a5587572d4a276'),
)


def mailbox0_contract(core0_path):
    core=Path(core0_path).read_bytes()
    if hashlib.sha256(core).hexdigest()!=MAILBOX0_CORE0 or any(
            hashlib.sha256(core[off:off+size]).hexdigest()!=digest for off,size,digest in MAILBOX0_SPANS):
        raise ValueError('Original ABOX DSP mailbox-0 dispatch changed')
    hcb=original('hcb/hcb.img').read_bytes()
    prefix='/vplatform/vl,audio/vl,abox_mailbox0@'
    for vmid in (2,3,4):
        start=hcb.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        tree=parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
        mem,irq=tree[prefix+'mem'],tree[prefix+'irq']
        if (mem['reg']!=struct.pack('>QI',0x19c94000,0x1000) or
                ('vl,disabled' in mem)!=(vmid!=2) or ('vl,disabled' in irq)!=(vmid!=2) or
                (vmid==2 and irq['interrupts']!=struct.pack('>III',0,49,4))):
            raise ValueError('Original VM2 mailbox-0 HCB assignment changed')
    linux=parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())
    if linux['/abox_mailbox@0x19C94000']['spi-mailbox']!=struct.pack('>4I',0xc1,0xc6,0xcb,0xd0):
        raise ValueError('Original AP ABOX mailbox interrupt selector changed')
    return dict(vmid=2,bank=0,base=0x19c94000,ap_spi=49,abox_intid=225,dsp_intid=224,
                original_dsp_sha256=MAILBOX0_CORE0,original_dsp_spans=MAILBOX0_SPANS,
                scope='Real DSP transport and interrupt routing; no synthesized ACK or audio')


def mailbox_banks(ap, dsp):
    marker=b'\0abox-linked-bank-mask\0'
    versions=[]
    for binary in (ap,dsp):
        with Path(binary).open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
            versions.append(data.find(marker)>=0)
    if versions[0]!=versions[1]:
        raise ValueError('Native ABOX bank-0 support requires matching AP and Xtensa carriers')
    return (0,1,2) if versions[0] else (1,2)


def verify_kernel(kernel, identity):
    offset, length, digest, table = identity
    if (hashlib.sha256(kernel[offset:offset+length]).hexdigest() != digest
            or kernel[table:table+16] != struct.pack('<4I', *CONFIG)):
        raise ValueError('Original ABOX DSP power-status instruction or table changed')


def policy(profile, abox_axi, pmu_profile, acpm_clock):
    if profile is None:
        return None
    if profile != PROFILE or not abox_axi or pmu_profile != 'virtual-cold-v1' or not acpm_clock:
        raise ValueError('ABOX DSP cold status requires native ABOX AXIUSER and explicit PMU/ACPM')
    hcb = original('hcb/hcb.img').read_bytes()
    digest = hashlib.sha256(hcb).hexdigest()
    if digest != abox_axi['original_hcb_sha256']:
        raise ValueError('Original ABOX DSP HCB identity changed')
    for vmid in (3, 4):
        off = hcb.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        end = off+struct.unpack_from('>I', hcb, off+4)[0]
        grant = parse_dtb(hcb[off:end])['/vplatform/vl,pmu_audio@mem']
        if ((PMU_BASE+CONFIG[0], 0x200) not in struct.iter_unpack('>QI', grant['reg'])
                or 'vl,disabled' in grant):
            raise ValueError(f'Original ABOX DSP PMU grant changed for VM{vmid}')
    verify_kernel(factory_file('ivi/Image').read_bytes(), LINUX)
    return dict(profile=profile, addresses=[PMU_BASE+off+4 for off in CONFIG],
                hcb_sha256=digest, linux_code_and_table=LINUX, android_code_and_table=ANDROID,
                status_model='read-only virtual off', dsp_firmware_executed=False,
                real_silicon_reset_verified=False)


def runtime(qmp):
    names = (*[f'abox-dsp-core{i}-status-reads' for i in range(len(CONFIG))], 'abox-dsp-status-rejected')
    return {key: qmp.execute('qom-get', dict(path='/machine/exynos-pmu', property=key)) for key in names}

