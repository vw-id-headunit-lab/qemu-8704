"""In-memory 50005 Android SDK gateway fixture, forwarding only to local RSI mock.

This implements the observed routing/registration/subscription structure, not
the proprietary time digest or a drop-in authentication implementation.
"""
import argparse
import asyncio
from dataclasses import dataclass, field
import ipaddress
import json
from pathlib import Path
import re
import signal
import time
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web


CLIENTS = '/gateway/clients/'
MAX_CLIENTS = 128
MAX_SOCKETS = 32
MAX_SUBSCRIPTIONS = 16
MAX_BRIDGES = 64
MAX_RESPONSE = 1024 * 1024
STATE_KEY = web.AppKey('sdk_gateway_state', object)
SESSION_KEY = web.AppKey('sdk_gateway_session', ClientSession)
LOGICAL_HOSTS = frozenset(('localhost', '127.0.0.1', '::1', '::108',
                           'fd53:7cb8:383:3::108', '10.0.2.100'))
VERSION = re.compile(r'^/api/v(?:1|1\.0|1\.0\.16)(?=/|$)')


class GatewayError(Exception):
    def __init__(self, status, message, gateway_code=None):
        super().__init__(message)
        self.status, self.gateway_code = status, gateway_code


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def backend_url(origin):
    """Pin the configured origin to a literal loopback address; no DNS/proxy."""
    u = urlsplit(origin)
    if (u.scheme != 'http' or not loopback(u.hostname) or not u.port
            or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment):
        raise ValueError('backend_origin must be an HTTP literal loopback origin with a port')
    return origin.rstrip('/')


