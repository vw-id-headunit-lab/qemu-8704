/* Loader bridge only. EGL, GLES and Wayland window functions come from Mesa
 * and libwayland-egl through DT_NEEDED; no graphics function is implemented. */
__attribute__((visibility("hidden")))
const char agl_mesa_compat_identity[] = "AGL Mesa Buster ARM64 SONAME bridge";
