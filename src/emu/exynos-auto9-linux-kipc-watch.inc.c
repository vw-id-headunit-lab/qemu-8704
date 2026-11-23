/* Observe the original Linux persistence KIPC route and dequeue, not its data. */
#ifndef CONFIG_USER_ONLY
#include "qemu/bswap.h"
#include "trace.h"

#define AUTO9_KIPC_ROUTE_PC UINT64_C(0xffffffc010584d20)
#define AUTO9_KIPC_POP_PC UINT64_C(0xffffffc010584974)

void HELPER(auto9_vm3_kipc_watch)(CPUARMState *env, uint32_t edge)
{
    static const uint8_t site[2][16] = {
        { 0xfd, 0x7b, 0xbf, 0xa9, 0xfd, 0x03, 0x00, 0x91,
          0x01, 0x04, 0x40, 0x79, 0x3f, 0xe0, 0x0f, 0x71 },
        { 0xe0, 0x03, 0x15, 0xaa, 0xf3, 0x53, 0x41, 0xa9,
          0xf5, 0x5b, 0x42, 0xa9, 0xfd, 0x7b, 0xc3, 0xa8 },
    };
    static unsigned rows[8]; /* One writer per AP vCPU index. */
    CPUState *cpu = env_cpu(env);
    int index = cpu->cpu_index;
    uint64_t pkt, body, pc;
    uint8_t code[16], hdr[14], msg[12];
    unsigned src, dst, len;
    const char *kind = "other";

    if (!trace_event_get_state_backends(TRACE_AUTO9_VM3_KIPC_WATCH) ||
        edge >= ARRAY_SIZE(site) || index < 0 || index >= ARRAY_SIZE(rows) ||
        rows[index] >= 512 || (env->cp15.vttbr_el2 >> 48) != 3 ||
        arm_current_el(env) != 1 || !is_a64(env)) {
        return;
    }
    pkt = edge ? env->xregs[21] : env->xregs[0];
    if (!auto9_vm3_signal_read(cpu, pkt, hdr, sizeof(hdr))) {
        return;
    }
    src = lduw_le_p(hdr);
    dst = lduw_le_p(hdr + 2);
    if (!((src == 15 && dst == 52) || (src == 52 && dst == 15))) {
        return;
    }
    pc = edge ? AUTO9_KIPC_POP_PC : AUTO9_KIPC_ROUTE_PC;
    if (!auto9_vm3_signal_read(cpu, pc, code, sizeof(code)) ||
        memcmp(code, site[edge], sizeof(code))) {
        return;
    }
    len = lduw_le_p(hdr + 4);
    body = ldq_le_p(hdr + 6);
    /* The fixed envelope is classified locally; no guest bytes are logged. */
    if (len >= sizeof(msg) && len <= 0x6800 &&
        auto9_vm3_signal_read(cpu, body, msg, sizeof(msg))) {
        uint32_t iface = ldl_le_p(msg);
        uint32_t api = ldl_le_p(msg + 8);

        if (src == 15 && iface == 1400 && api == 9999) {
            kind = "subscription";
        } else if (src == 52 && iface == 9000) {
            kind = "welcome";
        } else if (src == 52 && (iface == 1400 || iface == 1401) && api == 1) {
            kind = "database-medium";
        }
    }
    trace_auto9_vm3_kipc_watch(index, ++rows[index], edge ? "dequeue" : "route",
                              src, dst, kind);
}
#endif
