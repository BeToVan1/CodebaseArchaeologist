[CmdletBinding()]
param([switch]$SkipLiveGithub)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runSuffix = [guid]::NewGuid().ToString('N')
$imageName = "codebase-archaeologist-deep-test:$runSuffix"
$containerName = "archaeologist-validation-$runSuffix"
$imageBuilt = $false
$contextCreated = $false
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$contextName = "archaeologist-build-$runSuffix"
$contextPath = [IO.Path]::GetFullPath((Join-Path $tempRoot $contextName))
# Exact files only: do not enumerate the checkout, even through dockerignore.
$contextFiles = @(
    'Dockerfile.deep-service', 'Dockerfile.deep-service.dockerignore',
    'requirements-service.txt', 'requirements.txt', 'requirements-dev.txt',
    'analyzer.py', 'repository_loader.py', 'deep_analysis_worker.py', 'deep_service.py',
    'api.py', 'interpretation.py', 'test_analyzer.py', 'test_repository_loader.py',
    'test_api.py', 'test_interpretation.py', 'test_deep_worker.py', 'test_deep_service.py',
    'tests/fixtures/portable-report/api.py', 'tests/fixtures/portable-report/models.py',
    'tests/fixtures/portable-report/repository.py', 'scripts/container_smoke.py'
)
Push-Location -LiteralPath $projectPath
try {
    New-Item -ItemType Directory -Path $contextPath | Out-Null
    $contextCreated = $true
    foreach ($relativePath in $contextFiles) {
        $source = Join-Path $projectPath $relativePath
        $sourceItem = Get-Item -LiteralPath $source
        if ($sourceItem.PSIsContainer -or ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Expected a regular build input: $relativePath"
        }
        $destination = Join-Path $contextPath $relativePath
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination
    }
    Write-Host "Prepared $($contextFiles.Count) explicit build inputs; repository caches are excluded."
    docker build --file (Join-Path $contextPath 'Dockerfile.deep-service') --target validation --tag $imageName $contextPath
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
    try {
        # Only this run's exact randomly named container/image can be removed.
        if ($imageBuilt) {
            $ownedContainer = docker ps -aq --filter "name=^/$containerName$"
            if ($ownedContainer) { docker rm -f $containerName | Out-Null }
            docker image rm $imageName | Out-Null
        }
    }
    finally {
        try {
            if ($contextCreated -and (Test-Path -LiteralPath $contextPath)) {
                $resolvedContext = (Resolve-Path -LiteralPath $contextPath).Path
                if ([IO.Path]::GetDirectoryName($resolvedContext) -ne $tempRoot -or
                    [IO.Path]::GetFileName($resolvedContext) -ne $contextName -or
                    ((Get-Item -LiteralPath $resolvedContext).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw 'Refusing cleanup: staging path no longer matches this run.'
                }
                Remove-Item -LiteralPath $resolvedContext -Recurse -Force
            }
        }
        finally { Pop-Location }
    }
}
