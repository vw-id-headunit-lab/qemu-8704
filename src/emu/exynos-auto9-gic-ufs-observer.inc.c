/* Host PPI telemetry; no CPU interface MMIO reads. Counters belong to a
 * single GIC object and survive guest reset until the QEMU process closes. */
enum {
    AUTO9_PPI27_RISE, AUTO9_PPI27_FALL, AUTO9_PPI27_PHYS_ACK, AUTO9_PPI27_VIRT_ACK,
    AUTO9_PPI27_PHYS_EOI, AUTO9_PPI27_VIRT_EOI, AUTO9_PPI27_PHYS_DIR, AUTO9_PPI27_VIRT_DIR,
    AUTO9_PPI27_COUNT,
};

static void auto9_gic_count_ppi27(GICState *s, int irq, unsigned core,
                                   unsigned event)
{
    if (irq == 27 && s->revision == 2 && s->virt_extn && core < s->num_cpu) {
        s->auto9_ppi27[core][event]++;
    }
}

static char *auto9_gic_ppi27_snapshot(Object *obj, Error **errp)
{
    static const char *const labels[AUTO9_PPI27_COUNT] = {
        "rise", "fall", "physical_ack", "virtual_ack", "physical_eoi_write",
        "virtual_eoi_write", "physical_dir_write", "virtual_dir_write",
    };
    GICState *s = ARM_GIC_COMMON(obj);
    if (s->revision != 2 || !s->virt_extn || s->num_irq <= 27) {
        error_setg(errp, "Auto9 PPI27 snapshot needs a virtual GICv2");
        return NULL;
    }
    GString *out = g_string_new("[");
    for (unsigned i = 0; i < s->num_cpu; i++) {
        unsigned cm = 1u << i, vi = GIC_NCPU + i, entries = 0;
        g_string_append_printf(out,
            "%s{\"cpu\":%u,\"level\":%u,\"pending\":%u,\"active\":%u,"
            "\"virtual_pending_id\":%u,\"counts\":{",
            i ? "," : "", i + s->first_cpu_index, !!(s->irq_state[27].level & cm),
            !!gic_test_pending(s, 27, cm), !!(s->irq_state[27].active & cm),
            s->current_pending[vi]);
        for (unsigned j = 0; j < AUTO9_PPI27_COUNT; j++) {
            g_string_append_printf(out, "%s\"%s\":%" PRIu64,
                                   j ? "," : "", labels[j], s->auto9_ppi27[i][j]);
        }
        g_string_append(out, "},\"lr\":[");
        for (unsigned j = 0; j < s->num_lrs; j++) {
            uint32_t lr = s->h_lr[j][i];
            if (GICH_LR_STATE(lr) != GICH_LR_STATE_INVALID &&
                (GICH_LR_VIRT_ID(lr) == 27 ||
                 (GICH_LR_HW(lr) && GICH_LR_PHYS_ID(lr) == 27))) {
                g_string_append_printf(out, "%s[%u,%u]", entries++ ? "," : "", j, lr);
            }
        }
        g_string_append(out, "]}");
    }
    g_string_append_c(out, ']');
    return g_string_free(out, false);
}

/* Read-only host observation of the original VH1 and DECON IRQs. Never
 * read IAR or use current_cpu from the management thread. */
static void auto9_gic_append_irq(GString *out, GICState *s, unsigned irq)
{
    g_string_append_printf(out,
        "{\"id\":%u,\"ctlr\":%u,\"level\":%u,\"enabled\":%u,"
        "\"pending_latch\":%u,\"active\":%u,\"target\":%u,"
        "\"priority\":%u,\"group\":%u,\"edge\":%u,\"cpus\":[",
        irq, s->ctlr, s->irq_state[irq].level, s->irq_state[irq].enabled,
        s->irq_state[irq].pending, s->irq_state[irq].active,
        s->irq_target[irq], s->priority2[irq - GIC_INTERNAL],
        s->irq_state[irq].group, s->irq_state[irq].edge_trigger);
    for (unsigned i = 0; i < s->num_cpu; i++) {
        unsigned vi = i + GIC_NCPU;
        unsigned matching = 0;
        g_string_append_printf(out,
            "%s{\"cpu\":%u,\"phys_ctlr\":%u,\"phys_pmr\":%u,"
            "\"phys_rpr\":%u,\"phys_pending\":%u,\"virt_ctlr\":%u,"
            "\"virt_pmr\":%u,\"virt_rpr\":%u,\"virt_pending\":%u,"
            "\"hcr\":%u,\"misr\":%u,\"effective_pending\":%u,\"lr\":[",
            i ? "," : "", i + s->first_cpu_index, s->cpu_ctlr[i],
            s->priority_mask[i], s->running_priority[i], s->current_pending[i],
            s->cpu_ctlr[vi], s->priority_mask[vi], s->running_priority[vi],
            s->current_pending[vi], s->h_hcr[i], s->h_misr[i],
            gic_test_pending(s, irq, 1u << i));
        for (unsigned j = 0; j < s->num_lrs; j++) {
            uint32_t lr = s->h_lr[j][i];

            if (GICH_LR_STATE(lr) != GICH_LR_STATE_INVALID &&
                (GICH_LR_VIRT_ID(lr) == irq ||
                 ((lr & (1u << 31)) && ((lr >> 10) & 0x3ff) == irq))) {
                g_string_append_printf(out, "%s[%u,%u]", matching++ ? "," : "",
                                       j, lr);
            }
        }
        g_string_append(out, "]}");
    }
    g_string_append(out, "]}");
}

static char *auto9_gic_vh1_snapshot(Object *obj, Error **errp)
{
    const unsigned irq = GIC_INTERNAL + 228;
    GICState *s = ARM_GIC_COMMON(obj);
    GString *out;

    if (s->revision != 2 || !s->virt_extn || irq >= s->num_irq) {
        error_setg(errp, "Auto9 VH1 snapshot needs the native virtual GICv2");
        return NULL;
    }
    out = g_string_new(NULL);
    auto9_gic_append_irq(out, s, irq);
    return g_string_free(out, false);
}

static char *auto9_gic_decon_snapshot(Object *obj, Error **errp)
{
    static const unsigned spis[] = { 116, 117, 121, 122, 130, 131 };
    GICState *s = ARM_GIC_COMMON(obj);
    GString *out;

    if (s->revision != 2 || !s->virt_extn ||
        GIC_INTERNAL + spis[G_N_ELEMENTS(spis) - 1] >= s->num_irq) {
        error_setg(errp, "Auto9 DECON snapshot needs the native virtual GICv2");
        return NULL;
    }
    out = g_string_new("[");
    for (unsigned i = 0; i < G_N_ELEMENTS(spis); i++) {
        if (i) {
            g_string_append_c(out, ',');
        }
        auto9_gic_append_irq(out, s, GIC_INTERNAL + spis[i]);
    }
    g_string_append_c(out, ']');
    return g_string_free(out, false);
}

static char *auto9_gic_eqos_snapshot(Object *obj, Error **errp)
{
    GICState *s = ARM_GIC_COMMON(obj);
    if (s->revision != 2 || !s->virt_extn || GIC_INTERNAL + 327 >= s->num_irq) {
        error_setg(errp, "Auto9 EQOS snapshot needs the native virtual GICv2");
        return NULL;
    }
    GString *out = g_string_new("[");
    for (unsigned spi = 312; spi <= 327; spi++) {
        if (spi != 312) {
            g_string_append_c(out, ',');
        }
        auto9_gic_append_irq(out, s, GIC_INTERNAL + spi);
    }
    g_string_append_c(out, ']');
    return g_string_free(out, false);
}
