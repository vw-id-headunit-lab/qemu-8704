"""Build the small AArch64 EGL forwarder from source with clang and lld."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--linker", default="ld.lld")
    parser.add_argument('--gpu', action='store_true', help='Also build the experimental VirGL scanout compatibility shim')
    args = parser.parse_args()
    clang = shutil.which(args.clang)
    linker = shutil.which(args.linker)
    if not clang or not linker:
        parser.error("Install LLVM clang and lld, or pass --clang/--linker paths")
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    source = ROOT / "native/egl-forward-bridge.c"
    obj, library = output / "egl-forward-bridge.o", output / "libegl-forward-bridge.so"
    backoff_source = ROOT / "native/missing-ioctl-backoff.c"
    backoff_obj = output / "missing-ioctl-backoff.o"
    commands = [[clang, "--target=aarch64-linux-gnu", "-O2", "-fPIC", "-fvisibility=hidden",
                 "-fno-stack-protector", "-fno-builtin", "-Wall", "-Wextra", "-c", str(source), "-o", str(obj)],
                [clang, "--target=aarch64-linux-gnu", "-O2", "-fPIC", "-fvisibility=hidden",
                 "-fno-stack-protector", "-fno-builtin", "-Wall", "-Wextra", "-c", str(backoff_source), "-o", str(backoff_obj)],
                [linker, "-m", "aarch64elf", "-shared", "--hash-style=both", "--soname=libegl-forward-bridge.so", str(obj), str(backoff_obj), "-o", str(library)]]
    if args.gpu:
        gpu_obj = output / 'virgl-drm-compat.o'
        commands += [
            [clang, '--target=aarch64-linux-gnu', '-O2', '-fPIC', '-fvisibility=hidden',
             '-fno-stack-protector', '-fno-builtin', '-Wall', '-Wextra', '-c',
             str(ROOT / 'native/virgl-drm-compat.c'), '-o', str(gpu_obj)],
            [linker, '-m', 'aarch64elf', '-shared', '--hash-style=both',
             '--soname=virgl-drm-compat.so', str(gpu_obj), '-o', str(output / 'libvirgl-drm-compat.so')],
        ]
    records = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        records.append({"argv": command, "exit_status": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError(result.stderr)
    data = library.read_bytes()
    if data[:4] != b"\x7fELF" or int.from_bytes(data[18:20], "little") != 183:
        raise ValueError("Expected AArch64 ELF")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/native-build.json").write_text(json.dumps({"commands": records,
        "sha256": hashlib.sha256(data).hexdigest(), "machine": "AArch64", "bytes": len(data)}, indent=2) + "\n")
    print("Built", library)


if __name__ == "__main__":
    main()
