/* SPDX-License-Identifier: GPL-2.0-or-later
 * Independent DPP and IDMA interrupt words for original VM2/VM3/VM4.
 * Masks bind to the original per-domain driver RMW pairs. No stream
 * completion can be generated until a real DPP/IDMA requester is wired.
 */
#define TYPE_AUTO9_DPP_IRQ "exynos-auto9.dpp-irq"
#define AUTO9_DPP_WORDS 12
#define AUTO9_QNX_DPP_PORTS 6
#define AUTO9_LINUX_DPP_PORTS 3
OBJECT_DECLARE_SIMPLE_TYPE(Auto9DppIrq, AUTO9_DPP_IRQ)

static const struct {
    uint8_t idma;
    uint16_t offset;
} auto9_qnx_dpp_config[] = {
    { 0, 0x00 }, { 0, 0x08 }, { 0, 0x18 }, { 0, 0xa54 }, { 0, 0xd00 },
    { 1, 0x00 }, { 1, 0x0c }, { 1, 0x58 }, { 1, 0x08 }, { 1, 0x10 }, { 1, 0x14 }, { 1, 0x18 }, { 1, 0x24 },
    { 1, 0x28 }, { 1, 0x30 }, { 1, 0x40 }, { 1, 0x44 }, { 1, 0x48 },
    { 1, 0x4c }, { 1, 0x5c }, { 1, 0x68 },
};

typedef struct Auto9DppIrqWord {
    MemoryRegion region;
    uint32_t enable_mask, status_mask;
    uint32_t config, pending, reads, writes;
} Auto9DppIrqWord;

typedef struct Auto9QnxDppWord {
    MemoryRegion region;
    uint32_t value, reads, writes, resets, rejected;
    Auto9DppIrq *bank;
    unsigned port, kind, offset;
} Auto9QnxDppWord;

/* Stock Linux dpp_reg_enable, set_config and reset. The IRQ words keep
 * their separate W1C state; these words are not a DMA execution engine. */
static const uint16_t auto9_linux_dpp_offsets[] = { 0x00, 0x08, 0x18, 0xa54, 0xd00 };
static const uint16_t auto9_linux_idma_offsets[] = {
    0x00, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x24, 0x28, 0x30,
    0x40, 0x44, 0x48, 0x4c, 0x58, 0x5c, 0x68,
};
static const uint16_t auto9_linux_common_offsets[] = {
    0x20, 0x24, 0x28, 0x2c, 0x70, 0x74, 0x80,
};

typedef struct Auto9LinuxDppPage {
    MemoryRegion region;
    Auto9DppIrqWord *irq;
    const uint16_t *offsets;
    unsigned count;
    uint32_t value[G_N_ELEMENTS(auto9_linux_idma_offsets)];
    uint32_t writes, rejected, resets;
    bool idma;
} Auto9LinuxDppPage;

struct Auto9DppIrq {
    SysBusDevice parent_obj;
    Auto9DppIrqWord words[AUTO9_DPP_WORDS];
    Auto9QnxDppWord config[AUTO9_QNX_DPP_PORTS][G_N_ELEMENTS(auto9_qnx_dpp_config)];
    hwaddr qnx_idma_base[AUTO9_QNX_DPP_PORTS];
    Auto9QnxDppWord qnx_common[2][2]; /* DPUM/DPUS0 QoS, when Linux has no common page. */
    Auto9LinuxDppPage linux_pages[2 * AUTO9_LINUX_DPP_PORTS + 2];
};

typedef struct Auto9QnxDppSource {
    uint32_t address, control, frame_size, crop_offset, crop_size, output_size, writes;
} Auto9QnxDppSource;

