
import os
import hashlib
from pathlib import Path
import time

def profile_seconds(seconds, *, extended=False, stock=False, prepare=False, environ=None):
    env = os.environ if environ is None else environ
    value = env.get("OEM_8704_STOCK_LIVE_SECONDS", "")
    if not value:
        return 240
    if value not in ("600", "1200") or int(value) != seconds or not extended or not stock or prepare:
        raise ValueError("Stock live observation requires matching 600/1200 seconds, explicit extended observation, stock three-domain boot and no seed preparation")
    return int(value)


def virtual_acpm_rx_policy(environ=None, *, acpm_clock_hz=0, native_three_guests=False):
    env=os.environ if environ is None else environ
    value=env.get('OEM_8704_VIRTUAL_ACPM_RX','')
    if not value:return None
    if value!='virtual-intmr0-unmasked-v1' or not native_three_guests or not 1000000<=acpm_clock_hz<=1000000000:
        raise ValueError('Virtual ACPM predecessor requires exact opt-in profile, original three-domain boot and explicit management clock')
    if env.get('OEM_8704_ACPM_DOORBELL_DIAGNOSTIC','1')!='1':
        raise ValueError('Virtual receiver condition requires the aggregate native IRQ0 route')
    return {'profile':value,'default_enabled':False,'address':'0x10500010','value':0,
            'source':'User-authorized virtual predecessor input; NOT original POR or executed factory stage',
            'physical_reset_verified':False,'original_predecessor_recovered':False,
            'factory_images_modified':False,'pending_or_reply_injected':False,
            'cold_secure_boot_verified':False,
            'controller_debug_view':'VM-tagged existing FIFO/status snapshot; NOT recovered silicon encoding; error/reply state unchanged'}


def native_lifetime_policy(*, stock=False, prepare=False, environ=None):
    env=os.environ if environ is None else environ
    value=env.get('OEM_8704_NATIVE_LIFETIME','')
    if not value:return None
    if (value!='until-stop-v1' or not stock or prepare or
        env.get('OEM_8704_VERIFIED_LINUX_SEED')!='1' or
        env.get('OEM_8704_ISOLATED_LINUX_UART')!='4'):
        raise ValueError('Persistent native runtime needs exact opt-in, original three-domain stock profile, full verified seed and isolated UART4')
    return {'profile':value,'default_enabled':False,'time_budget_exit':False,
            'stop_file':'native-stop.request','trace_size_limit_bytes':16*1024*1024,
            'trace_overflow':'rotate-two-window-v1',
            'stop_on_actual_vm_stop':True,'boot_acceptance':False,
            'scope':'Host lifecycle only; guest code, timers, secure returns and startup dependencies unchanged'}


def native_observation_active(now, deadline, *, lifetime=None):
    return lifetime is not None or now<deadline


def service_native_trace(qmp, output, lifetime, previous=None):







    output=Path(output).resolve();limit=16*1024*1024
    active=output/'tcg.log'
    if active.stat().st_size<=limit:return None,previous
    if lifetime is None:return 'trace-size-budget',previous
    if lifetime.get('trace_overflow')!='rotate-two-window-v1':
        raise ValueError('Persistent trace rotation requires its explicit policy')
    bridge=output/'tcg-switch.log';closed=output/'tcg-closed.log'
    archive=output/'tcg-previous.log';temporary=output/'tcg-previous.tmp'
    if any(p.exists() for p in (bridge,closed,temporary)):
        raise FileExistsError('Unfinished native trace rotation')
    def switch(path):
        name=path.as_posix()
        if any(c in name for c in ('"','%','\n','\r')):
            raise ValueError('Unsafe HMP log path')
        reply=qmp.execute('human-monitor-command',{'command-line':f'logfile "{name}"'})
                                                                              
        if reply or not path.is_file():raise OSError(f'QEMU logfile switch failed: {reply!r}')
    def move_closed(source,destination):
        try:os.replace(source,destination)
        except PermissionError:
            time.sleep(0.25)
            os.replace(source,destination)
    switch(bridge)
    move_closed(active,archive)
    switch(active)
    move_closed(bridge,closed)
    old_bytes=archive.stat().st_size;bridge_bytes=closed.stat().st_size
                                                                           
    tail_bytes=min(limit,bridge_bytes)
    with closed.open('rb') as f:
        f.seek(bridge_bytes-tail_bytes);tail=f.read(tail_bytes)
    keep_old=min(old_bytes,limit-len(tail))
    with archive.open('rb') as f:
        f.seek(old_bytes-keep_old);data=f.read(keep_old)+tail
    with temporary.open('xb') as f:f.write(data)
    os.replace(temporary,archive);closed.unlink()
    return None,{'profile':'rotate-two-window-v1',
        'rotations':int((previous or {}).get('rotations',0))+1,
        'limit_bytes_per_window':limit,'closed_input_bytes':old_bytes+bridge_bytes,
        'retained_previous_bytes':len(data),'retained_previous_sha256':hashlib.sha256(data).hexdigest(),
        'discarded_old_history_bytes':old_bytes+bridge_bytes-len(data),
        'active_path':str(active),'previous_path':str(archive),
        'guest_execution_changed':False,'trace_masks_changed':False,
        'scope':'Rolling host diagnostics; older history intentionally bounded, NOT guest boot acceptance'}
