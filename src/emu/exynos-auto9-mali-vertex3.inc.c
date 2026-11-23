/* SPDX-License-Identifier: GPL-2.0-or-later
 * Descriptor-bound VM3 execution of the pinned original vertex shaders.
 * Included after VM3 DMA helpers and the exact shader's host arithmetic.
 */
typedef struct Auto9Vm3Vertex3Span {
    uint64_t pa;
    unsigned length;
    const uint8_t *data;
} Auto9Vm3Vertex3Span;

static bool auto9_vm3_vertex_read(Auto9MaliG3d1 *s, uint64_t va,
                                  uint8_t *data, unsigned size,
                                  Auto9Vm3ClearSpan *input, unsigned cap, unsigned *used)
{
    unsigned first = *used;
    if (!auto9_vm3_clear_plan(s, va, 1, 0, size, false, input, cap, used)) {
        return false;
    }
    for (unsigned i = first; i < *used; i++) {
        if (address_space_read(&address_space_memory, input[i].pa,
                               MEMTXATTRS_UNSPECIFIED, data, input[i].length) != MEMTX_OK) {
            return false;
        }
        data += input[i].length;
    }
    return true;
}

static bool auto9_vm3_vertex3_read(Auto9MaliG3d1 *s, uint64_t va,
                                   uint8_t *data, unsigned size,
                                   Auto9Vm3ClearSpan input[64], unsigned *used)
{
    return auto9_vm3_vertex_read(s, va, data, size, input, 64, used);
}

/* Resolve the attribute record's selected, ordinary 1-D buffer. Reject
 * signed offsets, wrapping, instancing modes and truncated fourth records. */
static bool auto9_vm3_vertex3_address(const uint8_t rec[16], uint32_t offset,
                                      unsigned first, unsigned width, unsigned step,
                                      bool output, uint64_t *base)
{
    uint64_t tagged = ldq_le_p(rec);
    uint32_t length = ldl_le_p(rec + 12);
    /* Type 1 outputs don't divide an instance index. The original 400 B
     * and 560 B producers leave unrelated data in the divisor byte. */
    const uint64_t tag_mask = output ? UINT64_C(0x00ff00000000003f) :
                                      UINT64_C(0xffff00000000003f);
    uint64_t start = (uint64_t)first * step + offset;
    uint64_t extent = start + 3 * step + width;
    if ((tagged & tag_mask) != 1 || (uint32_t)ldl_le_p(rec + 8) != step ||
        (step && width > step) || extent > length) {
        return false;
    }
    uint64_t address = tagged & UINT64_C(0x0000ffffffffffc0);
    if (!address || address >= (UINT64_C(1) << 48) ||
        extent > (UINT64_C(1) << 48) - address) {
        return false;
    }
    *base = address + start;
    return true;
}

