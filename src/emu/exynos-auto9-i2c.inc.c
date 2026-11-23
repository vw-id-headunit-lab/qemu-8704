/* SPDX-License-Identifier: GPL-2.0-or-later
 * Source-bound QNX I2C17 and opt-in original Linux I2C1/5/9/19 AUTO ports.
 * Real QEMU I2C bus, 64-byte FIFOs, per-byte virtual deadlines and GIC IRQ.
 * No native endpoint is attached. Missing peers really NACK, never fake IDs.
 * Virtual cold zeros and 22500ns/byte are declared assumptions, not silicon.
 * Manual/10-bit/high-speed wire modes and physical SCL/clock timing absent.
 * Linux idle1/trailing timing is explicitly inferred from original consumers;
 * the legacy QNX17 status/completion coding remains independent.
 */
#include "hw/i2c/i2c.h"
#define TYPE_EXYNOS_AUTO9_I2C "exynos-auto9.i2c"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9I2c, EXYNOS_AUTO9_I2C)
struct ExynosAuto9I2c {
    SysBusDevice parent_obj;
    MemoryRegion mmio, selector_mmio;
    I2CBus *bus;
    qemu_irq irq;
    QEMUTimer *deadline;
    uint32_t regs[0x100 / 4], selector;
    uint32_t bus_index, linux_profile;
    bool linux_port;
    uint8_t tx[64], rx[64];
    unsigned tx_head, tx_num, rx_head, rx_num, remaining;
    bool pending, address_phase, reading, stop, owned;
    uint64_t byte_ns, start_ns;
    uint32_t selector_reads, selector_writes, reads, writes, rejected;
    uint32_t starts, completed, nacks, cancelled, resets, tx_bytes, rx_bytes;
    uint32_t last_target, last_length, last_read;
    uint64_t targets[2];
};
static uint32_t exynos_auto9_i2c_status(ExynosAuto9I2c *s)
{
    uint32_t result = s->regs[0x24 / 4];
    /* Linux drains final RX data after the engine has completed. Readiness
     * follows real buffered bytes; it must not disappear with RUN. */
    if (s->linux_port && s->rx_num && s->rx_num >= MAX(1, (s->regs[1] >> 4) & 127)) { result |= 2; }
    if (s->pending && !s->address_phase) {
        if (s->reading && s->rx_num && s->rx_num >= MAX(1, (s->regs[1] >> 4) & 127)) {
            result |= 2;
        } else if (!s->reading && s->remaining && s->tx_num <= ((s->regs[1] >> 16) & 127)) {
            result |= 1;
        }
    }
    return result;
}
static void exynos_auto9_i2c_irq(ExynosAuto9I2c *s)
{
    qemu_set_irq(s->irq, (exynos_auto9_i2c_status(s) & s->regs[0x20 / 4]) != 0);
}
static void exynos_auto9_i2c_end(ExynosAuto9I2c *s)
{
    if (s->owned) { i2c_end_transfer(s->bus); s->owned = false; }
}
static void exynos_auto9_i2c_clear(ExynosAuto9I2c *s)
{
    if (s->pending || s->owned) { s->cancelled++; }
    timer_del(s->deadline); exynos_auto9_i2c_end(s);
    memset(s->regs, 0, sizeof(s->regs));
    s->pending = s->address_phase = false;
    s->tx_head = s->tx_num = s->rx_head = s->rx_num = s->remaining = 0;
    qemu_set_irq(s->irq, 0);
}
static void exynos_auto9_i2c_finish(ExynosAuto9I2c *s, uint32_t error)
{
    s->pending = false;
    s->address_phase = false;
    s->regs[0x44 / 4] &= ~0x80000000U;
    if (error || s->stop) { exynos_auto9_i2c_end(s); }
    if (error) { s->regs[0x24 / 4] |= error; }
    else {
        s->completed++; s->regs[0x24 / 4] |= 0x80;
        /* Original Linux enables trailing bit6 for a final partial RX batch.
         * Its deadline is assumed to coincide with actual last-byte completion. */
        if (s->linux_port && s->reading && s->rx_num) { s->regs[0x24 / 4] |= 0x40; }
    }
    exynos_auto9_i2c_irq(s);
}
static void exynos_auto9_i2c_tick(void *opaque)
{
    ExynosAuto9I2c *s = opaque;
    if (!s->pending) { return; }
    if (s->address_phase) {
        /* Address ACK/NACK comes from an actual attached QEMU slave. */
        if (i2c_start_transfer(s->bus, (s->regs[0x70 / 4] >> 10) & 127, s->reading)) {
            s->nacks++; exynos_auto9_i2c_finish(s, 0x400); return;
        }
        s->owned = true; s->address_phase = false;
    } else if (s->reading && (s->regs[0] & 0x40) && s->rx_num < 64) {
        s->rx[(s->rx_head + s->rx_num++) & 63] = i2c_recv(s->bus);
        s->rx_bytes++; s->remaining--;
        if (!s->remaining) { i2c_nack(s->bus); }
    } else if (!s->reading && (s->regs[0] & 0x80) && s->tx_num) {
        uint8_t value = s->tx[s->tx_head];
        s->tx_head = (s->tx_head + 1) & 63; s->tx_num--;
        if (i2c_send(s->bus, value)) {
            s->nacks++; exynos_auto9_i2c_finish(s, 0x200); return;
        }
        s->tx_bytes++; s->remaining--;
    } else if ((s->regs[0x48 / 4] & 0x80000000U) &&
               qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->start_ns >=
               ((s->regs[0x48 / 4] & 255) + 1) * s->byte_ns) {
        exynos_auto9_i2c_finish(s, 0x800); return;
    }
    if (!s->remaining) { exynos_auto9_i2c_finish(s, 0); return; }
    exynos_auto9_i2c_irq(s);
    timer_mod(s->deadline, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->byte_ns);
}
static MemTxResult exynos_auto9_i2c_reject(ExynosAuto9I2c *s, hwaddr off, bool write)
{
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 I2C%u unsupported %s at local0x%llx\n",
                  s->bus_index, write ? "write" : "read", (unsigned long long)off);
    return MEMTX_DECODE_ERROR;
}
static bool exynos_auto9_i2c_config(hwaddr off)
{
    switch (off) {
    case 0: case 4: case 8: case 0xc: case 0x10: case 0x20:
    case 0x40: case 0x44: case 0x48: case 0x54: case 0x58: case 0x5c:
    case 0x60: case 0x64: case 0x68: case 0x6c: case 0x70: case 0xc4: case 0xc8:
        return true;
    default: return false;
    }
}
static MemTxResult exynos_auto9_i2c_read(void *opaque, hwaddr off,
                                       uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9I2c *s = opaque;
    s->reads++;
    switch (off) {
    case 0x24: *value = exynos_auto9_i2c_status(s); break;
    case 0x2c: *value = s->regs[0x24 / 4] & 0xf00; break;
    case 0x30:
        *value = s->tx_num | (s->tx_num == 64 ? 0x80 : 0) | (!s->tx_num ? 0x100 : 0) |
                 (s->rx_num << 16) | (s->rx_num == 64 ? 0x800000 : 0) | (!s->rx_num ? 0x1000000 : 0);
        break;
    case 0x38:
        if (!s->rx_num) {
            s->regs[0x24 / 4] |= 0x10; exynos_auto9_i2c_irq(s);
            return exynos_auto9_i2c_reject(s, off, false);
        }
        *value = s->rx[s->rx_head]; s->rx_head = (s->rx_head + 1) & 63; s->rx_num--;
        exynos_auto9_i2c_irq(s); break;
    case 0x50:
        /* Original Auto9 Linux SDA completion consumer waits for low4==1.
         * Explicit virtual idle coding, not recovered silicon FSM states. */
        *value = s->linux_port ? (s->pending || s->owned ? 0x20000 : 1) :
            (s->pending || s->owned ? 0x20000 : 0) | (s->address_phase ? 1 : 0);
        break;
    default:
        if (!exynos_auto9_i2c_config(off)) { return exynos_auto9_i2c_reject(s, off, false); }
        *value = s->regs[off / 4]; break;
    }
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_i2c_write(void *opaque, hwaddr off,
                                        uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9I2c *s = opaque;
    s->writes++;
    if (off == 0x24) {
        s->regs[off / 4] &= ~value; exynos_auto9_i2c_irq(s); return MEMTX_OK;
    }
    if (off == 0x34) {
        if (s->tx_num == 64 || value > 255) {
            s->regs[0x24 / 4] |= 8; exynos_auto9_i2c_irq(s);
            return exynos_auto9_i2c_reject(s, off, true);
        }
        s->tx[(s->tx_head + s->tx_num++) & 63] = value;
        exynos_auto9_i2c_irq(s); return MEMTX_OK;
    }
    if (!exynos_auto9_i2c_config(off)) { return exynos_auto9_i2c_reject(s, off, true); }
    if ((off == 0 && (value & 0x80000000)) || (off == 0xc4 && (value & 1))) {
        exynos_auto9_i2c_clear(s); s->resets++;
        s->regs[off / 4] = value; return MEMTX_OK;
    }
    if (s->pending && off != 0x20 && off != 4) {
        if (off == 0 && !((value ^ s->regs[0]) & ~0xc0U)) {
            /* Original QNX receive arms RUN before enabling RXCHON. */
            s->regs[0] = value; return MEMTX_OK;
        }
        /* Mid-transaction config changes have unknown physical semantics. */
        return exynos_auto9_i2c_reject(s, off, true);
    }
    if (off == 0x44 && (value & 0x80000000)) {
        if (s->selector != 4 || (s->regs[0xc4 / 4] & 1) || !(s->regs[0] & 8) ||
            !(value & 0xffff) || (s->regs[0x40 / 4] & 0x60000000) ||
            !s->byte_ns || s->byte_ns > 1000000000 ||
            (s->linux_port && !(s->regs[0x40 / 4] & 0x80000000U))) {
            return exynos_auto9_i2c_reject(s, off, true);
        }
        s->remaining = value & 0xffff; s->reading = value & 0x10000; s->stop = value & 0x20000;
        s->pending = s->address_phase = true; s->starts++;
        s->last_target = (s->regs[0x70 / 4] >> 10) & 127;
        s->last_length = s->remaining; s->last_read = s->reading;
        s->targets[s->last_target / 64] |= 1ULL << (s->last_target & 63);
        s->start_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        timer_mod(s->deadline, s->start_ns + s->byte_ns);
    }
    s->regs[off / 4] = value; exynos_auto9_i2c_irq(s); return MEMTX_OK;
}
static MemTxResult exynos_auto9_i2c_selector_read(void *opaque, hwaddr off,
                                                uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9I2c *s = opaque; s->selector_reads++; *value = s->selector; return MEMTX_OK;
}
static MemTxResult exynos_auto9_i2c_selector_write(void *opaque, hwaddr off,
                                                 uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9I2c *s = opaque;
    if (value != 0 && value != 4) { return exynos_auto9_i2c_reject(s, 0x1000, true); }
    if (!value) { exynos_auto9_i2c_clear(s); }
    s->selector = value; s->selector_writes++; return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_i2c_ops = {
    .read_with_attrs = exynos_auto9_i2c_read, .write_with_attrs = exynos_auto9_i2c_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static const MemoryRegionOps exynos_auto9_i2c_selector_ops = {
    .read_with_attrs = exynos_auto9_i2c_selector_read, .write_with_attrs = exynos_auto9_i2c_selector_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_i2c_reset(DeviceState *dev)
{
    ExynosAuto9I2c *s = EXYNOS_AUTO9_I2C(dev);
    exynos_auto9_i2c_clear(s); s->selector = 0;
    s->selector_reads = s->selector_writes = s->reads = s->writes = s->rejected = 0;
    s->starts = s->completed = s->nacks = s->cancelled = s->resets = s->tx_bytes = s->rx_bytes = 0;
    s->last_target = s->last_length = s->last_read = 0;
    s->targets[0] = s->targets[1] = 0;
}
static void exynos_auto9_i2c_timing_get(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    uint64_t value = EXYNOS_AUTO9_I2C(obj)->byte_ns;
    visit_type_uint64(v, name, &value, errp);
}
static void exynos_auto9_i2c_timing_set(Object *obj, Visitor *v, const char *name, void *opaque, Error **errp)
{
    ExynosAuto9I2c *s = EXYNOS_AUTO9_I2C(obj);
    uint64_t value;
    if (!visit_type_uint64(v, name, &value, errp)) { return; }
    if (!value || value > 1000000000 || s->pending || s->owned) {
        error_setg(errp, "Virtual I2C byte period requires1..1e9ns and an idle bus"); return;
    }
    s->byte_ns = value;
}
static void exynos_auto9_i2c_init(Object *obj)
{
    ExynosAuto9I2c *s = EXYNOS_AUTO9_I2C(obj);
    s->byte_ns = 22500;
    s->deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_i2c_tick, s);
    memory_region_init_io(&s->mmio, obj, &exynos_auto9_i2c_ops, s, TYPE_EXYNOS_AUTO9_I2C, 0x1000);
    memory_region_init_io(&s->selector_mmio, obj, &exynos_auto9_i2c_selector_ops, s, "exynos-auto9.usi-i2c-selector", 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio); sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->selector_mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    object_property_add(obj, "virtual-byte-ns", "uint64", exynos_auto9_i2c_timing_get, exynos_auto9_i2c_timing_set, NULL, NULL);
    object_property_add_uint32_ptr(obj, "linux-profile", &s->linux_profile, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "selector", &s->selector, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "selector-reads", &s->selector_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "selector-writes", &s->selector_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "transfers-started", &s->starts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "transfers-completed", &s->completed, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "nacks", &s->nacks, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cancelled", &s->cancelled, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "software-resets", &s->resets, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "tx-bytes", &s->tx_bytes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rx-bytes", &s->rx_bytes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-target", &s->last_target, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-length", &s->last_length, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-read", &s->last_read, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "targets-low64", &s->targets[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "targets-high64", &s->targets[1], OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_i2c_finalize(Object *obj) { timer_free(EXYNOS_AUTO9_I2C(obj)->deadline); }
static void exynos_auto9_i2c_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9I2c *s = EXYNOS_AUTO9_I2C(dev);
    if (s->linux_profile > 1) { error_setg(errp, "Require virtual-linux-ports0/1"); return; }
    g_autofree char *name = g_strdup_printf("i2c%u", s->bus_index);
    s->bus = i2c_init_bus(dev, name);
}
static const Property exynos_auto9_i2c_properties[] = {
    DEFINE_PROP_UINT32("bus-index", ExynosAuto9I2c, bus_index, 17),
    DEFINE_PROP_UINT32("virtual-linux-ports", ExynosAuto9I2c, linux_profile, 0),
    DEFINE_PROP_BOOL("linux-port", ExynosAuto9I2c, linux_port, false),
};
static void exynos_auto9_i2c_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, exynos_auto9_i2c_reset);
    dc->realize = exynos_auto9_i2c_realize;
    device_class_set_props(dc, exynos_auto9_i2c_properties);
}
static const TypeInfo exynos_auto9_i2c_type = {
    .name = TYPE_EXYNOS_AUTO9_I2C, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9I2c), .instance_init = exynos_auto9_i2c_init,
    .instance_finalize = exynos_auto9_i2c_finalize, .class_init = exynos_auto9_i2c_class_init,
};
static void exynos_auto9_i2c_register(void) { type_register_static(&exynos_auto9_i2c_type); }
type_init(exynos_auto9_i2c_register)
static void exynos_auto9_i2c_create(VirtMachineState *vms, unsigned index,
                                   hwaddr base, hwaddr selector, unsigned spi, bool linux_port)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_I2C);
    g_autofree char *name = g_strdup_printf("exynos-i2c%u", index);
    qdev_prop_set_uint32(dev, "bus-index", index);
    qdev_prop_set_bit(dev, "linux-port", linux_port);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    ExynosAuto9I2c *s = EXYNOS_AUTO9_I2C(dev);
    if (!linux_port || s->linux_profile == 1) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, selector);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, spi));
    }
}
