/* SPDX-License-Identifier: GPL-2.0-or-later
 * OEM TI983 control subset: separate I2C17/0C peer, no FPD or video link.
 * Linux VM3 reaches this bus via the original VM2 vi2c backend. The QNX
 * TI941 remains a distinct peripheral. Untrained virtual registers read
 * zero, never a fabricated chip revision or successful training signature.
 */
#define TYPE_EXYNOS_AUTO9_TI983 "exynos-auto9.ti983-abt"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Ti983, EXYNOS_AUTO9_TI983)

struct ExynosAuto9Ti983 {
    I2CSlave parent_obj;
    QEMUTimer *start;
    uint32_t profile, rails, powered, reads, writes, rejected, apb_requests;
    uint8_t phase, ptr, bank, indirect, low, high, ctl;
    bool ptr_valid, apb_valid;
};

static void exynos_auto9_ti983_clear(ExynosAuto9Ti983 *s)
{
    s->phase = s->ptr = s->bank = s->indirect = s->low = s->high = s->ctl = 0;
    s->ptr_valid = s->apb_valid = false;
}

static void exynos_auto9_ti983_ready(void *opaque)
{
    ExynosAuto9Ti983 *s = opaque;
    s->powered = s->rails == 7;
}

static void exynos_auto9_ti983_gpio(void *opaque, int line, int level)
{
    ExynosAuto9Ti983 *s = opaque;
    unsigned old = s->rails;

    s->rails = (s->rails & ~(1U << line)) | (!!level << line);
    if (s->rails == old) {
        return;
    }
    timer_del(s->start);
    s->powered = 0;
    exynos_auto9_ti983_clear(s);
    if (s->rails == 7) {
        /* Virtual settling delay; not a recovered analog specification. */
        timer_mod(s->start, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100000);
    }
}

static bool exynos_auto9_ti983_apb_known(ExynosAuto9Ti983 *s)
{
    switch ((s->high << 8) | s->low) {
    case 0x400: case 0x404: case 0x420: case 0x424: case 0x428:
    case 0x42c: case 0x43c: case 0x440: case 0x500: case 0x514:
        return true;
    default:
        return false;
    }
}

static bool exynos_auto9_ti983_write(ExynosAuto9Ti983 *s, uint8_t val)
{
    switch (s->ptr) {
    case 0x40:
        if (val != 0x31) { return false; }
        s->bank = val;
        return true;
    case 0x41:
        if (val != 0x31) { return false; }
        s->indirect = val;
        return true;
    case 0x48:
        s->apb_valid = false;
        if (val != 3 || !exynos_auto9_ti983_apb_known(s)) { return false; }
        s->ctl = val;
        s->apb_valid = true;
        s->apb_requests++;
        return true;
    case 0x49:
        s->low = val;
        s->apb_valid = false;
        return true;
    case 0x4a:
        s->high = val;
        s->apb_valid = false;
        return true;
    default:
        return false;
    }
}

static bool exynos_auto9_ti983_readable(ExynosAuto9Ti983 *s)
{
    switch (s->ptr) {
    case 0x0c: case 0x40: case 0x41: case 0x48: case 0x49: case 0x4a:
        return true;
    case 0x42:
        return s->bank == 0x31 && s->indirect == 0x31;
    case 0x4b: case 0x4c: case 0x4d: case 0x4e:
        return s->apb_valid;
    default:
        return false;
    }
}

static int exynos_auto9_ti983_event(I2CSlave *dev, enum i2c_event event)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(dev);

    if (event == I2C_FINISH) {
        s->phase = 0; /* The pointer survives STOP and a separate RECV. */
    } else if (event == I2C_START_SEND || event == I2C_START_RECV) {
        if (!s->powered || (event == I2C_START_RECV &&
            (!s->ptr_valid || !exynos_auto9_ti983_readable(s)))) {
            s->rejected++;
            return 1;
        }
        s->phase = event == I2C_START_SEND ? 0 : 2;
    } else if (event == I2C_START_SEND_ASYNC) {
        s->rejected++;
        return 1;
    }
    return 0;
}

