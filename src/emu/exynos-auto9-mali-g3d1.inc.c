/* SPDX-License-Identifier: GPL-2.0-or-later
 * Explicit virtual G76 bootstrap for distinct G3D1 and QNX G3D00 apertures.
 * Optional feature discovery describes a front end with no execution resources.
 * Optional QNX jobs use AS0, with bounded vertex and tiler profiles.
 * Optional VM3 G3D1 control jobs have a separate, VM3-only AS0.
 */
#define TYPE_AUTO9_MALI_G3D1 "exynos-auto9.mali-g3d1"
OBJECT_DECLARE_SIMPLE_TYPE(Auto9MaliG3d1, AUTO9_MALI_G3D1)
#define AUTO9_G3D1_RESET_NS 1000
#define AUTO9_G3D1_RESET_DONE BIT(8)
#define AUTO9_G3D1_CACHE_DONE BIT(17)
#define AUTO9_G3D1_CACHE_NS 1000
#define AUTO9_G3D00_CYCLE_PERIOD_NS 10 /* Virtual 100 MHz, not a silicon rate. */
#define AUTO9_G3D1_JM_SLOTS 3
#define AUTO9_G3D00_AS0_BASE 0x2400
#define AUTO9_G3D00_AS_STRIDE 0x40
enum { AS0_TTBR, AS0_MEMATTR, AS0_LOCKADDR, AS0_TRANSCFG, AS0_PAIRS };
enum {
    AS0_NOP, AS0_UPDATE, AS0_LOCK, AS0_UNLOCK, AS0_FLUSH_PT, AS0_FLUSH_MEM,
};
enum { G3D1_GPU, G3D1_JOB, G3D1_MMU, G3D1_IRQ_COUNT };

#define AUTO9_G3D00_GPU_PAGE 4096ULL
#define AUTO9_G3D00_PA_MASK UINT64_C(0x000000fffffff000)
#define AUTO9_G3D00_PA_FIELD UINT64_C(0x000ffffffffff000)
/* Original VM2 ordinary RAM above the debug snapshot reservation. */
#define AUTO9_G3D00_SCREEN_BANK_START UINT64_C(0xe0210000)
#define AUTO9_G3D00_SCREEN_BANK_END UINT64_C(0xe3c00000)
#define AUTO9_G3D00_JS_BASE 0x1800
#define AUTO9_G3D00_JS_STRIDE 0x80
#define AUTO9_G3D00_CHAIN_MAX 64
#define AUTO9_G3D00_VERTEX_SAMPLES 8
#define AUTO9_G3D00_TILE_BATCHES 16
#define AUTO9_VM3_TILE_BATCHES AUTO9_G3D00_CHAIN_MAX
#define AUTO9_G3D00_TILE_VERTICES 96
#define AUTO9_VM3_CENTER_TARGETS 16
#define AUTO9_VM3_PF_WRITERS 16
#define AUTO9_VM3_PF_HEADER_PAGES 32
#define AUTO9_VM3_ATLAS_HEADER_PAGES 64
#define AUTO9_VM3_ATLAS_MAX_PAGES 4160
#define AUTO9_VM3_ATLAS_SLOTS 4

enum {
    G3D00_JOB_DONE = 0x01,
    G3D00_JOB_CONFIG_FAULT = 0x40,
    G3D00_JOB_READ_FAULT = 0x42,
    G3D00_JOB_WRITE_FAULT = 0x43,
};

typedef struct Auto9G3d00JobSlot {
    uint32_t head_next[2], affinity_next[2], config_next, flush_id_next;
    uint64_t head;
    uint32_t status;
    uint64_t queued_head, queued_ttbr, queued_transcfg, queued_memattr;
    uint32_t queued_config;
    bool queued;
} Auto9G3d00JobSlot;

typedef struct Auto9G3d00Triangle {
    int32_t xy[3][2];
    float z[3], inv_w[3];
    uint16_t rgba[3][4], tile[4]; /* Inclusive [minX,minY,maxX,maxY]. */
    uint16_t uv[3][2];           /* Stock VM3 RG16F, not a QNX color. */
    float uv32[3][2];            /* VM3 interleaved RG32F + RGBA16F. */
    float uv2[3][2];             /* Second VM3 RG32F varying, when present. */
    uint16_t pixel[4];            /* Clipped conservative pixel bounds. */
} Auto9G3d00Triangle;

typedef struct Auto9G3d00TileBatch {
    uint64_t ttbr, context, renderer;
    uint64_t textures, samplers, push_uniforms;
    uint16_t width, height, count, index_count;
    bool color_fan, color_clipped, interleaved_fan, atlas_strip, atlas_constant, indexed;
    bool dual_uv, radial_fan, triangle_list, store_point, tagged_interleaved;
    uint16_t point_x, point_y;
    /* VM3's 150-vertex list has 50 primitives before clipping. */
    Auto9G3d00Triangle triangle[96];
} Auto9G3d00TileBatch;

typedef struct Auto9Vm3CenterTarget {
    char footprint[65], content[65];
    uint64_t generation, color_generation;
    int64_t time_ns, color_time_ns;
    uint32_t pitch, bytes, rgb;
    bool clear;
} Auto9Vm3CenterTarget;

typedef struct Auto9Vm3AfbcProducer {
    uint64_t pages[AUTO9_VM3_ATLAS_HEADER_PAGES];
    uint32_t sequence, format, modes, flags, shape, target;
    bool tiler;
} Auto9Vm3AfbcProducer;

/* Bounded internal texture storage for pinned stock atlas jobs. It is not
 * an AFBC bitstream or a guest scanout buffer. */
typedef struct Auto9Vm3AtlasImage {
    uint64_t ttbr, va, preload_va, generation;
    unsigned pages;
    uint64_t backing[AUTO9_VM3_ATLAS_MAX_PAGES];
    uint8_t *backing_bytes;
    uint32_t *rgba;
} Auto9Vm3AtlasImage;

typedef struct Auto9Vm3FanOrigin {
    uint64_t ttbr, pa[4][2];
    uint32_t job;
    uint8_t position[64], rgba[32];
} Auto9Vm3FanOrigin;

struct Auto9MaliG3d1 {
    SysBusDevice parent_obj;
    MemoryRegion regs;
    uint32_t gpu_id, id_reads, rejected, last_rejected, last_rejected_write;
    uint32_t last_rejected_value;
    uint32_t feature_reads, feature_last_offset;
    bool discovery, reset_irq, config_enabled, empty_power, g3d00, jm_topology;
    uint32_t jm_probe_reads;
    uint32_t as_present, as0_updates, as0_locks, as0_flushes, as0_locked;
    uint64_t as0_staged[AS0_PAIRS];
    uint64_t as0_active_ttbr, as0_active_memattr, as0_active_transcfg;
    uint64_t as0_lock_region;
    uint32_t control_jobs, job_starts, job_completions, job_failures;
    /* Descriptor attempts, not JS submissions or distinct guest indexes. */
    uint64_t vm3_desc_attempts, vm3_desc_completed, vm3_desc_failed;
    uint32_t vm3_control, vm3_control_accesses, vm3_control_denied;
    uint32_t vm3_foreign_job_irq_clear, vm3_foreign_mmu_irq_clear;
    uint32_t vm4_irq_mask[2], vm4_job_mask_writes, vm4_mmu_mask_writes;
    uint32_t vm3_oem_transcfg_updates, vm3_translation_mode_rejections;
    uint32_t vm3_fragment_attempts, vm3_fragment_decoded, vm3_fragment_tiler_rejections;
    uint32_t vm3_empty_fragment_failures, vm3_empty_fragment_profiles, vm3_empty_discard_validated;
    uint32_t vm3_preframe_attempts;
    uint32_t vm3_pf_producers_seen, vm3_pf_producers_mapped;
    Auto9Vm3AfbcProducer vm3_pf_producers[AUTO9_VM3_PF_WRITERS];
    Auto9Vm3AtlasImage *vm3_atlas[AUTO9_VM3_ATLAS_SLOTS];
    uint64_t vm3_atlas_generation;
    uint32_t vm3_empty_failure_profiles[8][22];
    uint32_t vm3_zero_resolves, vm3_single_zero_clears, vm3_geometry_seen, vm3_geometry_samples;
    uint32_t vm3_unresolved_dependencies;
    uint32_t vm3_stock_fans, vm3_stock_fan_primitives, vm3_fragment_matched_batches;
    uint32_t vm3_color_fans, vm3_color_resolves;
    uint32_t vm3_color_clipped_fans;
    uint32_t vm3_faulted_empty_batches, vm3_reset_tile_batches;
    uint32_t vm3_interleaved_fans, vm3_interleaved_fragment_samples;
    uint32_t vm3_indexed_draw_profiles, vm3_indexed_tagged_traces;
    bool vm3_dual_uv_sampled, vm3_triangle_list_sampled;
    unsigned vm3_short_lists_sampled, vm3_tagged_tiler_traces;
    uint32_t vm3_interleaved_resolves, vm3_atlas_strips, vm3_atlas_fragment_samples;
    uint32_t vm3_atlas_always_samples;
    uint32_t vm3_indexed_samples, vm3_indexed_layout_samples;
    bool vm3_indexed42_sampled;
    uint64_t vm3_color_pixels;
    uint32_t vm3_textured_resolves, vm3_textured_attempts;
    uint32_t vm3_textured_state_fault_samples, vm3_clipped_afbc_samples;
    uint64_t vm3_textured_bytes, vm3_textured_nonzero_pixels, vm3_textured_shaded_pixels;
    uint32_t vm3_texture_loaded, vm3_texture_zero_opacity;
    uint64_t vm3_texture_rgb_texels, vm3_texture_alpha_texels, vm3_texture_rgb_samples;
    uint32_t vm3_nearest_texture_loads, vm3_single_tile_zero_pitch_loads;
    uint64_t vm3_nearest_rgb_texels;
    uint32_t vm3_linear_rgba_loads, vm3_linear_reverse_loads;
    uint32_t vm3_center_reads_traced, vm3_center_rt_probes;
    uint32_t vm3_chain_fault_traces, vm3_failed_vertex_samples, vm3_vertex3_layout_samples;
    uint32_t vm3_vertex4_layout_samples, vm3_vertex4_program_attempts;
    uint32_t vm3_vertex448_layout_samples, vm3_tiler_stop_samples, vm3_new_tiler_stops;
    uint32_t vm3_vertex336_layout_samples, vm3_vertex336_stage, vm3_vertex336_tagged_jobs;
    uint32_t vm3_compute_samples, vm3_compute_programs, vm3_fan_origin_samples;
    uint32_t vm3_compute_jobs, vm3_points_binned, vm3_point_fragment_jobs, vm3_point_mismatches;
    char vm3_compute_prefixes[4][65];
    Auto9Vm3FanOrigin vm3_fan_origins[8];
    bool vm3_vertex4_program_captured, vm3_vertex45_program_captured;
    uint32_t vm3_vertex45_layout_samples, vm3_vertex45_program_attempts, vm3_vertex45_jobs;
    uint32_t vm3_vertex608_tagged_traces, vm3_vertex592_pos44_traces;
    char vm3_failed_vertex_fingerprints[4][65];
    uint64_t vm3_linear_rgba_rgb_texels;
    Auto9Vm3CenterTarget vm3_center_target[AUTO9_VM3_CENTER_TARGETS];
    uint64_t vm3_center_generation, vm3_center_evictions;
    unsigned vm3_center_depth_observed;
    uint32_t vm3_js_state, vm3_next_queued, vm3_next_dispatched, vm3_next_canceled, vm3_next_as_mismatch;
    uint64_t vm3_crc_invalidated_bytes;
    uint32_t vm3_vertex_attempts, vm3_vertex_samples;
    uint32_t vm3_stock_vertices_written, vm3_vertex3_jobs, vm3_vertex4_jobs, vm3_vertex448_jobs;
    char vm3_vertex_fingerprints[4][65];
    uint32_t job_descriptors_completed, job_cache_flushes, vertex_samples;
    uint32_t vertex_descriptors_completed, vertices_written;
    uint32_t tiler_sample_attempts, fragment_sample_attempts, fragment_fault_samples;
    uint64_t fragment_fault_signature[4];
    uint32_t tiler_descriptors_completed, primitives_binned;
    uint32_t fragment_descriptors_completed, fragment_pixels_shaded;
    uint32_t fragment_te_unconditional;
    uint64_t fragment_bytes_written;
    Auto9G3d00TileBatch tile_batch[AUTO9_VM3_TILE_BATCHES];
    unsigned tile_batch_count;
    uint32_t vertex_sample_attempts;
    uint32_t qnx_chain_fault_samples, qnx_vertex_fault_samples;
    unsigned qnx_vertex_first_stages;
    uint64_t qnx_screen_leaf_reads, qnx_screen_leaf_writes;
    uint64_t qnx_viewport_cross_page_jobs;
    uint32_t last_job_type, latest_flush_id, latest_flush_reads;
    Auto9G3d00JobSlot slot[AUTO9_G3D1_JM_SLOTS];
    uint32_t power_reads, power_writes;
    /* JM, shader, tiler, L2/MMU configuration and no-coherency selector. */
    uint32_t config[4], coherency, config_reads, config_writes;
    QEMUTimer *reset_timer, *cache_timer;
    qemu_irq irq[G3D1_IRQ_COUNT];
    uint32_t irq_raw[G3D1_IRQ_COUNT], irq_mask[G3D1_IRQ_COUNT];
    uint32_t irq_reads, irq_writes, reset_commands, reset_completions;
    uint32_t gpu_irq_level;
    uint32_t cache_commands, cache_completions;
    uint32_t cycle_running, cycle_starts, cycle_stops, cycle_reads;
    uint64_t cycle_epoch_ns, cycle_elapsed_ns;
};

static MemTxResult auto9_mali_g3d1_reject(Auto9MaliG3d1 *s, hwaddr off, bool write)
{
    s->rejected++;
    s->last_rejected = off;
    s->last_rejected_write = write;
    qemu_log_mask(LOG_UNIMP, "Auto9 %s unsupported %s 0x%" HWADDR_PRIx "\n",
                  s->g3d00 ? "G3D00" : "G3D1", write ? "write" : "read", off);
    return MEMTX_DECODE_ERROR;
}

static void auto9_mali_g3d1_irq_update(Auto9MaliG3d1 *s)
{
    for (unsigned i = 0; i < G3D1_IRQ_COUNT; i++) {
        bool level = (s->irq_raw[i] & s->irq_mask[i]) != 0;
        qemu_set_irq(s->irq[i], level);
        if (i == G3D1_GPU) {
            s->gpu_irq_level = level;
        }
    }
}

static void auto9_mali_g3d1_config_reset(Auto9MaliG3d1 *s)
{
    memset(s->config, 0, sizeof(s->config));
    s->coherency = 31; /* No fabric coherency engine in this profile. */
}

static bool auto9_mali_g3d1_config_access(Auto9MaliG3d1 *s, hwaddr off,
                                         uint64_t *value, bool write)
{
    uint32_t *latch;

    if (off == 0x304) {
        if (write && *value != 31) {
            return false; /* Only the coherency mode advertised by GPU_FEATURES. */
        }
        latch = &s->coherency;
    } else if (off >= 0xf00 && off <= 0xf0c && !(off & 3)) {
        /* Preserve implementation-defined bits; no execution side effects. */
        latch = &s->config[(off - 0xf00) / 4];
    } else {
        return false;
    }
    if (write) {
        *latch = *value;
        s->config_writes++;
    } else {
        *value = *latch;
        s->config_reads++;
    }
    return true;
}

/* READY, TRANSITION and ACTIVE are empty when all PRESENT masks are zero.
 * Zero ON/OFF requests are no-ops; nonzero requests cannot create resources. */
static bool auto9_mali_g3d1_empty_power(Auto9MaliG3d1 *s, hwaddr off,
                                       uint64_t *value, bool write)
{
    switch (off) {
    case 0x140: case 0x144: case 0x150: case 0x154: case 0x160: case 0x164:
    case 0x200: case 0x204: case 0x210: case 0x214: case 0x220: case 0x224:
    case 0x240: case 0x244: case 0x250: case 0x254: case 0x260: case 0x264:
    case 0xe10: case 0xe14: case 0xe40: case 0xe44:
        if (write) {
            return false;
        }
        *value = 0;
        s->power_reads++;
        return true;
    case 0x180: case 0x184: case 0x190: case 0x194: case 0x1a0: case 0x1a4:
    case 0x1c0: case 0x1c4: case 0x1d0: case 0x1d4: case 0x1e0: case 0x1e4:
    case 0xe20: case 0xe24: case 0xe30: case 0xe34:
        if (!write || *value != 0) {
            return false;
        }
        s->power_writes++;
        return true;
    default:
        return false;
    }
}

static uint64_t auto9_g3d00_cycle_elapsed(const Auto9MaliG3d1 *s)
{
    uint64_t elapsed = s->cycle_elapsed_ns;
    if (s->cycle_running) {
        elapsed += qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - s->cycle_epoch_ns;
    }
    return elapsed;
}

static void auto9_mali_cycle_reset(Auto9MaliG3d1 *s)
{
    s->cycle_running = 0;
    s->cycle_epoch_ns = s->cycle_elapsed_ns = 0;
}

static void auto9_vm3_atlas_drop(Auto9Vm3AtlasImage **slot)
{
    Auto9Vm3AtlasImage *image = *slot;
    if (image) {
        g_free(image->rgba);
        g_free(image->backing_bytes);
        g_free(image);
        *slot = NULL;
    }
}

/* Slots 0/2 hold short allocations; slots 1/3 hold full allocations. */
static unsigned auto9_vm3_atlas_count(Auto9MaliG3d1 *s, unsigned height)
{
    unsigned count = 0;
    for (unsigned i = height == 2048; i < G_N_ELEMENTS(s->vm3_atlas); i += 2) {
        count += s->vm3_atlas[i] != NULL;
    }
    return count;
}

static void auto9_vm3_atlas_invalidate(Auto9MaliG3d1 *s, uint64_t ttbr,
                                     uint64_t lock, unsigned flush, bool scoped)
{
    unsigned shift = (lock & 63) + 1;
    uint64_t size = shift < 48 && shift >= 12 ? UINT64_C(1) << shift : 0;
    uint64_t start = size ? lock & ~(size - 1) : 0;
    for (unsigned i = 0; i < G_N_ELEMENTS(s->vm3_atlas); i++) {
        Auto9Vm3AtlasImage *img = s->vm3_atlas[i];
        if (img && (!scoped || (img->ttbr == ttbr &&
            (!size || (start < img->va + img->pages * 4096ULL &&
                       img->va < start + size) ||
                      (start < img->preload_va + img->pages * 4096ULL &&
                       img->preload_va < start + size))))) {
            if (scoped) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 private atlas invalidated: height=%u generation=%" PRIu64
                    " flush=%u region=%" PRIu64 "\n",
                    (i & 1) ? 2048 : 1024, img->generation, flush, size);
            }
            auto9_vm3_atlas_drop(&s->vm3_atlas[i]);
        }
    }
}

static void auto9_g3d00_as0_reset(Auto9MaliG3d1 *s)
{
    /* A guest GPU reset resets MMU/slots, not external texture memory.
     * Atlas backing is revalidated in full after the next AS setup. */
    unsigned short_atlases = auto9_vm3_atlas_count(s, 1024);
    unsigned full_atlases = auto9_vm3_atlas_count(s, 2048);
    if (short_atlases || full_atlases) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 private atlas preserved across GPU reset: short=%u full=%u\n",
                      short_atlases, full_atlases);
    }
    memset(s->as0_staged, 0, sizeof(s->as0_staged));
    s->as0_active_ttbr = s->as0_active_memattr = s->as0_active_transcfg = 0;
    s->as0_locked = s->as0_lock_region = 0;
    memset(s->slot, 0, sizeof(s->slot));
    s->vm3_js_state = 0;
    s->latest_flush_id = 0;
}

static bool auto9_mali_transcfg_supported(bool vm3_control, uint64_t transcfg)
{
    /* The original VM3 kbase_mmu_hw_configure adds 0x42000000 to the
     * AArch64 4 KiB mode on this board's COHERENCY_NONE (0x1f) path.
     * Its policy bits do not change the geometry of this uncached walker. */
    return vm3_control ? transcfg == 0x42000006 :
                         (transcfg == 6 || transcfg == 0x02000006);
}

static bool auto9_g3d00_as0_access(Auto9MaliG3d1 *s, hwaddr off,
                                  uint64_t *value, bool write)
{
    unsigned pair, shift;
    uint64_t mask;

    if (!s->as_present || off < AUTO9_G3D00_AS0_BASE ||
        off >= AUTO9_G3D00_AS0_BASE + AUTO9_G3D00_AS_STRIDE || (off & 3)) {
        return false;
    }
    switch (off - AUTO9_G3D00_AS0_BASE) {
    case 0x00: case 0x04:
        pair = AS0_TTBR;
        break;
    case 0x08: case 0x0c:
        pair = AS0_MEMATTR;
        break;
    case 0x10: case 0x14:
        pair = AS0_LOCKADDR;
        break;
    case 0x30: case 0x34:
        pair = AS0_TRANSCFG;
        break;
    case 0x18:
        if (!write || timer_pending(s->reset_timer)) {
            return false;
        }
        switch (*value) {
        case AS0_NOP:
            return true;
        case AS0_UPDATE:
            /* OEM writes split words before UPDATE commits the context. */
            s->as0_active_ttbr = s->as0_staged[AS0_TTBR];
            s->as0_active_memattr = s->as0_staged[AS0_MEMATTR];
            s->as0_active_transcfg = s->as0_staged[AS0_TRANSCFG];
            s->as0_updates++;
            if (s->vm3_control && auto9_mali_transcfg_supported(true, s->as0_active_transcfg)) {
                s->vm3_oem_transcfg_updates++;
            }
            return true;
        case AS0_LOCK:
            s->as0_lock_region = s->as0_staged[AS0_LOCKADDR];
            s->as0_locked = 1;
            s->as0_locks++;
            return true;
        case AS0_UNLOCK:
        case AS0_FLUSH_PT:
        case AS0_FLUSH_MEM:
            if (s->vm3_control && *value != AS0_UNLOCK) {
                auto9_vm3_atlas_invalidate(s, s->as0_active_ttbr,
                    s->as0_locked ? s->as0_lock_region : 0, *value, true);
            }
            s->as0_locked = 0;
            s->as0_lock_region = 0;
            s->as0_flushes++;
            return true;
        default:
            return false;
        }
    case 0x1c: case 0x20: case 0x24: case 0x28: case 0x38: case 0x3c:
        if (write) {
            return false;
        }
        /* Control jobs are synchronous; failures terminate the job. */
        *value = 0;
        return true;
    default:
        return false;
    }
    shift = (off & 4) ? 32 : 0;
    if (write) {
        mask = UINT64_C(0xffffffff) << shift;
        s->as0_staged[pair] = (s->as0_staged[pair] & ~mask) |
                             ((uint64_t)(uint32_t)*value << shift);
    } else {
        *value = (uint32_t)(s->as0_staged[pair] >> shift);
    }
    return true;
}

/* Only exclusive original HCB banks for the owning VM; never shared RAM/MMIO. */
static bool auto9_mali_private_ram(Auto9MaliG3d1 *s, uint64_t pa,
                                   unsigned len, bool write, bool leaf)
{
    MemoryRegionSection sec;
    bool inside, ok, screen = false;

    if (!len || len > AUTO9_G3D00_GPU_PAGE) {
        return false;
    }
    if (s->g3d00) {
        /* Only allow leaf mappings here. The GPU page-table roots and
         * intermediate tables retain their original two-bank restriction. */
        screen = leaf && pa >= AUTO9_G3D00_SCREEN_BANK_START &&
                 pa <= AUTO9_G3D00_SCREEN_BANK_END - len;
        inside = (pa >= 0x90000000ULL && pa <= 0xa0000000ULL - len) ||
                 (pa >= 0x880000000ULL && pa <= 0x8ce000000ULL - len) ||
                 screen;
    } else if (s->vm3_control) {
        inside = (pa >= 0xb8000000ULL && pa <= 0xd0000000ULL - len) ||
                 (pa >= 0xe3c00000ULL && pa <= 0xe4c00000ULL - len) ||
                 (pa >= 0xa80000000ULL && pa <= 0xb80000000ULL - len);
    } else {
        return false;
    }
    if (!inside) {
        return false;
    }
    sec = memory_region_find(get_system_memory(), pa, len);
    if (!sec.mr) {
        return false;
    }
    ok = sec.offset_within_address_space == pa &&
         int128_eq(sec.size, int128_make64(len)) &&
         memory_region_is_ram(sec.mr) && !memory_region_is_ram_device(sec.mr) &&
         (!write || !sec.readonly);
    memory_region_unref(sec.mr);
    if (ok && screen) {
        if (write) {
            s->qnx_screen_leaf_writes++;
        } else {
            s->qnx_screen_leaf_reads++;
        }
    }
    return ok;
}

static bool auto9_g3d00_translate(Auto9MaliG3d1 *s, uint64_t va,
                                 unsigned len, bool write, uint64_t *pa)
{
    uint64_t table = s->as0_active_ttbr;

    /* OEM Bifrost mode 6, 48-bit VA, 40-bit PA, 4 KiB granule. Never fall
     * back to an identity mapping. All current callers fit in one page. */
    if (!auto9_mali_transcfg_supported(s->vm3_control, s->as0_active_transcfg)) {
        s->vm3_translation_mode_rejections += s->vm3_control;
        return false;
    }
    if (!table ||
        (table & ~AUTO9_G3D00_PA_MASK) || va >= (1ULL << 48) ||
        !len || len > AUTO9_G3D00_GPU_PAGE - (va & 0xfff)) {
        return false;
    }
    for (unsigned level = 0; level < 4; level++) {
        unsigned shift = 39 - 9 * level;
        uint64_t slot = table + ((va >> shift) & 0x1ff) * 8;
        uint8_t entry[8];
        uint64_t pte, base;
        unsigned type, ap;

        if (!auto9_mali_private_ram(s, table, AUTO9_G3D00_GPU_PAGE, false, false) ||
            address_space_read(&address_space_memory, slot,
                               MEMTXATTRS_UNSPECIFIED, entry,
                               sizeof(entry)) != MEMTX_OK) {
            return false;
        }
        pte = ldq_le_p(entry);
        if (pte & (AUTO9_G3D00_PA_FIELD & ~AUTO9_G3D00_PA_MASK)) {
            return false;
        }
        type = pte & 3;
        base = pte & AUTO9_G3D00_PA_MASK;
        if (level < 3 && type == 3) {
            if (pte >> 59) { /* No hierarchical permission overrides. */
                return false;
            }
            table = base;
            continue;
        }
        if (type != (level == 3 ? 3 : 1) || !(pte & BIT(10))) {
            return false;
        }
        ap = (pte >> 6) & 3;
        if (ap != 1 && (ap != 3 || write)) {
            return false;
        }
        uint64_t block = (1ULL << shift) - 1;
        if (base & block) {
            return false;
        }
        *pa = base | (va & block);
        return auto9_mali_private_ram(s, *pa, len, write, true);
    }
    return false;
}

static bool auto9_g3d00_dma_read(Auto9MaliG3d1 *s, uint64_t va,
                                uint8_t *data, unsigned len)
{
    if (!len || va >= (1ULL << 48) || len > (1ULL << 48) - va) {
        return false;
    }
    while (len) {
        unsigned page_len = MIN(len, AUTO9_G3D00_GPU_PAGE - (va & 0xfff));
        uint64_t pa;
        if (!auto9_g3d00_translate(s, va, page_len, false, &pa) ||
            address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                               data, page_len) != MEMTX_OK) {
            return false;
        }
        data += page_len;
        va += page_len;
        len -= page_len;
    }
    return true;
}

static void auto9_g3d00_sample_bytes(const char *stage, unsigned sample,
                                     const char *object, unsigned index,
                                     const uint8_t *bytes, unsigned len)
{
    static const char digits[] = "0123456789abcdef";
    char hex[513];

    if (len > (sizeof(hex) - 1) / 2) {
        return;
    }
    for (unsigned i = 0; i < len; i++) {
        hex[i * 2] = digits[bytes[i] >> 4];
        hex[i * 2 + 1] = digits[bytes[i] & 15];
    }
    hex[len * 2] = 0;
    qemu_log_mask(LOG_GUEST_ERROR,
                  "Auto9 G3D00 %s bytes: sample=%u object=%s "
                  "index=%u bytes=%u hex=%s\n", stage, sample, object, index, len, hex);
}

static void auto9_g3d00_vertex_bytes(unsigned sample, const char *object,
                                    unsigned index, const uint8_t *bytes,
                                    unsigned len)
{
    auto9_g3d00_sample_bytes("vertex", sample, object, index, bytes, len);
}

static void auto9_g3d00_tiler_bytes(Auto9MaliG3d1 *s, const char *object,
                                    unsigned index, uint64_t va, unsigned len)
{
    uint8_t data[256];
    if (va && len <= sizeof(data) && auto9_g3d00_dma_read(s, va, data, len)) {
        auto9_g3d00_sample_bytes("tiler", s->tiler_sample_attempts,
                                object, index, data, len);
    }
}

/* Capture the first two real v7 Tiler jobs and their immediate inputs after
 * the preceding vertex writebacks. Every read uses VM2's checked AS0 walker.
 * No tiler heap, polygon list, or render target is modified by this path. */
static void auto9_g3d00_tiler_sample(Auto9MaliG3d1 *s, uint64_t head)
{
    uint8_t job[256], ctx[192], renderer[64], attr[8], buffer[16];
    const uint8_t *draw = job + 128;
    uint64_t ptr, varyings, buffers;
    unsigned varying_count = 0;

    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) || s->tiler_sample_attempts >= 2) {
        return;
    }
    s->tiler_sample_attempts++;
    if (!auto9_g3d00_dma_read(s, head, job, sizeof(job))) {
        return;
    }
    auto9_g3d00_sample_bytes("tiler", s->tiler_sample_attempts,
                            "job", 0, job, sizeof(job));
    ptr = ldq_le_p(job + 72);
    if (ptr && !(ptr & 63) && auto9_g3d00_dma_read(s, ptr, ctx, sizeof(ctx))) {
        auto9_g3d00_sample_bytes("tiler", s->tiler_sample_attempts,
                                "context", 0, ctx, sizeof(ctx));
        auto9_g3d00_tiler_bytes(s, "heap-descriptor", 0, ldq_le_p(ctx + 24), 32);
    }
    ptr = ldq_le_p(draw + 56);
    if (ptr && !(ptr & 63) && auto9_g3d00_dma_read(s, ptr, renderer, sizeof(renderer))) {
        varying_count = MIN(lduw_le_p(renderer + 14), 4U);
        auto9_g3d00_sample_bytes("tiler", s->tiler_sample_attempts,
                                "renderer", 0, renderer, sizeof(renderer));
        auto9_g3d00_tiler_bytes(s, "fragment-program-prefix", 0, ldq_le_p(renderer), 256);
        auto9_g3d00_tiler_bytes(s, "blend-rt0", 0, ptr + sizeof(renderer), 16);
    }
    auto9_g3d00_tiler_bytes(s, "viewport", 0, ldq_le_p(draw + 96), 32);
    auto9_g3d00_tiler_bytes(s, "position-prefix", 0, ldq_le_p(draw + 16), 96);
    varyings = ldq_le_p(draw + 88);
    buffers = ldq_le_p(draw + 80);
    if (!varyings || !buffers || (varyings & 7) || (buffers & 31)) {
        return;
    }
    for (unsigned i = 0; i < varying_count; i++) {
        if (!auto9_g3d00_dma_read(s, varyings + i * 8, attr, sizeof(attr))) {
            break;
        }
        auto9_g3d00_sample_bytes("tiler", s->tiler_sample_attempts,
                                "varying", i, attr, sizeof(attr));
        unsigned idx = ldl_le_p(attr) & 0x1ff;
        if (!auto9_g3d00_dma_read(s, buffers + idx * 16, buffer, sizeof(buffer))) {
            continue;
        }
        auto9_g3d00_sample_bytes("tiler", s->tiler_sample_attempts,
                                "varying-buffer", i, buffer, sizeof(buffer));
        if ((buffer[0] & 63) == 1) {
            auto9_g3d00_tiler_bytes(s, "varying-prefix", i,
                                    ldq_le_p(buffer) & UINT64_C(0x00ffffffffffffc0),
                                    MIN(ldl_le_p(buffer + 12), 128U));
        }
    }
}

static bool auto9_g3d00_fragment_inputs(Auto9MaliG3d1 *s, uint64_t head,
                                        uint8_t job[64], uint8_t fb[128],
                                        uint8_t ext[64], uint8_t rt[64], bool observe)
{
    uint64_t tag, address;

    if (!auto9_g3d00_dma_read(s, head, job, 64)) {
        return false;
    }
    tag = ldq_le_p(job + 40);
    if (s->vm3_control && observe && qemu_loglevel_mask(LOG_GUEST_ERROR) &&
        s->fragment_sample_attempts < 4) {
        unsigned n = ++s->fragment_sample_attempts;
        qemu_log_mask(LOG_GUEST_ERROR,
                      "Auto9 G3D1 fragment header: sample=%u min=0x%x max=0x%x tag=0x%llx extra=%u\n",
                      n, ldl_le_p(job + 32), ldl_le_p(job + 36),
                      (unsigned long long)(tag & 63),
                      (unsigned)!!(ldq_le_p(job + 48) || ldq_le_p(job + 56)));
    }
    address = tag & ~UINT64_C(63);
    if (!(tag & 1) || !address || !auto9_g3d00_dma_read(s, address, fb, 128) ||
        ((tag & 2) && !auto9_g3d00_dma_read(s, address + 128, ext, 64)) ||
        !auto9_g3d00_dma_read(s, address + 128 + ((tag & 2) ? 64 : 0), rt, 64)) {
        return false;
    }
    if (!(tag & 2)) {
        memset(ext, 0, 64);
    }
    if (s->g3d00 && observe && qemu_loglevel_mask(LOG_GUEST_ERROR) && s->fragment_sample_attempts < 2) {
        unsigned i = ++s->fragment_sample_attempts;
        auto9_g3d00_sample_bytes("fragment", i, "job", 0, job, 64);
        auto9_g3d00_sample_bytes("fragment", i, "framebuffer", 0, fb, 128);
        if (tag & 2) {
            auto9_g3d00_sample_bytes("fragment", i, "zs-crc", 0, ext, 64);
        }
        auto9_g3d00_sample_bytes("fragment", i, "render-target", 0, rt, 64);
    }
    return true;
}

enum { FRAG_INPUT, FRAG_PROFILE, FRAG_TARGET, FRAG_STORAGE, FRAG_TRANSLATE,
       FRAG_SHADER, FRAG_BUDGET, FRAG_NO_BATCH, FRAG_WRITE,
       FRAG_TEXTURE_STATE, FRAG_TEXTURE_SURFACE, FRAG_TEXTURE_MEMORY,
       FRAG_AFBC_LAYOUT, FRAG_AFBC_COVERAGE, FRAG_AFBC_HEADER, FRAG_AFBC_ALIAS,
       FRAG_AFBC_DEPENDENCY, FRAG_DISCARD };

static void auto9_g3d00_fragment_fault_sample(Auto9MaliG3d1 *s, uint64_t head,
                                              unsigned stage, uint64_t renderer)
{
    uint8_t job[64], fb[128], ext[64], rt[64], rsd[80], code[128];
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) || s->fragment_fault_samples >= 4 ||
        !auto9_g3d00_fragment_inputs(s, head, job, fb, ext, rt, false)) {
        return;
    }
    /* Compare meaningful profile fields, not per-job GPU addresses. */
    uint64_t sig = ((uint64_t)stage << 56) ^ ldq_le_p(job + 32) ^
                   ldq_le_p(fb + 44) ^ ldq_le_p(rt) ^ ldl_le_p(rt + 40);
    for (unsigned i = 0; i < s->fragment_fault_samples; i++) {
        if (s->fragment_fault_signature[i] == sig) {
            return;
        }
    }
    s->fragment_fault_signature[s->fragment_fault_samples++] = sig;
    unsigned n = s->fragment_fault_samples;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 G3D00 fragment rejected: sample=%u stage=%u\n", n, stage);
    auto9_g3d00_sample_bytes("fragment-fault", n, "job", 0, job, sizeof(job));
    auto9_g3d00_sample_bytes("fragment-fault", n, "framebuffer", 0, fb, sizeof(fb));
    auto9_g3d00_sample_bytes("fragment-fault", n, "render-target", 0, rt, sizeof(rt));
    if (job[40] & 2) {
        auto9_g3d00_sample_bytes("fragment-fault", n, "zs-crc", 0, ext, sizeof(ext));
    }
    if (renderer && auto9_g3d00_dma_read(s, renderer, rsd, sizeof(rsd))) {
        auto9_g3d00_sample_bytes("fragment-fault", n, "renderer-and-blend", 0, rsd, sizeof(rsd));
        if (auto9_g3d00_dma_read(s, ldq_le_p(rsd), code, sizeof(code))) {
            auto9_g3d00_sample_bytes("fragment-fault", n, "shader", 0, code, sizeof(code));
        }
    }
}

/* VM3 may submit a whole geometry chain before the corresponding JS0 job.
 * Preserve submission order and report a full queue rather than evicting
 * in-flight geometry. QNX keeps its original cap. */
static bool auto9_mali_tile_queue_full(const Auto9MaliG3d1 *s)
{
    return s->tile_batch_count >= (s->vm3_control ? AUTO9_VM3_TILE_BATCHES : AUTO9_G3D00_TILE_BATCHES);
}

static void auto9_g3d00_consume_tiles(Auto9MaliG3d1 *s, uint64_t tiler)
{
    unsigned keep = 0;
    for (unsigned i = 0; i < s->tile_batch_count; i++) {
        Auto9G3d00TileBatch *b = &s->tile_batch[i];
        if (b->context != tiler || b->ttbr != s->as0_active_ttbr) {
            if (keep != i) {
                s->tile_batch[keep] = *b;
            }
            keep++;
        }
    }
    s->tile_batch_count = keep;
}

/* A hard-faulted JS1 chain cannot leave empty polygon-list tokens behind.
 * Keep earlier submissions and this chain's real geometry in draw order.
 * Successful empty submissions still serve as a match for JS0 validation. */
static void auto9_vm3_discard_faulted_empty_batches(Auto9MaliG3d1 *s,
                                                     unsigned first)
{
    unsigned keep = first;
    unsigned prior = s->tile_batch_count;

    for (unsigned i = first; i < prior; i++) {
        if (!s->tile_batch[i].count && !s->tile_batch[i].store_point) {
            continue;
        }
        if (keep != i) {
            s->tile_batch[keep] = s->tile_batch[i];
        }
        keep++;
    }
    s->tile_batch_count = keep;
    if (keep != prior) {
        s->vm3_faulted_empty_batches += prior - keep;
        if (s->vm3_faulted_empty_batches <= 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 faulted empty batches: discarded=%u retained=%u total=%u\n",
                prior - keep, keep, s->vm3_faulted_empty_batches);
        }
    }
}

/* Genxml v7 Attribute descriptors are 8 bytes, buffer records are 16. */
static void auto9_g3d00_vertex_arrays(Auto9MaliG3d1 *s, uint64_t table,
                                     uint64_t buffers, unsigned count,
                                     bool output)
{
    for (unsigned i = 0; i < MIN(count, 8U); i++) {
        uint8_t attr[8], buffer[16], values[256];
        unsigned index, size;
        uint64_t va;

        if (!table || !buffers || (table & 7) || (buffers & 31) ||
            !auto9_g3d00_dma_read(s, table + i * 8, attr, sizeof(attr))) {
            return;
        }
        auto9_g3d00_vertex_bytes(s->vertex_samples, output ? "varying" : "attribute",
                                 i, attr, sizeof(attr));
        index = ldl_le_p(attr) & 0x1ff;
        if (!auto9_g3d00_dma_read(s, buffers + index * 16, buffer, sizeof(buffer))) {
            continue;
        }
        auto9_g3d00_vertex_bytes(s->vertex_samples,
                                 output ? "varying-buffer" : "attribute-buffer",
                                 i, buffer, sizeof(buffer));
        va = ldq_le_p(buffer) & UINT64_C(0x00ffffffffffffc0);
        size = MIN(ldl_le_p(buffer + 12), sizeof(values));
        /* Preview only the beginning of each real input, not destination RAM. */
        if (!output && (buffer[0] & 63) == 1 && va &&
            auto9_g3d00_dma_read(s, va, values, size)) {
            auto9_g3d00_vertex_bytes(s->vertex_samples, "attribute-input", i,
                                     values, size);
        }
    }
}

/*
 * Genxml v7: vertex job has a Draw at +64, its renderer state pointer at
 * Draw+56, and shader VA at renderer+0. Capture only a bounded code prefix
 * for the missing renderer, never mark this descriptor as executed.
 */
static void auto9_g3d00_vertex_sample(Auto9MaliG3d1 *s, uint64_t head,
                                     unsigned slot)
{
    uint8_t draw_prefix[64], renderer[16], code[128];
    uint64_t state, shader;
    char hex[sizeof(code) * 2 + 1];

    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) ||
        s->vertex_sample_attempts >= AUTO9_G3D00_VERTEX_SAMPLES) {
        return;
    }
    s->vertex_sample_attempts++;
    if (!auto9_g3d00_dma_read(s, head + 64, draw_prefix, sizeof(draw_prefix))) {
        return;
    }
    state = ldq_le_p(draw_prefix + 56);
    if (!state || (state & 63) ||
        !auto9_g3d00_dma_read(s, state, renderer, sizeof(renderer))) {
        return;
    }
    shader = ldq_le_p(renderer);
    if (!shader || (shader & 15) ||
        !auto9_g3d00_dma_read(s, shader, code, sizeof(code))) {
        return;
    }
    for (unsigned i = 0; i < sizeof(code); i++) {
        static const char digits[] = "0123456789abcdef";
        hex[i * 2] = digits[code[i] >> 4];
        hex[i * 2 + 1] = digits[code[i] & 15];
    }
    hex[sizeof(hex) - 1] = 0;
    s->vertex_samples++;
    qemu_log_mask(LOG_GUEST_ERROR,
                  "Auto9 G3D00 vertex input: slot=%u attributes=%u "
                  "varyings=%u shader-prefix=%s\n", slot,
                  lduw_le_p(renderer + 12), lduw_le_p(renderer + 14), hex);
    if (s->vertex_samples <= 2) {
        uint8_t draw[128], rsd[64], invocation[8], program[240], uniforms[128];

        if (auto9_g3d00_dma_read(s, shader, program, sizeof(program))) {
            auto9_g3d00_vertex_bytes(s->vertex_samples, "shader", 0,
                                     program, sizeof(program));
        }
        if (auto9_g3d00_dma_read(s, head + 32, invocation, sizeof(invocation))) {
            auto9_g3d00_vertex_bytes(s->vertex_samples, "invocation", 0,
                                     invocation, sizeof(invocation));
        }
        if (auto9_g3d00_dma_read(s, state, rsd, sizeof(rsd))) {
            auto9_g3d00_vertex_bytes(s->vertex_samples, "renderer", 0, rsd, sizeof(rsd));
        }
        if (!auto9_g3d00_dma_read(s, head + 64, draw, sizeof(draw))) {
            return;
        }
        auto9_g3d00_vertex_bytes(s->vertex_samples, "draw", 0, draw, sizeof(draw));
        auto9_g3d00_vertex_arrays(s, ldq_le_p(draw + 72), ldq_le_p(draw + 64),
                                  lduw_le_p(renderer + 12), false);
        auto9_g3d00_vertex_arrays(s, ldq_le_p(draw + 88), ldq_le_p(draw + 80),
                                  lduw_le_p(renderer + 14), true);
        uint64_t fau = ldq_le_p(draw + 48);
        if (fau && auto9_g3d00_dma_read(s, fau, uniforms, sizeof(uniforms))) {
            auto9_g3d00_vertex_bytes(s->vertex_samples, "push-uniforms", 0,
                                     uniforms, sizeof(uniforms));
        }
    }
}

/* Gen7 format-0 clause header and tag termination. Stop at the first END
 * clause, not at a guessed byte size. This is framing, not ISA validation;
 * the decoder still needs to check branch targets and instruction semantics. */
static unsigned auto9_g3d1_bifrost_extent(const uint8_t *code, unsigned capacity,
                                         unsigned *stop, unsigned *reason)
{
    bool start = true, shader_end = false;
    *reason = 6; /* Budget without a proven END clause. */
    for (unsigned off = 0; off + 16 <= capacity; off += 16) {
        *stop = off;
        const uint8_t *word = code + off;
        unsigned tag = word[0], group = (tag >> 3) & 7, low = tag & 7;
        if (start) {
            if ((tag & 0x80) || (group != 1 && group != 5)) {
                *reason = 2;
                return 0;
            }
            /* QEMU's ldl_le_p returns int. Header fields are unsigned
             * bit slices: sign extension would manufacture reserved bits. */
            uint32_t lo = ldl_le_p(word + 8), hi = ldl_le_p(word + 12);
            uint64_t header = (lo >> 19) | ((uint64_t)hi << 13);
            if (header & 0x401f) { /* Reserved clause header bits. */
                *reason = 3;
                return 0;
            }
            shader_end = ((header >> 11) & 7) == 0;
        } else if (!(tag & 0x80) && (group == 1 || group == 5)) {
            *reason = 4;
            return 0; /* New header before the previous clause ends. */
        }
        if (!(tag & 0x80) && group == 0 && (low < 1 || low == 2)) {
            *reason = 5;
            return 0;
        }
        if (!(tag & 0x80) && group == 7 && low == 7) {
            *reason = 5;
            return 0; /* Reserved format-12 position. */
        }
        start = (tag & 0xc0) == 0x40 && group != 4 && group != 5 &&
                !(group == 0 && low == 1);
        if (start && shader_end) {
            *reason = 0;
            return off + 16;
        }
    }
    return 0;
}

/* Read descriptor metadata and framed code from VM3's DMA view. Never log
 * vertex values, uniform words, texture data or guest addresses. */
static void auto9_g3d1_vm3_vertex_sample(Auto9MaliG3d1 *s, uint64_t head,
                                        unsigned slot)
{
    enum { PREFIX = 64 };
    uint8_t invocation[8], draw[96], renderer[64], code[512];
    if (s->vm3_vertex_attempts >= 24 ||
        s->vm3_vertex_samples >= G_N_ELEMENTS(s->vm3_vertex_fingerprints) ||
        !qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        return;
    }
    s->vm3_vertex_attempts++;
    if (!auto9_g3d00_dma_read(s, head + 32, invocation, sizeof(invocation)) ||
        !auto9_g3d00_dma_read(s, head + 64, draw, sizeof(draw))) {
        return;
    }
    uint64_t state = ldq_le_p(draw + 56);
    if (!state || (state & 63) ||
        !auto9_g3d00_dma_read(s, state, renderer, sizeof(renderer))) {
        return;
    }
    uint64_t shader = ldq_le_p(renderer);
    if (!shader || (shader & 15)) {
        return;
    }
    /* Read the prefix and candidate clauses as one immutable host snapshot.
     * Restrict the larger read to the current GPU page. A shorter fallback
     * read is still validated by the existing cross-page VM3 DMA walker. */
    unsigned capacity = MIN(sizeof(code), AUTO9_G3D00_GPU_PAGE -
                            (shader & (AUTO9_G3D00_GPU_PAGE - 1)));
    if (capacity < PREFIX || !auto9_g3d00_dma_read(s, shader, code, capacity)) {
        capacity = PREFIX;
        if (!auto9_g3d00_dma_read(s, shader, code, capacity)) {
            return;
        }
    }
    g_autofree char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256,
                                                            code, PREFIX);
    if (!digest) {
        return;
    }
    for (unsigned i = 0; i < s->vm3_vertex_samples; i++) {
        if (!strcmp(s->vm3_vertex_fingerprints[i], digest)) {
            return;
        }
    }
    g_strlcpy(s->vm3_vertex_fingerprints[s->vm3_vertex_samples], digest, 65);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 vertex: sample=%u slot=%u inv0=0x%x inv4=0x%x draw0=0x%x "
        "rsd8=0x%x attributes=%u varyings=%u shader64-sha256=%s\n",
        ++s->vm3_vertex_samples, slot, ldl_le_p(invocation), ldl_le_p(invocation + 4),
        ldl_le_p(draw), ldl_le_p(renderer + 8), lduw_le_p(renderer + 12),
        lduw_le_p(renderer + 14), digest);
    unsigned sample = s->vm3_vertex_samples;
    unsigned stop = 0, reason = 0;
    unsigned extent = auto9_g3d1_bifrost_extent(code, capacity, &stop, &reason);
    if (extent) {
        for (unsigned off = 0; off < extent; off += 128) {
            auto9_g3d00_sample_bytes("vm3-vertex", sample, "framed-program", off,
                                     code + off, MIN(extent - off, 128));
        }
    } else if (!strcmp(digest, "62a6124a87f441f2b2db955321aa278f89e576a6d1ba290052526c77a6c63486")) {
        /* This exact prefix was independently observed in two OEM VM3
         * sessions. Do not spill any unframed adjacent GPU memory. */
        auto9_g3d00_sample_bytes("vm3-vertex", sample, "pinned-prefix", 0, code, PREFIX);
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 vertex layout: sample=%u extent=%u rsd48=0x%x push=%u "
        "draw-bias=%u\n", sample, extent, ldl_le_p(renderer + 48),
        (unsigned)!!ldq_le_p(draw + 48),
        (unsigned)!!(ldq_le_p(draw + 4) || ldq_le_p(draw + 16)));
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 vertex boundary: sample=%u stop=%u reason=%u\n",
        sample, stop, reason);
    /* The observed ABI uses two input attributes and two varyings. Report
     * their descriptors and storage lengths, but not buffer pointers. */
    if (lduw_le_p(renderer + 12) == 2 && lduw_le_p(renderer + 14) == 2) {
        const unsigned offsets[] = { 72, 88, 64, 80 };
        for (unsigned i = 0; i < G_N_ELEMENTS(offsets); i++) {
            uint8_t data[32];
            uint64_t va = ldq_le_p(draw + offsets[i]);
            if (va && auto9_g3d00_dma_read(s, va, data, i < 2 ? 16 : 32)) {
                if (i < 2) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 G3D1 VM3 vertex fields: sample=%u table=%u a=0x%x b=0x%x c=0x%x d=0x%x\n",
                        sample, i, ldl_le_p(data), ldl_le_p(data + 4),
                        ldl_le_p(data + 8), ldl_le_p(data + 12));
                } else {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 G3D1 VM3 vertex strides: sample=%u table=%u a=%u b=%u c=%u d=%u\n",
                        sample, i, ldl_le_p(data + 8), ldl_le_p(data + 12),
                        ldl_le_p(data + 24), ldl_le_p(data + 28));
                    /* Both attributes select input buffer 0. Only that
                     * input entry and the two selected outputs are needed. */
                    unsigned entries = i == 2 ? 1 : 2;
                    for (unsigned j = 0; j < entries; j++) {
                        uint64_t tagged = ldq_le_p(data + 16 * j);
                        qemu_log_mask(LOG_GUEST_ERROR,
                            "Auto9 G3D1 VM3 vertex buffer: sample=%u table=%u entry=%u type=%u mode=%u\n",
                            sample, i, j, (unsigned)(tagged & 63), (unsigned)(tagged >> 56));
                    }
                }
            }
        }
    }
}

static void auto9_g3d00_flush_ordering(Auto9MaliG3d1 *s)
{
    /* No cache to invalidate: serialize direct RAM transactions and advance
     * only for a completed flush, never from a guest-provided NEXT tag. */
    smp_mb();
    s->latest_flush_id++;
}

static bool auto9_g3d00_overlap(uint64_t a, unsigned alen,
                               uint64_t b, unsigned blen)
{
    return a < b + blen && b < a + alen;
}

static float auto9_g3d00_float(const uint8_t *p)
{
    uint32_t word = ldl_le_p(p);
    float value;
    memcpy(&value, &word, sizeof(value));
    return value;
}

/* Conservative 16x16 tile bounds. Exact coverage belongs to the fragment
 * stage, which also supplies the sample pattern. xy and clip use 24.8. */
static bool auto9_g3d00_bin_triangle(Auto9G3d00Triangle *tri,
                                    const int32_t clip_lo[2], const int32_t clip_hi[2])
{
    int64_t dx1 = tri->xy[1][0] - tri->xy[0][0], dy1 = tri->xy[1][1] - tri->xy[0][1];
    int64_t dx2 = tri->xy[2][0] - tri->xy[0][0], dy2 = tri->xy[2][1] - tri->xy[0][1];
    if (dx1 * dy2 == dx2 * dy1) {
        return false;
    }
    for (unsigned a = 0; a < 2; a++) {
        int32_t lo = MAX(clip_lo[a], MIN(tri->xy[0][a], MIN(tri->xy[1][a], tri->xy[2][a])));
        int32_t hi = MIN(clip_hi[a], MAX(tri->xy[0][a], MAX(tri->xy[1][a], tri->xy[2][a])));
        if (lo >= hi) {
            return false;
        }
        tri->pixel[a] = lo / 256;
        tri->pixel[a + 2] = (hi - 1) / 256;
        tri->tile[a] = lo / (16 * 256);
        tri->tile[a + 2] = (hi - 1) / (16 * 256);
    }
    return true;
}

/* The hardware heap is an opaque GPU-private polygon list. Keep its virtual
 * equivalent in the device, keyed by the original AS root and Tiler Context;
 * retain draw order for fragment blending. No guest framebuffer write here. */
static uint32_t auto9_g3d00_tile_job(Auto9MaliG3d1 *s, uint64_t head,
                                    unsigned slot, Auto9G3d00TileBatch *batch)
{
    uint8_t job[256], ctx[192], viewport[32], attr[8], buffer[16], heap[32];
    uint8_t positions[AUTO9_G3D00_TILE_VERTICES * 16];
    uint8_t colors[AUTO9_G3D00_TILE_VERTICES * 8];
    const uint8_t *draw = job + 128;
    uint64_t vp, pos, va, attrs, buffers, heap_va;
    uint32_t count, wh, minimum, maximum;
    int32_t clip_lo[2], clip_hi[2];

    if (slot != 1 || s->tile_batch_count >= AUTO9_G3D00_TILE_BATCHES) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, head, job, sizeof(job))) {
        return G3D00_JOB_READ_FAULT;
    }
    count = ldl_le_p(job + 32);
    if (count >= AUTO9_G3D00_TILE_VERTICES || (count + 1) % 3 ||
        ldl_le_p(job + 36) != 0x38000000 || ldl_le_p(job + 40) != 0x18030008 ||
        ldq_le_p(job + 44) || ldl_le_p(job + 52) != count || ldq_le_p(job + 56) ||
        ldl_le_p(draw) != 0x23 || ldq_le_p(draw + 4)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    count++;
    batch->ttbr = s->as0_active_ttbr;
    batch->context = ldq_le_p(job + 72);
    batch->renderer = ldq_le_p(draw + 56);
    vp = ldq_le_p(draw + 96);
    pos = ldq_le_p(draw + 16);
    attrs = ldq_le_p(draw + 88);
    buffers = ldq_le_p(draw + 80);
    if (!batch->context || !batch->renderer || !pos || !vp || !attrs || !buffers ||
        ((batch->context | batch->renderer) & 63) || ((vp | buffers) & 31) ||
        (attrs & 7) || (pos & 15)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, batch->context, ctx, sizeof(ctx)) ||
        !auto9_g3d00_dma_read(s, vp, viewport, sizeof(viewport)) ||
        !auto9_g3d00_dma_read(s, attrs, attr, sizeof(attr)) ||
        !auto9_g3d00_dma_read(s, buffers, buffer, sizeof(buffer))) {
        return G3D00_JOB_READ_FAULT;
    }
    wh = ldl_le_p(ctx + 12);
    minimum = ldl_le_p(viewport + 24);
    maximum = ldl_le_p(viewport + 28);
    heap_va = ldq_le_p(ctx + 24);
    if ((wh & 65535) >= 4096 || (wh >> 16) >= 4096 || ldl_le_p(ctx + 8) ||
        !heap_va || (heap_va & 63) || ldl_le_p(attr) != (0xdf000U << 10) ||
        ldl_le_p(attr + 4) || ldl_le_p(buffer + 8) != 8 ||
        ldl_le_p(buffer + 12) < count * 8 ||
        ldq_le_p(viewport) != UINT64_C(0xff800000ff800000) ||
        ldq_le_p(viewport + 8) != UINT64_C(0x7f8000007f800000) ||
        ldq_le_p(viewport + 16) != UINT64_C(0x3f80000000000000)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    batch->width = (wh & 65535) + 1;
    batch->height = (wh >> 16) + 1;
    if ((maximum & 65535) >= batch->width || (maximum >> 16) >= batch->height ||
        (minimum & 65535) > (maximum & 65535) || (minimum >> 16) > (maximum >> 16)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    clip_lo[0] = (minimum & 65535) * 256;
    clip_lo[1] = (minimum >> 16) * 256;
    clip_hi[0] = ((maximum & 65535) + 1) * 256;
    clip_hi[1] = ((maximum >> 16) + 1) * 256;
    va = ldq_le_p(buffer);
    if ((va & UINT64_C(0xff0000000000003f)) != 1 || va <= 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, heap_va, heap, sizeof(heap)) ||
        !auto9_g3d00_dma_read(s, pos, positions, count * 16) ||
        !auto9_g3d00_dma_read(s, va & ~UINT64_C(63), colors, count * 8)) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t base = ldq_le_p(heap + 8), bottom = ldq_le_p(heap + 16);
    uint64_t top = ldq_le_p(heap + 24);
    uint32_t size = ldl_le_p(heap + 4);
    if (!base || ((base | bottom | top | size) & 4095) || base > bottom ||
        bottom >= top || top >= (UINT64_C(1) << 48) || top - base > size) {
        return G3D00_JOB_CONFIG_FAULT;
    }

    for (unsigned i = 0; i < count; i += 3) {
        Auto9G3d00Triangle tri = {0};
        for (unsigned j = 0; j < 3; j++) {
            const uint8_t *p = positions + (i + j) * 16;
            for (unsigned a = 0; a < 2; a++) {
                float xy = auto9_g3d00_float(p + a * 4) * 256.0f;
                if (!isfinite(xy) || fabsf(xy) > 0x1p23f || truncf(xy) != xy) {
                    return G3D00_JOB_CONFIG_FAULT;
                }
                tri.xy[j][a] = xy;
            }
            tri.z[j] = auto9_g3d00_float(p + 8);
            tri.inv_w[j] = auto9_g3d00_float(p + 12);
            if (!(tri.z[j] >= 0 && tri.z[j] <= 1) ||
                !isfinite(tri.inv_w[j]) || tri.inv_w[j] <= 0) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            for (unsigned a = 0; a < 4; a++) {
                uint16_t half = lduw_le_p(colors + (i + j) * 8 + a * 2);
                if (half > 0x3c00) { /* Vertex writer supplies finite UNORM. */
                    return G3D00_JOB_CONFIG_FAULT;
                }
                tri.rgba[j][a] = half;
            }
        }
        if (auto9_g3d00_bin_triangle(&tri, clip_lo, clip_hi)) {
            batch->triangle[batch->count++] = tri;
        }
    }
    return G3D00_JOB_DONE;
}

/* Retain only a bounded, in-process history. No physical or virtual
 * addresses, shader inputs, or raw position/color words enter the trace. */
static void auto9_vm3_vertex3_origin(Auto9MaliG3d1 *s, const uint64_t pa[4][2],
                                       const uint8_t pos[4][16], const uint8_t vary[4][16])
{
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        return;
    }
    uint32_t job = s->vm3_vertex3_jobs + 1;
    Auto9Vm3FanOrigin *o = &s->vm3_fan_origins[job % G_N_ELEMENTS(s->vm3_fan_origins)];
    o->job = job;
    o->ttbr = s->as0_active_ttbr;
    memcpy(o->pa, pa, sizeof(o->pa));
    memcpy(o->position, pos, sizeof(o->position));
    for (unsigned i = 0; i < 4; i++) {
        memcpy(o->rgba + 8 * i, vary[i], 8);
    }
}

static void auto9_vm3_color_origin(Auto9MaliG3d1 *s, uint64_t pos, uint64_t vary,
                                   const uint8_t positions[64], const uint8_t colors[32])
{
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) || s->vm3_fan_origin_samples >= 8) {
        return;
    }
    uint64_t pa[4][2] = {0};
    unsigned positive = 0, negative = 0, zero = 0, invalid = 0, zero_xyz = 0;
    bool mapped[2] = { true, true };
    for (unsigned v = 0; v < 4; v++) {
        mapped[0] &= auto9_g3d00_translate(s, pos + 16 * v, 16, false, &pa[v][0]);
        mapped[1] &= auto9_g3d00_translate(s, vary + 8 * v, 8, false, &pa[v][1]);
        const uint8_t *p = positions + 16 * v;
        float w = auto9_g3d00_float(p + 12);
        if (!isfinite(w)) {
            invalid |= 1U << v;
        } else if (w > 0.0f) {
            positive |= 1U << v;
        } else if (w < 0.0f) {
            negative |= 1U << v;
        } else {
            zero |= 1U << v;
        }
        if (auto9_g3d00_float(p) == 0 && auto9_g3d00_float(p + 4) == 0 &&
            auto9_g3d00_float(p + 8) == 0) {
            zero_xyz |= 1U << v;
        }
    }
    uint32_t writer[2] = {0};
    bool same[2] = {0};
    for (unsigned i = 0; i < G_N_ELEMENTS(s->vm3_fan_origins); i++) {
        const Auto9Vm3FanOrigin *o = &s->vm3_fan_origins[i];
        if (!o->job || o->ttbr != s->as0_active_ttbr) {
            continue;
        }
        for (unsigned k = 0; k < 2; k++) {
            bool match = mapped[k];
            for (unsigned v = 0; v < 4; v++) {
                match &= o->pa[v][k] == pa[v][k];
            }
            if (match && o->job > writer[k]) {
                writer[k] = o->job;
                same[k] = k ? !memcmp(o->rgba, colors, sizeof(o->rgba))
                            : !memcmp(o->position, positions, sizeof(o->position));
            }
        }
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 color origin: sample=%u pos-writer=%u rgba-writer=%u "
        "pos-same=%u rgba-same=%u pos-mapped=%u rgba-mapped=%u "
        "w-pos=0x%x w-neg=0x%x w-zero=0x%x w-invalid=0x%x xyz-zero=0x%x\n",
        ++s->vm3_fan_origin_samples, writer[0], writer[1], same[0], same[1],
        mapped[0], mapped[1], positive, negative, zero, invalid, zero_xyz);
}

/* VM3's original nonindexed four-vertex fan consumes the real stock
 * viewport outputs. Only the geometry is executed here, not its RSD or texture. */
typedef struct Auto9Vm3ClipVertex {
    double xyzw[4], rgba[4], uv[2], uv2[2];
} Auto9Vm3ClipVertex;

static float auto9_vm3_stock_half(uint16_t h);

static double auto9_vm3_clip_distance(const Auto9Vm3ClipVertex *v, unsigned plane,
                                      const double lo[3], const double hi[3])
{
    unsigned a = plane / 2;
    return plane & 1 ? hi[a] * v->xyzw[3] - v->xyzw[a]
                     : v->xyzw[a] - lo[a] * v->xyzw[3];
}

/* The original position varying is already in window space and supplies
 * signed reciprocal W. Undo the divide, clip against the window/depth
 * half-spaces, interpolate smooth colors there, then divide and snap the
 * generated vertices. Original XY is already quantized by SNAP_4. */
static bool auto9_vm3_clip_color_triangle(Auto9G3d00TileBatch *batch,
                                          const Auto9G3d00Triangle *in,
                                          const int32_t clip_lo[2], const int32_t clip_hi[2])
{
    enum { MAX_CLIPPED = 12 };
    Auto9Vm3ClipVertex vertices[2][MAX_CLIPPED];
    const double lo[3] = {clip_lo[0] / 256.0, clip_lo[1] / 256.0, 0};
    const double hi[3] = {clip_hi[0] / 256.0, clip_hi[1] / 256.0, 1};
    unsigned bank = 0, count = 3;
    for (unsigned i = 0; i < 3; i++) {
        Auto9Vm3ClipVertex *v = &vertices[0][i];
        double w = 1.0 / (double)in->inv_w[i];
        v->xyzw[0] = (in->xy[i][0] / 256.0) * w;
        v->xyzw[1] = (in->xy[i][1] / 256.0) * w;
        v->xyzw[2] = in->z[i] * w;
        v->xyzw[3] = w;
        for (unsigned k = 0; k < 4; k++) {
            v->rgba[k] = auto9_vm3_stock_half(in->rgba[i][k]);
        }
        for (unsigned k = 0; k < 2; k++) {
            v->uv[k] = in->uv32[i][k];
            v->uv2[k] = in->uv2[i][k];
        }
    }
    for (unsigned p = 0; p < 6 && count >= 3; p++) {
        const Auto9Vm3ClipVertex *cur = vertices[bank];
        Auto9Vm3ClipVertex *next = vertices[bank ^ 1];
        unsigned used = 0;
        for (unsigned i = 0; i < count; i++) {
            const Auto9Vm3ClipVertex *a = &cur[i], *b = &cur[(i + 1) % count];
            double da = auto9_vm3_clip_distance(a, p, lo, hi);
            double db = auto9_vm3_clip_distance(b, p, lo, hi);
            if (da >= 0) {
                if (used == MAX_CLIPPED) {
                    return false;
                }
                next[used++] = *a;
            }
            /* Ignore zero endpoints here: they are included by the inside
             * case. Symmetric distance weights agree on a reversed edge. */
            if ((da < 0 && db > 0) || (da > 0 && db < 0)) {
                if (used == MAX_CLIPPED) {
                    return false;
                }
                double wa = fabs(db), wb = fabs(da), den = wa + wb;
                Auto9Vm3ClipVertex *out = &next[used++];
                for (unsigned k = 0; k < 4; k++) {
                    out->xyzw[k] = (wa * a->xyzw[k] + wb * b->xyzw[k]) / den;
                    out->rgba[k] = (wa * a->rgba[k] + wb * b->rgba[k]) / den;
                }
                for (unsigned k = 0; k < 2; k++) {
                    out->uv[k] = (wa * a->uv[k] + wb * b->uv[k]) / den;
                    out->uv2[k] = (wa * a->uv2[k] + wb * b->uv2[k]) / den;
                }
                unsigned axis = p / 2;
                out->xyzw[axis] = (p & 1 ? hi[axis] : lo[axis]) * out->xyzw[3];
            }
        }
        count = used;
        bank ^= 1;
    }
    if (count < 3) {
        return true; /* Real clipping may discard the entire primitive. */
    }
    struct { int32_t xy[2]; float z, inv, uv[2], uv2[2]; uint16_t rgba[4]; } projected[MAX_CLIPPED];
    unsigned points = 0;
    for (unsigned i = 0; i < count; i++) {
        const Auto9Vm3ClipVertex *v = &vertices[bank][i];
        double w = v->xyzw[3];
        /* The homogeneous origin has no projected coordinate. Removing
         * that single zero vector retains the projected nondegenerate hull. */
        if (w == 0 && v->xyzw[0] == 0 && v->xyzw[1] == 0 && v->xyzw[2] == 0) {
            continue;
        }
        if (!(w > 0)) {
            return false;
        }
        float xy[4] = {0};
        for (unsigned k = 0; k < 2; k++) {
            xy[k] = fmax(lo[k], fmin(hi[k], v->xyzw[k] / w));
        }
        float inv = 1.0 / w;
        if (!(inv > 0) || !isfinite(inv) || !auto9_bifrost_snap4(xy)) {
            return false;
        }
        for (unsigned k = 0; k < 2; k++) {
            projected[points].xy[k] = xy[k] * 256.0f;
        }
        projected[points].inv = inv;
        projected[points].z = fmax(0.0, fmin(1.0, v->xyzw[2] / w));
        for (unsigned k = 0; k < 2; k++) {
            projected[points].uv[k] = v->uv[k];
            projected[points].uv2[k] = v->uv2[k];
            if (!isfinite(projected[points].uv[k]) || !isfinite(projected[points].uv2[k])) {
                return false;
            }
        }
        for (unsigned k = 0; k < 4; k++) {
            uint16_t half = auto9_bifrost_f32_half_rne(v->rgba[k]);
            if ((half & 0x7c00) == 0x7c00) {
                return false;
            }
            projected[points].rgba[k] = half;
        }
        points++;
    }
    for (unsigned i = 1; i + 1 < points; i++) {
        const unsigned index[3] = {0, i, i + 1};
        Auto9G3d00Triangle tri = {0};
        for (unsigned j = 0; j < 3; j++) {
            unsigned k = index[j];
            memcpy(tri.xy[j], projected[k].xy, sizeof(tri.xy[j]));
            memcpy(tri.rgba[j], projected[k].rgba, sizeof(tri.rgba[j]));
            memcpy(tri.uv32[j], projected[k].uv, sizeof(tri.uv32[j]));
            memcpy(tri.uv2[j], projected[k].uv2, sizeof(tri.uv2[j]));
            tri.z[j] = projected[k].z;
            tri.inv_w[j] = projected[k].inv;
        }
        if (auto9_g3d00_bin_triangle(&tri, clip_lo, clip_hi)) {
            if (batch->count >= G_N_ELEMENTS(batch->triangle)) {
                return false;
            }
            batch->triangle[batch->count++] = tri;
        }
    }
    return true;
}

/* A point is binned independently of its storage-only fragment. Keep
 * it in the original context and order without inventing a triangle or
 * running the fragment shader during the tiler job. */
static uint32_t auto9_vm3_store_point_bin(Auto9MaliG3d1 *s, const uint8_t job[232],
                                          Auto9G3d00TileBatch *batch, unsigned *stage)
{
    const uint8_t *draw = job + 128;
    uint64_t context = ldq_le_p(job + 72), pos = ldq_le_p(draw + 16);
    uint64_t vp = ldq_le_p(draw + 96), rsd = ldq_le_p(draw + 56);
    uint64_t push = ldq_le_p(draw + 48);
    uint8_t ctx[32], view[32], p[16], state[80], code[240], heap[32];
    if (ldl_le_p(job + 32) || ldl_le_p(job + 36) != 0x38000000 ||
        ldl_le_p(job + 40) != 1 || ldl_le_p(job + 44) || ldq_le_p(job + 48) ||
        ldq_le_p(job + 56) || ldq_le_p(job + 64) != 0x3f800000 ||
        ldq_le_p(draw) != 0x803 || ldq_le_p(draw + 8) ||
        ldq_le_p(draw + 80) || ldq_le_p(draw + 88) ||
        !context || (context & 63) || !rsd || (rsd & 63) ||
        !pos || (pos & 15) || !vp || (vp & 31) || !push || (push & 7)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = 2;
    if (!auto9_g3d00_dma_read(s, context, ctx, sizeof(ctx)) ||
        !auto9_g3d00_dma_read(s, pos, p, sizeof(p)) ||
        !auto9_g3d00_dma_read(s, vp, view, sizeof(view)) ||
        !auto9_g3d00_dma_read(s, rsd, state, sizeof(state))) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t shader = ldq_le_p(state), heap_va = ldq_le_p(ctx + 24);
    if (!shader || (shader & 15) || !heap_va || (heap_va & 63) ||
        !auto9_g3d00_dma_read(s, shader, code, sizeof(code)) ||
        !auto9_g3d00_dma_read(s, heap_va, heap, sizeof(heap))) {
        return G3D00_JOB_READ_FAULT;
    }
    *stage = 3;
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, sizeof(code));
    uint32_t wh = ldl_le_p(ctx + 12);
    unsigned width = (wh & 65535) + 1, height = (wh >> 16) + 1;
    uint64_t base = ldq_le_p(heap + 8), bottom = ldq_le_p(heap + 16), top = ldq_le_p(heap + 24);
    uint32_t size = ldl_le_p(heap + 4);
    unsigned invalid = 0;
    if (!sha || strcmp(sha, "436ead842d971f1718ac5575fdae3d8f075d752f54f8147b43f70a0ef3056bdd") ||
        ldl_le_p(state + 8) != 0x10001 || ldl_le_p(state + 12) ||
        ldl_le_p(state + 16) != 0x08602201 || ldl_le_p(state + 32) != 0xffff ||
        ldl_le_p(state + 36) || ldl_le_p(state + 48) != 0x98080 ||
        ldl_le_p(state + 60) || ldl_le_p(state + 64)) {
        invalid |= BIT(0);
    }
    if ((ldl_le_p(ctx + 8) & ~0x1ffffU) || width > 4096 || height > 4096) {
        invalid |= BIT(1);
    }
    if (!base || ((base | bottom | top | size) & 4095) || base > bottom ||
        bottom >= top || top >= (UINT64_C(1) << 48) || top - base > size) {
        invalid |= BIT(2);
    }
    if (ldq_le_p(view) || ldq_le_p(view + 8) != UINT64_C(0x42fe000042fe0000) ||
        ldq_le_p(view + 16) != UINT64_C(0x3f80000000000000) ||
        ldl_le_p(view + 24) || ldl_le_p(view + 28) != 0x7f007f ||
        ldl_le_p(p + 8) || ldl_le_p(p + 12) != 0x3f800000) {
        invalid |= BIT(3);
    }
    if (invalid) {
        if (++s->vm3_point_mismatches <= 8) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 point geometry mismatch: mask=0x%x dimensions=%ux%u ctx=0x%x\n",
                invalid, width, height, ldl_le_p(ctx + 8));
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    float x = auto9_g3d00_float(p), y = auto9_g3d00_float(p + 4);
    /* Fraction .25 on each axis puts exactly the containing pixel center
     * inside the unit square, away from the half-open edge ambiguity. */
    /* The scissor can be larger than the target. Bin only a sample that
     * is inside both; never enlarge the framebuffer to the viewport. */
    if (!(x >= 0 && x < 127 && x < width && y >= 0 && y < 127 && y < height) ||
        x - floorf(x) != 0.25f || y - floorf(y) != 0.25f) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *batch = (Auto9G3d00TileBatch){ .ttbr = s->as0_active_ttbr, .context = context,
        .renderer = rsd, .push_uniforms = push, .width = width, .height = height,
        .store_point = true, .point_x = x, .point_y = y };
    *stage = 6;
    return G3D00_JOB_DONE;
}

static bool auto9_vm3_radial_program(const uint8_t rsd[80],
                                    const uint8_t code[256])
{
    if (ldl_le_p(rsd + 8) != 0x10001 || ldl_le_p(rsd + 12) != 0x30000 ||
        ldl_le_p(rsd + 16) != 0x09502a01 || ldl_le_p(rsd + 32) != 0x3750ffff ||
        ldl_le_p(rsd + 36) != 0x44000000 || ldl_le_p(rsd + 48) != 0x9a080 ||
        ldl_le_p(rsd + 60) != 0x08214202 || ldl_le_p(rsd + 64) != 0x201 ||
        (uint32_t)ldl_le_p(rsd + 68) != 0xf0d32503U || (ldl_le_p(rsd + 72) & 3) != 2 ||
        ldl_le_p(rsd + 76) != 0xed000) {
        return false;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, 256);
    return sha && !strcmp(sha, "e1219820f6f8a419f0179311ac7cbb16fce69f1a75548d89a36b337f0148a1cb");
}

static uint32_t auto9_g3d1_vm3_fan_job(Auto9MaliG3d1 *s, uint64_t head,
                                       unsigned slot, Auto9G3d00TileBatch *batch,
                                       unsigned *stage)
{
    enum { LIST_VERTICES = 150 };
    uint8_t job[232], ctx[32], viewport[32], attr[24], buffer[16], heap[32];
    uint8_t positions[LIST_VERTICES * 16], varying[LIST_VERTICES * 16];
    const uint8_t *draw = job + 128;
    int32_t clip_lo[2], clip_hi[2];

    *stage = 1;
    if (slot != 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, head, job, sizeof(job))) {
        return G3D00_JOB_READ_FAULT;
    }
    if (ldl_le_p(job + 40) == 1) {
        return auto9_vm3_store_point_bin(s, job, batch, stage);
    }
    uint32_t start = ldl_le_p(draw + 4);
    /* The atlas copy is a four-vertex non-indexed strip, with RG32F UV. */
    bool atlas_strip = ldl_le_p(job + 40) == 0x1818000a;
    bool list = ldl_le_p(job + 40) == 0x18030008;
    /* Observed local UV/color layouts; unknown lengths still fault. */
    unsigned count_minus_one = ldl_le_p(job + 52);
    unsigned vertices = !list ? 4 : count_minus_one == 17 ? 18 :
                        count_minus_one == 29 ? 30 :
                        count_minus_one == 35 ? 36 :
                        count_minus_one == 71 ? 72 : LIST_VERTICES;
    if ((uint32_t)ldl_le_p(job + 32) != vertices - 1 || ldl_le_p(job + 36) != 0x38000000 ||
        (!atlas_strip && !list && ldl_le_p(job + 40) != 0x1803000c) || ldl_le_p(job + 44) ||
        (atlas_strip && ldl_le_p(job + 48) != 0xffff) ||
        (uint32_t)ldl_le_p(job + 52) != vertices - 1 || ldq_le_p(job + 56) ||
        ldl_le_p(draw) != (atlas_strip ? 0x3 : 0x23) ||
        (atlas_strip && start) || start > UINT16_MAX || ldl_le_p(draw + 8)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    batch->ttbr = s->as0_active_ttbr;
    batch->context = ldq_le_p(job + 72);
    batch->renderer = ldq_le_p(draw + 56);
    batch->textures = ldq_le_p(draw + 32);
    batch->samplers = ldq_le_p(draw + 40);
    batch->push_uniforms = ldq_le_p(draw + 48);
    uint64_t pos = ldq_le_p(draw + 16), vp = ldq_le_p(draw + 96);
    uint64_t attrs = ldq_le_p(draw + 88), buffers = ldq_le_p(draw + 80);
    bool constant = atlas_strip && !attrs && !buffers && batch->push_uniforms &&
                    !batch->textures && !batch->samplers;
    *stage = 2;
    if (!batch->context || !batch->renderer || !pos || !vp ||
        (!constant && (!attrs || !buffers)) ||
        ((batch->context | batch->renderer) & 63) || (vp & 31) ||
        (buffers & 15) || (attrs & 7) || (pos & 15)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, batch->context, ctx, sizeof(ctx)) ||
        !auto9_g3d00_dma_read(s, vp, viewport, sizeof(viewport)) ||
        (!constant &&
         (!auto9_g3d00_dma_read(s, attrs, attr, 8) ||
          !auto9_g3d00_dma_read(s, buffers, buffer, sizeof(buffer))))) {
        return G3D00_JOB_READ_FAULT;
    }
    if (constant) {
        uint8_t rsd[80], program[64];
        if (!auto9_g3d00_dma_read(s, batch->renderer, rsd, sizeof(rsd)) ||
            !ldq_le_p(rsd) || (ldq_le_p(rsd) & 15) ||
            !auto9_g3d00_dma_read(s, ldq_le_p(rsd), program, sizeof(program))) {
            return G3D00_JOB_READ_FAULT;
        }
        g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256,
                                                           program, sizeof(program));
        if (!sha || strcmp(sha, "588dd8ba981ff0cd782ca2ff95937a23e0c0dbcba4cf69bf6a4360211e3f6156") ||
            ldq_le_p(rsd + 8) || ldl_le_p(rsd + 16) != 0x41582201 ||
            ldl_le_p(rsd + 32) != 0x0740ffff || ldl_le_p(rsd + 36) ||
            ldl_le_p(rsd + 48) != 0x22080 || ldl_le_p(rsd + 60) ||
            ldl_le_p(rsd + 64) != 0x200 || (uint32_t)ldl_le_p(rsd + 68) != 0xf0122122U ||
            (ldl_le_p(rsd + 72) & 3) != 1 || ldl_le_p(rsd + 76) != 0x10ed000) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        /* This fragment reads only push uniforms. The tiler consumes no
         * varying data, and must not require or fabricate a varying table. */
        memset(attr, 0, sizeof(attr));
        memset(buffer, 0, sizeof(buffer));
    }
    uint32_t wh = ldl_le_p(ctx + 12), ctx_flags = ldl_le_p(ctx + 8);
    uint32_t minimum = ldl_le_p(viewport + 24), maximum = ldl_le_p(viewport + 28);
    uint64_t heap_va = ldq_le_p(ctx + 24), vary_va = ldq_le_p(buffer);
    uint64_t vary_base = vary_va & UINT64_C(0x0000ffffffffffc0);
    unsigned vary_tag = vary_va >> 56;
    uint32_t attr_word = ldl_le_p(attr);
    /* Select the local varying ABI from its descriptors, not from Offset
     * Start. Neither supported color ABI sets Offset Enable. */
    /* Offset Start is not an ABI discriminator. The original four-vertex
     * fan can start at zero with local, offset-disabled UV/color bindings. */
    bool zero_start_fan = !start && !list && !atlas_strip &&
                          ldl_le_p(job + 40) == 0x1803000c &&
                          ldl_le_p(buffer + 8) == 16 && ldl_le_p(buffer + 12) == 64;
    bool interleaved = !atlas_strip && (start || zero_start_fan) &&
                       attr_word == 0x2bc00000;
    bool color = interleaved || (!atlas_strip && start && attr_word == 0x37c00000);
    bool dual_uv = interleaved && !list && ldl_le_p(buffer + 8) == 24;
    unsigned vary_stride = dual_uv ? 24 : interleaved ? 16 : (color || atlas_strip) ? 8 : 4;
    if (interleaved && !auto9_g3d00_dma_read(s, attrs + 8, attr + 8, 8)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (dual_uv && !auto9_g3d00_dma_read(s, attrs + 16, attr + 16, 8)) {
        return G3D00_JOB_READ_FAULT;
    }
    *stage = 3;
    /* Same storage stride as the older dual-texture fan, but varying 1 is
     * color and varying 2 is a radial coordinate, not a second texture. */
    bool radial_fan = dual_uv && (start == 0x11f8 || start == 0x11fc) &&
                      ldl_le_p(buffer + 12) == 96 && !vary_tag &&
                      batch->push_uniforms && batch->textures && batch->samplers &&
                      ldl_le_p(attr + 8) == 0x37c00000 && !ldl_le_p(attr + 12) &&
                      ldl_le_p(attr + 16) == 0x2bc00000 && ldl_le_p(attr + 20) == 16;
    /* The original tagged fan uses one local 64-byte record for both
     * bindings. Type-1's high byte does not participate in its address. */
    bool tagged_interleaved = vary_tag == 0xff && interleaved && !dual_uv &&
                              !list && ldl_le_p(buffer + 12) == 64 &&
                              ldl_le_p(attr + 8) == 0x37c00000;
    /* Native index-23 fan: same local type-1 RGBA16F/UV buffer, but the
     * captured descriptor carries 0xf0 in the unused divisor byte. */
    bool tagged_linear_fan = interleaved && !dual_uv && !list &&
                             start == 0x23d0 && vary_tag == 0xf0 &&
                             ldl_le_p(buffer + 12) == 64 && !minimum &&
                             maximum == 0x02bb0617 && !batch->push_uniforms &&
                             batch->textures && batch->samplers;
    /* Native index 12 uses the same pinned shader with a different type-1
     * divisor byte on the same local color/dual-UV binding. */
    bool tagged_dual = dual_uv && start == 0x23f0 &&
                       (vary_tag == 0xbf || (vary_tag == 0xff && !minimum &&
                                             maximum == 0x02bb0617)) &&
                       ldl_le_p(buffer + 12) == 96 && !batch->push_uniforms &&
                       batch->textures && batch->samplers;
    bool tagged_color = (vary_tag == 0xff || (vary_tag == 0x3f && start == 0x3fc)) &&
                        color && !interleaved && !list && !atlas_strip &&
                        ldl_le_p(buffer + 12) == 32 && !batch->push_uniforms &&
                        !batch->textures && !batch->samplers;
    /* Each captured list consumes one local RGBA16F + RG32F buffer. */
    bool tagged_a8_list = list && interleaved && vertices == 30 && start == 0xbf1c &&
                          vary_tag == 0xff && ldl_le_p(buffer + 12) == 480 &&
                          !minimum && (maximum == 0x07400617 || maximum == 0x00740617);
    /* This original 36-vertex list uses the same alpha shader but a narrower scissor. */
    bool a8_list36 = list && interleaved && vertices == 36 && start == 0xbef8 &&
                     !vary_tag && ldl_le_p(buffer + 12) == 576 &&
                     !minimum && maximum == 0x003b0617;
    bool a8_native_short = list && interleaved &&
        ((vertices == 150 && start == 0xbf6a && vary_tag == 0xff && ldl_le_p(buffer + 12) == 2400 &&
          !minimum && maximum == 0x02bb0617) ||
         (vertices == 72 && start == 0xbeb0 && !vary_tag && ldl_le_p(buffer + 12) == 1152 &&
          minimum == 0x02120020 && maximum == 0x02bb01db));
    bool local_a8_list = list && interleaved && batch->textures && batch->samplers &&
                       (tagged_a8_list || a8_list36 || a8_native_short ||
                        (vertices == 30 && start == 0xbf3a && vary_tag == 0xc4 &&
                         ldl_le_p(buffer + 12) == 480) ||
                        (vertices == 72 && ldl_le_p(buffer + 12) == 1152 &&
                         ((start == 0xbe74 && (vary_tag == 0 || vary_tag == 0xff)) ||
                          (start == 0xbeb6 && vary_tag == 0))));
    bool atlas_view = atlas_strip && (wh == 0x07ff07ff || wh == 0x03ff07ff) &&
                      (constant ? wh == 0x03ff07ff && !(ctx_flags & ~0x100U) :
                                  ctx_flags == 0x100 && attr_word == 0x2bc04000) &&
                      !ldq_le_p(viewport) && ldl_le_p(viewport + 8) == 0x45000000 &&
                      ldl_le_p(viewport + 12) == (wh == 0x07ff07ff ? 0x45000000 : 0x44800000);
    if ((wh & 65535) >= 4096 || (wh >> 16) >= 4096 || (ctx_flags & ~0x1ffffU) ||
        !heap_va || (heap_va & 63) || (!atlas_strip && start && !color) ||
         ((list || dual_uv) && (!interleaved || (batch->push_uniforms && !radial_fan))) ||
         ((vertices == 36 || vertices == 72) && !local_a8_list) ||
         (atlas_strip ? !atlas_view : color ? (wh != 0x02bb0617 || ctx_flags ||
                   attr_word != (interleaved ? 0x2bc00000 : 0x37c00000))
               : ((attr_word & ~BIT(9)) != 0x33c00000)) ||
         (!constant && (ldl_le_p(attr + 4) != (interleaved ? 8 : 0) ||
         (dual_uv && !radial_fan && (ldl_le_p(attr + 8) != 0x2bc00000 || ldl_le_p(attr + 12) != 16 ||
                      ldl_le_p(attr + 16) != 0x37c00000 || ldl_le_p(attr + 20))) ||
         (interleaved && !dual_uv && (ldl_le_p(attr + 8) != 0x37c00000 || ldl_le_p(attr + 12))) ||
         (uint32_t)ldl_le_p(buffer + 8) != vary_stride ||
         (uint32_t)ldl_le_p(buffer + 12) < vertices * vary_stride || !vary_base ||
         (vary_tag && !tagged_interleaved && !tagged_linear_fan &&
          !tagged_dual && !tagged_color && !local_a8_list &&
          (color || atlas_strip || vary_tag != 0xff || (attr_word & BIT(9)))) ||
         vary_va != (((uint64_t)vary_tag << 56) | vary_base | 1))) ||
        (!atlas_strip && (ldq_le_p(viewport) != UINT64_C(0xff800000ff800000) ||
                         ldq_le_p(viewport + 8) != UINT64_C(0x7f8000007f800000))) ||
        ldq_le_p(viewport + 16) != UINT64_C(0x3f80000000000000)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* Require the original fragment code for these local A8/colour layouts,
     * including the zero-divisor form of the observed 72-vertex list. */
    bool tagged_profile = local_a8_list || tagged_dual || tagged_linear_fan ||
                          (tagged_color && vary_tag == 0x3f);
    if (tagged_profile) {
        uint8_t pc[8], code[272];
        if (tagged_linear_fan) {
            uint8_t texture[32];
            if ((batch->textures | batch->samplers) & 31) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            if (!auto9_g3d00_dma_read(s, batch->textures, texture, sizeof(texture))) {
                return G3D00_JOB_READ_FAULT;
            }
            if (ldl_le_p(texture) != 0x2ec00022 || ldl_le_p(texture + 4) != 0x03ff07ff ||
                ldl_le_p(texture + 8) != 0x2688 || ldl_le_p(texture + 12) ||
                ldq_le_p(texture + 24)) {
                return G3D00_JOB_CONFIG_FAULT;
            }
        }
        if (tagged_a8_list || a8_list36 || a8_native_short) {
            uint8_t texture[32];
            if (!auto9_g3d00_dma_read(s, batch->textures, texture, sizeof(texture))) {
                return G3D00_JOB_READ_FAULT;
            }
            /* Keep the two observed scissor/atlas pairs together. The
             * fragment path separately validates the actual texture span. */
            uint32_t dims = tagged_a8_list && maximum == 0x07400617 ?
                            0x07ff07ff : 0x03ff07ff;
            if (ldl_le_p(texture) != 0x06839422 ||
                (uint32_t)ldl_le_p(texture + 4) != dims || ldl_le_p(texture + 8) != 0x1688) {
                return G3D00_JOB_CONFIG_FAULT;
            }
        }
        unsigned size = tagged_dual ? sizeof(code) : (local_a8_list || tagged_linear_fan) ? 64 : 32;
        if (!auto9_g3d00_dma_read(s, batch->renderer, pc, sizeof(pc))) {
            return G3D00_JOB_READ_FAULT;
        }
        uint64_t shader = ldq_le_p(pc);
        if (!shader || (shader & 15) || shader >= (UINT64_C(1) << 48)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!auto9_g3d00_dma_read(s, shader, code, size)) {
            return G3D00_JOB_READ_FAULT;
        }
        g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, size);
        const char *expected = tagged_dual ?
            "229b76b0d860ef1bbc813f34d74f94a06aa0b03292e7478d52b723278f3b953d" : local_a8_list ?
            "5878d902768842639c143f49cd0777375af7c53979ac3a0c6405d39986ce4268" : tagged_linear_fan ?
            "89f988de48c3b0d1be7378d02bbdcb0514c67301e54fd379cb8109f22d2ba56d" :
            "d263dd306bf49ae57533928edc687ab36073622175d9ff3298a138d1e68f889f";
        if (!sha || strcmp(sha, expected)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    if (radial_fan) {
        uint8_t rsd[80], code[256];
        if (!auto9_g3d00_dma_read(s, batch->renderer, rsd, sizeof(rsd))) {
            return G3D00_JOB_READ_FAULT;
        }
        uint64_t shader = ldq_le_p(rsd);
        if (!shader || (shader & 15) || shader >= (UINT64_C(1) << 48)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!auto9_g3d00_dma_read(s, shader, code, sizeof(code))) {
            return G3D00_JOB_READ_FAULT;
        }
        if (!auto9_vm3_radial_program(rsd, code)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    batch->width = (wh & 65535) + 1;
    batch->height = (wh >> 16) + 1;
    *stage = 4;
    /* The tagged fan's scissor exceeds the attachment. Preserve the
     * origin and intersect its upper bounds with the target. */
    if ((!tagged_interleaved && !tagged_a8_list && ((maximum & 65535) >= batch->width ||
                                 (maximum >> 16) >= batch->height)) ||
        (minimum & 65535) >= batch->width || (minimum >> 16) >= batch->height ||
        (minimum & 65535) > (maximum & 65535) || (minimum >> 16) > (maximum >> 16)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    clip_lo[0] = (minimum & 65535) * 256;
    clip_lo[1] = (minimum >> 16) * 256;
    clip_hi[0] = MIN((maximum & 65535) + 1, batch->width) * 256;
    clip_hi[1] = MIN((maximum >> 16) + 1, batch->height) * 256;
    *stage = 5;
    /* The position pointer and these varyings name already-produced local
     * vertices. Do not apply Offset Start a second time. */
    if (!auto9_g3d00_dma_read(s, heap_va, heap, sizeof(heap)) ||
        !auto9_g3d00_dma_read(s, pos, positions, vertices * 16) ||
        (!constant && !auto9_g3d00_dma_read(s, vary_base, varying, vertices * vary_stride))) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t base = ldq_le_p(heap + 8), bottom = ldq_le_p(heap + 16), top = ldq_le_p(heap + 24);
    uint32_t size = ldl_le_p(heap + 4);
    if (!base || ((base | bottom | top | size) & 4095) || base > bottom ||
        bottom >= top || top >= (UINT64_C(1) << 48) || top - base > size) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = 6;
    bool clipped = false;
    for (unsigned i = 0; i < (list ? vertices / 3 : 2); i++) {
        const unsigned vertex[] = { list ? 3 * i : atlas_strip && i == 1 ? 2 : 0,
                                    list ? 3 * i + 1 : atlas_strip && i == 1 ? 1 : i + 1,
                                    list ? 3 * i + 2 : i + 2 };
        Auto9G3d00Triangle tri = {0};
        bool negative = false;
        for (unsigned j = 0; j < 3; j++) {
            unsigned v = vertex[j];
            const uint8_t *p = positions + v * 16;
            float window[4] = {auto9_g3d00_float(p), auto9_g3d00_float(p + 4), 0, 0};
            /* Unlike the pinned application vertex jobs, a driver-emitted
             * constant rectangle can supply unsnapped window coordinates. */
            if (constant && !auto9_bifrost_snap4(window)) {
                *stage = 61;
                return G3D00_JOB_CONFIG_FAULT;
            }
            for (unsigned a = 0; a < 2; a++) {
                float xy = window[a] * 256.0f;
                /* The driver rectangle uses 65536 as an offscreen
                 * corner. 24.8 edges still fit safely in signed 64 bits. */
                if (!isfinite(xy) || fabsf(xy) > (constant ? 0x1p24f : 0x1p23f)) {
                    *stage = (color || constant) ? 61 : 6;
                    return G3D00_JOB_CONFIG_FAULT;
                }
                if (truncf(xy) != xy) {
                    *stage = (color || constant) ? 62 : 6;
                    return G3D00_JOB_CONFIG_FAULT;
                }
                tri.xy[j][a] = xy;
            }
            for (unsigned a = 0; a < (color ? 4 : atlas_strip ? 0 : 2); a++) {
                uint16_t half = lduw_le_p(varying + v * vary_stride + a * 2);
                if ((half & 0x7c00) == 0x7c00) {
                    *stage = color ? 63 : 6;
                    return G3D00_JOB_CONFIG_FAULT;
                }
                if (color) {
                    tri.rgba[j][a] = half;
                } else {
                    tri.uv[j][a] = half;
                }
            }
            if (interleaved || (atlas_strip && !constant)) {
                for (unsigned a = 0; a < 2; a++) {
                    tri.uv32[j][a] = auto9_g3d00_float(varying + v * vary_stride +
                                                       (interleaved ? 8 : 0) + 4 * a);
                    if (!isfinite(tri.uv32[j][a])) {
                        *stage = 67;
                        return G3D00_JOB_CONFIG_FAULT;
                    }
                }
            }
            if (dual_uv) {
                for (unsigned a = 0; a < 2; a++) {
                    tri.uv2[j][a] = auto9_g3d00_float(varying + v * vary_stride + 16 + a * 4);
                    if (!isfinite(tri.uv2[j][a])) {
                        *stage = 67;
                        return G3D00_JOB_CONFIG_FAULT;
                    }
                }
            }
            tri.z[j] = auto9_g3d00_float(p + 8);
            tri.inv_w[j] = auto9_g3d00_float(p + 12);
            if (constant ? !isfinite(tri.z[j]) : !(tri.z[j] >= 0 && tri.z[j] <= 1)) {
                *stage = (color || constant) ? 64 : 6;
                return G3D00_JOB_CONFIG_FAULT;
            }
            if (!isfinite(tri.inv_w[j]) || tri.inv_w[j] == 0 ||
                (!color && !constant && tri.inv_w[j] < 0)) {
                if (color && !interleaved) {
                    auto9_vm3_color_origin(s, pos, vary_base, positions, varying);
                }
                *stage = (color || constant) ? 65 : 6;
                return G3D00_JOB_CONFIG_FAULT;
            }
            negative |= tri.inv_w[j] < 0 ||
                        (constant && (tri.z[j] < 0 || tri.z[j] > 1));
        }
        if (negative) {
            if (!clipped && color && !interleaved) {
                auto9_vm3_color_origin(s, pos, vary_base, positions, varying);
            }
            clipped = true;
            if (!auto9_vm3_clip_color_triangle(batch, &tri, clip_lo, clip_hi)) {
                *stage = 66;
                return G3D00_JOB_CONFIG_FAULT;
            }
        } else if (auto9_g3d00_bin_triangle(&tri, clip_lo, clip_hi)) {
            if (batch->count >= G_N_ELEMENTS(batch->triangle)) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            batch->triangle[batch->count++] = tri;
        }
    }
    batch->color_fan = color && !interleaved;
    batch->color_clipped = clipped;
    batch->interleaved_fan = interleaved;
    batch->dual_uv = dual_uv && !radial_fan;
    batch->radial_fan = radial_fan;
    batch->tagged_interleaved = tagged_interleaved;
    batch->triangle_list = list;
    batch->atlas_strip = atlas_strip;
    batch->atlas_constant = constant;
    if ((tagged_dual || a8_native_short) && s->vm3_tagged_tiler_traces < 16 &&
        qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        ++s->vm3_tagged_tiler_traces;
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 native HMI geometry: first=0x%x vertices=%u tag=0x%x triangles=%u dual=%u\n",
            start, vertices, vary_tag, batch->count, (unsigned)tagged_dual);
    }
    if (radial_fan) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 radial fan binned: first=0x%x triangles=%u\n",
                      start, batch->count);
    }
    if (tagged_profile && s->vm3_tagged_tiler_traces < 16 &&
        qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        unsigned n = ++s->vm3_tagged_tiler_traces;
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 tagged type7 binned: n=%u index=%u first=%u "
            "vertices=%u tag=0x%x triangles=%u\n",
            n, (unsigned)(ldl_le_p(job + 16) >> 16) & 65535, start,
            vertices, vary_tag, batch->count);
    }
    return G3D00_JOB_DONE;
}

/* The two real slot-1 jobs supplied the same non-instanced, linear ABI.
 * Decode addresses from the guest descriptors; never reuse captured VAs or
 * vertex values. Commit both varyings before the caller completes the job. */
static uint32_t auto9_g3d00_viewport_job(Auto9MaliG3d1 *s, uint64_t head,
                                        unsigned slot, uint64_t status_pa,
                                        unsigned *stage)
{
    enum { MAX_VERTICES = 256, SOURCE_BYTES = 20, DESCRIPTOR_BYTES = 192 };
    uint8_t invocation[8], draw[128], renderer[64], uniforms[32];
    uint8_t shader[AUTO9_BIFROST_VIEWPORT_PROGRAM_SIZE];
    uint8_t attr[16], vary[16], source[16], target[32];
    uint8_t rgba[MAX_VERTICES * 8], position[MAX_VERTICES * 16];
    struct {
        uint64_t pa;
        unsigned bytes, stream, offset;
    } spans[6]; /* Descriptor and two varyings, at most two pages each. */
    unsigned span_count = 0, first_store = 0;
    uint64_t va[3] = { head }, input;
    const uint8_t *data[3] = { NULL, rgba, position };
    const unsigned width[2] = { 8, 16 };
    uint32_t count, stride;
    float u[8];

    *stage = 1; /* Slot, then descriptor and invocation. */
    if (slot != 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = 2;
    if (!auto9_g3d00_dma_read(s, head + 32, invocation, sizeof(invocation)) ||
        !auto9_g3d00_dma_read(s, head + 64, draw, sizeof(draw))) {
        return G3D00_JOB_READ_FAULT;
    }
    /* 1xNx1 workgroups of one thread; no indexed or instanced address bias. */
    count = ldl_le_p(invocation);
    if (count >= MAX_VERTICES || ldl_le_p(invocation + 4) != 0x38000000 ||
        ldl_le_p(draw) != 2 || ldq_le_p(draw + 4) || ldq_le_p(draw + 16)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    count++;
    *stage = 3; /* Pointer layout and renderer state. */
    uint64_t state = ldq_le_p(draw + 56), fau = ldq_le_p(draw + 48);
    uint64_t attributes = ldq_le_p(draw + 72), varyings = ldq_le_p(draw + 88);
    uint64_t ab = ldq_le_p(draw + 64), vb = ldq_le_p(draw + 80);
    if (!state || !fau || !attributes || !varyings || !ab || !vb ||
        (state & 63) || ((fau | attributes | varyings) & 7) || ((ab | vb) & 31)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, state, renderer, sizeof(renderer))) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t shader_va = ldq_le_p(renderer);
    if (!shader_va || (shader_va & 15) || ldl_le_p(renderer + 8) ||
        ldl_le_p(renderer + 12) != 0x00020002 ||
        ldl_le_p(renderer + 48) != 0x26000) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = 4; /* Program mapping and identity. */
    if (!auto9_g3d00_dma_read(s, shader_va, shader, sizeof(shader))) {
        return G3D00_JOB_READ_FAULT;
    }
    char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256, shader, sizeof(shader));
    bool match = digest && !strcmp(digest, AUTO9_BIFROST_VIEWPORT_PROGRAM_SHA256);
    g_free(digest);
    if (!match) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = 5; /* Attribute/varying ABI and source shape. */
    if (!auto9_g3d00_dma_read(s, attributes, attr, sizeof(attr)) ||
        !auto9_g3d00_dma_read(s, varyings, vary, sizeof(vary)) ||
        !auto9_g3d00_dma_read(s, ab, source, sizeof(source)) ||
        !auto9_g3d00_dma_read(s, vb, target, sizeof(target)) ||
        !auto9_g3d00_dma_read(s, fau, uniforms, sizeof(uniforms))) {
        return G3D00_JOB_READ_FAULT;
    }
    /* RGBA32F, RGBA8_UNORM -> RGBA16F, SNAP_4; RGBA component order. */
    if (ldl_le_p(attr) != ((0xbf000U << 10) | BIT(9)) ||
        ldl_le_p(attr + 4) || ldl_le_p(attr + 8) != ((0xbb000U << 10) | BIT(9)) ||
        ldl_le_p(attr + 12) != 16 || ldl_le_p(vary) != (0xdf000U << 10) ||
        ldl_le_p(vary + 4) || ldl_le_p(vary + 8) != ((0x5e000U << 10) | 1) ||
        ldl_le_p(vary + 12)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    input = ldq_le_p(source);
    stride = ldl_le_p(source + 8);
    uint32_t source_size = ldl_le_p(source + 12);
    if ((input & UINT64_C(0xff0000000000003f)) != 1 ||
        stride < SOURCE_BYTES || (stride & 3) ||
        (uint64_t)stride * (count - 1) + SOURCE_BYTES > source_size) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    input &= ~UINT64_C(63);
    *stage = 6; /* Output mapping and alias checks. */
    if (!input) {
        return G3D00_JOB_WRITE_FAULT;
    }
    unsigned size[3] = { DESCRIPTOR_BYTES, count * width[0], count * width[1] };
    for (unsigned i = 0; i < 2; i++) {
        const uint8_t *b = target + i * 16;
        uint64_t tagged = ldq_le_p(b);
        uint32_t target_stride = ldl_le_p(b + 8), target_size = ldl_le_p(b + 12);
        if ((tagged & UINT64_C(0xff0000000000003f)) != 1 ||
            target_stride != width[i] || size[i + 1] > target_size) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        va[i + 1] = tagged & ~UINT64_C(63);
    }
    /* Preflight all pages before rendering. GPU-adjacent pages need not be
     * adjacent in physical memory. Reject aliases, including the second
     * descriptor page, before the first varying can be written. */
    for (unsigned stream = 0; stream < G_N_ELEMENTS(va); stream++) {
        if (stream == 1) {
            first_store = span_count;
        }
        if (!va[stream] || va[stream] >= (UINT64_C(1) << 48) ||
            size[stream] > (UINT64_C(1) << 48) - va[stream]) {
            return G3D00_JOB_WRITE_FAULT;
        }
        for (unsigned off = 0; off < size[stream]; ) {
            uint64_t at = va[stream] + off, pa;
            unsigned n = MIN(size[stream] - off,
                             AUTO9_G3D00_GPU_PAGE - (at & (AUTO9_G3D00_GPU_PAGE - 1)));
            if ((!s->g3d00 && n != size[stream]) ||
                span_count >= G_N_ELEMENTS(spans) ||
                !auto9_g3d00_translate(s, at, n, stream != 0, &pa) ||
                (!span_count && pa != status_pa)) {
                return G3D00_JOB_WRITE_FAULT;
            }
            for (unsigned i = 0; i < span_count; i++) {
                if (auto9_g3d00_overlap(pa, n, spans[i].pa, spans[i].bytes)) {
                    return G3D00_JOB_WRITE_FAULT;
                }
            }
            spans[span_count].pa = pa;
            spans[span_count].bytes = n;
            spans[span_count].stream = stream;
            spans[span_count++].offset = off;
            off += n;
        }
    }
    *stage = 7; /* Vertex reads and numeric execution. */
    for (unsigned i = 0; i < 8; i++) {
        uint32_t word = ldl_le_p(uniforms + 4 * i);
        memcpy(&u[i], &word, sizeof(word));
    }
    for (unsigned i = 0; i < count; i++) {
        uint8_t raw[SOURCE_BYTES];
        uint16_t color[4];
        float p[4], out[4];
        if (!auto9_g3d00_dma_read(s, input + (uint64_t)i * stride, raw, sizeof(raw))) {
            return G3D00_JOB_READ_FAULT;
        }
        for (unsigned j = 0; j < 4; j++) {
            uint32_t word = ldl_le_p(raw + 4 * j);
            memcpy(&p[j], &word, sizeof(word));
        }
        if (!auto9_bifrost_viewport_vertex(p, raw + 16, u, color, out)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned j = 0; j < 4; j++) {
            uint32_t word;
            rgba[i * 8 + j * 2] = color[j];
            rgba[i * 8 + j * 2 + 1] = color[j] >> 8;
            memcpy(&word, &out[j], sizeof(word));
            stl_le_p(position + i * 16 + j * 4, word);
        }
    }
    *stage = 8; /* Actual DMA store. */
    for (unsigned i = first_store; i < span_count; i++) {
        if (address_space_write(&address_space_memory, spans[i].pa, MEMTXATTRS_UNSPECIFIED,
                                data[spans[i].stream] + spans[i].offset, spans[i].bytes) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    s->vertices_written += count;
    s->qnx_viewport_cross_page_jobs += s->g3d00 && span_count > 3;
    return G3D00_JOB_DONE;
}

/* Exact OEM VM3 four-vertex program. Unlike G3D00, both attributes are
 * RG32F in one buffer, with separate RG16F and SNAP_4 outputs. */
static uint32_t auto9_g3d1_stock_viewport_job(Auto9MaliG3d1 *s, uint64_t head,
                                              unsigned slot, uint64_t status_pa)
{
    uint8_t invocation[8], draw[96], renderer[64], code[AUTO9_BIFROST_VM3_VIEWPORT_SIZE];
    uint8_t attr[16], vary[16], source[16], target[32], raw[64], fau[112];
    uint8_t texcoord[16], position[64];
    uint64_t dst[2], source_pa;
    const unsigned stride[2] = { 4, 16 }, len[2] = { 16, 64 };
    const uint64_t linear = UINT64_C(0xff0000000000003f);
    float uniform[28];

    s->vm3_vertex336_stage = 1;
    if (!s->vm3_control || slot != 1) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, head + 32, invocation, sizeof(invocation)) ||
        !auto9_g3d00_dma_read(s, head + 64, draw, sizeof(draw))) {
        return G3D00_JOB_READ_FAULT;
    }
    if (ldl_le_p(invocation) != 3 || ldl_le_p(invocation + 4) != 0x38000000 ||
        ldl_le_p(draw) != 2 || ldq_le_p(draw + 4) || ldq_le_p(draw + 16)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t state = ldq_le_p(draw + 56), fau_va = ldq_le_p(draw + 48);
    uint64_t attributes = ldq_le_p(draw + 72), varyings = ldq_le_p(draw + 88);
    uint64_t ab = ldq_le_p(draw + 64), vb = ldq_le_p(draw + 80);
    s->vm3_vertex336_stage = 2;
    if (!state || !fau_va || !attributes || !varyings || !ab || !vb ||
        (state & 63) || ((fau_va | attributes | varyings) & 7) || ((ab | vb) & 31)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, state, renderer, sizeof(renderer))) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t shader = ldq_le_p(renderer);
    s->vm3_vertex336_stage = 3;
    if (!shader || (shader & 15) || ldl_le_p(renderer + 8) ||
        ldl_le_p(renderer + 12) != 0x00020002 || ldl_le_p(renderer + 48) != 0x76000) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_dma_read(s, shader, code, sizeof(code))) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, sizeof(code));
    if (!sha || strcmp(sha, AUTO9_BIFROST_VM3_VIEWPORT_SHA256)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    s->vm3_vertex336_stage = 4;
    if (!auto9_g3d00_dma_read(s, attributes, attr, sizeof(attr)) ||
        !auto9_g3d00_dma_read(s, varyings, vary, sizeof(vary)) ||
        !auto9_g3d00_dma_read(s, ab, source, sizeof(source)) ||
        !auto9_g3d00_dma_read(s, vb, target, sizeof(target)) ||
        !auto9_g3d00_dma_read(s, fau_va, fau, sizeof(fau))) {
        return G3D00_JOB_READ_FAULT;
    }
    if (ldl_le_p(attr) != 0x2bc04200 || ldl_le_p(attr + 4) ||
        ldl_le_p(attr + 8) != 0x2bc04200 || ldl_le_p(attr + 12) != 8 ||
        ldl_le_p(vary) != 0x33c00000 || ldl_le_p(vary + 4) ||
        ldl_le_p(vary + 8) != 0x17800001 || ldl_le_p(vary + 12)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t src = ldq_le_p(source);
    uint32_t src_size = ldl_le_p(source + 12);
    s->vm3_vertex336_stage = 5;
    if ((src & linear) != 1 || ldl_le_p(source + 8) != 16 || src_size < sizeof(raw)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    src &= ~UINT64_C(63);
    if (!src || !auto9_g3d00_translate(s, src, sizeof(raw), false, &source_pa) ||
        !auto9_g3d00_dma_read(s, src, raw, sizeof(raw))) {
        return G3D00_JOB_READ_FAULT;
    }
    unsigned output_tag = ldq_le_p(target) >> 56;
    /* The OEM also emits 0xff in the divisor byte on both 1D stores.
     * It is not part of the 48-bit VA or an instancing divisor for type 1. */
    if (output_tag != 0 && output_tag != 0xff) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned i = 0; i < 2; i++) {
        const uint8_t *b = target + i * 16;
        uint64_t tagged = ldq_le_p(b);
        uint64_t va = tagged & UINT64_C(0x0000ffffffffffc0);
        uint32_t b_stride = ldl_le_p(b + 8), b_size = ldl_le_p(b + 12);
        if (tagged != (((uint64_t)output_tag << 56) | va | 1) ||
            b_stride != stride[i] || b_size < len[i]) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!va || !auto9_g3d00_translate(s, va, len[i], true, &dst[i]) ||
            auto9_g3d00_overlap(dst[i], len[i], source_pa, sizeof(raw))) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    s->vm3_vertex336_stage = 6;
    if (auto9_g3d00_overlap(dst[0], len[0], dst[1], len[1])) {
        return G3D00_JOB_WRITE_FAULT;
    }
    /* The chain descriptor may cross a GPU page. Check both physical
     * pieces before any output or status write. */
    for (unsigned off = 0; off < 192; ) {
        unsigned span = MIN(192 - off, AUTO9_G3D00_GPU_PAGE - ((head + off) & 4095));
        uint64_t pa;
        if (!auto9_g3d00_translate(s, head + off, span, false, &pa) ||
            (!off && pa != status_pa) ||
            auto9_g3d00_overlap(dst[0], len[0], pa, span) ||
            auto9_g3d00_overlap(dst[1], len[1], pa, span)) {
            return G3D00_JOB_WRITE_FAULT;
        }
        off += span;
    }
    const uint64_t inputs[] = {state, shader, attributes, varyings, ab, vb, fau_va};
    const unsigned sizes[] = {sizeof(renderer), sizeof(code), sizeof(attr),
                              sizeof(vary), sizeof(source), sizeof(target), sizeof(fau)};
    for (unsigned i = 0; i < G_N_ELEMENTS(inputs); i++) {
        for (unsigned off = 0; off < sizes[i]; ) {
            uint64_t va = inputs[i] + off, pa;
            unsigned span = MIN(sizes[i] - off, AUTO9_G3D00_GPU_PAGE - (va & 4095));
            if (!auto9_g3d00_translate(s, va, span, false, &pa) ||
                auto9_g3d00_overlap(dst[0], len[0], pa, span) ||
                auto9_g3d00_overlap(dst[1], len[1], pa, span)) {
                return G3D00_JOB_WRITE_FAULT;
            }
            off += span;
        }
    }
    s->vm3_vertex336_stage = 7;
    for (unsigned i = 0; i < G_N_ELEMENTS(uniform); i++) {
        uint32_t word = ldl_le_p(fau + 4 * i);
        memcpy(&uniform[i], &word, 4);
    }
    for (unsigned i = 0; i < 4; i++) {
        float xyuv[4], out[4];
        uint16_t uv[2];
        for (unsigned j = 0; j < 4; j++) {
            uint32_t word = ldl_le_p(raw + i * 16 + j * 4);
            memcpy(&xyuv[j], &word, 4);
        }
        if (!auto9_bifrost_vm3_viewport(xyuv, uniform, uv, out)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned j = 0; j < 2; j++) {
            stw_le_p(texcoord + 4 * i + 2 * j, uv[j]);
        }
        for (unsigned j = 0; j < 4; j++) {
            uint32_t word;
            memcpy(&word, &out[j], 4);
            stl_le_p(position + 16 * i + 4 * j, word);
        }
    }
    s->vm3_vertex336_stage = 8;
    if (address_space_write(&address_space_memory, dst[0], MEMTXATTRS_UNSPECIFIED,
                            texcoord, sizeof(texcoord)) != MEMTX_OK ||
        address_space_write(&address_space_memory, dst[1], MEMTXATTRS_UNSPECIFIED,
                            position, sizeof(position)) != MEMTX_OK) {
        return G3D00_JOB_WRITE_FAULT;
    }
    s->vertices_written += 4;
    s->vm3_stock_vertices_written += 4;
    if (output_tag) {
        s->vm3_vertex336_tagged_jobs++;
    }
    if (output_tag ? s->vm3_vertex336_tagged_jobs <= 8 : s->vm3_stock_vertices_written <= 32) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 vertex336 writeback: tag=0x%x vertices=4 position-bytes=64 uv-bytes=16\n",
            output_tag);
    }
    return G3D00_JOB_DONE;
}

static bool auto9_g3d00_fragment_shader(Auto9MaliG3d1 *s, uint64_t va)
{
    static const uint32_t state[] = {
        0, 0x00010000, 0x48182200, 0, 0, 0, 0x3950ffff, 0x4500ffff,
        0x0007ff00, 0x0007ff00, 0x00002080, 0, 0, 0x00001a01,
        0x00000200, 0xf0122122, 0x00000019, 0x000ed000,
    };
    uint8_t rsd[80], code[32];

    if (!auto9_g3d00_dma_read(s, va, rsd, sizeof(rsd))) {
        return false;
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(state); i++) {
        if (ldl_le_p(rsd + 8 + i * 4) != state[i]) {
            return false;
        }
    }
    if (!auto9_g3d00_dma_read(s, ldq_le_p(rsd), code, sizeof(code))) {
        return false;
    }
    char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, sizeof(code));
    bool match = sha && !strcmp(sha,
        "d263dd306bf49ae57533928edc687ab36073622175d9ff3298a138d1e68f889f");
    g_free(sha);
    return match;
}

static int64_t auto9_g3d00_edge(const int32_t a[2], const int32_t b[2],
                                int32_t x, int32_t y)
{
    return (int64_t)(b[0] - a[0]) * (y - a[1]) -
           (int64_t)(b[1] - a[1]) * (x - a[0]);
}

static bool auto9_g3d00_top_left(const int32_t a[2], const int32_t b[2])
{
    return b[1] < a[1] || (b[1] == a[1] && b[0] > a[0]);
}

static float auto9_g3d00_half(uint16_t h)
{
    unsigned exponent = h >> 10, mantissa = h & 1023;
    return exponent ? ldexpf(1024 + mantissa, (int)exponent - 25)
                    : ldexpf(mantissa, -24);
}

/* Execute the captured fragment profile: one centre sample, top-left
 * coverage, fixed-function D24 LESS, and an opaque RGBA16F varying store.
 * ATEST only forwards coverage here (no alpha-to-coverage); the pinned
 * BLEND descriptor converts to linear RGBA8 and the RT swizzle writes BGRA. */
static unsigned auto9_g3d00_raster_triangle(const Auto9G3d00Triangle *tri,
                                            unsigned width, uint32_t *depth,
                                            uint8_t *bgra)
{
    unsigned v[3] = {0, 1, 2}, count = 0;
    int64_t area = auto9_g3d00_edge(tri->xy[0], tri->xy[1], tri->xy[2][0], tri->xy[2][1]);
    if (area < 0) {
        v[1] = 2;
        v[2] = 1;
        area = -area;
    }
    if (!area) {
        return 0;
    }
    bool inclusive[3];
    float color[3][4];
    for (unsigned j = 0; j < 3; j++) {
        inclusive[j] = auto9_g3d00_top_left(tri->xy[v[(j + 1) % 3]],
                                          tri->xy[v[(j + 2) % 3]]);
        for (unsigned c = 0; c < 4; c++) {
            color[j][c] = auto9_g3d00_half(tri->rgba[v[j]][c]);
        }
    }
    for (unsigned y = tri->pixel[1]; y <= tri->pixel[3]; y++) {
        for (unsigned x = tri->pixel[0]; x <= tri->pixel[2]; x++) {
            int64_t e[3];
            bool inside = true;
            for (unsigned j = 0; j < 3; j++) {
                e[j] = auto9_g3d00_edge(tri->xy[v[(j + 1) % 3]],
                                       tri->xy[v[(j + 2) % 3]],
                                       x * 256 + 128, y * 256 + 128);
                inside &= e[j] > 0 || (!e[j] && inclusive[j]);
            }
            if (!inside) {
                continue;
            }
            unsigned i = y * width + x;
            double z = 0, weights[3], sum = 0;
            for (unsigned j = 0; j < 3; j++) {
                z += (double)e[j] * tri->z[v[j]];
                weights[j] = (double)e[j] * tri->inv_w[v[j]];
                sum += weights[j];
            }
            uint32_t d24 = floor(z * 16777215.0 / area + 0.5);
            if (d24 >= depth[i] || !(sum > 0)) {
                continue;
            }
            depth[i] = d24;
            for (unsigned c = 0; c < 4; c++) {
                unsigned channel = c < 3 ? 2 - c : c;
                double value = 0;
                for (unsigned j = 0; j < 3; j++) {
                    value += weights[j] * color[j][channel];
                }
                bgra[i * 4 + c] = floor(value * 255.0 / sum + 0.5);
            }
            count++;
        }
    }
    return count;
}

/* TE affects which tile stores may be skipped, not their color contents.
 * Validate the original per-tile CRC allocation but never rely on it. */
static bool auto9_g3d00_crc_span(Auto9MaliG3d1 *s, const uint8_t ext[64],
                                unsigned width, unsigned height)
{
    uint64_t base = ldq_le_p(ext), pa;
    uint32_t stride = ldl_le_p(ext + 8);
    unsigned row = ((width + 15) / 16) * 8;
    unsigned rows = (height + 15) / 16;
    uint64_t bytes = (uint64_t)(rows - 1) * stride + row;
    if (!base || (base & 7) || (stride & 7) || stride < row ||
        stride > 4096 || ((ldl_le_p(ext + 12) >> 11) & 15) ||
        base >= (UINT64_C(1) << 48) || bytes > (UINT64_C(1) << 48) - base) {
        return false;
    }
    while (bytes) {
        unsigned len = MIN(bytes, 4096 - (base & 4095));
        if (!auto9_g3d00_translate(s, base, len, true, &pa)) {
            return false;
        }
        base += len;
        bytes -= len;
    }
    return true;
}

typedef struct Auto9Vm3ClearSpan {
    uint64_t pa;
    unsigned length;
} Auto9Vm3ClearSpan;

static int auto9_vm3_clear_span_cmp(const void *a, const void *b)
{
    uint64_t pa = ((const Auto9Vm3ClearSpan *)a)->pa;
    uint64_t pb = ((const Auto9Vm3ClearSpan *)b)->pa;
    return (pa > pb) - (pa < pb);
}

/* Split only at GPU page boundaries. The address space walker still owns
 * the read/write permission and VM3 private-RAM checks for every span. */
static bool auto9_vm3_clear_plan(Auto9MaliG3d1 *s, uint64_t base, unsigned rows,
                                  unsigned pitch, unsigned active, bool write,
                                  Auto9Vm3ClearSpan *out, unsigned limit, unsigned *used)
{
    uint64_t extent = (uint64_t)(rows - 1) * pitch + active;
    if (!base || base >= (UINT64_C(1) << 48) || extent > (UINT64_C(1) << 48) - base) {
        return false;
    }
    for (unsigned row = 0; row < rows; row++) {
        uint64_t va = base + (uint64_t)row * pitch;
        unsigned remaining = active;
        while (remaining) {
            unsigned n = MIN(remaining, 4096 - (va & 4095));
            uint64_t pa;
            if (*used >= limit || !auto9_g3d00_translate(s, va, n, write, &pa)) {
                return false;
            }
            out[(*used)++] = (Auto9Vm3ClearSpan){ pa, n };
            va += n;
            remaining -= n;
        }
    }
    return true;
}

/* Hash logical rows of the preflighted DMA plan; never expose addresses.
 * Merge contiguous chunks within each row so GPU and DRM page sizes need
 * not agree. Width and height are part of the byte-level format. */
static void auto9_vm3_center_footprint(const Auto9Vm3ClearSpan *spans, unsigned count,
                                     char digest[65])
{
    g_autoptr(GChecksum) hash = g_checksum_new(G_CHECKSUM_SHA256);
    uint8_t item[12];
    unsigned left = 1560 * 4, run = 0;
    uint64_t start = 0;
    stl_le_p(item, 1560 * 4);
    stl_le_p(item + 4, 700);
    g_checksum_update(hash, item, 8);
    for (unsigned i = 0; i < count; i++) {
        if (run && start + run != spans[i].pa) {
            stq_le_p(item, start);
            stl_le_p(item + 8, run);
            g_checksum_update(hash, item, sizeof(item));
            run = 0;
        }
        if (!run) {
            start = spans[i].pa;
        }
        run += spans[i].length;
        left -= spans[i].length;
        if (!left) {
            stq_le_p(item, start);
            stl_le_p(item + 8, run);
            g_checksum_update(hash, item, sizeof(item));
            left = 1560 * 4;
            run = 0;
        }
    }
    memcpy(digest, g_checksum_get_string(hash), 65);
}

/* Only call after successful writes of the original 1560x700 target. */
static void auto9_vm3_center_record(Auto9MaliG3d1 *s, const char footprint[65],
                                  unsigned pitch, unsigned bytes, unsigned rgb,
                                  const uint8_t *image)
{
    Auto9Vm3CenterTarget *target = NULL, *oldest = &s->vm3_center_target[0];
    if (!bytes) {
        return;
    }
    for (unsigned i = 0; i < AUTO9_VM3_CENTER_TARGETS; i++) {
        Auto9Vm3CenterTarget *row = &s->vm3_center_target[i];
        if (row->generation && !strcmp(row->footprint, footprint)) {
            target = row;
            break;
        }
        if (row->generation < oldest->generation) {
            oldest = row;
        }
    }
    if (!target) {
        target = oldest;
        s->vm3_center_evictions += target->generation != 0;
        memset(target, 0, sizeof(*target));
        memcpy(target->footprint, footprint, sizeof(target->footprint));
    }
    target->generation = ++s->vm3_center_generation;
    target->time_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    target->pitch = pitch;
    target->bytes = bytes;
    target->rgb = rgb;
    target->clear = !image;
    target->content[0] = 0;
    if (rgb) {
        target->color_generation = target->generation;
        target->color_time_ns = target->time_ns;
    }
    /* The resolve scratch buffer has no valid bytes for untouched tiles. */
    if (image && bytes == 1560 * 700 * 4) {
        g_autofree char *content = g_compute_checksum_for_data(G_CHECKSUM_SHA256, image, bytes);
        memcpy(target->content, content, sizeof(target->content));
    }
}

static char *auto9_vm3_center_history(Object *obj, Error **errp)
{
    Auto9MaliG3d1 *s = AUTO9_MALI_G3D1(obj);
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    GString *result = g_string_new(NULL);
    g_string_append_printf(result, "{\"version\":1,\"generation\":%" PRIu64
                           ",\"evictions\":%" PRIu64 ",\"targets\":[",
                           s->vm3_center_generation, s->vm3_center_evictions);
    bool first = true;
    for (unsigned i = 0; i < AUTO9_VM3_CENTER_TARGETS; i++) {
        const Auto9Vm3CenterTarget *row = &s->vm3_center_target[i];
        if (!row->generation) {
            continue;
        }
        g_string_append_printf(result, "%s{\"footprint\":\"%s\",\"content\":\"%s\","
                               "\"generation\":%" PRIu64 ",\"color_generation\":%" PRIu64
                               ",\"age_ns\":%" PRId64 ",\"color_age_ns\":%" PRId64
                               ",\"pitch\":%u,\"bytes\":%u,\"rgb\":%u,\"clear\":%s}",
                               first ? "" : ",", row->footprint, row->content,
                               row->generation, row->color_generation, MAX(0, now - row->time_ns),
                               row->color_generation ? MAX(0, now - row->color_time_ns) : -1,
                               row->pitch, row->bytes, row->rgb, row->clear ? "true" : "false");
        first = false;
    }
    g_string_append(result, "]}");
    return g_string_free(result, false);
}

/* Stock full-frame zero-color jobs: the existing 4x resolve and a
 * 1560x700 single-sample clear. Zero is identical under either swizzle
 * and no nonzero dithered color or Z/S operation is implemented here.
 * This virtual engine never uses TE to skip stores. Invalidate CRC
 * entries; do not invent silicon CRC values. */
static uint32_t auto9_g3d1_vm3_zero_resolve(Auto9MaliG3d1 *s, uint64_t head,
                                            uint64_t status_pa, const uint8_t job[64],
                                            const uint8_t fb[128], const uint8_t ext[64],
                                            const uint8_t rt[64], unsigned *stage)
{
    uint32_t wh = ldl_le_p(fb + 32), flags = ldl_le_p(fb + 48);
    unsigned width = (wh & 65535) + 1, height = (wh >> 16) + 1;
    unsigned row = width * 4, crc_rows = (height + 15) / 16;
    unsigned crc_row = ((width + 15) / 16) * 8;
    uint32_t pitch = ldl_le_p(rt + 40), crc_pitch = ldl_le_p(ext + 8);
    uint64_t tag = ldq_le_p(job + 40), sample_locs = ldq_le_p(fb + 16);
    uint64_t raster = ldq_le_p(rt + 32), crc = ldq_le_p(ext);
    static const uint8_t zero[4096];
    Auto9Vm3ClearSpan reserved[5];
    unsigned saved = 0, used = 0, raster_spans;
    char center_footprint[65] = {0};
    bool single = ldl_le_p(fb + 44) == 0x01001000;
    /* Another original full clear enables CRC comparison. There is no
     * tiler, depth store or pre-frame load in this exact descriptor. */
    bool compare_crc = flags == 0xc0210000U && !single &&
        wh == 0x030b045fU && pitch == 4480 && crc_pitch == 576 &&
        ldl_le_p(fb + 52) == 0x3f800000 && ldl_le_p(ext + 12) == 0x001105d5;
    bool profile = single ?
        wh == 0x02bb0617 && ldl_le_p(fb + 52) == 0 &&
        (uint32_t)ldl_le_p(rt + 4) == 0x86888299U && pitch == 6272 &&
        crc_pitch == 832 && ldl_le_p(ext + 12) == 0x00110420 &&
        !ldq_le_p(ext + 16) && !ldq_le_p(ext + 32) :
        ldl_le_p(fb + 44) == 0x04001012 && (uint32_t)ldl_le_p(rt + 4) == 0x860a9299U;

    *stage = FRAG_PROFILE;
    if (!s->vm3_control || !profile || width > 1920 || height > 1080 || (tag & 63) != 3 ||
        ldl_le_p(job + 32) ||
        (uint32_t)ldl_le_p(job + 36) != (((height - 1) / 16) << 16 | ((width - 1) / 16)) ||
        ldq_le_p(job + 48) || ldq_le_p(job + 56) || ldl_le_p(fb) ||
        !sample_locs || (sample_locs & 7) || ldq_le_p(fb + 24) || ldl_le_p(fb + 36) ||
        (uint32_t)ldl_le_p(fb + 40) != wh ||
        (flags != 0x80210000U && !compare_crc) || ldq_le_p(fb + 56) ||
        ldl_le_p(rt) != 0x04000000 ||
        ldq_le_p(rt + 44) || ldq_le_p(rt + 52) || ldl_le_p(rt + 60) ||
        pitch < row || pitch > (row + 63) || (pitch & 63) || (raster & 63) ||
        crc_pitch < crc_row || crc_pitch > 4096 || (crc_pitch & 7) || (crc & 7) ||
        ((uint32_t)ldl_le_p(ext + 12) & 0x7800U)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    unsigned max_spans = height * (2 + (row - 1) / 4096) + 2 * crc_rows;
    *stage = FRAG_STORAGE;
    g_autofree Auto9Vm3ClearSpan *out = g_try_new(Auto9Vm3ClearSpan, max_spans);
    if (!out) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TRANSLATE;
    if (!auto9_vm3_clear_plan(s, head, 1, 64, 64, false, reserved, 5, &saved) ||
        reserved[0].pa != status_pa ||
        !auto9_vm3_clear_plan(s, tag & ~UINT64_C(63), 1, 256, 256, false, reserved, 5, &saved) ||
        !auto9_vm3_clear_plan(s, sample_locs, 1, 16, 16, false, reserved, 5, &saved) ||
        !auto9_vm3_clear_plan(s, raster, height, pitch, row, true, out, max_spans, &used)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    raster_spans = used;
    if (!auto9_vm3_clear_plan(s, crc, crc_rows, crc_pitch, crc_row, true, out, max_spans, &used)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    if (width == 1560 && height == 700) {
        auto9_vm3_center_footprint(out, raster_spans, center_footprint);
    }
    qsort(out, used, sizeof(*out), auto9_vm3_clear_span_cmp);
    for (unsigned i = 0; i < used; i++) {
        if (i && auto9_g3d00_overlap(out[i - 1].pa, out[i - 1].length, out[i].pa, out[i].length)) {
            return G3D00_JOB_WRITE_FAULT;
        }
        for (unsigned j = 0; j < saved; j++) {
            if (auto9_g3d00_overlap(out[i].pa, out[i].length, reserved[j].pa, reserved[j].length)) {
                return G3D00_JOB_WRITE_FAULT;
            }
        }
    }
    *stage = FRAG_WRITE;
    for (unsigned i = 0; i < used; i++) {
        if (address_space_write(&address_space_memory, out[i].pa, MEMTXATTRS_UNSPECIFIED,
                                zero, out[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    if (single) {
        if (++s->vm3_single_zero_clears <= 4) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 single-sample zero clear: sample=%u "
                "footprint=%s color-bytes=%u crc-bytes=%u\n",
                s->vm3_single_zero_clears, center_footprint, row * height, crc_row * crc_rows);
        }
    } else {
        s->vm3_zero_resolves++;
    }
    s->fragment_bytes_written += (uint64_t)row * height;
    s->fragment_te_unconditional += compare_crc;
    s->vm3_crc_invalidated_bytes += (uint64_t)crc_row * crc_rows;
    if (center_footprint[0]) {
        auto9_vm3_center_record(s, center_footprint, pitch, row * height, 0, NULL);
    }
    return G3D00_JOB_DONE;
}

typedef struct Auto9Vm3StockTexture {
    uint8_t *data;
    unsigned width, height, row_stride;
    float alpha;
    bool nearest, linear_rgba, reverse_rows, tiled_a8, repeat_bilinear, destination_mask;
    char traced_rows[65];
} Auto9Vm3StockTexture;

typedef struct Auto9Vm3Radial {
    float radius_offset, radius_scale, outer_scale, inner_offset, inner_scale;
    float bias[4], scale[4];
} Auto9Vm3Radial;

/* The output list has already been sorted and checked for internal overlap.
 * Find the first span whose end is past pa in O(log n). */
static bool auto9_vm3_stock_disjoint(uint64_t pa, unsigned n,
                                     const Auto9Vm3ClearSpan *out, unsigned count)
{
    unsigned lo = 0, hi = count;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        if (out[mid].pa + out[mid].length <= pa) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo == count || out[lo].pa >= pa + n;
}

static bool auto9_vm3_stock_read_traced(Auto9MaliG3d1 *s, uint64_t va, uint8_t *data,
                                        unsigned bytes, const Auto9Vm3ClearSpan *out,
                                        unsigned count, Auto9Vm3ClearSpan *source, unsigned *used)
{
    if (!va || !bytes || va >= (UINT64_C(1) << 48) || bytes > (UINT64_C(1) << 48) - va) {
        return false;
    }
    while (bytes) {
        unsigned n = MIN(bytes, 4096 - (va & 4095));
        uint64_t pa;
        if (!auto9_g3d00_translate(s, va, n, false, &pa) ||
            !auto9_vm3_stock_disjoint(pa, n, out, count) ||
            (data && address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                                        data, n) != MEMTX_OK)) {
            return false;
        }
        if (source) {
            /* A 6240-byte row spans at most three 4 KiB pages. */
            source[(*used)++] = (Auto9Vm3ClearSpan){ pa, n };
        }
        va += n;
        if (data) {
            data += n;
        }
        bytes -= n;
    }
    return true;
}

static bool auto9_vm3_stock_read(Auto9MaliG3d1 *s, uint64_t va, uint8_t *data,
                                 unsigned bytes, const Auto9Vm3ClearSpan *out,
                                 unsigned count)
{
    return auto9_vm3_stock_read_traced(s, va, data, bytes, out, count, NULL, NULL);
}

/* Pin the whole backing including the AFBC body, not just the first header
 * page. A moved, mutated, or output-aliasing image must not reuse host pixels. */
static bool auto9_vm3_atlas_backing(Auto9MaliG3d1 *s, Auto9Vm3AtlasImage *img,
                                    uint64_t base, bool capture,
                                    const Auto9Vm3ClearSpan *out, unsigned count,
                                    unsigned *miss)
{
    uint8_t page[4096];
    *miss = 0;
    if (!base || (base & 4095) || !img->pages ||
        img->pages > AUTO9_VM3_ATLAS_MAX_PAGES ||
        base > (UINT64_C(1) << 48) - img->pages * 4096ULL) {
        return false;
    }
    for (unsigned i = 0; i < img->pages; i++) {
        uint64_t pa, writable, va = base + i * 4096ULL;
        uint8_t *saved = img->backing_bytes + i * 4096ULL;
        unsigned reason = !auto9_g3d00_translate(s, va, 4096, false, &pa) ? 1 :
            capture && (!auto9_g3d00_translate(s, va, 4096, true, &writable) ||
                        pa != writable) ? 2 : !capture && pa != img->backing[i] ? 3 :
            !auto9_vm3_stock_disjoint(pa, 4096, out, count) ? 4 :
            address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                               capture ? saved : page, sizeof(page)) != MEMTX_OK ? 5 :
            !capture && memcmp(saved, page, sizeof(page)) ? 6 : 0;
        if (reason) {
            *miss = (i << 4) | reason;
            return false;
        }
        if (capture) {
            img->backing[i] = pa;
        }
    }
    return true;
}

static const uint32_t *auto9_vm3_atlas_lookup(Auto9MaliG3d1 *s, unsigned height,
                                              uint64_t base,
                                              const Auto9Vm3ClearSpan *out, unsigned count)
{
    /* VM3 clients can import the same allocation in another GPU AS. Each
     * page still has to resolve to the same private PA and exact contents. */
    const Auto9Vm3AtlasImage *failed = NULL;
    unsigned deepest = 0;
    for (unsigned i = height == 2048; i < G_N_ELEMENTS(s->vm3_atlas); i += 2) {
        Auto9Vm3AtlasImage *img = s->vm3_atlas[i];
        unsigned miss;
        if (!img) {
            continue;
        }
        /* The backing check rejects a different head page before reading it. */
        if (auto9_vm3_atlas_backing(s, img, base, false, out, count, &miss)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 private atlas hit: height=%u slot=%u generation=%" PRIu64 "\n",
                height, i, img->generation);
            return img->rgba;
        }
        /* Report the candidate that passed the most identity checks. */
        if (!failed || (miss >> 4) > (deepest >> 4) ||
            ((miss >> 4) == (deepest >> 4) && (miss & 15) > (deepest & 15))) {
            failed = img;
            deepest = miss;
        }
    }
    if (failed) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 private atlas lookup miss: height=%u same-as=%u page=%u reason=%u\n",
            height, (unsigned)(failed->ttbr == s->as0_active_ttbr), deepest >> 4, deepest & 15);
    }
    return NULL;
}

static float auto9_vm3_stock_half(uint16_t h)
{
    float v = auto9_g3d00_half(h & 0x7fff);
    return h & 0x8000 ? -v : v;
}

static uint32_t auto9_vm3_color_state(Auto9MaliG3d1 *s, const Auto9G3d00TileBatch *batch,
                                     const Auto9Vm3ClearSpan *out, unsigned count,
                                     unsigned *stage)
{
    uint8_t rsd[80], code[32];
    *stage = FRAG_SHADER;
    if (!auto9_vm3_stock_read(s, batch->renderer, rsd, sizeof(rsd), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    uint32_t blend = ldl_le_p(rsd + 72);
    /* LD_VAR F16x4, always-pass/no-write depth, no stencil/A2C/MSAA.
     * Same code as one earlier draw, but a different original blend. */
    if (ldl_le_p(rsd + 8) || ldl_le_p(rsd + 12) != 0x10000 ||
        ldl_le_p(rsd + 16) != 0x49502200 || ldl_le_p(rsd + 32) != 0x3750ffff ||
        ldl_le_p(rsd + 36) != 0x44000000 || ldl_le_p(rsd + 48) != 0x2080 ||
        ldl_le_p(rsd + 60) != 0x1a01 || ldl_le_p(rsd + 64) != 0x201 ||
        (uint32_t)ldl_le_p(rsd + 68) != 0xf0d32503U ||
        (blend & ~(BIT(5) | BIT(6))) != 0x1a || ldl_le_p(rsd + 76) != 0xed000) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t shader = ldq_le_p(rsd);
    if ((shader & 15) || !auto9_vm3_stock_read(s, shader, code, sizeof(code), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, sizeof(code));
    return sha && !strcmp(sha, "d263dd306bf49ae57533928edc687ab36073622175d9ff3298a138d1e68f889f")
           ? G3D00_JOB_DONE : G3D00_JOB_CONFIG_FAULT;
}

/* Equation 0xf0d32503: RGB = dst + (src - dst) * src.a, alpha =
 * src.a + dst.a * (1 - src.a). Varying is straight RGBA; RT bytes are BGRA. */
static void auto9_vm3_color_blend(const float rgba[4], uint8_t bgra[4])
{
    float a = rgba[3];
    for (unsigned k = 0; k < 3; k++) {
        float d = bgra[k], v = d + (rgba[2 - k] * 255.0f - d) * a;
        bgra[k] = MIN(v + 0.5f, 255.0f);
    }
    bgra[3] = MIN(a * 255.0f + bgra[3] * (1.0f - a) + 0.5f, 255.0f);
}

static unsigned auto9_vm3_stock_spread(unsigned v);

/* Nearest, normalized and repeating, for the pinned VAR_TEX sampler. A
 * group with identical solid headers is invariant to the intra-group
 * permutation. Use double for the perspective divide and repeat step. */
static void auto9_vm3_group_sample(const uint8_t *groups, const uint32_t *atlas,
                                  const Auto9Vm3StockTexture *linear,
                                  double u, double v, float rgba[4])
{
    unsigned n = atlas ? 2048 : 16;
    bool copied = linear && linear->data;
    unsigned w = copied ? linear->width : n;
    unsigned h = copied ? linear->height : linear && linear->height == 1024 ? n / 2 : n;
    unsigned x = MIN((unsigned)((u - floor(u)) * w), w - 1);
    unsigned y = MIN((unsigned)((v - floor(v)) * h), h - 1);
    if (copied && linear->repeat_bilinear) {
        double xx = (u - floor(u)) * w - 0.5, yy = (v - floor(v)) * h - 0.5;
        int ix = floor(xx), iy = floor(yy);
        unsigned x0 = ix < 0 ? w - 1 : (unsigned)ix, y0 = iy < 0 ? h - 1 : (unsigned)iy;
        unsigned x1 = (x0 + 1) % w, y1 = (y0 + 1) % h;
        const uint8_t *a = linear->data + (size_t)y0 * linear->row_stride + 4 * x0;
        const uint8_t *b = linear->data + (size_t)y0 * linear->row_stride + 4 * x1;
        const uint8_t *c = linear->data + (size_t)y1 * linear->row_stride + 4 * x0;
        const uint8_t *d = linear->data + (size_t)y1 * linear->row_stride + 4 * x1;
        for (unsigned k = 0; k < 4; k++) {
            double top = a[k] + (b[k] - a[k]) * (xx - ix);
            double bottom = c[k] + (d[k] - c[k]) * (xx - ix);
            rgba[k] = auto9_vm3_stock_half(auto9_bifrost_f32_half_rne((top + (bottom - top) * (yy - iy)) / 255.0));
        }
        return;
    }
    if (copied && linear->tiled_a8) {
        unsigned off = (auto9_vm3_stock_spread(y) * 3) ^ auto9_vm3_stock_spread(x);
        uint8_t a = linear->data[(size_t)(y >> 4) * linear->row_stride + (x >> 4) * 256 + off];
        rgba[0] = rgba[1] = rgba[2] = 0;
        rgba[3] = auto9_vm3_stock_half(auto9_bifrost_unorm8_half(a));
        return;
    }
    uint8_t pixel[4];
    if (atlas) {
        stl_le_p(pixel, atlas[y * n + x]);
    }
    const uint8_t *p = copied ? linear->data + (size_t)y * linear->row_stride + x * 4 :
                       atlas ? pixel : groups + (y * n + x) * 4;
    for (unsigned k = 0; k < 4; k++) {
        rgba[k] = auto9_vm3_stock_half(auto9_bifrost_unorm8_half(p[k]));
    }
}

static float auto9_vm3_round_half(float x)
{
    uint16_t h = auto9_bifrost_f32_half_rne(x);
    if ((h & 0x7c00) == 0x7c00) {
        return h & 0x03ff ? NAN : h & 0x8000 ? -INFINITY : INFINITY;
    }
    return auto9_vm3_stock_half(h);
}

/* Register group 0 is texture 1 at UV0; group 1 is texture 0 at UV1.
 * Keep the original FP16 rounding points, including both premultiplications
 * and the two FMAs. FRCP uses a rounded reciprocal in this software model. */
static void auto9_vm3_dual_uv_color(const float rear[4], const float front[4],
                                    const float varying[4], float out[4])
{
    float a0 = rear[3], a1 = front[3];
    float a = auto9_vm3_round_half(fmaf(a1, auto9_vm3_round_half(1.0f - a0), a0));
    out[3] = fminf(1.0f, fmaxf(0.0f, auto9_vm3_round_half(a * varying[3])));
    out[0] = out[1] = out[2] = 0;
    /* A zero-alpha fragment leaves the attachment unchanged. Do not
     * evaluate the reciprocal for it (0 * infinity is not a color). */
    if (!(varying[0] > 0.0f) || !(out[3] > 0.0f)) {
        return;
    }
    float reciprocal = auto9_vm3_round_half(1.0f / a);
    for (unsigned k = 0; k < 3; k++) {
        float p0 = auto9_vm3_round_half(rear[k] * a0);
        float diff = auto9_vm3_round_half(fmaf(front[k], varying[k], -p0));
        float mixed = auto9_vm3_round_half(fmaf(a1, diff, p0));
        out[k] = fminf(1.0f, fmaxf(0.0f, auto9_vm3_round_half(mixed * reciprocal)));
    }
}

static float auto9_vm3_radial_unit(float x)
{
    return fminf(1.0f, fmaxf(0.0f, x));
}

/* r4/r5 come from the second preload (varying 2, FP32 vec2).
 * r0/r1 are FP16 texture 0 using varying 0; LD_VAR_IMM supplies RGBA.
 * Keep the FP16 conversion and two multiply/round stages independent. */
static void auto9_vm3_radial_color(const Auto9Vm3Radial *p, float x, float y,
                                  const float texel[4], float rgba[4])
{
    float radius = sqrtf(fmaf(y, y, x * x));
    float t = auto9_vm3_radial_unit((radius - p->radius_offset) * p->radius_scale);
    float outer = auto9_vm3_radial_unit(t * p->outer_scale);
    float inner = auto9_vm3_radial_unit((t - p->inner_offset) * p->inner_scale);
    t = auto9_vm3_radial_unit(fmaf(inner - outer, 0.5f, outer));
    float weight = (t * t) * (3.0f - 2.0f * t);
    for (unsigned i = 0; i < 4; i++) {
        float factor = auto9_vm3_round_half(fmaf(weight, p->scale[i], p->bias[i]));
        float source = auto9_vm3_round_half(texel[i] * rgba[i]);
        rgba[i] = auto9_vm3_radial_unit(auto9_vm3_round_half(source * factor));
    }
}

/* Disabled multisample rasterization: a covered pixel center covers all
 * four target samples. With no target storage this only supplies coverage
 * for the AFBC path, using exactly the same edge test as color writeback. */
static unsigned auto9_vm3_color_raster_target(const Auto9G3d00Triangle *tri, unsigned width,
                                       unsigned tiles_x, uint8_t *samples, uint8_t *dirty,
                                        uint64_t *rgb_pixels, const uint8_t *groups,
                                        const uint32_t *atlas, const Auto9Vm3StockTexture *linear,
                                        const Auto9Vm3StockTexture *second, const uint8_t *mask,
                                        uint32_t *attachment, uint8_t *modified,
                                        const Auto9Vm3Radial *radial)
{
    bool textured = groups || atlas || linear;
    int64_t area = auto9_g3d00_edge(tri->xy[0], tri->xy[1], tri->xy[2][0], tri->xy[2][1]);
    if (!area) {
        return 0;
    }
    unsigned v[3] = { 0, area > 0 ? 1 : 2, area > 0 ? 2 : 1 };
    bool inclusive[3];
    int64_t step_x[3], step_y[3], row[3];
    double weighted[3][4], uv[3][2], uv2[3][2], rw[3];
    for (unsigned j = 0; j < 3; j++) {
        const int32_t *a = tri->xy[v[(j + 1) % 3]], *b = tri->xy[v[(j + 2) % 3]];
        inclusive[j] = auto9_g3d00_top_left(a, b);
        step_x[j] = (int64_t)(a[1] - b[1]) * 256;
        step_y[j] = (int64_t)(b[0] - a[0]) * 256;
        row[j] = auto9_g3d00_edge(a, b, tri->pixel[0] * 256 + 128, tri->pixel[1] * 256 + 128);
        if (samples || attachment) {
            rw[j] = tri->inv_w[v[j]];
            for (unsigned k = 0; k < 4; k++) {
                weighted[j][k] = rw[j] * auto9_vm3_stock_half(tri->rgba[v[j]][k]);
            }
            if (textured) {
                for (unsigned k = 0; k < 2; k++) {
                    uv[j][k] = rw[j] * (double)tri->uv32[v[j]][k];
                    if (second || radial) {
                        uv2[j][k] = rw[j] * (double)tri->uv2[v[j]][k];
                    }
                }
            }
        }
    }
    unsigned covered = 0;
    for (unsigned y = tri->pixel[1]; y <= tri->pixel[3]; y++) {
        int64_t e[3] = { row[0], row[1], row[2] };
        for (unsigned x = tri->pixel[0]; x <= tri->pixel[2]; x++) {
            if ((!mask || mask[(y >> 4) * tiles_x + (x >> 4)]) &&
                (e[0] > 0 || (!e[0] && inclusive[0])) &&
                (e[1] > 0 || (!e[1] && inclusive[1])) &&
                (e[2] > 0 || (!e[2] && inclusive[2]))) {
                if (samples || attachment) {
                    /* Nonnegative edges and positive inverse W guaranteed
                     * by the fan decoder imply a strictly positive sum. */
                    double denom = e[0] * rw[0] + e[1] * rw[1] + e[2] * rw[2];
                    float rgba[4];
                    float texel[4];
                    if (textured) {
                        double u = (e[0] * uv[0][0] + e[1] * uv[1][0] + e[2] * uv[2][0]) / denom;
                        double vv = (e[0] * uv[0][1] + e[1] * uv[1][1] + e[2] * uv[2][1]) / denom;
                        auto9_vm3_group_sample(groups, atlas, second ? second : linear, u, vv, texel);
                    }
                    for (unsigned k = 0; k < 4; k++) {
                        double value = (e[0] * weighted[0][k] + e[1] * weighted[1][k] +
                                        e[2] * weighted[2][k]) / denom;
                        float half_input = textured ? (float)value : fminf(1.0f, fmaxf(0.0f, (float)value));
                        float half_value = auto9_vm3_stock_half(auto9_bifrost_f32_half_rne(half_input));
                        if (textured && !second && !radial) {
                            /* The A8 program preserves varying RGB and applies
                             * the texture as a coverage mask to alpha only. */
                            if (!linear || !linear->tiled_a8 || k == 3) {
                                half_value = auto9_vm3_stock_half(auto9_bifrost_f32_half_rne(half_value * texel[k]));
                            }
                            half_value = fminf(1.0f, fmaxf(0.0f, half_value));
                        }
                        rgba[k] = half_value;
                    }
                    if (second) {
                        float front[4], result[4];
                        double u = (e[0] * uv2[0][0] + e[1] * uv2[1][0] + e[2] * uv2[2][0]) / denom;
                        double vv = (e[0] * uv2[0][1] + e[1] * uv2[1][1] + e[2] * uv2[2][1]) / denom;
                        auto9_vm3_group_sample(NULL, NULL, linear, u, vv, front);
                        auto9_vm3_dual_uv_color(texel, front, rgba, result);
                        memcpy(rgba, result, sizeof(result));
                    } else if (radial) {
                        float rx = (e[0] * uv2[0][0] + e[1] * uv2[1][0] + e[2] * uv2[2][0]) / denom;
                        float ry = (e[0] * uv2[0][1] + e[1] * uv2[1][1] + e[2] * uv2[2][1]) / denom;
                        auto9_vm3_radial_color(radial, rx, ry, texel, rgba);
                    }
                    size_t i = (size_t)y * width + x;
                    if (attachment) {
                        uint32_t old = attachment[i], result = 0;
                        if (linear && linear->destination_mask) {
                            /* 0xf0531531: both RGB and A are D * Sa. */
                            for (unsigned k = 0; k < 4; k++) {
                                result |= (uint32_t)(((old >> (8 * k)) & 255) * rgba[3] + 0.5f) << (8 * k);
                            }
                        } else if (linear) {
                            uint8_t bgra[] = { old >> 16, old >> 8, old, old >> 24 };
                            auto9_vm3_color_blend(rgba, bgra);
                            result = bgra[2] | (uint32_t)bgra[1] << 8 |
                                     (uint32_t)bgra[0] << 16 | (uint32_t)bgra[3] << 24;
                        } /* The validated uniform-only strip stores transparent black. */
                        attachment[i] = result;
                        modified[i] = 1;
                        *rgb_pixels += !!(result & 0xffffff);
                    } else {
                        *rgb_pixels += rgba[3] > 0 && (rgba[0] > 0 || rgba[1] > 0 || rgba[2] > 0);
                        uint8_t *dst = samples + i * 16;
                        for (unsigned k = 0; k < 4; k++) {
                            auto9_vm3_color_blend(rgba, dst + 4 * k);
                        }
                    }
                }
                dirty[(y >> 4) * tiles_x + (x >> 4)] = 1;
                covered++;
            }
            for (unsigned j = 0; j < 3; j++) {
                e[j] += step_x[j];
            }
        }
        for (unsigned j = 0; j < 3; j++) {
            row[j] += step_y[j];
        }
    }
    return covered;
}

static unsigned auto9_vm3_color_raster(const Auto9G3d00Triangle *tri, unsigned width,
                                       unsigned tiles_x, uint8_t *samples, uint8_t *dirty,
                                       uint64_t *rgb_pixels, const uint8_t *groups,
                                       const uint32_t *atlas, const Auto9Vm3StockTexture *linear,
                                       const Auto9Vm3StockTexture *second,
                                       const Auto9Vm3Radial *radial)
{
    return auto9_vm3_color_raster_target(tri, width, tiles_x, samples, dirty,
                                        rgb_pixels, groups, atlas, linear, second, NULL, NULL, NULL,
                                        radial);
}

static uint32_t auto9_vm3_stock_texture(Auto9MaliG3d1 *s, const Auto9G3d00TileBatch *batch,
                                       const Auto9Vm3ClearSpan *out, unsigned count,
                                       Auto9Vm3StockTexture *tex, unsigned *stage)
{
    uint8_t rsd[80], shader[64], desc[32], sampler[32], uniform[4], surface[16];
    const char digest[] = "27ba6f4da9e135c141c42b5c49dd6890ad86335101e1b12cf4fc55d4b3e18c8a";

    *stage = FRAG_TEXTURE_STATE;
    if ((batch->textures | batch->samplers) & 31 || batch->push_uniforms & 7) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, batch->renderer, rsd, sizeof(rsd), out, count) ||
        !auto9_vm3_stock_read(s, batch->textures, desc, sizeof(desc), out, count) ||
        !auto9_vm3_stock_read(s, batch->samplers, sampler, sizeof(sampler), out, count) ||
        !auto9_vm3_stock_read(s, batch->push_uniforms, uniform, sizeof(uniform), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (s->vm3_textured_attempts <= 4) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 G3D1 VM3 texture filter: tex-shape=0x%x anisotropy=%u alpha-finite-unit=%u\n",
            ldl_le_p(desc + 4), ((uint32_t)ldl_le_p(sampler + 8) >> 16) & 31,
            (unsigned)(lduw_le_p(uniform) <= 0x3c00));
    }
    uint32_t filter = ldl_le_p(sampler);
    uint32_t wh = ldl_le_p(desc + 4);
    bool tiled_bgra = ldl_le_p(desc) == 0x2ec01022 && ldl_le_p(desc + 8) == 0x1688;
    /* OEM full-size color source. In v7, component order 0 is RGBA and
     * texture layout 2 is linear. Never mix format and layout independently. */
    bool linear_rgba = ldl_le_p(desc) == 0x2ec00022 && ldl_le_p(desc + 8) == 0x2688 &&
                       wh == ((699U << 16) | 1559U) && filter == 0x42899901U;
    if (ldl_le_p(rsd + 8) != 0x00010001 || ldl_le_p(rsd + 12) != 0x00010000 ||
        ldl_le_p(rsd + 16) != 0x49502a01 || ldl_le_p(rsd + 32) != 0x3751ffff ||
        ldl_le_p(rsd + 36) != 0x0400ffff || ldl_le_p(rsd + 48) != 0xa080 ||
        ldl_le_p(rsd + 60) != 0x4202 || ldl_le_p(rsd + 64) != 0x201 ||
        (uint32_t)ldl_le_p(rsd + 68) != 0xf0d32d32U || (ldl_le_p(rsd + 72) & 3) != 2 ||
        ldl_le_p(rsd + 76) != 0xed000 || (!tiled_bgra && !linear_rgba) ||
        ldl_le_p(desc + 12) || ldq_le_p(desc + 24) ||
        (filter != 0x42899901U && filter != 0x5a899901U) || ldl_le_p(sampler + 4) != 0x1fffc000 ||
        ((uint32_t)ldl_le_p(sampler + 8) & 0x001f0000U)) {
        if (s->vm3_fragment_tiler_rejections < 4) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 alternate fragment state: tex0=0x%x tex1=0x%x tex2=0x%x tex3=0x%x "
                "sampler0=0x%x sampler1=0x%x message=0x%x r8=0x%x r12=0x%x r16=0x%x "
                "r32=0x%x r36=0x%x r48=0x%x blend=0x%x eq=0x%x mode=%u\n",
                ldl_le_p(desc), ldl_le_p(desc + 4), ldl_le_p(desc + 8), ldl_le_p(desc + 12),
                ldl_le_p(sampler), ldl_le_p(sampler + 4), ldl_le_p(rsd + 60),
                ldl_le_p(rsd + 8), ldl_le_p(rsd + 12), ldl_le_p(rsd + 16), ldl_le_p(rsd + 32),
                ldl_le_p(rsd + 36), ldl_le_p(rsd + 48), ldl_le_p(rsd + 64), ldl_le_p(rsd + 68),
                (unsigned)ldl_le_p(rsd + 72) & 3);
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t code = ldq_le_p(rsd);
    if ((code & 15) || !auto9_vm3_stock_read(s, code, shader, sizeof(shader), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, shader, sizeof(shader));
    uint16_t half = lduw_le_p(uniform);
    if (!sha || strcmp(sha, digest) || (half & 0x7c00) == 0x7c00) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    tex->alpha = auto9_vm3_stock_half(half);
    tex->nearest = filter == 0x5a899901U;
    tex->linear_rgba = linear_rgba;
    if (!(tex->alpha >= 0 && tex->alpha <= 1)) {
        if (s->vm3_fragment_tiler_rejections < 4) {
            qemu_log_mask(LOG_GUEST_ERROR, "Auto9 G3D1 VM3 texture opacity outside modeled unit interval\n");
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    tex->width = (wh & 65535) + 1;
    tex->height = (wh >> 16) + 1;
    uint64_t surface_va = ldq_le_p(desc + 16);
    *stage = FRAG_TEXTURE_SURFACE;
    if (tex->width > 2048 || tex->height > 2048 || (surface_va & 7) ||
        !auto9_vm3_stock_read(s, surface_va, surface, sizeof(surface), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t base = ldq_le_p(surface);
    int32_t row_stride = ldl_le_p(surface + 8);
    uint32_t slice = ldl_le_p(surface + 12);
    bool reverse = row_stride < 0;
    /* Bifrost v7 stores a signed byte step from the logical first row.
     * Preserve logical row order in the private copy; tiled stays forward. */
    uint32_t stride = reverse ? -(int64_t)row_stride : row_stride;
    unsigned active = linear_rgba ? tex->width * 4 : ((tex->width + 15) / 16) * 1024;
    unsigned rows = linear_rgba ? tex->height : (tex->height + 15) / 16;
    /* The original single-block U-interleaved surface can omit its row
     * step: no second tile row is addressed. Still read the full block
     * through the normal DMA range and destination-alias checks. */
    bool single_tile_zero_pitch = !linear_rgba && rows == 1 &&
                                  active == 1024 && row_stride == 0 && slice == 0;
    if (single_tile_zero_pitch) {
        stride = active;
    }
    uint64_t delta = (uint64_t)(rows - 1) * stride;
    uint64_t bytes = delta + active, limit = UINT64_C(1) << 48;
    if (s->vm3_textured_attempts <= 4) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 G3D1 VM3 texture surface: row=%" PRId32 " slice=%u active=%u rows=%u aligned=%u\n",
            row_stride, slice, active, rows, (unsigned)!(base & 63));
    }
    if (linear_rgba && s->vm3_linear_rgba_loads < 4 && s->vm3_fragment_tiler_rejections < 4) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 G3D1 VM3 linear RGBA surface: row=%" PRId32 " slice=%u active=%u rows=%u aligned=%u\n",
            row_stride, slice, active, rows, (unsigned)!(base & 3));
    }
    bool aligned = !(base & (linear_rgba ? 3 : 63));
    bool range = base && base < limit &&
                 (reverse ? delta < base && active <= limit - base : bytes <= limit - base);
    if (!aligned || !range || (reverse && !linear_rgba) || stride < active ||
        (linear_rgba ? (stride & 3) || stride > 8192 : stride & 1023) || bytes > (32U << 20) ||
        (slice && (slice < bytes || slice > (32U << 20)))) {
        if (s->vm3_fragment_tiler_rejections < 4) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 texture surface rejected: attempt=%u linear=%u "
                "row=%" PRId32 " slice=%u active=%u rows=%u aligned=%u range=%u\n",
                s->vm3_textured_attempts, (unsigned)linear_rgba, row_stride,
                slice, active, rows, (unsigned)aligned, (unsigned)range);
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    tex->row_stride = stride;
    tex->reverse_rows = reverse;
    *stage = FRAG_TEXTURE_MEMORY;
    tex->data = g_try_malloc(bytes);
    if (!tex->data) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    g_autofree Auto9Vm3ClearSpan *input_spans = NULL;
    unsigned used = 0;
    if (linear_rgba && stride == 6272 && s->vm3_center_reads_traced < 512 &&
        qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        input_spans = g_try_new(Auto9Vm3ClearSpan, 700 * 3);
    }
    /* Keep DMA and logical copy order identical with tracing on or off. */
    for (unsigned y = 0; y < rows; y++) {
        uint64_t offset = (uint64_t)y * stride;
        unsigned first = used;
        if (!auto9_vm3_stock_read_traced(s, reverse ? base - offset : base + offset,
                                       tex->data + (size_t)y * stride, active, out, count,
                                       input_spans, &used)) {
            return G3D00_JOB_READ_FAULT;
        }
        if (input_spans && reverse) {
            for (unsigned l = first, r = used - 1; l < r; l++, r--) {
                Auto9Vm3ClearSpan item = input_spans[l];
                input_spans[l] = input_spans[r];
                input_spans[r] = item;
            }
        }
    }
    if (input_spans) {
        /* Reverse the row order, not the chunk order within each row. */
        if (reverse) {
            for (unsigned l = 0, r = used - 1; l < r; l++, r--) {
                Auto9Vm3ClearSpan item = input_spans[l];
                input_spans[l] = input_spans[r];
                input_spans[r] = item;
            }
        }
        auto9_vm3_center_footprint(input_spans, used, tex->traced_rows);
    }
    if (single_tile_zero_pitch) {
        s->vm3_single_tile_zero_pitch_loads++;
    }
    return G3D00_JOB_DONE;
}

/* This exact original program consumes VAR_TEX and a separate F16x4
 * preload, then multiplies them. Do not silently substitute the old scalar
 * opacity shader. Decode exact solid groups without guessing their internal
 * permutation or treating a compressed payload as linear bytes. */
static unsigned auto9_vm3_atlas_origin(Auto9MaliG3d1 *s, uint64_t source);

static bool auto9_vm3_radial_uniforms(const uint8_t u[120], Auto9Vm3Radial *p)
{
    *p = (Auto9Vm3Radial) {
        .radius_offset = auto9_g3d00_float(u + 4 * 8 + 4),
        .radius_scale = auto9_g3d00_float(u + 11 * 8),
        .outer_scale = auto9_g3d00_float(u + 12 * 8),
        .inner_offset = auto9_g3d00_float(u + 12 * 8 + 4),
        .inner_scale = auto9_g3d00_float(u + 4),
        .bias = { auto9_vm3_stock_half(lduw_le_p(u + 14 * 8 + 4)),
                  auto9_vm3_stock_half(lduw_le_p(u + 13 * 8 + 6)),
                  auto9_vm3_stock_half(lduw_le_p(u + 4 * 8)),
                  auto9_vm3_stock_half(lduw_le_p(u + 11 * 8 + 6)) },
        .scale = { auto9_g3d00_float(u + 8),
                   auto9_g3d00_float(u + 14 * 8),
                   auto9_g3d00_float(u + 13 * 8),
                   auto9_g3d00_float(u + 2 * 8 + 4) },
    };
    /* Nonfinite guest parameters are outside this pinned display path.
     * Validate the original half bits before the finite-only half helper. */
    const unsigned half_offsets[] = { 116, 110, 32, 94 };
    for (unsigned i = 0; i < 4; ++i) {
        if ((lduw_le_p(u + half_offsets[i]) & 0x7c00) == 0x7c00 ||
            !isfinite(p->scale[i])) {
            return false;
        }
    }
    return isfinite(p->radius_offset) && isfinite(p->radius_scale) &&
           isfinite(p->outer_scale) && isfinite(p->inner_offset) &&
           isfinite(p->inner_scale);
}

static uint32_t auto9_vm3_radial_texture(Auto9MaliG3d1 *s,
                                        const Auto9G3d00TileBatch *b,
                                        const Auto9Vm3ClearSpan *out, unsigned count,
                                        unsigned *stage, Auto9Vm3StockTexture *tex,
                                        Auto9Vm3Radial *radial)
{
    enum { W = 2048, H = 1024, ROW = W * 4, BYTES = ROW * H };
    const uint64_t limit = UINT64_C(1) << 48;
    uint8_t rsd[80], code[256], desc[32], sampler[32], surface[16], fau[120];
    *stage = FRAG_TEXTURE_STATE;
    if (!b->textures || !b->samplers || !b->push_uniforms ||
        ((b->textures | b->samplers) & 31) || (b->push_uniforms & 7)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, b->renderer, rsd, sizeof(rsd), out, count) ||
        !auto9_vm3_stock_read(s, b->textures, desc, sizeof(desc), out, count) ||
        !auto9_vm3_stock_read(s, b->samplers, sampler, sizeof(sampler), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (ldl_le_p(desc) != 0x2ec00022 || ldl_le_p(desc + 4) != 0x03ff07ff ||
        ldl_le_p(desc + 8) != 0x2688 || ldl_le_p(desc + 12) || ldq_le_p(desc + 24) ||
        ldl_le_p(sampler) != 0x5a888801 || ldl_le_p(sampler + 4) != 0x1fffc000 ||
        ldq_le_p(sampler + 8) || ldq_le_p(sampler + 16) || ldq_le_p(sampler + 24)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_SHADER;
    uint64_t shader = ldq_le_p(rsd);
    if (!shader || (shader & 15) || shader > limit - sizeof(code)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, shader, code, sizeof(code), out, count) ||
        !auto9_vm3_stock_read(s, b->push_uniforms, fau, sizeof(fau), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (!auto9_vm3_radial_program(rsd, code) || !auto9_vm3_radial_uniforms(fau, radial)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TEXTURE_SURFACE;
    uint64_t surf = ldq_le_p(desc + 16);
    if (!surf || (surf & 7) || surf > limit - sizeof(surface)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, surf, surface, sizeof(surface), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t base = ldq_le_p(surface);
    if (!base || (base & 3) || base > limit - BYTES ||
        ldl_le_p(surface + 8) != ROW || ldl_le_p(surface + 12)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TEXTURE_MEMORY;
    g_autofree uint8_t *data = g_try_malloc(BYTES);
    if (!data) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, base, data, BYTES, out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    *tex = (Auto9Vm3StockTexture){ .data = data, .width = W,
                                  .height = H, .row_stride = ROW, .linear_rgba = true };
    data = NULL;
    return G3D00_JOB_DONE;
}

static uint32_t auto9_vm3_dual_uv_textures(Auto9MaliG3d1 *s,
                                           const Auto9G3d00TileBatch *batch,
                                           const Auto9Vm3ClearSpan *out, unsigned count,
                                           unsigned *stage, Auto9Vm3StockTexture tex[2])
{
    enum { W = 2048, H = 1024, ROW = W * 4, BYTES = ROW * H };
    uint8_t rsd[80], program[272], desc[64], samplers[64], surface[2][16];
    uint64_t base[2];
    *stage = FRAG_TEXTURE_STATE;
    if (!batch->textures || !batch->samplers || ((batch->textures | batch->samplers) & 31) ||
        batch->push_uniforms) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, batch->renderer, rsd, sizeof(rsd), out, count) ||
        !auto9_vm3_stock_read(s, batch->textures, desc, sizeof(desc), out, count) ||
        !auto9_vm3_stock_read(s, batch->samplers, samplers, sizeof(samplers), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (ldl_le_p(rsd + 8) != 0x20002 || ldl_le_p(rsd + 12) != 0x30000 ||
        ldl_le_p(rsd + 16) != 0x09502a00 || ldl_le_p(rsd + 32) != 0x3750ffff ||
        ldl_le_p(rsd + 36) != 0x44000000 || ldl_le_p(rsd + 48) != 0x2080 ||
        ldl_le_p(rsd + 60) != 0x02120282 || ldl_le_p(rsd + 64) != 0x201 ||
        (uint32_t)ldl_le_p(rsd + 68) != 0xf0d32503U || (ldl_le_p(rsd + 72) & 3) != 2 ||
        ldl_le_p(rsd + 76) != 0xed000) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned i = 0; i < 2; i++) {
        const uint8_t *d = desc + 32 * i, *p = samplers + 32 * i;
        if (ldl_le_p(d) != 0x2ec00022 || ldl_le_p(d + 4) != 0x03ff07ff ||
            ldl_le_p(d + 8) != 0x2688 || ldl_le_p(d + 12) || ldq_le_p(d + 24) ||
            ldl_le_p(p) != 0x5a888801 || ldl_le_p(p + 4) != 0x1fffc000 ||
            ldq_le_p(p + 8) || ldq_le_p(p + 16) || ldq_le_p(p + 24)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    *stage = FRAG_SHADER;
    uint64_t code = ldq_le_p(rsd);
    if (!code || (code & 15) || !auto9_vm3_stock_read(s, code, program, sizeof(program), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, program, sizeof(program));
    if (!sha || strcmp(sha, "229b76b0d860ef1bbc813f34d74f94a06aa0b03292e7478d52b723278f3b953d")) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TEXTURE_SURFACE;
    for (unsigned i = 0; i < 2; i++) {
        uint64_t addr = ldq_le_p(desc + 32 * i + 16);
        if (!addr || (addr & 7) || !auto9_vm3_stock_read(s, addr, surface[i], sizeof(surface[i]), out, count)) {
            return G3D00_JOB_READ_FAULT;
        }
        base[i] = ldq_le_p(surface[i]);
        if (!base[i] || (base[i] & 3) || ldl_le_p(surface[i] + 8) != ROW || ldl_le_p(surface[i] + 12)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    *stage = FRAG_TEXTURE_MEMORY;
    g_autofree uint8_t *data = g_try_malloc(2 * BYTES);
    if (!data) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* O(2*W*H), one checked copy of each input. Outputs are still staged;
     * rejecting either texture must leave the guest render target intact. */
    for (unsigned i = 0; i < 2; i++) {
        if (!auto9_vm3_stock_read(s, base[i], data + i * BYTES, BYTES, out, count)) {
            return G3D00_JOB_READ_FAULT;
        }
        tex[i] = (Auto9Vm3StockTexture){ .width = W, .height = H,
                                       .row_stride = ROW, .linear_rgba = true };
    }
    tex[0].data = data;
    tex[1].data = data + BYTES;
    data = NULL;
    return G3D00_JOB_DONE;
}

/* Bifrost v7 supplies the PC low word with four low bits reserved.
 * The high word belongs to the primary fragment shader's code region. */
static uint64_t auto9_vm3_blend_code(const uint8_t renderer[80])
{
    uint64_t fragment = ldq_le_p(renderer);
    uint32_t control = ldl_le_p(renderer + 72), pc = ldl_le_p(renderer + 76);
    if (!fragment || fragment >= (UINT64_C(1) << 48) || (fragment & 15) ||
        !(ldl_le_p(renderer + 64) & BIT(9)) || (control & 7) || (pc & 15)) {
        return 0;
    }
    return (fragment & UINT64_C(0xffffffff00000000)) | pc;
}

/* A clipped-out OEM draw has no fragments, so neither VAR_TEX nor
 * its LD_TILE/BLEND shader can execute. Validate this exact pair and the
 * source descriptor, but do not read texels or touch its destination. */
static uint32_t auto9_vm3_clipped_interleaved_state(Auto9MaliG3d1 *s,
        const uint8_t state[80], const uint8_t texture[32],
        const Auto9Vm3ClearSpan *out, unsigned count)
{
    static const uint32_t state_words[] = {
        0x10001, 0x20000, 0x49502a00, 0, 0, 0, 0x3710ffff, 0x44000000,
        0x7ff00, 0x7ff00, 0x2080, 0, 0, 0x1a114202, 0x201, 0, 0,
    };
    uint8_t fragment[64], blend[112], surface[16];
    uint64_t pc = ldq_le_p(state), blend_pc = auto9_vm3_blend_code(state);
    uint64_t surf = ldq_le_p(texture + 16);
    for (unsigned i = 0; i < G_N_ELEMENTS(state_words); i++) {
        if ((uint32_t)ldl_le_p(state + 8 + 4 * i) != state_words[i]) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    if (!pc || (pc & 15) || pc > (UINT64_C(1) << 48) - sizeof(fragment) ||
        !blend_pc || blend_pc > (UINT64_C(1) << 48) - sizeof(blend) ||
        !surf || (surf & 7) || surf > (UINT64_C(1) << 48) - sizeof(surface)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, pc, fragment, sizeof(fragment), out, count) ||
        !auto9_vm3_stock_read(s, blend_pc, blend, sizeof(blend), out, count) ||
        !auto9_vm3_stock_read(s, surf, surface, sizeof(surface), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *frag_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, fragment, sizeof(fragment));
    g_autofree char *blend_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, blend, sizeof(blend));
    uint64_t tagged = ldq_le_p(surface), base = tagged & ~UINT64_C(4095);
    if (!frag_sha || strcmp(frag_sha, "89f988de48c3b0d1be7378d02bbdcb0514c67301e54fd379cb8109f22d2ba56d") ||
        !blend_sha || strcmp(blend_sha, "8a39f5df7435639900514669d93cf836797abbe07f63ba663e5672cc660dd27a") ||
        (tagged & 4095) != 0x39 || !base || base > (UINT64_C(1) << 48) - 8519680 ||
        ldl_le_p(surface + 8) != 16384 || ldl_le_p(surface + 12) != 8519680) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    return G3D00_JOB_DONE;
}

static uint32_t auto9_vm3_interleaved_texture(Auto9MaliG3d1 *s,
                                      const Auto9G3d00TileBatch *batch,
                                      const Auto9Vm3ClearSpan *out, unsigned count,
                                      unsigned *stage, uint8_t groups[16 * 16 * 4],
                                      const uint32_t **atlas, Auto9Vm3StockTexture *linear)
{
    enum { GROUP_BYTES = 64 * 16 };
    uint8_t rsd[80], program[64], desc[32], sampler[32], surface[16];
    *stage = FRAG_TEXTURE_STATE;
    if (!batch->textures || !batch->samplers ||
        ((batch->textures | batch->samplers) & 31) || batch->push_uniforms) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_vm3_stock_read(s, batch->renderer, rsd, sizeof(rsd), out, count) ||
        !auto9_vm3_stock_read(s, batch->textures, desc, sizeof(desc), out, count) ||
        !auto9_vm3_stock_read(s, batch->samplers, sampler, sizeof(sampler), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    bool linear_rgba = ldl_le_p(desc + 4) == 0x03ff07ff && ldl_le_p(desc + 8) == 0x2688;
    bool short_afbc = ldl_le_p(desc + 4) == 0x03ff07ff && ldl_le_p(desc + 8) == 0xc688;
    bool indexed_atlas = batch->indexed && batch->width == 2048 && batch->height == 1024;
    bool mask_a8 = indexed_atlas && batch->index_count == 30;
    bool bilinear = indexed_atlas && batch->index_count == 6 && linear_rgba;
    bool tall_a8 = !indexed_atlas && batch->triangle_list && ldl_le_p(desc + 4) == 0x07ff07ff;
    bool tiled_a8 = (batch->triangle_list || mask_a8) && ldl_le_p(desc) == 0x06839422 &&
                    (ldl_le_p(desc + 4) == 0x03ff07ff || tall_a8) && ldl_le_p(desc + 8) == 0x1688;
    if (!batch->count && !batch->indexed && !batch->triangle_list &&
        batch->width == 1560 && batch->height == 700 && short_afbc &&
        ldl_le_p(desc) == 0x2ec00022 && !ldl_le_p(desc + 12) && !ldq_le_p(desc + 24) &&
        ldl_le_p(sampler) == 0x42888801 && ldl_le_p(sampler + 4) == 0x1fffc000 &&
        !ldq_le_p(sampler + 8) && !ldq_le_p(sampler + 16) && !ldq_le_p(sampler + 24)) {
        uint32_t result = auto9_vm3_clipped_interleaved_state(s, rsd, desc, out, count);
        if (result == G3D00_JOB_DONE && s->vm3_clipped_afbc_samples < 8 &&
            qemu_loglevel_mask(LOG_GUEST_ERROR)) {
            unsigned sample = ++s->vm3_clipped_afbc_samples;
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 clipped AFBC blend verified: sample=%u attempt=%u triangles=0\n",
                sample, s->vm3_textured_attempts);
        }
        return result;
    }
    if ((indexed_atlas && !bilinear && !(mask_a8 && tiled_a8)) ||
        ldl_le_p(rsd + 8) != 0x10001 || ldl_le_p(rsd + 12) != 0x20000 ||
        ldl_le_p(rsd + 16) != 0x49502a00 || ldl_le_p(rsd + 32) != (mask_a8 ? 0x3710ffff : 0x3750ffff) ||
        ldl_le_p(rsd + 36) != 0x44000000 || ldl_le_p(rsd + 48) != 0x2080 ||
        ldl_le_p(rsd + 60) != 0x1a114202 || ldl_le_p(rsd + 64) != 0x201 ||
        (uint32_t)ldl_le_p(rsd + 68) != (mask_a8 ? 0xf0531531U : 0xf0d32503U) || (ldl_le_p(rsd + 72) & 3) != 2 ||
        ldl_le_p(rsd + 76) != 0xed000 || (!tiled_a8 && ldl_le_p(desc) != 0x2ec00022) ||
        (!tiled_a8 && !linear_rgba && !short_afbc &&
         (ldl_le_p(desc + 4) != 0x07ff07ff || ldl_le_p(desc + 8) != 0xc688)) ||
        ldl_le_p(desc + 12) || ldq_le_p(desc + 24) ||
        ldl_le_p(sampler) != (bilinear ? 0x42888801 : 0x5a888801) || ldl_le_p(sampler + 4) != 0x1fffc000 ||
        ldq_le_p(sampler + 8) || ldq_le_p(sampler + 16) || ldq_le_p(sampler + 24)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t code = ldq_le_p(rsd), surf = ldq_le_p(desc + 16);
    if (!code || (code & 15) || !surf || (surf & 7) ||
        !auto9_vm3_stock_read(s, code, program, sizeof(program), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, program, sizeof(program));
    const char *expected = tiled_a8 ? "5878d902768842639c143f49cd0777375af7c53979ac3a0c6405d39986ce4268" :
                                    "89f988de48c3b0d1be7378d02bbdcb0514c67301e54fd379cb8109f22d2ba56d";
    if (!sha || strcmp(sha, expected)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TEXTURE_SURFACE;
    if (!auto9_vm3_stock_read(s, surf, surface, sizeof(surface), out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (tiled_a8) {
        enum { W = 2048, ROW = W * 16 };
        unsigned height = tall_a8 ? 2048 : 1024, rows = height / 16;
        const uint64_t limit = UINT64_C(1) << 48;
        uint64_t base = ldq_le_p(surface);
        int32_t row = ldl_le_p(surface + 8), slice = ldl_le_p(surface + 12);
        if (!base || (base & 63) || base >= limit || row < ROW || row > 2 * ROW || (row & 63)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        unsigned bytes = (rows - 1) * (unsigned)row + ROW;
        if (bytes > limit - base || ((mask_a8 || tall_a8) && (row != ROW || slice)) ||
            (slice && (slice < (int32_t)bytes || slice > (int32_t)(2 * ROW * rows)))) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        *stage = FRAG_TEXTURE_MEMORY;
        g_autofree uint8_t *copy = g_try_malloc(ROW * rows);
        if (!copy) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        /* The descriptor step is per 16-texel-high tile row. Copy active
         * bytes only, preserving the U order and excluding row padding. */
        for (unsigned y = 0; y < rows; y++) {
            if (!auto9_vm3_stock_read(s, base + (uint64_t)y * row,
                                     copy + (size_t)y * ROW, ROW, out, count)) {
                return G3D00_JOB_READ_FAULT;
            }
        }
        *linear = (Auto9Vm3StockTexture){ .data = copy, .width = W, .height = height,
                                        .row_stride = ROW, .tiled_a8 = true, .destination_mask = mask_a8 };
        copy = NULL;
        return G3D00_JOB_DONE;
    }
    if (linear_rgba) {
        enum { W = 2048, H = 1024, ROW = W * 4 };
        const uint64_t limit = UINT64_C(1) << 48;
        uint64_t base = ldq_le_p(surface);
        int32_t row = ldl_le_p(surface + 8);
        uint32_t slice = ldl_le_p(surface + 12);
        uint32_t step = row < 0 ? -(int64_t)row : row;
        uint64_t delta = (uint64_t)(H - 1) * step, bytes = delta + ROW;
        if (s->vm3_interleaved_fragment_samples < 8) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 interleaved linear surface: n=%u dimensions=%ux%u row=%" PRId32 " slice=%u\n",
                ++s->vm3_interleaved_fragment_samples, W, H, row, slice);
        }
        if (!base || (base & 3) || base >= limit || (step & 3) || (bilinear && (row != ROW || slice)) ||
            step < ROW || step > 2 * ROW || (slice && (slice < bytes || slice > (32U << 20))) ||
            (row < 0 ? delta >= base || ROW > limit - base : bytes > limit - base)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        *stage = FRAG_TEXTURE_MEMORY;
        g_autofree uint8_t *copy = g_try_malloc(ROW * H);
        if (!copy) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        /* O(W*H) checked copy of the active texels. No padding, AFBC
         * header interpretation, or destination-overlapping input. */
        for (unsigned y = 0; y < H; y++) {
            uint64_t off = (uint64_t)y * step;
            if (!auto9_vm3_stock_read(s, row < 0 ? base - off : base + off,
                                     copy + (size_t)y * ROW, ROW, out, count)) {
                return G3D00_JOB_READ_FAULT;
            }
        }
        *linear = (Auto9Vm3StockTexture){ .data = copy, .width = W, .height = H,
                                        .row_stride = ROW, .linear_rgba = true, .repeat_bilinear = bilinear };
        copy = NULL;
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 interleaved linear sampled source: dimensions=%ux%u row=%" PRId32 "\n",
                      W, H, row);
        return G3D00_JOB_DONE;
    }
    if (s->vm3_interleaved_fragment_samples < 8) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 interleaved AFBC texture: n=%u triangles=%u flags=0x%x row=%u slice=%u filter-extra=0x%x\n",
            ++s->vm3_interleaved_fragment_samples, batch->count,
            (unsigned)(ldq_le_p(surface) & 4095), ldl_le_p(surface + 8),
            ldl_le_p(surface + 12), ldl_le_p(sampler + 8));
    }
    uint64_t tagged = ldq_le_p(surface), source = tagged & ~UINT64_C(4095);
    unsigned height = short_afbc ? 1024 : 2048;
    unsigned groups_count = 16 * (height / 128), header_bytes = groups_count * GROUP_BYTES;
    unsigned slice_bytes = header_bytes + 2048 * height * 4;
    if ((tagged & 4095) != 0x39 || ldl_le_p(surface + 8) != 16 * GROUP_BYTES ||
        (uint32_t)ldl_le_p(surface + 12) != slice_bytes ||
        !source || source > (UINT64_C(1) << 48) - slice_bytes) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TEXTURE_MEMORY;
    *atlas = auto9_vm3_atlas_lookup(s, height, source, out, count);
    linear->height = height;
    if (*atlas) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 private atlas sampled: height=%u\n", height);
        return G3D00_JOB_DONE;
    }
    g_autofree uint8_t *headers = g_try_malloc(header_bytes);
    if (!headers ||
        !auto9_vm3_stock_read(s, source, headers, header_bytes, out, count)) {
        return G3D00_JOB_READ_FAULT;
    }
    unsigned solid = 0, mixed = 0, payload = 0, reserved = 0, rgb = 0;
    for (unsigned g = 0; g < groups_count; g++) {
        const uint8_t *first = headers + g * GROUP_BYTES;
        uint32_t rgba = ldl_le_p(first + 8);
        bool uniform = true;
        for (unsigned b = 0; b < 64; b++) {
            const uint8_t *h = first + b * 16;
            if (h[4] & 63) {
                payload++;
                uniform = false;
            } else if (ldq_le_p(h) || ldl_le_p(h + 12)) {
                reserved++;
                uniform = false;
            } else {
                solid++;
                uniform &= (uint32_t)ldl_le_p(h + 8) == rgba;
            }
        }
        stl_le_p(groups + g * 4, rgba);
        mixed += !uniform;
        rgb += uniform && !!(rgba & 0xffffff);
    }
    if (s->vm3_interleaved_fragment_samples <= 8) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 interleaved AFBC groups: solid=%u mixed=%u payload=%u reserved=%u rgb-groups=%u\n",
            solid, mixed, payload, reserved, rgb);
        if (qemu_loglevel_mask(LOG_GUEST_ERROR)) {
            auto9_vm3_atlas_origin(s, source);
        }
    }
    return mixed ? G3D00_JOB_CONFIG_FAULT : G3D00_JOB_DONE;
}

static unsigned auto9_vm3_stock_spread(unsigned v)
{
    v &= 15;
    v = (v | (v << 2)) & 0x33;
    return (v | (v << 1)) & 0x55;
}

static const uint8_t *auto9_vm3_stock_texel(const Auto9Vm3StockTexture *tex,
                                           unsigned x, unsigned y)
{
    if (tex->linear_rgba) {
        return tex->data + (size_t)y * tex->row_stride + x * 4;
    }
    unsigned offset = (auto9_vm3_stock_spread(y) * 3) ^ auto9_vm3_stock_spread(x);
    return tex->data + (size_t)(y >> 4) * tex->row_stride + (x >> 4) * 1024 + offset * 4;
}

/* Classify only active texels: unused edges of padded tiles can contain
 * nonzero bytes unrelated to the producer. No texture bytes are logged. */
static void auto9_vm3_stock_texture_profile(const Auto9Vm3StockTexture *tex,
                                           uint64_t *rgb, uint64_t *alpha)
{
    *rgb = *alpha = 0;
    if (tex->linear_rgba) {
        for (unsigned y = 0; y < tex->height; y++) {
            const uint8_t *p = tex->data + (size_t)y * tex->row_stride;
            for (unsigned x = 0; x < tex->width; x++, p += 4) {
                *rgb += !!(p[0] | p[1] | p[2]);
                *alpha += p[3] != 0;
            }
        }
        return;
    }
    for (unsigned y = 0; y < tex->height; y += 16) {
        for (unsigned x = 0; x < tex->width; x += 16) {
            if (x + 16 <= tex->width && y + 16 <= tex->height) {
                const uint8_t *tile = auto9_vm3_stock_texel(tex, x, y);
                for (unsigned k = 0; k < 256; k++, tile += 4) {
                    *rgb += !!(tile[0] | tile[1] | tile[2]);
                    *alpha += tile[3] != 0;
                }
            } else {
                for (unsigned ty = y; ty < MIN(y + 16, tex->height); ty++) {
                    for (unsigned tx = x; tx < MIN(x + 16, tex->width); tx++) {
                        const uint8_t *p = auto9_vm3_stock_texel(tex, tx, ty);
                        *rgb += !!(p[0] | p[1] | p[2]);
                        *alpha += p[3] != 0;
                    }
                }
            }
        }
    }
}

/* The scratch tile and resolved memory are BGRA. Convert the verified
 * linear RGBA source to that order before the component-wise blend. */
static void auto9_vm3_stock_sample(const Auto9Vm3StockTexture *tex, double u, double v,
                                  float src[4])
{
    if (tex->nearest) {
        unsigned x = MIN(MAX(u * tex->width, 0.0), tex->width - 1.0);
        unsigned y = MIN(MAX(v * tex->height, 0.0), tex->height - 1.0);
        const uint8_t *pixel = auto9_vm3_stock_texel(tex, x, y);
        for (unsigned k = 0; k < 4; k++) {
            float value = auto9_vm3_stock_half(auto9_bifrost_unorm8_half(pixel[k]));
            src[k] = auto9_vm3_stock_half(auto9_bifrost_f32_half_rne(value * tex->alpha));
        }
        return;
    }
    double x = MIN(MAX(u * tex->width - 0.5, 0.0), tex->width - 1.0);
    double y = MIN(MAX(v * tex->height - 0.5, 0.0), tex->height - 1.0);
    unsigned x0 = x, y0 = y, x1 = MIN(x0 + 1, tex->width - 1), y1 = MIN(y0 + 1, tex->height - 1);
    float fx = x - x0, fy = y - y0;
    const uint8_t *a = auto9_vm3_stock_texel(tex, x0, y0);
    const uint8_t *b = auto9_vm3_stock_texel(tex, x1, y0);
    const uint8_t *c = auto9_vm3_stock_texel(tex, x0, y1);
    const uint8_t *d = auto9_vm3_stock_texel(tex, x1, y1);
    for (unsigned k = 0; k < 4; k++) {
        unsigned i = tex->linear_rgba && k < 3 ? 2 - k : k;
        float upper = a[i] + (b[i] - a[i]) * fx;
        float lower = c[i] + (d[i] - c[i]) * fx;
        float unorm = (upper + (lower - upper) * fy) * (1.0f / 255.0f);
        float preloaded = auto9_vm3_stock_half(auto9_bifrost_f32_half_rne(unorm));
        src[k] = auto9_vm3_stock_half(auto9_bifrost_f32_half_rne(preloaded * tex->alpha));
    }
}

/*
 * The four sample positions each give one integer x interval on a scanline:
 * intersect the three linear edge inequalities, then union the intervals.
 * The coverage-only path never reads samples. Work is proportional to the
 * number of scanlines and marked tile ranges, not to all pixels in the box.
 */
static unsigned auto9_vm3_stock_coverage(const Auto9G3d00Triangle *tri, unsigned tiles_x,
                                         const bool inclusive[3],
                                         const int64_t step_x[3], const int64_t step_y[3],
                                         int64_t row_edge[3], const int64_t sample_edge[4][3],
                                         uint8_t *dirty)
{
    unsigned left = tri->pixel[0], count = 0;
    int64_t last_x = tri->pixel[2] - (int64_t)left;
    for (unsigned y = tri->pixel[1]; y <= tri->pixel[3]; y++) {
        struct { unsigned lo, hi; } range[4], val;
        unsigned n = 0;
        for (unsigned k = 0; k < 4; k++) {
            int64_t lo = 0, hi = last_x;
            for (unsigned j = 0; j < 3 && lo <= hi; j++) {
                int64_t e = row_edge[j] + sample_edge[k][j] - !inclusive[j];
                int64_t dx = step_x[j];
                if (dx < 0) {
                    if (e < 0) {
                        hi = -1;
                    } else {
                        hi = MIN(hi, e / -dx);
                    }
                } else if (e < 0) {
                    if (dx == 0) {
                        hi = -1;
                    } else {
                        /* ceil(-e / dx) without negating INT64_MIN. */
                        lo = MAX(lo, -(e + 1) / dx + 1);
                    }
                }
            }
            if (lo <= hi) {
                val.lo = lo;
                val.hi = hi;
                unsigned i = n++;
                while (i && range[i - 1].lo > val.lo) {
                    range[i] = range[i - 1];
                    i--;
                }
                range[i] = val;
            }
        }
        for (unsigned i = 0; i < n;) {
            unsigned lo = range[i].lo, hi = range[i++].hi;
            while (i < n && range[i].lo <= hi + 1) {
                hi = MAX(hi, range[i].hi);
                i++;
            }
            count += hi - lo + 1;
            unsigned first_tile = (left + lo) >> 4, last_tile = (left + hi) >> 4;
            memset(dirty + (y >> 4) * tiles_x + first_tile, 1, last_tile - first_tile + 1);
        }
        for (unsigned j = 0; j < 3; j++) {
            row_edge[j] += step_y[j];
        }
    }
    return count;
}

/* Rasterize the captured rotated 4x pattern. The shader executes once per
 * covered pixel; its single result is blended into the covered samples. */
static unsigned auto9_vm3_stock_raster(const Auto9G3d00Triangle *tri,
                                       const Auto9Vm3StockTexture *tex,
                                       unsigned width, unsigned tiles_x,
                                       uint8_t *samples, uint8_t *dirty, uint64_t *rgb_samples)
{
    static const int8_t dx[4] = { -32, 96, -96, 32 };
    static const int8_t dy[4] = { -96, -32, 32, 96 };
    unsigned v[3] = { 0, 1, 2 }, covered = 0;
    int64_t area = auto9_g3d00_edge(tri->xy[0], tri->xy[1], tri->xy[2][0], tri->xy[2][1]);
    if (!area) {
        return 0;
    }
    if (area < 0) {
        v[1] = 2;
        v[2] = 1;
    }
    bool inclusive[3];
    int64_t step_x[3], step_y[3], row_edge[3], sample_edge[4][3];
    double uv[3][2], rw[3];
    for (unsigned j = 0; j < 3; j++) {
        const int32_t *a = tri->xy[v[(j + 1) % 3]], *b = tri->xy[v[(j + 2) % 3]];
        int32_t gx = a[1] - b[1], gy = b[0] - a[0];
        step_x[j] = (int64_t)gx * 256;
        step_y[j] = (int64_t)gy * 256;
        inclusive[j] = auto9_g3d00_top_left(a, b);
        row_edge[j] = auto9_g3d00_edge(a, b, tri->pixel[0] * 256 + 128,
                                           tri->pixel[1] * 256 + 128);
        for (unsigned k = 0; k < 4; k++) {
            sample_edge[k][j] = (int64_t)gx * dx[k] + (int64_t)gy * dy[k];
        }
        if (tex) {
            rw[j] = tri->inv_w[v[j]];
            for (unsigned k = 0; k < 2; k++) {
                uv[j][k] = rw[j] * auto9_vm3_stock_half(tri->uv[v[j]][k]);
            }
        }
    }
    if (!tex) {
        return auto9_vm3_stock_coverage(tri, tiles_x, inclusive, step_x, step_y,
                                       row_edge, sample_edge, dirty);
    }
    for (unsigned y = tri->pixel[1]; y <= tri->pixel[3]; y++) {
        int64_t edge[3] = { row_edge[0], row_edge[1], row_edge[2] };
        for (unsigned x = tri->pixel[0]; x <= tri->pixel[2]; x++) {
            unsigned mask = 0;
            for (unsigned k = 0; k < 4; k++) {
                bool inside = true;
                for (unsigned j = 0; j < 3; j++) {
                    int64_t e = edge[j] + sample_edge[k][j];
                    inside &= e > 0 || (!e && inclusive[j]);
                }
                mask |= (unsigned)inside << k;
            }
            if (mask && tex) {
                double sum = 0, uu = 0, vv = 0;
                for (unsigned j = 0; j < 3; j++) {
                    sum += edge[j] * rw[j];
                    uu += edge[j] * uv[j][0];
                    vv += edge[j] * uv[j][1];
                }
                /* A covered edge pixel's center can lie outside the
                 * primitive. Fall back to a covered sample if its W fails. */
                if (!(sum > 0)) {
                    unsigned k = ctz32(mask);
                    sum = uu = vv = 0;
                    for (unsigned j = 0; j < 3; j++) {
                        double e = edge[j] + sample_edge[k][j];
                        sum += e * rw[j];
                        uu += e * uv[j][0];
                        vv += e * uv[j][1];
                    }
                }
                float src[4];
                auto9_vm3_stock_sample(tex, uu / sum, vv / sum, src);
                *rgb_samples += src[0] > 0 || src[1] > 0 || src[2] > 0;
                uint8_t *dst = samples + ((size_t)y * width + x) * 16;
                float inv_a = 1.0f - src[3];
                for (unsigned k = 0; k < 4; k++) {
                    if (mask & (1U << k)) {
                        for (unsigned c = 0; c < 4; c++) {
                            float color = src[c] * 255.0f + dst[k * 4 + c] * inv_a;
                            dst[k * 4 + c] = MIN(color + 0.5f, 255.0f);
                        }
                    }
                }
            }
            if (mask) {
                dirty[(y >> 4) * tiles_x + (x >> 4)] = 1;
                covered++;
            }
            for (unsigned j = 0; j < 3; j++) {
                edge[j] += step_x[j];
            }
        }
        for (unsigned j = 0; j < 3; j++) {
            row_edge[j] += step_y[j];
        }
    }
    return covered;
}

/* Commit only dirty tiles in the preflighted, ordered DMA spans. Preserve
 * uncovered tiles, as well as row padding and their virtual CRC entries. */
static bool auto9_vm3_stock_commit(const Auto9Vm3ClearSpan *spans, unsigned count,
                                    const uint8_t *image, unsigned stride,
                                    const uint8_t *dirty, unsigned tiles_x,
                                    unsigned tile_bytes, unsigned tile_rows,
                                    uint64_t *bytes)
{
    static const uint8_t zero[4096];
    unsigned row = 0, pos = 0;
    for (unsigned i = 0; i < count; i++) {
        unsigned offset = 0;
        while (offset < spans[i].length) {
            unsigned col = (pos + offset) / tile_bytes;
            const uint8_t *tile = dirty + row / tile_rows * tiles_x;
            unsigned end = col + 1;
            while (end < tiles_x && end * tile_bytes < pos + spans[i].length &&
                   tile[end] == tile[col]) {
                end++;
            }
            unsigned len = MIN(end * tile_bytes - pos, spans[i].length) - offset;
            if (tile[col]) {
                const uint8_t *src = image ? image + (size_t)row * stride + pos + offset : zero;
                if (address_space_write(&address_space_memory, spans[i].pa + offset,
                                        MEMTXATTRS_UNSPECIFIED, src, len) != MEMTX_OK) {
                    return false;
                }
                *bytes += len;
            }
            offset += len;
        }
        pos += spans[i].length;
        if (pos == stride) {
            pos = 0;
            row++;
        }
    }
    return true;
}

enum {
    VM3_AFBC_GROUP_SIDE = 8,
    VM3_AFBC_BLOCKS = VM3_AFBC_GROUP_SIDE * VM3_AFBC_GROUP_SIDE,
    VM3_AFBC_HEADER_GROUP = VM3_AFBC_BLOCKS * 16,
    VM3_AFBC_MAX_GROUPS = 78, /* ceil(1560/128) * ceil(700/128) */
};

/* The driver groups 8x8 superblocks in raster order. Within a group,
 * uniform bytes do not depend on header permutation. Do not guess a
 * permutation to write a partially dirty group. Edge padding within a
 * written group is covered by the same padded attachment allocation. */
static bool auto9_vm3_afbc_groups(const uint8_t *dirty, unsigned tx, unsigned ty,
                                 uint8_t groups[VM3_AFBC_MAX_GROUPS],
                                 unsigned *selected, unsigned *mixed)
{
    unsigned nx = (tx + 7) / 8, ny = (ty + 7) / 8;
    if (!nx || !ny || nx > VM3_AFBC_MAX_GROUPS / ny) {
        return false;
    }
    *selected = *mixed = 0;
    for (unsigned gy = 0; gy < ny; gy++) {
        for (unsigned gx = 0; gx < nx; gx++) {
            unsigned on = 0, count = 0;
            for (unsigned y = gy * 8; y < MIN(gy * 8 + 8, ty); y++) {
                for (unsigned x = gx * 8; x < MIN(gx * 8 + 8, tx); x++) {
                    on += dirty[y * tx + x] != 0;
                    count++;
                }
            }
            groups[gy * nx + gx] = on == count;
            *selected += on == count;
            *mixed += on != 0 && on != count;
        }
    }
    return *mixed == 0;
}

/* Plan only touched header groups. A solid block has no payload and does
 * not require writable pages in the sparse AFBC body. */
static bool auto9_vm3_afbc_plan(Auto9MaliG3d1 *s, uint64_t base,
                               const uint8_t groups[VM3_AFBC_MAX_GROUPS],
                               Auto9Vm3ClearSpan *out, unsigned limit, unsigned *used)
{
    for (unsigned g = 0; g < VM3_AFBC_MAX_GROUPS; g++) {
        if (groups[g] &&
            !auto9_vm3_clear_plan(s, base + g * VM3_AFBC_HEADER_GROUP, 1, 0,
                                 VM3_AFBC_HEADER_GROUP, true, out, limit, used)) {
            return false;
        }
    }
    return true;
}

/* Bifrost v7 tiled AFBC supports solid blocks. A zero first subblock size
 * selects the embedded value in word 2; D24S8 uses the RGBA8 AFBC mode
 * (Z24 low bits, S8 high bits). No body pointer or body write is needed. */
static bool auto9_vm3_afbc_commit(const Auto9Vm3ClearSpan *spans, unsigned count, uint64_t *written)
{
    uint8_t data[VM3_AFBC_HEADER_GROUP] = {0};
    for (unsigned off = 8; off < sizeof(data); off += 16) {
        stl_le_p(data + off, 0x00ffffff);
    }
    for (unsigned i = 0; i < count; i++) {
        /* Every header group is 1 KiB inside a single GPU page. */
        if (spans[i].length != sizeof(data) ||
            address_space_write(&address_space_memory, spans[i].pa,
                    MEMTXATTRS_UNSPECIFIED, data, sizeof(data)) != MEMTX_OK) {
            return false;
        }
        *written += sizeof(data);
    }
    return true;
}

/* The known RT clears clean pixels in dirty tiles and has no pre-frame
 * shader. The optional exact Z/S profile stores the uniform Z/S clear;
 * its pinned draw state does not test or modify individual Z/S samples.
 * Blend reads per-sample tile color. TE, precision and dither are approximate. */
static void auto9_vm3_textured_state_fault(Auto9MaliG3d1 *s,
        const Auto9G3d00TileBatch *batch, const Auto9Vm3ClearSpan *out,
        unsigned count, unsigned ordinal, unsigned stage, unsigned status,
        bool single)
{
    if (!single || stage != FRAG_TEXTURE_STATE ||
        !qemu_loglevel_mask(LOG_GUEST_ERROR) || s->vm3_textured_state_fault_samples >= 8) {
        return;
    }
    unsigned n = ++s->vm3_textured_state_fault_samples;
    uint8_t state[80], texture[32], sampler[32];
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 center texture fault: sample=%u attempt=%u batch=%u status=0x%x "
        "triangles=%u indexed=%u indices=%u list=%u radial=%u dual=%u interleaved=%u "
        "textures=%u samplers=%u fau=%u\n", n, s->vm3_textured_attempts, ordinal, status,
        batch->count, (unsigned)batch->indexed, batch->index_count, (unsigned)batch->triangle_list,
        (unsigned)batch->radial_fan, (unsigned)batch->dual_uv, (unsigned)batch->interleaved_fan,
        (unsigned)!!batch->textures, (unsigned)!!batch->samplers,
        (unsigned)!!batch->push_uniforms);
    /* Read only the failing batch's descriptors, not texture data or FAU.
     * Use the resolver's mapping and destination exclusion even on fault. */
    if (batch->textures && !(batch->textures & 31) &&
        auto9_vm3_stock_read(s, batch->textures, texture, sizeof(texture), out, count)) {
        memset(texture + 16, 0, 8); /* Surface pointer is not needed. */
        auto9_g3d00_sample_bytes("vm3-center-texture-fault", n, "texture", 0, texture, sizeof(texture));
    }
    if (batch->samplers && !(batch->samplers & 31) &&
        auto9_vm3_stock_read(s, batch->samplers, sampler, sizeof(sampler), out, count)) {
        auto9_g3d00_sample_bytes("vm3-center-texture-fault", n, "sampler", 0, sampler, sizeof(sampler));
    }
    if (!batch->renderer || (batch->renderer & 63) ||
        !auto9_vm3_stock_read(s, batch->renderer, state, sizeof(state), out, count)) {
        return;
    }
    uint64_t pc = ldq_le_p(state);
    auto9_g3d00_sample_bytes("vm3-center-texture-fault", n, "renderer", 8, state + 8, sizeof(state) - 8);
    if (!pc || (pc & 15) || pc > (UINT64_C(1) << 48) - 256) {
        return;
    }
    uint8_t program[256];
    for (unsigned read = 16; read <= sizeof(program); read += 16) {
        unsigned stop, reason;
        if (!auto9_vm3_stock_read(s, pc + read - 16, program + read - 16, 16, out, count)) {
            break;
        }
        unsigned size = auto9_g3d1_bifrost_extent(program, read, &stop, &reason);
        if (size) {
            auto9_g3d00_sample_bytes("vm3-center-texture-fault", n, "shader", 0, program, size);
            break;
        }
        if (reason != 6) {
            break;
        }
    }
}

static uint32_t auto9_g3d1_vm3_textured_resolve(Auto9MaliG3d1 *s, uint64_t head,
                                                uint64_t status_pa, const uint8_t job[64],
                                                const uint8_t fb[128], const uint8_t ext[64],
                                                const uint8_t rt[64], unsigned *stage)
{
    uint32_t wh = ldl_le_p(fb + 32), clear = ldl_le_p(rt + 48);
    unsigned width = (wh & 65535) + 1, height = (wh >> 16) + 1;
    unsigned row = width * 4, tx = (width + 15) / 16, ty = (height + 15) / 16;
    uint32_t pitch = ldl_le_p(rt + 40), crc_pitch = ldl_le_p(ext + 8);
    uint32_t flags = ldl_le_p(fb + 48);
    bool afbc = flags == 0xc0250000U;
    bool single = flags == 0x80210000U && ldl_le_p(fb + 44) == 0x01001000 &&
                  (uint32_t)ldl_le_p(rt + 4) == 0x86888299U;
    uint64_t tag = ldq_le_p(job + 40), locs = ldq_le_p(fb + 16);
    uint64_t raster = ldq_le_p(rt + 32), crc = ldq_le_p(ext), context = ldq_le_p(fb + 56);
    uint64_t zheader = afbc ? ldq_le_p(ext + 16) : 0, zbody = afbc ? ldq_le_p(ext + 32) : 0;
    unsigned max_spans = height * (2 + (row - 1) / 4096) + 2 * ty;
    unsigned used = 0, raster_spans, crc_end, batches = 0, color_batches = 0, interleaved = 0, dual = 0;
    unsigned selected = 0, mixed = 0, radial_batches = 0;
    uint8_t depth_groups[VM3_AFBC_MAX_GROUPS] = {0};
    uint64_t work = 0, shaded = 0, nonzero = 0, written = 0, invalidated = 0, rgb_samples = 0, zwritten = 0;
    uint64_t a8_pixels = 0, a8_rgb = 0, full_a8_pixels = 0;
    uint64_t dual_pixels = 0, dual_rgb = 0;
    uint64_t radial_pixels = 0, radial_rgb = 0;
    uint64_t color_pixels = 0, color_rgb_pixels = 0;

    *stage = FRAG_PROFILE;
    if (width > 1920 || height > 1080 || (tag & 63) != 3 || ldl_le_p(job + 32) ||
        (uint32_t)ldl_le_p(job + 36) != ((ty - 1) << 16 | (tx - 1)) ||
        ldq_le_p(job + 48) || ldq_le_p(job + 56) || (ldl_le_p(fb) & 0x1ff) ||
        !locs || (locs & 7) || ldl_le_p(fb + 36) || (uint32_t)ldl_le_p(fb + 40) != wh ||
        (!single && (ldl_le_p(fb + 44) != 0x04001012 ||
                     (flags != 0xc0210000U && !afbc) ||
                     (uint32_t)ldl_le_p(rt + 4) != 0x860a9299U)) ||
        ldl_le_p(rt) != 0x04000000 ||
        ldl_le_p(rt + 44) || (uint32_t)ldl_le_p(rt + 52) != clear ||
        (uint32_t)ldl_le_p(rt + 56) != clear || (uint32_t)ldl_le_p(rt + 60) != clear ||
        pitch < row || pitch > row + 63 || (pitch & 63) || (raster & 63) ||
        (crc && (crc_pitch < tx * 8 || crc_pitch > 4096 || (crc_pitch & 7) || (crc & 7))) ||
        ((uint32_t)ldl_le_p(ext + 12) & 0x7800U)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (single && (wh != 0x02bb0617 || pitch != 6272 || ldq_le_p(fb + 24) ||
                   ldl_le_p(fb + 52) || !crc || crc_pitch != 832 ||
                   ldl_le_p(ext + 12) != 0x00110420 || ldq_le_p(ext + 16) ||
                   ldq_le_p(ext + 32))) {
        if (s->vm3_fragment_tiler_rejections < 8) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 single fragment profile: frame=%u z=0x%x crc=%u/%u ext=0x%x aux=%u\n",
                (unsigned)!!ldq_le_p(fb + 24), ldl_le_p(fb + 52), (unsigned)!!crc,
                crc_pitch, ldl_le_p(ext + 12), (unsigned)!!(ldq_le_p(ext + 16) | ldq_le_p(ext + 32)));
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (afbc) {
        *stage = FRAG_AFBC_LAYOUT;
        if (wh != 0x02bb0617 || pitch != 6272 || ldq_le_p(fb + 24) ||
            ldl_le_p(fb + 52) != 0x3f800000 || ldl_le_p(ext + 12) != 0x001105d5 ||
            ldl_le_p(ext + 24) != 104 || ldl_le_p(ext + 28) || ldq_le_p(ext + 40) ||
            !zheader || ((zheader | zbody) & 4095) || zbody <= zheader ||
            zbody - zheader < VM3_AFBC_MAX_GROUPS * VM3_AFBC_HEADER_GROUP ||
            zbody - zheader > (1 << 20)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        max_spans += VM3_AFBC_MAX_GROUPS;
    }
    *stage = FRAG_STORAGE;
    g_autofree Auto9Vm3ClearSpan *out = g_try_new(Auto9Vm3ClearSpan, max_spans);
    g_autofree Auto9Vm3ClearSpan *sorted = g_try_new(Auto9Vm3ClearSpan, max_spans);
    if (!out || !sorted) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TRANSLATE;
    uint64_t header_pa;
    if (!auto9_g3d00_translate(s, head, 64, false, &header_pa) || header_pa != status_pa ||
        !auto9_vm3_clear_plan(s, raster, height, pitch, row, true, out, max_spans, &used)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    raster_spans = used;
    crc_end = used;
    /* Original AFBC Z/S jobs keep the CRC extension with a null CRC pointer. */
    if (crc &&
        !auto9_vm3_clear_plan(s, crc, ty, crc_pitch, tx * 8, true, out, max_spans, &used)) {
        if (afbc && s->vm3_center_depth_observed < 8) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 AFBC CRC plan failed: pitch=%u tiles=%ux%u\n",
                crc_pitch, tx, ty);
        }
        return G3D00_JOB_WRITE_FAULT;
    }
    if (crc) {
        crc_end = used;
    }
    if (afbc) {
        uint8_t coverage[98 * 44] = {0};
        uint64_t coverage_work = 0;
        *stage = FRAG_AFBC_COVERAGE;
        for (unsigned i = 0; i < s->tile_batch_count; i++) {
            const Auto9G3d00TileBatch *b = &s->tile_batch[i];
            if (b->ttbr != s->as0_active_ttbr || b->context != context ||
                b->width != width || b->height != height) {
                continue;
            }
            for (unsigned j = 0; j < b->count; j++) {
                const Auto9G3d00Triangle *t = &b->triangle[j];
                coverage_work += (uint64_t)(t->pixel[2] - t->pixel[0] + 1) *
                                 (t->pixel[3] - t->pixel[1] + 1);
                if (coverage_work > (uint64_t)width * height * 8) {
                    return G3D00_JOB_CONFIG_FAULT;
                }
                if (b->color_fan || b->interleaved_fan || b->dual_uv) {
                    auto9_vm3_color_raster(t, width, tx, NULL, coverage, NULL, NULL, NULL, NULL, NULL, NULL);
                } else {
                    auto9_vm3_stock_raster(t, NULL, width, tx, NULL, coverage, NULL);
                }
            }
        }
        if (!auto9_vm3_afbc_groups(coverage, tx, ty, depth_groups, &selected, &mixed)) {
            if (s->vm3_center_depth_observed < 8) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 G3D1 VM3 AFBC partial depth groups: complete=%u mixed=%u\n", selected, mixed);
            }
            return G3D00_JOB_CONFIG_FAULT;
        }
        *stage = FRAG_AFBC_HEADER;
        if (!auto9_vm3_afbc_plan(s, zheader, depth_groups, out, max_spans, &used) ||
            used - crc_end != selected) {
            return G3D00_JOB_WRITE_FAULT;
        }
        *stage = FRAG_AFBC_ALIAS;
    }
    memcpy(sorted, out, used * sizeof(*out));
    qsort(sorted, used, sizeof(*sorted), auto9_vm3_clear_span_cmp);
    for (unsigned i = 1; i < used; i++) {
        if (auto9_g3d00_overlap(sorted[i - 1].pa, sorted[i - 1].length,
                                sorted[i].pa, sorted[i].length)) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    if (afbc) {
        *stage = FRAG_AFBC_DEPENDENCY;
    }
    if (!auto9_vm3_stock_disjoint(header_pa, 64, sorted, used) ||
        !auto9_vm3_stock_read(s, tag & ~UINT64_C(63), NULL, 256, sorted, used) ||
        !auto9_vm3_stock_read(s, context, NULL, 192, sorted, used) ||
        !auto9_vm3_stock_read(s, locs, NULL, single ? 16 : 132, sorted, used)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    *stage = FRAG_STORAGE;
    unsigned pixels = width * height;
    g_autofree uint8_t *samples = g_try_malloc((size_t)pixels * 16);
    g_autofree uint8_t *image = g_try_malloc((size_t)pixels * 4);
    g_autofree uint8_t *dirty = g_try_malloc((size_t)tx * ty);
    if (!samples || !image || !dirty) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    memset(dirty, 0, (size_t)tx * ty);
    uint32_t bgra = (clear & 0xff00ff00U) | ((clear & 0xffU) << 16) | ((clear >> 16) & 0xffU);
    /* The accepted descriptors have no active pre-frame shader. A
     * transparent clear is still a clear, not a request to load a reused
     * allocation. Preserve untouched tiles by omitting their DMA writes. */
    if (!bgra) {
        memset(samples, 0, (size_t)pixels * 16);
    } else {
        for (unsigned i = 0; i < pixels * 4; i++) {
            stl_le_p(samples + i * 4, bgra);
        }
    }
    for (unsigned i = 0; i < s->tile_batch_count; i++) {
        const Auto9G3d00TileBatch *b = &s->tile_batch[i];
        if (b->ttbr != s->as0_active_ttbr || b->context != context ||
            b->width != width || b->height != height) {
            continue;
        }
        batches++;
        if (b->color_fan || b->interleaved_fan || b->dual_uv) {
            uint8_t groups[16 * 16 * 4];
            const uint8_t *texture = NULL;
            const uint32_t *atlas = NULL;
            Auto9Vm3StockTexture sources[2] = {0};
            Auto9Vm3Radial radial;
            g_autofree uint8_t *linear_data = NULL;
            uint32_t result;
            if (b->radial_fan) {
                result = auto9_vm3_radial_texture(s, b, sorted, used, stage, sources, &radial);
                linear_data = sources[0].data;
                radial_batches++;
            } else if (b->dual_uv) {
                result = auto9_vm3_dual_uv_textures(s, b, sorted, used, stage, sources);
                linear_data = sources[0].data;
                dual++;
            } else if (b->interleaved_fan) {
                result = auto9_vm3_interleaved_texture(s, b, sorted, used, stage, groups, &atlas, sources);
                linear_data = sources[0].data;
                texture = linear_data ? NULL : groups;
                interleaved++;
            } else {
                result = auto9_vm3_color_state(s, b, sorted, used, stage);
            }
            if (result != G3D00_JOB_DONE) {
                auto9_vm3_textured_state_fault(s, b, sorted, used, batches, *stage, result, single);
                return result;
            }
            color_batches++;
            for (unsigned j = 0; j < b->count; j++) {
                const Auto9G3d00Triangle *t = &b->triangle[j];
                work += (uint64_t)(t->pixel[2] - t->pixel[0] + 1) * (t->pixel[3] - t->pixel[1] + 1);
                if (work > (uint64_t)pixels * 8) {
                    *stage = FRAG_BUDGET;
                    return G3D00_JOB_CONFIG_FAULT;
                }
                uint64_t before = color_rgb_pixels;
                unsigned covered = auto9_vm3_color_raster(t, width, tx, samples, dirty,
                                                         &color_rgb_pixels, texture, atlas,
                                                         sources[0].height ? &sources[0] : NULL,
                                                         b->dual_uv ? &sources[1] : NULL,
                                                         b->radial_fan ? &radial : NULL);
                color_pixels += covered;
                if (b->radial_fan) {
                    radial_pixels += covered;
                    radial_rgb += color_rgb_pixels - before;
                }
                if (sources[0].tiled_a8) {
                    a8_pixels += covered;
                    if (sources[0].height == 2048) {
                        full_a8_pixels += covered;
                    }
                    a8_rgb += color_rgb_pixels - before;
                }
                if (b->dual_uv) {
                    dual_pixels += covered;
                    dual_rgb += color_rgb_pixels - before;
                }
            }
            continue;
        }
        /* The prior texture program uses true four-sample coverage. Do not
         * use it on this one-sample target until its single-sample state is known. */
        if (single) {
            *stage = FRAG_PROFILE;
            return G3D00_JOB_CONFIG_FAULT;
        }
        Auto9Vm3StockTexture tex = {0};
        uint32_t result = auto9_vm3_stock_texture(s, b, sorted, used, &tex, stage);
        if (result == G3D00_JOB_DONE) {
            uint64_t rgb, alpha;
            auto9_vm3_stock_texture_profile(&tex, &rgb, &alpha);
            s->vm3_texture_loaded++;
            s->vm3_texture_zero_opacity += tex.alpha == 0;
            s->vm3_texture_rgb_texels += rgb;
            s->vm3_texture_alpha_texels += alpha;
            if (tex.nearest) {
                s->vm3_nearest_texture_loads++;
                s->vm3_nearest_rgb_texels += rgb;
            }
            if (tex.linear_rgba) {
                s->vm3_linear_rgba_loads++;
                s->vm3_linear_rgba_rgb_texels += rgb;
                if (tex.traced_rows[0]) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 VM3 center read: n=%u attempt=%u footprint=%s "
                        "rgb=%" PRIu64 " alpha=%" PRIu64 " reverse=%u\n",
                        ++s->vm3_center_reads_traced, s->vm3_textured_attempts,
                        tex.traced_rows, rgb, alpha, (unsigned)tex.reverse_rows);
                }
                if (tex.reverse_rows && ++s->vm3_linear_reverse_loads <= 4) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 G3D1 VM3 linear RGBA reverse rows loaded: attempt=%u pitch=%u rows=%u\n",
                        s->vm3_textured_attempts, tex.row_stride, tex.height);
                }
            }
            if (s->vm3_textured_attempts <= 4) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 G3D1 VM3 texture content: dimensions=%ux%u rgb=%" PRIu64
                    " alpha=%" PRIu64 " zero-opacity=%u\n",
                    tex.width, tex.height, rgb, alpha, (unsigned)(tex.alpha == 0));
            }
            /* A zero vector under src-over leaves every covered sample
             * unchanged. Still execute coverage and retain dirty-tile,
             * shader-input validation, and AFBC writeback semantics. */
            const Auto9Vm3StockTexture *sample_tex =
                (tex.alpha == 0 || (!rgb && !alpha)) ? NULL : &tex;
            for (unsigned j = 0; j < b->count; j++) {
                const Auto9G3d00Triangle *t = &b->triangle[j];
                work += (uint64_t)(t->pixel[2] - t->pixel[0] + 1) * (t->pixel[3] - t->pixel[1] + 1);
                if (work > (uint64_t)pixels * 8) {
                    *stage = FRAG_BUDGET;
                    result = G3D00_JOB_CONFIG_FAULT;
                    break;
                }
                shaded += auto9_vm3_stock_raster(t, sample_tex, width, tx, samples, dirty, &rgb_samples);
            }
        }
        g_free(tex.data);
        if (result != G3D00_JOB_DONE) {
            return result;
        }
    }
    if (!batches) {
        *stage = FRAG_NO_BATCH;
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (single && !interleaved && !dual && !radial_batches) {
        *stage = FRAG_PROFILE;
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (afbc) {
        uint8_t resolved[VM3_AFBC_MAX_GROUPS];
        *stage = FRAG_AFBC_COVERAGE;
        if (!auto9_vm3_afbc_groups(dirty, tx, ty, resolved, &selected, &mixed) ||
            memcmp(depth_groups, resolved, sizeof(resolved))) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    for (unsigned y = 0; y < height; y++) {
        for (unsigned x = 0; x < width; x++) {
            if (!dirty[(y >> 4) * tx + (x >> 4)]) {
                continue;
            }
            unsigned i = y * width + x;
            const uint8_t *src = samples + (size_t)i * 16;
            uint8_t *dst = image + (size_t)i * 4;
            if (single) {
                /* RT swizzle 0x688 is RGBA. Keep the internal blend order
                 * shared with the existing BGRA path, then store one sample. */
                dst[0] = src[2];
                dst[1] = src[1];
                dst[2] = src[0];
                dst[3] = src[3];
            } else {
                for (unsigned c = 0; c < 4; c++) {
                    dst[c] = (src[c] + src[c + 4] + src[c + 8] + src[c + 12] + 2) >> 2;
                }
            }
            nonzero += !!(dst[0] | dst[1] | dst[2]);
        }
    }
    *stage = FRAG_WRITE;
    if (afbc && !auto9_vm3_afbc_commit(out + crc_end, used - crc_end, &zwritten)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    if (!auto9_vm3_stock_commit(out, raster_spans, image, row, dirty, tx, 64, 16, &written) ||
        !auto9_vm3_stock_commit(out + raster_spans, crc_end - raster_spans, NULL, tx * 8,
                              dirty, tx, 8, 1, &invalidated)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    if (color_batches) {
        s->vm3_color_pixels += color_pixels;
        if (++s->vm3_color_resolves <= 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 color resolve: n=%u batches=%u shaded=%" PRIu64
                " rgb=%" PRIu64 " rt0-bytes=%" PRIu64 "\n",
                s->vm3_color_resolves, color_batches, color_pixels, color_rgb_pixels, written);
        }
    }
    shaded += color_pixels;
    rgb_samples += color_rgb_pixels;
    if (a8_pixels) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 tiled A8 resolve: shaded=%" PRIu64 " rgb-samples=%" PRIu64
            " rt0-bytes=%" PRIu64 "\n", a8_pixels, a8_rgb, written);
    }
    if (full_a8_pixels) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 full-height A8 writeback: width=2048 height=2048 shaded=%" PRIu64 "\n",
            full_a8_pixels);
    }
    if (dual_pixels) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 dual-UV resolve: shaded=%" PRIu64 " rgb-samples=%" PRIu64
            " rt0-bytes=%" PRIu64 "\n", dual_pixels, dual_rgb, written);
    }
    if (radial_pixels) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "Auto9 VM3 radial fan resolve: shaded=%" PRIu64 " rgb-samples=%" PRIu64
                      " rt0-bytes=%" PRIu64 "\n", radial_pixels, radial_rgb, written);
    }
    s->vm3_textured_resolves++;
    if (interleaved && ++s->vm3_interleaved_resolves <= 8) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 interleaved resolve: n=%u batches=%u single=%u rt0-bytes=%" PRIu64 " rgb=%" PRIu64 "\n",
            s->vm3_interleaved_resolves, interleaved, (unsigned)single, written, nonzero);
    }
    s->vm3_textured_bytes += written;
    s->vm3_textured_nonzero_pixels += nonzero;
    s->vm3_textured_shaded_pixels += shaded;
    s->vm3_texture_rgb_samples += rgb_samples;
    s->fragment_bytes_written += written;
    s->vm3_crc_invalidated_bytes += invalidated;
    if (afbc && selected && s->vm3_center_depth_observed < 8) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 G3D1 VM3 AFBC depth committed: groups=%u depth-bytes=%" PRIu64
            " color-bytes=%" PRIu64 " rgb=%" PRIu64 "\n", selected, zwritten, written, nonzero);
    }
    if (width == 1560 && height == 700 && written) {
        char footprint[65];
        auto9_vm3_center_footprint(out, raster_spans, footprint);
        auto9_vm3_center_record(s, footprint, pitch, written, nonzero, image);
    }
    return G3D00_JOB_DONE;
}

/* Capture the exact Z-write descriptor family. Check the same private
 * translation as an actual color job, but
 * record only a destination footprint; no pixel, address, or DMA write. */
static void auto9_vm3_center_depth_observe(Auto9MaliG3d1 *s, const uint8_t job[64],
                                         const uint8_t fb[128], const uint8_t ext[64],
                                         const uint8_t rt[64], unsigned attempt)
{
    unsigned pitch = ldl_le_p(rt + 40), used = 0;
    uint64_t color = ldq_le_p(rt + 32), context = ldq_le_p(fb + 56);
    char footprint[65] = "-";
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) || s->vm3_center_depth_observed >= 8 ||
        (uint32_t)ldl_le_p(fb + 32) != 0x02bb0617U ||
        (uint32_t)ldl_le_p(fb + 48) != 0xc0250000U ||
        (ldq_le_p(job + 40) & 63) != 3 || pitch != 6272) {
        return;
    }
    s->vm3_center_depth_observed++;
    const unsigned limit = 700 * 3;
    g_autofree Auto9Vm3ClearSpan *spans = g_try_new(Auto9Vm3ClearSpan, limit);
    if (spans && !(color & 63) &&
        auto9_vm3_clear_plan(s, color, 700, pitch, 1560 * 4, true, spans, limit, &used)) {
        auto9_vm3_center_footprint(spans, used, footprint);
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 center depth: sample=%u attempt=%u footprint=%s clear=0x%x "
        "zs=0x%x depth-base=%u depth-row=%u depth-surface=%u stencil-base=%u "
        "stencil-row=%u stencil-surface=%u modes=0x%x pre-frame=%u\n",
        s->vm3_center_depth_observed, attempt, footprint, ldl_le_p(fb + 52),
        ldl_le_p(ext + 12), (unsigned)!!ldq_le_p(ext + 16), ldl_le_p(ext + 24),
        ldl_le_p(ext + 28), (unsigned)!!ldq_le_p(ext + 32), ldl_le_p(ext + 40),
        ldl_le_p(ext + 44), ldl_le_p(fb) & 0x1ff, (unsigned)!!ldq_le_p(fb + 24));
    unsigned observed = 0;
    for (unsigned i = 0; i < s->tile_batch_count && observed < 4; i++) {
        const Auto9G3d00TileBatch *batch = &s->tile_batch[i];
        uint8_t state[64];
        if (batch->ttbr == s->as0_active_ttbr && batch->context == context &&
            batch->width == 1560 && batch->height == 700 &&
            auto9_g3d00_dma_read(s, batch->renderer, state, sizeof(state))) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 center depth draw: sample=%u draw=%u triangles=%u "
                "props=0x%x ms=0x%x stencil=0x%x front=0x%x back=0x%x\n",
                s->vm3_center_depth_observed, ++observed, batch->count, ldl_le_p(state + 16),
                ldl_le_p(state + 32), ldl_le_p(state + 36), ldl_le_p(state + 40),
                ldl_le_p(state + 44));
        }
    }
}

/* Collect only bounded scalar state from a rejected no-tiler job. Never
 * publish a guest address, source pixels or change the completion status. */
static void auto9_vm3_empty_fragment_fault(Auto9MaliG3d1 *s, const uint8_t job[64],
                                           const uint8_t fb[128], const uint8_t ext[64],
                                           const uint8_t rt[64], unsigned stage, uint32_t result)
{
    s->vm3_empty_fragment_failures++;
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) ||
        s->vm3_empty_fragment_profiles == G_N_ELEMENTS(s->vm3_empty_failure_profiles)) {
        return;
    }
    uint32_t shape[] = {
        stage, result, ldl_le_p(fb), ldl_le_p(fb + 32), ldl_le_p(fb + 40),
        ldl_le_p(fb + 44), ldl_le_p(fb + 48), ldl_le_p(fb + 52),
        ldl_le_p(rt), ldl_le_p(rt + 4), ldl_le_p(rt + 40), ldl_le_p(rt + 44),
        ldl_le_p(rt + 48), ldl_le_p(rt + 52), ldl_le_p(rt + 56), ldl_le_p(rt + 60),
        ldl_le_p(job + 32), ldl_le_p(job + 36), ldq_le_p(job + 40) & 63,
        ldl_le_p(ext + 8), ldl_le_p(ext + 12),
        (unsigned)!!ldq_le_p(fb + 16) | ((unsigned)!!ldq_le_p(fb + 24) << 1) |
        ((unsigned)!!ldq_le_p(ext) << 2) | ((unsigned)!!ldq_le_p(ext + 16) << 3) |
        ((unsigned)!!ldq_le_p(ext + 32) << 4) | ((unsigned)!!ldq_le_p(rt + 32) << 5),
    };
    QEMU_BUILD_BUG_ON(sizeof(shape) != sizeof(s->vm3_empty_failure_profiles[0]));
    for (unsigned i = 0; i < s->vm3_empty_fragment_profiles; i++) {
        if (!memcmp(shape, s->vm3_empty_failure_profiles[i], sizeof(shape))) {
            return;
        }
    }
    unsigned idx = s->vm3_empty_fragment_profiles++;
    memcpy(s->vm3_empty_failure_profiles[idx], shape, sizeof(shape));
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 no-tiler fault: profile=%u stage=%u status=0x%x "
        "fb0=0x%x wh=0x%x fb40=0x%x fb44=0x%x flags=0x%x depth-clear=0x%x "
        "rt0=0x%x rt4=0x%x pitch=%u rt44=0x%x clear=0x%x/0x%x/0x%x/0x%x "
        "bounds=0x%x/0x%x tag=%u crc-pitch=%u ext12=0x%x resources=0x%x\n",
        idx + 1, shape[0], shape[1], shape[2], shape[3], shape[4], shape[5],
        shape[6], shape[7], shape[8], shape[9], shape[10], shape[11], shape[12],
        shape[13], shape[14], shape[15], shape[16], shape[17], shape[18],
        shape[19], shape[20], shape[21]);
}

/* Captured VM3 fragment with all external stores disabled. The ZS/CRC
 * extension may retain attachment addresses, but none are read or written
 * when pre/post shaders, attachment loads/stores and CRC are disabled.
 * The caller still performs the normal descriptor status write and IRQ. */
static uint32_t auto9_vm3_empty_discard(Auto9MaliG3d1 *s, uint64_t head,
                                       uint64_t status_pa, const uint8_t job[64],
                                       const uint8_t fb[128], const uint8_t ext[64],
                                       const uint8_t rt[64], unsigned *stage)
{
    uint32_t wh = ldl_le_p(fb + 32);
    uint32_t pitch = wh == 0x030b045fU ? 576 : wh == 0x02bb0617U ? 832 : 0;
    unsigned width = (wh & 65535) + 1, height = (wh >> 16) + 1;
    uint64_t sample_locs = ldq_le_p(fb + 16), header_pa;

    *stage = FRAG_PROFILE;
    if (!pitch || ldl_le_p(fb + 48) != 0x00210000 || ldl_le_p(fb) ||
        !sample_locs || (sample_locs & 7) || ldq_le_p(fb + 24) ||
        ldl_le_p(fb + 36) || (uint32_t)ldl_le_p(fb + 40) != wh ||
        ldl_le_p(fb + 44) != 0x04001012 || ldl_le_p(fb + 52) != 0x3f800000 ||
        ldq_le_p(fb + 56) || (ldq_le_p(job + 40) & 63) != 3 ||
        ldl_le_p(job + 32) ||
        (uint32_t)ldl_le_p(job + 36) != (((height - 1) / 16) << 16 | ((width - 1) / 16)) ||
        ldq_le_p(job + 48) || ldq_le_p(job + 56) ||
        ldl_le_p(rt) != 0x04000000 || (uint32_t)ldl_le_p(rt + 4) != 0x80008100U ||
        ldq_le_p(rt + 32) || ldq_le_p(rt + 40) || ldq_le_p(rt + 48) || ldq_le_p(rt + 56) ||
        (uint32_t)ldl_le_p(ext + 8) != pitch || ldl_le_p(ext + 12) != 0x001105d5) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TRANSLATE;
    if (!auto9_g3d00_translate(s, head, sizeof(uint64_t) * 8, false, &header_pa) ||
        header_pa != status_pa) {
        return G3D00_JOB_WRITE_FAULT;
    }
    *stage = FRAG_DISCARD;
    s->vm3_empty_discard_validated++;
    return G3D00_JOB_DONE;
}

/* Full-frame transfer observed in stock 8704. The blend descriptor selects
 * Src + Src * 0, all channels, without a destination load. Source and
 * writeback are both linear color-space RGBA8 (AFBC only changes storage).
 * The input is already on the 8-bit output grid. */
enum {
    VM3_PF_W = 2048, VM3_PF_H = 1024, VM3_PF_PITCH = VM3_PF_W * 4,
    VM3_PF_GROUP_PIXELS = 128, VM3_PF_NX = VM3_PF_W / VM3_PF_GROUP_PIXELS,
    VM3_PF_NY = VM3_PF_H / VM3_PF_GROUP_PIXELS,
    VM3_PF_HEADER_BYTES = VM3_PF_NX * VM3_PF_NY * VM3_AFBC_HEADER_GROUP,
    VM3_PF_SLICE_BYTES = VM3_PF_HEADER_BYTES + VM3_PF_PITCH * VM3_PF_H,
    VM3_PF_SPANS = VM3_PF_H * 3,
    VM3_PF_WH = ((VM3_PF_H - 1) << 16) | (VM3_PF_W - 1),
};

typedef struct Auto9Vm3PreframeStats {
    unsigned solid, rgb, alpha, raw, coded, reserved, mixed_groups, headers;
} Auto9Vm3PreframeStats;

static bool auto9_vm3_preframe_headers(const uint8_t *headers,
                                      uint32_t colors[VM3_PF_NX * VM3_PF_NY],
                                      Auto9Vm3PreframeStats *stats)
{
    static const uint8_t raw_sizes[12] = {
        0x41, 0x10, 0x04, 0x41, 0x10, 0x04, 0x41, 0x10, 0x04, 0x41, 0x10, 0x04,
    };
    for (unsigned g = 0; g < VM3_PF_NX * VM3_PF_NY; g++) {
        const uint8_t *group = headers + g * VM3_AFBC_HEADER_GROUP;
        uint32_t first = ldl_le_p(group + 8);
        bool uniform = true;
        for (unsigned i = 0; i < VM3_AFBC_BLOCKS; i++) {
            const uint8_t *h = group + i * 16;
            uint32_t color = ldl_le_p(h + 8);
            if (!(h[4] & 63)) {
                if (ldq_le_p(h) || ldl_le_p(h + 12)) {
                    stats->reserved++;
                    uniform = false;
                } else {
                    stats->solid++;
                    stats->rgb += (color & 0x00ffffff) != 0;
                    stats->alpha += (color >> 24) != 0;
                    uniform &= color == first;
                }
            } else {
                stats->raw += !memcmp(h + 4, raw_sizes, sizeof(raw_sizes));
                stats->coded += memcmp(h + 4, raw_sizes, sizeof(raw_sizes)) != 0;
                uniform = false;
            }
        }
        /* The 8x8 groups are raster ordered. A uniform group does not
         * depend on the unverified permutation of its 64 headers. */
        colors[g] = first;
        stats->mixed_groups += !uniform;
    }
    stats->headers = VM3_PF_HEADER_BYTES / 16;
    return !stats->mixed_groups;
}

enum {
    PF_DRAW_FIELDS = 1, PF_LOCAL_STORAGE, PF_STATE_MAP, PF_TEXTURE_MAP, PF_SAMPLER_MAP,
    PF_RENDERER_FIELDS, PF_SHADER_MAP, PF_SHADER_CODE, PF_SAMPLER_FIELDS, PF_TEXTURE_FIELDS,
};

static uint32_t auto9_vm3_preframe_state_fault(unsigned attempt, unsigned group)
{
    if (attempt <= 64) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "Auto9 VM3 AFBC preframe state: attempt=%u group=%u\n", attempt, group);
    }
    return G3D00_JOB_CONFIG_FAULT;
}

static uint32_t auto9_vm3_preframe_renderer_diff(const uint8_t rsd[80])
{
    static const struct {
        unsigned word;
        uint32_t mask, value;
    } contract[] = {
        { 2, UINT32_MAX, 0x00010001 }, { 3, UINT32_MAX, 0 },
        { 4, UINT32_MAX, 0x00102200 }, { 8, UINT32_MAX, 0x0700ffff },
        /* Stencil disabled; front/back masks and valid operations are
         * inert. Other enables in word 9 must still be off. */
        { 9, 0xffff0000U, 0 }, { 10, 0xf0000000U, 0 }, { 11, 0xf0000000U, 0 },
        { 12, UINT32_MAX, 0x2880 },
        /* Word 14 belongs to the secondary vertex shader address.
         * This is a fragment DCD, whose word 15 is message preload. */
        { 15, UINT32_MAX, 0 }, { 16, UINT32_MAX, 0xa00 },
        { 17, UINT32_MAX, 0xf0122122U }, { 18, UINT32_MAX, 0x19 },
        { 19, UINT32_MAX, 0x010ed000 },
    };
    uint32_t mismatch = 0;
    for (unsigned i = 0; i < G_N_ELEMENTS(contract); i++) {
        unsigned word = contract[i].word;
        if ((ldl_le_p(rsd + word * 4) & contract[i].mask) != contract[i].value) {
            mismatch |= BIT(word);
        }
    }
    return mismatch;
}

static float auto9_vm3_pf_f32(const uint8_t *p)
{
    uint32_t bits = ldl_le_p(p);
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static unsigned auto9_vm3_pf_view_diff(const uint8_t view[32])
{
    /* Infinite reference bounds and finite clipping bounds are equivalent
     * here only if all target pixels remain inside the viewport and scissor. */
    return (unsigned)!(auto9_vm3_pf_f32(view) <= 0.0f) |
           ((unsigned)!(auto9_vm3_pf_f32(view + 4) <= 0.0f) << 1) |
           ((unsigned)!(auto9_vm3_pf_f32(view + 8) >= VM3_PF_W) << 2) |
           ((unsigned)!(auto9_vm3_pf_f32(view + 12) >= VM3_PF_H) << 3) |
           ((unsigned)(auto9_vm3_pf_f32(view + 16) != 0.0f) << 4) |
           ((unsigned)(ldl_le_p(view + 20) != 0x3f800000) << 5) |
           ((unsigned)!!ldl_le_p(view + 24) << 6) |
           ((unsigned)(ldl_le_p(view + 28) != VM3_PF_WH) << 7);
}

static unsigned auto9_vm3_pf_position_diff(const uint8_t position[64])
{
    unsigned corners[4], seen = 0, diff = 0;
    for (unsigned i = 0; i < 4; i++) {
        const uint8_t *vertex = position + i * 16;
        float x = auto9_vm3_pf_f32(vertex), y = auto9_vm3_pf_f32(vertex + 4);
        unsigned c = (x == VM3_PF_W) | ((y == VM3_PF_H) << 1);
        diff |= (unsigned)(x != 0.0f && x != VM3_PF_W) << (i * 4);
        diff |= (unsigned)(y != 0.0f && y != VM3_PF_H) << (i * 4 + 1);
        diff |= (unsigned)(auto9_vm3_pf_f32(vertex + 8) != 0.0f) << (i * 4 + 2);
        diff |= (unsigned)(ldl_le_p(vertex + 12) != 0x3f800000) << (i * 4 + 3);
        corners[i] = c;
        seen |= BIT(c);
    }
    /* In a strip the two middle vertices share the diagonal. This also
     * accepts reversed winding (the pinned DRAW has no face culling). */
    return diff | ((seen != 15 || (corners[1] ^ corners[2]) != 3) ? BIT(17) : 0);
}

enum {
    PF_POS_OK, PF_POS_ABSENT, PF_POS_ALIGN, PF_POS_MMU, PF_POS_ALIAS, PF_POS_READ,
};

static unsigned auto9_vm3_pf_read_position(Auto9MaliG3d1 *s, uint64_t pos,
                                          uint8_t data[64], const Auto9Vm3ClearSpan *out,
                                          unsigned count)
{
    if (!pos) {
        return PF_POS_ABSENT;
    }
    if (pos & 3) { /* Four float components at a scalar-aligned address. */
        return PF_POS_ALIGN;
    }
    if (pos > (UINT64_C(1) << 48) - 64) {
        return PF_POS_MMU;
    }
    for (unsigned offset = 0; offset < 64; ) {
        unsigned n = MIN(64 - offset, 4096 - ((pos + offset) & 4095));
        uint64_t pa;
        if (!auto9_g3d00_translate(s, pos + offset, n, false, &pa)) {
            return PF_POS_MMU;
        }
        if (!auto9_vm3_stock_disjoint(pa, n, out, count)) {
            return PF_POS_ALIAS;
        }
        if (address_space_read(&address_space_memory, pa, MEMTXATTRS_UNSPECIFIED,
                               data + offset, n) != MEMTX_OK) {
            return PF_POS_READ;
        }
        offset += n;
    }
    return PF_POS_OK;
}

static bool auto9_vm3_pf_header_map(Auto9MaliG3d1 *s, uint64_t base, bool write,
                                    uint64_t *pages, unsigned count)
{
    QEMU_BUILD_BUG_ON(VM3_PF_HEADER_BYTES != AUTO9_VM3_PF_HEADER_PAGES * 4096);
    if (!base || (base & 4095) || base > (UINT64_C(1) << 48) - count * 4096ULL) {
        return false;
    }
    for (unsigned i = 0; i < count; i++) {
        if (!auto9_g3d00_translate(s, base + i * 4096, 4096, write, &pages[i])) {
            return false;
        }
    }
    return true;
}

/* Record programmed AFBC color header mappings, not write completion.
 * Match the whole 128/256 KiB header footprint across address spaces. */
static void auto9_vm3_pf_producer(Auto9MaliG3d1 *s, const uint8_t fb[128],
                                  const uint8_t rt[64], bool tiler, unsigned target)
{
    uint32_t format = ldl_le_p(rt + 4);
    uint32_t shape = ldl_le_p(fb + 32);
    bool atlas = shape == 0x07ff07ff;
    unsigned block = (format >> 8) & 15;
    Auto9Vm3AfbcProducer record = {0};
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) || (!atlas && shape != VM3_PF_WH) ||
        !(format & 1) || (block != 13 && !(atlas && block == 12)) ||
        (ldl_le_p(rt) & BIT(24)) ||
        (ldl_le_p(rt + 28) & BIT(19))) {
        return;
    }
    record.sequence = ++s->vm3_pf_producers_seen;
    unsigned count = atlas ? AUTO9_VM3_ATLAS_HEADER_PAGES : AUTO9_VM3_PF_HEADER_PAGES;
    if (!auto9_vm3_pf_header_map(s, ldq_le_p(rt + 16), true, record.pages, count)) {
        return;
    }
    record.format = format;
    record.modes = ldl_le_p(fb) & 0x1ff;
    record.flags = ldl_le_p(fb + 48);
    record.shape = shape;
    record.target = target;
    record.tiler = tiler;
    s->vm3_pf_producers[s->vm3_pf_producers_mapped++ % AUTO9_VM3_PF_WRITERS] = record;
}

/* The tag and framebuffer duplicate the count. Do not read adjacent memory
 * as more render targets if these two independent fields disagree. */
static void auto9_vm3_atlas_targets(Auto9MaliG3d1 *s, uint64_t tag,
                                    const uint8_t fb[128])
{
    unsigned n = ((ldl_le_p(fb + 44) >> 19) & 15) + 1;
    if (!qemu_loglevel_mask(LOG_GUEST_ERROR) || ldl_le_p(fb + 32) != 0x07ff07ff ||
        n != (((tag >> 2) & 15) + 1) || !(tag & 1)) {
        return;
    }
    uint64_t rt_base = (tag & ~UINT64_C(63)) + 128 + ((tag & 2) ? 64 : 0);
    for (unsigned i = 1; i < n; i++) {
        uint8_t rt[64];
        if (!auto9_g3d00_dma_read(s, rt_base + i * 64, rt, sizeof(rt))) {
            break;
        }
        auto9_vm3_pf_producer(s, fb, rt, !!ldq_le_p(fb + 56), i);
    }
}

static const Auto9Vm3AfbcProducer *auto9_vm3_afbc_origin(Auto9MaliG3d1 *s, uint64_t source,
                                                         uint32_t shape, unsigned *matches)
{
    const Auto9Vm3AfbcProducer *latest = NULL;
    uint64_t pages[AUTO9_VM3_ATLAS_HEADER_PAGES];
    unsigned count = shape == 0x07ff07ff ? AUTO9_VM3_ATLAS_HEADER_PAGES : AUTO9_VM3_PF_HEADER_PAGES;
    *matches = 0;
    if (!s->vm3_pf_producers_mapped ||
        !auto9_vm3_pf_header_map(s, source, false, pages, count)) {
        return NULL;
    }
    for (unsigned i = 0; i < AUTO9_VM3_PF_WRITERS; i++) {
        const Auto9Vm3AfbcProducer *r = &s->vm3_pf_producers[i];
        if (r->sequence && r->shape == shape && !memcmp(r->pages, pages, count * sizeof(*pages))) {
            (*matches)++;
            if (!latest || latest->sequence < r->sequence) {
                latest = r;
            }
        }
    }
    return latest;
}

static const Auto9Vm3AfbcProducer *auto9_vm3_pf_origin(Auto9MaliG3d1 *s, uint64_t source,
                                                        unsigned *matches)
{
    return auto9_vm3_afbc_origin(s, source, VM3_PF_WH, matches);
}

static unsigned auto9_vm3_atlas_origin(Auto9MaliG3d1 *s, uint64_t source)
{
    unsigned matches;
    const Auto9Vm3AfbcProducer *r = auto9_vm3_afbc_origin(s, source, 0x07ff07ff, &matches);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas origin: seen=%u mapped=%u matches=%u sequence=%u format=0x%x flags=0x%x modes=0x%x tiler=%u rt=%u\n",
        s->vm3_pf_producers_seen, s->vm3_pf_producers_mapped, matches,
        r ? r->sequence : 0, r ? r->format : 0, r ? r->flags : 0,
        r ? r->modes : 0, r ? (unsigned)r->tiler : 0, r ? r->target : 0);
    return matches;
}

/* A Mali job's enable bits are indexed in framebuffer coordinates, not
 * relative to the job's scissor. Clip the map before using it for DMA. */
static bool auto9_vm3_atlas_tile_mask(const uint8_t job[64], unsigned rows,
                                      const uint8_t *map, uint8_t *enabled,
                                      unsigned *active)
{
    uint32_t lo = ldl_le_p(job + 32), hi = ldl_le_p(job + 36);
    uint32_t pitch = ldl_le_p(job + 56);
    unsigned min_x = lo & 4095, min_y = (lo >> 16) & 4095;
    unsigned max_x = hi & 4095, max_y = (hi >> 16) & 4095;
    if (!(hi & BIT(31)) || (lo & 0xf000f000) || (hi & 0x7000f000) ||
        min_x > max_x || max_x >= 128 || min_y > max_y || max_y >= rows ||
        pitch < 16 || pitch > 255 || !ldq_le_p(job + 48) || ldl_le_p(job + 60)) {
        return false;
    }
    *active = 0;
    memset(enabled, 0, rows * 128);
    for (unsigned y = min_y; y <= max_y; y++) {
        for (unsigned x = min_x; x <= max_x; x++) {
            bool set = map[y * pitch + x / 8] & BIT(x % 8);
            enabled[y * 128 + x] = set;
            *active += set;
        }
    }
    return true;
}

/* Summarize both defined bitmap bytes and stride padding without treating
 * padding or alternative indexing as live tiles. Call only on a valid map. */
static void auto9_vm3_atlas_map_probe(unsigned n, const uint8_t *map,
                                      unsigned rows, unsigned pitch, unsigned inside)
{
    unsigned total = 0, padding_nonzero = 0, row_mask = 0;
    unsigned x0 = 128, y0 = rows, x1 = 0, y1 = 0;
    for (unsigned y = 0; y < rows; y++) {
        for (unsigned i = 0; i < pitch; i++) {
            unsigned byte = map[y * pitch + i];
            if (i >= 16) {
                padding_nonzero += byte != 0;
                continue;
            }
            if (byte) {
                total += ctpop32(byte);
                x0 = MIN(x0, 8 * i + ctz32(byte));
                x1 = MAX(x1, 8 * i + 31 - clz32(byte));
                y0 = MIN(y0, y);
                y1 = MAX(y1, y);
                row_mask |= BIT(y / 8);
            }
        }
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, map, rows * pitch);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas map extent: n=%u total=%u inside=%u padding=%u "
        "box=%u,%u-%u,%u row-groups=0x%x sha256=%s\n",
        n, total, inside, padding_nonzero, x0, y0, x1, y1, row_mask, sha ? sha : "unavailable");
}

static void auto9_vm3_atlas_shader_probe(Auto9MaliG3d1 *s, unsigned n,
                                        unsigned phase, uint64_t program)
{
    uint8_t code[512];
    unsigned cap = MIN(sizeof(code), 4096 - (program & 4095));
    unsigned stop = 0, reason = 0;
    if (!program || (program & 15) || !auto9_g3d00_dma_read(s, program, code, cap)) {
        return;
    }
    unsigned extent = auto9_g3d1_bifrost_extent(code, cap, &stop, &reason);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas shader: n=%u phase=%u extent=%u stop=%u reason=%u\n",
        n, phase, extent, stop, reason);
    for (unsigned i = 0; i < extent; i += 128) {
        auto9_g3d00_sample_bytes("vm3-atlas-shader", n * 4 + phase, "framed-program",
                                 i, code + i, MIN(extent - i, 128));
    }
}

static void auto9_vm3_atlas_state_probe(Auto9MaliG3d1 *s, unsigned n, bool preload,
                                       uint64_t rsd, uint64_t table, uint64_t sampler)
{
    uint8_t state[80], desc[32], samp[32], surf[16];
    if (!rsd || (rsd & 63) || !auto9_g3d00_dma_read(s, rsd, state, sizeof(state))) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas state: n=%u preload=%u rsd8=0x%x rsd12=0x%x "
        "rsd16=0x%x rsd32=0x%x rsd36=0x%x rsd48=0x%x rsd60=0x%x "
        "blend=0x%x/0x%x/0x%x/0x%x\n",
        n, (unsigned)preload, ldl_le_p(state + 8), ldl_le_p(state + 12),
        ldl_le_p(state + 16), ldl_le_p(state + 32), ldl_le_p(state + 36),
        ldl_le_p(state + 48), ldl_le_p(state + 60), ldl_le_p(state + 64),
        ldl_le_p(state + 68), ldl_le_p(state + 72) & 7,
        (ldl_le_p(state + 72) & 3) ? ldl_le_p(state + 76) : 0);
    uint64_t code = ldq_le_p(state);
    auto9_vm3_atlas_shader_probe(s, n, preload ? 2 : 0, code);
    if (!(ldl_le_p(state + 72) & 3) && code) {
        uint64_t blend = (code & UINT64_C(0xffffffff00000000)) | (ldl_le_p(state + 76) & ~15U);
        auto9_vm3_atlas_shader_probe(s, n, preload ? 3 : 1, blend);
    }
    if (!table || !sampler || ((table | sampler) & 31) ||
        !auto9_g3d00_dma_read(s, table, desc, sizeof(desc)) ||
        !auto9_g3d00_dma_read(s, sampler, samp, sizeof(samp)) ||
        !ldq_le_p(desc + 16) || !auto9_g3d00_dma_read(s, ldq_le_p(desc + 16), surf, sizeof(surf))) {
        return;
    }
    unsigned layout = (ldl_le_p(desc + 8) >> 12) & 15;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas input: n=%u preload=%u tex=0x%x/0x%x/0x%x/0x%x/0x%x/0x%x "
        "sampler=0x%x/0x%x/0x%x tag=0x%x row=%u slice=%u\n",
        n, (unsigned)preload, ldl_le_p(desc), ldl_le_p(desc + 4), ldl_le_p(desc + 8),
        ldl_le_p(desc + 12), ldl_le_p(desc + 24), ldl_le_p(desc + 28), ldl_le_p(samp),
        ldl_le_p(samp + 4), ldl_le_p(samp + 8),
        layout == 12 ? (unsigned)(ldq_le_p(surf) & 4095) : 0,
        ldl_le_p(surf + 8), ldl_le_p(surf + 12));
}

static void auto9_vm3_atlas_job_probe(Auto9MaliG3d1 *s, unsigned n, const uint8_t job[64],
                                      const uint8_t fb[128], const uint8_t ext[64],
                                      const uint8_t rt[64])
{
    uint8_t draw[128];
    uint32_t wh = ldl_le_p(fb + 32);
    unsigned rows = ((wh >> 16) + 1) / 16, pitch = ldl_le_p(job + 56);
    uint64_t header = ldq_le_p(rt + 16), body = ldq_le_p(rt + 32);
    uint64_t context = ldq_le_p(fb + 56), dcd = ldq_le_p(fb + 24);
    unsigned active = 0;
    bool always = (uint32_t)ldl_le_p(rt + 4) == 0x86888d99U;
    if (always) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 atlas always contract: n=%u fb0=0x%x fb4=0x%x fb8-nonzero=%u depth=0x%x "
            "crc-base=%u crc-row=%u ext12=0x%x ext-tail=%u dcd=%u mask=%u pitch=%u\n",
            n, ldl_le_p(fb), ldl_le_p(fb + 4), (unsigned)!!ldq_le_p(fb + 8), ldl_le_p(fb + 52),
            (unsigned)!!ldq_le_p(ext), ldl_le_p(ext + 8), ldl_le_p(ext + 12),
            (unsigned)!!(ldq_le_p(ext + 16) | ldq_le_p(ext + 24) | ldq_le_p(ext + 32) |
                         ldq_le_p(ext + 40) | ldq_le_p(ext + 48) | ldq_le_p(ext + 56)),
            (unsigned)!!dcd, (unsigned)!!ldq_le_p(job + 48), pitch);
    }
    if (pitch >= 16 && pitch <= 255 && (rows == 64 || rows == 128)) {
        g_autofree uint8_t *map = g_try_malloc(rows * pitch);
        g_autofree uint8_t *mask = g_try_malloc(rows * 128);
        if (map && mask && auto9_g3d00_dma_read(s, ldq_le_p(job + 48), map, rows * pitch) &&
            auto9_vm3_atlas_tile_mask(job, rows, map, mask, &active)) {
            unsigned groups = 0, partial = 0;
            for (unsigned gy = 0; gy < rows; gy += 8) {
                for (unsigned gx = 0; gx < 128; gx += 8) {
                    unsigned enabled = 0;
                    for (unsigned y = 0; y < 8; y++) {
                        for (unsigned x = 0; x < 8; x++) {
                            enabled += mask[(gy + y) * 128 + gx + x];
                        }
                    }
                    groups += enabled != 0;
                    partial += enabled != 0 && enabled != 64;
                }
            }
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 atlas mask: n=%u pitch=%u rows=%u enabled=%u groups=%u partial=%u\n",
                n, pitch, rows, active, groups, partial);
            if (always) {
                auto9_vm3_atlas_map_probe(n, map, rows, pitch, active);
            }
        }
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas output: n=%u header=%u body=%u gap=%" PRIu64 " bytes=%u "
        "clear=0x%x/0x%x/0x%x/0x%x clip=0x%x/0x%x locations=%u\n",
        n, (unsigned)!!header, (unsigned)!!body, body >= header ? body - header : 0,
        ldl_le_p(rt + 40), ldl_le_p(rt + 48), ldl_le_p(rt + 52),
        ldl_le_p(rt + 56), ldl_le_p(rt + 60), ldl_le_p(fb + 36), ldl_le_p(fb + 40),
        (unsigned)!!ldq_le_p(fb + 16));
    for (unsigned i = 0; i < s->tile_batch_count; i++) {
        const Auto9G3d00TileBatch *b = &s->tile_batch[i];
        if (b->ttbr == s->as0_active_ttbr && b->context == context && b->atlas_strip) {
            auto9_vm3_atlas_state_probe(s, n, false, b->renderer, b->textures, b->samplers);
            if (always && b->atlas_constant && b->push_uniforms && !(b->push_uniforms & 7)) {
                uint8_t color[16];
                if (auto9_g3d00_dma_read(s, b->push_uniforms + 16, color, sizeof(color))) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 VM3 atlas always constant: n=%u color=0x%x/0x%x/0x%x/0x%x bins=%u\n",
                        n, ldl_le_p(color), ldl_le_p(color + 4), ldl_le_p(color + 8),
                        ldl_le_p(color + 12), b->count);
                }
            }
            break;
        }
    }
    if (dcd && !(dcd & 63) && dcd < (UINT64_C(1) << 48) - 256 &&
        auto9_g3d00_dma_read(s, dcd + 128, draw, sizeof(draw))) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 atlas pre-draw: n=%u word0=0x%x word4=0x%x position=%u viewport=%u\n",
            n, ldl_le_p(draw), ldl_le_p(draw + 4), (unsigned)!!ldq_le_p(draw + 16),
            (unsigned)!!ldq_le_p(draw + 96));
        auto9_vm3_atlas_state_probe(s, n, true, ldq_le_p(draw + 56), ldq_le_p(draw + 32),
                                    ldq_le_p(draw + 40));
        if (always) {
            uint8_t viewport[32], tex[32], surface[16];
            uint64_t vp = ldq_le_p(draw + 96), table = ldq_le_p(draw + 32);
            if (vp && !(vp & 31) && auto9_g3d00_dma_read(s, vp, viewport, sizeof(viewport))) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 atlas always preload viewport: n=%u min=0x%x max=0x%x\n",
                    n, ldl_le_p(viewport + 24), ldl_le_p(viewport + 28));
            }
            if (header && table && !(table & 31) &&
                auto9_g3d00_dma_read(s, table, tex, sizeof(tex)) &&
                ldl_le_p(tex + 4) == 0x03ff07ff && ldl_le_p(tex + 8) == 0xc688 &&
                auto9_g3d00_dma_read(s, ldq_le_p(tex + 16), surface, sizeof(surface))) {
                uint64_t source = ldq_le_p(surface) & ~UINT64_C(4095);
                uint64_t in[AUTO9_VM3_PF_HEADER_PAGES], out[AUTO9_VM3_PF_HEADER_PAGES];
                bool mapped = auto9_vm3_pf_header_map(s, source, false, in, G_N_ELEMENTS(in)) &&
                              auto9_vm3_pf_header_map(s, header, true, out, G_N_ELEMENTS(out));
                unsigned same = 0;
                for (unsigned i = 0; mapped && i < G_N_ELEMENTS(in); i++) {
                    same += in[i] == out[i];
                }
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 atlas always preload mapping: n=%u mapped=%u matching-pages=%u/%u\n",
                    n, (unsigned)mapped, same, (unsigned)G_N_ELEMENTS(in));
            }
        }
    }
}

/* Single-sample ATEST/opaque BLEND with the exact VAR_TEX F16 preload.
 * Coordinates are unnormalized; both filters are nearest, clamp to edge. */
static void auto9_vm3_atlas_draw(const Auto9G3d00Triangle *tri, const uint8_t *mask,
                                 const uint8_t *src, unsigned sw, unsigned sh,
                                 uint32_t *pixels, uint8_t *covered,
                                 unsigned *drawn, unsigned *rgb)
{
    unsigned v[3] = {0, 1, 2};
    int64_t area = auto9_g3d00_edge(tri->xy[0], tri->xy[1], tri->xy[2][0], tri->xy[2][1]);
    if (!area) {
        return;
    }
    if (area < 0) {
        v[1] = 2;
        v[2] = 1;
    }
    int64_t row[3], dx[3], dy[3];
    bool top[3];
    double w[3], uv[3][2];
    for (unsigned k = 0; k < 3; k++) {
        const int32_t *a = tri->xy[v[(k + 1) % 3]], *b = tri->xy[v[(k + 2) % 3]];
        row[k] = auto9_g3d00_edge(a, b, tri->pixel[0] * 256 + 128, tri->pixel[1] * 256 + 128);
        dx[k] = (int64_t)(a[1] - b[1]) * 256;
        dy[k] = (int64_t)(b[0] - a[0]) * 256;
        top[k] = auto9_g3d00_top_left(a, b);
        w[k] = tri->inv_w[v[k]];
        uv[k][0] = w[k] * tri->uv32[v[k]][0];
        uv[k][1] = w[k] * tri->uv32[v[k]][1];
    }
    for (unsigned y = tri->pixel[1]; y <= tri->pixel[3]; y++) {
        int64_t e[3] = { row[0], row[1], row[2] };
        for (unsigned x = tri->pixel[0]; x <= tri->pixel[2]; x++) {
            if (mask[(y / 16) * 128 + x / 16] &&
                (e[0] > 0 || (!e[0] && top[0])) &&
                (e[1] > 0 || (!e[1] && top[1])) &&
                (e[2] > 0 || (!e[2] && top[2]))) {
                double sum = e[0] * w[0] + e[1] * w[1] + e[2] * w[2];
                double u = (e[0] * uv[0][0] + e[1] * uv[1][0] + e[2] * uv[2][0]) / sum;
                double t = (e[0] * uv[0][1] + e[1] * uv[1][1] + e[2] * uv[2][1]) / sum;
                unsigned sx = fmin(sw - 1, fmax(0, u)), sy = fmin(sh - 1, fmax(0, t));
                unsigned i = y * 2048 + x;
                uint32_t color = ldl_le_p(src + 4 * ((size_t)sy * sw + sx));
                covered[i] = 1;
                pixels[i] = color;
                ++*drawn;
                *rgb += !!(color & 0xffffff);
            }
            for (unsigned k = 0; k < 3; k++) {
                e[k] += dx[k];
            }
        }
        for (unsigned k = 0; k < 3; k++) {
            row[k] += dy[k];
        }
    }
}

/* Only commit groups whose final 64 headers can all be the same solid
 * color. This proves correctness for every within-group permutation.
 * A masked or uncovered pixel must retain the actual preloaded color. */
static bool auto9_vm3_atlas_solid(const uint32_t *pixels, const uint8_t *covered,
                                  const uint8_t *mask, unsigned rows, uint8_t *headers,
                                  uint8_t groups[256], unsigned *changed, unsigned *unsupported)
{
    *changed = *unsupported = 0;
    for (unsigned gy = 0; gy < rows / 8; gy++) {
        for (unsigned gx = 0; gx < 16; gx++) {
            unsigned g = gy * 16 + gx;
            uint8_t *h = headers + g * 1024;
            uint32_t prior = ldl_le_p(h + 8), color = 0;
            bool seen = false, keep = false, mixed = false;
            groups[g] = 0;
            for (unsigned ty = gy * 8; ty < gy * 8 + 8; ty++) {
                for (unsigned tx = gx * 8; tx < gx * 8 + 8; tx++) {
                    if (!mask[ty * 128 + tx]) {
                        keep = true;
                        continue;
                    }
                    for (unsigned y = ty * 16; y < ty * 16 + 16; y++) {
                        for (unsigned x = tx * 16; x < tx * 16 + 16; x++) {
                            unsigned i = y * 2048 + x;
                            if (!covered[i]) {
                                keep = true;
                            } else {
                                mixed |= seen && color != pixels[i];
                                color = pixels[i];
                                seen = true;
                            }
                        }
                    }
                }
            }
            if (!seen) {
                continue;
            }
            bool uniform = true;
            for (unsigned j = 0; j < 64; j++) {
                const uint8_t *p = h + j * 16;
                uniform &= !ldq_le_p(p) && !ldl_le_p(p + 12) &&
                           (uint32_t)ldl_le_p(p + 8) == prior;
            }
            if (mixed || (keep && (!uniform || color != prior))) {
                ++*unsupported;
                continue;
            }
            if (!uniform || color != prior) {
                for (unsigned j = 0; j < 64; j++) {
                    memset(h + j * 16, 0, 16);
                    stl_le_p(h + j * 16 + 8, color);
                }
                groups[g] = 1;
                ++*changed;
            }
        }
    }
    return *unsupported == 0;
}

/* Resolve untouched pixels from the real pre-frame image. On the first draw
 * only permutation-independent, uniform solid groups are supported. */
static bool auto9_vm3_atlas_merge(uint32_t *pixels, const uint8_t *covered,
                                  const uint8_t *headers, const uint32_t *prior,
                                  unsigned height)
{
    for (unsigned gy = 0; gy < height / 128; gy++) {
        for (unsigned gx = 0; gx < 16; gx++) {
            const uint8_t *h = headers + (gy * 16 + gx) * 1024;
            uint32_t rgba = ldl_le_p(h + 8);
            if (!prior) {
                for (unsigned i = 0; i < 64; i++, h += 16) {
                    if (ldq_le_p(h) || ldl_le_p(h + 12) ||
                        (uint32_t)ldl_le_p(h + 8) != rgba) {
                        return false;
                    }
                }
            }
            for (unsigned y = gy * 128; y < gy * 128 + 128; y++) {
                for (unsigned x = gx * 128; x < gx * 128 + 128; x++) {
                    unsigned i = y * 2048 + x;
                    if (!covered[i]) {
                        pixels[i] = prior ? prior[i] : rgba;
                    }
                }
            }
        }
    }
    return true;
}

static bool auto9_vm3_atlas_store(Auto9MaliG3d1 *s, uint64_t header, uint64_t preload,
                                  unsigned height, const uint8_t *headers,
                                  const uint8_t *covered, uint32_t **pixels,
                                  unsigned *published_slot)
{
    unsigned bucket = height == 2048, slot = bucket;
    unsigned bytes = height * 2048 * 4, hdr = bytes / 64, miss;
    Auto9Vm3AtlasImage *img = g_try_new(Auto9Vm3AtlasImage, 1);
    if (!img) {
        return false;
    }
    *img = (Auto9Vm3AtlasImage){ .ttbr = s->as0_active_ttbr, .va = header, .preload_va = preload,
        .pages = (hdr + bytes) / 4096 };
    img->backing_bytes = g_try_malloc(hdr + bytes);
    g_autofree Auto9Vm3ClearSpan *spans = g_try_new(Auto9Vm3ClearSpan, img->pages);
    if (!img->backing_bytes || !spans ||
        !auto9_vm3_atlas_backing(s, img, header, true, NULL, 0, &miss) ||
        memcmp(headers, img->backing_bytes, hdr) ||
        !auto9_vm3_atlas_backing(s, img, preload, false, NULL, 0, &miss)) {
        auto9_vm3_atlas_drop(&img);
        return false;
    }
    for (unsigned i = 0; i < img->pages; i++) {
        spans[i] = (Auto9Vm3ClearSpan){ img->backing[i], 4096 };
    }
    qsort(spans, img->pages, sizeof(*spans), auto9_vm3_clear_span_cmp);
    for (unsigned i = 1; i < img->pages; i++) {
        if (spans[i - 1].pa == spans[i].pa) {
            auto9_vm3_atlas_drop(&img);
            return false;
        }
    }
    const uint32_t *prior = auto9_vm3_atlas_lookup(s, height, preload, NULL, 0);
    if (!auto9_vm3_atlas_merge(*pixels, covered, headers, prior, height)) {
        auto9_vm3_atlas_drop(&img);
        return false;
    }
    /* Keep disjoint allocations even at the same dimensions. Drop every
     * physical alias, including partial overlaps and cross-size aliases. */
    for (unsigned i = 0; i < G_N_ELEMENTS(s->vm3_atlas); i++) {
        Auto9Vm3AtlasImage *other = s->vm3_atlas[i];
        for (unsigned j = 0; other && j < other->pages; j++) {
            if (!auto9_vm3_stock_disjoint(other->backing[j], 4096, spans, img->pages)) {
                auto9_vm3_atlas_drop(&s->vm3_atlas[i]);
                break;
            }
        }
    }
    /* Empty way first; with two disjoint occupants, evict the oldest writer. */
    for (unsigned i = bucket; i < G_N_ELEMENTS(s->vm3_atlas); i += 2) {
        if (!s->vm3_atlas[i]) {
            slot = i;
            break;
        }
        if (s->vm3_atlas[i]->generation < s->vm3_atlas[slot]->generation) {
            slot = i;
        }
    }
    auto9_vm3_atlas_drop(&s->vm3_atlas[slot]);
    img->generation = ++s->vm3_atlas_generation;
    img->rgba = *pixels;
    *pixels = NULL;
    s->vm3_atlas[slot] = img;
    if (published_slot) {
        *published_slot = slot;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 private atlas published: height=%u slot=%u generation=%" PRIu64 " live=%u\n",
        height, slot, img->generation, auto9_vm3_atlas_count(s, height));
    return true;
}

static uint32_t auto9_vm3_atlas_resolve(Auto9MaliG3d1 *s, uint64_t head, uint64_t status_pa,
                                       const uint8_t job[64], const uint8_t fb[128],
                                       const uint8_t rt[64], unsigned *stage)
{
    static const char *const digests[] = {
        "57fe4d9a1d6d616e3912a188e91810371fd14c4959f8680259d36056e60fdc29",
        "0aaf6bf77095d8b855189a2e8ef1a591ad912df3b7245ac74718fb4480e6e77d",
    };
    uint32_t wh = ldl_le_p(fb + 32);
    unsigned height = (wh >> 16) + 1, rows = height / 16, pitch = ldl_le_p(job + 56);
    unsigned header_bytes = rows * 128 * 16;
    uint64_t header = ldq_le_p(rt + 16), body = ldq_le_p(rt + 32), dcd = ldq_le_p(fb + 24);
    uint64_t context = ldq_le_p(fb + 56), fbd = ldq_le_p(job + 40) & ~UINT64_C(63);
    uint64_t status, preload = 0;
    const Auto9G3d00TileBatch *batch = NULL;
    g_autofree Auto9Vm3ClearSpan *sorted =
        g_try_new(Auto9Vm3ClearSpan, AUTO9_VM3_ATLAS_MAX_PAGES);
    uint64_t before[64], after[64];
    uint8_t draw[128], state[80], desc[32], samp[32], surface[16], code[64], groups[256];
    unsigned enabled, drawn = 0, rgb = 0, changed, unsupported, used = 0;

    *stage = FRAG_PROFILE;
    if ((wh != 0x07ff07ff && wh != VM3_PF_WH) || !context ||
        (ldq_le_p(job + 40) & 63) != 1 || pitch != 64 || !ldq_le_p(job + 48) ||
        ldq_le_p(fb) != 0x10 || ldq_le_p(fb + 8) || !ldq_le_p(fb + 16) ||
        (ldq_le_p(fb + 16) & 7) || ldl_le_p(fb + 36) || (uint32_t)ldl_le_p(fb + 40) != wh ||
        ldl_le_p(fb + 44) != 0x01001000 || ldl_le_p(fb + 48) != 0x10000 ||
        ldl_le_p(fb + 52) || !dcd || (dcd & 63) || dcd >= (UINT64_C(1) << 48) - 256 ||
        ldl_le_p(rt) != 0x04000000 || ldl_le_p(rt + 4) != 0x06888d99 ||
        ldl_le_p(rt + 24) != 0x80 || ldl_le_p(rt + 28) != 0x120000 ||
        ldq_le_p(rt + 40) || ldq_le_p(rt + 48) || ldq_le_p(rt + 56) ||
        !header || (header & 4095) || body < header || body - header != header_bytes) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_NO_BATCH;
    for (unsigned i = 0; i < s->tile_batch_count; i++) {
        const Auto9G3d00TileBatch *b = &s->tile_batch[i];
        if (b->context != context || b->ttbr != s->as0_active_ttbr) {
            continue;
        }
        if (batch || !b->atlas_strip || b->atlas_constant || b->width != 2048 || b->height != height ||
            b->count != 2 || b->push_uniforms) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        batch = b;
    }
    if (!batch) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_STORAGE;
    g_autofree uint8_t *headers = g_try_malloc(header_bytes);
    g_autofree uint8_t *mask = g_try_malloc(rows * 128);
    g_autofree uint8_t *tilemap = g_try_malloc(rows * pitch);
    g_autofree uint8_t *covered = g_try_malloc(2048 * height);
    g_autofree uint32_t *pixels = g_try_new(uint32_t, 2048 * height);
    g_autofree uint8_t *source = NULL;
    g_autofree uint8_t *solid_headers = g_try_malloc(header_bytes);
    if (!headers || !solid_headers || !mask || !tilemap || !covered || !pixels || !sorted) {
        return G3D00_JOB_WRITE_FAULT;
    }
    memset(covered, 0, 2048 * height);
    *stage = FRAG_AFBC_ALIAS;
    if (!auto9_g3d00_translate(s, head, 64, false, &status) || status != status_pa ||
        !auto9_vm3_clear_plan(s, header, 1, 0, header_bytes + 2048 * height * 4,
                             true, sorted, AUTO9_VM3_ATLAS_MAX_PAGES, &used)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    qsort(sorted, used, sizeof(*sorted), auto9_vm3_clear_span_cmp);
    for (unsigned i = 1; i < used; i++) {
        if (sorted[i - 1].pa + sorted[i - 1].length > sorted[i].pa) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    if (!auto9_vm3_stock_disjoint(status_pa, 64, sorted, used) ||
        !auto9_vm3_stock_read(s, fbd, NULL, 192, sorted, used) ||
        !auto9_vm3_stock_read(s, context, NULL, 192, sorted, used) ||
        !auto9_vm3_stock_read(s, ldq_le_p(fb + 16), NULL, 16, sorted, used) ||
        !auto9_vm3_stock_read(s, ldq_le_p(job + 48), tilemap, rows * pitch, sorted, used) ||
        !auto9_vm3_stock_read(s, dcd + 128, draw, sizeof(draw), sorted, used)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    *stage = FRAG_AFBC_LAYOUT;
    if (!auto9_vm3_atlas_tile_mask(job, rows, tilemap, mask, &enabled) || !enabled ||
        ldq_le_p(draw) != 0x803 || ldq_le_p(draw + 8) || ldq_le_p(draw + 48)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* Validate both original programs before touching any render target. */
    unsigned sw = height == 2048 ? 1920 : 1555, sh = height == 2048 ? 1080 : 875;
    unsigned source_stride = height == 2048 ? 7680 : 6272;
    bool small_source = false;
    for (unsigned i = 0; i < 2; i++) {
        uint64_t rsd = i ? ldq_le_p(draw + 56) : batch->renderer;
        uint64_t table = i ? ldq_le_p(draw + 32) : batch->textures;
        uint64_t sampler = i ? ldq_le_p(draw + 40) : batch->samplers;
        *stage = FRAG_TEXTURE_STATE;
        if ((rsd & 63) || ((table | sampler) & 31) ||
            !auto9_vm3_stock_read(s, rsd, state, sizeof(state), sorted, used) ||
            !auto9_vm3_stock_read(s, table, desc, sizeof(desc), sorted, used) ||
            !auto9_vm3_stock_read(s, sampler, samp, sizeof(samp), sorted, used) ||
            !auto9_vm3_stock_read(s, ldq_le_p(desc + 16), surface, sizeof(surface), sorted, used)) {
            return G3D00_JOB_READ_FAULT;
        }
        /* The same opaque shader also copies the original 60x60 source
         * into a small masked portion of the 2048x1024 atlas. */
        if (!i && height == 1024 && ldl_le_p(desc + 4) == 0x003b003b) {
            sw = sh = 60;
            source_stride = 256;
            small_source = true;
        }
        uint32_t input_wh = ((sh - 1) << 16) | (sw - 1);
        if (ldl_le_p(state + 8) != 0x10001 || ldl_le_p(state + 12) != (i ? 0 : 0x10000) ||
            ldl_le_p(state + 16) != (i ? 0x00102200 : 0x41582a00) ||
            ldl_le_p(state + 32) != (i ? 0x0700ffff : 0x0740ffff) ||
            ldl_le_p(state + 36) || ldl_le_p(state + 48) != (i ? 0x2880 : 0x2080) ||
            ldl_le_p(state + 60) != (i ? 0 : 0x4002) ||
            ldl_le_p(state + 64) != 0xa00 || (uint32_t)ldl_le_p(state + 68) != 0xf0122122U ||
            (ldl_le_p(state + 72) & 7) != 1 || ldl_le_p(state + 76) != 0x10ed000 ||
            ldl_le_p(desc) != 0x2ec00042 || (uint32_t)ldl_le_p(desc + 4) != (i ? wh : input_wh) ||
            ldl_le_p(desc + 8) != (i ? 0xc688 : 0x2688) || ldl_le_p(desc + 12) ||
            ldl_le_p(desc + 24) || (uint32_t)ldl_le_p(desc + 28) != (i ? wh : input_wh) ||
            ldl_le_p(samp) != (i ? 0x50099901 : 0x58099901) ||
            ldq_le_p(samp + 4) || ldl_le_p(samp + 12) || ldq_le_p(samp + 16) || ldq_le_p(samp + 24)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        *stage = FRAG_SHADER;
        unsigned code_bytes = i ? 64 : 32;
        uint64_t program = ldq_le_p(state);
        if ((program & 15) || !auto9_vm3_stock_read(s, program, code, code_bytes, sorted, used)) {
            return G3D00_JOB_READ_FAULT;
        }
        g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, code_bytes);
        if (!sha || strcmp(sha, digests[i])) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        uint64_t base = ldq_le_p(surface);
        *stage = FRAG_TEXTURE_MEMORY;
        if (!i) {
            unsigned stride = ldl_le_p(surface + 8);
            if (!base || (base & 3) || stride != source_stride || ldl_le_p(surface + 12) ||
                base >= (UINT64_C(1) << 48) - (uint64_t)stride * sh) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            source = g_try_malloc((size_t)sw * sh * 4);
            if (!source) {
                return G3D00_JOB_WRITE_FAULT;
            }
            for (unsigned y = 0; y < sh; y++) {
                if (!auto9_vm3_stock_read(s, base + (uint64_t)y * stride,
                                         source + (size_t)y * sw * 4, sw * 4, sorted, used)) {
                    return G3D00_JOB_READ_FAULT;
                }
            }
        } else {
            unsigned pages = header_bytes / 4096;
            preload = base & ~UINT64_C(4095);
            if ((base & 4095) != 0x39 || ldl_le_p(surface + 8) != 16384 ||
                (uint32_t)ldl_le_p(surface + 12) != header_bytes + 2048 * height * 4 ||
                !auto9_vm3_pf_header_map(s, base & ~UINT64_C(4095), false, before, pages) ||
                !auto9_vm3_pf_header_map(s, header, true, after, pages) ||
                memcmp(before, after, pages * sizeof(*before))) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            for (unsigned j = 0; j < pages; j++) {
                if (address_space_read(&address_space_memory, before[j], MEMTXATTRS_UNSPECIFIED,
                                       headers + 4096 * j, 4096) != MEMTX_OK) {
                    return G3D00_JOB_READ_FAULT;
                }
            }
        }
    }
    for (unsigned i = 0; i < batch->count; i++) {
        auto9_vm3_atlas_draw(&batch->triangle[i], mask, source, sw, sh, pixels, covered, &drawn, &rgb);
    }
    *stage = FRAG_AFBC_COVERAGE;
    memcpy(solid_headers, headers, header_bytes);
    bool solid = auto9_vm3_atlas_solid(pixels, covered, mask, rows, solid_headers, groups,
                                       &changed, &unsupported);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 atlas raster: wh=0x%x tiles=%u drawn=%u rgb=%u solid-groups=%u unsupported=%u\n",
        wh, enabled, drawn, rgb, changed, unsupported);
    if (!drawn) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!solid || auto9_vm3_atlas_count(s, height)) {
        if (!auto9_vm3_atlas_store(s, header, preload, height, headers, covered, &pixels, NULL)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 private atlas rejected: height=%u\n", height);
            return G3D00_JOB_CONFIG_FAULT;
        }
        s->fragment_pixels_shaded += drawn;
        *stage = FRAG_WRITE;
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 private atlas committed: height=%u drawn=%u rgb=%u\n", height, drawn, rgb);
        if (small_source) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 atlas small source committed: tiles=%u drawn=%u rgb=%u storage=private\n",
                enabled, drawn, rgb);
        }
        return G3D00_JOB_DONE;
    }
    *stage = FRAG_WRITE;
    for (unsigned g = 0; g < header_bytes / 1024; g++) {
        if (groups[g] && address_space_write(&address_space_memory,
                after[g / 4] + (g % 4) * 1024, MEMTXATTRS_UNSPECIFIED,
                solid_headers + g * 1024, 1024) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    s->fragment_bytes_written += changed * 1024;
    s->fragment_pixels_shaded += drawn;
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 atlas committed: wh=0x%x bytes=%u rgb=%u\n",
                  wh, changed * 1024, rgb);
    if (small_source) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 atlas small source committed: tiles=%u drawn=%u rgb=%u storage=solid\n",
            enabled, drawn, rgb);
    }
    return G3D00_JOB_DONE;
}

/* Render the captured in-place, single-sample atlas family. The CPU
 * shadow pins both AFBC mappings and keeps untouched pixels from the
 * original preload. CRC entries are invalidated, not used for TE. */
static uint32_t auto9_vm3_atlas_always_resolve(Auto9MaliG3d1 *s, uint64_t head, uint64_t status_pa,
                                              const uint8_t job[64], const uint8_t fb[128],
                                              const uint8_t ext[64], const uint8_t rt[64], unsigned *stage)
{
    enum { W = 2048, H = 1024, TX = W / 16, TY = H / 16, PITCH = 64, PAGES = 32 };
    enum { HDR = PAGES * 4096, BYTES = HDR + W * H * 4, SPANS = BYTES / 4096 + TY * 2 };
    uint64_t target = ldq_le_p(rt + 16), body = ldq_le_p(rt + 32), dcd = ldq_le_p(fb + 24);
    uint64_t context = ldq_le_p(fb + 56), crc = ldq_le_p(ext), pa;
    uint64_t fbd = ldq_le_p(job + 40) & ~UINT64_C(63);
    uint8_t draw[128], state[80], code[64], desc[32], samp[32], surf[16], view[32];
    unsigned n = 0, nc = 0, enabled, pixels_drawn = 0, indexed_drawn = 0;
    uint64_t rgb = 0, crc_bytes = 0, work = 0;
    uint8_t dirty[TX * TY] = {0};
    Auto9Vm3ClearSpan crc_spans[TY * 2];

    *stage = FRAG_PROFILE;
    if ((ldq_le_p(job + 40) & 63) != 3 || !context || !crc || (crc & 7) ||
        ldl_le_p(ext + 8) != TX * 8 || ldl_le_p(ext + 12) != 0x00110420 ||
        ldq_le_p(fb) != 8 || ldq_le_p(fb + 8) || !ldq_le_p(fb + 16) ||
        ldl_le_p(fb + 32) != VM3_PF_WH || ldl_le_p(fb + 36) ||
        ldl_le_p(fb + 40) != VM3_PF_WH || ldl_le_p(fb + 44) != 0x01001000 ||
        (uint32_t)ldl_le_p(fb + 48) != 0x80210000U || ldl_le_p(fb + 52) ||
        !dcd || (dcd & 63) || dcd >= (UINT64_C(1) << 48) - 256 ||
        ldl_le_p(rt) != 0x04000000 || (uint32_t)ldl_le_p(rt + 4) != 0x86888d99U ||
        ldl_le_p(rt + 24) != 0x80 || ldl_le_p(rt + 28) != 0x120000 ||
        ldq_le_p(rt + 40) || ldq_le_p(rt + 48) || ldq_le_p(rt + 56) ||
        !target || (target & 4095) || body < target || body - target != HDR ||
        ldl_le_p(job + 56) != PITCH) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_AFBC_LAYOUT;
    g_autofree uint8_t *mask = g_try_malloc(TX * TY);
    g_autofree uint8_t *map = g_try_malloc(PITCH * TY);
    if (!mask || !map || !auto9_vm3_stock_read(s, ldq_le_p(job + 48), map, PITCH * TY, NULL, 0)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (!auto9_vm3_atlas_tile_mask(job, TY, map, mask, &enabled)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_AFBC_ALIAS;
    g_autofree Auto9Vm3ClearSpan *out = g_try_new(Auto9Vm3ClearSpan, SPANS);
    if (!out || !auto9_g3d00_translate(s, head, 64, false, &pa) || pa != status_pa ||
        !auto9_vm3_clear_plan(s, target, 1, 0, BYTES, true, out, SPANS, &n) ||
        !auto9_vm3_clear_plan(s, crc, TY, TX * 8, TX * 8, true, crc_spans, G_N_ELEMENTS(crc_spans), &nc)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    memcpy(out + n, crc_spans, nc * sizeof(*out));
    n += nc;
    qsort(out, n, sizeof(*out), auto9_vm3_clear_span_cmp);
    for (unsigned i = 1; i < n; i++) {
        if (out[i - 1].pa + out[i - 1].length > out[i].pa) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    if (!auto9_vm3_stock_disjoint(status_pa, 64, out, n) ||
        !auto9_vm3_stock_read(s, fbd, NULL, 256, out, n) ||
        !auto9_vm3_stock_read(s, context, NULL, 192, out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(fb + 16), NULL, 16, out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(job + 48), map, PITCH * TY, out, n) ||
        !auto9_vm3_stock_read(s, dcd + 128, draw, sizeof(draw), out, n)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    if (!auto9_vm3_atlas_tile_mask(job, TY, map, mask, &enabled)) {
        *stage = FRAG_AFBC_LAYOUT;
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!enabled) {
        bool has_map_tiles = false;
        for (unsigned y = 0; y < TY; y++) {
            for (unsigned x = 0; x < TX / 8; x++) {
                has_map_tiles |= map[y * PITCH + x] != 0;
            }
        }
        if (!has_map_tiles) {
            *stage = FRAG_AFBC_LAYOUT;
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    *stage = FRAG_SHADER;
    if (ldq_le_p(draw) != 0x803 || ldq_le_p(draw + 8) || ldq_le_p(draw + 48) ||
        !auto9_vm3_stock_read(s, ldq_le_p(draw + 56), state, sizeof(state), out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(state), code, sizeof(code), out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(draw + 32), desc, sizeof(desc), out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(draw + 40), samp, sizeof(samp), out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(desc + 16), surf, sizeof(surf), out, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(draw + 96), view, sizeof(view), out, n)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, sizeof(code));
    if (!sha || strcmp(sha, "0aaf6bf77095d8b855189a2e8ef1a591ad912df3b7245ac74718fb4480e6e77d") ||
        auto9_vm3_preframe_renderer_diff(state) || auto9_vm3_pf_view_diff(view) ||
        ldl_le_p(desc) != 0x2ec00042 || ldl_le_p(desc + 4) != VM3_PF_WH ||
        ldl_le_p(desc + 8) != 0xc688 || ldl_le_p(desc + 12) || ldl_le_p(desc + 24) ||
        ldl_le_p(desc + 28) != VM3_PF_WH || ldl_le_p(samp) != 0x50099901 ||
        ldl_le_p(samp + 4) || ldq_le_p(samp + 8) || ldq_le_p(samp + 16) || ldq_le_p(samp + 24) ||
        (ldq_le_p(surf) & 4095) != 0x39 || ldl_le_p(surf + 8) != 16384 || ldl_le_p(surf + 12) != BYTES) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_AFBC_HEADER;
    uint64_t preload = ldq_le_p(surf) & ~UINT64_C(4095);
    uint64_t src_pages[PAGES], dst_pages[PAGES];
    if (!auto9_vm3_pf_header_map(s, preload, false, src_pages, PAGES) ||
        !auto9_vm3_pf_header_map(s, target, true, dst_pages, PAGES) ||
        memcmp(src_pages, dst_pages, sizeof(src_pages))) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!enabled) {
        /* The valid bitmap contains tiles, but none inside this job's bounds.
         * The pinned pre-frame shader only preloads the same AFBC surface;
         * no tile reaches rasterization or writeback, including CRC. */
        *stage = FRAG_WRITE;
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 indexed atlas Always: disjoint tile map, no color or CRC writes\n");
        return G3D00_JOB_DONE;
    }
    g_autofree uint8_t *headers = g_try_malloc(HDR);
    g_autofree uint8_t *modified = g_try_malloc(W * H);
    g_autofree uint32_t *pixels = g_try_new(uint32_t, W * H);
    if (!headers || !modified || !pixels) {
        return G3D00_JOB_WRITE_FAULT;
    }
    memset(modified, 0, W * H);
    for (unsigned i = 0; i < PAGES; i++) {
        if (address_space_read(&address_space_memory, src_pages[i], MEMTXATTRS_UNSPECIFIED,
                               headers + i * 4096, 4096) != MEMTX_OK) {
            return G3D00_JOB_READ_FAULT;
        }
    }
    const uint32_t *prior = auto9_vm3_atlas_lookup(s, H, preload, NULL, 0);
    if (!auto9_vm3_atlas_merge(pixels, modified, headers, prior, H)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* Draw in original submission order. Only covered pixels in enabled
     * tiles are changed, even if a batch covers other parts of the atlas. */
    for (unsigned i = 0; i < s->tile_batch_count; i++) {
        const Auto9G3d00TileBatch *b = &s->tile_batch[i];
        if (b->context != context || b->ttbr != s->as0_active_ttbr) {
            continue;
        }
        *stage = FRAG_NO_BATCH;
        if (b->width != W || b->height != H || (!b->atlas_constant && !b->indexed)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!b->count) {
            continue;
        }
        Auto9Vm3StockTexture tex = {0};
        g_autofree uint8_t *data = NULL;
        if (b->atlas_constant) {
            uint8_t constant[16];
            *stage = FRAG_SHADER;
            if (!auto9_vm3_stock_read(s, b->renderer, state, sizeof(state), out, n) ||
                !auto9_vm3_stock_read(s, ldq_le_p(state), code, sizeof(code), out, n) ||
                !auto9_vm3_stock_read(s, b->push_uniforms + 16, constant, sizeof(constant), out, n)) {
                return G3D00_JOB_READ_FAULT;
            }
            g_autofree char *hash = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, sizeof(code));
            if (!hash || strcmp(hash, "588dd8ba981ff0cd782ca2ff95937a23e0c0dbcba4cf69bf6a4360211e3f6156") ||
                ldq_le_p(constant) || ldq_le_p(constant + 8) || ldq_le_p(state + 8) ||
                ldl_le_p(state + 16) != 0x41582201 || ldl_le_p(state + 32) != 0x0740ffff ||
                ldl_le_p(state + 36) || ldl_le_p(state + 48) != 0x22080 || ldl_le_p(state + 60) ||
                ldl_le_p(state + 64) != 0x200 || (uint32_t)ldl_le_p(state + 68) != 0xf0122122U ||
                (ldl_le_p(state + 72) & 3) != 1 || ldl_le_p(state + 76) != 0x10ed000) {
                return G3D00_JOB_CONFIG_FAULT;
            }
        } else {
            uint8_t unused[16 * 16 * 4];
            const uint32_t *atlas = NULL;
            uint32_t rc = auto9_vm3_interleaved_texture(s, b, out, n, stage, unused, &atlas, &tex);
            data = tex.data;
            if (rc != G3D00_JOB_DONE || !data || (!tex.repeat_bilinear && !tex.destination_mask)) {
                return rc == G3D00_JOB_DONE ? G3D00_JOB_CONFIG_FAULT : rc;
            }
        }
        for (unsigned j = 0; j < b->count; j++) {
            const Auto9G3d00Triangle *t = &b->triangle[j];
            work += (uint64_t)(t->pixel[2] - t->pixel[0] + 1) * (t->pixel[3] - t->pixel[1] + 1);
            if (work > W * H * 8) {
                *stage = FRAG_BUDGET;
                return G3D00_JOB_CONFIG_FAULT;
            }
            unsigned hits = auto9_vm3_color_raster_target(t, W, TX, NULL, dirty, &rgb, NULL, NULL,
                                                        data ? &tex : NULL, NULL, mask, pixels, modified, NULL);
            pixels_drawn += hits;
            indexed_drawn += b->indexed ? hits : 0;
        }
    }
    *stage = FRAG_AFBC_COVERAGE;
    unsigned published_slot;
    if (!pixels_drawn ||
        !auto9_vm3_atlas_store(s, target, preload, H, headers, modified, &pixels, &published_slot)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_WRITE;
    if (!auto9_vm3_stock_commit(crc_spans, nc, NULL, TX * 8, mask, TX, 8, 1, &crc_bytes)) {
        /* Do not advertise a usable shadow after a failed guest store. */
        auto9_vm3_atlas_drop(&s->vm3_atlas[published_slot]);
        return G3D00_JOB_WRITE_FAULT;
    }
    s->fragment_pixels_shaded += pixels_drawn;
    s->vm3_crc_invalidated_bytes += crc_bytes;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 indexed atlas Always: tiles=%u shaded=%u indexed=%u rgb=%" PRIu64 " crc=%" PRIu64 "\n",
        enabled, pixels_drawn, indexed_drawn, rgb, crc_bytes);
    return G3D00_JOB_DONE;
}

static uint32_t auto9_vm3_preframe_resolve(Auto9MaliG3d1 *s, uint64_t head,
                                         uint64_t status_pa, const uint8_t job[64],
                                         const uint8_t fb[128], const uint8_t rt[64],
                                         unsigned *stage, unsigned attempt)
{
    static const uint8_t shader[64] = {
        0x29,0x00,0x00,0xb0,0x43,0xc0,0x66,0x80,0x23,0x33,0x07,0xc0,0x1c,0x08,0xc6,0x48,
        0x21,0x00,0x05,0x01,0x7c,0x58,0xcb,0x80,0x93,0x65,0x67,0x02,0x00,0x52,0xc4,0x58,
        0x44,0x00,0x86,0x41,0x0f,0x00,0x00,0x00,0x00,0x00,0x30,0xe0,0x01,0x73,0x01,0xc0,
        0x4e,0x08,0x30,0xcc,0x6b,0x18,0xcb,0x80,0x83,0xa5,0x02,0x00,0x04,0x00,0x48,0x02,
    };
    uint8_t draw[128], rsd[80], code[64], desc[32], sampler[32], surface[16];
    uint8_t viewport[32], position[64], row[VM3_PF_PITCH];
    uint32_t colors[VM3_PF_NX * VM3_PF_NY];
    Auto9Vm3PreframeStats stats = {0};
    unsigned n = 0, group_row = VM3_PF_NY, offset = 0, y = 0;
    uint64_t dcd = ldq_le_p(fb + 24), output = ldq_le_p(rt + 32), status;
    uint64_t state, table, samp, tls, surf, tagged, source, program, view, pos;
    unsigned tag, view_mask = BIT(8), pos_mask = BIT(16), pos_code;
    uint32_t renderer_diff;
    bool mapped, uniform, full, implicit;
    const uint32_t *atlas = NULL;
    g_autofree Auto9Vm3ClearSpan *spans = NULL, *sorted = NULL;
    g_autofree uint8_t *headers = NULL;

    *stage = FRAG_PROFILE;
    if ((ldq_le_p(job + 40) & 63) != 1 || ldl_le_p(job + 32) ||
        ldl_le_p(job + 36) != 0x003f007f || ldq_le_p(job + 48) || ldq_le_p(job + 56) ||
        ldq_le_p(fb) != 8 || ldq_le_p(fb + 8) || ldq_le_p(fb + 56) ||
        ldl_le_p(fb + 32) != VM3_PF_WH || ldl_le_p(fb + 36) ||
        ldl_le_p(fb + 40) != VM3_PF_WH || ldl_le_p(fb + 44) != 0x01001000 ||
        ldl_le_p(fb + 48) != 0x00010000 || ldl_le_p(fb + 52) ||
        ldl_le_p(rt) != 0x04000000 || (uint32_t)ldl_le_p(rt + 4) != 0x86888299U ||
        ldl_le_p(rt + 40) != VM3_PF_PITCH || ldl_le_p(rt + 44) ||
        ldq_le_p(rt + 48) || ldq_le_p(rt + 56) || (output & 3) ||
        !dcd || (dcd & 63) || dcd > (UINT64_C(1) << 48) - 256) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_TRANSLATE;
    spans = g_try_new(Auto9Vm3ClearSpan, VM3_PF_SPANS);
    sorted = g_try_new(Auto9Vm3ClearSpan, VM3_PF_SPANS);
    headers = g_try_malloc(VM3_PF_HEADER_BYTES);
    if (!spans || !sorted || !headers) {
        return G3D00_JOB_WRITE_FAULT;
    }
    if (!auto9_vm3_clear_plan(s, output, VM3_PF_H, VM3_PF_PITCH, VM3_PF_PITCH,
                             true, spans, VM3_PF_SPANS, &n) ||
        !auto9_g3d00_translate(s, head, sizeof(uint64_t) * 8, false, &status) ||
        status != status_pa) {
        return G3D00_JOB_WRITE_FAULT;
    }
    memcpy(sorted, spans, n * sizeof(*sorted));
    qsort(sorted, n, sizeof(*sorted), auto9_vm3_clear_span_cmp);
    for (unsigned i = 1; i < n; i++) {
        if (sorted[i - 1].pa + sorted[i - 1].length > sorted[i].pa) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    if (!auto9_vm3_stock_disjoint(status_pa, 64, sorted, n) ||
        !auto9_vm3_stock_read(s, ldq_le_p(job + 40) & ~UINT64_C(63),
                             NULL, 192, sorted, n)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    *stage = FRAG_TEXTURE_STATE;
    if (!auto9_vm3_stock_read(s, dcd + 128, draw, sizeof(draw), sorted, n)) {
        return G3D00_JOB_READ_FAULT;
    }
    state = ldq_le_p(draw + 56);
    table = ldq_le_p(draw + 32);
    samp = ldq_le_p(draw + 40);
    tls = ldq_le_p(draw + 112);
    if (ldq_le_p(draw) != 3 || ldq_le_p(draw + 8) || ldq_le_p(draw + 24) ||
        ldq_le_p(draw + 48) || ldq_le_p(draw + 64) || ldq_le_p(draw + 72) ||
        ldq_le_p(draw + 104)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_DRAW_FIELDS);
    }
    /* Bifrost DRAW word 26 is Occlusion; word 28 is Thread Storage.
     * The observed original DCD has a local-storage descriptor. The
     * pinned TEXC/BLEND program uses no stack or WLS payload. */
    if ((tls & 63) || !auto9_vm3_stock_read(s, tls, NULL, 32, sorted, n)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_LOCAL_STORAGE);
    }
    if ((state & 63) || !auto9_vm3_stock_read(s, state, rsd, sizeof(rsd), sorted, n)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_STATE_MAP);
    }
    if ((table & 31) || !auto9_vm3_stock_read(s, table, desc, sizeof(desc), sorted, n)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_TEXTURE_MAP);
    }
    if ((samp & 31) || !auto9_vm3_stock_read(s, samp, sampler, sizeof(sampler), sorted, n)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_SAMPLER_MAP);
    }
    program = ldq_le_p(rsd);
    renderer_diff = auto9_vm3_preframe_renderer_diff(rsd);
    if (attempt <= 64) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe renderer: attempt=%u "
                      "diff=0x%x sm=0x%x front=0x%x back=0x%x secondary=%u\n",
                      attempt, renderer_diff, ldl_le_p(rsd + 36), ldl_le_p(rsd + 40),
                      ldl_le_p(rsd + 44), (unsigned)!!ldl_le_p(rsd + 56));
    }
    if (renderer_diff) {
        return auto9_vm3_preframe_state_fault(attempt, PF_RENDERER_FIELDS);
    }
    if ((program & 15) ||
        !auto9_vm3_stock_read(s, program, code, sizeof(code), sorted, n)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_SHADER_MAP);
    }
    if (memcmp(code, shader, sizeof(shader))) {
        return auto9_vm3_preframe_state_fault(attempt, PF_SHADER_CODE);
    }
    if (ldl_le_p(sampler) != 0x50099901 || ldl_le_p(sampler + 4) ||
        ldq_le_p(sampler + 8) || ldq_le_p(sampler + 16) || ldq_le_p(sampler + 24)) {
        return auto9_vm3_preframe_state_fault(attempt, PF_SAMPLER_FIELDS);
    }
    if (ldl_le_p(desc) != 0x2ec00042 || ldl_le_p(desc + 4) != VM3_PF_WH ||
        ldl_le_p(desc + 8) != 0xc688 || ldl_le_p(desc + 12) ||
        ldl_le_p(desc + 24) || ldl_le_p(desc + 28) != VM3_PF_WH) {
        return auto9_vm3_preframe_state_fault(attempt, PF_TEXTURE_FIELDS);
    }
    *stage = FRAG_AFBC_LAYOUT;
    surf = ldq_le_p(desc + 16);
    if ((surf & 7) || !auto9_vm3_stock_read(s, surf, surface, sizeof(surface), sorted, n)) {
        return G3D00_JOB_READ_FAULT;
    }
    tagged = ldq_le_p(surface);
    source = tagged & ~UINT64_C(4095);
    tag = tagged & 4095;
    /* Only tiled 16x16 (no Wide). For solid blocks the header embeds
     * already decoded RGBA; YTR/split affect the absent body only. */
    if ((tag & ~0x3b) || !(tag & 8) ||
        ldl_le_p(surface + 8) != VM3_PF_NX * VM3_AFBC_HEADER_GROUP ||
        ldl_le_p(surface + 12) != VM3_PF_SLICE_BYTES ||
        !source || source > (UINT64_C(1) << 48) - VM3_PF_SLICE_BYTES) {
        if (attempt <= 64) {
            qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe layout: attempt=%u "
                          "tag=0x%x row=0x%x slice=0x%x\n", attempt, tag,
                          ldl_le_p(surface + 8), ldl_le_p(surface + 12));
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = FRAG_AFBC_HEADER;
    mapped = auto9_vm3_stock_read(s, source, headers, VM3_PF_HEADER_BYTES, sorted, n);
    uniform = mapped && auto9_vm3_preframe_headers(headers, colors, &stats);
    if (mapped && tag == 0x39) {
        atlas = auto9_vm3_atlas_lookup(s, 1024, source, sorted, n);
    }
    view = ldq_le_p(draw + 96);
    pos = ldq_le_p(draw + 16);
    if (!(view & 31) && auto9_vm3_stock_read(s, view, viewport, sizeof(viewport), sorted, n)) {
        view_mask = auto9_vm3_pf_view_diff(viewport);
    }
    pos_code = auto9_vm3_pf_read_position(s, pos, position, sorted, n);
    if (pos_code == PF_POS_OK) {
        pos_mask = auto9_vm3_pf_position_diff(position);
    }
    /* This exact Pre Frame 1 = ALWAYS job has no tiler, tile map or
     * ordinary draw. Arm's pre-frame path generates full-tile fragments;
     * the pinned shader reads the fragment position (r59), not varyings.
     * The stock driver uses a finite full-frame viewport with an aligned
     * position word that need not map vertex data for this path. Keep
     * readable-but-invalid geometry, aliases and DMA faults rejected. */
    implicit = !view_mask && pos_code == PF_POS_MMU && !(pos & 15) &&
               auto9_vm3_pf_f32(viewport) == 0.0f &&
               auto9_vm3_pf_f32(viewport + 4) == 0.0f &&
               auto9_vm3_pf_f32(viewport + 8) == VM3_PF_W &&
               auto9_vm3_pf_f32(viewport + 12) == VM3_PF_H;
    full = !view_mask && (!pos_mask || implicit);
    if (attempt <= 64) {
        if (!full || implicit) {
            qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe geometry: attempt=%u "
                          "viewport=0x%x position=0x%x poscode=%u alignment=%u implicit=%u\n",
                          attempt, view_mask, pos_mask, pos_code, (unsigned)(pos & 15),
                          (unsigned)implicit);
            if (!(view_mask & BIT(8))) {
                qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe clip: attempt=%u "
                              "xmin=0x%x ymin=0x%x xmax=0x%x ymax=0x%x\n", attempt,
                              ldl_le_p(viewport), ldl_le_p(viewport + 4),
                              ldl_le_p(viewport + 8), ldl_le_p(viewport + 12));
            }
        }
        if (mapped && qemu_loglevel_mask(LOG_GUEST_ERROR)) {
            unsigned matches;
            const Auto9Vm3AfbcProducer *origin = auto9_vm3_pf_origin(s, source, &matches);
            qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe origin: attempt=%u "
                          "seen=%u mapped=%u matches=%u sequence=%u format=0x%x "
                          "flags=0x%x modes=0x%x tiler=%u\n", attempt,
                          s->vm3_pf_producers_seen, s->vm3_pf_producers_mapped, matches,
                          origin ? origin->sequence : 0, origin ? origin->format : 0,
                          origin ? origin->flags : 0, origin ? origin->modes : 0,
                          origin ? (unsigned)origin->tiler : 0);
        }
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe input: attempt=%u tag=0x%x "
            "headers=%u solid=%u rgb=%u alpha=%u raw=%u coded=%u reserved=%u mixed=%u full=%u\n",
            attempt, tag, stats.headers, stats.solid, stats.rgb, stats.alpha, stats.raw,
            stats.coded, stats.reserved, stats.mixed_groups, (unsigned)full);
    }
    if (!mapped) {
        return G3D00_JOB_READ_FAULT;
    }
    if ((!uniform && !atlas) || !full) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* O(pixels) writeback. All inputs
     * and destinations have been checked before the first guest write. */
    *stage = FRAG_WRITE;
    if (atlas) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 private atlas sampled: height=1024\n");
    }
    for (unsigned i = 0; i < n; i++) {
        if (atlas && !offset) {
            for (unsigned x = 0; x < VM3_PF_W; x++) {
                stl_le_p(row + x * 4, atlas[y * VM3_PF_W + x]);
            }
        } else if (!atlas && y / VM3_PF_GROUP_PIXELS != group_row) {
            group_row = y / VM3_PF_GROUP_PIXELS;
            for (unsigned x = 0; x < VM3_PF_W; x++) {
                stl_le_p(row + x * 4, colors[group_row * VM3_PF_NX + x / VM3_PF_GROUP_PIXELS]);
            }
        }
        if (address_space_write(&address_space_memory, spans[i].pa, MEMTXATTRS_UNSPECIFIED,
                                row + offset, spans[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
        offset += spans[i].length;
        if (offset == sizeof(row)) {
            offset = 0;
            y++;
        }
    }
    s->fragment_bytes_written += VM3_PF_PITCH * VM3_PF_H;
    s->fragment_pixels_shaded += VM3_PF_W * VM3_PF_H;
    return G3D00_JOB_DONE;
}

static uint32_t auto9_g3d00_fragment_job(Auto9MaliG3d1 *s, uint64_t head,
                                        uint64_t status_pa, uint64_t *tiler,
                                        unsigned *stage, uint64_t *renderer)
{
    enum { MAX_PIXELS = 1920 * 1080, MAX_OVERDRAW = 16 };
    typedef struct { uint64_t pa; unsigned length; } Span;
    uint8_t job[64], fb[128], ext[64], rt[64];
    unsigned width, height, bytes, pixels, pages, count = 0, batches = 0;
    uint64_t address, header_pa, work = 0;
    g_autofree Span *spans = NULL;
    g_autofree uint8_t *image = NULL;
    g_autofree uint32_t *depth = NULL;

    *stage = FRAG_INPUT;
    s->vm3_fragment_attempts += s->vm3_control;
    if (!auto9_g3d00_fragment_inputs(s, head, job, fb, ext, rt, true)) {
        return G3D00_JOB_READ_FAULT;
    }
    *tiler = ldq_le_p(fb + 56);
    uint32_t wh = ldl_le_p(fb + 32), clear = ldl_le_p(rt + 48), flags = ldl_le_p(fb + 48);
    bool crc_read = flags & BIT(30);
    width = (wh & 65535) + 1;
    height = (wh >> 16) + 1;
    if (s->vm3_control) {
        auto9_vm3_pf_producer(s, fb, rt, !!*tiler, 0);
        auto9_vm3_atlas_targets(s, ldq_le_p(job + 40), fb);
        if ((wh == 0x07ff07ff || wh == VM3_PF_WH) && qemu_loglevel_mask(LOG_GUEST_ERROR) &&
            s->vm3_atlas_fragment_samples < 16) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 atlas fragment: n=%u wh=0x%x tiler=%u flags=0x%x "
                "fb44=0x%x rt0=0x%x rt4=0x%x rt24=0x%x rt28=0x%x "
                "tag=%u min=0x%x max=0x%x modes=0x%x\n",
                ++s->vm3_atlas_fragment_samples, wh, (unsigned)!!*tiler, flags,
                ldl_le_p(fb + 44), ldl_le_p(rt), ldl_le_p(rt + 4), ldl_le_p(rt + 24),
                ldl_le_p(rt + 28), (unsigned)(ldq_le_p(job + 40) & 63),
                ldl_le_p(job + 32), ldl_le_p(job + 36), ldl_le_p(fb) & 0x1ff);
            if (*tiler && (uint32_t)ldl_le_p(rt + 4) == 0x06888d99U &&
                ldl_le_p(fb) == 0x10 && (ldl_le_p(job + 36) & BIT(31))) {
                auto9_vm3_atlas_job_probe(s, s->vm3_atlas_fragment_samples, job, fb, ext, rt);
            }
        }
        /* Independent budget so early producer activity cannot consume all
         * samples before the first Always/clean-write job arrives. */
        if (wh == VM3_PF_WH && *tiler && flags == 0x80210000U &&
            (uint32_t)ldl_le_p(rt + 4) == 0x86888d99U && ldl_le_p(fb) == 0x8 &&
            qemu_loglevel_mask(LOG_GUEST_ERROR) && s->vm3_atlas_always_samples < 3) {
            unsigned n = 100 + ++s->vm3_atlas_always_samples;
            auto9_vm3_atlas_job_probe(s, n, job, fb, ext, rt);
        }
        if (++s->vm3_fragment_decoded <= 4) {
            bool has_crc_ext = ldq_le_p(job + 40) & 2;
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 fragment target: sample=%u dimensions=%ux%u tiler=%u flags=0x%x "
                "fb44=0x%x rt0=0x%x rt4=0x%x pitch=%u clear=0x%x\n",
                s->vm3_fragment_decoded, width, height, (unsigned)!!*tiler, flags,
                ldl_le_p(fb + 44), ldl_le_p(rt), ldl_le_p(rt + 4), ldl_le_p(rt + 40), clear);
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 fragment contract: sample=%u frame-modes=0x%x shader-dcd=%u "
                "sample-locations=%u crc-base=%u crc-stride=%u crc-target=%u\n",
                s->vm3_fragment_decoded, ldl_le_p(fb) & 0x1ff,
                (unsigned)!!ldq_le_p(fb + 24), (unsigned)!!ldq_le_p(fb + 16),
                (unsigned)(has_crc_ext && ldq_le_p(ext)), has_crc_ext ? ldl_le_p(ext + 8) : 0,
                has_crc_ext ? (ldl_le_p(ext + 12) >> 11) & 15 : 0);
        }
        /* A nonempty tiler context needs real upstream geometry execution. */
        if (*tiler) {
            unsigned matched = 0, indexed = 0;
            bool supported_indexed = wh == 0x02bb0617;
            for (unsigned i = 0; i < s->tile_batch_count; i++) {
                const Auto9G3d00TileBatch *b = &s->tile_batch[i];
                if (b->store_point && b->ttbr == s->as0_active_ttbr && b->context == *tiler) {
                    /* Resolve in draw order only after the genuine STORE
                     * operands and the framebuffer contract are known. */
                    *renderer = b->renderer;
                    *stage = FRAG_SHADER;
                    if (++s->vm3_point_fragment_jobs <= 12) {
                        qemu_log_mask(LOG_GUEST_ERROR,
                            "Auto9 VM3 point fragment pending: dimensions=%ux%u bin=%u/%u "
                            "flags=0x%x rt=0x%x bounds=0x%x/0x%x\n", width, height,
                            b->point_x / 16, b->point_y / 16, flags, ldl_le_p(rt + 4),
                            ldl_le_p(job + 32), ldl_le_p(job + 36));
                    }
                    return G3D00_JOB_CONFIG_FAULT;
                }
                if (b->ttbr == s->as0_active_ttbr && b->context == *tiler &&
                    b->width == width && b->height == height) {
                    matched++;
                    indexed += b->indexed;
                    if (b->indexed) {
                        supported_indexed &= b->interleaved_fan;
                        *renderer = b->renderer;
                    }
                }
            }
            s->vm3_fragment_matched_batches += matched;
            unsigned attempt = ++s->vm3_textured_attempts;
            if (attempt <= 4) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 G3D1 VM3 textured target: matches=%u dimensions=%ux%u flags=0x%x "
                    "fb44=0x%x rt0=0x%x rt4=0x%x pitch=%u modes=0x%x\n",
                    matched, width, height, flags, ldl_le_p(fb + 44), ldl_le_p(rt),
                    ldl_le_p(rt + 4), ldl_le_p(rt + 40), ldl_le_p(fb) & 0x1ff);
            }
            *stage = FRAG_NO_BATCH;
            uint32_t result = G3D00_JOB_CONFIG_FAULT;
            if (matched && wh == VM3_PF_WH && (uint32_t)ldl_le_p(rt + 4) == 0x86888d99U) {
                result = auto9_vm3_atlas_always_resolve(s, head, status_pa, job, fb, ext, rt, stage);
            } else if (indexed && !supported_indexed) {
                /* An IDVS batch does not imply the downstream fragment
                 * program is implemented. Never resolve it as a stock fan. */
                *stage = FRAG_SHADER;
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 indexed fragment pending: dimensions=%ux%u batches=%u\n",
                    width, height, indexed);
            } else if (matched) {
                if ((wh == 0x07ff07ff || wh == VM3_PF_WH) && ldl_le_p(rt + 4) == 0x06888d99) {
                    result = auto9_vm3_atlas_resolve(s, head, status_pa, job, fb, rt, stage);
                } else {
                    result = auto9_g3d1_vm3_textured_resolve(s, head, status_pa, job, fb, ext, rt, stage);
                }
            }
            if (flags == 0xc0250000U) {
                unsigned prior = s->vm3_center_depth_observed;
                auto9_vm3_center_depth_observe(s, job, fb, ext, rt, attempt);
                if (s->vm3_center_depth_observed != prior) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 G3D1 VM3 AFBC result: attempt=%u stage=%u status=0x%x\n",
                        attempt, *stage, result);
                }
            }
            s->vm3_fragment_tiler_rejections += result != G3D00_JOB_DONE;
            if (attempt <= 4 || (result != G3D00_JOB_DONE && s->vm3_fragment_tiler_rejections <= 4)) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 G3D1 VM3 textured result: attempt=%u stage=%u status=0x%x "
                    "completed=%u bytes=%" PRIu64 " nonzero=%" PRIu64 "\n",
                    attempt, *stage, result, s->vm3_textured_resolves,
                    s->vm3_textured_bytes, s->vm3_textured_nonzero_pixels);
                if (result != G3D00_JOB_DONE && *stage == FRAG_PROFILE) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 G3D1 VM3 alternate fragment target: wh=0x%x flags=0x%x fb44=0x%x "
                        "rt0=0x%x rt4=0x%x pitch=%u tag=%u bounds-min=0x%x bounds-max=0x%x\n",
                        wh, flags, ldl_le_p(fb + 44), ldl_le_p(rt), ldl_le_p(rt + 4),
                        ldl_le_p(rt + 40), (unsigned)(ldq_le_p(job + 40) & 63),
                        ldl_le_p(job + 32), ldl_le_p(job + 36));
                }
            }
            return result;
        }
        if (wh == VM3_PF_WH && flags == 0x00010000 &&
            (uint32_t)ldl_le_p(rt + 4) == 0x86888299U && ldl_le_p(fb) == 8) {
            unsigned attempt = ++s->vm3_preframe_attempts;
            uint32_t result = auto9_vm3_preframe_resolve(s, head, status_pa, job, fb, rt,
                                                       stage, attempt);
            if (attempt <= 64) {
                qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 AFBC preframe result: attempt=%u "
                              "stage=%u status=0x%x\n", attempt, *stage, result);
            }
            return result;
        }
        if (flags == 0x00210000U && (uint32_t)ldl_le_p(rt + 4) == 0x80008100U) {
            uint32_t result = auto9_vm3_empty_discard(s, head, status_pa, job, fb, ext, rt, stage);
            if (result != G3D00_JOB_DONE) {
                auto9_vm3_empty_fragment_fault(s, job, fb, ext, rt, *stage, result);
            }
            return result;
        }
        if (flags == 0x80210000U || flags == 0xc0210000U) {
            uint32_t result = auto9_g3d1_vm3_zero_resolve(s, head, status_pa, job, fb, ext, rt, stage);
            if (result != G3D00_JOB_DONE) {
                auto9_vm3_empty_fragment_fault(s, job, fb, ext, rt, *stage, result);
            }
            return result;
        }
    }
    *stage = FRAG_PROFILE;
    if (width > 4096 || height > 4096 || width * height > MAX_PIXELS ||
        (ldq_le_p(job + 40) & 63) != (s->vm3_control ? 1 : 3) || ldl_le_p(job + 32) ||
        (uint32_t)ldl_le_p(job + 36) != (((height - 1) / 16) << 16 | ((width - 1) / 16)) ||
        ldq_le_p(job + 48) || ldq_le_p(job + 56) || ldl_le_p(fb) ||
        ldq_le_p(fb + 24) || ldl_le_p(fb + 36) || (uint32_t)ldl_le_p(fb + 40) != wh ||
        ldl_le_p(fb + 44) != 0x01001000 ||
        (s->vm3_control ? flags != 0x20210000 : (flags & ~BIT(30)) != 0xa0210000) ||
        ldl_le_p(fb + 52) != 0x3f800000 || ldl_le_p(rt) != 0x04000000 ||
        (uint32_t)ldl_le_p(rt + 4) != (s->vm3_control ? 0x860a0299U : 0x860a8299U) ||
        (uint32_t)ldl_le_p(rt + 40) != width * 4 ||
        ldl_le_p(rt + 44) || (uint32_t)ldl_le_p(rt + 52) != clear ||
        (uint32_t)ldl_le_p(rt + 56) != clear || (uint32_t)ldl_le_p(rt + 60) != clear) {
        if (s->vm3_control) {
            auto9_vm3_empty_fragment_fault(s, job, fb, ext, rt, *stage, G3D00_JOB_CONFIG_FAULT);
        }
        return G3D00_JOB_CONFIG_FAULT;
    }
    address = ldq_le_p(rt + 32);
    pixels = width * height;
    bytes = pixels * 4;
    *stage = FRAG_TARGET;
    if (!address || (address & 63) || address >= (UINT64_C(1) << 48) ||
        bytes > (UINT64_C(1) << 48) - address ||
        !auto9_g3d00_translate(s, head, sizeof(job), false, &header_pa) ||
        header_pa != status_pa) {
        return G3D00_JOB_WRITE_FAULT;
    }
    pages = ((address & 4095) + bytes + 4095) / 4096;
    *stage = FRAG_STORAGE;
    spans = g_try_new(Span, pages);
    image = g_try_malloc(bytes);
    if (!spans || !image) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* Preflight every owning VM page before making any write. */
    *stage = FRAG_TRANSLATE;
    if (crc_read && !auto9_g3d00_crc_span(s, ext, width, height)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    unsigned offset = 0;
    for (unsigned i = 0; i < pages; i++) {
        uint64_t va = address + offset;
        spans[i].length = MIN(bytes - offset, 4096 - (va & 4095));
        if (!auto9_g3d00_translate(s, va, spans[i].length, true, &spans[i].pa) ||
            auto9_g3d00_overlap(spans[i].pa, spans[i].length, status_pa, sizeof(job))) {
            return G3D00_JOB_WRITE_FAULT;
        }
        offset += spans[i].length;
    }
    uint32_t output_clear = clear;
    if (s->vm3_control) {
        /* The validated VM3 profile takes an RGBA8 tilebuffer clear word
         * and uses RT swizzle 0x60a (B,G,R,A) for linear writeback. */
        output_clear = (clear & 0xff00ff00U) | ((clear & 0xffU) << 16) |
                       ((clear >> 16) & 0xffU);
    }
    /* QNX Screen already committed this linear scanout. Geometry jobs are
     * GLES overlays; filling every pixel with the RT clear colour erases
     * the GTF plane. Load the existing buffer, then raster on top.
     * Colour-clear jobs without a tiler still write the packed clear. */
    if (*tiler && !s->vm3_control) {
        offset = 0;
        for (unsigned i = 0; i < pages; i++) {
            if (address_space_read(&address_space_memory, spans[i].pa,
                                   MEMTXATTRS_UNSPECIFIED, image + offset,
                                   spans[i].length) != MEMTX_OK) {
                return G3D00_JOB_WRITE_FAULT;
            }
            offset += spans[i].length;
        }
    } else {
        for (unsigned i = 0; i < pixels; i++) {
            stl_le_p(image + i * 4, output_clear);
        }
    }
    if (*tiler) {
        *stage = FRAG_STORAGE;
        depth = g_try_new(uint32_t, pixels);
        if (!depth) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned i = 0; i < pixels; i++) {
            depth[i] = 0xffffff;
        }
        for (unsigned i = 0; i < s->tile_batch_count; i++) {
            const Auto9G3d00TileBatch *b = &s->tile_batch[i];
            if (b->ttbr != s->as0_active_ttbr || b->context != *tiler) {
                continue;
            }
            if (b->width != width || b->height != height ||
                !auto9_g3d00_fragment_shader(s, b->renderer)) {
                *stage = FRAG_SHADER;
                *renderer = b->renderer;
                return G3D00_JOB_CONFIG_FAULT;
            }
            for (unsigned j = 0; j < b->count; j++) {
                const Auto9G3d00Triangle *tri = &b->triangle[j];
                work += (tri->pixel[2] - tri->pixel[0] + 1) *
                        (tri->pixel[3] - tri->pixel[1] + 1);
                if (work > (uint64_t)pixels * MAX_OVERDRAW) {
                    *stage = FRAG_BUDGET;
                    return G3D00_JOB_CONFIG_FAULT;
                }
                count += auto9_g3d00_raster_triangle(tri, width, depth, image);
            }
            batches++;
        }
        if (!batches) {
            *stage = FRAG_NO_BATCH;
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    /* No transaction elimination on this virtual device. Even when the OEM
     * requests CRC comparison, write every tile and do not invent CRC data. */
    *stage = FRAG_WRITE;
    offset = 0;
    for (unsigned i = 0; i < pages; i++) {
        if (address_space_write(&address_space_memory, spans[i].pa, MEMTXATTRS_UNSPECIFIED,
                                image + offset, spans[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
        offset += spans[i].length;
    }
    s->fragment_bytes_written += bytes;
    s->fragment_pixels_shaded += count;
    s->fragment_te_unconditional += crc_read;
    return G3D00_JOB_DONE;
}

/* Identify only exactly bounded linear color RT0 mappings. Do not issue
 * stores or broaden the supported descriptor set. Read translation is
 * intentional: a failed write might still have an identifiable mapping. */
static bool auto9_vm3_center_rt_mapping(Auto9MaliG3d1 *s, const uint8_t fb[128],
                                       const uint8_t rt[64], char footprint[65],
                                       uint32_t shape[4])
{
    unsigned used = 0;
    uint32_t format = ldl_le_p(rt + 4);
    if (ldl_le_p(fb + 32) != 0x02bb0617U || ldl_le_p(rt) != 0x04000000U ||
        (format & 0x2fffU) != 0x0299U || ldl_le_p(rt + 40) != 6272 ||
        ldl_le_p(rt + 44)) {
        return 0;
    }
    shape[0] = ldl_le_p(fb + 44);
    shape[1] = ldl_le_p(fb + 48);
    shape[2] = format;
    shape[3] = !!ldq_le_p(fb + 56);
    g_autofree Auto9Vm3ClearSpan *spans = g_try_new(Auto9Vm3ClearSpan, 700 * 3);
    if (spans && auto9_vm3_clear_plan(s, ldq_le_p(rt + 32), 700, 6272, 6240,
                                    false, spans, 700 * 3, &used)) {
        auto9_vm3_center_footprint(spans, used, footprint);
    }
    return true;
}

static unsigned auto9_vm3_center_rt_probe(Auto9MaliG3d1 *s, uint64_t head,
                                         char footprint[65], uint32_t shape[4])
{
    uint8_t job[64], fb[128], ext[64], rt[64];
    if (!s->vm3_control || s->vm3_center_rt_probes >= 512 ||
        !qemu_loglevel_mask(LOG_GUEST_ERROR) ||
        !auto9_g3d00_fragment_inputs(s, head, job, fb, ext, rt, false) ||
        !auto9_vm3_center_rt_mapping(s, fb, rt, footprint, shape)) {
        return 0;
    }
    return ++s->vm3_center_rt_probes;
}

/* AUTO9_VM3_VERTEX3_MODEL */

typedef struct Auto9Vm3IndexedBuffer {
    uint64_t base;
    unsigned stride, size;
} Auto9Vm3IndexedBuffer;

static bool auto9_vm3_indexed_buffer(const uint8_t *rec, unsigned stride,
                                     unsigned tag, Auto9Vm3IndexedBuffer *out)
{
    uint64_t word = ldq_le_p(rec);
    if ((word >> 56) != tag) {
        return false;
    }
    word &= UINT64_C(0x00ffffffffffffff);
    out->base = word & UINT64_C(0x0000ffffffffffc0);
    out->stride = ldl_le_p(rec + 8);
    out->size = ldl_le_p(rec + 12);
    return out->base && word == (out->base | 1) && out->stride == stride;
}

static uint64_t auto9_vm3_indexed_address(const Auto9Vm3IndexedBuffer *buffer,
                                          unsigned index, unsigned offset, unsigned bytes)
{
    uint64_t start = (uint64_t)buffer->stride * index + offset;
    return start + bytes <= buffer->size ? buffer->base + start : 0;
}

/* Bifrost v7 IDVS has two draws, not a type-7 job followed by a type-5
 * descriptor. r61 indexes input at offset_start + local index; varying
 * output is local. The special position varying uses the fragment draw's
 * position storage, not the placeholder buffer in vertex varying entry 0. */
static uint32_t auto9_vm3_indexed_job(Auto9MaliG3d1 *s, uint64_t head,
                                      uint64_t status_pa, Auto9G3d00TileBatch *batch,
                                      unsigned *stage)
{
    enum { VERTICES = 24, INDEX_COUNT = 42, MAX_WRITES = VERTICES * 4 };
    uint8_t job[384], rsd[64], program[352], secondary[208], fau[192], indices[INDEX_COUNT * 2];
    uint8_t attr[24], vary[32], abuf[32], vbuf[32], ctx[32], viewport[32], heap[32];
    uint8_t output[VERTICES][2][16];
    /* Each of the three attributes can cross a page; include metadata. */
    Auto9Vm3ClearSpan reads[VERTICES * 6 + 32] = {{status_pa, 4}}, targets[MAX_WRITES];
    Auto9Vm3Vertex3Span writes[MAX_WRITES];
    unsigned nread = 1, nwrite = 0, order[INDEX_COUNT], seen = 0;
    const uint8_t *primitive = job + 40, *frag = job + 128, *vert = job + 256;
    static const uint32_t aformat[] = {0x2bc04200, 0x2fc00201, 0x2bc04200};
    static const unsigned aoffset[] = {0, 16, 8}, awidth[] = {8, 16, 8};
    static const uint32_t vformat[] = {0x17c39c00, 0x17800001, 0x37c00000, 0x2bc00000};
    Auto9Vm3IndexedBuffer in[2], out[2];
    float u[48];

    *stage = 1;
    if (!auto9_vm3_vertex_read(s, head, job, sizeof(job), reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    uint32_t start = ldl_le_p(vert + 4), base_word = ldl_le_p(primitive + 4);
    uint32_t inv = ldl_le_p(job + 32), count_minus_one = ldl_le_p(primitive + 12);
    /* Only the observed non-instanced invocation/count pairs are supported. */
    unsigned vertices = inv == 3 ? 4 : inv == 7 ? 8 : inv == 15 ? 16 : VERTICES;
    unsigned index_count = vertices == 4 ? 6 : vertices == 8 ? 10 :
                           vertices == 16 ? 30 : INDEX_COUNT;
    int64_t base = (int64_t)base_word - ((base_word & BIT(31)) ? INT64_C(4294967296) : 0);
    if ((inv != 3 && inv != 7 && inv != 15 && inv != VERTICES - 1) || ldl_le_p(job + 36) != 0x38000000 ||
        ldl_le_p(primitive) != 0x1407020a || ldl_le_p(primitive + 8) ||
        count_minus_one != index_count - 1 ||
        ldl_le_p(vert) != 2 || (ldl_le_p(frag) & ~BIT(5)) != 3 ||
        (uint32_t)ldl_le_p(frag + 4) != start || ldq_le_p(vert + 8) ||
        ldq_le_p(vert + 16) || ldq_le_p(frag + 8) || start > UINT16_MAX) {
        return G3D00_JOB_CONFIG_FAULT;
    }

    *stage = 2;
    uint64_t va[] = {
        ldq_le_p(vert + 56), ldq_le_p(vert + 48), ldq_le_p(primitive + 16),
        ldq_le_p(vert + 72), ldq_le_p(vert + 88), ldq_le_p(vert + 64),
        ldq_le_p(vert + 80), ldq_le_p(job + 72), ldq_le_p(frag + 96),
    };
    struct { void *data; unsigned size, align; } meta[] = {
        {rsd, sizeof(rsd), 64}, {fau, sizeof(fau), 8}, {indices, index_count * 2, 2},
        {attr, sizeof(attr), 8}, {vary, sizeof(vary), 8}, {abuf, sizeof(abuf), 16},
        {vbuf, sizeof(vbuf), 16}, {ctx, sizeof(ctx), 64}, {viewport, sizeof(viewport), 32},
    };
    for (unsigned i = 0; i < G_N_ELEMENTS(meta); i++) {
        if (!va[i] || (va[i] & (meta[i].align - 1))) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!auto9_vm3_vertex_read(s, va[i], meta[i].data, meta[i].size,
                                  reads, G_N_ELEMENTS(reads), &nread)) {
            return G3D00_JOB_READ_FAULT;
        }
    }
    uint64_t pc = ldq_le_p(rsd), pc2 = ldq_le_p(rsd + 56);
    if (!pc || !pc2 || ((pc | pc2) & 15) || ldl_le_p(rsd + 8) ||
        ldl_le_p(rsd + 12) != 0x40003 || ldl_le_p(rsd + 16) != 0x0800a001 ||
        ldl_le_p(rsd + 48) != 0xc6c00 || ldl_le_p(rsd + 52) != 0xc6000) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    *stage = 3;
    if (!auto9_vm3_vertex_read(s, pc, program, sizeof(program), reads, G_N_ELEMENTS(reads), &nread) ||
        !auto9_vm3_vertex_read(s, pc2, secondary, sizeof(secondary), reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, program, sizeof(program));
    g_autofree char *sha2 = g_compute_checksum_for_data(G_CHECKSUM_SHA256, secondary, sizeof(secondary));
    if (!sha || !sha2 ||
        strcmp(sha, "543394558325daf16772336d8a7232363eefab2d07b2b2bfa96902e8a94d5fe9") ||
        strcmp(sha2, "667f251fe13c30fc9aa14e96be3b6e81182a7a6da0def1bd4164ce41d2d7a3da")) {
        return G3D00_JOB_CONFIG_FAULT;
    }

    *stage = 4;
    for (unsigned i = 0; i < G_N_ELEMENTS(aformat); i++) {
        if ((uint32_t)ldl_le_p(attr + i * 8) != aformat[i] ||
            (uint32_t)ldl_le_p(attr + i * 8 + 4) != aoffset[i]) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(vformat); i++) {
        if ((uint32_t)ldl_le_p(vary + i * 8) != vformat[i] ||
            ldl_le_p(vary + i * 8 + 4) != (i == 3 ? 8 : 0)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    unsigned interleaved_tag = vbuf[7], position_tag = vbuf[23];
    /* This original non-instanced quad uses local 64-byte buffers. The
     * two pinned programs do not consume the 1D output divisor byte. */
    bool tagged_quad = vertices == 4 &&
        ((interleaved_tag == 0x44 && (position_tag == 0 || position_tag == 0x44)) ||
         (interleaved_tag == 0 &&
          (((start == 0x23c0 || start == 0x23c4 || start == 0x2398) && position_tag == 0xc4) ||
           ((start == 0x23d4 || start == 0x2350 || start == 0x2354 ||
             start == 0x2360) && position_tag == 0xbf)) &&
          base == -(int64_t)start)) &&
        ldl_le_p(vbuf + 12) == 64 && ldl_le_p(vbuf + 28) == 64;
    /* Observed 42-index bindings. The same local strip order and
     * exact lengths are checked below, independent of the guest addresses. */
    bool tagged_wide = vertices == VERTICES &&
                       ((interleaved_tag == 0xc4 && position_tag == 0x44) ||
                        ((start == 0x2338 || start == 0x233c || start == 0x2348) &&
                         interleaved_tag == 0x44 && position_tag == 0));
    /* The observed type-1 tag does not change linear writeback. Match the
     * precise offset, tag pair, buffer lengths and local index order. */
    bool tagged_grid = vertices == 16 && base == -(int64_t)start &&
                       ((interleaved_tag == 0 &&
                         (((start == 0x23c8 || start == 0x23e0) && position_tag == 0xbf) ||
                          ((start == 0x2328 || start == 0x232c || start == 0x2334) &&
                           position_tag == 0x44) ||
                          (start == 0x2328 && position_tag == 0xc3))) ||
                        (start == 0x23e0 && interleaved_tag == 0xbf && position_tag == 0) ||
                        (start == 0x23c4 && interleaved_tag == 0x44 && position_tag == 0)) &&
                       ldl_le_p(vbuf + 12) == 256 && ldl_le_p(vbuf + 28) == 256;
    bool tagged_row = vertices == 8 && start == 0x23d8 && base == -(int64_t)start &&
                      (interleaved_tag == 0x38 || interleaved_tag == 0x44) && position_tag == 0 &&
                      ldl_le_p(vbuf + 12) == 128 && ldl_le_p(vbuf + 28) == 128;
    if ((!tagged_quad && !tagged_wide && !tagged_grid && !tagged_row &&
         ((interleaved_tag && interleaved_tag != 0xff) ||
          (position_tag && position_tag != 0xff))) ||
        !auto9_vm3_indexed_buffer(abuf, 24, 0, &in[0]) ||
        !auto9_vm3_indexed_buffer(abuf + 16, 0, 0, &in[1]) ||
        !auto9_vm3_indexed_buffer(vbuf, 16, interleaved_tag, &out[1]) ||
        !auto9_vm3_indexed_buffer(vbuf + 16, 16, position_tag, &out[0]) ||
        ldq_le_p(frag + 16) != out[0].base) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned i = 0; i < index_count; i++) {
        int64_t local = lduw_le_p(indices + 2 * i) + base;
        if (local < 0 || local >= vertices) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        order[i] = local;
        seen |= BIT(local);
    }
    static const unsigned quad_order[] = {0, 0, 2, 1, 3, 3};
    if (tagged_quad && memcmp(order, quad_order, sizeof(quad_order))) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (tagged_grid || tagged_row) {
        /* The 10-index row is the first strip of the 30-index grid. */
        static const unsigned grid[] = {0,0,4,1,5,2,6,3,7,7,
                                       4,4,8,5,9,6,10,7,11,11,
                                       8,8,12,9,13,10,14,11,15,15};
        if (memcmp(order, grid, index_count * sizeof(order[0])) ||
            in[0].size != 221184 || in[1].size != 256) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    if (vertices == VERTICES) {
        /* Three strips over four rows of six vertices. The DMA addresses
         * and index origin remain supplied by the actual draw descriptors. */
        if ((!tagged_wide && (interleaved_tag || position_tag)) ||
            out[0].size != VERTICES * 16 ||
            out[1].size != VERTICES * 16 || in[0].size != 221184 || in[1].size != 256 ||
            base != -(int64_t)start) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned row = 0; row < 3; row++) {
            unsigned b = row * 14, v = row * 6;
            if (order[b] != v || order[b + 13] != v + 11) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            for (unsigned col = 0; col < 6; col++) {
                if (order[b + 1 + col * 2] != v + col ||
                    order[b + 2 + col * 2] != v + col + 6) {
                    return G3D00_JOB_CONFIG_FAULT;
                }
            }
        }
    }

    *stage = 5;
    uint32_t wh = ldl_le_p(ctx + 12), lo = ldl_le_p(viewport + 24), hi = ldl_le_p(viewport + 28);
    batch->width = (wh & 65535) + 1;
    batch->height = (wh >> 16) + 1;
    uint64_t heap_va = ldq_le_p(ctx + 24);
    if ((wh & 65535) >= 4096 || (wh >> 16) >= 4096 ||
        (ldl_le_p(ctx + 8) & ~0x1ffffU) || !heap_va || (heap_va & 63) ||
        ldq_le_p(viewport + 16) != UINT64_C(0x3f80000000000000) ||
        (lo & 65535) > (hi & 65535) || (lo >> 16) > (hi >> 16) ||
        (hi & 65535) >= batch->width || (hi >> 16) >= batch->height) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned i = 0; i < 2; i++) {
        float lower = auto9_g3d00_float(viewport + 4 * i);
        float upper = auto9_g3d00_float(viewport + 8 + 4 * i);
        if (!(lower <= upper)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    if (!auto9_vm3_vertex_read(s, heap_va, heap, sizeof(heap), reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    uint64_t heap_base = ldq_le_p(heap + 8), bottom = ldq_le_p(heap + 16), top = ldq_le_p(heap + 24);
    uint32_t size = ldl_le_p(heap + 4);
    if (!heap_base || ((heap_base | bottom | top | size) & 4095) || heap_base > bottom ||
        bottom >= top || top >= (UINT64_C(1) << 48) || top - heap_base > size) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    const int32_t clip_lo[] = {(lo & 65535) * 256, (lo >> 16) * 256};
    const int32_t clip_hi[] = {((hi & 65535) + 1) * 256, ((hi >> 16) + 1) * 256};

    *stage = 6;
    for (unsigned i = 0; i < G_N_ELEMENTS(u); i++) {
        u[i] = auto9_g3d00_float(fau + 4 * i);
    }
    for (unsigned i = 0; i < vertices; i++) {
        if (!(seen & BIT(i))) {
            continue;
        }
        uint8_t raw[3][16];
        for (unsigned k = 0; k < 3; k++) {
            uint64_t addr = auto9_vm3_indexed_address(&in[k == 1], start + i, aoffset[k], awidth[k]);
            if (!addr || !auto9_vm3_vertex_read(s, addr, raw[k], awidth[k], reads, G_N_ELEMENTS(reads), &nread)) {
                return G3D00_JOB_READ_FAULT;
            }
        }
        float xy[] = {auto9_g3d00_float(raw[0]), auto9_g3d00_float(raw[0] + 4)}, pos[4];
        float uv[] = {auto9_g3d00_float(raw[2]), auto9_g3d00_float(raw[2] + 4)};
        if (!auto9_bifrost_vm3_indexed_position(xy, u, pos) || !pos[3] ||
            !auto9_bifrost_vm3_indexed_uv(uv, u, ldl_le_p(fau + 4 * 32))) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        for (unsigned k = 0; k < 4; k++) {
            uint32_t bits;
            memcpy(&bits, &pos[k], sizeof(bits));
            stl_le_p(output[i][0] + 4 * k, bits);
            uint16_t half = auto9_bifrost_f32_half_rne(auto9_g3d00_float(raw[1] + 4 * k));
            if ((half & 0x7c00) == 0x7c00) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            stw_le_p(output[i][1] + 2 * k, half);
        }
        for (unsigned k = 0; k < 2; k++) {
            uint32_t bits;
            memcpy(&bits, &uv[k], sizeof(bits));
            stl_le_p(output[i][1] + 8 + 4 * k, bits);
        }
        for (unsigned k = 0; k < 2; k++) {
            uint64_t addr = auto9_vm3_indexed_address(&out[k], i, 0, 16);
            unsigned first = nwrite, used = 0;
            if (!addr || !auto9_vm3_clear_plan(s, addr, 1, 0, 16, true,
                                              targets, G_N_ELEMENTS(targets), &nwrite)) {
                return G3D00_JOB_WRITE_FAULT;
            }
            for (unsigned j = first; j < nwrite; j++) {
                writes[j] = (Auto9Vm3Vertex3Span){targets[j].pa, targets[j].length, output[i][k] + used};
                used += targets[j].length;
            }
        }
    }

    *stage = 7;
    for (unsigned i = 0; i + 2 < index_count; i++) {
        const unsigned v[] = {order[i + (i & 1)], order[i + !(i & 1)], order[i + 2]};
        Auto9G3d00Triangle tri = {0};
        bool clip = false;
        if (v[0] == v[1] || v[0] == v[2] || v[1] == v[2]) {
            continue;
        }
        for (unsigned j = 0; j < 3; j++) {
            const uint8_t *p = output[v[j]][0], *color = output[v[j]][1];
            float window[4] = {auto9_g3d00_float(p), auto9_g3d00_float(p + 4), 0, 0};
            if (!auto9_bifrost_snap4(window)) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            for (unsigned k = 0; k < 2; k++) {
                float xy = window[k] * 256.0f;
                if (!(fabsf(xy) <= 16777216.0f)) {
                    return G3D00_JOB_CONFIG_FAULT;
                }
                tri.xy[j][k] = xy;
                tri.uv32[j][k] = auto9_g3d00_float(color + 8 + 4 * k);
            }
            for (unsigned k = 0; k < 4; k++) {
                tri.rgba[j][k] = lduw_le_p(color + 2 * k);
            }
            tri.z[j] = auto9_g3d00_float(p + 8);
            tri.inv_w[j] = auto9_g3d00_float(p + 12);
            clip |= tri.inv_w[j] < 0 || tri.z[j] < 0 || tri.z[j] > 1;
        }
        if (clip) {
            if (!auto9_vm3_clip_color_triangle(batch, &tri, clip_lo, clip_hi)) {
                return G3D00_JOB_CONFIG_FAULT;
            }
        } else if (auto9_g3d00_bin_triangle(&tri, clip_lo, clip_hi)) {
            if (batch->count >= G_N_ELEMENTS(batch->triangle)) {
                return G3D00_JOB_CONFIG_FAULT;
            }
            batch->triangle[batch->count++] = tri;
        }
    }
    *stage = 8;
    if (!auto9_vm3_vertex448_disjoint(reads, nread, targets, nwrite)) {
        return G3D00_JOB_WRITE_FAULT;
    }
    for (unsigned i = 0; i < nwrite; i++) {
        if (address_space_write(&address_space_memory, writes[i].pa, MEMTXATTRS_UNSPECIFIED,
                                writes[i].data, writes[i].length) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    }
    batch->ttbr = s->as0_active_ttbr;
    batch->context = va[7];
    batch->renderer = ldq_le_p(frag + 56);
    batch->textures = ldq_le_p(frag + 32);
    batch->samplers = ldq_le_p(frag + 40);
    batch->push_uniforms = ldq_le_p(frag + 48);
    batch->index_count = index_count;
    /* The checked vertex ABI writes RGBA16F + RG32F exactly like the
     * interleaved draw path. The fragment resolver still checks its shader. */
    batch->interleaved_fan = true;
    batch->indexed = true;
    if ((tagged_quad || tagged_wide || tagged_grid || tagged_row) &&
        s->vm3_indexed_tagged_traces < 16 &&
        qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        unsigned n = ++s->vm3_indexed_tagged_traces;
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 indexed tagged writeback: n=%u first=%u interleaved=0x%x "
            "position=0x%x bins=%u spans=%u\n",
            n, start, interleaved_tag, position_tag, batch->count, nwrite);
    }
    return G3D00_JOB_DONE;
}

static bool auto9_vm3_compute_sum(const uint8_t *pair, uint32_t *result)
{
    float a = auto9_g3d00_float(pair), b = auto9_g3d00_float(pair + 4);
    if (!isfinite(a) || !isfinite(b) || fpclassify(a) == FP_SUBNORMAL || fpclassify(b) == FP_SUBNORMAL) {
        return false;
    }
    float value = a + b;
    if (!isfinite(value) || fpclassify(value) == FP_SUBNORMAL) {
        return false;
    }
    memcpy(result, &value, sizeof(*result));
    return true;
}

/* Use the same ordinal as the existing rejected-job reader. This reports
 * exactly one failure site and never logs addresses or shader operands. */
static uint32_t auto9_vm3_compute_write_fault(Auto9MaliG3d1 *s,
                                              const char *stage, unsigned store,
                                              unsigned detail)
{
    if (s->vm3_compute_samples < 12) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 compute write fault: sample=%u stage=%s store=%u detail=%u\n",
            s->vm3_compute_samples + 1, stage, store, detail);
    }
    return G3D00_JOB_WRITE_FAULT;
}

/* Two native single-invocation Compute programs, not a generic dispatcher.
 * Their only writes are the exact six STOREs decoded from the pinned ISA. */
static uint32_t auto9_vm3_compute_job(Auto9MaliG3d1 *s, uint64_t head,
                                      uint64_t status_pa)
{
    static const char *const digests[2] = {
        "7c3af3ff6acfa8f1ee210cde850dbbb670c316dd393366e70dae73ebb7110e46",
        "896190d47196939f0a003473771dc4569d6323eb410647d221c0f1fcd1d56fab",
    };
    static const unsigned fadd_count[2] = { 2, 4 }, first_pair[2] = { 29, 33 };
    static const unsigned offset[2][6] = {
        { 0x30, 0x20, 0xc8, 0xd0, 0xd8, 0xe0 },
        { 0x30, 0x20, 0xe8, 0xf0, 0xf8, 0x100 },
    };
    static const unsigned vector_order[2][4] = {{1, 0, 1, 0}, {1, 0, 2, 3}};
    static const unsigned scalar_order[2][4] = {{0, 0, 1, 1}, {0, 1, 2, 3}};
    static const unsigned len[6] = { 2, 16, 4, 4, 4, 4 };
    uint8_t header[192], rsd[64], shader[256], value[6][16];
    uint8_t address[8], predicate[4], pairs[32];
    Auto9Vm3ClearSpan reads[16], stores[12];
    unsigned nread = 0, nstore = 0, start[7] = {0}, read_ends[4];
    uint32_t sum[4] = {0};

    if (!auto9_vm3_vertex_read(s, head, header, sizeof(header), reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    if (reads[0].pa != status_pa) {
        return auto9_vm3_compute_write_fault(s, "status-mismatch", 0, 0);
    }
    read_ends[0] = nread;
    const uint8_t *draw = header + 64;
    if (ldl_le_p(header + 32) || ldl_le_p(header + 36) != 0x400000 ||
        ldl_le_p(header + 40) != 0x14000000 || ldl_le_p(header + 44) ||
        ldq_le_p(header + 48) || ldq_le_p(header + 56) || ldl_le_p(draw) != 2 || ldl_le_p(draw + 8)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    uint64_t r = ldq_le_p(draw + 56), u = ldq_le_p(draw + 48);
    if ((r & 63) || (u & 7) ||
        !auto9_vm3_vertex_read(s, r, rsd, sizeof(rsd), reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    read_ends[1] = nread;
    uint32_t preload = ldl_le_p(rsd + 48);
    if (ldl_le_p(rsd + 8) || ldl_le_p(rsd + 12) || ldl_le_p(rsd + 16) != 0x08002001 ||
        (preload != 0xf8000 && preload != 0x128000)) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    unsigned mode = preload == 0x128000;
    unsigned size = mode ? 256 : 240;
    unsigned base_off = 8 * (mode ? 19 : 17), cmp_off = 8 * (mode ? 24 : 20);
    unsigned pair_off = 8 * first_pair[mode], pair_bytes = 8 * fadd_count[mode];
    uint64_t pc = ldq_le_p(rsd);
    if ((pc & 15) || !auto9_vm3_vertex_read(s, pc, shader, size, reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }
    read_ends[2] = nread;
    g_autofree char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256, shader, size);
    if (!digest || strcmp(digest, digests[mode])) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    /* These two pinned programs read only these FAU words. The guest
     * packs unrelated output data into the same allocation. Record only
     * consumed spans for alias checks, while keeping descriptor/code and
     * every actual input protected, including inputs used after STORE #1. */
    if (!u || u >= (UINT64_C(1) << 48) ||
        pair_off + pair_bytes > (UINT64_C(1) << 48) - u ||
        !auto9_vm3_vertex_read(s, u + base_off, address, sizeof(address),
                              reads, G_N_ELEMENTS(reads), &nread) ||
        !auto9_vm3_vertex_read(s, u + cmp_off, predicate, sizeof(predicate),
                              reads, G_N_ELEMENTS(reads), &nread) ||
        !auto9_vm3_vertex_read(s, u + pair_off, pairs, pair_bytes,
                              reads, G_N_ELEMENTS(reads), &nread)) {
        return G3D00_JOB_READ_FAULT;
    }

    read_ends[3] = nread;
    uint64_t base = ldq_le_p(address);
    uint32_t compare = ldl_le_p(predicate);
    if (!base || (base & 3) || base >= (UINT64_C(1) << 48) ||
        (uint32_t)base > UINT32_MAX - offset[mode][5] - len[5]) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned i = 0; i < fadd_count[mode]; i++) {
        if (!auto9_vm3_compute_sum(pairs + 8 * i, &sum[i])) {
            return G3D00_JOB_CONFIG_FAULT;
        }
    }
    /* ICMP.v2i16.eq defaults to i1; STORE.i16 uses the low result lane. */
    stw_le_p(value[0], (uint16_t)compare == 0);
    for (unsigned i = 0; i < 4; i++) {
        stl_le_p(value[1] + 4 * i, sum[vector_order[mode][i]]);
        stl_le_p(value[2 + i], sum[scalar_order[mode][i]]);
    }
    for (unsigned i = 0; i < 6; i++) {
        if (!auto9_vm3_clear_plan(s, base + offset[mode][i], 1, 0, len[i], true,
                                 stores, G_N_ELEMENTS(stores), &nstore)) {
            /* Distinguish a readable but non-writable span from a bad map.
             * The second walk does not read or write target bytes. */
            unsigned readable = 0;
            Auto9Vm3ClearSpan probe[2];
            if (s->vm3_compute_samples < 12 && qemu_loglevel_mask(LOG_GUEST_ERROR)) {
                auto9_vm3_clear_plan(s, base + offset[mode][i], 1, 0, len[i], false,
                                    probe, G_N_ELEMENTS(probe), &readable);
            }
            return auto9_vm3_compute_write_fault(s, "store-map", i + 1, readable);
        }
        start[i + 1] = nstore;
    }
    for (unsigned i = 0; i < nstore; i++) {
        unsigned store = 1;
        while (i >= start[store]) {
            store++;
        }
        for (unsigned j = 0; j < nread; j++) {
            if (auto9_g3d00_overlap(stores[i].pa, stores[i].length, reads[j].pa, reads[j].length)) {
                /* Read-span groups: header, renderer state, code, FAU. */
                unsigned input = 0;
                while (j >= read_ends[input]) {
                    input++;
                }
                return auto9_vm3_compute_write_fault(s, "input-alias", store, input + 1);
            }
        }
        for (unsigned j = 0; j < i; j++) {
            if (auto9_g3d00_overlap(stores[i].pa, stores[i].length, stores[j].pa, stores[j].length)) {
                return auto9_vm3_compute_write_fault(s, "store-alias", store, 0);
            }
        }
    }
    for (unsigned i = 0; i < 6; i++) {
        const uint8_t *src = value[i];
        for (unsigned j = start[i]; j < start[i + 1]; j++) {
            if (address_space_write(&address_space_memory, stores[j].pa, MEMTXATTRS_UNSPECIFIED,
                                    src, stores[j].length) != MEMTX_OK) {
                return auto9_vm3_compute_write_fault(s, "store-transaction", i + 1, j - start[i] + 1);
            }
            src += stores[j].length;
        }
    }
    if (++s->vm3_compute_jobs <= 16) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 compute writeback: n=%u program-bytes=%u stores=6 bytes=34 first=%u\n",
            s->vm3_compute_jobs, size, ldl_le_p(draw + 4));
    }
    return G3D00_JOB_DONE;
}

static void auto9_vm3_tiler_stop_sample(Auto9MaliG3d1 *s, uint64_t head,
                                        unsigned slot, unsigned stage, uint32_t result);
static void auto9_g3d1_vm3_geometry_observe(Auto9MaliG3d1 *s, uint64_t head,
                                            unsigned type, unsigned slot,
                                            unsigned sample, bool stopped);

/* First rejection at each real viewport check, independent of the startup
 * success sample budget. Read only descriptor metadata and framed code;
 * never read the vertex contents, push values or output buffers here. */
static void auto9_qnx_vertex_fault(Auto9MaliG3d1 *s, uint64_t head,
                                  unsigned slot, unsigned stage, uint32_t status)
{
    uint8_t inv[8], draw[128], rsd[64], code[768];
    unsigned seen = BIT(stage);

    if (!s->g3d00 || !qemu_loglevel_mask(LOG_GUEST_ERROR) ||
        ((s->qnx_vertex_first_stages & seen) && s->qnx_vertex_fault_samples >= 12)) {
        return;
    }
    s->qnx_vertex_first_stages |= seen;
    unsigned n = ++s->qnx_vertex_fault_samples;
    uint8_t header[32];
    unsigned index = auto9_g3d00_dma_read(s, head, header, sizeof(header))
                   ? (uint32_t)ldl_le_p(header + 16) >> 16 : 0;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 QNX viewport fault: n=%u slot=%u index=%u stage=%u status=0x%x\n",
        n, slot, index, stage, status);
    if (!auto9_g3d00_dma_read(s, head + 32, inv, sizeof(inv)) ||
        !auto9_g3d00_dma_read(s, head + 64, draw, sizeof(draw))) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 QNX viewport draw: n=%u inv=0x%x/0x%x flags=0x%x bias=%u position=%u\n",
        n, ldl_le_p(inv), ldl_le_p(inv + 4), ldl_le_p(draw),
        (unsigned)!!ldq_le_p(draw + 4), (unsigned)!!ldq_le_p(draw + 16));
    uint64_t state = ldq_le_p(draw + 56);
    if (!state || (state & 63) ||
        !auto9_g3d00_dma_read(s, state, rsd, sizeof(rsd))) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 QNX viewport renderer: n=%u r8=0x%x attributes=%u varyings=%u preload=0x%x\n",
        n, ldl_le_p(rsd + 8), lduw_le_p(rsd + 12), lduw_le_p(rsd + 14),
        ldl_le_p(rsd + 48));
    uint64_t pc = ldq_le_p(rsd);
    if (!pc || (pc & 15)) {
        return;
    }
    unsigned capacity = MIN(sizeof(code), AUTO9_G3D00_GPU_PAGE -
                            (pc & (AUTO9_G3D00_GPU_PAGE - 1)));
    if (!auto9_g3d00_dma_read(s, pc, code, capacity)) {
        return;
    }
    unsigned stop = 0, reason = 0;
    unsigned extent = auto9_g3d1_bifrost_extent(code, capacity, &stop, &reason);
    g_autofree char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code,
                                                          MIN(capacity, 64U));
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 QNX viewport program: n=%u prefix-sha256=%s prefix-bytes=%u extent=%u stop=%u reason=%u\n",
        n, digest ? digest : "-", MIN(capacity, 64U), extent, stop, reason);
    for (unsigned off = 0; off < extent; off += 128) {
        auto9_g3d00_sample_bytes("qnx-viewport-fault", n, "framed-program", off,
                                 code + off, MIN(extent - off, 128U));
    }
}

/* Run one supported descriptor. Commit status after its real operation. */
static uint32_t auto9_g3d00_control_descriptor(Auto9MaliG3d1 *s, uint64_t head,
                                              unsigned slot, unsigned type)
{
    uint8_t payload[24], status[4];
    uint64_t wb, dst, addr;
    uint32_t mode;
    unsigned width;
    bool flush = false;

    /* VM3 has a separate, bounded implementation of the same job types. */
    bool compute = type == 4 && s->vm3_control && slot == 1;
    bool indexed = type == 10 && s->vm3_control && slot == 1;
    if ((type == 9 && slot != 0) ||
        (type != 1 && type != 3 && type != 5 && type != 7 && type != 9 &&
         !compute && !indexed && (type != 2 || slot != 1))) {
        return G3D00_JOB_CONFIG_FAULT;
    }
    if (!auto9_g3d00_translate(s, head, sizeof(status), true, &wb)) {
        return compute ? auto9_vm3_compute_write_fault(s, "status-map", 0, 0) : G3D00_JOB_WRITE_FAULT;
    }
    if (type == 9) {
        uint64_t tiler = 0, renderer = 0;
        unsigned stage;
        char footprint[65] = {0};
        uint32_t shape[4];
        unsigned trace = auto9_vm3_center_rt_probe(s, head, footprint, shape);
        uint32_t result = auto9_g3d00_fragment_job(s, head, wb, &tiler, &stage, &renderer);
        if (trace) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 center RT0: n=%u footprint=%s fb44=0x%x flags=0x%x "
                "format=0x%x tiler=%u stage=%u status=0x%x\n",
                trace, footprint[0] ? footprint : "-", shape[0], shape[1],
                shape[2], shape[3], stage, result);
        }
        if (s->g3d00 && result != G3D00_JOB_DONE) {
            auto9_g3d00_fragment_fault_sample(s, head, stage, renderer);
        }
        if (tiler) {
            auto9_g3d00_consume_tiles(s, tiler);
        }
        if (result != G3D00_JOB_DONE) {
            return result;
        }
        smp_mb();
    } else if (type == 7 || indexed) {
        if (auto9_mali_tile_queue_full(s)) {
            auto9_vm3_tiler_stop_sample(s, head, slot, 0, G3D00_JOB_CONFIG_FAULT);
            return G3D00_JOB_CONFIG_FAULT;
        }
        Auto9G3d00TileBatch *batch = &s->tile_batch[s->tile_batch_count];
        memset(batch, 0, sizeof(*batch));
        unsigned stage = 0;
        uint32_t result = indexed ? auto9_vm3_indexed_job(s, head, wb, batch, &stage) :
                          s->vm3_control ? auto9_g3d1_vm3_fan_job(s, head, slot, batch, &stage)
                                         : auto9_g3d00_tile_job(s, head, slot, batch);
        if (indexed && s->vm3_chain_fault_traces < 256) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 indexed vertex: stage=%u status=0x%x dimensions=%ux%u bins=%u indices=%u\n",
                stage, result, batch->width, batch->height,
                result == G3D00_JOB_DONE ? batch->count : 0, batch->index_count);
        } else if (s->vm3_control && s->vm3_geometry_seen <= 4) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 G3D1 VM3 fan result: sample=%u stage=%u status=0x%x bins=%u\n",
                s->vm3_geometry_seen, stage, result, result == G3D00_JOB_DONE ? batch->count : 0);
        }
        if (result != G3D00_JOB_DONE) {
            if (!indexed) {
                auto9_vm3_tiler_stop_sample(s, head, slot, stage, result);
            }
            return result;
        }
        smp_mb();
    } else if (type == 5) {
        unsigned stage = 0;
        uint32_t result = auto9_g3d1_stock_viewport_job(s, head, slot, wb);
        if (result == G3D00_JOB_CONFIG_FAULT) {
            result = auto9_g3d00_viewport_job(s, head, slot, wb, &stage);
        }
        if (result == G3D00_JOB_CONFIG_FAULT && s->vm3_control) {
            result = auto9_vm3_vertex3_job(s, head, slot, wb);
        }
        if (result == G3D00_JOB_CONFIG_FAULT && s->vm3_control) {
            result = auto9_vm3_vertex448_job(s, head, slot, wb);
        }
        if (result != G3D00_JOB_DONE) {
            if (stage) {
                auto9_qnx_vertex_fault(s, head, slot, stage, result);
            }
            return result;
        }
        smp_mb();
    } else if (compute) {
        uint32_t result = auto9_vm3_compute_job(s, head, wb);
        if (result != G3D00_JOB_DONE) {
            return result;
        }
        smp_mb();
    } else if (type == 2) {
        if (!auto9_g3d00_dma_read(s, head + 32, payload, sizeof(payload))) {
            return G3D00_JOB_READ_FAULT;
        }
        mode = ldl_le_p(payload + 8);
        if (mode < 4 || mode > 7) { /* Immediate 8/16/32/64 only. */
            return G3D00_JOB_CONFIG_FAULT;
        }
        addr = ldq_le_p(payload);
        width = 1U << (mode - 4);
        if ((addr & (width - 1)) ||
            !auto9_g3d00_translate(s, addr, width, true, &dst) ||
            (dst < wb + sizeof(status) && wb < dst + width)) {
            return G3D00_JOB_WRITE_FAULT;
        }
        if (address_space_write(&address_space_memory, dst,
                                MEMTXATTRS_UNSPECIFIED, payload + 16,
                                width) != MEMTX_OK) {
            return G3D00_JOB_WRITE_FAULT;
        }
    } else if (type == 3) {
        if (!auto9_g3d00_dma_read(s, head + 32, payload, 8)) {
            return G3D00_JOB_READ_FAULT;
        }
        uint32_t unit = ldl_le_p(payload), l2 = ldl_le_p(payload + 4);
        if ((unit & ~0x03030007U) || (l2 & ~3U)) {
            return G3D00_JOB_CONFIG_FAULT;
        }
        flush = (unit | l2) != 0;
        if (flush) {
            smp_mb();
        }
    }
    stl_le_p(status, G3D00_JOB_DONE);
    if (address_space_write(&address_space_memory, wb,
                            MEMTXATTRS_UNSPECIFIED, status, sizeof(status)) != MEMTX_OK) {
        return compute ? auto9_vm3_compute_write_fault(s, "status-transaction", 0, 0) : G3D00_JOB_WRITE_FAULT;
    }
    if (flush) {
        s->job_cache_flushes++;
        s->latest_flush_id++;
    }
    if (type == 5) {
        s->vertex_descriptors_completed++;
    } else if (type == 7 || indexed) {
        if (indexed) {
            s->vertex_descriptors_completed++;
            const Auto9G3d00TileBatch *batch = &s->tile_batch[s->tile_batch_count];
            if (batch->count && qemu_loglevel_mask(LOG_GUEST_ERROR)) {
                unsigned shape = batch->index_count == 6 ? 0 : batch->index_count == 10 ? 1 :
                                 batch->index_count == 30 ? 2 : 3;
                unsigned bit = BIT(shape + (batch->height == 1024 ? 4 : 0));
                if (!(s->vm3_indexed_draw_profiles & bit)) {
                    s->vm3_indexed_draw_profiles |= bit;
                    auto9_g3d1_vm3_geometry_observe(s, head, type, slot, 0, false);
                }
            }
        } else if (s->vm3_control) {
            s->vm3_stock_fans++;
            s->vm3_stock_fan_primitives += s->tile_batch[s->tile_batch_count].count;
            const Auto9G3d00TileBatch *batch = &s->tile_batch[s->tile_batch_count];
            if (batch->store_point && ++s->vm3_points_binned <= 16) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 point binned: n=%u dimensions=%ux%u bin=%u/%u\n",
                    s->vm3_points_binned, batch->width, batch->height,
                    batch->point_x / 16, batch->point_y / 16);
            }
            if (batch->atlas_constant) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 constant strip: dimensions=%ux%u triangles=%u\n",
                    batch->width, batch->height, batch->count);
            } else if (batch->atlas_strip && ++s->vm3_atlas_strips <= 16) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 atlas strip: n=%u dimensions=%ux%u triangles=%u\n",
                    s->vm3_atlas_strips, batch->width, batch->height, batch->count);
            }
            if (s->tile_batch[s->tile_batch_count].interleaved_fan &&
                ++s->vm3_interleaved_fans <= 16) {
                qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 interleaved fan: n=%u triangles=%u\n",
                    s->vm3_interleaved_fans, s->tile_batch[s->tile_batch_count].count);
                if (s->vm3_interleaved_fans <= 2 && qemu_loglevel_mask(LOG_GUEST_ERROR)) {
                    auto9_g3d1_vm3_geometry_observe(s, head, 7, slot,
                                                   200 + s->vm3_interleaved_fans, false);
                }
            }
            /* The old index-14 stop has a valid zero-offset fan but an
             * independent blend program. Capture it even if geometry now
             * succeeds; fragment execution still needs its own checks. */
            if (batch->interleaved_fan && s->vm3_interleaved_fans <= 16 &&
                qemu_loglevel_mask(LOG_GUEST_ERROR)) {
                uint8_t descriptor[24];
                if (auto9_g3d00_dma_read(s, head, descriptor, sizeof(descriptor)) &&
                    (ldl_le_p(descriptor + 16) >> 16) == 14 &&
                    ldl_le_p(descriptor + 20) == 0x000c000d) {
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 VM3 index 14 geometry complete: bins=%u\n", batch->count);
                    auto9_g3d1_vm3_geometry_observe(s, head, 7, slot, 0, false);
                }
            }
            if (batch->tagged_interleaved && s->vm3_interleaved_fans <= 16) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 tagged interleaved fan: bins=%u dimensions=%ux%u\n",
                    batch->count, batch->width, batch->height);
            }
            if (batch->dual_uv && !s->vm3_dual_uv_sampled && qemu_loglevel_mask(LOG_GUEST_ERROR)) {
                s->vm3_dual_uv_sampled = true;
                qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 dual-UV geometry: triangles=%u\n", batch->count);
                auto9_g3d1_vm3_geometry_observe(s, head, 7, slot, 0, false);
            }
            if (batch->triangle_list && qemu_loglevel_mask(LOG_GUEST_ERROR)) {
                uint8_t descriptor[40];
                if (s->vm3_short_lists_sampled != 3 &&
                    auto9_g3d00_dma_read(s, head + 16, descriptor, sizeof(descriptor))) {
                    unsigned last = ldl_le_p(descriptor + 36);
                    unsigned bit = last == 17 ? BIT(0) : last == 29 ? BIT(1) : 0;
                    if (bit && !(s->vm3_short_lists_sampled & bit)) {
                        s->vm3_short_lists_sampled |= bit;
                        qemu_log_mask(LOG_GUEST_ERROR,
                            "Auto9 VM3 short list complete: index=%u vertices=%u triangles=%u\n",
                            ldl_le_p(descriptor) >> 16, last + 1, batch->count);
                    }
                }
                if (!s->vm3_triangle_list_sampled) {
                    s->vm3_triangle_list_sampled = true;
                    auto9_g3d1_vm3_geometry_observe(s, head, 7, slot, 210, false);
                }
            }
            if (s->tile_batch[s->tile_batch_count].color_clipped && ++s->vm3_color_clipped_fans <= 16) {
                qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 clipped fan: n=%u triangles=%u\n",
                    s->vm3_color_clipped_fans, s->tile_batch[s->tile_batch_count].count);
            }
            if (s->tile_batch[s->tile_batch_count].color_fan && ++s->vm3_color_fans <= 16) {
                qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 color fan: n=%u triangles=%u\n",
                    s->vm3_color_fans, s->tile_batch[s->tile_batch_count].count);
            }
        }
        s->primitives_binned += s->tile_batch[s->tile_batch_count++].count;
        s->tiler_descriptors_completed++;
    } else if (type == 9) {
        s->fragment_descriptors_completed++;
    }
    s->job_descriptors_completed++;
    return G3D00_JOB_DONE;
}

/* Verify the pinned driver's point program's six DMA destinations without
 * accessing their contents or reporting the underlying guest addresses. */
static void auto9_vm3_point_store_map(Auto9MaliG3d1 *s, uint64_t head,
                                    const uint8_t job[48], const uint8_t draw[104],
                                    const uint8_t renderer[80], const uint8_t *code,
                                    unsigned extent, unsigned sample)
{
    if (ldl_le_p(job + 8) != 1 || ldl_le_p(job + 20) || ldl_le_p(draw) != 0x803 ||
        ldl_le_p(job + 32) != 0x3f800000 || ldl_le_p(job + 36) ||
        ldl_le_p(renderer + 16) != 0x08602201 || extent != 240) {
        return;
    }
    g_autofree char *digest = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, extent);
    if (!digest || strcmp(digest, "436ead842d971f1718ac5575fdae3d8f075d752f54f8147b43f70a0ef3056bdd")) {
        return;
    }
    const uint64_t u = ldq_le_p(draw + 48);
    uint8_t arg6[8], arg17[12];
    if (!u || (u & 7) || u >= (UINT64_C(1) << 48) - 152 ||
        !auto9_g3d00_dma_read(s, u + 48, arg6, sizeof(arg6)) ||
        !auto9_g3d00_dma_read(s, u + 136, arg17, sizeof(arg17))) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 point stores: sample=%u operands-unreadable\n", sample);
        return;
    }
    const uint64_t a = ldq_le_p(arg6);
    /* u17.w1 is a reciprocal input, not the high half of address b. */
    const uint64_t b = (uint64_t)ldl_le_p(arg17 + 8) << 32 | ldl_le_p(arg17);
    /* Compare translations only, never use a truncated candidate for a
     * transaction. The original program may pack non-address high bits. */
    unsigned readable_candidates[2] = {0}, writable_candidates[2] = {0};
    const uint64_t base[] = {a, b};
    const unsigned off[] = {16, 0x58}, length[] = {12, 4};
    const unsigned widths[] = {32, 36, 40, 48};
    for (unsigned i = 0; i < G_N_ELEMENTS(base); i++) {
        for (unsigned k = 0; k < G_N_ELEMENTS(widths); k++) {
            const uint64_t low = base[i] & ((UINT64_C(1) << widths[k]) - 1);
            uint64_t pa;
            if ((uint32_t)low > UINT32_MAX - off[i] - length[i]) {
                continue;
            }
            if (low && auto9_g3d00_translate(s, low + off[i], length[i], false, &pa)) {
                readable_candidates[i] |= BIT(k);
            }
            if (low && auto9_g3d00_translate(s, low + off[i], length[i], true, &pa)) {
                writable_candidates[i] |= BIT(k);
            }
        }
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 point address forms: sample=%u a-upper16=%u b-upper16=%u "
        "a-wrap=%u b-wrap=%u a-read=0x%x a-write=0x%x b-read=0x%x b-write=0x%x\n",
        sample, (unsigned)!!(a >> 48), (unsigned)!!(b >> 48),
        (unsigned)((uint32_t)a > UINT32_MAX - 28),
        (unsigned)((uint32_t)b > UINT32_MAX - 116),
        readable_candidates[0], writable_candidates[0],
        readable_candidates[1], writable_candidates[1]);

    if ((uint32_t)a > UINT32_MAX - 28 || (uint32_t)b > UINT32_MAX - 116 ||
        (a | b) >= (UINT64_C(1) << 48)) {
        qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 point stores: sample=%u operands-outside-profile\n", sample);
        return;
    }

    const uint64_t dst[] = {a + 16, b + 0x60, b + 0x68, b + 0x58, b + 0x70, a};
    const unsigned len[] = {12, 4, 4, 4, 4, 16};
    const uint64_t source[] = {head, ldq_le_p(draw + 56), ldq_le_p(renderer),
                               u + 48, u + 72, u + 120, ldq_le_p(draw + 16)};
    const unsigned source_len[] = {232, 80, 240, 8, 8, 32, 16};
    Auto9Vm3ClearSpan in[14], out[12];
    unsigned reads = 0, writes = 0;
    unsigned read_mask = 0, write_mask = 0, pair_alias = 0, input_alias = 0;
    bool input_complete = true;
    for (unsigned i = 0; i < G_N_ELEMENTS(source); i++) {
        unsigned n = reads;
        if (auto9_vm3_clear_plan(s, source[i], 1, 0, source_len[i], false,
                                 in, G_N_ELEMENTS(in), &n)) {
            reads = n;
        } else {
            input_complete = false;
        }
    }
    for (unsigned i = 0; i < G_N_ELEMENTS(dst); i++) {
        unsigned n = writes;
        if (auto9_vm3_clear_plan(s, dst[i], 1, 0, len[i], true,
                                 out, G_N_ELEMENTS(out), &n)) {
            write_mask |= BIT(i);
            for (unsigned j = writes; j < n; j++) {
                for (unsigned k = 0; k < writes; k++) {
                    if (auto9_g3d00_overlap(out[j].pa, out[j].length, out[k].pa, out[k].length)) {
                        pair_alias |= BIT(i);
                    }
                }
                for (unsigned k = 0; k < reads; k++) {
                    if (auto9_g3d00_overlap(out[j].pa, out[j].length, in[k].pa, in[k].length)) {
                        input_alias |= BIT(i);
                    }
                }
            }
            writes = n;
        }
        Auto9Vm3ClearSpan probe[2];
        n = 0;
        if (auto9_vm3_clear_plan(s, dst[i], 1, 0, len[i], false, probe, G_N_ELEMENTS(probe), &n)) {
            read_mask |= BIT(i);
        }
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 point stores: sample=%u read=0x%x write=0x%x pair-alias=0x%x input-alias=0x%x "
        "input-complete=%u same-base=%u\n", sample, read_mask, write_mask, pair_alias,
        input_alias, (unsigned)input_complete, (unsigned)(a == b));
}

static void auto9_vm3_blend_probe(Auto9MaliG3d1 *s, const uint8_t renderer[80], unsigned sample)
{
    uint8_t code[512];
    uint64_t pc = auto9_vm3_blend_code(renderer);
    unsigned stop = 0, reason = 0, extent = 0;
    bool readable = pc && auto9_g3d00_dma_read(s, pc, code, sizeof(code));
    if (readable) {
        extent = auto9_g3d1_bifrost_extent(code, sizeof(code), &stop, &reason);
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 blend program: sample=%u readable=%u return=%u extent=%u stop=%u reason=%u\n",
        sample, (unsigned)readable, (unsigned)!!(ldl_le_p(renderer + 72) >> 3),
        extent, stop, reason);
    if (readable && !reason) {
        for (unsigned off = 0; off < extent; off += 128) {
            auto9_g3d00_sample_bytes("vm3-blend-program", sample, "framed-program",
                                     off, code + off, MIN(extent - off, 128));
        }
    }
}

/* Distinguish the first downstream draw without capturing guest textures,
 * uniforms, pixels or any of their addresses. Geometry is not executed. */
static void auto9_g3d1_vm3_geometry_observe(Auto9MaliG3d1 *s, uint64_t head,
                                             unsigned type, unsigned slot,
                                             unsigned sample, bool stopped)
{
    uint8_t job[48], draw[104], renderer[80], code[512];
    if (!auto9_g3d00_dma_read(s, head + 32, job, sizeof(job)) ||
        !auto9_g3d00_dma_read(s, head + 128, draw, sizeof(draw))) {
        return;
    }
    if (!stopped) {
        sample = ++s->vm3_geometry_samples;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 geometry: sample=%u type=%u slot=%u inv0=0x%x inv4=0x%x "
        "primitive=0x%x count=%u draw=0x%x tiler=%u viewport=%u position=%u\n",
        sample, type, slot, ldl_le_p(job), ldl_le_p(job + 4), ldl_le_p(job + 8),
        ldl_le_p(job + 20), ldl_le_p(draw), (unsigned)!!ldq_le_p(job + 40),
        (unsigned)!!ldq_le_p(draw + 96), (unsigned)!!ldq_le_p(draw + 16));
    uint8_t ctx[32], viewport[32], attr[8], buffer[16], texture[32], sampler[32];
    uint64_t ct = ldq_le_p(job + 40), vp = ldq_le_p(draw + 96);
    uint64_t at = ldq_le_p(draw + 88), vb = ldq_le_p(draw + 80);
    if (stopped) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 stopped tiler shape: sample=%u primitive12=0x%x "
            "primitive16=0x%x indices=%u draw4=0x%x draw8=0x%x\n",
            sample, ldl_le_p(job + 12), ldl_le_p(job + 16),
            (unsigned)!!ldq_le_p(job + 24), ldl_le_p(draw + 4), ldl_le_p(draw + 8));
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 stopped tiler pointers: sample=%u position=%u state=%u viewport=%u "
            "varying=%u buffer=%u push=%u\n", sample,
            (unsigned)!!ldq_le_p(draw + 16), (unsigned)!!ldq_le_p(draw + 56),
            (unsigned)!!vp, (unsigned)!!at, (unsigned)!!vb, (unsigned)!!ldq_le_p(draw + 48));
        if (vp && auto9_g3d00_dma_read(s, vp, viewport, sizeof(viewport))) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 stopped tiler viewport: sample=%u low=0x%x/0x%x high=0x%x/0x%x "
                "depth=0x%x/0x%x clip=0x%x/0x%x\n",
                sample, ldl_le_p(viewport), ldl_le_p(viewport + 4), ldl_le_p(viewport + 8),
                ldl_le_p(viewport + 12), ldl_le_p(viewport + 16), ldl_le_p(viewport + 20),
                ldl_le_p(viewport + 24), ldl_le_p(viewport + 28));
        }
        uint64_t pos = ldq_le_p(draw + 16);
        if (!at && !vb && pos && !(pos & 15) && ldl_le_p(job + 8) == 1 &&
            ldl_le_p(job + 20) == 0 && ldl_le_p(draw) == 0x803) {
            uint8_t xyzw[16];
            bool valid = auto9_g3d00_dma_read(s, pos, xyzw, sizeof(xyzw));
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 stopped point: sample=%u size-word=0x%x reserved=0x%x "
                "position-valid=%u xyzw=0x%x/0x%x/0x%x/0x%x\n",
                sample, ldl_le_p(job + 32), ldl_le_p(job + 36), (unsigned)valid,
                valid ? ldl_le_p(xyzw) : 0, valid ? ldl_le_p(xyzw + 4) : 0,
                valid ? ldl_le_p(xyzw + 8) : 0, valid ? ldl_le_p(xyzw + 12) : 0);
        }
        if (!at && !vb && pos && !(pos & 15) && ldl_le_p(job + 8) == 0x1818000a) {
            uint8_t values[64];
            if (auto9_g3d00_dma_read(s, pos, values, sizeof(values))) {
                for (unsigned i = 0; i < 4; i++) {
                    const uint8_t *v = values + 16 * i;
                    qemu_log_mask(LOG_GUEST_ERROR,
                        "Auto9 VM3 constant strip position: sample=%u vertex=%u xyzw=0x%x/0x%x/0x%x/0x%x\n",
                        sample, i, ldl_le_p(v), ldl_le_p(v + 4),
                        ldl_le_p(v + 8), ldl_le_p(v + 12));
                }
            }
        }
    }
    if (ct && vp && at && vb && auto9_g3d00_dma_read(s, ct, ctx, sizeof(ctx)) &&
        auto9_g3d00_dma_read(s, vp, viewport, sizeof(viewport)) &&
        auto9_g3d00_dma_read(s, at, attr, sizeof(attr)) &&
        auto9_g3d00_dma_read(s, vb, buffer, sizeof(buffer))) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 G3D1 VM3 fan layout: sample=%u ctx8=0x%x wh=0x%x vpmin=0x%x vpmax=0x%x "
            "var0=0x%x offset=%d buffer-type=%u stride=%u bytes=%u\n",
            sample, ldl_le_p(ctx + 8), ldl_le_p(ctx + 12), ldl_le_p(viewport + 24),
            ldl_le_p(viewport + 28), ldl_le_p(attr), ldl_le_p(attr + 4),
            (unsigned)(ldq_le_p(buffer) & 63), ldl_le_p(buffer + 8), ldl_le_p(buffer + 12));
    }
    uint64_t state = ldq_le_p(draw + 56);
    if (!state || (state & 63) || !auto9_g3d00_dma_read(s, state, renderer, sizeof(renderer))) {
        return;
    }
    if (stopped && at && vb && (at | vb) < (UINT64_C(1) << 48)) {
        unsigned count = MIN(4, lduw_le_p(renderer + 14));
        for (unsigned i = 0; i < count; i++) {
            uint8_t a[8], b[16];
            if (!auto9_g3d00_dma_read(s, at + 8 * i, a, sizeof(a))) {
                break;
            }
            uint32_t word = ldl_le_p(a), index = word & 511;
            if (index >= 8 || !auto9_g3d00_dma_read(s, vb + 16 * index, b, sizeof(b))) {
                break;
            }
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 stopped tiler varying: sample=%u i=%u a=0x%x offset=%d "
                "type=%u mode=%u stride=%u size=%u\n",
                sample, i, word, (int32_t)ldl_le_p(a + 4), (unsigned)(ldq_le_p(b) & 63),
                (unsigned)(ldq_le_p(b) >> 56), ldl_le_p(b + 8), ldl_le_p(b + 12));
        }
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 geometry renderer: sample=%u rsd8=0x%x rsd12=0x%x "
        "rsd16=0x%x rsd32=0x%x rsd36=0x%x rsd48=0x%x\n",
        sample, ldl_le_p(renderer + 8), ldl_le_p(renderer + 12), ldl_le_p(renderer + 16),
        ldl_le_p(renderer + 32), ldl_le_p(renderer + 36), ldl_le_p(renderer + 48));
    unsigned blend_mode = ldl_le_p(renderer + 72) & 3;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 fragment state: sample=%u message=0x%x blend-flags=0x%x "
        "blend-eq=0x%x blend-mode=%u conversion=0x%x push=%u\n",
        sample, ldl_le_p(renderer + 60), ldl_le_p(renderer + 64),
        ldl_le_p(renderer + 68), blend_mode, blend_mode == 0 ? 0 : ldl_le_p(renderer + 76),
        (unsigned)!!ldq_le_p(draw + 48));
    if (!blend_mode && type == 7 && ldl_le_p(job + 8) == 0x1803000c &&
        ldl_le_p(renderer + 16) == 0x49502a00 && ldl_le_p(renderer + 32) == 0x3710ffff &&
        ldl_le_p(renderer + 60) == 0x1a114202 && ldl_le_p(renderer + 64) == 0x201) {
        auto9_vm3_blend_probe(s, renderer, sample);
    }
    uint64_t tex = ldq_le_p(draw + 32), samp = ldq_le_p(draw + 40);
    if (tex && samp && ldl_le_p(renderer + 8) == 0x20002 &&
        ldl_le_p(renderer + 12) == 0x30000 && ldl_le_p(renderer + 60) == 0x02120282 &&
        auto9_g3d00_dma_read(s, tex + 32, texture, sizeof(texture)) &&
        auto9_g3d00_dma_read(s, samp + 32, sampler, sizeof(sampler))) {
        uint8_t surface[16];
        uint64_t va = ldq_le_p(texture + 16);
        if (va && !(va & 7) && auto9_g3d00_dma_read(s, va, surface, sizeof(surface))) {
            qemu_log_mask(LOG_GUEST_ERROR,
                "Auto9 VM3 dual-UV second texture: n=%u tex=0x%x/0x%x/0x%x/0x%x/0x%x/0x%x "
                "sampler=0x%x/0x%x/0x%x tag=0x%x row=%u slice=%u\n",
                sample, ldl_le_p(texture), ldl_le_p(texture + 4), ldl_le_p(texture + 8),
                ldl_le_p(texture + 12), ldl_le_p(texture + 24), ldl_le_p(texture + 28),
                ldl_le_p(sampler), ldl_le_p(sampler + 4), ldl_le_p(sampler + 8),
                (unsigned)(ldq_le_p(surface) & 4095), ldl_le_p(surface + 8), ldl_le_p(surface + 12));
        }
    }
    if (tex && samp && auto9_g3d00_dma_read(s, tex, texture, sizeof(texture)) &&
        auto9_g3d00_dma_read(s, samp, sampler, sizeof(sampler))) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 G3D1 VM3 texture state: sample=%u tex0=0x%x tex1=0x%x tex2=0x%x "
            "tex3=0x%x tex6=0x%x tex7=0x%x sampler0=0x%x sampler1=0x%x\n",
            sample, ldl_le_p(texture), ldl_le_p(texture + 4), ldl_le_p(texture + 8),
            ldl_le_p(texture + 12), ldl_le_p(texture + 24), ldl_le_p(texture + 28),
            ldl_le_p(sampler), ldl_le_p(sampler + 4));
        uint32_t format = ldl_le_p(texture + 8), wh = ldl_le_p(texture + 4);
        if ((format == 0xc688 && (wh == 0x07ff07ff || wh == 0x03ff07ff)) ||
            ((format == 0x2688 || format == 0x1688) && wh == 0x03ff07ff)) {
            uint8_t surface[16];
            uint64_t va = ldq_le_p(texture + 16);
            if (va && !(va & 7) && auto9_g3d00_dma_read(s, va, surface, sizeof(surface))) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 interleaved %s tiler surface: n=%u flags=0x%x row=%u slice=%u filter-extra=0x%x\n",
                    format == 0xc688 ? "AFBC" : format == 0x1688 ? "tiled" : "linear",
                    sample, (unsigned)(ldq_le_p(surface) & 4095),
                    ldl_le_p(surface + 8), ldl_le_p(surface + 12), ldl_le_p(sampler + 8));
            }
        }
    }
    uint64_t shader = ldq_le_p(renderer);
    unsigned cap = MIN(sizeof(code), 4096 - (shader & 4095));
    if (!shader || (shader & 15) || !auto9_g3d00_dma_read(s, shader, code, cap)) {
        return;
    }
    unsigned stop = 0, reason = 0;
    unsigned extent = auto9_g3d1_bifrost_extent(code, cap, &stop, &reason);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 G3D1 VM3 geometry shader: sample=%u extent=%u stop=%u reason=%u\n",
        sample, extent, stop, reason);
    if (stopped && !reason) {
        auto9_vm3_point_store_map(s, head, job, draw, renderer, code, extent, sample);
    }
    for (unsigned off = 0; off < extent; off += 128) {
        auto9_g3d00_sample_bytes("vm3-geometry", sample, "framed-program", off,
                                 code + off, MIN(extent - off, 128));
    }
}

static void auto9_g3d1_vm3_geometry_sample(Auto9MaliG3d1 *s, uint64_t head,
                                            unsigned type, unsigned slot)
{
    if (++s->vm3_geometry_seen <= 4 && qemu_loglevel_mask(LOG_GUEST_ERROR) &&
        (type == 7 || type == 10)) {
        auto9_g3d1_vm3_geometry_observe(s, head, type, slot, 0, false);
    }
}

static void auto9_vm3_tiler_stop_sample(Auto9MaliG3d1 *s, uint64_t head,
                                        unsigned slot, unsigned stage, uint32_t result)
{
    uint8_t header[32];
    if (!s->vm3_control || slot != 1 || s->vm3_tiler_stop_samples >= 12 ||
        s->vm3_chain_fault_traces >= 256 ||
        !qemu_loglevel_mask(LOG_GUEST_ERROR) ||
        !auto9_g3d00_dma_read(s, head, header, sizeof(header))) {
        return;
    }
    uint32_t word = ldl_le_p(header + 16), deps = ldl_le_p(header + 20);
    /* Reserve one observation for each recurring descriptor/dependency tuple. */
    unsigned index = word >> 16;
    unsigned new_bit = index == 37 && deps == 0x00240023 ? BIT(0) :
                       index == 21 && deps == 0x00140013 ? BIT(1) :
                       index == 17 && deps == 0x000f0010 ? BIT(2) :
                       index == 23 && deps == 0x00150016 ? BIT(3) : 0;
    if (new_bit) {
        if (s->vm3_new_tiler_stops & new_bit) {
            return;
        }
        s->vm3_new_tiler_stops |= new_bit;
    } else if (s->vm3_tiler_stop_samples - ctpop32(s->vm3_new_tiler_stops) >= 8 ||
               !((index == 1 && !deps) || (index == 2 && (deps & 0xffff) == 1) ||
                 (index == 3 && deps == 0x00010002) || (index == 4 && deps == 0x00020003) ||
                 (index == 7 && deps == 0x00050006) || (index == 9 && deps == 0x00070008) ||
                 (index == 12 && deps == 0x0009000b) || (index == 14 && deps == 0x000c000d) ||
                 (index == 18 && deps == 0x00100011) || ((index == 36 || index == 87) && !deps))) {
        return;
    }
    unsigned sample = 100 + ++s->vm3_tiler_stop_samples;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 stopped tiler: sample=%u stage=%u status=0x%x index=%u dep1=%u dep2=%u\n",
        sample, stage, result, index, deps & 0xffff, deps >> 16);
    auto9_g3d1_vm3_geometry_observe(s, head, 7, slot, sample, true);
}

/* Read only layout metadata for the pinned original vertex programs. */
static void auto9_vm3_vertex_layout(Auto9MaliG3d1 *s, const uint8_t draw[96],
                                    unsigned fault, unsigned profile)
{
    uint32_t single_sample = 0;
    uint32_t *samples = profile == 3 ? &s->vm3_vertex3_layout_samples :
                        profile == 4 ? &s->vm3_vertex4_layout_samples :
                        profile == 42 ? &single_sample :
                        profile == 10 ? &s->vm3_indexed_layout_samples :
                        profile == 336 ? &s->vm3_vertex336_layout_samples :
                        profile == 45 ? &s->vm3_vertex45_layout_samples :
                                       &s->vm3_vertex448_layout_samples;
    const char *label = profile == 3 ? "vertex3" : profile == 4 ? "vertex4" :
                        (profile == 10 || profile == 42) ? "indexed-vertex" :
                        profile == 336 ? "vertex336" :
                        profile == 45 ? "vertex45" : "vertex448";
    if (*samples >= 8) {
        return;
    }
    unsigned sample = ++*samples;
    if (profile == 336) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 vertex336 layout: sample=%u fault=%u stage=%u draw4=0x%x draw8=0x%x pos=%u\n",
            sample, fault, s->vm3_vertex336_stage, ldl_le_p(draw + 4), ldl_le_p(draw + 8),
            (unsigned)!!ldq_le_p(draw + 16));
    } else {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 %s layout: sample=%u fault=%u draw4=0x%x draw8=0x%x draw16=0x%x draw20=0x%x\n",
            label, sample, fault, ldl_le_p(draw + 4), ldl_le_p(draw + 8), ldl_le_p(draw + 16),
            ldl_le_p(draw + 20));
    }
    for (unsigned varying = 0; varying < 2; varying++) {
        uint64_t at = ldq_le_p(draw + (varying ? 88 : 72));
        uint64_t bt = ldq_le_p(draw + (varying ? 80 : 64));
        unsigned count = profile == 336 ? 2 : profile == 45 ? (varying ? 5 : 4) :
                         (varying ? 3 : 2) + (unsigned)(profile != 3);
        for (unsigned i = 0; i < count; i++) {
            uint8_t a[8], b[16];
            if (!at || at >= (UINT64_C(1) << 48) ||
                !auto9_g3d00_dma_read(s, at + 8 * i, a, sizeof(a))) {
                break;
            }
            uint32_t aw = ldl_le_p(a), index = aw & 511;
            if (bt && bt < (UINT64_C(1) << 48) && index < 8 &&
                auto9_g3d00_dma_read(s, bt + 16 * index, b, sizeof(b))) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 %s entry: sample=%u varying=%u i=%u a=0x%x offset=%d "
                    "type=%u mode=%u stride=%u size=%u\n",
                    label, sample, varying, i, aw, (int32_t)ldl_le_p(a + 4),
                    (unsigned)(ldq_le_p(b) & 63), (unsigned)(ldq_le_p(b) >> 56),
                    ldl_le_p(b + 8), ldl_le_p(b + 12));
            } else {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 %s entry: sample=%u varying=%u i=%u a=0x%x offset=%d no-buffer\n",
                    label, sample, varying, i, aw, (int32_t)ldl_le_p(a + 4));
            }
        }
    }
}

/* For scoped captures, read one aligned quadword at a time through the
 * private GPU DMA path; stop at the first framed END, fault, or 4 KiB. */
static unsigned auto9_vm3_vertex4_extent(Auto9MaliG3d1 *s, uint64_t program,
                                         uint8_t code[4096], unsigned *read,
                                         unsigned *stop, unsigned *reason)
{
    *read = 64;
    unsigned extent = auto9_g3d1_bifrost_extent(code, *read, stop, reason);
    while (!extent && *reason == 6 && *read < 4096) {
        if (!auto9_g3d00_dma_read(s, program + *read, code + *read, 16)) {
            *reason = 7; /* Private DMA read failed before END. */
            break;
        }
        *read += 16;
        extent = auto9_g3d1_bifrost_extent(code, *read, stop, reason);
    }
    return extent;
}

static void auto9_vm3_vertex4_program(Auto9MaliG3d1 *s, uint64_t program,
                                      const uint8_t prefix[64], unsigned fault, bool vertex45)
{
    bool *captured = vertex45 ? &s->vm3_vertex45_program_captured : &s->vm3_vertex4_program_captured;
    uint32_t *attempts = vertex45 ? &s->vm3_vertex45_program_attempts : &s->vm3_vertex4_program_attempts;
    const char *label = vertex45 ? "vertex45" : "vertex4";
    if (*captured || *attempts >= 3) {
        return;
    }
    uint8_t code[4096];
    unsigned read, stop, reason;
    memcpy(code, prefix, 64);
    unsigned extent = auto9_vm3_vertex4_extent(s, program, code, &read, &stop, &reason);
    unsigned attempt = ++*attempts;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 %s framing: fault=%u attempt=%u read=%u extent=%u stop=%u reason=%u\n",
        label, fault, attempt, read, extent, stop, reason);
    if (extent) {
        *captured = true;
        for (unsigned off = 0; off < extent; off += 128) {
            auto9_g3d00_sample_bytes(vertex45 ? "vm3-vertex45" : "vm3-vertex4", attempt, "framed-program",
                                     off, code + off, MIN(extent - off, 128));
        }
    }
}

/* Read only the type-4 descriptor that actually reached a stop. A snapshot
 * of binding metadata and delimited instructions is not execution. */
static void auto9_vm3_failed_compute(Auto9MaliG3d1 *s, uint64_t head, unsigned fault)
{
    uint8_t payload[160], rsd[64], code[4096];
    if (s->vm3_compute_samples >= 12 ||
        !auto9_g3d00_dma_read(s, head + 32, payload, sizeof(payload))) {
        return;
    }
    const uint8_t *draw = payload + 32;
    uint64_t renderer = ldq_le_p(draw + 56);
    unsigned sample = ++s->vm3_compute_samples;
    unsigned ptrs = 0;
    for (unsigned off = 16; off <= 112; off += 8) {
        ptrs |= (unsigned)!!ldq_le_p(draw + off) << ((off - 16) / 8);
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 compute stop: sample=%u fault=%u inv0=0x%x inv4=0x%x task=0x%x "
        "draw0=0x%x draw4=0x%x draw8=0x%x pointer-mask=0x%x\n",
        sample, fault, ldl_le_p(payload), ldl_le_p(payload + 4), ldl_le_p(payload + 8),
        ldl_le_p(draw), ldl_le_p(draw + 4), ldl_le_p(draw + 8), ptrs);
    if (!renderer || (renderer & 63) || !auto9_g3d00_dma_read(s, renderer, rsd, sizeof(rsd))) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 compute state: sample=%u resources=0x%x layout=0x%x properties=0x%x preload=0x%x\n",
        sample, ldl_le_p(rsd + 8), ldl_le_p(rsd + 12), ldl_le_p(rsd + 16), ldl_le_p(rsd + 48));
    for (unsigned v = 0; v < 2; v++) {
        uint64_t attr = ldq_le_p(draw + (v ? 88 : 72));
        uint64_t buffers = ldq_le_p(draw + (v ? 80 : 64));
        unsigned count = MIN(8, lduw_le_p(rsd + 12 + 2 * v));
        if (!attr || (attr | buffers) >= (UINT64_C(1) << 48)) {
            continue;
        }
        for (unsigned i = 0; i < count; i++) {
            uint8_t entry[8], buf[16];
            if (!auto9_g3d00_dma_read(s, attr + 8 * i, entry, sizeof(entry))) {
                break;
            }
            uint32_t word = ldl_le_p(entry);
            unsigned index = word & 511;
            if (buffers && index < 8 &&
                auto9_g3d00_dma_read(s, buffers + 16 * index, buf, sizeof(buf))) {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 compute binding: sample=%u varying=%u i=%u a=0x%x offset=%d "
                    "type=%u mode=%u stride=%u size=%u\n",
                    sample, v, i, word, (int32_t)ldl_le_p(entry + 4),
                    (unsigned)(ldq_le_p(buf) & 63), (unsigned)(ldq_le_p(buf) >> 56),
                    ldl_le_p(buf + 8), ldl_le_p(buf + 12));
            } else {
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 compute binding: sample=%u varying=%u i=%u a=0x%x offset=%d no-buffer\n",
                    sample, v, i, word, (int32_t)ldl_le_p(entry + 4));
            }
        }
    }
    uint64_t program = ldq_le_p(rsd);
    if (!program || (program & 15) || !auto9_g3d00_dma_read(s, program, code, 64)) {
        return;
    }
    g_autofree char *prefix = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, 64);
    if (!prefix) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR, "Auto9 VM3 compute prefix: sample=%u sha64=%s\n", sample, prefix);
    for (unsigned i = 0; i < s->vm3_compute_programs; i++) {
        if (!strcmp(prefix, s->vm3_compute_prefixes[i])) {
            return;
        }
    }
    if (s->vm3_compute_programs == G_N_ELEMENTS(s->vm3_compute_prefixes)) {
        return;
    }
    unsigned read, stop, reason;
    unsigned extent = auto9_vm3_vertex4_extent(s, program, code, &read, &stop, &reason);
    memcpy(s->vm3_compute_prefixes[s->vm3_compute_programs++], prefix, 65);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 compute framing: sample=%u read=%u extent=%u stop=%u reason=%u\n",
        sample, read, extent, stop, reason);
    for (unsigned off = 0; off < extent; off += 128) {
        auto9_g3d00_sample_bytes("vm3-compute-stop", sample, "framed-program",
                                 off, code + off, MIN(extent - off, 128));
    }
}

/* Independently sample rejected vertex work, not only the first 24
 * submissions (which were typically successful Weston shaders). */
static void auto9_vm3_failed_vertex(Auto9MaliG3d1 *s, uint64_t head,
                                    unsigned draw_offset, unsigned fault)
{
    uint8_t inv[8], draw[96], rsd[64], code[512];
    if (!auto9_g3d00_dma_read(s, head + 32, inv, sizeof(inv)) ||
        !auto9_g3d00_dma_read(s, head + draw_offset, draw, sizeof(draw))) {
        return;
    }
    uint64_t state = ldq_le_p(draw + 56);
    if (!state || (state & 63) || !auto9_g3d00_dma_read(s, state, rsd, sizeof(rsd))) {
        return;
    }
    uint64_t program = ldq_le_p(rsd);
    if (!program || (program & 15)) {
        return;
    }
    if (!auto9_g3d00_dma_read(s, program, code, 64)) {
        return;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, 64);
    if (!sha) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 rejected vertex: fault=%u inv0=0x%x inv4=0x%x draw=0x%x "
        "rsd8=0x%x attr=%u vary=%u fau=%u rsd48=0x%x shader64=%s\n",
        fault, ldl_le_p(inv), ldl_le_p(inv + 4), ldl_le_p(draw), ldl_le_p(rsd + 8),
        lduw_le_p(rsd + 12), lduw_le_p(rsd + 14), (unsigned)!!ldq_le_p(draw + 48),
        ldl_le_p(rsd + 48), sha);
    if (!strcmp(sha, "988f96111feb69808a24e7c2ccf2653917d2ac9a91273651e1271d86ee2667ce")) {
        auto9_vm3_vertex_layout(s, draw, fault, 3);
    }
    if (lduw_le_p(rsd + 12) == 2 && lduw_le_p(rsd + 14) == 2 && ldl_le_p(rsd + 48) == 0x76000 &&
        !strcmp(sha, "62a6124a87f441f2b2db955321aa278f89e576a6d1ba290052526c77a6c63486")) {
        auto9_vm3_vertex_layout(s, draw, fault, 336);
    }
    if (lduw_le_p(rsd + 12) == 3 && lduw_le_p(rsd + 14) == 4 &&
        ldl_le_p(rsd + 48) == 0xc6000 &&
        !strcmp(sha, "9dadf27f21adb9afa1f25d398d8163f2f2ec2297c14d44f94fb11ec3fdccbf1d")) {
        auto9_vm3_vertex_layout(s, draw, fault, 4);
        auto9_vm3_vertex4_program(s, program, code, fault, false);
        return;
    }
    /* The post-150 OEM job exceeds the generic 512 B same-page probe.
     * Follow its exact observed prefix via bounded private instruction DMA. */
    if (lduw_le_p(rsd + 12) == 4 && lduw_le_p(rsd + 14) == 5 &&
        !strcmp(sha, "d75826ab564f009ea16d759efa11fd78c0069cdbcb61aaa7c4214dca984fbb83")) {
        auto9_vm3_vertex_layout(s, draw, fault, 45);
        auto9_vm3_vertex4_program(s, program, code, fault, true);
        return;
    }
    if (lduw_le_p(rsd + 12) == 3 && lduw_le_p(rsd + 14) == 4 &&
        ldl_le_p(rsd + 48) == 0x6e000 &&
        !strcmp(sha, "23b6a95d4cff188feded578c4750d56b6a8deed16542ee2eb66c2c99660da0d3")) {
        auto9_vm3_vertex_layout(s, draw, fault, 448);
    }
    if (s->vm3_failed_vertex_samples >= G_N_ELEMENTS(s->vm3_failed_vertex_fingerprints)) {
        return;
    }
    for (unsigned i = 0; i < s->vm3_failed_vertex_samples; i++) {
        if (!strcmp(sha, s->vm3_failed_vertex_fingerprints[i])) {
            return;
        }
    }
    unsigned cap = MIN(sizeof(code), 4096 - (program & 4095));
    if (cap <= 64 || !auto9_g3d00_dma_read(s, program + 64, code + 64, cap - 64)) {
        cap = 64;
    }
    unsigned stop, reason;
    unsigned extent = auto9_g3d1_bifrost_extent(code, cap, &stop, &reason);
    unsigned sample = s->vm3_failed_vertex_samples++;
    memcpy(s->vm3_failed_vertex_fingerprints[sample], sha, 65);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 rejected vertex program: fault=%u sample=%u extent=%u reason=%u\n",
        fault, sample + 1, extent, reason);
    for (unsigned off = 0; off < extent; off += 128) {
        auto9_g3d00_sample_bytes("vm3-failed-vertex", sample + 1, "framed-program",
                                 off, code + off, MIN(extent - off, 128));
    }
}

/* The OEM position shader in this type-10 profile is known; the secondary
 * shader and index values are not. Capture only the matching local job. */
static void auto9_vm3_indexed_inputs(Auto9MaliG3d1 *s, const uint8_t primitive[32],
                                     const uint8_t vertex[96], unsigned fault)
{
    uint8_t rsd[64], index[84], code[4096];
    uint32_t count = ldl_le_p(primitive + 12);
    if (ldl_le_p(primitive) != 0x1407020a ||
        (count != 5 && count != 9 && count != 29 && count != 41)) {
        return;
    }
    uint64_t state = ldq_le_p(vertex + 56), idx = ldq_le_p(primitive + 16);
    if (!state || (state & 63) || !idx || (idx & 1) ||
        !auto9_g3d00_dma_read(s, state, rsd, sizeof(rsd)) ||
        ldl_le_p(rsd + 12) != 0x40003 || ldl_le_p(rsd + 48) != 0xc6c00 ||
        !auto9_g3d00_dma_read(s, idx, index, (count + 1) * 2)) {
        return;
    }
    uint64_t primary = ldq_le_p(rsd), secondary = ldq_le_p(rsd + 56);
    if (!primary || (primary & 15) ||
        !auto9_g3d00_dma_read(s, primary, code, 64)) {
        return;
    }
    g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256, code, 64);
    if (!sha || strcmp(sha, "bc4e390eb1103bc61d25115369a8ea179cb7b991fdf85d55e99458d6028ee2cf")) {
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 indexed inputs: fault=%u base=0x%x restart=0x%x "
        "index=%u,%u,%u,%u,%u,%u secondary=%u properties=0x%x preload=0x%x\n",
        fault, ldl_le_p(primitive + 4), ldl_le_p(primitive + 8),
        lduw_le_p(index), lduw_le_p(index + 2), lduw_le_p(index + 4),
        lduw_le_p(index + 6), lduw_le_p(index + 8), lduw_le_p(index + 10),
        (unsigned)!!secondary, ldl_le_p(rsd + 16), ldl_le_p(rsd + 52));
    if (count > 5) {
        auto9_g3d00_sample_bytes("vm3-indexed-input", fault, "indices", 0, index, (count + 1) * 2);
    }
    auto9_vm3_vertex_layout(s, vertex, fault, count == 41 ? 42 : 10);
    if (!secondary || (secondary & 15) ||
        !auto9_g3d00_dma_read(s, secondary, code, 64)) {
        return;
    }
    unsigned read, stop, reason;
    unsigned extent = auto9_vm3_vertex4_extent(s, secondary, code, &read, &stop, &reason);
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 indexed secondary: fault=%u read=%u extent=%u stop=%u reason=%u\n",
        fault, read, extent, stop, reason);
    for (unsigned off = 0; off < extent; off += 128) {
        auto9_g3d00_sample_bytes("vm3-indexed-secondary", fault, "framed-program",
                                 off, code + off, MIN(extent - off, 128));
    }
}

/* Observe bounded, readable headers after a real failure. Never call the
 * descriptor executor or mark a skipped job complete. */
static void auto9_vm3_chain_fault(Auto9MaliG3d1 *s, uint64_t head,
                                  const uint8_t header[32], const uint64_t *prefix,
                                  unsigned count, unsigned slot, const char *why,
                                  uint32_t result)
{
    uint64_t seen[8];
    uint32_t word = ldl_le_p(header + 16), deps = ldl_le_p(header + 20);
    unsigned type = (word >> 1) & 0x7f;
    if (!s->vm3_control || s->vm3_chain_fault_traces >= 256 ||
        !qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        return;
    }
    unsigned fault = ++s->vm3_chain_fault_traces;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 VM3 chain stop: n=%u slot=%u node=%u type=%u index=%u "
        "flags=0x%x dep1=%u dep2=%u why=%s status=0x%x\n",
        fault, slot, count, type, word >> 16, word & 0xff01,
        deps & 0xffff, deps >> 16, why, result);
    if ((word & 1) && type == 5 && !strcmp(why, "descriptor")) {
        auto9_vm3_failed_vertex(s, head, 64, fault);
    }
    if ((word & 1) && type == 10 && slot == 1 && !strcmp(why, "descriptor") &&
        (s->vm3_indexed_samples < 4 || !s->vm3_indexed42_sampled)) {
        uint8_t primitive[32], frag[96], vertex[96];
        if (auto9_g3d00_dma_read(s, head + 40, primitive, sizeof(primitive)) &&
            auto9_g3d00_dma_read(s, head + 128, frag, sizeof(frag)) &&
            auto9_g3d00_dma_read(s, head + 256, vertex, sizeof(vertex))) {
            /* Reserve one independent capture for the observed 24/42 form. */
            bool wide = ldl_le_p(primitive) == 0x1407020a &&
                        ldl_le_p(primitive + 12) == 41 &&
                        ldl_le_p(vertex + 4) == 0x2310;
            if (wide ? !s->vm3_indexed42_sampled : s->vm3_indexed_samples < 4) {
                if (wide) {
                    s->vm3_indexed42_sampled = true;
                } else {
                    s->vm3_indexed_samples++;
                }
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 indexed layout: fault=%u prim=0x%x count-minus-one=%u indices=%u "
                    "frag=0x%x/0x%x vertex=0x%x/0x%x positions=%u/%u\n",
                    fault, ldl_le_p(primitive), ldl_le_p(primitive + 12),
                    (unsigned)!!ldq_le_p(primitive + 16), ldl_le_p(frag), ldl_le_p(frag + 4),
                    ldl_le_p(vertex), ldl_le_p(vertex + 4),
                    (unsigned)!!ldq_le_p(frag + 16), (unsigned)!!ldq_le_p(vertex + 16));
                auto9_vm3_failed_vertex(s, head, 256, fault);
                auto9_vm3_indexed_inputs(s, primitive, vertex, fault);
            }
        }
    }
    if ((word & 1) && type == 4 && slot == 1 && !strcmp(why, "descriptor")) {
        auto9_vm3_failed_compute(s, head, fault);
    }
    if (!(word & 1)) {
        return;
    }
    uint64_t next = ldq_le_p(header + 24);
    for (unsigned n = 0; n < G_N_ELEMENTS(seen) && next; n++) {
        uint8_t h[32];
        if ((next & 63) || next == head) {
            break;
        }
        for (unsigned i = 0; i < count; i++) {
            if (prefix[i] == next) {
                return;
            }
        }
        for (unsigned i = 0; i < n; i++) {
            if (seen[i] == next) {
                return;
            }
        }
        if (!auto9_g3d00_dma_read(s, next, h, sizeof(h))) {
            break;
        }
        word = ldl_le_p(h + 16);
        if (!(word & 1) || ldl_le_p(h)) {
            break;
        }
        seen[n] = next;
        type = (word >> 1) & 0x7f;
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 chain skipped: n=%u tail=%u type=%u index=%u\n",
            fault, n + 1, type, word >> 16);
        if (type == 9) {
            uint8_t job[64], fb[128], ext[64], rt[64];
            if (auto9_g3d00_fragment_inputs(s, next, job, fb, ext, rt, false)) {
                char footprint[65] = {0};
                uint32_t shape[4];
                auto9_vm3_center_rt_mapping(s, fb, rt, footprint, shape);
                qemu_log_mask(LOG_GUEST_ERROR,
                    "Auto9 VM3 chain skipped RT0: n=%u tail=%u wh=0x%x "
                    "format=0x%x footprint=%s\n",
                    fault, n + 1, ldl_le_p(fb + 32), ldl_le_p(rt + 4),
                    footprint[0] ? footprint : "-");
            }
        }
        next = ldq_le_p(h + 24);
    }
}

static void auto9_qnx_chain_fault(Auto9MaliG3d1 *s, const uint8_t *header,
                                   unsigned slot, unsigned node, const char *phase,
                                   uint32_t status)
{
    if (!s->g3d00 || s->qnx_chain_fault_samples >= 16 ||
        !qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        return;
    }
    uint32_t flags = header ? ldl_le_p(header + 16) : 0;
    uint32_t deps = header ? ldl_le_p(header + 20) : 0;
    qemu_log_mask(LOG_GUEST_ERROR,
        "Auto9 QNX chain stop: n=%u slot=%u node=%u phase=%s status=0x%x "
        "header=%u type=%u index=%u flags=0x%x dep1=%u dep2=%u\n",
        ++s->qnx_chain_fault_samples, slot, node, phase, status,
        (unsigned)!!header, (flags >> 1) & 0x7f, flags >> 16,
        flags & 0xff01, deps & 0xffff, deps >> 16);
}

/*
 * Serial execution is one valid order for backward dependencies. Stop at
 * the first unsupported/faulted node and preserve preceding real writebacks.
 */
static uint32_t auto9_g3d00_control_chain(Auto9MaliG3d1 *s, uint64_t head,
                                         unsigned slot, uint32_t config)
{
    uint64_t visited[AUTO9_G3D00_CHAIN_MAX];
    uint16_t completed[AUTO9_G3D00_CHAIN_MAX];

    s->last_job_type = 0;
    if (!head || (config & 0xf) || s->as0_locked) {
        auto9_qnx_chain_fault(s, NULL, slot, 0,
            !head ? "empty-head" : (config & 0xf) ? "address-space" : "locked",
            G3D00_JOB_CONFIG_FAULT);
        return G3D00_JOB_CONFIG_FAULT;
    }
    for (unsigned n = 0; n < AUTO9_G3D00_CHAIN_MAX; n++) {
        uint8_t header[32];
        uint32_t word, deps, type, result;
        uint16_t index, dep1, dep2;
        bool seen1, seen2;

        if (head & 63) {
            auto9_qnx_chain_fault(s, NULL, slot, n, "alignment", G3D00_JOB_CONFIG_FAULT);
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (!auto9_g3d00_dma_read(s, head, header, sizeof(header))) {
            auto9_qnx_chain_fault(s, NULL, slot, n, "header-read", G3D00_JOB_READ_FAULT);
            return G3D00_JOB_READ_FAULT;
        }
        word = ldl_le_p(header + 16);
        deps = ldl_le_p(header + 20);
        type = (word >> 1) & 0x7f;
        index = word >> 16;
        dep1 = deps;
        dep2 = deps >> 16;
        seen1 = !dep1;
        seen2 = !dep2;
        s->last_job_type = type;
        if (s->g3d00 && (word & 1) && type == 7) {
            auto9_g3d00_tiler_sample(s, head);
        }
        if (!(word & 1) || ldl_le_p(header) || (word & 0x2400U)) {
            auto9_qnx_chain_fault(s, header, slot, n, "header", G3D00_JOB_CONFIG_FAULT);
            auto9_vm3_chain_fault(s, head, header, visited, n, slot,
                                  "header", G3D00_JOB_CONFIG_FAULT);
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (s->g3d00 && type == 5) {
            auto9_g3d00_vertex_sample(s, head, slot);
        }
        if (s->vm3_control && type == 5) {
            auto9_g3d1_vm3_vertex_sample(s, head, slot);
        }
        if (s->vm3_control && (type == 6 || type == 7 || type == 8 || type == 10)) {
            auto9_g3d1_vm3_geometry_sample(s, head, type, slot);
        }
        for (unsigned i = 0; i < n; i++) {
            if (visited[i] == head || (index && completed[i] == index)) {
                auto9_qnx_chain_fault(s, header, slot, n, "duplicate", G3D00_JOB_CONFIG_FAULT);
                auto9_vm3_chain_fault(s, head, header, visited, n, slot,
                                      "duplicate", G3D00_JOB_CONFIG_FAULT);
                return G3D00_JOB_CONFIG_FAULT;
            }
            seen1 |= dep1 == completed[i];
            seen2 |= dep2 == completed[i];
        }
        if (!seen1 || !seen2) {
            s->vm3_unresolved_dependencies += s->vm3_control;
            auto9_qnx_chain_fault(s, header, slot, n, "dependency", G3D00_JOB_CONFIG_FAULT);
            auto9_vm3_chain_fault(s, head, header, visited, n, slot,
                                  "dependency", G3D00_JOB_CONFIG_FAULT);
            return G3D00_JOB_CONFIG_FAULT;
        }
        if (word & (BIT(8) | BIT(9))) {
            /* The synchronous path has no prefetched descriptors or GPU cache. */
            smp_mb();
        }
        if (s->vm3_control) {
            s->vm3_desc_attempts++;
        }
        result = auto9_g3d00_control_descriptor(s, head, slot, type);
        if (result != G3D00_JOB_DONE) {
            if (s->vm3_control) {
                s->vm3_desc_failed++;
            }
            auto9_qnx_chain_fault(s, header, slot, n, "descriptor", result);
            auto9_vm3_chain_fault(s, head, header, visited, n, slot, "descriptor", result);
            return result;
        }
        if (s->vm3_control) {
            s->vm3_desc_completed++;
        }
        visited[n] = head;
        completed[n] = index;
        head = ldq_le_p(header + 24);
        if (!head) {
            return G3D00_JOB_DONE;
        }
    }
    auto9_qnx_chain_fault(s, NULL, slot, AUTO9_G3D00_CHAIN_MAX,
                         "chain-limit", G3D00_JOB_CONFIG_FAULT);
    return G3D00_JOB_CONFIG_FAULT;
}

static void auto9_g3d00_job_submit(Auto9MaliG3d1 *s, unsigned index,
                                    uint64_t head, uint32_t config, bool same_as)
{
    Auto9G3d00JobSlot *job = &s->slot[index];
    unsigned first_batch = s->tile_batch_count;
    s->job_starts++;
    job->head = head;
    if (!same_as) {
        s->last_job_type = 0;
        auto9_qnx_chain_fault(s, NULL, index, 0, "queued-address-space", G3D00_JOB_CONFIG_FAULT);
    }
    job->status = same_as ? auto9_g3d00_control_chain(s, head, index, config)
                          : G3D00_JOB_CONFIG_FAULT;
    if (job->status == G3D00_JOB_DONE) {
        unsigned flush = (config >> 12) & 3;
        if (flush == 1 || flush == 3) {
            auto9_g3d00_flush_ordering(s);
        }
        s->job_completions++;
        s->irq_raw[G3D1_JOB] |= BIT(index);
    } else {
        /* JS1 only appends batches; only JS0 can consume them. This chain
         * ran synchronously, so the prefix still belongs to older submits. */
        if (s->vm3_control && index == 1) {
            auto9_vm3_discard_faulted_empty_batches(s, first_batch);
        }
        s->job_failures++;
        s->irq_raw[G3D1_JOB] |= BIT(index + 16);
    }
    auto9_mali_g3d1_irq_update(s);
}

/* The software engine delays a queued slot until the older IRQ is ACKed.
 * Keep the IRQ_JS_STATE sample from the ACK, not the new completion, so
 * the OEM ISR never mistakes two submitted jobs for two old completions. */
static void auto9_vm3_job_ack(Auto9MaliG3d1 *s, uint32_t clear, uint32_t acknowledged)
{
    for (unsigned index = 0; index < AUTO9_G3D1_JM_SLOTS; index++) {
        uint32_t bits = BIT(index) | BIT(index + 16);
        Auto9G3d00JobSlot *job = &s->slot[index];
        if (!(clear & bits)) {
            continue;
        }
        s->vm3_js_state &= ~bits;
        if (job->queued) {
            s->vm3_js_state |= BIT(index + 16);
        }
        if (!job->queued || !(acknowledged & bits) || (s->irq_raw[G3D1_JOB] & bits) ||
            timer_pending(s->reset_timer)) {
            continue;
        }
        bool same_as = job->queued_ttbr == s->as0_active_ttbr &&
                       job->queued_transcfg == s->as0_active_transcfg &&
                       job->queued_memattr == s->as0_active_memattr;
        s->vm3_next_as_mismatch += !same_as;
        s->vm3_next_dispatched++;
        job->queued = false;
        auto9_g3d00_job_submit(s, index, job->queued_head, job->queued_config, same_as);
    }
}

static bool auto9_g3d00_job_access(Auto9MaliG3d1 *s, hwaddr off,
                                  uint64_t *value, bool write)
{
    Auto9G3d00JobSlot *job;
    uint32_t *latch;
    unsigned index;

    if (!s->control_jobs) {
        return false;
    }
    if (s->vm3_control && off == 0x1010 && !write) {
        *value = s->vm3_js_state;
        return true;
    }
    if (off < AUTO9_G3D00_JS_BASE ||
        off >= AUTO9_G3D00_JS_BASE + AUTO9_G3D1_JM_SLOTS * AUTO9_G3D00_JS_STRIDE) {
        return false;
    }
    index = (off - AUTO9_G3D00_JS_BASE) / AUTO9_G3D00_JS_STRIDE;
    job = &s->slot[index];
    switch (off & 0x7f) {
    case 0x00: case 0x04:
        if (write) {
            return false;
        }
        *value = (uint32_t)(job->head >> ((off & 4) ? 32 : 0));
        return true;
    case 0x08: case 0x0c: /* No hardware replay tail for the synchronous path. */
        if (write) {
            return false;
        }
        *value = 0;
        return true;
    case 0x24:
        if (write) {
            return false;
        }
        *value = job->status;
        return true;
    case 0x40: case 0x44:
        latch = &job->head_next[(off & 4) != 0];
        break;
    case 0x50: case 0x54:
        latch = &job->affinity_next[(off & 4) != 0];
        break;
    case 0x58:
        latch = &job->config_next;
        break;
    case 0x70:
        latch = &job->flush_id_next;
        break;
    case 0x60: {
        if (!write) {
            *value = job->queued;
            return true;
        }
        if (!*value) {
            if (job->queued) {
                job->queued = false;
                s->vm3_next_canceled++;
            }
            return true;
        }
        if (*value != 1 || timer_pending(s->reset_timer) || job->queued) {
            return false;
        }
        uint64_t head = ((uint64_t)job->head_next[1] << 32) | job->head_next[0];
        if (s->irq_raw[G3D1_JOB] & (BIT(index) | BIT(index + 16))) {
            if (!s->vm3_control) {
                return false;
            }
            job->queued = true;
            job->queued_head = head;
            job->queued_config = job->config_next;
            job->queued_ttbr = s->as0_active_ttbr;
            job->queued_transcfg = s->as0_active_transcfg;
            job->queued_memattr = s->as0_active_memattr;
            s->vm3_next_queued++;
            return true;
        }
        auto9_g3d00_job_submit(s, index, head, job->config_next, true);
        return true;
    }
    default:
        return false;
    }
    if (write) {
        *latch = *value;
    } else {
        *value = *latch;
    }
    return true;
}

static void auto9_vm3_reset_tile_queue(Auto9MaliG3d1 *s)
{
    if (!s->vm3_control || !s->tile_batch_count) {
        return;
    }
    /* JS and MMU reset invalidates host-side pending geometry, not texture
     * memory. Keep verified atlas backing and leave QNX's model unchanged. */
    unsigned retired = s->tile_batch_count;
    s->tile_batch_count = 0;
    s->vm3_reset_tile_batches += retired;
    if (s->vm3_reset_tile_batches <= 512) {
        qemu_log_mask(LOG_GUEST_ERROR,
            "Auto9 VM3 reset retired tile batches: retired=%u total=%u\n",
            retired, s->vm3_reset_tile_batches);
    }
}

static void auto9_mali_g3d1_reset_complete(void *opaque)
{
    Auto9MaliG3d1 *s = opaque;
    auto9_mali_g3d1_config_reset(s);
    auto9_mali_cycle_reset(s);
    auto9_g3d00_as0_reset(s);
    auto9_vm3_reset_tile_queue(s);
    s->reset_completions++;
    s->irq_raw[G3D1_GPU] |= AUTO9_G3D1_RESET_DONE;
    auto9_mali_g3d1_irq_update(s);
}

static void auto9_mali_g3d1_cache_complete(void *opaque)
{
    Auto9MaliG3d1 *s = opaque;
    /* This model has no cached copies; control-job DMA writes RAM directly. */
    if (s->control_jobs) {
        auto9_g3d00_flush_ordering(s);
    }
    s->cache_completions++;
    s->irq_raw[G3D1_GPU] |= AUTO9_G3D1_CACHE_DONE;
    auto9_mali_g3d1_irq_update(s);
}

static bool auto9_mali_g3d1_irq_access(Auto9MaliG3d1 *s, hwaddr off,
                                      uint64_t *value, bool write)
{
    unsigned bank, word;

    if (off >= 0x20 && off <= 0x2c) {
        bank = G3D1_GPU;
        word = off - 0x20;
    } else if (off >= 0x1000 && off <= 0x100c) {
        bank = G3D1_JOB;
        word = off - 0x1000;
    } else if (off >= 0x2000 && off <= 0x200c) {
        bank = G3D1_MMU;
        word = off - 0x2000;
    } else {
        return false;
    }

    if (write) {
        switch (word) {
        case 4: {
            uint32_t acknowledged = s->irq_raw[bank] & *value;
            s->irq_raw[bank] &= ~*value;
            if (s->vm3_control && bank == G3D1_JOB) {
                auto9_vm3_job_ack(s, *value, acknowledged);
            }
            break;
        }
        case 8:
            s->irq_mask[bank] = *value;
            break;
        default:
            return false;
        }
        s->irq_writes++;
        auto9_mali_g3d1_irq_update(s);
    } else {
        switch (word) {
        case 0:
            *value = s->irq_raw[bank];
            break;
        case 8:
            *value = s->irq_mask[bank];
            break;
        case 12:
            *value = s->irq_raw[bank] & s->irq_mask[bank];
            break;
        default:
            return false;
        }
        s->irq_reads++;
    }
    return true;
}

static int auto9_mali_control_vmid(void)
{
    if (!current_cpu) {
        return -1;
    }
    CPUARMState *env = &ARM_CPU(current_cpu)->env;
    /* G3D1 is dynamically granted to VM4 as well. A physical MMIO address
     * alone cannot authorize access to the VM3 page-table and job state. */
    if (!is_a64(env) || arm_current_el(env) != 1 || !(arm_hcr_el2_eff(env) & HCR_VM)) {
        return -1;
    }
    return env->cp15.vttbr_el2 >> 48;
}

/* VM4 has no job/MMU engine in this profile. Expose its own mask storage
 * without propagating a mask or interrupt to the VM3 device bank. */
static bool auto9_mali_vm4_irq_mask(Auto9MaliG3d1 *s, int vmid, hwaddr off,
                                   uint64_t *value, bool write)
{
    if (!s->vm3_control || vmid != 4 || (off != 0x1008 && off != 0x2008)) {
        return false;
    }
    unsigned bank = off == 0x2008;
    if (write) {
        s->vm4_irq_mask[bank] = *value;
        if (bank) {
            s->vm4_mmu_mask_writes++;
        } else {
            s->vm4_job_mask_writes++;
        }
    } else {
        *value = s->vm4_irq_mask[bank];
    }
    return true;
}

static bool auto9_mali_control_regs(hwaddr off)
{
    return (off >= 0x1000 && off < 0x2800) || off == 0x38;
}

static MemTxResult auto9_mali_g3d1_read(void *opaque, hwaddr off, uint64_t *value,
                                        unsigned size, MemTxAttrs attrs)
{
    Auto9MaliG3d1 *s = opaque;
    int vmid = s->vm3_control ? auto9_mali_control_vmid() : -1;
    bool control_caller = !s->vm3_control || vmid == 3;
    if (off == 0) {
        *value = s->gpu_id;
        s->id_reads++;
        return MEMTX_OK;
    }
    if (s->g3d00 && off >= 0x90 && off <= 0x9c && !(off & 3)) {
        uint64_t ns = off < 0x98 ? auto9_g3d00_cycle_elapsed(s) :
                                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        uint64_t count = ns / AUTO9_G3D00_CYCLE_PERIOD_NS;
        s->cycle_reads++;
        *value = (uint32_t)(count >> ((off & 4) ? 32 : 0));
        return MEMTX_OK;
    }
    if (s->vm3_control && !control_caller && auto9_mali_control_regs(off)) {
        if (auto9_mali_vm4_irq_mask(s, vmid, off, value, false)) {
            return MEMTX_OK;
        }
        s->vm3_control_denied++;
        *value = 0; /* No VM3 state is exported to another requester. */
        return MEMTX_OK;
    }
    if (control_caller && (auto9_g3d00_as0_access(s, off, value, false) ||
        auto9_g3d00_job_access(s, off, value, false))) {
        s->vm3_control_accesses += s->vm3_control;
        return MEMTX_OK;
    }
    if (s->reset_irq && auto9_mali_g3d1_irq_access(s, off, value, false)) {
        return MEMTX_OK;
    }
    if (s->control_jobs && off == 0x38) { /* Bifrost LATEST_FLUSH, read-only. */
        *value = s->latest_flush_id;
        s->latest_flush_reads++;
        return MEMTX_OK;
    }
    if (s->reset_irq && off == 0x34) {
        *value = 0; /* No running shader or performance counter. */
        return MEMTX_OK;
    }
    if (s->config_enabled && auto9_mali_g3d1_config_access(s, off, value, false)) {
        return MEMTX_OK;
    }
    if (s->empty_power && auto9_mali_g3d1_empty_power(s, off, value, false)) {
        return MEMTX_OK;
    }
    if (s->jm_topology) {
        /* A virtual three-slot register topology for the OEM scheduler.
         * The third selector is used by original kbase; it does not
         * establish a third independently executing silicon pipeline. */
        static const uint32_t features[] = { BIT(9), 0x9c, 0x18 };
        if (off == 0x1c) {
            *value = BIT(AUTO9_G3D1_JM_SLOTS) - 1;
        } else if (off >= 0xc0 && off < 0xc0 + sizeof(features) && !(off & 3)) {
            *value = features[(off - 0xc0) / 4];
        } else if (off == 0x1010 || (off >= 0x1800 && off < 0x1980 &&
                   ((off & 0x7f) == 0x24 || (off & 0x7f) == 0x60))) {
            *value = 0; /* No active job or queued command. */
        } else {
            goto other_features;
        }
        s->jm_probe_reads++;
        return MEMTX_OK;
    }
other_features:
    if (s->discovery) {
        /*
         * Read-only GPU feature aperture (original kbase_gpuprops).
         * No shader, tiler, L2 or coherency engine advertised.
         * 48-bit VA/40-bit PA describe the virtual address interface only.
         */
        switch (off) {
        case 0x14:
            *value = 0x2830;
            break;
        case 0x300:
            *value = BIT(31); /* COHERENCY_NONE */
            break;
        case 0x18:
            *value = control_caller ? s->as_present : 0;
            break;
        case 0x04: case 0x08: case 0x0c: case 0x10: case 0x1c:
        case 0xa0: case 0xa4: case 0xa8: case 0xac:
        case 0xb0: case 0xb4: case 0xb8: case 0xbc:
        case 0xc0: case 0xc4: case 0xc8: case 0xcc:
        case 0xd0: case 0xd4: case 0xd8: case 0xdc:
        case 0xe0: case 0xe4: case 0xe8: case 0xec:
        case 0xf0: case 0xf4: case 0xf8: case 0xfc:
        case 0x100: case 0x104: case 0x110: case 0x114:
        case 0x120: case 0x124: case 0x310: case 0xe00: case 0xe04:
            *value = 0;
            break;
        default:
            return auto9_mali_g3d1_reject(s, off, false);
        }
        s->feature_reads++;
        s->feature_last_offset = off;
        return MEMTX_OK;
    }
    return auto9_mali_g3d1_reject(s, off, false);
}

static MemTxResult auto9_mali_g3d1_write(void *opaque, hwaddr off, uint64_t value,
                                         unsigned size, MemTxAttrs attrs)
{
    Auto9MaliG3d1 *s = opaque;
    int vmid = s->vm3_control ? auto9_mali_control_vmid() : -1;
    if (s->vm3_control && auto9_mali_control_regs(off) && vmid != 3) {
        if (auto9_mali_vm4_irq_mask(s, vmid, off, &value, true)) {
            return MEMTX_OK;
        }
        if (off == 0x1004) {
            /* JOB_IRQ_CLEAR is W1C. The other requester's job IRQ view is
             * empty, so clearing it cannot acknowledge a VM3-owned IRQ. */
            s->vm3_foreign_job_irq_clear++;
            return MEMTX_OK;
        }
        if (off == 0x2004) {
            /* Like JOB_IRQ_CLEAR, this is W1C against the requester's
             * empty view, never the VM3-owned fault or interrupt bank. */
            s->vm3_foreign_mmu_irq_clear++;
            return MEMTX_OK;
        }
        s->vm3_control_denied++;
        if (value) {
            s->last_rejected_value = value;
        }
        return value == 0 ? MEMTX_OK : auto9_mali_g3d1_reject(s, off, true);
    }
    if (auto9_g3d00_as0_access(s, off, &value, true) ||
        auto9_g3d00_job_access(s, off, &value, true)) {
        s->vm3_control_accesses += s->vm3_control;
        return MEMTX_OK;
    }
    if (s->config_enabled && auto9_mali_g3d1_config_access(s, off, &value, true)) {
        return MEMTX_OK;
    }
    if (s->empty_power && auto9_mali_g3d1_empty_power(s, off, &value, true)) {
        return MEMTX_OK;
    }
    if (s->g3d00 && off == 0x30 && (value == 5 || value == 6)) {
        if (value == 5) {
            if (!s->cycle_running) {
                s->cycle_epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
                s->cycle_running = 1;
            }
            s->cycle_starts++;
        } else {
            s->cycle_elapsed_ns = auto9_g3d00_cycle_elapsed(s);
            s->cycle_running = 0;
            s->cycle_stops++;
        }
        /* Counter operations have no effect on job or IRQ state. */
        return MEMTX_OK;
    }
    if (s->reset_irq) {
        if (auto9_mali_g3d1_irq_access(s, off, &value, true)) {
            return MEMTX_OK;
        }
        if (off == 0x30 && (value == 1 || value == 2)) {
            if (s->cache_timer) {
                timer_del(s->cache_timer);
            }
            memset(s->irq_raw, 0, sizeof(s->irq_raw));
            s->reset_commands++;
            auto9_mali_g3d1_irq_update(s);
            timer_mod(s->reset_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      AUTO9_G3D1_RESET_NS);
            return MEMTX_OK;
        }
        if (off == 0x30 && (value == 8 || (s->control_jobs && value == 7)) &&
            s->cache_timer &&
            !timer_pending(s->reset_timer) && !timer_pending(s->cache_timer)) {
            /* GPU IRQ 17 acknowledges the completed cache operation. */
            s->cache_commands++;
            timer_mod(s->cache_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      AUTO9_G3D1_CACHE_NS);
            return MEMTX_OK;
        }
    }
    s->last_rejected_value = value;
    if (s->g3d00 && off == 0x30) {
        qemu_log_mask(LOG_UNIMP, "Auto9 G3D00 unsupported GPU_COMMAND 0x%" PRIx64 "\n", value);
    }
    return auto9_mali_g3d1_reject(s, off, true);
}

static const MemoryRegionOps auto9_mali_g3d1_ops = {
    .read_with_attrs = auto9_mali_g3d1_read,
    .write_with_attrs = auto9_mali_g3d1_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void auto9_mali_g3d1_reset(DeviceState *dev)
{
    Auto9MaliG3d1 *s = AUTO9_MALI_G3D1(dev);
    auto9_vm3_atlas_invalidate(s, 0, 0, 0, false);
    s->id_reads = s->rejected = s->last_rejected = s->last_rejected_write = 0;
    s->feature_reads = s->feature_last_offset = s->last_rejected_value = 0;
    s->jm_probe_reads = 0;
    auto9_g3d00_as0_reset(s);
    s->as0_updates = s->as0_locks = s->as0_flushes = 0;
    s->job_starts = s->job_completions = s->job_failures = s->last_job_type = 0;
    s->vm3_desc_attempts = s->vm3_desc_completed = s->vm3_desc_failed = 0;
    s->vm3_control_accesses = s->vm3_control_denied = 0;
    s->vm3_foreign_job_irq_clear = 0;
    s->vm3_foreign_mmu_irq_clear = 0;
    memset(s->vm4_irq_mask, 0, sizeof(s->vm4_irq_mask));
    s->vm4_job_mask_writes = s->vm4_mmu_mask_writes = 0;
    s->vm3_oem_transcfg_updates = s->vm3_translation_mode_rejections = 0;
    s->vm3_fragment_attempts = s->vm3_fragment_decoded = s->vm3_fragment_tiler_rejections = 0;
    s->vm3_empty_fragment_failures = s->vm3_empty_fragment_profiles = 0;
    s->vm3_empty_discard_validated = 0;
    s->vm3_preframe_attempts = 0;
    s->vm3_pf_producers_seen = s->vm3_pf_producers_mapped = 0;
    memset(s->vm3_pf_producers, 0, sizeof(s->vm3_pf_producers));
    s->vm3_zero_resolves = s->vm3_single_zero_clears = 0;
    s->vm3_center_reads_traced = s->vm3_center_rt_probes = 0;
    s->vm3_chain_fault_traces = s->vm3_failed_vertex_samples = s->vm3_vertex3_layout_samples = 0;
    s->vm3_vertex4_layout_samples = s->vm3_vertex4_program_attempts = 0;
    s->vm3_vertex448_layout_samples = s->vm3_tiler_stop_samples = s->vm3_new_tiler_stops = 0;
    s->vm3_vertex336_layout_samples = s->vm3_vertex336_stage = 0;
    s->vm3_vertex336_tagged_jobs = 0;
    s->vm3_compute_samples = s->vm3_compute_programs = s->vm3_fan_origin_samples = 0;
    s->vm3_compute_jobs = s->vm3_points_binned = s->vm3_point_fragment_jobs = s->vm3_point_mismatches = 0;
    memset(s->vm3_compute_prefixes, 0, sizeof(s->vm3_compute_prefixes));
    memset(s->vm3_fan_origins, 0, sizeof(s->vm3_fan_origins));
    s->vm3_vertex4_program_captured = s->vm3_vertex45_program_captured = false;
    s->vm3_vertex45_layout_samples = s->vm3_vertex45_program_attempts = s->vm3_vertex45_jobs = 0;
    s->vm3_vertex608_tagged_traces = s->vm3_vertex592_pos44_traces = 0;
    memset(s->vm3_failed_vertex_fingerprints, 0, sizeof(s->vm3_failed_vertex_fingerprints));
    s->vm3_geometry_seen = s->vm3_geometry_samples = 0;
    s->vm3_stock_fans = s->vm3_stock_fan_primitives = s->vm3_fragment_matched_batches = 0;
    s->vm3_color_fans = s->vm3_color_resolves = 0;
    s->vm3_color_clipped_fans = 0;
    s->vm3_faulted_empty_batches = s->vm3_reset_tile_batches = 0;
    s->vm3_interleaved_fans = s->vm3_interleaved_fragment_samples = 0;
    s->vm3_indexed_draw_profiles = s->vm3_indexed_tagged_traces = 0;
    s->vm3_dual_uv_sampled = false;
    s->vm3_triangle_list_sampled = false;
    s->vm3_short_lists_sampled = s->vm3_tagged_tiler_traces = 0;
    s->vm3_interleaved_resolves = s->vm3_atlas_strips = s->vm3_atlas_fragment_samples = 0;
    s->vm3_atlas_always_samples = 0;
    s->vm3_indexed_samples = s->vm3_indexed_layout_samples = 0;
    s->vm3_indexed42_sampled = false;
    s->vm3_color_pixels = 0;
    s->vm3_textured_resolves = s->vm3_textured_attempts = 0;
    s->vm3_textured_state_fault_samples = 0;
    s->vm3_clipped_afbc_samples = 0;
    s->vm3_textured_bytes = s->vm3_textured_nonzero_pixels = s->vm3_textured_shaded_pixels = 0;
    memset(s->vm3_center_target, 0, sizeof(s->vm3_center_target));
    s->vm3_center_generation = s->vm3_center_evictions = 0;
    s->vm3_center_depth_observed = 0;
    s->vm3_linear_rgba_loads = s->vm3_linear_reverse_loads = s->vm3_linear_rgba_rgb_texels = 0;
    s->vm3_texture_loaded = s->vm3_texture_zero_opacity = 0;
    s->vm3_texture_rgb_texels = s->vm3_texture_alpha_texels = s->vm3_texture_rgb_samples = 0;
    s->vm3_nearest_texture_loads = s->vm3_single_tile_zero_pitch_loads = 0;
    s->vm3_nearest_rgb_texels = 0;
    s->vm3_next_queued = s->vm3_next_dispatched = s->vm3_next_canceled = s->vm3_next_as_mismatch = 0;
    s->vm3_unresolved_dependencies = 0;
    s->vm3_crc_invalidated_bytes = 0;
    s->vm3_vertex_attempts = s->vm3_vertex_samples = 0;
    s->vm3_stock_vertices_written = s->vm3_vertex3_jobs = s->vm3_vertex4_jobs = 0;
    s->vm3_vertex448_jobs = 0;
    memset(s->vm3_vertex_fingerprints, 0, sizeof(s->vm3_vertex_fingerprints));
    s->job_descriptors_completed = s->job_cache_flushes = s->vertex_samples = 0;
    s->vertex_descriptors_completed = s->vertices_written = 0;
    s->tiler_sample_attempts = 0;
    s->fragment_sample_attempts = s->tiler_descriptors_completed = 0;
    s->fragment_fault_samples = 0;
    s->primitives_binned = s->tile_batch_count = 0;
    s->fragment_descriptors_completed = s->fragment_pixels_shaded = 0;
    s->fragment_te_unconditional = 0;
    s->fragment_bytes_written = 0;
    s->vertex_sample_attempts = 0;
    s->qnx_chain_fault_samples = s->qnx_vertex_fault_samples = 0;
    s->qnx_vertex_first_stages = 0;
    s->qnx_screen_leaf_reads = s->qnx_screen_leaf_writes = 0;
    s->qnx_viewport_cross_page_jobs = 0;
    s->latest_flush_reads = 0;
    auto9_mali_g3d1_config_reset(s);
    s->config_reads = s->config_writes = 0;
    s->power_reads = s->power_writes = 0;
    auto9_mali_cycle_reset(s);
    s->cycle_starts = s->cycle_stops = s->cycle_reads = 0;
    if (s->cache_timer) {
        timer_del(s->cache_timer);
    }
    s->cache_commands = s->cache_completions = 0;
    if (s->reset_irq) {
        timer_del(s->reset_timer);
        memset(s->irq_raw, 0, sizeof(s->irq_raw));
        memset(s->irq_mask, 0, sizeof(s->irq_mask));
        s->irq_reads = s->irq_writes = 0;
        s->reset_commands = s->reset_completions = 0;
        auto9_mali_g3d1_irq_update(s);
    }
}

static void auto9_mali_g3d1_init(Object *obj)
{
    Auto9MaliG3d1 *s = AUTO9_MALI_G3D1(obj);
    memory_region_init_io(&s->regs, obj, &auto9_mali_g3d1_ops, s,
                          "auto9-mali-g3d1", 0x5000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regs);
    for (unsigned i = 0; i < G3D1_IRQ_COUNT; i++) {
        sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq[i]);
    }
    object_property_add_uint32_ptr(obj, "power-reads", &s->power_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "power-writes", &s->power_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "config-reads", &s->config_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "config-writes", &s->config_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "jm-config", &s->config[0], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "shader-config", &s->config[1], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "tiler-config", &s->config[2], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "l2-mmu-config", &s->config[3], OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "coherency-mode", &s->coherency, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-reads", &s->irq_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "irq-writes", &s->irq_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-commands", &s->reset_commands, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "reset-completions", &s->reset_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "gpu-irq-level", &s->gpu_irq_level, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cache-commands", &s->cache_commands, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cache-completions", &s->cache_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "gpu-id", &s->gpu_id, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cycle-running", &s->cycle_running, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cycle-starts", &s->cycle_starts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cycle-stops", &s->cycle_stops, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cycle-reads", &s->cycle_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "id-reads", &s->id_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "feature-reads", &s->feature_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "jm-probe-reads", &s->jm_probe_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "control-jobs", &s->control_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-control", &s->vm3_control, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-control-accesses", &s->vm3_control_accesses, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-control-denied", &s->vm3_control_denied, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-foreign-job-irq-clear", &s->vm3_foreign_job_irq_clear, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-foreign-mmu-irq-clear", &s->vm3_foreign_mmu_irq_clear, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm4-job-mask-writes", &s->vm4_job_mask_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm4-mmu-mask-writes", &s->vm4_mmu_mask_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-oem-transcfg-updates", &s->vm3_oem_transcfg_updates, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-translation-mode-rejections", &s->vm3_translation_mode_rejections, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-fragment-attempts", &s->vm3_fragment_attempts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-fragment-decoded", &s->vm3_fragment_decoded, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-fragment-tiler-rejections", &s->vm3_fragment_tiler_rejections, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-empty-fragment-failures",
                                   &s->vm3_empty_fragment_failures, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-empty-fragment-profiles",
                                   &s->vm3_empty_fragment_profiles, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-empty-discard-validated",
                                   &s->vm3_empty_discard_validated, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-zero-resolves", &s->vm3_zero_resolves, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-single-zero-clears", &s->vm3_single_zero_clears,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-crc-invalidated-bytes", &s->vm3_crc_invalidated_bytes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-geometry-seen", &s->vm3_geometry_seen, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-geometry-samples", &s->vm3_geometry_samples, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-stock-fans", &s->vm3_stock_fans, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-color-fans", &s->vm3_color_fans, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-color-clipped-fans", &s->vm3_color_clipped_fans, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-faulted-empty-batches", &s->vm3_faulted_empty_batches, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-reset-tile-batches", &s->vm3_reset_tile_batches, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "tile-batch-count", &s->tile_batch_count, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-color-resolves", &s->vm3_color_resolves, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-color-pixels", &s->vm3_color_pixels, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-stock-fan-primitives", &s->vm3_stock_fan_primitives, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-fragment-matched-batches", &s->vm3_fragment_matched_batches, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-textured-attempts", &s->vm3_textured_attempts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-textured-resolves", &s->vm3_textured_resolves, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-textured-bytes", &s->vm3_textured_bytes, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-textured-nonzero-pixels", &s->vm3_textured_nonzero_pixels, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-textured-shaded-pixels", &s->vm3_textured_shaded_pixels, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-texture-loaded", &s->vm3_texture_loaded, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-texture-zero-opacity", &s->vm3_texture_zero_opacity, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-texture-rgb-texels", &s->vm3_texture_rgb_texels, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-texture-alpha-texels", &s->vm3_texture_alpha_texels, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-texture-rgb-samples", &s->vm3_texture_rgb_samples, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-nearest-texture-loads", &s->vm3_nearest_texture_loads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-single-tile-zero-pitch-loads",
                                   &s->vm3_single_tile_zero_pitch_loads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-nearest-rgb-texels", &s->vm3_nearest_rgb_texels, OBJ_PROP_FLAG_READ);
    object_property_add_str(obj, "vm3-center-target-history", auto9_vm3_center_history, NULL);
    object_property_add_uint32_ptr(obj, "vm3-linear-rgba-loads", &s->vm3_linear_rgba_loads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-linear-reverse-loads", &s->vm3_linear_reverse_loads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-linear-rgba-rgb-texels", &s->vm3_linear_rgba_rgb_texels, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-next-queued", &s->vm3_next_queued, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-next-dispatched", &s->vm3_next_dispatched, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-next-canceled", &s->vm3_next_canceled, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-next-as-mismatch", &s->vm3_next_as_mismatch, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-unresolved-dependencies", &s->vm3_unresolved_dependencies, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-vertex-attempts", &s->vm3_vertex_attempts, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-vertex-samples", &s->vm3_vertex_samples, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-stock-vertices-written", &s->vm3_stock_vertices_written, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-vertex3-jobs", &s->vm3_vertex3_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-vertex4-jobs", &s->vm3_vertex4_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-vertex448-jobs", &s->vm3_vertex448_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-vertex45-jobs", &s->vm3_vertex45_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vm3-compute-jobs", &s->vm3_compute_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "job-starts", &s->job_starts, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-desc-attempts", &s->vm3_desc_attempts, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-desc-completed", &s->vm3_desc_completed, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "vm3-desc-failed", &s->vm3_desc_failed, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "job-completions", &s->job_completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "job-failures", &s->job_failures, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "qnx-screen-leaf-read-maps",
                                   &s->qnx_screen_leaf_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "qnx-screen-leaf-write-maps",
                                   &s->qnx_screen_leaf_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "qnx-viewport-cross-page-jobs",
                                   &s->qnx_viewport_cross_page_jobs, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "job-descriptors-completed", &s->job_descriptors_completed,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "job-cache-flushes", &s->job_cache_flushes,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vertex-samples", &s->vertex_samples, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vertex-descriptors-completed",
                                   &s->vertex_descriptors_completed, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "vertices-written", &s->vertices_written, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "tiler-descriptors-completed",
                                   &s->tiler_descriptors_completed, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "primitives-binned", &s->primitives_binned, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fragment-descriptors-completed",
                                   &s->fragment_descriptors_completed, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fragment-pixels-shaded",
                                   &s->fragment_pixels_shaded, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "fragment-bytes-written",
                                   &s->fragment_bytes_written, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "fragment-te-unconditional",
                                   &s->fragment_te_unconditional, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-job-type", &s->last_job_type, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "latest-flush-id", &s->latest_flush_id, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "latest-flush-reads", &s->latest_flush_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "as-present", &s->as_present, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "as0-updates", &s->as0_updates, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "as0-locks", &s->as0_locks, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "as0-flushes", &s->as0_flushes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "as0-locked", &s->as0_locked, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "as0-active-ttbr", &s->as0_active_ttbr, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "as0-active-memattr", &s->as0_active_memattr, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "as0-active-transcfg", &s->as0_active_transcfg, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "as0-lock-region", &s->as0_lock_region, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "feature-last-offset", &s->feature_last_offset,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected", &s->last_rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected-write", &s->last_rejected_write,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "last-rejected-value", &s->last_rejected_value,
                                   OBJ_PROP_FLAG_READ);
}

static void auto9_mali_g3d1_finalize(Object *obj)
{
    Auto9MaliG3d1 *s = AUTO9_MALI_G3D1(obj);
    auto9_vm3_atlas_invalidate(s, 0, 0, 0, false);
    if (s->reset_timer) {
        timer_free(s->reset_timer);
    }
    if (s->cache_timer) {
        timer_free(s->cache_timer);
    }
}

static void auto9_mali_g3d1_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), auto9_mali_g3d1_reset);
}

static const TypeInfo auto9_mali_g3d1_type = {
    .name = TYPE_AUTO9_MALI_G3D1, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Auto9MaliG3d1), .instance_init = auto9_mali_g3d1_init,
    .class_init = auto9_mali_g3d1_class_init,
    .instance_finalize = auto9_mali_g3d1_finalize,
};

static void auto9_mali_g3d1_register(void)
{
    type_register_static(&auto9_mali_g3d1_type);
}
type_init(auto9_mali_g3d1_register)

static void auto9_mali_create(VirtMachineState *vms, bool g3d00)
{
    uint32_t id = g3d00 ? 0x72110000 : vms->exynos_mali_g3d1_id;
    if (!id) {
        return;
    }
    DeviceState *dev = qdev_new(TYPE_AUTO9_MALI_G3D1);
    Auto9MaliG3d1 *s = AUTO9_MALI_G3D1(dev);
    s->gpu_id = id;
    s->g3d00 = g3d00;
    s->discovery = g3d00 || vms->exynos_mali_g3d1_discovery;
    s->reset_irq = g3d00 || vms->exynos_mali_g3d1_reset_irq;
    s->config_enabled = g3d00 || vms->exynos_mali_g3d1_config;
    s->empty_power = g3d00 || vms->exynos_mali_g3d1_empty_power;
    s->jm_topology = g3d00 ? vms->exynos_qnx_g3d00_jm_topology :
                            vms->exynos_mali_g3d1_jm_topology;
    s->vm3_control = !g3d00 && vms->exynos_mali_g3d1_jm_topology == 2;
    s->as_present = (g3d00 && s->jm_topology) || s->vm3_control ? BIT(0) : 0;
    s->control_jobs = (g3d00 && s->as_present && vms->exynos_qnx_g3d00_jm_topology == 2) ||
                      s->vm3_control;
    if (s->reset_irq) {
        s->reset_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                      auto9_mali_g3d1_reset_complete, s);
        if (s->jm_topology && (!g3d00 || s->control_jobs)) {
            s->cache_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                          auto9_mali_g3d1_cache_complete, s);
        }
    }
    object_property_add_child(OBJECT(vms), g3d00 ? "exynos-mali-g3d00" : "exynos-mali-g3d1",
                              OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, g3d00 ? 0x19300000 : 0x19700000);
    if (s->reset_irq) {
        /* DT gives SPI indices, not architectural interrupt IDs. */
        for (unsigned i = 0; i < G3D1_IRQ_COUNT; i++) {
            sysbus_connect_irq(SYS_BUS_DEVICE(dev), i,
                               qdev_get_gpio_in(vms->gic, (g3d00 ? 247 : 255) + i));
        }
    }
}

static void auto9_mali_g3d1_create(VirtMachineState *vms)
{
    auto9_mali_create(vms, false);
    if (vms->exynos_qnx_g3d00) {
        auto9_mali_create(vms, true);
    }
}

