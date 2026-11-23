/*
 *
 * Copyright (c) 2015 Linaro Limited
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms and conditions of the GNU General Public License,
 * version 2 or later, as published by the Free Software Foundation.
 *
 * This program is distributed in the hope it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 * Emulate a virtual board which works by passing Linux all the information
 * it needs about what devices are present via the device tree.
 * There are some restrictions about what we can do here:
 *  + we can only present devices whose Linux drivers will work based
 *    purely on the device tree with no platform data at all
 *  + we want to present a very stripped-down minimalist platform,
 *    both because this reduces the security attack surface from the guest
 *    and also because it reduces our exposure to being broken when
 *    the kernel updates its device tree bindings and requires further
 *    information in a device binding that we aren't providing.
 * This is essentially the same approach kvmtool uses.
 */

#ifndef QEMU_ARM_VIRT_H
#define QEMU_ARM_VIRT_H

#include "exec/hwaddr.h"
#include "qemu/notify.h"
#include "hw/core/boards.h"
#include "hw/acpi/ghes.h"
#include "hw/arm/boot.h"
#include "hw/arm/bsa.h"
#include "hw/block/flash.h"
#include "hw/cxl/cxl.h"
#include "system/kvm.h"
#include "hw/intc/arm_gicv3_common.h"
#include "qom/object.h"
#include "hw/core/cpu.h"

#define NUM_GICV2M_SPIS       64
#define NUM_VIRTIO_TRANSPORTS 32
#define NUM_SMMU_IRQS          4

/* See Linux kernel arch/arm64/include/asm/pvclock-abi.h */
#define PVTIME_SIZE_PER_CPU 64

/* GPIO pins */
#define GPIO_PIN_POWER_BUTTON  3

#define CPU_MAX_CACHES 16

enum {
    VIRT_FLASH,
    VIRT_MEM,
    VIRT_CPUPERIPHS,
    VIRT_GIC_DIST,
    VIRT_GIC_CPU,
    VIRT_GIC_V2M,
    VIRT_GIC_HYP,
    VIRT_GIC_VCPU,
    VIRT_GIC_ITS,
    VIRT_GIC_REDIST,
    VIRT_GICV5_IRS_S,
    VIRT_GICV5_IRS_NS,
    VIRT_GICV5_IRS_EL3,
    VIRT_GICV5_IRS_REALM,
    VIRT_GICV5_ITS_S,
    VIRT_GICV5_ITS_NS,
    VIRT_GICV5_ITS_EL3,
    VIRT_GICV5_ITS_REALM,
    VIRT_GICV5_ITS_TR_S,
    VIRT_GICV5_ITS_TR_NS,
    VIRT_GICV5_ITS_TR_EL3,
    VIRT_GICV5_ITS_TR_REALM,
    VIRT_SMMU,
    VIRT_UART0,
    VIRT_MMIO,
    VIRT_RTC,
    VIRT_FW_CFG,
    VIRT_PCIE,
    VIRT_PCIE_MMIO,
    VIRT_PCIE_PIO,
    VIRT_PCIE_ECAM,
    VIRT_PLATFORM_BUS,
    VIRT_GPIO,
    VIRT_UART1,
    VIRT_SECURE_MEM,
    VIRT_SECURE_GPIO,
    VIRT_PCDIMM_ACPI,
    VIRT_ACPI_GED,
    VIRT_NVDIMM_ACPI,
    VIRT_PVTIME,
    VIRT_ACPI_PCIHP,
    VIRT_GWDT_WS0,
    VIRT_GWDT_REFRESH,
    VIRT_GWDT_CONTROL,
    VIRT_LOWMEMMAP_LAST,
};

/* indices of IO regions located after the RAM */
enum {
    VIRT_HIGH_GIC_REDIST2 =  VIRT_LOWMEMMAP_LAST,
    VIRT_CXL_HOST,
    VIRT_HIGH_PCIE_ECAM,
    VIRT_HIGH_PCIE_MMIO,
};

typedef enum VirtIOMMUType {
    VIRT_IOMMU_NONE,
    VIRT_IOMMU_SMMUV3,
    VIRT_IOMMU_VIRTIO,
} VirtIOMMUType;

typedef enum VirtMSIControllerType {
    VIRT_MSI_CTRL_NONE,
    /* This value is overridden at runtime.*/
    VIRT_MSI_CTRL_AUTO,
    /* Legacy option: its=off provides a GICv2m when using GICv2 */
    VIRT_MSI_LEGACY_OPT_ITS_OFF,
    VIRT_MSI_CTRL_GICV2M,
    VIRT_MSI_CTRL_ITS,
} VirtMSIControllerType;

typedef enum VirtGICType {
    VIRT_GIC_VERSION_MAX = 0,
    VIRT_GIC_VERSION_HOST = 1,
    /* The concrete GIC values have to match the GIC version number */
    VIRT_GIC_VERSION_2 = 2,
    VIRT_GIC_VERSION_3 = 3,
    VIRT_GIC_VERSION_4 = 4,
    VIRT_GIC_VERSION_5 = 5,
    VIRT_GIC_VERSION_NOSEL,
} VirtGICType;

