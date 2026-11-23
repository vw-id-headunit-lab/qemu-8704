/* SPDX-License-Identifier: GPL-2.0-or-later
 * Auto9 QNX USI09 and optional Linux USI03/04/08 UARTs.
 * Reuses upstream Samsung UART FIFOs/IRQ/chardev, not a printable RAM window.
 * Cold selector0 and finite C4 reset/C8 options are behavior-level assumptions.
 * Only observed UART mode is implemented here; SPI/I2C selections reject.
 */
#define TYPE_EXYNOS_AUTO9_USI_UART "exynos-auto9.usi-uart"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9UsiUart, EXYNOS_AUTO9_USI_UART)
struct ExynosAuto9UsiUart {
    SysBusDevice parent_obj;
    MemoryRegion selector_mmio, config_mmio, uintm_mmio;
    DeviceState *uart;
    MemoryRegion *uart_mmio;
    qemu_irq irq;
    bool wide_uintm;
    uint32_t selector, control, option, irq_level;
    uint32_t selector_reads, selector_writes, config_reads, config_writes, resets, rejected;
    uint32_t uintm_wide_reads, uintm_wide_writes, uintm_wide_rejected;
};
static void exynos_auto9_usi_uart_update(ExynosAuto9UsiUart *s)
{
    bool enabled = s->selector == 1 && !(s->control & 1);
    if (s->uart_mmio) { memory_region_set_enabled(s->uart_mmio, enabled); }
    if (s->wide_uintm) { memory_region_set_enabled(&s->uintm_mmio, enabled); }
    qemu_set_irq(s->irq, enabled && s->irq_level);
}
static void exynos_auto9_usi_uart_irq(void *opaque, int line, int level)
{
    ExynosAuto9UsiUart *s = opaque; s->irq_level = level;
    exynos_auto9_usi_uart_update(s);
}
static MemTxResult exynos_auto9_usi_uart_read(void *opaque, hwaddr off,
                                            uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UsiUart *s = opaque;
    if (off == 0) { *value = s->selector; s->selector_reads++; }
    else { *value = off == 4 ? s->control : s->option; s->config_reads++; }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_usi_uart_write(void *opaque, hwaddr off,
                                             uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UsiUart *s = opaque;
    if ((off == 0 && value > 1) || (off == 4 && value > 1) || (off == 8 && (value & ~6ULL))) {
        s->rejected++;
        qemu_log_mask(LOG_UNIMP, "Auto9 USI UART unsupported config offset0x%llx value0x%llx\n",
                      (unsigned long long)off, (unsigned long long)value);
        return MEMTX_DECODE_ERROR;
    }
    if (off == 0) { s->selector = value; s->selector_writes++; }
    else if (off == 4) {
        s->control = value; s->config_writes++;
        if (value && s->uart) { device_cold_reset(s->uart); s->resets++; }
    } else { s->option = value; s->config_writes++; }
    exynos_auto9_usi_uart_update(s); return MEMTX_OK;
}
static MemTxResult exynos_auto9_usi_uart_config_read(void *opaque, hwaddr off,
                                                   uint64_t *value, unsigned size, MemTxAttrs attrs)
{ return exynos_auto9_usi_uart_read(opaque, off + 4, value, size, attrs); }
static MemTxResult exynos_auto9_usi_uart_config_write(void *opaque, hwaddr off,
                                                    uint64_t value, unsigned size, MemTxAttrs attrs)
{ return exynos_auto9_usi_uart_write(opaque, off + 4, value, size, attrs); }
static const MemoryRegionOps exynos_auto9_usi_uart_selector_ops = {
    .read_with_attrs = exynos_auto9_usi_uart_read, .write_with_attrs = exynos_auto9_usi_uart_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static const MemoryRegionOps exynos_auto9_usi_uart_config_ops = {
    .read_with_attrs = exynos_auto9_usi_uart_config_read, .write_with_attrs = exynos_auto9_usi_uart_config_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

/*
 * Original VM3 startup does an aligned 64-bit RMW of UINTM on USI08.
 * Keep the upstream 32-bit register and its interrupt side effects. The
 * adjacent unmapped lane is reserved in this access, not a new register.
 */
static MemTxResult exynos_auto9_usi_uart_uintm_read(void *opaque, hwaddr off,
                                                   uint64_t *value, unsigned size,
                                                   MemTxAttrs attrs)
{
    ExynosAuto9UsiUart *s = opaque;
    MemTxResult ret;
    if (off || (size != 4 && size != 8)) {
        s->uintm_wide_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    *value = 0;
    ret = memory_region_dispatch_read(s->uart_mmio, 0x38, value, MO_LEUL, attrs);
    if (ret == MEMTX_OK && size == 8) {
        *value = (uint32_t)*value;
        s->uintm_wide_reads++;
    }
    return ret;
}

static MemTxResult exynos_auto9_usi_uart_uintm_write(void *opaque, hwaddr off,
                                                    uint64_t value, unsigned size,
                                                    MemTxAttrs attrs)
{
    ExynosAuto9UsiUart *s = opaque;
    MemTxResult ret;
    if (off || (size != 4 && size != 8) || value >> 32) {
        s->uintm_wide_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    ret = memory_region_dispatch_write(s->uart_mmio, 0x38, value, MO_LEUL, attrs);
    if (ret == MEMTX_OK && size == 8) {
        s->uintm_wide_writes++;
    }
    return ret;
}

static const MemoryRegionOps exynos_auto9_usi_uart_uintm_ops = {
    .read_with_attrs = exynos_auto9_usi_uart_uintm_read,
    .write_with_attrs = exynos_auto9_usi_uart_uintm_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 8 },
    .impl = { .min_access_size = 4, .max_access_size = 8 },
};

static void exynos_auto9_usi_uart_reset(DeviceState *dev)
{
    ExynosAuto9UsiUart *s = EXYNOS_AUTO9_USI_UART(dev);
    s->selector = s->control = s->option = s->irq_level = 0;
    s->selector_reads = s->selector_writes = s->config_reads = s->config_writes = s->resets = s->rejected = 0;
    s->uintm_wide_reads = s->uintm_wide_writes = s->uintm_wide_rejected = 0;
    exynos_auto9_usi_uart_update(s);
}
static void exynos_auto9_usi_uart_init(Object *obj)
{
    ExynosAuto9UsiUart *s = EXYNOS_AUTO9_USI_UART(obj);
    memory_region_init_io(&s->selector_mmio, obj, &exynos_auto9_usi_uart_selector_ops, s, "exynos-auto9.usi-uart-selector", 4);
    memory_region_init_io(&s->config_mmio, obj, &exynos_auto9_usi_uart_config_ops, s, "exynos-auto9.usi-uart-config", 8);
    memory_region_init_io(&s->uintm_mmio, obj, &exynos_auto9_usi_uart_uintm_ops, s, "exynos-auto9.usi-uart-uintm", 8);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->selector_mmio); sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->config_mmio);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->uintm_mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    qdev_init_gpio_in(DEVICE(obj), exynos_auto9_usi_uart_irq, 1);
    object_property_add_uint32_ptr(obj, "selector", &s->selector, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "control", &s->control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "option", &s->option, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "selector-reads", &s->selector_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "selector-writes", &s->selector_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "config-reads", &s->config_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "config-writes", &s->config_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "software-resets", &s->resets, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "uintm-wide-reads", &s->uintm_wide_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "uintm-wide-writes", &s->uintm_wide_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "uintm-wide-rejected", &s->uintm_wide_rejected, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_usi_uart_class_init(ObjectClass *klass, const void *data)
{ device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_usi_uart_reset); }
static const TypeInfo exynos_auto9_usi_uart_type = {
    .name = TYPE_EXYNOS_AUTO9_USI_UART, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9UsiUart), .instance_init = exynos_auto9_usi_uart_init,
    .class_init = exynos_auto9_usi_uart_class_init,
};
static void exynos_auto9_usi_uart_register(void) { type_register_static(&exynos_auto9_usi_uart_type); }
type_init(exynos_auto9_usi_uart_register)
static void exynos_auto9_usi_uart_create(VirtMachineState *vms, const char *name,
                                          hwaddr base, hwaddr selector, unsigned spi,
                                          unsigned fifo, unsigned uart_channel,
                                          unsigned backend)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_USI_UART);
    ExynosAuto9UsiUart *s = EXYNOS_AUTO9_USI_UART(dev);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, spi));
    s->uart = exynos4210_uart_create(base, fifo, uart_channel, serial_hd(backend),
                                     qdev_get_gpio_in(dev, 0));
    s->uart_mmio = sysbus_mmio_get_region(SYS_BUS_DEVICE(s->uart), 0);
    /* Original s3c64xx UINTM RMW is 64-bit at +0x38 on each Linux USI UART. */
    s->wide_uintm = base == 0x10360000 || base == 0x10380000 || base == 0x10940000;
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, selector);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, base + 0xc4);
    if (s->wide_uintm) {
        sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 2, base + 0x38, 1);
    }
    exynos_auto9_usi_uart_update(s);
}
