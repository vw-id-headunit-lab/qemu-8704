
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import struct
from tempfile import TemporaryDirectory
import time
import zlib

from linux_pid1_context import LINUX_SHA
from original_8704_vm import original_wod_ignores_pixel_alpha

VM3_RAM=((0xb8000000,0xd0000000),(0xe3c00000,0xe4c00000),(0xa80000000,0xb00000000),(0xb00000000,0xb80000000))
SYSMMU=('0x18c80000','0x1aaa0000')
WOD_DETECT=(0xffffffc010512020,bytes.fromhex('00f044b9c0035fd6'))
WOD_BLEND_ABI=('wod_update_plane',0xffffffc0105128c0,0x138,
               'a1f135a1f9af2e3091cf0cbbcea1000ed6e4350059e06c08a05728ee693374d4')
DRM_TYPE_ABI=('drm_connector_init',0xffffffc0104dc880,0x10c,bytes.fromhex('635e0e29'))
DRM_ZPOS_ABI=('drm_plane_create_zpos_property',0xffffffc0104dec80,128,
              'b4516cab9ad998671530a76b83dd391d4b870d15b70c45fdd40753cc661afb27')
CENTER_FORMATS=('XR24','AR24')
VMEMMAP_BASE=0xfffffffeffe00000
GEM_ABI=(
    ('exynos_drm_gem_map',0xffffffc0104f5bb0,584,'62b586be444384774f4bea5bae6d3b40093835964aad4311be96653768714c02'),
    ('exynos_drm_gem_alloc',0xffffffc0104f5f60,272,'e319ead3b223364c13779d9c2695f99bb2fffdbb8025fec15300f0cb3adf14d1'),
    ('exynos_drm_framebuffer_init',0xffffffc0104f5890,240,'b6d4fe7116762b3133c9222394c53119c482ab386df4fad7316e0029617a8e71'),
    ('drm_helper_mode_fill_fb_struct',0xffffffc0104c33c0,128,'553b57b39bf11f4dfcb3ef179d911815fcec8494a8c601235ec0db2a57df0448'),
    ('sg_next',0xffffffc0103b70c8,36,'d9f00549eb3b82e2930ce41ff1eb976c0e0bb97e58441a3460641a1103f5c17b'),
)
DRM_COMPLETION_ABI=(
    ('drm_crtc_vblank_count',0xffffffc0104e5ec8,56,'1ac211cd1012003b45f32dedd518c762a805e07e31ef23197c5ce42e887d45ca'),
    ('drm_handle_vblank',0xffffffc0104e77c8,600,'a47d757f9e7f86d23581d5da7c5e294e12e2e7ee61214937a6b2bdd13084cfca'),
    ('drm_crtc_arm_vblank_event',0xffffffc0104e6d28,136,'1e560dc72a7f6ba9f8aa3b43807ee30278dcf2c130847727761c153b1aeec81d'),
    ('drm_atomic_helper_setup_commit',0xffffffc0104c10f0,1040,'b59de307a8b9083680be74643d4513a65215db5eddff9a1882579d285d9a8d5e'),
    ('drm_atomic_helper_wait_for_flip_done',0xffffffc0104bed30,176,'4e4e17665fafeb33f56d517def18f13958509f78baffee580bdfda7de6b333b5'),
    ('exynos_drm_commit_hw_done',0xffffffc0104f4ce8,264,'a84fa5883f8bd01d4a26c58391055ccae684a3d3f0703b06c40561288ee36523'),
    ('drm_atomic_helper_commit_cleanup_done',0xffffffc0104bf148,296,'f36e7d92afb469bec8eb49748642b7f9d06dfc32cacfea56669431e8fe131f74'),
)
WOD_GATE_ABI=(
    ('wod_notifier_from_vdpu',0xffffffc010512af8,376,'09d35e326a680c56973e3231468363bb8cead490b256d4b1e1a300d1754dff15'),
    ('wod_get_window_update_req.isra.0',0xffffffc010512a48,176,'0dda37e6701608b5ee96505b034f113df2a449879a7a04aee05744a1f2b16a19'),
)


def inspect_wod_gate(dev, crtc, read, members):

    def u32(address):
        return (read(address&~7)>>(8*(address&7)))&0xffffffff

    wod=read(crtc+0x498)
    if not wod or read(wod+8)!=dev or read(wod+0x4e8)!=crtc:
        raise ValueError('Original WOD context is not bound to this CRTC')
    gate=u32(crtc+0x9c)&0xff
    pipe=u32(wod+0x4f0)
    if gate not in (0,1) or pipe not in (0,2):
        raise ValueError('Original WOD vblank flag or physical pipe is invalid')
    mask=u32(crtc+0x9760)
    checked=[]
    for plane in members(dev+0x2d0,8):
        index=u32(plane+0x24c)
        if index>=32 or not mask&(1<<index):
            continue
        window=u32(plane-0x40)
        if window>=16 or any(row['index']==index for row in checked):
            raise ValueError('Invalid WOD window selected by original plane mask')
        checked.append(dict(index=index,window=window))
    return dict(status='observed',vdpu_vblank_gate=bool(gate),decon_pipe=pipe,
                plane_mask=mask,windows_tested=checked)


def inspect_flip_completion(dev, crtc, read, members):

    def u32(address):
        return (read(address&~7)>>(8*(address&7)))&0xffffffff

    if read(crtc)!=dev:
        raise ValueError('Original DRM CRTC belongs to another device')
    count=u32(dev+0x170)
    pipe=u32(crtc+0x90)
    array=read(dev+0x130)
    if not array or not 0<=pipe<count<=16:
        raise ValueError('Original DRM vblank array is not bound to the CRTC')
    vblank=array+pipe*0x158
    enabled=u32(vblank+0x150)&0xff
    if enabled not in (0,1):
        raise ValueError('Invalid OEM DRM vblank enabled flag')
    commits=[]
    for commit in members(crtc+0x3f8,0x70):
        if read(commit)!=crtc or len(commits)>=8:
            raise ValueError('Original DRM commit owner or count is invalid')
        commits.append(dict(flip_done=bool(u32(commit+0x10)),
                            hw_done=bool(u32(commit+0x30)),
                            cleanup_done=bool(u32(commit+0x50))))
    events=[]
    for index,event in enumerate(members(dev+0x148,0x28)):
        owner=u32(event+0x48)
        if owner>=count or index>=16:
            raise ValueError('Original DRM vblank event owner or count is invalid')
        if owner==pipe:
            events.append(read(event+0x50))
    return dict(status='observed',pipe=pipe,vblank_count=read(vblank+0x50),
                vblank_enabled=bool(enabled),armed_vblank_sequences=events,commits=commits)


