
import hashlib
import struct


PAGE=0xe5d02000
GUEST_CACHE=0xffffffc01129e790
KERNEL_BASE=0xffffffc010080000
FALLBACK=(0xffffffc010b0fa80,32,'172cc34ee3189ea2469f594a8d7f67f694a9e0dd842903b5cf89dbeae269ef19')
CONSUMERS=(
    (0xffffffc010581280,0x30,'a86d14254afcdb231b62178fbc4ee8a4da9eb7e7b2fc9d4c699f55d2a178d549'),
    (0xffffffc0105812e0,0x70,'bdbb2070e2cefcf85182af42204093378493da5c831f982851ed7308aab1e209'),
    (0xffffffc010581b98,0x608,'407b5586b543d41f5d4d359fd4a147ea2a64ba01458df57c504172453b44d753'),
)
FACTORY_SCRIPTS={
    '/lge/app_ro/post_init/post_init_main.sh':'89adf770ae874b7d336e5790ed1c1e9ed1ee51dd2ec91db101d836b467d1681e',
    '/lge/app_ro/post_init/data/hmi.sh':'e4f91342935a22c09f04f906c5afa929b0434aab2a64b321c916c39ff769591b',
    '/lge/app_ro/bin/vw_fsck_rw_partitions.sh':'d3b967feb86563a82621978149fea65b38e6484007a6f3899d6f3f6dfc648cf9',
}


def contract():
    from dissect.extfs import ExtFS
    from linux_weston_platform import ROOT_MEMBER
    from vlm_platform import factory_file,original,parse_dtb
    hcb=original('hcb/hcb.img').read_bytes()
    for vmid in (2,3,4):
        start=hcb.index(f'VDTVM{vmid}'.encode()+bytes(2))+8
        tree=parse_dtb(hcb[start:start+struct.unpack_from('>I',hcb,start+4)[0]])
        grants=[(base,size) for node,row in tree.items()
                if node.startswith('/vplatform/vl,io-memory@') and 'vl,disabled' not in row
                for base,size in struct.iter_unpack('>QI',row.get('reg',b''))
                if base<PAGE+4096 and PAGE<base+size]
        if grants!=([(PAGE,8192)] if vmid==3 else []):
            raise ValueError('Original VM3 MCU page ownership changed')
    dt=parse_dtb(factory_file('ivi/icas3cn-gp_b0_zero.dtb').read_bytes())
    mcu=dt['/reserved-memory/lk_data/mcu_data']
    if (mcu['reg']!=struct.pack('>QI',PAGE,4096) or
            dt['/common_gpio_default']['memory-region'][:4]!=mcu['phandle']):
        raise ValueError('Original VM3 MCU DT consumer changed')
    with factory_file('ivi/Image').open('rb') as stream:
        for address,size,expected in (*CONSUMERS,FALLBACK):
            stream.seek(address-KERNEL_BASE)
            raw=stream.read(size)
            if hashlib.sha256(raw).hexdigest()!=expected:
                raise ValueError(f'Original VM3 MCU bytes changed at {address:#x}')
    with factory_file(ROOT_MEMBER).open('rb') as stream:
        fs=ExtFS(stream)
        for path,expected in FACTORY_SCRIPTS.items():
            if hashlib.sha256(fs.get(path).open().read()).hexdigest()!=expected:
                raise ValueError('Original ICAS3 region selection changed: '+path)
    if raw[:3]!=b'\x00\x65\x00' or raw[4]!=0:
        raise ValueError('Original VM3 fallback variant or market changed')
                                                                       
                                                                               
    data=bytearray(raw)
    data[2]=4
    data[4]=4
    return dict(profile='virtual-icas3cn-gp',vmid=3,page=PAGE,platform=4,market=4,variant=0x65,
                data_offset=1,data_hex=data.hex(),guest_cache=hex(GUEST_CACHE),
                scope='OEM default record with ICAS3CN virtual board platform and market; no vehicle EEPROM')


def loader_options(selected):
    data=bytes.fromhex(selected['data_hex'])
    return [arg for i in range(0,len(data),8) for arg in
            ('-device',f'loader,addr={PAGE+1+i:#x},data={int.from_bytes(data[i:i+8],"little"):#x},data-len=8')]


def guest_identity(word):
    data=word.to_bytes(8,'little')
    return dict(platform=data[3],market=data[5],variant=int.from_bytes(data[1:3],'big'))
