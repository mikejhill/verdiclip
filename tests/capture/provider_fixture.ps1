param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce,
    [Parameter(Mandatory=$true)][ValidateSet('end','stop','interference')][string]$Case,
    [switch]$ExecuteAfterHandoff
)
$ErrorActionPreference = 'Stop'
if (-not $ExecuteAfterHandoff) { throw 'Fixture requires explicit desktop handoff; no UI was created.' }
# Fixed owned frames contain only a validated nonce, booleans and finite numbers.
# Do not load a general JSON serializer on the WPF dispatcher or during retirement.
function ConvertTo-FixtureJsonValue([object]$value, [string]$kind) {
    if ($kind -eq 'nonce') {
        if ($value -isnot [string] -or $value -cnotmatch '\A[a-f0-9]{32}\z') { throw 'Invalid fixture nonce' }
        return '"' + $value + '"'
    }
    if ($kind -eq 'boolean') {
        if ($value -isnot [bool]) { throw 'Invalid fixture boolean' }
        return $value.ToString().ToLowerInvariant()
    }
    if ($kind -ne 'integer' -and $kind -ne 'number') { throw 'Invalid fixture field kind' }
    if ($value -is [int] -or $value -is [long]) {
        return $value.ToString([Globalization.CultureInfo]::InvariantCulture)
    }
    if ($kind -eq 'number' -and $value -is [double]) {
        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) { throw 'Nonfinite fixture number' }
        $text = $value.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
        # Preserve JSON floating-point values even when the value is integral.
        if ($text.IndexOf('.') -lt 0 -and $text.IndexOf('E') -lt 0 -and $text.IndexOf('e') -lt 0) { $text += '.0' }
        return $text
    }
    throw 'Invalid fixture number'
}
function ConvertTo-FixtureStateJson([hashtable]$state) {
    if ($state.physical_rectangle -isnot [hashtable]) { throw 'Invalid fixture rectangle' }
    $fields = @(
        '"nonce":' + (ConvertTo-FixtureJsonValue $state.nonce 'nonce')
        '"pid":' + (ConvertTo-FixtureJsonValue $state.pid 'integer')
        '"hwnd":' + (ConvertTo-FixtureJsonValue $state.hwnd 'integer')
        '"started_ticks":' + (ConvertTo-FixtureJsonValue $state.started_ticks 'integer')
        '"sequence":' + (ConvertTo-FixtureJsonValue $state.sequence 'integer')
        '"visible":' + (ConvertTo-FixtureJsonValue $state.visible 'boolean')
        '"closed":' + (ConvertTo-FixtureJsonValue $state.closed 'boolean')
        '"activation_complete":' + (ConvertTo-FixtureJsonValue $state.activation_complete 'boolean')
        '"offset":' + (ConvertTo-FixtureJsonValue $state.offset 'number')
        '"scrollable_height":' + (ConvertTo-FixtureJsonValue $state.scrollable_height 'number')
        '"maximum_offset":' + (ConvertTo-FixtureJsonValue $state.maximum_offset 'number')
        '"end_seen":' + (ConvertTo-FixtureJsonValue $state.end_seen 'boolean')
        '"viewport_height":' + (ConvertTo-FixtureJsonValue $state.viewport_height 'number')
        '"content_rows":' + (ConvertTo-FixtureJsonValue $state.content_rows 'integer')
        '"scrollbar_x":' + (ConvertTo-FixtureJsonValue $state.scrollbar_x 'integer')
        '"scrollbar_y":' + (ConvertTo-FixtureJsonValue $state.scrollbar_y 'integer')
        '"physical_rectangle":{' +
            '"x":' + (ConvertTo-FixtureJsonValue $state.physical_rectangle.x 'integer') +
            ',"y":' + (ConvertTo-FixtureJsonValue $state.physical_rectangle.y 'integer') +
            ',"width":' + (ConvertTo-FixtureJsonValue $state.physical_rectangle.width 'integer') +
            ',"height":' + (ConvertTo-FixtureJsonValue $state.physical_rectangle.height 'integer') + '}'
    )
    return '{' + ($fields -join ',') + '}'
}
function ConvertTo-FixtureAckJson([hashtable]$ack) {
    return '{"nonce":' + (ConvertTo-FixtureJsonValue $ack.nonce 'nonce') +
        ',"sequence":' + (ConvertTo-FixtureJsonValue $ack.sequence 'integer') +
        ',"offset":' + (ConvertTo-FixtureJsonValue $ack.offset 'number') + '}'
}
function ConvertTo-FixtureRetiredJson([hashtable]$receipt) {
    return '{"nonce":' + (ConvertTo-FixtureJsonValue $receipt.nonce 'nonce') +
        ',"pid":' + (ConvertTo-FixtureJsonValue $receipt.pid 'integer') +
        ',"closed":' + (ConvertTo-FixtureJsonValue $receipt.closed 'boolean') + '}'
}
function Get-FixtureStartedTicks {
    $ownedProcess = [Diagnostics.Process]::GetCurrentProcess()
    try { return $ownedProcess.StartTime.ToUniversalTime().Ticks }
    finally { $ownedProcess.Dispose() }
}
# Loaded only in the separately owned STA child after authorization.
$clock = [Diagnostics.Stopwatch]::StartNew()
$script:fixtureTraceEnabled = $false
$window = $null; $timer = $null; $closed = $false
$fixtureDirectory = [IO.Path]::GetFullPath($Directory)
if (-not [IO.Directory]::Exists($fixtureDirectory)) { throw 'Runner must create private case directory.' }
try {
if ($env:VERDICLIP_FIXTURE_TRACE_DIR) {
    try {
        . (Join-Path $PSScriptRoot 'fixture_startup_trace.ps1')
        Initialize-FixtureTrace $env:VERDICLIP_FIXTURE_TRACE_DIR $Nonce
    } catch { $script:fixtureTraceEnabled = $false }
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'entry'; Write-FixtureTrace 'assemblies_begin' }
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'assemblies_end' }
Write-Output ('FIXTURE_PHASE=assemblies-loaded utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'identity_begin' }
$startedTicks = Get-FixtureStartedTicks
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'identity_end' }
Write-Output ('FIXTURE_PHASE=before-window utc=' + [DateTime]::UtcNow.ToString('o'))
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'window_begin' }
$window = [Windows.Window]::new()
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'window_end' }
Write-Output ('FIXTURE_PHASE=window-created utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'controls_begin' }
$window.Title = "VerdiClip synthetic scroll fixture $Nonce"
$window.Width = 480; $window.Height = 420
$window.WindowStartupLocation = 'CenterScreen'
$window.ResizeMode = 'NoResize'
$window.UseLayoutRounding = $true
$stack = [Windows.Controls.StackPanel]::new()
$banner = [Windows.Controls.TextBlock]::new()
$banner.Text = "SYNTHETIC ONLY - $Case - $($Nonce.Substring(0,8))"
$banner.Margin = '12'; $stack.Children.Add($banner) | Out-Null
$viewer = [Windows.Controls.ScrollViewer]::new()
$viewer.Width = 400; $viewer.Height = 280
$viewer.HorizontalScrollBarVisibility = 'Disabled'
$viewer.VerticalScrollBarVisibility = 'Visible'
$viewer.CanContentScroll = $false
[Windows.Automation.AutomationProperties]::SetAutomationId($viewer, 'VerdiClipQualificationScrollViewer')
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'controls_end' }
Write-Output ('FIXTURE_PHASE=controls-created utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
$pixelWidth = 384
$pixelHeight = if ($Case -eq 'end') { 1120 } else { 8000 }
# Input is prepared before native launch; no CodeDOM/compiler cold-start here.
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'pixels_begin' }
$pixels = [IO.File]::ReadAllBytes((Join-Path $fixtureDirectory 'fixture-pixels.bgr32'))
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'pixels_end' }
Write-Output ('FIXTURE_PHASE=pixels-read utc=' + [DateTime]::UtcNow.ToString('o'))
if ($pixels.Length -ne ($pixelWidth * $pixelHeight * 4)) { throw 'Invalid synthetic bitmap length' }
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'bitmap_begin' }
$bitmap = [Windows.Media.Imaging.BitmapSource]::Create($pixelWidth,$pixelHeight,96,96,[Windows.Media.PixelFormats]::Bgr32,$null,$pixels,($pixelWidth*4))
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'bitmap_end' }
Write-Output ('FIXTURE_PHASE=bitmap-copied utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'freeze_begin' }
$bitmap.Freeze()
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'freeze_end' }
Write-Output ('FIXTURE_PHASE=bitmap-created utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'content_begin' }
$image = [Windows.Controls.Image]::new()
$image.Source = $bitmap; $image.Width = $pixelWidth; $image.Height = $pixelHeight
$image.Stretch = 'None'; $image.HorizontalAlignment = 'Left'
[Windows.Media.RenderOptions]::SetBitmapScalingMode($image, 'NearestNeighbor')
$viewer.Content = $image; $stack.Children.Add($viewer) | Out-Null
$window.Content = $stack
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'content_end' }
Write-Output ('FIXTURE_PHASE=content-assigned utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
$sequence = 0; $closed = $false; $ready = $false; $renderObserved = $false; $maximumOffset = 0.0; $endSeen = $false
$lastCommand = 0; $pendingCommand = $null
# Bounded owned-fixture evidence only; no window titles, values or source paths.
$focusEvents = 0
function Write-FocusPhase([string]$phase) {
    if ($script:focusEvents -ge 4) { return }
    $script:focusEvents++
    try {
    [Console]::Error.WriteLine('FIXTURE_FOCUS=' + $phase + ' elapsed_ms=' + $clock.ElapsedMilliseconds)
    } catch { } # Optional evidence never changes fixture behavior.
}
$window.Add_Activated({ Write-FocusPhase 'activated' })
$viewer.Add_GotKeyboardFocus({ Write-FocusPhase 'viewer-focus' })
function Confirm-FixtureActivation([object]$owner) {
    # ShowDialog normally activates this owned window already. Do not enter a
    # second native activation transaction from ContentRendered unnecessarily.
    if (-not $owner.IsActive) {
        if (-not $owner.Activate()) { throw 'Owned fixture activation refused' }
    }
    if (-not $owner.IsActive) { throw 'Owned fixture activation incomplete' }
}
function Write-SnapshotPhase([string]$phase) {
    if ($script:snapshotTraceComplete) { return }
    # Exact fixed phase names and elapsed timings only. No UI content or CPU queries.
    try {
    [Console]::Error.WriteLine('FIXTURE_SNAPSHOT=' + $phase +
        ' elapsed_ms=' + $clock.ElapsedMilliseconds)
    } catch { } # Diagnostics never change fixture admission or error propagation.
}
$script:snapshotTraceComplete = $false
function Write-FixtureState {
    if (-not $ready -or -not $window.IsLoaded -or $closed) { return }
    if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'snapshot_begin' }
    Write-SnapshotPhase 'geometry-start'
    $handle = ([Windows.Interop.WindowInteropHelper]::new($window)).Handle.ToInt64()
    $point = $viewer.PointToScreen(([Windows.Point]::new(0,0)))
    $bottom = $viewer.PointToScreen(([Windows.Point]::new([Math]::Min($pixelWidth,$viewer.ViewportWidth),$viewer.ViewportHeight)))
    $bar = $viewer.Template.FindName('PART_VerticalScrollBar', $viewer)
    if ($null -eq $bar -or -not $bar.IsVisible) { throw 'Owned vertical scrollbar unavailable' }
    $barPoint = $bar.PointToScreen(([Windows.Point]::new(($bar.ActualWidth/2),($bar.ActualHeight/2))))
    $script:sequence++
    $script:maximumOffset = [Math]::Max($maximumOffset, $viewer.VerticalOffset)
    if ($viewer.ScrollableHeight -gt 0 -and $viewer.VerticalOffset -ge ($viewer.ScrollableHeight - 0.01)) { $script:endSeen = $true }
    $state = @{
        nonce=$Nonce; pid=$PID; hwnd=$handle; started_ticks=$startedTicks
        sequence=$sequence; visible=[bool]$window.IsVisible; closed=$closed; activation_complete=$ready
        offset=$viewer.VerticalOffset; scrollable_height=$viewer.ScrollableHeight
        maximum_offset=$maximumOffset; end_seen=$endSeen
        viewport_height=$viewer.ViewportHeight; content_rows=$pixelHeight
        scrollbar_x=[int][Math]::Floor($barPoint.X); scrollbar_y=[int][Math]::Floor($barPoint.Y)
        physical_rectangle=@{x=[int][Math]::Ceiling($point.X);y=[int][Math]::Ceiling($point.Y)
            width=[int][Math]::Floor($bottom.X-$point.X);height=[int][Math]::Floor($bottom.Y-$point.Y)}
    }
    Write-SnapshotPhase 'state-built'
    $json = ConvertTo-FixtureStateJson $state
    Write-SnapshotPhase 'serialized'
    $temporary = Join-Path $fixtureDirectory 'fixture-state.partial'
    [IO.File]::WriteAllText($temporary,$json)
    if ($sequence -gt 10000) { throw 'Fixture snapshot budget exceeded' }
    $target = Join-Path $fixtureDirectory ('fixture-state-{0:D6}.json' -f $sequence)
    if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
    [IO.File]::Move($temporary,$target)
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'snapshot_end' }
    Write-SnapshotPhase 'published'
    $script:snapshotTraceComplete = $true
}
$timer = [Windows.Threading.DispatcherTimer]::new()
$timer.Interval = [TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    if ($clock.Elapsed.TotalSeconds -ge 90 -or [IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
        if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
        $timer.Stop(); $window.Close(); return
    }
    $commandPath = Join-Path $fixtureDirectory 'fixture-command.json'
    if ([IO.File]::Exists($commandPath)) {
        $command = [IO.File]::ReadAllText($commandPath) | ConvertFrom-Json
        if ($command.nonce -cne $Nonce -or $command.operation -cne 'scroll-percent' -or
            ($command.sequence -isnot [long] -and $command.sequence -isnot [int]) -or
            ($command.percent -isnot [int] -and $command.percent -isnot [long] -and $command.percent -isnot [double]) -or
            $command.percent -lt 0 -or $command.percent -gt 100) { throw 'Invalid owned fixture command' }
        if ($command.sequence -gt $lastCommand) {
            $script:lastCommand = $command.sequence
            $script:pendingCommand = $command
            $viewer.ScrollToVerticalOffset($viewer.ScrollableHeight * $command.percent / 100.0)
        }
    }
    if ($null -ne $pendingCommand -and [Math]::Abs($viewer.VerticalOffset - $viewer.ScrollableHeight * $pendingCommand.percent / 100.0) -lt 0.1) {
        $ackTemporary=Join-Path $fixtureDirectory 'fixture-command-ack.partial'
        $ackPath=Join-Path $fixtureDirectory 'fixture-command-ack.json'
        [IO.File]::WriteAllText($ackTemporary,
            (ConvertTo-FixtureAckJson @{nonce=$Nonce;sequence=$pendingCommand.sequence;offset=$viewer.VerticalOffset}))
        if ([IO.File]::Exists($ackPath)) { [IO.File]::Replace($ackTemporary,$ackPath,[NullString]::Value) }
        else { [IO.File]::Move($ackTemporary,$ackPath) }
        $script:pendingCommand = $null
    }
    Write-FixtureState
})
$window.Add_ContentRendered({
    if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'render' }
    # One ordinary owned-window activation; never force foreground or send input.
    if ($script:ready) {
        if (-not $script:renderObserved) {
            [Console]::Error.WriteLine('FIXTURE_PHASE=repeat-render-ignored')
            $script:renderObserved = $true
        }
        return
    } # Later renders cannot reactivate an established fixture.
    [Console]::Error.WriteLine('FIXTURE_PHASE=initial-activation utc=' + [DateTime]::UtcNow.ToString('o'))
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'activation_begin' }
    Confirm-FixtureActivation $window
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'activation_end' }
    [Console]::Error.WriteLine('FIXTURE_PHASE=activation-complete utc=' + [DateTime]::UtcNow.ToString('o'))
    $script:ready = $true
    Write-FixtureState
    if ($script:snapshotTraceComplete) {
        [Console]::Error.WriteLine('FIXTURE_PHASE=initial-snapshot-written utc=' + [DateTime]::UtcNow.ToString('o'))
    }
})
$viewer.Add_ScrollChanged({ Write-FixtureState })
$window.Add_Closed({ $script:closed=$true; $timer.Stop() })
Write-Output ('FIXTURE_PHASE=before-show utc=' + [DateTime]::UtcNow.ToString('o'))
$timer.Start() # Cancellation does not depend on ContentRendered firing.
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'stop_observed' }
    return
}
if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'show_begin' }
$window.ShowDialog() | Out-Null
}
finally {
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'retirement_begin' }
    try {
    if ($null -ne $timer) { $timer.Stop() }
    if ($null -ne $window -and $window.IsVisible) { $window.Close() }
    [IO.File]::WriteAllText((Join-Path $fixtureDirectory 'fixture-retired.json'),(ConvertTo-FixtureRetiredJson @{nonce=$Nonce;pid=$PID;closed=$true}))
    if ($script:fixtureTraceEnabled) { Write-FixtureTrace 'fixture_retired' }
    } finally { if ($script:fixtureTraceEnabled) { Close-FixtureTrace } }
}
