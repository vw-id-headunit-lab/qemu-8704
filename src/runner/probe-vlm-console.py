
import argparse
import json
import socket
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--connect-seconds', type=int, default=300)
    args = parser.parse_args()
    session = args.session.resolve()
    metadata = session / 'linux-native-console.json'
    deadline = time.monotonic() + args.connect_seconds
    while not metadata.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError('vLM launch did not stage console metadata')
        time.sleep(0.5)
    endpoint = json.loads(metadata.read_bytes())
    if (endpoint['source'], endpoint['serial_index'], endpoint['host']) != (
            'vlm-console-mux', 1, '127.0.0.1'):
        raise ValueError('Refusing stale or non-vLM console metadata')
    if {guest: item['vmid'] for guest, item in endpoint['focus'].items()} != {
            'qnx': 2, 'linux': 3, 'android': 4}:
        raise ValueError('Unexpected original vLM console routes')
    while True:
        try:
            sock = socket.create_connection(('127.0.0.1', endpoint['port']), timeout=1)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
    with sock, (session / 'vlm-console-rx.bin').open('xb', buffering=0) as wire, (
            session / 'vlm-console-events.jsonl').open('x', encoding='utf-8', buffering=1) as events:
        started = time.monotonic()
        steps = iter([(110, 'linux'), (180, 'android'), (265, 'linux'), (385, 'android'), (485, 'linux')])
        scheduled = next(steps, None)
        while True:
            elapsed = time.monotonic() - started
            if scheduled and elapsed >= scheduled[0]:
                _, guest = scheduled
                key = bytes.fromhex(endpoint['focus'][guest]['keys_hex'][0])
                                                                             
                                                                             
                sock.sendall(key)
                events.write(json.dumps(dict(event='focus_key_sent', guest=guest,
                    elapsed=elapsed, host_monotonic=time.monotonic(), rx_offset=wire.tell(), key_hex=key.hex()))+'\n')
                scheduled = next(steps, None)
            try:
                data = sock.recv(8192)
            except socket.timeout:
                continue
            except ConnectionResetError:
                data = b''
            if not data:
                events.write(json.dumps(dict(event='eof', elapsed=elapsed, rx_bytes=wire.tell()))+'\n')
                break
            wire.write(data)


if __name__ == '__main__':
    main()
