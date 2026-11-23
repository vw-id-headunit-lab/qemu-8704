/* SPDX-License-Identifier: GPL-2.0-or-later */
#define TYPE_AUTO9_SYSMMU_DISCOVERY "exynos-auto9.sysmmu-discovery"
#define AUTO9_SYSMMU_INT_STATUS 0x60
#define AUTO9_SYSMMU_INT_CLEAR 0x64
#define AUTO9_SYSMMU_VM4_START 0xa0000000ull
#define AUTO9_SYSMMU_VM4_END 0xb8000000ull
#define AUTO9_SYSMMU_ROOT_SIZE 0x4000ull
#define AUTO9_SYSMMU_VM3_HIGH_START 0xa80000000ull
#define AUTO9_SYSMMU_VM3_HIGH_END 0xb80000000ull
OBJECT_DECLARE_SIMPLE_TYPE(Auto9SysmmuDiscovery, AUTO9_SYSMMU_DISCOVERY)

typedef struct Auto9DisplaySysmmuPort {
    hwaddr base;
    unsigned tlbs;
    uint32_t stream_ids[4];
} Auto9DisplaySysmmuPort;


static const Auto9DisplaySysmmuPort auto9_display_sysmmu_ports[] = {
    { 0x1aa80000, 3, { 0x0c070004, 0x0c000400, 0x0c000800 } },
    { 0x1aa90000, 3, { 0x0c070004, 0x0c000400, 0x0c000800 } },
    { 0x18c80000, 3, { 0x0c070004, 0x0c000400, 0x0c000800 } },
    { 0x1aaa0000, 4, { 0x00070001, 0x00070002, 0x00070003, 0x00070004 } },
};

struct Auto9SysmmuDiscovery {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    hwaddr base;
    unsigned vid;
    bool linux_vm3, abox_dma, mfc;
    const Auto9DisplaySysmmuPort *display;
    uint32_t version_reads, capa1_reads, rejected, last_rejected;
    uint32_t ptbase_reads, ptbase_writes, ptbase_page, flush_all;
    uint32_t pending, status_reads, clear_writes;
    uint32_t abox_ctrl, abox_cfg, abox_default, abox_writes, abox_index_mask;
    uint32_t abox_tlb[4][3];
    uint32_t ctrl, ctrl_writes, range_first, range_last, range_phase, flush_range;
    uint32_t mfc_qos, mfc_default_tlb, mfc_index[3];
    uint32_t mfc_default_writes, mfc_index_write_mask;
    uint32_t display_qos, display_default_tlb, display_tlb[4][3];
    uint32_t display_qos_writes, display_default_writes, display_index_write_mask;
};

static MemTxResult auto9_sysmmu_reject(Auto9SysmmuDiscovery *s, hwaddr offset,
                                        const char *operation)
{
    s->rejected++;
    s->last_rejected = offset;
    qemu_log_mask(LOG_UNIMP, "Auto9 SysMMU unsupported %s at 0x%" HWADDR_PRIx "\n",
                  operation, s->base + offset);
    return MEMTX_DECODE_ERROR;
}


static uint32_t *auto9_abox_sysmmu_config(Auto9SysmmuDiscovery *s, hwaddr off,
                                         uint32_t *expected, unsigned *index_bit)
{
    static const uint32_t ids[] = {0x70004, 0x70005, 0x70006, 0x70000};
    *index_bit = 0;
    if (off == 0x4) {
        *expected = 0xf80; 
        return &s->abox_cfg;
    }
    if (off == 0x2004) {
        *expected = 0;
        return &s->abox_default;
    }
    if (off >= 0x2024 && off <= 0x2094) {
        unsigned i = (off - 0x2020) >> 5;
        unsigned field;
        switch (off & 0x1f) {
        case 4: field = 0; *expected = 0; break;
        case 8: field = 1; *expected = 0x10300; break;
        case 0x14: field = 2; *expected = ids[i]; break;
        default: return NULL;
        }
        *index_bit = BIT(i * 3 + field);
        return &s->abox_tlb[i][field];
    }
    return NULL;
}


