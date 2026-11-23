/* SPDX-License-Identifier: GPL-2.0-or-later
 * Bounded SSS DMA operations; never substitute a digest/authentication result.
 * AES/SHA256 control fields: Linux drivers/crypto/s5p-sss.c.
 * Auto9 key/IV/output offsets: unchanged EPBL sub_244/sub_85C.
 * Mode 0x19 => HMAC384 is an UNVERIFIED mapping, explicitly opt-in only.
 * No BootROM contents, fused key, signature or TEE success is synthesized.
 */
#include "crypto/hash.h"
#include "crypto/aes.h"
#define TYPE_EXYNOS_AUTO9_SSS "exynos-auto9.sss"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Sss, EXYNOS_AUTO9_SSS)
#define AUTO9_SSS_MMIO 0x2000
#define AUTO9_SSS_SRAM_BYTES 0x400
#define AUTO9_SSS_MAX_DMA (1024 * 1024)
struct ExynosAuto9Sss {
    SysBusDevice parent_obj;
    MemoryRegion mmio, sram;
    MemoryRegion *dma_root;
    AddressSpace dma_as;
    uint32_t words[AUTO9_SSS_MMIO / 4];
    uint32_t starts, status_acks, length_writes, rejected;
    uint32_t hash_jobs, aes_jobs;
    bool experimental_mode19;
};
static bool auto9_sss_ram(ExynosAuto9Sss *s, uint64_t pa, uint32_t len, bool write)
{
    MemoryRegionSection sec;
    bool ok;
    if (!len || len > AUTO9_SSS_MAX_DMA || pa > UINT64_MAX - len) {
        return false;
    }
    sec = memory_region_find(s->dma_root, pa, len);
    if (!sec.mr) {
        return false;
    }
    ok = sec.offset_within_address_space == pa &&
         int128_eq(sec.size, int128_make64(len)) &&
         memory_region_is_ram(sec.mr) && !memory_region_is_ram_device(sec.mr) &&
         (!write || !sec.readonly);
    if (!ok) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 SSS RAM preflight pa=0x%" PRIx64 " len=%u region=%s base=0x%" PRIx64 " size=0x%" PRIx64 " ram=%d device=%d readonly=%d\n",
            pa, len, memory_region_name(sec.mr), sec.offset_within_address_space,
            int128_get64(sec.size), memory_region_is_ram(sec.mr),
            memory_region_is_ram_device(sec.mr), sec.readonly);
    }
    memory_region_unref(sec.mr);
    return ok;
}
static bool auto9_sss_read_ram(ExynosAuto9Sss *s, uint64_t pa, uint8_t *buf, uint32_t len)
{
    MemTxAttrs attrs = { .secure = true };
    return auto9_sss_ram(s, pa, len, false) &&
        address_space_read(&s->dma_as, pa, attrs, buf, len) == MEMTX_OK;
}
static void auto9_sss_words(ExynosAuto9Sss *s, uint32_t off,
                            uint8_t *out, size_t len)
{
    size_t i;
    for (i = 0; i < len; i += 4) {
        stl_le_p(out + i, s->words[(off + i) / 4]);
    }
}
static void auto9_sss_digest(ExynosAuto9Sss *s, const uint8_t *buf, size_t len)
{
    size_t i;
    memset(&s->words[0x11c0 / 4], 0, 64);
    for (i = 0; i < len; i += 4) {
        s->words[(0x11c0 + i) / 4] = ldl_le_p(buf + i);
    }
}
static bool auto9_sss_hash(ExynosAuto9Sss *s, const uint8_t *buf, size_t len)
{
    uint32_t ctrl = s->words[0x1000 / 4];
    uint8_t result[48], *p = result;
    size_t size;
    Error *err = NULL;
    int rc;
    /* Only complete, single messages with the observed natural-byte swap. */
    if (s->words[0x100c / 4] != 15 ||
        s->words[0x1020 / 4] != len || s->words[0x1024 / 4] ||
        s->words[0x1030 / 4] || s->words[0x1034 / 4]) {
        return false;
    }
    if (ctrl == 0x14) {
        size = 32;
        rc = qcrypto_hash_bytes(QCRYPTO_HASH_ALGO_SHA256, buf, len,
                                &p, &size, &err);
    } else if (ctrl == 0x19 && s->experimental_mode19) {
        /* RFC2104 HMAC, SHA384 block=128. Full hardware key block is used. */
        uint8_t key[128], ipad[128], opad[128], inner[48], *ip = inner;
        struct iovec iov[2];
        size_t inner_size = sizeof(inner), i;
        auto9_sss_words(s, 0x1100, key, sizeof(key));
        for (i = 0; i < sizeof(key); i++) {
            ipad[i] = key[i] ^ 0x36;
            opad[i] = key[i] ^ 0x5c;
        }
        iov[0] = (struct iovec){ .iov_base = ipad, .iov_len = sizeof(ipad) };
        iov[1] = (struct iovec){ .iov_base = (void *)buf, .iov_len = len };
        rc = qcrypto_hash_bytesv(QCRYPTO_HASH_ALGO_SHA384, iov, 2,
                                 &ip, &inner_size, &err);
        size = sizeof(result);
        if (!rc) {
            iov[0] = (struct iovec){ .iov_base = opad, .iov_len = sizeof(opad) };
            iov[1] = (struct iovec){ .iov_base = inner, .iov_len = sizeof(inner) };
            rc = qcrypto_hash_bytesv(QCRYPTO_HASH_ALGO_SHA384, iov, 2,
                                     &p, &size, &err);
        }
        memset(key, 0, sizeof(key));
        memset(ipad, 0, sizeof(ipad));
        memset(opad, 0, sizeof(opad));
        memset(inner, 0, sizeof(inner));
    } else {
        return false;
    }
    if (rc) {
        error_free(err);
        return false;
    }
    auto9_sss_digest(s, result, size);
    memset(result, 0, sizeof(result));
    s->hash_jobs++;
    return true;
}
static bool auto9_sss_hash_dma(ExynosAuto9Sss *s, uint32_t len)
{
    uint64_t src = ((uint64_t)s->words[0x04c / 4] << 32) |
                   s->words[0x040 / 4];
    g_autofree uint8_t *buf = NULL;
    if ((s->words[0x014 / 4] & 3) || !auto9_sss_ram(s, src, len, false)) {
        return false;
    }
    buf = g_malloc(len);
    return auto9_sss_read_ram(s, src, buf, len) && auto9_sss_hash(s, buf, len);
}
static bool auto9_sss_aes_dma(ExynosAuto9Sss *s, uint32_t len)
{
    uint64_t src = ((uint64_t)s->words[0x02c / 4] << 32) | s->words[0x020 / 4];
    uint64_t dst = ((uint64_t)s->words[0x03c / 4] << 32) | s->words[0x030 / 4];
    uint32_t ctrl = s->words[0x400 / 4], route = s->words[0x014 / 4] & 3;
    unsigned keymode = (ctrl >> 4) & 3, keylen = 16 + keymode * 8;
    AES_KEY schedule;
    MemTxAttrs attrs = { .secure = true };
    g_autofree uint8_t *in = NULL, *out = NULL;
    uint8_t key[32], iv[16];
    unsigned offset, j;
    int rc;
    /* Exact EPBL natural-byte CBC control family; no guessed mode fallback. */
    if (keymode > 2 || (ctrl & ~0x31u) != 0xf8a ||
        len % 16 || s->words[0x024 / 4] != len ||
        (route != 0 && route != 2) ||
        !auto9_sss_ram(s, src, len, false) || !auto9_sss_ram(s, dst, len, true) ||
        (src != dst && src < dst + len && dst < src + len)) {
        return false;
    }
    /* Validate fused HASH parameters before any guest destination write. */
    if (route == 2 && (s->words[0x1000 / 4] != 0x14 ||
        s->words[0x100c / 4] != 15 || s->words[0x1020 / 4] != len ||
        s->words[0x1024 / 4] || s->words[0x1030 / 4] || s->words[0x1034 / 4])) {
        return false;
    }
    auto9_sss_words(s, 0x480 + 32 - keylen, key, keylen);
    auto9_sss_words(s, 0x430, iv, sizeof(iv));
    /* This build's high-level cipher backend is a stub. Reuse QEMU's own
     * low-level AES (already linked for ARM), without adding a dependency. */
    rc = (ctrl & 1) ? AES_set_decrypt_key(key, keylen * 8, &schedule) :
                      AES_set_encrypt_key(key, keylen * 8, &schedule);
    memset(key, 0, sizeof(key));
    if (rc) {
        return false;
    }
    in = g_malloc(len);
    out = g_malloc(len);
    if (auto9_sss_read_ram(s, src, in, len)) {
        for (offset = 0; offset < len; offset += 16) {
            if (ctrl & 1) {
                AES_decrypt(in + offset, out + offset, &schedule);
                for (j = 0; j < 16; j++) { out[offset + j] ^= iv[j]; }
                memcpy(iv, in + offset, 16);
            } else {
                for (j = 0; j < 16; j++) { out[offset + j] = in[offset + j] ^ iv[j]; }
                AES_encrypt(out + offset, out + offset, &schedule);
                memcpy(iv, out + offset, 16);
            }
        }
        rc = 0;
    } else {
        rc = -1;
    }
    memset(&schedule, 0, sizeof(schedule));
    memset(iv, 0, sizeof(iv));
    if (!rc && route == 2 && !auto9_sss_hash(s, out, len)) {
        rc = -1;
    }
    if (!rc) {
        rc = address_space_write(&s->dma_as, dst, attrs, out, len) == MEMTX_OK ? 0 : -1;
    }
    memset(in, 0, len);
    memset(out, 0, len);
    if (rc) {
        return false;
    }
    s->aes_jobs++;
    return true;
}
static MemTxResult exynos_auto9_sss_read(void *opaque, hwaddr off,
    uint64_t *value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Sss *s = opaque;
    if (off >= AUTO9_SSS_MMIO || (off & 3) || size != 4) {
        return MEMTX_DECODE_ERROR;
    }
    *value = s->words[off / 4];
    return MEMTX_OK;
}
static MemTxResult exynos_auto9_sss_write(void *opaque, hwaddr off,
    uint64_t value, unsigned size, MemTxAttrs attrs)
{
    ExynosAuto9Sss *s = opaque;
    uint32_t word = value;
    bool ok;
    if (off >= AUTO9_SSS_MMIO || (off & 3) || size != 4) {
        return MEMTX_DECODE_ERROR;
    }
    if (off == 0x00c || off == 0x1010) {
        s->words[off / 4] &= ~word;
        s->status_acks++;
        return MEMTX_OK;
    }
    s->words[off / 4] = word;
    if (off == 0x044 || off == 0x034) {
        uint32_t bit = off == 0x044 ? 2 : 4;
        s->length_writes++;
        s->words[0x00c / 4] &= ~(off == 0x044 ? 2 : 12);
        s->words[0x1010 / 4] &= ~0x40;
        ok = off == 0x044 ? auto9_sss_hash_dma(s, word) : auto9_sss_aes_dma(s, word);
        if (ok) {
            s->words[0x00c / 4] |= off == 0x044 ? bit : 12;
            if (off == 0x044 || (s->words[0x014 / 4] & 3) == 2) {
                s->words[0x1010 / 4] |= 0x40;
            }
            s->starts++;
        } else {
            s->rejected++;
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 SSS unsupported/invalid DMA off=0x%x len=%u; no completion\n",
                (unsigned)off, word);
        }
    }
    return MEMTX_OK;
}
static const MemoryRegionOps exynos_auto9_sss_ops = {
    .read_with_attrs = exynos_auto9_sss_read,
    .write_with_attrs = exynos_auto9_sss_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};
