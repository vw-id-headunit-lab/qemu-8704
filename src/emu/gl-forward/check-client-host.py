
from pathlib import Path
import argparse,hashlib,json,os,subprocess,threading,tempfile

ROOT=Path(__file__).resolve().parents[3]


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('client',type=Path);parser.add_argument('host',type=Path);parser.add_argument('--expect',default='TRIANGLE_CENTER=4080bfff CORNER=000000ff REAL_DELETE=1 GL_ERROR=0');args=parser.parse_args()
 env=os.environ.copy();env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
 with tempfile.TemporaryDirectory(prefix='GL-client-host-',dir=ROOT/'var/inputs/support/center-live-render/center-power/rsi-queue/gl-forward')as td:
  env['MHI2_GL_FRAME']=str(Path(td)/'renderer.ppm')
  client=subprocess.Popen([str(args.client.resolve(strict=True))],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
  host=subprocess.Popen([str(args.host.resolve(strict=True))],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
  streams={'requests':bytearray(),'replies':bytearray()};errors=[]
  def relay(source,destination,key):
   try:
    while data:=source.read1(65536):
     if len(streams[key])+len(data)>4<<20:raise ValueError('Fixture wire budget exceeded')
     streams[key].extend(data);destination.write(data);destination.flush()
   except (OSError,ValueError)as error:errors.append(str(error))
   finally:destination.close()
  threads=[threading.Thread(target=relay,args=(client.stdout,host.stdin,'requests'),daemon=True),threading.Thread(target=relay,args=(host.stdout,client.stdin,'replies'),daemon=True)]
  for thread in threads:thread.start()
  try:client_code=client.wait(timeout=45);host_code=host.wait(timeout=15)
  finally:
   for process in [client,host]:
    if process.poll()is None:process.terminate();process.wait(timeout=10)
   for thread in threads:thread.join(timeout=3)
  frame=Path(td)/'renderer.ppm';frame_data=frame.read_bytes()if frame.exists()else b''
  row={'event':'copied-GL-EGL-client-to-actual-ANGLE-host','client_exit':client_code,'host_exit':host_code,
   'client_stderr':client.stderr.read().decode('utf8','backslashreplace'),'host_stderr':host.stderr.read().decode('utf8','backslashreplace'),
   'wire':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest(),'hex':v.hex()}for k,v in streams.items()},
   'relay_errors':errors,'frame':{'bytes':len(frame_data),'sha256':hashlib.sha256(frame_data).hexdigest()if frame_data else None},'Linux_ARM64_execution':False,'original_HMI_execution':False,'guest_dynamic_FPS_verified':False}
  print(json.dumps(row))
  assert client_code==host_code==0 and not errors,row
  assert 'unsupported'not in row['client_stderr'],row
  assert args.expect in row['client_stderr'],row
  if args.expect.startswith('EGL_REAL_PIXELS='):
   header=b'P6\n1560 700\n255\n';assert frame_data==header+bytes([255,0,0])*(1560*700),row
  assert 'Direct3D11'in row['client_stderr']and'SwiftShader'not in row['client_stderr']and'WARP'not in row['client_stderr'],row


if __name__=='__main__':main()
