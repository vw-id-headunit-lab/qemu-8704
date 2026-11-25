from pathlib import Path
import sys,json,hashlib,tempfile,subprocess
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];root=binary.parents[2];p=root/'src/emu/gl-forward/qnx-transport.h';s=p.read_text();record={'label':label,'input':'Same owned stream: EAGAIN, short read/write, EINTR, EAGAIN, remaining bytes; exact16 byte response/request, one unlock, no reconnect','source_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
try:
 a=s[s.index('static void io_send'):s.index('static void connect_once')];b=s[s.index('static int recv_bytes'):s.index('static int recv_all')]
 code='''#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int fake_errno,bridge_in=7,bridge_out=7,unlocks,reads,writes,received,sent;
#define errno fake_errno
#define EINTR 4
static char outgoing[16];static const char incoming[]="0123456789abcdef";
static void transport_failed(void){fprintf(stderr,"terminal error=%d reads=%d writes=%d received=%d sent=%d\\n",errno,reads,writes,received,sent);exit(1);}
static void unlock_record(void){unlocks++;}
static int usleep(unsigned n){return 0;}
static long fake_read(int fd,void *p,unsigned long n){reads++;if(reads==1||reads==4){errno=11;return -1;}if(reads==3){errno=4;return -1;}if(n>5)n=5;memcpy(p,incoming+received,n);received+=n;return n;}
static long fake_write(int fd,const void *p,unsigned long n){writes++;if(writes==1||writes==4){errno=11;return -1;}if(writes==3){errno=4;return -1;}if(n>3)n=3;memcpy(outgoing+sent,p,n);sent+=n;return n;}
#define read fake_read
#define write fake_write
'''+a+b+'''
int main(void){char response[16];recv_bytes(response,16,1);io_send(incoming,16);if(memcmp(response,incoming,16)||memcmp(outgoing,incoming,16)||received!=16||sent!=16||unlocks!=1)return 3;printf("same stream exact16 read=%d write=%d unlock=%d reconnect=0\\n",reads,writes,unlocks);return 0;}
'''
 with tempfile.TemporaryDirectory()as t:
  c=Path(t)/'fixture.c';c.write_text(code);exe=Path(t)/'fixture';x=subprocess.run(['/usr/bin/cc',str(c),'-o',str(exe)],capture_output=True,text=True);assert x.returncode==0,x.stderr;a=subprocess.run([str(exe)],capture_output=True,text=True);record.update(stdout=a.stdout,stderr=a.stderr,exit=a.returncode,scope='Exact product send/receive helpers with deterministic stream events; real original native re-execution separately required')
except Exception as ex:record.update(exit=1,error=repr(ex))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
