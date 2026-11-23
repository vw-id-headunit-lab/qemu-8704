#ifndef OEM_MAPPED_COPY_H
#define OEM_MAPPED_COPY_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>
static void mapped_write(volatile unsigned char *device,const void *normal,size_t n)
{
    const unsigned char *p=normal;
    while(n&&((uintptr_t)device&7)){*device++=*p++;n--;}
    while(n>=8){uint64_t v;memcpy(&v,p,8);*(volatile uint64_t *)device=v;device+=8;p+=8;n-=8;}
    while(n){*device++=*p++;n--;}
}
static void mapped_read(void *normal,const volatile unsigned char *device,size_t n)
{
    unsigned char *p=normal;
    while(n&&((uintptr_t)device&7)){*p++=*device++;n--;}
    while(n>=8){uint64_t v=*(const volatile uint64_t *)device;memcpy(p,&v,8);device+=8;p+=8;n-=8;}
    while(n){*p++=*device++;n--;}
}
#endif
