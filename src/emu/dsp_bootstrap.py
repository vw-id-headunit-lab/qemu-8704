
import hashlib
import json
from pathlib import Path
import struct
import time

import original_8704_vm as vm
import ufs_dsp_media as media
import ufs_platform as ufs

PROFILE = 'virtual-lk-dsp-ufs-read-v1'
WINDOWS = ((0xd0000000,0x2000000),(0xd4000000,0x2000000),
           (0xd8000000,0x2000000),(0xdc000000,0x2000000))
MAX_PRD = 0x40000
PERM_TABLE = 0x40120000
PERM_BYTES = 0x10000


def policy(profile, original_lk, ufs_host0, verified_disk, slot):
    if profile is None:
        return None
    if (profile != PROFILE or not original_lk or not ufs_host0 or not verified_disk
            or verified_disk['profile'] != media.PROFILE or slot not in ('a','b')):
        raise ValueError('DSP preboot READ requires original LK, host0 and verified DSP LUN0 with explicit slot')
    from vlm_platform import original,parse_dtb
    hcb = original('hcb/hcb.img')
    raw = hcb.read_bytes()
    for vmid in (2,3,4):
        start = raw.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        size = struct.unpack_from('>I',raw,start+4)[0]
        node = parse_dtb(raw[start:start+size])['/vplatform/vl,audio/vl,hifi_core@mem']
        if (tuple(struct.iter_unpack('>QI',node['reg'])) != WINDOWS
                or 'vl,disabled' in node or 'vl,allow-sharing' not in node):
            raise ValueError(f'Original VM{vmid} HiFi4 shared grants changed')
    manifest = json.loads(Path(verified_disk['manifest']).read_bytes())
    if manifest != media.expected_plan():
        raise ValueError('Verified DSP manifest changed before preboot')
    parts = []
    for core,(address,capacity) in enumerate(WINDOWS[:3]):
        p = next(r for r in manifest['partitions'] if r['label']==f'{slot}_audio{core}fw')
        payload = p['payload']
        size = payload['bytes']
        if size > capacity or size % ufs.BLOCK_SIZE or p['first']+size//ufs.BLOCK_SIZE-1 > p['last']:
            raise ValueError('Original DSP payload not an aligned bounded load')
        parts.append(dict(core=core,label=p['label'],lba=p['first'],address=address,
                          blocks=size//ufs.BLOCK_SIZE,**payload))
    return dict(profile=profile,slot=slot,parts=parts,hcb_sha256=vm.sha(hcb),
                hcb_windows=WINDOWS,core3_present=False,original_loader_executed=False,
                model='Explicit local ROM-stage UFS data load; no DSP power, reset or completion supplied')


def descriptors(address, size):
    if not 0<size<=MAX_PRD*32 or address%ufs.BLOCK_SIZE or size%ufs.BLOCK_SIZE:
        raise ValueError('Unaligned or oversize preboot DSP UFS data')
    return b''.join(struct.pack('<4I',(address+off)&0xffffffff,(address+off)>>32,0,min(MAX_PRD,size-off)-1)
                    for off in range(0,size,MAX_PRD))


def write_permissions(parts):

    data = bytearray(PERM_BYTES)
    for r in parts:
        start = r['address']
        size = r['bytes']
        if not 0xc0000000<=start<start+size<=0x100000000 or (start|size)&4095:
            raise ValueError('Destination left temporary VID2 region')
        for page in range((start-0xc0000000)//4096,(start+size-0xc0000000)//4096):
            data[page//4] |= 2 << ((page%4)*2)
    return bytes(data)


def sense_key(response):

    if len(response)<37 or struct.unpack_from('>H',response,32)[0]<3:
        return None
    encoding=response[34]&0x7f
    if encoding in (0x70,0x71):
        return response[36]&0xf
    if encoding in (0x72,0x73):
        return response[35]&0xf
    return None


def execute(io, qmp, output, plan, s2mpu, utrd, cmd, wall_budget):

    out = Path(output)
    out.mkdir()
    result = dict(plan=plan,attempts=[],passed=False,permission_restored=False,dsp_executed=False)
    descriptor = s2mpu+0x4400+3*8
    saved = (io.read(descriptor),io.read(descriptor+4))
    permission_bytes = write_permissions(plan['parts'])
    from lk_runtime import device_snapshot,counter_delta
    before = device_snapshot(qmp)
    vm.save(out/'result.json',result)
    def submit(request,prds):
        io.put(cmd,bytes(4096))
        io.put(cmd,request)
        io.put(cmd+2048,prds)
        io.put(utrd,ufs.utrd(command=cmd,direction=0x04000000,prds=len(prds)//16,byte_granularity=True))
        io.write(ufs.HCI+0x58,1)
        until = time.monotonic()+wall_budget
        while not io.read(ufs.HCI+0x20)&1 and time.monotonic()<until:
            time.sleep(.001)
        r = io.get(cmd+1024,52)
        outcome = dict(ocs=io.read(utrd+8)&15,doorbell=io.read(ufs.HCI+0x58),
                       response_transaction=r[0],response_result=r[6],scsi_status=r[7],
                       residual=struct.unpack_from('>I',r,12)[0],response_hex=r.hex())
        io.write(ufs.HCI+0x20,io.read(ufs.HCI+0x20))
        return outcome,r

    def good(outcome):
        return outcome['response_transaction']==0x21 and not any(
            outcome[k] for k in ('ocs','doorbell','response_result','scsi_status','residual'))

    try:
        io.put(PERM_TABLE,permission_bytes)
        io.write(descriptor,PERM_TABLE>>4)
        io.write(descriptor+4,1)
        io.write(s2mpu+0x1000,2<<16)
        for p in plan['parts']:
            row = dict(p)
            result['attempts'].append(row)
            prds = descriptors(p['address'],p['bytes'])
            request = ufs.scsi_request(0x28,p['lba'],p['blocks'])
            row.update(cdb=request[16:32].hex(),prdt_entries=len(prds)//16)
            outcome,r = submit(request,prds)
            if (p['core']==0 and not outcome['ocs'] and not outcome['doorbell']
                    and (r[0],r[7])==(0x21,2) and sense_key(r)==6):
                                                                            
                                                                                      
                row['initial_unit_attention'] = outcome
                sense=bytearray(288)
                                                                      
                                                                                     
                sense[:2],sense[16],sense[20]=b'\x01\x40',3,20
                struct.pack_into('>I',sense,12,20)
                sense_prd=struct.pack('<4I',ufs.DATA,0,0,19)
                cleared,sense_rsp = submit(sense,sense_prd)
                row['request_sense'] = cleared
                short_fixed=(sense_rsp[1]&0x60)==0x20 and cleared['residual']==2
                if not (good(cleared) or (short_fixed and good(dict(cleared,residual=0)))):
                    raise ValueError('Preboot DSP UFS REQUEST SENSE failed')
                outcome,r = submit(request,prds)
            row.update(outcome)
            vm.save(out/'result.json',result)
            if not good(outcome):
                raise ValueError(f'Preboot DSP UFS READ failed for core{p["core"]}')
            target = out/f'core{p["core"]}-ap-dma.bin'
            qmp.execute('pmemsave',dict(val=p['address'],size=p['bytes'],filename=str(target)))
            row['ap_memory_sha256']=vm.sha(target)
            if target.stat().st_size!=p['bytes'] or row['ap_memory_sha256']!=p['sha256']:
                raise ValueError(f'Original DSP core{p["core"]} DMA readback mismatch')
        result['dma_delta']=counter_delta(before,device_snapshot(qmp))
        if result['dma_delta']['dma-rejected'] or not result['dma_delta']['dma-writes']:
            raise ValueError('Preboot DSP DMA counters did not verify')
        result['passed']=True
    except Exception as exc:
        result['error']=str(exc)
        raise
    finally:
        try:
            io.write(descriptor,saved[0])
            io.write(descriptor+4,saved[1])
            io.write(s2mpu+0x1000,2<<16)
            result['permission_restored']=(io.read(descriptor),io.read(descriptor+4))==saved
            if not result['permission_restored']:
                raise ValueError('Preboot S2MPU restore failed')
        finally:
            if not result['permission_restored']:
                result['passed']=False
            vm.save(out/'result.json',result)
    return result

