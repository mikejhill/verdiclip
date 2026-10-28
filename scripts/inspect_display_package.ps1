#Requires -Version 7.0
param([string]$StateFile = '')
# Downloaded files are data only. Never invoke the package's installer or tools.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip') {
    throw 'Inspection requires explicit standard hosted Windows x64 dispatch.'
}
if ($StateFile) {
    $stateTarget = [IO.Path]::GetFullPath($StateFile)
    $tempPrefix = [IO.Path]::GetFullPath($env:RUNNER_TEMP) + [IO.Path]::DirectorySeparatorChar
    if (-not $stateTarget.StartsWith($tempPrefix,[StringComparison]::OrdinalIgnoreCase) -or
        (Test-Path -LiteralPath $stateTarget)) { throw 'State output must be a new runner-temp file.' }
}
$uri = 'https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/VirtualDisplayDriver-x86.Driver.Only.zip'
$expected = 'e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a'
$root = Join-Path $env:RUNNER_TEMP ('display-inspect-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$archive = Join-Path $root 'package.zip'
Invoke-WebRequest -Uri $uri -OutFile $archive -TimeoutSec 30 -MaximumRedirection 5
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'Pinned archive SHA256 mismatch; no extraction performed.' }
$destination = Join-Path $root 'data'
[IO.Directory]::CreateDirectory($destination) | Out-Null
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    if ($zip.Entries.Count -gt 512) { throw 'Archive entry bound exceeded.' }
    $total = 0L
    foreach ($entry in $zip.Entries) {
        $total += $entry.Length
        if ($total -gt 134217728 -or $entry.Length -gt 67108864) { throw 'Archive size bound exceeded.' }
        $path = [IO.Path]::GetFullPath((Join-Path $destination $entry.FullName))
        if (-not $path.StartsWith($destination + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase) -or $entry.FullName.Contains(':')) {
            throw 'Archive path is not a confined relative data path.'
        }
        # Validate every path before extraction. No link or executable is invoked.
        if (($entry.ExternalAttributes -shr 16 -band 61440) -eq 40960) { throw 'Archive symlink rejected.' }
    }
} finally { $zip.Dispose() }
[IO.Compression.ZipFile]::ExtractToDirectory($archive, $destination)

$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
$tools = @(Get-ChildItem -LiteralPath $sdkRoot -Filter signtool.exe -Recurse -File |
    Where-Object { $_.Directory.Name -eq 'x64' } | Sort-Object FullName -Descending)
