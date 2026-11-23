
import hashlib
import json
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory
import time
import zlib

import original_8704_vm as vm
from qnx_dsi_platform import DECON_PATH, DECON_WINDOWS
from qnx_dp0_platform import MEMBER_SHA256


                                                                           
                                                                          
ROLES={'ic':(10,100,1,0,800,400),'hud':(11,104,0,1,1120,780)}
CONFIG_SHA='629202751d6a852e9572f6f3e3c9768db9269245f29ef0351f2687a025b958ec'
RAM=(0x90000000,0xa0000000)
PHYSICAL_USAGE_MASK=0x7fff                                            
DISALLOWED_USAGE=(1<<9)|(1<<12)                                          
PORT_MODE=re.compile(r'wfdSetPortMode:\d+\]\s+port:(\d+) mode:(\d+)x(\d+) @')
PORT_DSI=re.compile(r'wfd_hw_set_dsim_enable:\d+\]\s+Port-(\d+) DSI(\d+) powered-on')


def validate_contract():
    from qnx_dp0_platform import SOURCE, MEMBER_OFFSET, MEMBER_BYTES
    from vlm_platform import Elf, factory_file, original, parse_dtb
    with factory_file(SOURCE).open('rb') as f:
        f.seek(3980057)
        graphics=f.read(1180)
        f.seek(MEMBER_OFFSET)
        dpu=f.read(MEMBER_BYTES)
    if hashlib.sha256(graphics).hexdigest()!=CONFIG_SHA or hashlib.sha256(dpu).hexdigest()!=MEMBER_SHA256:
        raise ValueError('Original QNX Screen/WFD identity changed')
    e=Elf(dpu)
    if struct.unpack('<8I',e.at(0x3c728,32))!=tuple(range(8)):
        raise ValueError('Original window channel permutation changed')
    if hashlib.sha256(e.at(0xf4a0,0x98)).hexdigest()!='0df2782aa11136fd86d3f95d5fa4f2171e6e88efe68b83a12f9f4490403745d3':
        raise ValueError('Original QNX window commit consumer changed')
                                                                       
    link=struct.pack('<QQ',0x57eb8,1027)
    rel=dpu.index(link)
    string=struct.unpack_from('<q',dpu,rel+16)[0]
    if e.at(string,10)!=b'ARGB_8888\0' or struct.unpack('<IIII',e.at(0x57ec0,16))!=(0,0,0,0):
        raise ValueError('Original linear ARGB8888 entry changed')
    conf=graphics.decode('ascii')
    hcb=original('hcb/hcb.img').read_bytes()
    h=hcb.index(b'VDTHYP\0\0')+8
    nodes=parse_dtb(hcb[h:h+struct.unpack_from('>I',hcb,h+4)[0]])
    if (RAM[0],RAM[1]-RAM[0]) not in struct.iter_unpack('>QQ',nodes['/vlm/vm@2/vm-memory']['reg']):
        raise ValueError('Original QNX private Screen RAM changed')
    for port,pipeline,*_ in ROLES.values():
        if not re.search(rf'begin class framebuffer\s+display = {port}\s+pipeline = {pipeline}\b',conf):
            raise ValueError('Original Screen role and pipeline changed')


def confirm_routes(log):
    modes={int(p):(int(w),int(h)) for p,w,h in PORT_MODE.findall(log)}
    links={int(p):int(dsi) for p,dsi in PORT_DSI.findall(log)}
    return {name for name,(port,_,_,dsi,w,h) in ROLES.items()
            if modes.get(port)==(w,h) and links.get(port)==dsi}


def verified_plane(name, windows, pipe, allocations):
    port,pipeline,pipe_id,dsi,width,height=ROLES[name]
    size=(height<<16)|width
    if (pipe['active-dsi']!=dsi or pipe['active-size']!=size or not pipe['running'] or
            not pipe.get('scanned-frames') or pipe.get('scanned-nonzero')!=1):
        return None
    active=[w for w in windows if w['owner']==pipe_id]
    if len(active)!=1:
        return None                                                                
    w=active[0]
    address=w['source-address']
    if (w['active-control']&3!=1 or not w['commits'] or not w['source-writes'] or
            w['active-start']!=0 or w['active-end']!=(((height-1)<<16)|(width-1)) or
            w['source-control']!=0 or w['source-crop-offset']!=0 or
            any(w[key]!=size for key in ('source-frame-size','source-crop-size','source-output-size')) or
            address!=pipe.get('scanned-source') or address&0xfff or
            not RAM[0]<=address<address+width*height*4<=RAM[1]):
        return None
    matching=[r for r in allocations if int(r['physical'],16)==address and
              r.get('iova')=='0x0' and r['format']==8 and r['bpp']==4 and
              r['width']==width and r['height']==height and r['stride']==4*width and
              r['usage']&~PHYSICAL_USAGE_MASK==0 and r['usage']&DISALLOWED_USAGE==0]
    if len(matching)!=1:
        return None
    return dict(port=port,pipeline=pipeline,pipe=pipe_id,dsi=dsi,width=width,height=height,
                physical=address,source_writes=w['source-writes'],commits=w['commits'],
                window=w['window'],frame_ticks=pipe['frame-ticks'],scanned_frames=pipe['scanned-frames'],
                format='ARGB8888',secure=False)


