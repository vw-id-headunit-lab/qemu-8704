
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib,json,os,re,runpy,struct,sys,time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/emu'))
import original_8704_vm as vm
import vlm_live as vlm_platform
from native_live_policy import profile_seconds

out=Path(sys.argv[sys.argv.index('--output')+1]).resolve()
run_seconds=int(sys.argv[sys.argv.index('--seconds')+1])
stock_profile_seconds=profile_seconds(run_seconds, extended='--extended-observation' in sys.argv, stock=all(x in sys.argv for x in ('--native-stock-weston','--load-qnx','--load-linux','--load-android','--native-parallel-tcg')), prepare='--prepare-linux-seed' in sys.argv)
stock_live=stock_profile_seconds>240
if stock_live:
                                                                      
                                                                          
    import linux_early_uart
    linux_early_uart.HMI_START_GRACE_SECONDS=stock_profile_seconds
short_validation=run_seconds<=120
vhpath='/machine/exynos-ufs-vh1'
orig=vm.QMP.execute
state={'last':0.0,'rows':0,'ever_vm3_active':False}
sample_center=None
center_events=None
qnx_live_log='--qnx-factory-bank' in sys.argv
qnx_display_profile='--qnx-display-profile' in sys.argv
if qnx_display_profile:
    import qnx_display_identity
if qnx_live_log:
    import cal_init_platform as cal
    cal.qnx_log_source()
    import qnx_native_display as qnx_display
    qnx_display.validate_contract()
    qnx_screen_candidates=runpy.run_path(str(Path(__file__).with_name('qnx-buffer-capture.py')))['candidates']
native_rx_watch='--linux-native-console-port' in sys.argv
if native_rx_watch:
    import linux_uart_platform as uart
    uart.native_rx_contract()
if '--native-center-scanout' in sys.argv:
    sys.argv.remove('--native-center-scanout')
    symbols=Path(sys.argv[sys.argv.index('--symbol-directory')+1])/'linux-symbols.json'
    drm_symbols=json.loads(symbols.read_bytes())
    sample_center=runpy.run_path(str(Path(__file__).with_name('native-linux-scanout.py')))['record_current']
    if ('--native-stock-weston' in sys.argv and not short_validation and
            run_seconds<=stock_profile_seconds and os.environ.get('OEM_8704_IVI_LIFECYCLE_SAMPLE')=='1'):
        sample_ivi=runpy.run_path(str(Path(__file__).with_name('observe-live-ivi.py')))['capture']
    else:
        sample_ivi=None
    if not short_validation and run_seconds<=stock_profile_seconds and os.environ.get('OEM_8704_CENTER_EVENT_SAMPLE')=='1':
        event_code=runpy.run_path(str(Path(__file__).with_name('center-event-sample.py')))
        center_events=event_code['CenterEventCadence']()
        center_history_property=event_code['CENTER_GPU_HISTORY']


center_lean = bool(sample_center and run_seconds == stock_profile_seconds
                   and '--native-stock-weston' in sys.argv
                   and os.environ.get('OEM_8704_CENTER_LEAN_SAMPLING') == '1')
early_boot_timing=os.environ.get('OEM_8704_EARLY_BOOT_TIMING')=='1'
android_startup_ring=os.environ.get('OEM_8704_ANDROID_STARTUP_RING')=='1'
if android_startup_ring and (run_seconds != stock_profile_seconds or early_boot_timing or
        not all(k in sys.argv for k in ('--load-android','--native-parallel-tcg','--symbol-directory'))):
    raise ValueError('Android startup ring requires the native parallel 240-second session')
if early_boot_timing:
    if (run_seconds!=120 or not all(k in sys.argv for k in
            ('--native-stock-weston','--load-linux','--load-android','--native-parallel-tcg','--symbol-directory'))):
        raise ValueError('Early boot timing requires the bounded native parallel startup probe')
if early_boot_timing or android_startup_ring:
    boot_ring=runpy.run_path(str(Path(__file__).with_name('early-boot-ring.py')))
    boot_symbol_dir=Path(sys.argv[sys.argv.index('--symbol-directory')+1])


def observe_early_ring(qmp, now):
    anchor=state['first_board_sample']
    elapsed=now-anchor
    if not any(elapsed>=offset and offset not in state.get('boot_ring_done',()) for offset in (9,27,54,90)):
        return
    due={offset for offset in (9,27,54,90) if elapsed>=offset}
    state.setdefault('boot_ring_done',set()).update(due)
    try:
        if 'boot_ring_config' not in state:
            state['boot_ring_config']=boot_ring['setup'](out,boot_symbol_dir)
        row=boot_ring['capture'](qmp,orig,out,state['boot_ring_config'],now,120)
    except (KeyError,OSError,RuntimeError,ValueError) as exc:
        row=dict(host_monotonic=now,guests={},rejected={'observer':str(exc)})
    row['seconds_from_first_board_sample']=round(elapsed,3)
    with (out/'early-boot-milestones.jsonl').open('a',encoding='utf8') as f:
        f.write(json.dumps(row,separators=(',',':'))+'\n')


def observe_android_startup(qmp, now):
    elapsed=now-state['first_board_sample']
    targets=(60,96,132,168,204,225)
    done=state.setdefault('android_startup_samples',set())
    due={offset for offset in targets if elapsed>=offset and offset not in done}
    if not due:
        return
    done.update(due)
    try:
        if 'android_startup_ring_config' not in state:
            state['android_startup_ring_config']=boot_ring['setup'](out,boot_symbol_dir,guests=('android',))
        row=boot_ring['capture'](qmp,orig,out,state['android_startup_ring_config'],now,240,
                                events=boot_ring['ANDROID_STARTUP_EVENT'])
    except (KeyError,OSError,RuntimeError,ValueError) as exc:
        row=dict(host_monotonic=now,guests={},rejected={'observer':str(exc)})
    row['planned_offsets_reached']=sorted(due)
    row['seconds_from_first_board_sample']=round(elapsed,3)
    with (out/'native-android-startup-rings.jsonl').open('a',encoding='utf8') as f:
        f.write(json.dumps(row,separators=(',',':'))+'\n')


def observe_center_event(qmp,now):
    if not center_events.probe_due(now) or state.get('center_event_disabled'):
        return
    if not state.get('center_event_seen'):
        vm.save(out/'native-center-event-policy.json',dict(
            source='original-ap-qom-history',probe_interval_seconds=.8,extra_capture_limit=8,
            scopes=['active-sysmmu-rows','gpu-content-sha256'],first_probe_monotonic=now))
        state['center_event_seen']=True
    try:
        raw=orig(qmp,'qom-get',center_history_property)
        candidate=center_events.claim(raw,now,state.get('center_last',0))
    except (OSError,RuntimeError,ValueError) as exc:
        state['center_event_disabled']=True
        vm.save(out/'native-center-event-error.json',dict(error=str(exc)))
        return
    if candidate is None:
        return
    state['center_last']=now
    try:
        sample_center(qmp,out,drm_symbols,center_only=True)
    except (KeyError,OSError,RuntimeError,ValueError) as exc:
        vm.save(out/'native-center-frame.json',dict(status='not_verified',reason=str(exc)))
    try:
        proof=json.loads((out/'native-center-gpu-correlation.json').read_bytes())
    except (OSError,ValueError):
        proof=None
    matched=center_events.confirm(candidate,proof,now)
    with (out/'native-center-events.jsonl').open('a',encoding='utf8') as log:
        log.write(json.dumps(dict(candidate,matched_active_rows=matched,host_monotonic=now),
                             separators=(',',':'))+'\n')

