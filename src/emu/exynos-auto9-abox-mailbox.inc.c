/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Auto9 ABOX mailbox: narrow, two-direction native hardware model.
 * AP -> DSP uses INTGR0 bits 31:16, DSP -> AP INTGR1 bits 15:0.
 * Two peers exchange register snapshots and ACKs, not canned IPC responses.
 */
#include "chardev/char-fe.h"
#include "chardev/char.h"
#include "hw/core/irq.h"
#include "qemu/bitops.h"
#include "qemu/bswap.h"
#include "system/reset.h"

enum {
    /* DSP RX reads ISSR[0..31]; AP normally writes only 26 words. */
    ABOX_MSG_WORDS = 32,
    ABOX_MSG_BYTES = 16 + 4 * ABOX_MSG_WORDS,
    ABOX_MB_MAGIC = 0x584f4241,  /* "ABOX" on the wire */
    ABOX_MB_SEND = 1, ABOX_MB_ACK = 2, ABOX_MB_CANCEL = 3,
};

typedef struct Auto9MailboxLink Auto9MailboxLink;
typedef struct Auto9MailboxBank {
    Auto9MailboxLink *link;
    MemoryRegion mmio;
    qemu_irq irq, notify;
    uint32_t tx[ABOX_MSG_WORDS], rx[ABOX_MSG_WORDS], mask[2];
    uint32_t tx_pending, rx_pending, tx_seq, rx_seq;
    uint32_t sends, receives, acknowledgments;
    unsigned index;
} Auto9MailboxBank;

struct Auto9MailboxLink {
    CharFrontend serial;
    Auto9MailboxBank bank[4];
    uint8_t input[ABOX_MSG_BYTES];
    unsigned have;
    bool dsp;
    uint32_t sends, receives, acknowledgments, rejects, linked_bank_mask;
};

static void auto9_mb_irq(Auto9MailboxBank *b)
{
    unsigned incoming = b->link->dsp ? 0 : 1;
    bool high = !!(b->rx_pending & ~b->mask[incoming]);
    qemu_set_irq(b->irq, high);
    qemu_set_irq(b->notify, high);
}

static bool auto9_mb_single(uint32_t value)
{
    return value && !(value & (value - 1));
}

static bool auto9_mb_emit(Auto9MailboxBank *b, uint32_t op, uint32_t seq,
                         uint32_t bits, const uint32_t *data)
{
    Auto9MailboxLink *l = b->link;
    uint8_t frame[ABOX_MSG_BYTES] = {0};
    stl_le_p(frame, ABOX_MB_MAGIC);
    stl_le_p(frame + 4, seq);
    stl_le_p(frame + 8, bits);
    stl_le_p(frame + 12, (op << 8) | b->index);
    if (data) {
        for (unsigned i = 0; i < ABOX_MSG_WORDS; ++i) {
            stl_le_p(frame + 16 + 4 * i, data[i]);
        }
    }
    return qemu_chr_fe_backend_open(&l->serial) &&
        qemu_chr_fe_write_all(&l->serial, frame, sizeof(frame)) == sizeof(frame);
}

static void auto9_mb_consume(Auto9MailboxLink *l)
{
    uint32_t seq = ldl_le_p(l->input + 4), bits = ldl_le_p(l->input + 8);
    uint32_t code = ldl_le_p(l->input + 12), i = code & 0xff, op = code >> 8;
    if (ldl_le_p(l->input) != ABOX_MB_MAGIC || !seq || i >= ARRAY_SIZE(l->bank) ||
        !(l->linked_bank_mask & BIT(i)) ||
        !auto9_mb_single(bits)) {
        goto reject;
    }
    Auto9MailboxBank *b = &l->bank[i];
    if (op == ABOX_MB_SEND) {
        uint32_t allowed = l->dsp ? 0xffff0000u : 0x0000ffffu;
        if (b->rx_pending || (bits & ~allowed)) {
            goto reject;
        }
        for (unsigned word = 0; word < ABOX_MSG_WORDS; ++word) {
            b->rx[word] = ldl_le_p(l->input + 16 + 4 * word);
        }
        b->rx_seq = seq;
        b->rx_pending = bits;
        b->receives++;
        l->receives++;
        qemu_log_mask(LOG_UNIMP,
                      "Auto9 ABOX %s RX bank=%u seq=%u bits=%08x words=%08x/%08x/%08x\n",
                      l->dsp ? "DSP" : "AP", i, seq, bits, b->rx[0], b->rx[1], b->rx[2]);
        auto9_mb_irq(b);
    } else if (op == ABOX_MB_ACK) {
        if (b->tx_seq != seq || !b->tx_pending) {
            return; /* Late ACK to an already canceled generation. */
        }
        if (b->tx_pending != bits) {
            goto reject;
        }
        b->tx_pending = 0;
        b->acknowledgments++;
        l->acknowledgments++;
    } else if (op == ABOX_MB_CANCEL) {
        if (b->rx_seq == seq && b->rx_pending == bits) {
            b->rx_pending = 0;
            auto9_mb_irq(b);
        }
    } else {
        goto reject;
    }
    return;
reject:
    l->rejects++;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX rejected wire bank=%u op=%u seq=%u bits=%08x\n",
                  i, op, seq, bits);
}

