




import hashlib
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

CORE0_SHA256 = "cdc63fb8ac191c40cd4d50915bff11b8ed3829570509551adf3d5af1f42d8714"
ENTRY = 0xD0000000
ABOX_MAIN_RETURN = 0xD0010C26
FAULT_VECTORS = (0xD0000B00, 0xD0000B40, 0xD0000BC0, 0xD0000C00)
LINK_ID = "auto9-dsp-lifecycle"
PMU = "/machine/exynos-pmu"


def wrap_original(data):

    if hashlib.sha256(data).hexdigest() != CORE0_SHA256:
        raise ValueError("The same-run UFS DMA core0 readback does not match the original")
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    header = ident + struct.pack("<HHIIIIIHHHHHH", 2, 94, 1, ENTRY, 52, 0, 0x300,
                                 52, 32, 1, 40, 0, 0)
    load = struct.pack("<8I", 1, 4096, ENTRY, ENTRY, len(data), len(data), 7, 4096)
    return (header + load).ljust(4096, b"\0") + data


def snapshot(qmp):
    return {key: qmp.execute("qom-get", {"path": PMU, "property": key})
            for key in ("abox-core0-executing", "abox-core0-link-requests",
                        "abox-core0-execution-reports", "abox-core0-link-rejected",
                        "abox-dsp-core0-status-reads", "abox-timer-high-reads",
                        "abox-timer-low-reads")}


