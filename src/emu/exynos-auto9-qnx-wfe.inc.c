/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CONFIG_USER_ONLY
/* The read is restricted to one debug page of the native VM2 RAM banks. */
static bool auto9_qnx_wfe_read(CPUState *cs, uint64_t addr, uint8_t *raw,
                              size_t length)
{
    TranslateForDebugResult translation;

    if (!length || length > 32 || (addr & 0xfff) + length > 0x1000 ||
        !cpu_translate_for_debug(cs, addr, &translation) ||
        !((translation.physaddr >= UINT64_C(0x90000000) &&
           translation.physaddr <= UINT64_C(0xa0000000) - length) ||
          (translation.physaddr >= UINT64_C(0x880000000) &&
           translation.physaddr <= UINT64_C(0x8ce000000) - length))) {
        return false;
    }
    return cpu_memory_rw_debug(cs, addr, raw, length, false) == 0;
}

static bool auto9_qnx_wfe_match(CPUState *cs, uint64_t addr,
                               const uint32_t *expected, size_t count)
{
    uint8_t code[32];

    if (!count || count > sizeof(code) / 4 ||
        !auto9_qnx_wfe_read(cs, addr, code, count * 4)) {
        return false;
    }
    for (size_t i = 0; i < count; i++) {
        if (ldl_le_p(code + i * 4) != expected[i]) {
            return false;
        }
    }
    return true;
}

/* In native VM2 the generated dispatch BLR calls original procnto, which
 * restores the dispatch LR from X26, loads the original lock literal and
 * tail-branches into the same LDAXR/STLXR/WFE sequence. Require the actual
 * generated caller as well as both original ELF windows and the literal. */
static bool auto9_qnx_wfe_dispatch_caller(CPUState *cs, uint64_t lr)
{
    static const struct {
        uint64_t lr, entry_pc, tail_pc;
        uint32_t generated[8], entry[3], tail[3];
    } paths[] = {
        { UINT64_C(0xffffff80813a11b0), UINT64_C(0xffffff80601155e0),
          UINT64_C(0xffffff80601155fc),
          { 0x8b130ef7U, 0xd28abc00U, 0xf2ac0220U, 0xf2dff000U,
            0xf2ffffe0U, 0xd63f0000U, 0x17ffffebU, 0xd2840006U },
          { 0xaa1e03faU, 0x58000420U, 0x97fff967U },
          { 0xaa1a03feU, 0x58000340U, 0x17fff958U } },
        { UINT64_C(0xffffff80813a115c), UINT64_C(0xffffff8060115570),
          UINT64_C(0xffffff8060115588),
          { 0x8b130ef7U, 0xd28aae00U, 0xf2ac0220U, 0xf2dff000U,
            0xf2ffffe0U, 0xd63f0000U, 0xd2840006U, 0xf2a80406U },
          { 0xaa1e03faU, 0x580007a0U, 0x97fff983U },
          { 0xaa1a03feU, 0x580006e0U, 0x17fff975U } },
    };
    static const uint32_t lock_literal[] = { 0x60196aa0U, 0xffffff80U };

    for (size_t i = 0; i < G_N_ELEMENTS(paths); i++) {
        if (paths[i].lr == lr) {
            return auto9_qnx_wfe_match(cs, lr - 24, paths[i].generated,
                                       G_N_ELEMENTS(paths[i].generated)) &&
                   auto9_qnx_wfe_match(cs, paths[i].entry_pc, paths[i].entry,
                                       G_N_ELEMENTS(paths[i].entry)) &&
                   auto9_qnx_wfe_match(cs, paths[i].tail_pc, paths[i].tail,
                                       G_N_ELEMENTS(paths[i].tail)) &&
                   auto9_qnx_wfe_match(cs, UINT64_C(0xffffff8060115668),
                                       lock_literal, G_N_ELEMENTS(lock_literal));
        }
    }
    return false;
}

/* Original QNX procnto SMP lock used by interrupt events and ASID switching.
 * STLR releases it without SEV. In the failed-STLXR path TCG can have discarded
 * its exclusive address, so the generic WFE helper can sleep instead of
 * checking the lock again. This WFE hint may finish spontaneously; yield
 * to the scheduler first and let the original LDAXR/STLXR retry unchanged.
 */