#define VIRT_GIC_VERSION_2_MASK BIT(VIRT_GIC_VERSION_2)
#define VIRT_GIC_VERSION_3_MASK BIT(VIRT_GIC_VERSION_3)
#define VIRT_GIC_VERSION_4_MASK BIT(VIRT_GIC_VERSION_4)
#define VIRT_GIC_VERSION_5_MASK BIT(VIRT_GIC_VERSION_5)

struct VirtMachineClass {
    MachineClass parent;
    bool no_tcg_its;
    bool no_highmem_compact;
    bool no_kvm_steal_time;
    bool acpi_expose_flash;
    bool no_secure_gpio;
    /* Machines < 6.2 have no support for describing cpu topology to guest */
    bool no_cpu_topology;
    bool no_tcg_lpa2;
    bool no_ns_el2_virt_timer_irq;
    bool no_nested_smmu;
    /* HVF specific: support for kernel-irqchip=on introduced in QEMU 11.1 */
    bool hvf_no_kernel_irqchip_default;
};

struct VirtMachineState {
    MachineState parent;
    Notifier machine_done;
    DeviceState *platform_bus_dev;
    FWCfgState *fw_cfg;
    PFlashCFI01 *flash[2];
    bool secure;
    bool exynos_vlm; /* Local 8704 virtual-platform extension, opt-in. */
    bool exynos_acpm; /* Separate original ACPM management CPU, opt-in. */
    bool exynos_ufs; /* Native-address UFS standard transport, opt-in. */
    uint64_t exynos_ufs_mailbox; /* PH/VH polling mailbox */
    uint64_t exynos_ufs_vh; /* Independent behavior-level VH controllers */
    uint64_t exynos_qnx_dsi; /* Original VM2 DSIM bootstrap. */
    uint64_t exynos_linux_usb_phy; /* Original VM3 USB2 PHY controls. */
    uint64_t exynos_linux_dwc3; /* 0=off, 1=upstream DWC3 without DMA. */
    uint64_t exynos_linux_mfc; /* Original VM3 MFC cold reset. */
    uint64_t exynos_qnx_dpp_irq; /* Original VM2 DPP/IDMA interrupt words. */
    uint64_t exynos_linux_dpp_irq; /* Original VM3 DPP/IDMA interrupt words */
    uint64_t exynos_ufs_host0; /* 0=uniform; 1=physical HCI VGEN entry0. */
    uint64_t exynos_ufs_prelink; /* 0=off, 1=original QNX prelink config. */
    bool exynos_ufs_shareability; /* Single UFS0 FSYS2 word, opt-in. */
    bool exynos_ufs_phy_isolation; /* Single UFS0 PMU config bit, opt-in. */
    uint64_t exynos_acpm_hz; /* Explicit virtual, not measured silicon clock. */
    uint64_t exynos_pscdc_profile; /* 0=strict, 1=explicit virtual-oem-v1. */
    uint64_t exynos_pscdc_cycles; /* Virtual transaction latency, not silicon timing. */
    uint64_t exynos_pmu_profile; /* 0=absent, 1=explicit virtual-cold-v1. */
    uint64_t exynos_tmu_profile; /* 0=absent, 1=legacy ADC, 2=QNX finite controller. */
    uint64_t exynos_lk_pwm_profile; /* 0=absent, 1=upstream virtual 24MHz PWM. */
    uint64_t exynos_mcu_spi6_startup; /* 0=absent, 1=source-pinned SPI6 startup. */
    uint64_t exynos_g2d_reset;
    uint64_t exynos_scaler_reset;
    uint64_t exynos_abox_gic;
    uint64_t exynos_abox_sysmmu;
    uint64_t exynos_abox_axiuser;
    uint64_t exynos_abox_dsp_off;
    uint64_t exynos_linux_usi_uart;
    uint64_t exynos_qnx_bts;
    uint64_t exynos_qnx_g3d00;
    uint64_t exynos_qnx_g3d00_jm_topology; /* 1=AS0, 2=AS0 and control jobs. */
    uint64_t exynos_mali_g3d1_empty_power;
    uint64_t exynos_mali_g3d1_jm_topology;
    uint64_t exynos_mali_g3d1_config;
    uint64_t exynos_mali_g3d1_reset_irq;
    uint64_t exynos_mali_g3d1_discovery; /* opt-in read-only feature aperture */
    uint64_t exynos_mali_g3d1_id; /* 0=absent; virtual G76 ID otherwise. */
    uint64_t exynos_pcie2_atu; /* 0=off; 1=unrolled iATU and empty secondary bus. */
    uint64_t exynos_pcie2_dbi; /* 0=off; 1=original root config latches only. */
    uint64_t exynos_pcie2_pcs; /* 0=off; 1=source-bound cold no-endpoint path. */
    uint64_t exynos_pcie2_phy; /* 0=off; 1=PMU +0x71c bit0. */
    uint64_t exynos_pcie2_elbi; /* 0=off; 1=VM3 cold ELBI. */
    uint64_t exynos_decon_window; /* 0=off; 1=VM4 WOD window registers. */
    uint64_t exynos_linux_decon_window; /* 0=off; 1=VM3 WOD windows. */
    uint64_t exynos_dp0_aux_sink; /* 0=off, 1=local virtual HPD/AUX sink. */
    uint64_t exynos_dp0_irq; /* 0=off, 1=VM2 HPD control and plug IRQ words. */
    uint64_t exynos_dp0_link_config; /* 0=off, 1=VM2 local LINK configuration. */
    uint64_t exynos_dp0_phy; /* 0=off, 1=VM2 local DP0 PHY startup. */
    uint64_t exynos_dp0_reset; /* 0=off, 1=VM2 DP0 LINK reset. */
    uint64_t exynos_dpp_irq; /* 0=off, 1=original VM4 IRQ register words. */
    uint64_t exynos_linux_sysmmu;
    uint64_t exynos_sysmmu_discovery; /* 0=off, 1=virtual v7 identity. */
    uint64_t exynos_eqos_mac; /* 0 = absent, otherwise explicit local MAC. */
    uint64_t exynos_cal_init_profile; /* 0=strict, 1=source-bound CAL config retention. */
    char *exynos_acpm_firmware; /* Verified scatter-load bytes, not runtime snapshot. */
    bool exynos_acpm_doorbells; /* Diagnostic until the downstream peripherals are modeled. */
    MemoryRegion exynos_usi;
    DeviceState *exynos_uart;
    uint32_t exynos_usi_con;
    uint32_t exynos_usi_option;
    bool highmem;
    bool highmem_compact;
    bool highmem_cxl;
    bool highmem_ecam;
    bool highmem_mmio;
    bool highmem_redists;
    bool tcg_its;
    bool virt;
    bool ras;
    bool mte;
    bool dtb_randomness;
    bool second_ns_uart_present;
    OnOffAuto acpi;
    VirtGICType gic_version;
    VirtIOMMUType iommu;
    bool default_bus_bypass_iommu;
    VirtMSIControllerType msi_controller;
    uint16_t virtio_iommu_bdf;
    struct arm_boot_info bootinfo;
    MemMapEntry *memmap;
    char *pciehb_nodename;
    const int *irqmap;
    int fdt_size;
    uint32_t clock_phandle;
    uint32_t gic_phandle;
    uint32_t msi_phandle;
    uint32_t iommu_phandle;
    uint32_t *cpu_phandles;
    int psci_conduit;
    uint8_t virtio_transports;
    hwaddr highest_gpa;
    DeviceState *gic;
    DeviceState *acpi_dev;
    Notifier powerdown_notifier;
    Notifier generic_error_notifier;
    PCIBus *bus;
    char *oem_id;
    char *oem_table_id;
    bool ns_el2_virt_timer_irq;
    CXLState cxl_devices_state;
    bool legacy_smmuv3_present;
    MemoryRegion *sysmem;
    MemoryRegion *secure_sysmem;
    bool pci_preserve_config;
    GPtrArray *smmuv3_devices;
};