def spi6_dma_program(qmp, channel_status):

    def words(text, width):
        return [int(word,16) for line in text.splitlines() if ':' in line
                for word in re.findall(rf'\b0x[0-9a-f]{{{width}}}\b',line.split(':',1)[1])]

    def xp(kind, address, length, width):
        result=orig(qmp,'human-monitor-command',{'command-line':f'xp /{length}{kind} {address:#x}'})
        data=words(result,width)
        if len(data)!=length:
            raise ValueError('Incomplete original PDMA readback')
        return data

    pairs=words(channel_status,8)
    if len(pairs)!=16:
        raise ValueError('Unexpected original PDMA channel registers')
    for channel,pc in enumerate(pairs[1::2]):
                                                                           
        start=pc&~0x1ff
        if not 0xb8000000<=start<=0xd0000000-64 or not 0<pc-start<64:
            continue
        src,dst,ccr=xp('wx',0x1b2e0400+channel*0x20,3,8)
        if dst!=0x10900018 and src!=0x1090001c:
            continue
        if xp('wx',0x1b2e4500,1,8)!=[0]:
            continue
        program=bytes(xp('bx',start,64,2))
        peripheral=bytes.fromhex('bc0218009010' if dst==0x10900018 else 'bc001c009010')
        if not program.startswith(b'\xbc\x01') or peripheral not in program[:32]:
            continue
        if struct.unpack_from('<I',program,2)[0]!=ccr:
            continue
                                                                            
        data=program[:pc-start+1]
        return dict(channel=channel,pc=hex(pc),program_base=hex(start),ccr=hex(ccr),
                    src_burst=((ccr>>4)&15)+1, dst_burst=((ccr>>18)&15)+1,
                    src_width=1<<((ccr>>1)&7), dst_width=1<<((ccr>>15)&7),
                    program_hex=data.hex(),host_monotonic=time.monotonic(),
                    scope='Original VM3-generated PL330 instructions, not SPI payload')

