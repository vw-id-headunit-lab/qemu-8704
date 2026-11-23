
from collections import defaultdict
import re

WAYLAND = '/run/platform/weston/wayland-0'
TRACE = '/run/log/trace_unix_socket'
NOTIFY = '/run/systemd/notify'
PROCESSES = {'weston', 'JAVA', 'displaymgr', 'vtee', 'coredumpsensor', 'trace-daemon', 'trace-system-se'}
ABI = (
    ('sock_from_file',0xffffffc0108241e8,0x28,'716bbb070cdce740e309358d9bf50789d0d6fc53873effdb914961c3a21c5939'),
    ('sock_poll',0xffffffc010824740,0x28,'4abd08e653cee563c91a93449e1eccdd3170514c700e9e9924408e2f72b6eef1'),
    ('unix_peer_get',0xffffffc0109903d0,0x48,'7240f48ebe4a51ab804173da097662a64f60a20d36477d65dd815510ee13b9fd'),
    ('unix_getname',0xffffffc010991550,0xb8,'dd5c722323e0a62cecc4d10bc71b6edab4e4f4749391972ab65ca6925e5e7d83'),
    ('unix_seq_show',0xffffffc010990418,0x110,'f486c2feb50b4089043b16991fdc35d9cf4cdde14b63201c0fcb8ebaf91fdd53'),
    ('sock_init_data_uid',0xffffffc01082bc58,0xc0,'ef00c9e74212f0df1deb36174397f5eba2cd0532cdb0e61f7057df720fb352ca'),
    ('show_map_vma',0xffffffc010289dd8,0x190,'cdad2b94ce647e58b97ed5f8fc5a79b06d470bd45063fc4fba3836455b6e8fb9'),
    ('m_start',0xffffffc01028ba90,0x180,'b43d1e520a394c48964acc58c33d03d0a46f1a716d9f8dc4d0ee9dcbc19ff4cf'),
    ('m_next',0xffffffc010289f78,0x70,'07d75e7f6dbb94d84cc545b2a5c8b551e618d69f8bdf930279ed8c4a233ba653'),
    ('auxv_read',0xffffffc01028d8a8,0x68,'aeaea026ebbbc626fb36db99cdbd8b92f20db991518f278c03b0651fc92865de'),
)
GRAPHICS = re.compile(r'^(?:lib(?:EGL|GLES|mali|wayland|gbm|drm|OpenGL|GL|ImageLoader|Nme|jvm|jsig)[\w.-]*\.so(?:\.[\d.]+)?|classes\.jza)$',re.I)


def original_auxv(mm, read):

    fields={11:'uid',12:'euid',13:'gid',14:'egid',23:'at_secure'}
    result={}
    seen=set()
    for offset in range(0,48*16,16):
        tag=read(mm+0x148+offset)
        if not tag:
            if set(result)==set(fields.values()) and result['at_secure'] in (0,1):
                return dict(result,status='observed')
            break
        if tag>0xffff or tag in seen:
            break
        seen.add(tag)
        if tag in fields:
            result[fields[tag]]=read(mm+0x150+offset)
    return dict(status='not_verified')


def mapped_graphics(tasks, read, ptr, text):

    rows=[]
    for task in (t for t in tasks if t['comm'] in ('weston','JAVA','displaymgr')):
        info={k:task[k] for k in ('pid','comm')}
        try:
            mm=ptr(int(task['task'],16)+0x458)
            if task['comm']=='JAVA':
                info['original_auxv']=original_auxv(mm,read)
            count=read(mm+0x60)&0xffffffff
            if not 1<=count<=2048:
                raise ValueError('Native mmap count is out of scope')
            cur=read(mm)
            end=0
            prev=0
            matched=set()
            for _ in range(count):
                if (cur<0xffffff8000000000 or cur&7 or
                        read(cur+0x40)!=mm or read(cur+0x18)!=prev):
                    raise ValueError('Invalid native VMA owner or links')
                first,last=read(cur),read(cur+8)
                if first<end or last<=first or (first|last)&0xfff or last>1<<48:
                    raise ValueError('Unexpected native userspace map bounds')
                end=last
                if read(cur+0xa0):
                    file=ptr(cur+0xa0)
                    d=ptr(file+0x18)
                    length=read(d+0x20)>>32
                    if 1<=length<=128:
                        name=text(ptr(d+0x28),length)
                        if GRAPHICS.fullmatch(name):
                            matched.add(name)
                prev,cur=cur,read(cur+0x10)
            if cur:
                raise ValueError('Native mmap count did not cover the complete list')
            rows.append(dict(info,status='observed',map_count=count,libraries=sorted(matched)))
        except (OSError,ValueError) as error:
            rows.append(dict(info,status='not_verified',reason=str(error)))
    return rows


