
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'src/emu'))
from linux_uart_platform import VLM_RAM, VLM_RX_CAPTURE, VLM_VCONS, native_rx_contract
from vlm_platform import Elf, original

HYP_SHA = '49250f278f354865146c05355575312caa8dac24274f7d46e680aa48f1c889ff'
VMS, OUT_OFFSET = 0x801955a8, 0x8018e97c
CONSOLE_RX_HANDLER, VSUART_TX_HANDLER = 0x800ae394, 0x800c19cc
TX_ABI = (
    ('_cons_out_ring_putchar', 0x800a9f5c, 0x94, 'a4dd93aec73f82ef20371da45a8a51b16c08d45c8dec5cbc616a2c55772e8313'),
    ('_cons_out_vmid', 0x800aa3c8, 0x60, '3baf8d47862995934a7984e31c907452479d897260dba2ea996a1a3b8fc8752a'),
    ('_vcons_out', 0x800ae270, 0xf8, 'fdba446168ccda8005ee5676de16e3b6fb58cbac0593490b6f2c975cb4b65461'),
    ('_vcons_rx_handler', CONSOLE_RX_HANDLER, 0xc, 'addc8897303e04aacbd905c95bf13acd30ef1055700fcd4b0726afbd69480d1a'),
    ('_vsuart_tx_update', 0x800c1198, 0xe0, '4360350840595a809c69741585aa055e56a4d830c0cf600eeb4bf3b3cd24710f'),
    ('_vsuart_tx_handler', VSUART_TX_HANDLER, 0xc, '08ac333ee9a44e79c75bdbbc2987d5febb7990477fd4c5d45deeac44cc41043a'),
    ('_vsuart_store_utxh', 0x800c1e6c, 0xc8, '56f63ec5547ef2dcc3ab00d376667fe7ed2e1b74fa6b82ba69cbfd1994d48f8b'),
    ('cons_out_init_vm', 0x800daabc, 0x188, 'dbf2753755656962801b506540d4a861be97379f97d42759c8fa49250a981689'),
)


def verify_original():
    native_rx_contract()
    image = original('hv.img').read_bytes()
    if sha256(image).hexdigest() != HYP_SHA:
        raise ValueError('Unrecognized HYP image')
    elf = Elf(original('hypervisor').read_bytes())
    symbols = {s['name']:(s['address'], s['size']) for s in elf.symbols}
    if symbols.get('_vms') != (VMS, 0x100):
        raise ValueError('Unrecognized VM table')
    for name, address, size, digest in TX_ABI:
        start = address-0x80080000
        body = elf.at(address, size)
        if (symbols.get(name) != (address, size) or
                body != image[start:start+size] or sha256(body).hexdigest() != digest):
            raise ValueError('Original TX ABI changed: '+name)


