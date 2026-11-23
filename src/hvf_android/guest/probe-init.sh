#!/bin/sh
B=/bin/busybox
$B --install -s /bin
export PATH=/bin:/sbin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /dev
mknod /dev/console c 5 1
mknod /dev/null c 1 3
chmod 666 /dev/null
exec </dev/console >/dev/console 2>&1
echo ANDROID_8704_HVF_BEGIN
uname -a
for name in virtio_mmio virtio_blk virtio_console virtio-rng; do insmod /modules/$name.ko; echo MODULE=$name,EXIT=$?; done
mdev -s
chmod 666 /dev/random /dev/urandom /dev/null
mkdir -p /lower/system /lower/vendor /android /rw
mount -t tmpfs -o size=512m tmpfs /rw
mkdir -p /rw/system /rw/swork /rw/vendor /rw/vwork
SYSTEM_DISK=
VENDOR_DISK=
mkdir -p /check
for disk in /dev/vd?; do
 mount -t ext4 -o ro,noload "$disk" /check || continue
 [ -x /check/system/bin/sh ] && SYSTEM_DISK=$disk
 [ -d /check/lib64/hw ] && VENDOR_DISK=$disk
 umount /check
done
[ -n "$SYSTEM_DISK" ] && [ -n "$VENDOR_DISK" ] || { echo PARTITION_DISCOVERY_FAILED; while :; do sleep 60; done; }
mount -t ext4 -o ro,noload "$SYSTEM_DISK" /lower/system
mount -t ext4 -o ro,noload "$VENDOR_DISK" /lower/vendor
mount -t overlay overlay -o lowerdir=/lower/system,upperdir=/rw/system,workdir=/rw/swork /android
mkdir -p /android/vendor /android/proc /android/sys /android/dev /android/bench /android/lib
mount -t overlay overlay -o lowerdir=/lower/vendor,upperdir=/rw/vendor,workdir=/rw/vwork /android/vendor
mount -t proc proc /android/proc
mount -t sysfs sysfs /android/sys
mount -o bind /dev /android/dev
cp /bin/busybox /android/bench/busybox
cp /lib/ld-musl-aarch64.so.1 /android/lib/ld-musl-aarch64.so.1
mkdir -p /tmp /android/apex/com.android.runtime /android/bench /android/data/local/tmp
unzip -p /android/system/apex/com.android.runtime.apex apex_payload.img >/rw/runtime-apex.img || exit 1
mount -t ext4 -o ro,loop,noload /rw/runtime-apex.img /android/apex/com.android.runtime || exit 1
printf 'root:x:0:0:root:/:/system/bin/sh\nshell:x:2000:2000:Android shell:/data/local/tmp:/system/bin/sh\n' >/android/bench/passwd
mount -o bind /android/bench/passwd /android/system/etc/passwd
chmod 1777 /android/data/local/tmp
chroot /android /system/bin/sh -c 'echo ORIGINAL_ANDROID11_SHELL; /system/bin/id; /system/bin/uname -m; /system/bin/grep ro.system.build.version.release /system/build.prop; /system/bin/ls /system/framework/framework.jar /vendor/etc/selinux/precompiled_sepolicy'
echo ORIGINAL_ANDROID11_SHELL_EXIT=$?
chroot /android /bench/busybox su shell -s /system/bin/sh -c 'id; echo ANDROID_UID2000_READY'
echo ANDROID_SCOPED_SHELL_EXIT=$?
if [ -f /framework-setup ]; then
 . /framework-setup
 echo ANDROID_FRAMEWORK_SETUP_EXIT=$?
fi
for p in /sys/class/virtio-ports/*/name; do
 if [ "$(cat "$p")" = bench.control ]; then
  d=${p%/name};d=/dev/${d##*/}
  (while :; do /bin/sh 3<>"$d" <&3 >&3 2>&1; sleep 1; done)&
  echo ANDROID_CONTROL_READY=$d
 fi
done
echo ANDROID_8704_HVF_PROBE_READY
while :; do sleep 60; done