if ($tools.Count -eq 0) { throw 'Existing x64 Windows SDK SignTool unavailable; no tool installation attempted.' }
$tool = $tools[0]
$toolSignature = Get-AuthenticodeSignature -LiteralPath $tool.FullName
if ($toolSignature.Status -ne 'Valid' -or
    $toolSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw 'Existing SDK verification tool is not validly Microsoft-signed.'
}
$osVersion = [Environment]::OSVersion.Version
$policyOS = '2:' + $osVersion.Major + '.' + $osVersion.Minor + '.' + $osVersion.Build
$verifications = [Collections.Generic.List[object]]::new()
function Get-VerificationVerdict([int]$exitCode, [string]$output) {
    if ($output -match '(?i)SignTool Error:.*(?:option|command|usage)|Usage:') {
        return 'tool-error'
    }
    switch ($exitCode) { 0 {'verified'} 2 {'warnings'} default {'rejected'} }
}
function Verify-Data([string]$label, [string[]]$arguments) {
    $info = [Diagnostics.ProcessStartInfo]::new($tool.FullName)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($argument in $arguments) { $info.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Could not start existing Windows verification tool.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) {
            $process.Kill($true)
            if (-not $process.WaitForExit(5000)) { throw 'Verifier cleanup timed out.' }
            $verifications.Add([ordered]@{check=$label; verdict='timeout'; exit_code=$null})
            return
        }
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw 'Verifier output drain timed out.' }
        # These logs describe public package data; replace the ephemeral root.
        $output = ($stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()).Replace($root, '<inspection>')
        $verdict = Get-VerificationVerdict $process.ExitCode $output
        $verifications.Add([ordered]@{check=$label; verdict=$verdict; exit_code=$process.ExitCode; arguments=@($arguments | ForEach-Object { $_.Replace($root, '<inspection>') }); output=$output})
    } finally {
        if (-not $process.HasExited) { $process.Kill($true); $process.WaitForExit(5000) | Out-Null }
        $process.Dispose()
    }
}
$files = @(Get-ChildItem -LiteralPath $destination -File -Recurse)
$inventory = @($files | ForEach-Object {
    [ordered]@{path=[IO.Path]::GetRelativePath($destination,$_.FullName);bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
})
$binaries = @($files | Where-Object { $_.Extension -in @('.dll','.sys') } | ForEach-Object {
    $stream = [IO.File]::OpenRead($_.FullName)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        $machine = $null
        if ($stream.Length -ge 64 -and $reader.ReadUInt16() -eq 0x5a4d) {
            $stream.Position = 0x3c
            $offset = $reader.ReadUInt32()
            if ($offset -le $stream.Length - 6) {
                $stream.Position = $offset
                if ($reader.ReadUInt32() -eq 0x4550) { $machine = '{0:x4}' -f $reader.ReadUInt16() }
            }
        }
        [ordered]@{path=[IO.Path]::GetRelativePath($destination,$_.FullName);pe_machine=$machine;file_version=$_.VersionInfo.FileVersion}
    } finally { $reader.Dispose(); $stream.Dispose() }
})
$frameworks = @('System32/WUDFx02000.dll', 'System32/drivers/UMDF/WUDFx02000.dll') | ForEach-Object {
    $path = Join-Path $env:SystemRoot $_
    [ordered]@{os_path=$_;present=(Test-Path -LiteralPath $path -PathType Leaf);file_version=$(if(Test-Path -LiteralPath $path -PathType Leaf) { [Diagnostics.FileVersionInfo]::GetVersionInfo($path).FileVersion } else { $null })}
}
$infs = @($files | Where-Object Extension -eq '.inf')
$packages = @()
foreach ($inf in $infs) {
    $content = Get-Content -Raw -LiteralPath $inf.FullName
    if ($content -notmatch '(?i)NTamd64') { continue }
    $relative = [IO.Path]::GetRelativePath($destination,$inf.FullName)
    $catNames = @([regex]::Matches($content,'(?im)^\s*CatalogFile(?:\.\w+)?\s*=\s*([^;\r\n]+)') | ForEach-Object { $_.Groups[1].Value.Trim().Trim('"') } | Select-Object -Unique)
    $metadata = @($content -split '\r?\n' | Where-Object { $_ -match '(?i)^\s*(DriverVer|Class|ClassGuid|Provider|CatalogFile|(?:Umdf|Kmdf)\w*|ServiceBinary|Include|Needs|[^;=]*NTamd64)\b' })
    $packages += [ordered]@{inf=$relative;declarations=$metadata;runtime_load_verified=$false;os_framework_compatibility_verified=$false}
    foreach ($catName in $catNames) {
        $catalog = [IO.Path]::GetFullPath((Join-Path $inf.DirectoryName $catName))
        if (-not $catalog.StartsWith($destination + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $catalog -PathType Leaf)) { throw 'INF catalog is missing or outside extracted data.' }
        $catRelative = [IO.Path]::GetRelativePath($destination,$catalog)
        Verify-Data ($catRelative + ':authenticode') @('verify','/v','/pa',$catalog)
        Verify-Data ($catRelative + ':kernel-policy') @('verify','/v','/kp',$catalog)
        $payloads = @($inf) + @(Get-ChildItem -LiteralPath $inf.DirectoryName -Recurse -File | Where-Object { $_.Extension -in @('.dll','.sys') })
        foreach ($payload in $payloads) {
            $payloadRelative = [IO.Path]::GetRelativePath($destination,$payload.FullName)
            Verify-Data ($payloadRelative + ':catalog-membership-authenticode') @('verify','/v','/pa','/c',$catalog,$payload.FullName)
            Verify-Data ($payloadRelative + ':catalog-membership-kernel-policy') @('verify','/v','/kp','/c',$catalog,$payload.FullName)
        }
    }
}
if ($packages.Count -eq 0 -or $verifications.Count -eq 0) { throw 'No inspectable amd64 INF/catalog pair found.' }
$report = [ordered]@{
    schema=1; kind='display-package-signature-preflight';source_sha=$env:GITHUB_SHA
    runner_image=$env:ImageOS; image_version=$env:ImageVersion; os_version=$osVersion.ToString();policy_os=$policyOS
    archive_url=$uri; archive_sha256=$actual; pinned_hash_matched=$true
    sdk_tool_version=$tool.VersionInfo.FileVersion; sdk_tool_signature=$toolSignature.Status.ToString()
    sdk_tool_sha256=(Get-FileHash -LiteralPath $tool.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    inspection_completed=$true; verification_completed=(@($verifications | Where-Object { $_.verdict -in @('tool-error','timeout') }).Count -eq 0); explicit_os_policy_target=$false; package_approved=$false; installed=$false; downloaded_code_executed=$false
    certificates_imported=$false; catalog_database_modified=$false; display_configuration_changed=$false
    runtime_load_verified=$false; os_framework_compatibility_verified=$false; multi_monitor_profile_verified=$false
    inventory=$inventory; binary_metadata=$binaries; os_framework_inventory=@($frameworks)
    amd64_packages=$packages; verification_results=$verifications.ToArray()
}
$json = $report | ConvertTo-Json -Depth 12
if ($StateFile) {
    [ordered]@{data_root=$destination;report=$report} | ConvertTo-Json -Depth 14 |
        Set-Content -LiteralPath $stateTarget -Encoding utf8
}
Write-Output $json
@('### Display package inspection only', '', '```json', $json, '```',
    'Completed inspection does not approve installation, driver loading or display capability.') -join "`n" |
    Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8
