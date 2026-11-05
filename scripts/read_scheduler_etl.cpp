// Reads a finalized private ETL only. No live session, recording, input or GUI APIs.
#include <windows.h>
#include <evntrace.h>
#include <tdh.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <climits>
#include "scheduler_payload.hpp"

static const GUID ThreadProvider = {0x3d6fa8d1,0xfe05,0x11d0,{0x9d,0xda,0x00,0xc0,0x4f,0xd7,0xba,0x7c}};
static ULONG targetPid, targetTid, maximumLost = 0;
static unsigned long long count = 0;
static ULONG invalid = 0;

static bool property(PEVENT_RECORD event, const wchar_t* name, ULONG& value, ULONG size = sizeof(ULONG)) {
    PROPERTY_DATA_DESCRIPTOR descriptor{};
    descriptor.PropertyName = reinterpret_cast<ULONGLONG>(name);
    descriptor.ArrayIndex = ULONG_MAX;
    ULONG actual = 0;
    ULONG status = TdhGetPropertySize(event, 0, nullptr, 1, &descriptor, &actual);
    if (status != ERROR_SUCCESS || actual != size) {
        std::printf("D,%u,%u,1,%u,%u,%u\n", event->EventHeader.EventDescriptor.Opcode,
            event->EventHeader.EventDescriptor.Version, status, actual, size);
        return false;
    }
    value = 0;
    status = TdhGetProperty(event, 0, nullptr, 1, &descriptor, size, reinterpret_cast<PBYTE>(&value));
    if (status != ERROR_SUCCESS) std::printf("D,%u,%u,2,%u,%u,%u\n", event->EventHeader.EventDescriptor.Opcode,
        event->EventHeader.EventDescriptor.Version, status, actual, size);
    return status == ERROR_SUCCESS;
}

static void WINAPI receive(PEVENT_RECORD event) {
    if (invalid || !IsEqualGUID(event->EventHeader.ProviderId, ThreadProvider)) return;
    if (++count > 2000000) { invalid = 10; return; }
    ULONG first = 0, second = 0, state = 0;
    const auto stamp = event->EventHeader.TimeStamp.QuadPart;
    switch(event->EventHeader.EventDescriptor.Opcode) {
    case EVENT_TRACE_TYPE_START:
    case EVENT_TRACE_TYPE_END:
        if (!property(event, L"TThreadId", first)) { invalid = 11; return; }
        if (first != targetTid) return;
        if (!property(event, L"ProcessId", second)) { invalid = 12; return; }
        std::printf("L,%lld,%u,%u,%u\n", stamp, event->EventHeader.EventDescriptor.Opcode, second, first);
        break;
    case 36: { // CSwitch v5 has no TDH schema on the observed hosted image.
        SchedulerSwitch decoded{};
        if (!decode_scheduler_switch(event->EventHeader.EventDescriptor.Version,
                event->UserData, event->UserDataLength, decoded)) { invalid = 13; return; }
        first = decoded.next; second = decoded.previous; state = decoded.state;
        if (first != targetTid && second != targetTid) return;
        std::printf("S,%lld,%u,%u,%u\n", stamp, first, second, state);
        break;
    }
    case 50: // ReadyThread.
        if (!property(event, L"TThreadId", first)) { invalid = 11; return; }
        if (first == targetTid) std::printf("R,%lld,%u\n", stamp, first);
        break;
    default: break;
    }
}

static ULONG WINAPI buffer(PEVENT_TRACE_LOGFILEW log) {
    maximumLost = (std::max)(maximumLost, log->LogfileHeader.EventsLost);
    maximumLost = (std::max)(maximumLost, log->LogfileHeader.BuffersLost);
    return invalid ? FALSE : TRUE;
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 4) return 2;
    wchar_t* end = nullptr;
    targetPid = wcstoul(argv[2], &end, 10);
    if (!targetPid || *end) return 2;
    targetTid = wcstoul(argv[3], &end, 10);
    if (!targetTid || *end) return 2;
    EVENT_TRACE_LOGFILEW log{};
    log.LogFileName = argv[1];
    log.ProcessTraceMode = PROCESS_TRACE_MODE_EVENT_RECORD | PROCESS_TRACE_MODE_RAW_TIMESTAMP;
    log.EventRecordCallback = receive;
    log.BufferCallback = buffer;
    auto handle = OpenTraceW(&log);
    if (handle == INVALID_PROCESSTRACE_HANDLE) return 3;
    const auto header = log.LogfileHeader;
    const ULONG result = ProcessTrace(&handle, 1, nullptr, nullptr);
    const ULONG closed = CloseTrace(handle);
    std::printf("H,%u,%lld,%u,%u,%u,%u,%u,%llu\n", header.ReservedFlags,
        header.PerfFreq.QuadPart, header.EventsLost, header.BuffersLost, maximumLost,
        result, closed, count);
    if (std::fflush(stdout) != 0 || std::ferror(stdout)) return 5;
    if (invalid) return static_cast<int>(invalid);
    if (result != ERROR_SUCCESS || closed != ERROR_SUCCESS ||
        header.ReservedFlags != 1 || header.PerfFreq.QuadPart <= 0 ||
        header.EndTime.QuadPart == 0 || header.EventsLost || header.BuffersLost || maximumLost) return 4;
    return 0;
}
