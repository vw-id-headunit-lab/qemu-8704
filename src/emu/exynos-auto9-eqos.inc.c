/* SPDX-License-Identifier: GPL-2.0-or-later
 * Auto V9 ETH0 control-plane device. Original devnp-dwceqos and VDTVM2
 * supply the nonstandard 4K queue stride and relocated DMA registers.
 * Channels retain setup while disconnected; no frames, packet timestamps, DMA
 * descriptor ownership changes, or IRQ. MDIO clocks an empty bus unless the
 * optional QNX management-plane endpoint is attached.
 */
#include "qemu/int128.h"

#define AUTO9_PTP_BASE 0x1000
#define AUTO9_PTP_REF_HZ 26000000u
#define AUTO9_PTP_TCR 0x00
#define AUTO9_PTP_SSINC 0x04
#define AUTO9_PTP_SEC 0x08
#define AUTO9_PTP_SUBSEC 0x0c
#define AUTO9_PTP_SETSEC 0x10
#define AUTO9_PTP_SETSUBSEC 0x14
#define AUTO9_PTP_ADDEND 0x18
#define AUTO9_PTP_PPS_CONTROL 0x70
#define AUTO9_PTP_PPS0_SEC 0x80
#define AUTO9_PTP_PRESENTATION 0x240
#define AUTO9_PTP_PRESENTATION_UPDATE 0x244
#define AUTO9_PTP_ENABLE BIT(0)
#define AUTO9_PTP_FINE BIT(1)
#define AUTO9_PTP_INIT BIT(2)
#define AUTO9_PTP_ADJUST BIT(3)
#define AUTO9_PTP_LOAD_ADDEND BIT(5)
#define AUTO9_PTP_DIGITAL BIT(9)
#define AUTO9_PTP_SUBTRACT BIT(31)
#define AUTO9_PTP_COMMANDS (AUTO9_PTP_INIT | AUTO9_PTP_ADJUST | AUTO9_PTP_LOAD_ADDEND)
#define TYPE_AUTO9_EQOS "exynos-auto9.eqos"
#define AUTO9_EQOS_BASE 0x17d80000
#define AUTO9_EQOS_CLK 0x17d60200
#define AUTO9_EQOS_FSYS2 0x17c20008
#define AUTO9_EQOS_FSYS2_MASK (BIT(0) | BIT(4))
#define AUTO9_EQOS_DMA_MODE 0x11000
#define AUTO9_EQOS_DMA_SYSBUS 0x11004
#define AUTO9_EQOS_RESET_NS 50000
#define AUTO9_EQOS_MDIO_NS 30000
#define AUTO9_EQOS_MDIO_ADDR 0x200
#define AUTO9_EQOS_MDIO_DATA 0x204
#define AUTO9_EQOS_MMC_RX_FIFO_OVERFLOW 0x7d4
#define AUTO9_EQOS_MTL_TXQ_START 0x8000
#define AUTO9_EQOS_MTL_TXQ_END 0x10000
#define AUTO9_EQOS_MTL_FTQ BIT(0)
#define AUTO9_EQOS_CH_START 0x18000
#define AUTO9_EQOS_CH_STRIDE 0x1000
/* OEM dwmac510_rx_watchdog writes the receive IRQ watchdog at +0x38.
 * The disconnected model keeps its interval; no RX arrival can arm it. */
#define AUTO9_EQOS_CH_RIWT 0x38
#define AUTO9_EQOS_CH_RX_DROP 0x64
#define AUTO9_EQOS_CH_COUNT 8 /* VM2 ch0..4, VM3 ch5..7; original vLM owns grants. */
#define AUTO9_EQOS_READ_WORDS ((AUTO9_EQOS_CH_START + AUTO9_EQOS_CH_COUNT * AUTO9_EQOS_CH_STRIDE) / 4)
#define AUTO9_EQOS_FEATURE1 ((7u << 6) | 7u) /* Declared 16 KiB TX/RX FIFOs. */
OBJECT_DECLARE_SIMPLE_TYPE(Auto9Eqos, AUTO9_EQOS)