static Auto9QnxDppSource auto9_qnx_dpp_source(Auto9DppIrq *s, hwaddr idma)
{
    Auto9QnxDppSource result = { 0 };
    if (!s) {
        return result;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->config); i++) {
        if (s->qnx_idma_base[i] != idma) {
            continue;
        }
        /* The DPP/IDMA register pairs follow dpp_reg_set_config. Reject
         * partial initialisation: no guessed address, size, or crop. */
        for (unsigned j = 0; j < G_N_ELEMENTS(s->config[i]); j++) {
            Auto9QnxDppWord *word = &s->config[i][j];
            unsigned offset = auto9_qnx_dpp_config[j].offset;
            uint32_t *target = NULL;
            if (auto9_qnx_dpp_config[j].idma) {
                switch (offset) {
                case 0x08: target = &result.control; break;
                case 0x10: target = &result.frame_size; break;
                case 0x14: target = &result.crop_offset; break;
                case 0x18: target = &result.crop_size; break;
                case 0x40: target = &result.address; result.writes = word->writes; break;
                }
            } else if (offset == 0x18) {
                target = &result.output_size;
            }
            if (target) {
                if (!word->writes) {
                    return (Auto9QnxDppSource) { 0 };
                }
                *target = word->value;
            }
        }
        return result;
    }
    return result;
}

static uint64_t auto9_dpp_irq_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9DppIrqWord *word = opaque;
    word->reads++;
    return word->config | word->pending;
}

static void auto9_dpp_irq_write(void *opaque, hwaddr off, uint64_t value,
                                 unsigned size)
{
    Auto9DppIrqWord *word = opaque;
    word->writes++;
    word->config = value & word->enable_mask;
    word->pending &= ~(value & word->status_mask); /* W1C, not an IRQ source. */
}

static const MemoryRegionOps auto9_dpp_irq_ops = {
    .read = auto9_dpp_irq_read, .write = auto9_dpp_irq_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static uint64_t auto9_qnx_dpp_config_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9QnxDppWord *word = opaque;
    word->reads++;
    return word->value;
}

static MemTxResult auto9_qnx_dpp_config_write(void *opaque, hwaddr off,
                                             uint64_t value, unsigned size,
                                             MemTxAttrs attrs)
{
    Auto9QnxDppWord *word = opaque;
    Auto9DppIrq *bank = word->bank;

    if (word->kind == 2) {
        /* Original dpp_reg_enable writes this QoS pattern, not an IRQ. */
        if (value != 0x44444444) {
            goto reject;
        }
    } else if (!word->offset) {
        uint32_t clocks = word->kind ? 0x200 : 0x700;
        /* OP_STATUS bit 2 is read-only and idle without a stream requester.
         * The original driver clears the clock gates before programming DMA. */
        if (value & ~(clocks | BIT(24))) {
            goto reject;
        }
        if (value & BIT(24)) {
            /* No transactions are outstanding in this frontend. Complete the
             * reset synchronously, invalidating only this engine's source. */
            for (unsigned j = 0; j < G_N_ELEMENTS(bank->config[word->port]); j++) {
                if (auto9_qnx_dpp_config[j].idma == word->kind) {
                    bank->config[word->port][j].value = 0;
                    bank->config[word->port][j].writes = 0;
                }
            }
            Auto9DppIrqWord *irq = &bank->words[2 * word->port + word->kind];
            irq->config = irq->pending = 0;
            word->resets++;
            word->writes++;
            return MEMTX_OK;
        }
    }
    /* Configuration retention alone does not execute a stream or signal it. */
    word->value = value;
    word->writes++;
    return MEMTX_OK;
reject:
    word->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 QNX DPP unsupported control write kind=%u port=%u offset=0x%x value=0x%" PRIx64 "\n",
                  word->kind, word->port, word->offset, value);
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps auto9_qnx_dpp_config_ops = {
    .read = auto9_qnx_dpp_config_read,
    .write_with_attrs = auto9_qnx_dpp_config_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static int auto9_linux_dpp_lookup(Auto9LinuxDppPage *p, hwaddr off)
{
    for (unsigned i = 0; i < p->count; i++) {
        if (p->offsets[i] == off) {
            return i;
        }
    }
    return -1;
}

static MemTxResult auto9_linux_dpp_read(void *opaque, hwaddr off, uint64_t *value,
                                       unsigned size, MemTxAttrs attrs)
{
    Auto9LinuxDppPage *p = opaque;
    if (p->irq && off == 4) {
        *value = auto9_dpp_irq_read(p->irq, 0, size);
        return MEMTX_OK;
    }
    int index = auto9_linux_dpp_lookup(p, off);
    if (index < 0) {
        p->rejected++;
        qemu_log_mask(LOG_UNIMP, "Auto9 Linux DPP unsupported read at 0x%" HWADDR_PRIx "\n",
                      p->region.addr + off);
        return MEMTX_DECODE_ERROR;
    }
    *value = p->value[index];
    return MEMTX_OK;
}

static MemTxResult auto9_linux_dpp_write(void *opaque, hwaddr off, uint64_t value,
                                        unsigned size, MemTxAttrs attrs)
{
    Auto9LinuxDppPage *p = opaque;
    if (p->irq && off == 4) {
        auto9_dpp_irq_write(p->irq, 0, value, size);
        return MEMTX_OK;
    }
    int index = auto9_linux_dpp_lookup(p, off);
    if (index < 0) {
        goto reject;
    }
    if (p->irq && off == 0) {
        uint32_t clock_mask = p->idma ? 0x200 : 0x700;
        /* OP_STATUS bit 2 is read-only and remains idle without a DMA
         * requester. SW_RESET bit 24 clears this page and self-clears. */
        if (value & ~(clock_mask | BIT(24))) {
            goto reject;
        }
        if (value & BIT(24)) {
            memset(p->value, 0, sizeof(p->value));
            p->irq->config = p->irq->pending = 0;
            p->resets++;
            p->writes++;
            return MEMTX_OK;
        }
    } else if (!p->irq && off != 0x80) {
        uint32_t expected = off < 0x70 ? UINT32_MAX : 0x44444444;
        if (value != expected) {
            goto reject;
        }
    }
    p->value[index] = value;
    p->writes++;
    return MEMTX_OK;
reject:
    p->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 Linux DPP unsupported write at 0x%" HWADDR_PRIx "\n",
                  p->region.addr + off);
    return MEMTX_DECODE_ERROR;
}

