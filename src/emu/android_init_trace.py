
import hashlib
import re
from vlm_platform import parse_dtb,rebuild_fdt

TOKEN='initcall_debug'
ENTRY=(0x198050,880,'ddc68b230fb09cdf6a86b27cbb0575fdf8760667a9d16439a02a9c609d807ab0')


def verify_kernel(kernel):
    off,size,digest=ENTRY
    if (hashlib.sha256(kernel[off:off+size]).hexdigest()!=digest or
            b'initcall_debug\0' not in kernel):
        raise ValueError('Original Android initcall tracing contract changed')


def boot_dtb(original):
    nodes=parse_dtb(original)
    args=nodes['/chosen']['bootargs']
    if not args.endswith(b'\0') or b'\0' in args[:-1]:
        raise ValueError('Original Android /chosen/bootargs not a single string')
    old=args[:-1].decode('ascii')
    if any(w.partition('=')[0]==TOKEN for w in old.split()):
        raise ValueError('Duplicate Android initcall_debug setting')
    new=old.rstrip()+' '+TOKEN
    nodes['/chosen']['bootargs']=new.encode('ascii')+b'\0'
    adapted=rebuild_fdt(original,nodes)
    if parse_dtb(adapted)!=nodes:
        raise ValueError('Diagnostic DT roundtrip failed')
    return adapted,dict(parent_sha256=hashlib.sha256(original).hexdigest(),
                        diagnostic_sha256=hashlib.sha256(adapted).hexdigest(),
                        bootargs_before=old,bootargs_after=new,
                        changed_properties=['/chosen/bootargs'],
                        oem_code_changed=False,boot_policy_changed=False)


def summarize(records):
    starts = re.compile(r'^calling\s+(\S+)\s+@\s+(\d+)')
    ends = re.compile(r'^initcall\s+(\S+)\s+returned\s+(-?\d+)')
    waiting=[]
    calls=[]
    for row in records:
        text=row.get('text','').strip()
        a,b=starts.match(text),ends.match(text)
        if a:
            waiting.append(dict(function=a[1],pid=int(a[2])))
        if b:
            candidates=[n for n,p in enumerate(waiting) if p['function']==b[1]]
            call=waiting.pop(candidates[0]) if candidates else dict(function=b[1])
            call.update(ret=int(b[2]))
            calls.append(call)
    return dict(completed=calls,without_observed_return=waiting,
                pid1_exec_log=[r['text'] for r in records if r.get('text','').startswith('Run /init as init process')],
                scope='Retained original printk ring, no proof that unreturned entry is permanently blocked')

