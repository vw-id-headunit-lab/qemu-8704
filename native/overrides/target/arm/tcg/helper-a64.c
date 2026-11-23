/*
 *  AArch64 specific helpers
 *
 *  Copyright (c) 2013 Alexander Graf <agraf@suse.de>
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, see <http://www.gnu.org/licenses/>.
 */

#include "qemu/osdep.h"
#include "qemu/units.h"
#include "cpu.h"
#include "gdbstub/helpers.h"
#include "helper.h"
#include "helper-a64.h"
#include "qemu/host-utils.h"
#include "qemu/log.h"
#include "qemu/main-loop.h"
#include "qemu/bitops.h"
#include "internals.h"
#include "qemu/crc32c.h"
#include "exec/cpu-common.h"
#include "accel/tcg/cpu-ldst.h"
#include "accel/tcg/cpu-loop.h"
#include "accel/tcg/helper-retaddr.h"
#include "accel/tcg/probe.h"
#include "exec/target_page.h"
#include "exec/tlb-flags.h"
#include "qemu/int128.h"
#include "qemu/atomic128.h"
#include "fpu/softfloat.h"
#include <zlib.h> /* for crc32 */
#ifdef CONFIG_USER_ONLY
#include "user/page-protection.h"
#endif
#include "vec_internal.h"

#define HELPER_H "tcg/helper-a64-defs.h"
#include "exec/helper-info.c.inc"

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

/* Observe the shipped VM3 Linux signal frame input without changing it. */
#ifndef CONFIG_USER_ONLY
#include "qemu/bswap.h"
#include "trace.h"

#define AUTO9_VM3_SIGNAL_ENTRY UINT64_C(0xffffffc01008ea98)
#define AUTO9_VM3_TASK_LEADER 0x548
#define AUTO9_VM3_TASK_THREAD_PID 0x570
#define AUTO9_VM3_TASK_COMM 0x6c0
#define AUTO9_VM3_PID_FIRST_NR 0x50
#define AUTO9_VM3_KSIGNAL_INFO 0x20
#define AUTO9_VM3_PTREGS_PC 0x100

static bool auto9_vm3_signal_read(CPUState *cpu, uint64_t addr, void *dst,
                                 size_t len)
{
    return addr >= UINT64_C(0xffffff8000000000) &&
        addr <= UINT64_MAX - len &&
        !cpu_memory_rw_debug(cpu, addr, dst, len, false);
}

static uint64_t auto9_vm3_signal_ptr(CPUState *cpu, uint64_t addr)
{
    uint8_t data[8];
    return auto9_vm3_signal_read(cpu, addr, data, sizeof(data)) ?
        ldq_le_p(data) : 0;
}

static uint32_t auto9_vm3_signal_tid(CPUState *cpu, uint64_t task)
{
    uint64_t pid = auto9_vm3_signal_ptr(cpu, task + AUTO9_VM3_TASK_THREAD_PID);
    uint8_t data[4];
    return pid && auto9_vm3_signal_read(cpu, pid + AUTO9_VM3_PID_FIRST_NR,
                                       data, sizeof(data)) ? ldl_le_p(data) : 0;
}

void HELPER(auto9_vm3_hmi_signal)(CPUARMState *env)
{
    CPUState *cpu = env_cpu(env);
    int index = cpu->cpu_index;
    unsigned sig = env->xregs[0];
    uint64_t current = env->sp_el[0], leader, pc, addr = 0;
    uint32_t tid, tgid;
    uint8_t code[16], info[24], name[5];
    int32_t si_code;
    /* Independent counters: each index is written only by its AP vCPU. */
    static unsigned rows[8];
    static const uint8_t entry[] = {
        0xfd, 0x7b, 0xb5, 0xa9, 0xfd, 0x03, 0x00, 0x91,
        0xf3, 0x53, 0x01, 0xa9, 0xf4, 0x03, 0x03, 0xaa,
    };

    if (!trace_event_get_state_backends(TRACE_AUTO9_VM3_HMI_SIGNAL) ||
        index < 0 || index >= ARRAY_SIZE(rows) || rows[index] >= 256 ||
        (env->cp15.vttbr_el2 >> 48) != 3 || arm_current_el(env) != 1 ||
        !is_a64(env) || (sig != 1 && sig != 4 && sig != 7 && sig != 8 &&
                         sig != 11)) {
        return;
    }
    if (!auto9_vm3_signal_read(cpu, AUTO9_VM3_SIGNAL_ENTRY, code, sizeof(code)) ||
        memcmp(code, entry, sizeof(entry))) {
        return;
    }
    leader = auto9_vm3_signal_ptr(cpu, current + AUTO9_VM3_TASK_LEADER);
    if (!leader ||
        !auto9_vm3_signal_read(cpu, leader + AUTO9_VM3_TASK_COMM, name, sizeof(name)) ||
        memcmp(name, "JAVA", sizeof(name))) {
        return;
    }
    tid = auto9_vm3_signal_tid(cpu, current);
    tgid = auto9_vm3_signal_tid(cpu, leader);
    if (!tid || !tgid || !auto9_vm3_signal_read(cpu,
        env->xregs[1] + AUTO9_VM3_KSIGNAL_INFO, info, sizeof(info)) ||
        ldl_le_p(info) != sig ||
        !auto9_vm3_signal_read(cpu, env->xregs[3] + AUTO9_VM3_PTREGS_PC,
                              code, 16)) {
        return;
    }
    pc = ldq_le_p(code);
    if (ldq_le_p(code + 8) & 0x1f) {
        return; /* Not a saved native AArch64 EL0 PSTATE. */
    }
    si_code = (int32_t)ldl_le_p(info + 8);
    if (si_code > 0 && sig != 1) {
        addr = ldq_le_p(info + 16);
    }
    trace_auto9_vm3_hmi_signal(index, ++rows[index], tgid, tid, sig,
                               si_code, pc, addr);
}
#endif

/* Observe the original Linux persistence KIPC route and dequeue, not its data. */
#ifndef CONFIG_USER_ONLY
#include "qemu/bswap.h"
#include "trace.h"

#define AUTO9_KIPC_ROUTE_PC UINT64_C(0xffffffc010584d20)
#define AUTO9_KIPC_POP_PC UINT64_C(0xffffffc010584974)

void HELPER(auto9_vm3_kipc_watch)(CPUARMState *env, uint32_t edge)
{
    static const uint8_t site[2][16] = {
        { 0xfd, 0x7b, 0xbf, 0xa9, 0xfd, 0x03, 0x00, 0x91,
          0x01, 0x04, 0x40, 0x79, 0x3f, 0xe0, 0x0f, 0x71 },
        { 0xe0, 0x03, 0x15, 0xaa, 0xf3, 0x53, 0x41, 0xa9,
          0xf5, 0x5b, 0x42, 0xa9, 0xfd, 0x7b, 0xc3, 0xa8 },
    };
    static unsigned rows[8]; /* One writer per AP vCPU index. */
    CPUState *cpu = env_cpu(env);
    int index = cpu->cpu_index;
    uint64_t pkt, body, pc;
    uint8_t code[16], hdr[14], msg[12];
    unsigned src, dst, len;
    const char *kind = "other";

    if (!trace_event_get_state_backends(TRACE_AUTO9_VM3_KIPC_WATCH) ||
        edge >= ARRAY_SIZE(site) || index < 0 || index >= ARRAY_SIZE(rows) ||
        rows[index] >= 512 || (env->cp15.vttbr_el2 >> 48) != 3 ||
        arm_current_el(env) != 1 || !is_a64(env)) {
        return;
    }
    pkt = edge ? env->xregs[21] : env->xregs[0];
    if (!auto9_vm3_signal_read(cpu, pkt, hdr, sizeof(hdr))) {
        return;
    }
    src = lduw_le_p(hdr);
    dst = lduw_le_p(hdr + 2);
    if (!((src == 15 && dst == 52) || (src == 52 && dst == 15))) {
        return;
    }
    pc = edge ? AUTO9_KIPC_POP_PC : AUTO9_KIPC_ROUTE_PC;
    if (!auto9_vm3_signal_read(cpu, pc, code, sizeof(code)) ||
        memcmp(code, site[edge], sizeof(code))) {
        return;
    }
    len = lduw_le_p(hdr + 4);
    body = ldq_le_p(hdr + 6);
    /* The fixed envelope is classified locally; no guest bytes are logged. */
    if (len >= sizeof(msg) && len <= 0x6800 &&
        auto9_vm3_signal_read(cpu, body, msg, sizeof(msg))) {
        uint32_t iface = ldl_le_p(msg);
        uint32_t api = ldl_le_p(msg + 8);

        if (src == 15 && iface == 1400 && api == 9999) {
            kind = "subscription";
        } else if (src == 52 && iface == 9000) {
            kind = "welcome";
        } else if (src == 52 && (iface == 1400 || iface == 1401) && api == 1) {
            kind = "database-medium";
        }
    }
    trace_auto9_vm3_kipc_watch(index, ++rows[index], edge ? "dequeue" : "route",
                              src, dst, kind);
}
#endif

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

