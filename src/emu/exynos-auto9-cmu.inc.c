/* SPDX-License-Identifier: GPL-2.0-or-later
 * Bounded Auto9 CMU fabric: five TOP, eight observed MIF, and nine original
 * CAL-declared auxiliary PLLs (AUD/MMC signed fractional K included), plus
 * source-bound mux/div/QCH configuration and opt-in behavioral PSCDC.
 * Port addresses come from the original HCB, not an
 * identity mapping/RAM substitute. The virtual reset selects the 26MHz
 * reference; it is NOT a recovered BL2/physical CMU register snapshot.
 * PLL fields/formula: upstream Samsung clk-pll.c; addresses: original ACPM
 * sub_2074E/sub_20F88 and Samsung clk-exynosautov9.c. Lock deadlines use the
 * programmed reference-cycle counter, not read counts or constant ready.
 * Per-domain gates, full clock-tree wiring and analog dynamics are absent.
 */
#define TYPE_EXYNOS_AUTO9_CMU "exynos-auto9.cmu-fabric"
#include "qapi/visitor.h"
#define AUTO9_CMU_PORTS 39
#define AUTO9_CMU_WORDS (0x8000 / 4)
#define AUTO9_PLL_COUNT (13 + G_N_ELEMENTS(exynos_auto9_aux_plls))
#define AUTO9_PLL_ENABLE (1U << 31)
#define AUTO9_PLL_LOCKED (1U << 29)
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Cmu, EXYNOS_AUTO9_CMU)
typedef struct ExynosAuto9CmuPort {
    ExynosAuto9Cmu *fabric;
    unsigned index;
} ExynosAuto9CmuPort;
typedef struct ExynosAuto9Pll {
    ExynosAuto9Cmu *fabric;
    QEMUTimer *deadline;
    Clock *raw, *selected;
    uint64_t raw_hz, selected_hz, queued_hz;
    unsigned index, port, slot;
    uint64_t base;
} ExynosAuto9Pll;
struct ExynosAuto9Cmu {
    SysBusDevice parent_obj;
    MemoryRegion mmio[AUTO9_CMU_PORTS];
    ExynosAuto9CmuPort ports[AUTO9_CMU_PORTS];
    hwaddr bases[AUTO9_CMU_PORTS];
    uint32_t words[AUTO9_CMU_PORTS][AUTO9_CMU_WORDS];
    ExynosAuto9Pll plls[AUTO9_PLL_COUNT];
    unsigned top_index;
    uint64_t reference_hz;
    uint32_t locked_mask, lock_completions, writes;
    uint32_t fsys1_gate_reads, fsys1_gate_writes, fsys1_gate_enabled;
    uint32_t fsys1_gate_read_mask, fsys1_gate_write_mask;
    uint32_t pll_count;
    Clock *core_bus_mux, *core_bus_div, *mif_switch_mux;
    uint64_t core_bus_mux_hz, core_bus_div_hz, mif_switch_mux_hz;
    uint32_t queue_entries[8], mirror_entries[8];
    uint32_t queue_control, mirror_control, queue_valid, mirror_valid;
    uint32_t queue_rejected, mirror_rejected, last_queue_request, last_mirror_request;
    /* Explicit opt-in virtual transaction contract, NOT recovered Auto9 ISA. */
    uint64_t pscdc_profile, pscdc_cycles;
    QEMUTimer *pscdc_deadline;
    uint32_t active_queue[8], active_mirror[2], active_count;
    uint32_t mirror_status, queue_completions, mirror_completions;
    uint32_t queue_cancellations, queue_waits, memory_pause, phase_barriers;
    uint32_t phy_auto_gate_mask;
    bool standalone_mirror, apply_mirror;
    Clock *mif_phy[4];
    uint64_t mif_phy_hz[4];
};

static void exynos_auto9_cmu_update_topology(ExynosAuto9Cmu *s);

/* virtual-oem-v1 assumptions:
 * 00010000/80020000 open a logical memory-clock change envelope;
 * 80040000 commits the armed replica, 80030000 closes the envelope.
 * Zero is an empty slot. Direct 12xx words are bounded TOP field writes.
 * Replica 600 selects the four known MIF DDRPHY2X parents; A08 latches a
 * virtual automatic-gating policy, not an electrical DDRPHY gate model.
 * No RAM stall, training result, ACPM-ready value, or guest image is injected.
 * All words validate before work starts. Completion needs a virtual deadline
 * and nonzero selected clock sources; unknown words still fail the request.
 */
