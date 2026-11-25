struct ME_Readback {uint32_t pid,tid,width,height,sequence,pending;GLuint buffer;uint64_t offset;EGLContext context;};
static struct ME_Readback me_readbacks[256];
static void me_readback_request(uint32_t pid,uint32_t tid,GLuint buffer,uint64_t offset,unsigned width,unsigned height)
{
 struct ME_Thread *t=me_thread(pid,tid,0);struct ME_Readback *r=&me_readbacks[tid%256];uint32_t sequence=r->pid==pid&&r->tid==tid?r->sequence:0;
 GLint64 capacity=0;glGetBufferParameteri64v(GL_PIXEL_PACK_BUFFER,GL_BUFFER_SIZE,&capacity);
 uint64_t bytes=(uint64_t)width*height*4;unsigned pending=capacity>=0&&offset<=(uint64_t)capacity&&bytes<=(uint64_t)capacity-offset;
 *r=(struct ME_Readback){pid,tid,width,height,sequence,pending,buffer,offset,t?t->context:EGL_NO_CONTEXT};
}
static void me_readback_mapped(uint32_t pid,uint32_t tid,GLuint buffer,int64_t offset,int64_t length,const void *address)
{
 struct ME_Readback *r=&me_readbacks[tid%256];struct ME_Thread *t=me_thread(pid,tid,0);
 if(!meb_qnx_strict||!t||!r->pending||r->pid!=pid||r->tid!=tid||r->buffer!=buffer||r->context!=t->context||offset<0||r->offset<(uint64_t)offset)return;
 uint64_t begin=r->offset-(uint64_t)offset,bytes=(uint64_t)r->width*r->height*4;
 if(length<0||begin>(uint64_t)length||bytes>(uint64_t)length-begin||!bytes)return;
 r->pending=0;const unsigned char *pixels=(const unsigned char *)address+begin;unsigned nonzero=0,variation=0;
 for(uint64_t i=0;i<bytes;i+=4){if(pixels[i]||pixels[i+1]||pixels[i+2])nonzero++;if(memcmp(pixels,pixels+i,3))variation=1;}
 char path[4096],temporary[4096],meta[4096],metatmp[4096];
 int n=snprintf(path,sizeof(path),"%s.readback.%u.%u.%ux%u.ppm",frame_path(),pid,tid,r->width,r->height);
 if(n<0||n>=(int)sizeof(path)||snprintf(temporary,sizeof(temporary),"%s.tmp",path)>=(int)sizeof(temporary))return;
 unsigned char *rgb=malloc((size_t)r->width*r->height*3);if(!rgb)return;
 for(unsigned y=0;y<r->height;y++)for(unsigned x=0;x<r->width;x++)memcpy(rgb+((size_t)y*r->width+x)*3,pixels+((size_t)(r->height-1-y)*r->width+x)*4,3);
 FILE *file=fopen(temporary,"wb");if(!file){free(rgb);return;}int okay=fprintf(file,"P6\n%u %u\n255\n",r->width,r->height)>0&&fwrite(rgb,1,(size_t)r->width*r->height*3,file)==(size_t)r->width*r->height*3;free(rgb);
 if(fclose(file)||!okay){unlink(temporary);return;}if(rename(temporary,path))return;
 if(snprintf(meta,sizeof(meta),"%s.json",path)>=(int)sizeof(meta)||snprintf(metatmp,sizeof(metatmp),"%s.tmp",meta)>=(int)sizeof(metatmp))return;
 struct timespec now;clock_gettime(CLOCK_MONOTONIC,&now);uint64_t context=0;
 for(unsigned i=0;i<me_handle_count;i++)if(me_handles[i].owner==pid&&me_handles[i].kind==ME_CONTEXT&&me_handles[i].native==r->context){context=i+1;break;}
 file=fopen(metatmp,"w");if(!file)return;
 fprintf(file,"{\"source\":\"%s\",\"pid\":%u,\"tid\":%u,\"width\":%u,\"height\":%u,\"context_id\":%llu,\"pixel_pack_buffer\":%u,\"offset\":%llu,\"sequence\":%u,\"nonzero_rgb_pixels\":%u,\"color_variation\":%u,\"monotonic\":%.9f}\n",meb_qnx_fixture?"qnx_gl_fixture_test":"original_qnx_gl_pbo_readback",pid,tid,r->width,r->height,(unsigned long long)context,buffer,(unsigned long long)r->offset,++r->sequence,nonzero,variation,now.tv_sec+now.tv_nsec/1e9);
 if(fclose(file)||rename(metatmp,meta))return;
}
