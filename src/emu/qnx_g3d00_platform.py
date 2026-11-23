
import hashlib
import struct

from vlm_platform import Elf, factory_file, original, parse_dtb

BASE=0x19300000
SIZE=0x5000
VIRTUAL_ID=0x72110000
SPIS={'gpu':247,'job':248,'mmu':249}
PATH='/machine/exynos-mali-g3d00'
IFS='qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn'
MEMBER=(0x69a4000,238491,'608ee478b0a8de97e772d997b15d4c2872db405db308b0de8a3e8e6a7f191cd2')
CODE=(
    (0x15850,0x398,'34f95b339c0bbef6ee3c856c072cc6f6f6a5dd433e58990a2da874aa3234b346'),
    (0x14e70,0x678,'58c396595c761a7a5cbcdaf4e4d8358a8bb6802831446eafca330f5a0dffd50f'),
    (0x29f30,0xad8,'69d60f5ff4562361f51464042989aa68b9a1a0b2e805ec5ec2f4870c03b39d5a'),
)
AS0_CODE=(
    (0x25308,0xf4,'b3aefa76bcbd400a277ea3873df1aca001e38b659f86f13821c7f0c6998ec382'),
    (0x25400,0x1bc,'76d9618d0b050394aafdb96201f5caa187450797f3d9d54b26cf5667a07fca41'),
    (0x257d8,0x9c,'89b3a509ddcaf0f97dc2eb9f61d54bfd31b597f0bf5b9e994f900434893fe15f'),
)
AS0_FIELDS=('as-present','as0-updates','as0-locks','as0-flushes','as0-locked',
            'as0-active-ttbr','as0-active-memattr','as0-active-transcfg','as0-lock-region')


VM2_DMA_BANKS=((0x90000000,0xa0000000),(0x880000000,0x8ce000000))
VM2_SCREEN_OWNER_BANK=(0xe0000000,0xe3c00000)
VM2_SCREEN_LEAF_BANK=(0xe0210000,0xe3c00000)
QNX_DTB='qnx/qtd/icas3gp-qla-vm.dtb'
CONTROL_JOB_CODE=(0x18528,0x1a8,'59776a95c01ef4315cc35292d4d6cc2080ba168e12d426032a60c721bfda1e9e')
CONTROL_FIELDS=('control-jobs','job-starts','job-completions','job-failures','last-job-type')
CHAIN_FIELDS=('job-descriptors-completed','job-cache-flushes','vertex-samples')


def validate_dma_banks(hcb, qnx_dtb):
    p=hcb.index(b'VDTHYP\0\0')+8
    n=struct.unpack_from('>I',hcb,p+4)[0]
    tree=parse_dtb(hcb[p:p+n])
    owner='/vlm/vm@2/vm-memory'
    ranges=tuple(struct.iter_unpack('>QQ',tree[owner]['reg']))
    owned=(*VM2_DMA_BANKS,VM2_SCREEN_OWNER_BANK)
    if any((lo,hi-lo) not in ranges for lo,hi in owned):
        raise ValueError('Original G3D00 VM2 private DMA banks changed')
    for name,node in tree.items():
        if name.startswith('/vlm/vm@') and name.endswith('/vm-memory') and name!=owner:
            if any(start<hi and lo<start+size for start,size in struct.iter_unpack('>QQ',node['reg'])
                   for lo,hi in owned):
                raise ValueError('Original G3D00 private DMA bank overlaps another VM')
    guest=parse_dtb(qnx_dtb)
    lo,hi=VM2_SCREEN_OWNER_BANK
    leaf,leaf_hi=VM2_SCREEN_LEAF_BANK
    if (leaf_hi!=hi or not any((lo,hi-lo) in tuple(struct.iter_unpack('>QI',node['reg']))
            for path,node in guest.items() if path.startswith('/memory@'))):
        raise ValueError('Original QNX Screen RAM is not ordinary guest memory')
    debug='/reserved-memory/debug_snapshot'
    if (guest[debug+'/header']['reg']!=struct.pack('>QI',lo,0x10000)
            or guest[debug+'/log_kernel']['reg']!=struct.pack('>QI',lo+0x10000,leaf-lo-0x10000)):
        raise ValueError('Original QNX debug snapshot RAM boundary changed')
    for path,node in guest.items():
        if (path.startswith('/reserved-memory/') and 'reg' in node
                and any(start<leaf_hi and leaf<start+size
                        for start,size in struct.iter_unpack('>QI',node['reg']))):
            raise ValueError('Original QNX Screen DMA overlaps reserved guest RAM')


