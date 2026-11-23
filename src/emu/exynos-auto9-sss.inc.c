/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original EPBL sub_244 / sub_85C HASH window at physical 0x1BF50000.
 * Completes the observed wait bits after the original start+length sequence.
 * Digest words stay reset-zero. This is not a verified HASH engine, not a
 * recovered 960-byte SRAM payload, and not a successful self-decrypt.
 */
#define TYPE_EXYNOS_AUTO9_SSS "exynos-auto9.sss"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Sss, EXYNOS_AUTO9_SSS)
#define AUTO9_SSS_MMIO 0x2000
#define AUTO9_SSS_SRAM_BYTES 0x400
struct ExynosAuto9Sss {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    MemoryRegion sram;
    uint32_t words[AUTO9_SSS_MMIO / 4];
    uint32_t starts;
    uint32_t status_acks;
    uint32_t length_writes;
};
static void auto9_sss_complete(ExynosAuto9Sss *s, uint32_t status_bit)
{
    /* First EPBL wait uses 0x1BF5000C bit1; sub_85C waits bit2. HASH MSG_DONE is bit6. */
    s->words[0x00c / 4] |= status_bit;
    s->words[0x1010 / 4] |= 0x40;
    s->starts++;
}
static MemTxResult exynos_auto9_sss_read(void *opaque, hwaddr off,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    ExynosAuto9Sss *s = opaque;
    if (off + size > AUTO9_SSS_MMIO || (off & 3) || size != 4) {
        return MEMTX_DECODE_ERROR;
    }
    *value = s->words[off / 4];
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_sss_write(void *opaque, hwaddr off,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    ExynosAuto9Sss *s = opaque;
    uint32_t word = value;
    if (off + size > AUTO9_SSS_MMIO || (off & 3) || size != 4) {
        return MEMTX_DECODE_ERROR;
    }
    if (off == 0x00c || off == 0x1010) {
        s->words[off / 4] &= ~word;
        s->status_acks++;
        return MEMTX_OK;
    }
    s->words[off / 4] = word;
    if (off == 0x044) {
        s->length_writes++;
        if (s->words[0x1000 / 4] == 25 && word == 960) {
            auto9_sss_complete(s, 2);
        }
    } else if (off == 0x034) {
        auto9_sss_complete(s, 4);
    }
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_sss_ops = {
    .read_with_attrs = exynos_auto9_sss_read,
    .write_with_attrs = exynos_auto9_sss_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};
static void exynos_auto9_sss_reset(DeviceState *dev)
{
    ExynosAuto9Sss *s = EXYNOS_AUTO9_SSS(dev);
    memset(s->words, 0, sizeof(s->words));
    s->starts = 0;
    s->status_acks = 0;
    s->length_writes = 0;
}
static void exynos_auto9_sss_init(Object *obj)
{
    ExynosAuto9Sss *s = EXYNOS_AUTO9_SSS(obj);
    memory_region_init_io(&s->mmio, obj, &exynos_auto9_sss_ops, s,
                          TYPE_EXYNOS_AUTO9_SSS, AUTO9_SSS_MMIO);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    memory_region_init_ram(&s->sram, obj, "auto9-epbl-hash-src",
                           AUTO9_SSS_SRAM_BYTES, &error_fatal);
}
static void exynos_auto9_sss_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_sss_reset);
}
static const TypeInfo exynos_auto9_sss_type = {
    .name = TYPE_EXYNOS_AUTO9_SSS, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Sss),
    .instance_init = exynos_auto9_sss_init,
    .class_init = exynos_auto9_sss_class_init,
};
static void exynos_auto9_sss_register(void)
{
    type_register_static(&exynos_auto9_sss_type);
}
type_init(exynos_auto9_sss_register)
static void exynos_auto9_sss_create(VirtMachineState *vms)
{
    DeviceState *dev;
    ExynosAuto9Sss *s;
    if (!vms->exynos_vlm) {
        return;
    }
    dev = qdev_new(TYPE_EXYNOS_AUTO9_SSS);
    object_property_add_child(OBJECT(vms), "exynos-sss", OBJECT(dev));
    s = EXYNOS_AUTO9_SSS(dev);
    object_property_add_uint32_ptr(OBJECT(dev), "starts", &s->starts,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "status-acks", &s->status_acks,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "length-writes",
                                   &s->length_writes, OBJ_PROP_FLAG_READ);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x1bf50000);
    memory_region_add_subregion(get_system_memory(), 0x1fc00, &s->sram);
}
