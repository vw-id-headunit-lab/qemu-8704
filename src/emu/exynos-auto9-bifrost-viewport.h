/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef EXYNOS_AUTO9_BIFROST_VIEWPORT_H
#define EXYNOS_AUTO9_BIFROST_VIEWPORT_H

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define AUTO9_BIFROST_VIEWPORT_PROGRAM_SIZE 240u
#define AUTO9_BIFROST_VIEWPORT_PROGRAM_SHA256 \
    "f127f2b4bac7fc2485595553eb7429dff1e3dc5c32d2a47b6aaf8b0271d4516c"
#define AUTO9_BIFROST_VM3_VIEWPORT_SIZE 336u
#define AUTO9_BIFROST_VM3_VIEWPORT_SHA256 \
    "233e85d1630e303eed9f723c671bd4e02cd0cf12c691e815535abf4b85a2edc8"

/* Executes the floating-point part of the 8704 Bifrost vertex program.
 * The caller must match the exact program and resolve attribute, uniform,
 * and varying descriptors first. No guest memory is accessed here.
 * Uniform words are in FAU order: u0.w0, u0.w1, u1.w0, u1.w1, and so on.
 * FRCP uses IEEE binary32 division, not Mali's hardware approximation.
 */
static inline bool auto9_bifrost_viewport_alu(const float position[4],
                                             const float uniform[8],
                                             float varying[4])
{
    const uint32_t floor_bits = UINT32_C(0x32e635d3);
    float floor, abs_sum, denominator, inverse, x, y, z, clipped, result[4];

    _Static_assert(sizeof(float) == sizeof(floor_bits) && FLT_RADIX == 2 &&
                   FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128,
                   "binary32 required");
    for (unsigned i = 0; i < 4; i++) {
        if (!isfinite(position[i])) {
            return false;
        }
    }
    for (unsigned i = 0; i < 8; i++) {
        if (!isfinite(uniform[i])) {
            return false;
        }
    }

    memcpy(&floor, &floor_bits, sizeof(floor));
    abs_sum = fmaf(fabsf(position[2]), floor, -0.0f);
    abs_sum = fmaf(fabsf(position[1]), floor, abs_sum);
    abs_sum = fmaf(fabsf(position[0]), floor, abs_sum);
    denominator = fmaxf(fmaxf(fabsf(position[3]), floor), abs_sum);
    inverse = 1.0f / denominator;
    if (0.0f > position[3]) {
        inverse = -inverse;
    }

    x = fmaf(position[0], uniform[0], fmaf(position[1], uniform[3], -0.0f));
    y = fmaf(position[1], uniform[1], fmaf(position[0], uniform[7], -0.0f));
    z = fmaf(fmaf(position[2], uniform[2], 0.0f), inverse, -0.0f);
    clipped = fmaxf(-1.0f, fminf(1.0f, z));
    if (!isfinite(x) || !isfinite(y) || !isfinite(z)) {
        return false;
    }

    result[0] = fmaf(x, inverse, uniform[4]);
    result[1] = fmaf(y, inverse, uniform[5]);
    /* MUX.int_zero selects its first input when FCMP returns zero. */
    result[2] = (fabsf(position[2]) > fabsf(position[3]) ? z : clipped)
                + uniform[6];
    result[3] = inverse;
    for (unsigned i = 0; i < 4; i++) {
        if (!isfinite(result[i])) {
            return false;
        }
    }
    memcpy(varying, result, sizeof(result));
    return true;
}

/* Binary32-to-binary16 with explicit nearest-even rounding, independent of
 * host FP environment. Callers still decide whether their shader flushes
 * denormals or suppresses NaNs/infinities before this store. */
static inline uint16_t auto9_bifrost_f32_half_rne(float f)
{
    uint32_t bits;
    memcpy(&bits, &f, sizeof(bits));
    uint16_t sign = (bits >> 16) & 0x8000;
    unsigned exp = (bits >> 23) & 255;
    uint32_t mant = bits & 0x7fffff;

    if (exp == 255) {
        return sign | 0x7c00 | (mant ? 0x200 | (mant >> 13) : 0);
    }
    if (exp > 142) {
        return sign | 0x7c00;
    }
    if (exp < 102) {
        return sign;
    }
    if (exp < 113) {
        unsigned shift = 126 - exp;
        uint32_t halfway = UINT32_C(1) << (shift - 1);
        uint32_t sig = mant | 0x800000;
        return sign | ((sig + halfway - 1 + ((sig >> shift) & 1)) >> shift);
    }
    unsigned half = ((exp - 112) << 10) | (mant >> 13);
    unsigned remainder = mant & 0x1fff;
    half += remainder > 0x1000 || (remainder == 0x1000 && (half & 1));
    return sign | half;
}

static inline uint16_t auto9_bifrost_unorm8_half(uint8_t v)
{
    return auto9_bifrost_f32_half_rne((float)v / 255.0f);
}

