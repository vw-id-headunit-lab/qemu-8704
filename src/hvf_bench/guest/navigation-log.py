from __future__ import print_function
import os,sys

def collect(path,stream,limit=2097152):
    if limit < 4096:
        raise ValueError('Log limit too small')
    if os.path.exists(path) and os.path.getsize(path) > limit:
        os.replace(path,path+'.1')
    output=open(path,'ab',0)
    size=output.tell()
    try:
        while True:
            data=stream.read(16384)
            if not data:
                break
            while data:
                if size == limit:
                    output.close()
                    os.replace(path,path+'.1')
                    output=open(path,'wb',0)
                    size=0
                piece=data[:limit-size]
                output.write(piece)
                size+=len(piece)
                data=data[len(piece):]
    finally:
        output.close()

if __name__ == '__main__':
    try:
        collect(sys.argv[1],getattr(sys.stdin,'buffer',sys.stdin))
    finally:
        if len(sys.argv)==3:
            os.unlink(sys.argv[2])
