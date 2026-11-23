"""Own the disposable VM and the loopback servers for one local session."""
from contextlib import ExitStack
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from .archive import build_initramfs, digest
from .config import Config, PROJECT
from .sparse_nbd import SparseNBDServer


def free_port(minimum: int = 1) -> int:
    # Windows may allocate an ephemeral port below 5900; QEMU's VNC argument
    # uses a nonnegative display number (TCP port minus 5900).
    for _ in range(128):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            if port >= minimum:
                return port
    raise OSError('Could not allocate a suitable loopback port')


def qmp(port: int, command: str) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        stream = connection.makefile("rwb", buffering=0)
        if "QMP" not in json.loads(stream.readline()):
            raise RuntimeError("Expected this session's QEMU monitor")
        result = {}
        for operation in ("qmp_capabilities", command):
            stream.write((json.dumps({"execute": operation}) + "\n").encode())
            while True:
                line = stream.readline()
                if not line:
                    raise RuntimeError("QEMU monitor closed before replying")
                response = json.loads(line)
                if "error" in response:
                    raise RuntimeError(response["error"])
                if "return" in response:
                    result = response["return"]
                    break
        return result


def make_argv(config: Config, mode: str, initrd: Path, *, nbd_port: int = 9000,
              rfb_port: int = 5901, qmp_port: int = 9001, input_port: int = 9002,
              debug_port: int | None = None, api_backend_port: int | None = None,
              sdk_backend_port: int | None = None, clock_port: int | None = None,
              gpu: bool = False) -> list[str]:
    argv = [str(config["qemu"]), "-machine", "virt,gic-version=3", "-cpu", "host", "-accel", "hvf",
            "-smp", "4" if mode == "hmi" else "2", "-m", "2048" if mode == "hmi" else "1024",
            "-display", "gtk,gl=on,zoom-to-fit=on" if gpu else "none", "-serial", "stdio", "-monitor", "none", "-no-reboot", "-nic", "none",
            "-kernel", str(config["kernel"]), "-initrd", str(initrd),
            "-append", "console=ttyAMA0 rdinit=/probe-init", "-qmp",
            f"tcp:127.0.0.1:{qmp_port},server=on,wait=off"]
    if mode != "baseline":
        for name, path in (("agl", config["firmware_root"]), ("modules", config["modules"]), ("custom", config["firmware_custom"])):
            argv += ["-drive", f"file={path.as_posix().replace(',', ',,')},format=raw,if=none,id={name},readonly=on",
                     "-device", f"virtio-blk-device,drive={name}"]
    if mode == "hmi":
        if api_backend_port is not None:
            from .guest_gateway import netdev_arguments
            argv += netdev_arguments(api_backend_port, sdk_backend_port)
        argv += ["-drive", f"file=nbd://127.0.0.1:{nbd_port},format=raw,if=none,id=vwhmi,readonly=on",
                  "-device", "virtio-blk-device,drive=vwhmi", "-device",
                  ("virtio-gpu-gl-pci" if gpu else "virtio-gpu-pci") + ",xres=1920,yres=1080",
                 "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci",
                  "-L", str(config["qemu_data"]),
                 "-device", "virtio-serial-pci", "-chardev",
                 f"socket,id=bench-input,host=127.0.0.1,port={input_port},server=on,wait=off",
                 "-device", "virtserialport,chardev=bench-input,name=bench.input"]
        if not gpu:
            argv += ["-vnc", f"127.0.0.1:{rfb_port - 5900}"]
        if debug_port is not None:
            argv += ["-chardev", f"socket,id=bench-control,host=127.0.0.1,port={debug_port},server=on,wait=off",
                     "-device", "virtserialport,chardev=bench-control,name=bench.control"]
        if clock_port is not None:
            argv += ['-chardev', f'socket,id=bench-clock,host=127.0.0.1,port={clock_port}',
                     '-device', 'virtserialport,chardev=bench-clock,name=bench.clock']
    return argv


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def startup_failure(mode: str, log: str) -> str | None:
    # A guest poweroff can exit QEMU successfully after a failed readiness gate.
    if mode == 'hmi':
        if 'DISPLAY_SETUP_FAILED' in log:
            return 'display_readiness'
        if 'HOST_CLOCK_SETUP_FAILED' in log:
            return 'host_clock'
        if 'GUEST_GATEWAY_SETUP_FAILED' in log:
            return 'guest_gateway'
        if 'PLATFORM_SETUP_FAILED' in log:
            return 'platform_readiness'
        import re
        status = re.search(r'ORIGINAL_MIBMAIN_EXIT=(\d+)', log)
        if status and int(status[1]) not in (0, 143):
            return 'hmi_process'
    return None


