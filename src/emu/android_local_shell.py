
import gzip
import hashlib
import io
import zlib

FORCE_FILE = 'force_debuggable'
POLICY = 'android-local-shell.json'
PROOF = 'android-local-shell-proof.json'
SHELL_UID = 2000
MARKER_PREFIX = '8704-vm4-check'
RESPONSE_TAG = 'original-vm4-tag'


def _ramdisk_members(raw):
    max_bytes=128*1024**2
    if raw[:2]==b'\x1f\x8b':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            data=stream.read(max_bytes+1)
    else:
        data=raw
    if not data or len(data)>max_bytes:
        raise ValueError('Invalid OEM initramfs size')
    pos=0
    entries=set()
    trailer=False
    while pos<len(data):
        if data[pos]==0:
            pos+=1
            continue
        if pos+110>len(data) or data[pos:pos+6] not in (b'070701',b'070702'):
            raise ValueError('Invalid OEM initramfs newc header')
        fields=[int(data[pos+6+i*8:pos+14+i*8],16) for i in range(13)]
        n,size=fields[11],fields[6]
        name_at=pos+110
        payload=(name_at+n+3)&~3
        if (not 1<n<=4096 or payload+size>len(data) or
                data[name_at+n-1:name_at+n]!=b'\0'):
            raise ValueError('Invalid OEM initramfs member extent')
        name=data[name_at:name_at+n-1].decode('ascii')
        pos=(payload+size+3)&~3
        trailer=name=='TRAILER!!!'
        if not trailer:
            entries.add(name.removeprefix('./').removeprefix('/'))
    if not trailer:
        raise ValueError('Missing OEM initramfs trailer')
    return entries


def stock_console_policy(generic, vendor):

    policy=dict(local_android_shell=True,local_android_shell_mode='stock-console-observe',
                ramdisk_changed=False,avb_partitions_changed=False,oem_code_changed=False,
                generic_sha256=hashlib.sha256(generic).hexdigest(),
                vendor_sha256=hashlib.sha256(vendor).hexdigest(),
                scope='OEM ramdisk bytes unchanged; verify only a real OEM VM4 shell prompt')
    try:
        members=_ramdisk_members(vendor)|_ramdisk_members(generic)
    except (ValueError,OSError,EOFError,zlib.error) as error:
        policy.update(debug_ramdisk_members_verified=False, archive_audit_error=str(error))
        return policy
    wanted=(FORCE_FILE,'adb_debug.prop','userdebug_plat_sepolicy.cil')
    policy['debug_ramdisk_members_verified']=True
    policy['root_debug_members']={name:name in members for name in wanted}
    policy['first_stage_debug_members']={name:f'first_stage_ramdisk/{name}' in members for name in wanted}
    return policy


def response_source(capture, expected):

    from original_8704_vm import native_console_record

    for line in capture.split(b'\n')[:-1]:
        if line.rstrip(b'\r') == expected:
            return 'shared-physical-uart'
        tagged = native_console_record(line)
        if tagged and tagged == ('android', expected.decode('ascii')):
            return RESPONSE_TAG
    return None


def read_verified_proof(session):

    import json
    import re

    def read_json(name, limit):
        with (session/name).open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Oversized VM4 proof input')
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError('Invalid VM4 proof input')
        return parsed

    try:
        policy = read_json(POLICY, 32768)
        if policy.get('local_android_shell') is not True:
            return None
        proof = read_json(PROOF, 4096)
        if (proof.get('status') != 'original_command_executed' or
                proof.get('command_execution_verified') is not True or proof.get('vmid') != 4 or
                proof.get('focus_verified') is not True or proof.get('uid') != SHELL_UID or
                proof.get('kernel') != 'Linux'):
            return None
        nonce = proof.get('nonce')
        start, end = proof.get('raw_start'), proof.get('raw_end')
        if (not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{24}', nonce) or
                type(start) is not int or type(end) is not int or not 0 <= start < end <= 2**48 or
                end - start > 40960):
            return None
        with (session/'vlm-console-rx.bin').open('rb') as stream:
            stream.seek(start)
            wire = stream.read(end - start)
        if len(wire) != end - start or hashlib.sha256(wire).hexdigest() != proof.get('raw_sha256'):
            return None
        marker = f'{MARKER_PREFIX}:{nonce}:{SHELL_UID}:Linux'.encode('ascii')
        source = response_source(wire, marker)
        if source is not None and source == proof.get('response_source'):
            return dict(uid=SHELL_UID, kernel='Linux', response_source=source)
    except (OSError, ValueError, OverflowError, KeyError, TypeError):
        return None
    return None


