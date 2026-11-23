
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time


@contextmanager
def snapshot_clock(qmp, execute, output, environ=None):
    env = os.environ if environ is None else environ
    value = env.get('OEM_8704_PAUSE_OBSERVER_CLOCK', '')
    if not value:
        yield
        return
    if (value != 'paused-snapshot-v1' or
            env.get('OEM_8704_OFFLINE_DEVELOPMENT') != 'android-private-unencrypted-v1' or
            env.get('OEM_8704_OFFLINE_CLOCK')):
        raise ValueError('Paused observations require explicit offline wall-clock mode')
    before = execute(qmp, 'query-status')
    owned_pause = bool(before.get('running'))
    row = {'profile': value, 'before': before, 'owned_pause': owned_pause,
           'guest_code_modified': False, 'guest_irq_masked': False,
           'scope': 'Same owning AP QMP connection; pause/resume the simulator for read-only sampling'}
    started = time.monotonic()
    previous_depth = getattr(qmp, '_offline_snapshot_depth', 0)
    try:
        if owned_pause:
            execute(qmp, 'stop')
            paused = execute(qmp, 'query-status')
            row['paused'] = paused
            if paused.get('running'):
                raise RuntimeError('Native observer did not obtain stopped state')
            if paused.get('status') != 'paused':
                raise RuntimeError('Native observer pause is not a coherent sampling state')
            qmp._offline_snapshot_depth = previous_depth + 1
        yield
    except BaseException as error:
        row['sampling_error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        qmp._offline_snapshot_depth = previous_depth
        try:
            if owned_pause:
                execute(qmp, 'cont')
                row['after'] = execute(qmp, 'query-status')
                if row['after'].get('running') is not True:
                    raise RuntimeError('Native observer did not restore running state')
        finally:
            row['sampling_wall_seconds'] = time.monotonic() - started
            row['observed_monotonic'] = time.monotonic()
            path = Path(output) / 'native-observer-clock.jsonl'
            with path.open('a', encoding='utf8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