static uint32_t *auto9_mfc_sysmmu_reg(Auto9SysmmuDiscovery *s, hwaddr offset,
                                      uint32_t *expected, uint32_t *index_mask)
{
    *index_mask = 0;
    switch (offset) {
    case 0x4:
        *expected = 0xf80; 
        return &s->mfc_qos;
    case 0x2004:
        *expected = 0x60; 
        return &s->mfc_default_tlb;
    }
    if (s->base == 0x198b0000) {
        switch (offset) {
        case 0x2024: *expected = 0x20; *index_mask = BIT(0); return &s->mfc_index[0];
        case 0x2028: *expected = 0x10300; *index_mask = BIT(1); return &s->mfc_index[1];
        case 0x2034: *expected = 0x10001; *index_mask = BIT(2); return &s->mfc_index[2];
        }
    }
    return NULL;
}


static uint32_t *auto9_display_sysmmu_reg(Auto9SysmmuDiscovery *s, hwaddr off,
                                         uint32_t *expected, uint32_t *mask)
{
    *mask = 0;
    if (off == 0x4) {
        *expected = 0xf80;
        return &s->display_qos;
    }
    if (off == 0x2004) {
        *expected = 0x0a;
        return &s->display_default_tlb;
    }
    if (off >= 0x2024 && off <= 0x2094) {
        unsigned i = (off - 0x2020) >> 5;
        unsigned field;

        if (i >= s->display->tlbs) {
            return NULL;
        }
        switch (off & 0x1f) {
        case 4: field = 0; *expected = 0x0a; break;
        case 8: field = 1; *expected = 0x10100; break;
        case 0x14: field = 2; *expected = s->display->stream_ids[i]; break;
        default: return NULL;
        }
        *mask = BIT(i * 3 + field);
        return &s->display_tlb[i][field];
    }
    return NULL;
}

static MemTxResult auto9_sysmmu_read(void *opaque, hwaddr offset, uint64_t *value,
                                      unsigned size, MemTxAttrs attrs)
{
    Auto9SysmmuDiscovery *s = opaque;
    if (offset == (s->vid << 12) + 0xc) {
        s->ptbase_reads++;
        *value = s->ptbase_page;
        return MEMTX_OK;
    }
    if (s->abox_dma) {
        uint32_t expected;
        unsigned bit;
        uint32_t *reg = auto9_abox_sysmmu_config(s, offset, &expected, &bit);
        if (reg) {
            *value = *reg;
            return MEMTX_OK;
        }
        if (offset == 0x0 || offset == 0x8) {
            
            *value = offset == 0 ? s->abox_ctrl : (s->abox_ctrl == 7);
            return MEMTX_OK;
        }
    }
    if (s->linux_vm3 && !s->display && offset == 0x2004) {
        
        *value = s->mfc_default_tlb;
        return MEMTX_OK;
    }
    if (s->mfc || s->display) {
        uint32_t expected, index_mask;
        uint32_t *reg = s->display ?
            auto9_display_sysmmu_reg(s, offset, &expected, &index_mask) :
            auto9_mfc_sysmmu_reg(s, offset, &expected, &index_mask);
        if (reg) {
            *value = *reg;
            return MEMTX_OK;
        }
        if (offset == 0 || offset == 8) {
            
            *value = offset == 0 ? s->ctrl : (s->ctrl == 7);
            return MEMTX_OK;
        }
    }
    switch (offset) {
    case 0x34: 
        s->version_reads++;
        *value = 0x70000000;
        return MEMTX_OK;
    case AUTO9_SYSMMU_INT_STATUS:
        
        s->status_reads++;
        *value = s->pending;
        return MEMTX_OK;
    case 0x874: 
        s->capa1_reads++;
        *value = s->display ? (1 + s->display->tlbs) << 4 :
                 s->abox_dma ? 5 << 4 : 0;
        return MEMTX_OK;
    }
    return auto9_sysmmu_reject(s, offset, "read");
}

