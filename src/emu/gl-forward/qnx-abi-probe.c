extern long write(int, const void *, unsigned long);
extern int getpid(void);
extern unsigned pthread_self(void);
extern int *__get_errno_ptr(void);
extern int pthread_key_create(unsigned *, void (*)(void *));
extern int pthread_setspecific(unsigned, const void *);
extern void *pthread_getspecific(unsigned);
extern int pthread_key_delete(unsigned);
extern int pthread_mutex_init(void *, const void *);
extern int pthread_mutex_lock(void *);
extern int pthread_mutex_unlock(void *);
extern int pthread_mutex_destroy(void *);
static char result[512];
static unsigned used;
static unsigned long mutex[32];
static void put(char c) { if (used < sizeof(result)-1) result[used++] = c; }
static void text(const char *s) { while (*s) put(*s++); }
static void number(unsigned long n) { char b[24]; unsigned i=0; do { b[i++]='0'+n%10;n/=10; } while (n);while(i)put(b[--i]); }
static void item(const char *s, unsigned long n) { text(s);number(n); }
__attribute__((constructor)) static void probe(void)
{
    unsigned key=0;int value=714926;
    used=0;text("MEBQNX_ABI_V1|");item("pid=",(unsigned)getpid());item("|thread=",pthread_self());
    int *error=__get_errno_ptr();item("|errno_ptr=",(unsigned long)error);
    int a=pthread_key_create(&key,0);int b=a?-1:pthread_setspecific(key,&value);
    item("|key_create=",(unsigned)a);item("|set=",(unsigned)b);
    item("|specific=",!a&&!b&&pthread_getspecific(key)==&value);
    item("|key_delete=",a?999:(unsigned)pthread_key_delete(key));
    int c=pthread_mutex_init(mutex,0);item("|mutex_init=",(unsigned)c);
    int d=c?-1:pthread_mutex_lock(mutex);item("|mutex_lock=",(unsigned)d);
    int e=d?-1:pthread_mutex_unlock(mutex);item("|mutex_unlock=",(unsigned)e);
    item("|mutex_destroy=",c?999:(unsigned)pthread_mutex_destroy(mutex));text("|end\n");
    long sent=write(1,result,used);item("MEBQNX_ABI_WRITE|bytes=",(unsigned long)sent);text("|end\n");
    write(1,result,used);
}