static int exynos_auto9_cmu_mif_port(ExynosAuto9Cmu *s, unsigned bank)
{
    return s->plls[5 + 2 * bank].port;
}
static bool exynos_auto9_pscdc_replica_valid(ExynosAuto9Cmu *s)
{
    uint32_t first = s->mirror_entries[0], second = s->mirror_entries[1];
    return (s->mirror_valid & 3) == 3 &&
           (first & 0xff00ffffU) == 0x12000600 &&
           (((first >> 16) & 0xff) & ~0x30U) == 0 &&
           (second & 0xfffeffffU) == 0x10000a08;
}
static uint32_t exynos_auto9_pscdc_top_mask(uint32_t word)
{
    if ((word & 0xff00ffffU) == 0x12001020 ||
        (word & 0xff00ffffU) == 0x1200109c) { return 7; }
    if ((word & 0xff00ffffU) == 0x1200181c) { return 15; }
    return 0;
}
static uint64_t exynos_auto9_pscdc_delay(ExynosAuto9Cmu *s)
{
    return (s->pscdc_cycles * NANOSECONDS_PER_SECOND + s->reference_hz - 1) /
           s->reference_hz;
}
static void exynos_auto9_pscdc_busy_fields(ExynosAuto9Cmu *s, bool busy)
{
    for (unsigned i = 0; i < s->active_count; i++) {
        uint32_t word = s->active_queue[i];
        if (exynos_auto9_pscdc_top_mask(word)) {
            uint32_t *reg = &s->words[s->top_index][(word & 0xffff) / 4];
            *reg = (*reg & ~0x10000U) | (busy ? 0x10000U : 0);
        }
    }
    if (s->apply_mirror || s->standalone_mirror) {
        for (unsigned i = 0; i < 4; i++) {
            uint32_t *reg = &s->words[exynos_auto9_cmu_mif_port(s, i)][0x600 / 4];
            *reg = (*reg & ~0x10000U) | (busy ? 0x10000U : 0);
        }
    }
}
static void exynos_auto9_pscdc_cancel(ExynosAuto9Cmu *s)
{
    if ((s->queue_control & 0x100) || s->mirror_status) { s->queue_cancellations++; }
    timer_del(s->pscdc_deadline);
    exynos_auto9_pscdc_busy_fields(s, false);
    s->queue_control &= ~0x101U;
    s->mirror_status = s->memory_pause = s->active_count = 0;
    s->standalone_mirror = s->apply_mirror = false;
}
static bool exynos_auto9_pscdc_sources_ready(ExynosAuto9Cmu *s)
{
    if (!s->apply_mirror && !s->standalone_mirror) { return true; }
    unsigned select = (s->active_mirror[0] >> 20) & 3;
    uint32_t mif_select = s->words[s->top_index][0x109c / 4] & 7;
    for (unsigned i = 0; i < s->active_count; i++) {
        if ((s->active_queue[i] & 0xff00ffffU) == 0x1200109c) {
            mif_select = (s->active_queue[i] >> 16) & 7;
        }
    }
    uint32_t *top = s->words[s->top_index];
    uint64_t switch_parents[] = {
        s->plls[0].raw_hz, s->plls[1].raw_hz, s->plls[2].raw_hz, s->plls[4].raw_hz,
        s->plls[0].selected_hz / ((top[0x18b8 / 4] & 1) + 1),
        s->plls[1].selected_hz / ((top[0x18c0 / 4] & 1) + 1),
        s->plls[2].selected_hz / ((top[0x18cc / 4] & 1) + 1), s->plls[3].raw_hz };
    for (unsigned i = 0; i < 4; i++) {
        uint64_t parents[] = { s->reference_hz, switch_parents[mif_select],
                              s->plls[5 + 2 * i].selected_hz, s->plls[6 + 2 * i].selected_hz };
        if (!parents[select]) { return false; }
    }
    return true;
}
static void exynos_auto9_pscdc_replica_commit(ExynosAuto9Cmu *s)
{
    uint32_t selection = (s->active_mirror[0] >> 16) & 0x30;
    uint32_t automatic = (s->active_mirror[1] >> 16) & 1;
    for (unsigned i = 0; i < 4; i++) {
        uint32_t *regs = s->words[exynos_auto9_cmu_mif_port(s, i)];
        regs[0x600 / 4] = (regs[0x600 / 4] & ~0x10030U) | selection;
        regs[0xa08 / 4] = (regs[0xa08 / 4] & ~1U) | automatic;
    }
    s->mirror_status = 0;
    s->mirror_control &= ~1U;
    s->mirror_completions++;
    exynos_auto9_cmu_update_topology(s);
}
static void exynos_auto9_pscdc_complete(void *opaque)
{
    ExynosAuto9Cmu *s = opaque;
    if (!exynos_auto9_pscdc_sources_ready(s)) {
        s->queue_waits++;
        timer_mod(s->pscdc_deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + exynos_auto9_pscdc_delay(s));
        return; /* A missing clock cannot be turned into a completed request. */
    }
    if (s->standalone_mirror) {
        exynos_auto9_pscdc_replica_commit(s);
    } else {
        for (unsigned i = 0; i < s->active_count; i++) {
            uint32_t word = s->active_queue[i], mask = exynos_auto9_pscdc_top_mask(word);
            if (mask) {
                uint32_t *reg = &s->words[s->top_index][(word & 0xffff) / 4];
                *reg = (*reg & ~(mask | 0x10000U)) | ((word >> 16) & mask);
                exynos_auto9_cmu_update_topology(s);
            } else if (word == 0x80040000) {
                exynos_auto9_pscdc_replica_commit(s);
            } else if (word == 0x10000 || word == 0x80020000) {
                s->memory_pause = 1; s->phase_barriers++;
            } else if (word == 0x80030000) {
                s->memory_pause = 0;
            }
        }
        s->queue_completions++;
        s->queue_control &= ~0x101U;
    }
    exynos_auto9_pscdc_busy_fields(s, false);
    s->active_count = 0; s->apply_mirror = s->standalone_mirror = false;
}
static MemTxResult exynos_auto9_pscdc_behavior_write(ExynosAuto9Cmu *s,
                                                   hwaddr offset, uint32_t value)
{
    bool busy = (s->queue_control & 0x100) || s->mirror_status;
    if (offset == 0x3c00) {
        s->last_queue_request = value;
        if (!value) {
            exynos_auto9_pscdc_cancel(s); s->queue_control = 0; s->queue_valid = 0;
            return MEMTX_OK;
        }
        if (busy || (value != 0x211 && value != 0x241 && value != 0x271)) { goto reject_queue; }
        unsigned count = ((value >> 4) & 15) + 1;
        if ((s->queue_valid & ((1U << count) - 1)) != (1U << count) - 1) { goto reject_queue; }
        bool paused = false, replica = false;
        for (unsigned i = 0; i < count; i++) {
            uint32_t word = s->queue_entries[i], mask = exynos_auto9_pscdc_top_mask(word);
            if (!word) { continue; }
            if (word == 0x10000 || word == 0x80020000) { paused = true; }
            else if (word == 0x80030000 && paused) { paused = false; }
            else if (word == 0x80040000 && paused && !replica &&
                     (s->mirror_control & 0x10) && exynos_auto9_pscdc_replica_valid(s)) { replica = true; }
            else if (!(mask && paused && (((word >> 16) & 255) & ~mask) == 0)) { goto reject_queue; }
        }
        if (paused || s->queue_entries[count - 1] != 0x80030000) { goto reject_queue; }
        memcpy(s->active_queue, s->queue_entries, count * sizeof(uint32_t));
        memcpy(s->active_mirror, s->mirror_entries, sizeof(s->active_mirror));
        s->active_count = count; s->apply_mirror = replica; s->standalone_mirror = false;
        /* Bit0 is the observed request; bit8 is device-owned busy, NOT set
         * in the original 0x271 write. Keep it asserted until real work ends. */
        s->queue_control = value | 0x100; s->memory_pause = 1; s->mirror_status = replica;
    } else if (offset == 0x3e00) {
        s->last_mirror_request = value;
        if (!value) { exynos_auto9_pscdc_cancel(s); s->mirror_control = 0; return MEMTX_OK; }
        if (busy || (value != 0x10 && value != 0x11)) { goto reject_mirror; }
        if (value == 0x10) { s->mirror_control = value; return MEMTX_OK; }
        if (!exynos_auto9_pscdc_replica_valid(s)) { goto reject_mirror; }
        memcpy(s->active_mirror, s->mirror_entries, sizeof(s->active_mirror));
        s->active_count = 0; s->standalone_mirror = true; s->apply_mirror = false;
        s->mirror_control = value; s->mirror_status = 1;
    } else if (!busy && offset >= 0x3c10 && offset <= 0x3c2c && offset % 4 == 0) {
        unsigned slot = (offset - 0x3c10) / 4;
        s->queue_entries[slot] = value; s->queue_valid |= 1U << slot; return MEMTX_OK;
    } else if (!busy && offset >= 0x3e10 && offset <= 0x3e2c && offset % 4 == 0) {
        unsigned slot = (offset - 0x3e10) / 4;
        s->mirror_entries[slot] = value; s->mirror_valid |= 1U << slot; return MEMTX_OK;
    } else { return MEMTX_DECODE_ERROR; }
    exynos_auto9_pscdc_busy_fields(s, true);
    timer_mod(s->pscdc_deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + exynos_auto9_pscdc_delay(s));
    return MEMTX_OK;
reject_queue:
    s->queue_rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 virtual PSCDC rejected queue control=0x%x\n", value);
    return MEMTX_DECODE_ERROR;
reject_mirror:
    s->mirror_rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 virtual PSCDC rejected replica control=0x%x\n", value);
    return MEMTX_DECODE_ERROR;
}

