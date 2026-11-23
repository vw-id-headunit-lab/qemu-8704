"""Translate the QEMU tablet into the bench's existing DSI pointer protocol.

Runs under the firmware's Python 2.7 or Python 3; no host mouse hooks needed.
"""
from __future__ import print_function
import os

WIDTH, HEIGHT = 1560, 700
EVENT_SIZE = 24  # AArch64 Linux struct input_event; QEMU tablet axes are 0..32767.

def little_endian(data):
    return sum(ord(data[index:index+1]) << (index * 8) for index in range(len(data)))

def coordinate(value, limits, size):
    low, high = limits
    return max(0, min(size - 1, (value - low) * (size - 1) // max(1, high - low)))

def main():
    names = [name for name in os.listdir('/sys/class/input') if name.startswith('event')
             and 'QEMU Virtio Tablet' in open('/sys/class/input/' + name + '/device/name').read()]
    if len(names) != 1:
        raise RuntimeError('Expected exactly one QEMU Virtio Tablet')
    event_name = names[0]
    with open('/dev/input/' + event_name, 'rb', 0) as device:
        x_range = y_range = (0, 32767)
        x, y, pressed, active = x_range[0], y_range[0], False, False
        last = None
        os.write(1, ('NATIVE_POINTER_READY device=' + event_name + '\n').encode('ascii'))
        with open('/tmp/bench-native-input', 'wb', 0) as output:
            while True:
                data = device.read(EVENT_SIZE)
                if len(data) != EVENT_SIZE:
                    raise RuntimeError('Tablet event stream closed')
                kind, code = little_endian(data[16:18]), little_endian(data[18:20])
                value = little_endian(data[20:24])
                if value & 0x80000000: value -= 0x100000000
                if kind == 3 and code == 0: x = value
                elif kind == 3 and code == 1: y = value
                elif kind == 1 and code in (272, 330): pressed = value != 0
                elif kind == 0 and code == 0:
                    point = (coordinate(x, x_range, WIDTH), coordinate(y, y_range, HEIGHT))
                    action = ('down' if not active else 'move') if pressed else ('up' if active else None)
                    if action and (action != 'move' or point != last):
                        output.write(('%s %d %d\n' % (action, point[0], point[1])).encode('ascii'))
                        last = point
                    active = pressed

if __name__ == '__main__':
    main()