def walk_iova(root, iova, read_word, backed):

    if not 0<=iova<1<<32 or root&0x3fff or not backed(root,0x4000):
        raise ValueError('Unbacked SysMMU root or non-32-bit IOVA')
    first=read_word(root+(iova>>20)*4)
    if first&7==2:
        bits=20
        leaf=first
    elif first&7==1:
        second=(first<<4)&0xffffffc00
        if not backed(second,0x400):
            raise ValueError('SysMMU L2 table is outside backed VM3 RAM')
        leaf=read_word(second+((iova>>12)&0xff)*4)
        kind=leaf&3
        if kind not in (1,2):
            raise ValueError('OEM SysMMU IOVA has no L2 page')
        bits=16 if kind==1 else 12
    else:
        raise ValueError('OEM SysMMU IOVA has no L1 descriptor')
    mask=(1<<bits)-1
    physical=((leaf<<4)&~mask)|(iova&mask)
    if not backed(physical,1):
        raise ValueError('SysMMU display address is outside backed VM3 RAM')
    return physical,(1<<bits)-(iova&mask)


def contiguous_spans(root, iova, length, read_word, backed):
    if not 0<length<=16*1024*1024 or iova+length>1<<32:
        raise ValueError('OEM image exceeds bounded 32-bit aperture')
    remaining=length
    spans=[]
    while remaining:
        physical,available=walk_iova(root,iova,read_word,backed)
        span=min(remaining,available)
        if not backed(physical,span):
            raise ValueError('OEM image crosses an unbacked VM3 boundary')
        if spans and spans[-1][0]+spans[-1][1]==physical:
            spans[-1]=(spans[-1][0],spans[-1][1]+span)
        else:
            spans.append((physical,span))
        if len(spans)>1024:
            raise ValueError('OEM image scatter list exceeded capture budget')
        iova+=span
        remaining-=span
    return spans


def nonoverlapping(spans):
    ordered=sorted(spans)
    return all(left+n<=right for (left,n),(right,_) in zip(ordered,ordered[1:]))


def gem_scatter_spans(gem, iova, plane_offset, length, memstart, read, backed):

    if not (0<length<=16<<20 and memstart%4096==0 and 0<=plane_offset<=16<<20):
        raise ValueError('Native GEM range is invalid')
    size=read(gem+0x140)
    if (plane_offset+length>size or size>32<<20 or not read(gem+0x148)
            or not read(gem+0x150) or read(gem+0x160)+plane_offset!=iova):
        raise ValueError('Native GEM allocation and active framebuffer do not match')
    sgt=read(gem+0x158)
    entry=read(sgt)
    counts=read(sgt+8)
    mapped,original=counts&0xffffffff,counts>>32
    if not 1<=mapped<=original<=2048 or read(entry+0x10)+plane_offset!=iova:
        raise ValueError('Native GEM attachment is not the active DMA mapping')
                                                                           
    anchor=VMEMMAP_BASE-((memstart>>12)<<6)
    skip,remaining=plane_offset,length
    spans=[]
    seen=set()
    for _ in range(original):
        if entry in seen:
            raise ValueError('Native GEM scatterlist loops')
        seen.add(entry)
        page=read(entry)
        fields=read(entry+8)
        offset,chunk=fields&0xffffffff,fields>>32
        delta=(page&~3)-anchor
        if page&1 or delta<0 or delta&63 or offset>=4096 or not 0<chunk<=size:
            raise ValueError('Native GEM scatter entry is invalid')
        physical=(delta>>6)*4096+offset
        if not backed(physical,chunk):
            raise ValueError('Native GEM pages are outside backed VM3 RAM')
        if skip>=chunk:
            skip-=chunk
        else:
            physical+=skip
            take=min(remaining,chunk-skip)
            if spans and spans[-1][0]+spans[-1][1]==physical:
                spans[-1]=(spans[-1][0],spans[-1][1]+take)
            else:
                spans.append((physical,take))
            skip=0
            remaining-=take
            if not remaining:
                if len(spans)>1024 or not nonoverlapping(spans):
                    raise ValueError('Native GEM pixel pages are aliased or too fragmented')
                return spans
        if page&2:
            break
        entry+=0x20
        following=read(entry)
        if following&1:
            entry=following&~3
    raise ValueError('Native GEM scatterlist does not cover the active plane')


def read_active_raster(proof, read_physical):

    if (proof.get('status') not in ('active_scanout_pages_verified','active_gem_pages_verified')
            or proof.get('secure') is not False):
        raise ValueError('No verified unprotected active OEM DRM plane')
    if proof['status']=='active_gem_pages_verified' and (proof.get('gem_plane_verified') is not True or
            proof.get('capture_origin')!='original_exynos_gem_sg'):
        raise ValueError('Native GEM pixel source was not verified')
    width,height,pitch=(proof[k] for k in ('width','height','pitch'))
    if ((width,height)!=(1560,700) or not width*4<=pitch<=16384 or
            proof.get('format') not in CENTER_FORMATS or proof.get('modifier')!=0):
        raise ValueError('Unsupported native plane format')
    spans=[(int(address,16),length) for address,length in proof['physical_spans']]
    if not 0<len(spans)<=1024 or sum(length for _,length in spans)!=pitch*height:
        raise ValueError('Incomplete OEM DRM physical spans')
    packed=bytearray()
    for address,length in spans:
        if length<=0 or not any(start<=address<address+length<=end for start,end in VM3_RAM):
            raise ValueError('Read request not confined to the verified guest private RAM')
        chunk=read_physical(address,length)
        if len(chunk)!=length:
            raise ValueError('Short native physical read')
        packed.extend(chunk)
    return packed


def center_active_footprint(proof):

    width,height,pitch=(proof[k] for k in ('width','height','pitch'))
    if ((width,height)!=(1560,700) or not width*4<=pitch<=16384 or
            proof.get('status') not in ('active_scanout_pages_verified','active_gem_pages_verified') or
            proof.get('secure') is not False or proof.get('modifier')!=0 or
            proof.get('format') not in CENTER_FORMATS):
        raise ValueError('Not a verified linear OEM center target')
    if proof['status']=='active_gem_pages_verified' and (proof.get('gem_plane_verified') is not True or
            proof.get('capture_origin')!='original_exynos_gem_sg'):
        raise ValueError('Unverified OEM GEM plane')
    row=width*4
    spans=[(int(address,16),length) for address,length in proof['physical_spans']]
    if (not 0<len(spans)<=1024 or sum(length for _,length in spans)!=pitch*height or
            not nonoverlapping(spans) or any(length<=0 or not any(
                lo<=address<address+length<=hi for lo,hi in VM3_RAM) for address,length in spans)):
        raise ValueError('Incomplete or invalid physical layout')
    digest=hashlib.sha256(struct.pack('<II',row,height))
    index=offset=0
    for _ in range(height):
        active,left=0,pitch
        begin=run=0
        while left:
            base,length=spans[index]
            take=min(left,length-offset)
            address=base+offset
            visible=min(take,row-active)
            if visible:
                if run and begin+run!=address:
                    digest.update(struct.pack('<QI',begin,run))
                    run=0
                if not run:
                    begin=address
                run+=visible
                active+=visible
            left-=take
            offset+=take
            if offset==length:
                index+=1
                offset=0
        digest.update(struct.pack('<QI',begin,run))
    return digest.hexdigest()


