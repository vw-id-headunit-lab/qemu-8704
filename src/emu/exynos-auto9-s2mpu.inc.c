/* SPDX-License-Identifier: GPL-2.0-or-later
 * Native Auto9 S2MPU permission-table format and observed register interface.
 * Eight VMIDs, 64 x 1GiB descriptors each, optional packed 2-bit L2 permissions.
 * The DMA address space really enforces permissions; it is not system RAM alias.
 * VMID is carried internally in MemTxAttrs.pid (not a PCI PASID on this bus).
 * DMA masters and display engines remain separate devices.
 */
#define TYPE_EXYNOS_AUTO9_S2MPU "exynos-auto9.s2mpu"
#define TYPE_EXYNOS_AUTO9_S2IOMMU "exynos-auto9.s2iommu"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9S2mpu, EXYNOS_AUTO9_S2MPU)
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9S2Iommu, EXYNOS_AUTO9_S2IOMMU)

struct ExynosAuto9S2Iommu {
    IOMMUMemoryRegion parent_obj;
    ExynosAuto9S2mpu *owner;
};

struct ExynosAuto9S2mpu {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    ExynosAuto9S2Iommu iommu;
    AddressSpace dma_as;
    qemu_irq irq;
    uint32_t control[2];
    uint32_t ecc;
    uint32_t irq_enable;
    uint32_t irq_status;
    uint32_t irq_clear_writes;
    uint32_t irq_clear_last_mask;
    uint32_t fault[8][8];
    uint32_t l1[8][64][2];
    uint32_t invalidate[3];
    uint32_t debug_index;
    uint64_t invalidations;
};

static void exynos_auto9_s2mpu_irq(ExynosAuto9S2mpu *s)
{
    qemu_set_irq(s->irq, (s->control[0] & 2) &&
                 (s->irq_enable & s->irq_status & 0xff));
}

static void exynos_auto9_s2mpu_invalidate(ExynosAuto9S2mpu *s, unsigned vid)
{
    /* No internal permission cache: invalidate downstream IOMMU users too. */
    IOMMUTLBEvent event = {
        .type = IOMMU_NOTIFIER_UNMAP,
        .entry = { .iova = 0, .translated_addr = 0,
                   .addr_mask = (1ULL << 36) - 1, .perm = IOMMU_NONE },
    };
    memory_region_notify_iommu(IOMMU_MEMORY_REGION(&s->iommu), vid, event);
    s->invalidations++;
}

static void exynos_auto9_s2mpu_fault(ExynosAuto9S2mpu *s, unsigned vid,
                                    hwaddr address, IOMMUAccessFlags access)
{
    uint32_t bit = 1U << vid;
    if (!(s->irq_status & bit)) {
        s->fault[vid][1] = address;
        s->fault[vid][2] = address >> 32;
        /* Original INFO: VID[26:24], RW[20], AxID[15:0]. AxID unknown here. */
        s->fault[vid][4] = (vid << 24) | ((access & IOMMU_WO) ? 1U << 20 : 0);
    }
    s->irq_status |= bit;
    exynos_auto9_s2mpu_irq(s);
}

static IOMMUTLBEntry exynos_auto9_s2mpu_translate(IOMMUMemoryRegion *iommu,
                                                hwaddr address,
                                                IOMMUAccessFlags access,
                                                int index)
{
    ExynosAuto9S2mpu *s = EXYNOS_AUTO9_S2IOMMU(iommu)->owner;
    unsigned vid = index;
    uint64_t granule = 1ULL << 30;
    unsigned permission = 0;
    MemTxResult result = MEMTX_OK;
    IOMMUTLBEntry entry = { .target_as = &address_space_memory };
    if (!(s->control[0] & 1)) {
        permission = IOMMU_RW;
    } else if (address < (1ULL << 36) && vid < 8) {
        uint32_t *descriptor = s->l1[vid][address >> 30];
        uint32_t attr = descriptor[1];
        if (!(attr & 1)) {
            permission = (attr >> 1) & 3;
        } else {
            unsigned kind = (attr >> 4) & 3;
            static const unsigned shift[3] = {12, 16, 21};
            if (kind < 3) {
                uint64_t slot;
                uint32_t packed;
                granule = 1ULL << shift[kind];
                slot = (address & ((1ULL << 30) - 1)) >> shift[kind];
                packed = address_space_ldl_le(&address_space_memory,
                            ((uint64_t)descriptor[0] << 4) + (slot / 16) * 4,
                            MEMTXATTRS_UNSPECIFIED, &result);
                if (result == MEMTX_OK) {
                    permission = (packed >> ((slot & 15) * 2)) & 3;
                }
            }
        }
    }
    entry.iova = address & ~(granule - 1);
    entry.translated_addr = entry.iova;
    entry.addr_mask = granule - 1;
    entry.perm = permission;
    if (access != IOMMU_NONE && (permission & access) != access) {
        exynos_auto9_s2mpu_fault(s, vid & 7, address, access);
    }
    return entry;
}

