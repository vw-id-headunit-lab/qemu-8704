import struct
import secrets





def encode(x,y,phase,tick=0,*,panel=(1560,700)):

 if panel not in ((1560,700),(1920,1024)):raise ValueError('Unsupported explicit KIPC geometry')
 if type(x)is not int or type(y)is not int or not 0<=x<panel[0] or not 0<=y<panel[1]:raise ValueError('Coordinate outside selected KIPC geometry')

 if phase not in ('down','move','up','cancel') or type(tick)is not int:raise ValueError('Invalid original touch phase')

 raw_x=x//2;pressure={'down':253,'move':254,'up':255,'cancel':251}[phase];fingers=1 if phase in ('down','move')else 0

 return bytes.fromhex('8350200b0000000000a0')+bytes([tick&255,16|fingers,16|(raw_x>>6),((raw_x&63)<<2)|(y>>8),y&255,pressure])





def packet(x,y,phase,tick=0,*,panel=(1560,700)):

 return struct.pack('<HHHQ',57,43,16,0)+encode(x,y,phase,tick,panel=panel)





def compact_format(raw):
 out=''
 for b in reversed(raw):
  if 32<=b<=126 and b not in (39,92,37):part=chr(b)
  elif b==37:part='%%'
  else:part='\\'+(('%03o' if out[:1] in '01234567' and out else '%o')%b)
  out=part+out
 return out


def command(x,y,phase,tick=0):
 raw=packet(x,y,phase,tick);name='/tmp/'+secrets.token_hex(3)
 return "test -f /run/oem-bench-input&&(p="+name+";set -C;printf '"+compact_format(raw)+"'>$p&&{ dd if=$p of=/dev/kipc bs=30;q=$?;rm $p;exit $q;})"


def gesture_commands(events,nonce=None,*,panel=(1560,700)):
 nonce=nonce or secrets.token_hex(5)
 if not nonce or len(nonce)>12 or any(c not in '0123456789abcdef' for c in nonce):raise ValueError('Invalid input transaction nonce')
 if not 2<=len(events)<=64 or events[0][2]!='down' or events[-1][2]not in ('up','cancel'):raise ValueError('Incomplete input gesture')
 path='/tmp/t'+nonce;commands=[]
 for i,(x,y,phase,tick) in enumerate(events):
  fmt=compact_format(packet(x,y,phase,tick,panel=panel));commands.append("test -f /run/oem-bench-input&&(umask 077;"+('set -C;' if i==0 else '')+"printf '"+fmt+"'"+('>' if i==0 else '>>')+path+')')
 commands.append('test -f /run/oem-bench-input&&(dd if='+path+' of=/dev/kipc bs=30;q=$?;rm '+path+';exit $q)')
 return path,commands


def fast_setup_commands():
 header=compact_format(packet(0,0,'down',0)[:24])
 return ["OEM_TP='"+header+"'",'t8704(){ test -f /run/oem-bench-input&&(umask 077;p=/tmp/t$1;set -C;printf "$OEM_TP$2$OEM_TP$3">$p&&{ dd if=$p of=/dev/kipc bs=30;q=$?;rm $p;exit $q;});}']


def fast_tap_command(x,y,delta=10,*,panel=(1560,700)):
 if type(delta)is not int or not 0<=delta<=255:raise ValueError('Invalid original delta time')
 tails=[compact_format(packet(x,y,phase,tick,panel=panel)[24:])for phase,tick in [('down',0),('up',delta)]]
 return "declare -F t8704 >/dev/null&&t8704 "+secrets.token_hex(5)+" '"+tails[0]+"' '"+tails[1]+"'"


def fast_gesture_setup_command():
 return 'g8704(){ (p=/tmp/t$1;shift;test -f /run/oem-bench-input&&test -f "$p"&&test ! -L "$p"&&(set -e;for v;do printf "$OEM_TP$v";done>>"$p"));}'


def fast_gesture_commands(events,nonce=None,*,panel=(1560,700)):
 nonce=nonce or secrets.token_hex(5)
 if not nonce or len(nonce)>12 or any(c not in '0123456789abcdef' for c in nonce):raise ValueError('Invalid input transaction nonce')
 if not 2<=len(events)<=64 or events[0][2]!='down' or events[-1][2]not in ('up','cancel'):raise ValueError('Incomplete input gesture')
 path='/tmp/t'+nonce;commands=['test -f /run/oem-bench-input&&(umask 077;set -C;:>'+path+')'];prefix='g8704 '+nonce;chunk=prefix
 for x,y,phase,tick in events:
  tail=" '"+compact_format(packet(x,y,phase,tick,panel=panel)[24:])+"'"
  if len((chunk+tail).encode())>174:commands.append(chunk);chunk=prefix
  chunk+=tail
 if chunk!=prefix:commands.append(chunk)
 commands.append('test -f /run/oem-bench-input&&(dd if='+path+' of=/dev/kipc bs=30;q=$?;rm '+path+';exit $q)')
 return path,commands
