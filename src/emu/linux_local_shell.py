
import hashlib

import linux_early_uart as early


SHELL = early.SHELL
DIRECT_TTY = early.DIRECT_TTY
SHELL_SHA = 'c003aa1fc532253f9792c567b115df4e6abbe3251727dc4b9acbf826972abbdb'
CAPSH_SHA = 'af0f20672db966f894bbf8e48927debc32ba61ebbfdd7c239fb80ce1d4bfa325'
SETSID_SHA = '02f1895bfe781735ce20056383b13affe4205f80f2331a6d22467d4a0da77baa'
BUSYBOX_SHA = 'e4e3516b96c1022d3622e734a1df37072c04ec94cc66cf1dec37f11f2c37c15d'


def validate_factory(root_image):

    from dissect.extfs import ExtFS

    with root_image.open('rb') as disk:
        fs = ExtFS(disk)
        for path, digest in ((SHELL, SHELL_SHA), (early.CAPSH, CAPSH_SHA),
                             (early.SETSID, SETSID_SHA), ('/bin/busybox.nosuid', BUSYBOX_SHA)):
            node = fs.get(path)
            with node.open() as data:
                raw = data.read(1 << 20)
            if (node.filetype != 0x8000 or raw[:6] != b'\x7fELF\x02\x01' or
                    raw[18:20] != b'\xb7\x00' or hashlib.sha256(raw).hexdigest() != digest):
                raise ValueError('The 8704 VM3 early UART input changed: '+path)
        for path in ('/bin/mkdir','/bin/ln'):
            node = fs.get(path)
            with node.open() as data:
                if node.filetype != 0xa000 or data.read(64) != b'/bin/busybox.nosuid':
                    raise ValueError('The 8704 VM3 staging tool changed: '+path)
        for path, name, ids in (('/etc/passwd', b'nobody', [b'65534', b'65534']),
                                ('/etc/group', b'nogroup', [b'65534'])):
            with fs.get(path).open() as data:
                rows = [line.split(b':') for line in data.read(65536).splitlines()
                        if line.partition(b':')[0] == name]
            if len(rows) != 1 or rows[0][2:2+len(ids)] != ids:
                raise ValueError('The 8704 VM3 local shell account or group changed')


def stage_script(tty=early.TTY):
    return early.stage_script(tty)


def response_source(capture, expected, *, dedicated=False):

    from original_8704_vm import native_console_record

    for line in capture.split(b'\n')[:-1]:
        if line.rstrip(b'\r') == expected:
            return early.DIRECT_SOURCE if dedicated else 'shared-physical-uart'
        if dedicated:
            continue
        tagged = native_console_record(line)
        if tagged and tagged == ('linux', expected.decode('ascii')):
            return 'original-vm3-tag'
    return None


