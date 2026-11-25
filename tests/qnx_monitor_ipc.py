from pathlib import Path
import sys,json,hashlib
r=Path.cwd();binary=Path(sys.argv[1]).resolve();root=binary.parents[2];label=sys.argv[2];p=root/'src/emu/gl-forward/qnx-transport.h';source=p.read_text();body=source.split('static _Noreturn void qnx_native_observer(void)\n{',1)[1];record=dict(label=label,input='Explicit native observer: actual original GTF/MgrTsk channels, connections, POSIX timers and threads; no synthetic heartbeat',source_sha256=hashlib.sha256(p.read_bytes()).hexdigest())
try:
 expected=['pidin -p GtfStartup channels','pidin -p GtfStartup fds','pidin -p GtfStartup timers','pidin -p GtfStartup threads','pidin -p MgrTsk channels','pidin -p MgrTsk fds','pidin -p MgrTsk timers','pidin -p MgrTsk threads']
 record['queries']=[x for x in expected if x in body];assert len(record['queries'])==len(expected),'Missing native monitor endpoint/timer query';assert 'unsetenv("LD_PRELOAD")'in body and 'MEB_QNX_OBSERVE' in source;assert 'MsgSendPulse('not in body and 'SignalKill('not in body
 record.update(exit=0,scope='Read-only native command composition; actual native outputs required separately, not graphics acceptance')
except Exception as e:record.update(exit=1,error=repr(e))
(r/'.local/mac-port/transaction'/('qnx-gl-forward-'+label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));raise SystemExit(record['exit'])
