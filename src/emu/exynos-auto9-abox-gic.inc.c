/* SPDX-License-Identifier: GPL-2.0-or-later
 * Independent ABOX GICv2 register file for the original VM3/VM4 drivers.
 * No DSP or audio IRQ wiring; never route these registers to the AP GIC.
 */
#define TYPE_AUTO9_ABOX_GIC "exynos-auto9.abox-gic"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9AboxGic, AUTO9_ABOX_GIC)

struct Auto9AboxGic {
    SysBusDevice parent_obj;
    MemoryRegion cpu_view, axiuser_reg, *cpu_regs;
    uint32_t eoi_writes, last_eoi, iar_reads, last_iar;
    uint32_t axiuser, axi_reads, axi_writes, axi_rejected;
};

static MemTxResult auto9_abox_cpu_read(void *opaque, hwaddr off, uint64_t *value,
                                       unsigned size, MemTxAttrs attrs)
{
    Auto9AboxGic *s = opaque;
    MemTxResult result = memory_region_dispatch_read(s->cpu_regs, off, value,
                                                    size_memop(size), attrs);
    if (result == MEMTX_OK && off == 0xc) {
        s->iar_reads++;
        s->last_iar = *value;
    }
    return result;
}

static MemTxResult auto9_abox_cpu_write(void *opaque, hwaddr off, uint64_t value,
                                        unsigned size, MemTxAttrs attrs)
{
    Auto9AboxGic *s = opaque;
    MemTxResult result = memory_region_dispatch_write(s->cpu_regs, off, value,
                                                     size_memop(size), attrs);
    if (result == MEMTX_OK && off == 0x10) {
        s->eoi_writes++;
        s->last_eoi = value;
    }
    return result;
}

static const MemoryRegionOps auto9_abox_cpu_ops = {
    .read_with_attrs = auto9_abox_cpu_read,
    .write_with_attrs = auto9_abox_cpu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* Shared SYSREG word observed by VM4 after its real vLM property read.
 * Eight two-bit AXIUSER config lanes. No stream/DSP is attached. */
static uint64_t auto9_abox_axi_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9AboxGic *s = opaque;
    s->axi_reads++;
    return s->axiuser;
}

static MemTxResult auto9_abox_axi_write(void *opaque, hwaddr off, uint64_t value,
                                      unsigned size, MemTxAttrs attrs)
{
    Auto9AboxGic *s = opaque;
    if (value & ~0xffffULL) {
        s->axi_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    s->axiuser = value;
    s->axi_writes++;
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_abox_axi_ops = {
    .read = auto9_abox_axi_read,
    .write_with_attrs = auto9_abox_axi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_abox_reset(DeviceState *dev)
{
    Auto9AboxGic *s = AUTO9_ABOX_GIC(dev);
    s->eoi_writes = s->last_eoi = s->iar_reads = s->last_iar = 0;
    s->axiuser = s->axi_reads = s->axi_writes = s->axi_rejected = 0;
}

static void auto9_abox_init(Object *obj)
{
    Auto9AboxGic *s = AUTO9_ABOX_GIC(obj);
    memory_region_init_io(&s->cpu_view, obj, &auto9_abox_cpu_ops, s,
                          "auto9-abox-gicc", 0x1004);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->cpu_view);
    object_property_add_uint32_ptr(obj, "eoi-writes", &s->eoi_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-eoi", &s->last_eoi, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "iar-reads", &s->iar_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-iar", &s->last_iar, OBJ_PROP_FLAG_READ);
}

static void auto9_abox_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_abox_reset);
}

static const TypeInfo auto9_abox_type = {
    .name = TYPE_AUTO9_ABOX_GIC, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9AboxGic), .instance_init = auto9_abox_init,
    .class_init = auto9_abox_class,
};
static void auto9_abox_register(void) { type_register_static(&auto9_abox_type); }
type_init(auto9_abox_register)

static void auto9_abox_gic_create(VirtMachineState *vms)
{
    if (!vms->exynos_abox_gic) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_ABOX_GIC);
    DeviceState *gic = qdev_new("arm_gic");
    object_property_add_child(OBJECT(vms), "exynos-abox-gic", OBJECT(dev));
    object_property_add_child(OBJECT(dev), "controller", OBJECT(gic));
    /* Explicit virtual count: covers the captured INTID 230. No DSP inputs. */
    qdev_prop_set_uint32(gic, "num-cpu", 1);
    qdev_prop_set_uint32(gic, "num-irq", 256);
    qdev_prop_set_uint32(gic, "revision", 2);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gic), &error_fatal);
    AUTO9_ABOX_GIC(dev)->cpu_regs = sysbus_mmio_get_region(SYS_BUS_DEVICE(gic), 1);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gic), 0, 0x19cf1000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x19cf2000);
    if (vms->exynos_abox_axiuser) {
        Auto9AboxGic *s = AUTO9_ABOX_GIC(dev);
        memory_region_init_io(&s->axiuser_reg, OBJECT(s), &auto9_abox_axi_ops, s,
                              "auto9-abox-axiuser", 4);
        memory_region_add_subregion(get_system_memory(), 0x19c1040c, &s->axiuser_reg);
        object_property_add_uint32_ptr(OBJECT(s), "axiuser", &s->axiuser, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "axi-reads", &s->axi_reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "axi-writes", &s->axi_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "axi-rejected", &s->axi_rejected, OBJ_PROP_FLAG_READ);
    }
}

