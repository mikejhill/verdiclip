# Test-only cold managed/native root comparison. One operation, no warmup or retry.
param(
    [Parameter(Mandatory=$true)][string]$PipeName,
    [Parameter(Mandatory=$true)][ValidateSet('managed', 'native')][string]$Client
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Test-ProbeFields($Value, [string[]]$Names) {
    if ($Value -isnot [pscustomobject]) { return $false }
    $actual = @($Value.PSObject.Properties.Name)
    if ($actual.Count -ne $Names.Count) { return $false }
    foreach ($name in $actual) { if ($Names -cnotcontains $name) { return $false } }
    return $true
}
function Test-ProbeInteger($Value, [long]$Maximum) {
    # ConvertFrom-Json yields Int32/Int64. Never admit bool, float, or string.
    return ($Value -is [int] -or $Value -is [long]) -and $Value -gt 0 -and $Value -le $Maximum
}
function Assert-ProbeRequest($Request) {
    if (-not (Test-ProbeFields $Request @('request_id', 'action', 'payload'))) { throw 'unsupported_or_changed' }
    if ($Request.request_id -isnot [string] -or $Request.request_id -cnotmatch '\A[a-f0-9]{32}\z') { throw 'unsupported_or_changed' }
    if ($Request.action -isnot [string] -or $Request.action -cne 'inspect') { throw 'unsupported_or_changed' }
    $payload = $Request.payload
    if (-not (Test-ProbeFields $payload @('window_handle', 'process_id', 'started_ticks', 'foreground_handle'))) { throw 'unsupported_or_changed' }
    if (-not (Test-ProbeInteger $payload.window_handle ([long]::MaxValue)) -or
        -not (Test-ProbeInteger $payload.foreground_handle ([long]::MaxValue)) -or
        -not (Test-ProbeInteger $payload.process_id ([int]::MaxValue)) -or
        -not (Test-ProbeInteger $payload.started_ticks ([datetime]::MaxValue.Ticks))) { throw 'unsupported_or_changed' }
}
function Start-RootProbe {
    if ($Client -cnotin @('managed', 'native')) { throw 'unsupported_or_changed' }
    if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or
        [IntPtr]::Size -ne 8 -or $env:PROCESSOR_ARCHITECTURE -cne 'AMD64') { throw 'unsupported_or_changed' }
    if ([Threading.Thread]::CurrentThread.GetApartmentState() -ne [Threading.ApartmentState]::MTA) { throw 'unsupported_or_changed' }
    $source = Join-Path $PSScriptRoot '../../src/verdiclip/capture/scroll'
    if ($Client -ceq 'managed') {
        Add-Type -AssemblyName UIAutomationClient
        Add-Type -AssemblyName UIAutomationTypes
        Add-Type -AssemblyName WindowsBase
        Add-Type -AssemblyName System.Drawing
    }
    # Both arms compile identical test scaffolding. Explicit COM activation is
    # native-operation-only; managed discovery remains the original direct call.
    # Never compile the product's mutation implementation.
    Add-Type -Path @((Join-Path $source 'uia_nofocus_interop.cs'),
        (Join-Path $PSScriptRoot 'uia_root_probe_native.cs'),
        (Join-Path $source 'uia_desktop.cs'))
    [VerdiClip.Automation.RootProbeGuard]::RequireMta()
    if ($Client -ceq 'native') { [VerdiClip.Automation.RootProbeGuard]::RequireNativeOnlyProcess() }
    $powershell = $PSVersionTable.PSVersion.ToString()
    $clr = [Environment]::Version.ToString()
    foreach ($version in @($powershell, $clr)) {
        if ($version.Length -gt 32 -or $version -cnotmatch '\A[0-9]+(\.[0-9]+){1,3}\z') { throw 'unsupported_or_changed' }
    }
    return @{ powershell=$powershell; clr=$clr; architecture='x64' }
}
function Invoke-RootProbe($Request, $Runtime) {
    Assert-ProbeRequest $Request
    $requestId = $Request.request_id
    $payload = $Request.payload
    $handle = [IntPtr]::new([long]$payload.window_handle)
    $priorDpi = [IntPtr]::Zero
    $session = $null
    $root = $null
    $current = $null
    $failure = $null
    $releaseConfirmed = $false
    $dpiRestored = $false
    $durations = @{ construct=$null; root=$null; validate=$null; release=$null }
    try {
        [VerdiClip.Automation.RootProbeGuard]::RequireMta()
        if ($Client -ceq 'native') { [VerdiClip.Automation.RootProbeGuard]::RequireNativeOnlyProcess() }
        [VerdiClip.Automation.RootProbeTiming]::Phase('inspect_desktop')
        $priorDpi = [CaptureDesktop]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
        if ($priorDpi -eq [IntPtr]::Zero) { throw 'unsupported_or_changed' }
        [VerdiClip.Automation.RootProbeGuard]::RequirePhysicalPixels()
        [VerdiClip.Automation.RootProbeGuard]::RequireOwnedWindow($payload.window_handle, $payload.process_id, $payload.started_ticks, $payload.foreground_handle)
        if ($Client -ceq 'native') {
            $session = [VerdiClip.Automation.RootProbeSession]::new()
            $session.Construct()
            [VerdiClip.Automation.RootProbeGuard]::RequireForeground($payload.foreground_handle)
            $session.Acquire($payload.window_handle, $requestId)
            [VerdiClip.Automation.RootProbeGuard]::RequireForeground($payload.foreground_handle)
            $session.Validate($payload.process_id, $payload.window_handle)
            $durations.construct = $session.ConstructUs
            $durations.root = $session.RootUs
            $durations.validate = $session.ValidateUs
        } else {
            [VerdiClip.Automation.RootProbeTiming]::Phase('inspect_root')
            $started = [VerdiClip.Automation.RootProbeTiming]::Start()
            $root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
            $durations.root = [VerdiClip.Automation.RootProbeTiming]::Finish($started)
            [VerdiClip.Automation.RootProbeTiming]::RootRecord($requestId, $durations.root)
            [VerdiClip.Automation.RootProbeGuard]::RequireForeground($payload.foreground_handle)
            $started = [VerdiClip.Automation.RootProbeTiming]::Start()
            if ($null -eq $root) { throw 'unsupported_or_changed' }
            [VerdiClip.Automation.RootProbeTiming]::Phase('inspect_process')
            $current = $root.Current
            if ($current.ProcessId -ne $payload.process_id -or [long]$current.NativeWindowHandle -ne $payload.window_handle) { throw 'unsupported_or_changed' }
            [VerdiClip.Automation.RootProbeTiming]::Phase('inspect_root_runtime')
            [VerdiClip.Automation.RootProbeGuard]::RequireRuntime($root.GetRuntimeId())
            $durations.validate = [VerdiClip.Automation.RootProbeTiming]::Finish($started)
        }
        [VerdiClip.Automation.RootProbeGuard]::RequireOwnedWindow($payload.window_handle, $payload.process_id, $payload.started_ticks, $payload.foreground_handle)
        [VerdiClip.Automation.RootProbeGuard]::RequirePhysicalPixels()
        if ($Client -ceq 'native') { [VerdiClip.Automation.RootProbeGuard]::RequireNativeOnlyProcess() }
    } catch { $failure = $_.Exception }
    finally {
        try {
            if ($null -ne $session) {
                $session.Release()
                $durations.release = $session.ReleaseUs
            } else {
                $started = [VerdiClip.Automation.RootProbeTiming]::Start()
                # Managed AutomationElement is not an RCW. Release an exposed RCW
                # defensively, never force collection of framework-owned objects.
                if ($null -ne $root -and [Runtime.InteropServices.Marshal]::IsComObject($root)) {
                    if ([Runtime.InteropServices.Marshal]::ReleaseComObject($root) -ne 0) { throw 'unsupported_or_changed' }
                }
                $current = $null
                $root = $null
                $durations.release = [VerdiClip.Automation.RootProbeTiming]::Finish($started)
            }
            $releaseConfirmed = $true
        } catch {
            if ($null -eq $failure) { $failure = $_.Exception }
            else { $failure = [AggregateException]::new([Exception[]]@($failure, $_.Exception)) }
        }
        try {
            if ($priorDpi -ne [IntPtr]::Zero) {
                [VerdiClip.Automation.RootProbeGuard]::RequireForeground($payload.foreground_handle)
            }
        } catch {
            if ($null -eq $failure) { $failure = $_.Exception }
            else { $failure = [AggregateException]::new([Exception[]]@($failure, $_.Exception)) }
        }
        try {
            if ($priorDpi -ne [IntPtr]::Zero) {
                [VerdiClip.Automation.RootProbeGuard]::RestoreDpi($priorDpi)
                $dpiRestored = $true
            }
        } catch {
            if ($null -eq $failure) { $failure = $_.Exception }
            else { $failure = [AggregateException]::new([Exception[]]@($failure, $_.Exception)) }
        }
    }
    # A release or DPI error must never become an ok response. Keep original
    # exceptions only in-process; do not expose messages, provider data, or HRESULTs.
    if ($null -ne $failure) { throw $failure }
    if (-not $releaseConfirmed -or -not $dpiRestored) { throw 'unsupported_or_changed' }
    [VerdiClip.Automation.RootProbeGuard]::RequireMta()
    [VerdiClip.Automation.RootProbeGuard]::RequireOwnedWindow($payload.window_handle, $payload.process_id, $payload.started_ticks, $payload.foreground_handle)
    if ($Client -ceq 'native') { [VerdiClip.Automation.RootProbeGuard]::RequireNativeOnlyProcess() }
    return @{
        client=$Client; apartment='MTA'; identity_verified=$true; foreground_unchanged=$true
        native_only_verified=($Client -ceq 'native'); release_confirmed=$releaseConfirmed; dpi_restored=$dpiRestored
        release_kind=$(if ($Client -ceq 'native') { 'native_rcw_release_completed' } else { 'managed_reference_dropped' })
        durations_us=$durations; runtime=$Runtime
    }
}

