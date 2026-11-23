"""Loopback-only, in-memory RSI 30001 simulator; never performs device/cloud actions."""
import asyncio
import copy
import ipaddress
import json
import math
from pathlib import Path
import signal
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from aiohttp import web, WSMsgType
from .rsi_contract import RESOURCES

CATALOG_PATH = '/colorservicemanagement/services/'
MAX_SUBSCRIPTIONS = 16
STATE_KEY = web.AppKey('rsi_state', object)
WS_KEY = web.AppKey('rsi_websockets', set)


def identity(name):
    return str(uuid5(NAMESPACE_URL, 'urn:hmi-bench:rsi-simulation:' + name))


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def typed(value, kind):
    if kind == 'number':
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    return {'boolean': type(value) is bool, 'integer': type(value) is int,
            'string': isinstance(value, str), 'array': isinstance(value, list),
            'object': isinstance(value, dict)}.get(kind, True)


class RSIState:
    def __init__(self):
        self.started = time.monotonic()
        self.items = {path: {} for path in RESOURCES}
        self.events = []
        self.ticks = 0
        seeds = {
            '/gnss/simple/': dict(status=False, timestamp='0', latitude='0.000000', longitude='0.000000',
                                 altitude='0.000000', speed='0.000000', direction='0', satellite_cnt=0, satellites=[]),
            '/onlineengineeringmode/networkmode/': {'mode': '1'},
            '/onlineengineeringmode/environment/': dict(networkEnvironment='Normal', switchTraceLog=False, reboot=False),
            '/onlineengineeringmode/usbManagementResource/': dict(usbStatus=False, logLevel='error', exportLog=False,
                                                                  exportLogStatus=False, clearLog=False),
            '/onlineservicesetting/onlineNotifyConfigs/': {'operation': False},
            '/onlineservicesetting/onlineservicestates/': {'onlineservicestate': False},
            '/onlineservicesetting/privacygroups/': dict(voiceservice=False, voiceserviceAccount=False, voicecollection=False),
            '/parkinglotsetup/successfullyPaidNotificationSettings/': {'status': False},
            '/parkinglotsetup/successfullyPaidTTSSettings/': {'status': False},
            '/startupbroadcasting/broadcastingsettings/': dict(weatherOpen=False, roadRestrictionOpen=False,
                                                                 violationRemindOpen=False),
            '/songidentification/identifyControls/': dict(startRecording=False, stopRecording=False, appAvailable=False,
                                                          controllerStatus='initialized', addFavorite='initialized',
                                                          failReason='noMusic', songData={}),
        }
        for path, fields in seeds.items():
            self.insert(path, fields, identity(path + 'default'), 'Simulation default')

    def uptime(self):
        return int((time.monotonic() - self.started) * 1000)

    def envelope(self, data, **extra):
        return dict(status='ok', data=data, timestamp=self.uptime(), simulation=True, **extra)

    def insert(self, path, fields, identifier=None, name='Simulation item'):
        identifier = identifier or str(uuid4())
        row = dict(fields, id=identifier, name=fields.get('name', name), uri=path + identifier)
        self.items[path][identifier] = row
        return row

    def tick(self):
        self.ticks += 1
        # Simulator tick counter, explicitly not GNSS epoch or a real fix.
        next(iter(self.items['/gnss/simple/'].values()))['timestamp'] = str(self.ticks)

    def services(self, port):
        names = sorted({p.split('/')[1] for p in RESOURCES})
        return [dict(id=identity('service:' + name), name=name, uri='/' + name + '/', port=port,
                     description='Local synthetic service; no external effects', versions=[], simulation=True)
                for name in names]

    def catalog(self, origin):
        # Optional fixture for the existing Go gateway's 30002 service catalog.
        # Not advertised as a thirteenth 30001 service.
        return [dict(id=identity('gateway:' + name), name='SIMULATION.' + name, apnType='b2c',
                     baseUrl=origin + '/' + name + '/', uri=CATALOG_PATH + identity('gateway:' + name))
                for name in sorted({p.split('/')[1] for p in RESOURCES})]

    def resolve(self, path):
        if not path.startswith('/') or '%' in path or '\\' in path or '..' in path.split('/'):
            raise APIError(400, 'Malformed resource path')
        for root in sorted(RESOURCES, key=len, reverse=True):
            if path == root[:-1]:
                return root, 'metadata', None
            if path == root:
                return root, 'list', None
            if path.startswith(root):
                tail = path[len(root):]
                if tail in ('$id', '$spec'):
                    return root, tail, None
                if '/' in tail or not tail:
                    raise APIError(400, 'Element UUID must not have a trailing slash')
                if root == '/cdn/images/':
                    raise APIError(404, 'No simulated CDN files; no host filesystem access')
                try:
                    if str(UUID(tail)) != tail.lower():
                        raise ValueError('UUID format')
                except ValueError:
                    raise APIError(400, 'Element identifier must be a UUID')
                return root, 'element', tail
        raise APIError(404, 'Service/resource not present')

    def projected(self, row, query, metadata_only=False):
        fields = query.get('$fields', [''])[0]
        names = set(fields.split(',')) if fields else ({'id', 'name', 'uri'} if metadata_only else set(row))
        return {name: copy.deepcopy(value) for name, value in row.items() if name in names}

    def read(self, path, query, port, origin):
        if path == '/':
            return self.envelope(self.services(port))
        if path == CATALOG_PATH:
            return self.envelope(self.catalog(origin), paging={'total': 12})
        names = {p.split('/')[1] for p in RESOURCES}
        if path.count('/') == 2 and path.endswith('/') and path.strip('/') in names:
            service = path.strip('/')
            return self.envelope([dict(id=identity(p), name=p.split('/')[2], uri=p,
                simulation=True, methods=self.methods(p), websocket=RESOURCES[p]['ws'])
                for p in RESOURCES if p.startswith('/' + service + '/')])
        root, kind, identifier = self.resolve(path)
        contract = RESOURCES[root]
        if kind == '$id':
            return identity(root)
        if kind in ('$spec', 'metadata'):
            return self.envelope(dict(id=identity(root), name=root.split('/')[2], uri=root,
                 properties=contract['fields'], methods=self.methods(root), websocket=contract['ws'],
                 specOrigin='Authored simulator contract; not a vendor schema or equivalence guarantee'))
        if kind == 'element':
            row = self.items[root].get(identifier)
            if row is None:
                raise APIError(404, 'Element not present')
            return self.envelope(self.projected(row, query))
        rows = list(self.items[root].values())
        term = query.get('$q', [''])[0].lower()
        if term:
            rows = [r for r in rows if term in json.dumps(r, ensure_ascii=False).lower()]
        sort = query.get('$sortby', [''])[0]
        if sort:
            field = sort.lstrip('-')
            rows = sorted(rows, key=lambda r: (r.get(field) is None, str(r.get(field, ''))), reverse=sort.startswith('-'))
        total = len(rows)
        try:
            offset = int(query.get('$offset', ['0'])[0])
            limit = int(query.get('$limit', ['100'])[0])
            expand = int(query.get('$expand', ['0'])[0])
        except ValueError:
            raise APIError(400, 'Simulator paging requires nonnegative integer offsets/limits')
        if offset < 0 or not 0 <= limit <= 1000:
            raise APIError(400, 'Paging value outside simulator range')
        rows = rows[offset:offset + limit]
        # Existing Go mode discovery consumes mode directly without $expand.
        summary_only = not 1 <= expand <= 3 and root != '/onlineengineeringmode/networkmode/'
        return self.envelope([self.projected(row, query, summary_only) for row in rows],
                             paging=dict(total=total, offset=offset, limit=limit))

    @staticmethod
    def methods(root):
        c = RESOURCES[root]
        return dict(resource=['GET'] + (['POST'] if c['create'] else []),
                    element=['GET'] + (['POST'] if c['update'] else []) + (['DELETE'] if c['delete'] else []))

    def write(self, method, path, query, body):
        root, kind, identifier = self.resolve(path)
        c = RESOURCES[root]
        if method == 'PUT' or kind not in ('list', 'element'):
            raise APIError(403, 'Method is not implemented for this resource')
        if query:
            raise APIError(400, 'Query writes/bulk operations are not simulated')
        allowed = c['delete'] if method == 'DELETE' else c['create'] if kind == 'list' else c['update']
        if not allowed or (method == 'DELETE' and kind != 'element'):
            raise APIError(403, 'Method is not implemented for this resource')
        if kind == 'element' and identifier not in self.items[root]:
            raise APIError(404, 'Element not present')
        if method == 'DELETE':
            removed = self.items[root].pop(identifier)
            return 200, self.envelope(removed, effects='memory_only')
        if not isinstance(body, dict):
            raise APIError(400, 'Expected a JSON object')
        rule = c['input'].get('resource' if kind == 'list' else 'element', {})
        known = dict(c['fields'], name='string')
        known.update({k: v.get('type', 'string') for k, v in rule.get('fields', {}).items()})
        for field in rule.get('required', []):
            if field not in body:
                raise APIError(400, 'Missing required field: ' + field)
        for field, value in body.items():
            if field not in known or not typed(value, known[field]):
                raise APIError(400, 'Unknown field or incorrect JSON type: ' + field)
            enums = rule.get('fields', {}).get(field, {}).get('enum')
            if enums and value not in enums:
                raise APIError(400, 'Invalid enum value: ' + field)
        if root == '/onlineengineeringmode/networkmode/':
            # The original fromJSON does not parse mode. Do not invent a setter.
            raise APIError(403, 'Network mode setter is not modeled; fixture mode stays direct')
        if body.get('startRecording') and body.get('stopRecording'):
            raise APIError(400, 'Conflicting recording controls')
        if kind == 'list':
            row = self.insert(root, copy.deepcopy(body))
            code = 201
        else:
            row = self.items[root][identifier]
            row.update(copy.deepcopy(body))
            code = 200
        if root == '/songidentification/identifyControls/':
            # UI-state simulation only, never starts audio capture or cloud recognition.
            if body.get('startRecording'):
                row.update(controllerStatus='isRecording', failReason='noMusic')
            elif body.get('stopRecording'):
                row.update(controllerStatus='stopRecordingSuccess', startRecording=False)
        self.events.append(dict(method=method, resource=root, effects='memory_only', timestamp=self.uptime()))
        del self.events[:-1000]
        return code, self.envelope(copy.deepcopy(row), effects='memory_only')


