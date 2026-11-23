/* SPDX-License-Identifier: GPL-2.0-or-later
 * Real upstream PL330 connected through the modeled VGEN and S2MPU.
 * The native board programs all sixteen PDMA AxID entries to VM3. The upstream
 * PL330 lacks Samsung's per-channel AxID sideband; accept uniform VID state
 * only, rejecting mixed contexts instead of silently selecting the wrong VM.
 * Factory DT gives instruction and per-channel AR/AW wrapper addresses.
 * Prefix composition is a declared behavior-level model, not a silicon spec.
 * Private requester_id metadata distinguishes actual fetch/read/write paths;
 * no permissions or completion are invented. Prefixes sampled per transaction.
 */
#define TYPE_EXYNOS_AUTO9_PDMA "exynos-auto9.pdma"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Pdma, EXYNOS_AUTO9_PDMA)
struct ExynosAuto9Pdma {
    SysBusDevice parent_obj;
    MemoryRegion dma;
    MemoryRegion wrappers;
    uint32_t inst, ar[8], aw[8];
    uint32_t wrapper_reads, wrapper_writes, rejected;
    ExynosAuto9Vgen *vgen;
    ExynosAuto9S2mpu *s2mpu;
    qemu_irq irq;
    uint64_t levels;
};
static bool exynos_auto9_pdma_address(ExynosAuto9Pdma *s, hwaddr *address,
                                     MemTxAttrs attrs, bool write)
{
    unsigned kind = attrs.requester_id >> 8, tag = attrs.requester_id & 0xff;
    uint32_t high;
    if (*address >> 32 || attrs.unspecified) { return false; }
    if (kind == 1 && tag <= 8 && !write) { high = s->inst; }
    else if (kind == 2 && tag < 8 && !write) { high = s->ar[tag]; }
    else if (kind == 3 && tag < 8 && write) { high = s->aw[tag]; }
    else { return false; }
    *address |= (uint64_t)high << 32;
    return true;
}
static uint32_t *exynos_auto9_pdma_wrapper(ExynosAuto9Pdma *s, hwaddr off)
{
    if (off == 0x500) { return &s->inst; }
    if (off >= 0x400 && off < 0x500) {
        unsigned tag = (off - 0x400) / 0x20;
        if ((off & 0x1f) == 0) { return &s->ar[tag]; }
        if ((off & 0x1f) == 4) { return &s->aw[tag]; }
    }
    return NULL;
}
static MemTxResult exynos_auto9_pdma_wrapper_read(void *opaque, hwaddr off,
                                                  uint64_t *value, unsigned size,
                                                  MemTxAttrs attrs)
{
    ExynosAuto9Pdma *s = opaque;
    uint32_t *word = exynos_auto9_pdma_wrapper(s, off);
    if (!word || size != 4) { s->rejected++; return MEMTX_DECODE_ERROR; }
    *value = *word; s->wrapper_reads++;
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_pdma_wrapper_write(void *opaque, hwaddr off,
                                                   uint64_t value, unsigned size,
                                                   MemTxAttrs attrs)
{
    ExynosAuto9Pdma *s = opaque;
    uint32_t *word = exynos_auto9_pdma_wrapper(s, off);
    if (!word || size != 4 || value > 15) {
        s->rejected++;
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 PDMA unsupported wrapper write 0x%llx=0x%llx\n",
                      (unsigned long long)off, (unsigned long long)value);
        return MEMTX_DECODE_ERROR;
    }
    *word = value; s->wrapper_writes++;
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_pdma_wrapper_ops = {
    .read_with_attrs = exynos_auto9_pdma_wrapper_read,
    .write_with_attrs = exynos_auto9_pdma_wrapper_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static bool exynos_auto9_pdma_attrs(ExynosAuto9Pdma *s, MemTxAttrs *attrs)
{
    uint32_t vid = s->vgen->indexed[0][0] & 7;
    for (unsigned i = 1; i < 16; i++) {
        if ((s->vgen->indexed[i][0] & 7) != vid) {
            qemu_log_mask(LOG_GUEST_ERROR, "Auto9 PDMA mixed VGEN contexts unsupported\n");
            return false;
        }
    }
    attrs->unspecified = 0;
    attrs->pid = vid;
    return true;
}
static MemTxResult exynos_auto9_pdma_read(void *opaque, hwaddr address,
                                        uint64_t *value, unsigned size,
                                        MemTxAttrs attrs)
{
    ExynosAuto9Pdma *s = opaque;
    uint8_t bytes[8] = { 0 };
    MemTxResult result;
    if (!exynos_auto9_pdma_address(s, &address, attrs, false) ||
        !exynos_auto9_pdma_attrs(s, &attrs)) {
        return MEMTX_ACCESS_ERROR;
    }
    result = address_space_read(&s->s2mpu->dma_as, address, attrs, bytes, size);
    *value = ldn_le_p(bytes, size);
    return result;
}
static MemTxResult exynos_auto9_pdma_write(void *opaque, hwaddr address,
                                         uint64_t value, unsigned size,
                                         MemTxAttrs attrs)
{
    ExynosAuto9Pdma *s = opaque;
    uint8_t bytes[8];
    if (!exynos_auto9_pdma_address(s, &address, attrs, true) ||
        !exynos_auto9_pdma_attrs(s, &attrs)) {
        return MEMTX_ACCESS_ERROR;
    }
    stn_le_p(bytes, size, value);
    return address_space_write(&s->s2mpu->dma_as, address, attrs, bytes, size);
}
static const MemoryRegionOps exynos_auto9_pdma_ops = {
    .read_with_attrs = exynos_auto9_pdma_read,
    .write_with_attrs = exynos_auto9_pdma_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 8,
    .impl.min_access_size = 1, .impl.max_access_size = 8,
};
static void exynos_auto9_pdma_irq(void *opaque, int line, int level)
{
    ExynosAuto9Pdma *s = opaque;
    uint64_t bit = 1ULL << line;
    s->levels = (s->levels & ~bit) | (level ? bit : 0);
    qemu_set_irq(s->irq, s->levels != 0);
}
static void exynos_auto9_pdma_reset(DeviceState *dev)
{
    ExynosAuto9Pdma *s = EXYNOS_AUTO9_PDMA(dev);
    s->levels = 0;
    s->inst = 0;
    memset(s->ar, 0, sizeof(s->ar)); memset(s->aw, 0, sizeof(s->aw));
    s->wrapper_reads = s->wrapper_writes = s->rejected = 0;
    qemu_set_irq(s->irq, 0);
}
static void exynos_auto9_pdma_init(Object *obj)
{
    ExynosAuto9Pdma *s = EXYNOS_AUTO9_PDMA(obj);
    memory_region_init_io(&s->dma, obj, &exynos_auto9_pdma_ops, s,
                          "exynos-auto9.pdma-filtered", 1ULL << 36);
    memory_region_init_io(&s->wrappers, obj, &exynos_auto9_pdma_wrapper_ops, s,
                          "exynos-auto9.pdma-wrappers", 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->wrappers);
    object_property_add_uint32_ptr(obj, "wrapper-reads", &s->wrapper_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "wrapper-writes", &s->wrapper_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    qdev_init_gpio_in(DEVICE(obj), exynos_auto9_pdma_irq, 33);
}
static void exynos_auto9_pdma_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_pdma_reset);
}
static const TypeInfo exynos_auto9_pdma_type = {
    .name = TYPE_EXYNOS_AUTO9_PDMA, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Pdma),
    .instance_init = exynos_auto9_pdma_init,
    .class_init = exynos_auto9_pdma_class_init,
};
static void exynos_auto9_pdma_register(void)
{
    type_register_static(&exynos_auto9_pdma_type);
}
type_init(exynos_auto9_pdma_register)

