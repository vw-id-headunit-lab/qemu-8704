
import argparse
from collections import deque
import ctypes as c
import ctypes.wintypes as w
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

from native_center_live import NativeCenterReader,center_reader
import offline_touch_input as touch


def viewport(width,height,panel=(1560,700)):
 scale=max(0,min(width/panel[0],(height-36)/panel[1]))
 ww,hh=int(panel[0]*scale),int(panel[1]*scale)
 return (width-ww)//2,36+(height-36-hh)//2,ww,hh


def panel_point(x,y,width,height,clamp=False,panel=(1560,700)):
 left,top,ww,hh=viewport(width,height,panel)
 if ww<=0 or hh<=0:return None
 if not clamp and not (left<=x<left+ww and top<=y<top+hh):return None
 return max(0,min(panel[0]-1,int((x-left)*panel[0]/ww))),max(0,min(panel[1]-1,int((y-top)*panel[1]/hh)))


def dib_pixels(raw,proof,ignore_alpha=False):
 width,height,pitch=(proof[k] for k in ('width','height','pitch'))
 if ((width,height)not in ((1560,700),(1920,1080)) or not width*4<=pitch<=16384 or
     len(raw)!=pitch*height or proof.get('format') not in ('XR24','AR24')):
  raise ValueError('Unsupported original pixel layout')
 blend=proof.get('original_wod_blend',{})
 if blend.get('verified') is True and blend.get('plane_alpha8')!=255:
  raise ValueError('Original multi-plane composition required')
 rows=raw if pitch==width*4 else b''.join(raw[y*pitch:y*pitch+width*4]for y in range(height))
 if proof['format']=='AR24' and not ignore_alpha and rows[3::4].count(255)!=width*height:
  raise ValueError('Original transparent plane composition required')
 return bytes(rows)


class PresenterClock:
 def __init__(self):self.last=None;self.times=deque()
 def presented(self,version,now,visible):
  if visible and version!=self.last:self.last=version;self.times.append(now)
  return self.fps(now)
 def fps(self,now):
  while self.times and self.times[0]<now-2:self.times.popleft()
  return max(0,len(self.times)-1)/2


class PixelFeed:
 def __init__(self,session):
  self.session=Path(session);self.current=None;self.error='Waiting for original pixels'
  self.closed=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def run(self):
  reader=None;last=None;active=0
  try:
   while not self.closed.is_set():
    start=time.monotonic()
    try:
     if reader is None:reader=center_reader(self.session)
     raw,proof=reader.sample(raw_pixels=True)
     pixels=dib_pixels(raw,proof,reader.scan['original_wod_ignores_pixel_alpha'](proof))
     digest=proof['pixel_buffer_sha256']
     if digest!=last:last=digest;active=start+1
     self.current=(pixels,proof);self.error=None
     delay=1/30 if start<active else .2
    except (OSError,ValueError,KeyError,RuntimeError)as error:
     self.current=None;self.error=str(error);delay=.2
     if (self.session/'integrity.json').exists():break
    self.closed.wait(max(.005,delay-(time.monotonic()-start)))
  finally:
   self.current=None
   if reader is not None:reader.close()
 def latest(self):
  result=self.current
  return result if result and 0<=time.monotonic()-result[1]['captured_monotonic']<.75 else None
 def close(self):self.closed.set();self.thread.join(timeout=3)


def sensor_events(events,panel):
 if panel==(1560,700):return events
 if panel!=(1920,1080):raise ValueError('Unsupported source geometry')
 return [(x,min(y,1023),phase,tick)for x,y,phase,tick in events]


def stationary_tap(events):
 return (len(events)>=2 and events[0][2]=='down' and events[-1][2]=='up' and
         events[-1][:2]==events[0][:2] and
         all(event[:2]==events[0][:2] and event[2]=='move' for event in events[1:-1]))