struct Auto9Eqos {
    SysBusDevice parent_obj;
    MemoryRegion regs, clk, fsys2;
    QEMUTimer *reset, *mdio;
    uint32_t words[0x11008 / 4];
    uint32_t channels[AUTO9_EQOS_CH_COUNT][0x64 / 4];
    uint64_t mac;
    uint32_t clock, reads, writes, mac_reads, clock_reads, clock_writes;
    uint32_t fsys2_config, fsys2_reads, fsys2_writes;
    uint32_t reset_requests, reset_completions, rejected, last_rejected;
    uint64_t mmc_overflow_reads, ptp_control_reads, ptp_time_reads, dma_debug_reads;
    uint64_t mtl_txq_flushes;
    uint32_t mtl_txq_flush_mask;
    uint64_t read_by_word[AUTO9_EQOS_READ_WORDS];
    uint32_t channel_reads, channel_writes, channel_read_mask, channel_write_mask;
    uint32_t armed_tx, armed_rx;
    uint32_t rx_drop_reads, rx_drop_read_mask;
    uint32_t mdio_reads, mdio_writes, mdio_completions;
    uint32_t mdio_busy_writes, mdio_due_on_access;
    bool phy_88q2112;
    Auto9Q2112 phy;
    uint32_t ptp_control, ptp_inc, ptp_setsec, ptp_setsubsec;
    uint32_t ptp_addend, ptp_active_addend, ptp_commands, ptp_writes;
    uint32_t ptp_mcgr, ptp_mcgr_writes, ptp_pps0_sec, ptp_pps0_writes;
    uint32_t ptp_presentation_reads;
    uint32_t ptp_presentation_latch, ptp_presentation_bias;
    uint32_t ptp_presentation_writes, ptp_presentation_commits;
    bool ptp_presentation_pending;
    uint64_t ptp_epoch;
    int64_t ptp_anchor_ns;
};

static bool auto9_eqos_config_word(hwaddr off)
{
    if (off >= 0x10 && off < 0x30) {
        return true; /* Original hash filter loop at 0x11f20 writes eight words. */
    }
    if (off >= 0x300 && off < 0x400) {
        return true; /* 32 station address pairs, original MAC init loop. */
    }
    if (off >= 0x70 && off <= 0x8c) {
        return true; /* Per-queue TX flow control. */
    }
    if (off >= AUTO9_EQOS_MTL_TXQ_START && off < AUTO9_EQOS_MTL_TXQ_END) {
        switch (off & 0xfff) {
        case 0: case 0x10: case 0x18: case 0x1c: case 0x20: case 0x24:
        case 0x2c: case 0x30:
            return true;
        }
    }
    switch (off) {
    case 0: /* Original Marvell speed/duplex RMW (0x133d8). */
    case 4: case 8: case 0xa4: case 0xa8: case 0xac:
        /* Original filter/RX queue steering at 0x11ba0..0x11bf0. */
    case 0x90: case 0x98: case 0x9c: case 0xa0: case 0xb4:
    case 0x700: case 0x70c: case 0x710: case 0x800:
    case 0x2000: case 0x2030: case 0x2034:
    case AUTO9_EQOS_DMA_MODE: case AUTO9_EQOS_DMA_SYSBUS:
        return true;
    }
    return false;
}

static uint32_t *auto9_eqos_channel_word(Auto9Eqos *s, hwaddr off)
{
    if (off < AUTO9_EQOS_CH_START) {
        return NULL;
    }
    unsigned index = (off - AUTO9_EQOS_CH_START) / AUTO9_EQOS_CH_STRIDE;
    if (index >= AUTO9_EQOS_CH_COUNT) {
        return NULL;
    }
    switch (off & 0xfff) {
    case 0: case 4: case 8:
    case 0x10: case 0x14: case 0x18: case 0x1c: case 0x20:
    case 0x28: case 0x2c: case 0x30: case 0x34: case AUTO9_EQOS_CH_RIWT: case 0x60:
        return &s->channels[index][(off & 0xfff) / 4];
    }
    return NULL;
}

static bool auto9_eqos_mmc_counter(hwaddr off)
{
    /* Original VM2 uses 16-byte LDP groups from 0x808 through 0x887.
     * All mapped packet/octet counters are zero with no RX backend. */
    if (!(off & 3) &&
        ((off >= 0x810 && off <= 0x844) ||
         (off >= 0x850 && off <= 0x884))) {
        return true;
    }
    switch (off) {
    case 0x71c: case 0x720: case 0x748: case 0x74c: case 0x750:
    case 0x754: case 0x758: case 0x75c: case 0x760: case 0x764:
    case 0x768: case 0x76c: case 0x788: case 0x78c: case 0x790:
    case 0x794: case 0x798: case 0x79c: /* RX runt error. */
    case 0x7dc: /* MMC RX watchdog errors; no received frames on this link. */
    case 0x7e0: /* MMC RX receive errors, same disconnected counter bank. */
    case 0x7a0: case 0x7a4: case 0x7a8:
    case 0x7c8: case 0x7cc: case AUTO9_EQOS_MMC_RX_FIFO_OVERFLOW:
        return true;
    default:
        return false;
    }
}