static int exynos_auto9_s2mpu_index(IOMMUMemoryRegion *iommu, MemTxAttrs attrs)
{
    return attrs.pid & 7;
}

static int exynos_auto9_s2mpu_indexes(IOMMUMemoryRegion *iommu)
{
    return 8;
}

static void exynos_auto9_s2iommu_class_init(ObjectClass *klass, const void *data)
{
    IOMMUMemoryRegionClass *imc = IOMMU_MEMORY_REGION_CLASS(klass);
    imc->translate = exynos_auto9_s2mpu_translate;
    imc->attrs_to_index = exynos_auto9_s2mpu_index;
    imc->num_indexes = exynos_auto9_s2mpu_indexes;
}

static MemTxResult exynos_auto9_s2mpu_read(void *opaque, hwaddr offset,
                                         uint64_t *value, unsigned size,
                                         MemTxAttrs attrs)
{
    ExynosAuto9S2mpu *s = opaque;
    if (offset >= 0x4000 && offset < 0x5000) {
        unsigned word = (offset - 0x4000) / 4;
        *value = s->l1[word / 128][(word / 2) & 63][word & 1];
        return MEMTX_OK;
    }
    if (offset >= 0x2000 && offset < 0x2100) {
        unsigned vid = (offset - 0x2000) / 32;
        unsigned word = (offset / 4) & 7;
        *value = (word == 0) ? s->irq_status : s->fault[vid][word];
        return MEMTX_OK;
    }
    switch (offset) {
    case 0: case 4: *value = s->control[offset / 4]; break;
    case 0x1c: *value = s->ecc; break;
    case 0x20: case 0x24: case 0x28: *value = s->irq_enable; break;
    case 0x1000: *value = 0; break; /* synchronous invalidation, no pending work */
    case 0x1020: case 0x1024: case 0x1028:
        *value = s->invalidate[(offset - 0x1020) / 4]; break;
    case 0x3000: *value = s->debug_index; break;
    case 0x3004: case 0x3008: case 0x3010:
        *value = 0; break; /* this implementation has no internal MPT cache */
    default:
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.s2mpu unknown read 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_s2mpu_write(void *opaque, hwaddr offset,
                                          uint64_t value, unsigned size,
                                          MemTxAttrs attrs)
{
    ExynosAuto9S2mpu *s = opaque;
    if (offset >= 0x4000 && offset < 0x5000) {
        unsigned word = (offset - 0x4000) / 4;
        s->l1[word / 128][(word / 2) & 63][word & 1] = value;
        return MEMTX_OK;
    }
    switch (offset) {
    case 0: case 4:
        s->control[offset / 4] = value;
        for (unsigned i = 0; i < 8; i++) {
            exynos_auto9_s2mpu_invalidate(s, i);
        }
        break;
    case 0x1c: s->ecc = value; break;
    case 0x20: s->irq_enable |= value & 0xff; break;
    case 0x24: s->irq_enable &= ~(value & 0xff); break;
    case 0x2c:
        /* OEM EL2 writes one bit per VID; fault status at 0x2000 is read-only. */
        s->irq_clear_writes++;
        s->irq_clear_last_mask = value & 0xff;
        s->irq_status &= ~s->irq_clear_last_mask;
        break;
    case 0x1000:
        exynos_auto9_s2mpu_invalidate(s, (value >> 16) & 7); break;
    case 0x1020: case 0x1024: case 0x1028:
        s->invalidate[(offset - 0x1020) / 4] = value;
        if (offset == 0x1020) {
            exynos_auto9_s2mpu_invalidate(s, (value >> 16) & 7);
        }
        break;
    case 0x3000: s->debug_index = value; break;
    default:
        qemu_log_mask(LOG_UNIMP, "exynos-auto9.s2mpu unknown write 0x%llx\n",
                      (unsigned long long)offset);
        return MEMTX_DECODE_ERROR;
    }
    exynos_auto9_s2mpu_irq(s);
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_s2mpu_ops = {
    .read_with_attrs = exynos_auto9_s2mpu_read,
    .write_with_attrs = exynos_auto9_s2mpu_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static void exynos_auto9_s2mpu_reset(DeviceState *dev)
{
    ExynosAuto9S2mpu *s = EXYNOS_AUTO9_S2MPU(dev);
    memset(s->control, 0, sizeof(s->control));
    memset(s->fault, 0, sizeof(s->fault));
    memset(s->l1, 0, sizeof(s->l1));
    memset(s->invalidate, 0, sizeof(s->invalidate));
    s->ecc = s->irq_enable = s->irq_status = s->debug_index = 0;
    s->invalidations = 0;
    s->irq_clear_writes = s->irq_clear_last_mask = 0;
    exynos_auto9_s2mpu_irq(s);
}

static void exynos_auto9_s2mpu_init(Object *obj)
{
    ExynosAuto9S2mpu *s = EXYNOS_AUTO9_S2MPU(obj);
    memory_region_init_io(&s->iomem, obj, &exynos_auto9_s2mpu_ops, s,
                          TYPE_EXYNOS_AUTO9_S2MPU, 0x5000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    memory_region_init_iommu(&s->iommu, sizeof(s->iommu), TYPE_EXYNOS_AUTO9_S2IOMMU,
                             obj, "exynos-auto9.s2mpu-dma", 1ULL << 36);
    s->iommu.owner = s;
    object_property_add_uint32_ptr(obj, "irq-enable", &s->irq_enable, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-status", &s->irq_status, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-clear-writes", &s->irq_clear_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-clear-last-mask", &s->irq_clear_last_mask,
                                   OBJ_PROP_FLAG_READ);
    address_space_init(&s->dma_as, MEMORY_REGION(&s->iommu), "exynos-auto9.s2mpu-dma");
}

static void exynos_auto9_s2mpu_finalize(Object *obj)
{
    address_space_destroy(&EXYNOS_AUTO9_S2MPU(obj)->dma_as);
}

static void exynos_auto9_s2mpu_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_s2mpu_reset);
}

static const TypeInfo exynos_auto9_s2mpu_types[] = {
    { .name = TYPE_EXYNOS_AUTO9_S2IOMMU, .parent = TYPE_IOMMU_MEMORY_REGION,
      .instance_size = sizeof(ExynosAuto9S2Iommu),
      .class_init = exynos_auto9_s2iommu_class_init },
    { .name = TYPE_EXYNOS_AUTO9_S2MPU, .parent = TYPE_SYS_BUS_DEVICE,
      .instance_size = sizeof(ExynosAuto9S2mpu),
      .instance_init = exynos_auto9_s2mpu_init,
      .instance_finalize = exynos_auto9_s2mpu_finalize,
      .class_init = exynos_auto9_s2mpu_class_init },
};
DEFINE_TYPES(exynos_auto9_s2mpu_types)

static void exynos_auto9_s2mpu_create(VirtMachineState *vms, unsigned number,
                                     hwaddr base, DeviceState *controller,
                                     unsigned interrupt)
{
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_S2MPU);
    g_autofree char *name = g_strdup_printf("exynos-s2mpu%u", number);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(controller, interrupt));
}
