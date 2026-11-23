/* SPDX-License-Identifier: GPL-2.0-or-later
 * Native-address adapter for the unmodified upstream Samsung-authored UFS HCI.
 * The private PCI bus is an implementation detail, never a guest PCI device.
 * Descriptor/data DMA traverses the VGEN/S2MPU path. The default requires
 * uniform VID2..4; the opt-in physical-HCI profile uses only slot0/VID2.
 * Opt-in virtual hosts use separate HCI engines and slots 1/2, not PH state.
 * Bounded Samsung vendor configuration and real software/device reset below.
 * Shared physical PHY, FMP and stock multi-host arbitration are not modeled.
 */
#include "scsi/constants.h"
#include "hw/pci/pci_host.h"
#include "hw/ufs/ufs.h"

#define TYPE_EXYNOS_AUTO9_UFS "exynos-auto9.ufs"
#define TYPE_EXYNOS_AUTO9_UFS_IOMMU "exynos-auto9.ufs-iommu"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Ufs, EXYNOS_AUTO9_UFS)
typedef struct ExynosAuto9UfsIommu {
    IOMMUMemoryRegion parent_obj;
    ExynosAuto9Ufs *owner;
} ExynosAuto9UfsIommu;
typedef struct ExynosAuto9UfsMailbox {
    MemoryRegion regs;
    uint32_t rx, pending, ready_writes, ready_reads, clear_writes, tx5_writes;
    uint32_t ack, rejected, last_rejected;
    bool ready_seen, rx_read, cleared, complete;
} ExynosAuto9UfsMailbox;

typedef struct ExynosAuto9UfsNotifier {
    IOMMUNotifier notifier;
    ExynosAuto9Ufs *owner;
} ExynosAuto9UfsNotifier;
struct ExynosAuto9Ufs {
    PCIHostState parent_obj; /* Required private root-bus owner; no guest config MMIO. */
    MemoryRegion hci_alias, vendor_mmio, pci_mem, pci_io;
    MemoryRegion shareability_mmio, unipro_cfg, pma_cfg;
    uint32_t unipro_period, unipro_writes, pma_words[0x1b8 / 4], pma_writes;
    uint32_t prelink_rejected, prelink_last_pma;
    bool power_config_enabled;
    uint32_t power_timer_mask, power_timer_writes, power_pma_writes;
    uint32_t power_timer[6];
    uint32_t shareability, shareability_reads, shareability_writes;
    ExynosAuto9UfsIommu filtered_dma;
    ExynosAuto9UfsNotifier invalidation[8];
    AddressSpace dma_as;
    PCIBus *bus;
    PCIDevice *engine;
    ExynosAuto9Vgen *vgen;
    ExynosAuto9S2mpu *s2mpu;
    qemu_irq irq;
    uint32_t irq_level, irq_assertions, dma_reads, dma_writes, dma_rejected;
    uint32_t host0_route, host0_last_vid, host0_invalid, host0_s2_denied;
    bool mailbox;
    uint32_t host_index;
    ExynosAuto9UfsMailbox *endpoint;
    ExynosAuto9Ufs *physical;
    uint32_t vh_config;
    ExynosAuto9UfsMailbox vh[2];
    uint32_t mh_config, ph_tx, ph_tx_count, ph_rejected;
    unsigned registered_notifiers;
    UfsReg reset_reg;
    QEMUBH *reset_bh;
    uint32_t vendor[0x310 / 4], vendor_rejected, software_resets;
    uint32_t device_assertions, device_releases;
    uint64_t reset_assert_ns, reset_pulse_ns;
};
static void exynos_auto9_ufs_reset_engine(ExynosAuto9Ufs *s);
static void exynos_auto9_ufs_mailbox_reset(ExynosAuto9Ufs *s, bool machine_reset);

/* Original devb-ufs +0x15da8..0x15dc8 and Auto V9's upstream syscon:
 * UFS0 FSYS2+0x710, bits 1 (read) and 2 (write) shareable. This is
 * configuration retention only; no effect on DMA policy, HCE, PHY or IRQ.
 */
#define AUTO9_UFS0_SHAREABILITY 0x17c20710
#define AUTO9_UFS0_SHAREABILITY_MASK (BIT(1) | BIT(2))

static uint64_t exynos_auto9_ufs_shareability_read(void *opaque, hwaddr offset,
                                                  unsigned size)
{
    ExynosAuto9Ufs *s = opaque;
    s->shareability_reads++;
    return s->shareability;
}

static void exynos_auto9_ufs_shareability_write(void *opaque, hwaddr offset,
                                                uint64_t value, unsigned size)
{
    ExynosAuto9Ufs *s = opaque;
    s->shareability_writes++;
    s->shareability = value & AUTO9_UFS0_SHAREABILITY_MASK;
}

