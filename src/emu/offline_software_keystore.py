import hashlib
import struct

SOURCE_SHA = '57f147bb4f9b444e40f9ff071a37856009b5e4a18d0f3f77377ff0f209994453'
CALL = 0x54fa4
ENTRY = 0x5d800
RX_SIZE = 0x47610
ORIGINAL_SIZE = 423776
STRINGS = 0x2e5c
STRINGS_SIZE = 0x54e6
APEX_LIBRARY = b'/apex/com.android.os.statsd/lib64/libstatssocket.so\0'
APEX_FILE = 0x68000
APEX_VADDR = 0x64000
TLS_HEADER = 0x40 + 6 * 56


def _string_section(raw, address):
    start = struct.unpack_from('<Q', raw, 40)[0]
    width, count = struct.unpack_from('<HH', raw, 58)
    if width != 64:
        raise ValueError('Unexpected original section layout')
    sections = [start + i * width for i in range(count)
                if struct.unpack_from('<I', raw, start + i * width + 4)[0] == 3
                and struct.unpack_from('<Q', raw, start + i * width + 16)[0] == address]
    if len(sections) != 1:
        raise ValueError('Require one original dynamic string section')
    return sections[0]


def _apex_link(original):
    raw = bytearray(original)
    empty_tls = struct.pack('<IIQQQQQQ', 7, 4, 0x5e000, 0x5e000, 0x5e000, 0, 0, 64)
    if raw[TLS_HEADER:TLS_HEADER + 56] != empty_tls:
        raise ValueError('Require the original empty TLS header')
    fields = {tag: (at, value) for at in range(0x613d8, 0x61768, 16)
              for tag, value in [struct.unpack_from('<QQ', raw, at)]}
    if any(fields.get(tag, (0, None))[1] != value
           for tag, value in [(5, STRINGS), (10, STRINGS_SIZE), (30, 8), (0x6ffffffb, 1)]):
        raise ValueError('Unexpected original strings or eager binding flags')
    strings = bytes(raw[STRINGS:STRINGS + STRINGS_SIZE]) + APEX_LIBRARY
    section = _string_section(raw, STRINGS)
    struct.pack_into('<QQQ', raw, section + 16, APEX_VADDR, APEX_FILE, len(strings))
    struct.pack_into('<Q', raw, fields[5][0] + 8, APEX_VADDR)
    struct.pack_into('<Q', raw, fields[10][0] + 8, len(strings))
    struct.pack_into('<QQ', raw, fields[30][0], 1, STRINGS_SIZE)
    struct.pack_into('<IIQQQQQQ', raw, TLS_HEADER, 1, 4, APEX_FILE, APEX_VADDR,
                     APEX_VADDR, len(strings), len(strings), 4096)
    raw.extend(bytes(APEX_FILE - len(raw)))
    raw.extend(strings)
    return bytes(raw)


def _restore_apex(private):
    size = STRINGS_SIZE + len(APEX_LIBRARY)
    expected = struct.pack('<IIQQQQQQ', 1, 4, APEX_FILE, APEX_VADDR,
                           APEX_VADDR, size, size, 4096)
    if (len(private) != APEX_FILE + size or
            private[TLS_HEADER:TLS_HEADER + 56] != expected or
            any(private[ORIGINAL_SIZE:APEX_FILE]) or
            private[APEX_FILE:] != private[STRINGS:STRINGS + STRINGS_SIZE] + APEX_LIBRARY):
        raise ValueError('Unexpected private APEX dependency segment')
    raw = bytearray(private[:ORIGINAL_SIZE])
    section = _string_section(raw, APEX_VADDR)
    struct.pack_into('<QQQ', raw, section + 16, STRINGS, STRINGS, STRINGS_SIZE)
    fields = {tag: (at, value) for at in range(0x613d8, 0x61768, 16)
              for tag, value in [struct.unpack_from('<QQ', raw, at)]}
    if fields[5][1] != APEX_VADDR or fields[10][1] != size:
        raise ValueError('Unexpected private APEX string references')
    needed = [at for at in range(0x613d8, 0x61768, 16)
              if struct.unpack_from('<QQ', raw, at) == (1, STRINGS_SIZE)]
    if len(needed) != 1:
        raise ValueError('Require the one original APEX dependency')
    struct.pack_into('<Q', raw, fields[5][0] + 8, STRINGS)
    struct.pack_into('<Q', raw, fields[10][0] + 8, STRINGS_SIZE)
    struct.pack_into('<QQ', raw, needed[0], 30, 8)
    struct.pack_into('<IIQQQQQQ', raw, TLS_HEADER, 7, 4, 0x5e000, 0x5e000, 0x5e000, 0, 0, 64)
    return bytes(raw)
