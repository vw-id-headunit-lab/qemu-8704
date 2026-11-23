"""Report actual boot milestones, never an invented percentage or UI success."""
from functools import lru_cache
import os
from pathlib import Path
import re
import threading

MARKERS = (
    ('Booting Linux', 'kernel', 'ARM64 系统启动中'),
    ('HMI_GUEST_BEGIN', 'mounts', '挂载只读固件'),
    ('WESTON_SOCKET_READY=true', 'compositor', '显示服务已启动，准备原 HMI'),
    ('ORIGINAL_MIBMAIN_BEGIN', 'jvm', '原 HMI 初始化中（首次需等待）'),
    ('ViewCompositor initialized', 'graphics', '原图形初始化完成，等待语言和业务服务'),
    ('onNewSkin: 1', 'skin', '原皮肤已加载，等待首屏'),
    ('check And Show WelcomeScreen: INITIAL', 'welcome', '原欢迎页已初始化；等待启动流程汇总'),
    ('===== END OF STARTUP SUMMARY =====', 'startup_complete', '原 HMI 已输出启动汇总；业务功能以实际界面为准'),
)


class _LogProgress:
    """Incrementally retain milestones even after they leave the log tail.

    Read an existing log once in bounded chunks, then only appended bytes. A
    replaced/truncated log is a new session, not the previous boot's success.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.identity = None
        self.offset = 0
        self.mtime = None
        self.carry = b''
        self.rank = -1
        self.unit = None
        self.ended = False

    def read(self, path):
        with self.lock, path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if (identity != self.identity or stat.st_size < self.offset or
                    (stat.st_size == self.offset and stat.st_mtime_ns != self.mtime)):
                self.reset()
            self.identity, self.mtime = identity, stat.st_mtime_ns
            stream.seek(self.offset)
            while chunk := stream.read(512 * 1024):
                data = self.carry + chunk
                text = data.decode('utf-8', errors='replace')
                for rank, (marker, _, _) in enumerate(MARKERS):
                    if marker in text:
                        self.rank = max(self.rank, rank)
                units = re.findall(r'Starting Work Unit (WORKUNIT_\w+)', text)
                if units:
                    self.unit = units[-1].removeprefix('WORKUNIT_')
                self.ended |= 'ORIGINAL_MIBMAIN_EXIT=' in text
                self.carry = data[-4096:]
                self.offset = stream.tell()
            phase, label = 'starting', '虚拟机启动中'
            if self.rank >= 0:
                _, phase, label = MARKERS[self.rank]
            complete = self.rank == len(MARKERS) - 1
            if self.unit and not complete:
                label += ' · 最近启动模块：' + self.unit
            if self.ended:
                phase, label = 'ended', '原 HMI 进程已结束，请检查启动窗口'
            return {'phase': phase, 'label': label, 'latest_work_unit': self.unit,
                    'startup_summary_complete': complete, 'visual_success_inferred': False}


@lru_cache(maxsize=32)
def _reader(path: Path):
    return _LogProgress()


def read_progress(path: Path | None) -> dict:
    if path is None:
        return {'phase': 'display', 'label': '实时显示通道', 'visual_success_inferred': False}
    path = Path(path).resolve()
    try:
        return _reader(path).read(path)
    except OSError:
        return {'phase': 'starting', 'label': '虚拟机启动中，首次加载需要等待', 'visual_success_inferred': False}
