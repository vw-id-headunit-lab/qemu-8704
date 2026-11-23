/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original ACPM MFD driver, local A0560000/A0570000 -> AP 10560000/10570000.
 * Register layout/error bits cross-checked against the 2024-12-12 Linux
 * Samsung SPEEDY driver proposal (Markuss Broks / Maksym Holovach).
 * Default: no slave attached. An explicit virtual common-interface endpoint
 * may expose device0/register0x10. A separate explicit virtual revision-zero
 * policy adds only five BL2-proven bytes and a declared virtual revision;
 * no physical chip identity, OTP or arbitrary register defaults are inferred.
 * The deadline is an explicit virtual parameter, not physical bus timing.
 * Only the observed single-byte/random path is supported; no physical IRQ
 * route, burst transfer, IP_BATCHER or electrical line model is claimed.
 */
#define TYPE_EXYNOS_AUTO9_SPEEDY "exynos-auto9.speedy"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Speedy, EXYNOS_AUTO9_SPEEDY)
struct ExynosAuto9Speedy {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    QEMUTimer *deadline;
    uint32_t control, fifo_control, command, enable, status, gap, timeout;
    uint32_t tx_data, commands, timeouts, last_command, last_tx, status_poll_completions;
    bool pending, tx_valid;
    uint64_t latency_ns;
    uint32_t interface_byte; /* 256 means absent; 0..255 is explicit virtual policy. */
    uint32_t interface_reads, interface_writes, response_timeout;
    uint8_t rx[2];
    unsigned rx_count, rx_index;
    unsigned controller;
    uint32_t bl2_rev0, virtual_revision, reg_bd, reg_47, reg_55;
    uint32_t profile_reads, profile_writes;
    uint32_t selector[6]; /* 256 = unknown until an actual successful write. */
    uint32_t pmu_input_byte, pmu_input_reads; /* Explicit optional device1/0x0C input. */
};
/* Original ACPM table at local 0x2487c, 12 x 44-byte selector descriptors.
 * These are full-byte voltage selector registers, not power-good/status.
 * Preserve the written code only; no analog voltage or ramp model is claimed.
 */
static const uint8_t exynos_auto9_selector_regs[2][6] = {
    { 0x4a, 0x4c, 0x4e, 0x50, 0x52, 0x48 },
    { 0x48, 0x4a, 0x4c, 0x4e, 0x50, 0x56 },
};

static uint32_t *exynos_auto9_speedy_profile_register(ExynosAuto9Speedy *s,
                                                    unsigned device, unsigned reg,
                                                    bool write, bool *writable)
{
    *writable = true;
    if (s->bl2_rev0 != 1) { return NULL; }
    if (s->controller == 0 && device == 1 && reg == 0x0c && s->pmu_input_byte <= 255) {
        /* Original PMU init13188 reads this byte and consumes bit0.
         * Its physical meaning/reset value is NOT recovered. A caller must
         * explicitly select a virtual byte; it is never a blanket PMIC ACK. */
        *writable = false;
        return &s->pmu_input_byte;
    }
    if (device == 0 && reg == 0) {
        *writable = false;
        return &s->virtual_revision; /* Explicit virtual revision 0; NOT physical PMIC ID. */
    }
    if (device == 15 && reg == 0xbd) { return &s->reg_bd; }
    if (device == 1 && reg == 0x47) { return &s->reg_47; }
    if (device == 1 && reg == 0x55 && s->controller == 0) { return &s->reg_55; }
    if (device == 1) {
        for (unsigned i = 0; i < 6; i++) {
            if (reg == exynos_auto9_selector_regs[s->controller][i] &&
                (write || s->selector[i] <= 255)) { return &s->selector[i]; }
        }
    }
    return NULL;
}

