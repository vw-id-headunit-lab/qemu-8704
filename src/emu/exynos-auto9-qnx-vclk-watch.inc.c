/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CONFIG_USER_ONLY

/* Restrict debug reads to the original VM2 user RAM. Unknown shared-memory
 * translations are explicitly unobserved, not treated as sequence zero. */
static bool auto9_qnx_vclk_read(CPUState *cpu, uint64_t va, uint8_t *buf,
                               unsigned size)
{
    TranslateForDebugResult t;

    return va && !(va & 3) && va < (UINT64_C(1) << 39) &&
           (va & 0xfff) + size <= 0x1000 &&
           cpu_translate_for_debug(cpu, va, &t) &&
           ((t.physaddr >= 0x90000000 && t.physaddr <= 0xa0000000 - size) ||
            (t.physaddr >= 0xe0000000 && t.physaddr <= 0xe3c00000 - size) ||
            (t.physaddr >= 0x880000000 && t.physaddr <= 0x8ce000000 - size)) &&
           !cpu_memory_rw_debug(cpu, va, buf, size, false);
}

/* Original clk_ctrl_be: the ordinary vclk_ctrl channel uses a condition
 * variable; the fast channel calls the same dispatch directly. Do not conflate
 * an XIRQ callback, worker wake and reply with completion on the Linux peer. */
void HELPER(auto9_qnx_vclk_watch)(CPUARMState *env, uint64_t pc, uint32_t insn)
{
    enum { RECEIVE, WORKER, DISPATCH, RETURN, REPLY };
    static const struct {
        unsigned offset;
        const char *name;
        uint32_t words[4];
    } sites[] = {
        { 0x6de8, "receive", { 0xa9be7bfd, 0x910003fd, 0xa90153f3, 0x9103c014 } },
        { 0x6614, "worker", { 0x97ffff5f, 0xaa1403e0, 0x97fff0dd, 0x34fffe80 } },
        { 0x640c, "dispatch", { 0xd63f0060, 0xf9408a62, 0xb9000a80, 0xb9400281 } },
        { 0x6410, "return", { 0xf9408a62, 0xb9000a80, 0xb9400281, 0x8b204040 } },
        { 0x6434, "reply", { 0xf947fc42, 0xa94153f3, 0xa8c27bfd, 0xf9402842 } },
    };
    static unsigned counts[G_N_ELEMENTS(sites)];
    CPUState *cpu = env_cpu(env);
    uint8_t data[16];
    uint64_t ctx, share, bias;
    uint32_t peer, seq = 0, ack = 0;
    bool header;
    int cmd = -1, result_length = -1;
    unsigned i;

    if ((env->cp15.vttbr_el2 >> 48) != 2 || arm_current_el(env) != 0 ||
        icount_enabled() || !qatomic_read(&auto9_vm3_vclk_startup_window)) {
        return;
    }
    for (i = 0; i < G_N_ELEMENTS(sites); i++) {
        if ((pc & 0xfff) == (sites[i].offset & 0xfff) &&
            insn == sites[i].words[0]) {
            break;
        }
    }
    if (i == G_N_ELEMENTS(sites) || qatomic_read(&counts[i]) >= 256 ||
        pc < sites[i].offset || !auto9_qnx_vclk_read(cpu, pc, data, sizeof(data))) {
        return;
    }
    for (unsigned j = 0; j < G_N_ELEMENTS(sites[i].words); j++) {
        if (ldl_le_p(data + 4 * j) != sites[i].words[j]) {
            return;
        }
    }
    bias = pc - sites[i].offset;
    ctx = env->xregs[i >= DISPATCH ? 19 : 0];
    if (!ctx || (ctx & 7) || ctx >= (UINT64_C(1) << 39) - 0x70 ||
        !auto9_qnx_vclk_read(cpu, ctx + 0x68, data, 8) ||
        ldq_le_p(data) != bias + 0x3a58 ||
        !auto9_qnx_vclk_read(cpu, ctx + 0x38, data, 4)) {
        return;
    }
    peer = ldl_le_p(data);
    if (peer != 3 || !auto9_qnx_vclk_read(cpu, ctx + 0x18, data, 8)) {
        return;
    }
    share = ldq_le_p(data);
    header = share && share < (UINT64_C(1) << 39) - 32 &&
             auto9_qnx_vclk_read(cpu, share, data, 12);
    if (header) {
        seq = ldl_le_p(data);
        ack = ldl_le_p(data + 4);
        if (i <= DISPATCH && seq == ack) {
            return; /* Not an outstanding clock request. */
        }
        if (i <= DISPATCH && ldl_le_p(data + 8) == 88 &&
            auto9_qnx_vclk_read(cpu, share + 28, data, 4)) {
            uint32_t operation = ldl_le_p(data);
            cmd = operation <= 10 ? (int)operation : -1;
        }
    }
    /* W1 identifies the original request even if the shared page could not
     * be read using the bounded RAM allowlist. */
    if (i == DISPATCH && (uint32_t)env->xregs[1] != 88) {
        return;
    }
    if (i == RETURN) {
        result_length = (int32_t)env->xregs[0];
    }
    unsigned ordinal = qatomic_fetch_inc(&counts[i]) + 1;
    if (ordinal <= 256) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM2 original clock backend: stage=%s peer=%u ordinal=%u"
            " virtual-ns=%" PRId64 " header=%u seq=%u ack=%u cmd=%d response-bytes=%d\n",
            sites[i].name, peer, ordinal, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
            (unsigned)header, seq, ack, cmd, result_length);
    }
}
#endif
