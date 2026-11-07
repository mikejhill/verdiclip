param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$paths = @(
  'C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe',
  'C:\Program Files (x86)\Windows Kits\8.1\Debuggers\x64\cdb.exe'
)
$result = @{ status='skipped'; reason='fixed SDK x64 CDB absent'; candidates=$paths }
try {
foreach ($path in $paths) {
  if (!(Test-Path -LiteralPath $path -PathType Leaf)) { continue }
  $signature = Get-AuthenticodeSignature -LiteralPath $path
  if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)CN=Microsoft Corporation(,|$)') {
    throw 'Present debugger does not have a valid Microsoft Corporation signature'
  }
  $bytes = [IO.File]::ReadAllBytes($path)
  if ($bytes.Length -lt 256 -or [BitConverter]::ToUInt16($bytes,0) -ne 0x5a4d) { throw 'Invalid CDB PE' }
  $offset = [BitConverter]::ToInt32($bytes,0x3c)
  if ($offset -lt 0 -or $offset -gt $bytes.Length-6 -or [BitConverter]::ToUInt32($bytes,$offset) -ne 0x4550 -or [BitConverter]::ToUInt16($bytes,$offset+4) -ne 0x8664) { throw 'CDB is not AMD64 PE' }
  $version = (Get-Item -LiteralPath $path).VersionInfo
  if ([string]::IsNullOrWhiteSpace($version.FileVersion) -or $version.OriginalFilename -notmatch '(?i)^cdb\.exe$') { throw 'CDB version identity mismatch' }
  $result = @{ status='available'; path=$path; sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower(); version=$version.FileVersion; original_filename=$version.OriginalFilename; signature_status=[string]$signature.Status; signer=$signature.SignerCertificate.Subject; signer_thumbprint=$signature.SignerCertificate.Thumbprint; machine='AMD64' }
  break
}
} catch {
  @{ status='failed'; reason=$_.Exception.Message } | ConvertTo-Json | Set-Content -LiteralPath $Output -Encoding utf8
  throw
}
$result.runner_image = @{ ImageOS=$env:ImageOS; ImageVersion=$env:ImageVersion }
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Output -Encoding utf8
