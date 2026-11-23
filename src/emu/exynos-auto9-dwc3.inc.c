/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original VM3 DWC3 device reset, global registers and upstream xHCI.
 * No downstream USB peripheral or unrestricted DMA is wired.
 */
#include "hw/usb/hcd-dwc3.h"
#include "system/reset.h"

#define TYPE_AUTO9_USB_EMPTY_DMA "exynos-auto9.usb-empty-dma"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9UsbEmptyDma, AUTO9_USB_EMPTY_DMA)

struct Auto9UsbEmptyDma {
    Object parent_obj;
    MemoryRegion blocked, dctl;
    QEMUTimer *dctl_timer;
    uint32_t dctl_active, dctl_reads, dctl_requests, dctl_completions;
    uint32_t dcfg, dcfg_reads, dcfg_writes;
};

#define AUTO9_DWC3_DCFG 0xc700
#define AUTO9_DWC3_DCFG_SPEED_MASK 7
#define AUTO9_DWC3_CSFTRST BIT(30)
#define AUTO9_DWC3_DCTL_RESET_NS 50000 /* Virtual device-side reset latency. */

static void auto9_dwc3_dctl_complete(void *opaque)
{
    Auto9UsbEmptyDma *s = opaque;
    s->dctl_active = 0;
    s->dcfg = 0;
    s->dctl_completions++;
}

static void auto9_dwc3_dctl_reset(void *opaque)
{
    Auto9UsbEmptyDma *s = opaque;
    timer_del(s->dctl_timer);
    s->dctl_active = s->dctl_reads = s->dctl_requests = s->dctl_completions = 0;
    s->dcfg = s->dcfg_reads = s->dcfg_writes = 0;
}

static uint64_t auto9_dwc3_dctl_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9UsbEmptyDma *s = opaque;
    if (off == 0) {
        s->dcfg_reads++;
        return s->dcfg;
    }
    s->dctl_reads++;
    return s->dctl_active ? AUTO9_DWC3_CSFTRST : 0;
}

static MemTxResult auto9_dwc3_dctl_write(void *opaque, hwaddr off, uint64_t value,
                                        unsigned size, MemTxAttrs attrs)
{
    Auto9UsbEmptyDma *s = opaque;
    if (off == 0) {
        if (s->dctl_active || (value & ~((uint64_t)AUTO9_DWC3_DCFG_SPEED_MASK))) {
            return MEMTX_ACCESS_ERROR;
        }
        /* Software-advertised maximum speed, not a negotiated USB link. */
        s->dcfg = value;
        s->dcfg_writes++;
        return MEMTX_OK;
    }
    if (value & ~((uint64_t)AUTO9_DWC3_CSFTRST)) {
        return MEMTX_ACCESS_ERROR; /* No gadget run/stop or event DMA. */
    }
    if (value && !s->dctl_active) {
        s->dctl_active = 1;
        s->dctl_requests++;
        timer_mod(s->dctl_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  AUTO9_DWC3_DCTL_RESET_NS);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_dwc3_dctl_ops = {
    .read = auto9_dwc3_dctl_read,
    .write_with_attrs = auto9_dwc3_dctl_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_usb_empty_dma_init(Object *obj)
{
    Auto9UsbEmptyDma *s = AUTO9_USB_EMPTY_DMA(obj);
    /* Deliberately no subregions. Untranslated bus-master requests fail. */
    memory_region_init(&s->blocked, obj, "auto9-dwc3-empty-dma", UINT64_MAX);
    memory_region_init_io(&s->dctl, obj, &auto9_dwc3_dctl_ops, s,
                          "auto9-dwc3-device-config", 8);
    s->dctl_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_dwc3_dctl_complete, s);
    qemu_register_reset(auto9_dwc3_dctl_reset, s);
    object_property_add_uint32_ptr(obj, "dctl-reads", &s->dctl_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dctl-requests", &s->dctl_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dctl-completions", &s->dctl_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dcfg", &s->dcfg, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dcfg-reads", &s->dcfg_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dcfg-writes", &s->dcfg_writes, OBJ_PROP_FLAG_READ);
}

static void auto9_usb_empty_dma_finalize(Object *obj)
{
    Auto9UsbEmptyDma *s = AUTO9_USB_EMPTY_DMA(obj);
    qemu_unregister_reset(auto9_dwc3_dctl_reset, s);
    timer_free(s->dctl_timer);
}

static const TypeInfo auto9_usb_empty_dma_type = {
    .name = TYPE_AUTO9_USB_EMPTY_DMA,
    .parent = TYPE_OBJECT,
    .instance_size = sizeof(Auto9UsbEmptyDma),
    .instance_init = auto9_usb_empty_dma_init,
    .instance_finalize = auto9_usb_empty_dma_finalize,
};

static void auto9_usb_empty_dma_register(void)
{
    type_register_static(&auto9_usb_empty_dma_type);
}
type_init(auto9_usb_empty_dma_register)

static void auto9_dwc3_create(VirtMachineState *vms)
{
    static const unsigned spi[] = { 199, 201, 191 };
    if (!vms->exynos_linux_dwc3) {
        return;
    }
    /* USB3 remains disabled in the original DT. */
    for (unsigned i = 0; i < G_N_ELEMENTS(spi); i++) {
        g_autofree char *name = i ? g_strdup_printf("exynos-linux-dwc3-%u", i) :
                                   g_strdup("exynos-linux-dwc3");
        g_autofree char *dma = i ? g_strdup_printf("exynos-dwc3-dma-empty-%u", i) :
                                  g_strdup("exynos-dwc3-dma-empty");
        Object *backing = object_new(TYPE_AUTO9_USB_EMPTY_DMA);
        Auto9UsbEmptyDma *s = AUTO9_USB_EMPTY_DMA(backing);
        DeviceState *dev = qdev_new(TYPE_USB_DWC3);
        Object *xhci = object_resolve_path_component(OBJECT(dev), "dwc3-xhci");

        object_property_add_child(OBJECT(vms), dma, backing);
        object_unref(backing);
        object_property_set_link(xhci, "dma", OBJECT(&s->blocked), &error_fatal);
        object_property_add_child(OBJECT(vms), name, OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        /* Upstream global/xHCI model has no device-register subregion. */
        memory_region_add_subregion(sysbus_mmio_get_region(SYS_BUS_DEVICE(dev), 0),
                                    AUTO9_DWC3_DCFG, &s->dctl);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x17200000 + 0x100000 * i);
        sysbus_connect_irq(SYS_BUS_DEVICE(xhci), 0, qdev_get_gpio_in(vms->gic, spi[i]));
    }
}
