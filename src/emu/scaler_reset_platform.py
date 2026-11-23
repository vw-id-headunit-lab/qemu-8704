
import hashlib
import struct
import original_8704_vm as vm
from vlm_platform import factory_file, original, parse_dtb

PROFILE = 'virtual-linux-scaler-reset-v1'
PROBE_PROFILE = 'virtual-linux-scaler-poly-probe-v2'
VERSION = 0x04000002
VERSION_TABLE = (0xab4f68, 0x98, 'a82627ddab28ac56468866da9cfd2040081f58e0d5c30cec5f3215eea4eb8826')
BASE = 0x19b10000
PATH = '/machine/exynos-scaler-reset'
CODE = (0x6669b8,2760,'fc373d3ac560984c8f8989c57b2c44541cb67359fdfd8ca3b3123ff99a60d35e')
KEYS = ('cfg','reset-requests','reset-completions','cfg-reads','rejected','last-offset','last-value')
REFERENCE = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/mchehab/linux-media/+/be718b524d8d8a25c18b7403c4dbfddacc8e89e3/drivers/gpu/drm/exynos/exynos_drm_scaler.c'


def policy(profile, native):
    if profile is None:
        return None
    if profile not in (PROFILE,PROBE_PROFILE) or not native:
        raise ValueError('Scaler reset requires original Linux DT/HCB')
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb')
    p='/scaler@19B10000'
    node=parse_dtb(dt.read_bytes())[p]
    reg=struct.pack('>QI',BASE,0x3000)
    ints=struct.pack('>3I',0,245,4)
    if (node['status']!=b'okay\0' or node['compatible']!=b'samsung,exynos5-scaler\0'
            or node['reg']!=reg or node['interrupts']!=ints):
        raise ValueError('Original Scaler DT changed')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    pos=raw.index(b'VDTVM3\0\0')+8
    tree=parse_dtb(raw[pos:pos+struct.unpack_from('>I',raw,pos+4)[0]])
    path='/vplatform/vl,scaler/vl,scaler'
    mem,irq=(tree[path+suffix] for suffix in ('@mem','@irq'))
    if mem['reg']!=reg or irq['interrupts']!=ints or any('vl,disabled' in row for row in (mem,irq)):
        raise ValueError('Original Scaler resource grant changed')
    start,size,digest=CODE
    with factory_file('ivi/Image').open('rb') as f:
        f.seek(start)
        if hashlib.sha256(f.read(size)).hexdigest()!=digest:
            raise ValueError('Original Scaler probe body changed')
        if profile==PROBE_PROFILE:
            off,length,expected=VERSION_TABLE
            f.seek(off)
            table=f.read(length)
            if (hashlib.sha256(table).hexdigest()!=expected
                    or struct.unpack_from('<II',table)!=(VERSION,0x50100)):
                raise ValueError('Original Scaler POLY version table changed')
    return dict(profile=profile,original_dtb_sha256=vm.sha(dt),original_hcb_sha256=vm.sha(hcb),
                base=BASE,cfg=4,reset_bit=1,virtual_reset_ns=128,native_spi=245,
                probe_code=CODE,reference=REFERENCE,irq_generated=False,dma=False,scanout=False,
                mode=2 if profile==PROBE_PROFILE else 1,
                virtual_version=VERSION if profile==PROBE_PROFILE else None,
                physical_version_verified=False,
                virtual_stop_ns=128 if profile==PROBE_PROFILE else None)


def runtime(qmp):
    props={r['name'] for r in qmp.execute('qom-list',dict(path=PATH))}
    extra=('version','version-reads','stop-requests','stop-acks')
    return {key:qmp.execute('qom-get',dict(path=PATH,property=key))
            for key in KEYS+tuple(k for k in extra if k in props)}

