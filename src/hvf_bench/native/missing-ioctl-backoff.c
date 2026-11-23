/* Optional pacing for the native bench's absent KIPC reader.
 * Always invoke real ioctl first. Only ioctl(-1, 0, ...) returning EBADF is
 * paced, never a valid device or a successful request. Preserve result/errno.
 * No messages, state or hardware readiness are synthesized here.
 */
typedef long time_t;
struct bench_timespec { time_t tv_sec; long tv_nsec; };
extern void *dlsym(void *, const char *);
extern char *getenv(const char *);
extern int *__errno_location(void);
extern int nanosleep(const struct bench_timespec *, struct bench_timespec *);
extern int clock_gettime(int, struct bench_timespec *);
extern int dprintf(int, const char *, ...);
#define EXPORT __attribute__((visibility("default")))
static void *real_ioctl_slot;
static unsigned long paced_calls;
static unsigned long last_report;

static int backoff_ms(void) {
    const char *value = getenv("BENCH_MISSING_IOCTL_BACKOFF_MS");
    if (!value) return 0;
    int result = 0;
    for (int i = 0; value[i]; ++i) {
        if (i > 2 || value[i] < '0' || value[i] > '9') return 0;
        result = result * 10 + value[i] - '0';
    }
    return result >= 1 && result <= 100 ? result : 0;
}

EXPORT int ioctl(int fd, unsigned long request, ...) {
    __builtin_va_list args;
    __builtin_va_start(args, request);
    void *argument = __builtin_va_arg(args, void *);
    __builtin_va_end(args);
    void *slot = __atomic_load_n(&real_ioctl_slot, __ATOMIC_ACQUIRE);
    if (!slot) {
        slot = dlsym((void *)-1L, "ioctl"); /* RTLD_NEXT: exact libc result */
        if (!slot) __builtin_trap();
        __atomic_store_n(&real_ioctl_slot, slot, __ATOMIC_RELEASE);
    }
    int (*real)(int, unsigned long, void *) = slot;
    int result = real(fd, request, argument);
    int saved_errno = *__errno_location();
    if (fd == -1 && request == 0 && result == -1 && saved_errno == 9) {
        int delay = backoff_ms();
        if (delay) {
            unsigned long count = __atomic_add_fetch(&paced_calls, 1, __ATOMIC_RELAXED);
            if (count == 1) dprintf(2, "BENCH_MISSING_IOCTL_BACKOFF active fd=-1 request=0 errno=EBADF ms=%d\n", delay);
            struct bench_timespec time;
            if (clock_gettime(1, &time) == 0) {
                unsigned long previous = __atomic_load_n(&last_report, __ATOMIC_RELAXED);
                if ((unsigned long)time.tv_sec >= previous + 10 &&
                        __atomic_compare_exchange_n(&last_report, &previous, time.tv_sec, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
                    dprintf(2, "BENCH_MISSING_IOCTL_BACKOFF calls=%lu elapsed_clock=%ld\n", count, time.tv_sec);
            }
            struct bench_timespec pause = {0, delay * 1000000L};
            /* Do not retry EINTR: shutdown/signals must not wait for the delay. */
            nanosleep(&pause, (void *)0);
        }
    }
    *__errno_location() = saved_errno;
    return result;
}
