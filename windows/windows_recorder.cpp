#include <windows.h>
#include <audioclient.h>
#include "loopback_compat.h"
#include <propkeydef.h>
#include <functiondiscoverykeys_devpkey.h>
#include <mmdeviceapi.h>
#include <objbase.h>
#include <objidl.h>
#include <propsys.h>
#include <tlhelp32.h>
#include <winternl.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <cwctype>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;

namespace {
constexpr uint32_t kSampleRate = 48000;
constexpr uint32_t kChannels = 2;
constexpr uint32_t kBlockAlign = 4;
constexpr uint64_t kMaxFrames = (static_cast<uint64_t>(std::numeric_limits<uint32_t>::max()) - 36ULL) / kBlockAlign;

struct Error : std::runtime_error {
    std::string code;
    int exitCode;
    Error(std::string c, int e, std::string message)
        : std::runtime_error(std::move(message)), code(std::move(c)), exitCode(e) {}
};

struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    Handle() = default;
    explicit Handle(HANDLE h) : value(h) {}
    ~Handle() { close(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value(other.value) { other.value = INVALID_HANDLE_VALUE; }
    Handle& operator=(Handle&& other) noexcept {
        if (this != &other) { close(); value = other.value; other.value = INVALID_HANDLE_VALUE; }
        return *this;
    }
    bool valid() const { return value && value != INVALID_HANDLE_VALUE; }
    void close() { if (valid()) CloseHandle(value); value = INVALID_HANDLE_VALUE; }
};

std::string utf8(const std::wstring& value) {
    if (value.empty()) return {};
    int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                                    static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (!length) throw Error("IO_ERROR", 7, "Could not encode UTF-8 text");
    std::string result(static_cast<size_t>(length), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
                        static_cast<int>(value.size()), result.data(), length, nullptr, nullptr);
    return result;
}

std::wstring fromUtf8(const std::string& value) {
    if (value.empty()) return {};
    int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                                     static_cast<int>(value.size()), nullptr, 0);
    if (!length) throw Error("IO_ERROR", 7, "Invalid UTF-8 session data");
    std::wstring result(static_cast<size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                        static_cast<int>(value.size()), result.data(), length);
    return result;
}

std::string quote(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<int>(c) << std::dec;
            } else out << static_cast<char>(c);
        }
    }
    return out.str() + '"';
}

std::string quote(const std::wstring& value) { return quote(utf8(value)); }

std::string number(double value) {
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << std::setprecision(12) << value;
    return out.str();
}

void emit(const std::string& command, const std::string& data) {
    std::cout << "{\"schemaVersion\":1,\"ok\":true,\"command\":" << quote(command)
              << ",\"data\":" << data << "}\n";
}

void emitError(const std::string& command, const Error& error) {
    std::cout << "{\"schemaVersion\":1,\"ok\":false,\"command\":" << quote(command)
              << ",\"error\":{\"code\":" << quote(error.code)
              << ",\"message\":" << quote(error.what()) << "}}\n";
}

[[noreturn]] void failHr(HRESULT hr, const char* action) {
    std::ostringstream text;
    text << action << " failed (HRESULT 0x" << std::hex << static_cast<unsigned long>(hr) << ')';
    if (hr == E_ACCESSDENIED || hr == AUDCLNT_E_DEVICE_IN_USE)
        throw Error("PERMISSION_REQUIRED", 3, text.str());
    throw Error("CAPTURE_FAILED", 6, text.str());
}

void check(HRESULT hr, const char* action) { if (FAILED(hr)) failHr(hr, action); }

[[noreturn]] void failWin32(const char* action) {
    std::ostringstream text;
    text << action << " failed (Win32 " << GetLastError() << ')';
    throw Error("IO_ERROR", 7, text.str());
}

uint64_t nowMs() {
    FILETIME time{};
    GetSystemTimeAsFileTime(&time);
    ULARGE_INTEGER value{};
    value.LowPart = time.dwLowDateTime;
    value.HighPart = time.dwHighDateTime;
    return value.QuadPart / 10000ULL;
}

std::string isoNow() {
    SYSTEMTIME time{};
    GetSystemTime(&time);
    char buffer[32];
    sprintf_s(buffer, "%04u-%02u-%02uT%02u:%02u:%02uZ", time.wYear, time.wMonth,
              time.wDay, time.wHour, time.wMinute, time.wSecond);
    return buffer;
}

std::wstring newId() {
    GUID guid{};
    check(CoCreateGuid(&guid), "Create session ID");
    wchar_t buffer[40];
    StringFromGUID2(guid, buffer, 40);
    std::wstring id(buffer);
    id.erase(std::remove(id.begin(), id.end(), L'{'), id.end());
    id.erase(std::remove(id.begin(), id.end(), L'}'), id.end());
    return id;
}

std::wstring normalizedId(const std::wstring& input) {
    if (input.size() != 36) throw Error("INVALID_SESSION_ID", 4, "Session ID must be a UUID");
    for (size_t i = 0; i < input.size(); ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (input[i] != L'-') throw Error("INVALID_SESSION_ID", 4, "Session ID must be a UUID");
        } else if (!iswxdigit(input[i])) throw Error("INVALID_SESSION_ID", 4, "Session ID must be a UUID");
    }
    std::wstring result = input;
    std::transform(result.begin(), result.end(), result.begin(), towupper);
    return result;
}

std::wstring env(const wchar_t* key) {
    DWORD needed = GetEnvironmentVariableW(key, nullptr, 0);
    if (!needed) return {};
    std::wstring value(needed, L'\0');
    DWORD actual = GetEnvironmentVariableW(key, value.data(), needed);
    if (!actual || actual >= needed) failWin32("Read environment variable");
    value.resize(actual);
    return value;
}

std::wstring lower(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(), towlower);
    return value;
}

struct Options {
    std::wstring source = L"both";
    std::wstring app;
    std::wstring micDevice;
    fs::path output;
    fs::path liveDir;
    double duration = 0;
    double micGain = 1;
    double systemGain = 1;
    bool keepTracks = false;
};

struct Session {
    std::wstring id;
    std::wstring state = L"starting";
    DWORD pid = 0;
    fs::path output;
    std::wstring source;
    std::wstring app;
    fs::path logPath;
    uint64_t createdMs = 0;
    std::string createdAt;
    std::string startedAt;
    std::string endedAt;
    double durationSeconds = 0;
    std::wstring error;
    std::vector<fs::path> tracks;
    Options options;
};

bool active(const Session& s) {
    return s.state == L"starting" || s.state == L"recording" || s.state == L"stopping";
}