def to_png(raw, width, height):
    if len(raw)!=4*width*height:
        raise ValueError('Incomplete original QNX plane')
                                                                        
                                                                          
    lines=bytearray((3*width+1)*height)
    for y in range(height):
        row=raw[y*4*width:(y+1)*4*width]
        base=y*(3*width+1)+1
        lines[base:base+3*width:3]=row[2::4]
        lines[base+1:base+3*width:3]=row[1::4]
        lines[base+2:base+3*width:3]=row[0::4]
    if not any(lines):
        return None
    def chunk(tag, data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+
            chunk(b'IDAT',zlib.compress(lines,3))+chunk(b'IEND',b''))


def capture_live(qmp, directory, log, parse_allocations, *, call=None, properties=None):

    routes=confirm_routes(log)
    if not routes:
        return
    execute=qmp.execute if call is None else call
    available=properties if properties is not None else {
        r['name'] for r in execute('qom-list',{'path':DECON_PATH})}
    if not {'window-0-source-address','scanned-frames-0','scanned-frames-1'}<=available:
        return
    qom=lambda key:execute('qom-get',{'path':DECON_PATH,'property':key})
    if not any(qom(f'window-{i}-source-writes') for i in DECON_WINDOWS):
        return
    before=execute('query-status')
    own_pause=bool(before.get('running'))
    borrowed_pause=(before.get('status')=='paused' and
                    getattr(qmp,'_offline_snapshot_depth',0)>0)
    if not own_pause and not borrowed_pause:
        return
    previous={}
    planes={}
    from cal_init_platform import QNX_LOG_BASE, QNX_LOG_BYTES, decode_qnx_log_area
    if own_pause:
        execute('stop')
    try:
        keys=('commits','active-control','active-start','active-end','source-address','source-control',
              'source-frame-size','source-crop-offset','source-crop-size','source-output-size','source-writes')
        owners={i:qom(f'window-{i}-owner') for i in DECON_WINDOWS}
        needed={ROLES[name][2] for name in routes}
        windows=[dict(window=i,owner=owner,
                      **({k:qom(f'window-{i}-{k}') for k in keys} if owner in needed else {}))
                 for i,owner in owners.items()]
                                                                                
                                                                            
        live_routes=set()
        allocations=[]
        if any(w.get('source-writes') for w in windows):
            with TemporaryDirectory(prefix='qnx-allocator-',dir=directory) as scratch:
                snapshot=Path(scratch)/'log.bin'
                execute('pmemsave',{'val':QNX_LOG_BASE,'size':QNX_LOG_BYTES,'filename':str(snapshot)})
                payload,terminated=decode_qnx_log_area(snapshot.read_bytes())
            if terminated and payload.endswith(b'\n'):
                current=payload.decode('utf-8',errors='backslashreplace')
                live_routes=confirm_routes(current)&routes
                allocations=parse_allocations(current)
        for name in routes:
            path=directory/f'native-{name}-frame.json'
            if path.is_file():
                previous[name]=json.loads(path.read_bytes())
            if name not in live_routes:
                continue
            p=ROLES[name][2]
            pipe={k:qom(f'{k}-{p}') for k in ('active-dsi','active-size','running','frame-ticks',
                                             'scanned-frames','scanned-source','scanned-nonzero')}
            plane=verified_plane(name,windows,pipe,allocations)
            if plane:
                with TemporaryDirectory(prefix='qnx-plane-',dir=directory) as scratch:
                    raw=Path(scratch)/'scanout.bin'
                    execute('pmemsave',{'val':plane['physical'],'size':4*plane['width']*plane['height'],
                                           'filename':str(raw)})
                    planes[name]=(plane,raw.read_bytes())
    finally:
        if own_pause:
            execute('cont')
    for name in routes:
        path=directory/f'native-{name}-frame.json'
        frame=planes.get(name)
        meta=dict(status='no_verified_qnx_pixels',captured_monotonic=time.monotonic())
        if frame:
            plane,raw=frame
            png=to_png(raw,plane['width'],plane['height'])
            if png:
                digest=hashlib.sha256(png).hexdigest()
                image=directory/f'native-{name}-{digest}.png'
                if not image.exists():
                    tmp=image.with_suffix('.tmp')
                    tmp.write_bytes(png)
                    tmp.replace(image)
                old=previous.get(name,{})
                previous_frame=old.get('status')=='active_qnx_source_verified'
                count=old.get('scanned_frames')
                same_sequence=previous_frame and type(count) is int and 0<count<=plane['scanned_frames']
                                                                                        
                advance=same_sequence and plane['scanned_frames']>count
                meta=dict(plane,status='active_qnx_source_verified',frame_source='original_vm2_dpum',
                          dpu_sha256=MEMBER_SHA256,nonzero=True,png=image.name,png_sha256=digest,
                          captured_monotonic=meta['captured_monotonic'],
                          live_proof_monotonic=(meta['captured_monotonic'] if advance else
                                                old.get('live_proof_monotonic') if same_sequence else None))
        tmp=path.with_suffix('.tmp')
        vm.save(tmp,meta)
        tmp.replace(path)
        oldname=previous.get(name,{}).get('png','')
        if oldname!=meta.get('png') and re.fullmatch(rf'native-{name}-[a-f0-9]{{64}}\.png',oldname):
            (directory/oldname).unlink(missing_ok=True)
