
import hashlib
import struct

import original_8704_vm as vm
from vlm_platform import LINUX_FACTORY_HIGH_BANK, factory_file, factory_high_bank, original, parse_dtb

BASE=0x19700000
SIZE=0x5000
MODEL_MASK=0xf00f0000
G76=0x70010000
PATH='/machine/exynos-mali-g3d1'

DISCOVERY_PROFILE='virtual-g76-feature-discovery-v1'
DISCOVERY_CODE=(0x8010b4,0x414,'99f78bf751415a39eccc05e67745e2f7eafd044fc5d908f4b54311d1180075d9')

RESET_IRQ_PROFILE='virtual-g76-reset-irq-v1'
RESET_IRQ_CODE=(0x801670,536,'68352196f0ec499d0944b0a68f977fbbd9e263c4133b873657e2b22f6ab86ee3')
SPIS={'job':256,'mmu':257,'gpu':255}

CONFIG_PROFILE='virtual-g76-config-v1'
CONFIG_CODE=(8408536, 1728, 'cd404f692621cc98c3f28ed904f0ac1b1b6d23b64d5f23295326d5da5a221194')

EMPTY_POWER_PROFILE='virtual-g76-empty-power-v1'
JM_TOPOLOGY_PROFILE='virtual-g76-jm-topology-v1'
JM_VM3_CONTROL_PROFILE='virtual-g76-vm3-control-as0-v1'
VM3_CONTROL_BANKS=((0xb8000000,0x18000000),(0xe3c00000,0x1000000),LINUX_FACTORY_HIGH_BANK)
LINUX_JM_TOPOLOGY_CODE=(
    (0x4bd810,72,'688de04438bc017ab8740b4668c8be66b5b1520d9d7f1f68c31e77bc01898069'),
    (0x4bdfc8,504,'d453f23e8e8a30f983ed918488abf72f0df9f81d38f21c9912179ce06d7db13d'),
    (0x4c7640,768,'a1c8a9376e288f5c67f504db598c6568c3844a4b9a10f434883e825e42edaa1a'),
)
LINUX_CACHE_IRQ_CODE=(
    (0x496270,0x58,'e237bf05f8cf917e94cd1903c97cfba2efbc4827d1a1135f1dbc8663e739daa3'),
    (0x4c3280,0x2b0,'d16cf58d92c8329f91667a5a839361bbf465471d80c54c7a2f4cac252900b5c4'),
)
EMPTY_POWER_CODE=(
    (0x80387c,1376,'171b7c89d565e8dd7661f981b8a599c56373a84e48509cc06d3bf578165fbfee'),
    (0x805b70,408,'8fdb86fdf9478a7f6b32110217235c8f68c9a3062a4db371a4412067cd101538'),
)

def verify_discovery(profile, kernel, reset_irq=None, config=None, empty_power=None):
    offset, size, digest=DISCOVERY_CODE
    if profile!=DISCOVERY_PROFILE or hashlib.sha256(kernel[offset:offset+size]).hexdigest()!=digest:
        raise ValueError('Original Android kbase_backend_gpuprops_get bytes changed')
    if reset_irq:
        offset, size, digest=RESET_IRQ_CODE
        if reset_irq!=RESET_IRQ_PROFILE or hashlib.sha256(kernel[offset:offset+size]).hexdigest()!=digest:
            raise ValueError('Original Android kbase_install_interrupts bytes changed')
    if config:
        offset, size, digest=CONFIG_CODE
        if config!=CONFIG_PROFILE or hashlib.sha256(kernel[offset:offset+size]).hexdigest()!=digest:
            raise ValueError('Original Android kbase_pm_init_hw bytes changed')
    if empty_power:
        if empty_power!=EMPTY_POWER_PROFILE or any(
            hashlib.sha256(kernel[off:off+size]).hexdigest()!=digest
            for off,size,digest in EMPTY_POWER_CODE
        ):
            raise ValueError('Original Android kbase PM L2/invoke bytes changed')