std::string sessionJson(const Session& s) {
    std::string result = "{\"schemaVersion\":1,\"id\":" + quote(s.id)
        + ",\"state\":" + quote(s.state) + ",\"output\":" + quote(s.output.wstring())
        + ",\"source\":" + quote(s.source) + ",\"createdAt\":" + quote(s.createdAt)
        + ",\"logPath\":" + quote(s.logPath.wstring());
    if (s.pid) result += ",\"pid\":" + std::to_string(s.pid);
    if (!s.app.empty()) result += ",\"app\":" + quote(s.app);
    if (!s.startedAt.empty()) result += ",\"startedAt\":" + quote(s.startedAt);
    if (!s.endedAt.empty()) result += ",\"endedAt\":" + quote(s.endedAt);
    if (s.state == L"done") result += ",\"durationSeconds\":" + number(s.durationSeconds);
    if (!s.error.empty()) result += ",\"error\":" + quote(s.error);
    result += ",\"tracks\":[";
    for (size_t i = 0; i < s.tracks.size(); ++i) {
        if (i) result += ',';
        result += quote(s.tracks[i].wstring());
    }
    return result + "]}";
}

void writeString(std::ostream& out, const std::string& value) {
    if (value.size() > 1024 * 1024) throw Error("IO_ERROR", 7, "Session field is too long");
    uint32_t length = static_cast<uint32_t>(value.size());
    out.write(reinterpret_cast<const char*>(&length), sizeof(length));
    out.write(value.data(), length);
}

void writeString(std::ostream& out, const std::wstring& value) { writeString(out, utf8(value)); }
void writePath(std::ostream& out, const fs::path& value) { writeString(out, value.wstring()); }

std::string readString(std::istream& in) {
    uint32_t length = 0;
    in.read(reinterpret_cast<char*>(&length), sizeof(length));
    if (!in || length > 1024 * 1024) throw Error("IO_ERROR", 7, "Invalid session file");
    std::string value(length, '\0');
    in.read(value.data(), length);
    if (!in) throw Error("IO_ERROR", 7, "Truncated session file");
    return value;
}

std::wstring readWide(std::istream& in) { return fromUtf8(readString(in)); }
fs::path readPath(std::istream& in) { return fs::path(readWide(in)); }

template<typename T> void writeValue(std::ostream& out, const T& value) {
    out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}

template<typename T> T readValue(std::istream& in) {
    T value{};
    in.read(reinterpret_cast<char*>(&value), sizeof(value));
    if (!in) throw Error("IO_ERROR", 7, "Truncated session file");
    return value;
}

class Store {
    fs::path root_;
    Handle mutex_;

    struct Guard {
        HANDLE h;
        explicit Guard(HANDLE handle) : h(handle) {
            DWORD result = WaitForSingleObject(h, 10000);
            if (result != WAIT_OBJECT_0 && result != WAIT_ABANDONED)
                throw Error("IO_ERROR", 7, "Could not lock session store");
        }
        ~Guard() { ReleaseMutex(h); }
    };

    fs::path path(const std::wstring& id) const { return root_ / (normalizedId(id) + L".session"); }

    Session readRaw(const std::wstring& id) const {
        std::ifstream in(path(id), std::ios::binary);
        if (!in) throw Error("SESSION_NOT_FOUND", 4, "Session not found: " + utf8(id));
        if (readValue<uint32_t>(in) != 0x31575241) throw Error("IO_ERROR", 7, "Invalid session version");
        Session s;
        s.id = readWide(in); s.state = readWide(in); s.pid = readValue<DWORD>(in);
        s.output = readPath(in); s.source = readWide(in); s.app = readWide(in);
        s.logPath = readPath(in); s.createdMs = readValue<uint64_t>(in);
        s.createdAt = readString(in); s.startedAt = readString(in); s.endedAt = readString(in);
        s.durationSeconds = readValue<double>(in); s.error = readWide(in);
        uint32_t count = readValue<uint32_t>(in);
        if (count > 8) throw Error("IO_ERROR", 7, "Invalid track count");
        for (uint32_t i = 0; i < count; ++i) s.tracks.push_back(readPath(in));
        s.options.source = readWide(in); s.options.app = readWide(in);
        s.options.micDevice = readWide(in); s.options.output = readPath(in);
        s.options.duration = readValue<double>(in); s.options.micGain = readValue<double>(in);
        s.options.systemGain = readValue<double>(in);
        s.options.keepTracks = readValue<uint8_t>(in) != 0;
        return s;
    }

    void saveRaw(const Session& s) const {
        fs::path temp = root_ / (s.id + L".tmp." + std::to_wstring(GetCurrentProcessId()));
        {
            std::ofstream out(temp, std::ios::binary | std::ios::trunc);
            if (!out) throw Error("IO_ERROR", 7, "Could not write session file");
            writeValue<uint32_t>(out, 0x31575241);
            writeString(out, s.id); writeString(out, s.state); writeValue(out, s.pid);
            writePath(out, s.output); writeString(out, s.source); writeString(out, s.app);
            writePath(out, s.logPath); writeValue(out, s.createdMs);
            writeString(out, s.createdAt); writeString(out, s.startedAt); writeString(out, s.endedAt);
            writeValue(out, s.durationSeconds); writeString(out, s.error);
            writeValue<uint32_t>(out, static_cast<uint32_t>(s.tracks.size()));
            for (const auto& track : s.tracks) writePath(out, track);
            writeString(out, s.options.source); writeString(out, s.options.app);
            writeString(out, s.options.micDevice); writePath(out, s.options.output);
            writeValue(out, s.options.duration); writeValue(out, s.options.micGain);
            writeValue(out, s.options.systemGain);
            writeValue<uint8_t>(out, s.options.keepTracks ? 1 : 0);
            out.flush();
            if (!out) throw Error("IO_ERROR", 7, "Could not flush session file");
        }
        if (!MoveFileExW(temp.c_str(), path(s.id).c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
            failWin32("Commit session file");
    }

    Session refresh(Session s) const {
        if (!active(s)) return s;
        bool running = false;
        if (s.pid) {
            Handle process(OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, s.pid));
            if (process.valid()) running = WaitForSingleObject(process.value, 0) == WAIT_TIMEOUT;
            else if (GetLastError() == ERROR_ACCESS_DENIED) running = true;
        } else running = nowMs() - s.createdMs < 60000;
        if (running) return s;
        s.state = L"failed";
        s.error = L"Recorder process exited before finalizing; inspect log and temporary tracks";
        s.endedAt = isoNow();
        saveRaw(s);
        return s;
    }

    std::vector<Session> listRaw() const {
        std::vector<Session> items;
        for (const auto& entry : fs::directory_iterator(root_)) {
            if (entry.path().extension() != L".session") continue;
            items.push_back(refresh(readRaw(entry.path().stem().wstring())));
        }
        std::sort(items.begin(), items.end(), [](const Session& a, const Session& b) {
            return a.createdMs > b.createdMs;
        });
        return items;
    }

public:
    Store() {
        std::wstring configured = env(L"AUDIO_RECORDER_STATE_DIR");
        if (!configured.empty()) root_ = fs::absolute(fs::path(configured));
        else {
            std::wstring local = env(L"LOCALAPPDATA");
            if (local.empty()) throw Error("IO_ERROR", 7, "LOCALAPPDATA is unavailable");
            root_ = fs::path(local) / L"AudioRecorder" / L"sessions";
        }
        std::error_code ec;
        fs::create_directories(root_, ec);
        if (ec) throw Error("IO_ERROR", 7, "Could not create session directory");
        mutex_ = Handle(CreateMutexW(nullptr, FALSE, L"Local\\AudioRecorderSessionStore"));
        if (!mutex_.valid()) failWin32("Create session mutex");
    }
    const fs::path& root() const { return root_; }
    Session read(const std::wstring& id) const { Guard lock(mutex_.value); return refresh(readRaw(id)); }
    std::vector<Session> list() const { Guard lock(mutex_.value); return listRaw(); }
    void create(const Session& session) const {
        Guard lock(mutex_.value);
        for (const auto& current : listRaw()) {
            if (active(current)) throw Error("ALREADY_RECORDING", 4, "Active session: " + utf8(current.id));
        }
        saveRaw(session);
    }
    Session update(const std::wstring& id, const std::function<void(Session&)>& change) const {
        Guard lock(mutex_.value);
        Session s = readRaw(id);
        change(s);
        saveRaw(s);
        return s;
    }
};

struct DeviceInfo { std::wstring id; std::wstring name; };

std::vector<DeviceInfo> devices(EDataFlow flow) {
    ComPtr<IMMDeviceEnumerator> enumerator;
    check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                           IID_PPV_ARGS(&enumerator)), "Enumerate audio devices");
    ComPtr<IMMDeviceCollection> collection;
    check(enumerator->EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE, &collection), "List audio endpoints");
    UINT count = 0;
    check(collection->GetCount(&count), "Count audio endpoints");
    std::vector<DeviceInfo> result;
    for (UINT i = 0; i < count; ++i) {
        ComPtr<IMMDevice> device;
        check(collection->Item(i, &device), "Read audio endpoint");
        LPWSTR id = nullptr;
        check(device->GetId(&id), "Read audio endpoint ID");
        DeviceInfo info;
        info.id = id;
        CoTaskMemFree(id);
        ComPtr<IPropertyStore> properties;
        check(device->OpenPropertyStore(STGM_READ, &properties), "Read audio endpoint properties");
        PROPVARIANT name{};
        PropVariantInit(&name);
        if (SUCCEEDED(properties->GetValue(PKEY_Device_FriendlyName, &name)) && name.vt == VT_LPWSTR)
            info.name = name.pwszVal;
        PropVariantClear(&name);
        result.push_back(std::move(info));
    }
    return result;
}