static const MemoryRegionOps exynos_auto9_ufs_shareability_ops = {
    .read = exynos_auto9_ufs_shareability_read,
    .write = exynos_auto9_ufs_shareability_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/* Original devb-ufs sub_15B80: vendor VA = standard HCI VA + 0x1100.
 * sub_16330/sub_169E0 use these finite configuration words. Samsung's
 * upstream ufs-exynos.c independently declares reset50, GPIO70 and clocks.
 * Retaining clock/PRDT/MH configuration is NOT a full clock/VF data path.
 */
static bool exynos_auto9_ufs_vendor_config(hwaddr offset)
{
    switch (offset) {
    case 0x00: case 0x04: case 0x0c: case 0x40: case 0x44:
    case 0x60: case 0x68: case 0x6c: case 0x7c: case 0x80:
    case 0x84: case 0x88: case 0x8c: case 0xb0: case 0xb4:
    case 0xfc: case 0x100: case 0x308: case 0x30c:
        return true;
    default: return false;
    }
}
static void exynos_auto9_ufs_software_reset(void *opaque)
{
    ExynosAuto9Ufs *s = opaque;
    /* Completion follows cancellation of actual backend requests and BHs,
     * not a read-count, successful-return stub or fixed ready-register value.
     */
    exynos_auto9_ufs_reset_engine(s);
    exynos_auto9_ufs_mailbox_reset(s, false);
    s->vendor[0x50 / 4] = 0;
    s->software_resets++;
}
static MemTxResult exynos_auto9_ufs_vendor_read(void *opaque, hwaddr offset,
                                   uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    if (size != 4 || offset % 4) { return MEMTX_ACCESS_ERROR; }
    /* QNX reads the timeout divider as part of the vendor register dump.
     * This model has no vendor timeout-divider clock; expose the untouched
     * configuration word without generating a timeout, interrupt or ready bit. */
    if (!s->host_index && offset == 0x08) {
        *value = s->vendor[offset / 4];
        return MEMTX_OK;
    }
    if (offset == 0x38) { *value = 0; return MEMTX_OK; } /* No modeled vendor IRQ source. */
    if (offset == 0x50 || offset == 0x70 || exynos_auto9_ufs_vendor_config(offset)) {
        *value = s->vendor[offset / 4]; return MEMTX_OK;
    }
    s->vendor_rejected++; return MEMTX_DECODE_ERROR;
}
static MemTxResult exynos_auto9_ufs_vendor_write(void *opaque, hwaddr offset,
                                  uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    if (size != 4 || offset % 4) { return MEMTX_ACCESS_ERROR; }
    if (offset == 0x50) {
        /* Native driver and primary source request both LINK/UNIPRO resets.
         * Separate component reset semantics are not implemented as aliases.
         */
        if (value != 3 || s->vendor[0x50 / 4]) { goto unsupported; }
        s->vendor[0x50 / 4] = value;
        qemu_bh_schedule(s->reset_bh); return MEMTX_OK;
    }
    if (offset == 0x70) {
        if (value > 1 || s->vendor[0x50 / 4]) { goto unsupported; }
        if (!value && s->vendor[0x70 / 4]) {
            s->reset_assert_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
            s->device_assertions++;
            exynos_auto9_ufs_reset_engine(s);
            /* Do not roll back permanent WP/FW-update flags or persistent
             * device configuration to a construction-time snapshot.
             */
            UFS(s->engine)->flags.device_init = 0;
            exynos_auto9_ufs_mailbox_reset(s, false);
        } else if (value && !s->vendor[0x70 / 4]) {
            s->reset_pulse_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->reset_assert_ns;
            s->device_releases++;
        }
        s->vendor[0x70 / 4] = value; return MEMTX_OK;
    }
    if (offset == 0x38 && !(value & ~0x100000ULL)) { return MEMTX_OK; }
    if (exynos_auto9_ufs_vendor_config(offset)) {
        s->vendor[offset / 4] = value; return MEMTX_OK;
    }
unsupported:
    s->vendor_rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 UFS vendor unsupported offset=0x%"HWADDR_PRIx" value=0x%"PRIx64"\n", offset, value);
    return MEMTX_DECODE_ERROR;
}
static const MemoryRegionOps exynos_auto9_ufs_vendor_ops = {
    .read_with_attrs = exynos_auto9_ufs_vendor_read,
    .write_with_attrs = exynos_auto9_ufs_vendor_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};
/* Pinned original devb-ufs table at 0x18508. The UniPro word retains the
 * 16e9 / 166MHz period; the lane-0 PMA cells retain only table writes.
 * No status, PLL, M-PHY training, physical clock or link is produced here. */
static bool exynos_auto9_ufs_pma_supported(hwaddr offset, uint64_t value, bool write)
{
    switch (offset) {
    case 0x74: return !write || value == 0x10;
    case 0x8c: return !write || value == 0x80 || value == 0xc0 || value == 0;
    case 0x110: return !write || value == 0xb5;
    case 0x134: return !write || value == 0x43;
    case 0x16c: return !write || value == 0x20;
    case 0x178: return !write || value == 0xc0;
    case 0xe0: return !write || value == 0x12;
    case 0x164: return !write || value == 0x58;
    case 0x1b0: return !write || value == 0x18;
    case 0x1b4: return !write || value == 2;
    case 0x108: return !write || value == 0x5d;
    case 0x10c: return !write || value == 0;
    default: return false;
    }
}

static MemTxResult exynos_auto9_ufs_prelink_reject(ExynosAuto9Ufs *s,
                                                  const char *bank, hwaddr off)
{
    s->prelink_rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 UFS prelink unsupported %s 0x%" HWADDR_PRIx "\n",
                  bank, off);
    return MEMTX_DECODE_ERROR;
}

/* Exact UniPro power timer offsets/values in the three original QNX
 * devb-ufs op-0xa tables. No physical timer, PHY transition or IRQ here. */
static const struct { hwaddr offset; uint32_t value; } ufs_power_timer[] = {
    {0x7888, 0x1f80}, {0x788c, 0x6e40}, {0x7890, 0x4ec0},
    {0x78b8, 0x2ee0}, {0x78bc, 0x7d00}, {0x78c0, 0x3e80},
};

