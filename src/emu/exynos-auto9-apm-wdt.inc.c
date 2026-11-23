/* SPDX-License-Identifier: GPL-2.0-or-later
 * Explicit virtual-samsung32-v1 APM watchdog. Original4BAC/4BE8 and the
 * public Samsung family establish00/04/08/0C and the control coding.
 * Clock and cold-disabled state are DECLARED virtual conditions, not fuses.
 * IRQ/reset requests are real named outputs; private MCU routes are unknown.
 * Default expiry-stop preserves an unhandled request instead of ignoring it.
 */
#include "qapi/visitor.h"
#include "qemu/host-utils.h"
#define TYPE_EXYNOS_AUTO9_APM_WDT "exynos-auto9.apm-wdt"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9ApmWdt, EXYNOS_AUTO9_APM_WDT)
struct ExynosAuto9ApmWdt {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    QEMUTimer *deadline;
    qemu_irq events[2];
    uint32_t profile, clock_hz, expiry_stop, tick;
    uint32_t control, reload, count;
    int64_t epoch_ns;
    uint32_t irq_pending, irq_level, reset_pending;
    uint32_t reads, writes, clears, feeds, expirations, rejected;
    uint32_t irq_assertions, reset_requests;
};
static uint32_t exynos_auto9_apm_wdt_div(ExynosAuto9ApmWdt *s)
{
    return (((s->control >> 8) & 255) + 1) * (16U << ((s->control >> 3) & 3));
}
static uint32_t exynos_auto9_apm_wdt_count(ExynosAuto9ApmWdt *s)
{
    if (s->reset_pending) { return 0; }
    if (!s->tick || !(s->control & 0x20)) { return s->count; }
    uint64_t elapsed = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->epoch_ns;
    uint64_t ticks = muldiv64(elapsed, s->clock_hz, NANOSECONDS_PER_SECOND) /
                     exynos_auto9_apm_wdt_div(s);
    return ticks >= s->count ? 0 : s->count - ticks;
}
static void exynos_auto9_apm_wdt_schedule(ExynosAuto9ApmWdt *s)
{
    timer_del(s->deadline);
    if (!s->tick || s->profile != 1 || !(s->control & 0x20) || s->reset_pending) { return; }
    uint64_t cycles = (uint64_t)MAX(s->count, 1) * exynos_auto9_apm_wdt_div(s);
    /* Max32bit count*32768 fits64bits; muldiv64 avoids nanosecond overflow.
     * The remainder product is <1e18 because the explicit clock <=1GHz. */
    uint64_t ns = muldiv64(cycles, NANOSECONDS_PER_SECOND, s->clock_hz);
    if ((cycles % s->clock_hz) * (NANOSECONDS_PER_SECOND % s->clock_hz) % s->clock_hz) { ns++; }
    timer_mod(s->deadline, s->epoch_ns + MAX(ns, 1));
}
static void exynos_auto9_apm_wdt_irq(ExynosAuto9ApmWdt *s)
{
    uint32_t level = s->irq_pending && (s->control & 4);
    if (level && !s->irq_level) { s->irq_assertions++; }
    s->irq_level = level;
    qemu_set_irq(s->events[0], level);
}
static void exynos_auto9_apm_wdt_expire(void *opaque)
{
    ExynosAuto9ApmWdt *s = opaque;
    if (!s->tick || !(s->control & 0x20) || s->profile != 1 || s->reset_pending) { return; }
    s->expirations++;
    if (s->control & 4) { s->irq_pending = 1; }
    exynos_auto9_apm_wdt_irq(s);
    if (s->control & 1) {
        s->count = 0;
        s->reset_pending = 1;
        s->reset_requests++;
        qemu_set_irq(s->events[1], 1);
        /* Wait for the external reset sink. Never pretend a CPU was reset. */
    } else {
        s->count = s->reload;
        s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        exynos_auto9_apm_wdt_schedule(s);
    }
    if (s->expiry_stop && (s->irq_pending || s->reset_pending)) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 APM watchdog request requires IRQ/reset route irq=%u reset=%u\n",
                      s->irq_pending, s->reset_pending);
        qemu_system_debug_request();
    }
}
static MemTxResult exynos_auto9_apm_wdt_read(void *opaque, hwaddr off,
                                           uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9ApmWdt *s = opaque;
    if (s->profile == 1) {
        if (off == 0) { *value = s->control; s->reads++; return MEMTX_OK; }
        if (off == 4) { *value = s->reload; s->reads++; return MEMTX_OK; }
        if (off == 8) { *value = exynos_auto9_apm_wdt_count(s); s->reads++; return MEMTX_OK; }
    }
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 APM watchdog unsupported read offset=0x%llx\n", (unsigned long long)off);
    return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_apm_wdt_write(void *opaque, hwaddr off,
                                            uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9ApmWdt *s = opaque;
    if (s->profile != 1) { goto reject; }
    if (off == 0) {
        /* Reserved/DBGACK semantics are not established for private APM. */
        if (value & ~0xff3dU) { goto reject; }
        if (s->control != value) {
            s->count = exynos_auto9_apm_wdt_count(s);
            s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            s->control = value;
            exynos_auto9_apm_wdt_irq(s);
            exynos_auto9_apm_wdt_schedule(s);
        }
    } else if (off == 4) {
        s->reload = value; /* Does not feed or change a running deadline. */
    } else if (off == 8) {
        s->count = value;
        s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        s->feeds++;
        exynos_auto9_apm_wdt_schedule(s);
    } else if (off == 12) {
        s->irq_pending = 0; s->clears++;
        exynos_auto9_apm_wdt_irq(s); /* CLRINT cannot undo an asserted reset request. */
    } else { goto reject; }
    s->writes++;
    return MEMTX_OK;
reject:
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 APM watchdog unsupported write offset=0x%llx value=0x%llx\n",
                   (unsigned long long)off, (unsigned long long)value);
    return MEMTX_DECODE_ERROR;
}
static const MemoryRegionOps exynos_auto9_apm_wdt_ops = {
    .read_with_attrs = exynos_auto9_apm_wdt_read, .write_with_attrs = exynos_auto9_apm_wdt_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_apm_wdt_get(Object *obj, Visitor *visitor,
                                    const char *name, void *opaque, Error **errp)
{
    ExynosAuto9ApmWdt *s = EXYNOS_AUTO9_APM_WDT(obj);
    uint32_t value = !strcmp(name, "profile") ? s->profile :
                     !strcmp(name, "clock-hz") ? s->clock_hz :
                     !strcmp(name, "current-count") ? exynos_auto9_apm_wdt_count(s) : s->expiry_stop;
    visit_type_uint32(visitor, name, &value, errp);
}
static void exynos_auto9_apm_wdt_set(Object *obj, Visitor *visitor,
                                    const char *name, void *opaque, Error **errp)
{
    ExynosAuto9ApmWdt *s = EXYNOS_AUTO9_APM_WDT(obj);
    uint32_t value;
    if (!visit_type_uint32(visitor, name, &value, errp)) { return; }
    if (!strcmp(name, "clock-hz")) {
        if (value < 1000000 || value > 1000000000 || (s->control & 0x20)) {
            error_setg(errp, "Virtual watchdog clock requires1MHz..1GHz and disabled counter"); return;
        }
        s->clock_hz = value;
    } else {
        if (value > 1) { error_setg(errp, "Virtual watchdog policy must be0 or1"); return; }
        if (!strcmp(name, "profile")) {
            if ((s->control & 0x20) || s->irq_pending || s->reset_pending || (value && !s->clock_hz)) {
                error_setg(errp, "Watchdog profile requires idle outputs and explicit clock"); return;
            }
            s->profile = value;
        } else { s->expiry_stop = value; }
    }
}
static void exynos_auto9_apm_wdt_reset(DeviceState *dev)
{
    ExynosAuto9ApmWdt *s = EXYNOS_AUTO9_APM_WDT(dev);
    timer_del(s->deadline);
    s->control = s->reload = s->count = 0; /* Explicit virtual cold-disabled state. */
    s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->irq_pending = s->irq_level = s->reset_pending = 0;
    s->reads = s->writes = s->clears = s->feeds = s->expirations = s->rejected = 0;
    s->irq_assertions = s->reset_requests = 0;
    qemu_set_irq(s->events[0], 0); qemu_set_irq(s->events[1], 0);
}
static void exynos_auto9_apm_wdt_init(Object *obj)
{
    ExynosAuto9ApmWdt *s = EXYNOS_AUTO9_APM_WDT(obj);
    memory_region_init_io(&s->mmio, obj, &exynos_auto9_apm_wdt_ops, s, TYPE_EXYNOS_AUTO9_APM_WDT, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    qdev_init_gpio_out_named(DEVICE(obj), s->events, "watchdog-event", 2);
    s->deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_apm_wdt_expire, s);
    s->expiry_stop = 1;
    s->tick = 1;
    object_property_add(obj, "profile", "uint32", exynos_auto9_apm_wdt_get, exynos_auto9_apm_wdt_set, NULL, NULL);
    object_property_add(obj, "clock-hz", "uint32", exynos_auto9_apm_wdt_get, exynos_auto9_apm_wdt_set, NULL, NULL);
    object_property_add(obj, "expiry-stop", "uint32", exynos_auto9_apm_wdt_get, exynos_auto9_apm_wdt_set, NULL, NULL);
    object_property_add(obj, "current-count", "uint32", exynos_auto9_apm_wdt_get, NULL, NULL, NULL);
    object_property_add_uint32_ptr(obj, "control", &s->control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reload", &s->reload, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "feeds", &s->feeds, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "interrupt-clears", &s->clears, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "expirations", &s->expirations, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-pending", &s->irq_pending, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-pending", &s->reset_pending, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-assertions", &s->irq_assertions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-requests", &s->reset_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_apm_wdt_finalize(Object *obj)
{
    timer_free(EXYNOS_AUTO9_APM_WDT(obj)->deadline);
}
static void exynos_auto9_apm_wdt_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_apm_wdt_reset);
}
static const TypeInfo exynos_auto9_apm_wdt_type = {
    .name = TYPE_EXYNOS_AUTO9_APM_WDT, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9ApmWdt), .instance_init = exynos_auto9_apm_wdt_init,
    .instance_finalize = exynos_auto9_apm_wdt_finalize, .class_init = exynos_auto9_apm_wdt_class_init,
};
static void exynos_auto9_apm_wdt_register(void) { type_register_static(&exynos_auto9_apm_wdt_type); }
type_init(exynos_auto9_apm_wdt_register)
static void exynos_auto9_cluster_wdt_create(VirtMachineState *vms, const char *name, hwaddr base)
{
    /* Same 00/04/08/0C family as APM. HWP watchdog_cl0@10050000 /
     * watchdog_cl1@10060000. Accept HYP WTCON=0x8039. Do not tick or
     * fabricate a bite reset: peri clock and reset sink are unverified.
     */
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_APM_WDT);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
    EXYNOS_AUTO9_APM_WDT(dev)->tick = 0;
    object_property_set_uint(OBJECT(dev), "clock-hz", 24000000, &error_fatal);
    object_property_set_uint(OBJECT(dev), "expiry-stop", 0, &error_fatal);
    object_property_set_uint(OBJECT(dev), "profile", 1, &error_fatal);
}

static void exynos_auto9_apm_wdt_create(VirtMachineState *vms)
{
    if (!vms->exynos_acpm) { return; }
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_APM_WDT);
    object_property_add_child(OBJECT(vms), "exynos-apm-wdt", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10440000);
    exynos_auto9_cluster_wdt_create(vms, "exynos-cluster-wdt0", 0x10050000);
    exynos_auto9_cluster_wdt_create(vms, "exynos-cluster-wdt1", 0x10060000);
}
