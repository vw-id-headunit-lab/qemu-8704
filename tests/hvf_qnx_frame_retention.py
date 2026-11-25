from pathlib import Path
import subprocess,sys,json
source=Path(sys.argv[1]);text=source.read_text();start=text.find('function nativeFrameView(role){');end=text.find("\nfor(const role of ['ic','hud'])",start)
record={'source':str(source.resolve()),'input':'Successful exact-epoch image fetch, unavailable refresh, wrong epoch, owner reset, new exact epoch; pure JS DOM fixture, not native pixel evidence'}
if start<0 or end<0:record.update(exit=2,error='No epoch-bound retained image view');print(json.dumps(record));raise SystemExit(2)
js=r'''
let qnxEpoch='epoch-A',now=10000,serial=0,revoked=[];Date.now=()=>now;
const nodes={'hud-frame':{hidden:true,removeAttribute(){delete this.src}},'hud-note':{}};
const document={getElementById:id=>nodes[id]};const URL={createObjectURL:()=>('blob-fixture-'+(++serial)),revokeObjectURL:x=>revoked.push(x)};
let answer={ok:true,epoch:'epoch-A',age:'2'},fetches=0;async function fetch(){fetches++;return{ok:answer.ok,headers:{get:k=>k==='X-QNX-Epoch'?answer.epoch:answer.age},blob:async()=>({fixture:true})}}
function check(condition,message){if(!condition)throw Error(message)}
'''+text[start:end]+r'''
(async()=>{const view=nativeFrameView('hud');await view.refresh();check(!nodes['hud-frame'].hidden,'First exact-epoch image visible');const first=nodes['hud-frame'].src;check(nodes['hud-note'].textContent.includes('2 秒前'),'Actual frame age shown');now=30000;answer.ok=false;await view.refresh();check(nodes['hud-frame'].src===first&&!nodes['hud-frame'].hidden,'HTTP503 must retain actual prior blob');check(nodes['hud-note'].textContent.includes('22 秒前'),'Capture timestamp never freshened by failed fetch');answer={ok:true,epoch:'wrong-epoch',age:'0'};await view.refresh();check(nodes['hud-frame'].src===first&&serial===1,'Wrong owner never replaces last verified image');qnxEpoch='epoch-B';view.reset();check(nodes['hud-frame'].hidden&&!nodes['hud-frame'].src&&revoked.includes(first),'Owner reset removes and revokes old image');answer={ok:true,epoch:'epoch-B',age:'1'};await view.refresh();check(!nodes['hud-frame'].hidden&&nodes['hud-frame'].src!==first,'New owner needs new successful fetch');console.log(JSON.stringify({checks:6,fetches,passed:true,fixture:true}))})().catch(e=>{console.error(e.stack);process.exitCode=1});
'''
p=subprocess.run(['node','-e',js],capture_output=True,text=True);record.update(stdout=p.stdout,stderr=p.stderr,exit=p.returncode);print(json.dumps(record,ensure_ascii=False,indent=2));raise SystemExit(p.returncode)
