/* SPDX-License-Identifier: GPL-2.0-or-later
 * Finite original ti941_iid control endpoint, not a complete TI chip model.
 * Original SHA c652be6e...8567: I2C17/1A, GPIO10830080+4 bit2 PDB,
 * byte pointer SEND, STOP,20ms delay, one-byte RECV. Public TI941AS documents
 * the indirect interface and matching DSI clock setup, not OEM23/24 fields.
 * Explicit virtual-ti941-iid-v1 models an unconnected cable probe only.
 * No video, DSI clock, FPD training, deserializer, HDCP or factory frame.
 */
#include "hw/i2c/i2c.h"
#include "qapi/visitor.h"
#define TYPE_EXYNOS_AUTO9_TI941 "exynos-auto9.ti941-iid"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Ti941, EXYNOS_AUTO9_TI941)
struct ExynosAuto9Ti941 {
    I2CSlave parent_obj;
    QEMUTimer *power_timer, *probe_timer;
    qemu_irq active_output;
    uint32_t profile, powerup_ns, probe_ns;
    uint32_t pdb, powered, cable_present, phase, pointer, pointer_valid;
    uint32_t probe_pending, probe_result, probe_completions, untrained_probes;
    uint32_t reads, writes, nacks, rejected, powerups, powerdowns;
    uint32_t pdb_edges, starts, stops, indirect_writes, indirect_reads;
    uint32_t history[256], history_count, history_dropped;
    uint8_t reset_ctl, port_sel, indirect_ctl, indirect_address;
    uint8_t gpio0_config, private_ctl[2], dual_ctl1;
    uint8_t bridge_ctl[2], dual_ctl2[2], indirect[5][256];
};
static bool exynos_auto9_ti941_known(unsigned reg)
{
    switch (reg) {
    case 0: case 1: case 0xd: case 0x1e: case 0x23: case 0x24:
    case 0x40: case 0x41: case 0x42: case 0x4f: case 0x5b: case 0x5c: return true;
    default: return false;
    }
}
static void exynos_auto9_ti941_cancel_probe(ExynosAuto9Ti941 *s)
{
    timer_del(s->probe_timer);
    s->probe_pending = s->probe_result = 0;
}
static void exynos_auto9_ti941_register_reset(ExynosAuto9Ti941 *s)
{
    exynos_auto9_ti941_cancel_probe(s);
    s->phase = s->pointer = s->pointer_valid = 0;
    s->reset_ctl = s->indirect_ctl = s->indirect_address = 0;
    s->gpio0_config = s->dual_ctl1 = 0; s->port_sel = 1;
    s->private_ctl[0] = s->private_ctl[1] = 0;
    memset(s->bridge_ctl, 0, sizeof(s->bridge_ctl));
    s->dual_ctl2[0] = s->dual_ctl2[1] = 7;
    memset(s->indirect, 0, sizeof(s->indirect));
    /* These are declared virtual cold values. Only original accesses are
     * implemented; unspecified silicon/strap defaults are NOT recovered. */
}
static void exynos_auto9_ti941_power_expire(void *opaque)
{
    ExynosAuto9Ti941 *s = opaque;
    if (s->profile == 1 && s->pdb) {
        s->powered = 1; s->powerups++;
        qemu_set_irq(s->active_output, 1); /* I2C control interface only. */
    }
}
static void exynos_auto9_ti941_probe_expire(void *opaque)
{
    ExynosAuto9Ti941 *s = opaque;
    if (!s->powered || !s->probe_pending) { return; }
    s->probe_pending = 0;
    if (!s->cable_present) {
        /* Original153C..1558 recognizes4 as cable absent. This is an
         * explicit OEM probe hypothesis, NOT a documented TI status field. */
        s->probe_result = 4; s->probe_completions++;
    } else {
        /* An attached cable is not evidence of trained FPD/DSI. Never
         * generate the original successful1B signature without a backend. */
        s->probe_result = 0; s->untrained_probes++;
    }
}
static void exynos_auto9_ti941_input(void *opaque, int line, int level)
{
    ExynosAuto9Ti941 *s = opaque;
    if (line == 1) {
        if (s->cable_present != !!level) {
            s->cable_present = !!level;
            exynos_auto9_ti941_cancel_probe(s);
        }
        return;
    }
    if (s->pdb == !!level) { return; }
    s->pdb = !!level; s->pdb_edges++;
    timer_del(s->power_timer); s->powered = 0;
    qemu_set_irq(s->active_output, 0);
    exynos_auto9_ti941_register_reset(s);
    if (level) {
        timer_mod(s->power_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->powerup_ns);
    } else { s->powerdowns++; }
}
static int exynos_auto9_ti941_indirect_bank(ExynosAuto9Ti941 *s)
{
    switch (s->indirect_ctl & 0x1c) {
    case 4: return 0;
    case 8: return 1;
    case 0x10: return 2;
    case 0x14: return (s->port_sel & 2) && !(s->port_sel & 1) ? 4 : 3;
    default: return -1;
    }
}
static bool exynos_auto9_ti941_indirect_known(int bank, unsigned reg)
{
    if (bank < 0) { return false; }
    if (bank < 2) {
        return reg == 5 || reg == 0x20 || reg == 0x21 || (reg >= 0x30 && reg <= 0x33);
    }
    return reg == 0x49 || reg == 0x4a || reg == 0x4b ||
           (bank == 2 && (reg == 0x86 || reg == 0x94));
}
static bool exynos_auto9_ti941_write_reg(ExynosAuto9Ti941 *s, unsigned reg, uint8_t value)
{
    switch (reg) {
    case 1:
        if (value & ~8U) { return false; } /* Original DSI disable/enable. */
        s->reset_ctl = value; return true;
    case 0x1e:
        if (!value || value > 3) { return false; }
        s->port_sel = value; return true;
    case 0x40:
        if (value & ~0x1fU) { return false; }
        if ((value & 0x1c) != 4 && (value & 0x1c) != 8 &&
            (value & 0x1c) != 0x10 && (value & 0x1c) != 0x14) { return false; }
        s->indirect_ctl = value; return true;
    case 0x41: s->indirect_address = value; return true;
    case 0x42: {
        int bank = exynos_auto9_ti941_indirect_bank(s);
        /* Original IID2 and TI's own sequence write DATA with READ set;
         * READ controls the read path, not permission to store DATA. */
        if (!exynos_auto9_ti941_indirect_known(bank, s->indirect_address)) { return false; }
        s->indirect[bank][s->indirect_address] = value; s->indirect_writes++;
        if (s->indirect_ctl & 2) { s->indirect_address++; }
        return true;
    }
    case 0x4f: case 0x5c:
        for (unsigned port = 0; port < 2; port++) {
            if (!(s->port_sel & (1U << port))) { continue; }
            if (reg == 0x4f) { s->bridge_ctl[port] = value; }
            else { s->dual_ctl2[port] = value; }
        }
        /* 5C forced clock/link request bits are retained, NOT actual lock. */
        return true;
    case 0x5b: s->dual_ctl1 = value; return true;
    case 0x23: case 0x24:
        if (value != 0 && value != 0x80) { return false; }
        if (s->private_ctl[reg - 0x23] == value) { return true; }
        s->private_ctl[reg - 0x23] = value;
        exynos_auto9_ti941_cancel_probe(s);
        if (s->private_ctl[0] == 0x80 && s->private_ctl[1] == 0x80) {
            s->probe_pending = 1;
            timer_mod(s->probe_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->probe_ns);
        }
        return true;
    default: return false;
    }
}
static uint8_t exynos_auto9_ti941_read_reg(ExynosAuto9Ti941 *s, unsigned reg)
{
    unsigned port = (s->port_sel & 1) ? 0 : 1;
    switch (reg) {
    case 0: return I2C_SLAVE(s)->address << 1;
    case 1: return s->reset_ctl;
    case 0xd: return s->gpio0_config;
    case 0x1e: return s->port_sel;
    case 0x23: return s->private_ctl[0];
    case 0x24: return s->probe_pending ? 0x80 : s->probe_result;
    case 0x40: return s->indirect_ctl;
    case 0x41: return s->indirect_address;
    case 0x42: {
        int bank = exynos_auto9_ti941_indirect_bank(s);
        if (!(s->indirect_ctl & 1) || !exynos_auto9_ti941_indirect_known(bank, s->indirect_address)) {
            s->rejected++; return 0xff;
        }
        uint8_t value = s->indirect[bank][s->indirect_address]; s->indirect_reads++;
        if (s->indirect_ctl & 2) { s->indirect_address++; }
        return value;
    }
    case 0x4f: return s->bridge_ctl[port];
    case 0x5b: return s->dual_ctl1;
    case 0x5c: return s->dual_ctl2[port];
    default: s->rejected++; return 0xff;
    }
}
static int exynos_auto9_ti941_event(I2CSlave *dev, enum i2c_event event)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(dev);
    if (event == I2C_START_SEND || event == I2C_START_RECV) {
        s->starts++;
        if (s->profile != 1 || !s->powered || !s->pdb ||
            (event == I2C_START_RECV && !s->pointer_valid)) { s->nacks++; return 1; }
        s->phase = event == I2C_START_SEND ? 0 : 2;
    } else if (event == I2C_START_SEND_ASYNC) { s->nacks++; return 1;
    } else if (event == I2C_FINISH) { s->phase = 0; s->stops++; }
    return 0;
}
static int exynos_auto9_ti941_send(I2CSlave *dev, uint8_t value)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(dev);
    if (!s->powered || !s->pdb) { goto nack; }
    if (!s->phase) {
        if (!exynos_auto9_ti941_known(value)) { s->pointer_valid = 0; goto nack; }
        s->pointer = value; s->pointer_valid = 1; s->phase = 1; return 0;
    }
    if (s->phase != 1 || !exynos_auto9_ti941_write_reg(s, s->pointer, value)) { goto nack; }
    s->writes++;
    if (s->history_count < ARRAY_SIZE(s->history)) {
        /* Preserve port, indirect selector/address and actual accepted byte. */
        s->history[s->history_count++] = (s->port_sel << 24) | (s->indirect_ctl << 16) | (s->pointer << 8) | value;
    } else { s->history_dropped++; }
    s->phase = 3; /* Finite one-byte writes; no unproved sequential side effects. */
    return 0;
