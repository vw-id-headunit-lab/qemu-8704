







from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import threading
import time
import urllib.parse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def load_config(path=HERE/'config.json'):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    for key in ('qemu', 'qemu_img', 'original', 'assets', 'sessions', 'ivi_root', 'android_system', 'android_vendor'):
        item = Path(cfg[key])
        cfg[key] = item if item.is_absolute() else ROOT/item
    if not 512 <= cfg['memory_mib'] <= 16384 or not 1 <= cfg['cpus'] <= 8:
        raise ValueError('Invalid VM resource limits')
    if not 1024 <= cfg['http_port'] <= 65535 or not 1 <= cfg['serial_byte_delay_ms'] <= 20:
        raise ValueError('Invalid port/serial delay')
    return cfg


def align(value, page):
    return (value+page-1)//page*page


def android_parts(boot, vendor):

    if len(boot) < 4096 or boot[:8] != b'ANDROID!' or struct.unpack_from('<I', boot, 40)[0] != 3:
        raise ValueError('Require Android boot header v3')
    kernel_size, ramdisk_size, _, header_size = struct.unpack_from('<4I', boot, 8)
    if header_size < 44 or header_size > 4096:
        raise ValueError('Invalid boot header size')
    kernel_start = align(header_size, 4096)
    ramdisk_start = kernel_start + align(kernel_size, 4096)
    if not kernel_size or ramdisk_start+ramdisk_size > len(boot):
        raise ValueError('Truncated boot payload')
    if len(vendor) < 2112 or vendor[:8] != b'VNDRBOOT':
        raise ValueError('Require vendor_boot image')
    version, page = struct.unpack_from('<2I', vendor, 8)
    size = struct.unpack_from('<I', vendor, 24)[0]
    header = struct.unpack_from('<I', vendor, 2096)[0]
    if version != 3 or page not in (2048, 4096, 16384) or not 2112 <= header <= 65536:
        raise ValueError('Unsupported vendor_boot layout')
    start = align(header, page)
    if start+size > len(vendor):
        raise ValueError('Truncated vendor ramdisk')
    return boot[kernel_start:kernel_start+kernel_size], boot[ramdisk_start:ramdisk_start+ramdisk_size], vendor[start:start+size]


