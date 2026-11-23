/* SPDX-License-Identifier: GPL-2.0-or-later
 * Auto9 PCIe2 cold ELBI probe and opt-in native PHY configuration.
 * Source-bound latches only; no link, PLL lock, IRQ or endpoint producer.
 */
#define TYPE_AUTO9_PCIE2_ELBI "exynos-auto9.pcie2-elbi"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9Pcie2Elbi, AUTO9_PCIE2_ELBI)

/* Original Linux channel 2, two lanes, bifurcation=false, SRIS=false.
 * Only the original cold/no-link configuration path. Write values are
 * bounded to that path; status words are zero (no endpoint or lock). */
static const struct {
    uint32_t address;
    uint8_t readable, write_count;
    uint32_t first, second;
} auto9_pcie2_cold_words[] = {
    {0x17730804, 1, 1, 0x0, 0x0},
    {0x17a80054, 0, 2, 0x1, 0x0},
    {0x17a80080, 0, 1, 0x4, 0x0},
    /* Late poweron reads +0x2e8/+0x2c8 then writes 1 at +0x54 and polls
     * (+0x2c8 & 0x1f)==0x11. The original timeout path then stores 0 at
     * +0x54 (`str wzr`). Keep status 0 (no link); do not synthesize 0x11. */
    {0x17a802c8, 1, 0, 0x0, 0x0},
    {0x17a802e8, 1, 0, 0x0, 0x0},
    /* Original late consumer: readl +0x3bc, OR 0x11, write; write 2 at
     * +0x3a0; readl +0x3a8, AND 0xfffff000, write. No link. */
    {0x17a803a0, 0, 1, 0x2, 0x0},
    {0x17a803a4, 1, 2, 0x0, 0x2},
    {0x17a803a8, 1, 1, 0x0, 0x0},
    {0x17a803bc, 1, 1, 0x11, 0x0},
    {0x17a803d4, 1, 1, 0x0, 0x0},
    /* The original late ELBI consumer reads +0xc00, then writes 0/3 and
     * two exact companion words. Latch them without bringing up a link. */
    {0x17a80c00, 1, 2, 0x0, 0x3},
    {0x17a80c04, 0, 1, 0x200000, 0x0},
    {0x17a80c08, 0, 1, 0xffffffff, 0x0},
    /* Original timeout dump: readl +0xc88 down to +0xc0c (32 words).
     * Return 0; this is not link or endpoint state. */
    {0x17a80c0c, 1, 0, 0x0, 0x0},
    {0x17a80c10, 1, 0, 0x0, 0x0},
    {0x17a80c14, 1, 0, 0x0, 0x0},
    {0x17a80c18, 1, 0, 0x0, 0x0},
    {0x17a80c1c, 1, 0, 0x0, 0x0},
    {0x17a80c20, 1, 0, 0x0, 0x0},
    {0x17a80c24, 1, 0, 0x0, 0x0},
    {0x17a80c28, 1, 0, 0x0, 0x0},
    {0x17a80c2c, 1, 0, 0x0, 0x0},
    {0x17a80c30, 1, 0, 0x0, 0x0},
    {0x17a80c34, 1, 0, 0x0, 0x0},
    {0x17a80c38, 1, 0, 0x0, 0x0},
    {0x17a80c3c, 1, 0, 0x0, 0x0},
    {0x17a80c40, 1, 0, 0x0, 0x0},
    {0x17a80c44, 1, 0, 0x0, 0x0},
    {0x17a80c48, 1, 0, 0x0, 0x0},
    {0x17a80c4c, 1, 0, 0x0, 0x0},
    {0x17a80c50, 1, 0, 0x0, 0x0},
    {0x17a80c54, 1, 0, 0x0, 0x0},
    {0x17a80c58, 1, 0, 0x0, 0x0},
    {0x17a80c5c, 1, 0, 0x0, 0x0},
    {0x17a80c60, 1, 0, 0x0, 0x0},
    {0x17a80c64, 1, 0, 0x0, 0x0},
    {0x17a80c68, 1, 0, 0x0, 0x0},
    {0x17a80c6c, 1, 0, 0x0, 0x0},
    {0x17a80c70, 1, 0, 0x0, 0x0},
    {0x17a80c74, 1, 0, 0x0, 0x0},
    {0x17a80c78, 1, 0, 0x0, 0x0},
    {0x17a80c7c, 1, 0, 0x0, 0x0},
    {0x17a80c80, 1, 0, 0x0, 0x0},
    {0x17a80c84, 1, 0, 0x0, 0x0},
    {0x17a80c88, 1, 0, 0x0, 0x0},
    {0x17a81400, 1, 2, 0x0, 0x1},
    {0x17a81404, 1, 2, 0x0, 0x1},
    {0x17a81408, 1, 2, 0x0, 0x1},
    {0x17ad0004, 0, 1, 0x3, 0x0},
    {0x17ad0188, 1, 0, 0x0, 0x0},
    {0x17ad0804, 0, 1, 0x3, 0x0},
    {0x17ad0988, 1, 0, 0x0, 0x0},
    {0x17b10030, 0, 1, 0xb9, 0x0},
    {0x17b10044, 0, 1, 0x3, 0x0},
    {0x17b10078, 0, 1, 0x2f, 0x0},
    {0x17b1007c, 0, 1, 0xf8, 0x0},
    {0x17b1018c, 0, 1, 0x4d, 0x0},
    {0x17b10214, 0, 1, 0x1c, 0x0},
    {0x17b10228, 0, 1, 0x56, 0x0},
    {0x17b10400, 1, 2, 0x0, 0x3},
    {0x17b10438, 0, 1, 0x1, 0x0},
    {0x17b10450, 0, 1, 0x8a, 0x0},
    {0x17b10478, 0, 1, 0x5d, 0x0},
    {0x17b1080c, 0, 1, 0x27, 0x0},
    {0x17b10818, 0, 1, 0x1f, 0x0},
    {0x17b10820, 0, 1, 0x77, 0x0},
    {0x17b10830, 0, 1, 0xfe, 0x0},
    {0x17b10838, 0, 1, 0x12, 0x0},
    {0x17b1083c, 0, 1, 0xd8, 0x0},
    {0x17b10848, 0, 1, 0x0, 0x0},
    {0x17b108ec, 0, 1, 0x3a, 0x0},
    {0x17b108f0, 0, 1, 0x1e, 0x0},
    {0x17b108f8, 0, 1, 0x1e, 0x0},
    {0x17b108fc, 0, 1, 0xf0, 0x0},
    {0x17b10914, 0, 1, 0x70, 0x0},
    {0x17b10990, 0, 1, 0xe4, 0x0},
    {0x17b10994, 0, 1, 0xe, 0x0},
    {0x17b1099c, 0, 1, 0x44, 0x0},
    {0x17b109a0, 0, 1, 0x23, 0x0},
    {0x17b109a4, 0, 1, 0x5e, 0x0},
    {0x17b10a18, 0, 1, 0x3e, 0x0},
    {0x17b10a1c, 0, 1, 0x0, 0x0},
    {0x17b10a40, 0, 1, 0xf, 0x0},
    {0x17b10a44, 0, 1, 0xf, 0x0},
    {0x17b10a48, 0, 1, 0xf, 0x0},
    {0x17b10b4c, 0, 1, 0x25, 0x0},
    {0x17b10b58, 0, 1, 0x24, 0x0},
    {0x17b10b98, 0, 1, 0x1c, 0x0},
    {0x17b10ba0, 0, 1, 0x5, 0x0},
    {0x17b10ba4, 0, 1, 0x55, 0x0},
    {0x17b10ba8, 0, 1, 0x6a, 0x0},
    {0x17b10d24, 1, 0, 0x0, 0x0},
    {0x17b1100c, 0, 1, 0x27, 0x0},
    {0x17b11018, 0, 1, 0x1f, 0x0},
    {0x17b11020, 0, 1, 0x77, 0x0},
    {0x17b11030, 0, 1, 0xfe, 0x0},
    {0x17b11038, 0, 1, 0x12, 0x0},
    {0x17b1103c, 0, 1, 0xd8, 0x0},
    {0x17b11048, 0, 1, 0x0, 0x0},
    {0x17b110ec, 0, 1, 0x3a, 0x0},
    {0x17b110f0, 0, 1, 0x1e, 0x0},
    {0x17b110f8, 0, 1, 0x1e, 0x0},
    {0x17b110fc, 0, 1, 0xf0, 0x0},
    {0x17b11114, 0, 1, 0x70, 0x0},
    {0x17b11190, 0, 1, 0xe4, 0x0},
    {0x17b11194, 0, 1, 0xe, 0x0},
    {0x17b1119c, 0, 1, 0x44, 0x0},
    {0x17b111a0, 0, 1, 0x23, 0x0},
    {0x17b111a4, 0, 1, 0x5e, 0x0},
    {0x17b11218, 0, 1, 0x3e, 0x0},
    {0x17b1121c, 0, 1, 0x0, 0x0},
    {0x17b11240, 0, 1, 0xf, 0x0},
    {0x17b11244, 0, 1, 0xf, 0x0},
    {0x17b11248, 0, 1, 0xf, 0x0},
    {0x17b1134c, 0, 1, 0x25, 0x0},
    {0x17b11358, 0, 1, 0x24, 0x0},
    {0x17b11398, 0, 1, 0x1c, 0x0},
    {0x17b113a0, 0, 1, 0x5, 0x0},
    {0x17b113a4, 0, 1, 0x55, 0x0},
    {0x17b113a8, 0, 1, 0x6a, 0x0},
    {0x17b11524, 1, 0, 0x0, 0x0},
};

