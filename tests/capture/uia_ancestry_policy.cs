// Test-only ancestry evidence, never a discovery/protocol result.
using System;
using System.Collections.Generic;
namespace VerdiClip.Automation {
public sealed class AncestryObservation {
    public string hit_category, walk_outcome;
    public bool scroll_available, root_reached;
    public int ancestor_count;
}
public static class AncestryPolicy {
    static object Property(IDiscoveryElement element,int id,Action guard) {
        guard(); object value=element.Property(id); guard(); return value;
    }
    public static AncestryObservation Trace(IDiscoveryBackend backend,int[] rootId,
        IDiscoveryElement hit,int[] hitId,long handle,int pid,int x,int y,Action guard) {
        string root=DiscoveryPolicy.Runtime(rootId);
        DiscoveryPolicy.Require(hit!=null && pid>0 && handle>0);
        DiscoveryPolicy.Require(DiscoveryPolicy.Integer(Property(hit,30002,guard))==pid);
        int control=DiscoveryPolicy.Integer(Property(hit,30003,guard));
        DiscoveryPolicy.Require(control>=50000 && control<=50040);
        bool scroll=DiscoveryPolicy.Boolean(Property(hit,30034,guard));
        string category=control==50014 ? "scrollbar" : control==50032 ? "window" :
            control==50007 ? "list_item" : "other";
        var result=new AncestryObservation {hit_category=category,scroll_available=scroll,
            walk_outcome="identified_hit",root_reached=false,ancestor_count=0};
        string shape=RuntimeComparisonPolicy.Shape(hitId);
        DiscoveryPolicy.Require(shape=="valid" || shape=="null" || shape=="empty");
        if(shape=="valid") return result;
        if(scroll || category=="scrollbar" || category=="window") {
            result.walk_outcome="candidate_hit"; return result;
        }
        var bounds=DiscoveryPolicy.Rectangle(Property(hit,30001,guard));
        DiscoveryPolicy.Require(x>=bounds.Left && x<bounds.Right && y>=bounds.Top && y<bounds.Bottom);
        var seen=new HashSet<string>(StringComparer.Ordinal);
        IDiscoveryElement current=hit;
        // The initial missing-ID leaf consumes depth0. Never skip a second one.
        for(int depth=1;depth<32;depth++) {
            guard(); current=backend.Parent(current); guard();
            DiscoveryPolicy.Require(current!=null);
            DiscoveryPolicy.Require(DiscoveryPolicy.Integer(Property(current,30002,guard))==pid);
            guard(); int[] runtime=current.Runtime(); guard();
            string identity=DiscoveryPolicy.Runtime(runtime);
            DiscoveryPolicy.Require(seen.Add(identity));
            result.ancestor_count=depth;
            if(identity==root) {
                DiscoveryPolicy.Require((long)DiscoveryPolicy.Integer(Property(current,30020,guard))==handle);
                result.walk_outcome="verified_suffix"; result.root_reached=true; return result;
            }
        }
        throw new InvalidOperationException("ancestry_unverified");
    }
}
}
