import argparse
import hashlib
from pathlib import Path
import struct


def sanitize(raw, prefixes):
    data=bytearray(raw)
    pe=struct.unpack_from('<I',data,0x3c)[0]
    if data[:2]!=b'MZ' or data[pe:pe+4]!=b'PE\0\0':
        raise ValueError('Expected a Windows PE image')
    count=struct.unpack_from('<H',data,pe+6)[0]
    optional=struct.unpack_from('<H',data,pe+20)[0]
    table=pe+24+optional
    sections=[]
    replacements=0
    for i in range(count):
        at=table+i*40
        size,offset=struct.unpack_from('<II',data,at+16)
        flags=struct.unpack_from('<I',data,at+36)[0]
        if offset+size>len(data):
            raise ValueError('Invalid PE section bounds')
        block=bytes(data[offset:offset+size])
        modified=block
        for prefix in prefixes:
            old=prefix.encode('utf8')
            if len(old)<10:
                raise ValueError('A source prefix must contain at least ten bytes')
            hits=modified.count(old)
            if hits and (flags&0xa0000000 or not flags&0x40000000):
                raise ValueError('Source prefix outside a read-only non-executable section')
            modified=modified.replace(old,b'BUILD_ROOT'.ljust(len(old),b'/'))
            replacements+=hits
        data[offset:offset+size]=modified
        if flags&0x20000000:
            if modified!=block:
                raise ValueError('Executable bytes changed')
            sections.append(hashlib.sha256(block).hexdigest())
    for prefix in prefixes:
        if prefix.encode('utf8') in data:
            raise ValueError('A prefix remains outside sanitized sections')
    if len(data)!=len(raw):
        raise ValueError('PE layout changed')
    struct.pack_into('<I',data,pe+24+64,0)
    return bytes(data),{'replacements':replacements,'executable_section_sha256':sections,'layout_bytes':len(data)}


def sanitize_compiled_prefix(raw, prefix):
    old=prefix.encode('utf8')
    if len(old)!=16:
        raise ValueError('The compiled relocation prefix must have sixteen bytes')
    first=b'\x48\xb8'+old[:8]+b'\x48\x33\x07'
    second=b'\x48\xba'+old[8:]+b'\x48\x33\x57\x08\x48\x09\xd0'
    signature=first+second
    if raw.count(signature)!=1:
        raise ValueError('Expected one exact relocation-prefix instruction sequence')
    offset=raw.index(signature)
    pe=struct.unpack_from('<I',raw,0x3c)[0]
    table=pe+24+struct.unpack_from('<H',raw,pe+20)[0]
    executable=False
    for i in range(struct.unpack_from('<H',raw,pe+6)[0]):
        at=table+i*40
        size,start=struct.unpack_from('<II',raw,at+16)
        flags=struct.unpack_from('<I',raw,at+36)[0]
        if start<=offset and offset+len(signature)<=start+size:
            executable=bool(flags&0x20000000) and not bool(flags&0x80000000)
    if not executable:
        raise ValueError('Relocation instructions are not in a read-only executable section')
    replacement=b'BUILD_ROOT//////'
    data=bytearray(raw)
    data[offset+2:offset+10]=replacement[:8]
    data[offset+len(first)+2:offset+len(first)+10]=replacement[8:]
    return bytes(data),{'instruction_offset':offset,'immediate_bytes':16,'replacement':replacement.decode('ascii')}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prefix',action='append',required=True)
    parser.add_argument('--compiled-prefix')
    args=parser.parse_args()
    if args.input.resolve()==args.output.resolve() or args.output.exists():
        raise ValueError('Require a fresh output copy')
    raw=args.input.read_bytes()
    compiled=None
    if args.compiled_prefix:
        raw,compiled=sanitize_compiled_prefix(raw,args.compiled_prefix)
    data,result=sanitize(raw,args.prefix)
    if compiled:result['compiled_relocation_prefix']=compiled
    args.output.write_bytes(data)
    print(result)


if __name__=='__main__':
    main()