ASM = '''stp x29, x30, [sp, #-64]!
mov x29, sp
stp x19, x20, [sp, #16]
mov x19, x8
bl #0x501d4
ldr x0, [x19, #8]
cbnz x0, done
ldr x0, [x19]
cbz x0, done
stp xzr, xzr, [sp, #32]
str xzr, [sp, #48]
add x8, sp, #32
bl #0x501d4
ldr x0, [sp, #32]
cbz x0, release
add x0, x19, #8
add x1, sp, #32
bl #0x50c38
release:
add x0, sp, #32
bl #0x50acc
done:
ldp x19, x20, [sp, #16]
ldp x29, x30, [sp], #64
ret'''


def _code():
    from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN
    ks = Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN)
    return bytes(ks.asm(ASM, addr=ENTRY)[0]), bytes(ks.asm(f'bl #{ENTRY}', addr=CALL)[0])


def _rx_header(raw):
    phoff = struct.unpack_from('<Q', raw, 32)[0]
    entsize, count = struct.unpack_from('<HH', raw, 54)
    if entsize != 56 or count != 12:
        raise ValueError('Unexpected original keystore program headers')
    found = [phoff + i * entsize for i in range(count)
             if struct.unpack_from('<II', raw, phoff + i * entsize) == (1, 5)]
    if len(found) != 1:
        raise ValueError('Require one original RX segment')
    return found[0]


def build(original):
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA:
        raise ValueError('Require exact original keystore')
    code, call = _code()
    if ENTRY + len(code) >= 0x5e000 or any(original[0x5d610:0x5e000]):
        raise ValueError('Original executable padding is not available')
    ph = _rx_header(original)
    if (struct.unpack_from('<QQ', original, ph + 32) != (RX_SIZE, RX_SIZE) or
            original[CALL:CALL + 4] != bytes.fromhex('8cecff97')):
        raise ValueError('Unexpected original initialization call or RX extent')
    result = bytearray(original)
    result[ENTRY:ENTRY + len(code)] = code
    result[CALL:CALL + 4] = call
    size = ENTRY + len(code) - 0x16000
    struct.pack_into('<QQ', result, ph + 32, size, size)
    result = _apex_link(bytes(result))
    if restore(result) != original:
        raise ValueError('Private keystore failed exact rollback')
    return result, {'original_sha256': SOURCE_SHA,
                    'private_sha256': hashlib.sha256(result).hexdigest(),
                    'changed_call': hex(CALL), 'routing_entry': hex(ENTRY),
                    'routing_bytes': len(code), 'security_level': 'SOFTWARE',
                    'original_checks_preserved': True,
                    'apex_dependency': APEX_LIBRARY[:-1].decode('ascii'),
                    'apex_strings_readonly': True, 'eager_binding_preserved': True}


def restore(private):
    if len(private) > ORIGINAL_SIZE:
        private = _restore_apex(private)
    code, call = _code()
    ph = _rx_header(private)
    size = ENTRY + len(code) - 0x16000
    if (private[ENTRY:ENTRY + len(code)] != code or private[CALL:CALL + 4] != call or
            struct.unpack_from('<QQ', private, ph + 32) != (size, size)):
        raise ValueError('Unexpected private keystore routing')
    result = bytearray(private)
    result[ENTRY:ENTRY + len(code)] = bytes(len(code))
    result[CALL:CALL + 4] = bytes.fromhex('8cecff97')
    struct.pack_into('<QQ', result, ph + 32, RX_SIZE, RX_SIZE)
    if hashlib.sha256(result).hexdigest() != SOURCE_SHA:
        raise ValueError('Private keystore has unrelated changes')
    return bytes(result)
