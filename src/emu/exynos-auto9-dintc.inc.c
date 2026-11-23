/* SPDX-License-Identifier: GPL-2.0-or-later
 * Local ExynosAuto9 combiner model derived from the retained native firmware.
 * Bank offsets 0x280/0x2b0 and enable/disable/pending accesses are observed.
 * Pending clear/level reassertion is a model inference, not silicon validation.
 * No peripheral sources are invented. Unimplemented registers report errors.
 */
#define TYPE_EXYNOS_AUTO9_DINTC "exynos-auto9.dintc"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Dintc, EXYNOS_AUTO9_DINTC)

struct ExynosAuto9Dintc {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq irq;
    uint32_t enable[2];
    uint32_t pending[2];
    uint32_t level[2];
    uint32_t banks;
};

static void exynos_auto9_dintc_update(ExynosAuto9Dintc *s)
{
    bool active = false;
    unsigned i;
    for (i = 0; i < s->banks; i++) {
        active |= (s->enable[i] & s->pending[i]) != 0;
    }
    qemu_set_irq(s->irq, active);
}

static void exynos_auto9_dintc_input(void *opaque, int line, int level)
{
    ExynosAuto9Dintc *s = opaque;
    unsigned bank = line / 32;
    uint32_t bit = 1U << (line % 32);
    if (bank >= s->banks) {
        return;
    }
    if (level) {
        s->level[bank] |= bit;
        s->pending[bank] |= bit;
    } else {
        s->level[bank] &= ~bit;
    }
    exynos_auto9_dintc_update(s);
}

static int exynos_auto9_dintc_decode(ExynosAuto9Dintc *s, hwaddr *offset)
{
    unsigned bank;
    static const unsigned base[2] = {0x280, 0x2b0};
    for (bank = 0; bank < s->banks; bank++) {
        if (*offset >= base[bank] + 8 && *offset <= base[bank] + 20) {
            *offset -= base[bank];
            return bank;
        }
    }
    return -1;
}

static uint64_t exynos_auto9_dintc_read(void *opaque, hwaddr offset, unsigned size)
{
    ExynosAuto9Dintc *s = opaque;
    hwaddr original = offset;
    int bank = exynos_auto9_dintc_decode(s, &offset);
    if (bank >= 0) {
        switch (offset) {
        case 8:
        case 12:
            return s->enable[bank];
        case 16:
            return s->pending[bank] & s->enable[bank];
        case 20:
            return s->pending[bank];
        }
    }
    qemu_log_mask(LOG_UNIMP, "exynos-auto9.dintc unimplemented read 0x%llx\n",
                  (unsigned long long)original);
    return 0;
}

static void exynos_auto9_dintc_write(void *opaque, hwaddr offset,
                                    uint64_t value, unsigned size)
{
    ExynosAuto9Dintc *s = opaque;
    hwaddr original = offset;
    int bank = exynos_auto9_dintc_decode(s, &offset);
    if (bank >= 0) {
        switch (offset) {
        case 8:
            s->enable[bank] |= value;
            break;
        case 12:
            s->enable[bank] &= ~value;
            break;
        case 20:
            s->pending[bank] &= ~value;
            s->pending[bank] |= s->level[bank];
            break;
        default:
            goto unimplemented;
        }
        exynos_auto9_dintc_update(s);
        return;
    }
unimplemented:
    qemu_log_mask(LOG_UNIMP, "exynos-auto9.dintc unimplemented write 0x%llx\n",
                  (unsigned long long)original);
}

static const MemoryRegionOps exynos_auto9_dintc_ops = {
    .read = exynos_auto9_dintc_read,
    .write = exynos_auto9_dintc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static void exynos_auto9_dintc_reset(DeviceState *dev)
{
    ExynosAuto9Dintc *s = EXYNOS_AUTO9_DINTC(dev);
    memset(s->enable, 0, sizeof(s->enable));
    memset(s->pending, 0, sizeof(s->pending));
    memset(s->level, 0, sizeof(s->level));
    exynos_auto9_dintc_update(s);
}

static void exynos_auto9_dintc_init(Object *obj)
{
    ExynosAuto9Dintc *s = EXYNOS_AUTO9_DINTC(obj);
    qdev_init_gpio_in(DEVICE(obj), exynos_auto9_dintc_input, 64);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    memory_region_init_io(&s->iomem, obj, &exynos_auto9_dintc_ops,
                          s, TYPE_EXYNOS_AUTO9_DINTC, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
}

static const Property exynos_auto9_dintc_properties[] = {
    DEFINE_PROP_UINT32("banks", ExynosAuto9Dintc, banks, 2),
};

static void exynos_auto9_dintc_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Dintc *s = EXYNOS_AUTO9_DINTC(dev);
    if (s->banks < 1 || s->banks > 2) {
        error_setg(errp, "exynos-auto9.dintc banks must be 1 or 2");
    }
}

static void exynos_auto9_dintc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = exynos_auto9_dintc_realize;
    device_class_set_legacy_reset(dc, exynos_auto9_dintc_reset);
    device_class_set_props(dc, exynos_auto9_dintc_properties);
    dc->desc = "Local Auto9 DINTC, observed register subset; no migration";
}

static const TypeInfo exynos_auto9_dintc_type = {
    .name = TYPE_EXYNOS_AUTO9_DINTC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Dintc),
    .instance_init = exynos_auto9_dintc_init,
    .class_init = exynos_auto9_dintc_class_init,
};

static void exynos_auto9_dintc_register(void)
{
    type_register_static(&exynos_auto9_dintc_type);
}
type_init(exynos_auto9_dintc_register)

static void exynos_auto9_dintc_create(VirtMachineState *vms, unsigned number,
                                     hwaddr base, unsigned spi, unsigned banks)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_DINTC);
    g_autofree char *name = g_strdup_printf("exynos-dintc%u", number);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    qdev_prop_set_uint32(dev, "banks", banks);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, spi));
}
