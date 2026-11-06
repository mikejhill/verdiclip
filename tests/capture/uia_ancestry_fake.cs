using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using VerdiClip.Automation;
public static class AncestryTests {
    static int checks;
    static void Check(bool condition) {if(!condition)throw new Exception("ancestry_test_failed");checks++;}
    sealed class Node:IDiscoveryElement {
        public int[] Id=new int[]{1}; public object Pid=7,Control=50007,ScrollAvailable=false,Handle=42;
        public object Bounds=new double[]{0,0,100,100}; public Node Up;
        public Action Tick=()=>{};
        public object Property(int id) {
            Tick();
            if(id==30002)return Pid;if(id==30003)return Control;
            if(id==30034)return ScrollAvailable;if(id==30001)return Bounds;
            if(id==30020)return Handle;throw new Exception("unexpected_property");
        }
        public int[] Runtime(){Tick();return Id;}
        public IDiscoveryScroll Scroll(){throw new Exception("pattern_not_permitted");}
    }
    sealed class Backend:IDiscoveryBackend {
        public int Parents; public Action Tick=()=>{};
        public IDiscoveryElement Parent(IDiscoveryElement value){Tick();Parents++;return ((Node)value).Up;}
        public IDiscoveryElement Root(long handle){throw new Exception("unexpected_root");}
        public IDiscoveryElement Hit(int x,int y){throw new Exception("unexpected_hit");}
        public void Guard(){Tick();} public void Mark(string phase){} public void Dispose(){}
    }
    static Node Chain(int depth) {
        Node parent=new Node{Id=new int[]{9},Control=50032};
        for(int i=depth-1;i>0;i--)parent=new Node{Id=new int[]{100+i},Up=parent};
        return new Node{Id=new int[0],Up=parent};
    }
    static AncestryObservation Trace(Backend backend,Node hit) {
        return AncestryPolicy.Trace(backend,new int[]{9},hit,hit.Id,42,7,50,50,backend.Guard);
    }
    static void Refuse(Backend backend,Node hit) {
        bool returned=false;try{Trace(backend,hit);returned=true;}catch(InvalidOperationException){}
        Check(!returned);
    }
    public static int Run() {
        var method=typeof(IElement).GetMethod("GetRuntimeId");
        var output=method.GetParameters()[0];
        var marshal=(MarshalAsAttribute)Attribute.GetCustomAttribute(output,typeof(MarshalAsAttribute));
        Check(method.ReturnType==typeof(void) && output.IsOut && output.ParameterType==typeof(int[]).MakeByRefType());
        Check(marshal.Value==UnmanagedType.SafeArray && marshal.SafeArraySubType==VarEnum.VT_I4);
        var parent=typeof(IWalker).GetMethod("GetParentElement");
        Check(parent.ReturnType==typeof(void) && parent.GetParameters()[0].ParameterType==typeof(IElement));
        Check(parent.GetParameters()[1].IsOut && parent.GetParameters()[1].ParameterType==typeof(IElement).MakeByRefType());

        foreach(int depth in new int[]{1,2,31}){
            var backend=new Backend();var result=Trace(backend,Chain(depth));
            Check(result.walk_outcome=="verified_suffix" && result.root_reached);
            Check(result.ancestor_count==depth && backend.Parents==depth);
        }
        foreach(object control in new object[]{50014,50032,50007}) {
            var backend=new Backend();var hit=Chain(2);hit.Control=control;hit.ScrollAvailable=(int)control==50007;
            var result=Trace(backend,hit);
            Check(result.walk_outcome=="candidate_hit" && !result.root_reached && backend.Parents==0);
        }
        {var backend=new Backend();var hit=Chain(2);hit.Id=new int[]{3};var result=Trace(backend,hit);
         Check(result.walk_outcome=="identified_hit" && backend.Parents==0);}
        {var backend=new Backend();var hit=Chain(2);hit.Id=null;Check(Trace(backend,hit).root_reached);}
        foreach(int[] missing in new int[][]{null,new int[0],new int[65]}) {
            var backend=new Backend();var hit=Chain(2);hit.Up.Id=missing;Refuse(backend,hit);Check(backend.Parents==1);
        }
        {var backend=new Backend();var hit=Chain(2);hit.Id=new int[65];Refuse(backend,hit);Check(backend.Parents==0);}
        {var backend=new Backend();var hit=Chain(2);hit.Up=hit;Refuse(backend,hit);Check(backend.Parents==1);}
        {var backend=new Backend();var hit=Chain(2);hit.Up.Up=hit.Up;Refuse(backend,hit);Check(backend.Parents==2);}
        {var backend=new Backend();var hit=Chain(2);hit.Up.Pid=8;Refuse(backend,hit);}
        {var backend=new Backend();var hit=Chain(1);hit.Up.Handle=43;Refuse(backend,hit);}
        {var backend=new Backend();var hit=Chain(1);hit.Up.Id=new int[]{10};Refuse(backend,hit);}
        {var backend=new Backend();var hit=Chain(32);Refuse(backend,hit);Check(backend.Parents==31);}
        {var backend=new Backend();var hit=Chain(2);hit.Bounds=new double[]{100,100,50,50};Refuse(backend,hit);Check(backend.Parents==0);}
        foreach(object wrong in new object[]{null,true,"7",7L}){
            var backend=new Backend();var hit=Chain(2);hit.Pid=wrong;Refuse(backend,hit);
        }
        foreach(object wrong in new object[]{null,true,"50007",49999,50041}){
            var backend=new Backend();var hit=Chain(2);hit.Control=wrong;Refuse(backend,hit);
        }
        foreach(object wrong in new object[]{null,0,"false"}){
            var backend=new Backend();var hit=Chain(2);hit.ScrollAvailable=wrong;Refuse(backend,hit);
        }
        // Every injected provider/guard failure remains terminal, never absence.
        int total=0;
        {var backend=new Backend();var hit=Chain(2);Action tick=()=>total++;
         backend.Tick=tick;for(Node node=hit;node!=null;node=node.Up)node.Tick=tick;Trace(backend,hit);}
        for(int fail=0;fail<total;fail++) {
            int calls=0;var expected=new Exception("private provider failure");
            var backend=new Backend();var hit=Chain(2);Action tick=()=>{if(calls++==fail)throw expected;};
            backend.Tick=tick;for(Node node=hit;node!=null;node=node.Up)node.Tick=tick;
            bool returned=false;try{Trace(backend,hit);returned=true;}catch(Exception error){Check(Object.ReferenceEquals(error,expected));}
            Check(!returned && calls==fail+1);
        }
        return checks;
    }
}
