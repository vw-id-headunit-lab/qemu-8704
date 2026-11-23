
from linux_pid1_context import LINUX_SHA

OBJECTS = frozenset(('common::bin','graphics::sub','log::sub','carssw::sub','hmi::sub'))


def capture(symbols, read_u64):
    if symbols['sha256']!=LINUX_SHA:
        raise ValueError('SMACK layout is specific to the original 8704 Linux Image')
    lookup={row['name']:int(row['address'],16) for row in symbols['symbols']}

    def read(addr):
        if not 0xffffff8000000000<=addr<=0xfffffffffffffff8 or addr%8:
            raise ValueError(f'Invalid OEM kernel pointer {addr:#x}')
        return read_u64(addr)

    def label(addr):
                                                                         
        text=bytearray()
        for qword in range(addr&~7,(addr+263)&~7,8):
            chunk=read(qword).to_bytes(8,'little')[max(0,addr-qword):]
            prefix,nul,_=chunk.partition(bytes(1))
            text.extend(prefix)
            if nul and 0<len(text)<=255 and all(32<=b<127 for b in text):
                return text.decode('ascii')
            if nul or len(text)>255:
                break
        raise ValueError('Invalid original SMACK label')

    floor=lookup['smack_known_floor']
                                                              
                                                                        
    if label(read(floor+0x20))!='_':
        raise ValueError('Original SMACK floor subject mismatch')
    head=floor+0x60
    matches={name:None for name in sorted(OBJECTS)}
    seen=set()
    node=read(head)
    for _ in range(1024):
        if node==head:
            return dict(status='verified',subject='_',rule_count=len(seen),rules=matches,
                        source='Read-only paused original Linux; not a policy change')
        if node in seen or read(node+0x10)!=floor:
            raise ValueError('Cyclic or foreign OEM SMACK floor rule')
        seen.add(node)
        name=label(read(read(node+0x18)+0x20))
        if name in matches:
            matches[name]=hex(read(node+0x20)&0xffffffff)
        node=read(node)
    raise ValueError('OEM SMACK floor rule list exceeded capture bound')
