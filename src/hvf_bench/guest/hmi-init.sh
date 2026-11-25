#!/bin/sh
BB=/bin/busybox
$BB --install -s /usr/bin
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
$BB mount -t proc proc /proc
$BB mount -t sysfs sysfs /sys
$BB mount -t devtmpfs devtmpfs /dev
$BB ip link set lo up
echo 'HMI_GUEST_BEGIN'
$BB uname -m
$BB modprobe virtio_mmio
$BB modprobe virtio_blk
$BB modprobe squashfs
$BB mdev -s
$BB mkdir -p /mnt/modules /mnt/check /mnt/agl
for disk in /dev/vd?; do
    case "$($BB blkid "$disk" 2>/dev/null)" in *'TYPE="squashfs"'*) ;; *) continue ;; esac
    if $BB mount -t squashfs -o ro "$disk" /mnt/modules 2>/dev/null; then
        $BB mount -o bind /mnt/modules/modules /lib/modules
        break
    fi
done
$BB modprobe ext4
ROOT_DISK=
HMI_DISK=
CUSTOM_DISK=
for disk in /dev/vd?; do
    case "$($BB blkid "$disk" 2>/dev/null)" in *'TYPE="ext4"'*) ;; *) continue ;; esac
    if $BB mount -t ext4 -o ro,noload "$disk" /mnt/check 2>/dev/null; then
        if [ -x /mnt/check/lge/hmi/jvm/bin/java ]; then ROOT_DISK=$disk; fi
        if [ -f /mnt/check/hmi/runHMI.sh ]; then HMI_DISK=$disk; fi
        if [ -f /mnt/check/lib64/libNmeSDK.so ]; then CUSTOM_DISK=$disk; fi
        $BB umount /mnt/check
    fi
