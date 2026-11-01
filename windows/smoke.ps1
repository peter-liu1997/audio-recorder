param(
    [Parameter(Mandatory = $true)][string]$Exe
)

$ErrorActionPreference = "Stop"
$priorState = $env:AUDIO_RECORDER_STATE_DIR
$state = Join-Path $env:TEMP ("audio-recorder-smoke-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $state | Out-Null
$env:AUDIO_RECORDER_STATE_DIR = $state

function Invoke-Recorder {
    param([int]$Expected, [string[]]$Arguments)
    $output = & $Exe @Arguments --json
    if ($LASTEXITCODE -ne $Expected) { throw "Unexpected exit code for $Arguments : $LASTEXITCODE" }
    if ($output.Count -ne 1) { throw "Expected exactly one JSON response line for $Arguments" }
    $response = $output | ConvertFrom-Json
    if ($response.schemaVersion -ne 1) { throw "Unexpected JSON schema" }
    return $response
}

try {
    $doctor = Invoke-Recorder -Expected 0 -Arguments @("doctor")
    if (-not $doctor.ok) { throw "doctor failed" }
    $audio = Invoke-Recorder -Expected 0 -Arguments @("__selftest")
    if ($audio.data.audioMix -ne "passed") { throw "Synthetic audio mixing failed" }
    $empty = Invoke-Recorder -Expected 0 -Arguments @("list")
    if ($empty.data.sessions.Count -ne 0) { throw "Expected empty isolated state directory" }
    $status = Invoke-Recorder -Expected 0 -Arguments @("status")
    if ($null -ne $status.data.session) { throw "Expected null session" }
    $usage = Invoke-Recorder -Expected 2 -Arguments @("record", "--source", "invalid")
    if ($usage.error.code -ne "INVALID_ARGUMENT") { throw "Unexpected argument error" }
    $missing = Invoke-Recorder -Expected 4 -Arguments @("stop")
    if ($missing.error.code -ne "NO_ACTIVE_SESSION") { throw "Unexpected session error" }
    $missingAudio = Invoke-Recorder -Expected 5 -Arguments @("transcribe", "--input", (Join-Path $state "missing.wav"))
    if ($missingAudio.error.code -ne "INPUT_NOT_FOUND") { throw "Unexpected transcription input error" }
    $liveUsage = Invoke-Recorder -Expected 2 -Arguments @("live", "--source", "invalid")
    if ($liveUsage.error.code -ne "INVALID_ARGUMENT") { throw "Unexpected live argument error" }
    Write-Host "Windows agent CLI smoke checks passed"
} finally {
    $env:AUDIO_RECORDER_STATE_DIR = $priorState
    Remove-Item -LiteralPath $state -Recurse -Force
}
