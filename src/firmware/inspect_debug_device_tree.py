
import hashlib
import json
import re
import struct
from pathlib import Path

BASE = Path(__file__).resolve().parent


def parse_dtb(data):
    assert len(data) >= 40
    magic, total, struct_off, strings_off, _, version, _, _, strings_size, struct_size = struct.unpack_from('>10I', data)
    assert magic == 0xd00dfeed and version >= 17 and total <= len(data)
    assert struct_off + struct_size <= total and strings_off + strings_size <= total
    strings = data[strings_off:strings_off+strings_size]
    pos, end, stack, nodes = struct_off, struct_off+struct_size, [], {}
    while pos < end:
        token = struct.unpack_from('>I', data, pos)[0]
        pos += 4
        if token == 1:
            stop = data.index(b'\0', pos, end)
            stack.append(data[pos:stop].decode())
            pos = (stop+4) & ~3
            path = '/'+'/'.join(x for x in stack if x)
            assert path not in nodes
            nodes[path] = {}
        elif token == 2:
            assert stack
            stack.pop()
        elif token == 3:
            length, name_off = struct.unpack_from('>II', data, pos)
            pos += 8
            assert stack and pos+length <= end and name_off < len(strings)
            name = strings[name_off:strings.index(b'\0', name_off)].decode()
            value = data[pos:pos+length]
            nodes['/'+'/'.join(x for x in stack if x)][name] = value
            pos = (pos+length+3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            assert not stack
            return nodes
        else:
            raise ValueError(f'Unknown DTB token {token}')
    raise ValueError('No FDT_END')


def render(value):
    if not value:
        return True
    if value.rstrip(b'\0') and value.endswith(b'\0') and all(x == 0 or 32 <= x < 127 for x in value):
        parts = value.rstrip(b'\0').decode().split('\0')
        return parts[0] if len(parts) == 1 else parts
    return dict(hex=value.hex(), cells=list(struct.unpack('>'+str(len(value)//4)+'I', value)) if len(value)%4 == 0 else None)


def main():
    paths = [BASE/'cpu/ivi/icas3cn-gp_b0_zero.dtb', BASE/'cpu/qnx/qtd/icas3gp-qla-vm.dtb']
    reports = []
    for path in paths:
        raw = path.read_bytes()
        nodes = parse_dtb(raw)
        selected = {}
        for name, props in nodes.items():
            compat = props.get('compatible', b'')
            if name in ('/', '/aliases', '/chosen') or re.search('usb|dwc3|ethernet|serial|uart|console|gpio_default|vnet|vevdev', name, re.I) or re.search(b'usb|dwc3|ethernet|vevdev', compat, re.I):
                selected[name] = {k: render(v) for k, v in props.items()}
        reports.append(dict(path=str(path.relative_to(BASE)), sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), parsed_nodes=len(nodes), selected_nodes=selected))
    report = dict(scope='Static DTB structural parsing; node addresses are SoC/controller identifiers, not connector pins or wiring proof', images=reports, runtime_verified=False)
    (BASE/'debug-device-tree.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    for row in reports:
        print(row['path'], row['parsed_nodes'], 'nodes')
        for name, props in row['selected_nodes'].items():
            if any(k in name for k in ('usb@', 'ethernet@', 'vevdev', 'aliases', 'chosen')):
                print(name, {k:v for k,v in props.items() if k in ('compatible','status','dr_mode','phy-mode','bootargs','stdout-path','ethernet0','ethernet1','serial0')})


if __name__ == '__main__':
    main()
