"""P0 host-clock measurements: logged milestones and operator observations stay distinct."""
import csv
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import threading
import time
import uuid

from .progress import MARKERS


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


class Measurement:
    def __init__(self, output, *, origin=None, origin_label='run_function', scenario='default',
                 cache_state='unspecified', label='', clock=time.perf_counter):
        self.clock = clock
        self.origin = self.clock() if origin is None else origin
        self.lock = threading.RLock()
        self.directory = Path(output) / 'measurements' / (
            datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:10])
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / 'measurement.json'
        self.record = {
            'schema': 1, 'id': self.directory.name, 'state': 'preparing',
            'clock': {'kind': 'host perf_counter', 'origin': origin_label,
                      'note': 'Includes entrypoint imports when origin=python_entry; excludes interpreter bootstrap before the first Python timestamp.'},
            'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'scenario': scenario, 'cache_state': cache_state, 'label': label,
            'host': {'platform': platform.platform(), 'machine': platform.machine(), 'logical_cpus': os.cpu_count()},
            'events': {}, 'observations': {}, 'interactions': [], 'input_transport': [],
            'metrics_seconds': {}, 'profile_id': None, 'qemu_pid': None,
            'limitations': [
                'Log milestones are not proof of visible UI or successful interaction.',
                'Operator-confirmed times include observer reaction and HTTP scheduling; they are upper bounds, not automated frame analysis.',
                'Cache state is an operator label; this tool does not flush OS caches.',
                'CPU is QEMU-process CPU only; it excludes the viewer, Python, browser and other host processes.',
                'No VNC request count is reported as rendered FPS.',
            ],
        }
        self.pending = None
        self.save()

    def elapsed(self):
        return round(self.clock() - self.origin, 6)

    def save(self):
        with self.lock:
            atomic_json(self.path, self.record)

    def mark(self, name, *, source='host', detail=None):
        with self.lock:
            if name not in self.record['events']:
                self.record['events'][name] = {'elapsed_seconds': self.elapsed(), 'source': source}
                if detail is not None:
                    self.record['events'][name]['detail'] = detail
                self._metrics()
                self.save()

    def observe_log(self, data):
        for marker, phase, _ in MARKERS:
            if marker.encode() in data:
                self.mark('log_' + phase, source='guest-log-received-on-host')

    def set_profile(self, *, mode, language, members, sources, inputs, argv):
        with self.lock:
            profile = {'mode': mode, 'language': language, 'overlay_members': members,
                       'source_sha256': sources, 'input_file_identities': inputs,
                       'clock_origin': self.record['clock']['origin'],
                       'host': self.record['host'], 'vm': {key: argv[argv.index(key) + 1]
                           for key in ('-machine', '-cpu', '-accel', '-smp', '-m') if key in argv}}
            self.record['profile'] = profile
            self.record['profile_id'] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:20]
            self.record['qemu_argv'] = argv
            self.save()

    def attach_process(self, pid):
        with self.lock:
            self.record.update(qemu_pid=pid, state='running')
            self.mark('qemu_process_created')

    def _metrics(self):
        events = self.record['events']
        metrics = self.record['metrics_seconds']
        def at(name):
            return events.get(name, {}).get('elapsed_seconds')
        for metric, start, end in (
            ('initramfs_build', 'initramfs_build_started', 'initramfs_ready'),
            ('transport_setup', 'transport_setup_started', 'transports_ready'),
            ('qemu_process_creation', 'qemu_launch_started', 'qemu_process_created'),
        ):
            if at(start) is not None and at(end) is not None:
                metrics[metric] = round(at(end) - at(start), 6)
        if at('qemu_launch_started') is not None:
            metrics['python_entry_to_qemu_launch'] = at('qemu_launch_started')
        if at('qemu_process_created') is not None:
            metrics['python_entry_to_qemu_process'] = at('qemu_process_created')
        for key in ('first_visible', 'menu_ready'):
            observation = self.record['observations'].get(key)
            if observation:
                metrics[key + '_operator_confirmed'] = observation['elapsed_seconds']
                if at('qemu_launch_started') is not None:
                    metrics['qemu_launch_to_' + key + '_operator_confirmed'] = round(observation['elapsed_seconds'] - at('qemu_launch_started'), 6)

    def exclusions(self, session_record):
        """A manual host stop is not a complete cold-start trial merely because QEMU exits 0."""
        with self.lock:
            self.record['trial_flags'] = {name: bool(session_record.get(name))
                for name in ('host_timeout', 'interrupted')}

    def action(self, data, source='viewer-operator'):
        if not isinstance(data, dict):
            raise ValueError('Measurement action must be an object')
        with self.lock:
            kind = data.get('action')
            if kind in ('first_visible', 'menu_ready'):
                if self.record['state'] != 'running':
                    raise ValueError('Visual observations require a running measured session')
                if kind == 'menu_ready' and 'first_visible' not in self.record['observations']:
                    raise ValueError('Confirm the first visible UI before confirming the usable menu')
                if kind not in self.record['observations']:
                    self.record['observations'][kind] = {
                        'elapsed_seconds': self.elapsed(), 'source': source,
                        'method': 'explicit operator confirmation; not inferred from logs or framebuffer activity',
                    }
            elif kind == 'arm_click':
                if self.pending:
                    raise ValueError('A click measurement is already pending; confirm or cancel it first')
                label = data.get('label', 'Settings')
                if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
                    raise ValueError('A click label of 1..80 characters is required')
                if self.record['state'] != 'running':
                    raise ValueError('A measured guest must be running')
                self.pending = {'id': uuid.uuid4().hex[:12], 'label': label.strip(),
                                'armed_at_seconds': self.elapsed(), 'source': source,
                                'pointer_down_seconds': None, 'pointer_up_seconds': None}
                self.record['interactions'].append(self.pending)
            elif kind == 'confirm_click':
                if not self.pending or self.pending['pointer_up_seconds'] is None:
                    raise ValueError('No completed physical pointer press/release is pending')
                if self.pending.get('transport_failed') or self.pending.get('multiple_physical_clicks'):
                    raise ValueError('The pending input failed or contains multiple clicks; cancel and retry')
                now = self.elapsed()
                self.pending.update(confirmed_at_seconds=now, confirmed_by=source,
                    down_to_confirmation_seconds=round(now - self.pending['pointer_down_seconds'], 6),
                    up_to_confirmation_seconds=round(now - self.pending['pointer_up_seconds'], 6),
                    method='operator-confirmed response upper bound; includes observer reaction and transport scheduling')
                self.pending = None
            elif kind == 'cancel_click':
                if self.pending:
                    self.pending.update(cancelled_at_seconds=self.elapsed(), cancelled_by=source)
                    self.pending = None
            else:
                raise ValueError('Unknown measurement action')
            self._metrics()
            self.save()
            return self.public()

    def pointer_start(self, action, x, y):
        with self.lock:
            timestamp = self.elapsed()
            if self.pending and action == 'down' and self.pending['pointer_down_seconds'] is None:
                self.pending.update(pointer_down_seconds=timestamp, pointer_coordinates=[x, y])
                self.save()
            elif self.pending and action == 'down' and self.pending['pointer_up_seconds'] is not None:
                self.pending['multiple_physical_clicks'] = True
                self.save()
            return timestamp

    def pointer_end(self, action, began, sent):
        with self.lock:
            if action == 'move':
                return
            now = self.elapsed()
            if action in ('down', 'up', 'cancel'):
                self.record['input_transport'].append({'action': action, 'sent': bool(sent),
                    'request_at_seconds': began, 'host_handler_seconds': round(now - began, 6),
                    'scope': 'HTTP handler to host TCP drain only; not guest/UI response'})
            if self.pending and self.pending['pointer_down_seconds'] is not None:
                if not sent:
                    self.pending['transport_failed'] = True
                elif action == 'up' and self.pending['pointer_up_seconds'] is None:
                    self.pending['pointer_up_seconds'] = now
                elif action == 'cancel':
                    self.pending.update(cancelled_at_seconds=now, cancelled_by='physical pointer cancellation')
                    self.pending = None
            self.save()

    def public(self):
        with self.lock:
            return {'enabled': True, 'id': self.record['id'], 'state': self.record['state'],
                    'elapsed_seconds': self.elapsed(), 'events': dict(self.record['events']),
                    'observations': dict(self.record['observations']), 'metrics_seconds': dict(self.record['metrics_seconds']),
                    'pending_click': dict(self.pending) if self.pending else None,
                    'interactions': [dict(row) for row in self.record['interactions']],
                    'note': 'Confirm only what is visibly observed. Manual latency includes observer reaction.'}

    def finish(self, state, qemu_status=None):
        with self.lock:
            if self.pending:
                self.pending.update(unconfirmed_at_session_end=True)
                self.pending = None
            self.record.update(state=state, qemu_exit_status=qemu_status, ended_at_seconds=self.elapsed())
            if self.record['state'] in ('ended', 'error'):
                self.record['visual_confirmation_observed'] = bool(self.record['observations'])
            self.save()