@web.middleware
async def simulation_boundary(request, handler):
    if request.remote and not ipaddress.ip_address(request.remote).is_loopback:
        return web.json_response(dict(status='error', code=403, message='Loopback clients only', simulation=True), status=403)
    if request.headers.get('Origin'):
        try:
            origin_host = urlsplit(request.headers['Origin']).hostname
            allowed = ipaddress.ip_address(origin_host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            return web.json_response(dict(status='error', code=403, message='Non-loopback Origin rejected', simulation=True), status=403)
    try:
        response = await handler(request)
    except APIError as error:
        response = web.json_response(dict(status='error', code=error.status, message=error.message, simulation=True),
                                     status=error.status)
    except (ValueError, json.JSONDecodeError):
        response = web.json_response(dict(status='error', code=400, message='Invalid JSON/value', simulation=True), status=400)
    response.headers['X-HMI-Bench-Simulation'] = 'true'
    response.headers['Cache-Control'] = 'no-store'
    return response


def make_app(state=None):
    state = state or RSIState()
    app = web.Application(middlewares=[simulation_boundary], client_max_size=256 * 1024)
    app[STATE_KEY] = state
    app[WS_KEY] = set()

    async def handle(request):
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            if request.path != '/':
                raise APIError(404, 'WS endpoint is /')
            return await websocket(request)
        if request.path == '/__simulation/health' and request.method == 'GET':
            return web.json_response(state.envelope(dict(program='hmi-rsi-simulator', services=12, resources=40,
                externalEffects=False, gatewayCatalogFixture=CATALOG_PATH)))
        origin = request.scheme + '://' + request.host
        query = parse_qs(request.query_string, keep_blank_values=True)
        if request.method == 'GET':
            result = state.read(request.path, query, request.url.port or 80, origin)
            return web.Response(text=result, content_type='text/plain') if isinstance(result, str) else web.json_response(result)
        if request.method not in ('POST', 'PUT', 'DELETE'):
            raise APIError(405, 'Method not simulated')
        body = None if request.method == 'DELETE' else await request.json()
        code, result = state.write(request.method, request.path, query, body)
        return web.json_response(result, status=code)

    async def websocket(request):
        origin = request.headers.get('Origin')
        if origin:
            u = urlsplit(origin)
            try:
                if not ipaddress.ip_address(u.hostname).is_loopback:
                    raise APIError(403, 'Cross-origin WS rejected')
            except ValueError:
                raise APIError(403, 'Cross-origin WS rejected')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        app[WS_KEY].add(ws)
        tasks = {}

        async def publish(event, interval, limit):
            u = urlsplit(event)
            count = 0
            try:
                while not ws.closed and (limit is None or count < limit):
                    result = state.read(u.path, parse_qs(u.query), request.url.port or 80,
                                        request.scheme + '://' + request.host)
                    await ws.send_json(dict(result, type='data', event=event))
                    count += 1
                    await asyncio.sleep(interval)
            except (ConnectionError, asyncio.CancelledError):
                pass
            except APIError as error:
                if not ws.closed:
                    await ws.send_json(dict(type='error', event=event, code=error.status,
                                            message=error.message, simulation=True))

        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                event = ''
                try:
                    body = json.loads(message.data)
                    if not isinstance(body, dict):
                        raise APIError(400, 'Expected WS JSON object')
                    event = body.get('event', '')
                    if not isinstance(event, str) or len(event) > 2048:
                        raise APIError(400, 'Invalid event')
                    u = urlsplit(event)
                    if u.scheme or u.netloc or not u.path.startswith('/'):
                        raise APIError(400, 'Event must be a local resource URI')
                    action = body.get('type')
                    if action == 'unsubscribe':
                        task = tasks.pop(event, None)
                        if task:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                        await ws.send_json(dict(type='unsubscribe', event=event, status='ok', simulation=True))
                        continue
                    if action != 'subscribe':
                        raise APIError(400, 'Only subscribe/unsubscribe are simulated')
                    if u.path != CATALOG_PATH:
                        root, kind, identifier = state.resolve(u.path)
                        if not RESOURCES[root]['ws'] or kind not in ('list', 'element'):
                            raise APIError(501, 'Resource has no simulated WS support')
                    state.read(u.path, parse_qs(u.query), request.url.port or 80, request.scheme + '://' + request.host)
                    interval = body.get('interval', 1000)
                    limit = body.get('updatelimit')
                    if type(interval) is not int or interval < 0 or ('interval' in body and limit is not None):
                        raise APIError(400, 'Invalid interval or interval/updatelimit combination')
                    if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
                        raise APIError(400, 'Invalid updatelimit')
                    if event not in tasks and len(tasks) >= MAX_SUBSCRIPTIONS:
                        raise APIError(503, 'Subscription limit reached')
                    old = tasks.pop(event, None)
                    if old:
                        old.cancel()
                        await asyncio.gather(old, return_exceptions=True)
                    await ws.send_json(dict(type='subscribe', event=event, status='ok', simulation=True))
                    tasks[event] = asyncio.create_task(publish(event, max(1.0, interval / 1000), limit))
                except (APIError, ValueError, TypeError) as error:
                    await ws.send_json(dict(type='error', event=event, status='error',
                        code=error.status if isinstance(error, APIError) else 400, message=str(error), simulation=True))
        finally:
            try:
                for task in tasks.values():
                    task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
            finally:
                # Disconnect may cancel this handler while awaiting task cleanup.
                app[WS_KEY].discard(ws)
        return ws

    async def ticker(app):
        async def clock():
            while True:
                await asyncio.sleep(1)
                state.tick()
        task = asyncio.create_task(clock())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def shutdown(app):
        await asyncio.gather(*(ws.close(code=1001, message=b'Simulator stopped') for ws in list(app[WS_KEY])))

    app.cleanup_ctx.append(ticker)
    app.on_shutdown.append(shutdown)
    app.router.add_route('*', '/{tail:.*}', handle)
    return app


async def serve(host='127.0.0.1', port=30001, seconds=0, ready_file=None, stop_file=None):
    if not ipaddress.ip_address(host).is_loopback or not 0 <= port <= 65535:
        raise ValueError('Simulator must bind an explicit loopback IP and valid port')
    if seconds < 0:
        raise ValueError('seconds must be nonnegative')
    if stop_file and Path(stop_file).exists():
        raise ValueError('Stop file already exists; choose a fresh session path')
    app = make_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    record = None
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        address = site._server.sockets[0].getsockname()
        bracket = '[' + host + ']' if ':' in host else host
        record = dict(program='hmi-rsi-simulator', state='running', host=host, port=address[1],
                      url='http://%s:%d' % (bracket, address[1]), services=12, resources=40, simulation=True,
                      stop_file=str(stop_file) if stop_file else None)
        if ready_file:
            ready_file = Path(ready_file)
            ready_file.parent.mkdir(parents=True, exist_ok=True)
            ready_file.write_text(json.dumps(record, indent=2), encoding='utf-8')
        print('SIMULATOR_READY=' + json.dumps(record), flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        start = time.monotonic()
        while not stop.is_set():
            if seconds and time.monotonic() - start >= seconds:
                break
            if stop_file and Path(stop_file).exists():
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=.2)
            except asyncio.TimeoutError:
                pass
    finally:
        await runner.cleanup()
        if ready_file and record and Path(ready_file).exists():
            record['state'] = 'ended'
            Path(ready_file).write_text(json.dumps(record, indent=2), encoding='utf-8')
        print('SIMULATOR_STOPPED memory_state_discarded=true', flush=True)
