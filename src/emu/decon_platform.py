
import hashlib
import struct
from vlm_platform import factory_file, original, parse_dtb

PROFILE = 'virtual-dpus0-window-v1'
PATH = '/machine/exynos-decon-window'
WINDOWS = (0x1aa32000, 0x1aa33000, 0x1aa34000, 0x1aa35000)
MASKS = {0:0xf3, 0xc:0x3fff3fff, 0x10:0x3fff3fff, 0x14:0x00ff03ff, 0x18:0x03ff03ff, 0x1c:0xffffffff}
STATUS = 0x810

def one(raw):
    return struct.unpack('>I', raw)[0]

def chunks(raw):
    return tuple(struct.iter_unpack('>QI', raw))

def policy(profile, dpp_contract):
    if profile is None:
        return None
    if profile != PROFILE or not dpp_contract:
        raise ValueError('DECON WOD windows require the original Android DPP profile')
    image = factory_file('android/vendor_boot.img')
    with image.open('rb') as f:
        header = f.read(2112)
        page, ramdisk, hlen, dtlen = (struct.unpack_from('<I',header,i)[0] for i in (12,24,2096,2100))
        f.seek(((hlen+page-1)//page+(ramdisk+page-1)//page)*page)
        dt = f.read(dtlen)
    digest = hashlib.sha256(dt).hexdigest()
    if digest != dpp_contract['factory_android_dtb_sha256']:
        raise ValueError('DECON WOD and DPP require the same Android DT')
    nodes = parse_dtb(dt)
    refs = {props['phandle']:name for name, props in nodes.items() if 'phandle' in props}
    hcb = original('hcb/hcb.img').read_bytes()
    start = hcb.index(b'VDTVM4\0\0')+8
    hcb_nodes = parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
    expected = {'/wod@display_main':WINDOWS[:2],'/wod@display_avatar':WINDOWS[2:]}
    entries = []
    wod_paths = tuple(refs[handle] for (handle,) in struct.iter_unpack('>4s',nodes['/vdpu']['decon-list']))
    if set(wod_paths) != set(expected):
        raise ValueError('Original Android vDPU WOD list changed')
    dpp_nodes = {row['android_node'] for row in dpp_contract['words']}
    for wod, bases in expected.items():
        parent = nodes[wod]
        if parent['compatible'] != b'samsung,exynos9-wod\0' or one(parent['samsung,decon_idx']) != 2:
            raise ValueError('Original Android WOD kind or DECON index changed')
        windows = tuple(refs[handle] for (handle,) in struct.iter_unpack('>4s', parent['samsung,windows']))
        if len(windows) != len(bases):
            raise ValueError('Original Android WOD window count changed')
        for node, base in zip(windows,bases):
            props = nodes[node]
            dpp = refs[props['samsung,channel-in']]
            index = one(props['cell-index'])
            hcb_node = f'/vplatform/vl,dpus0/vl,io-memory@0x{base:X}'
            grant = hcb_nodes[hcb_node]
            if dpp not in dpp_nodes or chunks(grant['reg']) != chunks(props['reg']) or 'vl,disabled' in grant:
                raise ValueError(f'WOD window not bound to original VM4 hardware: {node}')
            if chunks(props['reg'])[0] != (base,0x1000) or index != WINDOWS.index(base)+2:
                raise ValueError('Unexpected Android WOD window address or index')
            entries.append(dict(node=node,wod=wod,base=base,index=index,dpp=dpp,hcb_node=hcb_node))
    return dict(profile=profile,windows=entries,factory_android_dtb_sha256=digest,
                live_status_offset=STATUS,staged_control_masks=MASKS,scanout=False)

def runtime_state(qmp):
    keys=('status','status-reads','control','control-reads','control-writes','rejected')
    return {hex(base):{key:qmp.execute('qom-get',dict(path=PATH,property=f'{key}-{i}')) for key in keys}
            for i,base in enumerate(WINDOWS)}


LINUX_PROFILE='virtual-linux-wod-window-v1'
LINUX_PATH='/machine/exynos-linux-decon-window'
LINUX_WINDOWS=(0x18c31000,0x1aa31000,0x1aa36000)
LINUX_WODS={'/wod@dpum':(LINUX_WINDOWS[0],0,1,'dpum'),
            '/wod@wod_2nd':(LINUX_WINDOWS[1],2,1,'dpus0'),
            '/wod@dpus0':(LINUX_WINDOWS[2],2,6,'dpus0')}
LINUX_CODE=(0x479a38,56,'35739d03a0413e3eec28427537326e2eda0ca947a1469dcc64589ee25abbba8f')
LINUX_WINCON_CODE=(
    (0x4931a0,1216,'f70609d34facf66a0a62fa47a8bf8641f2b5d95afcbb9e0e301ce8b61036ad5c'),                        
    (0x479a18,32,'f8e783a9d702960e9a9174cf052ba8fb53ea06473734258bdf47e65bf75c34ae'),                           
    (0x479980,72,'b81ec9afe03465dd6fd37c4ffa1b873d0aeba530fb37d5f817c025373b1c5368'),                          
    (0x4799c8,80,'61d9f25a1b6932bffecb2a731f0ff9a5a0bab7263f42b23d1a8a3dfbfaad519c'),                        
    (0x479e10,56,'649fe7a852796037b93aefc603a9f6c9b23d8cdd5feda1e75367a56041e8cadb'),                   
)


def linux_policy(profile, dpp_contract):
    if profile is None:
        return None
    if profile!=LINUX_PROFILE or not dpp_contract:
        raise ValueError('Linux WOD windows require original VM3 DPP')
    dt=factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes()
    if hashlib.sha256(dt).hexdigest()!=dpp_contract['original_linux_dtb_sha256']:
        raise ValueError('Linux WOD/DPP device tree differs')
    raw=original('hcb/hcb.img').read_bytes()
    start=raw.index(b'VDTVM3\0\0')+8
    hcb=parse_dtb(raw[start:start+struct.unpack_from('>I',raw,start+4)[0]])
    nodes=parse_dtb(dt)
    refs={row['phandle']:k for k,row in nodes.items() if 'phandle' in row}
    wods={refs[handle] for (handle,) in struct.iter_unpack('>4s',nodes['/vdpu']['decon-list'])}
    if wods!=LINUX_WODS.keys():
        raise ValueError('VM3 vDPU window owners changed')
    windows=[]
    for wod,(base,decon,index,domain) in LINUX_WODS.items():
        row=nodes[wod]
        name=refs[row['samsung,windows']]
        props=nodes[name]
        grant=f'/vplatform/vl,{domain}/vl,io-memory@0x{base:X}'
        h=hcb[grant]
        resources=tuple((address,0x1000) for address in (base,base+0x20000,base+0x28000))
        if (row['compatible']!=b'samsung,exynos9-wod\0' or one(row['samsung,decon_idx'])!=decon
                or one(props['cell-index'])!=index or 'vl,disabled' in h or props['reg']!=h['reg']
                or chunks(props['reg'])!=resources):
            raise ValueError('VM3 WOD window grant changed: '+name)
        windows.append(dict(base=base,wincon=resources[1][0],node=name,wod=wod,decon=decon,index=index,hcb_node=grant))
    with factory_file('ivi/Image').open('rb') as f:
        for offset,size,digest in (LINUX_CODE,*LINUX_WINCON_CODE):
            f.seek(offset)
            if hashlib.sha256(f.read(size)).hexdigest()!=digest:
                raise ValueError(f'VM3 DECON window consumer changed at {offset:#x}')
    return dict(profile=profile,windows=windows,code=LINUX_CODE,wincon_code=LINUX_WINCON_CODE,
                live_status_offset=STATUS,wincon_update_offset=0x14,wincon_update_mask=1,
                latch='matching native DECON frame boundary',
                staged_control_masks={k:v for k,v in MASKS.items() if k}|{4:0xffff0f03,8:0x0f0f0f0f},scanout=False)


def linux_runtime(qmp):
    keys=('status','status-reads','control','control-reads','control-writes','rejected')
    keys+=('wincon-requests','wincon-pending','wincon-commits','wincon-active-control')
    return {hex(base):{k:qmp.execute('qom-get',dict(path=LINUX_PATH,property=f'{k}-{i}')) for k in keys}
            for i,base in enumerate(LINUX_WINDOWS)}
