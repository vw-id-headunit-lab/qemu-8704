import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PUBLIC_DIRS={'src','tools','native','runtime','config','licenses','tests','docs'}
PUBLIC_ROOT={'README.md','VERSION','LICENSE','NOTICE.md','运行边界.md','发布状态.md','MEB-EMU.ps1','requirements.txt','pyproject.toml','.gitignore','.gitattributes'}
VERSION=(ROOT/'VERSION').read_text(encoding='utf8').strip()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def public_files(root=ROOT):
    files=[]
    candidates=[root/name for name in PUBLIC_ROOT if (root/name).exists()]
    for name in sorted(PUBLIC_DIRS):
        base=root/name
        if base.is_symlink() or base.is_junction():raise ValueError('Public paths cannot be links')
        if base.exists():candidates.extend(base.rglob('*'))
    for path in candidates:
        relative=path.relative_to(root)
        if path.is_symlink() or path.is_junction():raise ValueError('Public paths cannot be links')
        if not path.is_file() or '__pycache__' in relative.parts or path.suffix in ('.pyc','.pyo'):continue
        files.append(path)
    return sorted(files,key=lambda p:p.relative_to(root).as_posix())


def privacy_findings(root=ROOT):
    findings=[]
    patterns={
        'personal-home':rb'(?i)[a-z]:[\\/]Users[\\/][^\x00\r\n\\/ ]+',
        'research-workspace':rb'(?i)MEB'+rb'-MKII|scratch[/\\][^\x00\r\n ]*20\d{6}T\d{6}Z',
        'private-key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{24}',
    }
    for path in public_files(root):
        relative=path.relative_to(root).as_posix()
        if path.suffix in ('.img','.qcow2','.ram','.pem','.key','.p12'):
            findings.append({'file':relative,'kind':'excluded-input-type'})
        if relative=='native/upstream/qemu-11.1.0.tar.xz':
            if sha(path)!='6ee1d1a61f68212476b27108c26da5f449dc09b626d42f8279ba0dc2e08fa858':
                findings.append({'file':relative,'kind':'upstream-source-identity'})
            continue
        data=path.read_bytes()
        for name,pattern in patterns.items():
            if re.search(pattern,data):
                findings.append({'file':relative,'kind':name})
        for label in ['C:'+chr(92)+'Users'+chr(92),'D:'+chr(92)+'code'+chr(92)+'MEB'+'-MKII']:
            if label.encode('utf-16-le') in data:
                findings.append({'file':relative,'kind':'utf16-private-path'})
    return findings


def environment():
    env=dict(os.environ)
    env['PATH']=str(ROOT/'.local/deps/bin')+os.pathsep+env.get('PATH','')
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env


def runtime_version():
    process=subprocess.run([str(ROOT/'runtime/qemu-system-aarch64.exe'),'--version'],cwd=ROOT,env=environment(),capture_output=True,timeout=20)
    return {'exit':process.returncode,'stdout':process.stdout.decode('utf8','replace'),'stderr':process.stderr.decode('utf8','replace')}


def dependency_state():
    lock=json.loads((ROOT/'config/dependencies.lock.json').read_text(encoding='utf8'))
    result=[]
    for package in lock['packages']:
        for file in package['files']:
            path=ROOT/'.local/deps'/file['target']
            result.append({'file':file['target'],'valid':path.is_file() and sha(path)==file['sha256']})
    return result


def firmware_state():
    manifest=ROOT/'var/inputs/imported.json'
    if not manifest.is_file():
        return {'state':'not-imported','files':0}
    rows=json.loads(manifest.read_text(encoding='utf8'))['files']
    missing=[row['target'] for row in rows if not (ROOT/'var/inputs'/row['target']).is_file()]
    return {'state':'missing-files' if missing else 'imported','files':len(rows),'missing':missing}


