import http.client
import json
from pathlib import Path
import sys
import time

state = Path('/tmp/bench-update')
mode = sys.argv[1]

def request(path, port=80):
    connection = http.client.HTTPConnection('::1', port, timeout=1)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        if response.status != 200:
            raise OSError('Original service HTTP ' + str(response.status))
        return json.loads(response.read())
    finally:
        connection.close()

while True:
    event = None
    try:
        if mode == 'normal':
            services = request('/?name=softwareupdate')['data']
            for service in services:
                port = service['port']
                transactions = request('/softwareupdate/softwareTransactions/', port)['data']
                for transaction in transactions:
                    if transaction.get('action') == 'start_rem' and transaction.get('status') == 'pending':
                        event = {'request': 'enter_rem', 'source': 'original-softwareupdate-1.7.2',
                                 'transaction': transaction}
                        break
                if event:
                    break
        elif mode == 'rem':
            text = (state / 'swup-rem.log').read_text(errors='replace')
            if '[SWUP] 6 -> 7' in text:
                event = {'request': 'enter_normal', 'source': 'original-RemMgr',
                         'transition': '6 -> 7', 'meaning': 'Reboot to normal mode selected in original UI'}
        else:
            raise ValueError('Unknown runtime mode')
        if event:
            with (state / 'mode-events.jsonl').open('a') as stream:
                stream.write(json.dumps(event) + '\n')
            with (state / 'reset-command').open('w') as stream:
                stream.write(event['request'] + '\n')
            break
    except (OSError, KeyError, TypeError, ValueError):
        pass
    time.sleep(.25)
