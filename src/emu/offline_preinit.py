





import hashlib,struct

BASE=0x400000
ENTRY=BASE+0x1000
DATA=BASE+0x4000


def build(files, *, odm_files=None, odm_links=None, odm_labels=None):
    from keystone import Ks,KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN
    required={'init.rc','vendor-init.rc','services.rc','fstab.exynosauto9'}
    zygote='init.zygote64_32.rc'
    if set(files) not in (required,required|{zygote}):raise ValueError('Unexpected development configuration files')
    if (b'import /oem/offline-dev/init.zygote64_32.rc\n' in files['init.rc'])!=(zygote in files):
        raise ValueError('Unpaired private zygote import and configuration')
    if (odm_files is None)!=(odm_links is None):raise ValueError('Require complete paired private ODM view')
    if odm_files is None:raise ValueError('Vendor init requires the private ODM view')
    if odm_files is not None:
        required_odm={'NOTICE.xml.gz','build.prop','group','passwd','fstab.offline-dev'}
        routing={'offline-keystore','init/offline-keystore.rc'}
        if set(odm_files) not in (required_odm,required_odm|{'vintf/manifest.xml'},required_odm|{'vintf/manifest.xml'}|routing):raise ValueError('Incomplete private ODM files')
        software_routing=routing.issubset(odm_files)
        if software_routing:
            from offline_software_keystore import restore
            restore(odm_files['offline-keystore'][0])
            if odm_files['offline-keystore'][1]!=0o755:raise ValueError('Require executable private keystore')
            expected=(b'service keystore /odm/etc/offline-keystore /data/misc/keystore\n'
                      b'    override\n    class main\n    user keystore\n'
                      b'    group keystore drmrpc readproc log\n'
                      b'    writepid /dev/cpuset/foreground/tasks\n')
            if odm_files['init/offline-keystore.rc']!=(expected,0o644):raise ValueError('Unexpected private keystore service')
        if 'vintf/manifest.xml' in odm_files and odm_files['vintf/manifest.xml']!=(b'<manifest version="2.0" type="device"><hal format="hidl" override="true"><name>android.hardware.keymaster</name><transport>hwbinder</transport></hal></manifest>\n',0o644):
            raise ValueError('Require exact private software-keymaster VINTF override')
        if set(odm_links)!={'app','bin','firmware','framework','lib','lib64','overlay','priv-app','usr'}:raise ValueError('Incomplete preserved ODM links')
        if any(t!='/vendor/odm/'+n for n,t in odm_links.items()):raise ValueError('Unexpected original ODM link')
        paths={'/odm','/odm/etc','/odm/etc/init','/odm/etc/init/offline-vendor.rc'}|{'/odm/'+n for n in odm_links}|{'/odm/etc/'+n for n in odm_files}
        if 'vintf/manifest.xml' in odm_files:paths.add('/odm/etc/vintf')
        if odm_labels is None or set(odm_labels)!=paths:raise ValueError('Require complete original ODM SELinux metadata')
        allowed={'vendor_file','vendor_configs_file','vendor_app_file','vendor_framework_file','vendor_overlay_file'}
        for name,context in odm_labels.items():
            if software_routing and name=='/odm/etc/offline-keystore':
                if context!='u:object_r:keystore_exec:s0':raise ValueError('Require original keystore executable label')
            elif context not in {'u:object_r:'+t+':s0'for t in allowed}:raise ValueError('Unexpected original ODM security label')
    blob=bytearray()
    def data(raw,align=1):
        blob.extend(b'\0'*(-len(blob)%align));address=DATA+len(blob);blob.extend(raw);return address
    def string(text):return data(text.encode('ascii')+b'\0')
    def adr(reg,address):return imm(reg,address)
    def imm(reg,value):
        if not 0<=value<1<<32:raise ValueError('Immediate outside bounded preinit contract')
        return f'movz {reg}, #{value&65535}; movk {reg}, #{value>>16}, lsl #16'
    tmpfs=string('tmpfs');oem=string('/oem');directory=string('/oem/offline-dev');opts=string('mode=0755');init=string('/init');argv=data(struct.pack('<QQ',init,0),8)
    errno=data(b'ret=0x0000000000000000\n',8)
    banner=b'OFFLINE DEVELOPMENT: private temporary config; factory security NOT verified\n'
    banner_at=data(banner)
    failures=[]
    def checked(label):
        failures.append(label);return f'tbnz x0, #63, fail_{label}'
    code=['mov x27, sp','ldr x0, [x27]','add x28, x27, #16','add x28, x28, x0, lsl #3',
          'mov x0, #1',adr('x1',banner_at),imm('x2',len(banner)),'bl write_all']
    code += [adr('x0',tmpfs),adr('x1',oem),adr('x2',tmpfs),'mov x3, #14',adr('x4',opts),'mov x8, #40','svc #0',checked('mount')]
    def mount_odm(readonly=False):
        flags=(5 if readonly else 4)if software_routing else(15 if readonly else 14)
        return [adr('x0',loop),adr('x1',odm),adr('x2',ext4),f'mov x3, #{flags}','mov x4, #0','mov x8, #40','svc #0']
    if odm_files is not None:
        loop=string('/offline-loop0');image=string('/offline-odm.ext4');odm=string('/odm');ext4=string('ext4')
        code += ['mov x0, #-100',adr('x1',loop),imm('x2',0o60600),'mov x3, #1792','mov x8, #33','svc #0',checked('loop_node')]
        for path,reg,label in [(loop,'x25','loop_open'),(image,'x26','image_open')]:
            code += ['mov x0, #-100',adr('x1',path),'mov x2, #2','mov x3, #0','mov x8, #56','svc #0',checked(label),f'mov {reg}, x0']
        code += ['mov x0, x25','mov x1, #19456','mov x2, x26','mov x8, #29','svc #0',checked('loop_bind')]
        code += mount_odm()+[checked('mount_odm')]
    for i,target in enumerate([directory]+([string('/odm/etc')]if odm_files is not None else[])):
        code += ['mov x0, #-100',adr('x1',target),'mov x2, #493','mov x8, #34','svc #0',checked('mkdir'if i==0 else'mkdir_odm')]
    if odm_links is not None:
        for i,(name,target)in enumerate(sorted(odm_links.items())):
            code += [adr('x0',string(target)),'mov x1, #-100',adr('x2',string('/odm/'+name)),'mov x8, #36','svc #0',checked(f'symlink_{i}')]
    if odm_files is not None and 'vintf/manifest.xml' in odm_files:
        code += ['mov x0, #-100',adr('x1',string('/odm/etc/vintf')),'mov x2, #493','mov x8, #34','svc #0',checked('mkdir_vintf')]
    if odm_files is not None:
        code += ['mov x0, #-100',adr('x1',string('/odm/etc/init')),'mov x2, #493','mov x8, #34','svc #0',checked('mkdir_vendor_init')]
    writes={('/odm/etc/init/offline-vendor.rc'if n=='vendor-init.rc'else'/oem/offline-dev/'+n):(b,0o644)for n,b in files.items()}
    if odm_files is not None:writes.update({'/odm/etc/'+n:(b,mode)for n,(b,mode)in odm_files.items()})
    for i,(name,(raw,mode))in enumerate(writes.items()):
        executable=odm_files is not None and software_routing and name=='/odm/etc/offline-keystore'
        if not isinstance(raw,bytes)or not 0<=len(raw)<(512 if executable else 256)*1024 or mode not in((0o755,)if executable else(0o600,0o644)):raise ValueError('Invalid development config size/type/mode')
        path=string(name);payload=data(raw)
        code+=['mov x0, #-100',adr('x1',path),'mov x2, #577',imm('x3',mode),'mov x8, #56','svc #0',checked(f'open_{i}'),'mov x19, x0']
        if raw:code += [adr('x1',payload),imm('x2',len(raw)),'bl write_all']
        code+=['mov x0, x19','mov x8, #57','svc #0',checked(f'close_{i}')]
    if odm_labels is not None:
        attr=string('security.selinux')
        for i,(name,context)in enumerate(sorted(odm_labels.items())):
            value=context.encode('ascii')+b'\0'
            code += [adr('x0',string(name)),adr('x1',attr),adr('x2',data(value)),imm('x3',len(value)),
                     'mov x4, #0','mov x8, #6','svc #0',checked(f'label_{i}')]
                                                                            
                                                                            
                                                                           
        code += [adr('x0',odm),'mov x1, #0','mov x8, #39','svc #0',checked('odm_unmount')]
        code += mount_odm(readonly=True)+[checked('odm_remount')]
        for reg,label in [('x26','image_close'),('x25','loop_close')]:
            code += [f'mov x0, {reg}','mov x8, #57','svc #0',checked(label)]
    ready=b'OFFLINE PREINIT: configuration staged; exec original /init\n';ready_at=data(ready)
    code+=['mov x0, #1',adr('x1',ready_at),imm('x2',len(ready)),'bl write_all',
           adr('x0',init),adr('x1',argv),'mov x2, x28','mov x8, #221','svc #0','b fail_exec']
    code+=['write_all:','mov x9, x0','mov x10, x1','mov x11, x2','write_retry:','mov x0, x9','mov x1, x10','mov x2, x11','mov x8, #64','svc #0',
           'cmn x0, #4','b.eq write_retry','tbnz x0, #63, fail_write','cbz x0, fail_write','cmp x0, x11','b.hi fail_write',
           'add x10, x10, x0','subs x11, x11, x0','b.ne write_retry','ret']
    for label in failures+['exec','write']:
        msg=f'OFFLINE PREINIT: {label} failed; refusing handoff\n'.encode('ascii');a=data(msg)
        code+=[f'fail_{label}:','mov x24, x0',adr('x1',a),imm('x2',len(msg)),'b die']
    code+=['die:','mov x0, #2','mov x8, #64','svc #0',adr('x1',errno),'add x2, x1, #6','mov x3, #16',
           'hex_loop:','lsr x4, x24, #60','cmp x4, #10','add x5, x4, #48','add x6, x4, #87','csel x5, x5, x6, lo',
           'strb w5, [x2], #1','lsl x24, x24, #4','subs x3, x3, #1','b.ne hex_loop',
           'mov x0, #2',f'mov x2, #{len(b"ret=0x0000000000000000\n")}','mov x8, #64','svc #0',
           'mov x0, #100','mov x8, #94','svc #0','b die']
    asm='\n'.join(code)
    native,count=Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN).asm(asm,addr=ENTRY,as_bytes=True)
    if len(native)>DATA-ENTRY or len(blob)>512*1024:raise ValueError('Development preinit exceeds bounded layout')
    ident=b'\x7fELF\x02\x01\x01'+b'\0'*9
    hdr=struct.pack('<16sHHIQQQIHHHHHH',ident,2,183,1,ENTRY,64,0,0,64,56,3,0,0,0)
    ph=struct.pack('<IIQQQQQQ',1,5,0,BASE,BASE,4096+len(native),4096+len(native),4096)
    ph+=struct.pack('<IIQQQQQQ',1,6,DATA-BASE,DATA,DATA,len(blob),len(blob),4096)
    ph+=struct.pack('<IIQQQQQQ',0x6474e551,6,0,0,0,0,0,16)
    elf=(hdr+ph).ljust(4096,b'\0')+native
    elf=elf.ljust(DATA-BASE,b'\0')+blob
    return bytes(elf),{'sha256':hashlib.sha256(elf).hexdigest(),'code_bytes':len(native),'data_bytes':len(blob),
      'configuration_paths':list(writes),'vendor_init_script':'/odm/etc/init/offline-vendor.rc',
      'vendor_init_context':'u:r:vendor_init:s0','selinux_policy_changed':False,
      'asm':asm,'syscalls':{'mount':40,'umount2':39,'mknodat':33,'ioctl':29,'mkdirat':34,'symlinkat':36,'lsetxattr':6,'openat':56,'write':64,'close':57,'execve':221,'exit_group':94},
      'scope':'Explicit offline preinit; no libc, driver emulation, entropy injection, secure-return patch or factory security claim'}
