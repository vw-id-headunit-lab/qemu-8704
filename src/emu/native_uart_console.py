
import hashlib
from linux_early_uart import COMMAND

KERNEL_SHA='c2a246bbf7c85e17325678262c1db535348038f6ed1b1ba06b0cd722db3e3109'
CONSOLE_MODE='/sys/class/tty/ttySAC0/device/console_mode'
ABI=(
    ('exynos_serial_tx_chars',0xffffffc01049f568,0x2e8,'796b2a7cbf2ec2fc3c372e5a3406dc7002869df4604c6bf42df88e1c19aaf810'),
    ('store_console_mode',0xffffffc010a841a4,0x108,'086a0733fc3d4e000fe602686342046d751abdc986cf19791fd2326a5cee32e9'),
    ('exynos_uart_mode_init',0xffffffc01049cd18,0x44,'bb099cbb58c7dad3b3a492d81c7b7d6133d87e79f61325a208da595198c716a5'),
    ('__uart_start.isra.0',0xffffffc010493e78,0x44,'b8f9c3932c6f3aef1230f7576fdfad31553b1fc422779fe3d8e368ef041880b8'),
)

def validate(root):
    from vlm_platform import factory_file
    from linux_local_shell import validate_factory
    from dissect.extfs import ExtFS
    validate_factory(root)
    image=factory_file('ivi/Image')
    with image.open('rb') as file:
        if hashlib.file_digest(file,'sha256').hexdigest()!=KERNEL_SHA:
            raise ValueError('Original console policy kernel changed')
        for name,address,size,digest in ABI:
            file.seek(address-0xffffffc010080000)
            if hashlib.sha256(file.read(size)).hexdigest()!=digest:
                raise ValueError('Original console owner API changed: '+name)
    with root.open('rb') as disk:
        fs=ExtFS(disk)
        for path in ('/bin/stty','/bin/sleep'):
            node=fs.get(path)
            with node.open() as file:
                if node.filetype!=0xa000 or file.read(64)!=b'/bin/busybox.nosuid':
                    raise ValueError('Original staging applet changed: '+path)
    return dict(kernel_sha256=KERNEL_SHA,terminal='/dev/ttySAC0',console_mode_path=CONSOLE_MODE,
                console_mode=0,termios='original stty sane; preserve baud',uid=65534,
                kernel_owner_rejection_preserved=True,interactive_shell_verified=False,
                scope='Explicit local VM boot policy, not stock cold-boot policy or a vehicle change')

def stage_script():
                                                                               
                                                                         
                                                                           
                                                                          
    return ("umask 077;if ln -s /dev/null /run/systemd/system/serial-getty@ttySAC0.service;then "
            "(t=/dev/ttySAC0;until [ -c $t ];do /bin/sleep 1;done;echo 0 >"+CONSOLE_MODE+
            "&&/bin/stty -F $t sane&&PATH=/usr/bin:/bin HOME=/nonexistent TERM=dumb "
            "PS1='8704-vm3$\\n' "+' '.join(COMMAND)+" <$t>$t 2>&1)&fi;exec /sbin/init")

def compact_staging(script):
                                                                              
                                                                              
    marker="then echo '<5>OEM coldplug staged';"
    if script.count(marker)!=1:
        raise ValueError('Expected the current coldplug staging log exactly once')
    return script.replace(marker,'then :;',1)


def configure_usi04():

    import linux_early_uart as early
    import linux_local_shell as local
    from linux_uart_platform import policy, PROFILE
    if getattr(early,'_native_usi04_installed',False):
        return
    if early.DIRECT_INDEX!=3:
        raise ValueError('Unexpected prior native UART policy')
    contract=policy(PROFILE,True)
    rows=[x for x in contract['ports'] if x['index']==4]
    if len(rows)!=1 or any(rows[0][k]!=x for k,x in
            (('base',0x10380000),('spi',353),('fifo',64))):
        raise ValueError('Original VM3 UART4 grant differs')
    original_serial=early.direct_serial
    early.DIRECT_INDEX=4
    early.DIRECT_BASE=0x10380000
    early.DIRECT_SPI=353
    early.DIRECT_TTY='/dev/ttySAC4'
    early.DIRECT_CHR='oem-linux-usi04'
    early.DIRECT_SOURCE='dedicated-linux-usi04'
    early.DIRECT_CAPTURE='linux-usi04-console-rx.bin'
    local.DIRECT_TTY=early.DIRECT_TTY
    def direct_serial(session,port,profile):
        args,detail=original_serial(session,port,profile)
                                                                         
        args[1]=args[1].replace('linux-usi03-uart.bin','linux-usi04-uart.bin')
        return args,detail
    early.direct_serial=direct_serial
    early._native_usi04_installed=True


def configure_session(session):

    import json
    from pathlib import Path
    import linux_early_uart as early
    session=Path(session).resolve(strict=True)
    path=session/early.DIRECT_META
    if not path.is_file():
        if early.DIRECT_INDEX!=3:
            raise ValueError('Mixed native UART sessions in one page')
        return
    meta=json.loads(path.read_bytes())
    if meta.get('serial_index')==4:
        boot=json.loads((session/'linux-initcall-dtb.json').read_bytes())
        if (meta.get('terminal')!='/dev/ttySAC4' or
                boot.get('local_linux_shell_tty')!='/dev/ttySAC4' or
                boot.get('local_linux_shell_mode')!='early-usi04-drop-privileges'):
            raise ValueError('Incomplete isolated UART4 boot policy')
        configure_usi04()
    elif early.DIRECT_INDEX!=3:
        raise ValueError('Mixed native UART sessions in one page')


if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Verify the selected original Linux UART shell')
    parser.add_argument('session',type=Path)
    parser.add_argument('--seconds',type=int,default=95)
    parser.add_argument('--guest',choices=('linux','android'),default='linux')
    args=parser.parse_args()
    if not 1<=args.seconds<=7200:parser.error('--seconds must be 1..7200')
    configure_session(args.session)
    if args.guest=='linux':
        from linux_local_shell import verify_live
    else:
        from android_local_shell import verify_live
    proof=verify_live(args.session,args.seconds)
    print(json.dumps(proof,ensure_ascii=True))
    raise SystemExit(0 if proof['command_execution_verified'] else 1)
