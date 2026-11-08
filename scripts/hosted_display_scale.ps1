# Test-only companion to hosted_display_scale.py. All UIA calls are isolated
# behind the Python parent's 60-second timeout. No registry or private APIs.
param([Parameter(Mandatory = $true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Only a disposable GitHub-hosted runner may run this helper'
}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class HostedScaleWindows {
    public const int WindowLimit = 256;
    public static int ObservedCount;
    public static bool Overflow;
    public static IntPtr[] RetainedWindows = new IntPtr[0];
    private delegate bool Visit(IntPtr window, IntPtr data);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumWindows(Visit callback, IntPtr data);
    [DllImport("user32.dll")]
    private static extern bool EnumChildWindows(IntPtr parent, Visit callback, IntPtr data);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")]
    public static extern IntPtr GetAncestor(IntPtr window, uint flags);
    public static int Owner(IntPtr window) {
        uint process;
        if (GetWindowThreadProcessId(window, out process) == 0)
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return (int)process;
    }
    public static IntPtr[] Windows(IntPtr parent) {
        var windows = new List<IntPtr>();
        ObservedCount = 0;
        Overflow = false;
        RetainedWindows = new IntPtr[0];
        Visit callback = (window, data) => {
            ObservedCount++;
            if (windows.Count == WindowLimit) { Overflow = true; return false; }
            windows.Add(window); return true;
        };
        bool result = parent == IntPtr.Zero
            ? EnumWindows(callback, IntPtr.Zero)
            : EnumChildWindows(parent, callback, IntPtr.Zero);
        RetainedWindows = windows.ToArray();
        if (Overflow) throw new InvalidOperationException(
            "Native " + (parent == IntPtr.Zero ? "top-level" : "child") +
            " enumeration exceeded " + WindowLimit + "; parent=" + parent.ToInt64() +
            "; observed=" + ObservedCount);
        if (parent == IntPtr.Zero && !result)
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return RetainedWindows;
    }
}
'@
$clock = [Diagnostics.Stopwatch]::StartNew()
$walker = [Windows.Automation.TreeWalker]::ControlViewWalker
$root = $null
$settingsRoot = $null
$identity = $null
$process = $null
$started = $null
$outcome = @{ status = 'unsupported'; stage = 'preflight'; selection_attempted = $false }

function Assert-Budget {
    if ($clock.Elapsed.TotalSeconds -ge 45) { throw 'Settings discovery budget exhausted' }
}

function Get-NativeWindows([IntPtr]$parent, [string]$phase) {
    try { [HostedScaleWindows]::Windows($parent) }
    finally {
        # Retain each kind separately, even when native enumeration throws.
        $outcome[$phase + '_enumeration'] = @{
            parent_hwnd = $parent.ToInt64()
            limit = [HostedScaleWindows]::WindowLimit
            observed_count = [HostedScaleWindows]::ObservedCount
            retained_count = [HostedScaleWindows]::RetainedWindows.Length
            overflow = [HostedScaleWindows]::Overflow
            sample_hwnds = @([HostedScaleWindows]::RetainedWindows |
                Select-Object -First 16 | ForEach-Object { $_.ToInt64() })
        }
    }
}

function Assert-Owned($element) {
    $frame = [IntPtr]$identity.frame
    $content = [IntPtr]$identity.content
    if ((Get-Process -Id $identity.frame_pid).StartTime.Ticks -ne $identity.frame_start_ticks -or
        (Get-Process -Id $identity.settings_pid).StartTime.Ticks -ne $identity.settings_start_ticks -or
        -not [HostedScaleWindows]::IsWindow($frame) -or
        -not [HostedScaleWindows]::IsWindow($content) -or
        [HostedScaleWindows]::Owner($frame) -ne $identity.frame_pid -or
        [HostedScaleWindows]::Owner($content) -ne $identity.settings_pid -or
        [HostedScaleWindows]::GetAncestor($content, 2) -ne $frame -or
        ($root.GetRuntimeId() -join ',') -ne $identity.frame_runtime_id -or
        ($settingsRoot.GetRuntimeId() -join ',') -ne $identity.content_runtime_id -or
        $settingsRoot.Current.ProcessId -ne $identity.settings_pid) {
        throw 'Retained Settings ownership changed'
    }
    if ($null -ne $element) {
        if ($element.Current.ProcessId -ne $identity.settings_pid) {
            throw 'Mutation target is not owned by Settings'
        }
        $ancestor = $element
        for ($depth = 0; $depth -le 16 -and $null -ne $ancestor; $depth++) {
            Assert-Budget
            if ([Windows.Automation.Automation]::Compare($ancestor, $settingsRoot)) { return }
            $ancestor = $walker.GetParent($ancestor)
        }
        throw 'Mutation target left the retained Settings subtree'
    }
}

function Get-BoundedTree($start) {
    $queue = [Collections.Generic.Queue[object]]::new()
    $nodes = [Collections.Generic.List[object]]::new()
    $queue.Enqueue(@($start, 0))
    while ($queue.Count -gt 0) {
        Assert-Budget
        $entry = $queue.Dequeue()
        $nodes.Add($entry[0])
        if ($nodes.Count -gt 256) { throw 'Settings tree exceeded 256 nodes' }
        if ($entry[1] -ge 8) { continue }
        $child = $walker.GetFirstChild($entry[0])
        while ($null -ne $child) {
            Assert-Budget
            if ($nodes.Count + $queue.Count -ge 256) { throw 'Settings tree exceeded 256 nodes' }
            # Stay within the verified Settings process, including popup children.
            if ($child.Current.ProcessId -eq $process.Id) {
                $queue.Enqueue(@($child, ($entry[1] + 1)))
            }
            $child = $walker.GetNextSibling($child)
        }
    }
    return $nodes.ToArray()
}

function Save-FailureImage {
    if ($null -eq $root) { return }
    try {
        Add-Type -AssemblyName System.Drawing
        $box = $root.Current.BoundingRectangle
        if ($box.Width -lt 1 -or $box.Height -lt 1 -or $box.Width * $box.Height -gt 16777216) { return }
        $bitmap = [Drawing.Bitmap]::new([int]$box.Width, [int]$box.Height)
        try {
            $graphics = [Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.CopyFromScreen([int]$box.X, [int]$box.Y, 0, 0, $bitmap.Size)
                $bitmap.Save((Join-Path $OutputDirectory 'settings-failure.png'))
                $outcome.screenshot_saved = $true
            } finally { $graphics.Dispose() }
        } finally { $bitmap.Dispose() }
    } catch { $outcome.screenshot_saved = $false }
}

try {
    if (@(Get-Process -Name SystemSettings -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'Settings already exists; ownership is ambiguous'
    }
    $baseline = @(Get-NativeWindows ([IntPtr]::Zero) 'before_launch' | ForEach-Object { $_.ToInt64() })
    $launchedAfter = [DateTime]::Now
    $outcome.stage = 'launch'
    Start-Process 'ms-settings:display'
    while ($null -eq $root) {
        Assert-Budget
        $found = @(Get-Process -Name SystemSettings -ErrorAction SilentlyContinue)
        if ($found.Count -gt 1) { throw 'Multiple Settings processes appeared' }
        if ($found.Count -eq 1) {
            if ($null -eq $process) { $process = $found[0]; $started = $process.StartTime }
            if ($found[0].Id -ne $process.Id -or $found[0].StartTime -ne $started) {
                throw 'The newly launched Settings process was replaced'
            }
            if ($started -lt $launchedAfter -or
                $process.Path -ine (Join-Path $env:WINDIR 'ImmersiveControlPanel\SystemSettings.exe')) {
                throw 'Unexpected Settings executable'
            }
            $outcome.settings_pid = $process.Id
            $outcome.settings_start_ticks = $started.Ticks
            $roots = @()
            $observed = @()
            foreach ($window in (Get-NativeWindows ([IntPtr]::Zero) 'after_launch')) {
                Assert-Budget
                $owner = [HostedScaleWindows]::Owner($window)
                $new = $baseline -notcontains $window.ToInt64()
                if ($observed.Count -lt 16) {
                    $observed += @{ hwnd = $window.ToInt64(); pid = $owner; new_window = $new }
                }
                $outcome.observed_roots = $observed
                if (-not $new -or -not [HostedScaleWindows]::IsWindowVisible($window)) { continue }
                $frameProcess = Get-Process -Id $owner
                $content = $window
                if ($owner -ne $process.Id) {
                    if ($frameProcess.Path -ine (Join-Path $env:WINDIR 'System32\ApplicationFrameHost.exe')) { continue }
                    $children = @(Get-NativeWindows $window 'host_children' | ForEach-Object {
                        @{ hwnd = $_.ToInt64(); pid = [HostedScaleWindows]::Owner($_) }
                    })
                    $outcome.observed_host_frame = $window.ToInt64()
                    $outcome.observed_host_pid = $owner
                    $outcome.observed_host_children = @($children | Select-Object -First 16)
                    if (@($children | Where-Object { $_.pid -notin @($owner, $process.Id) }).Count -ne 0) {
                        throw 'New frame contains an unrelated process; ownership is ambiguous'
                    }
                    $settingsChildren = @($children | Where-Object {
                        $_.pid -eq $process.Id -and
                        [HostedScaleWindows]::Owner([HostedScaleWindows]::GetAncestor([IntPtr]$_.hwnd, 1)) -ne $process.Id
                    })
                    if ($settingsChildren.Count -gt 1) { throw 'New frame has ambiguous Settings children' }
                    if ($settingsChildren.Count -eq 0) { continue }
                    $content = [IntPtr]$settingsChildren[0].hwnd
                }
                if ([HostedScaleWindows]::GetAncestor($content, 2) -ne $window) {
                    throw 'Settings child no longer belongs to the new frame'
                }
                $roots += @{ frame = $window; content = $content; owner = $frameProcess }
            }
            $outcome.observed_roots = $observed
            $outcome.verified_root_candidates = $roots.Count
            if ($roots.Count -gt 1) { throw 'Multiple Settings roots appeared' }
            if ($roots.Count -eq 1) {
                $candidate = $roots[0]
                $root = [Windows.Automation.AutomationElement]::FromHandle($candidate.frame)
                $settingsRoot = [Windows.Automation.AutomationElement]::FromHandle($candidate.content)
                $identity = @{
                    frame = $candidate.frame.ToInt64(); content = $candidate.content.ToInt64()
                    frame_pid = $candidate.owner.Id; settings_pid = $process.Id
                    frame_start_ticks = $candidate.owner.StartTime.Ticks; settings_start_ticks = $started.Ticks
                    frame_runtime_id = ($root.GetRuntimeId() -join ',')
                    content_runtime_id = ($settingsRoot.GetRuntimeId() -join ',')
                }
                $outcome.ownership = $identity
                Assert-Owned $null
            }
        }
        if ($null -eq $root) { Start-Sleep -Milliseconds 100 }
    }
    $outcome.stage = 'find_scale'
    $pageDeadline = $clock.Elapsed.TotalSeconds + 5
    do {
        $nodes = @(Get-BoundedTree $settingsRoot)
        $combos = @($nodes | Where-Object {
            $_.Current.ControlType -eq [Windows.Automation.ControlType]::ComboBox -and
            $_.Current.Name -in @('Scale', 'Change the size of text, apps, and other items')
        })
        if ($combos.Count -eq 0) { Start-Sleep -Milliseconds 100 }
    } while ($combos.Count -eq 0 -and $clock.Elapsed.TotalSeconds -lt $pageDeadline)
    $outcome.scale_candidates = $combos.Count
    $outcome.observed_combo_boxes = @($nodes | Where-Object {
        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ComboBox
    } | Select-Object -First 16 | ForEach-Object {
        $name = $_.Current.Name; $id = $_.Current.AutomationId
        @{ name = $name.Substring(0, [Math]::Min(128, $name.Length))
           automation_id = $id.Substring(0, [Math]::Min(128, $id.Length))
           enabled = $_.Current.IsEnabled }
    })
    if ($combos.Count -ne 1) { throw 'Expected exactly one labeled Scale combo box' }
    $combo = $combos[0]
    if (-not $combo.Current.IsEnabled) { throw 'Scale control is disabled' }
    $pattern = $null
    if (-not $combo.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
        throw 'Scale control lacks ExpandCollapse support'
    }
    Assert-Owned $combo
    $pattern.Expand()
    Start-Sleep -Milliseconds 200
    $outcome.stage = 'find_200'
    $optionNodes = @(Get-BoundedTree $combo)
    $outcome.observed_list_items = @($optionNodes | Where-Object {
        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ListItem
    } | Select-Object -First 16 | ForEach-Object {
        $name = $_.Current.Name
        @{ name = $name.Substring(0, [Math]::Min(128, $name.Length))
           enabled = $_.Current.IsEnabled }
    })
    $options = @($optionNodes | Where-Object {
        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ListItem -and
        $_.Current.Name -match '^200%([ ]+\(Recommended\))?$'
    })
    $outcome.target_candidates = $options.Count
    if ($options.Count -ne 1 -or -not $options[0].Current.IsEnabled) {
        throw 'No unique enabled 200% scale option'
    }
    if (-not $options[0].TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
        throw '200% option lacks SelectionItem support'
    }
    $outcome.stage = 'select_200'
    Assert-Owned $options[0]
    $outcome.selection_attempted = $true
    $outcome | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'settings-outcome.json')
    $pattern.Select()
    Start-Sleep -Milliseconds 500
    $nodes = @(Get-BoundedTree $settingsRoot)
    if (@($nodes | Where-Object { $_.Current.Name -match '(?i)sign out|restart' }).Count -ne 0) {
        throw 'Settings requires a sign-out or restart; not permitted by this setup'
    }
    $outcome.status = 'selection_completed'
    $outcome.stage = 'native_readback_required'
} catch {
    $outcome.failure_type = $_.Exception.GetType().Name
    $outcome.reason = $_.Exception.Message.Substring(0, [Math]::Min(512, $_.Exception.Message.Length))
    Save-FailureImage
} finally {
    $outcome.close_requested = $false
    $outcome.closed_owned_window = $false
    if ($null -ne $root -and $null -ne $identity) {
        try {
            Assert-Owned $null
            $pattern = $null
            if ($root.TryGetCurrentPattern([Windows.Automation.WindowPattern]::Pattern, [ref]$pattern)) {
                $pattern.Close()
                $outcome.close_requested = $true
                $closeClock = [Diagnostics.Stopwatch]::StartNew()
                while ($closeClock.Elapsed.TotalSeconds -lt 2) {
                    if (-not [HostedScaleWindows]::IsWindow([IntPtr]$identity.frame)) {
                        $outcome.closed_owned_window = $true
                        break
                    }
                    Start-Sleep -Milliseconds 100
                }
            }
        } catch { $outcome.closed_owned_window = $false }
    }
    if ($outcome.status -eq 'selection_completed' -and -not $outcome.closed_owned_window) {
        $outcome.status = 'cleanup_unverified'
        $outcome.stage = 'close_owned_window'
        $outcome.reason = 'The owned Settings window did not confirm disappearance'
        Save-FailureImage
    }
    $outcome | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'settings-outcome.json')
}
if ($outcome.status -ne 'selection_completed') { exit 1 }
