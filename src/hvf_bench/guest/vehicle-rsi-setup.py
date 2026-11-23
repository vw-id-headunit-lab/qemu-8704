"""Allow a local simulator producer to update the RAM-only vehicleStates mock."""
from __future__ import print_function
import re
import sys


def encode_json(value):
    if isinstance(value, dict):
        return '{' + ','.join(encode_json(key) + ':' + encode_json(item)
                              for key, item in sorted(value.items())) + '}'
    if isinstance(value, list):
        return '[' + ','.join(encode_json(item) for item in value) + ']'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + re.sub(r'["\\\x00-\x1f]',
        lambda match: '\\u%04x' % ord(match.group(0)), value) + '"'


def allow_producer_updates(spec):
    resource = spec['resources']['vehicleStates']
    resource['endpoints']['element']['post'] = {
        'description': 'Local stationary bench signal producer',
        'parameters': {'method': 'post', 'resource': 'vehicledynamics.vehicleStates',
            'properties': {'switchValue': {'type': 'string', 'enum': ['ON', 'OFF']}},
            'required': ['switchValue']}}
    spec['resources']['powermanagement']['endpoints']['element']['post'] = {
        'description': 'Local stationary bench power producer',
        'parameters': {'method': 'post', 'resource': 'vehicledynamics.powermanagement',
            'properties': {'powerstates': {'type': 'string', 'enum': ['on', 'standby']}},
            'required': ['powerstates']}}


if __name__ == '__main__':
    helpers = {'__name__': 'vehicle_setup_readiness'}
    with open('/tmp/bench-readiness.py') as source:
        eval(compile(source.read(), '/tmp/bench-readiness.py', 'exec'), helpers)
    path = sys.argv[1]
    if path != '/tmp/bench-spec/viwi.service.vehicledynamics/viwi.service.vehicledynamics-0.2.2.json':
        raise ValueError('Unexpected RAM mock specification')
    with open(path) as source:
        spec = helpers['parse_json'](source.read())
    allow_producer_updates(spec)
    with open(path, 'w') as target:
        target.write(encode_json(spec))
    print('VEHICLE_RSI_RAM_SPEC_READY')