def jm_topology_policy(profile, linux_kernel, empty_power, *, vm3_native=False):
    if profile is None:
        return None
    if profile not in (JM_TOPOLOGY_PROFILE,JM_VM3_CONTROL_PROFILE) or empty_power!=EMPTY_POWER_PROFILE or linux_kernel is None:
        raise ValueError('Virtual G3D1 job topology requires native OEM Linux and empty-power profile')
    vm3_control=profile==JM_VM3_CONTROL_PROFILE
    if vm3_control:
        if not vm3_native:
            raise ValueError('VM3 G3D1 AS0 requires the complete native board and OEM VM3 RAM')
        hcb=original('hcb/hcb.img').read_bytes()
        for bank in VM3_CONTROL_BANKS:
            factory_high_bank(hcb,3,bank)
    with linux_kernel.open('rb') as source:
        for offset, size, digest in LINUX_JM_TOPOLOGY_CODE+LINUX_CACHE_IRQ_CODE:
            source.seek(offset)
            if hashlib.sha256(source.read(size)).hexdigest()!=digest:
                raise ValueError('Original Linux kbase job-slot consumer changed')
    return dict(profile=profile,js_present=7,js_features=[0x200,0x9c,0x18],vm3_control=vm3_control,
                backed_address_spaces=int(vm3_control),shader_present=0,gpu_jobs=vm3_control,scanout=False,
                supported_job_types=[1,2,3,5,9] if vm3_control else [],
                vertex_scope='exact shader digest and linear viewport ABI only; OEM VM3 RG16F/SNAP_4' if vm3_control else None,
                vm4_irq_scope='independent empty JOB/MMU status and mask storage; no VM3 state access' if vm3_control else None,
                fragment_scope='full linear clear; exact zero-only 4x resolve with virtual CRC invalidation; no geometry or shaders' if vm3_control else None,
                dma_private_banks=VM3_CONTROL_BANKS if vm3_control else [],
                original_linux_consumer=LINUX_JM_TOPOLOGY_CODE,
                empty_cache_maintenance={'command':8,'gpu_irq_bit':17,'original_linux_code':LINUX_CACHE_IRQ_CODE},
                scope='Virtual OEM scheduler topology; not a physical silicon register dump')


