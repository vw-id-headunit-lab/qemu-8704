"""Firmware Python2/3: apply a fixed-format host clock to local RSI Time only."""
from __future__ import print_function
import os
import re
import sys


def decode_sample(line, parse_json):
    if len(line) > 1024:
        raise ValueError('Clock packet exceeds limit')
    sample = parse_json(line)
    if sample.get('source') != 'host_system_clock':
        raise ValueError('Unexpected clock producer')
    for key in ('localDate', 'utcDate'):
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', sample.get(key, '')):
            raise ValueError('Invalid clock date')
    for key in ('localTime', 'utcTime'):
        value = sample.get(key, '')
        if not re.match(r'^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ?$', value):
            raise ValueError('Invalid clock time')
    offset = sample.get('timeZoneOffset')
    if type(offset) is not int or not -720 <= offset <= 840:
        raise ValueError('Invalid clock zone offset')
    if type(sample.get('dstActive')) is not bool:
        raise ValueError('Invalid clock DST')
    return sample


def update(path, body):
    # Both arguments are generated/validated locally. Never accepts a URL or
    # shell command from the clock stream. Path is a previously discovered UUID.
    command = ("/usr/bin/curl -g -f -sS --noproxy '*' --connect-timeout 1 --max-time 2 "
               "-H 'Content-Type: application/json' -X POST -d '%s' "
               "'http://[::1]:7770%s' >/dev/null 2>&1") % (body, path)
    if os.system(command) != 0:
        raise ValueError('Local Time update failed')


def main():
    helpers = {'__name__': 'host_clock_readiness'}
    with open('/tmp/bench-readiness.py') as source:
        eval(compile(source.read(), '/tmp/bench-readiness.py', 'exec'), helpers)
    parse_json = helpers['parse_json']
    paths = {}
    for row in helpers['get_json'](7770, '/Time/times/'):
        if row.get('type') in ('CustomerTime', 'UTC'):
            paths[row['type']] = row.get('uri', '')
    rows = helpers['get_json'](7770, '/Time/timeSettings/')
    paths['settings'] = rows[0].get('uri', '') if rows else ''
    for kind, path in paths.items():
        resource = 'timeSettings' if kind == 'settings' else 'times'
        if not re.match(r'^/Time/%s/[0-9a-f-]{36}$' % resource, path):
            raise ValueError('Unexpected local Time resource')
    if set(paths) != set(('CustomerTime', 'UTC', 'settings')):
        raise ValueError('Missing Time fixture objects')
    previous = None
    count = 0
    device = sys.argv[1]
    if not re.match(r'^/dev/vport\d+p\d+$', device):
        raise ValueError('Invalid host clock device')
    while True:
        with open(device, 'r') as stream:
            while True:
                line = stream.readline(1025)
                if not line:
                    break
                try:
                    sample = decode_sample(line, parse_json)
                    if previous != (sample['timeZoneOffset'], sample['dstActive']):
                        update(paths['settings'], '{"timeMode":"automatic","timeZoneOffset":%d,"dstActive":%s}' % (
                            sample['timeZoneOffset'], 'true' if sample['dstActive'] else 'false'))
                        previous = (sample['timeZoneOffset'], sample['dstActive'])
                    for kind, date, time in (('CustomerTime', sample['localDate'], sample['localTime']),
                                             ('UTC', sample['utcDate'], sample['utcTime'])):
                        update(paths[kind], '{"date":"%s","time":"%s"}' % (date, time))
                    count += 1
                    if count == 1:
                        with open('/tmp/host-clock.ready', 'w') as ready:
                            ready.write('host_system_clock')
                    if count == 1 or count % 60 == 0:
                        print('HOST_CLOCK_APPLIED local=%sT%s offset_minutes=%d samples=%d' % (
                            sample['localDate'], sample['localTime'], sample['timeZoneOffset'], count))
                        sys.stdout.flush()
                except (ValueError, IOError, KeyError, TypeError) as error:
                    print('HOST_CLOCK_SAMPLE_FAILED ' + str(error))
                    sys.stdout.flush()
        helpers['time'].sleep(.5)


if __name__ == '__main__':
    main()
