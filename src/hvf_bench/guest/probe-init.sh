#!/bin/sh
BB=/bin/busybox
$BB --install -s /usr/bin
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
$BB mount -t proc proc /proc
$BB mount -t sysfs sysfs /sys
$BB mount -t devtmpfs devtmpfs /dev
echo 'ARM64_GUEST_BEGIN'
$BB uname -m
$BB uname -r
echo 'ARM64_GUEST_END'
$BB modprobe virtio_mmio
$BB modprobe virtio_blk
$BB modprobe squashfs
$BB mdev -s
$BB mkdir -p /mnt/modules
for disk in /dev/vd?; do
    case "$($BB blkid "$disk" 2>/dev/null)" in *'TYPE="squashfs"'*) ;; *) continue ;; esac
    if $BB mount -t squashfs -o ro "$disk" /mnt/modules 2>/dev/null; then
        $BB mount -o bind /mnt/modules/modules /lib/modules
        echo "COMPLETE_KERNEL_MODULES_MOUNTED=$disk"
        break
    fi
done
$BB modprobe ext4
$BB mdev -s
$BB mkdir -p /mnt/agl
mounted=false
for disk in /dev/vd?; do
    case "$($BB blkid "$disk" 2>/dev/null)" in *'TYPE="ext4"'*) ;; *) continue ;; esac
    if $BB mount -t ext4 -o ro,noload "$disk" /mnt/agl 2>/dev/null; then
        mounted=true
        break
    fi
done
if [ "$mounted" = true ]; then
    echo 'FIRMWARE_ROOT_MOUNTED_READONLY'
    $BB mount -t proc proc /mnt/agl/proc
    $BB mount -t tmpfs tmpfs /mnt/agl/tmp
    $BB mount -t devtmpfs devtmpfs /mnt/agl/dev
    echo 'FIRMWARE_JVM_BEGIN'
    $BB chroot /mnt/agl /lge/hmi/jvm/bin/java -version
    echo "FIRMWARE_JVM_EXIT=$?"
    echo 'FIRMWARE_JVM_END'
else
    echo 'FIRMWARE_ROOT_NOT_MOUNTED'
fi
echo 'ARM64_PROBE_DONE'
$BB poweroff -f
while :; do $BB sleep 1; done
