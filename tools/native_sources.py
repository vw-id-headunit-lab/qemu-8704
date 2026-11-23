import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[1]


def prepare(build_root=None):
    archive=ROOT/'native/upstream/qemu-11.1.0.tar.xz'
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!='6ee1d1a61f68212476b27108c26da5f449dc09b626d42f8279ba0dc2e08fa858':
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
