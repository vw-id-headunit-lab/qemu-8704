
from bisect import bisect_right
from collections import deque

ANDROID_SHA = '471ce1178f76ae9b4dfba3f87110bfef4f31e432574dafda7f89330f87ca3a29'
WATCH = {'init', 'mke2fs', 'wait_for_keymas', 'keymaster@4.0-s', 'android.hardwar',
         'vold', 'fsck.ext4', 'tee-supplicant', 'mcDriverDaemon'}
STACK_BYTES = 0x4000


def capture(symbols, read_u64):

    if symbols.get('sha256') != ANDROID_SHA:
        raise ValueError('Unverified Android task layout')
    rows = symbols['symbols']
    addresses = {x['name']: int(x['address'],16) for x in rows}
    text = {int(x['address'],16):x['name'] for x in rows if x.get('type') in ('t','T')}
    starts = sorted(text)
    ranges = [(addresses['_text'],addresses['_etext']), (addresses['_sinittext'],addresses['_einittext'])]
    init = addresses['init_task']
    pid_ns = addresses['init_pid_ns']

    def read(a):
        if not 0xffffff8000000000 <= a <= 0xfffffffffffffff8 or a%8:
            raise ValueError(f'invalid original Android kernel pointer {a:#x}')
        return read_u64(a)

    def exe_basename(task):
                                                             
                                                                       
        mm = read(task+0x4d8)
        exe = read(mm+0x380) if mm else 0
        dent = read(exe+0x18) if exe else 0
        if not dent:
            return None
        length = read(dent+0x20) >> 32
        addr = read(dent+0x28)
        if not 1 <= length <= 255 or not 0xffffff8000000000 <= addr <= 0xffffffffffffff00:
            raise ValueError('Original executable dentry has invalid qstr')
        start = addr & ~7
        end = (addr + length + 8) & ~7
        raw = b''.join(read(a).to_bytes(8,'little') for a in range(start,end,8))
        at = addr-start
        name = raw[at:at+length]
        if raw[at+length:at+length+1]!=b'\0' or any(ch<0x21 or ch>0x7e or ch==47 for ch in name):
            raise ValueError('Original executable qstr was not a complete basename')
        return name.decode('ascii')

    def unwind(task):
        state = read(task+0x28)
        result = {'state':hex(state),'frames':[]}
        if state == 0:
            return dict(result, status='runnable_or_running')
        base = read(task+0x30)
        fp,sp,pc = [read(task+off) for off in (0xba0,0xba8,0xbb0)]
        if base%4096 or not 0xffffff8000000000 <= base or not base <= sp < base+STACK_BYTES or sp%16:
            raise ValueError('original saved SP is outside aligned task stack')
        result.update(stack_base=hex(base),saved_sp=hex(sp))
        for i in range(24):
            if fp == pc == 0:
                return dict(result,status='end_of_chain')
            callsite = pc - (4 if i else 0)
            if callsite%4 or not any(a <= callsite < b for a,b in ranges):
                return dict(result,status='non_kernel_return_address',terminal_pc=hex(pc))
            idx = bisect_right(starts,callsite)-1
            if idx<0:
                return dict(result,status='unresolved_kernel_address',terminal_pc=hex(pc))
            anchor = starts[idx]
            result['frames'].append({'pc':hex(pc),'fp':hex(fp),'symbol':text[anchor],'offset':callsite-anchor})
            if fp == 0:
                return dict(result,status='end_of_chain')
            if fp%16 or not sp <= fp <= base+STACK_BYTES-16:
                return dict(result,status='frame_outside_stack')
            nxt,pc = read(fp),read(fp+8)
            if nxt and nxt<=fp:
                return dict(result,status='non_monotonic_frame')
            fp=nxt
        return dict(result,status='frame_limit')

                                                                    
                                                                                    
    q = deque([(init,0)])
    seen = {init}
    tasks = []
    issues = []
    while q:
        parent, ppid = q.popleft()
        head = parent+0x5a8
        try:
            entry = read(head)
            while entry != head:
                task = entry-0x5b8
                if len(seen)>=512 or task in seen:
                    raise ValueError('cyclic or oversized child list')
                seen.add(task)
                pid = read(task+0x5f0)
                n = (read(pid+0x60)&0xffffffff) if pid and read(pid+0x68)==pid_ns else None
                raw = b''.join(read(task+off).to_bytes(8,'little') for off in (0x750,0x758))
                comm = raw.partition(b'\0')[0].decode('ascii',errors='backslashreplace')
                row = {'pid':n,'ppid':ppid,'comm':comm,'task':hex(task)}
                if comm in WATCH or n==1:
                    try:
                        exe=exe_basename(task)
                        if exe:
                            row['exe_basename']=exe
                    except ValueError as error:
                        row['exe_unverified']=str(error)
                    try:
                        row.update(unwind(task))
                    except ValueError as error:
                        row.update(status='unverified_stack',error=str(error))
                tasks.append(row)
                if n is not None and n != 2:                                                             
                    q.append((task,n))
                entry = read(entry)
        except ValueError as error:
            issues.append({'parent':hex(parent),'error':str(error)})
    return {'kernel_sha256':ANDROID_SHA,'scope':'Stopped original VM, read only; runnable state has no claimed current backtrace',
            'tasks':tasks,'issues':issues}
