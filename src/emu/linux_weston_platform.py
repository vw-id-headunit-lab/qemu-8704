
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
import subprocess


ROOT_MEMBER = 'ivi/icas3cn-gp-image-euto-v9.ext4'
CONFIG = '/etc/early_init.conf'
CONFIG_SHA = 'a9b9f8313b07c46ca82a688d32406955eb4e7a2425c5d148167c624c2ae74baf'
ROOT_SHA = '047407f07805902c2bb58f90c14a6ff6c735b009abc0e50b22e22dd484c09387'
SALT = bytes.fromhex('34f6b885ae84b0ba59190e8f5fa8acc27dd94adee46aaf103d339fba02078c25')
BLOCK = 4096
PRIVATE_CLUSTER = 65536
DATA_BLOCKS = 786432
CONFIG_BLOCK = 9910
VERITY_PREFIX = f'verity 1 /dev/sda14 /dev/sda27 {BLOCK} {BLOCK} {DATA_BLOCKS} 1 sha256 '
ORIGINAL_TABLE = f'{VERITY_PREFIX}{ROOT_SHA} {SALT.hex()} 1 panic_on_corruption'
FILES = {
    '/usr/bin/weston': '11e1659a8c25f9129bd0f42ae93689c593d698dc84cb92f4ab4e64c7ac35ff69',
    '/usr/lib/weston/drm-backend.so': '29b1811b509b33c4f3a1c223168bd76b2a1bc609bf8c658af36542ec60fa4942',
    '/lib/systemd/system/weston.service': '69631b0dfb6296da563d07d995b9ceab14539018a346d57e897ec7401b502f15',
    '/usr/lib/libpixman-1.so.0.32.8': 'b7860c2e649482988d7db3a4fc209872e31373770c59e6de2f17b6a178add8c0',
}
TRACE_FILES = {
    '/bin/early_init': '9529bff2fc66e639735de7b7a518093e4110bc2a6b4c1678c930f731f8b8639d',
    '/usr/lib/libwayland-server.so.0.1.0': '3d97a8f960f28cf476ec91071106286ff0f6eae7f0695261aec06767f3d1daec',
}
TRACE_ENV = b'env=WAYLAND_DEBUG=server\n'
TRACE_LOG = '/run/weston.txt'


@dataclass(frozen=True)
class Change:
    slot: int
    block: int
    offset: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class Plan:
    base: Path
    virtual_bytes: int
    root_sha256: str
    changes: tuple[Change, ...]
    trace_protocol: bool = False
    pixman: bool = True

    def evidence(self):
        evidence = dict(renderer='pixman' if self.pixman else 'stock', launcher='/bin/early_init', config=CONFIG,
                    added_arg='--use-pixman' if self.pixman else None, original_weston=True, original_drm_backend=True,
                    original_root_sha256=ROOT_SHA, local_derived_root_sha256=self.root_sha256,
                    dm_verity_enabled=True, source_sha256={**FILES, **TRACE_FILES} if self.trace_protocol else FILES,
                    original_config_sha256=CONFIG_SHA,
                    private_vm3_delta=[dict(slot=x.slot, block=x.block, offset=x.offset,
                        before_sha256=sha(x.before), after_sha256=sha(x.after)) for x in self.changes])
        if self.trace_protocol:
            evidence.update(wayland_debug='server', native_log=TRACE_LOG)
        return evidence


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(block):
    if len(block) != BLOCK:
        raise ValueError('Short original dm-verity block')
    return hashlib.sha256(SALT + block).digest()


def configure(raw, *, trace_protocol=False, pixman=True):
    if sha(raw) != CONFIG_SHA:
        raise ValueError('Original early-init configuration changed')
    preamble = b'# Simple configure file for early-init program\n'
    argv = (b'[weston]\nuser=sint_sec_weston\n'
            b'env=XDG_RUNTIME_DIR=/run/platform/weston\n'
            b'env=WESTON_DISABLE_ATOMIC=1\nenv=WESTON_DRM_FD_LEASE_ENABLE=0\n'
            b'wait=/dev/dri/card0\ncmd=/usr/bin/weston\n'
            b'argv=--tty=2\nargv=--config=/usr/lib/weston.ini\nargv=--idle-time=0\n')
    option = b'argv=--use-pixman\n' if pixman else b''
    if not option and not trace_protocol:
        raise ValueError('Stock Weston without diagnostics needs no private configuration')
    if not raw.startswith(preamble) or raw.count(argv) != 1:
        raise ValueError('Original Weston configuration layout changed')
                                                                         
    extra = len(option) + (len(TRACE_ENV) if trace_protocol else 0)
    if extra >= len(preamble)-1:
        raise ValueError('Original comment cannot accommodate scoped Weston settings')
    modified = preamble[:len(preamble)-extra-1] + b'\n' + raw[len(preamble):]
    modified = modified.replace(argv, argv + option, 1)
    if trace_protocol:
                                                                               
                                                            
        anchor = b'env=WESTON_DRM_FD_LEASE_ENABLE=0\nwait=/dev/dri/card0\n'
        if modified.count(anchor)!=1 or modified.count(b'log='+TRACE_LOG.encode('ascii')+b'\n')!=1:
            raise ValueError('Original early Weston logging contract changed')
        modified = modified.replace(anchor, TRACE_ENV + anchor, 1)
    if len(modified) != len(raw):
        raise ValueError('Weston update changed the original inode size')
    return modified


