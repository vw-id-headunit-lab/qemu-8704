

ABI=(
    ('kipc_write',0xffffffc0105836a0,0x148,'b757695298becd2a4a5eb3cfef68460e38193aef0c5ae0c6af8b65e056fe535d'),
    ('kipc_ioctl',0xffffffc0105833c0,0x98,'e15261a8b51933b7ebe8236693421af6c9f93138f3f56f75a9bbf45b394a0770'),
    ('get_kipc_id_info',0xffffffc010584698,0xc0,'da8fb38ca483d8cdbbee60148af45f4746e1a3225579abda507292af9a35297b'),
    ('pop_queue',0xffffffc0105848e0,0x80,'b134f242ce1fe80fe8bda51858495dab395ca6af49cd2635d5c889ef573eb0fb'),
)
OPS=0xffffffc0111afb08
LEGACY=0xffffffc01129ec10
PROCESSES=('JAVA','persistence-app','mcu-interpreter')


def queues(va,read):
    if va.get('kipc_property_legacy')!=LEGACY:
        raise ValueError('Original KIPC property table address differs')
    rows={}
                                                                       
                                                                           
    for endpoint in (15,52):
        a=LEGACY+0x38*endpoint
        header=read(a+0x28)
        row=rows[str(endpoint)]=dict(registered_id=(header>>48)&0xffff,
                                     pending=(header>>32)&0xffff)
        if row['registered_id']!=endpoint or row['pending']>4096:
            raise ValueError('Original KIPC queue ID or count differs')
        if (read(a)==a)!=(row['pending']==0):
            raise ValueError('Original KIPC count does not match list emptiness')
    return rows


def inspect(tasks,va,read,ptr,text):
    if va.get('kipc_ops')!=OPS:
        raise ValueError('Original KIPC operations address differs')
    result={name:[] for name in PROCESSES}
    for row in tasks:
        comm=row['comm']
        if comm not in result:
            continue
        proc=int(row['task'],16)
        table=ptr(ptr(proc+0x708)+0x20)
        count=read(table)&0xffffffff
        if not 1<=count<=4096:
            raise ValueError('Original KIPC process FD table exceeds bound')
        fds=ptr(table+8)
        matches=[]
        for fd in range(min(count,256)):
            slot=fds+8*fd
            if not read(slot):
                continue
            file=ptr(slot)
            if read(file+0x28)!=OPS:
                continue
            d,inode=ptr(file+0x18),ptr(file+0x20)
            if (read(inode)&0xf000!=0x2000 or ptr(d+0x30)!=inode or
                    read(d+0x20)>>32!=4 or text(ptr(d+0x28),4)!='kipc'):
                raise ValueError('Original KIPC FD does not identify the KIPC char inode')
            dev=read(inode+0x48)>>32
            matches.append(dict(fd=fd,major=dev>>20,minor=dev&0xfffff))
        result[comm].append(dict(pid=row['pid'],fd_table_entries=count,
                                 complete=count<=256,kipc_fds=matches))
    out=dict(status='observed',scope='Only original process FD tables and two endpoint queue counters',processes=result)
    try:
        out['endpoint_queues']=queues(va,read)
    except (OSError,ValueError) as exc:
        out['endpoint_queues']=dict(status='not_verified',reason=str(exc))
    return out
