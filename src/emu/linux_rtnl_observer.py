
from bisect import bisect_right

from linux_pid1_context import LINUX_SHA, capture_task_stack


def capture(symbols, read_u64, cpus):
    if symbols['sha256'] != LINUX_SHA:
        raise ValueError('RTNL owner layout requires the exact OEM kernel')
    rows = symbols['symbols']
    addresses = {r['name']: int(r['address'], 16) for r in rows}

    def read(address):
        if not 0xffffff8000000000 <= address <= 0xfffffffffffffff8 or address % 8:
            raise ValueError(f'Invalid original Linux RTNL pointer {address:#x}')
        return read_u64(address)

    mutex = addresses['rtnl_mutex']
                                                                        
                                                                      
    raw = read(mutex)
    owner = raw & ~7
    result = dict(mutex=hex(mutex), owner_flags=raw & 7, source='paused original Linux')
    if not owner:
        return dict(result, status='no_owner_at_capture')
                                                                        
    raw_comm = b''.join(read(owner + off).to_bytes(8, 'little') for off in (0x6c0, 0x6c8))
    name = raw_comm.split(bytes(1), 1)[0].decode('ascii', 'backslashreplace')
    pid = read(owner + 0x570)
    if not pid or read(pid + 0x58) != addresses['init_pid_ns']:
        raise ValueError('Original RTNL owner does not have an init namespace PID')
    result.update(status='verified', owner=hex(owner), owner_comm=name,
                  owner_pid=read(pid + 0x50) & 0xffffffff,
                  saved=capture_task_stack(symbols, owner, read))
    starts = sorted((int(r['address'], 16), r['name']) for r in rows
                    if r.get('type') in ('t', 'T') and not r['name'].startswith('$'))
    pcs = [a for a, _ in starts]
    matches = []
    for cpu in cpus:
        system, core = cpu.get('system', {}), cpu.get('core', {})
        if (int(system.get('VTTBR_EL2', '0'), 16) >> 48 != 3 or
                int(system.get('SP_EL0', '0'), 16) != owner or
                int(core.get('cpsr', '0'), 16) & 15 not in (4, 5)):
            continue
        pc = int(core['pc'], 16)
        candidate = dict(thread=cpu['thread'], pc=hex(pc), lr=core['x30'])
        if addresses['_text'] <= pc < addresses['_etext']:
            start, name = starts[bisect_right(pcs, pc) - 1]
            candidate.update(symbol=name, offset=pc - start)
        matches.append(candidate)
    return dict(result, running_owner=matches)
