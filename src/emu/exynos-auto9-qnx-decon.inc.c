/* SPDX-License-Identifier: GPL-2.0-or-later
 * VM2 DPUM DECON0/1 and optional DPUS0 DECON0 configuration.
 * DSI/DP0 routing, timing and a bounded original single-plane pixel scan.
 * Composition, scaling and silicon color CRC are not implemented.
 */
#define TYPE_AUTO9_QNX_DECON "exynos-auto9.qnx-decon"
#define DECON_AUTO9_RESET_NS 2000
#define DECON_AUTO9_UPDATE_NS 10000
#define DECON_AUTO9_SRESET BIT(28)
#define DECON_AUTO9_UPDATE_GLOBAL BIT(31)
#define DECON_AUTO9_FRAME_START BIT(12)
#define DECON_AUTO9_FRAME_END BIT(13)
#define DECON_AUTO9_GLOBAL_IRQ BIT(0)
#define DECON_AUTO9_RUNNING BIT(4)
#define DECON_AUTO9_IDLE BIT(5)
#define DECON_AUTO9_CTRL_MASK (BIT(24) | BIT(20) | BIT(9) | BIT(8) | 3U)
#define DECON_AUTO9_DP_PIPE 2
#define DECON_AUTO9_DP_STREAM 2
#define DECON_AUTO9_IC_PIPE 1
#define DECON_AUTO9_IC_SELECTOR 2
#define DECON_AUTO9_IC_IDMA 0x18c67000
#define DECON_AUTO9_SCAN_COMPARE_FRAMES 32
OBJECT_DECLARE_SIMPLE_TYPE(Auto9QnxDecon, AUTO9_QNX_DECON)

static const struct {
    uint16_t offset;
    uint32_t mask;
} auto9_decon_words[] = {
    { 0x000, DECON_AUTO9_CTRL_MASK }, /* Global control (status computed). */
    { 0x030, 0x01111111 }, /* SRAM sharing */
    { 0x040, 0x00303011 }, /* Interrupt enables, global + event bits. */
    { 0x044, 0x00000110 }, /* Extra interrupt enables */
    { 0x070, 0x03ff311d }, /* Trigger control */
    { 0x120, 0x3fff3fff }, /* Output FIFO size */
    { 0x12c, 0x00000007 }, /* Output FIFO threshold */
    { 0x130, 0x00000070 }, /* Pixel order */
    { 0x200, 0x3fff3fff }, /* Background image size */
    { 0x230, 0x000070ff }, /* Output interface/DSC/enhance */
};

typedef struct Auto9DeconPipe {
    MemoryRegion main, shadow, global;
    Auto9QnxDecon *parent;
    QEMUTimer *timer, *frame_timer;
    qemu_irq frame_irq, frame_end_irq;
    void (*wod_boundary)(void *opaque, unsigned pipe, bool reset);
    void *wod_opaque;
    unsigned index;
    uint32_t staged[G_N_ELEMENTS(auto9_decon_words)];
    uint32_t active[G_N_ELEMENTS(auto9_decon_words)];
    uint32_t reads, writes, rejected, reset_pending, updates, rejected_updates;
    uint32_t update_pending, running, active_size, active_dsi, active_dp;
    uint32_t crc_control, crc_writes, crc_empty_reads, frame_count_reads;
    uint32_t timing_ns, frame_pending, frame_ticks, frame_irqs, frame_acks, frame_level;
    uint32_t scanned_frames, scanned_source, scanned_nonzero, frame_end_irqs, frame_end_acks, frame_end_level;
    uint32_t direct_scan_frames, copied_scan_frames;
    uint64_t direct_scan_ns, copied_scan_ns;
} Auto9DeconPipe;

static const unsigned auto9_decon_windows[] = { 0, 2, 3, 4 };
static const unsigned auto9_dpus_windows[] = { 0, 7 };

typedef struct Auto9DeconWin Auto9DeconWin;
typedef struct Auto9DeconWinCtl {
    MemoryRegion region;
    QEMUTimer *timer;
    Auto9DeconWin *win;
    unsigned pipe;
    uint32_t control, update_control, pending, reads;
} Auto9DeconWinCtl;

struct Auto9DeconWin {
    MemoryRegion regs;
    Auto9QnxDecon *parent;
    Auto9DeconWinCtl ctl[2];
    unsigned first_pipe, control_count;
    uint32_t staged[8], active[8], active_owner, active_control, commits, bad_updates;
    Auto9QnxDppSource source;
};

struct Auto9QnxDecon {
    SysBusDevice parent_obj;
    MemoryRegion con, dp_con;
    Auto9QnxDsi *dsi;
    Auto9Dp0Reset *dp0;
    Auto9DppIrq *dpp;
    Auto9DeconPipe pipe[3];
    Auto9DeconWin win[G_N_ELEMENTS(auto9_decon_windows)];
    Auto9DeconWin dpwin[G_N_ELEMENTS(auto9_dpus_windows)];
    uint32_t routes[2], route_written, route_writes;
    uint32_t dp_route, dp_route_writes;
};

static int auto9_decon_word_index(hwaddr off)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_decon_words); i++) {
        if (auto9_decon_words[i].offset == off) {
            return i;
        }
    }
    return -1;
}