nack:
    s->rejected++; s->nacks++; return 1;
}
static uint8_t exynos_auto9_ti941_recv(I2CSlave *dev)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(dev);
    if (!s->powered || !s->pdb || !s->pointer_valid) { s->rejected++; return 0xff; }
    uint8_t value = exynos_auto9_ti941_read_reg(s, s->pointer); s->reads++;
    s->pointer = (s->pointer + 1) & 255;
    s->pointer_valid = exynos_auto9_ti941_known(s->pointer);
    return value;
}
static char *exynos_auto9_ti941_history(Object *obj, Error **errp)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(obj);
    GString *text = g_string_new("");
    for (unsigned i = 0; i < s->history_count; i++) { g_string_append_printf(text, "%s%08x", i ? "," : "", s->history[i]); }
    return g_string_free(text, false);
}
static void exynos_auto9_ti941_reset(DeviceState *dev)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(dev);
    timer_del(s->power_timer); s->powered = 0; qemu_set_irq(s->active_output, 0);
    exynos_auto9_ti941_register_reset(s);
    s->reads = s->writes = s->nacks = s->rejected = s->powerups = s->powerdowns = 0;
    s->probe_completions = s->untrained_probes = s->pdb_edges = s->starts = s->stops = 0;
    s->indirect_writes = s->indirect_reads = s->history_count = s->history_dropped = 0;
    if (s->pdb && s->profile == 1) {
        timer_mod(s->power_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->powerup_ns);
    }
}
static void exynos_auto9_ti941_init(Object *obj)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(obj);
    s->power_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_ti941_power_expire, s);
    s->probe_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_ti941_probe_expire, s);
    qdev_init_gpio_in_named(DEVICE(obj), exynos_auto9_ti941_input, "environment", 2);
    qdev_init_gpio_out_named(DEVICE(obj), &s->active_output, "i2c-active", 1);
    object_property_add_uint32_ptr(obj, "pdb-level", &s->pdb, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "powered", &s->powered, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cable-present", &s->cable_present, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "nacks", &s->nacks, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "powerups", &s->powerups, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "powerdowns", &s->powerdowns, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pdb-edges", &s->pdb_edges, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "starts", &s->starts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "stops", &s->stops, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "probe-pending", &s->probe_pending, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "probe-result", &s->probe_result, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "probe-completions", &s->probe_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "untrained-probes", &s->untrained_probes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "indirect-writes", &s->indirect_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "indirect-reads", &s->indirect_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "history-dropped", &s->history_dropped, OBJ_PROP_FLAG_READ);
    object_property_add_str(obj, "write-history-first256", exynos_auto9_ti941_history, NULL);
}
static void exynos_auto9_ti941_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(dev);
    Object *gpio = object_resolve_path("/machine/exynos-gpio4", NULL);
    if (s->profile != 1 || !s->powerup_ns || s->powerup_ns > 1000000000 ||
        !s->probe_ns || s->probe_ns > 1000000000 || I2C_SLAVE(s)->address != 0x1a ||
        !gpio || !object_dynamic_cast(gpio, TYPE_EXYNOS_AUTO9_GPIO)) {
        error_setg(errp, "TI941 endpoint requires explicit virtual-profile1,1A,GPIO4 and1..1e9ns deadlines"); return;
    }
    qdev_connect_gpio_out(DEVICE(gpio), 34, qdev_get_gpio_in_named(dev, "environment", 0));
}
static void exynos_auto9_ti941_finalize(Object *obj)
{
    ExynosAuto9Ti941 *s = EXYNOS_AUTO9_TI941(obj);
    timer_free(s->power_timer); timer_free(s->probe_timer);
}
static const Property exynos_auto9_ti941_props[] = {
    DEFINE_PROP_UINT32("virtual-profile", ExynosAuto9Ti941, profile, 0),
    DEFINE_PROP_UINT32("powerup-ns", ExynosAuto9Ti941, powerup_ns, 100000),
    DEFINE_PROP_UINT32("probe-ns", ExynosAuto9Ti941, probe_ns, 100000),
};
static void exynos_auto9_ti941_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass); I2CSlaveClass *ic = I2C_SLAVE_CLASS(klass);
    dc->realize = exynos_auto9_ti941_realize; device_class_set_props(dc, exynos_auto9_ti941_props);
    device_class_set_legacy_reset(dc, exynos_auto9_ti941_reset);
    ic->event = exynos_auto9_ti941_event; ic->send = exynos_auto9_ti941_send; ic->recv = exynos_auto9_ti941_recv;
}
static const TypeInfo exynos_auto9_ti941_type = {
    .name = TYPE_EXYNOS_AUTO9_TI941, .parent = TYPE_I2C_SLAVE,
    .instance_size = sizeof(ExynosAuto9Ti941), .instance_init = exynos_auto9_ti941_init,
    .instance_finalize = exynos_auto9_ti941_finalize, .class_init = exynos_auto9_ti941_class_init,
};
static void exynos_auto9_ti941_register(void) { type_register_static(&exynos_auto9_ti941_type); }
type_init(exynos_auto9_ti941_register)
