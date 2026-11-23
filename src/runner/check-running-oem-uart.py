
import argparse
import json
from pathlib import Path
import re
import secrets
import socket
import time


def load(path):
    return json.loads(path.read_bytes())


def run(session, port):
    original = session / 'linux-native-console.json'
    live = session / 'vlm-console-focus.json'
    begin = time.monotonic()
    while True:
        if live.is_file() and original.is_file():
            now = load(live)
            if now.get('running') and now.get('vmid') in (2,3,4):
                break
        if time.monotonic()-begin > 300:
            raise TimeoutError('No live focus from this OEM session')
        time.sleep(.5)
    cfg = load(original)
    if (cfg.get('source'),cfg.get('serial_index'),cfg.get('host'),cfg.get('port')) != (
            'vlm-console-mux',1,'127.0.0.1',port):
        raise ValueError('Unexpected OEM UART route')
    if {k: v.get('vmid') for k,v in cfg['focus'].items()} != {'qnx':2,'linux':3,'android':4}:
        raise ValueError('Unexpected OEM UART focus keys')
    if (session / 'native-console-server.json').is_file():
        raise ValueError('A page already owns the UART')
    results={}
    marker='meb-qnx-'+secrets.token_hex(6)
    with socket.create_connection(('127.0.0.1',port),timeout=3) as sock:
        sock.settimeout(.25)

        def recv(seconds):
            end=time.monotonic()+seconds
            data=bytearray()
            while time.monotonic()<end:
                try:
                    b=sock.recv(65536)
                except TimeoutError:
                    continue
                if not b:
                    break
                data.extend(b)
                if len(data)>1024*1024:
                    raise ValueError('UART observation bounded at 1 MiB')
            return bytes(data)

        for guest in ('qnx','android','linux'):
            focus=cfg['focus'][guest]
            previous=load(live)
            if not previous.get('running'):
                raise ValueError('Native VM stopped during UART check')
            sock.sendall(bytes.fromhex(focus['keys_hex'][0]))
            end=time.monotonic()+6
            while True:
                fresh=load(live)
                if (fresh.get('running') and not fresh.get('read_only')
                    and fresh.get('vmid')==focus['vmid']
                    and fresh['host_monotonic']>previous['host_monotonic']):
                    break
                if time.monotonic()>end:
                    raise TimeoutError('Original vLM focus did not confirm '+guest)
                time.sleep(.1)
            recv(.5)                                                     
            sock.sendall(b'\r')
            wake=recv(6)
            response=b''
            if guest=='qnx':
                sock.sendall(f"printf '%s\\n' '{marker}'\r".encode('ascii'))
                response=recv(6)
            with (session / ('oem-uart-'+guest+'.bin')).open('xb') as f:
                f.write(wake+response)
                                                                        
            plain=re.sub(rb'(?m)^\d+ \d+ \d+:', b'', wake+response)
            results[guest]=dict(focus=fresh,cr_bytes=len(wake),command_bytes=len(response),
                login_prompt=bool(re.search(rb'(?im)(?:^|\r|\n)[^\r\n]*\blogin: ?',plain)),
                exact_execution_reply=marker.encode() in plain.splitlines() if guest=='qnx' else None)
    with (session/'oem-uart-wake-result.json').open('x',encoding='utf-8') as f:
        json.dump(results,f,indent=2)
    return results


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('session',type=Path)
    p.add_argument('--port',type=int,required=True)
    a=p.parse_args()
    print(json.dumps(run(a.session.resolve(strict=True),a.port)))
