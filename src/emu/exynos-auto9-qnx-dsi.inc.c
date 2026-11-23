/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original VM2 display-PHY SYSREG and two DSIM LINK reset/config banks.
 * Behavior-level PHY/link handshakes and peripheral control short packets.
 * No electrical signaling, pixel packets, frame generation or routed IRQ.
 */
#define TYPE_AUTO9_QNX_DSI "exynos-auto9.qnx-dsi"
#define AUTO9_DSIM_SWRESET_NS 500
#define AUTO9_DPHY_LOCK_NS 20000 /* Virtual timing, not an Auto9 measurement. */
#define AUTO9_DPHY_PLL_ENABLE BIT(12)
#define AUTO9_DSIM_PLL_ENABLE BIT(23)
#define AUTO9_DSIM_PLL_STABLE BIT(31)
#define AUTO9_DSIM_HEADER_EMPTY BIT(28)
#define AUTO9_DSIM_HEADER_NS 2000 /* Virtual delivery latency. */
#define AUTO9_DSIM_PERIPHERAL_OFF 0x22
#define AUTO9_DSIM_PERIPHERAL_ON 0x32
OBJECT_DECLARE_SIMPLE_TYPE(Auto9QnxDsi, AUTO9_QNX_DSI)

typedef struct Auto9DsimLink {
    MemoryRegion region, phy;
    QEMUTimer *timer, *pll_timer, *header_timer;
    Auto9QnxDsi *parent;
    unsigned id;
    uint32_t clk_ctrl, lane_en, resetting, reset_requests, reset_completions;
    uint32_t clock_writes, lane_writes;
    uint32_t link_words[0x118 / 4], phy_words[0x700 / 4];
    uint32_t pll_locked, pll_locks, pll_status_reads, intsrc, phy_writes;
    uint32_t pending_header, peripheral_on, headers_sent, headers_rejected;
    uint32_t headers_cancelled, last_header;
} Auto9DsimLink;

struct Auto9QnxDsi {
    SysBusDevice parent_obj;
    MemoryRegion route, bias;
    uint32_t dphy_ctrl, route_reads, route_writes;
    uint32_t bias_words[5], bias_writes;
    Auto9DsimLink link[2];
};

static uint64_t auto9_dsi_route_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9QnxDsi *s = opaque;
    s->route_reads++;
    return s->dphy_ctrl;
}

static void auto9_dsi_route_write(void *opaque, hwaddr off, uint64_t value,
                                 unsigned size)
{
    Auto9QnxDsi *s = opaque;
    /* Original per-link reset control bits 0/1 and source selection 4/5. */
    s->dphy_ctrl = value & 0x33;
    s->route_writes++;
}

