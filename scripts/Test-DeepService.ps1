[CmdletBinding()]
param([switch]$SkipLiveGithub)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runSuffix = [guid]::NewGuid().ToString('N')
$imageName = "codebase-archaeologist-deep-test:$runSuffix"
$containerName = "archaeologist-validation-$runSuffix"
$imageBuilt = $false
Push-Location -LiteralPath $projectPath
try {
    docker build --file Dockerfile.deep-service --target validation --tag $imageName .
    if ($LASTEXITCODE -ne 0) { throw 'Docker build failed. Copy the error output back to the project task.' }
    $imageBuilt = $true

    # No host mounts, Docker socket, published ports, or host credentials.
    $limits = @('--rm', '--init', '--name', $containerName, '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--memory=1g', '--memory-swap=1g', '--cpus=1',
        '--pids-limit=64', '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777')
    docker run @limits --network=none $imageName
    if ($LASTEXITCODE -ne 0) { throw 'Offline regression tests failed. Do not deploy this service.' }

    if (-not $SkipLiveGithub) {
        Write-Host 'Running public GitHub smoke test; source stays inside the disposable container.'
        docker run @limits $imageName python scripts/container_smoke.py
        if ($LASTEXITCODE -ne 0) { throw 'Live GitHub smoke test failed. Do not deploy this service.' }
    }
    Write-Host 'PASS: requested container validation completed. The public website was not changed.'
}
finally {
    # Only this run's exact randomly named container/image can be removed.
    if ($imageBuilt) {
        $ownedContainer = docker ps -aq --filter "name=^/$containerName$"
        if ($ownedContainer) { docker rm -f $containerName | Out-Null }
        docker image rm $imageName | Out-Null
    }
    Pop-Location
}