static MemTxResult auto9_decon_reject(Auto9DeconPipe *p, hwaddr off,
                                      const char *op)
{
    p->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 DECON%u unsupported %s 0x%" HWADDR_PRIx "\n",
                  p->index, op, off);
    return MEMTX_DECODE_ERROR;
}

static int auto9_decon_selected_dsi(const Auto9DeconPipe *p)
{
    const Auto9QnxDecon *s = p->parent;
    unsigned path = p->staged[auto9_decon_word_index(0x230)] & 0xf;
    for (unsigned i = 0; i < 2; i++) {
        /* Exact original QNX routing encodings for its two DSI paths. */
        unsigned selector = p->index ? 2 : i;
        unsigned interface = p->index ? 1 : (i + 1);
        if (path == interface && (s->route_written & BIT(i)) &&
            s->routes[i] == selector && s->dsi->link[i].peripheral_on &&
            auto9_dsim_hs_ready(&s->dsi->link[i])) {
            return i;
        }
    }
    return -1;
}

static bool auto9_decon_dp_ready(const Auto9DeconPipe *p)
{
    const Auto9QnxDecon *s = p->parent;
    return s->dp_route_writes && s->dp_route == 0 &&
           auto9_dp0_sst_running(s->dp0, DECON_AUTO9_DP_STREAM);
}

static void auto9_decon_frame_irq_update(Auto9DeconPipe *p)
{
    uint32_t enables = p->staged[auto9_decon_word_index(0x40)];
    bool running = p->running && !p->reset_pending && (enables & DECON_AUTO9_GLOBAL_IRQ) &&
                   (!p->active_dp || auto9_decon_dp_ready(p));
    bool level = running && (p->frame_pending & enables & DECON_AUTO9_FRAME_START);
    bool end = running && (p->frame_pending & enables & DECON_AUTO9_FRAME_END);
    if (level && !p->frame_level) {
        p->frame_irqs++;
    }
    p->frame_level = level;
    qemu_set_irq(p->frame_irq, level);
    if (end && !p->frame_end_level) {
        p->frame_end_irqs++;
    }
    p->frame_end_level = end;
    qemu_set_irq(p->frame_end_irq, end);
}

/* Read the full mapped plane; alpha alone is not visible color. Keeping the
 * mask outside the loop lets the compiler reduce multiple pixels at once. */
static bool auto9_decon_mapped_rgb(const uint8_t *pixels, unsigned bytes)
{
    uint64_t bits = 0;
    unsigned off = 0;
    for (; bytes - off >= 8; off += 8) {
        bits |= ldq_le_p(pixels + off);
    }
    if (off < bytes) {
        bits |= ldl_le_p(pixels + off);
    }
    return (bits & UINT64_C(0x00ffffff00ffffff)) != 0;
}

/* Consume actual VM2 pixels only for the original direct linear plane. A
 * timing event, missing source, blend, or MMIO alias is not a scanned frame. */
static bool auto9_decon_scan_plane(Auto9DeconPipe *p)
{
    Auto9QnxDecon *s = p->parent;
    const Auto9DeconWin *win = p->active_dp ? s->dpwin : s->win;
    const unsigned n = p->active_dp ? G_N_ELEMENTS(s->dpwin) : G_N_ELEMENTS(s->win);
    const Auto9DeconWin *active = NULL;
    unsigned width = p->active_size & 0x3fff, height = p->active_size >> 16;
    uint8_t data[4096];
    bool nonzero = false;
    bool direct = false;

    for (unsigned i = 0; i < n; i++) {
        if (win[i].active_owner == p->index) {
            if (active) {
                return false;
            }
            active = &win[i];
        }
    }
    if (!width || width > 1560 || !height || height > 780 || !active ||
        (active->active_control & 3) != 1 || active->active[3] ||
        active->active[4] != ((height - 1) << 16 | (width - 1))) {
        return false;
    }
    const Auto9QnxDppSource *src = &active->source;
    uint64_t address = src->address;
    unsigned bytes = width * height * 4;
    if (!src->writes || src->control || src->crop_offset ||
        src->frame_size != p->active_size || src->crop_size != p->active_size ||
        src->output_size != p->active_size || (address & 4095) ||
        address < UINT64_C(0x90000000) || address + bytes > UINT64_C(0xa0000000)) {
        return false;
    }
    MemoryRegionSection sec = memory_region_find(get_system_memory(), address, bytes);
    if (!sec.mr) {
        return false;
    }
    bool ram = memory_region_is_ram(sec.mr) && !memory_region_is_ram_device(sec.mr) &&
               sec.offset_within_address_space == address && int128_eq(sec.size, int128_make64(bytes));
    memory_region_unref(sec.mr);
    if (!ram) {
        return false;
    }
    int64_t begin = qemu_clock_get_ns(QEMU_CLOCK_REALTIME);
    /* Compare alternate initial frames with the existing read path. After
     * this bounded sample, map once rather than translating/copying each page.
     * A partial map keeps the checked copy path, not a short/fake scan. */
    if (p->scanned_frames >= DECON_AUTO9_SCAN_COMPARE_FRAMES ||
        (p->scanned_frames & 1)) {
        hwaddr length = bytes;
        uint8_t *mapped = address_space_map(&address_space_memory, address,
                                           &length, false, MEMTXATTRS_UNSPECIFIED);
        if (mapped) {
            direct = length == bytes;
            if (direct) {
                nonzero = auto9_decon_mapped_rgb(mapped, bytes);
            }
            address_space_unmap(&address_space_memory, mapped, length,
                                false, direct ? bytes : 0);
        }
    }
    if (!direct) {
        for (unsigned off = 0; off < bytes; off += sizeof(data)) {
            unsigned len = MIN(sizeof(data), bytes - off);
            if (address_space_read(&address_space_memory, address + off,
                                   MEMTXATTRS_UNSPECIFIED, data, len) != MEMTX_OK) {
                return false;
            }
            for (unsigned j = 0; j < len && !nonzero; j += 4) {
                nonzero = (ldl_le_p(data + j) & 0x00ffffff) != 0;
            }
        }
    }
    uint64_t elapsed = qemu_clock_get_ns(QEMU_CLOCK_REALTIME) - begin;
    if (direct) {
        p->direct_scan_frames++;
        p->direct_scan_ns += elapsed;
    } else {
        p->copied_scan_frames++;
        p->copied_scan_ns += elapsed;
    }
    p->scanned_source = address;
    p->scanned_nonzero = nonzero;
    p->scanned_frames++;
    return true;
}