static bool auto9_sysmmu_vm3_high_root(uint64_t address)
{
    if (address < AUTO9_SYSMMU_VM3_HIGH_START ||
        address > AUTO9_SYSMMU_VM3_HIGH_END - AUTO9_SYSMMU_ROOT_SIZE ||
        (address & (AUTO9_SYSMMU_ROOT_SIZE - 1))) {
        return false;
    }
    
    MemoryRegionSection region = memory_region_find(get_system_memory(),
        address, AUTO9_SYSMMU_ROOT_SIZE);
    if (!region.mr) {
        return false;
    }
    bool ok = memory_region_is_ram(region.mr) &&
              int128_eq(region.size, int128_make64(AUTO9_SYSMMU_ROOT_SIZE));
    memory_region_unref(region.mr);
    return ok;
}

static bool auto9_sysmmu_vm4_high_root(uint64_t address)
{
    
    bool owned = (address >= 0xa00000000ULL &&
                  address <= 0xa80000000ULL - AUTO9_SYSMMU_ROOT_SIZE) ||
                 (address >= 0xb80000000ULL &&
                  address <= 0xc00000000ULL - AUTO9_SYSMMU_ROOT_SIZE);
    if (!owned || (address & (AUTO9_SYSMMU_ROOT_SIZE - 1))) {
        return false;
    }
    MemoryRegionSection region = memory_region_find(get_system_memory(),
        address, AUTO9_SYSMMU_ROOT_SIZE);
    if (!region.mr) {
        return false;
    }
    bool ok = memory_region_is_ram(region.mr) &&
              !memory_region_is_rom(region.mr) &&
              int128_eq(region.size, int128_make64(AUTO9_SYSMMU_ROOT_SIZE));
    memory_region_unref(region.mr);
    return ok;
}

static MemTxResult auto9_sysmmu_write(void *opaque, hwaddr offset, uint64_t value,
                                       unsigned size, MemTxAttrs attrs)
{
    Auto9SysmmuDiscovery *s = opaque;
    uint64_t address = value << 12;
    uint64_t context = s->vid << 12;
    bool owner_ram;

    if (s->linux_vm3) {
        
        owner_ram = (address >= 0xb8000000ULL &&
                     address <= 0xd0000000ULL - AUTO9_SYSMMU_ROOT_SIZE) ||
                    (address >= 0xe3c00000ULL &&
                     address <= 0xe4c00000ULL - AUTO9_SYSMMU_ROOT_SIZE) ||
                    auto9_sysmmu_vm3_high_root(address);
    } else {
        owner_ram = (address >= AUTO9_SYSMMU_VM4_START &&
                     address <= AUTO9_SYSMMU_VM4_END - AUTO9_SYSMMU_ROOT_SIZE) ||
                    auto9_sysmmu_vm4_high_root(address);
    }
    if (s->abox_dma) {
        uint32_t expected;
        unsigned bit;
        uint32_t *reg = auto9_abox_sysmmu_config(s, offset, &expected, &bit);
        if (reg && (value == expected || (offset == 0x4 && value == 0))) {
            *reg = value;
            s->abox_writes++;
            s->abox_index_mask |= bit;
            return MEMTX_OK;
        }
        if (offset == 0 && (value == 0 || value == 5 || value == 7)) {
            s->abox_ctrl = value;
            s->abox_writes++;
            return MEMTX_OK;
        }
    }
    if (s->mfc || s->display) {
        uint32_t expected, index_mask;
        uint32_t *reg = s->display ?
            auto9_display_sysmmu_reg(s, offset, &expected, &index_mask) :
            auto9_mfc_sysmmu_reg(s, offset, &expected, &index_mask);
        
        if (reg && (value == expected || (offset == 0x4 && value == 0))) {
            *reg = value;
            if (s->display) {
                s->display_qos_writes += offset == 0x4;
                s->display_default_writes += offset == 0x2004;
                s->display_index_write_mask |= index_mask;
            } else {
                s->mfc_default_writes += offset == 0x2004;
                s->mfc_index_write_mask |= index_mask;
            }
            return MEMTX_OK;
        }
        if (offset == 0 && (value == 0 || value == 5 || value == 7)) {
            s->ctrl = value;
            s->ctrl_writes++;
            if (value == 0) {
                s->range_phase = 0;
            }
            return MEMTX_OK;
        }
    }
    if (s->linux_vm3 && !s->display && offset == 0x2004 && (value & ~0xeeu) == 0) {
        s->mfc_default_tlb = value;
        s->mfc_default_writes++;
        return MEMTX_OK;
    }
    if (offset == AUTO9_SYSMMU_INT_CLEAR) {
        s->pending &= ~(uint32_t)value;
        s->clear_writes++;
        return MEMTX_OK;
    }
    
    if (offset == context + 0xc &&
        (!address || (owner_ram && !(address & (AUTO9_SYSMMU_ROOT_SIZE - 1))))) {
        s->ptbase_page = value;
        s->ptbase_writes++;
        return MEMTX_OK;
    }
    
    if (offset == context + 0x10 && value == 1 &&
        (s->ptbase_page || (s->linux_vm3 && s->base == 0x19e00000))) {
        
        s->flush_all++;
        return MEMTX_OK;
    }
    if (s->mfc || s->display) {
        
        if (offset == context + 0x20) {
            s->range_first = value;
            s->range_phase = 1;
            return MEMTX_OK;
        }
        if (offset == context + 0x24 && s->range_phase == 1 &&
            value >= s->range_first) {
            s->range_last = value;
            s->range_phase = 2;
            return MEMTX_OK;
        }
        if (offset == context + 0x18 && value == 1 && s->range_phase == 2) {
            s->flush_range++;
            s->range_phase = 0;
            return MEMTX_OK;
        }
        if (offset == context + 0x18 || offset == context + 0x20 ||
            offset == context + 0x24) {
            s->range_phase = 0;
        }
    }
    return auto9_sysmmu_reject(s, offset, "write");
}