def process_stats(pid):
    """Read QEMU CPU and resident memory with native APIs, without psutil."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak_rss', 'rss', 'peak_paged', 'paged',
                    'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), 'OpenProcess failed')
        try:
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
                raise OSError(ctypes.get_last_error(), 'GetProcessTimes failed')
            memory = Counters()
            memory.cb = ctypes.sizeof(memory)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                raise OSError(ctypes.get_last_error(), 'GetProcessMemoryInfo failed')
            ticks = lambda value: (value.dwHighDateTime << 32) | value.dwLowDateTime
            return {'pid': pid, 'identity': str(ticks(created)), 'cpu_seconds': (ticks(system) + ticks(user)) / 1e7,
                    'rss_bytes': memory.rss, 'process_peak_rss_bytes': memory.peak_rss}
        finally:
            kernel.CloseHandle(handle)
    if platform.system() == 'Linux':
        text = Path(f'/proc/{pid}/stat').read_text()
        fields = text[text.rfind(')') + 2:].split()
        ticks = os.sysconf('SC_CLK_TCK')
        return {'pid': pid, 'identity': fields[19], 'cpu_seconds': (int(fields[11]) + int(fields[12])) / ticks,
                'rss_bytes': int(fields[21]) * os.sysconf('SC_PAGE_SIZE'), 'process_peak_rss_bytes': None}
    raise OSError('Native process sampling supports Windows and Linux only')


def sample_window(pid, seconds=10, *, interval=1.0, label='idle-menu', reader=process_stats,
                  clock=time.perf_counter, sleep=time.sleep):
    if not 1 <= seconds <= 60 or not 0.1 <= interval <= 10:
        raise ValueError('Sample duration must be 1..60 seconds and interval 0.1..10 seconds')
    samples = []
    started = clock()
    samples.append({'elapsed_seconds': 0.0, **reader(pid)})
    while clock() - started < seconds:
        sleep(min(interval, max(0, seconds - (clock() - started))))
        samples.append({'elapsed_seconds': clock() - started, **reader(pid)})
    if any(row['identity'] != samples[0]['identity'] for row in samples):
        raise RuntimeError('Process identity changed during sampling')
    wall = samples[-1]['elapsed_seconds']
    cpu = samples[-1]['cpu_seconds'] - samples[0]['cpu_seconds']
    return {'label': label, 'pid': pid, 'duration_seconds': round(wall, 6),
            'cpu_seconds': round(cpu, 6), 'average_host_cores': round(cpu / wall, 4),
            'host_cpu_percent': round(cpu / wall / (os.cpu_count() or 1) * 100, 3),
            'max_sampled_rss_bytes': max(row['rss_bytes'] for row in samples), 'samples': samples,
            'scope': 'QEMU process only; not guest per-thread CPU, viewer latency or rendered FPS'}


class ProcessSampler:
    def __init__(self, measurement, pid, interval=1.0, reader=process_stats):
        self.measurement, self.pid, self.interval, self.reader = measurement, pid, interval, reader
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='P0-QEMU-sampler')
        self.error = None
        self.count = 0
        self.first = self.last = None
        self.max_rss = 0

    def start(self):
        self.thread.start()

    def _run(self):
        with (self.measurement.directory / 'process-samples.jsonl').open('w', encoding='utf-8', buffering=1) as stream:
            while not self.stop_event.is_set():
                try:
                    sample = {'elapsed_seconds': self.measurement.elapsed(), **self.reader(self.pid)}
                    if self.first and sample['identity'] != self.first['identity']:
                        raise RuntimeError('QEMU PID identity changed')
                    self.first = self.first or sample
                    self.last = sample
                    self.max_rss = max(self.max_rss, sample['rss_bytes'])
                    self.count += 1
                    stream.write(json.dumps(sample) + '\n')
                except (OSError, ValueError, RuntimeError) as error:
                    self.error = str(error)
                    break
                self.stop_event.wait(self.interval)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        result = {'samples': self.count, 'error': self.error, 'max_sampled_rss_bytes': self.max_rss or None,
                  'scope': 'Whole measured QEMU lifetime; compare same-scene windows separately'}
        if self.first and self.last and self.last['elapsed_seconds'] > self.first['elapsed_seconds']:
            wall = self.last['elapsed_seconds'] - self.first['elapsed_seconds']
            cpu = self.last['cpu_seconds'] - self.first['cpu_seconds']
            result.update(duration_seconds=round(wall, 6), cpu_seconds=round(cpu, 6), average_host_cores=round(cpu / wall, 4))
        with self.measurement.lock:
            self.measurement.record['process_sampling'] = result
            self.measurement.save()


def distribution(values):
    if not values:
        return {'n': 0, 'min': None, 'median': None, 'p95': None, 'max': None}
    values = sorted(values)
    return {'n': len(values), 'min': values[0], 'median': statistics.median(values),
            'p95': values[max(0, math.ceil(len(values) * .95) - 1)], 'max': values[-1]}


def summarize(directory):
    groups = {}
    for file in sorted(Path(directory).glob('*/measurement.json')):
        row = json.loads(file.read_text(encoding='utf-8'))
        if not row.get('profile_id'):
            continue
        key = (row['profile_id'], row['scenario'], row['cache_state'])
        group = groups.setdefault(key, {'profile_id': key[0], 'scenario': key[1], 'cache_state': key[2],
            'runs': [], 'metrics': {}, 'confirmed_clicks': {}, 'cpu_windows': {}})
        group['runs'].append({'id': row['id'], 'state': row['state'], 'qemu_exit_status': row.get('qemu_exit_status'),
                              'excluded': any(row.get('trial_flags', {}).values())})
        successful = row['state'] == 'ended' and row.get('qemu_exit_status') == 0 and not any(row.get('trial_flags', {}).values())
        if successful:
            for metric, value in row.get('metrics_seconds', {}).items():
                group['metrics'].setdefault(metric, []).append(value)
            for click in row.get('interactions', []):
                if 'down_to_confirmation_seconds' in click and not click.get('transport_failed') and not click.get('multiple_physical_clicks'):
                    group['confirmed_clicks'].setdefault(click['label'], []).append(click['down_to_confirmation_seconds'])
        for window_file in file.parent.glob('cpu-window-*.json'):
            window = json.loads(window_file.read_text())
            group['cpu_windows'].setdefault(window['label'], []).append(window['average_host_cores'])
    for group in groups.values():
        group['repeat_count'] = len(group['runs'])
        group['completed_runs'] = sum(row['state'] == 'ended' and row['qemu_exit_status'] == 0 and not row.get('excluded', False) for row in group['runs'])
        group['at_least_three_runs'] = group['completed_runs'] >= 3
        group['all_metric_sample_counts_at_least_three'] = bool(group['metrics']) and all(len(values) >= 3 for values in group['metrics'].values())
        for key in ('metrics', 'confirmed_clicks', 'cpu_windows'):
            group[key] = {name: distribution(values) for name, values in group[key].items()}
    return {'groups': list(groups.values()), 'aggregation': 'Exact profile + scenario + declared cache state; timing distributions include only ended QEMU-exit-0 runs; missing metrics are not treated as zero',
            'note': 'Manual observations include reaction time; inspect per-metric n, failed runs and incomplete sessions.'}


def summary_csv(path, report):
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['profile_id', 'scenario', 'cache_state', 'metric_kind', 'metric', 'n', 'min', 'median', 'p95', 'max'])
        for group in report['groups']:
            for kind in ('metrics', 'confirmed_clicks', 'cpu_windows'):
                for name, values in group[kind].items():
                    writer.writerow([group['profile_id'], group['scenario'], group['cache_state'], kind, name,
                                     *[values[key] for key in ('n', 'min', 'median', 'p95', 'max')]])
