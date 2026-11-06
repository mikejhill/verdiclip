// Test-only, root-only UIA client. No traversal, patterns, or provider text.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Threading;
namespace VerdiClip.Automation {
public static class RootProbeGuard {
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll")] private static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] private static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    public static void Require(bool condition) {
        if (!condition) throw new InvalidOperationException("unsupported_or_changed");
    }
    public static void RequireMta() {
        Require(Thread.CurrentThread.GetApartmentState() == ApartmentState.MTA);
    }
    public static void RequireNativeOnlyProcess() {
        foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies()) {
            string name = assembly.GetName().Name;
            Require(!String.Equals(name, "UIAutomationClient", StringComparison.OrdinalIgnoreCase) &&
                !String.Equals(name, "UIAutomationTypes", StringComparison.OrdinalIgnoreCase));
        }
    }
    // Boundary sample only: this is not continuous foreground monitoring.
    public static void RequireForeground(long expected) {
        Require(expected > 0 && GetForegroundWindow() == new IntPtr(expected));
    }
    public static void RequireOwnedWindow(long handle, int processId, long startedTicks, long foreground) {
        RequireMta();
        Require(handle > 0 && processId > 0 && startedTicks > 0 &&
            startedTicks <= DateTime.MaxValue.Ticks && foreground > 0);
        IntPtr window = new IntPtr(handle);
        Require(CaptureDesktop.Interactive() && CaptureDesktop.IsWindowVisible(window) &&
            !CaptureDesktop.IsIconic(window));
        uint owner;
        Require(GetWindowThreadProcessId(window, out owner) != 0 && owner == (uint)processId);
        using (var process = Process.GetProcessById(processId)) {
            Require(process.StartTime.ToUniversalTime().Ticks == startedTicks);
        }
        Require(GetForegroundWindow() == new IntPtr(foreground));
    }
    public static void RequirePhysicalPixels() {
        Require(AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), new IntPtr(-4)));
    }
    public static void RestoreDpi(IntPtr previous) {
        RestoreDpiWithCalls(previous, CaptureDesktop.SetThreadDpiAwarenessContext,
            GetThreadDpiAwarenessContext, AreDpiAwarenessContextsEqual, RootProbeTiming.Phase);
    }
    // Pure injected boundary for failure-policy tests; the live wrapper uses only
    // the original APIs once each. No retry or canonicalization of opaque tokens.
    public static void RestoreDpiWithCalls(IntPtr previous, Func<IntPtr,IntPtr> set,
        Func<IntPtr> get, Func<IntPtr,IntPtr,bool> equal, Action<string> phase) {
        phase("probe_restore_previous");
        Require(previous != IntPtr.Zero);
        phase("probe_restore_set");
        IntPtr replaced = set(previous);
        Require(replaced != IntPtr.Zero);
        phase("probe_restore_set_succeeded");
        IntPtr current = get();
        phase("probe_restore_compare");
        Require(equal(current, previous));
        phase("probe_restore_verified");
    }
    public static void RequireRuntime(int[] runtime) {
        Require(runtime != null && runtime.Length >= 1 && runtime.Length <= 64);
    }
    // Injectable pure release policy lets tests cover failure and alias handling
    // without creating a native client or calling any desktop API.
    public static void ReleaseOwned(object[] values, Func<object, bool> isCom, Func<object, int> release) {
        var failures = new List<Exception>();
        for (int index = values.Length - 1; index >= 0; index--) {
            object value = values[index];
            if (value == null) continue;
            bool duplicate = false;
            for (int later = index + 1; later < values.Length; later++)
                if (Object.ReferenceEquals(value, values[later])) duplicate = true;
            if (duplicate) continue;
            try {
                if (isCom(value)) Require(release(value) == 0);
            } catch (Exception error) { failures.Add(error); }
        }
        if (failures.Count == 1) ExceptionDispatchInfo.Capture(failures[0]).Throw();
        if (failures.Count > 1) throw new AggregateException(failures);
    }
}
public static class RootProbeTiming {
    public static long? Start() {
        try { return Stopwatch.GetTimestamp(); } catch { return null; }
    }
    public static long? Finish(long? start) {
        if (!start.HasValue) return null;
        try {
            double elapsed = Math.Floor((Stopwatch.GetTimestamp() - start.Value) *
                1000000.0 / Stopwatch.Frequency);
            if (elapsed >= 0 && elapsed <= 999999999) return (long)elapsed;
        } catch { }
        return null;
    }
    public static void RootRecord(string requestId, long? duration) {
        try {
            if (duration.HasValue && duration.Value >= 0 && duration.Value <= 999999999 &&
                requestId != null && System.Text.RegularExpressions.Regex.IsMatch(requestId, "\\A[a-f0-9]{32}\\z"))
                Console.Error.WriteLine("VERDICLIP_UIA_ROOT_US=" + requestId + ":" +
                    duration.Value.ToString(CultureInfo.InvariantCulture));
        } catch { } // Optional timing cannot replace an operation's exception.
    }
    public static void Phase(string phase) {
        try { Console.Error.WriteLine("VERDICLIP_UIA_PHASE=" + phase); } catch { }
    }
}
public sealed class RootProbeSession {
    private object instance;
    private IAutomation2 automation;
    private IElement root;
    private object processValue, windowValue;
    private bool released;
    public long? ConstructUs, RootUs, ValidateUs, ReleaseUs;
    public void Construct() {
        RootProbeGuard.RequireMta();
        RootProbeGuard.RequireNativeOnlyProcess();
        RootProbeGuard.Require(instance == null && !released);
        RootProbeTiming.Phase("nofocus_constructing");
        long? started = RootProbeTiming.Start();
        var type = Type.GetTypeFromCLSID(new Guid("e22ad333-b25f-460c-83d0-0581107395c9"), true); // CUIAutomation8
        instance = Activator.CreateInstance(type); // Own before cast or validation.
        RootProbeGuard.Require(instance != null && Marshal.IsComObject(instance));
        automation = (IAutomation2)instance;
        automation.put_AutoSetFocus(0); // A per-client property, never system policy.
        int enabled;
        automation.get_AutoSetFocus(out enabled);
        RootProbeGuard.Require(enabled == 0);
        ConstructUs = RootProbeTiming.Finish(started);
        RootProbeTiming.Phase("nofocus_constructed");
    }
    public void Acquire(long handle, string requestId) {
        RootProbeGuard.RequireMta();
        RootProbeGuard.Require(automation != null && root == null && !released);
        RootProbeTiming.Phase("inspect_root");
        IntPtr window = new IntPtr(handle);
        long? started = RootProbeTiming.Start();
        automation.ElementFromHandle(window, out root); // Own the out field immediately.
        RootUs = RootProbeTiming.Finish(started);
        RootProbeTiming.RootRecord(requestId, RootUs);
    }
    public void Validate(int expectedProcess, long expectedHandle) {
        RootProbeGuard.RequireMta();
        long? started = RootProbeTiming.Start();
        RootProbeGuard.Require(root != null && Marshal.IsComObject(root) && !released);
        RootProbeTiming.Phase("inspect_process");
        root.GetCurrentPropertyValue(30002, out processValue); // UIA_ProcessIdPropertyId
        RootProbeGuard.Require(processValue is int && (int)processValue == expectedProcess);
        root.GetCurrentPropertyValue(30020, out windowValue); // UIA_NativeWindowHandlePropertyId
        RootProbeGuard.Require(windowValue is int && (long)(int)windowValue == expectedHandle);
        RootProbeTiming.Phase("inspect_root_runtime");
        int[] runtime;
        root.GetRuntimeId(out runtime);
        RootProbeGuard.RequireRuntime(runtime);
        int enabled;
        automation.get_AutoSetFocus(out enabled);
        RootProbeGuard.Require(enabled == 0);
        RootProbeGuard.RequireNativeOnlyProcess();
        ValidateUs = RootProbeTiming.Finish(started);
    }
    public void Release() {
        RootProbeGuard.Require(!released);
        released = true; // Never retry a partially completed release.
        long? started = RootProbeTiming.Start();
        object[] owned = { instance, root, processValue, windowValue };
        instance = null; automation = null; root = null; processValue = null; windowValue = null;
        RootProbeGuard.ReleaseOwned(owned, Marshal.IsComObject, Marshal.ReleaseComObject);
        ReleaseUs = RootProbeTiming.Finish(started);
    }
}
}