static void auto9_decon_frame_tick(void *opaque)
{
    Auto9DeconPipe *p = opaque;
    if (!p->running || p->reset_pending || !p->timing_ns) {
        return;
    }
    const unsigned i = p->active_dsi;
    const Auto9QnxDecon *s = p->parent;
    if ((p->active_dp && auto9_decon_dp_ready(p)) ||
        (i < 2 && s->routes[i] == (p->index ? 2 : i) &&
        s->dsi->link[i].peripheral_on &&
        auto9_dsim_hs_ready(&s->dsi->link[i]))) {
        p->frame_ticks++;
        /* Latch client windows before the VM2 frame IRQ and its vDPU VSync. */
        if (p->wod_boundary) {
            p->wod_boundary(p->wod_opaque, p->index, false);
        }
        if (auto9_decon_scan_plane(p)) {
            p->frame_pending |= DECON_AUTO9_FRAME_END;
        }
        p->frame_pending |= DECON_AUTO9_FRAME_START;
        auto9_decon_frame_irq_update(p);
    } else if (p->active_dp) {
        p->frame_pending = 0;
        auto9_decon_frame_irq_update(p);
    }
    timer_mod(p->frame_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + p->timing_ns);
}

static unsigned auto9_decon_mode_period(const Auto9DeconPipe *p)
{
    if (p->active_dp) {
        const Auto9Dp0Reset *dp = p->parent->dp0;
        unsigned stream = DECON_AUTO9_DP_STREAM;
        uint32_t ht = auto9_dp0_sst_value(dp, stream, 0x414);
        uint32_t vt = auto9_dp0_sst_value(dp, stream, 0x418);
        uint32_t mvid = auto9_dp0_sst_value(dp, stream, 0x44);
        /* Original dp_reg_set_video_clock divides its pixel clock by 2000.
         * Only time the selected 1560x700 mode with plausible programmed totals. */
        if (p->active_size == ((700u << 16) | 1560u) && mvid == 37281 &&
            ht >= 1560 && ht <= 2200 && vt >= 700 && vt <= 950) {
            uint64_t ns = (uint64_t)ht * vt * 1000000000 / (2000 * mvid);
            if (ns >= 15384615 && ns <= 18181819) {
                return ns;
            }
        }
        return 0;
    }
    /* Same two modes and routes selected by the OEM WFD display stack.
     * The observed refresh rates are 60.000183 and 59.999619 Hz.
     * Timing only: no assumption about pixel data or another mode. */
    if (!p->running || p->active_dsi != 1 - p->index) {
        return 0;
    }
    if (p->index == 0 && p->active_size == ((780u << 16) | 1120u)) {
        return 16666616;
    }
    if (p->index == 1 && p->active_size == ((400u << 16) | 800u)) {
        return 16666773;
    }
    return 0;
}

static void auto9_decon_clear(Auto9DeconPipe *p)
{
    timer_del(p->timer);
    if (p->wod_boundary) {
        p->wod_boundary(p->wod_opaque, p->index, true);
    }
    memset(p->staged, 0, sizeof(p->staged));
    memset(p->active, 0, sizeof(p->active));
    p->reset_pending = p->update_pending = p->running = p->active_size = 0;
    p->active_dsi = UINT32_MAX;
    p->active_dp = 0;
    p->crc_control = 0;
    p->scanned_frames = p->scanned_source = p->scanned_nonzero = 0;
    p->direct_scan_frames = p->copied_scan_frames = 0;
    p->direct_scan_ns = p->copied_scan_ns = 0;
    timer_del(p->frame_timer);
    p->timing_ns = p->frame_pending = 0;
    auto9_decon_frame_irq_update(p);
}

