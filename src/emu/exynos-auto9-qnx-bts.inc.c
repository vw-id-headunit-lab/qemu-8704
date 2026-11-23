/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original QNX BTS DMC outstanding-request limits. This stores eight
 * independent configuration words; it does not change host DMA or latency.
 */
#define TYPE_AUTO9_QNX_BTS "exynos-auto9.qnx-bts"
#define AUTO9_QNX_BTS_LIMIT_MASK UINT32_C(0x3f3f0000)
OBJECT_DECLARE_SIMPLE_TYPE(Auto9QnxBts, AUTO9_QNX_BTS)

static const hwaddr auto9_qnx_bts_dmc[] = {
    0x1b430000, 0x1b480000, 0x1b630000, 0x1b680000,
    0x1c430000, 0x1c480000, 0x1c630000, 0x1c680000,
};
static const hwaddr auto9_qnx_bts_trex[] = {
    0x1b8a4000, 0x1b8b4000, 0x1b8c4000, 0x1b8d4000,
};
static const struct {
    hwaddr offset;
    uint32_t init;
} auto9_qnx_bts_trex_init[] = {
    { 0, 0x0b070000 }, { 0x100, 0x10101010 }, { 0x180, 0x10101010 },
    { 0x200, 0x08080808 }, { 0x280, 0x08080808 },
};
static const hwaddr auto9_qnx_bts_qos[] = {
    0x1c000000, 0x1c010000, 0x1c020000, 0x1c030000, 0x1c040000, 0x1c050000,
    0x1c060000, 0x1c070000, 0x1c080000, 0x1c090000, 0x1c0a0000, 0x1c0b0000,
    0x1c0f0000, 0x1c100000, 0x1c110000, 0x1c120000, 0x1c130000, 0x1c140000,
    0x1c150000, 0x1c160000, 0x1c170000, 0x1c1a0000, 0x1c0c0000, 0x1c0d0000,
    0x1c0e0000, 0x1ccc0000, 0x1ccd0000, 0x1cd20000, 0x1cd30000, 0x1cd50000,
    0x1cd60000, 0x1b370000, 0x1b360000,
};
static const struct {
    uint16_t offset;
    uint32_t mask;
} auto9_qnx_bts_qos_words[] = {
    { 0x00, 0x0ff00001 }, { 0x10, 0x00ff00ff }, { 0x20, 0x0000f101 },
    { 0x24, 0x0000ffff }, { 0x2c, 0x0000ffff }, { 0x30, 0x0000ffff },
    { 0x34, 0x0000ffff }, { 0x40, 0x0000f101 }, { 0x44, 0x0000ffff },
    { 0x4c, 0x0000ffff }, { 0x50, 0x0000ffff }, { 0x54, 0x0000ffff },
};

typedef struct Auto9QnxBtsDmc {
    MemoryRegion region;
    uint32_t limit, reads, writes, rejected;
} Auto9QnxBtsDmc;

typedef struct Auto9QnxBtsTrex Auto9QnxBtsTrex;
typedef struct Auto9QnxBtsTrexWord {
    MemoryRegion region;
    Auto9QnxBtsTrex *bank;
    uint32_t value, expected;
} Auto9QnxBtsTrexWord;

struct Auto9QnxBtsTrex {
    Auto9QnxBtsTrexWord word[G_N_ELEMENTS(auto9_qnx_bts_trex_init)];
    uint32_t reads, writes, rejected;
};

typedef struct Auto9QnxBtsQos {
    MemoryRegion region;
    Auto9QnxBts *parent;
    uint32_t value[G_N_ELEMENTS(auto9_qnx_bts_qos_words)], touched;
} Auto9QnxBtsQos;

struct Auto9QnxBts {
    SysBusDevice parent_obj;
    Auto9QnxBtsDmc dmc[G_N_ELEMENTS(auto9_qnx_bts_dmc)];
    Auto9QnxBtsTrex trex[G_N_ELEMENTS(auto9_qnx_bts_trex)];
    Auto9QnxBtsQos qos[G_N_ELEMENTS(auto9_qnx_bts_qos)];
    uint32_t qos_reads, qos_writes, qos_rejected, qos_configured;
};

