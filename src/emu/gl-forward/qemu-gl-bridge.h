#define GL_BULK_BYTES (1 * MiB)
/* Existing bridge clients limit one locked record to 16 MiB plus its header. */
#define GL_MAX_RECORD (16 * MiB + 24)
#define GL_TX_BYTES (2 * GL_MAX_RECORD)

typedef struct OEMGLService {
    MemoryRegion gl_mmio;
    MemoryRegion gl_bulk;
    CharFrontend gl_chr;
    uint8_t gl_tx[4096];
    uint8_t *gl_bulk_tx, *gl_rx;
    unsigned gl_rx_count;
    bool gl_locked;
    uint8_t gl_pending[GL_TX_BYTES];
    unsigned gl_tx_head, gl_tx_count;
    guint gl_watch;
} OEMGLService;

/* Optional experimental GLES transport, enabled by -chardev ...,id=glbridge.
 * This aperture is a host service, not a Tegra hardware device.
 */
static gboolean gl_writable(void *unused, GIOCondition condition, void *opaque);

static void gl_drain(OEMGLService *s)
{
    unsigned budget = 256 * KiB;

    while (s->gl_tx_count && budget && qemu_chr_fe_backend_open(&s->gl_chr)) {
        unsigned n = MIN(budget, MIN(s->gl_tx_count,
                                    GL_TX_BYTES - s->gl_tx_head));
        int written = qemu_chr_fe_write(&s->gl_chr,
                                       s->gl_pending + s->gl_tx_head, n);
        if (written <= 0) {
            break;
        }
        s->gl_tx_head = (s->gl_tx_head + written) % GL_TX_BYTES;
        s->gl_tx_count -= written;
        budget -= written;
    }
    if (s->gl_tx_count && !s->gl_watch &&
        qemu_chr_fe_backend_open(&s->gl_chr)) {
        s->gl_watch = qemu_chr_fe_add_watch(&s->gl_chr, G_IO_OUT | G_IO_HUP,
                                           gl_writable, s);
    }
}

static gboolean gl_writable(void *unused, GIOCondition condition, void *opaque)
{
    OEMGLService *s = opaque;
    s->gl_watch = 0;
    if (!(condition & G_IO_HUP)) {
        gl_drain(s);
    }
    return G_SOURCE_REMOVE;
}

static void gl_queue(OEMGLService *s, const uint8_t *data, unsigned length)
{
    if (length > GL_TX_BYTES - s->gl_tx_count) {
        error_report("glbridge: TX exceeds reserved record capacity");
        return;
    }
    unsigned tail = (s->gl_tx_head + s->gl_tx_count) % GL_TX_BYTES;
    unsigned first = MIN(length, GL_TX_BYTES - tail);
    memcpy(s->gl_pending + tail, data, first);
    memcpy(s->gl_pending, data + first, length - first);
    s->gl_tx_count += length;
    gl_drain(s);
}

static void gl_clear_tx(OEMGLService *s)
{
    if (s->gl_watch) {
        g_source_remove(s->gl_watch);
        s->gl_watch = 0;
    }
    s->gl_tx_head = s->gl_tx_count = 0;
}

static void gl_event(void *opaque, QEMUChrEvent event)
{
    OEMGLService *s = opaque;
    if (event == CHR_EVENT_OPENED) {
        gl_drain(s);
    } else if (event == CHR_EVENT_CLOSED) {
        gl_clear_tx(s);
        s->gl_rx_count = 0;
        s->gl_locked = false;
    }
}