static void auto9_decon_timer(void *opaque)
{
    Auto9DeconPipe *p = opaque;
    if (p->reset_pending) {
        auto9_decon_clear(p);
        return;
    }
    uint32_t size = p->staged[auto9_decon_word_index(0x120)];
    bool start = (p->staged[0] & 3) == 3;
    bool dp = p->index == DECON_AUTO9_DP_PIPE &&
              (p->staged[auto9_decon_word_index(0x230)] & 0xf) == 8 &&
              auto9_decon_dp_ready(p);
    int dsi = p->index == DECON_AUTO9_DP_PIPE ? -1 : auto9_decon_selected_dsi(p);

    p->update_pending = 0;
    if (start && ((!dp && dsi < 0) || !(size >> 16) || !(size & 0x3fff))) {
        /* Refuse the mode transition; do not manufacture an active output. */
        p->rejected_updates++;
        return;
    }
    memcpy(p->active, p->staged, sizeof(p->active));
    p->running = start;
    p->active_size = start ? size : 0;
    p->active_dsi = start && !dp ? dsi : UINT32_MAX;
    p->active_dp = start && dp;
    unsigned period = auto9_decon_mode_period(p);
    if (period != p->timing_ns) {
        p->timing_ns = period;
        p->frame_pending = 0;
        timer_del(p->frame_timer);
        if (period) {
            timer_mod(p->frame_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + period);
        }
        auto9_decon_frame_irq_update(p);
    }
    p->updates++;
}

static MemTxResult auto9_decon_main_read(void *opaque, hwaddr off,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    Auto9DeconPipe *p = opaque;
    int i = auto9_decon_word_index(off);
    if (off == 0x290) {
        *value = p->crc_control; /* No checksum data without a pixel source. */
    } else if (off == 0x280 || off == 0x284 || off == 0x288 || off == 0x28c) {
        /* OEM selects a color component via CRC control. The virtual result
         * latches remain empty until an actual pixel pipeline feeds them;
         * timing IRQs and geometry commits alone never produce a checksum. */
        p->crc_empty_reads++;
        *value = 0;
    } else if (off == 0x2a0) {
        p->frame_count_reads++;
        *value = p->scanned_frames;
    } else if (off == 0x4c || off == 0x50) {
        *value = off == 0x4c ? p->frame_pending : 0;
    } else if (i >= 0) {
        *value = p->staged[i];
        if (off == 0) {
            *value |= p->running ? DECON_AUTO9_RUNNING : DECON_AUTO9_IDLE;
            *value |= p->reset_pending ? DECON_AUTO9_SRESET : 0;
        }
    } else {
        return auto9_decon_reject(p, off, "main read");
    }
    p->reads++;
    return MEMTX_OK;
}

static MemTxResult auto9_decon_main_write(void *opaque, hwaddr off,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    Auto9DeconPipe *p = opaque;
    int i = auto9_decon_word_index(off);
    if (off == 0x290) {
        if (value & ~UINT64_C(0x30001)) {
            return auto9_decon_reject(p, off, "CRC control write");
        }
        p->crc_control = value;
        p->crc_writes++;
    } else if (off == 0x4c || off == 0x50) {
        uint32_t valid = off == 0x4c ? 0x00303010 : 0x110;
        if (value & ~((uint64_t)valid)) {
            return auto9_decon_reject(p, off, "pending write");
        }
        if (off == 0x4c && (p->frame_pending & value & DECON_AUTO9_FRAME_START)) {
            p->frame_pending &= ~DECON_AUTO9_FRAME_START;
            p->frame_acks++;
            auto9_decon_frame_irq_update(p);
        }
        if (off == 0x4c && (p->frame_pending & value & DECON_AUTO9_FRAME_END)) {
            p->frame_pending &= ~DECON_AUTO9_FRAME_END;
            p->frame_end_acks++;
            auto9_decon_frame_irq_update(p);
        }
    } else if (i >= 0) {
        uint64_t valid = auto9_decon_words[i].mask;
        if (off == 0) {
            valid |= DECON_AUTO9_SRESET | DECON_AUTO9_RUNNING | DECON_AUTO9_IDLE;
        }
        if (value & ~valid) {
            return auto9_decon_reject(p, off, "main value");
        }
        if (off == 0 && (value & DECON_AUTO9_SRESET)) {
            p->reset_pending = 1;
            timer_mod(p->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      DECON_AUTO9_RESET_NS);
        }
        p->staged[i] = value & auto9_decon_words[i].mask;
        if (off == 0x40 || (off == 0 && p->reset_pending)) {
            auto9_decon_frame_irq_update(p);
        }
    } else {
        return auto9_decon_reject(p, off, "main write");
    }
    p->writes++;
    return MEMTX_OK;
}

static MemTxResult auto9_decon_shadow_read(void *opaque, hwaddr off,
                                          uint64_t *value, unsigned size,
                                          MemTxAttrs attrs)
{
    Auto9DeconPipe *p = opaque;
    int i = auto9_decon_word_index(off);
    if (i < 0) {
        return auto9_decon_reject(p, off, "shadow read");
    }
    *value = p->active[i];
    return MEMTX_OK;
}

static MemTxResult auto9_decon_shadow_write(void *opaque, hwaddr off,
                                           uint64_t value, unsigned size,
                                           MemTxAttrs attrs)
{
    return auto9_decon_reject(opaque, off, "shadow write");
}

