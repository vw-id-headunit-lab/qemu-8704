
import hashlib
import json
from pathlib import Path
from types import FunctionType
from bisect import bisect_right

import android_task_context as original

EXTRA_WATCH = frozenset({'main', 'zygote', 'zygote64', 'system_server', 'surfaceflinger',
                         'dex2oat', 'dex2oat64', 'keystore', 'keymaster@4.0_t',
                         'composer@2.4-se', 'app_process64', 'app_process32'})


def thread_groups(symbols, leaders, read):






    addresses = {x['name']: int(x['address'], 16) for x in symbols['symbols']}
    text = {int(x['address'], 16): x['name'] for x in symbols['symbols']
            if x.get('type') in ('t', 'T')}
    starts = sorted(text)
    ranges = [(addresses['_text'], addresses['_etext']),
              (addresses['_sinittext'], addresses['_einittext'])]
    result = []
    for leader in leaders:
        if leader.get('exe_basename') not in ('app_process64', 'app_process32', 'surfaceflinger', 'keystore'):
            continue
        task = int(leader['task'], 16)
        group = {'pid': leader['pid'], 'exe_basename': leader['exe_basename'], 'threads': []}
        result.append(group)
        head = task + 0x638
        node = head
        seen = set()
        try:
            mm = read(task + 0x4d8)
            while True:
                if node in seen or len(seen) >= 128:
                    raise ValueError('Cyclic/oversized original thread group')
                seen.add(node)
                member = node - 0x638
                nxt, prev = read(node), read(node + 8)
                if read(nxt + 8) != node or read(prev) != node:
                    raise ValueError('Inconsistent original thread links')
                if read(member + 0x5c8) != task or read(member + 0x4d8) != mm:
                    raise ValueError('Original thread group owner/mm differs')
                pid = read(member + 0x5f0)
                if not pid or read(pid + 0x68) != addresses['init_pid_ns']:
                    raise ValueError('Original thread PID namespace differs')
                tid = read(pid + 0x60) & 0xffffffff
                comm = b''.join(read(member + off).to_bytes(8, 'little') for off in (0x750, 0x758))
                state = read(member + 0x28)
                row = {'tid': tid, 'comm': comm.partition(b'\0')[0].decode('ascii', 'backslashreplace'),
                       'task': hex(member), 'state': hex(state), 'frames': []}
                group['threads'].append(row)
                if state:
                    base = read(member + 0x30)
                    fp, sp, pc = [read(member + off) for off in (0xba0, 0xba8, 0xbb0)]
                    if (base % 4096 or not 0xffffff8000000000 <= base or
                            not base <= sp < base + original.STACK_BYTES or sp % 16):
                        raise ValueError('Original saved thread stack invalid')
                    for depth in range(24):
                        callsite = pc - (4 if depth else 0)
                        if callsite % 4 or not any(a <= callsite < b for a, b in ranges):
                            break
                        anchor = starts[bisect_right(starts, callsite) - 1]
                        row['frames'].append({'pc': hex(pc), 'symbol': text[anchor], 'offset': callsite-anchor})
                        if not fp or fp % 16 or not sp <= fp <= base + original.STACK_BYTES - 16:
                            break
                        new_fp, pc = read(fp), read(fp + 8)
                        if new_fp and new_fp <= fp:
                            break
                        fp = new_fp
                                                                             
                                                                           
                    pstate = read(base + 0x3fc8)
                    if pstate & 0x1f not in (0, 0x10):
                        raise ValueError('Saved context is not original EL0')
                    nr = read(base + 0x3fd8) & 0xffffffff
                    row['saved_syscall'] = {'number': nr if nr < 0x80000000 else nr-(1<<32),
                        'args': [hex(read(base + 0x3fd0))] + [hex(read(base + 0x3ec0 + 8*i)) for i in range(1, 6)],
                        'pc': hex(read(base + 0x3fc0)), 'pstate': hex(pstate),
                        'compat32': pstate & 0x1f == 0x10,
                        'scope': 'Saved EL0 syscall context of sleeping original thread, not a user stack unwind'}
                else:
                    row['status'] = 'runnable_or_running; no saved syscall claim'
                node = nxt
                if node == head:
                    break
        except ValueError as error:
            group['error'] = str(error)
    return result


def capture(symbols, read_u64, output):
    if symbols.get('sha256') != original.ANDROID_SHA:
        raise ValueError('Unverified original Android layout')
    assert original.capture.__closure__ is None
    words = {}

    def read(address):
        if not 0xffffff8000000000 <= address <= 0xfffffffffffffff8 or address % 8:
            raise ValueError('Invalid bounded original kernel word address')
        if address not in words:
            if len(words) >= 32768:
                raise ValueError('Bounded offline stack capture exceeded')
            try:
                value = read_u64(address)
                if not isinstance(value, int) or not 0 <= value < 1 << 64:
                    raise ValueError('Invalid original RAM word')
                words[address] = {'value': hex(value)}
            except ValueError as error:
                words[address] = {'error': str(error)}
        row = words[address]
        if 'error' in row:
            raise ValueError(row['error'])
        return int(row['value'], 16)

                                                                            
    namespace = dict(original.capture.__globals__)
    namespace['WATCH'] = original.WATCH | EXTRA_WATCH
    collect = FunctionType(original.capture.__code__, namespace,
                           original.capture.__name__, original.capture.__defaults__)
    tasks = collect(symbols, read)
    tasks['thread_groups'] = thread_groups(symbols, tasks['tasks'], read)
    replay = {'kernel_sha256': original.ANDROID_SHA,
              'scope': 'Read-only original stopped VM4 RAM words; no guest writes',
              'original_capture_sha256': hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest(),
              'symbols': symbols,
              'words': {hex(address): row for address, row in words.items()}}
    path = Path(output) / 'android-wait-replay.json'
    raw = (json.dumps(replay, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    path.write_bytes(raw)
    tasks['offline_wait_capture'] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                                     'words': len(words), 'additional_names': sorted(EXTRA_WATCH),
                                     'guest_memory_modified': False}
    return tasks