def decode(raw):
    if len(raw) not in (0x480000, VLM_RX_CAPTURE):
        raise ValueError('Unexpected HYP RAM capture length')

    def get(fmt, address):
        off = address-VLM_RAM
        if not 0 <= off <= len(raw)-struct.calcsize(fmt):
            raise ValueError(f'Pointer outside captured OEM HYP RAM: {address:#x}')
        return struct.unpack_from(fmt, raw, off)[0]

    def u32(addr):
        return get('<I', addr)

    def u64(addr):
        return get('<Q', addr)

    offset = u32(OUT_OFFSET)
    if offset != 0x5ca8:
        raise ValueError('Unrecognized console-output VM field')
    result = {'scope':'Read-only original HYP UART TX counters; not guest command proof',
              'capture_bytes':len(raw), 'vmids':{}}
    for vmid in (2,3,4):
        vm = u64(VMS+8*(vmid-1))
        if u32(vm) != vmid:
            raise ValueError('Original VM table ID does not match its slot')
        out = u64(vm+offset)
        consume, produce = u32(out+16), u32(out+20)
        pending = (produce-consume)&0xffffffff
        if pending > 1024:
            raise ValueError('Inconsistent OEM cooked output ring')
        result['vmids'][str(vmid)] = dict(cooked=dict(produced=produce, consumed=consume,
            pending=pending, flush_timer_present=bool(u64(out+1088)),
            flush_timer_armed=bool(u32(out+1084))), vfifos=[])

    first = u64(VLM_VCONS)
    seen = set()
    while first:
        try:
            vid = u32(first)
            following = u64(first+0x48)
            link = first
            while link:
                if link in seen or len(seen)>=24 or u32(link)!=vid:
                    raise ValueError('Unrecognized original virtual-console list')
                seen.add(link)
                rx = u64(link+8)                                           
                if rx and str(vid) in result['vmids']:
                    tx, buf = u64(rx+8), u64(rx)
                    cap, mask = u32(buf+0x20), u32(buf+0x24)
                    producer, consumer = u32(tx+0x14), u32(rx+0x14)
                    pending = (producer-consumer)&0xffffffff
                    if (u64(tx+8)!=rx or u64(tx)!=buf or u64(rx+0x28)!=CONSOLE_RX_HANDLER or
                            u64(rx+0x30)!=link or cap not in (128,256,512,1024,2048,4096) or
                            mask!=cap-1 or pending>cap):
                        raise ValueError('Unrecognized OEM virtual UART TX FIFO')
                    row = dict(active=bool(u32(link+4)), xoff=bool(u32(link+56)),
                               draining=bool(u32(link+60)), produced=producer, consumed=consumer,
                               pending=pending, capacity=cap)
                    if u64(tx+0x28)==VSUART_TX_HANDLER:
                        uart = u64(tx+0x30)
                        if u32(uart)!=vid or u64(uart+0x108)!=tx:
                            raise ValueError('OEM virtual UART TX pointer does not match the FIFO')
                        row['uart'] = dict(irq_id=u32(uart+0x10), tx_mode=(u32(uart+0x18)>>2)&3,
                            tx_empty=bool(u32(uart+0x24)&4), tx_pending=bool(u32(uart+0x48)&4),
                            tx_masked=bool(u32(uart+0x4c)&4), tx_quota=u32(uart+0x120),
                            tx_quota_used=u32(uart+0x124))
                    result['vmids'][str(vid)]['vfifos'].append(row)
                link = u64(link+0x50)
            first = following
        except ValueError as exc:
            result['vfifo_partial'] = str(exc)
            break
    return result


def shared_reader(path):

    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'rb', buffering=0)


def read_live(session, seconds):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline and not (session/'integrity.json').is_file():
        for path in session.glob('vlm-rx-*/ram.bin'):
            try:
                with shared_reader(path) as file:
                    ready = min(deadline,time.monotonic()+.5)
                    while os.fstat(file.fileno()).st_size<VLM_RX_CAPTURE and time.monotonic()<ready:
                        time.sleep(.002)
                    raw = file.read(VLM_RX_CAPTURE+1)
                if len(raw)==VLM_RX_CAPTURE:
                    return raw
            except OSError:
                pass                                                             
        time.sleep(.004)
    raise TimeoutError('No complete live HYP RAM sample before the observation deadline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--watch-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0<=args.watch_seconds<=180:
        parser.error('--watch-seconds must be 0..180')
    session = args.session.resolve(strict=True)
    launch = json.loads((session/'launch.json').read_bytes())
    if launch.get('input_sha256')!=HYP_SHA:
        raise ValueError('Session does not use the original HYP bytes')
    verify_original()
    raw = (read_live(session,args.watch_seconds) if args.watch_seconds else
           (session/'hypervisor-ram.bin').read_bytes())
    result = decode(raw)
    result.update(session=session.name, captured_monotonic=time.monotonic())
    print(json.dumps(result, indent=2))


if __name__=='__main__':
    main()
