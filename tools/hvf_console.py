from pathlib import Path
import sys,json,socket,secrets,time
ROOT=Path(__file__).resolve().parents[1]
def execute(command,timeout=15,*,port=None):
 if port is None:
  current=json.loads((ROOT/'var/hvf/current.json').read_bytes());s=Path(current['session']);j=json.loads((s/'session.json').read_bytes());port=j['ports']['debug_port']
 marker='MEB_DONE_'+secrets.token_hex(8)
 with socket.create_connection(('127.0.0.1',port),timeout=3)as sock:
  sock.settimeout(.5);sock.sendall((command+'\necho '+marker+'\n').encode());raw=bytearray();deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   try:b=sock.recv(65536)
   except socket.timeout:continue
   if not b:break
   raw.extend(b)
   if marker.encode()in raw:break
  else:raise TimeoutError('Guest console command incomplete; session retained')
 return raw.decode('utf8',errors='replace')
if __name__=='__main__':print(execute(sys.argv[1],float(sys.argv[2])if len(sys.argv)>2 else 15),end='')
