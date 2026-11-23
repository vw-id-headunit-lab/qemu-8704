
import ctypes as ct
from ctypes import wintypes as wt
import struct
import subprocess
import sys


def parse_cpu_sets(raw):
    rows=[]
    seen=set()
    offset=0
    while offset<len(raw):
        if len(raw)-offset<8:
            raise ValueError('Truncated Windows CPU Set record')
        size,kind=struct.unpack_from('<II',raw,offset)
        if not 8<=size<=len(raw)-offset:
            raise ValueError('Invalid Windows CPU Set record length')
        if kind==0:                     
            if size<20: raise ValueError('Short Windows CpuSetInformation record')
            ident,group,index,core,_cache,_numa,eff,flags=struct.unpack_from('<IH6B',raw,offset+8)
            if ident in seen or index>=64: raise ValueError('Invalid Windows CPU Set identity')
            seen.add(ident)
            rows.append(dict(id=ident,group=group,index=index,core=core,efficiency=eff,
                             parked=bool(flags&1),allocated=bool(flags&2),owned=bool(flags&4)))
        offset+=size
    return rows


def select_fast(rows,affinity):
    if any(row['group']!=0 for row in rows):
        raise ValueError('Fast CPU Sets currently require a single Windows processor group')
    eligible=[r for r in rows if affinity&(1<<r['index']) and (not r['allocated'] or r['owned'])]
    classes={r['efficiency'] for r in eligible}
    if len(classes)<2:
        raise ValueError('No distinct eligible Windows performance CPU Sets')
    best=[r for r in eligible if r['efficiency']==max(classes)]
    if not any(not r['parked'] for r in best):
        raise ValueError('No active Windows performance CPU Set')
    return sorted(best,key=lambda r:r['id'])


def apply_to_child(child):
    if sys.platform!='win32' or not isinstance(child,subprocess.Popen) or child.poll() is not None:
        raise ValueError('Fast CPU Sets require the live Windows native AP child')
    k=ct.WinDLL('kernel32',use_last_error=True)
    def api(name,args,result):
        fn=getattr(k,name)
        fn.argtypes=args
        fn.restype=result
        return fn
    ul=wt.ULONG
    ptr_ul=ct.POINTER(ul)
    handle_t=wt.HANDLE
    open_process=api('OpenProcess',[wt.DWORD,wt.BOOL,wt.DWORD],handle_t)
    close=api('CloseHandle',[handle_t],wt.BOOL)
    sets=api('GetSystemCpuSetInformation',[ct.c_void_p,ul,ptr_ul,handle_t,ul],wt.BOOL)
    affinity=api('GetProcessAffinityMask',[handle_t,ct.POINTER(ct.c_size_t),ct.POINTER(ct.c_size_t)],wt.BOOL)
    set_default=api('SetProcessDefaultCpuSets',[handle_t,ptr_ul,ul],wt.BOOL)
    get_default=api('GetProcessDefaultCpuSets',[handle_t,ptr_ul,ul,ptr_ul],wt.BOOL)
    h=open_process(0x1000|0x2000,False,child.pid)                                 
    if not h: raise ct.WinError(ct.get_last_error())
    try:
        required=ul()
        sets(None,0,ct.byref(required),h,0)
        if not 0<required.value<=65536: raise ValueError('Unbounded Windows CPU Set inventory')
        raw=ct.create_string_buffer(required.value)
        if not sets(raw,len(raw),ct.byref(required),h,0): raise ct.WinError(ct.get_last_error())
        mask,system=ct.c_size_t(),ct.c_size_t()
        if not affinity(h,ct.byref(mask),ct.byref(system)): raise ct.WinError(ct.get_last_error())
        chosen=select_fast(parse_cpu_sets(raw.raw[:required.value]),mask.value)
        ids=[r['id'] for r in chosen]
        selected=(ul*len(ids))(*ids)
        if not set_default(h,selected,len(ids)): raise ct.WinError(ct.get_last_error())
        returned=(ul*len(ids))()
        actual=ul()
        if not get_default(h,returned,len(ids),ct.byref(actual)): raise ct.WinError(ct.get_last_error())
        if actual.value!=len(ids) or sorted(returned)!=ids:
            raise ValueError('Windows AP CPU Set preference did not read back')
        return dict(status='windows_fast_cpu_sets_applied',pid=child.pid,sets=chosen,host_affinity_mask=hex(mask.value),
                    scope='Process default soft preference; no guest clock, host global setting or speed claim')
    finally:
        close(h)


def linux_seed_cpu_pools(rows, affinity):

    fast = select_fast(rows, affinity)
    cores = {}
    for row in sorted(fast, key=lambda item: (item['parked'], item['index'])):
        cores.setdefault(row['core'], row['id'])
    slow = sorted(r['id'] for r in rows if r['efficiency'] < fast[0]['efficiency']
                  and affinity & (1 << r['index']) and (not r['allocated'] or r['owned']))
    if len(cores) < 3 or len(slow) < 5:
        raise ValueError('Linux seed CPU layout requires three fast physical cores and five other logical CPUs')
    linux = dict(zip((1, 2, 5), list(cores.values())[:3]))
    return {i: [linux[i]] if i in linux else slow for i in range(8)}


