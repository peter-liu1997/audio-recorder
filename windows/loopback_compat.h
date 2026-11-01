#pragma once

#include <windows.h>

#if __has_include(<audioclientactivationparams.h>)
#include <audioclientactivationparams.h>
#endif

// Some MinGW header sets omit this public Windows process-loopback ABI.
// Layout and enum values follow Microsoft's AUDIOCLIENT_ACTIVATION_PARAMS:
// https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/
// The runtime build >= 20348 check remains in processClient().
#ifndef VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
#define VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK L"VAD\\Process_Loopback"
enum PROCESS_LOOPBACK_MODE {
    PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0,
    PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
};
struct AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS {
    DWORD TargetProcessId;
    PROCESS_LOOPBACK_MODE ProcessLoopbackMode;
};
enum AUDIOCLIENT_ACTIVATION_TYPE {
    AUDIOCLIENT_ACTIVATION_TYPE_DEFAULT = 0,
    AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
};
struct AUDIOCLIENT_ACTIVATION_PARAMS {
    AUDIOCLIENT_ACTIVATION_TYPE ActivationType;
    union { AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS ProcessLoopbackParams; };
};
static_assert(sizeof(AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS) == 8, "Windows loopback ABI");
static_assert(sizeof(AUDIOCLIENT_ACTIVATION_PARAMS) == 12, "Windows activation ABI");
#endif