def plan(base, *, trace_protocol=False, pixman=True):
    from dissect.extfs import ExtFS
    from vlm_platform import factory_file

    base = Path(base).resolve(strict=True)
    layout = json.loads(base.with_suffix('.json').read_bytes())
    rows = {p['slot']: p for p in layout['partitions'] if p['slot'] in (14, 27)}
    if (len(rows)!=2 or layout['sector_bytes']!=BLOCK or layout['blocks']*BLOCK!=base.stat().st_size
            or rows[14]['label']!='a_agl_system_ivi_rbhv-qia' or rows[27]['label']!='a_hash_ivi_rbhv-qia'
            or rows[14]['last']+1-rows[14]['first']!=DATA_BLOCKS
            or rows[14]['payload']['member']!=ROOT_MEMBER or rows[27]['payload']['member']!='ivi/ivi_hash.img'):
        raise ValueError('Original VM3 verity partition geometry changed')
    with factory_file(ROOT_MEMBER).open('rb') as stream:
        fs=ExtFS(stream)
        files = {**FILES, **TRACE_FILES} if trace_protocol else FILES
        for name, expected in files.items():
            with fs.get(name).open() as member:
                raw=member.read(1 << 20)
            if sha(raw)!=expected:
                raise ValueError('Original Weston renderer file changed: '+name)
        config = fs.get(CONFIG)
        if fs.block_size!=BLOCK or config.filetype!=stat.S_IFREG or config.dataruns()!=[(CONFIG_BLOCK,1)]:
            raise ValueError('Original early-init file allocation changed')
        original = config.open().read()
    modified = configure(original,trace_protocol=trace_protocol,pixman=pixman)
    with base.open('rb') as stream:
        def read(slot, index):
            block = rows[slot]['first'] + index
            if not rows[slot]['first'] <= block <= rows[slot]['last']:
                raise ValueError('Derived verity edit exceeds its partition')
            offset=block*BLOCK
            stream.seek(offset)
            return offset,stream.read(BLOCK)

        offset, old = read(14, CONFIG_BLOCK)
        if old[:len(original)]!=original:
            raise ValueError('VM3 original configuration does not match the base disk')
        new = modified + old[len(original):]
        changes = [Change(14, CONFIG_BLOCK, offset, old, new)]
        index = CONFIG_BLOCK
                                                                         
                                                                       
        for start in (50,2,1):
            parent_index, slot = divmod(index,128)
            block = start+parent_index
            offset, parent = read(27,block)
            pos=slot*32
            if parent[pos:pos+32]!=digest(old):
                raise ValueError('Original config-to-root authentication path is invalid')
            old,new=parent,parent[:pos]+digest(new)+parent[pos+32:]
            changes.append(Change(27, block, offset, old, new))
            index=parent_index
        if index or digest(old).hex()!=ROOT_SHA:
            raise ValueError('Original root digest does not match the factory boot table')
    return Plan(base,layout['blocks']*BLOCK,digest(new).hex(),tuple(changes),trace_protocol,pixman)


def inherited_sectors(selected, prior):

    if (prior.base!=selected.base or prior.virtual_bytes!=selected.virtual_bytes or
            [(c.slot,c.block,c.offset,c.before) for c in prior.changes] !=
            [(c.slot,c.block,c.offset,c.before) for c in selected.changes]):
        raise ValueError('Prior Weston delta does not have the same original sectors')
    return tuple(c.after for c in prior.changes)


def bootargs(value, selected):
    if value.count(ORIGINAL_TABLE)!=1 or selected.root_sha256==ROOT_SHA:
        raise ValueError('Expected one unmodified OEM dm-verity table')
    return value.replace(ORIGINAL_TABLE, ORIGINAL_TABLE.replace(ROOT_SHA,selected.root_sha256,1),1)


