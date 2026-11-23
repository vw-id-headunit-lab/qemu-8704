import argparse
from pathlib import Path
import struct


def sanitize(raw):
    if raw[:6]!=b'\x7fELF\x02\x01':
        raise ValueError('Expected little-endian ELF64')
    data=bytearray(raw)
    phoff,shoff=struct.unpack_from('<QQ',raw,32)
    phsize,phnum,shsize,shnum,shstr=struct.unpack_from('<HHHHH',raw,54)
    if shsize!=64 or shoff+shnum*shsize>len(raw) or phoff+phnum*phsize>len(raw):
        raise ValueError('Invalid ELF table bounds')
    names_header=shoff+shstr*shsize
    names_offset,names_size=struct.unpack_from('<QQ',raw,names_header+24)
    names=raw[names_offset:names_offset+names_size]
    loaded=[]
    for i in range(phnum):
        at=phoff+i*phsize
        kind=struct.unpack_from('<I',raw,at)[0]
        offset=struct.unpack_from('<Q',raw,at+8)[0]
        size=struct.unpack_from('<Q',raw,at+32)[0]
        if kind==1:loaded.append((offset,offset+size))
    removed=[]
    for i in range(shnum):
        at=shoff+i*shsize
        name,kind,flags=struct.unpack_from('<IIQ',raw,at)
        label=names[name:].split(b'\0',1)[0]
        offset,size=struct.unpack_from('<QQ',raw,at+24)
        if not label.startswith((b'.debug',b'.zdebug')):continue
        if flags&2 or offset+size>len(raw) or any(offset<end and start<offset+size for start,end in loaded):
            raise ValueError('Debug section overlaps loaded data')
        data[offset:offset+size]=b'\0'*size
        data[at:at+shsize]=b'\0'*shsize
        removed.append(label.decode('ascii'))
    for start,end in loaded:
        if data[start:end]!=raw[start:end]:raise ValueError('ELF loaded bytes changed')
    return bytes(data),{'removed_sections':removed,'loaded_bytes_unchanged':True,'layout_bytes':len(data)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.input.resolve()==args.output.resolve():raise ValueError('Require a fresh output copy')
    data,result=sanitize(args.input.read_bytes())
    args.output.write_bytes(data)
    print(result)


if __name__=='__main__':
    main()
