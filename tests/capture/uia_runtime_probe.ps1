# Test-only, one request. No retry or production fallback.
param([Parameter(Mandatory=$true)][string]$PipeName)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
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
    if (-not (Test-ProbeFields $payload @('window_handle', 'process_id', 'started_ticks', 'foreground_handle', 'x', 'y'))) { throw 'unsupported_or_changed' }
    foreach ($coordinate in @($payload.x, $payload.y)) {
        if (($coordinate -isnot [int] -and $coordinate -isnot [long]) -or $coordinate -lt [int]::MinValue -or $coordinate -gt [int]::MaxValue) { throw 'unsupported_or_changed' }
    }
    if (-not (Test-ProbeInteger $payload.window_handle ([long]::MaxValue)) -or
        -not (Test-ProbeInteger $payload.foreground_handle ([long]::MaxValue)) -or
        -not (Test-ProbeInteger $payload.process_id ([int]::MaxValue)) -or
        -not (Test-ProbeInteger $payload.started_ticks ([datetime]::MaxValue.Ticks))) { throw 'unsupported_or_changed' }
}
function Start-RuntimeProbe {
    if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1 -or [IntPtr]::Size -ne 8) { throw 'unsupported_or_changed' }
    $source = Join-Path $PSScriptRoot '../../src/verdiclip/capture/scroll'
    Add-Type -AssemblyName System.Drawing
    Add-Type -Path @((Join-Path $source 'uia_nofocus_interop.cs'), (Join-Path $source 'uia_desktop.cs'), (Join-Path $source 'uia_discovery.cs'), (Join-Path $PSScriptRoot 'uia_runtime_probe.cs'))
    [VerdiClip.Automation.DiscoveryPolicy]::RequireNativeOnly()
}
function Invoke-RuntimeProbe($Request) {
    Assert-ProbeRequest $Request
    $p = $Request.payload
    return [VerdiClip.Automation.RuntimeProbe]::Run($p.window_handle, $p.process_id, $p.started_ticks, $p.foreground_handle, $p.x, $p.y)
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
    Start-RuntimeProbe
    [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=runtime_ready')
    $writer.WriteLine((@{ ready=$true; protocol=1 } | ConvertTo-Json -Compress))
    $requestLine = $reader.ReadLine()
    if ($null -eq $requestLine -or $requestLine.Length -gt 4096) { throw 'unsupported_or_changed' }
    $request = $requestLine | ConvertFrom-Json
    # Correlate even an otherwise malformed request, without coercing its ID.
    if ($request.request_id -is [string] -and $request.request_id -cmatch '\A[a-f0-9]{32}\z') { $requestId = $request.request_id }
    $result = Invoke-RuntimeProbe $request
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
