/* SPDX-License-Identifier: GPL-2.0-or-later
 * Original QNX CAL lpm_init op1/op3 configuration retention only.
 * Source: 1936013a8c897dcae7b11503634c445b68d9c63dbbaf9aa1b4d5b404bef69ceb
 * Explicit profile, cold zero assumed. No clock/power/IRQ/ready side effects.
 */
static bool exynos_auto9_cal_init_enabled;
static const uint64_t exynos_auto9_cal_cmu_config[] = {
    0x10020840, /* PERIS_HCHGEN_CLKMUX */
    0x19200820, /* G3D00_SHORTSTOP */
    0x19200830, /* G3D00_CLKDIVSTEP */
    0x19400820, /* G3D01_SHORTSTOP */
    0x19400830, /* G3D01_CLKDIVSTEP */
    0x19600820, /* G3D1_SHORTSTOP */
    0x19600830, /* G3D1_CLKDIVSTEP */
    0x19c00850, /* AUD_HCHGEN_CLKMUX */
    0x19c00a00, /* AUD_SPARE0 */
    0x1b030820, /* CORE_SHORTSTOP */
    0x1b030850, /* CORE_HCHGEN_CLKMUX_CMUREF */
    0x1b200850, /* BUSMC_HCHGEN_CLKMUX_CMUREF */
    0x1b240850, /* CMU_HCHGEN_CLKMUX_CMUREF */
    0x1b400850, /* MIF0_HCHGEN_CLKMUX_CMUREF */
    0x1b600850, /* MIF1_HCHGEN_CLKMUX_CMUREF */
    0x1bf00850, /* BUSC_HCHGEN_CLKMUX_CMUREF */
    0x1c400850, /* MIF2_HCHGEN_CLKMUX_CMUREF */
    0x1c600850, /* MIF3_HCHGEN_CLKMUX_CMUREF */
    0x1d020820, /* CPUCL0_SHORTSTOP_CORE */
    0x1d020824, /* CPUCL0_SHORTSTOP_CLUSTER */
    0x1d020828, /* CPUCL0_SHORTSTOP_DBG */
    0x1d020830, /* CPUCL0_CLKDIVSTEP_CORE */
    0x1d020840, /* CPUCL0_CLKDIVSTEP_CLUSTER */
    0x1d020850, /* CPUCL0_HCHGEN_CLKMUX_CMUREF */
    0x1d020854, /* CPUCL0_HCHGEN_CLKMUX_CPU */
    0x1d0230b4, /* QCH_CON_CLUSTER0_QCH_SCLK */
    0x1d120820, /* CPUCL1_SHORTSTOP_CORE */
    0x1d120824, /* CPUCL1_SHORTSTOP_CLUSTER */
    0x1d120830, /* CPUCL1_CLKDIVSTEP_CORE */
    0x1d120840, /* CPUCL1_CLKDIVSTEP_CLUSTER */
    0x1d120850, /* CPUCL1_HCHGEN_CLKMUX_CMUREF */
    0x1d120854, /* CPUCL1_HCHGEN_CLKMUX_CPU */
    0x1d123054, /* QCH_CON_CLUSTER1_QCH_SCLK */
    0x1d700830, /* DNC_CLKDIVSTEP_CPU */
    0x1d700840, /* DNC_CLKDIVSTEP_BUSD */
    0x1dc00830, /* NPU00_CLKDIVSTEP */
    0x1dd00830, /* NPU10_CLKDIVSTEP */
    0x1de00830, /* NPU01_CLKDIVSTEP */
    0x1df00830, /* NPU11_CLKDIVSTEP */
};
static const uint32_t exynos_auto9_cal_pmu_offsets[] = {
    0x284, /* TCXO_DURATION3 */
    0x30c, /* PS_HOLD_CONTROL */
    0xa08, /* BURNIN_CTRL */
    0xe00, /* EXT_REGULATOR_MAIN_DURATION */
    0xe08, /* EXT_REGULATOR_G3D0_DURATION */
    0xe0c, /* EXT_REGULATOR_G3D1_DURATION */
    0x3a44, /* SYSTEM_INT_EN */
};
static const uint64_t exynos_auto9_cal_sysreg_addresses[] = {
    0x10030104, /* SYSREG_PERIS_BUS_COMPONENT_DRCG_EN */
    0x100a0104, /* SYSREG_PERIS_1_BUS_COMPONENT_DRCG_EN */
    0x100b0104, /* SYSREG_PERIS_2_BUS_COMPONENT_DRCG_EN */
    0x10220104, /* SYSREG_PERIC0_BUS_COMPONENT_DRCG_EN */
    0x10420104, /* SYSREG_APM_BUS_COMPONENT_DRCG_EN */
    0x10820104, /* SYSREG_PERIC1_BUS_COMPONENT_DRCG_EN */
    0x17020104, /* SYSREG_FSYS1_BUS_COMPONENT_DRCG_EN */
    0x17730104, /* SYSREG_FSYS0_BUS_COMPONENT_DRCG_EN */
    0x17c20104, /* SYSREG_FSYS2_BUS_COMPONENT_DRCG_EN */
    0x18010104, /* SYSREG_ISPB0_BUS_COMPONENT_DRCG_EN */
    0x18210104, /* SYSREG_ISPB1_BUS_COMPONENT_DRCG_EN */
    0x18a20104, /* SYSREG_DPTX_BUS_COMPONENT_DRCG_EN */
    0x18c10104, /* SYSREG_DPUM_BUS_COMPONENT_DRCG_EN */
    0x19220104, /* SYSREG_G3D00_BUS_COMPONENT_DRCG_EN */
    0x19420104, /* SYSREG_G3D01_BUS_COMPONENT_DRCG_EN */
    0x19620104, /* SYSREG_G3D1_BUS_COMPONENT_DRCG_EN */
    0x19810104, /* SYSREG_MFC_BUS_COMPONENT_DRCG_EN */
    0x19ae0104, /* SYSREG_G2D_BUS_COMPONENT_DRCG_EN */
    0x19c10104, /* SYSREG_AUD_BUS_COMPONENT_DRCG_EN */
    0x1a010104, /* SYSREG_TAA0_BUS_COMPONENT_DRCG_EN */
    0x1a210104, /* SYSREG_TAA1_BUS_COMPONENT_DRCG_EN */
    0x1a810104, /* SYSREG_ACC_BUS_COMPONENT_DRCG_EN */
    0x1a81010c, /* SYSREG_ACC_LH_DRCG_EN */
    0x1aa10104, /* SYSREG_DPUS0_BUS_COMPONENT_DRCG_EN */
    0x1ac10104, /* SYSREG_DPUS1_BUS_COMPONENT_DRCG_EN */
    0x1b020104, /* SYSREG_CORE_BUS_COMPONENT_DRCG_EN */
    0x1b220104, /* SYSREG_BUSMC_BUS_COMPONENT_DRCG_EN */
    0x1b420104, /* SYSREG_MIF0_BUS_COMPONENT_DRCG_EN */
    0x1b620104, /* SYSREG_MIF1_BUS_COMPONENT_DRCG_EN */
    0x1bf20104, /* SYSREG_BUSC_BUS_COMPONENT_DRCG_EN */
    0x1c420104, /* SYSREG_MIF2_BUS_COMPONENT_DRCG_EN */
    0x1c620104, /* SYSREG_MIF3_BUS_COMPONENT_DRCG_EN */
    0x1d000104, /* SYSREG_CPUCL0_BUS_COMPONENT_DRCG_EN */
    0x1d00010c, /* SYSREG_CPUCL0_BUS_COMPONENT_DRCG_EN_XIU */
    0x1d100104, /* SYSREG_CPUCL1_BUS_COMPONENT_DRCG_EN */
    0x1d720104, /* SYSREG_DNC_BUS_COMPONENT_DRCG_EN */
    0x1d72010c, /* SYSREG_DNC_BUS_COMPONENT_DRCG_EN_1 */
    0x1d720110, /* SYSREG_DNC_BUS_COMPONENT_DRCG_EN_2 */
    0x1dc20104, /* SYSREG_NPU00_BUS_COMPONENT_DRCG_EN */
    0x1dd20104, /* SYSREG_NPU01_BUS_COMPONENT_DRCG_EN */
    0x1de20104, /* SYSREG_NPU10_BUS_COMPONENT_DRCG_EN */
    0x1df20104, /* SYSREG_NPU11_BUS_COMPONENT_DRCG_EN */
};

