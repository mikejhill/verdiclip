// One helper-owned native UIA client. AutoSetFocus is NOT a system setting.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Runtime.InteropServices;
using System.Runtime.ExceptionServices;
namespace VerdiClip.Automation {
public sealed class ForegroundGuard {
    private readonly Func<IntPtr> read;
    private readonly IntPtr original;
    public ForegroundGuard(Func<IntPtr> reader) : this(reader, reader()) {}
    public ForegroundGuard(Func<IntPtr> reader, IntPtr expected) {
        read = reader;
        original = expected;
        if (original == IntPtr.Zero) throw new InvalidOperationException("native_scroll_foreground_unavailable");
        EnsureUnchanged();
    }
    public void EnsureUnchanged() {
        if (read() != original) throw new InvalidOperationException("native_scroll_foreground_changed");
    }
}
public sealed class ScrollRequest {
    public long Handle, StartedTicks;
    public int ProcessId;
    public string RootRuntime, Runtime;
    public double[] Bounds;
    public double Position, ViewSize, Destination;
}
public sealed class ScrollTarget {
    public long Handle, StartedTicks;
    public int ProcessId;
    public string RootRuntime, Runtime;
    public double[] Bounds;
    public double Position, ViewSize;
    public bool Enabled, Offscreen, Scrollable;
    internal IScroll Pattern;
}
public interface IScrollClient : IDisposable {
    bool AutoSetFocus { get; set; }
    ScrollTarget Resolve(ScrollRequest request);
    void Move(ScrollTarget target, double destination);
}
internal static class Phase {
    internal static void Mark(string phase) {
        try { Console.Error.WriteLine("VERDICLIP_UIA_PHASE=" + phase); }
        catch (IOException) { /* Optional diagnostics must not alter an operation. */ }
        catch (ObjectDisposedException) { /* The parent may already be retiring. */ }
    }
}
public static class NoFocusScroll {
    private static bool Finite(double value) { return !Double.IsNaN(value) && !Double.IsInfinity(value); }
    private static void Require(bool valid) { if (!valid) throw new InvalidOperationException("native_scroll_identity_or_policy_changed"); }
    public static void Execute(IScrollClient client, ScrollRequest request, Func<bool> permitMutation) {
        if (client == null || request == null || permitMutation == null) throw new ArgumentNullException();
        Require(request.Handle != 0 && request.ProcessId > 0 && request.StartedTicks > 0);
        Require(!String.IsNullOrEmpty(request.Runtime) && !String.IsNullOrEmpty(request.RootRuntime));
        Require(request.Bounds != null && request.Bounds.Length == 4);
        foreach (double value in request.Bounds) Require(Finite(value) && value == Math.Round(value));
        Require(request.Bounds[2] > 0 && request.Bounds[3] > 0);
        Require(Finite(request.Destination) && request.Destination >= 0 && request.Destination <= 100);
        Require(Finite(request.Position) && request.Position >= 0 && request.Position <= 100);
        Require(Finite(request.ViewSize) && request.ViewSize > 0 && request.ViewSize < 100);
        client.AutoSetFocus = false;
        Require(!client.AutoSetFocus);
        Phase.Mark("nofocus_policy");
        ScrollTarget target = client.Resolve(request);
        Phase.Mark("nofocus_resolved");
        Require(target != null && target.Handle == request.Handle && target.ProcessId == request.ProcessId);
        Require(target.StartedTicks == request.StartedTicks && target.RootRuntime == request.RootRuntime && target.Runtime == request.Runtime);
        Require(target.Bounds != null && target.Bounds.Length == 4);
        for (int i=0; i<4; i++) Require(Finite(target.Bounds[i]) && target.Bounds[i] == request.Bounds[i]);
        Require(target.Enabled && !target.Offscreen && target.Scrollable);
        Require(Finite(target.Position) && target.Position >= 0 && target.Position <= 100 && Math.Abs(target.Position-request.Position) <= 0.001);
        Require(Finite(target.ViewSize) && target.ViewSize > 0 && target.ViewSize < 100 && Math.Abs(target.ViewSize-request.ViewSize) <= 0.001);
        Require(!client.AutoSetFocus); // Do not mutate if this client no longer honors the policy.
        Require(permitMutation()); // Repeat the original full-area occlusion/identity guard after resolution.
        Require(!client.AutoSetFocus); // The delegate must not change this client policy.
        Phase.Mark("nofocus_guarded");
        client.Move(target, request.Destination);
        Phase.Mark("nofocus_moved");
    }
    public static void ExecuteOwned(IScrollClient client, ScrollRequest request, Func<bool> permitMutation) {
        Exception operationFailure = null;
        try { Execute(client, request, permitMutation); }
        catch (Exception error) { operationFailure = error; }
        try { client.Dispose(); }
        catch (Exception cleanupFailure) {
            if (operationFailure != null) throw new AggregateException("native_scroll_operation_and_cleanup_failed", operationFailure, cleanupFailure);
            throw;
        }
        if (operationFailure != null) ExceptionDispatchInfo.Capture(operationFailure).Throw();
    }
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    public static void RunOnMta(Action operation) {
        Exception failure = null;
        var thread = new Thread(() => {
            try { operation(); }
            catch (Exception error) { failure = error; }
        });
        thread.IsBackground = true;
        thread.SetApartmentState(ApartmentState.MTA);
        thread.Start();
        // The unchanged parent operation deadline kills this helper on a hang.
        thread.Join();
        if (failure != null) ExceptionDispatchInfo.Capture(failure).Throw();
    }
    public static void WithPhysicalPixels(Action operation, Func<IntPtr, IntPtr> setContext) {
        IntPtr prior = setContext(new IntPtr(-4));
        if (prior == IntPtr.Zero) throw new InvalidOperationException("physical_pixel_context_unavailable");
        Exception failure = null;
        try { operation(); } catch (Exception error) { failure = error; }
        try {
            if (setContext(prior) == IntPtr.Zero) throw new InvalidOperationException("physical_pixel_context_restore_failed");
        } catch (Exception cleanup) {
            if (failure != null) throw new AggregateException("operation_and_dpi_cleanup_failed", failure, cleanup);
            throw;
        }
        if (failure != null) ExceptionDispatchInfo.Capture(failure).Throw();
    }
    public static bool PermitViewport(ScrollRequest request, int[] area) {
        if (area == null || area.Length != 4 || area[2] <= 0 || area[3] <= 0 ||
            (long)area[2]*area[3] > 16000000 || request.Bounds == null || request.Bounds.Length != 4 ||
            area[0] < request.Bounds[0] || area[1] < request.Bounds[1] ||
            (long)area[0]+area[2] > request.Bounds[0]+request.Bounds[2] ||
            (long)area[1]+area[3] > request.Bounds[1]+request.Bounds[3]) return false;
        IntPtr handle = new IntPtr(request.Handle);
        return CaptureDesktop.Interactive() && CaptureDesktop.IsWindowVisible(handle) &&
            !CaptureDesktop.IsIconic(handle) && CaptureDesktop.Unoccluded(handle,area[0],area[1],area[2],area[3]);
    }
    public static void SetPercent(long handle, int pid, long ticks, string rootRuntime, string runtime,
        double[] bounds, double position, double viewSize, double destination, int[] viewport, Func<bool> permitMutation) {
        // PowerShell runs only this preflight on its own runspace thread.
        Require(permitMutation != null && permitMutation());
        Require(bounds != null && viewport != null);
        IntPtr foreground = GetForegroundWindow();
        var request = new ScrollRequest { Handle=handle, ProcessId=pid, StartedTicks=ticks,
            RootRuntime=rootRuntime, Runtime=runtime, Bounds=(double[])bounds.Clone(),
            Position=position, ViewSize=viewSize, Destination=destination };
        var area = (int[])viewport.Clone();
        RunOnMta(() => WithPhysicalPixels(
            () => ExecuteOwned(new NativeScrollClient(foreground), request, () => PermitViewport(request, area)),
            CaptureDesktop.SetThreadDpiAwarenessContext));
    }
}
internal sealed class NativeScrollClient : IScrollClient, IDisposable {
    private readonly List<object> owned = new List<object>();
    private readonly IAutomation2 automation;
    private readonly ForegroundGuard foreground;
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    public NativeScrollClient(IntPtr expectedForeground) {
        foreground = new ForegroundGuard(GetForegroundWindow, expectedForeground);
        var type = Type.GetTypeFromCLSID(new Guid("e22ad333-b25f-460c-83d0-0581107395c9"), true); // CUIAutomation8
        Phase.Mark("nofocus_constructing");
        object instance = Activator.CreateInstance(type);
        try {
            automation = (IAutomation2)instance; owned.Add(instance);
            foreground.EnsureUnchanged();
            Phase.Mark("nofocus_constructed");
        }
        catch (Exception primary) {
            try { if (instance != null && Marshal.IsComObject(instance)) Marshal.ReleaseComObject(instance); }
            catch (Exception cleanup) { throw new AggregateException("native_scroll_construction_and_cleanup_failed", primary, cleanup); }
            throw;
        }
    }
    public bool AutoSetFocus {
        get { int enabled; automation.get_AutoSetFocus(out enabled); foreground.EnsureUnchanged(); return enabled != 0; }
        set { foreground.EnsureUnchanged(); automation.put_AutoSetFocus(value ? 1 : 0); foreground.EnsureUnchanged(); }
    }
    private T Own<T>(T value) where T : class {
        if (value == null) throw new InvalidOperationException("native_scroll_interface_unavailable");
        owned.Add(value); return value;
    }
    private static object Property(IElement element, int property) {
        object value; element.GetCurrentPropertyValue(property, out value); return value;
    }
    private static string Runtime(IElement element) {
        int[] value; element.GetRuntimeId(out value);
        if (value == null || value.Length == 0 || value.Length > 64) throw new InvalidOperationException("native_scroll_runtime_unavailable");
        return String.Join(".", value);
    }
    private static double[] Bounds(IElement element) {
        var raw = Property(element, 30001) as double[]; // UIA_BoundingRectanglePropertyId
        if (raw == null || raw.Length != 4) throw new InvalidOperationException("native_scroll_bounds_unavailable");
        return new double[] { Math.Ceiling(raw[0]), Math.Ceiling(raw[1]),
            Math.Floor(raw[0]+raw[2])-Math.Ceiling(raw[0]), Math.Floor(raw[1]+raw[3])-Math.Ceiling(raw[1]) };
    }
    public ScrollTarget Resolve(ScrollRequest request) {
        foreground.EnsureUnchanged();
        IntPtr handle = new IntPtr(request.Handle);
        uint owner; GetWindowThreadProcessId(handle, out owner);
        if (owner != request.ProcessId) throw new InvalidOperationException("native_scroll_window_changed");
        long started;
        using (var process = Process.GetProcessById(request.ProcessId)) started = process.StartTime.ToUniversalTime().Ticks;
        IElement root; automation.ElementFromHandle(handle, out root); Own(root);
        string rootId = Runtime(root);
        if ((int)Property(root,30002) != request.ProcessId || rootId != request.RootRuntime || started != request.StartedTicks)
            throw new InvalidOperationException("native_scroll_root_changed");
        IWalker walker; automation.get_RawViewWalker(out walker); Own(walker);
        var point = new NativePoint { X=checked((int)Math.Floor(request.Bounds[0]+request.Bounds[2]/2)),
            Y=checked((int)Math.Floor(request.Bounds[1]+request.Bounds[3]/2)) };
        IElement element; automation.ElementFromPoint(point, out element);
        if (element != null) Own(element);
        IElement target = null;
        bool reachedRoot = false;
        for (int depth=0; depth<32 && element != null; depth++) {
            if ((int)Property(element,30002) != request.ProcessId) throw new InvalidOperationException("native_scroll_cross_process");
            string id = Runtime(element);
            if (id == request.Runtime) target = element;
            if (id == rootId) { reachedRoot = true; break; }
            IElement parent; walker.GetParentElement(element, out parent);
            element = parent; if (element != null) Own(element);
        }
        if (!reachedRoot || target == null) throw new InvalidOperationException("native_scroll_ancestry_changed");
        Guid patternId = new Guid("88f4d42a-e881-459d-a77c-73bbbb7e02dc");
        IScroll pattern; target.GetCurrentPatternAs(10004, ref patternId, out pattern); Own(pattern);
        double position, view; int scrollable;
        pattern.get_CurrentVerticalScrollPercent(out position);
        pattern.get_CurrentVerticalViewSize(out view);
        pattern.get_CurrentVerticallyScrollable(out scrollable);
        return new ScrollTarget { Handle=request.Handle, ProcessId=(int)Property(target,30002), StartedTicks=started,
            RootRuntime=rootId, Runtime=Runtime(target), Bounds=Bounds(target), Position=position, ViewSize=view,
            Enabled=(bool)Property(target,30010), Offscreen=(bool)Property(target,30022), Scrollable=scrollable!=0, Pattern=pattern };
    }
    public void Move(ScrollTarget target, double destination) {
        uint owner; GetWindowThreadProcessId(new IntPtr(target.Handle), out owner);
        using (var process = Process.GetProcessById(target.ProcessId)) {
            if (owner != target.ProcessId || process.StartTime.ToUniversalTime().Ticks != target.StartedTicks)
                throw new InvalidOperationException("native_scroll_precondition_changed");
        }
        foreground.EnsureUnchanged();
        Phase.Mark("nofocus_moving");
        target.Pattern.SetScrollPercent(-1, destination);
        Phase.Mark("nofocus_call_returned");
        foreground.EnsureUnchanged();
    }
    public void Dispose() {
        var failures = new List<Exception>();
        for (int i=owned.Count-1; i>=0; i--) {
            try { Marshal.ReleaseComObject(owned[i]); }
            catch (Exception error) { failures.Add(error); } // Continue independent releases.
        }
        owned.Clear();
        if (failures.Count != 0) throw new AggregateException("native_scroll_cleanup_failed", failures);
    }
}
}
