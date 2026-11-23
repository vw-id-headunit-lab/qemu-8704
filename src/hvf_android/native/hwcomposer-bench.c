#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
enum { HAL_TRANSFORM_FLIP_H=1, HAL_TRANSFORM_FLIP_V=2, HAL_TRANSFORM_ROT_90=4, HAL_TRANSFORM_ROT_180=3, HAL_TRANSFORM_ROT_270=7 };
#include <hardware/hwcomposer.h>
#include <hardware/hwcomposer2.h>
#include <hardware/gralloc.h>
#include <hardware/fb.h>
extern void *calloc(size_t,size_t);extern void free(void*);extern void *memcpy(void*,const void*,size_t);
extern int close(int);extern int usleep(unsigned);extern int pthread_create(unsigned long*,const void*,void*(*)(void*),void*);extern int pthread_detach(unsigned long);
struct timespec{long tv_sec,tv_nsec;};extern int clock_gettime(int,struct timespec*);
extern int __android_log_print(int,const char*,const char*,...);
static framebuffer_device_t *fb;static buffer_handle_t target;static int target_fence=-1;
static hwc2_function_pointer_t cb[7];static void *cbdata[7];static volatile int vsync_enabled;
static struct{hwc2_layer_t id;int type;}layers[512];static uint64_t next_layer=1;
static int64_t now(void){struct timespec t;clock_gettime(1,&t);return (int64_t)t.tv_sec*1000000000LL+t.tv_nsec;}
static void *vsync_thread(void *p){(void)p;for(;;){usleep(16667);if(!vsync_enabled)continue;int64_t n=now();if(cb[HWC2_CALLBACK_VSYNC_2_4])((HWC2_PFN_VSYNC_2_4)cb[HWC2_CALLBACK_VSYNC_2_4])(cbdata[HWC2_CALLBACK_VSYNC_2_4],0,n,16666667);else if(cb[HWC2_CALLBACK_VSYNC])((HWC2_PFN_VSYNC)cb[HWC2_CALLBACK_VSYNC])(cbdata[HWC2_CALLBACK_VSYNC],0,n);}return 0;}
static int32_t callback(hwc2_device_t*d,int32_t desc,void*data,hwc2_function_pointer_t fn){(void)d;if(desc<1||desc>6)return HWC2_ERROR_BAD_PARAMETER;cbdata[desc]=data;cb[desc]=fn;if(desc==HWC2_CALLBACK_HOTPLUG&&fn)((HWC2_PFN_HOTPLUG)fn)(data,0,HWC2_CONNECTION_CONNECTED);return 0;}
static void capabilities(hwc2_device_t*d,uint32_t*n,int32_t*out){(void)d;(void)out;*n=0;}
static int32_t noop(hwc2_device_t*d,...){(void)d;return 0;}
static int32_t unsupported(hwc2_device_t*d,...){(void)d;return HWC2_ERROR_UNSUPPORTED;}
static uint32_t max_virtual(hwc2_device_t*d){(void)d;return 0;}
static int32_t create_layer(hwc2_device_t*d,hwc2_display_t display,hwc2_layer_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;for(int i=0;i<512;i++)if(!layers[i].id){layers[i].id=next_layer++;layers[i].type=HWC2_COMPOSITION_INVALID;*out=layers[i].id;return 0;}return HWC2_ERROR_NO_RESOURCES;}
static int32_t destroy_layer(hwc2_device_t*d,hwc2_display_t display,hwc2_layer_t layer){(void)d;(void)display;for(int i=0;i<512;i++)if(layers[i].id==layer){layers[i].id=0;return 0;}return HWC2_ERROR_BAD_LAYER;}
static int32_t layer_type(hwc2_device_t*d,hwc2_display_t display,hwc2_layer_t layer,int32_t type){(void)d;(void)display;for(int i=0;i<512;i++)if(layers[i].id==layer){layers[i].type=type;return 0;}return HWC2_ERROR_BAD_LAYER;}
static int32_t layer_buffer(hwc2_device_t*d,hwc2_display_t display,hwc2_layer_t layer,buffer_handle_t buffer,int32_t fence){(void)d;(void)display;(void)layer;(void)buffer;if(fence>=0)close(fence);return 0;}
static int32_t changed(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,hwc2_layer_t*out_layers,int32_t*out_types){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;uint32_t k=0,cap=*n;for(int i=0;i<512;i++)if(layers[i].id&&layers[i].type!=HWC2_COMPOSITION_CLIENT){if(out_layers&&out_types&&k<cap){out_layers[k]=layers[i].id;out_types[k]=HWC2_COMPOSITION_CLIENT;}k++;}*n=k;return 0;}
static int32_t validate(hwc2_device_t*d,hwc2_display_t display,uint32_t*types,uint32_t*requests){*types=0;*requests=0;changed(d,display,types,0,0);return *types?HWC2_ERROR_HAS_CHANGES:0;}
static int32_t accept(hwc2_device_t*d,hwc2_display_t display){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;for(int i=0;i<512;i++)if(layers[i].id)layers[i].type=HWC2_COMPOSITION_CLIENT;return 0;}
static int32_t requests(hwc2_device_t*d,hwc2_display_t display,int32_t*req,uint32_t*n,hwc2_layer_t*layers_out,int32_t*out){(void)d;(void)layers_out;(void)out;if(display)return HWC2_ERROR_BAD_DISPLAY;*req=0;*n=0;return 0;}
static int32_t client_target(hwc2_device_t*d,hwc2_display_t display,buffer_handle_t buffer,int32_t fence,int32_t dataspace,hwc_region_t damage){(void)d;(void)dataspace;(void)damage;if(display)return HWC2_ERROR_BAD_DISPLAY;if(target_fence>=0)close(target_fence);target=buffer;target_fence=fence;return 0;}
static int32_t present(hwc2_device_t*d,hwc2_display_t display,int32_t*fence){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*fence=-1;if(target_fence>=0){close(target_fence);target_fence=-1;}if(target){int e=fb->post(fb,target);if(e)return HWC2_ERROR_NO_RESOURCES;}return 0;}
static int32_t release_fences(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,hwc2_layer_t*ls,int32_t*fs){(void)d;(void)ls;(void)fs;if(display)return HWC2_ERROR_BAD_DISPLAY;*n=0;return 0;}
static int32_t configs(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,hwc2_config_t*cfg){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;if(cfg&&*n)cfg[0]=0;*n=1;return 0;}
static int32_t active(hwc2_device_t*d,hwc2_display_t display,hwc2_config_t*cfg){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*cfg=0;return 0;}
static int32_t attr(hwc2_device_t*d,hwc2_display_t display,hwc2_config_t cfg,int32_t attribute,int32_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;if(cfg)return HWC2_ERROR_BAD_CONFIG;switch(attribute){case HWC2_ATTRIBUTE_WIDTH:*out=fb->width;break;case HWC2_ATTRIBUTE_HEIGHT:*out=fb->height;break;case HWC2_ATTRIBUTE_VSYNC_PERIOD:*out=16666667;break;case HWC2_ATTRIBUTE_DPI_X:case HWC2_ATTRIBUTE_DPI_Y:*out=160000;break;case HWC2_ATTRIBUTE_CONFIG_GROUP:*out=0;break;default:return HWC2_ERROR_BAD_PARAMETER;}return 0;}
static int32_t display_type(hwc2_device_t*d,hwc2_display_t display,int32_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*out=HWC2_DISPLAY_TYPE_PHYSICAL;return 0;}
static int32_t zero_support(hwc2_device_t*d,hwc2_display_t display,int32_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*out=0;return 0;}
static int32_t brightness_support(hwc2_device_t*d,hwc2_display_t display,bool*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*out=false;return 0;}
static int32_t connection(hwc2_device_t*d,hwc2_display_t display,uint32_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*out=HWC2_DISPLAY_CONNECTION_TYPE_INTERNAL;return 0;}
static int32_t period(hwc2_device_t*d,hwc2_display_t display,hwc2_vsync_period_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;*out=16666667;return 0;}
static int32_t constraints(hwc2_device_t*d,hwc2_display_t display,hwc2_config_t cfg,hwc_vsync_period_change_constraints_t*c,hwc_vsync_period_change_timeline_t*t){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;if(cfg)return HWC2_ERROR_BAD_CONFIG;t->newVsyncAppliedTimeNanos=c->desiredTimeNanos>now()?c->desiredTimeNanos:now();t->refreshRequired=false;t->refreshTimeNanos=0;return 0;}
static int32_t vsync(hwc2_device_t*d,hwc2_display_t display,int32_t enabled){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;vsync_enabled=enabled==HWC2_VSYNC_ENABLE;return 0;}
static int32_t modes(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,int32_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;if(out&&*n)out[0]=0;*n=1;return 0;}
static int32_t hdr(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,int32_t*out,float*a,float*b,float*c){(void)d;(void)out;if(display)return HWC2_ERROR_BAD_DISPLAY;*n=0;*a=*b=*c=0;return 0;}
static int32_t none(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,int32_t*out){(void)d;(void)out;if(display)return HWC2_ERROR_BAD_DISPLAY;*n=0;return 0;}
static int32_t intents(hwc2_device_t*d,hwc2_display_t display,int32_t mode,uint32_t*n,int32_t*out){(void)mode;return modes(d,display,n,out);}
static int32_t name(hwc2_device_t*d,hwc2_display_t display,uint32_t*n,char*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;const char text[]="Android CPU compositor";uint32_t z=sizeof(text)-1;if(out){uint32_t k=*n<z?*n:z;memcpy(out,text,k);}*n=z;return 0;}
static void dump(hwc2_device_t*d,uint32_t*n,char*out){(void)d;(void)out;*n=0;}
static int32_t target_property(hwc2_device_t*d,hwc2_display_t display,hwc_client_target_property_t*out){(void)d;if(display)return HWC2_ERROR_BAD_DISPLAY;out->pixelFormat=1;out->dataspace=0;return 0;}
static hwc2_function_pointer_t function(hwc2_device_t*d,int32_t x){(void)d;switch(x){
#define F(a,b) case HWC2_FUNCTION_##a:return (hwc2_function_pointer_t)b
F(ACCEPT_DISPLAY_CHANGES,accept);F(CREATE_LAYER,create_layer);F(DESTROY_LAYER,destroy_layer);F(CREATE_VIRTUAL_DISPLAY,unsupported);F(DESTROY_VIRTUAL_DISPLAY,unsupported);F(GET_MAX_VIRTUAL_DISPLAY_COUNT,max_virtual);F(DUMP,dump);F(GET_ACTIVE_CONFIG,active);F(GET_CHANGED_COMPOSITION_TYPES,changed);F(GET_CLIENT_TARGET_SUPPORT,noop);F(GET_COLOR_MODES,modes);F(GET_DISPLAY_ATTRIBUTE,attr);F(GET_DISPLAY_CONFIGS,configs);F(GET_DISPLAY_NAME,name);F(GET_DISPLAY_REQUESTS,requests);F(GET_DISPLAY_TYPE,display_type);F(GET_DOZE_SUPPORT,zero_support);F(GET_HDR_CAPABILITIES,hdr);F(GET_RELEASE_FENCES,release_fences);F(PRESENT_DISPLAY,present);F(REGISTER_CALLBACK,callback);F(SET_CLIENT_TARGET,client_target);F(SET_LAYER_SIDEBAND_STREAM,unsupported);F(SET_COLOR_TRANSFORM,unsupported);F(SET_LAYER_BUFFER,layer_buffer);F(SET_LAYER_COMPOSITION_TYPE,layer_type);F(SET_VSYNC_ENABLED,vsync);F(VALIDATE_DISPLAY,validate);F(GET_PER_FRAME_METADATA_KEYS,none);F(GET_RENDER_INTENTS,intents);F(GET_DISPLAY_IDENTIFICATION_DATA,unsupported);F(GET_DISPLAY_CAPABILITIES,none);F(GET_DISPLAY_BRIGHTNESS_SUPPORT,brightness_support);F(SET_DISPLAY_BRIGHTNESS,unsupported);F(GET_DISPLAY_CONNECTION_TYPE,connection);F(GET_DISPLAY_VSYNC_PERIOD,period);F(SET_ACTIVE_CONFIG_WITH_CONSTRAINTS,constraints);F(GET_SUPPORTED_CONTENT_TYPES,none);F(GET_CLIENT_TARGET_PROPERTY,target_property);
#undef F
 default:if((x>=HWC2_FUNCTION_SET_ACTIVE_CONFIG&&x<=HWC2_FUNCTION_SET_POWER_MODE)||(x>=HWC2_FUNCTION_SET_LAYER_FLOAT_COLOR&&x<=HWC2_FUNCTION_SET_LAYER_PER_FRAME_METADATA)||x==HWC2_FUNCTION_SET_COLOR_MODE_WITH_RENDER_INTENT||x==HWC2_FUNCTION_SET_LAYER_COLOR_TRANSFORM||x==HWC2_FUNCTION_SET_LAYER_PER_FRAME_METADATA_BLOBS||x==HWC2_FUNCTION_SET_CONTENT_TYPE)return(hwc2_function_pointer_t)noop;return 0;}}