static bool auto9_eqos_mmc_snapshot_extra(hwaddr off)
{
    switch (off) {
    case 0x808: /* IPC interrupt status: no incoming packets or pending IRQ. */
    case 0x80c: case 0x848: case 0x84c:
        /* Read-only padding touched by the original 0x808/0x848 LDP. */
        return true;
    default:
        return false;
    }
}

static void auto9_eqos_mdio_done(void *opaque)
{
    Auto9Eqos *s = opaque;
    uint32_t *address = &s->words[AUTO9_EQOS_MDIO_ADDR / 4];
    uint32_t *data = &s->words[AUTO9_EQOS_MDIO_DATA / 4];
    bool write = (*address & 0xc) == 4;
    uint16_t result = 0xffff;
    if (s->phy_88q2112) {
        result = auto9_q2112_access(&s->phy, (*address >> 21) & 31,
                                    (*address >> 16) & 31, write, *data,
                                    qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    }
    if (!write) {
        *data = result;
    }
    *address &= ~1u;
    s->mdio_completions++;
}

/* MMIO and timer callbacks are BQL-serialized. The management transfer is
 * complete at its virtual deadline even if the timer callback was deferred. */
static bool auto9_eqos_mdio_busy(Auto9Eqos *s)
{
    if (timer_pending(s->mdio) &&
        timer_expired(s->mdio, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL))) {
        timer_del(s->mdio);
        s->mdio_due_on_access++;
        auto9_eqos_mdio_done(s);
    }
    return s->words[AUTO9_EQOS_MDIO_ADDR / 4] & 1;
}

static void auto9_eqos_reset_regs(Auto9Eqos *s)
{
    timer_del(s->mdio);
    memset(s->words, 0, sizeof(s->words));
    memset(s->channels, 0, sizeof(s->channels));
    s->armed_tx = s->armed_rx = 0;
    s->ptp_control = s->ptp_inc = s->ptp_setsec = s->ptp_setsubsec = 0;
    s->ptp_addend = s->ptp_active_addend = s->ptp_mcgr = 0;
    s->ptp_pps0_sec = 0;
    s->ptp_presentation_latch = s->ptp_presentation_bias = 0;
    s->ptp_presentation_pending = false;
    s->ptp_epoch = 0;
    s->ptp_anchor_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->words[0x300 / 4] = BIT(31) | ((s->mac >> 32) & 0xffff);
    s->words[0x304 / 4] = s->mac;
}

static void auto9_eqos_dma_reset_done(void *opaque)
{
    Auto9Eqos *s = opaque;
    auto9_eqos_reset_regs(s);
    s->reset_completions++;
}

static MemTxResult auto9_eqos_reject(Auto9Eqos *s, hwaddr off, bool write,
                                      uint64_t value)
{
    s->rejected++;
    s->last_rejected = off;
    qemu_log_mask(LOG_UNIMP,
                  "Auto9 EQoS unsupported %s at 0x%" HWADDR_PRIx " value=0x%" PRIx64 "\n",
                  write ? "write" : "read", AUTO9_EQOS_BASE + off, value);
    return MEMTX_DECODE_ERROR;
}

static uint32_t auto9_eqos_ptp_roll(uint32_t control)
{
    return control & AUTO9_PTP_DIGITAL ? NANOSECONDS_PER_SECOND : BIT(31);
}

static uint64_t auto9_eqos_ptp_now(Auto9Eqos *s, int64_t now)
{
    uint64_t ticks, lo, hi;
    uint64_t modulus = (uint64_t)auto9_eqos_ptp_roll(s->ptp_control) << 32;
    uint32_t inc256 = (s->ptp_inc >> 8) & 0xffff;
    if (!(s->ptp_control & AUTO9_PTP_ENABLE) || now <= s->ptp_anchor_ns) {
        return s->ptp_epoch;
    }
    ticks = muldiv64(now - s->ptp_anchor_ns, AUTO9_PTP_REF_HZ,
                     NANOSECONDS_PER_SECOND);
    if (s->ptp_control & AUTO9_PTP_FINE) {
        /* Fine update emits one increment per 32-bit accumulator overflow. */
        mulu64(&lo, &hi, ticks, s->ptp_active_addend);
        ticks = (hi << 32) | (lo >> 32);
    }
    mulu64(&lo, &hi, ticks, inc256);
    Int128 elapsed = int128_urshift(int128_make128(lo, hi), 8);
    return int128_getlo(int128_remu(int128_add(elapsed, int128_make64(s->ptp_epoch)),
                                   int128_make64(modulus)));
}