static void exynos_auto9_speedy_response(void *opaque)
{
    ExynosAuto9Speedy *s = opaque;
    unsigned device = (s->command >> 15) & 15;
    unsigned reg = (s->command >> 7) & 255;
    bool write = s->command & 0x100000;
    bool interface = device == 0 && reg == 0x10 && s->interface_byte <= 255;
    bool writable;
    uint32_t *profile = exynos_auto9_speedy_profile_register(s, device, reg, write, &writable);
    uint8_t response;
    s->pending = false;
    s->tx_valid = false;
    s->rx_index = s->rx_count = 0;
    /* This is a stateful virtual interface endpoint, NOT a blanket PMIC
     * register bank. Unknown registers/device IDs and injected missing
     * responses cannot return an ACK, data, voltage or device identity.
     */
    if ((!interface && (!profile || (write && !writable))) || s->response_timeout) {
        s->status |= 2;
        s->timeouts++;
        return;
    }
    if (interface) {
        if (write) { s->interface_byte = s->tx_data; s->interface_writes++; }
        else { s->interface_reads++; }
        response = s->interface_byte;
    } else {
        if (write) { *profile = s->tx_data; s->profile_writes++; }
        else {
            s->profile_reads++;
            if (profile == &s->pmu_input_byte) { s->pmu_input_reads++; }
        }
        response = *profile;
    }
    /* The original driver consumes an error trailer before the read byte
     * when controller bit6 is enabled (sub_56E0 then sub_573C). The virtual
     * transport has no electrical corruption; an absent response above
     * never manufactures this valid-frame trailer. No wire encoding model
     * or Auto9 silicon reset-byte measurement is claimed.
     */
    if (s->control & 0x40) { s->rx[s->rx_count++] = 0; }
    if (!write) { s->rx[s->rx_count++] = response; }
    s->status |= 1;
}
static void exynos_auto9_speedy_cancel(ExynosAuto9Speedy *s)
{
    timer_del(s->deadline);
    s->pending = s->tx_valid = false;
    s->fifo_control = s->command = s->enable = s->status = 0;
    s->tx_data = 0;
    s->rx_index = s->rx_count = 0;
}
static void exynos_auto9_speedy_start(ExynosAuto9Speedy *s)
{
    s->last_command = s->command;
    s->last_tx = s->tx_data;
    s->commands++;
    timer_mod(s->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->latency_ns);
}
static MemTxResult exynos_auto9_speedy_read(void *opaque, hwaddr offset,
                                          uint64_t *value, unsigned size,
                                          MemTxAttrs attrs)
{
    ExynosAuto9Speedy *s = opaque;
    switch (offset) {
    case 0x00: *value = s->control; break;
    case 0x04: *value = s->fifo_control; break;
    case 0x08: *value = s->command; break;
    case 0x0c: *value = s->enable; break;
    case 0x10:
        /* MMIO and this virtual timer are serialized by the BQL. A guest
         * polling on another host thread can reach the deadline before the
         * main loop dispatches it. Retire only the already-due timer via the
         * normal callback, including genuine unanswered transactions. A
         * write waiting for its TX byte has no armed timer. */
        if (s->pending && timer_pending(s->deadline) &&
            timer_expired(s->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL))) {
            timer_del(s->deadline);
            s->status_poll_completions++;
            exynos_auto9_speedy_response(s);
        }
        *value = s->status;
        break;
    case 0x30: *value = s->rx_count ? s->rx_count : s->tx_valid ? 1 : 0x40; break;
    case 0x44: *value = s->gap; break;
    case 0x48: *value = s->timeout; break;
    case 0x38:
        if (!s->rx_count) {
            qemu_log_mask(LOG_UNIMP, "Auto9 SPEEDY RX read without responding slave\n");
            return MEMTX_DECODE_ERROR;
        }
        *value = s->rx[s->rx_index++];
        s->rx_count--;
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "Auto9 SPEEDY unsupported read 0x%llx\n", (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_speedy_write(void *opaque, hwaddr offset,
                                           uint64_t value, unsigned size,
                                           MemTxAttrs attrs)
{
    ExynosAuto9Speedy *s = opaque;
    switch (offset) {
    case 0x00:
        if (value & 0x80000000U) { exynos_auto9_speedy_cancel(s); }
        s->control = value & ~0x80000000U;
        break;
    case 0x04:
        if (value & 0x80000000U) {
            s->tx_valid = false;
            s->rx_index = s->rx_count = 0;
            /* FIFO reset cannot silently complete/cancel an active command. */
        }
        s->fifo_control = value & ~0x80000000U;
        break;
    case 0x08:
        if (s->pending || s->rx_count || !(s->control & 1) || (s->control & 0xe) ||
            (s->fifo_control & 0xffff) != 0x101 || !(value & 0x80000) ||
            (value & 0xffe0007fU) || s->latency_ns == 0 || s->latency_ns > 1000000000) {
            qemu_log_mask(LOG_UNIMP, "Auto9 SPEEDY unsupported/busy command 0x%llx\n", (unsigned long long)value);
            return MEMTX_DECODE_ERROR;
        }
        s->command = value;
        s->pending = true;
        /* Read commands issue now. Writes issue only after their TX byte. */
        if (!(value & 0x100000) || s->tx_valid) { exynos_auto9_speedy_start(s); }
        break;
    case 0x0c: s->enable = value; break;
    case 0x10: s->status &= ~value; break; /* W1C, cannot invent success. */
    case 0x34:
        if (!s->pending || !(s->command & 0x100000) || s->tx_valid || value > 255 ||
            s->latency_ns == 0 || s->latency_ns > 1000000000) {
            return MEMTX_DECODE_ERROR;
        }
        s->tx_data = value;
        s->tx_valid = true;
        exynos_auto9_speedy_start(s);
        break;
    case 0x44: s->gap = value; break;
    case 0x48: s->timeout = value; break;
    default:
        qemu_log_mask(LOG_UNIMP, "Auto9 SPEEDY unsupported write 0x%llx\n", (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_speedy_ops = {
    .read_with_attrs = exynos_auto9_speedy_read,
    .write_with_attrs = exynos_auto9_speedy_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_speedy_reset(DeviceState *dev)
{
    ExynosAuto9Speedy *s = EXYNOS_AUTO9_SPEEDY(dev);
    exynos_auto9_speedy_cancel(s);
    s->control = s->gap = s->timeout = 0;
    s->commands = s->timeouts = s->last_command = s->last_tx = 0;
    s->status_poll_completions = 0;
    s->interface_reads = s->interface_writes = 0;
    s->profile_reads = s->profile_writes = 0;
    s->pmu_input_reads = 0;
    /* Controller reset is not a PMIC power cycle: retain endpoint state. */
}
static void exynos_auto9_speedy_init(Object *obj)
{
    ExynosAuto9Speedy *s = EXYNOS_AUTO9_SPEEDY(obj);
    memory_region_init_io(&s->mmio, obj, &exynos_auto9_speedy_ops, s, TYPE_EXYNOS_AUTO9_SPEEDY, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    s->deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_speedy_response, s);
    s->latency_ns = 1000;
    s->interface_byte = 256;
    s->pmu_input_byte = 256;
    object_property_add_uint32_ptr(obj, "virtual-pmu-input-byte", &s->pmu_input_byte, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint32_ptr(obj, "pmu-input-reads", &s->pmu_input_reads, OBJ_PROP_FLAG_READ);
    /* Inactive unless the hash-verified, explicitly selected virtual profile
     * is enabled. These are known BL2 writes, not guessed power-on values. */
    s->virtual_revision = 0; /* Explicit supported virtual revision, not a measured ID. */
    s->reg_bd = 0x16; s->reg_47 = 0xfc; s->reg_55 = 0x7c;
    for (unsigned i = 0; i < 6; i++) {
        g_autofree char *name = g_strdup_printf("selector%u-byte", i);
        s->selector[i] = 256;
        object_property_add_uint32_ptr(obj, name, &s->selector[i], OBJ_PROP_FLAG_READ);
    }
    object_property_add_uint32_ptr(obj, "virtual-bl2-rev0", &s->bl2_rev0, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint32_ptr(obj, "virtual-revision-byte", &s->virtual_revision, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "virtual-bd-byte", &s->reg_bd, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "virtual-reg47-byte", &s->reg_47, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "virtual-reg55-byte", &s->reg_55, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "profile-reads", &s->profile_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "profile-writes", &s->profile_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "virtual-interface-byte", &s->interface_byte, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint32_ptr(obj, "interface-reads", &s->interface_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "interface-writes", &s->interface_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "response-timeout", &s->response_timeout, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "unanswered-command-latency-ns", &s->latency_ns, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint32_ptr(obj, "commands-issued", &s->commands, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "unanswered-commands", &s->timeouts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-command", &s->last_command, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-tx-byte", &s->last_tx, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "interrupt-status", &s->status, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "status-poll-completions", &s->status_poll_completions, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_speedy_finalize(Object *obj)
{
    timer_free(EXYNOS_AUTO9_SPEEDY(obj)->deadline);
}
static void exynos_auto9_speedy_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_speedy_reset);
}
static const TypeInfo exynos_auto9_speedy_type = {
    .name = TYPE_EXYNOS_AUTO9_SPEEDY, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Speedy), .instance_init = exynos_auto9_speedy_init,
    .instance_finalize = exynos_auto9_speedy_finalize, .class_init = exynos_auto9_speedy_class_init,
};
static void exynos_auto9_speedy_register(void) { type_register_static(&exynos_auto9_speedy_type); }
type_init(exynos_auto9_speedy_register)

static void exynos_auto9_speedy_create(VirtMachineState *vms)
{
    if (!vms->exynos_acpm) { return; }
    for (unsigned i = 0; i < 2; i++) {
        DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_SPEEDY);
        char name[32];
        ExynosAuto9Speedy *s = EXYNOS_AUTO9_SPEEDY(dev);
        s->controller = i;
        s->reg_47 = i == 0 ? 0xfc : 0x7c;
        snprintf(name, sizeof(name), "exynos-speedy%u", i);
        object_property_add_child(OBJECT(vms), name, OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x10560000 + i * 0x10000);
    }
}
