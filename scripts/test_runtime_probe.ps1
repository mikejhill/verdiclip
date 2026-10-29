#Requires -Version 7.0
# Managed fakes plus compile-only declarations. No native factory is instantiated.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=Join-Path $PSScriptRoot 'runtime_probe'
. (Join-Path $root 'source_identity.ps1')
# Synthetic SHA here tests byte checking only, not an approval for runtime execution.
Assert-RuntimeSourceIdentity $root (Join-Path $root 'source-manifest.json') ('a'*40) ('a'*40)
$refused=$false
try { Assert-RuntimeSourceIdentity $root (Join-Path $root 'source-manifest.json') ('a'*40) ('b'*40) }
catch {$refused=$true}
if(-not $refused){throw 'Mismatched reviewed SHA was accepted.'}
$scratch=Join-Path (Join-Path $PSScriptRoot '../.tmp') ('runtime-identity-'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($scratch) | Out-Null
foreach($name in @('SoftwareDeviceLease.cs','SoftwareDeviceLeaseTests.cs','RuntimeController.cs','RuntimeControllerTests.cs')) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $scratch $name)
}
$manifest=Join-Path $root 'source-manifest.json'
Assert-RuntimeSourceIdentity $scratch $manifest ('a'*40) ('a'*40)
Add-Content -LiteralPath (Join-Path $scratch 'RuntimeController.cs') -Value '// changed-source negative fixture'
$refused=$false
try { Assert-RuntimeSourceIdentity $scratch $manifest ('a'*40) ('a'*40) } catch {$refused=$true}
if(-not $refused){throw 'Changed controller bytes were accepted.'}
$files=@('SoftwareDeviceLease.cs','SoftwareDeviceLeaseTests.cs','RuntimeController.cs','RuntimeControllerTests.cs') |
    ForEach-Object { Join-Path $root $_ }
$tests=Get-Content -Raw (Join-Path $root 'RuntimeControllerTests.cs')
foreach($forbidden in @('new NativeSoftwareDeviceApi','Activator.CreateInstance','DllImport(',
    'LoadLibrary','X509Store','Process.Start','static void Main')) {
    if($tests.Contains($forbidden)){throw ('Unsafe test activation: '+$forbidden)}
}
Add-Type -Path $files
[SoftwareDeviceLeaseTests]::Run()
[RuntimeControllerTests]::Run()
Write-Output 'Only managed fakes executed. Native declarations compiled; no native runtime, trust, device, display, UI or input call.'