/* MAC presentation time wraps in 32-bit nanoseconds regardless of PTP mode. */
static uint32_t auto9_eqos_presentation_ns(uint64_t epoch, uint32_t roll)
{
    return (uint32_t)muldiv64(epoch, NANOSECONDS_PER_SECOND, roll);
}

static void auto9_eqos_record_read(Auto9Eqos *s, hwaddr off)
{
    /* Accepted 32-bit MMIO is BQL-serialized. No per-access allocation,
     * QMP, timing, or logging on the vCPU path. */
    s->reads++;
    if (off / 4 < G_N_ELEMENTS(s->read_by_word)) {
        s->read_by_word[off / 4]++;
    }
}

static MemTxResult auto9_eqos_ptp_read(Auto9Eqos *s, hwaddr off, uint64_t *v)
{
    uint64_t t;
    uint32_t roll = auto9_eqos_ptp_roll(s->ptp_control);
    switch (off - AUTO9_PTP_BASE) {
    case AUTO9_PTP_TCR:
        s->ptp_control_reads++;
        *v = s->ptp_control;
        break;
    case AUTO9_PTP_SSINC: *v = s->ptp_inc; break;
    case AUTO9_PTP_SEC:
    case AUTO9_PTP_SUBSEC:
        s->ptp_time_reads++;
        t = auto9_eqos_ptp_now(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
        *v = off == AUTO9_PTP_BASE + AUTO9_PTP_SEC ? t / roll : t % roll;
        break;
    case AUTO9_PTP_SETSEC: *v = s->ptp_setsec; break;
    case AUTO9_PTP_SETSUBSEC: *v = s->ptp_setsubsec; break;
    case AUTO9_PTP_ADDEND: *v = s->ptp_addend; break;
    case AUTO9_PTP_PPS_CONTROL: *v = s->ptp_mcgr; break;
    case AUTO9_PTP_PPS0_SEC: *v = s->ptp_pps0_sec; break;
    default: return auto9_eqos_reject(s, off, false, 0);
    }
    auto9_eqos_record_read(s, off);
    return MEMTX_OK;
}

static MemTxResult auto9_eqos_ptp_write(Auto9Eqos *s, hwaddr off, uint32_t v)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    uint64_t epoch = auto9_eqos_ptp_now(s, now);
    uint32_t oldroll = auto9_eqos_ptp_roll(s->ptp_control);
    switch (off - AUTO9_PTP_BASE) {
    case AUTO9_PTP_TCR: {
        uint32_t cmd = v & AUTO9_PTP_COMMANDS;
        uint32_t roll = auto9_eqos_ptp_roll(v);
        uint32_t frac = s->ptp_setsubsec & ~AUTO9_PTP_SUBTRACT;
        bool subtract = s->ptp_setsubsec & AUTO9_PTP_SUBTRACT;
        uint64_t modulus = (uint64_t)roll << 32;
        uint32_t presentation = auto9_eqos_presentation_ns(epoch, oldroll) +
                                s->ptp_presentation_bias;
        if ((cmd & (cmd - 1)) ||
            (cmd == AUTO9_PTP_INIT && (subtract || frac >= roll)) ||
            (cmd == AUTO9_PTP_ADJUST && (frac > roll || (!subtract && frac == roll)))) {
            return auto9_eqos_reject(s, off, true, v);
        }
        if (oldroll != roll) {
            epoch = muldiv64(epoch, roll, oldroll);
        }
        if (cmd == AUTO9_PTP_LOAD_ADDEND) {
            s->ptp_active_addend = s->ptp_addend;
        } else if (cmd == AUTO9_PTP_INIT) {
            epoch = (uint64_t)s->ptp_setsec * roll + frac;
        } else if (cmd == AUTO9_PTP_ADJUST) {
            uint64_t delta;
            if (subtract) {
                delta = ((uint64_t)(0u - s->ptp_setsec) * roll + (roll - frac) % roll) % modulus;
                epoch = (epoch + modulus - delta) % modulus;
            } else {
                delta = (uint64_t)s->ptp_setsec * roll + frac;
                epoch = (epoch + delta) % modulus;
            }
        }
        /*
         * Original seqos_config_mcgr_dma stages an unsigned correction at
         * MAC+0x244, then uses TSUPDT and the PTP ADDSUB direction. Consume
         * the staged correction once; a later ordinary PTP update does not
         * replay the old media correction. No MCGR pulses or DMA are emitted.
         */
        if ((cmd == AUTO9_PTP_INIT || cmd == AUTO9_PTP_ADJUST) &&
            s->ptp_presentation_pending) {
            uint32_t staged = s->ptp_presentation_latch;
            if (cmd == AUTO9_PTP_INIT) {
                presentation = staged;
            } else if (subtract) {
                presentation -= staged;
            } else {
                presentation += staged;
            }
            s->ptp_presentation_bias = presentation -
                                        auto9_eqos_presentation_ns(epoch, roll);
            s->ptp_presentation_pending = false;
            s->ptp_presentation_commits++;
        } else if (cmd == AUTO9_PTP_INIT) {
            s->ptp_presentation_bias = 0;
        }
        /* Clock state is committed in this MMIO transaction; commands do
         * not complete on a later read, and cannot fabricate packet events. */
        s->ptp_control = v & ~AUTO9_PTP_COMMANDS;
        s->ptp_commands += !!cmd;
        s->ptp_epoch = epoch;
        s->ptp_anchor_ns = now;
        break;
    }
    case AUTO9_PTP_SSINC:
        s->ptp_inc = v & 0xffff00;
        s->ptp_epoch = epoch;
        s->ptp_anchor_ns = now;
        break;
    case AUTO9_PTP_SETSEC: s->ptp_setsec = v; break;
    case AUTO9_PTP_SETSUBSEC: s->ptp_setsubsec = v; break;
    case AUTO9_PTP_ADDEND: s->ptp_addend = v; break;
    case AUTO9_PTP_PPS0_SEC:
        s->ptp_pps0_sec = v;
        s->ptp_pps0_writes++;
        break;
    case AUTO9_PTP_PPS_CONTROL:
        /* Original seqos_config_mcgr programs output zero only. Retain
         * its mode/config; no PPS pins or DMA request pulses are connected. */
        if (v & ~0xffu) {
            return auto9_eqos_reject(s, off, true, v);
        }
        s->ptp_mcgr = v;
        s->ptp_mcgr_writes++;
        break;
    default: return auto9_eqos_reject(s, off, true, v);
    }
    s->ptp_writes++;
    s->writes++;
    return MEMTX_OK;
}

