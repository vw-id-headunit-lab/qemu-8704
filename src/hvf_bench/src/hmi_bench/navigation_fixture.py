import json,struct
from pathlib import Path

def gnss_frame(path):
 p=json.loads(Path(path).read_bytes())
 if p['mode']!='virtual' or p['coordinate_system']!='WGS84':raise ValueError('Expected declared virtual WGS84 position')
 lat=float(p['latitude']);lon=float(p['longitude'])
 if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Position outside WGS84 range')
 frame=bytearray(847)
 struct.pack_into('<IiiiiI',frame,33,3,round(lat*1e7),round(lon*1e7),round(float(p['altitude_m'])*1000),round(float(p['heading_deg'])*1000),round(float(p['speed_mps'])*100))
 struct.pack_into('<H',frame,89,1)
 frame[32]=1
 return bytes(frame)


def validate_simulation(path):
 import importlib.util
 source=Path(__file__).resolve().parents[2]/'guest/navigation-motion.py'
 spec=importlib.util.spec_from_file_location('navigation_motion_validation',source)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 return module.RoadLoop(json.loads(Path(path).read_bytes()))


def uplink(path):
 import ipaddress,re
 p=json.loads(Path(path).read_bytes())
 expected={'profile','network','address','gateway','dns','mac'}
 if set(p)!=expected or p['profile']!='navigation-real-uplink-v1':raise ValueError('Invalid navigation uplink profile')
 net=ipaddress.IPv4Network(p['network']);values=[ipaddress.IPv4Address(p[k])for k in ['address','gateway','dns']]
 if not net.is_private or net.prefixlen!=24 or any(a not in net or a in [net.network_address,net.broadcast_address]for a in values)or len(set(values))!=3:raise ValueError('Invalid private navigation subnet')
 if net.overlaps(ipaddress.IPv4Network('10.87.4.0/24'))or net.overlaps(ipaddress.IPv4Network('10.0.2.0/24'))or net.overlaps(ipaddress.IPv4Network('192.168.0.0/24')):raise ValueError('Navigation uplink conflicts with existing private links')
 if not re.fullmatch(r'52:54:00:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}',p['mac']):raise ValueError('Invalid virtual navigation MAC')
 settings=''.join(f"NAV_UPLINK_{k.upper()}={p[k]}\n"for k in ['network','address','gateway','dns','mac'])
 return p,settings.encode('ascii')
