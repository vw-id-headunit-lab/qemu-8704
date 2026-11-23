
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

KIT = Path(__file__).resolve().parent
ROOT = KIT.parents[1]
MANIFEST = KIT / "manifest.json"


def load_manifest():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    entries = manifest["entries"]
    names, paths = set(), set()
    for entry in entries:
        relative = Path(entry["path"])
        target = (ROOT / relative).resolve()
        roots = [(ROOT / base).resolve() for base in ("src/firmware", "src/emu", "src/runner")]
        if (relative.is_absolute() or ".." in relative.parts or target.suffix != ".py"
                or not any(target.is_relative_to(base) for base in roots)):
            raise ValueError("Manifest path outside source trees: " + entry["path"])
        if entry["name"] in names or entry["path"] in paths:
            raise ValueError("Duplicate manifest name/path")
        digest = entry["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid SHA256: " + entry["path"])
        names.add(entry["name"])
        paths.add(entry["path"])
    if manifest["script_count"] != len(entries):
        raise ValueError("Manifest count mismatch")
    return manifest


def select(manifest, name):
    exact = [e for e in manifest["entries"] if name in (e["name"], e["path"])]
    matches = exact or [e for e in manifest["entries"] if Path(e["path"]).name == name]
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous tool: " + name)
    return matches[0]


def stage(entry):

    source = ROOT / entry["path"]
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("Source SHA256 mismatch; review and rebuild manifest: " + entry["path"])
    target = ROOT / ".local" / "rekit" / entry["sha256"] / source.name
    if not target.resolve().is_relative_to(ROOT / ".local" / "rekit"):
        raise ValueError("Runtime path escaped tools/rekit")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError("Existing runtime copy differs: " + str(target))
    else:
        with target.open("xb") as stream:
            stream.write(data)
    return source, target


class CopyLoader(importlib.abc.Loader):

    def __init__(self, entry):
        self.entry = entry

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source, copy = stage(self.entry)
        module.__file__ = str(source)
        code = compile(copy.read_bytes(), str(source), "exec")
        exec(code, module.__dict__)


class CopyFinder(importlib.abc.MetaPathFinder):
    def __init__(self, manifest):
        self.entries = {(ROOT / e["path"]).resolve(): e for e in manifest["entries"]}
        self.roots = [(ROOT / base).resolve() for base in ("src/firmware", "src/emu", "src/runner")]

    def find_spec(self, fullname, path=None, target=None):
        leaf = fullname.rsplit(".", 1)[-1]
        for directory in sys.path if path is None else path:
            base = Path(directory or ROOT)
            for candidate, package in ((base / leaf / "__init__.py", True), (base / (leaf + ".py"), False)):
                resolved = candidate.resolve()
                if resolved in self.entries:
                    spec = importlib.util.spec_from_loader(fullname, CopyLoader(self.entries[resolved]),
                                                         origin=str(resolved), is_package=package)
                    if package:
                        spec.submodule_search_locations = [str(resolved.parent)]
                    return spec
                if candidate.is_file() and any(resolved.is_relative_to(base) for base in self.roots):
                    raise ImportError("Source not in reviewed manifest: " + str(candidate))
        return None


def execute_copy(manifest, entry, arguments):
    source, copy = stage(entry)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source.parent))
    sys.meta_path.insert(0, CopyFinder(manifest))
    sys.argv = [str(source), *arguments]
    namespace = {"__name__": "__main__", "__file__": str(source),
                 "__package__": None, "__spec__": None, "__builtins__": __builtins__}
    exec(compile(copy.read_bytes(), str(source), "exec"), namespace)
    return 0


def run(manifest, entry, arguments):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = ROOT / "var/logs" / ("rekit-run-" + stamp)
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()),
               "_execute", entry["name"], "--", *arguments]
    record = {"tool": entry, "argv": command, "cwd": str(ROOT),
              "started_at_utc": stamp, "exit_code": None,
              "scope": "Explicit tool invocation; this launcher does not sandbox target script side effects."}
    report = output / "assessment.json"
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("rekit run record: " + str(report), file=sys.stderr, flush=True)
    try:
        stage(entry)
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        record["exit_code"] = result.returncode
        return result.returncode
    except BaseException as error:
        record["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List every manifest entry and SHA256; never run sources")
    listing.add_argument("--json", action="store_true", help="Emit the full machine-readable manifest")
    describe = commands.add_parser("describe", help="Show source docstring, SHA256 and effect classification")
    describe.add_argument("name")
                                                                                          
    for name in ("run", "_execute"):
        command = commands.add_parser(name, help="Run a verified tools/ copy" if name == "run" else argparse.SUPPRESS)
        command.add_argument("name")
        command.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if args.command == "list":
            if args.json:
                print(json.dumps(manifest, ensure_ascii=False, indent=2))
            else:
                print("NAME\tREAD_ONLY_HEURISTIC\tSHA256\tPATH\tPURPOSE")
                for entry in manifest["entries"]:
                    purpose = " ".join(entry["purpose"].split())
                    print("\t".join([entry["name"], str(entry["read_only"]).lower(),
                                     entry["sha256"], entry["path"], purpose]))
                print("TOTAL\t" + str(len(manifest["entries"])))
            return 0
        entry = select(manifest, args.name)
        if args.command == "describe":
            print(json.dumps(entry, ensure_ascii=False, indent=2))
            return 0
        arguments = args.arguments
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        return (execute_copy if args.command == "_execute" else run)(manifest, entry, arguments)
    except (OSError, ValueError, KeyError, ImportError) as error:
        print("rekit: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
