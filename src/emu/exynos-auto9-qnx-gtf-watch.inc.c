/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CONFIG_USER_ONLY
#include "exec/icount.h"
#include "qemu/timer.h"

static bool auto9_qnx_wdi_read(CPUState *cpu, uint64_t va, uint8_t *out,
                              unsigned size)
{
    TranslateForDebugResult t;
    if (!va || (va & (size - 1)) || va >= (UINT64_C(1) << 39) ||
        !cpu_translate_for_debug(cpu, va, &t)) {
        return false;
    }
    return ((t.physaddr >= 0x90000000 && t.physaddr <= 0xa0000000 - size) ||
            (t.physaddr >= 0x880000000 && t.physaddr <= 0x8ce000000 - size)) &&
           !cpu_memory_rw_debug(cpu, va, out, size, false);
}

/* Original libc errno accessor loads the pointer at TPIDRRO_EL0 + 16. */
static void auto9_qnx_wdi_errno(CPUARMState *env, CPUState *cpu,
                               const char *stage)
{
    uint64_t tls = env->cp15.tpidrro_el[0];
    uint8_t pointer[8], value[4];
    bool valid = tls && tls < (UINT64_C(1) << 39) - 16 &&
                 auto9_qnx_wdi_read(cpu, tls + 16, pointer, sizeof(pointer)) &&
                 auto9_qnx_wdi_read(cpu, ldq_le_p(pointer), value, sizeof(value));
    qemu_log_mask(LOG_GUEST_ERROR,
                  "Auto9 VM2 original HUD %s errno: valid=%u value=%d client=wdi\n",
                  stage, (unsigned)valid, valid ? (int32_t)ldl_le_p(value) : 0);
}

/*
 * Original QNX GtfStartup code is identical in the two IFS occurrences;
 * only ELF physical-address header fields differ. This table also pins the
 * libGtfCore handoff to its original executable instructions. The QEMU
 * patcher derives its translator prefilter from the same rows.
 */