static int exynos_auto9_ufs_timer_index(ExynosAuto9Ufs *s, hwaddr offset)
{
    if (s->power_config_enabled) {
        for (int i = 0; i < ARRAY_SIZE(ufs_power_timer); i++) {
            if (ufs_power_timer[i].offset == offset) {
                return i;
            }
        }
    }
    return -1;
}

static bool exynos_auto9_ufs_power_pma(ExynosAuto9Ufs *s, hwaddr off,
                                      uint64_t value, bool write)
{
    if (!s->power_config_enabled || s->power_timer_mask != 0x3f) {
        return false;
    }
    /* Original mode-specific op-4 rows, only after all timer words. */
    switch (off) {
    case 0xc8: return !write || value == 0x40 || value == 0xbc;
    case 0xf0: return !write || value == 0x77 || value == 0x7f;
    case 0x120: return !write || value == 0x80 || value == 0xc0;
    case 0x128: return !write || value == 0 || value == 8 || value == 2;
    case 0x12c: return !write || value == 0 || value == 0x10;
    case 0x134:
        return !write || value == 0x43 || value == 0xd3 || value == 0x73 || value == 0x63;
    default: return false;
    }
}

static MemTxResult exynos_auto9_ufs_unipro_read(void *opaque, hwaddr off,
    uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    int i = exynos_auto9_ufs_timer_index(s, off);
    if (i >= 0) {
        *value = s->power_timer[i];
        return MEMTX_OK;
    }
    if (off != 0x44) {
        return exynos_auto9_ufs_prelink_reject(s, "UniPro read", off);
    }
    *value = s->unipro_period;
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_ufs_unipro_write(void *opaque, hwaddr off,
    uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    int i = exynos_auto9_ufs_timer_index(s, off);
    if (i >= 0 && value == ufs_power_timer[i].value) {
        s->power_timer[i] = value;
        s->power_timer_mask |= 1u << i;
        s->power_timer_writes++;
        return MEMTX_OK;
    }
    if (off != 0x44 || value != 16000000000ULL / 166000000ULL) {
        return exynos_auto9_ufs_prelink_reject(s, "UniPro write", off);
    }
    s->unipro_period = value;
    s->unipro_writes++;
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_ufs_pma_read(void *opaque, hwaddr off,
    uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    if (off >= sizeof(s->pma_words) || (s->vendor[0xb0 / 4] & 8) ||
        !(exynos_auto9_ufs_pma_supported(off, 0, false) ||
          exynos_auto9_ufs_power_pma(s, off, 0, false))) {
        return exynos_auto9_ufs_prelink_reject(s, "PMA read", off);
    }
    *value = s->pma_words[off / 4];
    return MEMTX_OK;
}

static MemTxResult exynos_auto9_ufs_pma_write(void *opaque, hwaddr off,
    uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    bool power = exynos_auto9_ufs_power_pma(s, off, value, true);
    if (off >= sizeof(s->pma_words) || (s->vendor[0xb0 / 4] & 8) ||
        !(power || exynos_auto9_ufs_pma_supported(off, value, true))) {
        return exynos_auto9_ufs_prelink_reject(s, "PMA write", off);
    }
    if (power) {
        s->power_pma_writes++;
    }
    s->pma_words[off / 4] = value;
    s->pma_writes++;
    s->prelink_last_pma = off;
    return MEMTX_OK;
}

static const MemoryRegionOps exynos_auto9_ufs_unipro_ops = {
    .read_with_attrs = exynos_auto9_ufs_unipro_read,
    .write_with_attrs = exynos_auto9_ufs_unipro_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};
static const MemoryRegionOps exynos_auto9_ufs_pma_ops = {
    .read_with_attrs = exynos_auto9_ufs_pma_read,
    .write_with_attrs = exynos_auto9_ufs_pma_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

/*
 * Physical host +d8 carries the destination mask in bits [10:8].
 * Model polling and one latch per VH, so early PH writes are not lost.
 * No IRQ, disk data or upstream host0 register state is shared with VH.
 */
#define AUTO9_UFS_MB_STS 0xd0
#define AUTO9_UFS_MB_CONFIG 0xd4
#define AUTO9_UFS_MB_PH_TX 0xd8
#define AUTO9_UFS_MB_VH_TX 0xdc
#define AUTO9_UFS_MB_PENDING BIT(16)
#define AUTO9_UFS_MB_VHS 6U

static MemTxResult exynos_auto9_ufs_vh_reject(ExynosAuto9UfsMailbox *m, hwaddr offset)
{
    m->rejected++;
    m->last_rejected = offset;
    return MEMTX_DECODE_ERROR;
}

static MemTxResult exynos_auto9_ufs_vh_read(void *opaque, hwaddr offset,
                                            uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UfsMailbox *m = opaque;
    switch (offset) {
    case AUTO9_UFS_MB_STS:
        *value = m->pending;
        return MEMTX_OK;
    case AUTO9_UFS_MB_PH_TX:
        *value = m->rx;
        if (m->ready_seen && (m->rx & 0xff) == 1) {
            m->ready_reads++;
            m->rx_read = true;
        }
        return MEMTX_OK;
    default:
        return exynos_auto9_ufs_vh_reject(m, offset);
    }
}

static MemTxResult exynos_auto9_ufs_vh_write(void *opaque, hwaddr offset,
                                             uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9UfsMailbox *m = opaque;
    if (offset == AUTO9_UFS_MB_STS && !(value & ~AUTO9_UFS_MB_PENDING)) {
        if (m->rx_read && (m->pending & value)) {
            m->cleared = true;
        }
        m->pending &= ~value;
        m->clear_writes++;
        return MEMTX_OK;
    }
    if (offset == AUTO9_UFS_MB_VH_TX && value == 2) {
        m->ready_seen = true;
        m->rx_read = m->cleared = false;
        m->ready_writes++;
        return MEMTX_OK;
    }
    if (offset == AUTO9_UFS_MB_VH_TX && value == 5) {
        m->tx5_writes++;
        /* Original VH writes 5 on timeout as well. Count only full exchanges. */
        if (m->ready_seen && m->rx_read && m->cleared) {
            m->ack++;
            m->complete = true;
            m->rx = 0;
        }
        m->ready_seen = m->rx_read = m->cleared = false;
        return MEMTX_OK;
    }
    return exynos_auto9_ufs_vh_reject(m, offset);
}

static const MemoryRegionOps exynos_auto9_ufs_vh_ops = {
    .read_with_attrs = exynos_auto9_ufs_vh_read,
    .write_with_attrs = exynos_auto9_ufs_vh_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult exynos_auto9_ufs_ph_send(ExynosAuto9Ufs *s, uint32_t value)
{
    uint32_t dest = value >> 8;

    if ((value & 0xff) != 1 || !dest || (dest & ~AUTO9_UFS_MB_VHS)) {
        s->ph_rejected++;
        return MEMTX_DECODE_ERROR;
    }
    s->ph_tx = value;
    s->ph_tx_count++;
    for (unsigned i = 0; i < G_N_ELEMENTS(s->vh); i++) {
        ExynosAuto9UfsMailbox *m = &s->vh[i];
        if (dest & BIT(i + 1)) {
            m->rx = value & 0xff;
            m->pending |= AUTO9_UFS_MB_PENDING;
            m->rx_read = m->cleared = false;
        }
    }
    return MEMTX_OK;
}

static void exynos_auto9_ufs_mailbox_reset(ExynosAuto9Ufs *s, bool machine_reset)
{
    s->mh_config = s->ph_tx = 0;
    if (machine_reset) {
        s->ph_tx_count = s->ph_rejected = 0;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(s->vh); i++) {
        ExynosAuto9UfsMailbox *m = &s->vh[i];
        m->rx = m->pending = 0;
        m->rx_read = m->cleared = m->complete = false;
        if (machine_reset) {
            m->ready_seen = false; /* A PH link reset does not restart the VH kernel. */
            m->ready_writes = m->ready_reads = m->clear_writes = m->tx5_writes = 0;
            m->ack = m->rejected = m->last_rejected = 0;
        }
    }
}

static void exynos_auto9_ufs_mailbox_properties(Object *obj, ExynosAuto9Ufs *s)
{
    object_property_add_uint32_ptr(obj, "mailbox-ph-config", &s->mh_config, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mailbox-ph-tx", &s->ph_tx, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mailbox-ph-writes", &s->ph_tx_count, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mailbox-ph-rejected", &s->ph_rejected, OBJ_PROP_FLAG_READ);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->vh); i++) {
        ExynosAuto9UfsMailbox *m = &s->vh[i];
        const char *const names[] = {
            "rx", "pending", "ready-writes", "ready-reads", "clear-writes",
            "tx5-writes", "ack", "rejected", "last-rejected",
        };
        uint32_t *values[] = {
            &m->rx, &m->pending, &m->ready_writes, &m->ready_reads, &m->clear_writes,
            &m->tx5_writes, &m->ack, &m->rejected, &m->last_rejected,
        };
        for (unsigned n = 0; n < G_N_ELEMENTS(names); n++) {
            g_autofree char *name = g_strdup_printf("mailbox-vh%u-%s", i + 1, names[n]);
            object_property_add_uint32_ptr(obj, name, values[n], OBJ_PROP_FLAG_READ);
        }
    }
}

static bool exynos_auto9_ufs_is_vh_mailbox(hwaddr offset)
{
    return offset == AUTO9_UFS_MB_STS || offset == AUTO9_UFS_MB_PH_TX ||
           offset == AUTO9_UFS_MB_VH_TX;
}

/* OEM VH ufshcd_send_uic_cmd returns without a UIC transaction. Expose
 * only the link bits once the real PH has linked and this VH consumed its
 * COMPLETE. Other HCI state (including UTRD, IRQ and HCE) is local. */
static uint32_t exynos_auto9_ufs_vh_link(ExynosAuto9Ufs *s)
{
    uint32_t required = R_HCS_DP_MASK | R_HCS_UTRLRDY_MASK |
                        R_HCS_UTMRLRDY_MASK | R_HCS_UCRDY_MASK;
    if (!s->endpoint->complete || !UFS(s->engine)->lus[0]) {
        return 0;
    }
    uint32_t state = UFS(s->physical->engine)->reg.hcs & required;
    return state == required ? state : 0;
}

static MemTxResult exynos_auto9_ufs_hci_read(void *opaque, hwaddr offset,
                                 uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    if (s->endpoint) {
        if (exynos_auto9_ufs_is_vh_mailbox(offset)) {
            return exynos_auto9_ufs_vh_read(s->endpoint, offset, value, size, attrs);
        }
        if (offset == AUTO9_UFS_MB_CONFIG) {
            *value = s->vh_config;
            return MEMTX_OK;
        }
        if (offset == A_VER) {
            /* Legacy HCI 2.1 personality for the original driver; not an
             * assertion about the physical controller's revision. */
            *value = 0x210;
            return MEMTX_OK;
        }
        if (offset == A_HCS) {
            *value = exynos_auto9_ufs_vh_link(s);
            return MEMTX_OK;
        }
    }
    if (s->mailbox && offset == AUTO9_UFS_MB_CONFIG) {
        *value = s->mh_config;
        return MEMTX_OK;
    }
    if (s->mailbox && offset == AUTO9_UFS_MB_PH_TX) {
        *value = s->ph_tx;
        return MEMTX_OK;
    }
    return memory_region_dispatch_read(&UFS(s->engine)->iomem, offset, value,
                                       size_memop(size), attrs);
}
static MemTxResult exynos_auto9_ufs_hci_write(void *opaque, hwaddr offset,
                                uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Ufs *s = opaque;
    if (s->vendor[0x50 / 4] || !s->vendor[0x70 / 4]) { return MEMTX_ERROR; }
    if (s->endpoint) {
        if (exynos_auto9_ufs_is_vh_mailbox(offset)) {
            return exynos_auto9_ufs_vh_write(s->endpoint, offset, value, size, attrs);
        }
        if (offset == AUTO9_UFS_MB_CONFIG) {
            /* VM3's original exynos_ufsiov_enable_intr also sets bit 9. */
            uint32_t mask = BIT(16) | (s->host_index == 1 ? BIT(9) : 0);
            if (value & ~mask) {
                return exynos_auto9_ufs_vh_reject(s->endpoint, offset);
            }
            s->vh_config = value;
            return MEMTX_OK;
        }
        if (offset == A_UICCMD ||
            (offset == A_UTRLDBR && value &&
             (!exynos_auto9_ufs_vh_link(s) || !UFS(s->engine)->reg.utrlrsr))) {
            return exynos_auto9_ufs_vh_reject(s->endpoint, offset);
        }
    }
    if (s->mailbox && offset == AUTO9_UFS_MB_CONFIG) {
        s->mh_config = value;
        return MEMTX_OK;
    }
    if (s->mailbox && offset == AUTO9_UFS_MB_PH_TX) {
        return exynos_auto9_ufs_ph_send(s, value);
    }
    return memory_region_dispatch_write(&UFS(s->engine)->iomem, offset, value,
                                        size_memop(size), attrs);
}
static const MemoryRegionOps exynos_auto9_ufs_hci_ops = {
    .read_with_attrs = exynos_auto9_ufs_hci_read,
    .write_with_attrs = exynos_auto9_ufs_hci_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static bool exynos_auto9_ufs_attrs(ExynosAuto9Ufs *s, MemTxAttrs *attrs)
{
    uint32_t vid = s->vgen->vid[s->host_index];
    if (s->host0_route) {
        /* A reprogrammed slot cannot borrow another VM's S2MPU context. */
        s->host0_last_vid = vid;
        if (vid != s->host_index + 2) {
            s->host0_invalid++;
            s->dma_rejected++;
            return false;
        }
        attrs->unspecified = 0;
        attrs->pid = vid;
        return true;
    }
    if (vid < 2 || vid > 4) { s->dma_rejected++; return false; }
    for (unsigned i = 1; i < 8; i++) {
        if (s->vgen->vid[i] != vid) {
            s->dma_rejected++;
            qemu_log_mask(LOG_GUEST_ERROR, "Auto9 UFS mixed VGEN contexts unsupported\n");
            return false;
        }
    }
    attrs->unspecified = 0;
    attrs->pid = vid;
    return true;
}
static IOMMUTLBEntry exynos_auto9_ufs_translate(IOMMUMemoryRegion *iommu,
                                                hwaddr address,
                                                IOMMUAccessFlags access, int index)
{
    ExynosAuto9Ufs *s = ((ExynosAuto9UfsIommu *)iommu)->owner;
    MemTxAttrs attrs = MEMTXATTRS_UNSPECIFIED;
    IOMMUTLBEntry entry = { .target_as = &address_space_memory,
        .iova = address & ~0xfffULL, .translated_addr = address & ~0xfffULL,
        .addr_mask = 0xfff, .perm = IOMMU_NONE };
    if (!exynos_auto9_ufs_attrs(s, &attrs)) { return entry; }
    /* Compose the real existing S2MPU permission walker with the actual
     * selected VGEN context. Direct RAM mappings preserve SG DMA semantics;
     * MMIO forwarding would bounce failed reads into zero-filled disk writes.
     */
    entry = exynos_auto9_s2mpu_translate(IOMMU_MEMORY_REGION(&s->s2mpu->iommu),
                                       address, access, attrs.pid);
    if (access & IOMMU_RO) { s->dma_reads++; }
    if (access & IOMMU_WO) { s->dma_writes++; }
    if ((entry.perm & access) != access) {
        s->dma_rejected++;
        if (s->host0_route) {
            s->host0_s2_denied++;
        }
    }
    return entry;
}
static void exynos_auto9_ufs_iommu_class_init(ObjectClass *klass, const void *data)
{
    IOMMU_MEMORY_REGION_CLASS(klass)->translate = exynos_auto9_ufs_translate;
}
static void exynos_auto9_ufs_invalidate(IOMMUNotifier *notifier, IOMMUTLBEntry *entry)
{
    ExynosAuto9Ufs *s = container_of(notifier, ExynosAuto9UfsNotifier, notifier)->owner;
    /* A real guest S2MPU invalidation retries an SG mapping waiting on access.
     * No transfer is completed or data copied by this notification itself.
     */
    if (s->engine) { address_space_notify_map_clients(&s->engine->bus_master_as); }
}
static AddressSpace *exynos_auto9_ufs_dma_space(PCIBus *bus, void *opaque, int devfn)
{
    return &EXYNOS_AUTO9_UFS(opaque)->dma_as;
}
static const PCIIOMMUOps exynos_auto9_ufs_iommu_ops = {
    .get_address_space = exynos_auto9_ufs_dma_space,
};
static int exynos_auto9_ufs_map_irq(PCIDevice *dev, int pin) { return 0; }
static void exynos_auto9_ufs_irq(void *opaque, int line, int level)
{
    ExynosAuto9Ufs *s = opaque;
    if (level && !s->irq_level) {
        s->irq_assertions++;
    }
    s->irq_level = level;
    qemu_set_irq(s->irq, level);
}
static bool exynos_auto9_ufs_transport_get(Object *obj, Error **errp)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(obj);
    return s->engine && (pci_get_word(s->engine->config + PCI_COMMAND) & PCI_COMMAND_MASTER);
}
static void exynos_auto9_ufs_transport_set(Object *obj, bool value, Error **errp)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(obj);
    if (!s->engine) { error_setg(errp, "UFS internal transport not realized"); return; }
    /* Only enable the hidden upstream transport after real machine reset.
     * This does not enable HCI/HCE or invent UIC/device/transfer completions.
     */
    pci_default_write_config(s->engine, PCI_COMMAND,
                            value ? PCI_COMMAND_MEMORY | PCI_COMMAND_MASTER : 0, 2);
}
static bool exynos_auto9_ufs_plain128_get(Object *obj, Error **errp)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(obj);
    return s->engine && UFS(s->engine)->params.samsung_plain_prdt128;
}

static void exynos_auto9_ufs_plain128_set(Object *obj, bool value, Error **errp)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(obj);
    UfsHc *u = s->engine ? UFS(s->engine) : NULL;

    if (!u || !u->params.samsung_byte_granularity || u->params.mcq ||
        u->reg.utrldbr || u->reg.utmrldbr) {
        error_setg(errp, "UFS PRDT128 requires idle legacy byte-unit engine");
        return;
    }
    u->params.samsung_plain_prdt128 = value;
}

static void exynos_auto9_ufs_realize(DeviceState *dev, Error **errp)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(dev);
    VirtMachineState *vms = VIRT_MACHINE(qdev_get_machine());
    UfsHc *core;
    hwaddr base = 0x17e00000 + s->host_index * 0x10000;
    g_autofree char *bus_name = g_strdup_printf("auto9-ufs%u-private-pci", s->host_index);
    g_autofree char *serial = g_strdup_printf("VIRTUAL-AUTO9-UFS%u", s->host_index);
    if (!vms->exynos_vlm || !s->vgen || !s->s2mpu || s->vgen->layout != 2) {
        error_setg(errp, "UFS requires native board and explicit UFS VGEN/S2MPU links");
        return;
    }
    memory_region_init(&s->pci_mem, OBJECT(s), "auto9-ufs-private-pci-memory", UINT64_MAX);
    memory_region_init(&s->pci_io, OBJECT(s), "auto9-ufs-private-pci-io", 0x10000);
    s->bus = pci_register_root_bus(dev, bus_name,
        exynos_auto9_ufs_irq, exynos_auto9_ufs_map_irq, s,
        &s->pci_mem, &s->pci_io, 0, 1, TYPE_PCIE_BUS);
    s->parent_obj.bus = s->bus;
    pci_setup_iommu(s->bus, &exynos_auto9_ufs_iommu_ops, s);
    s->engine = pci_new(0, TYPE_UFS);
    object_property_add_child(OBJECT(s), "controller", OBJECT(s->engine));
    DEVICE(s->engine)->id = s->host_index ?
        g_strdup_printf("auto9-ufs-vh%u-luns", s->host_index) : g_strdup("auto9-ufs0-luns");
    qdev_prop_set_string(DEVICE(s->engine), "serial", serial);
    qdev_prop_set_uint8(DEVICE(s->engine), "nutrs", 32);
    qdev_prop_set_bit(DEVICE(s->engine), "mcq", false);
    /* Never bounce an unmapped/denied SG page into an invented zero buffer. */
    object_property_set_uint(OBJECT(s->engine), "x-max-bounce-buffer-size", 0, &error_fatal);
    if (!pci_realize_and_unref(s->engine, s->bus, errp)) { return; }
    for (unsigned i = 0; i < 8; i++) {
        s->invalidation[i].owner = s;
        iommu_notifier_init(&s->invalidation[i].notifier, exynos_auto9_ufs_invalidate,
                            IOMMU_NOTIFIER_UNMAP, 0, (1ULL << 36) - 1, i);
        if (memory_region_register_iommu_notifier(MEMORY_REGION(&s->s2mpu->iommu),
                         &s->invalidation[i].notifier, errp)) { return; }
        s->registered_notifiers++;
    }
    core = UFS(s->engine);
    s->reset_reg = core->reg;
    /* Standard legacy HCI only. Do not expose upstream MCQ/unused space as
     * Samsung vendor-register success. Unknown native vendor windows fault.
     */
    memory_region_init_io(&s->hci_alias, OBJECT(s), &exynos_auto9_ufs_hci_ops,
                           s, s->host_index ? "auto9-ufs-vh-hci" : "auto9-ufs0-standard-hci", 0x100);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->hci_alias);
    sysbus_mmio_map(SYS_BUS_DEVICE(s), 0, base);
    memory_region_init_io(&s->vendor_mmio, OBJECT(s), &exynos_auto9_ufs_vendor_ops,
                           s, s->host_index ? "auto9-ufs-vh-vendor" : "auto9-ufs0-vendor", 0x310);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->vendor_mmio);
    sysbus_mmio_map(SYS_BUS_DEVICE(s), 1, base + 0x1100);
    if (!s->host_index && vms->exynos_ufs_shareability) {
        memory_region_init_io(&s->shareability_mmio, OBJECT(s),
                              &exynos_auto9_ufs_shareability_ops, s,
                              "auto9-ufs0-shareability", 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->shareability_mmio);
        sysbus_mmio_map(SYS_BUS_DEVICE(s), 2, AUTO9_UFS0_SHAREABILITY);
    }
    if (!s->host_index && vms->exynos_ufs_prelink) {
        memory_region_init_io(&s->unipro_cfg, OBJECT(s), &exynos_auto9_ufs_unipro_ops,
                              s, "auto9-ufs0-unipro-config", 0x8000);
        memory_region_init_io(&s->pma_cfg, OBJECT(s), &exynos_auto9_ufs_pma_ops,
                              s, "auto9-ufs0-pma-config", 0xc00);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->unipro_cfg);
        sysbus_mmio_map(SYS_BUS_DEVICE(s), vms->exynos_ufs_shareability ? 3 : 2,
                        0x17e80000);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->pma_cfg);
        sysbus_mmio_map(SYS_BUS_DEVICE(s), vms->exynos_ufs_shareability ? 4 : 3,
                        0x17e04000);
    }
    if (s->mailbox) {
        for (unsigned i = 0; !vms->exynos_ufs_vh && i < G_N_ELEMENTS(s->vh); i++) {
            ExynosAuto9UfsMailbox *m = &s->vh[i];
            memory_region_init_io(&m->regs, OBJECT(s), &exynos_auto9_ufs_vh_ops,
                                  m, "auto9-ufs-vh-mailbox", 0x100);
            memory_region_add_subregion(get_system_memory(), 0x17e10000 + i * 0x10000,
                                        &m->regs);
        }
        exynos_auto9_ufs_mailbox_properties(OBJECT(s), s);
    }
    /* Original HCB SPIs 227/228/229 (GIC IDs 259/260/261). */
    sysbus_connect_irq(SYS_BUS_DEVICE(s), 0,
                      qdev_get_gpio_in(vms->gic, 227 + s->host_index));
}
static bool exynos_auto9_ufs_power_get(Object *obj, Error **errp)
{
    return EXYNOS_AUTO9_UFS(obj)->power_config_enabled;
}

