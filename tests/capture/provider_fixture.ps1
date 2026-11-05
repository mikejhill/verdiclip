param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce,
    [Parameter(Mandatory=$true)][ValidateSet('end','stop','interference')][string]$Case,
    [switch]$ExecuteAfterHandoff
)
$ErrorActionPreference = 'Stop'
if (-not $ExecuteAfterHandoff) { throw 'Fixture requires explicit desktop handoff; no UI was created.' }
# Loaded only in the separately owned STA child after authorization.
$clock = [Diagnostics.Stopwatch]::StartNew()
$window = $null; $timer = $null; $closed = $false
$fixtureDirectory = [IO.Path]::GetFullPath($Directory)
if (-not [IO.Directory]::Exists($fixtureDirectory)) { throw 'Runner must create private case directory.' }
try {
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
Write-Output ('FIXTURE_PHASE=assemblies-loaded utc=' + [DateTime]::UtcNow.ToString('o'))
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) { return }
$fixtureProcess = Get-Process -Id $PID
$startedTicks = $fixtureProcess.StartTime.ToUniversalTime().Ticks
Write-Output ('FIXTURE_PHASE=before-window utc=' + [DateTime]::UtcNow.ToString('o'))
$window = [Windows.Window]::new()
Write-Output ('FIXTURE_PHASE=window-created utc=' + [DateTime]::UtcNow.ToString('o'))
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
Write-Output ('FIXTURE_PHASE=controls-created utc=' + [DateTime]::UtcNow.ToString('o'))
$pixelWidth = 384
$pixelHeight = if ($Case -eq 'end') { 1120 } else { 8000 }
# Input is prepared before native launch; no CodeDOM/compiler cold-start here.
$pixels = [IO.File]::ReadAllBytes((Join-Path $fixtureDirectory 'fixture-pixels.bgr32'))
if ($pixels.Length -ne ($pixelWidth * $pixelHeight * 4)) { throw 'Invalid synthetic bitmap length' }
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) { return }
$bitmap = [Windows.Media.Imaging.BitmapSource]::Create($pixelWidth,$pixelHeight,96,96,[Windows.Media.PixelFormats]::Bgr32,$null,$pixels,($pixelWidth*4))
$bitmap.Freeze()
Write-Output ('FIXTURE_PHASE=bitmap-created utc=' + [DateTime]::UtcNow.ToString('o'))
$image = [Windows.Controls.Image]::new()
$image.Source = $bitmap; $image.Width = $pixelWidth; $image.Height = $pixelHeight
$image.Stretch = 'None'; $image.HorizontalAlignment = 'Left'
[Windows.Media.RenderOptions]::SetBitmapScalingMode($image, 'NearestNeighbor')
$viewer.Content = $image; $stack.Children.Add($viewer) | Out-Null
$window.Content = $stack
$sequence = 0; $closed = $false; $ready = $false; $renderObserved = $false; $maximumOffset = 0.0; $endSeen = $false
$lastCommand = 0; $pendingCommand = $null
# Bounded owned-fixture evidence only; no window titles, values or source paths.
$focusEvents = 0
function Write-FocusPhase([string]$phase) {
    if ($script:focusEvents -ge 4) { return }
    $script:focusEvents++
    try {
    $methods = @([Diagnostics.StackTrace]::new().GetFrames() | ForEach-Object {
        $method = $_.GetMethod()
        $type = $method.DeclaringType.FullName
        if (($type -like 'System.Windows.*' -or $type -like 'MS.Internal.*') -and
            ($type -like '*Automation*' -or $method.Name -match 'Focus|Activat')) {
            $type + '.' + $method.Name
        }
    } | Select-Object -First 12)
    [Console]::Error.WriteLine('FIXTURE_FOCUS=' + $phase + ' elapsed_ms=' + $clock.ElapsedMilliseconds + ' methods=' + ($methods -join ','))
    } catch { } # Optional evidence never changes fixture behavior.
}
$window.Add_Activated({ Write-FocusPhase 'activated' })
$viewer.Add_GotKeyboardFocus({ Write-FocusPhase 'viewer-focus' })
function Write-FixtureState {
    if (-not $ready -or -not $window.IsLoaded -or $closed) { return }
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
    $temporary = Join-Path $fixtureDirectory 'fixture-state.partial'
    [IO.File]::WriteAllText($temporary,($state | ConvertTo-Json -Depth 5))
    if ($sequence -gt 10000) { throw 'Fixture snapshot budget exceeded' }
    $target = Join-Path $fixtureDirectory ('fixture-state-{0:D6}.json' -f $sequence)
    [IO.File]::Move($temporary,$target)
}
$timer = [Windows.Threading.DispatcherTimer]::new()
$timer.Interval = [TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    if ($clock.Elapsed.TotalSeconds -ge 90 -or [IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
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
            (@{nonce=$Nonce;sequence=$pendingCommand.sequence;offset=$viewer.VerticalOffset} | ConvertTo-Json))
        if ([IO.File]::Exists($ackPath)) { [IO.File]::Replace($ackTemporary,$ackPath,[NullString]::Value) }
        else { [IO.File]::Move($ackTemporary,$ackPath) }
        $script:pendingCommand = $null
    }
    Write-FixtureState
})
$window.Add_ContentRendered({
    # One ordinary owned-window activation; never force foreground or send input.
    if ($script:ready) {
        if (-not $script:renderObserved) {
            [Console]::Error.WriteLine('FIXTURE_PHASE=repeat-render-ignored')
            $script:renderObserved = $true
        }
        return
    } # Later renders cannot reactivate an established fixture.
    [Console]::Error.WriteLine('FIXTURE_PHASE=initial-activation utc=' + [DateTime]::UtcNow.ToString('o'))
    $null = $window.Activate()
    $script:ready = $true
    Write-FixtureState
})
$viewer.Add_ScrollChanged({ Write-FixtureState })
$window.Add_Closed({ $script:closed=$true; $timer.Stop() })
Write-Output ('FIXTURE_PHASE=before-show utc=' + [DateTime]::UtcNow.ToString('o'))
$timer.Start() # Cancellation does not depend on ContentRendered firing.
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) { return }
$window.ShowDialog() | Out-Null
}
finally {
    if ($null -ne $timer) { $timer.Stop() }
    if ($null -ne $window -and $window.IsVisible) { $window.Close() }
    [IO.File]::WriteAllText((Join-Path $fixtureDirectory 'fixture-retired.json'),(@{nonce=$Nonce;pid=$PID;closed=$true} | ConvertTo-Json))
}
