from pathlib import Path
import importlib.util,json,subprocess,sys,tempfile
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 p=r/'src/hvf_bench/src/hmi_bench/navigation_fixture.py';spec=importlib.util.spec_from_file_location('nav_uplink_fixture',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 config=json.loads((r/'config/hvf-8704.json').read_bytes());assert 'navigation_uplink'in config
 profile=r/'config/navigation-uplink.json';network,settings=m.uplink(profile);assert network['mac']=='52:54:00:87:04:01'
 assert b'NAV_UPLINK_DNS=10.87.5.3'in settings
 source=r/'src/hvf_bench';init=(source/'guest/hmi-init.sh').read_text();assert init.index('$BB sh /navigation-network')<init.index('$BB sh /navigation-setup')
 assert "uplink(config['navigation_uplink'])"in(source/'src/hmi_bench/launcher.py').read_text()
 assert "members['navigation-uplink.conf']"in(source/'src/hmi_bench/archive.py').read_text()
 sys.path.insert(0,str(source/'src'))
 import hmi_bench
 hmi_bench.__path__.append(str(Path(__file__).resolve().parents[1]/'src/hvf_bench/src/hmi_bench'))
 from hmi_bench.config import Config,PATH_KEYS
 from hmi_bench.launcher import make_argv
 paths={k:Path('/fixture')for k in PATH_KEYS};online=Config(profile,dict(paths,navigation_uplink=profile));offline=Config(profile,paths)
 assert not any('navigation-uplink-nic'in a for a in make_argv(online,'hmi',Path('/fixture/initrd')))
 assert 'navigation_slirp.py'in(r/'tools/hvf_network.py').read_text()
 assert 'slirp_pollfds_fill_socket'in(r/'tools/navigation_slirp.py').read_text()
 assert not any('navigation-uplink'in a for a in make_argv(offline,'hmi',Path('/fixture/initrd')))
 assert not any('navigation-uplink'in a for a in make_argv(online,'jvm',Path('/fixture/initrd')))

 relay=subprocess.run([sys.executable,'-B',r/'tools/navigation_slirp.py',profile],input='',capture_output=True,text=True,timeout=3)
 assert relay.returncode==0,(relay.stdout,relay.stderr)
 assert 'NAVIGATION_REAL_NAT_READY 10.87.5.0/24'in relay.stderr
 subprocess.run(['/bin/sh','-n',source/'guest/navigation-network.sh'],check=True,capture_output=True)
 with tempfile.TemporaryDirectory()as d:
  bad=json.loads(profile.read_text());bad['network']='10.87.4.0/24';q=Path(d)/'bad.json';q.write_text(json.dumps(bad))
  try:m.uplink(q)
  except ValueError:pass
  else:raise AssertionError('conflicting uplink accepted')
 print(json.dumps(dict(exit=0,stdout='NAVIGATION_REAL_UPLINK_STAGE_PASS dedicated_NAT=1 default_offline_without_profile=1 original_online_and_activation_state_unmodified=1',profile=network,settings=settings.decode())));raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,stderr=repr(e))));raise SystemExit(2)
