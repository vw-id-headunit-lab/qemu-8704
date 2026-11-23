/* SPDX-License-Identifier: GPL-2.0-or-later
 * Auto9 DP0 LINK reset and optional local PHY initialization. Bind to
 * original VM2 HCB and libDPUexynos. Optional virtual AUX/HPD sink;
 * no stream generation, HDCP, physical display timing, or scanout.
 */
#define TYPE_AUTO9_DP0_RESET "exynos-auto9.dp0-reset"
#define AUTO9_DP0_RESET_NS 500
#define AUTO9_DP0_PLL_NS 4000
#define AUTO9_DP0_HPD_NS 1000000
#define AUTO9_DP0_AUX_NS 4000
#define AUTO9_DP0_TRAIN_NS 50000
#define AUTO9_DP0_HPD_PLUG BIT(8)
#define AUTO9_DP0_HPD_LEVEL BIT(8)
OBJECT_DECLARE_SIMPLE_TYPE(Auto9Dp0Reset, AUTO9_DP0_RESET)

typedef struct Auto9Dp0LinkWord {
    MemoryRegion region;
    Auto9Dp0Reset *controller;
    hwaddr offset;
    uint32_t value, mask, reads, writes;
    bool w1c;
} Auto9Dp0LinkWord;

/* Per-source SST programming; these latches do not emit pixels or IRQs. */
static const struct { hwaddr off; uint32_t mask; bool w1c; } auto9_dp0_sst_words[] = {
    /* OEM sets bits 7/8 individually; startup also clears low eight bits. */
    { 0x028, 0x1ff, true }, { 0x02c, 0x1ff, false },
    { 0x044, UINT32_MAX }, { 0x048, UINT32_MAX },
    { 0x04c, UINT32_MAX }, { 0x050, UINT32_MAX },
    { 0x080, 0x3f }, { 0x084, 0x3fffffff }, { 0x088, 0x3fffffff },
    { 0x090, 0x3f }, { 0x094, 0x3fffffff }, { 0x098, 0xf },
    { 0x400, 0x3ff },
    /* dp_reg_start enables the video request; no vblank without scanout. */
    { 0x404, 1 }, { 0x408, 1 },
    { 0x414, UINT32_MAX }, { 0x418, UINT32_MAX },
    { 0x41c, UINT32_MAX }, { 0x420, UINT32_MAX },
    { 0x424, UINT32_MAX }, { 0x428, UINT32_MAX },
    { 0x42c, UINT32_MAX }, { 0x430, UINT32_MAX },
    /* The sink did not advertise DSC. Expose disabled status only. */
    { 0x434, 0 }, { 0x450, 8 },
};
typedef struct Auto9Dp0Sst {
    MemoryRegion region;
    uint32_t words[G_N_ELEMENTS(auto9_dp0_sst_words)], writes;
} Auto9Dp0Sst;

static const struct {
    hwaddr offset;
    uint32_t mask;
    const char *name;
    bool plug, w1c;
} auto9_dp0_link_words[] = {
    { 0x14, 1, "software-functions", false, false },
    { 0x18, 3, "common-functions", false, false },
    { 0x3004, 0x3333, "lane-map", false, false },
    /* Original VM2 clears pending with ones, then enables HPD mask bits.
     * Without a sink there is no HPD input or source of pending events. */
    { 0x108, 0xf00, "plug-status", true, true },
    { 0x10c, 0xf00, "plug-mask", true, false },
    /* Store only the OEM deglitch field; HPD_STATUS[8] is not writable. */
    { 0x28, 0x3fff0000, "hpd-config", true, false },
    /* Original link-training latches; map only with the virtual sink. */
    { 0x10, 0xff, "source-lane-count", false, false },
    { 0x3000, 0x71, "source-pcs-control", false, false },
    { 0x3008, 0x7, "source-pcs-test-pattern", false, false },
    { 0x5000, 0x60, "source-enhanced-sst0", false, false },
    { 0x6000, 0x60, "source-enhanced-sst1", false, false },
    { 0x7000, 0x60, "source-enhanced-sst2", false, false },
    { 0x8000, 0x60, "source-enhanced-sst3", false, false },
    { 0x2000, 0, "source-mst-disabled", false, false },
    { 0x2010, 1, "source-enable-sst0", false, false },
    { 0x2020, 1, "source-enable-sst1", false, false },
    { 0x2030, 1, "source-enable-sst2", false, false },
    { 0x2040, 1, "source-enable-sst3", false, false },
    /* OEM dp_reg_sst_function_enable selects non-uniform LINK offsets. */
    { 0x1c, 1, "source-functions-sst0", false, false },
    { 0x20, 1, "source-functions-sst1", false, false },
    { 0x30, 1, "source-functions-sst2", false, false },
    { 0x34, 1, "source-functions-sst3", false, false },
};