static const MemoryRegionOps auto9_sysmmu_ops = {
    .read_with_attrs = auto9_sysmmu_read, .write_with_attrs = auto9_sysmmu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_sysmmu_reset(DeviceState *dev)
{
    Auto9SysmmuDiscovery *s = AUTO9_SYSMMU_DISCOVERY(dev);
    s->version_reads = s->capa1_reads = s->rejected = s->last_rejected = 0;
    s->ptbase_reads = s->ptbase_writes = s->ptbase_page = s->flush_all = 0;
    s->pending = s->status_reads = s->clear_writes = 0;
    s->abox_ctrl = s->abox_cfg = s->abox_default = s->abox_writes = 0;
    s->abox_index_mask = 0;
    memset(s->abox_tlb, 0, sizeof(s->abox_tlb));
    s->ctrl = s->ctrl_writes = s->range_first = s->range_last = s->range_phase = s->flush_range = 0;
    s->mfc_qos = s->mfc_default_tlb = s->mfc_default_writes = s->mfc_index_write_mask = 0;
    memset(s->mfc_index, 0, sizeof(s->mfc_index));
    s->display_qos = s->display_default_tlb = s->display_qos_writes = 0;
    s->display_default_writes = s->display_index_write_mask = 0;
    memset(s->display_tlb, 0, sizeof(s->display_tlb));
}

static void auto9_sysmmu_init(Object *obj)
{
    Auto9SysmmuDiscovery *s = AUTO9_SYSMMU_DISCOVERY(obj);
    memory_region_init_io(&s->mmio, obj, &auto9_sysmmu_ops, s, "auto9-sysmmu-discovery", 0x10000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    object_property_add_uint32_ptr(obj, "status-reads", &s->status_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "clear-writes", &s->clear_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pending", &s->pending, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "version-reads", &s->version_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptbase-reads", &s->ptbase_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptbase-writes", &s->ptbase_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "flush-all", &s->flush_all, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptbase-page", &s->ptbase_page, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "capa1-reads", &s->capa1_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected", &s->last_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mfc-flush-range", &s->flush_range, OBJ_PROP_FLAG_READ);
}

static void auto9_sysmmu_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_sysmmu_reset);
}

static const TypeInfo auto9_sysmmu_type = {
    .name = TYPE_AUTO9_SYSMMU_DISCOVERY, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9SysmmuDiscovery), .instance_init = auto9_sysmmu_init,
    .class_init = auto9_sysmmu_class,
};

static void auto9_sysmmu_register(void) { type_register_static(&auto9_sysmmu_type); }
type_init(auto9_sysmmu_register)

