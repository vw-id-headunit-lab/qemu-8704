param(
    [ValidateRange(1024,65535)][int]$Port=18740,
    [switch]$NoBrowser,
    [switch]$NativeWindow,
    [switch]$Native,
    [switch]$LocalLinuxShell,
    [switch]$LocalAndroidShell,
    [switch]$QnxMaliControlJobs=$true,
    [switch]$KeepLinuxOnFailure,
    [switch]$LinuxPixman,
    [string]$LinuxDataFrom,
    [string]$LinuxHmiDataFrom,
    [string]$LinuxHmiInstallMedia,
    [string]$QnxDataFrom,
    [switch]$ProvisionNavData,
    [switch]$PrepareLinuxSeed,
    [switch]$SeedSessionOnly,
    [switch]$AcceleratedLinuxSeed,
    [switch]$PrioritizeOemPostInit,
    [switch]$NativeHmiStartGrace,
    [switch]$NativeHmiInterpreter,
    [switch]$NativeHmiFlightRecording,
    [switch]$NativeHmiVmLog,
    [switch]$NativeWaylandTrace,
    [switch]$NativeStockWeston,
    [switch]$NativeMaliControlAs0,
    [switch]$NativePerformanceCores,
    [switch]$NativeParallelTcg,
    [switch]$NativeTeeObserve,
    [switch]$NativeHmiSignalObserve,
    [switch]$InfoNativeBoot,
    [ValidateSet(0,1,2,4)][int]$NativeClockShift=1,
    [switch]$QnxPhy88q2112,
    [ValidateSet('iid-arhud')][string]$QnxDisplayProfile,
    [ValidateRange(1024,65535)][int]$ConsolePort=32228,
    [ValidateRange(65,7200)][int]$Seconds=240,
    [switch]$ExtendedObservation,
    [switch]$OpenBrowser
)
$ErrorActionPreference='Stop'
if ($env:OEM_8704_OFFLINE_DEVELOPMENT -cne 'android-private-unencrypted-v1') { throw 'Explicit offline-development entry required.' }
if ($NoBrowser -and $OpenBrowser) { throw 'Choose either -NoBrowser or -OpenBrowser.' }
if ($NativeWindow -and (-not $Native -or $PrepareLinuxSeed)) { throw '-NativeWindow requires an ordinary native session.' }
if($SeedSessionOnly -and (-not $PrepareLinuxSeed -or -not $AcceleratedLinuxSeed -or -not $NoBrowser -or $OpenBrowser -or $LocalAndroidShell)){
    throw '-SeedSessionOnly requires the accelerated, browser-free seed preparation and no Android console.'
}
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python=Join-Path $root '.venv/Scripts/python.exe'
$rekit=Join-Path $root 'tools/rekit/rekit.py'
$instructionClock=$env:OEM_8704_OFFLINE_CLOCK -ceq 'icount-shift4-v1'
if ($env:OEM_8704_OFFLINE_CLOCK -and (-not $instructionClock -or $env:OEM_8704_OFFLINE_DEVELOPMENT -cne 'android-private-unencrypted-v1' -or $NativeParallelTcg -or $NativeClockShift -ne 4)) { throw 'Invalid explicit offline instruction clock.' }
if ($NativeClockShift -eq 4 -and -not $instructionClock) { throw 'Shift4 requires explicit offline instruction-clock profile.' }
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
if($NativeMaliControlAs0 -and (-not $Native -or $Seconds -gt $routineSeconds -or ($ExtendedObservation -and -not $stockLive))){
    throw '-NativeMaliControlAs0 requires a native routine boot of at most 240 seconds.'
}
if($KeepLinuxOnFailure -and -not $Native){throw '-KeepLinuxOnFailure requires -Native.'}
if($LocalLinuxShell -and -not $Native){throw '-LocalLinuxShell requires -Native.'}
if($LocalAndroidShell -and -not $Native){throw '-LocalAndroidShell requires -Native.'}
if($PSBoundParameters.ContainsKey('QnxMaliControlJobs') -and $QnxMaliControlJobs -and -not $Native){throw '-QnxMaliControlJobs requires -Native.'}
if($LinuxPixman -and -not $Native){throw '-LinuxPixman requires -Native.'}
if($LinuxDataFrom -and -not $Native){throw '-LinuxDataFrom requires -Native.'}
if($LinuxHmiInstallMedia -and (-not $Native -or ($Seconds -gt $routineSeconds -and -not $PrepareLinuxSeed))){
    throw '-LinuxHmiInstallMedia requires a regular native cold boot of at most 240 seconds.'
}
if($QnxDataFrom -and (-not $Native -or $Seconds -gt $routineSeconds)){
    throw "-QnxDataFrom requires a normal native run of at most $routineSeconds seconds."
}
if($NativeTeeObserve -and (-not $Native -or $Seconds -gt $routineSeconds)){
    throw "-NativeTeeObserve requires a normal native run of at most $routineSeconds seconds."
}
if($NativeHmiSignalObserve -and (-not $Native -or $Seconds -gt $routineSeconds)){
    throw "-NativeHmiSignalObserve requires a normal native run of at most $routineSeconds seconds."
}
if($ProvisionNavData -and -not $Native){throw '-ProvisionNavData requires -Native.'}
if($AcceleratedLinuxSeed -and (-not $PrepareLinuxSeed -or $Seconds -notin @($routineSeconds,600) -or
    -not $NativeParallelTcg -or -not $NativeStockWeston -or -not $LinuxHmiInstallMedia -or $OpenBrowser)){
    throw '-AcceleratedLinuxSeed requires the native 240- or 600-second stock preparation profile with original install media and no browser.'
}
if($PrepareLinuxSeed -and (-not $Native -or -not $ExtendedObservation -or -not $LinuxDataFrom -or
   -not $ProvisionNavData -or $LocalLinuxShell -or ($Seconds -le 600 -and -not $AcceleratedLinuxSeed) -or $Seconds -gt 1200)){
    throw '-PrepareLinuxSeed requires a native clone with /navi_data, explicit preparation budget, and no alternate Linux shell.'
}
if($PrioritizeOemPostInit -and (-not $Native -or -not $ProvisionNavData -or -not $LinuxDataFrom -or
   $PrepareLinuxSeed -or $Seconds -gt $routineSeconds)){
    throw "-PrioritizeOemPostInit requires an explicit native data seed, /navi_data and a normal run up to $routineSeconds seconds."
}
if($NativeHmiStartGrace -and (-not $Native -or -not $PrioritizeOemPostInit -or
   -not $LocalLinuxShell -or $Seconds -gt $routineSeconds -or ($ExtendedObservation -and -not $stockLive))){
    throw '-NativeHmiStartGrace requires the normal native post-init comparison and original Linux UART.'
}
if($NativeHmiInterpreter -and -not $NativeHmiStartGrace){throw '-NativeHmiInterpreter requires -NativeHmiStartGrace.'}
if($NativeHmiFlightRecording -and (-not $NativeHmiStartGrace -or $NativeHmiInterpreter -or -not $LinuxHmiDataFrom -or $Seconds -ne $routineSeconds)){
    throw '-NativeHmiFlightRecording requires the native HMI data comparison, original JVM mode and 240 seconds.'
}
$verifiedLinuxSeed=($env:OEM_8704_VERIFIED_LINUX_SEED -eq '1')
if($env:OEM_8704_LINUX_SEED_SPLIT_CPU -eq '1' -and (-not $AcceleratedLinuxSeed -or $Seconds -ne $routineSeconds)){
    throw 'Split CPU placement requires the accelerated 240-second seed rehearsal.'
}
if($env:OEM_8704_QNX_DISPLAY_CPU_SPLIT -eq '1' -and (-not $Native -or -not $NativeParallelTcg -or
   -not $NativeStockWeston -or $PrepareLinuxSeed -or $Seconds -ne $routineSeconds -or
   $env:OEM_8704_FAST_AP_THREADS -ne '1' -or $env:OEM_8704_LINUX_SEED_SPLIT_CPU -eq '1')){
    throw 'QNX display CPU placement requires the routine 240-second stock parallel preset.'
}
if($env:OEM_8704_SEED_NO_DISPLAY -eq '1' -and (-not $AcceleratedLinuxSeed -or
   -not $SeedSessionOnly -or $Seconds -ne $routineSeconds)){
    throw 'Seed display isolation requires the 240-second page-free Linux preparation.'
}
if($env:OEM_8704_SEED_DIRECT_UEVENTS -eq '1' -and (-not $AcceleratedLinuxSeed -or
   -not $SeedSessionOnly -or $Seconds -ne $routineSeconds)){
    throw 'Targeted coldplug requires the 240-second page-free Linux preparation.'
}
if($verifiedLinuxSeed -and (-not $Native -or -not $NativeStockWeston -or -not $LinuxDataFrom -or
    $LinuxHmiDataFrom -or $PrepareLinuxSeed -or -not $NativeHmiStartGrace -or -not $ProvisionNavData -or
    $Seconds -ne $routineSeconds -or ($ExtendedObservation -and -not $stockLive))){
    throw 'Verified Linux seed requires the complete paired data source and the stock 240-second run.'
}
if($NativeHmiVmLog -and (-not $NativeHmiStartGrace -or $NativeHmiInterpreter -or $NativeHmiFlightRecording -or
    -not ($LinuxHmiDataFrom -or $verifiedLinuxSeed) -or $Seconds -ne $routineSeconds)){
    throw '-NativeHmiVmLog requires a stock JVM with the native HMI data comparison, no flight recorder, and 240 seconds.'
}
if($NativeWaylandTrace -and -not ($NativeHmiStartGrace -or $PrepareLinuxSeed)){throw '-NativeWaylandTrace requires the HMI profile or seed preparation.'}
if($NativeStockWeston -and (-not $Native -or -not $NativeWaylandTrace -or $LinuxPixman -or ($Seconds -gt $routineSeconds -and -not $PrepareLinuxSeed))){
    throw '-NativeStockWeston requires a regular native Wayland-observed cold boot without -LinuxPixman.'
}
if($LinuxHmiDataFrom -and -not $NativeHmiStartGrace){throw '-LinuxHmiDataFrom requires -NativeHmiStartGrace.'}
if($NativePerformanceCores -and (-not $Native -or $Seconds -gt $routineSeconds)){
    throw "-NativePerformanceCores requires a native run up to $routineSeconds seconds."
}
if($NativeParallelTcg -and (-not $Native -or ($Seconds -gt $routineSeconds -and -not $PrepareLinuxSeed) -or $NativePerformanceCores -or
   $PSBoundParameters.ContainsKey('NativeClockShift'))){
    throw "-NativeParallelTcg is a separate native run up to $routineSeconds seconds, without instruction clock or a CPU Set preference."
}
if($InfoNativeBoot -and -not $Native){throw '-InfoNativeBoot requires -Native.'}
if($PSBoundParameters.ContainsKey('NativeClockShift') -and -not $Native){throw '-NativeClockShift requires -Native.'}
if($QnxPhy88q2112 -and -not $Native){throw '-QnxPhy88q2112 requires -Native.'}
if($QnxDisplayProfile -and -not $Native){throw '-QnxDisplayProfile requires -Native.'}
if($ExtendedObservation -and -not $Native){throw '-ExtendedObservation requires -Native.'}
if($Native -and $Seconds -gt $routineSeconds -and -not $ExtendedObservation){
    throw "Routine native verification is limited to $routineSeconds seconds. Use -ExtendedObservation for a specific later boot milestone."
}