static const MemoryRegionOps auto9_dsi_route_ops = {
    .read = auto9_dsi_route_read,
    .write = auto9_dsi_route_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static bool auto9_dsim_pll_configured(const Auto9DsimLink *link)
{
    static const uint32_t bias[] = { 0x10, 0x110, 0x3223, 0, 0 };
    uint32_t phy = link->phy_words[0], ctrl = link->link_words[0xa0 / 4];
    unsigned p = phy & 0x3f, m = link->phy_words[2] & 0x3ff;
    unsigned s = (phy >> 8) & 7;

    /* OEM writes both PMS copies, then enables both PLL controls. */
    return !link->resetting && (phy & AUTO9_DPHY_PLL_ENABLE) &&
           (ctrl & AUTO9_DSIM_PLL_ENABLE) && p && m &&
           (ctrl & 0x7ffff) == (p << 13 | (m & 0x1ff) << 3 | s) &&
           (link->phy_words[0x1c / 4] & 0xffff) &&
           !memcmp(bias, link->parent->bias_words, sizeof(bias));
}

static void auto9_dsim_pll_done(void *opaque)
{
    Auto9DsimLink *link = opaque;
    if (auto9_dsim_pll_configured(link)) {
        link->pll_locked = 1;
        link->pll_locks++;
        link->intsrc |= AUTO9_DSIM_PLL_STABLE;
    }
}

static void auto9_dsim_pll_update(Auto9DsimLink *link, bool changed)
{
    if (changed || !auto9_dsim_pll_configured(link)) {
        link->pll_locked = 0;
        timer_del(link->pll_timer);
    }
    if (auto9_dsim_pll_configured(link) && !link->pll_locked &&
        !timer_pending(link->pll_timer)) {
        timer_mod(link->pll_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  AUTO9_DPHY_LOCK_NS);
    }
}

static bool auto9_dphy_lane_control(hwaddr off)
{
    return off >= 0x200 && off <= 0x600 && (off & 0xff) == 0;
}

static bool auto9_dphy_word(hwaddr off)
{
    hwaddr lane = off >> 8, reg = off & 0xff;
    if (off <= 0x20) {
        return true; /* PLL_CON0..8 */
    }
    if (lane < 2 || lane > 6) {
        return false;
    }
    return reg <= (lane == 2 ? 0xc : 0x14) ||
           (reg >= 0x30 && reg <= 0x40) || (lane == 2 && reg == 0x50);
}

static MemTxResult auto9_dphy_read(void *opaque, hwaddr off, uint64_t *value,
                                   unsigned size, MemTxAttrs attrs)
{
    Auto9DsimLink *link = opaque;
    if (off == 0x40) {
        link->pll_status_reads++;
        *value = link->pll_locked;
    } else if (auto9_dphy_word(off)) {
        *value = link->phy_words[off / 4];
        if (auto9_dphy_lane_control(off) && link->pll_locked && (*value & 1)) {
            *value |= BIT(1); /* Lane-ready readback, not signaling or data. */
        }
    } else {
        qemu_log_mask(LOG_UNIMP, "Auto9 DPHY unsupported read offset 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult auto9_dphy_write(void *opaque, hwaddr off, uint64_t value,
                                    unsigned size, MemTxAttrs attrs)
{
    Auto9DsimLink *link = opaque;
    if (!auto9_dphy_word(off)) {
        qemu_log_mask(LOG_UNIMP, "Auto9 DPHY unsupported write offset 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_DECODE_ERROR;
    }
    if (auto9_dphy_lane_control(off)) {
        value &= ~BIT(1); /* Ready is read-only; preserve guest RMW semantics. */
    }
    bool changed = link->phy_words[off / 4] != value;
    link->phy_words[off / 4] = value;
    link->phy_writes++;
    if (off <= 0x20 && changed) {
        auto9_dsim_pll_update(link, true);
    }
    return MEMTX_OK;
}

static uint64_t auto9_dphy_bias_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9QnxDsi *s = opaque;
    return s->bias_words[off / 4];
}

static void auto9_dphy_bias_write(void *opaque, hwaddr off, uint64_t value,
                                unsigned size)
{
    Auto9QnxDsi *s = opaque;
    bool changed = s->bias_words[off / 4] != value;
    s->bias_words[off / 4] = value;
    s->bias_writes++;
    if (changed) {
        for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
            auto9_dsim_pll_update(&s->link[i], true);
        }
    }
}

static const MemoryRegionOps auto9_dphy_ops = {
    .read_with_attrs = auto9_dphy_read,
    .write_with_attrs = auto9_dphy_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static const MemoryRegionOps auto9_dphy_bias_ops = {
    .read = auto9_dphy_bias_read,
    .write = auto9_dphy_bias_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static bool auto9_dsim_latch(hwaddr off)
{
    /* OEM init/start configuration, not FIFO or display output. */
    switch (off) {
    case 0x24: case 0x28: case 0x2c: case 0x30: case 0x38:
    case 0x3c: case 0x44: case 0x48: case 0x54: case 0x64:
    case 0x80: case 0x84: case 0x88: case 0x90:
    case 0x104: case 0x108: case 0x110: case 0x114:
        return true;
    default:
        return false;
    }
}

static bool auto9_dsim_hs_ready(const Auto9DsimLink *link)
{
    return !link->resetting && link->pll_locked &&
           (link->parent->dphy_ctrl & BIT(link->id)) &&
           (link->clk_ctrl & BIT(20)) && (link->clk_ctrl & BIT(26)) &&
           (link->phy_words[0x200 / 4] & 1);
}

static void auto9_dsim_header_cancel(Auto9DsimLink *link)
{
    if (link->pending_header) {
        timer_del(link->header_timer);
        link->pending_header = 0;
        link->headers_cancelled++;
    }
}

static void auto9_dsim_header_done(void *opaque)
{
    Auto9DsimLink *link = opaque;
    uint32_t header = link->pending_header;

    if (!auto9_dsim_hs_ready(link)) {
        auto9_dsim_header_cancel(link);
        return;
    }
    link->pending_header = 0;
    link->last_header = header;
    link->peripheral_on = header == AUTO9_DSIM_PERIPHERAL_ON;
    link->headers_sent++;
    link->intsrc |= AUTO9_DSIM_HEADER_EMPTY;
}

static void auto9_dsim_reset_done(void *opaque)
{
    Auto9DsimLink *link = opaque;
    link->resetting = 0;
    link->clk_ctrl = link->lane_en = 0;
    memset(link->link_words, 0, sizeof(link->link_words));
    auto9_dsim_pll_update(link, true);
    link->intsrc = BIT(30);
    link->reset_completions++;
}

static MemTxResult auto9_dsim_read(void *opaque, hwaddr off, uint64_t *value,
                                  unsigned size, MemTxAttrs attrs)
{
    Auto9DsimLink *link = opaque;
    switch (off) {
    case 0: *value = 0x02030000; return MEMTX_OK; /* Modeled EVT1 family. */
    case 4: *value = link->resetting; return MEMTX_OK;
    case 0x14: *value = link->pll_locked; return MEMTX_OK;
    case 0x1c:
        *value = auto9_dsim_hs_ready(link) ? BIT(10) : 0;
        return MEMTX_OK;
    case 0x20: *value = link->clk_ctrl; return MEMTX_OK;
    case 0x4c: *value = link->lane_en; return MEMTX_OK;
    case 0x50: *value = link->intsrc; return MEMTX_OK;
    case 0x68:
        /* Only the header queue is active. Payload and RX queues stay empty. */
        *value = BIT(12) | BIT(8) | (link->pending_header ?
                 BIT(16) | BIT(11) : BIT(10));
        return MEMTX_OK;
    case 0xa0: *value = link->link_words[off / 4]; return MEMTX_OK;
    }
    if (auto9_dsim_latch(off)) {
        *value = link->link_words[off / 4];
        return MEMTX_OK;
    }
    qemu_log_mask(LOG_UNIMP, "Auto9 DSIM unsupported read offset 0x%" HWADDR_PRIx "\n", off);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_dsim_write(void *opaque, hwaddr off, uint64_t value,
                                   unsigned size, MemTxAttrs attrs)
{
    Auto9DsimLink *link = opaque;
    switch (off) {
    case 4:
        if (value & ~UINT64_C(1)) {
            break;
        }
        if ((value & 1) && !link->resetting) {
            link->resetting = 1;
            auto9_dsim_header_cancel(link);
            auto9_dsim_pll_update(link, true);
            link->reset_requests++;
            timer_mod(link->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      AUTO9_DSIM_SWRESET_NS);
        }
        return MEMTX_OK;
    case 0x20:
        if (value & ~UINT64_C(0x07131fff)) {
            break;
        }
        link->clk_ctrl = value;
        link->clock_writes++;
        return MEMTX_OK;
    case 0x4c:
        if (value & ~UINT64_C(0x7dfdffff)) {
            break;
        }
        link->lane_en = value;
        link->lane_writes++;
        return MEMTX_OK;
    case 0x50:
        link->intsrc &= ~value;
        return MEMTX_OK;
    case 0x58:
        if ((value != AUTO9_DSIM_PERIPHERAL_ON &&
             value != AUTO9_DSIM_PERIPHERAL_OFF) ||
            link->pending_header || !auto9_dsim_hs_ready(link)) {
            link->headers_rejected++;
            break;
        }
        link->pending_header = value;
        timer_mod(link->header_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  AUTO9_DSIM_HEADER_NS);
        return MEMTX_OK;
    case 0x68:
        if (value & ~UINT64_C(7)) {
            break;
        }
        if (value & BIT(1)) {
            auto9_dsim_header_cancel(link);
        }
        return MEMTX_OK;
    case 0xa0:
        if (value & ~(UINT64_C(0x7ffff) | AUTO9_DSIM_PLL_ENABLE)) {
            break;
        }
        if (link->link_words[off / 4] != value) {
            link->link_words[off / 4] = value;
            auto9_dsim_pll_update(link, true);
        }
        return MEMTX_OK;
    }
    if (auto9_dsim_latch(off)) {
        link->link_words[off / 4] = value;
        return MEMTX_OK;
    }
    qemu_log_mask(LOG_UNIMP, "Auto9 DSIM unsupported write offset 0x%" HWADDR_PRIx
                  ": 0x%" PRIx64 "\n", off, value);
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps auto9_dsim_ops = {
    .read_with_attrs = auto9_dsim_read,
    .write_with_attrs = auto9_dsim_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_qnx_dsi_reset(DeviceState *dev)
{
    Auto9QnxDsi *s = AUTO9_QNX_DSI(dev);
    s->dphy_ctrl = s->route_reads = s->route_writes = 0;
    memset(s->bias_words, 0, sizeof(s->bias_words));
    s->bias_writes = 0;
    for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
        Auto9DsimLink *link = &s->link[i];
        timer_del(link->timer);
        timer_del(link->pll_timer);
        timer_del(link->header_timer);
        link->pending_header = link->last_header = link->peripheral_on = 0;
        link->headers_sent = link->headers_rejected = link->headers_cancelled = 0;
        memset(link->link_words, 0, sizeof(link->link_words));
        memset(link->phy_words, 0, sizeof(link->phy_words));
        link->pll_locked = link->pll_locks = link->pll_status_reads = 0;
        link->intsrc = link->phy_writes = 0;
        link->clk_ctrl = link->lane_en = link->resetting = 0;
        link->reset_requests = link->reset_completions = 0;
        link->clock_writes = link->lane_writes = 0;
    }
}

static void auto9_qnx_dsi_init(Object *obj)
{
    Auto9QnxDsi *s = AUTO9_QNX_DSI(obj);
    memory_region_init_io(&s->route, obj, &auto9_dsi_route_ops, s,
                          "auto9-dsi-dphy-control", 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->route);
    memory_region_init_io(&s->bias, obj, &auto9_dphy_bias_ops, s,
                          "auto9-dphy-bias", sizeof(s->bias_words));
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->bias);
    object_property_add_uint32_ptr(obj, "bias-writes", &s->bias_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dphy-control", &s->dphy_ctrl, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "route-reads", &s->route_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "route-writes", &s->route_writes, OBJ_PROP_FLAG_READ);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
        Auto9DsimLink *link = &s->link[i];
        g_autofree char *requests = g_strdup_printf("reset-requests-%u", i);
        g_autofree char *completed = g_strdup_printf("reset-completions-%u", i);
        g_autofree char *clocks = g_strdup_printf("clock-writes-%u", i);
        g_autofree char *lanes = g_strdup_printf("lane-writes-%u", i);
        g_autofree char *locks = g_strdup_printf("pll-locks-%u", i);
        g_autofree char *locked = g_strdup_printf("pll-locked-%u", i);
        g_autofree char *polls = g_strdup_printf("pll-status-reads-%u", i);
        g_autofree char *writes = g_strdup_printf("phy-writes-%u", i);
        link->parent = s;
        link->id = i;
        memory_region_init_io(&link->region, obj, &auto9_dsim_ops, link,
                              "auto9-dsim-link", 0x1000);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &link->region);
        memory_region_init_io(&link->phy, obj, &auto9_dphy_ops, link,
                              "auto9-dsim-phy", sizeof(link->phy_words));
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &link->phy);
        link->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dsim_reset_done, link);
        link->pll_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dsim_pll_done, link);
        link->header_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                         auto9_dsim_header_done, link);
        static const char *const names[] = {
            "peripheral-on", "headers-sent", "headers-rejected",
            "headers-cancelled", "last-header",
        };
        uint32_t *const counters[] = {
            &link->peripheral_on, &link->headers_sent, &link->headers_rejected,
            &link->headers_cancelled, &link->last_header,
        };
        for (unsigned j = 0; j < G_N_ELEMENTS(names); j++) {
            g_autofree char *key = g_strdup_printf("%s-%u", names[j], i);
            object_property_add_uint32_ptr(obj, key, counters[j], OBJ_PROP_FLAG_READ);
        }
        object_property_add_uint32_ptr(obj, locks, &link->pll_locks, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, locked, &link->pll_locked, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, polls, &link->pll_status_reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, writes, &link->phy_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, requests, &link->reset_requests, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, completed, &link->reset_completions, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, clocks, &link->clock_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, lanes, &link->lane_writes, OBJ_PROP_FLAG_READ);
    }
}

