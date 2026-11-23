
import os

PROFILE = 'icount-shift4-v1'


def selected(environ=None):
    env = os.environ if environ is None else environ
    value = env.get('OEM_8704_OFFLINE_CLOCK', '')
    if not value:
        return False
    if (value != PROFILE or
            env.get('OEM_8704_OFFLINE_DEVELOPMENT') != 'android-private-unencrypted-v1'):
        raise ValueError('Unknown or non-offline instruction clock')
    return True


def policy(*, native, parallel, shift, environ=None):
    if not selected(environ):
        return None
    if not native or parallel or shift != 4:
        raise ValueError('Instruction clock needs original three-domain layout, single TCG, shift4')
    return {'profile': PROFILE, 'default_enabled': False, 'tcg_thread': 'single',
            'icount': 'shift=4,align=off,sleep=off', 'ns_per_instruction': 16,
            'factory_timing_verified': False, 'cycle_accurate': False,
            'interrupts_masked': False, 'guest_event_pool_changed': False,
            'guest_panic_detection_disabled': False,
            'scope': 'Pace virtual timers by actual translated instructions; preserve guest timers and faults'}