static int auto9_mb_can_read(void *opaque)
{
    Auto9MailboxLink *l = opaque;
    return ABOX_MSG_BYTES - l->have;
}

static void auto9_mb_receive(void *opaque, const uint8_t *data, int n)
{
    Auto9MailboxLink *l = opaque;
    while (n > 0) {
        unsigned take = MIN((unsigned)n, ABOX_MSG_BYTES - l->have);
        memcpy(l->input + l->have, data, take);
        l->have += take;
        data += take;
        n -= take;
        if (l->have == ABOX_MSG_BYTES) {
            auto9_mb_consume(l);
            l->have = 0;
        }
    }
}

static void auto9_mb_reset_bank(Auto9MailboxBank *b)
{
    memset(b->tx, 0, sizeof(b->tx));
    memset(b->rx, 0, sizeof(b->rx));
    b->mask[0] = b->mask[1] = 0;
    b->tx_pending = b->rx_pending = 0;
    auto9_mb_irq(b);
}

static void auto9_mb_reset(void *opaque)
{
    Auto9MailboxLink *l = opaque;
    l->have = 0;
    for (unsigned i = 0; i < ARRAY_SIZE(l->bank); ++i) {
        auto9_mb_reset_bank(&l->bank[i]);
    }
}

static void auto9_mb_event(void *opaque, QEMUChrEvent event)
{
    if (event == CHR_EVENT_CLOSED) {
        auto9_mb_reset(opaque);
    }
}

static bool auto9_mb_data_offset(hwaddr off, unsigned base)
{
    return off >= base && off < base + ABOX_MSG_WORDS * 4;
}

static MemTxResult auto9_mb_read(void *opaque, hwaddr off, uint64_t *value,
                               unsigned size, MemTxAttrs attrs)
{
    Auto9MailboxBank *b = opaque;
    unsigned incoming = b->link->dsp ? 0 : 1;
    if (off == 0) {
        *value = 0;  /* reset strobe self-clears */
        return MEMTX_OK;
    }
    unsigned rx_base = b->link->dsp ? 0x80 : 0x100;
    unsigned tx_base = b->link->dsp ? 0x100 : 0x80;
    if (auto9_mb_data_offset(off, rx_base)) {
        *value = b->rx[(off - rx_base) >> 2];
        return MEMTX_OK;
    }
    if (auto9_mb_data_offset(off, tx_base)) {
        *value = b->tx[(off - tx_base) >> 2];
        return MEMTX_OK;
    }
    if (off >= 8 && off <= 0x2c) {
        unsigned group = (off - 8) / 0x14, reg = (off - 8) % 0x14;
        uint32_t pending = group == incoming ? b->rx_pending : b->tx_pending;
        switch (reg) {
        case 8: *value = b->mask[group]; return MEMTX_OK;
        case 12: *value = pending; return MEMTX_OK;
        case 16: *value = pending & ~b->mask[group]; return MEMTX_OK;
        }
    }
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX unsupported read bank=%u off=%03x\n",
                  b->index, (unsigned)off);
    return MEMTX_DECODE_ERROR;
}

