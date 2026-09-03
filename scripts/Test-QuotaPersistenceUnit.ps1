# Offline only: every Docker invocation is mocked, including volume creation.
$ErrorActionPreference = 'Stop'
$global:quotaMock = $null
function Assert-Quota($condition, $message) { if (-not $condition) { throw "FAIL: $message" } }
function docker {
    $pipe = @($input) -join "`n"
    $call = @($args)
    $global:quotaMock.Calls.Add($call)
    $global:LASTEXITCODE = 0
    if ($call[0] -eq 'image') {
        if ($global:quotaMock.Case -eq 'wrong-image') { 'linux/arm64 invalid root' }
        else { 'linux/amd64 sha256:' + ('b' * 64) + ' 10001:10001' }
    } elseif ($call[0] -eq 'volume' -and $call[1] -eq 'ls') {
        if ($global:quotaMock.Case -eq 'collision') { 'existing' }
    } elseif ($call[0] -eq 'volume' -and $call[1] -eq 'create') {
        $global:quotaMock.Label = $call[3].Split('=')[1]
        $global:quotaMock.Volume = $call[4]
    } elseif ($call[0] -eq 'run') {
        Assert-Quota ($call -contains '--network=none') 'network disabled'
        Assert-Quota ($call -contains '--read-only') 'read-only root'
        Assert-Quota ($call -contains '--memory=384m' -and $call -contains '--memory-swap=384m') 'memory capped'
        Assert-Quota ($call -contains '--pull=never') 'no image pull'
        Assert-Quota ($call -contains ('sha256:' + ('b' * 64))) 'immutable image used'
        Assert-Quota (($call -join ' ') -notmatch 'type=bind|--publish|--env-file|docker.sock') 'no host secrets, ports or sockets'
        $mounts = @($call | Where-Object { $_ -like '--mount=*' })
        if ($call[-1] -eq 'missing') { Assert-Quota ($mounts.Count -eq 0) 'missing case has no mount' }
        else { Assert-Quota ($mounts.Count -eq 1 -and $mounts[0] -eq "--mount=type=volume,src=$($global:quotaMock.Volume),dst=/quota,volume-nocopy") 'only owned test volume mounted' }
        if ($call -contains '--user=0:0') {
            Assert-Quota ($call -contains '--cap-add=CHOWN') 'root initialization limited to ownership change'
        } else {
            Assert-Quota ($call -contains '--user=10001:10001') 'probes non-root'
            Assert-Quota ($call -notcontains '--cap-add=CHOWN') 'no extra probe capabilities'
            Assert-Quota ($pipe -match 'Offline runtime-image probe') 'exact probe passed via stdin'
        }
        if ($call[-1] -eq $global:quotaMock.Case) { $global:LASTEXITCODE = 1 }
    } elseif ($call[0] -eq 'volume' -and $call[1] -eq 'inspect') {
        @{ 'archaeologist.quota-test' = $(if ($global:quotaMock.Case -eq 'wrong-owner') { 'wrong' } else { $global:quotaMock.Label }) } | ConvertTo-Json -Compress
    } elseif ($call[0] -eq 'volume' -and $call[1] -eq 'rm') {
        Assert-Quota ($call[2] -eq $global:quotaMock.Volume) 'cleanup targets owned volume only'
    }
}
foreach ($case in @('success', 'seed', 'persisted', 'missing', 'wrong-owner', 'collision', 'wrong-image')) {
    $global:quotaMock = [pscustomobject]@{ Case = $case; Calls = [Collections.Generic.List[object]]::new(); Label = ''; Volume = '' }
    $failed = $false
    try { & (Join-Path $PSScriptRoot 'Test-QuotaPersistence.ps1') -Image ('codebase-archaeologist-deep:oracle-' + ('a' * 32)) }
    catch { $failed = $true; if ($case -eq 'success') { throw } }
    Assert-Quota ($failed -eq ($case -ne 'success')) "$case expected result"
    $removed = @($global:quotaMock.Calls | Where-Object { $_[0] -eq 'volume' -and $_[1] -eq 'rm' })
    Assert-Quota ($removed.Count -eq $(if ($case -in @('wrong-owner', 'collision', 'wrong-image')) { 0 } else { 1 })) "$case safe cleanup"
    $runs = @($global:quotaMock.Calls | Where-Object { $_[0] -eq 'run' })
    if ($case -eq 'success') { Assert-Quota ($runs.Count -eq 4) 'four distinct containers' }
    Write-Host "PASS: mocked quota persistence $case"
}
Write-Host 'PASS: 7 offline quota-container orchestration scenarios. Real Docker checks remain pending.'
