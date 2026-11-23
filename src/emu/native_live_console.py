
import json
import os
import re
import sys
import time
import original_8704_vm as api

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
        pattern=rb'(?:\x1b\[[0-9;]*m)?[0-9]+ HYP:\[\+([0-9]+) ms\] \[([0-9]+) ms\] System-wide panic initiated from VM([0-9]+)\.'
        for line in data.splitlines():
            match=re.fullmatch(pattern,line)
            if match:
                self._native_panic_latched={'state':'system-wide-panic-observed','initiator_vmid':int(match[3]),
                    'hypervisor_elapsed_ms':int(match[1]),'source':str(path),'literal_line':line.decode('ascii'),
                    'scope':'Original vLM UART event; not QMP running or guest recovery. New session required before further input.'}
                return self._native_panic_latched
        return None

    def _transmit(self,guest,line):
        if self.native_panic_state() is not None:
            raise ValueError('Original vLM system-wide panic observed; no further UART bytes sent. Inspect first guest failure.')
        return super()._transmit(guest,line)

    def status(self):
        result=super().status()
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
        return None if self.guest_panic() else super().native_center(with_proof)

    def native_center_foreground(self, with_proof=False):
        return None if self.guest_panic() else super().native_center_foreground(with_proof)

    def native_qnx(self, name, with_proof=False):
        return None if self.guest_panic() else super().native_qnx(name, with_proof)

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
            rows['ic']['reason']+=' 扫描像素不等于完整仪表GUI，不能用数值或测试色块代替。'
            rows['hud']['reason']+=' 近场状态区与远场AR区须分别验收；GTF离屏条带不等于完整HUD。'
        rows['center']['source_extent']={'width':1560,'height':700,'complete_hmi_verified':False}
        rows['ic']['gui_verified']=False
        rows['hud']['optical_fields']={name:{'mapping_verified':False,'available':False}
            for name in ('near_status','far_ar')}
        return rows


_BaseNativeServer=api.ThreadingHTTPServer
class NativeRuntimeServer(_BaseNativeServer):
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
                if self.path!='/':return super().do_GET()
                html=(api.HERE/'console.html').read_bytes()
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
#center.native-pixel-scale .screen img{width:1560px;max-width:none;flex:none}
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
 n.textContent='中控源 1560×700：适配显示保留全范围；1:1 可横向查看。源图缺块尚未修复，不拉伸补图。';center.append(n);
}
const ic=document.querySelector('[data-display="ic"]');
if(ic)ic.querySelector('[data-role="title"]').textContent='04 / IC 像素源 · 非已验证仪表GUI';
const hud=document.querySelector('[data-display="hud"]');
if(hud){
 hud.querySelector('[data-role="title"]').textContent='05 / HUD 原始输出 · 双区映射待验证';
 const fields=document.createElement('div');fields.className='hud-optical-fields';
 for(const [name,title]of[['near_status','近场状态区'],['far_ar','远场 AR 区']]){
  const f=document.createElement('div');f.dataset.opticalField=name;
  f.textContent=title+'：尚无已验证实时映射';fields.append(f);
 }
 hud.append(fields);
 const n=document.createElement('div');n.className='note';
 n.textContent='1140×114 GTF 离屏条带 ≠ 1120×780 WDI 输出；不擅自裁成两幅，不复制同帧充当近/远场。';hud.append(n);
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
