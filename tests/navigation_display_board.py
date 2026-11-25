from pathlib import Path
import json,subprocess,sys,tempfile
r=Path(__file__).resolve().parents[1];p=Path(sys.argv[1])if len(sys.argv)>1 else r/'src/hvf_bench/native/navigation-gnss.c';records=[]
try:
 s=p.read_text();body=s[s.index('static int same_path('):s.index('__attribute__((visibility("default"))) void *fopen(')];body=body.replace('dlsym(','lookup(').replace('__errno_location()','error_pointer()')
 c='''#include <stddef.h>
#include <string.h>
static int error_value=2,existing=0,denied=0;
static int *error_pointer(void){return &error_value;}
static int dprintf(int fd,const char *format,...){return 0;}
static void *original(const char *path,const char *mode){
if(strcmp(path,"/sys/devices/platform/common_gpio_default/display_id")==0){error_value=denied?13:2;return existing?(void *)9:0;}
if(strcmp(path,"/tmp/bench-navigation/display_id")==0)return(void *)7;
return 0;}
static void *lookup(void *handle,const char *symbol){return(void *)original;}
'''+body+'''
int main(void){const char *path="/sys/devices/platform/common_gpio_default/display_id";
if(board_open(path,"r","fopen")!=(void *)7)return 2;
existing=1;if(board_open(path,"r","fopen")!=(void *)9)return 3;
existing=0;denied=1;if(board_open(path,"r","fopen"))return 4;
denied=0;if(board_open("/sys/other/display_id","r","fopen"))return 5;
return 0;}
'''
 with tempfile.TemporaryDirectory()as tmp:
  f=Path(tmp)/'test.c';f.write_text(c);exe=f.with_suffix('');q=subprocess.run(['cc',str(f),'-o',str(exe)],capture_output=True,text=True);records.append(dict(argv=q.args,stdout=q.stdout,stderr=q.stderr,exit=q.returncode));assert q.returncode==0
  q=subprocess.run([str(exe)],capture_output=True,text=True);records.append(dict(argv=q.args,stdout=q.stdout,stderr=q.stderr,exit=q.returncode));assert q.returncode==0
 out=dict(scope='Real C fallback execution: ENOENT exact display only; real value and permission error preserved',commands=records,exit=0)
except Exception as e:out=dict(error=repr(e),commands=records,exit=2)
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