def inherited_cluster_contents(selected):

    expected={}
    with selected.base.open('rb') as base:
        for change in selected.changes:
            start=change.offset-change.offset%PRIVATE_CLUSTER
            pos=change.offset-start
            if start not in expected:
                base.seek(start)
                expected[start]=bytearray(base.read(PRIVATE_CLUSTER))
            block=expected[start]
            if (len(block)!=PRIVATE_CLUSTER or pos+BLOCK>len(block)
                    or block[pos:pos+BLOCK]!=change.before or len(change.after)!=BLOCK):
                raise ValueError('Original Weston cluster does not match its verified sector')
            block[pos:pos+BLOCK]=change.after
    return {offset:bytes(content) for offset,content in expected.items()}


def verify_inherited(selected, prior, info, source, out, allocation_rows):

    from linux_hmi_overlay import NBD, QEMU, connect
    import original_8704_vm as vm
    recorded=prior.get('early_weston_verity')
    if (not isinstance(recorded,dict) or recorded.get('exact_leaf_readback') is not True
            or any(recorded.get(k)!=v for k,v in selected.evidence().items())
            or info.get('cluster-size')!=PRIVATE_CLUSTER):
        raise ValueError('Prior VM3 Weston delta identity or cluster size changed')
    expected=inherited_cluster_contents(selected)
    checked=set()
    verified_rows=set()
    for row in allocation_rows:
        if row['depth'] or not row.get('present',row['data']):
            continue
        start,length=row['start'],row['length']
        if not any(start<offset+PRIVATE_CLUSTER and offset<start+length for offset in expected):
            continue
                                                                                    
        if length<=0 or start%PRIVATE_CLUSTER or length%PRIVATE_CLUSTER or length>len(expected)*PRIVATE_CLUSTER:
            raise ValueError('Prior VM3 Weston allocation crosses its verified clusters')
        clusters=set(range(start,start+length,PRIVATE_CLUSTER))
        if clusters&checked or not clusters<=expected.keys():
            raise ValueError('Prior VM3 Weston allocation reaches an unverified cluster')
        checked.update(clusters)
        verified_rows.add((start,length))
    if checked!=expected.keys():
        raise ValueError('Prior VM3 Weston delta has missing private allocations')
    port=vm.free_port()
    with (out/'linux-weston-prior-nbd.log').open('xb') as log:
        proc=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
                              '--format=qcow2','--read-only','--persistent',str(source)],
                             stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(proc,port) as sock:
                nbd=NBD(sock)
                if nbd.size!=selected.virtual_bytes or not nbd.flags&2:
                    raise ValueError('Prior VM3 Weston leaf is not the expected read-only export')
                for offset,content in expected.items():
                    if nbd.command(0,offset,PRIVATE_CLUSTER)!=content:
                        raise ValueError('Prior VM3 Weston cluster has unapproved bytes')
                nbd.command(2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=8)
    return frozenset(verified_rows)


def install(selected, base, media, out, overlay, *, inherited_plan=None):

    from linux_hmi_overlay import NBD, QEMU, ROOT, connect
    import original_8704_vm as vm

    out=Path(out).resolve(strict=True)
    image=Path(overlay['path']).resolve(strict=True)
    if (Path(base).resolve()!=selected.base or not out.is_relative_to(ROOT/'var')
            or image!=out/'ufs-vh1-data-session.qcow2' or overlay.get('owner')!='VM3 / UFS VH1'
            or media['virtual_bytes']!=selected.virtual_bytes):
        raise ValueError('Early Weston requires the matching session-private VM3 disk')
    port=vm.free_port()
    with (out/'linux-weston-verity-nbd.log').open('xb') as log:
        process=subprocess.Popen([str(QEMU/'qemu-nbd.exe'),'--bind=127.0.0.1',f'--port={port}',
                                  '--format=qcow2','--persistent',str(image)],stdout=log,stderr=log,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            with connect(process,port) as sock:
                nbd=NBD(sock)
                if nbd.size!=selected.virtual_bytes or not nbd.flags&4:
                    raise ValueError('VM3 Weston leaf size or flush support differs')
                actual=tuple(nbd.command(0,c.offset,BLOCK) for c in selected.changes)
                inherited=overlay.get('prior_weston_clusters_verified',0)>0
                expected=(inherited_sectors(selected,inherited_plan or selected) if inherited else
                          tuple(c.before for c in selected.changes))
                if actual!=expected:
                    raise ValueError('VM3 leaf does not contain the expected original or verified inherited sectors')
                if actual != tuple(c.after for c in selected.changes):
                    for change in selected.changes:
                        nbd.command(1,change.offset,BLOCK,change.after)
                    nbd.command(3)
                if any(nbd.command(0,c.offset,BLOCK)!=c.after for c in selected.changes):
                    raise ValueError('VM3 Weston verity delta readback failed')
                nbd.command(2)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
    return dict(overlay,early_weston_verity=dict(selected.evidence(),exact_leaf_readback=True,
                                                 inherited_verified_delta=inherited))
