using System;
using System.Collections.Generic;
using VerdiClip.Automation;
public static class DiscoveryTests {
    private static int checks;
    static void Check(bool value) { checks++; if(!value) throw new Exception("Discovery assertion failed"); }
    static void Reject(Action action) { bool failed=false; try { action(); } catch(InvalidOperationException) { failed=true; } Check(failed); }
    sealed class Element : IDiscoveryElement {
        public Dictionary<int,object> Values=new Dictionary<int,object>();
        public Action Tick=()=>{};
        public int[] Id; public IDiscoveryScroll Pattern; public bool ThrowScroll;
        public Element(int id) { Id=new[]{id}; Values[30002]=42; Values[30020]=id==1?100:0; Values[30034]=false; Values[30001]=new double[]{-10.2,3.2,100.8,200.8}; Values[30003]=50014; Values[30023]=2; Values[30022]=false; Values[30010]=true; }
        public object Property(int id) { Tick(); return Values[id]; }
        public int[] Runtime() { Tick(); return Id; }
        public IDiscoveryScroll Scroll() { if(ThrowScroll) throw new InvalidOperationException("provider-failure"); return Pattern; }
    }
    sealed class Scroll : IDiscoveryScroll {
        public bool Scrollable=true; public double Position=17,View=20;
        public bool VerticallyScrollable { get { return Scrollable; } }
        public double VerticalScrollPercent { get { return Position; } }
        public double VerticalViewSize { get { return View; } }
    }
    sealed class Backend : IDiscoveryBackend {
        public Element RootElement=new Element(1), HitElement=new Element(2);
        public Dictionary<IDiscoveryElement,IDiscoveryElement> Parents=new Dictionary<IDiscoveryElement,IDiscoveryElement>();
        public Action Tick=()=>{};
        public int X,Y,Guards; public bool FailGuard; public List<string> Phases=new List<string>();
        public Backend() { Parents[HitElement]=RootElement; }
        public IDiscoveryElement Root(long handle) { return RootElement; }
        public IDiscoveryElement Hit(int x,int y) { X=x;Y=y;return HitElement; }
        public IDiscoveryElement Parent(IDiscoveryElement element) { Tick(); return Parents.ContainsKey(element)?Parents[element]:null; }
        public void Guard() { Tick(); Guards++; if(FailGuard) throw new InvalidOperationException("guard"); }
        public void Mark(string phase) { Phases.Add(phase); }
        public void Dispose() { }
    }
    public static int Run() {
        Check(DiscoveryPolicy.Runtime(new[]{Int32.MinValue,0,Int32.MaxValue})=="-2147483648.0.2147483647");
        Reject(()=>DiscoveryPolicy.Runtime(null)); Reject(()=>DiscoveryPolicy.Runtime(new int[0])); Reject(()=>DiscoveryPolicy.Runtime(new int[65]));
        foreach(object wrong in new object[]{null,true,1L,1.0,"1"}) Reject(()=>DiscoveryPolicy.Integer(wrong));
        Check(DiscoveryPolicy.Integer(42)==42); Check(DiscoveryPolicy.Boolean(true));
        foreach(object wrong in new object[]{null,0,1,"false"}) Reject(()=>DiscoveryPolicy.Boolean(wrong));
        var rect=DiscoveryPolicy.Rectangle(new double[]{-100.25,20.25,301,100.5});
        Check(rect.Left==-100 && rect.Top==21 && rect.Width==300 && rect.Height==99);
        foreach(object wrong in new object[]{null,new int[]{1,2,3,4},new double[3],new double[]{0,0,0,1},new double[]{0,0,1,-1},new double[]{Double.NaN,0,1,1},new double[]{0,0,Double.PositiveInfinity,1},new double[]{Int32.MaxValue,0,2,1},new double[]{-1e20,0,1e21,1},new double[]{0.1,0.1,0.1,0.1}}) Reject(()=>DiscoveryPolicy.Rectangle(wrong));
        Check(DiscoveryPolicy.Pixel(-1.1)==-2); Check(DiscoveryPolicy.Pixel(1.9)==1); Check(DiscoveryPolicy.Pixel(1.5)==1); Check(DiscoveryPolicy.Pixel(-1.5)==-2);
        foreach(double wrong in new[]{Double.NaN,Double.PositiveInfinity,(double)Int32.MaxValue+1,(double)Int32.MinValue-1}) Reject(()=>DiscoveryPolicy.Pixel(wrong));
        var backend=new Backend(); var resolver=new DiscoveryResolver(backend,100);
        var chain=resolver.FindAncestors(-1.5,2.5); Check(chain.Length==2 && chain[0].RuntimeIdentity=="2" && chain[1].RuntimeIdentity=="1"); Check(backend.X==-2 && backend.Y==2 && backend.Guards>0);
        Check(chain[0].GetScroll()==null); backend.HitElement.Values[30034]=1; Reject(()=>chain[0].GetScroll());
        backend.HitElement.Values[30034]=true; Reject(()=>chain[0].GetScroll());
        backend.HitElement.ThrowScroll=true; Reject(()=>chain[0].GetScroll()); backend.HitElement.ThrowScroll=false;
        var pattern=new Scroll(); backend.HitElement.Pattern=pattern; var scroll=chain[0].GetScroll(); Check(scroll!=null && scroll.Current.VerticalScrollPercent==17 && scroll.Current.VerticalViewSize==20);
        pattern.Scrollable=false; Check(chain[0].GetScroll()==null); pattern.Scrollable=true;
        foreach(double wrong in new[]{Double.NaN,Double.PositiveInfinity,-1.0,101.0}) { pattern.Position=wrong; Reject(()=>{double n=scroll.VerticalScrollPercent;}); }
        foreach(double wrong in new[]{Double.NaN,Double.PositiveInfinity,0.0,100.0}) { pattern.View=wrong; Reject(()=>{double n=scroll.VerticalViewSize;}); }
        backend.HitElement.Values[30022]=1; Reject(()=>{bool offscreen=chain[0].IsOffscreen;});
        backend.HitElement.Values[30002]=43; backend.Phases.Clear(); Reject(()=>resolver.FindAncestors(0,0));
        Check(backend.Phases[backend.Phases.Count-1]=="inspect_ancestor_process_guard");
        backend.HitElement.Values[30002]=42;
        backend.HitElement.Id=null; backend.Phases.Clear(); Reject(()=>resolver.FindAncestors(0,0));
        Check(String.Join(",",backend.Phases)=="inspect_hit,inspect_ancestor_identity,inspect_ancestor_process_read,inspect_ancestor_process_guard,inspect_ancestor_runtime_read,discovery_runtime_guard_before,discovery_runtime_call,discovery_runtime_array,discovery_runtime_guard_after,discovery_runtime_verified");
        backend.HitElement.Id=new[]{2};
        backend.Parents[backend.HitElement]=backend.HitElement; Reject(()=>resolver.FindAncestors(0,0));
        backend.Parents.Clear(); Reject(()=>resolver.FindAncestors(0,0));
        backend=new Backend(); backend.RootElement.Values[30020]=101; Reject(()=>new DiscoveryResolver(backend,100));
        backend=new Backend(); backend.RootElement.Values[30002]=0; Reject(()=>new DiscoveryResolver(backend,100));
        backend=new Backend(); resolver=new DiscoveryResolver(backend,100); backend.FailGuard=true; Reject(()=>resolver.FindAncestors(0,0));
        // Exactly32 nearest-first ancestors accepted;33 and unrelated root refused.
        for(int count=32;count<=33;count++) {
            backend=new Backend(); backend.Parents.Clear(); Element prior=backend.HitElement;
            for(int i=0;i<count-2;i++) { var node=new Element(i+3); backend.Parents[prior]=node; prior=node; }
            backend.Parents[prior]=backend.RootElement; resolver=new DiscoveryResolver(backend,100);
            if(count==32) Check(resolver.FindAncestors(0,0).Length==32); else Reject(()=>resolver.FindAncestors(0,0));
        }
        // Every marshalled acquisition is balanced, including aliased RCWs.
        object a=new object(),b=new object(); var released=new List<object>();
        DiscoveryPolicy.Release(new[]{a,b,a},v=>true,v=>{released.Add(v);return released.Count==1?1:0;});
        Check(released.Count==3 && Object.ReferenceEquals(released[0],a) && Object.ReferenceEquals(released[1],b));
        Reject(()=>DiscoveryPolicy.Release(new[]{a},v=>true,v=>1));
        Reject(()=>DiscoveryPolicy.Release(new[]{a,a},v=>true,v=>0));
        released.Clear(); Exception one=new Exception("release-a"),two=new Exception("release-b"); Exception seen=null;
        try { DiscoveryPolicy.Release(new[]{a,b},v=>true,v=>{released.Add(v);throw Object.ReferenceEquals(v,a)?one:two;}); } catch(Exception error) { seen=error; }
        Check(released.Count==2 && seen is AggregateException && ((AggregateException)seen).InnerExceptions.Count==2);
        seen=null; try { DiscoveryPolicy.Release(new[]{a},v=>true,v=>{throw one;}); } catch(Exception error) { seen=error; } Check(Object.ReferenceEquals(seen,one));
        for(int faults=0;faults<4;faults++) {
            var trace=new List<string>(); var phases=new List<string>(); seen=null;
            try { DiscoveryPolicy.Retire(
                ()=>{trace.Add("release");if((faults&1)!=0)throw one;},
                ()=>{trace.Add("restore");if((faults&2)!=0)throw two;},phases.Add); }
            catch(Exception error) { seen=error; }
            Check(String.Join(",",trace)=="release,restore");
            Check(String.Join(",",phases)=="discovery_release,"+((faults&1)!=0?"discovery_release_failed":"discovery_released")+",discovery_restore,"+((faults&2)!=0?"discovery_restore_failed":"discovery_restored"));
            if(faults==0) Check(seen==null);
            if(faults==1) Check(Object.ReferenceEquals(seen,one));
            if(faults==2) Check(Object.ReferenceEquals(seen,two));
            if(faults==3) Check(seen is AggregateException && ((AggregateException)seen).InnerExceptions.Count==2);
        }
        // A diagnostic failure at every marker must not change cleanup outcome,
        // skip the independent action, or replace the original exception(s).
        for(int faults=0;faults<4;faults++) {
            var trace=new List<string>(); int markers=0; seen=null;
            try { DiscoveryPolicy.Retire(
                ()=>{trace.Add("release");if((faults&1)!=0)throw one;},
                ()=>{trace.Add("restore");if((faults&2)!=0)throw two;},
                phase=>{markers++;throw new InvalidOperationException("diagnostic-fault");}); }
            catch(Exception error) { seen=error; }
            Check(String.Join(",",trace)=="release,restore" && markers==4);
            if(faults==0) Check(seen==null);
            if(faults==1) Check(Object.ReferenceEquals(seen,one));
            if(faults==2) Check(Object.ReferenceEquals(seen,two));
            if(faults==3) {
                Check(seen is AggregateException);
                var aggregate=(AggregateException)seen;
                Check(aggregate.InnerExceptions.Count==2 && Object.ReferenceEquals(aggregate.InnerExceptions[0],one) && Object.ReferenceEquals(aggregate.InnerExceptions[1],two));
            }
        }
        foreach(string fault in new[]{"", "guard-before", "call", "null", "empty", "oversized", "guard-after"}) {
            foreach(bool brokenDiagnostics in new[]{false,true}) {
                var phases=new List<string>(); int calls=0,guards=0; int[] result=null;
                int[] valid=new[]{Int32.MinValue,0,Int32.MaxValue}; seen=null;
                try { result=DiscoveryPolicy.ReadRuntime(
                    ()=>{calls++;if(fault=="call")throw one;
                        if(fault=="null")return null;if(fault=="empty")return new int[0];
                        return fault=="oversized"?new int[65]:valid;},
                    ()=>{guards++;if((guards==1 && fault=="guard-before") || (guards==2 && fault=="guard-after"))throw two;},
                    phase=>{phases.Add(phase);if(brokenDiagnostics)throw new Exception("optional-diagnostic");}); }
                catch(Exception error) { seen=error; }
                Check(calls==(fault=="guard-before"?0:1));
                Check(guards==((fault=="" || fault=="guard-after")?2:1));
                var expected=new List<string>{"discovery_runtime_guard_before"};
                if(fault!="guard-before")expected.Add("discovery_runtime_call");
                if(fault!="guard-before" && fault!="call")expected.Add("discovery_runtime_array");
                if(fault=="" || fault=="guard-after")expected.Add("discovery_runtime_guard_after");
                if(fault=="")expected.Add("discovery_runtime_verified");
                Check(String.Join(",",phases)==String.Join(",",expected));
                Check((fault=="")== (seen==null));
                if(fault=="")Check(Object.ReferenceEquals(result,valid));else Check(result==null);
                if(fault=="call")Check(Object.ReferenceEquals(seen,one));
                if(fault.StartsWith("guard-"))Check(Object.ReferenceEquals(seen,two));
            }
        }
        foreach(bool fail in new[]{false,true}) {
            object retained=null; seen=null;
            try { DiscoveryPolicy.Acquire<object>(delegate(out object value){value=a;if(fail)throw one;},v=>{retained=v;}); }
            catch(Exception error) { seen=error; }
            Check(Object.ReferenceEquals(retained,a));
            Check(fail?Object.ReferenceEquals(seen,one):seen==null);
        }
        object empty=new object();
        DiscoveryPolicy.Acquire<object>(delegate(out object value){value=null;},v=>{empty=v;});
        Check(empty==null);
        UnresolvedCases();
        return checks;
    }

