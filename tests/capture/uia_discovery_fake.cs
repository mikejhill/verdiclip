using System;
using System.Collections.Generic;
using VerdiClip.Automation;
public static class DiscoveryTests {
    private static int checks;
    static void Check(bool value) { checks++; if(!value) throw new Exception("Discovery assertion failed"); }
    static void Reject(Action action) { bool failed=false; try { action(); } catch(InvalidOperationException) { failed=true; } Check(failed); }
    sealed class Element : IDiscoveryElement {
        public Dictionary<int,object> Values=new Dictionary<int,object>();
        public int[] Id; public IDiscoveryScroll Pattern; public bool ThrowScroll;
        public Element(int id) { Id=new[]{id}; Values[30002]=42; Values[30020]=id==1?100:0; Values[30034]=false; Values[30001]=new double[]{-10.2,3.2,100.8,200.8}; Values[30003]=50014; Values[30023]=2; Values[30022]=false; Values[30010]=true; }
        public object Property(int id) { return Values[id]; }
        public int[] Runtime() { return Id; }
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
        public int X,Y,Guards; public bool FailGuard; public List<string> Phases=new List<string>();
        public Backend() { Parents[HitElement]=RootElement; }
        public IDiscoveryElement Root(long handle) { return RootElement; }
        public IDiscoveryElement Hit(int x,int y) { X=x;Y=y;return HitElement; }
        public IDiscoveryElement Parent(IDiscoveryElement element) { return Parents.ContainsKey(element)?Parents[element]:null; }
        public void Guard() { Guards++; if(FailGuard) throw new InvalidOperationException("guard"); }
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
        Check(backend.Phases[backend.Phases.Count-1]=="inspect_ancestor_runtime_read");
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
        return checks;
    }
}
