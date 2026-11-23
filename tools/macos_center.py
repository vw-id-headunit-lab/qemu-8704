import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
PYTHON=ROOT/'.venv/bin/python'
CURRENT=ROOT/'var/current-center.json'


def profile():
    source=(ROOT/'src/runner/Run-OfflineDevelopment.ps1').read_text()
    body=source.split('$argsList = @(',1)[1].split('\n)',1)[0]
    args=[]
    for line in body.splitlines()[3:]:
        args.extend(re.findall(r"'([^']*)'",line))
    args[args.index('--abox-dsp-xtensa')+1]=str(ROOT/'runtime/darwin/qemu-system-xtensa')
    args += ['--pmu-om-input','0','--qnx-dp0-phy','--qnx-dp0-link-config','--qnx-dp0-irq','--qnx-dp0-aux-sink',
             '--qnx-dpp','--linux-dwc3','--linux-usb-phy','--qnx-dsi','--android-ufs-overlay',
             '--linux-native-console-port','32228','--linux-hmi-overlay',str(ROOT/'var/inputs/hmi/install-overlay/result.json'),
             '--linux-ivi-data','virtual-empty-ivi-data-ext4-v1','--linux-ivi-parent',str(ROOT/'var/inputs/seeds/linux'),
             '--mali-g3d1-jm-topology','virtual-g76-vm3-control-as0-v1','--qnx-g3d00-jm-topology',
             '--qnx-g3d00-control-jobs','--linux-factory-bank','--qnx-factory-bank','--qnx-bts',
             '--local-linux-shell','--local-android-shell','--seed-native-nav-data',
             '--prioritize-oem-post-init','--native-hmi-start-grace','--native-wayland-trace',
             '--native-stock-weston','--native-parallel-tcg','--linux-systemd-info','--host-file-ram',
             '--keep-linux-on-failure','--qnx-phy88q2112','--qnx-display-profile','iid-arhud']
    seed=ROOT/'var/inputs/seeds/qnx'
    if (seed/'darwin-relocation.json').is_file():args += ['--qnx-ufs-parent',str(seed)]
    env=dict(os.environ)
    for k in list(env):
        if k.startswith('OEM_8704_') or k=='EMU_DIAGVAL_SHA256':env.pop(k)
    env.update({
        'OEM_8704_OFFLINE_DEVELOPMENT':'android-private-unencrypted-v1',
        'OEM_8704_NATIVE_LIFETIME':'until-stop-v1',
        'OEM_8704_ISOLATED_LINUX_UART':'4','OEM_8704_VERIFIED_LINUX_SEED':'1',
        'OEM_8704_VM3_SECOND_BANK':'original-b0-v1',
        'OEM_8704_OFFLINE_TDM_FAILURE':'keep-running-v1','OEM_8704_OFFLINE_HUNG_TASK':'warn-v1',
        'OEM_8704_OFFLINE_RUNTIME_REBOOT':'reject-v1','OEM_8704_OFFLINE_HMI_ORDER':'post-init-v1',
        'OEM_8704_OFFLINE_BASE_RAM':'file-v1','OEM_8704_NATIVE_WALL_CLOCK':'half-v1',
        'OEM_8704_GL_FORWARD_BOOT':'original-hmi-egl-v1','OEM_8704_OFFLINE_BENCH':'virtual-bench-v1',
        'OEM_8704_OFFLINE_BENCH_PANEL':'bench-1920x1080-v1',
        'OEM_8704_OFFLINE_BENCH_POWER':'factory-rsi-power-v1',
        'OEM_8704_OFFLINE_BENCH_INPUT_READY':'factory-input-ready-v1',
        'OEM_8704_OFFLINE_BENCH_RSI_QUEUE':'original-found-dispatch-v1',
        'OEM_8704_OFFLINE_BENCH_IGNITION':'vehicle-ignition-on-v1',
        'OEM_8704_OFFLINE_BENCH_GUEST':'factory-anonymous-guest-v1',
        'OEM_8704_OFFLINE_BENCH_RSI_POWER':'original-vehicle-state-io-recovery-v1',
        'OEM_8704_OFFLINE_BENCH_RSI_ROUTE':'original-local-vehicle-provider-v1',
        'OEM_8704_QNX_HUD_STARTUP':'bench-arhud-v1',
        'PYTHONDONTWRITEBYTECODE':'1',
        'DYLD_LIBRARY_PATH':str(ROOT/'.local/angle-sdk/lib')})
    android_seed=ROOT/'var/sessions/android-seed-20261119T035932852'
    if (android_seed/'darwin-relocation.json').is_file():
        env['OEM_8704_OFFLINE_ANDROID_SEED']=str(android_seed)
    return args,env


