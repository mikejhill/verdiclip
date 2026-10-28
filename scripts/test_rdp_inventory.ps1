# Pure fixtures, source inspection and C# compilation only. No native invocation.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'rdp_inventory_policy.ps1')
$checks=0
foreach($case in @(@('127.0.0.1','loopback'),@('127.0.0.2','loopback'),@('::1','loopback'),
    @('0.0.0.0','wildcard'),@('::','wildcard'),@('192.0.2.1','nonloopback'),@('bad','unknown'))){
    if((Get-RdpBindingCategory $case[0]) -cne $case[1]){throw 'Binding category mismatch.'};$checks++
}
$ready=@{query_errors=@();service_status='Running';effective_deny=0;listener_enabled=1;port=3389;
    transport_files=@{mstsc=$true;mstscax=$true};tcp_binding_categories=@('loopback');
    udp_binding_categories=@();session_protocol='rdp'}
$verdict=Get-RdpInventoryVerdict $ready
if(-not $verdict.initial_prerequisites_observed -or -not $verdict.inventory_complete -or
    $verdict.feasibility -cne 'blocked' -or $verdict.connection_authorized -or
    $verdict.full_monitor_profile_qualified -or $verdict.unresolved.Count -ne 4){throw 'Inventory became qualification or hides positive prerequisite control.'};$checks++
foreach($case in @(
    @{field='service_status';value='Stopped'},@{field='effective_deny';value=$null},
    @{field='effective_deny';value=1},@{field='effective_deny';value='0'},
    @{field='listener_enabled';value=$true},@{field='listener_enabled';value=0},
    @{field='port';value=0},@{field='port';value=65536},@{field='port';value='3389'},
    @{field='transport_files';value=@{mstsc=$false;mstscax=$true}},
    @{field='transport_files';value=@{mstsc=$true;mstscax='false'}},
    @{field='tcp_binding_categories';value=@()},
    @{field='tcp_binding_categories';value=@('loopback','wildcard')},
    @{field='udp_binding_categories';value=@('nonloopback')},
    @{field='udp_binding_categories';value=@('unknown')},
    @{field='session_protocol';value='console'},@{field='session_protocol';value='unknown'},
    @{field='query_errors';value=@('synthetic_query_failure')}
)){
    $fixture=$ready.Clone();$fixture[$case.field]=$case.value;$result=Get-RdpInventoryVerdict $fixture
    if($result.initial_prerequisites_observed -or $result.initial_blockers.Count -eq 0 -or
        $result.connection_authorized -or $result.feasibility -cne 'blocked'){throw 'Missing prerequisite passed.'};$checks++
}
$errorsFixture=$ready.Clone();$errorsFixture.query_errors=@('synthetic_failure')
if((Get-RdpInventoryVerdict $errorsFixture).inventory_complete){throw 'Failed query hidden.'};$checks++
$tokens=$null;$errors=$null
$path=Join-Path $PSScriptRoot 'probe_rdp_prerequisites.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Invalid probe syntax.'}
$text=$ast.Extent.Text
foreach($forbidden in @('Start-Service','Set-Service','Start-Process','ProcessStartInfo','Connect-',
    'Get-Credential','LogonUser','CreateProcessAsUser','Set-ItemProperty','SetValue(',
    'CreateSubKey','New-Net','Set-Net','TcpClient','UdpClient','SendInput','OpenInputDesktop',
    'GetActiveTcpConnections','Get-CimInstance')){
    if($text.Contains($forbidden)){throw ('Unexpected operation in read-only probe: '+$forbidden)}
}
if($text.IndexOf("`$env:GITHUB_ACTIONS -ne 'true'") -gt $text.IndexOf('Add-Type') -or
    -not $text.Contains('OpenSubKey($read.path,$false)') -or
    -not $text.Contains('$clock.ElapsedMilliseconds -ge 45000') -or
    -not $text.Contains('binding_owner_verified=$false')){throw 'Guard, read-only access, bound or evidence limit missing.'};$checks++
$definitions=@($ast.FindAll({param($node)
    $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Add-Type'
},$true))
if($definitions.Count -ne 1){throw 'Unexpected native definitions.'}
$definition=$definitions[0].CommandElements[-1]
if($definition -isnot [Management.Automation.Language.StringConstantExpressionAst]){throw 'Dynamic native source refused.'}
foreach($method in @('WTSQuerySessionInformationW','WTSFreeMemory')){
    if(-not $definition.Value.Contains($method)){throw 'Required read-only native source missing.'}
}
Add-Type -TypeDefinition $definition.Value # Compile only; NEVER invoke Read().
$checks++
$workflow=Get-Content -LiteralPath (Join-Path $PSScriptRoot '../.github/workflows/ci.yml') -Raw
$jobs=[regex]::Matches($workflow,'(?ms)^  ([a-z][a-z0-9-]+):\r?\n(.*?)(?=^  [a-z][a-z0-9-]+:\r?\n|\z)')
$count=0
foreach($job in $jobs){
    $body=$job.Groups[2].Value
    if($body -notmatch '(?m)^    if: (.+)$'){continue}
    $condition=$Matches[1]
    if($job.Groups[1].Value -eq 'rdp-prerequisites'){
        if(-not $condition.Contains('&& inputs.rdp_prerequisites_only &&') -or
            -not $body.Contains('timeout-minutes: 2') -or $body.Contains('upload-artifact') -or
            $body.Contains('cache') -or -not $body.Contains('contents: read')){throw 'Inventory job scope changed.'}
    }elseif($condition.Trim() -cne "github.event_name == 'pull_request'" -and
        -not $condition.Contains('!inputs.rdp_prerequisites_only')){throw 'Another job can run during inventory dispatch.'}
    $count++
}
if($count -ne 14){throw ('Unexpected workflow job guard count: '+$count)};$checks++
Write-Output ($checks.ToString()+' pure policy/static/compile-only RDP checks passed; no native, service, registry or network query invoked.')
