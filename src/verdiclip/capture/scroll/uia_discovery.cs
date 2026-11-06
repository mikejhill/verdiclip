// Read-only UIA discovery. No mutation, activation, managed UIA or provider text.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Threading;
namespace VerdiClip.Automation {
public interface IDiscoveryElement {
    object Property(int id);
    int[] Runtime();
    IDiscoveryScroll Scroll();
}
public interface IDiscoveryScroll {
    bool VerticallyScrollable { get; }
    double VerticalScrollPercent { get; }
    double VerticalViewSize { get; }
}
public interface IDiscoveryBackend : IDisposable {
    IDiscoveryElement Root(long handle);
    IDiscoveryElement Hit(int x, int y);
    IDiscoveryElement Parent(IDiscoveryElement element);
    void Guard();
    void Mark(string phase);
}
public sealed class DiscoveryRectangle {
    public double Left, Top, Width, Height;
    public double Right { get { return Left + Width; } }
    public double Bottom { get { return Top + Height; } }
    public bool IsEmpty { get { return Width <= 0 || Height <= 0; } }
}
public delegate void DiscoveryAcquisition<T>(out T value) where T:class;
public static class DiscoveryPolicy {
    public static T Acquire<T>(DiscoveryAcquisition<T> acquire, Action<T> own) where T:class {
        T value=null;
        try { acquire(out value); } finally { own(value); }
        return value;
    }
    public static void Require(bool condition) {
        if (!condition) throw new InvalidOperationException("discovery_identity_or_policy_changed");
    }
    public static bool Finite(double value) { return !Double.IsNaN(value) && !Double.IsInfinity(value); }
    public static int Integer(object value) { Require(value is int); return (int)value; }
    public static bool Boolean(object value) { Require(value is bool); return (bool)value; }
    public static string Runtime(int[] value) {
        Require(value != null && value.Length > 0 && value.Length <= 64);
        var parts = new string[value.Length];
        for (int i=0; i<value.Length; i++) parts[i] = value[i].ToString(CultureInfo.InvariantCulture);
        return String.Join(".", parts);
    }
    public static DiscoveryRectangle Rectangle(object value) {
        var a = value as double[];
        Require(a != null && a.Length == 4);
        foreach (double n in a) Require(Finite(n));
        Require(a[2] > 0 && a[3] > 0);
        double left = Math.Ceiling(a[0]), top = Math.Ceiling(a[1]);
        double right = Math.Floor(a[0]+a[2]), bottom = Math.Floor(a[1]+a[3]);
        Require(Finite(right) && Finite(bottom) && left >= Int32.MinValue && top >= Int32.MinValue &&
            right <= Int32.MaxValue && bottom <= Int32.MaxValue && right > left && bottom > top &&
            right-left <= Int32.MaxValue && bottom-top <= Int32.MaxValue);
        return new DiscoveryRectangle { Left=left, Top=top, Width=right-left, Height=bottom-top };
    }
    // Deliberate native sampling policy, not an asserted equivalence to managed
    // FromPoint: floor chooses the containing pixel, including negative coordinates.
    public static int Pixel(double value) {
        Require(Finite(value) && value >= Int32.MinValue && value <= Int32.MaxValue);
        return checked((int)Math.Floor(value));
    }
    public static void Metrics(double position, double view) {
        Require(Finite(position) && position >= 0 && position <= 100 &&
            Finite(view) && view > 0 && view < 100);
    }
    public static void Release(object[] values, Func<object,bool> isCom, Func<object,int> release) {
        var failures = new List<Exception>();
        // Each acquisition is retained, even if it aliases an existing RCW. COM
        // interop increments its RCW reference count per marshalled acquisition.
        for (int i=values.Length-1; i>=0; i--) {
            object value = values[i];
            if (value == null) continue;
            try {
                if (isCom(value)) {
                    bool earlier=false;
                    for(int j=0;j<i;j++) if(Object.ReferenceEquals(values[j],value)) earlier=true;
                    int remaining=release(value);
                    Require(remaining >= 0 && (earlier ? remaining > 0 : remaining == 0));
                }
            }
            catch (Exception failure) { failures.Add(failure); }
        }
        ThrowFailures(failures);
    }
    private static void TryMark(Action<string> mark, string phase) {
        try { mark(phase); }
        catch(Exception) { /* Optional diagnostics must never prevent cleanup. */ }
    }
    public static int[] ReadRuntime(Func<int[]> read, Action guard, Action<string> mark) {
        TryMark(mark,"discovery_runtime_guard_before"); guard();
        TryMark(mark,"discovery_runtime_call"); int[] value=read();
        TryMark(mark,"discovery_runtime_array"); Runtime(value);
        TryMark(mark,"discovery_runtime_guard_after"); guard();
        TryMark(mark,"discovery_runtime_verified"); return value;
    }
    public static void Retire(Action release, Action restore) {
        Retire(release,restore,phase=>{});
    }
    public static void Retire(Action release, Action restore, Action<string> mark) {
        var failures=new List<Exception>();
        TryMark(mark,"discovery_release");
        try { release(); TryMark(mark,"discovery_released"); }
        catch(Exception error) { TryMark(mark,"discovery_release_failed"); failures.Add(error); }
        TryMark(mark,"discovery_restore");
        try { restore(); TryMark(mark,"discovery_restored"); }
        catch(Exception error) { TryMark(mark,"discovery_restore_failed"); failures.Add(error); }
        ThrowFailures(failures);
    }
    public static void ThrowFailures(List<Exception> failures) {
        if (failures.Count == 1) ExceptionDispatchInfo.Capture(failures[0]).Throw();
        if (failures.Count > 1) throw new AggregateException("discovery_cleanup_failed", failures);
    }
    public static void RequireNativeOnly() {
        foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies()) {
            string name=assembly.GetName().Name;
            Require(!String.Equals(name,"UIAutomationClient",StringComparison.OrdinalIgnoreCase) &&
                !String.Equals(name,"UIAutomationTypes",StringComparison.OrdinalIgnoreCase));
        }
        Require(Thread.CurrentThread.GetApartmentState() == ApartmentState.MTA);
    }
}
public sealed class DiscoveryNode {
    internal readonly IDiscoveryElement Element;
    private readonly IDiscoveryBackend backend;
    internal DiscoveryNode(IDiscoveryBackend backend, IDiscoveryElement element) {
        DiscoveryPolicy.Require(element != null); this.backend=backend; Element=element;
    }
    private object Property(int id) { backend.Guard(); object result=Element.Property(id); backend.Guard(); return result; }
    public DiscoveryNode Current { get { return this; } }
    // PowerShell can suppress property-getter errors. Cross its boundary through
    // explicit methods so provider/COM exceptions remain terminating failures.
    public DiscoveryRectangle ReadBounds() { return BoundingRectangle; }
    public string ReadRuntime() { return RuntimeIdentity; }
    public int ReadControlType() { return ControlType; }
    public int ReadOrientation() { return Orientation; }
    public bool ReadOffscreen() { return IsOffscreen; }
    public bool ReadEnabled() { return IsEnabled; }
    public int ProcessId { get { return DiscoveryPolicy.Integer(Property(30002)); } }
    public int NativeWindowHandle { get { return DiscoveryPolicy.Integer(Property(30020)); } }
    public int ControlType { get { return DiscoveryPolicy.Integer(Property(30003)); } }
    public int Orientation { get { return DiscoveryPolicy.Integer(Property(30023)); } }
    public bool IsOffscreen { get { return DiscoveryPolicy.Boolean(Property(30022)); } }
    public bool IsEnabled { get { return DiscoveryPolicy.Boolean(Property(30010)); } }
    public DiscoveryRectangle BoundingRectangle { get { return DiscoveryPolicy.Rectangle(Property(30001)); } }
    public int[] GetRuntimeId() { return DiscoveryPolicy.ReadRuntime(Element.Runtime,backend.Guard,backend.Mark); }
    public string RuntimeIdentity { get { return DiscoveryPolicy.Runtime(GetRuntimeId()); } }
    public DiscoveryPattern GetScroll() {
        // A typed availability property distinguishes genuine absence from failed
        // pattern acquisition. Provider/COM errors never become unsupported.
        backend.Mark("inspect_pattern");
        if (!DiscoveryPolicy.Boolean(Property(30034))) return null;
        backend.Guard(); var pattern=Element.Scroll(); backend.Guard();
        DiscoveryPolicy.Require(pattern != null);
        var result=new DiscoveryPattern(backend,pattern);
        backend.Mark("inspect_scrollable"); return result.VerticallyScrollable ? result : null;
    }
}
public sealed class DiscoveryPattern {
    private readonly IDiscoveryBackend backend;
    private readonly IDiscoveryScroll pattern;
    internal DiscoveryPattern(IDiscoveryBackend backend, IDiscoveryScroll pattern) { this.backend=backend; this.pattern=pattern; }
    public DiscoveryPattern Current { get { return this; } }
    public double ReadPosition() { return VerticalScrollPercent; }
    public double ReadViewSize() { return VerticalViewSize; }
    public bool VerticallyScrollable { get { backend.Guard(); bool value=pattern.VerticallyScrollable; backend.Guard(); return value; } }
    public double VerticalScrollPercent { get { backend.Guard(); double value=pattern.VerticalScrollPercent; backend.Guard(); DiscoveryPolicy.Require(DiscoveryPolicy.Finite(value) && value>=0 && value<=100); return value; } }
    public double VerticalViewSize { get { backend.Guard(); double value=pattern.VerticalViewSize; backend.Guard(); DiscoveryPolicy.Require(DiscoveryPolicy.Finite(value) && value>0 && value<100); return value; } }
}
public sealed class DiscoveryResolver {
    private readonly IDiscoveryBackend backend;
    public readonly DiscoveryNode Root;
    public readonly int ProcessId;
    private readonly string rootRuntime;
    public DiscoveryResolver(IDiscoveryBackend backend, long handle) {
        DiscoveryPolicy.Require(backend != null && handle > 0); this.backend=backend;
        backend.Mark("inspect_root"); Root=new DiscoveryNode(backend,backend.Root(handle));
        backend.Mark("inspect_process"); ProcessId=Root.ProcessId;
        DiscoveryPolicy.Require(ProcessId>0 && (long)Root.NativeWindowHandle==handle);
        backend.Mark("inspect_root_runtime"); rootRuntime=Root.RuntimeIdentity;
    }
    public DiscoveryNode[] FindAncestors(double x,double y) {
        var chain=new List<DiscoveryNode>(); var seen=new HashSet<string>(StringComparer.Ordinal);
        backend.Mark("inspect_hit"); backend.Guard(); IDiscoveryElement element=backend.Hit(DiscoveryPolicy.Pixel(x),DiscoveryPolicy.Pixel(y)); backend.Guard();
        for (int depth=0; depth<32 && element!=null; depth++) {
            var node=new DiscoveryNode(backend,element);
            backend.Mark("inspect_ancestor_identity");
            backend.Mark("inspect_ancestor_process_read"); int process=node.ProcessId;
            backend.Mark("inspect_ancestor_process_guard"); DiscoveryPolicy.Require(process==ProcessId);
            backend.Mark("inspect_ancestor_runtime_read"); string runtime=node.RuntimeIdentity;
            backend.Mark("inspect_ancestor_cycle_guard"); DiscoveryPolicy.Require(seen.Add(runtime));
            backend.Mark("inspect_ancestor_verified"); chain.Add(node);
            if (runtime==rootRuntime) { DiscoveryPolicy.Require(node.NativeWindowHandle==Root.NativeWindowHandle); return chain.ToArray(); }
            backend.Mark("inspect_parent"); backend.Guard(); element=backend.Parent(element); backend.Guard();
        }
        throw new InvalidOperationException("discovery_ancestry_unverified");
    }
}
internal sealed class NativeDiscoveryBackend : IDiscoveryBackend {
    private readonly List<object> owned=new List<object>();
    private IAutomation2 automation;
    private IWalker walker;
    private readonly IntPtr foreground;
    private bool disposed;
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    private readonly bool diagnostics;
    public NativeDiscoveryBackend(bool diagnostics) {
        this.diagnostics=diagnostics;
        DiscoveryPolicy.RequireNativeOnly(); foreground=GetForegroundWindow(); DiscoveryPolicy.Require(foreground!=IntPtr.Zero);
    }
    public void Mark(string phase) {
        if(!diagnostics) return;
        try { Console.Error.WriteLine("VERDICLIP_UIA_PHASE="+phase); } catch(System.IO.IOException) {} catch(ObjectDisposedException) {}
    }
    public void Construct() {
        Guard(); var type=Type.GetTypeFromCLSID(new Guid("e22ad333-b25f-460c-83d0-0581107395c9"),true);
        object instance=Activator.CreateInstance(type); owned.Add(instance); automation=(IAutomation2)instance;
        automation.put_AutoSetFocus(0); Guard();
        walker=DiscoveryPolicy.Acquire<IWalker>(automation.get_RawViewWalker, value=>Own(value));
        DiscoveryPolicy.Require(walker!=null); Guard();
    }
    internal T Own<T>(T value) where T:class { if(value!=null) owned.Add(value); return value; }
    private IDiscoveryElement Wrap(IElement value) { return value==null ? null : new NativeDiscoveryElement(this,value); }
    public void Guard() {
        DiscoveryPolicy.Require(!disposed); DiscoveryPolicy.RequireNativeOnly();
        DiscoveryPolicy.Require(GetForegroundWindow()==foreground);
        if(automation!=null) { int enabled; automation.get_AutoSetFocus(out enabled); DiscoveryPolicy.Require(enabled==0); }
    }
    public IDiscoveryElement Root(long handle) { return Wrap(DiscoveryPolicy.Acquire<IElement>(delegate(out IElement value){automation.ElementFromHandle(new IntPtr(handle),out value);}, value=>Own(value))); }
    public IDiscoveryElement Hit(int x,int y) { return Wrap(DiscoveryPolicy.Acquire<IElement>(delegate(out IElement value){automation.ElementFromPoint(new NativePoint{X=x,Y=y},out value);}, value=>Own(value))); }
    public IDiscoveryElement Parent(IDiscoveryElement element) { return Wrap(DiscoveryPolicy.Acquire<IElement>(delegate(out IElement value){walker.GetParentElement(((NativeDiscoveryElement)element).Value,out value);}, value=>Own(value))); }
    public void Dispose() {
        if(disposed) throw new InvalidOperationException("discovery_already_retired");
        disposed=true; object[] values=owned.ToArray(); owned.Clear(); automation=null; walker=null;
        var failures=new List<Exception>();
        try { DiscoveryPolicy.Release(values,Marshal.IsComObject,Marshal.ReleaseComObject); } catch(Exception error) { failures.Add(error); }
        try { DiscoveryPolicy.Require(GetForegroundWindow()==foreground); } catch(Exception error) { failures.Add(error); }
        DiscoveryPolicy.ThrowFailures(failures);
    }
}
internal sealed class NativeDiscoveryElement : IDiscoveryElement {
    internal readonly IElement Value; private readonly NativeDiscoveryBackend owner;
    internal NativeDiscoveryElement(NativeDiscoveryBackend owner,IElement value) { this.owner=owner; Value=value; }
    public object Property(int id) { return DiscoveryPolicy.Acquire<object>(delegate(out object value){Value.GetCurrentPropertyValue(id,out value);}, value=>{if(value!=null && Marshal.IsComObject(value))owner.Own(value);}); }
    public int[] Runtime() { int[] value; Value.GetRuntimeId(out value); return value; }
    public IDiscoveryScroll Scroll() {
        Guid iid=new Guid("88f4d42a-e881-459d-a77c-73bbbb7e02dc");
        IScroll pattern=DiscoveryPolicy.Acquire<IScroll>(delegate(out IScroll value){Value.GetCurrentPatternAs(10004,ref iid,out value);}, value=>owner.Own(value));
        DiscoveryPolicy.Require(pattern!=null); return new NativeDiscoveryScroll(pattern);
    }
}
internal sealed class NativeDiscoveryScroll : IDiscoveryScroll {
    private readonly IScroll value; internal NativeDiscoveryScroll(IScroll value) { this.value=value; }
    public bool VerticallyScrollable { get { int result; value.get_CurrentVerticallyScrollable(out result); DiscoveryPolicy.Require(result==0 || result==1); return result==1; } }
    public double VerticalScrollPercent { get { double result; value.get_CurrentVerticalScrollPercent(out result); return result; } }
    public double VerticalViewSize { get { double result; value.get_CurrentVerticalViewSize(out result); return result; } }
}
public sealed class DiscoverySession : IDisposable {
    private NativeDiscoveryBackend backend;
    private IntPtr prior;
    private bool disposed;
    private bool diagnostics;
    private void Mark(string phase) {
        if(!diagnostics) return;
        try { Console.Error.WriteLine("VERDICLIP_UIA_PHASE="+phase); } catch(System.IO.IOException) {} catch(ObjectDisposedException) {}
    }
    public DiscoveryResolver Resolver { get; private set; }
    public long StartedTicks { get; private set; }
    [DllImport("user32.dll")] private static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] private static extern bool AreDpiAwarenessContextsEqual(IntPtr a,IntPtr b);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
    public DiscoverySession() { DiscoveryPolicy.RequireNativeOnly(); }
    public void Open(long handle, bool diagnostics) {
        this.diagnostics=diagnostics;
        DiscoveryPolicy.Require(!disposed && backend==null && prior==IntPtr.Zero && handle>0);
        prior=CaptureDesktop.SetThreadDpiAwarenessContext(new IntPtr(-4)); DiscoveryPolicy.Require(prior!=IntPtr.Zero);
        DiscoveryPolicy.Require(AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(),new IntPtr(-4)));
        IntPtr window=new IntPtr(handle); DiscoveryPolicy.Require(CaptureDesktop.Interactive() && CaptureDesktop.IsWindowVisible(window) && !CaptureDesktop.IsIconic(window));
        uint pid; DiscoveryPolicy.Require(GetWindowThreadProcessId(window,out pid)!=0 && pid>0 && pid<=Int32.MaxValue);
        using(var process=Process.GetProcessById((int)pid)) StartedTicks=process.StartTime.ToUniversalTime().Ticks;
        backend=new NativeDiscoveryBackend(diagnostics); backend.Construct(); Resolver=new DiscoveryResolver(backend,handle);
        DiscoveryPolicy.Require(Resolver.ProcessId==(int)pid); Verify(handle);
    }
    public void Verify(long handle) {
        backend.Guard(); uint pid; DiscoveryPolicy.Require(GetWindowThreadProcessId(new IntPtr(handle),out pid)!=0 && pid==Resolver.ProcessId);
        using(var process=Process.GetProcessById((int)pid)) DiscoveryPolicy.Require(process.StartTime.ToUniversalTime().Ticks==StartedTicks);
        DiscoveryPolicy.Require(AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(),new IntPtr(-4)));
    }
    public void Dispose() {
        DiscoveryPolicy.Require(!disposed); disposed=true;
        NativeDiscoveryBackend owned=backend; backend=null;
        IntPtr saved=prior; prior=IntPtr.Zero;
        DiscoveryPolicy.Retire(
            () => { if(owned!=null) owned.Dispose(); },
            () => { if(saved!=IntPtr.Zero) {
                DiscoveryPolicy.Require(CaptureDesktop.SetThreadDpiAwarenessContext(saved)!=IntPtr.Zero);
                DiscoveryPolicy.Require(AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(),saved));
            } },Mark);
    }
}
}
