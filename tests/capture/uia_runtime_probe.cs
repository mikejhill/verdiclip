// Test-only identity comparison. No acceptance/fallback, capture or input.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
namespace VerdiClip.Automation {
public static class RuntimeProbeGuard {
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

    public static void Restore(IntPtr previous) {
        Require(previous != IntPtr.Zero);
        Require(CaptureDesktop.SetThreadDpiAwarenessContext(previous) != IntPtr.Zero);
        Require(AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), previous));
    }
}
public sealed class RuntimeShapes {
    public string method, property;
    public bool? equal;
}
public static class RuntimeComparisonPolicy {
    public static RuntimeProbeResult Complete(Func<RuntimeProbeResult> work, Action release, Action restore, Action verify) {
        var errors=new List<Exception>(); RuntimeProbeResult result=null;
        try { result=work(); } catch(Exception error) { errors.Add(error); }
        foreach(Action cleanup in new Action[]{release,restore,verify}) {
            try { cleanup(); } catch(Exception error) { errors.Add(error); }
        }
        DiscoveryPolicy.ThrowFailures(errors); DiscoveryPolicy.Require(result!=null);
        result.ownership_verified=true; result.foreground_unchanged=true;
        result.release_confirmed=true; result.dpi_restored=true; result.native_only_verified=true;
        return result;
    }

    public static string Shape(object value) {
        if(value == null) return "null";
        int[] array=value as int[];
        if(array == null) return "wrong_type";
        if(array.Length == 0) return "empty";
        if(array.Length > 64) return "oversized";
        return "valid";
    }
    public static RuntimeShapes Compare(Func<int[]> method, Func<object> property, Action guard) {
        guard(); int[] first=method(); guard();
        object second=property(); guard();
        var result=new RuntimeShapes {method=Shape(first),property=Shape(second),equal=null};
        if(result.method=="valid" && result.property=="valid") {
            int[] other=(int[])second;
            bool same=first.Length==other.Length;
            for(int i=0;same && i<first.Length;i++) same=first[i]==other[i];
            result.equal=same;
        }
        return result;
    }
}
public sealed class RuntimeProbeResult {
    public RuntimeShapes root, hit;
    public AncestryObservation ancestry;
    public bool ownership_verified, foreground_unchanged, release_confirmed, dpi_restored, native_only_verified;
}
public static class RuntimeProbe {
    public static RuntimeProbeResult Run(long handle,int pid,long started,long foreground,int x,int y) {
        IntPtr prior=IntPtr.Zero;
        NativeDiscoveryBackend backend=null;
        Action owned=()=>RuntimeProbeGuard.RequireOwnedWindow(handle,pid,started,foreground);
        return RuntimeComparisonPolicy.Complete(()=> {
            DiscoveryPolicy.RequireNativeOnly(); owned();
            prior=CaptureDesktop.SetThreadDpiAwarenessContext(new IntPtr(-4));
            DiscoveryPolicy.Require(prior!=IntPtr.Zero);
            RuntimeProbeGuard.RequirePhysicalPixels();
            backend=new NativeDiscoveryBackend(false); backend.Construct();
            Action guard=()=>{ backend.Guard(); owned(); RuntimeProbeGuard.RequirePhysicalPixels(); };
            guard(); var root=backend.Root(handle); guard();
            DiscoveryPolicy.Require(root!=null);
            DiscoveryPolicy.Require(DiscoveryPolicy.Integer(root.Property(30002))==pid);
            DiscoveryPolicy.Require((long)DiscoveryPolicy.Integer(root.Property(30020))==handle);
            guard();
            int[] rootId=null;
            var rootShapes=RuntimeComparisonPolicy.Compare(()=>{rootId=root.Runtime();return rootId;},()=>root.Property(30000),guard);
            var hit=backend.Hit(x,y); guard();
            DiscoveryPolicy.Require(hit!=null);
            DiscoveryPolicy.Require(DiscoveryPolicy.Integer(hit.Property(30002))==pid);
            guard();
            int[] hitId=null;
            var hitShapes=RuntimeComparisonPolicy.Compare(()=>{hitId=hit.Runtime();return hitId;},()=>hit.Property(30000),guard);
            var ancestry=AncestryPolicy.Trace(backend,rootId,hit,hitId,handle,pid,x,y,guard);
            return new RuntimeProbeResult {root=rootShapes,hit=hitShapes,ancestry=ancestry};
        },()=>{ if(backend!=null) backend.Dispose(); },
        ()=>{ if(prior!=IntPtr.Zero) RuntimeProbeGuard.Restore(prior); },
        ()=>{ owned(); DiscoveryPolicy.RequireNativeOnly(); });
    }
}
}