def read_verified_proof(session):

    import json
    import re

    def read_json(name, limit):
        with (session/name).open('rb') as stream:
            data=stream.read(limit+1)
        if len(data)>limit:
            raise ValueError('Oversized VM3 proof input')
        parsed=json.loads(data)
        if not isinstance(parsed,dict):
            raise ValueError('Invalid VM3 proof input')
        return parsed

    try:
        policy=read_json('linux-initcall-dtb.json', 32768)
        if policy.get('local_linux_shell') is not True:
            return None
        direct=early.read_direct_binding(session)
        capture=early.DIRECT_CAPTURE if direct else 'vlm-console-rx.bin'
        if not direct and policy.get('local_linux_shell_tty')==DIRECT_TTY:
            return None
        proof=read_json('linux-local-shell-proof.json', 4096)
        routed=(proof.get('route_verified') is True and proof.get('route')==early.DIRECT_SOURCE
                if direct else proof.get('focus_verified') is True)
        if (proof.get('status')!='original_command_executed' or
                proof.get('command_execution_verified') is not True or proof.get('vmid')!=3 or
                not routed or proof.get('uid')!=65534 or proof.get('kernel')!='Linux'):
            return None
        nonce=proof.get('nonce')
        start,end=proof.get('raw_start'),proof.get('raw_end')
        if (not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{24}',nonce) or
                type(start) is not int or type(end) is not int or not 0<=start<end<=2**48 or end-start>40960):
            return None
        with (session/capture).open('rb') as stream:
            stream.seek(start)
            wire=stream.read(end-start)
        if len(wire)!=end-start or hashlib.sha256(wire).hexdigest()!=proof.get('raw_sha256'):
            return None
        marker=f'8704-vm3-check:{nonce}:65534:Linux'.encode('ascii')
        source=response_source(wire,marker,dedicated=bool(direct))
        if source is not None and source==proof.get('response_source'):
            return dict(uid=65534,kernel='Linux',response_source=source)
    except (OSError,ValueError,OverflowError,KeyError,TypeError):
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
    policy = json.loads((session/'linux-initcall-dtb.json').read_bytes())
    if policy.get('local_linux_shell') is not True:
        raise ValueError('Session was not started with the explicit local VM3 shell')
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
                raw = reply.read((1 << 20)+1)
                if reply.status != 200 or len(raw) > 1 << 20:
                    raise ValueError(f'Unexpected native page response: HTTP {reply.status}')
                return json.loads(raw)
        finally:
            connection.close()

    nonce = secrets.token_hex(12)
    marker = f'8704-vm3-check:{nonce}:65534:Linux'.encode('ascii')
    deadline = time.monotonic() + seconds
    result = {'guest': 'linux', 'command_execution_verified': False, 'status': 'shell_prompt_not_observed'}
    direct=tap.direct
    if not direct and policy.get('local_linux_shell_tty')==DIRECT_TTY:
        raise ValueError('Staged ttySAC3 has no verified chardev')
    capture = session/(early.DIRECT_CAPTURE if direct else 'vlm-console-rx.bin')
    while time.monotonic() < deadline and not (session/'result.json').exists():
        linux = call('/api/status')['linux']
        if linux['session'] != str(session):
            raise ValueError('Native page is not attached to the selected session')
        fields=('console','console_pending') if direct else ('console','console_pending','shared_uart_unattributed')
        text = '\n'.join(linux[k] for k in fields)
        if not linux['serial_writable'] or '8704-vm3$' not in text:
            time.sleep(.5)
            continue
        with capture.open('rb') as stream:
            offset=stream.seek(0, 2)
            result['status'] = 'command_reply_not_observed'
            line = "printf '\\n8704-vm3-check:%s:%s:%s\\n' '" + nonce + "' \"$(/usr/bin/id -u)\" \"$(/bin/uname -s)\""
            sent = call('/api/serial', {'guest': 'linux', 'command': line})
            route=(dict(route_verified=True,route=early.DIRECT_SOURCE) if direct else dict(focus_verified=True))
            if sent.get('sent') is not True or sent.get('vmid') != 3 or any(sent.get(k)!=v for k,v in route.items()):
                raise ValueError('Native page did not confirm command routing to VM3')
            buf = bytearray()
            while time.monotonic() < deadline:
                buf += stream.read(8192)
                source = response_source(buf, marker, dedicated=bool(direct))
                if source:
                    result.update(status='original_command_executed', command_execution_verified=True,
                                  uid=65534,kernel='Linux',response_source=source,nonce=nonce,vmid=3,
                                  raw_start=offset,raw_end=offset+len(buf),
                                  raw_sha256=hashlib.sha256(buf).hexdigest(),**route)
                    break
                                                                                         
                if len(buf) > 32768:
                    newline = buf.find(b'\n', 16384)
                    cut=newline+1 if newline!=-1 else 16384
                    del buf[:cut]
                    offset+=cut
                if (session/'result.json').exists():
                    break
                time.sleep(.25)
        break
    save(session/'linux-local-shell-proof.json', result)
    return result


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description='Verify command execution on an explicitly enabled VM3 shell.')
    parser.add_argument('session', type=Path)
    parser.add_argument('--seconds', type=int, default=95, metavar='1..7200')
    args = parser.parse_args()
    if type(args.seconds) is not int or not 1 <= args.seconds <= 7200:
        parser.error('--seconds must be 1..7200')
    proof = verify_live(args.session, args.seconds)
    print(json.dumps(proof, ensure_ascii=False))
    raise SystemExit(0 if proof['command_execution_verified'] else 1)