def observe(qmp,command,args=None):
    data=orig(qmp,command,args)
    if command!='query-status' or not data.get('running'):
        return data
    now=time.monotonic()
    coarse_due=(now-state['last']>=9 and
                state['rows']<(max(135,run_seconds//9+2) if sample_center else 68))
    event_due=(center_events is not None and 'first_board_sample' in state and
               75<=now-state['first_board_sample']<run_seconds-3 and not state.get('center_event_disabled') and
               now>=center_events.next_probe and center_events.capture_count<8)
    qnx_due=(qnx_live_log and 'qnx_pixel_log' in state and
             now>=state.get('qnx_plane_next',0))
    if not coarse_due and not event_due and not qnx_due:
        return data
                                                                      
                                                                          
    if 'ap_port' not in state:
        if not (out/'linux-ivi-data.json').is_file() or not (out/'ufs-runtime-uic-trace.json').is_file():
            return data
        state['ap_port']=int(json.loads((out/'launch.json').read_bytes())['qmp_port'])
    if qmp.socket.getpeername()!=('127.0.0.1',state['ap_port']):
        return data
    state['ap_qmp']=qmp
    if event_due:
        observe_center_event(qmp,now)
    if qnx_due:
        interval=9.0 if prepare_linux_seed else 2.0
        state['qnx_plane_next']=now+interval
        try:
            path=qnx_display.DECON_PATH
            execute=(lambda op,params=None: orig(qmp,op,params))
            if 'qnx_plane_properties' not in state:
                state['qnx_plane_properties']={r['name'] for r in execute('qom-list',{'path':path})}
            props=state['qnx_plane_properties']
            routes=qnx_display.confirm_routes(state['qnx_pixel_log'])
            counters={name:execute('qom-get',{'path':path,'property':key}) for name in routes
                      if (key:=f'scanned-frames-{qnx_display.ROLES[name][2]}') in props}
            if any(counters.values()):
                qnx_display.capture_live(qmp,out,state['qnx_pixel_log'],qnx_screen_candidates,
                                         call=execute,properties=props)
                state['qnx_plane_samples']=state.get('qnx_plane_samples',0)+1
                if state['qnx_plane_samples']==1 or state['qnx_plane_samples']%10==0:
                    vm.save(out/'native-qnx-live-cadence.json',dict(samples=state['qnx_plane_samples'],
                        last_monotonic=now,interval_s=interval,scanned=counters))
        except (OSError,RuntimeError,ValueError) as exc:
            state['qnx_plane_next']=now+9.0
            vm.save(out/'qnx-plane-live-error.json',dict(error=str(exc)))
    if not coarse_due:
        return data
    if center_lean and 'first_board_sample' not in state:
                                                                             
        vm.save(out/'native-center-sampling-policy.json', dict(
            mode='center-lean', full_board_period_seconds=45, focus_period_seconds=9,
            center_period_seconds=9, frame_event_policy_unchanged=True))
    state.setdefault('first_board_sample',now)
    state['last']=now
    state['rows']+=1
                                                                        
                                                                  
    row=dict(seq=state['rows'],host_monotonic=now)
    full_due = not center_lean or now-state.get('last_full', -float('inf')) >= 45
    row['full_board_sample']=full_due
    if full_due:
        state['last_full']=now
        regs=orig(qmp,'human-monitor-command',{'command-line':'info registers -a'})
        disk=next((n['stats'] for n in orig(qmp,'query-blockstats')
                   if n.get('node-name')=='ufs-vh1-private-lun0'),None)
        ufs={k:orig(qmp,'qom-get',{'path':vhpath,'property':k})
             for k in ('irq-level','irq-assertions','dma-writes','dma-rejected')}
        pic=orig(qmp,'human-monitor-command',{'command-line':'info pic'})
        pdma={label:orig(qmp,'human-monitor-command',{'command-line':cmd})
              for label,cmd in {'channels':'xp /16wx 0x1b2e0100',
                                                                                
                                                                                  
                                'faults':'xp /3wx 0x1b2e0030',
                                'channel_faults':'xp /8wx 0x1b2e0040',
                                'status':'xp /5wx 0x1b2e0020'}.items()}
        if not state.get('spi6_dma_done'):
            try:
                witness=spi6_dma_program(qmp,pdma['channels'])
                if witness:
                    vm.save(out/'spi6-original-dma-program.json',witness)
                    state['spi6_dma_done']=True
            except (OSError,RuntimeError,ValueError) as exc:
                vm.save(out/'spi6-original-dma-error.json',dict(error=str(exc)))
        if (out/'mcu-spi6-original-contract.json').is_file():
            try:
                import sysreg_platform
                mcu=sysreg_platform.mcu_spi6_runtime(qmp)
                if mcu!=state.get('mcu_spi6'):
                    state['mcu_spi6']=mcu
                    with (out/'mcu-spi6-live.jsonl').open('a',encoding='utf8') as f:
                        f.write(json.dumps(dict(monotonic=now,**mcu))+'\n')
            except (OSError,RuntimeError,ValueError) as exc:
                vm.save(out/'mcu-spi6-live-error.json',dict(error=str(exc)))
        if 'gic_path' not in state:
            candidates=[]
            for prefix in ('/machine','/machine/unattached'):
                for item in orig(qmp,'qom-list',{'path':prefix}):
                    if item['type']!='child<arm_gic>':
                        continue
                    path=prefix+'/'+item['name']
                    if orig(qmp,'qom-get',{'path':path,'property':'num-irq'})>260:
                        candidates.append(path)
            if len(candidates)!=1:
                raise ValueError('Cannot identify unique native AP GIC: '+repr(candidates))
            state['gic_path']=candidates[0]
        gic=json.loads(orig(qmp,'qom-get',{'path':state['gic_path'],'property':'x-auto9-spi228-state'}))
        from ufs_mailbox_platform import runtime as mb_runtime
        mail=mb_runtime(qmp)
        row.update(mailbox=mail,ufs=ufs,blockstats=disk,registers=regs,pic=pic,pdma=pdma,gic=gic)
                                                                           
                                                                           
    launch=json.loads((out/'launch.json').read_bytes())
    if launch['input_sha256']=='49250f278f354865146c05355575312caa8dac24274f7d46e680aa48f1c889ff':
        focus=orig(qmp,'human-monitor-command',{'command-line':'xp /2bx 0x80196038'})
        match=re.search(r':\s+(0x[\da-f]{2})\s+(0x[\da-f]{2})',focus)
        if match:
            row['vlm_focus_raw_hex']=bytes(int(x,16) for x in match.groups()).hex()
    with (out/'live-vh1.jsonl').open('a',encoding='utf8') as f:
        f.write(json.dumps(row)+'\n')
    if early_boot_timing:
        observe_early_ring(qmp,now)
    if android_startup_ring and now-state['first_board_sample']<run_seconds-3:
        observe_android_startup(qmp,now)
    if isolated_linux_uart and now-state.get('uart4_regs_last',state['first_board_sample'])>=15:
        state['uart4_regs_last']=now
        sample={'host_monotonic':now,'scope':'Read-only UART4 status/control; never UERSTAT or URXH'}
        for name,address,n in [('config',0x10380000,4),('status',0x10380010,1),
                               ('fifo',0x10380018,1),('irq',0x10380030,3)]:
            sample[name]=orig(qmp,'human-monitor-command',{'command-line':f'xp /{n}wx {address:#x}'})
        with (out/'uart4-registers-live.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(sample)+'\n')
    if native_rx_watch and now-state.get('native_rx_last',state['first_board_sample'])>=65:
        state['native_rx_last']=now
        try:
            with TemporaryDirectory(prefix='vlm-rx-',dir=out) as directory:
                snapshot=Path(directory)/'ram.bin'
                orig(qmp,'pmemsave',dict(val=uart.VLM_RAM,size=uart.VLM_RX_CAPTURE,filename=str(snapshot)))
                raw=snapshot.read_bytes()
                rx=uart.native_rx_snapshot(raw)
                tx_code=runpy.run_path(str(Path(__file__).with_name('original-console-tx.py')))
                tx_code['verify_original']()
                rx['tx']=tx_code['decode'](raw)
            with (out/'vlm-native-rx-live.jsonl').open('a',encoding='utf8') as f:
                f.write(json.dumps(rx)+'\n')
        except (OSError,RuntimeError,ValueError) as exc:
            vm.save(out/'vlm-native-rx-error.json',dict(error=str(exc)))
    if qnx_live_log and now-state.get('qnx_log_last',state['first_board_sample'])>=(30 if short_validation else 75):
        state['qnx_log_last']=now
        try:
                                                                             
                                                               
            with TemporaryDirectory(prefix='qnx-log-',dir=out) as directory:
                area=Path(directory)/'log.bin'
                orig(qmp,'pmemsave',dict(val=cal.QNX_LOG_BASE,size=cal.QNX_LOG_BYTES,filename=str(area)))
                payload,_=cal.decode_qnx_log_area(area.read_bytes())
            if payload:
                path=out/'qnx-root-live-log.txt'
                staged=path.with_suffix('.tmp')
                log=payload.decode('utf-8',errors='backslashreplace')
                staged.write_text(log,encoding='utf-8',newline='')
                staged.replace(path)
                state['qnx_pixel_log']=log
                if qnx_display_profile:
                    identity=qnx_display_identity.readback(qmp)
                    values=re.findall(r'type_abt\s*=\s*(\d+)\s+type_iid\s*=\s*(\d+)\s*,?\s*type_hud\s*=\s*(\d+)',log)
                    vm.save(out/'qnx-display-identity-live.json',dict(identity, captured_monotonic=now,
                        serializer_values=values, serializer_fallback='invalid display id' in log))
            if '--qnx-bts' in sys.argv:
                import qnx_bts_platform as bts
                import qnx_dsi_platform as dsi
                names=('running-2','frame-ticks-2','dpus0-window-0-commits',
                       'dpus0-window-7-commits')
                props={r['name'] for r in orig(qmp,'qom-list',{'path':dsi.DECON_PATH})}
                names+=tuple(name for i in range(3) if (name:=f'crc-empty-reads-{i}') in props)
                live={key:orig(qmp,'qom-get',{'path':dsi.DECON_PATH,'property':key}) for key in names}
                vm.save(out/'qnx-devices-live.json',dict(monotonic=now,bts=bts.runtime(qmp),
                    trex=bts.trex_runtime(qmp),qos=bts.qos_runtime(qmp),decon=live))
        except (OSError,RuntimeError,ValueError) as exc:
            vm.save(out/'qnx-root-live-error.json',dict(error=str(exc)))
    if '--linux-decon' in sys.argv and now-state.get('wod_last',state['first_board_sample'])>=27:
        state['wod_last']=now
        try:
            import decon_platform
            vm.save(out/'linux-wod-live.json',dict(captured_monotonic=now,
                                                  windows=decon_platform.linux_runtime(qmp)))
        except (OSError,RuntimeError,ValueError) as exc:
            vm.save(out/'linux-wod-live-error.json',dict(error=str(exc)))
                                                                            
                                                                            
    if 'ti983_attached' not in state:
        p=out/'ti983-virtual-profile.json'
        try:
            profile=json.loads(p.read_bytes()) if p.is_file() else {}
            state['ti983_attached']=isinstance(profile,dict) and profile.get('address')==12
        except (OSError,ValueError) as exc:
            vm.save(out/'ti983-live-error.json',dict(error=str(exc)))
    if state.get('ti983_attached') and not state.get('ti983_completed_exchange'):
        try:
            import ti983_platform
            ti=ti983_platform.runtime_state(qmp)
            state['ti983_completed_exchange']=bool(ti['register-writes'] and ti['register-reads'] and ti['apb-requests'])
            vm.save(out/'ti983-live.json',dict(captured_monotonic=now,state=ti,
                completed_exchange=state['ti983_completed_exchange'],
                scope='Actual emulated I2C17 endpoint activity on this original board; not FPD/video'))
        except (OSError,RuntimeError,ValueError) as exc:
            vm.save(out/'ti983-live-error.json',dict(error=str(exc)))
    if (sample_center and now-state['first_board_sample']>=(75 if short_validation else 90)
            and now-state.get('center_last',0)>=(18 if short_validation else 9)):
        state['center_last']=now
        try:
            sample_center(qmp,out,drm_symbols,ivi_snapshot=sample_ivi)
        except (KeyError,OSError,RuntimeError,ValueError) as exc:
            vm.save(out/'native-center-frame.json',dict(status='not_verified',reason=str(exc)))
    return data

vm.QMP.execute=observe
import android_init_trace
from linux_pid1_context import without_oem_autoreboot

keep_linux_on_failure='--keep-linux-on-failure' in sys.argv
if keep_linux_on_failure:
    sys.argv.remove('--keep-linux-on-failure')
linux_pixman='--linux-pixman' in sys.argv
stock_weston='--native-stock-weston' in sys.argv
wayland_trace='--native-wayland-trace' in sys.argv
accelerated_linux_seed='--accelerated-linux-seed' in sys.argv
if accelerated_linux_seed:
    sys.argv.remove('--accelerated-linux-seed')
seed_time_ok=(600<run_seconds<=1200) or vlm_platform.accelerated_seed_window(run_seconds,
    prepare='--prepare-linux-seed' in sys.argv,accelerated=accelerated_linux_seed)
stock_seed_profile=('--prepare-linux-seed' in sys.argv and '--extended-observation' in sys.argv and
                    '--native-parallel-tcg' in sys.argv and stock_weston and seed_time_ok)
if accelerated_linux_seed and not (stock_seed_profile and run_seconds in (stock_profile_seconds,600)):
    raise ValueError('Accelerated Linux seed requires an exact 240- or 600-second stock preparation profile')
seed_no_display=os.environ.get('OEM_8704_SEED_NO_DISPLAY')=='1'
if seed_no_display and not (accelerated_linux_seed and run_seconds==stock_profile_seconds):
    raise ValueError('Seed display isolation is confined to the 240-second stock preparation')
seed_direct_uevents=os.environ.get('OEM_8704_SEED_DIRECT_UEVENTS')=='1'
if seed_direct_uevents and not (accelerated_linux_seed and run_seconds==stock_profile_seconds):
    raise ValueError('Targeted coldplug is confined to the 240-second stock preparation')
if stock_weston:
    sys.argv.remove('--native-stock-weston')
    if linux_pixman or not wayland_trace or not (0<run_seconds<=stock_profile_seconds or stock_seed_profile):
        raise ValueError('Stock Weston requires regular native scene observation without Pixman')
if wayland_trace:
    sys.argv.remove('--native-wayland-trace')
    if not (linux_pixman or stock_weston) or ('--native-hmi-start-grace' not in sys.argv and not stock_seed_profile):
        raise ValueError('Native Wayland trace requires the original early Weston and native HMI start grace')
weston_plan=None
weston_baseline=None
if linux_pixman or stock_weston:
    if linux_pixman:
        sys.argv.remove('--linux-pixman')
    import linux_weston_platform
    if '--ufs-lun0-image' not in sys.argv or '--linux-ivi-data' not in sys.argv:
        raise ValueError('Original early Weston requires the native VM3 private disk')
    layout=Path(sys.argv[sys.argv.index('--ufs-lun0-image')+1]).resolve(strict=True)
    weston_base=layout.with_name('ufs-lun0.raw')
    weston_baseline=linux_weston_platform.plan(weston_base)
    weston_plan=(linux_weston_platform.plan(weston_base,trace_protocol=True,pixman=not stock_weston)
                 if wayland_trace else weston_baseline)

getty_profile='--seed-native-getty-instance' in sys.argv
getty_modes={key for key in ('--stage-oem-run-getty','--stage-oem-run-getty-alias') if key in sys.argv}
local_linux_shell='--local-linux-shell' in sys.argv
shared_linux_console=os.environ.get('OEM_8704_SHARED_LINUX_CONSOLE')=='1'
isolated_linux_uart=os.environ.get('OEM_8704_ISOLATED_LINUX_UART')
if isolated_linux_uart is not None:
    if isolated_linux_uart!='4' or not local_linux_shell or shared_linux_console:
        raise ValueError('Isolated UART4 needs the original local shell and no shared route')
    import native_uart_console
    native_uart_console.configure_usi04()
local_android_shell='--local-android-shell' in sys.argv
if local_android_shell:
    import android_local_shell
    if not native_rx_watch or '--lk-android-normal' not in sys.argv:
        raise ValueError('Local VM4 console requires LK normal Android handoff and the loopback vLM UART')
    sys.argv.remove('--local-android-shell')
    original_android_parts=vm.android_parts

    def android_parts_with_console(boot, vendor_boot):
        kernel,generic,vendor=original_android_parts(boot, vendor_boot)
        meta=android_local_shell.stock_console_policy(generic,vendor)
        vm.save(out/android_local_shell.POLICY,meta)
        return kernel,generic,vendor

    vm.android_parts=android_parts_with_console
if len(getty_modes)+getty_profile+local_linux_shell>1:
    raise ValueError('Select one original getty staging path')
if local_linux_shell:
    import linux_local_shell
    if (not native_rx_watch or '--linux-factory-bank' not in sys.argv or
            not 1024 <= int(sys.argv[sys.argv.index('--linux-native-console-port')+1]) <= 65535):
        raise ValueError('Local VM3 shell requires the private Linux factory bank and loopback console')
    if shared_linux_console:
        sys.argv.remove('--local-linux-shell')
    else:
        sys.argv[sys.argv.index('--local-linux-shell')]='--linux-direct-console'
    linux_local_shell.validate_factory(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
for mode in getty_modes:
    sys.argv.remove(mode)
run_getty=bool(getty_modes)
alias_getty='--stage-oem-run-getty-alias' in getty_modes
if getty_profile:
    import runpy
    import linux_data_overlay
    sys.argv.remove('--seed-native-getty-instance')
    getty=runpy.run_path(str(Path(__file__).with_name('seed-oem-getty.py')))
    original_create=linux_data_overlay.create

    def with_oem_getty(profile, base, media, output, previous=None, **options):
        return getty['install'](base, media, output, original_create(profile,base,media,output,previous,**options))

    linux_data_overlay.create=with_oem_getty

native_nav_data='--seed-native-nav-data' in sys.argv
prepare_linux_seed='--prepare-linux-seed' in sys.argv
prioritize_post_init='--prioritize-oem-post-init' in sys.argv
hmi_start_grace='--native-hmi-start-grace' in sys.argv
hmi_interpreter='--native-hmi-interpreter' in sys.argv
hmi_flight_recording='--native-hmi-flight-recording' in sys.argv
hmi_vm_log='--native-hmi-vm-log' in sys.argv
no_hmi_vm_log=os.environ.get('OEM_8704_NO_HMI_VM_LOG')=='1'
hmi_thread_dump=os.environ.get('OEM_8704_HMI_THREAD_DUMP')=='1'
hmi_queue_read=os.environ.get('OEM_8704_HMI_QUEUE_READ')=='1'
persistence_priority=os.environ.get('OEM_8704_PERSISTENCE_PRIORITY')=='1'
persistence_before_hmi=os.environ.get('OEM_8704_PERSISTENCE_BEFORE_HMI')=='1'
parallel_persistence_sysinit=os.environ.get('OEM_8704_PERSISTENCE_PARALLEL_SYSINIT')=='1'
persistence_start_grace=os.environ.get('OEM_8704_PERSISTENCE_START_GRACE')=='1'
post_init_after_tmpfiles=os.environ.get('OEM_8704_POST_INIT_AFTER_TMPFILES')=='1'
hmi_after_post_init=os.environ.get('OEM_8704_HMI_AFTER_POST_INIT')=='1'
post_init_nice_raw=os.environ.get('OEM_8704_POST_INIT_NICE','')
if post_init_nice_raw not in ('','-5','-10','-15','-19','-20'):
    raise ValueError('Unsupported original post-init Nice value')
post_init_nice=int(post_init_nice_raw) if post_init_nice_raw else None
verified_linux_seed=os.environ.get('OEM_8704_VERIFIED_LINUX_SEED')=='1'
if shared_linux_console and not (local_linux_shell and no_hmi_vm_log and verified_linux_seed):
    raise ValueError('Shared local console needs the complete verified seed and no VM log')
if shared_linux_console:
    import native_uart_console
    shared_console_policy=native_uart_console.validate(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
hmi_data_source='--native-hmi-userdata-from' in sys.argv
persistence_start_grace_seconds=None
defer_hmi_precompile=os.environ.get('OEM_8704_HMI_DEFER_PRECOMPILE')=='1'
lsc_timeout_raw=os.environ.get('OEM_8704_HMI_LSC_TIMEOUT_MS','')
lsc_timeout_ms=None
if verified_linux_seed:
    import linux_early_uart
    if (not stock_weston or not native_nav_data or not prioritize_post_init or prepare_linux_seed or
            not hmi_start_grace or hmi_after_post_init or hmi_data_source or '--linux-ivi-parent' not in sys.argv or
            run_seconds!=stock_profile_seconds):
        raise ValueError('Verified Linux seed requires the paired source and stock 240-second run')
    source=Path(sys.argv[sys.argv.index('--linux-ivi-parent')+1])
    linux_early_uart.verified_clean_seed(source)
if no_hmi_vm_log and (not stock_weston or hmi_vm_log or hmi_flight_recording or hmi_interpreter or
                      not hmi_start_grace or not (hmi_data_source or verified_linux_seed) or not lsc_timeout_raw):
    raise ValueError('No HMI VM log is limited to the stock HMI data/timing comparison')
if lsc_timeout_raw:
    if (not lsc_timeout_raw.isascii() or not lsc_timeout_raw.isdecimal() or len(lsc_timeout_raw)>5
            or not 5000<=int(lsc_timeout_raw)<=90000 or not parallel_persistence_sysinit
            or not (hmi_vm_log or no_hmi_vm_log) or not hmi_queue_read or defer_hmi_precompile or hmi_thread_dump):
        raise ValueError('OEM LSC timer comparison requires the 240s stock JVM/parallel persistence profile')
    lsc_timeout_ms=int(lsc_timeout_raw)
stage_persistence_priority=None
stage_hmi_precompile=None
stage_hmi_compact=None
if persistence_before_hmi and not persistence_priority:
    raise ValueError('Persistence-before-HMI requires the original early persistence comparison')
if parallel_persistence_sysinit and (not persistence_priority or persistence_before_hmi
                                     or '--linux-systemd-info' not in sys.argv):
    raise ValueError('Parallel persistence requires the routine info comparison without HMI ordering changes')
if persistence_start_grace and (not parallel_persistence_sysinit or lsc_timeout_ms is None
                                or not stock_weston or not prioritize_post_init):
    raise ValueError('Persistence start grace requires the original stock center comparison')
if post_init_after_tmpfiles and not (persistence_start_grace and os.environ.get('OEM_8704_TRACE_BACKUP_SETTLE')=='1'):
    raise ValueError('Early original post-init requires the verified trace/persistence preset')
if hmi_after_post_init and not (post_init_after_tmpfiles and no_hmi_vm_log and stock_weston):
    raise ValueError('HMI ordering requires original early post-init and the stock no-XML profile')
if post_init_nice and not (hmi_after_post_init and persistence_priority and
                           os.environ.get('OEM_8704_SINGLE_EARLY_COLDPLUG_BATCH')=='1'):
    raise ValueError('Post-init scheduling comparison requires single-batch HMI-after-post-init')
if defer_hmi_precompile and (not (persistence_before_hmi or parallel_persistence_sysinit)
                            or not hmi_vm_log or hmi_thread_dump
                            or '--linux-systemd-info' not in sys.argv):
    raise ValueError('Deferred HMI precompile requires a routine persistence timing comparison and original VM log')
if persistence_priority:
    import runpy
    if (not prioritize_post_init or not hmi_start_grace or not wayland_trace or not hmi_queue_read
            or run_seconds!=stock_profile_seconds or ('--extended-observation' in sys.argv and not stock_live)):
        raise ValueError('Persistence priority requires the routine original center comparison')
    priority=runpy.run_path(str(Path(__file__).with_name('native-persistence-priority.py')))
    priority['validate'](vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'),
                         start_grace=persistence_start_grace,post_init_after_tmpfiles=post_init_after_tmpfiles)
    if persistence_start_grace:
        persistence_start_grace_seconds=priority['PERSISTENCE_START_GRACE_SECONDS']
    stage_persistence_priority=priority['stage']
    stage_hmi_precompile=priority['defer_hmi_precompile'] if defer_hmi_precompile else None
    stage_hmi_compact=priority['compact_shared_systemd_root'] if lsc_timeout_ms is not None else None
if hmi_queue_read and (not wayland_trace or run_seconds!=stock_profile_seconds):
    raise ValueError('OEM queue inspection requires the routine native center preset')
if hmi_thread_dump and not hmi_vm_log:
    raise ValueError('One-shot OEM thread dump requires the original HotSpot VM log')
if hmi_vm_log:
    sys.argv.remove('--native-hmi-vm-log')
    if (not hmi_start_grace or hmi_interpreter or hmi_flight_recording or not (hmi_data_source or verified_linux_seed)
            or run_seconds!=stock_profile_seconds):
        raise ValueError('HMI VM log requires the stock JVM and the routine native HMI data comparison')
if hmi_flight_recording:
    sys.argv.remove('--native-hmi-flight-recording')
    if (not hmi_start_grace or hmi_interpreter or '--native-hmi-userdata-from' not in sys.argv
            or run_seconds!=stock_profile_seconds):
        raise ValueError('HMI startup recording requires original JVM mode and the routine native HMI data comparison')
if hmi_interpreter:
    sys.argv.remove('--native-hmi-interpreter')
    if not hmi_start_grace:
        raise ValueError('HMI interpreter comparison requires HMI start grace')
if hmi_start_grace:
    import linux_early_uart
    sys.argv.remove('--native-hmi-start-grace')
    if (not prioritize_post_init or not local_linux_shell or run_seconds>stock_profile_seconds
            or ('--extended-observation' in sys.argv and not stock_live)):
        raise ValueError('HMI start grace requires the normal native post-init comparison and original Linux UART')
    linux_early_uart.validate_hmi_start_grace(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
    if hmi_after_post_init:
        linux_early_uart.validate_hmi_post_init_order(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
    if hmi_flight_recording or hmi_vm_log or no_hmi_vm_log:
        linux_early_uart.validate_hmi_recording(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'),
                                               vm_log=hmi_vm_log or no_hmi_vm_log)
if prioritize_post_init:
    import linux_early_uart
    sys.argv.remove('--prioritize-oem-post-init')
    if (prepare_linux_seed or not native_nav_data or run_seconds>stock_profile_seconds or '--linux-ivi-parent' not in sys.argv):
        raise ValueError('OEM post-init priority requires an explicit native data seed and a normal run')
    linux_early_uart.validate_post_init_priority(sys.argv[sys.argv.index('--linux-ivi-parent')+1],
                                                 clean_seed=verified_linux_seed)
    linux_early_uart.validate_clean_seed_policy(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
if prepare_linux_seed:
    import linux_early_uart
    sys.argv.remove('--prepare-linux-seed')
    if (not native_nav_data or not seed_time_ok or local_linux_shell or run_getty or getty_profile
            or not all(k in sys.argv for k in ('--extended-observation','--linux-factory-bank','--linux-ivi-parent'))):
        raise ValueError('Clean seed requires a bounded native clone, /navi_data, and no alternate Linux shell')
    linux_early_uart.validate_clean_seed_policy(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'),
                                               accelerated=accelerated_linux_seed,no_display=seed_no_display)
    if stock_seed_profile:
        linux_early_uart.validate_hmi_start_grace(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
        linux_early_uart.validate_hmi_post_init_order(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
        linux_early_uart.validate_post_init_priority(sys.argv[sys.argv.index('--linux-ivi-parent')+1])
        vlm_platform.probe.oem_seed_preparation=True
        if accelerated_linux_seed:
            linux_early_uart.validate_trace_backup_settle(vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
            vlm_platform.probe.oem_accelerated_seed=True
trace_backup_settle=os.environ.get('OEM_8704_TRACE_BACKUP_SETTLE')=='1'
early_nav_coldplug=os.environ.get('OEM_8704_EARLY_NAV_COLDPLUG')=='1'
early_nav_core_coldplug=os.environ.get('OEM_8704_EARLY_NAV_CORE_COLDPLUG')=='1'
early_swup_coldplug=os.environ.get('OEM_8704_EARLY_SWUP_COLDPLUG')=='1'
early_radio_coldplug=os.environ.get('OEM_8704_EARLY_RADIO_COLDPLUG')=='1'
single_early_coldplug_batch=os.environ.get('OEM_8704_SINGLE_EARLY_COLDPLUG_BATCH')=='1'
if single_early_coldplug_batch and not (trace_backup_settle and early_nav_coldplug and early_nav_core_coldplug):
    raise ValueError('Single early coldplug batch requires trace backup settle and both early navigation options')
if (early_nav_coldplug or early_nav_core_coldplug) and not trace_backup_settle:
    raise ValueError('Early navigation coldplug requires the bounded trace backup settle profile')
if early_swup_coldplug and (not trace_backup_settle or single_early_coldplug_batch):
    raise ValueError('Early /swup coldplug requires bounded separate trigger batches')
if early_radio_coldplug and (not trace_backup_settle or single_early_coldplug_batch):
    raise ValueError('Early /radio/osdb coldplug requires bounded separate trigger batches')
if trace_backup_settle and not (native_nav_data and stock_weston and persistence_start_grace
                                and stage_hmi_compact and run_seconds==stock_profile_seconds):
    raise ValueError('Trace backup settle requires the stock 240s center preset and persistence start grace')
native_prior_slots=()
if native_nav_data:
    import runpy
    import linux_early_uart
    import linux_data_overlay
    sys.argv.remove('--seed-native-nav-data')
    if '--linux-ivi-parent' in sys.argv:
        native_prior_slots=linux_early_uart.prior_local_fs_slots(sys.argv[sys.argv.index('--linux-ivi-parent')+1])
    if trace_backup_settle:
        linux_early_uart.trace_backup_settle_units(native_prior_slots,early_nav=early_nav_coldplug,
                                                   early_nav_core=early_nav_core_coldplug,
                                                   single_batch=single_early_coldplug_batch,
                                                   early_swup=early_swup_coldplug,early_radio=early_radio_coldplug)
        linux_early_uart.validate_trace_backup_settle(
            vlm_platform.factory_file('ivi/icas3cn-gp-image-euto-v9.ext4'))
    original_create_nav_data=linux_data_overlay.create
    seed_nav_data=runpy.run_path(str(Path(__file__).with_name('seed-oem-nav-data.py')))['install']

    def with_nav_data(profile,base,media,output,previous=None,**options):
        overlay=original_create_nav_data(profile,base,media,output,previous,**options)
        return seed_nav_data(base,media,output,overlay)

    linux_data_overlay.create=with_nav_data

if weston_plan:
    import linux_data_overlay
    original_create_weston=linux_data_overlay.create

    def with_early_weston(profile,base,media,output,previous=None):
        inherited_plan=weston_baseline
        if previous is not None:
            prior=json.loads((Path(previous)/'linux-ivi-data.json').read_bytes())
            recorded=prior.get('early_weston_verity')
            if (isinstance(recorded,dict) and recorded.get('exact_leaf_readback') is True
                    and all(recorded.get(k)==v for k,v in weston_plan.evidence().items())):
                inherited_plan=weston_plan
        overlay=original_create_weston(profile,base,media,output,previous,weston_plan=inherited_plan)
        return linux_weston_platform.install(weston_plan,base,media,output,overlay,inherited_plan=inherited_plan)

    linux_data_overlay.create=with_early_weston

if '--native-hmi-userdata-from' in sys.argv:
    import linux_data_overlay
    import linux_hmi_userdata
    flag=sys.argv.index('--native-hmi-userdata-from')
    if not hmi_start_grace or flag+1>=len(sys.argv) or not native_nav_data:
        raise ValueError('Native HMI userdata requires original HMI start grace and /navi_data')
    hmi_userdata_source=Path(sys.argv[flag+1]).resolve(strict=True)
    del sys.argv[flag:flag+2]
    original_create_hmi_userdata=linux_data_overlay.create

    def with_hmi_userdata(profile,base,media,output,previous=None,**options):
        overlay=original_create_hmi_userdata(profile,base,media,output,previous,**options)
        proof=linux_hmi_userdata.merge(hmi_userdata_source,base,media,output,overlay,
                                       flight_recording=hmi_flight_recording,vm_log=hmi_vm_log)
        return dict(overlay,hmi_userdata_seed=proof)

    linux_data_overlay.create=with_hmi_userdata

parent = android_init_trace.boot_dtb
quiet_linux_initcalls=os.environ.get('OEM_8704_QUIET_LINUX_INITCALLS')=='1'
if quiet_linux_initcalls and not (run_seconds==stock_profile_seconds and stock_weston and
                                  (not prepare_linux_seed or accelerated_linux_seed)):
    raise ValueError('Quiet Linux initcalls requires a 240-second stock-Weston run or accelerated seed rehearsal')
systemd_info_only='--linux-systemd-info' in sys.argv
if systemd_info_only:
    sys.argv.remove('--linux-systemd-info')
systemd_level='info' if systemd_info_only else 'debug'
tokens = ('SYSTEMD_LOG_LEVEL='+systemd_level, 'SYSTEMD_LOG_TARGET=kmsg',
          'systemd.log_level='+systemd_level, 'systemd.log_target=kmsg')

def trace_bootargs(original):
    data, meta = parent(original)
    nodes = vlm_platform.parse_dtb(data)
    before = nodes['/chosen']['bootargs'][:-1].decode('ascii')
    if 'root=/dev/dm-0 ' not in before or 'init=/sbin/init ' not in before:
        return data, meta
    weston_policy=weston_plan.evidence() if weston_plan else None
    if any(w.split('=',1)[0] in {t.split('=',1)[0] for t in tokens} for w in before.split()):
        raise ValueError('Original PID 1 logging parameters already present')
    if before.count('log_buf_len=512K ') != 1:
        raise ValueError('Original IVI printk buffer option changed')
    logging_tokens=tokens[2:] if stock_seed_profile else tokens
    after = before.replace('log_buf_len=512K ', 'log_buf_len=2M ', 1) + ' ' + ' '.join(logging_tokens)
    if quiet_linux_initcalls:
        token=' '+android_init_trace.TOKEN
        if after.count(token+' ')!=1:
            raise ValueError('Expected the injected Linux initcall tracing token exactly once')
        after=after.replace(token+' ',' ',1)
    if keep_linux_on_failure:
        after=without_oem_autoreboot(after)
    if weston_plan:
        after=linux_weston_platform.bootargs(after,weston_plan)
    if getty_profile:
        if any(w.startswith('SYSTEMD_UNIT_PATH=') for w in before.split()):
            raise ValueError('OEM unit search path already overridden')
        after+=' '+getty['UNIT_ENV']
    if run_getty or local_linux_shell or native_nav_data or prepare_linux_seed or prioritize_post_init:
        if ' -- ' in after or 'boot_cpus=' in after or after.count('init=/sbin/init')!=1:
            raise ValueError('Expected unmodified OEM Linux argument layout')
                                                                       
                                                                          
                                                                           
                                                                            
                                                                             
                                                                          
        script=("umask 022; if /bin/mkdir -p /run/systemd/system "
                "&& /bin/cat /lib/systemd/system/serial-getty@.service "
                "> /run/systemd/system/serial-getty@ttySAC0.service; then "
                "echo '<5>OEM run overlay: exact getty staged on /run; handing off to original init' >/dev/kmsg; "
                "else echo '<3>OEM run overlay: staging failed' >/dev/kmsg; fi; exec /sbin/init")
        if alias_getty:
                                                                            
                                                                              
                                                                         
            script=("umask 022; p=/run/systemd/system; d=$p/getty.target.wants; "
                    "if /bin/mkdir -p $d && sed -e 's/%I/ttySAC0/g' -e 's/%i/ttySAC0/g' "
                    "/lib/systemd/system/serial-getty@.service >$p/oem-local-uart.service "
                    "&& ln -s ../oem-local-uart.service $d/oem-local-uart.service; then "
                    "echo '<5>OEM run overlay: staged non-template OEM getty' >/dev/kmsg; "
                    "else echo '<3>OEM run overlay: staging failed' >/dev/kmsg; fi; exec /sbin/init")
        if local_linux_shell:
            script=(native_uart_console.stage_script() if shared_linux_console else
                    linux_early_uart.stage_script(linux_local_shell.DIRECT_TTY,compact=True)
                    if (hmi_flight_recording or hmi_vm_log or no_hmi_vm_log) else linux_local_shell.stage_script(linux_local_shell.DIRECT_TTY))
        elif not run_getty:
            script='exec /sbin/init'
        if native_nav_data:
            from linux_early_uart import stage_coldplug_order
            script=stage_coldplug_order(native_prior_slots,compact=hmi_start_grace,
                                        trace_backup_settle=trace_backup_settle,early_nav=early_nav_coldplug,
                                        early_nav_core=early_nav_core_coldplug,
                                        single_batch=single_early_coldplug_batch,
                                        early_swup=early_swup_coldplug,early_radio=early_radio_coldplug)+script
        if prepare_linux_seed:
            script=linux_early_uart.stage_clean_seed()+script
        if stock_seed_profile:
            script=linux_early_uart.stage_stock_seed(native_prior_slots,accelerated=accelerated_linux_seed,
                                                    no_display=seed_no_display,direct_uevents=seed_direct_uevents)
        if prioritize_post_init:
            priority=linux_early_uart.stage_post_init_priority()
            script=(stage_persistence_priority(priority,before_hmi=persistence_before_hmi,
                                               parallel_sysinit=parallel_persistence_sysinit,
                                               start_grace=persistence_start_grace,
                                               post_init_after_tmpfiles=post_init_after_tmpfiles,
                                               post_init_nice=post_init_nice)
                    if stage_persistence_priority else priority)+script
        if hmi_start_grace:
            script=linux_early_uart.stage_hmi_start_grace(interpreter=hmi_interpreter,
                                                        flight_recording=hmi_flight_recording,vm_log=hmi_vm_log,
                                                        lsc_timeout_ms=lsc_timeout_ms,
                                                        after_post_init=hmi_after_post_init)+script
        if stage_hmi_precompile:
            script=stage_hmi_precompile(script,linux_early_uart.HMI_VM_LOG_PATH)
        if stage_hmi_compact:
            script=stage_hmi_compact(script)
        if shared_linux_console:
            script=native_uart_console.compact_staging(script)
        if hmi_thread_dump:
                                                                              
                                                                         
                                                                 
            script="(sleep 110;set -- $(pgrep -xu 724 JAVA);[ $# = 1 ]&&grep -qE '^SigCgt:.*[4567cdef]$' /proc/$1/status&&kill -3 $1)&"+script
                                                                            
                                                                         
        after=after.replace('init=/sbin/init','init=/bin/sh',1)+' boot_cpus=0,1,2 -- -c "'+script+'"'
        if len(after)>2048:
            raise ValueError('Early OEM init argument exceeds the original 2048-byte command line')
        if (hmi_start_grace or accelerated_linux_seed) and len(after)+len(' boot_cpus=0,1,2')+1>2048:
            raise ValueError('OEM startup staging would leave no room for original vLM boot_cpus')
    nodes['/chosen']['bootargs'] = after.encode('ascii') + bytes(1)
    diagnostic = vlm_platform.rebuild_fdt(data, nodes)
    if vlm_platform.parse_dtb(diagnostic) != nodes:
        raise ValueError('Diagnostic DT round-trip failed')
    return diagnostic, dict(meta, diagnostic_sha256=hashlib.sha256(diagnostic).hexdigest(),
                            bootargs_after=after, logging_tokens=list(logging_tokens),
                            boot_policy_changed=keep_linux_on_failure or bool(weston_plan) or local_linux_shell or native_nav_data or prepare_linux_seed or prioritize_post_init,
                            oem_prepare_linux_seed=prepare_linux_seed,
                            oem_stock_seed_preparation=stock_seed_profile,
                            oem_accelerated_linux_seed=accelerated_linux_seed,
                            oem_seed_no_display=seed_no_display,
                            oem_seed_direct_uevents=seed_direct_uevents,
                            oem_prioritize_post_init=prioritize_post_init,
                            oem_persistence_priority=persistence_priority,
                            oem_persistence_before_hmi=persistence_before_hmi,
                            oem_persistence_parallel_sysinit=parallel_persistence_sysinit,
                            oem_persistence_start_grace_seconds=persistence_start_grace_seconds,
                            oem_post_init_after_tmpfiles=post_init_after_tmpfiles,
                            oem_quiet_linux_initcalls=quiet_linux_initcalls,
                            oem_hmi_precompile_deferred=defer_hmi_precompile,
                            oem_hmi_lsc_timeout_ms=lsc_timeout_ms,
                            oem_hmi_after_post_init=hmi_after_post_init,
                            oem_post_init_nice=-20 if accelerated_linux_seed else post_init_nice,
                            oem_verified_linux_seed=verified_linux_seed,
                            oem_hmi_start_grace_seconds=(linux_early_uart.HMI_START_GRACE_SECONDS if hmi_start_grace else None),
                            oem_hmi_interpreter=hmi_interpreter,
                            oem_hmi_flight_recording=(dict(path=linux_early_uart.HMI_RECORDING_PATH,
                                                         guest_duration=linux_early_uart.HMI_RECORDING_SECONDS)
                                                      if hmi_flight_recording else None),
                            oem_hmi_vm_log=(linux_early_uart.HMI_VM_LOG_PATH if hmi_vm_log else None),
                            oem_hmi_thread_dump_guest_seconds=110 if hmi_thread_dump else None,
                            oem_wayland_trace=wayland_trace,
                            original_udev_coldplug_after_ready=native_nav_data,
                            original_trace_backup_settle_seconds=(
                                linux_early_uart.TRACE_BACKUP_SETTLE_SEC if trace_backup_settle else None),
                            original_udev_prior_nav=20 in native_prior_slots,
                            original_udev_early_nav_coldplug=early_nav_coldplug,
                            original_udev_early_nav_core_coldplug=early_nav_core_coldplug,
                            original_udev_early_swup_coldplug=early_swup_coldplug,
                            original_udev_early_radio_coldplug=early_radio_coldplug,
                            original_udev_single_early_batch=single_early_coldplug_batch,
                            original_udev_prior_slots=list(native_prior_slots),
                            linux_systemd_info_only=systemd_info_only,
                            oem_automatic_reboot='disabled' if keep_linux_on_failure else 'unchanged',
                            oem_weston_renderer=weston_policy['renderer'] if weston_policy else 'unchanged',
                            oem_weston_policy=weston_policy,
                            oem_run_getty_bootstrap=run_getty,oem_getty_alias=alias_getty,
                            local_linux_shell=local_linux_shell,
                            local_linux_shell_mode=('early-shared-owner-api-drop-privileges' if shared_linux_console else 'early-usi04-drop-privileges' if isolated_linux_uart else 'early-usi03-drop-privileges') if local_linux_shell else None,
                            local_linux_shell_tty=(linux_early_uart.TTY if shared_linux_console else linux_local_shell.DIRECT_TTY) if local_linux_shell else None,
                            local_shared_console_policy=shared_console_policy if shared_linux_console else None,
                            verity_arguments_preserved=not bool(weston_plan),dm_verity_enabled=True)

android_init_trace.boot_dtb = trace_bootargs

import linux_pid1_context
real_capture=linux_pid1_context.capture

def read_original_ivi_frame(spans):
    qmp=state.get('ap_qmp')
    if (qmp is None or qmp.socket.getpeername()!=('127.0.0.1',state['ap_port']) or
            orig(qmp,'query-status')['status']!='paused'):
        raise ValueError('Original AP QMP is not paused for terminal IVI capture')
    scope=((0xb8000000,0xd0000000),(0xe3c00000,0xe4c00000),(0xa80000000,0xb00000000))
    if (not 0<len(spans)<=1072 or sum(n for _,n in spans)!=6272*700 or
            any(not any(lo<=address<address+n<=hi for lo,hi in scope) for address,n in spans)):
        raise ValueError('Terminal IVI byte request exceeds original VM3 raster bounds')
    raster=bytearray()
    started=time.monotonic()
    with TemporaryDirectory(prefix='ivi16-read-',dir=out) as folder:
        temp=Path(folder)/'span.bin'
        for address,length in spans:
            if time.monotonic()-started>4:
                raise ValueError('Terminal IVI physical read budget exceeded')
            orig(qmp,'pmemsave',dict(val=address,size=length,filename=str(temp)))
            chunk=temp.read_bytes()
            if len(chunk)!=length:
                raise ValueError('Short terminal IVI physical read')
            raster.extend(chunk)
    try:
        gpu=orig(qmp,'qom-get',dict(path='/machine/exynos-mali-g3d1',property='vm3-center-target-history'))
    except (OSError,RuntimeError,ValueError):
        gpu=None
    return raster,gpu


def inspect_tee_rpmb(symbols, read_u64):

    from linux_pid1_context import LINUX_SHA, capture_task_stack
    if symbols.get('sha256') != LINUX_SHA:
        raise ValueError('TEE observer requires the original 8704 Linux kernel')
    names = {x['name']: int(x['address'], 16) for x in symbols['symbols']}
    if names.get('rpmb_mutex') != 0xffffffc0111aee10:
        raise ValueError('Original RPMB mutex symbol changed')
    state = read_u64(0xffffffc0111f5a10) & 0xffffffff
    word = read_u64(names['rpmb_mutex'])
    owner = word & ~7
    result = dict(tee_start_state=state, rpmb_mutex_word=hex(word),
                  owner_task=hex(owner), owner_flags=word & 7,
                  scope='Stopped original VM3; owner and saved stack only, no secure payloads')
    if not owner:
        return dict(result, status='unowned')
    if not 0xffffff8000000000 <= owner <= 0xfffffffffffff000 or owner % 64:
        raise ValueError('Invalid original RPMB owner pointer')
    pid = read_u64(owner + 0x570)
    if not 0xffffff8000000000 <= pid <= 0xffffffffffffffa0 or pid % 8:
        raise ValueError('Invalid original RPMB owner PID object')
    if read_u64(pid + 0x58) != names['init_pid_ns']:
        raise ValueError('Original RPMB owner PID namespace mismatch')
    comm = b''.join(read_u64(owner + 0x6c0 + i).to_bytes(8, 'little') for i in (0, 8))
    result.update(status='verified_owner', pid=read_u64(pid + 0x50) & 0xffffffff,
                  comm=comm.split(b'\0', 1)[0].decode('ascii', 'backslashreplace'))
    try:
        result['saved_stack'] = capture_task_stack(symbols, owner, read_u64)
    except ValueError as exc:
        result['stack_unverified'] = str(exc)
    return result


def inspect_original_tasks(symbols, read_u64, *, read_user_u64=None):
    result=real_capture(symbols,read_u64)
    observer=Path(__file__).with_name('original-linux-observer.py')
    if observer.is_file():
        import runpy
        try:
            snapshot=runpy.run_path(str(observer))['inspect'](
                symbols,read_u64,result,center_peers=wayland_trace,
                read_user_u64=read_user_u64 if wayland_trace else None,
                read_ivi_frame=read_original_ivi_frame if sample_center is not None and not short_validation else None,
                hotspot_log_parser=(runpy.run_path(str(observer.with_name('oem-hotspot-log.py')))['parse']
                                    if hmi_thread_dump else None),
                inspect_hmi_queues=(runpy.run_path(str(observer.with_name('observe-hmi-queues.py')))['inspect_hmi_queues']
                                    if hmi_queue_read else None))
        except (ValueError,KeyError) as error:
            snapshot=dict(status='not_verified',reason=str(error))
        try:
            snapshot['native_tee_rpmb'] = inspect_tee_rpmb(symbols, read_u64)
        except (OSError, RuntimeError, KeyError, ValueError) as exc:
            snapshot['native_tee_rpmb'] = dict(status='not_verified', reason=str(exc))
        vm.save(out/'linux-original-userspace.json',snapshot)
    return result

linux_pid1_context.capture=inspect_original_tasks
import cal_init_platform
original_qnx_capture=cal_init_platform.capture_runtime

def capture_qnx_native(qmp,output):
    result=original_qnx_capture(qmp,output)
    observer=Path(__file__).with_name('qnx-buffer-capture.py')
    if observer.is_file():
        try:
            runpy.run_path(str(observer))['capture'](qmp,Path(output))
        except (ValueError,OSError) as error:
            vm.save(Path(output)/'screen-buffer-error.json',dict(error=str(error),verified=False))
    if qnx_live_log:
        try:
            runpy.run_path(str(Path(__file__).with_name('qnx-bootmarkers.py')))['capture'](qmp,Path(output))
        except (ValueError,OSError,RuntimeError) as error:
            vm.save(Path(output)/'qnx-bootmarkers.json',dict(status='not_verified',reason=str(error)))
    return result

cal_init_platform.capture_runtime=capture_qnx_native

if '--watch-native-mali-submit' in sys.argv:
    sys.argv.remove('--watch-native-mali-submit')
    import linux_panic_watch as panic

    arm_original,observe_original=panic.arm,panic.observe
    sites=(0xffffffc01053dac4,0xffffffc01053dac8)
    start=0xffffffc010080000
    proof={'guest':'linux','vmid':3,'events':[],'foreign_hits':[],'memory_modified':False}

    def arm_mali(rsp,symbols,kernel):
        state=arm_original(rsp,symbols,kernel)
        data=Path(kernel['kernel']).read_bytes()
        if hashlib.sha256(data).hexdigest()!='c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109':
            raise ValueError('Original Linux kernel differs at the Mali submit site')
        proof['code']={hex(pc):data[pc-start:pc-start+4].hex() for pc in sites}
        if rsp.command(f'Z1,{sites[0]:x},4')!=b'OK':
            raise ValueError('Could not arm original Mali submit breakpoint')
        vm.save(out/'original-mali-submit.json',proof)
        return state

    def observe_mali(rsp,qmp,state,output):
        n=len(proof['events'])
        if n<len(sites) and not proof.get('watch_disabled'):
            pc=sites[n]
            info=qmp.execute('human-monitor-command',{'command-line':'info registers -a'})
            if re.search(r'PC=0*'+format(pc,'x')+r'\b',info,re.I):
                cpu=next(c for c in rsp.system_snapshot()['cpus'] if c.get('core',{}).get('pc')==hex(pc))
                vmid=int(cpu['system'].get('VTTBR_EL2','0'),16)>>48
                if vmid!=3:
                                                                          
                                                                               
                                                                           
                                                                              
                    proof['foreign_hits'].append(dict(pc=hex(pc),thread=cpu['thread'],vmid=vmid))
                    reply=rsp.command(f'z1,{pc:x},4')
                    if reply[:1] in (b'S',b'T'):
                        reply=rsp.receive()
                    if reply!=b'OK':
                        raise ValueError('Could not withdraw shared-VA Mali watch')
                    if len(proof['foreign_hits'])>=8:
                        proof['watch_disabled']='shared virtual address hit budget'
                    else:
                        if rsp.command('Hc'+cpu['thread'])!=b'OK' or rsp.command('s')[:1] not in (b'S',b'T'):
                            raise ValueError('Could not step over foreign guest shared VA')
                        if rsp.command(f'Z1,{pc:x},4')!=b'OK':
                            raise ValueError('Could not re-arm native Linux Mali watch')
                    vm.save(out/'original-mali-submit.json',proof)
                    return True
                if rsp.command('Hg'+cpu['thread'])!=b'OK':
                    raise ValueError('Could not select original Mali submit context')
                insn=panic.mem(rsp,pc,4)
                if insn is None or insn.hex()!=proof['code'][hex(pc)]:
                    raise ValueError('Original Mali submit code did not match stopped context')
                regs=cpu['core']
                event=dict(pc=hex(pc),thread=cpu['thread'],argument_x1=regs['x1'],
                           argument_x2=regs['x2'],result_x0=regs['x0'])
                if n==0:
                    prev=int(regs['x1'],16)
                    next_=int(regs['x2'],16)
                    prev_link=panic.mem(rsp,prev,8)
                    next_link=panic.mem(rsp,next_+8,8)
                    event['previous_link_points_to_next']=bool(prev_link and int.from_bytes(prev_link,'little')==next_)
                    event['next_link_points_to_previous']=bool(next_link and int.from_bytes(next_link,'little')==prev)
                else:
                    event['same_vcpu_as_entry']=cpu['thread']==proof['events'][0]['thread']
                    event['list_validator_accepted']=bool(int(regs['x0'],16)&0xff)
                proof['events'].append(event)
                reply=rsp.command(f'z1,{pc:x},4')
                if reply[:1] in (b'S',b'T'):
                    reply=rsp.receive()
                if reply!=b'OK':
                    raise ValueError('Could not withdraw Mali submit breakpoint')
                if n+1<len(sites) and rsp.command(f'Z1,{sites[n+1]:x},4')!=b'OK':
                    raise ValueError('Could not arm original Mali list validation return')
                vm.save(out/'original-mali-submit.json',proof)
                return True
        return observe_original(rsp,qmp,state,output)

    panic.arm,panic.observe=arm_mali,observe_mali
vlm_platform.main()
