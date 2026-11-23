
import argparse
import hashlib
import importlib.util
import json
import mmap
from pathlib import Path
import re
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
from cal_init_platform import QNX_RUNTIME_BASE, QNX_RUNTIME_BYTES, formatted_qnx_boot_log
import qnx_bts_platform as bts
from vlm_platform import QNX_FACTORY_HIGH_BANK, factory_file, original, parse_dtb


READ=re.compile(rb'ufs_native_read_result host=VIRTUAL-AUTO9-UFS0 slot=\d+ lun=0 '
                rb'opcode=0x[0-9a-f]+ lba=(\d+) blocks=(\d+) ocs=(\d+) status=(\d+)')
TOKENS={
    'slot_a':b'A boot(OM_value = 0)\n',
    'slot_b':b'B boot(OM_value = 1)\n',
    'shell_enomem':b'sh: cannot fork - try again, errno 12 (Not enough memory)\n',
    'child_failed':b'Child exited with status 256\n',
    'qtd_node_timeout':b'/dev/qtd-1 partition wait timeout\n',
    'qtd_mount_failed':b'mount QTD rootfs\nmount fail\n',
    'qtd_mount_success':b'QTD rootfs mount success\n',
    'qnx_startup_missing':b'Unable to start "/scripts/startup.sh" (2)',
}
TREX_ABORT=re.compile(rb'Auto9 VM2 external abort: pc=0x([0-9a-f]+) va=0x[0-9a-f]+ '
                      rb'pa=0x1b8a4000 .* access=1 size=4')


def root_file_lbas(first_lba):
    base=ROOT/'var/inputs/analysis/qnx-container-20261020'
    inventory=json.loads((base/'filesystem-inventory.json').read_bytes())
    spec=importlib.util.spec_from_file_location('native_root_qnx6',base/'inspect_qnx6.py')
    reader=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    names=('/bin/bts-exynos','/scripts/startup.sh')
    result={}
    with factory_file(bts.SOURCE).open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as mm:
        fs=reader.Qnx6(mm)
        if fs.bs!=4096 or fs.data_start%4096:
            raise ValueError('OEM QNX6 file-block/LBA units changed')
        for name in names:
            record,=[r for r in inventory['entries'] if r['path']==name and r['type']=='regular']
            inode=fs.inode(record['inode'])
            count=(inode['size']+fs.bs-1)//fs.bs
            if inode['levels'] or not 0<count<=16:
                raise ValueError('Unexpected boot-file pointer tree')
            payload=fs.read(inode,0,inode['size'])
            digest=hashlib.sha256(payload).hexdigest()
            if digest!=record['sha256'] or name=='/bin/bts-exynos' and digest!=bts.DRIVER_SHA:
                raise ValueError('OEM boot file identity changed')
            result[name]=dict(sha256=digest,lbas=[first_lba+fs.data_start//4096+block
                                                 for block in inode['ptr'][:count]],hits=set())
    return result