static MemTxResult auto9_decon_global_read(void *opaque, hwaddr off,
                                          uint64_t *value, unsigned size,
                                          MemTxAttrs attrs)
{
    Auto9DeconPipe *p = opaque;
    if (off != 0) {
        return auto9_decon_reject(p, off, "global read");
    }
    *value = p->update_pending;
    return MEMTX_OK;
}

static MemTxResult auto9_decon_global_write(void *opaque, hwaddr off,
                                           uint64_t value, unsigned size,
                                           MemTxAttrs attrs)
{
    Auto9DeconPipe *p = opaque;
    if (off != 0 || (value & ~((uint64_t)DECON_AUTO9_UPDATE_GLOBAL))) {
        return auto9_decon_reject(p, off, "global/window update write");
    }
    if (value && !p->reset_pending && !p->update_pending) {
        p->update_pending = value;
        timer_mod(p->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  DECON_AUTO9_UPDATE_NS);
    }
    return MEMTX_OK;
}

static MemTxResult auto9_decon_con_read(void *opaque, hwaddr off,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    Auto9QnxDecon *s = opaque;
    if (off != 0 && off != 0x1000) {
        return auto9_decon_reject(&s->pipe[0], off, "connection read");
    }
    *value = s->routes[off >> 12];
    return MEMTX_OK;
}

static MemTxResult auto9_decon_con_write(void *opaque, hwaddr off,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    Auto9QnxDecon *s = opaque;
    if ((off != 0 && off != 0x1000) || value > 2) {
        return auto9_decon_reject(&s->pipe[0], off, "connection write");
    }
    s->routes[off >> 12] = value;
    s->route_written |= BIT(off >> 12);
    s->route_writes++;
    return MEMTX_OK;
}

static MemTxResult auto9_decon_dp_con_read(void *opaque, hwaddr off,
                                          uint64_t *value, unsigned size,
                                          MemTxAttrs attrs)
{
    Auto9QnxDecon *s = opaque;
    *value = s->dp_route;
    return MEMTX_OK;
}

static MemTxResult auto9_decon_dp_con_write(void *opaque, hwaddr off,
                                           uint64_t value, unsigned size,
                                           MemTxAttrs attrs)
{
    Auto9QnxDecon *s = opaque;
    if (value & ~UINT64_C(0x77)) {
        return auto9_decon_reject(&s->pipe[DECON_AUTO9_DP_PIPE], off,
                                  "DP connection write");
    }
    s->dp_route = value;
    s->dp_route_writes++;
    return MEMTX_OK;
}

static const uint32_t auto9_decon_win_mask[] = {
    [1] = 0xffff0f03, [2] = 0x0f0f0f0f,
    [3] = 0x3fff3fff, [4] = 0x3fff3fff,
    [5] = 0x00ff03ff, [6] = 0x03ff03ff, [7] = 0x00003fff,
};

static hwaddr auto9_decon_window_idma(unsigned pipe, unsigned selector)
{
    /* WINCON names the original WFD channel, not a physical register stride.
     * VM2's pipeline 100 programmed the bank at 0x18c67000, including
     * the exact buffer address from the original Screen allocator. */
    return pipe == DECON_AUTO9_IC_PIPE && selector == DECON_AUTO9_IC_SELECTOR ?
        DECON_AUTO9_IC_IDMA : 0;
}

static void auto9_decon_win_update(void *opaque)
{
    Auto9DeconWinCtl *c = opaque;
    Auto9DeconWin *w = c->win;
    Auto9DeconPipe *p = &w->parent->pipe[c->pipe];
    bool enable = c->control & 1;

    c->pending = 0;
    if (enable) {
        uint32_t start = w->staged[3], end = w->staged[4];
        uint32_t sx = start & 0x3fff, sy = start >> 16;
        uint32_t ex = end & 0x3fff, ey = end >> 16;
        if (!p->running || (w->active_owner != UINT32_MAX &&
                            w->active_owner != c->pipe) ||
            sx > ex || sy > ey || ex >= (p->active_size & 0x3fff) ||
            ey >= (p->active_size >> 16)) {
            w->bad_updates++;
            return;
        }
        w->active_owner = c->pipe;
    } else if (w->active_owner == c->pipe) {
        w->active_owner = UINT32_MAX;
    } else if (w->active_owner != UINT32_MAX) {
        /* A disabled update through the other pipe cannot steal its window. */
        w->commits++;
        return;
    }
    memcpy(w->active, w->staged, sizeof(w->active));
    w->active_control = c->control;
    unsigned channel = (c->control >> 4) & 0xf;
    hwaddr idma = auto9_decon_window_idma(c->pipe, channel);
    /* A color-map selection or an unobserved channel is not a DMA source. */
    w->source = enable && !(c->control & 2) && idma ?
        auto9_qnx_dpp_source(w->parent->dpp, idma) :
        (Auto9QnxDppSource) { 0 };
    w->commits++;
}

static MemTxResult auto9_decon_win_read(void *opaque, hwaddr off,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    Auto9DeconWin *w = opaque;
    hwaddr word = off & 0x7ff;
    if (word < sizeof(w->staged) && auto9_decon_win_mask[word / 4]) {
        *value = (off & 0x800) ? w->active[word / 4] : w->staged[word / 4];
        return MEMTX_OK;
    }
    return auto9_decon_reject(&w->parent->pipe[w->first_pipe], off, "window read");
}

