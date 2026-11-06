# Bounded JSON-line operations in one warm MTA with a finite parent-owned lease.
# No clicks, InvokePattern, activation, policy changes, or input injection.
param([Parameter(Mandatory=$true)][string]$PipeName, [switch]$NativeOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=script_entered')
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($PipeName -notmatch '^verdiclip-uia-[a-f0-9]{32}$') { exit 1 }
$channel = [IO.Pipes.NamedPipeClientStream]::new('.', $PipeName, [IO.Pipes.PipeDirection]::InOut)
$channel.Connect(5000)
[Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=pipe_connected')
$reader = [IO.StreamReader]::new($channel, [Text.UTF8Encoding]::new($false), $false, 1024, $true)
$writer = [IO.StreamWriter]::new($channel, [Text.UTF8Encoding]::new($false), 1024, $true)
$writer.AutoFlush = $true
function Test-OwnedScrollMetrics {
    param([double]$Position, [double]$ViewSize, $Reference)
    $expectedPosition = [double]$Reference.vertical_percent
    $expectedViewSize = [double]$Reference.view_size
    foreach ($value in @($Position, $ViewSize, $expectedPosition, $expectedViewSize)) {
        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) { return $false }
    }
    return $Position -ge 0 -and $Position -le 100 -and $ViewSize -gt 0 -and $ViewSize -lt 100 -and $expectedViewSize -gt 0 -and $expectedViewSize -lt 100 -and [Math]::Abs($Position - $expectedPosition) -le 0.001 -and [Math]::Abs($ViewSize - $expectedViewSize) -le 0.001
}
function Test-SamePhysicalBounds {
    param($Actual, $Expected)
    if (@($Actual).Count -ne 4 -or @($Expected).Count -ne 4) { return $false }
    for ($index = 0; $index -lt 4; $index++) {
        $a = [double]$Actual[$index]
        $b = [double]$Expected[$index]
        if ([double]::IsNaN($a) -or [double]::IsInfinity($a) -or [double]::IsNaN($b) -or [double]::IsInfinity($b) -or $a -ne $b) { return $false }
    }
    return $true
}
function Assert-DiscoveryFields($Value, [string[]]$Required, [string[]]$Optional=@()) {
    if ($Value -isnot [pscustomobject]) { throw 'invalid_request' }
    $names = @($Value.PSObject.Properties.Name)
    foreach ($name in $Required) { if ($names -cnotcontains $name) { throw 'invalid_request' } }
    foreach ($name in $names) { if ($Required -cnotcontains $name -and $Optional -cnotcontains $name) { throw 'invalid_request' } }
}
function Test-DiscoveryNumber($Value) {
    return ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) -and -not [double]::IsNaN($Value) -and -not [double]::IsInfinity($Value)
}
function Assert-DiscoveryInteger($Value, [long]$Minimum, [long]$Maximum) {
    if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt $Minimum -or $Value -gt $Maximum) { throw 'invalid_identity' }
}
function Assert-DiscoveryBounds($Bounds) {
    if ($Bounds -isnot [array] -or $Bounds.Count -ne 4) { throw 'invalid_geometry' }
    foreach ($value in $Bounds) {
        if (-not (Test-DiscoveryNumber $value) -or $value -ne [Math]::Truncate($value) -or $value -lt [int]::MinValue -or $value -gt [int]::MaxValue) { throw 'invalid_geometry' }
    }
    if ($Bounds[2] -le 0 -or $Bounds[3] -le 0 -or [double]$Bounds[0]+$Bounds[2] -gt [int]::MaxValue -or [double]$Bounds[1]+$Bounds[3] -gt [int]::MaxValue) { throw 'invalid_geometry' }
}
function Assert-DiscoveryRequest($Request) {
    Assert-DiscoveryFields $Request @('request_id','action','payload')
    if ($Request.request_id -isnot [string] -or $Request.request_id -cnotmatch '\A[a-f0-9]{32}\z' -or $Request.action -isnot [string] -or $Request.action -cnotin @('inspect','observe','sample')) { throw 'invalid_request' }
    $payload = $Request.payload
    if ($Request.action -ceq 'inspect') {
        Assert-DiscoveryFields $payload @('window_handle','x','y')
        foreach ($value in @($payload.x,$payload.y)) {
            if (-not (Test-DiscoveryNumber $value) -or $value -lt [int]::MinValue -or $value -gt [int]::MaxValue) { throw 'invalid_point' }
        }
    } else {
        if ($Request.action -ceq 'sample') {
            Assert-DiscoveryFields $payload @('window_handle','reference','viewport','confirmed','image_path')
            if ($payload.confirmed -isnot [bool] -or -not $payload.confirmed -or $payload.image_path -isnot [string] -or $payload.image_path.Length -eq 0 -or $payload.image_path.Length -gt 32767) { throw 'capture_unconfirmed' }
        } else { Assert-DiscoveryFields $payload @('window_handle','reference') @('viewport') }
        $reference = $payload.reference
        Assert-DiscoveryFields $reference @('window_handle','process_id','started_ticks','runtime_id','bounds','vertical_percent','view_size')
        Assert-DiscoveryInteger $reference.window_handle 1 ([long]::MaxValue)
        Assert-DiscoveryInteger $reference.process_id 1 ([int]::MaxValue)
        Assert-DiscoveryInteger $reference.started_ticks 1 ([datetime]::MaxValue.Ticks)
        if ($reference.window_handle -ne $payload.window_handle -or $reference.runtime_id -isnot [string] -or $reference.runtime_id.Length -gt 767 -or $reference.runtime_id -cnotmatch '\A-?[0-9]+(\.-?[0-9]+){0,63}\z') { throw 'invalid_identity' }
        foreach ($part in $reference.runtime_id.Split('.')) {
            $integer = 0
            if (-not [int]::TryParse($part, [Globalization.NumberStyles]::AllowLeadingSign, [Globalization.CultureInfo]::InvariantCulture, [ref]$integer) -or $integer.ToString([Globalization.CultureInfo]::InvariantCulture) -cne $part) { throw 'invalid_identity' }
        }
        Assert-DiscoveryBounds $reference.bounds
        foreach ($value in @($reference.vertical_percent,$reference.view_size)) { if (-not (Test-DiscoveryNumber $value)) { throw 'invalid_metrics' } }
        if ($reference.vertical_percent -lt 0 -or $reference.vertical_percent -gt 100 -or $reference.view_size -le 0 -or $reference.view_size -ge 100) { throw 'invalid_metrics' }
        if ($payload.PSObject.Properties.Name -ccontains 'viewport') {
            Assert-DiscoveryBounds $payload.viewport
            if ([double]$payload.viewport[2]*$payload.viewport[3] -gt 16000000) { throw 'invalid_viewport' }
        }
    }
    Assert-DiscoveryInteger $payload.window_handle 1 ([long]::MaxValue)
}

