@echo off
set "AUDIO_RECORDER_CLI=%~dp0audio-recorder.exe"
if defined AUDIO_RECORDER_PYTHON (
  "%AUDIO_RECORDER_PYTHON%" "%~dp0gui\meeting_assistant.py" %*
) else (
  py -3 "%~dp0gui\meeting_assistant.py" %*
)
