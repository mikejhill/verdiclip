# Pure request conversion; native work stays in the separately owned helper.
function Invoke-NativeScrollRequest {
    param($Request)
    if ($Request.action -notin @('step', 'restore')) { throw 'native_action_required' }
    $payload = $Request.payload
    $reference = $payload.reference
    if ($payload.confirmed -isnot [bool] -or -not $payload.confirmed) { throw 'scroll_unconfirmed' }
    $handle = [long]$payload.window_handle
    if ($handle -eq 0 -or [long]$reference.window_handle -ne $handle) { throw 'target_identity_changed' }
    $position = [double]$reference.vertical_percent
    $view = [double]$reference.view_size
    foreach ($value in @($position, $view)) {
        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) { throw 'invalid_metrics' }
    }
    if ($position -lt 0 -or $position -gt 100 -or $view -le 0 -or $view -ge 100) { throw 'invalid_metrics' }
    $bounds = @($reference.bounds)
    $area = $bounds
    if ($null -ne $payload.viewport) { $area = @($payload.viewport) }
    if ($bounds.Count -ne 4 -or $area.Count -ne 4) { throw 'invalid_geometry' }
    foreach ($coordinate in $area) {
        $number = [double]$coordinate
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number) -or $number -ne [Math]::Round($number) -or $number -lt [int]::MinValue -or $number -gt [int]::MaxValue) { throw 'invalid_viewport' }
    }
    $destination = if ($Request.action -eq 'restore') { [double]$payload.restore_percent } else { [Math]::Min([double]100, $position + (50 * $view / (100 - $view))) }
    if ([double]::IsNaN($destination) -or [double]::IsInfinity($destination) -or $destination -lt 0 -or $destination -gt 100) { throw 'invalid_destination' }
    $updated = [VerdiClip.Automation.NoFocusScroll]::Mutate(
        $handle, [int]$reference.process_id, [long]$reference.started_ticks,
        [string]$reference.runtime_id, [double[]]$bounds, $position, $view, $destination, [int[]]$area)
    return @{ reference=@{
        window_handle=$updated.Handle; process_id=$updated.ProcessId; started_ticks=$updated.StartedTicks
        runtime_id=$updated.Runtime; bounds=$updated.Bounds
        vertical_percent=$updated.Position; view_size=$updated.ViewSize
    }; at_end=($updated.Position -ge 100) }
}