def inspect(tasks, va, read, ptr, text, weston_log_inode):





    if (va['socket_file_ops'], va['unix_stream_ops'], va['unix_dgram_ops']) != (
            0xffffffc010b62380, 0xffffffc010b72de8, 0xffffffc010b72ed0):
        raise ValueError('Unexpected native AF_UNIX operations table')
    sock_ops = va['socket_file_ops']
    by_sk = defaultdict(list)
    inspected, errors = [], []
    selected = [t for t in tasks if t['comm'] in PROCESSES]
    weston_stderr = None

    def endpoint(sk):
        a = read(sk+0x2d8)
        if not a:
            return None
        a = ptr(sk+0x2d8)
        n = read(a)>>32
        if not 2<=n<=110 or (read(a+8)>>32)&0xffff != 1:
            raise ValueError('Unexpected native UNIX address size or family')
        if n==2 or (read(a+8)>>48)&0xff == 0:
            return None
        name = text(a+14,n-2)
        return name if name in (WAYLAND,TRACE,NOTIFY) else None

    for t in selected:
        who = {'pid':t['pid'],'comm':t['comm']}
        try:
            proc = int(t['task'],16)
            table = ptr(ptr(proc+0x708)+0x20)
            slots = read(table)&0xffffffff
            if not 3<=slots<=65536:
                raise ValueError('Unexpected OEM FD table size')
            limit = min(slots,256)
            fds = ptr(table+8)
            meta = dict(who,slots=slots,slots_inspected=limit,complete=slots==limit)
            inspected.append(meta)
            if t['comm']=='weston' and weston_stderr is None:
                stderr = read(fds+16)
                inode = ptr(ptr(fds+16)+0x20) if stderr else None
                weston_stderr = dict(pid=t['pid'],fd_open=bool(stderr),
                                     matches_original_log=inode==weston_log_inode if inode and weston_log_inode else None)
            for fd in range(limit):
                entry = fds+8*fd
                if not read(entry):
                    continue
                file = ptr(entry)
                if read(file+0x28)!=sock_ops:
                    continue
                sock = ptr(file+0xc8)
                ops = read(sock+0x20)
                if ops not in (va['unix_stream_ops'],va['unix_dgram_ops']):
                    continue
                sk = ptr(sock+0x18)
                if read(sk+0x270)!=sock:
                    raise ValueError('Socket back pointer does not identify the FD owner')
                peer = read(sk+0x330)
                if peer:
                    peer = ptr(sk+0x330)
                by_sk[sk].append(dict(who,fd=fd,peer=peer,local_path=endpoint(sk),
                                     transport='stream' if ops==va['unix_stream_ops'] else 'datagram'))
        except (OSError,ValueError) as error:
            errors.append(dict(who,reason=str(error)))

    def public(row):
        return {k:row[k] for k in ('pid','comm','fd','local_path','transport')}

    pairs, bound, unattached, unattached_errors = [], [], [], []
    for sk, lefts in by_sk.items():
        for left in lefts:
            if left['local_path']:
                bound.append(public(left))
            peer = left['peer']
            if left['comm']=='JAVA' and left['transport']=='stream' and peer and peer not in by_sk:
                try:
                    if read(peer+0x330)==sk and endpoint(peer)==WAYLAND and not read(peer+0x270):
                                                                         
                                                                            
                        unattached.append(dict(client=public(left),peer_path=WAYLAND,
                                               reciprocal=True,peer_has_socket_owner=False))
                except (OSError,ValueError):
                    unattached_errors.append(left['fd'])
            if not peer or sk>=peer:
                continue
            for right in by_sk.get(peer,[]):
                if right['peer']==sk and (left['local_path'] or right['local_path']):
                    pairs.append({'one':public(left),'other':public(right),'reciprocal':True})
    java_to_weston = any(
        {p['one']['comm'],p['other']['comm']}=={'JAVA','weston'} and
        WAYLAND in (p['one']['local_path'],p['other']['local_path']) for p in pairs)
    return dict(status='observed' if inspected else 'not_observed',
                scope='Stopped native VM3 AF_UNIX FD and reciprocal peer identity only; not past activity or a frame',
                complete=bool(inspected) and all(r['complete'] for r in inspected) and not errors,
                processes=inspected, errors=errors, weston_stderr=weston_stderr, bound=bound,
                reciprocal_pairs=pairs, java_to_original_wayland_observed=java_to_weston,
                java_wayland_peers_without_socket_owner=unattached,
                java_unattached_peer_unverified_fds=unattached_errors,
                mapped_graphics=mapped_graphics(tasks,read,ptr,text))