static int device_close(hw_device_t*d){free(d);return 0;}
static int device_open(const hw_module_t*m,const char*id,hw_device_t**out){(void)id;const hw_module_t*g=0;int e=hw_get_module(GRALLOC_HARDWARE_MODULE_ID,&g);if(e)return e;e=g->methods->open(g,GRALLOC_HARDWARE_FB0,(hw_device_t**)&fb);if(e)return e;hwc2_device_t*d=calloc(1,sizeof(*d));if(!d)return -12;d->common.tag=HARDWARE_DEVICE_TAG;d->common.version=HWC_DEVICE_API_VERSION_2_0;d->common.module=(hw_module_t*)m;d->common.close=device_close;d->getCapabilities=capabilities;d->getFunction=function;*out=&d->common;unsigned long thread;if(!pthread_create(&thread,0,vsync_thread,0))pthread_detach(thread);__android_log_print(4,"BenchHWC2","original client-composed buffers, HAL2.4 functions supported");return 0;}
static hw_module_methods_t methods={.open=device_open};
__attribute__((visibility("default"))) hw_module_t HMI={.tag=HARDWARE_MODULE_TAG,.module_api_version=HWC_MODULE_API_VERSION_0_1,.hal_api_version=0,.id=HWC_HARDWARE_MODULE_ID,.name="CPU client-target display bridge",.author="MEB-EMU",.methods=&methods};
