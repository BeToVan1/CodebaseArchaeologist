# Offline regression check. The function below replaces Docker entirely.
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$originalLocation = (Get-Location).Path
$originalExitCode = $global:LASTEXITCODE
if (Get-Variable -Name DeepServiceStagingTest -Scope Global -ErrorAction SilentlyContinue) { throw 'Staging test already running.' }
$global:DeepServiceStagingTest = @{ Contexts = @(); RunCalls = 0; FailBuild = $false }

function docker {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'build') {
        $context = [string]$args[-1]
        $global:DeepServiceStagingTest.Contexts += $context
        if ($context -eq $projectPath -or $context -eq '.') { throw 'Repository used as Docker context.' }
        $inputs = @(Get-ChildItem -LiteralPath $context -File -Recurse -Force)
        if ($inputs.Count -ne 21) { throw "Unexpected input count: $($inputs.Count)" }
        foreach ($name in @('.pytest_cache', '.git', '.env', 'node_modules', 'public')) {
            if (Test-Path -LiteralPath (Join-Path $context $name)) { throw "Unexpected build input: $name" }
        }
        $dockerfileIndex = [Array]::IndexOf($args, '--file') + 1
        if ($args[$dockerfileIndex] -ne (Join-Path $context 'Dockerfile.deep-service')) { throw 'Wrong Dockerfile path.' }
        $sourceHash = (Get-FileHash -LiteralPath (Join-Path $projectPath 'deep_service.py')).Hash
        $stagedHash = (Get-FileHash -LiteralPath (Join-Path $context 'deep_service.py')).Hash
        if ($sourceHash -ne $stagedHash) { throw 'Staged source differs from checkout.' }
        if ($global:DeepServiceStagingTest.FailBuild) { $global:LASTEXITCODE = 1 }
    }
    elseif ($args[0] -eq 'run') { $global:DeepServiceStagingTest.RunCalls++ }
    elseif ($args[0] -eq 'ps') { return }
    elseif ($args[0] -eq 'image' -and $args[1] -eq 'rm') { return }
    else { throw "Unexpected mock Docker operation: $($args[0])" }
}

try {
    & (Join-Path $PSScriptRoot 'Test-DeepService.ps1')
    if ($global:DeepServiceStagingTest.RunCalls -ne 2) { throw 'Expected offline tests and live smoke commands.' }
    $global:DeepServiceStagingTest.FailBuild = $true
    $expectedFailure = $false
    try { & (Join-Path $PSScriptRoot 'Test-DeepService.ps1') }
    catch {
        if ($_.Exception.Message -notlike 'Docker build failed.*') { throw }
        $expectedFailure = $true
    }
    if (-not $expectedFailure) { throw 'Failed build did not stop validation.' }
    if ($global:DeepServiceStagingTest.RunCalls -ne 2) { throw 'Tests were attempted after failed build.' }
    if ($global:DeepServiceStagingTest.Contexts.Count -ne 2) { throw 'Expected two separate staging directories.' }
    foreach ($context in $global:DeepServiceStagingTest.Contexts) {
        if (Test-Path -LiteralPath $context) { throw "Staging cleanup failed: $context" }
    }
    if ((Get-Location).Path -ne $originalLocation) { throw 'Working directory was not restored.' }
    Write-Host 'PASS: 21-file staging, source integrity, failure handling and cleanup; no Docker engine used.'
}
finally {
    $global:LASTEXITCODE = $originalExitCode
    Remove-Variable -Name DeepServiceStagingTest -Scope Global
}
