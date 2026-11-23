/* SPDX-License-Identifier: GPL-2.0-or-later
 * VM3 MFC cold bus quiesce, reset and shutdown; no firmware execution or DMA.
 */
#define TYPE_AUTO9_MFC "exynos-auto9.mfc-cold"
#define AUTO9_MFC_BUS_CTRL 0x7110
#define AUTO9_MFC_BUS_NS 20000
OBJECT_DECLARE_SIMPLE_TYPE(Auto9Mfc, AUTO9_MFC)

struct Auto9Mfc {
    SysBusDevice parent_obj;
    MemoryRegion regs;
    QEMUTimer *timer;
    uint32_t request, ack, reset_asserted, off;
    uint32_t bus_requests, bus_acks, bus_releases, resets, clear_words, poweroffs;
    uint32_t rejected;
};

static void auto9_mfc_bus_idle(void *opaque)
{
    Auto9Mfc *s = opaque;
    if (s->request) {
        /* There are no bus masters; quiesce follows a guest request. */
        s->ack = 1;
        s->bus_acks++;
    }
}

static MemTxResult auto9_mfc_bad(Auto9Mfc *s, hwaddr off, const char *op)
{
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 MFC unsupported %s offset 0x%" HWADDR_PRIx "\n", op, off);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_mfc_read(void *opaque, hwaddr off, uint64_t *value,
                                 unsigned size, MemTxAttrs attrs)
{
    Auto9Mfc *s = opaque;
    switch (off) {
    case AUTO9_MFC_BUS_CTRL: *value = s->request | (s->ack << 1); return MEMTX_OK;
    case 0x7124: *value = 0; return MEMTX_OK; /* No RISC core running. */
    case 0x7120: *value = s->off; return MEMTX_OK;
    }
    return auto9_mfc_bad(s, off, "read");
}

static MemTxResult auto9_mfc_write(void *opaque, hwaddr off, uint64_t value,
                                  unsigned size, MemTxAttrs attrs)
{
    Auto9Mfc *s = opaque;
    if (off == AUTO9_MFC_BUS_CTRL && !(value & ~UINT64_C(3))) {
        if (value & 1) {
            if (!s->request) {
                s->request = 1;
                s->bus_requests++;
                timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUTO9_MFC_BUS_NS);
            }
        } else {
            if (s->request) {
                s->bus_releases++;
            }
            s->request = s->ack = 0;
            timer_del(s->timer);
        }
        return MEMTX_OK;
    }
    if (!s->ack) {
        return auto9_mfc_bad(s, off, "write while bus not quiesced");
    }
    if (!value && (off == 0x1100 || off == 0x1104 || (off >= 0xf000 && off < 0x10000))) {
        /* Original cold reset clears the whole firmware mailbox page. */
        s->clear_words++;
        return MEMTX_OK;
    }
    if (off == 0x1070 && value == 0x1fff) {
        s->reset_asserted = 1;
        return MEMTX_OK;
    }
    if (off == 0x1070 && !value && s->reset_asserted) {
        s->reset_asserted = 0;
        s->resets++;
        return MEMTX_OK;
    }
    if (off == 0x7120 && value == 1 && s->resets) {
        s->off = 1;
        s->poweroffs++;
        return MEMTX_OK;
    }
    return auto9_mfc_bad(s, off, "write");
}

static const MemoryRegionOps auto9_mfc_ops = {
    .read_with_attrs = auto9_mfc_read,
    .write_with_attrs = auto9_mfc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_mfc_reset(DeviceState *dev)
{
    Auto9Mfc *s = AUTO9_MFC(dev);
    timer_del(s->timer);
    s->request = s->ack = s->reset_asserted = s->off = s->rejected = 0;
    s->bus_requests = s->bus_acks = s->bus_releases = s->resets = 0;
    s->clear_words = s->poweroffs = 0;
}

static void auto9_mfc_init(Object *obj)
{
    Auto9Mfc *s = AUTO9_MFC(obj);
    memory_region_init_io(&s->regs, obj, &auto9_mfc_ops, s, "auto9-mfc-cold", 0x10000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regs);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_mfc_bus_idle, s);
    const char *const names[] = {"bus-requests","bus-acks","bus-releases","resets",
                                 "clear-words","poweroffs","rejected"};
    uint32_t *const values[] = {&s->bus_requests,&s->bus_acks,&s->bus_releases,&s->resets,
                                &s->clear_words,&s->poweroffs,&s->rejected};
    for (unsigned i = 0; i < G_N_ELEMENTS(names); i++) {
        object_property_add_uint32_ptr(obj, names[i], values[i], OBJ_PROP_FLAG_READ);
    }
}

static void auto9_mfc_finalize(Object *obj)
{
    timer_free(AUTO9_MFC(obj)->timer);
}

static void auto9_mfc_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_mfc_reset);
}

static const TypeInfo auto9_mfc_type = {
    .name = TYPE_AUTO9_MFC, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9Mfc),
    .instance_init = auto9_mfc_init, .instance_finalize = auto9_mfc_finalize,
    .class_init = auto9_mfc_class,
};

static void auto9_mfc_register(void)
{
    type_register_static(&auto9_mfc_type);
}
type_init(auto9_mfc_register)

static void auto9_mfc_create(VirtMachineState *vms)
{
    if (vms->exynos_linux_mfc) {
        DeviceState *dev = qdev_new(TYPE_AUTO9_MFC);
        object_property_add_child(OBJECT(vms), "exynos-linux-mfc", OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x198e0000);
    }
}

