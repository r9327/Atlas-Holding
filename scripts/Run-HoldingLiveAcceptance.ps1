#requires -Version 5.1
<#
NONRELEASE developer acceptance on Windows, never install or update Atlas.
Only a named preinstalled local Ollama model; no model downloads.
Requires Git on PATH, but not Python. All code writes occur in a temp Git
fixture; the generated source is statically inspected, never executed.
Never run the candidate with a privileged account or production credentials.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)*$')]
    [string] $Model,

    [ValidateRange(1024, 131072)]
    [int] $NumCtx = 4096,

    [ValidateRange(2, 3600)]
    [int] $TimeoutSeconds = 600,

    [string] $EvidenceDirectory = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$kit = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$manifestPath = Join-Path $kit 'nonrelease-manifest.json'
if (!(Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw 'Manifest of exact tested binaries is missing'
}
$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
if ($manifest.schema -ne 1 -or $manifest.release_ready -ne $false -or
    $manifest.git_sha -cnotmatch '^[0-9a-f]{40}$') {
    throw 'Untrusted acceptance manifest: not a nonrelease exact-SHA build'
}
$names = @('AtlasHoldingAgent.exe', 'AtlasHoldingAcceptance.exe')
$actualNames = @($manifest.files.PSObject.Properties.Name)
if ((Compare-Object $actualNames $names).Count -ne 0) {
    throw 'Candidate manifest contains an unexpected binary list'
}
foreach ($name in $names) {
    $path = Join-Path $kit $name
    $item = Get-Item -LiteralPath $path -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band
            [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'Linked or missing candidate binary refused'
    }
    $expected = [string]$manifest.files.PSObject.Properties[$name].Value
    if ($expected -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Invalid expected binary pin'
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $expected) {
        throw 'Downloaded candidate does not match its published SHA256'
    }
}
if (!(Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw 'Git for Windows is required on PATH'
}
if ($EvidenceDirectory -eq '') {
    if (!$env:LOCALAPPDATA) { throw 'Cannot find private local evidence directory' }
    $EvidenceDirectory = Join-Path $env:LOCALAPPDATA 'AtlasHolding\AcceptanceEvidence'
}
$parent = [System.IO.Path]::GetFullPath($EvidenceDirectory)
[void](New-Item -ItemType Directory -Force -Path $parent)
$parent = (Resolve-Path -LiteralPath $parent).Path
$guid = [guid]::NewGuid().ToString('N')
$modelFile = Join-Path $parent ("model-" + $guid + ".json")
$receipt = Join-Path $parent ("receipt-" + $guid + ".json")

# Only loopback is contacted; explicitly disable HTTP proxy inheritance.
Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(5)
try {
    $json = $client.GetStringAsync('http://127.0.0.1:11434/api/tags').GetAwaiter().GetResult()
    if ($json.Length -gt 262144) { throw 'Ollama model inventory is too large' }
    $tags = $json | ConvertFrom-Json
    $matches = @($tags.models | Where-Object { $_.name -ceq $Model })
    if ($matches.Count -ne 1) { throw 'Select one exact installed Ollama model' }
    $digest = [string]$matches[0].digest
    if ($digest -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Ollama local model has no valid exact digest'
    }
    $config = @{
        schema = 1; model = $Model; digest = $digest
        num_ctx = $NumCtx; timeout = $TimeoutSeconds
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($modelFile, $config, [System.Text.UTF8Encoding]::new($false))
    $agent = Join-Path $kit 'AtlasHoldingAgent.exe'
    $runner = Join-Path $kit 'AtlasHoldingAcceptance.exe'
    & $runner --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Acceptance runtime self-test failed' }
    & $runner --agent $agent --model-json $modelFile --receipt-json $receipt
    if ($LASTEXITCODE -ne 0) { throw 'Real-model isolated coding acceptance failed' }
    if (!(Test-Path -LiteralPath $receipt -PathType Leaf)) {
        throw 'Real-model proof was not created'
    }
    $proof = Get-Content -Raw -LiteralPath $receipt -Encoding UTF8 | ConvertFrom-Json
    if ($proof.source -cne 'LIVE_PINNED_OLLAMA' -or
        $proof.model -cne $Model -or $proof.model_digest -cne $digest -or
        $proof.doctor_status -cne 'NOT_EXECUTED' -or
        $proof.release_ready -ne $false) {
        throw 'Receipt contradicts the selected model or claimed certification'
    }
    Write-Output 'LIVE_MODEL_LOCAL_ACCEPTANCE_PASS (nonrelease only)'
    Write-Output ("Candidate SHA: " + $manifest.git_sha)
    Write-Output ("Evidence: " + $receipt)
}
finally {
    $client.Dispose()
    $handler.Dispose()
    if (Test-Path -LiteralPath $modelFile) {
        Remove-Item -Force -LiteralPath $modelFile
    }
}
