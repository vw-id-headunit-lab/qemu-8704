struct QnxMap {u32 token,length,flags;void *allocation,*data;struct QnxMap *next;};
static struct QnxMap *qnx_maps;
static struct QnxMap *qnx_map_find(u32 token)
{for(struct QnxMap *m=qnx_maps;m;m=m->next)if(m->token==token)return m;return 0;}
static void qnx_map_retire(u32 token)
{
 pthread_mutex_lock(record_mutex);struct QnxMap **link=&qnx_maps;
 while(*link){struct QnxMap *m=*link;if(m->token==token){*link=m->next;free(m->allocation);free(m);break;}link=&m->next;}
 pthread_mutex_unlock(record_mutex);
}
static struct QnxMap *qnx_current_map(u32 target,u32 pname,u32 header[4])
{
 u32 p[2]={target,pname};emit_iv(154,(i32 *)p,2);recv_all(header,16);
 if(!header[0])return 0;
 pthread_mutex_lock(record_mutex);struct QnxMap *m=qnx_map_find(header[0]);pthread_mutex_unlock(record_mutex);
 if(!m||m->length!=header[1]||m->flags!=header[2])transport_failed();return m;
}
void *glMapBufferRange(u32 target,int64_t offset,int64_t length,u32 flags)
{
 u32 p[2]={target,flags},header[4];int64_t range[2]={offset,length};rec(153,24);PART(p,8);PART(range,16);recv_bytes(header,16,0);
 if(!header[0]){if(header[1])transport_failed();unlock_record();return 0;}
 if(length<0||header[1]!=(uint64_t)length||header[1]>16*1024*1024)transport_failed();
 struct QnxMap *m=calloc(1,sizeof(*m));if(!m)transport_failed();m->allocation=malloc((size_t)header[1]+127);if(!m->allocation)transport_failed();
 m->data=(void *)((((uintptr_t)m->allocation+63)&~(uintptr_t)63)+((uint64_t)offset&63));m->token=header[0];m->length=header[1];m->flags=header[2];recv_bytes(m->data,m->length,1);
 if(header[3])qnx_map_retire(header[3]);pthread_mutex_lock(record_mutex);m->next=qnx_maps;qnx_maps=m;pthread_mutex_unlock(record_mutex);return m->data;
}
u32 glUnmapBuffer(u32 target)
{
 u32 header[4],result=0;struct QnxMap *m=qnx_current_map(target,0,header);u32 bytes=m&&(m->flags&2)&&!(m->flags&16)?m->length:0;
 u32 p[4]={target,m?m->token:0,bytes,0};rec(156,16+bytes);PART(p,16);if(bytes)PART(m->data,bytes);recv_all(&result,4);if(m)qnx_map_retire(m->token);return result;
}
void glFlushMappedBufferRange(u32 target,int64_t offset,int64_t length)
{
 u32 header[4];struct QnxMap *m=qnx_current_map(target,0,header);u32 bytes=m&&(m->flags&2)&&(m->flags&16)&&offset>=0&&length>=0&&(uint64_t)offset<=m->length&&(uint64_t)length<=m->length-(uint64_t)offset?(u32)length:0;
 u32 p[2]={target,m?m->token:0},tail[2]={bytes,0},result;int64_t range[2]={offset,length};rec(155,32+bytes);PART(p,8);PART(range,16);PART(tail,8);if(bytes)PART((unsigned char *)m->data+offset,bytes);recv_all(&result,4);if(result!=1)transport_failed();
}
void glGetBufferPointerv(u32 target,u32 pname,void **result)
{u32 header[4];struct QnxMap *m=qnx_current_map(target,pname,header);if(result)*result=m?m->data:0;}
void glGetBufferParameteriv(u32 target,u32 pname,i32 *value)
{u32 p[3]={1,target,pname};i32 result;emit_iv(157,(i32 *)p,3);recv_all(&result,4);if(value)*value=result;}
void glGetBufferParameteri64v(u32 target,u32 pname,int64_t *value)
{u32 p[3]={2,target,pname};int64_t result;emit_iv(157,(i32 *)p,3);recv_all(&result,8);if(value)*value=result;}
