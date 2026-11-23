/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original QNX CAL domain table (SHA1936013a...ceb), not a silicon PMU spec.
 * Only explicit virtual-sequencer-v1 implements logical power transitions.
 * Supply-good is declared virtual environment; analog rails/clocks/GPU/DP
 * training and physical resets are NOT implemented by this child device.
 */
#include "qapi/visitor.h"
#define TYPE_EXYNOS_AUTO9_DOMAINS "exynos-auto9.pmu-domains"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9Domains, EXYNOS_AUTO9_DOMAINS)
static const uint32_t exynos_auto9_domain_configs[20] = {
    0x1980, 0x1d00, 0x1d80, 0x1e00, 0x1e80, 0x1f00, 0x1f80,
    0x2000, 0x2080, 0x2100, 0x2880, 0x2780, 0x2800, 0x2680,
    0x2700, 0x2480, 0x2900, 0x2500, 0x2580, 0x2280,
};
typedef struct ExynosAuto9DomainTimer {
    ExynosAuto9Domains *owner;
    QEMUTimer *deadline;
    unsigned index;
} ExynosAuto9DomainTimer;
struct ExynosAuto9Domains {
    SysBusDevice parent_obj;
    ExynosAuto9DomainTimer timers[20];
    qemu_irq outputs[20];
    uint32_t profile, transition_ns, supply, request, active, pending;
    uint32_t out_words[3];
    uint32_t reads, writes, rejected, completions, cancellations, losses;
};
/* Fixed20-entry allowlist, O(20), never a writable register/RAM aperture. */
static int exynos_auto9_domain_index(hwaddr off)
{
    for (unsigned i = 0; i < ARRAY_SIZE(exynos_auto9_domain_configs); i++) {
        if (off == exynos_auto9_domain_configs[i] ||
            off == exynos_auto9_domain_configs[i] + 4) { return i; }
    }
    return -1;
}
static int exynos_auto9_domain_out(hwaddr off)
{
    return off == 0x2520 ? 0 : off == 0x25a0 ? 1 : off == 0x22a0 ? 2 : -1;
}
static bool exynos_auto9_domain_address(hwaddr off)
{
    /* Route aliases for validation too, but only OUT atomic aliases are
     * proven by original op14/op15. Configuration/status aliases fault. */
    hwaddr target = off & 0x3fff;
    return off < 0x10000 && (exynos_auto9_domain_index(target) >= 0 ||
                                  exynos_auto9_domain_out(target) >= 0);
}
static void exynos_auto9_domain_drive(ExynosAuto9Domains *s, unsigned i, bool level)
{
    uint32_t bit = 1U << i;
    if (level) { s->active |= bit; } else { s->active &= ~bit; }
    qemu_set_irq(s->outputs[i], level);
}
static void exynos_auto9_domain_schedule(ExynosAuto9Domains *s, unsigned i)
{
    uint32_t bit = 1U << i;
    if (s->pending & bit) { s->cancellations++; }
    timer_del(s->timers[i].deadline);
    s->pending &= ~bit;
    if (s->profile != 1 || !((s->request ^ s->active) & bit) ||
        ((s->request & bit) && !(s->supply & bit))) { return; }
    s->pending |= bit;
    timer_mod(s->timers[i].deadline,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->transition_ns);
}
static void exynos_auto9_domain_expire(void *opaque)
{
    ExynosAuto9DomainTimer *t = opaque;
    ExynosAuto9Domains *s = t->owner;
    uint32_t bit = 1U << t->index;
    if (!(s->pending & bit)) { return; }
    s->pending &= ~bit;
    if (s->profile != 1 || ((s->request & bit) && !(s->supply & bit))) { return; }
    exynos_auto9_domain_drive(s, t->index, !!(s->request & bit));
    s->completions++;
}
static void exynos_auto9_domain_supply(void *opaque, int line, int level)
{
    ExynosAuto9Domains *s = opaque;
    uint32_t bit = 1U << line;
    if (!!(s->supply & bit) == !!level) { return; }
    if (level) { s->supply |= bit; } else {
        s->supply &= ~bit;
        if (s->active & bit) {
            s->losses++;
            exynos_auto9_domain_drive(s, line, false);
        }
    }
    exynos_auto9_domain_schedule(s, line);
}
static MemTxResult exynos_auto9_domain_access(ExynosAuto9Domains *s, hwaddr off,
                                             uint64_t *value, bool write)
{
    unsigned alias = off & 0xc000;
    hwaddr target = off & 0x3fff;
    int index = exynos_auto9_domain_index(target);
    int out = exynos_auto9_domain_out(target);
    if (s->profile != 1) { goto reject; }
    if (index >= 0) {
        uint32_t bit = 1U << index;
        bool status = target == exynos_auto9_domain_configs[index] + 4;
        if (alias || (write && (status || (*value & ~1ULL)))) { goto reject; }
        if (write) {
            bool before = !!(s->request & bit);
            if (*value) { s->request |= bit; } else { s->request &= ~bit; }
            /* Idempotent software writes do not push the deadline forever. */
            if (before != !!*value) { exynos_auto9_domain_schedule(s, index); }
        } else { *value = !!((status ? s->active : s->request) & bit); }
    } else if (out >= 0) {
        if (write) {
            if (!alias && !(*value & ~4ULL)) { s->out_words[out] = *value; }
            else if ((alias == 0xc000 || alias == 0x8000) && *value == 2) {
                if (alias == 0xc000) { s->out_words[out] |= 4; }
                else { s->out_words[out] &= ~4U; }
            } else { goto reject; }
        } else {
            if (alias) { goto reject; }
            *value = s->out_words[out];
        }
    } else { goto reject; }
    if (write) { s->writes++; } else { s->reads++; }
    return MEMTX_OK;
reject:
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, "Auto9 PMU domain unsupported %s offset=0x%llx\n",
                  write ? "write" : "read", (unsigned long long)off);
    return MEMTX_DECODE_ERROR;
}
static void exynos_auto9_domain_get(Object *obj, Visitor *v, const char *name,
                                   void *opaque, Error **errp)
{
    ExynosAuto9Domains *s = EXYNOS_AUTO9_DOMAINS(obj);
    uint32_t value = !strcmp(name, "profile") ? s->profile :
        !strcmp(name, "transition-ns") ? s->transition_ns :
        !strcmp(name, "waiting-supply-mask") ? s->request & ~s->supply : s->supply;
    visit_type_uint32(v, name, &value, errp);
}
static void exynos_auto9_domain_set(Object *obj, Visitor *v, const char *name,
                                   void *opaque, Error **errp)
{
    ExynosAuto9Domains *s = EXYNOS_AUTO9_DOMAINS(obj);
    uint32_t value;
    if (!visit_type_uint32(v, name, &value, errp)) { return; }
    if (!strcmp(name, "supply-good-mask")) {
        if (value & ~0xfffffU) { error_setg(errp, "Require20-bit virtual supply mask"); return; }
        for (unsigned i = 0; i < 20; i++) { exynos_auto9_domain_supply(s, i, (value >> i) & 1); }
    } else {
        if (s->request || s->active || s->pending) {
            error_setg(errp, "Domain policy changes require no live or requested domain"); return;
        }
        if (!strcmp(name, "profile")) {
            if (value > 1) { error_setg(errp, "Require domain profile0 or1"); return; }
            s->profile = value;
        } else {
            if (!value || value > 1000000000) { error_setg(errp, "Require1..1e9 virtual nanoseconds"); return; }
            s->transition_ns = value;
        }
    }
}
static void exynos_auto9_domain_reset(DeviceState *dev)
{
    ExynosAuto9Domains *s = EXYNOS_AUTO9_DOMAINS(dev);
    /* Environment/profile survive reset; actual request/status/timers do not. */
    s->request = s->active = s->pending = 0;
    memset(s->out_words, 0, sizeof(s->out_words));
    s->reads = s->writes = s->rejected = s->completions = s->cancellations = s->losses = 0;
    for (unsigned i = 0; i < 20; i++) {
        timer_del(s->timers[i].deadline);
        qemu_set_irq(s->outputs[i], 0);
    }
}
static void exynos_auto9_domain_init(Object *obj)
{
    ExynosAuto9Domains *s = EXYNOS_AUTO9_DOMAINS(obj);
    s->transition_ns = 1000; /* Declared virtual policy, NOT measured timing. */
    for (unsigned i = 0; i < 20; i++) {
        s->timers[i].owner = s; s->timers[i].index = i;
        s->timers[i].deadline = timer_new_ns(QEMU_CLOCK_VIRTUAL, exynos_auto9_domain_expire, &s->timers[i]);
    }
    qdev_init_gpio_out_named(DEVICE(obj), s->outputs, "domain-active", 20);
    qdev_init_gpio_in_named(DEVICE(obj), exynos_auto9_domain_supply, "domain-supply-good", 20);
    const char *inputs[] = {"profile", "transition-ns", "supply-good-mask"};
    for (unsigned i = 0; i < ARRAY_SIZE(inputs); i++) {
        object_property_add(obj, inputs[i], "uint32", exynos_auto9_domain_get, exynos_auto9_domain_set, NULL, NULL);
    }
    object_property_add(obj, "waiting-supply-mask", "uint32", exynos_auto9_domain_get, NULL, NULL, NULL);
    object_property_add_uint32_ptr(obj, "request-mask", &s->request, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "active-mask", &s->active, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "pending-mask", &s->pending, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "register-writes", &s->writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "rejected-accesses", &s->rejected, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "completions", &s->completions, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "cancellations", &s->cancellations, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "supply-losses", &s->losses, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_domain_finalize(Object *obj)
{
    ExynosAuto9Domains *s = EXYNOS_AUTO9_DOMAINS(obj);
    for (unsigned i = 0; i < 20; i++) { timer_free(s->timers[i].deadline); }
}
static void exynos_auto9_domain_class_init(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_domain_reset);
}
static const TypeInfo exynos_auto9_domain_type = {
    .name = TYPE_EXYNOS_AUTO9_DOMAINS, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9Domains), .instance_init = exynos_auto9_domain_init,
    .instance_finalize = exynos_auto9_domain_finalize, .class_init = exynos_auto9_domain_class_init,
};
static void exynos_auto9_domain_register(void) { type_register_static(&exynos_auto9_domain_type); }
type_init(exynos_auto9_domain_register)