static bool auto9_qnx_wfe_retry(CPUARMState *env, uint32_t len)
{
    static const uint32_t original[] = {
        0x8802fc01U, 0x35000042U, 0xd65f03c0U, 0xd503205fU, 0x17fffffaU,
    };
    static const struct {
        uint64_t lock, return_pc;
        const char *name;
        uint32_t caller[4]; /* optional exact window at LR - 8 */
    } sites[] = {
        { UINT64_C(0xffffff8060196aa0), UINT64_C(0xffffff8060129074), "irq-event", { 0 } },
        /* Original BL at 0xffffff8060127278 targets the same SMP lock. */
        { UINT64_C(0xffffff8060197eb8), UINT64_C(0xffffff806012727c), "irq-queue", { 0 } },
        /* Original BL at 0xffffff8060109274, before the original ASID update. */
        { UINT64_C(0xffffff806018b368), UINT64_C(0xffffff8060109278), "asid-switch", { 0 } },
        /* Original queue insertion reacquires the IRQ queue lock via X27. */
        { UINT64_C(0xffffff8060197eb8), UINT64_C(0xffffff8060127204),
          "irq-queue-reinsert", { 0xaa1b03e0U, 0x97ffb258U, 0xb9401361U, 0xf9400760U } },
        /* Same queue, initial acquisition before detaching the list head. */
        { UINT64_C(0xffffff8060197eb8), UINT64_C(0xffffff80601270e4),
          "irq-queue-entry", { 0xaa1b03e0U, 0x97ffb2a0U, 0xf940029cU, 0xb9401361U } },
        /* Original scheduler enters the same lock after the DAIF prologue. */
        { UINT64_C(0xffffff806019a9d8), UINT64_C(0xffffff806014ff64),
          "scheduler-queue", { 0x91002280U, 0x97ff0f01U, 0xa94697a0U, 0xf94047a4U } },
        /* A second scheduler call to the same lock, after masking local IRQ. */
        { UINT64_C(0xffffff806019a9d8), UINT64_C(0xffffff806014fe58),
          "scheduler-queue-reentry", { 0x91002280U, 0x97ff0f44U, 0x91006360U, 0xf94053a1U } },
        /* The original caller uses the DAIF-setting entry of the same lock. */
        { UINT64_C(0xffffff806018b030), UINT64_C(0xffffff8060102250),
          "kernel-queue", { 0xaa1803e0U, 0x94004645U, 0x7100069fU, 0x54fffa01U } },
    };
    static bool matched[G_N_ELEMENTS(sites)];
    const uint64_t code = UINT64_C(0xffffff8060113b70);
    CPUState *cs = env_cpu(env);
    static bool dispatch_logged[2];

    if (len != 4 || !env->aarch64 || arm_current_el(env) != 1 ||
        (env->cp15.vttbr_el2 >> 48) != 2 || env->pc != code + 16) {
        return false;
    }
    unsigned site = 0;
    while (site < G_N_ELEMENTS(sites) &&
           (env->xregs[0] != sites[site].lock || env->xregs[30] != sites[site].return_pc)) {
        site++;
    }
    bool alternate = env->xregs[30] == UINT64_C(0xffffff80813a115c);
    bool dynamic = (site == G_N_ELEMENTS(sites) &&
                    env->xregs[0] == UINT64_C(0xffffff8060196aa0) &&
                    (alternate || env->xregs[30] == UINT64_C(0xffffff80813a11b0)) &&
                    env->xregs[26] == env->xregs[30]);
    if (site == G_N_ELEMENTS(sites) && !dynamic) {
        return false;
    }
    if (!dynamic && qatomic_read(&matched[site])) {
        return true;
    }
    if (!auto9_qnx_wfe_match(cs, code, original, G_N_ELEMENTS(original))) {
        return false;
    }
    if (dynamic) {
        if (!auto9_qnx_wfe_dispatch_caller(cs, env->xregs[30])) {
            return false;
        }
        if (!qatomic_xchg(&dispatch_logged[alternate], true)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM2 original IRQ lock WFE: cooperative retry site=%s\n",
                alternate ? "irq-dispatch-early-return" : "irq-dispatch-return");
        }
        return true;
    }
    if (sites[site].caller[0] &&
        !auto9_qnx_wfe_match(cs, sites[site].return_pc - 8, sites[site].caller,
                             G_N_ELEMENTS(sites[site].caller))) {
        return false;
    }
    if (!qatomic_xchg(&matched[site], true)) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM2 original IRQ lock WFE: cooperative retry site=%s\n", sites[site].name);
    }
    return true;
}
#endif
