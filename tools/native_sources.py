import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
SOURCE_URL='https://download.qemu.org/qemu-11.1.0.tar.xz'
SOURCE_SHA256='6ee1d1a61f68212476b27108c26da5f449dc09b626d42f8279ba0dc2e08fa858'


def ensure_archive(archive):
    """Fetch only the pinned open-source QEMU archive, never OEM firmware."""
    archive=Path(archive)
    if archive.exists():return
    archive.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=archive.parent,prefix='.qemu-source-',delete=False) as output:
            temporary=Path(output.name)
            with urllib.request.urlopen(SOURCE_URL,timeout=60) as response:
                shutil.copyfileobj(response,output)
        with temporary.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=SOURCE_SHA256:
                raise ValueError('Downloaded upstream source checksum mismatch')
        if not archive.exists():temporary.replace(archive)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


def prepare(build_root=None):
    archive=ROOT/'native/upstream/qemu-11.1.0.tar.xz'
    ensure_archive(archive)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=SOURCE_SHA256:
            raise ValueError('Upstream source checksum mismatch')
    destination=Path(build_root).resolve() if build_root else ROOT/'.local/build'
    if not destination.is_relative_to(ROOT/'.local'):raise ValueError('Build root must stay in .local')
    source=destination/'qemu-11.1.0'
    if source.exists():raise ValueError('Source destination already exists; use a fresh build directory')
    destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'r:xz') as tar:
        for member in tar.getmembers():
            target=(destination/member.name).resolve()
            if not target.is_relative_to(source.resolve()):raise ValueError('Unexpected source archive path')
        def windows_source_filter(member, directory):
            if member.name=='qemu-11.1.0/roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack':
                return None
            return tarfile.data_filter(member,directory)
        tar.extractall(destination,filter=windows_source_filter)
    for override in (ROOT/'native/overrides').rglob('*'):
        if override.is_file():
            target=source/override.relative_to(ROOT/'native/overrides')
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(override,target)
    print('Prepared '+source.relative_to(ROOT).as_posix())


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--build-root',type=Path)
    prepare(parser.parse_args().build_root)