function Get-NativePageIdentity([string]$Session,[int]$PagePort){
    $descriptor=Join-Path $Session 'native-console-server.json'
    if(-not (Test-Path -LiteralPath $descriptor)){return}
    $url=(Get-Content -LiteralPath $descriptor -Raw | ConvertFrom-Json).url
    $uri=[Uri]$url
    if($uri.Scheme -ne 'http' -or $uri.Host -ne '127.0.0.1' -or $uri.Port -ne $PagePort -or
       $uri.AbsolutePath -ne '/' -or -not $uri.Fragment.StartsWith('#token=')){return}
    $headers=@{'X-8704-Token'=$uri.Fragment.Substring(7)}
    $status=Invoke-RestMethod -Uri "http://127.0.0.1:$PagePort/api/status" -Headers $headers -TimeoutSec 2
    if($status.linux.session -eq $Session -and $status.android.session -eq $Session){
        return @{Url=$url; Headers=$headers; Status=$status}
    }
}

function Get-NativePageUrl([string]$Session,[int]$PagePort){
    $identity=Get-NativePageIdentity $Session $PagePort
    if($identity -and $identity.Status.linux.serial_writable){return $identity.Url}
}

function Test-NativePageEntry([string]$Python,[string]$Rekit){
    $null=& $Python $Rekit _execute native_offline_console -- check-native-page
    if($LASTEXITCODE -ne 0){throw 'Native page preflight failed; review the rekit source manifest before boot.'}
}