#define VIRT_ECAM_ID(high) (high ? VIRT_HIGH_PCIE_ECAM : VIRT_PCIE_ECAM)

#define TYPE_VIRT_MACHINE   MACHINE_TYPE_NAME("virt")
OBJECT_DECLARE_TYPE(VirtMachineState, VirtMachineClass, VIRT_MACHINE)

void virt_acpi_setup(VirtMachineState *vms);
bool virt_is_acpi_enabled(const VirtMachineState *vms);

#define CLIDR_CTYPE_NO_CACHE 0x00
#define CLIDR_CTYPE_I_CACHE 0x01
#define CLIDR_CTYPE_D_CACHE 0x02
#define CLIDR_CTYPE_SEPARATE_I_D_CACHES 0x03
#define CLIDR_CTYPE_UNIFIED_CACHE 0x04
#define CLIDR_CTYPE_MAX_CACHE_LEVEL 7

unsigned int virt_get_caches(const VirtMachineState *vms,
                             CPUCoreCaches *caches);
void set_cpu_cache(CPUCoreCaches *cpu_cache, enum CacheType cache_type,
                   int cache_level, bool is_i_cache);

/* Return number of redistributors that fit in the specified region */
static uint32_t virt_redist_capacity(VirtMachineState *vms, int region)
{
    uint32_t redist_size;

    if (vms->gic_version == VIRT_GIC_VERSION_3) {
        redist_size = GICV3_REDIST_SIZE;
    } else {
        redist_size = GICV4_REDIST_SIZE;
    }
    return vms->memmap[region].size / redist_size;
}

/* Return the number of used redistributor regions  */
static inline int virt_gicv3_redist_region_count(VirtMachineState *vms)
{
    uint32_t redist0_capacity = virt_redist_capacity(vms, VIRT_GIC_REDIST);

    assert(vms->gic_version != VIRT_GIC_VERSION_2);

    return (MACHINE(vms)->smp.cpus > redist0_capacity &&
            vms->highmem_redists) ? 2 : 1;
}

#endif /* QEMU_ARM_VIRT_H */
