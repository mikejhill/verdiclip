# One bounded UI Automation operation in a short-lived MTA process.
# No clicks, InvokePattern, activation, policy changes, or input injection.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$requestId = ''
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $requestId = [string]$request.request_id
    if ($requestId -notmatch '^[a-f0-9]{32}$') { throw 'invalid_request' }
    if ($request.action -notin @('inspect', 'observe', 'step', 'restore')) {
        throw 'unsupported_action'
    }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName WindowsBase
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CaptureDesktop {
    [StructLayout(LayoutKind.Sequential)] struct Rectangle { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetTopWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out Rectangle rectangle);
    [DllImport("user32.dll", SetLastError=true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder value, uint size, out uint needed);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr handle);
    public static bool Interactive() {
        var handle = OpenInputDesktop(0, false, 1);
        if (handle == IntPtr.Zero) return false;
        try {
            var name = new StringBuilder(256); uint needed;
            return GetUserObjectInformation(handle, 2, name, 512, out needed) && name.ToString() == "Default";
        } finally { CloseDesktop(handle); }
    }
    public static bool Unoccluded(IntPtr target, int x, int y, int width, int height) {
        // Refuse every overlapping visible top-level rectangle above target,
        // including transparent/tool/owned popup windows. Unknown is not clear.
        var window = GetTopWindow(IntPtr.Zero);
        for (int count = 0; count < 512 && window != IntPtr.Zero; count++) {
            if (window == target) return true;
            if (IsWindowVisible(window) && !IsIconic(window)) {
                Rectangle bounds;
                if (!GetWindowRect(window, out bounds)) return false;
                if ((long)bounds.Left < (long)x + width && bounds.Right > x &&
                    (long)bounds.Top < (long)y + height && bounds.Bottom > y) return false;
            }
            window = GetWindow(window, 2);
        }
        return false;
    }
}
'@
    if (-not [CaptureDesktop]::Interactive()) { throw 'interactive_desktop_unavailable' }
    $payload = $request.payload
    $handle = [IntPtr]([long]$payload.window_handle)
    if ($handle -eq [IntPtr]::Zero -or -not [CaptureDesktop]::IsWindowVisible($handle) -or [CaptureDesktop]::IsIconic($handle)) {
        throw 'target_unavailable'
    }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
    $process = Get-Process -Id $root.Current.ProcessId
    $started = $process.StartTime.ToUniversalTime().Ticks
    $windowId = '{0}:{1}:{2}' -f $handle.ToInt64(), $process.Id, $started
    $rootRuntime = ($root.GetRuntimeId() -join '.')
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker

    function Get-Bounds($element) {
        $bounds = $element.Current.BoundingRectangle
        if ($bounds.IsEmpty -or [double]::IsInfinity($bounds.Width) -or [double]::IsNaN($bounds.Width)) { throw 'invalid_geometry' }
        # Insets exclude fractional pixels outside a provider's physical bounds.
        $left = [Math]::Ceiling($bounds.Left)
        $top = [Math]::Ceiling($bounds.Top)
        return @($left, $top, [Math]::Floor($bounds.Right) - $left, [Math]::Floor($bounds.Bottom) - $top)
    }
    function Get-Scroll($element) {
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$pattern)) {
            if ($pattern.Current.VerticallyScrollable) { return $pattern }
        }
        return $null
    }
    function Get-Reference($element, $pattern) {
        $bounds = Get-Bounds $element
        return @{
            window_handle = $handle.ToInt64(); process_id = $process.Id
            started_ticks = $started; runtime_id = ($element.GetRuntimeId() -join '.')
            bounds = $bounds; vertical_percent = [double]$pattern.Current.VerticalScrollPercent
            view_size = [double]$pattern.Current.VerticalViewSize
        }
    }
    function Find-Ancestors($point) {
        $element = [System.Windows.Automation.AutomationElement]::FromPoint($point)
        $chain = [System.Collections.Generic.List[object]]::new()
        for ($depth = 0; $depth -lt 32 -and $null -ne $element; $depth++) {
            if ($element.Current.ProcessId -ne $process.Id) { throw 'cross_process_target' }
            $chain.Add($element)
            if (($element.GetRuntimeId() -join '.') -eq $rootRuntime) { return ,$chain }
            $element = $walker.GetParent($element)
        }
        throw 'ancestry_unverified'
    }
    if ($request.action -eq 'inspect') {
        $point = [System.Windows.Point]::new([double]$payload.x, [double]$payload.y)
        $chain = Find-Ancestors $point
        $nodes = @()
        for ($index = 0; $index -lt $chain.Count; $index++) {
            $element = $chain[$index]
            $runtime = ($element.GetRuntimeId() -join '.')
            $identity = $windowId + '/' + $runtime
            $parent = if ($index + 1 -lt $chain.Count) { $windowId + '/' + ($chain[$index + 1].GetRuntimeId() -join '.') } else { $null }
            $pattern = Get-Scroll $element
            $kind = 'other'
            if ($runtime -eq $rootRuntime) { $kind = 'window' }
            elseif ($null -ne $pattern) { $kind = 'content' }
            elseif ($element.Current.ControlType -eq [System.Windows.Automation.ControlType]::ScrollBar -and $element.Current.Orientation -eq [System.Windows.Automation.OrientationType]::Vertical) { $kind = 'vertical_scrollbar' }
            $reference = if ($null -ne $pattern) { Get-Reference $element $pattern } else { $null }
            $nodes += @{ identity=$identity; window_identity=$windowId; kind=$kind; bounds=(Get-Bounds $element); parent_identity=$parent; vertically_scrollable=($null -ne $pattern); reference=$reference }
        }
        $result = @{ nodes=$nodes; hit_identity=$nodes[0].identity; window_identity=$windowId }
    } else {
        $reference = $payload.reference
        if ([long]$reference.window_handle -ne $handle.ToInt64() -or [long]$reference.started_ticks -ne $started -or [int]$reference.process_id -ne $process.Id) { throw 'target_identity_changed' }
        $bounds = $reference.bounds
        $point = [System.Windows.Point]::new([double]$bounds[0] + [double]$bounds[2] / 2, [double]$bounds[1] + [double]$bounds[3] / 2)
        $chain = Find-Ancestors $point
        $element = $null
        foreach ($candidate in $chain) {
            if (($candidate.GetRuntimeId() -join '.') -eq $reference.runtime_id) { $element = $candidate; break }
        }
        if ($null -eq $element -or ((Get-Bounds $element) -join ',') -ne ($bounds -join ',')) { throw 'target_geometry_changed' }
        $pattern = Get-Scroll $element
        if ($null -eq $pattern -or $element.Current.IsOffscreen -or -not $element.Current.IsEnabled) { throw 'unsupported_scroll_area' }
        # Full-area top-level occlusion refusal, not sampled-point inference.
        # In-process/custom composited overlays still require native qualification;
        # this is not a claim of universal semantic visibility.
        if (-not [CaptureDesktop]::Unoccluded($handle, [int]$bounds[0], [int]$bounds[1], [int]$bounds[2], [int]$bounds[3])) { throw 'target_occluded_or_uncertain' }
        $position = [double]$pattern.Current.VerticalScrollPercent
        $view = [double]$pattern.Current.VerticalViewSize
        if ([double]::IsNaN($position) -or [double]::IsNaN($view) -or $position -lt 0 -or $position -gt 100 -or $view -le 0 -or $view -ge 100) { throw 'unsupported_scroll_metrics' }
        if ($request.action -in @('step', 'restore')) {
            if ($payload.confirmed -isnot [bool] -or -not $payload.confirmed -or [Math]::Abs($position - [double]$reference.vertical_percent) -gt 0.001) { throw 'scroll_interference_or_unconfirmed' }
            if ($request.action -eq 'restore') {
                $destination = [double]$payload.restore_percent
                if ($destination -lt 0 -or $destination -gt 100 -or [double]::IsNaN($destination)) { throw 'invalid_restore_position' }
            } else {
                # Half a reported viewport is a proposal, never measured displacement.
                $destination = [Math]::Min(100, $position + (50 * $view / (100 - $view)))
            }
            $pattern.SetScrollPercent([System.Windows.Automation.ScrollPattern]::NoScroll, $destination)
        }
        $updated = Get-Reference $element $pattern
        $result = @{ reference=$updated; at_end=($updated.vertical_percent -ge 100) }
    }
    @{ request_id=$requestId; ok=$true; result=$result } | ConvertTo-Json -Depth 12 -Compress
} catch {
    # Provider text may contain document content. Return only a bounded generic code.
    @{ request_id=$requestId; ok=$false; error='unsupported_or_changed' } | ConvertTo-Json -Compress
    exit 1
}
