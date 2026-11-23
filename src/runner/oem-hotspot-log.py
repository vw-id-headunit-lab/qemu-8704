
import hashlib
import html
import re

THREAD = re.compile(r'^"([^"]{1,120})"\s.*\bnid=0x([0-9a-f]+)\b')
FRAME = re.compile(r'^\s*at ([\w.$<>/]+)\(([^()\r\n]{1,120})\)$')
STATE = re.compile(r'^\s*java\.lang\.Thread\.State: ([A-Z_]+)')
WAIT = re.compile(r'^\s*- (parking to wait for|waiting on|waiting to lock|locked)\s+'
                  r'<(0x[0-9a-f]+)> \(a[n]? ([\w.$]+)\)')
WATCH = {'UI-Thread','Startup-Thread','MAIN_THREAD','main','App-Startup-Thread',
         'ODP-Startup-Thread','Signal Dispatcher'}
FRAMEWORK_PREFIX=('de.vw.mib.', 'de.vw.mib2.', 'org.osgi.', 'org.apache.felix.')


def lsc_option(raw, expected_pid):

    head=raw[:16384].split(b'<tty>',1)[0]
    match=re.search(rb"<hotspot_log[^>]{0,200}\bprocess='([0-9]{1,8})'",head[:2048])
    if not match or int(match[1])!=expected_pid:
        return dict(status='original_jvm_log_owner_unverified')
    config=re.search(rb'<vm_arguments>(.*?)</vm_arguments>',head,re.S)
    if not config:
        return dict(status='original_jvm_option_header_unverified')
    properties=re.search(rb'<properties>(.*?)</properties>',config[1],re.S)
    flags=re.search(rb'<args>(.*?)</args>',config[1],re.S)
    key=rb'de\.vw\.mib\.lsc\.initial\.timeout='
    values=re.findall(rb'(?:^|[\r\n])'+key+rb'([0-9]{1,5})(?:\r?$|\n)',
                      properties[1] if properties else b'',re.M)
    args=re.findall(rb'(?:^|\s)-D'+key+rb'([0-9]{1,5})(?=\s|$)',flags[1] if flags else b'')
    if len(values)!=1 or len(args)!=1 or values[0]!=args[0] or not 5000<=int(values[0])<=90000:
        return dict(status='original_jvm_lsc_timeout_unverified')
    return dict(status='original_jvm_lsc_timeout_observed',timeout_ms=int(values[0]))


def parse(raw):
    doc = raw.decode('utf-8', 'replace')
    pid = re.search(r"<hotspot_log[^>]{0,200}\bprocess='(\d+)'", doc[:2048])
    tty = html.unescape(doc.partition('<tty>')[2].split('</tty>',1)[0])
    dumps = []
    for block in tty.split('Full thread dump ')[1:4]:
        rows = []
        row = None
        body = block.split('\nJNI global references:',1)[0]
        for line in body.splitlines()[:12000]:
            if thread := THREAD.match(line):
                row = dict(name=thread[1], tid=int(thread[2],16), frames=[], waits=[])
                rows.append(row)
            elif row is not None:
                if match := STATE.match(line):
                    row['state'] = match[1]
                elif (match := FRAME.match(line)) and len(row['frames'])<24:
                    row['frames'].append(dict(method=match[1],source=match[2]))
                elif (match := WAIT.match(line)) and len(row['waits'])<12:
                    row['waits'].append((match[1],match[2],match[3]))
        selected = [row for row in rows if row['name'] in WATCH]
        keys = {key for row in selected for kind,key,_ in row['waits'] if kind!='locked'}
        for row in selected:
            row['waits'] = [dict(kind=kind, token=key, class_name=klass)
                            for kind,key,klass in row['waits']]
        owners = [dict(name=row['name'],tid=row['tid'],lock_tokens=[key for kind,key,_ in row['waits']
                  if key in keys and kind=='locked'])
                  for row in rows if row not in selected and any(
                      key in keys and kind=='locked' for kind,key,_ in row['waits'])]
        framework=[
            dict(name=row['name'],tid=row['tid'],state=row.get('state'),
                 first_site=next(frame for frame in row['frames']
                                 if frame['method'].startswith(FRAMEWORK_PREFIX)))
            for row in rows if row not in selected and any(
                frame['method'].startswith(FRAMEWORK_PREFIX) for frame in row['frames'])
        ][:64]
        dumps.append(dict(threads=len(rows),selected=selected,shared_lock_holders=owners,
                          framework_sites=framework,
                          complete_marker='\nJNI global references:' in block))
    return dict(pid=int(pid[1]) if pid else None,sha256=hashlib.sha256(raw).hexdigest(),
                bytes=len(raw), dumps=dumps)