/* Default strict mode retains register staging only. The original BL2 uses mirror control 0x11 then
 * waits; ACPM uses 0x10 to arm without that explicit bit0 trigger. No queue
 * execution is accepted in strict mode. The explicit behavior profile above
 * implements its own documented virtual contract, not recovered silicon ISA.
 * An idle mirror read is derived from zero accepted work, NOT an ACK for
 * an unsupported start. Unknown execution always returns a real bus error.
 */
static MemTxResult exynos_auto9_cmu_queue_read(ExynosAuto9Cmu *s,
                                              hwaddr offset, uint64_t *value)
{
    if (offset == 0x3c00) { *value = s->queue_control; }
    else if (offset == 0x3e00) { *value = s->mirror_control; }
    else if (offset == 0x3e30) { *value = s->pscdc_profile ? s->mirror_status : 0; }
    else if (offset >= 0x3c10 && offset <= 0x3c2c && offset % 4 == 0) {
        *value = s->queue_entries[(offset - 0x3c10) / 4];
    } else if (offset >= 0x3e10 && offset <= 0x3e2c && offset % 4 == 0) {
        *value = s->mirror_entries[(offset - 0x3e10) / 4];
    } else { return MEMTX_DECODE_ERROR; }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_cmu_queue_write(ExynosAuto9Cmu *s,
                                               hwaddr offset, uint32_t value)
{
    if (s->pscdc_profile) { return exynos_auto9_pscdc_behavior_write(s, offset, value); }
    if (offset == 0x3c00) {
        s->last_queue_request = value;
        if (value) {
            s->queue_rejected++;
            qemu_log_mask(LOG_UNIMP, "Auto9 CMU queue execution unsupported control=0x%x\n", value);
            return MEMTX_DECODE_ERROR;
        }
        s->queue_control = 0;
    } else if (offset == 0x3e00) {
        s->last_mirror_request = value;
        if (value != 0 && value != 0x10) {
            s->mirror_rejected++;
            qemu_log_mask(LOG_UNIMP, "Auto9 CMU mirror execution unsupported control=0x%x\n", value);
            return MEMTX_DECODE_ERROR;
        }
        s->mirror_control = value;
    } else if (offset >= 0x3c10 && offset <= 0x3c2c && offset % 4 == 0) {
        unsigned slot = (offset - 0x3c10) / 4;
        s->queue_entries[slot] = value; s->queue_valid |= 1U << slot;
    } else if (offset >= 0x3e10 && offset <= 0x3e2c && offset % 4 == 0) {
        unsigned slot = (offset - 0x3e10) / 4;
        s->mirror_entries[slot] = value; s->mirror_valid |= 1U << slot;
    } else { return MEMTX_DECODE_ERROR; }
    return MEMTX_OK;
}

static void exynos_auto9_cmu_update_topology(ExynosAuto9Cmu *s)
{
    /* Samsung's upstream clk-exynosautov9.c parent ordering and DIV widths.
     * These are explicitly UNGATED signals, not fabricated gate-ready status.
     * Named shared dividers are programmable registers, NOT fixed /2 or /3.
     * Direct fout parents deliberately differ from the bypass-selected mout.
     * Fixed eight-parent calculation is O(1) per relevant write/PLL event.
     */
    uint32_t *regs = s->words[s->top_index];
    uint64_t div02 = s->plls[0].selected_hz / ((regs[0x18b8 / 4] & 1) + 1);
    uint64_t div03 = s->plls[0].selected_hz / ((regs[0x18bc / 4] & 3) + 1);
    uint64_t div12 = s->plls[1].selected_hz / ((regs[0x18c0 / 4] & 1) + 1);
    uint64_t div13 = s->plls[1].selected_hz / ((regs[0x18c4 / 4] & 3) + 1);
    uint64_t div22 = s->plls[2].selected_hz / ((regs[0x18cc / 4] & 1) + 1);
    uint64_t div23 = s->plls[2].selected_hz / ((regs[0x18d0 / 4] & 3) + 1);
    uint64_t div42 = s->plls[4].selected_hz / ((regs[0x18d8 / 4] & 1) + 1);
    const uint64_t core_parents[] = { div02, div12, div22, div03,
                                     div42, div13, div23, s->plls[3].raw_hz };
    const uint64_t mif_parents[] = { s->plls[0].raw_hz, s->plls[1].raw_hz,
        s->plls[2].raw_hz, s->plls[4].raw_hz, div02, div12, div22, s->plls[3].raw_hz };
    s->core_bus_mux_hz = core_parents[regs[0x1020 / 4] & 7];
    s->core_bus_div_hz = s->core_bus_mux_hz / ((regs[0x181c / 4] & 15) + 1);
    s->mif_switch_mux_hz = mif_parents[regs[0x109c / 4] & 7];
    clock_update(s->core_bus_mux, CLOCK_PERIOD_FROM_HZ(s->core_bus_mux_hz));
    clock_update(s->core_bus_div, CLOCK_PERIOD_FROM_HZ(s->core_bus_div_hz));
    clock_update(s->mif_switch_mux, CLOCK_PERIOD_FROM_HZ(s->mif_switch_mux_hz));
    s->phy_auto_gate_mask = 0;
    for (unsigned i = 0; i < 4; i++) {
        uint32_t selector = (s->words[exynos_auto9_cmu_mif_port(s, i)][0x600 / 4] >> 4) & 3;
        uint64_t parents[] = { s->reference_hz, s->mif_switch_mux_hz,
                              s->plls[5 + 2 * i].selected_hz, s->plls[6 + 2 * i].selected_hz };
        s->mif_phy_hz[i] = parents[selector];
        s->phy_auto_gate_mask |= (s->words[exynos_auto9_cmu_mif_port(s, i)][0xa08 / 4] & 1) << i;
        clock_update(s->mif_phy[i], CLOCK_PERIOD_FROM_HZ(s->mif_phy_hz[i]));
    }
}
static void exynos_auto9_cmu_update_outputs(ExynosAuto9Pll *p)
{
    ExynosAuto9Cmu *s = p->fabric;
    uint32_t mux = s->words[p->port][(0x100 + 0x40 * p->slot) / 4];
    p->selected_hz = mux & 0x10 ? p->raw_hz : s->reference_hz;
    clock_update(p->raw, CLOCK_PERIOD_FROM_HZ(p->raw_hz));
    clock_update(p->selected, CLOCK_PERIOD_FROM_HZ(p->selected_hz));
    exynos_auto9_cmu_update_topology(s);
}
static uint64_t exynos_auto9_cmu_programmed_rate(ExynosAuto9Pll *p, uint32_t config)
{
    ExynosAuto9Cmu *s = p->fabric;
    uint32_t m = (config >> 16) & 0x3ff, divisor = (config >> 8) & 0x3f;
    unsigned shift = config & 7;
    int64_t scaled = (int64_t)m << 16;
    if (p->index >= 13 && exynos_auto9_aux_plls[p->index - 13].fractional) {
        /* Original CAL declares signed 16-bit K at CON5. Samsung's public
         * PLL0831x formula uses M + (s16)K / 65536, not unsigned K. */
        scaled += (int16_t)s->words[p->port][(0x114 + 0x40 * p->slot) / 4];
    }
    if (!divisor || scaled <= 0) { return 0; }
    return s->reference_hz * (uint64_t)scaled / (((uint64_t)divisor << shift) << 16);
}
static void exynos_auto9_cmu_lock(void *opaque)
{
    ExynosAuto9Pll *p = opaque;
    ExynosAuto9Cmu *s = p->fabric;
    unsigned word = (0x10c + 0x40 * p->slot) / 4;
    if (!(s->words[p->port][word] & AUTO9_PLL_ENABLE)) { return; }
    s->locked_mask |= 1U << p->index;
    s->lock_completions++;
    p->raw_hz = p->queued_hz;
    exynos_auto9_cmu_update_outputs(p);
}
static bool exynos_auto9_cmu_config(hwaddr offset, bool top, hwaddr base)
{
    if (exynos_auto9_cal_cmu_declared(base + offset)) { return true; }
    /* Original dptx_save row129208 restores CONTROLLER_OPTION bits28/29.
     * Configuration retention only, NOT physical automatic clock gating. */
    if (base == 0x18a00000 && offset == 0x800) { return true; }
    /* Sorted original CAL mux/div config declarations, O(log N). This is
     * not a broad RAM window, a reset snapshot, or status/lock emulation. */
    size_t lo = 0, hi = G_N_ELEMENTS(exynos_auto9_cmu_declared_config);
    uint64_t address = base + offset;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (exynos_auto9_cmu_declared_config[mid] < address) { lo = mid + 1; }
        else { hi = mid; }
    }
    if (offset < 0x8000 && lo < G_N_ELEMENTS(exynos_auto9_cmu_declared_config) &&
        exynos_auto9_cmu_declared_config[lo] == address) { return true; }
    /* These are selection/divisor words, NOT status/lock/error registers.
     * Unknown offsets still fault. Only configuration retention is claimed
     * for domain clocks not wired to the selected TOP PLL outputs yet.
     */
    /* Original sub_20DE0 auto-gating config, not a fabricated ready bit. */
    if (offset == 0x800 && (base == 0x1bf00000 || base == 0x1b200000 ||
        base == 0x1b030000 || base == 0x10400000 || base == 0x1b240000 ||
        base == 0x10020000 || base == 0x1b400000 || base == 0x1b600000 ||
        base == 0x1c400000 || base == 0x1c600000 || base == 0x10200000 ||
        base == 0x10800000 || base == 0x17700000 || base == 0x17040000 ||
        base == 0x17c00000 || base == 0x1d020000 || base == 0x1d120000 ||
        base == 0x19200000 || base == 0x19600000 || base == 0x19400000)) { return true; } /* sub_1F178/1F03C/1F0E8. */
    if (top) {
        switch (offset) {
        case 0x1000: case 0x1004: case 0x1008: case 0x100c:
        case 0x1010: case 0x1018: case 0x101c: case 0x1020: case 0x1024:
        case 0x102c: case 0x1030: case 0x1034: case 0x1038: case 0x103c:
        case 0x1040: case 0x1044: case 0x1048: case 0x104c: case 0x1050:
        case 0x1054: case 0x1058: case 0x105c: case 0x1060: case 0x1064:
        case 0x1068: case 0x106c: case 0x1070: case 0x1074: case 0x1078:
        case 0x107c: case 0x1080: case 0x1084: case 0x1088: case 0x108c: case 0x1090:
        case 0x1094: case 0x1098: case 0x109c: case 0x10a0: case 0x10a4:
        case 0x10a8: case 0x10ac: case 0x10b0: case 0x10b4: case 0x10c0:
            return true;
        default: break;
        }
        /* Legacy observed interval, supplemented by original CAL above.
         * Missing upstream names are not proof of physical reserved holes.
         */
        if (offset >= 0x1800 && offset <= 0x18dc && offset % 4 == 0 &&
            offset != 0x1814 && offset != 0x1824 && offset != 0x18ac && offset != 0x18b0) { return true; }
    } else {
        /* Domain-specific original sub_20F88/2074E/208C2/208F8/20C8A accesses.
         * Do NOT pretend all 39 heterogeneous CMUs share every register.
         */
        switch (base) {
        case 0x1b400000: case 0x1b600000: case 0x1c400000: case 0x1c600000:
            /* sub_204B4's four-bank mux selection and gating configuration. */
            return offset == 0x610 || offset == 0x850;
        case 0x10400000: return offset == 0x600 || offset == 0x1000;
        case 0x1bf00000: return offset == 0x600 || offset == 0x610 || offset == 0x1800;
        case 0x1b200000: return offset == 0x600 || offset == 0x1800;
        case 0x1b030000: return offset == 0x600 || offset == 0x1800;
        case 0x1d020000: case 0x1d120000:
            return offset == 0x600 || offset == 0x610 || offset == 0x620 ||
                   offset == 0x804 || offset == 0x820 || offset == 0x828 || offset == 0x854;
        case 0x19c00000:
            /* Original sub_1F218 configuration, not completion status. */
            return offset == 0x600 || offset == 0x610 || offset == 0x850 || offset == 0xa00;
        case 0x19600000: return offset == 0x804; /* Original sub_1F03C. */
        /* Remaining original startup functions 20D58/20974/209CA/20A44/
         * 20AD4/20BB0/20D94: exact finite configuration ranges, no status ACK. */
        case 0x17700000:
            return offset == 0x600 || offset == 0x610 || offset == 0x1000 || offset == 0x1004;
        case 0x17040000:
            return offset == 0x600 || offset == 0x610 || offset == 0x620 || offset == 0x1000 || offset == 0x1800;
        case 0x17c00000:
            return offset == 0x600 || offset == 0x610 || offset == 0x620 ||
                   offset == 0x1000 || offset == 0x1004 || offset == 0x1008;
        case 0x10200000: case 0x10800000:
            return offset == 0x600 || offset == 0x610 ||
                   (offset >= 0x1000 && offset <= 0x1018 && offset % 4 == 0) ||
                   (offset >= 0x1800 && offset <= 0x1818 && offset % 4 == 0);
        case 0x10020000: return offset == 0x600;
        case 0x1d700000: return offset == 0x600 || offset == 0x610 || offset == 0x1804;
        case 0x1dc00000: case 0x1dd00000: case 0x1de00000: case 0x1df00000:
            return offset == 0x600 || offset == 0x1804;
        default: return false;
        }
    }
    return false;
}
/* Return finite source-bound PLL index and kind: lock counter=0, bypass=1,
 * CON3=2, CON1=3, signed fractional CON5=4, retained CON2 configuration=5.
 */
