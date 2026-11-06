using System;
using System.Collections.Generic;
using VerdiClip.Automation;
public static class RuntimeProbeTests {
    static int count;
    static void Check(bool value) { if(!value) throw new Exception("runtime_policy_failure"); count++; }
    public static int Run() {
        object[] values={null,new int[0],new int[65],new long[]{1},new object(),new int[]{Int32.MinValue,Int32.MaxValue}};
        string[] shapes={"null","empty","oversized","wrong_type","wrong_type","valid"};
        for(int i=0;i<values.Length;i++) Check(RuntimeComparisonPolicy.Shape(values[i])==shapes[i]);
        foreach(int[] first in new int[][]{null,new int[0],new int[65],new int[]{1},new int[]{-1,2}}) {
            foreach(object second in values) {
                int calls=0,reads=0,guards=0;
                var pair=RuntimeComparisonPolicy.Compare(()=>{calls++;return first;},()=>{reads++;return second;},()=>guards++);
                Check(calls==1 && reads==1 && guards==3);
                Check(pair.method==RuntimeComparisonPolicy.Shape(first) && pair.property==RuntimeComparisonPolicy.Shape(second));
                Check(pair.equal.HasValue==(pair.method=="valid" && pair.property=="valid"));
            }
        }
        Check(RuntimeComparisonPolicy.Compare(()=>new int[]{-1,2},()=>new int[]{-1,2},()=>{}).equal==true);
        Check(RuntimeComparisonPolicy.Compare(()=>new int[]{-1,2},()=>new int[]{-1,3},()=>{}).equal==false);
        for(int failure=0;failure<5;failure++) {
            int stage=0; var expected=new Exception("private");
            Action tick=()=>{if(stage++==failure)throw expected;};
            bool returned=false;
            try { RuntimeComparisonPolicy.Compare(()=>{tick();return new int[]{1};},()=>{tick();return new int[]{1};},tick); returned=true; }
            catch(Exception error) { Check(Object.ReferenceEquals(expected,error)); }
            Check(!returned && stage==failure+1);
        }
        // Any combination of operation/release/restore/verify faults: all cleanup
        // attempts must happen, and every original exception remains accounted for.
        for(int mask=0;mask<16;mask++) {
            var calls=new List<int>(); var expected=new List<Exception>();
            Action<int> action=i=>{calls.Add(i); if((mask&(1<<i))!=0){var e=new Exception("private");expected.Add(e);throw e;}};
            bool returned=false;
            try { var result=RuntimeComparisonPolicy.Complete(()=>{action(0);return new RuntimeProbeResult();},()=>action(1),()=>action(2),()=>action(3)); returned=true; Check(result.release_confirmed && result.dpi_restored && result.native_only_verified); }
            catch(Exception error) {
                Exception[] leaves=error is AggregateException ? new List<Exception>(((AggregateException)error).Flatten().InnerExceptions).ToArray() : new Exception[]{error};
                Check(leaves.Length==expected.Count);
                for(int i=0;i<leaves.Length;i++)Check(Object.ReferenceEquals(leaves[i],expected[i]));
            }
            Check(calls.Count==4 && calls[0]==0 && calls[1]==1 && calls[2]==2 && calls[3]==3);
            Check(returned==(mask==0));
        }
        return count;
    }
}
