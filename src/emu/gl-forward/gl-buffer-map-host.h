struct ME_BufferMap {uint32_t token,group,flags,length;GLuint buffer;void *address;struct ME_BufferMap *next;};
static uint32_t me_context_group(uint32_t pid,uint32_t tid)
{
 struct ME_Thread *t=me_thread(pid,tid,0);if(!t)return 0;
 for(unsigned i=0;i<me_handle_count;i++)if(me_handles[i].owner==pid&&me_handles[i].kind==ME_CONTEXT&&me_handles[i].native==t->context)return me_handles[i].share_group;
 return 0;
}
static GLenum me_buffer_binding_pname(GLenum target)
{
 switch(target){case GL_ARRAY_BUFFER:return GL_ARRAY_BUFFER_BINDING;case GL_ELEMENT_ARRAY_BUFFER:return GL_ELEMENT_ARRAY_BUFFER_BINDING;
 case GL_COPY_READ_BUFFER:return GL_COPY_READ_BUFFER_BINDING;case GL_COPY_WRITE_BUFFER:return GL_COPY_WRITE_BUFFER_BINDING;
 case GL_PIXEL_PACK_BUFFER:return GL_PIXEL_PACK_BUFFER_BINDING;case GL_PIXEL_UNPACK_BUFFER:return GL_PIXEL_UNPACK_BUFFER_BINDING;
 case GL_TRANSFORM_FEEDBACK_BUFFER:return GL_TRANSFORM_FEEDBACK_BUFFER_BINDING;case GL_UNIFORM_BUFFER:return GL_UNIFORM_BUFFER_BINDING;default:return 0;}
}
static struct ME_BufferMap **me_map_slot(struct ME_Owner *o,GLuint buffer,uint32_t group)
{struct ME_BufferMap **slot=&o->maps;while(*slot&&((*slot)->buffer!=buffer||(*slot)->group!=group))slot=&(*slot)->next;return slot;}
static struct ME_BufferMap *me_bound_map(struct ME_Owner *o,uint32_t group,GLenum target)
{
 GLenum pname=me_buffer_binding_pname(target);if(!pname)return NULL;GLint buffer=0;glGetIntegerv(pname,&buffer);if(!buffer)return NULL;
 struct ME_BufferMap **slot=me_map_slot(o,(GLuint)buffer,group);if(!*slot)return NULL;
 GLint mapped=0;glGetBufferParameteriv(target,GL_BUFFER_MAPPED,&mapped);
 if(!mapped){struct ME_BufferMap *old=*slot;*slot=old->next;free(old);return NULL;}return *slot;
}
static int64_t me_map_i64(const uint8_t *p,unsigned offset)
{int64_t n;memcpy(&n,p+offset,8);return n;}
static int me_buffer_record_valid(uint32_t op,uint32_t len,const uint8_t *p)
{
 if(op==153)return len==24&&me_map_i64(p,16)<=16*1024*1024;
 if(op==154)return len==8;
 if(op==155)return len>=32&&u32(p,24)==len-32&&u32(p,24)<=16*1024*1024;
 if(op==156)return len>=16&&u32(p,8)==len-16&&u32(p,8)<=16*1024*1024;
 return op==157&&len==12&&u32(p,0)>=1&&u32(p,0)<=2;
}
static int me_buffer_dispatch(uint32_t pid,uint32_t tid,uint32_t op,const uint8_t *p)
{
 struct ME_Owner *o=me_owner(pid,0);uint32_t group=me_context_group(pid,tid);if(!o||!group)return 0;
 if(op==153){
  GLenum target=u32(p,0);GLbitfield flags=u32(p,4);int64_t offset=me_map_i64(p,8),length=me_map_i64(p,16);void *address=glMapBufferRange(target,(GLintptr)offset,(GLsizeiptr)length,flags);uint32_t header[4]={0};
  if(address){
   GLenum pname=me_buffer_binding_pname(target);if(!pname||length<=0||length>16*1024*1024)return 0;GLint buffer=0;glGetIntegerv(pname,&buffer);if(!buffer)return 0;
   struct ME_BufferMap **slot=me_map_slot(o,(GLuint)buffer,group);if(*slot){struct ME_BufferMap *old=*slot;header[3]=old->token;*slot=old->next;free(old);}
   struct ME_BufferMap *m=calloc(1,sizeof(*m));if(!m||o->map_counter==UINT32_MAX)return 0;
   m->token=++o->map_counter;m->group=group;m->flags=flags;m->length=(uint32_t)length;m->buffer=(GLuint)buffer;m->address=address;m->next=o->maps;o->maps=m;
   header[0]=m->token;header[1]=m->length;header[2]=m->flags;
   if(target==GL_PIXEL_PACK_BUFFER&&(flags&GL_MAP_READ_BIT))me_readback_mapped(pid,tid,(GLuint)buffer,offset,length,address);
  }
  return me_write_all(header,16)&&(!header[0]||me_write_all(address,header[1]));
 }
 if(op==157){
  if(u32(p,0)==1){GLint value=0;glGetBufferParameteriv(u32(p,4),u32(p,8),&value);return me_write_all(&value,4);}
  GLint64 value=0;glGetBufferParameteri64v(u32(p,4),u32(p,8),&value);return me_write_all(&value,8);
 }
 GLenum target=u32(p,0);struct ME_BufferMap *m=me_bound_map(o,group,target);
 if(op==154){
  uint32_t header[4]={0};void *address=m?m->address:NULL;
  if(u32(p,4)){address=NULL;glGetBufferPointerv(target,u32(p,4),&address);}
  if(m&&address==m->address){header[0]=m->token;header[1]=m->length;header[2]=m->flags;}
  return me_write_all(header,16);
 }
 if((m?m->token:0)!=u32(p,4))return 0;
 if(op==155){
  int64_t offset=me_map_i64(p,8),length=me_map_i64(p,16);uint32_t bytes=u32(p,24);
  if(bytes){if(!m||!(m->flags&GL_MAP_WRITE_BIT)||!(m->flags&GL_MAP_FLUSH_EXPLICIT_BIT)||offset<0||length<0||(uint64_t)offset>m->length||(uint64_t)length>m->length-(uint64_t)offset||bytes!=(uint64_t)length)return 0;memcpy((uint8_t *)m->address+offset,p+32,bytes);}
  glFlushMappedBufferRange(target,(GLintptr)offset,(GLsizeiptr)length);uint32_t ack=1;return me_write_all(&ack,4);
 }
 if(op==156){
  uint32_t bytes=u32(p,8);
  if(bytes){if(!m||!(m->flags&GL_MAP_WRITE_BIT)||(m->flags&GL_MAP_FLUSH_EXPLICIT_BIT)||bytes!=m->length)return 0;memcpy(m->address,p+16,bytes);}
  uint32_t result=glUnmapBuffer(target);
  if(m){struct ME_BufferMap **slot=me_map_slot(o,m->buffer,m->group);*slot=m->next;free(m);}
  return me_write_all(&result,4);
 }
 return 0;
}