class TouchSender:
 def __init__(self,session):
  self.session=Path(session);self.pending=queue.Queue(maxsize=1);self.busy=False;self.result=''
  self.closed=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def submit(self,events,panel=(1560,700)):
  if self.busy or self.closed.is_set():return False
  self.busy=True;self.pending.put_nowait((sensor_events(events,panel),(1920,1024)if panel==(1920,1080)else(1560,700)));return True
 def command(self,payload):
  submitted=time.time();began=time.monotonic()
  state=json.loads((self.session/'native-runtime-state.json').read_bytes())
  if not state['running'] or (self.session/'integrity.json').exists():raise ValueError('Original session ended')
  uri=urllib.parse.urlsplit(json.loads((self.session/'native-console-server.json').read_bytes())['url'])
  if uri.scheme!='http' or uri.hostname!='127.0.0.1' or not uri.port:raise ValueError('Owned loopback console required')
  token=urllib.parse.parse_qs(uri.fragment)['token'][0]
  op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  headers={'X-8704-Token':token,'Content-Type':'application/json'}
  def call(path,data=None):
   with op.open(urllib.request.Request(f'http://127.0.0.1:{uri.port}/api/'+path,
    headers=headers,data=None if data is None else json.dumps(data).encode()),timeout=10)as response:return json.load(response)
  status=call('status')['linux']
  if not status['board_running'] or not status['serial_writable']:raise ValueError('Original input unavailable')
  nonce=secrets.token_hex(3);command=f'echo;echo B{nonce}; {payload}; r=$?; echo; echo RC:$r; echo E{nonce}'
  if len(command.encode())>=240:raise ValueError('Original UART limit')
  path=self.session/'linux-usi04-console-rx.bin';offset=path.stat().st_size
  call('serial',{'guest':'linux','command':command});deadline=time.monotonic()+30
  pattern=rb'\nB'+nonce.encode()+rb'\r?\n(.*?)\nE'+nonce.encode()+rb'\r?\n'
  while not self.closed.wait(.1):
   with path.open('rb')as stream:stream.seek(offset);data=stream.read(4<<20)
   match=re.search(pattern,data,re.S)
   if match:
    output=match[1].decode('utf8','backslashreplace')
    with (self.session/'native-window-input.jsonl').open('a',encoding='utf8')as stream:
     stream.write(json.dumps({'command':command,'stdout':output,'session':str(self.session),'submitted_unix':submitted,'receipt_unix':time.time(),'elapsed_seconds':time.monotonic()-began,'window_process_pid':os.getpid()})+'\n')
    if not re.search(r'(?:^|\n)RC:0\r?(?:\n|$)',output):raise ValueError('Original input command failed')
    return
   if time.monotonic()>deadline:raise TimeoutError('Original input receipt timeout')
  raise ValueError('Input window closed')
 def run(self):
  ready=False;gesture_ready=False
  while not self.closed.is_set():
   try:events,packet_panel=self.pending.get(timeout=.2)
   except queue.Empty:continue
   try:
    if not ready:
     for command in touch.fast_setup_commands():self.command(command)
     ready=True
    if stationary_tap(events):
     commands=[touch.fast_tap_command(*events[0][:2],events[-1][3],panel=packet_panel)]
    else:
     if not gesture_ready:self.command(touch.fast_gesture_setup_command());gesture_ready=True
     _,commands=touch.fast_gesture_commands(events,panel=packet_panel)
    for command in commands:self.command(command)
    self.result='Original KIPC receipt OK'
   except (OSError,ValueError,KeyError,RuntimeError)as error:self.result=str(error);ready=False;gesture_ready=False
   finally:self.busy=False
 def close(self):self.closed.set();self.thread.join(timeout=2)


