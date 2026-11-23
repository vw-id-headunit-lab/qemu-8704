
from pathlib import Path
import argparse,hashlib,json,struct,sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/emu'))
from vlm_offline_live import original,factory_high_bank,LINUX_FACTORY_SECOND_HIGH_BANK
from vlm_platform import parse_dtb,rebuild_fdt

def reserve(raw):
 hcb=original('hcb/hcb.img');source=hcb.read_bytes()
 if hashlib.sha256(source).hexdigest()!='cf218e39cb2afc9fd06b009198f53ca41112a12b52a89e1b2a868cf8716a6606':raise ValueError('HCB changed')
 base,size=factory_high_bank(source,3,LINUX_FACTORY_SECOND_HIGH_BANK);start=base+size-0x400000
 nodes=parse_dtb(raw);parent=nodes['/reserved-memory']
 if parent['#address-cells']!=struct.pack('>I',2)or parent['#size-cells']!=struct.pack('>I',1)or parent['ranges']:raise ValueError('Unsupported reserved-memory address schema')
 name=f'/reserved-memory/oem-gl-forward@{start:x}'
 if name in nodes:raise ValueError('Already reserved')
 for path,props in nodes.items():
  if path.startswith('/reserved-memory/')and'reg'in props:
   if len(props['reg'])%12:raise ValueError('Unexpected reservation encoding')
   for address,length in struct.iter_unpack('>QI',props['reg']):
    if length and start<address+length and address<start+0x400000:raise ValueError('Existing reservation overlaps transport')
 pos=struct.unpack_from('>I',raw,16)[0]
 while True:
  address,length=struct.unpack_from('>QQ',raw,pos);pos+=16
  if not(address or length):break
  if length and start<address+length and address<start+0x400000:raise ValueError('FDT memory reservation overlaps transport')
 nodes[name]={'compatible':b'oem8704,private-gl-forward-v1\0','reg':struct.pack('>QI',start,0x400000),'no-map':b''}
 out=rebuild_fdt(raw,nodes)
 if parse_dtb(out)!=nodes:raise ValueError('FDT roundtrip differs')
 return out,dict(control=hex(start),bulk=hex(start+0x100000),bulk_bytes=0x100000,reserved_bytes=0x400000,owner='factory VM3 only',HCB_sha256=hashlib.sha256(source).hexdigest(),node=name)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('input',type=Path);p.add_argument('output',type=Path);a=p.parse_args();output=a.output.resolve()
 if not output.is_relative_to(ROOT)or output==a.input.resolve():raise ValueError('Require a separate project output')
 raw=a.input.read_bytes();data,meta=reserve(raw);output.write_bytes(data)
 if output.read_bytes()!=data or a.input.read_bytes()!=raw:raise ValueError('Write verification failed')
 meta.update(input=str(a.input.resolve()),output=str(output),parent_sha256=hashlib.sha256(raw).hexdigest(),sha256=hashlib.sha256(data).hexdigest());print(json.dumps(meta))

if __name__=='__main__':main()
