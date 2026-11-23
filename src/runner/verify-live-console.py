
import argparse
import json
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/emu'))
import original_8704_vm as vm


def verify(session):
    focus_path=session/'vlm-console-focus.json'
    deadline=time.monotonic()+240
    while True:
        if focus_path.is_file():
            focus=json.loads(focus_path.read_bytes())
            if focus.get('running') and focus.get('vmid') in (3,4):
                break
        if time.monotonic()>=deadline:
            raise TimeoutError('Native vLM did not publish a live Linux/Android focus')
        time.sleep(.25)

    server,guests,token=vm.create_server({},0,native_session=session,native_interactive=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    results={}

    def request(path,body=None):
        raw=json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(urllib.request.Request(base+path,data=raw,
                                    headers={'X-8704-Token':token}),timeout=6) as response:
            return json.load(response)

    try:
        if guests:
            raise ValueError('Native endpoint unexpectedly started a separate guest')
                                                                              
        for guest,vmid in [('android',4),('linux',3)]:
            marker='meb-'+guest+'-'+secrets.token_hex(6)
            command=f"printf '%s\\n' '{marker}'"
            previous=json.loads(focus_path.read_bytes())
            response=request('/api/serial',dict(guest=guest,command=command))
            after=json.loads(focus_path.read_bytes())
            if response!={'sent':True,'focus_verified':True,'vmid':vmid,'execution_verified':False}:
                raise ValueError(f'Unexpected {guest} HTTP delivery result: {response}')
            if after['vmid']!=vmid or after['host_monotonic']<=previous['host_monotonic']:
                raise ValueError(f'No fresh OEM focus readback for {guest}')
            time.sleep(2)
            status=request('/api/status')[guest]
                                                                          
                                                                            
            results[guest]=dict(before=previous,after=after,delivery=response,
                                standalone_marker_observed=marker in status['console'].splitlines())
    finally:
        server.shutdown()
        server.native_console.close()
        server.server_close()
        thread.join(timeout=3)
        vm.save(session/'native-console-http-verification.json',results)
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session',type=Path)
    a=parser.parse_args()
    print(json.dumps(verify(a.session.resolve()),ensure_ascii=False))
