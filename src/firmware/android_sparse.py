




import bisect
import hashlib
import io
import struct

class SparseView(io.RawIOBase):
    def __init__(self, fh):
        self.fh, self.pos, self.runs = fh, 0, []
        h = fh.read(28)
        magic, major, minor, hs, cs, bs, blocks, chunks, crc = struct.unpack('<I4H4I', h)
        if magic != 0xed26ff3a or major != 1 or hs < 28 or cs < 12:
            raise ValueError('Unsupported sparse header')
        self.size = bs*blocks
        if self.size > 40*1024**3 or chunks > 1000000:
            raise ValueError('Sparse bounds exceeded')
        fh.seek(hs)
        offset = 0
        for _ in range(chunks):
            t, _, count, total = struct.unpack('<2H2I', fh.read(12))
            if total < cs:
                raise ValueError('Bad chunk length')
            fh.seek(cs-12, 1)
            payload, size = fh.tell(), count*bs
            if t == 0xcac1 and total-cs != size:
                raise ValueError('Bad raw chunk')
            if t not in (0xcac1, 0xcac2, 0xcac3, 0xcac4):
                raise ValueError('Unknown sparse chunk')
            fill = fh.read(4) if t == 0xcac2 else None
            if size:
                self.runs.append((offset, offset+size, t, payload, fill))
            offset += size
            fh.seek(payload+total-cs)
        if offset != self.size:
            raise ValueError('Sparse expanded size mismatch')
        self.starts = [r[0] for r in self.runs]
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos+offset if whence == 1 else self.size+offset
        if self.pos < 0: raise ValueError('Negative offset')
        return self.pos
    def read(self, size=-1):
        size = self.size-self.pos if size < 0 else min(size, self.size-self.pos)
        if size > 64*1024**2: raise ValueError('Use bounded reads')
        output = bytearray()
        while size > 0:
            start, end, t, offset, fill = self.runs[bisect.bisect_right(self.starts,self.pos)-1]
            n = min(size,end-self.pos)
            if t == 0xcac1:
                self.fh.seek(offset+self.pos-start)
                data = self.fh.read(n)
                if len(data) != n: raise ValueError('Truncated sparse file')
            elif t == 0xcac2:
                shift = (self.pos-start)%4
                data = (fill*((n+shift+3)//4))[shift:shift+n]
            elif t == 0xcac3:
                data = bytes(n)
            else:
                raise ValueError('CRC chunk unexpectedly has output')
            output.extend(data)
            self.pos += n
            size -= n
        return bytes(output)

def partitions(view):
    view.seek(4096)
    geometry = view.read(52)
    magic, length = struct.unpack_from('<II', geometry)
    if magic != 0x616c4467 or length != 52:
        raise ValueError('Unsupported liblp geometry')
    if hashlib.sha256(geometry[:8]+bytes(32)+geometry[40:]).digest()!=geometry[8:40]:
        raise ValueError('Geometry checksum mismatch')
    view.seek(12288)
    head = view.read(128)
    magic, major, minor, length = struct.unpack_from('<IHHI',head)
    if magic != 0x414c5030 or major != 10 or length not in (128,256):
        raise ValueError('Unsupported liblp metadata')
    if length > 128: head += view.read(length-128)
    if hashlib.sha256(head[:12]+bytes(32)+head[44:]).digest()!=head[12:44]:
        raise ValueError('Metadata header checksum mismatch')
    table_size=struct.unpack_from('<I',head,44)[0]
    if table_size>4*1024**2: raise ValueError('Tables too large')
    tables=view.read(table_size)
    if hashlib.sha256(tables).digest()!=head[48:80]:
        raise ValueError('Metadata tables checksum mismatch')
    def table(desc,fmt):
        offset,count,entry=struct.unpack_from('<III',head,desc)
        if entry != struct.calcsize(fmt) or offset+count*entry>len(tables):
            raise ValueError('Table bounds mismatch')
        return [struct.unpack_from(fmt,tables,offset+i*entry) for i in range(count)]
    extents=table(92,'<QIQI')
    result=[]
    for name,attr,first,count,group in table(80,'<36sIIII'):
        name=name.rstrip(b'\0').decode('ascii')
        result.append(dict(name=name,attributes=attr,extents=extents[first:first+count],
                           size=sum(e[0]*512 for e in extents[first:first+count])))
    return result

class PartitionView(SparseView):
    def __init__(self,source,part):
        self.fh,self.pos,self.runs=source,0,[]
        offset=0
        for sectors,t,physical,device in part['extents']:
            size=sectors*512
            if t not in (0,1) or device != 0 or physical*512+size>source.size:
                raise ValueError('Unsupported partition extent')
            self.runs.append((offset,offset+size,0xcac1 if t==0 else 0xcac3,physical*512,None))
            offset+=size
        self.size=offset
        self.starts=[r[0] for r in self.runs]
