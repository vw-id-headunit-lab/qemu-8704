/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original ACPM TMU D02C/CDC0/CF60/CE04 descriptor/register contract.
 * Explicit virtual-onepoint-v1: calibration code150 at25C, one ADC unit/C.
 * This is a uniform virtual thermal environment, NOT physical trim or an
 * analog thermal simulation. No guest code, ready marker or branch is patched.
 * Profile1 preserves the original trim/sample-only model. Profile2 opts into
 * the QNX tmu control/threshold/W1C contract. Cold enabled banks, 50ns initial
 * ADC and 1ms period are explicit behavior assumptions, not silicon timing.
 */
#include "qapi/visitor.h"
#define TYPE_EXYNOS_AUTO9_TMU "exynos-auto9.tmu"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Tmu, EXYNOS_AUTO9_TMU)
typedef struct ExynosAuto9TmuPort { ExynosAuto9Tmu *tmu; unsigned bank; } ExynosAuto9TmuPort;
typedef struct ExynosAuto9TmuBank {
    QEMUTimer *deadline;
    qemu_irq irq;
    uint32_t control, control1, interval, average, valid, code;
    uint32_t enable[12], pending[12], thresholds[12][8], programmed[12];
    uint32_t completions, irq_level;
} ExynosAuto9TmuBank;
struct ExynosAuto9Tmu {
    SysBusDevice parent_obj;
    MemoryRegion mmio[2];
    ExynosAuto9TmuPort ports[2];
    QEMUTimer *deadline;
    int64_t temperature_c, sampled_c;
    uint64_t period_ns, initial_ns;
    uint32_t reference_code, sampled_code, valid, conversions, trim_reads, sample_reads, rejected;
    bool controller;
    ExynosAuto9TmuBank banks[2];
    uint32_t control_reads, control_writes, valid_reads, threshold_writes, irq_raises;
    uint64_t control_history[256];
    uint32_t history_count, history_dropped;
};
static void exynos_auto9_tmu_record(ExynosAuto9Tmu *s, unsigned bank,
                                   hwaddr offset, uint32_t value, bool write)
{
    if (s->history_count == ARRAY_SIZE(s->control_history)) { s->history_dropped++; return; }
    s->control_history[s->history_count++] = ((uint64_t)bank << 63) |
        ((uint64_t)write << 62) | ((uint64_t)offset << 32) | value;
}
static void exynos_auto9_tmu_irq(ExynosAuto9Tmu *s, unsigned bank)
{
    ExynosAuto9TmuBank *b = &s->banks[bank];
    uint32_t pending = 0;
    for (unsigned i = 0; i < 12; i++) { pending |= b->pending[i] & b->enable[i]; }
    uint32_t level = !!(pending && (b->control & 1));
    if (level && !b->irq_level) { s->irq_raises++; }
    b->irq_level = level;
    qemu_set_irq(b->irq, level);
}
static void exynos_auto9_tmu_bank_convert(void *opaque)
{
    ExynosAuto9TmuPort *p = opaque;
    ExynosAuto9Tmu *s = p->tmu;
    ExynosAuto9TmuBank *b = &s->banks[p->bank];
    if (!(b->control & 1)) { return; }
    uint32_t next = s->reference_code + s->temperature_c - 25;
    /* O(12 sensors * 8 thresholds); latch crossings, not polling side effects.
     * Rising levels0..7 occupy pairs3,2,1,0, falling pairs7,6,5,4.
     * First conversion establishes history; it does not invent a crossing. */
    if (b->valid) {
        for (unsigned sensor = 0; sensor < (p->bank ? 11 : 12); sensor++) {
            for (unsigned level = 0; level < 8; level++) {
                unsigned pair = 3 - level / 2, shift = (level & 1) * 16;
                uint32_t rise = (b->thresholds[sensor][pair] >> shift) & 511;
                uint32_t fall = (b->thresholds[sensor][pair + 4] >> shift) & 511;
                if ((b->programmed[sensor] & (1u << pair)) && b->code < rise && next >= rise) {
                    b->pending[sensor] |= 1u << level;
                }
                if ((b->programmed[sensor] & (1u << (pair + 4))) && b->code > fall && next <= fall) {
                    b->pending[sensor] |= 1u << (level + 16);
                }
            }
        }
    }
    b->code = next;
    b->valid = 1;
    b->completions++;
    s->sampled_c = s->temperature_c; s->sampled_code = next;
    s->valid = 1; s->conversions++;
    exynos_auto9_tmu_irq(s, p->bank);
    timer_mod(b->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->period_ns);
}
static hwaddr exynos_auto9_tmu_irq_offset(unsigned sensor)
{
    return 16 * sensor + (sensor <= 4 ? 0x110 : sensor <= 7 ? 0x2c0 : 0x5d0);
}
static hwaddr exynos_auto9_tmu_threshold_offset(unsigned sensor)
{
    return sensor == 0 ? 0x50 : 32 * sensor + (sensor <= 7 ? 0x150 : 0x350);
}
static bool exynos_auto9_tmu_controller_read(ExynosAuto9Tmu *s, unsigned bank,
                                            hwaddr offset, uint64_t *value)
{
    ExynosAuto9TmuBank *b = &s->banks[bank];
    switch (offset) {
    case 0x20: *value = b->control; break;
    case 0x24: *value = b->control1; break;
    case 0x28:
        /* Original QNX getter5440 uses bit12+sensor for0..7 and16+sensor
         * for8..11. Valid is withdrawn on disable, raised only by conversion. */
        *value = b->valid ? (bank ? 0x070ff000 : 0x0f0ff000) : 0;
        s->valid_reads++; goto accepted;
    case 0x2c: *value = b->interval; break;
    case 0x38: *value = b->average; break;
    default:
        for (unsigned sensor = 0; sensor < (bank ? 11 : 12); sensor++) {
            hwaddr irq = exynos_auto9_tmu_irq_offset(sensor);
            hwaddr threshold = exynos_auto9_tmu_threshold_offset(sensor);
            if (offset == irq) { *value = b->enable[sensor]; goto counted; }
            if (offset == irq + 8) { *value = b->pending[sensor]; goto counted; }
            if (offset >= threshold && offset <= threshold + 28 && !(offset & 3)) {
                *value = b->thresholds[sensor][(offset - threshold) / 4];
                goto counted;
            }
        }
        return false;
    }
counted:
    s->control_reads++;
accepted:
    exynos_auto9_tmu_record(s, bank, offset, *value, false); return true;
}
static bool exynos_auto9_tmu_controller_write(ExynosAuto9Tmu *s, unsigned bank,
                                             hwaddr offset, uint32_t value)
{
    ExynosAuto9TmuBank *b = &s->banks[bank];
    if (offset == 0x20) {
        bool was_enabled = b->control & 1;
        if (value & ~0x1f000f01u) { return false; }
        b->control = value;
        if (!(value & 1)) {
            timer_del(b->deadline); b->valid = 0;
            s->valid = s->banks[0].valid || s->banks[1].valid;
        } else if (!was_enabled) {
            timer_mod(b->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->initial_ns);
        }
    } else if (offset == 0x24 && !(value & ~0xf0000u)) {
        b->control1 = value; /* QNX4D40 keeps this selector; timing units unknown. */
    } else if (offset == 0x2c) {
        b->interval = value; /* Retention only, does not assert unverified timing. */
    } else if (offset == 0x38 && !(value & ~0x17u)) {
        b->average = value; /* OTP-derived digital configuration, no analog claim. */
    } else {
        for (unsigned sensor = 0; sensor < (bank ? 11 : 12); sensor++) {
            hwaddr irq = exynos_auto9_tmu_irq_offset(sensor);
            hwaddr threshold = exynos_auto9_tmu_threshold_offset(sensor);
            if (offset == irq && !(value & ~0x00ff00ffu)) {
                b->enable[sensor] = value; goto accepted;
            }
            if (offset == irq + 8) {
                /* Original init44E8 and ISR1D60/4908 write back pending bits. */
                b->pending[sensor] &= ~value; goto accepted;
            }
            if (offset >= threshold && offset <= threshold + 28 && !(offset & 3) &&
                !(value & ~0x01ff01ffu)) {
                unsigned pair = (offset - threshold) / 4;
                b->thresholds[sensor][pair] = value;
                b->programmed[sensor] |= 1u << pair;
                s->threshold_writes++; goto accepted;
            }
        }
        return false;
    }
accepted:
    s->control_writes++; exynos_auto9_tmu_record(s, bank, offset, value, true);
    exynos_auto9_tmu_irq(s, bank); return true;
}
static void exynos_auto9_tmu_convert(void *opaque)
{
    ExynosAuto9Tmu *s = opaque;
    s->sampled_c = s->temperature_c;
    s->sampled_code = s->reference_code + s->sampled_c - 25;
    s->valid = 1; s->conversions++;
    timer_mod(s->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->period_ns);
}
static uint32_t exynos_auto9_tmu_sample_mask(hwaddr offset, unsigned bank)
{
    switch (offset) {
    case 0x40: return 0x1ff; /* Sensor0/16. */
    case 0x44: case 0x48: return 0x7ffffff; /* Three nine-bit sensor codes. */
    case 0x440: return 0x3ffff; /* Sensors8/9 or24/25. */
    case 0x444: return bank ? 0x1ff : 0x3fe00; /* Sensor26 vs sensor11. */
    default: return 0;
    }
}
static MemTxResult exynos_auto9_tmu_read(void *opaque, hwaddr offset,
                                      uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9TmuPort *port = opaque;
    ExynosAuto9Tmu *s = port->tmu;
    if (s->controller && exynos_auto9_tmu_controller_read(s, port->bank, offset, value)) {
        return MEMTX_OK;
    }
    if ((offset <= 0x1c || (offset >= 0x400 && offset <= 0x40c)) && offset % 4 == 0) {
        /* Bit23=0 selects ORIGINAL one-point math; it is not a validity bit.
         * Do not describe this declared virtual calibration as factory OTP. */
        *value = s->reference_code; s->trim_reads++; return MEMTX_OK;
    }
    uint32_t mask = exynos_auto9_tmu_sample_mask(offset, port->bank);
    uint32_t code = s->sampled_code, valid = s->valid;
    if (s->controller) {
        code = s->banks[port->bank].code; valid = s->banks[port->bank].valid;
        if (offset == 0x40) { mask = 0x3ffff; }
        if (offset == 0x444 && !port->bank) { mask = 0x3ffff; }
    }
    if (mask && valid) {
        *value = (code | (code << 9) | (code << 18)) & mask;
        s->sample_reads++; return MEMTX_OK;
    }
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 TMU unsupported/unready read bank=%u offset=0x%llx\n", port->bank, (unsigned long long)offset);
    return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_tmu_write(void *opaque, hwaddr offset,
                                       uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9TmuPort *port = opaque;
    if (port->tmu->controller && exynos_auto9_tmu_controller_write(port->tmu, port->bank, offset, value)) {
        return MEMTX_OK;
    }
    port->tmu->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 TMU unsupported write bank=%u offset=0x%llx\n", port->bank, (unsigned long long)offset);
    return MEMTX_ACCESS_ERROR;
}
static const MemoryRegionOps exynos_auto9_tmu_ops = {
    .read_with_attrs = exynos_auto9_tmu_read, .write_with_attrs = exynos_auto9_tmu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_tmu_get_temperature(Object *obj, Visitor *visitor,
                                            const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(obj);
    int64_t value = !strcmp(name, "temperature-c") ? s->temperature_c : s->sampled_c;
    visit_type_int64(visitor, name, &value, errp);
}
static void exynos_auto9_tmu_set_temperature(Object *obj, Visitor *visitor,
                                            const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(obj);
    int64_t value;
    if (!visit_type_int64(visitor, name, &value, errp)) { return; }
    if (value < -40 || value > 150) { error_setg(errp, "Virtual TMU temperature must be -40..150C"); return; }
    s->temperature_c = value; /* Environment changes take effect at next conversion, not on reads. */
}
static void exynos_auto9_tmu_get_timing(Object *obj, Visitor *visitor,
                                       const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(obj);
    int64_t value = !strcmp(name, "virtual-time-ns") ? qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) :
                    timer_expire_time_ns(s->banks[!strcmp(name, "bank1-deadline-ns")].deadline);
    visit_type_int64(visitor, name, &value, errp);
}
static void exynos_auto9_tmu_get_history(Object *obj, Visitor *visitor,
                                        const char *name, void *opaque, Error **errp)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(obj);
    GString *str = g_string_new(NULL);
    for (unsigned i = 0; i < s->history_count; i++) {
        g_string_append_printf(str, "%s%016" PRIx64, i ? "," : "", s->control_history[i]);
    }
    char *value = str->str;
    visit_type_str(visitor, name, &value, errp); g_string_free(str, true);
}
static void exynos_auto9_tmu_reset(DeviceState *dev)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(dev);
    timer_del(s->deadline);
    s->sampled_code = s->sampled_c = s->valid = s->conversions = 0;
    s->trim_reads = s->sample_reads = s->rejected = 0;
    s->control_reads = s->control_writes = s->valid_reads = s->threshold_writes = s->irq_raises = 0;
    s->history_count = s->history_dropped = 0;
    for (unsigned i = 0; i < 2; i++) {
        ExynosAuto9TmuBank *b = &s->banks[i];
        timer_del(b->deadline);
        memset(b->enable, 0, sizeof(b->enable)); memset(b->pending, 0, sizeof(b->pending));
        memset(b->thresholds, 0, sizeof(b->thresholds)); memset(b->programmed, 0, sizeof(b->programmed));
        b->control = s->controller ? 1 : 0; /* Explicit cold environment: running ADC. */
        b->control1 = b->interval = b->average = b->valid = b->code = b->completions = b->irq_level = 0;
        qemu_set_irq(b->irq, 0);
        if (s->controller) { timer_mod(b->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->initial_ns); }
    }
    if (!s->controller) { timer_mod(s->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->initial_ns); }
}
static void exynos_auto9_tmu_init(Object *obj)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(obj);
    s->temperature_c = 25; s->reference_code = 150;
    s->period_ns = 1000000; s->initial_ns = 50;
    s->deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_tmu_convert, s);
    for (unsigned i = 0; i < 2; i++) {
        s->ports[i].tmu = s; s->ports[i].bank = i;
        s->banks[i].deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_tmu_bank_convert, &s->ports[i]);
        sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->banks[i].irq);
        memory_region_init_io(&s->mmio[i], obj, &exynos_auto9_tmu_ops, &s->ports[i], TYPE_EXYNOS_AUTO9_TMU, 0x1000);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio[i]);
        char name[40];
        snprintf(name, sizeof(name), "bank%u-control", i);
        object_property_add_uint32_ptr(obj, name, &s->banks[i].control, OBJ_PROP_FLAG_READ);
        snprintf(name, sizeof(name), "bank%u-valid", i);
        object_property_add_uint32_ptr(obj, name, &s->banks[i].valid, OBJ_PROP_FLAG_READ);
        snprintf(name, sizeof(name), "bank%u-completions", i);
        object_property_add_uint32_ptr(obj, name, &s->banks[i].completions, OBJ_PROP_FLAG_READ);
        snprintf(name, sizeof(name), "bank%u-irq-level", i);
        object_property_add_uint32_ptr(obj, name, &s->banks[i].irq_level, OBJ_PROP_FLAG_READ);
    }
    object_property_add(obj, "temperature-c", "int", exynos_auto9_tmu_get_temperature, exynos_auto9_tmu_set_temperature, NULL, NULL);
    object_property_add(obj, "sampled-temperature-c", "int", exynos_auto9_tmu_get_temperature, NULL, NULL, NULL);
    object_property_add_uint64_ptr(obj, "sample-period-ns", &s->period_ns, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "initial-conversion-latency-ns", &s->initial_ns, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "onepoint-reference-code", &s->reference_code, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "sampled-code", &s->sampled_code, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "sample-valid", &s->valid, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "sample-completions", &s->conversions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "trim-reads", &s->trim_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "sample-reads", &s->sample_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "control-reads", &s->control_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "control-writes", &s->control_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "valid-reads", &s->valid_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "threshold-writes", &s->threshold_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-raises", &s->irq_raises, OBJ_PROP_FLAG_READ);
    object_property_add(obj, "virtual-time-ns", "int", exynos_auto9_tmu_get_timing, NULL, NULL, NULL);
    object_property_add(obj, "bank0-deadline-ns", "int", exynos_auto9_tmu_get_timing, NULL, NULL, NULL);
    object_property_add(obj, "bank1-deadline-ns", "int", exynos_auto9_tmu_get_timing, NULL, NULL, NULL);
    object_property_add(obj, "control-history-first256", "string", exynos_auto9_tmu_get_history, NULL, NULL, NULL);
    object_property_add_uint32_ptr(obj, "history-dropped", &s->history_dropped, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_tmu_finalize(Object *obj)
{
    ExynosAuto9Tmu *s = EXYNOS_AUTO9_TMU(obj);
    timer_free(s->deadline);
    for (unsigned i = 0; i < 2; i++) { timer_free(s->banks[i].deadline); }
}
static void exynos_auto9_tmu_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_tmu_reset);
}
static const TypeInfo exynos_auto9_tmu_type = {
    .name = TYPE_EXYNOS_AUTO9_TMU, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Tmu), .instance_init = exynos_auto9_tmu_init,
    .instance_finalize = exynos_auto9_tmu_finalize, .class_init = exynos_auto9_tmu_class_init,
};
static void exynos_auto9_tmu_register(void) { type_register_static(&exynos_auto9_tmu_type); }
type_init(exynos_auto9_tmu_register)
static void exynos_auto9_tmu_create(VirtMachineState *vms)
{
    if (vms->exynos_tmu_profile > 2) { error_report("Unsupported virtual TMU profile"); exit(1); }
    if (!vms->exynos_acpm || !vms->exynos_tmu_profile) { return; }
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_TMU);
    EXYNOS_AUTO9_TMU(dev)->controller = vms->exynos_tmu_profile == 2;
    object_property_add_child(OBJECT(vms), "exynos-tmu", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10090000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, 0x10094000);
    if (EXYNOS_AUTO9_TMU(dev)->controller) {
        /* Original QNX tmu_domain descriptors and InterruptAttach(+32).
         * VLM's own interrupt routing still applies; no direct guest injection. */
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, 462));
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1, qdev_get_gpio_in(vms->gic, 461));
    }
}