static MemTxResult auto9_eqos_read(void *opaque, hwaddr off, uint64_t *value,
                                    unsigned size, MemTxAttrs attrs)
{
    Auto9Eqos *s = opaque;
    uint32_t *channel = auto9_eqos_channel_word(s, off);
    if (off == AUTO9_PTP_PRESENTATION) {
        /* Presentation time wraps at 2^32 ns, independent of the PTP
         * subsecond register's digital versus binary representation. */
        uint64_t t = auto9_eqos_ptp_now(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
        uint32_t roll = auto9_eqos_ptp_roll(s->ptp_control);
        *value = auto9_eqos_presentation_ns(t, roll) +
                 s->ptp_presentation_bias;
        s->ptp_presentation_reads++;
        auto9_eqos_record_read(s, off);
        return MEMTX_OK;
    }
    /* Both OEM drivers read this per-channel counter. The native vLM still
     * owns the VM2/VM3 aperture grants. No ingress means no missed frames. */
    if (off >= AUTO9_EQOS_CH_START &&
        off < AUTO9_EQOS_CH_START + AUTO9_EQOS_CH_COUNT * AUTO9_EQOS_CH_STRIDE &&
        (off & 0xfff) == AUTO9_EQOS_CH_RX_DROP) {
        unsigned index = (off - AUTO9_EQOS_CH_START) / AUTO9_EQOS_CH_STRIDE;
        s->rx_drop_reads++;
        s->rx_drop_read_mask |= BIT(index);
        s->channel_reads++;
        s->channel_read_mask |= BIT(index);
        auto9_eqos_record_read(s, off);
        *value = 0;
        return MEMTX_OK;
    }
    if (off == AUTO9_PTP_PRESENTATION_UPDATE) {
        *value = s->ptp_presentation_latch;
        auto9_eqos_record_read(s, off);
        return MEMTX_OK;
    }
    if (off >= AUTO9_PTP_BASE && off < AUTO9_PTP_BASE + 0x100) {
        return auto9_eqos_ptp_read(s, off, value);
    }
    if (channel) {
        *value = *channel;
        s->channel_reads++;
        s->channel_read_mask |= BIT((off - AUTO9_EQOS_CH_START) / AUTO9_EQOS_CH_STRIDE);
    } else if (off == AUTO9_EQOS_MDIO_ADDR || off == AUTO9_EQOS_MDIO_DATA) {
        auto9_eqos_mdio_busy(s);
        *value = s->words[off / 4];
        s->mdio_reads++;
    } else if (off == 0x1100c || off == 0x11010 || off == 0x11014) {
        /* DMA debug states are STOPPED without a connected packet engine. */
        s->dma_debug_reads++;
        *value = 0;
    } else if (auto9_eqos_mmc_counter(off) ||
               auto9_eqos_mmc_snapshot_extra(off)) {
        if (off == AUTO9_EQOS_MMC_RX_FIFO_OVERFLOW) {
            s->mmc_overflow_reads++;
        }
        *value = 0;
    } else if (off == 0x11c || off == 0x120) {
        *value = off == 0x120 ? AUTO9_EQOS_FEATURE1 : 0;
    } else if (auto9_eqos_config_word(off)) {
        *value = s->words[off / 4];
    } else {
        return auto9_eqos_reject(s, off, false, 0);
    }
    auto9_eqos_record_read(s, off);
    if (off == 0x300 || off == 0x304) {
        s->mac_reads++;
    }
    return MEMTX_OK;
}

static MemTxResult auto9_eqos_write(void *opaque, hwaddr off, uint64_t value,
                                     unsigned size, MemTxAttrs attrs)
{
    Auto9Eqos *s = opaque;
    uint32_t *channel = auto9_eqos_channel_word(s, off);
    if (off == AUTO9_PTP_PRESENTATION_UPDATE) {
        s->ptp_presentation_latch = value;
        s->ptp_presentation_pending = true;
        s->ptp_presentation_writes++;
        s->writes++;
        return MEMTX_OK;
    }
    if (off >= AUTO9_PTP_BASE && off < AUTO9_PTP_BASE + 0x100) {
        return auto9_eqos_ptp_write(s, off, value);
    }
    if (channel) {
        hwaddr reg = off & 0xfff;
        if (reg == 0x60) {
            *channel &= ~(uint32_t)value; /* W1C; never asserts completion. */
        } else {
            *channel = reg == AUTO9_EQOS_CH_RIWT ? value & 0xff : value;
            if (reg == 4 || reg == 8) {
                unsigned index = (off - AUTO9_EQOS_CH_START) / AUTO9_EQOS_CH_STRIDE;
                uint32_t *armed = reg == 4 ? &s->armed_tx : &s->armed_rx;
                *armed = (*armed & ~BIT(index)) | ((value & 1) << index);
                /* Disconnected device: no DMA scheduler can consume a ring. */
            }
        }
        s->channel_writes++;
        s->channel_write_mask |= BIT((off - AUTO9_EQOS_CH_START) / AUTO9_EQOS_CH_STRIDE);
        s->writes++;
        return MEMTX_OK;
    }
    if (off == AUTO9_EQOS_MDIO_ADDR || off == AUTO9_EQOS_MDIO_DATA) {
        if (auto9_eqos_mdio_busy(s)) {
            /* The register exists. Preserve the in-flight command/data and
             * deadline on an out-of-contract write, rather than aborting
             * the CPU's interconnect access or queuing a second transfer. */
            s->mdio_busy_writes++;
            return MEMTX_OK;
        }
        if (off == AUTO9_EQOS_MDIO_ADDR && (value & 1)) {
            if ((value & 2) || ((value & 0xc) != 0xc && (value & 0xc) != 4)) {
                return auto9_eqos_reject(s, off, true, value);
            }
            timer_mod(s->mdio, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                                AUTO9_EQOS_MDIO_NS);
        }
        s->words[off / 4] = off == AUTO9_EQOS_MDIO_DATA ? value & 0xffff : value;
        s->mdio_writes++;
        s->writes++;
        return MEMTX_OK;
    }
    if (!auto9_eqos_config_word(off)) {
        return auto9_eqos_reject(s, off, true, value);
    }
    if (off >= AUTO9_EQOS_MTL_TXQ_START && off < AUTO9_EQOS_MTL_TXQ_END &&
        !(off & 0xfff) && (value & AUTO9_EQOS_MTL_FTQ)) {
        /* No TX FIFO or pending TX status exists on this model. The empty
         * queue's flush completes in this transaction; FTQ never latches. */
        s->mtl_txq_flushes++;
        s->mtl_txq_flush_mask |= BIT((off - AUTO9_EQOS_MTL_TXQ_START) / 0x1000);
        value &= ~AUTO9_EQOS_MTL_FTQ;
    }
    if (off == AUTO9_EQOS_DMA_MODE) {
        if (value & ~0x10001ull) {
            return auto9_eqos_reject(s, off, true, value);
        }
        if (value & 1) {
            if (timer_pending(s->reset)) {
                return auto9_eqos_reject(s, off, true, value);
            }
            timer_mod(s->reset, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                                AUTO9_EQOS_RESET_NS);
            s->reset_requests++;
        } else if (timer_pending(s->reset)) {
            /* The guest cannot clear a reset that is still in progress. */
            value |= 1;
        }
    }
    s->words[off / 4] = value;
    s->writes++;
    return MEMTX_OK;
}

static uint64_t auto9_eqos_clock_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9Eqos *s = opaque;
    s->clock_reads++;
    return s->clock;
}

