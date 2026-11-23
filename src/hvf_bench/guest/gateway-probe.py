"""Firmware-Python-compatible guest gateway readiness. Uses shipped curl only."""
from __future__ import print_function
import os
import sys
readiness = {'__name__': 'gateway_prerequisite'}
with open('/tmp/bench-readiness.py') as source:
    eval(compile(source.read(), '/tmp/bench-readiness.py', 'exec'), readiness)

def fetch(url, proxy=None, sdk=False, timeout=8):
    command = "/usr/bin/curl -g -f -sS -i --raw --connect-timeout 2 --max-time %d " % timeout
    command += "--noproxy '' --proxy '%s' " % proxy if proxy else "--noproxy '*' "
    if sdk:
        command += "-H 'Rsi-Time: 20261114120000' -H 'Rsi-Time-Hash: SIMULATION' "
        command += "-H 'Rsi-Host: fd53:7cb8:383:3::108#30001' -H 'Rsi-Target-Path: /gnss/simple/?$expand=1' "
    command += "'%s' 2>/dev/null" % url
    stream = os.popen(command)
    data = stream.read(262145)
    status = stream.close()
    if status or len(data) > 262144:
        raise ValueError('Gateway probe request failed: ' + str(status))
    if not isinstance(data, bytes): data = data.encode('utf-8')
    # Health documents have no RSI wrapper; parse after HTTP headers directly.
    headers, sep, body = data.partition(b'\r\n\r\n')
    if not sep or b' 200 ' not in headers.split(b'\r\n')[0]:raise ValueError('Non-200 probe')
    if b'transfer-encoding: chunked' in headers.lower():
        chunks=[]
        while True:
            line, sep, body=body.partition(b'\r\n');n=int(line,16)
            if n==0:body=b''.join(chunks);break
            chunks.append(body[:n]);body=body[n+2:]
    return readiness['parse_json'](body.decode('utf-8'))

try:
    for port in (50006,50007):
        proxy='http://[::1]:%d'%port
        health={}
        deadline=readiness['steady_time']()+10
        while readiness['steady_time']()<deadline:
            try:
                health=fetch(proxy+'/__ivi_proxy_health',timeout=2)
                if health.get('program')=='ivi-go-gateway' and health.get('catalog',{}).get('mode')=='direct':break
            except ValueError:
                pass
            readiness['time'].sleep(.1)
        if health.get('program')!='ivi-go-gateway' or health.get('catalog',{}).get('mode')!='direct':
            raise ValueError('Unexpected gateway identity/mode')
        result=fetch('http://[::1]:30001/gnss/simple/?$expand=1',proxy)
        if result.get('simulation') is not True or not result.get('data'):raise ValueError('Simulator route unavailable')
        print('GUEST_GATEWAY_PROXY_HTTP_OK port=%d simulation=true'%port)
    if os.path.isfile('/tmp/sdk-relay.ready'):
        result=fetch('http://[::1]:50005/gateway/clients/00000000-0000-0000-0000-000000000001',sdk=True)
        if result.get('simulation') is not True or not result.get('data'):raise ValueError('SDK simulator route unavailable')
        print('GUEST_SDK_GATEWAY_HTTP_OK port=50005 simulation=true time_policy=shape-only')
    print('GUEST_GATEWAY_PROBE_PASS')
except Exception as error:
    print('GUEST_GATEWAY_PROBE_FAILED '+str(error),file=sys.stderr)
    sys.exit(1)
