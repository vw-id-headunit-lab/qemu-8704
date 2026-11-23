/* SPDX-License-Identifier: GPL-2.0-or-later
 * APM mailbox transport, not an ACPM firmware replacement. Native acpm_ipc
 * writes INTGR0@8, INTCR1@0x20, INTMR1@0x24 and reads INTSR1@0x28.
 * Channel table/FVMap SRAM is real RAM and initially empty. Messages are never
 * echoed or acknowledged without an actual remote producer/firmware.
 */
#define TYPE_EXYNOS_AUTO9_APM "exynos-auto9.apm-mailbox"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Apm, EXYNOS_AUTO9_APM)
struct ExynosAuto9Apm {
    SysBusDevice parent_obj;
    MemoryRegion registers;
    MemoryRegion sram;
    qemu_irq irq[2];
    qemu_irq acpm_doorbell[16];
    bool acpm_native_route;
    uint32_t acpm_pulses, acpm_pulse_bits;
    uint32_t pending[2];
    uint32_t mask[2];
    uint32_t shared[16];
    uint32_t generated[2], cleared[2], raised[2], mask_writes[2];
    uint32_t last_generate[2], last_clear[2], irq_level[2];
};
static uint32_t exynos_auto9_apm_valid(unsigned bank)
{
    return bank ? 0xffff : 0xffff0000;
}
static void exynos_auto9_apm_update(ExynosAuto9Apm *s)
{
    for (unsigned bank = 0; bank < 2; bank++) {
        uint32_t level = (s->pending[bank] & ~s->mask[bank]) != 0;
        if (level && !s->irq_level[bank]) {
            s->raised[bank]++;
        }
        s->irq_level[bank] = level;
        qemu_set_irq(s->irq[bank], level);
    }
}
static void exynos_auto9_apm_input(void *opaque, int line, int level)
{
    ExynosAuto9Apm *s = opaque;
    if (level) {
        unsigned bank = line / 16;
        s->pending[bank] |= 1U << (line % 16 + (bank ? 0 : 16));
        exynos_auto9_apm_update(s);
    }
}
static void exynos_auto9_apm_ring_management(ExynosAuto9Apm *s, uint32_t value)
{
    if (!s->acpm_native_route) {
        return;
    }
    /* A doorbell is an event, not a held NVIC level. The stock receiver
     * clears NVIC pending bits and advances the SRAM ring, not INTCR0.
     * IRQ masking/acknowledgement stays with the original Cortex-M code. */
    for (unsigned i = 0; i < G_N_ELEMENTS(s->acpm_doorbell); i++) {
        if (value & BIT(i + 16)) {
            s->acpm_pulses++;
            s->acpm_pulse_bits |= BIT(i + 16);
            qemu_irq_pulse(s->acpm_doorbell[i]);
        }
    }
}
static MemTxResult exynos_auto9_apm_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    ExynosAuto9Apm *s = opaque;
    if (offset >= 0x80 && offset < 0xc0) {
        *value = s->shared[(offset - 0x80) / 4];
        return MEMTX_OK;
    }
    unsigned bank = offset >= 0x1c;
    hwaddr reg = offset - bank * 0x14;
    switch (reg) {
    case 8: /* write-only interrupt generation */
    case 12: /* write-one clear */
        *value = 0;
        break;
    case 16:
        *value = s->mask[bank];
        break;
    case 20:
        *value = s->pending[bank];
        break;
    case 24:
        *value = s->pending[bank] & ~s->mask[bank];
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "Auto9 APM unknown read 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_apm_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    ExynosAuto9Apm *s = opaque;
    if (offset >= 0x80 && offset < 0xc0) {
        s->shared[(offset - 0x80) / 4] = value;
        return MEMTX_OK;
    }
    unsigned bank = offset >= 0x1c;
    hwaddr reg = offset - bank * 0x14;
    value &= exynos_auto9_apm_valid(bank);
    switch (reg) {
    case 8:
        s->generated[bank]++;
        s->last_generate[bank] = value;
        s->pending[bank] |= value;
        if (!bank) {
            exynos_auto9_apm_ring_management(s, value);
        }
        break;
    case 12:
        s->cleared[bank]++;
        s->last_clear[bank] = value;
        s->pending[bank] &= ~value;
        break;
    case 16:
        s->mask_writes[bank]++;
        s->mask[bank] = value;
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "Auto9 APM unknown write 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    exynos_auto9_apm_update(s);
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_apm_ops = {
    .read_with_attrs = exynos_auto9_apm_read,
    .write_with_attrs = exynos_auto9_apm_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_apm_reset(DeviceState *dev)
{
    ExynosAuto9Apm *s = EXYNOS_AUTO9_APM(dev);
    s->pending[0] = s->pending[1] = 0;
    s->acpm_pulses = s->acpm_pulse_bits = 0;
    memset(s->shared, 0, sizeof(s->shared));
    for (unsigned bank = 0; bank < 2; bank++) {
        s->mask[bank] = exynos_auto9_apm_valid(bank);
        s->generated[bank] = s->cleared[bank] = s->raised[bank] = 0;
        s->mask_writes[bank] = s->last_generate[bank] = s->last_clear[bank] = 0;
        s->irq_level[bank] = 0;
    }
    exynos_auto9_apm_update(s);
}
static void exynos_auto9_apm_init(Object *obj)
{
    ExynosAuto9Apm *s = EXYNOS_AUTO9_APM(obj);
    memory_region_init_io(&s->registers, obj, &exynos_auto9_apm_ops, s,
                          TYPE_EXYNOS_AUTO9_APM, 0x200);
    memory_region_init_ram(&s->sram, obj, "exynos-acpm-shared-sram", 0x38000,
                           &error_fatal);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->registers);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->sram);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq[0]);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq[1]);
    qdev_init_gpio_in(DEVICE(obj), exynos_auto9_apm_input, 32);
    qdev_init_gpio_out_named(DEVICE(obj), s->acpm_doorbell, "acpm-doorbell",
                             G_N_ELEMENTS(s->acpm_doorbell));
    object_property_add_uint32_ptr(obj, "acpm-pulses", &s->acpm_pulses, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "acpm-pulse-bits", &s->acpm_pulse_bits, OBJ_PROP_FLAG_READ);
    for (unsigned bank = 0; bank < 2; bank++) {
        static const char *names[] = {
            "pending", "mask", "generated", "cleared", "irq-rises",
            "mask-writes", "last-generate", "last-clear", "irq-level",
        };
        uint32_t *values[] = {
            &s->pending[bank], &s->mask[bank], &s->generated[bank], &s->cleared[bank],
            &s->raised[bank], &s->mask_writes[bank], &s->last_generate[bank],
            &s->last_clear[bank], &s->irq_level[bank],
        };
        for (unsigned i = 0; i < G_N_ELEMENTS(names); i++) {
            g_autofree char *key = g_strdup_printf("bank%u-%s", bank, names[i]);
            object_property_add_uint32_ptr(obj, key, values[i], OBJ_PROP_FLAG_READ);
        }
    }
}
static void exynos_auto9_apm_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_apm_reset);
}
static const TypeInfo exynos_auto9_apm_type = {
    .name = TYPE_EXYNOS_AUTO9_APM, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Apm),
    .instance_init = exynos_auto9_apm_init,
    .class_init = exynos_auto9_apm_class_init,
};
static void exynos_auto9_apm_register(void)
{
    type_register_static(&exynos_auto9_apm_type);
}
type_init(exynos_auto9_apm_register)

static void exynos_auto9_apm_create(VirtMachineState *vms)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_APM);
    object_property_add_child(OBJECT(vms), "exynos-apm-mailbox", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10500000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, 0x02039000);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1, qdev_get_gpio_in(vms->gic, 23));
}
