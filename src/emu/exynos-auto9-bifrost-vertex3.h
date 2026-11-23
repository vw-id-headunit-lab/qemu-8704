/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef EXYNOS_AUTO9_BIFROST_VERTEX3_H
#define EXYNOS_AUTO9_BIFROST_VERTEX3_H

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define AUTO9_BIFROST_VM3_VERTEX3_SIZE 400u
#define AUTO9_BIFROST_VM3_VERTEX3_SHA256 \
    "1df6028d8211fba7f8491d8f3d9b545e0d703a9d5277ce507984c58e7f14cdfa"
#define AUTO9_BIFROST_VM3_VERTEX4_SIZE 560u
#define AUTO9_BIFROST_VM3_VERTEX4_SHA256 \
    "21d1e8030d0dd9cffb48d171cc4d8889fe332f2c4c69190a0a6b36606943ec32"
#define AUTO9_BIFROST_VM3_VERTEX45_SIZE 608u
#define AUTO9_BIFROST_VM3_VERTEX45_SHA256 \
    "8809a5ea565a9c81ba1bc6b2b833e097ebe68eed01a71fe76a70cfa8d104627f"
#define AUTO9_BIFROST_VM3_VERTEX592_SIZE 592u
#define AUTO9_BIFROST_VM3_VERTEX592_SHA256 \
    "80f1b50fdf11d1a27288655250be92e87d728d10ac72cf3908ea800837364a14"
#define AUTO9_BIFROST_VM3_VERTEX448_SIZE 448u
#define AUTO9_BIFROST_VM3_VERTEX448_SHA256 \
    "c205e13ce26f7d8de7a4edef7ff8041342b9eecf4f3b459b2b929629dc41d887"

/* Identical viewport tail in the two observed programs. FRCP uses binary32
 * division; SNAP_4 conversion belongs to the descriptor executor. */
static inline bool auto9_bifrost_vm3_screen(const float clip[4], const float u[8],
                                            float out[4])
{
    const uint32_t floor_bits = UINT32_C(0x27004334);
    const uint32_t large_y_bits = UINT32_C(0x46000001);
    const uint32_t bumped_w_bits = UINT32_C(0x3f800001);
    float floor, large_y, bumped_w, abs_sum, denom, inverse, sx, sy, sz, result[4];

    _Static_assert(sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24 &&
                   FLT_MAX_EXP == 128, "binary32 required");
    for (unsigned i = 0; i < 8; i++) {
        if (!isfinite(u[i])) {
            return false;
        }
    }
    memcpy(&floor, &floor_bits, sizeof(floor));
    memcpy(&large_y, &large_y_bits, sizeof(large_y));
    memcpy(&bumped_w, &bumped_w_bits, sizeof(bumped_w));

    for (unsigned i = 0; i < 4; i++) {
        if (!isfinite(clip[i])) {
            return false;
        }
    }
    abs_sum = fmaf(fabsf(clip[2]), floor, -0.0f);
    abs_sum = fmaf(fabsf(clip[1]), floor, abs_sum);
    abs_sum = fmaf(fabsf(clip[0]), floor, abs_sum);
    denom = fmaxf(fmaxf(fabsf(clip[3]), floor), abs_sum);
    inverse = 1.0f / denom;
    if (clip[3] < 0.0f) {
        inverse = -inverse;
    }

    sx = fmaf(clip[0], u[0], fmaf(clip[1], u[3], -0.0f));
    sy = fmaf(clip[1], u[1], fmaf(clip[0], u[7], -0.0f));
    sz = fmaf(fmaf(clip[2], u[2], 0.0f), inverse, -0.0f);
    result[0] = fmaf(sx, inverse, u[4]);
    result[1] = fmaf(sy, inverse, u[5]);
    result[2] = (fabsf(clip[2]) > fabsf(clip[3]) ? sz : fmaxf(-1.0f, fminf(1.0f, sz))) + u[6];
    result[3] = inverse == 1.0f && fabsf(result[1]) > large_y ? bumped_w : inverse;
    for (unsigned i = 0; i < 4; i++) {
        if (!isfinite(result[i])) {
            return false;
        }
    }
    memcpy(out, result, sizeof(result));
    return true;
}

#define AUTO9_BIFROST_VM3_POSITION352_SIZE 352u
#define AUTO9_BIFROST_VM3_POSITION352_SHA256 \
    "477a93f653ab663fa18f4680e81470bb76c99c90f3651e203303803f62ccba31"

#define AUTO9_BIFROST_VM4_POSITION400_SIZE 400u
#define AUTO9_BIFROST_VM4_POSITION400_SHA256 \
    "5215c55f4e7513c86f306e842deb4646e4d4f40d450a7c105f5dc0dd6800ecc0"

