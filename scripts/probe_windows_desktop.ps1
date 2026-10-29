#Requires -Version 7.0
# Read-only prerequisites, not native UI/input qualification. No app is launched.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
    $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch' -or
    $env:GITHUB_REPOSITORY -ne 'mikejhill/verdiclip') {
    throw 'This probe requires an explicit dispatch on standard hosted Windows x64.'
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

public static class ReadOnlyDesktopProbe {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WtsLevelOne {
        public uint SessionId;
        public int State, Flags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string Station;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 21)] public string User;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 18)] public string Domain;
        public long Logon, Connect, Disconnect, LastInput, Current;
        public uint IncomingBytes, OutgoingBytes, IncomingFrames, OutgoingFrames;
        public uint IncomingCompressedBytes, OutgoingCompressedBytes;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct WtsInfo { public uint Level; public WtsLevelOne Data; }
    [DllImport("wtsapi32.dll", SetLastError = true, ExactSpelling = true)]
    private static extern bool WTSQuerySessionInformationW(
        IntPtr server, uint session, int kind, out IntPtr buffer, out uint bytes);
    [DllImport("wtsapi32.dll")]
    private static extern void WTSFreeMemory(IntPtr buffer);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr GetProcessWindowStation();
    [DllImport("user32.dll", SetLastError = true, ExactSpelling = true)]
    private static extern bool GetUserObjectInformationW(
        IntPtr handle, int kind, IntPtr buffer, uint length, out uint needed);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")]
    private static extern uint GetDpiForSystem();
    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo { public uint Size; public Rect Bounds, Work; public uint Flags; }
    private delegate bool MonitorCallback(IntPtr monitor, IntPtr hdc, ref Rect rect, IntPtr data);
    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumDisplayMonitors(
        IntPtr hdc, IntPtr clip, MonitorCallback callback, IntPtr data);
    [DllImport("user32.dll", SetLastError = true, ExactSpelling = true)]
    private static extern bool GetMonitorInfoW(IntPtr monitor, ref MonitorInfo info);
    [DllImport("shcore.dll")]
    private static extern int GetScaleFactorForMonitor(IntPtr monitor, out int scale);
    [DllImport("user32.dll")]
    private static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")]
    private static extern int GetAwarenessFromDpiAwarenessContext(IntPtr context);

    private static int[] Bounds(Rect value) {
        return new int[] {value.Left, value.Top, value.Right, value.Bottom};
    }

    public static Dictionary<string, object> Read() {
        var errors = new List<string>();
        int session = Process.GetCurrentProcess().SessionId;
        var result = new Dictionary<string, object> {
            {"process_session_id", session},
            {"process_is_64_bit", Environment.Is64BitProcess},
            {"dotnet_user_interactive", Environment.UserInteractive},
            {"wts_state", null}, {"wts_flags", null},
            {"window_station_visible", null}, {"input_desktop_is_default", null},
            {"foreground_present", GetForegroundWindow() != IntPtr.Zero},
            {"virtual_x", GetSystemMetrics(76)}, {"virtual_y", GetSystemMetrics(77)},
            {"virtual_width", GetSystemMetrics(78)}, {"virtual_height", GetSystemMetrics(79)},
            {"visible_monitor_count", GetSystemMetrics(80)},
            {"system_dpi", GetDpiForSystem()}
        };
        var monitors = new List<Dictionary<string, object>>();
        var displayErrors = new List<string>();
        MonitorCallback callback = delegate(IntPtr monitor, IntPtr hdc, ref Rect rect, IntPtr extra) {
            try {
                var info = new MonitorInfo {Size = (uint)Marshal.SizeOf<MonitorInfo>()};
                if (!GetMonitorInfoW(monitor, ref info)) {
                    displayErrors.Add("GetMonitorInfoW:" + Marshal.GetLastWin32Error());
                    return false;
                }
                int scale;
                int status = GetScaleFactorForMonitor(monitor, out scale);
                // The API supplies a fallback even on failure: do not report it as evidence.
                if (status != 0) displayErrors.Add("GetScaleFactorForMonitor:" + status);
                monitors.Add(new Dictionary<string, object> {
                    {"ordinal", monitors.Count + 1}, {"bounds", Bounds(info.Bounds)},
                    {"work_area", Bounds(info.Work)}, {"primary", (info.Flags & 1) != 0},
                    {"scale_percent", status == 0 ? (object)scale : null}
                });
                return true;
            } catch (Exception error) {
                displayErrors.Add("MonitorQuery:" + error.GetType().Name);
                return false;
            }
        };
        if (!EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, callback, IntPtr.Zero))
            displayErrors.Add("EnumDisplayMonitors:" + Marshal.GetLastWin32Error());
        GC.KeepAlive(callback);
        result["monitors"] = monitors.ToArray();
        result["monitor_count"] = monitors.Count;
        result["display_query_errors"] = displayErrors.ToArray();
        result["display_inventory_complete"] = monitors.Count > 0 && displayErrors.Count == 0;
        result["thread_dpi_awareness"] = GetAwarenessFromDpiAwarenessContext(GetThreadDpiAwarenessContext());
        result["coordinate_context"] = "query caller; no DPI awareness or display configuration changed";
        IntPtr data;
        uint size;
        if (!WTSQuerySessionInformationW(IntPtr.Zero, (uint)session, 25, out data, out size)) {
            errors.Add("WTSQuerySessionInformationW:" + Marshal.GetLastWin32Error());
        } else {
            try {
                if (size < Marshal.SizeOf<WtsInfo>()) errors.Add("WTSINFOEX:short_buffer");
                else {
                    var info = Marshal.PtrToStructure<WtsInfo>(data);
                    if (info.Level != 1 || info.Data.SessionId != session)
                        errors.Add("WTSINFOEX:unexpected_level_or_session");
                    else { result["wts_state"] = info.Data.State; result["wts_flags"] = info.Data.Flags; }
                }
            } finally { WTSFreeMemory(data); }
        }
        // The station handle is borrowed; only our allocated buffer is freed.
        IntPtr station = GetProcessWindowStation();
        IntPtr flags = Marshal.AllocHGlobal(12); // USEROBJECTFLAGS: two BOOLs, DWORD.
        try {
            uint needed;
            if (station == IntPtr.Zero || !GetUserObjectInformationW(station, 1, flags, 12, out needed))
                errors.Add("WindowStationFlags:" + Marshal.GetLastWin32Error());
            else result["window_station_visible"] = (Marshal.ReadInt32(flags, 8) & 1) != 0;
        } finally { Marshal.FreeHGlobal(flags); }
        // DESKTOP_READOBJECTS only. No switch, activation, unlocking or creation.
        IntPtr desktop = OpenInputDesktop(0, false, 1);
        if (desktop == IntPtr.Zero) errors.Add("OpenInputDesktop:" + Marshal.GetLastWin32Error());
        else {
            try {
                uint needed;
                GetUserObjectInformationW(desktop, 2, IntPtr.Zero, 0, out needed); // UOI_NAME.
                if (needed < 2 || needed > 65536) errors.Add("InputDesktopName:invalid_size");
                else {
                    IntPtr name = Marshal.AllocHGlobal((int)needed);
                    try {
                        if (!GetUserObjectInformationW(desktop, 2, name, needed, out needed))
                            errors.Add("InputDesktopName:" + Marshal.GetLastWin32Error());
                        else result["input_desktop_is_default"] = string.Equals(
                            Marshal.PtrToStringUni(name), "Default", StringComparison.OrdinalIgnoreCase);
                    } finally { Marshal.FreeHGlobal(name); }
                }
            } finally {
                if (!CloseDesktop(desktop)) errors.Add("CloseDesktop:" + Marshal.GetLastWin32Error());
            }
        }
        result["query_errors"] = errors.ToArray();
        return result;
    }
}
'@