struct Auto9Dp0Reset {
    SysBusDevice parent_obj;
    MemoryRegion reg;
    Auto9Dp0LinkWord link[G_N_ELEMENTS(auto9_dp0_link_words)];
    Auto9Dp0Sst sst[4];
    QEMUTimer *timer;
    uint32_t busy, reads, writes, requests, completions;
    MemoryRegion phy, pll_status;
    QEMUTimer *pll_timer;
    uint64_t phy_config_seen;
    int64_t phy_start_ns;
    bool phy_armed;
    uint32_t phy_control, phy_mode, phy_ssc, phy_bias, phy_rate, phy_lanes[4];
    uint32_t phy_reads, phy_writes, phy_extra_seen;
    uint32_t pll_locked, pll_reads, pll_requests, pll_completions;
    uint32_t phy_release_seen, phy_release_gap_ns, phy_release_config_count;
    bool sink_enabled, cr_read, eq_read;
    qemu_irq sink_irq;
    MemoryRegion aux;
    QEMUTimer *hpd_timer, *aux_timer;
    uint32_t hpd_level, hpd_events, hpd_irq_level;
    uint32_t aux_control, aux_addr_only, aux_request, aux_command, aux_monitor;
    uint32_t aux_busy, aux_requests, aux_replies, aux_nacks, aux_mmio_rejected;
    uint32_t aux_native_reads, aux_native_writes, aux_i2c_reads, aux_i2c_writes;
    uint32_t aux_last_command, aux_last_address, link_phase, link_trained;
    uint32_t phy_tune_writes;
    uint8_t phy_tune[4][4];
    int64_t link_ready_ns;
    uint8_t tx[16], rx[16], dpcd_config[16], dpcd_power, edid_offset;
};

static void auto9_dp0_hpd_maybe(Auto9Dp0Reset *s);
static void auto9_dp0_hpd_update_irq(Auto9Dp0Reset *s);
static void auto9_dp0_sink_reset(Auto9Dp0Reset *s);

static void auto9_dp0_reset_done(void *opaque)
{
    Auto9Dp0Reset *s = opaque;
    s->busy = 0;
    s->completions++;
    auto9_dp0_hpd_maybe(s);
}

static uint64_t auto9_dp0_reset_read(void *opaque, hwaddr addr, unsigned size)
{
    Auto9Dp0Reset *s = opaque;
    s->reads++;
    return s->busy;
}

static void auto9_dp0_reset_write(void *opaque, hwaddr addr, uint64_t value,
                                 unsigned size)
{
    Auto9Dp0Reset *s = opaque;
    s->writes++;
    /* A write of zero or a duplicate assert cannot cancel/extend the pulse. */
    if ((value & 1) && !s->busy) {
        for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
            s->link[i].value = 0;
        }
        for (unsigned i = 0; i < G_N_ELEMENTS(s->sst); i++) {
            memset(s->sst[i].words, 0, sizeof(s->sst[i].words));
        }
        auto9_dp0_sink_reset(s);
        s->busy = 1;
        s->requests++;
        timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  AUTO9_DP0_RESET_NS);
    }
}

static const MemoryRegionOps auto9_dp0_reset_ops = {
    .read = auto9_dp0_reset_read,
    .write = auto9_dp0_reset_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* OEM phy_default_value at libDPUexynos.so.1 VA 0x3e088. The
 * offset/value sequence is not a register specification for other PHYs. */
static const struct { uint16_t offset; uint8_t value; } auto9_dp0_phy_defaults[] = {
    { 0x0830, 0x07 },
    { 0x085c, 0x80 },
    { 0x1030, 0x07 },
    { 0x105c, 0x80 },
    { 0x1830, 0x07 },
    { 0x185c, 0x80 },
    { 0x2030, 0x07 },
    { 0x205c, 0x80 },
    { 0x0228, 0x38 },
    { 0x0104, 0x44 },
    { 0x0248, 0x44 },
    { 0x038c, 0x02 },
    { 0x0878, 0x04 },
    { 0x1878, 0x04 },
    { 0x0898, 0x77 },
    { 0x1898, 0x77 },
    { 0x0054, 0x01 },
    { 0x00e0, 0x38 },
    { 0x0060, 0x24 },
    { 0x0064, 0x77 },
    { 0x0070, 0x76 },
    { 0x0234, 0xe8 },
    { 0x0af4, 0x15 },
    { 0x1af4, 0x15 },
    { 0x081c, 0xe5 },
    { 0x181c, 0xe5 },
    { 0x091c, 0x1f },
    { 0x191c, 0x1f },
    { 0x0928, 0x7c },
    { 0x1928, 0x7c },
    { 0x0994, 0x14 },
    { 0x1994, 0x14 },
    { 0x099c, 0x48 },
    { 0x199c, 0x48 },
    { 0x09a4, 0x0b },
    { 0x09a8, 0x62 },
    { 0x19a4, 0x0b },
    { 0x19a8, 0x62 },
    { 0x09b8, 0x3e },
    { 0x19b8, 0x3e },
    { 0x09e4, 0x05 },
    { 0x09e8, 0x02 },
    { 0x09ec, 0x02 },
    { 0x19e4, 0x05 },
    { 0x19e8, 0x02 },
    { 0x19ec, 0x02 },
    { 0x0a34, 0x1c },
    { 0x1a34, 0x1c },
    { 0x0a98, 0x2f },
    { 0x1a98, 0x2f },
    { 0x0c30, 0x0c },
    { 0x0c48, 0x08 },
    { 0x1c30, 0x0c },
    { 0x1c48, 0x08 },
};

static int auto9_dp0_phy_index(hwaddr off)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_dp0_phy_defaults); i++) {
        if (auto9_dp0_phy_defaults[i].offset == off) {
            return i;
        }
    }
    return -1;
}

