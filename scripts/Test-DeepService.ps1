[CmdletBinding()]
param(
    [switch]$SkipLiveGithub,
    [ValidateSet(384, 1024)][int]$MemoryMiB = 1024,
    [switch]$ExportOracleBundle
)

$ErrorActionPreference = 'Stop'
if ($ExportOracleBundle -and ($SkipLiveGithub -or $MemoryMiB -ne 384)) {
    throw 'Oracle Micro export requires -MemoryMiB 384 and live GitHub validation (do not use -SkipLiveGithub).'
}
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runSuffix = [guid]::NewGuid().ToString('N')
$imageName = "codebase-archaeologist-deep-test:$runSuffix"
$containerName = "archaeologist-validation-$runSuffix"
$imageBuilt = $false
$runtimeImage = "codebase-archaeologist-deep:oracle-$runSuffix"
$runtimeBuilt = $false
$contextCreated = $false
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$contextName = "archaeologist-build-$runSuffix"
$contextPath = [IO.Path]::GetFullPath((Join-Path $tempRoot $contextName))
# Exact files only: do not enumerate the checkout, even through dockerignore.
$contextFiles = @(
    'Dockerfile.deep-service', 'Dockerfile.deep-service.dockerignore',
    'requirements-service.txt', 'requirements.txt', 'requirements-dev.txt',
    'analyzer.py', 'repository_loader.py', 'deep_analysis_worker.py', 'deep_service.py', 'deep_quota.py', 'test_deep_quota.py',
    'api.py', 'interpretation.py', 'test_analyzer.py', 'test_architecture_acceptance.py', 'test_repository_loader.py',
    'test_api.py', 'test_interpretation.py', 'test_deep_worker.py', 'test_deep_service.py',
    'tests/fixtures/portable-report/api.py', 'tests/fixtures/portable-report/models.py',
    'tests/fixtures/portable-report/repository.py', 'scripts/container_smoke.py',
    'scripts/Test-QuotaPersistence.ps1', 'scripts/container_quota_check.py', 'test_container_quota_probe.py'
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
    docker build --platform linux/amd64 --file (Join-Path $contextPath 'Dockerfile.deep-service') --target validation --tag $imageName $contextPath
    if ($LASTEXITCODE -ne 0) { throw 'Docker build failed. Copy the error output back to the project task.' }
    $imageBuilt = $true

    # No host mounts, Docker socket, published ports, or host credentials.
    $limits = @('--rm', '--init', '--name', $containerName, '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', "--memory=$($MemoryMiB)m", "--memory-swap=$($MemoryMiB)m", '--cpus=1',
        '--pids-limit=64', '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777')
    Write-Host "Testing linux/amd64 with $MemoryMiB MiB memory, no swap, and one CPU."
    docker run @limits --network=none $imageName
    if ($LASTEXITCODE -ne 0) { throw 'Offline regression tests failed. Do not deploy this service.' }

    if (-not $SkipLiveGithub) {
        Write-Host 'Running public GitHub smoke test; source stays inside the disposable container.'
        docker run @limits $imageName python scripts/container_smoke.py
        if ($LASTEXITCODE -ne 0) { throw 'Live GitHub smoke test failed. Do not deploy this service.' }
    }
    if ($ExportOracleBundle) {
        # Export the serving target, not the Dockerfile's default test target.
        docker build --platform linux/amd64 --file (Join-Path $contextPath 'Dockerfile.deep-service') --target runtime --tag $runtimeImage $contextPath
        if ($LASTEXITCODE -ne 0) { throw 'Runtime image build failed; no bundle exported.' }
        $runtimeBuilt = $true
        $runtimePlatform = docker image inspect --format '{{.Os}}/{{.Architecture}}' $runtimeImage
        if ($LASTEXITCODE -ne 0 -or "$runtimePlatform".Trim() -ne 'linux/amd64') {
            throw 'Runtime image is not linux/amd64; no bundle exported.'
        }
        Write-Host 'Testing the exact runtime image with a temporary in-container token and no published ports.'
        # Pass only the staged, stdlib-only smoke script through stdin, no host mount.
        Get-Content -LiteralPath (Join-Path $contextPath 'scripts/container_smoke.py') -Raw |
            docker run @limits -i $runtimeImage python -
        if ($LASTEXITCODE -ne 0) { throw 'Runtime smoke test failed; no bundle exported.' }

        Write-Host 'Checking quota persistence across isolated replacement containers (no network or analysis jobs).'
        & (Join-Path $contextPath 'scripts/Test-QuotaPersistence.ps1') -Image $runtimeImage

        $artifactRoot = Join-Path $projectPath 'artifacts'
        if (Test-Path -LiteralPath $artifactRoot) {
            $artifactItem = Get-Item -LiteralPath $artifactRoot
            if (-not $artifactItem.PSIsContainer -or ($artifactItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Refusing export: artifacts must be a regular directory.'
            }
        } else {
            New-Item -ItemType Directory -Path $artifactRoot | Out-Null
        }
        $bundlePath = Join-Path $artifactRoot "oracle-$runSuffix"
        New-Item -ItemType Directory -Path $bundlePath | Out-Null
        $archivePath = Join-Path $bundlePath 'deep-service.tar'
        # Native output avoids binary corruption through Windows PowerShell pipes.
        docker image save --output $archivePath $runtimeImage $imageName
        if ($LASTEXITCODE -ne 0) { throw "Image export failed. Incomplete bundle (do not upload): $bundlePath" }
        $sourceHashes = [ordered]@{}
        foreach ($relativePath in $contextFiles) {
            $sourceHashes[$relativePath] = (Get-FileHash -LiteralPath (Join-Path $contextPath $relativePath) -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        Copy-Item -LiteralPath (Join-Path $contextPath 'scripts/container_smoke.py') -Destination (Join-Path $bundlePath 'container_smoke.py')
        Copy-Item -LiteralPath (Join-Path $contextPath 'scripts/container_quota_check.py') -Destination (Join-Path $bundlePath 'container_quota_check.py')
        $manifest = [ordered]@{
            schemaVersion = 1
            platform = 'linux/amd64'
            memoryMiB = $MemoryMiB
            cpus = 1
            tmpfsMiB = 128
            pidsLimit = 64
            runtimeImage = $runtimeImage
            validationImage = $imageName
            archive = 'deep-service.tar'
            archiveSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
            sourceSha256 = $sourceHashes
            localValidation = 'offline-suite-and-live-validation-and-live-runtime-passed'
            quotaPersistence = 'isolated-container-replacement-and-missing-storage-passed'
            oracleValidation = 'pending'
        }
        # Written last: a failed export must never have a success manifest.
        [IO.File]::WriteAllText((Join-Path $bundlePath 'bundle.json'), ($manifest | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
        Write-Host "PASS: Oracle test bundle prepared at $bundlePath"
        Write-Host "Archive SHA256: $($manifest.archiveSha256)"
        Write-Host 'Oracle validation is still required; this is not a public deployment approval.'
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
        if ($runtimeBuilt) { docker image rm $runtimeImage | Out-Null }
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
