# Pure projections only; no service, registry, network or desktop calls.
Set-StrictMode -Version Latest
function Get-RdpBindingCategory([string]$Address) {
    $parsed=$null
    if(-not [Net.IPAddress]::TryParse($Address,[ref]$parsed)){return 'unknown'}
    if($parsed.Equals([Net.IPAddress]::Any) -or $parsed.Equals([Net.IPAddress]::IPv6Any)){return 'wildcard'}
    if([Net.IPAddress]::IsLoopback($parsed)){return 'loopback'}
    return 'nonloopback'
}
function Get-RdpInventoryVerdict($Observed) {
    $missing=[Collections.Generic.List[string]]::new()
    foreach($errorCode in @($Observed.query_errors)){ $missing.Add('query:'+ $errorCode) }
    if($Observed.service_status -cne 'Running'){$missing.Add('rds_service_not_observed_running')}
    if($Observed.effective_deny -isnot [int] -or $Observed.effective_deny -ne 0){$missing.Add('rdp_allow_setting_not_established')}
    if($Observed.listener_enabled -isnot [int] -or $Observed.listener_enabled -ne 1){$missing.Add('rdp_listener_not_observed_enabled')}
    if($Observed.port -isnot [int] -or $Observed.port -lt 1 -or $Observed.port -gt 65535){$missing.Add('valid_listener_port_not_established')}
    if($Observed.transport_files.mstsc -isnot [bool] -or -not $Observed.transport_files.mstsc -or
        $Observed.transport_files.mstscax -isnot [bool] -or -not $Observed.transport_files.mstscax){$missing.Add('inbox_rdp_client_not_observed')}
    if(@($Observed.tcp_binding_categories).Count -eq 0){$missing.Add('tcp_listener_not_observed')}
    $all=@($Observed.tcp_binding_categories)+@($Observed.udp_binding_categories)
    if(@($all | Where-Object {$_ -cne 'loopback'}).Count){$missing.Add('loopback_only_binding_not_established')}
    if($Observed.session_protocol -cne 'rdp'){$missing.Add('current_session_not_observed_rdp')}
    return [ordered]@{
        inventory_complete=(@($Observed.query_errors).Count -eq 0)
        initial_prerequisites_observed=($missing.Count -eq 0)
        initial_blockers=$missing.ToArray()
        feasibility='blocked'
        unresolved=@('permitted_authentication_not_tested','topology_generating_client_not_established',
            'same_session_qt_win32_capture_not_tested','negative_origin_and_mixed_dpi_not_demonstrated')
        connection_authorized=$false;full_monitor_profile_qualified=$false
    }
}