static uint64_t auto9_qnx_bts_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9QnxBtsDmc *dmc = opaque;
    dmc->reads++;
    return dmc->limit;
}

static MemTxResult auto9_qnx_bts_write(void *opaque, hwaddr off, uint64_t value,
                                       unsigned size, MemTxAttrs attrs)
{
    Auto9QnxBtsDmc *dmc = opaque;
    if (value & ~(uint64_t)AUTO9_QNX_BTS_LIMIT_MASK) {
        dmc->rejected++;
        return MEMTX_DECODE_ERROR;
    }
    dmc->limit = value;
    dmc->writes++;
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_qnx_bts_ops = {
    .read = auto9_qnx_bts_read,
    .write_with_attrs = auto9_qnx_bts_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static uint64_t auto9_qnx_bts_trex_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9QnxBtsTrexWord *word = opaque;
    word->bank->reads++;
    return word->value;
}

static MemTxResult auto9_qnx_bts_trex_write(void *opaque, hwaddr off, uint64_t value,
                                            unsigned size, MemTxAttrs attrs)
{
    Auto9QnxBtsTrexWord *word = opaque;
    /* Only the five OEM boot-init values are known. No traffic control or
     * ITMON status is simulated by these isolated configuration latches. */
    if (value != word->expected) {
        word->bank->rejected++;
        return MEMTX_DECODE_ERROR;
    }
    word->value = value;
    word->bank->writes++;
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_qnx_bts_trex_ops = {
    .read = auto9_qnx_bts_trex_read,
    .write_with_attrs = auto9_qnx_bts_trex_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static int auto9_qnx_bts_qos_index(hwaddr off)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_qnx_bts_qos_words); i++) {
        if (off == auto9_qnx_bts_qos_words[i].offset) {
            return i;
        }
    }
    return -1;
}

