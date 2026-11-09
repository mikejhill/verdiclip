# Test-only companion to hosted_display_scale.py. All UIA calls are isolated
# behind the Python parent's 60-second timeout. No registry or private APIs.
param(
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [ValidateSet(125, 200)][int]$Scale = 200
)
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
    // Display-member layout of documented DEVMODEW, not its printer union.
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct DisplayMode {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
        public ushort SpecVersion, DriverVersion, Size, DriverExtra;
        public uint Fields;
        public int PositionX, PositionY;
        public uint Orientation, FixedOutput;
        public short Color, Duplex, YResolution, TTOption, Collate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string FormName;
        public ushort LogPixels;
        public uint BitsPerPel, PelsWidth, PelsHeight, DisplayFlags, DisplayFrequency;
        public uint ICMMethod, ICMIntent, MediaType, DitherType, Reserved1, Reserved2;
        public uint PanningWidth, PanningHeight;
    }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool EnumDisplaySettingsW(string device, int mode, ref DisplayMode data);
    public static int[] PhysicalResolution() {
        var mode = new DisplayMode();
        mode.Size = (ushort)Marshal.SizeOf(typeof(DisplayMode));
        // ENUM_CURRENT_SETTINGS returns physical pixels, never DPI-virtualized.
        if (!EnumDisplaySettingsW(null, -1, ref mode) ||
            (mode.Fields & 0x180000) != 0x180000 || mode.PelsWidth == 0 || mode.PelsHeight == 0)
            throw new InvalidOperationException("Current physical display mode unavailable");
        return new int[] { (int)mode.PelsWidth, (int)mode.PelsHeight };
    }
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
$outcome = @{
    scale_target = $Scale
    status = 'unsupported'; stage = 'preflight'; selection_attempted = $false
    resolution_selection_attempted = $false; keep_changes_attempted = $false
    keep_changes_confirmed = $false; resolution_verified = $false
}

function Assert-Budget {
    if ($clock.Elapsed.TotalSeconds -ge 45) { throw 'Settings discovery budget exhausted' }
}

function Get-Resolution {
    $size = [HostedScaleWindows]::PhysicalResolution()
    return @{ width = $size[0]; height = $size[1] }
}

