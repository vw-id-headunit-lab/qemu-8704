
import hashlib
import json
import re
from pathlib import Path

import original_8704_vm as vm

                                                                  
BANK=(0x90000000,0xa0000000)
SHAPES={(800,400),(1140,114),(1120,780)}
IMAGE=re.compile(r"show_mem_img_info:.* format \((0x[0-9a-f]+)\) size (0x[0-9a-f]+)\((\d+)x(\d+)\) stride \((\d+)\) usage \((0x[0-9a-f]+)\)")
BPP=re.compile(r"show_mem_base_info:.* bpp \((\d+)\)")
ADDR=re.compile(r"show_mem_addr_info:.* (ALLOC|FREE_): idx \((\d+)\) paddr \((0x[0-9a-f]+)\) dvaddr \((0x[0-9a-f]+)\) vaddr \((0x[0-9a-f]+)\)")

def candidates(text):
    live={}
    img=None
    bpp=0
    for line in text.splitlines():
        b=BPP.search(line)
        if b:
            bpp=int(b[1])
            img=None
        i=IMAGE.search(line)
        if i:
            fmt,size,w,h,stride,usage=[int(x,0) for x in i.groups()]
            img=dict(format=fmt,allocation_bytes=size,width=w,height=h,stride=stride,usage=usage,bpp=bpp)
        m=ADDR.search(line)
        if not m:
            continue
        kind,index,pa,dev,va=m.groups()
        addr=int(pa,16)
        live.pop(addr,None)
        if kind=='FREE_' or img is None:
            continue
        w,h=img['width'],img['height']
        stride,bpp=img['stride'],img['bpp']
        extent=stride*h
        if (w,h) in SHAPES and bpp in (2,4) and w*bpp<=stride<=w*bpp+1024 and (
                extent<=img['allocation_bytes']<=8<<20 and not addr%4096 and
                BANK[0]<=addr<addr+img['allocation_bytes']<=BANK[1]):
            live[addr]=dict(img,index=int(index),physical=hex(addr),iova=dev,virtual=va,
                            active_at_end_of_log=True,bytes=extent,original_log=line.strip())
    return list(live.values())[-6:]

def capture(qmp,output):
    if qmp.execute('query-status')['running']:
        raise ValueError('Require stopped QNX board for bounded allocator inspection')
    text=(output/'qnx-boot-live.txt').read_text(encoding='utf8')
    records=candidates(text)
    for r in records:
        p=output/f"qnx-screen-{r['physical'][2:]}.bin"
        qmp.execute('pmemsave',dict(val=int(r['physical'],16),size=r['bytes'],filename=str(p)))
        data=p.read_bytes()
        if len(data)!=r['bytes']:
            raise ValueError('Unexpected Screen snapshot length')
        bpp=r['bpp']
        value0=data[:bpp]
        stride,w,h=r['stride'],r['width'],r['height']
        visible=w*bpp
        nonzero=0
        first_pattern=value0*w
        uniform=True
        for y in range(h):
            pixels=data[y*stride:y*stride+visible]
            nonzero+=visible-pixels.count(0)
            uniform=uniform and pixels==first_pattern
        r.update(path=p.name,sha256=hashlib.sha256(data).hexdigest(),
                 visible_nonzero_bytes=nonzero,visible_uniform=uniform,
                 scanout_verified=False)
    vm.save(output/'screen-buffer-observation.json',dict(buffers=records,read_only=True,
        scope='OEM QNX Screen allocations in a stopped VM; no active output or continuity inferred'))
