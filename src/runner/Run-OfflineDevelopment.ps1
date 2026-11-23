param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$OutputName,
    [ValidateRange(65,7200)][int]$Seconds=240,
    [switch]$AndroidWritable,
    [switch]$Dsi,
    [switch]$DpAuxSink,
    [switch]$SftControl,
    [switch]$UsbPmu,
    [string]$AndroidParent,
    [string]$LinuxParent,
    [string]$LinuxHmiParent,
    [string]$QnxParent,
    [string]$Qemu,
    [switch]$MaliJm,
    [switch]$MaliControlAs0,
    [switch]$QnxMaliJm,
    [switch]$QnxMaliControlJobs,
    [switch]$WatchMaliSubmit,
    [switch]$OemGettyInstance,
    [switch]$LinuxFactoryBank,
    [switch]$QnxFactoryBank,
    [switch]$QnxBts,
    [switch]$StageOemRunGetty,
    [switch]$StageOemRunGettyAlias,
    [switch]$LocalLinuxShell,
    [switch]$LocalAndroidShell,
    [switch]$ProvisionNavData,
    [switch]$PrepareLinuxSeed,
    [switch]$AcceleratedLinuxSeed,
    [switch]$PrioritizeOemPostInit,
    [switch]$NativeHmiStartGrace,
    [switch]$NativeHmiInterpreter,
    [switch]$NativeHmiFlightRecording,
    [switch]$NativeHmiVmLog,
    [switch]$NativeWaylandTrace,
    [switch]$NativeStockWeston,
    [switch]$NativePerformanceCores,
    [switch]$NativeParallelTcg,
    [switch]$NativeTeeObserve,
    [switch]$NativeHmiSignalObserve,
    [switch]$InfoNativeBoot,
    [switch]$NativeCenterScanout,
    [switch]$KeepLinuxOnFailure,
    [switch]$LinuxPixman,
    [switch]$QnxPhy88q2112,
    [ValidateSet('iid-arhud')][string]$QnxDisplayProfile,
    [switch]$HostFileRam=$true,
    [Nullable[uint32]]$PmuOmInput,
    [ValidateRange(0,65535)][int]$ConsolePort=0,
    [string]$LinuxHmiOverlay,
    [ValidateSet(0,1,2,4)][int]$ClockShift=0,
    [switch]$ExtendedObservation
)
$ErrorActionPreference = 'Stop'
$instructionClock=$env:OEM_8704_OFFLINE_CLOCK -ceq 'icount-shift4-v1'
if ($env:OEM_8704_OFFLINE_CLOCK -and (-not $instructionClock -or $env:OEM_8704_OFFLINE_DEVELOPMENT -cne 'android-private-unencrypted-v1' -or $NativeParallelTcg -or $ClockShift -ne 4)) { throw 'Invalid explicit offline instruction clock.' }
if ($ClockShift -eq 4 -and -not $instructionClock) { throw 'Shift4 requires explicit offline instruction-clock profile.' }
$routineSeconds=240
$stockLive=$false
if ($env:OEM_8704_STOCK_LIVE_SECONDS) {
    if ($env:OEM_8704_STOCK_LIVE_SECONDS -notin @('600','1200') -or
        $env:OEM_8704_STOCK_LIVE_SECONDS -cne [string]$Seconds -or -not $ExtendedObservation -or
        -not $NativeStockWeston -or (-not $NativeParallelTcg -and -not $instructionClock) -or $PrepareLinuxSeed) {
        throw 'Invalid explicit stock live observation profile.'
    }
    $stockLive=$true
    $routineSeconds=$Seconds
}
$androidStockTiming=$env:OEM_8704_ANDROID_STOCK_TIMING -eq '1'
if ($androidStockTiming -and ($Seconds -ne $routineSeconds -or -not $NativeParallelTcg -or
    -not $LinuxFactoryBank -or -not $QnxFactoryBank -or -not $AndroidWritable)) {
    throw 'Android stock timing requires the routine parallel three-domain native run.'
}
if ($Seconds -gt $routineSeconds -and -not $ExtendedObservation) {
    throw "Routine native verification is limited to $routineSeconds seconds. Use -ExtendedObservation for a specific later boot milestone."
}
if ($Seconds -gt $routineSeconds -and -not $LinuxFactoryBank) { throw 'Extended observation requires -LinuxFactoryBank' }
if ($Seconds -gt 1200 -and -not $QnxFactoryBank) { throw 'Extended cold startup requires -QnxFactoryBank' }
if ($QnxParent -and (-not $QnxFactoryBank -or $Seconds -gt $routineSeconds)) {
    throw "-QnxParent requires a normal native QNX run up to $routineSeconds seconds."
}
if ($NativeTeeObserve -and (-not $QnxFactoryBank -or $Seconds -gt $routineSeconds)) {
    throw "-NativeTeeObserve requires the original QNX bank in a normal run up to $routineSeconds seconds."
}
if ($NativeHmiSignalObserve -and (-not $LinuxFactoryBank -or $Seconds -gt $routineSeconds)) {
    throw "-NativeHmiSignalObserve requires the original Linux bank in a normal run up to $routineSeconds seconds."
}
if ($AcceleratedLinuxSeed -and (-not $PrepareLinuxSeed -or $Seconds -notin @($routineSeconds,600) -or
    -not $NativeParallelTcg -or -not $NativeStockWeston)) {
    throw '-AcceleratedLinuxSeed requires the native 240- or 600-second stock preparation profile.'
}
if ($PrepareLinuxSeed -and (-not $ExtendedObservation -or ($Seconds -le 600 -and -not $AcceleratedLinuxSeed) -or $Seconds -gt 1200 -or
    -not $LinuxFactoryBank -or -not $LinuxParent -or -not $ProvisionNavData -or
    $LocalLinuxShell -or $OemGettyInstance -or $StageOemRunGetty -or $StageOemRunGettyAlias)) {
    throw '-PrepareLinuxSeed requires a native clone with /navi_data, explicit preparation budget, and no alternate Linux shell.'
}
if ($PrioritizeOemPostInit -and ($PrepareLinuxSeed -or $Seconds -gt $routineSeconds -or
    -not $LinuxFactoryBank -or -not $LinuxParent -or -not $ProvisionNavData)) {
    throw "-PrioritizeOemPostInit requires an explicit native data seed, /navi_data and a normal run up to $routineSeconds seconds."
}
if ($NativeHmiStartGrace -and (-not $PrioritizeOemPostInit -or -not $LocalLinuxShell -or
    $Seconds -gt $routineSeconds -or ($ExtendedObservation -and -not $stockLive))) {
    throw '-NativeHmiStartGrace requires the normal native post-init comparison and original Linux UART.'
}
if ($NativeHmiInterpreter -and -not $NativeHmiStartGrace) { throw '-NativeHmiInterpreter requires -NativeHmiStartGrace.' }
if ($NativeHmiFlightRecording -and (-not $NativeHmiStartGrace -or $NativeHmiInterpreter -or -not $LinuxHmiParent -or $Seconds -ne $routineSeconds)) {
    throw '-NativeHmiFlightRecording requires the native HMI data comparison, original JVM mode and 240 seconds.'
}
if ($NativeHmiVmLog -and (-not $NativeHmiStartGrace -or $NativeHmiInterpreter -or $NativeHmiFlightRecording -or
    -not ($LinuxHmiParent -or $env:OEM_8704_VERIFIED_LINUX_SEED -eq '1') -or $Seconds -ne $routineSeconds)) {
    throw '-NativeHmiVmLog requires a stock JVM with the native HMI data comparison, no flight recorder, and 240 seconds.'
}
if ($NativeWaylandTrace -and (-not ($NativeHmiStartGrace -or $PrepareLinuxSeed) -or (-not $LinuxPixman -and -not $NativeStockWeston))) { throw '-NativeWaylandTrace requires native early Weston and the HMI or seed profile.' }
if ($NativeStockWeston -and ($LinuxPixman -or -not $NativeWaylandTrace -or ($Seconds -gt $routineSeconds -and -not $PrepareLinuxSeed))) { throw '-NativeStockWeston requires a native Wayland-observed cold boot without -LinuxPixman.' }
if ($LinuxHmiParent -and (-not $NativeHmiStartGrace -or $LinuxHmiParent -eq $LinuxParent)) {
    throw '-LinuxHmiParent requires an independent OEM VM3 source and -NativeHmiStartGrace.'
}
if ($NativePerformanceCores -and (-not $LinuxFactoryBank -or $Seconds -gt $routineSeconds)) {
    throw "-NativePerformanceCores requires a native run up to $routineSeconds seconds."
}
if ($NativeParallelTcg -and (-not $LinuxFactoryBank -or ($Seconds -gt $routineSeconds -and -not $PrepareLinuxSeed) -or $NativePerformanceCores -or
    $PSBoundParameters.ContainsKey('ClockShift'))) {
    throw "-NativeParallelTcg is a separate native run up to $routineSeconds seconds, without instruction clock or a CPU Set preference."
}
if ($LocalLinuxShell -and (-not $LinuxFactoryBank -or $ConsolePort -lt 1024 -or
    $OemGettyInstance -or $StageOemRunGetty -or $StageOemRunGettyAlias)) {
    throw '-LocalLinuxShell requires the private Linux factory bank and its loopback console, without other getty staging.'
}
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$output = Join-Path $PSScriptRoot $OutputName
if (Test-Path -LiteralPath $output) { throw 'Output exists; choose a new name' }
if ($Qemu) {
    $Qemu = (Resolve-Path -LiteralPath $Qemu).Path
} else {
    $Qemu = Join-Path $root 'runtime/qemu-system-aarch64.exe'
}
$argsList = @(
    (Join-Path $PSScriptRoot 'trace-offline-live.py'), 'probe'
    '--output', $output, '--seconds', ([string]$Seconds), '--qemu', $qemu
    '--qnx-dp0-reset'
    '--linux-mfc'
    '--qnx-g3d00'
    '--abox-dsp-mailbox'
    '--lk-android-normal'
    '--linux-pid1-context'
    '--linux-smack-observe'
    '--android-task-context'
    '--steps', '0'
    '--trace-mode', 'boot'
    '--board-adapter'
    '--native-ufs'
    '--hcb'
    '--hcb-ram'
    '--payload-sized-qnx'
    '--guest-dtbs'
    '--map-pram'
    '--loader-ram-mib', '1024'
    '--cpus', '8'
    '--load-qnx'
    '--load-linux'
    '--load-android'
    '--rtc-bootstrap'
    '--symbol-directory', 'var/inputs/symbols'
    '--lk-linux-slot', 'a'
    '--acpm-clock-hz', '96000000'
    '--pmic-interface-reset-byte', '0'
    '--pmic-bootstrap-profile', 'virtual-bl2-rev0'
    '--bl2-fvmap-profile', 'virtual-asv0'
    '--pscdc-profile', 'virtual-oem-v1'
    '--pscdc-latency-cycles', '64'
    '--pmic-pmu-input-byte', '0'
    '--pmu-profile', 'virtual-cold-v1'
    '--tmu-profile', 'virtual-qnx-controller-v1'
    '--tmu-temperature-c', '25'
    '--pmu-link-profile', 'virtual-pmlink-v1'
    '--pmu-signal-profile', 'virtual-latched-signals-v1'
    '--pmu-status1204', '0'
    '--pmu-core-status-mask', '0'
    '--apm-wdt-profile', 'virtual-samsung32-v1'
    '--apm-wdt-clock-hz', '96000000'
    '--pmu-domain-profile', 'virtual-sequencer-v1'
    '--pmu-domain-latency-ns', '1000'
    '--pmu-domain-supply-mask', '0xfffff'
    '--pmu-dptx-debug-profile', 'virtual-input-bank-v1'
    '--pmu-dptx-debug-word0', '0'
    '--pmu-dptx-debug-word1', '0'
    '--pmu-dptx-debug-word2', '0'
    '--serializer-profile', 'virtual-ti941-iid-v1'
    '--serializer-powerup-ns', '100000'
    '--serializer-probe-ns', '100000'
    '--ufs-descriptor-profile', 'virtual-samsung-byte-v1'
    '--ufs-vgen-profile', 'virtual-ufs0-host0-v1'
    '--ufs-mailbox', 'virtual-ufs-ph-vh-mailbox-v1'
    '--ufs-vh', 'virtual-ufs-vh-ro-v1'
    '--ufs-boot-luns', 'var/inputs/support/ufs-mailbox-native/boot-luns/boot-luns.json'
    '--ufs-lun0-image', 'var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.json'
    '--ufs-prdt-profile', 'virtual-ufs0-plain-prdt128-v1'
    '--ufs-power-profile', 'virtual-ufs0-power-config-v1'
    '--ufs-prelink-profile', 'virtual-ufs0-prelink-config-v1'
    '--mali-g3d1-id', '0x72110000'
    '--mali-g3d1-discovery', 'virtual-g76-feature-discovery-v1'
    '--mali-g3d1-reset-irq', 'virtual-g76-reset-irq-v1'
    '--mali-g3d1-config', 'virtual-g76-config-v1'
    '--mali-g3d1-empty-power', 'virtual-g76-empty-power-v1'
    '--linux-usi-uart', 'virtual-linux-usi-uart-v1'
    '--g2d-reset', 'virtual-fimg2d-reset-v1'
    '--ufs-uic-trace'
    '--pcie2-atu-profile', 'virtual-pcie2-unrolled-empty-v1'
    '--pcie2-dbi-profile', 'virtual-pcie2-dbi-root-latches-v1'
    '--pcie2-pcs-profile', 'virtual-pcie2-pcs-no-endpoint-v1'
    '--pcie2-phy-profile', 'virtual-pcie2-phy-cold-v1'
    '--pcie2-elbi-profile', 'virtual-pcie2-elbi-cold-v1'
    '--decon-window-profile', 'virtual-dpus0-window-v1'
    '--linux-panic-watch'
    '--dpp-profile', 'virtual-dpus0-dpp-irq-v1'
    '--lk-dsp-load', 'virtual-lk-dsp-ufs-read-v1'
    '--abox-dsp-off', 'virtual-abox-dsp-off-v1'
    '--abox-axiuser', 'virtual-abox-axiuser-v1'
    '--abox-sysmmu', 'virtual-abox-dma-sysmmu-v1'
    '--linux-initcall-trace'
    '--abox-gic', 'virtual-abox-gic-v1'
    '--scaler-reset', 'virtual-linux-scaler-poly-probe-v2'
    '--linux-decon', 'virtual-linux-wod-window-v1'
    '--linux-dpp', 'virtual-linux-dpp-irq-v1'
    '--linux-sysmmu', 'virtual-linux-sysmmu-v7-v1'
    '--sysmmu-profile', 'virtual-sysmmu-v7-discovery-v1'
    '--eqos-mac', '02:87:04:00:00:01'
    '--ufs-phy-isolation-profile', 'virtual-ufs0-phy-isolation-v1'
    '--translate-address', 'linux:0xffffffc0127a5080'
    '--translate-address', 'linux:0xffffffc012ae8704'
    '--translate-address', 'android:0xffffffc021d50020'
    '--translate-address', 'android:0xffffffc021d50030'
    '--translate-address', 'android:0xffffffc013320030'
    '--translate-address', 'android:0xffffffc013328030'
    '--translate-address', 'android:0xffffffc013341000'
    '--ufs-shareability-profile', 'virtual-ufs0-shareability-v1'
    '--cal-init-config-profile', 'virtual-qnx-cal-config-v1'
    '--ect-handoff-profile', 'original-bl2-ect-v1'
    '--lk-pwm-profile', 'virtual-lk-pwm24-v1'
    '--lk-handoff-profile', 'virtual-original-lk-ssu-v1'
    '--usi-spi-profile', 'virtual-linux-spi-v1'
    '--usi-i2c-profile', 'virtual-linux-i2c-v1'
    '--abox-dsp-xtensa', 'var/inputs/support/abox-dsp-native/xtensa-build/qemu-system-xtensa.exe'
)
if (-not $androidStockTiming) { $argsList += @('--android-abox-watch','--android-initcall-trace') }
$argsList += @('--qnx-dp0-phy','--qnx-dp0-link-config','--qnx-dp0-irq')
if ($ExtendedObservation) { $argsList += '--extended-observation' }
if ($DpAuxSink) { $argsList += '--qnx-dp0-aux-sink' }
$argsList += @('--qnx-dpp','--linux-dwc3','--linux-usb-phy')
if ($Dsi) { $argsList += '--qnx-dsi' }
if ($AndroidWritable) { $argsList += '--android-ufs-overlay' }
if ($AndroidParent) { $argsList += @('--android-ufs-parent', [IO.Path]::GetFullPath($AndroidParent)) }
if ($ConsolePort) { $argsList += @('--linux-native-console-port', [string]$ConsolePort) }
if ($LinuxHmiOverlay) { $argsList += @('--linux-hmi-overlay', [IO.Path]::GetFullPath($LinuxHmiOverlay)) }
$argsList += @('--linux-ivi-data','virtual-empty-ivi-data-ext4-v1')
if ($LinuxParent) { $argsList += @('--linux-ivi-parent', [IO.Path]::GetFullPath($LinuxParent)) }
if ($QnxParent) { $argsList += @('--qnx-ufs-parent', [IO.Path]::GetFullPath($QnxParent)) }
if ($MaliControlAs0) {
    if (-not $MaliJm -or -not $LinuxFactoryBank -or $Seconds -gt $routineSeconds -or ($ExtendedObservation -and -not $stockLive)) {
        throw '-MaliControlAs0 requires the native VM3 RAM and a routine 240-second-or-shorter boot.'
    }
    $argsList += @('--mali-g3d1-jm-topology', 'virtual-g76-vm3-control-as0-v1')
} elseif ($MaliJm) { $argsList += @('--mali-g3d1-jm-topology', 'virtual-g76-jm-topology-v1') }
if ($QnxMaliJm) { $argsList += '--qnx-g3d00-jm-topology' }
if ($QnxMaliControlJobs) {
    if (-not $QnxMaliJm) { throw '-QnxMaliControlJobs requires -QnxMaliJm.' }
    $argsList += '--qnx-g3d00-control-jobs'
}
if ($WatchMaliSubmit) { $argsList += '--watch-native-mali-submit' }
if ($OemGettyInstance) { $argsList += '--seed-native-getty-instance' }
if ($LinuxFactoryBank) { $argsList += '--linux-factory-bank' }
if ($QnxFactoryBank) { $argsList += '--qnx-factory-bank' }
if ($QnxBts) { $argsList += '--qnx-bts' }
if ($StageOemRunGetty) { $argsList += '--stage-oem-run-getty' }
if ($StageOemRunGettyAlias) { $argsList += '--stage-oem-run-getty-alias' }
if ($LocalLinuxShell) { $argsList += '--local-linux-shell' }
if ($LocalAndroidShell) {
    if ($ConsolePort -lt 1024) { throw '-LocalAndroidShell requires the loopback vLM console.' }
    $argsList += '--local-android-shell'
}
if ($ProvisionNavData) { $argsList += '--seed-native-nav-data' }
if ($PrepareLinuxSeed) { $argsList += '--prepare-linux-seed' }
if ($AcceleratedLinuxSeed) { $argsList += '--accelerated-linux-seed' }
if ($PrioritizeOemPostInit) { $argsList += '--prioritize-oem-post-init' }
if ($NativeHmiStartGrace) { $argsList += '--native-hmi-start-grace' }
if ($NativeHmiInterpreter) { $argsList += '--native-hmi-interpreter' }
if ($NativeHmiFlightRecording) { $argsList += '--native-hmi-flight-recording' }
if ($NativeHmiVmLog) { $argsList += '--native-hmi-vm-log' }
if ($NativeWaylandTrace) { $argsList += '--native-wayland-trace' }
if ($NativeStockWeston) { $argsList += '--native-stock-weston' }
if ($LinuxHmiParent) { $argsList += @('--native-hmi-userdata-from', $LinuxHmiParent) }
if ($NativePerformanceCores) { $argsList += '--native-performance-cores' }
if ($NativeParallelTcg) { $argsList += '--native-parallel-tcg' }
else { $argsList += @('--icount-shift', ([string]$ClockShift)) }
if ($NativeTeeObserve) { $argsList += '--native-tee-observe' }
if ($NativeHmiSignalObserve) { $argsList += '--native-hmi-signal-observe' }
if ($InfoNativeBoot) { $argsList += '--linux-systemd-info' }
if ($NativeCenterScanout) { $argsList += '--native-center-scanout' }
if ($KeepLinuxOnFailure) { $argsList += '--keep-linux-on-failure' }
if ($LinuxPixman) { $argsList += '--linux-pixman' }
if ($QnxPhy88q2112) { $argsList += '--qnx-phy88q2112' }
if ($QnxDisplayProfile) { $argsList += @('--qnx-display-profile',$QnxDisplayProfile) }
if ($HostFileRam) { $argsList += '--host-file-ram' }
if ($PSBoundParameters.ContainsKey('PmuOmInput')) { $argsList += @('--pmu-om-input', [string]$PmuOmInput) }

$hash=[Security.Cryptography.SHA256]::Create()
try{
    $id=[BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToUpperInvariant()))).Replace('-','')
}finally{$hash.Dispose()}
$lease=[Threading.Mutex]::new($false,('Local\MEB8704-Native-'+$id))
$held=$false
try {
    try{$held=$lease.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
    if(-not $held){throw 'This checkout already has an active native boot runner.'}
    if(Get-Process -Name qemu-system-aarch64 -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $Qemu}){
        throw 'The requested native QEMU executable is already running.'
    }
    Push-Location -LiteralPath $root
    try {
        & (Join-Path $root '.venv/Scripts/python.exe') @argsList
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    if($held){$lease.ReleaseMutex()}
    $lease.Dispose()
}
exit $rc