def doctor():
    deps=dependency_state()
    python={name:importlib.util.find_spec(name) is not None for name in ['capstone','elftools','dissect.extfs','lz4','lief']}
    version=runtime_version() if all(x['valid'] for x in deps) else {'exit':None,'stdout':'','stderr':'Run setup to install locked dependencies'}
    return {'version':VERSION,'python_3_14':sys.version_info>=(3,14),'python_modules':python,'dependencies':deps,'native':version,'storage_tools':{'qemu_img':(ROOT/'.local/deps/bin/qemu-img.exe').is_file(),'mke2fs_for_new_ext4':(ROOT/'.local/deps/bin/mke2fs.exe').is_file()},'firmware':firmware_state()}


def freeze():
    paths=public_files()
    rows=[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in paths if p.relative_to(ROOT).as_posix()!='config/release-manifest.json']
    target=ROOT/'config/release-manifest.json'
    target.write_text(json.dumps({'schema_version':1,'version':VERSION,'files':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return {'files':len(rows)}


def verify():
    manifest=json.loads((ROOT/'config/release-manifest.json').read_text(encoding='utf8'))
    actual={p.relative_to(ROOT).as_posix():p for p in public_files() if p.relative_to(ROOT).as_posix()!='config/release-manifest.json'}
    errors=[]
    expected={x['path']:x for x in manifest['files']}
    if set(actual)!=set(expected):
        errors.append({'inventory_missing':sorted(set(expected)-set(actual)),'inventory_extra':sorted(set(actual)-set(expected))})
    for name,row in expected.items():
        if name in actual and (actual[name].stat().st_size!=row['bytes'] or sha(actual[name])!=row['sha256']):
            errors.append({'file':name,'kind':'hash-mismatch'})
    for name,path in actual.items():
        if path.suffix=='.py':
            try:ast.parse(path.read_bytes())
            except SyntaxError:errors.append({'file':name,'kind':'python-syntax'})
    findings=privacy_findings()
    return {'version':VERSION,'files':len(actual),'hash_errors':errors,'privacy_findings':findings,'passed':not errors and not findings}


def import_inputs(bundle):
    bundle=Path(bundle).resolve(strict=True)
    source=json.loads((bundle/'manifest.json').read_text(encoding='utf8'))
    if source.get('schema_version')!=1 or not isinstance(source.get('files'),list) or not source['files']:
        raise ValueError('Require a non-empty input bundle manifest')
    destination=ROOT/'var/inputs'
    planned=[]
    seen=set()
    for row in source['files']:
        target=Path(row['target']);origin=Path(row['source'])
        if any(x.endswith((' ','.')) for x in target.parts+origin.parts):raise ValueError('Ambiguous Windows bundle path')
        if not target.parts or not origin.parts or target.is_absolute() or origin.is_absolute() or any(x in ('..','') or ':' in x or '\0' in x for x in target.parts+origin.parts):
            raise ValueError('Bundle paths must be safe relative paths')
        if target.as_posix().casefold()=='imported.json' or target.as_posix().casefold() in seen or target.suffix.lower() in ('.py','.ps1','.exe','.dll','.bat','.cmd'):
            raise ValueError('Bundle cannot replace executable project code or import records')
        seen.add(target.as_posix().casefold())
        src=(bundle/origin).resolve(strict=True);dst=(destination/target).resolve()
        if not src.is_relative_to(bundle) or not dst.is_relative_to(destination.resolve()) or (bundle/origin).is_symlink() or not src.is_file():
            raise ValueError('Bundle source/target escapes its root')
        if not re.fullmatch('[0-9a-f]{64}',row['sha256']) or sha(src)!=row['sha256']:
            raise ValueError('Bundle input checksum mismatch')
        if dst.exists() and sha(dst)!=row['sha256']:
            raise ValueError('Existing private input differs; no automatic overwrite')
        planned.append((src,dst,{'target':target.as_posix(),'sha256':row['sha256'],'bytes':src.stat().st_size}))
    for src,dst,row in planned:
        if not dst.exists():
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        if sha(dst)!=row['sha256']:
            raise ValueError('Imported input checksum mismatch')
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'imported.json').write_text(json.dumps({'schema_version':1,'files':[x[2] for x in planned]},indent=2)+'\n',encoding='utf8')
    return {'imported':len(planned),'location':'var/inputs','included_in_release':False}


def launch_plan(profile):
    state=firmware_state()
    if state['state']!='imported':
        raise ValueError('Import a prepared firmware input bundle before plan/start')
    profile=Path(profile or ROOT/'config/launch.example.json').resolve(strict=True)
    data=json.loads(profile.read_text(encoding='utf8'))
    allowed={'Native','NoBrowser','NativeWindow','LocalLinuxShell','LocalAndroidShell','QnxMaliControlJobs','KeepLinuxOnFailure','LinuxDataFrom','LinuxHmiDataFrom','LinuxHmiInstallMedia','QnxDataFrom','ProvisionNavData','PrioritizeOemPostInit','NativeHmiStartGrace','NativeWaylandTrace','NativeStockWeston','NativeParallelTcg','NativeHmiSignalObserve','InfoNativeBoot','NativeMaliControlAs0','QnxPhy88q2112','QnxDisplayProfile','Seconds','Port','ConsolePort'}
    if data.get('schema_version')!=1 or set(data['arguments'])-allowed:
        raise ValueError('Unsupported launch profile arguments')
    if data['arguments'].get('Native') is not True:
        raise ValueError('This preview uses the native carrier only')
    arguments=['-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'src/emu/Launch-OfflineDevelopment.ps1')]
    for name,value in data['arguments'].items():
        if value is False or value is None:continue
        arguments.append('-'+name)
        if value is not True:
            if name.endswith('From') or name=='LinuxHmiInstallMedia':
                value=(ROOT/value).resolve()
                if not value.is_relative_to(ROOT/'var') or not value.exists():
                    raise ValueError('Required prepared private input is missing: '+name)
            arguments.append(str(value))
    env=environment()
    for key in list(env):
        if key.startswith('OEM_8704_') or key=='EMU_DIAGVAL_SHA256':env.pop(key)
    for name,value in data['environment'].items():
        if not name.startswith('OEM_8704_') and name!='EMU_DIAGVAL_SHA256':
            raise ValueError('Unsupported profile environment key')
        if name in ('OEM_8704_OFFLINE_DIAGVAL','OEM_8704_OFFLINE_ANDROID_SEED'):
            path=(ROOT/value).resolve()
            if not path.is_relative_to(ROOT/'var') or not path.exists():raise ValueError('Missing private profile input')
            value=str(path)
        env[name]=str(value)
    return arguments,env


def package():
    result=verify()
    if not result['passed']:raise ValueError('Release verification failed')
    destination=ROOT/'dist';destination.mkdir(exist_ok=True)
    target=destination/('MEB-EMU-'+VERSION+'.zip')
    temporary=target.with_suffix('.tmp')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in public_files():
            name='MEB-EMU-'+VERSION+'/'+path.relative_to(ROOT).as_posix()
            info=zipfile.ZipInfo(name,(2026,11,23,0,0,0))
            info.compress_type=zipfile.ZIP_STORED if path.suffix=='.xz' else zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            archive.writestr(info,path.read_bytes())
    temporary.replace(target)
    digest=sha(target)
    target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
    return {'file':str(target),'bytes':target.stat().st_size,'sha256':digest,'files':len(public_files())}


def main():
    parser=argparse.ArgumentParser(prog='MEB-EMU')
    parser.add_argument('action',choices=['doctor','verify','freeze','test','import','plan','start','package'])
    parser.add_argument('--bundle')
    parser.add_argument('--profile')
    args=parser.parse_args()
    try:
        if args.action=='doctor':result=doctor();status=0 if result['native']['exit']==0 and all(result['python_modules'].values()) else 2
        elif args.action=='verify':result=verify();status=0 if result['passed'] else 2
        elif args.action=='freeze':result=freeze();status=0
        elif args.action=='import':result=import_inputs(args.bundle);status=0
        elif args.action=='package':result=package();status=0
        elif args.action=='test':
            return subprocess.call([sys.executable,'-X','utf8','-B','-m','unittest','discover','-s','tests','-v'],cwd=ROOT,env=environment())
        else:
            arguments,env=launch_plan(args.profile)
            if args.action=='start':return subprocess.call(['powershell.exe',*arguments],cwd=ROOT,env=env)
            result={'entry':'src/emu/Launch-OfflineDevelopment.ps1','firmware':firmware_state(),'profile_valid':True};status=0
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return status
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