def correlate_center_target(proof, raster, snapshot):

    footprint=center_active_footprint(proof)
    if not isinstance(snapshot,str) or len(snapshot)>8192:
        raise ValueError('Invalid VM3 GPU snapshot size')
    data=json.loads(snapshot)
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('targets'),list):
        raise ValueError('Unsupported VM3 GPU snapshot')
    generation,evictions=data.get('generation'),data.get('evictions')
    targets=data['targets']
    if (type(generation) is not int or type(evictions) is not int or
            not 0<=evictions<=generation or len(targets)>16):
        raise ValueError('Invalid VM3 GPU history counters')
    match=None
    seen=set()
    keys=('generation','color_generation','age_ns','color_age_ns','pitch','bytes','rgb')
    full=1560*700*4
    for target in targets:
        if (not isinstance(target,dict) or not all(type(target.get(key)) is int for key in keys) or
                type(target.get('clear')) is not bool):
            raise ValueError('Malformed VM3 GPU target record')
        f=target.get('footprint')
        content=target.get('content')
        if (not isinstance(f,str) or not re.fullmatch(r'[a-f0-9]{64}',f) or f in seen or
                not isinstance(content,str) or (content and (not re.fullmatch(r'[a-f0-9]{64}',content)
                    or target['bytes']!=full or target['clear'])) or
                not 0<=target['color_generation']<=target['generation']<=generation or
                target['generation']==0 or target['age_ns']<0 or
                (target['color_age_ns']<target['age_ns'] if target['color_generation']
                    else target['color_age_ns']!=-1) or
                not 6240<=target['pitch']<=6303 or not 0<target['bytes']<=full or
                not 0<=target['rgb']<=1560*700 or
                (target['clear'] and (target['rgb'] or target['bytes']!=full))):
            raise ValueError('Inconsistent VM3 GPU target record')
        seen.add(f)
        if f==footprint:
            match=target
    evidence=dict(active_footprint_sha256=footprint,gpu_generation=generation,
                  gpu_target_evictions=evictions,gpu_targets_sampled=len(targets),
                  status='no_matching_retained_target')
    if match is None:
        return evidence
    evidence.update(status='matching_physical_rows',latest_write=match)
    if match['content'] or match['clear']:
        pitch=proof['pitch']
        if len(raster)!=pitch*700:
            raise ValueError('Incomplete native raster')
        captured=hashlib.sha256()
        for offset in range(0,len(raster),pitch):
            captured.update(raster[offset:offset+6240])
        active_content=captured.hexdigest()
        reference=match['content'] or hashlib.sha256(bytes(full)).hexdigest()
        evidence.update(active_content_sha256=active_content,latest_write_content_matches=active_content==reference)
    return evidence


def render_active_raster(proof, packed):

    width,height,pitch=(proof[k] for k in ('width','height','pitch'))
    if len(packed)!=pitch*height:
        raise ValueError('Incomplete captured OEM raster')
    line_bytes=width*3
    rows=bytearray((line_bytes+1)*height)
    ignore_alpha=proof['format']=='AR24' and original_wod_ignores_pixel_alpha(proof)
    opaque_alpha=proof['format']=='AR24'
    blend=proof.get('original_wod_blend',{})
    if isinstance(blend,dict) and blend.get('verified') is True and blend.get('plane_alpha8')!=255:
        raise ValueError('Translucent original plane requires the original DRM blend composition')
    for y in range(height):
        src=y*pitch
        dst=y*(line_bytes+1)+1
        if proof['format']=='AR24' and packed[src+3:src+width*4:4].count(0xff)!=width:
            opaque_alpha=False
            if not ignore_alpha:
                raise ValueError('Nonopaque AR24 needs the original DRM blend composition')
        for channel in range(3):
            rows[dst+channel:dst+line_bytes:3]=packed[src+2-channel:src+width*4:4]

    def png_chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))

    image=b'\x89PNG\r\n\x1a\n'+png_chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
    image+=png_chunk(b'IDAT',zlib.compress(rows,level=3))+png_chunk(b'IEND',b'')
    return image,dict(pixel_sha256=hashlib.sha256(rows).hexdigest(),nonzero=any(rows),
                      opaque_alpha_verified=opaque_alpha,alpha_ignored_by_original_plane=ignore_alpha,
                      raster_bytes=len(packed),png_sha256=hashlib.sha256(image).hexdigest())


def read_active_rgb(proof, read_physical):
    return render_active_raster(proof,read_active_raster(proof,read_physical))


def summarize_premultiplied_raster(proof, packed):

    blend=proof.get('original_wod_blend',{})
    if (proof.get('format')!='AR24' or proof.get('planes_on_crtc')!=1 or
            proof.get('crtc_active') is not True or not isinstance(blend,dict) or
            blend.get('verified') is not True or blend.get('drm_pixel_blend_mode')!=0 or
            blend.get('wod_blend')!=1 or blend.get('plane_alpha8')!=255):
        return None
    width,height,pitch=(proof[k] for k in ('width','height','pitch'))
    if not (0<width<=1560 and 0<height<=700 and width*4<=pitch<=16384 and
            len(packed)==pitch*height):
        raise ValueError('Incomplete original premultiplied raster')
    opaque=transparent=nonzero=invalid=0
    for y in range(height):
        start=y*pitch
        row=packed[start:start+width*4]
        for b,g,r,a in zip(row[0::4],row[1::4],row[2::4],row[3::4]):
            opaque+=a==255
            transparent+=a==0
            nonzero+=bool(b|g|r)
            invalid+=b>a or g>a or r>a
    return dict(status='original_premultiplied_foreground_profile',
                captured_monotonic=proof.get('captured_monotonic'),
                framebuffer_id=proof.get('framebuffer_id'),capture_origin=proof.get('capture_origin'),
                width=width,height=height,total_pixels=width*height,opaque_pixels=opaque,
                transparent_pixels=transparent,nonzero_rgb_pixels=nonzero,
                out_of_premultiplied_range_pixels=invalid,
                scope='Original foreground only; does not establish backdrop, final display, or HMI readiness')


def encode_foreground_rgba(width,height,pitch,packed):

    if not 0<width<=1560 or not 0<height<=700 or not width*4<=pitch<=16384 or len(packed)!=pitch*height:
        raise ValueError('Incomplete original foreground')
                                                                               
    lut=tuple(bytes(min(255,(c*255+a//2)//a) for c in range(256)) if a else bytes(256)
              for a in range(256))
    line=width*4+1
    rows=bytearray(height*line)
    for y in range(height):
        source=y*pitch
        dest=y*line+1
        row=packed[source:source+width*4]
        alpha=row[3::4]
        rows[dest+3:dest+width*4:4]=alpha
        for channel in range(3):
            rows[dest+channel:dest+width*4:4]=bytes(lut[a][c] for a,c in zip(alpha,row[2-channel::4]))

    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))

    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+
            chunk(b'IDAT',zlib.compress(rows,level=3))+chunk(b'IEND',b''))