static int gl_can_receive(void *opaque)
{
    OEMGLService *s = opaque;
    return GL_BULK_BYTES - s->gl_rx_count;
}
static void gl_receive(void *opaque, const uint8_t *buf, int size)
{
    OEMGLService *s = opaque;
    if (size < 0 || (unsigned)size > GL_BULK_BYTES - s->gl_rx_count) {
        error_report("glbridge: RX length exceeds available capacity");return;
    }
    memcpy(s->gl_rx + s->gl_rx_count, buf, size);
    s->gl_rx_count += size;
}
static uint64_t gl_read(void *opaque, hwaddr addr, unsigned size)
{
    OEMGLService *s = opaque;
    if (addr == 40) { return GL_TX_BYTES - s->gl_tx_count; }
    if (addr == 44) { return qemu_chr_fe_backend_open(&s->gl_chr); }
    if (addr == 48) { return 2; }
    if (addr == 0) {
        return 0x474c4252;
    }
    if (addr == 16) {
        /* Apply backpressure before accepting a record, without sleeping
         * under the BQL. The existing guest lock retry yields its CPU. */
        bool held = s->gl_locked || s->gl_tx_count > GL_TX_BYTES - GL_MAX_RECORD;
        if (!held) {
            s->gl_locked = true;
        }
        return held;
    }
    if (addr == 8) {
        return MIN(s->gl_rx_count, 4096);
    }
    if (addr == 24) {
        return GL_BULK_BYTES;
    }
    if (addr == 32) {
        return s->gl_rx_count;
    }
    if (addr >= 0x2000 && addr - 0x2000 + size <= s->gl_rx_count) {
        uint64_t value = 0;
        for (unsigned i = 0; i < size; i++) {
            value |= (uint64_t)s->gl_rx[addr - 0x2000 + i] << (8 * i);
        }
        return value;
    }
    if (addr == 12 && s->gl_rx_count) {
        uint8_t value = s->gl_rx[0];
        memmove(s->gl_rx, s->gl_rx + 1, --s->gl_rx_count);
        qemu_chr_fe_accept_input(&s->gl_chr);
        return value;
    }
    return 0;
}
static void gl_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    OEMGLService *s = opaque;
    if (addr == 16) {
        s->gl_locked = false;
    } else if (((addr == 20 && value <= 4096) || addr == 36) &&
               value <= s->gl_rx_count) {
        s->gl_rx_count -= value;
        memmove(s->gl_rx, s->gl_rx + value, s->gl_rx_count);
        qemu_chr_fe_accept_input(&s->gl_chr);
    } else if (addr >= 0x1000 && addr + size <= 0x2000) {
        for (unsigned i = 0; i < size; i++) {
            s->gl_tx[addr - 0x1000 + i] = value >> (i * 8);
        }
    } else if (addr == 4 && value <= sizeof(s->gl_tx)) {
        gl_queue(s, s->gl_tx, value);
    } else if (addr == 28 && value <= GL_BULK_BYTES) {
        gl_queue(s, s->gl_bulk_tx, value);
    }
}
static const MemoryRegionOps gl_ops = {
    .read = gl_read, .write = gl_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
};


static void auto9_gl_bridge_reset(void *opaque)
{
 OEMGLService *s=opaque;s->gl_locked=false;s->gl_rx_count=0;gl_clear_tx(s);
}
static void auto9_gl_bridge_init(MachineState *machine)
{
 Chardev *chr=qemu_chr_find("glbridge");if(!chr)return;
 OEMGLService *s=g_new0(OEMGLService,1);
 memory_region_init_ram(&s->gl_bulk,NULL,"oem8704-private-gl-bulk",2*GL_BULK_BYTES,&error_fatal);
 memory_region_add_subregion_overlap(get_system_memory(),0xb7fd00000ULL,&s->gl_bulk,1);
 s->gl_bulk_tx=memory_region_get_ram_ptr(&s->gl_bulk);s->gl_rx=s->gl_bulk_tx+GL_BULK_BYTES;
 qemu_chr_fe_init(&s->gl_chr,chr,&error_fatal);
 qemu_chr_fe_set_handlers(&s->gl_chr,gl_can_receive,gl_receive,gl_event,NULL,s,NULL,true);
 memory_region_init_io(&s->gl_mmio,OBJECT(machine),&gl_ops,s,"oem8704-private-gl-control",0x3000);
 memory_region_add_subregion_overlap(get_system_memory(),0xb7fc00000ULL,&s->gl_mmio,1);
 qemu_register_reset(auto9_gl_bridge_reset,s);
}
