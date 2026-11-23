"""Command-line entry points for configuration, launch and local lifecycle."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

from .config import Config
from .launcher import make_argv, qmp, run


def main(argv=None) -> int:
    entry_origin = getattr(sys, '_hmi_bench_entry_perf', time.perf_counter())
    entry_origin_label = 'python_entry' if hasattr(sys, '_hmi_bench_entry_perf') else 'cli_main_after_imports'
    parser = argparse.ArgumentParser(description="Source-only local MIB3 ARM64 HMI bench")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "plan", "run", "status", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("config.local.json"))
        if name in ("doctor", "plan", "run"):
            command.add_argument("--mode", choices=("baseline", "jvm", "hmi"), default="hmi")
            command.add_argument('--gpu', action='store_true', help='Use native VirGL GPU rendering and tablet input instead of the browser viewer')
        if name == "run":
            command.add_argument('--http-gateway', type=Path, help='Opt in to guest 50006/50007 using this Linux ARM64 Go gateway ELF')
            command.add_argument('--gateway-relay', type=Path, default=Path('build/bench-tcp-relay-linux-arm64'))
            command.add_argument('--hmi-proxy-port', type=int, choices=(50006, 50007), default=50007)
            command.add_argument('--sdk-gateway', action='store_true', help='Also bridge the local simulated Android SDK gateway on guest 50005')
            command.add_argument('--disable-animations', action='store_true', help='Disable original View widget animations on a fresh HMI boot; other rendering settings unchanged')
            command.add_argument("--seconds", type=int, default=3600)
            command.add_argument("--open-viewer", action="store_true")
            command.add_argument("--language", choices=("en_US", "zh_CN"), default="en_US", help="Initial original HMI language, supplied through local RSI data")
            command.add_argument('--measure', action='store_true', help='Record P0 timing and QEMU process samples; no performance tuning is applied')
            command.add_argument('--scenario', default='default', help='Same-scene grouping label for comparisons')
            command.add_argument('--cache-state', choices=('unspecified', 'first', 'repeat'), default='unspecified', help='Operator label only; no OS caches are flushed')
            command.add_argument('--label', default='', help='Free-text run label, e.g. baseline-1')
            command.add_argument("--debug-shell", action="store_true", help="Expose an unauthenticated guest root shell on a random loopback socket (off by default)")
    metrics = commands.add_parser('metrics', help='Inspect/annotate a measured session or summarize repeated runs')
    operations = metrics.add_subparsers(dest='operation', required=True)
    show = operations.add_parser('show')
    show.add_argument('--config', type=Path, default=Path('config.local.json'))
    sample = operations.add_parser('sample')
    sample.add_argument('--config', type=Path, default=Path('config.local.json'))
    sample.add_argument('--seconds', type=int, default=10)
    sample.add_argument('--label', default='idle-menu')
    mark = operations.add_parser('mark')
    mark.add_argument('--config', type=Path, default=Path('config.local.json'))
    mark.add_argument('--event', choices=('first_visible', 'menu_ready', 'arm_click', 'confirm_click', 'cancel_click'), required=True)
    mark.add_argument('--label', default='Settings')
    summary = operations.add_parser('summary')
    summary.add_argument('--directory', type=Path, default=Path('output/measurements'))
    summary.add_argument('--json', dest='json_path', type=Path)
    summary.add_argument('--csv', dest='csv_path', type=Path)
    simulator = commands.add_parser('simulate-api', help='Run the in-memory loopback RSI 30001 simulator; no guest/cloud/device actions')
    simulator.add_argument('--host', choices=('127.0.0.1', '::1'), default='127.0.0.1')
    simulator.add_argument('--port', type=int, default=30001)
    simulator.add_argument('--seconds', type=float, default=0, help='Zero runs until interrupted or stop-file is created')
    simulator.add_argument('--ready-file', type=Path)
    simulator.add_argument('--stop-file', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'simulate-api':
            import asyncio
            from .rsi_simulator import serve
            try:
                asyncio.run(serve(args.host, args.port, args.seconds, args.ready_file, args.stop_file))
            except KeyboardInterrupt:
                pass
            return 0
        if args.command == 'metrics' and args.operation == 'summary':
            from .measurement import atomic_json, summarize, summary_csv
            report = summarize(args.directory)
            if args.json_path:
                args.json_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(args.json_path, report)
            if args.csv_path:
                args.csv_path.parent.mkdir(parents=True, exist_ok=True)
                summary_csv(args.csv_path, report)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        config = Config.load(args.config)
        if args.command == 'metrics':
            session = json.loads((config['output'] / 'session.json').read_text(encoding='utf-8'))
            if 'measurement_file' not in session:
                raise ValueError('This session was not started with --measure; no measurements are invented for older sessions')
            from .measurement import atomic_json, sample_window
            path = Path(session['measurement_file']).resolve()
            if (config['output'] / 'measurements').resolve() not in path.parents:
                raise ValueError('Unexpected measurement record location')
            if args.operation == 'show':
                print(path.read_text(encoding='utf-8'))
                return 0
            if session['state'] != 'running' or not qmp(session['ports']['qmp_port'], 'query-status').get('running'):
                raise ValueError('Sampling/marking requires the current measured VM to be running')
            if args.operation == 'sample':
                if not 1 <= len(args.label.strip()) <= 80:
                    raise ValueError('A sample label of 1..80 characters is required')
                report = sample_window(session['qemu_pid'], args.seconds, label=args.label.strip())
                destination = path.parent / ('cpu-window-' + uuid.uuid4().hex[:12] + '.json')
                atomic_json(destination, report)
                print(json.dumps({'saved_to': str(destination), **report}, indent=2))
                return 0
            from urllib.parse import urlsplit
            from urllib.request import ProxyHandler, Request, build_opener
            import urllib.error
            viewer = urlsplit(session.get('viewer_url', ''))
            if viewer.scheme != 'http' or viewer.hostname != '127.0.0.1' or viewer.port is None:
                raise ValueError('The measured viewer must be on loopback')
            endpoint = f'http://127.0.0.1:{viewer.port}/api/measurement'
            request = Request(endpoint, data=json.dumps({'action': args.event, 'label': args.label}).encode(), headers={'Content-Type': 'application/json'})
            try:
                with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                    result = json.load(response)
            except urllib.error.HTTPError as error:
                raise ValueError(error.read().decode()) from error
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "doctor":
            config.validate(args.mode, gpu=args.gpu)
            print(json.dumps({"configuration": "ready", "mode": args.mode, "ui_verified": False}, indent=2))
            return 0
        if args.command == "plan":
            config.validate(args.mode, gpu=args.gpu)
            print(json.dumps({"argv_template": make_argv(config, args.mode, config["output"] / (args.mode + "-initramfs.gz"), gpu=args.gpu),
                              "note": "Port numbers are placeholders in a plan. No processes or sockets have been started."}, indent=2))
            return 0
        if args.command == "run":
            gateway = None
            if args.http_gateway:
                from .guest_gateway import GatewayOptions
                gateway = GatewayOptions(args.http_gateway.expanduser().resolve(), args.gateway_relay.expanduser().resolve(),
                                         args.hmi_proxy_port, args.sdk_gateway)
            elif args.sdk_gateway:
                parser.error('--sdk-gateway requires --http-gateway')
            if not 1 <= args.seconds <= 3600:
                parser.error("--seconds must be between 1 and 3600")
            if not args.scenario.strip() or len(args.scenario) > 80 or len(args.label) > 120:
                parser.error('--scenario must contain 1..80 characters and --label at most 120')
            return run(config, args.mode, args.seconds, args.open_viewer, args.debug_shell, args.language,
                       measure=args.measure, scenario=args.scenario, cache_state=args.cache_state,
                       label=args.label, entry_origin=entry_origin, entry_origin_label=entry_origin_label, gateway=gateway,
                       disable_animations=args.disable_animations, gpu=args.gpu)
        record = json.loads((config["output"] / "session.json").read_text(encoding="utf-8"))
        if record["state"] != "running":
            print(json.dumps(record, indent=2))
            return 0
        result = qmp(record["ports"]["qmp_port"], "quit" if args.command == "stop" else "query-status")
        print(json.dumps({"session_state": record["state"], "qmp": result, "viewer_url": record.get("viewer_url")}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