static bool exynos_auto9_cal_pmu_config(hwaddr off)
{
    if (!exynos_auto9_cal_init_enabled) { return false; }
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_cal_pmu_offsets); i++) {
        if (exynos_auto9_cal_pmu_offsets[i] == off) { return true; }
    }
    return false;
}
static bool exynos_auto9_cal_cmu_declared(hwaddr address)
{
    if (!exynos_auto9_cal_init_enabled) { return false; }
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_cal_cmu_config); i++) {
        if (exynos_auto9_cal_cmu_config[i] == address) { return true; }
    }
    return false;
}
#define TYPE_EXYNOS_AUTO9_CAL_INIT "exynos-auto9.cal-init"
OBJECT_DECLARE_SIMPLE_TYPE(ExynosAuto9CalInit, EXYNOS_AUTO9_CAL_INIT)
typedef struct {
    MemoryRegion mr;
    ExynosAuto9CalInit *owner;
    unsigned index;
} ExynosAuto9CalReg;
struct ExynosAuto9CalInit {
    SysBusDevice parent_obj;
    ExynosAuto9CalReg regions[G_N_ELEMENTS(exynos_auto9_cal_sysreg_addresses)];
    uint32_t words[G_N_ELEMENTS(exynos_auto9_cal_sysreg_addresses)];
    uint64_t reads, writes;
};
static uint64_t exynos_auto9_cal_reg_read(void *opaque, hwaddr off, unsigned size)
{
    ExynosAuto9CalReg *r = opaque;
    r->owner->reads++;
    return r->owner->words[r->index];
}
static void exynos_auto9_cal_reg_write(void *opaque, hwaddr off, uint64_t value, unsigned size)
{
    ExynosAuto9CalReg *r = opaque;
    r->owner->writes++;
    r->owner->words[r->index] = value;
}
static const MemoryRegionOps exynos_auto9_cal_reg_ops = {
    .read = exynos_auto9_cal_reg_read, .write = exynos_auto9_cal_reg_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};
