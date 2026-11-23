/* SPDX-License-Identifier: GPL-2.0-or-later
 * 8704 MCU SPI6 startup peer, derived from the selected original VLE routines.
 * Only the A0 reset/answer and B1 START phase is implemented. No application
 * KIPC messages, vehicle data, HSM work, or arbitrary acknowledgements.
 */
#include "hw/ssi/ssi.h"
#define TYPE_AUTO9_MCU_SPI "exynos-auto9.mcu-spi6-startup"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9McuSpi, AUTO9_MCU_SPI)

#define AUTO9_MCU_SPI_FRAME 512
#define AUTO9_MCU_SPI_PREFIX 18
#define AUTO9_MCU_A0_TURNAROUND_NS 50000
#define AUTO9_MCU_B1_TURNAROUND_NS 5000000

typedef enum Auto9McuSpiPhase {
    MCU_SPI_IDLE,
    MCU_SPI_A0_PENDING,
    MCU_SPI_A0_READY,
    MCU_SPI_B1_PENDING,
    MCU_SPI_B1_READY,
} Auto9McuSpiPhase;

struct Auto9McuSpi {
    SSIPeripheral parent_obj;
    QEMUTimer *ready_timer;
    qemu_irq request;
    uint8_t inbound[AUTO9_MCU_SPI_PREFIX], outbound[14];
    unsigned words;
    bool selected, response_transfer;
    Auto9McuSpiPhase phase;
    uint32_t resets, responses, starts, invalid, request_low, version;
};

static uint8_t auto9_mcu_spi_sum(const uint8_t *p, unsigned end)
{
    uint8_t sum = 0;
    for (unsigned i = 2; i < end; i++) {
        sum += p[i];
    }
    return sum;
}

static void auto9_mcu_spi_request(Auto9McuSpi *s, bool asserted)
{
    s->request_low = asserted;
    qemu_set_irq(s->request, !asserted); /* Original MCU_REQ is active low. */
}

static void auto9_mcu_spi_ready(void *opaque)
{
    Auto9McuSpi *s = opaque;
    if (s->phase == MCU_SPI_A0_PENDING) {
        s->phase = MCU_SPI_A0_READY;
    } else if (s->phase == MCU_SPI_B1_PENDING) {
        s->phase = MCU_SPI_B1_READY;
    } else {
        return;
    }
    auto9_mcu_spi_request(s, true);
}

static void auto9_mcu_spi_consume(Auto9McuSpi *s)
{
    static const uint8_t reset[] = { 0xfe,0xff,0xaa,0,9,1,0x90,0xa0,0x55,0xaa,0x55,0xaa };
    static const uint8_t start[] = { 0xfe,0xff,0xaa,0,13,1,0x90,0xb1,0xaa,0x55,0xaa,0x55,'S','T','A','R','T' };
    static const uint8_t response[] = { 0xff,0xfe,0xaa,0,9,0x81,0x90,0xa0,0x55,0xaa,0x55,0xaa };
    const uint8_t *p = s->inbound;
    if (!memcmp(p, reset, sizeof(reset)) && (p[12] == 0x0e || p[12] == 0x0f) &&
        p[13] == auto9_mcu_spi_sum(p, 13)) {
        /* VLE 0x103b0ca -> 0x103dcb8/0x103dcee -> 0x103b56e. */
        memcpy(s->outbound, response, sizeof(response));
        s->outbound[12] = s->version = p[12];
        s->outbound[13] = auto9_mcu_spi_sum(s->outbound, 13);
        s->resets++;
        s->phase = MCU_SPI_A0_PENDING;
        auto9_mcu_spi_request(s, false);
        /* Turnaround is virtual; wait until the AP write transaction ended. */
        timer_mod(s->ready_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                                  AUTO9_MCU_A0_TURNAROUND_NS);
    } else if (s->phase == MCU_SPI_B1_READY && !memcmp(p, start, sizeof(start)) &&
               p[17] == auto9_mcu_spi_sum(p, 17)) {
        /* VLE 0x103b6c0 -> 0x103b64a records line OK; no response is made. */
        s->starts++;
        s->phase = MCU_SPI_IDLE;
        auto9_mcu_spi_request(s, false);
    } else if (p[0] == 0xfe && p[1] == 0xff && p[2] == 0xaa &&
               (p[7] == 0xa0 || p[7] == 0xb1)) {
        s->invalid++;
    }
}

