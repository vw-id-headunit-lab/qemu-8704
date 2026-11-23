#pragma once
#include <stdint.h>
#include <stddef.h>
struct android_ycbcr { void *y; void *cb; void *cr; size_t ystride,cstride,chroma_step; uint32_t reserved[8]; };
struct android_flex_layout;
