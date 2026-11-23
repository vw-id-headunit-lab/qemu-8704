"""Bounded readiness checks, compatible with the firmware Python 2.7 and Python 3.

Only observes the guest's own IPv6 address, registry and original mockup. Empty
resource collections are valid; a listening socket alone is not readiness.
"""
from __future__ import print_function
import os
import re
import sys
import time


def steady_time():
    return time.monotonic() if hasattr(time, 'monotonic') else os.times()[4]


HTTP_DEADLINE = None


def parse_json(text):
    """Strict small JSON reader: firmware lacks the optional stdlib json package."""
    token = re.compile(r'\s*("(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|[{}\[\]:,])')
    tokens = []
    position = 0
    while position < len(text):
        match = token.match(text, position)
        if not match:
            if not text[position:].strip():
                break
            raise ValueError('Malformed JSON token')
        tokens.append(match.group(1)); position = match.end()
    index = [0]
    def take():
        if index[0] >= len(tokens):
            raise ValueError('Truncated JSON')
        value = tokens[index[0]]; index[0] += 1
        return value
    def string(value):
        escapes = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}
        unicode_char = chr if sys.version_info[0] >= 3 else unichr
        return re.sub(r'\\(u[0-9a-fA-F]{4}|["\\/bfnrt])',
                      lambda m: unicode_char(int(m.group(1)[1:], 16)) if m.group(1).startswith('u') else escapes[m.group(1)], value[1:-1])
    def value(depth=0):
        if depth > 32:
            raise ValueError('JSON nesting limit')
        item = take()
        if item in ('[', '{'):
            closing = ']' if item == '[' else '}'
            result = [] if item == '[' else {}
            if index[0] < len(tokens) and tokens[index[0]] == closing:
                take(); return result
            while True:
                if item == '{':
                    name = take()
                    if not name.startswith('"') or take() != ':':
                        raise ValueError('Invalid JSON key')
                    name = string(name)
                    if name in result:
                        raise ValueError('Duplicate JSON key')
                    result[name] = value(depth + 1)
                else:
                    result.append(value(depth + 1))
                separator = take()
                if separator == closing:
                    return result
                if separator != ',':
                    raise ValueError('Invalid JSON separator')
        if item.startswith('"'):
            return string(item)
        if item in ('true', 'false', 'null'):
            return {'true': True, 'false': False, 'null': None}[item]
        if item[0] in '-0123456789':
            return float(item) if any(c in item for c in '.eE') else int(item)
        raise ValueError('Invalid JSON value')
    result = value()
    if index[0] != len(tokens):
        raise ValueError('Trailing JSON')
    return result


def alive(pid):
    try:
        os.kill(pid, 0)
        with open('/proc/%d/stat' % pid) as stream:
            state = stream.read().rsplit(')', 1)[1].split()[0]
        return state != 'Z'
    except (OSError, IndexError):
        return False


def address_ready(path='/proc/net/if_inet6'):
    expected = 'fd537cb8038300030000000000000108'
    with open(path) as stream:
        for line in stream:
            fields = line.split()
            if len(fields) == 6 and fields[0] == expected and fields[5] == 'eth0.3':
                return not (int(fields[4], 16) & (0x40 | 0x08))  # tentative / DAD failed
    return False


def decode_http(raw):
    headers, separator, body = raw.partition(b'\r\n\r\n')
    if not separator or not headers.split(b'\r\n')[0].startswith((b'HTTP/1.0 200 ', b'HTTP/1.1 200 ')):
        raise ValueError('HTTP response not 200')
    fields = dict(line.lower().split(b':', 1) for line in headers.split(b'\r\n')[1:] if b':' in line)
    if fields.get(b'transfer-encoding', b'').strip() == b'chunked':
        chunks = []
        while True:
            size_line, sep, body = body.partition(b'\r\n')
            if not sep:
                raise ValueError('Truncated chunk header')
            size = int(size_line.split(b';')[0], 16)
            if size == 0:
                body = b''.join(chunks); break
            if len(body) < size + 2 or body[size:size+2] != b'\r\n':
                raise ValueError('Truncated HTTP chunk')
            chunks.append(body[:size]); body = body[size+2:]
    elif b'content-length' in fields:
        if len(body) != int(fields[b'content-length']):
            raise ValueError('HTTP length mismatch')
    value = parse_json(body.decode('utf-8'))
    if not isinstance(value, dict) or value.get('status') != 'ok' or not isinstance(value.get('data'), list):
        raise ValueError('Invalid RSI envelope')
    return value['data']


