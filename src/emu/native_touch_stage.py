from pathlib import Path
import gzip,hashlib,json,shlex

ROOT=Path(__file__).resolve().parents[2]
PROFILE='original-unified-touch-v1'

def stage(nodes):
 chosen=nodes['/chosen'];manifest=json.loads((ROOT/'runtime/guest/native-touch/manifest.json').read_bytes())
 if manifest['profile']!=PROFILE or any(n.startswith('oem-touch-')for n in chosen):raise ValueError('Native touch staging differs')
 prefix='d=/run/oem-touch;[ ! -e "$d" ]||exit 86;mkdir -m 0755 "$d"||exit 86;'
 for name,row in manifest['assets'].items():
  p=ROOT/row['path'];raw=p.read_bytes()
  if not p.is_relative_to(ROOT/'runtime/guest/native-touch')or len(raw)!=row['bytes']or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Native touch asset differs: '+name)
  prop='oem-touch-'+name.replace('.','-');chosen[prop]=gzip.compress(raw,mtime=0)
  prefix+='/bin/busybox gzip -dc /sys/firmware/devicetree/base/chosen/'+prop+' >"$d/'+name+'"||exit 86;printf "%s  %s\\n" '+row['sha256']+' "$d/'+name+'"|/usr/bin/sha256sum -c -||exit 86;'
 prepare='''set -eu
r=/run/oem-touch
exec >"$r/prepare.stdout" 2>"$r/prepare.stderr"
chmod 0644 "$r/prepare.stdout" "$r/prepare.stderr"
trap 'rc=$?;printf "%s\\n" "$rc" >"$r/prepare.exit";chmod 0644 "$r/prepare.exit"' 0
n=0
until grep -q ' /vw ext4 rw[, ]' /proc/mounts;do n=$((n+1));[ "$n" -le 120 ] || exit 87;/bin/sleep 1;done
printf '%s  %s\\n' e1bb4059e85b0ffc1108f66b0a9e23f5a1ebec590804602ebf00ab67c47256c9 /lge/app_ro/hmi/touch-app | /usr/bin/sha256sum -c -
d=/vw/oem-touch
[ ! -e "$d" ] || exit 86
mkdir -m 0755 "$d"
for name in touch-app provider.so;do
 cp -a /lge/app_ro/hmi/touch-app "$d/$name"
 [ ! -L "$d/$name" ] || exit 86
 cat "$r/$name" >"$d/$name"
 /usr/bin/chsmack -a hmi::sub -e hmi::sub "$d/$name"
 /usr/bin/chsmack "$d/$name"
done
chmod 0755 "$d/touch-app"
chmod 0644 "$d/provider.so"
cp "$r/relay" "$d/relay"
[ ! -L "$d/relay" ] || exit 86
chmod 0755 "$d/relay"
[ ! -e /tmp/bench-touch-events ] || exit 86
/bin/busybox mkfifo /tmp/bench-touch-events
chown 979:19 /tmp/bench-touch-events
chmod 0600 /tmp/bench-touch-events
/usr/bin/chsmack -a hmi::sub /tmp/bench-touch-events
/lib/ld-linux-aarch64.so.1 --list "$d/touch-app" >"$r/loader.stdout" 2>"$r/loader.stderr"
chmod 0644 "$r/loader.stdout" "$r/loader.stderr"
grep -Fq /vw/oem-touch/provider.so "$r/loader.stdout"
printf 'ORIGINAL_TOUCH_FILES_PREPARED\\n'
'''
 prepare_unit='[Unit]\nDescription=Prepare original unified touch hardware provider\nDefaultDependencies=no\nRequires=post-init.service oem-gl-prepare.service\nAfter=post-init.service oem-gl-prepare.service\nBefore=touch-app.service\n[Service]\nType=oneshot\nExecStart=/bin/sh /run/oem-touch/prepare\nTimeoutStartSec=600\nRemainAfterExit=yes\n'
 dropin='[Unit]\nRequires=oem-touch-prepare.service\nAfter=oem-touch-prepare.service\n[Service]\nExecStart=\nExecStart=/vw/oem-touch/touch-app -rsi-log_level=error\nStandardOutput=file:/run/oem-touch/touch.stdout\nStandardError=file:/run/oem-touch/touch.stderr\n'
 unit='[Unit]\nDescription=Virtual physical touch ring to original RSI provider\nDefaultDependencies=no\nRequires=oem-touch-prepare.service\nAfter=oem-touch-prepare.service\n[Service]\nType=simple\nExecStart=/vw/oem-touch/relay\nStandardOutput=file:/run/oem-touch/relay.stdout\nStandardError=file:/run/oem-touch/relay.stderr\n'
 for name,text in dict(prepare=prepare,prepare_unit=prepare_unit,dropin=dropin,unit=unit).items():
  prop='oem-touch-'+name;chosen[prop]=text.encode();prefix+='/bin/cat /sys/firmware/devicetree/base/chosen/'+prop+' >"$d/'+name+'"||exit 86;'
 chosen['oem-touch-manifest']=json.dumps(manifest,sort_keys=True).encode()
 prefix+='p=/run/systemd/system;cp "$d/prepare_unit" "$p/oem-touch-prepare.service";cp "$d/unit" "$p/oem-touch-relay.service";mkdir -p "$p/basic.target.wants" "$p/touch-app.service.d";cp "$d/dropin" "$p/touch-app.service.d/90-unified-provider.conf";ln -s ../oem-touch-relay.service "$p/basic.target.wants/oem-touch-relay.service";'
 return prefix