static void auto9_sysmmu_port(VirtMachineState *vms, hwaddr base,
                             unsigned vid, bool linux_vm3)
{
    g_autofree char *name = g_strdup_printf("exynos-sysmmu-%" HWADDR_PRIx, base);
    DeviceState *dev = qdev_new(TYPE_AUTO9_SYSMMU_DISCOVERY);
    Auto9SysmmuDiscovery *s = AUTO9_SYSMMU_DISCOVERY(dev);
    s->base = base;
    s->vid = vid;
    s->linux_vm3 = linux_vm3;
    s->abox_dma = linux_vm3 && base == 0x19e00000 && vms->exynos_abox_sysmmu;
    if (s->abox_dma) {
        object_property_add_uint32_ptr(OBJECT(s), "abox-ctrl", &s->abox_ctrl, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "abox-cfg", &s->abox_cfg, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "abox-default", &s->abox_default, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "abox-writes", &s->abox_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "abox-index-write-mask",
                                       &s->abox_index_mask, OBJ_PROP_FLAG_READ);
        for (unsigned i = 0; i < 4; i++) {
            const char *fields[] = {"config", "match", "id"};
            for (unsigned j = 0; j < G_N_ELEMENTS(fields); j++) {
                g_autofree char *key = g_strdup_printf("abox-tlb-%u-%s", i + 1, fields[j]);
                object_property_add_uint32_ptr(OBJECT(s), key, &s->abox_tlb[i][j],
                                               OBJ_PROP_FLAG_READ);
            }
        }
    }
    s->mfc = linux_vm3 && (base == 0x19880000 || base == 0x198b0000);
    if ((linux_vm3 && vid == 3) || (!linux_vm3 && vid == 4)) {
        for (unsigned i = 0; i < G_N_ELEMENTS(auto9_display_sysmmu_ports); i++) {
            if (base == auto9_display_sysmmu_ports[i].base) {
                s->display = &auto9_display_sysmmu_ports[i];
                break;
            }
        }
    }
    if (s->mfc) {
        object_property_add_uint32_ptr(OBJECT(s), "mfc-ctrl", &s->ctrl, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "mfc-qos", &s->mfc_qos, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "mfc-default-tlb", &s->mfc_default_tlb, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "mfc-default-writes", &s->mfc_default_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "mfc-index-write-mask", &s->mfc_index_write_mask, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "mfc-ctrl-writes", &s->ctrl_writes, OBJ_PROP_FLAG_READ);
    }
    if (s->display) {
        object_property_add_uint32_ptr(OBJECT(s), "display-ctrl", &s->ctrl, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-qos", &s->display_qos, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-qos-writes", &s->display_qos_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-default-tlb", &s->display_default_tlb, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-default-writes", &s->display_default_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-index-write-mask", &s->display_index_write_mask, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-ctrl-writes", &s->ctrl_writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(OBJECT(s), "display-flush-range", &s->flush_range, OBJ_PROP_FLAG_READ);
    }
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
}

static void auto9_sysmmu_create(VirtMachineState *vms)
{
    const hwaddr ports[] = { 0x19a60000, 0x19a90000, 0x1aa80000, 0x1aa90000 };
    if (vms->exynos_sysmmu_discovery) {
        for (unsigned i = 0; i < G_N_ELEMENTS(ports); i++) {
            auto9_sysmmu_port(vms, ports[i], 4, false);
        }
    }
    if (vms->exynos_linux_sysmmu) {
        static const struct { hwaddr base; unsigned vid; } vm3[] = {
            {0x18050000,0}, {0x18250000,0}, {0x18c80000,3}, {0x19880000,3},
            {0x198b0000,3}, {0x19b90000,3}, {0x19e00000,1}, {0x1a080000,0},
            {0x1a280000,0}, {0x1a8d0000,0}, {0x1a9d0000,0}, {0x1aaa0000,3},
            {0x1d800000,0}, {0x1d830000,0}, {0x1d860000,0}, {0x1d890000,0},
            {0x1d8c0000,0}, {0x1d900000,0}, {0x1d930000,0},
        };
        for (unsigned i = 0; i < G_N_ELEMENTS(vm3); i++) {
            auto9_sysmmu_port(vms, vm3[i].base, vm3[i].vid, true);
        }
    }
}