static void auto9_dp0_pll_locked(void *opaque)
{
    Auto9Dp0Reset *s = opaque;
    s->pll_locked = 1;
    s->pll_completions++;
    auto9_dp0_hpd_maybe(s);
}

static MemTxResult auto9_dp0_phy_fault(hwaddr off, bool write)
{
    qemu_log_mask(LOG_UNIMP, "Auto9 DP0 PHY unsupported %s offset 0x%"
                  HWADDR_PRIx "\n", write ? "write" : "read", off);
    return MEMTX_DECODE_ERROR;
}

/* OEM phy_tune_parameters at 0x3df88. A lane has four byte-valued latches. */
static const uint8_t auto9_dp0_phy_tune_values[16][4] = {
    {33,16,66,229}, {37,20,66,229}, {38,23,67,229}, {43,28,67,231},
    {38,16,66,231}, {43,21,66,231}, {43,24,67,231}, {43,24,67,231},
    {42,16,66,231}, {43,21,67,231}, {43,21,67,231}, {43,21,67,231},
    {43,16,67,231}, {43,16,67,231}, {43,16,67,231}, {43,16,67,231},
};

static bool auto9_dp0_phy_tune_index(Auto9Dp0Reset *s, hwaddr off,
                                    unsigned *lane, unsigned *field)
{
    if (!s->sink_enabled || off < 0x810 || off > 0x201c || (off & 3) ||
        ((off - 0x810) & 0x7ff) > 0xc) {
        return false;
    }
    *lane = (off - 0x810) >> 11;
    *field = ((off - 0x810) & 0x7ff) >> 2;
    return true;
}

static MemTxResult auto9_dp0_phy_read(void *opaque, hwaddr off, uint64_t *value,
                                     unsigned size, MemTxAttrs attrs)
{
    Auto9Dp0Reset *s = opaque;
    unsigned lane, field;
    switch (off) {
    case 0x38c: *value = s->phy_control; break;
    case 0x024: *value = s->phy_mode; break;
    case 0x28c: *value = s->phy_rate; break;
    case 0x2d0: *value = s->phy_ssc; break;
    case 0x854: case 0x1054: case 0x1854: case 0x2054:
        lane = (off - 0x854) / 0x800;
        *value = s->phy_lanes[lane];
        break;
    default:
        if (auto9_dp0_phy_tune_index(s, off, &lane, &field)) {
            *value = s->phy_tune[lane][field];
            break;
        }
        return auto9_dp0_phy_fault(off, false);
    }
    s->phy_reads++;
    return MEMTX_OK;
}

static MemTxResult auto9_dp0_phy_write(void *opaque, hwaddr off, uint64_t value,
                                      unsigned size, MemTxAttrs attrs)
{
    Auto9Dp0Reset *s = opaque;
    const uint64_t all_defaults = (UINT64_C(1) << G_N_ELEMENTS(auto9_dp0_phy_defaults)) - 1;
    int index = auto9_dp0_phy_index(off);
    unsigned lane, field;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    /* 0x81c/0x181c also occur in the OEM cold-default table. In the
     * powered phase they are per-lane tune writes, not cold defaults. */
    if ((index < 0 || (s->phy_control & 4)) &&
        auto9_dp0_phy_tune_index(s, off, &lane, &field)) {
        for (unsigned i = 0; i < G_N_ELEMENTS(auto9_dp0_phy_tune_values); i++) {
            if (auto9_dp0_phy_tune_values[i][field] == value) {
                s->phy_tune[lane][field] = value;
                s->phy_tune_writes++;
                s->phy_writes++;
                return MEMTX_OK;
            }
        }
        return auto9_dp0_phy_fault(off, true);
    }

    if (off == 0x38c) {
        uint32_t old = s->phy_control;
        if (value & ~UINT64_C(0xe)) {
            return auto9_dp0_phy_fault(off, true);
        }
        s->phy_control = value;
        if (!value || ((old & 4) && !(value & 4))) {
            s->phy_config_seen = s->phy_extra_seen = 0;
        }
        if (index >= 0 && value == auto9_dp0_phy_defaults[index].value) {
            s->phy_config_seen |= UINT64_C(1) << index;
        }
        if (!(value & 4)) {
            timer_del(s->pll_timer);
            s->pll_locked = 0;
        }
        if (value == 0xa) {
            s->phy_start_ns = now;
            s->phy_armed = true;
        } else {
            if (value == 0xe && old == 0xa && s->phy_armed) {
                s->phy_release_seen++;
                s->phy_release_gap_ns = MIN(now - s->phy_start_ns, UINT32_MAX);
                s->phy_release_config_count = ctpop64(s->phy_config_seen);
            }
            if (value == 0xe && old == 0xa && s->phy_armed && s->completions &&
                s->phy_config_seen == all_defaults && s->phy_extra_seen == 0x7f &&
                s->phy_mode == 0x68 && !s->phy_ssc && s->phy_bias == 0xff &&
                s->phy_lanes[0] == s->phy_lanes[1] &&
                s->phy_lanes[0] == s->phy_lanes[2] &&
                s->phy_lanes[0] == s->phy_lanes[3]) {
                /* Model a local PLL after the verified programming sequence,
                 * not a connected sink or an operational DP link. */
                s->pll_requests++;
                timer_mod(s->pll_timer, MAX(now, s->phy_start_ns + 1000) +
                          AUTO9_DP0_PLL_NS);
            }
            s->phy_armed = false;
        }
    } else if (index >= 0) {
        if (value != auto9_dp0_phy_defaults[index].value || (s->phy_control & 4)) {
            return auto9_dp0_phy_fault(off, true);
        }
        s->phy_config_seen |= UINT64_C(1) << index;
        if (auto9_dp0_phy_tune_index(s, off, &lane, &field)) {
            s->phy_tune[lane][field] = value;
        }
    } else {
        switch (off) {
        case 0x024:
            if (value & ~UINT64_C(0x78)) {
                return auto9_dp0_phy_fault(off, true);
            }
            s->phy_mode = value;
            s->phy_extra_seen |= 1;
            break;
        case 0x2d0:
            if (value & ~UINT64_C(2)) {
                return auto9_dp0_phy_fault(off, true);
            }
            s->phy_ssc = value;
            s->phy_extra_seen |= 2;
            break;
        case 0x28c:
            /* dp_reg_phy_get/set_link_bw use bits 6:5 only. Cold zero is
             * a virtual RBR selector, not a negotiated sink/link rate. */
            if (value & ~UINT64_C(0x60)) {
                return auto9_dp0_phy_fault(off, true);
            }
            s->phy_rate = value;
            break;
        case 0x288:
            if (value != 0xff && value != 0) {
                return auto9_dp0_phy_fault(off, true);
            }
            s->phy_bias = value;
            s->phy_extra_seen |= 4;
            break;
        case 0x854: case 0x1054: case 0x1854: case 0x2054:
            if (value & ~UINT64_C(2)) {
                return auto9_dp0_phy_fault(off, true);
            }
            lane = (off - 0x854) / 0x800;
            s->phy_lanes[lane] = value;
            s->phy_extra_seen |= 8 << lane;
            break;
        default:
            return auto9_dp0_phy_fault(off, true);
        }
    }
    s->phy_writes++;
    return MEMTX_OK;
}

