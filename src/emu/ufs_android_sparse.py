
from dataclasses import dataclass
import hashlib
import struct
import zlib

SPARSE = 0xed26ff3a
RAW, FILL, HOLE, CRC = 0xcac1, 0xcac2, 0xcac3, 0xcac4
BUFFER = 4 << 20


@dataclass(frozen=True)
class SparseDigest:
    size: int
    input_size: int
    input_sha256: str
    output_sha256: str
    crc32: int
    chunks: int


def expand(source, target, offset, capacity):

    if offset < 0 or capacity <= 0 or offset % 4096 or capacity % 4096:
        raise ValueError('Require aligned bounded destination')
    encoded = hashlib.sha256()
    decoded = hashlib.sha256()
    encoded_size = 0
    crc = 0

    def take(count):
        nonlocal encoded_size
        data = source.read(count)
        if len(data) != count:
            raise EOFError('Short Android sparse member')
        encoded.update(data)
        encoded_size += count
        return data

    magic, major, minor, hs, cs, block, nblock, nseg, expected_crc = struct.unpack('<I4H4I', take(28))
    size = block*nblock
    if (magic != SPARSE or (major, minor) != (1, 0) or block != 4096 or
            not 28 <= hs <= 4096 or not 12 <= cs <= 4096 or size != capacity or
            not 0 < nseg <= min(nblock*2+1, 1_000_000)):
        raise ValueError('Unexpected Android sparse version, header or extent')
    take(hs-28)
    pos = 0
    for _ in range(nseg):
        kind, reserved, blocks, total = struct.unpack('<HHII', take(12))
        take(cs-12)
        out_size = blocks*block
        if reserved or pos+out_size > size or total < cs:
            raise ValueError('Out-of-range Android sparse chunk')
        if kind == CRC and not blocks and total == cs+4:
            if struct.unpack('<I', take(4))[0] != crc:
                raise ValueError('Android sparse chunk CRC mismatch')
            continue
        if not blocks or (kind, total-cs) not in ((RAW, out_size),(FILL,4),(HOLE,0)):
            raise ValueError('Unsupported or malformed Android sparse chunk')
        fill = (take(4) if kind == FILL else bytes(4)) if kind != RAW else None
        repeated = fill*(min(out_size,BUFFER)//4) if fill is not None else None
        if kind == RAW or any(fill):
            target.seek(offset+pos)
        remaining = out_size
        while remaining:
            count = min(remaining,BUFFER)
            data = take(count) if kind == RAW else repeated[:count]
            if kind == RAW or any(fill):
                if target.write(data) != count:
                    raise OSError('Short Android sparse destination write')
            decoded.update(data)
            crc = zlib.crc32(data, crc)
            remaining -= count
        pos += out_size
    if pos != size or (expected_crc and expected_crc != crc) or source.read(1):
        raise ValueError('Android sparse image length, CRC or trailer mismatch')
    return SparseDigest(size, encoded_size, encoded.hexdigest(), decoded.hexdigest(), crc, nseg)

