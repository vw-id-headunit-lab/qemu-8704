
import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def run(session, port, offsets, receive_seconds):
    info=json.loads((session/'native-console-server.json').read_bytes())
    endpoint=urllib.parse.urlsplit(info['url'])
    if endpoint.scheme!='http' or endpoint.netloc!=f'127.0.0.1:{port}' or endpoint.path!='/':
        raise ValueError('Unexpected native page descriptor')
    token=endpoint.fragment.removeprefix('token=')
    if not token or endpoint.query:
        raise ValueError('Missing local page session token')
    focus_file=session/'vlm-console-focus.json'
    capture=session/'live-vh1.jsonl'
    with capture.open('rb') as fp:
        first=json.loads(fp.readline())['host_monotonic']

    def call(path,data=None):
        wire=json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=wire,
                                   headers={'X-8704-Token':token})
        with urllib.request.urlopen(req,timeout=7) as response:
            return json.load(response)

    def elapsed():
        return time.monotonic()-first

    def prompt(text):
        return bool(re.search(r'(?im)(?:^|\n)[^\r\n]*\blogin:[ \t]*(?:\r?$)',text))

    def tagged_prompt(status):
        return prompt(status['console']+'\n'+status.get('console_pending',''))

    def shared_prompt(status):
        return prompt(status.get('shared_uart_unattributed',''))

    with (session/'native-linux-getty-wake-late.jsonl').open('x',encoding='utf-8',buffering=1) as out:
        for offset in offsets:
            while elapsed()<offset:
                focus=json.loads(focus_file.read_bytes())
                if not focus['running']:
                    out.write(json.dumps(dict(result='capture_ended',elapsed=elapsed()))+'\n')
                    return
                time.sleep(min(5,max(.25,offset-elapsed())))
            before=call('/api/status')['linux']
            row=dict(elapsed=elapsed(),bytes_before=len(before['console']),prompt_before=tagged_prompt(before),
                     shared_prompt_before=shared_prompt(before))
            if not before['board_running'] or not before['serial_writable'] or row['prompt_before'] or row['shared_prompt_before']:
                row['result']='not_sent'
            else:
                try:
                    row['wake']=call('/api/native-return',{'guest':'linux'})
                    deadline=time.monotonic()+receive_seconds
                    while True:
                        after=call('/api/status')['linux']
                        row.update(bytes_after=len(after['console']),prompt_after=tagged_prompt(after),
                                   shared_prompt_after=shared_prompt(after))
                        if row['prompt_after'] or row['shared_prompt_after'] or not after['board_running'] or time.monotonic()>=deadline:
                            break
                        time.sleep(.5)
                except urllib.error.HTTPError as exc:
                    row['http_status']=exc.code
            out.write(json.dumps(row)+'\n')
            if row['prompt_before'] or row['shared_prompt_before'] or row.get('prompt_after') or row.get('shared_prompt_after'):
                return


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('session',type=Path)
    p.add_argument('--port',type=int,required=True)
    p.add_argument('--at',nargs='+',type=int,required=True)
    p.add_argument('--receive-seconds',type=int,default=8)
    a=p.parse_args()
    if sorted(set(a.at))!=a.at or not all(60<=n<=1780 for n in a.at) or not 1<=a.receive_seconds<=30:
        p.error('Use ordered distinct probe offsets inside the native capture')
    run(a.session.resolve(strict=True),a.port,a.at,a.receive_seconds)