static uint64_t auto9_dp0_pll_status(void *opaque, hwaddr off, unsigned size)
{
    Auto9Dp0Reset *s = opaque;
    s->pll_reads++;
    /* OEM CMP followed by CCMP ... NZCV=4 exits the loop when bit 4
     * is set. It stays in the loop for zero until the count expires. */
    return s->pll_locked ? 0x10 : 0;
}

static MemTxResult auto9_dp0_pll_write(void *opaque, hwaddr off, uint64_t value,
                                     unsigned size, MemTxAttrs attrs)
{
    return auto9_dp0_phy_fault(0x2c, true);
}

static const MemoryRegionOps auto9_dp0_phy_ops = {
    .read_with_attrs = auto9_dp0_phy_read,
    .write_with_attrs = auto9_dp0_phy_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static const MemoryRegionOps auto9_dp0_pll_ops = {
    .read = auto9_dp0_pll_status,
    .write_with_attrs = auto9_dp0_pll_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};



enum { DP0_PLUG_STATUS = 3, DP0_PLUG_MASK = 4,
       DP0_SOURCE_LANES = 6, DP0_SOURCE_PCS = 7 };

/* Virtual 1560x700 @60 Hz preferred timing, not a captured OEM panel. */
static const uint8_t auto9_dp0_edid[128] = {
    0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00, 0x59, 0xa5, 0x04, 0x87,
    [16] = 1, 30, 1, 4, 0xa0, 35, 16, 120, 10,
    [38] = 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    0x6e, 0x1d, 0x18, 0xa0, 0x60, 0xbc, 0x1e, 0x20, 0x30, 0x20,
    0x35, 0, 0x5e, 0xa0, 0x10, 0, 0, 0x1e,
    0, 0, 0, 0xfc, 0, 'A', 'u', 't', 'o', '9', ' ', 'V', 'M', ' ', 'A', 'B', 'T', '\n',
    [93] = 0x10, [111] = 0x10, [127] = 0xf4,
};

static void auto9_dp0_hpd_update_irq(Auto9Dp0Reset *s)
{
    if (s->sink_enabled) {
        s->hpd_irq_level = !!(s->link[DP0_PLUG_STATUS].value &
                              s->link[DP0_PLUG_MASK].value);
        qemu_set_irq(s->sink_irq, s->hpd_irq_level);
    }
}

static void auto9_dp0_hpd_plug(void *opaque)
{
    Auto9Dp0Reset *s = opaque;
    if (s->pll_locked && !s->busy && !s->hpd_level) {
        s->hpd_level = 1;
        s->hpd_events++;
        s->link[DP0_PLUG_STATUS].value |= AUTO9_DP0_HPD_PLUG;
        auto9_dp0_hpd_update_irq(s);
    }
}

static void auto9_dp0_hpd_maybe(Auto9Dp0Reset *s)
{
    if (s->sink_enabled && s->pll_locked && !s->busy && !s->hpd_level &&
        (s->link[DP0_PLUG_MASK].value & AUTO9_DP0_HPD_PLUG) &&
        !timer_pending(s->hpd_timer)) {
        timer_mod(s->hpd_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  AUTO9_DP0_HPD_NS);
    }
}

static void auto9_dp0_sink_reset(Auto9Dp0Reset *s)
{
    if (!s->sink_enabled) {
        return;
    }
    timer_del(s->hpd_timer);
    timer_del(s->aux_timer);
    s->hpd_level = s->aux_busy = s->aux_monitor = s->aux_control = 0;
    s->aux_addr_only = s->aux_request = s->aux_command = s->link_phase = 0;
    s->link_trained = s->edid_offset = 0;
    s->dpcd_power = 1;
    s->cr_read = s->eq_read = false;
    memset(s->tx, 0, sizeof(s->tx));
    memset(s->rx, 0, sizeof(s->rx));
    memset(s->dpcd_config, 0, sizeof(s->dpcd_config));
    auto9_dp0_hpd_update_irq(s);
}

static unsigned auto9_dp0_lane_count(uint8_t value)
{
    unsigned n = value & 0x1f;
    return (n == 1 || n == 2 || n == 4) ? n : 0;
}

static uint8_t auto9_dp0_lane_status(Auto9Dp0Reset *s, unsigned first)
{
    unsigned n = auto9_dp0_lane_count(s->dpcd_config[1]);
    unsigned bits = s->link_phase == 2 ? 7 : 1;
    if (!s->link_phase || s->dpcd_power != 1 || s->link[DP0_SOURCE_LANES].value != n ||
        (!s->link_trained && ((s->link[DP0_SOURCE_PCS].value >> 4) & 7) != s->link_phase) ||
        qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) < s->link_ready_ns) {
        return 0;
    }
    if (n > first) {
        s->cr_read = true;
        s->eq_read |= bits == 7;
    }
    return (n > first ? bits : 0) | (n > first + 1 ? bits << 4 : 0);
}

