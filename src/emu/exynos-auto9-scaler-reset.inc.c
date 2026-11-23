/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original VM3 Scaler POLY discovery/stop/reset. No scaler jobs or DMA.
 */
#define TYPE_AUTO9_SCALER_RESET "exynos-auto9.scaler-reset"
#define AUTO9_SCALER_RESET_NS 128
#define AUTO9_SCALER_CFG 0x4
#define AUTO9_SCALER_SW_RESET BIT(1)
#define AUTO9_SCALER_STOP_ACK BIT(2)
#define AUTO9_SCALER_STOP_REQ BIT(3)
#define AUTO9_SCALER_VERSION 0x260
#define AUTO9_SCALER_POLY_PROBE_ID 0x04000002
OBJECT_DECLARE_SIMPLE_TYPE(Auto9ScalerReset, AUTO9_SCALER_RESET)

struct Auto9ScalerReset {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    QEMUTimer *timer;
    uint32_t cfg, reset_requests, reset_completions, cfg_reads;
    uint32_t rejected, last_offset, last_value;
    uint32_t version, version_reads, stop_requests, stop_acks;
};

static void auto9_scaler_done(void *opaque)
{
    Auto9ScalerReset *s = opaque;
    if (s->cfg == AUTO9_SCALER_STOP_REQ) {
        /* No job engine is exposed, so no transfer remains after quiesce. */
        s->cfg |= AUTO9_SCALER_STOP_ACK;
        s->stop_acks++;
    } else if (s->cfg == AUTO9_SCALER_SW_RESET) {
        s->cfg = 0;
        s->reset_completions++;
    }
}

static MemTxResult auto9_scaler_read(void *opaque, hwaddr off, uint64_t *value,
                                     unsigned size, MemTxAttrs attrs)
{
    Auto9ScalerReset *s = opaque;
    if (off == AUTO9_SCALER_VERSION && s->version) {
        s->version_reads++;
        *value = s->version;
        return MEMTX_OK;
    }
    if (off != AUTO9_SCALER_CFG) {
        s->rejected++;
        s->last_offset = off;
        return MEMTX_DECODE_ERROR;
    }
    s->cfg_reads++;
    *value = s->cfg;
    return MEMTX_OK;
}

static MemTxResult auto9_scaler_write(void *opaque, hwaddr off, uint64_t value,
                                      unsigned size, MemTxAttrs attrs)
{
    Auto9ScalerReset *s = opaque;
    s->last_offset = off;
    s->last_value = value;
    if (off == AUTO9_SCALER_CFG && s->version && value == AUTO9_SCALER_STOP_REQ &&
        !s->cfg) {
        s->cfg = AUTO9_SCALER_STOP_REQ;
        s->stop_requests++;
        timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_SCALER_RESET_NS);
        return MEMTX_OK;
    }
    /* The stock POLY probe can write SW_RESET directly for rev 0x50100
     * (sc_probe +0x4e0 branches to +0x530). No jobs exist in this model:
     * a cold/idle device is already quiescent. If STOP was explicitly
     * requested, the guest still has to wait for its real timed ACK. */
    if (off != AUTO9_SCALER_CFG || value != AUTO9_SCALER_SW_RESET ||
        (s->version && s->cfg && s->cfg !=
         (AUTO9_SCALER_STOP_REQ | AUTO9_SCALER_STOP_ACK))) {
        s->rejected++;
        return MEMTX_DECODE_ERROR;
    }
    s->cfg = AUTO9_SCALER_SW_RESET;
    s->reset_requests++;
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_SCALER_RESET_NS);
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_scaler_ops = {
    .read_with_attrs = auto9_scaler_read,
    .write_with_attrs = auto9_scaler_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_scaler_reset(DeviceState *dev)
{
    Auto9ScalerReset *s = AUTO9_SCALER_RESET(dev);
    timer_del(s->timer);
    s->cfg = s->reset_requests = s->reset_completions = s->cfg_reads = 0;
    s->rejected = s->last_offset = s->last_value = 0;
    s->version_reads = s->stop_requests = s->stop_acks = 0;
}

static void auto9_scaler_init(Object *obj)
{
    Auto9ScalerReset *s = AUTO9_SCALER_RESET(obj);
    memory_region_init_io(&s->mmio, obj, &auto9_scaler_ops, s, "auto9-scaler-reset", 0x3000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_scaler_done, s);
    object_property_add_uint32_ptr(obj, "cfg", &s->cfg, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-requests", &s->reset_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-completions", &s->reset_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cfg-reads", &s->cfg_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-offset", &s->last_offset, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-value", &s->last_value, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "version", &s->version, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "version-reads", &s->version_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "stop-requests", &s->stop_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "stop-acks", &s->stop_acks, OBJ_PROP_FLAG_READ);
}

static void auto9_scaler_finalize(Object *obj)
{
    timer_free(AUTO9_SCALER_RESET(obj)->timer);
}

static void auto9_scaler_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_scaler_reset);
}

static const TypeInfo auto9_scaler_type = {
    .name = TYPE_AUTO9_SCALER_RESET,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9ScalerReset),
    .instance_init = auto9_scaler_init,
    .instance_finalize = auto9_scaler_finalize,
    .class_init = auto9_scaler_class_init,
};
static void auto9_scaler_register(void) { type_register_static(&auto9_scaler_type); }
type_init(auto9_scaler_register)

static void auto9_scaler_create(VirtMachineState *vms)
{
    if (vms->exynos_scaler_reset) {
        DeviceState *dev = qdev_new(TYPE_AUTO9_SCALER_RESET);
        if (vms->exynos_scaler_reset == 2) {
            AUTO9_SCALER_RESET(dev)->version = AUTO9_SCALER_POLY_PROBE_ID;
        }
        object_property_add_child(OBJECT(vms), "exynos-scaler-reset", OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x19b10000);
    }
}