static int auto9_mcu_spi_cs(SSIPeripheral *dev, bool high)
{
    static const uint8_t dummy[AUTO9_MCU_SPI_PREFIX];
    Auto9McuSpi *s = AUTO9_MCU_SPI(dev);
    if (!high) {
        s->selected = true;
        s->words = 0;
        s->response_transfer = s->phase == MCU_SPI_A0_READY;
    } else if (s->selected) {
        s->selected = false;
        if (s->words == AUTO9_MCU_SPI_FRAME) {
            /* A retry can clock the same MISO bytes, but its nonzero MOSI
             * is an A0 write, not the AP's dummy-clock response read. */
            if (s->response_transfer && !memcmp(s->inbound, dummy, sizeof(dummy))) {
                s->responses++;
                auto9_mcu_spi_request(s, false);
                /* The OEM worker returns after accepting A0; its state 2
                 * enters the B1 send only on another low MCU_REQ interrupt.
                 * Give the AP a distinct edge, not another MISO frame. */
                s->phase = MCU_SPI_B1_PENDING;
                timer_mod(s->ready_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                                          AUTO9_MCU_B1_TURNAROUND_NS);
            }
            auto9_mcu_spi_consume(s);
        }
    }
    return 0;
}

static uint32_t auto9_mcu_spi_transfer(SSIPeripheral *dev, uint32_t value)
{
    Auto9McuSpi *s = AUTO9_MCU_SPI(dev);
    unsigned i = s->words;
    if (!s->selected) {
        return 0;
    }
    if (i <= AUTO9_MCU_SPI_FRAME) {
        s->words++;
    }
    if (i < sizeof(s->inbound)) {
        s->inbound[i] = value;
    }
    return s->response_transfer && i < sizeof(s->outbound) ? s->outbound[i] : 0;
}

static void auto9_mcu_spi_reset(DeviceState *dev)
{
    Auto9McuSpi *s = AUTO9_MCU_SPI(dev);
    timer_del(s->ready_timer);
    s->selected = s->response_transfer = false;
    s->phase = MCU_SPI_IDLE;
    s->words = s->resets = s->responses = s->starts = s->invalid = s->version = 0;
    auto9_mcu_spi_request(s, false);
}

static void auto9_mcu_spi_realize(SSIPeripheral *dev, Error **errp)
{
    Auto9McuSpi *s = AUTO9_MCU_SPI(dev);
    Object *obj = OBJECT(dev);
    qdev_init_gpio_out(DEVICE(dev), &s->request, 1);
    s->ready_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, auto9_mcu_spi_ready, s);
#define AUTO9_MCU_SPI_OBS(name, member) \
    object_property_add_uint32_ptr(obj, name, &s->member, OBJ_PROP_FLAG_READ)
    AUTO9_MCU_SPI_OBS("valid-reset-frames", resets);
    AUTO9_MCU_SPI_OBS("completed-responses", responses);
    AUTO9_MCU_SPI_OBS("confirmed-start-frames", starts);
    AUTO9_MCU_SPI_OBS("rejected-startup-frames", invalid);
    AUTO9_MCU_SPI_OBS("request-low", request_low);
    AUTO9_MCU_SPI_OBS("selected-version", version);
#undef AUTO9_MCU_SPI_OBS
}

static void auto9_mcu_spi_finalize(Object *obj)
{
    Auto9McuSpi *s = AUTO9_MCU_SPI(obj);
    if (s->ready_timer) {
        timer_free(s->ready_timer);
    }
}

static void auto9_mcu_spi_class_init(ObjectClass *klass, const void *data)
{
    SSIPeripheralClass *ssc = SSI_PERIPHERAL_CLASS(klass);
    ssc->realize = auto9_mcu_spi_realize;
    ssc->set_cs = auto9_mcu_spi_cs;
    ssc->cs_polarity = SSI_CS_LOW;
    ssc->transfer = auto9_mcu_spi_transfer;
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_mcu_spi_reset);
}

static const TypeInfo auto9_mcu_spi_type = {
    .name = TYPE_AUTO9_MCU_SPI,
    .parent = TYPE_SSI_PERIPHERAL,
    .instance_size = sizeof(Auto9McuSpi),
    .instance_finalize = auto9_mcu_spi_finalize,
    .class_init = auto9_mcu_spi_class_init,
};

static void auto9_mcu_spi_register(void)
{
    type_register_static(&auto9_mcu_spi_type);
}
type_init(auto9_mcu_spi_register)
