/* SPDX-License-Identifier: GPL-2.0-or-later */
/* 88Q211x management-plane endpoint. No MDI partner or packet transport.
 * Infineon MV-S110850-00 Rev. K, tables 46-55, 91-93 and 181.
 * The original QNX driver uses Clause 22 registers 13/14 as an MMD window.
 */
#define AUTO9_Q2112_ADDRESS 7
#define AUTO9_Q2112_RESET_NS 100000
#define AUTO9_Q2112_ID1 0x002b
#define AUTO9_Q2112_ID2 0x0981 /* Virtual family revision 1, not silicon identification. */
#define AUTO9_Q2112_LATCHES 192

typedef struct Auto9Q2112Latch {
    uint32_t key;
    uint16_t data;
} Auto9Q2112Latch;

typedef struct Auto9Q2112 {
    bool partner;
    uint16_t selector, pointer[32];
    uint16_t pma_control, pma_t1, pma_t1_control, pcs_control, pcs_t1;
    uint16_t autoneg, port_control;
    int64_t pma_reset_until, pcs_reset_until;
    uint32_t reads, writes, unknown, link_reads, id_reads, reset_requests;
    unsigned used;
    Auto9Q2112Latch latches[AUTO9_Q2112_LATCHES];
} Auto9Q2112;

static void auto9_q2112_reset(Auto9Q2112 *p)
{
    memset(p, 0, sizeof(*p));
    /* Virtual 100 Mb/s slave strap; the OEM configures its own mode. */
    p->pma_t1 = 0x8000;
}

static bool auto9_q2112_vendor(unsigned dev, uint16_t reg)
{
    /* Retain the original driver's opaque tuning writes, without analog
     * effects. Reserved/unknown reads without a preceding write stay open. */
    return (dev == 3 || dev == 7 || dev == 31) && reg >= 0x8000;
}

static uint16_t auto9_q2112_mmd_read(Auto9Q2112 *p, unsigned dev,
                                    uint16_t reg, int64_t now)
{
    if (dev == 1 || dev == 3 || dev == 7) {
        switch (reg) {
        case 2: case 14: p->id_reads++; return AUTO9_Q2112_ID1;
        case 3: case 15: p->id_reads++; return AUTO9_Q2112_ID2;
        case 5: return 0x008a; /* PMA, PCS and AN; not a Clause 22 register bank. */
        case 6: return 0;
        }
    }
    switch ((dev << 16) | reg) {
    case 0x10000:
        return p->pma_control | ((p->pma_t1 & 0xf) == 1 ? 0x0040 : 0x2000) |
               (now < p->pma_reset_until ? 0x8000 : 0);
    case 0x10004: return 0x0010;
    case 0x10012: return 3;
    case 0x10834: return p->pma_t1;
    case 0x10900: return p->pma_t1_control | (now < p->pma_reset_until ? 0x8000 : 0);
    case 0x30000: return p->pcs_control | (now < p->pcs_reset_until ? 0x8000 : 0);
    case 0x30900: return p->pcs_t1 | (now < p->pcs_reset_until ? 0x8000 : 0);
    case 0x38000: return now < p->pcs_reset_until ? 0x8000 : 0;
    case 0x10001: case 0x10901: case 0x30001: case 0x30901:
    case 0x30902: case 0x70201:
        p->link_reads++;
        return p->partner ? BIT(2) : 0;
    case 0x70200: return p->autoneg;
    case 0x1f8001: return p->port_control;
    }
    if (auto9_q2112_vendor(dev, reg)) {
        for (unsigned i = 0; i < p->used; i++) {
            if (p->latches[i].key == ((dev << 16) | reg)) {
                return p->latches[i].data;
            }
        }
    }
    p->unknown++;
    return 0xffff;
}

static void auto9_q2112_mmd_write(Auto9Q2112 *p, unsigned dev, uint16_t reg,
                                  uint16_t value, int64_t now)
{
    int64_t *reset = NULL;
    switch ((dev << 16) | reg) {
    case 0x10000: p->pma_control = value & 0x0801; reset = &p->pma_reset_until; break;
    case 0x10834:
        if ((value & 0xf) <= 1) {
            p->pma_t1 = 0x8000 | (value & 0x4001);
        } else {
            p->unknown++;
        }
        return;
    case 0x10900: p->pma_t1_control = value & 0x4800; reset = &p->pma_reset_until; break;
    case 0x30000: p->pcs_control = value & 0x4800; reset = &p->pcs_reset_until; break;
    case 0x30900: p->pcs_t1 = value & 0x4000; reset = &p->pcs_reset_until; break;
    case 0x38000: reset = &p->pcs_reset_until; break;
    case 0x70200: p->autoneg = value & 0x1000; return;
    case 0x1f8001: p->port_control = value & 0xc000; return;
    }
    if (reset) {
        if (value & 0x8000) {
            *reset = now + AUTO9_Q2112_RESET_NS;
            p->reset_requests++;
        }
        return;
    }
    if (auto9_q2112_vendor(dev, reg)) {
        uint32_t key = (dev << 16) | reg;
        for (unsigned i = 0; i < p->used; i++) {
            if (p->latches[i].key == key) {
                p->latches[i].data = value;
                return;
            }
        }
        if (p->used < AUTO9_Q2112_LATCHES) {
            p->latches[p->used++] = (Auto9Q2112Latch){ key, value };
            return;
        }
    }
    p->unknown++;
}

static uint16_t auto9_q2112_access(Auto9Q2112 *p, unsigned phy, unsigned reg,
                                   bool write, uint16_t data, int64_t now)
{
    if (phy != AUTO9_Q2112_ADDRESS) {
        return 0xffff;
    }
    if (write) {
        p->writes++;
    } else {
        p->reads++;
    }
    if (reg == 13) {
        if (write) {
            p->selector = data & 0xc01f;
        }
        return p->selector;
    }
    if (reg == 14) {
        unsigned dev = p->selector & 31;
        switch (p->selector >> 14) {
        case 0:
            if (write) {
                p->pointer[dev] = data;
            }
            return p->pointer[dev];
        case 1:
            if (write) {
                auto9_q2112_mmd_write(p, dev, p->pointer[dev], data, now);
                return 0;
            }
            return auto9_q2112_mmd_read(p, dev, p->pointer[dev], now);
        }
    }
    p->unknown++;
    return 0xffff;
}