static const MemoryRegionOps auto9_linux_dpp_ops = {
    .read_with_attrs = auto9_linux_dpp_read,
    .write_with_attrs = auto9_linux_dpp_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_dpp_irq_reset(DeviceState *dev)
{
    Auto9DppIrq *s = AUTO9_DPP_IRQ(dev);
    for (unsigned i = 0; i < AUTO9_DPP_WORDS; i++) {
        Auto9DppIrqWord *word = &s->words[i];
        word->config = word->pending = word->reads = word->writes = 0;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->config); i++) {
        for (unsigned j = 0; j < G_N_ELEMENTS(s->config[i]); j++) {
            Auto9QnxDppWord *w = &s->config[i][j];
            w->value = w->reads = w->writes = w->resets = w->rejected = 0;
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->qnx_common); i++) {
        for (unsigned j = 0; j < G_N_ELEMENTS(s->qnx_common[i]); j++) {
            Auto9QnxDppWord *w = &s->qnx_common[i][j];
            w->value = w->reads = w->writes = w->resets = w->rejected = 0;
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->linux_pages); i++) {
        Auto9LinuxDppPage *p = &s->linux_pages[i];
        memset(p->value, 0, sizeof(p->value));
        p->writes = p->rejected = p->resets = 0;
    }
}

static void auto9_dpp_irq_init(Object *obj)
{
    Auto9DppIrq *s = AUTO9_DPP_IRQ(obj);
    for (unsigned i = 0; i < AUTO9_DPP_WORDS; i++) {
        Auto9DppIrqWord *word = &s->words[i];
        bool idma = i & 1;
        g_autofree char *reads = g_strdup_printf("reads-%u", i);
        g_autofree char *writes = g_strdup_printf("writes-%u", i);
        g_autofree char *config = g_strdup_printf("config-%u", i);
        g_autofree char *pending = g_strdup_printf("pending-%u", i);
        word->enable_mask = idma ? 0x7f7 : 0x43;
        word->status_mask = idma ? 0x03fb0000 : 0x00210000;
        memory_region_init_io(&word->region, obj, &auto9_dpp_irq_ops, word,
                              idma ? "auto9-idma-irq" : "auto9-dpp-irq", 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &word->region);
        object_property_add_uint32_ptr(obj, reads, &word->reads, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, writes, &word->writes, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, config, &word->config, OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, pending, &word->pending, OBJ_PROP_FLAG_READ);
    }
}

