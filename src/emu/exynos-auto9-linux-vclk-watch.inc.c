/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CONFIG_USER_ONLY
#include "exec/icount.h"
/* Host observation only: bound the QNX peer trace to original Linux clock init. */
static unsigned auto9_vm3_vclk_startup_window;

/* Original VM3 clock frontend: separate repeated query-8 from clk_register.
 * Only the original synchronous init task is measured between its first
 * platform-register entry and return. The initial query-7 uses the same
 * transport edges but an independent sample from the query-8 loop. */
void HELPER(auto9_vm3_vclk_watch)(CPUARMState *env, uint32_t site)
{
    /* Virtual clock reads from an arbitrary translated instruction are
     * invalid in icount mode. Leave the guest and TB timing unchanged. */
    if (icount_enabled()) {
        return;
    }
    static const struct {
        uint64_t pc;
        uint32_t words[4];
    } edges[] = {
        { UINT64_C(0xffffffc010edc0b0), {0x97e25bfa, 0x2a0003f4, 0x340000c0, 0xf0fff6e0} },
        { UINT64_C(0xffffffc010edc0b4), {0x2a0003f4, 0x340000c0, 0xf0fff6e0, 0x91370000} },
        { UINT64_C(0xffffffc010edc0e0), {0x97e25bee, 0x2a0003f4, 0x34000160, 0xf0fff6e0} },
        { UINT64_C(0xffffffc010edc0e4), {0x2a0003f4, 0x34000160, 0xf0fff6e0, 0x91370000} },
        { UINT64_C(0xffffffc010edc120), {0x97d9f754, 0x2a0003f4, 0x34000160, 0xb4000073} },
        { UINT64_C(0xffffffc010edc124), {0x2a0003f4, 0x34000160, 0xb4000073, 0xaa1303e0} },
        { UINT64_C(0xffffffc01077af34), {0x97fffdfd, 0x2a0003f3, 0x350018c0, 0xa90573fb} },
        { UINT64_C(0xffffffc01077af38), {0x2a0003f3, 0x350018c0, 0xa90573fb, 0xb9400ef8} },
        { UINT64_C(0xffffffc01077b02c), {0x97fffdbf, 0xf94037e7, 0x35000dc0, 0xb9400ce0} },
        { UINT64_C(0xffffffc01077b030), {0xf94037e7, 0x35000dc0, 0xb9400ce0, 0x37f80d80} },
        { UINT64_C(0xffffffc01077b198), {0x97fffd64, 0x35000b80, 0xb9400ef7, 0x37f80b57} },
        { UINT64_C(0xffffffc01077b19c), {0x35000b80, 0xb9400ef7, 0x37f80b57, 0xaa1803e0} },
        { UINT64_C(0xffffffc01077b108), {0x97f2f0d4, 0x35001400, 0xf90046d7, 0xa9446bf9} },
        { UINT64_C(0xffffffc01077b10c), {0x35001400, 0xf90046d7, 0xa9446bf9, 0xa94573fb} },
        { UINT64_C(0xffffffc01077b0c8), {0x97f2f3c6, 0xb140041f, 0x54001488, 0xf9001360} },
        { UINT64_C(0xffffffc01077b0cc), {0xb140041f, 0x54001488, 0xf9001360, 0x11000673} },
        { UINT64_C(0xffffffc01077230c), {0xd63f0060, 0xaa1303e0, 0x97fffdf9, 0x72001c1f} },
        { UINT64_C(0xffffffc010772310), {0xaa1303e0, 0x97fffdf9, 0x72001c1f, 0x540002e0} },
        { UINT64_C(0xffffffc010772390), {0x940cb85a, 0x910103e1, 0x52800042, 0xaa1703e0} },
        { UINT64_C(0xffffffc010772394), {0x910103e1, 0x52800042, 0xaa1703e0, 0x97e60050} },
        { UINT64_C(0xffffffc0107723b4), {0xaa1703e0, 0x910103e1, 0x97e600bf, 0xf9401bf7} },
    };
    static struct {
        int64_t start, total, longest;
        unsigned calls, returns, slowest_ordinal;
    } repeated[2];
    static struct {
        int64_t begin, notify, notify_return, first_wait, last_wake, complete;
        uint32_t selector, waits, wakes;
        bool notify_seen, wait_seen;
    } request, initial;
    static unsigned seen;
    static uint64_t init_task;
    uint8_t code[16];
    CPUState *cpu = env_cpu(env);
    TranslateForDebugResult t;
    unsigned observed = qatomic_read(&seen);
    bool in_loop = site == 8 || site == 9 || site == 14 || site == 15;
    bool rpc_site = site >= 16;
    bool query7 = (observed & BIT(10)) && !(observed & BIT(11));
    if (site >= G_N_ELEMENTS(edges) || (env->cp15.vttbr_el2 >> 48) != 3 ||
        arm_current_el(env) != 1 || (!in_loop && !rpc_site && (observed & BIT(site))) ||
        !env->sp_el[0] ||
        (rpc_site && ((observed & BIT(5)) || (!query7 &&
                        repeated[0].calls == repeated[0].returns))) ||
        (site >= 5 && (!(observed & BIT(4)) ||
                       qatomic_read(&init_task) != env->sp_el[0])) ||
        (in_loop && (observed & BIT(5))) ||
        !cpu_translate_for_debug(cpu, edges[site].pc, &t) ||
        t.physaddr < 0xa0000000 || t.physaddr > 0xc0000000 - sizeof(code) ||
        cpu_memory_rw_debug(cpu, edges[site].pc, code, sizeof(code), false)) {
        return;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(edges[site].words); i++) {
        if (ldl_le_p(code + 4 * i) != edges[site].words[i]) {
            return;
        }
    }
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (site == 4) {
        qatomic_set(&init_task, env->sp_el[0]);
        qatomic_set(&auto9_vm3_vclk_startup_window, 1);
    } else if (site == 5) {
        qatomic_set(&auto9_vm3_vclk_startup_window, 0);
    }
    if (site == 10) {
        initial.begin = now;
    } else if (site == 11) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 original first clock request: query=7 begin-ns=%" PRId64
            " notify-ns=%" PRId64 " notify-return-ns=%" PRId64 " first-wait-ns=%" PRId64
            " last-wake-ns=%" PRId64 " complete-ns=%" PRId64 " return-ns=%" PRId64
            " waits=%u wakes=%u result=%d\n", initial.begin, initial.notify,
            initial.notify_return, initial.first_wait, initial.last_wake,
            initial.complete, now, initial.waits, initial.wakes, (int32_t)env->xregs[0]);
    }
    if (rpc_site) {
        /* Both paths execute one original synchronous call; never reset
         * the loop's request when the early call passes through here. */
        typeof(request) *sample = query7 ? &initial : &request;
        switch (site) {
        case 16:
            if (!sample->notify_seen) {
                sample->notify = now;
                sample->notify_seen = true;
            }
            break;
        case 17:
            if (sample->notify_seen && !sample->notify_return) {
                sample->notify_return = now;
            }
            break;
        case 18:
            if (!sample->wait_seen) { sample->first_wait = now; sample->wait_seen = true; }
            if (sample->waits < UINT32_MAX) { sample->waits++; }
            break;
        case 19:
            if (sample->wakes < UINT32_MAX) { sample->wakes++; }
            sample->last_wake = now;
            break;
        case 20: sample->complete = now; break;
        }
        return;
    }
    if (in_loop) {
        unsigned k = site >= 14;
        if (site & 1) {
            if (repeated[k].calls == repeated[k].returns || now < repeated[k].start) {
                return;
            }
            int64_t elapsed = now - repeated[k].start;
            repeated[k].total += elapsed;
            repeated[k].returns++;
            if (!k && (elapsed >= 100000000 || repeated[k].returns == 42)) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 original clock request: ordinal=%u selector=0x%x begin-ns=%" PRId64
                    " notify-ns=%" PRId64 " notify-return-ns=%" PRId64 " first-wait-ns=%" PRId64
                    " last-wake-ns=%" PRId64 " complete-ns=%" PRId64 " return-ns=%" PRId64
                    " waits=%u wakes=%u result=%d\n", repeated[k].returns, request.selector,
                    request.begin, request.notify, request.notify_return, request.first_wait,
                    request.last_wake, request.complete, now, request.waits, request.wakes,
                    (int32_t)env->xregs[0]);
            }
            if (elapsed > repeated[k].longest) {
                repeated[k].longest = elapsed;
                repeated[k].slowest_ordinal = repeated[k].returns;
            }
        } else {
            if (repeated[k].calls != repeated[k].returns || repeated[k].calls >= 4096) {
                return;
            }
            repeated[k].start = now;
            repeated[k].calls++;
            if (!k) {
                memset(&request, 0, sizeof(request));
                request.begin = now;
                /* Original W2 is the scalar packed by the caller into the request. */
                request.selector = (uint32_t)env->xregs[2];
            }
        }
    }
    if (!(qatomic_fetch_or(&seen, BIT(site)) & BIT(site))) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "Auto9 VM3 original clock init: phase=%u return=%u virtual-ns=%" PRId64 " result=%d\n",
                      site / 2, site & 1, now,
                      (site & 1) && site != 15 ? (int32_t)env->xregs[0] : 0);
        if (site == 5) {
            for (unsigned i = 0; i < G_N_ELEMENTS(repeated); i++) {
                qemu_log_mask(LOG_GUEST_ERROR,
                              "Auto9 VM3 original clock loop: kind=%u calls=%u returns=%u total-ns=%" PRId64
                              " max-ns=%" PRId64 " ordinal=%u\n", i, repeated[i].calls,
                              repeated[i].returns, repeated[i].total, repeated[i].longest,
                              repeated[i].slowest_ordinal);
            }
        }
    }
}
#endif
