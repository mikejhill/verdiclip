# Pure source/diagnostic checks. Never invokes SignTool or downloaded files.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$path = Join-Path $PSScriptRoot 'inspect_display_package.ps1'
$ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Preflight syntax invalid.' }
$helper = $ast.Find({param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-VerificationVerdict'
}, $true)
if ($null -eq $helper) { throw 'Pure verdict helper missing.' }
# This helper is repository code containing no native/tool operations.
. ([scriptblock]::Create($helper.Extent.Text))
$cases = @(
    @(0, 'Successfully verified', 'verified'),
    @(2, 'Warning: no timestamp', 'warnings'),
    @(1, 'SignTool Error: A certificate chain could not be built', 'rejected'),
    @(1, 'SignTool Error: The /o option is incompatible with the /pa option.', 'tool-error'),
    @(1, 'SignTool Error: The /o option requires the use of one of the following', 'tool-error'),
    @(1, 'Usage: signtool verify', 'tool-error')
)
foreach ($case in $cases) {
    if ((Get-VerificationVerdict $case[0] $case[1]) -ne $case[2]) {
        throw 'Verification diagnostic classification mismatch.'
    }
}
$calls = @($ast.FindAll({param($node)
    $node -is [Management.Automation.Language.CommandAst] -and
    $node.GetCommandName() -eq 'Verify-Data'
}, $true))
if ($calls.Count -ne 4) { throw 'Expected catalog and membership checks for both policies.' }
foreach ($call in $calls) {
    $arguments = $call.CommandElements[-1].Extent.Text
    if ($arguments -match "'/o'" -or $arguments -notmatch "'verify','/v','/(?:pa|kp)'") {
        throw 'Unexpected verification argument vector.'
    }
}
Write-Output '6 diagnostic cases and 4 source argument vectors passed; no native calls.'
