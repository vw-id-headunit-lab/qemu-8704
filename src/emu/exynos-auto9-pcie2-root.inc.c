/* SPDX-License-Identifier: GPL-2.0-or-later
 * Native Auto9 PCIe2 root-port configuration and unrolled iATU.
 * The secondary bus is deliberately empty: no endpoint, link, IRQ or DMA.
 */
#define AUTO9_PCIE2_ATU_BASE 0x13600000
#define AUTO9_PCIE2_CFG_BASE 0x5fffd000
#define AUTO9_PCIE2_CFG_SIZE 0x2000
#define AUTO9_PCIE2_MEM_BASE 0x40000000
#define AUTO9_PCIE2_ATU_ENABLE BIT(31)
#define AUTO9_PCIE2_ATU_REGIONS 8

enum { ATU_CR1, ATU_CR2, ATU_BASE_LO, ATU_BASE_HI, ATU_LIMIT,
       ATU_TARGET_LO, ATU_TARGET_HI, ATU_LIMIT_HI, ATU_WORDS };

/* Standard type-1 bridge fields; IDs come from the existing guest DBI writes.
 * Reserved words aren't general RAM. No root BAR backs guest memory. */
static const struct {
    uint16_t off;
    uint32_t initial, writable, unlock;
} auto9_pcie2_root_words[] = {
    {0x004, 0x00100000, 0x000007ff, 0},
    {0x008, 0x06040000, 0, 0xffff0000},
    {0x00c, 0x00010000, 0x0000ffff, 0},
    {0x010, 0, 0, 0},
    {0x014, 0, 0, 0},
    {0x018, 0, 0xffffffff, 0},
    {0x01c, 0x000000f0, 0x0000f0f0, 0},
    {0x020, 0x0000fff0, 0xfff0fff0, 0},
    {0x024, 0x0000fff0, 0xfff0fff0, 0},
    {0x028, 0, 0xffffffff, 0},
    {0x02c, 0, 0xffffffff, 0},
    {0x030, 0, 0xffffffff, 0},
    {0x034, 0x40, 0, 0},
    {0x038, 0, 0, 0},
    {0x03c, 0x00000100, 0xffff00ff, 0x0000ff00},
    {0x040, 0x00037001, 0, 0}, /* PCI PM v3 -> PCIe capability. */
    {0x070, 0x00420010, 0, 0}, /* PCIe v2 type Root Port. */
    {0x074, 0, 0, 0},
    {0x078, 0, 0x00007fff, 0},
    {0x084, 0, 0, 0}, /* Slot capability: no hot-plug slot. */
    {0x088, 0, 0, 0},
    {0x08c, 0, 0x0000001f, 0},
    {0x090, 0, 0, 0},
    {0x094, 0, 0, 0},
    {0x098, 0, 0x00001fff, 0},
    {0x09c, 0, 0, 0},
    {0x710, 0, 0x003f0000, 0}, /* Port link lane mode. */
    {0x72c, 0, 0, 0},         /* PHY debug: disconnected. */
    {0x80c, 0, 0x00001f00, 0}, /* Speed change request cannot train link. */
};

typedef struct Auto9Pcie2Root {
    bool enabled, aperture_active;
    MemoryRegion atu_mmio, cfg_mmio, empty_mem_mmio;
    uint32_t config[ARRAY_SIZE(auto9_pcie2_root_words)];
    uint32_t atu[2][AUTO9_PCIE2_ATU_REGIONS][ATU_WORDS];
    uint32_t msi_enable[8], msi_mask[8];
    uint32_t discovery_reads, header_reads, atu_reads, atu_writes, atu_rejected;
    uint32_t atu_active, cfg_absent_reads, cfg_absent_writes, cfg_rejected;
    uint32_t last_atu_offset, last_atu_value;
} Auto9Pcie2Root;

static void auto9_pcie2_root_aperture(Auto9Pcie2Root *s, bool active)
{
    if (!s->enabled || s->aperture_active == active) {
        return;
    }
    memory_region_transaction_begin();
    memory_region_set_enabled(&s->empty_mem_mmio, active);
    memory_region_set_enabled(&s->cfg_mmio, active);
    memory_region_transaction_commit();
    s->aperture_active = active;
}

static void auto9_pcie2_root_reset(Auto9Pcie2Root *s)
{
    auto9_pcie2_root_aperture(s, false);
    for (unsigned i = 0; i < ARRAY_SIZE(auto9_pcie2_root_words); i++) {
        s->config[i] = auto9_pcie2_root_words[i].initial;
    }
    memset(s->atu, 0, sizeof(s->atu));
    memset(s->msi_enable, 0, sizeof(s->msi_enable));
    memset(s->msi_mask, 0xff, sizeof(s->msi_mask));
    s->discovery_reads = s->header_reads = 0;
    s->atu_reads = s->atu_writes = s->atu_rejected = s->atu_active = 0;
    s->cfg_absent_reads = s->cfg_absent_writes = s->cfg_rejected = 0;
    s->last_atu_offset = s->last_atu_value = 0;
}