def get_json(port, path):
    # The firmware Python is also missing _socket. Its shipped curl is complete.
    # Only fixed local ports/paths are accepted, never user strings in a shell.
    if port not in (80, 7770) or not re.match(r'^/[A-Za-z/]*/?$', path):
        raise ValueError('Unexpected readiness endpoint')
    budget = 2.0 if HTTP_DEADLINE is None else min(2.0, HTTP_DEADLINE - steady_time())
    if budget <= 0:
        raise ValueError('HTTP readiness deadline exceeded')
    command = "/usr/bin/curl --noproxy '*' -g -f -sS -i --raw --connect-timeout %.3f --max-time %.3f 'http://[::1]:%d%s' 2>/dev/null" % (min(1.0, budget), max(0.001, budget), port, path)
    stream = os.popen(command)
    try:
        data = getattr(stream, 'buffer', stream).read(2 * 1024 * 1024 + 1)
    finally:
        status = stream.close()
    if status or len(data) > 2 * 1024 * 1024:
        raise ValueError('HTTP response failed port=%d path=%s status=%s bytes=%d' % (port, path, status, len(data)))
    if not isinstance(data, bytes):
        data = data.encode('utf-8')
    return decode_http(data)


def check(mode, expected):
    if mode == 'network':
        return address_ready()
    if mode == 'registry':
        # Registry has no service document before the first provider registers.
        # This is ONLY the prerequisite for launching that provider. The later
        # services gate validates registry JSON plus every required data plane.
        with open('/proc/net/tcp6') as stream:
            return any(len(row) > 3 and row[1] == '00000000000000000000000001000000:0050'
                       and row[3] == '0A' for row in (line.split() for line in stream))
    registry = get_json(80, '/')
    services = dict((row.get('name'), row) for row in registry if isinstance(row, dict))
    for name in expected:
        row = services.get(name, {})
        if row.get('port') != 7770 or not row.get('versions'):
            return False
    # Read the data plane too: registration may precede HTTP resource readiness.
    for path in ('/language/settings/', '/language/strategies/', '/language/components/',
                 '/displayintentcenter/clients/', '/displayintentcenter/entryPoints/',
                 '/displayintentcenter/displayIntents/'):
        data = get_json(7770, path)
        if path.startswith('/language/') and not data:
            return False
    if 'hvac' in expected:
        rows = get_json(7770, '/hvac/rows/')
        zones = get_json(7770, '/hvac/zones/')
        controls = get_json(7770, '/hvac/valueControls/')
        by_id = dict((row.get('id'), row) for row in controls)
        if not rows or len(zones) < 2:
            return False
        for side in ('leftSide', 'rightSide'):
            zone = next((z for z in zones if z.get('location') == side), {})
            control = by_id.get(zone.get('temperature', {}).get('id'), {})
            if control.get('unitTemperature') != 'C' or not isinstance(control.get('currentValue'), (int, float)):
                return False
    if 'Time' in expected:
        times = get_json(7770, '/Time/times/')
        settings = get_json(7770, '/Time/timeSettings/')
        if set(row.get('type') for row in times) != set(('CustomerTime', 'UTC')) or not settings:
            return False
    return True


def wait_ready(mode, expected, pids, timeout, clock=None, sleep=time.sleep,
               checker=check, process_alive=alive):
    clock = clock or steady_time
    start = clock()
    last = 'condition not met'
    while True:
        if any(not process_alive(pid) for pid in pids):
            raise RuntimeError('required process exited')
        try:
            if checker(mode, expected):
                # The probe must not outlive its deadline or accept a dying process.
                if clock() - start > timeout:
                    raise RuntimeError('readiness deadline exceeded')
                if any(not process_alive(pid) for pid in pids):
                    raise RuntimeError('required process exited during probe')
                return clock() - start
        except (IOError, ValueError) as error:
            last = str(error)
        if clock() - start >= timeout:
            raise RuntimeError('readiness timeout: ' + last)
        sleep(min(0.1, timeout - (clock() - start)))


def main():
    global HTTP_DEADLINE
    try:
        args = sys.argv[1:]
        mode = args.pop(0)
        if mode not in ('network', 'registry', 'services') or len(args) % 2:
            raise ValueError('expected mode and option/value pairs')
        pids = []; timeout = 30; services_file = '/tmp/bench-full-mockup/services'
        while args:
            key, val = args[:2]; args = args[2:]
            if key == '--pid': pids.append(int(val))
            elif key == '--timeout': timeout = float(val)
            elif key == '--services-file': services_file = val
            else: raise ValueError('unknown option: ' + key)
        if not 0 < timeout <= 60 or any(pid <= 0 for pid in pids):
            raise ValueError('invalid timeout or pid')
        expected = []
        if mode == 'services':
            with open(services_file) as stream:
                expected = [line.strip().split(':', 1)[0] for line in stream
                            if line.strip() and not line.lstrip().startswith('#')]
            if not expected: raise ValueError('empty service list')
        HTTP_DEADLINE = steady_time() + timeout
        duration = wait_ready(mode, expected, pids, timeout)
        print('PLATFORM_READY_CHECK=%s elapsed=%.3f' % (mode, duration))
        return 0
    except Exception as error:
        print('PLATFORM_READY_FAILED reason=%s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