static void exynos_auto9_ufs_power_set(Object *obj, bool enabled, Error **errp)
{
    EXYNOS_AUTO9_UFS(obj)->power_config_enabled = enabled;
}

static void exynos_auto9_ufs_init(Object *obj)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(obj);
    memory_region_init_iommu(&s->filtered_dma, sizeof(s->filtered_dma),
                             TYPE_EXYNOS_AUTO9_UFS_IOMMU, obj,
                             "auto9-ufs-vgen-filtered", 1ULL << 36);
    s->filtered_dma.owner = s;
    s->reset_bh = qemu_bh_new(exynos_auto9_ufs_software_reset, s);
    address_space_init(&s->dma_as, MEMORY_REGION(&s->filtered_dma), "auto9-ufs-dma");
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    object_property_add_bool(obj, "transport-enabled", exynos_auto9_ufs_transport_get,
                             exynos_auto9_ufs_transport_set);
    object_property_add_bool(obj, "virtual-plain-prdt128-v1",
                             exynos_auto9_ufs_plain128_get, exynos_auto9_ufs_plain128_set);
    object_property_add_bool(obj, "virtual-power-config-v1",
                             exynos_auto9_ufs_power_get, exynos_auto9_ufs_power_set);
    object_property_add_uint32_ptr(obj, "power-timer-mask", &s->power_timer_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "power-timer-writes", &s->power_timer_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "power-pma-writes", &s->power_pma_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "unipro-period", &s->unipro_period, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "unipro-writes", &s->unipro_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pma-writes", &s->pma_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "prelink-rejected", &s->prelink_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "prelink-last-pma", &s->prelink_last_pma, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-level", &s->irq_level, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-assertions", &s->irq_assertions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "host-index", &s->host_index, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "shareability", &s->shareability, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "shareability-reads", &s->shareability_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "shareability-writes", &s->shareability_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dma-reads", &s->dma_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dma-writes", &s->dma_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "dma-rejected", &s->dma_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "host0-last-vid", &s->host0_last_vid, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "host0-invalid", &s->host0_invalid, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "host0-s2-denied", &s->host0_s2_denied, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vendor-rejected", &s->vendor_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "software-resets", &s->software_resets, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "device-reset-assertions", &s->device_assertions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "device-reset-releases", &s->device_releases, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "reset-pulse-ns", &s->reset_pulse_ns, OBJ_PROP_FLAG_READ);
}
static const Property exynos_auto9_ufs_properties[] = {
    DEFINE_PROP_LINK("vgen", ExynosAuto9Ufs, vgen, TYPE_EXYNOS_AUTO9_VGEN, ExynosAuto9Vgen *),
    DEFINE_PROP_LINK("s2mpu", ExynosAuto9Ufs, s2mpu, TYPE_EXYNOS_AUTO9_S2MPU, ExynosAuto9S2mpu *),
};
static void exynos_auto9_ufs_reset_engine(ExynosAuto9Ufs *s)
{
    UfsHc *core;
    if (!s->engine) { return; }
    core = UFS(s->engine);
    /* The upstream PCI core has no HCI reset callback. Cancel real backend
     * work before releasing SG lists; never finish an aborted guest request.
     * Keep actual LU contents/descriptors, restore only HCI power-on registers.
     */
    for (unsigned i = 0; i < UFS_MAX_LUS; i++) {
        if (core->lus[i] && core->lus[i]->scsi_dev) {
            scsi_device_purge_requests(core->lus[i]->scsi_dev, SENSE_CODE(RESET));
        }
    }
    qemu_bh_cancel(core->doorbell_bh);
    qemu_bh_cancel(core->complete_bh);
    for (unsigned i = 0; i < core->params.nutrs; i++) {
        UfsRequest *request = &core->req_list[i];
        if (request->sg) {
            qemu_sglist_destroy(request->sg);
            g_free(request->sg);
        }
        memset(request, 0, sizeof(*request));
        request->hc = core; request->slot = i; request->state = UFS_REQUEST_IDLE;
    }
    core->reg = s->reset_reg;
    pci_irq_deassert(s->engine);
    s->irq_level = 0;
    qemu_set_irq(s->irq, 0);
}
static void exynos_auto9_ufs_reset(DeviceState *dev)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(dev);
    qemu_bh_cancel(s->reset_bh);
    exynos_auto9_ufs_reset_engine(s);
    exynos_auto9_ufs_mailbox_reset(s, true);
    memset(s->vendor, 0, sizeof(s->vendor));
    s->host0_last_vid = s->host0_invalid = s->host0_s2_denied = 0;
    s->irq_assertions = s->vh_config = 0;
    /* Cold zero is a virtual initial state, not a measured FSYS reset value. */
    s->shareability = 0;
    s->unipro_period = s->unipro_writes = s->pma_writes = 0;
    s->prelink_rejected = s->prelink_last_pma = 0;
    memset(s->pma_words, 0, sizeof(s->pma_words));
    s->power_timer_mask = s->power_timer_writes = s->power_pma_writes = 0;
    memset(s->power_timer, 0, sizeof(s->power_timer));
    /* Explicit virtual initial condition: attached device out of reset.
     * Not a recovered OEM GPIO/PHY snapshot; no link/ready bit is asserted.
     */
    s->vendor[0x70 / 4] = 1;
    s->reset_assert_ns = s->reset_pulse_ns = 0;
}
static void exynos_auto9_ufs_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = exynos_auto9_ufs_realize;
    device_class_set_legacy_reset(dc, exynos_auto9_ufs_reset);
    device_class_set_props(dc, exynos_auto9_ufs_properties);
}
static void exynos_auto9_ufs_finalize(Object *obj)
{
    ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(obj);
    qemu_bh_delete(s->reset_bh);
    for (unsigned i = 0; i < s->registered_notifiers; i++) {
        memory_region_unregister_iommu_notifier(MEMORY_REGION(&s->s2mpu->iommu),
                                                &s->invalidation[i].notifier);
    }
    address_space_destroy(&s->dma_as);
}
static const TypeInfo exynos_auto9_ufs_type = {
    .name = TYPE_EXYNOS_AUTO9_UFS, .parent = TYPE_PCI_HOST_BRIDGE,
    .instance_size = sizeof(ExynosAuto9Ufs), .instance_init = exynos_auto9_ufs_init,
    .instance_finalize = exynos_auto9_ufs_finalize, .class_init = exynos_auto9_ufs_class_init,
};
static const TypeInfo exynos_auto9_ufs_iommu_type = {
    .name = TYPE_EXYNOS_AUTO9_UFS_IOMMU, .parent = TYPE_IOMMU_MEMORY_REGION,
    .instance_size = sizeof(ExynosAuto9UfsIommu),
    .class_init = exynos_auto9_ufs_iommu_class_init,
};
static void exynos_auto9_ufs_register(void)
{
    type_register_static(&exynos_auto9_ufs_iommu_type);
    type_register_static(&exynos_auto9_ufs_type);
}
type_init(exynos_auto9_ufs_register)