/* C2.4.7 Multiply and divide */
/* special cases for 0 and LLONG_MIN are mandated by the standard */
uint64_t HELPER(udiv64)(uint64_t num, uint64_t den)
{
    if (den == 0) {
        return 0;
    }
    return num / den;
}

int64_t HELPER(sdiv64)(int64_t num, int64_t den)
{
    if (den == 0) {
        return 0;
    }
    if (num == LLONG_MIN && den == -1) {
        return LLONG_MIN;
    }
    return num / den;
}

uint64_t HELPER(rbit64)(uint64_t x)
{
    return revbit64(x);
}

void HELPER(msr_i_spsel)(CPUARMState *env, uint32_t imm)
{
    update_spsel(env, imm);
}

void HELPER(msr_set_allint_el1)(CPUARMState *env)
{
    /* ALLINT update to PSTATE. */
    if (arm_hcrx_el2_eff(env) & HCRX_TALLINT) {
        raise_exception_ra(env, EXCP_UDEF,
                           syn_aa64_sysregtrap(0, 1, 0, 4, 1, 0x1f, 0), 2,
                           GETPC());
    }

    env->pstate |= PSTATE_ALLINT;
}

static void daif_check(CPUARMState *env, uint32_t op,
                       uint32_t imm, uintptr_t ra)
{
    /* DAIF update to PSTATE. This is OK from EL0 only if UMA is set.  */
    if (arm_current_el(env) == 0 && !(arm_sctlr(env, 0) & SCTLR_UMA)) {
        raise_exception_ra(env, EXCP_UDEF,
                           syn_aa64_sysregtrap(0, extract32(op, 0, 3),
                                               extract32(op, 3, 3), 4,
                                               imm, 0x1f, 0),
                           exception_target_el(env), ra);
    }
}

void HELPER(msr_i_daifset)(CPUARMState *env, uint32_t imm)
{
    daif_check(env, 0x1e, imm, GETPC());
    env->daif |= (imm << 6) & PSTATE_DAIF;
    arm_rebuild_hflags(env);
}

void HELPER(msr_i_daifclear)(CPUARMState *env, uint32_t imm)
{
    daif_check(env, 0x1f, imm, GETPC());
    env->daif &= ~((imm << 6) & PSTATE_DAIF);
    arm_rebuild_hflags(env);
}

/* Convert a softfloat float_relation_ (as returned by
 * the float*_compare functions) to the correct ARM
 * NZCV flag state.
 */
static inline uint32_t float_rel_to_flags(int res)
{
    uint64_t flags;
    switch (res) {
    case float_relation_equal:
        flags = PSTATE_Z | PSTATE_C;
        break;
    case float_relation_less:
        flags = PSTATE_N;
        break;
    case float_relation_greater:
        flags = PSTATE_C;
        break;
    case float_relation_unordered:
    default:
        flags = PSTATE_C | PSTATE_V;
        break;
    }
    return flags;
}

uint64_t HELPER(vfp_cmph_a64)(uint32_t x, uint32_t y, float_status *fp_status)
{
    return float_rel_to_flags(float16_compare_quiet(x, y, fp_status));
}

uint64_t HELPER(vfp_cmpeh_a64)(uint32_t x, uint32_t y, float_status *fp_status)
{
    return float_rel_to_flags(float16_compare(x, y, fp_status));
}

uint64_t HELPER(vfp_cmps_a64)(float32 x, float32 y, float_status *fp_status)
{
    return float_rel_to_flags(float32_compare_quiet(x, y, fp_status));
}

uint64_t HELPER(vfp_cmpes_a64)(float32 x, float32 y, float_status *fp_status)
{
    return float_rel_to_flags(float32_compare(x, y, fp_status));
}

uint64_t HELPER(vfp_cmpd_a64)(float64 x, float64 y, float_status *fp_status)
{
    return float_rel_to_flags(float64_compare_quiet(x, y, fp_status));
}

uint64_t HELPER(vfp_cmped_a64)(float64 x, float64 y, float_status *fp_status)
{
    return float_rel_to_flags(float64_compare(x, y, fp_status));
}

float32 HELPER(vfp_mulxs)(float32 a, float32 b, float_status *fpst)
{
    a = float32_squash_input_denormal(a, fpst);
    b = float32_squash_input_denormal(b, fpst);

    if ((float32_is_zero(a) && float32_is_infinity(b)) ||
        (float32_is_infinity(a) && float32_is_zero(b))) {
        /* 2.0 with the sign bit set to sign(A) XOR sign(B) */
        return make_float32((1U << 30) |
                            ((float32_val(a) ^ float32_val(b)) & (1U << 31)));
    }
    return float32_mul(a, b, fpst);
}

float64 HELPER(vfp_mulxd)(float64 a, float64 b, float_status *fpst)
{
    a = float64_squash_input_denormal(a, fpst);
    b = float64_squash_input_denormal(b, fpst);

    if ((float64_is_zero(a) && float64_is_infinity(b)) ||
        (float64_is_infinity(a) && float64_is_zero(b))) {
        /* 2.0 with the sign bit set to sign(A) XOR sign(B) */
        return make_float64((1ULL << 62) |
                            ((float64_val(a) ^ float64_val(b)) & (1ULL << 63)));
    }
    return float64_mul(a, b, fpst);
}

/* 64bit/double versions of the neon float compare functions */
uint64_t HELPER(neon_ceq_f64)(float64 a, float64 b, float_status *fpst)
{
    return -float64_eq_quiet(a, b, fpst);
}

uint64_t HELPER(neon_cge_f64)(float64 a, float64 b, float_status *fpst)
{
    return -float64_le(b, a, fpst);
}

uint64_t HELPER(neon_cgt_f64)(float64 a, float64 b, float_status *fpst)
{
    return -float64_lt(b, a, fpst);
}

/*
 * Reciprocal step and sqrt step. Note that unlike the A32/T32
 * versions, these do a fully fused multiply-add or
 * multiply-add-and-halve.
 * The FPCR.AH == 1 versions need to avoid flipping the sign of NaN.
 */
