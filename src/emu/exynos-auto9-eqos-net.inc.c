/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "net/checksum.h"

#define EQOS_PACKET_MAX 16384

static bool auto9_eqos_dma_span(uint64_t a, size_t n)
{
    return n && ((a >= 0x90000000 && a <= UINT64_C(0xa0000000) - n) ||
                 (a >= UINT64_C(0x880000000) && a <= UINT64_C(0x8ce000000) - n) ||
                 (a >= UINT64_C(0xe0210000) && a <= UINT64_C(0xe3c00000) - n));
}

static bool auto9_eqos_dma(Auto9Eqos *s, uint64_t a, void *p, size_t n, bool wr)
{
    if (!auto9_eqos_dma_span(a, n)) {
        s->dma_errors++;
        return false;
    }
    MemTxResult result = wr ? address_space_write(&address_space_memory, a,
        MEMTXATTRS_UNSPECIFIED, p, n) : address_space_read(&address_space_memory,
        a, MEMTXATTRS_UNSPECIFIED, p, n);
    if (result != MEMTX_OK) {
        s->dma_errors++;
        return false;
    }
    return true;
}

static void auto9_eqos_irq_update(Auto9Eqos *s, unsigned q)
{
    uint32_t pending = s->channels[q][0x60 / 4] & s->channels[q][0x34 / 4];
    qemu_set_irq(s->rx_irq[q], !!(pending & (BIT(6) | BIT(7) | BIT(12))));
    qemu_set_irq(s->tx_irq[q], !!(pending & (BIT(0) | BIT(1) | BIT(2) | BIT(12))));
}

static bool auto9_eqos_link(Auto9Eqos *s)
{
    return s->nic && !qemu_get_queue(s->nic)->link_down &&
           qemu_get_queue(s->nic)->peer && !qemu_get_queue(s->nic)->peer->link_down;
}

static uint64_t auto9_eqos_desc(Auto9Eqos *s, unsigned q, bool tx, unsigned index)
{
    uint32_t *c = s->channels[q];
    uint64_t base = ((uint64_t)c[(tx ? 0x10 : 0x18) / 4] << 32) |
                    c[(tx ? 0x14 : 0x1c) / 4];
    unsigned size = tx && (c[1] & BIT(28)) ? 32 : s->descriptor_bytes;
    size += ((c[0] >> 18) & 7) * 8;
    return base + (uint64_t)index * size;
}

static bool auto9_eqos_descriptor(Auto9Eqos *s, uint64_t address, uint32_t d[4])
{
    uint8_t raw[16];
    if (!auto9_eqos_dma(s, address, raw, sizeof(raw), false)) {
        return false;
    }
    for (unsigned i = 0; i < 4; i++) {
        d[i] = ldl_le_p(raw + i * 4);
    }
    return true;
}

static bool auto9_eqos_write_word(Auto9Eqos *s, uint64_t a, uint32_t word)
{
    uint8_t raw[4];
    stl_le_p(raw, word);
    return auto9_eqos_dma(s, a, raw, sizeof(raw), true);
}

static bool auto9_eqos_checksum(uint8_t *packet, unsigned len)
{
    unsigned ip = 14;
    uint16_t type = lduw_be_p(packet + 12);
    for (unsigned tags = 0; type == 0x8100 || type == 0x88a8; tags++) {
        if (tags == 2 || len < ip + 4) { return false; }
        type = lduw_be_p(packet + ip + 2);
        ip += 4;
    }
    if (type == 0x0800) {
        net_checksum_calculate(packet, len, CSUM_ALL);
        return true;
    }
    if (type != 0x86dd || len < ip + 40) { return false; }
    unsigned size = lduw_be_p(packet + ip + 4), proto = packet[ip + 6];
    unsigned offset = proto == 6 ? 16 : proto == 17 ? 6 : proto == 58 ? 2 : UINT_MAX;
    if (offset == UINT_MAX || size < offset + 2 || len < ip + 40 + size) { return false; }
    uint8_t *data = packet + ip + 40;
    stw_be_p(data + offset, 0);
    uint32_t sum = net_checksum_add(32, packet + ip + 8) + size + proto;
    sum += net_checksum_add(size, data);
    stw_be_p(data + offset, net_checksum_finish_nozero(sum));
    return true;
}

