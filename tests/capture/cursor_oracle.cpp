// Test-only Desktop Duplication feasibility oracle. Never run on a user desktop.
// Build (Developer Command Prompt): cl /nologo /std:c++17 /EHsc /W4
//   tests\capture\cursor_oracle.cpp /Fe:cursor_oracle.exe
//   /link d3d11.lib dxgi.lib user32.lib gdi32.lib
// Protocol: `arm\n`, `observe\n`, or `quit\n`; one fixed JSON response per command.
// Arm BEFORE mutation. An armed observation requires a post-boundary DXGI pointer
// update; unchanged state without an update is unsupported, never preservation proof.
// Initial ready response is NOT evidence of visibility. Unsupported is fail-closed.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <cwchar>
using Microsoft::WRL::ComPtr;

namespace {
constexpr int W = 480, H = 360, R = 72;
constexpr COLORREF Background = RGB(37, 83, 121);
HWND fixture = nullptr;
COLORREF marker = RGB(201, 11, 17);
LRESULT CALLBACK Proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_PAINT) {
        PAINTSTRUCT p{}; HDC dc = BeginPaint(h, &p);
        RECT r{}; GetClientRect(h, &r);
        HBRUSH b = CreateSolidBrush(Background); FillRect(dc, &r, b); DeleteObject(b);
        r = {16, 16, 48, 48}; b = CreateSolidBrush(marker);
        FillRect(dc, &r, b); DeleteObject(b); EndPaint(h, &p); return 0;
    }
    // Do not call SetCursor while observing: that would interfere with the lease.
    if (m == WM_SETCURSOR) return TRUE;
    return DefWindowProcW(h, m, w, l);
}
void Reply(bool supported, const char* status, const char* reason, bool afterBoundary = false) {
    // All callers pass compile-time enumerations, never OS/user strings.
    std::printf("{\"supported\":%s,\"status\":\"%s\",\"reason\":\"%s\",\"pointer_update_after_boundary\":%s}\n",
                supported ? "true" : "false", status, reason, afterBoundary ? "true" : "false"); std::fflush(stdout);
}
bool Guard(const wchar_t* name, const wchar_t* expected) {
    wchar_t value[64]{}; DWORD n = GetEnvironmentVariableW(name, value, 64);
    return n && n < 64 && std::wcscmp(value, expected) == 0;
}
void Pump() {
    MSG m{}; unsigned n = 0;
    while (n++ < 128 && PeekMessageW(&m, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&m); DispatchMessageW(&m);
    }
}
// Conservative proof of visible COLOR pixels. Requiring an opaque contrasting
// pixel avoids assumptions about straight versus premultiplied partial alpha.
// Other shape formats are deliberately unsupported, rather than guessed.
bool ShapeContrasts(const DXGI_OUTDUPL_POINTER_SHAPE_INFO& shape,
                    const unsigned char* bytes, UINT required, size_t capacity) {
    if (shape.Type != DXGI_OUTDUPL_POINTER_SHAPE_TYPE_COLOR ||
        shape.Width == 0 || shape.Width > 64 || shape.Height == 0 || shape.Height > 64 ||
        shape.Pitch < shape.Width * 4 || required > capacity ||
        static_cast<uint64_t>(shape.Pitch) * shape.Height > required) return false;
    for (UINT y = 0; y < shape.Height; ++y) {
        for (UINT x = 0; x < shape.Width; ++x) {
            const unsigned char* p = bytes + static_cast<size_t>(y) * shape.Pitch + x * 4;
            if (p[3] == 255 && (p[0] != GetBValue(Background) ||
                p[1] != GetGValue(Background) || p[2] != GetRValue(Background))) return true;
        }
    }
    return false;
}
struct Oracle {
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<IDXGIOutputDuplication> duplication;
    ComPtr<ID3D11Texture2D> staging;
    RECT desktop{}, window{};
    POINT original{}, anchor{};
    HCURSOR previous = nullptr;
    bool moved = false, cursorSet = false, baseline = false, visible = false;
    bool pointerKnown = false, shapeKnown = false;
    int showCalls = 0, originalShowCount = 0;
    bool retired = false, cleanupConfirmed = false;
    unsigned sequence = 0;
    LARGE_INTEGER armedAt{};
    bool armed = false;
    DXGI_OUTDUPL_POINTER_SHAPE_INFO shape{};
    POINT pointer{};
    bool Retire() {
        if (retired) return cleanupConfirmed;
        retired = true;
        bool ok = true;
        if (moved) {
            POINT restored{};
            ok = SetCursorPos(original.x, original.y) != FALSE;
            ok = GetCursorPos(&restored) && restored.x == original.x && restored.y == original.y && ok;
            moved = false;
        }
        if (cursorSet) {
            SetCursor(previous);
            // GetCursor checks only this fixture thread's cursor handle, not presentation.
            ok = GetCursor() == previous && ok;
            cursorSet = false;
        }
        int finalCount = originalShowCount;
        while (showCalls > 0) { finalCount = ShowCursor(FALSE); --showCalls; }
        ok = finalCount == originalShowCount && ok;
        if (fixture) {
            ok = DestroyWindow(fixture) != FALSE && ok;
            if (IsWindow(fixture)) ok = false;
            fixture = nullptr;
        }
        staging.Reset(); duplication.Reset(); context.Reset(); device.Reset();
        // This attests only fixture cleanup, never any prior/global magnification state.
        cleanupConfirmed = ok;
        return cleanupConfirmed;
    }
    ~Oracle() { Retire(); }
    bool Init() {
        // DPI awareness avoids interpreting virtualized coordinates as surface pixels.
        if (!SetProcessDPIAware()) return false;
        if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                  D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
                  &device, nullptr, &context))) return false;
        ComPtr<IDXGIDevice> dxDevice; ComPtr<IDXGIAdapter> adapter;
        if (FAILED(device.As(&dxDevice)) || FAILED(dxDevice->GetAdapter(&adapter))) return false;
        ComPtr<IDXGIOutput> output;
        for (UINT i = 0; i < 16; ++i) {
            ComPtr<IDXGIOutput> candidate;
            if (FAILED(adapter->EnumOutputs(i, &candidate))) break;
            DXGI_OUTPUT_DESC d{};
            if (SUCCEEDED(candidate->GetDesc(&d)) && d.AttachedToDesktop &&
                d.DesktopCoordinates.left == 0 && d.DesktopCoordinates.top == 0 &&
                d.Rotation == DXGI_MODE_ROTATION_IDENTITY) {
                desktop = d.DesktopCoordinates; output = candidate; break;
            }
        }
        if (!output || desktop.right < W + 80 || desktop.bottom < H + 80) return false;
        ComPtr<IDXGIOutput1> output1;
        if (FAILED(output.As(&output1)) || FAILED(output1->DuplicateOutput(device.Get(), &duplication))) return false;
        WNDCLASSW wc{}; wc.lpfnWndProc = Proc; wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = L"VerdiclipCursorOracleFixture";
        if (!RegisterClassW(&wc)) return false;
        fixture = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, wc.lpszClassName,
                  L"Cursor feasibility fixture", WS_POPUP, 40, 40, W, H,
                  nullptr, nullptr, wc.hInstance, nullptr);
        if (!fixture || !GetCursorPos(&original)) return false;
        ShowWindow(fixture, SW_SHOWNOACTIVATE); UpdateWindow(fixture);
        if (!GetWindowRect(fixture, &window)) return false;
        anchor = {window.left + W / 2, window.top + H / 2};
        previous = SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32512))); cursorSet = true;
        // Bounded normalization, exactly balanced during teardown.
        bool shown = false;
        for (int i = 0; i < 16; ++i) {
            const int count = ShowCursor(TRUE);
            if (!showCalls) originalShowCount = count - 1;
            ++showCalls;
            if (count >= 0) { shown = true; break; }
        }
        if (!shown) return false;
        moved = true;
        if (!SetCursorPos(anchor.x, anchor.y)) return false;
        Pump(); return true;
    }
    bool Owned() {
        RECT r{}; POINT current{};
        if (!GetCursorPos(&current) || current.x != anchor.x || current.y != anchor.y) return false;
        if (!IsWindowVisible(fixture) || !GetWindowRect(fixture, &r) ||
            !EqualRect(&r, &window)) return false;
        // Check every sampled surface point against the topmost HWND, not just corners.
        for (int y = anchor.y - R; y <= anchor.y + R; ++y)
            for (int x = anchor.x - R; x <= anchor.x + R; ++x)
                if (WindowFromPoint(POINT{x, y}) != fixture) return false;
        return WindowFromPoint(POINT{window.left + 24, window.top + 24}) == fixture;
    }
    bool Pixel(const D3D11_MAPPED_SUBRESOURCE& map, int x, int y, COLORREF color) {
        const auto* p = static_cast<const unsigned char*>(map.pData) +
                        static_cast<size_t>(y) * map.RowPitch + static_cast<size_t>(x) * 4;
        return p[0] == GetBValue(color) && p[1] == GetGValue(color) && p[2] == GetRValue(color);
    }
    void Arm() {
        if (armed) { std::printf("{\"ok\":false}\n"); std::fflush(stdout); return; }
        if (!QueryPerformanceCounter(&armedAt) || armedAt.QuadPart <= 0) {
            std::printf("{\"ok\":false}\n"); std::fflush(stdout); return;
        }
        armed = true;
        std::printf("{\"ok\":true}\n"); std::fflush(stdout);
    }
    void Observe() {
        const bool requireUpdate = armed;
        const LONGLONG boundary = armedAt.QuadPart;
        armed = false; // Consume even if this observation cannot complete.
        bool afterBoundary = false;
        if (!Owned()) { Reply(false, "unsupported", "ownership"); return; }
        // An independently painted marker makes a fresh desktop presentation observable.
        ++sequence;
        marker = RGB(128 + (sequence & 63), 11 + ((sequence >> 6) & 63), 17);
        RECT mark{16,16,48,48}; InvalidateRect(fixture, &mark, FALSE); UpdateWindow(fixture);
        const ULONGLONG end = GetTickCount64() + 2400;
        for (unsigned attempts = 0; attempts < 40 && GetTickCount64() < end; ++attempts) {
            Pump(); DXGI_OUTDUPL_FRAME_INFO info{}; ComPtr<IDXGIResource> resource;
            HRESULT hr = duplication->AcquireNextFrame(50, &info, &resource);
            if (hr == DXGI_ERROR_WAIT_TIMEOUT) continue;
            if (FAILED(hr)) { Reply(false, "unsupported", "duplication"); return; }
            struct Release { IDXGIOutputDuplication* d; ~Release() { d->ReleaseFrame(); } } release{duplication.Get()};
            // DXGI carries deltas: zero timestamp means KEEP the last explicit state,
            // not a new invisible pointer. Hidden is impossible before an explicit
            // invisible update following the established visible baseline.
            if (info.LastMouseUpdateTime.QuadPart) {
                if (requireUpdate && info.LastMouseUpdateTime.QuadPart >= boundary)
                    afterBoundary = true;
                pointerKnown = true; visible = info.PointerPosition.Visible != FALSE;
                if (visible) pointer = info.PointerPosition.Position;
            }
            if (info.PointerShapeBufferSize) {
                // Fixed bounded storage, no retained or emitted image data.
                unsigned char bytes[65536]{}; UINT required = 0;
                if (info.PointerShapeBufferSize > sizeof(bytes) ||
                    FAILED(duplication->GetFramePointerShape(static_cast<UINT>(sizeof(bytes)), bytes, &required, &shape))) {
                    Reply(false, "unsupported", "pointer_shape"); return;
                }
                shapeKnown = ShapeContrasts(shape, bytes, required, sizeof(bytes));
                SecureZeroMemory(bytes, sizeof(bytes));
            }
            ComPtr<ID3D11Texture2D> texture;
            if (FAILED(resource.As(&texture))) { Reply(false, "unsupported", "surface"); return; }
            D3D11_TEXTURE2D_DESC desc{}; texture->GetDesc(&desc);
            if (desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM || desc.Width != static_cast<UINT>(desktop.right) ||
                desc.Height != static_cast<UINT>(desktop.bottom)) { Reply(false, "unsupported", "surface_format"); return; }
            if (desc.Width == 0 || desc.Height == 0 || desc.Width > 8192 || desc.Height > 8192 ||
                static_cast<uint64_t>(desc.Width) * desc.Height > 32000000 ||
                desc.MipLevels != 1 || desc.ArraySize != 1 || desc.SampleDesc.Count != 1) {
                Reply(false, "unsupported", "surface_bounds"); return;
            }
            if (!staging) {
                desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0;
                desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ; desc.MiscFlags = 0;
                if (FAILED(device->CreateTexture2D(&desc, nullptr, &staging))) {
                    Reply(false, "unsupported", "surface"); return;
                }
            }
            context->CopyResource(staging.Get(), texture.Get());
            D3D11_MAPPED_SUBRESOURCE map{};
            const ULONGLONG mapEnd = GetTickCount64() + 500;
            HRESULT mapped = DXGI_ERROR_WAS_STILL_DRAWING;
            do {
                mapped = context->Map(staging.Get(), 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &map);
                if (mapped != DXGI_ERROR_WAS_STILL_DRAWING) break;
                Sleep(5);
            } while (GetTickCount64() < mapEnd);
            if (FAILED(mapped)) {
                Reply(false, "unsupported", "surface"); return;
            }
            bool fresh = Pixel(map, window.left + 24, window.top + 24, marker);
            bool clear = true;
            for (int y = anchor.y - R; y <= anchor.y + R && clear; ++y)
                for (int x = anchor.x - R; x <= anchor.x + R; ++x)
                    if (!Pixel(map, x, y, Background)) { clear = false; break; }
            context->Unmap(staging.Get(), 0);
            if (!fresh) continue;
            // Never let a pre-operation cached state prove an armed result. A
            // qualifying explicit update can precede this fresh surface frame.
            if (requireUpdate && !afterBoundary) continue;
            if (!Owned()) { Reply(false, "unsupported", "ownership"); return; }
            if (!clear) { Reply(false, "unsupported", "surface_not_background"); return; }
            if (!pointerKnown || !shapeKnown) { Reply(false, "unsupported", "pointer_plane_unproven"); return; }
            const LONG ph = static_cast<LONG>(shape.Height);
            if (pointer.x < anchor.x - R || pointer.y < anchor.y - R ||
                pointer.x + static_cast<LONG>(shape.Width) > anchor.x + R || pointer.y + ph > anchor.y + R) {
                Reply(false, "unsupported", "pointer_outside_fixture"); return;
            }
            if (visible) { baseline = true; Reply(true, "visible", "separate_plane_clear_surface", requireUpdate && afterBoundary); return; }
            if (baseline) { Reply(true, "hidden", "absent_plane_clear_surface", requireUpdate && afterBoundary); return; }
            Reply(false, "unsupported", "visible_baseline_required"); return;
        }
        if (requireUpdate && !afterBoundary)
            Reply(false, "unsupported", "pointer_update_timeout");
        else Reply(false, "unsupported", "fresh_frame_timeout");
    }
};
}
int main() {
    if (!Guard(L"GITHUB_ACTIONS", L"true") || !Guard(L"RUNNER_ENVIRONMENT", L"github-hosted") ||
        !Guard(L"VERDICLIP_CURSOR_FEASIBILITY", L"1")) {
        Reply(false, "unsupported", "host_guard"); return 2;
    }
    Oracle oracle;
    if (!oracle.Init()) { Reply(false, "unsupported", "initialization"); return 3; }
    Reply(false, "unsupported", "ready_for_baseline");
    // Pipe input only: never enter an unbounded blocking console/read operation.
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (GetFileType(input) != FILE_TYPE_PIPE) {
        Reply(false, "unsupported", "input_pipe_required"); return 4;
    }
    char line[32]{}; unsigned used = 0, commands = 0;
    const ULONGLONG lifetimeEnd = GetTickCount64() + 100000;
    ULONGLONG idleEnd = GetTickCount64() + 15000;
    while (commands < 48 && GetTickCount64() < lifetimeEnd && GetTickCount64() < idleEnd) {
        Pump(); DWORD available = 0;
        if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) return 0;
        if (!available) { Sleep(10); continue; }
        char c = 0; DWORD read = 0;
        if (!ReadFile(input, &c, 1, &read, nullptr) || read != 1) return 0;
        if (c == '\r') continue;
        if (c != '\n') {
            if (used + 1 >= sizeof(line)) { Reply(false, "unsupported", "invalid_command"); return 4; }
            line[used++] = c; continue;
        }
        line[used] = 0; ++commands;
        if (std::strcmp(line, "quit") == 0) {
            const bool cleaned = oracle.Retire();
            std::printf("{\"supported\":false,\"status\":\"unsupported\",\"reason\":\"stopped\",\"cleanup_confirmed\":%s}\n",
                        cleaned ? "true" : "false");
            std::fflush(stdout); return cleaned ? 0 : 5;
        }
        if (std::strcmp(line, "arm") == 0) oracle.Arm();
        else if (std::strcmp(line, "observe") == 0) oracle.Observe();
        else { Reply(false, "unsupported", "invalid_command"); return 4; }
        used = 0; idleEnd = GetTickCount64() + 15000;
    }
    Reply(false, "unsupported", "protocol_timeout"); return 0;
}