#define DO_RECPS(NAME, CTYPE, FLOATTYPE, CHSFN)                         \
    CTYPE HELPER(NAME)(CTYPE a, CTYPE b, float_status *fpst)            \
    {                                                                   \
        a = FLOATTYPE ## _squash_input_denormal(a, fpst);               \
        b = FLOATTYPE ## _squash_input_denormal(b, fpst);               \
        a = FLOATTYPE ## _ ## CHSFN(a);                                 \
        if ((FLOATTYPE ## _is_infinity(a) && FLOATTYPE ## _is_zero(b)) || \
            (FLOATTYPE ## _is_infinity(b) && FLOATTYPE ## _is_zero(a))) { \
            return FLOATTYPE ## _two;                                   \
        }                                                               \
        return FLOATTYPE ## _muladd(a, b, FLOATTYPE ## _two, 0, fpst);  \
    }

DO_RECPS(recpsf_f16, uint32_t, float16, chs)
DO_RECPS(recpsf_f32, float32, float32, chs)
DO_RECPS(recpsf_f64, float64, float64, chs)
DO_RECPS(recpsf_ah_f16, uint32_t, float16, ah_chs)
DO_RECPS(recpsf_ah_f32, float32, float32, ah_chs)
DO_RECPS(recpsf_ah_f64, float64, float64, ah_chs)

#define DO_RSQRTSF(NAME, CTYPE, FLOATTYPE, CHSFN)                       \
    CTYPE HELPER(NAME)(CTYPE a, CTYPE b, float_status *fpst)            \
    {                                                                   \
        a = FLOATTYPE ## _squash_input_denormal(a, fpst);               \
        b = FLOATTYPE ## _squash_input_denormal(b, fpst);               \
        a = FLOATTYPE ## _ ## CHSFN(a);                                 \
        if ((FLOATTYPE ## _is_infinity(a) && FLOATTYPE ## _is_zero(b)) || \
            (FLOATTYPE ## _is_infinity(b) && FLOATTYPE ## _is_zero(a))) { \
            return FLOATTYPE ## _one_point_five;                        \
        }                                                               \
        return FLOATTYPE ## _muladd_scalbn(a, b, FLOATTYPE ## _three,   \
                                           -1, 0, fpst);                \
    }                                                                   \

DO_RSQRTSF(rsqrtsf_f16, uint32_t, float16, chs)
DO_RSQRTSF(rsqrtsf_f32, float32, float32, chs)
DO_RSQRTSF(rsqrtsf_f64, float64, float64, chs)
DO_RSQRTSF(rsqrtsf_ah_f16, uint32_t, float16, ah_chs)
DO_RSQRTSF(rsqrtsf_ah_f32, float32, float32, ah_chs)
DO_RSQRTSF(rsqrtsf_ah_f64, float64, float64, ah_chs)

/* Floating-point reciprocal exponent - see FPRecpX in ARM ARM */
uint32_t HELPER(frecpx_f16)(uint32_t a, float_status *fpst)
{
    uint16_t val16, sbit;
    int16_t exp;

    if (float16_is_any_nan(a)) {
        float16 nan = a;
        if (float16_is_signaling_nan(a, fpst)) {
            float_raise(float_flag_invalid, fpst);
            if (!get_default_nan_mode(fpst)) {
                nan = float16_silence_nan(a, fpst);
            }
        }
        if (get_default_nan_mode(fpst)) {
            nan = float16_default_nan(fpst);
        }
        return nan;
    }

    a = float16_squash_input_denormal(a, fpst);

    val16 = float16_val(a);
    sbit = 0x8000 & val16;
    exp = extract32(val16, 10, 5);

    if (exp == 0) {
        return make_float16(deposit32(sbit, 10, 5, 0x1e));
    } else {
        return make_float16(deposit32(sbit, 10, 5, ~exp));
    }
}

float32 HELPER(frecpx_f32)(float32 a, float_status *fpst)
{
    uint32_t val32, sbit;
    int32_t exp;

    if (float32_is_any_nan(a)) {
        float32 nan = a;
        if (float32_is_signaling_nan(a, fpst)) {
            float_raise(float_flag_invalid, fpst);
            if (!get_default_nan_mode(fpst)) {
                nan = float32_silence_nan(a, fpst);
            }
        }
        if (get_default_nan_mode(fpst)) {
            nan = float32_default_nan(fpst);
        }
        return nan;
    }

    a = float32_squash_input_denormal(a, fpst);

    val32 = float32_val(a);
    sbit = 0x80000000ULL & val32;
    exp = extract32(val32, 23, 8);

    if (exp == 0) {
        return make_float32(sbit | (0xfe << 23));
    } else {
        return make_float32(sbit | (~exp & 0xff) << 23);
    }
}

float64 HELPER(frecpx_f64)(float64 a, float_status *fpst)
{
    uint64_t val64, sbit;
    int64_t exp;

    if (float64_is_any_nan(a)) {
        float64 nan = a;
        if (float64_is_signaling_nan(a, fpst)) {
            float_raise(float_flag_invalid, fpst);
            if (!get_default_nan_mode(fpst)) {
                nan = float64_silence_nan(a, fpst);
            }
        }
        if (get_default_nan_mode(fpst)) {
            nan = float64_default_nan(fpst);
        }
        return nan;
    }

    a = float64_squash_input_denormal(a, fpst);

    val64 = float64_val(a);
    sbit = 0x8000000000000000ULL & val64;
    exp = extract64(float64_val(a), 52, 11);

    if (exp == 0) {
        return make_float64(sbit | (0x7feULL << 52));
    } else {
        return make_float64(sbit | (~exp & 0x7ffULL) << 52);
    }
}

float32 HELPER(fcvtx_f64_to_f32)(float64 a, float_status *fpst)
{
    float32 r;
    int old = get_float_rounding_mode(fpst);

    set_float_rounding_mode(float_round_to_odd, fpst);
    r = float64_to_float32(a, fpst);
    set_float_rounding_mode(old, fpst);
    return r;
}

/*
 * AH=1 min/max have some odd special cases:
 * comparing two zeroes (regardless of sign), (NaN, anything),
 * or (anything, NaN) should return the second argument (possibly
 * squashed to zero).
 * Also, denormal outputs are not squashed to zero regardless of FZ or FZ16.
 */
#define AH_MINMAX_HELPER(NAME, CTYPE, FLOATTYPE, MINMAX)                \
    CTYPE HELPER(NAME)(CTYPE a, CTYPE b, float_status *fpst)            \
    {                                                                   \
        bool save;                                                      \
        CTYPE r;                                                        \
        a = FLOATTYPE ## _squash_input_denormal(a, fpst);               \
        b = FLOATTYPE ## _squash_input_denormal(b, fpst);               \
        if (FLOATTYPE ## _is_zero(a) && FLOATTYPE ## _is_zero(b)) {     \
            return b;                                                   \
        }                                                               \
        if (FLOATTYPE ## _is_any_nan(a) ||                              \
            FLOATTYPE ## _is_any_nan(b)) {                              \
            float_raise(float_flag_invalid, fpst);                      \
            return b;                                                   \
        }                                                               \
        save = get_flush_to_zero(fpst);                                 \
        set_flush_to_zero(false, fpst);                                 \
        r = FLOATTYPE ## _ ## MINMAX(a, b, fpst);                       \
        set_flush_to_zero(save, fpst);                                  \
        return r;                                                       \
    }

AH_MINMAX_HELPER(vfp_ah_minh, dh_ctype_f16, float16, min)
AH_MINMAX_HELPER(vfp_ah_mins, float32, float32, min)
AH_MINMAX_HELPER(vfp_ah_mind, float64, float64, min)
AH_MINMAX_HELPER(vfp_ah_maxh, dh_ctype_f16, float16, max)
AH_MINMAX_HELPER(vfp_ah_maxs, float32, float32, max)
AH_MINMAX_HELPER(vfp_ah_maxd, float64, float64, max)
AH_MINMAX_HELPER(sme2_ah_fmax_b16, bfloat16, bfloat16, max)
AH_MINMAX_HELPER(sme2_ah_fmin_b16, bfloat16, bfloat16, min)

/* 64-bit versions of the CRC helpers. Note that although the operation
 * (and the prototypes of crc32c() and crc32() mean that only the bottom
 * 32 bits of the accumulator and result are used, we pass and return
 * uint64_t for convenience of the generated code. Unlike the 32-bit
 * instruction set versions, val may genuinely have 64 bits of data in it.
 * The upper bytes of val (above the number specified by 'bytes') must have
 * been zeroed out by the caller.
 */
uint64_t HELPER(crc32_64)(uint64_t acc, uint64_t val, uint32_t bytes)
{
    uint8_t buf[8];

    stq_le_p(buf, val);

    /* zlib crc32 converts the accumulator and output to one's complement.  */
    return crc32(acc ^ 0xffffffff, buf, bytes) ^ 0xffffffff;
}

uint64_t HELPER(crc32c_64)(uint64_t acc, uint64_t val, uint32_t bytes)
{
    uint8_t buf[8];

    stq_le_p(buf, val);

    /* Linux crc32c converts the output to one's complement.  */
    return crc32c(acc, buf, bytes) ^ 0xffffffff;
}

/*
 * AdvSIMD half-precision
 */

#define ADVSIMD_HELPER(name, suffix) HELPER(glue(glue(advsimd_, name), suffix))

#define ADVSIMD_HALFOP(name) \
uint32_t ADVSIMD_HELPER(name, h)(uint32_t a, uint32_t b, float_status *fpst) \
{ \
    return float16_ ## name(a, b, fpst);    \
}

#define ADVSIMD_TWOHALFOP(name)                                         \
uint32_t ADVSIMD_HELPER(name, 2h)(uint32_t two_a, uint32_t two_b,       \
                                  float_status *fpst)                   \
{ \
    float16  a1, a2, b1, b2;                        \
    uint32_t r1, r2;                                \
    a1 = extract32(two_a, 0, 16);                   \
    a2 = extract32(two_a, 16, 16);                  \
    b1 = extract32(two_b, 0, 16);                   \
    b2 = extract32(two_b, 16, 16);                  \
    r1 = float16_ ## name(a1, b1, fpst);            \
    r2 = float16_ ## name(a2, b2, fpst);            \
    return deposit32(r1, 16, 16, r2);               \
}

ADVSIMD_TWOHALFOP(add)
ADVSIMD_TWOHALFOP(sub)
ADVSIMD_TWOHALFOP(mul)
ADVSIMD_TWOHALFOP(div)
ADVSIMD_TWOHALFOP(min)
ADVSIMD_TWOHALFOP(max)
ADVSIMD_TWOHALFOP(minnum)
ADVSIMD_TWOHALFOP(maxnum)

/* Data processing - scalar floating-point and advanced SIMD */
static float16 float16_mulx(float16 a, float16 b, float_status *fpst)
{
    a = float16_squash_input_denormal(a, fpst);
    b = float16_squash_input_denormal(b, fpst);

    if ((float16_is_zero(a) && float16_is_infinity(b)) ||
        (float16_is_infinity(a) && float16_is_zero(b))) {
        /* 2.0 with the sign bit set to sign(A) XOR sign(B) */
        return make_float16((1U << 14) |
                            ((float16_val(a) ^ float16_val(b)) & (1U << 15)));
    }
    return float16_mul(a, b, fpst);
}

ADVSIMD_HALFOP(mulx)
ADVSIMD_TWOHALFOP(mulx)

/* fused multiply-accumulate */
uint32_t HELPER(advsimd_muladdh)(uint32_t a, uint32_t b, uint32_t c,
                                 float_status *fpst)
{
    return float16_muladd(a, b, c, 0, fpst);
}

uint32_t HELPER(advsimd_muladd2h)(uint32_t two_a, uint32_t two_b,
                                  uint32_t two_c, float_status *fpst)
{
    float16  a1, a2, b1, b2, c1, c2;
    uint32_t r1, r2;
    a1 = extract32(two_a, 0, 16);
    a2 = extract32(two_a, 16, 16);
    b1 = extract32(two_b, 0, 16);
    b2 = extract32(two_b, 16, 16);
    c1 = extract32(two_c, 0, 16);
    c2 = extract32(two_c, 16, 16);
    r1 = float16_muladd(a1, b1, c1, 0, fpst);
    r2 = float16_muladd(a2, b2, c2, 0, fpst);
    return deposit32(r1, 16, 16, r2);
}

/*
 * Floating point comparisons produce an integer result. Softfloat
 * routines return float_relation types which we convert to the 0/-1
 * Neon requires.
 */

#define ADVSIMD_CMPRES(test) (test) ? 0xffff : 0

uint32_t HELPER(advsimd_ceq_f16)(uint32_t a, uint32_t b, float_status *fpst)
{
    int compare = float16_compare_quiet(a, b, fpst);
    return ADVSIMD_CMPRES(compare == float_relation_equal);
}

uint32_t HELPER(advsimd_cge_f16)(uint32_t a, uint32_t b, float_status *fpst)
{
    int compare = float16_compare(a, b, fpst);
    return ADVSIMD_CMPRES(compare == float_relation_greater ||
                          compare == float_relation_equal);
}

uint32_t HELPER(advsimd_cgt_f16)(uint32_t a, uint32_t b, float_status *fpst)
{
    int compare = float16_compare(a, b, fpst);
    return ADVSIMD_CMPRES(compare == float_relation_greater);
}

uint32_t HELPER(advsimd_acge_f16)(uint32_t a, uint32_t b, float_status *fpst)
{
    float16 f0 = float16_abs(a);
    float16 f1 = float16_abs(b);
    int compare = float16_compare(f0, f1, fpst);
    return ADVSIMD_CMPRES(compare == float_relation_greater ||
                          compare == float_relation_equal);
}

uint32_t HELPER(advsimd_acgt_f16)(uint32_t a, uint32_t b, float_status *fpst)
{
    float16 f0 = float16_abs(a);
    float16 f1 = float16_abs(b);
    int compare = float16_compare(f0, f1, fpst);
    return ADVSIMD_CMPRES(compare == float_relation_greater);
}

/* round to integral */
uint32_t HELPER(advsimd_rinth_exact)(uint32_t x, float_status *fp_status)
{
    return float16_round_to_int(x, fp_status);
}

uint32_t HELPER(advsimd_rinth)(uint32_t x, float_status *fp_status)
{
    int old_flags = get_float_exception_flags(fp_status), new_flags;
    float16 ret;

    ret = float16_round_to_int(x, fp_status);

    /* Suppress any inexact exceptions the conversion produced */
    if (!(old_flags & float_flag_inexact)) {
        new_flags = get_float_exception_flags(fp_status);
        set_float_exception_flags(new_flags & ~float_flag_inexact, fp_status);
    }

    return ret;
}

#ifndef CONFIG_USER_ONLY
static int el_from_spsr(uint32_t spsr)
{
    /* Return the exception level that this SPSR is requesting a return to,
     * or -1 if it is invalid (an illegal return)
     */
    if (spsr & PSTATE_nRW) {
        switch (spsr & CPSR_M) {
        case ARM_CPU_MODE_USR:
            return 0;
        case ARM_CPU_MODE_HYP:
            return 2;
        case ARM_CPU_MODE_FIQ:
        case ARM_CPU_MODE_IRQ:
        case ARM_CPU_MODE_SVC:
        case ARM_CPU_MODE_ABT:
        case ARM_CPU_MODE_UND:
        case ARM_CPU_MODE_SYS:
            return 1;
        case ARM_CPU_MODE_MON:
            /* Returning to Mon from AArch64 is never possible,
             * so this is an illegal return.
             */
        default:
            return -1;
        }
    } else {
        if (extract32(spsr, 1, 1)) {
            /* Return with reserved M[1] bit set */
            return -1;
        }
        if (extract32(spsr, 0, 4) == 1) {
            /* return to EL0 with M[0] bit set */
            return -1;
        }
        return extract32(spsr, 2, 2);
    }
}

#ifndef CONFIG_USER_ONLY
void auto9_qnx_tee_eret(CPUARMState *env, int cur_el, int new_el, uint64_t pc);
#endif

void HELPER(exception_return)(CPUARMState *env, uint64_t new_pc)
{
    ARMCPU *cpu = env_archcpu(env);
    int cur_el = arm_current_el(env);
    unsigned int spsr_idx = aarch64_banked_spsr_index(cur_el);
    uint64_t spsr = env->banked_spsr[spsr_idx];
    int new_el;
    bool return_to_aa64 = (spsr & PSTATE_nRW) == 0;

    aarch64_save_sp(env, cur_el);

    arm_clear_exclusive(env);

    /* We must squash the PSTATE.SS bit to zero unless both of the
     * following hold:
     *  1. debug exceptions are currently disabled
     *  2. singlestep will be active in the EL we return to
     * We check 1 here and 2 after we've done the pstate/cpsr write() to
     * transition to the EL we're going to.
     */
    if (arm_generate_debug_exceptions(env)) {
        spsr &= ~PSTATE_SS;
    }

    new_el = el_from_spsr(spsr);
    if (new_el == -1) {
        goto illegal_return;
    }
    if (new_el > cur_el || (new_el == 2 && !arm_is_el2_enabled(env))) {
        /* Disallow return to an EL which is unimplemented or higher
         * than the current one.
         */
        goto illegal_return;
    }

    /*
     * FEAT_RME forbids return from EL3 to a lower exception level
     * with an invalid security state.
     * We don't need an explicit check for FEAT_RME here because we enforce
     * in scr_write() that you can't set the NSE bit without it.
     */
    if (cur_el == 3 && new_el < 3 &&
        (env->cp15.scr_el3 & (SCR_NS | SCR_NSE)) == SCR_NSE) {
        goto illegal_return;
    }

    if (new_el != 0 && arm_el_is_aa64(env, new_el) != return_to_aa64) {
        /* Return to an EL which is configured for a different register width */
        goto illegal_return;
    }

    if (!return_to_aa64 && !cpu_isar_feature(aa64_aa32, cpu)) {
        /* Return to AArch32 when CPU is AArch64-only */
        goto illegal_return;
    }

    if (new_el == 1 && (arm_hcr_el2_eff(env) & HCR_TGE)) {
        goto illegal_return;
    }

    /*
     * If GetCurrentEXLOCKEN, the exception return path must use GCSPOPCX,
     * which will set PSTATE.EXLOCK.  We need not explicitly check FEAT_GCS,
     * because GCSCR_ELx cannot be set without it.
     */
    if (new_el == cur_el &&
        (env->cp15.gcscr_el[cur_el] & GCSCR_EXLOCKEN) &&
        !(env->pstate & PSTATE_EXLOCK)) {
        goto illegal_return;
    }

    bql_lock();
    arm_call_pre_el_change_hook(cpu);
    bql_unlock();

    if (!return_to_aa64) {
        env->aarch64 = false;
        /* We do a raw CPSR write because aarch64_sync_64_to_32()
         * will sort the register banks out for us, and we've already
         * caught all the bad-mode cases in el_from_spsr().
         */
        cpsr_write_from_spsr_elx(env, spsr);
        if (!arm_singlestep_active(env)) {
            env->pstate &= ~PSTATE_SS;
        }
        aarch64_sync_64_to_32(env);

        if (spsr & CPSR_T) {
            env->regs[15] = new_pc & ~0x1;
        } else {
            env->regs[15] = new_pc & ~0x3;
        }
        helper_rebuild_hflags_a32(env, new_el);
        qemu_log_mask(CPU_LOG_INT, "Exception return from AArch64 EL%d to "
                      "AArch32 EL%d PC 0x%" PRIx32 "\n",
                      cur_el, new_el, env->regs[15]);
    } else {
        int tbii;

        env->aarch64 = true;
        spsr &= aarch64_pstate_valid_mask(&cpu->isar);
        pstate_write(env, spsr);
        if (!arm_singlestep_active(env)) {
            env->pstate &= ~PSTATE_SS;
        }
        aarch64_restore_sp(env, new_el);
        helper_rebuild_hflags_a64(env, new_el);

        /*
         * Apply TBI to the exception return address.  We had to delay this
         * until after we selected the new EL, so that we could select the
         * correct TBI+TBID bits.  This is made easier by waiting until after
         * the hflags rebuild, since we can pull the composite TBII field
         * from there.
         */
        tbii = EX_TBFLAG_A64(env->hflags, TBII);
        if ((tbii >> extract64(new_pc, 55, 1)) & 1) {
            /* TBI is enabled. */
            int core_mmu_idx = arm_env_mmu_index(env);
            if (regime_has_2_ranges(core_to_aa64_mmu_idx(core_mmu_idx))) {
                new_pc = sextract64(new_pc, 0, 56);
            } else {
                new_pc = extract64(new_pc, 0, 56);
            }
        }
        env->pc = new_pc;
#ifndef CONFIG_USER_ONLY
        auto9_qnx_tee_eret(env, cur_el, new_el, new_pc);
#endif

        qemu_log_mask(CPU_LOG_INT, "Exception return from AArch64 EL%d to "
                      "AArch64 EL%d PC 0x%" PRIx64 "\n",
                      cur_el, new_el, env->pc);
    }

    /*
     * Note that cur_el can never be 0.  If new_el is 0, then
     * el0_a64 is return_to_aa64, else el0_a64 is ignored.
     */
    aarch64_sve_change_el(env, cur_el, new_el, return_to_aa64);

    bql_lock();
    arm_call_el_change_hook(cpu);
    bql_unlock();

    return;

illegal_return:
    /* Illegal return events of various kinds have architecturally
     * mandated behaviour:
     * restore NZCV and DAIF from SPSR_ELx
     * set PSTATE.IL
     * restore PC from ELR_ELx
     * no change to exception level, execution state or stack pointer
     */
    env->pstate |= PSTATE_IL;
    env->pc = new_pc;
    spsr &= PSTATE_NZCV | PSTATE_DAIF | PSTATE_ALLINT;
    spsr |= pstate_read(env) & ~(PSTATE_NZCV | PSTATE_DAIF | PSTATE_ALLINT);
    pstate_write(env, spsr);
    if (!arm_singlestep_active(env)) {
        env->pstate &= ~PSTATE_SS;
    }
    helper_rebuild_hflags_a64(env, cur_el);
    qemu_log_mask(LOG_GUEST_ERROR, "Illegal exception return at EL%d: "
                  "resuming execution at 0x%" PRIx64 "\n", cur_el, env->pc);
}
#endif /* !CONFIG_USER_ONLY */

void HELPER(dc_zva)(CPUARMState *env, uint64_t vaddr_in)
{
    uintptr_t ra = GETPC();

    /*
     * Implement DC ZVA, which zeroes a fixed-length block of memory.
     * Note that we do not implement the (architecturally mandated)
     * alignment fault for attempts to use this on Device memory
     * (which matches the usual QEMU behaviour of not implementing either
     * alignment faults or any memory attribute handling).
     */
    int blocklen = 4 << get_dczid_bs(env_archcpu(env));
    uint64_t vaddr = vaddr_in & ~(blocklen - 1);
    int mmu_idx = arm_env_mmu_index(env);
    void *mem;

    /*
     * Trapless lookup.  In addition to actual invalid page, may
     * return NULL for I/O, watchpoints, clean pages, etc.
     */
    mem = tlb_vaddr_to_host(env, vaddr, MMU_DATA_STORE, mmu_idx);

#ifndef CONFIG_USER_ONLY
    if (unlikely(!mem)) {
        /*
         * Trap if accessing an invalid page.  DC_ZVA requires that we supply
         * the original pointer for an invalid page.  But watchpoints require
         * that we probe the actual space.  So do both.
         */
        (void) probe_write(env, vaddr_in, 1, mmu_idx, ra);
        mem = probe_write(env, vaddr, blocklen, mmu_idx, ra);

        if (unlikely(!mem)) {
            /*
             * The only remaining reason for mem == NULL is I/O.
             * Just do a series of byte writes as the architecture demands.
             */
            for (int i = 0; i < blocklen; i++) {
                cpu_stb_mmuidx_ra(env, vaddr + i, 0, mmu_idx, ra);
            }
            return;
        }
    }
#endif

    set_helper_retaddr(ra);
    memset(mem, 0, blocklen);
    clear_helper_retaddr();
}

void HELPER(arm_unaligned_access)(CPUARMState *env, uint64_t addr,
                                  uint32_t access_type, uint32_t mmu_idx)
{
    arm_cpu_do_unaligned_access(env_cpu(env), addr, access_type,
                                mmu_idx, GETPC());
}

/* Memory operations (memset, memmove, memcpy) */

/*
 * Return true if the CPY* and SET* insns can execute; compare
 * pseudocode CheckMOPSEnabled(), though we refactor it a little.
 */
static bool mops_enabled(CPUARMState *env)
{
    int el = arm_current_el(env);

    if (el < 2 &&
        (arm_hcr_el2_eff(env) & (HCR_E2H | HCR_TGE)) != (HCR_E2H | HCR_TGE) &&
        !(arm_hcrx_el2_eff(env) & HCRX_MSCEN)) {
        return false;
    }

    if (el == 0) {
        if (!el_is_in_host(env, 0)) {
            return env->cp15.sctlr_el[1] & SCTLR_MSCEN;
        } else {
            return env->cp15.sctlr_el[2] & SCTLR_MSCEN;
        }
    }
    return true;
}

static void check_mops_enabled(CPUARMState *env, uintptr_t ra)
{
    if (!mops_enabled(env)) {
        raise_exception_ra(env, EXCP_UDEF, syn_uncategorized(),
                           exception_target_el(env), ra);
    }
}

/*
 * Return the target exception level for an exception due
 * to mismatched arguments in a FEAT_MOPS copy or set.
 * Compare pseudocode MismatchedCpySetTargetEL()
 */
static int mops_mismatch_exception_target_el(CPUARMState *env)
{
    int el = arm_current_el(env);

    if (el > 1) {
        return el;
    }
    if (el == 0 && (arm_hcr_el2_eff(env) & HCR_TGE)) {
        return 2;
    }
    if (el == 1 && (arm_hcrx_el2_eff(env) & HCRX_MCE2)) {
        return 2;
    }
    return 1;
}

/*
 * Check whether an M or E instruction was executed with a CF value
 * indicating the wrong option for this implementation.
 * Assumes we are always Option A.
 */
static void check_mops_wrong_option(CPUARMState *env, uint32_t syndrome,
                                    uintptr_t ra)
{
    if (env->CF != 0) {
        syndrome |= 1 << 17; /* Set the wrong-option bit */
        raise_exception_ra(env, EXCP_UDEF, syndrome,
                           mops_mismatch_exception_target_el(env), ra);
    }
}

/*
 * Return the maximum number of bytes we can transfer starting at addr
 * without crossing a page boundary.
 */
static uint64_t page_limit(uint64_t addr)
{
    return TARGET_PAGE_ALIGN(addr + 1) - addr;
}

/*
 * Return the number of bytes we can copy starting from addr and working
 * backwards without crossing a page boundary.
 */
static uint64_t page_limit_rev(uint64_t addr)
{
    return (addr & ~TARGET_PAGE_MASK) + 1;
}

/*
 * Perform part of a memory set on an area of guest memory starting at
 * toaddr (a dirty address) and extending for setsize bytes.
 *
 * Returns the number of bytes actually set, which might be less than
 * setsize; the caller should loop until the whole set has been done.
 * The caller should ensure that the guest registers are correct
 * for the possibility that the first byte of the set encounters
 * an exception or watchpoint. We guarantee not to take any faults
 * for bytes other than the first.
 */
static uint64_t set_step(CPUARMState *env, uint64_t toaddr,
                         uint64_t setsize, uint32_t data, int memidx,
                         uint32_t *mtedesc, uintptr_t ra)
{
    void *mem;

    setsize = MIN(setsize, page_limit(toaddr));
    if (*mtedesc) {
        uint64_t mtesize = mte_mops_probe(env, toaddr, setsize, *mtedesc);
        if (mtesize == 0) {
            /* Trap, or not. All CPU state is up to date */
            mte_check_fail(env, *mtedesc, toaddr, ra);
            /* Continue, with no further MTE checks required */
            *mtedesc = 0;
        } else {
            /* Advance to the end, or to the tag mismatch */
            setsize = MIN(setsize, mtesize);
        }
    }

    toaddr = useronly_clean_ptr(toaddr);
    /*
     * Trapless lookup: returns NULL for invalid page, I/O,
     * watchpoints, clean pages, etc.
     */
    mem = tlb_vaddr_to_host(env, toaddr, MMU_DATA_STORE, memidx);

#ifndef CONFIG_USER_ONLY
    if (unlikely(!mem)) {
        /*
         * Slow-path: just do one byte write. This will handle the
         * watchpoint, invalid page, etc handling correctly.
         * For clean code pages, the next iteration will see
         * the page dirty and will use the fast path.
         */
        cpu_stb_mmuidx_ra(env, toaddr, data, memidx, ra);
        return 1;
    }
#endif
    /* Easy case: just memset the host memory */
    set_helper_retaddr(ra);
    memset(mem, data, setsize);
    clear_helper_retaddr();
    return setsize;
}

/*
 * Similar, but setting tags. The architecture requires us to do this
 * in 16-byte chunks. SETP accesses are not tag checked; they set
 * the tags.
 */
static uint64_t set_step_tags(CPUARMState *env, uint64_t toaddr,
                              uint64_t setsize, uint32_t data, int memidx,
                              uint32_t *mtedesc, uintptr_t ra)
{
    void *mem;
    uint64_t cleanaddr;

    setsize = MIN(setsize, page_limit(toaddr));

    cleanaddr = useronly_clean_ptr(toaddr);
    /*
     * Trapless lookup: returns NULL for invalid page, I/O,
     * watchpoints, clean pages, etc.
     */
    mem = tlb_vaddr_to_host(env, cleanaddr, MMU_DATA_STORE, memidx);

#ifndef CONFIG_USER_ONLY
    if (unlikely(!mem)) {
        /*
         * Slow-path: just do one write. This will handle the
         * watchpoint, invalid page, etc handling correctly.
         * The architecture requires that we do 16 bytes at a time,
         * and we know both ptr and size are 16 byte aligned.
         * For clean code pages, the next iteration will see
         * the page dirty and will use the fast path.
         */
        uint64_t repldata = data * 0x0101010101010101ULL;
        MemOpIdx oi16 = make_memop_idx(MO_128, memidx);
        cpu_st16_mmu(env, toaddr, int128_make128(repldata, repldata), oi16, ra);
        mte_mops_set_tags(env, toaddr, 16, *mtedesc);
        return 16;
    }
#endif
    /* Easy case: just memset the host memory */
    set_helper_retaddr(ra);
    memset(mem, data, setsize);
    clear_helper_retaddr();
    mte_mops_set_tags(env, toaddr, setsize, *mtedesc);
    return setsize;
}

typedef uint64_t StepFn(CPUARMState *env, uint64_t toaddr,
                        uint64_t setsize, uint32_t data,
                        int memidx, uint32_t *mtedesc, uintptr_t ra);

/* Extract register numbers from a MOPS exception syndrome value */
static int mops_destreg(uint32_t syndrome)
{
    return extract32(syndrome, 10, 5);
}

static int mops_srcreg(uint32_t syndrome)
{
    return extract32(syndrome, 5, 5);
}

static int mops_sizereg(uint32_t syndrome)
{
    return extract32(syndrome, 0, 5);
}

/*
 * Return true if the TCMA, TBI, and MTX bits mean we need to do MTE checks.
 * We only need to do this once per MOPS insn, not for every page.
 */
static bool mte_checks_needed(uint64_t ptr, uint32_t desc)
{
    int bit55 = extract64(ptr, 55, 1);

    /*
     * Note that tbi_or_mtx_check() return true for "access checked", but
     * tcma_check() returns true for "access unchecked".
     */
    if (!tbi_or_mtx_check(desc, bit55)) {
        return false;
    }

    return !tcma_check(desc, bit55, allocation_tag_from_addr(ptr));
}

/* Take an exception if the SETG addr/size are not granule aligned */
static void check_setg_alignment(CPUARMState *env, uint64_t ptr, uint64_t size,
                                 uint32_t memidx, uintptr_t ra)
{
    if ((size != 0 && !QEMU_IS_ALIGNED(ptr, TAG_GRANULE)) ||
        !QEMU_IS_ALIGNED(size, TAG_GRANULE)) {
        arm_cpu_do_unaligned_access(env_cpu(env), ptr, MMU_DATA_STORE,
                                    memidx, ra);

    }
}

static uint64_t arm_reg_or_xzr(CPUARMState *env, int reg)
{
    /*
     * Runtime equivalent of cpu_reg() -- return the CPU register value,
     * for contexts when index 31 means XZR (not SP).
     */
    return reg == 31 ? 0 : env->xregs[reg];
}

/*
 * For the Memory Set operation, our implementation chooses
 * always to use "option A", where we update Xd to the final
 * address in the SETP insn, and set Xn to be -(bytes remaining).
 * On SETM and SETE insns we only need update Xn.
 *
 * @env: CPU
 * @syndrome: syndrome value for mismatch exceptions
 * (also contains the register numbers we need to use)
 * @mtedesc: MTE descriptor word
 * @stepfn: function which does a single part of the set operation
 * @is_setg: true if this is the tag-setting SETG variant
 */
static void do_setp(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc,
                    StepFn *stepfn, bool is_setg, uintptr_t ra)
{
    /* Prologue: we choose to do up to the next page boundary */
    int rd = mops_destreg(syndrome);
    int rs = mops_srcreg(syndrome);
    int rn = mops_sizereg(syndrome);
    uint8_t data = arm_reg_or_xzr(env, rs);
    uint32_t memidx = FIELD_EX32(mtedesc, MTEDESC, MIDX);
    uint64_t toaddr = env->xregs[rd];
    uint64_t setsize = env->xregs[rn];
    uint64_t stagesetsize, step;

    check_mops_enabled(env, ra);

    if (setsize > INT64_MAX) {
        setsize = INT64_MAX;
        if (is_setg) {
            setsize &= ~0xf;
        }
    }

    if (unlikely(is_setg)) {
        check_setg_alignment(env, toaddr, setsize, memidx, ra);
    } else if (!mte_checks_needed(toaddr, mtedesc)) {
        mtedesc = 0;
    }

    stagesetsize = MIN(setsize, page_limit(toaddr));
    while (stagesetsize) {
        env->xregs[rd] = toaddr;
        env->xregs[rn] = setsize;
        step = stepfn(env, toaddr, stagesetsize, data, memidx, &mtedesc, ra);
        toaddr += step;
        setsize -= step;
        stagesetsize -= step;
    }
    /* Insn completed, so update registers to the Option A format */
    env->xregs[rd] = toaddr + setsize;
    env->xregs[rn] = -setsize;

    /* Set NZCV = 0000 to indicate we are an Option A implementation */
    env->NF = 0;
    env->ZF = 1; /* our env->ZF encoding is inverted */
    env->CF = 0;
    env->VF = 0;
}

void HELPER(setp)(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc)
{
    do_setp(env, syndrome, mtedesc, set_step, false, GETPC());
}

void HELPER(setgp)(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc)
{
    do_setp(env, syndrome, mtedesc, set_step_tags, true, GETPC());
}

static void do_setm(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc,
                    StepFn *stepfn, bool is_setg, uintptr_t ra)
{
    /* Main: we choose to do all the full-page chunks */
    CPUState *cs = env_cpu(env);
    int rd = mops_destreg(syndrome);
    int rs = mops_srcreg(syndrome);
    int rn = mops_sizereg(syndrome);
    uint8_t data = arm_reg_or_xzr(env, rs);
    uint64_t toaddr = env->xregs[rd] + env->xregs[rn];
    uint64_t setsize = -env->xregs[rn];
    uint32_t memidx = FIELD_EX32(mtedesc, MTEDESC, MIDX);
    uint64_t step, stagesetsize;

    check_mops_enabled(env, ra);

    /*
     * We're allowed to NOP out "no data to copy" before the consistency
     * checks; we choose to do so.
     */
    if (env->xregs[rn] == 0) {
        return;
    }

    check_mops_wrong_option(env, syndrome, ra);

    /*
     * Our implementation will work fine even if we have an unaligned
     * destination address, and because we update Xn every time around
     * the loop below and the return value from stepfn() may be less
     * than requested, we might find toaddr is unaligned. So we don't
     * have an IMPDEF check for alignment here.
     */

    if (unlikely(is_setg)) {
        check_setg_alignment(env, toaddr, setsize, memidx, ra);
    } else if (!mte_checks_needed(toaddr, mtedesc)) {
        mtedesc = 0;
    }

    /* Do the actual memset: we leave the last partial page to SETE */
    stagesetsize = setsize & TARGET_PAGE_MASK;
    while (stagesetsize > 0) {
        step = stepfn(env, toaddr, stagesetsize, data, memidx, &mtedesc, ra);
        toaddr += step;
        setsize -= step;
        stagesetsize -= step;
        env->xregs[rn] = -setsize;
        if (stagesetsize > 0 && unlikely(cpu_loop_exit_requested(cs))) {
            cpu_loop_exit_restore(cs, ra);
        }
    }
}

void HELPER(setm)(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc)
{
    do_setm(env, syndrome, mtedesc, set_step, false, GETPC());
}

void HELPER(setgm)(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc)
{
    do_setm(env, syndrome, mtedesc, set_step_tags, true, GETPC());
}

static void do_sete(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc,
                    StepFn *stepfn, bool is_setg, uintptr_t ra)
{
    /* Epilogue: do the last partial page */
    int rd = mops_destreg(syndrome);
    int rs = mops_srcreg(syndrome);
    int rn = mops_sizereg(syndrome);
    uint8_t data = arm_reg_or_xzr(env, rs);
    uint64_t toaddr = env->xregs[rd] + env->xregs[rn];
    uint64_t setsize = -env->xregs[rn];
    uint32_t memidx = FIELD_EX32(mtedesc, MTEDESC, MIDX);
    uint64_t step;

    check_mops_enabled(env, ra);

    /*
     * We're allowed to NOP out "no data to copy" before the consistency
     * checks; we choose to do so.
     */
    if (setsize == 0) {
        return;
    }

    check_mops_wrong_option(env, syndrome, ra);

    /*
     * Our implementation has no address alignment requirements, but
     * we do want to enforce the "less than a page" size requirement,
     * so we don't need to have the "check for interrupts" here.
     */
    if (setsize >= TARGET_PAGE_SIZE) {
        raise_exception_ra(env, EXCP_UDEF, syndrome,
                           mops_mismatch_exception_target_el(env), ra);
    }

    if (unlikely(is_setg)) {
        check_setg_alignment(env, toaddr, setsize, memidx, ra);
    } else if (!mte_checks_needed(toaddr, mtedesc)) {
        mtedesc = 0;
    }

    /* Do the actual memset */
    while (setsize > 0) {
        step = stepfn(env, toaddr, setsize, data, memidx, &mtedesc, ra);
        toaddr += step;
        setsize -= step;
        env->xregs[rn] = -setsize;
    }
}

void HELPER(sete)(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc)
{
    do_sete(env, syndrome, mtedesc, set_step, false, GETPC());
}

void HELPER(setge)(CPUARMState *env, uint32_t syndrome, uint32_t mtedesc)
{
    do_sete(env, syndrome, mtedesc, set_step_tags, true, GETPC());
}

/*
 * Perform part of a memory copy from the guest memory at fromaddr
 * and extending for copysize bytes, to the guest memory at
 * toaddr. Both addresses are dirty.
 *
 * Returns the number of bytes actually set, which might be less than
 * copysize; the caller should loop until the whole copy has been done.
 * The caller should ensure that the guest registers are correct
 * for the possibility that the first byte of the copy encounters
 * an exception or watchpoint. We guarantee not to take any faults
 * for bytes other than the first.
 */
static uint64_t copy_step(CPUARMState *env, uint64_t toaddr, uint64_t fromaddr,
                          uint64_t copysize, int wmemidx, int rmemidx,
                          uint32_t *wdesc, uint32_t *rdesc, uintptr_t ra)
{
    void *rmem;
    void *wmem;

    /* Don't cross a page boundary on either source or destination */
    copysize = MIN(copysize, page_limit(toaddr));
    copysize = MIN(copysize, page_limit(fromaddr));
    /*
     * Handle MTE tag checks: either handle the tag mismatch for byte 0,
     * or else copy up to but not including the byte with the mismatch.
     */
    if (*rdesc) {
        uint64_t mtesize = mte_mops_probe(env, fromaddr, copysize, *rdesc);
        if (mtesize == 0) {
            mte_check_fail(env, *rdesc, fromaddr, ra);
            *rdesc = 0;
        } else {
            copysize = MIN(copysize, mtesize);
        }
    }
    if (*wdesc) {
        uint64_t mtesize = mte_mops_probe(env, toaddr, copysize, *wdesc);
        if (mtesize == 0) {
            mte_check_fail(env, *wdesc, toaddr, ra);
            *wdesc = 0;
        } else {
            copysize = MIN(copysize, mtesize);
        }
    }

    toaddr = useronly_clean_ptr(toaddr);
    fromaddr = useronly_clean_ptr(fromaddr);
    /* Trapless lookup of whether we can get a host memory pointer */
    wmem = tlb_vaddr_to_host(env, toaddr, MMU_DATA_STORE, wmemidx);
    rmem = tlb_vaddr_to_host(env, fromaddr, MMU_DATA_LOAD, rmemidx);

#ifndef CONFIG_USER_ONLY
    /*
     * If we don't have host memory for both source and dest then just
     * do a single byte copy. This will handle watchpoints, invalid pages,
     * etc correctly. For clean code pages, the next iteration will see
     * the page dirty and will use the fast path.
     */
    if (unlikely(!rmem || !wmem)) {
        uint8_t byte;
        if (rmem) {
            byte = *(uint8_t *)rmem;
        } else {
            byte = cpu_ldub_mmuidx_ra(env, fromaddr, rmemidx, ra);
        }
        if (wmem) {
            *(uint8_t *)wmem = byte;
        } else {
            cpu_stb_mmuidx_ra(env, toaddr, byte, wmemidx, ra);
        }
        return 1;
    }
#endif
    /* Easy case: just memmove the host memory */
    set_helper_retaddr(ra);
    memmove(wmem, rmem, copysize);
    clear_helper_retaddr();
    return copysize;
}

/*
 * Do part of a backwards memory copy. Here toaddr and fromaddr point
 * to the *last* byte to be copied.
 */
static uint64_t copy_step_rev(CPUARMState *env, uint64_t toaddr,
                              uint64_t fromaddr,
                              uint64_t copysize, int wmemidx, int rmemidx,
                              uint32_t *wdesc, uint32_t *rdesc, uintptr_t ra)
{
    void *rmem;
    void *wmem;

    /* Don't cross a page boundary on either source or destination */
    copysize = MIN(copysize, page_limit_rev(toaddr));
    copysize = MIN(copysize, page_limit_rev(fromaddr));

    /*
     * Handle MTE tag checks: either handle the tag mismatch for byte 0,
     * or else copy up to but not including the byte with the mismatch.
     */
    if (*rdesc) {
        uint64_t mtesize = mte_mops_probe_rev(env, fromaddr, copysize, *rdesc);
        if (mtesize == 0) {
            mte_check_fail(env, *rdesc, fromaddr, ra);
            *rdesc = 0;
        } else {
            copysize = MIN(copysize, mtesize);
        }
    }
    if (*wdesc) {
        uint64_t mtesize = mte_mops_probe_rev(env, toaddr, copysize, *wdesc);
        if (mtesize == 0) {
            mte_check_fail(env, *wdesc, toaddr, ra);
            *wdesc = 0;
        } else {
            copysize = MIN(copysize, mtesize);
        }
    }

    toaddr = useronly_clean_ptr(toaddr);
    fromaddr = useronly_clean_ptr(fromaddr);
    /* Trapless lookup of whether we can get a host memory pointer */
    wmem = tlb_vaddr_to_host(env, toaddr, MMU_DATA_STORE, wmemidx);
    rmem = tlb_vaddr_to_host(env, fromaddr, MMU_DATA_LOAD, rmemidx);

#ifndef CONFIG_USER_ONLY
    /*
     * If we don't have host memory for both source and dest then just
     * do a single byte copy. This will handle watchpoints, invalid pages,
     * etc correctly. For clean code pages, the next iteration will see
     * the page dirty and will use the fast path.
     */
    if (unlikely(!rmem || !wmem)) {
        uint8_t byte;
        if (rmem) {
            byte = *(uint8_t *)rmem;
        } else {
            byte = cpu_ldub_mmuidx_ra(env, fromaddr, rmemidx, ra);
        }
        if (wmem) {
            *(uint8_t *)wmem = byte;
        } else {
            cpu_stb_mmuidx_ra(env, toaddr, byte, wmemidx, ra);
        }
        return 1;
    }
#endif
    /*
     * Easy case: just memmove the host memory. Note that wmem and
     * rmem here point to the *last* byte to copy.
     */
    set_helper_retaddr(ra);
    memmove(wmem - (copysize - 1), rmem - (copysize - 1), copysize);
    clear_helper_retaddr();
    return copysize;
}

/*
 * for the Memory Copy operation, our implementation chooses always
 * to use "option A", where we update Xd and Xs to the final addresses
 * in the CPYP insn, and then in CPYM and CPYE only need to update Xn.
 *
 * @env: CPU
 * @syndrome: syndrome value for mismatch exceptions
 * (also contains the register numbers we need to use)
 * @wdesc: MTE descriptor for the writes (destination)
 * @rdesc: MTE descriptor for the reads (source)
 * @move: true if this is CPY (memmove), false for CPYF (memcpy forwards)
 */
static void do_cpyp(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                    uint32_t rdesc, uint32_t move, uintptr_t ra)
{
    int rd = mops_destreg(syndrome);
    int rs = mops_srcreg(syndrome);
    int rn = mops_sizereg(syndrome);
    uint32_t rmemidx = FIELD_EX32(rdesc, MTEDESC, MIDX);
    uint32_t wmemidx = FIELD_EX32(wdesc, MTEDESC, MIDX);
    bool forwards = true;
    uint64_t toaddr = env->xregs[rd];
    uint64_t fromaddr = env->xregs[rs];
    uint64_t copysize = env->xregs[rn];
    uint64_t stagecopysize, step;

    check_mops_enabled(env, ra);


    if (move) {
        /*
         * Copy backwards if necessary. The direction for a non-overlapping
         * copy is IMPDEF; we choose forwards.
         */
        if (copysize > 0x007FFFFFFFFFFFFFULL) {
            copysize = 0x007FFFFFFFFFFFFFULL;
        }
        uint64_t fs = extract64(fromaddr, 0, 56);
        uint64_t ts = extract64(toaddr, 0, 56);
        uint64_t fe = extract64(fromaddr + copysize, 0, 56);

        if (fs < ts && fe > ts) {
            forwards = false;
        }
    } else {
        if (copysize > INT64_MAX) {
            copysize = INT64_MAX;
        }
    }

    if (!mte_checks_needed(fromaddr, rdesc)) {
        rdesc = 0;
    }
    if (!mte_checks_needed(toaddr, wdesc)) {
        wdesc = 0;
    }

    if (forwards) {
        stagecopysize = MIN(copysize, page_limit(toaddr));
        stagecopysize = MIN(stagecopysize, page_limit(fromaddr));
        while (stagecopysize) {
            env->xregs[rd] = toaddr;
            env->xregs[rs] = fromaddr;
            env->xregs[rn] = copysize;
            step = copy_step(env, toaddr, fromaddr, stagecopysize,
                             wmemidx, rmemidx, &wdesc, &rdesc, ra);
            toaddr += step;
            fromaddr += step;
            copysize -= step;
            stagecopysize -= step;
        }
        /* Insn completed, so update registers to the Option A format */
        env->xregs[rd] = toaddr + copysize;
        env->xregs[rs] = fromaddr + copysize;
        env->xregs[rn] = -copysize;
    } else {
        /*
         * In a reverse copy the to and from addrs in Xs and Xd are the start
         * of the range, but it's more convenient for us to work with pointers
         * to the last byte being copied.
         */
        toaddr += copysize - 1;
        fromaddr += copysize - 1;
        stagecopysize = MIN(copysize, page_limit_rev(toaddr));
        stagecopysize = MIN(stagecopysize, page_limit_rev(fromaddr));
        while (stagecopysize) {
            env->xregs[rn] = copysize;
            step = copy_step_rev(env, toaddr, fromaddr, stagecopysize,
                                 wmemidx, rmemidx, &wdesc, &rdesc, ra);
            copysize -= step;
            stagecopysize -= step;
            toaddr -= step;
            fromaddr -= step;
        }
        /*
         * Insn completed, so update registers to the Option A format.
         * For a reverse copy this is no different to the CPYP input format.
         */
        env->xregs[rn] = copysize;
    }

    /* Set NZCV = 0000 to indicate we are an Option A implementation */
    env->NF = 0;
    env->ZF = 1; /* our env->ZF encoding is inverted */
    env->CF = 0;
    env->VF = 0;
}

void HELPER(cpyp)(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                  uint32_t rdesc)
{
    do_cpyp(env, syndrome, wdesc, rdesc, true, GETPC());
}

void HELPER(cpyfp)(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                   uint32_t rdesc)
{
    do_cpyp(env, syndrome, wdesc, rdesc, false, GETPC());
}

static void do_cpym(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                    uint32_t rdesc, uint32_t move, uintptr_t ra)
{
    /* Main: we choose to copy until less than a page remaining */
    CPUState *cs = env_cpu(env);
    int rd = mops_destreg(syndrome);
    int rs = mops_srcreg(syndrome);
    int rn = mops_sizereg(syndrome);
    uint32_t rmemidx = FIELD_EX32(rdesc, MTEDESC, MIDX);
    uint32_t wmemidx = FIELD_EX32(wdesc, MTEDESC, MIDX);
    bool forwards = true;
    uint64_t toaddr, fromaddr, copysize, step;

    check_mops_enabled(env, ra);

    /* We choose to NOP out "no data to copy" before consistency checks */
    if (env->xregs[rn] == 0) {
        return;
    }

    check_mops_wrong_option(env, syndrome, ra);

    if (move) {
        forwards = (int64_t)env->xregs[rn] < 0;
    }

    if (forwards) {
        toaddr = env->xregs[rd] + env->xregs[rn];
        fromaddr = env->xregs[rs] + env->xregs[rn];
        copysize = -env->xregs[rn];
    } else {
        copysize = env->xregs[rn];
        /* This toaddr and fromaddr point to the *last* byte to copy */
        toaddr = env->xregs[rd] + copysize - 1;
        fromaddr = env->xregs[rs] + copysize - 1;
    }

    if (!mte_checks_needed(fromaddr, rdesc)) {
        rdesc = 0;
    }
    if (!mte_checks_needed(toaddr, wdesc)) {
        wdesc = 0;
    }

    /* Our implementation has no particular parameter requirements for CPYM */

    /* Do the actual memmove */
    if (forwards) {
        while (copysize >= TARGET_PAGE_SIZE) {
            step = copy_step(env, toaddr, fromaddr, copysize,
                             wmemidx, rmemidx, &wdesc, &rdesc, ra);
            toaddr += step;
            fromaddr += step;
            copysize -= step;
            env->xregs[rn] = -copysize;
            if (copysize >= TARGET_PAGE_SIZE &&
                unlikely(cpu_loop_exit_requested(cs))) {
                cpu_loop_exit_restore(cs, ra);
            }
        }
    } else {
        while (copysize >= TARGET_PAGE_SIZE) {
            step = copy_step_rev(env, toaddr, fromaddr, copysize,
                                 wmemidx, rmemidx, &wdesc, &rdesc, ra);
            toaddr -= step;
            fromaddr -= step;
            copysize -= step;
            env->xregs[rn] = copysize;
            if (copysize >= TARGET_PAGE_SIZE &&
                unlikely(cpu_loop_exit_requested(cs))) {
                cpu_loop_exit_restore(cs, ra);
            }
        }
    }
}

void HELPER(cpym)(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                  uint32_t rdesc)
{
    do_cpym(env, syndrome, wdesc, rdesc, true, GETPC());
}

void HELPER(cpyfm)(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                   uint32_t rdesc)
{
    do_cpym(env, syndrome, wdesc, rdesc, false, GETPC());
}

static void do_cpye(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                    uint32_t rdesc, uint32_t move, uintptr_t ra)
{
    /* Epilogue: do the last partial page */
    int rd = mops_destreg(syndrome);
    int rs = mops_srcreg(syndrome);
    int rn = mops_sizereg(syndrome);
    uint32_t rmemidx = FIELD_EX32(rdesc, MTEDESC, MIDX);
    uint32_t wmemidx = FIELD_EX32(wdesc, MTEDESC, MIDX);
    bool forwards = true;
    uint64_t toaddr, fromaddr, copysize, step;

    check_mops_enabled(env, ra);

    /* We choose to NOP out "no data to copy" before consistency checks */
    if (env->xregs[rn] == 0) {
        return;
    }

    check_mops_wrong_option(env, syndrome, ra);

    if (move) {
        forwards = (int64_t)env->xregs[rn] < 0;
    }

    if (forwards) {
        toaddr = env->xregs[rd] + env->xregs[rn];
        fromaddr = env->xregs[rs] + env->xregs[rn];
        copysize = -env->xregs[rn];
    } else {
        copysize = env->xregs[rn];
        /* This toaddr and fromaddr point to the *last* byte to copy */
        toaddr = env->xregs[rd] + copysize - 1;
        fromaddr = env->xregs[rs] + copysize - 1;
    }

    if (!mte_checks_needed(fromaddr, rdesc)) {
        rdesc = 0;
    }
    if (!mte_checks_needed(toaddr, wdesc)) {
        wdesc = 0;
    }

    /* Check the size; we don't want to have do a check-for-interrupts */
    if (copysize >= TARGET_PAGE_SIZE) {
        raise_exception_ra(env, EXCP_UDEF, syndrome,
                           mops_mismatch_exception_target_el(env), ra);
    }

    /* Do the actual memmove */
    if (forwards) {
        while (copysize > 0) {
            step = copy_step(env, toaddr, fromaddr, copysize,
                             wmemidx, rmemidx, &wdesc, &rdesc, ra);
            toaddr += step;
            fromaddr += step;
            copysize -= step;
            env->xregs[rn] = -copysize;
        }
    } else {
        while (copysize > 0) {
            step = copy_step_rev(env, toaddr, fromaddr, copysize,
                                 wmemidx, rmemidx, &wdesc, &rdesc, ra);
            toaddr -= step;
            fromaddr -= step;
            copysize -= step;
            env->xregs[rn] = copysize;
        }
    }
}

void HELPER(cpye)(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                  uint32_t rdesc)
{
    do_cpye(env, syndrome, wdesc, rdesc, true, GETPC());
}

void HELPER(cpyfe)(CPUARMState *env, uint32_t syndrome, uint32_t wdesc,
                   uint32_t rdesc)
{
    do_cpye(env, syndrome, wdesc, rdesc, false, GETPC());
}

static bool is_guarded_page(CPUARMState *env, vaddr addr, uintptr_t ra)
{
#ifdef CONFIG_USER_ONLY
    return page_get_flags(addr) & PAGE_BTI;
#else
    CPUTLBEntryFull *full;
    void *host;
    int mmu_idx = cpu_mmu_index(env_cpu(env), true);
    int flags = probe_access_full(env, addr, 0, MMU_INST_FETCH, mmu_idx,
                                  false, &host, &full, ra);

    assert(!(flags & TLB_INVALID_MASK));
    return full->extra.arm.guarded;
#endif
}

void HELPER(guarded_page_check)(CPUARMState *env)
{
    /*
     * We have already verified that bti is enabled, and that the
     * instruction at PC is not ok for BTYPE.  This is always at
     * the beginning of a block, so PC is always up-to-date and
     * no unwind is required.
     */
    if (is_guarded_page(env, env->pc, 0)) {
        raise_exception(env, EXCP_UDEF, syn_btitrap(env->btype),
                        exception_target_el(env));
    }
}

void HELPER(guarded_page_br)(CPUARMState *env, vaddr pc)
{
    /*
     * We have already checked for branch via x16 and x17.
     * What remains for choosing BTYPE is checking for a guarded page.
     */
    env->btype = is_guarded_page(env, pc, GETPC()) ? 3 : 1;
}
