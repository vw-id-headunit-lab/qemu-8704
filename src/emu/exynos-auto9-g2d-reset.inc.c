/* SPDX-License-Identifier: GPL-2.0-or-later
 * Auto9 FIMG2D reset handshake only; no job execution, DMA or IRQ.
 */
#define TYPE_AUTO9_G2D_RESET "exynos-auto9.g2d-reset"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9G2dReset, AUTO9_G2D_RESET)
#define AUTO9_G2D_RESET_NS 128

struct Auto9G2dReset {
    SysBusDevice parent_obj;
    MemoryRegion regs;
    QEMUTimer *timer;
    uint32_t ready, resets, completions, status_reads, version_reads;
    uint32_t rejected, last_rejected, last_write;
};

static void auto9_g2d_reset_done(void *opaque)
{
    Auto9G2dReset *s = opaque;
    s->ready = 1;
    s->completions++;
}

static MemTxResult auto9_g2d_reject(Auto9G2dReset *s, hwaddr off, bool write)
{
    s->rejected++;
    s->last_rejected = off;
    s->last_write = write;
    qemu_log_mask(LOG_UNIMP, "Auto9 G2D unsupported %s at 0x%" HWADDR_PRIx "\n",
                  write ? "write" : "read", off);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_g2d_read(void *opaque, hwaddr off, uint64_t *value,
                                  unsigned size, MemTxAttrs attrs)
{
    Auto9G2dReset *s = opaque;
    if (off == 0x10) {
        *value = s->ready;
        s->status_reads++;
    } else if (off == 0x14) {
        /* Unspecified virtual version, not a claimed silicon revision. */
        *value = 0;
        s->version_reads++;
    } else {
        return auto9_g2d_reject(s, off, false);
    }
    return MEMTX_OK;
}

static MemTxResult auto9_g2d_write(void *opaque, hwaddr off, uint64_t value,
                                   unsigned size, MemTxAttrs attrs)
{
    Auto9G2dReset *s = opaque;
    if (off != 0 || value != 3) {
        return auto9_g2d_reject(s, off, true);
    }
    s->ready = 0;
    s->resets++;
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_G2D_RESET_NS);
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_g2d_ops = {
    .read_with_attrs = auto9_g2d_read,
    .write_with_attrs = auto9_g2d_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_g2d_reset(DeviceState *dev)
{
    Auto9G2dReset *s = AUTO9_G2D_RESET(dev);
    timer_del(s->timer);
    s->ready = s->resets = s->completions = s->status_reads = s->version_reads = 0;
    s->rejected = s->last_rejected = s->last_write = 0;
}

static void auto9_g2d_init(Object *obj)
{
    Auto9G2dReset *s = AUTO9_G2D_RESET(obj);
    memory_region_init_io(&s->regs, obj, &auto9_g2d_ops, s, "auto9-g2d-reset", 0xa000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regs);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_g2d_reset_done, s);
    object_property_add_uint32_ptr(obj, "ready", &s->ready, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "resets", &s->resets, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "completions", &s->completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "status-reads", &s->status_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "version-reads", &s->version_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected", &s->last_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected-write", &s->last_write, OBJ_PROP_FLAG_READ);
}

static void auto9_g2d_finalize(Object *obj)
{
    Auto9G2dReset *s = AUTO9_G2D_RESET(obj);
    timer_free(s->timer);
}

static void auto9_g2d_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_g2d_reset);
}

static const TypeInfo auto9_g2d_type = {
    .name = TYPE_AUTO9_G2D_RESET, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9G2dReset), .instance_init = auto9_g2d_init,
    .instance_finalize = auto9_g2d_finalize, .class_init = auto9_g2d_class_init,
};

static void auto9_g2d_register(void)
{
    type_register_static(&auto9_g2d_type);
}
type_init(auto9_g2d_register)

static void auto9_g2d_create(VirtMachineState *vms)
{
    if (!vms->exynos_g2d_reset) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_G2D_RESET);
    object_property_add_child(OBJECT(vms), "exynos-g2d-reset", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x19a20000);
}

