#include "scheduler_payload.hpp"
#include <array>
#include <cassert>
// Inactive alternatives deliberately differ; selection must follow the flag only.
struct FakeEvent {
    struct { unsigned Flags; } EventHeader;
    struct { unsigned short ProcessorIndex; unsigned char ProcessorNumber; } BufferContext;
};
int main() {
    constexpr unsigned processorIndexFlag = 0x0200;
    const FakeEvent indexed{{processorIndexFlag | 0x0010}, {65535, 7}};
    const auto indexCpu = scheduler_logging_cpu(indexed, processorIndexFlag);
    assert(indexCpu.indexed == 1 && indexCpu.value == 65535);
    const FakeEvent numbered{{0x0010}, {65000, 255}};
    const auto numberCpu = scheduler_logging_cpu(numbered, processorIndexFlag);
    assert(numberCpu.indexed == 0 && numberCpu.value == 255);
    const FakeEvent zero{{0}, {65000, 0}};
    const auto zeroCpu = scheduler_logging_cpu(zero, processorIndexFlag);
    assert(zeroCpu.indexed == 0 && zeroCpu.value == 0);

    std::array<std::uint8_t, 32> bytes{};
    bytes[0]=0x78; bytes[1]=0x56; bytes[2]=0x34; bytes[3]=0x12;
    bytes[4]=0xff; bytes[5]=0xee; bytes[6]=0xdd; bytes[7]=0xcc;
    bytes[14]=5; bytes[15]=99;
    SchedulerSwitch decoded{};
    assert(decode_scheduler_switch(5,bytes.data(),bytes.size(),decoded));
    assert(decoded.next==0x12345678 && decoded.previous==0xccddeeff && decoded.state==5);
    assert(decode_scheduler_switch(2,bytes.data(),24,decoded));
    assert(!decode_scheduler_switch(2,bytes.data(),32,decoded));
    assert(!decode_scheduler_switch(6,bytes.data(),32,decoded));
    assert(!decode_scheduler_switch(5,bytes.data(),23,decoded));
    assert(!decode_scheduler_switch(5,nullptr,32,decoded));
    return 0;
}
