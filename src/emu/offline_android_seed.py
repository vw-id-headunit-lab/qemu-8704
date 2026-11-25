
import hashlib
import json
import os
import re
from pathlib import Path

import original_8704_vm as vm
from android_local_shell import read_verified_proof

KERNEL_SHA = '471ce1178f76ae9b4dfba3f87110bfef4f31e432574dafda7f89330f87ca3a29'


def _native_command_proof(source):
    capture = source / 'exynos-uart.bin'
    with (source / 'native-console-commands.jsonl').open() as records:
        for line in records:
            item = json.loads(line)
            if item.get('guest') != 'android' or item.get('command') != 'id -u; uname -r':
                continue
            result = item['result']
            if (item.get('session') != str(source) or item.get('capture') != str(capture) or
                    result.get('exit') != 0 or result.get('stdout') != '2000\n5.4.131-android11-2-ab151\n'):
                continue
            start, end = result['raw_start'], result['raw_end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= start + 40960:
                continue
            with capture.open('rb') as stream:
                stream.seek(start)
                raw = stream.read(end - start)
            if hashlib.sha256(raw).hexdigest() != result['raw_sha256']:
                continue
            rows = []
            for row in raw.split(b'\n')[:-1]:
                tagged = vm.native_console_record(row)
                if tagged and tagged[0] != 'android':
                    continue
                rows.append(tagged[1] if tagged else row.rstrip(b'\r').decode('utf8', 'replace'))
            begin = item['begin']
            if not re.fullmatch('MEBB_[0-9a-f]{16}', begin) or item['end'] != 'MEBE_' + begin[5:]:
                continue
            if begin not in rows or item['end'] + ':0' not in rows:
                continue
            a, b = rows.index(begin) + 1, rows.index(item['end'] + ':0')
            if rows[a:b] == ['2000', '5.4.131-android11-2-ab151', '']:
                return True
    return False


def select(policy, environ=None):
    env = os.environ if environ is None else environ
    value = env.get('OEM_8704_OFFLINE_ANDROID_SEED', '')
    if not value:
        return None
    if (policy.get('profile') != 'android-private-unencrypted-v1' or
            policy.get('factory_boot_verified') is not False or
            env.get('OEM_8704_OFFLINE_DEVELOPMENT') != 'android-private-unencrypted-v1'):
        raise ValueError('Private seed is confined to explicit offline development')
    source = Path(value).resolve(strict=True)
    if (source.parent not in (vm.ROOT / 'var/sessions', vm.ROOT / 'var/hvf') or
            Path(value).is_symlink() or not source.is_dir()):
        raise ValueError('Require a local native offline session, without links')

    def load(name):
        path = source / name
        if path.is_symlink():
            raise ValueError('Seed receipts cannot redirect')
        return json.loads(path.read_bytes())

    if not (source / 'result.json').is_file():
        raise ValueError('Private seed session has not closed')
    integrity = load('integrity.json')
    dev = load('offline-development.json')
    origin = load('android-ufs-overlay.json')
    if (not integrity.get('original_unchanged') or integrity.get('exit_code') != 0 or
            integrity.get('before') != integrity.get('after') or
            dev.get('profile') != 'android-private-unencrypted-v1' or
            dev.get('factory_boot_verified') is not False or
            dev.get('data_encrypted') is not False or dev.get('tee_success_synthesized') is not False or
            not (dev.get('fresh_android_data_only') is True or
                 dev.get('private_android_seed', {}).get('validated') is True)):
        raise ValueError('Not a proven private unencrypted offline predecessor')
    if not (read_verified_proof(source) or (source.parent == vm.ROOT / 'var/hvf' and _native_command_proof(source))):
        raise ValueError('Private seed lacks original Android command verification')
    kernel = source / 'android-kernel-original.bin'
    if hashlib.sha256(kernel.read_bytes()).hexdigest() != KERNEL_SHA:
        raise ValueError('Private seed original kernel differs')
    uart = (source / 'exynos-uart.bin').read_bytes()
    if b'System-wide panic initiated' in uart or b'Kernel panic - not syncing:' in uart:
        raise ValueError('Do not promote a guest-panic snapshot as a private seed')
    if source.parent == vm.ROOT / 'var/hvf':
        base = load('ufs-lun0-base-integrity.json')
        if base.get('unchanged_stat') is not True:
            raise ValueError('Native predecessor base changed')
    image = source / 'ufs-vh2-lun0-overlay.qcow2'
    if (image.is_symlink() or not image.is_file() or
            Path(origin['path']).resolve() != image or origin.get('owner') != 'VM4 / UFS VH2' or
            origin.get('base_read_only') is not True):
        raise ValueError('Private seed VM4 image ownership differs')
                                                                                
                                                                               
    receipt = {'source': str(source), 'image_sha256': vm.sha(image), 'validated': True,
               'original_kernel_sha256': KERNEL_SHA, 'vehicle_data_allowed': False,
               'source_modified': False, 'guest_clean_unmount_claimed': False,
               'scope': 'Only known private offline bytes; original fsck/journal recovery remains active'}
    policy['private_android_seed'] = receipt
    policy['fresh_android_data_only'] = False
    policy['android_data_private_only'] = True
    return source