static bool auto9_dp0_dpcd(Auto9Dp0Reset *s, bool write, unsigned addr, unsigned n)
{
    /* A local DP 1.2 sink, HBR, up to 4 lanes, no MST/HDCP. Read-only
     * status evolves only through the guest's actual training sequence. */
    static const uint8_t caps[16] = {0x12, 0x0a, 0x84, 0, 0, 0, 1, 0, 2};
    unsigned end = addr + n;
    if (addr == 0x600 && n == 1) {
        if (!write) {
            s->rx[0] = s->dpcd_power;
        } else if (s->tx[0] == 1 || s->tx[0] == 2) {
            s->dpcd_power = s->tx[0];
            if (s->dpcd_power == 2) {
                s->link_phase = s->link_trained = 0;
            }
        } else {
            return false;
        }
        return true;
    }
    if (!write && end <= 0x20) {
        for (unsigned i = 0; i < n; i++) {
            s->rx[i] = addr + i < sizeof(caps) ? caps[addr + i] : 0;
        }
        return true;
    }
    /* The original QNX reset worker queries DSC and FEC capabilities.
     * Successful reads of those defined capability bytes advertise neither. */
    if (!write && ((addr >= 0x60 && end <= 0x70) || (addr == 0x90 && n == 1))) {
        memset(s->rx, 0, n);
        return true;
    }
    if (!write && addr >= 0x200 && end <= 0x210) {
        for (unsigned i = 0; i < n; i++) {
            switch (addr + i) {
            case 0x200: s->rx[i] = 1; break;
            case 0x202: s->rx[i] = auto9_dp0_lane_status(s, 0); break;
            case 0x203: s->rx[i] = auto9_dp0_lane_status(s, 2); break;
            case 0x204: case 0x205:
                s->rx[i] = s->link_phase == 2 && s->dpcd_power == 1 &&
                           qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) >= s->link_ready_ns;
                break;
            default: s->rx[i] = 0; break;
            }
        }
        return true;
    }
    if (addr < 0x100 || end > 0x110) {
        return false;
    }
    if (!write) {
        memcpy(s->rx, s->dpcd_config + (addr - 0x100), n);
        return true;
    }
    if ((addr == 0x100 && s->tx[0] != 6 && s->tx[0] != 10) ||
        (addr <= 0x101 && end > 0x101 && !auto9_dp0_lane_count(s->tx[0x101 - addr]))) {
        return false;
    }
    if ((addr == 0x100 && s->tx[0] != s->dpcd_config[0]) ||
        (addr <= 0x101 && end > 0x101 && s->tx[0x101 - addr] != s->dpcd_config[1])) {
        s->link_phase = s->link_trained = 0;
    }
    memcpy(s->dpcd_config + (addr - 0x100), s->tx, n);
    if (addr <= 0x102 && end > 0x102) {
        uint8_t pattern = s->dpcd_config[2] & 3;
        if (pattern == 1 && s->dpcd_config[0] &&
            auto9_dp0_lane_count(s->dpcd_config[1]) && s->dpcd_power == 1) {
            s->link_phase = 1;
            s->link_trained = s->cr_read = s->eq_read = false;
            s->link_ready_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_DP0_TRAIN_NS;
        } else if (pattern == 2 && s->link_phase == 1 && s->cr_read) {
            s->link_phase = 2;
            s->link_ready_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_DP0_TRAIN_NS;
        } else if (!pattern && s->link_phase == 2 && s->eq_read) {
            s->link_trained = 1;
        } else if (pattern != 2 || s->link_phase != 2) {
            s->link_phase = s->link_trained = 0;
        }
    }
    return true;
}