typedef struct Auto9Pcie2ColdAperture {
    MemoryRegion mmio;
    Auto9Pcie2Elbi *owner;
    uint32_t base;
} Auto9Pcie2ColdAperture;

/* Original root-port setup words and the MSI target-address latches.
 * No downstream config window, MSI output, iATU, DMA or link. */
static const struct {
    uint32_t address, mask;
    bool ro_unlock;
} auto9_pcie2_dbi_words[] = {
    {0x13000000, 0xffffffff, true}, /* Vendor/device: original writes both halfwords. */
    {0x13000044, 0x00000103, false}, /* Power-management control. */
    {0x1300007c, 0x000383ff, true}, /* Link capability fields programmed by original. */
    {0x13000080, 0x00000020, false}, /* Retrain request, never asserts link status. */
    {0x130000a0, 0x0000000f, false}, /* Target speed. */
    {0x13000820, 0xffffffff, false}, /* MSI address low/high only. */
    {0x13000824, 0xffffffff, false},
    {0x13000890, 0x00010000, false}, /* Original bit16 EQ control. */
    {0x130008bc, 0x00000001, false}, /* DBI_RO_WR_EN, bit0. */
    {0x13000b40, 0x000000ff, false},
    {0x13000b44, 0x000000ff, false},
    {0x13200010, 0x00000001, true}, /* DBI2 BAR and ROM enable controls. */
    {0x13200030, 0x00000001, true},
};

