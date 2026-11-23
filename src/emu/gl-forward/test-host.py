
from pathlib import Path
import argparse,hashlib,json,os,struct,subprocess,tempfile

ROOT=Path(__file__).resolve().parents[3]


def packet(op,payload=b''):
 return struct.pack('<II',op,len(payload))+payload


def checks(binary):
 binary=Path(binary).resolve(strict=True);env=os.environ.copy()
 env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+str(ROOT/'.local/deps/bin')+os.pathsep+env['PATH']
 rows=[]
 with tempfile.TemporaryDirectory(prefix='GL-host-',dir=ROOT/'var/inputs/support/center-live-render/center-power/rsi-queue/gl-forward')as td:
  frame=Path(td)/'actual-frame.ppm';env['MHI2_GL_FRAME']=str(frame)
  color=packet(119,struct.pack('<I',1))+packet(1,struct.pack('<II',1560,700))+packet(3,struct.pack('<4f',.25,.5,.75,1))+packet(4,struct.pack('<I',0x4000))
  readback=packet(120,struct.pack('<6I',0,0,2,2,0x1908,0x1401))+packet(105)+packet(5)
  cases=[('ANGLE-shaders',['--check-shaders'],b''),('GLES-pixels',[],color+readback),
   ('bad-record',[],packet(3,b'bad')),('unsupported-video',[],packet(122,struct.pack('<I',1)))]
  for label,args,data in cases:
   result=subprocess.run([str(binary),*args],input=data,env=env,capture_output=True,timeout=60)
   row={'label':label,'argv':[str(binary),*args],'stdin_hex':data.hex(),'stdout_hex':result.stdout.hex(),
    'stderr':result.stderr.decode('utf8','backslashreplace'),'exit':result.returncode};rows.append(row)
   if label=='ANGLE-shaders':assert result.returncode==0 and b'75 translated shaders checked, 0 failed'in result.stderr,row
   elif label=='GLES-pixels':
    assert result.returncode==0 and result.stdout==bytes([64,128,191,255])*4+bytes(4),row
    assert b'Direct3D11'in result.stderr and b'SwiftShader'not in result.stderr and b'WARP'not in result.stderr,row
    ppm=frame.read_bytes();header=b'P6\n1560 700\n255\n';assert ppm[:len(header)]==header and ppm[len(header):]==bytes([64,128,191])*(1560*700)
    row['frame_sha256']=hashlib.sha256(ppm).hexdigest();row['frame_bytes']=len(ppm)
   else:assert result.returncode==2 and not result.stdout,row
 return {'event':'original-upstream-dispatch-Windows-ANGLE-functional-check','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
  'records':rows,'original_HMI_frame':False,'guest_touch_verified':False,'dynamic_guest_FPS_verified':False}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('binary',type=Path);args=parser.parse_args();print(json.dumps(checks(args.binary)))


if __name__=='__main__':main()