struct ProcessInfo { DWORD pid; std::wstring name; };

std::vector<ProcessInfo> processes() {
    Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (!snapshot.valid()) failWin32("List processes");
    PROCESSENTRY32W item{};
    item.dwSize = sizeof(item);
    std::vector<ProcessInfo> result;
    if (!Process32FirstW(snapshot.value, &item)) return result;
    do { result.push_back({item.th32ProcessID, item.szExeFile}); }
    while (Process32NextW(snapshot.value, &item));
    std::sort(result.begin(), result.end(), [](const auto& a, const auto& b) {
        return lower(a.name) < lower(b.name);
    });
    return result;
}

DWORD resolveProcess(const std::wstring& query) {
    if (query.empty()) throw Error("INVALID_ARGUMENT", 2, "--app needs a process name or PID");
    wchar_t* end = nullptr;
    unsigned long numeric = wcstoul(query.c_str(), &end, 10);
    if (*end == L'\0' && numeric > 0) {
        Handle process(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, numeric));
        if (!process.valid()) throw Error("SOURCE_NOT_FOUND", 5, "Process not found: " + utf8(query));
        return numeric;
    }
    std::wstring target = lower(query);
    auto entries = processes();
    std::vector<DWORD> exact, partial;
    for (const auto& entry : entries) {
        std::wstring name = lower(entry.name);
        if (name == target || name == target + L".exe") exact.push_back(entry.pid);
        else if (name.find(target) != std::wstring::npos) partial.push_back(entry.pid);
    }
    const auto& matched = exact.empty() ? partial : exact;
    if (matched.empty()) throw Error("SOURCE_NOT_FOUND", 5, "No process matches: " + utf8(query));
    if (matched.size() != 1) throw Error("SOURCE_NOT_FOUND", 5, "Multiple processes match; use a PID from 'apps'");
    return matched.front();
}

uint32_t windowsBuild() {
    using RtlGetVersionFn = LONG(WINAPI*)(PRTL_OSVERSIONINFOW);
    HMODULE library = GetModuleHandleW(L"ntdll.dll");
    auto fn = reinterpret_cast<RtlGetVersionFn>(GetProcAddress(library, "RtlGetVersion"));
    if (!fn) return 0;
    RTL_OSVERSIONINFOW version{};
    version.dwOSVersionInfoSize = sizeof(version);
    return fn(&version) == 0 ? version.dwBuildNumber : 0;
}

class ActivationHandler final : public IActivateAudioInterfaceCompletionHandler, public IAgileObject {
    std::atomic<ULONG> refs_{1};
public:
    Handle completed{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    HRESULT result = E_PENDING;
    ComPtr<IAudioClient> client;

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void** object) override {
        if (!object) return E_POINTER;
        if (id == __uuidof(IUnknown) || id == __uuidof(IActivateAudioInterfaceCompletionHandler)) {
            *object = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
            AddRef();
            return S_OK;
        }
        if (id == __uuidof(IAgileObject)) {
            *object = static_cast<IAgileObject*>(this);
            AddRef();
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG left = --refs_;
        if (!left) delete this;
        return left;
    }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT activation = E_FAIL;
        ComPtr<IUnknown> unknown;
        result = operation->GetActivateResult(&activation, &unknown);
        if (SUCCEEDED(result)) result = activation;
        if (SUCCEEDED(result)) result = unknown.As(&client);
        SetEvent(completed.value);
        return S_OK;
    }
};

ComPtr<IAudioClient> processClient(DWORD pid) {
    if (windowsBuild() < 20348)
        throw Error("UNSUPPORTED_OS", 5, "Application loopback needs Windows build 20348 or later");
    AUDIOCLIENT_ACTIVATION_PARAMS activation{};
    activation.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    activation.ProcessLoopbackParams.TargetProcessId = pid;
    activation.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT parameters{};
    parameters.vt = VT_BLOB;
    parameters.blob.cbSize = sizeof(activation);
    parameters.blob.pBlobData = reinterpret_cast<BYTE*>(&activation);
    ActivationHandler* handler = new ActivationHandler();
    if (!handler->completed.valid()) {
        handler->Release();
        failWin32("Create activation event");
    }
    ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
    HRESULT hr = ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
                                              __uuidof(IAudioClient), &parameters,
                                              handler, &operation);
    if (FAILED(hr)) { handler->Release(); failHr(hr, "Activate application loopback"); }
    DWORD wait = WaitForSingleObject(handler->completed.value, 10000);
    if (wait != WAIT_OBJECT_0) {
        handler->Release();
        throw Error("CAPTURE_FAILED", 6, "Application loopback activation timed out");
    }
    hr = handler->result;
    ComPtr<IAudioClient> client = handler->client;
    handler->Release();
    check(hr, "Activate application loopback");
    return client;
}

