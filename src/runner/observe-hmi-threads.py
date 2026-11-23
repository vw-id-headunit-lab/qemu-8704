

ABI=(
    ('collect_syscall',0xffffffc0103d7c58,320,'1b96f9cbbc84397e91b0b927c1f7a1ad3755c8655fccb088a2d7e38a7bfdfbe1'),
    ('proc_task_readdir',0xffffffc010291858,848,'9faa7898f5b989ac938baedf950fab80251d84222d0e7a9eea74a45863936326'),
    ('show_map_vma',0xffffffc010289dd8,0x190,'cdad2b94ce647e58b97ed5f8fc5a79b06d470bd45063fc4fba3836455b6e8fb9'),
    ('m_next',0xffffffc010289f78,0x70,'07d75e7f6dbb94d84cc545b2a5c8b551e618d69f8bdf930279ed8c4a233ba653'),
    ('mm_init.isra.0',0xffffffc0100a4fe0,0xd0,'688a537c47cd5b99eaa6ce7951c55189ce0106809e9ed3869e6e87e1b03164c6'),
)
WATCH=('hmi','render','draw','opengl','egl','surface','graphic','wayland','display','mali-')
UI_THREADS={'ui-thread','main_thread','startup-thread'}


def inspect(tasks, symbols, va, read, ptr, string, links, verify, capture_stack,
            read_user=None, inspect_queues=None, *, weston=False):
    process='weston' if weston else 'JAVA'
    if weston and inspect_queues is not None:
        raise ValueError('OEM Java queue inspection does not apply to Weston')
    leaders=[t for t in tasks if t['comm']==process and t['has_mm']]
    if len(leaders)!=1:
        return dict(status='not_observed',reason=f'No unique original {process} process')
    verify(va,ABI)
    leader=int(leaders[0]['task'],16)
    mm=ptr(leader+0x458)
    if ptr(leader+0x548)!=leader:
        raise ValueError(f'Original {process} is not a thread group leader')
    nodes=[leader]+[entry-0x5b8 for entry in links(leader+0x5b8,31 if weston else 191)]
    threads=[]
    by_pc={}
    for task in nodes:
        if ptr(task+0x548)!=leader or ptr(task+0x458)!=mm:
            raise ValueError(f'Original {process} thread owner or mm differs')
        pid=ptr(task+0x570)
        if ptr(pid+0x58)!=va['init_pid_ns']:
            raise ValueError(f'Original {process} thread outside the initial PID namespace')
        state=read(task+0x20)
        name=string(task+0x6c0,16)
        row=dict(tid=read(pid+0x50)&0xffffffff,comm=name,state=hex(state))
        threads.append(row)
        if not weston and task!=leader and name.lower() not in UI_THREADS and not any(s in name.lower() for s in WATCH):
            continue
        if state not in (1,2):
            continue
        stack=ptr(task+0x28)
        if stack&0x3fff or read(stack+0x3fc8)&0x1f:
            continue
        pc=read(stack+0x3fc0)
        if not 0x1000<=pc<1<<39 or pc&3:
            continue
        nr=read(stack+0x3fd8)&0xffffffff
        row['user_syscall']=None if nr==0xffffffff else nr
        by_pc.setdefault(pc,[]).append(row)
        try:
            snapshot=capture_stack(symbols,task,read)
            row['kernel_frames']=[f['symbol'] for f in snapshot['frames'][:16]]
        except (KeyError,ValueError):
            row['kernel_frames_unverified']=True
        if read_user is not None and (weston or name.lower() in UI_THREADS):
            try:
                pgd=ptr(mm+0x40)
                sp=read(stack+0x3fb8)
                fp=read(stack+0x3fa8)
                frames=[]
                row['user_return_frames']=frames
                for _ in range(8):
                    if not (0x1000<=sp<=fp<=sp+0x40000 and fp+16<=1<<39) or fp&15:
                        break
                    previous=read_user(pgd,fp)
                    ret=read_user(pgd,fp+8)
                    if not 0x1000<=ret<1<<39 or ret&3:
                        break
                    frame={}
                    frames.append(frame)
                    by_pc.setdefault(ret,[]).append(frame)
                    if previous<=fp:
                        break
                    fp=previous
            except (OSError,ValueError):
                row['user_return_read_incomplete']=True
    maps=[]
    if by_pc or inspect_queues:
        count=read(mm+0x60)&0xffffffff
        if not 1<=count<=2048:
            raise ValueError('Native JVM mmap count is out of scope')
        cur=ptr(mm)
        prev=end=0
        for _ in range(count):
            if read(cur+0x40)!=mm or read(cur+0x18)!=prev:
                raise ValueError('Original JVM VMA links or owner changed')
            start,stop=read(cur),read(cur+8)
            if not end<=start<stop<=1<<39:
                raise ValueError('Invalid original JVM VMA range')
            end=stop
            hits=[pc for pc in by_pc if start<=pc<stop]
            if hits or inspect_queues:
                module=None
                pages=read(cur+0x98)
                if read(cur+0xa0):
                    dentry=ptr(ptr(cur+0xa0)+0x18)
                    length=read(dentry+0x20)>>32
                    if 1<=length<=128 and (hits or length==len('libjvm.so') and pages==0):
                        module=string(ptr(dentry+0x28),length)
                if inspect_queues:
                    maps.append((start,stop,read(cur+0x50),(module,pages) if module else None))
                for pc in hits:
                    for row in by_pc.pop(pc):
                        mapping=dict(module=module,offset=hex(pc-start))
                        if module:
                            if pages<1<<32:
                                mapping['file_offset']=hex((pages<<12)+pc-start)
                        row['user_mapping']=mapping
            if not by_pc and not inspect_queues:
                break
            prev,cur=cur,read(cur+0x10)
            if not cur:
                break
    result=dict(status='observed',pid=leaders[0]['pid'],threads=threads,
                scope=('Original stopped Weston; bounded thread stacks, no arguments or heap dump' if weston else
                       'Original stopped JVM; selected thread stacks and opt-in OEM queue counters, no arguments or heap dump'))
    if inspect_queues:
        try:
            if read_user is None or len(maps)!=count or cur:
                raise ValueError('Original JVM mapping inventory did not close')
            result['event_queues']=inspect_queues(ptr(mm+0x40),maps,threads,read_user)
        except (OSError,ValueError) as exc:
            result['event_queues']=dict(status='not_verified',reason=str(exc))
    return result
