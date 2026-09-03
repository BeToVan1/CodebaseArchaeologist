[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^codebase-archaeologist-deep:oracle-[a-f0-9]{32}$')][string]$Image)

$ErrorActionPreference = 'Stop'
$testId = [guid]::NewGuid().ToString('N')
$volumeName = "archaeologist-quota-test-$testId"
$testContainer = "archaeologist-quota-probe-$testId"
$volumeCreated = $false
$label = "archaeologist.quota-test=$testId"
$probePath = Join-Path $PSScriptRoot 'container_quota_check.py'
if (-not (Test-Path -LiteralPath $probePath -PathType Leaf)) { throw 'Missing offline quota probe.' }
$metadata = docker image inspect --format '{{.Os}}/{{.Architecture}} {{.Id}} {{.Config.User}}' $Image
if ($LASTEXITCODE -ne 0 -or "$metadata".Trim() -notmatch '^linux/amd64 (sha256:[a-f0-9]{64}) 10001:10001$') {
    throw 'Expected an existing Linux amd64 non-root runtime image. No images are pulled.'
}
$imageId = $Matches[1]
# Require a working daemon and no name collision before creating this test volume.
$existing = docker volume ls --filter "name=^$volumeName$" --format '{{.Name}}'
if ($LASTEXITCODE -ne 0 -or $existing) { throw 'Cannot safely create the isolated test volume.' }
try {
    docker volume create --label $label $volumeName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Test volume creation failed.' }
    $volumeCreated = $true
    $limits = @('--rm', '--pull=never', '--init', '--name', $testContainer, '--label', $label,
        '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--memory=384m', '--memory-swap=384m', '--cpus=1', '--pids-limit=64',
        '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777')
    $mount = "--mount=type=volume,src=$volumeName,dst=/quota,volume-nocopy"
    # Only this new volume's root permissions are set; no host bind mounts.
    docker run @limits --user=0:0 --cap-add=CHOWN $mount $imageId python -c "import os; os.chmod('/quota',0o700); os.chown('/quota',10001,10001)"
    if ($LASTEXITCODE -ne 0) { throw 'Test-volume permissions could not be initialized.' }
    foreach ($mode in @('seed', 'persisted', 'missing')) {
        $mountArgs = @()
        if ($mode -ne 'missing') { $mountArgs = @($mount) }
        Get-Content -LiteralPath $probePath -Raw | docker run @limits --user=10001:10001 @mountArgs -i $imageId python - $mode
        if ($LASTEXITCODE -ne 0) { throw "Quota persistence check failed: $mode. Do not deploy." }
    }
    Write-Host 'PASS: quota survives container replacement; missing storage rejects analysis. No public ports or repository jobs.'
} finally {
    # Never use broad prune commands or production container/volume names.
    if ($volumeCreated) {
        $owned = docker ps -aq --filter "name=^/$testContainer$" --filter "label=$label"
        if ($LASTEXITCODE -ne 0) { throw "Cleanup could not verify test container. Inspect $testContainer and $volumeName manually." }
        if ($owned) {
            docker rm -f $testContainer | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Temporary container cleanup failed: $testContainer" }
        }
        $ownerLabels = docker volume inspect --format '{{json .Labels}}' $volumeName
        if ($LASTEXITCODE -ne 0) { throw 'Test volume ownership could not be verified; not deleting it.' }
        $ownerLabel = ($ownerLabels | ConvertFrom-Json).'archaeologist.quota-test'
        if ($ownerLabel -ne $testId) { throw 'Test volume ownership could not be verified; not deleting it.' }
        docker volume rm $volumeName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Temporary volume cleanup failed: $volumeName" }
        Write-Host 'Removed only this run''s disposable quota-test volume; its synthetic admissions are not recoverable.'
    }
}