static bool auto9_dp0_aux_i2c(Auto9Dp0Reset *s, unsigned cmd, unsigned addr, unsigned n)
{
    if (s->aux_addr_only) {
        return addr == 0x50 || addr == 0x30;
    }
    if (!(cmd & 1)) {
        if (addr == 0x50 && n == 1) {
            s->edid_offset = s->tx[0];
            return true;
        }
        return addr == 0x30 && n == 1 && s->tx[0] == 0;
    }
    if (addr != 0x50 || s->edid_offset + n > sizeof(auto9_dp0_edid)) {
        return false;
    }
    memcpy(s->rx, auto9_dp0_edid + s->edid_offset, n);
    s->edid_offset += n;
    return true;
}

static void auto9_dp0_aux_complete(void *opaque)
{
    Auto9Dp0Reset *s = opaque;
    unsigned cmd = s->aux_request >> 28, addr = (s->aux_request >> 8) & 0xfffff;
    unsigned n = (s->aux_request & 0x3f) + 1;
    bool ok = false;
    s->aux_busy = 0;
    s->aux_last_command = cmd;
    s->aux_last_address = addr;
    memset(s->rx, 0, sizeof(s->rx));
    if (s->hpd_level && !(s->aux_control & BIT(16)) && n <= sizeof(s->tx)) {
        if (cmd == 8 || cmd == 9) {
            s->aux_native_reads += cmd == 9;
            s->aux_native_writes += cmd == 8;
            ok = auto9_dp0_dpcd(s, cmd == 8, addr, n);
        } else if (cmd == 0 || cmd == 1 || cmd == 4 || cmd == 5) {
            s->aux_i2c_reads += cmd & 1;
            s->aux_i2c_writes += !(cmd & 1);
            ok = auto9_dp0_aux_i2c(s, cmd, addr, n);
        }
    }
    /* Original checks the command status nibble, not the receive length. */
    s->aux_monitor = ok ? (((cmd & 1) && !s->aux_addr_only) ? n << 24 : 0) : BIT(8);
    s->aux_replies += ok;
    s->aux_nacks += !ok;
}

