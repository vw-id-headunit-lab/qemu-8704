/* SPDX-License-Identifier: GPL-2.0-or-later
 * Native normal-GPIO/EINT layout. Bank widths and parent IRQs come from HCB.
 * Pin mux, input/output levels, edge/level pending, W1C and service arbitration.
 * Filter configuration is retained; analogue/debounce timing is not emulated.
 */
#define TYPE_EXYNOS_AUTO9_GPIO "exynos-auto9.gpio"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Gpio, EXYNOS_AUTO9_GPIO)

struct ExynosAuto9Gpio {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq irq;
    qemu_irq outputs[64];
    qemu_irq pin_irqs[64];
    uint32_t bank_pins; /* 4-bit width per bank, zero terminates */
    unsigned banks;
    uint32_t config[8][6];
    uint32_t input[8];
    uint64_t reset_input_high; /* Board-connected peripheral idle levels. */
    uint32_t eint_config[8];
    uint32_t filter[8][2];
    uint32_t mask[8];
    uint32_t pending[8];
};

static unsigned exynos_auto9_gpio_width(ExynosAuto9Gpio *s, unsigned bank)
{
    return (s->bank_pins >> (bank * 4)) & 15;
}

static void exynos_auto9_gpio_update(ExynosAuto9Gpio *s)
{
    bool active = false;
    for (unsigned bank = 0; bank < s->banks; bank++) {
        for (unsigned pin = 0; pin < exynos_auto9_gpio_width(s, bank); pin++) {
            unsigned mux = (s->config[bank][0] >> (pin * 4)) & 15;
            unsigned trigger = (s->eint_config[bank] >> (pin * 4)) & 15;
            uint32_t bit = 1U << pin;
            bool level = (s->input[bank] & bit) != 0;
            qemu_set_irq(s->outputs[bank * 8 + pin],
                         mux == 1 && (s->config[bank][1] & bit));
            if (mux == 15 && ((trigger == 0 && !level) ||
                              (trigger == 1 && level))) {
                s->pending[bank] |= bit;
            }
        }
        active |= (s->pending[bank] & ~s->mask[bank]) != 0;
        for (unsigned pin = 0; pin < 8; pin++) {
            qemu_set_irq(s->pin_irqs[bank * 8 + pin],
                         (s->pending[bank] & ~s->mask[bank] & (1U << pin)) != 0);
        }
    }
    qemu_set_irq(s->irq, active);
}

static void exynos_auto9_gpio_input(void *opaque, int line, int level)
{
    ExynosAuto9Gpio *s = opaque;
    unsigned bank = line / 8;
    unsigned pin = line % 8;
    if (bank >= s->banks || pin >= exynos_auto9_gpio_width(s, bank)) {
        return;
    }
    uint32_t bit = 1U << pin;
    bool old = (s->input[bank] & bit) != 0;
    if (level) {
        s->input[bank] |= bit;
    } else {
        s->input[bank] &= ~bit;
    }
    unsigned mux = (s->config[bank][0] >> (pin * 4)) & 15;
    unsigned trigger = (s->eint_config[bank] >> (pin * 4)) & 15;
    if (mux == 15 && ((trigger == 2 && old && !level) ||
                      (trigger == 3 && !old && level) ||
                      (trigger == 4 && old != (bool)level))) {
        s->pending[bank] |= bit;
    }
    exynos_auto9_gpio_update(s);
}

