import ctypes
import os
from pathlib import Path


def executable(pid):
    lib=ctypes.CDLL('/usr/lib/libproc.dylib')
    lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
    lib.proc_pidpath.restype=ctypes.c_int
    buf=ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(pid,buf,len(buf))<=0:raise ProcessLookupError(pid)
    return Path(os.fsdecode(buf.value)).resolve(strict=True)


def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
