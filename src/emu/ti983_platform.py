
import hashlib
import struct

QOM = '/machine/peripheral/ti983-abt'
OPTION = 'exynos-auto9.ti983-abt,id=ti983-abt,bus=i2c17,address=0x0c,virtual-profile=1'
KEYS = ('rail-mask', 'powered', 'register-reads', 'register-writes', 'rejected-accesses', 'apb-requests')
BASE = 0xffffffc010080000
CODE = (
    (0xffffffc010589c58, 248, 'ad6b582ba44c04575447605b23659b9a795d27642dfb8511d7124190303adaa2'),
    (0xffffffc010589d50, 216, '993e15ea18417e5a2d29dedaa3d35ff4900a8fda676a6bfb7f40a557e924a476'),
    (0xffffffc010589e28, 264, '22c44752d3ca24e0f46b2d5424ec8fb47ff2bf78f730ce93a8db3d26b954907e'),
    (0xffffffc01058b070, 504, 'f3837c150f9fc8baf906d03bff36ce6569b7356f0981d623a6349845fa8b03fc'),
)


def policy(enabled):
    if not enabled:
        return None
    from vlm_platform import factory_file, original, parse_dtb

    hcb = original('hcb/hcb.img').read_bytes()
    for vmid in (2, 3, 4):
        start = hcb.index(f'VDTVM{vmid}'.encode() + b'\0\0') + 8
        end = start + struct.unpack_from('>I', hcb, start+4)[0]
        nodes = parse_dtb(hcb[start:end])
        row = nodes['/vplatform/vl,usi08_sub_i2c/vl,usi08_sub_i2c@mem']
        if (row['reg'] != struct.pack('>QIQI', 0x10821014, 4, 0x10950000, 4096) or
                ('vl,disabled' not in row) != (vmid == 2)):
            raise ValueError('Original physical I2C17 ownership changed')

    dt = parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())
    controller = '/pinctrl@10830000'
    groups = tuple(n.rsplit('/', 1)[-1] for n, props in dt.items()
                   if n.startswith(controller+'/') and n.count('/') == 2 and 'gpio-controller' in props)
    route = '/i2c-adapter@10950000'
    if (dt['/hsi2c@10950000']['status'] != b'disabled\0' or dt[route]['status'] != b'okay\0' or
            dt[route]['vl,vlink'] != b'vi2c@10950000\0' or
            groups != ('gpp3', 'gpp4', 'gpp5', 'gpg1', 'gpg2', 'gpg3')):
        raise ValueError('Original VM3 indirect TI983 route changed')
    child = dt[route+'/ti983@0c']
    if (child['reg'] != struct.pack('>I', 12) or child['compatible'] != b'lge,ti983\0' or
            child['status'] != b'okay\0'):
        raise ValueError('Original TI983 device selection changed')
    pins = {}
    for name, group, pin in (('pdb', 'gpg2', 1), ('1v8', 'gpg2', 6), ('1v1', 'gpg3', 2)):
        if child[f'lge,ti983-{name}-gpio'] != dt[f'{controller}/{group}']['phandle']+struct.pack('>II', pin, 0):
            raise ValueError('Original TI983 power GPIO selection changed')
        pins[name] = groups.index(group)*8+pin
    with factory_file('ivi/Image').open('rb') as image:
        for address, count, digest in CODE:
            image.seek(address-BASE)
            if hashlib.sha256(image.read(count)).hexdigest() != digest:
                raise ValueError(f'Original TI983 transaction code changed at {address:#x}')
    return dict(source_vmid=3, physical_owner=2, physical_base=0x10950000, address=12,
                bus='i2c17', gpio4_outputs=pins, virtual_powerup_ns=100000,
                scope='OEM transaction subset, virtual disconnected endpoint; no training, remote peer or video')


def runtime_state(qmp):
    return {key: qmp.execute('qom-get', {'path': QOM, 'property': key}) for key in KEYS}


def carrier_has_device(executable, mingw):

    import os
    import subprocess

    env = dict(os.environ, PATH=str(mingw)+os.pathsep+os.environ.get('PATH', ''))
    result = subprocess.run([str(executable), '-device', 'help'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, check=True, timeout=10,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return b'name "exynos-auto9.ti983-abt"' in result.stdout
