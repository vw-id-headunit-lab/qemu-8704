
import argparse
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/emu'))
import original_8704_vm as vm

HYP_BASE=0x80000000
HYP_BYTES=0x480000
ORIGINAL_HV='49250f278f354865146c05355575312caa8dac24274f7d46e680aa48f1c889ff'
VTCR_WORD=0x801969d0                                                                  
OEM_VTCR=0x8001355c                                                           
RAM=(0x90000000,0xa0000000)
PA_BITS=36


def map_ipa(vttbr,vtcr,ipa,read):
    if vtcr!=OEM_VTCR or vttbr>>48!=2 or not 0<=ipa<1<<PA_BITS:
        raise ValueError('Unsupported original VM2 stage-2 context')
    mask=(1<<PA_BITS)-1
    table=vttbr&mask&~0x1ff
    entries=[]
    for shift in (30,21,12):
        width=0x3f if shift==30 else 0x1ff
        ptr=table+((ipa>>shift)&width)*8
        desc=read(ptr)
        entries.append((hex(ptr),hex(desc)))
        kind=desc&3
        if shift==12 and kind!=3 or shift!=12 and kind not in (1,3):
            raise ValueError(f'Invalid original stage-2 descriptor at {ptr:#x}')
        if shift==12 or kind==1:
            if not desc&(1<<10) or not desc&(1<<6):
                raise ValueError(f'Unaccessed or unreadable original stage-2 mapping at {ptr:#x}')
            offset=ipa&((1<<shift)-1)
            return (desc&mask&~((1<<shift)-1))+offset,(1<<shift)-offset,entries
        table=desc&mask&~0xfff
    raise ValueError('No VM2 leaf descriptor')


def verify(session):
    launch=json.loads((session/'launch.json').read_bytes())
    if launch.get('input_sha256')!=ORIGINAL_HV:
        raise ValueError('The captured monitor does not have the reviewed original VTCR code')
    state=json.loads((session/'cpu-system-registers.json').read_bytes())
    raw=(session/'hypervisor-ram.bin').read_bytes()
    if len(raw)!=HYP_BYTES:
        raise ValueError('Unexpected original hypervisor physical capture range')
    vtcr=struct.unpack_from('<I',raw,VTCR_WORD-HYP_BASE)[0]
    matches=[(cpu['thread'],cpu['system']) for cpu in state['cpus']
             if int(cpu['system'].get('VTTBR_EL2','0'),16)>>48==2 and
             int(cpu['system'].get('HCR_EL2','0'),16)&1]
    vttbrs={int(row['VTTBR_EL2'],16) for _,row in matches}
    if len(vttbrs)!=1 or any(int(row.get('VTCR_EL2',hex(vtcr)),16)!=vtcr for _,row in matches):
        raise ValueError('No single original active VM2 stage-2 context')
    vttbr=vttbrs.pop()

    def read(address):
        if address&7 or not HYP_BASE<=address<=HYP_BASE+len(raw)-8:
            raise ValueError(f'VM2 table entry was not in captured hypervisor RAM: {address:#x}')
        return struct.unpack_from('<Q',raw,address-HYP_BASE)[0]

    src=session/'qnx-native-runtime/screen-buffer-observation.json'
    rows=[]
    for item in json.loads(src.read_bytes())['buffers']:
        ipa=int(item['physical'],16)
        left=item['bytes']
        if not 0<left<=8<<20 or not RAM[0]<=ipa<ipa+left<=RAM[1]:
            raise ValueError('OEM allocator extent outside reviewed QNX RAM')
        identity=True
        spans=[]
        while left:
            physical,max_size,entries=map_ipa(vttbr,vtcr,ipa,read)
            size=min(left,max_size)
            if not RAM[0]<=physical<physical+size<=RAM[1]:
                raise ValueError('Resolved display memory leaves first original QNX RAM bank')
            identity=identity and physical==ipa
            spans.append(dict(ipa=hex(ipa),physical=hex(physical),bytes=size,pte_path=entries))
            ipa+=size
            left-=size
        rows.append(dict(source=item['path'],bytes=item['bytes'],identity=identity,spans=spans,
                         captured_bytes_are_correctly_addressed=identity,
                         visible_nonzero_bytes=item['visible_nonzero_bytes'] if identity else None,
                         scanout_verified=False))
    return dict(vmid=2,vtcr_el2=hex(vtcr),vttbr_el2=hex(vttbr),
                source_threads=[thread for thread,_ in matches],buffers=rows,
                scope='Original vLM stage-2 mappings and allocator bytes; not active display routing')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=verify(args.session.resolve(strict=True))
    vm.save(args.output,report)
    print(json.dumps(report))
