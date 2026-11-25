from pathlib import Path
import hashlib,json,re,sqlite3,subprocess,sys,tempfile
source=Path(sys.argv[1]);coding=Path(sys.argv[2]);label=sys.argv[3]
out={'label':label,'input':'Original Ric ID.3 record namespace33554432/key1536/slot1, 25 captured bytes; empty ALM nav.db','scope':'Actual startup SQL on isolated database; original native Pers/brand/roads checked separately'}
try:
 data=coding.read_bytes();assert len(data)==25
 with tempfile.TemporaryDirectory(prefix='meb-nav-coding-')as tmp:
  root=Path(tmp);db=root/'nav.db';fixture=root/'coding.bin';fixture.write_bytes(data)
  c=sqlite3.connect(db);c.executescript('CREATE TABLE Persistence(ns INTEGER NOT NULL,key INTEGER NOT NULL,slot INTEGER NOT NULL,data BLOB NOT NULL,PRIMARY KEY(ns,key,slot)); INSERT INTO Persistence VALUES(99,8,1,X\'1234\');');c.close()
  text=source.read_text();block=re.search(r'(seed_car_coding\(\) \{.*?\n\}\nseed_car_coding \|\| exit \$\?)',text,re.S)
  script=''if not block else block[1].replace('$B chroot "$ROOT" /usr/bin/sqlite3','/usr/bin/sqlite3').replace('$B ','').replace('coding=/bench-ric-id3-coding.bin','coding='+str(fixture)).replace('db=/data/lge/navi/persist/nav.db','db='+str(db))
  q=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True);out['startup']={'stdout':q.stdout,'stderr':q.stderr,'exit':q.returncode}
  c=sqlite3.connect(db);row=c.execute('SELECT data FROM Persistence WHERE ns=33554432 AND key=1536 AND slot=1').fetchone();assert c.execute('SELECT hex(data) FROM Persistence WHERE ns=99 AND key=8 AND slot=1').fetchone()[0]=='1234';c.close();out['record_bytes']=len(row[0])if row else 0
  assert q.returncode==0 and row and row[0]==data,'Original navigation car-coding input missing'
  before=hashlib.sha256(db.read_bytes()).hexdigest();q=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True);assert q.returncode==0 and hashlib.sha256(db.read_bytes()).hexdigest()==before
  c=sqlite3.connect(db);c.execute('UPDATE Persistence SET data=? WHERE ns=33554432 AND key=1536 AND slot=1',(bytes(25),));c.commit();c.close();before=hashlib.sha256(db.read_bytes()).hexdigest();q=subprocess.run(['/bin/sh','-c',script],capture_output=True,text=True);out['conflict']={'stdout':q.stdout,'stderr':q.stderr,'exit':q.returncode};assert q.returncode==12 and hashlib.sha256(db.read_bytes()).hexdigest()==before
 out['exit']=0
except Exception as e:out.update(exit=1,error=repr(e))
print(json.dumps(out,indent=2));raise SystemExit(out['exit'])
