
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/emu'))
import original_8704_vm as vm


def obj(path):
    return json.loads(path.read_bytes()) if path.is_file() else {}


def rows(path):
    return [json.loads(row) for row in path.read_bytes().splitlines()] if path.is_file() else []


def read_ring(session, guest, kernel):
    raw=session/f'{guest}-printk-ram.bin'
    report=obj(session/f'{guest}-printk-records.json')
    ring=report.get('ring',{})
    if ring.get('status')=='verified_live_ring':
        return report['records'],ring
    if not raw.is_file():
        return [],{'status':'not_captured'}
    data=raw.read_bytes()
    keys=('first','next_index','first_seq','next_seq')
    if kernel and all(key in ring for key in keys):
        layout=vm.original_printk_layout(kernel)
        try:
            records=vm.printk_live_ring(data,**{key:ring[key] for key in keys},
                physical=int(report['buffer_physical'],16),**layout)
            return records,{'status':'verified_live_ring_redecode','layout':layout,**{key:ring[key] for key in keys}}
        except ValueError as error:
            ring={**ring,'reason':str(error)}
    return vm.printk_records(data,max_records=65536),{'status':'heuristic_fallback','reason':ring.get('reason')}


def verify(session):
    linux=obj(session/'linux-original-userspace.json')
    media=obj(session/'ufs-lun0-input.json')
    parts=obj(Path(media['base']).with_suffix('.json'))['partitions']
    nav=next(p for p in parts if p['slot']==18 and p['label']=='navi_core')
    nav_data=next(p for p in parts if p['slot']==26 and p['label']=='nav_rw_data_ivi_rbhv-qia')
    qnx_root={key:next(p for p in parts if p['slot']==slot and p['label']==label) for key,slot,label in
              (('a',32,'system_qnx_rbhv-qia'),('b',33,'system_qnx_bak_rbhv-qia'))}
    stats=Counter()
    qnx_reads=Counter()
    pmu_unsupported_base=0
    read=re.compile(r'ufs_native_read_result host=VIRTUAL-AUTO9-UFS([01]) .*? lun=0 .*? lba=(\d+) blocks=(\d+) ocs=(\d+) status=(\d+)')
    with (session/'tcg.log').open(encoding='utf-8',errors='replace') as log:
        for line in log:
            if line.strip()=='Auto9 PMU unsupported read offset=0x0':
                pmu_unsupported_base+=1
            if match:=read.search(line):
                host,lba,count,ocs,status=map(int,match.groups())
                if host==0:
                    qnx_reads['failed' if ocs or status else 'successful']+=1
                    for key,part in qnx_root.items():
                        if lba<=part['last'] and lba+count-1>=part['first']:
                            qnx_reads[key+('_failed' if ocs or status else '_successful')]+=1
                    continue
                if ocs or status:
                    stats['failed']+=1
                else:
                    stats['successful']+=1
                    if lba<=nav['last'] and lba+count-1>=nav['first']:
                        stats['navi_core']+=1
                    if lba<=nav_data['last'] and lba+count-1>=nav_data['first']:
                        stats['navi_data']+=1
    loads={row['guest']:row.get('kernel_sha256') for row in obj(session/'kernel-loads.json')}
    parsed={guest:read_ring(session,guest,loads.get(guest)) for guest in ('linux','android')}
    qnxpath=session/'qnx-native-runtime/qnx-boot-live.txt'
    qnx=qnxpath.read_text(encoding='utf-8') if qnxpath.is_file() else ''
    rx=session/'vlm-console-rx.bin'
    uart_raw=rx.read_bytes() if rx.is_file() else b''
    uart=vm.native_console_lines(uart_raw,limit=1<<20)
    partial=vm.native_console_record(uart_raw.rpartition(b'\n')[2])
    linux_prompt=uart['linux']+('\n'+partial[1] if partial and partial[0]=='linux' else '')
    linux_rows,linux_ring=parsed['linux']
    android_rows,android_ring=parsed['android']
    linux_text='\n'.join(r['text'] for r in linux_rows)+'\n'+uart['linux']
    android_text='\n'.join(r['text'] for r in android_rows)
    linux_boot_rows=[r['text'] for r in linux_rows if r['timestamp_ns']==0]
    focuses=rows(session/'live-vh1.jsonl')
    events=rows(session/'vlm-console-events.jsonl')
    transitions=[]
    for event in events:
        if event['event']!='focus_key_sent' or 'host_monotonic' not in event:
            continue
        target={'linux':'3300','android':'3400'}[event['guest']]
        at=event['host_monotonic']
        prior=[row.get('vlm_focus_raw_hex') for row in focuses if row['host_monotonic']<at]
        after=[row.get('vlm_focus_raw_hex') for row in focuses if at<=row['host_monotonic']<=at+12]
        transitions.append(dict(target=event['guest'],before=prior[-1] if prior else None,after=after,
                                changed=bool(prior and prior[-1] in ('3300','3400') and
                                             prior[-1]!=target and target in after)))
    pans=obj(session/'linux-panic-watch.json')
    nav_mount=next((m for m in linux.get('mounts',[]) if m['mountpoint']=='/lge/navi/naviengine'),None)
    nav_data_mount=next((m for m in linux.get('mounts',[]) if m['mountpoint']=='/navi_data'),None)
    sysmmu=obj(session/'linux-sysmmu-runtime.json').get('0x18c80000',{})
    console_proof=obj(session/'native-console-http-verification.json')
    console_executed={guest:console_proof.get(guest,{}).get('standalone_marker_observed') is True
                      for guest in ('linux','android')}
    chardev_log=session/'exynos-uart.bin'
    physical_uart=chardev_log.read_bytes() if chardev_log.is_file() else b''
    vm2_pmu_aborts=[]
    for fault in re.finditer(rb'(?m)^(?:\x1b\[[0-9;]+m)?(\d+) HYP:[^\n]* ESR 0x96000010 HDFAR 0x10460000 HPFAR 0x104600\r*$',physical_uart):
        cpu=fault[1]
        context=physical_uart[fault.end():fault.end()+16384]
                                                                        
                                                                          
        prefix=rb'(?m)^'+cpu+rb' HYP:[^\n]*'
        if (re.search(prefix+rb"_iomem_load64",context) and
                re.search(prefix+rb'vttbr_el2:\s*0x0002[0-9a-f]{12}\b',context) and
                re.search(prefix+rb'PANIC 1 in PANIC',context)):
            vm2_pmu_aborts.append(int(cpu))
    return dict(run=str(session),run_observation=obj(session/'run-observation.json'),
        originals_unchanged=obj(session/'integrity.json').get('original_unchanged'),
        linux=dict(records=len(linux_rows),ring=linux_ring,weston_ready='[Startup] weston ready' in linux_text,
            weston_ready_ns=next((r['timestamp_ns'] for r in linux_rows if '[Startup] weston ready' in r['text']),None),
            weston_allocation_warning='weston: page allocation failure' in linux_text,
            oom_killed=bool(re.search(r'Out of memory: Killed process',linux_text)),
            memory_ranges=[line.strip() for line in linux_boot_rows if 'node   0: [mem ' in line],
            memory_summary=next((line for line in linux_boot_rows if line.startswith('Memory: ')),None),
            fsck_ok=sorted(set(re.findall(r'fsck /dev/(sda\d+) -> OK',linux_text))),
            remount_unit_completed='Finished Remount Root and Kernel File Systems' in linux_text,
            udev_coldplug_completed='Started udev Coldplug all Devices' in linux_text,
            udev_started=any(t['comm']=='systemd-udevd' for t in linux.get('tasks',[])),
            read_requests=dict(successful=stats['successful'],failed=stats['failed'],navi_core=stats['navi_core'],
                               navi_data=stats['navi_data']),
            naviengine_mounted=nav_mount is not None,naviengine_mount=nav_mount,
            navi_data_mount=nav_data_mount,
            navi_data_ext4_missing="EXT4-fs (sda26): VFS: Can't find ext4 filesystem" in linux_text,
            emergency_target='Reached target Emergency Mode.' in linux_text,
            local_fs_reached='Reached target Local File Systems.' in linux_text,
            post_init_script_executed=bool(re.search(r'post-init\.service: Executing: /lge/app_ro/post_init/post_init_main\.sh systemd',linux_text)),
            weston_service_failed='Failed to start Weston Wayland compositor startup.' in linux_text,
            clock_prepare_lock=linux.get('clock_prepare_lock'),
            mxnavi_running=any(t['comm']=='MXNavi' for t in linux.get('tasks',[])),
            relevant_tasks=[t['comm'] for t in linux.get('tasks',[]) if re.search('navi|weston|mali|udev|displaymgr|agetty',t['comm'],re.I)],
            navi_dcache=linux.get('dcache',{}).get('/dev/disk/by-partlabel/navi_core'),
            getty_unit_not_found='Unit serial-getty@ttySAC0.service not found' in linux_text,
            getty_unit_job_done=bool(re.search(r'(?:serial-getty@ttySAC0|oem-local-uart)\.service/start finished, result=done',linux_text)),
            getty_alias_job_canceled=bool(re.search(r'oem-local-uart\.service/start finished, result=canceled',linux_text)),
            getty_process=any(t['comm']=='agetty' for t in linux.get('tasks',[])),
            early_getty_overlay=next((r['text'] for r in linux_rows if r['text'].startswith('OEM run overlay:')),None),
            login_prompt_verified=bool(re.search(r'(?:^|\n)[^\r\n]*\blogin: ?(?:\n|$)',linux_prompt)),
            mali_scheduler_fault='kbase_js_ctx_list_add_pullable_nolock' in linux_text,
            dpu_sysmmu_root=hex(sysmmu['ptbase-page']<<12) if sysmmu.get('ptbase-writes') else None,
            dpu_sysmmu_rejections=sysmmu.get('rejected'),
            native_scanout=obj(session/'linux-native-scanout.json'),
            panic_events=[dict(name=x['name'],message=x.get('message'),fault_pc=x.get('system',{}).get('ELR_EL1')) for x in pans.get('events',[])],
            jm_probe_reads=obj(session/'mali-g3d1-runtime.json').get('jm-probe-reads')),
        android=dict(records=len(android_rows),ring=android_ring,second_stage='init second stage started' in android_text,
            keymaster_wait='Waited one second for android.hardware.keymaster' in android_text,
            zygote_started=bool(re.search(r"starting service 'zygote(?:_secondary)?'",android_text))),
        qnx=dict(trustonic_nq_failure='NQ start failed ret=-5' in qnx,
            pmu_om_input=obj(session/'pmu-runtime-state.json').get('om_input'),
            pmu_om_unsupported_reads=pmu_unsupported_base,vm2_pmu_om_el2_abort_on_cpu=vm2_pmu_aborts,
            root_startup_enoent=b'Unable to start "/scripts/startup.sh" (2)' in physical_uart,
            physical_ufs0_lun0_reads={k:qnx_reads[k] for k in
                ('successful','failed','a_successful','a_failed','b_successful','b_failed')},
            screen_buffers=[(x['width'],x['height'],x['visible_nonzero_bytes'],x['scanout_verified'])
                            for x in obj(session/'qnx-native-runtime/screen-buffer-observation.json').get('buffers',[])],
            frame_start_acks=[obj(session/'qnx-decon-runtime.json').get(f'frame-start-acks-{i}') for i in range(2)]),
        console=dict(received_chars={k:len(v) for k,v in uart.items()},focus_transitions=transitions,
                     shell_execution_verified=console_executed),
        acceptance=dict(original_command_echoes=sum(console_executed.values()),original_live_displays=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('session',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    data=verify(a.session.resolve(strict=True))
    vm.save(a.output,data)
    print(json.dumps(data,ensure_ascii=False))
