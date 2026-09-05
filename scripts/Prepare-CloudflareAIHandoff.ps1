# Run in the owner's regular Windows PowerShell. Values are never printed,
# committed, or sent to Cloudflare by this helper. It does not publish the site.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$artifactRoot = Join-Path $repositoryRoot 'artifacts'
$handoffDirectory = Join-Path $artifactRoot 'cloudflare-ai-handoff'
$handoffFile = Join-Path $handoffDirectory '.env.cloudflare-ai-handoff'
$createdDirectory = $false
$createdFile = $false
$accountId = $null
$secureToken = $null
$token = $null
$tokenPointer = [IntPtr]::Zero
$stage = 'preflight'

try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper in your regular Windows PowerShell.' }
    $manifest = Get-Content -LiteralPath (Join-Path $repositoryRoot '.openai\hosting.json') -Raw | ConvertFrom-Json
    if ($manifest.project_id -cne 'appgprj_6a88e5fb129c8191bcb950c9f3711614') {
        throw 'This is not the expected Sites project.'
    }
    if (-not (Test-Path -LiteralPath $artifactRoot -PathType Container)) { throw 'The expected artifacts directory is missing.' }
    if (Test-Path -LiteralPath $handoffDirectory) { throw 'A Cloudflare handoff already exists. Report this message; do not overwrite it.' }

    $ancestor = $handoffDirectory
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'A handoff path component is a link. Stop and review it.' }
        }
        $parent = Split-Path -Parent $ancestor
        if ($parent -eq $ancestor) { break }
        $ancestor = $parent
    }

    $stage = 'credential input'
    $accountId = (Read-Host 'Cloudflare Account ID').Trim()
    if ($accountId -cnotmatch '^[a-f0-9]{32}$') { throw 'The Account ID must be 32 lowercase hexadecimal characters.' }
    $secureToken = Read-Host 'Cloudflare Workers AI API token' -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    if ($token -cnotmatch '^[\x21-\x7e]{32,256}$') { throw 'The API token format is invalid.' }

    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $codexAccount = New-Object Security.Principal.NTAccount($env:COMPUTERNAME, 'CodexSandboxUsers')
    $codexSid = $codexAccount.Translate([Security.Principal.SecurityIdentifier])
    if ([Security.Principal.WindowsIdentity]::GetCurrent().Name -notmatch '\\bevan$') { throw 'Run this as the owner account.' }
    $systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($ownerSid)
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($ownerSid, 'FullControl', $inherit, $propagation, $allow)))
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($systemSid, 'FullControl', $inherit, $propagation, $allow)))
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($codexSid, 'ReadAndExecute, Delete', $inherit, $propagation, $allow)))

    $stage = 'protected file creation'
    $null = New-Item -ItemType Directory -Path $handoffDirectory
    $createdDirectory = $true
    Set-Acl -LiteralPath $handoffDirectory -AclObject $acl
    $checkedAcl = Get-Acl -LiteralPath $handoffDirectory
    if (-not $checkedAcl.AreAccessRulesProtected) { throw 'Could not protect the handoff directory.' }
    foreach ($rule in $checkedAcl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier])
        if ($sid.Value -notin @($ownerSid.Value, $systemSid.Value, $codexSid.Value)) { throw 'Unexpected access to the handoff directory.' }
    }

    $contents = "ARCHAEOLOGIST_CF_ACCOUNT_ID=$accountId`nARCHAEOLOGIST_CF_AI_TOKEN=$token`n"
    $bytes = [Text.Encoding]::UTF8.GetBytes($contents)
    $stream = [IO.File]::Open($handoffFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $createdFile = $true
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
    Write-Host 'PASS: protected Cloudflare AI handoff prepared. Credentials were not displayed.'
    Write-Host 'Tell Codex the handoff is ready. Do not open or paste the file. Nothing was published or called.'
} catch {
    if ($createdFile -and (Test-Path -LiteralPath $handoffFile -PathType Leaf)) { Remove-Item -LiteralPath $handoffFile -Force }
    if ($createdDirectory -and (Test-Path -LiteralPath $handoffDirectory -PathType Container)) { Remove-Item -LiteralPath $handoffDirectory }
    Write-Host "STOP: handoff failed during $stage. No credentials were displayed."
    exit 1
} finally {
    if ($tokenPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer) }
    $accountId = $null
    $secureToken = $null
    $token = $null
    $contents = $null
    $checkedAcl = $null
}