def retain_nonzero_foreground(directory,proof,raster,profile,evidence,save):
    if (not profile['nonzero_rgb_pixels'] or profile['out_of_premultiplied_range_pixels'] or
            evidence.get('latest_write_content_matches') is not True or
            evidence.get('latest_write',{}).get('rgb')!=profile['nonzero_rgb_pixels']):
        return
    path=directory/'native-center-last-nonzero-foreground.png'
    record=path.with_suffix('.json')
    digest=None
    if path.is_file() and record.is_file():
        prior=json.loads(record.read_bytes())
        if (prior.get('active_content_sha256')==evidence['active_content_sha256'] and
                prior.get('png_sha256')==hashlib.sha256(path.read_bytes()).hexdigest()):
            digest=prior['png_sha256']
    if digest is None:
        png=encode_foreground_rgba(proof['width'],proof['height'],proof['pitch'],raster)
        digest=hashlib.sha256(png).hexdigest()
        pending=path.with_suffix('.part')
        pending.write_bytes(png)
        pending.replace(path)
                                                                            
                                                                             
    pending_record=record.with_suffix('.json.part')
    save(pending_record,dict(**profile,active_content_sha256=evidence['active_content_sha256'],
                     gpu_generation=evidence['latest_write']['generation'],png_sha256=digest,
                     kernel_sha256=proof.get('kernel_sha256'),secure=proof.get('secure'),
                     full_display=False,live_stream=False))
    pending_record.replace(record)


def publish_current_foreground(directory, proof, raster, profile):
    if (proof.get('status')!='active_scanout_pages_verified' or
            proof.get('kernel_sha256')!=LINUX_SHA or proof.get('capture_origin')!='original_sysmmu' or
            proof.get('sysmmu_matches_gem') is not True or proof.get('secure') is not False or
            not profile['nonzero_rgb_pixels'] or profile['out_of_premultiplied_range_pixels']):
        return None
    png=encode_foreground_rgba(proof['width'],proof['height'],proof['pitch'],raster)
    dest=directory/'native-center-current-foreground.png'
    temp=dest.with_suffix('.png.part')
    temp.write_bytes(png)
    temp.replace(dest)
    return dict(proof,status='verified_original_current_foreground',full_display=False,
                nonzero_rgb_pixels=profile['nonzero_rgb_pixels'],
                out_of_premultiplied_range_pixels=profile['out_of_premultiplied_range_pixels'],
                png=dest.name,png_sha256=hashlib.sha256(png).hexdigest())


def retain_distinct_scanout(directory, proof, png, evidence, save):

    content = evidence.get('active_content_sha256')
    write = evidence.get('latest_write', {})
                                                                          
                                                                             
    known_map = '334b6c564a9f1958cca5c835681f00aa1676e0193acc2db75ae3752c630cca9e'
    if (proof.get('secure') is not False or proof.get('status') != 'active_scanout_pages_verified'
            or proof.get('nonzero') is not True or evidence.get('status') != 'matching_physical_rows'
            or evidence.get('latest_write_content_matches') is not True or not write.get('rgb')
            or not isinstance(content, str) or not re.fullmatch(r'[a-f0-9]{64}', content)
            or content == known_map):
        return
    target = directory / 'native-center-distinct-scanout.png'
    record = target.with_suffix('.json')
    digest = hashlib.sha256(png).hexdigest()
    if proof.get('png_sha256') != digest:
        raise ValueError('Distinct scanout PNG does not match its raster proof')
    if target.is_file() and record.is_file():
        prior = json.loads(record.read_bytes())
        if (prior.get('active_content_sha256') == content and prior.get('png_sha256') == digest
                and hashlib.sha256(target.read_bytes()).hexdigest() == digest):
            return
    pending = target.with_suffix('.png.part')
    pending.write_bytes(png)
    pending.replace(target)
    pending = record.with_suffix('.json.part')
    save(pending, dict(status='verified_original_active_scanout_sample', live_stream=False,
                       secure=False, png=target.name, png_sha256=digest, active_content_sha256=content,
                       captured_monotonic=proof.get('captured_monotonic'), generation=write.get('generation'),
                       rgb_nonzero=write['rgb'], width=proof['width'], height=proof['height']))
    pending.replace(record)


def vm3_metadata_reader(read_physical):

    @lru_cache(maxsize=256)
    def page(base):
        data=read_physical(base,4096)
        if len(data)!=4096:
            raise ValueError('Short native VM3 metadata page')
        return data

    def word(address):
        base=address&~4095
        if address&7 or not any(start<=base<base+4096<=end for start,end in VM3_RAM):
            raise ValueError(f'Original VM3 translated metadata address {address:#x} is outside VM3 RAM')
        return struct.unpack_from('<Q',page(base),address-base)[0]

    return word,page.cache_info


class _ImmutableImageView:
    __slots__=('data','position')
    def __init__(self,data):self.data=data;self.position=0
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def seek(self,offset):
        if not 0<=offset<=len(self.data):raise ValueError('Original image offset exceeds bounds')
        self.position=offset
    def read(self,size):
        start=self.position;self.position=min(len(self.data),start+size)
        return self.data[start:self.position]


class VerifiedFactoryImage:
    __slots__=('__data','__symbols','__addresses')
    def __init__(self,symbols):
        from types import MappingProxyType
        if symbols.get('sha256')!=LINUX_SHA:raise ValueError('Original symbol identity differs')
        self.__symbols=symbols
        self.__addresses=MappingProxyType({s['name']:int(s['address'],16)for s in symbols['symbols']})
        from vlm_platform import factory_file
        data=factory_file('ivi/Image').read_bytes()
        if hashlib.sha256(data).hexdigest()!=LINUX_SHA:
            raise ValueError('Original kernel identity differs')
        self.__data=data
    def open(self):
        return _ImmutableImageView(self.__data)
    def addresses(self,symbols):
        if symbols is not self.__symbols:raise ValueError('Different bound original symbols')
        return self.__addresses


