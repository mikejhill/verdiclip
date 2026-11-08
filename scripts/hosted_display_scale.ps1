# Test-only companion to hosted_display_scale.py. All UIA calls are isolated
# behind the Python parent's 60-second timeout. No registry or private APIs.
param([Parameter(Mandatory = $true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'Only a disposable GitHub-hosted runner may run this helper'
}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$clock = [Diagnostics.Stopwatch]::StartNew()
$walker = [Windows.Automation.TreeWalker]::ControlViewWalker
$root = $null
$process = $null
$started = $null
$outcome = @{ status = 'unsupported'; stage = 'preflight'; selection_attempted = $false }

function Assert-Budget {
    if ($clock.Elapsed.TotalSeconds -ge 45) { throw 'Settings discovery budget exhausted' }
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
    $outcome.stage = 'launch'
    Start-Process 'ms-settings:display'
    while ($null -eq $root) {
        Assert-Budget
        $found = @(Get-Process -Name SystemSettings -ErrorAction SilentlyContinue)
        if ($found.Count -gt 1) { throw 'Multiple Settings processes appeared' }
        if ($found.Count -eq 1) {
            $process = $found[0]
            if ($process.Path -ine (Join-Path $env:WINDIR 'ImmersiveControlPanel\SystemSettings.exe')) {
                throw 'Unexpected Settings executable'
            }
            $roots = @()
            $node = $walker.GetFirstChild([Windows.Automation.AutomationElement]::RootElement)
            $count = 0
            while ($null -ne $node) {
                Assert-Budget
                if (++$count -gt 64) { throw 'Too many top-level windows' }
                if ($node.Current.ProcessId -eq $process.Id) { $roots += $node }
                $node = $walker.GetNextSibling($node)
            }
            if ($roots.Count -gt 1) { throw 'Multiple Settings roots appeared' }
            if ($roots.Count -eq 1) { $root = $roots[0] }
        }
        if ($null -eq $root) { Start-Sleep -Milliseconds 100 }
    }
    $started = $process.StartTime
    $outcome.stage = 'find_scale'
    $pageDeadline = $clock.Elapsed.TotalSeconds + 5
    do {
        $nodes = @(Get-BoundedTree $root)
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
    $outcome.selection_attempted = $true
    $outcome | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutputDirectory 'settings-outcome.json')
    $pattern.Select()
    Start-Sleep -Milliseconds 500
    $nodes = @(Get-BoundedTree $root)
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
    if ($null -ne $root -and $null -ne $process -and $null -ne $started) {
        try {
            $current = Get-Process -Id $process.Id
            $pattern = $null
            if ($current.StartTime -eq $started -and $root.Current.ProcessId -eq $process.Id -and
                $root.TryGetCurrentPattern([Windows.Automation.WindowPattern]::Pattern, [ref]$pattern)) {
                $pattern.Close()
                $outcome.close_requested = $true
                $closeClock = [Diagnostics.Stopwatch]::StartNew()
                while ($closeClock.Elapsed.TotalSeconds -lt 2) {
                    try { $null = $root.Current.ProcessId }
                    catch {
                        if ($_.Exception -is [Windows.Automation.ElementNotAvailableException] -or
                            $_.Exception.InnerException -is [Windows.Automation.ElementNotAvailableException]) {
                            $outcome.closed_owned_window = $true
                        }
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
