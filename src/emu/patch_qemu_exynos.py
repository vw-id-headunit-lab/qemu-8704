





import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
import original_8704_vm as vm
import vlm_platform as platform

ARCHIVE_SHA256='6ee1d1a61f68212476b27108c26da5f449dc09b626d42f8279ba0dc2e08fa858'


def one(text, before, after):
    if text.count(before)!=1:raise ValueError('Expected one patch anchor: '+before[:100])
    return text.replace(before,after,1)


def qnx_gtf_watch_rows(model):
    rows=re.findall(r'^\s*\{ (0x[0-9a-f]{3}), "([a-z0-9-]+)", GTF_[A-Z0-9_]+, '
                    r'\{ (0x[0-9a-f]{8}), (0x[0-9a-f]{8}), (0x[0-9a-f]{8}), '
                    r'(0x[0-9a-f]{8}) \} \},$',model,re.MULTILINE)
    table={name:(int(offset,16),tuple(int(word,16) for word in words))
           for offset,name,*words in rows}
    matches={(offset,words[0]) for offset,words in table.values()}
    if not 2 <= len(rows) <= 128 or len(matches)!=len(rows) or len(table)!=len(rows):
        raise ValueError('Invalid or duplicate QNX GTF watch site')
    return table


def qnx_vclk_watch_rows(model):
    rows=re.findall(r'^\s*\{ (0x[0-9a-f]+), "([a-z-]+)", \{ (0x[0-9a-f]{8}), (0x[0-9a-f]{8}), '
                    r'(0x[0-9a-f]{8}), (0x[0-9a-f]{8}) \} \},$',model,re.MULTILINE)
    table={name:(int(offset,16),tuple(int(word,16) for word in words))
           for offset,name,*words in rows}
    matches={(offset & 0xfff,words[0]) for offset,words in table.values()}
    if len(rows)!=5 or len(table)!=5 or len(matches)!=5:
        raise ValueError('Invalid or duplicate original QNX clock backend edge')
    return table


