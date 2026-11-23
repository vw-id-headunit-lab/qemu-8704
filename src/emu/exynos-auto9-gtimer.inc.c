/* Exynos Auto V9 AP system counter: 26 MHz = 13 ticks per 500 ns. */
static uint64_t auto9_gt_ns_to_ticks(uint64_t ns)
{
    /* Split before multiplying so full-width inputs cannot overflow. */
    return ns / 500 * 13 + ns % 500 * 13 / 500;
}

int64_t auto9_gt_ticks_to_ns(uint64_t ticks)
{
    uint64_t whole = ticks / 13;
    uint64_t fraction = (ticks % 13 * 500 + 12) / 13;

    /* Ceiling is required: a deadline must never fire before CVAL. */
    if (whole > ((uint64_t)INT64_MAX - fraction) / 500) {
        return INT64_MAX;
    }
    return whole * 500 + fraction;
}
