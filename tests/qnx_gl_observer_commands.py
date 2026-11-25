from pathlib import Path
import sys,json,re,hashlib
r=Path.cwd();binary=Path(sys.argv[1]).resolve();label=sys.argv[2];p=binary.parents[2]/'src/emu/gl-forward/qnx-transport.h';s=p.read_text();record={'source':str(p),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'label':label,'input':'Independent observer must capture GTF retained buffer before hardware log floods; initial observation must begin before GTF termination; source contract only, actual QNX execution separately required'}
try:
 assert '/proc/boot/slog2info -b GtfStartup -S'in s,'missing targeted GTF log buffer'
 assert '/proc/boot/slog2info -b wdi,HeadUpDisplay_app -S'in s,'missing original HUD logs'
 assert 'for(unsigned i=0;i<10;i++)usleep(500000);'in s,'initial observer delay still misses live GTF'
 assert 'kind<sizeof(commands)/sizeof(commands[0])'in s,'new command omitted from loop'
 record.update(exit=0,targeted_gtf_logs=True,targeted_original_hud_logs=True,initial_delay_microseconds=5000000)
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