static MemTxResult auto9_qnx_bts_qos_read(void *opaque, hwaddr off, uint64_t *value,
                                         unsigned size, MemTxAttrs attrs)
{
    Auto9QnxBtsQos *bank = opaque;
    int word = auto9_qnx_bts_qos_index(off);
    if (word < 0) {
        bank->parent->qos_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    *value = bank->value[word];
    bank->parent->qos_reads++;
    return MEMTX_OK;
}

static MemTxResult auto9_qnx_bts_qos_write(void *opaque, hwaddr off, uint64_t value,
                                          unsigned size, MemTxAttrs attrs)
{
    Auto9QnxBtsQos *bank = opaque;
    int word = auto9_qnx_bts_qos_index(off);
    if (word < 0 || value & ~(uint64_t)auto9_qnx_bts_qos_words[word].mask) {
        bank->parent->qos_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    bank->value[word] = value;
    bank->parent->qos_writes++;
    if (off == 0 && !bank->touched) {
        /* Original bts_setqos writes the bank's control word last. */
        bank->touched = 1;
        bank->parent->qos_configured++;
    }
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_qnx_bts_qos_ops = {
    .read_with_attrs = auto9_qnx_bts_qos_read,
    .write_with_attrs = auto9_qnx_bts_qos_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_qnx_bts_reset(DeviceState *dev)
{
    Auto9QnxBts *s = AUTO9_QNX_BTS(dev);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->dmc); i++) {
        Auto9QnxBtsDmc *dmc = &s->dmc[i];
        dmc->limit = dmc->reads = dmc->writes = dmc->rejected = 0;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->trex); i++) {
        Auto9QnxBtsTrex *trex = &s->trex[i];
        trex->reads = trex->writes = trex->rejected = 0;
        for (unsigned j = 0; j < G_N_ELEMENTS(trex->word); j++) {
            trex->word[j].value = 0;
        }
    }
    s->qos_reads = s->qos_writes = s->qos_rejected = s->qos_configured = 0;
    for (unsigned i = 0; i < G_N_ELEMENTS(s->qos); i++) {
        memset(s->qos[i].value, 0, sizeof(s->qos[i].value));
        s->qos[i].touched = 0;
    }
}

static void auto9_qnx_bts_init(Object *obj)
{
    Auto9QnxBts *s = AUTO9_QNX_BTS(obj);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->dmc); i++) {
        Auto9QnxBtsDmc *dmc = &s->dmc[i];
        const char *const names[] = { "limit", "reads", "writes", "rejected" };
        uint32_t *const fields[] = { &dmc->limit, &dmc->reads, &dmc->writes, &dmc->rejected };
        memory_region_init_io(&dmc->region, obj, &auto9_qnx_bts_ops, dmc,
                              "auto9-qnx-bts-dmc", 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &dmc->region);
        for (unsigned j = 0; j < G_N_ELEMENTS(names); j++) {
            g_autofree char *name = g_strdup_printf("dmc%u-%s", i, names[j]);
            object_property_add_uint32_ptr(obj, name, fields[j], OBJ_PROP_FLAG_READ);
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->trex); i++) {
        Auto9QnxBtsTrex *trex = &s->trex[i];
        const char *const names[] = { "reads", "writes", "rejected" };
        uint32_t *const fields[] = { &trex->reads, &trex->writes, &trex->rejected };
        for (unsigned j = 0; j < G_N_ELEMENTS(trex->word); j++) {
            Auto9QnxBtsTrexWord *word = &trex->word[j];
            word->bank = trex;
            word->expected = auto9_qnx_bts_trex_init[j].init;
            memory_region_init_io(&word->region, obj, &auto9_qnx_bts_trex_ops, word,
                                  "auto9-qnx-bts-trex", 4);
            sysbus_init_mmio(SYS_BUS_DEVICE(obj), &word->region);
        }
        for (unsigned j = 0; j < G_N_ELEMENTS(names); j++) {
            g_autofree char *name = g_strdup_printf("trex%u-%s", i, names[j]);
            object_property_add_uint32_ptr(obj, name, fields[j], OBJ_PROP_FLAG_READ);
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->qos); i++) {
        Auto9QnxBtsQos *bank = &s->qos[i];
        bank->parent = s;
        memory_region_init_io(&bank->region, obj, &auto9_qnx_bts_qos_ops, bank,
                              "auto9-qnx-bts-qos", 0x58);
    }
    object_property_add_uint32_ptr(obj, "qos-reads", &s->qos_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "qos-writes", &s->qos_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "qos-rejected", &s->qos_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "qos-configured", &s->qos_configured, OBJ_PROP_FLAG_READ);
}

static void auto9_qnx_bts_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_qnx_bts_reset);
}

static const TypeInfo auto9_qnx_bts_type = {
    .name = TYPE_AUTO9_QNX_BTS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9QnxBts),
    .instance_init = auto9_qnx_bts_init,
    .class_init = auto9_qnx_bts_class,
};

static void auto9_qnx_bts_register(void)
{
    type_register_static(&auto9_qnx_bts_type);
}
type_init(auto9_qnx_bts_register)

static void auto9_qnx_bts_create(VirtMachineState *vms)
{
    if (vms->exynos_qnx_bts != 1) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_QNX_BTS);
    object_property_add_child(OBJECT(vms), "exynos-qnx-bts", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_qnx_bts_dmc); i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), i, auto9_qnx_bts_dmc[i] + 0x258);
    }
    unsigned index = G_N_ELEMENTS(auto9_qnx_bts_dmc);
    for (unsigned i = 0; i < G_N_ELEMENTS(auto9_qnx_bts_trex); i++) {
        for (unsigned j = 0; j < G_N_ELEMENTS(auto9_qnx_bts_trex_init); j++) {
            sysbus_mmio_map(SYS_BUS_DEVICE(dev), index++,
                           auto9_qnx_bts_trex[i] + auto9_qnx_bts_trex_init[j].offset);
        }
    }
    Auto9QnxBts *s = AUTO9_QNX_BTS(dev);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->qos); i++) {
        memory_region_add_subregion(get_system_memory(), auto9_qnx_bts_qos[i], &s->qos[i].region);
    }
}