def show(feed,sender=None,seconds=0,preview=False):
 user=c.WinDLL('user32',use_last_error=True);gdi=c.WinDLL('gdi32',use_last_error=True);kernel=c.WinDLL('kernel32',use_last_error=True)
 LRESULT=c.c_ssize_t;WPARAM=c.c_size_t;LPARAM=c.c_ssize_t
 CALLBACK=c.WINFUNCTYPE(LRESULT,w.HWND,w.UINT,WPARAM,LPARAM)
 class WindowClass(c.Structure):
  _fields_=[('style',w.UINT),('proc',CALLBACK),('class_extra',c.c_int),('window_extra',c.c_int),('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('background',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
 class Paint(c.Structure):
  _fields_=[('dc',w.HDC),('erase',w.BOOL),('rect',w.RECT),('restore',w.BOOL),('update',w.BOOL),('reserved',c.c_byte*32)]
 class Bitmap(c.Structure):
  _fields_=[('size',w.DWORD),('width',w.LONG),('height',w.LONG),('planes',w.WORD),('bits',w.WORD),('compression',w.DWORD),('image_size',w.DWORD),('xppm',w.LONG),('yppm',w.LONG),('used',w.DWORD),('important',w.DWORD)]
 def api(dll,name,args,ret):
  fn=getattr(dll,name);fn.argtypes=args;fn.restype=ret;return fn
 api(kernel,'GetModuleHandleW',[w.LPCWSTR],w.HMODULE)
 api(user,'DefWindowProcW',[w.HWND,w.UINT,WPARAM,LPARAM],LRESULT)
 api(user,'CreateWindowExW',[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p],w.HWND)
 api(user,'RegisterClassW',[c.POINTER(WindowClass)],w.WORD)
 api(user,'BeginPaint',[w.HWND,c.POINTER(Paint)],w.HDC);api(user,'EndPaint',[w.HWND,c.POINTER(Paint)],w.BOOL)
 api(user,'GetClientRect',[w.HWND,c.POINTER(w.RECT)],w.BOOL)
 api(user,'FillRect',[w.HDC,c.POINTER(w.RECT),w.HBRUSH],c.c_int)
 api(user,'InvalidateRect',[w.HWND,c.c_void_p,w.BOOL],w.BOOL)
 api(user,'SetTimer',[w.HWND,c.c_size_t,w.UINT,c.c_void_p],c.c_size_t)
 api(user,'IsIconic',[w.HWND],w.BOOL);api(user,'IsWindowVisible',[w.HWND],w.BOOL)
 api(user,'DestroyWindow',[w.HWND],w.BOOL);api(user,'ShowWindow',[w.HWND,c.c_int],w.BOOL)
 api(user,'SetCapture',[w.HWND],w.HWND);api(user,'ReleaseCapture',[],w.BOOL)
 api(user,'LoadCursorW',[w.HINSTANCE,c.c_void_p],w.HANDLE)
 api(user,'GetMessageW',[c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT],w.BOOL)
 api(user,'TranslateMessage',[c.POINTER(w.MSG)],w.BOOL);api(user,'DispatchMessageW',[c.POINTER(w.MSG)],LRESULT)
 api(gdi,'GetStockObject',[c.c_int],w.HANDLE)
 api(gdi,'StretchDIBits',[w.HDC,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_int,c.c_void_p,c.POINTER(Bitmap),w.UINT,w.DWORD],c.c_int)
 api(gdi,'SetTextColor',[w.HDC,w.DWORD],w.DWORD);api(gdi,'SetBkMode',[w.HDC,c.c_int],c.c_int)
 api(gdi,'TextOutW',[w.HDC,c.c_int,c.c_int,w.LPCWSTR,c.c_int],w.BOOL)
 clock=PresenterClock();bitmap=Bitmap(c.sizeof(Bitmap),1560,-700,1,32,0,1560*700*4,0,0,0,0)
 panel_size=(1560,700)
 current=None;buffer=None;events=[];gesture_start=0;started=time.monotonic();paints=0;last_status=None;error=None;paint_ms=[]
 def rect(hwnd):
  box=w.RECT();user.GetClientRect(hwnd,c.byref(box));return box
 @CALLBACK
 def proc(hwnd,msg,wp,lp):
  nonlocal current,buffer,events,gesture_start,paints,last_status,error,panel_size
  try:
   if msg==0x113:
    if seconds and time.monotonic()-started>=seconds:user.DestroyWindow(hwnd);return 0
    latest=feed.latest();status=(latest[1]['pixel_buffer_sha256']if latest else None,feed.error,int(time.monotonic()*2))
    if status!=last_status:
     current=latest;buffer=c.create_string_buffer(latest[0])if latest and (not buffer or status[0]!=getattr(buffer,'digest',None))else buffer
     if latest:
      panel_size=(latest[1].get('width',1560),latest[1].get('height',700));bitmap.width=panel_size[0];bitmap.height=-panel_size[1];bitmap.image_size=panel_size[0]*panel_size[1]*4
     if buffer and latest:buffer.digest=status[0]
     last_status=status;user.InvalidateRect(hwnd,None,False)
    return 0
   if msg==0xf:
    paint=Paint();dc=user.BeginPaint(hwnd,c.byref(paint))
    try:
     box=rect(hwnd);user.FillRect(dc,c.byref(box),gdi.GetStockObject(4))
     latest=feed.latest();live=current and latest and current[1]['pixel_buffer_sha256']==latest[1]['pixel_buffer_sha256']
     if live and buffer:
      left,top,ww,hh=viewport(box.right,box.bottom,panel_size)
      if ww and hh:
       before=time.perf_counter()
       result=gdi.StretchDIBits(dc,left,top,ww,hh,0,0,panel_size[0],panel_size[1],buffer,c.byref(bitmap),0,0x00cc0020)
       if result not in (0,-1):
        paints+=1
        paint_ms.append((time.perf_counter()-before)*1000)
        if len(paint_ms)>300:del paint_ms[0]
        if not preview:clock.presented(current[1]['pixel_buffer_sha256'],time.monotonic(),user.IsWindowVisible(hwnd)and not user.IsIconic(hwnd))
     label=('OFFLINE PREVIEW - input disabled'if preview else f'{clock.fps(time.monotonic()):.1f} FPS | ORIGINAL PIXELS | '+(('Touch pending'if sender.busy else(sender.result or 'Touch ready'))if live and sender else str(feed.error or 'Waiting')))
     gdi.SetTextColor(dc,0x009aff78);gdi.SetBkMode(dc,1);gdi.TextOutW(dc,10,9,label,len(label))
    finally:user.EndPaint(hwnd,c.byref(paint))
    return 0
   if msg in (0x201,0x200,0x202):
    x=c.c_short(lp&65535).value;y=c.c_short((lp>>16)&65535).value;box=rect(hwnd)
    if msg==0x201:
     point=panel_point(x,y,box.right,box.bottom,panel=panel_size)
     if sender and not sender.busy and feed.latest()and point:
      events=[(*point,'down',0)];gesture_start=time.monotonic();user.SetCapture(hwnd)
    elif events:
     point=panel_point(x,y,box.right,box.bottom,True,panel_size)
     if point:
      event=(*point,'up'if msg==0x202 else 'move',min(255,int((time.monotonic()-gesture_start)*100)))
      if len(events)>=63:events[-1]=event
      else:events.append(event)
     if msg==0x202:
      complete=events;events=[]
      user.ReleaseCapture()
      if feed.latest()and complete[-1][2]=='up':sender.submit(complete,panel_size)
    return 0
   if msg==0x215:events=[];return 0
   if msg==0x14:return 1
   if msg==2:user.PostQuitMessage(0);return 0
  except Exception as exc:error=str(exc);user.DestroyWindow(hwnd);return 0
  return user.DefWindowProcW(hwnd,msg,wp,lp)
 instance=kernel.GetModuleHandleW(None);name='MEB-Original-Pixel-Window-'+str(time.time_ns())
 wc=WindowClass(3,proc,0,0,instance,None,user.LoadCursorW(None,c.c_void_p(32512)),None,None,name)
 if not user.RegisterClassW(c.byref(wc)):raise c.WinError(c.get_last_error())
 hwnd=user.CreateWindowExW(0,name,'MEB original center - '+('OFFLINE PREVIEW'if preview else 'live native pixels'),0x00cf0000,100,100,1260,636,None,None,instance,None)
 if not hwnd:raise c.WinError(c.get_last_error())
 if not user.SetTimer(hwnd,1,16,None):user.DestroyWindow(hwnd);raise c.WinError(c.get_last_error())
 user.ShowWindow(hwnd,5);message=w.MSG()
 while True:
  result=user.GetMessageW(c.byref(message),None,0,0)
  if result==0:break
  if result==-1:raise c.WinError(c.get_last_error())
  user.TranslateMessage(c.byref(message));user.DispatchMessageW(c.byref(message))
 if error:raise RuntimeError(error)
 return {'gdi_paints':paints,'gdi_median_ms':sorted(paint_ms)[len(paint_ms)//2]if paint_ms else None,
  'preview':preview,'dynamic_guest_fps_verified':False}


def main():
 parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group(required=True)
 group.add_argument('--session',type=Path);group.add_argument('--preview',type=Path);parser.add_argument('--seconds',type=float,default=0)
 args=parser.parse_args();sender=None
 if args.preview:
  from PIL import Image
  with Image.open(args.preview)as image:
   if image.size not in ((1560,700),(1920,1080)):raise ValueError('Require original panel dimensions')
   pixels=image.convert('RGB').tobytes('raw','BGRX');preview_size=image.size
  class Preview:
   error=None
   def latest(self):return pixels,{'pixel_buffer_sha256':hashlib.sha256(pixels).hexdigest(),'width':preview_size[0],'height':preview_size[1]}
   def close(self):pass
  feed=Preview()
 else:
  session=args.session.resolve(strict=True)
  if session.parent!=NativeCenterReader.__init__.__globals__['BASE'] or (session/'integrity.json').exists():
   raise ValueError('Require current owned native session')
  if not json.loads((session/'native-runtime-state.json').read_bytes())['running']:
   raise ValueError('Original session ended')
  args.session=session
  feed=PixelFeed(args.session);sender=TouchSender(args.session)
 try:print(json.dumps(show(feed,sender,args.seconds,bool(args.preview))))
 finally:
  feed.close()
  if sender:sender.close()


if __name__=='__main__':main()