static void auto9_dpp_irq_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_dpp_irq_reset);
}

static const TypeInfo auto9_dpp_irq_type = {
    .name = TYPE_AUTO9_DPP_IRQ, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9DppIrq), .instance_init = auto9_dpp_irq_init,
    .class_init = auto9_dpp_irq_class,
};

static void auto9_dpp_irq_register(void) { type_register_static(&auto9_dpp_irq_type); }
type_init(auto9_dpp_irq_register)

static void auto9_dpp_irq_create_bank(VirtMachineState *vms, const char *name,
                                      const hwaddr *words, unsigned count,
                                      bool qnx_config)
{
    DeviceState *dev = qdev_new(TYPE_AUTO9_DPP_IRQ);
    object_property_add_child(OBJECT(vms), name, OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < count; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), i, words[i]);
    }
    if (qnx_config) {
        Auto9DppIrq *s = AUTO9_DPP_IRQ(dev);
        assert(count == 2 * G_N_ELEMENTS(s->config));
        for (unsigned i = 0; i < G_N_ELEMENTS(s->config); i++) {
            s->qnx_idma_base[i] = words[2 * i + 1] - 4;
            for (unsigned j = 0; j < G_N_ELEMENTS(s->config[i]); j++) {
                Auto9QnxDppWord *word = &s->config[i][j];
                unsigned idma = auto9_qnx_dpp_config[j].idma;
                unsigned off = auto9_qnx_dpp_config[j].offset;
                word->bank = s;
                word->port = i;
                word->kind = idma;
                word->offset = off;
                hwaddr address = words[2 * i + idma] - 4 + off;
                g_autofree char *key = g_strdup_printf("%s-%u-%03x", idma ? "idma" : "dpp", i, off);
                g_autofree char *writes = g_strdup_printf("%s-writes", key);
                memory_region_init_io(&word->region, OBJECT(dev), &auto9_qnx_dpp_config_ops,
                                      word, "auto9-qnx-dpp-config", 4);
                memory_region_add_subregion(get_system_memory(), address, &word->region);
                object_property_add_uint32_ptr(OBJECT(dev), key, &word->value, OBJ_PROP_FLAG_READ);
                object_property_add_uint32_ptr(OBJECT(dev), writes, &word->writes, OBJ_PROP_FLAG_READ);
                if (!off) {
                    g_autofree char *reads = g_strdup_printf("%s-reads", key);
                    g_autofree char *resets = g_strdup_printf("%s-resets", key);
                    g_autofree char *rejected = g_strdup_printf("%s-rejected", key);
                    object_property_add_uint32_ptr(OBJECT(dev), reads, &word->reads, OBJ_PROP_FLAG_READ);
                    object_property_add_uint32_ptr(OBJECT(dev), resets, &word->resets, OBJ_PROP_FLAG_READ);
                    object_property_add_uint32_ptr(OBJECT(dev), rejected, &word->rejected, OBJ_PROP_FLAG_READ);
                }
            }
        }
        if (!vms->exynos_linux_dpp_irq) {
            static const hwaddr common[] = { 0x18c60000, 0x1aa60000 };
            /* When Linux is present, its existing physical common pages are
             * the single shared register bank for both guests. */
            for (unsigned i = 0; i < G_N_ELEMENTS(common); i++) {
                for (unsigned j = 0; j < 2; j++) {
                    Auto9QnxDppWord *word = &s->qnx_common[i][j];
                    word->bank = s;
                    word->kind = 2;
                    word->offset = 0x70 + 4 * j;
                    memory_region_init_io(&word->region, OBJECT(dev), &auto9_qnx_dpp_config_ops,
                                          word, "auto9-qnx-dpp-common", 4);
                    memory_region_add_subregion_overlap(get_system_memory(),
                        common[i] + word->offset, &word->region, 1);
                }
            }
        }
    }
}

