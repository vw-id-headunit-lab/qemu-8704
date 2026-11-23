
import hashlib
import json
import struct

PROFILE = 'virtual-ufs-vh-ro-v1'
PROPERTIES = ('host-index', 'transport-enabled', 'irq-level', 'irq-assertions',
              'dma-reads', 'dma-writes', 'dma-rejected', 'host0-last-vid',
              'host0-invalid', 'host0-s2-denied', 'vendor-rejected')
ANDROID_CODE=((0x8760cc,364,'9757d656f845796af548c24e8e061850f0fbb65f3714c72a65d12faebab05547'),
              (0x878ea4,400,'a21dc1cb16d92b47f473c486af3f77f2cf501575a250bfc347ef937735952c3d'),
              (0x8932dc,88,'f78cdc9d87fd4c37fdd0b7f11199ee60d6a97eeb5b4b1cb908614231c624d726'))
LINUX_CODE=((0x53e510,112,'d5455198f2b2df86a21219be2691ec650ca658d4a91258176ba26e53f4a6f9ea'),
            (0x53df68,88,'fe98cafeb4eebe422b11d834a28263cf35a356a989f7866ae3421ffe680b0207'))
HMI_PREDECESSOR='ufs-vh1-hmi-predecessor'


def verify_android(kernel):
    for offset,size,digest in ANDROID_CODE:
        if hashlib.sha256(kernel[offset:offset+size]).hexdigest()!=digest:
            raise ValueError('Android UFS link/PRDT consumer changed')


def verify_linux(kernel_path):
    with kernel_path.open('rb') as f:
        for offset,size,digest in LINUX_CODE:
            f.seek(offset)
            if hashlib.sha256(f.read(size)).hexdigest()!=digest:
                raise ValueError('Linux UFS interrupt/PRDT consumer changed')


def policy(profile, mailbox, lun0, android_overlay=False):
    if profile is None:
        return None
    if profile != PROFILE or not mailbox or lun0 is None:
        raise ValueError('VH transport needs the original mailbox and verified derived LUN0')
    return dict(profile=profile, hosts=mailbox['hosts'][1:],
                transport='Independent upstream HCI controller and S2MPU walker per VH',
                required_vid=[3, 4], backend=('VM3 read-only base; VM4 private overlay, separate from PH' if android_overlay
                    else 'read-only derived base, not PH write overlay'),
                android_private_overlay=android_overlay,
                original_hcb_sha256=mailbox['original_hcb_sha256'],
                original_mailbox_consumer=mailbox['android_consumer'],
                original_android_code=ANDROID_CODE,original_linux_code=LINUX_CODE,
                hci_version='0x210 compatibility profile',prdt_stride=128,inline_fmp=False,
                exact_hardware=False, factory_lun_visibility_verified=False)


def node_name(i, android_overlay=False, linux_data=False):
    if i==1 and linux_data:
        return 'ufs-vh1-private-lun0'
    if i==2 and android_overlay:
        return 'ufs-vh2-private-lun0'
    return f'ufs-vh{i}-ro-lun0'


def devices(android_overlay=None,linux_hmi_overlay=None,linux_data=None,linux_hmi_predecessor=None):
    if linux_data is not None and linux_hmi_overlay is None:
        raise ValueError('Writable VM3 layer must back onto the immutable OEM Linux media')
    if linux_hmi_predecessor is not None and (
            linux_hmi_overlay is None or linux_hmi_predecessor==linux_hmi_overlay):
        raise ValueError('A VW install delta needs separate verified media and its predecessor')
    options = []
    for i in (1, 2):
        name = node_name(i,android_overlay is not None,linux_data is not None)
        if i==1 and linux_hmi_overlay is not None:
            hmi_backing='ufs-base'
            if linux_hmi_predecessor is not None:
                                                                           
                                                                                
                options += ['-blockdev',json.dumps({'driver':'file','filename':str(linux_hmi_predecessor),
                                                   'node-name':HMI_PREDECESSOR+'-file','read-only':True}),
                            '-blockdev',json.dumps({'driver':'qcow2','file':HMI_PREDECESSOR+'-file',
                                                   'backing':'ufs-base','node-name':HMI_PREDECESSOR,'read-only':True})]
                hmi_backing=HMI_PREDECESSOR
            options += ['-blockdev',json.dumps({'driver':'file','filename':str(linux_hmi_overlay),
                                               'node-name':'ufs-vh1-hmi-file','read-only':True})]
            node={'driver':'qcow2','file':'ufs-vh1-hmi-file','backing':hmi_backing,
                  'node-name':node_name(1),'read-only':True}
            if linux_data is not None:
                options += ['-blockdev',json.dumps(node),
                            '-blockdev',json.dumps({'driver':'file','filename':str(linux_data),
                                                    'node-name':'ufs-vh1-private-file'})]
                node={'driver':'qcow2','file':'ufs-vh1-private-file','backing':node_name(1),
                      'node-name':name}
        elif i==2 and android_overlay is not None:
                                                                          
            options += ['-blockdev', json.dumps({'driver':'file','filename':str(android_overlay),
                                                 'node-name':'ufs-vh2-private-file'})]
            node = {'driver':'qcow2','file':'ufs-vh2-private-file','backing':'ufs-base','node-name':name}
        else:
            node = {'driver':'raw','file':'ufs-base','node-name':name,'read-only':True}
        options += ['-blockdev',json.dumps(node),
                    '-device',f'ufs-lu,drive={name},bus=auto9-ufs-vh{i}-luns,lun=0']
    return options


