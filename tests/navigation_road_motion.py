from pathlib import Path
import ast,hashlib,importlib.util,json,math,os,signal,struct,subprocess,sys,tempfile,time
root=Path(sys.argv[1])if len(sys.argv)>1 else Path(__file__).resolve().parents[1];source=Path(sys.argv[2]) if len(sys.argv)>2 else root/'src/hvf_bench/native/navigation-gnss.c';records=[]
try:
    text=source.read_text();start=text.index('static int load_gnss_frame(');end=text.index('static void *publish(',start);body=text[start:end]
    assert 'if(!load_gnss_frame(packet))' in text and 'usleep(200000)' in text and 'tick-source_tick>2000000000ULL' in text
    spec=importlib.util.spec_from_file_location('motion',root/'src/hvf_bench/guest/navigation-motion.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    config=root/'simulation.json' if (root/'simulation.json').exists()else root/'var/hvf/navigation-simulation.json';conf=json.loads(config.read_text());route=mod.RoadLoop(conf)
    for i in range(10000):
        t=i*.2;a=route.sample(t);b=route.sample(t+.2)
        assert route.distance((a['longitude'],a['latitude']),(b['longitude'],b['latitude'])) <= route.speed*.2+.02
        assert 0<=a['heading_deg']<360 and 32<a['latitude']<32.1 and 118.7<a['longitude']<118.9
    assert route.distance(route.position(route.length-.0001),route.position(.0001))<.001
    with tempfile.TemporaryDirectory(prefix='gnss-live-fixture-')as tmp:
        d=Path(tmp);frame=d/'gnss.bin';packet=bytearray(847);packet[32]=1;struct.pack_into('<I',packet,33,3);frame.write_bytes(packet)
        c='#include <stdint.h>\n#include <stddef.h>\n#include <fcntl.h>\n#include <unistd.h>\n'+body.replace('"/tmp/bench-navigation/gnss-frame.bin"',json.dumps(str(frame)))+'\nint main(void){unsigned char a[847],b[847];if(!load_gnss_frame(a))return 1;int fd=open('+json.dumps(str(frame))+',O_WRONLY);a[37]=7;if(write(fd,a,847)!=847)return 2;close(fd);if(!load_gnss_frame(b)||b[37]!=7)return 3;return 0;}\n'
        cp=d/'read.c';cp.write_text(c);exe=d/'read';p=subprocess.run(['cc',str(cp),'-o',str(exe)],capture_output=True,text=True);records.append(dict(argv=p.args,stdout=p.stdout,stderr=p.stderr,exit=p.returncode));assert p.returncode==0
        p=subprocess.run([str(exe)],capture_output=True,text=True);records.append(dict(argv=p.args,stdout=p.stdout,stderr=p.stderr,exit=p.returncode));assert p.returncode==0
        cfg=d/'cfg.json';cfg.write_text(json.dumps(conf));p=subprocess.Popen([sys.executable,'-B',str(root/'src/hvf_bench/guest/navigation-motion.py'),str(cfg),str(frame)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        samples=[];deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            status=d/'simulation-state.json'
            if status.exists():
                v=json.loads(status.read_text())
                if not samples or v['sequence']!=samples[-1]['sequence']:samples.append(v)
                if len(samples)>=5:break
            time.sleep(.04)
        live=frame.read_bytes(); heading=struct.unpack_from('<i',live,49)[0]; assert 0 <= heading < 360000; assert struct.unpack_from('<I',live,53)[0] == round(route.speed*100)
        p.send_signal(signal.SIGTERM);out,err=p.communicate(timeout=3);records.append(dict(argv=p.args,stdout=out,stderr=err,exit=p.returncode));assert p.returncode==0 and len(samples)>=5 and samples[-1]['sequence']-samples[0]['sequence']>=4
        assert route.distance((samples[0]['longitude'],samples[0]['latitude']),(samples[-1]['longitude'],samples[-1]['latitude']))>4
        final=frame.read_bytes();assert struct.unpack_from('<I',final,53)[0]==0 and struct.unpack_from('<Q',final,0)[0]==0
    report=dict(scope='Real host GNSS-file reload and 5Hz publisher execution; not native engine acceptance',road_meters=route.length,road_names=conf['source']['roads'],samples=samples,commands=records,exit=0)
except Exception as e:report=dict(error=repr(e),commands=records,exit=2)
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(report['exit'])