static void exynos_auto9_cal_reg_reset(DeviceState *dev)
{
    ExynosAuto9CalInit *s = EXYNOS_AUTO9_CAL_INIT(dev);
    memset(s->words, 0, sizeof(s->words)); s->reads = s->writes = 0;
}
static void exynos_auto9_cal_reg_init(Object *obj)
{
    ExynosAuto9CalInit *s = EXYNOS_AUTO9_CAL_INIT(obj);
    for (unsigned i = 0; i < G_N_ELEMENTS(s->regions); i++) {
        ExynosAuto9CalReg *r = &s->regions[i]; r->owner = s; r->index = i;
        memory_region_init_io(&r->mr, obj, &exynos_auto9_cal_reg_ops, r, "auto9-cal-sysreg", 4);
    }
    object_property_add_uint64_ptr(obj, "config-reads", &s->reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "config-writes", &s->writes, OBJ_PROP_FLAG_READ);
}
static void exynos_auto9_cal_reg_class(ObjectClass *klass, const void *data)
{
    device_class_set_legacy_reset(DEVICE_CLASS(klass), exynos_auto9_cal_reg_reset);
}
static const TypeInfo exynos_auto9_cal_reg_type = {
    .name = TYPE_EXYNOS_AUTO9_CAL_INIT, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ExynosAuto9CalInit),
    .instance_init = exynos_auto9_cal_reg_init, .class_init = exynos_auto9_cal_reg_class,
};
static void exynos_auto9_cal_reg_register(void) { type_register_static(&exynos_auto9_cal_reg_type); }
type_init(exynos_auto9_cal_reg_register)
static void exynos_auto9_cal_init_create(VirtMachineState *vms, bool enabled)
{
    exynos_auto9_cal_init_enabled = enabled;
    if (!enabled) { return; }
    DeviceState *dev = qdev_new(TYPE_EXYNOS_AUTO9_CAL_INIT);
    object_property_add_child(OBJECT(vms), "exynos-cal-init", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < G_N_ELEMENTS(exynos_auto9_cal_sysreg_addresses); i++) {
        /* More than32 sparse words: direct MemoryRegions, not SysBus MMIO slots. */
        ExynosAuto9CalInit *s = EXYNOS_AUTO9_CAL_INIT(dev);
        memory_region_add_subregion(get_system_memory(), exynos_auto9_cal_sysreg_addresses[i], &s->regions[i].mr);
    }
}