static void exynos_auto9_pdma_create(VirtMachineState *vms, hwaddr base,
                                   unsigned spi, const char *vgen_path,
                                   const char *s2mpu_path)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_PDMA);
    ExynosAuto9Pdma *s = EXYNOS_AUTO9_PDMA(dev);
    DeviceState *engine = qdev_new("pl330");
    s->vgen = EXYNOS_AUTO9_VGEN(object_resolve_path(vgen_path, NULL));
    s->s2mpu = EXYNOS_AUTO9_S2MPU(object_resolve_path(s2mpu_path, NULL));
    object_property_add_child(OBJECT(vms), "exynos-pdma", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base + 0x4000);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(vms->gic, spi));
    object_property_add_child(OBJECT(dev), "engine", OBJECT(engine));
    qdev_prop_set_uint32(engine, "num_chnls", 8);
    qdev_prop_set_uint8(engine, "num_periph_req", 32);
    qdev_prop_set_uint8(engine, "num_events", 32);
    qdev_prop_set_bit(engine, "x-auto9-sideband", true);
    /* OEM SPI6 RX/TX are requests 12/13. Its virtual ready line accounts
     * for the current FIFO, not another uncommitted PL330 burst. */
    qdev_prop_set_uint32(engine, "x-auto9-peripheral-fence-mask", (1u << 12) | (1u << 13));
    /* OEM IVI DTS ties EQoS "mcgr" to PDMA0 request 22. The EQoS
     * control-plane model has no PPS request producer yet. Absence of a
     * request means busy to the PL330 GPIO convention, not always-ready.
     * Leave other request lines and all memory DMA behavior unchanged. */
    if (vms->exynos_eqos_mac) {
        qdev_prop_set_uint32(engine, "x-auto9-idle-peripheral-mask", 1u << 22);
    }
    object_property_set_link(OBJECT(engine), "memory", OBJECT(&s->dma), &error_fatal);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(engine), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(engine), 0, base);
    for (unsigned i = 0; i < 33; i++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(engine), i, qdev_get_gpio_in(dev, i));
    }
}