static MemTxResult exynos_auto9_gpio_read(void *opaque, hwaddr offset,
                                        uint64_t *value, unsigned size,
                                        MemTxAttrs attrs)
{
    ExynosAuto9Gpio *s = opaque;
    unsigned bank = offset / 32;
    unsigned reg = (offset % 32) / 4;
    if (bank < s->banks && reg < 6) {
        *value = s->config[bank][reg];
        if (reg == 1) {
            *value = 0;
            for (unsigned pin = 0; pin < exynos_auto9_gpio_width(s, bank); pin++) {
                bool output = ((s->config[bank][0] >> (pin * 4)) & 15) == 1;
                *value |= (output ? s->config[bank][1] : s->input[bank]) & (1U << pin);
            }
        }
        return MEMTX_OK;
    }
    if (offset >= 0x700 && offset < 0x700 + s->banks * 4) {
        *value = s->eint_config[(offset - 0x700) / 4];
    } else if (offset >= 0x800 && offset < 0x800 + s->banks * 8) {
        *value = s->filter[(offset - 0x800) / 8][(offset / 4) & 1];
    } else if (offset >= 0x900 && offset < 0x900 + s->banks * 4) {
        *value = s->mask[(offset - 0x900) / 4];
    } else if (offset >= 0xa00 && offset < 0xa00 + s->banks * 4) {
        *value = s->pending[(offset - 0xa00) / 4];
    } else if (offset == 0xb08) {
        *value = 0;
        for (unsigned i = 0; i < s->banks; i++) {
            uint32_t pending = s->pending[i] & ~s->mask[i];
            if (pending) {
                *value = ((i + 1) << 3) | ctz32(pending);
                break;
            }
        }
    } else {
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.gpio unknown read 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_gpio_write(void *opaque, hwaddr offset,
                                         uint64_t value, unsigned size,
                                         MemTxAttrs attrs)
{
    ExynosAuto9Gpio *s = opaque;
    unsigned bank = offset / 32;
    unsigned reg = (offset % 32) / 4;
    if (bank < s->banks && reg < 6) {
        s->config[bank][reg] = value;
    } else if (offset >= 0x700 && offset < 0x700 + s->banks * 4) {
        s->eint_config[(offset - 0x700) / 4] = value;
    } else if (offset >= 0x800 && offset < 0x800 + s->banks * 8) {
        s->filter[(offset - 0x800) / 8][(offset / 4) & 1] = value;
    } else if (offset >= 0x900 && offset < 0x900 + s->banks * 4) {
        bank = (offset - 0x900) / 4;
        s->mask[bank] = value & ((1U << exynos_auto9_gpio_width(s, bank)) - 1);
    } else if (offset >= 0xa00 && offset < 0xa00 + s->banks * 4) {
        s->pending[(offset - 0xa00) / 4] &= ~value;
    } else {
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.gpio unknown write 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    exynos_auto9_gpio_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_gpio_ops = {
    .read_with_attrs = exynos_auto9_gpio_read,
    .write_with_attrs = exynos_auto9_gpio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static void exynos_auto9_gpio_reset(DeviceState *dev)
{
    ExynosAuto9Gpio *s = EXYNOS_AUTO9_GPIO(dev);
    memset(s->config, 0, sizeof(s->config));
    memset(s->input, 0, sizeof(s->input));
    memset(s->eint_config, 0, sizeof(s->eint_config));
    memset(s->filter, 0, sizeof(s->filter));
    memset(s->pending, 0, sizeof(s->pending));
    for (unsigned i = 0; i < s->banks; i++) {
        s->input[i] = (s->reset_input_high >> (i * 8)) & 0xff;
        s->mask[i] = (1U << exynos_auto9_gpio_width(s, i)) - 1;
    }
    exynos_auto9_gpio_update(s);
}

static void exynos_auto9_gpio_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Gpio *s = EXYNOS_AUTO9_GPIO(dev);
    s->banks = 0;
    for (unsigned i = 0; i < 8; i++) {
        unsigned width = exynos_auto9_gpio_width(s, i);
        if (width > 8 || (width && s->banks != i)) {
            error_setg(errp, "Invalid Auto9 GPIO bank widths");
            return;
        }
        if (width) {
            s->banks++;
        }
    }
    if (!s->banks) {
        error_setg(errp, "Auto9 GPIO requires at least one bank");
    }
}

static void exynos_auto9_gpio_init(Object *obj)
{
    ExynosAuto9Gpio *s = EXYNOS_AUTO9_GPIO(obj);
    memory_region_init_io(&s->iomem, obj, &exynos_auto9_gpio_ops, s,
                          TYPE_EXYNOS_AUTO9_GPIO, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    qdev_init_gpio_in(DEVICE(obj), exynos_auto9_gpio_input, 64);
    qdev_init_gpio_out(DEVICE(obj), s->outputs, 64);
    qdev_init_gpio_out_named(DEVICE(obj), s->pin_irqs, "pin-irq", 64);
}

static const Property exynos_auto9_gpio_properties[] = {
    DEFINE_PROP_UINT32("bank-pins", ExynosAuto9Gpio, bank_pins, 0),
};

static void exynos_auto9_gpio_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = exynos_auto9_gpio_realize;
    device_class_set_legacy_reset(dc, exynos_auto9_gpio_reset);
    device_class_set_props(dc, exynos_auto9_gpio_properties);
}

static const TypeInfo exynos_auto9_gpio_type = {
    .name = TYPE_EXYNOS_AUTO9_GPIO, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Gpio),
    .instance_init = exynos_auto9_gpio_init,
    .class_init = exynos_auto9_gpio_class_init,
};

static void exynos_auto9_gpio_register(void)
{
    type_register_static(&exynos_auto9_gpio_type);
}
type_init(exynos_auto9_gpio_register)

static DeviceState *exynos_auto9_gpio_create(VirtMachineState *vms, unsigned number,
                                   hwaddr base, uint32_t pins, unsigned spi)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_GPIO);
    g_autofree char *name = g_strdup_printf("exynos-gpio%u", number);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    qdev_prop_set_uint32(dev, "bank-pins", pins);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
    if (spi < 512) {
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, spi));
    }
    return dev;
}