struct Auto9Pcie2Elbi {
    SysBusDevice parent_obj;
    MemoryRegion regs;
    Auto9Pcie2ColdAperture cold_areas[3], dbi_areas[2];
    Auto9Pcie2Root root;
    uint32_t dbi[ARRAY_SIZE(auto9_pcie2_dbi_words)];
    uint32_t dbi_reads, dbi_writes, dbi_rejected, dbi_last_address;
    uint32_t dbi_last_value, dbi_last_write, dbi_rejected_address;
    bool dbi_ro_write;
    bool cold_profile;
    uint32_t cold_config[ARRAY_SIZE(auto9_pcie2_cold_words)];
    uint32_t cold_reads, cold_writes, cold_rejected;
    uint32_t cold_last_address, cold_last_value, cold_last_write;
    uint32_t cold_last_rejected_address;
    uint32_t config[3], reads[3], writes[3];
    uint32_t rejected, last_rejected, last_rejected_value, last_rejected_write;
};

static MemTxResult auto9_pcie2_cold_access(Auto9Pcie2Elbi *s,
                                           uint32_t address,
                                           uint64_t *value, bool write)
{
    /* O(log n), never use an address as an array index. */
    unsigned lo = 0, hi = ARRAY_SIZE(auto9_pcie2_cold_words);
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (auto9_pcie2_cold_words[mid].address < address) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < ARRAY_SIZE(auto9_pcie2_cold_words) &&
        auto9_pcie2_cold_words[lo].address == address) {
        const typeof(auto9_pcie2_cold_words[0]) *r = &auto9_pcie2_cold_words[lo];
        if (write && ((r->write_count >= 1 && *value == r->first) ||
                      (r->write_count == 2 && *value == r->second))) {
            s->cold_config[lo] = *value;
            s->cold_writes++;
        } else if (!write && r->readable) {
            *value = s->cold_config[lo];
            s->cold_reads++;
        } else {
            goto rejected;
        }
        s->cold_last_address = address;
        s->cold_last_value = *value;
        s->cold_last_write = write;
        return MEMTX_OK;
    }
