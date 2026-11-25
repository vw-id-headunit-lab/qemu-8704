import threading,time
from hvf_qnx import owner
from native_domain_state import require_running
from gl_center_live_darwin import GLCenterReader

_lock=threading.RLock()
_reader=None
_epoch=None

def close():
 global _reader,_epoch
 with _lock:
  if _reader is not None:_reader.close()
  _reader=None;_epoch=None

def sample():
 global _reader,_epoch
 with _lock:
  s,pid=owner();require_running(s,pid,3);epoch=(str(s),pid)
  if _reader is not None and _epoch!=epoch:close()
  try:
   if _reader is None:_reader=GLCenterReader(s);_epoch=epoch
   png,proof=_reader.sample()
   if proof['qemu_pid']!=pid or proof['guest_pid']!=_reader.provenance['guest_pid'] or proof['frame_source']!='original_hmi_authenticated_ANGLE' or (proof['width'],proof['height'])!=(1920,1080):raise ValueError('Original native HMI frame source differs')
   _reader.check_live()
   proof.update(session=str(s),frame_age_seconds=max(0,time.time()-proof['source_frame_mtime_ns']/1e9))
   return png,proof
  except BaseException:close();raise
