param([string]$Package = $PSScriptRoot, [string]$ReportUrl = "")
$ErrorActionPreference = "Stop"
$work = Join-Path $env:TEMP ("audio-recorder-functional-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
Copy-Item "$Package\*" $work -Recurse -Force
$exe = Join-Path $work "audio-recorder.exe"
$priorState = $env:AUDIO_RECORDER_STATE_DIR
$env:AUDIO_RECORDER_STATE_DIR = Join-Path $work "sessions"
$results = [System.Collections.Generic.List[object]]::new()
$player = $null
function Publish {
    $report = @{ os = [Environment]::OSVersion.VersionString; architecture = $env:PROCESSOR_ARCHITECTURE; work = $work; results = @($results.ToArray()) }
    $json = $report | ConvertTo-Json -Depth 20
    Set-Content -Path (Join-Path $work "report.json") -Value $json -Encoding UTF8
    if ($ReportUrl) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $ReportUrl -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType "application/json" | Out-Null }
        catch { Write-Warning $_ }
    }
}
function Check($Name, [scriptblock]$Test) {
    try { $detail = & $Test; $results.Add(@{name=$Name; status="passed"; detail=$detail}) }
    catch { $results.Add(@{name=$Name; status="failed"; error=$_.ToString()}) }
    Write-Host ($results[$results.Count-1] | ConvertTo-Json -Depth 10 -Compress)
    Publish
}
function Invoke-Recorder([string[]]$Arguments, [int]$Expected = 0) {
    $output = @(& $exe @Arguments --json)
    if ($LASTEXITCODE -ne $Expected) { throw "Exit $LASTEXITCODE, expected $Expected for $Arguments; output: $output" }
    if ($output.Count -ne 1) { throw "Expected one JSON line: $output" }
    $data = $output[0] | ConvertFrom-Json
    if ($data.schemaVersion -ne 1) { throw "Invalid schema: $output" }
    return $data
}
function Wav($Path, [bool]$Audible = $false) {
    $b = [IO.File]::ReadAllBytes($Path)
    if ($b.Length -le 44 -or [Text.Encoding]::ASCII.GetString($b,0,4) -ne "RIFF") { throw "Empty or invalid WAV" }
    $peak = 0
    for ($i=44; $i -lt $b.Length-1; $i+=2) { $v=[Math]::Abs([int][BitConverter]::ToInt16($b,$i)); if ($v -gt $peak) {$peak=$v} }
    $seconds = ($b.Length-44)/192000.0
    if ($seconds -lt 1) { throw "WAV too short: $seconds" }
    if ($Audible -and $peak -lt 100) { throw "Loopback WAV has no test tone: peak=$peak" }
    return @{ file=$Path; bytes=$b.Length; seconds=$seconds; peak=$peak; sampleRate=[BitConverter]::ToInt32($b,24); channels=[BitConverter]::ToInt16($b,22) }
}
try {
    Check "doctor" { Invoke-Recorder @("doctor") }
    Check "devices" { Invoke-Recorder @("devices") }
    Check "apps" { Invoke-Recorder @("apps") }
    Check "synthetic-mixing" { $r=Invoke-Recorder @("__selftest"); if ($r.data.audioMix -ne "passed") {throw "Mix failed"}; $r }
    Check "invalid-argument" { $r=Invoke-Recorder @("record","--source","invalid") 2; if ($r.error.code -ne "INVALID_ARGUMENT") {throw "Wrong code"}; $r }
    Check "empty-state" { $r=Invoke-Recorder @("list"); if ($r.data.sessions.Count -ne 0) {throw "State not isolated"}; Invoke-Recorder @("status") }
    Check "stop-without-session" { $r=Invoke-Recorder @("stop") 4; if ($r.error.code -ne "NO_ACTIVE_SESSION") {throw "Wrong code"}; $r }
    # A deterministic stereo PCM tone rendered in this PowerShell process.
    $tone = Join-Path $work "tone.wav"
    $writer = [IO.BinaryWriter]::new([IO.File]::Create($tone))
    $frames=48000; $bytes=$frames*4
    $writer.Write([Text.Encoding]::ASCII.GetBytes("RIFF")); $writer.Write([int](36+$bytes))
    $writer.Write([Text.Encoding]::ASCII.GetBytes("WAVEfmt ")); $writer.Write([int]16)
    $writer.Write([int16]1); $writer.Write([int16]2); $writer.Write([int]48000); $writer.Write([int]192000)
    $writer.Write([int16]4); $writer.Write([int16]16); $writer.Write([Text.Encoding]::ASCII.GetBytes("data")); $writer.Write([int]$bytes)
    for ($i=0;$i -lt $frames;$i++) { $v=[int16](10000*[Math]::Sin(2*[Math]::PI*440*$i/48000)); $writer.Write($v); $writer.Write($v) }
    $writer.Dispose()
    $player=[System.Media.SoundPlayer]::new($tone); $player.Load(); $player.PlayLooping()
    Start-Sleep -Milliseconds 700
    Check "system-loopback" { $p=Join-Path $work "system.wav"; $r=Invoke-Recorder @("record","--source","system","--duration","3","--output",$p); @{response=$r; wav=(Wav $p $true)} }
    Check "application-loopback" { $p=Join-Path $work "app.wav"; $r=Invoke-Recorder @("record","--source","system","--app","$PID","--duration","3","--output",$p); @{response=$r; wav=(Wav $p $true)} }
    Check "microphone-input" { $p=Join-Path $work "mic.wav"; $r=Invoke-Recorder @("record","--source","mic","--duration","2","--output",$p); @{response=$r; wav=(Wav $p)} }
    Check "mixed-separate-tracks" { $p=Join-Path $work "both.wav"; $r=Invoke-Recorder @("record","--source","both","--duration","2","--output",$p,"--keep-tracks"); @{response=$r; mixed=(Wav $p $true); mic=(Wav (Join-Path $work "both.microphone.wav")); system=(Wav (Join-Path $work "both.system.wav") $true)} }
    Check "background-start-status-stop" {
        $p=Join-Path $work "background.wav"
        $start=Invoke-Recorder @("start","--source","system","--output",$p)
        Start-Sleep -Seconds 2
        $status=Invoke-Recorder @("status")
        if ($status.data.session.state -ne "recording") {throw "Worker is not recording"}
        $stop=Invoke-Recorder @("stop")
        if ($stop.data.session.state -ne "done") {throw "Worker did not finish"}
        @{start=$start; status=$status; stop=$stop; wav=(Wav $p $true)}
    }
    Check "refuse-overwrite" { Invoke-Recorder @("record","--source","system","--duration","1","--output",(Join-Path $work "system.wav")) 4 }
    $player.Stop()
    Check "python-gui-dependency" {
        $python=Get-Command python -ErrorAction SilentlyContinue
        $py=Get-Command py -ErrorAction SilentlyContinue
        @{python="$($python.Source)"; launcher="$($py.Source)"; gui="Requires Python with Tkinter; UI acceptance remains separate"}
    }
} finally {
    if ($player) {$player.Stop()}
    $env:AUDIO_RECORDER_STATE_DIR=$priorState
    Publish
}
Write-Host "Results: $work\report.json"