def prepare(cfg):
    assets = cfg['assets']
    assets.mkdir(parents=True, exist_ok=True)
    original = cfg['original']
    boot_path, vendor_path = original/'android/boot.img', original/'android/vendor_boot.img'
                                                                                 
    index = {row['name'].removeprefix('./'): row for row in
             map(json.loads, (ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines())}
    verified = []
    for name in ('android/boot.img', 'android/vendor_boot.img', 'ivi/Image'):
        path = original/name
        if not path.exists():
            if name == 'ivi/Image':
                continue
            raise FileNotFoundError(path)
        digest = sha(path)
        if digest != index[name]['sha256'] or path.stat().st_size != index[name]['size']:
            raise ValueError('Original input mismatch: '+name)
        verified.append({'member': name, 'sha256': digest})
    kernel, generic, vendor = android_parts(boot_path.read_bytes(), vendor_path.read_bytes())
    for name, data in (('android-kernel', kernel), ('android-ramdisk.gz', generic+vendor)):
        path = assets/name
        if path.exists() and path.read_bytes() != data:
            raise ValueError('Preserve differing existing asset: '+str(path))
        if not path.exists():
            path.write_bytes(data)
    configurations = {}
    for name, data in [('android', kernel)] + ([('linux', (original/'ivi/Image').read_bytes())] if (original/'ivi/Image').exists() else []):
        start = data.find(b'IKCFG_ST')
        if start >= 0:
            try:
                end = data.index(b'IKCFG_ED', start+8)
                text = gzip.decompress(data[start+8:end]).decode('utf-8')
                (assets/(name+'-kernel.config')).write_text(text, encoding='utf-8')
                wanted = ('CONFIG_SERIAL_AMBA_PL011', 'CONFIG_SERIAL_SAMSUNG', 'CONFIG_VIRTIO', 'CONFIG_DRM', 'CONFIG_FB', 'CONFIG_HVC', 'CONFIG_IKCONFIG', 'CONFIG_ARM_PSCI', 'CONFIG_ARM_GIC', 'CONFIG_ARCH_EXYNOS')
                configurations[name] = [line for line in text.splitlines() if any(x in line for x in wanted)]
            except (ValueError, OSError, UnicodeError) as error:
                configurations[name] = {'error': str(error)}
    result = {'verified_originals': verified, 'android_kernel_bytes': len(kernel), 'arm64_image_magic': kernel[56:60].hex(),
              'kernel_config': configurations, 'factory_hmi_verified': False, 'guest_network': 'none'}
    save(assets/'prepare.json', result)
    return result


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def printk_records(data, *, max_records=4096):





    if not 3 <= max_records <= 65536:
        raise ValueError('Printk record limit outside 3..65536')
    candidates = []
    for match in re.finditer(rb'Booting Linux on physical CPU 0x[0-9a-fA-F]+', data):
        for header in range(16, 129, 4):
            position = match.start()-header
            records = []
            while 0 <= position <= len(data)-header and len(records) < max_records:
                timestamp, length, text_length, dictionary_length, facility, flags = struct.unpack_from('<QHHHBB', data, position)
                if not header < length <= 8192 or text_length > 4096 or timestamp > 10**15 or facility > 23:
                    break
                                                                              
                                                                          
                                                                           
                if not text_length and (not timestamp or (not dictionary_length and length - header > 16)):
                    break
                if length < header+text_length+dictionary_length or position+length > len(data):
                    break
                text = data[position+header:position+header+text_length]
                                                                         
                                                                               
                                                                                  
                allowed_min = 8 if text.startswith(b'mke2fs: ') else 9
                if any(byte < allowed_min or 13 < byte < 32 for byte in text):
                    break
                records.append({'physical_address': hex(0x40000000+position), 'timestamp_ns': timestamp,
                                'length': length, 'header_bytes_inferred': header, 'facility': facility, 'flags': flags,
                                'text': text.decode('utf-8', errors='replace')})
                position += length
            if len(records) >= 3:
                candidates.append(records)
    return max(candidates, key=len) if candidates else []


def original_printk_layout(kernel_sha256):
                                                                          
                                                                       
                                                                         
    from linux_pid1_context import LINUX_SHA
    return dict(header=48,timestamp_offset=8) if kernel_sha256==LINUX_SHA else dict(header=40,timestamp_offset=0)


def printk_live_ring(data, *, first, next_index, first_seq, next_seq, physical, header=40, timestamp_offset=0):

    count=next_seq-first_seq
    if (header not in range(16,129,4) or timestamp_offset not in range(0,header-15,4) or
            not 0<=first_seq<=next_seq or not 0<=count<=min(len(data)//header,262144) or
            not all(0<=idx<len(data) and idx%4==0 for idx in (first,next_index))):
        raise ValueError('Invalid original printk ring indices or record count')
    index=first
    result=[]
    visited=set()
    for seq in range(first_seq,next_seq):
        if index+header>len(data):
            raise ValueError('Original printk index outside complete record header')
        if not struct.unpack_from('<H',data,index+timestamp_offset+8)[0] and index:
            index=0                                                    
        if index in visited:
            raise ValueError('Original printk chain reused an occupied record')
        visited.add(index)
        stamp,length,text_length,dict_length,facility,flags=struct.unpack_from('<QHHHBB',data,index+timestamp_offset)
        if (not header<=length<=8192 or length%4 or index+length>len(data) or
                text_length+dict_length>length-header or stamp>10**15):
            raise ValueError(f'Inconsistent original printk record at {index:#x}, seq {seq}')
        text=data[index+header:index+header+text_length].decode('utf-8',errors='replace')
        result.append(dict(physical_address=hex(physical+index),timestamp_ns=stamp,
            length=length,header_bytes_inferred=header,facility=facility,flags=flags,text=text,sequence=seq))
        index+=length
    if index!=next_index:
        raise ValueError('Original printk chain does not end at log_next_idx')
    return result


def core_id_probe_copy(data, name, skip_logger=False):






    known = {
        'linux': ('c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109', 7158316, '300780d202e01fd4'),
        'android': ('471ce1178f76ae9b4dfba3f87110bfef4f31e432574dafda7f89330f87ca3a29', 7403660, '3007805202e01fd4')}
    if name not in known:
        raise ValueError('Unknown original kernel')
    expected, offset, context = known[name]
    if hashlib.sha256(data).hexdigest() != expected or data[offset-4:offset+4].hex() != context:
        raise ValueError('Core-id probe requires the exact reviewed original kernel and instruction context')
    if skip_logger:
        offset, prologue = (4059320, 'fd7bbba9') if name == 'linux' else (7403576, 'ff0302d1')
        if data[offset:offset+4].hex() != prologue:
            raise ValueError('Boot-logger prologue differs from reviewed original')
    replacement = bytes.fromhex('c0035fd6' if skip_logger else '000080d2')                     
    result = data[:offset]+replacement+data[offset+4:]
    return result, {'original_sha256': expected, 'derived_sha256': hashlib.sha256(result).hexdigest(),
                    'offset': offset, 'before_hex': data[offset:offset+4].hex(), 'after_hex': replacement.hex(),
                    'cpus': 1, 'mode': 'skip-boot-logger' if skip_logger else 'core-id',
                    'scope': 'One-instruction platform diagnostic copy; NOT unmodified original kernel or full Exynos platform'}


class QMP:
    def __init__(self, port):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=3)
        self.stream = self.socket.makefile('rwb', buffering=0)
        greeting = json.loads(self.stream.readline())
        if 'QMP' not in greeting:
            self.close()
            raise ValueError('Unexpected management endpoint')
        self.execute('qmp_capabilities')

    def execute(self, command, arguments=None):
        self.stream.write(json.dumps({'execute': command, 'arguments': arguments or {}}).encode()+b'\n')
        for line in self.stream:
            result = json.loads(line)
            if 'error' in result:
                raise RuntimeError(json.dumps(result['error']))
            if 'return' in result:
                return result['return']
        raise OSError('QMP disconnected')

    def close(self):
        self.stream.close()
        self.socket.close()


class Guest:
    def __init__(self, cfg, name):
        if name not in ('linux', 'android'):
            raise ValueError('Unknown guest')
        self.cfg, self.name = cfg, name
        self.lock = threading.RLock()
        self.process = None
        self.session = None
        self.error = None
        self.started = None
        self.qmp_port = None
        self.inputs = {}
        self.last_frame = None
        self.reader = None
        self.boot_diagnostic = None

    def start(self, original_dtb=False, core_id_shim=False, skip_boot_logger=False):
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError('Guest already running')
            if core_id_shim and skip_boot_logger:
                raise ValueError('Choose only one platform-probe condition')
            cfg = self.cfg
            kernel = cfg['original']/'ivi/Image' if self.name == 'linux' else cfg['assets']/'android-kernel'
            if not kernel.is_file():
                raise FileNotFoundError(kernel)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            self.session = cfg['sessions']/(stamp+'-'+self.name)
            self.session.mkdir(parents=True)
            adaptation = None
            original_kernel = kernel
            if core_id_shim or skip_boot_logger:
                derived, adaptation = core_id_probe_copy(kernel.read_bytes(), self.name, skip_boot_logger)
                kernel = self.session/'single-core-platform-probe.Image'
                temporary = kernel.with_suffix('.tmp')
                temporary.write_bytes(derived)
                os.replace(temporary, kernel)
                save(self.session/'platform-adaptation.json', adaptation)
            self.qmp_port = free_port()
            command = [str(cfg['qemu']), '-machine', 'virt,gic-version=3', '-cpu', 'cortex-a72', '-accel', 'tcg,thread=multi',
                       '-smp', '1' if adaptation else str(cfg['cpus']), '-m', str(cfg['memory_mib']), '-kernel', str(kernel),
                       '-display', 'none', '-device', 'virtio-gpu-pci', '-device', 'virtio-keyboard-pci',
                       '-device', 'virtio-tablet-pci', '-serial', 'stdio', '-monitor', 'none', '-nic', 'none',
                       '-qmp', f'tcp:127.0.0.1:{self.qmp_port},server=on,wait=off',
                       '-action', 'reboot=shutdown,shutdown=pause,panic=pause',
                       '-name', '8704-original-'+self.name]
            append = 'earlycon=pl011,mmio32,0x09000000 console=ttyAMA0,115200 ignore_loglevel loglevel=8 panic=-1'
            disks = [cfg['ivi_root']] if self.name == 'linux' else [cfg['android_system'], cfg['android_vendor']]
            self.inputs = {str(path): sha(path) for path in [original_kernel, kernel, *disks]}
            for number, backing in enumerate(disks):
                if not backing.is_file() or ',' in str(backing):
                    raise ValueError('Invalid backing file')
                overlay = self.session/f'disk-{number}.qcow2'
                create = subprocess.run([str(cfg['qemu_img']), 'create', '-f', 'qcow2', '-F', 'raw', '-b', str(backing), str(overlay)],
                                        capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=30)
                (self.session/f'disk-{number}-create.log').write_bytes(create.stdout+create.stderr)
                create.check_returncode()
                command += ['-drive', f'file={overlay},format=qcow2,if=none,id=d{number}', '-device', f'virtio-blk-pci,drive=d{number}']
            if self.name == 'linux':
                append += ' root=/dev/vda rootfstype=ext4 rootwait rw init=/sbin/init'
            else:
                command += ['-initrd', str(cfg['assets']/'android-ramdisk.gz')]
                append += ' androidboot.hardware=exynosauto9 androidboot.slot_suffix=_a'
            if original_dtb:
                dtb = ROOT/('var/inputs/analysis/cpu/ivi/icas3cn-gp_b0_zero.dtb' if self.name == 'linux' else 'var/inputs/analysis/cpu/android/vendor_boot.dtb')
                command += ['-dtb', str(dtb)]
            command += ['-append', append]
            self.error, self.last_frame, self.started = None, None, time.monotonic()
            self.boot_diagnostic = None
            self.adaptation = adaptation
            save(self.session/'session.json', {'guest': self.name, 'command': command, 'original_dtb': original_dtb, 'platform_adaptation': adaptation,
                 'input_hashes_before': self.inputs, 'board': 'QEMU virt (NOT ExynosAuto9)', 'factory_hmi_verified': False})
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.reader = threading.Thread(target=self._read, args=(self.process, self.session), daemon=True)
            self.reader.start()
            return self.status()

    def _read(self, process, session):
        with (session/'serial.log').open('wb') as log:
            while chunk := process.stdout.read1(16384):
                log.write(chunk)
                log.flush()
        code = process.wait()
        if code:
            self.error = f'QEMU exited with code {code}; inspect serial.log'
        save(session/'exit.json', {'exit_code': code, 'factory_hmi_verified': False})

    def status(self):
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            serial = self.session/'serial.log' if self.session else None
            raw = b''
            if serial and serial.exists():
                with serial.open('rb') as stream:
                    stream.seek(max(0, serial.stat().st_size-262144))
                    raw = stream.read(262144)
            text = raw.decode('utf-8', errors='replace').replace('\x00', '\\x00')
            return {'guest': self.name, 'running': running, 'pid': self.process.pid if running else None,
                    'uptime_seconds': round(time.monotonic()-self.started, 1) if running else 0,
                    'console': text, 'serial_bytes_retained': len(raw), 'session': str(self.session) if self.session else None,
                    'error': self.error, 'factory_hmi_verified': False, 'last_frame': self.last_frame,
                    'boot_diagnostic': self.boot_diagnostic,
                    'platform_adaptation': getattr(self, 'adaptation', None),
                    'scope': 'Derived single-core HVC probe; no original GUI acceptance' if getattr(self, 'adaptation', None) else 'Original kernel on QEMU virt; no original GUI acceptance yet'}

    def monitor(self, command):
        if command not in ('info registers', 'info cpus', 'info status'):
            raise ValueError('Only read-only diagnostic monitor commands are exposed')
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise ValueError('Guest is not running')
            connection = QMP(self.qmp_port)
            try:
                value = connection.execute('human-monitor-command', {'command-line': command})
                save(self.session/('monitor-'+command.replace(' ', '-')+'.json'), {'command': command, 'output': value})
                return value
            finally:
                connection.close()

    def serial(self, command):
        if not isinstance(command, str) or not command.strip() or len(command) > 4096 or '\x00' in command:
            raise ValueError('Invalid serial command')
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise ValueError('Guest is not running')
                                                                                        
            for byte in (command+'\n').encode('utf-8'):
                self.process.stdin.write(bytes([byte]))
                self.process.stdin.flush()
                time.sleep(self.cfg['serial_byte_delay_ms']/1000)
            with (self.session/'serial-input.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps({'command': command, 'sent_at_utc': datetime.now(timezone.utc).isoformat()})+'\n')

    def frame(self):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise ValueError('Guest is not running')
            path = self.session/'screen.png'
            connection = QMP(self.qmp_port)
            try:
                connection.execute('screendump', {'filename': str(path), 'format': 'png'})
            finally:
                connection.close()
            raw = path.read_bytes()
            if raw[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValueError('QEMU did not provide PNG')
            self.last_frame = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'captured_at_utc': datetime.now(timezone.utc).isoformat(),
                               'origin': 'QEMU framebuffer; NOT automatically verified as original HMI'}
            save(self.session/'screen.json', self.last_frame)
            return raw

    def memory_diagnostic(self):

        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise ValueError('Guest is not running')
            return self._memory_diagnostic()

    def _memory_diagnostic(self):
        path = self.session/'boot-ram-64m.bin'
        connection = QMP(self.qmp_port)
        try:
            connection.execute('pmemsave', {'val': 0x40000000, 'size': 64*1024*1024, 'filename': str(path)})
        finally:
            connection.close()
        import re
        data = path.read_bytes()
        notes = []
        for match in re.finditer(rb'[\x20-\x7e]{20,}', data):
            value = match.group().decode('ascii')
            if any(word in value for word in ('Kernel panic', 'VFS:', 'Machine model:', 'Booting Linux', 'No working init', 'Unable to handle kernel')):
                notes.append({'physical_address': hex(0x40000000+match.start()), 'text': value[:1500]})
        report = {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                  'notes': notes, 'printk_records': printk_records(data),
                  'limitations': 'RAM contains code literals and live log records. Only a validated consecutive printk record chain is reported as printk_records.'}
        save(self.session/'boot-ram-notes.json', report)
        records = report['printk_records']
        self.boot_diagnostic = {'source': str(path), 'record_count': len(records),
                                'header_bytes_inferred': records[0]['header_bytes_inferred'] if records else None,
                                'kernel_panic_observed': any('Kernel panic - not syncing:' in row['text'] for row in records),
                                'text': '\n'.join(row['text'] for row in records),
                                'captured_at_utc': datetime.now(timezone.utc).isoformat()}
        save(self.session/'boot-diagnostic.json', self.boot_diagnostic)
        return report

    def stop(self):
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    connection = QMP(self.qmp_port)
                    try:
                        connection.execute('quit')
                    finally:
                        connection.close()
                    process.wait(timeout=8)
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            if self.session:
                if self.reader:
                    self.reader.join(timeout=3)
                after = {path: sha(path) for path in self.inputs}
                save(self.session/'integrity.json', {'before': self.inputs, 'after': after, 'originals_unchanged': after == self.inputs})
            return self.status()


                                                                       
VLM_CONSOLE_TAG = re.compile(rb'^(?:\x1b\[[0-9;]{1,16}m)*\d+ ([234]) \d+:(.*)$')
VLM_CONSOLE_GUEST = {b'3': 'linux', b'4': 'android'}


def native_console_record(raw):
    match=VLM_CONSOLE_TAG.fullmatch(raw.rstrip(b'\r'))
    if match and match[1] in VLM_CONSOLE_GUEST:
        data=match[2].decode('utf-8','replace')
        return VLM_CONSOLE_GUEST[match[1]],''.join(
            ch if ch.isprintable() or ch=='\t' else '\ufffd' for ch in data)
    return None


def native_console_lines(raw, *, limit=32768):

    result = {guest: [] for guest in VLM_CONSOLE_GUEST.values()}
    for line in raw.split(b'\n')[:-1]:
        parsed=native_console_record(line)
        if parsed:
            result[parsed[0]].append(parsed[1])
    if not 1<=limit<=1<<20:
        raise ValueError('Native console tail limit outside 1..1048576')
    return {guest: '\n'.join(lines)[-limit:] for guest, lines in result.items()}


def native_shared_uart(raw, *, limit=16384):

    if not 1<=limit<=1<<20:
        raise ValueError('Shared native UART tail limit outside 1..1048576')
                                                                     
    lines=[]
    for line in raw.split(b'\n'):
        line=line.rstrip(b'\r')
        if line and not VLM_CONSOLE_TAG.fullmatch(line):
            decoded=line.decode('utf-8','replace')
            lines.append(''.join(ch if ch.isprintable() or ch=='\t' else '\ufffd' for ch in decoded))
    return '\n'.join(lines)[-limit:]


class NativeConsoleTap:

    def __init__(self, session):
        self.session = Path(session).resolve(strict=True)
        meta = json.loads((self.session/'linux-native-console.json').read_bytes())
        if (meta.get('source'), meta.get('serial_index'), meta.get('host')) != (
                'vlm-console-mux', 1, '127.0.0.1'):
            raise ValueError('Session does not bind the original vLM console')
        self.meta = meta
        self.direct = None
        if (self.session/'linux-direct-console.json').is_file():
            from linux_early_uart import read_direct_binding
            self.direct = read_direct_binding(self.session)

    def focus(self):
        try:
            record=json.loads((self.session/'vlm-console-focus.json').read_bytes())
            at=record['host_monotonic']
            if (record.get('source')=='original-vlm-focus' and record.get('running') is True and
                    type(at) in (int,float) and 0<=time.monotonic()-at<2.5 and
                    type(record.get('vmid')) is int and 0<=record['vmid']<=4):
                return record
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def status(self):
                                                                              
        def tail(name, limit):
            try:
                with (self.session/name).open('rb') as stream:
                    offset = max(0, stream.seek(0, os.SEEK_END)-limit)
                    stream.seek(offset)
                    data = stream.read(limit)
                return data.partition(b'\n')[2] if offset else data
            except FileNotFoundError:
                return b''

        data = tail('vlm-console-rx.bin', 256 << 10)
        lines = native_console_lines(data)
                                                                             
                                                                              
        partial=native_console_record(data.rpartition(b'\n')[2])
        events = tail('vlm-console-events.jsonl', 4096).split(b'\n')[:-1]
                                                                             
                                                                             
        ended = bool(events and json.loads(events[-1]).get('event') == 'eof') and self.focus() is None
        status = ('采集已结束；显示本次真实串口留存。' if ended else
                  '已收到原 vLM 串口字节；未验证登录或命令执行。' if data else
                  '等待原 vLM 串口字节。')
                                                                         
        running=self.focus() is not None
        states={name: dict(guest=name, mode='native-vlm', running=False, board_running=running, console=lines[name],
                           console_pending=partial[1][-4096:] if partial and partial[0]==name else '',
                           session=str(self.session), serial_writable=False, capture_ended=ended,
                           source_state=status, factory_hmi_verified=False, command_execution_verified=False)
                for name in lines}
        if self.direct:
            from linux_early_uart import DIRECT_CAPTURE
            wire=tail(DIRECT_CAPTURE,256 << 10)
            complete,sep,pending=wire.rpartition(b'\n')
            if not sep:
                complete,pending=b'',wire

            def clean(raw):
                return ''.join(ch if ch.isprintable() or ch in '\t\n' else '\ufffd'
                               for ch in raw.decode('utf-8','replace').replace('\r',''))

            states['linux'].update(console=clean(complete)[-32768:],console_pending=clean(pending)[-4096:],
                console_route=self.direct['source'],
                source_state='VM3 独占的原厂 ttySAC3；尚未验证命令回显。')
        if (self.session/'linux-local-shell-proof.json').is_file():
            proof=None
            try:
                from linux_local_shell import read_verified_proof
                proof=read_verified_proof(self.session)
            except (ImportError, OSError, ValueError) as error:
                                                                             
                                                                                   
                states['linux']['command_proof_error']=str(error)
            if proof:
                states['linux'].update(command_execution_verified=True, command_proof=proof,
                    source_state='本次原厂 Linux 已实际执行命令：UID 65534，内核 Linux。'+
                                 ('串口已结束。' if ended else ''))
        if (self.session/'android-local-shell-proof.json').is_file():
            proof=None
            try:
                from android_local_shell import read_verified_proof as read_android_proof
                proof=read_android_proof(self.session)
            except (ImportError, OSError, ValueError) as error:
                states['android']['command_proof_error']=str(error)
            if proof:
                states['android'].update(command_execution_verified=True, command_proof=proof,
                    source_state='本次原厂 Android 已实际执行命令：UID 2000，内核 Linux。'+
                                 ('串口已结束。' if ended else ''))
                                                                            
                                                                                
        states['linux']['shared_uart_unattributed']=native_shared_uart(data)
        return states


class NativeUartWire:

    def open_wire(self, port, raw_path, events_path):
        self.lock=threading.Lock()
        self.closed=threading.Event()
        self.error=None
        self.socket=socket.create_connection(('127.0.0.1',port),timeout=2)
        try:
            self.socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            create=not raw_path.exists() and not events_path.exists()
            self.wire=raw_path.open('xb' if create else 'ab',buffering=0)
            try:
                self.events=events_path.open('x' if create else 'a',encoding='utf-8',buffering=1)
            except OSError:
                self.wire.close()
                raise
        except OSError:
            self.socket.close()
            raise
        self.socket.settimeout(0.5)
        self.reader=threading.Thread(target=self.receive,daemon=True)
        self.reader.start()

    def receive(self):
        with self.wire, self.events:
            try:
                while not self.closed.is_set():
                    try:
                        data=self.socket.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    self.wire.write(data)
            except OSError as error:
                if not self.closed.is_set():
                    self.error=str(error)
            finally:
                self.closed.set()
                self.socket.close()
                self.events.write(json.dumps(dict(event='eof',rx_bytes=self.wire.tell()))+'\n')

    def close(self):
        self.closed.set()
        self.reader.join(timeout=2)


class NativeInteractiveConsole(NativeConsoleTap,NativeUartWire):

    def __init__(self, session, byte_delay_ms=3, direct_console=True):
        super().__init__(session)
        if type(direct_console) is not bool:
            raise ValueError('Dedicated console selection must be boolean')
        if type(byte_delay_ms) is not int or not 1<=byte_delay_ms<=20:
            raise ValueError('Native UART byte delay requires 1..20 ms')
        self.byte_delay=byte_delay_ms/1000
        port=self.meta['port']
        launch=json.loads((self.session/'launch.json').read_bytes())
        chardev=f'socket,id=oem-vlm-console,host=127.0.0.1,port={port},server=on,wait=off,logfile='
        if (type(port) is not int or not 1024<=port<=65535 or
                not any(arg.startswith(chardev) for arg in launch['command'])):
            raise ValueError('Native session has no matching vLM chardev')
        self.keys={}
        for guest,vmid in (('linux',3),('android',4)):
            route=self.meta['focus'][guest]
            key=bytes.fromhex(route['keys_hex'][0])
            if route['vmid']!=vmid or not re.fullmatch(rb'\x1b[O\[][0-9;A-Z~]{1,10}',key):
                raise ValueError('Invalid native focus key')
            self.keys[guest]=(vmid,key)
        if self.focus() is None:
            raise ValueError('No recent live focus from the native QMP owner')
        self.direct_wire=None
        self.open_wire(port,self.session/'vlm-console-rx.bin',self.session/'vlm-console-events.jsonl')
        if self.direct and direct_console:
            try:
                self.open_direct_console()
            except Exception:
                super().close()
                raise

    def open_direct_console(self):
        if self.direct and self.direct_wire is None:
            from linux_early_uart import DIRECT_CAPTURE
            wire=NativeUartWire()
            wire.open_wire(self.direct['port'],self.session/DIRECT_CAPTURE,
                           self.session/'linux-usi03-console-events.jsonl')
            self.direct_wire=wire

    def close(self):
        if self.direct_wire:
            self.direct_wire.close()
        super().close()

    def status(self):
        states=super().status()
        for state in states.values():
            writable=state['board_running'] and not self.closed.is_set()
            state.update(serial_writable=writable,native_return=True,
                         source_state=('原厂 UART 已连接；发送前会确认原厂焦点，不代表 shell 已启动。'
                                       if writable and not state['command_execution_verified'] else state['source_state']),
                         error=self.error)
        if self.direct_wire:
            linux=states['linux']
            writable=linux['board_running'] and not self.direct_wire.closed.is_set()
            linux.update(serial_writable=writable,error=self.direct_wire.error,
                         source_state=('VM3 独占的原厂 ttySAC3 已连接；尚未验证命令回显。'
                                       if writable and not linux['command_execution_verified']
                                       else linux['source_state']))
        return states

    def serial(self, guest, command):
        if not isinstance(command,str) or not command.strip() or not command.isprintable():
            raise ValueError('Require one printable command line')
        line=command.encode('utf-8')+b'\r'
        if len(line)>240:
            raise ValueError('Native UART command exceeds 240 bytes including carriage return')
        return self._transmit(guest,line)

    def return_key(self, guest):
                                                                              
                                                           
        return self._transmit(guest,b'\r')

    def _transmit(self, guest, line):
        direct=getattr(self,'direct_wire',None)
        if guest=='linux' and direct:
            with direct.lock:
                for i,byte in enumerate(line):
                    if (i%16==0 or i==len(line)-1) and (direct.closed.is_set() or self.focus() is None):
                        raise ValueError('Native VM3 UART or board heartbeat unavailable')
                    direct.socket.sendall(bytes((byte,)))
                    if i+1<len(line):time.sleep(self.byte_delay)
            return dict(sent=True,route_verified=True,route=self.direct['source'],
                        vmid=3,execution_verified=False)
        vmid,key=self.keys[guest]
        with self.lock:
            if self.closed.is_set() or self.focus() is None:
                raise ValueError('Native console or live focus unavailable')
            before=time.monotonic()
            self.socket.sendall(key)
            while not self.closed.is_set() and time.monotonic()-before<3:
                state=self.focus()
                if (state and state['host_monotonic']>before and
                        state.get('vmid')==vmid and state.get('read_only') is False):
                                                                             
                                                                          
                                                                          
                    for i,byte in enumerate(line):
                        if i and (i%16==0 or i==len(line)-1):
                            latest=self.focus()
                            if (self.closed.is_set() or not latest or latest.get('vmid')!=vmid or
                                    latest.get('read_only') is not False):
                                raise ValueError('Original vLM focus changed; command transmission was interrupted')
                        self.socket.sendall(bytes((byte,)))
                        if i+1<len(line):time.sleep(self.byte_delay)
                    return dict(sent=True,focus_verified=True,vmid=vmid,execution_verified=False)
                time.sleep(.05)
        raise ValueError('Original vLM focus was not confirmed; command bytes not sent')


def original_wod_ignores_pixel_alpha(meta):

    blend=meta.get('original_wod_blend')
    return (isinstance(blend,dict) and blend.get('verified') is True and
            blend.get('plane_alpha8')==255 and blend.get('drm_pixel_blend_mode')==2 and
            blend.get('wod_blend')==0 and meta.get('planes_on_crtc')==1 and
            meta.get('crtc_active') is True)


class Displays:





    def __init__(self, guests, native=None):
        self.guests = guests
        self.native = native

    def native_center(self, with_proof=False):
        from linux_pid1_context import LINUX_SHA
        if not self.native or self.native.focus() is None:
            return None
        try:
            path=self.native.session/'native-center-frame.json'
            with path.open('rb') as manifest:
                body=manifest.read((128<<10)+1)
            if len(body)>128<<10:
                return None
            meta=json.loads(body)
            if not isinstance(meta,dict):
                return None
            stamp=meta.get('captured_monotonic')
            digest=meta.get('png_sha256')
            mode=meta.get('status')
            gem=(mode=='active_gem_pages_verified' and meta.get('gem_plane_verified') is True and
                 meta.get('capture_origin')=='original_exynos_gem_sg' and meta.get('sysmmu_matches_gem') is False)
            if ((mode!='active_scanout_pages_verified' and not gem) or
                    meta.get('frame_source')!='original_vm3_drm_primary' or meta.get('connector')!='Virtual-1' or
                    meta.get('kernel_sha256')!=LINUX_SHA or meta.get('format') not in ('XR24','AR24') or
                    (meta.get('format')=='AR24' and meta.get('opaque_alpha_verified') is not True and
                     not (meta.get('alpha_ignored_by_original_plane') is True and original_wod_ignores_pixel_alpha(meta))) or
                    (meta.get('width'),meta.get('height'))!=(1560,700) or meta.get('modifier')!=0 or
                    meta.get('nonzero') is not True or meta.get('secure') is not False or
                    type(stamp) not in (int,float) or not 0<=time.monotonic()-stamp<15 or
                    not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest)):
                return None
            expected='native-center-'+digest+'.png'
            if meta.get('png')!=expected:
                return None
            frame=(self.native.session/expected).resolve(strict=True)
            if frame.parent!=self.native.session:
                return None
            with frame.open('rb') as stream:
                data=stream.read((8<<20)+1)
            if (not 33<=len(data)<=8<<20 or data[:8]!=b'\x89PNG\r\n\x1a\n' or
                    data[12:16]!=b'IHDR' or struct.unpack_from('>II',data,16)!=(1560,700) or
                    hashlib.sha256(data).hexdigest()!=digest):
                return None
            return (data,meta) if with_proof else data
        except (ValueError, OSError):
            return None

    def native_center_foreground(self, with_proof=False):

        from linux_pid1_context import LINUX_SHA
        if not self.native or self.native.focus() is None:
            return None
        session=self.native.session
        if (session/'integrity.json').is_file():
            return None
        try:
            def record(name):
                with (session/name).open('rb') as stream:
                    data=stream.read(16385)
                if len(data)>16384:
                    raise ValueError('Foreground proof exceeds size limit')
                value=json.loads(data)
                if not isinstance(value,dict):
                    raise ValueError('Invalid foreground proof')
                return value
            try:
                live=record('native-center-frame.json')
            except (OSError,ValueError):
                live={}
            if live.get('status')=='verified_original_current_foreground':
                stamp=live.get('captured_monotonic')
                digest=live.get('png_sha256')
                rgb=live.get('nonzero_rgb_pixels')
                expected='native-center-current-foreground.png'
                if (live.get('kernel_sha256')!=LINUX_SHA or live.get('secure') is not False or
                        live.get('connector')!='Virtual-1' or live.get('capture_origin')!='original_sysmmu' or
                        live.get('sysmmu_matches_gem') is not True or live.get('full_display') is not False or
                        live.get('crtc_active') is not True or live.get('planes_on_crtc')!=1 or
                        live.get('format')!='AR24' or live.get('modifier')!=0 or
                        (live.get('width'),live.get('height'))!=(1560,700) or
                        live.get('out_of_premultiplied_range_pixels')!=0 or
                        live.get('original_wod_blend')!={'verified':True,'plane_alpha8':255,
                            'drm_pixel_blend_mode':0,'wod_blend':1} or
                        type(rgb) is not int or not 0<rgb<=1560*700 or
                        type(stamp) not in (int,float) or not 0<=time.monotonic()-stamp<20 or
                        live.get('png')!=expected or not isinstance(digest,str) or
                        not re.fullmatch(r'[a-f0-9]{64}',digest)):
                    return None
                frame=(session/expected).resolve(strict=True)
                if frame.parent!=session:
                    return None
                with frame.open('rb') as stream:
                    png=stream.read((8<<20)+1)
                if (not 33<=len(png)<=8<<20 or png[:16]!=b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR' or
                        struct.unpack_from('>IIBB',png,16)!=(1560,700,8,6) or
                        hashlib.sha256(png).hexdigest()!=digest or record('native-center-frame.json')!=live):
                    return None
                return (png,live) if with_proof else png
            proof=record('native-center-last-nonzero-foreground.json')
            active=record('native-center-gpu-correlation.json')
            write=active.get('latest_write')
            stamp=proof.get('captured_monotonic')
            digest=proof.get('png_sha256')
            content=proof.get('active_content_sha256')
            rgb=proof.get('nonzero_rgb_pixels')
            if (proof.get('status')!='original_premultiplied_foreground_profile' or
                    proof.get('kernel_sha256')!=LINUX_SHA or proof.get('secure') is not False or
                    proof.get('capture_origin')!='original_sysmmu' or proof.get('full_display') is not False or
                    (proof.get('width'),proof.get('height'))!=(1560,700) or
                    proof.get('out_of_premultiplied_range_pixels')!=0 or
                    type(rgb) is not int or not 0<rgb<=1560*700 or
                    type(stamp) not in (int,float) or not 0<=time.monotonic()-stamp<12 or
                    active.get('capture_monotonic')!=stamp or
                    active.get('status')!='matching_physical_rows' or
                    active.get('latest_write_content_matches') is not True or
                    not isinstance(content,str) or not re.fullmatch(r'[a-f0-9]{64}',content) or
                    content!=active.get('active_content_sha256') or
                    not isinstance(write,dict) or write.get('content')!=content or write.get('rgb')!=rgb or
                    type(write.get('generation')) is not int or write['generation']<=0 or
                    proof.get('gpu_generation')!=write['generation'] or
                    not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest)):
                return None
            frame=(session/'native-center-last-nonzero-foreground.png').resolve(strict=True)
            if frame.parent!=session:
                return None
            with frame.open('rb') as stream:
                png=stream.read((8<<20)+1)
            if (not 33<=len(png)<=8<<20 or png[:16]!=b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR' or
                    struct.unpack_from('>IIBB',png,16)!=(1560,700,8,6) or
                    hashlib.sha256(png).hexdigest()!=digest or
                    record('native-center-gpu-correlation.json')!=active):
                return None
            return (png,proof) if with_proof else png
        except (ValueError,OSError):
            return None

    def native_qnx(self, name, with_proof=False):
        try:
            from qnx_native_display import ROLES, MEMBER_SHA256
        except (ImportError, ValueError):
            return None
        if not self.native or self.native.focus() is None or name not in ROLES:
            return None
        port,pipeline,pipe,dsi,width,height=ROLES[name]
        try:
            with (self.native.session/f'native-{name}-frame.json').open('rb') as manifest:
                body=manifest.read(32769)
            if len(body)>32768:
                return None
            meta=json.loads(body)
            if not isinstance(meta,dict):
                return None
            now=time.monotonic()
            stamp=meta.get('captured_monotonic')
            proof=meta.get('live_proof_monotonic')
            digest=meta.get('png_sha256')
            if (meta.get('status')!='active_qnx_source_verified' or
                    meta.get('frame_source')!='original_vm2_dpum' or meta.get('dpu_sha256')!=MEMBER_SHA256 or
                    tuple(meta.get(key) for key in ('port','pipeline','pipe','dsi','width','height'))!=
                    (port,pipeline,pipe,dsi,width,height) or meta.get('format')!='ARGB8888' or
                    meta.get('secure') is not False or meta.get('nonzero') is not True or
                    type(meta.get('scanned_frames')) is not int or meta['scanned_frames']<1 or
                    type(stamp) not in (int,float) or type(proof) not in (int,float) or
                    not 0<=now-stamp<=now-proof<20 or not isinstance(digest,str) or
                    not re.fullmatch(r'[a-f0-9]{64}',digest)):
                return None
            expected=f'native-{name}-{digest}.png'
            if meta.get('png')!=expected:
                return None
            frame=(self.native.session/expected).resolve(strict=True)
            if frame.parent!=self.native.session:
                return None
            with frame.open('rb') as stream:
                data=stream.read((8<<20)+1)
            if (not 33<=len(data)<=8<<20 or data[:8]!=b'\x89PNG\r\n\x1a\n' or
                    data[12:16]!=b'IHDR' or struct.unpack_from('>II',data,16)!=(width,height) or
                    hashlib.sha256(data).hexdigest()!=digest):
                return None
            return (data,meta) if with_proof else data
        except (ValueError,OSError):
            return None

    def status(self):
        rows = {
            'center': {'title': '中控 / ABT', 'renderer': 'Linux Weston / 原厂 HMI',
                       'source': 'linux-qemu-diagnostic', 'available': not self.native and self.guests['linux'].status()['running'],
                       'factory_hmi_verified': False, 'reason': '仅绑定 Linux QEMU 诊断帧，尚未进入原厂中控 HMI。'},
            'ic': {'title': 'IC 仪表', 'renderer': 'QNX GTF / IID 或 MiniFID', 'source': None,
                   'available': False, 'factory_hmi_verified': False,
                   'reason': 'QNX 原厂运行时未启动；GTF → Screen → Exynos DPU 链路尚无实时帧。'},
            'hud': {'title': 'HUD', 'renderer': 'QNX GTF / HUD_MODEL + WDI', 'source': None,
                    'available': False, 'factory_hmi_verified': False,
                    'reason': 'QNX Screen 共享缓冲与 WDI 未运行；未绑定 HUD 实时输出。'}}
        if self.native:
            for name, reason in {
                'center': '未取得 Linux 中控的原厂实时画面。',
                'ic': '未取得原 QNX 仪表活动图层的实时画面。',
                'hud': '未取得原 QNX HUD/WDI 活动图层的实时画面。',
            }.items():
                rows[name].update(source=None, reason=reason)
            center=self.native_center(with_proof=True)
            if center is not None:
                _,proof=center
                gem=proof.get('status')=='active_gem_pages_verified'
                def recent(key):
                    value=proof.get(key)
                    return type(value) in (int,float) and 0<=time.monotonic()-value<20
                rows['center'].update(source='original-vm3-drm',available=True,original_scanout=True,
                    frame_version=proof['png_sha256'],
                    pixel_source='original-gem' if gem else 'original-sysmmu',
                    frame_change_verified=recent('last_pixel_change_monotonic'),
                    buffer_switch_verified=recent('last_buffer_switch_monotonic'),
                    reason=('本次原厂 Virtual-1 活动主图层的 GEM 逻辑像素；未验证物理显示。' if gem else
                            '来自本次原厂 Linux 的 Virtual-1 活动主图层；未据此认定 HMI 已完整启动。'))
            else:
                foreground=self.native_center_foreground(with_proof=True)
                if foreground is not None:
                    rows['center'].update(foreground_available=True,foreground_source='original-vm3-drm-argb',
                        frame_version=foreground[1]['png_sha256'],
                        reason='当前原厂中控前景；透明区域未合成，尚未取得完整主页。')
            for name in ('ic','hud'):
                qnx=self.native_qnx(name,with_proof=True)
                if qnx is not None:
                    rows[name].update(source='original-vm2-dpum',available=True,original_scanout=True,
                        frame_version=qnx[1]['png_sha256'],
                        reason='本次 QNX 原厂 Screen 活动单层和已提交 IDMA 源；不代表物理屏或合成已验收。')
        return rows

    @staticmethod
    def match_frame(sample, version):
        if sample is None:
            return None
        data,proof=sample
        if version is not None and proof['png_sha256']!=version:
            raise ValueError('原厂帧已切换，等待下一次状态刷新。')
        return data

    def frame(self, name, version=None):
        if name not in ('center','ic','hud'):
            raise ValueError('Unknown logical display')
        if name=='center' and self.native:
            data=self.match_frame(self.native_center(with_proof=True),version)
            if data is not None:
                return data
            raise ValueError('未取得本次原厂 Linux 的有效实时帧。')
        if name != 'center':
            if self.native:
                data=self.match_frame(self.native_qnx(name,with_proof=True),version)
                if data is not None:
                    return data
            raise ValueError(self.status()[name]['reason'])
        if version is not None:
            raise ValueError('诊断画面没有原厂帧版本。')
        return self.guests['linux'].frame()


