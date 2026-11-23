#include "chardev/char-fe.h"
#include "chardev/char.h"
/* SPDX-License-Identifier: GPL-2.0-or-later
 * Explicit virtual-cold-v1 PMU, bound to original ACPM13188/15B38/1582C.
 * Config/atomic addresses come from those instructions, not a RAM window.
 * Cold input/status values are virtual zero, NOT recovered physical reset.
 * Set/clear bit-index aliases match the Samsung/Tensor public PMU family;
 * the Auto9 connection is inferred from the original writes, not silicon QA.
 * Timers do real virtual-time work. No ready marker, OS branch or image patch.
 * Analog power sequencing, CPU release, and physical IRQ delivery are absent.
 */
#define TYPE_EXYNOS_AUTO9_PMU "exynos-auto9.pmu"
#include "qapi/visitor.h"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Pmu, EXYNOS_AUTO9_PMU)
typedef struct ExynosAuto9PmuTimer {
    ExynosAuto9Pmu *pmu;
    QEMUTimer *deadline;
    unsigned index;
} ExynosAuto9PmuTimer;
typedef struct ExynosAuto9PmuSignal {
    ExynosAuto9Pmu *pmu;
    QEMUTimer *deadline;
    unsigned index;
} ExynosAuto9PmuSignal;
/* Source: devb-ufs +0x147dc, Samsung Auto V9 PHY .isol descriptor. */
#define AUTO9_PMU_USB0_PHY_ISOLATION 0x704
#define AUTO9_PMU_USB0_PHY_ISOLATION_2ND 0x70c
static const unsigned auto9_pmu_usb_host_isolation[] = { 0x708, 0x710, 0x714 };

static int auto9_pmu_usb_host_index(hwaddr off)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_pmu_usb_host_isolation); i++) {
        if (off == auto9_pmu_usb_host_isolation[i]) {
            return i;
        }
    }
    return -1;
}
#define AUTO9_PMU_UFS0_PHY_ISOLATION 0x728
/* Original /pcie@17A80000 pmu-phy-offset and regmap mask1. */
#define AUTO9_PMU_PCIE23_PHY_CONTROL 0x71c

struct ExynosAuto9Pmu {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    ExynosAuto9Domains *domains;
    uint32_t words[0x4000 / 4], group_enable[31], group_pending[31];
    ExynosAuto9PmuTimer timers[3];
    uint32_t writes, atomic_writes, rejected, timer_completions, timer_status;
    uint32_t last_offset, last_value;
    uint32_t display_phy_reads, display_phy_writes, ufs_flag_reads, ufs_flag_writes;
    /* Optional board strap input. Its Auto9 electrical reset value is unknown. */
    uint32_t om_input_active, om_input_value, om_input_reads, om_input_cpu_reads;
    /* Read-only first64 access records. Host debugger accesses are explicitly
     * separated from TCG CPU accesses. No handshake value is synthesized. */
    struct { uint64_t pc; uint32_t value; int cpu; bool write; } ufs_access[64];
    bool pcie23_phy_control, ufs0_phy_isolation, usb0_phy_isolation, abox_dsp_off;
    uint32_t usb0_phy_reads, usb0_phy_writes;
    uint32_t usb0_phy2_reads, usb0_phy2_writes;
    uint32_t usb_host_reads[G_N_ELEMENTS(auto9_pmu_usb_host_isolation)];
    uint32_t usb_host_writes[G_N_ELEMENTS(auto9_pmu_usb_host_isolation)];
    CharFrontend abox_peer;
    bool abox_link, abox_started;
    uint32_t abox_running;
    uint32_t abox_config[4], abox_requests, abox_reports, abox_bad_messages;
    MemoryRegion abox_core3_select, abox_timer_view, abox_hifi_early, abox_dram_request_mmio;
    uint32_t abox_dram_request;
    int64_t abox_timer_start_ns;
    uint64_t abox_timer_sample;
    bool abox_timer_sample_valid;
    uint32_t abox_timer_high_reads, abox_timer_low_reads, abox_hifi_early_reads;
    uint32_t abox_dsp_status_reads[4], abox_dsp_status_rejected;
    uint32_t pcie23_phy_reads, pcie23_phy_writes;
    uint32_t ufs0_phy_reads, ufs0_phy_writes;
    uint32_t ufs_access_count, ufs_access_dropped, ufs_cpu_reads, ufs_cpu_writes;
    /* Explicit opaque input bank for three original debug-dump words only.
     * Physical fields UNKNOWN. No ACK/status/rail/clock semantics inferred. */
    uint32_t debug_profile, debug_words[3], debug_reads[3];
    /* Optional message transport, NOT a recovered HCU/power instruction set.
     * Original193DC uses1520[8]/1524[10:11];160AC(9) uses1CA0[13]/1CA4[5:6].
     * A declared virtual peer consumes the byte before asserting ACK. No ADC,
     * rail, CPU-release or physical PMIC effect is inferred from the payload. */
    QEMUTimer *link_deadline;
    uint32_t link_profile, link_peer, link_selector, link_selected;
    uint32_t link_state, link_route, link_status[2], link_active_payload;
    uint32_t link_deliveries, link_releases, link_cancelled, link_last_payload;
    uint32_t link_last_route, link_received[2], link_received_valid;
    uint32_t link_history[32], link_history_count;
    uint64_t link_last_delivery_ns;
    /* Explicit shadow/latch hypothesis for original195E8/19628. These
     * virtual output pins have NO inferred rail, CPU or physical IRQ route. */
    ExynosAuto9PmuSignal signals[2];
    qemu_irq signal_outputs[64];
    uint32_t signal_profile, signal_peer, status1204, core_status_mask;
    uint32_t signal_state[2], signal_pending[2];
    uint32_t signal_latched[2], signal_ack[2], signal_commits, signal_releases;
    uint32_t signal_cancelled, signal_failed, signal_valid;
};
/* Exact four PMU isolation words in original QNX exynos_phy_control36440.
 * Only bit0 bypasses isolation; this does NOT generate PHY lock/link training.
 * 840 is the software LK/UFS handoff word, not a hardware-ready status.
 */
