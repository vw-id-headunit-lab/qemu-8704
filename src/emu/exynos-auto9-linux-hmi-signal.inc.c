/* Observe the shipped VM3 Linux signal frame input without changing it. */
#ifndef CONFIG_USER_ONLY
#include "qemu/bswap.h"
#include "trace.h"

#define AUTO9_VM3_SIGNAL_ENTRY UINT64_C(0xffffffc01008ea98)
#define AUTO9_VM3_TASK_LEADER 0x548
#define AUTO9_VM3_TASK_THREAD_PID 0x570
#define AUTO9_VM3_TASK_COMM 0x6c0
#define AUTO9_VM3_PID_FIRST_NR 0x50
#define AUTO9_VM3_KSIGNAL_INFO 0x20
#define AUTO9_VM3_PTREGS_PC 0x100

static bool auto9_vm3_signal_read(CPUState *cpu, uint64_t addr, void *dst,
                                 size_t len)
{
    return addr >= UINT64_C(0xffffff8000000000) &&
        addr <= UINT64_MAX - len &&
        !cpu_memory_rw_debug(cpu, addr, dst, len, false);
}

static uint64_t auto9_vm3_signal_ptr(CPUState *cpu, uint64_t addr)
{
    uint8_t data[8];
    return auto9_vm3_signal_read(cpu, addr, data, sizeof(data)) ?
        ldq_le_p(data) : 0;
}

static uint32_t auto9_vm3_signal_tid(CPUState *cpu, uint64_t task)
{
    uint64_t pid = auto9_vm3_signal_ptr(cpu, task + AUTO9_VM3_TASK_THREAD_PID);
    uint8_t data[4];
    return pid && auto9_vm3_signal_read(cpu, pid + AUTO9_VM3_PID_FIRST_NR,
                                       data, sizeof(data)) ? ldl_le_p(data) : 0;
}

void HELPER(auto9_vm3_hmi_signal)(CPUARMState *env)
{
    CPUState *cpu = env_cpu(env);
    int index = cpu->cpu_index;
    unsigned sig = env->xregs[0];
    uint64_t current = env->sp_el[0], leader, pc, addr = 0;
    uint32_t tid, tgid;
    uint8_t code[16], info[24], name[5];
    int32_t si_code;
    /* Independent counters: each index is written only by its AP vCPU. */
    static unsigned rows[8];
    static const uint8_t entry[] = {
        0xfd, 0x7b, 0xb5, 0xa9, 0xfd, 0x03, 0x00, 0x91,
        0xf3, 0x53, 0x01, 0xa9, 0xf4, 0x03, 0x03, 0xaa,
    };

    if (!trace_event_get_state_backends(TRACE_AUTO9_VM3_HMI_SIGNAL) ||
        index < 0 || index >= ARRAY_SIZE(rows) || rows[index] >= 256 ||
        (env->cp15.vttbr_el2 >> 48) != 3 || arm_current_el(env) != 1 ||
        !is_a64(env) || (sig != 1 && sig != 4 && sig != 7 && sig != 8 &&
                         sig != 11)) {
        return;
    }
    if (!auto9_vm3_signal_read(cpu, AUTO9_VM3_SIGNAL_ENTRY, code, sizeof(code)) ||
        memcmp(code, entry, sizeof(entry))) {
        return;
    }
    leader = auto9_vm3_signal_ptr(cpu, current + AUTO9_VM3_TASK_LEADER);
    if (!leader ||
        !auto9_vm3_signal_read(cpu, leader + AUTO9_VM3_TASK_COMM, name, sizeof(name)) ||
        memcmp(name, "JAVA", sizeof(name))) {
        return;
    }
    tid = auto9_vm3_signal_tid(cpu, current);
    tgid = auto9_vm3_signal_tid(cpu, leader);
    if (!tid || !tgid || !auto9_vm3_signal_read(cpu,
        env->xregs[1] + AUTO9_VM3_KSIGNAL_INFO, info, sizeof(info)) ||
        ldl_le_p(info) != sig ||
        !auto9_vm3_signal_read(cpu, env->xregs[3] + AUTO9_VM3_PTREGS_PC,
                              code, 16)) {
        return;
    }
    pc = ldq_le_p(code);
    if (ldq_le_p(code + 8) & 0x1f) {
        return; /* Not a saved native AArch64 EL0 PSTATE. */
    }
    si_code = (int32_t)ldl_le_p(info + 8);
    if (si_code > 0 && sig != 1) {
        addr = ldq_le_p(info + 16);
    }
    trace_auto9_vm3_hmi_signal(index, ++rows[index], tgid, tid, sig,
                               si_code, pc, addr);
}
#endif
