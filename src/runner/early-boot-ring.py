
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time

import original_8704_vm as vm
from vlm_platform import walk_guest_4k


IDENTITIES = {
    'linux': (3, 0xb8080000, 'c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109',
              ((0xb8000000, 0xd0000000), (0xa80000000, 0xb00000000))),
    'android': (4, 0xa0080000, '471ce1178f76ae9b4dfba3f87110bfef4f31e432574dafda7f89330f87ca3a29',
                ((0xa0000000, 0xb8000000), (0xe4c00000, 0xe5c00000))),
}
NAMES = ('_text', 'swapper_pg_dir', 'log_buf', 'log_buf_len', 'log_first_idx', 'log_next_idx',
         'log_first_seq', 'log_next_seq')
EVENT = re.compile(
    r'^(?:calling\s+(?:vlx_clk_fe_init|samsung_abox_driver_init|kbase_driver_init)\+|'
    r'initcall (?:vlx_clk_fe_init|samsung_abox_driver_init|kbase_driver_init)\+|'
    r'vclk-fe: peer->be_version:|probe of clk@fe returned|Run /bin/sh as init process$|'
    r'systemd\[1\]: (?:systemd 241 running|(?:Starting|Started) udev Coldplug all Devices|'
    r'Reached target (?:Local File Systems|System Initialization|Basic System)\.)|'
    r'init: init second stage started!|mali [\da-f]+\.G3D1: (?:Mali GPU Probed|Arbitration interface enabled)|'
    r'HidlServiceManagement: Waited one second for android\.hardware\.keymaster@4\.0::IKeymasterDevice/default)')
ANDROID_STARTUP_EVENT = re.compile(
    r"^(?:Linux version |init: (?:init second stage started|starting service '(?:apexd|wait_for_keymaster|[^']*keymaster)|"
    r"Service '(?:apexd|wait_for_keymaster|[^']*keymaster)|Command 'exec_start (?:apexd|wait_for_keymaster)|"
    r"Control message: Could not find 'android\.hardware\.keymaster)|"
    r"apexd: (?:Bootstrap|Bootstrapping|Activated \d+ packages|wait for |.*(?:[Ee]rror|[Ff]ail|[Dd]enied))|"
    r"ueventd: (?:ueventd started|Coldboot took)|loop: module loaded|"
    r"(?:reboot: Restarting system|wait_for_keymaster:|[^ ]*keymaster[^ ]*:.*(?:register|fail))|"
    r"Trustonic TEE: mobicore_(?:init|probe))")


def plan(loaded, guest, symbols):
    vmid, base, digest, banks = IDENTITIES[guest]
    if (loaded['vmid'], loaded['physical'], loaded['kernel_sha256'], symbols['sha256']) != (vmid,base,digest,digest):
        raise ValueError('Original kernel identity or placement differs')
    syms = {r['name']: int(r['address'],16) for r in symbols['symbols'] if r['name'] in NAMES}
    addresses = {name:base+syms[name]-syms['_text'] for name in NAMES[1:]}
    if any(not base <= a <= base+0x02000000-8 for a in addresses.values()):
        raise ValueError('Boot ring metadata leaves the bounded image window')
    return dict(addresses=addresses, banks=banks, tcr=int(symbols['observed_tcr_el1'],16), digest=digest)