static int exynos_auto9_cmu_pll_register(hwaddr base, hwaddr offset, unsigned *kind)
{
    unsigned first, count;
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_aux_plls); i++) {
        const ExynosAuto9AuxPllDef *p = &exynos_auto9_aux_plls[i];
        if (base != p->base) { continue; }
        if (offset == p->slot * 4) { *kind = 0; return 13 + i; }
        if (offset == 0x100 + p->slot * 0x40) { *kind = 1; return 13 + i; }
        if (offset == 0x10c + p->slot * 0x40) { *kind = 2; return 13 + i; }
        if (offset == 0x104 + p->slot * 0x40) { *kind = 3; return 13 + i; }
        if (offset == 0x108 + p->slot * 0x40) { *kind = 5; return 13 + i; }
        if (p->fractional && offset == 0x114 + p->slot * 0x40) { *kind = 4; return 13 + i; }
    }
    if (base == 0x1b240000) { first = 0; count = 5; }
    else {
        const hwaddr mif[] = { 0x1b400000, 0x1b600000, 0x1c400000, 0x1c600000 };
        unsigned bank;
        for (bank = 0; bank < 4 && base != mif[bank]; bank++) { }
        if (bank == 4) { return -1; }
        first = 5 + 2 * bank; count = 2;
    }
    for (unsigned i = 0; i < count; i++) {
        if (offset == i * 4) { *kind = 0; return first + i; }
        if (offset == 0x100 + i * 0x40) { *kind = 1; return first + i; }
        if (offset == 0x10c + i * 0x40) { *kind = 2; return first + i; }
        if (offset == 0x104 + i * 0x40) { *kind = 3; return first + i; }
        if (offset == 0x108 + i * 0x40) { *kind = 5; return first + i; }
    }
    return -1;
}
/* Same-SoC upstream clk-exynosautov9.c (fsys1_gate_clks): the gate
 * control is bit 21 in these discrete words. It is not a PLL lock/ACK.
 * No USB/MMC hardware clock consumer is wired to these bits yet. */