def inspect(symbols, read_u64, directory, sysmmu=None, *, verified_image=None):
    if symbols.get('sha256')!=LINUX_SHA:
        raise ValueError('Not the reviewed OEM Linux DRM layout')
    if verified_image is not None and type(verified_image) is not VerifiedFactoryImage:
        raise ValueError('Require a verified immutable factory image')
    va=({s['name']:int(s['address'],16) for s in symbols['symbols']} if verified_image is None
        else verified_image.addresses(symbols))
    from vlm_platform import factory_file
    with (factory_file('ivi/Image').open('rb') if verified_image is None else verified_image.open()) as image:
        address, original=WOD_DETECT
        image.seek(address-0xffffffc010080000)
        wod_detect_verified=(va.get('wod_detect')==address and image.read(len(original))==original)
        gem_abi_verified=True
        for symbol,address,size,digest in GEM_ABI:
            image.seek(address-0xffffffc010080000)
            gem_abi_verified &= va.get(symbol)==address and hashlib.sha256(image.read(size)).hexdigest()==digest
        flip_abi_verified=True
        for symbol,address,size,digest in DRM_COMPLETION_ABI:
            image.seek(address-0xffffffc010080000)
            flip_abi_verified &= va.get(symbol)==address and hashlib.sha256(image.read(size)).hexdigest()==digest
        wod_gate_verified=True
        for symbol,address,size,digest in WOD_GATE_ABI:
            image.seek(address-0xffffffc010080000)
            wod_gate_verified &= va.get(symbol)==address and hashlib.sha256(image.read(size)).hexdigest()==digest
        symbol,address,size,digest=WOD_BLEND_ABI
        image.seek(address-0xffffffc010080000)
        wod_blend_verified=(va.get(symbol)==address and hashlib.sha256(image.read(size)).hexdigest()==digest)
        symbol,address,offset,expected=DRM_TYPE_ABI
        image.seek(address+offset-0xffffffc010080000)
        drm_type_verified=(va.get(symbol)==address and image.read(len(expected))==expected)
        symbol,address,size,digest=DRM_ZPOS_ABI
        image.seek(address-0xffffffc010080000)
        drm_zpos_verified=(va.get(symbol)==address and hashlib.sha256(image.read(size)).hexdigest()==digest)

    @lru_cache(maxsize=8192)
    def read(address):
        if address&7 or not 0xffffff8000000000<=address<=0xfffffffffffffff8:
            raise ValueError(f'Invalid OEM DRM pointer {address:#x}')
        return read_u64(address)

    def u32(address):
        return (read(address&~7)>>(8*(address&7)))&0xffffffff

    def byte(address):
        return (read(address&~7)>>(8*(address&7)))&0xff

    def name(address):
        data=bytes(byte(address+i) for i in range(32)).partition(b'\0')
        if not data[1]:
            raise ValueError('Unterminated OEM DRM connector name')
        return data[0].decode('ascii')

    def members(head, offset):
        link=read(head)
        previous=head
        seen=set()
        while link!=head:
            if link in seen or len(seen)>=64 or read(link+8)!=previous:
                raise ValueError('Inconsistent OEM DRM object list')
            seen.add(link)
            yield link-offset
            previous=link
            link=read(link)
        if read(head+8)!=previous:
            raise ValueError('Inconsistent OEM DRM list tail')

    def idr(index):
        root=va['drm_minors_idr']
        index-=u32(root+0x10)
        if index<0:
            return 0
        entry=read(root+8)
        previous=66
        for _ in range(11):
            if entry&3!=2:
                return entry if previous<66 or not index else 0
            if entry<0xffffff8000000000:
                raise ValueError('Unexpected internal entry in OEM DRM IDR')
            node=entry-2
            shift=byte(node)
            if shift>=previous or shift%6 or index>=1<<(previous if previous<66 else shift+6):
                return 0
            entry=read(node+0x28+8*((index>>shift)&0x3f))
            previous=shift
        raise ValueError('OEM DRM IDR depth exceeded')

    result={'status':'no_active_original_scanout','kernel_sha256':LINUX_SHA,
            'connector':'Virtual-1','scope':'Paused VM3 atomic DRM state; not physical scanout',
            'connector_observations':[]}
    devices={read(minor+0x10) for i in range(8) if (minor:=idr(i))}
    routes=[]
    for dev in sorted(devices-{0}):
        crtcs=None
        for conn in members(dev+0x278,0x18):
            label=name(read(conn+0x48))
            state=read(conn+0x420)
            crtc=read(state+8) if state else 0
            observation={'name':label,'crtc':hex(crtc) if crtc else None}
            if drm_type_verified:
                                                                             
                observation['connector_type']=u32(conn+0x74)
            if wod_detect_verified and label in ('Virtual-1','Virtual-2','Virtual-3'):
                                                                             
                value=u32(conn+0x4f0)
                if value in (1,2,3):
                    observation['wod_detect']=value
            if flip_abi_verified and crtc and label in ('Virtual-1','Virtual-2','Virtual-3'):
                try:
                    if crtcs is None:
                        crtcs=set(members(dev+0x2e8,0x10))
                    if read(state)!=conn or crtc not in crtcs:
                        raise ValueError('Original DRM connector and CRTC do not belong to this device')
                    observation['flip_completion']=inspect_flip_completion(dev,crtc,read,members)
                except (ValueError,OSError,RuntimeError) as exc:
                    observation['flip_completion']=dict(status='unverified',reason=str(exc))
                else:
                    if wod_gate_verified:
                        try:
                            observation['wod_gate']=inspect_wod_gate(dev,crtc,read,members)
                        except (ValueError,OSError,RuntimeError) as exc:
                            observation['wod_gate']=dict(status='unverified',reason=str(exc))
            result['connector_observations'].append(observation)
            if label=='Virtual-1' and crtc:
                if not state or read(state)!=conn:
                    raise ValueError('OEM DRM connector state has another owner')
                routes.append((dev,crtc))
    if not routes:
        return result
    if len(routes)!=1:
        raise ValueError('Ambiguous original Virtual-1 DRM routing')
    dev,crtc=routes[0]
    if crtc not in set(members(dev+0x2e8,0x10)):
        raise ValueError('DRM connector CRTC belongs to another device')
    state=read(crtc+0x3f0)
    if not state or not byte(state+8):
        return result
    primary=read(crtc+0x80)
    active=[]
    for plane in members(dev+0x2d0,8):
        plane_state=read(plane+0x258)
        if plane_state and read(plane_state+8)==crtc and read(plane_state+0x10):
            active.append((plane,plane_state))
    result.update(crtc_active=True,planes_on_crtc=len(active))
    if drm_zpos_verified:
                                                                          
        result['original_active_plane_order']=[
            dict(primary=plane==primary, zpos=u32(plane_state+0x48),
                 normalized_zpos=u32(plane_state+0x4c),zpos_property=bool(read(plane+0x268)))
            for plane,plane_state in active if read(plane_state)==plane]
    if not active:
        return result
    if len(active)!=1 or active[0][0]!=primary:
        result.update(status='composition_not_supported')
        return result
    state=active[0][1]
    if wod_blend_verified and read(state)==primary:
        wod=next((o.get('wod_gate',{}) for o in result['connector_observations'] if o['name']=='Virtual-1'),{})
        index=u32(primary+0x24c)
        if (index<32 and wod.get('status')=='observed' and wod.get('plane_mask')==1<<index and
                len(wod.get('windows_tested',()))==1):
                                                                       
                                                  
            mode=byte(state+0x42)|(byte(state+0x43)<<8)
            if mode in (0,1,2):
                result['original_wod_blend']=dict(verified=True,plane_alpha8=byte(state+0x41),
                                                  drm_pixel_blend_mode=mode,wod_blend=(mode+1)%3)
    framebuffer=read(state+0x10)
    if read(framebuffer+0x50)!=va['exynos_drm_fb_funcs']:
        raise ValueError('Active framebuffer is not the OEM Exynos object')
    fmt=read(framebuffer+0x48)
    fourcc=u32(fmt).to_bytes(4,'little').decode('ascii',errors='replace')
    width,height=(u32(framebuffer+i) for i in (0x80,0x84))
    pitch=u32(framebuffer+0x58)
    iova=read(framebuffer+0xe8)                                             
    modifier=read(framebuffer+0x78)
    gem=read(framebuffer+0xc8)
    secure=bool(u32(gem+0x170)&8)
    coords=[u32(state+i) for i in range(0x20,0x40,4)]
    result.update(framebuffer=hex(framebuffer),framebuffer_id=u32(framebuffer+0x18),
                  width=width,height=height,pitch=pitch,format=fourcc,modifier=modifier,
                  iova=hex(iova),source_and_destination=coords,secure=secure)
    if (secure or (width,height)!=(1560,700) or modifier or fourcc not in CENTER_FORMATS or
            not width*4<=pitch<=16384 or coords!=[0,0,width,height,0,0,height<<16,width<<16]):
        result.update(status='active_format_not_supported')
        return result
    if sysmmu is None:
        sysmmu=json.loads((directory/'linux-sysmmu-runtime.json').read_bytes())
    hcb=json.loads((directory/'hcb-adaptation.json').read_bytes())
    banks=[(int(b['base'],16),int(b['base'],16)+b['bytes']) for change in hcb['changes'] for b in change.get('banks',[])]
    def backed(address,length):
        return any(start<=address<=address+length<=end for start,end in VM3_RAM) and any(
            start<=address<=address+length<=end for start,end in banks)
    memstart=read(va['memstart_addr'])
    gem_pages=None
    if gem_abi_verified and gem==read(framebuffer+0xa8):
        try:
            gem_pages=gem_scatter_spans(gem,iova,u32(framebuffer+0x68),pitch*height,memstart,read,backed)
        except ValueError as error:
            result['gem_mapping_error']=str(error)
    def read_phys_word(address):
        if address<memstart:
            raise ValueError('SysMMU word is below native linear map')
        return u32(0xffffff8000000000+address-memstart)
    translated=[]
    roots={int(sysmmu[port]['ptbase-page'])<<12 for port in SYSMMU if sysmmu.get(port,{}).get('ptbase-writes')}
    for root in sorted(roots):
        try:
            spans=contiguous_spans(root,iova,pitch*height,read_phys_word,backed)
            translated.append((root,spans))
        except ValueError:
            continue
    trusted_sysmmu=(bool(translated) and nonoverlapping(translated[0][1]) and
                    all(spans==translated[0][1] for _,spans in translated))
    result['sysmmu_roots']=[hex(root) for root,_ in translated]
    if gem_pages is not None and (not trusted_sysmmu or gem_pages!=translated[0][1]):
        result.update(status='active_gem_pages_verified',capture_origin='original_exynos_gem_sg',
                      gem_plane_verified=True,sysmmu_matches_gem=False,
                      physical_spans=[(hex(pa),n) for pa,n in gem_pages])
        return result
    if not trusted_sysmmu:
        result.update(status='active_scanout_mapping_unproven')
        return result
    result.update(status='active_scanout_pages_verified',physical_spans=[(hex(pa),n) for pa,n in translated[0][1]],
                  capture_origin='original_sysmmu',sysmmu_matches_gem=True if gem_pages else None)
    return result