static MemTxResult auto9_mb_write(void *opaque, hwaddr off, uint64_t value,
                                unsigned size, MemTxAttrs attrs)
{
    Auto9MailboxBank *b = opaque;
    Auto9MailboxLink *l = b->link;
    unsigned incoming = l->dsp ? 0 : 1;
    if (off == 0 && value == 1 && !b->tx_pending && !b->rx_pending) {
        auto9_mb_reset_bank(b);
        return MEMTX_OK;
    }
    unsigned tx_base = l->dsp ? 0x100 : 0x80;
    if (auto9_mb_data_offset(off, tx_base) && !b->tx_pending) {
        b->tx[(off - tx_base) >> 2] = value;
        return MEMTX_OK;
    }
    if (off >= 8 && off <= 0x2c) {
        unsigned group = (off - 8) / 0x14, reg = (off - 8) % 0x14;
        uint32_t allowed = group ? 0xffffu : 0xffff0000u;
        if (reg == 8 && !(value & ~allowed)) {
            b->mask[group] = value;
            auto9_mb_irq(b);
            return MEMTX_OK;
        }
        if (reg == 0 && group != incoming && !(value & ~allowed) &&
            b->irq && !b->tx_pending && auto9_mb_single(value)) {
            uint32_t seq = b->tx_seq + 1;
            if (!seq) {
                seq = 1;
            }
            /* INTGR is a present mailbox strobe. A missing DSP peer must not
             * turn this store into a synchronous external abort: OEM
             * tdm_mux_ipc wrote 0x19c95008 and the resulting SEA panicked VM3. */
            if (!auto9_mb_emit(b, ABOX_MB_SEND, seq, value, b->tx)) {
                if (l->dsp) {
                    goto reject;
                }
                qemu_log_mask(LOG_UNIMP,
                              "Auto9 ABOX %s TX bank=%u seq=%u bits=%08x dropped; DSP peer not open\n",
                              l->dsp ? "DSP" : "AP", b->index, seq, (uint32_t)value);
            }
            b->tx_pending = value;
            b->tx_seq = seq;
            b->sends++;
            l->sends++;
            qemu_log_mask(LOG_UNIMP,
                          "Auto9 ABOX %s TX bank=%u seq=%u bits=%08x words=%08x/%08x/%08x\n",
                          l->dsp ? "DSP" : "AP", b->index, seq, (uint32_t)value,
                          b->tx[0], b->tx[1], b->tx[2]);
            return MEMTX_OK;
        }
        if (reg == 4 && !(value & ~allowed)) {
            if (group == incoming) {
                uint32_t cleared = b->rx_pending & value;
                if (cleared && !auto9_mb_emit(b, ABOX_MB_ACK, b->rx_seq, cleared, NULL)) {
                    if (l->dsp) {
                        goto reject;
                    }
                    qemu_log_mask(LOG_UNIMP,
                                  "Auto9 ABOX ACK bank=%u seq=%u bits=%08x dropped; DSP peer not open\n",
                                  b->index, b->rx_seq, cleared);
                }
                b->rx_pending &= ~cleared;
                auto9_mb_irq(b);
            } else {
                uint32_t cleared = b->tx_pending & value;
                if (cleared && !auto9_mb_emit(b, ABOX_MB_CANCEL, b->tx_seq, cleared, NULL)) {
                    if (l->dsp) {
                        goto reject;
                    }
                    qemu_log_mask(LOG_UNIMP,
                                  "Auto9 ABOX CANCEL bank=%u seq=%u bits=%08x dropped; DSP peer not open\n",
                                  b->index, b->tx_seq, cleared);
                }
                b->tx_pending &= ~cleared;
            }
            return MEMTX_OK;
        }
    }
reject:
    l->rejects++;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 ABOX rejected write bank=%u off=%03x value=%08x\n",
                  b->index, (unsigned)off, (uint32_t)value);
    return MEMTX_ACCESS_ERROR;
}

static const MemoryRegionOps auto9_mb_ops = {
    .read_with_attrs = auto9_mb_read,
    .write_with_attrs = auto9_mb_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_mb_create(Object *owner, Chardev *chardev, bool dsp,
                           qemu_irq mailbox0, qemu_irq mailbox1, qemu_irq mailbox2,
                           qemu_irq notify0, qemu_irq notify1, qemu_irq notify2)
{
    Auto9MailboxLink *l = g_new0(Auto9MailboxLink, 1);
    Object *stats = object_new(TYPE_CONTAINER);
    object_property_add_child(owner, "abox-mailbox-link", stats);
    object_unref(stats);
    l->dsp = dsp;
    object_property_add_uint32_ptr(stats, "sends", &l->sends, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(stats, "receives", &l->receives, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(stats, "acknowledgments", &l->acknowledgments, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(stats, "rejects", &l->rejects, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(stats, "abox-linked-bank-mask", &l->linked_bank_mask, OBJ_PROP_FLAG_READ);
    const qemu_irq mailbox[] = { mailbox0, mailbox1, mailbox2, NULL };
    const qemu_irq notify[] = { notify0, notify1, notify2, NULL };
    for (unsigned i = 0; i < ARRAY_SIZE(l->bank); ++i) {
        Auto9MailboxBank *b = &l->bank[i];
        g_autofree char *name = g_strdup_printf("auto9-abox-mailbox%u", i);
        b->link = l;
        b->index = i;
        b->irq = mailbox[i];
        b->notify = notify[i];
        if (b->irq) {
            g_autofree char *sends = g_strdup_printf("bank-%u-sends", i);
            g_autofree char *receives = g_strdup_printf("bank-%u-receives", i);
            g_autofree char *acks = g_strdup_printf("bank-%u-acknowledgments", i);
            l->linked_bank_mask |= BIT(i);
            object_property_add_uint32_ptr(stats, sends, &b->sends, OBJ_PROP_FLAG_READ);
            object_property_add_uint32_ptr(stats, receives, &b->receives, OBJ_PROP_FLAG_READ);
            object_property_add_uint32_ptr(stats, acks, &b->acknowledgments, OBJ_PROP_FLAG_READ);
        }
        memory_region_init_io(&b->mmio, stats, &auto9_mb_ops, b, name, 0x1000);
        memory_region_add_subregion(get_system_memory(), 0x19c94000u + 0x1000u * i, &b->mmio);
    }
    qemu_register_reset(auto9_mb_reset, l);
    qemu_chr_fe_init(&l->serial, chardev, &error_fatal);
    qemu_chr_fe_set_handlers(&l->serial, auto9_mb_can_read, auto9_mb_receive,
                             auto9_mb_event, NULL, l, NULL, true);
}