def serve(session):
    session=Path(session)
    deadline=time.monotonic()+180
    while True:
        try:
            focus=json.loads((session/'vlm-console-focus.json').read_bytes())
            if focus.get('running') and 0<=time.monotonic()-focus['host_monotonic']<2.5:break
            if (session/'integrity.json').exists():raise RuntimeError('Center session ended before page attachment')
        except (OSError,ValueError,KeyError):pass
        if time.monotonic()>deadline:raise TimeoutError('Waiting for live center console')
        time.sleep(.2)
    sys.path.insert(0,str(ROOT/'src/emu'))
    import native_center_live_darwin
    sys.modules['native_center_live']=native_center_live_darwin
    previous=session/'native-console-server.json'
    if previous.exists():
        import original_8704_vm as api
        token=json.loads(previous.read_bytes())['url'].split('#token=',1)[1]
        create_server=api.create_server
        def resumed_server(*args,**kwargs):
            kwargs['token']=token
            return create_server(*args,**kwargs)
        api.create_server=resumed_server
    sys.argv=[str(ROOT/'src/emu/native_offline_console_darwin.py'),'serve','--port','18740',
              '--native-session',str(session),'--native-interactive']
    runpy.run_path(str(ROOT/'src/emu/native_offline_console_darwin.py'),run_name='__main__')


def start():
    if CURRENT.exists():
        old=json.loads(CURRENT.read_bytes());pid=old['runner_pid']
        try:os.kill(pid,0)
        except ProcessLookupError:pass
        else:raise RuntimeError('Center session already active: '+old['session'])
    if CURRENT.exists():
        previous=Path(json.loads(CURRENT.read_bytes())['session'])/'native-console-server.json'
        if previous.exists():
            from urllib.request import Request,urlopen
            from urllib.error import URLError
            url=json.loads(previous.read_bytes())['url']
            if not url.startswith('http://127.0.0.1:18740/#token='):raise ValueError('Unexpected previous page origin')
            try:
                request=Request('http://127.0.0.1:18740/api/shutdown',data=b'{}',headers={'X-8704-Token':url.split('#token=',1)[1],'Content-Type':'application/json'})
                with urlopen(request,timeout=5) as response:response.read()
                time.sleep(.7)
            except URLError:pass
    name='center-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    session=ROOT/'var/sessions'/name
    session.parent.mkdir(parents=True,exist_ok=True)
    args,env=profile()
    command=[str(PYTHON),'-B',str(ROOT/'src/runner/trace-offline-live-darwin.py'),'probe',
             '--output',str(session),'--seconds','240','--qemu',str(ROOT/'runtime/darwin/qemu-system-aarch64'),*args]
    stdout=session.parent/(name+'.stdout');stderr=session.parent/(name+'.stderr')
    with stdout.open('xb')as out,stderr.open('xb')as err:
        runner=subprocess.Popen(command,cwd=ROOT,env=env,stdout=out,stderr=err,start_new_session=True)
    state={'runner_pid':runner.pid,'session':str(session),'argv':command,'stdout':str(stdout),'stderr':str(stderr)}
    with (session.parent/(name+'.page.stdout')).open('xb') as out,(session.parent/(name+'.page.stderr')).open('xb') as err:
        page=subprocess.Popen([str(PYTHON),'-B',str(Path(__file__).resolve()),'serve','--session',str(session)],cwd=ROOT,stdout=out,stderr=err,start_new_session=True)
    state['page_pid']=page.pid
    CURRENT.write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps({k:state[k] for k in ('runner_pid','session','stderr')},indent=2),flush=True)
    return 0


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','serve','status','stop']);p.add_argument('--session',type=Path);a=p.parse_args()
    if a.action=='start':return start()
    if a.action=='serve':return serve(a.session or Path(json.loads(CURRENT.read_bytes())['session']))
    state=json.loads(CURRENT.read_bytes());session=Path(state['session'])
    if a.action=='stop':
        (session/'native-stop.request').write_text('user-stop\n')
        print(json.dumps({'stopping':str(session)}));return 0
    for name in ('native-runtime-state.json','gl-forward-runtime.json','vlm-console-focus.json'):
        f=session/name
        if f.exists():state[name]=json.loads(f.read_bytes())
    print(json.dumps(state,indent=2));return 0


if __name__=='__main__':
    raise SystemExit(main())
