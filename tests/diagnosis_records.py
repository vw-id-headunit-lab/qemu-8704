from pathlib import Path
import importlib.util,json,subprocess,sys,tempfile,hashlib
r=Path(__file__).resolve().parents[1];target=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else r;report={'target':str(target)}
try:
 p=target/'src/hvf_bench/src/hmi_bench/car_configuration.py';spec=importlib.util.spec_from_file_location('records_under_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 profile=json.loads((r/'src/hvf_bench/local/vehicle-profile.json').read_bytes());raw,receipt=m.diagnosis_records(profile,r/'var/inputs/support/ric-id3/diagval.bin');assert len(receipt['applied'])==46;assert {x['key']for x in receipt['excluded']}=={1313,1314,1317,2833,1412,999}
 rows={int(x.split()[0]):bytes(map(int,x.split()[1:]))for x in(r/'var/inputs/support/ric-id3/diagval.bin').read_text().splitlines()if x.strip()}
 for line in raw.decode().splitlines():
  address,hexbytes=line.split('=');assert bytes.fromhex(hexbytes)==rows[int(address.split('/')[1])]
 jdk=Path('/opt/homebrew/var/homebrew/tmp/.cellar/openjdk@17/17.0.21.1/bin');cp=':'.join(str(x)for x in[target/'src/hvf_bench/build/bench-platform.jar',r/'src/hvf_bench/local/compile/hmi.jar',r/'src/hvf_bench/local/compile/osgi.jar'])
 code='''import java.io.*;import java.util.*;import java.lang.reflect.*;
public class RecordsCheck {
 public static void main(String[] a)throws Exception {
  Class<?> c=Class.forName("bench.platform.BenchPlatform$Persistence");Method m=c.getDeclaredMethod("readRecords",InputStream.class);m.setAccessible(true);
  Map<?,?> rows=(Map<?,?>)m.invoke(null,new FileInputStream(a[0]));Properties p=new Properties();try(InputStream in=new FileInputStream(a[0])){p.load(in);}
  if(rows.size()!=46||rows.size()!=p.size())throw new Exception("record count");
  for(String key:p.stringPropertyNames()) {byte[] v=(byte[])rows.get("Buffer:"+key.replace('/',':'));StringBuilder b=new StringBuilder();for(byte x:v)b.append(String.format("%02x",x&255));if(!b.toString().equals(p.getProperty(key)))throw new Exception("bytes "+key);}
  if(!new String((byte[])rows.get("Buffer:50331648:61833"),"US-ASCII").equals("8704"))throw new Exception("identity");
  for(String bad:new String[]{"9/1=00","16777216/1=0","16777216/1=zz",""}) {
   try {m.invoke(null,new ByteArrayInputStream(bad.getBytes("US-ASCII")));throw new Exception("accepted invalid input");}catch(InvocationTargetException e){if(!(e.getCause()instanceof IOException))throw e;}
  }
  System.out.println("ACTUAL_JAVA_RECORDS=46 EXACT_SUPPLIED_BYTES=46 MU_SOFTWARE_VERSION=8704 REJECTED_INVALID=4");
 }
}'''
 with tempfile.TemporaryDirectory(prefix='diagnosis-records-')as tmp:
  d=Path(tmp);(d/'RecordsCheck.java').write_text(code);(d/'records.properties').write_bytes(raw);a=subprocess.run([str(jdk/'javac'),'--release','8','-d',str(d),str(d/'RecordsCheck.java')],capture_output=True,text=True);assert a.returncode==0,a.stderr
  a=subprocess.run([str(jdk/'java'),'-cp',str(d)+':'+cp,'RecordsCheck',str(d/'records.properties')],capture_output=True,text=True);report['java']={'stdout':a.stdout,'stderr':a.stderr,'exit':a.returncode};assert a.returncode==0,a.stderr
 report.update(exit=0,source_sha256=receipt['source_sha256'],payload_sha256=hashlib.sha256(raw).hexdigest(),applied=len(receipt['applied']),excluded=receipt['excluded'])
except Exception as e:report.update(exit=2,error=repr(e))
print(json.dumps(report,indent=2));raise SystemExit(report['exit'])