function Close-CompletedNativePage([string]$Session,[int]$PagePort){
    if(-not (Test-Path -LiteralPath (Join-Path $Session 'result.json') -PathType Leaf)){
        $terminalPath=Join-Path $Session 'integrity.json'
        if(-not (Test-Path -LiteralPath $terminalPath -PathType Leaf) -or
           -not (Test-Path -LiteralPath (Join-Path $Session 'run-observation.json') -PathType Leaf)){return $false}
        $terminal=Get-Content -LiteralPath $terminalPath -Raw | ConvertFrom-Json
        if($terminal.original_unchanged -ne $true -or $terminal.before -notmatch '^[0-9a-f]{64}$' -or
           $terminal.before -ne $terminal.after -or $terminal.exit_code -isnot [long]){return $false}
    }
    $focus=Get-Content -LiteralPath (Join-Path $Session 'vlm-console-focus.json') -Raw | ConvertFrom-Json
    if($focus.source -ne 'original-vlm-focus' -or $focus.running -isnot [bool] -or $focus.running){return $false}
    $identity=Get-NativePageIdentity $Session $PagePort
    if(-not $identity -or $identity.Status.linux.board_running -ne $false -or $identity.Status.android.board_running -ne $false -or
       $identity.Status.linux.capture_ended -ne $true -or $identity.Status.android.capture_ended -ne $true -or
       $identity.Status.linux.serial_writable -ne $false -or $identity.Status.android.serial_writable -ne $false){return $false}
    $reply=Invoke-RestMethod -Uri "http://127.0.0.1:$PagePort/api/shutdown" -Method Post `
        -Headers $identity.Headers -ContentType 'application/json' -Body '{}' -TimeoutSec 2
    return $reply.stopping -eq $true
}

function Test-LocalPortFree([int]$Number){
    $listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$Number)
    try{$listener.Start();return $true}catch [Net.Sockets.SocketException]{return $false}finally{$listener.Stop()}
}

function Assert-NativeScratchCapacity([int64]$AvailableBytes,[int64]$LinuxSeedBytes,[bool]$FileRam,[string]$Format,[bool]$BaseFileRam=$false){
    $required=$LinuxSeedBytes+2GB
    if($FileRam){
        if($Format -ne 'NTFS'){throw 'Native file RAM requires local NTFS scratch.'}
        $required=[Math]::Max(6GB,$required+4352MB)
    }
    if($env:OEM_8704_VM3_SECOND_BANK){
        if(-not $FileRam -or $env:OEM_8704_VM3_SECOND_BANK -cne 'original-b0-v1'){
            throw 'VM3 second bank requires the original offline file-RAM profile.'
        }
        $required+=2GB
    }
    if($BaseFileRam){
        if(-not $FileRam){throw 'Base file RAM requires the native file-RAM profile.'}
        $required+=3GB
    }
    if($AvailableBytes -lt $required){
        throw ('Native boot scratch needs {0:F2} GiB free before staging; {1:F2} GiB is available.' -f ($required/1GB),($AvailableBytes/1GB))
    }
}

if($Native){
    . (Join-Path $PSScriptRoot 'Native-Secure-Routing.ps1')
    $null=Assert-NativeSecureCarrier $root
    if(-not $PSBoundParameters.ContainsKey('LocalAndroidShell') -and -not $SeedSessionOnly){$LocalAndroidShell=$true}
    if($Port -eq $ConsolePort){throw 'The page and OEM console require different loopback ports.'}
    $lscTimer=$null
    if($env:OEM_8704_HMI_LSC_TIMEOUT_MS){
        $rawLsc=$env:OEM_8704_HMI_LSC_TIMEOUT_MS
        $leanHmi=($env:OEM_8704_NO_HMI_VM_LOG -eq '1' -and $NativeStockWeston -and -not $NativeHmiVmLog -and
                  -not $NativeHmiFlightRecording -and -not $NativeHmiInterpreter -and ($LinuxHmiDataFrom -or $verifiedLinuxSeed))
        if($rawLsc -cnotmatch '^[0-9]{1,5}$' -or [int]$rawLsc -lt 5000 -or [int]$rawLsc -gt 90000 -or
           -not ($NativeHmiVmLog -or $leanHmi) -or -not $NativeHmiStartGrace -or -not $InfoNativeBoot -or
           $Seconds -ne $routineSeconds -or ($ExtendedObservation -and -not $stockLive) -or
           $env:OEM_8704_PERSISTENCE_PRIORITY -ne '1' -or $env:OEM_8704_PERSISTENCE_PARALLEL_SYSINIT -ne '1' -or
           $env:OEM_8704_HMI_QUEUE_READ -ne '1' -or $env:OEM_8704_HMI_DEFER_PRECOMPILE -eq '1' -or
           $env:OEM_8704_HMI_THREAD_DUMP -eq '1'){
            throw 'Native LSC timeout comparison needs 5000..90000 ms and the stock 240-second parallel-persistence preset.'
        }
        $lscTimer=[int]$rawLsc
    }
    $workspace=Join-Path $root 'var/sessions'
    $overlay=Join-Path $root 'var/inputs/support/vw-verity-native/overlay-verity/result.json'
    $installMediaHash=$null
    if($LinuxHmiInstallMedia){
        $overlay=(Resolve-Path -LiteralPath $LinuxHmiInstallMedia).Path
        $scratch=(Join-Path $root 'var')+[IO.Path]::DirectorySeparatorChar
        if(-not $overlay.StartsWith($scratch,[StringComparison]::OrdinalIgnoreCase) -or
            -not(Test-Path -LiteralPath $overlay -PathType Leaf)){
            throw '-LinuxHmiInstallMedia must be a verified media manifest in project scratch.'
        }
        $installMedia=Get-Content -LiteralPath $overlay -Raw | ConvertFrom-Json
        if($installMedia.install_metadata_transition.private_slot -ne 23 -or
           $installMedia.overlay_sha256 -notmatch '^[0-9a-f]{64}$'){
            throw 'Expected a verified OEM slot 23 install metadata overlay.'
        }
        $installMediaHash=$installMedia.overlay_sha256
    }
    $qnxParent=$null
    if($QnxDataFrom){
        $qnxParent=(Resolve-Path -LiteralPath $QnxDataFrom).Path
        $scratch=(Join-Path $root 'var')+[IO.Path]::DirectorySeparatorChar
        if(-not $qnxParent.StartsWith($scratch,[StringComparison]::OrdinalIgnoreCase) -or
           -not(Test-Path -LiteralPath (Join-Path $qnxParent 'result.json') -PathType Leaf)){
            throw '-QnxDataFrom must name a completed native PH session in project scratch.'
        }
    }
    $linuxParent=Join-Path $root 'var/inputs/seeds/linux'
    if($LinuxDataFrom){
        $linuxParent=(Resolve-Path -LiteralPath $LinuxDataFrom).Path
        $scratch=(Join-Path $root 'var')+[IO.Path]::DirectorySeparatorChar
        if(-not $linuxParent.StartsWith($scratch,[StringComparison]::OrdinalIgnoreCase) -or
           -not(Test-Path -LiteralPath (Join-Path $linuxParent 'result.json') -PathType Leaf)){
            throw '-LinuxDataFrom must name a completed native VM3 session in project scratch.'
        }
    }
    $hmiParent=$null
    if($LinuxHmiDataFrom){
        $hmiParent=(Resolve-Path -LiteralPath $LinuxHmiDataFrom).Path
        $scratch=(Join-Path $root 'var')+[IO.Path]::DirectorySeparatorChar
        if(-not $hmiParent.StartsWith($scratch,[StringComparison]::OrdinalIgnoreCase) -or
           $hmiParent -eq $linuxParent -or -not(Test-Path -LiteralPath (Join-Path $hmiParent 'result.json') -PathType Leaf)){
            throw '-LinuxHmiDataFrom must name an independent completed native VM3 session in project scratch.'
        }
    }
    $pagePortWasFree=Test-LocalPortFree $Port
    $active=Get-ChildItem -LiteralPath $workspace -Directory | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName 'native-console-server.json')
    } | Sort-Object LastWriteTime -Descending
    foreach($item in $active){
        if($pagePortWasFree -or $SeedSessionOnly){break}
        $url=$null
        try{
            $focus=Get-Content -LiteralPath (Join-Path $item.FullName 'vlm-console-focus.json') -Raw | ConvertFrom-Json
            if($focus.source -ne 'original-vlm-focus' -or $focus.running -ne $true){continue}
            if($PrepareLinuxSeed){
                $identity=Get-NativePageIdentity $item.FullName $Port
                if($identity -and $identity.Status.linux.board_running -eq $true){$url=$identity.Url}
            }
            else{$url=Get-NativePageUrl $item.FullName $Port}
        }catch{continue}
        if($url){
            $activeMedia=Get-Content -LiteralPath (Join-Path $item.FullName 'linux-vw-overlay.json') -Raw | ConvertFrom-Json
            if([bool]$activeMedia.install_metadata_transition -ne [bool]$installMediaHash -or
               ($installMediaHash -and $activeMedia.overlay_sha256 -ne $installMediaHash)){
                throw 'The existing board uses different VW install media and remains running.'
            }
            $linuxPolicy=Get-Content -LiteralPath (Join-Path $item.FullName 'linux-initcall-dtb.json') -Raw | ConvertFrom-Json
            if([bool]$linuxPolicy.oem_prepare_linux_seed -ne [bool]$PrepareLinuxSeed){
                throw 'The existing board uses a different Linux cleanup lifecycle and remains running.'
            }
            if([bool]$linuxPolicy.oem_accelerated_linux_seed -ne [bool]$AcceleratedLinuxSeed){
                throw 'The existing board uses a different Linux first-init profile and remains running.'
            }
            if($PrioritizeOemPostInit -and -not $linuxPolicy.oem_prioritize_post_init){
                throw 'The existing board uses stock post-init ordering and remains running.'
            }
            if([bool]$linuxPolicy.oem_hmi_start_grace_seconds -ne [bool]$NativeHmiStartGrace){
                throw 'The existing board uses a different VW HMI start timeout and remains running.'
            }
            if([bool]$linuxPolicy.oem_hmi_interpreter -ne [bool]$NativeHmiInterpreter){
                throw 'The existing board uses a different original VW HMI JVM execution mode and remains running.'
            }
            if([bool]$linuxPolicy.oem_hmi_flight_recording -ne [bool]$NativeHmiFlightRecording){
                throw 'The existing board uses a different OEM HMI flight-recording policy and remains running.'
            }
            if([bool]$linuxPolicy.oem_hmi_vm_log -ne [bool]$NativeHmiVmLog){
                throw 'The existing board uses a different OEM HMI VM log policy and remains running.'
            }
            if($linuxPolicy.oem_hmi_lsc_timeout_ms -ne $lscTimer){
                throw 'The existing board uses a different OEM LSC timer and remains running.'
            }
            if([bool]$linuxPolicy.oem_hmi_after_post_init -ne ($env:OEM_8704_HMI_AFTER_POST_INIT -eq '1')){
                throw 'The existing board uses a different HMI post-init ordering and remains running.'
            }
            if([bool]$linuxPolicy.oem_quiet_linux_initcalls -ne ($env:OEM_8704_QUIET_LINUX_INITCALLS -eq '1')){
                throw 'The existing board uses a different Linux initcall logging policy and remains running.'
            }
            $expectedPostInitNice=if($AcceleratedLinuxSeed){'-20'}else{[string]$env:OEM_8704_POST_INIT_NICE}
            if([string]$linuxPolicy.oem_post_init_nice -cne $expectedPostInitNice){
                throw 'The existing board has a different original post-init scheduling policy.'
            }
            if([bool]$linuxPolicy.oem_verified_linux_seed -ne $verifiedLinuxSeed){
                throw 'The existing board uses a different verified Linux data policy and remains running.'
            }
            if([bool]$linuxPolicy.oem_wayland_trace -ne [bool]$NativeWaylandTrace -or
                ($NativeWaylandTrace -and $linuxPolicy.oem_weston_policy.wayland_debug -ne 'server')){
                throw 'The existing board uses different native Weston protocol logging and remains running.'
            }
            if(($linuxPolicy.oem_weston_renderer -eq 'stock') -ne [bool]$NativeStockWeston){
                throw 'The existing board uses a different Weston renderer policy and remains running.'
            }
            $hasLifetime=Test-Path -LiteralPath (Join-Path $item.FullName 'native-runtime-lifetime.json') -PathType Leaf
            if($hasLifetime -ne ($env:OEM_8704_NATIVE_LIFETIME -ceq 'until-stop-v1')){
                throw 'The existing board uses a different runtime lifetime. It remains running.'
            }
            $hmiPolicy=(Get-Content -LiteralPath (Join-Path $item.FullName 'linux-ivi-data.json') -Raw | ConvertFrom-Json).hmi_userdata_seed
            if([bool]$hmiPolicy -ne [bool]$hmiParent -or ($hmiParent -and $hmiPolicy.prior_session -ne $hmiParent)){
                throw 'The existing board uses different OEM HMI userdata. It remains running.'
            }
            if($PSBoundParameters.ContainsKey('NativePerformanceCores')){
                $hostCpu=Test-Path -LiteralPath (Join-Path $item.FullName 'native-host-cpu-policy.json')
                if($hostCpu -ne [bool]$NativePerformanceCores){
                    throw 'The existing board uses a different host CPU Set policy and remains running.'
                }
            }
            if($NativeParallelTcg -and (Get-Content -LiteralPath (Join-Path $item.FullName 'launch.json') -Raw |
               ConvertFrom-Json).tcg_execution.thread_model -ne 'multi'){
                throw 'The existing board uses a different TCG schedule and remains running.'
            }
            $fastAp=Test-Path -LiteralPath (Join-Path $item.FullName 'native-host-ap-thread-policy.json')
            if($fastAp -ne ($AcceleratedLinuxSeed -or $env:OEM_8704_FAST_AP_THREADS -eq '1')){
                throw 'The existing board uses a different AP thread CPU Set policy and remains running.'
            }
            if($fastAp){
                $apPolicy=Get-Content -LiteralPath (Join-Path $item.FullName 'native-host-ap-thread-policy.json') -Raw | ConvertFrom-Json
                if(($apPolicy.status -eq 'windows_linux_seed_split_cpu_sets_applied') -ne ($env:OEM_8704_LINUX_SEED_SPLIT_CPU -eq '1')){
                    throw 'The existing board uses a different seed CPU layout and remains running.'
                }
                if(($apPolicy.status -eq 'windows_qnx_display_split_cpu_sets_applied') -ne ($env:OEM_8704_QNX_DISPLAY_CPU_SPLIT -eq '1')){
                    throw 'The existing board uses a different QNX display CPU layout and remains running.'
                }
            }
            if($NativeTeeObserve -and -not(Test-Path -LiteralPath (Join-Path $item.FullName 'native-qnx-tee-observation.json'))){
                throw 'The existing board has no original QNX TEE observer. It remains running.'
            }
            if($NativeHmiSignalObserve -and -not(Test-Path -LiteralPath (Join-Path $item.FullName 'native-linux-hmi-signal-observation.json'))){
                throw 'The existing session did not enable native HMI signal observation.'
            }
            if($PSBoundParameters.ContainsKey('InfoNativeBoot') -and
               [bool]$linuxPolicy.linux_systemd_info_only -ne [bool]$InfoNativeBoot){
                throw 'The existing board uses a different Linux systemd log level. It remains running; apply the option on the next cold boot.'
            }
            if($PSBoundParameters.ContainsKey('NativeClockShift')){
                $clock=(Get-Content -LiteralPath (Join-Path $item.FullName 'launch.json') -Raw | ConvertFrom-Json).instruction_clock
                if($null -eq $clock -or $clock.shift -ne $NativeClockShift){
                    throw 'The existing board uses a different native TCG clock. It remains running; apply the option on the next cold boot.'
                }
            }
            if([bool]$linuxPolicy.local_linux_shell -ne [bool]$LocalLinuxShell -or
               ($LocalLinuxShell -and $linuxPolicy.local_linux_shell_tty -ne $(if($env:OEM_8704_ISOLATED_LINUX_UART -eq '4'){'/dev/ttySAC4'}elseif($env:OEM_8704_SHARED_LINUX_CONSOLE -eq '1'){'/dev/ttySAC0'}else{'/dev/ttySAC3'}))){
                throw 'The existing board uses a different Linux UART policy. It remains running; choose the UART policy on the next cold boot.'
            }
            $androidPolicyPath=Join-Path $item.FullName 'android-local-shell.json'
            $currentAndroid=$false
            if(Test-Path -LiteralPath $androidPolicyPath){
                $currentAndroid=((Get-Content -LiteralPath $androidPolicyPath -Raw | ConvertFrom-Json).local_android_shell -eq $true)
            }
            if($PSBoundParameters.ContainsKey('LocalAndroidShell') -and $currentAndroid -ne [bool]$LocalAndroidShell){
                throw 'The existing board uses a different Android console policy. It remains running; choose the console policy on the next cold boot.'
            }
            if($LocalAndroidShell -and -not $currentAndroid){
                Write-Warning 'Reusing the existing board; the Android console observer is enabled on the next cold boot, not on this page attachment.'
            }
            if($LinuxDataFrom){
                $dataPolicy=Get-Content -LiteralPath (Join-Path $item.FullName 'linux-ivi-data.json') -Raw | ConvertFrom-Json
                if($dataPolicy.prior_session -ne $linuxParent){
                    throw 'The existing board has a different Linux data parent. It remains running; select data on the next cold boot.'
                }
            }
            if($qnxParent){
                $diskPolicy=Get-Content -LiteralPath (Join-Path $item.FullName 'ufs-lun0-input.json') -Raw | ConvertFrom-Json
                if($diskPolicy.qnx_firstboot_seed.prior_session -ne $qnxParent){
                    throw 'The existing board has a different QNX data parent. It remains running.'
                }
            }
            if($ProvisionNavData -and -not (Test-Path -LiteralPath (Join-Path $item.FullName 'linux-nav-data-material.json'))){
                throw 'The existing board has no provisioned private /navi_data volume. It remains running; apply the option on the next cold boot.'
            }
            if($KeepLinuxOnFailure -or $LinuxPixman){
                if($KeepLinuxOnFailure -and $linuxPolicy.oem_automatic_reboot -ne 'disabled'){
                    throw 'The existing board has a different Linux boot policy. It remains running; apply the requested policy on the next cold boot.'
                }
                if($LinuxPixman -and ($linuxPolicy.oem_weston_renderer -ne 'pixman' -or
                    $linuxPolicy.oem_weston_policy.launcher -ne '/bin/early_init')){
                    throw 'The existing board uses a different Weston renderer. It remains running; apply the requested renderer on the next cold boot.'
                }
            }
            if($QnxDisplayProfile){
                $identity=Join-Path $item.FullName 'qnx-display-identity.json'
                if(-not (Test-Path -LiteralPath $identity) -or
                   (Get-Content -LiteralPath $identity -Raw | ConvertFrom-Json).profile -ne $QnxDisplayProfile){
                    throw 'The existing board has a different QNX display configuration. It remains running; apply the requested profile on the next cold boot.'
                }
            }
            $gpuPolicy=Join-Path $item.FullName 'qnx-g3d00-profile.json'
            if($PSBoundParameters.ContainsKey('NativeMaliControlAs0')){
                $maliPolicy=Join-Path $item.FullName 'mali-g3d1-profile.json'
                $currentMaliControl=(Test-Path -LiteralPath $maliPolicy) -and
                    ((Get-Content -LiteralPath $maliPolicy -Raw | ConvertFrom-Json).jm_topology.vm3_control -eq $true)
                if($currentMaliControl -ne [bool]$NativeMaliControlAs0){
                    throw 'The existing board has a different VM3 G3D1 policy. It remains running; choose this profile on the next cold boot.'
                }
            }
            $currentJobs=$false
            if(Test-Path -LiteralPath $gpuPolicy){
                $currentJobs=(Get-Content -LiteralPath $gpuPolicy -Raw | ConvertFrom-Json).control_jobs -eq $true
            }
            if($currentJobs -ne [bool]$QnxMaliControlJobs){
                if($PSBoundParameters.ContainsKey('QnxMaliControlJobs')){
                    throw 'The existing board has a different QNX job policy. It remains running; choose this profile on the next cold boot.'
                }
                Write-Warning 'Reusing the existing AS0-only board; control jobs take effect on the next cold boot, not on this page attachment.'
            }
            if($QnxPhy88q2112){
                $eqos=Get-Content -LiteralPath (Join-Path $item.FullName 'eqos-profile.json') -Raw | ConvertFrom-Json
                if($eqos.phy.phy_address -ne 7){
                    throw 'The existing board uses a different QNX PHY configuration. It remains running; apply the requested PHY on the next cold boot.'
                }
            }
            Write-Output $url
            if($NativeWindow){
                Start-Process -FilePath $python -ArgumentList @(
                    '-X','utf8','-B',('"'+$rekit+'"'),'run','native_center_window','--',
                    '--session',('"'+$session+'"')
                ) -WorkingDirectory $root -WindowStyle Hidden `
                    -RedirectStandardOutput (Join-Path $session 'native-window.log') `
                    -RedirectStandardError (Join-Path $session 'native-window-error.log') | Out-Null
            }
            if($OpenBrowser){Start-Process $url}
            return
        }
    }
    $pageRelative='src/emu/native_offline_console.py'
    $pageSource=Join-Path $root $pageRelative
    $rekitManifest=Get-Content -LiteralPath (Join-Path $root 'tools/rekit/manifest.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $pageEntry=@($rekitManifest.entries | Where-Object {$_.name -eq 'native_offline_console'})
    if($pageEntry.Count -ne 1 -or $pageEntry[0].path -cne $pageRelative -or
       (Get-FileHash -LiteralPath $pageSource -Algorithm SHA256).Hash -ne $pageEntry[0].sha256){
        throw "Native page source does not match rekit manifest; update the reviewed entry for $pageRelative before booting."
    }
    Test-NativePageEntry $python $rekit
    $qemu=Join-Path $root 'runtime/qemu-system-aarch64.exe'
    if(Get-Process -Name qemu-system-aarch64 -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $qemu}){
        throw 'A native board is already running. Use its existing page port; a new port does not create a second board.'
    }
    if(-not (Test-LocalPortFree $ConsolePort)){throw "Loopback port $ConsolePort is already in use."}
    if(-not $SeedSessionOnly -and -not (Test-LocalPortFree $Port)){
        foreach($item in $active){
            try{if(Close-CompletedNativePage $item.FullName $Port){break}}catch{Write-Verbose 'Not an authenticated, completed OEM page.'}
        }
        $free=$false
        for($attempt=0;$attempt -lt 12;$attempt++){
            if(Test-LocalPortFree $Port){$free=$true;break}
            Start-Sleep -Milliseconds 125
        }
        if(-not $free){throw "Loopback port $Port is already in use."}
    }
    $runner=Join-Path $root 'src/runner/Run-OfflineDevelopment.ps1'
    $androidParent=Join-Path $root 'var/inputs/support/android-metadata-native/native-capture'
    foreach($inputPath in @($runner,$qemu,$python,$linuxParent,$androidParent,$overlay)){
        if(-not (Test-Path -LiteralPath $inputPath)){throw "Missing native boot input: $inputPath"}
    }
    $os=Get-CimInstance Win32_OperatingSystem
    $commit=[int64]$os.FreeVirtualMemory*1024
    $baseFileRam=$false
    if($env:OEM_8704_OFFLINE_BASE_RAM){
        if($env:OEM_8704_OFFLINE_BASE_RAM -cne 'file-v1' -or
           $env:OEM_8704_OFFLINE_DEVELOPMENT -cne 'android-private-unencrypted-v1' -or
           $env:OEM_8704_VM3_SECOND_BANK -cne 'original-b0-v1' -or $PrepareLinuxSeed -or $SeedSessionOnly){
            throw 'Base file RAM requires the original three-domain offline file-RAM layout.'
        }
        $baseFileRam=$true
    }
    $hostFileRam=$baseFileRam -or $commit -lt 14GB
    $floor=if($baseFileRam){7GB+128MB}else{9GB+128MB}
    if($hostFileRam -and $commit -lt $floor){
        throw ('Native board needs at least {0:F3} GiB of available host commit in file-RAM mode; measured {1:F2} GiB.' -f ($floor/1GB),($commit/1GB))
    }
    $seedFile=Get-Item -LiteralPath (Join-Path $linuxParent 'ufs-vh1-data-session.qcow2') -ErrorAction SilentlyContinue
    $seedBytes=if($seedFile -and -not $seedFile.PSIsContainer){[int64]$seedFile.Length}else{0L}
    $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($workspace))
    Assert-NativeScratchCapacity $drive.AvailableFreeSpace $seedBytes $hostFileRam $drive.DriveFormat $baseFileRam
    $name='oem-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmssfff')
    $session=Join-Path $workspace $name
    $hostExe=(Get-Process -Id $PID).Path
    $bootArgs=@('-NoProfile','-File',('"'+$runner+'"'),'-OutputName',$name,
        '-Seconds',$Seconds,'-Dsi','-DpAuxSink','-ConsolePort',$ConsolePort,
        '-Qemu',('"'+$qemu+'"'),'-MaliJm','-QnxMaliJm','-LinuxFactoryBank','-QnxFactoryBank',
        '-QnxBts','-NativeCenterScanout','-PmuOmInput',0,
        '-LinuxHmiOverlay',('"'+$overlay+'"'),
        '-AndroidWritable')
    if(Test-Path -LiteralPath (Join-Path $linuxParent 'ufs-vh1-data-session.qcow2')){
        $bootArgs+=@('-LinuxParent',('"'+$linuxParent+'"'))
    }
    if($hostFileRam){$bootArgs+='-HostFileRam'}
    if($ExtendedObservation){$bootArgs+='-ExtendedObservation'}
    if($QnxMaliControlJobs){$bootArgs+='-QnxMaliControlJobs'}
    if($NativeMaliControlAs0){$bootArgs+='-MaliControlAs0'}
    if($LocalLinuxShell){$bootArgs+='-LocalLinuxShell'}elseif(-not $PrepareLinuxSeed){$bootArgs+='-StageOemRunGettyAlias'}
    if($ProvisionNavData){$bootArgs+='-ProvisionNavData'}
    if($PrepareLinuxSeed){$bootArgs+='-PrepareLinuxSeed'}
    if($AcceleratedLinuxSeed){$bootArgs+='-AcceleratedLinuxSeed'}
    if($PrioritizeOemPostInit){$bootArgs+='-PrioritizeOemPostInit'}
    if($NativeHmiStartGrace){$bootArgs+='-NativeHmiStartGrace'}
    if($NativeHmiInterpreter){$bootArgs+='-NativeHmiInterpreter'}
    if($NativeHmiFlightRecording){$bootArgs+='-NativeHmiFlightRecording'}
    if($NativeHmiVmLog){$bootArgs+='-NativeHmiVmLog'}
    if($NativeWaylandTrace){$bootArgs+='-NativeWaylandTrace'}
    if($hmiParent){$bootArgs+=@('-LinuxHmiParent',('"'+$hmiParent+'"'))}
    if($NativePerformanceCores){$bootArgs+='-NativePerformanceCores'}
    if($NativeParallelTcg){$bootArgs+='-NativeParallelTcg'}else{$bootArgs+=@('-ClockShift',$NativeClockShift)}
    if($qnxParent){$bootArgs+=@('-QnxParent',('"'+$qnxParent+'"'))}
    if($NativeTeeObserve){$bootArgs+='-NativeTeeObserve'}
    if($NativeHmiSignalObserve){$bootArgs+='-NativeHmiSignalObserve'}
    if($InfoNativeBoot){$bootArgs+='-InfoNativeBoot'}
    if($LocalAndroidShell){$bootArgs+='-LocalAndroidShell'}
    $bootArgs+='-KeepLinuxOnFailure'
    if($NativeStockWeston){$bootArgs+='-NativeStockWeston'}else{$bootArgs+='-LinuxPixman'}
    $bootArgs+='-QnxPhy88q2112'
    if($QnxDisplayProfile){$bootArgs+=@('-QnxDisplayProfile',$QnxDisplayProfile)}
    else{$bootArgs+=@('-QnxDisplayProfile','iid-arhud')}
    $boot=Start-Process -FilePath $hostExe -ArgumentList $bootArgs -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput "$session-boot.log" -RedirectStandardError "$session-boot-error.log" -PassThru
    if($SeedSessionOnly){
        $boot.WaitForExit()
        if($boot.ExitCode -ne 0){throw "Native seed runner exited ($($boot.ExitCode)); see $session-boot-error.log"}
        if(-not (Test-Path -LiteralPath (Join-Path $session 'result.json') -PathType Leaf)){
            throw "Native seed runner has no terminal result: $session"
        }
        Write-Output $session
        return
    }
    $focusFile=Join-Path $session 'vlm-console-focus.json'
    $deadline=[DateTime]::UtcNow.AddMinutes(6)
    do {
        if($boot.HasExited){throw "Native board exited ($($boot.ExitCode)); see $session-boot-error.log"}
        $focus=$null
        if(Test-Path -LiteralPath $focusFile){
            try{$focus=Get-Content -LiteralPath $focusFile -Raw | ConvertFrom-Json}catch{Write-Verbose 'Waiting for a complete focus sample.'}
        }
        if($focus.running){break}
        Start-Sleep -Milliseconds 500
    }while([DateTime]::UtcNow -lt $deadline)
    if(-not $focus.running){throw "Native UART not yet available; see $session-boot-error.log"}
    $pageArgs=@(('"'+$rekit+'"'),'run','native_offline_console','--','serve','--port',$Port,
                '--native-session',('"'+$session+'"'),'--native-interactive')
    $page=Start-Process -FilePath $python -ArgumentList $pageArgs -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $session 'page.log') `
        -RedirectStandardError (Join-Path $session 'page-error.log') -PassThru
    for($attempt=0;$attempt -lt 120;$attempt++){
        if($page.HasExited){throw "Native page exited ($($page.ExitCode)); see $session/page-error.log"}
        $url=$null
        try{
            if($PrepareLinuxSeed){$url=(Get-NativePageIdentity $session $Port).Url}
            else{$url=Get-NativePageUrl $session $Port}
        }catch{Write-Verbose 'Waiting for the authenticated native page.'}
        if($url){
            Write-Output $url
            if($LocalLinuxShell){
                Start-Process -FilePath $python -ArgumentList @(
                    '-B',('"'+(Join-Path $PSScriptRoot 'native_uart_console.py')+'"'),
                    ('"'+$session+'"'),'--seconds',[string]$Seconds
                ) -WorkingDirectory $root -WindowStyle Hidden `
                    -RedirectStandardOutput (Join-Path $session 'linux-local-shell-verify.log') `
                    -RedirectStandardError (Join-Path $session 'linux-local-shell-verify-error.log') | Out-Null
            }
            if($LocalAndroidShell){
                Start-Process -FilePath $python -ArgumentList @(
                    '-B',('"'+$rekit+'"'),'run','offline_android_console','--',
                    ('"'+$session+'"'),'--seconds',[string]$Seconds
                ) -WorkingDirectory $root -WindowStyle Hidden `
                    -RedirectStandardOutput (Join-Path $session 'android-local-shell-verify.log') `
                    -RedirectStandardError (Join-Path $session 'android-local-shell-verify-error.log') | Out-Null
            }
            if($OpenBrowser){Start-Process $url}
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Native page did not start; see $session/page-error.log"
}

$config=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$assets=Join-Path $root $config.assets
$stateFile=Join-Path $assets 'server.json'
if(Test-Path -LiteralPath $stateFile){
    $saved=Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if($saved.port -eq $Port){
        try{
            $uri=[Uri]$saved.url
            $token=$uri.Fragment.Substring('#token='.Length)
            $null=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -Headers @{'X-8704-Token'=$token} -TimeoutSec 2
            Write-Output $saved.url
            if($OpenBrowser){Start-Process $saved.url}
            return
        }catch{Write-Verbose 'Saved controller is no longer reachable.'}
    }
}
New-Item -ItemType Directory -Path $assets -Force | Out-Null
$arguments='"'+$rekit+'" run original_8704_vm -- serve --port '+$Port
$process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $assets 'controller.log') -RedirectStandardError (Join-Path $assets 'controller.stderr.txt') -PassThru
for($attempt=0;$attempt -lt 60;$attempt++){
    Start-Sleep -Milliseconds 250
    $process.Refresh()
    if($process.HasExited){throw 'Controller exited; inspect assets/controller.stderr.txt'}
    if(Test-Path -LiteralPath $stateFile){
        $saved=Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if($saved.port -eq $Port){
            try{
                $uri=[Uri]$saved.url
                $token=$uri.Fragment.Substring('#token='.Length)
                $null=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -Headers @{'X-8704-Token'=$token} -TimeoutSec 2
                Write-Output $saved.url
                if($OpenBrowser){Start-Process $saved.url}
                return
            }catch{Write-Verbose 'Waiting for controller.'}
        }
    }
}
throw 'Controller did not become ready; inspect its logs.'
