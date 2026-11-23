#!/bin/sh
B=/bin/busybox
set -e
DATA_DISK=
for node in /sys/block/vd*/serial; do
 if [ "$(cat "$node")" = bench-data ]; then d=${node%/serial};DATA_DISK=/dev/${d##*/};fi
done
[ -n "$DATA_DISK" ] || { echo ANDROID_PRIVATE_DATA_MISSING;exit 1; }
if ! blkid "$DATA_DISK" | grep -q 'TYPE="ext4"'; then
 chroot /android /system/bin/mke2fs -t ext4 -O ^metadata_csum,^64bit -m 0 "$DATA_DISK"
fi
mkdir -p /android/metadata /android/data
mount -t ext4 -o noatime "$DATA_DISK" /android/data
mkdir -p /android/dev/block/by-name /android/data/local/tmp /android/data/cache
ln -sf "$DATA_DISK" /android/dev/block/by-name/userdata
chmod 1777 /android/data/local/tmp
for node in /sys/block/vd*/serial; do
 if [ "$(cat "$node")" = bench-misc ]; then d=${node%/serial};ln -sf /dev/${d##*/} /android/dev/block/by-name/misc;fi
done
mkdir -p /rw/sysinit/hw /rw/vendorinit
cp -a /android/system/etc/init/. /rw/sysinit/
for rc in /android/vendor/etc/init/*.rc; do
 case "$rc" in */init.exynosauto9.rc)continue;; esac
 cp "$rc" /rw/vendorinit/
done
cp /android/system/etc/init/hw/init.zygote64_32.rc /rw/sysinit/hw/init.zygote64.rc
sed -i '/^service zygote_secondary /,$d' /rw/sysinit/hw/init.zygote64.rc
cp /android/system/etc/prop.default /rw/prop.default
cp /android/vendor/default.prop /rw/vendor.default.prop
cp /android/system/build.prop /rw/system.build.prop
cp /android/vendor/build.prop /rw/vendor.build.prop
sed -i 's/^ro\.zygote=zygote64_32$/ro.zygote=zygote64/' /rw/prop.default /rw/vendor.default.prop
for pf in /rw/system.build.prop /rw/vendor.build.prop; do
 sed -i -e 's/^ro\.product\.cpu\.abilist=.*/ro.product.cpu.abilist=arm64-v8a/' -e 's/^ro\.product\.cpu\.abilist32=.*/ro.product.cpu.abilist32=/' -e 's/^ro\.vendor\.product\.cpu\.abilist=.*/ro.vendor.product.cpu.abilist=arm64-v8a/' -e 's/^ro\.vendor\.product\.cpu\.abilist32=.*/ro.vendor.product.cpu.abilist32=/' "$pf"
done
mount -o bind /lower/system/system /android/system
mount -o bind /rw/sysinit /android/system/etc/init
mount -o bind /rw/vendorinit /android/vendor/etc/init
mount -o bind /rw/prop.default /android/system/etc/prop.default
mount -o bind /rw/vendor.default.prop /android/vendor/default.prop
mount -o bind /rw/system.build.prop /android/system/build.prop
mount -o bind /rw/vendor.build.prop /android/vendor/build.prop
for dir in bin lib lib64; do mount -o bind /lower/vendor/$dir /android/vendor/$dir;done
cp /bench-keystore-software /android/bench/keystore-software
chmod 755 /android/bench/keystore-software
mount -o bind /android/bench/keystore-software /android/system/bin/keystore
sed -i '/^service keystore /a\    seclabel u:r:keystore:s0' /rw/sysinit/keystore.rc
mkdir -p /android/vendor/odm/etc/vintf
printf '<manifest version="2.0" type="device"><hal format="hidl" override="true"><name>android.hardware.keymaster</name><transport>hwbinder</transport></hal></manifest>\n' >/android/vendor/odm/etc/vintf/manifest.xml
rm -f /rw/vendorinit/android.hardware.keymaster@4.0_tee-service.rc
printf '/dev/block/by-name/userdata /data ext4 noatime,nosuid,nodev,discard wait,latemount,check,quota\n/dev/block/by-name/misc /misc emmc defaults defaults\n' >/android/bench/fstab.bench
mount -o bind /android/bench/fstab.bench /android/vendor/etc/fstab.exynosauto9
cp /android/bench/fstab.bench /android/vendor/etc/fstab.bench
cat > /rw/vendorinit/init.bench.rc <<'RC'
on early-init
    setprop ro.hardware.gralloc bench
    setprop ro.hardware.hwcomposer bench
    setprop ro.hardware.egl swiftshader
    setprop ro.sf.lcd_density 160
    setprop ro.opengles.version 196608
    setprop ro.zygote.disable_gl_preload true
on fs
    mount_all /vendor/etc/fstab.exynosauto9 --early
on late-fs
    mount_all /vendor/etc/fstab.exynosauto9 --late
on init
    start bench-control
service bench-control /bench/busybox sh /bench/control.sh
    class core
    user root
    group root shell
    seclabel u:r:shell:s0
    disabled
RC
cp /framework-control /android/bench/control.sh
chmod 755 /android/bench/control.sh
mkdir -p /rw/egl /rw/ework /rw/hw /rw/hwork
mount -t overlay overlay -o lowerdir=/lower/vendor/lib64/egl,upperdir=/rw/egl,workdir=/rw/ework /android/vendor/lib64/egl
mount -t overlay overlay -o lowerdir=/lower/vendor/lib64/hw,upperdir=/rw/hw,workdir=/rw/hwork /android/vendor/lib64/hw
for name in libEGL_swiftshader.so libGLESv1_CM_swiftshader.so libGLESv2_swiftshader.so; do cp /bench-$name /android/vendor/lib64/egl/$name; done
cp /bench-gralloc.bench.so /android/vendor/lib64/hw/gralloc.bench.so
cp /bench-hwcomposer.bench.so /android/vendor/lib64/hw/hwcomposer.bench.so
for node in /sys/class/virtio-ports/*/name; do
 if [ "$(cat "$node")" = bench.display ]; then
  port=${node%/name};port=/dev/${port##*/}
  ln -sf "$port" /android/dev/bench-display
  chown 1000:1003 "$port"
  chmod 0660 "$port"
 fi
done
mount -t selinuxfs selinuxfs /android/sys/fs/selinux
B=/bin/busybox
$B dd if=/android/vendor/etc/selinux/precompiled_sepolicy of=/android/sys/fs/selinux/load bs=4M
printf 0 >/android/sys/fs/selinux/enforce
mkdir -p /android/dev/pts /android/dev/socket
mount -t devpts devpts /android/dev/pts
for node in binder hwbinder vndbinder ashmem random urandom null;do [ ! -e /android/dev/$node ] || chmod 666 /android/dev/$node;done
umount /android/apex/com.android.runtime
umount /android/data
export INIT_SECOND_STAGE=true INIT_STARTED_AT=0 INIT_SELINUX_TOOK=0
cd /
echo ANDROID_ORIGINAL_SECOND_STAGE_EXEC
exec switch_root /android /system/bin/init second_stage
