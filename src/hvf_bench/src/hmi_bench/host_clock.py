"""One-way Windows/host wall-clock feed for the original guest Time service."""
from datetime import datetime, timezone
import json
from pathlib import Path
import socketserver
import threading

from .fixtures import element


def time_objects():
    # Empty optional time/date fields remain unavailable until the first host
    # sample is applied; never show a made-up startup clock as real time.
    return {
        'Time/times.json': [element('Time', 'times', kind, type=kind)
                            for kind in ('CustomerTime', 'UTC')],
        'Time/timeSettings.json': [element('Time', 'timeSettings', 'Host clock',
            dstActive=False, timeMode='automatic', timeZoneOffset=0)],
    }


def clock_packet(now=None):
    now = now or datetime.now().astimezone()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Host clock sample must include a UTC offset')
    offset = int(now.utcoffset().total_seconds() / 60)
    if not -720 <= offset <= 840:
        raise ValueError('Host offset exceeds the Time service range')
    utc = now.astimezone(timezone.utc)
    return dict(source='host_system_clock', localDate=now.strftime('%Y-%m-%d'),
        localTime=now.strftime('%H:%M:%S'), utcDate=utc.strftime('%Y-%m-%d'),
        utcTime=utc.strftime('%H:%M:%SZ'), timeZoneOffset=offset,
        dstActive=bool(now.dst() and now.dst().total_seconds()),
        hostUnixMillis=int(now.timestamp() * 1000))


class HostClockFeed:
    """Push trusted clock-only JSON through a dedicated virtio-serial channel.

    No destination, command or arbitrary file content is accepted from the VM.
    """
    def __init__(self, output, *, interval=1.0, provider=clock_packet):
        self.output = Path(output)
        self.interval = interval
        self.provider = provider
        self.stop = threading.Event()
        self.latest = None
        self.sent = 0
        self.error = None
        self.lock = threading.Lock()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(2)
                while not owner.stop.is_set():
                    try:
                        sample = owner.provider()
                        self.request.sendall((json.dumps(sample, separators=(',', ':')) + '\n').encode('ascii'))
                        with owner.lock:
                            owner.latest = sample
                            owner.sent += 1
                    except (OSError, ValueError) as error:
                        owner.error = str(error)
                        return
                    owner.stop.wait(owner.interval)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name='HMI-Host-Clock', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / 'host-clock.json').write_text(json.dumps(dict(
            state='ended', source='host_system_clock', sent=self.sent,
            latest=self.latest, error=self.error), indent=2), encoding='utf-8')
