
import re


ABI = (
    ('get_task_mm', 0xffffffc0100a5720, 0x68, '48d9cfe91a8f92d7d53f917bf021db7b8228cd9c5ab78b5814e80b33aff9bbf7'),
    ('proc_pid_cmdline_read', 0xffffffc01028ea48, 0x100, '1e0b0fdae9dd03c34ae8a051a76a9a17806c100a85892762313cd51310c524be'),
    ('show_map_vma', 0xffffffc010289dd8, 0x190, 'cdad2b94ce647e58b97ed5f8fc5a79b06d470bd45063fc4fba3836455b6e8fb9'),
)
SCRIPT = 'post_init_main.'
TOOLS = {'chmod','chown','cp','tar','rm','mkdir','touch','cat','tee','sync','readlink','find','stat'}
ARG = re.compile(rb'(?:[-A-Za-z0-9_:.,+*=]+|/(?:data|nav|navi_data|lge|vw|tmp|sys|proc|bin|usr/bin|dev)(?:/[A-Za-z0-9_./+*-]*)?)')


def read_argv(task, read, ptr, read_user):
    mm = ptr(int(task['task'],16)+0x458)
    pgd = ptr(mm+0x40)
    first, end, env = (read(mm+off) for off in (0x128,0x130,0x138))
    if not (0x1000 <= first < end <= env < 1<<48 and end-first <= 1024):
        raise ValueError('Argument interval missing, unbounded, or overlaps environment')
    aligned = first&~7
                                                                                  
    raw = b''.join(read_user(pgd,i).to_bytes(8,'little') for i in range(aligned,end,8))[first-aligned:end-aligned]
    if not raw.endswith(b'\0'):
        raise ValueError('Argument interval lacks its terminal NUL')
    fields = raw[:-1].split(b'\0')
    if not 1 <= len(fields) <= 32 or not fields[0] or len(fields[0]) > 96:
        raise ValueError('Original command has an unexpected argv shape')
                                                                          
                                                                             
    return [field.decode('ascii') if len(field)<=200 and ARG.fullmatch(field) else '[redacted]'
            for field in fields]


def inspect(tasks, read, ptr, read_user):
    heads = [row['pid'] for row in tasks if row['comm']==SCRIPT and row['ppid']==1]
    if len(heads) != 1:
        return dict(status='post_init_root_not_observed',children=[])
    by_parent = {}
    for row in tasks:
        by_parent.setdefault(row['ppid'],[]).append(row)
    todo = [(heads[0],0)]
    seen = {heads[0]}
    children = []
    while todo:
        parent, depth = todo.pop()
        if depth >= 6:
            continue
        for row in by_parent.get(parent,[]):
            if row['pid'] in seen or len(seen)>=24:
                raise ValueError('Original post-init tree exceeds witness bound')
            seen.add(row['pid'])
            info = {key:row[key] for key in ('pid','ppid','comm','state')}
            if row['comm'] in TOOLS and row['has_mm']:
                try:
                    info['argv'] = read_argv(row, read, ptr, read_user)
                except (ValueError, OSError, KeyError, RuntimeError) as exc:
                    info['argv_unverified'] = str(exc)
            children.append(info)
            todo.append((row['pid'],depth+1))
    return dict(status='post_init_subtree_observed',pid=heads[0],children=children)
