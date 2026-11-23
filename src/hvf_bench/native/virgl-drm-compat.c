/* The firmware Weston backend forces ARGB8888 scanout. Virtio's primary
 * plane requires opaque XRGB8888. Both formats have the same memory layout;
 * only the unused scanout alpha channel changes. Rendering stays in Mesa.
 */
extern void *dlsym(void *, const char *);

__attribute__((visibility("default")))
void *gbm_surface_create(void *device, unsigned width, unsigned height,
                         unsigned format, unsigned flags) {
    typedef void *(*Create)(void *, unsigned, unsigned, unsigned, unsigned);
    static Create real;
    if (!real) real = (Create)dlsym((void *)-1L, "gbm_surface_create");
    if (!real) return (void *)0;
    if (format == 0x34325241u) format = 0x34325258u;
    return real(device, width, height, format, flags);
}

__attribute__((visibility("default")))
int drmModeAddFB2(int fd, unsigned width, unsigned height, unsigned format,
                 const unsigned *handles, const unsigned *pitches,
                 const unsigned *offsets, unsigned *id, unsigned flags) {
    typedef int (*AddFB2)(int, unsigned, unsigned, unsigned, const unsigned *,
                         const unsigned *, const unsigned *, unsigned *, unsigned);
    static AddFB2 real;
    if (!real) real = (AddFB2)dlsym((void *)-1L, "drmModeAddFB2");
    if (!real) return -38; /* ENOSYS, matching libdrm's negative errno ABI. */
    if (format == 0x34325241u) format = 0x34325258u;
    return real(fd, width, height, format, handles, pitches, offsets, id, flags);
}
