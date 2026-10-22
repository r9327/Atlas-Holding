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

    [ValidateRange(1, 5)]
    [int] $Trials = 1,

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
$names = @('AtlasHoldingAgent.exe', 'AtlasHoldingAcceptance.exe',
           'Run-HoldingLiveAcceptance.ps1', 'README.md')
$actualNames = @($manifest.files.PSObject.Properties.Name)
$unexpected = @(Compare-Object -ReferenceObject $actualNames -DifferenceObject $names)
if ($unexpected.Count -ne 0) {
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
$session = Join-Path $parent ("session-" + $guid + ".json")

# Only loopback is contacted; explicitly disable HTTP proxy inheritance.
Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(5)
try {
    $response = $client.GetAsync(
        'http://127.0.0.1:11434/api/tags',
        [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
    ).GetAwaiter().GetResult()
    try {
        if (!$response.IsSuccessStatusCode) { throw 'Local Ollama inventory is unavailable' }
        $length = $response.Content.Headers.ContentLength
        if ($null -ne $length -and $length -gt 262144) {
            throw 'Local Ollama inventory exceeds 256 KiB'
        }
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $buffer = New-Object byte[] 4096
        $memory = [System.IO.MemoryStream]::new()
        $inventoryTimer = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            while ($true) {
                $left = 5000 - [int]$inventoryTimer.ElapsedMilliseconds
                if ($left -le 0) { throw 'Local Ollama inventory read timed out' }
                $readTask = $stream.ReadAsync($buffer, 0, $buffer.Length)
                if (!$readTask.Wait($left)) {
                    throw 'Local Ollama inventory read timed out'
                }
                $count = $readTask.GetAwaiter().GetResult()
                if ($count -eq 0) { break }
                if ($memory.Length + $count -gt 262144) {
                    throw 'Local Ollama inventory exceeds 256 KiB'
                }
                $memory.Write($buffer, 0, $count)
            }
            $json = [System.Text.Encoding]::UTF8.GetString($memory.ToArray())
        }
        finally {
            $memory.Dispose()
            $stream.Dispose()
        }
    }
    finally {
        $response.Dispose()
    }
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
    # Independent disposable repositories for each trial. Stop on any
    # error; a partial set of receipts is NOT a passed multi-trial session.
    $results = @()
    for ($trial = 1; $trial -le $Trials; $trial++) {
        $receipt = Join-Path $parent ("receipt-" + $guid + "-" + $trial + ".json")
        & $runner --agent $agent --model-json $modelFile --receipt-json $receipt
        if ($LASTEXITCODE -ne 0) {
            throw ("Local coding trial " + $trial + " failed; no session PASS")
        }
        if (!(Test-Path -LiteralPath $receipt -PathType Leaf)) {
            throw 'Local model trial did not create its evidence receipt'
        }
        $proof = Get-Content -Raw -LiteralPath $receipt -Encoding UTF8 | ConvertFrom-Json
        if ($proof.source -cne 'LIVE_PINNED_OLLAMA' -or
            $proof.model -cne $Model -or $proof.model_digest -cne $digest -or
            $proof.doctor_status -cne 'NOT_EXECUTED' -or
            $proof.mission_state -cne 'QA_PENDING_AWAITING_PRIVILEGED_DOCTOR' -or
            $proof.github_push_status -cne 'NOT_PERMITTED' -or
            $proof.static_ast_exact_match -ne $true -or
            $proof.release_ready -ne $false -or
            $proof.candidate_commit_sha -cnotmatch '^[0-9a-f]{40}$') {
            throw 'Receipt contradicts the selected model or claimed certification'
        }
        $receiptSha = (Get-FileHash -LiteralPath $receipt -Algorithm SHA256).Hash.ToLowerInvariant()
        $results += @{
            trial = $trial
            receipt_sha256 = $receiptSha
            duration_ms = [int64]$proof.duration_ms
            commit_sha = [string]$proof.candidate_commit_sha
        }
        Write-Output ("Local trial " + $trial + "/" + $Trials + ": static QA + local Git commit PASS")
    }
    # Session-level proof exists ONLY after all trials succeeded. It is
    # unsigned, nonrelease, and deliberately not a comparative quality score.
    $summary = @{
        schema = 1
        kind = 'NONRELEASE_REPEATED_LOCAL_ACCEPTANCE'
        source_sha = [string]$manifest.git_sha
        model = $Model
        model_digest = $digest
        trials_requested = $Trials
        trials_passed = $results.Count
        results = @($results)
        independent_doctor_qa = 'NOT_EXECUTED'
        model_quality_benchmark = 'NOT_CERTIFIED'
        release_ready = $false
    } | ConvertTo-Json -Depth 7 -Compress
    $stream = [System.IO.FileStream]::new(
        $session, [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $writer = [System.IO.StreamWriter]::new(
            $stream, [System.Text.UTF8Encoding]::new($false))
        try {
            $writer.WriteLine($summary)
            $writer.Flush()
            $stream.Flush($true)
        }
        finally {
            $writer.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
    Write-Output ("LIVE_MODEL_LOCAL_ACCEPTANCE_PASS " + $Trials + "/" + $Trials + " (nonrelease only)")
    Write-Output ("Candidate SHA: " + $manifest.git_sha)
    Write-Output ("Session evidence: " + $session)
}
finally {
    $client.Dispose()
    $handler.Dispose()
    if (Test-Path -LiteralPath $modelFile) {
        Remove-Item -Force -LiteralPath $modelFile
    }
}