class DspPeer:
    def __init__(self, qemu, dma_readback, output, env, mailbox_arguments=()):
        self.qemu = Path(qemu).resolve(strict=True)
        self.dma_readback = Path(dma_readback)
        self.output = Path(output)
        self.env = env
        self.mailbox_arguments = tuple(mailbox_arguments)
        self.closed = threading.Event()
        self.report = dict(connected=False, original_return_observed=False,
                           hardware_status_reported=False, guest_mailbox_linked=False)

    def __enter__(self):
        self.output.mkdir()
        self.server = socket.create_server(("127.0.0.1", 0), backlog=1)
        self.server.settimeout(.2)
        self.thread = threading.Thread(target=self._run, name="Auto9 core0 link")
        try:
            self.thread.start()
        except BaseException:
            self.server.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown()

    def shutdown(self):
        self.closed.set()
        self.thread.join(20)
        self.server.close()
        if self.thread.is_alive():
            raise RuntimeError("Local DSP peer did not finish bounded shutdown")

    @property
    def arguments(self):
        port = self.server.getsockname()[1]
        return ["-chardev", f"socket,id={LINK_ID},host=127.0.0.1,port={port}"]

    def _start_core(self, log):
        import original_8704_vm as vm
        from vlm_platform import RSP

        data = self.dma_readback.read_bytes()
        image = self.output / "original-core0-load.elf"
        image.write_bytes(wrap_original(data))
        if image.read_bytes()[4096:] != data:
            raise RuntimeError("Original core0 ELF load changed bytes")
        mgmt, debug = vm.free_port(), vm.free_port()
        args = [str(self.qemu), "-machine", "sim", "-cpu", "auto9-boot-test",
                "-m", "128", "-smp", "1", "-display", "none", "-serial", "none",
                "-monitor", "none", "-nic", "none", "-S", "-no-reboot",
                "-semihosting-config", "enable=off", "-kernel", str(image),
                "-device", f"loader,addr={ENTRY:#x},cpu-num=0",
                "-qmp", f"tcp:127.0.0.1:{mgmt},server=on,wait=off",
                "-gdb", f"tcp:127.0.0.1:{debug}", "-d", "in_asm,int,unimp,guest_errors",
                "-D", str(self.output / "native-core0-trace.log")]
        if self.mailbox_arguments:
                                                                         
                                                                            
                                                                            
            args += ["-icount", "shift=0,sleep=on"]
        args += self.mailbox_arguments
        self.report.update(original_sha256=CORE0_SHA256, qemu_sha256=vm.sha(self.qemu),
                           command=args, dma_readback=str(self.dma_readback),
                           guest_mailbox_linked=bool(self.mailbox_arguments))
        self.process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                       env=self.env, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        deadline = time.monotonic() + 6
        while not self.closed.is_set() and time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Xtensa exited before QMP startup")
            try:
                self.qmp = vm.QMP(mgmt)
                break
            except OSError:
                self.closed.wait(.03)
        else:
            raise RuntimeError("Xtensa QMP startup did not complete")
        g = RSP(debug)
        self.debugger = None
        try:
            g.socket.settimeout(6)
            g.command("?")
            if g.command(f"m{ENTRY:x},40") != data[:64].hex().encode():
                raise RuntimeError("Xtensa entry does not match same-run DMA bytes")
            for addr in (*FAULT_VECTORS, ABOX_MAIN_RETURN):
                if g.command(f"Z1,{addr:x},3") != b"OK":
                    raise RuntimeError(f"Xtensa observer could not arm {addr:#x}")
            self.report["stop_reply"] = g.command("c").decode()
            state = self.qmp.execute("human-monitor-command", {"command-line":"info registers"})
            (self.output / "core0-start-registers.txt").write_text(state, encoding="utf-8")
            if f"PC={ABOX_MAIN_RETURN:08x}" not in state:
                raise RuntimeError("Xtensa stopped before original ABOX main return")
            self.report["original_return_observed"] = True
            for addr in (*FAULT_VECTORS, ABOX_MAIN_RETURN):
                if g.command(f"z1,{addr:x},3") != b"OK":
                    raise RuntimeError("Xtensa observer could not disarm")
            if self.mailbox_arguments:
                                                                            
                                                                        
                for addr in (0xd0000adc, 0xd0000afc, 0xd0000b1c):
                    if g.command(f"Z1,{addr:x},3") != b"OK":
                        raise RuntimeError("Xtensa live fault observer could not arm")
                self.debugger = g
            elif g.command("D") != b"OK":
                raise RuntimeError("Xtensa debugger did not detach")
            if self.qmp.execute("query-status")["status"] != "running":
                self.qmp.execute("cont")
                                                                                
                                                                               
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                runstate = self.qmp.execute("query-status")
                live = self.qmp.execute("human-monitor-command", {"command-line":"info registers"})
                if (runstate["status"] == "running" and self.process.poll() is None
                        and f"PC={ABOX_MAIN_RETURN:08x}" not in live
                        and not any(f"PC={pc:08x}" in live for pc in FAULT_VECTORS)):
                    (self.output / "core0-live-registers.txt").write_text(live, encoding="utf-8")
                    return
                self.closed.wait(.02)
            raise RuntimeError("Xtensa did not execute beyond ABOX main return")
        finally:
            if self.debugger is not g:
                g.socket.close()

    def _run(self):
        import original_8704_vm as vm
        self.process = self.qmp = self.debugger = None
        try:
            while not self.closed.is_set():
                try:
                    conn, _ = self.server.accept()
                    break
                except TimeoutError:
                    continue
            else:
                return
            with conn, (self.output/"qemu.log").open("wb") as log:
                self.report["connected"] = True
                conn.settimeout(.2)
                vm.save(self.output/"link.json", self.report)
                while not self.closed.is_set():
                    if self.process is not None and self.process.poll() is not None:
                        raise RuntimeError("Xtensa exited during the AP session")
                    try:
                        data = conn.recv(1)
                    except TimeoutError:
                        if self.qmp and self.qmp.execute("query-status")["status"] != "running":
                            state = self.qmp.execute("human-monitor-command", {"command-line":"info registers"})
                            (self.output/"core0-fault-registers.txt").write_text(state,encoding="utf-8")
                            conn.sendall(b"F")
                            raise RuntimeError("Xtensa stopped during the AP session")
                        continue
                    if not data:
                        self.report["disconnect"] = "AP peer closed"
                        return
                    if data == b"S" and self.process is None:
                        self.report["original_pmu_release_received"] = True
                        try:
                            self._start_core(log)
                        except Exception:
                            conn.sendall(b"F")
                            raise
                        conn.sendall(b"O")
                        self.report["hardware_status_reported"] = True
                        vm.save(self.output/"link.json", self.report)
                    elif data == b"R":
                        self.report["reset_received"] = True
                        return
                    else:
                        raise RuntimeError(f"Invalid local lifecycle byte: {data.hex()}")
        except ConnectionResetError as exc:
            if self.closed.is_set():
                self.report["disconnect"] = "session shutdown"
            else:
                self.report["error"] = str(exc)
        except (OSError,RuntimeError,ValueError) as exc:
            self.report["error"] = str(exc)
        finally:
            if self.qmp is not None:
                try:
                    self.qmp.execute("quit")
                except (OSError,RuntimeError,ValueError):
                    pass
                finally:
                    self.qmp.close()
            if self.debugger is not None:
                self.debugger.socket.close()
            if self.process is not None:
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                self.report["process_exit"] = self.process.returncode
            vm.save(self.output/"link.json", self.report)

