
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess

ROOT=Path(__file__).resolve().parents[3]


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--native-bindings',action='store_true');args=parser.parse_args();output=args.output.resolve()
 if not output.is_relative_to(ROOT):raise ValueError('Output outside project')
 compiler=Path(os.environ.get('ZIG') or shutil.which('zig') or ROOT/'.local/toolchains/zig/zig.exe')
 source=Path(__file__).with_name('gles-client.c');env=os.environ.copy()
 env['ZIG_GLOBAL_CACHE_DIR']=str(ROOT/'.local/cache/zig-global')
 env['ZIG_LOCAL_CACHE_DIR']=str(ROOT/'.local/cache/zig-local')
 command=[str(compiler),'cc','-target','aarch64-linux-gnu.2.17','-O2','-fPIC','-shared','-pthread','-mno-outline-atomics','-Wl,-Bsymbolic','-I'+str(Path(os.environ.get('EMU_ANGLE_SDK',str(ROOT/'.local/angle-sdk')))/'include'),'-Wl,-soname,liboem_gl_forward.so',str(source),'-o',str(output)]
 if args.native_bindings:command.insert(-3,'-DOEM_GL_NATIVE_BINDINGS')
 result=subprocess.run(command,env=env,capture_output=True)
 row={'argv':command,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
  'native_bindings':args.native_bindings,'native_aliases_sha256':hashlib.sha256(source.with_name('native-aliases.inc').read_bytes()).hexdigest()if args.native_bindings else None,
  'transport_header_sha256':hashlib.sha256(source.with_name('fd-transport.h').read_bytes()).hexdigest(),
  'EGL_headers':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in source.parent.glob('egl-*')if p.is_file()},
  'stdout':result.stdout.decode('utf8','backslashreplace'),'stderr':result.stderr.decode('utf8','backslashreplace'),
  'exit':result.returncode,'output':str(output),
  'sha256':hashlib.sha256(output.read_bytes()).hexdigest()if result.returncode==0 else None}
 print(json.dumps(row));raise SystemExit(result.returncode)


if __name__=='__main__':main()
