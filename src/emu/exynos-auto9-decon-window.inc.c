/* SPDX-License-Identifier: GPL-2.0-or-later
 * Guest WOD window programming. VM3 owns distinct WIN and WINCON pages;
 * only its selected native DECON boundary can latch a pending window.
 */
#define TYPE_AUTO9_DECON_WINDOW "exynos-auto9.decon-window"
#define AUTO9_DECON_WINDOWS 4
OBJECT_DECLARE_SIMPLE_TYPE(Auto9DeconWindow, AUTO9_DECON_WINDOW)

typedef struct Auto9DeconWindowBank {
    MemoryRegion region, wincon;
    uint32_t control[8];
    uint32_t live_status;
    uint32_t status_reads, control_reads, control_writes, rejected;
    uint32_t enable, active_enable, trigger, pending, requests, commits;
    bool separate_wincon;
} Auto9DeconWindowBank;

struct Auto9DeconWindow {
    SysBusDevice parent_obj;
    Auto9DeconWindowBank banks[AUTO9_DECON_WINDOWS];
    unsigned window_count, pipe[AUTO9_DECON_WINDOWS];
    Auto9DeconWindow *peer;
};

/* Masks match the original Android window enable/config/colormap methods. */
static int auto9_decon_window_index(hwaddr offset)
{
    switch (offset) {
    case 0: return 0;
    case 4: return 6;
    case 8: return 7;
    case 0xc: return 1;
    case 0x10: return 2;
    case 0x14: return 3;
    case 0x18: return 4;
    case 0x1c: return 5;
    default: return -1;
    }
}

static MemTxResult auto9_decon_window_read(void *opaque, hwaddr offset,
                                           uint64_t *value, unsigned size,
                                           MemTxAttrs attrs)
{
    Auto9DeconWindowBank *bank = opaque;
    int index = auto9_decon_window_index(offset);
    if (offset == 0x810) {
        bank->status_reads++;
        *value = bank->live_status;
        return MEMTX_OK;
    }
    if (index >= 0 && (bank->separate_wincon ? index != 0 : index < 6)) {
        bank->control_reads++;
        *value = bank->control[index];
        return MEMTX_OK;
    }
    bank->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 DECON window unknown read 0x%" HWADDR_PRIx "\n",
                  offset);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_decon_window_write(void *opaque, hwaddr offset,
                                            uint64_t value, unsigned size,
                                            MemTxAttrs attrs)
{
    static const uint32_t masks[] = {
        0xf3, 0x3fff3fff, 0x3fff3fff, 0x00ff03ff, 0x03ff03ff, UINT32_MAX,
        0xffff0f03, 0x0f0f0f0f,
    };
    Auto9DeconWindowBank *bank = opaque;
    int index = auto9_decon_window_index(offset);
    if (index < 0 || (bank->separate_wincon ? index == 0 : index >= 6)) {
        bank->rejected++;
        qemu_log_mask(LOG_UNIMP, "Auto9 DECON window unsupported write 0x%" HWADDR_PRIx "\n",
                      offset);
        return MEMTX_DECODE_ERROR;
    }
    bank->control_writes++;
    bank->control[index] = value & masks[index];
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_decon_window_ops = {
    .read_with_attrs = auto9_decon_window_read,
    .write_with_attrs = auto9_decon_window_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* Same request protocol as the QNX window controller, on the original
 * VM3 DT's second window resource, not WIN geometry register 0x14. */
static MemTxResult auto9_wod_wincon_read(void *opaque, hwaddr offset,
                                      uint64_t *value, unsigned size,
                                      MemTxAttrs attrs)
{
    Auto9DeconWindowBank *bank = opaque;
    switch (offset) {
    case 0: *value = bank->enable; return MEMTX_OK;
    case 0x10: *value = bank->trigger; return MEMTX_OK;
    case 0x14: *value = bank->pending; return MEMTX_OK;
    default: bank->rejected++; return MEMTX_DECODE_ERROR;
    }
}

static MemTxResult auto9_wod_wincon_write(void *opaque, hwaddr offset,
                                       uint64_t value, unsigned size,
                                       MemTxAttrs attrs)
{
    Auto9DeconWindowBank *bank = opaque;
    switch (offset) {
    case 0:
        if (!(value & ~UINT64_C(0xf3))) {
            bank->enable = value;
            return MEMTX_OK;
        }
        break;
    case 0x10:
        if (value <= 3) {
            bank->trigger = value;
            return MEMTX_OK;
        }
        break;
    case 0x14:
        if (value <= 1) {
            if (!value) {
                bank->pending = 0;
            } else if (!bank->pending && !(bank->trigger & 2)) {
                bank->pending = 1;
                bank->requests++;
            }
            return MEMTX_OK;
        }
        break;
    }
    bank->rejected++;
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps auto9_wod_wincon_ops = {
    .read_with_attrs = auto9_wod_wincon_read,
    .write_with_attrs = auto9_wod_wincon_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* Original VM3 samsung,decon_idx, in the MMIO order below. */
static const unsigned auto9_linux_wod_pipe[] = { 0, 2, 2 };

static void auto9_wod_boundary(void *opaque, unsigned pipe, bool reset)
{
    Auto9DeconWindow *s = opaque;
    for (unsigned i = 0; i < s->window_count; i++) {
        Auto9DeconWindowBank *bank = &s->banks[i];
        if (s->pipe[i] != pipe) {
            continue;
        }
        if (reset) {
            bank->pending = bank->active_enable = bank->live_status = 0;
        } else if (bank->pending) {
            bank->active_enable = bank->enable;
            bank->live_status = bank->control[2]; /* WIN_END_POSITION shadow */
            bank->pending = 0;
            bank->commits++;
        }
    }
    if (s->peer) {
        auto9_wod_boundary(s->peer, pipe, reset);
    }
}

static void auto9_decon_window_reset(DeviceState *dev)
{
    Auto9DeconWindow *s = AUTO9_DECON_WINDOW(dev);
    for (unsigned i = 0; i < AUTO9_DECON_WINDOWS; i++) {
        Auto9DeconWindowBank *b = &s->banks[i];
        memset(b->control, 0, sizeof(b->control));
        b->live_status = b->status_reads = b->control_reads = 0;
        b->control_writes = b->rejected = 0;
        b->enable = b->active_enable = b->trigger = b->pending = 0;
        b->requests = b->commits = 0;
    }
}

static void auto9_decon_window_init(Object *obj)
{
    Auto9DeconWindow *s = AUTO9_DECON_WINDOW(obj);
    for (unsigned i = 0; i < AUTO9_DECON_WINDOWS; i++) {
        Auto9DeconWindowBank *b = &s->banks[i];
        g_autofree char *status = g_strdup_printf("status-%u", i);
        g_autofree char *reads = g_strdup_printf("status-reads-%u", i);
        g_autofree char *control = g_strdup_printf("control-%u", i);
        g_autofree char *cr = g_strdup_printf("control-reads-%u", i);
        g_autofree char *cw = g_strdup_printf("control-writes-%u", i);
        g_autofree char *rejected = g_strdup_printf("rejected-%u", i);
        g_autofree char *request = g_strdup_printf("wincon-requests-%u", i);
        g_autofree char *pending = g_strdup_printf("wincon-pending-%u", i);
        g_autofree char *commits = g_strdup_printf("wincon-commits-%u", i);
        g_autofree char *active = g_strdup_printf("wincon-active-control-%u", i);
        memory_region_init_io(&b->region, obj, &auto9_decon_window_ops, b,
                              "auto9-decon-window", 0x1000);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &b->region);
        object_property_add_uint32_ptr(obj, status, &b->live_status, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, reads, &b->status_reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, control, &b->control[0], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, cr, &b->control_reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, cw, &b->control_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, rejected, &b->rejected, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, request, &b->requests, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, pending, &b->pending, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, commits, &b->commits, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, active, &b->active_enable, OBJ_PROP_FLAG_READ);
    }
    for (unsigned i = 0; i < AUTO9_DECON_WINDOWS; i++) {
        Auto9DeconWindowBank *b = &s->banks[i];
        memory_region_init_io(&b->wincon, obj, &auto9_wod_wincon_ops, b,
                              "auto9-linux-wod-wincon", 0x1000);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &b->wincon);
    }
}

static void auto9_decon_window_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_decon_window_reset);
}