static void auto9_linux_dpp_page(DeviceState *dev, unsigned i, hwaddr base,
                                 const uint16_t *offsets, unsigned count,
                                 bool port, bool idma)
{
    Auto9DppIrq *s = AUTO9_DPP_IRQ(dev);
    Auto9LinuxDppPage *p = &s->linux_pages[i];
    g_autofree char *prefix = g_strdup_printf("linux-page-%" HWADDR_PRIx, base);
    g_autofree char *writes = g_strdup_printf("%s-writes", prefix);
    g_autofree char *rejects = g_strdup_printf("%s-rejected", prefix);
    g_autofree char *resets = g_strdup_printf("%s-resets", prefix);
    p->irq = port ? &s->words[i] : NULL;
    p->idma = idma;
    p->offsets = offsets;
    p->count = count;
    assert(count <= G_N_ELEMENTS(p->value));
    memory_region_init_io(&p->region, OBJECT(dev), &auto9_linux_dpp_ops, p,
                          prefix, 0x1000);
    /* HCB vl,io-memory windows already occupy these PAs at priority 0.
     * Priority -1 lost the IDMA page at 0x1aa63000 (SEA, no DPP log). */
    memory_region_add_subregion_overlap(get_system_memory(), base, &p->region, 1);
    object_property_add_uint32_ptr(OBJECT(dev), writes, &p->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), rejects, &p->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), resets, &p->resets, OBJ_PROP_FLAG_READ);
    for (unsigned j = 0; j < count; j++) {
        g_autofree char *key = g_strdup_printf("%s-%03x", prefix, offsets[j]);
        object_property_add_uint32_ptr(OBJECT(dev), key, &p->value[j], OBJ_PROP_FLAG_READ);
    }
}

static void auto9_linux_dpp_create(VirtMachineState *vms)
{
    static const hwaddr pages[] = {
        0x18c22000, 0x18c61000, 0x1aa25000, 0x1aa63000, 0x1aa27000, 0x1aa67000,
    };
    DeviceState *dev = qdev_new(TYPE_AUTO9_DPP_IRQ);
    object_property_add_child(OBJECT(vms), "exynos-linux-dpp-irqs", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < G_N_ELEMENTS(pages); i++) {
        bool idma = i & 1;
        auto9_linux_dpp_page(dev, i, pages[i],
            idma ? auto9_linux_idma_offsets : auto9_linux_dpp_offsets,
            idma ? G_N_ELEMENTS(auto9_linux_idma_offsets) : G_N_ELEMENTS(auto9_linux_dpp_offsets),
            true, idma);
    }
    auto9_linux_dpp_page(dev, 6, 0x18c60000, auto9_linux_common_offsets,
                        G_N_ELEMENTS(auto9_linux_common_offsets), false, false);
    auto9_linux_dpp_page(dev, 7, 0x1aa60000, auto9_linux_common_offsets,
                        G_N_ELEMENTS(auto9_linux_common_offsets), false, false);
}

static void auto9_dpp_irq_create(VirtMachineState *vms)
{
    /* The DT node's @ name is NOT the IDMA base; use each reg tuple. */
    const hwaddr words[] = {
        0x1aa21004, 0x1aa65004, 0x1aa22004, 0x1aa61004,
        0x1aa23004, 0x1aa62004, 0x1aa24004, 0x1aa66004,
    };
    const hwaddr qnx_words[] = {
        0x18c27004, 0x18c67004, 0x18c25004, 0x18c63004,
        0x18c28004, 0x18c68004, 0x18c26004, 0x18c64004,
        0x1aa28004, 0x1aa68004, 0x1aa26004, 0x1aa64004,
    };
    if (vms->exynos_qnx_dpp_irq) {
        auto9_dpp_irq_create_bank(vms, "exynos-qnx-dpp-irqs", qnx_words,
                                 G_N_ELEMENTS(qnx_words), true);
    }
    if (vms->exynos_dpp_irq) {
        auto9_dpp_irq_create_bank(vms, "exynos-dpp-irqs", words, G_N_ELEMENTS(words), false);
    }
    if (vms->exynos_linux_dpp_irq) {
        auto9_linux_dpp_create(vms);
    }
}