#define AUTO9_FSYS1_GATE_ENABLE (1U << 21)

static int exynos_auto9_fsys1_gate_index(hwaddr base, hwaddr offset)
{
    const uint16_t offsets[] = {
        0x2018, 0x2028, 0x202c, 0x204c, 0x2058, 0x2064,
        0x2070, 0x2074, 0x2078, 0x207c, 0x2080,
    };
    if (base != 0x17040000) {
        return -1;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(offsets); i++) {
        if (offsets[i] == offset) {
            return i;
        }
    }
    return -1;
}

static MemTxResult exynos_auto9_cmu_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9CmuPort *port = opaque;
    ExynosAuto9Cmu *s = port->fabric;
    bool top = port->index == s->top_index;
    int gate = exynos_auto9_fsys1_gate_index(s->bases[port->index], offset);
    if (gate >= 0) {
        s->fsys1_gate_reads++;
        s->fsys1_gate_read_mask |= 1U << gate;
        *value = s->words[port->index][offset / 4];
        return MEMTX_OK;
    }
    if (exynos_auto9_pmu_debug_cmu(s->bases[port->index], offset, value)) { return MEMTX_OK; }
    if (top && offset >= 0x3c00 && offset < 0x3f00) {
        return exynos_auto9_cmu_queue_read(s, offset, value);
    }
    unsigned kind;
    int pll = exynos_auto9_cmu_pll_register(s->bases[port->index], offset, &kind);
    bool virtual_mif = s->pscdc_profile && (offset == 0x600 || offset == 0xa08) &&
        (s->bases[port->index] == 0x1b400000 || s->bases[port->index] == 0x1b600000 ||
         s->bases[port->index] == 0x1c400000 || s->bases[port->index] == 0x1c600000);
    if (pll >= 0 || virtual_mif || exynos_auto9_cmu_config(offset, top, s->bases[port->index])) {
        *value = s->words[port->index][offset / 4];
        if (pll >= 0 && kind == 2) {
            *value = (*value & ~AUTO9_PLL_LOCKED) |
                     ((s->locked_mask & (1U << pll)) ? AUTO9_PLL_LOCKED : 0);
        }
        return MEMTX_OK;
    }
    qemu_log_mask(LOG_UNIMP, "Auto9 CMU unsupported read base=0x%llx offset=0x%llx\n", (unsigned long long)s->bases[port->index], (unsigned long long)offset);
    return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_cmu_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9CmuPort *port = opaque;
    ExynosAuto9Cmu *s = port->fabric;
    bool top = port->index == s->top_index;
    int gate = exynos_auto9_fsys1_gate_index(s->bases[port->index], offset);
    if (gate >= 0) {
        uint32_t bit = 1U << gate;
        if (value & ~((uint64_t)AUTO9_FSYS1_GATE_ENABLE)) {
            qemu_log_mask(LOG_UNIMP, "Auto9 FSYS1 gate unknown fields at 0x%"
                          HWADDR_PRIx ": 0x%" PRIx64 "\n", offset, value);
            return MEMTX_DECODE_ERROR;
        }
        s->fsys1_gate_writes++;
        s->fsys1_gate_write_mask |= bit;
        s->fsys1_gate_enabled = (s->fsys1_gate_enabled & ~bit) | (value ? bit : 0);
        s->words[port->index][offset / 4] = value;
        s->writes++;
        return MEMTX_OK;
    }
    if (s->bases[port->index] == 0x18a00000 && offset == 0x800 &&
        (value & ~0x30000000ULL)) {
        qemu_log_mask(LOG_UNIMP, "Auto9 DPTX CMU option contains unmodelled fields\n");
        return MEMTX_DECODE_ERROR;
    }
    if (top && offset >= 0x3c00 && offset < 0x3f00) {
        MemTxResult result = exynos_auto9_cmu_queue_write(s, offset, value);
        if (result == MEMTX_OK) { s->writes++; }
        return result;
    }
    unsigned kind;
    int index = exynos_auto9_cmu_pll_register(s->bases[port->index], offset, &kind);
    uint32_t *word;
    bool virtual_mif = s->pscdc_profile && (offset == 0x600 || offset == 0xa08) &&
        (s->bases[port->index] == 0x1b400000 || s->bases[port->index] == 0x1b600000 ||
         s->bases[port->index] == 0x1c400000 || s->bases[port->index] == 0x1c600000);
    if (index < 0 && !virtual_mif && !exynos_auto9_cmu_config(offset, top, s->bases[port->index])) {
        qemu_log_mask(LOG_UNIMP, "Auto9 CMU unsupported write base=0x%llx offset=0x%llx\n", (unsigned long long)s->bases[port->index], (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    word = &s->words[port->index][offset / 4];
    if (index >= 0 && kind == 4) {
        ExynosAuto9Pll *p = &s->plls[index];
        uint32_t config = s->words[p->port][(0x10c + 0x40 * p->slot) / 4];
        uint32_t cycles = s->words[p->port][p->slot];
        if (value > 0xffff || ((config & AUTO9_PLL_ENABLE) && (!cycles || cycles > 0xffffff))) {
            return MEMTX_DECODE_ERROR;
        }
        bool changed = *word != value;
        *word = value;
        if (changed && (config & AUTO9_PLL_ENABLE)) {
            timer_del(p->deadline); s->locked_mask &= ~(1U << index); p->raw_hz = 0;
            p->queued_hz = exynos_auto9_cmu_programmed_rate(p, config);
            exynos_auto9_cmu_update_outputs(p);
            timer_mod(p->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                ((uint64_t)cycles * NANOSECONDS_PER_SECOND + s->reference_hz - 1) / s->reference_hz);
        }
        s->writes++; return MEMTX_OK;
    }
    if (index >= 0 && kind == 2) {
        ExynosAuto9Pll *p = &s->plls[index];
        uint32_t config = value & ~AUTO9_PLL_LOCKED;
        uint32_t m = (config >> 16) & 0x3ff, divisor = (config >> 8) & 0x3f;
        uint32_t cycles = s->words[p->port][p->slot];
        if ((config & AUTO9_PLL_ENABLE) && (!m || !divisor || !cycles ||
            cycles > 0xffffff || s->reference_hz < 1000000 || s->reference_hz > 1000000000)) {
            qemu_log_mask(LOG_UNIMP, "Auto9 CMU invalid/unsupported PLL request\n");
            return MEMTX_DECODE_ERROR;
        }
        /* A guest write of the lock bit cannot complete queued work. */
        if (*word != config || !(config & AUTO9_PLL_ENABLE)) {
            timer_del(p->deadline);
            s->locked_mask &= ~(1U << index);
            p->raw_hz = 0;
            *word = config;
            exynos_auto9_cmu_update_outputs(p);
            if (config & AUTO9_PLL_ENABLE) {
                uint64_t delay = ((uint64_t)cycles * NANOSECONDS_PER_SECOND + s->reference_hz - 1) / s->reference_hz;
                p->queued_hz = exynos_auto9_cmu_programmed_rate(p, config);
                timer_mod(p->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + delay);
            }
        }
    } else {
        if (s->pscdc_profile && (virtual_mif || (top &&
            (offset == 0x1020 || offset == 0x109c || offset == 0x181c)))) {
            if ((s->queue_control & 0x100) || s->mirror_status) { return MEMTX_DECODE_ERROR; }
            if (offset != 0xa08) { value &= ~0x10000U; }
        }
        *word = value;
        if (index >= 0 && kind == 1) { exynos_auto9_cmu_update_outputs(&s->plls[index]); }
    }
    s->writes++;
    bool mif_config = offset == 0x600 &&
        (s->bases[port->index] == 0x1b400000 || s->bases[port->index] == 0x1b600000 ||
         s->bases[port->index] == 0x1c400000 || s->bases[port->index] == 0x1c600000);
    if (top || virtual_mif || mif_config) { exynos_auto9_cmu_update_topology(s); }
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_cmu_ops = {
    .read_with_attrs = exynos_auto9_cmu_read, .write_with_attrs = exynos_auto9_cmu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_cmu_reset(DeviceState *dev)
{
    ExynosAuto9Cmu *s = EXYNOS_AUTO9_CMU(dev);
    memset(s->words, 0, sizeof(s->words));
    s->locked_mask = s->lock_completions = s->writes = 0;
    s->fsys1_gate_reads = s->fsys1_gate_writes = s->fsys1_gate_enabled = 0;
    s->fsys1_gate_read_mask = s->fsys1_gate_write_mask = 0;
    memset(s->queue_entries, 0, sizeof(s->queue_entries));
    memset(s->mirror_entries, 0, sizeof(s->mirror_entries));
    s->queue_control = s->mirror_control = s->queue_valid = s->mirror_valid = 0;
    s->queue_rejected = s->mirror_rejected = s->last_queue_request = s->last_mirror_request = 0;
    timer_del(s->pscdc_deadline);
    s->active_count = s->mirror_status = s->memory_pause = s->phase_barriers = 0;
    s->queue_completions = s->mirror_completions = s->queue_cancellations = s->queue_waits = 0;
    s->phy_auto_gate_mask = 0; s->standalone_mirror = s->apply_mirror = false;
    memset(s->active_queue, 0, sizeof(s->active_queue));
    memset(s->active_mirror, 0, sizeof(s->active_mirror));
    for (unsigned i = 0; i < AUTO9_PLL_COUNT; i++) {
        timer_del(s->plls[i].deadline);
        s->plls[i].raw_hz = 0;
        exynos_auto9_cmu_update_outputs(&s->plls[i]);
    }
}
static void exynos_auto9_cmu_clock_period_get(Object *obj, Visitor *visitor,
                                              const char *name, void *opaque,
                                              Error **errp)
{
    uint64_t period = clock_get(opaque);
    visit_type_uint64(visitor, name, &period, errp);
}
static void exynos_auto9_cmu_init(Object *obj)
{
    ExynosAuto9Cmu *s = EXYNOS_AUTO9_CMU(obj);
    s->reference_hz = 26000000;
    s->pll_count = AUTO9_PLL_COUNT;
    object_property_add_uint32_ptr(obj, "pll-count", &s->pll_count, OBJ_PROP_FLAG_READ);
    s->pscdc_cycles = 64;
    s->pscdc_deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_pscdc_complete, s);
    object_property_add_uint64_ptr(obj, "pscdc-profile", &s->pscdc_profile, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "pscdc-latency-cycles", &s->pscdc_cycles, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "queue-completions", &s->queue_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mirror-completions", &s->mirror_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "queue-cancellations", &s->queue_cancellations, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "queue-clock-waits", &s->queue_waits, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "memory-clock-pause", &s->memory_pause, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phase-barriers", &s->phase_barriers, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-auto-gate-mask", &s->phy_auto_gate_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "reference-hz", &s->reference_hz, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "locked-mask", &s->locked_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "lock-completions", &s->lock_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys1-gate-reads", &s->fsys1_gate_reads,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys1-gate-writes", &s->fsys1_gate_writes,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys1-gate-enabled-mask", &s->fsys1_gate_enabled,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys1-gate-read-mask", &s->fsys1_gate_read_mask,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys1-gate-write-mask", &s->fsys1_gate_write_mask,
                                   OBJ_PROP_FLAG_READ);
    s->core_bus_mux = qdev_init_clock_out(DEVICE(obj), "core-bus-mux");
    s->core_bus_div = qdev_init_clock_out(DEVICE(obj), "core-bus-div-ungated");
    s->mif_switch_mux = qdev_init_clock_out(DEVICE(obj), "mif-switch-mux");
    for (unsigned i = 0; i < 4; i++) {
        g_autofree char *name = g_strdup_printf("mif-ddrphy2x%u-ungated", i);
        g_autofree char *hz = g_strdup_printf("%s-hz", name);
        g_autofree char *period = g_strdup_printf("%s-period", name);
        s->mif_phy[i] = qdev_init_clock_out(DEVICE(obj), name);
        object_property_add_uint64_ptr(obj, hz, &s->mif_phy_hz[i], OBJ_PROP_FLAG_READ);
        object_property_add(obj, period, "uint64", exynos_auto9_cmu_clock_period_get,
                            NULL, NULL, s->mif_phy[i]);
    }
    object_property_add(obj, "core-bus-mux-period", "uint64", exynos_auto9_cmu_clock_period_get,
                        NULL, NULL, s->core_bus_mux);
    object_property_add(obj, "core-bus-div-ungated-period", "uint64", exynos_auto9_cmu_clock_period_get,
                        NULL, NULL, s->core_bus_div);
    object_property_add(obj, "mif-switch-mux-period", "uint64", exynos_auto9_cmu_clock_period_get,
                        NULL, NULL, s->mif_switch_mux);
    object_property_add_uint64_ptr(obj, "core-bus-mux-hz", &s->core_bus_mux_hz, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "core-bus-div-ungated-hz", &s->core_bus_div_hz, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "mif-switch-mux-hz", &s->mif_switch_mux_hz, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "queue-control", &s->queue_control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mirror-control", &s->mirror_control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "queue-valid-mask", &s->queue_valid, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mirror-valid-mask", &s->mirror_valid, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "queue-rejected-starts", &s->queue_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mirror-rejected-starts", &s->mirror_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-queue-request", &s->last_queue_request, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-mirror-request", &s->last_mirror_request, OBJ_PROP_FLAG_READ);
    for (unsigned i = 0; i < 8; i++) {
        g_autofree char *queue_name = g_strdup_printf("queue-entry%u", i);
        g_autofree char *mirror_name = g_strdup_printf("mirror-entry%u", i);
        object_property_add_uint32_ptr(obj, queue_name, &s->queue_entries[i], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, mirror_name, &s->mirror_entries[i], OBJ_PROP_FLAG_READ);
    }
    for (unsigned i = 0; i < AUTO9_CMU_PORTS; i++) {
        s->ports[i].fabric = s; s->ports[i].index = i;
        memory_region_init_io(&s->mmio[i], obj, &exynos_auto9_cmu_ops, &s->ports[i], TYPE_EXYNOS_AUTO9_CMU, 0x10000);
    }
    for (unsigned i = 0; i < AUTO9_PLL_COUNT; i++) {
        ExynosAuto9Pll *p = &s->plls[i];
        char name[48];
        p->fabric = s; p->index = i;
        p->slot = i < 5 ? i : (i < 13 ? (i - 5) % 2 : exynos_auto9_aux_plls[i - 13].slot);
        p->base = i < 5 ? 0x1b240000 : (i < 13 ?
            (const uint64_t[]){ 0x1b400000, 0x1b600000, 0x1c400000, 0x1c600000 }[(i - 5) / 2] :
            exynos_auto9_aux_plls[i - 13].base);
        p->deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_cmu_lock, p);
        snprintf(name, sizeof(name), "pll%u", i); p->raw = qdev_init_clock_out(DEVICE(obj), name);
        snprintf(name, sizeof(name), "shared%u", i); p->selected = qdev_init_clock_out(DEVICE(obj), name);
        snprintf(name, sizeof(name), "pll%u-hz", i); object_property_add_uint64_ptr(obj, name, &p->raw_hz, OBJ_PROP_FLAG_READ);
        snprintf(name, sizeof(name), "shared%u-hz", i); object_property_add_uint64_ptr(obj, name, &p->selected_hz, OBJ_PROP_FLAG_READ);
        snprintf(name, sizeof(name), "pll%u-base", i); object_property_add_uint64_ptr(obj, name, &p->base, OBJ_PROP_FLAG_READ);
        snprintf(name, sizeof(name), "pll%u-slot", i); object_property_add_uint32_ptr(obj, name, &p->slot, OBJ_PROP_FLAG_READ);
    }
}
static void exynos_auto9_cmu_finalize(Object *obj)
{
    ExynosAuto9Cmu *s = EXYNOS_AUTO9_CMU(obj);
    timer_free(s->pscdc_deadline);
    for (unsigned i = 0; i < AUTO9_PLL_COUNT; i++) { timer_free(s->plls[i].deadline); }
}
static void exynos_auto9_cmu_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_cmu_reset);
}
static const TypeInfo exynos_auto9_cmu_type = {
    .name = TYPE_EXYNOS_AUTO9_CMU, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Cmu), .instance_init = exynos_auto9_cmu_init,
    .instance_finalize = exynos_auto9_cmu_finalize, .class_init = exynos_auto9_cmu_class_init,
};
static void exynos_auto9_cmu_register(void) { type_register_static(&exynos_auto9_cmu_type); }
type_init(exynos_auto9_cmu_register)

