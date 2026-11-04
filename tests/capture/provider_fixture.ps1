param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce,
    [Parameter(Mandatory=$true)][ValidateSet('end','stop','interference')][string]$Case,
    [switch]$ExecuteAfterHandoff
)
$ErrorActionPreference = 'Stop'
if (-not $ExecuteAfterHandoff) { throw 'Fixture requires explicit desktop handoff; no UI was created.' }
# Loaded only in the separately owned STA child after authorization.
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
$fixtureDirectory = [IO.Path]::GetFullPath($Directory)
if (-not [IO.Directory]::Exists($fixtureDirectory)) { throw 'Runner must create private case directory.' }
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
# Pure managed generation avoids millions of interpreted PowerShell iterations.
Add-Type -TypeDefinition @'
public static class QualificationPixels {
    public static byte[] Make(int width, int height) {
        var pixels = new byte[checked(width * height * 4)];
        for (int y = 0; y < height; y++) for (int x = 0; x < width; x++) {
            int i = (y * width + x) * 4;
            pixels[i] = (byte)((y * 17 + x * 11) % 251);
            pixels[i+1] = (byte)((y * 31 + x * 7 + y / 251) % 253);
            pixels[i+2] = (byte)((y * 43 + x * 19 + y / 127) % 255);
            pixels[i+3] = 255;
        }
        return pixels;
    }
}
'@
$pixels = [QualificationPixels]::Make($pixelWidth, $pixelHeight)
$bitmap = [Windows.Media.Imaging.BitmapSource]::Create($pixelWidth,$pixelHeight,96,96,[Windows.Media.PixelFormats]::Bgr32,$null,$pixels,($pixelWidth*4))
$bitmap.Freeze()
$image = New-Object Windows.Controls.Image
$image.Source = $bitmap; $image.Width = $pixelWidth; $image.Height = $pixelHeight
$image.Stretch = 'None'; $image.HorizontalAlignment = 'Left'
[Windows.Media.RenderOptions]::SetBitmapScalingMode($image, 'NearestNeighbor')
$viewer.Content = $image; $stack.Children.Add($viewer) | Out-Null
$window.Content = $stack
$sequence = 0; $closed = $false; $maximumOffset = 0.0; $endSeen = $false
$lastCommand = 0; $pendingCommand = $null
$clock = [Diagnostics.Stopwatch]::StartNew()
function Write-FixtureState {
    if (-not $window.IsLoaded -or $closed) { return }
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
        sequence=$sequence; visible=[bool]$window.IsVisible; closed=$closed
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
    # One ordinary owned-window activation; never force foreground or send input.
    $null = $window.Activate()
    Write-FixtureState; $timer.Start()
})
$viewer.Add_ScrollChanged({ Write-FixtureState })
$window.Add_Closed({ $script:closed=$true; $timer.Stop() })
try { $window.ShowDialog() | Out-Null }
finally {
    $timer.Stop()
    [IO.File]::WriteAllText((Join-Path $fixtureDirectory 'fixture-retired.json'),(@{nonce=$Nonce;pid=$PID;closed=$true} | ConvertTo-Json))
}
