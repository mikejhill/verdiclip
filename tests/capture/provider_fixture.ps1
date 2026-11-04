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
$window = New-Object Windows.Window
$window.Title = "VerdiClip synthetic scroll fixture $Nonce"
$window.Width = 480; $window.Height = 420
$window.WindowStartupLocation = 'CenterScreen'
$window.ResizeMode = 'NoResize'
$window.UseLayoutRounding = $true
$stack = New-Object Windows.Controls.StackPanel
$banner = New-Object Windows.Controls.TextBlock
$banner.Text = "SYNTHETIC ONLY - $Case - $($Nonce.Substring(0,8))"
$banner.Margin = '12'; $stack.Children.Add($banner) | Out-Null
$viewer = New-Object Windows.Controls.ScrollViewer
$viewer.Width = 400; $viewer.Height = 280
$viewer.HorizontalScrollBarVisibility = 'Disabled'
$viewer.VerticalScrollBarVisibility = 'Visible'
$viewer.CanContentScroll = $false
[Windows.Automation.AutomationProperties]::SetAutomationId($viewer, 'VerdiClipQualificationScrollViewer')
$pixelWidth = 384
$pixelHeight = if ($Case -eq 'end') { 1120 } else { 8000 }
# Input is prepared before native launch; no CodeDOM/compiler cold-start here.
$pixels = [IO.File]::ReadAllBytes((Join-Path $fixtureDirectory 'fixture-pixels.bgr32'))
if ($pixels.Length -ne ($pixelWidth * $pixelHeight * 4)) { throw 'Invalid synthetic bitmap length' }
if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) { return }
$bitmap = [Windows.Media.Imaging.BitmapSource]::Create($pixelWidth,$pixelHeight,96,96,[Windows.Media.PixelFormats]::Bgr32,$null,$pixels,($pixelWidth*4))
$bitmap.Freeze()
$image = New-Object Windows.Controls.Image
$image.Source = $bitmap; $image.Width = $pixelWidth; $image.Height = $pixelHeight
$image.Stretch = 'None'; $image.HorizontalAlignment = 'Left'
[Windows.Media.RenderOptions]::SetBitmapScalingMode($image, 'NearestNeighbor')
$viewer.Content = $image; $stack.Children.Add($viewer) | Out-Null
$window.Content = $stack
$sequence = 0; $closed = $false; $ready = $false; $maximumOffset = 0.0; $endSeen = $false
$lastCommand = 0; $pendingCommand = $null
function Write-FixtureState {
    if (-not $ready -or -not $window.IsLoaded -or $closed) { return }
    $handle = (New-Object Windows.Interop.WindowInteropHelper($window)).Handle.ToInt64()
    $point = $viewer.PointToScreen((New-Object Windows.Point(0,0)))
    $bottom = $viewer.PointToScreen((New-Object Windows.Point([Math]::Min($pixelWidth,$viewer.ViewportWidth),$viewer.ViewportHeight)))
    $bar = $viewer.Template.FindName('PART_VerticalScrollBar', $viewer)
    if ($null -eq $bar -or -not $bar.IsVisible) { throw 'Owned vertical scrollbar unavailable' }
    $barPoint = $bar.PointToScreen((New-Object Windows.Point(($bar.ActualWidth/2),($bar.ActualHeight/2))))
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
$timer = New-Object Windows.Threading.DispatcherTimer
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
    Write-Output ('FIXTURE_PHASE=content-rendered utc=' + [DateTime]::UtcNow.ToString('o'))
    # One ordinary owned-window activation; never force foreground or send input.
    if ($ready) { return } # Later renders cannot reactivate an established fixture.
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