def local_uri(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise GatewayError(400, 'Expected a local RSI URI of at most 2048 characters')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise GatewayError(400, 'Control characters in RSI URI')
    u = urlsplit(value)
    path = unquote(u.path)
    if (u.scheme or u.netloc or u.fragment or not u.path.startswith('/') or
            '%' in u.path or '\\' in path or path.startswith('//') or '..' in path.split('/')):
        raise GatewayError(400, 'Target/event must be a local resource URI without traversal')
    return value


def logical_target(value, subscription=False):
    if not isinstance(value, str) or len(value) > 256 or value.count('#') != 1:
        raise GatewayError(403, 'Expected Rsi-Host logical-host#30001', 4)
    host, port = value.split('#')
    # U is present in the Android SDK subscribeInfo literals. Its transport
    # semantics are not invented here; the fixture accepts the observed marker.
    if subscription and port.endswith('U'):
        port = port[:-1]
    if host.lower().strip('[]') not in LOGICAL_HOSTS or port != '30001':
        raise GatewayError(403, 'Logical target is not the local simulated 30001 service', 8)


def parse_sub(value):
    if not isinstance(value, str) or len(value) > 2306:
        raise GatewayError(400, 'sub must be a bounded subscribeInfo string')
    parts = value.split('#', 2)
    if len(parts) != 3:
        raise GatewayError(400, 'sub format: logical-host#30001U#/resource/URI')
    logical_target('#'.join(parts[:2]), subscription=True)
    return local_uri(parts[2])


def resource_path(path):
    return VERSION.sub('', path) or '/'


def client_id(path):
    if path in (CLIENTS, CLIENTS[:-1]):
        return None
    if not path.startswith(CLIENTS):
        raise GatewayError(404, 'Only gateway client resources are simulated')
    tail = path[len(CLIENTS):]
    try:
        if str(UUID(tail)) != tail.lower():
            raise ValueError()
    except ValueError:
        raise GatewayError(400, 'Gateway element identifier must be a UUID')
    return tail.lower()


@dataclass
class ClientElement:
    id: str
    sub: str
    event: str
    latest: dict = field(default_factory=lambda: {'simulation': True})
    watchers: set = field(default_factory=set)

    @property
    def uri(self):
        return CLIENTS + self.id


class GatewayState:
    def __init__(self, backend_origin, header_policy):
        self.backend_origin = backend_origin
        self.header_policy = header_policy
        self.clients = {}
        self.sockets = set()
        self.handlers = {}
        self.bridges = set()
        self.started = time.monotonic()

    def envelope(self, data, **extra):
        return dict(status='ok', data=data, simulation=True, **extra)

    def health(self):
        return self.envelope(dict(program='hmi-sdk-gateway-simulator', logicalPort=50005,
            backendOrigin=self.backend_origin, simulatedBackendPort=30001,
            headerPolicy=self.header_policy, vendorTimeDigestVerified=False,
            clients=len(self.clients), sockets=len(self.sockets), bridges=len(self.bridges),
            externalEffects=False))


def check_time(request, policy):
    if policy == 'off':
        return
    stamp, digest = request.headers.get('Rsi-Time'), request.headers.get('Rsi-Time-Hash')
    if stamp is None:
        raise GatewayError(403, 'Missing Rsi-Time (simulation shape-only policy)', 10)
    if not digest:
        raise GatewayError(403, 'Missing Rsi-Time-Hash (simulation shape-only policy)', 11)
    if not re.fullmatch(r'[0-9]{14}', stamp):
        raise GatewayError(403, 'Rsi-Time must be 14 ASCII digits in this fixture', 12)
    if len(digest) > 128:
        raise GatewayError(403, 'Rsi-Time-Hash exceeds fixture limit', 13)
    # Deliberately no digest equality/freshness claim. This is not vendor auth.


async def json_body(request):
    body = await request.json()
    if not isinstance(body, dict):
        raise GatewayError(400, 'Expected a JSON object')
    return body


def make_app(backend_origin='http://127.0.0.1:30001', *, header_policy='shape-only'):
    """Create an aiohttp app; caller owns AppRunner, loopback binding and cleanup.

    shape-only requires time headers but does NOT validate the vendor digest.
    off is an explicit diagnostic mode; neither policy represents authentication.
    """
    origin = backend_url(backend_origin)
    if header_policy not in ('shape-only', 'off'):
        raise ValueError('header_policy must be shape-only or off')
    state = GatewayState(origin, header_policy)

    @web.middleware
    async def boundary(request, handler):
        try:
            if request.remote and not loopback(request.remote):
                raise GatewayError(403, 'Loopback clients only', 15)
            if request.headers.get('Origin'):
                u = urlsplit(request.headers['Origin'])
                if not loopback(u.hostname):
                    raise GatewayError(403, 'Non-loopback Origin rejected')
            response = await handler(request)
        except GatewayError as error:
            response = web.json_response(dict(status='error', code=error.status,
                gatewayError=error.gateway_code, message=str(error), simulation=True), status=error.status)
        except (ValueError, TypeError, json.JSONDecodeError):
            response = web.json_response(dict(status='error', code=400,
                message='Invalid JSON/value', simulation=True), status=400)
        response.headers.update({'X-HMI-Bench-Simulation': 'true',
            'X-HMI-Bench-Time-Policy': header_policy, 'Cache-Control': 'no-store'})
        return response

    app = web.Application(middlewares=[boundary], client_max_size=256 * 1024)
    app[STATE_KEY] = state

    async def cancel_tasks(tasks):
        tasks = list(tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def verify_backend():
        async with app[SESSION_KEY].get(origin + '/__simulation/health', allow_redirects=False) as r:
            if r.status != 200 or r.headers.get('X-HMI-Bench-Simulation') != 'true':
                raise GatewayError(502, 'Configured backend did not identify as the local simulator')
            data = json.loads(await limited_read(r))
            if not isinstance(data, dict) or data.get('simulation') is not True:
                raise GatewayError(502, 'Backend simulation health marker missing')

    async def forward(request):
        if request.method not in ('GET', 'POST', 'DELETE'):
            raise GatewayError(403, 'RSI SDK updates use wire POST, not HTTP PUT')
        logical_target(request.headers.get('Rsi-Host'))
        target = local_uri(request.headers['Rsi-Target-Path'])
        dtype = request.headers.get('Rsi-Data-Type', 'json').lower()
        if dtype not in ('json', 'application/json'):
            raise GatewayError(501, 'Only JSON is simulated; no file or stream access')
        payload = await request.read() if request.method == 'POST' else None
        try:
            await verify_backend()
            async with app[SESSION_KEY].request(request.method, origin + target, data=payload,
                    headers={'Content-Type': 'application/json'}, allow_redirects=False) as r:
                if r.headers.get('X-HMI-Bench-Simulation') != 'true':
                    raise GatewayError(502, 'Configured backend is not the local RSI simulator')
                data = await limited_read(r)
                if 300 <= r.status < 400:
                    raise GatewayError(502, 'Backend redirects are not followed')
                headers = {'Content-Type': r.headers.get('Content-Type', 'application/json')}
                return web.Response(body=data, status=r.status, headers=headers)
        except (ClientError, asyncio.TimeoutError):
            raise GatewayError(502, 'Local simulated backend did not respond')

    async def limited_read(response):
        chunks, length = [], 0
        async for chunk in response.content.iter_chunked(65536):
            length += len(chunk)
            if length > MAX_RESPONSE:
                raise GatewayError(502, 'Backend response exceeds fixture limit')
            chunks.append(chunk)
        return b''.join(chunks)

    async def handle(request):
        path = resource_path(request.path)
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            if path != '/':
                raise GatewayError(404, 'Gateway WS endpoint is / or /api/v1')
            return await websocket(request)
        if request.method == 'GET' and path == '/__simulation/health':
            return web.json_response(state.health())
        if request.method == 'GET' and path == '/':
            return web.json_response(state.envelope([dict(name='gateway', uri='/gateway/',
                port=50005, versions=['1.0.16'], simulation=True)], version='1.0.16'))
        if request.method == 'GET' and path == '/gateway/':
            return web.json_response(state.envelope([dict(name='clients', uri=CLIENTS,
                simulation=True)]))
        identifier = client_id(path)
        check_time(request, header_policy)
        if 'Rsi-Target-Path' in request.headers:
            return await forward(request)
        if 'Rsi-Host' in request.headers:
            raise GatewayError(403, 'Missing Rsi-Target-Path for logical forwarding', 5)
        if request.method == 'GET':
            if identifier is None:
                return web.json_response(state.envelope([dict(id=e.id, uri=e.uri, sub=e.sub,
                    simulation=True) for e in state.clients.values()]))
            entry = state.clients.get(identifier)
            if entry is None:
                raise GatewayError(404, 'Gateway client element not present')
            # Original toJSON returns the most recent inner backend message.
            return web.json_response(entry.latest)
        if request.method == 'POST' and identifier is None:
            body = await json_body(request)
            if set(body) != {'sub'}:
                raise GatewayError(400, 'Registration expects only the sub string')
            event = parse_sub(body['sub'])
            if len(state.clients) >= MAX_CLIENTS:
                raise GatewayError(503, 'Gateway client element limit reached')
            entry = ClientElement(str(uuid4()), body['sub'], event)
            state.clients[entry.id] = entry
            return web.json_response(state.envelope(dict(id=entry.id, uri=entry.uri, sub=entry.sub)),
                                     status=201, headers={'Location': entry.uri})
        if request.method == 'DELETE' and identifier is not None:
            entry = state.clients.pop(identifier, None)
            if entry is None:
                raise GatewayError(404, 'Gateway client element not present')
            await cancel_tasks(entry.watchers)
            return web.json_response(state.envelope(dict(id=identifier), effects='memory_only'))
        raise GatewayError(403, 'Local element update/stream/method is not modeled')

    async def websocket(request):
        if len(state.sockets) >= MAX_SOCKETS:
            raise GatewayError(503, 'Gateway socket limit reached')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        ws.headers.update({'X-HMI-Bench-Simulation': 'true', 'X-HMI-Bench-Time-Policy': header_policy})
        state.sockets.add(ws)
        state.handlers[ws] = asyncio.current_task()
        try:
            await ws.prepare(request)
        except BaseException:
            state.sockets.discard(ws)
            state.handlers.pop(ws, None)
            raise
        tasks = {}

        async def pump(outer_event, entry, options):
            try:
                await verify_backend()
                async with app[SESSION_KEY].ws_connect(origin + '/', heartbeat=20,
                        max_msg_size=MAX_RESPONSE) as upstream:
                    await upstream.send_json(dict(type='subscribe', event=entry.event, **options))
                    async for message in upstream:
                        if message.type != WSMsgType.TEXT:
                            continue
                        inner = json.loads(message.data)
                        if not isinstance(inner, dict):
                            raise GatewayError(502, 'Invalid backend WS object')
                        if inner.get('type') == 'subscribe':
                            continue
                        if inner.get('type') == 'error':
                            await ws.send_json(dict(type='error', event=outer_event, uri=entry.uri,
                                code=inner.get('code', 502), data=inner, simulation=True))
                            break
                        if inner.get('event') != entry.event or 'data' not in inner:
                            continue
                        # Preserve every backend field; the authored 30001 mock
                        # lacks uri, so supply it only when absent.
                        inner.setdefault('uri', urlsplit(entry.event).path)
                        entry.latest = inner
                        await ws.send_json(dict(type='data', event=outer_event, uri=entry.uri,
                            data=inner, simulation=True))
            except asyncio.CancelledError:
                raise
            except (ClientError, asyncio.TimeoutError, ValueError, GatewayError) as error:
                if not ws.closed:
                    await ws.send_json(dict(type='error', event=outer_event, uri=entry.uri,
                        code=502, message=str(error), simulation=True))

        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                event = ''
                try:
                    body = json.loads(message.data)
                    if not isinstance(body, dict):
                        raise GatewayError(400, 'Expected WS JSON object')
                    event = local_uri(body.get('event'))
                    identifier = client_id(resource_path(urlsplit(event).path))
                    if identifier is None:
                        raise GatewayError(403, 'WS subscription requires a gateway element ID', 6)
                    entry = state.clients.get(identifier)
                    if entry is None:
                        raise GatewayError(410, 'Gateway client element no longer exists', 7)
                    action = body.get('type')
                    if action == 'unsubscribe':
                        old = tasks.pop(event, None)
                        if old:
                            await cancel_tasks([old])
                        await ws.send_json(dict(type='unsubscribe', event=event, status='ok', simulation=True))
                        continue
                    if action != 'subscribe':
                        raise GatewayError(400, 'Only subscribe/unsubscribe are simulated')
                    options = {k: body[k] for k in ('interval', 'updatelimit') if k in body}
                    if (('interval' in options and 'updatelimit' in options) or
                            any(type(v) is not int or v < 0 for v in options.values()) or
                            ('updatelimit' in options and not 1 <= options['updatelimit'] <= 10000)):
                        raise GatewayError(400, 'Invalid interval/updatelimit')
                    old = tasks.get(event)
                    if old and not old.done():
                        await ws.send_json(dict(type='subscribe', event=event, status='ok', simulation=True))
                        continue
                    if (sum(not t.done() for t in tasks.values()) >= MAX_SUBSCRIPTIONS or
                            len(state.bridges) >= MAX_BRIDGES):
                        raise GatewayError(503, 'Gateway subscription limit reached')
                    await ws.send_json(dict(type='subscribe', event=event, status='ok', simulation=True))
                    task = asyncio.create_task(pump(event, entry, options), name='sdk-gateway-bridge')
                    tasks[event] = task
                    state.bridges.add(task)
                    entry.watchers.add(task)
                    task.add_done_callback(state.bridges.discard)
                    task.add_done_callback(entry.watchers.discard)
                    def forget(done, key=event):
                        if tasks.get(key) is done:
                            tasks.pop(key, None)
                    task.add_done_callback(forget)
                except (ValueError, GatewayError) as error:
                    await ws.send_json(dict(type='error', event=event, status='error',
                        code=error.status if isinstance(error, GatewayError) else 400,
                        gatewayError=error.gateway_code if isinstance(error, GatewayError) else None,
                        message=str(error), simulation=True))
        finally:
            try:
                await cancel_tasks(tasks.values())
            finally:
                # aiohttp may cancel the handler again on disconnect while
                # cleanup awaits. Bookkeeping still has to be released.
                state.sockets.discard(ws)
                state.handlers.pop(ws, None)
        return ws

    async def resources(app):
        async with ClientSession(timeout=ClientTimeout(total=5), trust_env=False) as session:
            app[SESSION_KEY] = session
            yield
        state.clients.clear()

    async def shutdown(app):
        handlers = list(state.handlers.values())
        await asyncio.gather(*(ws.close(code=1001, message=b'Simulator stopped')
                               for ws in list(state.sockets)))
        await cancel_tasks(state.bridges)
        await asyncio.gather(*handlers, return_exceptions=True)

    app.cleanup_ctx.append(resources)
    app.on_shutdown.append(shutdown)
    app.router.add_route('*', '/{tail:.*}', handle)
    return app


async def serve(backend_origin='http://127.0.0.1:30001', host='127.0.0.1', port=50005,
                seconds=0, ready_file=None, stop_file=None, header_policy='shape-only'):
    if not loopback(host) or not 0 <= port <= 65535 or seconds < 0:
        raise ValueError('Use a literal loopback bind, valid port and nonnegative duration')
    if stop_file and Path(stop_file).exists():
        raise ValueError('Stop file already exists; choose a fresh session path')
    app = make_app(backend_origin, header_policy=header_policy)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    record = None
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        actual_port = site._server.sockets[0].getsockname()[1]
        bracket = '[' + host + ']' if ':' in host else host
        record = dict(program='hmi-sdk-gateway-simulator', state='running', simulation=True,
            url='http://%s:%d' % (bracket, actual_port), headerPolicy=header_policy,
            backendOrigin=app[STATE_KEY].backend_origin)
        if ready_file:
            Path(ready_file).parent.mkdir(parents=True, exist_ok=True)
            Path(ready_file).write_text(json.dumps(record, indent=2), encoding='utf-8')
        print('SDK_GATEWAY_READY=' + json.dumps(record), flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        started = time.monotonic()
        while not stop.is_set():
            if seconds and time.monotonic() - started >= seconds:
                break
            if stop_file and Path(stop_file).exists():
                break
            try:
                await asyncio.wait_for(stop.wait(), .2)
            except asyncio.TimeoutError:
                pass
    finally:
        await runner.cleanup()
        if record and ready_file:
            record['state'] = 'ended'
            Path(ready_file).write_text(json.dumps(record, indent=2), encoding='utf-8')
        print('SDK_GATEWAY_STOPPED memory_state_discarded=true', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-origin', default='http://127.0.0.1:30001')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=50005)
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--ready-file')
    parser.add_argument('--stop-file')
    parser.add_argument('--header-policy', choices=['shape-only', 'off'], default='shape-only')
    args = parser.parse_args(argv)
    asyncio.run(serve(**vars(args)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
