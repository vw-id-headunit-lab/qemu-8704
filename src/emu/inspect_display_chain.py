







import argparse
import hashlib
import json
import mmap
from pathlib import Path, PurePosixPath
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'var/inputs/analysis'
sys.path.insert(0, str(BASE/'qnx-container-20261020'))
sys.path.insert(0, str(BASE))
from inspect_qnx6 import Qnx6
from inspect_debug_device_tree import parse_dtb, render
from dissect.extfs import ExtFS

WORDS = re.compile(r'gtf|hmi|iid|hud|cluster|screen|display|weston|vgpu|dpu|mali|egl|wayland|graphics|rsi|Virtual-|DSI|DP-|connector', re.I)
TEXT = re.compile(rb'[\x20-\x7e]{5,}')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def selected(name):
    lowered = name.lower()
    if '/resources/extensions/' in lowered and lowered.endswith(('/libmodeladapter.so', '/libvariantdefinition.so', '/librsiclientsearly.so', '/librsiclientslate.so')):
        return True
    if '/resources/' in lowered or '/test_patterns/' in lowered or '/shaders/' in lowered:
        return False
    if lowered.endswith(('.sh', '.conf', '.ini', '.json', '.mose', '.service')):
        return bool(WORDS.search(name)) or lowered.endswith(('startup.sh', 'early_init_qtd.sh'))
    return lowered.endswith(('/gtfstartup', '/cluster_services', '/screen', '/vgpu-arbiter', '/dpu_driver',
                             '/ti941_iid', '/displaymgr', '/wdi', '/libdisplaymanagement.so', '/libdisplayspi.so', '/boot/.script'))


