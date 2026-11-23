
import hashlib
import json
import os
import re
import struct
import sys
from urllib.parse import parse_qs, urlsplit
import time
import original_8704_vm as api

def native_vlm_terminal(data):
    if b'System-wide panic initiated' not in data and b'PANIC 1 in PANIC' not in data:
        return None
    pattern=rb'(?:\x1b\[[0-9;]*m)?([0-9]+) HYP:\[\+([0-9]+) ms\] \[[0-9]+ ms\] (?:System-wide panic initiated from VM([0-9]+)\.|PANIC 1 in PANIC)'
    for line in data.splitlines():
        match=re.fullmatch(pattern,line)
        if match:
            vmid=int(match[3]) if match[3] is not None else None
            return {'state':'system-wide-panic-observed' if vmid is not None else 'el2-recursive-panic-observed',
                    'initiator_vmid':vmid,'cpu':int(match[1]),'hypervisor_elapsed_ms':int(match[2]),
                    'literal_line':line.decode('ascii')}
    return None

class ConsoleAdapter(api.NativeInteractiveConsole):
    def __init__(self, session, byte_delay_ms=3):
        from native_uart_console import configure_session
        configure_session(session)
        super().__init__(session,byte_delay_ms)
        policy=json.loads((self.session/'linux-initcall-dtb.json').read_bytes())
        self.staged_bash=(policy.get('local_linux_shell') is True and
            policy.get('local_linux_shell_tty') in ('/dev/ttySAC0','/dev/ttySAC3','/dev/ttySAC4') and
            policy.get('local_linux_shell_mode') in
            ('early-usi03-drop-privileges','early-usi04-drop-privileges','early-shared-owner-api-drop-privileges'))

    def serial(self, guest, command):
        if guest!='linux' or not self.staged_bash:
            return super().serial(guest,command)
        if not isinstance(command,str) or not command.strip() or not command.isprintable():
            raise ValueError('Require one printable command line')
        line=command.encode('utf-8')+b'\n'
        if len(line)>240:
            raise ValueError('Native UART command exceeds 240 bytes including line feed')
                                                                            
                                                                                
        return self._transmit(guest,line)

    def return_key(self, guest):
        if guest=='linux' and self.staged_bash:
            return self._transmit(guest,b'\n')
        return super().return_key(guest)

    def native_panic_state(self):

        if getattr(self,'_native_panic_latched',None):return self._native_panic_latched
        path=self.session/'exynos-uart.bin'
        try:
            with path.open('rb') as f:
                f.seek(0,2);end=f.tell();start=max(0,end-1024*1024);f.seek(start);data=f.read(end-start)
        except FileNotFoundError:return None
        panic=native_vlm_terminal(data)
        if panic:
            self._native_panic_latched=dict(panic,source=str(path),
                scope='Original vLM UART event; new session required before further input.')
            return self._native_panic_latched
        return None

    def _transmit(self,guest,line):
        if self.native_panic_state() is not None:
            raise ValueError('Original vLM system-wide panic observed; no further UART bytes sent. Inspect first guest failure.')
        return super()._transmit(guest,line)

    def status(self):
        result=super().status()
        dev=self.session/'offline-development.json'
        if dev.is_file():
            profile=json.loads(dev.read_bytes())
            for guest in ('linux','android'):
                result[guest]['offline_development']=profile
                result[guest]['source_state']='【离线开发：数据未加密，非原厂安全启动】'+result[guest].get('source_state','')
        result['linux']['command_line_ending']='LF' if self.staged_bash else 'CR'
        if self.direct and self.direct.get('serial_index')==4:
            result['linux']['source_state']='原厂 VM3 ttySAC4 独立输入；命令执行以原 UART 挑战输出为准。'
        receipt=self.session/'virtual-acpm-rx-predecessor.json'
        if receipt.is_file() or os.environ.get('OEM_8704_VIRTUAL_ACPM_RX')=='virtual-intmr0-unmasked-v1':
            warning='【虚拟前级条件：INTMR0/只读诊断快照；非原厂冷启动验收】'
            for guest in ('linux','android'):
                result[guest]['virtual_predecessor_condition']='virtual-intmr0-unmasked-v1'
                result[guest]['source_state']=warning+result[guest].get('source_state','')
        state=self.session/'native-runtime-state.json'
        if state.is_file():
            runtime=json.loads(state.read_bytes())
            runtime['observed_fresh']=0<=time.time()-runtime.get('observed_at_unix',0)<8
            for guest in ('linux','android'):result[guest]['native_runtime']=runtime
        panic=self.native_panic_state()
        if panic is not None:
            for guest in ('linux','android'):
                result[guest]['system_wide_panic']=panic
                result[guest]['serial_writable']=False
                result[guest]['running']=False
                result[guest]['error']='Original VM3-triggered system-wide panic; inspect first guest failure, not FIFO capacity.' if panic['initiator_vmid']==3 else 'Original system-wide panic observed; guest recovery not verified.'
                result[guest]['source_state']='【原vLM整机panic；禁止继续发送】'+result[guest].get('source_state','')
        return result

    def request_native_stop(self):
        policy=self.session/'native-runtime-lifetime.json'
        if not policy.is_file() or json.loads(policy.read_bytes()).get('profile')!='until-stop-v1':
            raise ValueError('This session is bounded; persistent runtime control not enabled')
        path=self.session/'native-stop.request'
        if path.is_symlink():raise ValueError('Unexpected stop marker link')
        try:
            with path.open('x',encoding='ascii') as f:f.write('user-request\n')
        except FileExistsError:pass
        return {'stop_requested':True,'stopped':False,'guest_execution_verified':False,
                'scope':'Host lifecycle request; completion requires actual owner QMP stopped state'}