def qnx_display_cpu_pools(rows, affinity, qnx_workers):

    if tuple(qnx_workers) != (0, 4):
        raise ValueError('QNX display placement needs the original AP0/AP4 affinity')
    fast = select_fast(rows, affinity)
    active = sorted((r for r in fast if not r['parked']), key=lambda r: r['index'])
    cores = {}
    for row in active:
        cores.setdefault(row['core'], row['id'])
    if len(cores) < 3:
        raise ValueError('QNX display placement needs two separate active fast cores and one remaining')
    isolated = dict(list(cores.items())[:2])
    rest = sorted(r['id'] for r in rows if r['core'] not in isolated and
                  affinity & (1 << r['index']) and (not r['allocated'] or r['owned']))
    if len(rest) < 6:
        raise ValueError('QNX display placement needs six other logical CPUs')
    dedicated = dict(zip(qnx_workers, isolated.values()))
    return {i: [dedicated[i]] if i in dedicated else rest for i in range(8)}


def prefer_ap_threads(child, threads, *, linux_seed_split=False, qnx_workers=()):

    if sys.platform != 'win32' or not isinstance(child, subprocess.Popen) or child.poll() is not None:
        raise ValueError('AP CPU Sets require a live Windows QEMU child')
    workers = {r['cpu']: r['host_thread'] for r in threads if r['role'] == 'ap'}
    if set(workers) != set(range(8)) or len(set(workers.values())) != 8:
        raise ValueError('Expected distinct QMP identities for all eight AP threads')
    if linux_seed_split and qnx_workers:
        raise ValueError('Select only one per-domain CPU arrangement')
    k = ct.WinDLL('kernel32', use_last_error=True)

    def api(name, args, result):
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, result
        return fn

    h, u, d = wt.HANDLE, wt.ULONG, wt.DWORD
    pu, ps = ct.POINTER(u), ct.POINTER(ct.c_size_t)
    close = api('CloseHandle', [h], wt.BOOL)
    process = api('OpenProcess', [d, wt.BOOL, d], h)
    thread = api('OpenThread', [d, wt.BOOL, d], h)
    owner = api('GetProcessIdOfThread', [h], d)
    inventory = api('GetSystemCpuSetInformation', [ct.c_void_p, u, pu, h, u], wt.BOOL)
    affinity = api('GetProcessAffinityMask', [h, ps, ps], wt.BOOL)
    get_sets = api('GetThreadSelectedCpuSets', [h, pu, u, pu], wt.BOOL)
    set_sets = api('SetThreadSelectedCpuSets', [h, pu, u], wt.BOOL)
    proc = process(0x1000, False, child.pid)
    if not proc:
        raise ct.WinError(ct.get_last_error())
    handles, assigned = [], []
    try:
        n = u()
        inventory(None, 0, ct.byref(n), proc, 0)
        if not 0 < n.value <= 65536:
            raise ValueError('Unbounded Windows CPU Set inventory')
        buf = ct.create_string_buffer(n.value)
        mask, system = ct.c_size_t(), ct.c_size_t()
        if not inventory(buf, len(buf), ct.byref(n), proc, 0) or not affinity(proc, ct.byref(mask), ct.byref(system)):
            raise ct.WinError(ct.get_last_error())
                                                                           
                                                                            
                                                                        
        rows = parse_cpu_sets(buf.raw[:n.value])
        pool = select_fast(rows, mask.value)
        if not (linux_seed_split or qnx_workers) and len(pool) < len(workers):
            raise ValueError('Fast CPU Sets must not oversubscribe the AP worker count')
        if linux_seed_split:
            pools = linux_seed_cpu_pools(rows, mask.value)
            mode = 'windows_linux_seed_split_cpu_sets_applied'
        elif qnx_workers:
            pools = qnx_display_cpu_pools(rows, mask.value, qnx_workers)
            mode = 'windows_qnx_display_split_cpu_sets_applied'
        else:
            pools = {i: [r['id'] for r in pool] for i in workers}
            mode = 'windows_fast_ap_thread_cpu_sets_applied'
        pool = [r for r in sorted(rows, key=lambda row: row['id'])
                if any(r['id'] in ids for ids in pools.values())]
        for cpu, tid in sorted(workers.items()):
            t = thread(0x0800 | 0x0400, False, tid)
            if not t:
                raise ct.WinError(ct.get_last_error())
            handles.append((cpu, tid, t))
            if owner(t) != child.pid:
                raise ValueError('QMP thread is not owned by the launched QEMU child')
            if not get_sets(t, None, 0, ct.byref(n)) or n.value:
                raise ValueError('AP thread already has an explicit CPU Set assignment')
        for cpu, _, t in handles:
            ids = pools[cpu]
            wanted = (u * len(ids))(*ids)
            if not set_sets(t, wanted, len(ids)):
                raise ct.WinError(ct.get_last_error())
            assigned.append(t)
            actual = (u * len(ids))()
            if not get_sets(t, actual, len(actual), ct.byref(n)):
                raise ct.WinError(ct.get_last_error())
            if n.value != len(ids) or sorted(actual) != ids:
                raise ValueError('AP CPU Set assignment did not read back')
        return dict(status=mode, pid=child.pid, sets=pool,
                    threads=[dict(cpu=cpu, host_thread=tid, selected=pools[cpu]) for cpu, tid, _ in handles],
                    scope='Only native AP threads; no affinity changes to QEMU I/O, DSP, ACPM or other processes')
    except BaseException:
        for t in assigned:
            set_sets(t, None, 0)
        raise
    finally:
        for _, _, t in handles:
            close(t)
        close(proc)