static void auto9_eqos_clock_write(void *opaque, hwaddr off, uint64_t value,
                                  unsigned size)
{
    Auto9Eqos *s = opaque;
    s->clock_writes++;
    s->clock = value & BIT(1);
}

/*
 * Original Linux EQoS ETH0 resource (final 4-byte DT reg), seqos_platform_probe
 * + exact read/OR 0x11/write sequence at 0xffffffc0105f11d4..11f8.
 * Virtual reset is zero; only the two original configuration bits latch.
 * This SYSREG is separate from MAC DMA reset and does not imply link, DMA,
 * interrupt, or timestamp readiness.
 */
static uint64_t auto9_eqos_fsys2_read(void *opaque, hwaddr off, unsigned size)
{
    Auto9Eqos *s = opaque;
    s->fsys2_reads++;
    return s->fsys2_config;
}

static void auto9_eqos_fsys2_write(void *opaque, hwaddr off, uint64_t value,
                                  unsigned size)
{
    Auto9Eqos *s = opaque;
    s->fsys2_writes++;
    s->fsys2_config = value & AUTO9_EQOS_FSYS2_MASK;
}

static const MemoryRegionOps auto9_eqos_fsys2_ops = {
    .read = auto9_eqos_fsys2_read,
    .write = auto9_eqos_fsys2_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static const MemoryRegionOps auto9_eqos_ops = {
    .read_with_attrs = auto9_eqos_read, .write_with_attrs = auto9_eqos_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static const MemoryRegionOps auto9_eqos_clock_ops = {
    .read = auto9_eqos_clock_read, .write = auto9_eqos_clock_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_eqos_reset(DeviceState *dev)
{
    Auto9Eqos *s = AUTO9_EQOS(dev);
    timer_del(s->reset);
    auto9_eqos_reset_regs(s);
    auto9_q2112_reset(&s->phy);
    s->clock = s->reads = s->writes = s->mac_reads = 0;
    s->clock_reads = s->clock_writes = 0;
    s->fsys2_config = s->fsys2_reads = s->fsys2_writes = 0;
    s->channel_reads = s->channel_writes = 0;
    s->channel_read_mask = s->channel_write_mask = 0;
    s->ptp_commands = s->ptp_writes = s->ptp_mcgr_writes = 0;
    s->ptp_pps0_writes = s->ptp_presentation_reads = 0;
    s->mdio_reads = s->mdio_writes = s->mdio_completions = 0;
    s->mdio_busy_writes = s->mdio_due_on_access = 0;
    s->reset_requests = s->reset_completions = s->rejected = s->last_rejected = 0;
    s->mmc_overflow_reads = s->ptp_control_reads = s->ptp_time_reads = s->dma_debug_reads = 0;
    s->mtl_txq_flushes = s->mtl_txq_flush_mask = 0;
    memset(s->read_by_word, 0, sizeof(s->read_by_word));
}

static char *auto9_eqos_read_histogram(Object *obj, Error **errp)
{
    const Auto9Eqos *s = AUTO9_EQOS(obj);
    GString *out = g_string_new(NULL);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->read_by_word); i++) {
        if (s->read_by_word[i]) {
            g_string_append_printf(out, "%x:%" PRIu64 "\n", i * 4, s->read_by_word[i]);
        }
    }
    return g_string_free(out, false);
}

static void auto9_eqos_init(Object *obj)
{
    Auto9Eqos *s = AUTO9_EQOS(obj);
    s->reset = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_eqos_dma_reset_done, s);
    s->mdio = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_eqos_mdio_done, s);
    memory_region_init_io(&s->regs, obj, &auto9_eqos_ops, s,
                          "auto9-eqos0-control", 0x20000);
    memory_region_init_io(&s->clk, obj, &auto9_eqos_clock_ops, s,
                          "auto9-eqos0-clock-config", 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regs);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->clk);
    memory_region_init_io(&s->fsys2, obj, &auto9_eqos_fsys2_ops, s,
                          "auto9-eqos0-fsys2-config", 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->fsys2);
    object_property_add_uint32_ptr(obj, "fsys2-config", &s->fsys2_config, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys2-reads", &s->fsys2_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fsys2-writes", &s->fsys2_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_str(obj, "read-histogram", auto9_eqos_read_histogram, NULL);
    object_property_add_uint64_ptr(obj, "mtl-txq-flushes", &s->mtl_txq_flushes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mtl-txq-flush-mask", &s->mtl_txq_flush_mask, OBJ_PROP_FLAG_READ);
    /* Host-only counters: terminal QMP attribution without a log per read. */
    object_property_add_uint64_ptr(obj, "mmc-overflow-reads", &s->mmc_overflow_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "ptp-control-reads", &s->ptp_control_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "ptp-time-reads", &s->ptp_time_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "dma-debug-reads", &s->dma_debug_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "channel-reads", &s->channel_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-control", &s->ptp_control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-writes", &s->ptp_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-commands", &s->ptp_commands, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-mcgr", &s->ptp_mcgr, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-mcgr-writes", &s->ptp_mcgr_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-pps0-writes", &s->ptp_pps0_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-presentation-reads", &s->ptp_presentation_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-presentation-update", &s->ptp_presentation_latch, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-presentation-writes", &s->ptp_presentation_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-presentation-commits", &s->ptp_presentation_commits, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-increment", &s->ptp_inc, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "ptp-active-addend", &s->ptp_active_addend, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "channel-writes", &s->channel_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "channel-read-mask", &s->channel_read_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "channel-write-mask", &s->channel_write_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rx-drop-reads", &s->rx_drop_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rx-drop-read-mask", &s->rx_drop_read_mask, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "armed-tx", &s->armed_tx, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "armed-rx", &s->armed_rx, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mdio-reads", &s->mdio_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mdio-writes", &s->mdio_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mdio-completions", &s->mdio_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mdio-busy-writes", &s->mdio_busy_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mdio-due-on-access", &s->mdio_due_on_access, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "q2112-reads", &s->phy.reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "q2112-writes", &s->phy.writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "q2112-id-reads", &s->phy.id_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "q2112-link-reads", &s->phy.link_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "q2112-reset-requests", &s->phy.reset_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "q2112-unsupported", &s->phy.unknown, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mac-reads", &s->mac_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "hash-lo", &s->words[0x10 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "hash-2", &s->words[0x18 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "hash-7", &s->words[0x2c / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "hash-hi", &s->words[0x14 / 4], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "mac-config", &s->words[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "clock-config", &s->clock, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "clock-reads", &s->clock_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "clock-writes", &s->clock_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-requests", &s->reset_requests, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-completions", &s->reset_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected", &s->last_rejected, OBJ_PROP_FLAG_READ);
}

static void auto9_eqos_finalize(Object *obj)
{
    Auto9Eqos *s = AUTO9_EQOS(obj);
    timer_free(s->reset);
    timer_free(s->mdio);
}

static void auto9_eqos_class(ObjectClass *klass, const void *data)
{
    static const Property properties[] = {
        DEFINE_PROP_BOOL("phy-88q2112", Auto9Eqos, phy_88q2112, false),
    };
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, auto9_eqos_reset);
    device_class_set_props(dc, properties);
}

static const TypeInfo auto9_eqos_type = {
    .name = TYPE_AUTO9_EQOS, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9Eqos), .instance_init = auto9_eqos_init,
    .instance_finalize = auto9_eqos_finalize, .class_init = auto9_eqos_class,
};

static void auto9_eqos_register(void) { type_register_static(&auto9_eqos_type); }
type_init(auto9_eqos_register)

static void auto9_eqos_create(VirtMachineState *vms)
{
    if (!vms->exynos_eqos_mac) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_EQOS);
    AUTO9_EQOS(dev)->mac = vms->exynos_eqos_mac;
    object_property_add_child(OBJECT(vms), "exynos-eqos0", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, AUTO9_EQOS_BASE);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, AUTO9_EQOS_CLK);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2, AUTO9_EQOS_FSYS2);
}