def edits(relative,text):
    if relative=='hw/core/machine-qmp-cmds.c':
        anchor='        if (mc->cpu_index_to_instance_props) {\n'
        return one(text,anchor,'''        /* Report auxiliary CPUs, but do not index their non-SMP topology. */
        if (mc->cpu_index_to_instance_props && cpu->cpu_index < ms->smp.max_cpus) {
''')
    if relative=='accel/tcg/tcg-all.c':
        text=one(text,'    OnOffAuto mttcg_enabled;\n',
                 '    OnOffAuto mttcg_enabled;\n    bool auxiliary_vcpu;\n')
        anchor='    qemu_add_vm_change_state_handler(tcg_vm_change_state, NULL);\n'
        text=one(text,anchor,'''    if (s->auxiliary_vcpu) {
        if (s->mttcg_enabled != ON_OFF_AUTO_ON) {
            error_report("x-auxiliary-vcpu requires multi-threaded TCG");
            return -EINVAL;
        }
        /* Reserve one context for a management CPU outside machine SMP. */
        max_threads++;
    }

'''+anchor)
        anchor='static bool tcg_get_splitwx(Object *obj, Error **errp)\n'
        text=one(text,anchor,'''#ifndef CONFIG_USER_ONLY
static bool tcg_get_auxiliary_vcpu(Object *obj, Error **errp)
{
    return TCG_STATE(obj)->auxiliary_vcpu;
}

static void tcg_set_auxiliary_vcpu(Object *obj, bool value, Error **errp)
{
    TCG_STATE(obj)->auxiliary_vcpu = value;
}
#endif

'''+anchor)
        anchor='    object_class_property_add(oc, "tb-size", "int",\n'
        return one(text,anchor,'''#ifndef CONFIG_USER_ONLY
    object_class_property_add_bool(oc, "x-auxiliary-vcpu",
        tcg_get_auxiliary_vcpu, tcg_set_auxiliary_vcpu);
    object_class_property_set_description(oc, "x-auxiliary-vcpu",
        "Reserve a TCG context for a board CPU outside SMP topology");
#endif

'''+anchor)
    if relative=='target/arm/cpu.h':
        text=one(text,'    uint64_t gt_cntfrq_hz;\n',
                 '    uint64_t gt_cntfrq_hz;\n    bool exynos_exact_counter;\n')
        return one(text,'unsigned int gt_cntfrq_period_ns(ARMCPU *cpu);\n',
                   'unsigned int gt_cntfrq_period_ns(ARMCPU *cpu);\n'
                   '#ifndef CONFIG_USER_ONLY\n'
                   'int64_t auto9_gt_ticks_to_ns(uint64_t ticks);\n'
                   '#endif\n')
    if relative=='target/arm/cpu.c':
        anchor='static const Property arm_cpu_reset_cbar_property =\n'
        text=one(text,anchor,'''static const Property arm_cpu_exynos_exact_counter_property =
            DEFINE_PROP_BOOL("x-exynos-exact-counter", ARMCPU,
                             exynos_exact_counter, false);

'''+anchor)
        anchor='        qdev_property_add_static(DEVICE(cpu), &arm_cpu_gt_cntfrq_property);\n'
        text=one(text,anchor,anchor+'''        qdev_property_add_static(DEVICE(cpu),
                                 &arm_cpu_exynos_exact_counter_property);
''')
        anchor='#ifndef CONFIG_USER_ONLY\n    {\n        uint64_t scale = gt_cntfrq_period_ns(cpu);'
        return one(text,anchor,'''#ifndef CONFIG_USER_ONLY
    if (cpu->exynos_exact_counter && cpu->gt_cntfrq_hz != 26000000) {
        error_setg(errp, "x-exynos-exact-counter requires a 26 MHz CNTFRQ");
        return;
    }
    {
        uint64_t scale = gt_cntfrq_period_ns(cpu);''')
    if relative=='target/arm/helper.c':
        model=Path(__file__).with_name('exynos-auto9-gtimer.inc.c').read_text(encoding='utf8')
        anchor='uint64_t gt_get_countervalue(CPUARMState *env)\n'
        text=one(text,anchor,model+'\n'+anchor)
        anchor='    return qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / gt_cntfrq_period_ns(cpu);\n'
        text=one(text,anchor,'''    uint64_t ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    return cpu->exynos_exact_counter ? auto9_gt_ns_to_ticks(ns)
                                    : ns / gt_cntfrq_period_ns(cpu);
''')
        anchor='        if (nexttick > INT64_MAX / gt_cntfrq_period_ns(cpu)) {\n'
        return one(text,anchor,'''        if (cpu->exynos_exact_counter) {
            timer_mod_ns(cpu->gt_timer[timeridx], auto9_gt_ticks_to_ns(nexttick));
        } else if (nexttick > INT64_MAX / gt_cntfrq_period_ns(cpu)) {
''')
    if relative=='target/arm/tcg/op_helper.c':
        model=Path(__file__).with_name('exynos-auto9-qnx-wfe.inc.c').read_text(encoding='utf8')
                                                                           
        wfit='    if (nexttick > INT64_MAX / gt_cntfrq_period_ns(cpu)) {\n        /*\n'
        text=one(text,wfit,'''    if (cpu->exynos_exact_counter) {
        timer_mod_ns(cpu->wfxt_timer, auto9_gt_ticks_to_ns(nexttick));
    } else if (nexttick > INT64_MAX / gt_cntfrq_period_ns(cpu)) {
        /*
''')
        anchor='        uint64_t count = gt_get_countervalue(env) - offset;\n'
        text=one(text,anchor,'''        uint64_t now = gt_get_countervalue(env);
        uint64_t count = now - offset;
''')
        anchor='        uint64_t lower_bits = MAKE_64BIT_MASK(0, bit - 1);\n'
        text=one(text,anchor,'''        uint64_t lower_bits = cpu->exynos_exact_counter
                            ? target_bit - 1 : MAKE_64BIT_MASK(0, bit - 1);
''')
        anchor='        if (uadd64_overflow(next_tick, offset, &abstick)) {\n'
        text=one(text,anchor,'''        if (cpu->exynos_exact_counter) {
            /* Counter phase was computed with the offset; now it is a delta. */
            if (uadd64_overflow(now, next_tick, &abstick)) {
                abstick = UINT64_MAX;
            }
            return auto9_gt_ticks_to_ns(abstick);
        }
'''+anchor)
        anchor='    next_event = gt_calc_next_event_stream(env);\n    if (next_event > 0 && next_event < nexttick) {\n'
        text=one(text,anchor,'''    next_event = gt_calc_next_event_stream(env);
    if (cpu->exynos_exact_counter) {
        int64_t deadline = auto9_gt_ticks_to_ns(nexttick);

        if (next_event > 0 && next_event < deadline) {
            deadline = next_event;
        }
        timer_mod_ns(cpu->wfxt_timer, deadline);
    } else if (next_event > 0 && next_event < nexttick) {
''')
                                                                     
        anchor='    if (nexttick > INT64_MAX / gt_cntfrq_period_ns(cpu)) {\n        nexttick = INT64_MAX;\n'
        text=one(text,anchor,'''    if (!cpu->exynos_exact_counter &&
        nexttick > INT64_MAX / gt_cntfrq_period_ns(cpu)) {
        nexttick = INT64_MAX;
''')
        tee=Path(__file__).with_name('exynos-auto9-qnx-tee-watch.inc.c').read_text(encoding='utf8')
        text=one(text,'#include "qemu/osdep.h"\n',
                 '#include "qemu/osdep.h"\n#include "qemu/log.h"\n#include "qemu/bswap.h"\n')
        anchor='void HELPER(wfe)(CPUARMState *env, uint32_t insn_len)'
        text=one(text,anchor,model+'\n'+anchor)
        anchor='void HELPER(pre_smc)(CPUARMState *env, uint32_t syndrome)'
        text=one(text,anchor,'#include "trace.h"\n\n'+tee+'\n'+anchor)
        start=text.index(anchor); stop=text.index('/* ??? Flag setting arithmetic',start)
        smc=text[start:stop]
        guard='#ifndef CONFIG_USER_ONLY\n'
        undef='        raise_exception(env, EXCP_UDEF, syn_uncategorized(),\n'
        if smc.count(undef)!=2:
            raise ValueError('Expected the two original pre-SMC undefined branches')
        smc=smc.replace(undef,guard+'        auto9_qnx_tee_edge(env, cur_el, "undef");\n#endif\n'+undef)
        hyp='        raise_exception(env, EXCP_HYP_TRAP, syndrome, 2);'
        smc=one(smc,hyp,guard+'        auto9_qnx_tee_edge(env, cur_el, "trap-el2");\n#endif\n'+hyp)
        end=smc.rindex('\n}\n')
        smc=smc[:end]+'\n'+guard+'    auto9_qnx_tee_edge(env, cur_el, "dispatch-smc");\n#endif'+smc[end:]
        text=text[:start]+smc+text[stop:]
        before=('        raise_exception(env, excp, syn_wfx(1, 0xe, 0, false, WFE, insn_len == 2),\n'
                '                        target_el);\n    }\n\n')
        return one(text,before,before+'    if (auto9_qnx_wfe_retry(env, insn_len)) {\n'
                   '        cs->exception_index = EXCP_YIELD;\n'
                   '        cpu_loop_exit(cs);\n'
                   '    }\n\n')
    if relative=='target/arm/tcg/psci.c':
        return text
    if relative=='target/arm/tcg/helper-a64-defs.h':
        anchor='DEF_HELPER_FLAGS_2(udiv64, TCG_CALL_NO_RWG_SE, i64, i64, i64)'
        return one(text,anchor,'#ifndef CONFIG_USER_ONLY\n'
                   'DEF_HELPER_3(auto9_qnx_gtf_watch, void, env, i64, i32)\n'
                   'DEF_HELPER_1(auto9_vm3_hmi_signal, void, env)\n'
                   'DEF_HELPER_2(auto9_vm3_kipc_watch, void, env, i32)\n'
                   'DEF_HELPER_2(auto9_vm3_vclk_watch, void, env, i32)\n'
                   'DEF_HELPER_3(auto9_qnx_vclk_watch, void, env, i64, i32)\n'
                   '#endif\n'+anchor)
    if relative=='target/arm/tcg/helper-a64.c':
        model=Path(__file__).with_name('exynos-auto9-qnx-gtf-watch.inc.c').read_text(encoding='utf8')
        signal=Path(__file__).with_name('exynos-auto9-linux-hmi-signal.inc.c').read_text(encoding='utf8')
        kipc=Path(__file__).with_name('exynos-auto9-linux-kipc-watch.inc.c').read_text(encoding='utf8')
        vclk=Path(__file__).with_name('exynos-auto9-linux-vclk-watch.inc.c').read_text(encoding='utf8')
        qnx_vclk=Path(__file__).with_name('exynos-auto9-qnx-vclk-watch.inc.c').read_text(encoding='utf8')
        anchor='#include "exec/helper-info.c.inc"\n'
        text=one(text,anchor,anchor+'\n'+model+'\n'+signal+'\n'+kipc+'\n'+vclk+'\n'+qnx_vclk)
        anchor='void HELPER(exception_return)(CPUARMState *env, uint64_t new_pc)'
        text=one(text,anchor,'#ifndef CONFIG_USER_ONLY\n'
            'void auto9_qnx_tee_eret(CPUARMState *env, int cur_el, int new_el, uint64_t pc);\n'
            '#endif\n\n'+anchor)
        return one(text,'        env->pc = new_pc;\n',
                   '        env->pc = new_pc;\n#ifndef CONFIG_USER_ONLY\n'
                   '        auto9_qnx_tee_eret(env, cur_el, new_el, new_pc);\n#endif\n')
    if relative=='target/arm/tcg/trace-events':
        return text+'\n# Read-only classification of original QNX TEE boot calls.\n' \
            'auto9_qnx_tee_edge(int cpu, uint32_t call, int el, const char *route) "cpu=%d call=0x%x el=%d route=%s"\n' \
            '# Original VM3 Linux kernel signal frame input for the HMI JVM.\n' \
            'auto9_vm3_hmi_signal(int cpu, unsigned seq, uint32_t tgid, uint32_t tid, unsigned sig, int32_t code, uint64_t pc, uint64_t addr) "cpu=%d seq=%u tgid=%u tid=%u sig=%u code=%d pc=0x%"PRIx64" addr=0x%"PRIx64\n' \
            'auto9_vm3_kipc_watch(int cpu, unsigned seq, const char *edge, unsigned src, unsigned dst, const char *kind) "cpu=%d seq=%u edge=%s src=%u dst=%u kind=%s"\n'
    if relative=='target/arm/tcg/translate-a64.c':
        anchor='    s->is_nonstreaming = false;\n    if (s->sme_trap_nonstreaming) {'
        model=Path(__file__).with_name('exynos-auto9-qnx-gtf-watch.inc.c').read_text(encoding='utf8')
        matches=' ||\n                  '.join(f'((pc & 0xfff) == 0x{offset:03x} && insn == 0x{words[0]:08x}U)'
                                               for offset,words in qnx_gtf_watch_rows(model).values())
        vclk=Path(__file__).with_name('exynos-auto9-linux-vclk-watch.inc.c').read_text(encoding='utf8')
        qnx_vclk=Path(__file__).with_name('exynos-auto9-qnx-vclk-watch.inc.c').read_text(encoding='utf8')
        qnx_vclk_matches=' ||\n                  '.join(
            f'((pc & 0xfff) == 0x{offset & 0xfff:03x} && insn == 0x{words[0]:08x}U)'
            for offset,words in qnx_vclk_watch_rows(qnx_vclk).values())
        vclk_sites=re.findall(r'\{ UINT64_C\((0x[0-9a-f]+)\), \{(0x[0-9a-f]+),',vclk)
        if len(vclk_sites)!=21 or len({pc for pc,_ in vclk_sites})!=21:
            raise ValueError('Expected 21 unique original Linux clock/RPC sites')
        vclk_pages=' || '.join(f'(pc >> 12) == UINT64_C(0x{page:x})'
                               for page in sorted({int(pc,16)>>12 for pc,_ in vclk_sites}))
        vclk_cases='\n'.join(f'        case UINT64_C({pc}):\n'
                             f'            if (insn == {word}U) {{\n'
                             f'                gen_helper_auto9_vm3_vclk_watch(tcg_env, tcg_constant_i32({i}));\n'
                             '            }\n            break;' for i,(pc,word) in enumerate(vclk_sites))
        return one(text,anchor,'''#ifndef CONFIG_USER_ONLY
    if (unlikely(pc == UINT64_C(0xffffffc01008ea98) && insn == 0xa9b57bfdU &&
                 s->current_el == 1 && (env->cp15.vttbr_el2 >> 48) == 3)) {
        gen_helper_auto9_vm3_hmi_signal(tcg_env);
    }
    if (unlikely(s->current_el == 1 && (env->cp15.vttbr_el2 >> 48) == 3)) {
        if (pc == UINT64_C(0xffffffc010584d20) && insn == 0xa9bf7bfdU) {
            gen_helper_auto9_vm3_kipc_watch(tcg_env, tcg_constant_i32(0));
        } else if (pc == UINT64_C(0xffffffc010584974) && insn == 0xaa1503e0U) {
            gen_helper_auto9_vm3_kipc_watch(tcg_env, tcg_constant_i32(1));
        }
    }
    if (unlikely((AUTO9_VCLK_PAGES) &&
                 s->current_el == 1 && (env->cp15.vttbr_el2 >> 48) == 3 &&
                 qemu_loglevel_mask(LOG_GUEST_ERROR))) {
        switch (pc) {
AUTO9_VCLK_CASES
        }
    }
    if (unlikely((env->cp15.vttbr_el2 >> 48) == 2 &&
                 s->current_el == 0 && (AUTO9_QNX_VCLK_MATCHES) &&
                 qemu_loglevel_mask(LOG_GUEST_ERROR))) {
        gen_helper_auto9_qnx_vclk_watch(tcg_env, tcg_constant_i64(pc), tcg_constant_i32(insn));
    }
    if (unlikely((env->cp15.vttbr_el2 >> 48) == 2 &&
                 (AUTO9_GTF_MATCHES)) &&
        qemu_loglevel_mask(LOG_GUEST_ERROR)) {
        gen_helper_auto9_qnx_gtf_watch(tcg_env, tcg_constant_i64(pc), tcg_constant_i32(insn));
    }
#endif

'''.replace('AUTO9_GTF_MATCHES',matches).replace('AUTO9_VCLK_PAGES',vclk_pages).replace(
                    'AUTO9_VCLK_CASES',vclk_cases).replace('AUTO9_QNX_VCLK_MATCHES',qnx_vclk_matches)+anchor)
    if relative=='backends/hostmem-ram.c':
        model=Path(__file__).with_name('exynos-auto9-windows-file-ram.inc.c').read_text(encoding='utf-8')
        anchor='static void register_types(void)\n'
        text=one(text,anchor,model+'\n'+anchor)
        return one(text,'    type_register_static(&ram_backend_info);',
                   '    type_register_static(&ram_backend_info);\n'
                   '#ifdef _WIN32\n'
                   '    type_register_static(&auto9_windows_file_ram_type);\n'
                   '#endif')
    if relative=='include/hw/intc/arm_gic_common.h':
        anchor='    /* Virtual interface control registers */\n'
        return one(text,anchor,'    /* Host-only PPI27 observation, not migrated guest state. */\n'
                   '    uint64_t auto9_ppi27[GIC_NCPU][8];\n\n'+anchor)
    if relative=='hw/intc/arm_gic.c':
        model=Path(__file__).with_name('exynos-auto9-gic-ufs-observer.inc.c').read_text(encoding='utf8')
        anchor='/* Process a change in an external IRQ input.  */\n'
        text=one(text,anchor,model+'\n'+anchor)
        anchor='    trace_gic_set_irq(irq, level, cm, target);'
        text=one(text,anchor,'''    if (irq == 27) {
        auto9_gic_count_ppi27(s, irq, ctz32(cm), level ? AUTO9_PPI27_RISE : AUTO9_PPI27_FALL);
    }
'''+anchor)
        anchor='    gic_activate_irq(s, cpu, irq);'
        text=one(text,anchor,'''    auto9_gic_count_ppi27(s, irq, gic_get_vcpu_real_id(cpu),
                           gic_is_vcpu(cpu) ? AUTO9_PPI27_VIRT_ACK : AUTO9_PPI27_PHYS_ACK);
'''+anchor)
        anchor='        gic_complete_irq(s, cpu, value & 0x3ff, attrs);'
        text=one(text,anchor,'''        auto9_gic_count_ppi27(s, value & 0x3ff, gic_get_vcpu_real_id(cpu),
                               gic_is_vcpu(cpu) ? AUTO9_PPI27_VIRT_EOI : AUTO9_PPI27_PHYS_EOI);
'''+anchor)
        anchor='        gic_deactivate_irq(s, cpu, value & 0x3ff, attrs);'
        text=one(text,anchor,'''        auto9_gic_count_ppi27(s, value & 0x3ff, gic_get_vcpu_real_id(cpu),
                               gic_is_vcpu(cpu) ? AUTO9_PPI27_VIRT_DIR : AUTO9_PPI27_PHYS_DIR);
'''+anchor)
        return one(text,'    device_class_set_parent_realize(dc, arm_gic_realize, &agc->parent_realize);',
                   '    device_class_set_parent_realize(dc, arm_gic_realize, &agc->parent_realize);\n'
                   '    object_class_property_add_str(klass, "x-auto9-spi228-state", auto9_gic_vh1_snapshot, NULL);\n'
                   '    object_class_property_add_str(klass, "x-auto9-decon-irq-state", auto9_gic_decon_snapshot, NULL);\n'
                   '    object_class_property_add_str(klass, "x-auto9-ppi27-state", auto9_gic_ppi27_snapshot, NULL);')
    if relative=='target/arm/tcg/tlb_helper.c':
        text=one(text,'#include "qemu/osdep.h"','#include "qemu/osdep.h"\n#include "qemu/log.h"')
        return one(text,'''    /* now we have a real cpu fault */
    cpu_restore_state(cs, retaddr);

    fi.ea = arm_extabort_type(response);''','''    /* now we have a real cpu fault */
    cpu_restore_state(cs, retaddr);

    /* Read-only failure evidence: VA and PA from the same access. */
    unsigned vmid = env->cp15.vttbr_el2 >> 48;
    if (is_a64(env) && arm_current_el(env) < 2 && vmid >= 2 && vmid <= 4) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "Auto9 VM%u external abort: pc=0x%" PRIx64
                      " va=0x%" PRIx64 " pa=0x%" HWADDR_PRIx
                      " ttbr0=0x%" PRIx64 " access=%d size=%u result=%u\\n",
                      vmid, env->pc, (uint64_t)addr, physaddr, env->cp15.ttbr0_el[1],
                      access_type, size, (unsigned)response);
    }

    fi.ea = arm_extabort_type(response);''')
    if relative=='gdbstub/system.c':
        return one(text,'''unsigned int gdb_get_max_cpus(void)
{
    MachineState *ms = MACHINE(qdev_get_machine());
    return ms->smp.max_cpus;
}''','''unsigned int gdb_get_max_cpus(void)
{
    MachineState *ms = MACHINE(qdev_get_machine());
    unsigned int count = ms->smp.max_cpus;
    CPUState *cpu;
    /* Auxiliary management cores need not be part of machine SMP topology.
     * vCont indexes its action array by every realized cpu_index. */
    CPU_FOREACH(cpu) {
        count = MAX(count, cpu->cpu_index + 1);
    }
    return count;
}''')
    if relative=='hw/ufs/ufs.h':
        return one(text,'    bool mcq; /* Multiple Command Queue support */',
                   '    bool mcq; /* Multiple Command Queue support */\n    bool samsung_byte_granularity; /* Explicit legacy-only byte-unit UTRD/PRDT. */\n    bool samsung_plain_prdt128; /* Legacy Samsung format, no inline crypto. */')
    if relative=='hw/ufs/trace-events':
        anchor='ufs_sendback_req(uint32_t slot) "UTRLDBR slot %"PRIu32""\n'
        return one(text,anchor,anchor+
                   'ufs_native_read_result(const char *serial, uint32_t slot, uint8_t lun, uint8_t opcode, uint64_t lba, uint32_t blocks, uint32_t ocs, uint8_t status) '
                   '"host=%s slot=%u lun=%u opcode=0x%x lba=%"PRIu64" blocks=%"PRIu32" ocs=%u status=%u"\n')
    if relative=='hw/ufs/ufs.c':
        text=one(text,'#include "qemu/osdep.h"','#include "qemu/osdep.h"\n#include "qemu/cutils.h"')
        text=one(text,'static inline hwaddr ufs_get_rsp_upiu_base_addr(const UtpTransferReqDesc *utrd)',
                 'static inline hwaddr ufs_get_rsp_upiu_base_addr(UfsHc *u, const UtpTransferReqDesc *utrd)')
        text=one(text,'le16_to_cpu(utrd->response_upiu_offset) * sizeof(uint32_t);',
                 'le16_to_cpu(utrd->response_upiu_offset) *\n        (u->params.samsung_byte_granularity ? 1 : sizeof(uint32_t));')
        text=one(text,'static MemTxResult ufs_dma_read_prdt(UfsRequest *req)\n{\n    UfsHc *u = req->hc;\n    uint16_t prdt_len = le16_to_cpu(req->utrd.prd_table_length);',
                 '''static unsigned ufs_prdt_stride(UfsHc *u)
{
    return u->params.samsung_plain_prdt128 ? 128 : sizeof(UfshcdSgEntry);
}

static MemTxResult ufs_dma_read_prdt(UfsRequest *req, bool *invalid_descriptor)
{
    UfsHc *u = req->hc;
    unsigned stride = ufs_prdt_stride(u);
    uint16_t prdt_len = le16_to_cpu(req->utrd.prd_table_length);
    /* Samsung legacy descriptors encode PRDT bytes, not SG entry count.
     * Never truncate a partial entry or rewrite the guest's descriptor. */
    if (u->params.samsung_byte_granularity) {
        if (prdt_len % stride) {
            return MEMTX_ERROR;
        }
        prdt_len /= stride;
    }''')
        text=one(text,'le16_to_cpu(req->utrd.prd_table_offset) * sizeof(uint32_t);',
                 'le16_to_cpu(req->utrd.prd_table_offset) *\n        (u->params.samsung_byte_granularity ? 1 : sizeof(uint32_t));')
        text=one(text,'uint32_t prdt_size = prdt_len * sizeof(UfshcdSgEntry);',
                 'uint32_t prdt_size = prdt_len * stride;')
        text=one(text,'g_autofree UfshcdSgEntry *prd_entries = NULL;',
                 'g_autofree uint8_t *prd_entries = NULL;')
        text=one(text,'prd_entries = g_new(UfshcdSgEntry, prdt_size);',
                 'prd_entries = g_malloc(prdt_size);')
        text=one(text,'    req->sg = g_malloc0(sizeof(QEMUSGList));',
                 '''    if (u->params.samsung_plain_prdt128) {
        for (uint16_t i = 0; i < prdt_len; ++i) {
            const uint8_t *p = prd_entries + i * stride;
            const UfshcdSgEntry *e = (const void *)p;
            /* The 128-byte Exynos format has optional inline FMP crypto.
             * Only known-clear descriptors are supported. Never drop flags,
             * process encrypted requests as plaintext, or log key bytes. */
            if ((le32_to_cpu(e->size) & ~0x3ffffU) || e->reserved ||
                !buffer_is_zero(p + sizeof(*e), stride - sizeof(*e))) {
                *invalid_descriptor = true;
                return MEMTX_ERROR;
            }
        }
    }
    req->sg = g_malloc0(sizeof(QEMUSGList));''')
        text=one(text,'''        hwaddr data_dma_addr = le64_to_cpu(prd_entries[i].addr);
        uint32_t data_byte_count =
            (le32_to_cpu(prd_entries[i].size) & 0x3ffff) + 1;''',
                 '''        const UfshcdSgEntry *e = (const void *)(prd_entries + i * stride);
        hwaddr data_dma_addr = le64_to_cpu(e->addr);
        uint32_t data_byte_count = (le32_to_cpu(e->size) & 0x3ffff) + 1;''')
        text=one(text,'ufs_dma_read_prdt(req);','ufs_dma_read_prdt(req, invalid_descriptor);')
        text=one(text,'ufs_get_rsp_upiu_base_addr(&req->utrd);','ufs_get_rsp_upiu_base_addr(u, &req->utrd);')
        text=one(text,'le16_to_cpu(req->utrd.response_upiu_length) * sizeof(uint32_t);',
                 'le16_to_cpu(req->utrd.response_upiu_length) *\n        (u->params.samsung_byte_granularity ? 1 : sizeof(uint32_t));')
        text=one(text,'static MemTxResult ufs_dma_read_upiu(UfsRequest *req)',
                 'static MemTxResult ufs_dma_read_upiu(UfsRequest *req, bool *invalid_descriptor)')
        text=one(text,'    ret = ufs_dma_read_req_upiu(req);',
                 '''    /* A malformed byte count is not a transient IOMMU/DMA fault.
     * Descriptor DMA has succeeded; distinguish structural rejection so it
     * cannot hang forever waiting for a permission repair that cannot help. */
    if (req->hc->params.samsung_byte_granularity &&
        le16_to_cpu(req->utrd.prd_table_length) % ufs_prdt_stride(req->hc)) {
        *invalid_descriptor = true;
        return MEMTX_ERROR;
    }
    ret = ufs_dma_read_req_upiu(req);''')
        text=one(text,'    if (ufs_dma_read_upiu(req)) {\n        return;\n    }',
                 '''    bool invalid_descriptor = false;
    if (ufs_dma_read_upiu(req, &invalid_descriptor)) {
        if (invalid_descriptor) {
            ufs_complete_req(req, UFS_REQUEST_FAIL);
        }
        return;
    }''')
        text=one(text,'        trace_ufs_sendback_req(req->slot);',r"""        if (trace_event_get_state_backends(TRACE_UFS_NATIVE_READ_RESULT) &&
            req->req_upiu.header.trans_type == UFS_UPIU_TRANSACTION_COMMAND) {
            const uint8_t *cdb = req->req_upiu.sc.cdb;
            uint64_t lba;
            uint32_t blocks;

            switch (cdb[0]) {
            case READ_10:
                lba = ldl_be_p(cdb + 2);
                blocks = lduw_be_p(cdb + 7);
                break;
            case READ_16:
                lba = ldq_be_p(cdb + 2);
                blocks = ldl_be_p(cdb + 10);
                break;
            default:
                blocks = 0;
                lba = 0;
                break;
            }
            if (blocks) {
                trace_ufs_native_read_result(u->params.serial, req->slot,
                    req->req_upiu.header.lun, cdb[0], lba, blocks,
                    le32_to_cpu(req->utrd.header.dword_2),
                    req->rsp_upiu.header.scsi_status);
            }
        }
        trace_ufs_sendback_req(req->slot);""")
        text=one(text,'static bool ufs_check_constraints(UfsHc *u, Error **errp)\n{',
                 '''static bool ufs_check_constraints(UfsHc *u, Error **errp)
{
    if (u->params.samsung_byte_granularity && u->params.mcq) {
        error_setg(errp, "Samsung byte granularity is implemented for legacy UTRD only");
        return false;
    }''')
        return one(text,'    DEFINE_PROP_BOOL("mcq", UfsHc, params.mcq, false),',
                   '    DEFINE_PROP_BOOL("mcq", UfsHc, params.mcq, false),\n    DEFINE_PROP_BOOL("x-samsung-byte-granularity", UfsHc, params.samsung_byte_granularity, false),')
    if relative=='system/qtest.c':
                                                                              
                                                                             
                                                                                
        text=one(text,'#define MAX_IRQ 256','#define MAX_IRQ 4096 /* Native GIC test observer capacity, not guest IRQ count. */')
        return one(text,'static void qtest_irq_handler(void *opaque, int n, int level)\n{',
                   'static void qtest_irq_handler(void *opaque, int n, int level)\n{\n    g_assert(n >= 0 && n < MAX_IRQ);')
    if relative=='hw/dma/pl330.c':
        text=one(text,'    uint32_t num_chnls;', '    uint32_t num_chnls;\n    bool auto9_sideband; /* Opt-in internal transfer class/channel, not silicon AxID. */\n    uint32_t auto9_idle_periph_mask; /* Busy inputs before any hardware request. */\n    uint32_t auto9_periph_fence_mask; /* GPIO handshakes with one request credit. */')
        text=one(text,'    bool is_manager;\n    PL330State *parent;',
                 '    bool is_manager;\n    bool auto9_periph_fence;\n    PL330State *parent;')
        text=one(text,'static inline void pl330_fault(PL330Chan *ch, uint32_t flags)\n', '''/* Private native-board metadata; stock PL330 keeps unspecified attributes. */
static MemTxAttrs pl330_auto9_attrs(PL330State *s, unsigned kind, unsigned tag)
{
    MemTxAttrs attrs = MEMTXATTRS_UNSPECIFIED;
    if (s->auto9_sideband) {
        attrs.unspecified = 0;
        attrs.requester_id = (kind << 8) | tag;
    }
    return attrs;
}

static inline void pl330_fault(PL330Chan *ch, uint32_t flags)
''')
        text=one(text,'static inline void pl330_fault(PL330Chan *ch, uint32_t flags)\n', '''static void pl330_auto9_purge(PL330Chan *ch)
{
    if (!ch->is_manager) {
        PL330State *s = ch->parent;
        PL330Fifo *f = &s->fifo;
        unsigned kept = 0, total = f->num;
        /* O(FIFO+queues); preserve other channels, purge failed work only.
         * Snapshot the original count: shrinking the loop bound skips bytes. */
        for (unsigned n = 0; n < total; n++) {
            unsigned src = (f->head + n) % f->buf_size;
            unsigned dst = (f->head + kept) % f->buf_size;
            if (f->tag[src] != ch->tag) {
                f->buf[dst] = f->buf[src]; f->tag[dst] = f->tag[src]; kept++;
            }
        }
        f->num = kept;
        pl330_queue_remove_tagged(&s->read_queue, ch->tag);
        pl330_queue_remove_tagged(&s->write_queue, ch->tag);
        s->lo_seqn[ch->tag] = s->hi_seqn[ch->tag];
        ch->auto9_periph_fence = false;
    }
}

static inline void pl330_fault(PL330Chan *ch, uint32_t flags)
''')
        text=one(text,'    ch->state = pl330_chan_fault;\n', '''    if (ch->parent->auto9_sideband) { pl330_auto9_purge(ch); }
    ch->state = pl330_chan_fault;
''')
        text=one(text,'''    ch->state = pl330_chan_killing;
    pl330_fifo_tagged_remove(&ch->parent->fifo, ch->tag);
    pl330_queue_remove_tagged(&ch->parent->read_queue, ch->tag);
    pl330_queue_remove_tagged(&ch->parent->write_queue, ch->tag);''', '''    ch->state = pl330_chan_killing;
    if (ch->parent->auto9_sideband) {
        pl330_auto9_purge(ch);
    } else {
        pl330_fifo_tagged_remove(&ch->parent->fifo, ch->tag);
        pl330_queue_remove_tagged(&ch->parent->read_queue, ch->tag);
        pl330_queue_remove_tagged(&ch->parent->write_queue, ch->tag);
    }''')
        text=one(text,'''    dma_memory_read(ch->parent->mem_as, ch->pc, &opcode, 1,
                    MEMTXATTRS_UNSPECIFIED);''', '''    MemTxResult result = dma_memory_read(ch->parent->mem_as, ch->pc, &opcode, 1,
                              pl330_auto9_attrs(ch->parent, 1, ch->tag));
    if (ch->parent->auto9_sideband && result != MEMTX_OK) {
        pl330_fault(ch, PL330_FAULT_INSTR_FETCH_ERR);
        return NULL;
    }''')
        text=one(text,'''    dma_memory_read(ch->parent->mem_as, ch->pc, buf, insn->size,
                    MEMTXATTRS_UNSPECIFIED);''', '''    MemTxResult result = dma_memory_read(ch->parent->mem_as, ch->pc, buf, insn->size,
                              pl330_auto9_attrs(ch->parent, 1, ch->tag));
    if (ch->parent->auto9_sideband && result != MEMTX_OK) {
        pl330_fault(ch, PL330_FAULT_INSTR_FETCH_ERR);
        return;
    }''')
        text=one(text,'    if (!insn) {\n        trace_pl330_chan_exec_undef();', '''    if (!insn) {
        if (ch->parent->auto9_sideband && ch->state == pl330_chan_fault) {
            return 0;
        }
        trace_pl330_chan_exec_undef();''')
        text=one(text,'    pl330_exec_insn(ch, insn);\n', '''    pl330_exec_insn(ch, insn);
    if (ch->parent->auto9_sideband && ch->state == pl330_chan_fault) {
        return 0;
    }
''')
        text=one(text,'static void pl330_dmaflushp(PL330Chan *ch, uint8_t opcode,\n', '''/* The Auto9 SPI6 GPIO adapter advertises one complete request. Do not
 * sample the next request or execute SEV before its queued bus beats drain. */
static void pl330_auto9_periph_fence(PL330Chan *ch, unsigned periph)
{
    if (!ch->stall && !ch->is_manager && ch->parent->auto9_sideband &&
        (ch->parent->auto9_periph_fence_mask & (UINT32_C(1) << periph))) {
        ch->auto9_periph_fence = true;
        ch->state = pl330_chan_at_barrier;
    }
}

static void pl330_dmaflushp(PL330Chan *ch, uint8_t opcode,
''')
        text=one(text,'''    /* Do nothing */
}

static void pl330_dmago(''', '''    /* OEM SPI6 uses WFP.B, LD.B, ST.B, FLUSHP (no STP).
     * Drain this channel's bus beats before a new request or SEV. FLUSHP
     * does not drop the SPI FIFO or alter an unrelated PL330 request. */
    pl330_auto9_periph_fence(ch, periph_id);
}

static void pl330_dmago(''')
        text=one(text,'    pl330_dmald(ch, opcode, args, len);\n}',
                 '    pl330_dmald(ch, opcode, args, len);\n    pl330_auto9_periph_fence(ch, periph_id);\n}')
        text=one(text,'    pl330_dmast(ch, opcode, args, len);\n}',
                 '    pl330_dmast(ch, opcode, args, len);\n    pl330_auto9_periph_fence(ch, periph_id);\n}')
        text=one(text,'    ch->stall = 0;\n    insn = pl330_fetch_insn(ch);', '''    if (ch->auto9_periph_fence) {
        if (pl330_queue_find_insn(&ch->parent->read_queue, ch->tag, false) ||
            pl330_queue_find_insn(&ch->parent->write_queue, ch->tag, false)) {
            return 0; /* pl330_exec_cycle still services the actual queues. */
        }
        ch->auto9_periph_fence = false;
        ch->state = pl330_chan_executing;
    }
    ch->stall = 0;
    insn = pl330_fetch_insn(ch);''')
        text=one(text,'    ch->control = 0;\n    ch->status = 0;',
                 '    ch->auto9_periph_fence = false;\n    ch->control = 0;\n    ch->status = 0;')
        text=one(text,'''        dma_memory_read(s->mem_as, q->addr, buf, len,
                        MEMTXATTRS_UNSPECIFIED);''', '''        MemTxResult result = dma_memory_read(s->mem_as, q->addr, buf, len,
                                            pl330_auto9_attrs(s, 2, q->tag));
        if (s->auto9_sideband && result != MEMTX_OK) {
            pl330_fault(&s->chan[q->tag], PL330_FAULT_DATA_READ_ERR);
            return num_exec;
        }''')
        text=one(text,'''            dma_memory_write(s->mem_as, q->addr, buf, len,
                             MEMTXATTRS_UNSPECIFIED);''', '''            MemTxResult result = dma_memory_write(s->mem_as, q->addr, buf, len,
                                                 pl330_auto9_attrs(s, 3, q->tag));
            if (s->auto9_sideband && result != MEMTX_OK) {
                pl330_fault(&s->chan[q->tag], PL330_FAULT_DATA_WRITE_ERR);
                return num_exec;
            }''')
        text=one(text,'        s->periph_busy[i] = 0;',
                 '        s->periph_busy[i] = !!(s->auto9_idle_periph_mask & (UINT32_C(1) << i));')
        return one(text,'    /* CR0 */\n    DEFINE_PROP_UINT32(', '''    DEFINE_PROP_BOOL("x-auto9-sideband", PL330State, auto9_sideband, false),
    DEFINE_PROP_UINT32("x-auto9-idle-peripheral-mask", PL330State, auto9_idle_periph_mask, 0),
    DEFINE_PROP_UINT32("x-auto9-peripheral-fence-mask", PL330State, auto9_periph_fence_mask, 0),
    /* CR0 */
    DEFINE_PROP_UINT32(''')
    if relative=='system/physmem.c':
        return one(text,'static void address_space_notify_map_clients(AddressSpace *as)\n',
                        'void address_space_notify_map_clients(AddressSpace *as)\n')
    if relative=='include/system/memory.h':
        return one(text,'void address_space_register_map_client(AddressSpace *as, QEMUBH *bh);',
                        'void address_space_register_map_client(AddressSpace *as, QEMUBH *bh);\n'
                        '/* Native Auto9 DMA permission repair wakes real deferred SG mappings. */\n'
                        'void address_space_notify_map_clients(AddressSpace *as);')
    if relative=='scripts/symlink-install-tree.py':
        return one(text,'import sys\n','''import sys

# Optional development install-tree links are not required for the emulator.
# Keep Windows host configuration unchanged and use explicit runtime asset paths.
if os.environ.get('QEMU_LOCAL_NO_SYMLINK_BUNDLE') == '1':
    print('Local build: development symlink bundle disabled explicitly')
    sys.exit(0)
''')
    if relative=='hw/timer/Kconfig':
        return text+'\nconfig EXYNOS4210_PWM\n    bool\n    select PTIMER\n'
    if relative=='hw/timer/meson.build':
        return one(text,"when: 'CONFIG_EXYNOS4', if_true: files('exynos4210_pwm.c')","when: 'CONFIG_EXYNOS4210_PWM', if_true: files('exynos4210_pwm.c')")
    if relative=='hw/char/Kconfig':
        return text+'\nconfig EXYNOS4210_UART\n    bool\n'
    if relative=='hw/char/meson.build':
        return one(text,"when: 'CONFIG_EXYNOS4', if_true: files('exynos4210_uart.c')","when: 'CONFIG_EXYNOS4210_UART', if_true: files('exynos4210_uart.c')")
    if relative=='hw/rtc/Kconfig':
        return text+'\nconfig EXYNOS4210_RTC\n    bool\n    select PTIMER\n'
    if relative=='hw/rtc/meson.build':
        return one(text,"when: 'CONFIG_EXYNOS4', if_true: files('exynos4210_rtc.c')","when: 'CONFIG_EXYNOS4210_RTC', if_true: files('exynos4210_rtc.c')")
    if relative=='hw/rtc/exynos4210_rtc.c':
        text=one(text,'#include "qom/object.h"\n','#include "qom/object.h"\n#include "hw/core/qdev-properties.h"\n')
        text=one(text,'#define     RTCCON          0x0040','#define     AUTO9_TICCKSEL  0x0038\n#define     RTCCON          0x0040')
        text=one(text,'    uint32_t    reg_rtccon;','    uint32_t    reg_rtccon;\n    bool auto9_layout;\n    uint32_t reg_ticcksel;')
        text=one(text,'    s->freq = RTC_BASE_FREQ / (1 << TICCKSEL(reg_value));',
                 '    s->freq = RTC_BASE_FREQ / (1 << TICCKSEL(s->auto9_layout ?\n                         (((s->reg_ticcksel >> 1) & 15) << 4) : reg_value));')
        text=one(text,'        value = s->reg_curticcnt;',
                 '        /* Native LK compares ascending CURTICNT deadlines. */\n        value = s->auto9_layout ? s->reg_ticcnt - s->reg_curticcnt : s->reg_curticcnt;')
        text=one(text,'    case RTCCON:\n        value = s->reg_rtccon;',
                 '    case AUTO9_TICCKSEL:\n        if (s->auto9_layout) { value = s->reg_ticcksel; }\n        else { qemu_log_mask(LOG_GUEST_ERROR, "RTC non-Auto9 selector read\\n"); }\n        break;\n    case RTCCON:\n        value = s->reg_rtccon;')
        text=one(text,'    case RTCCON:\n        ptimer_transaction_begin(s->ptimer_1Hz);',
                 '''    case AUTO9_TICCKSEL:
        if (s->auto9_layout) {
            s->reg_ticcksel = value & 31;
            ptimer_transaction_begin(s->ptimer);
            exynos4210_rtc_update_freq(s, s->reg_rtccon);
            ptimer_transaction_commit(s->ptimer);
        } else {
            qemu_log_mask(LOG_GUEST_ERROR, "RTC non-Auto9 selector write\\n");
        }
        break;
    case RTCCON:
        ptimer_transaction_begin(s->ptimer_1Hz);''')
        text=one(text,'    s->reg_curticcnt = 0;','    s->reg_curticcnt = 0;\n    s->reg_ticcksel = 0;')
        text=one(text,'static void exynos4210_rtc_class_init(ObjectClass *klass, const void *data)\n',
                 '''static const Property exynos4210_rtc_properties[] = {
    DEFINE_PROP_BOOL("auto9-layout", Exynos4210RTCState, auto9_layout, false),
};

static void exynos4210_rtc_class_init(ObjectClass *klass, const void *data)
''')
        return one(text,'    dc->vmsd = &vmstate_exynos4210_rtc_state;',
                   '    dc->vmsd = &vmstate_exynos4210_rtc_state;\n    device_class_set_props(dc, exynos4210_rtc_properties);')
    if relative=='include/hw/arm/virt.h':
        return one(text,'    bool secure;\n','''    bool secure;
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
''')
    if relative=='hw/arm/Kconfig':
        text=one(text,'config ARM_VIRT\n    bool\n','config ARM_VIRT\n    bool\n    select EXYNOS4210_UART\n    select EXYNOS4210_RTC\n    select EXYNOS4210_PWM\n    select PL330\n    select UFS_PCI\n    select I2C\n    select AT24C\n    select SSI\n    select USB_DWC3\n')
        return one(text,'config EXYNOS4\n    bool\n','config EXYNOS4\n    bool\n    select EXYNOS4210_UART\n    select EXYNOS4210_RTC\n    select EXYNOS4210_PWM\n')
    text=one(text,'#include "hw/arm/virt.h"\n','#include "hw/arm/virt.h"\n#include "hw/arm/exynos4210.h"\n#include "qemu/log.h"\n#include "hw/arm/armv7m.h"\n#include "hw/cpu/cluster.h"\n#include "hw/core/qdev-clock.h"\n#include "qemu/timer.h"\n#include "exec/page-vary.h"\n#include "system/runstate.h"\n')
    text=one(text,'        cpuobj = object_new(possible_cpus->cpus[n].type);\n',
        '''        cpuobj = object_new(possible_cpus->cpus[n].type);
        if (vms->exynos_vlm) {
            /* Match original HCB /timer and both original guest DTBs. */
            object_property_set_int(cpuobj, "cntfrq", 26000000, &error_fatal);
            object_property_set_bool(cpuobj, "x-exynos-exact-counter", true,
                                     &error_fatal);
        }
''')
    text=one(text,'    uint8_t clustersz;\n','''    uint8_t clustersz;
    if (vms->exynos_vlm) {
        /* Exact MPIDRs from the retained eight-core hardware HCB. */
        return ((uint64_t)(idx / 4) << 16) | ((uint64_t)(idx % 4) << 8);
    }
''')
    text=one(text,'#define NUM_IRQS 256\n','''#define NUM_IRQS 256

/* The retained 8704 HCB wires UART SPI345; ordinary virt keeps 256 SPIs. */
static unsigned int virt_num_irqs(const VirtMachineState *vms)
{
    return vms->exynos_vlm ? 512 : NUM_IRQS;
}
''')
    text=text.replace('"spi-range", NUM_IRQS','"spi-range", virt_num_irqs(vms)')
    text=text.replace('int intidbase = NUM_IRQS +','int intidbase = virt_num_irqs(vms) +')
    if text.count('"num-irq", NUM_IRQS + 32')!=2:raise ValueError('Unexpected GIC IRQ anchors')
    text=text.replace('"num-irq", NUM_IRQS + 32','"num-irq", virt_num_irqs(vms) + 32')
    text=one(text,'    if (ms->ram_slots > ACPI_MAX_RAM_SLOTS) {','''    if (vms->exynos_ufs_mailbox &&
        (vms->exynos_ufs_mailbox != 1 || vms->exynos_ufs_host0 != 1)) {
        error_report("UFS mailbox requires physical HCI host0");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_qnx_dsi &&
        (vms->exynos_qnx_dsi != 1 || !vms->exynos_vlm)) {
        error_report("QNX DSIM requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_usb_phy &&
        (vms->exynos_linux_usb_phy != 1 || vms->exynos_linux_dwc3 != 1)) {
        error_report("Linux USB PHY requires the native Auto9 DWC3");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_mfc &&
        (vms->exynos_linux_mfc != 1 || !vms->exynos_vlm)) {
        error_report("Linux MFC cold reset requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_dwc3 &&
        (vms->exynos_linux_dwc3 != 1 || !vms->exynos_vlm)) {
        error_report("Linux DWC3 requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_qnx_dpp_irq &&
        (vms->exynos_qnx_dpp_irq != 1 || !vms->exynos_vlm)) {
        error_report("QNX DPP requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_dpp_irq &&
        (vms->exynos_linux_dpp_irq != 1 || !vms->exynos_vlm)) {
        error_report("Linux DPP IRQ requires native Auto9");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_ufs_vh &&
        (vms->exynos_ufs_vh != 1 || vms->exynos_ufs_mailbox != 1)) {
        error_report("UFS VH transport requires the physical/virtual mailbox");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_ufs_host0 &&
        (!vms->exynos_vlm || !vms->exynos_ufs || vms->exynos_ufs_host0 != 1)) {
        error_report("UFS physical host0 routing requires native Auto9 UFS");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_ufs_prelink &&
        (!vms->exynos_vlm || !vms->exynos_ufs || vms->exynos_ufs_prelink != 1)) {
        error_report("UFS prelink configuration requires native Auto9 UFS");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_ufs_shareability && (!vms->exynos_vlm || !vms->exynos_ufs)) {
        error_report("UFS shareability requires native Auto9 board and UFS");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_qnx_g3d00 &&
        (vms->exynos_qnx_g3d00 != 1 || !vms->exynos_vlm)) {
        error_report("QNX G3D00 requires native Auto9");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_qnx_g3d00_jm_topology &&
        (vms->exynos_qnx_g3d00_jm_topology > 2 || vms->exynos_qnx_g3d00 != 1)) {
        error_report("QNX Mali JM topology requires native QNX G3D00");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_mali_g3d1_id &&
        (!vms->exynos_vlm || vms->exynos_mali_g3d1_id > UINT32_MAX ||
         (vms->exynos_mali_g3d1_id & 0xf00f0000) != 0x70010000)) {
        error_report("G3D1 requires native Auto9 and a declared virtual G76 ID");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_abox_dsp_off &&
        (vms->exynos_abox_dsp_off != 1 || vms->exynos_abox_axiuser != 1 ||
         !vms->exynos_acpm || vms->exynos_pmu_profile != 1)) {
        error_report("ABOX DSP cold status requires ABOX AXIUSER and PMU/ACPM");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_abox_axiuser &&
        (vms->exynos_abox_axiuser != 1 || vms->exynos_abox_gic != 1)) {
        error_report("ABOX AXIUSER requires native ABOX GIC");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_abox_sysmmu &&
        (vms->exynos_abox_sysmmu != 1 || vms->exynos_linux_sysmmu != 1 ||
         vms->exynos_abox_gic != 1)) {
        error_report("ABOX DMA SysMMU requires native VM3 SysMMU and ABOX GIC");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_abox_gic &&
        (vms->exynos_abox_gic != 1 || !vms->exynos_vlm)) {
        error_report("ABOX GIC requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_qnx_bts &&
        (vms->exynos_qnx_bts != 1 || !vms->exynos_vlm)) {
        error_report("QNX BTS requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_scaler_reset &&
        (vms->exynos_scaler_reset > 2 || !vms->exynos_vlm)) {
        error_report("Linux Scaler reset requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_g2d_reset &&
        (vms->exynos_g2d_reset != 1 || !vms->exynos_vlm)) {
        error_report("FIMG2D reset requires the native Exynos board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_usi_uart &&
        (vms->exynos_linux_usi_uart != 1 || !vms->exynos_vlm)) {
        error_report("Linux USI UART requires the native Exynos board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_mali_g3d1_jm_topology &&
        (vms->exynos_mali_g3d1_jm_topology > 2 || !vms->exynos_mali_g3d1_empty_power ||
         (vms->exynos_mali_g3d1_jm_topology == 2 && !vms->exynos_vlm))) {
        error_report("G3D1 virtual JM topology requires empty power");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_mali_g3d1_empty_power &&
        (vms->exynos_mali_g3d1_empty_power != 1 || !vms->exynos_mali_g3d1_config)) {
        error_report("G3D1 empty power requires its configuration profile");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_mali_g3d1_config &&
        (vms->exynos_mali_g3d1_config != 1 || !vms->exynos_mali_g3d1_reset_irq)) {
        error_report("G3D1 configuration requires its reset IRQ profile");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_mali_g3d1_reset_irq &&
        (vms->exynos_mali_g3d1_reset_irq != 1 || !vms->exynos_mali_g3d1_discovery)) {
        error_report("G3D1 reset IRQ requires its discovery profile");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_mali_g3d1_discovery &&
        (vms->exynos_mali_g3d1_discovery != 1 || !vms->exynos_mali_g3d1_id)) {
        error_report("G3D1 discovery requires a declared virtual G76 ID");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_pcie2_atu &&
        (vms->exynos_pcie2_atu != 1 || vms->exynos_pcie2_dbi != 1)) {
        error_report("PCIe2 iATU requires native PCIe2 root DBI");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_pcie2_dbi &&
        (vms->exynos_pcie2_dbi != 1 || vms->exynos_pcie2_pcs != 1)) {
        error_report("PCIe2 DBI requires the native cold PCS profile");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_pcie2_pcs &&
        (vms->exynos_pcie2_pcs != 1 || vms->exynos_pcie2_phy != 1)) {
        error_report("PCIe2 PCS configuration requires native PCIe2 PMU/ELBI profiles");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_pcie2_phy &&
        (!vms->exynos_vlm || vms->exynos_pcie2_phy != 1 ||
         vms->exynos_pcie2_elbi != 1 || !vms->exynos_acpm || vms->exynos_pmu_profile != 1)) {
        error_report("PCIe2 PHY control requires native Auto9 ELBI and cold PMU");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_pcie2_elbi &&
        (!vms->exynos_vlm || vms->exynos_pcie2_elbi != 1)) {
        error_report("PCIe2 ELBI requires native Auto9 and profile 1");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_decon_window &&
        (vms->exynos_linux_decon_window != 1 || vms->exynos_linux_dpp_irq != 1)) {
        error_report("Linux WOD windows require Linux DPP");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_decon_window &&
        (!vms->exynos_vlm || vms->exynos_decon_window != 1 || vms->exynos_dpp_irq != 1)) {
        error_report("DECON window requires native Auto9 DPP profile");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_dp0_aux_sink &&
        (vms->exynos_dp0_irq != 1 || vms->exynos_dp0_aux_sink != 1)) {
        error_report("DP0 AUX sink requires the native DP0 plug IRQ registers");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_dp0_irq &&
        (vms->exynos_dp0_link_config != 1 || vms->exynos_dp0_irq != 1)) {
        error_report("DP0 plug registers require native DP0 LINK configuration");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_dp0_link_config &&
        (vms->exynos_dp0_phy != 1 || vms->exynos_dp0_link_config != 1)) {
        error_report("DP0 LINK configuration requires the native Auto9 DP0 PHY");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_dp0_phy &&
        (vms->exynos_dp0_reset != 1 || vms->exynos_dp0_phy != 1)) {
        error_report("DP0 PHY requires the native Auto9 DP0 reset word");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_dp0_reset &&
        (!vms->exynos_vlm || vms->exynos_dp0_reset != 1)) {
        error_report("DP0 reset requires the native Auto9 board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_dpp_irq &&
        (!vms->exynos_vlm || vms->exynos_dpp_irq != 1 || vms->exynos_sysmmu_discovery != 1)) {
        error_report("DPP IRQ needs native Auto9 and SysMMU discovery");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_linux_sysmmu &&
        (vms->exynos_linux_sysmmu != 1 || !vms->exynos_vlm)) {
        error_report("Linux SysMMU requires the native Exynos board");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_sysmmu_discovery &&
        (!vms->exynos_vlm || vms->exynos_sysmmu_discovery != 1)) {
        error_report("SysMMU discovery requires native Auto9 and profile 1");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_eqos_mac &&
        (!vms->exynos_vlm || vms->exynos_eqos_mac >> 48 ||
         (vms->exynos_eqos_mac & 3) != 2)) {
        error_report("EQoS requires native Auto9 and a local unicast MAC");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_ufs_phy_isolation &&
        (!vms->exynos_vlm || !vms->exynos_ufs || !vms->exynos_acpm ||
         vms->exynos_pmu_profile != 1)) {
        error_report("UFS0 PHY isolation config requires native UFS and PMU");
        exit(EXIT_FAILURE);
    }
    if (vms->exynos_vlm) {
        /* Original HCB hardware addresses; remove PCI MMIO overlap first. */
        vms->memmap[VIRT_PCIE_MMIO].base = 0x30000000;
        vms->memmap[VIRT_PCIE_MMIO].size = 0x0eff0000;
        vms->memmap[VIRT_GIC_DIST].base = 0x10101000;
        vms->memmap[VIRT_GIC_DIST].size = 0x1000;
        vms->memmap[VIRT_GIC_CPU].base = 0x10102000;
        vms->memmap[VIRT_GIC_CPU].size = 0x1000;
        vms->memmap[VIRT_GIC_HYP].base = 0x10104000;
        vms->memmap[VIRT_GIC_HYP].size = 0x2000;
        vms->memmap[VIRT_GIC_VCPU].base = 0x10106000;
        vms->memmap[VIRT_GIC_VCPU].size = 0x2000;
    }

    if (ms->ram_slots > ACPI_MAX_RAM_SLOTS) {''')
    text=one(text,'    create_gic(vms, sysmem);\n','''    create_gic(vms, sysmem);
    if (vms->exynos_vlm) {
        if (vms->gic_version != VIRT_GIC_VERSION_2) {
            error_report("x-exynos-vlm requires gic-version=2 (original HCB)");
            exit(EXIT_FAILURE);
        }
        /* A real UART device: normal TX/RX FIFO state and GIC IRQ handling. */
        vms->exynos_uart = exynos4210_uart_create(0x10300000, 256, 0, serial_hd(1),
                              qdev_get_gpio_in(vms->gic, 345));
        memory_region_init_io(&vms->exynos_usi, OBJECT(vms),
                              &exynos_vlm_usi_ops, vms, "exynos-auto9-usi", 8);
        memory_region_add_subregion(sysmem, 0x103000c4, &vms->exynos_usi);
        exynos_auto9_dintc_create(vms, 0, 0x10030000, 459, 2);
        exynos_auto9_dintc_create(vms, 1, 0x100a0000, 457, 1);
        exynos_auto9_dintc_create(vms, 2, 0x100b0000, 455, 2);
    }
''')
    vgens=''.join('        exynos_auto9_vgen_create(vms, '+str(index)+', '+hex(row['base'])+', '+str(row['layout'])+');\n'
                  for index,row in enumerate(platform.native_vgen_layout()))
    text=one(text,'        exynos_auto9_dintc_create(vms, 2, 0x100b0000, 455, 2);\n',
             '        exynos_auto9_dintc_create(vms, 2, 0x100b0000, 455, 2);\n'+vgens)
    s2mpus=''
    for index,row in enumerate(platform.native_s2mpu_layout()):
        controller='vms->gic' if row['controller']=='gic' else 'DEVICE(object_resolve_path("/machine/'+row['controller']+'", NULL))'
        s2mpus+='        exynos_auto9_s2mpu_create(vms, '+str(index)+', '+hex(row['base'])+', '+controller+', '+str(row['irq'])+');\n'
    text=one(text,vgens,vgens+s2mpus)
    ufs_vi=next(i for i,r in enumerate(platform.native_vgen_layout()) if r['base']==0x17dd0000)
    ufs_mi=next(i for i,r in enumerate(platform.native_s2mpu_layout()) if r['base']==0x17ca0000)
    text=one(text,s2mpus,s2mpus+'        if (vms->exynos_ufs) { exynos_auto9_ufs_create(vms, "/machine/exynos-vgen'+str(ufs_vi)+'", "/machine/exynos-s2mpu'+str(ufs_mi)+'"); }\n')
    gpios=''.join('        exynos_auto9_gpio_create(vms, '+str(index)+', '+hex(row['base'])+', '+hex(row['encoded_pins'])+', '+str(row['irq'])+');\n'
                  for index,row in enumerate(platform.native_gpio_layout()))
    text=one(text,s2mpus,s2mpus+gpios)
    alive=platform.native_alive_gpio_layout()
    alive_code='        {\n            DeviceState *alive = exynos_auto9_gpio_create(vms, 5, '+hex(alive['base'])+', '+hex(alive['encoded_pins'])+', 512);\n'
    for pin,irq in enumerate(alive['pin_irqs']):
        alive_code+='            qdev_connect_gpio_out_named(alive, "pin-irq", '+str(pin)+', qdev_get_gpio_in(vms->gic, '+str(irq)+'));\n'
    alive_code+='        }\n'
    aud=platform.native_aud_gpio_layout()
    alive_code+='        /* AUD physical IRQ is not described by HCB; no invented route. */\n'
    alive_code+='        exynos_auto9_gpio_create(vms, 6, '+hex(aud['base'])+', '+hex(aud['encoded_pins'])+', 512);\n'
    alive_code+='''        if (vms->exynos_cal_init_profile > 1) {
            error_report("Unsupported virtual CAL configuration profile"); exit(EXIT_FAILURE);
        }
        exynos_auto9_cal_init_create(vms, vms->exynos_cal_init_profile == 1);
'''
    alive_code+='        auto9_eqos_create(vms);\n        auto9_sysmmu_create(vms);\n        auto9_dpp_irq_create(vms);\n        auto9_dp0_reset_create(vms);\n        auto9_dwc3_create(vms);\n        auto9_mfc_create(vms);\n        auto9_usb_phy_create(vms);\n        auto9_qnx_dsi_create(vms);\n        auto9_qnx_decon_create(vms);\n        auto9_qnx_bts_create(vms);\n        auto9_decon_window_create(vms);\n        auto9_pcie2_elbi_create(vms);\n        auto9_mali_g3d1_create(vms);\n        auto9_g2d_create(vms);\n        auto9_scaler_create(vms);\n        auto9_abox_gic_create(vms);\n'
    alive_code+='        exynos_auto9_chipid_create(vms);\n'
    alive_code+='''        if (vms->exynos_lk_pwm_profile > 1) {
            error_report("Unsupported virtual LK PWM profile");
            exit(EXIT_FAILURE);
        }
        if (vms->exynos_lk_pwm_profile == 1) {
            /* Upstream 24MHz virtual PWM, not an Auto9 clock measurement.
             * LK polls the counter; IRQ outputs are deliberately unconnected. */
            DeviceState *pwm = qdev_new("exynos4210.pwm");
            object_property_add_child(OBJECT(vms), "exynos-lk-pwm", OBJECT(pwm));
            sysbus_realize_and_unref(SYS_BUS_DEVICE(pwm), &error_fatal);
            sysbus_mmio_map(SYS_BUS_DEVICE(pwm), 0, 0x103f0000);
        }
''' 
    alive_code+='        exynos_auto9_i2c_create(vms, 17, 0x10950000, 0x10821014, 361, false);\n'
    import linux_i2c_platform
    for row in linux_i2c_platform.native_layout():
        alive_code+='        exynos_auto9_i2c_create(vms, '+str(row['index'])+', '+hex(row['base'])+', '+hex(row['selector'])+', '+str(row['spi'])+', true);\n'
    alive_code+='''        exynos_auto9_usi_uart_create(vms, "exynos-usi09", 0x10960000, 0x10821018, 364, 256, 2, 2);
        if (vms->exynos_linux_usi_uart) {
            exynos_auto9_usi_uart_create(vms, "exynos-usi03", 0x10360000, 0x10221018, 351, 64, 3, 3);
            exynos_auto9_usi_uart_create(vms, "exynos-usi04", 0x10380000, 0x10221020, 353, 64, 4, 4);
            /* Use the upstream small-FIFO UART encoding for USI08. */
            exynos_auto9_usi_uart_create(vms, "exynos-usi08", 0x10940000, 0x10821010, 362, 64, 3, 5);
        }
'''
    alive_code+='        exynos_auto9_apm_create(vms);\n'
    alive_code+='        exynos_auto9_sci_create(vms);\n'
    alive_code+='        exynos_auto9_speedy_create(vms);\n'
    alive_code+='        exynos_auto9_pmu_create(vms);\n'
    alive_code+='        exynos_auto9_sss_create(vms);\n'
    alive_code+='        exynos_auto9_tmu_create(vms);\n'
    cmus=platform.native_cmu_layout()
    alive_code+='        exynos_auto9_cmu_create(vms, (const hwaddr[]){'+','.join(hex(r['base']) for r in cmus)+'}, '+str(len(cmus))+');\n'
    alive_code+='        exynos_auto9_apm_wdt_create(vms);\n'
    alive_code+='        exynos_auto9_apm_core_create(vms);\n'
    text=one(text,'    arm_load_kernel(ARM_CPU(first_cpu), machine, &vms->bootinfo);\n',
             '    arm_load_kernel(ARM_CPU(first_cpu), machine, &vms->bootinfo);\n    exynos_auto9_apm_core_bootstrap(vms);\n')
    vgen_index=next(i for i,r in enumerate(platform.native_vgen_layout()) if r['base']==0x1b250000)
    s2mpu_index=next(i for i,r in enumerate(platform.native_s2mpu_layout()) if r['base']==0x1b2f0000)
    alive_code+='        exynos_auto9_pdma_create(vms, 0x1b2e0000, 48, "/machine/exynos-vgen'+str(vgen_index)+'", "/machine/exynos-s2mpu'+str(s2mpu_index)+'");\n'
    import sysreg_platform
    for row in sysreg_platform.native_layout():
        alive_code+='        exynos_auto9_usi_spi_create(vms, '+str(row['port'])+', '+hex(row['base'])+', '+hex(row['selector'])+', '+str(row['spi'])+', '+str(row['tx_request'])+', '+str(row['rx_request'])+', '+str(row['fifo_bytes'])+');\n'
    text=one(text,gpios,gpios+alive_code)
    text=one(text,gpios,gpios+'''        {
            DeviceState *rtc = qdev_new("exynos4210.rtc");
            object_property_add_child(OBJECT(vms), "exynos-rtc", OBJECT(rtc));
            qdev_prop_set_bit(rtc, "auto9-layout", true);
            sysbus_realize_and_unref(SYS_BUS_DEVICE(rtc), &error_fatal);
            sysbus_mmio_map(SYS_BUS_DEVICE(rtc), 0, 0x10540000);
            /* HCB has no physical RTC IRQ route: do not invent an SPI. */
        }
''')
    text=one(text,'        Chardev *serial1 = serial_hd(1);','        Chardev *serial1 = vms->exynos_vlm ? NULL : serial_hd(1);')
    text=one(text,'static void virt_instance_init(Object *obj)\n','''static bool virt_get_exynos_vlm(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->exynos_vlm;
}

static void virt_set_exynos_vlm(Object *obj, bool value, Error **errp)
{
    VIRT_MACHINE(obj)->exynos_vlm = value;
}

static bool virt_get_exynos_acpm(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->exynos_acpm;
}

static void virt_set_exynos_acpm(Object *obj, bool value, Error **errp)
{
    /* The management core needs 1KiB internal pages. Reserve that minimum
     * before the first A-profile CPU finalizes the global translation size.
     * This is an emulator granule, not a change to guest page tables.
     */
    if (value && !set_preferred_target_page_bits(10)) {
        error_setg(errp, "ACPM management core requires 1KiB internal pages before CPU realization");
        return;
    }
    VIRT_MACHINE(obj)->exynos_acpm = value;
}

static char *virt_get_exynos_acpm_firmware(Object *obj, Error **errp)
{
    return g_strdup(VIRT_MACHINE(obj)->exynos_acpm_firmware);
}
static void virt_set_exynos_acpm_firmware(Object *obj, const char *value, Error **errp)
{
    VirtMachineState *vms = VIRT_MACHINE(obj);
    g_free(vms->exynos_acpm_firmware);
    vms->exynos_acpm_firmware = g_strdup(value);
}

static bool virt_get_exynos_acpm_doorbells(Object *obj, Error **errp)
{
    return VIRT_MACHINE(obj)->exynos_acpm_doorbells;
}

static void virt_set_exynos_acpm_doorbells(Object *obj, bool value, Error **errp)
{
    VIRT_MACHINE(obj)->exynos_acpm_doorbells = value;
}

static void virt_instance_init(Object *obj)
''')
    models='\n'.join(Path(__file__).with_name(name).read_text(encoding='utf-8') for name in ('exynos-auto9-cal-init.inc.c','exynos-auto9-88q2112.inc.c','exynos-auto9-eqos.inc.c','exynos-auto9-sysmmu.inc.c','exynos-auto9-dpp.inc.c','exynos-auto9-dp0.inc.c','exynos-auto9-dwc3.inc.c','exynos-auto9-mfc.inc.c','exynos-auto9-usb-phy.inc.c','exynos-auto9-qnx-dsi.inc.c','exynos-auto9-qnx-decon.inc.c','exynos-auto9-qnx-bts.inc.c','exynos-auto9-decon-window.inc.c','exynos-auto9-pcie2-root.inc.c','exynos-auto9-pcie2-elbi.inc.c','exynos-auto9-mali-g3d1.inc.c','exynos-auto9-g2d-reset.inc.c','exynos-auto9-scaler-reset.inc.c','exynos-auto9-abox-gic.inc.c','exynos-auto9-dintc.inc.c','exynos-auto9-vgen.inc.c','exynos-auto9-s2mpu.inc.c','exynos-auto9-gpio.inc.c','exynos-auto9-chipid.inc.c','exynos-auto9-pdma.inc.c','exynos-auto9-apm-mailbox.inc.c','exynos-auto9-sci.inc.c','exynos-auto9-speedy.inc.c','exynos-auto9-pmu-domains.inc.c','exynos-auto9-abox-mailbox.inc.c','exynos-auto9-abox-sft.inc.c','exynos-auto9-pmu.inc.c','exynos-auto9-sss.inc.c','exynos-auto9-tmu.inc.c','exynos-auto9-cmu-configs.inc.c','exynos-auto9-cmu.inc.c','exynos-auto9-apm-wdt.inc.c','exynos-auto9-apm-core.inc.c','exynos-auto9-ufs.inc.c','exynos-auto9-i2c.inc.c','exynos-auto9-ti941.inc.c','exynos-auto9-usi-uart.inc.c','exynos-auto9-mcu-spi.inc.c','exynos-auto9-usi-spi.inc.c'))
    for header in ('exynos-auto9-bifrost-vertex3.h','exynos-auto9-bifrost-viewport.h'):
        models=Path(__file__).with_name(header).read_text(encoding='utf-8')+'\n'+models
    models=one(models,'/* AUTO9_VM3_VERTEX3_MODEL */',
               Path(__file__).with_name('exynos-auto9-mali-vertex3.inc.c').read_text(encoding='utf-8'))
    models+='\n'+Path(__file__).with_name('exynos-auto9-ti983.inc.c').read_text(encoding='utf-8')
    text=one(text,'static void create_gic(VirtMachineState *vms, MemoryRegion *mem)\n',models+'\n'+'''/* Auto9 USI configuration words observed at UART+0xc4/+0xc8. The core UART
 * still implements TX/RX FIFOs and IRQs. Configuration is retained and a
 * software reset resets the UART; unmodelled register ranges remain unmapped.
 */
static uint64_t exynos_vlm_usi_read(void *opaque, hwaddr offset, unsigned size)
{
    VirtMachineState *vms = opaque;
    return offset == 0 ? vms->exynos_usi_con : vms->exynos_usi_option;
}

static void exynos_vlm_usi_write(void *opaque, hwaddr offset,
                               uint64_t value, unsigned size)
{
    VirtMachineState *vms = opaque;
    if (offset == 0) {
        vms->exynos_usi_con = value;
        if (value & 1) {
            device_cold_reset(vms->exynos_uart);
        }
    } else {
        vms->exynos_usi_option = value;
    }
}

static const MemoryRegionOps exynos_vlm_usi_ops = {
    .read = exynos_vlm_usi_read,
    .write = exynos_vlm_usi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static void create_gic(VirtMachineState *vms, MemoryRegion *mem)
''')
    text=one(text,'    vms->secure = false;\n','''    vms->secure = false;
    object_property_add_bool(obj, "x-exynos-vlm",
                             virt_get_exynos_vlm, virt_set_exynos_vlm);
    object_property_add_bool(obj, "x-exynos-acpm",
                             virt_get_exynos_acpm, virt_set_exynos_acpm);
    object_property_add_bool(obj, "x-exynos-ufs",
                             virt_get_exynos_ufs, virt_set_exynos_ufs);
    object_property_add_uint64_ptr(obj, "x-exynos-ufs-mailbox", &vms->exynos_ufs_mailbox,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-qnx-dsi", &vms->exynos_qnx_dsi,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-qnx-bts", &vms->exynos_qnx_bts,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-usb-phy",
                                   &vms->exynos_linux_usb_phy, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-mfc", &vms->exynos_linux_mfc,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-dwc3", &vms->exynos_linux_dwc3,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-qnx-dpp-irq", &vms->exynos_qnx_dpp_irq,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-dpp-irq", &vms->exynos_linux_dpp_irq,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-ufs-vh", &vms->exynos_ufs_vh,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-ufs-host0", &vms->exynos_ufs_host0,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-ufs-prelink", &vms->exynos_ufs_prelink,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_bool(obj, "x-exynos-ufs-shareability",
                             virt_get_exynos_ufs_shareability, virt_set_exynos_ufs_shareability);
    object_property_add_bool(obj, "x-exynos-ufs-phy-isolation",
                             virt_get_exynos_ufs_phy_isolation, virt_set_exynos_ufs_phy_isolation);
    object_property_add_uint64_ptr(obj, "x-exynos-acpm-hz", &vms->exynos_acpm_hz,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pscdc-profile", &vms->exynos_pscdc_profile,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pscdc-cycles", &vms->exynos_pscdc_cycles,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pmu-profile", &vms->exynos_pmu_profile,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-tmu-profile", &vms->exynos_tmu_profile,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mcu-spi6-startup", &vms->exynos_mcu_spi6_startup,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-lk-pwm", &vms->exynos_lk_pwm_profile,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-abox-dsp-off", &vms->exynos_abox_dsp_off,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-abox-axiuser", &vms->exynos_abox_axiuser,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-abox-sysmmu", &vms->exynos_abox_sysmmu,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-abox-gic", &vms->exynos_abox_gic,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-scaler-reset", &vms->exynos_scaler_reset,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-g2d-reset",
                                   &vms->exynos_g2d_reset, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-usi-uart",
                                   &vms->exynos_linux_usi_uart, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-qnx-g3d00",
                                   &vms->exynos_qnx_g3d00, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-qnx-g3d00-jm-topology",
                                   &vms->exynos_qnx_g3d00_jm_topology, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mali-g3d1-empty-power",
                                   &vms->exynos_mali_g3d1_empty_power, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mali-g3d1-jm-topology",
                                   &vms->exynos_mali_g3d1_jm_topology, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mali-g3d1-config",
                                   &vms->exynos_mali_g3d1_config, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mali-g3d1-reset-irq",
                                   &vms->exynos_mali_g3d1_reset_irq, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mali-g3d1-discovery",
                                   &vms->exynos_mali_g3d1_discovery, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-mali-g3d1-id", &vms->exynos_mali_g3d1_id,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pcie2-atu", &vms->exynos_pcie2_atu,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pcie2-dbi", &vms->exynos_pcie2_dbi,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pcie2-pcs", &vms->exynos_pcie2_pcs,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pcie2-phy", &vms->exynos_pcie2_phy,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-pcie2-elbi", &vms->exynos_pcie2_elbi,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-decon-window",
                                   &vms->exynos_linux_decon_window, OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-decon-window", &vms->exynos_decon_window,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-dp0-aux-sink", &vms->exynos_dp0_aux_sink,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-dp0-irq", &vms->exynos_dp0_irq,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-dp0-link-config",
                                   &vms->exynos_dp0_link_config,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-dp0-phy", &vms->exynos_dp0_phy,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-dp0-reset", &vms->exynos_dp0_reset,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-dpp-irq", &vms->exynos_dpp_irq,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-linux-sysmmu", &vms->exynos_linux_sysmmu,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-sysmmu-discovery", &vms->exynos_sysmmu_discovery,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-eqos-mac", &vms->exynos_eqos_mac,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_uint64_ptr(obj, "x-exynos-cal-init", &vms->exynos_cal_init_profile,
                                   OBJ_PROP_FLAG_READWRITE);
    object_property_add_str(obj, "x-exynos-acpm-firmware",
                            virt_get_exynos_acpm_firmware, virt_set_exynos_acpm_firmware);
    object_property_add_bool(obj, "x-exynos-acpm-doorbells",
                             virt_get_exynos_acpm_doorbells, virt_set_exynos_acpm_doorbells);
    object_property_set_description(obj, "x-exynos-vlm",
        "Local 8704 Exynos UART/GIC layout; not a complete ExynosAuto9 SoC");
''')
    return text


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=vm.ROOT/'.local/build/qemu-11.1.0')
    parser.add_argument('--archive',type=Path,default=vm.ROOT/'native/upstream/qemu-11.1.0.tar.xz')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--previous-patch',type=Path,help='Explicit owned patch manifest for a hash-checked in-place upgrade; unchanged source bytes are not rewritten')
    args=parser.parse_args()
    source=args.source_root.resolve();out=args.output.resolve()
    if not source.is_relative_to(vm.ROOT/'.local/build') or not out.is_relative_to(vm.ROOT/'var') or out.exists():
        raise ValueError('Require scoped separate source and new scratch output')
    names=('hw/arm/virt.c','include/hw/arm/virt.h','hw/arm/Kconfig','hw/char/Kconfig','hw/char/meson.build','scripts/symlink-install-tree.py',
           'hw/rtc/Kconfig','hw/rtc/meson.build','hw/rtc/exynos4210_rtc.c','hw/timer/Kconfig','hw/timer/meson.build',
           'system/physmem.c','include/system/memory.h','hw/dma/pl330.c','system/qtest.c','hw/ufs/ufs.c','hw/ufs/ufs.h','hw/ufs/trace-events','gdbstub/system.c','target/arm/tcg/tlb_helper.c','hw/intc/arm_gic.c','include/hw/intc/arm_gic_common.h','backends/hostmem-ram.c',
           'target/arm/cpu.h','target/arm/cpu.c','target/arm/helper.c',
           'target/arm/tcg/helper-a64-defs.h','target/arm/tcg/helper-a64.c','target/arm/tcg/translate-a64.c',
           'target/arm/tcg/op_helper.c','target/arm/tcg/psci.c','target/arm/tcg/trace-events','accel/tcg/tcg-all.c','hw/core/machine-qmp-cmds.c')
    if vm.sha(args.archive)!=ARCHIVE_SHA256:raise ValueError('Pinned official QEMU source archive identity mismatch')
    before={}
    with tarfile.open(args.archive,'r|xz') as archive:
        for member in archive:
            relative=member.name.removeprefix('qemu-11.1.0/')
            if relative in names:
                if not member.isfile():raise ValueError('Patch input is not regular')
                before[relative]=archive.extractfile(member).read()
                if len(before)==len(names):break
    if set(before)!=set(names):raise ValueError('Source archive incomplete')
    previous={}
    if args.previous_patch:
        previous_path=args.previous_patch.resolve()
        if not previous_path.is_relative_to(vm.ROOT/'var'):raise ValueError('Previous patch must be retained task evidence')
        manifest=json.loads(previous_path.read_text(encoding='utf-8'))
        if Path(manifest['source']).resolve()!=source or manifest['archive_sha256']!=ARCHIVE_SHA256:
            raise ValueError('Previous patch belongs to a different source/archive')
        previous={row['path']:row for row in manifest['files']}
    changes={}
    for relative in names:
        original=before[relative]
        actual=(source/relative).read_bytes()
        after=edits(relative,original.decode('utf-8')).encode('utf-8')
        if actual not in (original,after):
            old=previous.get(relative)
            if not old or old['before_sha256']!=hashlib.sha256(original).hexdigest() or old['after_sha256']!=hashlib.sha256(actual).hexdigest():
                raise ValueError('Preserve modified/different source file: '+relative)
        changes[relative]=after
    out.mkdir(parents=True)
    diff=[];records=[]
    for relative in names:
        original=before[relative];after=changes[relative]
        backup=out/'before'/relative;backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(original)
        target=source/relative
        changed=target.read_bytes()!=after
        if changed:
            temporary=target.with_name(target.name+'.exynos-tmp');temporary.write_bytes(after);os.replace(temporary,target)
        diff.extend(difflib.unified_diff(original.decode().splitlines(True),after.decode().splitlines(True),fromfile='a/'+relative,tofile='b/'+relative))
        records.append({'path':relative,'before_sha256':hashlib.sha256(original).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),'changed_this_run':changed})
    (out/'qemu-exynos-vlm.patch').write_text(''.join(diff),encoding='utf-8')
    vm.save(out/'patch.json',{'source':str(source),'archive':str(args.archive),'archive_sha256':vm.sha(args.archive),'files':records,
                             'previous_patch':str(args.previous_patch.resolve()) if args.previous_patch else None,
                             'dintc_source_sha256':vm.sha(Path(__file__).with_name('exynos-auto9-dintc.inc.c')),
                             'vgen_source_sha256':vm.sha(Path(__file__).with_name('exynos-auto9-vgen.inc.c')),
                             'vgen_hardware_nodes':platform.native_vgen_layout(),
                             's2mpu_source_sha256':vm.sha(Path(__file__).with_name('exynos-auto9-s2mpu.inc.c')),
                             's2mpu_hardware_nodes':platform.native_s2mpu_layout(),
                             'gpio_source_sha256':vm.sha(Path(__file__).with_name('exynos-auto9-gpio.inc.c')),
                             'gpio_hardware_nodes':platform.native_gpio_layout(),
                             'cmu_hardware_ports':platform.native_cmu_layout(),
                             'model_sources':{p.name:vm.sha(p) for p in sorted(Path(__file__).parent.glob('exynos-auto9-*.inc.c'))},
                             'alive_gpio':platform.native_alive_gpio_layout(),'aud_gpio':platform.native_aud_gpio_layout(),
                             'effect':'Opt-in original board addresses/topology, VGEN/S2MPU, GPIO, RTC, ChipID, real PL330, native-address UFS standard HCI, APM mailbox and separate original-code management CPU; no Hypervisor or guest kernel patch',
                             'limitations':['Not a complete ExynosAuto9 SoC','DINTC/GPIO/APM semantics partially inferred; no silicon validation',
                                'Optional x-exynos-lk-pwm=1 maps the unmodified upstream Exynos4210 PWM at source-bound LK103F0000; 24MHz input is a declared virtual assumption, IRQ outputs and physical PWM wires are unconnected; default aperture absent',
                                'PDMA uniform VGEN VID only; native DT 36-bit instruction/AR/AW wrappers use assumed behavior-level prefixes and internal class/channel metadata, not recovered silicon AxID; bus errors fault actual engine, mixed VIDs reject',
                                'ACPM core requires derived 1KiB-granule machine, explicit virtual clock and verified BL2 load bytes; not complete ACPM or fabricated FVMap',
                                'APM counter 0x61 mode inferred from original code; IRQ/one-shot 0x73 not implemented',
                                'SCI/VCH bounded inferred configuration and empty-LLC work only; Speedy optional explicit common-interface byte is not full PMIC/identity/regulator/IRQ/burst model',
                                'Native UFS HCI retains upstream default descriptor behavior; explicit x-samsung-byte-granularity implements original legacy byte-unit UTRD/PRDT and rejects partial SG entries or MCQ. Bounded vendor configuration/backend-reset only; default uniform VGEN2..4 or explicit physical-HCI entry0/VID2; no physical PHY/FMP/OEM partitions or per-slot/VF routing; denied SG mappings wait for actual permission repair, not silicon-verified fatal-error sequencing',
                                'Original QNX storage startup and display/GPU backend not implemented',
                                'Optional virtual-ti941-iid-v1 is an original I2C17/1A finite control endpoint driven by real GPIO4 output34 PDB and virtual deadlines; indirect register retention and an explicitly disconnected OEM cable probe, not exact TI silicon, trained DSI/FPD, HDCP, deserializer or video frames',
                                'QNX I2C17 AUTO master uses virtual22500ns byte deadlines and real bus/FIFOs/IRQ361; no native endpoints/manual/10bit/HS wire. USI09 observed selector1 gates real upstream UART MMIO/IRQ364, not SPI/I2C or physical clock/power modeling; optional EEPROM is QTest fixture only',
                                'AUD physical IRQ route unknown','Local derived HCB and loader RAM, not factory physical memory description',
                                '22 source-bound PLL engines use virtual programmed-cycle lock time; AUD/MMC signed fractional K supported; full gates/clock tree/analog behavior absent',
                                'PSCDC strict default rejects execution; explicit virtual-oem-v1 is an assumed behavior-level queue/replica contract, not recovered silicon ISA or DRAM training',
                                'PMU optional virtual-cold-v1 retains finite original configs/atomic aliases, has real virtual timers and event latches; cold inputs assumed zero, analog power/CPU release/physical IRQ delivery absent',
                                'QNX display isolation730/738/73C/740 retains bit0 config only;840 is a real software handoff word starting0, never auto-marked A5; full LK UFS completion/PHY training absent',
                                'Optional PMU domain virtual-sequencer-v1 uses20 original CAL configuration/status pairs with declared supply-good and real virtual deadlines; logical active GPIO only, no analog rail/clock/GPU/DP/reset effects; OUT bit2 request retention is not physical reset delivery',
                                'Optional virtual-input-bank-v1 exposes exactly three opaque external debug inputs at1046210C/18A00604/18A00614; physical field meanings unknown,96 real virtual GPIO inputs, no power/clock/ready effects. DPTX18A00800 retains only original CAL save-mask bits28/29, no physical automatic-gating behavior',
                                'TMU optional virtual-onepoint-v1 preserves legacy ADC; opt-in virtual-qnx-controller-v1 adds independently enabled conversions, original finite control/threshold/W1C and real GIC462/461; cold enable/timing are assumptions, no physical trim/analog/history/migration',
                                'Optional x-exynos-cal-init=1 retains only91 original QNX lpm_init op1/op3 configuration rows in PMU/CMU and42 discrete SYSREG words; virtual cold0, no IRQ/clock/power/status completion effects',
                                'Optional x-exynos-dp0-reset=1 models one VM2 DP0 software-reset word with a virtual deadline; optional x-exynos-dp0-phy=1 recognizes OEM PHY configuration and a modeled local PLL lock; x-exynos-dp0-link-config=1 adds three original config words; x-exynos-dp0-irq=1 adds plug status W1C, mask, and HPD deglitch with no sink or asserted HPD; no AUX, IRQ delivery or scanout',
                                 'Optional x-exynos-linux-dwc3=1 uses upstream DWC3/xHCI at VM3 original 0x17200000 with IRQ199; DMA faults in an empty address space, no attached device, gadget, or role switching',
                                'Added device migration state not implemented' ]})
    print(json.dumps({'source':str(source),'files_changed':len(records),'patch':str(out/'qemu-exynos-vlm.patch')}))


if __name__=='__main__':main()
