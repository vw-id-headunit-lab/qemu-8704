/* SPDX-License-Identifier: GPL-2.0-or-later
 * Opt-in management CPU sharing the existing AP-visible SRAM and mailbox.
 * Firmware comes from verified BL2 copies, never a synthetic public header.
 * The explicit virtual clock is not a claim of measured silicon frequency.
 */
#define TYPE_EXYNOS_AUTO9_APM_CORE "exynos-auto9.apm-core"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9ApmCore, EXYNOS_AUTO9_APM_CORE)
struct ExynosAuto9ApmCore {
    SysBusDevice parent_obj;
    MemoryRegion memory, sram_alias, mailbox_alias, counter, counter_ap_alias, unmapped, peripheral_bus;
    ARMv7MState *arm;
    Clock *clock;
    uint64_t clock_hz;
    uint32_t control, reload, initial_count;
    int64_t epoch_ns;
    bool unmapped_seen;
    /* Capture at the actual transaction, BEFORE asynchronous debug stop and
     * guest exception/dump code can change PC/SP/LR. Observability only. */
    uint64_t fault_address;
    uint32_t fault_size, fault_write, fault_pc, fault_sp, fault_lr, fault_r4, fault_r5;
};

static void exynos_auto9_apm_unmapped_stop(ExynosAuto9ApmCore *s, hwaddr address,
                                          unsigned size, bool write)
{
    if (!s->unmapped_seen) {
        s->fault_address = address; s->fault_size = size; s->fault_write = write;
        if (s->arm) {
            s->fault_pc = s->arm->cpu->env.regs[15];
            s->fault_sp = s->arm->cpu->env.regs[13];
            s->fault_lr = s->arm->cpu->env.regs[14];
            s->fault_r4 = s->arm->cpu->env.regs[4];
            s->fault_r5 = s->arm->cpu->env.regs[5];
        }
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 APM unmapped %s address=0x%llx size=%u pc=0x%x\n",
                      write ? "write" : "read", (unsigned long long)address, size,
                      s->arm ? s->arm->cpu->env.regs[15] : 0);
        s->unmapped_seen = true;
    }
    /* Preserve the actual failing transaction/exception instead of returning
     * made-up ready data or allowing a recursive dump fault to abort the host.
     */
    qemu_system_debug_request();
    if (s->arm) { cpu_exit(CPU(s->arm->cpu)); }
}
static MemTxResult exynos_auto9_apm_unmapped_read(void *opaque, hwaddr address,
                                                uint64_t *value, unsigned size,
                                                MemTxAttrs attrs)
{
    exynos_auto9_apm_unmapped_stop(opaque, address, size, false);
    *value = 0;
    return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_apm_unmapped_write(void *opaque, hwaddr address,
                                                 uint64_t value, unsigned size,
                                                 MemTxAttrs attrs)
{
    exynos_auto9_apm_unmapped_stop(opaque, address, size, true);
    return MEMTX_DECODE_ERROR;
}
static const MemoryRegionOps exynos_auto9_apm_unmapped_ops = {
    .read_with_attrs = exynos_auto9_apm_unmapped_read,
    .write_with_attrs = exynos_auto9_apm_unmapped_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 8,
    .impl.min_access_size = 1, .impl.max_access_size = 8,
};

static MemTxResult exynos_auto9_apm_bus_read(void *opaque, hwaddr offset,
                                           uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9ApmCore *s = opaque;
    uint8_t data[8] = { 0 };
    MemTxResult result = address_space_read(&address_space_memory, 0x10000000 + offset,
                                           attrs, data, size);
    *value = 0;
    if (result != MEMTX_OK) {
        exynos_auto9_apm_unmapped_stop(s, 0xa0000000 + offset, size, false);
        return result;
    }
    for (unsigned i = 0; i < size; i++) { *value |= (uint64_t)data[i] << (i * 8); }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_apm_bus_write(void *opaque, hwaddr offset,
                                            uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9ApmCore *s = opaque;
    uint8_t data[8];
    MemTxResult result;
    for (unsigned i = 0; i < size; i++) { data[i] = value >> (i * 8); }
    result = address_space_write(&address_space_memory, 0x10000000 + offset, attrs, data, size);
    if (result != MEMTX_OK) { exynos_auto9_apm_unmapped_stop(s, 0xa0000000 + offset, size, true); }
    return result;
}
static const MemoryRegionOps exynos_auto9_apm_bus_ops = {
    .read_with_attrs = exynos_auto9_apm_bus_read, .write_with_attrs = exynos_auto9_apm_bus_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 8,
    .impl.min_access_size = 1, .impl.max_access_size = 8,
};

static uint32_t exynos_auto9_apm_counter_value(ExynosAuto9ApmCore *s)
{
    uint64_t ticks;
    if (s->control != 0x61) {
        return s->initial_count;
    }
    ticks = muldiv64(qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->epoch_ns,
                     s->clock_hz, NANOSECONDS_PER_SECOND);
    /* Original sub_4A10/4A90 uses the low 16-bit free-running downcounter.
     * Only this observed mode is supplied; the 0x73 IRQ/one-shot mode is not.
     */
    return (s->initial_count + s->reload + 1 -
            ticks % (s->reload + 1)) % (s->reload + 1);
}

static MemTxResult exynos_auto9_apm_counter_read(void *opaque, hwaddr offset,
                                               uint64_t *value, unsigned size,
                                               MemTxAttrs attrs)
{
    ExynosAuto9ApmCore *s = opaque;
    switch (offset) {
    case 0: *value = s->control; return MEMTX_OK;
    case 4: *value = s->reload; return MEMTX_OK;
    case 8: *value = exynos_auto9_apm_counter_value(s); return MEMTX_OK;
    default:
        qemu_log_mask(LOG_UNIMP, "Auto9 APM counter unknown read 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
}

static MemTxResult exynos_auto9_apm_counter_write(void *opaque, hwaddr offset,
                                                uint64_t value, unsigned size,
                                                MemTxAttrs attrs)
{
    ExynosAuto9ApmCore *s = opaque;
    uint32_t current = exynos_auto9_apm_counter_value(s);
    switch (offset) {
    case 0:
        if (value != 0x61 && value != 0x70 && value != 0) {
            qemu_log_mask(LOG_UNIMP, "Auto9 APM counter unsupported mode 0x%llx\n",
                          (unsigned long long)value);
            return MEMTX_DECODE_ERROR;
        }
        s->control = value;
        s->initial_count = current;
        break;
    case 4:
        if (value > 0xffff) { return MEMTX_DECODE_ERROR; }
        s->reload = value;
        s->initial_count = MIN(current, s->reload);
        break;
    case 8:
        if (value > 0xffff) { return MEMTX_DECODE_ERROR; }
        s->initial_count = value;
        /* Firmware writes count before reload during initialization. */
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "Auto9 APM counter unknown write 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_apm_counter_ops = {
    .read_with_attrs = exynos_auto9_apm_counter_read,
    .write_with_attrs = exynos_auto9_apm_counter_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static bool exynos_auto9_apm_core_halted(Object *obj, Error **errp)
{
    ExynosAuto9ApmCore *s = EXYNOS_AUTO9_APM_CORE(obj);
    return s->arm && CPU(s->arm->cpu)->halted;
}
static void exynos_auto9_apm_core_init(Object *obj)
{
    ExynosAuto9ApmCore *s = EXYNOS_AUTO9_APM_CORE(obj);
    memory_region_init(&s->memory, obj, "exynos-apm-local-address-space", 1ULL << 32);
    memory_region_init_io(&s->unmapped, obj, &exynos_auto9_apm_unmapped_ops, s,
                          "exynos-apm-unmapped-stop", 1ULL << 32);
    memory_region_add_subregion_overlap(&s->memory, 0, &s->unmapped, -100);
    /* Observed MCU/AP peripheral aliases differ by 0x90000000. Forward to
     * actual AP models, propagate failures; never create a second zero bank.
     */
    memory_region_init_io(&s->peripheral_bus, obj, &exynos_auto9_apm_bus_ops, s,
                          "exynos-apm-peripheral-bus", 0x20000000);
    memory_region_add_subregion_overlap(&s->memory, 0xa0000000, &s->peripheral_bus, -10);
    memory_region_init_io(&s->counter, obj, &exynos_auto9_apm_counter_ops, s,
                          "exynos-apm-counter", 0x1000);
    memory_region_add_subregion(&s->memory, 0xa0430000, &s->counter);
    /* AP alias is for qtest clock/state verification, not fake producer data. */
    memory_region_init_alias(&s->counter_ap_alias, obj, "exynos-apm-counter-ap",
                             &s->counter, 0, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->counter_ap_alias);
    s->reload = 0xffff;
    object_property_add_bool(obj, "management-halted", exynos_auto9_apm_core_halted, NULL);
    object_property_add_uint64_ptr(obj, "first-fault-address", &s->fault_address, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-size", &s->fault_size, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-write", &s->fault_write, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-pc", &s->fault_pc, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-sp", &s->fault_sp, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-lr", &s->fault_lr, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-r4", &s->fault_r4, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "first-fault-r5", &s->fault_r5, OBJ_PROP_FLAG_READ);
}

static void exynos_auto9_apm_core_reset(DeviceState *dev)
{
    ExynosAuto9ApmCore *s = EXYNOS_AUTO9_APM_CORE(dev);
    s->control = 0;
    s->reload = 0xffff;
    s->initial_count = 0;
    s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->unmapped_seen = false;
    s->fault_address = s->fault_size = s->fault_write = s->fault_pc = 0;
    s->fault_sp = s->fault_lr = s->fault_r4 = s->fault_r5 = 0;
}
static void exynos_auto9_apm_core_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_apm_core_reset);
}
static const TypeInfo exynos_auto9_apm_core_type = {
    .name = TYPE_EXYNOS_AUTO9_APM_CORE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9ApmCore),
    .instance_init = exynos_auto9_apm_core_init,
    .class_init = exynos_auto9_apm_core_class_init,
};
static void exynos_auto9_apm_core_register(void)
{
    type_register_static(&exynos_auto9_apm_core_type);
}
type_init(exynos_auto9_apm_core_register)

/* Machine memory setup commits its internal page size before command-line
 * properties are applied. A derived machine supplies the smaller promise
 * without changing ordinary virt (including x-exynos-vlm with no MCU).
 */
static CpuInstanceProperties virt_cpu_index_to_props(MachineState *ms,
                                                    unsigned cpu_index);
static CpuInstanceProperties exynos_auto9_apm_cpu_props(MachineState *ms,
                                                       unsigned cpu_index)
{
    if (cpu_index >= ms->smp.cpus) {
        /* The auxiliary core is not an application CPU/hotplug slot. Leave
         * application socket/core properties absent instead of indexing past
         * the eight-entry original topology when QMP enumerates all CPUs.
         */
        return (CpuInstanceProperties) { 0 };
    }
    return virt_cpu_index_to_props(ms, cpu_index);
}
static void exynos_auto9_apm_machine_class_init(ObjectClass *klass, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(klass);
    mc->desc = "Local 8704 Exynos platform with optional original ACPM management CPU";
    mc->alias = NULL;
    mc->is_default = false;
    mc->minimum_page_bits = 10;
    mc->cpu_index_to_instance_props = exynos_auto9_apm_cpu_props;
}
static const TypeInfo exynos_auto9_apm_machine_type = {
    .name = MACHINE_TYPE_NAME("virt-8704-apm"),
    .parent = MACHINE_TYPE_NAME("virt-11.1"),
    .class_init = exynos_auto9_apm_machine_class_init,
};
static void exynos_auto9_apm_machine_register(void)
{
    type_register_static(&exynos_auto9_apm_machine_type);
}
type_init(exynos_auto9_apm_machine_register)

static void exynos_auto9_apm_core_create(VirtMachineState *vms)
{
    DeviceState *dev, *cluster, *armdev;
    ExynosAuto9ApmCore *s;
    ExynosAuto9Apm *mailbox;
    if (!vms->exynos_acpm) { return; }
    if (!vms->exynos_vlm || vms->exynos_acpm_hz < 1000000 ||
        vms->exynos_acpm_hz > 1000000000) {
        error_report("x-exynos-acpm requires x-exynos-vlm and explicit 1MHz..1GHz virtual clock");
        exit(EXIT_FAILURE);
    }
    mailbox = EXYNOS_AUTO9_APM(object_resolve_path("/machine/exynos-apm-mailbox", NULL));
    dev = qdev_new(TYPE_EXYNOS_AUTO9_APM_CORE);
    object_property_add_child(OBJECT(vms), "exynos-apm-core", OBJECT(dev));
    s = EXYNOS_AUTO9_APM_CORE(dev);
    s->clock_hz = vms->exynos_acpm_hz;
    memory_region_init_alias(&s->sram_alias, OBJECT(dev), "exynos-apm-shared-local",
                             &mailbox->sram, 0, 0x38000);
    memory_region_add_subregion(&s->memory, 0, &s->sram_alias);
    memory_region_init_alias(&s->mailbox_alias, OBJECT(dev), "exynos-apm-mailbox-local",
                             &mailbox->registers, 0, 0x200);
    memory_region_add_subregion(&s->memory, 0xa0500000, &s->mailbox_alias);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10430000);

    /* Different CPU architecture and address space require a distinct cluster,
     * also allowing GDB multiprocess to supply the correct register XML.
     */
    cluster = qdev_new(TYPE_CPU_CLUSTER);
    object_property_add_child(OBJECT(dev), "management-cluster", OBJECT(cluster));
    qdev_prop_set_uint32(cluster, "cluster-id", 1);
    armdev = qdev_new(TYPE_ARMV7M);
    object_property_add_child(OBJECT(cluster), "armv7m", OBJECT(armdev));
    qdev_prop_set_string(armdev, "cpu-type", ARM_CPU_TYPE_NAME("cortex-m3"));
    qdev_prop_set_uint32(armdev, "num-irq", 64);
    qdev_prop_set_uint32(armdev, "num-prio-bits", 3);
    qdev_prop_set_uint32(armdev, "init-nsvtor", 0x3000);
    object_property_set_link(OBJECT(armdev), "memory", OBJECT(&s->memory), &error_fatal);
    s->clock = clock_new(OBJECT(dev), "virtual-apm-clock");
    clock_set_hz(s->clock, s->clock_hz);
    qdev_connect_clock_in(armdev, "cpuclk", s->clock);
    qdev_connect_clock_in(armdev, "refclk", s->clock);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(armdev), &error_fatal);
    s->arm = ARMV7M(armdev);
    CPU(s->arm->cpu)->ignore_memory_transaction_failures = false;
    if (vms->exynos_acpm_firmware) {
        /* Register the image in the MCU address space. Registering only the
         * AP alias would leave the MCU reset unable to find the ROM vectors
         * before ROMs are copied into the shared backing RAM.
         */
        armv7m_load_kernel(s->arm->cpu, vms->exynos_acpm_firmware, 0, 0x38000);
    }
    qdev_realize_and_unref(cluster, NULL, &error_fatal);
    /* Original vector slots 16+16 through 16+31 name the shared ACPM
     * handler; the firmware selects individual channels in NVIC ISER0.
     * Keep AP receive bank/SPI separate and leave unused wires disconnected
     * in the guestless board test. */
    if (vms->exynos_acpm_firmware && vms->exynos_acpm_doorbells) {
        for (unsigned i = 0; i < G_N_ELEMENTS(mailbox->acpm_doorbell); i++) {
            qdev_connect_gpio_out_named(DEVICE(mailbox), "acpm-doorbell", i,
                                        qdev_get_gpio_in(armdev, i + 16));
        }
        mailbox->acpm_native_route = true;
    }
}

static void exynos_auto9_apm_core_bootstrap(VirtMachineState *vms)
{
    ExynosAuto9ApmCore *s;
    if (!vms->exynos_acpm) { return; }
    s = EXYNOS_AUTO9_APM_CORE(object_resolve_path("/machine/exynos-apm-core", NULL));
    /* arm_load_kernel() configures PSCI secondaries by iterating every CPU.
     * The management processor is independent, not another application CPU.
     * Leave SP/PC untouched: its real reset reads the original vector table.
     */
    s->arm->cpu->env.boot_info = NULL;
    object_property_set_int(OBJECT(s->arm->cpu), "psci-conduit",
                            QEMU_PSCI_CONDUIT_DISABLED, &error_fatal);
    object_property_set_bool(OBJECT(s->arm->cpu), "start-powered-off",
                             !vms->exynos_acpm_firmware,
                             &error_fatal);
}
