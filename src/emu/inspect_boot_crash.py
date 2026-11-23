






import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
import original_8704_vm as vm


def inspect(kernel, memory):
    data, ram = kernel.read_bytes(), memory.read_bytes()
    records = vm.printk_records(ram)
    text = '\n'.join(row['text'] for row in records)
    pc = re.search(r'pc\s*:\s*(\w+)\+0x([0-9a-f]+)/0x([0-9a-f]+)', text)
    code = re.search(r'Code: ([0-9a-f ]+)\(([0-9a-f]{8})\)', text)
    if not pc or not code:
        raise ValueError('No consecutive crash records with symbol and Code signature')
    words = code[1].split()+[code[2]]
    signature = b''.join(struct.pack('<I', int(word, 16)) for word in words)
    positions = [match.start() for match in re.finditer(re.escape(signature), data)]
    if len(positions) != 1 or positions[0] % 4:
        raise ValueError('Crash Code signature is not a unique aligned original-image match')
    offset = positions[0]+len(signature)-4
    start, size = offset-int(pc[2], 16), int(pc[3], 16)
    if not 0 <= start < start+size <= len(data) or size > 16384:
        raise ValueError('Symbol extent outside original kernel')
    decoder = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    instructions = [{'file_offset': ins.address, 'bytes': ins.bytes.hex(), 'mnemonic': ins.mnemonic, 'operands': ins.op_str}
                    for ins in decoder.disasm(data[start:start+size], start)]
    if sum(len(bytes.fromhex(row['bytes'])) for row in instructions) != size:
        raise ValueError('Function did not decode completely')
    callees = []
    for ins in decoder.disasm(data[start:start+size], start):
        if ins.mnemonic != 'bl':
            continue
        target = int(ins.op_str.removeprefix('#'), 16)
        if not 0 <= target <= len(data)-64:
            continue
        callees.append({'callsite_file_offset': ins.address, 'target_file_offset': target,
                        'first_64_bytes': [{'file_offset': item.address, 'bytes': item.bytes.hex(),
                                            'mnemonic': item.mnemonic, 'operands': item.op_str}
                                           for item in decoder.disasm(data[target:target+64], target)]})
    calls = []
    for position in range(0, len(data)-3, 4):
        word = struct.unpack_from('<I', data, position)[0]
        if word & 0xfc000000 != 0x94000000:
            continue
        displacement = word & 0x3ffffff
        if displacement & (1 << 25):
            displacement -= 1 << 26
        if position+displacement*4 == start:
            calls.append(position)
    return {'kernel': str(kernel), 'kernel_sha256': hashlib.sha256(data).hexdigest(),
            'memory': str(memory), 'memory_sha256': hashlib.sha256(ram).hexdigest(),
            'record_count': len(records), 'boot_log': text,
            'symbol_reported_by_guest': pc[1], 'symbol_file_offset': start, 'symbol_bytes': size,
            'crash_instruction_file_offset': offset, 'signature_hex': signature.hex(),
            'direct_bl_callsite_file_offsets': calls, 'instructions': instructions, 'callees': callees,
            'limits': ['File offsets are correlated from exact guest Code bytes and symbol-relative offset; not live virtual addresses.',
                       'A unique code match and full decoding do not establish C types or all function side effects.',
                       'No firmware byte was modified; no boot workaround is applied.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest', choices=('linux', 'android'), required=True)
    parser.add_argument('--memory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cfg = vm.load_config()
    kernel = cfg['original']/'ivi/Image' if args.guest == 'linux' else cfg['assets']/'android-kernel'
    index = [json.loads(line) for line in (vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines()]
    if args.guest == 'linux':
        expected = next(row['sha256'] for row in index if row['name'].removeprefix('./') == 'ivi/Image')
    else:
        boot, vendor = cfg['original']/'android/boot.img', cfg['original']/'android/vendor_boot.img'
        for name, path in (('android/boot.img', boot), ('android/vendor_boot.img', vendor)):
            if vm.sha(path) != next(row['sha256'] for row in index if row['name'].removeprefix('./') == name):
                raise ValueError('Original boot identity mismatch')
        expected = hashlib.sha256(vm.android_parts(boot.read_bytes(), vendor.read_bytes())[0]).hexdigest()
    if vm.sha(kernel) != expected:
        raise ValueError('Kernel identity mismatch')
    result = inspect(kernel, args.memory)
    vm.save(args.output, result)
    print(json.dumps({'guest': args.guest, 'records': result['record_count'], 'symbol': result['symbol_reported_by_guest'],
                      'symbol_file_offset': result['symbol_file_offset'], 'bytes': result['symbol_bytes'],
                      'callsites': result['direct_bl_callsite_file_offsets'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