def run(config: Config, mode: str, seconds: int, open_viewer: bool = False, debug_shell: bool = False, language: str = 'en_US',
        *, measure: bool = False, scenario: str = 'default', cache_state: str = 'unspecified', label: str = '', entry_origin=None,
        entry_origin_label: str = 'run_function', gateway=None, disable_animations: bool = False,
        gpu: bool = False) -> int:
    measurement_origin = time.perf_counter() if entry_origin is None else entry_origin
    config.validate(mode, gpu=gpu)
    if gpu and mode != 'hmi':
        raise ValueError('GPU rendering requires hmi mode')
    if disable_animations and mode != 'hmi':
        raise ValueError('Animation control requires hmi mode')
    if gateway:
        gateway.validate(mode)
    output = config["output"]
    output.mkdir(parents=True, exist_ok=True)
    lock = output / "session.lock"
    # Exclusive creation prevents a second run from replacing an active session's record.
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as error:
        raise RuntimeError("Session lock exists. Check status/stop first; remove a stale output/session.lock only after confirming no VM is running.") from error
    process = None
    measurement = sampler = None
    record_lock = threading.Lock()
    record = {"state": "preparing", "mode": mode, "language": language, "host_command_argv": sys.argv,
              "view_animations_disabled": disable_animations,
              "graphics": "virgl-native" if gpu else "llvmpipe-vnc",
              "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "qemu_exit_status": None, "hmi_exit_status": None,
              "ui_observed": None, "interactive_menu_verified": None,
              "note": "Exit codes and sockets do not verify graphical application behavior."}
    session_path = output / "session.json"
    try:
        with handle, ExitStack() as cleanup:
            handle.write(str(os.getpid()))
            handle.flush()
            if measure:
                from .measurement import Measurement
                measurement = Measurement(output, origin=measurement_origin,
                    origin_label=entry_origin_label,
                    scenario=scenario, cache_state=cache_state, label=label)
                record['measurement_file'] = str(measurement.path)
                measurement.mark('configuration_validated')
                measurement.mark('initramfs_build_started')
            if gpu:
                initrd, members = build_initramfs(config, mode, seconds, language, debug_shell,
                    gateway=gateway, disable_animations=disable_animations, gpu=True)
            elif disable_animations:
                initrd, members = build_initramfs(config, mode, seconds, language, debug_shell,
                    gateway=gateway, disable_animations=True)
            elif gateway:
                initrd, members = build_initramfs(config, mode, seconds, language, debug_shell, gateway=gateway)
            else:
                initrd, members = build_initramfs(config, mode, seconds, language, debug_shell)
            if measurement:
                measurement.mark('initramfs_ready')
                measurement.mark('transport_setup_started')
            ports = {"qmp_port": free_port()}
            nbd = viewer = None
            if mode == "hmi":
                from .host_clock import HostClockFeed
                clock = HostClockFeed(output)
                cleanup.callback(clock.close)
                ports['clock_port'] = clock.port
                record['clock_source'] = 'host_system_clock'
                if gateway:
                    from .guest_gateway import LocalBackends
                    backends = LocalBackends(output, gateway.sdk_gateway)
                    cleanup.callback(backends.close)
                    ports.update(backends.start())
                    record['guest_gateway'] = dict(enabled=True, b2b_port=50006, b2c_port=50007,
                        hmi_proxy_port=gateway.proxy_port, sdk_gateway=gateway.sdk_gateway,
                        network='restricted user network with explicit loopback guestfwd only',
                        binary_sha256=digest(gateway.binary), relay_sha256=digest(gateway.relay))
                from .viewer import VNCViewerServer
                nbd = SparseNBDServer(config["firmware_hmi"])
                cleanup.callback(nbd.close)
                ports.update(nbd_port=nbd.port, rfb_port=free_port(5900), input_port=free_port())
                if debug_shell:
                    ports["debug_port"] = free_port()
                if not gpu:
                    viewer = VNCViewerServer(ports["rfb_port"], input_port=ports["input_port"], static_root=config["novnc"], progress_path=output / "stdout.txt", measurement=measurement)
                    cleanup.callback(viewer.close)
                    record["viewer_url"] = viewer.url
            argv = make_argv(config, mode, initrd, gpu=gpu, **ports)
            if measurement:
                measurement.mark('transports_ready')
                measurement.mark('profile_capture_started')
                source_files = [PROJECT / 'run.py', *sorted((PROJECT / 'src/hmi_bench').glob('*.py'))]
                sources = {str(path.relative_to(PROJECT)): digest(path) for path in source_files}
                # Do not scan firmware gigabytes and warm the OS page cache merely to measure startup.
                inputs = {key: {'path': str(config[key]), 'bytes': config[key].stat().st_size,
                                'mtime_ns': config[key].stat().st_mtime_ns}
                          for key in config.required(mode) if config[key].is_file()}
                measurement.set_profile(mode=mode, language=language, members=members, sources=sources, inputs=inputs, argv=argv)
                measurement.mark('profile_capture_finished')
            record.update(ports=ports, argv=argv, overlay_members=members,
                          initramfs_sha256=digest(initrd), state="starting")
            write_json(session_path, record)
            if measurement:
                measurement.mark('qemu_launch_started')
            # Windows QEMU's stdio reader retries successful zero-byte reads.
            # NUL therefore burns a host core even when the guest is paused.
            # An open, unwritten pipe blocks the reader until this session ends.
            startup = None
            if gpu and os.name == 'nt':
                import ctypes
                ctypes.windll.kernel32.SetErrorMode(0x8003)
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 1
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd=output, startupinfo=startup)
            cleanup.callback(process.stdin.close)
            record.update(qemu_pid=process.pid, state="running")
            write_json(session_path, record)
            if measurement:
                measurement.attach_process(process.pid)
                from .measurement import ProcessSampler
                sampler = ProcessSampler(measurement, process.pid)
                sampler.start()
                cleanup.callback(sampler.close)

            started = time.monotonic()
            record['milestone_wall_seconds'] = {}
            def capture(name: str) -> None:
                seen = b''
                from .progress import MARKERS
                with (output / (name + ".txt")).open("wb", buffering=0) as target:
                    while chunk := getattr(process, name).read1(65536):
                        target.write(chunk)
                        if name == 'stdout':
                            seen = (seen + chunk)[-32768:]
                            for marker, phase, _ in MARKERS:
                                if phase not in record['milestone_wall_seconds'] and marker.encode() in seen:
                                    record['milestone_wall_seconds'][phase] = round(time.monotonic() - started, 3)
                            if measurement:
                                measurement.observe_log(seen)
                                # Persist live metadata only at coarse milestones, not every log line.
                                count = len(record['milestone_wall_seconds'])
                                if count != getattr(threading.current_thread(), '_saved_milestones', -1):
                                    threading.current_thread()._saved_milestones = count
                                    with record_lock:
                                        write_json(session_path, record)
            readers = [threading.Thread(target=capture, args=(name,), daemon=True) for name in ("stdout", "stderr")]
            for reader in readers:
                reader.start()
            if viewer:
                print("LIVE_VIEWER_URL=" + viewer.url, flush=True)
            if measurement:
                print('MEASUREMENT_FILE=' + str(measurement.path), flush=True)
            if open_viewer and viewer:
                for _ in range(50):
                    try:
                        with socket.create_connection(("127.0.0.1", ports["rfb_port"]), timeout=0.2) as stream:
                            if stream.recv(12).startswith(b"RFB "):
                                webbrowser.open_new_tab(viewer.url)
                                break
                    except OSError:
                        if process.poll() is not None:
                            break
                        time.sleep(0.1)
            try:
                result = process.wait(timeout=None if mode == "hmi" and os.environ.get("MEB_HVF_UNTIL_STOP") == "1" else seconds + 90 if mode == "hmi" else 100)
            except subprocess.TimeoutExpired:
                record["host_timeout"] = True
                process.kill()
                result = process.wait(timeout=10)
            except KeyboardInterrupt:
                record["interrupted"] = True
                process.terminate()
                result = process.wait(timeout=10)
            for reader in readers:
                reader.join(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            record.update(state="ended", qemu_exit_status=result)
            if nbd:
                record["disk_transport"] = nbd.stats
            if viewer:
                record["pointer_events_sent"] = viewer.input_events_sent
            log = (output / "stdout.txt").read_text(encoding="utf-8", errors="replace")
            import re
            status = re.search(r"ORIGINAL_MIBMAIN_EXIT=(\d+)", log)
            record["hmi_exit_status"] = int(status[1]) if status else None
            record["arm64_shell_observed"] = "aarch64" in log
            record["jvm_version_observed"] = "FIRMWARE_JVM_EXIT=0" in log
            failure = startup_failure(mode, log)
            if failure:
                record.update(state='error', startup_failure=failure, error='Guest startup or readiness failed')
            print(json.dumps({key: record[key] for key in ("state", "qemu_exit_status", "hmi_exit_status", "ui_observed")}, indent=2))
            return result or (1 if failure else 0)
    except BaseException as error:
        record.update(state="error", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if measurement:
            measurement.exclusions(record)
            measurement.finish(record['state'], record['qemu_exit_status'])
        write_json(session_path, record)
        lock.unlink(missing_ok=True)
