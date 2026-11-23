/* SPDX-License-Identifier: GPL-2.0-or-later
 * VM3 USB v3.1 HS control bank. No electrical PHY, external VBUS, charger
 * detection, or USB attachment. LINK_SW_RST is asserted/released by software.
 */
#define TYPE_AUTO9_USB_PHY "exynos-auto9.usb-phy"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9UsbPhy, AUTO9_USB_PHY)

static const struct {
    uint16_t offset;
    uint32_t mask;
} auto9_usb_phy_words[] = {
    { 0x04, 0x00073ff1 }, /* LINK_CTRL configuration, not PME/error status */
    { 0x08, 0x0000037f }, /* LINK_PORT configuration, not host port counts */
    { 0x20, 0x000033df }, /* CLKRST; bit0 is LINK_SW_RST */
    { 0x50, 0x000001ff }, /* UTMI control, not external signal state */
    { 0x54, 0x3340335f }, /* HSP control; physical status bits read inactive */
    { 0x58, 0xf37ff777 }, /* HSP_TUNE */
    { 0x5c, 0x01000000 }, /* HSP_TEST.SIDDQ; test interface not implemented */
    /* Cold ReWA setup. Enabling the wake engine still fails closed. */
    { 0x100, 0 },          /* REWA_ENABLE: no remote wake engine */
    { 0x108, 0x11000000 }, /* HSREWA_CTRL: bypass and line monitor selectors */
    { 0x10c, 0xffffffff }, /* HSREWA_REFTO */
    { 0x110, 0xffffffff }, /* HSREWA_HSTK */
    { 0x11c, 0x00070007 }, /* HSREWA_INT1_MASK; no events raised */
    { 0x48, 0x00000010 }, /* COMBO_PMA_CTRL.LOW_PWRN: PIPE override path */
};

struct Auto9UsbPhy {
    SysBusDevice parent_obj;
    MemoryRegion regs;
    uint32_t words[G_N_ELEMENTS(auto9_usb_phy_words)];
    uint32_t reads, writes, read_mask, write_mask, rejected;
    uint32_t reset_assertions, reset_releases, pulse_ns;
    int64_t start_ns;
};

static int auto9_usb_phy_index(hwaddr offset)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_usb_phy_words); i++) {
        if (auto9_usb_phy_words[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static MemTxResult auto9_usb_phy_read(void *opaque, hwaddr offset,
                                     uint64_t *value, unsigned size,
                                     MemTxAttrs attrs)
{
    Auto9UsbPhy *s = opaque;
    int index = auto9_usb_phy_index(offset);
    if (index < 0) {
        s->rejected++;
        qemu_log_mask(LOG_UNIMP, "Auto9 USB PHY unknown read 0x%" HWADDR_PRIx "\n",
                      offset);
        return MEMTX_DECODE_ERROR;
    }
    s->reads++;
    s->read_mask |= 1U << index;
    *value = s->words[index];
    return MEMTX_OK;
}

static MemTxResult auto9_usb_phy_write(void *opaque, hwaddr offset,
                                      uint64_t value, unsigned size,
                                      MemTxAttrs attrs)
{
    Auto9UsbPhy *s = opaque;
    int index = auto9_usb_phy_index(offset);
    uint32_t old;
    if (index < 0 || value & ~((uint64_t)auto9_usb_phy_words[index].mask)) {
        s->rejected++;
        qemu_log_mask(LOG_UNIMP,
                      "Auto9 USB PHY unknown write 0x%" HWADDR_PRIx ": 0x%" PRIx64 "\n",
                      offset, value);
        return MEMTX_DECODE_ERROR;
    }
    old = s->words[index];
    s->words[index] = value;
    s->writes++;
    s->write_mask |= 1U << index;
    if (offset == 0x20 && ((old ^ value) & 1)) {
        int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        if (value & 1) {
            s->start_ns = now;
            s->reset_assertions++;
        } else {
            s->pulse_ns = MIN(now - s->start_ns, UINT32_MAX);
            s->reset_releases++;
        }
    }
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_usb_phy_ops = {
    .read_with_attrs = auto9_usb_phy_read,
    .write_with_attrs = auto9_usb_phy_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_usb_phy_reset(DeviceState *dev)
{
    Auto9UsbPhy *s = AUTO9_USB_PHY(dev);
    memset(s->words, 0, sizeof(s->words));
    s->reads = s->writes = s->read_mask = s->write_mask = s->rejected = 0;
    s->reset_assertions = s->reset_releases = s->pulse_ns = 0;
    s->start_ns = 0;
}

static void auto9_usb_phy_init(Object *obj)
{
    Auto9UsbPhy *s = AUTO9_USB_PHY(obj);
    memory_region_init_io(&s->regs, obj, &auto9_usb_phy_ops, s,
                          "auto9-usb-phy", 0x10000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regs);
    object_property_add_uint32_ptr(obj, "reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "read-mask", &s->read_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "write-mask", &s->write_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-assertions", &s->reset_assertions,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-releases", &s->reset_releases,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-pulse-ns", &s->pulse_ns, OBJ_PROP_FLAG_READ);
}

static void auto9_usb_phy_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_usb_phy_reset);
}

static const TypeInfo auto9_usb_phy_type = {
    .name = TYPE_AUTO9_USB_PHY,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9UsbPhy),
    .instance_init = auto9_usb_phy_init,
    .class_init = auto9_usb_phy_class,
};

static void auto9_usb_phy_register(void)
{
    type_register_static(&auto9_usb_phy_type);
}
type_init(auto9_usb_phy_register)

static void auto9_usb_phy_create(VirtMachineState *vms)
{
    if (!vms->exynos_linux_usb_phy) {
        return;
    }
    for (unsigned i = 0; i < 3; i++) {
        g_autofree char *name = i ? g_strdup_printf("exynos-linux-usb-phy-%u", i) :
                                   g_strdup("exynos-linux-usb-phy");
        DeviceState *dev = qdev_new(TYPE_AUTO9_USB_PHY);
        object_property_add_child(OBJECT(vms), name, OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x171c0000 + 0x10000 * i);
    }
}
