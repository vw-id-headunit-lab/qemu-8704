"""Loopback noVNC viewer and binary WebSocket/TCP bridge for a real RFB server.

The module never generates framebuffer data. The RFB peer owns all pixels.
Optional physical pointer edges use a dedicated guest input channel; keyboard
events continue through noVNC/RFB. No VM, browser, or subprocess is started here.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
import threading
from urllib.parse import urlencode
from uuid import uuid4

from aiohttp import WSMsgType, web
from .progress import read_progress


_STATIC_ROOT = Path(__file__).resolve().parents[2] / 'runtime' / 'novnc'
_VIEWER_HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>原应用实时界面</title><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#101419;color:#e5eaf0;font:14px system-ui,sans-serif}
body{display:flex;flex-direction:column}header{display:flex;align-items:center;flex-wrap:wrap;gap:10px 18px;min-height:48px;padding:6px 14px;background:#18212d}
header{flex-shrink:0}header strong{font-weight:600}#status{color:#c1cfde}#screen{flex:1;min-height:0;min-width:0;position:relative;overflow:hidden}#screen:focus{outline:0}
#vehicle-controls{display:flex;align-items:center;gap:12px;margin-left:auto}#vehicle-controls[hidden]{display:none}
#vehicle-state{color:#c1cfde;font-size:13px}#vehicle-toggle{border:0;border-radius:7px;padding:9px 18px;background:#ffc400;color:#171717;font:600 14px system-ui;cursor:pointer;white-space:nowrap}
#vehicle-toggle:disabled{opacity:.5;cursor:wait}#vehicle-toggle:focus-visible{outline:2px solid white;outline-offset:3px}#vehicle-toggle.running{background:#364557;color:#fff}
#vehicle-error{color:#ffb4a8;font-size:12px}#vehicle-error:empty{display:none}
#screen.touch-input,#screen.touch-input *{cursor:default!important}
#progress{color:#aebed0;font-size:12px}
#measurement{padding:5px 14px;background:#18212d;font:12px system-ui}#measurement button{margin:4px;padding:4px 8px}#measure-note{color:#c1cfde}
</style></head><body><header><strong>原应用实时界面</strong><span id="status">正在连接…</span><span id="progress"></span>
<div id="vehicle-controls" hidden><span id="vehicle-state" role="status">等待车辆信号…</span><button id="vehicle-toggle" type="button" disabled>启动车辆</button><span id="vehicle-error" role="alert"></span></div></header>
<details id="measurement" hidden><summary>P0 测量（仅确认实际看到的画面）</summary>
<button data-measure="first_visible">确认首屏可见</button><button data-measure="menu_ready">确认菜单可操作</button>
<button data-measure="arm_click">计时下一次 Settings 点击</button><button data-measure="confirm_click">目标页面已出现</button><button data-measure="cancel_click">取消点击计时</button>
<span id="measure-note">手动确认包含观察者反应时间，不是自动帧分析。</span></details>
<div id="screen" tabindex="0"></div><script type="module">
import RFB from '/core/rfb.js';
const status=document.getElementById('status');
const params=new URLSearchParams(location.search);
const endpoint=new URL('/websockify',location.href);
endpoint.protocol=location.protocol==='https:'?'wss:':'ws:';
const rfb=new RFB(document.getElementById('screen'),endpoint.href,{shared:true});
const touchEnabled=__TOUCH_ENABLED__;
rfb.scaleViewport=true;
rfb.clipViewport=false;
rfb.resizeSession=false;
rfb.viewOnly=false;
rfb.focusOnClick=true;
rfb.addEventListener('connect',()=>{status.textContent=touchEnabled?'已连接 · 触控输入':'已连接 · 可操作';rfb.focus();});
rfb.addEventListener('disconnect',e=>{status.textContent=e.detail.clean?'连接已结束':'连接已中断';});
rfb.addEventListener('credentialsrequired',()=>{status.textContent='VNC 服务器请求认证';});
rfb.addEventListener('securityfailure',()=>{status.textContent='VNC 认证未通过';});
let activePointer=null,lastPoint={x:0,y:0,width:1,height:1},pendingMove=null,animation=0,sending=false;
const inputQueue=[];
const screen=document.getElementById('screen');
screen.classList.toggle('touch-input',touchEnabled);
function point(event){const canvas=screen.querySelector('canvas');if(!canvas)return null;const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return null;return {x:Math.round(Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))*(canvas.width-1)),y:Math.round(Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))*(canvas.height-1)),width:canvas.width,height:canvas.height};}
async function pumpInput(){if(sending)return;sending=true;try{while(inputQueue.length){const event=inputQueue.shift();const response=await fetch('/input/pointer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(event)});if(!response.ok){const error=await response.json();throw Error(error.error||response.status)}}}catch(error){inputQueue.length=0;status.textContent='触控输入发送失败：'+error.message}finally{sending=false;}}
function sendTouch(action,position){lastPoint=position;const event={action,...position};if(action==='move'&&inputQueue.length&&inputQueue[inputQueue.length-1].action==='move')inputQueue[inputQueue.length-1]=event;else inputQueue.push(event);pumpInput();}
function flushMove(){animation=0;if(pendingMove){const p=pendingMove;pendingMove=null;sendTouch('move',p)}}
if(touchEnabled){screen.style.touchAction='none';for(const type of ['mousedown','mouseup','mousemove','click','dblclick','contextmenu','touchstart','touchmove','touchend','touchcancel','wheel'])screen.addEventListener(type,event=>{event.preventDefault();event.stopImmediatePropagation()},{capture:true,passive:false});
screen.addEventListener('pointerdown',event=>{if(activePointer!==null||event.button!==0||!event.isPrimary)return;const position=point(event);if(!position)return;event.preventDefault();event.stopImmediatePropagation();activePointer=event.pointerId;screen.setPointerCapture(event.pointerId);rfb.focus();sendTouch('down',position)},{capture:true,passive:false});
screen.addEventListener('pointermove',event=>{if(event.pointerId!==activePointer)return;event.preventDefault();event.stopImmediatePropagation();pendingMove=point(event);if(pendingMove&&!animation)animation=requestAnimationFrame(flushMove)},{capture:true,passive:false});
for(const type of ['pointerup','pointercancel'])screen.addEventListener(type,event=>{if(event.pointerId!==activePointer)return;event.preventDefault();event.stopImmediatePropagation();if(animation)cancelAnimationFrame(animation);flushMove();sendTouch(type==='pointercancel'?'cancel':'up',point(event)||lastPoint);activePointer=null;if(screen.hasPointerCapture(event.pointerId))screen.releasePointerCapture(event.pointerId)},{capture:true,passive:false});}
let pageActive=true;
const vehicleControls=document.getElementById('vehicle-controls'),vehicleButton=document.getElementById('vehicle-toggle'),vehicleState=document.getElementById('vehicle-state'),vehicleError=document.getElementById('vehicle-error');
vehicleControls.hidden=!touchEnabled;
let vehicleStarted=false,vehicleBusy=false;
function showVehicle(info){vehicleControls.hidden=!info.available;vehicleButton.disabled=vehicleBusy||!info.ready;if(!info.ready){vehicleState.textContent='等待 HMI 就绪…';return}vehicleStarted=info.started;vehicleState.textContent=info.started?'车辆模拟 · 已启动':'车辆模拟 · 未启动';vehicleButton.textContent=info.started?'车辆熄火':'启动车辆';vehicleButton.classList.toggle('running',info.started);vehicleButton.setAttribute('aria-pressed',String(info.started));}
async function refreshVehicle(){if(!pageActive)return;if(!vehicleBusy){try{const response=await fetch('/api/vehicle',{cache:'no-store'});const info=await response.json();if(!response.ok)throw Error(info.error||'车辆信号暂不可用');showVehicle(info)}catch(error){vehicleButton.disabled=true;vehicleState.textContent='车辆信号未连接';}}if(pageActive)setTimeout(refreshVehicle,2000)}
vehicleButton.addEventListener('click',async()=>{if(vehicleBusy||vehicleButton.disabled)return;vehicleBusy=true;vehicleButton.disabled=true;vehicleButton.textContent=vehicleStarted?'正在熄火…':'正在启动…';vehicleError.textContent='';try{const response=await fetch('/api/vehicle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({started:!vehicleStarted})});const info=await response.json();if(!response.ok)throw Error(info.error||'车辆操作失败');vehicleBusy=false;showVehicle(info)}catch(error){vehicleBusy=false;vehicleError.textContent=error.message;vehicleButton.textContent=vehicleStarted?'车辆熄火':'启动车辆';}});
refreshVehicle();
async function refreshProgress(){if(!pageActive)return;try{const response=await fetch('/api/progress',{cache:'no-store'});if(response.ok){const info=await response.json();document.getElementById('progress').textContent=info.label}}catch(error){}if(pageActive)setTimeout(refreshProgress,2000)}
refreshProgress();
const measurePanel=document.getElementById('measurement'),measureNote=document.getElementById('measure-note');
async function measureAction(action){try{const response=await fetch('/api/measurement',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,label:'Settings'})});const info=await response.json();if(!response.ok)throw Error(info.error||response.status);measureNote.textContent=info.pending_click?'等待一次真实按下/抬起后，目标页面出现时确认。':'已记录；手动确认时间包含观察者反应和传输调度。';}catch(error){measureNote.textContent=error.message}}
fetch('/api/measurement').then(response=>response.json()).then(info=>{measurePanel.hidden=!info.enabled}).catch(()=>{});
for(const button of measurePanel.querySelectorAll('button'))button.addEventListener('click',()=>measureAction(button.dataset.measure));
window.addEventListener('pagehide',()=>{pageActive=false;if(touchEnabled&&activePointer!==null)navigator.sendBeacon('/input/pointer',JSON.stringify({action:'cancel',...lastPoint}));rfb.disconnect()},{once:true});
</script></body></html>'''


class VNCViewerServer:
    """Start a loopback HTTP/WS viewer; call close() when the RFB session ends.

    Example::

        viewer = VNCViewerServer(rfb_port=5901)
        print(viewer.url)
        # Start/observe the separately owned QEMU RFB endpoint.
        viewer.close()

    The RFB endpoint is fixed to 127.0.0.1:rfb_port. The HTTP port is selected
    by the OS. Static assets are the verified @novnc/novnc package. Providing
    input_port enables the physical pointer adapter automatically; it forwards
    down/move/up pixel coordinates to a dedicated QEMU virtserial TCP channel.
    Set touch_input=False to retain the ordinary RFB pointer path instead.
    """

    def __init__(self, rfb_port: int, input_port: int | None = None, touch_input: bool | None = None, *, static_root: Path | None = None, progress_path: Path | None = None, measurement=None):
        if isinstance(rfb_port, bool) or not isinstance(rfb_port, int) or not 1 <= rfb_port <= 65535:
            raise ValueError('rfb_port must be an integer from 1 through 65535')
        self.static_root = Path(static_root) if static_root is not None else _STATIC_ROOT
        if not (self.static_root / 'core' / 'rfb.js').is_file():
            raise FileNotFoundError('noVNC core/rfb.js is missing under ' + str(self.static_root))
        if input_port is not None and (isinstance(input_port, bool) or not isinstance(input_port, int) or not 1 <= input_port <= 65535):
            raise ValueError('input_port must be an integer from 1 through 65535')
        enabled = input_port is not None if touch_input is None else bool(touch_input)
        if enabled and input_port is None:
            raise ValueError('touch_input requires the loopback input chardev port')
        self.rfb_port = rfb_port
        self.input_port = input_port
        self.touch_input = enabled
        self.progress_path = Path(progress_path) if progress_path is not None else None
        self.measurement = measurement
        self.input_events_sent = 0
        self.last_input_error: str | None = None
        self._input_active = False
        self._last_input_position = (0, 0)
        self._input_writer: asyncio.StreamWriter | None = None
        self._input_reader: asyncio.StreamReader | None = None
        self._input_lock: asyncio.Lock | None = None
        self.http_port: int | None = None
        self.url = ''
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._sessions: dict[web.WebSocketResponse, asyncio.StreamWriter] = {}
        self._ready = threading.Event()
        self._close_lock = threading.Lock()
        self._closed = False
        self._start_error: BaseException | None = None
        self._thread = threading.Thread(target=self._thread_main, name='noVNC-loopback-viewer', daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            self.close()
            raise TimeoutError('The noVNC loopback server did not become ready')
        if self._start_error is not None:
            raise RuntimeError('The noVNC loopback server failed to start') from self._start_error

    async def _start(self) -> None:
        self._input_lock = asyncio.Lock()
        @web.middleware
        async def local_request(request, handler):
            self._check_origin(request)
            response = await handler(request)
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        app = web.Application(middlewares=[local_request])
        app.router.add_get('/', self._index)
        app.router.add_get('/vnc.html', self._index)
        app.router.add_get('/websockify', self._bridge)
        app.router.add_post('/input/pointer', self._pointer)
        app.router.add_get('/api/vehicle', self._vehicle_get)
        app.router.add_post('/api/vehicle', self._vehicle_post)
        app.router.add_get('/api/progress', self._progress)
        app.router.add_get('/api/measurement', self._measurement_get)
        app.router.add_post('/api/measurement', self._measurement_post)
        app.router.add_static('/', str(self.static_root), show_index=False, follow_symlinks=False)
        self._runner = web.AppRunner(app, handle_signals=False, shutdown_timeout=2)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host='127.0.0.1', port=0)
        await site.start()
        addresses = self._runner.addresses
        self.http_port = addresses[0][1]
        options = {'autoconnect': 'true', 'resize': 'scale', 'path': 'websockify'}
        if self.touch_input:
            options['touch'] = 'true'
        query = urlencode(options)
        self.url = f'http://127.0.0.1:{self.http_port}/vnc.html?{query}'

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._start())
        except BaseException as error:
            self._start_error = error
            self._ready.set()
        else:
            self._ready.set()
            loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(self._shutdown())
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _index(self, request: web.Request) -> web.Response:
        page = _VIEWER_HTML.replace('__TOUCH_ENABLED__', 'true' if self.touch_input else 'false')
        return web.Response(text=page, content_type='text/html', headers={'Cache-Control': 'no-store'})

    async def _progress(self, request: web.Request) -> web.Response:
        return web.json_response(read_progress(self.progress_path), headers={'Cache-Control': 'no-store'})

    async def _measurement_get(self, request: web.Request) -> web.Response:
        value = self.measurement.public() if self.measurement else {'enabled': False}
        return web.json_response(value, headers={'Cache-Control': 'no-store'})

    async def _measurement_post(self, request: web.Request) -> web.Response:
        if not self.measurement:
            raise web.HTTPNotFound()
        try:
            result = self.measurement.action(await request.json())
        except (ValueError, TypeError) as error:
            return web.json_response({'error': str(error)}, status=400)
        return web.json_response(result)

    async def _connect_input(self) -> None:
        if self._input_writer is None or self._input_writer.is_closing():
            self._input_reader, self._input_writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', self.input_port, limit=256), timeout=3)

    async def _vehicle_command(self, action: str) -> dict:
        if not self.touch_input:
            return {'available': False, 'ready': False, 'started': None}
        assert self._input_lock is not None
        async with self._input_lock:
            request_id = uuid4().hex[:16]
            try:
                await self._connect_input()
                self._input_writer.write(f'vehicle {request_id} {action}\n'.encode('ascii'))
                await self._input_writer.drain()
                async with asyncio.timeout(3):
                    while True:
                        line = await self._input_reader.readline()
                        if not line:
                            raise ConnectionError('Vehicle input channel closed')
                        fields = line.decode('ascii').strip().split()
                        if len(fields) != 3 or fields[0] != 'vehicle':
                            raise ValueError('Invalid vehicle acknowledgement')
                        if fields[1] != request_id:
                            continue
                        state = fields[2]
                        if state not in ('started', 'stopped', 'waiting', 'disabled'):
                            raise ValueError('Invalid vehicle state')
                        return {'available': state != 'disabled', 'ready': state in ('started', 'stopped'),
                                'started': state == 'started' if state in ('started', 'stopped') else None}
            except (OSError, ValueError, asyncio.TimeoutError):
                if self._input_writer is not None:
                    self._input_writer.close()
                self._input_writer = self._input_reader = None
                raise

    async def _vehicle_response(self, action: str) -> web.Response:
        try:
            result = await self._vehicle_command(action)
        except (OSError, ValueError, asyncio.TimeoutError):
            return web.json_response({'available': True, 'ready': False, 'started': None,
                'error': '车辆模拟通道尚未就绪，请稍后重试'}, status=503, headers={'Cache-Control': 'no-store'})
        if action != 'status' and not result['ready']:
            return web.json_response({**result, 'error': '请等待 HMI 电源服务就绪'}, status=503)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    async def _vehicle_get(self, request: web.Request) -> web.Response:
        return await self._vehicle_response('status')

    async def _vehicle_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'started'} or type(body['started']) is not bool:
                raise ValueError('Expected a Boolean started field')
        except (ValueError, TypeError):
            return web.json_response({'error': '启动状态必须是布尔值'}, status=400)
        return await self._vehicle_response('start' if body['started'] else 'stop')

    def _check_origin(self, request: web.Request) -> None:
        origin = request.headers.get('Origin')
        accepted = {f'http://127.0.0.1:{self.http_port}', f'http://localhost:{self.http_port}', f'http://127.0.0.1:{self.http_port}'}
        if request.host not in {f'127.0.0.1:{self.http_port}', f'localhost:{self.http_port}'}:
            raise web.HTTPForbidden(text='Host differs from the local viewer')
        if origin and origin not in accepted:
            raise web.HTTPForbidden(text='Origin differs from the local viewer')

    async def _emit_input(self, action: str, x: int, y: int) -> bool:
        """Forward physical pointer edges to the dedicated guest input channel."""
        assert self._input_lock is not None
        async with self._input_lock:
            if action != 'down' and not self._input_active:
                return False
            await self._connect_input()
            wire_action = 'up' if action == 'cancel' else action
            if action == 'down' and self._input_active:
                old_x, old_y = self._last_input_position
                self._input_writer.write(f'up {old_x} {old_y}\n'.encode('ascii'))
            self._input_writer.write(f'{wire_action} {x} {y}\n'.encode('ascii'))
            try:
                await self._input_writer.drain()
            except (OSError, ConnectionError):
                self._input_writer.close()
                self._input_writer = None
                self._input_reader = None
                raise
            self._input_active = action in ('down', 'move')
            self._last_input_position = (x, y)
            self.input_events_sent += 1
            self.last_input_error = None
            return True

    async def _pointer(self, request: web.Request) -> web.Response:
        self._check_origin(request)
        if not self.touch_input:
            raise web.HTTPNotFound()
        try:
            data = await request.json()
            action, x, y, width, height = (data[key] for key in ('action', 'x', 'y', 'width', 'height'))
            if action not in ('down', 'move', 'up', 'cancel'):
                raise ValueError('Unknown pointer action')
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (x, y, width, height)):
                raise ValueError('Pointer coordinates must be integers')
            if not (1 <= width <= 16384 and 1 <= height <= 16384 and 0 <= x < width and 0 <= y < height):
                raise ValueError('Pointer coordinates differ from framebuffer bounds')
        except (ValueError, KeyError, TypeError):
            return web.json_response({'ok': False, 'error': 'Invalid pointer event'}, status=400)
        try:
            began = self.measurement.pointer_start(action, x, y) if self.measurement else None
            sent = await self._emit_input(action, x, y)
        except (OSError, ConnectionError, asyncio.TimeoutError) as error:
            if self.measurement:
                self.measurement.pointer_end(action, began, False)
            self.last_input_error = str(error)
            return web.json_response({'ok': False, 'error': str(error) or 'Input channel timed out'}, status=502)
        if self.measurement:
            self.measurement.pointer_end(action, began, sent)
        return web.json_response({'ok': True, 'sent': sent})

    async def _bridge(self, request: web.Request) -> web.StreamResponse:
        self._check_origin(request)
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', self.rfb_port), timeout=3)
        except (OSError, asyncio.TimeoutError) as error:
            raise web.HTTPBadGateway(text='The local RFB server is not accepting connections') from error
        ws = web.WebSocketResponse(protocols=('binary',), max_msg_size=16 * 1024 * 1024, autoping=True)
        try:
            await ws.prepare(request)
        except BaseException:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            raise
        self._sessions[ws] = writer

        async def tcp_to_ws() -> None:
            try:
                while chunk := await reader.read(65536):
                    await ws.send_bytes(chunk)
            except (OSError, ConnectionError):
                pass
            finally:
                await ws.close()

        tcp_pump = asyncio.create_task(tcp_to_ws(), name='RFB-to-WebSocket')
        try:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    writer.write(message.data)
                    await writer.drain()
                elif message.type == WSMsgType.TEXT:
                    await ws.close(code=1003, message=b'Binary WebSocket frames are required')
                    break
                elif message.type == WSMsgType.ERROR:
                    break
        except (OSError, ConnectionError):
            pass
        finally:
            tcp_pump.cancel()
            await asyncio.gather(tcp_pump, return_exceptions=True)
            self._sessions.pop(ws, None)
            if not self._sessions and self.touch_input and self._input_active:
                with contextlib.suppress(Exception):
                    await self._emit_input('up', *self._last_input_position)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            await ws.close()
        return ws

    async def _shutdown(self) -> None:
        if self.touch_input and self._input_active:
            with contextlib.suppress(Exception):
                await self._emit_input('up', *self._last_input_position)
        if self._input_writer is not None:
            self._input_writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._input_writer.wait_closed(), timeout=1)
            self._input_writer = None
            self._input_reader = None
        sessions = list(self._sessions.items())
        for ws, writer in sessions:
            writer.close()
        if sessions:
            await asyncio.gather(*(ws.close(code=1001, message=b'Viewer closing') for ws, _ in sessions), return_exceptions=True)
        if self._runner is not None:
            runner, self._runner = self._runner, None
            await runner.cleanup()

    def close(self) -> None:
        """Stop serving and release HTTP, WebSocket, TCP, loop, and thread resources."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            if loop is not None and loop.is_running():
                if threading.current_thread() is self._thread:
                    raise RuntimeError('Call close() from outside the viewer thread')
                future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                try:
                    future.result(timeout=6)
                finally:
                    loop.call_soon_threadsafe(loop.stop)
            if self._thread.is_alive():
                self._thread.join(timeout=8)
            if self._thread.is_alive():
                raise TimeoutError('The viewer thread did not exit')

    def __enter__(self) -> 'VNCViewerServer':
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