def retain(output, system, name, raw, records, expected=None):
    parts = PurePosixPath(name.lstrip('/'))
    if '..' in parts.parts or any(':' in part or '\\' in part for part in parts.parts):
        raise ValueError('Unsafe member name')
    actual = digest(raw)
    if expected and actual != expected:
        raise ValueError('Member differs from retained inventory: '+name)
    target = output/system/Path(*parts.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != raw:
                                                                                 
                                                                                   
        target = output/system/'occurrences'/actual[:16]/Path(*parts.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Digest-scoped output collision: '+str(target))
    if not target.exists():
        target.write_bytes(raw)
    text = raw.decode('utf-8', errors='replace') if target.suffix in ('.sh', '.conf', '.ini', '.json', '.mose', '.service') else None
    hits = []
    if text is not None:
        hits = [{'line': number, 'text': line[:1600]} for number, line in enumerate(text.splitlines(), 1) if WORDS.search(line)]
    else:
        hits = [{'file_offset': match.start(), 'text': match.group().decode('ascii')[:1000]}
                for match in TEXT.finditer(raw) if WORDS.search(match.group().decode('ascii'))]
    records.append({'system': system, 'path': name, 'bytes': len(raw), 'sha256': actual,
                    'extracted': str(target.relative_to(ROOT)), 'hits': hits})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT/'var'):
        raise ValueError('Output must be under project scratch')
    out.mkdir(parents=True, exist_ok=True)
    records, identities = [], []
    for kind, relative in [('qnx-ifs', 'qnx-ifs-20261021T125600Z/findings.json'),
                           ('qnx6', 'qnx-container-20261020/filesystem-inventory.json')]:
        inventory = json.loads((BASE/relative).read_text(encoding='utf-8'))
        source = ROOT/inventory['source']
        with source.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            actual = digest(data)
            if actual != inventory['sha256']:
                raise ValueError('Image identity changed: '+kind)
            identities.append({'system': kind, 'source': str(source.relative_to(ROOT)), 'sha256': actual})
            fs = Qnx6(data) if kind == 'qnx6' else None
            for entry in inventory['entries']:
                name = entry['name'] if kind == 'qnx-ifs' else entry['path']
                regular = entry['kind'] == 'reg' if kind == 'qnx-ifs' else entry['type'] == 'regular'
                size = entry.get('file_size', 0) if kind == 'qnx-ifs' else entry['size']
                if not regular or not selected(name) or not 0 < size <= 32*1024*1024:
                    continue
                if fs:
                    raw = fs.read(fs.inode(entry['inode']), 0, size)
                else:
                    position = entry['absolute_offset']
                    if not 0 <= position <= len(data)-size:
                        raise ValueError('IFS member out of bounds')
                    raw = data[position:position+size]
                retain(out, kind, name, raw, records, entry.get('sha256'))
    source = BASE/'cpu/ivi/icas3cn-gp-image-euto-v9.ext4'
    index = [json.loads(line) for line in (BASE/'cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines()]
    expected = next(row['sha256'] for row in index if row['name'] == './ivi/'+source.name)
    with source.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('IVI image identity changed')
        identities.append({'system': 'linux', 'source': str(source.relative_to(ROOT)), 'sha256': actual})
        stream.seek(0)
        fs = ExtFS(stream)
        inventory = json.loads((BASE/'linux-root.inventory.json').read_text(encoding='utf-8'))
        for entry in inventory['inventory']:
            if entry.get('type') != 32768 or not selected(entry['path']):
                continue
            node = fs.get(entry['path'])
            if not 0 < node.size <= 32*1024*1024:
                continue
            with node.open() as content:
                raw = content.read()
            retain(out, 'linux', entry['path'], raw, records)
    trees = []
    for relative in ('ivi/icas3cn-gp_b0_zero.dtb', 'qnx/qtd/icas3gp-qla-vm.dtb', 'android/vendor_boot.dtb'):
        source = BASE/'cpu'/relative
        raw = source.read_bytes()
        entry = next((row for row in index if row['name'].removeprefix('./') == relative), None)
        if entry is None and relative == 'android/vendor_boot.dtb':
            vendor = ROOT/'var/inputs/original/android/vendor_boot.img'
            content = vendor.read_bytes()
            original = next(row for row in index if row['name'].removeprefix('./') == 'android/vendor_boot.img')
            if digest(content) != original['sha256'] or content[:8] != b'VNDRBOOT':
                raise ValueError('Vendor boot identity changed')
            page, size, header, dtb_size = (struct.unpack_from('<I', content, offset)[0] for offset in (12, 24, 2096, 2100))
            start = ((header+page-1)//page+(size+page-1)//page)*page
            expected = digest(content[start:start+dtb_size])
        elif entry:
            expected = entry['sha256']
        else:
            raise ValueError('DTB has no retained source identity: '+relative)
        if digest(raw) != expected:
            raise ValueError('DTB identity changed: '+relative)
        nodes = parse_dtb(raw)
        chosen = {name: {key: render(value) for key, value in properties.items()}
                  for name, properties in nodes.items()
                  if name in ('/', '/chosen', '/aliases') or re.search('display|framebuffer|vdpu|vgpu|vdev|hypervisor|gpu|drm|dpu|decon|wfd', name, re.I)
                  or re.search(b'display|framebuffer|vdpu|vgpu|vdev|hypervisor|gpu|drm|dpu|decon|wfd', properties.get('compatible', b''), re.I)}
        trees.append({'source': str(source.relative_to(ROOT)), 'sha256': digest(raw), 'nodes': chosen})
    (out/'display-device-trees.json').write_text(json.dumps(trees, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    platform = []
    for name in ('hyp-binary/hv.img', 'hyp-binary/hypervisor', 'hyp-binary/hypervisor-debug'):
        source = out.parent/'original-platform'/name
        if not source.exists():
            continue
        raw = source.read_bytes()
        expected = next(row['sha256'] for row in index if row['name'].removeprefix('./') == name)
        if digest(raw) != expected:
            raise ValueError('Hypervisor member identity changed')
        item = {'source': str(source.relative_to(ROOT)), 'sha256': expected, 'bytes': len(raw),
                'hits': [{'file_offset': match.start(), 'text': match.group().decode('ascii')[:1000]}
                         for match in TEXT.finditer(raw)
                         if re.search(rb'vLM|NKDDI|hypervisor|Exynos|vdpu|vgpu|QNX|VLX|nk.version', match.group(), re.I)]}
        if raw[:6] == b'\x7fELF\x02\x01':
            header = struct.unpack_from('<HHIQQQIHHHHHH', raw, 16)
            item['elf'] = {'machine': header[1], 'entry': hex(header[3]), 'segments': []}
            for number in range(header[9]):
                position = header[4]+number*header[8]
                if position > len(raw)-56:
                    raise ValueError('Hypervisor ELF program table outside file')
                kind, flags, offset, virtual, physical, file_size, memory_size, alignment = struct.unpack_from('<IIQQQQQQ', raw, position)
                item['elf']['segments'].append({'type': kind, 'flags': flags, 'file_offset': offset,
                                               'virtual': hex(virtual), 'physical': hex(physical), 'file_bytes': file_size, 'memory_bytes': memory_size})
        platform.append(item)
    (out/'original-hypervisor.json').write_text(json.dumps(platform, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    report = {'inputs': identities, 'members': records, 'runtime_verified': False,
              'limits': ['Selected startup/configuration evidence; presence alone is not live display output.',
                         'Original IC/HUD roles require launch configuration and consumer verification, not filenames alone.']}
    (out/'members.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'members': len(records), 'inputs': identities, 'output': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