static int exynos_auto9_ti983_send(I2CSlave *dev, uint8_t val)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(dev);

    if (s->powered && s->phase == 0) {
        s->ptr_valid = val == 0x0c || (val >= 0x40 && val <= 0x42) ||
                       (val >= 0x48 && val <= 0x4e);
        if (s->ptr_valid) {
            s->ptr = val;
            s->phase = 1;
            return 0;
        }
    } else if (s->powered && s->phase == 1) {
        s->phase = 3; /* Exactly one data byte on an OEM SEND. */
        if (exynos_auto9_ti983_write(s, val)) {
            s->writes++;
            return 0;
        }
    }
    s->rejected++;
    return 1;
}

static uint8_t exynos_auto9_ti983_recv(I2CSlave *dev)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(dev);

    if (!s->powered || s->phase != 2 || !s->ptr_valid ||
        !exynos_auto9_ti983_readable(s)) {
        s->rejected++;
        return 0xff;
    }
    s->phase = 3;
    s->reads++;
    switch (s->ptr) {
    case 0x40: return s->bank;
    case 0x41: return s->indirect;
    case 0x48: return s->ctl;
    case 0x49: return s->low;
    case 0x4a: return s->high;
    default: return 0; /* No HPD, lock, trained lanes or analog telemetry. */
    }
}

static void exynos_auto9_ti983_reset(DeviceState *dev)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(dev);

    timer_del(s->start);
    s->rails = s->powered = s->reads = s->writes = s->rejected = s->apb_requests = 0;
    exynos_auto9_ti983_clear(s);
}

static void exynos_auto9_ti983_init(Object *obj)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(obj);

    s->start = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_ti983_ready, s);
    qdev_init_gpio_in_named(DEVICE(obj), exynos_auto9_ti983_gpio, "rails", 3);
    object_property_add_uint32_ptr(obj, "rail-mask", &s->rails, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "powered", &s->powered, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "apb-requests", &s->apb_requests, OBJ_PROP_FLAG_READ);
}

static void exynos_auto9_ti983_finalize(Object *obj)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(obj);
    timer_free(s->start);
}

static void exynos_auto9_ti983_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Ti983 *s = EXYNOS_AUTO9_TI983(dev);
    Object *gpio = object_resolve_path("/machine/exynos-gpio4", NULL);
    BusState *bus = qdev_get_parent_bus(dev);
    static const unsigned pins[] = { 33, 38, 42 }; /* GPG2[1,6], GPG3[2]. */

    if (s->profile != 1 || I2C_SLAVE(s)->address != 0x0c || !bus ||
        strcmp(bus->name, "i2c17") || !gpio ||
        !object_dynamic_cast(gpio, TYPE_EXYNOS_AUTO9_GPIO)) {
        error_setg(errp, "TI983 requires virtual-profile=1 and original I2C17 address 0x0c");
        return;
    }
    for (unsigned i = 0; i < ARRAY_SIZE(pins); i++) {
        qdev_connect_gpio_out(DEVICE(gpio), pins[i], qdev_get_gpio_in_named(dev, "rails", i));
    }
}

static const Property exynos_auto9_ti983_props[] = {
    DEFINE_PROP_UINT32("virtual-profile", ExynosAuto9Ti983, profile, 0),
};

static void exynos_auto9_ti983_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    I2CSlaveClass *ic = I2C_SLAVE_CLASS(klass);

    dc->realize = exynos_auto9_ti983_realize;
    dc->hotpluggable = false;
    device_class_set_props(dc, exynos_auto9_ti983_props);
    device_class_set_legacy_reset(dc, exynos_auto9_ti983_reset);
    ic->event = exynos_auto9_ti983_event;
    ic->send = exynos_auto9_ti983_send;
    ic->recv = exynos_auto9_ti983_recv;
}

static const TypeInfo exynos_auto9_ti983_type = {
    .name = TYPE_EXYNOS_AUTO9_TI983, .parent = TYPE_I2C_SLAVE,
    .instance_size = sizeof(ExynosAuto9Ti983), .instance_init = exynos_auto9_ti983_init,
    .instance_finalize = exynos_auto9_ti983_finalize, .class_init = exynos_auto9_ti983_class_init,
};

static void exynos_auto9_ti983_register(void)
{
    type_register_static(&exynos_auto9_ti983_type);
}
type_init(exynos_auto9_ti983_register)
