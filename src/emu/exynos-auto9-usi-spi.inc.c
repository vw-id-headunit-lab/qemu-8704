/* SPDX-License-Identifier: GPL-2.0-or-later
 * Explicit virtual-spi1-v1 and virtual-linux-spi-v1: source-pinned original
 * DT ports1/6/7/10, selector mask7/value2, flush_fifo, RX15/TXDONE25.
 * Bounded real FIFOs, virtual deadlines, IRQ and PL330 backpressure. SSI
 * transports real transfers; optional SPI6 peer is limited to the OEM startup
 * protocol. Timing,
 * cold state and one-word PIO ready thresholds are virtual assumptions.
 * CLK_CFG/USI_OPTION retain bounded config, not a recovered clock model.
 * No slave mode, extended CS delay or trailing interrupt is implemented.
 */
#include "hw/ssi/ssi.h"
#define TYPE_EXYNOS_AUTO9_USI_SPI "exynos-auto9.usi-spi"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9UsiSpi, EXYNOS_AUTO9_USI_SPI)
struct ExynosAuto9UsiSpi {
    SysBusDevice parent_obj;
    MemoryRegion selector_mmio, spi_mmio;
    SSIBus *bus;
    QEMUTimer *deadline;
    qemu_irq irq, dma_stop[2], cs;
    uint32_t profile, port, fifo_size, period_ns, selector, control, option;
    uint32_t ch, clk, mode, slave, int_en, pending, packet, swap, fb;
    uint8_t tx[256], rx[256];
    uint32_t tx_head, rx_head, tx_count, rx_count, packet_left;
    uint32_t reads, writes, selector_reads, selector_writes, rejected;
    uint32_t resets, shifted, loopback_words, wire_words, irq_level;
    uint32_t tx_overruns, rx_overruns, rx_underruns;
    uint32_t dma_tx_stop, dma_rx_stop, last_offset, last_value, active;
};
static bool exynos_auto9_usi_spi_enabled(ExynosAuto9UsiSpi *s)
{ return s->profile == 2 || (s->profile == 1 && s->port == 1); }
static unsigned exynos_auto9_usi_spi_width(ExynosAuto9UsiSpi *s)
{ return 1U << ((s->mode >> 29) & 3); }
static unsigned exynos_auto9_usi_spi_dma_bytes(ExynosAuto9UsiSpi *s)
{
    /* OEM prepare_dma configures maxburst 4 for MODE_CFG bit 0, 1 otherwise.
     * A PL330 request covers the whole burst; the PIO ready bit does not. */
    return exynos_auto9_usi_spi_width(s) * (s->mode & 1 ? 4 : 1);
}
static bool exynos_auto9_usi_spi_running(ExynosAuto9UsiSpi *s)
{ return exynos_auto9_usi_spi_enabled(s) && s->selector == 2 && !(s->control & 1) && !(s->ch & 0x20); }
static bool exynos_auto9_usi_spi_rx_clock(ExynosAuto9UsiSpi *s)
{
    /* OEM enable_datapath clears CH_CFG.TX for an RX-only SPI6 DMA transfer
     * and programs PACKET_CNT to its word count. Master supplies dummy MOSI. */
    return s->port == 6 && (s->ch & 3) == 2 && (s->mode & 4) &&
           (s->packet & 0x10000) && s->packet_left;
}
static bool exynos_auto9_usi_spi_clock_enabled(ExynosAuto9UsiSpi *s)
{
    return exynos_auto9_usi_spi_running(s) && (!(s->slave & 1) || (s->mode & 8)) &&
           (!(s->packet & 0x10000) || s->packet_left) &&
           ((s->ch & 1) || exynos_auto9_usi_spi_rx_clock(s));
}
static void exynos_auto9_usi_spi_update(ExynosAuto9UsiSpi *s)
{
    bool run = exynos_auto9_usi_spi_running(s);
    unsigned width = exynos_auto9_usi_spi_width(s);
    unsigned burst = exynos_auto9_usi_spi_dma_bytes(s);
    uint32_t ready = (s->tx_count <= s->fifo_size - width ? 1 : 0) | (s->rx_count >= width ? 2 : 0);
    s->active = exynos_auto9_usi_spi_enabled(s) && s->selector == 2;
    memory_region_set_enabled(&s->spi_mmio, s->active);
    s->irq_level = run && (s->int_en & (s->pending | ready));
    s->irq_level = !!s->irq_level; qemu_set_irq(s->irq, s->irq_level);
    /* Upstream PL330 inputs mean STOP/BUSY, not active-high requests. */
    s->dma_tx_stop = !(run && (s->ch & 1) && (s->mode & 2) &&
                       s->fifo_size - s->tx_count >= burst);
    s->dma_rx_stop = !(run && (s->ch & 2) && (s->mode & 4) && s->rx_count >= burst);
    qemu_set_irq(s->dma_stop[0], s->dma_tx_stop);
    qemu_set_irq(s->dma_stop[1], s->dma_rx_stop);
    qemu_set_irq(s->cs, !run || (s->slave & 1));
    if (!exynos_auto9_usi_spi_clock_enabled(s)) { timer_del(s->deadline); }
}
static uint32_t exynos_auto9_usi_spi_swap(uint32_t value, unsigned flags)
{
    if (!(flags & 1)) { return value; }
    if (flags & 2) {
        uint32_t x = value;
        x = ((x & 0x55555555) << 1) | ((x >> 1) & 0x55555555);
        x = ((x & 0x33333333) << 2) | ((x >> 2) & 0x33333333);
        value = ((x & 0x0f0f0f0f) << 4) | ((x >> 4) & 0x0f0f0f0f);
    }
    if (flags & 4) { value = ((value & 0x00ff00ff) << 8) | ((value >> 8) & 0x00ff00ff); }
    if (flags & 8) { value = (value << 16) | (value >> 16); }
    return value;
}
static void exynos_auto9_usi_spi_kick(ExynosAuto9UsiSpi *s)
{
    unsigned width = exynos_auto9_usi_spi_width(s);
    if (exynos_auto9_usi_spi_clock_enabled(s) &&
        (exynos_auto9_usi_spi_rx_clock(s) || s->tx_count >= width) &&
        !timer_pending(s->deadline)) {
        timer_mod(s->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + (uint64_t)s->period_ns * width);
    }
    exynos_auto9_usi_spi_update(s);
}
static void exynos_auto9_usi_spi_shift(void *opaque)
{
    ExynosAuto9UsiSpi *s = opaque;
    unsigned width = exynos_auto9_usi_spi_width(s);
    bool rx_clock = exynos_auto9_usi_spi_rx_clock(s);
    if (!exynos_auto9_usi_spi_clock_enabled(s) || (!rx_clock && s->tx_count < width)) { return; }
    uint32_t value = 0;
    if (!rx_clock) {
        for (unsigned i = 0; i < width; i++) {
            value |= (uint32_t)s->tx[s->tx_head] << (8 * i);
            s->tx_head = (s->tx_head + 1) % s->fifo_size; s->tx_count--;
        }
    }
    value = exynos_auto9_usi_spi_swap(value, s->swap & 15);
    if (s->mode & 8) { s->loopback_words++; }
    else { value = ssi_transfer(s->bus, value); s->wire_words++; }
    value = exynos_auto9_usi_spi_swap(value, s->swap >> 4);
    if (s->ch & 2) {
        if (s->rx_count + width <= s->fifo_size) {
            for (unsigned i = 0; i < width; i++) {
                s->rx[(s->rx_head + s->rx_count++) % s->fifo_size] = value >> (8 * i);
            }
        } else { s->pending |= 0x20; s->rx_overruns++; }
    }
    s->shifted++;
    if ((s->packet & 0x10000) && s->packet_left) { s->packet_left--; }
    exynos_auto9_usi_spi_kick(s);
}
static void exynos_auto9_usi_spi_flush(ExynosAuto9UsiSpi *s)
{
    timer_del(s->deadline); s->tx_head = s->rx_head = s->tx_count = s->rx_count = 0;
    s->packet_left = 0; s->pending = 0; s->resets++;
}
static MemTxResult exynos_auto9_usi_spi_read(void *opaque, hwaddr off,
                                            uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UsiSpi *s = opaque; s->reads++;
    if (off == 0x1c && (size == 1 || size == 2 || size == 4)) {
        *value = 0;
        if (s->rx_count < size) { s->pending |= 0x10; s->rx_underruns++; }
        else {
            for (unsigned i = 0; i < size; i++) {
                *value |= (uint64_t)s->rx[s->rx_head] << (8 * i);
                s->rx_head = (s->rx_head + 1) % s->fifo_size; s->rx_count--;
            }
        }
        exynos_auto9_usi_spi_update(s); return MEMTX_OK;
    }
    if (size != 4 || (off & 3)) { goto reject; }
    switch (off) {
    case 0: *value = s->ch; break;
    case 4: *value = s->clk; break;
    case 8: *value = s->mode; break;
    case 0xc: *value = s->slave; break;
    case 0x10: *value = s->int_en; break;
    case 0x14:
        *value = s->pending | (s->tx_count < s->fifo_size ? 1 : 0) | (s->rx_count ? 2 : 0) |
                 (s->tx_count << 6) | (s->rx_count << 15) |
                 (!s->tx_count && !timer_pending(s->deadline) ? 1U << 25 : 0);
        break;
    case 0x20: *value = (s->packet & 0x10000) | s->packet_left; break;
    case 0x24: *value = 0; break;
    case 0x28: *value = s->swap; break;
    case 0x2c: *value = s->fb; break;
    case 0xc4: *value = s->control; break;
    case 0xc8: *value = s->option; break;
    default: goto reject;
    }
    return MEMTX_OK;
reject:
    s->rejected++; *value = 0; return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_usi_spi_write(void *opaque, hwaddr off,
                                             uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UsiSpi *s = opaque; s->writes++; s->last_offset = off; s->last_value = value;
    if (off == 0x18 && (size == 1 || size == 2 || size == 4)) {
        if (s->tx_count + size > s->fifo_size) { s->pending |= 8; s->tx_overruns++; }
        else {
            for (unsigned i = 0; i < size; i++) {
                s->tx[(s->tx_head + s->tx_count++) % s->fifo_size] = value >> (8 * i);
            }
        }
        exynos_auto9_usi_spi_kick(s); return MEMTX_OK;
    }
    if (size != 4 || (off & 3)) { goto reject; }
    switch (off) {
    case 0:
        if (value & ~0x7fULL || (value & 0x10)) { goto reject; } /* Master only. */
        s->ch = value; if (value & 0x20) { exynos_auto9_usi_spi_flush(s); } break;
    case 4: if (value & ~0x7ffULL) { goto reject; } s->clk = value; break;
    case 8:
        if (value & ~0x7ffe000fULL || ((value >> 29) & 3) == 3 || ((value >> 17) & 3) == 3) { goto reject; }
        s->mode = value; break;
    case 0xc: if (value & ~3ULL) { goto reject; } s->slave = value; break;
    case 0x10: if (value & ~0x3fULL) { goto reject; } s->int_en = value; break;
    case 0x20: if (value & ~0x1ffffULL) { goto reject; } s->packet = value; s->packet_left = value & 0xffff; break;
    case 0x24:
        if (value & ~0x1eULL) { goto reject; }
        if (value & 2) { s->pending &= ~0x20; } if (value & 4) { s->pending &= ~0x10; }
        if (value & 8) { s->pending &= ~8; } if (value & 0x10) { s->pending &= ~4; }
        break;
    case 0x28: if (value & ~255ULL) { goto reject; } s->swap = value; break;
    case 0x2c: if (value & ~3ULL) { goto reject; } s->fb = value; break;
    case 0xc4: if (value > 1) { goto reject; } s->control = value; if (value) { exynos_auto9_usi_spi_flush(s); } break;
    case 0xc8: if (value & ~6ULL) { goto reject; } s->option = value; break;
    default: goto reject;
    }
    exynos_auto9_usi_spi_kick(s); return MEMTX_OK;
reject:
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 SPI%u unsupported off0x%llx value0x%llx\n", s->port, (unsigned long long)off, (unsigned long long)value);
    return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_usi_spi_selector_read(void *opaque, hwaddr off,
                                                     uint64_t *value, unsigned size, MemTxAttrs attrs)
{ ExynosAuto9UsiSpi *s = opaque; s->selector_reads++; *value = s->selector; return MEMTX_OK; }
static MemTxResult exynos_auto9_usi_spi_selector_write(void *opaque, hwaddr off,
                                                      uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UsiSpi *s = opaque;
    if (value != 0 && value != 1 && value != 2 && value != 4) { s->rejected++; return MEMTX_DECODE_ERROR; }
    s->selector = value; s->selector_writes++; exynos_auto9_usi_spi_kick(s); return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_usi_spi_ops = {
    .read_with_attrs = exynos_auto9_usi_spi_read, .write_with_attrs = exynos_auto9_usi_spi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
    .impl.min_access_size = 1, .impl.max_access_size = 4,
};
static const MemoryRegionOps exynos_auto9_usi_spi_selector_ops = {
    .read_with_attrs = exynos_auto9_usi_spi_selector_read, .write_with_attrs = exynos_auto9_usi_spi_selector_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_usi_spi_property_get(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    ExynosAuto9UsiSpi *s = EXYNOS_AUTO9_USI_SPI(obj);
    uint32_t value = !strcmp(name, "virtual-profile") ? s->profile : s->period_ns;
    visit_type_uint32(v, name, &value, errp);
}
static void exynos_auto9_usi_spi_property_set(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    ExynosAuto9UsiSpi *s = EXYNOS_AUTO9_USI_SPI(obj); uint32_t value;
    if (!visit_type_uint32(v, name, &value, errp)) { return; }
    if (DEVICE(obj)->realized) { error_setg(errp, "SPI profile/timing are immutable after realization"); return; }
    if (!strcmp(name, "virtual-profile")) {
        if (value > 2) { error_setg(errp, "Require SPI profile0/1/2"); return; } s->profile = value;
    } else {
        if (!value || value > 1000000) { error_setg(errp, "Require virtual SPI period1..1000000ns/byte"); return; } s->period_ns = value;
    }
}
static void exynos_auto9_usi_spi_reset(DeviceState *dev)
{
    ExynosAuto9UsiSpi *s = EXYNOS_AUTO9_USI_SPI(dev);
    s->selector = s->ch = s->clk = s->mode = s->int_en = s->pending = s->packet = s->swap = s->fb = 0;
    s->slave = s->control = 1; s->option = 0; exynos_auto9_usi_spi_flush(s);
    s->reads = s->writes = s->selector_reads = s->selector_writes = s->rejected = s->resets = 0;
    s->shifted = s->loopback_words = s->wire_words = s->last_offset = s->last_value = 0;
    s->tx_overruns = s->rx_overruns = s->rx_underruns = 0;
    exynos_auto9_usi_spi_update(s);
}
static void exynos_auto9_usi_spi_init(Object *obj)
{
    ExynosAuto9UsiSpi *s = EXYNOS_AUTO9_USI_SPI(obj); s->period_ns = 1000; s->fifo_size = 256;
    s->deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_usi_spi_shift, s);
    memory_region_init_io(&s->selector_mmio, obj, &exynos_auto9_usi_spi_selector_ops, s, "exynos-auto9.usi-spi-selector", 4);
    memory_region_init_io(&s->spi_mmio, obj, &exynos_auto9_usi_spi_ops, s, "exynos-auto9.spi", 0x100);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->selector_mmio); sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->spi_mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq); sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->cs);
    qdev_init_gpio_out_named(DEVICE(obj), s->dma_stop, "dma-stop", 2);
    object_property_add(obj, "virtual-profile", "uint32", exynos_auto9_usi_spi_property_get, exynos_auto9_usi_spi_property_set, NULL, NULL);
    object_property_add(obj, "word-period-ns", "uint32", exynos_auto9_usi_spi_property_get, exynos_auto9_usi_spi_property_set, NULL, NULL);
#define SPI_OBSERVE(name, field) object_property_add_uint32_ptr(obj, name, &s->field, OBJ_PROP_FLAG_READ)
    SPI_OBSERVE("fifo-bytes", fifo_size); SPI_OBSERVE("selector", selector); SPI_OBSERVE("selector-reads", selector_reads); SPI_OBSERVE("selector-writes", selector_writes);
    SPI_OBSERVE("register-reads", reads); SPI_OBSERVE("register-writes", writes); SPI_OBSERVE("rejected-accesses", rejected);
    SPI_OBSERVE("control", control); SPI_OBSERVE("mode", mode); SPI_OBSERVE("active", active);
    SPI_OBSERVE("tx-count", tx_count); SPI_OBSERVE("rx-count", rx_count); SPI_OBSERVE("pending", pending);
    SPI_OBSERVE("software-resets", resets); SPI_OBSERVE("words-shifted", shifted); SPI_OBSERVE("loopback-words", loopback_words);
    SPI_OBSERVE("wire-words", wire_words); SPI_OBSERVE("irq-level", irq_level);
    SPI_OBSERVE("dma-tx-stop", dma_tx_stop); SPI_OBSERVE("dma-rx-stop", dma_rx_stop);
    SPI_OBSERVE("tx-overruns", tx_overruns); SPI_OBSERVE("rx-overruns", rx_overruns);
    SPI_OBSERVE("rx-underruns", rx_underruns);
    SPI_OBSERVE("last-offset", last_offset); SPI_OBSERVE("last-value", last_value);
#undef SPI_OBSERVE
}
static void exynos_auto9_usi_spi_class_init(ObjectClass *klass, const void *data)
{ device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_usi_spi_reset); }
static const TypeInfo exynos_auto9_usi_spi_type = {
    .name = TYPE_EXYNOS_AUTO9_USI_SPI, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9UsiSpi), .instance_init = exynos_auto9_usi_spi_init,
    .class_init = exynos_auto9_usi_spi_class_init,
};
static void exynos_auto9_usi_spi_register(void) { type_register_static(&exynos_auto9_usi_spi_type); }
type_init(exynos_auto9_usi_spi_register)
static void exynos_auto9_usi_spi_create(VirtMachineState *vms, unsigned port, hwaddr base, hwaddr selector, unsigned spi, int tx_req, int rx_req, unsigned fifo_size)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_USI_SPI);
    ExynosAuto9UsiSpi *s = EXYNOS_AUTO9_USI_SPI(dev);
    s->port = port; s->fifo_size = fifo_size;
    g_autofree char *name = g_strdup_printf("exynos-spi%u", port);
    g_autofree char *busname = g_strdup_printf("spi%u", port);
    s->bus = ssi_create_bus(dev, busname);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, spi));
    if (exynos_auto9_usi_spi_enabled(s)) {
        DeviceState *dma = DEVICE(object_resolve_path("/machine/exynos-pdma/engine", NULL));
        if (tx_req >= 0) { qdev_connect_gpio_out_named(dev, "dma-stop", 0, qdev_get_gpio_in(dma, tx_req)); }
        if (rx_req >= 0) { qdev_connect_gpio_out_named(dev, "dma-stop", 1, qdev_get_gpio_in(dma, rx_req)); }
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, selector);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, base);
    }
    if (port == 6 && vms->exynos_mcu_spi6_startup) {
        DeviceState *peer, *alive;
        if (s->profile != 2 || vms->exynos_mcu_spi6_startup != 1) {
            error_report("SPI6 startup peer requires the original Linux SPI profile");
            exit(EXIT_FAILURE);
        }
        peer = qdev_new(TYPE_AUTO9_MCU_SPI);
        alive = DEVICE(object_resolve_path_component(OBJECT(vms), "exynos-gpio5"));
        object_property_add_child(OBJECT(vms), "exynos-mcu-spi6", OBJECT(peer));
        ssi_realize_and_unref(peer, s->bus, &error_fatal);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1,
                           qdev_get_gpio_in_named(peer, SSI_GPIO_CS, 0));
        qdev_connect_gpio_out(peer, 0, qdev_get_gpio_in(alive, 0)); /* GPA0[0] */
        /* GPIO bank reset can run after the peer's reset callback. */
        EXYNOS_AUTO9_GPIO(alive)->reset_input_high |= 1;
        auto9_mcu_spi_request(AUTO9_MCU_SPI(peer), false);
    }
    exynos_auto9_usi_spi_update(s);
}
