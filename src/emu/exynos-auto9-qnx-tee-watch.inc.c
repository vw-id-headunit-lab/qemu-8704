/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CONFIG_USER_ONLY
/* Observe two original tee-resmgr boot calls. Do not log arguments, shared
 * buffers, TA data or raw return registers. State belongs to its vCPU thread.
 */
typedef struct Auto9TeeWatch {
    uint64_t call_pc;
    uint32_t id;
    unsigned count;
    bool forward_seen;
} Auto9TeeWatch;

static Auto9TeeWatch auto9_qnx_tee_calls[8];

void auto9_qnx_tee_eret(CPUARMState *env, int cur_el, int new_el, uint64_t pc);

static void auto9_qnx_tee_edge(CPUARMState *env, int el, const char *route)
{
    int index = env_cpu(env)->cpu_index;
    uint64_t id = env->xregs[0];
    Auto9TeeWatch *watch;

    if (!trace_event_get_state_backends(TRACE_AUTO9_QNX_TEE_EDGE) ||
        !env->aarch64 || (unsigned)index >= G_N_ELEMENTS(auto9_qnx_tee_calls) ||
        (env->cp15.vttbr_el2 >> 48) != 2) {
        return;
    }
    watch = &auto9_qnx_tee_calls[index];
    if (el == 1 && watch->count < 8 &&
        ((id == 0xbf000001 && (env->pc & 0xfff) == 0x8a8) ||
         (id == 0xbf00000a && (env->pc & 0xfff) == 0xf94))) {
        watch->call_pc = env->pc;
        watch->id = id;
        watch->forward_seen = false;
        watch->count++;
        trace_auto9_qnx_tee_edge(index, id, el, route);
    } else if (el == 2 && watch->id && watch->id == id && !watch->forward_seen) {
        watch->forward_seen = true;
        trace_auto9_qnx_tee_edge(index, id, el, route);
    }
}

void auto9_qnx_tee_eret(CPUARMState *env, int cur_el, int new_el, uint64_t pc)
{
    int index = env_cpu(env)->cpu_index;
    Auto9TeeWatch *watch;
    uint64_t value;

    if (!trace_event_get_state_backends(TRACE_AUTO9_QNX_TEE_EDGE) ||
        cur_el != 2 || new_el != 1 || (unsigned)index >= G_N_ELEMENTS(auto9_qnx_tee_calls) ||
        (env->cp15.vttbr_el2 >> 48) != 2) {
        return;
    }
    watch = &auto9_qnx_tee_calls[index];
    if (!watch->id || (pc != watch->call_pc && pc != watch->call_pc + 4)) {
        return;
    }
    value = env->xregs[0];
    trace_auto9_qnx_tee_edge(index, watch->id, cur_el,
        pc == watch->call_pc ? "eret-retry" : value == UINT64_MAX ? "eret-all-ones" :
        value == 0 ? "eret-zero" : "eret-other");
    watch->id = 0;
}
#endif
