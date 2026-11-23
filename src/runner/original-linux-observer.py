
from collections import deque
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import runpy
import sys

from linux_pid1_context import LINUX_SHA, capture_task_stack

TASK_WATCH = {'init','systemd','systemd-journal','systemd-udevd','weston',
              'avahi-daemon','systemctl','rngd','agetty','login','bash.bash','capsh','setsid.util-lin','sh','MXNavi','mount','dbus-daemon','post_init_main.',
              'runNavi-al.sh','runNavi.sh','prework_naviengine','dummy-service','runHMI.sh','hmiroot.sh','displaymgr','tdmd','JAVA','java','tz_init_monitor','cp','tar','sync',
              'trace-daemon','trace-system-se','vw_fsck_rw_part','fsck.ext4','e2fsck','persistence-app'}
PERSIST_PRIORITY='/run/systemd/system/persistence-app.service.d/90-native-priority.conf'
PERSIST_GRACE='/run/systemd/system/persistence-app.service.d/9.conf'
POST_INIT_GRACE='/run/systemd/system/post-init.service.d/9.conf'
TMPFS_LOGS={'/run/weston.txt':'linux-weston-native-log.txt',
            '/run/systemd/system/vw-hmi.service.d/90.conf':'linux-hmi-order.conf',
            '/tmp/post_init_done.flag':'linux-post-init-native-log.txt',
            POST_INIT_GRACE:'linux-post-init-order.conf',
            PERSIST_PRIORITY:'linux-persistence-priority.conf',
            PERSIST_GRACE:'linux-persistence-start-grace.conf',
            '/run/systemd/system/b29.service':'linux-trace-volume.service',
            '/run/systemd/system/systemd-udev-trigger.service.d/9.conf':'linux-trace-volume-coldplug.conf'}

WESTON_LOG_ABI=(
    ('shmem_file_read_iter',0xffffffc0101a00d0,0xc0,'bbdaf4efe8ea2285aac0254bcd35596293a77846aee80815ea9183c7805ca058'),
    ('find_get_entry',0xffffffc010184658,0x38,'cce9906d27ba779c4623fd137376cbcf45bdcdffe5ded5847c53f966b5e2348c'),
    ('xas_start',0xffffffc010a75bc8,0x94,'15bf0c2cf534737193afd405b488f17679b13cd3ae8d5e532d5e4b295f518ec1'),
    ('xas_load',0xffffffc010a75c60,0x90,'771e411ca58cc12c6b47534547ec6785447ea34beeffe6576b802613ff310d08'),
    ('shmem_add_to_page_cache',0xffffffc01019cee0,0x290,'59effbf338519331381abc92ea20e054254061e7f62047f7b50af4d249620271'),
    ('copy_page_to_iter',0xffffffc0103bd078,0xf4,'5524036aa3aa6ff50f3d8739568f44bedc1a0bd54cae36d8e2dc7ec132779487'),
)
GETTY_RX_ABI=(
    ('__fget',0xffffffc01022d1c0,0x94,'a646905c5774573c508ad78ea38466883aa9109bea06040eb6d07eed6bbbd634'),
    ('tty_read',0xffffffc01046de28,0x94,'21c9093efb2c66aaa7b6cef462ff42b2a3b8824f653fd6aa9d2e502d7d7c5c32'),
    ('n_tty_read',0xffffffc010473a70,0x194,'1db418c3cc302c9670764c0bd159679a47d7394a40da543d1497005b75b61ea9'),
    ('n_tty_receive_char_special',0xffffffc010475108,0x100,'1b026250107941c50ca98b6296377340a126f0e08b4533e64f2c24ae223cd616'),
    ('copy_from_read_buf',0xffffffc010473810,0xe0,'2abc4c3649c5b511ab7b34b51d466135c93444cd93ad74faf844806d7b44f182'),
)
UART_TX_ABI=(
    ('s3c24xx_serial_request_port',0xffffffc010498a30,0x40,'0bca152723279ae45bccf804d5b7727c28e248b7ffbaa58b25b302aa06e0d8d1'),
    ('uart_write',0xffffffc010496bd0,0x268,'2575b09273544ad1310387e0cd44ba0d707e20004101322c64172c8ab22e2a22'),
    ('uart_chars_in_buffer',0xffffffc010495968,0x138,'6dc7cc604209c07aae4bf8c87f73967570b84059129625e12381e0014c776056'),
    ('uart_get_icount',0xffffffc010495eb0,0x158,'b81dc3c754259dffe7fbe7e9f5289aca08d150065fe6ec162696d9c62c1e130f'),
    ('s3c24xx_serial_start_tx',0xffffffc01049b120,0xe8,'eb6ee3144ed69c7b3ccc145b18573ffb28753b09e5040b0bdb17e4ee2b9c3c58'),
    ('s3c24xx_serial_tx_chars',0xffffffc01049ad58,0x200,'660552039b83e1629f4c882b555c358707b59e77ac257da1707c583e9dd51151'),
)
COPY_FD_ABI=(
    GETTY_RX_ABI[0],
    ('do_sendfile',0xffffffc010204cd8,0x110,'eaf138d8f74c3c7e2cffa12eadbb72bf80e16d75617a4891c59c6c35c01ed558'),
    ('proc_fd_link',0xffffffc010295068,0x110,'51c5ce9cd341501709203839c11a015eb3e2ddb2199a3a0e2d3668a2353805d0'),
)


