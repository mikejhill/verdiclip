#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

// Field offsets and v2-or-later prefix contract are documented in Microsoft's
// PerfView CSwitchTraceData implementation, KernelTraceEventParser.cs:
// https://github.com/microsoft/perfview/blob/3d89ee8570a943290c4b517663f87493cfbaa35b/src/TraceEvent/Parsers/KernelTraceEventParser.cs
// Deliberately accept only v2 and observed v5; future versions fail closed.
struct SchedulerSwitch { std::uint32_t next, previous; std::uint8_t state; };
inline bool decode_scheduler_switch(unsigned version, const void* payload, std::size_t bytes, SchedulerSwitch& output) {
    if ((version != 2 && version != 5) || payload == nullptr || bytes < 24 || (version == 2 && bytes != 24)) return false;
    const auto* data = static_cast<const std::uint8_t*>(payload);
    std::memcpy(&output.next, data, 4);
    std::memcpy(&output.previous, data + 4, 4);
    output.state = data[14];
    return true;
}