static bool auto9_pcie2_root_dbi(Auto9Pcie2Root *s, uint32_t off, unsigned size,
                                  uint64_t *v, bool write, bool unlock)
{
    unsigned shift = (off & 3) * 8;
    uint32_t word = off & ~3U;
    uint32_t lane = (UINT32_MAX >> ((4 - size) * 8)) << shift;
    if (off == 0x900 && size == 4 && !write) {
        *v = UINT32_MAX; /* DWC discovery for the separate unrolled aperture. */
        s->discovery_reads++;
        return true;
    }
    if (word >= 0x828 && word < 0x888) {
        unsigned bank = (word - 0x828) / 12;
        unsigned kind = (word - 0x828) % 12;
        uint32_t *state = kind == 0 ? &s->msi_enable[bank] : &s->msi_mask[bank];
        /* No attached producer: status always zero, W1C is inert. */
        if (!write) {
            *v = kind == 8 ? 0 : (*state & lane) >> shift;
        } else if (kind != 8) {
            *state = (*state & ~lane) | ((*v << shift) & lane);
        }
        return true;
    }
    for (unsigned i = 0; i < ARRAY_SIZE(auto9_pcie2_root_words); i++) {
        const typeof(auto9_pcie2_root_words[0]) *r = &auto9_pcie2_root_words[i];
        if (r->off != word) {
            continue;
        }
        if (write) {
            uint32_t mask = lane & (r->writable | (unlock ? r->unlock : 0));
            s->config[i] = (s->config[i] & ~mask) | ((*v << shift) & mask);
        } else {
            *v = (s->config[i] & lane) >> shift;
            if (off <= 0xe && off + size > 0xe) {
                s->header_reads++;
            }
        }
        return true;
    }
    return false;
}

static bool auto9_pcie2_atu_outbound_valid(const uint32_t *w)
{
    uint32_t type = w[ATU_CR1];
    uint32_t first = w[ATU_BASE_LO], last = w[ATU_LIMIT];
    if (w[ATU_BASE_HI] || w[ATU_LIMIT_HI] || last < first) {
        return false;
    }
    if (type == 4 || type == 5) {
        /* BDF encoding is bits[31:16], bus0 is the local root not a target. */
        return first >= AUTO9_PCIE2_CFG_BASE &&
               last < AUTO9_PCIE2_CFG_BASE + AUTO9_PCIE2_CFG_SIZE &&
               !(first & 0xfff) && (last & 0xfff) == 0xfff &&
               !w[ATU_TARGET_HI] && !(w[ATU_TARGET_LO] & 0xffff) &&
               (w[ATU_TARGET_LO] >> 24) != 0;
    }
    if (type == 0) {
        uint64_t target = ((uint64_t)w[ATU_TARGET_HI] << 32) | w[ATU_TARGET_LO];
        return first >= AUTO9_PCIE2_MEM_BASE && last < AUTO9_PCIE2_CFG_BASE &&
               target <= UINT64_MAX - (last - first);
    }
    return false;
}

static MemTxResult auto9_pcie2_atu_access(Auto9Pcie2Root *s, hwaddr off,
                                          uint64_t *v, bool write)
{
    unsigned bank = off >> 9, word = (off & 0xff) >> 2;
    bool inbound = off & 0x100;
    uint32_t *w;
    if (bank >= AUTO9_PCIE2_ATU_REGIONS || (off & 3) || word >= ATU_WORDS) {
        goto rejected;
    }
    w = s->atu[inbound][bank];
    if (!write) {
        *v = w[word];
        s->atu_reads++;
    } else if (word == ATU_CR2) {
        bool enable = *v & AUTO9_PCIE2_ATU_ENABLE;
        bool was_enabled = w[word] & AUTO9_PCIE2_ATU_ENABLE;
        if ((*v & ~((uint64_t)AUTO9_PCIE2_ATU_ENABLE)) ||
            (enable && (inbound || !auto9_pcie2_atu_outbound_valid(w)))) {
            goto rejected;
        }
        w[word] = *v;
        if (enable && !was_enabled) {
            s->atu_active++;
        } else if (!enable && was_enabled) {
            s->atu_active--;
        }
        s->atu_writes++;
    } else {
        if (word == ATU_CR1 && *v != 0 && *v != 4 && *v != 5) {
            goto rejected;
        }
        /* No effective stale/half-updated active decode during reprogramming. */
        if (w[ATU_CR2] & AUTO9_PCIE2_ATU_ENABLE) {
            w[ATU_CR2] = 0;
            s->atu_active--;
        }
        w[word] = *v;
        s->atu_writes++;
    }
    s->last_atu_offset = off;
    s->last_atu_value = *v;
    return MEMTX_OK;
rejected:
    s->atu_rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 PCIe2 iATU unsupported %s 0x%" HWADDR_PRIx "\n",
                  write ? "write" : "read", off);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_pcie2_atu_read(void *o, hwaddr off, uint64_t *v,
                                        unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_atu_access(o, off, v, false);
}