class NativeDisplays(api.Displays):

    def guest_panic(self):
        return ConsoleAdapter.native_panic_state(self.native) if self.native else None

    def native_center(self, with_proof=False):
        if self.guest_panic():return None
        if (self.native and os.name=='nt' and self.native.focus() is not None and
                (self.native.session/'native-runtime-state.json').is_file()):
            from native_center_live import LiveCenterFeed
            if not hasattr(self.native,'_center_live_feed'):
                self.native._center_live_feed=LiveCenterFeed(self.native.session)
            sample=self.native._center_live_feed.latest()
            if sample is not None:
                if sample[1].get('nonzero') is not True:return None
                return sample if with_proof else sample[0]
        return super().native_center(with_proof)

    def native_center_foreground(self, with_proof=False):
        if self.guest_panic():return None
        feed=getattr(self.native,'_center_live_feed',None)
        sample=feed.latest() if feed is not None else None
        if sample is not None and sample[1].get('nonzero') is not True:return None
        return super().native_center_foreground(with_proof)

    def native_qnx(self, name, with_proof=False):
        return None if self.guest_panic() else super().native_qnx(name, with_proof)

    def center_capture(self):
        from linux_pid1_context import LINUX_SHA
        if not self.native or self.guest_panic() or self.native.focus() is None:
            return None
        session=self.native.session
        stem='native-center-last-nonzero-foreground'
        try:
            meta_path=session/(stem+'.json')
            png_path=session/(stem+'.png')
            if any(p.resolve(strict=True).parent!=session or p.is_symlink() for p in (meta_path,png_path)):
                return None
            with meta_path.open('rb') as f:
                raw=f.read(16385)
            if len(raw)>16384:
                return None
            meta=json.loads(raw)
            if not isinstance(meta,dict):
                return None
            stamp=meta.get('captured_monotonic')
            sha=meta.get('png_sha256')
            rgb=meta.get('nonzero_rgb_pixels')
            w,h=1560,700
            if (meta.get('status')!='original_premultiplied_foreground_profile' or
                    meta.get('kernel_sha256')!=LINUX_SHA or meta.get('secure') is not False or
                    meta.get('capture_origin')!='original_sysmmu' or
                    meta.get('full_display') is not False or meta.get('live_stream') is not False or
                    (meta.get('width'),meta.get('height'))!=(w,h) or
                    meta.get('out_of_premultiplied_range_pixels')!=0 or
                    type(rgb) is not int or not 0<rgb<=w*h or
                    type(stamp) not in (int,float) or not 0<=stamp<=time.monotonic() or
                    not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{64}',sha)):
                return None
            with png_path.open('rb') as f:
                png=f.read((8<<20)+1)
            if (not 33<=len(png)<=8<<20 or png[:16]!=b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR' or
                    struct.unpack_from('>IIBB',png,16)!=(w,h,8,6) or
                    hashlib.sha256(png).hexdigest()!=sha):
                return None
            return png,meta
        except (OSError,ValueError):
            return None

    def status(self):
        rows=super().status()
        panic=self.guest_panic()
        if panic:
            for row in rows.values():
                row.update(available=False, foreground_available=False,
                    original_scanout=False, factory_hmi_verified=False,
                    system_wide_panic=panic,
                    reason='原vLM整机panic：残留扫描缓冲不是实时画面，已停止发布。')
                row.pop('frame_version',None)
        else:
            center=rows['center']
            if not (center['available'] or center.get('foreground_available')):
                sample=self.center_capture()
                if sample:
                    center.update(capture_available=True,frame_version=sample[1]['png_sha256'],
                                  reason='本次原厂中控前景留存')
        rows['center']['source_extent']={'width':1560,'height':700,'complete_hmi_verified':False}
        rows['ic']['gui_verified']=False
        rows['hud']['optical_fields']={name:{'mapping_verified':False,'available':False}
            for name in ('near_status','far_ar')}
        return rows


_BaseNativeServer=api.ThreadingHTTPServer
class NativeRuntimeServer(_BaseNativeServer):
    def server_close(self):
        control=getattr(self,'native_console',None);feed=getattr(control,'_center_live_feed',None)
        if feed is not None:feed.close()
        return super().server_close()

    def __init__(self,address,handler):
        class RuntimeHandler(handler):
            def do_POST(self):
                if self.path!='/api/native-stop':return super().do_POST()
                if not self.authorized():return self.reply(403,{'error':'Invalid local session token/host/origin'})
                try:
                    n=int(self.headers.get('Content-Length','0'))
                    if n!=2 or self.rfile.read(n)!=b'{}':raise ValueError('Require exact empty JSON object')
                    control=getattr(self.server,'native_console',None)
                    if not hasattr(control,'request_native_stop'):raise ValueError('Native persistent control unavailable')
                    return self.reply(202,control.request_native_stop())
                except (ValueError,OSError,RuntimeError) as e:return self.reply(409,{'error':str(e)})
            def do_GET(self):
                parsed=urlsplit(self.path)
                if parsed.path=='/api/center-capture':
                    if not self.authorized():
                        return self.reply(403,{'error':'Invalid local session token/host/origin'})
                    versions=parse_qs(parsed.query,keep_blank_values=True).get('version',[])
                    native=getattr(self.server,'native_console',None)
                    if len(versions)!=1 or not re.fullmatch(r'[a-f0-9]{64}',versions[0]):
                        return self.reply(409,{'error':'需要留存帧版本'})
                    try:
                        data=NativeDisplays.match_frame(NativeDisplays({},native).center_capture(),versions[0])
                        if data is None:
                            raise ValueError('本次无中控留存帧')
                        return self.reply(200,data,'image/png')
                    except (OSError,ValueError) as error:
                        return self.reply(409,{'error':str(error)})
                if self.path!='/':return super().do_GET()
                html=(api.HERE/'console.html').read_bytes()
                banner='<aside role="alert" style="padding:12px;background:#713d15;color:white">8704 · 本机离线开发</aside>'.encode('utf-8')
                marker=b'<header>'
                if html.count(marker)!=1:raise ValueError('Offline warning requires one original page header')
                html=html.replace(marker,banner+marker,1)
                controls=(RUNTIME_CONTROLS+DISPLAY_PRESENTATION).encode('utf-8')
                return self.reply(200,html.replace(b'</body>',controls+b'</body>',1) if b'</body>' in html else html+controls,'text/html; charset=utf-8')
        super().__init__(address,RuntimeHandler)


RUNTIME_CONTROLS='''
<div style="position:sticky;bottom:0;background:#142338;color:white;padding:8px;z-index:1000">
<span id="nativeLifetimeLabel">正在读取原生运行状态；不代表启动验收。</span>
<button id="nativeLifetimeStop" disabled>停止本机原生会话</button></div>
<script>(()=>{const b=document.getElementById('nativeLifetimeStop'),l=document.getElementById('nativeLifetimeLabel');
const t=new URLSearchParams(location.hash.slice(1)).get('token')||'';
const h={'X-8704-Token':t,'Content-Type':'application/json'};
async function poll(){try{const r=await fetch('/api/status',{headers:h});if(!r.ok)return;const j=await r.json(),s=j.linux.native_runtime;
const active=s&&s.profile==='until-stop-v1'&&s.observed_fresh&&s.running;b.disabled=!active;
l.textContent=j.linux.system_wide_panic?'原vLM整机panic；QMP运行不代表客体存活，停止并检查首发故障':active?'原生常驻运行（不代表三系统完整启动验收）':s?'原生运行已停或状态待更新':'当前为限时观察模式';}catch(e){b.disabled=true;l.textContent='原生运行状态暂不可读';}}
b.onclick=async()=>{if(!confirm('停止当前本机原生会话并保存真实退出证据？'))return;b.disabled=true;
try{const r=await fetch('/api/native-stop',{method:'POST',headers:h,body:'{}'});l.textContent=r.ok?'已请求停止，等待实际QMP停止确认':'停止请求被拒绝';}catch(e){l.textContent='停止请求未送达';}};
poll();setInterval(poll,2000);})();</script>
'''


DISPLAY_PRESENTATION='''
<style>
#center .screen{overflow:auto}
#center .screen img{max-height:none;height:auto}
#center.native-pixel-scale .screen{justify-content:flex-start}
#center.native-pixel-scale .screen img{width:1920px;max-width:none;flex:none}
.hud-optical-fields{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px}
.hud-optical-fields div{border:1px dashed #596b80;padding:8px;line-height:1.7;color:#c7ac7f}
</style>
<script>(()=>{
const center=document.querySelector('[data-display="center"]');
if(center){
 const b=document.createElement('button');b.type='button';b.id='center-pixel-scale';
 b.textContent='原始像素 1:1';b.setAttribute('aria-pressed','false');
 b.onclick=()=>{const on=document.getElementById('center').classList.toggle('native-pixel-scale');
  b.setAttribute('aria-pressed',String(on));b.textContent=on?'完整范围适配':'原始像素 1:1';};
 center.querySelector('.title').append(b);
 const n=document.createElement('div');n.className='note';
 n.textContent='中控源画面（目标 1920×1080）：适配显示保留全范围；1:1 可横向查看。';center.append(n);
}
const ic=document.querySelector('[data-display="ic"]');
if(ic)ic.querySelector('[data-role="title"]').textContent='04 / IC 仪表';
const hud=document.querySelector('[data-display="hud"]');
if(hud){
 hud.querySelector('[data-role="title"]').textContent='05 / HUD';
 const fields=document.createElement('div');fields.className='hud-optical-fields';
 for(const [name,title]of[['near_status','近场状态区'],['far_ar','远场 AR 区']]){
  const f=document.createElement('div');f.dataset.opticalField=name;
  f.textContent=title+'：尚无已验证实时映射';fields.append(f);
 }
 hud.append(fields);
}
})();</script>
'''


def main():
                                                                                
                                                                              
    if '--native-session' in sys.argv:
        index=sys.argv.index('--native-session')+1
        if index<len(sys.argv):
            from native_uart_console import configure_session
            configure_session(sys.argv[index])
                                                                              
                                                                        
    api.NativeInteractiveConsole=ConsoleAdapter
    api.Displays=NativeDisplays
    api.ThreadingHTTPServer=NativeRuntimeServer
    api.main()

if __name__=='__main__':
    main()