function Resolve-DiscoveryFrameDestination($payload) {
            if ($payload.confirmed -isnot [bool] -or -not $payload.confirmed -or $null -eq $payload.viewport) { throw 'capture_unconfirmed' }
            $output = [IO.Path]::GetFullPath([string]$payload.image_path)
            $directory = [IO.Path]::GetDirectoryName($output)
            $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
            if (-not $output.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($output) -ne 'frame.png' -or [IO.Path]::GetFileName($directory) -notmatch '^verdiclip-frame-[a-zA-Z0-9_-]+$' -or [IO.File]::Exists($output)) { throw 'invalid_frame_destination' }
            $folder = Get-Item -LiteralPath $directory
            if (-not $folder.PSIsContainer -or ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'invalid_frame_directory' }
    return $output
}
function Save-DiscoveryFrame($payload, $area) {
    $output = Resolve-DiscoveryFrameDestination $payload
            $bitmap = $null
            $graphics = $null
            try {
                $bitmap = [Drawing.Bitmap]::new([int]$area[2], [int]$area[3], [Drawing.Imaging.PixelFormat]::Format32bppRgb)
                $graphics = [Drawing.Graphics]::FromImage($bitmap)
                $graphics.CopyFromScreen([int]$area[0], [int]$area[1], 0, 0, [Drawing.Size]::new([int]$area[2], [int]$area[3]), [Drawing.CopyPixelOperation]::SourceCopy)
                $bitmap.Save($output, [Drawing.Imaging.ImageFormat]::Png)
            } finally {
                if ($null -ne $graphics) { $graphics.Dispose() }
                if ($null -ne $bitmap) { $bitmap.Dispose() }
            }
            if ([IO.FileInfo]::new($output).Length -gt ([double]$area[2] * $area[3] * 4 + 1048576)) { throw 'encoded_frame_exceeds_budget' }

    return $output
}

