/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original Auto9 ABOX control, cold DMA status, route mux and UAIF format.
 * No audio DMA or completion interrupts.
 */
typedef struct Auto9AboxSftWord {
    MemoryRegion region;
    uint32_t value, reset_value, mask, reads, writes, rejects;
    bool read_only;
} Auto9AboxSftWord;

typedef struct Auto9AboxSft {
    unsigned count;
    Auto9AboxSftWord words[];
} Auto9AboxSft;

static uint64_t auto9_abox_sft_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9AboxSftWord *word = opaque;
    word->reads++;
    return word->value;
}

static MemTxResult auto9_abox_sft_write(void *opaque, hwaddr off, uint64_t value,
                                        unsigned size, MemTxAttrs attrs)
{
    Auto9AboxSftWord *word = opaque;
    if (word->read_only || ((value ^ word->reset_value) & ~((uint64_t)word->mask))) {
        word->rejects++;
        return MEMTX_ACCESS_ERROR;
    }
    word->value = value;
    word->writes++;
    return MEMTX_OK;
}

static const MemoryRegionOps auto9_abox_sft_control_ops = {
    .read = auto9_abox_sft_read,
    .write_with_attrs = auto9_abox_sft_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_abox_sft_reset(void *opaque)
{
    Auto9AboxSft *s = opaque;
    for (unsigned i = 0; i < s->count; i++) {
        Auto9AboxSftWord *word = &s->words[i];
        word->value = word->reset_value;
        word->reads = word->writes = word->rejects = 0;
    }
}

static void auto9_abox_sft_create(Object *parent)
{
    static const struct {
        hwaddr address;
        uint32_t mask;
        const char *name;
        uint32_t reset_value;
    } regs[] = {
        { 0x19db0020, 0xc, "control", 0 }, /* Original probe regmap_update_bits */
        { 0x19db8040, 1, "rdma14-mux", 0 }, /* Original sft_rdma14_fc_enum */
        { 0x19db9040, 1, "rdma15-mux", 0 }, /* Original sft_rdma15_fc_enum */
        { 0x19db0030, 0x1f1f, "speaker-route", 0 }, /* Original UAIF4/5 enums */
        { 0x19db8030, 0, "rdma14-status", 0 }, /* Bit31 running; no DMA engine. */
        { 0x19db9030, 0, "rdma15-status", 0 },
        /* Format/polarity/clock role only; no clock generation or audio. */
        { 0x19dbc000, 0x00000004, "uaif4-ctrl0", 0x00108090 },
        { 0x19dbc004, 0x03800000, "uaif4-ctrl1", 0x04000000 },
        { 0x19dbd000, 0x00000004, "uaif5-ctrl0", 0x00108090 },
        { 0x19dbd004, 0x03800000, "uaif5-ctrl1", 0x04000000 },
        /* VM3 ASoC ASRC mux only; channel DMA and sample flow are not mapped. */
        { 0x19d30040, 0xf00, "linux-rdma0-asrc", 0 },
        { 0x19d31040, 0xf00, "linux-rdma1-asrc", 0 },
        { 0x19d33040, 0xf00, "linux-rdma3-asrc", 0 },
        { 0x19d34040, 0xf00, "linux-rdma4-asrc", 0 },
        { 0x19d3c040, 0xf00, "linux-rdma12-asrc", 0 },
        { 0x19d3d040, 0xf00, "linux-rdma13-asrc", 0 },
        { 0x19d71040, 0xf00, "linux-wdma1-asrc", 0 },
        { 0x19d73040, 0xf00, "linux-wdma3-asrc", 0 },
        { 0x19d74040, 0xf00, "linux-wdma4-asrc", 0 },
        { 0x19d7c040, 0xf00, "linux-wdma12-asrc", 0 },
        { 0x19d7d040, 0xf00, "linux-wdma13-asrc", 0 },
        { 0x19d91000, 0x00000004, "linux-uaif1-ctrl0", 0x00108090 },
        { 0x19d91004, 0x03800000, "linux-uaif1-ctrl1", 0x04000000 },
        { 0x19d93000, 0x00000004, "linux-uaif3-ctrl0", 0x00108090 },
        { 0x19d93004, 0x03800000, "linux-uaif3-ctrl1", 0x04000000 },
        /* Shared SFR cold route selectors: no write or audio data path. */
        { 0x19d40040, 0, "shared-route-40", 0 },
        { 0x19d40044, 0, "shared-route-44", 0 },
        { 0x19d40048, 0, "shared-route-48", 0 },
        { 0x19d4004c, 0, "shared-route-4c", 0 },
        { 0x19d40060, 0, "shared-route-60", 0 },
        { 0x19d40064, 0, "shared-route-64", 0 },
        { 0x19d40068, 0, "shared-route-68", 0 },
        /* DMA engine is absent: inactive, no address progress or completion. */
        { 0x19d30030, 0, "linux-rdma0-status", 0 },
        { 0x19d31030, 0, "linux-rdma1-status", 0 },
        { 0x19d33030, 0, "linux-rdma3-status", 0 },
        { 0x19d34030, 0, "linux-rdma4-status", 0 },
        { 0x19d3c030, 0, "linux-rdma12-status", 0 },
        { 0x19d3d030, 0, "linux-rdma13-status", 0 },
        { 0x19d71030, 0, "linux-wdma1-status", 0 },
        { 0x19d73030, 0, "linux-wdma3-status", 0 },
        { 0x19d74030, 0, "linux-wdma4-status", 0 },
        { 0x19d7c030, 0, "linux-wdma12-status", 0 },
        { 0x19d7d030, 0, "linux-wdma13-status", 0 },
        { 0x19d92000, 0x00000004, "qnx-uaif2-ctrl0", 0x00108090 },
        { 0x19d92004, 0x03800000, "qnx-uaif2-ctrl1", 0x04000000 },
    };
    Auto9AboxSft *s = g_malloc0(sizeof(*s) + sizeof(s->words[0]) * G_N_ELEMENTS(regs));
    s->count = G_N_ELEMENTS(regs);
    Object *obj = object_new(TYPE_CONTAINER);
    object_property_add_child(parent, "abox-sft", obj);
    object_unref(obj);
    for (unsigned i = 0; i < G_N_ELEMENTS(regs); i++) {
        Auto9AboxSftWord *word = &s->words[i];
        const char *name = regs[i].name;
        g_autofree char *reads = i ? g_strdup_printf("%s-reads", name) : g_strdup("reads");
        g_autofree char *writes = i ? g_strdup_printf("%s-writes", name) : g_strdup("writes");
        g_autofree char *rejects = i ? g_strdup_printf("%s-rejects", name) : g_strdup("rejects");
        word->mask = regs[i].mask;
        word->value = word->reset_value = regs[i].reset_value;
        word->read_only = regs[i].mask == 0;
        object_property_add_uint32_ptr(obj, name, &word->value, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, reads, &word->reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, writes, &word->writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, rejects, &word->rejects, OBJ_PROP_FLAG_READ);
        memory_region_init_io(&word->region, obj, &auto9_abox_sft_control_ops, word, name, 4);
        memory_region_add_subregion(get_system_memory(), regs[i].address, &word->region);
    }
    qemu_register_reset(auto9_abox_sft_reset, s);
}