static inline bool auto9_bifrost_vm4_position400(const float p[4], const float u[24],
                                                float out[4])
{
    float clip[4];
    for (unsigned i = 0; i < 4; i++) {
        if (!isfinite(p[i])) {
            return false;
        }
    }
    for (unsigned i = 0; i < 24; i++) {
        if (!isfinite(u[i])) {
            return false;
        }
    }
    for (unsigned i = 0; i < 4; i++) {
        clip[i] = fmaf(u[20 + i], p[3], fmaf(u[16 + i], p[2],
                       fmaf(u[8 + i], p[0], fmaf(u[12 + i], p[1], -0.0f))));
    }
    return auto9_bifrost_vm3_screen(clip, u, out);
}

static inline bool auto9_bifrost_vm3_position352(const float p[3], const float u[32],
                                                float out[4])
{
    float clip[4];
    for (unsigned i = 0; i < 3; i++) {
        if (!isfinite(p[i])) {
            return false;
        }
    }
    for (unsigned i = 0; i < 32; i++) {
        if (!isfinite(u[i])) {
            return false;
        }
    }
    clip[0] = fmaf(u[30], p[2], fmaf(u[8], p[0], fmaf(u[12], p[1], -0.0f))) + u[31];
    clip[1] = fmaf(u[24], p[2], fmaf(u[9], p[0], fmaf(u[13], p[1], -0.0f))) + u[25];
    clip[2] = fmaf(u[26], p[2], fmaf(u[10], p[0], fmaf(u[14], p[1], -0.0f))) + u[27];
    clip[3] = fmaf(u[28], p[2], fmaf(u[11], p[0], fmaf(u[15], p[1], -0.0f))) + u[29];
    return auto9_bifrost_vm3_screen(clip, u, out);
}

static inline bool auto9_bifrost_vm3_vertex3_position(const float in[3],
                                                      const float u[28],
                                                      float out[4])
{
    float clip[4];
    clip[0] = fmaf(u[26], in[2], fmaf(u[8], in[0], fmaf(u[12], in[1], -0.0f))) + u[27];
    clip[1] = fmaf(u[19], in[2], fmaf(u[11], in[0], fmaf(u[15], in[1], -0.0f))) + u[23];
    clip[2] = fmaf(u[18], in[2], fmaf(u[10], in[0], fmaf(u[14], in[1], -0.0f))) + u[22];
    clip[3] = fmaf(u[24], in[2], fmaf(u[9], in[0], fmaf(u[13], in[1], -0.0f))) + u[25];
    return auto9_bifrost_vm3_screen(clip, u, out);
}

/* 560-byte original: clauses 0/1/7 project, 11 branches over 14 for
 * raw UVs, 19/21 store UV, and 23/29 apply the shared viewport. The raw
 * branch operand is not a float; only u16.w0.h0 controls this branch. */
static inline bool auto9_bifrost_vm3_vertex4(const float p[3], const float uv[2],
                                             const float u[48], uint32_t uv_control,
                                             float position[4], float uv_out[2])
{
    float clip[4], x = uv[0], y = uv[1];
    clip[0] = fmaf(u[36], p[2], fmaf(u[8], p[0], fmaf(u[12], p[1], -0.0f))) + u[37];
    clip[1] = fmaf(u[40], p[2], fmaf(u[9], p[0], fmaf(u[13], p[1], -0.0f))) + u[41];
    clip[2] = fmaf(u[38], p[2], fmaf(u[10], p[0], fmaf(u[14], p[1], -0.0f))) + u[39];
    clip[3] = fmaf(u[34], p[2], fmaf(u[11], p[0], fmaf(u[15], p[1], -0.0f))) + u[35];
    if ((uint16_t)uv_control != 0) {
        float y_linear = fmaf(u[42], x, fmaf(u[27], y, -0.0f));
        float x_linear = fmaf(u[46], x, fmaf(u[44], y, -0.0f));
        y = (y_linear + u[43]) + u[45];
        x = (x_linear + u[47]) + u[30];
    }
    if (!isfinite(x) || !isfinite(y) || !auto9_bifrost_vm3_screen(clip, u, position)) {
        return false;
    }
    uv_out[0] = x;
    uv_out[1] = y;
    return true;
}

/* 448-byte original: UV is passed straight through by clause 10. The four
 * clip channels below follow clauses 1, 7 and 10 before the shared tail. */
static inline bool auto9_bifrost_vm3_vertex448(const float p[3], const float u[26],
                                               float position[4])
{
    float clip[4];
    clip[0] = fmaf(u[16], p[2], fmaf(u[8], p[0], fmaf(u[12], p[1], -0.0f))) + u[20];
    clip[1] = fmaf(u[17], p[2], fmaf(u[9], p[0], fmaf(u[13], p[1], -0.0f))) + u[21];
    clip[2] = fmaf(u[18], p[2], fmaf(u[10], p[0], fmaf(u[14], p[1], -0.0f))) + u[22];
    clip[3] = fmaf(u[24], p[2], fmaf(u[11], p[0], fmaf(u[15], p[1], -0.0f))) + u[25];
    return auto9_bifrost_vm3_screen(clip, u, position);
}