ComPtr<IAudioClient> endpointClient(EDataFlow flow, const std::wstring& deviceId) {
    ComPtr<IMMDeviceEnumerator> enumerator;
    check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                           IID_PPV_ARGS(&enumerator)), "Open audio device enumerator");
    ComPtr<IMMDevice> device;
    HRESULT hr;
    if (deviceId.empty()) hr = enumerator->GetDefaultAudioEndpoint(flow, eConsole, &device);
    else hr = enumerator->GetDevice(deviceId.c_str(), &device);
    if (FAILED(hr)) throw Error("SOURCE_NOT_FOUND", 5, "Audio device not found");
    ComPtr<IAudioClient> client;
    check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                           reinterpret_cast<void**>(client.GetAddressOf())), "Activate audio endpoint");
    return client;
}

uint64_t qpc100() {
    LARGE_INTEGER counter{}, frequency{};
    QueryPerformanceCounter(&counter);
    QueryPerformanceFrequency(&frequency);
    return static_cast<uint64_t>(static_cast<long double>(counter.QuadPart) * 10000000.0L
                                 / static_cast<long double>(frequency.QuadPart));
}

void seek(HANDLE file, uint64_t offset) {
    LARGE_INTEGER position{};
    position.QuadPart = static_cast<LONGLONG>(offset);
    if (!SetFilePointerEx(file, position, nullptr, FILE_BEGIN)) failWin32("Seek audio file");
}

void writeExact(HANDLE file, const void* data, DWORD bytes) {
    DWORD written = 0;
    if (!WriteFile(file, data, bytes, &written, nullptr) || written != bytes) failWin32("Write audio file");
}

struct Track {
    ComPtr<IAudioClient> client;
    ComPtr<IAudioCaptureClient> capture;
    Handle raw;
    fs::path rawPath;
    uint64_t nextFrame = 0;
    uint64_t maxFrame = 0;
    uint64_t packets = 0;

    Track() = default;
    Track(const Track&) = delete;
    Track& operator=(const Track&) = delete;
    Track(Track&&) = default;
    Track& operator=(Track&&) = default;

    void init(ComPtr<IAudioClient> audio, bool loopback, const fs::path& path) {
        client = std::move(audio);
        WAVEFORMATEX format{};
        format.wFormatTag = WAVE_FORMAT_PCM;
        format.nChannels = kChannels;
        format.nSamplesPerSec = kSampleRate;
        format.wBitsPerSample = 16;
        format.nBlockAlign = kBlockAlign;
        format.nAvgBytesPerSec = kSampleRate * kBlockAlign;
        DWORD flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
        if (loopback) flags |= AUDCLNT_STREAMFLAGS_LOOPBACK;
        check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 2000000, 0, &format, nullptr),
              "Initialize 48 kHz audio capture");
        check(client->GetService(IID_PPV_ARGS(&capture)), "Open audio capture stream");
        rawPath = path;
        raw = Handle(CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
                                 nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (!raw.valid()) failWin32("Create temporary audio track");
    }

    void drain(uint64_t origin) {
        UINT32 pending = 0;
        check(capture->GetNextPacketSize(&pending), "Read audio packet size");
        while (pending) {
            BYTE* data = nullptr;
            UINT32 frames = 0;
            DWORD flags = 0;
            UINT64 devicePosition = 0, timestamp = 0;
            check(capture->GetBuffer(&data, &frames, &flags, &devicePosition, &timestamp),
                  "Read audio packet");
            try {
                uint64_t position = nextFrame;
                if (!(flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR) && timestamp >= origin) {
                    uint64_t timed = (timestamp - origin) * kSampleRate / 10000000ULL;
                    if (timed > nextFrame + 48) position = timed;
                }
                if (position + frames > kMaxFrames)
                    throw Error("AUDIO_PROCESSING_FAILED", 6, "WAV 4 GiB size limit reached");
                seek(raw.value, position * kBlockAlign);
                if (flags & AUDCLNT_BUFFERFLAGS_SILENT) {
                    std::vector<BYTE> zero(static_cast<size_t>(frames) * kBlockAlign);
                    writeExact(raw.value, zero.data(), static_cast<DWORD>(zero.size()));
                } else writeExact(raw.value, data, frames * kBlockAlign);
                nextFrame = position + frames;
                maxFrame = std::max(maxFrame, nextFrame);
                ++packets;
            } catch (...) {
                capture->ReleaseBuffer(frames);
                throw;
            }
            check(capture->ReleaseBuffer(frames), "Release audio packet");
            check(capture->GetNextPacketSize(&pending), "Read audio packet size");
        }
    }
};

void writeWavHeader(HANDLE file, uint32_t bytes) {
    const char riff[4] = {'R', 'I', 'F', 'F'};
    const char wave[4] = {'W', 'A', 'V', 'E'};
    const char fmt[4] = {'f', 'm', 't', ' '};
    const char data[4] = {'d', 'a', 't', 'a'};
    uint32_t riffSize = 36 + bytes, fmtSize = 16, byteRate = kSampleRate * kBlockAlign;
    uint16_t pcm = 1, channels = kChannels, align = kBlockAlign, bits = 16;
    writeExact(file, riff, 4); writeExact(file, &riffSize, 4); writeExact(file, wave, 4);
    writeExact(file, fmt, 4); writeExact(file, &fmtSize, 4);
    writeExact(file, &pcm, 2); writeExact(file, &channels, 2);
    writeExact(file, &kSampleRate, 4); writeExact(file, &byteRate, 4);
    writeExact(file, &align, 2); writeExact(file, &bits, 2);
    writeExact(file, data, 4); writeExact(file, &bytes, 4);
}

void readSamples(Track* track, uint64_t frame, uint32_t count, std::vector<int16_t>& values) {
    values.assign(static_cast<size_t>(count) * kChannels, 0);
    if (!track || frame >= track->maxFrame) return;
    uint64_t available = std::min<uint64_t>(count, track->maxFrame - frame);
    seek(track->raw.value, frame * kBlockAlign);
    DWORD bytes = static_cast<DWORD>(available * kBlockAlign), got = 0;
    if (!ReadFile(track->raw.value, values.data(), bytes, &got, nullptr)) failWin32("Read temporary audio track");
    if (got < bytes) {
        BYTE* tail = reinterpret_cast<BYTE*>(values.data()) + got;
        std::fill(tail, tail + (bytes - got), 0);
    }
}

