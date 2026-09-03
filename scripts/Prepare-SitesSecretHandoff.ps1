# Run in the owner's regular Windows PowerShell, never on Oracle.
# No token is printed, added to Git, or sent anywhere except through verified SSH
# to this protected local handoff file. This script does not publish the website.
[CmdletBinding()]
param(
    [string]$KeyPath = 'C:\Users\bevan\Downloads\Resume and Transcript\ssh-key-2026-09-02.key'
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$artifactRoot = Join-Path $repositoryRoot 'artifacts'
$handoffDirectory = Join-Path $artifactRoot 'sites-secret-handoff'
$handoffFile = Join-Path $handoffDirectory '.env.sites-handoff'
$createdDirectory = $false
$createdFile = $false
$captured = $null
$token = $null
$stage = 'preflight'

try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this helper in your regular Windows PowerShell.' }
    $manifest = Get-Content -LiteralPath (Join-Path $repositoryRoot '.openai\hosting.json') -Raw | ConvertFrom-Json
    if ($manifest.project_id -cne 'appgprj_6a88e5fb129c8191bcb950c9f3711614') {
        throw 'This is not the expected Sites project.'
    }
    if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) { throw 'The SSH key file was not found. No key permissions were changed.' }
    # Protect against traversing junctions/symlinks to an unexpected location.
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
    if (Test-Path -LiteralPath $handoffDirectory) { throw 'A handoff directory already exists. Report this message; do not overwrite it.' }
    if (-not (Test-Path -LiteralPath $artifactRoot -PathType Container)) { throw 'The expected artifacts directory is missing.' }

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
    # Read plus Delete permits Codex to consume and remove the handoff, but not
    # modify its contents. No access is granted to the owner's SSH private key.
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($codexSid, 'ReadAndExecute, Delete', $inherit, $propagation, $allow)))
    $stage = 'directory permissions'
    $null = New-Item -ItemType Directory -Path $handoffDirectory
    $createdDirectory = $true
    Set-Acl -LiteralPath $handoffDirectory -AclObject $acl
    $checkedAcl = Get-Acl -LiteralPath $handoffDirectory
    if (-not $checkedAcl.AreAccessRulesProtected) { throw 'Could not protect the handoff directory.' }
    foreach ($rule in $checkedAcl.Access) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier])
        if ($sid.Value -notin @($ownerSid.Value, $systemSid.Value, $codexSid.Value)) { throw 'Unexpected access to the handoff directory.' }
    }

    # Use the owner's configured SSH key without opening it in PowerShell or
    # changing its ACL. Strict checking requires the already verified host key.
    $stage = 'verified SSH'
    $captured = @(& ssh.exe -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -i $KeyPath ubuntu@159.54.182.161 'sudo -n cat /etc/codebase-archaeologist/service.env')
    if ($LASTEXITCODE -ne 0) { throw 'SSH could not read the service token. No secret was saved. Keep host-key verification enabled.' }
    $stage = 'token format validation'
    $text = ($captured -join "`n").TrimEnd([char[]]"`r`n")
    $match = [regex]::Match($text, '\AARCHAEOLOGIST_SERVICE_TOKEN=([a-f0-9]{64})\z')
    if (-not $match.Success) { throw 'SSH did not return the expected service-token format. Output was not displayed or saved.' }
    $token = $match.Groups[1].Value
    $stage = 'protected file creation'
    $bytes = [Text.Encoding]::UTF8.GetBytes("ARCHAEOLOGIST_SERVICE_TOKEN=$token`n")
    $stream = [IO.File]::Open($handoffFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $createdFile = $true
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
    Write-Host 'PASS: protected Sites token handoff prepared. Token was not displayed.'
    Write-Host 'Only your account, Windows SYSTEM, and the Codex sandbox group can read this temporary file.'
    Write-Host 'Tell Codex the handoff is ready. Do not open or paste the file. Nothing was published.'
} catch {
    # Remove only files created by this invocation; never recurse or delete the
    # repository/artifacts root. An existing handoff is never touched.
    if ($createdFile -and (Test-Path -LiteralPath $handoffFile -PathType Leaf)) { Remove-Item -LiteralPath $handoffFile -Force }
    if ($createdDirectory -and (Test-Path -LiteralPath $handoffDirectory -PathType Container)) { Remove-Item -LiteralPath $handoffDirectory }
    # Do not display arbitrary exception objects or captured SSH stdout.
    Write-Host "STOP: handoff failed during $stage. No token was displayed. Report this message and any SSH error above."
    exit 1
} finally {
    $captured = $null
    $token = $null
    $text = $null
    $match = $null
}
