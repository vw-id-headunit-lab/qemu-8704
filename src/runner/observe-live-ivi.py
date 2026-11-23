
from functools import lru_cache
from pathlib import Path
import runpy
import time


SCENE = runpy.run_path(str(Path(__file__).with_name('observe-original-weston-scene.py')))
GPU_PATH = '/machine/exynos-mali-g3d1'
KERNEL_MIN = 0xffffff8000000000
KERNEL_MAX = 0xfffffffffffffff8


def find_weston(read, ptr, name, init_task, init_pid_ns):

    head = init_task + 0x528
    entry, previous = ptr(head), head
    matches, seen = [], set()
    while entry != head:
        if entry in seen or len(seen) >= 128 or ptr(entry+8) != previous:
            raise ValueError('Original PID 1 child list is inconsistent or too long')
        seen.add(entry)
        task = entry-0x538
        if name(task+0x6c0, 16) == 'weston' and read(task+0x458):
            pid_obj = ptr(task+0x570)
            pid = read(pid_obj+0x50) & 0xffffffff
            if ptr(pid_obj+0x58) == init_pid_ns and 1 < pid < 1 << 22:
                matches.append(dict(pid=pid, comm='weston', task=hex(task)))
        previous, entry = entry, ptr(entry)
    if ptr(head+8) != previous:
        raise ValueError('Original PID 1 child list tail disagrees')
    return matches


def ivisurface_state(scene):
    if scene.get('status') != 'observed':
        return scene
    target = next((r for r in scene['surfaces'] if r['ivi_id'] == 16), None)
    if target is None:
        stage = 'no_ivi16'
    elif target['current_buffer_reference'] and target['width'] and target['height']:
        stage = 'sized_current'
    elif target['current_buffer_reference']:
        stage = 'unsized_current'
    elif target['pending_attachment'] and target['pending_buffer_reference']:
        stage = 'pending_buffer'
    elif target['pending_attachment']:
        stage = 'pending_null_attachment'
    else:
        stage = 'no_current_or_attachment'
    return dict(status='observed', stage=stage, target=target,
                weston_pid=scene.get('weston_pid'),
                wayland_server_debug=scene.get('wayland_server_debug'))


def capture(qmp, lookup, tcr, translate, physical_word, deadline):
    from vlm_platform import walk_user_4k

    def check_budget():
        if time.monotonic() >= deadline:
            raise ValueError('IVI lifecycle sample time budget exceeded')

    def physical(address):
        check_budget()
        return physical_word(address)

    @lru_cache(maxsize=4096)
    def read(address):
        check_budget()
        return physical(translate(address))

    def ptr(address):
        value = read(address)
        if value & 7 or not KERNEL_MIN <= value <= KERNEL_MAX:
            raise ValueError('Unverified IVI sample kernel pointer')
        return value

    def span(address, size):
        if not 0 < size <= 256:
            raise ValueError('Unbounded IVI sample metadata span')
        start = address & ~7
        raw = b''.join(read(at).to_bytes(8, 'little') for at in range(start,address+size,8))
        return raw[address-start:address-start+size]

    def name(address, size):
        return span(address, size).split(b'\0', 1)[0].decode('ascii', 'backslashreplace')

    @lru_cache(maxsize=256)
    def user_page(root, page):
        return walk_user_4k(page, tcr, translate(root), physical)

    def user_word(root, address):
        if (root & 4095) or (address & 7):
            raise ValueError('Unaligned IVI userspace sample')
        return physical(user_page(root,address & ~4095) + (address & 4095))

    def verify_kernel_abi():
        from hashlib import sha256
        for symbol, site, size, digest in SCENE['ABI']:
            if lookup.get(symbol)!=site or sha256(span(site,size)).hexdigest()!=digest:
                raise ValueError('IVI lifecycle kernel ABI pin changed')

    verify_kernel_abi()
                                                                          
    init_task = lookup['init_task']
    pid1_head = init_task+0x528
    entry, previous = ptr(pid1_head), pid1_head
    seen = set()
    while entry != pid1_head and len(seen) < 64:
        if entry in seen or ptr(entry+8) != previous:
            raise ValueError('Original PID 1 sibling list is inconsistent')
        seen.add(entry)
        task = entry-0x538
        pid_obj = ptr(task+0x570)
        if read(pid_obj+0x50) & 0xffffffff == 1 and ptr(pid_obj+0x58)==lookup['init_pid_ns']:
            break
        previous, entry = entry, ptr(entry)
    else:
        return dict(status='not_observed',reason='Original PID 1 not linked')
    tasks=find_weston(read,ptr,name,task,lookup['init_pid_ns'])
    row=ivisurface_state(SCENE['inspect'](tasks,read,ptr,name,user_word,light=True))
    if row['status']=='observed' and time.monotonic()+.035 < deadline:
        for key in ('job-starts','vm3-color-fans'):
            if time.monotonic()+.035 >= deadline:
                break
            try:
                value=qmp.execute('qom-get',dict(path=GPU_PATH,property=key))
            except (OSError,RuntimeError,ValueError):
                break
            if type(value) is int and value >= 0:
                row.setdefault('g3d1',{})[key]=value
    return row