def create_server(cfg, port, guests=None, token=None, native_session=None, native_interactive=False):
    if native_interactive and native_session is None:
        raise ValueError('Interactive mode requires a fresh native session')
    native = (NativeInteractiveConsole(native_session,cfg.get('serial_byte_delay_ms',3)) if native_interactive
              else NativeConsoleTap(native_session) if native_session is not None else None)
    guests = {} if native else guests if guests is not None else {name: Guest(cfg, name) for name in ('linux', 'android')}
    displays = Displays(guests, native=native)
    token = token or secrets.token_urlsafe(32)
    origin = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def reply(self, code, data, content_type='application/json; charset=utf-8'):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            if self.headers.get('Host') != f'127.0.0.1:{port}':
                return False
            if self.headers.get('Origin') not in (None, origin):
                return False
            supplied = self.headers.get('X-8704-Token', '')
            return secrets.compare_digest(supplied, token)

        def do_GET(self):
            if self.path == '/':
                return self.reply(200, (HERE/'console.html').read_bytes(), 'text/html; charset=utf-8')
            if not self.authorized():
                return self.reply(403, {'error': 'Invalid local session token/host/origin'})
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == '/api/status':
                    return self.reply(200, native.status() if native else {name: guest.status() for name, guest in guests.items()})
                if parsed.path == '/api/displays':
                    return self.reply(200, displays.status())
                if parsed.path in ('/api/display-frame','/api/center-foreground'):
                    query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
                    versions=query.get('version')
                    if versions is not None and (len(versions)!=1 or not re.fullmatch(r'[a-f0-9]{64}',versions[0])):
                        raise ValueError('无效的原厂帧版本。')
                    version=versions[0] if versions else None
                    if parsed.path == '/api/display-frame':
                        return self.reply(200, displays.frame(query.get('display',['center'])[0],version), 'image/png')
                    frame=displays.match_frame(displays.native_center_foreground(with_proof=True),version)
                    if frame is None:
                        raise ValueError('本次没有可验证的原厂中控前景。')
                    return self.reply(200, frame, 'image/png')
                if parsed.path == '/api/frame':
                    name = urllib.parse.parse_qs(parsed.query).get('guest', ['linux'])[0]
                    return self.reply(200, guests[name].frame(), 'image/png')
                self.reply(404, {'error': 'Not found'})
            except (ValueError, OSError, RuntimeError, KeyError) as error:
                self.reply(409, {'error': str(error)})

        def do_POST(self):
            if not self.authorized():
                return self.reply(403, {'error': 'Invalid local session token/host/origin'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 16384:
                    raise ValueError('Body size must be 1..16384 bytes')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('JSON body must be an object')
                if self.path == '/api/shutdown':
                    self.reply(200, {'stopping': True})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
                if native:
                    if native_interactive and self.path=='/api/serial':
                        return self.reply(200,native.serial(body['guest'],body['command']))
                    if native_interactive and self.path=='/api/native-return':
                        return self.reply(200,native.return_key(body['guest']))
                    raise ValueError('Native console attachment is read-only; guest input focus is not verified')
                guest = guests[body['guest']]
                if self.path == '/api/start':
                    if any(key in body and not isinstance(body[key], bool) for key in ('original_dtb', 'core_id_shim', 'skip_boot_logger')):
                        raise ValueError('Boot flags must be JSON booleans')
                    return self.reply(200, guest.start(bool(body.get('original_dtb', False)), bool(body.get('core_id_shim', False)), bool(body.get('skip_boot_logger', False))))
                if self.path == '/api/stop':
                    return self.reply(200, guest.stop())
                if self.path == '/api/serial':
                    guest.serial(body['command'])
                    return self.reply(200, {'sent': True, 'execution_verified': False})
                if self.path == '/api/monitor':
                    return self.reply(200, {'output': guest.monitor(body['command'])})
                if self.path == '/api/diagnose':
                    guest.memory_diagnostic()
                    return self.reply(200, guest.status()['boot_diagnostic'])
                self.reply(404, {'error': 'Not found'})
            except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
                self.reply(409, {'error': str(error)})

    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        if native_interactive:
            native.close()
        raise
    if native_interactive:
        server.native_console=native
    port = server.server_address[1]
    origin = f'http://127.0.0.1:{port}'
    return server, guests, token


def serve(cfg, port, native_session=None, native_interactive=False):
    server, guests, token = create_server(cfg, port, native_session=native_session,
                                         native_interactive=native_interactive)
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    state_path = native_session/'native-console-server.json' if native_session is not None else cfg['assets']/'server.json'
    save(state_path, {'pid': os.getpid(), 'url': origin+'/#token='+token, 'port': server.server_address[1]})
    print(origin+'/#token='+token, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if native_interactive:
            server.native_console.close()
        for guest in guests.values():
            guest.stop()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=HERE/'config.json')
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('prepare')
    command = sub.add_parser('inspect-memory')
    command.add_argument('--input', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('probe')
    command.add_argument('--guest', choices=('linux', 'android'), required=True)
    command.add_argument('--seconds', type=int, default=30)
    command.add_argument('--original-dtb', action='store_true')
    command.add_argument('--capture-memory', action='store_true')
    adaptations = command.add_mutually_exclusive_group()
    adaptations.add_argument('--core-id-shim', action='store_true', help='Use a separately saved one-instruction single-CPU platform probe copy')
    adaptations.add_argument('--skip-boot-logger', action='store_true', help='Diagnostic copy returns immediately from vendor boot logger; not a complete platform port')
    command = sub.add_parser('serve')
    command.add_argument('--port', type=int)
    command.add_argument('--native-session', type=Path, help='Attach the page to an OEM vLM console capture')
    command.add_argument('--native-interactive', action='store_true',
                         help='Own the native UART and confirm live OEM input focus before forwarding')
    sub.add_parser('check-native-page', help='Verify deferred native page imports without starting a VM or opening a socket')
    args = parser.parse_args()
    if args.operation == 'check-native-page':
                                                                                
                                                                               
                                                                    
        from linux_early_uart import read_direct_binding
        from linux_local_shell import read_verified_proof as linux_proof
        from android_local_shell import read_verified_proof as android_proof
        from linux_pid1_context import LINUX_SHA
        from qnx_native_display import ROLES, MEMBER_SHA256
        if (not all(callable(x) for x in (read_direct_binding, linux_proof, android_proof)) or
                not re.fullmatch(r'[0-9a-f]{64}', LINUX_SHA) or not ROLES or not MEMBER_SHA256):
            raise ValueError('Incomplete native page dependency contract')
        load_config(args.config)
        return
    cfg = load_config(args.config)
    if args.operation == 'prepare':
        print(json.dumps(prepare(cfg), ensure_ascii=False, indent=2))
    elif args.operation == 'inspect-memory':
        records = printk_records(args.input.read_bytes())
        save(args.output, {'source': str(args.input.resolve()), 'source_sha256': sha(args.input), 'records': records})
        print(json.dumps({'validated_records': len(records), 'output': str(args.output)}))
    elif args.operation == 'serve':
        serve(cfg, args.port or cfg['http_port'], args.native_session, args.native_interactive)
    else:
        if not 1 <= args.seconds <= 180:
            raise ValueError('Probe duration must be 1..180 seconds')
        guest = Guest(cfg, args.guest)
        try:
            guest.start(args.original_dtb, args.core_id_shim, args.skip_boot_logger)
            deadline = time.monotonic()+args.seconds
            while time.monotonic() < deadline and guest.process.poll() is None:
                time.sleep(0.2)
            observations = {'status': guest.status()}
            if guest.process.poll() is None:
                for operation in ('info status', 'info registers'):
                    try:
                        observations[operation] = guest.monitor(operation)
                    except (OSError, RuntimeError) as error:
                        observations[operation] = {'error': str(error)}
                try:
                    guest.frame()
                except (OSError, RuntimeError, ValueError) as error:
                    observations['frame_error'] = str(error)
                if args.capture_memory:
                    try:
                        observations['memory_diagnostic'] = guest.memory_diagnostic()
                    except (OSError, RuntimeError, ValueError) as error:
                        observations['memory_error'] = str(error)
            save(guest.session/'probe.json', observations)
            print(json.dumps(observations, ensure_ascii=False, indent=2))
        finally:
            guest.stop()


if __name__ == '__main__':
    main()