static void auto9_qnx_dsi_finalize(Object *obj)
{
    Auto9QnxDsi *s = AUTO9_QNX_DSI(obj);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->link); i++) {
        timer_free(s->link[i].timer);
        timer_free(s->link[i].pll_timer);
        timer_free(s->link[i].header_timer);
    }
}

static void auto9_qnx_dsi_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_qnx_dsi_reset);
}

static const TypeInfo auto9_qnx_dsi_type = {
    .name = TYPE_AUTO9_QNX_DSI,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9QnxDsi),
    .instance_init = auto9_qnx_dsi_init,
    .instance_finalize = auto9_qnx_dsi_finalize,
    .class_init = auto9_qnx_dsi_class,
};

static void auto9_qnx_dsi_register(void)
{
    type_register_static(&auto9_qnx_dsi_type);
}
type_init(auto9_qnx_dsi_register)

static void auto9_qnx_dsi_create(VirtMachineState *vms)
{
    if (vms->exynos_qnx_dsi) {
        DeviceState *dev = qdev_new(TYPE_AUTO9_QNX_DSI);
        object_property_add_child(OBJECT(vms), "exynos-qnx-dsi", OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x18c11008);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, 0x18e20000);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2, 0x18e40000);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 3, 0x18e20100);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 4, 0x18e50000);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 5, 0x18e21100);
    }
}
