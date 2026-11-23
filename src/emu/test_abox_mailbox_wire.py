
import json
from pathlib import Path
import socket
import tempfile
import unittest

from abox_mailbox_wire import FRAME, MAGIC, MailboxWire


def connected(wire, side):
    options = wire.arguments(side)[1]
    port = int(next(x[5:] for x in options.split(",") if x.startswith("port=")))
    return socket.create_connection(("127.0.0.1",port),timeout=2)


def receive(sock, length):
    result=bytearray()
    while len(result)<length:
        chunk=sock.recv(length-len(result))
        if not chunk:
            raise EOFError("Mailbox stream ended before the complete frame")
        result.extend(chunk)
    return bytes(result)


class MailboxWireTest(unittest.TestCase):
    def test_bank_zero_forwards_original_bytes_and_peer_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'wire'
            tx=FRAME.pack(MAGIC,7,0x20000,0x100,*range(32))
            ack=FRAME.pack(MAGIC,7,0x20000,0x200,*([0]*32))
            with MailboxWire(out,banks=(0,1,2)) as wire, connected(wire,'ap') as ap, connected(wire,'dsp') as dsp:
                ap.sendall(tx)
                self.assertEqual(receive(dsp,len(tx)),tx)
                dsp.sendall(ack)
                self.assertEqual(receive(ap,len(ack)),ack)
            rows=[json.loads(line) for line in (out/'traffic.jsonl').read_text().splitlines()]
            self.assertEqual([(r['bank'],r['from'],r['kind']) for r in rows],[(0,'ap','send'),(0,'dsp','ack')])

    def test_bank_zero_is_closed_with_legacy_carriers(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'wire'
            with self.assertRaisesRegex(RuntimeError,'Invalid packet'):
                with MailboxWire(out) as wire, connected(wire,'ap') as ap, connected(wire,'dsp') as dsp:
                    ap.sendall(FRAME.pack(MAGIC,1,0x20000,0x100,*([0]*32)))
                    self.assertEqual(dsp.recv(1),b'')

    def test_preserves_both_directions_under_stream_fragmentation(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"wire"
            ap_frame=FRAME.pack(MAGIC,5,0x20000,0x101,*range(32))
            dsp_ack=FRAME.pack(MAGIC,5,0x20000,0x201,*([0]*32))
            dsp_frame=FRAME.pack(MAGIC,7,2,0x101,*range(32,64))
            with MailboxWire(out) as wire, connected(wire,"ap") as ap, connected(wire,"dsp") as dsp:
                ap.sendall(ap_frame[:9])
                ap.sendall(ap_frame[9:])
                self.assertEqual(receive(dsp,len(ap_frame)),ap_frame)
                dsp.sendall(dsp_ack+dsp_frame[:19])
                dsp.sendall(dsp_frame[19:])
                self.assertEqual(receive(ap,len(dsp_ack)+len(dsp_frame)),dsp_ack+dsp_frame)
            rows=[json.loads(line) for line in (out/"traffic.jsonl").read_text().splitlines()]
            self.assertEqual([(x["from"],x["bank"],x["kind"],x["seq"]) for x in rows],
                             [("ap",1,"send",5),("dsp",1,"ack",5),("dsp",1,"send",7)])
            self.assertEqual(rows[-1]["words"],list(range(32,64)))

    def test_rejects_invalid_magic_without_forwarding(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"wire"
            with self.assertRaisesRegex(RuntimeError,"Invalid packet"):
                with MailboxWire(out) as wire, connected(wire,"ap") as ap, connected(wire,"dsp") as dsp:
                    ap.sendall(FRAME.pack(0,1,0x20000,0x101,*([0]*32)))
                    self.assertEqual(dsp.recv(1),b"")
            self.assertEqual((out/"traffic.jsonl").read_text(),"")