$observed = [ReadOnlyDesktopProbe]::Read()
$available = $observed.process_is_64_bit -and $observed.process_session_id -gt 0 -and
    $observed.dotnet_user_interactive -and $observed.wts_state -eq 0 -and
    $observed.wts_flags -eq 1 -and $observed.window_station_visible -eq $true -and
    $observed.input_desktop_is_default -eq $true -and
    $observed.virtual_width -gt 0 -and $observed.virtual_height -gt 0 -and
    $observed.query_errors.Count -eq 0
$report = [ordered]@{
    schema = 1
    kind = 'read-only-windows-desktop-prerequisites'
    source_sha = $env:GITHUB_SHA
    runner_image = $env:ImageOS
    image_version = $env:ImageVersion
    os_version = [Environment]::OSVersion.Version.ToString()
    powershell = $PSVersionTable.PSVersion.ToString()
    read_only_prerequisites_observed = [bool]$available
    prerequisite_scope = 'session, station and input desktop; display inventory is separate'
    native_ui_verified = $false
    input_generated = $false
    windows_created = $false
    hotkeys_registered = $false
    migration_adequacy_verified = $false
    multi_monitor_profile_verified = $false
    observed = $observed
}
$json = $report | ConvertTo-Json -Depth 5
Write-Output $json
if ($env:GITHUB_STEP_SUMMARY) {
    @('### Read-only Windows desktop prerequisites', '', '```json', $json, '```',
        'No native UI, input, UIA or hotkey qualification was performed.') -join "`n" |
        Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Encoding utf8
}
if (-not $available) { exit 2 }