VDPU_ABI=(
    ('nk_xirq_attach',0xffffffc010752f40,360,'6843ba14e986974661c87ebbbcb00829d5225a9d41d0753412af2122b90eeea2'),
    ('vdpu_irq_hdl',0xffffffc01078adb8,40,'2028849239b8fbfedd09fd087a49b9b78303f84b29c8a16d3611dd2790252ad7'),
    ('vdpu_irqhdl_work',0xffffffc01078ade0,816,'c95b4110fc2d990a4b0659052687dbb9f708fe296c12f95343bd86826adf47a8'),
    ('vdpu_probe',0xffffffc01078b110,1408,'614000c45215db533794a776e4c8d659c1dc677fafbcf755740fd146853a6449'),
)
VDPU_HYP_RAM=(0x80000000,0x88000000)
VDPU_PMEM=(0x8e000000,0x90000000)
VDPU_POOL_GLOBAL=0x80194de0
VDPU_LINK_DISPLAY={0:2,1:0}
VDPU_POOL_ABI=(
    ('pool_pbase',0x80088b58,8,'dfb556a413c8bda5332942d0202e8d5f1e52f45158aec3676294cf11ad5909cb'),
    ('pool_plimit',0x80088b60,8,'bd237e5d99f6462770946dd64c9d5273577f92aeb78154ba485fef49e0d73851'),
    ('_vlink_pmem_setup_func',0x8008980c,436,'f6c8d07798e747196c32424cf1c625f325d12f3a7828c28c49340d141ca66503'),
    ('_vlink_info_do_pmem_alloc',0x800899c0,596,'b77459e5fde7f5cc804dc445c6cca4293967729ce001002132951645e3fd0e60'),
)


@lru_cache(maxsize=1)
def verify_vdpu_pool_abi():
    from vlm_platform import Elf, original, parse_dtb
    e=Elf(original('hypervisor').read_bytes())
    symbols={row['name']:(row['address'],row['size']) for row in e.symbols}
    if symbols.get('nk_pmem_pool')!=(VDPU_POOL_GLOBAL,8):
        raise ValueError('Original HYP PMEM pool global changed')
    for name,addr,size,digest in VDPU_POOL_ABI:
        if symbols.get(name)!=(addr,size) or hashlib.sha256(e.at(addr,size)).hexdigest()!=digest:
            raise ValueError('Original HYP PMEM ABI changed: '+name)
    if not any(s['type']==1 and s['virtual']==s['physical'] and
               s['physical']<=VDPU_POOL_GLOBAL<=s['physical']+s['memory_size']-8 for s in e.segments):
        raise ValueError('Original HYP PMEM global does not have an identity mapping')
    hcb=original('hcb/hcb.img').read_bytes()
    start=hcb.index(b'VDTHYP\0\0')+8
    dt=parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
    hyp=struct.unpack('>QQ',dt['/vlm/hyp/hyp-memory']['reg'])
    qnx=struct.unpack_from('>Q',dt['/vlm/vm@2/vm-memory']['reg'])[0]
    if (hyp[0],sum(hyp))!=VDPU_HYP_RAM or qnx!=VDPU_PMEM[1]:
        raise ValueError('Original HCB RAM partition boundaries changed')
    for link,display in VDPU_LINK_DISPLAY.items():
        client=dt[f'/vlm/vm@3/vdevs/vdpu{link}@fe']
        server=dt[f'/vlm/vm@2/vdevs/vdpu{link}@be']
        if (client.get('compatible')!=b'vdpu\0' or server.get('compatible')!=b'vdpu\0'
                or 'client' not in client or 'server' not in server
                or client.get('peer-phandle')!=server.get('phandle')
                or server.get('info')!=str(display).encode('ascii')+b'\0'):
            raise ValueError('Original VM3/VM2 vDPU link binding changed')


def read_vdpu_pool(hyp_word):

    pointer=hyp_word(VDPU_POOL_GLOBAL)
    lo,hi=VDPU_HYP_RAM
    if not lo<=pointer<=hi-32 or pointer&7:
        raise ValueError('Original HYP PMEM allocator is not live in HYP RAM')
    bounds=hyp_word(pointer+0x10),hyp_word(pointer+0x18)
    if bounds!=VDPU_PMEM:
        raise ValueError('Original HYP PMEM allocator returned unexpected bounds')
    return bounds


def vdpu_cursor_word(address, bounds, read_word, offset=0):
                                                                            
    if address&0xfff or not bounds[0]<=address<=bounds[1]-0x1000:
        raise ValueError('Original vDPU cursor is outside its HYP PMEM page')
    if offset&7 or not 0<=offset<=288:
        raise ValueError('Original vDPU read exceeds ring metadata')
    return read_word(address+offset)