static void auto9_eqos_tx(Auto9Eqos *s, unsigned q)
{
    uint32_t *c = s->channels[q];
    if (q >= 5 || !auto9_eqos_link(s) || !(s->words[0] & BIT(1)) || !(c[1] & 1)) {
        return;
    }
    unsigned count = (c[0x2c / 4] & 0x3ff) + 1;
    bool wide = s->words[AUTO9_EQOS_DMA_SYSBUS / 4] & BIT(11);
    for (unsigned work = 0; work <= count; work++) {
        uint32_t d[4];
        uint64_t a = auto9_eqos_desc(s, q, true, s->tx_index[q]);
        if (!auto9_eqos_descriptor(s, a, d)) {
            c[0x60 / 4] |= BIT(12) | BIT(14);
            break;
        }
        if (!(d[3] & BIT(31))) {
            c[0x60 / 4] |= BIT(2) | BIT(15);
            break;
        }
        if (work == count) {
            break;
        }
        bool context = d[3] & BIT(30), fail = false;
        if (context) {
            if (d[3] & BIT(16)) {
                s->tx_vlan[q] = d[3];
            }
        } else {
            if (d[3] & BIT(29)) {
                s->tx_length[q] = 0;
            }
            unsigned n1 = d[2] & 0x3fff, n2 = (d[2] >> 16) & 0x3fff;
            uint64_t buffer = d[0] | (wide ? (uint64_t)d[1] << 32 : 0);
            fail = (d[3] & BIT(18)) || (wide && n2) ||
                   s->tx_length[q] + n1 + n2 > EQOS_PACKET_MAX;
            if (!fail && n1) {
                fail = !auto9_eqos_dma(s, buffer,
                    s->tx_packet[q] + s->tx_length[q], n1, false);
                if (!fail) {
                    s->tx_length[q] += n1;
                }
            }
            if (!fail && n2) {
                fail = !auto9_eqos_dma(s, d[1],
                    s->tx_packet[q] + s->tx_length[q], n2, false);
                if (!fail) {
                    s->tx_length[q] += n2;
                }
            }
            if (!fail && (d[3] & BIT(28))) {
                uint8_t *packet = s->tx_packet[q];
                unsigned len = s->tx_length[q], vlan = (d[2] >> 14) & 3;
                if (len < 14) {
                    fail = true;
                } else {
                    bool tagged = lduw_be_p(packet + 12) == 0x8100;
                    if (vlan == 1 && tagged && len >= 18) {
                        memmove(packet + 12, packet + 16, len - 16);
                        len -= 4;
                    } else if (vlan >= 2 && (s->tx_vlan[q] & BIT(16))) {
                        if (!tagged) {
                            if (len + 4 > EQOS_PACKET_MAX) {
                                fail = true;
                            } else {
                                memmove(packet + 16, packet + 12, len - 12);
                                len += 4;
                            }
                        }
                        if (!fail) {
                            stw_be_p(packet + 12, 0x8100);
                            stw_be_p(packet + 14, s->tx_vlan[q]);
                        }
                    }
                    if (!fail) {
                        if ((d[3] & (3 << 16)) && !auto9_eqos_checksum(packet, len)) {
                            fail = true;
                        } else {
                            qemu_send_packet(qemu_get_queue(s->nic), packet, len);
                            s->tx_packets++;
                            s->tx_bytes += len;
                        }
                    }
                }
                s->tx_length[q] = 0;
            }
        }
        uint32_t done = d[3] & ~(BIT(31) | 0x3ffff);
        if (fail) {
            done |= BIT(15) | BIT(13);
            s->tx_errors++;
            s->tx_length[q] = 0;
        }
        if (!auto9_eqos_write_word(s, a + 12, done)) {
            c[0x60 / 4] |= BIT(12) | BIT(14);
            break;
        }
        s->tx_index[q] = (s->tx_index[q] + 1) % count;
        s->tx_descriptors++;
        if (!context && (d[2] & BIT(31))) {
            c[0x60 / 4] |= BIT(0) | BIT(15);
        }
    }
    auto9_eqos_irq_update(s, q);
}

static bool auto9_eqos_can_receive(NetClientState *nc)
{
    Auto9Eqos *s = qemu_get_nic_opaque(nc);
    return auto9_eqos_link(s) && (s->words[0] & 1) && (s->armed_rx & 31);
}

