// Pure policy tests. Never construct NativeScrollClient or invoke Mutate.
using System;
using System.Collections.Generic;
using System.Threading;
using VerdiClip.Automation;
public static class NoFocusPolicyTests {
    sealed class Fake : IScrollClient {
        public readonly List<string> Calls = new List<string>();
        public bool IgnoreDisable, DriftPolicy, FailResolve, FailMove;
        public int Reads, Moves, Disposals;
        public bool FailDispose;
        public Exception CleanupFailure = new InvalidOperationException("cleanup failure");
        public void Dispose() { Disposals++; if(FailDispose) throw CleanupFailure; }
        public ScrollTarget Target;
        public Exception Failure = new InvalidOperationException("owned failure");
        public bool AutoSetFocus {
            get { Calls.Add("read-policy"); Reads++; return IgnoreDisable || (DriftPolicy && Reads > 1); }
            set { if(value) throw new Exception("must never enable focus"); Calls.Add("disable-focus"); }
        }
        public ScrollTarget Resolve(ScrollRequest request) { Calls.Add("resolve"); if(FailResolve) throw Failure; return Target; }
        public void Move(ScrollTarget target, double destination) {
            Calls.Add("move"); Moves++; if(FailMove) throw Failure;
            if(target != Target || destination != 20) throw new Exception("changed mutation arguments");
        }
    }
    static ScrollRequest Request() { return new ScrollRequest { Handle=10, ProcessId=20, StartedTicks=30,
        RootRuntime="1.2", Runtime="1.2.3", Bounds=new double[]{-200,40,100,80}, Position=0, ViewSize=25, Destination=20 }; }
    static Fake Client() { return new Fake { Target=new ScrollTarget { Handle=10, ProcessId=20, StartedTicks=30,
        RootRuntime="1.2", Runtime="1.2.3", Bounds=new double[]{-200,40,100,80}, Position=0, ViewSize=25,
        Enabled=true, Offscreen=false, Scrollable=true }}; }
    static void Reject(Fake client, ScrollRequest request) {
        bool rejected=false;
        try { NoFocusScroll.Execute(client,request,()=>true); } catch(InvalidOperationException) { rejected=true; }
        if(!rejected || client.Moves!=0) throw new Exception("unsafe mutation was not refused");
    }
    public static int Run() {
        int count=0;
        var good=Client(); NoFocusScroll.Execute(good,Request(),()=>true); count++;
        if(String.Join(",",good.Calls)!="disable-focus,read-policy,resolve,read-policy,read-policy,move") throw new Exception("wrong client ordering");
        var ignore=Client();ignore.IgnoreDisable=true;Reject(ignore,Request());count++;
        if(ignore.Calls.Contains("resolve")) throw new Exception("resolved before no-focus verification");
        var drift=Client();drift.DriftPolicy=true;Reject(drift,Request());count++;
        Action<ScrollTarget>[] targetFaults={
            t=>t.Handle++, t=>t.ProcessId++, t=>t.StartedTicks++, t=>t.Runtime="other", t=>t.RootRuntime="other",
            t=>t.Bounds=null, t=>t.Bounds=new double[]{1}, t=>t.Bounds[0]++, t=>t.Bounds[1]++, t=>t.Bounds[2]++, t=>t.Bounds[3]++,
            t=>t.Enabled=false, t=>t.Offscreen=true, t=>t.Scrollable=false,
            t=>t.Position=Double.NaN, t=>t.Position=Double.PositiveInfinity, t=>t.Position=1,
            t=>t.ViewSize=Double.NaN, t=>t.ViewSize=26
        };
        foreach(var corrupt in targetFaults) { var c=Client();corrupt(c.Target);Reject(c,Request());count++; }
        Action<ScrollRequest>[] requestFaults={
            r=>r.Handle=0, r=>r.ProcessId=0, r=>r.StartedTicks=0, r=>r.Runtime="", r=>r.RootRuntime="",
            r=>r.Bounds=null, r=>r.Bounds=new double[]{1}, r=>r.Bounds[0]=Double.NaN, r=>r.Bounds[1]=0.5,
            r=>r.Bounds[2]=0, r=>r.Bounds[3]=-1, r=>r.Position=-1, r=>r.ViewSize=100,
            r=>r.Destination=Double.NaN, r=>r.Destination=-1, r=>r.Destination=101
        };
        foreach(var corrupt in requestFaults) { var c=Client();var r=Request();corrupt(r);Reject(c,r);count++;
            if(c.Calls.Count!=0) throw new Exception("invalid input touched client"); }
        var resolve=Client();resolve.FailResolve=true;
        try { NoFocusScroll.Execute(resolve,Request(),()=>true); throw new Exception("resolve failure swallowed"); }
        catch(InvalidOperationException error) { if(error!=resolve.Failure || resolve.Moves!=0) throw; } count++;
        var move=Client();move.FailMove=true;
        try { NoFocusScroll.Execute(move,Request(),()=>true); throw new Exception("move failure swallowed"); }
        catch(InvalidOperationException error) { if(error!=move.Failure || move.Moves!=1) throw; } count++;
        var refused=Client();
        try { NoFocusScroll.Execute(refused,Request(),()=>false); throw new Exception("guard refusal ignored"); }
        catch(InvalidOperationException) { if(refused.Moves!=0) throw; } count++;
        var changedByGuard=Client();
        try { NoFocusScroll.Execute(changedByGuard,Request(),()=>{ changedByGuard.IgnoreDisable=true; return true; }); throw new Exception("post-guard policy drift accepted"); }
        catch(InvalidOperationException) { if(changedByGuard.Moves!=0) throw; } count++;
        var owned=Client(); NoFocusScroll.ExecuteOwned(owned,Request(),()=>true);
        if(owned.Disposals!=1 || owned.Moves!=1) throw new Exception("owned success leaked"); count++;
        var primary=Client(); primary.FailResolve=true;
        try { NoFocusScroll.ExecuteOwned(primary,Request(),()=>true); throw new Exception("primary swallowed"); }
        catch(InvalidOperationException error) { if(error!=primary.Failure || primary.Disposals!=1) throw; } count++;
        var cleanupOnly=Client(); cleanupOnly.FailDispose=true;
        try { NoFocusScroll.ExecuteOwned(cleanupOnly,Request(),()=>true); throw new Exception("cleanup swallowed"); }
        catch(InvalidOperationException error) { if(error!=cleanupOnly.CleanupFailure || cleanupOnly.Disposals!=1) throw; } count++;
        var both=Client(); both.FailResolve=true; both.FailDispose=true;
        try { NoFocusScroll.ExecuteOwned(both,Request(),()=>true); throw new Exception("double failure swallowed"); }
        catch(AggregateException error) { if(error.InnerExceptions.Count!=2 || error.InnerExceptions[0]!=both.Failure || error.InnerExceptions[1]!=both.CleanupFailure || both.Disposals!=1) throw; } count++;
        IntPtr active = new IntPtr(17);
        var foreground = new ForegroundGuard(()=>active);
        foreground.EnsureUnchanged(); count++;
        active = new IntPtr(18); // A constructor/resolution must not adopt this new baseline.
        try { foreground.EnsureUnchanged(); throw new Exception("foreground was rebased"); }
        catch(InvalidOperationException) {} count++;
        try { new ForegroundGuard(()=>IntPtr.Zero); throw new Exception("zero foreground accepted"); }
        catch(InvalidOperationException) {} count++;
        int caller = Thread.CurrentThread.ManagedThreadId;
        int worker = caller; ApartmentState apartment = ApartmentState.Unknown;
        NoFocusScroll.RunOnMta(() => { worker=Thread.CurrentThread.ManagedThreadId; apartment=Thread.CurrentThread.GetApartmentState(); });
        if(worker==caller || apartment!=ApartmentState.MTA) throw new Exception("not a separate MTA"); count++;
        var threadFailure = new InvalidOperationException("thread operation");
        try { NoFocusScroll.RunOnMta(() => { throw threadFailure; }); throw new Exception("thread failure swallowed"); }
        catch(InvalidOperationException error) { if(error!=threadFailure) throw; } count++;
        var ownedThread = Client();
        NoFocusScroll.RunOnMta(() => NoFocusScroll.ExecuteOwned(ownedThread,Request(),()=>true));
        if(ownedThread.Moves!=1 || ownedThread.Disposals!=1) throw new Exception("thread ownership"); count++;
        for(int reject=0;reject<=4;reject++) {
            CaptureDesktop.Reject=reject;
            if(NoFocusScroll.PermitViewport(Request(),new int[]{-200,40,100,80}) != (reject==0)) throw new Exception("native viewport guard"); count++;
        }
        CaptureDesktop.Reject=0;
        if(NoFocusScroll.PermitViewport(Request(),new int[]{-201,40,100,80})) throw new Exception("outside viewport"); count++;
        var contexts=new List<IntPtr>();
        NoFocusScroll.WithPhysicalPixels(()=>contexts.Add(new IntPtr(99)),value=>{contexts.Add(value);return new IntPtr(7);});
        if(contexts.Count!=3 || contexts[0].ToInt64()!=-4 || contexts[1].ToInt64()!=99 || contexts[2].ToInt64()!=7) throw new Exception("dpi ordering"); count++;
        bool ran=false;
        try { NoFocusScroll.WithPhysicalPixels(()=>ran=true,value=>IntPtr.Zero); throw new Exception("dpi admission"); }
        catch(InvalidOperationException) { if(ran) throw; } count++;
        int dpiCalls=0;
        try { NoFocusScroll.WithPhysicalPixels(()=>{throw threadFailure;},value=>++dpiCalls==1?new IntPtr(7):IntPtr.Zero); throw new Exception("dpi failures lost"); }
        catch(AggregateException error) { if(error.InnerExceptions[0]!=threadFailure || dpiCalls!=2) throw; } count++;
        var snapshotClient=Client();
        int snapshot=NoFocusScroll.UseOwned(snapshotClient,()=>42);
        if(snapshot!=42 || snapshotClient.Disposals!=1) throw new Exception("snapshot ownership"); count++;
        var sourceTarget=Client().Target;
        var copiedTarget=NoFocusScroll.Snapshot(sourceTarget);
        if(copiedTarget==sourceTarget || copiedTarget.Bounds==sourceTarget.Bounds || copiedTarget.Pattern!=null || copiedTarget.Runtime!=sourceTarget.Runtime || copiedTarget.Position!=sourceTarget.Position) throw new Exception("snapshot retained live data"); count++;
        NoFocusScroll.RequireNativeOnlyProcess(); count++;
        var assemblyName=new System.Reflection.AssemblyName("UIAutomationClient");
        AppDomain.CurrentDomain.DefineDynamicAssembly(assemblyName,System.Reflection.Emit.AssemblyBuilderAccess.Run);
        try { NoFocusScroll.RequireNativeOnlyProcess(); throw new Exception("managed process admitted"); }
        catch(InvalidOperationException) {} count++;
        return count;
    }
}

// Production policy compiled against pure OS fakes: no user32 call is made here.
public static class CaptureDesktop {
    public static int Reject;
    public static bool Interactive() { return Reject!=1; }
    public static bool IsWindowVisible(IntPtr window) { return Reject!=2; }
    public static bool IsIconic(IntPtr window) { return Reject==3; }
    public static bool Unoccluded(IntPtr window,int x,int y,int width,int height) { return Reject!=4; }
    public static IntPtr SetThreadDpiAwarenessContext(IntPtr context) { throw new Exception("not called by pure tests"); }
}