def vdpu_pending_events(read_index, write_index, word):

    if not (0<=read_index<10 and 0<=write_index<10):
        raise ValueError('Original vDPU ring cursors out of range')
    events=[]
    for i in range((write_index-read_index)%10):
        slot=(read_index+i)%10
        record=0x38+slot*24
        kind_and_pipe=word(record)
        kind,pipe=kind_and_pipe&0xffffffff,kind_and_pipe>>32
        event=dict(slot=slot,kind=kind,pipe=pipe)
        if kind==6:                                                
            event['window_status_word']=word(record+8)&0xffffffff
        events.append(event)
    return events


def inspect_vdpu(lookup, private_word, shared_word, shared_metadata=None):

    from vlm_platform import factory_file

    with factory_file('ivi/Image').open('rb') as image:
        for name,address,size,digest in VDPU_ABI:
            image.seek(address-0xffffffc010080000)
            if lookup.get(name)!=address or hashlib.sha256(image.read(size)).hexdigest()!=digest:
                raise ValueError('OEM vDPU ABI changed: '+name)
    if lookup.get('xirq_wrappers')!=0xffffffc0111ef848 or lookup.get('xirq_to_irq')!=0xffffffc0113fcca8:
        raise ValueError('OEM xIRQ globals changed')

    def u32(address):
        return (private_word(address&~7)>>(8*(address&7)))&0xffffffff

    head=lookup['xirq_wrappers']
    link=private_word(head)
    last=head
    seen=set()
    seen_links=set()
    rings=[]
    while link!=head:
        if link in seen or len(seen)>=128 or private_word(link+8)!=last:
            raise ValueError('Original xIRQ wrapper list is inconsistent or exceeds the read bound')
        seen.add(link)
        wrapper=link-0x108
        if private_word(wrapper+8)==lookup['vdpu_irq_hdl']:
            device=private_word(wrapper+0x10)
            xirq=u32(wrapper)
            if not 0<xirq<1024 or u32(device+0x38)!=xirq or private_word(device+0x40)!=wrapper:
                raise ValueError('Original vDPU IRQ wrapper does not match its owner')
            if private_word(device+0x158)!=lookup['vdpu_irqhdl_work']:
                raise ValueError('Original vDPU work item does not match its owner')
            row=dict(link_id=u32(device),display_index=u32(device+4),xirq=xirq,
                     linux_irq=u32(lookup['xirq_to_irq']+4*xirq),work_pending=bool(private_word(device+0x140)&1))
            if (row['link_id'] in seen_links or
                    VDPU_LINK_DISPLAY.get(row['link_id'])!=row['display_index']):
                raise ValueError('Original vDPU wrapper is not a VM3 display link')
            seen_links.add(row['link_id'])
            try:
                ring=private_word(device+0x68)
                cursors=shared_word(ring)
                read_index,write_index=cursors&0xffffffff,cursors>>32
                if max(read_index,write_index)>=10:
                    raise ValueError('Original vDPU ring cursors out of range')
                row.update(read_index=read_index,write_index=write_index,
                           occupancy=(write_index-read_index)%10,capacity=9)
                if shared_metadata is not None:
                    try:
                        row['pending_events']=vdpu_pending_events(
                            read_index,write_index,lambda offset:shared_metadata(ring,offset))
                    except ValueError as exc:
                        row['pending_events_unverified']=str(exc)
                                                                        
                                                                             
                    try:
                        row['vlink_state']=u32(private_word(device+0x30))
                    except ValueError as exc:
                        row['vlink_state_unverified']=str(exc)
            except ValueError as exc:
                row['ring_unverified']=str(exc)
            rings.append(row)
        last=link
        link=private_word(link)
    if private_word(head+8)!=last:
        raise ValueError('Original xIRQ wrapper list tail does not match')
    return rings


def track_display_changes(proof, previous):

    if (previous.get('frame_source')!=proof['frame_source'] or
            previous.get('capture_origin')!=proof.get('capture_origin')):
        return {}
    result={}
    for value,field in (('pixel_sha256','last_pixel_change_monotonic'),
                        ('framebuffer_id','last_buffer_switch_monotonic')):
        prior,current=previous.get(value),proof.get(value)
        if prior is None or current is None:
            continue
        if prior!=current:
            result[field]=proof['captured_monotonic']
        elif field in previous:
            result[field]=previous[field]
    return result


def sample_wod_device_state(qmp, directory):
    from decon_platform import LINUX_PATH,LINUX_PROFILE,LINUX_WODS,LINUX_WINDOWS
    from qnx_dsi_platform import DECON_PATH

    profile=json.loads((directory/'linux-decon-profile.json').read_bytes())
    expected={(base,pipe,win) for base,pipe,win,_ in LINUX_WODS.values()}
    if (profile.get('profile')!=LINUX_PROFILE or
            {(r['base'],r['decon'],r['index']) for r in profile['windows']}!=expected):
        raise ValueError('Original WOD MMIO contract does not match')
    windows=[]
    for base,pipe,win in sorted(expected):
        ix=LINUX_WINDOWS.index(base)
        fields={k:qmp.execute('qom-get',dict(path=LINUX_PATH,property=f'wincon-{k}-{ix}'))
                for k in ('requests','pending','commits')}
        windows.append(dict(decon_pipe=pipe,window=win,**fields))
    irq={pipe:{k:qmp.execute('qom-get',dict(path=DECON_PATH,property=f'{k}-{pipe}'))
               for k in ('frame-ticks','frame-start-irqs','frame-start-acks')}
         for pipe in (0,2)}
    return dict(status='observed',windows=windows,decon=irq)