def locate(c, read):
    a = c['addresses']
    def checked(pa):
        if pa%8 or not any(lo<=pa<=hi-8 for lo,hi in c['banks']):
            raise ValueError('Boot ring page-table read outside original guest RAM')
        return read(pa)

    def scalar(name, size):
        pa = a[name]
        return (checked(pa&~7) >> ((pa&7)*8)) & ((1<<(size*8))-1)

    va, size = scalar('log_buf',8), scalar('log_buf_len',4)
    if not 0xffffff8000000000<=va<=0xffffffc012ffffff or va%8 or size not in (512<<10,2<<20):
        raise ValueError('Original printk ring is not initialized or geometry differs')
    first = walk_guest_4k(va,c['tcr'],a['swapper_pg_dir'],checked)
    pa = int(first['physical_target'],16)
    if not any(lo<=pa and pa+size<=hi for lo,hi in c['banks']):
        raise ValueError('Boot ring extent outside original guest RAM')
                                                                        
                                                                         
    cursor = va
    while cursor < va+size:
        row = first if cursor==va else walk_guest_4k(cursor,c['tcr'],a['swapper_pg_dir'],checked)
        if int(row['physical_target'],16)!=pa+cursor-va:
            raise ValueError('Original printk ring is not physically contiguous')
        shift = row['walk'][-1]['shift']
        cursor = ((cursor>>shift)+1)<<shift
        if shift < 21 and size > 512<<10:
            raise ValueError('Early capture requires a block-mapped large ring')
    keys = ('first','next_index','first_seq','next_seq')
    values = tuple(scalar(n,s) for n,s in zip(NAMES[4:],(4,4,8,8)))
    return pa,size,dict(zip(keys,values))


def capture(qmp, execute, output, configs, tick, cutoff, *, events=EVENT):

    captured = {}
    rejected = {}
    start = time.monotonic()
    with TemporaryDirectory(prefix='early-ring-',dir=output) as directory:
        temp = Path(directory)/'ring.bin'

        def read(address):
            if time.monotonic()-start > 1.25:
                raise ValueError('Early boot capture pause budget reached')
            reply = execute(qmp,'human-monitor-command',{'command-line':f'xp /1gx {address:#x}'})
            match = re.search(r':\s+0x([0-9a-fA-F]{1,16})(?:\s|$)',reply)
            if not match:
                raise ValueError('Missing QEMU physical word')
            return int(match[1],16)

        execute(qmp,'stop')
        try:
            if execute(qmp,'query-status')['status'] != 'paused':
                raise ValueError('Expected a coherent paused board')
            for guest, config in configs.items():
                try:
                    pa,size,ring = locate(config,read)
                    if not ring['next_seq']:
                        raise ValueError('No original records yet')
                    execute(qmp,'pmemsave',dict(val=pa,size=size,filename=str(temp)))
                    data = temp.read_bytes()
                    if len(data)!=size:
                        raise ValueError('Truncated original printk RAM')
                    captured[guest] = (pa,ring,data)
                except (ValueError,OSError,RuntimeError) as exc:
                    rejected[guest] = str(exc)
        finally:
            if execute(qmp,'query-status')['status'] == 'paused':
                execute(qmp,'cont')
    stop = time.monotonic()
    guests = {}
    for guest,(pa,ring,data) in captured.items():
        try:
            rows = vm.printk_live_ring(data,physical=pa,**ring,
                                      **vm.original_printk_layout(configs[guest]['digest']))
            selected = [dict(seq=r['sequence'], guest_ns=r['timestamp_ns'], text=r['text'].splitlines()[0][:240])
                        for r in rows if r['timestamp_ns']<=cutoff*10**9 and events.match(r['text'])]
            guests[guest] = dict(status='verified_ring', first_seq=ring['first_seq'],next_seq=ring['next_seq'],
                                 first_ns=rows[0]['timestamp_ns'],last_ns=rows[-1]['timestamp_ns'],
                                 sha256=hashlib.sha256(data).hexdigest(), events=selected)
        except (ValueError,IndexError) as exc:
            rejected[guest] = str(exc)
    return dict(host_monotonic=tick, sample_start_monotonic=start,sample_resume_monotonic=stop,
                guests=guests,rejected=rejected)


def setup(output, symbol_dir, *, guests=IDENTITIES):
    loaded = json.loads((output/'kernel-loads.json').read_bytes())
    return {g:plan(next(r for r in loaded if r['guest']==g),g,
                   json.loads((symbol_dir/f'{g}-symbols.json').read_bytes())) for g in guests}
