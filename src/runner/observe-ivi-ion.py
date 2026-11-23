
import hashlib
import json
import struct


W, H, PITCH = 1560, 700, 6272
VISIBLE = W * 4
IMAGE_BYTES = H * PITCH
ALLOC_BYTES = (IMAGE_BYTES + 4095) & ~4095
LAYOUT = (W, H, PITCH, int.from_bytes(b'AB24', 'little'))
VMEMMAP = 0xfffffffeffe00000
ION_ABI = (
    ('ion_heap_map_kernel', 0xffffffc01074c8f8, 0x2a0,
     '1a0e3726ca29ab3f889844fffb1fe2954ed0adb6e608a565cb17c723cfc55f32'),
    ('ion_dma_buf_mmap', 0xffffffc01074d0a0, 0xa0,
     '038178d3c525f95734d7ff70491d864464e26ab208eb027acba95ff4650ca1d0'),
    ('sg_next', 0xffffffc0103b70c8, 36,
     'd9f00549eb3b82e2930ce41ff1eb976c0e0bb97e58441a3460641a1103f5c17b'),
    ('exynos_drm_gem_map', 0xffffffc0104f5bb0, 584,
     '62b586be444384774f4bea5bae6d3b40093835964aad4311be96653768714c02'),
)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sg_spans(sgt, memstart, size, read, ptr, backed):

    need(memstart % 4096 == 0 and 0 <= memstart < 1 << 48, 'Invalid VM3 physical base')
    counts = read(sgt + 8)
    mapped, original = counts & 0xffffffff, counts >> 32
    need(1 <= mapped <= original <= size // 4096, 'ION scatterlist count outside allocation')
    entry = ptr(sgt)
    seen = set()
    spans = []
    total = 0
    anchor = VMEMMAP - ((memstart >> 12) << 6)
    for i in range(original):
        need(entry not in seen, 'ION scatterlist loop')
        seen.add(entry)
        page = read(entry)
        fields = read(entry + 8)
        offset, length = fields & 0xffffffff, fields >> 32
        delta = (page & ~3) - anchor
        need(not page & 1 and bool(page & 2) == (i == original - 1) and
             delta >= 0 and not delta & 63 and not offset and
             0 < length <= size - total and length % 4096 == 0, 'Invalid original ION scatter entry')
        physical = (delta >> 6) * 4096
        need(backed(physical, length), 'ION image outside original VM3 RAM')
        if spans and spans[-1][0] + spans[-1][1] == physical:
            pa, previous = spans[-1]
            spans[-1] = pa, previous + length
        else:
            spans.append((physical, length))
        total += length
        if i != original - 1:
            entry += 0x20
            following = read(entry)
            if following & 1:
                need(not following & 2 and following & ~3, 'Invalid ION scatter chain link')
                entry = following & ~3
    ordered = sorted(spans)
    need(total == size and all(a + n <= b for (a, n), (b, _) in zip(ordered, ordered[1:])),
         'ION allocation coverage or alias check failed')
    return spans


def sample(dmabuf, ops, va, read, ptr, backed, read_frame, *, layout):
    if layout != (*LAYOUT, 0) or read(dmabuf) != ALLOC_BYTES:
        return dict(status='not_observed', reason='Original AB24 tuple is not the zero-layout-key profile')
    need(ops == va['dma_buf_ops'] == 0xffffffc010b42270 and
         ptr(ops + 0x50) == va['ion_dma_buf_mmap'], 'Original ION exporter callback differs from the pinned ABI')
    ion = ptr(dmabuf + 0x88)
                                                                             
                                                                            
    flags = read(ion + 0x18)
    size = read(ion + 0x28)
    need(not flags & ~3, f'ION allocation flags {flags:#x} outside the unprotected AB24 profile')
    need(size == ALLOC_BYTES, f'ION allocation size {size} differs from the AB24 allocation')
    spans = sg_spans(ptr(ion + 0x68), read(va['memstart_addr']), ALLOC_BYTES, read, ptr, backed)
    used = []
    left = IMAGE_BYTES
    for pa, size in spans:
        take = min(size, left)
        if take:
            used.append((pa, take))
            left -= take
    need(not left, 'Incomplete ION image')
    raster, gpu_snapshot = read_frame(tuple(used))
    need(len(raster) == IMAGE_BYTES, 'Short paused ION pixel read')
    return summarize(tuple(used), raster, gpu_snapshot, flags)


def summarize(spans, raster, gpu_snapshot, flags):

    content = hashlib.sha256()
    footprint = hashlib.sha256(struct.pack('<II', VISIBLE, H))
    rgb = alpha = opaque = rgb_without_alpha = idx = offset = 0
    for y in range(H):
        row = raster[y * PITCH:y * PITCH + VISIBLE]
        content.update(row)
        for (pixel,) in struct.iter_unpack('<I', row):
            rgb_present = bool(pixel & 0xffffff)
            a = pixel >> 24
            rgb += rgb_present
            alpha += bool(a)
            opaque += a == 255
            rgb_without_alpha += rgb_present and not a
        left, active, run = PITCH, VISIBLE, 0
        begin = 0
        while left:
            base, length = spans[idx]
            take = min(left, length - offset)
            address = base + offset
            visible = min(take, active)
            if visible:
                if run and begin + run != address:
                    footprint.update(struct.pack('<QI', begin, run))
                    run = 0
                if not run:
                    begin = address
                run += visible
                active -= visible
            left -= take
            offset += take
            if offset == length:
                idx += 1
                offset = 0
        footprint.update(struct.pack('<QI', begin, run))
    f, c = footprint.hexdigest(), content.hexdigest()
    match = dict(status='gpu_history_unavailable')
    if isinstance(gpu_snapshot, str) and len(gpu_snapshot) <= 8192:
        try:
            snapshot = json.loads(gpu_snapshot)
            if snapshot.get('version') == 1 and 0 <= len(snapshot['targets']) <= 16:
                writes = [r for r in snapshot['targets'] if r['footprint'] == f]
                match = dict(status='no_matching_retained_target')
                if len(writes) == 1:
                    r = writes[0]
                    generation, written, write_rgb = (r[k] for k in ('generation', 'bytes', 'rgb'))
                    clear = r['clear']
                    need(type(clear) is bool and all(type(k) is int for k in (generation, written, write_rgb))
                         and generation > 0 and 0 < written <= VISIBLE * H and 0 <= write_rgb <= W * H,
                         'Invalid GPU writer record')
                    source_content = (hashlib.sha256(bytes(VISIBLE * H)).hexdigest()
                                      if clear else r['content'])
                    full = written == VISIBLE * H
                    match = dict(status='matching_physical_rows', generation=generation,
                                 bytes=written, rgb_nonzero=write_rgb, clear=clear,
                                 latest_write_content_matches=(source_content == c if full and source_content else None))
        except (AttributeError, KeyError, TypeError, ValueError):
            match = dict(status='gpu_history_unavailable')
    return dict(status='original_ivi16_ab24_pixels_observed', source='original_ion_sg',
                width=W, height=H, pitch=PITCH, format='AB24', flags=flags,
                rgb_nonzero=rgb, alpha_nonzero=alpha, alpha_opaque=opaque,
                rgb_without_alpha=rgb_without_alpha, physical_run_count=len(spans),
                footprint_sha256=f, content_sha256=c, gpu_correlation=match)
