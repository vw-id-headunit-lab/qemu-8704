/* SPDX-License-Identifier: GPL-2.0-or-later
 * ExynosAuto9 VGEN control plane. Register layouts are recovered from the
 * original _vgen_init/_vgen_dump. No DMA engine is fabricated by this device:
 * future physical masters must consume its VMID routing rather than bypass it.
 * Unknown offsets cause a bus decode error, not a fabricated successful read.
 */
#define TYPE_EXYNOS_AUTO9_VGEN "exynos-auto9.vgen"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Vgen, EXYNOS_AUTO9_VGEN)

struct ExynosAuto9Vgen {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    uint32_t layout; /* 0=indexed, 1=lite, 2=UFS, 3=DPU, 4=G2D */
    uint32_t index;
    uint32_t indexed[16][4]; /* VINFO, MATCHER0, MATCHER1, DEFAULT_CX_CFG */
    uint32_t vid[8];
    uint32_t g2d_global[3];
    uint32_t g2d_jobs[16][4];
};

static uint32_t *exynos_auto9_vgen_reg(ExynosAuto9Vgen *s, hwaddr offset)
{
    if (offset & 3) {
        return NULL;
    }
    switch (s->layout) {
    case 0:
        if (offset == 0) {
            return &s->index;
        }
        if (offset >= 4 && offset <= 16) {
            return &s->indexed[s->index & 15][offset / 4 - 1];
        }
        break;
    case 1:
    case 2:
        if (offset < 32) {
            return &s->vid[offset / 4];
        }
        break;
    case 3:
        if (offset >= 0x1004 && offset <= 0x8004 &&
            (offset & 0xfff) == 4) {
            return &s->vid[offset / 4096 - 1];
        }
        break;
    case 4:
        if (offset >= 0x44 && offset <= 0x4c) {
            return &s->g2d_global[(offset - 0x44) / 4];
        }
        if (offset >= 0x50 && offset < 0x150) {
            return &s->g2d_jobs[(offset - 0x50) / 16][(offset / 4) & 3];
        }
        break;
    }
    return NULL;
}

static MemTxResult exynos_auto9_vgen_read(void *opaque, hwaddr offset,
                                         uint64_t *value, unsigned size,
                                         MemTxAttrs attrs)
{
    ExynosAuto9Vgen *s = opaque;
    uint32_t *reg = exynos_auto9_vgen_reg(s, offset);
    if (!reg) {
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.vgen layout%u unknown read 0x%llx\n",
                      s->layout, (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    *value = *reg;
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_vgen_write(void *opaque, hwaddr offset,
                                          uint64_t value, unsigned size,
                                          MemTxAttrs attrs)
{
    ExynosAuto9Vgen *s = opaque;
    uint32_t *reg = exynos_auto9_vgen_reg(s, offset);
    if (!reg) {
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.vgen layout%u unknown write 0x%llx\n",
                      s->layout, (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    *reg = value;
    if (s->layout == 0 && offset == 0) {
        s->index &= 15;
    }
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_vgen_ops = {
    .read_with_attrs = exynos_auto9_vgen_read,
    .write_with_attrs = exynos_auto9_vgen_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static void exynos_auto9_vgen_reset(DeviceState *dev)
{
    ExynosAuto9Vgen *s = EXYNOS_AUTO9_VGEN(dev);
    s->index = 0;
    memset(s->indexed, 0, sizeof(s->indexed));
    memset(s->vid, 0, sizeof(s->vid));
    memset(s->g2d_global, 0, sizeof(s->g2d_global));
    memset(s->g2d_jobs, 0, sizeof(s->g2d_jobs));
}

static void exynos_auto9_vgen_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Vgen *s = EXYNOS_AUTO9_VGEN(dev);
    if (s->layout > 4) {
        error_setg(errp, "exynos-auto9.vgen layout must be 0..4");
        return;
    }
    memory_region_init_io(&s->iomem, OBJECT(s), &exynos_auto9_vgen_ops, s,
                          TYPE_EXYNOS_AUTO9_VGEN, s->layout == 3 ? 0x9000 : 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->iomem);
}

static const Property exynos_auto9_vgen_properties[] = {
    DEFINE_PROP_UINT32("layout", ExynosAuto9Vgen, layout, 0),
};

static void exynos_auto9_vgen_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = exynos_auto9_vgen_realize;
    device_class_set_legacy_reset(dc, exynos_auto9_vgen_reset);
    device_class_set_props(dc, exynos_auto9_vgen_properties);
    dc->desc = "Auto9 VGEN configuration registers; DMA masters separate";
}

static const TypeInfo exynos_auto9_vgen_type = {
    .name = TYPE_EXYNOS_AUTO9_VGEN,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Vgen),
    .class_init = exynos_auto9_vgen_class_init,
};

static void exynos_auto9_vgen_register(void)
{
    type_register_static(&exynos_auto9_vgen_type);
}
type_init(exynos_auto9_vgen_register)

static void exynos_auto9_vgen_create(VirtMachineState *vms, unsigned number,
                                    hwaddr base, unsigned layout)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_VGEN);
    g_autofree char *name = g_strdup_printf("exynos-vgen%u", number);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    qdev_prop_set_uint32(dev, "layout", layout);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
}