rejected:
    s->cold_rejected++;
    s->cold_last_rejected_address = address;
    qemu_log_mask(LOG_UNIMP, "Auto9 PCIe2 cold unsupported %s 0x%08" PRIx32 "\n",
                  write ? "write" : "read", address);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_pcie2_cold_read(void *opaque, hwaddr off, uint64_t *v,
                                         unsigned size, MemTxAttrs attrs)
{
    Auto9Pcie2ColdAperture *a = opaque;
    return auto9_pcie2_cold_access(a->owner, a->base + off, v, false);
}

static MemTxResult auto9_pcie2_cold_write(void *opaque, hwaddr off, uint64_t v,
                                          unsigned size, MemTxAttrs attrs)
{
    Auto9Pcie2ColdAperture *a = opaque;
    return auto9_pcie2_cold_access(a->owner, a->base + off, &v, true);
}

static const MemoryRegionOps auto9_pcie2_cold_ops = {
    .read_with_attrs = auto9_pcie2_cold_read,
    .write_with_attrs = auto9_pcie2_cold_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult auto9_pcie2_dbi_access(Auto9Pcie2ColdAperture *a, hwaddr off,
                                          uint64_t *value, unsigned size, bool write)
{
    Auto9Pcie2Elbi *s = a->owner;
    uint32_t address = a->base + off;
    uint32_t offset = address & 3, base = address - offset;
    if ((size != 1 && size != 2 && size != 4) || (address & (size - 1))) {
        return MEMTX_DECODE_ERROR;
    }
    uint32_t lane_mask = UINT32_MAX >> ((4 - size) * 8);
    lane_mask <<= offset * 8;
    if (s->root.enabled && a->base == 0x13000000 &&
        auto9_pcie2_root_dbi(&s->root, off, size, value, write, s->dbi_ro_write)) {
        if (write) {
            s->dbi_writes++;
        } else {
            s->dbi_reads++;
        }
        goto accepted;
    }
    for (unsigned i = 0; i < ARRAY_SIZE(auto9_pcie2_dbi_words); i++) {
        const typeof(auto9_pcie2_dbi_words[0]) *r = &auto9_pcie2_dbi_words[i];
        if (r->address != base) {
            continue;
        }
        if (!write) {
            *value = (s->dbi[i] & lane_mask) >> (offset * 8);
            s->dbi_reads++;
        } else {
            uint32_t mask = lane_mask & r->mask;
            if (!mask || (r->ro_unlock && !s->dbi_ro_write)) {
                goto rejected;
            }
            uint32_t updated = (s->dbi[i] & ~mask) | ((*value << (offset * 8)) & mask);
            /* Retrain is a command. A disconnected PHY cannot complete a link. */
            s->dbi[i] = base == 0x13000080 ? 0 : updated;
            if (base == 0x130008bc) {
                s->dbi_ro_write = s->dbi[i] & 1;
            }
            s->dbi_writes++;
        }
        goto accepted;
    }
    if (s->root.enabled && a->base == 0x13000000 && off < 0x700) {
        /* Architected PCI configuration space: reserved fields read zero,
         * writes have no side effects. It is not a vendor MMIO catch-all. */
        if (write) {
            s->dbi_writes++;
        } else {
            *value = 0;
            s->dbi_reads++;
        }
        goto accepted;
    }
rejected:
    s->dbi_rejected++;
    s->dbi_rejected_address = address;
    qemu_log_mask(LOG_UNIMP, "Auto9 PCIe2 DBI unsupported %s 0x%08" PRIx32 "\n",
                  write ? "write" : "read", address);
    return MEMTX_DECODE_ERROR;
accepted:
    s->dbi_last_address = address;
    s->dbi_last_value = *value;
    s->dbi_last_write = write;
    return MEMTX_OK;
}

static MemTxResult auto9_pcie2_dbi_read(void *o, hwaddr off, uint64_t *v,
                                        unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_dbi_access(o, off, v, size, false);
}

static MemTxResult auto9_pcie2_dbi_write(void *o, hwaddr off, uint64_t v,
                                         unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_dbi_access(o, off, &v, size, true);
}

static const MemoryRegionOps auto9_pcie2_dbi_ops = {
    .read_with_attrs = auto9_pcie2_dbi_read,
    .write_with_attrs = auto9_pcie2_dbi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static MemTxResult auto9_pcie2_elbi_access(void *opaque, hwaddr off,
                                         uint64_t *value, bool write)
{
    Auto9Pcie2Elbi *s = opaque;
    if (off == 0x10 || off == 0x14 || off == 0x18) {
        unsigned n = (off - 0x10) / 4;
        if (write) {
            s->writes[n]++;
            s->config[n] = *value;
        } else {
            s->reads[n]++;
            *value = s->config[n];
        }
        return MEMTX_OK;
    }
    if (s->cold_profile) {
        return auto9_pcie2_cold_access(s, 0x17a80000 + off, value, write);
    }
    s->rejected++;
    s->last_rejected = off;
    s->last_rejected_value = write ? *value : 0;
    s->last_rejected_write = write;
    qemu_log_mask(LOG_UNIMP, "Auto9 PCIe2 ELBI unsupported %s 0x%" HWADDR_PRIx "\n",
                  write ? "write" : "read", off);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_pcie2_elbi_read(void *o, hwaddr off, uint64_t *v,
                                        unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_elbi_access(o, off, v, false);
}

static MemTxResult auto9_pcie2_elbi_write(void *o, hwaddr off, uint64_t v,
                                         unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_elbi_access(o, off, &v, true);
}

static const MemoryRegionOps auto9_pcie2_elbi_ops = {
    .read_with_attrs = auto9_pcie2_elbi_read,
    .write_with_attrs = auto9_pcie2_elbi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_pcie2_elbi_reset(DeviceState *dev)
{
    Auto9Pcie2Elbi *s = AUTO9_PCIE2_ELBI(dev);
    memset(s->config, 0, sizeof(s->config));
    memset(s->cold_config, 0, sizeof(s->cold_config));
    s->cold_reads = s->cold_writes = s->cold_rejected = 0;
    s->cold_last_address = s->cold_last_value = s->cold_last_write = 0;
    s->cold_last_rejected_address = 0;
    memset(s->dbi, 0, sizeof(s->dbi));
    s->dbi_reads = s->dbi_writes = s->dbi_rejected = 0;
    s->dbi_last_address = s->dbi_last_value = s->dbi_last_write = s->dbi_rejected_address = 0;
    s->dbi_ro_write = false;
    auto9_pcie2_root_reset(&s->root);
    memset(s->reads, 0, sizeof(s->reads));
    memset(s->writes, 0, sizeof(s->writes));
    s->rejected = s->last_rejected = s->last_rejected_value = s->last_rejected_write = 0;
}

static bool auto9_pcie2_aperture_get(Object *obj, Error **errp)
{
    return AUTO9_PCIE2_ELBI(obj)->root.aperture_active;
}

static void auto9_pcie2_aperture_set(Object *obj, bool active, Error **errp)
{
    Auto9Pcie2Root *root = &AUTO9_PCIE2_ELBI(obj)->root;
    if (!root->enabled) {
        error_setg(errp, "PCIe2 iATU profile is disabled");
        return;
    }
    auto9_pcie2_root_aperture(root, active);
}

static void auto9_pcie2_elbi_init(Object *obj)
{
    Auto9Pcie2Elbi *s = AUTO9_PCIE2_ELBI(obj);
    memory_region_init_io(&s->regs, obj, &auto9_pcie2_elbi_ops, s,
                          "auto9-pcie2-elbi", 0x1be8);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regs);
    for (unsigned i = 0; i < 3; i++) {
        g_autofree char *v = g_strdup_printf("config-%x", 0x10 + i * 4);
        g_autofree char *r = g_strdup_printf("reads-%x", 0x10 + i * 4);
        g_autofree char *w = g_strdup_printf("writes-%x", 0x10 + i * 4);
        object_property_add_uint32_ptr(obj, v, &s->config[i], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, r, &s->reads[i], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, w, &s->writes[i], OBJ_PROP_FLAG_READ);
    }
    auto9_pcie2_root_properties(obj, &s->root);
    object_property_add_bool(obj, "pci-aperture-active",
                             auto9_pcie2_aperture_get, auto9_pcie2_aperture_set);
    object_property_add_uint32_ptr(obj, "dbi-reads", &s->dbi_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-writes", &s->dbi_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-rejected", &s->dbi_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-last-address", &s->dbi_last_address, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-last-value", &s->dbi_last_value, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-last-write", &s->dbi_last_write, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-rejected-address", &s->dbi_rejected_address,
                                  OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dbi-guest-vendor-device", &s->dbi[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-reads", &s->cold_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-writes", &s->cold_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-rejected", &s->cold_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-last-address", &s->cold_last_address, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-last-value", &s->cold_last_value, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-last-write", &s->cold_last_write, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cold-last-rejected-address", &s->cold_last_rejected_address,
                                  OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected", &s->last_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected-value", &s->last_rejected_value,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected-write", &s->last_rejected_write,
                                   OBJ_PROP_FLAG_READ);
}

static void auto9_pcie2_elbi_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_pcie2_elbi_reset);
}

static const TypeInfo auto9_pcie2_elbi_type = {
    .name = TYPE_AUTO9_PCIE2_ELBI, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9Pcie2Elbi), .instance_init = auto9_pcie2_elbi_init,
    .class_init = auto9_pcie2_elbi_class_init,
};

