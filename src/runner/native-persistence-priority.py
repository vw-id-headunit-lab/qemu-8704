
from pathlib import Path
import hashlib

from dissect.extfs import ExtFS

PERSISTENCE_START_GRACE_SECONDS=60

STOCK_FILES={
    '/lib/systemd/system/persistence-app.service':
        '2f15e8a1fd47ccc6bcac9b7c903f16791378b34580f73fd734cb92323375c715',
    '/lge/app_ro/hmi/persistence-app':
        'd8d62ee1a493e5fad6886b8003e8f0c777769feabd0ccff7648e7f92f459668a',
    '/lib/systemd/system/trace.service':
        '37b887a2bc6d9714c0abe77aa1317c4cc7fe815f4aca0620427ba75979bcfe6d',
    '/lib/systemd/system/multi-user.target':
        'e57f384474fa52248f912b11cce30dea793db7fa26a6cafea7213716eea18068',
}
STOCK_LINKS={
    '/etc/systemd/system/default.target':'/lib/systemd/system/multi-user.target',
    '/etc/systemd/system/multi-user.target.wants/persistence-app.service':'/lib/systemd/system/persistence-app.service',
}
POST_INIT_READY_FILES={
    '/lib/systemd/system/post-init.service':
        '4f21ae6dbf1883c15ac777728e6a3a4fd61957eafaef8c9971d4bce7bccc739c',
    '/lib/systemd/system/systemd-tmpfiles-setup.service':
        '4001af71ea0ee5dcb26ebdaafd5fec37645cf481a46f32f6c128c735fff99420',
    '/lge/app_ro/post_init/post_init_main.sh':
        '89adf770ae874b7d336e5790ed1c1e9ed1ee51dd2ec91db101d836b467d1681e',
}
POST_INIT_READY_LINKS={
    '/lib/systemd/system/sysinit.target.wants/systemd-tmpfiles-setup.service':'../systemd-tmpfiles-setup.service',
    '/etc/systemd/system/basic.target.wants/post-init.service':'/lib/systemd/system/post-init.service',
}


def validate(root, *, start_grace=False, post_init_after_tmpfiles=False):
    with Path(root).open('rb') as image:
        fs=ExtFS(image)
        files=STOCK_FILES | (POST_INIT_READY_FILES if post_init_after_tmpfiles else {})
        links=STOCK_LINKS | (POST_INIT_READY_LINKS if post_init_after_tmpfiles else {})
        for path,want in files.items():
            with fs.get(path).open() as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=want:
                    raise ValueError('Original persistence startup input differs: '+path)
        for path,want in links.items():
            node=fs.get(path)
            if node.filetype!=0xa000 or node.link!=want:
                raise ValueError('Original persistence activation link differs: '+path)
        if start_grace:
                                                                            
                                                      
            for base in ('/lib/systemd/system','/etc/systemd/system'):
                for unit in ('post-init','persistence-app'):
                    path=f'{base}/{unit}.service.d'
                    try:
                        fs.get(path)
                    except FileNotFoundError:
                        continue
                    raise ValueError('Unexpected factory unit drop-in: '+path)


def stage(post_init, *, before_hmi=False, parallel_sysinit=False, start_grace=False,
          post_init_after_tmpfiles=False, post_init_nice=None):

    prefix="p=/run/systemd/system/post-init.service.d; mkdir -p $p&&printf '"
    suffix="'>$p/90-native-priority.conf; "
    if not post_init.startswith(prefix) or not post_init.endswith(suffix):
        raise ValueError('Unexpected original post-init priority staging shape')
    unit=post_init[len(prefix):-len(suffix)]
    if not unit.startswith(r'[Unit]\n') or "'" in unit or r'\n[Service]' in unit:
        raise ValueError('Expected ordering-only unit drop-in')
    if start_grace and (not parallel_sysinit or before_hmi):
        raise ValueError('Persistence start grace requires the parallel original start order')
    if post_init_after_tmpfiles and not start_grace:
        raise ValueError('Early original post-init requires persistence start grace')
    if post_init_nice is not None and (type(post_init_nice) is not int or post_init_nice not in (-5,-10,-15,-19,-20)):
        raise ValueError('Unsupported original post-init Nice value')
    if post_init_nice is not None and not (parallel_sysinit and post_init_after_tmpfiles and not before_hmi):
        raise ValueError('Post-init scheduling comparison requires the existing early policy')
                                                                           
                                                                      
                                                                               
    if parallel_sysinit:
        if before_hmi or unit.count('After=sysinit.target ')!=1 or '%' in unit:
            raise ValueError('Parallel sysinit requires only the original persistence timing comparison')
                                                                              
                                                                                
                                                                             
        fmt=unit.replace('After=sysinit.target ','After=%s ',1)
                                                                             
                                                                            
                                                                    
        name='9.conf' if start_grace else '90-native-priority.conf'
        tail=(rf'[Service]\nTimeoutStartSec={PERSISTENCE_START_GRACE_SECONDS}\n' if start_grace else '')
                                                                         
                                                                              
        order='systemd-tmpfiles-setup.service' if post_init_after_tmpfiles else 'sysinit.target'
                                                                        
                                                                               
        arg=(r" ${x:+'\n[Service]\nNice="+str(post_init_nice)+"'}" if post_init_nice is not None else '')
        if post_init_nice is not None:
            fmt+='%b'
        return (f"p=/run/systemd/system;f={name};x={order};"
                "for n in post-init persistence-app;do d=$p/$n.service.d;"
                "mkdir -p $d&&printf '"+fmt+"' $x"+arg+">$d/$f;x=;done;"
                "printf 'RequiresMountsFor=/data\\n"+tail+"'>>$d/$f;"+
                ('' if start_grace else ' '))
    if before_hmi:
                                                                         
                                                                          
        return ("p=/run/systemd/system;f=90-native-priority.conf;for n in post-init persistence-app;do "
                "d=$p/$n.service.d;mkdir -p $d&&printf '"+unit+
                "'>$d/$f;done;printf 'RequiresMountsFor=/data\\nBefore=vw-hmi.service\\n'>>$d/$f; ")
    return ("p=/run/systemd/system;for n in post-init persistence-app;do "
            "d=$p/$n.service.d;mkdir -p $d&&printf '"+unit+
            "'>$d/90-native-priority.conf;done;printf 'RequiresMountsFor=/data\\n'>>$d/90-native-priority.conf; ")


def compact_shared_systemd_root(script):

    root='/run/systemd/system'
    root_decl='p='+root+';'
                                                                            
                                                                           
    replacements=(
        (root_decl,''),
        ('h='+root+'/vw-hmi.service.d;', 'h=$p/vw-hmi.service.d;'),
        ('d='+root+'/systemd-udev-trigger.service.d;', 'd=$p/systemd-udev-trigger.service.d;'),
    )
    for old,new in replacements:
        if script.count(old)!=1:
            raise ValueError('Original timing comparison staging shape changed: '+old)
        script=script.replace(old,new,1)
                                                                       
                                                                         
    old='>$d/../b29.service'
    if script.count(old)>1:
        raise ValueError('Ambiguous trace backup service staging')
    return root_decl+script.replace(old,'>$p/b29.service',1)


def defer_hmi_precompile(script, vm_log_path):

    old='-XX:LogFile='+vm_log_path+r'\047\n'
    if script.count(old)!=1:
        raise ValueError('Original precompile comparison VM logging shape changed')
    return compact_shared_systemd_root(script.replace(
        old,'-XX:LogFile='+vm_log_path+r' -XX:-PrecompilePreloadedClasses\047\n',1))
