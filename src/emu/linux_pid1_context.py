
from bisect import bisect_right

LINUX_SHA = 'c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109'
STACK_BYTES = 0x4000


def without_oem_autoreboot(bootargs):

    import re

                                                                           
                                                                               
    words = bootargs.split()
    if 'root=/dev/dm-0' not in words or 'init=/sbin/init' not in words:
        raise ValueError('OEM autoreboot policy requires the original IVI Linux bootargs')
    if [w for w in words if w.split('=', 1)[0] == 'auto_reboot'] != ['auto_reboot=1']:
        raise ValueError('Expected one original auto_reboot=1 setting')
    return re.sub(r'(?<!\S)auto_reboot=1(?!\S)', 'auto_reboot=0', bootargs, count=1)


def capture(symbols, read_u64, *, read_user_u64=None):

    if symbols['sha256'] != LINUX_SHA:
        raise ValueError('PID 1 context ABI is for the verified original 8704 Linux Image only')
    rows = symbols['symbols']
    addresses = {row['name']: int(row['address'], 16) for row in rows}

    def read(addr):
        if not 0xffffff8000000000 <= addr <= 0xfffffffffffffff8 or addr % 8:
            raise ValueError(f'invalid kernel pointer: {addr:#x}')
        return read_u64(addr)

                                                                      
                                                                          
    head = addresses['init_task'] + 0x528
    entry = read(head)
    seen = set()
    for _ in range(64):
        if entry == head:
            raise ValueError('PID 1 is not on original init_task children list')
        if entry in seen:
            raise ValueError('original task list is cyclic')
        seen.add(entry)
        task = entry - 0x538
        pid = read(task + 0x570)
        if pid and read(pid + 0x50) & 0xffffffff == 1 and read(pid + 0x58) == addresses['init_pid_ns']:
            break
        entry = read(entry)
    else:
        raise ValueError('original task list exceeded the capture bound')

    return dict(capture_task_stack(symbols, task, read), pid=1)


def capture_task_stack(symbols, task, read_u64):
    if symbols['sha256'] != LINUX_SHA:
        raise ValueError('Task context ABI is only for original 8704 Linux Image')
    addresses = {row['name']: int(row['address'], 16) for row in symbols['symbols']}
    rows = symbols['symbols']

    def read(address):
        if not 0xffffff8000000000 <= address <= 0xfffffffffffffff8 or address % 8:
            raise ValueError(f'Invalid OEM kernel task/stack pointer: {address:#x}')
        return read_u64(address)

    state = read(task + 0x20)
    result = {'task': hex(task), 'state': hex(state), 'kernel_sha256': LINUX_SHA,
              'scope': 'Read-only stopped VM snapshot; saved sleeping context, not current vCPU backtrace'}
    if state == 0:
        return dict(result, status='runnable_or_running', frames=[])
                                                                       
                                                                    
    base = read(task + 0x28)
    fp, sp, pc = (read(task + offset) for offset in (0xa60, 0xa68, 0xa70))
    if not 0xffffff8000000000 <= base <= 0xffffffffffffbfff or base % 4096:
        raise ValueError(f'invalid original task stack base: {base:#x}')
    if not base <= sp < base + STACK_BYTES or sp % 16:
        raise ValueError('saved SP is not on original task stack')
    text = {int(row['address'], 16): row['name'] for row in rows
            if row.get('type') in ('t', 'T') and not row['name'].startswith('$')}
    starts = sorted(text)
    ranges = [(addresses['_text'], addresses['_etext']),
              (addresses['_sinittext'], addresses['_einittext'])]
    frames = []
    status = 'frame_limit'
    for depth in range(24):
        if pc == 0 and fp == 0:
            status = 'end_of_chain'
            break
        callsite = pc if depth == 0 else pc - 4
        if not any(a <= callsite < b for a, b in ranges) or callsite % 4:
            status = 'non_kernel_return_address'
            break
        start = starts[bisect_right(starts, callsite) - 1]
        frames.append({'pc': hex(pc), 'fp': hex(fp), 'symbol': text[start], 'offset': callsite - start})
        if fp == 0:
            status = 'end_of_chain'
            break
        if not sp <= fp <= base + STACK_BYTES - 16 or fp % 16:
            status = 'frame_outside_stack'
            break
        next_fp, pc = read(fp), read(fp + 8)
        if next_fp and next_fp <= fp:
            status = 'non_monotonic_frame'
            break
        fp = next_fp
    return dict(result, status=status, stack_base=hex(base), saved_sp=hex(sp), frames=frames)