static ssize_t auto9_eqos_receive(NetClientState *nc, const uint8_t *buf, size_t len)
{
    Auto9Eqos *s = qemu_get_nic_opaque(nc);
    if (!auto9_eqos_can_receive(nc) || len < 14 || len > EQOS_PACKET_MAX - 4) {
        return -1;
    }
    bool match = (buf[0] & 1) || (s->words[8 / 4] & 1);
    unsigned preferred = 0;
    for (unsigned slot = 0; !match && slot < 32; slot++) {
        uint8_t mac[6];
        uint32_t high = s->words[(0x300 + slot * 8) / 4];
        if (slot && !(high & BIT(31))) { continue; }
        stl_le_p(mac, s->words[(0x304 + slot * 8) / 4]);
        stw_le_p(mac + 4, high);
        bool equal = true;
        for (unsigned byte = 0; byte < 6; byte++) {
            if (!(high & BIT(24 + byte)) && mac[byte] != buf[byte]) { equal = false; }
        }
        if (equal) {
            match = true;
            unsigned channels = (high >> 16) & 31;
            preferred = channels ? ctz32(channels) : 0;
        }
    }
    if (!match) {
        s->rx_filtered++;
        return len;
    }
    unsigned q = preferred < 5 && (s->channels[preferred][2] & 1) ? preferred : 0;
    while (q < 5 && !(s->channels[q][2] & 1)) {
        q++;
    }
    if (q == 5) {
        return -1;
    }
    uint32_t *c = s->channels[q];
    unsigned size = (c[2] >> 1) & 0x3fff;
    unsigned count = (c[0x30 / 4] & 0x3ff) + 1, total = MAX(len, 60) + 4;
    if (!size || total > size * count) {
        s->rx_dropped++;
        return len;
    }
    unsigned needed = (total + size - 1) / size;
    uint64_t desc[32], buffers[32];
    uint32_t words[32][4];
    if (needed > G_N_ELEMENTS(desc)) {
        s->rx_dropped++;
        return len;
    }
    for (unsigned i = 0; i < needed; i++) {
        unsigned index = (s->rx_index[q] + i) % count;
        desc[i] = auto9_eqos_desc(s, q, false, index);
        if (!auto9_eqos_descriptor(s, desc[i], words[i]) ||
            !(words[i][3] & BIT(31)) || !(words[i][3] & BIT(24))) {
            c[0x60 / 4] |= BIT(7) | BIT(14);
            auto9_eqos_irq_update(s, q);
            s->rx_dropped++;
            return len;
        }
        buffers[i] = words[i][0] | (uint64_t)words[i][1] << 32;
        if (!auto9_eqos_dma_span(buffers[i], MIN(size, total - i * size))) {
            c[0x60 / 4] |= BIT(12) | BIT(14);
            auto9_eqos_irq_update(s, q);
            s->dma_errors++;
            return len;
        }
    }
    uint8_t packet[EQOS_PACKET_MAX] = {0};
    memcpy(packet, buf, len);
    uint32_t crc = ~0u;
    for (unsigned i = 0; i < total - 4; i++) {
        crc ^= packet[i];
        for (unsigned bit = 0; bit < 8; bit++) {
            crc = (crc >> 1) ^ (0xedb88320u & -(crc & 1));
        }
    }
    stl_le_p(packet + total - 4, ~crc);
    bool interrupt = false;
    for (unsigned i = 0; i < needed; i++) {
        if (!auto9_eqos_dma(s, buffers[i], packet + i * size,
            MIN(size, total - i * size), true)) {
            c[0x60 / 4] |= BIT(12) | BIT(14);
            auto9_eqos_irq_update(s, q);
            return len;
        }
        interrupt |= words[i][3] & BIT(30);
        uint32_t flags = (i == 0 ? BIT(29) : 0) |
                         (i + 1 == needed ? BIT(28) | total : 0);
        if (!auto9_eqos_write_word(s, desc[i], 0) ||
            !auto9_eqos_write_word(s, desc[i] + 4, 0) ||
            !auto9_eqos_write_word(s, desc[i] + 8, 0) ||
            !auto9_eqos_write_word(s, desc[i] + 12, flags)) {
            c[0x60 / 4] |= BIT(12) | BIT(14);
            auto9_eqos_irq_update(s, q);
            return len;
        }
    }
    s->rx_index[q] = (s->rx_index[q] + needed) % count;
    s->rx_packets++;
    s->rx_bytes += len;
    s->rx_descriptors += needed;
    if (interrupt) {
        c[0x60 / 4] |= BIT(6) | BIT(15);
    }
    auto9_eqos_irq_update(s, q);
    return len;
}

static void auto9_eqos_link_changed(NetClientState *nc)
{
    Auto9Eqos *s = qemu_get_nic_opaque(nc);
    s->phy.partner = auto9_eqos_link(s);
    if (s->phy.partner) {
        for (unsigned q = 0; q < 5; q++) {
            auto9_eqos_tx(s, q);
        }
    }
}

static NetClientInfo auto9_eqos_net_info = {
    .type = NET_CLIENT_DRIVER_NIC,
    .size = sizeof(NICState),
    .can_receive = auto9_eqos_can_receive,
    .receive = auto9_eqos_receive,
    .link_status_changed = auto9_eqos_link_changed,
};

static void auto9_eqos_realize(DeviceState *dev, Error **errp)
{
    Auto9Eqos *s = AUTO9_EQOS(dev);
    if (!s->conf.peers.queues) {
        return;
    }
    for (unsigned i = 0; i < 6; i++) {
        s->conf.macaddr.a[i] = s->mac >> (i * 8);
    }
    s->nic = qemu_new_nic(&auto9_eqos_net_info, &s->conf,
        object_get_typename(OBJECT(s)), dev->id, &dev->mem_reentrancy_guard, s);
    s->phy.partner = auto9_eqos_link(s);
}
