param(
    [string]$Generator = "Visual Studio 17 2022"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $PSScriptRoot "build"

cmake -S $PSScriptRoot -B $build -G $Generator -A x64
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build $build --config Release --parallel
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exe = Join-Path $build "Release/audio-recorder.exe"
Copy-Item (Join-Path $root "scripts/transcribe.py") (Join-Path $build "Release/transcribe.py") -Force
Copy-Item (Join-Path $root "scripts/live.py") (Join-Path $build "Release/live.py") -Force
$gui = Join-Path $build "Release/gui"
New-Item -ItemType Directory -Force -Path $gui | Out-Null
foreach ($name in @("meeting_assistant.py", "meeting_service.py", "meeting_core.py")) {
    Copy-Item (Join-Path $root "gui/$name") (Join-Path $gui $name) -Force
}
Copy-Item (Join-Path $PSScriptRoot "meeting-assistant.cmd") (Join-Path $build "Release/meeting-assistant.cmd") -Force
& (Join-Path $PSScriptRoot "smoke.ps1") -Exe $exe
Write-Host "Built $exe and meeting-assistant.cmd"
