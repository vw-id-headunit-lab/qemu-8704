from pathlib import Path
import json,sys,time
p=Path(sys.argv[1]).resolve();namespace={'__file__':str(p),'__name__':'deadline_source'};exec(compile(p.read_bytes(),str(p),'exec'),namespace)
command='/bin/busybox sleep 5; echo SLOW_COMMAND_FINISHED';started=time.monotonic();record={'source':str(p),'input':command,'timeout':3}
try:record['stdout']=namespace['execute'](command,3);record['error']=None
except Exception as error:record['error']=str(error)
record['elapsed']=time.monotonic()-started
time.sleep(max(0,5.2-record['elapsed']))
record['recovery_stdout']=namespace['execute']('echo CONTROL_SHELL_RETAINED',5)
record['passed']=bool(record['error']and 'Guest command deadline reached; shell retained:'in record['error']and record['elapsed']<3 and 'CONTROL_SHELL_RETAINED'in record['recovery_stdout'])
print(json.dumps(record,ensure_ascii=False,indent=2));raise SystemExit(0 if record['passed']else 2)