def run(session):
    path=session/'qnx-native-runtime/qnx-runtime-e0000000.bin'
    raw=path.read_bytes()
    if len(raw)!=QNX_RUNTIME_BYTES:
        raise ValueError('Not a complete OEM QNX snapshot')
    offset,log,terminated=formatted_qnx_boot_log(raw)
    if offset is None:
        raise ValueError('No OEM QNX debug-snapshot boot log')
                                                                          
                                                                         
    tokens={key:[hex(QNX_RUNTIME_BASE+m.start()) for m in re.finditer(re.escape(value),raw)]
            for key,value in TOKENS.items()}
    source=json.loads((session/'ufs-lun0-input.json').read_bytes())
    disk=Path(source['base']).resolve(strict=True)
    disk_manifest=json.loads(disk.with_suffix('.json').read_bytes())
    root={str(p['slot']):dict(label=p['label'],first=p['first'],last=p['last'],
                                 payload=p['payload'],reads=0,failed=0)
          for p in disk_manifest['partitions'] if p['slot'] in (32,33)}
    if {item['label'] for item in root.values()}!={'system_qnx_rbhv-qia','system_qnx_bak_rbhv-qia'}:
        raise ValueError('Wrong OEM root slots')
    files=root_file_lbas(root['32']['first'])
    trex_fault=None
    with (session/'tcg.log').open('rb') as events:
        for number,line in enumerate(events,1):
            if trex_fault is None and (fault:=TREX_ABORT.search(line)):
                pc=int(fault[1],16)
                if pc&0xfff==0x1b8:
                    trex_fault=dict(trace_line=number,pc=hex(pc),elf_relative_pc='0x31b8')
            if not (m:=READ.search(line)):
                continue
            lba,count,ocs,status=map(int,m.groups())
            if not count:
                continue
            for slot,part in root.items():
                if lba<=part['last'] and lba+count-1>=part['first']:
                    part['failed' if ocs or status else 'reads']+=1
                    if slot=='32' and not (ocs or status):
                        for item in files.values():
                            item['hits'].update(x for x in item['lbas'] if lba<=x<lba+count)
    for item in files.values():
        item['hits']=sorted(item['hits'])
    ifs=json.loads((ROOT/'var/inputs/analysis/qnx-ifs-20261021T125600Z/findings.json').read_bytes())
    ifs_payload=factory_file('qnx/qtd/ifs-exynosauto9_evt1-qla_qtd.bin.vw_cn')
    if bts.vm.sha(ifs_payload)!=ifs['sha256']:
        raise ValueError('OEM initial filesystem identity changed')
    bts_ifs=any(row['name'].strip('/')=='bin/bts-exynos' or row.get('sha256')==bts.DRIVER_SHA
                for row in ifs['entries'])
    dmc_path=session/'qnx-bts-runtime.json'
    dmc=json.loads(dmc_path.read_bytes()) if dmc_path.is_file() else {}
    dmc_init=sum(row==dict(limit=0x04040000,reads=1,writes=1,rejected=0) for row in dmc.values())
    bts_file=files['/bin/bts-exynos']
    root_exec=not bts_ifs and dmc_init==8 and bts_file['lbas']==bts_file['hits']
    meta=json.loads((session/'hcb-adaptation.json').read_bytes())
    hcb=(session/'hcb-virtual-board.img').read_bytes()
    if meta['original_hcb_sha256']!=hashlib.sha256(original('hcb/hcb.img').read_bytes()).hexdigest():
        raise ValueError('Unexpected original HCB')
    hwp=parse_dtb(hcb[:struct.unpack_from('>I',hcb,4)[0]])
    backed=QNX_FACTORY_HIGH_BANK in struct.iter_unpack('>QI',hwp['/memory@80000000']['reg'])
    return dict(snapshot=str(path),snapshot_sha256=hashlib.sha256(raw).hexdigest(),
                qnx_debug_log=dict(start=hex(offset+QNX_RUNTIME_BASE),bytes=len(log),nul_terminated=terminated),
                exact_runtime_messages=tokens,ufs0_lun0_qtd_slots=root,
                a_slot_file_reads=files,bts_present_in_initial_ifs=bts_ifs,
                bts_oem_dmc_init_count=dmc_init,bts_trex_instruction_fault=trex_fault,
                qnx_root_oem_executable_observed=root_exec,
                vm2_high_bank_described_in_derived_hwp=backed,
                qnx_root_mount_success_observed=bool(tokens['qtd_mount_success']),
                scope='Readback/driver execution establish an operational A-slot root; independent signature success needs its own evidence.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('session',type=Path)
    a=p.parse_args()
    path=a.session.resolve(strict=True)
    result=run(path)
    with (path/'qnx-rootfs-runtime-proof.json').open('x',encoding='utf-8') as f:
        json.dump(result,f,indent=2)
    print(json.dumps(result))
