import hashlib
import json
from pathlib import Path
import tarfile
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def install():
    lock=json.loads((ROOT/'config/dependencies.lock.json').read_text(encoding='utf8'))
    cache=ROOT/'.local/cache';cache.mkdir(parents=True,exist_ok=True)
    destination=ROOT/'.local/deps'
    for package in lock['packages']:
        if all((destination/f['target']).is_file() and hashlib.file_digest((destination/f['target']).open('rb'),'sha256').hexdigest()==f['sha256'] for f in package['files']):
            continue
        url=urllib.parse.urlsplit(package['url'])
        if url.scheme!='https' or url.hostname not in ('repo.msys2.org','mirror.msys2.org'):
            raise ValueError('Dependency origin is not allowed')
        path=cache/(package['sha256']+'.pkg.tar.zst')
        if not path.exists():
            temporary=path.with_suffix('.part')
            with urllib.request.urlopen(package['url'],timeout=60) as source,temporary.open('wb') as output:
                size=0
                while data:=source.read(1<<20):
                    size+=len(data)
                    if size>package['bytes']:raise ValueError('Unexpected dependency size')
                    output.write(data)
            temporary.replace(path)
        if path.stat().st_size!=package['bytes'] or hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=package['sha256']:
            raise ValueError('Dependency archive checksum mismatch')
        with tarfile.open(path,'r:zst') as archive:
            for row in package['files']:
                target=(destination/row['target']).resolve()
                if not target.is_relative_to(destination.resolve()):raise ValueError('Unsafe dependency target')
                member=archive.getmember(row['member'])
                if not member.isfile() or member.size!=row['bytes']:raise ValueError('Invalid dependency member')
                data=archive.extractfile(member).read()
                if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Dependency member checksum mismatch')
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_suffix(target.suffix+'.part');temporary.write_bytes(data);temporary.replace(target)
    return sum(len(p['files']) for p in lock['packages'])


if __name__=='__main__':
    print(json.dumps({'verified_dependency_files':install()}))