DMABUF_FD_ABI=(
    GETTY_RX_ABI[0],
    ('dma_buf_get',0xffffffc010592b38,0x58,'20c3861443fe874173e4ae5486e1491839c4f5faf433ba44ebf91776be944c4e'),
    ('dma_buf_fd',0xffffffc010592ae8,0x50,'48b582e24466896f916191a1fd249cf51a444e241723d653f4cbcd073b6f6c94'),
    ('dma_buf_mmap_internal',0xffffffc010592418,0x5c,'ba86e8be8c7f01d8860e945d4b367c7c1243fe227853c8ceed9e1f898b914cb7'),
)


def inspect_dmabuf_fd(task,fd,va,read,ptr,*,ion_probe=None):

    table=ptr(ptr(int(task['task'],16)+0x708)+0x20)
    slots=read(table)&0xffffffff
    if not 0<=fd<slots<=65536 or va['dma_buf_fops']!=0xffffffc010b14500:
        raise ValueError('Unexpected OEM FD table or DMA-BUF file ABI')
    file=ptr(ptr(table+8)+8*fd)
    if read(file+0x28)!=va['dma_buf_fops']:
        return dict(status='observed',kind='other_file')
    dmabuf=ptr(file+0xc8)
    if ptr(dmabuf+8)!=file:
        raise ValueError('OEM DMA-BUF file ownership mismatch')
    size=read(dmabuf)
    if not 0<size<=512<<20:
        raise ValueError('OEM DMA-BUF size is out of scope')
    ops=ptr(dmabuf+0x20)
    exporter=next((name for name in ('ion_dma_buf_ops','dma_buf_ops','exynos_dma_buf_ops','vion_dmabuf_ops',
                                     'dmabuf_container_dma_buf_ops','drm_gem_prime_dmabuf_ops')
                   if va.get(name)==ops),None)
    result=dict(status='observed',kind='dma_buf',bytes=size,exporter_symbol=exporter)
    if ion_probe is not None and exporter=='dma_buf_ops':
        try:
            result['source_pixels']=ion_probe(dmabuf,ops)
        except (KeyError,OSError,RuntimeError,ValueError) as exc:
            result['source_pixels']=dict(status='not_verified',reason=str(exc))
    return result


def verify_kernel_abi(va, entries):
    from vlm_platform import factory_file
    with factory_file('ivi/Image').open('rb') as image:
        for name,address,length,sha in entries:
            image.seek(address-0xffffffc010080000)
            if va[name]!=address or hashlib.sha256(image.read(length)).hexdigest()!=sha:
                raise ValueError('Original kernel ABI changed: '+name)


def getty_rx_snapshot(getty, tty_inode, va, read, ptr, terminal='ttySAC0'):

    verify_kernel_abi(va, GETTY_RX_ABI)
    task=int(getty['task'],16)
    files=ptr(task+0x708)
    table=ptr(files+0x20)
    if not (read(table)&0xffffffff):
        raise ValueError('Original getty has no allocated FD table')
    file=ptr(ptr(table+8))
    if ptr(file+0x20)!=tty_inode:
        raise ValueError('Original tty stdin does not match the observed '+terminal+' inode')
    tty=ptr(ptr(file+0xc8))
    ldisc=ptr(tty+0x258)
    iflag=read(tty+0x110)&0xffffffff
    lflag=read(tty+0x118)>>32
    head,commit,canonical,tail=(read(ldisc+i) for i in (0,8,0x10,0x2260))
    if not tail<=commit<=head or head-tail>4096 or canonical>head:
        return dict(status='not_verified',reason='Original tty ring counters are inconsistent',
            pid=getty['pid'],read_head=head,commit_head=commit,canon_head=canonical,read_tail=tail,
            input_flags=hex(iflag),local_flags=hex(lflag),guest_shell_verified=False)
    return dict(status='verified_'+terminal+'_stdin',pid=getty['pid'],read_head=head,
        commit_head=commit,canon_head=canonical,read_tail=tail,pending=commit-tail,
        input_flags=hex(iflag),local_flags=hex(lflag),ignore_cr=bool(iflag&0x80),
        cr_to_lf=bool(iflag&0x100),canonical=bool(lflag&2),guest_shell_verified=False)


def shell_uart_snapshot(shell, tty_inode, va, read, ptr, terminal='ttySAC0'):

    verify_kernel_abi(va, GETTY_RX_ABI[:2]+UART_TX_ABI)
    task=int(shell['task'],16)
    table=ptr(ptr(task+0x708)+0x20)
    if (read(table)&0xffffffff)<3:
        raise ValueError('Original Bash has no stdio FD table')
    fds=ptr(table+8)
    tty=None
    for fd in (0,1,2):
        file=ptr(fds+8*fd)
        if ptr(file+0x20)!=tty_inode:
            raise ValueError(f'Bash fd {fd} is not the observed {terminal} inode')
        candidate=ptr(ptr(file+0xc8))
        if tty is not None and candidate!=tty:
            raise ValueError('Bash stdio points to different TTY objects')
        tty=candidate
    state=ptr(tty+0x260)
    port=ptr(state+0x198)
    if ptr(port+0xc8)!=state:
        raise ValueError('OEM UART port has no matching uart_state backpointer')
    xmit=read(state+0x170)
    head,tail=xmit&0xffffffff,xmit>>32
    if head>=4096 or tail>=4096:
        raise ValueError('OEM UART TX ring indices out of range')
    io=read(port+0xe0)
    ops=ptr(port+0x130)
    exynos=ops==va.get('exynos_serial_ops')
    if exynos:
        from native_uart_console import ABI
        verify_kernel_abi(va,tuple(row for row in ABI if row[0] in ('exynos_serial_tx_chars','store_console_mode')))
        mode=read(port+0x260)&0xffffffff
        container=port-0x68
        rx_engine=(read(container+0x30)>>32)&0xffffffff
        dma_object=read(container+0x290)
        driver=dict(rx_engine_state=rx_engine,dma_object_present=bool(dma_object),
                    driver='original_exynos_serial',console_mode=mode,
                    rx_muted=bool(mode&1),tx_muted=bool(mode&2),
                    tx_enabled=read(port-0x30)&0xffffffff)
    else:
        driver=dict(driver='other_original_uart',driver_specific_state_verified=False)
    return dict(status='verified_'+terminal+'_stdio',pid=shell['pid'],fds=[0,1,2],
        membase=hex(ptr(port+0x10)),mapbase=hex(read(port+0x148)),
        uart_rx=io&0xffffffff,uart_tx=io>>32,xmit_head=head,xmit_tail=tail,
        xmit_pending=(head-tail)&4095,**driver,
        tty_flow_stopped=bool(read(tty+0x1d0)&(1<<32)))