static MemTxResult auto9_dp0_aux_read(void *opaque, hwaddr off, uint64_t *value,
                                     unsigned size, MemTxAttrs attrs)
{
    Auto9Dp0Reset *s = opaque;
    switch (off) {
    case 0: *value = s->aux_control; return MEMTX_OK;
    case 4: *value = s->aux_busy; return MEMTX_OK;
    case 8: case 0x1c: *value = 0; return MEMTX_OK;
    case 0xc: *value = s->aux_addr_only; return MEMTX_OK;
    case 0x10: *value = s->aux_request; return MEMTX_OK;
    case 0x14: *value = s->aux_command; return MEMTX_OK;
    case 0x18: *value = s->aux_monitor | (s->aux_busy ? BIT(2) : 0); return MEMTX_OK;
    }
    if (off >= 0x30 && off < 0x50) {
        *value = ldl_le_p(off < 0x40 ? s->tx + (off - 0x30) : s->rx + (off - 0x40));
        return MEMTX_OK;
    }
    s->aux_mmio_rejected++;
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_dp0_aux_write(void *opaque, hwaddr off, uint64_t value,
                                      unsigned size, MemTxAttrs attrs)
{
    Auto9Dp0Reset *s = opaque;
    if (s->aux_busy) {
        goto rejected;
    }
    switch (off) {
    case 0: s->aux_control = value & 0x13703; return MEMTX_OK;
    case 4:
        if (value & 1) {
            s->aux_busy = 1;
            s->aux_requests++;
            timer_mod(s->aux_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_DP0_AUX_NS);
        }
        return MEMTX_OK;
    case 8:
        if (value & 1) {
            memset(s->tx, 0, sizeof(s->tx));
            memset(s->rx, 0, sizeof(s->rx));
        }
        return MEMTX_OK;
    case 0xc: s->aux_addr_only = value & 1; return MEMTX_OK;
    case 0x10: s->aux_request = value & 0xffffff3f; return MEMTX_OK;
    case 0x14: s->aux_command = value & 0x17f; return MEMTX_OK;
    }
    if (off >= 0x30 && off < 0x40) {
        stl_le_p(s->tx + (off - 0x30), value);
        return MEMTX_OK;
    }
rejected:
    s->aux_mmio_rejected++;
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps auto9_dp0_aux_ops = {
    .read_with_attrs = auto9_dp0_aux_read,
    .write_with_attrs = auto9_dp0_aux_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static uint64_t auto9_dp0_link_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9Dp0LinkWord *word = opaque;
    word->reads++;
    return word->value | (word->offset == 0x28 && word->controller->hpd_level ?
                          AUTO9_DP0_HPD_LEVEL : 0);
}

static MemTxResult auto9_dp0_link_write(void *opaque, hwaddr off, uint64_t value,
                                       unsigned size, MemTxAttrs attrs)
{
    Auto9Dp0LinkWord *word = opaque;
    if (word->offset == 0x2000 && value) {
        return MEMTX_DECODE_ERROR;
    }
    word->writes++;
    if (word->w1c) {
        word->value &= ~(value & word->mask);
    } else {
        word->value = value & word->mask;
    }
    if (word->offset == 0x108 || word->offset == 0x10c) {
        auto9_dp0_hpd_update_irq(word->controller);
        auto9_dp0_hpd_maybe(word->controller);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_dp0_link_ops = {
    .read = auto9_dp0_link_read,
    .write_with_attrs = auto9_dp0_link_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static int auto9_dp0_sst_index(hwaddr off)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_dp0_sst_words); i++) {
        if (off + 0x28 == auto9_dp0_sst_words[i].off) {
            return i;
        }
    }
    return -1;
}

static uint32_t auto9_dp0_sst_value(const Auto9Dp0Reset *s, unsigned stream,
                                  hwaddr offset)
{
    int i = auto9_dp0_sst_index(offset - 0x28);
    assert(stream < G_N_ELEMENTS(s->sst) && i >= 0);
    return s->sst[stream].words[i];
}

static bool auto9_dp0_sst_running(const Auto9Dp0Reset *s, unsigned stream)
{
    if (!s || !s->link_trained || !s->hpd_level) {
        return false;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
        if (s->link[i].offset == 0x2010 + 0x10 * stream) {
            return (s->link[i].value & 1) &&
                   (auto9_dp0_sst_value(s, stream, 0x404) & 1);
        }
    }
    return false;
}

static MemTxResult auto9_dp0_sst_read(void *opaque, hwaddr off,
                                     uint64_t *value, unsigned size,
                                     MemTxAttrs attrs)
{
    Auto9Dp0Sst *sst = opaque;
    int i = auto9_dp0_sst_index(off);
    if (i < 0) {
        return MEMTX_DECODE_ERROR;
    }
    *value = sst->words[i];
    return MEMTX_OK;
}

static MemTxResult auto9_dp0_sst_write(void *opaque, hwaddr off,
                                      uint64_t value, unsigned size,
                                      MemTxAttrs attrs)
{
    Auto9Dp0Sst *sst = opaque;
    int i = auto9_dp0_sst_index(off);
    if (i < 0) {
        return MEMTX_DECODE_ERROR;
    }
    if (auto9_dp0_sst_words[i].w1c) {
        sst->words[i] &= ~(value & auto9_dp0_sst_words[i].mask);
    } else {
        if (value & ~(uint64_t)auto9_dp0_sst_words[i].mask) {
            return MEMTX_DECODE_ERROR;
        }
        sst->words[i] = value;
    }
    sst->writes++;
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_dp0_sst_ops = {
    .read_with_attrs = auto9_dp0_sst_read,
    .write_with_attrs = auto9_dp0_sst_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_dp0_reset_device(DeviceState *dev)
{
    Auto9Dp0Reset *s = AUTO9_DP0_RESET(dev);
    timer_del(s->timer);
    s->busy = s->reads = s->writes = s->requests = s->completions = 0;
    for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
        Auto9Dp0LinkWord *word = &s->link[i];
        word->value = word->reads = word->writes = 0;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->sst); i++) {
        memset(s->sst[i].words, 0, sizeof(s->sst[i].words));
        s->sst[i].writes = 0;
    }
    timer_del(s->pll_timer);
    s->phy_config_seen = s->phy_extra_seen = 0;
    s->phy_control = s->phy_mode = s->phy_ssc = s->phy_bias = s->phy_rate = 0;
    s->phy_reads = s->phy_writes = 0;
    s->phy_release_seen = s->phy_release_gap_ns = s->phy_release_config_count = 0;
    s->phy_start_ns = 0;
    s->phy_armed = false;
    memset(s->phy_lanes, 0, sizeof(s->phy_lanes));
    memset(s->phy_tune, 0, sizeof(s->phy_tune));
    s->phy_tune_writes = 0;
    s->pll_locked = s->pll_reads = s->pll_requests = s->pll_completions = 0;
    auto9_dp0_sink_reset(s);
    s->hpd_events = 0;
    s->aux_requests = s->aux_replies = s->aux_nacks = s->aux_mmio_rejected = 0;
    s->aux_native_reads = s->aux_native_writes = s->aux_i2c_reads = s->aux_i2c_writes = 0;
    s->aux_last_command = s->aux_last_address = 0;
}

static void auto9_dp0_reset_init(Object *obj)
{
    Auto9Dp0Reset *s = AUTO9_DP0_RESET(obj);
    /* Do not turn unknown adjacent LINK or PHY registers into zero reads. */
    memory_region_init_io(&s->reg, obj, &auto9_dp0_reset_ops, s,
                          "auto9-dp0-sw-reset", 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->reg);
    memory_region_init_io(&s->phy, obj, &auto9_dp0_phy_ops, s,
                          "auto9-dp0-phy", 0xa000);
    memory_region_init_io(&s->pll_status, obj, &auto9_dp0_pll_ops, s,
                          "auto9-dp0-pll-status", 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->phy);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->pll_status);
    /* Discrete OEM words, not an open LINK window. */
    for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
        Auto9Dp0LinkWord *word = &s->link[i];
        const char *name = auto9_dp0_link_words[i].name;
        g_autofree char *value = g_strdup_printf("%s-value", name);
        g_autofree char *reads = g_strdup_printf("%s-reads", name);
        g_autofree char *writes = g_strdup_printf("%s-writes", name);

        word->mask = auto9_dp0_link_words[i].mask;
        word->w1c = auto9_dp0_link_words[i].w1c;
        word->controller = s;
        word->offset = auto9_dp0_link_words[i].offset;
        memory_region_init_io(&word->region, obj, &auto9_dp0_link_ops,
                              word, name, 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &word->region);
        object_property_add_uint32_ptr(obj, value, &word->value, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, reads, &word->reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, writes, &word->writes, OBJ_PROP_FLAG_READ);
    }
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dp0_reset_done, s);
    s->pll_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dp0_pll_locked, s);
    s->hpd_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dp0_hpd_plug, s);
    s->aux_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dp0_aux_complete, s);
    memory_region_init_io(&s->aux, obj, &auto9_dp0_aux_ops, s, "auto9-dp0-aux", 0x50);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->aux);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->sst); i++) {
        Auto9Dp0Sst *sst = &s->sst[i];
        g_autofree char *writes = g_strdup_printf("sst%u-timing-writes", i);
        g_autofree char *t420 = g_strdup_printf("sst%u-timing-420", i);
        g_autofree char *t430 = g_strdup_printf("sst%u-timing-430", i);
        g_autofree char *video = g_strdup_printf("sst%u-video-enable", i);
        memory_region_init_io(&sst->region, obj, &auto9_dp0_sst_ops, sst,
                              "auto9-dp0-sst-timing", 0x42c + 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &sst->region);
        object_property_add_uint32_ptr(obj, writes, &sst->writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, t420,
            &sst->words[auto9_dp0_sst_index(0x420 - 0x28)], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, t430,
            &sst->words[auto9_dp0_sst_index(0x430 - 0x28)], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, video,
            &sst->words[auto9_dp0_sst_index(0x404 - 0x28)], OBJ_PROP_FLAG_READ);
    }
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->sink_irq);
    object_property_add_uint32_ptr(obj, "hpd-level", &s->hpd_level, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "hpd-events", &s->hpd_events, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "hpd-irq-level", &s->hpd_irq_level, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-requests", &s->aux_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-replies", &s->aux_replies, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-nacks", &s->aux_nacks, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-mmio-rejected", &s->aux_mmio_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-native-reads", &s->aux_native_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-native-writes", &s->aux_native_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-i2c-reads", &s->aux_i2c_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-i2c-writes", &s->aux_i2c_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-last-command", &s->aux_last_command, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "aux-last-address", &s->aux_last_address, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-phase", &s->link_phase, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "link-trained", &s->link_trained, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-control", &s->phy_control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-release-seen", &s->phy_release_seen, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-release-gap-ns", &s->phy_release_gap_ns, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-release-config-count", &s->phy_release_config_count, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-mode", &s->phy_mode, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-extra-seen", &s->phy_extra_seen, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-rate", &s->phy_rate, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-reads", &s->phy_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-writes", &s->phy_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "phy-tune-writes", &s->phy_tune_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pll-reads", &s->pll_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pll-locked", &s->pll_locked, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pll-requests", &s->pll_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pll-completions", &s->pll_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "busy", &s->busy, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "requests", &s->requests,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "completions", &s->completions,
                                   OBJ_PROP_FLAG_READ);
}

