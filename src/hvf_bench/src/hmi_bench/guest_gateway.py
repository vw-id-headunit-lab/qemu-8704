"""Opt-in guest HTTP gateways and explicit loopback simulator transport."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import threading

from aiohttp import web


def validate_arm64(path):
    path = Path(path).expanduser().resolve()
    data = path.read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 183:
        raise ValueError('Expected separately built little-endian Linux AArch64 ELF: ' + str(path))
    return path


@dataclass(frozen=True)
class GatewayOptions:
    binary: Path
    relay: Path
    proxy_port: int = 50007
    sdk_gateway: bool = False

    def validate(self, mode):
        if mode != 'hmi':
            raise ValueError('Guest gateways are available only in hmi mode')
        if self.proxy_port not in (50006, 50007):
            raise ValueError('HMI proxy port must be 50006 or 50007')
        validate_arm64(self.binary)
        validate_arm64(self.relay)


def netdev_arguments(api_port, sdk_port=None):
    for port in (api_port, sdk_port):
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ValueError('Backend port outside range')
    # Windows libslirp cmd: fails to inherit socket handles. Use one chardev
    # stream and explicitly multiplex independent HTTP/WS connections over it.
    argv = ['-chardev', 'socket,id=bench-api,host=127.0.0.1,port=%d' % api_port]
    value = ('user,id=hmi-bench-local,restrict=on,ipv6=off,'
             'guestfwd=tcp:10.0.2.100:30001-chardev:bench-api')
    if sdk_port is not None:
        argv += ['-chardev', 'socket,id=bench-sdk,host=127.0.0.1,port=%d' % sdk_port]
        value += ',guestfwd=tcp:10.0.2.100:50005-chardev:bench-sdk'
    return argv + ['-netdev', value, '-device', 'virtio-net-pci,netdev=hmi-bench-local,mac=52:54:00:42:00:15,romfile=']


async def multiplex(reader, writer, backend_port):
    """Fixed-target bridge. Frames: type:u8, channel:u32be, length:u32be, data.

    1=open, 2=data, 3=write EOF, 4=close. No frame carries a destination.
    """
    channels, tasks = {}, set()
    lock = asyncio.Lock()

    async def send(kind, ident, data=b''):
        async with lock:
            writer.write(struct.pack('!BII', kind, ident, len(data)) + data)
            await writer.drain()

    async def download(ident, upstream, peer):
        try:
            while True:
                data = await upstream.read(32768)
                if not data:
                    await send(3, ident)
                    break
                await send(2, ident, data)
        except (ConnectionError, OSError):
            pass
        finally:
            channels.pop(ident, None)
            peer.close()
            with suppress(ConnectionError, OSError):
                await peer.wait_closed()
            if not writer.is_closing():
                with suppress(ConnectionError, OSError):
                    await send(4, ident)

    try:
        while True:
            kind, ident, size = struct.unpack('!BII', await reader.readexactly(9))
            if not ident or size > 65536 or kind not in (1, 2, 3, 4) or (kind != 2 and size):
                raise ValueError('Invalid multiplex frame')
            data = await reader.readexactly(size)
            if kind == 1:
                if ident in channels or len(channels) >= 64:
                    await send(4, ident)
                    continue
                upstream, peer = await asyncio.open_connection('127.0.0.1', backend_port)
                channels[ident] = peer
                task = asyncio.create_task(download(ident, upstream, peer))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            elif ident in channels:
                peer = channels[ident]
                if kind == 2:
                    peer.write(data)
                    await peer.drain()
                elif kind == 3:
                    peer.write_eof()
                else:
                    peer.close()
    except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
        pass
    finally:
        writer.close()
        for peer in list(channels.values()):
            peer.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)
        with suppress(ConnectionError, OSError):
            await writer.wait_closed()


class LocalBackends:
    """Own a single event loop for this VM's loopback-only API backends."""
    def __init__(self, output, sdk_gateway=False):
        self.output = Path(output)
        self.sdk_gateway = sdk_gateway
        self.ready = threading.Event()
        self.loop = None
        self.stop = None
        self.error = None
        self.ports = {}
        self.requests = []
        self.mux_servers = []
        self.mux_tasks = set()
        self.thread = threading.Thread(target=self._thread, daemon=True, name='HMI-Local-API')

    def _thread(self):
        try:
            asyncio.run(self._run())
        except BaseException as error:
            self.error = error
            self.ready.set()

    async def _run(self):
        from .rsi_simulator import make_app
        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        runners = []
        try:
            @web.middleware
            async def log_request(request, handler):
                response = await handler(request)
                self.requests.append(dict(method=request.method, path=request.path,
                    agent=request.headers.get('User-Agent', '')[:120], status=response.status))
                del self.requests[:-4000]
                return response

            async def listen(app, key):
                app.middlewares.insert(0, log_request)
                runner = web.AppRunner(app, access_log=None)
                runners.append(runner)
                await runner.setup()
                site = web.TCPSite(runner, '127.0.0.1', 0)
                await site.start()
                self.ports[key] = site._server.sockets[0].getsockname()[1]

            await listen(make_app(), 'api_http_port')
            if self.sdk_gateway:
                from .sdk_gateway_simulator import make_app as make_sdk_app
                await listen(make_sdk_app(backend_origin='http://127.0.0.1:%d' % self.ports['api_http_port']),
                             'sdk_http_port')
            for name in ('api', 'sdk') if self.sdk_gateway else ('api',):
                port = self.ports[name + '_http_port']
                async def connected(reader, writer, backend=port):
                    task = asyncio.current_task()
                    self.mux_tasks.add(task)
                    try:
                        await multiplex(reader, writer, backend)
                    finally:
                        self.mux_tasks.discard(task)
                server = await asyncio.start_server(connected, '127.0.0.1', 0)
                self.mux_servers.append(server)
                self.ports[name + '_backend_port'] = server.sockets[0].getsockname()[1]
            self.ready.set()
            await self.stop.wait()
        finally:
            for server in self.mux_servers:
                server.close()
                await server.wait_closed()
            for task in list(self.mux_tasks):
                task.cancel()
            await asyncio.gather(*list(self.mux_tasks), return_exceptions=True)
            for runner in reversed(runners):
                await runner.cleanup()
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / 'gateway-backend-requests.json').write_text(json.dumps(dict(
                state='ended', requests=self.requests, ports=self.ports, external_effects=False), indent=2), encoding='utf-8')

    def start(self):
        self.thread.start()
        if not self.ready.wait(15):
            self.close()
            raise RuntimeError('Local API backend startup timed out')
        if self.error:
            raise RuntimeError('Local API backend startup failed: ' + str(self.error))
        return {k: v for k, v in self.ports.items() if k.endswith('_backend_port')}

    def close(self):
        if self.loop and self.stop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop.set)
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError('Local API backend cleanup timed out')