def resident_log_reader(inode, read, ptr, *, limit=16<<20):

    mapping=ptr(inode+0x30)
    size=read(inode+0x50)
    if not 0<=size<=limit or read(mapping)!=inode:
        raise ValueError('Unexpected OEM log size or mapping owner')
    head=read(mapping+0x10)

    def resident_page(index):
        page=head
        prev=64
        depth=0
        while page>0x1000 and page&3==2:
            node=page-2
            shift=read(node)&0xff
            depth+=1
            if depth>10 or shift>=prev or shift%6 or (depth==1 and (index>>shift)>=64):
                raise ValueError('Unexpected OEM log xarray node')
            prev=shift
            page=read(node+0x28+8*((index>>shift)&63))
                                                                           
                                                                          
        vmemmap=0xfffffffeffe00000
        if (page<vmemmap or page&63 or read(page)&0x10000 or
                read(page+0x18)!=mapping or read(page+0x20)!=index):
            raise ValueError('OEM log page is not resident or not owned by this inode')
        address=0xffffff8000000000+((page-vmemmap)>>6)*4096
        n=min(4096,size-index*4096)
        if not 0<n<=4096:
            raise ValueError('OEM log page outside inode size')
        return b''.join(read(address+i).to_bytes(8,'little') for i in range(0,n,8))[:n]
    return size,resident_page


