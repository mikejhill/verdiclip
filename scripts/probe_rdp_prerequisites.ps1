#Requires -Version 7.0
# Hosted-only inventory. Never connect, authenticate, configure or launch a client.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'rdp_inventory_policy.ps1')
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or $env:GITHUB_RUN_ATTEMPT -ne '1'){
    throw 'Explicit standard public hosted Windows x64 inventory required.'
}
$event=Get-Content -LiteralPath $env:GITHUB_EVENT_PATH -Raw|ConvertFrom-Json
if($event.repository.private -ne $false -or [string]$event.inputs.rdp_prerequisites_only -ne 'true' -or
    @('driver_store_staging','driver_signature_preflight','desktop_capability_only','owned_window_canary').Where({
        [string]$event.inputs.$_ -eq 'true'
    }).Count){throw 'Exclusive read-only RDP inventory dispatch required.'}
$clock=[Diagnostics.Stopwatch]::StartNew()
$errors=[Collections.Generic.List[string]]::new()
function Assert-RdpInventoryDeadline {if($clock.ElapsedMilliseconds -ge 45000){throw 'Read-only inventory deadline exceeded.'}}
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class RdpReadOnlySession {
    [DllImport("wtsapi32.dll",ExactSpelling=true,SetLastError=true)]
    static extern bool WTSQuerySessionInformationW(IntPtr server,uint session,int kind,out IntPtr buffer,out uint bytes);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
    public static object Read() {
        using(var process=Process.GetCurrentProcess()) {
            IntPtr buffer;uint bytes;
            if(!WTSQuerySessionInformationW(IntPtr.Zero,(uint)process.SessionId,16,out buffer,out bytes))throw new Win32Exception();
            try {
                if(bytes!=2)throw new Exception("Unexpected WTS protocol size");
                ushort protocol=unchecked((ushort)Marshal.ReadInt16(buffer));
                return new {session_id=process.SessionId,protocol=protocol};
            } finally {WTSFreeMemory(buffer);}
        }
    }
}
'@
$observed=[ordered]@{service_status='unknown';session_id=$null;session_protocol='unknown';
    deny_local=$null;deny_policy=$null;effective_deny=$null;listener_enabled=$null;port=$null;
    nla_required=$null;security_layer=$null;tcp_binding_categories=@();udp_binding_categories=@();
    transport_files=[ordered]@{};query_errors=@()}
try {
    Assert-RdpInventoryDeadline
    $session=[RdpReadOnlySession]::Read();$observed.session_id=$session.session_id
    $observed.session_protocol=switch($session.protocol){0{'console'} 1{'legacy'} 2{'rdp'} default{'unknown'}}
} catch {$errors.Add('current_session_protocol')}
try {
    Assert-RdpInventoryDeadline
    $service=Get-Service -Name TermService -ErrorAction Stop
    try {$observed.service_status=$service.Status.ToString()} finally {$service.Dispose()}
} catch {$errors.Add('rds_service_status')}
$reads=@(
    @{path='SYSTEM\CurrentControlSet\Control\Terminal Server';name='fDenyTSConnections';field='deny_local'},
    @{path='SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services';name='fDenyTSConnections';field='deny_policy'},
    @{path='SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp';name='fEnableWinStation';field='listener_enabled'},
    @{path='SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp';name='PortNumber';field='port'},
    @{path='SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp';name='UserAuthentication';field='nla_required'},
    @{path='SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp';name='SecurityLayer';field='security_layer'}
)
foreach($read in $reads){
    $key=$null
    try{
        Assert-RdpInventoryDeadline
        $key=[Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($read.path,$false)
        if($null -ne $key -and $read.name -in $key.GetValueNames()){
            if($key.GetValueKind($read.name) -ne [Microsoft.Win32.RegistryValueKind]::DWord){throw 'Unexpected setting type.'}
            $observed[$read.field]=[int]$key.GetValue($read.name)
        }
    }catch{$errors.Add('rdp_setting:'+ $read.field)}finally{if($null -ne $key){$key.Dispose()}}
}
# Conservative projection, not a claim about effective runtime/group-policy enforcement.
$observed.effective_deny=$(if($observed.deny_local -eq 0 -and
    ($null -eq $observed.deny_policy -or $observed.deny_policy -eq 0)){0}
    elseif($observed.deny_local -eq 1 -or $observed.deny_policy -eq 1){1}else{$null})
foreach($file in @(@{name='mstsc';path='System32/mstsc.exe'},@{name='mstscax';path='System32/mstscax.dll'},
    @{name='vmconnect';path='System32/vmconnect.exe'})){
    try{
        Assert-RdpInventoryDeadline
        $observed.transport_files[$file.name]=[IO.File]::Exists((Join-Path $env:SystemRoot $file.path))
    }catch{$errors.Add('transport_file:'+ $file.name);$observed.transport_files[$file.name]=$null}
}
if($observed.port -is [int] -and $observed.port -gt 0 -and $observed.port -le 65535){
    foreach($protocol in @('tcp','udp')){
        try{
            Assert-RdpInventoryDeadline
            $network=[Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
            # Inbox local table reads, not connection probes; no CIM/provider service activation.
            $rows=@(if($protocol -eq 'tcp'){
                $network.GetActiveTcpListeners()
            }else{$network.GetActiveUdpListeners()})
            Assert-RdpInventoryDeadline
            if($rows.Count -gt 4096){throw 'Network-table row bound exceeded.'}
            $observed[$protocol+'_binding_categories']=@($rows | Where-Object Port -eq $observed.port |
                ForEach-Object {Get-RdpBindingCategory $_.Address.ToString()}|Sort-Object -Unique)
        }catch{$errors.Add($protocol+'_listener_bindings')}
    }
}else{$errors.Add('listener_port_unavailable')}
if($clock.ElapsedMilliseconds -ge 45000){$errors.Add('inventory_deadline_exceeded')}
$observed.query_errors=$errors.ToArray()
$verdict=Get-RdpInventoryVerdict $observed
$report=[ordered]@{schema=1;kind='read-only-rdp-prerequisites';source_sha=$env:GITHUB_SHA;
    image=$env:ImageOS;image_version=$env:ImageVersion;os=[Environment]::OSVersion.Version.ToString();
    elapsed_ms=$clock.ElapsedMilliseconds;observed=$observed;verdict=$verdict;
    connection_attempted=$false;authentication_attempted=$false;credentials_accessed=$false;
    services_changed=$false;firewall_changed=$false;network_changed=$false;software_installed=$false;
    resource_redirection_requested=$false;windows_created=$false;input_generated=$false;artifacts_uploaded=$false}
$report.binding_owner_verified=$false # port match is not process/service ownership
$report.binding_scope='configured port only; no firewall/reachability measurement'
$json=$report|ConvertTo-Json -Depth 8
Write-Output $json
@('### Read-only RDP prerequisites','','```json',$json,'```',
    'Inventory only. No session connection, topology change or capture qualification.') -join "`n"|
    Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8
if(-not $verdict.initial_prerequisites_observed){exit 2}
