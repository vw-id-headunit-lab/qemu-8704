"""Expose an Android sparse image to a local QEMU as a read-only NBD disk."""
from bisect import bisect_right
from pathlib import Path
import errno
import socket
import struct
import threading

OPTION_MAGIC=0x49484156454F5054
REPLY_MAGIC=0x0003E889045565A9


class SparseImage:
    def __init__(self,path):
        self.path=Path(path)
        self.file=self.path.open('rb')
        self.lock=threading.Lock()
        try:
            header=self.file.read(28)
            if len(header)!=28:raise ValueError('Truncated Android sparse header')
            magic,major,minor,fh,ch,bs,blocks,chunks,checksum=struct.unpack('<I4H4I',header)
            if magic!=0xed26ff3a:
                self.file.seek(1080)
                if self.file.read(2)!=b'\x53\xef':raise ValueError('Require original raw ext4 or Android sparse input')
                self.size=self.path.stat().st_size
                self.chunks=[(0,self.size,0xcac1,0,None)]
                self.starts=[0]
                return
            if magic!=0xed26ff3a or major!=1 or fh<28 or ch<12 or not bs or bs%4:
                raise ValueError('Unexpected Android sparse header')
            file_size=self.path.stat().st_size
            if fh>file_size or chunks>(file_size-fh)//ch:raise ValueError('Truncated sparse chunk list')
            self.size=blocks*bs
            self.chunks=[]
            logical=0
            self.file.seek(fh)
            for _ in range(chunks):
                data=self.file.read(ch)
                if len(data)!=ch:raise ValueError('Truncated sparse chunk header')
                kind,_,count,total=struct.unpack_from('<2H2I',data)
                offset=self.file.tell()
                expected={0xcac1:count*bs,0xcac2:4,0xcac3:0,0xcac4:4}.get(kind)
                if expected is None or total!=ch+expected or offset+expected>file_size:
                    raise ValueError('Invalid sparse chunk size/type')
                if kind==0xcac4:
                    if count:raise ValueError('CRC chunk must not contribute blocks')
                elif count:
                    pattern=self.file.read(4) if kind==0xcac2 else None
                    self.chunks.append((logical,logical+count*bs,kind,offset,pattern))
                logical+=count*bs
                if logical>self.size:raise ValueError('Sparse chunks exceed virtual size')
                self.file.seek(offset+expected)
            if logical!=self.size:raise ValueError('Sparse image size differs')
            self.starts=[row[0] for row in self.chunks]
        except BaseException:
            self.file.close()
            raise

    def read(self,offset,length):
        if offset<0 or length<0 or offset+length>self.size:raise ValueError('Read outside export')
        result=bytearray(length)
        done=0
        with self.lock:
            while done<length:
                position=offset+done
                start,end,kind,physical,pattern=self.chunks[bisect_right(self.starts,position)-1]
                count=min(length-done,end-position)
                if kind==0xcac1:
                    self.file.seek(physical+position-start)
                    data=self.file.read(count)
                    if len(data)!=count:raise ValueError('Truncated RAW data')
                    result[done:done+count]=data
                elif kind==0xcac2:
                    shift=(position-start)%4
                    result[done:done+count]=(pattern*((count+shift+3)//4))[shift:shift+count]
                elif kind!=0xcac3:
                    raise ValueError('Unexpected data chunk type')
                done+=count
        return bytes(result)

    def close(self):
        self.file.close()


class SparseNBDServer:
    def __init__(self,path):
        self.image=SparseImage(path)
        self.listener=socket.socket()
        self.listener.bind(('127.0.0.1',0))
        self.listener.listen(4)
        self.port=self.listener.getsockname()[1]
        self.closed=False
        self.clients=[]
        self.stats={'source':str(self.image.path),'export_bytes':self.image.size,'source_open_mode':'rb',
                    'read_requests':0,'read_bytes':0,'write_requests_denied':0,'options':[],'errors':[]}
        threading.Thread(target=self.accept,daemon=True).start()

    @staticmethod
    def receive(connection,count):
        out=bytearray()
        while len(out)<count:
            data=connection.recv(count-len(out))
            if not data:raise EOFError()
            out.extend(data)
        return bytes(out)

    @staticmethod
    def option_reply(connection,option,kind,payload=b''):
        connection.sendall(struct.pack('>QIII',REPLY_MAGIC,option,kind,len(payload))+payload)

    def accept(self):
        while not self.closed:
            try:connection,_=self.listener.accept()
            except OSError:return
            self.clients.append(connection)
            threading.Thread(target=self.handle,args=(connection,),daemon=True).start()

    def handle(self,connection):
        try:
            # Fixed-newstyle negotiation; export is strictly read-only.
            connection.sendall(b'NBDMAGIC'+struct.pack('>QH',OPTION_MAGIC,3))
            client_flags=struct.unpack('>I',self.receive(connection,4))[0]
            export_flags=1|2|4
            while True:
                magic,option,length=struct.unpack('>QII',self.receive(connection,16))
                if magic!=OPTION_MAGIC or length>1024*1024:raise ValueError('Invalid NBD option')
                payload=self.receive(connection,length)
                self.stats['options'].append(option)
                if option==1:
                    connection.sendall(struct.pack('>QH',self.image.size,export_flags)+(b'' if client_flags&2 else b'\0'*124))
                    break
                if option==2:
                    self.option_reply(connection,option,1)
                    return
                if option in (6,7):
                    self.option_reply(connection,option,3,struct.pack('>HQH',0,self.image.size,export_flags))
                    self.option_reply(connection,option,3,struct.pack('>HIII',3,1,4096,32*1024*1024))
                    self.option_reply(connection,option,1)
                    if option==7:break
                elif option in (3,9,10):
                    self.option_reply(connection,option,1)
                else:
                    self.option_reply(connection,option,0x80000001)
            while True:
                magic,flags,command,handle,offset,length=struct.unpack('>IHHQQI',self.receive(connection,28))
                if magic!=0x25609513:raise ValueError('Invalid NBD request')
                if command==2:return
                if command==0:
                    if length>32*1024*1024:raise ValueError('Oversized NBD read')
                    try:
                        data=self.image.read(offset,length)
                        connection.sendall(struct.pack('>IIQ',0x67446698,0,handle)+data)
                        self.stats['read_requests']+=1
                        self.stats['read_bytes']+=length
                    except ValueError:
                        connection.sendall(struct.pack('>IIQ',0x67446698,errno.EINVAL,handle))
                elif command==1:
                    remaining=length
                    while remaining:
                        count=min(65536,remaining);self.receive(connection,count);remaining-=count
                    self.stats['write_requests_denied']+=1
                    connection.sendall(struct.pack('>IIQ',0x67446698,errno.EPERM,handle))
                elif command in (3,5):
                    connection.sendall(struct.pack('>IIQ',0x67446698,0,handle))
                else:
                    connection.sendall(struct.pack('>IIQ',0x67446698,errno.EPERM,handle))
        except (EOFError,ConnectionError,OSError):
            pass
        except Exception as error:
            self.stats['errors'].append(repr(error))
        finally:
            connection.close()

    def close(self):
        self.closed=True
        self.listener.close()
        for connection in self.clients:
            try:connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            connection.close()
        self.image.close()