void renderWav(const fs::path& output, Track* mic, Track* system,
               uint64_t frames, double micGain, double systemGain, const std::wstring& id) {
    if (frames > kMaxFrames) throw Error("AUDIO_PROCESSING_FAILED", 6, "WAV 4 GiB size limit reached");
    fs::path temp = output;
    temp += L".part." + id;
    Handle file(CreateFileW(temp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!file.valid()) failWin32("Create temporary WAV");
    try {
        writeWavHeader(file.value, static_cast<uint32_t>(frames * kBlockAlign));
        std::vector<int16_t> micData, systemData, mixed;
        for (uint64_t position = 0; position < frames;) {
            uint32_t count = static_cast<uint32_t>(std::min<uint64_t>(8192, frames - position));
            readSamples(mic, position, count, micData);
            readSamples(system, position, count, systemData);
            mixed.resize(static_cast<size_t>(count) * kChannels);
            for (size_t i = 0; i < mixed.size(); ++i) {
                double sample = (mic ? micData[i] * micGain : 0.0)
                              + (system ? systemData[i] * systemGain : 0.0);
                mixed[i] = static_cast<int16_t>(std::clamp(std::lround(sample), -32768L, 32767L));
            }
            writeExact(file.value, mixed.data(), count * kBlockAlign);
            position += count;
        }
        if (!FlushFileBuffers(file.value)) failWin32("Flush WAV");
        file.close();
        if (!MoveFileExW(temp.c_str(), output.c_str(), MOVEFILE_WRITE_THROUGH)) {
            if (GetLastError() == ERROR_ALREADY_EXISTS || GetLastError() == ERROR_FILE_EXISTS)
                throw Error("OUTPUT_EXISTS", 4, "Output already exists: " + utf8(output.wstring()));
            failWin32("Commit WAV");
        }
    } catch (...) {
        file.close();
        DeleteFileW(temp.c_str());
        throw;
    }
}

std::atomic<bool> stopRequested{false};
BOOL WINAPI consoleControl(DWORD signal) {
    if (signal == CTRL_C_EVENT || signal == CTRL_BREAK_EVENT) {
        stopRequested = true;
        return TRUE;
    }
    return FALSE;
}

struct Outcome { double duration; std::vector<fs::path> tracks; };

Outcome captureAudio(const Options& options, const std::wstring& id,
                     const std::function<void()>& started,
                     const std::function<bool()>& shouldStop) {
    bool micEnabled = options.source != L"system";
    bool systemEnabled = options.source != L"mic";
    if (!options.liveDir.empty()) fs::create_directories(options.liveDir);
    fs::path micRaw = options.liveDir.empty() ? options.output : options.liveDir / L"microphone.raw";
    fs::path systemRaw = options.liveDir.empty() ? options.output : options.liveDir / L"system.raw";
    if (options.liveDir.empty()) {
        micRaw += L".microphone.raw." + id;
        systemRaw += L".system.raw." + id;
    }
    Track mic, system;
    if (micEnabled) mic.init(endpointClient(eCapture, options.micDevice), false, micRaw);
    if (systemEnabled) {
        if (options.app.empty()) system.init(endpointClient(eRender, L""), true, systemRaw);
        else system.init(processClient(resolveProcess(options.app)), true, systemRaw);
    }
    uint64_t origin = qpc100();
    bool micStarted = false, systemStarted = false;
    try {
        if (micEnabled) { check(mic.client->Start(), "Start microphone capture"); micStarted = true; }
        if (systemEnabled) { check(system.client->Start(), "Start system capture"); systemStarted = true; }
        started();
        SetConsoleCtrlHandler(consoleControl, TRUE);
        uint64_t end = origin;
        while (!stopRequested && !shouldStop()) {
            if (micEnabled) mic.drain(origin);
            if (systemEnabled) system.drain(origin);
            end = qpc100();
            if (options.duration > 0 && static_cast<double>(end - origin) / 10000000.0 >= options.duration) break;
            if ((end - origin) * kSampleRate / 10000000ULL >= kMaxFrames)
                throw Error("AUDIO_PROCESSING_FAILED", 6, "WAV 4 GiB size limit reached");
            Sleep(10);
        }
        if (micStarted) { check(mic.client->Stop(), "Stop microphone capture"); micStarted = false; }
        if (systemStarted) { check(system.client->Stop(), "Stop system capture"); systemStarted = false; }
        if (micEnabled) mic.drain(origin);
        if (systemEnabled) system.drain(origin);
        SetConsoleCtrlHandler(consoleControl, FALSE);
        if ((micEnabled && mic.packets == 0) && (!systemEnabled || system.packets == 0))
            throw Error("CAPTURE_FAILED", 6, "No audio packets arrived; check input device and playback");
        if (!micEnabled && system.packets == 0)
            throw Error("CAPTURE_FAILED", 6, "No audio packets arrived; check playback");
        uint64_t durationFrames = (end - origin) * kSampleRate / 10000000ULL;
        if (options.duration > 0)
            durationFrames = std::min(durationFrames, static_cast<uint64_t>(options.duration * kSampleRate));
        durationFrames = std::min(durationFrames, kMaxFrames);
        if (!durationFrames) throw Error("CAPTURE_FAILED", 6, "Recording is empty");
        Outcome outcome{static_cast<double>(durationFrames) / kSampleRate, {}};
        renderWav(options.output, micEnabled ? &mic : nullptr, systemEnabled ? &system : nullptr,
                  durationFrames, options.micGain, options.systemGain, id);
        if (options.keepTracks) {
            if (micEnabled) {
                fs::path target = options.output;
                target.replace_extension(L".microphone.wav");
                renderWav(target, &mic, nullptr, durationFrames, 1, 0, id);
                outcome.tracks.push_back(target);
            }
            if (systemEnabled) {
                fs::path target = options.output;
                target.replace_extension(L".system.wav");
                renderWav(target, nullptr, &system, durationFrames, 0, 1, id);
                outcome.tracks.push_back(target);
            }
        }
        mic.raw.close(); system.raw.close();
        if (options.liveDir.empty()) {
            if (micEnabled) DeleteFileW(micRaw.c_str());
            if (systemEnabled) DeleteFileW(systemRaw.c_str());
        }
        return outcome;
    } catch (...) {
        if (micStarted) mic.client->Stop();
        if (systemStarted) system.client->Stop();
        SetConsoleCtrlHandler(consoleControl, FALSE);
        // Raw tracks remain in the output directory for recovery.
        throw;
    }
}

void selfTest() {
    fs::path root = Store().root();
    fs::path micPath = root / L"synthetic-mic.raw";
    fs::path systemPath = root / L"synthetic-system.raw";
    fs::path output = root / L"synthetic.wav";
    Track mic, system;
    mic.raw = Handle(CreateFileW(micPath.c_str(), GENERIC_READ | GENERIC_WRITE, 0,
                                 nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
    system.raw = Handle(CreateFileW(systemPath.c_str(), GENERIC_READ | GENERIC_WRITE, 0,
                                    nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!mic.raw.valid() || !system.raw.valid()) failWin32("Create synthetic tracks");
    int16_t micSamples[] = {1000, -1000, 2000, -2000};
    int16_t systemSamples[] = {500, 500, -500, 500};
    writeExact(mic.raw.value, micSamples, sizeof(micSamples));
    writeExact(system.raw.value, systemSamples, sizeof(systemSamples));
    mic.maxFrame = system.maxFrame = 2;
    renderWav(output, &mic, &system, 2, 1, 1, newId());
    std::ifstream result(output, std::ios::binary);
    result.seekg(44);
    int16_t mixed[4]{};
    result.read(reinterpret_cast<char*>(mixed), sizeof(mixed));
    if (!result || mixed[0] != 1500 || mixed[1] != -500
        || mixed[2] != 1500 || mixed[3] != -1500)
        throw Error("AUDIO_PROCESSING_FAILED", 6, "Synthetic WAV mixing check failed");
    result.close();
    mic.raw.close(); system.raw.close();
    fs::remove(micPath); fs::remove(systemPath); fs::remove(output);
}

double positive(const std::wstring& value, const char* name, double min, double max) {
    try {
        size_t used = 0;
        double result = std::stod(value, &used);
        if (used != value.size() || !std::isfinite(result) || result < min || result > max)
            throw std::invalid_argument("range");
        return result;
    } catch (...) { throw Error("INVALID_ARGUMENT", 2, std::string("Invalid ") + name); }
}

Options parseOptions(const std::vector<std::wstring>& args) {
    Options options;
    for (size_t i = 0; i < args.size(); ++i) {
        const auto& flag = args[i];
        if (flag == L"--keep-tracks") { options.keepTracks = true; continue; }
        if (i + 1 >= args.size()) throw Error("INVALID_ARGUMENT", 2, "Missing value for " + utf8(flag));
        const auto& value = args[++i];
        if (flag == L"--source") options.source = value;
        else if (flag == L"--app") options.app = value;
        else if (flag == L"--mic-device") options.micDevice = value;
        else if (flag == L"--output") options.output = value;
        else if (flag == L"--live-dir") options.liveDir = value;
        else if (flag == L"--duration") options.duration = positive(value, "--duration", 0.001, 22000);
        else if (flag == L"--mic-gain") options.micGain = positive(value, "--mic-gain", 0, 4);
        else if (flag == L"--system-gain") options.systemGain = positive(value, "--system-gain", 0, 4);
        else throw Error("INVALID_ARGUMENT", 2, "Unknown option: " + utf8(flag));
    }
    if (options.source != L"mic" && options.source != L"system" && options.source != L"both")
        throw Error("INVALID_ARGUMENT", 2, "--source must be mic, system, or both");
    if (!options.app.empty() && options.source == L"mic")
        throw Error("INVALID_ARGUMENT", 2, "--app requires system audio");
    if (!options.micDevice.empty() && options.source == L"system")
        throw Error("INVALID_ARGUMENT", 2, "--mic-device requires microphone audio");
    if (options.output.empty()) {
        SYSTEMTIME time{}; GetLocalTime(&time);
        wchar_t name[64];
        swprintf_s(name, L"recording-%04u%02u%02u-%02u%02u%02u.wav", time.wYear,
                   time.wMonth, time.wDay, time.wHour, time.wMinute, time.wSecond);
        options.output = name;
    }
    options.output = fs::absolute(options.output).lexically_normal();
    if (!options.liveDir.empty()) options.liveDir = fs::absolute(options.liveDir).lexically_normal();
    if (lower(options.output.extension().wstring()) != L".wav")
        throw Error("INVALID_ARGUMENT", 2, "--output must have a .wav extension");
    if (!fs::exists(options.output.parent_path()))
        throw Error("INVALID_ARGUMENT", 2, "Output directory does not exist");
    if (fs::exists(options.output))
        throw Error("OUTPUT_EXISTS", 4, "Output already exists: " + utf8(options.output.wstring()));
    if (options.keepTracks) {
        for (const wchar_t* suffix : {L".microphone.wav", L".system.wav"}) {
            fs::path path = options.output;
            path.replace_extension(suffix);
            if (fs::exists(path)) throw Error("OUTPUT_EXISTS", 4, "Track already exists: " + utf8(path.wstring()));
        }
    }
    if (options.duration > 0 && options.duration * kSampleRate > kMaxFrames)
        throw Error("INVALID_ARGUMENT", 2, "Duration exceeds WAV 4 GiB limit");
    return options;
}

std::wstring modulePath() {
    std::wstring buffer(32768, L'\0');
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (!length || length == buffer.size()) failWin32("Find recorder executable");
    buffer.resize(length);
    return buffer;
}

std::wstring optionalId(const std::vector<std::wstring>& args) {
    if (args.empty()) return {};
    if (args.size() != 2 || args[0] != L"--id")
        throw Error("INVALID_ARGUMENT", 2, "Expected --id UUID");
    return normalizedId(args[1]);
}

void showSession(const std::string& command, const Session& session, bool json) {
    if (json) emit(command, "{\"session\":" + sessionJson(session) + "}");
    else {
        std::cout << utf8(session.id) << '\t' << utf8(session.state) << '\t'
                  << utf8(session.output.wstring()) << '\n';
        if (!session.error.empty()) std::cout << "Error: " << utf8(session.error) << '\n';
    }
}

Session newestOrActive(const std::vector<Session>& sessions, bool activeOnly) {
    for (const auto& s : sessions) if (active(s)) return s;
    if (!activeOnly && !sessions.empty()) return sessions.front();
    throw Error("NO_ACTIVE_SESSION", 4, "No active recording");
}

void validateSource(const Options& options) {
    if (options.source != L"system") {
        auto mics = devices(eCapture);
        if (mics.empty()) throw Error("SOURCE_NOT_FOUND", 5, "No active microphone endpoint");
        if (!options.micDevice.empty() &&
            std::none_of(mics.begin(), mics.end(), [&](const DeviceInfo& d) { return d.id == options.micDevice; }))
            throw Error("SOURCE_NOT_FOUND", 5, "Microphone device ID not found");
    }
    if (options.source != L"mic") {
        if (options.app.empty()) {
            if (devices(eRender).empty())
                throw Error("SOURCE_NOT_FOUND", 5, "No active render endpoint");
        } else {
            if (windowsBuild() < 20348)
                throw Error("UNSUPPORTED_OS", 5, "Application capture needs Windows build 20348 or later");
            resolveProcess(options.app);
        }
    }
}

void worker(const std::wstring& id) {
    Store store;
    Session session = store.read(id);
    stopRequested = false;
    try {
        Outcome outcome = captureAudio(session.options, id,
            [&] {
                store.update(id, [](Session& current) {
                    if (current.state != L"stopping") current.state = L"recording";
                    current.startedAt = isoNow();
                });
            },
            [&] { return store.read(id).state == L"stopping"; });
        store.update(id, [&](Session& current) {
            current.state = L"done";
            current.endedAt = isoNow();
            current.durationSeconds = outcome.duration;
            current.tracks = outcome.tracks;
        });
    } catch (const std::exception& e) {
        store.update(id, [&](Session& current) {
            current.state = L"failed";
            current.endedAt = isoNow();
            current.error = fromUtf8(e.what());
        });
        throw;
    }
}

void start(const Options& options, bool json) {
    if (!options.liveDir.empty()) throw Error("INVALID_ARGUMENT", 2, "--live-dir is only valid with foreground record");
    validateSource(options);
    Store store;
    Session session;
    session.id = newId();
    session.output = options.output;
    session.source = options.source;
    session.app = options.app;
    session.options = options;
    session.createdMs = nowMs();
    session.createdAt = isoNow();
    session.logPath = store.root() / (session.id + L".log");
    store.create(session);

    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.bInheritHandle = TRUE;
    Handle log(CreateFileW(session.logPath.c_str(), GENERIC_WRITE, FILE_SHARE_READ, &security,
                           CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!log.valid()) {
        store.update(session.id, [](Session& current) { current.state = L"failed"; current.error = L"Could not create worker log"; });
        failWin32("Create worker log");
    }
    Handle input(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             &security, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!input.valid()) failWin32("Open worker input");
    std::wstring command = L"\"" + modulePath() + L"\" __worker " + session.id;
    std::vector<wchar_t> mutableCommand(command.begin(), command.end());
    mutableCommand.push_back(L'\0');
    // Limit inheritance to the worker's log and NUL input. Inheriting the
    // caller's stdout pipe keeps PowerShell/Agent output capture waiting for
    // EOF until the recording ends, even after this start command has exited.
    SIZE_T attributeBytes = 0;
    InitializeProcThreadAttributeList(nullptr, 1, 0, &attributeBytes);
    if (!attributeBytes) failWin32("Size worker handle list");
    std::vector<unsigned char> attributeStorage(attributeBytes);
    auto attributes = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributeStorage.data());
    if (!InitializeProcThreadAttributeList(attributes, 1, 0, &attributeBytes))
        failWin32("Initialize worker handle list");
    HANDLE inheritedHandles[] = {input.value, log.value};
    if (!UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                   inheritedHandles, sizeof(inheritedHandles), nullptr, nullptr)) {
        DWORD error = GetLastError();
        DeleteProcThreadAttributeList(attributes);
        SetLastError(error);
        failWin32("Set worker handle list");
    }
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = input.value;
    startup.StartupInfo.hStdOutput = log.value;
    startup.StartupInfo.hStdError = log.value;
    startup.lpAttributeList = attributes;
    PROCESS_INFORMATION process{};
    BOOL created = CreateProcessW(nullptr, mutableCommand.data(), nullptr, nullptr, TRUE,
                                  CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT,
                                  nullptr, nullptr, &startup.StartupInfo, &process);
    DWORD createError = GetLastError();
    DeleteProcThreadAttributeList(attributes);
    if (!created) {
        store.update(session.id, [](Session& current) { current.state = L"failed"; current.error = L"Could not launch recorder worker"; });
        SetLastError(createError);
        failWin32("Launch recorder worker");
    }
    Handle child(process.hProcess), thread(process.hThread);
    store.update(session.id, [&](Session& current) { current.pid = process.dwProcessId; });
    uint64_t deadline = GetTickCount64() + 8000;
    Session current = store.read(session.id);
    while (current.state == L"starting" && GetTickCount64() < deadline) {
        Sleep(100);
        current = store.read(session.id);
    }
    if (current.state == L"failed")
        throw Error("CAPTURE_FAILED", 6, "Session " + utf8(session.id) + " failed: " + utf8(current.error));
    showSession("start", current, json);
}

void stop(const std::vector<std::wstring>& args, bool json) {
    std::wstring id = optionalId(args);
    Store store;
    Session session = id.empty() ? newestOrActive(store.list(), true) : store.read(id);
    session = store.update(session.id, [](Session& current) {
        if (current.state == L"starting" || current.state == L"recording") current.state = L"stopping";
    });
    if (session.state == L"failed")
        throw Error("CAPTURE_FAILED", 6, "Session " + utf8(session.id) + " failed: " + utf8(session.error));
    if (session.state == L"done") { showSession("stop", session, json); return; }
    uint64_t deadline = GetTickCount64() + 20000;
    while (GetTickCount64() < deadline) {
        Session current = store.read(session.id);
        if (current.state == L"failed")
            throw Error("CAPTURE_FAILED", 6, "Session " + utf8(session.id) + " failed: " + utf8(current.error));
        if (current.state == L"done") { showSession("stop", current, json); return; }
        Sleep(150);
    }
    throw Error("STOP_TIMEOUT", 6, "Stop requested; check status for session " + utf8(session.id));
}

const char* help = R"(audio-recorder - microphone and application/system audio on Windows

Usage:
  audio-recorder doctor [--json]
  audio-recorder devices [--json]
  audio-recorder apps [--json]
  audio-recorder record [options] [--json]
  audio-recorder start [options] [--json]
  audio-recorder status [--id UUID] [--json]
  audio-recorder stop [--id UUID] [--json]
  audio-recorder list [--json]
  audio-recorder transcribe --input FILE [--backend local|local-gguf|qwen] [options] [--json]
  audio-recorder live [record and model options] [--json]

Options:
  --source mic|system|both  Source, default both
  --app NAME|PID           Capture one process and its children; use apps for PID
  --mic-device ID          Device ID from devices
  --output FILE.wav        Output path (existing files are never overwritten)
  --duration SECONDS       Stop automatically; Ctrl-C stops foreground recording
  --mic-gain 0..4          Microphone mix level
  --system-gain 0..4       System mix level
  --keep-tracks            Save separate .microphone.wav and .system.wav
  --json                   Emit one JSON object on stdout

Transcription:
  --backend NAME            local (Whisper), local-gguf (Qwen3/SenseVoice), qwen (cloud)
  --model NAME              Whisper: small, medium, large-v3, large-v3-turbo
                            GGUF: qwen3-asr-0.6b, qwen3-asr-1.7b, sensevoice-small
  --model-path FILE         Local model path; required for local-gguf
  --language zh|yue|en|auto Spoken language; default zh
  --output FILE.txt         Transcript path; writes adjacent .json metadata
  --api-url URL             Qwen workspace /chat/completions URL

Exit codes: 0 success, 2 usage, 3 permission, 4 conflict,
            5 source/OS unavailable, 6 capture failure, 7 I/O failure
)";

std::wstring windowsArgument(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t backslashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') { ++backslashes; continue; }
        if (ch == L'"') {
            result.append(backslashes * 2 + 1, L'\\');
            result += ch;
            backslashes = 0;
            continue;
        }
        result.append(backslashes, L'\\');
        backslashes = 0;
        result += ch;
    }
    result.append(backslashes * 2, L'\\');
    return result + L'"';
}

[[noreturn]] void runPythonScript(const std::wstring& name,
                                  const std::vector<std::wstring>& args, bool json) {
    std::wstring modulePath(32768, L'\0');
    DWORD length = GetModuleFileNameW(nullptr, modulePath.data(), static_cast<DWORD>(modulePath.size()));
    if (!length || length >= modulePath.size())
        throw Error("IO_ERROR", 7, "Could not locate audio-recorder.exe");
    modulePath.resize(length);
    fs::path script = fs::path(modulePath).parent_path() / name;
    if (!fs::exists(script)) script = fs::current_path() / L"scripts" / name;
    if (!fs::exists(script))
        throw Error("DEPENDENCY_MISSING", 5, utf8(name) + " not found next to audio-recorder.exe");
    wchar_t pythonBuffer[32768] = {};
    DWORD pythonLength = GetEnvironmentVariableW(L"AUDIO_RECORDER_PYTHON", pythonBuffer, 32768);
    std::wstring python = pythonLength && pythonLength < 32768 ? pythonBuffer : L"python";
    std::wstring commandLine = windowsArgument(python) + L" " + windowsArgument(script.wstring());
    if (name == L"live.py") commandLine += L" --recorder-cli " + windowsArgument(modulePath);
    for (const auto& arg : args) commandLine += L" " + windowsArgument(arg);
    if (json) commandLine += L" --json";
    STARTUPINFOW startup = {};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {};
    if (!CreateProcessW(nullptr, commandLine.data(), nullptr, nullptr, TRUE, 0,
                        nullptr, nullptr, &startup, &process))
        throw Error("DEPENDENCY_MISSING", 5, "Python 3 is required; set AUDIO_RECORDER_PYTHON");
    CloseHandle(process.hThread);
    if (name == L"live.py") SetConsoleCtrlHandler(consoleControl, TRUE);
    WaitForSingleObject(process.hProcess, INFINITE);
    if (name == L"live.py") SetConsoleCtrlHandler(consoleControl, FALSE);
    DWORD exitCode = 1;
    GetExitCodeProcess(process.hProcess, &exitCode);
    CloseHandle(process.hProcess);
    ExitProcess(exitCode);
}

void run(const std::string& command, const std::vector<std::wstring>& args, bool json) {
    if (command == "help" || command == "--help" || command == "-h") {
        if (json) emit("help", "{\"text\":" + quote(std::string(help)) + "}");
        else std::cout << help;
    } else if (command == "doctor") {
        if (!args.empty()) throw Error("INVALID_ARGUMENT", 2, "doctor takes no options");
        uint32_t build = windowsBuild();
        bool microphone = !devices(eCapture).empty(), render = !devices(eRender).empty();
        if (json) emit("doctor", "{\"windowsBuild\":" + std::to_string(build)
                       + ",\"microphone\":" + quote(microphone ? "available" : "missing")
                       + ",\"systemAudio\":" + quote(render ? "available" : "missing")
                       + ",\"applicationLoopback\":" + (build >= 20348 ? "true" : "false") + "}");
        else std::cout << "Windows build: " << build << "\nMicrophone endpoint: "
                       << (microphone ? "available" : "missing") << "\nRender endpoint: "
                       << (render ? "available" : "missing") << "\nApplication loopback: "
                       << (build >= 20348 ? "available" : "requires build 20348+") << '\n';
    } else if (command == "devices") {
        if (!args.empty()) throw Error("INVALID_ARGUMENT", 2, "devices takes no options");
        auto items = devices(eCapture);
        if (json) {
            std::string data = "{\"devices\":[";
            for (size_t i = 0; i < items.size(); ++i) {
                if (i) data += ',';
                data += "{\"id\":" + quote(items[i].id) + ",\"name\":" + quote(items[i].name) + "}";
            }
            emit("devices", data + "]}");
        } else for (const auto& item : items)
            std::cout << utf8(item.id) << '\t' << utf8(item.name) << '\n';
    } else if (command == "apps") {
        if (!args.empty()) throw Error("INVALID_ARGUMENT", 2, "apps takes no options");
        auto items = processes();
        if (json) {
            std::string data = "{\"apps\":[";
            for (size_t i = 0; i < items.size(); ++i) {
                if (i) data += ',';
                data += "{\"pid\":" + std::to_string(items[i].pid)
                      + ",\"name\":" + quote(items[i].name) + "}";
            }
            emit("apps", data + "]}");
        } else for (const auto& item : items)
            std::cout << item.pid << '\t' << utf8(item.name) << '\n';
    } else if (command == "record") {
        Options options = parseOptions(args);
        validateSource(options);
        stopRequested = false;
        Outcome outcome = captureAudio(options, newId(), [] {}, [] { return false; });
        if (json) {
            std::string data = "{\"output\":" + quote(options.output.wstring())
                + ",\"durationSeconds\":" + number(outcome.duration)
                + ",\"sampleRate\":48000,\"channels\":2,\"format\":\"wav-pcm16\",\"tracks\":[";
            for (size_t i = 0; i < outcome.tracks.size(); ++i) {
                if (i) data += ',';
                data += quote(outcome.tracks[i].wstring());
            }
            emit("record", data + "]}");
        } else std::cout << "Saved " << utf8(options.output.wstring()) << "\n";
    } else if (command == "start") {
        start(parseOptions(args), json);
    } else if (command == "status") {
        std::wstring id = optionalId(args);
        Store store;
        if (!id.empty()) showSession("status", store.read(id), json);
        else {
            auto sessions = store.list();
            if (sessions.empty()) {
                if (json) emit("status", "{\"session\":null}");
                else std::cout << "No sessions\n";
            } else showSession("status", newestOrActive(sessions, false), json);
        }
    } else if (command == "stop") {
        stop(args, json);
    } else if (command == "list") {
        if (!args.empty()) throw Error("INVALID_ARGUMENT", 2, "list takes no options");
        auto sessions = Store().list();
        if (json) {
            std::string data = "{\"sessions\":[";
            for (size_t i = 0; i < sessions.size(); ++i) {
                if (i) data += ',';
                data += sessionJson(sessions[i]);
            }
            emit("list", data + "]}");
        } else {
            for (const auto& session : sessions) showSession("list", session, false);
            if (sessions.empty()) std::cout << "No sessions\n";
        }
    } else if (command == "transcribe") {
        runPythonScript(L"transcribe.py", args, json);
    } else if (command == "live") {
        runPythonScript(L"live.py", args, json);
    } else if (command == "__worker") {
        if (args.size() != 1) throw Error("INVALID_ARGUMENT", 2, "Invalid worker invocation");
        worker(normalizedId(args[0]));
    } else if (command == "__selftest") {
        if (!args.empty()) throw Error("INVALID_ARGUMENT", 2, "__selftest takes no options");
        selfTest();
        if (json) emit("__selftest", "{\"audioMix\":\"passed\"}");
        else std::cout << "Synthetic audio mixing check passed\n";
    } else throw Error("INVALID_ARGUMENT", 2, "Unknown command: " + command);
}

} // namespace

int wmain(int argc, wchar_t* argv[]) {
    SetConsoleOutputCP(CP_UTF8);
    HRESULT initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(initialized)) {
        std::cerr << "Could not initialize COM\n";
        return 1;
    }
    std::vector<std::wstring> args;
    bool json = false;
    for (int i = 1; i < argc; ++i) {
        if (std::wstring(argv[i]) == L"--json") json = true;
        else args.emplace_back(argv[i]);
    }
    std::string command = args.empty() ? "help" : utf8(args.front());
    if (!args.empty()) args.erase(args.begin());
    int exitCode = 0;
    try { run(command, args, json); }
    catch (const Error& error) {
        if (json) emitError(command, error);
        else std::cerr << "audio-recorder: " << error.what() << '\n';
        exitCode = error.exitCode;
    } catch (const std::exception& error) {
        Error wrapped("INTERNAL_ERROR", 1, error.what());
        if (json) emitError(command, wrapped);
        else std::cerr << "audio-recorder: " << wrapped.what() << '\n';
        exitCode = 1;
    }
    CoUninitialize();
    return exitCode;
}



