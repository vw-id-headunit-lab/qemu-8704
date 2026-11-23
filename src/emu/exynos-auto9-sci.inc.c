/* SPDX-License-Identifier: GPL-2.0-or-later
 * Bounded SCI/VCH configuration and asynchronous invalidation model.
 * Original ACPM sub_B13C issues bit0 and polls bit2 at SCI+0x488.
 * Interpretation of those bits is inferred from the shipped code. The
 * completion latency is an explicit virtual parameter, not silicon timing.
 * QEMU's coherent backing has no modeled LLC data lines to invalidate.
 */
#define TYPE_EXYNOS_AUTO9_SCI "exynos-auto9.sci"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Sci, EXYNOS_AUTO9_SCI)
typedef struct ExynosAuto9VchPort {
    ExynosAuto9Sci *sci;
    unsigned port;
} ExynosAuto9VchPort;
struct ExynosAuto9Sci {
    SysBusDevice parent_obj;
    MemoryRegion sci, vch[3], sysreg_lane;
    uint32_t registers[0x500 / 4];
    uint32_t channels[3][4];
    uint32_t completed;
    uint32_t lane_config;
    bool busy;
    uint64_t latency_ns;
    QEMUTimer *completion;
    ExynosAuto9VchPort ports[3];
};

static bool exynos_auto9_sci_config_offset(hwaddr offset)
{
    switch (offset) {
    case 0x10: case 0x30: case 0x38: case 0x60: case 0x80: case 0x88:
    case 0xa0: case 0xa8: case 0xb0: case 0x278: case 0x2a8:
    case 0x400: case 0x404: case 0x410: case 0x424: case 0x488:
        return true;
    default: return false;
    }
}
static void exynos_auto9_sci_complete(void *opaque)
{
    ExynosAuto9Sci *s = opaque;
    /* There are no hidden cache lines in this coherent virtual fabric. This
     * invalidates its empty LLC state, never erases the backing guest RAM.
     * Busy comes from actual queued work, not a fixed ready value on reads.
     */
    s->busy = false;
    s->completed++;
}
static MemTxResult exynos_auto9_sci_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    ExynosAuto9Sci *s = opaque;
    if (!exynos_auto9_sci_config_offset(offset)) {
        qemu_log_mask(LOG_UNIMP, "Auto9 SCI unsupported read 0x%llx\n", (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    *value = s->registers[offset / 4];
    if (offset == 0x488) { *value = (*value & ~4U) | (s->busy ? 4 : 0); }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_sci_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    ExynosAuto9Sci *s = opaque;
    if (!exynos_auto9_sci_config_offset(offset)) {
        qemu_log_mask(LOG_UNIMP, "Auto9 SCI unsupported write 0x%llx\n", (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    if (offset == 0x488) {
        if ((value & 1) && (s->latency_ns == 0 || s->latency_ns > 1000000000)) {
            qemu_log_mask(LOG_UNIMP, "Auto9 SCI invalid virtual operation latency\n");
            return MEMTX_DECODE_ERROR;
        }
        if ((value & 1) && s->busy) {
            qemu_log_mask(LOG_UNIMP, "Auto9 SCI overlapping command is not implemented\n");
            return MEMTX_DECODE_ERROR;
        }
        s->registers[offset / 4] = value & ~4U; /* Completion state is not guest-writable. */
        if (value & 1) {
            s->busy = true;
            timer_mod(s->completion, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->latency_ns);
        }
    } else {
        s->registers[offset / 4] = value;
    }
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_sci_ops = {
    .read_with_attrs = exynos_auto9_sci_read, .write_with_attrs = exynos_auto9_sci_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static MemTxResult exynos_auto9_vch_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9VchPort *p = opaque;
    unsigned words = p->port == 0 ? 4 : p->port == 1 ? 2 : 1;
    if (offset % 4 || offset / 4 >= words) { return MEMTX_DECODE_ERROR; }
    *value = p->sci->channels[p->port][offset / 4];
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_vch_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9VchPort *p = opaque;
    unsigned words = p->port == 0 ? 4 : p->port == 1 ? 2 : 1;
    if (offset % 4 || offset / 4 >= words) { return MEMTX_DECODE_ERROR; }
    p->sci->channels[p->port][offset / 4] = value;
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_vch_ops = {
    .read_with_attrs = exynos_auto9_vch_read, .write_with_attrs = exynos_auto9_vch_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

/* Original ACPM sub_20E72 RMWs AP1B221004's low byte; BL2 sub_984 also
 * configures it. Retain exactly this configuration word, not a SYSREG RAM
 * window or readiness register. Zero is the explicit virtual reset value. */
static uint64_t exynos_auto9_lane_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((ExynosAuto9Sci *)opaque)->lane_config;
}
static void exynos_auto9_lane_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    ((ExynosAuto9Sci *)opaque)->lane_config = value;
}
static const MemoryRegionOps exynos_auto9_lane_ops = {
    .read = exynos_auto9_lane_read, .write = exynos_auto9_lane_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_sci_reset(DeviceState *dev)
{
    ExynosAuto9Sci *s = EXYNOS_AUTO9_SCI(dev);
    s->lane_config = 0;
    timer_del(s->completion);
    memset(s->registers, 0, sizeof(s->registers));
    memset(s->channels, 0, sizeof(s->channels));
    s->busy = false;
    s->completed = 0;
}
static void exynos_auto9_sci_init(Object *obj)
{
    ExynosAuto9Sci *s = EXYNOS_AUTO9_SCI(obj);
    memory_region_init_io(&s->sysreg_lane, obj, &exynos_auto9_lane_ops, s,
                          "exynos-auto9-sysreg-lane-config", 4);
    memory_region_init_io(&s->sci, obj, &exynos_auto9_sci_ops, s, TYPE_EXYNOS_AUTO9_SCI, 0x10000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->sci);
    s->completion = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_sci_complete, s);
    object_property_add_uint32_ptr(obj, "invalidation-completions", &s->completed, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "invalidation-latency-ns", &s->latency_ns, OBJ_PROP_FLAG_READWRITE);
    s->latency_ns = 1000;
    for (unsigned i = 0; i < 3; i++) {
        ExynosAuto9VchPort *port = &s->ports[i];
        port->sci = s; port->port = i;
        memory_region_init_io(&s->vch[i], obj, &exynos_auto9_vch_ops, port, "exynos-auto9-vch-config", 0x1000);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->vch[i]);
    }
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->sysreg_lane);
    object_property_add_uint32_ptr(obj, "lane-config", &s->lane_config, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_sci_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_sci_reset);
}
static void exynos_auto9_sci_finalize(Object *obj)
{
    timer_free(EXYNOS_AUTO9_SCI(obj)->completion);
}
static const TypeInfo exynos_auto9_sci_type = {
    .name = TYPE_EXYNOS_AUTO9_SCI, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Sci), .instance_init = exynos_auto9_sci_init,
    .class_init = exynos_auto9_sci_class_init,
    .instance_finalize = exynos_auto9_sci_finalize,
};
static void exynos_auto9_sci_register(void) { type_register_static(&exynos_auto9_sci_type); }
type_init(exynos_auto9_sci_register)

static void exynos_auto9_sci_create(VirtMachineState *vms)
{
    DeviceState *dev;
    if (!vms->exynos_acpm) { return; }
    dev = qdev_new(TYPE_EXYNOS_AUTO9_SCI);
    object_property_add_child(OBJECT(vms), "exynos-sci", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x1b000000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, 0x1c251000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2, 0x1ce11000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 3, 0x1b901000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 4, 0x1b221004);
}
