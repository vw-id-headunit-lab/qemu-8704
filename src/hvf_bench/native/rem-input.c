typedef unsigned long size_t;
extern void *dlsym(void *, const char *);
extern int pthread_create(unsigned long *, const void *, void *(*)(void *), void *);
extern int open(const char *, int, ...);
extern void *fdopen(int, const char *);
extern char *fgets(char *, int, void *);
extern int sscanf(const char *, const char *, ...);
extern int strcmp(const char *, const char *);
extern int usleep(unsigned int);
extern int dprintf(int, const char *, ...);
typedef void (*push_fn)(void *, const char *, unsigned long, unsigned int, const char *);
static void send_message(push_fn push, void *instance, const unsigned char *body, unsigned int length) {
    unsigned char message[64] = {0x83,0x50,0x20,0x0b,0,0,0,0,0};
    for (unsigned int i=0;i<length;i++) message[9+i]=body[i];
    push(instance,"RemMgr",57,9+length,(const char *)message);
}
static void *input_loop(void *unused) {
    (void)unused;
    void **instance=dlsym((void *)0,"_ZN7KipcCom8instanceE");
    push_fn push=(push_fn)dlsym((void *)0,"_ZN5frame5Frame4pushEPKcmjS2_");
    if (!instance || !push) {dprintf(2,"REM_INPUT_SYMBOLS_MISSING\n");return (void *)0;}
    while (!*instance) usleep(100000);
    int fd=open("/tmp/bench-update/pointer",2);
    if(fd<0)return (void *)0;
    void *stream=fdopen(fd,"r");
    if(!stream)return (void *)0;
    char line[128],action[16],extra;
    int x,y;
    while(fgets(line,sizeof(line),stream)) {
        if(sscanf(line,"%15s %d %d %c",action,&x,&y,&extra)!=3 || x<0 || x>=1920 || y<0 || y>=1024)continue;
        int pressure;
        if(!strcmp(action,"down"))pressure=253;
        else if(!strcmp(action,"up"))pressure=255;
        else if(!strcmp(action,"move"))pressure=254;
        else continue;
        unsigned int px=(unsigned int)x/2,py=(unsigned int)y;
        unsigned char body[]={0xea,0,0x10,(unsigned char)(px>>6),(unsigned char)((px<<2)|(py>>8)),(unsigned char)py,(unsigned char)pressure};
        send_message(push,*instance,body,sizeof(body));
        dprintf(2,"REM_INPUT action=%s x=%d y=%d raw=%u,%u pressure=%d\n",action,x,y,px,py,pressure);
    }
    return (void *)0;
}
__attribute__((constructor)) static void init_input(void) {
    unsigned long thread;
    if(pthread_create(&thread,(void *)0,input_loop,(void *)0))dprintf(2,"REM_INPUT_THREAD_FAILED\n");
}