def policy(native,jm_topology=False,control_jobs=False):
    if control_jobs and not jm_topology:
        raise ValueError('G3D00 control jobs require JM and AS0')
    if not native:
        raise ValueError('G3D00 requires original QNX/HCB')
    hcb=original('hcb/hcb.img').read_bytes()
    if control_jobs:
        validate_dma_banks(hcb, factory_file(QNX_DTB).read_bytes())
    for vmid in (2,3,4):
        p=hcb.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        n=struct.unpack_from('>I',hcb,p+4)[0]
        tree=parse_dtb(hcb[p:p+n])
        path='/vplatform/vl,g3d00/vl,g3d00'
        mem=tree[path+'@mem']
        if (mem.get('reg')!=struct.pack('>QI',BASE,SIZE)
                or ('vl,disabled' in mem)!=(vmid!=2) or 'vl,dyn-resource-id' in mem):
            raise ValueError(f'VM{vmid} G3D00 ownership changed')
        for name,spi in SPIS.items():
            irq=tree[f'{path}_{name}@irq']
            if (irq.get('interrupts')!=struct.pack('>III',0,spi,4)
                    or ('vl,disabled' in irq)!=(vmid!=2)):
                raise ValueError(f'VM{vmid} G3D00 IRQ grant changed')
    with factory_file(IFS).open('rb') as f:
        f.seek(MEMBER[0])
        raw=f.read(MEMBER[1])
    if hashlib.sha256(raw).hexdigest()!=MEMBER[2]:
        raise ValueError('Original QNX G3D00 kbase member changed')
    elf=Elf(raw)
    if any(hashlib.sha256(elf.at(a,n)).hexdigest()!=sha for a,n,sha in CODE):
        raise ValueError('Original QNX G3D00 discovery/reset code changed')
    if jm_topology and any(hashlib.sha256(elf.at(a,n)).hexdigest()!=sha for a,n,sha in AS0_CODE):
        raise ValueError('Original QNX G3D00 MMU configure/flush code changed')
    if control_jobs:
        a,n,sha=CONTROL_JOB_CODE
        if hashlib.sha256(elf.at(a,n)).hexdigest()!=sha:
            raise ValueError('Original QNX G3D00 control job submission code changed')
    return dict(vm=2,base=BASE,size=SIZE,virtual_gpu_id=VIRTUAL_ID,irq_spi=SPIS,
                original_member=MEMBER,code=CODE,physical_revision_verified=False,
                reset_only_irq=not control_jobs,advertised_job_slots=3 if jm_topology else 0,
                expected_address_spaces=int(jm_topology),as0_code=AS0_CODE if jm_topology else (),
                address_space_translation=bool(control_jobs),
                vm2_dma_banks=VM2_DMA_BANKS if control_jobs else (),
                vm2_dma_extra_leaf_bank=VM2_SCREEN_LEAF_BANK if control_jobs else None,
                control_job_code=CONTROL_JOB_CODE if control_jobs else (),
                control_jobs=bool(control_jobs),
                jm_topology=bool(jm_topology),job_present_consumer='0x14f20',
                virtual_cycle_clock_hz=100_000_000,gpu_jobs=bool(control_jobs),
                dma=bool(control_jobs),shader=False,scanout=False)


def runtime(qmp,jm_topology=False):
    fields=('gpu-id','id-reads','feature-reads','feature-last-offset','rejected',
            'last-rejected','last-rejected-write','last-rejected-value','irq-reads','irq-writes',
            'reset-commands','reset-completions','gpu-irq-level',
            'power-reads','power-writes','config-reads','config-writes',
            'cycle-running','cycle-starts','cycle-stops','cycle-reads')
    if jm_topology:
        fields+=('jm-probe-reads',)
    state={name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in fields}
    if jm_topology:
                                                                           
                                                                         
        properties={entry['name'] for entry in qmp.execute('qom-list',dict(path=PATH))}
        state['as0_model_available']=all(name in properties for name in AS0_FIELDS)
        if state['as0_model_available']:
            state.update({name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in AS0_FIELDS})
        if all(name in properties for name in CONTROL_FIELDS):
            names=CONTROL_FIELDS+tuple(name for name in ('latest-flush-id','latest-flush-reads') if name in properties)
            state.update({name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in names})
            state['control_chain_model_available']=all(name in properties for name in CHAIN_FIELDS)
            if state['control_chain_model_available']:
                state.update({name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in CHAIN_FIELDS})
            vertex=('vertex-descriptors-completed','vertices-written')
            if all(name in properties for name in vertex):
                state.update({name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in vertex})
            tiler=('tiler-descriptors-completed','primitives-binned')
            if all(name in properties for name in tiler):
                state.update({name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in tiler})
            fragment=('fragment-descriptors-completed','fragment-pixels-shaded','fragment-bytes-written')
            if all(name in properties for name in fragment):
                state.update({name:qmp.execute('qom-get',dict(path=PATH,property=name)) for name in fragment})
            if 'fragment-te-unconditional' in properties:
                state['fragment-te-unconditional']=qmp.execute('qom-get',dict(path=PATH,property='fragment-te-unconditional'))
            for name in ('qnx-screen-leaf-read-maps','qnx-screen-leaf-write-maps',
                         'qnx-viewport-cross-page-jobs'):
                if name in properties:
                    state[name]=qmp.execute('qom-get',dict(path=PATH,property=name))
    return state