static inline bool auto9_bifrost_snap4(float result[4])
{
    for (unsigned i = 0; i < 2; i++) {
        float scaled = result[i] * 256.0f;
        if (!isfinite(scaled)) {
            return false;
        }
        float lower = floorf(scaled);
        float fraction = scaled - lower;
        if (fraction > 0.5f || (fraction == 0.5f && fmodf(lower, 2.0f) != 0.0f)) {
            lower += 1.0f;
        }
        result[i] = lower / 256.0f;
    }
    return true;
}

/* The six observed clauses of the exact VM3 program, not a general shader
 * interpreter. u[n*2 + word] is the live FAU word; no values are captured
 * or baked in. Restrict this lowering to finite arithmetic. The reciprocal
 * uses host IEEE binary32 division, not the hardware's estimate. */
static inline bool auto9_bifrost_vm3_viewport(const float input[4], const float u[28],
                                             uint16_t uv[2], float snap[4])
{
    static const unsigned used[] = {0,1,2,3,4,5,6,7,12,13,14,15,20,21,22,23,24,25,26,27};
    for (unsigned i = 0; i < 4; i++) {
        if (!isfinite(input[i])) {
            return false;
        }
    }
    if (fabsf(input[2]) > 65504.0f || fabsf(input[3]) > 65504.0f) {
        return false;
    }
    for (unsigned i = 0; i < sizeof(used) / sizeof(used[0]); i++) {
        if (!isfinite(u[used[i]])) {
            return false;
        }
    }
    const uint32_t floor_bits = UINT32_C(0x27004334);
    const uint32_t threshold_bits = UINT32_C(0x46000001);
    const uint32_t guard_bits = UINT32_C(0x3f800001);
    float floor, threshold, guard;
    memcpy(&floor, &floor_bits, 4);
    memcpy(&threshold, &threshold_bits, 4);
    memcpy(&guard, &guard_bits, 4);

    /* Clause 1: preserve the separate add after each FMA, and the
     * compiler's nonsequential FAU ordering for the Y products. */
    float yx = fmaf(u[12], input[1], -0.0f);
    float yy = fmaf(u[13], input[1], -0.0f);
    float yz = fmaf(u[14], input[1], -0.0f);
    float yw = fmaf(u[15], input[1], -0.0f);
    float cx = fmaf(u[26], input[0], yx) + u[27];
    float cy = fmaf(u[20], input[0], yy) + u[21];
    float cz = fmaf(u[22], input[0], yz) + u[23];
    float cw = fmaf(u[24], input[0], yw) + u[25];
    if (!isfinite(cx) || !isfinite(cy) || !isfinite(cz) || !isfinite(cw)) {
        return false;
    }
    /* Clauses 7/8 store the other RG32F input as RG16F. Clauses 9/15
     * compute the signed guarded reciprocal, viewport, and SNAP_4. */
    float sum = fmaf(fabsf(cz), floor, -0.0f);
    sum = fmaf(fabsf(cy), floor, sum);
    sum = fmaf(fabsf(cx), floor, sum);
    float inv = 1.0f / fmaxf(fmaxf(fabsf(cw), floor), sum);
    if (0.0f > cw) {
        inv = -inv;
    }
    float px = fmaf(cx, u[0], fmaf(cy, u[3], -0.0f));
    float py = fmaf(cy, u[1], fmaf(cx, u[7], -0.0f));
    float z = fmaf(fmaf(cz, u[2], 0.0f), inv, -0.0f);
    float out[4] = { fmaf(px, inv, u[4]), fmaf(py, inv, u[5]),
                     (fabsf(cz) > fabsf(cw) ? z : fmaxf(-1.0f, fminf(1.0f, z))) + u[6],
                     inv };
    if (fabsf(out[1]) > threshold && inv == 1.0f) {
        out[3] = guard;
    }
    if (!isfinite(sum) || !isfinite(z) || !isfinite(out[2]) || !isfinite(out[3]) ||
        !auto9_bifrost_snap4(out)) {
        return false;
    }
    uv[0] = auto9_bifrost_f32_half_rne(input[2]);
    uv[1] = auto9_bifrost_f32_half_rne(input[3]);
    memcpy(snap, out, sizeof(out));
    return true;
}

/* SNAP_4 encodes screen XY on a subpixel grid. Depth and reciprocal W
 * retain binary32 precision. Tie-to-even is the model's rounding rule. */
static inline bool auto9_bifrost_viewport_vertex(const float position[4],
                                                const uint8_t color[4],
                                                const float uniform[8],
                                                uint16_t rgba[4], float snap[4])
{
    float result[4];

    if (!auto9_bifrost_viewport_alu(position, uniform, result)) {
        return false;
    }
    if (!auto9_bifrost_snap4(result)) {
        return false;
    }
    for (unsigned i = 0; i < 4; i++) {
        rgba[i] = auto9_bifrost_unorm8_half(color[i]);
    }
    memcpy(snap, result, sizeof(result));
    return true;
}

#endif
