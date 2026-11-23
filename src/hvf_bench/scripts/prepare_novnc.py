"""Fetch a version-pinned noVNC package; verify npm integrity; run no install scripts."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.7.0"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/novnc")
    args = parser.parse_args()
    with urllib.request.urlopen(f"https://registry.npmjs.org/@novnc/novnc/{VERSION}", timeout=45) as response:
        metadata = json.load(response)
    url = metadata["dist"]["tarball"]
    if not url.startswith("https://registry.npmjs.org/"):
        raise ValueError("Expected the official npm registry tarball")
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Unexpected noVNC archive size")
    algorithm, expected = metadata["dist"]["integrity"].split("-", 1)
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(data).digest()).decode() != expected:
        raise ValueError("npm SHA-512 integrity mismatch")
    target = args.output.resolve()
    rows = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "package":
                raise ValueError("Unexpected npm archive member")
            if not member.isfile():
                if member.isdir():
                    continue
                raise ValueError("Only regular noVNC files/directories are accepted")
            relative = Path(*path.parts[1:])
            destination = (target / relative).resolve()
            if target not in destination.parents:
                raise ValueError("npm member leaves its destination")
            content = archive.extractfile(member).read()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            rows.append({"path": relative.as_posix(), "sha256": hashlib.sha256(content).hexdigest()})
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/novnc-download.json").write_text(json.dumps({"version": VERSION, "url": url,
        "integrity": metadata["dist"]["integrity"], "files": rows}, indent=2) + "\n")
    print("Verified noVNC", VERSION, "into", target)


if __name__ == "__main__":
    main()