static void exynos_auto9_cmu_create(VirtMachineState *vms, const hwaddr *bases, unsigned count)
{
    DeviceState *dev;
    ExynosAuto9Cmu *s;
    if (!vms->exynos_acpm) { return; }
    assert(count == AUTO9_CMU_PORTS);
    dev = qdev_new(TYPE_EXYNOS_AUTO9_CMU); s = EXYNOS_AUTO9_CMU(dev);
    if (vms->exynos_pscdc_profile > 1 || vms->exynos_pscdc_cycles > 1000000) {
        error_report("Unsupported virtual PSCDC profile/latency"); exit(1);
    }
    s->pscdc_profile = vms->exynos_pscdc_profile;
    if (vms->exynos_pscdc_cycles) { s->pscdc_cycles = vms->exynos_pscdc_cycles; }
    object_property_add_child(OBJECT(vms), "exynos-cmu", OBJECT(dev));
    s->top_index = AUTO9_CMU_PORTS;
    for (unsigned i = 0; i < count; i++) {
        s->bases[i] = bases[i];
        if (bases[i] == 0x1b240000) { s->top_index = i; }
    }
    assert(s->top_index < AUTO9_CMU_PORTS);
    for (unsigned p = 0; p < AUTO9_PLL_COUNT; p++) {
        unsigned i;
        for (i = 0; i < count && bases[i] != s->plls[p].base; i++) { }
        assert(i < count);
        s->plls[p].port = i;
    }
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    /* SysBusDevice has only 32 MMIO slots; retain all 39 HCB ports as
     * owned MemoryRegions on the system bus instead of overflowing it. */
    for (unsigned i = 0; i < count; i++) { memory_region_add_subregion(get_system_memory(), bases[i], &s->mmio[i]); }
}