static MemTxResult auto9_decon_win_write(void *opaque, hwaddr off,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    Auto9DeconWin *w = opaque;
    if (off >= sizeof(w->staged) || !auto9_decon_win_mask[off / 4] ||
        (value & ~((uint64_t)auto9_decon_win_mask[off / 4]))) {
        return auto9_decon_reject(&w->parent->pipe[w->first_pipe], off, "window write");
    }
    w->staged[off / 4] = value;
    return MEMTX_OK;
}

static MemTxResult auto9_decon_win_ctl_read(void *opaque, hwaddr off,
                                           uint64_t *value, unsigned size,
                                           MemTxAttrs attrs)
{
    Auto9DeconWinCtl *c = opaque;
    c->reads++;
    switch (off) {
    case 0: *value = c->control; return MEMTX_OK;
    case 0x10: *value = c->update_control; return MEMTX_OK;
    case 0x14: *value = c->pending; return MEMTX_OK;
    }
    return auto9_decon_reject(&c->win->parent->pipe[c->pipe], off,
                             "window ctl read");
}

static MemTxResult auto9_decon_win_ctl_write(void *opaque, hwaddr off,
                                            uint64_t value, unsigned size,
                                            MemTxAttrs attrs)
{
    Auto9DeconWinCtl *c = opaque;
    switch (off) {
    case 0:
        if (!(value & ~UINT64_C(0xf3))) {
            c->control = value;
            return MEMTX_OK;
        }
        break;
    case 0x10:
        if (value <= 3) {
            c->update_control = value;
            return MEMTX_OK;
        }
        break;
    case 0x14:
        if (value <= 1) {
            if (!value) {
                timer_del(c->timer);
                c->pending = 0;
            } else if (!c->pending && !(c->update_control & 2)) {
                c->pending = 1;
                timer_mod(c->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                          DECON_AUTO9_UPDATE_NS);
            }
            return MEMTX_OK;
        }
        break;
    }
    return auto9_decon_reject(&c->win->parent->pipe[c->pipe], off,
                             "window ctl write");
}

#define AUTO9_DECON_OPS(NAME, READ, WRITE)                         \
    static const MemoryRegionOps NAME = {                         \
        .read_with_attrs = READ, .write_with_attrs = WRITE,         \
        .endianness = DEVICE_LITTLE_ENDIAN,                        \
        .valid = { .min_access_size = 4, .max_access_size = 4 },     \
        .impl = { .min_access_size = 4, .max_access_size = 4 },      \
    }
AUTO9_DECON_OPS(auto9_decon_main_ops, auto9_decon_main_read, auto9_decon_main_write);
AUTO9_DECON_OPS(auto9_decon_shadow_ops, auto9_decon_shadow_read, auto9_decon_shadow_write);
AUTO9_DECON_OPS(auto9_decon_global_ops, auto9_decon_global_read, auto9_decon_global_write);
AUTO9_DECON_OPS(auto9_decon_con_ops, auto9_decon_con_read, auto9_decon_con_write);
AUTO9_DECON_OPS(auto9_decon_win_ops, auto9_decon_win_read, auto9_decon_win_write);
AUTO9_DECON_OPS(auto9_decon_win_ctl_ops, auto9_decon_win_ctl_read, auto9_decon_win_ctl_write);
AUTO9_DECON_OPS(auto9_decon_dp_con_ops, auto9_decon_dp_con_read, auto9_decon_dp_con_write);
#undef AUTO9_DECON_OPS

static void auto9_qnx_decon_reset_window(Auto9DeconWin *w)
{
    memset(w->staged, 0, sizeof(w->staged));
    memset(w->active, 0, sizeof(w->active));
    w->active_owner = UINT32_MAX;
    w->active_control = 0;
    w->source = (Auto9QnxDppSource) { 0 };
    w->commits = w->bad_updates = 0;
    for (unsigned j = 0; j < w->control_count; j++) {
        Auto9DeconWinCtl *c = &w->ctl[j];
        timer_del(c->timer);
        c->control = c->update_control = c->pending = c->reads = 0;
    }
}

static void auto9_qnx_decon_reset(DeviceState *dev)
{
    Auto9QnxDecon *s = AUTO9_QNX_DECON(dev);
    memset(s->routes, 0, sizeof(s->routes));
    s->route_written = s->route_writes = 0;
    s->dp_route = s->dp_route_writes = 0;
    for (unsigned i = 0; i < G_N_ELEMENTS(s->pipe); i++) {
        Auto9DeconPipe *p = &s->pipe[i];
        auto9_decon_clear(p);
        p->reads = p->writes = p->rejected = p->updates = p->rejected_updates = 0;
        p->crc_writes = p->crc_empty_reads = p->frame_count_reads = 0;
        p->frame_ticks = p->frame_irqs = p->frame_acks = 0;
        p->frame_end_irqs = p->frame_end_acks = 0;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->win); i++) {
        auto9_qnx_decon_reset_window(&s->win[i]);
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->dpwin); i++) {
        auto9_qnx_decon_reset_window(&s->dpwin[i]);
    }
}

