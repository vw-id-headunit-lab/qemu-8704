long arm32_entry(unsigned long *initial)
{
    unsigned long count = initial[0];
    char **input = (char **)(initial + 1);
    char **environment = input + count + 1;
    if (count < 3) return 126;
    char *output[count + 10];
    output[0] = "/bench/qemu-libs/ld-musl-aarch64.so.1";
    output[1] = "--library-path";
    output[2] = "/bench/qemu-libs";
    output[3] = "/bench/qemu-arm";
    output[4] = "-B";
    output[5] = "0";
    output[6] = "-R";
    output[7] = "0";
    output[8] = "-0";
    output[9] = input[2];
    output[10] = input[1];
    unsigned long at = 11;
    for (unsigned long i = 3; i < count; ++i) output[at++] = input[i];
    output[at] = 0;
    register long x0 __asm__("x0") = (long)output[0];
    register long x1 __asm__("x1") = (long)output;
    register long x2 __asm__("x2") = (long)environment;
    register long x8 __asm__("x8") = 221;
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x1), "r"(x2), "r"(x8) : "memory");
    return 126;
}
__asm__(".global _start\n_start:\nmov x0,sp\nbl arm32_entry\nmov x8,93\nsvc 0\n");