static MemTxResult auto9_pcie2_atu_write(void *o, hwaddr off, uint64_t v,
                                         unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_atu_access(o, off, &v, true);
}

static MemTxResult auto9_pcie2_cfg_access(Auto9Pcie2Root *s, hwaddr off,
                                          uint64_t *v, unsigned size, bool write)
{
    uint64_t first = AUTO9_PCIE2_CFG_BASE + off;
    if ((size != 1 && size != 2 && size != 4) || (first & (size - 1))) {
        s->cfg_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    for (unsigned bank = 0; bank < AUTO9_PCIE2_ATU_REGIONS; bank++) {
        const uint32_t *w = s->atu[0][bank];
        if ((w[ATU_CR2] & AUTO9_PCIE2_ATU_ENABLE) &&
            (w[ATU_CR1] == 4 || w[ATU_CR1] == 5) &&
            first >= w[ATU_BASE_LO] && first + size - 1 <= w[ATU_LIMIT]) {
            /* Valid routed config transaction to a genuinely empty bus. */
            if (write) {
                s->cfg_absent_writes++;
            } else {
                *v = UINT32_MAX >> ((4 - size) * 8);
                s->cfg_absent_reads++;
            }
            return MEMTX_OK;
        }
    }
    s->cfg_rejected++;
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_pcie2_cfg_read(void *o, hwaddr off, uint64_t *v,
                                        unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_cfg_access(o, off, v, size, false);
}

static MemTxResult auto9_pcie2_cfg_write(void *o, hwaddr off, uint64_t v,
                                         unsigned size, MemTxAttrs attrs)
{
    return auto9_pcie2_cfg_access(o, off, &v, size, true);
}

static const MemoryRegionOps auto9_pcie2_atu_ops = {
    .read_with_attrs = auto9_pcie2_atu_read,
    .write_with_attrs = auto9_pcie2_atu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult auto9_pcie2_empty_mem_read(void *o, hwaddr off, uint64_t *v,
                                              unsigned size, MemTxAttrs attrs)
{
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_pcie2_empty_mem_write(void *o, hwaddr off, uint64_t v,
                                               unsigned size, MemTxAttrs attrs)
{
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps auto9_pcie2_empty_mem_ops = {
    .read_with_attrs = auto9_pcie2_empty_mem_read,
    .write_with_attrs = auto9_pcie2_empty_mem_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
    .impl = { .min_access_size = 1, .max_access_size = 8 },
};

static const MemoryRegionOps auto9_pcie2_cfg_ops = {
    .read_with_attrs = auto9_pcie2_cfg_read,
    .write_with_attrs = auto9_pcie2_cfg_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void auto9_pcie2_root_properties(Object *obj, Auto9Pcie2Root *s)
{
    object_property_add_uint32_ptr(obj, "atu-discovery-reads", &s->discovery_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "root-header-reads", &s->header_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atu-reads", &s->atu_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atu-writes", &s->atu_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atu-rejected", &s->atu_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atu-active", &s->atu_active, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atu-last-offset", &s->last_atu_offset, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "atu-last-value", &s->last_atu_value, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cfg-absent-reads", &s->cfg_absent_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cfg-absent-writes", &s->cfg_absent_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cfg-rejected", &s->cfg_rejected, OBJ_PROP_FLAG_READ);
}

static void auto9_pcie2_root_map(Object *obj, Auto9Pcie2Root *s)
{
    s->enabled = true;
    memory_region_init_io(&s->atu_mmio, obj, &auto9_pcie2_atu_ops, s,
                          "auto9-pcie2-iatu-unrolled", 0x1000);
    memory_region_init_io(&s->cfg_mmio, obj, &auto9_pcie2_cfg_ops, s,
                          "auto9-pcie2-empty-config", AUTO9_PCIE2_CFG_SIZE);
    memory_region_add_subregion(get_system_memory(), AUTO9_PCIE2_ATU_BASE, &s->atu_mmio);
    /* Original VM3 HCB owns 0x40000000..0x5fffffff as PCI, not RAM.
     * Overlay the HCB-owned PCI aperture on generic virt's RAM backing. */
    memory_region_init_io(&s->empty_mem_mmio, obj, &auto9_pcie2_empty_mem_ops, s,
                          "auto9-pcie2-empty-memory", 0x20000000);
    /* The preceding original LK harness uses this physical range as a
     * temporary loader workspace. It must finish before PCI decoding. */
    memory_region_set_enabled(&s->empty_mem_mmio, false);
    memory_region_set_enabled(&s->cfg_mmio, false);
    memory_region_add_subregion_overlap(get_system_memory(), AUTO9_PCIE2_MEM_BASE,
                                        &s->empty_mem_mmio, 1);
    memory_region_add_subregion_overlap(get_system_memory(), AUTO9_PCIE2_CFG_BASE,
                                        &s->cfg_mmio, 2);
}