done
echo "HMI_ROOT_DISK=$ROOT_DISK"
echo "HMI_ASSET_DISK=$HMI_DISK"
if [ -n "$ROOT_DISK" ] && [ -n "$HMI_DISK" ]; then
    $BB mount -t ext4 -o ro,noload "$ROOT_DISK" /mnt/agl
    $BB mount -t ext4 -o ro,noload "$HMI_DISK" /mnt/agl/vw
    [ -n "$CUSTOM_DISK" ] || { echo ORIGINAL_8704_CUSTOM_MISSING; $BB poweroff -f; }
    $BB mkdir -p /mnt/custom
    $BB mount -t ext4 -o ro,noload "$CUSTOM_DISK" /mnt/custom
    $BB mount -o bind /mnt/custom/lib64 /mnt/agl/usr/lib64
    echo "ORIGINAL_8704_CUSTOM_DISK=$CUSTOM_DISK"
    for directory in proc sys dev; do
        $BB mount -o bind "/$directory" "/mnt/agl/$directory"
    done
    for directory in tmp run var data; do
        $BB mount -t tmpfs tmpfs "/mnt/agl/$directory"
    done
    $BB mkdir -p /mnt/agl/run/platform/weston /mnt/agl/var/lib/hmi /mnt/agl/data/lge/hmi
    prepare_shared_memory() {
        for directory in "$1/dev/shm" "$1/data/sharedmemory"; do
            [ ! -L "$directory" ] || return 1
            $BB mkdir -p "$directory" || return 1
            [ -d "$directory" ] || return 1
            $BB chmod 0777 "$directory" || return 1
        done
    }
    if ! prepare_shared_memory /mnt/agl; then
        echo 'ORIGINAL_SHARED_MEMORY_SETUP_FAILED'
        while :; do $BB sleep 60; done
    fi
    echo 'ORIGINAL_SHARED_MEMORY_READY'
    $BB modprobe virtio_console
    $BB mdev -s
    for port_name in /sys/class/virtio-ports/*/name; do
        if [ "$($BB cat "$port_name" 2>/dev/null)" = bench.control ]; then
            control_directory=${port_name%/name}
            control_device=/dev/${control_directory##*/}
            (
                exec 3<>"$control_device"
                while :; do
                    $BB sh <&3 >&3 2>&1
                    $BB sleep 1
                done
            ) &
            echo "BENCH_CONTROL_READY=$control_device"
        elif [ "$($BB cat "$port_name" 2>/dev/null)" = bench.input ]; then
            input_directory=${port_name%/name}
            input_device=/dev/${input_directory##*/}
            echo "$input_device" > /mnt/agl/tmp/bench-input.device
            echo "BENCH_INPUT_DEVICE=$input_device"
        fi
    done
    if [ -f /bench-gpu-virgl ]; then
        $BB mkdir -p /mnt/agl/tmp/mesa
        $BB tar -xzf /mesa-runtime.tar.gz -C /mnt/agl/tmp/mesa
        $BB ln -sf swrast_dri.so /mnt/agl/tmp/mesa/usr/lib/aarch64-linux-gnu/dri/virtio_gpu_dri.so
        $BB cp /bench-gpu-virgl /mnt/agl/tmp/bench-gpu-virgl
        $BB cp /virgl-drm-compat.so /mnt/agl/tmp/virgl-drm-compat.so
        $BB cp /native-pointer.py /mnt/agl/tmp/native-pointer.py
        $BB mkfifo /mnt/agl/tmp/bench-native-input
        $BB ln -s /tmp/bench-native-input /mnt/agl/dev/vport99p0
        echo /dev/vport99p0 > /mnt/agl/tmp/bench-input.device
        echo 'VIRGL_RUNTIME_STAGED'
    fi
    if [ -f /display-setup ]; then
        . /display-setup
        if [ "$WESTON_READY" != true ]; then
            echo 'DISPLAY_SETUP_FAILED'
            echo 'HMI_PROBE_DONE'
            $BB poweroff -f
            while :; do $BB sleep 1; done
        fi
    fi
    if [ -f /bench-gpu-virgl ]; then
        $BB chroot /mnt/agl /usr/bin/python3 /tmp/native-pointer.py > /mnt/agl/tmp/native-pointer.log 2>&1 &
        echo "NATIVE_POINTER_PID=$!"
    fi
    if [ -f /update-service ]; then $BB sh /update-service; fi
    if [ -f /egl-forward-trace.so ]; then
        $BB cp /egl-forward-trace.so /mnt/agl/tmp/libegl-forward-trace.so
    fi
    if [ -f /platform-setup ]; then
        . /platform-setup
        if [ "$PLATFORM_READY" != true ]; then
            echo 'PLATFORM_SETUP_FAILED'
            for log in /mnt/agl/tmp/registry.log /mnt/agl/tmp/rsi-mockup.log; do
                $BB tail -n 40 "$log"
            done
            echo 'HMI_PROBE_DONE'
            echo 'PLATFORM_FAILURE_DEBUG_CONSOLE_RETAINED'
            while :; do $BB sleep 60; done
            while :; do $BB sleep 1; done
        fi
    fi
    if [ -f /native-rsi-setup ]; then $BB sh /native-rsi-setup; fi
    if [ -f /mesa-runtime.tar.gz ] && [ ! -d /mnt/agl/tmp/mesa ]; then
        $BB mkdir -p /mnt/agl/tmp/mesa
        $BB tar -xzf /mesa-runtime.tar.gz -C /mnt/agl/tmp/mesa
        echo 'SOFTWARE_MESA_UNPACKED_IN_RAM'
    fi
    if [ -f /navigation-uplink.conf ]; then
        if ! $BB sh /navigation-network; then
            echo NAVIGATION_UPLINK_SETUP_FAILED
            while :; do $BB sleep 60; done
        fi
    fi
    if [ -f /navigation-setup ]; then
        if ! $BB sh /navigation-memory; then
            echo NAVIGATION_MEMORY_SETUP_FAILED
            while :; do $BB sleep 60; done
        fi
        $BB sh /navigation-setup
    fi
    if [ -f /host-clock-setup ]; then
        . /host-clock-setup
        if [ "$HOST_CLOCK_READY" != true ]; then
            echo 'HOST_CLOCK_SETUP_FAILED'
            $BB poweroff -f
            while :; do $BB sleep 1; done
        fi
    fi
    if [ -f /gateway-setup ]; then
        . /gateway-setup
        if [ "$GUEST_GATEWAY_READY" != true ]; then
            echo 'GUEST_GATEWAY_SETUP_FAILED'
            for log in /mnt/agl/tmp/api-relay.log /mnt/agl/tmp/sdk-relay.log /mnt/agl/tmp/gateway-b2b.log /mnt/agl/tmp/gateway-b2c.log; do
                $BB tail -n 35 "$log"
            done
            $BB poweroff -f
            while :; do $BB sleep 1; done
        fi
    fi
    if [ -f /bench-platform.jar ]; then
        $BB cp /bench-platform.jar /mnt/agl/tmp/bench-platform.jar
    fi
    if [ -f /bench-vehicle.properties ]; then
        $BB cp /bench-vehicle.properties /mnt/agl/tmp/bench-vehicle.properties
    fi
    if [ -f /bench-encoded-vehicle-type ]; then
        $BB cp /bench-encoded-vehicle-type /mnt/agl/tmp/bench-encoded-vehicle-type || exit 1
    fi
    if [ -f /display-map ]; then
        $BB sh /display-map >/mnt/agl/tmp/display-map.log 2>&1 &
    fi
    HMI_SECONDS=35
    if [ -f /bench-disable-animations ]; then
        $BB cp /bench-disable-animations /mnt/agl/tmp/bench-disable-animations
    fi
    if [ -f /hmi-seconds ]; then HMI_SECONDS=$($BB cat /hmi-seconds); fi
    if [ -f /bench-thread-dump ]; then
        (
            $BB sleep 15
            if [ -f /mnt/agl/tmp/hmi.pid ]; then
                echo 'ORIGINAL_HMI_THREAD_DUMP_REQUESTED'
                $BB kill -QUIT "$($BB cat /mnt/agl/tmp/hmi.pid)"
            fi
        ) &
    fi
    if [ -f /bench-diagApi.properties ]; then
        $BB cp /bench-diagApi.properties /mnt/agl/tmp/bench-diagApi.properties || exit 1
        $BB mount -o bind /mnt/agl/tmp/bench-diagApi.properties /mnt/agl/vw/hmi/config/diagApi.properties || exit 1
        $BB cp /bench-ric-id3-coding.bin /mnt/agl/tmp/bench-ric-id3-coding.bin || exit 1
        if [ -f /bench-diagnosis-records.properties ]; then
            $BB cp /bench-diagnosis-records.properties /mnt/agl/tmp/bench-diagnosis-records.properties || exit 1
        fi
        echo BENCH_RIC_ID3_CODING_INPUT_READY
    fi
    if [ -f /bench-car-configuration.sha256 ]; then
        (cd /mnt/agl && $BB sha256sum -c /bench-car-configuration.sha256) >/mnt/agl/tmp/car-configuration-check.txt 2>&1 || {
            $BB cat /mnt/agl/tmp/car-configuration-check.txt
            echo IMPORTED_CAR_CONFIGURATION_MISMATCH
            exit 1
        }
        echo "IMPORTED_CAR_CONFIGURATION_VERIFIED files=$($BB wc -l < /bench-car-configuration.sha256)"
    fi
    echo 'ORIGINAL_MIBMAIN_BEGIN'
    $BB chroot /mnt/agl /bin/sh -c '
        export LD_LIBRARY_PATH=/lge/hmi/libs:/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib
        export XDG_RUNTIME_DIR=/run/platform/weston
        export PATH=/usr/bin:/usr/sbin:/bin:/sbin
        export BENCH_MISSING_IOCTL_BACKOFF_MS=10
        graphics_options=
        animation_options=
        if [ -f /tmp/bench-disable-animations ]; then
            animation_options=-Dde.vw.mib.widgets.disableanimations=true
            echo "BENCH_VIEW_ANIMATIONS=disabled property=de.vw.mib.widgets.disableanimations"
        else
            echo "BENCH_VIEW_ANIMATIONS=original"
        fi
        gateway_options=
        if [ -f /tmp/hmi-http-gateway.options ]; then gateway_options=$(cat /tmp/hmi-http-gateway.options); fi
        input_options=
        if [ -f /tmp/bench-input.device ]; then
            input_options="-Dbench.input.8704=true -Dbench.input.device=$(cat /tmp/bench-input.device) -Dbench.vehicle.rsi=/tmp/bench-vehicle.properties"
            if [ ! -f /tmp/bench-gpu-virgl ]; then
                input_options="$input_options -Dbench.vehicle.control=true -Dde.vw.mib.powermanagement.usersi=1"
            fi
        fi
        if [ -d /tmp/mesa ]; then
            export LD_LIBRARY_PATH=/tmp/mesa/usr/lib/aarch64-linux-gnu:/tmp/mesa/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH
            export LIBGL_DRIVERS_PATH=/tmp/mesa/usr/lib/aarch64-linux-gnu/dri
            export __EGL_VENDOR_LIBRARY_FILENAMES=/tmp/mesa/usr/share/glvnd/egl_vendor.d/50_mesa.json
            if [ -f /tmp/bench-gpu-virgl ]; then
                unset LIBGL_ALWAYS_SOFTWARE GALLIUM_DRIVER
                export MESA_LOADER_DRIVER_OVERRIDE=virtio_gpu
                echo 'BENCH_RENDERING=virgl'
            else
                export LIBGL_ALWAYS_SOFTWARE=1
                export GALLIUM_DRIVER=llvmpipe
            fi
            export LP_NUM_THREADS=2
            export EGL_PLATFORM=wayland
            export MESA_DEBUG=1
            graphics_options=-Dde.vw.mib.graphics.gles.swapinterval=0
            echo 'MESA_DRI_LOADER_BEGIN'
            /lib/ld-linux-aarch64.so.1 --list /tmp/mesa/usr/lib/aarch64-linux-gnu/dri/swrast_dri.so
            echo "MESA_DRI_LOADER_EXIT=$?"
            echo 'MESA_DRI_LOADER_END'
        fi
        ulimit -c 0
        cd /tmp
        jars=
        for jar in /vw/hmi/jar/*.jar /vw/hmi/lib/*.jar /lge/hmi/jar/*.jar; do
            if [ -f "$jar" ]; then jars="${jars:+$jars:}$jar"; fi
        done
        car_model_options=-Dde.vw.mib.configuration.encodedVehicleType=0203010603000000
        if [ -f /tmp/bench-diagApi.properties ]; then
            car_model_options=-Dbench.diagnosis.coding=/tmp/bench-ric-id3-coding.bin
            if [ -f /tmp/bench-diagnosis-records.properties ]; then
                car_model_options="$car_model_options -Dbench.diagnosis.records=/tmp/bench-diagnosis-records.properties"
            fi
            echo BENCH_NATIVE_DIAGNOSIS_VEHICLE_TYPE
        fi
        if [ -f /tmp/bench-encoded-vehicle-type ]; then
            car_model_id=$(cat /tmp/bench-encoded-vehicle-type)
            case "$car_model_id" in *[!0-9]*) exit 1;; esac
            [ "${#car_model_id}" -eq 21 ] || exit 1
            [ -s "/vw/hmi/Resources/skin0/Scenes/$car_model_id/car.mcf" ] || exit 1
            car_model_options="-Dde.vw.mib.configuration.encodedVehicleType=$car_model_id"
            echo "BENCH_ENCODED_VEHICLE_TYPE=$car_model_id"
        fi
        main_class=de.vw.mib.MIBMain
        if [ -f /tmp/bench-platform.jar ]; then
            jars="$jars:/tmp/bench-platform.jar"
            main_class=bench.platform.BenchPlatform
        fi
        echo "HMI_CLASSPATH=$jars"
        echo $$ > /tmp/hmi.pid
        export LD_PRELOAD=/lge/hmi/jvm/lib/aarch64/libjsig.so:/lge/hmi/libs/libImageLoader.so:/usr/lib64/libNmeSDK.so:/lge/hmi/libs/libDebugSPINative.so
        if [ -f /tmp/libegl-forward-trace.so ]; then
            export LD_PRELOAD=/tmp/libegl-forward-trace.so:$LD_PRELOAD
        fi
        exec /lge/hmi/jvm/bin/java -Xms128m -Xmx296m $graphics_options $input_options $gateway_options $animation_options \
          -XX:+TieredCompilation -XX:TieredStopAtLevel=1 -XX:CICompilerCount=2 \
          -XX:+UseParallelOldGC -XX:ParallelGCThreads=2 \
          -XX:ErrorFile=/tmp/hmi-hs-error.log \
          -Djava.library.path=/lge/hmi/libs \
          -Dsun.boot.library.path=/lge/hmi/libs:/lge/app_ro/lib64:/usr/lib64:/usr/lib:/lib:/lge/hmi/jvm/lib/aarch64:/lge/hmi/jvm/lib/aarch64/server \
          -Dde.vw.mib.startup.order=MEB_ASIA \
          -Dde.vw.mib.asl.startup.enableCollectivePersistenceLoading=true \
          -DuseStartupBootExtension \
          -DdiagApi.properties=/vw/hmi/config/diagApi.properties \
          -Dde.vw.mib.promiseorder=2 \
          -Dde.vw.mib.rsi.event.resource_with_slash=true \
          -Dde.vw.mib.view.gfx.init.eager \
          -Dde.vw.mib.graphics.display.layout.direct \
          -Dsun.zip.disableMemoryMapping=true \
          -Dfile.encoding=UTF-8 \
          -Ddebugprobes.config.path=/vw/hmi/config \
          -Ddsiexclude.properties=/vw/hmi/config/dsiexclude.properties \
          -Dde.vw.mib.rsi.framework.predefined.services=false \
          -Dde.vw.mib.rsi.framework.server.port=80 \
          -Dde.vw.mib.rsi.framework.server.name=::1 \
          -Dde.vw.mib.rsi.framework.server.basepath=/ \
          -Dde.vw.mib.rsi.framework.server.eventbase=/ \
          -Dde.vw.mib.installation.directory=/vw/hmi \
          -Dde.vw.mib.configuration.directory=/vw/hmi/config \
          $car_model_options \
          -Dde.vw.mib.resources.directory=/vw/hmi/Resources \
          -Dvw.mib.resources=/vw/hmi/res \
          -Dbundles.properties=/vw/hmi/config/bundles_LG.properties \
          -Dlogging.properties=/vw/hmi/config/logging.properties \
          -Dthreads.properties=/vw/hmi/config/threads.properties \
          -Dsystem.properties=/vw/hmi/config/system.properties \
          -Dviewhandler.format=jar \
          "-Xbootclasspath/a:$jars" "$main_class"
    '
    echo "ORIGINAL_MIBMAIN_EXIT=$?"
    if [ -f /mnt/agl/tmp/hmi-hs-error.log ]; then
        echo 'HMI_VM_ERROR_LOG_BEGIN'
        $BB tail -n 100 /mnt/agl/tmp/hmi-hs-error.log
        echo 'HMI_VM_ERROR_LOG_END'
    fi
    echo 'ORIGINAL_MIBMAIN_END'
    for log in /mnt/agl/tmp/registry.log /mnt/agl/tmp/rsi-mockup.log; do
        echo "PLATFORM_LOG=$log"
        $BB tail -n 65 "$log"
    done
    echo 'DISPLAY_MAP_LOG_BEGIN'
    $BB tail -n 100 /mnt/agl/tmp/display-map.log
    echo 'DISPLAY_MAP_LOG_END'
else
    echo 'HMI_DISK_DISCOVERY_FAILED'
fi
echo 'HMI_PROBE_DONE'
$BB poweroff -f
while :; do $BB sleep 1; done
