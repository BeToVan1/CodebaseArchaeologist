# Offline orchestration tests. Docker is mocked; no daemon/network/SSH is used.
$ErrorActionPreference = 'Stop'
$testRootName = 'archaeologist-packaging-test-' + [guid]::NewGuid().ToString('N')
$testTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
$testRoot = Join-Path $testTempRoot $testRootName
$subjectPath = Join-Path $PSScriptRoot 'Test-DeepService.ps1'
$global:archaeologistPackagingMock = [pscustomobject]@{
    Calls = [Collections.Generic.List[object]]::new(); Scenario = ''; RunCount = 0
}
function Assert-Check($Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
}
function docker {
    $pipedText = @($input) -join "`n"
    $call = @($args)
    $global:archaeologistPackagingMock.Calls.Add($call)
    $global:LASTEXITCODE = 0
    if ($call[0] -eq 'build') {
        $staged = $call[-1]
        Assert-Check (-not (Test-Path (Join-Path $staged '.env'))) 'secret excluded from build context'
        Assert-Check (($call -join ' ') -match '--platform linux/amd64') 'amd64 build required'
        if ($global:archaeologistPackagingMock.Scenario -eq 'build-failure') { $global:LASTEXITCODE = 1 }
    } elseif ($call[0] -eq 'run') {
        $global:archaeologistPackagingMock.RunCount++
        Assert-Check (($call -join ' ') -notmatch '(^| )(-p|-v|--mount|--env)( |$)') 'no published ports/mounts/host secrets'
        if ($global:archaeologistPackagingMock.Scenario -ne 'default') {
            Assert-Check ($call -contains '--memory=384m') '384 MiB memory cap'
            Assert-Check ($call -contains '--memory-swap=384m') 'no extra swap'
        } else {
            Assert-Check ($call -contains '--memory=1024m') 'default memory preserved'
        }
        Assert-Check ($call -contains '--read-only') 'read-only root filesystem'
        Assert-Check ($call -contains '--pids-limit=64') 'PID limit preserved'
        if ($global:archaeologistPackagingMock.RunCount -eq 1) {
            Assert-Check ($call -contains '--network=none') 'offline suite networking disabled'
        }
        if ($global:archaeologistPackagingMock.RunCount -eq 3) {
            Assert-Check ($call -contains '-i') 'runtime smoke uses stdin'
            Assert-Check ($pipedText -match 'smoke-test-fixture') 'staged script reaches stdin'
            Assert-Check ($call[-1] -eq '-') 'Python reads stdin'
        }
        if (($global:archaeologistPackagingMock.Scenario -eq 'offline-failure' -and $global:archaeologistPackagingMock.RunCount -eq 1) -or
            ($global:archaeologistPackagingMock.Scenario -eq 'live-failure' -and $global:archaeologistPackagingMock.RunCount -eq 2) -or
            ($global:archaeologistPackagingMock.Scenario -eq 'runtime-failure' -and $global:archaeologistPackagingMock.RunCount -eq 3)) {
            $global:LASTEXITCODE = 1
        }
    } elseif ($call[0] -eq 'image' -and $call[1] -eq 'inspect') {
        if ($global:archaeologistPackagingMock.Scenario -eq 'wrong-platform') { 'linux/arm64' } else { 'linux/amd64' }
    } elseif ($call[0] -eq 'image' -and $call[1] -eq 'save') {
        if ($global:archaeologistPackagingMock.Scenario -eq 'save-failure') { $global:LASTEXITCODE = 1; return }
        Assert-Check ($call.Count -eq 6) 'archive contains exactly runtime and validation image tags'
        [IO.File]::WriteAllText($call[3], 'fake archive, not a Docker image')
    }
}
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    foreach ($case in @('success', 'default', 'build-failure', 'offline-failure', 'live-failure', 'runtime-failure', 'quota-failure', 'wrong-platform', 'save-failure', 'skip-guard', 'memory-guard')) {
        $global:archaeologistPackagingMock.Scenario = $case
        $global:archaeologistPackagingMock.RunCount = 0
        $global:archaeologistPackagingMock.Calls.Clear()
        $caseRoot = Join-Path $testRoot $case
        New-Item -ItemType Directory -Path (Join-Path $caseRoot 'scripts') -Force | Out-Null
        Copy-Item -LiteralPath $subjectPath -Destination (Join-Path $caseRoot 'scripts/Test-DeepService.ps1')
        # Read the subject's explicit staging manifest without evaluating it.
        $tokens = $null; $parseErrors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile($subjectPath, [ref]$tokens, [ref]$parseErrors)
        Assert-Check ($parseErrors.Count -eq 0) 'subject parses'
        $assignment = $ast.Find({ param($node)
            $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$contextFiles'
        }, $true)
        $paths = $assignment.Right.FindAll({ param($node)
            $node -is [Management.Automation.Language.StringConstantExpressionAst]
        }, $true)
        foreach ($pathNode in $paths) {
            $targetPath = Join-Path $caseRoot $pathNode.Value
            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($targetPath)) -Force | Out-Null
            [IO.File]::WriteAllText($targetPath, '# smoke-test-fixture')
        }
        # This suite mocks the delegated persistence check; its Docker safety
        # and failure paths are exercised separately in Test-QuotaPersistenceUnit.
        $quotaStub = "param([string]`$Image)`nif (`$global:archaeologistPackagingMock.Scenario -eq 'quota-failure') { throw 'quota test failed' }"
        [IO.File]::WriteAllText((Join-Path $caseRoot 'scripts/Test-QuotaPersistence.ps1'), $quotaStub)
        [IO.File]::WriteAllText((Join-Path $caseRoot '.env'), 'must never be staged')
        $parameters = @{ MemoryMiB = 384; ExportOracleBundle = $true }
        if ($case -eq 'default') { $parameters = @{ SkipLiveGithub = $true } }
        if ($case -eq 'skip-guard') { $parameters.SkipLiveGithub = $true }
        if ($case -eq 'memory-guard') { $parameters.MemoryMiB = 1024 }
        $failure = $null
        try { & (Join-Path $caseRoot 'scripts/Test-DeepService.ps1') @parameters } catch { $failure = $_ }
        $shouldPass = $case -in @('success', 'default')
        if ($shouldPass -and $failure) { throw $failure }
        Assert-Check (($null -eq $failure) -eq $shouldPass) "$case expected outcome"
        $manifests = @(Get-ChildItem -LiteralPath $caseRoot -Filter bundle.json -Recurse)
        Assert-Check ($manifests.Count -eq [int]($case -eq 'success')) "$case export gate"
        if ($case -eq 'success') {
            $manifest = Get-Content -LiteralPath $manifests[0].FullName -Raw | ConvertFrom-Json
            Assert-Check ($manifest.memoryMiB -eq 384 -and $manifest.oracleValidation -eq 'pending') 'manifest accurately scoped'
            $tarPath = Join-Path $manifests[0].DirectoryName 'deep-service.tar'
            Assert-Check ($manifest.archiveSha256 -eq (Get-FileHash -LiteralPath $tarPath -Algorithm SHA256).Hash.ToLowerInvariant()) 'archive checksum'
            Assert-Check ($manifest.sourceSha256.PSObject.Properties.Name.Count -eq 26) 'all staged input hashes recorded'
            Assert-Check ($manifest.quotaPersistence -eq 'isolated-container-replacement-and-missing-storage-passed') 'persistence gate recorded'
        }
        if ($case -in @('skip-guard', 'memory-guard')) {
            Assert-Check ($global:archaeologistPackagingMock.Calls.Count -eq 0) 'invalid export rejected before Docker'
        }
        foreach ($call in $global:archaeologistPackagingMock.Calls) {
            if ($call[0] -eq 'build') { Assert-Check (-not (Test-Path -LiteralPath $call[-1])) 'staging context cleaned' }
        }
        Write-Host "PASS: packaging $case"
    }
    Write-Host 'PASS: 11 offline packaging scenarios; real Docker validation remains pending.'
} finally {
    if (Test-Path -LiteralPath $testRoot) {
        $resolvedTestRoot = (Resolve-Path -LiteralPath $testRoot).Path
        if ([IO.Path]::GetDirectoryName($resolvedTestRoot) -ne $testTempRoot -or
            [IO.Path]::GetFileName($resolvedTestRoot) -ne $testRootName -or
            ((Get-Item -LiteralPath $resolvedTestRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Refusing cleanup: test directory does not match this run.'
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
