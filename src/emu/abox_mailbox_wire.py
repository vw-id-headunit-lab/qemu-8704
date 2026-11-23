
from pathlib import Path
import json
import selectors
import socket
import struct
import threading
import time

NAME = "auto9-dsp-mailbox"
MAGIC = 0x584f4241
FRAME = struct.Struct("<36I")
ENDPOINTS = ("ap", "dsp")


class MailboxWire:
    def __init__(self, output, banks=(1,2)):
        self.output = Path(output)
        self.banks=tuple(banks)
        if self.banks not in ((1,2),(0,1,2)):
            raise ValueError('Unknown native ABOX mailbox bank set')
        self.closed = threading.Event()
        self.servers = {}
        self.error = None

    def __enter__(self):
        self.output.mkdir()
        try:
            for name in ENDPOINTS:
                server = socket.create_server(("127.0.0.1", 0), backlog=1)
                self.servers[name] = server
                server.settimeout(.2)
            self.thread = threading.Thread(target=self._serve, name="ABOX data link")
            self.thread.start()
        except BaseException:
            for server in self.servers.values():
                server.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed.set()
        self.thread.join(3)
        for server in self.servers.values():
            server.close()
        if self.thread.is_alive():
            raise RuntimeError("ABOX data link did not stop")
        if exc_type is None and self.error is not None:
            raise RuntimeError(f"ABOX data link: {self.error}")

    def arguments(self, name):
        port = self.servers[name].getsockname()[1]
        return ["-chardev", f"socket,id={NAME},host=127.0.0.1,port={port},nodelay=on"]

    def _serve(self):
        from contextlib import ExitStack
        conns = {}
        pending = {name: bytearray() for name in ENDPOINTS}
        with ExitStack() as stack:
            log = stack.enter_context((self.output/"traffic.jsonl").open("w", encoding="utf-8", buffering=1))
            mux = stack.enter_context(selectors.DefaultSelector())
            try:
                for name in ENDPOINTS:
                    while not self.closed.is_set():
                        try:
                            sock, _ = self.servers[name].accept()
                            break
                        except TimeoutError:
                            continue
                    else:
                        return
                    sock = stack.enter_context(sock)
                    sock.settimeout(.5)
                    sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    mux.register(sock,selectors.EVENT_READ,name)
                    conns[name] = sock
                while not self.closed.is_set():
                    for key,_ in mux.select(.1):
                        name = key.data
                        b = key.fileobj.recv(FRAME.size)
                        if not b:
                            return
                        buf = pending[name]
                        buf.extend(b)
                        if len(buf) < FRAME.size:
                            continue
                        packet = bytes(buf[:FRAME.size])
                        del buf[:FRAME.size]
                        magic, seq, bits, code, *words = FRAME.unpack(packet)
                        bank, op = code & 255, code >> 8
                        if (magic != MAGIC or not seq or bank not in self.banks
                                or op not in (1,2,3) or not bits or bits & (bits-1)):
                            raise ValueError(f"Invalid packet from {name}")
                                                                              
                                                                              
                        other = "dsp" if name == "ap" else "ap"
                        conns[other].sendall(packet)
                        log.write(json.dumps({"from":name, "to":other, "seq":seq, "bank":bank,
                                              "kind":("send","ack","cancel")[op-1],"bits":bits,
                                              "words":words if op==1 else [],
                                              "host_monotonic_ns":time.monotonic_ns()})+"\n")
            except ConnectionResetError:
                                                                           
                return
            except (OSError, ValueError) as exc:
                if not self.closed.is_set():
                    self.error = str(exc)