$channel = $null; $reader = $null; $writer = $null
$requestId = ''; $responseAttempted = $false
try {
    if ($PipeName -cnotmatch '\Averdiclip-uia-[a-f0-9]{32}\z') { throw 'unsupported_or_changed' }
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=script_entered')
    $channel = [IO.Pipes.NamedPipeClientStream]::new('.', $PipeName, [IO.Pipes.PipeDirection]::InOut)
    $channel.Connect(5000)
    [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=pipe_connected')
    $reader = [IO.StreamReader]::new($channel, [Text.UTF8Encoding]::new($false), $false, 1024, $true)
    $writer = [IO.StreamWriter]::new($channel, [Text.UTF8Encoding]::new($false), 1024, $true)
    $writer.AutoFlush = $true
    $runtime = Start-RootProbe
    [VerdiClip.Automation.RootProbeTiming]::Phase('runtime_ready')
    $writer.WriteLine((@{ ready=$true; protocol=1 } | ConvertTo-Json -Compress))
    $requestLine = $reader.ReadLine()
    if ($null -eq $requestLine -or $requestLine.Length -gt 4096) { throw 'unsupported_or_changed' }
    $request = $requestLine | ConvertFrom-Json
    # Correlate even an otherwise malformed request, without coercing its ID.
    if ($request.request_id -is [string] -and $request.request_id -cmatch '\A[a-f0-9]{32}\z') { $requestId = $request.request_id }
    $result = Invoke-RootProbe $request $runtime
    $response = @{ request_id=$requestId; ok=$true; result=$result }
    $responseAttempted = $true
    $writer.WriteLine(($response | ConvertTo-Json -Depth 5 -Compress))
} catch {
    if ($null -ne $writer -and -not $responseAttempted) {
        $responseAttempted = $true
        try { $writer.WriteLine((@{ request_id=$requestId; ok=$false; error='unsupported_or_changed' } | ConvertTo-Json -Compress)) } catch { }
    }
} finally {
    foreach ($owned in @($reader, $writer, $channel)) {
        if ($null -ne $owned) { try { $owned.Dispose() } catch { } }
    }
}
# Exit after the single response. The parent's unchanged deadlines own any hang.