def verify_live(session, seconds):

    import http.client
    import json
    import re
    import secrets
    import time
    import urllib.parse

    from original_8704_vm import NativeConsoleTap, save

    tap = NativeConsoleTap(session)
    session = tap.session
    policy = json.loads((session/POLICY).read_bytes())
    if policy.get('local_android_shell') is not True:
        raise ValueError('Session was not started with the Android console observer')
    descriptor = json.loads((session/'native-console-server.json').read_bytes())
    uri = urllib.parse.urlsplit(descriptor['url'])
    token = uri.fragment.removeprefix('token=')
    if (uri.scheme != 'http' or uri.netloc != f'127.0.0.1:{uri.port}' or uri.path != '/' or uri.query or
            not uri.port or uri.fragment == token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token)):
        raise ValueError('Invalid native loopback page descriptor')

    def call(path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', uri.port, timeout=8 if data else 4)
        try:
            connection.request('GET' if body is None else 'POST', path, body,
                               {'X-8704-Token': token, 'Content-Type': 'application/json'})
            with connection.getresponse() as reply:
                raw = reply.read((1 << 20) + 1)
                if reply.status != 200 or len(raw) > 1 << 20:
                    raise ValueError(f'Unexpected native page response: HTTP {reply.status}')
                return json.loads(raw)
        finally:
            connection.close()

    nonce = secrets.token_hex(12)
    marker = f'{MARKER_PREFIX}:{nonce}:{SHELL_UID}:Linux'.encode('ascii')
    deadline = time.monotonic() + seconds
    result = {'guest': 'android', 'command_execution_verified': False, 'status': 'shell_prompt_not_observed'}
    capture = session/'vlm-console-rx.bin'
    while time.monotonic() < deadline and not (session/'result.json').exists():
        android = call('/api/status')['android']
        if android['session'] != str(session):
            raise ValueError('Native page is not attached to the selected session')
        text = '\n'.join(android[k] for k in ('console', 'console_pending'))
        if not android['serial_writable'] or 'console:/' not in text:
            time.sleep(.5)
            continue
        with capture.open('rb') as stream:
            offset = stream.seek(0, 2)
            result['status'] = 'command_reply_not_observed'
            line = ("printf '\\n"+MARKER_PREFIX+":%s:%s:%s\\n' '" + nonce +
                    "' \"$(/system/bin/id -u)\" \"$(/system/bin/uname -s)\"")
            sent = call('/api/serial', {'guest': 'android', 'command': line})
            if sent.get('sent') is not True or sent.get('vmid') != 4 or sent.get('focus_verified') is not True:
                raise ValueError('Native page did not confirm command routing to VM4')
            buf = bytearray()
            while time.monotonic() < deadline:
                buf += stream.read(8192)
                source = response_source(buf, marker)
                if source:
                    result.update(status='original_command_executed', command_execution_verified=True,
                                  uid=SHELL_UID, kernel='Linux', response_source=source, nonce=nonce, vmid=4,
                                  focus_verified=True, raw_start=offset, raw_end=offset + len(buf),
                                  raw_sha256=hashlib.sha256(buf).hexdigest())
                    break
                if len(buf) > 32768:
                    newline = buf.find(b'\n', 16384)
                    cut = newline + 1 if newline != -1 else 16384
                    del buf[:cut]
                    offset += cut
                if (session/'result.json').exists():
                    break
                time.sleep(.25)
        break
    save(session/PROOF, result)
    return result


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description='Verify command execution on the OEM Android console.')
    parser.add_argument('session', type=Path)
    parser.add_argument('--seconds', type=int, default=95, metavar='1..7200')
    args = parser.parse_args()
    if type(args.seconds) is not int or not 1 <= args.seconds <= 7200:
        parser.error('--seconds must be 1..7200')
    proof = verify_live(args.session, args.seconds)
    print(json.dumps(proof, ensure_ascii=False))
    raise SystemExit(0 if proof['command_execution_verified'] else 1)
