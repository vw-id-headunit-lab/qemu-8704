
import hashlib
import importlib.util
import json
import mmap
import struct

import original_8704_vm as vm
from vlm_platform import Elf, factory_file, original, parse_dtb


BASES=(0x1b430000,0x1b480000,0x1b630000,0x1b680000,
       0x1c430000,0x1c480000,0x1c630000,0x1c680000)
OFFSET=0x258
MASK=0x3f3f0000
SOURCE='qnx/qtd/system-qla.part_qtd.qnx6.vw_cn'
DRIVER_SHA='f25a735bd63ad0df825470ecc3527c2002864e6c1f46a3aad94a419420346c93'
LIMIT_CODE_SHA='ec1c0ea9556aac6f145ee7fe4f970726be5735a3364d84950247934675e8981f'
TREX_BASES=(0x1b8a4000,0x1b8b4000,0x1b8c4000,0x1b8d4000)
TREX_INIT=((0,0x0b070000),(0x100,0x10101010),(0x180,0x10101010),
           (0x200,0x08080808),(0x280,0x08080808))
TREX_CODE_SHA='1ec0227dc3895c50cc728378484db2bc80b7a41bc98f03ea52d4252b69bbc038'
QOS_CODE_SHA='3e3a051dd1a99ffd124b60eb56426ce1d194219ed0944c9b39af847236d5976d'
QOS_TABLE_SHA='d3c1d8c0e4f5ad7cc347365e2cab79b1344ef9cac03f1dc4ae8587fb8a438717'
QOS_BASES=tuple(0x1c000000+0x10000*i for i in (*range(12),*range(15,24),26,12,13,14))+(
    0x1ccc0000,0x1ccd0000,0x1cd20000,0x1cd30000,0x1cd50000,0x1cd60000,0x1b370000,0x1b360000)
QOS_MASKS=((0,0x0ff00001),(0x10,0x00ff00ff),(0x20,0xf101),(0x24,0xffff),
           (0x2c,0xffff),(0x30,0xffff),(0x34,0xffff),(0x40,0xf101),(0x44,0xffff),
           (0x4c,0xffff),(0x50,0xffff),(0x54,0xffff))
PATH='/machine/exynos-qnx-bts'


def driver():
    base=vm.ROOT/'var/inputs/analysis/qnx-container-20261020'
    index=json.loads((base/'filesystem-inventory.json').read_bytes())
    source=factory_file(SOURCE)
    row,=[r for r in index['entries'] if r['path']=='/bin/bts-exynos' and r['type']=='regular']
    if row['sha256']!=DRIVER_SHA or row['size']!=35464:
        raise ValueError('Original QNX BTS inventory changed')
    spec=importlib.util.spec_from_file_location('qnx_bts_fs_reader',base/'inspect_qnx6.py')
    fs_reader=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fs_reader)
    with source.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as data:
        fs=fs_reader.Qnx6(data)
        raw=fs.read(fs.inode(row['inode']),0,row['size'])
    if hashlib.sha256(raw).hexdigest()!=DRIVER_SHA:
        raise ValueError('Original QNX BTS payload changed')
    return raw


def policy(native_qnx):
    if not native_qnx:
        raise ValueError('QNX BTS requires original VM2 and HCB')
    hcb=original('hcb/hcb.img').read_bytes()
    for vmid in (2,3,4):
        pos=hcb.index(f'VDTVM{vmid}\0\0'.encode())+8
        dt=parse_dtb(hcb[pos:pos+struct.unpack_from('>I',hcb,pos+4)[0]])
        node=dt['/vplatform/vl,bts@mem']
        regs=set(struct.iter_unpack('>QI',node['reg']))
        if (('vl,disabled' in node)!=(vmid!=2) or
            not {(base,0x10000) for base in BASES}.issubset(regs)):
            raise ValueError(f'Original VM{vmid} BTS ownership changed')
        itmon=dt['/vplatform/vl,itmon/vl,itmon@mem']
        regions=set(struct.iter_unpack('>QI',itmon['reg']))
        if (('vl,disabled' in itmon)!=(vmid!=2) or
                not {(base&~0xffff,0x10000) for base in TREX_BASES}.issubset(regions)):
            raise ValueError(f'Original VM{vmid} TREX ownership changed')
        if (not {(base,0x10000) for base in QOS_BASES[:-2]}.issubset(regions) or
                not {(base,0x10000) for base in QOS_BASES[-2:]}.issubset(regs)):
            raise ValueError(f'Original VM{vmid} per-port QoS ownership changed')
    raw=driver()
    if (tuple(struct.unpack_from('<Q',raw,0x4088+0x20*i)[0] for i in range(8))!=BASES or
        hashlib.sha256(raw[0x2f68:0x2fa0]).hexdigest()!=LIMIT_CODE_SHA):
        raise ValueError('Original QNX BTS base/limit ABI changed')
    elf=Elf(raw)
    if (tuple(struct.unpack_from('<Q',raw,0x4008+0x20*i)[0] for i in range(4))!=TREX_BASES or
            hashlib.sha256(elf.at(0x31b0,0x28)).hexdigest()!=TREX_CODE_SHA):
        raise ValueError('Original QNX TREX boot-init ABI changed')
    table=elf.at(0x141a8,0x108*37)
    rows=[table[off:off+0x108] for off in range(0,len(table),0x108)]
    active=tuple(struct.unpack_from('<Q',row,8)[0] for row in rows
                 if row[0x18] and row[0xfc] and not struct.unpack_from('<I',row,0x1c)[0])
    if (active!=QOS_BASES or hashlib.sha256(table).hexdigest()!=QOS_TABLE_SHA or
            hashlib.sha256(elf.at(0x2cf0,0x218)).hexdigest()!=QOS_CODE_SHA):
        raise ValueError('Original QNX per-port QoS boot ABI changed')
    return dict(profile='virtual-qnx-bts-dmc-limit-v1',vm=2,hcb_node='/vplatform/vl,bts@mem',
                addresses=[base+OFFSET for base in BASES],mask=MASK,
                boot_pc_in_member=0x2f78,driver_sha256=DRIVER_SHA,
                trex=dict(bases=TREX_BASES,words=TREX_INIT,boot_pc_in_member=0x31b8,
                          hcb_node='/vplatform/vl,itmon/vl,itmon@mem'),
                qos=dict(bases=QOS_BASES,words=QOS_MASKS,boot_pc_in_member=0x2d0c),
                memory_arbitration=False,dma_timing=False)


def runtime(qmp):
    return {f'dmc{i}':{k:qmp.execute('qom-get',{'path':PATH,'property':f'dmc{i}-{k}'})
                       for k in ('limit','reads','writes','rejected')} for i in range(len(BASES))}


def trex_runtime(qmp):
    properties={row['name'] for row in qmp.execute('qom-list',{'path':PATH})}
    if 'trex0-writes' not in properties:
        return None
    return {f'trex{i}':{key:qmp.execute('qom-get',{'path':PATH,'property':f'trex{i}-{key}'})
                        for key in ('reads','writes','rejected')} for i in range(len(TREX_BASES))}


def qos_runtime(qmp):
    properties={row['name'] for row in qmp.execute('qom-list',{'path':PATH})}
    if 'qos-configured' not in properties:
        return None
    return {key:qmp.execute('qom-get',{'path':PATH,'property':'qos-'+key})
            for key in ('reads','writes','rejected','configured')}