static void auto9_dp0_reset_finalize(Object *obj)
{
    Auto9Dp0Reset *s = AUTO9_DP0_RESET(obj);
    timer_free(s->timer);
    timer_free(s->pll_timer);
    timer_free(s->hpd_timer);
    timer_free(s->aux_timer);
}

static void auto9_dp0_reset_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_dp0_reset_device);
}

static const TypeInfo auto9_dp0_reset_type = {
    .name = TYPE_AUTO9_DP0_RESET,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9Dp0Reset),
    .instance_init = auto9_dp0_reset_init,
    .instance_finalize = auto9_dp0_reset_finalize,
    .class_init = auto9_dp0_reset_class,
};

static void auto9_dp0_reset_register(void)
{
    type_register_static(&auto9_dp0_reset_type);
}
type_init(auto9_dp0_reset_register)

static void auto9_dp0_reset_create(VirtMachineState *vms)
{
    if (vms->exynos_dp0_reset) {
        DeviceState *dev = qdev_new(TYPE_AUTO9_DP0_RESET);
        AUTO9_DP0_RESET(dev)->sink_enabled = vms->exynos_dp0_aux_sink == 1;
        object_property_add_child(OBJECT(vms), "exynos-dp0-reset", OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x18a80004);
        if (vms->exynos_dp0_phy) {
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, 0x18aa0000);
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2, 0x18a8002c);
        }
        if (vms->exynos_dp0_link_config) {
            for (unsigned i = 0; i < G_N_ELEMENTS(auto9_dp0_link_words); i++) {
                if ((i < DP0_SOURCE_LANES || vms->exynos_dp0_aux_sink) &&
                    (!auto9_dp0_link_words[i].plug || vms->exynos_dp0_irq)) {
                    sysbus_mmio_map(SYS_BUS_DEVICE(dev), i + 3,
                                    0x18a80000 + auto9_dp0_link_words[i].offset);
                }
            }
        }
        if (vms->exynos_dp0_aux_sink) {
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), G_N_ELEMENTS(auto9_dp0_link_words) + 3,
                            0x18a81000);
            for (unsigned i = 0; i < G_N_ELEMENTS(AUTO9_DP0_RESET(dev)->sst); i++) {
                sysbus_mmio_map(SYS_BUS_DEVICE(dev), G_N_ELEMENTS(auto9_dp0_link_words) + 4 + i,
                                0x18a85028 + 0x1000 * i);
            }
            sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, 111));
        }
    }
}
