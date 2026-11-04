param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$Nonce,
    [Parameter(Mandatory=$true)][ValidateSet('end')][string]$Case,
    [switch]$ExecuteAfterHandoff
)
$ErrorActionPreference = 'Stop'
if (-not $ExecuteAfterHandoff) { throw 'Fixture requires explicit desktop handoff; no UI was created.' }
$clock = [Diagnostics.Stopwatch]::StartNew()
$fixtureDirectory = [IO.Path]::GetFullPath($Directory)
if (-not [IO.Directory]::Exists($fixtureDirectory)) { throw 'Runner must create private case directory.' }
$window = $null; $viewer = $null; $timer = $null; $bitmap = $null
$priorDpi = [IntPtr]::Zero
$sequence = 0; $ready = $false; $maximumOffset = 0; $endSeen = $false; $lastCommand = 0
try {
    Add-Type -AssemblyName System.Windows.Forms,System.Drawing
    [Windows.Forms.Application]::SetUnhandledExceptionMode([Windows.Forms.UnhandledExceptionMode]::ThrowException)
    # Only geometry queries on this owned fixture. No pointer/keyboard injection.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FixtureListGeometry {
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct Scrollbar {
        public int Size; public Rect Bounds; public int LineButton,ThumbTop,ThumbBottom,Reserved;
        [MarshalAs(UnmanagedType.ByValArray,SizeConst=6)] public int[] State;
    }
    [DllImport("user32.dll",SetLastError=true)] public static extern bool GetScrollBarInfo(IntPtr window,int objectId,ref Scrollbar info);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
}
'@
    $priorDpi = [FixtureListGeometry]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
    if ($priorDpi -eq [IntPtr]::Zero) { throw 'Physical pixel context unavailable' }
    Write-Output 'FIXTURE_PHASE=assemblies-loaded'
    if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) { return }
    $ownedProcess = [Diagnostics.Process]::GetCurrentProcess()
    try { $startedTicks = $ownedProcess.StartTime.ToUniversalTime().Ticks }
    finally { $ownedProcess.Dispose() }
    $pixels = [IO.File]::ReadAllBytes((Join-Path $fixtureDirectory 'fixture-pixels.bgr32'))
    if ($pixels.Length -ne 384*1120*4) { throw 'Invalid synthetic bitmap length' }
    $bitmap = [Drawing.Bitmap]::new(384,1120,[Drawing.Imaging.PixelFormat]::Format32bppRgb)
    $locked = $bitmap.LockBits([Drawing.Rectangle]::new(0,0,384,1120),[Drawing.Imaging.ImageLockMode]::WriteOnly,[Drawing.Imaging.PixelFormat]::Format32bppRgb)
    try {
        if ($locked.Stride -ne 384*4) { throw 'Unsupported synthetic bitmap stride' }
        [Runtime.InteropServices.Marshal]::Copy($pixels,0,$locked.Scan0,$pixels.Length)
    } finally { $bitmap.UnlockBits($locked) }
    $window = [Windows.Forms.Form]::new()
    $window.Text = "VerdiClip synthetic scroll fixture $Nonce"
    $window.AutoScaleMode = [Windows.Forms.AutoScaleMode]::None
    $window.FormBorderStyle = [Windows.Forms.FormBorderStyle]::FixedDialog
    $window.MaximizeBox = $false; $window.MinimizeBox = $false
    $window.StartPosition = [Windows.Forms.FormStartPosition]::CenterScreen
    $window.ClientSize = [Drawing.Size]::new(480,340)
    $viewer = [Windows.Forms.ListBox]::new()
    $viewer.Name = 'VerdiClipQualificationScrollViewer'
    $viewer.AccessibleName = 'VerdiClipQualificationScrollViewer'
    $viewer.Location = [Drawing.Point]::new(24,24)
    $viewer.BorderStyle = [Windows.Forms.BorderStyle]::None
    $viewer.IntegralHeight = $false
    $viewer.HorizontalScrollbar = $false
    $viewer.ScrollAlwaysVisible = $true
    $viewer.DrawMode = [Windows.Forms.DrawMode]::OwnerDrawFixed
    $viewer.ItemHeight = 8
    $viewer.Size = [Drawing.Size]::new((384+[FixtureListGeometry]::GetSystemMetrics(2)),280)
    for ($index=0;$index -lt 140;$index++) { $null=$viewer.Items.Add($index) }
    $viewer.Add_DrawItem({
        param($sender,$event)
        if ($event.Index -lt 0 -or $event.Index -ge 140) { return }
        # No text, selection or focus adornment: row pixels are the independent oracle.
        $destination = [Drawing.Rectangle]::new($event.Bounds.X,$event.Bounds.Y,384,8)
        $event.Graphics.DrawImage($bitmap,$destination,0,($event.Index*8),384,8,[Drawing.GraphicsUnit]::Pixel)
    })
    $window.Controls.Add($viewer)
    function Write-FixtureState {
        if (-not $ready -or $window.IsDisposed -or -not $window.Visible) { return }
        # This fixture deliberately refuses scaling instead of equating DIP and pixels.
        $graphics = $viewer.CreateGraphics()
        try { if ($graphics.DpiX -ne 96 -or $graphics.DpiY -ne 96) { throw '100 percent DPI required' } }
        finally { $graphics.Dispose() }
        if ($viewer.ClientSize.Width -ne 384 -or $viewer.ClientSize.Height -ne 280 -or $viewer.ItemHeight -ne 8) { throw 'Unexpected native list viewport' }
        $scrollbar = [FixtureListGeometry+Scrollbar]::new()
        $scrollbar.Size = [Runtime.InteropServices.Marshal]::SizeOf($scrollbar)
        if (-not [FixtureListGeometry]::GetScrollBarInfo($viewer.Handle,-5,[ref]$scrollbar)) { throw 'Owned vertical scrollbar unavailable' }
        if ($scrollbar.State[0] -band (1 -bor 0x8000 -bor 0x10000)) { throw 'Owned scrollbar unavailable or hidden' }
        if ($scrollbar.Bounds.Right -le $scrollbar.Bounds.Left -or $scrollbar.Bounds.Bottom -le $scrollbar.Bounds.Top) { throw 'Invalid owned scrollbar bounds' }
        $point = $viewer.PointToScreen([Drawing.Point]::new(0,0))
        $offset = $viewer.TopIndex*8
        $script:maximumOffset = [Math]::Max($maximumOffset,$offset)
        if ($offset -ge 840) { $script:endSeen=$true }
        $script:sequence++
        if ($sequence -gt 10000) { throw 'Fixture snapshot budget exceeded' }
        $state = @{
            nonce=$Nonce;pid=$PID;hwnd=$window.Handle.ToInt64();started_ticks=$startedTicks
            sequence=$sequence;visible=[bool]$window.Visible;closed=$false;activation_complete=$ready
            offset=$offset;scrollable_height=840;maximum_offset=$maximumOffset;end_seen=$endSeen
            viewport_height=280;content_rows=1120
            scrollbar_x=[int][Math]::Floor(($scrollbar.Bounds.Left+$scrollbar.Bounds.Right)/2)
            scrollbar_y=[int][Math]::Floor(($scrollbar.Bounds.Top+$scrollbar.Bounds.Bottom)/2)
            physical_rectangle=@{x=$point.X;y=$point.Y;width=384;height=280}
        }
        $temporary=Join-Path $fixtureDirectory 'fixture-state.partial'
        [IO.File]::WriteAllText($temporary,($state|ConvertTo-Json -Depth 5))
        [IO.File]::Move($temporary,(Join-Path $fixtureDirectory ('fixture-state-{0:D6}.json' -f $sequence)))
    }
    $timer = [Windows.Forms.Timer]::new()
    $timer.Interval = 100
    $timer.Add_Tick({
        if ($clock.Elapsed.TotalSeconds -ge 90 -or [IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) {
            $timer.Stop();$window.Close();return
        }
        $commandPath=Join-Path $fixtureDirectory 'fixture-command.json'
        if ([IO.File]::Exists($commandPath)) {
            $command=[IO.File]::ReadAllText($commandPath)|ConvertFrom-Json
            if ($command.nonce -cne $Nonce -or $command.operation -cne 'scroll-percent' -or
                ($command.sequence -isnot [long] -and $command.sequence -isnot [int]) -or
                ($command.percent -isnot [int] -and $command.percent -isnot [long] -and $command.percent -isnot [double]) -or
                [double]::IsNaN($command.percent) -or [double]::IsInfinity($command.percent) -or
                $command.percent -lt 0 -or $command.percent -gt 100) { throw 'Invalid owned fixture command' }
            if ($command.sequence -gt $lastCommand) {
                $script:lastCommand=$command.sequence
                $viewer.TopIndex=[int][Math]::Round(105*$command.percent/100)
                $ack=@{nonce=$Nonce;sequence=$lastCommand;offset=($viewer.TopIndex*8)}
                $temporary=Join-Path $fixtureDirectory 'fixture-command-ack.partial'
                $path=Join-Path $fixtureDirectory 'fixture-command-ack.json'
                [IO.File]::WriteAllText($temporary,($ack|ConvertTo-Json))
                if ([IO.File]::Exists($path)) { [IO.File]::Replace($temporary,$path,[NullString]::Value) }
                else { [IO.File]::Move($temporary,$path) }
            }
        }
        Write-FixtureState
    })
    $window.Add_Shown({
        if ($script:ready) { return }
        [Console]::Error.WriteLine('FIXTURE_PHASE=initial-activation')
        $null=$window.Activate()
        $script:ready=$true
        Write-FixtureState
    })
    $timer.Start()
    if ([IO.File]::Exists((Join-Path $fixtureDirectory 'fixture-stop.json'))) { return }
    Write-Output 'FIXTURE_PHASE=before-show'
    $window.ShowDialog()|Out-Null
}
finally {
    $cleanupErrors=0
    if ($null -ne $timer) {
        try { $timer.Stop() } catch { $cleanupErrors++ }
        try { $timer.Dispose() } catch { $cleanupErrors++ }
    }
    if ($null -ne $window) { try { $window.Dispose() } catch { $cleanupErrors++ } }
    if ($null -ne $viewer) { try { $viewer.Dispose() } catch { $cleanupErrors++ } }
    if ($null -ne $bitmap) { try { $bitmap.Dispose() } catch { $cleanupErrors++ } }
    if ($null -ne $priorDpi -and $priorDpi -ne [IntPtr]::Zero) {
        try { if ([FixtureListGeometry]::SetThreadDpiAwarenessContext($priorDpi) -eq [IntPtr]::Zero) { $cleanupErrors++ } }
        catch { $cleanupErrors++ }
    }
    $retired=$cleanupErrors -eq 0
    [IO.File]::WriteAllText((Join-Path $fixtureDirectory 'fixture-retired.json'),(@{nonce=$Nonce;pid=$PID;closed=$retired}|ConvertTo-Json))
    if (-not $retired) { throw 'Owned WinForms fixture cleanup failed' }
}