static void auto9_qnx_decon_attach_window(Object *obj, Auto9DeconWin *w,
                                         const char *name, unsigned n,
                                         unsigned first_pipe,
                                         unsigned control_count)
{
    Auto9QnxDecon *s = AUTO9_QNX_DECON(obj);
    SysBusDevice *bus = SYS_BUS_DEVICE(obj);
    g_autofree char *commits = g_strdup_printf("%s-%u-commits", name, n);
    g_autofree char *owner = g_strdup_printf("%s-%u-owner", name, n);
    g_autofree char *bad = g_strdup_printf("%s-%u-rejected-updates", name, n);
    g_autofree char *control = g_strdup_printf("%s-%u-active-control", name, n);
    g_autofree char *start = g_strdup_printf("%s-%u-active-start", name, n);
    g_autofree char *end = g_strdup_printf("%s-%u-active-end", name, n);
    w->parent = s;
    w->first_pipe = first_pipe;
    w->control_count = control_count;
    memory_region_init_io(&w->regs, obj, &auto9_decon_win_ops, w,
                          "auto9-decon-window-config", 0x1000);
    sysbus_init_mmio(bus, &w->regs);
    object_property_add_uint32_ptr(obj, commits, &w->commits, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, owner, &w->active_owner, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, bad, &w->bad_updates, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, control, &w->active_control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, start, &w->active[3], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, end, &w->active[4], OBJ_PROP_FLAG_READ);
    const char *const source_names[] = {
        "address", "control", "frame-size", "crop-offset", "crop-size", "output-size", "writes",
    };
    uint32_t *const source_fields[] = {
        &w->source.address, &w->source.control, &w->source.frame_size,
        &w->source.crop_offset, &w->source.crop_size, &w->source.output_size, &w->source.writes,
    };
    for (unsigned j = 0; j < G_N_ELEMENTS(source_names); j++) {
        g_autofree char *key = g_strdup_printf("%s-%u-source-%s", name, n, source_names[j]);
        object_property_add_uint32_ptr(obj, key, source_fields[j], OBJ_PROP_FLAG_READ);
    }
    assert(control_count <= G_N_ELEMENTS(w->ctl));
    for (unsigned j = 0; j < control_count; j++) {
        Auto9DeconWinCtl *c = &w->ctl[j];
        c->pipe = first_pipe + j;
        c->win = w;
        c->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_decon_win_update, c);
        memory_region_init_io(&c->region, obj, &auto9_decon_win_ctl_ops, c,
                              "auto9-decon-window-control", 0x1000);
        sysbus_init_mmio(bus, &c->region);
    }
}

static void auto9_qnx_decon_init(Object *obj)
{
    Auto9QnxDecon *s = AUTO9_QNX_DECON(obj);
    SysBusDevice *bus = SYS_BUS_DEVICE(obj);
    memory_region_init_io(&s->con, obj, &auto9_decon_con_ops, s,
                          "auto9-decon-con", 0x4000);
    sysbus_init_mmio(bus, &s->con);
    object_property_add_uint32_ptr(obj, "route-writes", &s->route_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dsi0-route", &s->routes[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dsi1-route", &s->routes[1], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dp0-route", &s->dp_route, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dp0-route-writes", &s->dp_route_writes, OBJ_PROP_FLAG_READ);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->pipe); i++) {
        Auto9DeconPipe *p = &s->pipe[i];
        p->parent = s;
        p->index = i;
        p->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_decon_timer, p);
        p->frame_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_decon_frame_tick, p);
        sysbus_init_irq(bus, &p->frame_irq);
        memory_region_init_io(&p->main, obj, &auto9_decon_main_ops, p, "auto9-decon-main", 0x1000);
        memory_region_init_io(&p->shadow, obj, &auto9_decon_shadow_ops, p, "auto9-decon-shadow", 0x1000);
        memory_region_init_io(&p->global, obj, &auto9_decon_global_ops, p, "auto9-decon-global", 0x1000);
        sysbus_init_mmio(bus, &p->main);
        sysbus_init_mmio(bus, &p->shadow);
        sysbus_init_mmio(bus, &p->global);
        const char *const names[] = {
            "reads", "writes", "rejected", "updates", "rejected-updates",
            "running", "active-size", "active-dsi", "active-dp", "crc-control", "crc-writes",
            "crc-empty-reads", "frame-count-reads", "timing-ns", "frame-pending", "frame-ticks",
            "frame-start-irqs", "frame-start-acks", "frame-irq-level",
            "scanned-frames", "scanned-source", "scanned-nonzero", "direct-scan-frames", "copied-scan-frames",
            "frame-end-irqs", "frame-end-acks", "frame-end-irq-level",
        };
        uint32_t *const values[] = {
            &p->reads, &p->writes, &p->rejected, &p->updates, &p->rejected_updates,
            &p->running, &p->active_size, &p->active_dsi, &p->active_dp,
            &p->crc_control, &p->crc_writes,
            &p->crc_empty_reads, &p->frame_count_reads, &p->timing_ns, &p->frame_pending, &p->frame_ticks,
            &p->frame_irqs, &p->frame_acks, &p->frame_level,
            &p->scanned_frames, &p->scanned_source, &p->scanned_nonzero, &p->direct_scan_frames, &p->copied_scan_frames,
            &p->frame_end_irqs, &p->frame_end_acks, &p->frame_end_level,
        };
        for (unsigned j = 0; j < G_N_ELEMENTS(names); j++) {
            g_autofree char *name = g_strdup_printf("%s-%u", names[j], i);
            object_property_add_uint32_ptr(obj, name, values[j], OBJ_PROP_FLAG_READ);
        }
        g_autofree char *direct_time = g_strdup_printf("direct-scan-host-ns-%u", i);
        g_autofree char *copied_time = g_strdup_printf("copied-scan-host-ns-%u", i);
        object_property_add_uint64_ptr(obj, direct_time, &p->direct_scan_ns, OBJ_PROP_FLAG_READ);
        object_property_add_uint64_ptr(obj, copied_time, &p->copied_scan_ns, OBJ_PROP_FLAG_READ);
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->pipe); i++) {
        sysbus_init_irq(bus, &s->pipe[i].frame_end_irq);
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->win); i++) {
        auto9_qnx_decon_attach_window(obj, &s->win[i], "window",
                                      auto9_decon_windows[i], 0, 2);
    }
    memory_region_init_io(&s->dp_con, obj, &auto9_decon_dp_con_ops, s,
                          "auto9-dpus0-dp0-connection", 4);
    sysbus_init_mmio(bus, &s->dp_con);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->dpwin); i++) {
        auto9_qnx_decon_attach_window(obj, &s->dpwin[i], "dpus0-window",
                                      auto9_dpus_windows[i], DECON_AUTO9_DP_PIPE, 1);
    }
}

