
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess

ROOT=Path(__file__).resolve().parents[3]


def build(source,output):
 source=Path(source).resolve(strict=True);output=Path(output).resolve()
 if not output.is_relative_to(ROOT):raise ValueError('Output outside project')
 sdk=Path(os.environ.get('EMU_ANGLE_SDK',str(ROOT/'.local/angle-sdk'))).resolve();compiler=Path(os.environ.get('CC') or shutil.which('gcc') or ROOT/'.local/deps/bin/gcc.exe')
 env=os.environ.copy();env['PATH']=str(compiler.parent)+os.pathsep+env['PATH']
 args=[str(compiler),'-O2','-I'+str(sdk/'include'),'-I'+str(Path(__file__).resolve().parent),str(source),
  '-L'+str(sdk/'lib'),'-lEGL','-lGLESv2','-lm','-o',str(output)]
 result=subprocess.run(args,env=env,capture_output=True)
 return {'argv':args,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
  'headers':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in Path(__file__).resolve().parent.glob('*.h')},
  'stdout':result.stdout.decode('utf8','backslashreplace'),'stderr':result.stderr.decode('utf8','backslashreplace'),
  'exit':result.returncode,'output':str(output),
  'output_sha256':hashlib.sha256(output.read_bytes()).hexdigest()if result.returncode==0 else None}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=Path(__file__).with_name('glhost-win32.c'));parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 result=build(args.source,args.output);print(json.dumps(result));raise SystemExit(result['exit'])


if __name__=='__main__':main()