def record_current(qmp, directory, symbols, *, center_only=False, ivi_snapshot=None):

    from original_8704_vm import save
    from sysmmu_platform import runtime_state
    from vlm_platform import walk_guest_4k

    before=qmp.execute('query-status')
    own_pause=bool(before.get('running'))
    borrowed_pause=(before.get('status')=='paused' and
                    getattr(qmp,'_offline_snapshot_depth',0)>0)
    if not own_pause and not borrowed_pause:
        return
    loaded=json.loads((directory/'kernel-loads.json').read_bytes())
    image=next(row for row in loaded if row['guest']=='linux')
    if image['kernel_sha256']!=LINUX_SHA or symbols['sha256']!=LINUX_SHA:
        raise ValueError('OEM Linux image and DRM symbols do not match')
    lookup={entry['name']:int(entry['address'],16) for entry in symbols['symbols']}
    table=image['physical']+lookup['swapper_pg_dir']-lookup['_text']
    tcr=int(symbols['observed_tcr_el1'],16)
    path=directory/'native-center-frame.json'
    previous=json.loads(path.read_bytes()) if path.is_file() else {}
    if not isinstance(previous,dict):
        raise ValueError('Invalid previous center frame manifest')
    raster=vdpu=gpu_snapshot=ivi_row=raster_error=None
    with TemporaryDirectory(prefix='center-plane-',dir=directory) as scratch:
        temp=Path(scratch)/'vm3-read.bin'
        stop_requested=time.monotonic()
        if own_pause:
            qmp.execute('stop')
        started=time.monotonic()
        try:
            if qmp.execute('query-status')['status']!='paused':
                raise ValueError('QMP did not stop for coherent native scanout capture')

            def bytes_at(address,length):
                if time.monotonic()-started>2.5:
                    raise ValueError('Live scanout read budget exceeded')
                qmp.execute('pmemsave',{'val':address,'size':length,'filename':str(temp)})
                return temp.read_bytes()

            physical_word,metadata_stats=vm3_metadata_reader(bytes_at)

            @lru_cache(maxsize=256)
            def read_hyp_word(address):
                if time.monotonic()-started>2.5:
                    raise ValueError('Live vDPU metadata read budget exceeded')
                result=qmp.execute('human-monitor-command',{'command-line':f'xp /1gx {address:#x}'})
                match=re.search(r':\s+0x([\da-fA-F]+)',result)
                if not match:
                    raise ValueError('Cannot read original HYP vDPU word')
                return int(match[1],16)

            def hyp_word(address):
                if address&7 or not VDPU_HYP_RAM[0]<=address<=VDPU_HYP_RAM[1]-8:
                    raise ValueError('Original HYP PMEM metadata is outside HYP RAM')
                return read_hyp_word(address)

            def translate(address):
                if address&7 or not 0xffffff8000000000<=address<=0xfffffffffffffff8:
                    raise ValueError('Invalid original VM3 kernel pointer')
                translated=walk_guest_4k(address,tcr,table,physical_word)
                return int(translated['physical_target'],16)

            def guest_word(address):
                return physical_word(translate(address))

            mmu=runtime_state(qmp,tuple(int(x,16) for x in SYSMMU))
            proof=inspect(symbols,guest_word,directory,mmu)
                                                                        
                                                                          
            center=next((r for r in proof.get('connector_observations',())
                         if r['name']=='Virtual-1'),{})
            flip=center.get('flip_completion',{})
            armed=bool(flip.get('vblank_enabled') and flip.get('armed_vblank_sequences'))
            if (not center_only and (proof.get('crtc_active') or armed)
                    and (armed or time.monotonic()-started<1.25)):
                try:
                    verify_vdpu_pool_abi()
                    bounds=read_vdpu_pool(hyp_word)
                    def queue_word(address,offset=0):
                        return vdpu_cursor_word(translate(address),bounds,read_hyp_word,offset)
                    rings=inspect_vdpu(lookup,guest_word,queue_word,queue_word)
                    vdpu=dict(rings=rings,status='original_ring_cursors_sampled' if any('occupancy' in r for r in rings)
                              else 'cursors_not_verified')
                except (OSError,RuntimeError,ValueError) as exc:
                    vdpu=dict(status='cursors_not_verified',reason=str(exc))
                vdpu.update(captured_monotonic=time.monotonic(),flip_completion=flip,
                            wod_gate=center.get('wod_gate'),crtc_active=bool(proof.get('crtc_active')))
            if (not center_only and (armed or time.monotonic()-started<1.25) and
                    any(r.get('wod_gate',{}).get('status')=='observed'
                        for r in proof.get('connector_observations',[]))):
                try:
                    proof['wod_device_state']=sample_wod_device_state(qmp,directory)
                except (KeyError,OSError,RuntimeError,ValueError) as exc:
                    proof['wod_device_state']=dict(status='unverified',reason=str(exc))
            if proof['status'] in ('active_scanout_pages_verified','active_gem_pages_verified'):
                try:
                    raster=read_active_raster(proof,bytes_at)
                except (OSError,RuntimeError,ValueError) as exc:
                                                                            
                                                                         
                    raster_error=exc
                if raster is not None and time.monotonic()-started<2:
                    try:
                        gpu_snapshot=qmp.execute('qom-get',dict(path='/machine/exynos-mali-g3d1',
                                                                property='vm3-center-target-history'))
                    except (RuntimeError,OSError):
                        gpu_snapshot=None
            proof['captured_monotonic']=time.monotonic()
            if ivi_snapshot is not None:
                deadline=min(time.monotonic()+.55,started+1.45)
                try:
                    ivi_row=ivi_snapshot(qmp,lookup,tcr,translate,physical_word,deadline)
                except (KeyError,OSError,RuntimeError,ValueError) as exc:
                    ivi_row=dict(status='not_verified',reason=str(exc))
                ivi_row['capture_monotonic']=time.monotonic()
        finally:
            if own_pause and qmp.execute('query-status')['status']=='paused':
                qmp.execute('cont')
        timing=dict(qmp_stop_to_resume_seconds=time.monotonic()-stop_requested,
                    pause_owner='scanout' if own_pause else 'outer-offline-observer',
                    vm3_metadata_pages=metadata_stats().misses,vm3_metadata_cache_hits=metadata_stats().hits)

                                                                        
    if vdpu is not None:
        vdpu['wod_device_state']=proof.get('wod_device_state')
        save(directory/'native-vdpu-ring.json',vdpu)
        if armed:
            with (directory/'native-vdpu-armed-flip.jsonl').open('a',encoding='utf-8') as log:
                log.write(json.dumps(vdpu,separators=(',',':'))+'\n')
    if raster_error is not None:
        raise raster_error
    if ivi_row is not None:
        ivi_row['stop_to_resume_seconds']=timing['qmp_stop_to_resume_seconds']
        with (directory/'native-ivi16-timeline.jsonl').open('a',encoding='utf8') as log:
            log.write(json.dumps(ivi_row,separators=(',',':'))+'\n')
    proof['capture_timing']=timing
    if raster is not None:
        try:
            evidence=correlate_center_target(proof,raster,gpu_snapshot) if gpu_snapshot is not None else dict(
                status='gpu_history_unavailable',active_footprint_sha256=center_active_footprint(proof))
        except (KeyError,TypeError,ValueError) as exc:
            evidence=dict(status='not_verified',reason=str(exc))
        evidence['capture_monotonic']=proof['captured_monotonic']
        save(directory/'native-center-gpu-correlation.json',evidence)
        if gpu_snapshot is not None:
                                                                              
            with (directory/'native-center-gpu-history.jsonl').open('a',encoding='utf-8') as log:
                log.write(json.dumps(evidence,separators=(',',':'))+'\n')
        try:
            png,pixels=render_active_raster(proof,raster)
        except ValueError:
            profile=summarize_premultiplied_raster(proof,raster)
            if profile is not None:
                save(directory/'native-center-raster-profile.json',profile)
                retain_nonzero_foreground(directory,proof,raster,profile,evidence,save)
                if current:=publish_current_foreground(directory,proof,raster,profile):
                    temp=path.with_suffix('.json.part')
                    save(temp,current)
                    temp.replace(path)
                    return
            raise
        proof.update(pixels)
        if pixels['nonzero']:
            frame=directory/('native-center-'+pixels['png_sha256']+'.png')
            if not frame.exists():
                pending=frame.with_suffix('.part')
                pending.write_bytes(png)
                pending.replace(frame)
            proof.update(png=frame.name,frame_source='original_vm3_drm_primary')
            proof.update(track_display_changes(proof,previous))
            retain_distinct_scanout(directory,proof,png,evidence,save)
    save(path,proof)
    old=previous.get('png')
    if isinstance(old,str) and old!=proof.get('png') and re.fullmatch(r'native-center-[a-f0-9]{64}\.png',old):
        (directory/old).unlink(missing_ok=True)
