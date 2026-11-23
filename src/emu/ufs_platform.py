





from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
from collections import deque

import original_8704_vm as vm
import vlm_platform as platform

HCI = 0x17E00000
UFS0_TIMEOUT_DIVIDER = HCI + 0x1108
VGEN = 0x17DD0000
S2MPU = 0x17CA0000
SHAREABILITY = 0x17C20710
SHAREABILITY_PROFILE = "virtual-ufs0-shareability-v1"
PHY_ISOLATION = 0x10460728
PHY_ISOLATION_PROFILE = "virtual-ufs0-phy-isolation-v1"
                                                                         
                                                                            
                                                                             
UTRL, COMMAND, DATA, TABLE = 0x40100000, 0x40101000, 0x40110000, 0x40108000
DATA_PERMISSION_WORD = TABLE + (((DATA & 0x3fffffff) >> 12) // 16) * 4
BLOCK_SIZE = 4096


def descriptor_options(profile,native_ufs):
    if profile is None:return []
    if profile!='virtual-samsung-byte-v1' or not native_ufs:
        raise ValueError('Explicit Samsung byte descriptors require native UFS')
    return ['-global','ufs.x-samsung-byte-granularity=on']


def descriptor_policy():
    return {'profile':'virtual-samsung-byte-v1','default_enabled':False,
            'utrd':'response length/offset and PRDT offset are bytes; PRDT length is bytes/16 per SG entry',
            'source':'Original LK52C50/509A0/50B8C and Linux exynosauto UFSHCD_QUIRK_PRDT_BYTE_GRAN',
            'guest_descriptor_rewritten':False,'partial_sg_entry':'structural failure, not DMA permission wait',
            'mcq_supported':False,'factory_images_modified':False,'lk_done_written':False,
            'limits':'Legacy descriptor units only; no physical PHY/FMP/multi-host or OEM root layout/handoff proof'}


def ufs0_config_binding(address):
    route = route_contract()
    inv = platform.native_ufs_inventory()
    node = next(r for r in inv['nodes'] if r['path']=='/vplatform/vl,ufs0/vl,ufs0@mem')
    raw = bytes.fromhex(node['properties']['reg']['hex'])
    if (address,4) not in set(struct.iter_unpack('>QI',raw)):
        raise ValueError(f'Original HCB no longer exposes UFS0 config at {address:#x}')
    return dict(hcb_sha256=route['hcb_sha256'],driver_sha256=route['driver_sha256'])


def phy_isolation_policy(profile, native_pmu_ufs):
    if profile is None:
        return None
    if profile != PHY_ISOLATION_PROFILE or not native_pmu_ufs:
        raise ValueError('Explicit UFS0 PHY isolation requires native UFS and PMU')
    return dict(profile=profile,default_enabled=False,address=hex(PHY_ISOLATION),
                width=4,mask=1,virtual_cold_value=0,
                original_consumer='devb-ufs +0x147dc writes 1 for HCI 0x17e00000',
                upstream_revision='9f6af9a6c2cc38',phy_link_implemented=False,
                ready_injected=False,**ufs0_config_binding(PHY_ISOLATION))


def shareability_policy(profile, native_ufs):
    if profile is None:
        return None
    if profile != SHAREABILITY_PROFILE or not native_ufs:
        raise ValueError('Explicit UFS0 shareability requires native Auto9 UFS')
    return dict(profile=profile,default_enabled=False,address=hex(SHAREABILITY),width=4,
                mask=0x6,virtual_cold_value=0,**ufs0_config_binding(SHAREABILITY),
                original_consumer='devb-ufs +0x15da8..0x15dc8 maps four bytes and ORs 0x6',
                upstream_revision='c4101e55974cc7d835fbd2d8e01553a3f61e9e75',
                dma_policy_changed=False,ready_injected=False)


def utrd(command=COMMAND, direction=0, prds=0, byte_granularity=False):
    if command % 128 or direction not in (0, 0x02000000, 0x04000000) or not 0 <= prds <= 32:
        raise ValueError('Invalid legacy UFS descriptor')
                                                                              
    return struct.pack('<6I4H', 0x11000000 | direction, 0, 15, 0,
                       command & 0xffffffff, command >> 32,
                       1152 if byte_granularity else 288, 1024 if byte_granularity else 256,
                       prds*16 if byte_granularity else prds, 2048 if byte_granularity else 512)


def scsi_request(opcode, lba, blocks=1):
    if opcode not in (0x28, 0x2A) or not 0 <= lba <= 0xffffffff or not 1 <= blocks <= 0xffff:
        raise ValueError('Invalid READ/WRITE10 arguments')
    request = bytearray(288)
    request[0:2] = bytes((1, 0x40 if opcode == 0x28 else 0x20))
    struct.pack_into('>I', request, 12, blocks * BLOCK_SIZE)
    request[16] = opcode
    struct.pack_into('>I', request, 18, lba)
    struct.pack_into('>H', request, 23, blocks)
    return bytes(request)


def route_contract():
    driver = vm.ROOT/'var/inputs/support/归档/虚拟机平台对照/ufs-native-model--devb-ufs'
    driver_sha = vm.sha(driver)
    if driver_sha != 'cbd669f438626d6ed3e014b3d92b660ee4bde3d650f8cd6d4c15f94aa1c3b835':
        raise ValueError('Original QNX UFS driver identity changed')
    vgens, mpus = platform.native_vgen_layout(), platform.native_s2mpu_layout()
    vi = next(i for i, row in enumerate(vgens) if row['base'] == VGEN)
    mi = next(i for i, row in enumerate(mpus) if row['base'] == S2MPU)
    inventory = platform.native_ufs_inventory()
    irq = next(row for row in inventory['nodes'] if row['path'] == '/vplatform/vl,ufs0/vl,ufs0@irq')
    if irq['properties']['interrupts']['u32'] != [0, 227, 4] or vgens[vi]['layout'] != 2:
        raise ValueError('Original UFS0 route changed')
    mem = next(row for row in inventory['nodes'] if row['path'] == '/vplatform/vl,ufs0/vl,ufs0@mem')
    apertures = struct.iter_unpack('>QI', bytes.fromhex(mem['properties']['reg']['hex']))
    if not any(base <= UFS0_TIMEOUT_DIVIDER <= base+size-4 for base,size in apertures):
        raise ValueError('Original HCB UFS0 timeout divider aperture changed')
    return {'hci': HCI, 'vgen': vgens[vi], 's2mpu': mpus[mi], 'spi': 227,
            'vgen_path': f'/machine/exynos-vgen{vi}', 's2mpu_path': f'/machine/exynos-s2mpu{mi}',
            'hcb_sha256': inventory['sha256'],
            'vendor_timeout_divider': {'address':hex(UFS0_TIMEOUT_DIVIDER),'width':4,
                                       'read_only_model':True,'timeout_clock_modeled':False},
            'binding_evidence': 'Original devb-ufs sub_4068 at 0x4ac0: HVC 0x61, S2MPU17CA0000,VGEN17DD0000',
            'driver_path': str(driver), 'driver_sha256': driver_sha}


class QTest:
    def __init__(self, port):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.stream = self.socket.makefile('rb')
        self.transcript, self.events = [], []

    def call(self, command):
        self.socket.sendall(command.encode('ascii') + b'\n')
        while True:
            line = self.stream.readline(32768).decode('ascii').strip()
            self.transcript.append({'command': command, 'response': line})
            if line.startswith('IRQ '):
                self.events.append(line)
                continue
            if not line.startswith('OK'):
                raise RuntimeError('QTest failure: ' + repr(line))
            return line

    def read(self, address):
        return int(self.call(f'readl {address:#x}').split()[1], 16)

    def write(self, address, value):
        self.call(f'writel {address:#x} {value:#x}')

    def put(self, address, data):
        self.call(f'write {address:#x} {len(data):#x} 0x{data.hex()}')

    def get(self, address, length):
        return bytes.fromhex(self.call(f'read {address:#x} {length:#x}').split()[1].removeprefix('0x'))

    def close(self):
        self.stream.close()
        self.socket.close()


def replay_original_power(qt, phase):







    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_PC
    source = Path(route_contract()['driver_path'])
    raw = source.read_bytes(); elf = platform.Elf(raw)
    if phase not in (0,1):raise ValueError('Require original callback phase 0/1')
    def branch(pc):
        word = struct.unpack('<I',elf.at(pc,4))[0]
        if word>>26!=0b100101:raise ValueError('Original BL instruction changed')
        displacement=word&0x3ffffff
        if displacement&0x2000000:displacement-=0x4000000
        return pc+4*displacement
    delay_pc=branch(0x16350)
    if branch(0x16358)!=delay_pc or branch(0x163cc)!=delay_pc:
        raise ValueError('Original delay call identity mismatch')
    if elf.at(0x16b80,8)!=bytes.fromhex('00008052c0035fd6'):
        raise ValueError('Original PHY callback body changed')
    uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
    highest=max(row['virtual']+row['memory_size'] for row in elf.segments if row['type']==1)
    if highest>0x80000:raise ValueError('Unexpected original driver LOAD bound')
    uc.mem_map(0,(highest+0xfff)&~0xfff)
    for row in elf.segments:
        if row['type']==1:uc.mem_write(row['virtual'],elf.read(row['offset'],row['file_size']))
    uc.mem_map(0x100000,0x10000)
    uc.mem_write(0x100050,struct.pack('<Q',0x101000))
    uc.mem_write(0x10104c,struct.pack('<I',HCI+0x1100))
    sentinel=0x10ffe0
    uc.reg_write(UC_ARM64_REG_X0,0x100000);uc.reg_write(UC_ARM64_REG_X1,phase)
    uc.reg_write(UC_ARM64_REG_SP,0x10f000);uc.reg_write(UC_ARM64_REG_LR,sentinel)
    events=[];trace=deque(maxlen=64);count=0;failure=None
    def stop(reason):
        nonlocal failure
        failure=reason;uc.emu_stop()
    def code(engine,address,size,data):
        nonlocal count
        count+=1;trace.append(hex(address))
        if address==delay_pc:
            milliseconds=engine.reg_read(UC_ARM64_REG_X0)
            if milliseconds not in (1,5):return stop('Unexpected original delay argument')
            qt.call(f'clock_step {milliseconds*1000000}')
            events.append({'operation':'qnx-delay','milliseconds':milliseconds})
            engine.reg_write(UC_ARM64_REG_PC,engine.reg_read(UC_ARM64_REG_LR))
        elif not any(begin<=address<end for begin,end in ((0x16330,0x164c0),(0x169e0,0x16ae4),(0x16b80,0x16b88))):
            stop('Unexpected native execution target '+hex(address))
    def mmio_read(engine,offset,size,data):
        if size!=4:return stop('Unexpected OEM MMIO read width') or 0
        address=0x17e01000+offset
        value=qt.read(address);events.append({'operation':'read','address':hex(address),'value':value})
        return value
    def mmio_write(engine,offset,size,value,data):
        if size!=4:return stop('Unexpected OEM MMIO write width')
        address=0x17e01000+offset
        qt.write(address,value);events.append({'operation':'write','address':hex(address),'value':value})
    uc.mmio_map(0x17e01000,0x1000,mmio_read,None,mmio_write,None)
    uc.hook_add(UC_HOOK_CODE,code)
    try:uc.emu_start(0x16330,sentinel,count=20000)
    except Exception as exc:failure=repr(exc)
    pc=uc.reg_read(UC_ARM64_REG_PC)
    return {'source':str(source),'sha256':vm.sha(source),'phase':phase,'instructions':count,
            'pc':hex(pc),'return_value':uc.reg_read(UC_ARM64_REG_X0),
            'returned':pc==sentinel and failure is None,'error':failure,'events':events,'last_pcs':list(trace),
            'oem_code_modified':False,'mmio_backend':'actual QEMU native UFS vendor model via QTest',
            'os_boundary':'Only QNX delay() advances requested virtual milliseconds',
            'original_system_boot_verified':False}


PRELINK_PROFILE='virtual-ufs0-prelink-config-v1'
PRELINK_UNIPRO=0x17e80000
PRELINK_PMA=0x17e04000
PRELINK_PMA_WORDS=((0x8c,0x80),(0x74,0x10),(0x110,0xb5),(0x134,0x43),(0x16c,0x20),(0x178,0xc0),
                   (0xe0,0x12),(0x164,0x58),(0x1b0,0x18),(0x1b4,2),(0x8c,0xc0),(0x8c,0),(0x108,0x5d),(0x10c,0))
PRELINK_PROPERTIES=('unipro-period','unipro-writes','pma-writes','prelink-rejected','prelink-last-pma')

def prelink_policy(profile, native):
    if profile is None:return None
    if profile!=PRELINK_PROFILE or not native:
        raise ValueError('Pre-link config requires native UFS and original LK/QNX')
    route=route_contract()
    data=platform.Elf(Path(route['driver_path']).read_bytes())
    words=[struct.unpack('<5I',data.at(0x18508+20*i,20)) for i in range(36)]
    if (words[0]!=(0x44,0,0x1ffff,0xf,0x1f) or words[-1][2] or
        tuple((o,v) for o,v,mask,op,_ in words if op in (3,4) and mask)!=PRELINK_PMA_WORDS):
        raise ValueError('Original UFS pre-link table changed')
    row=next(n for n in platform.native_ufs_inventory()['nodes'] if n['path']=='/vplatform/vl,ufs0/vl,ufs0@mem')
    regs=row['properties']['reg']['u32']
    apertures={(hi<<32|lo,sz) for hi,lo,sz in zip(regs[::3],regs[1::3],regs[2::3])}
    if not {(PRELINK_UNIPRO,0x8000),(PRELINK_PMA,0xc00)}<=apertures:
        raise ValueError('Factory UFS0 pre-link MMIO grants changed')
    return dict(profile=profile,driver_sha256=route['driver_sha256'],unipro_period_ns_x16=96,
                unipro=PRELINK_UNIPRO,pma=PRELINK_PMA,one_lane=True,pma_writes=PRELINK_PMA_WORDS,
                physical_mphy_or_pll=False,root_mount_verified=False)

def prelink_runtime(qmp):
    return {k:qmp.execute('qom-get',dict(path='/machine/exynos-ufs0',property=k)) for k in PRELINK_PROPERTIES}

POWER_PROFILE='virtual-ufs0-power-config-v1'
POWER_PROPERTIES=('power-timer-mask','power-timer-writes','power-pma-writes')
POWER_TIMERS=((0x7888,0x1f80),(0x788c,0x6e40),(0x7890,0x4ec0),
              (0x78b8,0x2ee0),(0x78bc,0x7d00),(0x78c0,0x3e80))
POWER_PWM=((0xc8,0x40,0x3ff),(0xf0,0x77,0x3ff),(0x120,0x80,0x3ff),
           (0x128,0,0x3ff),(0x12c,0,0x3ff),(0x134,0x43,0x3ff))
POWER_HS=((0xc8,0xbc,0x1fc00),(0xf0,0x7f,0x1fc00),(0x120,0xc0,0x1fc00),
          (0x128,8,0x1000),(0x128,2,0x4000),(0x128,0,0x10000),
          (0x12c,0x10,0x11000),(0x12c,0,0x4000),(0x134,0xd3,0x1000),
          (0x134,0x73,0x4000),(0x134,0x63,0x10000))

def power_policy(profile, prelink):
    if profile is None:
        return None
    if profile!=POWER_PROFILE or not prelink:
        raise ValueError('Power-mode register config requires the original UFS prelink profile')
    route=route_contract()
    elf=platform.Elf(Path(route['driver_path']).read_bytes())
    tables=[]
                                                                              
    for offset,mask,rows in ((0x1d1f8,0x3ff,POWER_PWM),(0x1d378,0x1fc00,POWER_HS),
                             (0x1d558,0x1fc00,POWER_HS)):
        expected=[(reg,value,mask,0xa,0x1f) for reg,value in POWER_TIMERS]
        expected.extend((reg,value,applicable,4,0x1f) for reg,value,applicable in rows)
        expected.append((0,)*5)
        data=elf.read(offset,20*len(expected))
        if list(struct.iter_unpack('<5I',data))!=expected:
            raise ValueError(f'Original power config table changed at file offset {offset:#x}')
        tables.append(dict(file_offset=offset,words=expected))
    return dict(profile=profile, driver_sha256=route['driver_sha256'], tables=tables,
                timer_addresses=[PRELINK_UNIPRO+reg for reg,_ in POWER_TIMERS],
                phase='after original LK', physical_timer_or_mphy=False, irq_injected=False)

def power_configure(qmp):
    qmp.execute('qom-set',dict(path='/machine/exynos-ufs0',property='virtual-power-config-v1',value=True))

def power_runtime(qmp):
    return {k:qmp.execute('qom-get',dict(path='/machine/exynos-ufs0',property=k)) for k in POWER_PROPERTIES}

HOST0_PROFILE='virtual-ufs0-host0-v1'
HOST0_PROPERTIES=('host0-last-vid','host0-invalid','host0-s2-denied')

def host0_policy(profile, native):
    if profile is None:return None
    if profile!=HOST0_PROFILE or not native:
        raise ValueError('Physical HCI host0 requires original native QNX UFS')
    route=route_contract()
    if route['vgen']['factory_vid']!=[2,3,4]:
        raise ValueError('Original UFS0 VGEN host table changed')
    raw=platform.original('hcb/hcb.img').read_bytes()
    hosts=[]
    for host,(vmid,base,spi) in enumerate(((2,HCI,227),(3,HCI+0x10000,228),(4,HCI+0x20000,229))):
        pos=raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        tree=platform.parse_dtb(raw[pos:pos+struct.unpack_from('>I',raw,pos+4)[0]])
        suffix=f'_{host}' if host else ''
        node=f'/vplatform/vl,ufs0{suffix}/vl,ufs0{suffix}'
        mem,irq=tree[node+'@mem'],tree[node+'@irq']
        ranges=set(struct.iter_unpack('>QI',mem['reg']))
        if (base,0x1000 if host else 0x2000) not in ranges or any(
            'vl,disabled' in row for row in (mem,irq)) or irq['interrupts']!=struct.pack('>3I',0,spi,4):
            raise ValueError('Original UFS host binding changed: '+node)
        if host and any('vl,disabled' not in tree['/vplatform/vl,ufs0/vl,ufs0'+end] for end in ('@mem','@irq')):
            raise ValueError('Original physical UFS window must be disabled in VH guest')
        hosts.append(dict(host=host,vmid=vmid,hci=base,spi=spi))
    return dict(profile=profile,hosts=hosts,modeled_hci=HCI,vgen_index=0,required_vid=2,
                s2mpu_enforced=True,virtual_hci_implemented=False,route=route)

def host0_runtime(qmp):
    return {k:qmp.execute('qom-get',dict(path='/machine/exynos-ufs0',property=k)) for k in HOST0_PROPERTIES}

PLAIN128_PROFILE='virtual-ufs0-plain-prdt128-v1'
PLAIN128_PROPERTY='virtual-plain-prdt128-v1'

def plain128_policy(profile, native):
    if profile is None:
        return None
    if profile!=PLAIN128_PROFILE or not native:
        raise ValueError('Extended PRDT requires the native QNX host0, power config and byte-unit legacy UFS')
    return dict(profile=profile, driver_sha256=route_contract()['driver_sha256'],
                prdt_stride=128, mode='clear descriptors only', guest_bytes_modified=False,
                source='Original QNX READ10 LBA0, byte length128 at offset1024; one 4096-byte data segment',
                primary_reference='https://android.googlesource.com/kernel/common/+/8164851725c867f74fc0e157f13a7f257da4a7db/drivers/ufs/host/ufs-exynos.c',
                encryption_implemented=False, s2mpu_enforced=True)

def configure_plain128(qmp, value=True):
    qmp.execute('qom-set',dict(path='/machine/exynos-ufs0',property=PLAIN128_PROPERTY,value=value))

def observe_slot0(qmp, output, hci_raw, byte_granularity, prdt_stride=16, slot_index=0):

    if not 0 <= slot_index <= (struct.unpack_from('<I',hci_raw)[0] & 31):
        raise ValueError('Slot outside reported legacy UFS CAP range')
    def get(addr,n,label):
        if not 0x80000000<=addr<=0x100000000-n:
            return None
        target=output/(f'ufs-runtime-slot{slot_index}-'+label+'.bin')
        qmp.execute('pmemsave',dict(val=addr,size=n,filename=str(target)))
        data=target.read_bytes()
        if len(data)!=n:raise ValueError('Short UFS slot snapshot')
        return data
    ut=struct.unpack_from('<Q',hci_raw,0x50)[0]
    detail=dict(utrl=hex(ut),slot=slot_index,scope='Final paused UTRD/UPIU RAM, not history or disk-read proof')
    data=get(ut+slot_index*32,32,'utrd') if ut else None
    if data is None:return detail
    command=struct.unpack_from('<Q',data,16)[0]
    rsp_len,rsp_offset=struct.unpack_from('<HH',data,24)
    unit=1 if byte_granularity else 4
    detail.update(command=hex(command),ocs=data[8]&0xf,response_offset=rsp_offset*unit,response_length=rsp_len*unit)
    request=get(command,32,'request')
    response=get(command+rsp_offset*unit,32,'response') if rsp_len*unit>=32 and rsp_offset else None
    if request:
        detail.update(request_transaction=request[0],request_opcode=request[12],request_idn=request[13])
        if request[0] == 1:
            detail.update(scsi_lun=request[2],scsi_cdb=request[16:32].hex(),
                          expected_data_bytes=struct.unpack_from('>I',request,12)[0])
            length, offset = struct.unpack_from('<HH',data,28)
            prds = length//prdt_stride if byte_granularity and length%prdt_stride==0 else length if not byte_granularity else 0
            detail['prdt'] = dict(raw_length=length,byte_offset=offset*unit,stride=prdt_stride,entries=[])
            if 0 < prds <= 32 and offset:
                                                                                 
                for i in range(prds):
                    header=get(command+offset*unit+i*prdt_stride,16,f'prdt-header-{i:02d}')
                    if header is not None:
                        lo,hi,rsv,sz=struct.unpack('<4I',header)
                        detail['prdt']['entries'].append(dict(address=lo|(hi<<32),reserved=rsv,
                                                            bytes=(sz&0x3ffff)+1,high_size_flags=sz>>18))
    if response:
        detail.update(response_transaction=response[0],response_result=response[6],scsi_status=response[7])
    return detail

def verify_completed_read(qmp, output, base, slot):

    result=dict(slot=slot['slot'], compared=False, scope='Final paused RAM only; not a mount or all reads')
    if (slot.get('ocs'),slot.get('response_transaction'),slot.get('response_result'),
            slot.get('scsi_status'),slot.get('scsi_lun'))!=(0,0x21,0,0,0):
        return dict(result,reason='Not a completed good LUN0 SCSI response')
    cdb=bytes.fromhex(slot['scsi_cdb'])
    if cdb[0]==0x28:
        lba,blocks=struct.unpack_from('>I',cdb,2)[0],struct.unpack_from('>H',cdb,7)[0]
    elif cdb[0]==0x88:
        lba,blocks=struct.unpack_from('>QI',cdb,2)
    else:
        return dict(result,reason='Not READ(10) or READ(16)')
    entries=slot.get('prdt',{}).get('entries',[])
    n=blocks*BLOCK_SIZE
    if (not 0<n<=65536 or slot.get('expected_data_bytes')!=n
            or sum(r['bytes'] for r in entries)!=n
            or any(not 0x80000000<=r['address']<=0x100000000-r['bytes'] or
                   r['reserved'] or r['high_size_flags'] for r in entries)):
        return dict(result,reason='Outside bounded clear READ DMA capture')
    if callable(base):
        expected=base(lba*BLOCK_SIZE,n)
    else:
        with Path(base).open('rb') as f:
            f.seek(lba*BLOCK_SIZE)
            expected=f.read(n)
    if len(expected)!=n:
        return dict(result,reason='Requested bytes outside base image')
    actual=bytearray()
    for i,r in enumerate(entries):
                                                                              
        target=output/f'ufs-runtime-read-slot{slot["slot"]}-{i:02d}.tmp'
        qmp.execute('pmemsave',dict(val=r['address'],size=r['bytes'],filename=str(target)))
        actual.extend(target.read_bytes())
        target.unlink()
    return dict(result,compared=True,lba=lba,blocks=blocks,bytes=n,
                matches=actual==expected,expected_sha256=hashlib.sha256(expected).hexdigest(),
                guest_sha256=hashlib.sha256(actual).hexdigest())

def observe_s2mpu(qmp, output, slot):

    def get(addr,n,label):
        f=output/('ufs-s2mpu-'+label+'.bin')
        qmp.execute('pmemsave',dict(val=addr,size=n,filename=str(f)))
        raw=f.read_bytes()
        if len(raw)!=n:raise ValueError('Short S2MPU readback')
        return raw
    status,lo,hi,_,info,*_=struct.unpack('<8I',get(S2MPU+0x2040,32,'vid2-fault'))
    fault=lo|(hi<<32)
    controls=struct.unpack('<2I',get(S2MPU,8,'controls'))
    addresses={r['address'] for r in slot.get('prdt',{}).get('entries',[])}
    if fault:
        addresses.add(fault)
    detail=[]
    for addr in sorted(addresses):
        if addr>=1<<36:
            continue
        group=addr>>30
        pa=(S2MPU+0x4000+2*0x200+group*8)
        base,attr=struct.unpack('<2I',get(pa,8,f'l1-{addr:x}'))
        row=dict(address=hex(addr),group=group,l1_base=hex(base<<4),l1_attr=hex(attr))
        if not attr&1:
            row['permission']=(attr>>1)&3
        else:
            kind=(attr>>4)&3
            if kind<3:
                index=(addr&0x3fffffff)>>(12,16,21)[kind]
                l2=(base<<4)+(index//16)*4
                if 0x80000000<=l2<=0xfffffffc:
                    word,=struct.unpack('<I',get(l2,4,f'l2-{addr:x}'))
                    row.update(l2_word=hex(word),index=index,permission=(word>>(index%16*2))&3)
        detail.append(row)
    return dict(vid=2,irq_status=status,latched_fault_address=hex(fault),latched_fault_info=hex(info),
                control=list(controls),entries=detail,scope='read-only final snapshot; fault registers can have been cleared by guest')

def check_transport(output, executable, oem_init=False, byte_granularity=False,lk_replay=False,shareability=False,phy_isolation=False,prelink=False,host0=False,power=False,plain128=False,mailbox=False):
    if plain128 and not (byte_granularity and host0 and power):
        raise ValueError('PRDT128 check requires byte-gran, --host0 and --power')
    if power and not prelink:raise ValueError('Power register check requires --prelink')
    import ufs_mailbox_platform as mb
    if mailbox and not host0:
        raise ValueError('Mailbox check requires --host0')
    route = route_contract()
    disk = output / 'ufs-fixture.raw'
    with disk.open('xb') as stream:
        stream.truncate(64 * 1024 * 1024)
    before_hash = vm.sha(disk)
    qmp_port, test_port = vm.free_port(), vm.free_port()
    command = [str(executable), '-machine', 'virt,virtualization=on,gic-version=2,x-exynos-vlm=on,x-exynos-ufs=on',
        '-cpu', 'cortex-a57', '-accel', 'qtest', '-smp', '8', '-m', '128', '-display', 'none',
        '-serial', 'null', '-serial', 'null', '-monitor', 'none', '-nic', 'none', '-S',
        '-drive', f'if=none,id=ufs-fixture,file={disk.as_posix()},format=raw,cache=directsync',
        '-device', 'ufs-lu,drive=ufs-fixture,bus=auto9-ufs0-luns,lun=0',
        '-qmp', f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
        '-qtest', f'tcp:127.0.0.1:{test_port},server=on,wait=off', '-qtest-log', str(output/'qtest.log')]
    if host0:
        vm.save(output/'host0-policy.json',host0_policy(HOST0_PROFILE,True))
        command[2]+=',x-exynos-ufs-host0=1'
    if mailbox:
        command[2]+=',x-exynos-ufs-mailbox=1'
        vm.save(output/'mailbox-policy.json',mb.policy(mb.PROFILE,host0_policy(HOST0_PROFILE,True),True))
    vgen_words=(2,3,4,0,0,0,0,0) if host0 else (2,)*8
    if prelink:
        vm.save(output/'prelink-policy.json',prelink_policy(PRELINK_PROFILE,True))
        command[2]+=',x-exynos-ufs-prelink=1'
    if shareability:
        vm.save(output/'shareability-policy.json',shareability_policy(SHAREABILITY_PROFILE,True))
        command[2]+=',x-exynos-ufs-shareability=on'
    if lk_replay:
        if not byte_granularity:raise ValueError('Original LK requires byte-unit descriptors')
        command[2]=command[2].replace('virt,','virt-8704-apm,')+',x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1'
    if phy_isolation:
        vm.save(output/'phy-isolation-policy.json',phy_isolation_policy(PHY_ISOLATION_PROFILE,True))
        command[2]=command[2].replace('virt,','virt-8704-apm,')+',x-exynos-acpm=on,x-exynos-acpm-hz=96000000,x-exynos-pmu-profile=1,x-exynos-ufs-phy-isolation=on'
    if byte_granularity:command+=['-global','ufs.x-samsung-byte-granularity=on']
    vm.save(output/'launch.json', {'command': command, 'qemu_sha256': vm.sha(executable), 'route': route,
                                  'samsung_byte_granularity':byte_granularity})
    env = os.environ.copy()
    env['PATH'] = str(vm.ROOT/'.local/deps/bin') + os.pathsep + env.get('PATH', '')
    qmp = qt = None
    checks, transfers = [], []
    error = None
    def check(name, actual, expected):
        checks.append({'name': name, 'actual': actual, 'expected': expected, 'passed': actual == expected})
        if actual != expected:
            raise ValueError('UFS check failed: ' + name)
    with (output/'qemu.log').open('wb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('QEMU exited before QMP; inspect qemu.log')
                try:
                    qmp = vm.QMP(qmp_port)
                    break
                except OSError:
                    time.sleep(.1)
            if qmp is None:
                raise TimeoutError('UFS QMP startup deadline')
            qt = QTest(test_port)
            path = '/machine/exynos-ufs0'
            if mailbox:
                a,b=HCI+0x10000,HCI+0x20000
                qt.write(b+0xdc,2)
                qt.write(b+0xdc,5)
                check('No PH producer and timeout TX5 never complete a mailbox',[
                    qt.read(b+0xd8),mb.runtime(qmp)['vh2']['ack']],[0,0])
                qt.write(HCI+0xd4,0x60200)
                qt.write(HCI+0xd8,0x201)
                check('Addressed PH write targets only VH1', [qt.read(a+0xd8),qt.read(b+0xd8)],[1,0])
                qt.write(HCI+0xd8,0x601)
                check('PH broadcast sets independent VH pending latches',[qt.read(a+0xd0),qt.read(b+0xd0)],[0x10000,0x10000])
                qt.write(b+0xdc,2)
                check('Original VH reads actual PH completion after its READY',qt.read(b+0xd8),1)
                qt.write(b+0xd0,0x10000)
                qt.write(b+0xdc,5)
                state=mb.runtime(qmp)
                check('Only completed VH2 receives ACK credit and consumes its latch',
                      [state['vh1']['ack'],state['vh2']['ack'],qt.read(a+0xd8),qt.read(b+0xd8)],[0,1,1,0])
                qt.write(HCI+0xd8,0x801)
                qt.read(b)
                state=mb.runtime(qmp)
                check('Unknown destinations and VH data plane are rejected',
                      [state['ph']['rejected'],state['vh2']['rejected']],[1,1])
                qt.write(HCI+0xdc,0)
                qt.write(HCI+0xd0,0x60000)
                check('Mailbox fixture drains PH messages before HCI-only checks',
                      [qt.read(HCI+0xdc),qt.read(HCI+0xd0)],[0,0])
                check('Mailbox fixture releases its shared IRQ before power tests',
                      qmp.execute('qom-get',{'path':'/machine/exynos-ufs0','property':'irq-level'}),0)
            if byte_granularity:
                check('Explicit actual byte-unit engine mode',qmp.execute('qom-get',
                      {'path':path+'/controller','property':'x-samsung-byte-granularity'}),True)
            def prop(name):
                return qmp.execute('qom-get', {'path': path, 'property': name})
            gic = next(row['name'] for row in qmp.execute('qom-list', {'path': '/machine/unattached'})
                       if row['type'] == 'child<arm_gic>')
            qt.call('irq_intercept_in /machine/unattached/' + gic)
            tree = qmp.execute('human-monitor-command',{'command-line':'info mtree'})
            check('Shareability word mapping is explicit', 'auto9-ufs0-shareability' in tree, shareability)
            if shareability:
                check('Cold UFS0 shareability read', qt.read(SHAREABILITY), 0)
                qt.write(SHAREABILITY, 6)                                        
                check('Native QNX mask readback', qt.read(SHAREABILITY), 6)
                qt.write(SHAREABILITY, 0xfffffffd)
                check('Only declared read/write bits writable', qt.read(SHAREABILITY), 4)
                qt.call(f'writeb {SHAREABILITY:#x} 0')
                qt.write(SHAREABILITY-4, 6)
                qt.write(SHAREABILITY+4, 6)
                check('Partial and neighboring writes rejected', prop('shareability'), 4)
                check('Only two accepted writes', prop('shareability-writes'), 2)
                check('Three accepted reads', prop('shareability-reads'), 3)
                check('Register sets no IRQ or DMA', [prop(k) for k in ('irq-level','dma-reads','dma-writes','dma-rejected')], [0,0,0,0])
                check('Register does not enable HCI', qt.read(HCI+0x34), 0)
                qmp.execute('system_reset')
                check('Shareability returns to declared cold state on system reset', qt.read(SHAREABILITY), 0)
            if phy_isolation:
                def pmu(name):
                    return qmp.execute('qom-get',{'path':'/machine/exynos-pmu','property':name})
                check('Cold PMA isolation control',qt.read(PHY_ISOLATION),0)
                qt.write(PHY_ISOLATION,1)                          
                check('PMA isolation release config is latched',qt.read(PHY_ISOLATION),1)
                qt.write(PHY_ISOLATION,2)
                qt.write(PHY_ISOLATION+4,1)
                qt.write(PHY_ISOLATION+0xc000,0)
                check('Reserved bits, UFS1 word and unverified alias do not write',pmu('ufs0-phy-writes'),1)
                check('PMA isolation config survives rejected writes',pmu('ufs0-phy-isolation'),1)
                check('Two PMA config reads observed',pmu('ufs0-phy-reads'),2)
                check('PMA config does not enable HCI',qt.read(HCI+0x34),0)
                check('PMA config sets no IRQ or DMA',[prop(k) for k in ('irq-level','dma-reads','dma-writes','dma-rejected')],[0,0,0,0])
                qt.write(PHY_ISOLATION,0)
                check('PMA isolation can be requested again',qt.read(PHY_ISOLATION),0)
                qt.write(PHY_ISOLATION,1)
                qmp.execute('system_reset')
                check('Machine reset clears the virtual isolation bit',qt.read(PHY_ISOLATION),0)
            check('Private transport disabled by real reset', prop('transport-enabled'), False)
            qmp.execute('qom-set', {'path': path, 'property': 'transport-enabled', 'value': True})
            qmp.execute('cont')                                                                    
            if oem_init:
                rejected=prop('vendor-rejected')
                check('UFS0 vendor timeout-divider readback does not change HCI state',
                      [qt.read(UFS0_TIMEOUT_DIVIDER),prop('vendor-rejected'),prop('irq-level')],
                      [0,rejected,0])
                qt.write(UFS0_TIMEOUT_DIVIDER,1)
                check('UFS0 timeout divider remains unprogrammed by unsupported writes',
                      [qt.read(UFS0_TIMEOUT_DIVIDER),prop('vendor-rejected')],[0,rejected+1])
                rejected+=1
                                                                                    
                qt.write(HCI+0x1100+0x2fc,0xdeadbeef)
                qt.write(HCI+0x1100+0x50,1)
                check('Unknown vendor and partial reset reject',prop('vendor-rejected'),rejected+2)
                native=replay_original_power(qt,0);vm.save(output/'oem-power-pre.json',native)
                check('Original pre callback returns',native['returned'],True)
                check('Original pre callback status',native['return_value'],0)
                check('Native real software reset executed',prop('software-resets'),1)
                check('Native GPIO asserts reset',prop('device-reset-assertions'),1)
                check('Native GPIO releases reset',prop('device-reset-releases'),1)
                check('Native delay yields actual 5ms GPIO pulse',prop('reset-pulse-ns'),5000000)
                check('Unknown writes did not become configuration',prop('vendor-rejected'),rejected+2)
                check('Native reset did not enable HCE',qt.read(HCI+0x34),0)
                qt.write(HCI+0x1100+0x70,0)
                qt.write(HCI+0x34,1)
                check('HCE cannot enable while reset asserted',qt.read(HCI+0x34),0)
                qt.call('clock_step 7000');qt.write(HCI+0x1100+0x70,1)
                check('Explicit second GPIO pulse duration',prop('reset-pulse-ns'),7000)
            check('Native HCI has 32 request slots', qt.read(HCI) & 31, 31)
            qt.write(HCI+0x34, 1)
            check('HCE enabled by host driver write', qt.read(HCI+0x34), 1)
            if oem_init:
                native=replay_original_power(qt,1);vm.save(output/'oem-power-post.json',native)
                check('Original post callback returns',native['returned'],True)
                check('Original post callback status',native['return_value'],0)
                for offset,value in ((0x00,12),(0x04,12),(0x0c,166),(0x40,0xffffffff),
                      (0x44,0xffffffff),(0x60,10),(0x6c,0x98000003),(0x308,1),(0x30c,67101182)):
                    check(f'Original vendor config {offset:#x}',qt.read(HCI+0x1100+offset),value)
            if prelink:
                qt.write(PRELINK_UNIPRO+0x44,96)
                check('Pinned UniPro period retained',qt.read(PRELINK_UNIPRO+0x44),96)
                qt.write(HCI+0x1100+0xb0,8)
                qt.write(PRELINK_PMA+0x8c,0x80)
                qt.write(HCI+0x1100+0xb0,0)
                for offset,value in PRELINK_PMA_WORDS:
                    qt.write(PRELINK_PMA+offset,value)
                qt.write(PRELINK_PMA+0x150,1)
                check('Original PMA config without PLL/link completion',qt.read(HCI+0x30)&7,0)
                check('Only unlocked PMA table writes accepted',prelink_runtime(qmp),
                      dict(zip(PRELINK_PROPERTIES,(96,1,len(PRELINK_PMA_WORDS),2,0x10c))))
            if power:
                vm.save(output/'power-policy.json',power_policy(POWER_PROFILE,True))
                qt.write(PRELINK_UNIPRO+POWER_TIMERS[0][0], POWER_TIMERS[0][1])
                check('Timer extension disabled by default',power_runtime(qmp),dict.fromkeys(POWER_PROPERTIES,0))
                power_configure(qmp)
                before=prop('prelink-rejected')
                qt.write(PRELINK_PMA+0xc8,0x40)
                qt.write(PRELINK_UNIPRO+POWER_TIMERS[0][0],0xdead)
                qt.write(PRELINK_UNIPRO+0x7884,0x1f80)
                check('Reject early PMA, wrong timer value and neighboring timer',prop('prelink-rejected')-before,3)
                for offset,value in POWER_TIMERS:
                    qt.write(PRELINK_UNIPRO+offset,value)
                    check(f'Exact timer {offset:#x} readback',qt.read(PRELINK_UNIPRO+offset),value)
                qt.write(HCI+0x1100+0xb0,8)
                qt.write(PRELINK_PMA+0xc8,0x40)
                qt.write(HCI+0x1100+0xb0,0)
                qt.write(PRELINK_PMA+0xc8,0x40)
                check('Original PMA mode word readback',qt.read(PRELINK_PMA+0xc8),0x40)
                check('Clock-stop guard still applies',prop('prelink-rejected')-before,4)
                check('Six real timer writes and one valid PMA write',power_runtime(qmp),
                      dict(zip(POWER_PROPERTIES,(0x3f,6,1))))
                check('Config writes do not manufacture link or IRQ',qt.read(HCI+0x30)&7,0)
                check('Config writes do not assert host IRQ',prop('irq-level'),0)
            qt.write(HCI+0x24, 0x400)
            for offset in (0x94, 0x98, 0x9c):
                qt.write(HCI+offset, 0)
            qt.write(HCI+0x90, 0x16)
            check('DME_LINK_STARTUP actual UCCS', qt.read(HCI+0x20) & 0x400, 0x400)
            check('UFS IRQ reaches original SPI227', 'IRQ raise 227' in qt.events, True)
            qt.write(HCI+0x20, 0x400)
            check('UCCS W1C lowers IRQ', prop('irq-level'), 0)
            check('Link and transfer list ready', qt.read(HCI+0x30) & 11, 11)
            for slot, vid in enumerate(vgen_words):
                qt.write(VGEN+slot*4, vid)
            qt.write(S2MPU, 3)
            qt.write(S2MPU+0x20, 4)
                                                                          
            qt.put(TABLE, b'\xff' * 128)
            qt.write(S2MPU+0x4408, TABLE >> 4)
            qt.write(S2MPU+0x440c, 1)
            qt.write(HCI+0x24, 0xffffffff)
            qt.write(HCI+0x50, UTRL)
            qt.write(HCI+0x54, 0)
            qt.write(HCI+0x60, 1)

            def send(name, request, direction=0, data=None, deny=None, raw_prdt_length=None, sg_records=None):
                qt.put(COMMAND, bytes(4096))
                qt.put(COMMAND, request)
                qt.put(DATA, data if data is not None else bytes(BLOCK_SIZE))
                if direction:
                    qt.put(COMMAND+2048, sg_records if sg_records is not None else struct.pack('<QII', DATA, 0, BLOCK_SIZE-1))
                descriptor=bytearray(utrd(direction=direction, prds=int(bool(direction)),byte_granularity=byte_granularity))
                if raw_prdt_length is not None:struct.pack_into('<H',descriptor,28,raw_prdt_length)
                qt.put(UTRL, bytes(descriptor))
                if deny:
                    rejected_before = prop('dma-rejected')
                    events_before = len(qt.events)
                    qt.write(S2MPU+0x2c, 4)
                    qt.write(DATA_PERMISSION_WORD, 0xfffffffc | (2 if deny == 'read' else 1))
                    qt.write(S2MPU+0x1000, 2 << 16)
                qt.write(HCI+0x58, 1)
                if deny:
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and prop('dma-rejected') == rejected_before:
                        qt.call('clock_step 10000')
                        time.sleep(.001)
                    check(name+' produces actual DMA rejection', prop('dma-rejected') > rejected_before, True)
                    check(name+' actual VID2 fault', qt.read(S2MPU+0x2000) & 4, 4)
                    check(name+' fault IRQ reaches original SPI384', 'IRQ raise 384' in qt.events[events_before:], True)
                    check(name+' fault address', qt.read(S2MPU+0x2044), DATA)
                    check(name+' fault direction', (qt.read(S2MPU+0x2050) >> 20) & 1, int(deny == 'write'))
                    check(name+' has no fabricated completion', qt.read(HCI+0x20), 0)
                    check(name+' request remains pending', qt.read(HCI+0x58), 1)
                    if deny == 'write':
                        check(name+' destination unchanged while denied', qt.get(DATA, BLOCK_SIZE) == bytes(BLOCK_SIZE), True)
                    else:
                        with disk.open('rb') as stream:
                            stream.seek(7*BLOCK_SIZE)
                            check(name+' disk unchanged while denied', stream.read(BLOCK_SIZE) == pattern, True)
                    qt.write(S2MPU+0x2000, 4)
                    check(name+' status write cannot acknowledge pending VID2', qt.read(S2MPU+0x2000) & 4, 4)
                    qt.write(S2MPU+0x2c, 2)
                    check(name+' clearing unrelated VID1 leaves VID2 and IRQ set',
                          (qt.read(S2MPU+0x2000) & 4, 'IRQ lower 384' in qt.events[events_before:]),
                          (4, False))
                                                                                          
                    qt.write(DATA_PERMISSION_WORD, 0xffffffff)
                    qt.write(S2MPU+0x2c, 4)
                    check(name+' W1C clears only the selected status bit', qt.read(S2MPU+0x2000) & 4, 0)
                    check(name+' acknowledging fault lowers SPI384', 'IRQ lower 384' in qt.events[events_before:], True)
                    qt.write(S2MPU+0x1000, 2 << 16)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not qt.read(HCI+0x20):
                    qt.call('clock_step 10000')
                    time.sleep(.001)
                status = qt.read(HCI+0x20)
                response = qt.get(COMMAND+1024, 288)
                ocs = qt.read(UTRL+8) & 15
                transfers.append({'name': name, 'is': status, 'ocs': ocs, 'response_hex': response.hex(),
                                  'doorbell': qt.read(HCI+0x58), 'dma_rejected': prop('dma-rejected')})
                check(name+' completed with UTRCS', status & 1, 1)
                check(name+' clears doorbell', qt.read(HCI+0x58), 0)
                check(name+' actual asserted IRQ', prop('irq-level'), 1)
                qt.write(HCI+0x20, status)
                check(name+' W1C lowers IRQ', prop('irq-level'), 0)
                return ocs, response

            ocs, response = send('NOP', bytes(288))
            check('NOP OCS', ocs, 0)
            check('NOP response transaction', response[0], 0x20)
                                                                                    
            request = bytearray(288)
            request[0], request[5], request[12], request[13] = 0x16, 0x81, 6, 1
            ocs, response = send('Set fDeviceInit', bytes(request))
            check('Set fDeviceInit OCS', ocs, 0)
            check('Set fDeviceInit response', response[6], 0)
            if lk_replay:
                import lk_runtime
                native=lk_runtime.replay(qt);vm.save(output/'original-lk-handoff.json',native)
                check('Original LK whole handoff returns',native['returned'],True)
                check('Original LK status',native['return_value'],0)
                check('Only original instruction writes actual PMU handoff',native['pmu_handoff']&0xff000000,0xa5000000)
                                                                                               
            ocs, response = send('READ10 initial', scsi_request(0x28, 7), 0x04000000)
            check('Initial READ10 OCS', ocs, 0)
            if response[7] == 2:
                sense = bytearray(288)
                sense[0:2], sense[16], sense[20] = b'\x01\x40', 3, 18
                struct.pack_into('>I', sense, 12, 18)
                                                                                                 
                ocs, response = send('REQUEST SENSE', bytes(sense), 0x04000000)
                check('REQUEST SENSE OCS', ocs, 0)
            pattern = bytes((i*29+17) & 255 for i in range(BLOCK_SIZE))
            ocs, response = send('WRITE10', scsi_request(0x2a, 7), 0x02000000, pattern)
            check('WRITE10 OCS', ocs, 0)
            check('WRITE10 SCSI good', response[7], 0)
            ocs, response = send('READ10', scsi_request(0x28, 7), 0x04000000)
            check('READ10 OCS', ocs, 0)
            check('READ10 SCSI good', response[7], 0)
            check('Readback actual DMA payload', hashlib.sha256(qt.get(DATA, BLOCK_SIZE)).hexdigest(),
                  hashlib.sha256(pattern).hexdigest())
            second_pattern = bytes(value ^ 255 for value in pattern)
            ocs, response = send('Denied WRITE10 data read', scsi_request(0x2a, 7), 0x02000000,
                                 second_pattern, deny='read')
            check('Repaired WRITE10 OCS', ocs, 0)
            check('Repaired WRITE10 SCSI good', response[7], 0)
            pattern = second_pattern
            ocs, response = send('Denied READ10 data write', scsi_request(0x28, 7), 0x04000000, deny='write')
            check('Repaired READ10 OCS', ocs, 0)
            check('Repaired READ10 SCSI good', response[7], 0)
            check('Repaired READ10 DMA payload', qt.get(DATA, BLOCK_SIZE) == pattern, True)
            if byte_granularity:
                ocs,response=send('Partial PRDT byte length rejected',scsi_request(0x28,7),0x04000000,raw_prdt_length=15)
                check('Partial PRDT cannot become successful truncated table',ocs!=0,True)
                check('Rejected byte table never writes DMA payload',qt.get(DATA,BLOCK_SIZE)==bytes(BLOCK_SIZE),True)
                check('Guest byte-unit descriptor retained after completion',struct.unpack('<4H',qt.get(UTRL+24,8)),(1152,1024,15,2048))
            if plain128:
                check('Legacy PRDT128 disabled by default',prop(PLAIN128_PROPERTY),False)
                configure_plain128(qmp)
                check('Legacy PRDT128 enabled on idle controller',prop(PLAIN128_PROPERTY),True)
                entry=lambda address,flags=0:struct.pack('<QII',address,0,(BLOCK_SIZE-1)|flags)+bytes(112)
                before=prop('dma-rejected')
                qt.put(DATA+BLOCK_SIZE,bytes([0xcc])*BLOCK_SIZE)
                ocs,response=send('PRDT128 two-segment READ10',scsi_request(0x28,7,2),0x04000000,
                                  raw_prdt_length=256,sg_records=entry(DATA)+entry(DATA+BLOCK_SIZE))
                check('PRDT128 original-sized scatter reads actual block backend', (ocs,response[7]),(0,0))
                check('PRDT128 first guest payload matches actual media',qt.get(DATA,BLOCK_SIZE)==pattern,True)
                check('PRDT128 second guest payload written at second header address',
                      qt.get(DATA+BLOCK_SIZE,BLOCK_SIZE)==bytes(BLOCK_SIZE),True)
                for name,length,record in (
                    ('crypto flags',128,entry(DATA,1<<28)),
                    ('opaque extension',128,entry(DATA)[:-1]+bytes([1])),
                    ('partial stride',127,entry(DATA)),
                ):
                    ocs,_=send(f'PRDT128 rejects {name}',scsi_request(0x28,7),0x04000000,
                               raw_prdt_length=length,sg_records=record)
                    check(f'PRDT128 {name} is structural, not successful',ocs!=0,True)
                    check(f'PRDT128 {name} leaves destination unchanged',qt.get(DATA,BLOCK_SIZE)==bytes(BLOCK_SIZE),True)
                check('PRDT128 padding never attempts DMA to address 0',prop('dma-rejected'),before)
                configure_plain128(qmp,False)
            rejected_before = prop('dma-rejected')
            qt.write(VGEN if host0 else VGEN+4, 3)
            qt.put(UTRL, utrd(byte_granularity=byte_granularity))
            qt.put(COMMAND, bytes(4096))
            qt.write(HCI+0x58, 1)
            deadline = time.monotonic()+1
            while prop('dma-rejected') == rejected_before and time.monotonic() < deadline:
                qt.call('clock_step 10000')
                time.sleep(.001)
            check('Unowned physical host VID rejects DMA' if host0 else 'Mixed VGEN contexts reject descriptor DMA',
                  prop('dma-rejected') > rejected_before, True)
            if host0:
                state=host0_runtime(qmp)
                check('Only physical HCI entry 0 consumed with VID3 rejected',state['host0-last-vid'],3)
                check('Wrong physical VID cannot fall back to a virtual host',state['host0-invalid']>0,True)
                check('S2MPU still enforces actual denied transfers',state['host0-s2-denied']>0,True)
            check('Rejected descriptor is not completed', qt.read(UTRL+8) & 15, 15)
            check('Mixed contexts do not invent completion IRQ', qt.read(HCI+0x20), 0)
            qmp.execute('system_reset')
            check('Actual machine reset disables private transport', prop('transport-enabled'), False)
            check('Actual machine reset clears HCE', qt.read(HCI+0x34), 0)
            check('Actual machine reset clears VGEN', qt.read(VGEN), 0)
            check('Actual machine reset clears pending doorbell', qt.read(HCI+0x58), 0)
                                                                            
                                                                        
            qmp.execute('qom-set', {'path': path, 'property': 'transport-enabled', 'value': True})
            qt.write(HCI+0x34, 1)
            qt.write(HCI+0x90, 0x16)
            qt.write(HCI+0x20, 0xffffffff)
            qt.write(HCI+0x24, 0xffffffff)
            for slot, vid in enumerate(vgen_words):
                qt.write(VGEN+4*slot, vid)
            qt.put(TABLE, b'\xff'*128)
            qt.write(S2MPU, 3)
            qt.write(S2MPU+0x4408, TABLE >> 4)
            qt.write(S2MPU+0x440c, 1)
            qt.write(HCI+0x50, UTRL)
            qt.write(HCI+0x54, 0)
            qt.write(HCI+0x60, 1)
            ocs, response = send('NOP after reset', bytes(288))
            check('Controller handles a new request after reset', (ocs, response[0]), (0, 0x20))
                                                                           
                                                                             
                                             
            ocs, response = send('READ10 after reset', scsi_request(0x28, 7), 0x04000000)
            if response[7] == 2:
                ocs, response = send('READ10 after reset UA cleared', scsi_request(0x28, 7), 0x04000000)
            check('READ10 after reset good', (ocs, response[7]), (0, 0))
            qt.put(COMMAND, bytes(4096))
            qt.put(COMMAND, scsi_request(0x2a, 8))
            qt.put(COMMAND+2048, struct.pack('<QII', DATA, 0, BLOCK_SIZE-1))
            qt.put(DATA, second_pattern)
            qt.put(UTRL, utrd(direction=0x02000000, prds=1,byte_granularity=byte_granularity))
            qt.write(DATA_PERMISSION_WORD, 0xfffffffe)
            qt.write(S2MPU+0x1000, 2 << 16)
            before_cancel = prop('dma-rejected')
            qt.write(HCI+0x58, 1)
            deadline = time.monotonic()+2
            while prop('dma-rejected') == before_cancel and time.monotonic() < deadline:
                qt.call('clock_step 10000')
                time.sleep(.001)
            check('Cancellation test has a real pending DMA fault', prop('dma-rejected') > before_cancel, True)
            check('Faulted WRITE10 is not completed before reset', qt.read(HCI+0x20), 0)
            if oem_init:
                resets=prop('software-resets')
                qt.write(HCI+0x1100+0x50,3)
                deadline=time.monotonic()+2
                while prop('software-resets')==resets and time.monotonic()<deadline:
                    qt.call('clock_step 10000')
                check('Vendor software reset performed backend cancellation',prop('software-resets'),resets+1)
                check('Software reset clears real pending DMA doorbell',qt.read(HCI+0x58),0)
                check('Software reset leaves aborted descriptor uncompleted',qt.read(UTRL+8)&15,15)
                check('Software reset clears HCE',qt.read(HCI+0x34),0)
                check('Software reset preserves actual private transport',prop('transport-enabled'),True)
                qt.write(DATA_PERMISSION_WORD,0xffffffff)
                qt.write(S2MPU+0x2c,4)
                qt.write(S2MPU+0x1000,2<<16)
                qt.call('clock_step 100000')
                check('Permission repair cannot resurrect aborted DMA',qt.read(HCI+0x58),0)
                with disk.open('rb') as stream:
                    stream.seek(8*BLOCK_SIZE)
                    check('Software-reset-aborted write never reaches disk',stream.read(BLOCK_SIZE)==bytes(BLOCK_SIZE),True)
                                                                              
                                                                                  
                qt.write(HCI+0x34,1);qt.write(HCI+0x90,0x16)
                qt.write(HCI+0x20,0xffffffff);qt.write(HCI+0x24,0xffffffff)
                qt.write(HCI+0x50,UTRL);qt.write(HCI+0x54,0);qt.write(HCI+0x60,1)
                ocs,response=send('READ10 after vendor reset',scsi_request(0x28,7),0x04000000)
                if response[7]==2:
                    ocs,response=send('READ10 after vendor reset UA cleared',scsi_request(0x28,7),0x04000000)
                check('Real read works after vendor reset',(ocs,response[7]),(0,0))
                qt.put(COMMAND,bytes(4096));qt.put(COMMAND,scsi_request(0x2a,8))
                qt.put(COMMAND+2048,struct.pack('<QII',DATA,0,BLOCK_SIZE-1))
                qt.put(DATA,second_pattern);qt.put(UTRL,utrd(direction=0x02000000,prds=1,byte_granularity=byte_granularity))
                qt.write(DATA_PERMISSION_WORD,0xfffffffe);qt.write(S2MPU+0x1000,2<<16)
                rejected=prop('dma-rejected');qt.write(HCI+0x58,1)
                deadline=time.monotonic()+2
                while prop('dma-rejected')==rejected and time.monotonic()<deadline:
                    qt.call('clock_step 10000');time.sleep(.001)
                check('Machine-reset regression has a new real pending DMA',prop('dma-rejected')>rejected,True)
                check('New pending DMA has no completion',qt.read(HCI+0x20),0)
            qmp.execute('system_reset')
            check('Reset cancels real backend wait', qt.read(HCI+0x58), 0)
            check('Reset does not complete aborted descriptor', qt.read(UTRL+8) & 15, 15)
            vm.save(output/'controller-state.json', {name: prop(name) for name in
                    ('transport-enabled', 'irq-level', 'dma-reads', 'dma-writes', 'dma-rejected')})
        except Exception as exc:
            error = repr(exc)
        finally:
            if qmp:
                try:
                    qmp.execute('quit')
                except (OSError, RuntimeError):
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if qt:
                vm.save(output/'transcript.json', qt.transcript)
                vm.save(output/'irq-events.json', qt.events)
                qt.close()
            if qmp:
                qmp.close()
    if error is None:
        try:
            with disk.open('rb') as stream:
                stream.seek(7*BLOCK_SIZE)
                check('Actual backing file contains written block', stream.read(BLOCK_SIZE) == pattern, True)
                check('Aborted WRITE10 never touched next disk block', stream.read(BLOCK_SIZE) == bytes(BLOCK_SIZE), True)
        except Exception as exc:
            error = repr(exc)
    result = {'passed': error is None and all(row['passed'] for row in checks), 'error': error,
              'checks': checks, 'transfers': transfers, 'qemu_exit_code': process.returncode,
              'disk_before': before_hash, 'disk_after': vm.sha(disk), 'route': route,
              'original_system_boot_verified': False, 'vendor_phy_fmp_implemented': False,
              'vendor_reset_config_implemented':True,
              'oem_callback_replayed':oem_init,'samsung_byte_granularity':byte_granularity}
    vm.save(output/'result.json', result)
    if not result['passed']:
        raise RuntimeError('Native UFS check failed; inspect '+str(output/'result.json'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('check', 'route', 'oem-init', 'byte-gran','lk-replay','shareability','phy-isolation'))
    parser.add_argument('--prelink',action='store_true')
    parser.add_argument('--mailbox',action='store_true')
    parser.add_argument('--host0',action='store_true')
    parser.add_argument('--power',action='store_true')
    parser.add_argument('--plain128',action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--qemu', type=Path, default=vm.ROOT/'runtime/qemu-system-aarch64.exe')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():
        raise ValueError('Require new output directory inside project scratch')
    output.mkdir(parents=True)
    result = (check_transport(output,args.qemu.resolve(),oem_init=args.operation in ('oem-init','byte-gran','lk-replay'),byte_granularity=args.operation in ('byte-gran','lk-replay'),lk_replay=args.operation=='lk-replay',shareability=args.operation in ('shareability','phy-isolation'),phy_isolation=args.operation=='phy-isolation',prelink=args.prelink,host0=args.host0,power=args.power,plain128=args.plain128,mailbox=args.mailbox)
              if args.operation in ('check','oem-init','byte-gran','lk-replay','shareability','phy-isolation') else route_contract())
    vm.save(output/'result.json', result)
    print(str(output/'result.json'))


if __name__ == '__main__':
    main()