function Assert-Resolution($size) {
    if ($size.width -ne $outcome.resolution_target.width -or $size.height -ne $outcome.resolution_target.height) {
        throw 'Physical display mode does not match the chosen allowed resolution'
    }
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

function Assert-Owned($element, $expected = $identity, $frameElement = $root, $contentElement = $settingsRoot) {
    $frame = [IntPtr]$expected.frame
    $content = [IntPtr]$expected.content
    $observed = @{}
    $outcome.last_ownership_observation = $observed
    # Keep partial numeric evidence if a provisional HWND disappears mid-read.
    $observed.frame_exists = [HostedScaleWindows]::IsWindow($frame)
    $observed.content_exists = [HostedScaleWindows]::IsWindow($content)
    $observed.frame_start_ticks = (Get-Process -Id $expected.frame_pid).StartTime.Ticks
    $observed.settings_start_ticks = (Get-Process -Id $expected.settings_pid).StartTime.Ticks
    $observed.frame_pid = [HostedScaleWindows]::Owner($frame)
    $observed.content_pid = [HostedScaleWindows]::Owner($content)
    $observed.content_native_root = [HostedScaleWindows]::GetAncestor($content, 2).ToInt64()
    $observed.frame_runtime_id = ($frameElement.GetRuntimeId() -join ',')
    $observed.content_runtime_id = ($contentElement.GetRuntimeId() -join ',')
    $observed.content_uia_pid = $contentElement.Current.ProcessId
    if (-not $observed.frame_exists -or -not $observed.content_exists -or
        $observed.frame_start_ticks -ne $expected.frame_start_ticks -or
        $observed.settings_start_ticks -ne $expected.settings_start_ticks -or
        $observed.frame_pid -ne $expected.frame_pid -or
        $observed.content_pid -ne $expected.settings_pid -or
        $observed.content_native_root -ne $expected.frame -or
        $observed.frame_runtime_id -ne $expected.frame_runtime_id -or
        $observed.content_runtime_id -ne $expected.content_runtime_id -or
        $observed.content_uia_pid -ne $expected.settings_pid) {
        throw 'Retained Settings ownership changed'
    }
    if ($null -ne $element) {
        if ($element.Current.ProcessId -ne $expected.settings_pid) {
            throw 'Mutation target is not owned by Settings'
        }
        $ancestor = $element
        for ($depth = 0; $depth -le 16 -and $null -ne $ancestor; $depth++) {
            Assert-Budget
            if ([Windows.Automation.Automation]::Compare($ancestor, $contentElement)) { return }
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
    $pendingKey = $null
    $outcome.provisional_rejections = 0
    $outcome.stable_observations = 0
    while ($null -eq $root) {
        Assert-Budget
        $observationKey = $null
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
            $discardRoots = $false
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
                    # Discard this entire provisional observation, not just one
                    # child. No root is adopted and no control has been invoked.
                    $discardRoots = $true
                    $outcome.provisional_rejections++
                    $outcome.last_provisional_reason = 'Settings child no longer belongs to the new frame'
                    break
                }
                $roots += @{ frame = $window; content = $content; owner = $frameProcess }
            }
            $outcome.observed_roots = $observed
            $outcome.verified_root_candidates = $roots.Count
            if ($roots.Count -gt 1) { throw 'Multiple Settings roots appeared' }
            if ($discardRoots) {
                $roots = @()
                $outcome.verified_root_candidates = 0
            }
            if ($roots.Count -eq 1) {
                $candidate = $roots[0]
                $outcome.last_ownership_observation = $null
                try {
                    $candidateRoot = [Windows.Automation.AutomationElement]::FromHandle($candidate.frame)
                    $candidateContent = [Windows.Automation.AutomationElement]::FromHandle($candidate.content)
                    $candidateIdentity = [ordered]@{
                        frame = $candidate.frame.ToInt64(); content = $candidate.content.ToInt64()
                        frame_pid = $candidate.owner.Id; settings_pid = $process.Id
                        frame_start_ticks = $candidate.owner.StartTime.Ticks; settings_start_ticks = $started.Ticks
                        frame_runtime_id = ($candidateRoot.GetRuntimeId() -join ',')
                        content_runtime_id = ($candidateContent.GetRuntimeId() -join ',')
                    }
                    $outcome.provisional_identity = $candidateIdentity
                    Assert-Owned $null $candidateIdentity $candidateRoot $candidateContent
                    # A stable splash is not ready: require the existing Display
                    # Scale control, read-only, before retaining any cleanup root.
                    $candidateNodes = @(Get-BoundedTree $candidateContent)
                    $candidateCombos = @($candidateNodes | Where-Object {
                        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ComboBox -and
                        $_.Current.Name -in @('Scale', 'Change the size of text, apps, and other items')
                    })
                    $outcome.scale_candidates = $candidateCombos.Count
                    $outcome.observed_combo_boxes = @($candidateNodes | Where-Object {
                        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ComboBox
                    } | Select-Object -First 16 | ForEach-Object {
                        $name = $_.Current.Name; $id = $_.Current.AutomationId
                        @{ name = $name.Substring(0, [Math]::Min(128, $name.Length))
                           automation_id = $id.Substring(0, [Math]::Min(128, $id.Length))
                           enabled = $_.Current.IsEnabled }
                    })
                    if ($candidateCombos.Count -ne 1 -or -not $candidateCombos[0].Current.IsEnabled) {
                        throw 'Provisional Display page lacks one enabled labeled Scale combo'
                    }
                    Assert-Owned $candidateCombos[0] $candidateIdentity $candidateRoot $candidateContent
                    $scaleRuntime = $candidateCombos[0].GetRuntimeId() -join ','
                    $outcome.provisional_scale_runtime_id = $scaleRuntime
                    $observationKey = ($candidateIdentity | ConvertTo-Json -Compress) + '|' + $scaleRuntime
                    if ($observationKey -eq $pendingKey) {
                        Assert-Budget
                        $root = $candidateRoot
                        $settingsRoot = $candidateContent
                        $identity = $candidateIdentity
                        $combo = $candidateCombos[0]
                        $outcome.ownership = $identity
                        $outcome.stable_observations = 2
                    } else { $outcome.stable_observations = 1 }
                } catch {
                    # Observation only: no root has been adopted or control invoked.
                    # The unchanged outer deadline and process checks still apply.
                    $observationKey = $null
                    $outcome.stable_observations = 0
                    $outcome.provisional_rejections++
                    $outcome.last_provisional_failure = $_.Exception.GetType().Name
                    $outcome.last_provisional_reason = $_.Exception.Message.Substring(0, [Math]::Min(512, $_.Exception.Message.Length))
                    $outcome.last_rejected_observation = $outcome.last_ownership_observation
                }
            }
        }
        if ($null -eq $root) {
            $pendingKey = $observationKey
            if ($null -eq $pendingKey) { $outcome.stable_observations = 0 }
            Start-Sleep -Milliseconds 100
        }
    }
    $outcome.stage = 'find_resolution'
    $outcome.resolution_before = Get-Resolution
    $resolutionNodes = @(Get-BoundedTree $settingsRoot)
    $resolutionCombos = @($resolutionNodes | Where-Object {
        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ComboBox -and
        $_.Current.Name -ceq 'Display resolution' -and
        $_.Current.AutomationId -ceq 'SystemSettings_Display_Resolution_ComboBox'
    })
    $outcome.resolution_candidates = $resolutionCombos.Count
    if ($resolutionCombos.Count -ne 1 -or -not $resolutionCombos[0].Current.IsEnabled) {
        throw 'No unique enabled Display resolution combo'
    }
    $resolutionCombo = $resolutionCombos[0]
    $resolutionPattern = $null
    if (-not $resolutionCombo.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$resolutionPattern)) {
        throw 'Resolution control lacks ExpandCollapse support'
    }
    Assert-Owned $resolutionCombo
    $resolutionPattern.Expand()
    Start-Sleep -Milliseconds 200
    $resolutionItems = @(Get-BoundedTree $resolutionCombo | Where-Object {
        $_.Current.ControlType -eq [Windows.Automation.ControlType]::ListItem
    })
    $outcome.observed_resolution_items = @($resolutionItems | Select-Object -First 16 | ForEach-Object {
        $name = $_.Current.Name
        @{ name = $name.Substring(0, [Math]::Min(128, $name.Length)); enabled = $_.Current.IsEnabled }
    })
    # Inspect the finite preference list before making ONE selection. Never try
    # a second mode after selection, confirmation, or scale discovery fails.
    $allowedResolutions = @{
        125 = @(
            @{ width = 2560; height = 1440 }
            @{ width = 1920; height = 1200 }
            @{ width = 1920; height = 1080 }
            @{ width = 1600; height = 1200 }
        )
        200 = @(
            @{ width = 2560; height = 1440 }
            @{ width = 1920; height = 1200 }
            @{ width = 1600; height = 1200 }
        )
    }[$Scale]
    $outcome.resolution_target = $null
    foreach ($mode in $allowedResolutions) {
        # ASCII x or U+00D7 multiplication sign, with the optional standard suffix.
        $label = '\A' + $mode.width + '[ ]+[x\u00d7][ ]+' + $mode.height + '([ ]+\(Recommended\))?\z'
        $resolutionOptions = @($resolutionItems | Where-Object {
            $_.Current.IsEnabled -and $_.Current.Name -cmatch $label
        })
        $outcome.resolution_target_candidates = $resolutionOptions.Count
        if ($resolutionOptions.Count -gt 1) { throw 'Ambiguous allowed resolution option' }
        if ($resolutionOptions.Count -eq 1) { $outcome.resolution_target = $mode; break }
    }
    if ($null -eq $outcome.resolution_target) { throw 'No enabled allowed resolution option' }
    if ($outcome.resolution_before.width -ne $outcome.resolution_target.width -or
        $outcome.resolution_before.height -ne $outcome.resolution_target.height) {
        $resolutionSelect = $null
        if (-not $resolutionOptions[0].TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$resolutionSelect)) {
            throw 'Chosen resolution option lacks SelectionItem support'
        }
        $outcome.stage = 'select_resolution'
        Assert-Owned $resolutionOptions[0]
        $outcome.resolution_selection_attempted = $true
        $outcome | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'settings-outcome.json')
        $resolutionSelect.Select()
        $outcome.stage = 'confirm_resolution'
        $confirmationClock = [Diagnostics.Stopwatch]::StartNew()
        do {
            Assert-Budget
            Assert-Owned $null
            $confirmationNodes = @(Get-BoundedTree $settingsRoot)
            $outcome.observed_confirmation_controls = @($confirmationNodes | Where-Object {
                $_.Current.ControlType -in @([Windows.Automation.ControlType]::Button, [Windows.Automation.ControlType]::Text)
            } | Sort-Object { $_.Current.Name -notmatch '(?i)display|keep|revert' } |
                Select-Object -First 16 | ForEach-Object {
                $name = $_.Current.Name; $id = $_.Current.AutomationId
                @{ name = $name.Substring(0, [Math]::Min(128, $name.Length))
                   automation_id = $id.Substring(0, [Math]::Min(128, $id.Length))
                   role = $_.Current.ControlType.Id; enabled = $_.Current.IsEnabled }
            })
            $keepButtons = @($confirmationNodes | Where-Object {
                $_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and
                $_.Current.Name -ceq 'Keep changes'
            })
            $prompts = @($confirmationNodes | Where-Object {
                $_.Current.ControlType -eq [Windows.Automation.ControlType]::Text -and
                $_.Current.Name -ceq 'Keep these display settings?'
            })
            $outcome.keep_candidates = $keepButtons.Count
            $outcome.confirmation_prompt_candidates = $prompts.Count
            if ($keepButtons.Count -gt 1 -or $prompts.Count -gt 1) { throw 'Ambiguous resolution confirmation' }
            $outcome.resolution_selected = Get-Resolution
            if ($keepButtons.Count -eq 1 -and $prompts.Count -eq 1) { break }
            if ($confirmationClock.Elapsed.TotalSeconds -ge 5) { throw 'Owned resolution confirmation not observed' }
            Start-Sleep -Milliseconds 100
        } while ($true)
        $keep = $keepButtons[0]
        if (-not $keep.Current.IsEnabled) { throw 'Keep changes button is disabled' }
        $keepPattern = $null
        if (-not $keep.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$keepPattern)) {
            throw 'Keep changes button lacks Invoke support'
        }
        Assert-Owned $prompts[0]
        Assert-Owned $keep
        Assert-Resolution $outcome.resolution_selected
        $outcome.keep_runtime_id = $keep.GetRuntimeId() -join ','
        $outcome.keep_changes_attempted = $true
        $outcome | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'settings-outcome.json')
        $keepPattern.Invoke()
        $confirmationClock.Restart()
        do {
            Assert-Budget
            Assert-Owned $null
            $remaining = @(Get-BoundedTree $settingsRoot | Where-Object {
                ($_.GetRuntimeId() -join ',') -eq $outcome.keep_runtime_id -or
                ($_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and $_.Current.Name -ceq 'Keep changes') -or
                ($_.Current.ControlType -eq [Windows.Automation.ControlType]::Text -and $_.Current.Name -ceq 'Keep these display settings?')
            })
            if ($remaining.Count -eq 0) { $outcome.keep_changes_confirmed = $true; break }
            if ($confirmationClock.Elapsed.TotalSeconds -ge 2) { throw 'Resolution confirmation did not disappear' }
            Start-Sleep -Milliseconds 100
        } while ($true)
    } else {
        Assert-Owned $resolutionCombo
        $resolutionPattern.Collapse()
    }
    $outcome.resolution_after = Get-Resolution
    Assert-Resolution $outcome.resolution_after
    $outcome.resolution_verified = $true
    $outcome.stage = 'find_scale'
    # Reuse only the control validated in the adopted observation. No retargeting.
    if (-not $combo.Current.IsEnabled) { throw 'Scale control is disabled' }
    $pattern = $null
    if (-not $combo.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
        throw 'Scale control lacks ExpandCollapse support'
    }
    Assert-Owned $combo
    $pattern.Expand()
    Start-Sleep -Milliseconds 200
    $outcome.stage = 'find_scale_option'
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
        $_.Current.Name -match ('^' + $Scale + '%([ ]+\(Recommended\))?$')
    })
    $outcome.target_candidates = $options.Count
    if ($options.Count -ne 1 -or -not $options[0].Current.IsEnabled) {
        throw "No unique enabled $Scale% scale option"
    }
    if (-not $options[0].TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
        throw "$Scale% option lacks SelectionItem support"
    }
    $outcome.stage = 'select_scale_option'
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
    try { $outcome.resolution_failure_readback = Get-Resolution } catch { }
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