static void exynos_auto9_pmu_ufs_observe(ExynosAuto9Pmu *s, bool write, uint32_t value)
{
    CPUState *cpu = current_cpu;
    if (cpu) { if (write) { s->ufs_cpu_writes++; } else { s->ufs_cpu_reads++; } }
    if (s->ufs_access_count == ARRAY_SIZE(s->ufs_access)) { s->ufs_access_dropped++; return; }
    unsigned i = s->ufs_access_count++;
    s->ufs_access[i].pc = cpu ? CPU_GET_CLASS(cpu)->get_pc(cpu) : 0;
    s->ufs_access[i].cpu = cpu ? cpu->cpu_index : -1;
    s->ufs_access[i].value = value; s->ufs_access[i].write = write;
}
static char *exynos_auto9_pmu_ufs_history(Object *obj, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    GString *text = g_string_new("[");
    for (unsigned i = 0; i < s->ufs_access_count; i++) {
        g_string_append_printf(text,
            "%s{\"cpu\":%d,\"reported_pc\":\"0x%" PRIx64 "\",\"write\":%s,\"value\":%u}",
            i ? "," : "", s->ufs_access[i].cpu, s->ufs_access[i].pc,
            s->ufs_access[i].write ? "true" : "false", s->ufs_access[i].value);
    }
    g_string_append(text, "]"); return g_string_free(text, false);
}
static bool exynos_auto9_pmu_display_phy(hwaddr off)
{
    return off == 0x730 || off == 0x738 || off == 0x73c || off == 0x740;
}
static bool exynos_auto9_pmu_signal_config(hwaddr off)
{
    return off == 0x1550 || off == 0x1560 || off == 0x1564 ||
           off == 0x1568 || off == 0x1504;
}
static void exynos_auto9_pmu_signal_expire(void *opaque)
{
    ExynosAuto9PmuSignal *t = opaque;
    ExynosAuto9Pmu *s = t->pmu;
    unsigned i = t->index;
    if (s->signal_state[i] == 1) {
        if (s->signal_peer == 1) {
            /* Actual latched outputs change only when the commit completes. */
            s->signal_latched[i] = s->signal_pending[i];
            for (unsigned bit = 0; bit < 32; bit++) {
                qemu_set_irq(s->signal_outputs[i * 32 + bit],
                             (s->signal_latched[i] >> bit) & 1);
            }
            s->signal_valid |= 1U << i;
            s->signal_commits++;
            s->signal_ack[i] = 1;
            s->signal_state[i] = 2;
        } else {
            s->signal_failed++;
            s->signal_state[i] = 0; /* No consumer: NO acknowledgement. */
        }
    } else if (s->signal_state[i] == 3) {
        s->signal_ack[i] = 0;
        s->signal_state[i] = 0;
        s->signal_releases++;
    }
}
static bool exynos_auto9_pmu_signal_request(ExynosAuto9Pmu *s, unsigned i,
                                           uint32_t before, uint32_t after)
{
    if (after & ~1U) { return false; }
    if (after == before) { return true; }
    if (after) {
        if (s->signal_state[i]) { return false; }
        s->signal_pending[i] = s->words[(0x1564 + i * 4) / 4];
        s->signal_state[i] = 1;
    } else if (s->signal_state[i] == 1) {
        timer_del(s->signals[i].deadline);
        s->signal_state[i] = 0;
        s->signal_cancelled++;
        return true;
    } else if (s->signal_state[i] == 2) { s->signal_state[i] = 3; }
    else { return true; }
    /* Explicit virtual64/26MHz tick latency, not observed physical timing. */
    timer_mod(s->signals[i].deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 2462);
    return true;
}
/* Only byte pairs actually present at the original callers19D3C/19EEC.
 * Their physical meanings are UNKNOWN. This is an explicit endpoint allowlist,
 * not a claim to decode the command or execute silicon microcode. O(1). */
static bool exynos_auto9_pmu_link_payload(uint32_t payload)
{
    switch (payload) {
    case 0x10: case 0x11: case 0x30: case 0x31: case 0x5c:
    case 0x5d: case 0x5e: case 0x5f: case 0x78: case 0x79: return true;
    default: return false;
    }
}
static void exynos_auto9_pmu_link_expire(void *opaque)
{
    ExynosAuto9Pmu *s = opaque;
    unsigned route = s->link_route, ack = route ? 5 : 10, error = route ? 6 : 11;
    if (s->link_state == 1) {
        if (s->link_peer == 1) {
            /* Commit an actual virtual packet to the selected peer mailbox. */
            s->link_received[route] = s->link_active_payload;
            s->link_received_valid |= 1U << route;
            s->link_last_payload = s->link_active_payload;
            s->link_last_route = route;
            if (s->link_history_count < ARRAY_SIZE(s->link_history)) {
                s->link_history[s->link_history_count++] = (route << 8) | s->link_active_payload;
            }
            s->link_last_delivery_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            s->link_deliveries++;
            s->link_status[route] = 1U << ack;
            s->link_state = 2;
        } else {
            s->link_status[route] = 1U << error;
            s->link_state = 0;
        }
    } else if (s->link_state == 3) {
        s->link_status[route] = 0;
        s->link_state = 0;
        s->link_releases++;
    }
}
static bool exynos_auto9_pmu_link_request(ExynosAuto9Pmu *s, unsigned route,
                                         uint32_t before, uint32_t after)
{
    uint32_t mask = 1U << (route ? 13 : 8);
    if (!((before ^ after) & mask)) { return true; }
    if (after & mask) {
        if (s->link_state || !s->link_selected) { return false; }
        s->link_route = route;
        s->link_active_payload = s->link_selector;
        s->link_selected = 0;
        s->link_status[route] = 0;
        s->link_state = 1;
    } else {
        if (s->link_route != route) { return false; }
        if (s->link_state == 1) {
            timer_del(s->link_deadline);
            s->link_cancelled++;
            s->link_state = 0;
            s->link_status[route] = 0;
            return true;
        }
        if (s->link_state == 2) { s->link_state = 3; }
        else { s->link_status[route] = 0; return true; }
    }
    /* Explicit virtual transport latency:64 ticks at26MHz, not measured silicon.
     * ACK and release only change at deadlines, never as a read side effect. */
    timer_mod(s->link_deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 2462);
    return true;
}
/* Only configuration retained here; unknown status and register holes fault.
 * Arithmetic ranges are the exact bounded loops in original13188/15B38. */
static bool exynos_auto9_pmu_config(hwaddr off)
{
    if ((off >= 0x1808 && off <= 0x2d08 && (off - 0x1808) % 0x80 == 0) ||
        (off >= 0x1044 && off <= 0x1244 && (off - 0x1044) % 0x80 == 0) ||
        (off >= 0x1048 && off <= 0x1248 && (off - 0x1048) % 0x80 == 0) ||
        (off >= 0x1344 && off <= 0x1544 && (off - 0x1344) % 0x80 == 0) ||
        (off >= 0x1348 && off <= 0x1548 && (off - 0x1348) % 0x80 == 0) ||
        (off >= 0x3244 && off <= 0x36c4 && (off - 0x3244) % 0x80 == 0)) { return true; }
    switch (off) {
    case 0x110: /* Original FlexPMU request through10AF8, observed8110 bit1 clear. */
    case 0xb14: case 0xb18: /* Original4704 WFI preparation; retained requests, not guessed clock gating. */
    case 0x90c: case 0x1520: case 0xb00: case 0xb20: case 0xb1c:
    case 0x1208: case 0x1508: case 0x1008: case 0x3a10: case 0x3a48:
    case 0x3948: case 0x3a44: case 0x3b34: case 0x3b38: case 0x3220:
    case 0x3a94: case 0xe00: case 0xe08: case 0xe0c: case 0xe10:
    case 0xe34: case 0xe38: case 0xe3c:
        return true;
    default: return exynos_auto9_cal_pmu_config(off);
    }
}
static void exynos_auto9_pmu_expire(void *opaque)
{
    ExynosAuto9PmuTimer *t = opaque;
    ExynosAuto9Pmu *s = t->pmu;
    if (s->words[(0xe34 + t->index * 4) / 4] & (1U << 31)) {
        s->timer_status |= 1U << (19 + t->index);
        s->timer_completions++;
    }
}
static void exynos_auto9_pmu_timer_update(ExynosAuto9Pmu *s, unsigned i)
{
    ExynosAuto9PmuTimer *t = &s->timers[i];
    uint32_t value = s->words[(0xe34 + 4 * i) / 4];
    uint64_t hz = (s->words[0xb20 / 4] & 0x10) ? 50000000 : 26000000;
    timer_del(t->deadline);
    s->timer_status &= ~(1U << (19 + i));
    if (value & (1U << 31)) {
        uint64_t ns = ((uint64_t)(value & 0x7fffffff) * NANOSECONDS_PER_SECOND + hz - 1) / hz;
        timer_mod(t->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + MAX(ns, 1));
    }
}
static bool exynos_auto9_pmu_abox_dsp_config(hwaddr off)
{
    return off >= 0x3400 && off <= 0x3580 && (off - 0x3400) % 0x80 == 0;
}

