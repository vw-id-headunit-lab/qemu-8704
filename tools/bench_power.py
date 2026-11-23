"""Set explicit stationary power signals in this checkout's offline RSI mockup."""
import argparse
import json
import re
import shlex
from pathlib import Path
from hvf_console import ROOT, execute


def power(action):
    text = execute('cat /mnt/agl/tmp/bench-vehicle.properties')
    paths = dict(re.findall(r'^(keyContact|ignition|comfortReadyState|power)=(/vehicledynamics/[A-Za-z]+/[0-9a-f-]{36})\r?$', text, re.M))
    if len(paths) != 4:
        raise RuntimeError('Offline vehicle services are not ready; retry after guest startup.')
    commands = []
    for key in ('keyContact', 'ignition', 'comfortReadyState', 'power'):
        payload = {'powerstates': 'on' if action == 'on' else 'standby'} if key == 'power' else {'switchValue': 'ON' if action == 'on' else 'OFF'}
        commands.append('echo SIGNAL=' + key + '; chroot /mnt/agl /usr/bin/curl --noproxy "*" -g -sS --max-time 3 -X POST -H "Content-Type: application/json" -d ' + shlex.quote(json.dumps(payload)) + ' -w " HTTP=%{http_code}\\n" ' + shlex.quote('http://[::1]:7770' + paths[key]) + '; echo CURL_EXIT=$?')
    stdout = execute('; '.join(commands), timeout=20)
    result = {'action': action, 'stdout': stdout, 'boundary': 'Explicit offline stationary simulation; not a physical vehicle command.'}
    session = Path(json.loads((ROOT / 'var/hvf/current.json').read_bytes())['session'])
    (session / ('bench-power-' + action + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if stdout.count('HTTP=200') + stdout.count('HTTP=204') == 4 and stdout.count('CURL_EXIT=0') == 4 else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('on', 'off'))
    raise SystemExit(power(parser.parse_args().action))