static void exynos_auto9_sss_reset(DeviceState *dev)
{
    ExynosAuto9Sss *s = EXYNOS_AUTO9_SSS(dev);
    memset(s->words, 0, sizeof(s->words));
    s->starts = s->status_acks = s->length_writes = s->rejected = 0;
    s->hash_jobs = s->aes_jobs = 0;
}
static const Property auto9_sss_properties[] = {
    DEFINE_PROP_BOOL("x-unverified-mode19-hmac384", ExynosAuto9Sss, experimental_mode19, false),
};
static void exynos_auto9_sss_init(Object *obj)
{
    ExynosAuto9Sss *s = EXYNOS_AUTO9_SSS(obj);
    memory_region_init_io(&s->mmio, obj, &exynos_auto9_sss_ops, s, TYPE_EXYNOS_AUTO9_SSS, AUTO9_SSS_MMIO);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    memory_region_init_ram(&s->sram, obj, "auto9-epbl-hash-src", AUTO9_SSS_SRAM_BYTES, &error_fatal);
}
static void exynos_auto9_sss_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, exynos_auto9_sss_reset);
    device_class_set_props(dc, auto9_sss_properties);
}
static void exynos_auto9_sss_finalize(Object *obj)
{
    ExynosAuto9Sss *s = EXYNOS_AUTO9_SSS(obj);
    if (s->dma_root) {
        address_space_destroy(&s->dma_as);
    }
}
static const TypeInfo exynos_auto9_sss_type = {
    .name = TYPE_EXYNOS_AUTO9_SSS, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Sss),
    .instance_init = exynos_auto9_sss_init,
    .instance_finalize = exynos_auto9_sss_finalize,
    .class_init = exynos_auto9_sss_class_init,
};
static void exynos_auto9_sss_register(void) { type_register_static(&exynos_auto9_sss_type); }
type_init(exynos_auto9_sss_register)
static void exynos_auto9_sss_create(VirtMachineState *vms)
{
    DeviceState *dev;
    ExynosAuto9Sss *s;
    if (!vms->exynos_vlm) { return; }
    dev = qdev_new(TYPE_EXYNOS_AUTO9_SSS);
    object_property_add_child(OBJECT(vms), "exynos-sss", OBJECT(dev));
    s = EXYNOS_AUTO9_SSS(dev);
    /* SSS is the secure boot crypto master. Preflight and transfer must use
     * the SAME view, including the existing phase-local EPBL SRAM. This does
     * not add/remap RAM, provide ROM bytes or change CPU access permissions. */
    s->dma_root = vms->secure_sysmem ? vms->secure_sysmem : get_system_memory();
    address_space_init(&s->dma_as, s->dma_root, "auto9-sss-secure-dma");
    object_property_add_uint32_ptr(OBJECT(dev), "starts", &s->starts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "status-acks", &s->status_acks, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "length-writes", &s->length_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "hash-jobs", &s->hash_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(OBJECT(dev), "aes-jobs", &s->aes_jobs, OBJ_PROP_FLAG_READ);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x1bf50000);
    memory_region_add_subregion(get_system_memory(), 0x1fc00, &s->sram);
}