/*
 * A local host link carries reset edges and execution liveness, never guest
 * IPC bytes. S/R: original PMU release/reset; O/F: DSP running/stopped.
 * Disconnect clears status. No other core is reported running in this model.
 */
static int exynos_auto9_abox_can_read(void *opaque)
{
    return 16;
}

static void exynos_auto9_abox_receive(void *opaque, const uint8_t *buf, int n)
{
    ExynosAuto9Pmu *s = opaque;
    for (int i = 0; i < n; ++i) {
        if (buf[i] == 'O' && s->abox_started && s->abox_config[0] == 1 &&
            !s->abox_running) {
            s->abox_running = true;
            s->abox_timer_start_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            s->abox_timer_sample_valid = false;
            s->abox_reports++;
            qemu_log_mask(LOG_UNIMP, "Auto9 ABOX live core0 execution reported\n");
        } else if (buf[i] == 'F') {
            s->abox_running = false;
            s->abox_started = false;
        } else {
            s->abox_bad_messages++;
        }
    }
}

static void exynos_auto9_abox_event(void *opaque, QEMUChrEvent event)
{
    ExynosAuto9Pmu *s = opaque;
    if (event == CHR_EVENT_CLOSED) {
        s->abox_running = false;
        s->abox_started = false;
    }
}

static void exynos_auto9_abox_request(ExynosAuto9Pmu *s, uint8_t event)
{
    s->abox_running = false;
    s->abox_started = false;
    if (qemu_chr_fe_backend_open(&s->abox_peer) &&
        qemu_chr_fe_write(&s->abox_peer, &event, 1) == 1) {
        s->abox_started = event == 'S';
        s->abox_requests++;
    }
}

static uint64_t exynos_auto9_abox_core3_select_read(void *opaque, hwaddr off,
                                                   unsigned size)
{
    /* The OEM three-core LK path explicitly clears bit 0. */
    return 0;
}

static MemTxResult exynos_auto9_abox_core3_select_write(void *opaque,
    hwaddr off, uint64_t value, unsigned size, MemTxAttrs attrs)
{
    return value == 0 ? MEMTX_OK : MEMTX_ACCESS_ERROR;
}

static const MemoryRegionOps exynos_auto9_abox_core3_select_ops = {
    .read = exynos_auto9_abox_core3_select_read,
    .write_with_attrs = exynos_auto9_abox_core3_select_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* AP reads two timestamp words after seeing the live PMU state.  Anchor the
 * 26 MHz behavior-level counter to the AP virtual clock at that transition.
 * AP/DSP virtual-clock phase is not synchronized across the two processes. */
static uint64_t exynos_auto9_abox_timer_ticks(ExynosAuto9Pmu *s)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    return s->abox_running && now > s->abox_timer_start_ns
        ? muldiv64(now - s->abox_timer_start_ns, 26000000u, NANOSECONDS_PER_SECOND)
        : 0;
}