static const TypeInfo auto9_decon_window_type = {
    .name = TYPE_AUTO9_DECON_WINDOW,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9DeconWindow),
    .instance_init = auto9_decon_window_init,
    .class_init = auto9_decon_window_class_init,
};

static void auto9_decon_window_register(void)
{
    type_register_static(&auto9_decon_window_type);
}
type_init(auto9_decon_window_register)

static void auto9_decon_window_create(VirtMachineState *vms)
{
    if (vms->exynos_decon_window) {
        DeviceState *dev = qdev_new(TYPE_AUTO9_DECON_WINDOW);
        Auto9DeconWindow *wod = AUTO9_DECON_WINDOW(dev);
        Object *decon = object_resolve_path_component(OBJECT(vms), "exynos-qnx-decon");
        object_property_add_child(OBJECT(vms), "exynos-decon-window", OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        wod->window_count = AUTO9_DECON_WINDOWS;
        for (unsigned i = 0; i < AUTO9_DECON_WINDOWS; i++) {
            wod->banks[i].separate_wincon = true;
            wod->pipe[i] = 2;
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), i, 0x1aa32000 + i * 0x1000);
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), AUTO9_DECON_WINDOWS + i,
                           0x1aa52000 + i * 0x1000);
        }
        if (decon) {
            Auto9DeconPipe *p = &AUTO9_QNX_DECON(decon)->pipe[2];
            p->wod_opaque = wod;
            p->wod_boundary = auto9_wod_boundary;
        }
    }
    if (vms->exynos_linux_decon_window) {
        const hwaddr windows[] = {0x18c31000, 0x1aa31000, 0x1aa36000};
        DeviceState *dev = qdev_new(TYPE_AUTO9_DECON_WINDOW);
        Auto9DeconWindow *wod = AUTO9_DECON_WINDOW(dev);
        Object *decon = object_resolve_path_component(OBJECT(vms), "exynos-qnx-decon");
        object_property_add_child(OBJECT(vms), "exynos-linux-decon-window", OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        wod->window_count = G_N_ELEMENTS(windows);
        memcpy(wod->pipe, auto9_linux_wod_pipe, sizeof(auto9_linux_wod_pipe));
        Object *peer = object_resolve_path_component(OBJECT(vms), "exynos-decon-window");
        wod->peer = peer ? AUTO9_DECON_WINDOW(peer) : NULL;
        for (unsigned i = 0; i < G_N_ELEMENTS(windows); i++) {
            unsigned pipe = auto9_linux_wod_pipe[i];
            wod->banks[i].separate_wincon = true;
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), i, windows[i]);
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), AUTO9_DECON_WINDOWS + i,
                           windows[i] + 0x20000);
            if (decon) {
                Auto9DeconPipe *p = &AUTO9_QNX_DECON(decon)->pipe[pipe];
                p->wod_opaque = wod;
                p->wod_boundary = auto9_wod_boundary;
            }
        }
    }
}