/* Original 608 B: LD_ATTR v3 expands RG32F z=0. Clause 14 runs
 * only when the low half of u6.w0 is zero. */
static inline bool auto9_bifrost_vm3_vertex45(const float p[3], float uv[2][2],
                                             const float u[58], uint32_t control,
                                             float position[4])
{
    float clip[4];
    clip[0] = fmaf(u[44], p[2], fmaf(u[13], p[0], fmaf(u[17], p[1], -0.0f))) + u[45];
    clip[1] = fmaf(u[48], p[2], fmaf(u[14], p[0], fmaf(u[18], p[1], -0.0f))) + u[49];
    clip[2] = fmaf(u[46], p[2], fmaf(u[15], p[0], fmaf(u[19], p[1], -0.0f))) + u[47];
    clip[3] = fmaf(u[42], p[2], fmaf(u[16], p[0], fmaf(u[20], p[1], -0.0f))) + u[43];
    if (!(uint16_t)control) {
        for (unsigned i = 0; i < 2; i++) {
            float x = uv[i][0], y = uv[i][1];
            uv[i][0] = u[i ? 54 : 56] + fmaf(u[i ? 55 : 57], x, fmaf(u[31], y, -0.0f));
            uv[i][1] = u[i ? 52 : 50] + fmaf(u[i ? 53 : 51], x, fmaf(u[32], y, -0.0f));
        }
    }
    return isfinite(uv[0][0]) && isfinite(uv[0][1]) &&
           isfinite(uv[1][0]) && isfinite(uv[1][1]) &&
           auto9_bifrost_vm3_screen(clip, u, position);
}

/* 592-byte sibling: separate FAU constants; only the first UV transform
 * is conditional. The second output uses r14/r15 on both branches. */
static inline bool auto9_bifrost_vm3_vertex592(const float p[3], float uv[2][2],
                                              const float u[66], uint32_t control,
                                              float position[4])
{
    float clip[4];
    clip[0] = fmaf(u[52], p[2], fmaf(u[13], p[0], fmaf(u[17], p[1], -0.0f))) + u[53];
    clip[1] = fmaf(u[56], p[2], fmaf(u[14], p[0], fmaf(u[18], p[1], -0.0f))) + u[57];
    clip[2] = fmaf(u[54], p[2], fmaf(u[15], p[0], fmaf(u[19], p[1], -0.0f))) + u[55];
    clip[3] = fmaf(u[50], p[2], fmaf(u[16], p[0], fmaf(u[20], p[1], -0.0f))) + u[51];
    if (!(uint16_t)control) {
        float x = uv[0][0], y = uv[0][1];
        uv[0][0] = fmaf(u[61], x, fmaf(u[35], y, -0.0f)) + u[60];
        uv[0][1] = fmaf(u[59], x, fmaf(u[36], y, -0.0f)) + u[58];
    }
    float x = uv[1][0], y = uv[1][1];
    uv[1][0] = fmaf(u[63], x, fmaf(u[31], y, -0.0f)) + u[62];
    uv[1][1] = fmaf(u[65], x, fmaf(u[32], y, -0.0f)) + u[64];
    return isfinite(uv[0][0]) && isfinite(uv[0][1]) &&
           isfinite(uv[1][0]) && isfinite(uv[1][1]) &&
           auto9_bifrost_vm3_screen(clip, u, position);
}

/* IDVS position program: the RG32F attribute supplies z=0. The last
 * two clauses are the same viewport calculation, not the same matrix. */
static inline bool auto9_bifrost_vm3_indexed_position(const float p[2],
                                                      const float u[48],
                                                      float position[4])
{
    float clip[4] = {
        fmaf(u[36], 0.0f, fmaf(u[8], p[0], fmaf(u[12], p[1], -0.0f))) + u[37],
        fmaf(u[40], 0.0f, fmaf(u[9], p[0], fmaf(u[13], p[1], -0.0f))) + u[41],
        fmaf(u[38], 0.0f, fmaf(u[10], p[0], fmaf(u[14], p[1], -0.0f))) + u[39],
        fmaf(u[34], 0.0f, fmaf(u[11], p[0], fmaf(u[15], p[1], -0.0f))) + u[35],
    };
    return auto9_bifrost_vm3_screen(clip, u, position);
}

static inline bool auto9_bifrost_vm3_indexed_uv(float uv[2], const float u[48],
                                                uint32_t control)
{
    if ((uint16_t)control) {
        float x = uv[0], y = uv[1];
        uv[0] = (fmaf(u[46], x, fmaf(u[44], y, -0.0f)) + u[47]) + u[30];
        uv[1] = (fmaf(u[42], x, fmaf(u[27], y, -0.0f)) + u[43]) + u[45];
    }
    return isfinite(uv[0]) && isfinite(uv[1]);
}

#endif