    static Backend MissingLeaf() {
        var b=new Backend(); b.HitElement.Id=new int[0];b.HitElement.Values[30003]=50007;
        return b;
    }
    static IDiscoveryElement MutatePath(Backend b,string selected) {
        return NativeIdentityPath.Resolve<IDiscoveryElement>(b.HitElement,"1",selected,42,100,
            e=>e.Property(30002),e=>e.Runtime(),b.Parent,e=>e.Property(30020),
            e=>{TransientHitPolicy.Validate(e.Property(30003),e.Property(30034),e.Property(30001),0,10);},b.Guard);
    }
    static void UnresolvedCases() {
        foreach(bool nullId in new[]{false,true}) {
            var b=MissingLeaf(); if(nullId)b.HitElement.Id=null;
            var r=new DiscoveryResolver(b,100); var chain=r.FindAncestors(0,10);
            Check(chain.Length==1 && chain[0].RuntimeIdentity=="1" && r.UnresolvedHitBounds!=null);
            Check(Object.ReferenceEquals(MutatePath(b,"1"),b.RootElement));
            b.HitElement.Values[30002]=43;Reject(()=>r.FindAncestors(0,10));Check(r.UnresolvedHitBounds==null);
            Reject(()=>MutatePath(b,"1"));
            b.HitElement.Values[30002]=42;b.HitElement.Id=new[]{2};r.FindAncestors(0,10);Check(r.UnresolvedHitBounds==null);
        }
        foreach(object control in new object[]{50014,50032,49999,50041,"50007",null}) {
            var b=MissingLeaf();b.HitElement.Values[30003]=control;var r=new DiscoveryResolver(b,100);
            Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));Check(r.UnresolvedHitBounds==null);
        }
        foreach(object scroll in new object[]{true,0,"false",null}) {
            var b=MissingLeaf();b.HitElement.Values[30034]=scroll;var r=new DiscoveryResolver(b,100);
            Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));
        }
        foreach(int length in new[]{0,65}) {
            var b=MissingLeaf();var middle=new Element(3);middle.Id=new int[length];
            b.Parents[b.HitElement]=middle;b.Parents[middle]=b.RootElement;
            var r=new DiscoveryResolver(b,100);Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));
        }
        {var b=MissingLeaf();b.Parents[b.HitElement]=b.HitElement;var r=new DiscoveryResolver(b,100);Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));}
        {var b=MissingLeaf();b.HitElement.Id=new int[65];var r=new DiscoveryResolver(b,100);Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));}
        {var b=MissingLeaf();b.HitElement.Values[30001]=new double[]{100,100,10,10};var r=new DiscoveryResolver(b,100);Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));}
        {var b=MissingLeaf();Reject(()=>MutatePath(b,"missing"));b.RootElement.Values[30020]=101;Reject(()=>MutatePath(b,"1"));}
        for(int count=32;count<=33;count++) {
            var b=MissingLeaf();Element previous=b.HitElement;
            for(int i=0;i<count-2;i++){var node=new Element(i+3);b.Parents[previous]=node;previous=node;}
            b.Parents[previous]=b.RootElement;var r=new DiscoveryResolver(b,100);
            if(count==32){Check(r.FindAncestors(0,10).Length==31);Check(MutatePath(b,"3")!=null);}
            else {Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"3"));}
        }
        {var b=new Backend();b.Parents[b.HitElement]=b.HitElement;Reject(()=>MutatePath(b,"2"));}
        {var b=MissingLeaf();var r=new DiscoveryResolver(b,100);b.RootElement.Id=new[]{9};Reject(()=>r.FindAncestors(0,10));Check(r.UnresolvedHitBounds==null);}
        int discoveryTotal=0;
        {var b=MissingLeaf();var r=new DiscoveryResolver(b,100);Action tick=()=>discoveryTotal++;b.Tick=tick;b.HitElement.Tick=tick;b.RootElement.Tick=tick;r.FindAncestors(0,10);}
        for(int fail=0;fail<discoveryTotal;fail++) {
            var b=MissingLeaf();var r=new DiscoveryResolver(b,100);int calls=0;var expected=new Exception("private discovery fault");
            Action tick=()=>{if(calls++==fail)throw expected;};b.Tick=tick;b.HitElement.Tick=tick;b.RootElement.Tick=tick;
            bool returned=false;try{r.FindAncestors(0,10);returned=true;}catch(Exception error){Check(Object.ReferenceEquals(error,expected));}
            Check(!returned && calls==fail+1 && r.UnresolvedHitBounds==null);
        }
        foreach(object badBounds in new object[]{null,new int[]{0,0,100,100},new double[3],
            new double[]{Double.NaN,0,100,100},new double[]{0,0,Double.PositiveInfinity,100},
            new double[]{0,0,0,100},new double[]{0,0,100,-1},new double[]{0.1,9.1,0.1,0.1},
            new double[]{Int32.MaxValue,0,2,100},new double[]{-1e20,0,1e21,100}}) {
            var b=MissingLeaf();b.HitElement.Values[30001]=badBounds;var r=new DiscoveryResolver(b,100);
            Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"1"));Check(r.UnresolvedHitBounds==null);
        }
        {var b=MissingLeaf();var middle=new Element(3);b.Parents[b.HitElement]=middle;b.Parents[middle]=middle;
         var r=new DiscoveryResolver(b,100);Reject(()=>r.FindAncestors(0,10));Reject(()=>MutatePath(b,"3"));Check(r.UnresolvedHitBounds==null);}
        // Inject provider/guard errors at every mutation traversal boundary.
        int total=0;
        {var b=MissingLeaf();Action tick=()=>total++;b.Tick=tick;b.HitElement.Tick=tick;b.RootElement.Tick=tick;MutatePath(b,"1");}
        for(int fail=0;fail<total;fail++) {
            var b=MissingLeaf();int calls=0;var expected=new Exception("private fault");
            Action tick=()=>{if(calls++==fail)throw expected;};b.Tick=tick;b.HitElement.Tick=tick;b.RootElement.Tick=tick;
            bool returned=false;try{MutatePath(b,"1");returned=true;}catch(Exception error){Check(Object.ReferenceEquals(error,expected));}
            Check(!returned && calls==fail+1);
        }
    }
}