def path(i):
    return f'/machine/exynos-ufs-vh{i}'


def enable(qmp,android_overlay=False,linux_hmi_overlay=False,linux_data=False,linux_hmi_predecessor=False):
    if linux_hmi_predecessor and not linux_hmi_overlay:
        raise ValueError('A VW predecessor is only valid under an install delta')
    for i in (1, 2):
        qmp.execute('qom-set', {'path': path(i), 'property': 'transport-enabled', 'value': True})
        qmp.execute('qom-set', {'path': path(i), 'property': 'virtual-plain-prdt128-v1', 'value': True})
    expected = {'ufs-base':True, 'ufs-base-file':True,
                node_name(1):True,node_name(2,android_overlay):not android_overlay}
    if linux_hmi_overlay:
        expected['ufs-vh1-hmi-file']=True
    if linux_hmi_predecessor:
        expected[HMI_PREDECESSOR]=True
        expected[HMI_PREDECESSOR+'-file']=True
    if linux_data:
        expected[node_name(1,linux_data=True)]=False
        expected['ufs-vh1-private-file']=False
    nodes = {r['node-name']:r for r in qmp.execute('query-named-block-nodes')
             if r.get('node-name') in expected}
    if {name:node['ro'] for name,node in nodes.items()}!=expected:
        raise ValueError('Unexpected native VH base/overlay write permissions')
    if linux_hmi_overlay and nodes[node_name(1)]['drv']!='qcow2':
        raise ValueError('Linux /vw must be a separate read-only qcow2')
    if linux_data and nodes[node_name(1,linux_data=True)]['drv']!='qcow2':
        raise ValueError('VM3 /data writes must go to a private qcow2')
    if linux_hmi_predecessor:
        depth={HMI_PREDECESSOR:1,node_name(1):2}
        if linux_data:
            depth[node_name(1,linux_data=True)]=3
        if any(nodes[name]['drv']!='qcow2' or nodes[name].get('backing_file_depth')!=n
               for name,n in depth.items()):
            raise ValueError('The live VM3 block graph is missing the immutable VW predecessor')
    if android_overlay and nodes[node_name(2,True)]['drv']!='qcow2':
        raise ValueError('Android VH writes must go to a private qcow2')
    return nodes


def counters(qmp, i):
    return {name: qmp.execute('qom-get', {'path': path(i), 'property': name})
            for name in PROPERTIES}


def runtime(qmp, output, backing=None, android_overlay=False, linux_hmi_overlay=None, linux_data=False):
    from ufs_platform import observe_slot0, verify_completed_read
    out = {}
    stats = {r.get('node-name'): r for r in qmp.execute('query-blockstats')}
    for i in (1, 2):
        base = 0x17e00000 + i * 0x10000
                                                                          
        capture = output / f'ufs-vh{i}-standard-hci.bin'
        qmp.execute('pmemsave', {'val': base, 'size': 0x80, 'filename': str(capture)})
        raw = capture.read_bytes()
        words = struct.unpack('<32I', raw)
        out[f'vh{i}'] = dict(hci={k: words[o // 4] for k, o in
            {'cap': 0, 'is': 0x20, 'ie': 0x24, 'hcs': 0x30, 'hce': 0x34,
             'utrl': 0x50, 'utrl_high': 0x54, 'doorbell': 0x58, 'run': 0x60}.items()},
            qom=counters(qmp, i), blockstats=stats.get(node_name(i,android_overlay,linux_data)))
        if backing is not None and words[0x50//4]:
            directory = output / f'ufs-vh{i}-slots'
            directory.mkdir()
            active = [j for j in range((words[0]&31)+1) if words[0x58//4] & (1<<j)]
                                                                            
            capture_slots = sorted(set([0,1,(words[0]&31)-2,words[0]&31]+active))
            result = [observe_slot0(qmp,directory,raw,True,128,j) for j in capture_slots if j>=0]
            out[f'vh{i}']['paused_slots'] = result
            if (i==2 and android_overlay) or (i==1 and linux_data):
                out[f'vh{i}']['block_read_matches_scope'] = 'Private overlay is mutable; no comparison against its immutable parent'
            else:
                expected=backing
                if i==1 and linux_hmi_overlay:
                    from linux_hmi_overlay import expected_reader
                    expected=expected_reader(backing,linux_hmi_overlay)
                out[f'vh{i}']['block_read_matches'] = [verify_completed_read(qmp,directory,expected,r) for r in result]
    return out