static uint32_t auto9_vm3_position352_job(Auto9MaliG3d1 *s, uint64_t head,
                                          unsigned slot, uint64_t status_pa)
{
    uint8_t header[192], rsd[64], code[AUTO9_BIFROST_VM4_POSITION400_SIZE];
    uint8_t attr[8], vary[16], source[16], target[16], fau[128], raw[16];
    uint8_t encoded[4][16];
    Auto9Vm3ClearSpan reads[64];
    Auto9Vm3Vertex3Span writes[16];
    unsigned nread = 0, nwrite = 0;
    uint64_t input, output;
    float u[32];
    if (!s->vm3_control || slot != 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_vertex3_read(s, head, header, sizeof(header), reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (reads[0].pa != status_pa) {
        return G3D00_JOB_WRITE_FAULT;
    }
    const uint8_t *draw = header + 64;
    unsigned first = ldl_le_p(draw + 4);
    if (ldl_le_p(header + 32) != 3 || ldl_le_p(header + 36) != 0x38000000 ||
        ldl_le_p(draw) != 2 || ldl_le_p(draw + 8) || ldq_le_p(draw + 16) || first > 0x100000) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t state = ldq_le_p(draw + 56), uniforms = ldq_le_p(draw + 48);
    uint64_t attributes = ldq_le_p(draw + 72), varyings = ldq_le_p(draw + 88);
    uint64_t ab = ldq_le_p(draw + 64), vb = ldq_le_p(draw + 80);
    if (!state || !uniforms || !attributes || !varyings || !ab || !vb ||
        (state & 63) || ((attributes | varyings | uniforms) & 7) || ((ab | vb) & 31)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_vertex3_read(s, state, rsd, sizeof(rsd), reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    bool full_matrix = ldl_le_p(rsd + 48) == 0x66000;
    unsigned code_size = full_matrix ? AUTO9_BIFROST_VM4_POSITION400_SIZE :
                                       AUTO9_BIFROST_VM3_POSITION352_SIZE;
    unsigned nfau = full_matrix ? 24 : 32;
    uint64_t program = ldq_le_p(rsd);
    if (!program || (program & 15) || ldl_le_p(rsd + 8) ||
        ldl_le_p(rsd + 12) != 0x20001 || (!full_matrix && ldl_le_p(rsd + 48) != 0x86000)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_vertex3_read(s, program, code, code_size, reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, code_size);
    if (!digest || strcmp(digest, full_matrix ? AUTO9_BIFROST_VM4_POSITION400_SHA256 :
                                      AUTO9_BIFROST_VM3_POSITION352_SHA256)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_vertex3_read(s, attributes, attr, sizeof(attr), reads, &nread) ||
        !auto9_vm3_vertex3_read(s, varyings, vary, sizeof(vary), reads, &nread) ||
        !auto9_vm3_vertex3_read(s, ab, source, sizeof(source), reads, &nread) ||
        !auto9_vm3_vertex3_read(s, vb + 16 * (ldl_le_p(vary + 8) & 1),
                                target, sizeof(target), reads, &nread) ||
        !auto9_vm3_vertex3_read(s, uniforms, fau, nfau * 4, reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    static unsigned descriptor_traces;
    if (descriptor_traces++ < 8) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "Auto9 VM3 position352 descriptors: attr=%#x off=%u vary=%#x off=%u "
                      "src=%#x:%#x step=%u bytes=%u dst=%#x:%#x step=%u bytes=%u\n",
                      ldl_le_p(attr), ldl_le_p(attr + 4), ldl_le_p(vary + 8),
                      ldl_le_p(vary + 12), ldl_le_p(source + 4), ldl_le_p(source),
                      ldl_le_p(source + 8), ldl_le_p(source + 12),
                      ldl_le_p(target + 4), ldl_le_p(target),
                      ldl_le_p(target + 8), ldl_le_p(target + 12));
    }
    unsigned width = ldl_le_p(attr) == 0x2bc04200 ? 8 :
                     ldl_le_p(attr) == 0x2dc04200 ? 12 :
                     full_matrix && ldl_le_p(attr) == 0x2fc04200 ? 16 : 0;
    unsigned in_stride = ldl_le_p(source + 8), out_stride = ldl_le_p(target + 8);
    if (!width || (ldl_le_p(attr + 4) & 3) || (ldl_le_p(vary + 8) & ~1u) != 0x17800000 ||
        (ldl_le_p(vary + 12) & 3) || in_stride > 16384 || out_stride < 16 ||
        out_stride > 16384 ||
        !auto9_vm3_vertex3_address(source, ldl_le_p(attr + 4), first, width,
                                   in_stride, false, &input) ||
        !auto9_vm3_vertex3_address(target, ldl_le_p(vary + 12), first, 16,
                                   out_stride, true, &output)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned i = 0; i < nfau; i++) {
        uint32_t bits = ldl_le_p(fau + 4 * i);
        memcpy(&u[i], &bits, sizeof(bits));
    }
    for (unsigned n = 0; n < 4; n++) {
        float p[4] = {0, 0, 0, 1}, result[4];
        if (!auto9_vm3_vertex3_read(s, input + n * in_stride, raw, width, reads, &nread)) {
            return G3D00_JOB_READ_FAULT;
        }
        for (unsigned i = 0; i < width / 4; i++) {
            uint32_t bits = ldl_le_p(raw + i * 4);
            memcpy(&p[i], &bits, sizeof(bits));
        }
        if (!(full_matrix ? auto9_bifrost_vm4_position400(p, u, result) :
                           auto9_bifrost_vm3_position352(p, u, result)) || !auto9_bifrost_snap4(result)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned i = 0; i < 4; i++) {
            uint32_t bits;
            memcpy(&bits, &result[i], sizeof(bits));
            stl_le_p(encoded[n] + 4 * i, bits);
        }
        for (unsigned off = 0; off < 16;) {
            uint64_t va = output + n * out_stride + off, pa;
            unsigned length = MIN(16 - off, 4096 - (va & 4095));
            if (nwrite == G_N_ELEMENTS(writes) ||
                !auto9_g3d00_translate(s, va, length, true, &pa)) {
                return G3D00_JOB_WRITE_FAULT;
            }
            writes[nwrite++] = (Auto9Vm3Vertex3Span){pa, length, encoded[n] + off};
            off += length;
        }
    }
    for (unsigned i = 0; i < nwrite; i++) {
        for (unsigned j = 0; j < nread; j++) {
            if (auto9_g3d00_overlap(writes[i].pa, writes[i].length, reads[j].pa, reads[j].length)) {
                return G3D00_JOB_WRITE_FAULT;
            }
        }
        for (unsigned j = 0; j < i; j++) {
            if (auto9_g3d00_overlap(writes[i].pa, writes[i].length, writes[j].pa, writes[j].length)) {
                return G3D00_JOB_WRITE_FAULT;
            }
        }
    }
    for (unsigned i = 0; i < nwrite; i++) {
        if (address_space_write(&address_space_memory, writes[i].pa, MEMTXATTRS_UNSPECIFIED,
                                writes[i].data, writes[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    s->vertices_written += 4;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM%u position%u writeback: first=%u vertices=4\n",
                  s->control_vmid, full_matrix ? 400 : 352, first);
    return G3D00_JOB_DONE;
}

static uint32_t auto9_vm3_vertex3_job(Auto9MaliG3d1 *s, uint64_t head,
                                     unsigned slot, uint64_t status_pa)
{
    static const uint32_t attrs[] = { 0x2bc04200, 0x2fc00201, 0x2bc04200 };
    static const uint32_t attr_offsets[] = { 0, 16, 8 };
    static const uint32_t vars[] = { 0x17c39c00, 0x17800001, 0x37c00000, 0x2bc00000 };
    static const uint32_t vary_offsets[] = { 0, 0, 0, 8 };
    unsigned stride[] = { 8, 0, 16, 8 };
    unsigned width[] = { 8, 16, 16, 8 };
    uint8_t header[192], renderer[64], shader[AUTO9_BIFROST_VM3_VERTEX4_SIZE];
    uint8_t attr[24], vary[32], rec[16], fau[192], raw[16], c[16];
    uint8_t encoded_pos[4][16], encoded_vary[4][16];
    Auto9Vm3ClearSpan reads[64];
    Auto9Vm3Vertex3Span writes[16];
    uint64_t base[4];
    unsigned nread = 0, nwrite = 0;
    float uniforms[48];

    if (!s->vm3_control || slot != 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_vertex3_read(s, head, header, sizeof(header), reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (reads[0].pa != status_pa) {
        return G3D00_JOB_WRITE_FAULT;
    }
    const uint8_t *inv = header + 32, *draw = header + 64;
    uint32_t first = ldl_le_p(draw + 4);
    if (ldl_le_p(inv) != 3 || ldl_le_p(inv + 4) != 0x38000000 ||
        ldl_le_p(draw) != 2 || ldl_le_p(draw + 8) || ldq_le_p(draw + 16)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t state = ldq_le_p(draw + 56);
    if ((state & 63) || !auto9_vm3_vertex3_read(s, state, renderer, sizeof(renderer), reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (ldl_le_p(renderer + 12) == 0x20001 && (ldl_le_p(renderer + 48) == 0x86000 ||
        ldl_le_p(renderer + 48) == 0x66000)) {
        return auto9_vm3_position352_job(s, head, slot, status_pa);
    }
    bool next = ldl_le_p(renderer + 12) == 0x00040003 && ldl_le_p(renderer + 48) == 0xc6000;
    if (ldl_le_p(renderer + 8) || (!next && (ldl_le_p(renderer + 12) != 0x00030002 ||
        ldl_le_p(renderer + 48) != 0x76000))) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t program = ldq_le_p(renderer);
    unsigned code_size = next ? AUTO9_BIFROST_VM3_VERTEX4_SIZE : AUTO9_BIFROST_VM3_VERTEX3_SIZE;
    if ((program & 15) ||
        !auto9_vm3_vertex3_read(s, program, shader, code_size, reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, shader, code_size);
    if (!sha || strcmp(sha, next ? AUTO9_BIFROST_VM3_VERTEX4_SHA256 : AUTO9_BIFROST_VM3_VERTEX3_SHA256)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t a = ldq_le_p(draw + 72), v = ldq_le_p(draw + 88), u = ldq_le_p(draw + 48);
    unsigned nattr = next ? 3 : 2, nvary = next ? 4 : 3, nfau = next ? 48 : 28;
    if (((a | v | u) & 7) ||
        !auto9_vm3_vertex3_read(s, a, attr, 8 * nattr, reads, &nread) ||
        !auto9_vm3_vertex3_read(s, v, vary, 8 * nvary, reads, &nread) ||
        !auto9_vm3_vertex3_read(s, u, fau, 4 * nfau, reads, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    for (unsigned i = 0; i < nattr; i++) {
        if ((uint32_t)ldl_le_p(attr + 8 * i) != attrs[i] ||
            (uint32_t)ldl_le_p(attr + 8 * i + 4) != attr_offsets[i]) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    for (unsigned i = 0; i < nvary; i++) {
        if ((uint32_t)ldl_le_p(vary + 8 * i) != vars[i] ||
            (uint32_t)ldl_le_p(vary + 8 * i + 4) != vary_offsets[i]) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    if (next) {
        /* UV shares attr buffer 0 at byte 8; its output shares varying
         * buffer 0 at byte 8 after the color. Entry 0 has no ST_CVT. */
        width[0] = stride[3] = width[3] = 16;
    }
    for (unsigned i = 0; i < 4; i++) {
        const unsigned bind[4] = { 0, 1, 1, 2 };
        unsigned b = bind[i];
        uint64_t table = ldq_le_p(draw + (i < 2 ? 64 : 80));
        unsigned index = (i < 2 ? attrs[b] : vars[b]) & 511;
        if (!table || (table & 31) || table >= (UINT64_C(1) << 48) ||
            index >= 8 ||
            !auto9_vm3_vertex3_read(s, table + 16 * index, rec, sizeof(rec), reads, &nread)) {
            return G3D00_JOB_READ_FAULT;
        }
        if (next && !i) {
            stride[0] = ldl_le_p(rec + 8);
            if (stride[0] != 16 && stride[0] != 24) {
                return G3D00_JOB_CONFIG_FAULT;
            }
        }
        /* The two input records enable the draw start offset. The two
         * varying records use local invocation indices, starting at zero. */
        if (!auto9_vm3_vertex3_address(rec, i < 2 ? attr_offsets[b] : vary_offsets[b], i < 2 ? first : 0,
                                        width[i], stride[i], i >= 2, &base[i])) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    for (unsigned i = 0; i < nfau; i++) {
        uint32_t bits = ldl_le_p(fau + 4 * i);
        memcpy(&uniforms[i], &bits, sizeof(bits));
    }
    for (unsigned n = 0; n < 4; n++) {
        float p[3] = {0}, position[4], uv[2] = {0}, uv_out[2] = {0};
        if (!auto9_vm3_vertex3_read(s, base[0] + n * stride[0], raw, width[0], reads, &nread) ||
            !auto9_vm3_vertex3_read(s, base[1] + n * stride[1], c, width[1], reads, &nread)) {
            return G3D00_JOB_READ_FAULT;
        }
        /* Original RG32F input supplies x/y; LD_ATTR.v3 fills z with 0. */
        for (unsigned i = 0; i < 2; i++) {
            uint32_t bits = ldl_le_p(raw + 4 * i);
            memcpy(&p[i], &bits, sizeof(bits));
            if (next) {
                bits = ldl_le_p(raw + 8 + 4 * i);
                memcpy(&uv[i], &bits, sizeof(bits));
            }
        }
        bool computed = next ? auto9_bifrost_vm3_vertex4(p, uv, uniforms, ldl_le_p(fau + 128),
                                                        position, uv_out)
                             : auto9_bifrost_vm3_vertex3_position(p, uniforms, position);
        if (!computed || !auto9_bifrost_snap4(position)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned i = 0; i < 4; i++) {
            uint32_t bits;
            memcpy(&bits, &position[i], sizeof(bits));
            stl_le_p(encoded_pos[n] + 4 * i, bits);
            float color;
            bits = ldl_le_p(c + 4 * i);
            memcpy(&color, &bits, sizeof(bits));
            uint16_t half = auto9_bifrost_f32_half_rne(color);
            if ((half & 0x7c00) == 0x7c00) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            stw_le_p(encoded_vary[n] + 2 * i, half);
        }
        if (next) {
            for (unsigned i = 0; i < 2; i++) {
                uint32_t bits;
                memcpy(&bits, &uv_out[i], sizeof(bits));
                stl_le_p(encoded_vary[n] + 8 + 4 * i, bits);
            }
        }
        for (unsigned i = 2; i < 4; i++) {
            const uint8_t *data = i == 2 ? encoded_pos[n] : encoded_vary[n];
            uint64_t address = base[i] + n * stride[i];
            for (unsigned left = width[i]; left;) {
                unsigned part = MIN(left, 4096 - (address & 4095));
                uint64_t pa;
                if (nwrite == G_N_ELEMENTS(writes) ||
                    !auto9_g3d00_translate(s, address, part, true, &pa)) {
                    return G3D00_JOB_WRITE_FAULT;
                }
                writes[nwrite++] = (Auto9Vm3Vertex3Span){ pa, part, data };
                address += part;
                data += part;
                left -= part;
            }
        }
    }
    /* All outputs are planned before the first store; no input/descriptor
     * or status alias and no overlapping outputs may be overwritten. */
    for (unsigned i = 0; i < nwrite; i++) {
        const Auto9Vm3Vertex3Span *w = &writes[i];
        for (unsigned j = 0; j < nread; j++) {
            if (auto9_g3d00_overlap(w->pa, w->length, reads[j].pa, reads[j].length)) {
                return G3D00_JOB_WRITE_FAULT;
            }
        }
        for (unsigned j = 0; j < i; j++) {
            if (auto9_g3d00_overlap(w->pa, w->length, writes[j].pa, writes[j].length)) {
                return G3D00_JOB_WRITE_FAULT;
            }
        }
    }
    for (unsigned i = 0; i < nwrite; i++) {
        if (address_space_write(&address_space_memory, writes[i].pa, MEMTXATTRS_UNSPECIFIED,
                                writes[i].data, writes[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    s->vertices_written += 4;
    if (next) {
        if (++s->vm3_vertex4_jobs <= 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex4 writeback: n=%u first=%u stride=%u vertices=4 position-bytes=64 color-bytes=32 uv-bytes=32\n",
                s->vm3_vertex4_jobs, first, stride[0]);
        }
    } else {
        if (nwrite == 8) {
            uint64_t pa[4][2];
            for (unsigned row = 0; row < 4; row++) {
                pa[row][0] = writes[row * 2].pa;
                pa[row][1] = writes[row * 2 + 1].pa;
            }
            auto9_vm3_vertex3_origin(s, pa, encoded_pos, encoded_vary);
        }
        if (++s->vm3_vertex3_jobs <= 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex3 writeback: n=%u first=%u vertices=4 position-bytes=64 color-bytes=32\n",
                s->vm3_vertex3_jobs, first);
        }
    }
    return G3D00_JOB_DONE;
}

/* Bind the 448-byte shader by its instruction-selected attributes, not by
 * captured addresses, offsets or strides. Only the known linear formats
 * are supported. All loads/stores must agree with the live allocation. */
static int auto9_vm3_vertex448_span_cmp(const void *a, const void *b)
{
    uint64_t pa = ((const Auto9Vm3ClearSpan *)a)->pa;
    uint64_t pb = ((const Auto9Vm3ClearSpan *)b)->pa;
    return (pa > pb) - (pa < pb);
}

/* Sort the physical ranges, not the writes themselves. Input reads can
 * repeat or overlap; outputs must be disjoint from inputs and each other. */
static bool auto9_vm3_vertex448_disjoint(Auto9Vm3ClearSpan *reads, unsigned nread,
                                        Auto9Vm3ClearSpan *out, unsigned nout)
{
    qsort(reads, nread, sizeof(*reads), auto9_vm3_vertex448_span_cmp);
    qsort(out, nout, sizeof(*out), auto9_vm3_vertex448_span_cmp);
    unsigned r = 0;
    for (unsigned i = 0; i < nout; i++) {
        const Auto9Vm3ClearSpan *w = &out[i];
        if (i && out[i - 1].pa + out[i - 1].length > w->pa) {
            return false;
        }
        while (r < nread && reads[r].pa + reads[r].length <= w->pa) {
            r++;
        }
        if (r < nread && reads[r].pa < w->pa + w->length) {
            return false;
        }
    }
    return true;
}

static uint32_t auto9_vm3_vertex448_job(Auto9MaliG3d1 *s, uint64_t head,
                                       unsigned slot, uint64_t status_pa)
{
    /* Count variants observed for this exact 448 B shader. */
    enum { MAX_VERTICES = 150, META_SPANS = 28 };
    typedef uint8_t EncodedVertex[4][16];
    static const uint32_t formats[8] = {
        0x2bc04000, 0x2fc00000, 0x2bc04000, 0x2bc04000,
        0x17800000, 0x37c00000, 0x2bc00000, 0x2bc00000,
    };
    static const unsigned width[8] = { 8, 16, 8, 8, 16, 8, 8, 8 };
    uint8_t header[192], renderer[64], shader[AUTO9_BIFROST_VM3_VERTEX45_SIZE];
    uint8_t attr[32], vary[40], rec[16], fau[264], raw[16];
    Auto9Vm3ClearSpan prefix[6];
    unsigned nread = 0, nwrite = 0, stride[8];
    uint64_t base[8];
    float uniforms[66];

    if (!s->vm3_control || slot != 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_vertex_read(s, head, header, sizeof(header), prefix, G_N_ELEMENTS(prefix), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (prefix[0].pa != status_pa) {
        return G3D00_JOB_WRITE_FAULT;
    }
    const uint8_t *inv = header + 32, *draw = header + 64;
    unsigned last = ldl_le_p(inv), first = ldl_le_p(draw + 4);
    if ((last != 3 && last != 17 && last != 29 && last != 35 && last != 53 &&
         last != 71 && last != MAX_VERTICES - 1) ||
        ldl_le_p(inv + 4) != 0x38000000 ||
        ldl_le_p(draw) != 2 || ldl_le_p(draw + 8) || ldq_le_p(draw + 16)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    unsigned count = last + 1;
    uint64_t state = ldq_le_p(draw + 56);
    if ((state & 63) || !auto9_vm3_vertex_read(s, state, renderer, sizeof(renderer),
                                               prefix, G_N_ELEMENTS(prefix), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    unsigned preload = ldl_le_p(renderer + 48);
    bool vertex45 = last == 3 && ldl_le_p(renderer + 12) == 0x00050004 &&
                    (preload == 0xfe000 || preload == 0x12e000);
    if (ldl_le_p(renderer + 8) || (!vertex45 &&
        (last == 3 || ldl_le_p(renderer + 12) != 0x00040003 || preload != 0x6e000))) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    unsigned code_size = vertex45 ? AUTO9_BIFROST_VM3_VERTEX45_SIZE : AUTO9_BIFROST_VM3_VERTEX448_SIZE;
    unsigned fau_size = vertex45 ? 232 : 104;
    unsigned uv_count = vertex45 ? 2 : 1, inputs = 2 + uv_count, outputs = 2 + uv_count;
    const char *expected = vertex45 ? AUTO9_BIFROST_VM3_VERTEX45_SHA256 : AUTO9_BIFROST_VM3_VERTEX448_SHA256;
    uint64_t code = ldq_le_p(renderer);
    bool short_shader = false;
    unsigned before_code = nread;
    if (vertex45 && preload == 0x12e000 && code && !(code & 15) &&
        auto9_vm3_vertex_read(s, code, shader, AUTO9_BIFROST_VM3_VERTEX592_SIZE,
                              prefix, G_N_ELEMENTS(prefix), &nread)) {
        g_autofree char *short_digest = g_compute_checksum_for_data(
            G_CHECKSUM_SHA256, shader, AUTO9_BIFROST_VM3_VERTEX592_SIZE);
        short_shader = short_digest && !strcmp(short_digest, AUTO9_BIFROST_VM3_VERTEX592_SHA256);
    }
    if (short_shader) {
        code_size = AUTO9_BIFROST_VM3_VERTEX592_SIZE;
        fau_size = sizeof(fau);
        expected = AUTO9_BIFROST_VM3_VERTEX592_SHA256;
    }
    nread = before_code;
    if ((code & 15) || !auto9_vm3_vertex_read(s, code, shader, code_size,
                                              prefix, G_N_ELEMENTS(prefix), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256, shader, code_size);
    if (!digest || strcmp(digest, expected)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* Each input/output may straddle two pages. Metadata includes one
     * buffer record per binding and at most twelve total reads. */
    unsigned out_cap = 2 * outputs * count, read_cap = 2 * inputs * count + META_SPANS;
    g_autofree EncodedVertex *encoded = g_try_new(EncodedVertex, count);
    g_autofree Auto9Vm3ClearSpan *reads = g_try_new(Auto9Vm3ClearSpan, read_cap);
    g_autofree Auto9Vm3ClearSpan *out = g_try_new(Auto9Vm3ClearSpan, out_cap);
    g_autofree Auto9Vm3Vertex3Span *writes = g_try_new(Auto9Vm3Vertex3Span, out_cap);
    if (!encoded || !reads || !out || !writes) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    memcpy(reads, prefix, nread * sizeof(*reads));
    unsigned output_tags[4] = {0};
    bool local_448_outputs = true, local_dual_uv_outputs = true;
    bool bf_592_profile = short_shader && first == 0x11f8, bf_592 = false;
    /* At 0x11f8, the captured 592 B shader also used 0x44 on both
     * tightly packed outputs. Do not infer that layout for other jobs. */
    bool both44_592 = false;
    /* Index 87 keeps the position record untagged and puts 0x3f on
     * the shared color/UV record only. */
    bool uv3f_592 = false;
    bool uv03_592_profile = short_shader && first == 0x11fc, uv03_592 = false;
    bool pos44_592_profile = short_shader && first == 0x11f4, pos44_592 = false;
    bool uvf0_592 = false;
    bool pair1113_profile = short_shader && first == 0x11f0;
    bool pos3f_592 = false;
    unsigned tagged_608_mode = preload == 0xfe000 &&
                               (first == 0x2354 || first == 0x2358 || first == 0x235c ||
                                first == 0x23b4 || first == 0x23b8 || first == 0x23f0 ||
                                first == 0x22d0 || first == 0x22cc || first == 0x22d8 ||
                                first == 0x22c8 || first == 0x2330) ? 0xff :
                               preload == 0x12e000 && first == 0x11f0 ? 0x44 : 0;
    bool tagged_608_profile = vertex45 && !short_shader && tagged_608_mode;
    unsigned expected_input_stride = preload == 0xfe000 ? 24 : 32;
    uint64_t a = ldq_le_p(draw + 72), v = ldq_le_p(draw + 88), u = ldq_le_p(draw + 48);
    if (((a | v | u) & 7) ||
        !auto9_vm3_vertex_read(s, a, attr, 8 * inputs, reads, read_cap, &nread) ||
        !auto9_vm3_vertex_read(s, v, vary, 8 * (outputs + 1), reads, read_cap, &nread) ||
        !auto9_vm3_vertex_read(s, u, fau, fau_size, reads, read_cap, &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(width); i++) {
        bool output = i >= 4;
        if (!vertex45 && (i == 3 || i == 7)) {
            continue;
        }
        const uint8_t *entry = (output ? vary : attr) + 8 * (output ? i - 3 : i);
        uint32_t word = ldl_le_p(entry), offset = ldl_le_p(entry + 4);
        uint64_t table = ldq_le_p(draw + (output ? 80 : 64));
        unsigned index = word & 511, alignment = i == 5 ? 2 : 4;
        if ((word & ~1023U) != formats[i] || (output && (word & 512)) ||
            offset > INT32_MAX || offset % alignment || index >= 8) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!table || (table & 31) || table >= (UINT64_C(1) << 48) ||
            !auto9_vm3_vertex_read(s, table + 16 * index, rec, sizeof(rec),
                                    reads, read_cap, &nread)) {
            return G3D00_JOB_READ_FAULT;
        }
        uint64_t tagged = ldq_le_p(rec);
        unsigned divisor = tagged >> 56;
        uint64_t address = tagged & UINT64_C(0x0000ffffffffffc0);
        uint32_t size = ldl_le_p(rec + 12);
        uint64_t bias = (word & 512) ? first : 0;
        stride[i] = ldl_le_p(rec + 8);
        /* Type 1 uses linear indexing, not the divisor addressing modes.
         * Capture the high bytes only for this pinned shader's local outputs. */
        bool output_448 = !vertex45 && output;
        bool output_608 = tagged_608_profile && output;
        bool output_592 = (bf_592_profile || pos44_592_profile || uv03_592_profile || pair1113_profile) && output;
        if (output_448 || output_608 || output_592) {
            output_tags[i - 4] = divisor;
        }
        bf_592 |= bf_592_profile && output && divisor == 0xbf;
        both44_592 |= bf_592_profile && output && divisor == 0x44;
        uv3f_592 |= bf_592_profile && output && divisor == 0x3f;
        uv03_592 |= uv03_592_profile && output && divisor == 0x03;
        pos44_592 |= pos44_592_profile && i == 4 && divisor == 0x44;
        pos3f_592 |= pair1113_profile && i == 4 && divisor == 0x3f;
        if (output_448) {
            local_448_outputs &= stride[i] == 16 && size == count * 16 &&
                                 offset == (i == 6 ? 8 : 0);
        }
        if (output_608 || output_592) {
            unsigned local_stride = i == 4 ? 16 : 24;
            local_dual_uv_outputs &= stride[i] == local_stride && size == count * local_stride &&
                                     offset == (i < 6 ? 0 : i == 6 ? 8 : 16);
        }
        if ((tagged & UINT64_C(0x00ff00000000003f)) != 1 ||
            (divisor && !(short_shader && output && divisor == 0xff) &&
             !(bf_592_profile && output && (divisor == 0xbf || divisor == 0x44)) &&
             !(bf_592_profile && i > 4 && divisor == 0x3f) &&
             !(pos44_592_profile && i == 4 && divisor == 0x44) &&
             !(uv03_592_profile && i > 4 && divisor == 0x03) &&
             !(pos44_592_profile && i > 4 && divisor == 0xf0) &&
             !(pair1113_profile && output && divisor == (i == 4 ? 0x11 : 0x13)) &&
             !(pair1113_profile && i == 4 && divisor == 0x3f) &&
             !output_448 && !output_608) || !address ||
            address >= (UINT64_C(1) << 48) || offset > size || width[i] > size - offset ||
            stride[i] % alignment || (stride[i] ? stride[i] < width[i] : i != 1) ||
            (stride[i] && bias + last > (size - offset - width[i]) / stride[i])) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        uint64_t start = offset + bias * stride[i];
        uint64_t extent = start + last * (uint64_t)stride[i] + width[i];
        if (extent > (UINT64_C(1) << 48) - address) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        base[i] = address + start;
    }
    unsigned position_tag = output_tags[0], color_tag = output_tags[1];
    uvf0_592 = pos44_592_profile && color_tag == 0xf0;
    bool pair1113_592 = pair1113_profile && (position_tag == 0x11 || color_tag == 0x13 ||
                                           output_tags[2] == 0x13 || output_tags[3] == 0x13);
    bool has_tags = (position_tag | color_tag | output_tags[2] | output_tags[3]) != 0;
    bool tagged_448 = !vertex45 && has_tags;
    bool tagged_608 = tagged_608_profile && has_tags;
    if ((tagged_608 || bf_592 || both44_592 || uv3f_592 || pos44_592 ||
         uv03_592 || uvf0_592 || pair1113_592 || pos3f_592) && (!local_dual_uv_outputs ||
        stride[0] != expected_input_stride ||
        stride[1] || stride[2] != expected_input_stride || stride[3] != expected_input_stride ||
        base[2] != base[0] + 8 || base[3] != base[0] + 16 ||
        base[6] != base[5] + 8 || base[7] != base[5] + 16 ||
        color_tag != output_tags[2] || color_tag != output_tags[3])) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* Exact type-1 records from fully captured 608 B jobs. A varying
     * record's high byte does not change the linear writeback address. */
    bool mixed_608 = tagged_608 && preload == 0xfe000 &&
                     ((first == 0x22d0 && position_tag == 0 && color_tag == 0x1f) ||
                      (first == 0x22cc && position_tag == 0 && color_tag == 0x07) ||
                      (first == 0x22c8 && position_tag == 0 && color_tag == 0x1f) ||
                      (first == 0x2354 && position_tag == 0 && color_tag == 0x1f) ||
                      (first == 0x23b8 && ((position_tag == 0x44 && color_tag == 0x3e) ||
                                          (position_tag == 0x3f && color_tag == 0) ||
                                          (position_tag == 0xff && color_tag == 0xd0))) ||
                      (first == 0x23f0 && position_tag == 0 &&
                       (color_tag == 0xf0 || color_tag == 0xbf)));
    if ((tagged_608 && !mixed_608 &&
         (first == 0x22c8 ||
          position_tag != tagged_608_mode || color_tag != position_tag)) ||
        (bf_592 && !((position_tag == 0xbf && color_tag == 0) ||
                     (position_tag == 0 && color_tag == 0xbf))) ||
        (both44_592 && (position_tag != 0x44 || color_tag != 0x44)) ||
        (uv3f_592 && (position_tag != 0 || color_tag != 0x3f)) ||
        (uv03_592 && (position_tag != 0 || color_tag != 0x03)) ||
        (uvf0_592 && position_tag != 0) ||
        (pair1113_592 && (position_tag != 0x11 || color_tag != 0x13)) ||
        (pos3f_592 && color_tag != 0) ||
        (pos44_592 && color_tag != 0)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (tagged_448) {
        /* Observed (position, interleaved color/UV) records. Other counts,
         * mode pairs and storage layouts keep the existing strict rejection. */
        unsigned tags = (position_tag << 8) | color_tag;
        if (!local_448_outputs || color_tag != output_tags[2] ||
            !((count == 18 && (tags == 0xffff ||
                               (first == 0xbf58 && (tags == 0x3f00 || tags == 0x4400)))) ||
              (count == 30 && (tags == 0xffff || tags == 0x44c4 ||
                               (tags == 0x3f00 && (first == 0xbe56 || first == 0xbe20 || first == 0xbf3a)) ||
                               ((tags == 0x001f || tags == 0xff00) && first == 0xbf3a) ||
                               (tags == 0x00bf && first == 0xbfd0))) ||
              (count == 36 && (tags == 0x00bf || (tags == 0x4300 && first == 0xbfac) ||
                               (tags == 0xffff && first == 0xbef8))) ||
              (count == 54 && (tags == 0x00bf || tags == 0xc300 ||
                               ((first == 0xbe7a || first == 0xbec2) && tags == 0xffff) ||
                               (first == 0xbe3e && (tags == 0x4200 || tags == 0xffff ||
                                                   tags == 0xe6e6)))) ||
              (count == 72 && (tags == 0xffff || tags == 0x00bf || tags == 0xff00 ||
                               tags == 0x3f00 || tags == 0x4343)) ||
              (count == 150 && (tags == 0xc400 ||
                                (first == 0xbf6a && (tags == 0x3f00 || tags == 0xffff || tags == 0x4141)))))) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    for (unsigned i = 0; i < fau_size / 4; i++) {
        uint32_t bits = ldl_le_p(fau + 4 * i);
        memcpy(&uniforms[i], &bits, sizeof(bits));
    }
    for (unsigned n = 0; n < count; n++) {
        float p[3] = {0}, position[4], uv[2][2];
        for (unsigned i = 0; i < inputs; i++) {
            if (!auto9_vm3_vertex_read(s, base[i] + (uint64_t)n * stride[i], raw, width[i],
                                        reads, read_cap, &nread)) {
                return G3D00_JOB_READ_FAULT;
            }
            for (unsigned k = 0; k < width[i] / 4; k++) {
                uint32_t bits = ldl_le_p(raw + 4 * k);
                float value;
                memcpy(&value, &bits, sizeof(bits));
                if (!isfinite(value)) {
                    return G3D00_JOB_CONFIG_FAULT;
                }
                if (i == 0) {
                    p[k] = value; /* RG32F provides x/y, missing z is zero. */
                } else if (i == 1) {
                    uint16_t half = auto9_bifrost_f32_half_rne(value);
                    if ((half & 0x7c00) == 0x7c00) {
                        return G3D00_JOB_CONFIG_FAULT;
                    }
                    stw_le_p(encoded[n][1] + 2 * k, half);
                } else {
                    uv[i - 2][k] = value;
                }
            }
        }
        bool transformed = short_shader ?
            auto9_bifrost_vm3_vertex592(p, uv, uniforms, ldl_le_p(fau + 48), position) :
            vertex45 ? auto9_bifrost_vm3_vertex45(p, uv, uniforms, ldl_le_p(fau + 48), position) :
                       auto9_bifrost_vm3_vertex448(p, uniforms, position);
        if (!transformed || !auto9_bifrost_snap4(position)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned k = 0; k < 4; k++) {
            uint32_t bits;
            memcpy(&bits, &position[k], sizeof(bits));
            stl_le_p(encoded[n][0] + 4 * k, bits);
        }
        for (unsigned uv_idx = 0; uv_idx < uv_count; uv_idx++) {
            for (unsigned k = 0; k < 2; k++) {
                uint32_t bits;
                memcpy(&bits, &uv[uv_idx][k], sizeof(bits));
                stl_le_p(encoded[n][2 + uv_idx] + 4 * k, bits);
            }
        }
        for (unsigned i = 4; i < 4 + outputs; i++) {
            uint64_t address = base[i] + (uint64_t)n * stride[i];
            const uint8_t *data = encoded[n][i - 4];
            for (unsigned left = width[i]; left;) {
                unsigned part = MIN(left, 4096 - (address & 4095));
                uint64_t pa;
                if (nwrite == out_cap ||
                    !auto9_g3d00_translate(s, address, part, true, &pa)) {
                    return G3D00_JOB_WRITE_FAULT;
                }
                writes[nwrite++] = (Auto9Vm3Vertex3Span){ pa, part, data };
                address += part;
                data += part;
                left -= part;
            }
        }
    }
    for (unsigned i = 0; i < nwrite; i++) {
        out[i] = (Auto9Vm3ClearSpan){ writes[i].pa, writes[i].length };
    }
    /* O(n log n) preflight, followed by the unchanged guest write order. */
    if (!auto9_vm3_vertex448_disjoint(reads, nread, out, nwrite)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    for (unsigned i = 0; i < nwrite; i++) {
        if (address_space_write(&address_space_memory, writes[i].pa, MEMTXATTRS_UNSPECIFIED,
                                writes[i].data, writes[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    s->vertices_written += count;
    if (vertex45) {
        if ((pos44_592 || both44_592 || uv3f_592 || uv03_592 || uvf0_592 ||
             pair1113_592 || pos3f_592) &&
            s->vm3_vertex592_pos44_traces < 16) {
            unsigned n = ++s->vm3_vertex592_pos44_traces;
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex592 exact writeback: n=%u first=%u position=0x%x interleaved=0x%x "
                "input-stride=32 position-bytes=64 interleaved-bytes=96\n", n, first, position_tag, color_tag);
        }
        if (tagged_608 && s->vm3_vertex608_tagged_traces < 32) {
            unsigned n = ++s->vm3_vertex608_tagged_traces;
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex608 tagged writeback: n=%u first=%u mode=0x%x "
                "input-stride=%u position-bytes=64 interleaved-bytes=96 interleaved-tag=0x%x\n",
                n, first, position_tag, stride[0], color_tag);
        }
        if (++s->vm3_vertex45_jobs <= 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex45 writeback: n=%u first=%u vertices=%u position-bytes=%u color-bytes=%u uv-bytes=%u\n",
                s->vm3_vertex45_jobs, first, count, count * 16, count * 8, count * 16);
            if (short_shader) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 vertex592 selected: job=%u first=%u bf-layout=%u "
                    "position=0x%x interleaved=0x%x\n",
                    s->vm3_vertex45_jobs, first, bf_592, position_tag, color_tag);
            }
        }
    } else {
        unsigned sequence = ++s->vm3_vertex448_jobs;
        if (sequence <= 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex448 writeback: n=%u first=%u vertices=%u position-bytes=%u color-bytes=%u uv-bytes=%u\n",
                sequence, first, count, count * 16, count * 8, count * 8);
        }
        if (tagged_448 && sequence <= 96) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 vertex448 local tags: n=%u first=%u vertices=%u position=0x%x interleaved=0x%x\n",
                sequence, first, count, position_tag, color_tag);
        }
    }
    return G3D00_JOB_DONE;
}