static bool virt_get_exynos_ufs(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->exynos_ufs;
}
static void virt_set_exynos_ufs(Object *obj, bool value, Error **errp)
{
    VIRT_MACHINE(obj)->exynos_ufs = value;
}
static bool virt_get_exynos_ufs_shareability(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->exynos_ufs_shareability;
}
static void virt_set_exynos_ufs_shareability(Object *obj, bool value, Error **errp)
{
    VIRT_MACHINE(obj)->exynos_ufs_shareability = value;
}
static void exynos_auto9_ufs_create(VirtMachineState *vms,
                                    const char *vgen_path, const char *s2mpu_path)
{
    ExynosAuto9Ufs *ph = NULL;
    for (unsigned i = 0; i <= (vms->exynos_ufs_vh ? 2 : 0); i++) {
        DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_UFS);
        ExynosAuto9Ufs *s = EXYNOS_AUTO9_UFS(dev);
        g_autofree char *name = i ? g_strdup_printf("exynos-ufs-vh%u", i) :
                                  g_strdup("exynos-ufs0");
        s->host_index = i;
        s->host0_route = vms->exynos_ufs_host0 == 1;
        s->mailbox = i == 0 && vms->exynos_ufs_mailbox == 1;
        if (i == 0) {
            ph = s;
        } else {
            s->physical = ph;
            s->endpoint = &ph->vh[i - 1];
        }
        object_property_add_child(OBJECT(vms), name, OBJECT(dev));
        object_property_set_link(OBJECT(dev), "vgen", object_resolve_path(vgen_path, NULL),
                                 &error_fatal);
        object_property_set_link(OBJECT(dev), "s2mpu", object_resolve_path(s2mpu_path, NULL),
                                 &error_fatal);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    }
}
