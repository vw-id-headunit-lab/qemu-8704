/* SPDX-License-Identifier: GPL-2.0-or-later
 * Chip identity, not a functional-device success stub. Product ID is specified
 * by Samsung's upstream exynos-chipid driver; revision is an explicit virtual
 * board property. UID is configurable virtual identity, not a physical ECU ID.
 */
#define TYPE_EXYNOS_AUTO9_CHIPID "exynos-auto9.chipid"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Chipid, EXYNOS_AUTO9_CHIPID)
struct ExynosAuto9Chipid {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    uint32_t revision;
    uint32_t product_variant;
    uint64_t uid;
};
static MemTxResult exynos_auto9_chipid_read(void *opaque, hwaddr offset,
                                          uint64_t *value, unsigned size,
                                          MemTxAttrs attrs)
{
    ExynosAuto9Chipid *s = opaque;
    if (offset == 0) {
        *value = 0xaaa80000;
    } else if (offset == 4) {
        *value = s->uid & UINT32_MAX;
    } else if (offset == 8) {
        *value = s->uid >> 32;
    } else if (offset == 0x10) {
        *value = s->revision << 20;
    } else if (offset == 0x24) {
        /* Native variant table adds bits[1:0] to the product ID. */
        *value = s->product_variant;
    } else {
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.chipid unknown read 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_chipid_write(void *opaque, hwaddr offset,
                                           uint64_t value, unsigned size,
                                           MemTxAttrs attrs)
{
    return MEMTX_ACCESS_ERROR; /* Identity fuses are not writable MMIO. */
}
static const MemoryRegionOps exynos_auto9_chipid_ops = {
    .read_with_attrs = exynos_auto9_chipid_read,
    .write_with_attrs = exynos_auto9_chipid_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_chipid_init(Object *obj)
{
    ExynosAuto9Chipid *s = EXYNOS_AUTO9_CHIPID(obj);
    memory_region_init_io(&s->iomem, obj, &exynos_auto9_chipid_ops, s,
                          TYPE_EXYNOS_AUTO9_CHIPID, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
}
static const Property exynos_auto9_chipid_properties[] = {
    DEFINE_PROP_UINT32("revision", ExynosAuto9Chipid, revision, 1),
    DEFINE_PROP_UINT32("product-variant", ExynosAuto9Chipid, product_variant, 0),
    DEFINE_PROP_UINT64("uid", ExynosAuto9Chipid, uid, 0),
};
static void exynos_auto9_chipid_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Chipid *s = EXYNOS_AUTO9_CHIPID(dev);
    if (s->revision > 15 || s->product_variant > 3) {
        error_setg(errp, "Auto9 ChipID revision/variant exceeds field width");
    }
}
static void exynos_auto9_chipid_class_init(ObjectClass *klass, const void *data)
{
    DEVICE_CLASS(klass)->realize = exynos_auto9_chipid_realize;
    device_class_set_props(DEVICE_CLASS(klass), exynos_auto9_chipid_properties);
}
static const TypeInfo exynos_auto9_chipid_type = {
    .name = TYPE_EXYNOS_AUTO9_CHIPID, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Chipid),
    .instance_init = exynos_auto9_chipid_init,
    .class_init = exynos_auto9_chipid_class_init,
};
static void exynos_auto9_chipid_register(void)
{
    type_register_static(&exynos_auto9_chipid_type);
}
type_init(exynos_auto9_chipid_register)

static void exynos_auto9_chipid_create(VirtMachineState *vms)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_CHIPID);
    object_property_add_child(OBJECT(vms), "exynos-chipid", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10000000);
}