void HELPER(auto9_qnx_gtf_watch)(CPUARMState *env, uint64_t pc, uint32_t insn)
{
    enum { GTF_NO_RESULT, GTF_W0_BOOL, GTF_X20_PRESENT, GTF_TASK_ENTRY, GTF_WDI_TASK_ENTRY,
           GTF_WDI_STATUS, GTF_WDI_EDGE, GTF_WDI_ATTEMPT,
           GTF_WDI_STREAM_ARG, GTF_WDI_STREAM_EVENT, GTF_WDI_STREAM_COUNT,
           GTF_HUD_SUBSCRIBE, GTF_HUD_VARIANT_ENTRY, GTF_HUD_VARIANT_COMMIT,
           GTF_HUD_FEATURE_VALUE, GTF_HUD_PATH_PRESENT, GTF_HUD_LOAD_ENTRY,
           GTF_HUD_LOAD_RESULT, GTF_HUD_SCREEN_RESULT, GTF_HUD_TIMER_EXECUTOR,
           GTF_HUD_TIMER_EDGE, GTF_HUD_PUBLISH_ENTRY,
           GTF_HUD_PUBLISH_VALUE,
           GTF_GRAPHICS_ENTRY, GTF_GRAPHICS_BOOL, GTF_GRAPHICS_HANDLE,
           GTF_GRAPHICS_STATUS, GTF_GRAPHICS_COMMAND,
           GTF_GRAPHICS_DRAW_ARRAYS, GTF_GRAPHICS_DRAW_ELEMENTS,
           GTF_GRAPHICS_KBASE_AS_UPDATE, GTF_GRAPHICS_KBASE_JOB_START };
    static const struct {
        unsigned offset;
        const char *name;
        unsigned result;
        uint32_t words[4];
    } sites[] = {
        { 0xa38, "crt-argv", GTF_NO_RESULT, { 0xaa0103f4, 0xaa0203f5, 0x97ffffa8, 0x90000080 } },
        { 0xa80, "crt-errno", GTF_NO_RESULT, { 0x97ffffa8, 0xb900001f, 0x2a1303e0, 0xaa1403e1 } },
        { 0x970, "wrapper-main", GTF_TASK_ENTRY, { 0xa9bc7bfd, 0xd2800005, 0xd2800004, 0x910003fd } },
        { 0x790, "core-main", GTF_NO_RESULT, { 0xb00002c6, 0xa9b07bfd, 0x910003fd, 0xf947ecc6 } },
        { 0x1ec, "config-import-return", GTF_W0_BOOL, { 0x53001c00, 0x35ffe8c0, 0xb00002c0, 0xf9003fa0 } },
        { 0xf48, "launcher-present", GTF_X20_PRESENT, { 0xb40001f4, 0x91002280, 0x9400d7de, 0xf94057a1 } },
        { 0xfc4, "launcher-run", GTF_NO_RESULT, { 0xd63f0020, 0x53001c00, 0xb9006ba0, 0xf9404fa0 } },
        { 0xfc8, "launcher-return", GTF_W0_BOOL, { 0x53001c00, 0xb9006ba0, 0xf9404fa0, 0xf94002a3 } },
        { 0x998, "core-return", GTF_W0_BOOL, { 0xf9401fb4, 0x53001c00, 0x52000013, 0xb4000274 } },
        { 0x3d0, "wdi-crt-first-call", GTF_WDI_TASK_ENTRY, { 0x97ffb4bc, 0x90000be0, 0x90000be1, 0x912f6000 } },
        { 0x424, "wdi-call-main", GTF_WDI_TASK_ENTRY, { 0x97ffc3f5, 0x97ffb79a, 0x14000000, 0x90000c41 } },
        { 0x3f8, "wdi-main", GTF_WDI_TASK_ENTRY, { 0xd112c3ff, 0xa9bc7bfd, 0x910003fd, 0xa90153f3 } },
        { 0x434, "wdi-constructor-call", GTF_WDI_EDGE, { 0x940056ee, 0x97fff142, 0x90000901, 0x91188021 } },
        { 0x438, "wdi-constructor-return", GTF_WDI_EDGE, { 0x97fff142, 0x90000901, 0x91188021, 0x97fff373 } },
        { 0x118, "wdi-vci-thread-return", GTF_WDI_STATUS, { 0x34000160, 0xb0000841, 0x910a3c21, 0x97ff9637 } },
        { 0x174, "wdi-warp-sleep-enter", GTF_WDI_ATTEMPT, { 0xf90063a0, 0xf94043a0, 0x97ff9a3d, 0x90000858 } },
        { 0x180, "wdi-warp-sleep-return", GTF_WDI_ATTEMPT, { 0x90000858, 0xb0000846, 0x911ee8c6, 0x2a1903e7 } },
        { 0x270, "wdi-warp-wait-done", GTF_WDI_EDGE, { 0xaa1403e0, 0x97ffecfc, 0x710006bf, 0x1a9f17e0 } },
        { 0x8f0, "wdi-create-window-call", GTF_WDI_EDGE, { 0x94001d75, 0xaa1403e0, 0x910243b4, 0x97ff9ef9 } },
        { 0x72c, "wdi-display-bind-return", GTF_WDI_STATUS, { 0xaa1703e2, 0x52800661, 0xf9403a60, 0x97ff66b2 } },
        { 0x964, "wdi-acquire-stream-present", GTF_WDI_STREAM_ARG, { 0x97ff9c57, 0x35000d60, 0xaa1703e2, 0x52800441 } },
        { 0x968, "wdi-acquire-return", GTF_WDI_STATUS, { 0x35000d60, 0xaa1703e2, 0x52800441, 0xf94033a0 } },
        { 0x168, "wdi-stream-event-handle", GTF_WDI_STREAM_EVENT, { 0xf9401fa0, 0xb4000b40, 0x910123b4, 0x52800281 } },
        { 0x190, "wdi-stream-id-compare", GTF_WDI_STATUS, { 0x350008c0, 0xf9404660, 0x97ff6562, 0xf9404a60 } },
        { 0x1b8, "wdi-consumer-create-return", GTF_WDI_STATUS, { 0x35000260, 0x52800001, 0xa9488262, 0x97ff61bf } },
        { 0x1c8, "wdi-consumer-bind-return", GTF_WDI_STATUS, { 0x340002c0, 0x97ff61f9, 0xb9400007, 0xb00007e6 } },
        { 0x288, "wdi-consumer-buffer-count", GTF_WDI_STREAM_COUNT, { 0x35000260, 0xb00007e6, 0x9122c8c6, 0x52800005 } },
        { 0x300, "wdi-stream-close", GTF_WDI_EDGE, { 0xf9404402, 0xb4000222, 0xa9be7bfd, 0x910003fd } },
        { 0x97c, "wdi-acquired-buffer-query-return", GTF_WDI_STATUS, { 0x35000cc0, 0xf9400260, 0x940036fc, 0x910153a2 } },
        { 0x564, "wdi-egl-initialize-return", GTF_WDI_STATUS, { 0x35000140, 0xf0000806, 0x913da8c6, 0x91348261 } },
        { 0x5f0, "wdi-screen-context-return", GTF_WDI_STATUS, { 0x34000100, 0xb00007e6, 0x912580c6, 0x52800005 } },
        { 0x678, "wdi-screen-window-return", GTF_WDI_STATUS, { 0x34000120, 0xb00007e6, 0x912620c6, 0x52800005 } },
        { 0x7f4, "wdi-screen-buffers-return", GTF_WDI_STATUS, { 0x340001e0, 0xb00007e6, 0x912808c6, 0x52800005 } },
        { 0xc10, "hud-available-subscribe-entry", GTF_HUD_SUBSCRIBE, { 0xd10883ff, 0xa9ba7bfd, 0x910003fd, 0xa9025bf5 } },
        { 0xf10, "hud-persistence-callback", GTF_HUD_VARIANT_ENTRY, { 0xa9ac7bfd, 0x910003fd, 0xa90153f3, 0xf0000513 } },
        { 0x17c, "hud-variant-committed", GTF_HUD_VARIANT_COMMIT, { 0xf9405ea0, 0xb4ffefc0, 0xf9400003, 0x90000521 } },
        { 0x974, "hud-available-value", GTF_HUD_FEATURE_VALUE, { 0x340000c0, 0xf94006b4, 0x910022a1, 0xb942ba80 } },
        { 0xa04, "hud-variant-path-present", GTF_HUD_PATH_PRESENT, { 0xb40000a1, 0x9100a2a2, 0xaa1603e1, 0xf94006a0 } },
        { 0x360, "hud-model-load-entry", GTF_HUD_LOAD_ENTRY, { 0xa9b67bfd, 0x910003fd, 0xa90153f3, 0xaa0003f4 } },
        { 0x3dc, "hud-model-load-id", GTF_HUD_LOAD_RESULT, { 0xb902ba80, 0xf9459673, 0xf9404fa1, 0xf9400260 } },
        { 0xee4, "gtf-stream-create-return", GTF_HUD_SCREEN_RESULT, { 0x34000760, 0x911dc274, 0x9100c295, 0xaa1503e0 } },
        { 0x120, "gtf-stream-buffers-return", GTF_HUD_SCREEN_RESULT, { 0x340004c0, 0x911dc274, 0x91014295, 0xaa1503e0 } },
        { 0x970, "gtf-stream-post-return", GTF_HUD_SCREEN_RESULT, { 0xa94153f3, 0xa9425bf5, 0xa94363f7, 0xa8c57bfd } },
        { 0x308, "gles3-catalog", GTF_GRAPHICS_ENTRY, { 0xb4000040, 0x17fffff9, 0xd65f03c0, 0xd503201f } },
        { 0xaf8, "egl-initialize", GTF_GRAPHICS_ENTRY, { 0xa9b953f3, 0x900082b4, 0xaa0003f3, 0xa9015bf5 } },
        { 0xd80, "egl-initialize-return", GTF_GRAPHICS_BOOL, { 0xd65f03c0, 0xd503201f, 0x91005276, 0xaa1603e0 } },
        { 0x970, "egl-create-context", GTF_GRAPHICS_ENTRY, { 0xd10243ff, 0xa90153f3, 0xb00082b4, 0xf90033fe } },
        { 0xa10, "egl-create-context-return", GTF_GRAPHICS_HANDLE, { 0xd65f03c0, 0xd503201f, 0xd2800013, 0x17fffff0 } },
        { 0x244, "egl-make-current-return", GTF_GRAPHICS_BOOL, { 0xd65f03c0, 0x52800001, 0xaa1c03e0, 0x97ffd8ee } },
        { 0x6e8, "egl-swap-buffers", GTF_GRAPHICS_ENTRY, { 0xa9ba53f3, 0x90008253, 0xa90263f7, 0xa9015bf5 } },
        { 0x788, "egl-swap-buffers-return", GTF_GRAPHICS_BOOL, { 0xd65f03c0, 0xd503201f, 0x2a1403e0, 0x17fffff3 } },
        { 0x828, "egl-swap-without-present", GTF_GRAPHICS_ENTRY, { 0x17ffffce, 0x97fff557, 0x91002316, 0xaa0003fa } },
        { 0xb78, "egl-present-window", GTF_GRAPHICS_ENTRY, { 0xa9b553f3, 0xb0008254, 0xaa0003f3, 0xa9015bf5 } },
        { 0xad0, "gles-clear", GTF_GRAPHICS_ENTRY, { 0xa9bf7bf3, 0x2a0003f3, 0x940284f6, 0xb4000120 } },
        { 0x910, "gles-draw-arrays", GTF_GRAPHICS_DRAW_ARRAYS, { 0xa9be53f3, 0x2a0003f3, 0x2a0103f4, 0xa9017bf5 } },
        { 0x998, "gles-draw-elements", GTF_GRAPHICS_DRAW_ELEMENTS, { 0xa9bd53f3, 0x2a0003f3, 0x2a0103f4, 0xa9015bf5 } },
        { 0x8d0, "gles-draw-arrays-instanced", GTF_GRAPHICS_DRAW_ARRAYS, { 0xa9bd53f3, 0x2a0003f3, 0x2a0103f4, 0xa9015bf5 } },
        { 0xce0, "gles-draw-elements-instanced", GTF_GRAPHICS_DRAW_ELEMENTS, { 0xa9bd53f3, 0x2a0003f3, 0x2a0103f4, 0xa9015bf5 } },
        { 0xa00, "screen-post-window", GTF_GRAPHICS_ENTRY, { 0xd12043ff, 0xa9bc7bfd, 0x910003fd, 0xa9025bf5 } },
        { 0xfd0, "screen-post-command", GTF_GRAPHICS_COMMAND, { 0x79000040, 0x91002059, 0xb9400aa0, 0x11000400 } },
        { 0xafc, "screen-post-window-return", GTF_GRAPHICS_STATUS, { 0xd65f03c0, 0x910112f8, 0xaa1803e0, 0x97ffc2be } },
        { 0xa98, "kbase-job-ioctl", GTF_GRAPHICS_ENTRY, { 0xa9be7bfd, 0x910003fd, 0xf9000bf3, 0xaa0103f3 } },
        { 0x3f8, "kbase-as-update-request", GTF_GRAPHICS_KBASE_AS_UPDATE, { 0x17ffbbdc, 0xd503201f, 0xa9bc7bfd, 0x71000c9f } },
        { 0x6bc, "kbase-job-start-request", GTF_GRAPHICS_KBASE_JOB_START, { 0x97ffef2b, 0xf94752d6, 0xf94037a1, 0xf94002c0 } },
        { 0x1f0, "gtf-stream-sharing-return", GTF_HUD_SCREEN_RESULT, { 0xf0000342, 0xaa1503e0, 0x910f6042, 0xf9400043 } },
        { 0x3d0, "gtf-stream-name-return", GTF_HUD_SCREEN_RESULT, { 0x52800020, 0xa94153f3, 0xa9425bf5, 0xa94363f7 } },
        { 0xe3c, "hud-timer-executor", GTF_HUD_TIMER_EXECUTOR, { 0xb4001060, 0x90002760, 0x910c6001, 0xd2800cc2 } },
        { 0xfbc, "hud-timer-construct", GTF_HUD_TIMER_EDGE, { 0x97f651d9, 0xaa1403e0, 0x97f6206f, 0xf9403a60 } },
        { 0x580, "hud-timer-fired", GTF_HUD_TIMER_EDGE, { 0xa9bd7bfd, 0x910003fd, 0xa90153f3, 0xd0003753 } },
        { 0xc9c, "adapter-3s-timer-construction", GTF_HUD_TIMER_EDGE, { 0x97ff1fdd, 0xf9414a60, 0xf9014a77, 0xb4000080 } },
        { 0xcd8, "adapter-3s-timer-start-return", GTF_HUD_TIMER_EDGE, { 0xaa1a03e0, 0x97ff1ea1, 0xf94063a0, 0xb4000040 } },
        { 0xf00, "adapter-3s-timer-callback", GTF_HUD_TIMER_EDGE, { 0xa9b17bfd, 0x910003fd, 0xa90153f3, 0xb0000534 } },
        { 0xfa4, "adapter-executor-submit", GTF_HUD_TIMER_EDGE, { 0xd63f0300, 0xb0000521, 0xf9406fa0, 0xf947ec21 } },
        { 0x1d0, "adapter-executor-late-worker", GTF_HUD_TIMER_EDGE, { 0xa9bd7bfd, 0x910003fd, 0xa90153f3, 0xb0000513 } },
        { 0xdd4, "adapter-dlopen-result", GTF_HUD_TIMER_EXECUTOR, { 0xaa1303e0, 0x97ff2ffe, 0xaa1603e0, 0x97ff2ffc } },
        { 0xef0, "hud-publisher-start-entry", GTF_HUD_PUBLISH_ENTRY, { 0xa9af7bfd, 0x910003fd, 0xa90153f3, 0xf0003754 } },
        { 0x0a4, "hud-publisher-start-value", GTF_HUD_PUBLISH_VALUE, { 0xd63f0040, 0x90003756, 0x3941e6e1, 0xf941fac0 } },
        { 0x794, "hud-publisher-stop-value", GTF_HUD_PUBLISH_VALUE, { 0xd63f0040, 0x90003756, 0xf941fac0, 0x3900001f } },
    };
    static uint64_t observed[2], graphics_seen[3][2], graphics_status_error[3][2];
    static uint64_t wdi_status[2][2], hud_model_seen[4][2], wdi_attempts[5][2];
    static uint32_t hud_variant_committed, hud_event_traces[4][2][2];
    static uint64_t gtf_ttbr0, wdi_ttbr0;
    QEMU_BUILD_BUG_ON(G_N_ELEMENTS(sites) > 64 * G_N_ELEMENTS(observed));
    const unsigned offset = pc & 0xfff;
    unsigned site = 0;
    uint8_t code[sizeof(sites[0].words)];
    TranslateForDebugResult translation;
    CPUState *cpu = env_cpu(env);

    if ((env->cp15.vttbr_el2 >> 48) != 2 || arm_current_el(env) >= 2) {
        return;
    }
    while (site < G_N_ELEMENTS(sites) &&
           (sites[site].offset != offset || sites[site].words[0] != insn)) {
        site++;
    }
    if (site == G_N_ELEMENTS(sites)) {
        return;
    }
    /* Keep the IC GTF, HUD WDI and other VM2 clients separate. No guest
     * pointer or page-table address is emitted to the trace. */
    uint64_t ttbr0 = env->cp15.ttbr0_el[1] & ~UINT64_C(1);
    const unsigned kind = sites[site].result;
    bool graphics = kind >= GTF_GRAPHICS_ENTRY;
    unsigned client = !graphics || !ttbr0 ? 0 :
        ttbr0 == qatomic_read(&wdi_ttbr0) ? 2 : ttbr0 == qatomic_read(&gtf_ttbr0) ? 1 : 0;
    const unsigned word = site / 64;
    uint64_t *seen = graphics ? &graphics_seen[client][word] : &observed[word];
    uint64_t site_bit = UINT64_C(1) << (site % 64);
    bool task_entry = kind == GTF_TASK_ENTRY || kind == GTF_WDI_TASK_ENTRY;
    uint32_t reg = env->xregs[1];
    int result = -1;
    bool stream = kind >= GTF_WDI_STREAM_ARG && kind <= GTF_WDI_STREAM_COUNT;
    bool hud_model = kind >= GTF_HUD_SUBSCRIBE && kind <= GTF_HUD_PUBLISH_VALUE;
    uint32_t *hud_event_count = NULL;
    unsigned after_hud_variant = 0;
    if (hud_model) {
        unsigned bucket = 0;
        if (!ttbr0 || ttbr0 != qatomic_read(&gtf_ttbr0)) {
            return;
        }
        if (kind == GTF_HUD_VARIANT_COMMIT) {
            uint8_t value[4];
            uint64_t object = env->xregs[21];
            if (!object || object > (UINT64_C(1) << 39) - 0x2c0 ||
                !auto9_qnx_wdi_read(cpu, object + 0x2bc, value, sizeof(value))) {
                return;
            }
            uint32_t variant = ldl_le_p(value);
            result = variant <= 2 ? (int)variant : -1;
            bucket = variant <= 2 ? variant : 3;
        } else if (kind == GTF_HUD_FEATURE_VALUE || kind == GTF_HUD_PATH_PRESENT) {
            result = kind == GTF_HUD_FEATURE_VALUE ? (uint8_t)env->xregs[0] != 0 :
                     env->xregs[1] != 0;
            bucket = result;
        } else if (kind == GTF_HUD_TIMER_EXECUTOR || kind == GTF_HUD_TIMER_EDGE) {
            result = kind == GTF_HUD_TIMER_EDGE || env->xregs[0] != 0;
            bucket = result;
        } else if (kind == GTF_HUD_PUBLISH_VALUE) {
            /* Before the original provider call: report only the boolean argument. */
            result = (uint8_t)env->xregs[1] != 0;
        } else if (kind == GTF_HUD_LOAD_RESULT) {
            /* The original callback stores W0 as the model ID, with -1 unset.
             * Record only whether an ID was returned, not its numeric value. */
            result = (int32_t)env->xregs[0] != -1;
            bucket = result;
        } else if (kind == GTF_HUD_SCREEN_RESULT) {
            result = (int32_t)env->xregs[0];
            bucket = result == 0 ? 0 : result == -1 ? 1 : 2;
        }
        if (kind == GTF_HUD_FEATURE_VALUE || kind == GTF_HUD_PUBLISH_VALUE ||
            kind == GTF_HUD_PUBLISH_ENTRY) {
            unsigned event = kind == GTF_HUD_FEATURE_VALUE ? 0 :
                             kind == GTF_HUD_PUBLISH_ENTRY ? 1 :
                             offset == 0x0a4 ? 2 : 3;
            after_hud_variant = qatomic_read(&hud_variant_committed) != 0;
            hud_event_count = &hud_event_traces[event][after_hud_variant][result > 0];
            if (qatomic_read(hud_event_count) >= 4) {
                return;
            }
        }
        seen = &hud_model_seen[bucket][word];
    }
    if (kind == GTF_WDI_STATUS || kind == GTF_WDI_EDGE || kind == GTF_WDI_ATTEMPT || stream) {
        if (!ttbr0 || ttbr0 != qatomic_read(&wdi_ttbr0)) {
            return;
        }
        /* Track both return classes. EGL succeeds with nonzero, Screen
         * succeeds with zero. Record the raw return, never a guest pointer. */
        if (kind == GTF_WDI_STATUS) {
            seen = &wdi_status[(uint32_t)env->xregs[0] != 0][word];
        } else if (kind == GTF_WDI_ATTEMPT) {
            /* Original W25 holds the ordinal both before and after sleep. */
            unsigned ordinal = (uint32_t)env->xregs[25];
            if (ordinal != 1 && (ordinal < 5 || ordinal > 20 || ordinal % 5)) {
                return;
            }
            seen = &wdi_attempts[ordinal / 5][word];
        } else if (stream) {
            if (kind == GTF_WDI_STREAM_ARG) {
                /* At the original BL: X0 is the buffer out pointer, X1
                 * is getCstream(), not an API status code. */
                if ((uint32_t)env->xregs[5] != 0x20 || env->xregs[2] ||
                    env->xregs[3] || env->xregs[4] || !env->xregs[0]) {
                    return;
                }
                result = env->xregs[1] != 0;
            } else if (kind == GTF_WDI_STREAM_COUNT) {
                result = (int32_t)env->xregs[0];
            } else {
                uint8_t value[8];
                uint64_t frame = env->xregs[29];
                if (frame > (UINT64_C(1) << 39) - 0x40 ||
                    !auto9_qnx_wdi_read(cpu, frame + 0x38, value, sizeof(value))) {
                    return;
                }
                result = ldq_le_p(value) != 0;
            }
            seen = &wdi_status[result != 0][word];
        }
    }
    if ((kind == GTF_GRAPHICS_COMMAND && (uint16_t)env->xregs[0] != 13) ||
        (kind == GTF_GRAPHICS_DRAW_ARRAYS && (int32_t)env->xregs[2] <= 0) ||
        (kind == GTF_GRAPHICS_DRAW_ELEMENTS && (int32_t)env->xregs[1] <= 0) ||
        (kind == GTF_GRAPHICS_KBASE_AS_UPDATE &&
         ((uint32_t)env->xregs[2] != 1 || reg < 0x2418 || reg > 0x27d8 || (reg & 0x3f) != 0x18)) ||
        (kind == GTF_GRAPHICS_KBASE_JOB_START &&
         ((uint32_t)env->xregs[2] != 1 || reg < 0x1860 || reg > 0x1960 || (reg & 0x7f) != 0x60))) {
        return;
    }
    if (kind == GTF_GRAPHICS_STATUS && (uint32_t)env->xregs[0] != 0) {
        /* A startup failure must not suppress the first later success. */
        seen = &graphics_status_error[client][word];
    }
    if (!task_entry && !hud_event_count && (qatomic_read(seen) & site_bit)) {
        return;
    }
    if (!cpu_translate_for_debug(cpu, pc, &translation)) {
        return;
    }
    /* Read only memory in the original guest's private low/high RAM. */
    if (!((translation.physaddr >= 0x90000000 &&
           translation.physaddr <= 0xa0000000 - sizeof(code)) ||
          (translation.physaddr >= 0x880000000 &&
           translation.physaddr <= 0x8ce000000 - sizeof(code))) ||
        cpu_memory_rw_debug(cpu, pc, code, sizeof(code), false)) {
        return;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(sites[site].words); i++) {
        if (ldl_le_p(code + 4 * i) != sites[site].words[i]) {
            return;
        }
    }
    if (kind == GTF_TASK_ENTRY) {
        qatomic_set(&gtf_ttbr0, ttbr0);
    } else if (kind == GTF_WDI_TASK_ENTRY) {
        qatomic_set(&wdi_ttbr0, ttbr0);
    }
    if (kind == GTF_HUD_VARIANT_COMMIT) {
        qatomic_set(&hud_variant_committed, 1);
    }
    if (hud_event_count) {
        unsigned n = qatomic_fetch_add(hud_event_count, 1);
        if (n < 4) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original HUD producer: stage=%s result=%d client=gtf "
                          "after-variant=%u ordinal=%u\n",
                          sites[site].name, result, after_hud_variant, n + 1);
        }
        return;
    }
    if (!(qatomic_fetch_or(seen, site_bit) & site_bit)) {
        if (sites[site].result == GTF_W0_BOOL) {
            result = (uint8_t)env->xregs[0];
        } else if (sites[site].result == GTF_X20_PRESENT) {
            result = env->xregs[20] != 0;
        } else if (sites[site].result == GTF_GRAPHICS_BOOL) {
            result = (uint32_t)env->xregs[0] != 0;
        } else if (sites[site].result == GTF_GRAPHICS_HANDLE) {
            result = env->xregs[0] != 0;
        } else if (kind == GTF_GRAPHICS_STATUS) {
            result = (int32_t)env->xregs[0];
        } else if (kind == GTF_GRAPHICS_COMMAND) {
            result = 13;
        } else if (kind == GTF_GRAPHICS_DRAW_ARRAYS || kind == GTF_GRAPHICS_DRAW_ELEMENTS) {
            result = 1; /* Non-empty draw request, not job or pixels. */
        } else if (kind == GTF_GRAPHICS_KBASE_AS_UPDATE) {
            result = (reg - 0x2418) >> 6; /* Address-space index; no addresses. */
        } else if (kind == GTF_GRAPHICS_KBASE_JOB_START) {
            result = (reg - 0x1860) >> 7; /* Slot index, not completion. */
        }
        if (graphics) {
            static const char *const names[] = { "other-or-unbound", "gtf", "wdi" };
            qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original graphics: stage=%s result=%d client=%s\n",
                          sites[site].name, result, names[client]);
        } else if (kind == GTF_WDI_TASK_ENTRY) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original HUD: stage=%s client=wdi\n",
                          sites[site].name);
        } else if (kind == GTF_WDI_EDGE || kind == GTF_WDI_ATTEMPT) {
            if (icount_enabled()) {
                qemu_log_mask(LOG_GUEST_ERROR,
                              "Auto9 VM2 original HUD checkpoint: stage=%s clock=icount-unsampled attempt=%u\n",
                              sites[site].name, kind == GTF_WDI_ATTEMPT ? (uint32_t)env->xregs[25] : 0);
            } else {
                qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original HUD checkpoint: stage=%s virtual-ns=%" PRId64 " attempt=%u\n",
                          sites[site].name, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
                          kind == GTF_WDI_ATTEMPT ? (uint32_t)env->xregs[25] : 0);
            }
        } else if (hud_model) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original HUD producer: stage=%s result=%d client=gtf\n",
                          sites[site].name, result);
        } else if (kind == GTF_WDI_STATUS || stream) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original HUD: stage=%s result=%d client=wdi\n",
                          sites[site].name, stream ? result : (int32_t)env->xregs[0]);
            if ((int32_t)env->xregs[0] == -1) {
                if (offset == 0x968) {
                    auto9_qnx_wdi_errno(env, cpu, "acquire");
                } else if (offset == 0x97c) {
                    auto9_qnx_wdi_errno(env, cpu, "buffer-query");
                }
            }
        } else {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "Auto9 VM2 original GtfStartup executed: site=0x%x "
                          "pc=0x%" PRIx64 " pa=0x%" HWADDR_PRIx " stage=%s result=%d\n",
                          offset, pc, translation.physaddr, sites[site].name, result);
        }
    }
}
#endif
