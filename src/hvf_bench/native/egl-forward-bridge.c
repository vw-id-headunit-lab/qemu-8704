/* Transparent compatibility forwarding, with opt-in diagnostics: EGL calls forward through the real GLVND
 * libEGL handle. Explicit handles work with the JVM's RTLD_LOCAL JNI scope.
 * No frame, EGL result, configuration, or application state is fabricated. */
typedef void *EGLDisplay;
typedef void *EGLSurface;
typedef void *EGLContext;
typedef unsigned int EGLBoolean;
typedef int EGLint;
extern void *dlsym(void *, const char *);
extern void *dlopen(const char *, int);
extern char *dlerror(void);
extern int dprintf(int, const char *, ...);
extern int getpid(void);
extern long syscall(long, ...);
extern int *__errno_location(void);
extern char *getenv(const char *);
#define EXPORT __attribute__((visibility("default")))
static void *egl_library, *gles_library;

static long thread_id(void) { return syscall(178L); }
static int enabled(const char *key) { const char *v=getenv(key); return v && v[0]=='1' && v[1]=='\0'; }
#define TRACE(...) do { if (enabled("AGL_EGL_TRACE")) dprintf(2, __VA_ARGS__); } while (0)

static void *resolve(const char *name, void **slot, int gl_string) {
    void *value = __atomic_load_n(slot, __ATOMIC_ACQUIRE);
    if (!value) {
        void **handle_slot = gl_string ? &gles_library : &egl_library;
        const char *library_name = gl_string ? "libGLESv2.so.2" : "libEGL.so.1";
        void *handle = __atomic_load_n(handle_slot, __ATOMIC_ACQUIRE);
        if (!handle) {
            handle = dlopen(library_name, 1); /* RTLD_LAZY | RTLD_LOCAL */
            TRACE("EGLTRACE pid=%d tid=%ld LIBRARY name=%s handle=%p\n", getpid(), thread_id(), library_name, handle);
            if (!handle) {
                dprintf(2, "EGLTRACE REAL_LIBRARY_LOAD_ERROR name=%s error=%s\n", library_name, dlerror());
                __builtin_trap();
            }
            __atomic_store_n(handle_slot, handle, __ATOMIC_RELEASE);
        }
        value = dlsym(handle, name);
        TRACE("EGLTRACE pid=%d tid=%ld %s RESOLVED library=%s target=%p\n", getpid(), thread_id(), name, library_name, value);
        if (!value) {
            dprintf(2, "EGLTRACE REAL_SYMBOL_LOOKUP_ERROR name=%s error=%s\n", name, dlerror());
            __builtin_trap();
        }
        __atomic_store_n(slot, value, __ATOMIC_RELEASE);
    }
    return value;
}

EXPORT EGLBoolean eglSwapBuffers(EGLDisplay display, EGLSurface surface) {
    static void *slot;
    int before = *__errno_location();
    TRACE("EGLTRACE pid=%d tid=%ld eglSwapBuffers ENTER display=%p surface=%p\n", getpid(), thread_id(), display, surface);
    EGLBoolean (*real)(EGLDisplay, EGLSurface) = resolve("eglSwapBuffers", &slot, 0);
    *__errno_location() = before;
    EGLBoolean result = real(display, surface);
    int after = *__errno_location();
    TRACE("EGLTRACE pid=%d tid=%ld eglSwapBuffers EXIT result=%u\n", getpid(), thread_id(), result);
    *__errno_location() = after;
    return result;
}

EXPORT EGLBoolean eglSwapInterval(EGLDisplay display, EGLint interval) {
    static void *slot;
    int before = *__errno_location();
    TRACE("EGLTRACE pid=%d tid=%ld eglSwapInterval ENTER display=%p interval=%d\n", getpid(), thread_id(), display, interval);
    EGLBoolean (*real)(EGLDisplay, EGLint) = resolve("eglSwapInterval", &slot, 0);
    *__errno_location() = before;
    EGLBoolean result = real(display, interval);
    int after = *__errno_location();
    TRACE("EGLTRACE pid=%d tid=%ld eglSwapInterval EXIT result=%u\n", getpid(), thread_id(), result);
    *__errno_location() = after;
    return result;
}

EXPORT EGLBoolean eglMakeCurrent(EGLDisplay display, EGLSurface draw, EGLSurface read, EGLContext context) {
    static void *slot, *strings;
    int before = *__errno_location();
    TRACE("EGLTRACE pid=%d tid=%ld eglMakeCurrent ENTER display=%p draw=%p read=%p context=%p\n", getpid(), thread_id(), display, draw, read, context);
    EGLBoolean (*real)(EGLDisplay, EGLSurface, EGLSurface, EGLContext) = resolve("eglMakeCurrent", &slot, 0);
    *__errno_location() = before;
    EGLBoolean result = real(display, draw, read, context);
    int after = *__errno_location();
    TRACE("EGLTRACE pid=%d tid=%ld eglMakeCurrent EXIT result=%u\n", getpid(), thread_id(), result);
    if (result && context && enabled("AGL_EGL_QUERY_GL")) {
        const unsigned char *(*get_string)(unsigned int) = resolve("glGetString", &strings, 1);
        const unsigned char *vendor = get_string(0x1F00u);
        const unsigned char *renderer = get_string(0x1F01u);
        const unsigned char *version = get_string(0x1F02u);
        TRACE("EGLTRACE pid=%d tid=%ld GL_VENDOR=%s GL_RENDERER=%s GL_VERSION=%s\n", getpid(), thread_id(), vendor ? (const char *)vendor : "(null)", renderer ? (const char *)renderer : "(null)", version ? (const char *)version : "(null)");
    }
    *__errno_location() = after;
    return result;
}