def tmpfs_log_snapshot(inode, va, read, ptr, output, filename):
    verify_kernel_abi(va, WESTON_LOG_ABI)
    size,page=resident_log_reader(inode,read,ptr)
    if not size:
        return dict(status='empty',size=0)
    pages=(size+4095)//4096
    indices=list(range(pages)) if pages<=4 else [0,1,pages-2,pages-1]
    target=output/filename
    result=dict(status='observed',size=size,sampled_pages=[],missing=[])
    chunks=[]
    for index in indices:
        try:
            raw=page(index)
        except (OSError,ValueError) as exc:
            result['missing'].append(dict(page=index,reason=str(exc)))
            continue
        result['sampled_pages'].append(dict(page=index,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        chunks.append((index,raw))
    if not chunks:
        return dict(result,status='not_verified')
    text=''.join(f'--- OEM tmpfs offset 0x{index*4096:x} ---\n'+
                 raw.decode('utf-8','backslashreplace').replace('\0','\\x00')+'\n' for index,raw in chunks)
    target.write_text(text,encoding='utf-8',newline='')
    return dict(result,path=target.name,complete=pages<=4 and not result['missing'])

def pending_clock_rpc(stack, read):
                                                                         
                                                                            
    frames={f['symbol']:f for f in stack.get('frames',[])}
    expected={'do_vrpc_call':'0xffffffc01077a8a4','vlx_clk_ctrl_call':'0xffffffc01077ab18',
              'vlx_clk_recalc_rate':'0xffffffc01077aba8','seqos_hw_setup':'0xffffffc0105e3ad0'}
    if any(frames.get(name,{}).get('pc')!=pc for name,pc in expected.items()):
        return None
    frame=int(frames['do_vrpc_call']['fp'],16)
    base=int(stack['stack_base'],16)
    if frame%16 or not base<=frame<=base+0x4000-0xb0:
        raise ValueError('Original VRPC frame not within owner stack')
    request_len=read(frame+0x80)&0xffffffff
    clock_id=read(frame+0x60)>>32
    if read(frame+0x30)&0xffffffff != clock_id:
        raise ValueError('Saved QNX clock ID does not match original call local')
    shared=read(frame+0x28)
                                                                     
    transport=shared-12
    first=read(transport)
    length_and_id=read(transport+8)
    operation_and_value=read(transport+16)
    result=dict(clock_id=clock_id,operation=4,argument=read(frame+0x78)&0xffffffff,
                local_request_bytes=request_len,transport_address=hex(transport),
                request_sequence=first&0xffffffff,reply_sequence=first>>32,
                shared_request_bytes=length_and_id&0xffffffff,
                shared_word_id_or_response=length_and_id>>32,
                shared_operation=operation_and_value&0xffffffff)
    if (request_len==88 and result['shared_request_bytes']==88
            and result['shared_word_id_or_response']==clock_id and result['shared_operation']==4
            and result['request_sequence']!=result['reply_sequence']):
        result['status']='verified_outstanding_request'
    else:
        result['status']='caller_verified_transport_not_correlated'
    return result

def clock_owner(symbols, va, read, ptr, string):
                                                                          
                                                                            
    word=read(va['prepare_lock'])
    owner=read(va['prepare_owner'])
    count=read(va['prepare_refcnt'])&0xffffffff
    result=dict(mutex_word=hex(word), prepare_owner=hex(owner), prepare_refcnt=count)
    if not owner:
        return dict(result,status='unowned' if not word and not count else 'owner_transition')
    if word&~7 != owner or not count or owner%64:
        return dict(result,status='inconsistent_owner')
                                                                         
    pid=ptr(owner+0x570)
    if ptr(pid+0x58)!=va['init_pid_ns']:
        raise ValueError('Clock mutex owner is not in the initial PID namespace')
    name=string(owner+0x6c0,16)
    if not name:
        raise ValueError('Clock mutex owner has no original task comm')
    result.update(status='verified_owner',task=hex(owner),comm=name,
                  pid=read(pid+0x50)&0xffffffff, mutex_flags=word&7)
    try:
        result['saved_stack']=capture_task_stack(symbols,owner,read)
    except ValueError as exc:
        result['stack_unverified']=str(exc)
    else:
        try:
            request=pending_clock_rpc(result['saved_stack'],read)
            if request is not None:
                result['clock_rpc']=request
        except ValueError as exc:
            result['clock_rpc_unverified']=str(exc)
    return result

FILE_WAIT_ABI=(
    ('__lock_page_killable',0xffffffc0101834f8,0x148,'08bd718baab23e9fbd9b866046c476400831f8732aecb97f45ed86a04df11da9'),
    ('filemap_fault',0xffffffc010185c18,0xb4,'59018adf7ce665f70d0a47a5ec835e979fffcde7ff63bb3cdd1c6e28975a2b14'),
    ('ext4_filemap_fault',0xffffffc0102cd590,0x50,'29c3eab460511c51a36ba434d65ee77ad99b9c3cae6856ea0c24566b81ff71e4'),
    ('show_map_vma',0xffffffc010289dd8,0x190,'cdad2b94ce647e58b97ed5f8fc5a79b06d470bd45063fc4fba3836455b6e8fb9'),
)
FILE_WAIT_FRAMES=(('io_schedule',0x14),('__lock_page_killable',0x138),
                  ('filemap_fault',0xb0),('ext4_filemap_fault',0x2c))


def file_wait_frame(item):
    stack=item.get('saved_stack',{})
    if item['state']!='0x102' or stack.get('kernel_sha256')!=LINUX_SHA:
        return None
    frames=stack.get('frames',[])
    for i in range(len(frames)-3):
        group=frames[i:i+4]
        if tuple((f['symbol'],f['offset']) for f in group)==FILE_WAIT_FRAMES:
            return group
    return None


def ext4_file_wait(item, frames, read, ptr, mountpath, ns, root):
    proc=int(item['task'],16)
    proc_root=ptr(proc+0x700)+0x18
    if ptr(ptr(proc+0x710)+0x18)!=ns or (ptr(proc_root)-0x20,ptr(proc_root+8))!=root:
        raise ValueError('File wait is outside the original mount namespace/root')
    stack=item['saved_stack'];base=int(stack['stack_base'],16);sp=int(stack['saved_sp'],16)
    f0,fp,f2,f3=(int(f['fp'],16) for f in frames)
    if not (base<=sp<=f0 and f0<fp and f2==fp+0xb0 and f3==f2+0xd0 and
            f3+0x20<=base+0x4000 and not (f0|fp|f2|f3)&0xf):
        raise ValueError('Unexpected OEM file wait frame bounds')
    vmf,page=ptr(fp+0x50),ptr(fp+0x58)
    if not (f3+0x20<=vmf<=base+0x4000-0x18):
        raise ValueError('Fault request is outside the caller kernel stack')
    vma=ptr(vmf)
    if ptr(vma+0x40)!=ptr(proc+0x458):
        raise ValueError('Fault mapping does not belong to this task')
    file=ptr(vma+0xa0)
    inode=ptr(file+0x20)
    dentry=ptr(file+0x18)
    if read(inode)&0xf000!=0x8000 or ptr(dentry+0x30)!=inode:
        raise ValueError('Fault target is not a verified regular file')
    mapping=ptr(file+0xf0)
    index=read(vmf+0x10)
    flags=read(page)
    size=read(inode+0x50)
    if (ptr(mapping)!=inode or read(page+0x18)!=mapping or read(page+0x20)!=index or
            read(page+8)&1 or not flags&1 or not 0<size<1<<63 or index>=(size+0xfff)//0x1000):
        raise ValueError('Page cache identity, index or lock no longer matches')
    mount=ptr(file+0x10)-0x20
    path=mountpath(mount,dentry)
    if not path or not path.startswith('/'):
        raise ValueError('File path unavailable in original task root')
    return dict(status='locked_ext4_file_page',path=path,file_offset=index<<12,
                inode_bytes=size,device=read(ptr(mount+0x28)+0x10)&0xffffffff)


def inspect(symbols, read_u64, pid1, *, center_peers=False, read_user_u64=None,
            hotspot_log_parser=None, inspect_hmi_queues=None, read_ivi_frame=None):
    if symbols.get('sha256') != LINUX_SHA or pid1.get('pid') != 1:
        raise ValueError('Unverified original Linux kernel or PID 1')
    va = {row['name']: int(row['address'],16) for row in symbols['symbols']}
    task = int(pid1['task'],16)
    drm_scanout=None
    directory=None
    if '--output' in sys.argv:
        from original_8704_vm import save
        directory=Path(sys.argv[sys.argv.index('--output')+1]).resolve()
        try:
            inspect_drm=runpy.run_path(str(Path(__file__).with_name('native-linux-scanout.py')))['inspect']
            drm_scanout=inspect_drm(symbols,read_u64,directory)
        except (OSError,ValueError,KeyError) as exc:
            drm_scanout=dict(status='not_verified',reason=str(exc))
        save(directory/'linux-native-scanout.json',drm_scanout)

    @lru_cache(maxsize=8192)
    def read(addr):
        if not 0xffffff8000000000 <= addr <= 0xfffffffffffffff8 or addr%8:
            raise ValueError(f'Invalid OEM kernel virtual pointer {addr:#x}')
        return read_u64(addr)

    def ptr(addr):
        val=read(addr)
        if not 0xffffff8000000000 <= val <= 0xfffffffffffffff8 or val%8:
            raise ValueError(f'Invalid object pointer at {addr:#x}: {val:#x}')
        return val

    def string(addr,limit):
        if not 0 < limit <= 256:
            raise ValueError('Unbounded OEM string')
        start=addr&~7
        data=bytearray()
        for i in range(0,limit+(addr-start),8):
            data.extend(read(start+i).to_bytes(8,'little'))
            if 0 in data[max(addr-start,i):]:
                break
        return bytes(data[addr-start:addr-start+limit]).split(bytes(1),1)[0].decode('ascii','backslashreplace')

    def links(head,max_count):
                                                                             
        entry=ptr(head)
        prev=head
        seen=set()
        while entry!=head:
            if entry in seen or len(seen)>=max_count or ptr(entry+8)!=prev:
                raise ValueError(f'Inconsistent or oversized OEM list at {head:#x}')
            seen.add(entry)
            yield entry
            prev=entry
            entry=ptr(entry)
        if ptr(head+8)!=prev:
            raise ValueError(f'Inconsistent OEM list tail at {head:#x}')

                                                                             
    tasks=[]
    seen={task}
    q=deque([(task,1,0)])
    while q:
        if len(seen)>256:
            raise ValueError('Original PID 1 descendant count exceeded 256')
        proc,n,pn=q.popleft()
        raw=read(proc+0x20)
        name=string(proc+0x6c0,16)
        item=dict(pid=n,ppid=pn,comm=name,task=hex(proc),state=hex(raw),
                  has_mm=bool(read(proc+0x458)))
        if raw and (n==1 or name in TASK_WATCH):
            try:
                item['saved_stack']=capture_task_stack(symbols,proc,read)
            except ValueError as exc:
                item['stack_unverified']=str(exc)
        tasks.append(item)
        for e in links(proc+0x528,192):
            child=e-0x538
            if child in seen:
                raise ValueError('Duplicate original task in process tree')
            seen.add(child)
            pid=ptr(child+0x570)
            if ptr(pid+0x58)==va['init_pid_ns']:
                q.append((child,read(pid+0x50)&0xffffffff,n))

    result=dict(status='verified_process_snapshot',scope='Stopped original OEM Linux; no guest writes',
                kernel_sha256=LINUX_SHA,tasks=tasks)
                                                                           
    if 'crng_init' in va:
        at=va['crng_init'];value=(read(at&~7)>>((at&7)*8))&0xffffffff
        if value not in (0,1,2):raise ValueError('Unexpected original CRNG state')
        result['native_rng_state']=dict(crng_init=value,ready=value==2,
            scope='Pinned original kernel scalar only, no random pool bytes read')

    if read_user_u64 is not None and any(t['comm']=='post_init_main.' and t['ppid']==1 for t in tasks):
        try:
            post_init=runpy.run_path(str(Path(__file__).with_name('observe-post-init-children.py')))
            verify_kernel_abi(va,post_init['ABI'])
            result['post_init_children']=post_init['inspect'](tasks,read,ptr,read_user_u64)
        except (ValueError,OSError,KeyError,RuntimeError) as exc:
            result['post_init_children']=dict(status='not_verified',reason=str(exc))
    try:
        from linux_mcu_identity import GUEST_CACHE,guest_identity
        result['mcu_identity']=guest_identity(read(GUEST_CACHE))
    except (OSError,ValueError,KeyError) as exc:
        result['mcu_identity']=dict(status='not_verified',reason=str(exc))
    try:
        result['clock_prepare_lock']=clock_owner(symbols,va,read,ptr,string)
    except (KeyError,ValueError) as exc:
        result['clock_prepare_lock']=dict(status='not_verified',reason=str(exc))
    if drm_scanout is not None:
        result['drm_scanout']=drm_scanout
    try:
                                                                   
                                                                           
        ns=ptr(ptr(task+0x710)+0x18)
        rootpath=ptr(task+0x700)+0x18
        root_mnt=ptr(rootpath)-0x20
        root_dentry=ptr(rootpath+8)

        def mountpath(mount, dentry=None):
            m=mount
            d=ptr(m+0x20) if dentry is None else dentry
            parts=[]
            seen=set()
            for _ in range(96):
                if (m,d)==(root_mnt,root_dentry):
                    return '/'+('/'.join(reversed(parts)))
                if (m,d) in seen:
                    raise ValueError('Cyclic original mount/dentry path')
                seen.add((m,d))
                if d==ptr(m+0x20):
                    parent=ptr(m+0x10)
                    if parent==m:
                        return None                                            
                    d=ptr(m+0x18)
                    m=parent
                    continue
                size=(read(d+0x20)>>32)&0xffffffff
                name=string(read(d+0x28),size)
                if not name or '/' in name:
                    raise ValueError('Unexpected original dentry component')
                parts.append(name)
                d=ptr(d+0x18)
            raise ValueError('Original mountpoint path exceeded bound')

        mounts=[]
        roots={'/':root_dentry}
        for entry in links(ns+0x28,128):
            m=entry-0x88
            sb=ptr(m+0x28)
            dev=read(sb+0x10)&0xffffffff
            devname=read(m+0x80)
            mountpoint=mountpath(m)
            fstype=string(read(ptr(sb+0x28)),64)
            if (mountpoint,fstype) in (('/dev','devtmpfs'),('/run','tmpfs'),('/tmp','tmpfs'),
                                        ('/data','ext4'),('/lge/navi/naviengine','ext4'),
                                        ('/navi_data','ext4')):
                roots[mountpoint]=ptr(m+0x20)
            mounts.append(dict(mountpoint=mountpoint,source=string(devname,128) if devname else None,
                               fstype=fstype,dev_major=dev>>20,
                               dev_minor=dev&0xfffff,read_only=bool((read(m+0x30)&0x40) or (read(sb+0x50)&1)),
                               mount_flags=hex(read(m+0x30)&0xffffffff),noexec=bool(read(m+0x30)&4),
                               mount_id=read(m+0x118)>>32))
        result.update(mount_namespace=hex(ns),mounts=mounts,mounts_verified=True)
    except ValueError as exc:
        result.update(mounts_verified=False,mount_error=str(exc))
        return result

    candidates=[(t,group) for t in tasks if (group:=file_wait_frame(t))]
    if candidates:
        try:
            verify_kernel_abi(va,FILE_WAIT_ABI)
            for item,frames in candidates[:24]:
                try:
                    item['ext4_file_wait']=ext4_file_wait(
                        item,frames,read,ptr,mountpath,ns,(root_mnt,root_dentry))
                except (OSError,RuntimeError,KeyError,ValueError) as exc:
                    item['ext4_file_wait']=dict(status='not_verified',reason=str(exc))
        except (OSError,KeyError,ValueError) as exc:
            result['ext4_file_wait_abi']=dict(status='not_verified',reason=str(exc))

    blocked_copies=[t for t in tasks if t['comm']=='cp' and
                    any(f['symbol']=='do_sendfile' for f in t.get('saved_stack',{}).get('frames',[]))]
    for item in blocked_copies:
        try:
            verify_kernel_abi(va,COPY_FD_ABI)
            proc=int(item['task'],16)
            proc_root=ptr(proc+0x700)+0x18
            if (ptr(ptr(proc+0x710)+0x18)!=ns or
                    (ptr(proc_root)-0x20,ptr(proc_root+8))!=(root_mnt,root_dentry)):
                raise ValueError('Copy process has a different mount namespace or root')
            table=ptr(ptr(proc+0x708)+0x20)
            count=read(table)&0xffffffff
            if not 4<=count<=4096:
                raise ValueError('Unexpected copy FD table size')
            entries=ptr(table+8)
            regular=[]
            for fd in range(3,min(count,32)):
                slot=entries+8*fd
                if not read(slot):
                    continue
                file=ptr(slot)
                inode=ptr(file+0x20)
                if read(inode)&0xf000!=0x8000:
                    continue
                mnt,dentry=ptr(file+0x10)-0x20,ptr(file+0x18)
                if ptr(dentry+0x30)!=inode:
                    raise ValueError('Copy file inode disagrees with the dentry')
                path=mountpath(mnt,dentry)
                if path is None:
                    continue
                mode=(read(file+0x40)>>32)&3
                regular.append(dict(fd=fd,path=path,readable=bool(mode&1),writable=bool(mode&2),
                                    offset=read(file+0x68),inode_bytes=read(inode+0x50)))
            item['copy_open_files']=dict(status='observed',files=regular,
                scope='Open regular FDs 3–31; sendfile register arguments not inspected')
        except (OSError,KeyError,ValueError) as exc:
            item['copy_open_files']=dict(status='not_verified',reason=str(exc))

                                                                   
                                                                        
                                                                          
    def component(parent,name):
        matches=[]
        for entry in links(parent+0xa0,2048):
            d=entry-0x90
            if ptr(d+0x18)!=parent:
                raise ValueError('Original dentry parent differs from subdir list')
            inode=read(d+0x30)
            if inode and (read(d+0x20)>>32)==len(name) and string(read(d+0x28),len(name))==name:
                matches.append((d,ptr(d+0x30)))
        if len(matches)>1:
            raise ValueError('Ambiguous positive OEM dcache entries for '+name)
        return matches[0] if matches else None

    paths=('/dev/sda18','/dev/ttySAC0','/dev/ttySAC3','/dev/ttySAC4','/dev/dri/card0','/dev/disk/by-partlabel/navi_core',
           '/lib/systemd/system/serial-getty@.service',
           '/run/systemd/system/serial-getty@ttySAC0.service',
           '/run/systemd/system/oem-local-uart.service',
           '/run/weston.txt','/run/platform/weston/wayland-0','/run/log/trace_unix_socket',
           '/run/systemd/system/vw-hmi.service.d/90.conf',
           '/tmp/traced.log','/data/lge/hmi','/tmp/post_init_done.flag','/data/post_init_done.flag',
           '/tmp/MXNavi.pid','/lge/app_ro/navi/naviengine/navicore',
           '/lge/navi/naviengine/package/MXNavi/MXNavi','/navi_data/MXNaviData',
           '/etc/systemd/system/multi-user.target.wants/naviengine.service',
           '/run/systemd/generator/local-fs.target.requires/lge-navi-naviengine.mount',
           '/run/systemd/generator/getty.target.wants/serial-getty@ttySAC0.service')
    result['dcache']={}

    def inspect_path(path):
        try:
            _,root,*names=path.split('/')
            nav_mount='/lge/navi/naviengine'
            if path.startswith(nav_mount+'/'):
                d=roots.get(nav_mount)
                names=path[len(nav_mount)+1:].split('/')
            elif root in ('dev','run','tmp','data','navi_data'):
                d=roots.get('/'+root)
            else:
                d=roots.get('/')
                names.insert(0,root)
            if d is None:
                raise ValueError('Mount root not available')
            for name in names:
                found=component(d,name)
                if found is None:
                    result['dcache'][path]=dict(status='not_observed',component=name)
                    break
                d,inode=found
            else:
                mode=read(inode)&0xffff
                item=dict(status='positive_dentry',address=hex(d),inode=hex(inode),mode=oct(mode))
                if mode&0xf000==0xa000 and read(inode+0x218):
                    item['inline_link']=string(read(inode+0x218),128)
                if mode&0xf000 in (0x2000,0x6000):
                    dev=read(inode+0x48)>>32
                    item.update(major=dev>>20,minor=dev&0xfffff)
                result['dcache'][path]=item
                if (path in TMPFS_LOGS and mode&0xf000==0x8000 and '--output' in sys.argv and
                        any(m['mountpoint']=='/'+root and m['fstype']=='tmpfs' for m in mounts)):
                    try:
                        item['runtime_log']=tmpfs_log_snapshot(inode,va,read,ptr,directory,TMPFS_LOGS[path])
                    except (OSError,KeyError,ValueError) as exc:
                        item['runtime_log']=dict(status='not_verified',reason=str(exc))
        except ValueError as exc:
            result['dcache'][path]=dict(status='not_verified',reason=str(exc))
    for path in paths:
        inspect_path(path)
    if os.environ.get('OEM_8704_KIPC_READ')=='1':
        try:
            kipc=runpy.run_path(str(Path(__file__).with_name('observe-native-kipc.py')))
            verify_kernel_abi(va,GETTY_RX_ABI[:1]+kipc['ABI'])
            result['native_kipc']=kipc['inspect'](tasks,va,read,ptr,string)
            inspect_path('/dev/kipc')
        except (OSError,ValueError,KeyError) as exc:
            result['native_kipc']=dict(status='not_verified',reason=str(exc))
    if os.environ.get('OEM_8704_PERSISTENCE_PRIORITY')=='1':
        inspect_path(PERSIST_GRACE if os.environ.get('OEM_8704_PERSISTENCE_START_GRACE')=='1'
                     else PERSIST_PRIORITY)
    if os.environ.get('OEM_8704_POST_INIT_AFTER_TMPFILES')=='1':
        inspect_path(POST_INIT_GRACE)
    if os.environ.get('OEM_8704_TRACE_BACKUP_SETTLE')=='1':
        from linux_early_uart import TRACE_BACKUP_SERVICE, TRACE_BACKUP_DROPIN
        for leaf in (TRACE_BACKUP_SERVICE,TRACE_BACKUP_DROPIN):
            inspect_path('/run/systemd/system/'+leaf)
    if (hotspot_log_parser is None and os.environ.get('OEM_8704_HMI_LSC_TIMEOUT_MS') and
            center_peers and inspect_hmi_queues is not None):
        java=[row['pid'] for row in tasks if row.get('comm')=='JAVA' and row.get('has_mm') is True]
        if len(java)==1:
            parse_option=runpy.run_path(str(Path(__file__).with_name('oem-hotspot-log.py')))['lsc_option']
            hotspot_log_parser=lambda raw: parse_option(raw,java[0])
    if hotspot_log_parser is not None:
        path='/data/8704-hmi-vm.xml'
        inspect_path(path)
        try:
            item=result['dcache'][path]
            if item['status']!='positive_dentry' or int(item['mode'],8)&0xf000!=0x8000:
                raise ValueError('Original HotSpot VM log is not a resident regular inode')
            verify_kernel_abi(va,WESTON_LOG_ABI[1:4]+WESTON_LOG_ABI[-1:])
            size,page=resident_log_reader(int(item['inode'],16),read,ptr,limit=128<<10)
            chunks=[]
            for index in range((size+4095)//4096):
                try:
                    chunks.append(page(index))
                except (OSError,ValueError):
                    break                                            
            raw=b''.join(chunks)
            result['hmi_java_cache']=dict(status='observed' if raw else 'not_observed',
                                          inode_bytes=size,complete=len(raw)==size,
                                          parsed=hotspot_log_parser(raw) if raw else None)
        except (KeyError,OSError,ValueError) as exc:
            result['hmi_java_cache']=dict(status='not_verified',reason=str(exc))
    if center_peers:
        try:
            hmi=runpy.run_path(str(Path(__file__).with_name('observe-hmi-threads.py')))
            result['hmi_threads']=hmi['inspect'](tasks,symbols,va,read,ptr,string,links,
                                                verify_kernel_abi,capture_task_stack,read_user_u64,
                                                inspect_queues=inspect_hmi_queues)
        except (KeyError,OSError,RuntimeError,ValueError) as exc:
            result['hmi_threads']=dict(status='not_verified',reason=str(exc))
        if read_user_u64 is not None and os.environ.get('OEM_8704_WESTON_MAIN_READ')=='1':
            try:
                result['weston_threads']=hmi['inspect'](tasks,symbols,va,read,ptr,string,links,
                                                        verify_kernel_abi,capture_task_stack,read_user_u64,
                                                        weston=True)
            except (KeyError,NameError,OSError,RuntimeError,ValueError) as exc:
                result['weston_threads']=dict(status='not_verified',reason=str(exc))
        try:
            center=runpy.run_path(str(Path(__file__).with_name('observe-center-peers.py')))
            verify_kernel_abi(va,GETTY_RX_ABI[:1]+center['ABI'])
            log_inode=result['dcache']['/run/weston.txt'].get('inode')
            result['center_peers']=center['inspect'](tasks,va,read,ptr,string,int(log_inode,16) if log_inode else None)
        except (OSError,KeyError,ValueError) as exc:
            result['center_peers']=dict(status='not_verified',reason=str(exc))
    if read_user_u64 is not None:
        try:
            scene=runpy.run_path(str(Path(__file__).with_name('observe-original-weston-scene.py')))
            verify_kernel_abi(va,scene['ABI'])
            @lru_cache(maxsize=1)
            def check_dmabuf_abi():
                verify_kernel_abi(va,DMABUF_FD_ABI)
            @lru_cache(maxsize=1)
            def load_ion_observer():
                import json
                observer=runpy.run_path(str(Path(__file__).with_name('observe-ivi-ion.py')))
                verify_kernel_abi(va,observer['ION_ABI'])
                hcb=json.loads((directory/'hcb-adaptation.json').read_bytes())
                banks=[(int(b['base'],16),int(b['base'],16)+b['bytes']) for change in hcb['changes']
                       for b in change.get('banks',[])]
                scope=((0xb8000000,0xd0000000),(0xe3c00000,0xe4c00000),(0xa80000000,0xb00000000))
                def backed(address,length):
                    return (any(lo<=address<address+length<=hi for lo,hi in scope) and
                            any(lo<=address<address+length<=hi for lo,hi in banks))
                return observer['sample'],backed
            def file_backing(process,fd,*,layout=None):
                check_dmabuf_abi()
                ion_probe=None
                if read_ivi_frame is not None and layout is not None and directory is not None:
                    def ion_probe(dmabuf,ops):
                        sample,backed=load_ion_observer()
                        return sample(dmabuf,ops,va,read,ptr,backed,read_ivi_frame,layout=layout)
                return inspect_dmabuf_fd(process,fd,va,read,ptr,ion_probe=ion_probe)
            result['center_original_scene']=scene['inspect'](tasks,read,ptr,string,read_user_u64,
                                                              file_backing=file_backing)
        except (OSError,KeyError,ValueError) as exc:
            result['center_original_scene']=dict(status='not_verified',reason=str(exc))
    gettys=[t for t in tasks if t['comm']=='agetty' and any(f['symbol'] in ('n_tty_read','do_select')
           for f in t.get('saved_stack',{}).get('frames',[]))]
    tty=result['dcache']['/dev/ttySAC0']
    if tty.get('inode') and gettys:
        try:
            rows=[getty_rx_snapshot(g,int(tty['inode'],16),va,read,ptr) for g in gettys]
            result['native_getty_rx']=rows
        except (OSError,KeyError,ValueError) as exc:
            result['native_getty_rx']=dict(status='not_verified',reason=str(exc))
    terminal='ttySAC0'
    direct_path=directory/'linux-direct-console.json'
    if '--output' in sys.argv and direct_path.is_file():
        import json
        terminal=json.loads(direct_path.read_bytes())['terminal'].removeprefix('/dev/')
        if terminal not in ('ttySAC3','ttySAC4'):
            raise ValueError('Unbound direct original UART terminal')
    tty=result['dcache']['/dev/'+terminal]
    shells=[t for t in tasks if t['comm']=='bash.bash' and any(
        f['symbol'] in ('n_tty_read','do_select') for f in t.get('saved_stack',{}).get('frames',[]))]
    if tty.get('inode') and shells:
        try:
            result['native_shell_rx']=[getty_rx_snapshot(t,int(tty['inode'],16),va,read,ptr,terminal) for t in shells]
        except (OSError,KeyError,ValueError) as exc:
            result['native_shell_rx']=dict(status='not_verified',reason=str(exc))
    if tty.get('inode'):
        for shell in (t for t in tasks if t['comm']=='bash.bash' and t['has_mm']):
            try:
                item=shell_uart_snapshot(shell,int(tty['inode'],16),va,read,ptr,terminal)
            except (OSError,KeyError,ValueError) as exc:
                item=dict(pid=shell['pid'],status='not_verified',reason=str(exc))
            result.setdefault('native_shell_uart',[]).append(item)
                                                                           
    owners=[]; inspected=0; skipped=0
    tty_ops={va[k] for k in ('tty_fops','console_fops') if k in va}
    if tty.get('inode') and len(tty_ops)==2:
        verify_kernel_abi(va,GETTY_RX_ABI[:2]+COPY_FD_ABI)
        target_inode=int(tty['inode'],16); target_ttys=set()
        for shell in (t for t in tasks if t['comm']=='bash.bash'):
            try:
                table=ptr(ptr(int(shell['task'],16)+0x708)+0x20)
                file=ptr(ptr(table+8))
                if ptr(file+0x20)==target_inode:
                    target_ttys.add(ptr(ptr(file+0xc8)))
            except (OSError,ValueError):continue
        for process in tasks:
            try:
                table=ptr(ptr(int(process['task'],16)+0x708)+0x20)
                count=read(table)&0xffffffff
                if not 1<=count<=4096:raise ValueError('Unbounded FD table')
                fds=ptr(table+8)
                for fd in range(min(count,64)):
                    if not read(fds+8*fd):continue
                    file=ptr(fds+8*fd);inspected+=1
                    if read(file+0x28) not in tty_ops:continue
                    actual=ptr(ptr(file+0xc8))
                    if actual not in target_ttys:continue
                    mode=(read(file+0x40)>>32)&3
                    owners.append(dict(pid=process['pid'],comm=process['comm'],fd=fd,
                        inode=hex(ptr(file+0x20)),same_inode=ptr(file+0x20)==target_inode,
                        readable=bool(mode&1),writable=bool(mode&2),tty=hex(actual)))
            except (OSError,ValueError):skipped+=1
        result['native_tty_open_files']=dict(status='verified_bounded_snapshot',terminal=terminal,
            files=owners,inspected_open_files=inspected,skipped_processes=skipped,
            scope='Process leaders FDs 0..63; original tty/console fops and exact tty pointer; open FD is not byte-consumption proof. No guest writes.')
    block=result['dcache']['/dev/sda18']
    if block.get('major'):
        inspect_path(f"/run/udev/data/b{block['major']}:{block['minor']}")
    return result