def policy(gpu_id, native_android, discovery=None, reset_irq=None, config=None, empty_power=None):
    if empty_power is not None and (empty_power!=EMPTY_POWER_PROFILE or config!=CONFIG_PROFILE):
        raise ValueError('G3D1 empty power requires explicit configuration')
    if config is not None and (config!=CONFIG_PROFILE or reset_irq!=RESET_IRQ_PROFILE):
        raise ValueError('G3D1 configuration requires explicit reset IRQ')
    if reset_irq is not None and (reset_irq!=RESET_IRQ_PROFILE or discovery!=DISCOVERY_PROFILE):
        raise ValueError('G3D1 reset IRQ requires explicit feature discovery')
    if discovery is not None and (discovery!=DISCOVERY_PROFILE or gpu_id is None):
        raise ValueError('G3D1 discovery requires explicit virtual G76 identity')
    if gpu_id is None:
        return None
    if not native_android or not (0 < gpu_id <= 0xffffffff) or gpu_id & MODEL_MASK != G76:
        raise ValueError('Require a declared virtual Mali-G76 ID and original native Android DT/HCB')
    image=factory_file('android/vendor_boot.img')
    with image.open('rb') as f:
        h=f.read(2112)
        page,rd,hs,ds=(struct.unpack_from('<I',h,i)[0] for i in (12,24,2096,2100))
        f.seek(((hs+page-1)//page+(rd+page-1)//page)*page)
        dt=f.read(ds)
    nodes=parse_dtb(dt)
    active={k:p for k,p in nodes.items() if p.get('compatible')==b'arm,mali\0' and p.get('status')==b'okay\0'}
    if tuple(active)!=('/G3D1@19700000',):
        raise ValueError('Original Android active Mali aperture changed')
    node=active['/G3D1@19700000']
    if node['reg']!=struct.pack('>QI',BASE,SIZE) or 'arbiter_if' not in node:
        raise ValueError('Original Android G3D1 address or arbiter reference changed')
    if reset_irq and (node['interrupt-names']!=b'JOB\0MMU\0GPU\0' or
        node['interrupts']!=b''.join(struct.pack('>III',0,spi,4) for spi in SPIS.values())):
        raise ValueError('Original Android G3D1 interrupt routing changed')
    hcb=original('hcb/hcb.img')
    raw=hcb.read_bytes()
    grant='/vplatform/vl,g3d1/vl,g3d1@mem'
    grants={}
    for vmid in (2,3,4):
        off=raw.index(f'VDTVM{vmid}\0\0'.encode())+8
        tree=parse_dtb(raw[off:off+struct.unpack_from('>I',raw,off+4)[0]])
        row=tree[grant]
        if row['reg']!=struct.pack('>QI',BASE,SIZE):
            raise ValueError('Original Mali VM aperture differs from guest DT')
        if reset_irq:
            for name,spi in SPIS.items():
                irq=tree[f'/vplatform/vl,g3d1/vl,g3d1_{name}@irq']
                if (irq['interrupts']!=struct.pack('>III',0,spi,4) or
                    ('vl,disabled' in irq)!=(vmid!=3) or
                    irq.get('vl,dyn-resource-id')!=(b'gpu.mp12\0' if vmid in (3,4) else None)):
                    raise ValueError(f'Original VM{vmid} G3D1 {name} IRQ grant changed')
        grants[str(vmid)]={'enabled_at_boot':'vl,disabled' not in row,
            'dynamic_id':row.get('vl,dyn-resource-id',b'').rstrip(b'\0').decode('ascii'),
            'allow_sharing':'vl,allow-sharing' in row,
            'dynamic_no_page_fault':'vl,dyn-resource-no-page-fault' in row}
    expected={
        '2':dict(enabled_at_boot=False,dynamic_id='',allow_sharing=False,dynamic_no_page_fault=False),
        '3':dict(enabled_at_boot=True,dynamic_id='gpu.mp12',allow_sharing=True,dynamic_no_page_fault=True),
        '4':dict(enabled_at_boot=False,dynamic_id='gpu.mp12',allow_sharing=True,dynamic_no_page_fault=True),
    }
    if grants!=expected:
        raise ValueError('Original VM2/3/4 G3D1 static/dynamic assignment changed')
    return dict(gpu_id=gpu_id,factory_android_dtb_sha256=hashlib.sha256(dt).hexdigest(),
                factory_hcb_sha256=vm.sha(hcb),grants=grants,base=BASE,size=SIZE,
                virtual_identity_only=not discovery,feature_discovery=discovery,
                original_feature_code=DISCOVERY_CODE if discovery else None,
                advertised_gpu_resources=[] if discovery else None,
                physical_gpu_revision_verified=False,
                reset_irq=reset_irq, original_irq_code=RESET_IRQ_CODE if reset_irq else None,
                irq_spis=SPIS if reset_irq else None,virtual_reset_ns=1000 if reset_irq else None,
                config=config, original_config_code=CONFIG_CODE if config else None,
                config_offsets=[0x304,0xf00,0xf04,0xf08,0xf0c] if config else [],
                empty_power=empty_power, original_power_code=EMPTY_POWER_CODE if empty_power else None,
                present_core_mask=0 if empty_power else None,
                gpu_jobs=False,gpu_mmu=False,irq=bool(reset_irq),scanout=False)

def runtime_state(qmp, jm_topology=False, *, vm3_control=False):
    props = (
        'gpu-id','id-reads','feature-reads','feature-last-offset','rejected','last-rejected','last-rejected-write',
        'irq-reads','irq-writes','reset-commands','reset-completions','gpu-irq-level',
        'power-reads','power-writes','config-reads','config-writes','jm-config','shader-config','tiler-config','l2-mmu-config','coherency-mode')
    present={p['name'] for p in qmp.execute('qom-list',dict(path=PATH))} if jm_topology or vm3_control else set()
    if jm_topology:
        props+=('jm-probe-reads',)
        props+=tuple(p for p in ('cache-commands','cache-completions') if p in present)
    if vm3_control:
        props+=('vm3-control','vm3-control-accesses','vm3-control-denied','as-present','as0-updates',
                'vm3-foreign-job-irq-clear','vm3-foreign-mmu-irq-clear',
                'vm4-job-mask-writes','vm4-mmu-mask-writes','last-rejected-value',
                'vm3-oem-transcfg-updates','vm3-translation-mode-rejections',
                'vm3-fragment-attempts','vm3-fragment-decoded','vm3-fragment-tiler-rejections',
                'vm3-zero-resolves','vm3-single-zero-clears','vm3-crc-invalidated-bytes','vm3-geometry-seen','vm3-geometry-samples',
                'vm3-unresolved-dependencies','vm3-stock-fans','vm3-stock-fan-primitives',
                'vm3-color-fans','vm3-color-clipped-fans','vm3-color-resolves','vm3-color-pixels',
                'vm3-fragment-matched-batches',
                'vm3-textured-attempts','vm3-textured-resolves','vm3-textured-bytes',
                'vm3-textured-nonzero-pixels','vm3-textured-shaded-pixels',
                'vm3-vertex-attempts','vm3-vertex-samples','vertex-descriptors-completed','vertices-written',
                'vm3-stock-vertices-written','vm3-vertex3-jobs','vm3-vertex4-jobs','vm3-vertex448-jobs','vm3-vertex45-jobs','vm3-compute-jobs',
                'as0-locks','as0-flushes','job-starts','job-completions','job-failures',
                'job-descriptors-completed','last-job-type','fragment-descriptors-completed',
                'fragment-bytes-written')
        props+=tuple(p for p in ('vm3-texture-loaded','vm3-texture-zero-opacity','vm3-texture-rgb-texels',
                                 'vm3-texture-alpha-texels','vm3-texture-rgb-samples',
                                 'fragment-te-unconditional',
                                 'vm3-linear-rgba-loads','vm3-linear-rgba-rgb-texels','vm3-linear-reverse-loads',
                                 'vm3-single-tile-zero-pitch-loads',
                                 'vm3-empty-fragment-failures','vm3-empty-fragment-profiles',
                                 'vm3-empty-discard-validated',
                                 'vm3-nearest-texture-loads','vm3-nearest-rgb-texels','vm3-next-queued',
                                 'vm3-next-dispatched','vm3-next-canceled','vm3-next-as-mismatch',
                                 'vm3-reset-tile-batches','tile-batch-count',
                                 'vm3-desc-attempts','vm3-desc-completed','vm3-desc-failed') if p in present)
    return {key:qmp.execute('qom-get',dict(path=PATH,property=key)) for key in props}