function Invoke-ReadOnlyDiscoveryRequest($request) {
    Assert-DiscoveryRequest $request
    $process = $null
    $discovery = $null
    $operationFailure = $null
    try {
    if ($request.action -in @('step', 'restore')) { throw 'native_process_required' }
    if ($request.action -eq 'inspect') { [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=inspect_desktop') }
    $payload = $request.payload
    $handle = [IntPtr]([long]$payload.window_handle)
    $discovery = [VerdiClip.Automation.DiscoverySession]::new()
    $discovery.Open($handle.ToInt64(), ($request.action -eq 'inspect'))
    $resolver = $discovery.Resolver
    $root = $resolver.Root
    $process = Get-Process -Id $resolver.ProcessId
    $started = $discovery.StartedTicks
    $windowId = '{0}:{1}:{2}' -f $handle.ToInt64(), $process.Id, $started
    $rootRuntime = $root.RuntimeIdentity

    function Get-Bounds($element) {
        if ($request.action -eq 'inspect') { [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=inspect_bounds') }
        $bounds = $element.Current.BoundingRectangle
        if ($bounds.IsEmpty -or [double]::IsInfinity($bounds.Width) -or [double]::IsNaN($bounds.Width)) { throw 'invalid_geometry' }
        # Insets exclude fractional pixels outside a provider's physical bounds.
        $left = [Math]::Ceiling($bounds.Left)
        $top = [Math]::Ceiling($bounds.Top)
        return @($left, $top, ([Math]::Floor($bounds.Right) - $left), ([Math]::Floor($bounds.Bottom) - $top))
    }
    function Get-Scroll($element) {
        return $element.GetScroll()
    }
    function Get-Reference($element, $pattern) {
        $bounds = Get-Bounds $element
        return @{
            window_handle = $handle.ToInt64(); process_id = $process.Id
            started_ticks = $started; runtime_id = $element.RuntimeIdentity
            bounds = $bounds; vertical_percent = [double]$pattern.Current.VerticalScrollPercent
            view_size = [double]$pattern.Current.VerticalViewSize
        }
    }
    function Find-Ancestors([double]$x, [double]$y) {
        return ,($resolver.FindAncestors($x, $y))
    }
    if ($request.action -eq 'inspect') {
        $chain = Find-Ancestors ([double]$payload.x) ([double]$payload.y)
        $nodes = @()
        for ($index = 0; $index -lt $chain.Count; $index++) {
            $element = $chain[$index]
            if ($request.action -eq 'inspect') { [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=inspect_node_identity') }
            $runtime = $element.RuntimeIdentity
            $identity = $windowId + '/' + $runtime
            $parent = if ($index + 1 -lt $chain.Count) { $windowId + '/' + $chain[$index + 1].RuntimeIdentity } else { $null }
            $pattern = Get-Scroll $element
            if ($request.action -eq 'inspect') { [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=inspect_control') }
            $kind = 'other'
            if ($runtime -eq $rootRuntime) { $kind = 'window' }
            elseif ($null -ne $pattern) { $kind = 'content' }
            elseif ($element.Current.ControlType -eq 50014 -and $element.Current.Orientation -eq 2) { $kind = 'vertical_scrollbar' }
            if ($request.action -eq 'inspect') { [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=inspect_reference') }
            $reference = if ($null -ne $pattern) { Get-Reference $element $pattern } else { $null }
            $nodes += @{ identity=$identity; window_identity=$windowId; kind=$kind; bounds=(Get-Bounds $element); parent_identity=$parent; vertically_scrollable=($null -ne $pattern); reference=$reference }
        }
        if ($request.action -eq 'inspect') { [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=inspect_complete') }
        $result = @{ nodes=$nodes; hit_identity=$nodes[0].identity; window_identity=$windowId }
    } else {
        $reference = $payload.reference
        if ([long]$reference.window_handle -ne $handle.ToInt64() -or [long]$reference.started_ticks -ne $started -or [int]$reference.process_id -ne $process.Id) { throw 'target_identity_changed' }
        $bounds = $reference.bounds
        $chain = Find-Ancestors ([double]$bounds[0] + [double]$bounds[2] / 2) ([double]$bounds[1] + [double]$bounds[3] / 2)
        $element = $null
        foreach ($candidate in $chain) {
            if ($candidate.RuntimeIdentity -eq $reference.runtime_id) { $element = $candidate; break }
        }
        if ($null -eq $element -or -not (Test-SamePhysicalBounds (Get-Bounds $element) $bounds)) { throw 'target_geometry_changed' }
        $pattern = Get-Scroll $element
        if ($null -eq $pattern -or $element.Current.IsOffscreen -or -not $element.Current.IsEnabled) { throw 'unsupported_scroll_area' }
        $area = $bounds
        if ($null -ne $payload.viewport) {
            $area = @($payload.viewport)
            if ($area.Count -ne 4) { throw 'invalid_viewport' }
            foreach ($coordinate in $area) {
                $number = [double]$coordinate
                if ([double]::IsNaN($number) -or [double]::IsInfinity($number) -or $number -ne [Math]::Round($number)) { throw 'invalid_viewport' }
            }
            if ($area[2] -le 0 -or $area[3] -le 0 -or [double]$area[2] * $area[3] -gt 16000000 -or $area[0] -lt $bounds[0] -or $area[1] -lt $bounds[1] -or $area[0] + $area[2] -gt $bounds[0] + $bounds[2] -or $area[1] + $area[3] -gt $bounds[1] + $bounds[3]) { throw 'viewport_outside_target' }
        }
        # Full-area top-level occlusion refusal, not sampled-point inference.
        # In-process/custom composited overlays still require native qualification;
        # this is not a claim of universal semantic visibility.
        if (-not [CaptureDesktop]::Unoccluded($handle, [int]$area[0], [int]$area[1], [int]$area[2], [int]$area[3])) { throw 'target_occluded_or_uncertain' }
        $position = [double]$pattern.Current.VerticalScrollPercent
        $view = [double]$pattern.Current.VerticalViewSize
        if ([double]::IsNaN($position) -or [double]::IsNaN($view) -or $position -lt 0 -or $position -gt 100 -or $view -le 0 -or $view -ge 100) { throw 'unsupported_scroll_metrics' }
        $updated = Get-Reference $element $pattern
        $result = @{ reference=$updated; at_end=($updated.vertical_percent -ge 100) }
        if ($request.action -eq 'sample') {
            $output = Save-DiscoveryFrame $payload $area
            $result.viewport = $area
            $result.image_path = $output
        }
    }
    $discovery.Verify($handle.ToInt64())
} catch {
    $operationFailure = $_.Exception
}
finally {
    if ($null -ne $process) {
        try { $process.Dispose() } catch {
            if ($null -eq $operationFailure) { $operationFailure = $_.Exception }
            else { $operationFailure = [AggregateException]::new([Exception[]]@($operationFailure, $_.Exception)) }
        }
        $process = $null
    }
    if ($null -ne $discovery) {
        try { $discovery.Dispose() } catch {
            if ($null -eq $operationFailure) { $operationFailure = $_.Exception }
            else { $operationFailure = [AggregateException]::new([Exception[]]@($operationFailure, $_.Exception)) }
        }
        $discovery = $null
    }
}
    if ($null -ne $operationFailure) { throw $operationFailure }
    return $result
}

try {
    if (-not $NativeOnly) {
        Add-Type -AssemblyName System.Drawing
        # Read-only resolver and shared ABI only; never compile mutation here.
        Add-Type -Path @((Join-Path $PSScriptRoot "uia_nofocus_interop.cs"), (Join-Path $PSScriptRoot "uia_discovery.cs"), (Join-Path $PSScriptRoot "uia_desktop.cs"))
        [VerdiClip.Automation.DiscoveryPolicy]::RequireNativeOnly()
    } else {
        . (Join-Path $PSScriptRoot "uia_native_request.ps1")
        Add-Type -Path @((Join-Path $PSScriptRoot "uia_nofocus_interop.cs"), (Join-Path $PSScriptRoot "uia_nofocus.cs"), (Join-Path $PSScriptRoot "uia_desktop.cs"))
        [VerdiClip.Automation.NoFocusScroll]::RequireNativeOnlyProcess()
    }

} catch {
    $writer.WriteLine((@{ request_id=''; ok=$false; error='worker_startup_unavailable' } | ConvertTo-Json -Compress))
    exit 1
}
# Reuse this MTA only while the parent's finite active/idle lease remains alive.
[Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=runtime_ready')
$writer.WriteLine((@{ ready=$true; protocol=1 } | ConvertTo-Json -Compress))
while ($null -ne ($requestLine = $reader.ReadLine())) {
    $requestId = ''
    $process = $null
    $discovery = $null
    $operationFailure = $null
try {
    $request = $requestLine | ConvertFrom-Json
    $requestId = [string]$request.request_id
    if ($requestId -notmatch '^[a-f0-9]{32}$') { throw 'invalid_request' }
    if ($request.action -notin @('inspect', 'observe', 'sample', 'step', 'restore')) {
        throw 'unsupported_action'
    }
    if ($NativeOnly) {
        [VerdiClip.Automation.NoFocusScroll]::RequireNativeOnlyProcess()
        [Console]::Error.WriteLine('VERDICLIP_UIA_PHASE=native_process_verified')
        $result = Invoke-NativeScrollRequest $request
        $writer.WriteLine((@{ request_id=$requestId; ok=$true; result=$result } | ConvertTo-Json -Depth 12 -Compress))
        continue
    }
    $result = Invoke-ReadOnlyDiscoveryRequest $request
} catch { $operationFailure = $_.Exception }

if ($null -eq $operationFailure) {
    $writer.WriteLine((@{ request_id=$requestId; ok=$true; result=$result } | ConvertTo-Json -Depth 12 -Compress))
} else {
    # Provider text may contain document content. Return only a bounded generic code.
    $writer.WriteLine((@{ request_id=$requestId; ok=$false; error='unsupported_or_changed' } | ConvertTo-Json -Compress))
}
}
$reader.Dispose()
$writer.Dispose()
$channel.Dispose()
