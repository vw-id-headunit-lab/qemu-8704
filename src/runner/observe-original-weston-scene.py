
from functools import lru_cache

ABI=(
    ('mm_init.isra.0',0xffffffc0100a4fe0,0xd0,
     '688a537c47cd5b99eaa6ce7951c55189ce0106809e9ed3869e6e87e1b03164c6'),
)
                                                                             
                                                                
MODULES={
    'weston': ((0xf258,'004840f91f0000f1e0079f1ac0035fd6'),
               (0xf580,'8062049177f4ff9720008052f71b40f9'),
               (0x11078,'22eeff97010080d28002039151f9ff97')),
    'ivi-shell.so': ((0x4968,'fd7bbca91f0000f1241840fafd030091'),
                     (0x62a0,'fd7bbda9fd030091f35301a9200d00b4'),
                     (0x6478,'fd7bbaa9fd030091f55b02a9b50000f0'),
                     (0x7870,'fd7bbba9fd030091f35301a9f30303aa')),
    'libwayland-server.so.0.1.0': ((0x8100,'610000b4048840b9240000b9620000b4'),
                                  (0x81c8,'002040f9c0035fd6012400f9c0035fd6')),
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def user_pointer(address):
    need(0x1000<=address<1<<39 and not address&7,'Invalid original compositor user pointer')
    return address


def wl_list(head, word, max_nodes=64):
    user_pointer(head)
    prev=head
    cur=word(head+8)
    seen=set()
    while cur!=head:
        user_pointer(cur)
        need(cur not in seen and len(seen)<max_nodes and word(cur)==prev,
             'Original compositor list has broken or excessive links')
        seen.add(cur)
        yield cur
        prev,cur=cur,word(cur+8)
    need(word(head)==prev,'Original compositor list tail disagrees')


MALI_PINS=((0x51bdec,'9357002997630129e3031a2a213042f9'),
           (0x51be28,'a123019021200e91f8d1fc97e1031caa'),
           (0x51c8b8,'822301f0fe1f00f9e00314aa42200e91'),
           (0x51ca40,'c00a8652df02006b41ffff54400840b9'),
           (0x51ca54,'400440b9f1ffff1717008052d7ffff17'),
           (0x51cc48,'859a4029070080d2840e40b903008052'),
           (0x51c580,'f37bbfa993d5fc97f30300aa000040b9'),
           (0x51c590,'ecd1fc97e00313aaf37bc1a8e5d2fc17'))


def buffer_identity(buffer, client, weston, wayland, word, dword, map_owner, *, mali=None, backing=None, pixel_backing=None):

    buffer=user_pointer(buffer)
    resource=word(buffer)
    if not resource:
        return dict(status='not_observed',reason='Current buffer resource was released')
    user_pointer(resource)
    need(word(resource)==wayland+0x24620,'Current resource is not the pinned wl_buffer class')
    need(word(resource+0x40)==client,'Current buffer resource has a different client')
    listener=buffer+0x18
    need(word(buffer+0x28)==weston+0xc1e0,'Weston buffer destroy listener is not pinned')
    prev,nxt=user_pointer(word(listener)),user_pointer(word(listener+8))
    need(prev!=listener and nxt!=listener and word(prev+8)==listener and word(nxt)==listener,
         'Weston buffer destroy listener is not linked')
    object_id=dword(resource+0x10)
    need(object_id>=2,'Invalid wl_buffer object ID')
    implementation=word(resource+8)
    identity=dict(status='observed',object_id=object_id)
    if implementation==weston+0x3bc78:
        data=user_pointer(word(resource+0x48))
        need(word(data)==resource and word(data+8)==0,'Original DMA-BUF user data has no matching owner')
        identity['type']='linux_dmabuf'
    elif implementation==wayland+0x23bb0:
        need(word(resource+0x48)!=0,'Original SHM buffer has no storage object')
        identity['type']='wl_shm'
    else:
        identity['type']='unclassified_wl_buffer'
                                                                   
                                                                        
    if implementation:
        owner=map_owner(user_pointer(implementation))
        identity['implementation_owner']=owner
        if (mali is not None and implementation==mali+0x298f388 and
                owner==dict(kind='file',module='libmali.so.0.31.0',file_offset=0x297f388)):
            data=user_pointer(word(resource+0x48))
            width,height=dword(data+4),dword(data+8)
            need(0<width<=16384 and 0<height<=16384,'Invalid original Mali Wayland buffer dimensions')
            identity.update(type='mali_egl_wayland',egl_width=width,egl_height=height,
                            layout_word=dword(data+12),format_token=dword(data+16))
            if backing is not None:
                fd=dword(data)
                try:
                    need(fd<65536,'Original Mali resource FD is outside the capture bound')
                    if pixel_backing is not None:
                        identity['layout_key']=word(data+0x18)
                        layout=(width,height,identity['layout_word'],identity['format_token'],identity['layout_key'])
                        identity['backing']=pixel_backing(fd,layout)
                    else:
                        identity['backing']=backing(fd)
                except (KeyError,OSError,ValueError):
                    identity['backing']=dict(status='not_verified',reason='Original FD backing could not be bound')
    return identity


def scene(base, wayland_base, word, dword, tasks, *, identify=None):

    layout=base+0x1d2a0
    compositor=user_pointer(word(layout))
    display=user_pointer(word(compositor+16))
    by_pid={r['pid']:r['comm'] for r in tasks}
    surfaces={}
    ids=set()
    for entry in wl_list(layout+8,word):
        need(word(entry+40)==layout,'IVI surface has a different layout owner')
        key=dword(entry+36)
        need(key not in ids,'Duplicate original IVI surface ID')
        ids.add(key)
        ws=user_pointer(word(entry+56))
        need(word(ws+0x18)==compositor,'Weston surface has a different compositor')
        width,height=dword(ws+0x68),dword(ws+0x6c)
        need(width<=16384 and height<=16384,'Invalid original surface dimensions')
        vis=dword(entry+208)&0xff
        need(vis in (0,1),'Invalid original surface visibility')
        owner=None
        surface_object_id=None
        client=None
        resource=word(ws)
        if resource:
            need(word(user_pointer(resource))==wayland_base+0x240d0 and word(resource+0x48)==ws,
                 'Original wl_surface resource does not point back to the scene surface')
            client=user_pointer(word(resource+0x40))
            pid=dword(client+0x88)
            need(0<pid<1<<22,'Invalid original Wayland client PID')
            owner=dict(pid=pid,comm=by_pid.get(pid))
            surface_object_id=dword(resource+0x10)
            need(surface_object_id>=2,'Invalid original wl_surface object ID')
        buffer=word(ws+0xc0)
        if buffer:
            user_pointer(buffer)
        attached=dword(ws+0x118)
        need(attached in (0,1),'Invalid original pending attachment flag')
        pending_buffer=word(ws+0x120)
        if pending_buffer:
            user_pointer(pending_buffer)
        output=word(ws+0x90)
        if output:
            user_pointer(output)
        surfaces[entry]=dict(ivi_id=key,client=owner,surface_object_id=surface_object_id,
                             visible=bool(vis),width=width,height=height,
                             current_buffer_reference=bool(buffer),weston_mapped=bool(output),
                             pending_attachment=bool(attached),pending_buffer_reference=bool(pending_buffer),
                             mapped_output=output,layers=[])
        if identify is not None and key in (16,36,108) and buffer and client:
            try:
                surfaces[entry]['buffer_identity']=identify(buffer,client,key==16 and owner['comm']=='JAVA')
            except (KeyError,OSError,ValueError) as exc:
                surfaces[entry]['buffer_identity']=dict(status='not_verified',reason=str(exc))

    layers={}
    layer_ids=set()
    for layer in wl_list(layout+0x18,word):
        lid=dword(layer+64)
        need(lid not in layer_ids,'Duplicate original IVI layer ID')
        layer_ids.add(lid)
        ids_in_order=[]
        for link in wl_list(layer+288,word):
            surface=surfaces.get(link-320)
            need(surface is not None,'Original IVI layer links to an unknown surface')
            surface['layers'].append(lid)
            ids_in_order.append(surface['ivi_id'])
        layers[layer]=dict(ivi_id=lid,visible=bool(dword(layer+120)),surface_list_order=ids_in_order)

    screens=[]
    outputs={}
    for scr in wl_list(layout+0x28,word,max_nodes=16):
        need(word(scr+40)==layout,'IVI screen has a different layout owner')
        sid=dword(scr+32)
        out=user_pointer(word(scr+48))
        need(out not in outputs and sid not in outputs.values(),'Duplicate original output identity')
        outputs[out]=sid
        ordered=[]
        for link in wl_list(scr+96,word):
            layer=layers.get(link-304)
            need(layer is not None,'Original screen links to an unknown IVI layer')
            ordered.append(layer['ivi_id'])
        screens.append(dict(screen_id=sid,layer_list_order=ordered))
    need(screens,'Original IVI shell has no initialized screen')
    for s in surfaces.values():
        output=s.pop('mapped_output')
        tag_surface_output(s,outputs.get(output) if output else None,screens)
    return dict(status='observed',surface_count=len(surfaces),surfaces=list(surfaces.values()),
                layers=list(layers.values()),screens=screens,
                scope='Stopped original scene objects only. List order and current buffer references are not an HMI-ready or pixel claim.')


def tag_surface_output(surface, mapped_screen, screens):

    ordered=[scr['screen_id'] for scr in screens
             if any(layer in surface['layers'] for layer in scr['layer_list_order'])]
    surface['screen_id']=mapped_screen                                     
    surface['render_order_screen_ids']=ordered
    if mapped_screen is None or not ordered:
        state='not_established'
    else:
        state='agrees' if mapped_screen in ordered else 'differs'
    surface['mapped_output_vs_render_order']=state


DEBUG_PINS = (
    (0x8390,'f30b00f99bfbff97200100b4210000d0'),
    (0x83b0,'e00000b021008052013403b9001c80d2'),
    (0x7510,'e00000d0c4ae47f9f76303a9003443b9'),
    (0x7538,'780840f98004003515630191e00315aa'),
)


def protocol_state(wayland, span, dword):
    if not all(span(wayland+offset,16)==bytes.fromhex(pin) for offset,pin in DEBUG_PINS):
        return dict(status='not_verified',reason='Original server debug ABI changed')
    flag=dword(wayland+0x25334)
    if flag not in (0,1):
        return dict(status='not_verified',reason='Unexpected original server debug flag')
    return dict(status='observed',enabled=bool(flag))


def inspect(tasks, read, ptr, text, read_user, *, file_backing=None, light=False):
    matches=[t for t in tasks if t['comm']=='weston']
    if len(matches)!=1:
        return dict(status='not_observed',reason='No unique original Weston process')
    task=matches[0]
    mm=ptr(int(task['task'],16)+0x458)
    pgd=ptr(mm+0x40)
    reads=0

    @lru_cache(maxsize=4096)
    def word(address):
        nonlocal reads
        user_pointer(address)
        reads+=1
        need(reads<=4096,'Original scene read budget exhausted')
        return read_user(pgd,address)

    def byte_span(address,size):
        first=address&~7
        data=b''.join(word(at).to_bytes(8,'little') for at in range(first,address+size,8))
        return data[address-first:address-first+size]

    def dword(address):
        need(not address&3,'Unaligned original compositor word')
        return (word(address&~7)>>(32 if address&4 else 0))&0xffffffff

    found={}
    mali_bases=[]
    vmas=[]
    count=read(mm+0x60)&0xffffffff
    need(0<count<=2048,'Unexpected original Weston VMA count')
    cur=read(mm)
    previous=0
    end=0
    for _ in range(count):
        need(cur>=0xffffff8000000000 and not cur&7 and read(cur+0x40)==mm and read(cur+0x18)==previous,
             'Unverified original Weston mapping chain')
        lo,hi=read(cur),read(cur+8)
        need(lo>=end and hi>lo and not (lo|hi)&0xfff and hi<=1<<39,'Unexpected original Weston mapping range')
        end=hi
        vmas.append((lo,hi,cur))
        if read(cur+0xa0) and not read(cur+0x98):
            file=ptr(cur+0xa0)
            d=ptr(file+0x18)
            n=read(d+0x20)>>32
            if 0<n<=128:
                name=text(ptr(d+0x28),n)
                if name=='libmali.so.0.31.0':
                    mali_bases.append((lo,hi))
                if name in MODULES and all(lo+off+16<=hi and byte_span(lo+off,16)==bytes.fromhex(signature)
                                          for off,signature in MODULES[name]):
                    need(name not in found,'Ambiguous original Weston executable mapping')
                    found[name]=lo
        previous,cur=cur,read(cur+0x10)
    need(not cur and set(found)==set(MODULES),'Missing or changed original compositor modules')
    weston,wayland=found['weston'],found['libwayland-server.so.0.1.0']
    pins=((weston+0xc1e0,'fd7bbda9fd030091f35301a9146000d1'),
          (weston+0xf504,'158400f8c6ecff97741600f920008052'),
          (weston+0x1f3cc,'218447f942a0319142400091f30b00f9'),
          (wayland+0x8168,'001040b9c0035fd600800091c0035fd6'),
          (wayland+0x8210,'600640f91f0014ebe0179f1af35341a9'),
          (wayland+0xa38c,'21a847f942402e9142800091f30b00f9'))

    def map_owner(address):
        for lo,hi,vma in vmas:
            if lo<=address<hi:
                if not read(vma+0xa0):
                    return dict(kind='anonymous')
                file=ptr(vma+0xa0)
                entry=ptr(file+0x18)
                length=read(entry+0x20)>>32
                need(0<length<=128,'Unexpected implementation module basename length')
                name=text(ptr(entry+0x28),length)
                return dict(kind='file',module=name,file_offset=(read(vma+0x98)<<12)+address-lo)
        return dict(kind='unmapped')

    @lru_cache(maxsize=1)
    def check_buffer_abi():
        return all(byte_span(at,16)==bytes.fromhex(expected) for at,expected in pins)

    @lru_cache(maxsize=1)
    def mali_abi():
        if len(mali_bases)!=1:
            return None
        lo,hi=mali_bases[0]
        return lo if all(lo+off+16<=hi and byte_span(lo+off,16)==bytes.fromhex(pin)
                         for off,pin in MALI_PINS) else None

    def identify(buffer,client,java_main):
        need(check_buffer_abi(),'Original Weston buffer ABI bytes differ')
        backing=(lambda fd: file_backing(task,fd)) if file_backing is not None else None
        pixel_backing=(lambda fd,layout: file_backing(task,fd,layout=layout)) if backing and java_main else None
        return buffer_identity(buffer,client,weston,wayland,word,dword,map_owner,
                               mali=mali_abi(),backing=backing,pixel_backing=pixel_backing)

    result=scene(found['ivi-shell.so'],wayland,word,dword,tasks,
                 identify=None if light else identify)
    try:
        result['wayland_server_debug']=protocol_state(wayland,byte_span,dword)
    except (OSError,ValueError) as exc:
        result['wayland_server_debug']=dict(status='not_verified',reason=str(exc))
    result.update(weston_pid=task['pid'],bound_mapped_text=sorted(found),metadata_words_read=reads)
    return result