static void auto9_qnx_decon_finalize(Object *obj)
{
    Auto9QnxDecon *s = AUTO9_QNX_DECON(obj);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->pipe); i++) {
        timer_free(s->pipe[i].timer);
        timer_free(s->pipe[i].frame_timer);
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->win); i++) {
        for (unsigned j = 0; j < s->win[i].control_count; j++) {
            timer_free(s->win[i].ctl[j].timer);
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->dpwin); i++) {
        timer_free(s->dpwin[i].ctl[0].timer);
    }
}

static void auto9_qnx_decon_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_qnx_decon_reset);
}

static const TypeInfo auto9_qnx_decon_type = {
    .name = TYPE_AUTO9_QNX_DECON,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9QnxDecon),
    .instance_init = auto9_qnx_decon_init,
    .instance_finalize = auto9_qnx_decon_finalize,
    .class_init = auto9_qnx_decon_class,
};

static void auto9_qnx_decon_register(void)
{
    type_register_static(&auto9_qnx_decon_type);
}
type_init(auto9_qnx_decon_register)

static void auto9_qnx_decon_create(VirtMachineState *vms)
{
    if (!vms->exynos_qnx_dsi) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_QNX_DECON);
    Auto9QnxDecon *s = AUTO9_QNX_DECON(dev);
    Object *dsi = object_resolve_path_component(OBJECT(vms), "exynos-qnx-dsi");
    s->dsi = AUTO9_QNX_DSI(dsi);
    Object *dpp = object_resolve_path_component(OBJECT(vms), "exynos-qnx-dpp-irqs");
    if (dpp) {
        s->dpp = AUTO9_DPP_IRQ(dpp);
    }
    if (vms->exynos_dp0_aux_sink) {
        Object *dp = object_resolve_path_component(OBJECT(vms), "exynos-dp0-reset");
        s->dp0 = AUTO9_DP0_RESET(dp);
    }
    object_property_add_child(OBJECT(vms), "exynos-qnx-decon", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x18c3c000);
    for (unsigned i = 0; i < 2; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1 + 3 * i, 0x18c38000 + 0x1000 * i);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2 + 3 * i, 0x18c3a000 + 0x1000 * i);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 3 + 3 * i, 0x18c4a000 + 0x1000 * i);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), i, qdev_get_gpio_in(vms->gic, 117 + 5 * i));
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), G_N_ELEMENTS(s->pipe) + i,
                           qdev_get_gpio_in(vms->gic, 116 + 5 * i));
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_decon_windows); i++) {
        hwaddr offset = 0x1000 * auto9_decon_windows[i];
        unsigned n = 1 + 3 * G_N_ELEMENTS(s->pipe) + 3 * i;
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), n, 0x18c30000 + offset);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), n + 1, 0x18c50000 + offset);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), n + 2, 0x18c58000 + offset);
    }
    if (s->dp0) {
        const unsigned n = 1 + 3 * DECON_AUTO9_DP_PIPE;
        const unsigned con = 1 + 3 * G_N_ELEMENTS(s->pipe) + 3 * G_N_ELEMENTS(s->win);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), n, 0x1aa38000);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), n + 1, 0x1aa3a000);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), n + 2, 0x1aa4a000);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), con, 0x1aa3e000);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), DECON_AUTO9_DP_PIPE,
                           qdev_get_gpio_in(vms->gic, 131));
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), G_N_ELEMENTS(s->pipe) + DECON_AUTO9_DP_PIPE,
                           qdev_get_gpio_in(vms->gic, 130));
        for (unsigned i = 0; i < G_N_ELEMENTS(s->dpwin); i++) {
            hwaddr offset = 0x1000 * auto9_dpus_windows[i];
            unsigned slot = con + 1 + 2 * i;
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), slot, 0x1aa30000 + offset);
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), slot + 1, 0x1aa50000 + offset);
        }
    }
}

