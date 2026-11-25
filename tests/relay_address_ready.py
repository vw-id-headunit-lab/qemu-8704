from pathlib import Path
import ast,errno,json,socket,sys,time,types
r=Path(__file__).resolve().parents[1];source=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r/'src/hvf_bench/guest/qnx-gl-relay.py';report={'source':str(source),'input':'First actual IPv6 bind uses unavailable fd53::108; subsequent IPv6 bind uses available ::1, original VIWI forbidden port ranges retained','scope':'Actual host IPv6 address startup race, not OEM boot acceptance'};created=[];failed=[];listener=None
try:
 tree=ast.parse(source.read_bytes());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='listen_native_bridge')
 class BindSocket:
  def __init__(self,*args):self.sock=socket.socket(*args);self.ordinal=len(created);created.append(self)
  def bind(self,addr):
   address=('fd53:7cb8:383:3::108',addr[1])if self.ordinal==0 else addr
   try:return self.sock.bind(address)
   except OSError as e:failed.append({'errno':e.errno,'error':str(e)});raise
  def __getattr__(self,name):return getattr(self.sock,name)
 env=dict(socket=types.SimpleNamespace(socket=BindSocket,AF_INET6=socket.AF_INET6,SOCK_STREAM=socket.SOCK_STREAM),time=time,errno=errno)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),env);listener=env['listen_native_bridge']('::1');port=listener.getsockname()[1]
 with socket.create_connection(('::1',port),timeout=2)as peer:
  connection,_=listener.accept()
  with connection:peer.sendall(b'QNX_GL_READY');received=connection.recv(64)
 report.update(attempts=len(created),actual_errors=failed,received=received.decode(),forbidden_port=False if not any(low<=port<=high for low,high in [(29700,29799),(29816,29816),(47000,49151),(49200,49799)])else True)
 assert failed[0]['errno']==errno.EADDRNOTAVAIL and received==b'QNX_GL_READY'and not report['forbidden_port']
 report['exit']=0
except Exception as e:report.update(exit=2,error=repr(e),actual_errors=failed)
finally:
 for sock in created:sock.close()
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