static void auto9_pcie2_elbi_register(void)
{
    type_register_static(&auto9_pcie2_elbi_type);
}
type_init(auto9_pcie2_elbi_register)

static void auto9_pcie2_elbi_create(VirtMachineState *vms)
{
    if (!vms->exynos_pcie2_elbi) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_PCIE2_ELBI);
    object_property_add_child(OBJECT(vms), "exynos-pcie2-elbi", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x17a80000);
    if (vms->exynos_pcie2_pcs) {
        Auto9Pcie2Elbi *s = AUTO9_PCIE2_ELBI(dev);
        static const struct { uint32_t base, size; const char *name; } areas[] = {
            {0x17730804, 4, "auto9-pcie2-sysreg"},
            {0x17ad0000, 0xcb0, "auto9-pcie2-pcs"},
            {0x17b10000, 0x1544, "auto9-pcie2-pma"},
        };
        s->cold_profile = true;
        for (unsigned i = 0; i < ARRAY_SIZE(areas); i++) {
            Auto9Pcie2ColdAperture *a = &s->cold_areas[i];
            a->base = areas[i].base;
            a->owner = s;
            memory_region_init_io(&a->mmio, OBJECT(s), &auto9_pcie2_cold_ops, a,
                                  areas[i].name, areas[i].size);
            memory_region_add_subregion(get_system_memory(), a->base, &a->mmio);
        }
    }
    if (vms->exynos_pcie2_dbi) {
        Auto9Pcie2Elbi *s = AUTO9_PCIE2_ELBI(dev);
        static const struct { uint32_t base, size; const char *name; } areas[] = {
            {0x13000000, 0x5000, "auto9-pcie2-dbi"},
            {0x13200000, 0x1000, "auto9-pcie2-dbi-shadow"},
        };
        for (unsigned i = 0; i < ARRAY_SIZE(areas); i++) {
            Auto9Pcie2ColdAperture *a = &s->dbi_areas[i];
            a->base = areas[i].base;
            a->owner = s;
            memory_region_init_io(&a->mmio, OBJECT(s), &auto9_pcie2_dbi_ops, a,
                                  areas[i].name, areas[i].size);
            memory_region_add_subregion(get_system_memory(), a->base, &a->mmio);
        }
    }
    if (vms->exynos_pcie2_atu) {
        auto9_pcie2_root_map(OBJECT(dev), &AUTO9_PCIE2_ELBI(dev)->root);
    }
}

