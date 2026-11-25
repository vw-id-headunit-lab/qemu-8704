from __future__ import print_function
import bisect,json,math,os,signal,struct,sys,time

class RoadLoop:
    def __init__(self, spec):
        if spec.get('mode') != 'virtual-road-loop' or spec.get('coordinate_system') != 'WGS84':
            raise ValueError('Expected declared virtual WGS84 road loop')
        points = spec['points']
        if not 4 <= len(points) <= 100000 or points[0] != points[-1]:
            raise ValueError('Expected closed road geometry')
        self.points = []
        for point in points:
            if len(point) != 2 or not all(math.isfinite(float(x)) for x in point):
                raise ValueError('Invalid road coordinate')
            lon, lat = map(float, point)
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError('Road coordinate outside WGS84')
            if not self.points or self.points[-1] != (lon, lat):
                self.points.append((lon, lat))
        self.scale = math.cos(math.radians(sum(p[1] for p in self.points) / len(self.points)))
        self.lengths = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.lengths.append(self.lengths[-1] + self.distance(a, b))
        self.length = self.lengths[-1]
        self.speed = float(spec['speed_mps'])
        self.hz = float(spec['hz'])
        if not 100 <= self.length <= 100000 or not 1 <= self.speed <= 22.22 or not 1 <= self.hz <= 10:
            raise ValueError('Road loop distance, virtual speed or rate outside supported range')
        self.offset = self.nearest(spec['initial']) if 'initial' in spec else 0.0

    def distance(self, a, b):
        return math.hypot((b[0] - a[0]) * self.scale, b[1] - a[1]) * 111195.08

    def position(self, meters):
        meters %= self.length
        i = min(bisect.bisect_right(self.lengths, meters) - 1, len(self.points) - 2)
        ratio = (meters - self.lengths[i]) / (self.lengths[i + 1] - self.lengths[i])
        a, b = self.points[i:i + 2]
        return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio)

    def nearest(self, point):
        best = (float('inf'), 0.0)
        for i, (a, b) in enumerate(zip(self.points, self.points[1:])):
            x, y = (b[0] - a[0]) * self.scale, b[1] - a[1]
            px, py = (point[0] - a[0]) * self.scale, point[1] - a[1]
            t = max(0.0, min(1.0, (px * x + py * y) / (x * x + y * y)))
            q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            best = min(best, (self.distance(point, q), self.lengths[i] + t * (self.lengths[i + 1] - self.lengths[i])))
        return best[1]

    def sample(self, elapsed):
        meters = self.offset + max(0.0, elapsed) * self.speed
        lon, lat = self.position(meters)
        a, b = self.position(meters - 4), self.position(meters + 4)
        heading = math.degrees(math.atan2((b[0] - a[0]) * self.scale, b[1] - a[1])) % 360
        return dict(longitude=lon, latitude=lat, heading_deg=heading, speed_mps=self.speed,
                    distance_m=meters % self.length, lap=int(meters // self.length))

def atomic_write(path, data):
    temporary = path + '.next-' + str(os.getpid())
    with open(temporary, 'wb') as stream:
        stream.write(data)
    os.replace(temporary, path)

def main():
    config, frame = sys.argv[1:3]
    with open(config) as stream:
        spec = json.load(stream)
    route = RoadLoop(spec)
    with open(frame, 'rb') as stream:
        packet = bytearray(stream.read())
    if len(packet) != 847:
        raise ValueError('Expected original 847-byte GNSS frame')
    latitude, longitude = struct.unpack_from('<ii', packet, 37)
    current = (longitude / 10000000.0, latitude / 10000000.0)
    offset = route.nearest(current)
    if route.distance(current, route.position(offset)) <= 1000:
        route.offset = offset
    lock = os.path.join(os.path.dirname(frame), 'motion.lock')
    os.mkdir(lock, 0o700)
    with open(os.path.join(lock, 'pid'), 'w') as owner:
        owner.write(str(os.getpid()))
    running = [True]
    def stop(signum, context):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    started = time.monotonic()
    sequence = 0
    status_path = os.path.join(os.path.dirname(frame), 'simulation-state.json')
    print('VIRTUAL_ROAD_LOOP_STARTED pid=%d hz=%g speed_mps=%g meters=%g' %
          (os.getpid(), route.hz, route.speed, route.length))
    sys.stdout.flush()
    try:
        while running[0]:
            sample = route.sample(time.monotonic() - started)
            struct.pack_into('<iiiiI', packet, 37, int(round(sample['latitude'] * 1e7)),
                             int(round(sample['longitude'] * 1e7)), int(round(float(spec.get('altitude_m', 10)) * 1000)),
                             int(round(sample['heading_deg'] * 1000)), int(round(sample['speed_mps'] * 100)))
            struct.pack_into('<Q', packet, 0, int(time.monotonic() * 1000000000))
            atomic_write(frame, packet)
            sequence += 1
            sample.update(mode='virtual-road-loop', coordinate_system='WGS84', sequence=sequence,
                          pid=os.getpid(), hz=route.hz, running=True, observed_unix=time.time(), source=spec['source'])
            atomic_write(status_path, json.dumps(sample, ensure_ascii=False).encode('utf8'))
            remaining = started + sequence / route.hz - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            elif remaining < -1:
                started = time.monotonic() - sequence / route.hz
    finally:
        struct.pack_into('<I', packet, 53, 0)
        struct.pack_into('<Q', packet, 0, 0)
        atomic_write(frame, packet)
        sample.update(mode='virtual-road-paused', running=False, speed_mps=0, observed_unix=time.time())
        atomic_write(status_path, json.dumps(sample, ensure_ascii=False).encode('utf8'))
        print('VIRTUAL_ROAD_LOOP_STOPPED pid=%d sequence=%d' % (os.getpid(), sequence))
        os.unlink(os.path.join(lock, 'pid'))
        os.rmdir(lock)

if __name__ == '__main__':
    main()