static MemTxResult exynos_auto9_abox_timer_read(void *opaque, hwaddr off,
        uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Pmu *s = opaque;
    if (off == 4) {
        s->abox_timer_high_reads++;
        s->abox_timer_sample = exynos_auto9_abox_timer_ticks(s);
        s->abox_timer_sample_valid = true;
        *value = s->abox_timer_sample >> 32;
    } else if (off == 0) {
        s->abox_timer_low_reads++;
        if (!s->abox_running) {
            s->abox_timer_sample_valid = false;
        }
        *value = (uint32_t)(s->abox_timer_sample_valid ? s->abox_timer_sample
                                                     : exynos_auto9_abox_timer_ticks(s));
        s->abox_timer_sample_valid = false;
    } else {
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_abox_timer_view_ops = {
    .read_with_attrs = exynos_auto9_abox_timer_read,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult exynos_auto9_abox_hifi_early_read(void *opaque, hwaddr off,
    uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Pmu *s = opaque;
    if (off != 0 || size != 4) {
        return MEMTX_DECODE_ERROR;
    }
    if (!s->abox_hifi_early_reads++) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX hifi_sfr early-audio read at 0x19c90014: cold state\n");
    }
    *value = 0;
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_abox_hifi_early_write(void *opaque, hwaddr off,
    uint64_t value, unsigned size, MemTxAttrs attrs)
{
    return MEMTX_ACCESS_ERROR;
}

static const MemoryRegionOps exynos_auto9_abox_hifi_early_ops = {
    .read_with_attrs = exynos_auto9_abox_hifi_early_read,
    .write_with_attrs = exynos_auto9_abox_hifi_early_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult exynos_auto9_abox_dram_request_read(void *opaque, hwaddr off,
    uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Pmu *s = opaque;
    *value = s->abox_dram_request;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX DRAM request read 0x%llx\n",
                  (unsigned long long)*value);
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_abox_dram_request_write(void *opaque, hwaddr off,
    uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Pmu *s = opaque;
    if (value > 1) {
        s->rejected++;
        return MEMTX_ACCESS_ERROR;
    }
    s->abox_dram_request = value;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX DRAM request write 0x%llx\n",
                  (unsigned long long)value);
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_abox_dram_request_ops = {
    .read_with_attrs = exynos_auto9_abox_dram_request_read,
    .write_with_attrs = exynos_auto9_abox_dram_request_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

#define AUTO9_PMU_ABOX_CONTROL 0x19a0

static bool exynos_auto9_pmu_abox_dsp_status(hwaddr off)
{
    return off >= 0x3404 && off <= 0x3584 && (off - 0x3404) % 0x80 == 0;
}

static MemTxResult exynos_auto9_pmu_read(void *opaque, hwaddr off,
                                      uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Pmu *s = opaque;
    /* Original vLM trapped a 32-bit read at the base of the shared PMU.
     * Only this exact word becomes readable when the board supplies a value. */
    if (off == 0 && s->om_input_active) {
        *value = s->om_input_value;
        s->om_input_reads++;
        s->om_input_cpu_reads += current_cpu != NULL;
        return MEMTX_OK;
    }
    if (s->abox_link && off == AUTO9_PMU_ABOX_CONTROL) {
        *value = s->words[off / 4];
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX PMU 0x19a0 read 0x%llx\n",
                      (unsigned long long)*value);
        return MEMTX_OK;
    }
    if (s->abox_dsp_off && exynos_auto9_pmu_abox_dsp_status(off)) {
        unsigned core = (off - 0x3404) / 0x80;
        s->abox_dsp_status_reads[core]++;
        *value = core == 0 && s->abox_running;
        return MEMTX_OK;
    }
    if (s->abox_link && exynos_auto9_pmu_abox_dsp_config(off)) {
        *value = s->abox_config[(off - 0x3400) / 0x80];
        return MEMTX_OK;
    }
    if (s->usb0_phy_isolation && off == AUTO9_PMU_USB0_PHY_ISOLATION) {
        s->usb0_phy_reads++;
        *value = s->words[off / 4];
        return MEMTX_OK;
    }
    if (s->usb0_phy_isolation && off == AUTO9_PMU_USB0_PHY_ISOLATION_2ND) {
        s->usb0_phy2_reads++;
        *value = s->words[off / 4];
        return MEMTX_OK;
    }
    int host = auto9_pmu_usb_host_index(off);
    if (s->usb0_phy_isolation && host >= 0) {
        s->usb_host_reads[host]++;
        *value = s->words[off / 4];
        return MEMTX_OK;
    }
    if (s->ufs0_phy_isolation && off == AUTO9_PMU_UFS0_PHY_ISOLATION) {
        s->ufs0_phy_reads++;
        *value = s->words[off / 4];
        return MEMTX_OK;
    }
    if (s->pcie23_phy_control && off == AUTO9_PMU_PCIE23_PHY_CONTROL) {
        s->pcie23_phy_reads++;
        *value = s->words[off / 4];
        return MEMTX_OK;
    }
    if (s->debug_profile == 1 && off == 0x210c) {
        *value = s->debug_words[0]; s->debug_reads[0]++;
        return MEMTX_OK;
    }
    if (exynos_auto9_domain_address(off)) {
        MemTxResult result = exynos_auto9_domain_access(s->domains, off, value, false);
        if (result != MEMTX_OK) { s->rejected++; }
        return result;
    }
    if (exynos_auto9_pmu_display_phy(off) || off == 0x840) {
        if (off == 0x840) { s->ufs_flag_reads++; exynos_auto9_pmu_ufs_observe(s, false, s->words[off / 4]); } else { s->display_phy_reads++; }
        *value = s->words[off / 4]; return MEMTX_OK;
    }
    if (off < 0x4000 && exynos_auto9_pmu_config(off)) { *value = s->words[off / 4]; return MEMTX_OK; }
    if (s->signal_profile == 1) {
        /* Original11B5C/op0 saves this single-bit input; it is NOT an ACK.
         * Explicit virtual input, with unknown physical reset/meaning. */
        if (off == 0x1204) { *value = s->status1204; return MEMTX_OK; }
        /* Exactly eight op0/mask1 literals at11C60..11E90. These external
         * inputs are NOT connected to virtual CPU power or fabricated ACKs. */
        if (off >= 0x1004 && off <= 0x1184 && (off - 0x1004) % 0x80 == 0) {
            *value = (s->core_status_mask >> ((off - 0x1004) / 0x80)) & 1;
            return MEMTX_OK;
        }
        if (off >= 0x1304 && off <= 0x1484 && (off - 0x1304) % 0x80 == 0) {
            *value = (s->core_status_mask >> (4 + (off - 0x1304) / 0x80)) & 1;
            return MEMTX_OK;
        }
        if (exynos_auto9_pmu_signal_config(off)) { *value = s->words[off / 4]; return MEMTX_OK; }
        if (off == 0x156c || off == 0x1570) { *value = s->signal_ack[(off - 0x156c) / 4]; return MEMTX_OK; }
        /* Declared cold SYSTEM_OPTION input, not a silicon reset value.
         * Original1576C uses bit3 to select its timeout policy. */
        if (off == 0x3a0c) { *value = 0; return MEMTX_OK; }
    }
    /* Selected cold external inputs: no warm reset or asserted input signal.
     * They are read-only and never become successful power transitions. */
    if (off == 0x404 || off == 0x500) { *value = 0; return MEMTX_OK; }
    if (off == 0x1524) { *value = s->link_status[0]; return MEMTX_OK; }
    if (s->link_profile == 1) {
        if (off == 0x3c00) { *value = s->link_selector; return MEMTX_OK; }
        if (off == 0x1ca4) { *value = s->link_status[1]; return MEMTX_OK; }
        if (off == 0x1ca0) { *value = s->words[off / 4]; return MEMTX_OK; }
    }
    if (off == 0x3a40) { *value = s->timer_status; return MEMTX_OK; }
    if (off >= 0x10000 && off < 0x11f00) {
        unsigned group = (off - 0x10000) / 0x100, reg = off & 255;
        if (reg == 0) { *value = s->group_enable[group]; return MEMTX_OK; }
        if (reg == 8) { *value = s->group_pending[group]; return MEMTX_OK; }
    }
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 PMU unsupported read offset=0x%llx\n", (unsigned long long)off);
    if (current_cpu) {
        CPUARMState *env = &ARM_CPU(current_cpu)->env;
        qemu_log_mask(LOG_UNIMP, "Auto9 PMU caller cpu=%d el=%d pc=0x%llx lr=0x%llx fp=0x%llx sp=0x%llx x0=0x%llx x1=0x%llx x2=0x%llx x3=0x%llx\n",
                      current_cpu->cpu_index, arm_current_el(env),
                      (unsigned long long)CPU_GET_CLASS(current_cpu)->get_pc(current_cpu),
                      (unsigned long long)env->xregs[30], (unsigned long long)env->xregs[29],
                      (unsigned long long)env->xregs[31], (unsigned long long)env->xregs[0],
                      (unsigned long long)env->xregs[1], (unsigned long long)env->xregs[2],
                      (unsigned long long)env->xregs[3]);
    }
    return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_pmu_write(void *opaque, hwaddr off,
                                       uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Pmu *s = opaque;
    s->last_offset = off; s->last_value = value;
    if (s->abox_link && off == AUTO9_PMU_ABOX_CONTROL) {
        if (value & ~BIT_ULL(11)) {
            goto reject;
        }
        s->words[off / 4] = value;
        s->writes++;
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX PMU 0x19a0 write 0x%llx\n",
                      (unsigned long long)value);
        return MEMTX_OK;
    }
    if (s->pcie23_phy_control && off == AUTO9_PMU_PCIE23_PHY_CONTROL) {
        if (value & ~1ULL) { goto reject; }
        /* Register latch only: neither PLL lock nor PCIe link is produced. */
        s->words[off / 4] = value;
        s->pcie23_phy_writes++;
        s->writes++;
        return MEMTX_OK;
    }
    if (s->abox_link && exynos_auto9_pmu_abox_dsp_config(off)) {
        unsigned core = (off - 0x3400) / 0x80;
        if (value > 1 || (core == 3 && value)) {
            goto reject;
        }
        if (s->abox_config[core] != value) {
            s->abox_config[core] = value;
            if (core == 0) {
                exynos_auto9_abox_request(s, value ? 'S' : 'R');
            }
        }
        s->writes++;
        return MEMTX_OK;
    }
    if (s->abox_dsp_off && exynos_auto9_pmu_abox_dsp_status(off)) {
        s->abox_dsp_status_rejected++;
        goto reject;
    }
    if (s->usb0_phy_isolation && off == AUTO9_PMU_USB0_PHY_ISOLATION) {
        /* Original UTMI regmap bit0: release isolation, not USB link ready. */
        if (value & ~1ULL) { goto reject; }
        s->words[off / 4] = value;
        s->usb0_phy_writes++;
        s->writes++;
        return MEMTX_OK;
    }
    if (s->usb0_phy_isolation && off == AUTO9_PMU_USB0_PHY_ISOLATION_2ND) {
        /* Original PIPE3 regmap isolation bit, independent of UTMI. */
        if (value & ~1ULL) { goto reject; }
        s->words[off / 4] = value;
        s->usb0_phy2_writes++;
        s->writes++;
        return MEMTX_OK;
    }
    int host = auto9_pmu_usb_host_index(off);
    if (s->usb0_phy_isolation && host >= 0) {
        if (value & ~1ULL) { goto reject; }
        s->words[off / 4] = value;
        s->usb_host_writes[host]++;
        s->writes++;
        return MEMTX_OK;
    }
    if (s->ufs0_phy_isolation && off == AUTO9_PMU_UFS0_PHY_ISOLATION) {
        if (value & ~1ULL) { goto reject; }
        /* Cold zero and writable bit0 only. No PMA, link, lock or IRQ result. */
        s->words[off / 4] = value;
        s->ufs0_phy_writes++;
        s->writes++;
        return MEMTX_OK;
    }
    if (exynos_auto9_domain_address(off)) {
        MemTxResult result = exynos_auto9_domain_access(s->domains, off, &value, true);
        if (result != MEMTX_OK) { s->rejected++; }
        return result;
    }
    if (exynos_auto9_pmu_display_phy(off) || off == 0x840) {
        if (off != 0x840 && (value & ~1ULL)) { goto reject; }
        s->words[off / 4] = value; s->writes++;
        if (off == 0x840) { s->ufs_flag_writes++; exynos_auto9_pmu_ufs_observe(s, true, value); } else { s->display_phy_writes++; }
        return MEMTX_OK;
    }
    if (off == 0x3c00 && s->link_profile == 1) {
        if (s->link_state || !exynos_auto9_pmu_link_payload(value)) { goto reject; }
        s->link_selector = value; s->link_selected = 1; s->writes++;
        return MEMTX_OK;
    }
    if (off >= 0x10000 && off < 0x11f00) {
        unsigned group = (off - 0x10000) / 0x100, reg = off & 255;
        if (reg == 0) { s->group_enable[group] = value; s->writes++; return MEMTX_OK; }
        if (reg == 12) { s->group_pending[group] &= ~value; s->writes++; return MEMTX_OK; }
    } else if (off < 0x10000) {
        unsigned alias = off & 0xc000, target = off & 0x3fff;
        bool link_config = s->link_profile == 1 && target == 0x1ca0;
        bool signal_config = s->signal_profile == 1 && exynos_auto9_pmu_signal_config(target);
        if (alias == 0x4000 || !(exynos_auto9_pmu_config(target) || link_config || signal_config) ||
            (alias && value >= 32)) { goto reject; }
        uint32_t before = s->words[target / 4], after;
        if (alias == 0x8000) { after = before & ~(1U << value); }
        else if (alias == 0xc000) { after = before | (1U << value); }
        else { after = value; }
        if (s->link_profile == 1 && (target == 0x1520 || target == 0x1ca0) &&
            !exynos_auto9_pmu_link_request(s, target == 0x1ca0, before, after)) { goto reject; }
        if (signal_config && (target == 0x1550 || target == 0x1560) &&
            !exynos_auto9_pmu_signal_request(s, target == 0x1560, before, after)) { goto reject; }
        s->words[target / 4] = after;
        s->writes++; if (alias) { s->atomic_writes++; }
        if (target >= 0xe34 && target <= 0xe3c && (after != before || !alias)) {
            exynos_auto9_pmu_timer_update(s, (target - 0xe34) / 4);
        }
        return MEMTX_OK;
    }
reject:
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 PMU unsupported write offset=0x%llx value=0x%llx\n",
                   (unsigned long long)off, (unsigned long long)value);
    return MEMTX_DECODE_ERROR;
}
/* An unconnected event line is low. Only a real virtual GPIO edge can latch
 * a pending bit; configuration writes never manufacture an interrupt. */
static void exynos_auto9_pmu_event(void *opaque, int line, int level)
{
    ExynosAuto9Pmu *s = opaque;
    if (level) { s->group_pending[line / 32] |= 1U << (line % 32); }
}
static const MemoryRegionOps exynos_auto9_pmu_ops = {
    .read_with_attrs = exynos_auto9_pmu_read, .write_with_attrs = exynos_auto9_pmu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_pmu_link_get(Object *obj, Visitor *visitor,
                                     const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    uint32_t value = !strcmp(name, "virtual-pmlink-v1") ? s->link_profile : s->link_peer;
    visit_type_uint32(visitor, name, &value, errp);
}
static char *exynos_auto9_pmu_link_history(Object *obj, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    GString *text = g_string_new("");
    for (unsigned i = 0; i < s->link_history_count; i++) {
        g_string_append_printf(text, "%s%u:%02x", i ? "," : "",
                               s->link_history[i] >> 8, s->link_history[i] & 255);
    }
    return g_string_free(text, false);
}
static void exynos_auto9_pmu_link_set(Object *obj, Visitor *visitor,
                                     const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    uint32_t value;
    if (!visit_type_uint32(visitor, name, &value, errp)) { return; }
    if (value > 1) { error_setg(errp, "Virtual PMU link input must be0 or1"); return; }
    if (!strcmp(name, "virtual-pmlink-v1")) {
        if (s->link_state || s->link_selected || (s->words[0x1520 / 4] & (1U << 8)) ||
            (s->words[0x1ca0 / 4] & (1U << 13))) {
            error_setg(errp, "Cannot change virtual PMU link profile during a transaction"); return;
        }
        s->link_profile = value;
    } else { s->link_peer = value; }
}
static void exynos_auto9_pmu_signal_get(Object *obj, Visitor *visitor,
                                       const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    uint32_t value = !strcmp(name, "virtual-latched-signals-v1") ? s->signal_profile :
                     !strcmp(name, "virtual-core-status-mask") ? s->core_status_mask :
                     !strcmp(name, "virtual-status1204") ? s->status1204 : s->signal_peer;
    visit_type_uint32(visitor, name, &value, errp);
}
static void exynos_auto9_pmu_signal_set(Object *obj, Visitor *visitor,
                                       const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    uint32_t value;
    if (!visit_type_uint32(visitor, name, &value, errp)) { return; }
    if (value > (!strcmp(name, "virtual-core-status-mask") ? 255 : 1)) {
        error_setg(errp, "Virtual PMU signal input outside its declared bound"); return;
    }
    if (!strcmp(name, "virtual-latched-signals-v1")) {
        if (s->signal_state[0] || s->signal_state[1] ||
            s->words[0x1550 / 4] || s->words[0x1560 / 4]) {
            error_setg(errp, "Cannot change virtual signal profile during a request"); return;
        }
        s->signal_profile = value;
    } else if (!strcmp(name, "virtual-core-status-mask")) { s->core_status_mask = value; }
    else if (!strcmp(name, "virtual-status1204")) { s->status1204 = value; }
    else { s->signal_peer = value; }
}
static void exynos_auto9_pmu_reset(DeviceState *dev)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(dev);
    memset(s->words, 0, sizeof(s->words));
    s->om_input_reads = s->om_input_cpu_reads = 0;
    memset(s->group_enable, 0, sizeof(s->group_enable));
    memset(s->group_pending, 0, sizeof(s->group_pending));
    s->writes = s->atomic_writes = s->rejected = s->timer_completions = 0;
    s->timer_status = s->last_offset = s->last_value = 0;
    s->ufs0_phy_reads = s->ufs0_phy_writes = 0;
    s->usb0_phy_reads = s->usb0_phy_writes = 0;
    s->usb0_phy2_reads = s->usb0_phy2_writes = 0;
    memset(s->usb_host_reads, 0, sizeof(s->usb_host_reads));
    memset(s->usb_host_writes, 0, sizeof(s->usb_host_writes));
    memset(s->abox_dsp_status_reads, 0, sizeof(s->abox_dsp_status_reads));
    s->abox_dsp_status_rejected = 0;
    if (s->abox_link && s->abox_config[0]) {
        exynos_auto9_abox_request(s, 'R');
    }
    memset(s->abox_config, 0, sizeof(s->abox_config));
    s->abox_started = false;
    s->abox_running = 0;
    s->abox_timer_sample_valid = false;
    s->abox_timer_high_reads = s->abox_timer_low_reads = 0;
    s->abox_hifi_early_reads = 0;
    s->abox_dram_request = 0;
    s->abox_requests = s->abox_reports = s->abox_bad_messages = 0;
    s->pcie23_phy_reads = s->pcie23_phy_writes = 0;
    s->display_phy_reads = s->display_phy_writes = s->ufs_flag_reads = s->ufs_flag_writes = 0;
    s->ufs_access_count = s->ufs_access_dropped = s->ufs_cpu_reads = s->ufs_cpu_writes = 0;
    memset(s->ufs_access, 0, sizeof(s->ufs_access));
    memset(s->debug_reads, 0, sizeof(s->debug_reads)); /* External inputs survive reset. */
    timer_del(s->link_deadline);
    s->link_selector = s->link_selected = s->link_state = s->link_route = 0;
    s->link_status[0] = s->link_status[1] = 0;
    s->link_deliveries = s->link_releases = s->link_cancelled = 0;
    s->link_last_payload = s->link_last_route = s->link_received_valid = 0;
    s->link_received[0] = s->link_received[1] = 0;
    s->link_history_count = 0; s->link_last_delivery_ns = 0;
    memset(s->link_history, 0, sizeof(s->link_history));
    memset(s->signal_state, 0, sizeof(s->signal_state));
    memset(s->signal_pending, 0, sizeof(s->signal_pending));
    memset(s->signal_latched, 0, sizeof(s->signal_latched));
    memset(s->signal_ack, 0, sizeof(s->signal_ack));
    s->signal_commits = s->signal_releases = s->signal_cancelled = s->signal_failed = s->signal_valid = 0;
    for (unsigned i = 0; i < 2; i++) { timer_del(s->signals[i].deadline); }
    for (unsigned i = 0; i < 64; i++) { qemu_set_irq(s->signal_outputs[i], 0); }
    for (unsigned i = 0; i < 3; i++) { timer_del(s->timers[i].deadline); }
}
static void exynos_auto9_pmu_debug_input(void *opaque, int line, int level)
{
    ExynosAuto9Pmu *s = opaque;
    uint32_t mask = 1U << (line % 32);
    if (level) { s->debug_words[line / 32] |= mask; }
    else { s->debug_words[line / 32] &= ~mask; }
}
static void exynos_auto9_pmu_debug_get(Object *obj, Visitor *v, const char *name,
                                      void *opaque, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    unsigned index = !strcmp(name, "dptx-debug-word2") ? 2 : !strcmp(name, "dptx-debug-word1");
    uint32_t value = !strcmp(name, "dptx-debug-profile") ? s->debug_profile : s->debug_words[index];
    visit_type_uint32(v, name, &value, errp);
}
static void exynos_auto9_pmu_debug_set(Object *obj, Visitor *v, const char *name,
                                      void *opaque, Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    uint32_t value;
    if (!visit_type_uint32(v, name, &value, errp)) { return; }
    if (!strcmp(name, "dptx-debug-profile")) {
        if (value > 1) { error_setg(errp, "Require debug input profile0 or1"); return; }
        s->debug_profile = value;
    } else {
        unsigned word = !strcmp(name, "dptx-debug-word2") ? 2 : !strcmp(name, "dptx-debug-word1");
        for (unsigned i = 0; i < 32; i++) {
            exynos_auto9_pmu_debug_input(s, word * 32 + i, (value >> i) & 1);
        }
    }
}
/* The same explicit external observation bank supplies two source-declared
 * CMU words that the original raw dump reads. No clock status is invented;
 * original setters poll600/610 bit16, NOT these604/614 bit28 fields.
 * Sparse shared input ownership avoids a second MMIO aperture/RAM overlay. */
static bool exynos_auto9_pmu_debug_cmu(hwaddr base, hwaddr off, uint64_t *value)
{
    if (base != 0x18a00000 || (off != 0x604 && off != 0x614)) { return false; }
    Object *obj = object_resolve_path("/machine/exynos-pmu", NULL);
    if (!obj) { return false; }
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    if (s->debug_profile != 1) { return false; }
    unsigned index = off == 0x604 ? 1 : 2;
    *value = s->debug_words[index]; s->debug_reads[index]++;
    return true;
}
static void exynos_auto9_pmu_om_input_set(Object *obj, Visitor *v,
                                         const char *name, void *opaque,
                                         Error **errp)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    uint32_t value;
    if (DEVICE(obj)->realized) {
        error_setg(errp, "PMU OM input is fixed at device realization");
        return;
    }
    if (visit_type_uint32(v, name, &value, errp)) {
        s->om_input_active = 1;
        s->om_input_value = value;
    }
}

static void exynos_auto9_pmu_init(Object *obj)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    object_property_add(obj, "virtual-om-input", "uint32", NULL,
                        exynos_auto9_pmu_om_input_set, NULL, NULL);
    object_property_add_uint32_ptr(obj, "om-input-active", &s->om_input_active, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "om-input-value", &s->om_input_value, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "om-input-reads", &s->om_input_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "om-input-cpu-reads", &s->om_input_cpu_reads, OBJ_PROP_FLAG_READ);
    qdev_init_gpio_in_named(DEVICE(obj), exynos_auto9_pmu_debug_input, "dptx-debug-input", 96);
    const char *debug_props[] = {"dptx-debug-profile", "dptx-debug-word0", "dptx-debug-word1", "dptx-debug-word2"};
    for (unsigned i = 0; i < ARRAY_SIZE(debug_props); i++) {
        object_property_add(obj, debug_props[i], "uint32", exynos_auto9_pmu_debug_get,
                            exynos_auto9_pmu_debug_set, NULL, NULL);
    }
    object_property_add_uint32_ptr(obj, "dptx-debug-reads0", &s->debug_reads[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dptx-debug-reads1", &s->debug_reads[1], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dptx-debug-reads2", &s->debug_reads[2], OBJ_PROP_FLAG_READ);
    memory_region_init_io(&s->mmio, obj, &exynos_auto9_pmu_ops, s, TYPE_EXYNOS_AUTO9_PMU, 0x12000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    qdev_init_gpio_in_named(DEVICE(obj), exynos_auto9_pmu_event, "pmu-event", 31 * 32);
    qdev_init_gpio_out_named(DEVICE(obj), s->signal_outputs, "pmu-latched-signals", 64);
    for (unsigned i = 0; i < 2; i++) {
        s->signals[i].pmu = s; s->signals[i].index = i;
        s->signals[i].deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_pmu_signal_expire, &s->signals[i]);
    }
    s->link_deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_pmu_link_expire, s);
    for (unsigned i = 0; i < 3; i++) {
        s->timers[i].pmu = s; s->timers[i].index = i;
        s->timers[i].deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_pmu_expire, &s->timers[i]);
    }
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "display-phy-reads", &s->display_phy_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "display-phy-writes", &s->display_phy_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs-bootflag-reads", &s->ufs_flag_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs-bootflag-writes", &s->ufs_flag_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs-cpu-reads", &s->ufs_cpu_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs-cpu-writes", &s->ufs_cpu_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs-access-dropped", &s->ufs_access_dropped, OBJ_PROP_FLAG_READ);
    object_property_add_str(obj, "ufs-access-first64", exynos_auto9_pmu_ufs_history, NULL);
    object_property_add_uint32_ptr(obj, "pcie23-phy-control", &s->words[AUTO9_PMU_PCIE23_PHY_CONTROL / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pcie23-phy-reads", &s->pcie23_phy_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pcie23-phy-writes", &s->pcie23_phy_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "usb0-phy-isolation", &s->words[AUTO9_PMU_USB0_PHY_ISOLATION / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "usb0-phy-reads", &s->usb0_phy_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "usb0-phy-writes", &s->usb0_phy_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "usb0-phy2-isolation", &s->words[AUTO9_PMU_USB0_PHY_ISOLATION_2ND / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "usb0-phy2-reads", &s->usb0_phy2_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "usb0-phy2-writes", &s->usb0_phy2_writes, OBJ_PROP_FLAG_READ);
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_pmu_usb_host_isolation); i++) {
        unsigned off = auto9_pmu_usb_host_isolation[i];
        g_autofree char *value = g_strdup_printf("usb-host-%03x-isolation", off);
        g_autofree char *reads = g_strdup_printf("usb-host-%03x-reads", off);
        g_autofree char *writes = g_strdup_printf("usb-host-%03x-writes", off);
        object_property_add_uint32_ptr(obj, value, &s->words[off / 4], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, reads, &s->usb_host_reads[i], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, writes, &s->usb_host_writes[i], OBJ_PROP_FLAG_READ);
    }
    object_property_add_uint32_ptr(obj, "ufs0-phy-isolation", &s->words[AUTO9_PMU_UFS0_PHY_ISOLATION / 4], OBJ_PROP_FLAG_READ);
    for (unsigned i = 0; i < ARRAY_SIZE(s->abox_dsp_status_reads); i++) {
        g_autofree char *key = g_strdup_printf("abox-dsp-core%u-status-reads", i);
        object_property_add_uint32_ptr(obj, key, &s->abox_dsp_status_reads[i],
                                       OBJ_PROP_FLAG_READ);
    }
    object_property_add_uint32_ptr(obj, "abox-hifi-early-reads", &s->abox_hifi_early_reads,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-timer-high-reads", &s->abox_timer_high_reads,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-timer-low-reads", &s->abox_timer_low_reads,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-core0-executing", &s->abox_running,
                                 OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-core0-link-requests", &s->abox_requests,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-core0-execution-reports", &s->abox_reports,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-core0-link-rejected", &s->abox_bad_messages,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "abox-dsp-status-rejected",
                                   &s->abox_dsp_status_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs0-phy-reads", &s->ufs0_phy_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs0-phy-writes", &s->ufs0_phy_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "display-phy-730", &s->words[0x730 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "display-phy-738", &s->words[0x738 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "display-phy-73c", &s->words[0x73c / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "display-phy-740", &s->words[0x740 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ufs-lk-bootflag", &s->words[0x840 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atomic-writes", &s->atomic_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "timer-completions", &s->timer_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "timer-status", &s->timer_status, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-write-offset", &s->last_offset, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-write-value", &s->last_value, OBJ_PROP_FLAG_READ);
    object_property_add(obj, "virtual-pmlink-v1", "uint32", exynos_auto9_pmu_link_get, exynos_auto9_pmu_link_set, NULL, NULL);
    object_property_add(obj, "link-peer-connected", "uint32", exynos_auto9_pmu_link_get, exynos_auto9_pmu_link_set, NULL, NULL);
    object_property_add_uint32_ptr(obj, "link-state", &s->link_state, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-deliveries", &s->link_deliveries, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-releases", &s->link_releases, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-cancelled", &s->link_cancelled, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-last-payload", &s->link_last_payload, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-last-route", &s->link_last_route, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-received0", &s->link_received[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-received1", &s->link_received[1], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-received-valid", &s->link_received_valid, OBJ_PROP_FLAG_READ);
    object_property_add_str(obj, "link-history-first32", exynos_auto9_pmu_link_history, NULL);
    object_property_add_uint64_ptr(obj, "link-last-delivery-ns", &s->link_last_delivery_ns, OBJ_PROP_FLAG_READ);
    object_property_add(obj, "virtual-latched-signals-v1", "uint32", exynos_auto9_pmu_signal_get, exynos_auto9_pmu_signal_set, NULL, NULL);
    object_property_add(obj, "signal-peer-connected", "uint32", exynos_auto9_pmu_signal_get, exynos_auto9_pmu_signal_set, NULL, NULL);
    object_property_add(obj, "virtual-status1204", "uint32", exynos_auto9_pmu_signal_get, exynos_auto9_pmu_signal_set, NULL, NULL);
    object_property_add(obj, "virtual-core-status-mask", "uint32", exynos_auto9_pmu_signal_get, exynos_auto9_pmu_signal_set, NULL, NULL);
    object_property_add_uint32_ptr(obj, "signal-latched0", &s->signal_latched[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-latched1", &s->signal_latched[1], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-state0", &s->signal_state[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-state1", &s->signal_state[1], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-commits", &s->signal_commits, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-releases", &s->signal_releases, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-cancelled", &s->signal_cancelled, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-failed", &s->signal_failed, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "signal-valid", &s->signal_valid, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_pmu_finalize(Object *obj)
{
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(obj);
    for (unsigned i = 0; i < 3; i++) { timer_free(s->timers[i].deadline); }
    if (s->abox_link) {
        qemu_chr_fe_deinit(&s->abox_peer, false);
    }
    timer_free(s->link_deadline);
    for (unsigned i = 0; i < 2; i++) { timer_free(s->signals[i].deadline); }
}
static void exynos_auto9_pmu_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_pmu_reset);
}
static const TypeInfo exynos_auto9_pmu_type = {
    .name = TYPE_EXYNOS_AUTO9_PMU, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Pmu), .instance_init = exynos_auto9_pmu_init,
    .instance_finalize = exynos_auto9_pmu_finalize, .class_init = exynos_auto9_pmu_class_init,
};
static void exynos_auto9_pmu_register(void) { type_register_static(&exynos_auto9_pmu_type); }
type_init(exynos_auto9_pmu_register)
static bool virt_get_exynos_ufs_phy_isolation(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->exynos_ufs_phy_isolation;
}
static void virt_set_exynos_ufs_phy_isolation(Object *obj, bool value, Error **errp)
{
    VIRT_MACHINE(obj)->exynos_ufs_phy_isolation = value;
}
static void exynos_auto9_pmu_create(VirtMachineState *vms)
{
    if (vms->exynos_pmu_profile > 1) { error_report("Unsupported virtual PMU profile"); exit(1); }
    if (!vms->exynos_acpm || !vms->exynos_pmu_profile) { return; }
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_PMU);
    object_property_add_child(OBJECT(vms), "exynos-pmu", OBJECT(dev));
    DeviceState *domains = qdev_new(TYPE_EXYNOS_AUTO9_DOMAINS);
    object_property_add_child(OBJECT(dev), "domains", OBJECT(domains));
    EXYNOS_AUTO9_PMU(dev)->domains = EXYNOS_AUTO9_DOMAINS(domains);
    EXYNOS_AUTO9_PMU(dev)->ufs0_phy_isolation = vms->exynos_ufs_phy_isolation;
    EXYNOS_AUTO9_PMU(dev)->usb0_phy_isolation = vms->exynos_linux_usb_phy;
    EXYNOS_AUTO9_PMU(dev)->abox_dsp_off = vms->exynos_abox_dsp_off == 1;
    EXYNOS_AUTO9_PMU(dev)->pcie23_phy_control = vms->exynos_pcie2_phy == 1;
    sysbus_realize_and_unref(SYS_BUS_DEVICE(domains), &error_fatal);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10460000);
    ExynosAuto9Pmu *s = EXYNOS_AUTO9_PMU(dev);
    Chardev *peer = qemu_chr_find("auto9-dsp-lifecycle");
    if (peer) {
        if (!s->abox_dsp_off) {
            error_report("Auto9 DSP lifecycle requires guarded PMU status");
            exit(1);
        }
        qemu_chr_fe_init(&s->abox_peer, peer, &error_fatal);
        s->abox_link = true;
        qemu_chr_fe_set_handlers(&s->abox_peer, exynos_auto9_abox_can_read,
                                 exynos_auto9_abox_receive,
                                 exynos_auto9_abox_event, NULL, s, NULL, true);
        memory_region_init_io(&s->abox_dram_request_mmio, OBJECT(dev),
                              &exynos_auto9_abox_dram_request_ops, s,
                              "auto9-abox-dram-request", 4);
        memory_region_add_subregion(get_system_memory(), 0x19d40010,
                                    &s->abox_dram_request_mmio);
        memory_region_init_io(&s->abox_hifi_early, OBJECT(dev),
                              &exynos_auto9_abox_hifi_early_ops, s,
                              "auto9-abox-hifi-early", 4);
        memory_region_add_subregion(get_system_memory(), 0x19c90014,
                                    &s->abox_hifi_early);
        memory_region_init_io(&s->abox_core3_select, OBJECT(dev),
                              &exynos_auto9_abox_core3_select_ops, s,
                              "auto9-abox-core3-disable", 4);
        memory_region_add_subregion(get_system_memory(), 0x19c9c000,
                                    &s->abox_core3_select);
        memory_region_init_io(&s->abox_timer_view, OBJECT(dev),
                              &exynos_auto9_abox_timer_view_ops, s,
                              "auto9-abox-ap-timer-read", 8);
        memory_region_add_subregion(get_system_memory(), 0x19ce0610,
                                    &s->abox_timer_view);
        Chardev *mailbox = qemu_chr_find("auto9-dsp-mailbox");
        if (mailbox) {
            Object *abox = object_resolve_path_component(OBJECT(vms), "exynos-abox-gic");
            Object *controller = abox ? object_resolve_path_component(abox, "controller") : NULL;
            if (!controller) {
                error_report("Auto9 native mailbox needs the independent ABOX GIC");
                exit(1);
            }
            DeviceState *gic = DEVICE(controller);
            /* The HCB physical line wakes its guest; the original handler
             * then reads independent ABOX GICC IAR. OEM spi-mailbox[]
             * gives SPIs 0xc1/0xc6/0xcb (INTIDs 225/230/235), not DSP 224/229/234. */
            auto9_mb_create(OBJECT(vms), mailbox, false,
                            qdev_get_gpio_in(gic, 0xc1),
                            qdev_get_gpio_in(gic, 0xc6),
                            qdev_get_gpio_in(gic, 0xcb),
                            qdev_get_gpio_in(vms->gic, 49),
                            qdev_get_gpio_in(vms->gic, 59),
                            qdev_get_gpio_in(vms->gic, 60));
            auto9_abox_sft_create(OBJECT(vms));
        }
    }
}
